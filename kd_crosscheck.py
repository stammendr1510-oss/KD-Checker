"""Experimental, conservative E3 / PE3 PDF cross-check. No AI or network."""
from __future__ import annotations
import re
from collections import defaultdict
from itertools import islice
from pathlib import Path
import fitz
from pdf_match_evidence import MAX_LOCATIONS, MAX_MATCHES, location_key, source

MAX_PDF_PAGES = 100
MAX_SCHEME_DESIGNATIONS_PER_PAGE = 5000
MAX_SCHEME_MENTIONS = 50000

# Identify components in the electrical scheme primarily from unambiguous wire tags.
TAG_PREFIX = r'(?:[123])?(?:QF|SF|KM|KK|KV|KT|TA|SB|HLG|HLW|XG|XT|XN|XL|X|PAV)'
TAG = re.compile(r'(?<![\w.])'+TAG_PREFIX+r'\d+(?:\.\d+)?(?=\s*:|\b)', re.I)
RANGE = re.compile(r'^([\d]*[A-ZА-Я]+)(\d+)\.{2,3}([\d]*[A-ZА-Я]+)?(\d+)$', re.I)
CYR_LOOKALIKES = str.maketrans('АВСЕНКМОРТХУавсенкмортху', 'ABCEHKMOPTXYaBcehkmoptxy')

def normalized(s):
    # Normalization is used only for *matching*, not silently rewriting source files.
    return s.translate(CYR_LOOKALIKES).upper().strip().replace(' ', '')

def designation_type(name):
    m = re.search(r'\.((?:ПЭ3)|(?:Э3))(?=\s|\.|$)', Path(name).stem, re.I)
    return m.group(1).upper() if m else None

def pair_key(name):
    return re.sub(r'\.(?:ПЭ3|Э3)(?=\s|\.|$)', '.', Path(name).stem, flags=re.I).strip('. ').casefold()

def issue(level, code, file, place, message, recommendation):
    return {'severity':level, 'code':code, 'file':file, 'location':place, 'message':message, 'recommendation':recommendation}

def _notation_parts(raw):
    """Split a PE3 designator cell into ``(fragment, ambiguous)`` pairs.

    Only explicit separators (``;`` and ``,``) start a new fragment.  Inside a
    fragment, whitespace/newlines may only complete a wrapped notation
    (``1TA1...`` + ``1TA3`` or ``3TA1`` + ``...3TA3``).  If a complete
    designation/range is followed by a token that does not continue it, the
    whole fragment is marked ambiguous so the caller records it in ``unknown``
    instead of inventing separate designations from a numeric gap
    (``XT1 2`` must never become ``XT1`` or ``XT12``).
    """
    parts = []
    for chunk in re.split(r'[;,]', raw.replace('…', '...')):
        stripped = chunk.strip()
        if not stripped:
            continue
        joined = ''
        ambiguous = False
        for token in (token for token in re.split(r'\s+', stripped) if token):
            if not joined:
                joined = token
                continue
            left = normalized(joined)
            if (TAG.fullmatch(left) or RANGE.fullmatch(left)) and not token.startswith('.'):
                ambiguous = True
                break
            joined += token
        parts.append((stripped, True) if ambiguous else (joined, False))
    return parts


def expand_notation(raw):
    out, unknown = set(), []
    for fragment, ambiguous in _notation_parts(raw):
        if ambiguous:
            unknown.append(fragment)
            continue
        part = normalized(fragment)
        if not part: continue
        if '..' not in part:
            if TAG.fullmatch(part): out.add(part)
            else: unknown.append(fragment)
            continue
        m = RANGE.fullmatch(part)
        if not m:
            unknown.append(fragment); continue
        left, first, right, last = m.groups()
        if right and right != left or int(last)<int(first) or int(last)-int(first)>100:
            unknown.append(fragment); continue
        out.update(left+str(i) for i in range(int(first),int(last)+1))
    return out, unknown

def _row_rules(page, column_left):
    """Horizontal rules crossing the designator column (PDF points)."""
    left, right = column_left, page.rect.width*.188
    rules = set()
    for drawing in page.get_drawings():
        for item in drawing['items']:
            if item[0] == 'l':
                a, b = item[1:3]
                if abs(a.y-b.y) < .5 and min(a.x,b.x) <= left and max(a.x,b.x) >= right:
                    rules.add((a.y+b.y)/2)
            elif item[0] == 're':
                rect = item[1]
                if rect.x0 <= left and rect.x1 >= right:
                    rules.update((rect.y0, rect.y1))
    return sorted(rules)

