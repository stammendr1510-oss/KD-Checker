"""Experimental, conservative E3 / PE3 PDF cross-check. No AI or network."""
from __future__ import annotations
import re
from collections import defaultdict
from itertools import islice, chain
from pathlib import Path
import fitz
from pdf_match_evidence import MAX_LOCATIONS, MAX_MATCHES, location_key, source
from designation_conflicts import COMPATIBLE, compare_names, conflict_findings
from confusable_designations import confusable_findings, model_tokens

# Designation normalization/range expansion live in naming.py; re-exported here
# for backward compatibility. Reference classes are configurable via
# naming.configure_reference_classes() (no hardcoded per-class rules).
from naming import (  # noqa: F401
    expand_notation, _notation_parts, _split_single_line, _is_complete_notation,
    base_designator, letter_designator,
    CYR_LOOKALIKES,
    _notation_part,
    DEFAULT_CLASS_PREFIX,
    RANGE,
    DOTTED_RANGE,
    TAG_PREFIX,  # kept as the historical default class prefix
    configure_reference_classes,
    current_tag,
    normalized,
    reference_tag,
    universal_reference_tag,
)

MAX_PDF_PAGES = 100
MAX_SCHEME_DESIGNATIONS_PER_PAGE = 5000
MAX_SCHEME_MENTIONS = 50000
MAX_NAME_LENGTH = 300
TRUNCATION_MARK = '…'

TAG = reference_tag()

# Semantic row model: bulk/length unit signatures for quantity-warning gating.
# Shared with engineering_model.SpecRow (kept in sync by tests); a local copy
# avoids an import cycle.
_LENGTH_OR_BULK_UNITS_RE = re.compile(
    r'(?<![\w])(?:м|мм|мм²|м2|см|км|кг|г|м2/м|пог\.?\s*м|м\.)(?![\w])', re.I)

def designation_type(name):
    m = re.search(r'\.((?:ПЭ3)|(?:Э3))(?=\s|\.|$)', Path(name).stem, re.I)
    return m.group(1).upper() if m else None

def pair_key(name):
    return re.sub(r'\.(?:ПЭ3|Э3)(?=\s|\.|$)', '.', Path(name).stem, flags=re.I).strip('. ').casefold()

def issue(level, code, file, place, message, recommendation):
    return {'severity':level, 'code':code, 'file':file, 'location':place, 'message':message, 'recommendation':recommendation}

def _designator_column(page, words):
    """Find the designator column from its header, with a safe fallback.

    Real PE3 forms use different column widths.  The previous fixed 9%--18.8%
    window could miss a whole grouped row such as ``TA2;TA3``.  The header and
    its vertical borders are structural evidence; when they are absent the
    historical fallback is preserved.
    """
    left, right = page.rect.width * .09, page.rect.width * .188
    headers = [
        word for word in words
        if word[1] < page.rect.height * .1
        and re.fullmatch(r'(?:поз\.?|poz\.?|обознач\w*|oboznachen\w*|позиц\w*|pozic\w*)',
                         word[4].casefold().rstrip('.'))
    ]
    if not headers:
        return left, right
    header_left = min(word[0] for word in headers)
    header_right = max(word[2] for word in headers)
    center_x = (header_left + header_right) / 2
    center_y = sum((word[1] + word[3]) / 2 for word in headers) / len(headers)
    borders = set()
    for drawing in page.get_drawings():
        for item in drawing['items']:
            if item[0] == 'l':
                a, b = item[1:3]
                if abs(a.x - b.x) < .5 and min(a.y, b.y) <= center_y <= max(a.y, b.y):
                    borders.add((a.x + b.x) / 2)
            elif item[0] == 're':
                rect = item[1]
                if rect.y0 <= center_y <= rect.y1:
                    borders.update((rect.x0, rect.x1))
    before = [x for x in borders if x < center_x]
    after = [x for x in borders if x > center_x]
    if before and after:
        candidate = (max(before), min(after))
        if 10 <= candidate[1] - candidate[0] <= page.rect.width * .35:
            return candidate
    if header_right - header_left >= 10:
        return header_left - 2, header_right + 2
    return left, right