def _quantity_words(page, words):
    """Use the quantity header's vertical borders to exclude numeric notes."""
    left, right = page.rect.width*.70, page.rect.width*.79
    headers = [w for w in words if left < w[0] < right
               and w[1] < page.rect.height*.1 and w[4].casefold().rstrip('.') == 'кол']
    if len(headers) == 1:
        header = headers[0]
        cx, cy = (header[0]+header[2])/2, (header[1]+header[3])/2
        borders = set()
        for drawing in page.get_drawings():
            for item in drawing['items']:
                if item[0] == 'l':
                    a, b = item[1:3]
                    if abs(a.x-b.x) < .5 and min(a.y,b.y) <= cy <= max(a.y,b.y):
                        borders.add((a.x+b.x)/2)
                elif item[0] == 're':
                    rect = item[1]
                    if rect.y0 <= cy <= rect.y1:
                        borders.update((rect.x0, rect.x1))
        before, after = [x for x in borders if x < cx], [x for x in borders if x > cx]
        if before and after:
            left, right = max(before), min(after)
    candidates = sorted([w for w in words if left < w[0] < right and re.fullmatch(r'\d+', w[4])
                         and w[1] < page.rect.height*.83], key=lambda w:(w[1],w[0]))
    rows = []
    for word in candidates:
        if rows and abs(word[1]-rows[-1][1]) < 2:
            # With no usable header, the leftmost number is the quantity;
            # a second number to its right belongs to notes on the same row.
            rows[-1] = min(rows[-1], word, key=lambda w:w[0])
        else:
            rows.append(word)
    return rows

def _row_band(quantities, index, rules):
    """A distinct grid cell containing exactly one quantity anchor, if known."""
    qty = quantities[index]
    center = (qty[1]+qty[3])/2
    above = [y for y in rules if y < center]
    below = [y for y in rules if y > center]
    if above and below:
        top, bottom = above[-1], below[0]
        # A border around the whole table is not a row boundary.
        if sum(top < (q[1]+q[3])/2 < bottom for q in quantities) == 1:
            return top, bottom
    return None


def _cell_words(words, quantities, index, rules, previous):
    qty = quantities[index]
    center = (qty[1]+qty[3])/2
    band = _row_band(quantities, index, rules)
    if band:
        top, bottom = band
        return [w for w in words if top < (w[1]+w[3])/2 < bottom], bottom

    # Without a grid, extend only within a PDF text block that intersects
    # this quantity and no other quantity. Never join arbitrary nearby lines.
    limit = min(qty[1]+8, quantities[index+1][1]-2) if index+1 < len(quantities) else qty[1]+8
    selected = [w for w in words if previous-2 <= w[1] <= limit]
    blocks = {w[5] for w in selected if w[1] <= center <= w[3]}
    for block in blocks:
        cell = [w for w in words if w[5] == block]
        top, bottom = min(w[1] for w in cell), max(w[3] for w in cell)
        if top >= previous-2 and sum(top <= (q[1]+q[3])/2 <= bottom for q in quantities) == 1:
            selected.extend(w for w in cell if w not in selected)
    last = max((w[1] for w in selected), default=qty[1])
    return selected, max(qty[1], last)+9

def _duplicate_range_spelling_is_exact(notation):
    # The legacy range expander converts numbers to int. For duplicate evidence
    # do not equate a padded source like KM01...KM03 with a separate KM1.
    for fragment, ambiguous in _notation_parts(notation):
        if ambiguous:
            continue
        match = RANGE.fullmatch(normalized(fragment))
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
                segments = [(r.tl,r.tr),(r.bl,r.br),(r.tl,r.bl),(r.tr,r.br)]
            else:
                continue
            for a, b in segments:
                if abs(a.x-b.x)<.5 and min(a.y,b.y)<=top+.5 and max(a.y,b.y)>=bottom-.5:
                    vertical.append((a.x+b.x)/2)
                if abs(a.y-b.y)<.5:
                    horizontal.append((min(a.x,b.x),max(a.x,b.x),(a.y+b.y)/2))
    center = page.rect.width*(.09+.188)/2
    before, after = [x for x in vertical if x<center], [x for x in vertical if x>center]
    if not before or not after:
        return None
    left, right = max(before), min(after)
    if right-left>page.rect.width*.15:
        return None
    if not all(any(x0<=left+.5 and x1>=right-.5 and abs(y-edge)<.5
                   for x0,x1,y in horizontal) for edge in band):
        return None
    return left, right