def _row_rules(page, column_left, column_right):
    """Horizontal rules crossing the designator column (PDF points)."""
    left, right = column_left, column_right
    rules = set()
    for drawing in page.get_drawings():
        for item in drawing['items']:
            if item[0] == 'l':
                a, b = item[1:3]
                if abs(a.y - b.y) < .5 and min(a.x, b.x) <= left and max(a.x, b.x) >= right:
                    rules.add((a.y + b.y) / 2)
            elif item[0] == 're':
                rect = item[1]
                if rect.x0 <= left and rect.x1 >= right:
                    rules.update((rect.y0, rect.y1))
    return sorted(rules)

def _quantity_words(page, words):
    """Use the quantity header's vertical borders to exclude numeric notes.

    The header is searched across the whole upper part of the sheet: real PE3
    forms place the quantity column at different X offsets, and the historical
    fixed 70--79% window would miss the entire table.
    """
    left, right = page.rect.width * .70, page.rect.width * .79
    headers = [w for w in words
               if w[1] < page.rect.height * .1
               and w[4].casefold().rstrip('.') in ('кол', 'kol')]
    if len(headers) == 1:
        header = headers[0]
        cx, cy = (header[0] + header[2]) / 2, (header[1] + header[3]) / 2
        borders = set()
        for drawing in page.get_drawings():
            for item in drawing['items']:
                if item[0] == 'l':
                    a, b = item[1:3]
                    # Borders may start just below a header that sits above
                    # the table frame: accept segments crossing the header
                    # band or beginning right under it.
                    if abs(a.x - b.x) < .5 and min(a.y, b.y) <= cy + 20 and max(a.y, b.y) >= cy - 2:
                        borders.add((a.x + b.x) / 2)
                elif item[0] == 're':
                    rect = item[1]
                    if rect.y0 <= cy + 20 and rect.y1 >= cy - 2:
                        borders.update((rect.x0, rect.x1))
        before, after = [x for x in borders if x < cx], [x for x in borders if x > cx]
        if before and after:
            left, right = max(before), min(after)
    candidates = sorted([w for w in words if left < w[0] < right and re.fullmatch(r'\d+', w[4])
                         and w[1] < page.rect.height * .83], key=lambda w: (w[1], w[0]))
    rows = []
    for word in candidates:
        if rows and abs(word[1] - rows[-1][1]) < 2:
            # With no usable header, the leftmost number is the quantity;
            # a second number to its right belongs to notes on the same row.
            rows[-1] = min(rows[-1], word, key=lambda w: w[0])
        else:
            rows.append(word)
    return rows

def _row_band(quantities, index, rules):
    """A distinct grid cell containing exactly one quantity anchor, if known."""
    qty = quantities[index]
    center = (qty[1] + qty[3]) / 2
    above = [y for y in rules if y < center]
    below = [y for y in rules if y > center]
    if above and below:
        top, bottom = above[-1], below[0]
        # A border around the whole table is not a row boundary.
        if sum(top < (q[1] + q[3]) / 2 < bottom for q in quantities) == 1:
            return top, bottom
    return None

def _cell_words(words, quantities, index, rules, previous):
    qty = quantities[index]
    center = (qty[1] + qty[3]) / 2
    band = _row_band(quantities, index, rules)
    if band:
        top, bottom = band
        return [w for w in words if top < (w[1] + w[3]) / 2 < bottom], bottom

    # Without a grid, extend only within a PDF text block that intersects
    # this quantity and no other quantity. Never join arbitrary nearby lines.
    limit = min(qty[1] + 8, quantities[index + 1][1] - 2) if index + 1 < len(quantities) else qty[1] + 8
    selected = [w for w in words if previous - 2 <= w[1] <= limit]
    blocks = {w[5] for w in selected if w[1] <= center <= w[3]}
    for block in blocks:
        cell = [w for w in words if w[5] == block]
        top, bottom = min(w[1] for w in cell), max(w[3] for w in cell)
        if top >= previous - 2 and sum(top <= (q[1] + q[3]) / 2 <= bottom for q in quantities) == 1:
            selected.extend(w for w in cell if w not in selected)
    last = max((w[1] for w in selected), default=qty[1])
    return selected, max(qty[1], last) + 9

def _duplicate_range_spelling_is_exact(notation):
    # The legacy range expander converts numbers to int. For duplicate evidence
    # do not equate a padded source like KM01...KM03 with a separate KM1.
    for part in notation.split(';'):
        cleaned = _notation_part(part) or ''
        if DOTTED_RANGE.fullmatch(cleaned):
            return False  # conservative: no duplicate proof from dotted ranges
        match = RANGE.fullmatch(cleaned)
        if match:
            _, first, _, last = match.groups()
            if str(int(first)) != first or str(int(last)) != last:
                return False
    return True

def _duplicate_column_bounds(page, band):
    """Require stroked borders of a narrow designator cell, not a text background."""
    if not band:
        return None
    top, bottom = band
    vertical, horizontal = [], []
    for drawing in page.get_drawings():
        if 's' not in drawing.get('type', ''):
            continue
        for item in drawing['items']:
            if item[0] == 'l':
                segments = [(item[1], item[2])]
            elif item[0] == 're':
                r = item[1]
                segments = [(r.tl, r.tr), (r.bl, r.br), (r.tl, r.bl), (r.tr, r.br)]
            else:
                continue
            for a, b in segments:
                if abs(a.x - b.x) < .5 and min(a.y, b.y) <= top + .5 and max(a.y, b.y) >= bottom - .5:
                    vertical.append((a.x + b.x) / 2)
                if abs(a.y - b.y) < .5:
                    horizontal.append((min(a.x, b.x), max(a.x, b.x), (a.y + b.y) / 2))
    center = page.rect.width * (.09 + .188) / 2
    before, after = [x for x in vertical if x < center], [x for x in vertical if x > center]
    if not before or not after:
        return None
    left, right = max(before), min(after)
    if right - left > page.rect.width * .15:
        return None
    if not all(any(x0 <= left + .5 and x1 >= right - .5 and abs(y - edge) < .5
                   for x0, x1, y in horizontal) for edge in band):
        return None
    return left, right

def get_spec_rows(pdf_bytes, *, include_evidence=False):
    """Read designator column and qty using original PDF word coordinates.
    Only rows with an identifiable numeric qty and designator are accepted.
    """
    rows = []
    with fitz.open(stream=pdf_bytes, filetype='pdf') as pdf:
        for pi, page in enumerate(islice(pdf, MAX_PDF_PAGES)):
            words = page.get_text('words', sort=True)
            page_size = (float(page.cropbox.width), float(page.cropbox.height))
            column_left, column_right = _designator_column(page, words)
            name_words = [t for t in words
                          if column_right <= t[0] < page.rect.width * .70
                          and t[1] < page.rect.height * .83]
            qty = _quantity_words(page, words)
            previous = 50
            column = [t for t in words if column_left <= t[0] < column_right and t[1] < page.rect.height * .83]
            rules = _row_rules(page, min((w[0] for w in column), default=column_left), column_right)
            for index, w in enumerate(qty):
                des, previous = _cell_words(column, qty, index, rules, previous)
                # PDF block/line identifiers preserve wrapped fragments without
                # rounding y coordinates into arbitrary six-point bands.
                lines = defaultdict(list)
                for t in des:
                    lines[(t[5], t[6])].append(t)
                ordered = sorted(lines.values(), key=lambda line: (min(t[1] for t in line), min(t[0] for t in line)))
                # Preserve physical line boundaries until cell geometry and
                # complete notation establish that flattening is safe.
                # Word gaps also matter: XT1 and 2 must not silently become XT12.
                notation = '\n'.join(' '.join(t[4] for t in sorted(line, key=lambda t: t[0])) for line in ordered)
                raw = notation
                entries, unknown = expand_notation(notation)
                if entries:
                    band = _row_band(qty, index, rules)
                    bounds = _duplicate_column_bounds(page, band) if not unknown else None
                    contained = bool(bounds and all(bounds[0] - .5 <= t[0] and t[2] <= bounds[1] + .5 for t in des))
                    complete = bool(bounds and all(t in des for t in words
                        if bounds[0] <= t[0] < bounds[1] and band[0] < (t[1] + t[3]) / 2 < band[1]))
                    # A verified cell can display wrapped, fully parsed values
                    # on one line. Otherwise preserve physical line breaks as
                    # source evidence, especially for ambiguous fragments.
                    if contained and complete and not unknown:
                        raw = ' '.join(notation.split())
                    name = ''
                    name_confidence = 'low'
                    if band:
                        name_left = bounds[1] if bounds else column_right
                        selected = [t for t in name_words
                                    if name_left <= t[0] < page.rect.width * .70
                                    and band[0] < (t[1] + t[3]) / 2 < band[1]]
                        if selected:
                            selected.sort(key=lambda t: (t[1], t[0]))
                            name = re.sub(r'\s+', ' ', ' '.join(t[4] for t in selected)).strip()
                            if len(name) > MAX_NAME_LENGTH:
                                name = name[:MAX_NAME_LENGTH - 1] + TRUNCATION_MARK
                            name_confidence = 'medium'
                    rows.append({'page': pi + 1, 'source': raw, 'quantity': int(w[4]), 'entries': entries, 'unknown': unknown,
                                 'row_band': band,
                                 'table_row': rules.index(band[0]) + 1 if band else None,
                                 'name': name,
                                 'name_confidence': name_confidence,
                                 'page_size_pt': page_size,
                                 'duplicate_eligible': bool(band and band[0] >= 50 and contained and complete and not unknown
                                                           and _duplicate_range_spelling_is_exact(notation))})
                    if include_evidence and contained and complete and len(des) == 1:
                        word = des[0]
                        if current_tag().fullmatch(word[4]) and normalized(word[4]) in entries:
                            rows[-1]["literal_box"] = (word[:4], (page.cropbox.width, page.cropbox.height))
    return rows