def get_spec_rows(pdf_bytes, *, include_evidence=False):
    """Read designator column and qty using original PDF word coordinates.
    Only rows with an identifiable numeric qty and designator are accepted.
    Page traversal is bounded by the same 100-page PDF limit as the reader.
    """
    rows=[]
    with fitz.open(stream=pdf_bytes, filetype='pdf') as pdf:
        truncated = len(pdf) > MAX_PDF_PAGES
        for pi, page in enumerate(islice(pdf, MAX_PDF_PAGES)):
            words=page.get_text('words',sort=True)
            page_size = (float(page.cropbox.width), float(page.cropbox.height))
            name_words = [t for t in words
                          if page.rect.width*.188 <= t[0] < page.rect.width*.70
                          and t[1] < page.rect.height*.83]
            qty = _quantity_words(page, words)
            previous=50
            column=[t for t in words if page.rect.width*.09 <= t[0] < page.rect.width*.188 and t[1]<page.rect.height*.83]
            rules = _row_rules(page, min((w[0] for w in column), default=page.rect.width*.09))
            for index, w in enumerate(qty):
                des, previous = _cell_words(column, qty, index, rules, previous)
                # PDF block/line identifiers preserve wrapped fragments without
                # rounding y coordinates into arbitrary six-point bands.
                lines = defaultdict(list)
                for t in des:
                    lines[(t[5],t[6])].append(t)
                ordered = sorted(lines.values(), key=lambda line:(min(t[1] for t in line), min(t[0] for t in line)))
                raw='\n'.join(''.join(t[4] for t in sorted(line,key=lambda t:t[0])) for line in ordered)
                # Word gaps also matter: XT1 and 2 must not silently become XT12.
                notation='\n'.join(' '.join(t[4] for t in sorted(line,key=lambda t:t[0])) for line in ordered)
                entries,unknown=expand_notation(notation)
                if unknown:
                    raw = notation
                if entries:
                    band = _row_band(qty, index, rules)
                    bounds = _duplicate_column_bounds(page, band) if not unknown else None
                    contained = bool(bounds and all(bounds[0]-.5<=t[0] and t[2]<=bounds[1]+.5 for t in des))
                    complete = bool(bounds and all(t in des for t in words
                        if bounds[0]<=t[0]<bounds[1] and band[0]<(t[1]+t[3])/2<band[1]))
                    name=''
                    name_confidence='low'
                    if band:
                        name_left = bounds[1] if bounds else page.rect.width*.188
                        selected = [t for t in name_words
                                    if name_left <= t[0] < page.rect.width*.70
                                    and band[0] < (t[1]+t[3])/2 < band[1]]
                        if selected:
                            selected.sort(key=lambda t:(t[1],t[0]))
                            name = re.sub(r'\s+', ' ', ' '.join(t[4] for t in selected)).strip()[:300]
                            name_confidence='medium'
                    rows.append({'page':pi+1,'source':raw,'quantity':int(w[4]),'entries':entries,'unknown':unknown,
                                 'row_band':band,
                                 'table_row':rules.index(band[0])+1 if band else None,
                                 'name':name,
                                 'name_confidence':name_confidence,
                                 'page_size_pt':page_size,
                                 'pdf_truncated':truncated,
                                 'duplicate_eligible':bool(band and band[0]>=50 and contained and complete and not unknown
                                                           and _duplicate_range_spelling_is_exact(notation))})
                    if include_evidence and contained and complete and len(des) == 1:
                        word = des[0]
                        if TAG.fullmatch(word[4]) and normalized(word[4]) in entries:
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
        locations = [f"Лист {row['page']}, строка таблицы {row['table_row']}"
                     for _, row in sorted(cells.items())]
        findings.append(issue(
            'warning', 'KD-PE-104', filename, '; '.join(locations),
            f'Для проверки инженером: обозначение «{designation}» распознано в {len(cells)} разных строках одного ПЭ3.',
            'Сопоставьте исходные ячейки перечня. Номера строк определены по горизонтальным границам таблицы, '
            'включая заголовок и пустые строки. Повтор может относиться к продолжению записи или особенностям '
            'комплектования; это не подтверждённая ошибка КД.',
        ))
    return findings