def duplicate_findings(rows, filename):
    """Compare complete, grid-confirmed rows of ONE PE3, never E3 mentions."""
    occurrences = defaultdict(dict)
    for row in rows:
        if not row.get('duplicate_eligible') or row['unknown']:
            continue
        # Multiple textual mentions or quantity anchors in a cell are not new rows.
        cell = (row['page'], *row['row_band'])
        for designation in row['entries']:
            occurrences[designation][cell] = row
    findings = []
    for designation, cells in sorted(occurrences.items()):
        if len(cells) < 2:
            continue
        ordered_rows = [row for _, row in sorted(cells.items())]
        locations = [f"Лист {row['page']}, строка таблицы {row['table_row']}"
                     for row in ordered_rows]
        names = [str(row.get('name') or '').strip() for row in ordered_rows]
        compatible = (
            len(names) >= 2
            and all(names)
            and all(compare_names(names[0], other) == COMPATIBLE for other in names[1:])
        )
        if compatible:
            # A repeated designation with the same product description may be a
            # legitimate continuation or a multi-sheet reference.  It is not a
            # duplicate error, but the engineer still sees the repeat.
            findings.append(issue(
                'info', 'KD-PE-104', filename, '; '.join(locations),
                f'Для проверки инженером: обозначение «{designation}» повторяется в '
                f'{len(cells)} строках ПЭ3, но наименование изделия совпадает. '
                'Повтор может быть продолжением записи или многолистовой ссылкой.',
                'Сопоставьте строки с исходным документом. Совместимые данные не '
                'превращаются в предупреждение; решение принимает инженер.',
            ))
            continue
        findings.append(issue(
            'warning', 'KD-PE-104', filename, '; '.join(locations),
            f'Для проверки инженером: обозначение «{designation}» распознано в {len(cells)} разных строках одного ПЭ3.',
            'Сопоставьте исходные ячейки перечня. Номера строк определены по горизонтальным границам таблицы, '
            'включая заголовок и пустые строки. Повтор может относиться к продолжению записи или особенностям '
            'комплектования; это не подтверждённая ошибка КД.',
        ))
    # A repeated designation is not automatically an error: only incompatible
    # product attributes make it a conflict.  The two diagnostics stay separate.
    findings.extend(conflict_findings(rows, filename))
    return findings

def check_spec_duplicates(filename, pdf_bytes):
    """Standalone PE3 validation; caller supplies a single document's bytes."""
    return duplicate_findings(get_spec_rows(pdf_bytes), filename)

def pe3_text_designations(pdf_bytes):
    """Return designators visible in the PE3 text layer, bounded by page limits.

    This is *presence evidence only*.  It prevents a false "missing product"
    finding when a grouped row could not be parsed, without ever inventing a
    match for KD-PE-100.
    """
    found = set()
    tag = current_tag()
    with fitz.open(stream=pdf_bytes, filetype='pdf') as pdf:
        for page in islice(pdf, MAX_PDF_PAGES):
            for match in tag.finditer(page.get_text()):
                found.add(normalized(match.group(0)))
                if len(found) >= MAX_SCHEME_MENTIONS:
                    return found
    return found

def _separated_by_rule(page, a, b):
    left, right = min(a[0], b[0]), max(a[2], b[2])
    top, bottom = (a[1] + a[3]) / 2, (b[1] + b[3]) / 2
    for drawing in page.get_drawings():
        for item in drawing['items']:
            if item[0] == 'l':
                p, q = item[1:3]
                if abs(p.y - q.y) < .5 and top < (p.y + q.y) / 2 < bottom and min(p.x, q.x) <= left and max(p.x, q.x) >= right:
                    return True
            elif item[0] == 're':
                rect = item[1]
                if rect.x0 <= left and rect.x1 >= right and any(top < y < bottom for y in (rect.y0, rect.y1)):
                    return True
    return False

def scheme_letter_designations(page, letter_candidates):
    allowed_letters = {normalized(v) for v in letter_candidates if letter_designator(v)}
    for word in page.get_text('words', sort=True):
        if letter_designator(word[4]) and normalized(word[4]) in allowed_letters:
            yield word[4]


def scheme_designations(page, *, letter_candidates=()):
    """Recover only an incomplete prefix + numeric suffix in adjacent PDF lines.
    Standalone complete labels, different blocks, columns and pages stay separate.
    """
    yield from (m.group(0) for m in current_tag().finditer(page.get_text()))
    yield from scheme_letter_designations(page, letter_candidates)
    # A range printed as one E3 token must contribute every member, not only
    # the endpoints found by the tag regex. The same grammar handles compound
    # semicolon lists without reference-class-specific rules.
    for word in page.get_text('words', sort=True):
        text = str(word[4])
        if not any(mark in text for mark in (';', '..', '…', '–', '—')):
            continue
        expanded, _unknown = expand_notation(text)
        yield from sorted(expanded)
    blocks = defaultdict(lambda: defaultdict(list))
    for word in page.get_text('words', sort=True):
        blocks[word[5]][word[6]].append(word)
    for lines in blocks.values():
        ordered = sorted(lines.values(), key=lambda words: min(w[1] for w in words))
        if len(ordered) != 2:
            continue
        first, second = ordered
        if len(first) != 1 or len(second) != 1:
            continue
        a, b = first[0], second[0]
        if not re.fullmatch(r'[123]?[A-Z]+', a[4], re.I) or not re.fullmatch(r'[A-Z]*\d+(?:\.\d+)?', b[4], re.I):
            continue
        height = max(a[3] - a[1], b[3] - b[1])
        if abs(a[0] - b[0]) > 2 or not (0 < b[1] - a[1] <= height * 1.6):
            continue
        candidate = a[4] + b[4]
        if current_tag().fullmatch(candidate) and not _separated_by_rule(page, a, b):
            yield candidate

def _unconfirmed_pe3_context(rows, examples):
    """Evidence-backed PDF locations for up to eight unconfirmed PE3 designators.

    Never infer a row number from text proximity. A bounded sample avoids
    huge finding strings on complex projects.
    """
    locations = []
    for designation in examples:
        places = set()
        for row in rows:
            if designation not in row['entries']:
                continue
            page = row['page']
            table_row = row.get('table_row') if row.get('row_band') is not None else None
            places.add((page, table_row))
        if places:
            ordered = sorted(places, key=lambda place: (place[0], place[1] is None,
                                                       place[1] if place[1] is not None else 0))
            visible = []
            for page, table_row in ordered[:3]:
                source = f"лист PDF {page}"
                if table_row is not None:
                    source += f", строка таблицы {table_row}"
                visible.append(source)
            extra = len(ordered) - len(visible)
            remainder = f"; ещё {extra} мест(а)" if extra else ""
            locations.append(f"{designation}: " + "; ".join(visible) + remainder)
    return " Места в ПЭ3: " + " | ".join(locations) + "." if locations else ""