def check_spec_duplicates(filename, pdf_bytes):
    """Standalone PE3 validation; caller supplies a single document's bytes."""
    return duplicate_findings(get_spec_rows(pdf_bytes), filename)


def _separated_by_rule(page, a, b):
    left, right = min(a[0], b[0]), max(a[2], b[2])
    top, bottom = (a[1]+a[3])/2, (b[1]+b[3])/2
    for drawing in page.get_drawings():
        for item in drawing['items']:
            if item[0] == 'l':
                p, q = item[1:3]
                if abs(p.y-q.y) < .5 and top < (p.y+q.y)/2 < bottom and min(p.x,q.x) <= left and max(p.x,q.x) >= right:
                    return True
            elif item[0] == 're':
                rect = item[1]
                if rect.x0 <= left and rect.x1 >= right and any(top < y < bottom for y in (rect.y0,rect.y1)):
                    return True
    return False


def scheme_designations(page):
    """Recover only an incomplete prefix + numeric suffix in adjacent PDF lines.
    Standalone complete labels, different blocks, columns and pages stay separate.
    """
    yield from (m.group(0) for m in TAG.finditer(page.get_text()))
    blocks = defaultdict(lambda: defaultdict(list))
    for word in page.get_text('words', sort=True):
        blocks[word[5]][word[6]].append(word)
    for lines in blocks.values():
        ordered = sorted(lines.values(), key=lambda words:min(w[1] for w in words))
        if len(ordered) != 2:
            continue
        first, second = ordered
        if len(first) != 1 or len(second) != 1:
            continue
        a, b = first[0], second[0]
        if not re.fullmatch(r'[123]?[A-Z]+', a[4], re.I) or not re.fullmatch(r'[A-Z]*\d+(?:\.\d+)?', b[4], re.I):
            continue
        height = max(a[3]-a[1], b[3]-b[1])
        if abs(a[0]-b[0]) > 2 or not (0 < b[1]-a[1] <= height*1.6):
            continue
        candidate = a[4]+b[4]
        if TAG.fullmatch(candidate) and not _separated_by_rule(page, a, b):
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