def _match_evidence(overlap, scheme_sources, scheme_pages, rows, pe_filename):
    matches = []
    truncated = len(overlap) > MAX_MATCHES
    for designation in overlap[:MAX_MATCHES]:
        scheme = scheme_sources[designation]
        truncated |= len(scheme_pages[designation]) > MAX_LOCATIONS or any(capped for _, capped in scheme)
        elements = {}
        for row in rows:
            if designation not in row["entries"]:
                continue
            table_row = row.get("table_row") if row.get("row_band") is not None else None
            box, size = row.get("literal_box", ((), ()))
            location, _ = source(pe_filename, row["page"], table_row, size=size, boxes=[box])
            key = location_key(location)
            if key not in elements or "boxes_pt" in location:
                elements[key] = location
        truncated |= len(elements) > MAX_LOCATIONS
        matches.append({
            "designation": designation,
            "scheme": [location for location, _ in scheme],
            "element_list": [elements[key] for key in sorted(elements)[:MAX_LOCATIONS]],
        })
    return {"schema_version": 1, "kind": "pdf_text_match", "matches": matches, "truncated": truncated}

def run(e3_filename, e3_bytes, pe_filename, pe_bytes, *, include_evidence=False):
    findings = []
    rows = get_spec_rows(pe_bytes, include_evidence=True) if include_evidence else get_spec_rows(pe_bytes)
    if not rows:
        return [issue('warning', 'KD-PE-000', pe_filename, 'PDF', 'Не удалось надёжно извлечь строки перечня элементов', 'Используйте структурированную выгрузку или Excel для точной сверки')]
    from e3_pe3_inventory import compare_inventories, extract_mentions
    pe3_text = pe3_text_designations(pe_bytes)
    scheme = set()
    script_candidates = defaultdict(set)
    scheme_pages = defaultdict(set)
    script_form_pages = defaultdict(lambda: defaultdict(set))
    spec = set().union(*(r['entries'] for r in rows))
    scheme_sources = defaultdict(list)
    scheme_mentions = []
    e3_model_pages = defaultdict(set)
    e3_pages_processed = 0
    e3_pages_with_text = 0
    e3_word_count = 0
    scheme_truncated = False
    with fitz.open(stream=e3_bytes, filetype='pdf') as pdf:
        e3_truncated = len(pdf) > MAX_PDF_PAGES
        for pi, page in enumerate(islice(pdf, MAX_PDF_PAGES)):
            e3_pages_processed += 1
            words = page.get_text('words', sort=True)
            if words:
                e3_pages_with_text += 1
            e3_word_count += len(words)
            for token in model_tokens(page.get_text()):
                e3_model_pages[token].add(pi + 1)
            page_values = list(islice(chain(scheme_designations(page), scheme_letter_designations(page, spec)),
                                      MAX_SCHEME_DESIGNATIONS_PER_PAGE))
            if len(page_values) >= MAX_SCHEME_DESIGNATIONS_PER_PAGE:
                scheme_truncated = True
            matched_on_page = set()
            for value in page_values:
                n = normalized(value)
                scheme.add(n)
                script_candidates[n].add(value)
                scheme_pages[n].add(pi + 1)
                script_form_pages[n][value].add(pi + 1)
                if include_evidence and n in spec and len(scheme_sources[n]) < MAX_LOCATIONS:
                    matched_on_page.add(n)
            if matched_on_page:
                boxes = defaultdict(list)
                for word in words:
                    designation = normalized(word[4])
                    if designation in matched_on_page and current_tag().fullmatch(word[4]):
                        boxes[designation].append(word[:4])
                for designation in matched_on_page:
                    scheme_sources[designation].append(source(
                        e3_filename, pi + 1, size=(page.cropbox.width, page.cropbox.height),
                        boxes=boxes[designation],
                    ))
            if len(scheme_mentions) < MAX_SCHEME_MENTIONS:
                scheme_mentions.extend(extract_mentions(
                    words, pi + 1,
                    page_size=(page.cropbox.width, page.cropbox.height),
                    legacy_values=page_values,
                ))
            else:
                scheme_truncated = True
    # No blanket "missing" reporting: drawings may contain auxiliary devices,
    # split representations and OCR/text-order artifacts.
    for r in rows:
        # Semantic row model: only rows whose quantity counts whole devices are
        # eligible. Accessory/sub-part, bulk/length and ambiguous rows carry
        # different quantity semantics; their apparent mismatch is reported
        # by the semantic model (KD-EM-109-3), not by the legacy warning.
        _name = str(r.get('name') or '')
        _bulk = bool(_LENGTH_OR_BULK_UNITS_RE.search(_name))
        from adapters import _classify_spec_row
        semantic = _classify_spec_row(r, [], False)
        _role_ok = (not r['unknown'] and len(r['entries']) > 1
                    and not _bulk and semantic['row_role'] in ('standalone_device', 'grouped_position'))
        if not r['unknown'] and len(r['entries']) > 1 and len(r['entries']) != r['quantity'] and _role_ok:
            # The row number is evidence only when backed by PDF grid lines.
            # Unframed extraction retains page-only location without guessing.
            place = f"Лист PDF {r['page']}"
            if r.get('row_band') is not None and r.get('table_row') is not None:
                place += f", строка таблицы {r['table_row']}"
            findings.append(issue('warning', 'KD-PE-102', pe_filename, place, f"Для дополнительной проверки: количество {r['quantity']} отличается от числа распознанных обозначений ({len(r['entries'])}) в записи «{r['source']}»", 'Требуется проверка инженером: сопоставьте количество и явно указанные обозначения в исходной строке ПЭ3, включая переносы и диапазоны. Это не подтверждённая ошибка КД.'))
    for code, forms in sorted(script_candidates.items()):
        if len(forms) > 1 and any(any('А' <= c <= 'я' for c in s) for s in forms):
            # Each spelling is tied only to pages where that exact spelling
            # was extracted. Never treat a normalized match as proof of identity.
            examples = []
            for form in sorted(forms):
                pages = sorted(script_form_pages[code][form])
                shown = ', '.join(str(p) for p in pages[:3])
                hidden = f' (+{len(pages) - 3} листов)' if len(pages) > 3 else ''
                examples.append(f'{form} — лист(ы) PDF {shown}{hidden}')
            findings.append(issue('warning', 'KD-PE-103', e3_filename, 'Обозначения в Э3', f"Смешение похожих латинских и кириллических букв: {', '.join(sorted(forms))}. Места исходных написаний: {'; '.join(examples)}.", 'Унифицируйте алфавит в исходном проекте; проверьте, не разные ли это объекты'))
    overlap = sorted(spec & scheme)
    # Only the PDF text extracted from E3 is evidence of a match.
    examples = []
    for designation in overlap[:5]:
        pages = sorted(scheme_pages[designation])
        visible = ', '.join(str(page) for page in pages[:3])
        extra = f' (+{len(pages) - 3} листов)' if len(pages) > 3 else ''
        examples.append(f'{designation}: лист(ы) PDF {visible}{extra}')
    match_context = ' Примеры совпадений в Э3: ' + '; '.join(examples) + '.' if examples else ''
    findings.append(issue('info', 'KD-PE-100', pe_filename, 'Сопоставление Э3 ↔ ПЭ3', f'Из ПЭ3 извлечено {len(rows)} строк с обозначениями и количеством; уникальных обозначений — {len(spec)}. Из текста Э3 распознано {len(scheme)} обозначений; совпадений — {len(overlap)}.' + match_context, 'Для проверки полноты сопоставьте исходные Э3 и ПЭ3 или структурированный экспорт проекта. Это выборочная машинная сверка: отсутствие замечаний не подтверждает полноту схем и перечня'))
    if include_evidence and overlap:
        findings[-1]["evidence"] = _match_evidence(overlap, scheme_sources, scheme_pages, rows, pe_filename)
    findings.extend(compare_inventories(
        e3_filename, pe_filename, scheme_mentions, rows,
        e3_word_count=e3_word_count,
        e3_pages_processed=e3_pages_processed,
        e3_pages_with_text=e3_pages_with_text,
        e3_truncated=e3_truncated or scheme_truncated,
        pe3_truncated=any(row.get('pdf_truncated') for row in rows),
        pe3_text_designations=pe3_text,
    ))
    findings.extend(confusable_findings(e3_filename, pe_filename, e3_model_pages, rows))
    scheme_bases = {base_designator(value) for value in scheme}
    missing = sorted(value for value in spec if base_designator(value) not in scheme_bases)
    if missing:
        context = _unconfirmed_pe3_context(rows, missing[:8])
        findings.append(issue('info', 'KD-PE-101', pe_filename, 'Ограничения анализа', f'{len(missing)} обозначений ПЭ3 не подтверждены по извлечённому тексту Э3 (примеры: {", ".join(missing[:8])}).' + context + ' Это не считается ошибкой КД.', 'Для достоверной проверки использовать структурированный экспорт проекта EPLAN или проверить графику вручную'))
    return findings