def run(e3_filename,e3_bytes,pe_filename,pe_bytes, *, include_evidence=False):
    findings=[]
    rows=get_spec_rows(pe_bytes, include_evidence=True) if include_evidence else get_spec_rows(pe_bytes)
    if not rows:
        return [issue('warning','KD-PE-000',pe_filename,'PDF','Не удалось надёжно извлечь строки перечня элементов','Используйте структурированную выгрузку или Excel для точной сверки')]
    from e3_pe3_inventory import compare_inventories, extract_mentions
    scheme=set(); script_candidates=defaultdict(set); scheme_pages=defaultdict(set)
    script_form_pages=defaultdict(lambda: defaultdict(set))
    spec=set().union(*(r['entries'] for r in rows))
    scheme_sources=defaultdict(list)
    scheme_mentions=[]
    e3_pages_processed=0
    e3_pages_with_text=0
    e3_word_count=0
    scheme_truncated=False
    with fitz.open(stream=e3_bytes, filetype='pdf') as pdf:
        e3_truncated = len(pdf) > MAX_PDF_PAGES
        for pi,page in enumerate(islice(pdf, MAX_PDF_PAGES)):
            e3_pages_processed += 1
            words = page.get_text('words', sort=True)
            if words:
                e3_pages_with_text += 1
            e3_word_count += len(words)
            page_values = list(islice(scheme_designations(page), MAX_SCHEME_DESIGNATIONS_PER_PAGE))
            if len(page_values) >= MAX_SCHEME_DESIGNATIONS_PER_PAGE:
                scheme_truncated = True
            matched_on_page = set()
            for value in page_values:
                n=normalized(value)
                scheme.add(n)
                script_candidates[n].add(value)
                scheme_pages[n].add(pi+1)
                script_form_pages[n][value].add(pi+1)
                if include_evidence and n in spec and len(scheme_sources[n]) < MAX_LOCATIONS:
                    matched_on_page.add(n)
            if matched_on_page:
                boxes = defaultdict(list)
                for word in words:
                    designation = normalized(word[4])
                    if designation in matched_on_page and TAG.fullmatch(word[4]):
                        boxes[designation].append(word[:4])
                for designation in matched_on_page:
                    scheme_sources[designation].append(source(
                        e3_filename, pi+1, size=(page.cropbox.width, page.cropbox.height),
                        boxes=boxes[designation],
                    ))
            if len(scheme_mentions) < MAX_SCHEME_MENTIONS:
                scheme_mentions.extend(extract_mentions(
                    words, pi+1,
                    page_size=(page.cropbox.width, page.cropbox.height),
                    legacy_values=page_values,
                ))
            else:
                scheme_truncated = True
    # No blanket "missing" reporting: drawings may contain auxiliary devices,
    # split representations and OCR/text-order artifacts.
    for r in rows:
        if not r['unknown'] and len(r['entries'])>1 and len(r['entries'])!=r['quantity']:
            # The row number is evidence only when backed by PDF grid lines.
            # Unframed extraction retains page-only location without guessing.
            place = f"Лист PDF {r['page']}"
            if r.get('row_band') is not None and r.get('table_row') is not None:
                place += f", строка таблицы {r['table_row']}"
            findings.append(issue('warning','KD-PE-102',pe_filename,place,f"Для дополнительной проверки: количество {r['quantity']} отличается от числа распознанных обозначений ({len(r['entries'])}) в записи «{r['source']}»",'Требуется проверка инженером: сопоставьте количество и явно указанные обозначения в исходной строке ПЭ3, включая переносы и диапазоны. Это не подтверждённая ошибка КД.'))
    for code,forms in sorted(script_candidates.items()):
        if len(forms)>1 and any(any('А'<=c<='я' for c in s) for s in forms):
            # Each spelling is tied only to pages where that exact spelling
            # was extracted. Never treat a normalized match as proof of identity.
            examples = []
            for form in sorted(forms):
                pages = sorted(script_form_pages[code][form])
                shown = ', '.join(str(p) for p in pages[:3])
                hidden = f' (+{len(pages)-3} листов)' if len(pages)>3 else ''
                examples.append(f'{form} — лист(ы) PDF {shown}{hidden}')
            findings.append(issue('warning','KD-PE-103',e3_filename,'Обозначения в Э3',f"Смешение похожих латинских и кириллических букв: {', '.join(sorted(forms))}. Места исходных написаний: {'; '.join(examples)}.",'Унифицируйте алфавит в исходном проекте; проверьте, не разные ли это объекты'))
    overlap=sorted(spec & scheme)
    # Only the PDF text extracted from E3 is evidence of a match.
    examples = []
    for designation in overlap[:5]:
        pages = sorted(scheme_pages[designation])
        visible = ', '.join(str(page) for page in pages[:3])
        extra = f' (+{len(pages)-3} листов)' if len(pages)>3 else ''
        examples.append(f'{designation}: лист(ы) PDF {visible}{extra}')
    match_context = ' Примеры совпадений в Э3: ' + '; '.join(examples) + '.' if examples else ''
    findings.append(issue('info','KD-PE-100',pe_filename,'Сопоставление Э3 ↔ ПЭ3',f'Из ПЭ3 извлечено {len(rows)} строк с обозначениями и количеством; уникальных обозначений — {len(spec)}. Из текста Э3 распознано {len(scheme)} обозначений; совпадений — {len(overlap)}.' + match_context,'Для проверки полноты сопоставьте исходные Э3 и ПЭ3 или структурированный экспорт проекта. Это выборочная машинная сверка: отсутствие замечаний не подтверждает полноту схем и перечня'))
    if include_evidence and overlap:
        findings[-1]["evidence"] = _match_evidence(overlap, scheme_sources, scheme_pages, rows, pe_filename)
    missing=sorted(spec-scheme)
    if missing:
        context = _unconfirmed_pe3_context(rows, missing[:8])
        findings.append(issue('info','KD-PE-101',pe_filename,'Ограничения анализа',f'{len(missing)} обозначений ПЭ3 не подтверждены по извлечённому тексту Э3 (примеры: {", ".join(missing[:8])}).' + context + ' Это не считается ошибкой КД.','Для достоверной проверки использовать структурированный экспорт проекта EPLAN или проверить графику вручную'))
    findings.extend(compare_inventories(
        e3_filename, pe_filename, scheme_mentions, rows,
        e3_word_count=e3_word_count,
        e3_pages_processed=e3_pages_processed,
        e3_pages_with_text=e3_pages_with_text,
        e3_truncated=e3_truncated or scheme_truncated,
        pe3_truncated=any(row.get('pdf_truncated') for row in rows),
    ))
    return findings


