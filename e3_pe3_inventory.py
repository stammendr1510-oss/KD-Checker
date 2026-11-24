"""Conservative E3 -> PE3 designation inventory comparison (KD-PE-105).

The module is pure: callers supply extracted E3 words and already parsed PE3
rows. It never opens PDF files, never uses the network and never writes data.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
import json
import math
import re

from kd_crosscheck import MAX_PDF_PAGES, TAG, designation_type, normalized, pair_key
from identifier_classification import (
    AMBIGUOUS,
    CONNECTION_REFERENCE,
    CROSS_REFERENCE,
    DEVICE_CANDIDATE,
    TERMINAL_OR_PIN,
    classification_report,
    classify_component,
)
from kd_pe_105_confidence import (
    MATCH_EXACT,
    MATCH_FAMILY_GROUP,
    MATCH_FAMILY_ONLY,
    MATCH_NONE,
    MATCH_UNDER_EXPANDED,
    assess_finding,
    validate_combination,
)


CODE = "KD-PE-105"

# KD-PE-113: bulk/length unit signatures (kept in sync with
# engineering_model.SpecRow); cable/wire/rail rows declare lengths, not
# device instances, and never under-expand a designation list.
_BULK_UNITS_RE = re.compile(
    r"(?<![\w])(?:м|мм|мм²|м2|см|км|кг|г|м2/м|пог\.?\s*м|м\.)(?![\w])", re.I)
DETAILS_SCHEMA_VERSION = 1
DETAILS_KIND = "e3_pe3_missing_designations"

MAX_COMPONENTS = 5000
MAX_MENTIONS_PER_COMPONENT = 20
MAX_PAGE_MENTIONS = 10000
MAX_EVIDENCE = 3
MAX_EVIDENCE_PAGES = 10
MAX_DETAIL_PAGES = MAX_PDF_PAGES
MAX_LISTED = 1000
MAX_AMBIGUOUS = 100
MAX_DETAILS_BYTES = 256 * 1024
MAX_MESSAGE = 14000
MAX_RECOMMENDATION = 4000
MAX_CONTEXT = 300
MAX_DOCUMENT = 1000
MAX_NAME = 300
MAX_SOURCE = 300
MAX_TYPE_NAME = 120
MAX_REASON = 500
MAX_MESSAGE_LISTED = 10
MAX_RECOMMENDATION_LISTED = 20

TERMINAL_FAMILIES = frozenset({"X", "XT", "XN", "XL", "XG"})
TYPE_NAMES = {
    "TA": "трансформатор тока",
    "KM": "контактор",
    "QF": "автоматический выключатель",
    "SF": "выключатель",
    "KK": "реле",
    "KV": "реле",
    "KT": "реле",
    "KA": "реле",
    "KL": "реле/контактор",
    "K": "реле/контактор",
    "KLD": "релейный модуль",
    "SB": "кнопка",
    "HLG": "лампа",
    "HLW": "лампа",
    "HL": "лампа",
    "XT": "клеммник",
    "XN": "клеммник",
    "XL": "клеммник",
    "XG": "клеммник",
    "X": "клеммник/соединение",
    "PAV": "прибор",
}
KINDS = frozenset({"label", "split_label", "reference", "note", "table", "unknown"})
CONFIDENCE_LEVELS = frozenset({"high", "medium", "low"})
STATUSES = frozenset({"evaluated", "not_evaluated"})
REASONS = frozenset({
    "quantity_mismatch", "reference_only", "no_text_layer", "pe3_not_parsed",
    "truncated", "no_matching_pe3_row", "row_matched_without_coverage",
    "row_ambiguous", "needs_context",
    # The designation is visible in the PE3 text layer, but its row could not
    # be parsed reliably.  This prevents a false "missing product" conclusion.
    "present_in_unparsed_pe3_text",
    # Deprecated: emitted by schema v1 before block 2. Kept readable so old
    # stored details can still be inspected, but never produced again.
    "ambiguous_row",
})
REASON_LABELS = {
    "quantity_mismatch": "количество в ПЭ3 не совпадает с раскрытыми обозначениями",
    "reference_only": "обозначения встречены только в ссылках или примечаниях",
    "no_text_layer": "в PDF Э3 не обнаружен текстовый слой",
    "pe3_not_parsed": "позиционные обозначения ПЭ3 не извлечены достоверно",
    "truncated": "обработка PDF ограничена",
    "no_matching_pe3_row": "не найдена строка ПЭ3 с подходящим типом",
    "row_matched_without_coverage": (
        "строка ПЭ3 похожа по типу и группе, но не доказывает принадлежность обозначения"
    ),
    "row_ambiguous": (
        "несколько строк ПЭ3 одинаково подходят; выбор строки не выполняется"
    ),
    "needs_context": (
        "обозначение неоднозначно; для отнесения к изделию требуется дополнительный контекст"
    ),
    "present_in_unparsed_pe3_text": (
        "обозначение присутствует в текстовом слое ПЭ3, но строка не разобрана достоверно"
    ),
    "ambiguous_row": "устаревшая причина: неоднозначная строка ПЭ3",
}

# Evidence classes for row attribution live in the central confidence model.
# Only ``under_expanded`` is proof that a row is under-specified; family/group
# coincidence stays tentative.  They are re-exported here for compatibility.

# Only an explicit marker *adjacent to the token* changes its occurrence role.
# A marker elsewhere on the line (for example a page reference in a table
# header) must not hide a standalone device designation.
REFERENCE_MARKER_RE = re.compile(
    r"(?:\bсм\.?\s*|\bсмотри\s+|\bпоз\.?\s*|\bпозици[яи]\s*|"
    r"\bприм(?:еч)?\.?\s*|\bтабл\.?\s*|\bлист\s*)$",
    re.I,
)
NOTE_LINE_RE = re.compile(r"^\s*прим(?:еч)?", re.I)
DOC_REF_RE = re.compile(r"\b[А-ЯA-Z]{2,}\.\d{3,}\.\d{3,}\b", re.I)
TRUNCATION_MARK = "…"
CONNECTION_RE = re.compile(
    r"[A-ZА-Я0-9.]+\s*:\s*[A-ZА-Я0-9.]+\s*[-–—]\s*[A-ZА-Я0-9.]+\s*:"
)
PARSE_DESIGNATION_RE = re.compile(r"^(\d*)([A-Z]+)(\d+)(?:\.\d+)?$")


@dataclass(frozen=True)
class Mention:
    designation: str
    raw: str
    page: int
    kind: str
    context: str
    confidence: str
    bbox: tuple[float, float, float, float] | None = None
    page_size: tuple[float, float] | None = None
    # Full source line (when available) lets block 3 see a pin suffix even if
    # the PDF split ``1KM1`` and ``:A1`` into separate words.
    line_text: str = ""


@dataclass
class SchemeComponent:
    designation: str
    raw_forms: set[str] = field(default_factory=set)
    pages: set[int] = field(default_factory=set)
    kinds: set[str] = field(default_factory=set)
    mentions: list[Mention] = field(default_factory=list)
    confidence: str = "low"
    # Block 3 contextual classification.  Only ``eligible_for_presence_check``
    # components may be reported as standalone products missing from PE3.
    category: str = ""
    classification_reason: str = ""
    classification_confidence: str = "low"
    parent_device: str | None = None
    eligible_for_presence_check: bool = False
    presence_evidence: bool = False
    requires_context: bool = False


def _clean(value, limit, *, mark_truncation=False):
    text = str(value if value is not None else "")
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if mark_truncation and len(text) > limit:
        return text[:max(0, limit-1)] + TRUNCATION_MARK
    return text[:limit]


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _page_size(value):
    return (isinstance(value, (tuple, list)) and len(value) == 2
            and all(_number(item) and item > 0 for item in value))


def _bbox(value, page_size):
    return (isinstance(value, (tuple, list)) and len(value) == 4
            and all(_number(item) for item in value)
            and _page_size(page_size)
            and 0 <= value[0] < value[2] <= page_size[0]
            and 0 <= value[1] < value[3] <= page_size[1])


def _kind_confidence(kind):
    if kind == "label":
        return "high"
    if kind in ("split_label", "reference"):
        return "medium"
    return "low"


def family_of(designation):
    match = re.match(r"^\d*([A-Z]+)", normalized(designation))
    return match.group(1) if match else ""


def group_of(designation):
    match = re.match(r"^(\d*)", normalized(designation))
    return match.group(1) if match else ""


def base_of(designation):
    value = normalized(designation)
    match = re.fullmatch(r"(.+)\.(\d+)", value)
    if match and TAG.fullmatch(match.group(1)):
        return match.group(1)
    return value


def parse_designation(designation):
    match = PARSE_DESIGNATION_RE.fullmatch(normalized(designation))
    if not match:
        return None
    return match.group(1) + match.group(2), int(match.group(3))


def _reference_role(line_text, word_text):
    """Return a reference/note role only when the marker is adjacent.

    A marker elsewhere on the line is context, not proof that the token is a
    reference.  This is the KD106-02 rule: the property of the designation
    (a standalone device label) is independent of a nearby word such as
    ``лист``, ``табл`` or ``примечание``.
    """
    position = line_text.find(word_text)
    before = line_text[:position] if position >= 0 else ""
    if REFERENCE_MARKER_RE.search(before):
        return "reference"
    if NOTE_LINE_RE.match(line_text):
        return "note"
    return None


def _classify_mention(word_text, norm_text, match, line_text):
    after = norm_text[match.end():match.end() + 1]
    if after == ":":
        return "reference"
    if any(mark in word_text for mark in ("...", "…")) or ";" in word_text:
        return "table"
    if line_text.count(":") >= 2 or CONNECTION_RE.search(line_text):
        return "reference"
    role = _reference_role(line_text, word_text)
    if role:
        return role
    stripped = word_text.strip(".,;:()[]{}")
    if stripped.startswith(("-", "–", "—")):
        return "reference"
    if stripped == word_text or stripped == word_text[:match.end()]:
        tokens = line_text.split()
        if len(tokens) >= 2 and any(token.isdigit() for token in tokens):
            if not any(any(ch.isalpha() for ch in token) for token in tokens if token != word_text):
                return "table"
        return "label"
    return "reference" if ":" in word_text else "unknown"


def _line_words(words):
    lines = defaultdict(list)
    for word in words:
        if not isinstance(word, (tuple, list)) or len(word) < 7:
            continue
        lines[(word[5], word[6])].append(word)
    for line in lines.values():
        line.sort(key=lambda word: (word[0], word[1]))
    return lines


def extract_mentions(words, page, *, page_size=None, legacy_values=()):
    """Return bounded, context-classified mentions for one E3 PDF page."""
    size = (float(page_size[0]), float(page_size[1])) if _page_size(page_size) else None
    found = []
    seen = set()
    for line in _line_words(words).values():
        line_text = " ".join(str(word[4]) for word in line)
        for word in line:
            text = str(word[4])
            norm_text = normalized(text)
            for match in TAG.finditer(norm_text):
                raw = text[match.start():match.end()]
                designation = normalized(raw)
                if not designation:
                    continue
                kind = _classify_mention(text, norm_text, match, line_text)
                key = (designation, kind)
                if key in seen:
                    continue
                seen.add(key)
                box = None
                if size and _bbox(word[:4], size):
                    box = tuple(float(value) for value in word[:4])
                context = raw if kind in ("label", "split_label") else line_text
                found.append(Mention(
                    designation=designation,
                    raw=_clean(raw, 100),
                    page=page,
                    kind=kind,
                    context=_clean(context, MAX_CONTEXT, mark_truncation=True),
                    confidence=_kind_confidence(kind),
                    bbox=box,
                    page_size=size if box else None,
                    line_text=_clean(line_text, MAX_CONTEXT, mark_truncation=True),
                ))
                if len(found) >= MAX_PAGE_MENTIONS:
                    break
            if len(found) >= MAX_PAGE_MENTIONS:
                break
        if len(found) >= MAX_PAGE_MENTIONS:
            break

    present = {mention.designation for mention in found}
    for value in legacy_values:
        designation = normalized(value)
        if not designation or designation in present or not TAG.fullmatch(designation):
            continue
        found.append(Mention(
            designation=designation,
            raw=_clean(value, 100),
            page=page,
            kind="split_label",
            context=_clean(value, MAX_CONTEXT, mark_truncation=True),
            confidence="medium",
            bbox=None,
            page_size=None,
        ))
        present.add(designation)
        if len(found) >= MAX_PAGE_MENTIONS:
            break
    return found


def build_components(mentions):
    grouped = defaultdict(list)
    for mention in mentions:
        grouped[mention.designation].append(mention)
    components = {}
    truncated = False
    for designation in sorted(grouped):
        if len(components) >= MAX_COMPONENTS:
            truncated = True
            break
        items = grouped[designation]
        component = SchemeComponent(designation=designation)
        for mention in items[:MAX_MENTIONS_PER_COMPONENT]:
            component.raw_forms.add(mention.raw)
            component.pages.add(mention.page)
            component.kinds.add(mention.kind)
            component.mentions.append(mention)
        if "label" in component.kinds:
            component.confidence = "high"
        elif "split_label" in component.kinds:
            component.confidence = "medium"
        elif "reference" in component.kinds and len(component.pages) >= 2:
            component.confidence = "medium"
        else:
            component.confidence = "low"
        classification = classify_component(component)
        component.category = classification.category
        component.classification_reason = classification.reason
        component.classification_confidence = classification.confidence
        component.parent_device = classification.parent_device
        component.eligible_for_presence_check = classification.eligible_for_presence_check
        component.presence_evidence = classification.presence_evidence
        component.requires_context = classification.requires_context
        components[designation] = component
    return components, truncated


def classification_summary(mentions):
    """Report which E3 identifiers may be checked for presence in PE3.

    See :func:`identifier_classification.classification_report` for the shape
    of the result.  Ambiguous identifiers are preserved here instead of being
    silently dropped.
    """
    return classification_report(mentions)


def _row_families(row):
    return {family_of(entry) for entry in (row.get("entries") or set()) if family_of(entry)}


def _row_confidence(row):
    entries = row.get("entries") or set()
    quantity = row.get("quantity")
    if not entries or not isinstance(quantity, int) or quantity < 0 or row.get("unknown"):
        return "low"
    if row.get("duplicate_eligible"):
        return "high"
    if row.get("row_band") is None:
        return "low"
    if len(entries) <= 1 and quantity > 1:
        return "low"
    return "medium"


def _row_key(row):
    if row is None:
        return None
    return (row.get("page"), row.get("table_row"), row.get("quantity"),
            tuple(sorted(row.get("entries") or set())))


def _group_compatible(designation, entries):
    """A designation without a leading group is compatible with any row.

    Group identity is *not* proof of row ownership; it only narrows the set of
    tentative candidates.  A concrete group must be present in the row.
    """
    group = group_of(designation)
    if not group:
        return True
    return any(group_of(entry) == group for entry in entries)


def _row_match_evidence(component, row):
    """Classify how strongly a PE3 row can be tied to a missing designation.

    ``exact``            - the designation (or its contact base) is in the row;
    ``under_expanded``   - the row declares more items than it expands and the
                           group is compatible, so the row is under-specified;
    ``family_group``     - same family and group, but no quantity/range proof;
    ``family_only``      - same letter family, different/unknown group;
    ``none``             - different family.

    Only ``under_expanded`` may support a warning.  A shared letter prefix is
    never promoted to a confirmed row.
    """
    designation = component.designation
    entries = row.get("entries") or set()
    if not entries:
        return MATCH_NONE
    if designation in entries or base_of(designation) in entries:
        return MATCH_EXACT
    families = {family_of(entry) for entry in entries if family_of(entry)}
    if family_of(designation) not in families:
        return MATCH_NONE
    # An unreadable/unknown part makes the row unsafe for attribution.
    if row.get("unknown"):
        return MATCH_FAMILY_ONLY
    if not _group_compatible(designation, entries):
        return MATCH_FAMILY_ONLY
    # KD-PE-113: a row whose declared quantity is length/bulk units (cable,
    # wire, rail by the metre) under-declares nothing about device instances.
    # It can never serve as under_expanded evidence for a missing designation.
    name = str(row.get("name") or "")
    if name and _BULK_UNITS_RE.search(name):
        return MATCH_FAMILY_ONLY
    quantity = row.get("quantity")
    if isinstance(quantity, int) and quantity > len(entries):
        return MATCH_UNDER_EXPANDED
    return MATCH_FAMILY_GROUP


def _candidate_signature(rows):
    return tuple(sorted(_row_key(row) for row in rows if row is not None))


def _attribute_component(component, rows_by_family):
    """Return ``(row, reason, evidence, candidate_signature)``.

    The function deliberately refuses to choose an arbitrary row when several
    rows carry the same evidence.  In that case ``row`` is ``None`` and the
    reason is ``row_ambiguous``; the candidate signature keeps unrelated
    ambiguities in separate findings.
    """
    candidates = [row for row in rows_by_family.get(family_of(component.designation), [])
                  if row.get("entries")]
    if not candidates:
        return None, "no_matching_pe3_row", MATCH_NONE, ()

    classified = [(row, _row_match_evidence(component, row)) for row in candidates]
    confirmed = [row for row, kind in classified if kind == MATCH_UNDER_EXPANDED]
    if len(confirmed) == 1:
        return confirmed[0], "quantity_mismatch", MATCH_UNDER_EXPANDED, _candidate_signature(confirmed)
    if len(confirmed) > 1:
        return (None, "row_ambiguous", "multiple_quantity_mismatch",
                _candidate_signature(confirmed))

    tentative = [row for row, kind in classified if kind == MATCH_FAMILY_GROUP]
    if len(tentative) == 1:
        return (tentative[0], "row_matched_without_coverage", MATCH_FAMILY_GROUP,
                _candidate_signature(tentative))
    if len(tentative) > 1:
        return (None, "row_ambiguous", "multiple_family_group",
                _candidate_signature(tentative))

    # Same letter family but no compatible group: explicitly not a match.
    evidence = MATCH_FAMILY_ONLY if classified else MATCH_NONE
    return None, "no_matching_pe3_row", evidence, ()


def _suggest_range(missing, row):
    if row is None:
        return ""
    prefixes = {group_of(designation) + family_of(designation) for designation in missing}
    if len(prefixes) != 1:
        return ""
    prefix = next(iter(prefixes))
    if not prefix:
        return ""
    numbers = []
    for value in list(row.get("entries") or set()) + list(missing):
        parsed = parse_designation(value)
        if parsed and parsed[0] == prefix:
            numbers.append(parsed[1])
    if not numbers:
        return ""
    numbers = sorted(set(numbers))
    if numbers != list(range(numbers[0], numbers[-1] + 1)):
        return ""
    return f"{prefix}{numbers[0]}...{prefix}{numbers[-1]}"


def _format_pages(pages):
    ordered = sorted(set(pages))
    shown = ", ".join(str(page) for page in ordered[:MAX_EVIDENCE_PAGES])
    if len(ordered) > MAX_EVIDENCE_PAGES:
        shown += f" (+{len(ordered) - MAX_EVIDENCE_PAGES} листов)"
    return shown or "не установлены"


def _format_designations(designations, limit):
    ordered = sorted(set(designations))
    shown = ", ".join(ordered[:limit])
    if len(ordered) > limit:
        shown += f" и ещё {len(ordered) - limit}"
    return shown


def _candidate_sort_key(key):
    page = key[0] if isinstance(key, (tuple, list)) and key else 0
    table_row = key[1] if isinstance(key, (tuple, list)) and len(key) > 1 else None
    return (
        page if type(page) is int else 0,
        table_row is None,
        table_row if type(table_row) is int else 0,
    )


def _format_candidate_rows(candidates, limit=10):
    """Human-readable PE3 candidate rows without unconfirmed coordinates.

    Only the source page and a grid-confirmed table row are shown.  A missing
    row number is stated explicitly instead of guessed from text proximity.
    """
    labels = []
    for key in sorted(set(candidates), key=_candidate_sort_key):
        if not isinstance(key, (tuple, list)) or not key:
            continue
        page = key[0]
        table_row = key[1] if len(key) > 1 else None
        if type(page) is not int or page < 1:
            continue
        label = f"лист PDF {page}"
        if type(table_row) is int and table_row > 0:
            label += f", строка таблицы {table_row}"
        else:
            label += " (строка не подтверждена)"
        labels.append(label)
    shown = labels[:limit]
    text = "; ".join(shown)
    if len(labels) > len(shown):
        text += f"; ещё {len(labels) - len(shown)}"
    return text


def _confidence_label(value):
    return {"high": "высокий", "medium": "средний", "low": "низкий"}.get(value, "низкий")


def _evidence(component):
    evidence = []
    seen = set()
    for mention in component.mentions:
        key = (mention.page, mention.kind, mention.context)
        if key in seen:
            continue
        seen.add(key)
        item = {"page": mention.page, "kind": mention.kind, "context": mention.context}
        if mention.bbox and mention.page_size:
            item["bbox_pt"] = list(mention.bbox)
            item["page_size_pt"] = list(mention.page_size)
        evidence.append(item)
        if len(evidence) >= MAX_EVIDENCE:
            break
    return evidence


def _missing_item(component):
    return {
        "designation": component.designation,
        "raw": sorted(component.raw_forms)[0] if component.raw_forms else component.designation,
        "pages": sorted(component.pages)[:MAX_EVIDENCE_PAGES],
        "kinds": sorted(component.kinds),
        "evidence": _evidence(component),
    }


def _ambiguous_item(component, reason=None):
    if reason is None:
        reason = ("needs_context"
                  if getattr(component, "category", "") == AMBIGUOUS
                  else "reference_only")
    return {
        "designation": component.designation,
        "raw": sorted(component.raw_forms)[0] if component.raw_forms else component.designation,
        "pages": sorted(component.pages)[:MAX_EVIDENCE_PAGES],
        "kinds": sorted(component.kinds),
        "reason": reason,
    }


def _details_base(e3_document, pe3_document, status, reason, e3_pages):
    return {
        "schema_version": DETAILS_SCHEMA_VERSION,
        "kind": DETAILS_KIND,
        "status": status,
        "reason": reason,
        "e3_document": _clean(e3_document, MAX_DOCUMENT),
        "pe3_document": _clean(pe3_document, MAX_DOCUMENT),
        "e3_pages": sorted(set(e3_pages))[:MAX_DETAIL_PAGES],
        "pe3_page": None,
        "pe3_table_row": None,
        "pe3_row_band_pt": None,
        "pe3_page_size_pt": None,
        "pe3_quantity": None,
        "pe3_entries_count": None,
        "pe3_name": "",
        "pe3_name_confidence": "low",
        "pe3_source": "",
        "type_family": "",
        "type_name": "",
        "confidence": "low",
        "partial": False,
        "truncated": False,
        "missing": [],
        "ambiguous": [],
        "limits": {
            "max_pages": MAX_PDF_PAGES,
            "max_listed": MAX_LISTED,
            "truncated_list": False,
        },
    }


def _details_for_group(details, row, components, reason, confidence):
    result = dict(details)
    result["reason"] = reason
    result["missing"] = [_missing_item(component) for component in components[:MAX_LISTED]]
    # Confidence is evidence quality, not a function of severity.  A tentative
    # or ambiguous row can never be reported as high confidence.
    result["confidence"] = confidence
    if len(components) > MAX_LISTED:
        result["limits"] = dict(result["limits"], truncated_list=True)
    while len(result["missing"]) > 1:
        try:
            size = len(json.dumps(result, ensure_ascii=False, allow_nan=False,
                                  sort_keys=True, separators=(",", ":")).encode("utf-8"))
        except (TypeError, ValueError):
            break
        if size <= MAX_DETAILS_BYTES:
            break
        result["missing"].pop()
        result["limits"] = dict(result["limits"], truncated_list=True)
    if row is not None:
        result["pe3_page"] = row.get("page")
        result["pe3_table_row"] = row.get("table_row") if row.get("row_band") is not None else None
        result["pe3_quantity"] = row.get("quantity")
        result["pe3_entries_count"] = len(row.get("entries") or set())
        result["pe3_name"] = _clean(row.get("name"), MAX_NAME)
        result["pe3_name_confidence"] = row.get("name_confidence", "low")
        result["pe3_source"] = _clean(row.get("source"), MAX_SOURCE)
        if row.get("row_band") is not None:
            result["pe3_row_band_pt"] = [float(value) for value in row["row_band"]]
        if _page_size(row.get("page_size_pt")):
            result["pe3_page_size_pt"] = [float(value) for value in row["page_size_pt"]]
    return result


def _finding(severity, file, location, message, recommendation, details):
    return {
        "severity": severity,
        "code": CODE,
        "file": _clean(file, MAX_DOCUMENT),
        "location": _clean(location, 1000),
        "message": _clean(message, MAX_MESSAGE),
        "recommendation": _clean(recommendation, MAX_RECOMMENDATION),
        "details": details,
    }


def _not_evaluated(e3_document, pe3_document, e3_pages, reason, *, ambiguous=(),
                   partial=False, truncated=False, ambiguous_reason=None):
    details = _details_base(e3_document, pe3_document, "not_evaluated", reason, e3_pages)
    details["ambiguous"] = [
        _ambiguous_item(component, ambiguous_reason)
        for component in ambiguous[:MAX_AMBIGUOUS]
    ]
    details["partial"] = bool(partial)
    details["truncated"] = bool(truncated)
    message = (
        "Проверка KD-PE-105 не выполнена (not_evaluated): "
        f"{REASON_LABELS.get(reason, reason)}. Отсутствие обозначений в ПЭ3 не утверждается."
    )
    recommendation = (
        "Выполните OCR или предоставьте PDF с текстовым слоем, затем повторите проверку. "
        "При неоднозначных ссылках проверьте схему и перечень вручную. "
        "Это не подтверждённая ошибка КД."
    )
    return _finding("info", pe3_document, "Сопоставление Э3 ↔ ПЭ3", message, recommendation, details)


def _match_confidence(group, row, reason, evidence, partial, truncated):
    """Compatibility wrapper around the central evidence model.

    Confidence is independent of severity.  The full decision, including the
    limiting evidence factor, is produced by
    :func:`kd_pe_105_confidence.assess_finding`.
    """
    return assess_finding(
        group=group, row=row, reason=reason, evidence=evidence,
        partial=partial, truncated=truncated,
    ).confidence


def compare_inventories(e3_document, pe3_document, mentions, rows, *,
                        e3_word_count, e3_pages_processed, e3_pages_with_text,
                        e3_truncated=False, pe3_truncated=False,
                        pe3_text_designations=()):
    """Compare E3 components with expanded PE3 positions; return KD-PE-105 findings.

    ``pe3_text_designations`` is presence evidence from the PE3 text layer.  A
    designation visible there but absent from parsed rows is *not* reported as
    missing: the row extraction, not the engineering document, is uncertain.
    """
    e3_pages = list(range(1, e3_pages_processed + 1))
    text_evidence = set(pe3_text_designations or ())
    if e3_word_count == 0:
        return [_not_evaluated(e3_document, pe3_document, e3_pages, "no_text_layer",
                               truncated=e3_truncated or pe3_truncated)]

    pe3_entries = set()
    rows_by_family = defaultdict(list)
    for row in rows:
        entries = row.get("entries") or set()
        pe3_entries.update(entries)
        for family in _row_families(row):
            rows_by_family[family].append(row)
    if not pe3_entries:
        return [_not_evaluated(e3_document, pe3_document, e3_pages, "pe3_not_parsed",
                               truncated=e3_truncated or pe3_truncated)]

    components, components_truncated = build_components(mentions)
    if not components:
        return []

    partial = (e3_truncated or pe3_truncated or components_truncated
               or (0 < e3_pages_with_text < e3_pages_processed))
    truncated = e3_truncated or pe3_truncated or components_truncated

    missing, ambiguous, reference_only, matched = [], [], [], []
    for designation in sorted(components):
        component = components[designation]
        if designation in pe3_entries or base_of(designation) in pe3_entries:
            matched.append(component)
        elif component.category in (TERMINAL_OR_PIN, CONNECTION_REFERENCE, CROSS_REFERENCE):
            # A pin, connection address or cross-reference is never a
            # standalone product on its own.  It may be presence evidence, but
            # it must not create a "missing product" finding.
            reference_only.append(component)
        elif component.kinds and component.kinds <= {"reference", "note", "table", "unknown"}:
            # The designation was seen only in a reference-like or note role.
            # Its property is preserved, but it is not proof of a missing
            # standalone product.  A bare label on the same line is a separate
            # mention kind and therefore still makes the component eligible.
            ambiguous.append(component)
        elif component.confidence == "low":
            # Insufficient extraction evidence to attribute a candidate row.
            # A bare X1 label stays a candidate for an informational row match
            # (family_allowed is False), never for a categorical conclusion.
            ambiguous.append(component)
        else:
            missing.append(component)

    # Presence in the PE3 text layer is evidence that the row extraction, not
    # the document, is uncertain.  Never turn this into a "missing product".
    text_present = [component for component in missing
                    if component.designation in text_evidence]
    if text_present:
        text_present_designations = {component.designation for component in text_present}
        missing = [component for component in missing
                   if component.designation not in text_present_designations]

    relevant_ambiguous = [
        component for component in ambiguous
        if family_of(component.designation) not in TERMINAL_FAMILIES
        and family_of(component.designation) in rows_by_family
    ]
    relevant_reference = [
        component for component in reference_only
        if family_of(component.designation) not in TERMINAL_FAMILIES
        and family_of(component.designation) in rows_by_family
    ]
    context_ambiguous = relevant_reference + relevant_ambiguous + text_present
    if not missing:
        if text_present:
            return [_not_evaluated(
                e3_document, pe3_document, e3_pages, "present_in_unparsed_pe3_text",
                ambiguous=context_ambiguous, partial=partial, truncated=truncated,
                ambiguous_reason="present_in_unparsed_pe3_text",
            )]
        if context_ambiguous:
            return [_not_evaluated(e3_document, pe3_document, e3_pages, "reference_only",
                                   ambiguous=context_ambiguous, partial=partial, truncated=truncated)]
        return []

    groups = defaultdict(list)
    group_meta = {}
    for component in missing:
        row, reason, evidence, candidates = _attribute_component(component, rows_by_family)
        family = family_of(component.designation)
        if row is not None:
            key = (_row_key(row), family, reason)
        else:
            key = (("unmatched", candidates), family, reason, evidence)
        groups[key].append(component)
        group_meta[key] = (row, reason, evidence, candidates)

    findings = []
    for key in sorted(groups, key=lambda value: str(value)):
        group = sorted(groups[key], key=lambda item: item.designation)
        row, reason, evidence, candidates = group_meta[key]
        family = family_of(group[0].designation)
        # One central model decides confidence and severity.  It uses only
        # discrete evidence levels and the minimum rule; no numeric weights.
        decision = assess_finding(
            group=group,
            row=row,
            reason=reason,
            evidence=evidence,
            partial=partial,
            truncated=truncated,
            candidates=len(set(candidates)),
            family=family,
            family_allowed=family not in TERMINAL_FAMILIES,
        )
        confidence = decision.confidence
        severity = decision.severity

        base = _details_base(e3_document, pe3_document, "evaluated", reason,
                             sorted({page for component in group for page in component.pages}))
        base["partial"] = bool(partial)
        base["truncated"] = bool(truncated)
        base["type_family"] = family
        base["type_name"] = TYPE_NAMES.get(family, f"тип обозначения {family}" if family else "")
        if context_ambiguous:
            base["ambiguous"] = [
                _ambiguous_item(
                    component,
                    "present_in_unparsed_pe3_text"
                    if component.designation in text_evidence else None,
                )
                for component in context_ambiguous[:MAX_AMBIGUOUS]
            ]
        details = _details_for_group(base, row, group, reason, confidence)

        designations = [component.designation for component in group]
        listed = _format_designations(designations, MAX_MESSAGE_LISTED)
        message_lines = [
            "На схеме обнаружены обозначения, отсутствующие в перечне элементов.",
            f"Обнаружено обозначений: {len(designations)}.",
            f"Первые обозначения: {listed}.",
            f"Документ Э3: {_clean(e3_document, MAX_DOCUMENT)}; листы PDF: {_format_pages(base['e3_pages'])}.",
            f"Причина: {REASON_LABELS.get(reason, reason)}.",
        ]
        if row is not None:
            place = f"лист PDF {row.get('page')}"
            if row.get("row_band") is not None and row.get("table_row") is not None:
                place += f", строка таблицы {row.get('table_row')}"
            message_lines.append(f"Документ ПЭ3: {_clean(pe3_document, MAX_DOCUMENT)}; {place}.")
            message_lines.append(
                f"Количество в ПЭ3: {row.get('quantity')}; раскрытых обозначений: {len(row.get('entries') or set())}."
            )
            if row.get("name"):
                message_lines.append(f"Наименование позиции ПЭ3: «{_clean(row.get('name'), MAX_NAME)}».")
        else:
            message_lines.append(f"Документ ПЭ3: {_clean(pe3_document, MAX_DOCUMENT)}; строка не сопоставлена.")
        if reason == "row_matched_without_coverage":
            message_lines.append(
                "Строка ПЭ3 не доказывает принадлежность: совпали только тип и/или группа; "
                "требуется ручная проверка."
            )
        elif reason == "row_ambiguous":
            count = len(set(candidates))
            candidate_text = _format_candidate_rows(candidates)
            if candidate_text:
                message_lines.append(f"Кандидаты ПЭ3: {candidate_text}.")
            message_lines.append(
                f"Найдено несколько подходящих строк ПЭ3 (кандидатов: {count}); "
                "автоматический выбор строки не выполнен."
            )
        message_lines.append(f"Тип обозначения: {family}" + (f" ({TYPE_NAMES[family]})" if family in TYPE_NAMES else "") + ".")
        message_lines.append(f"Уровень достоверности: {_confidence_label(details['confidence'])}.")
        message_lines.append(f"Основание достоверности: {decision.explanation}.")
        message_lines.append("Статус проверки: evaluated.")
        message_lines.append("Полный перечень и доказательства — в структурированных деталях замечания.")
        if truncated or partial:
            message_lines.append("Ограничение: проверка неполная; часть страниц или строк не обработана.")
        message = "\n".join(message_lines)

        short_list = _format_designations(designations, MAX_RECOMMENDATION_LISTED)
        suggestion = _suggest_range(designations, row) if reason == "quantity_mismatch" else ""
        recommendation = (
            f"Проверьте полноту перечня позиционных обозначений. На схеме Э3 присутствуют {short_list}, "
            "но они не обнаружены среди обозначений ПЭ3."
        )
        if suggestion:
            recommendation += f" Возможное исправление: дополнить диапазон {suggestion}."
        if reason == "row_matched_without_coverage":
            recommendation += (
                " Строка ПЭ3 подходит только предположительно по типу/группе; "
                "проверьте её вручную и не изменяйте документы автоматически."
            )
        elif reason == "row_ambiguous":
            recommendation += (
                " Найдено несколько похожих строк ПЭ3; сопоставьте обозначения с конкретными строками вручную."
            )
        recommendation += (
            " Требуется подтверждение инженером; автоматическое изменение документов не выполняется. "
            "Это результат текстового сопоставления, а не доказательство физического отсутствия элемента."
        )
        if severity == "info":
            recommendation = (
                "Для проверки инженером: " + recommendation
                + " Уровень достоверности недостаточен для категоричного вывода."
            )

        place_parts = [f"Э3: листы PDF {_format_pages(base['e3_pages'])}"]
        if row is not None:
            pe3_place = f"ПЭ3: лист PDF {row.get('page')}"
            if row.get("row_band") is not None and row.get("table_row") is not None:
                pe3_place += f", строка таблицы {row.get('table_row')}"
            place_parts.append(pe3_place)
        elif reason == "row_ambiguous":
            candidate_text = _format_candidate_rows(candidates)
            if candidate_text:
                place_parts.append(
                    f"ПЭ3: строка не сопоставлена; кандидаты: {candidate_text}"
                )
            else:
                place_parts.append(
                    f"ПЭ3: строка не сопоставлена (кандидатов: {len(set(candidates))})"
                )
        else:
            place_parts.append("ПЭ3: строка не сопоставлена")
        findings.append(_finding(severity, pe3_document, "; ".join(place_parts), message, recommendation, details))
    return findings


def _valid_string(value, limit, *, allow_empty=True):
    return (isinstance(value, str) and len(value) <= limit
            and (allow_empty or bool(value)))


def _member(value, allowed):
    return isinstance(value, str) and value in allowed


def _valid_pages(value, limit):
    return (isinstance(value, list) and len(value) <= limit
            and all(type(page) is int and 1 <= page <= 1000 for page in value)
            and value == sorted(set(value)))


def _valid_evidence(value, e3_pages):
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_EVIDENCE:
        return False
    for item in value:
        if not isinstance(item, dict):
            return False
        keys = set(item)
        if keys not in ({"page", "kind", "context"},
                        {"page", "kind", "context", "bbox_pt", "page_size_pt"}):
            return False
        if (type(item.get("page")) is not int or item["page"] < 1
                or item["page"] not in e3_pages
                or not _member(item.get("kind"), KINDS)
                or not _valid_string(item.get("context"), MAX_CONTEXT)):
            return False
        if "bbox_pt" in item:
            if not _bbox(item.get("bbox_pt"), item.get("page_size_pt")):
                return False
    return True


def _valid_missing_item(item, e3_pages):
    if not isinstance(item, dict) or set(item) != {"designation", "raw", "pages", "kinds", "evidence"}:
        return False
    if (not _valid_string(item.get("designation"), 100, allow_empty=False)
            or not _valid_string(item.get("raw"), 100, allow_empty=False)
            or not _valid_pages(item.get("pages"), MAX_EVIDENCE_PAGES)
            or not isinstance(item.get("kinds"), list) or not item["kinds"]
            or len(item["kinds"]) > len(KINDS)
            or any(not _member(kind, KINDS) for kind in item["kinds"])
            or item["kinds"] != sorted(set(item["kinds"]))):
        return False
    return _valid_evidence(item.get("evidence"), e3_pages)


def _valid_ambiguous_item(item):
    if not isinstance(item, dict) or set(item) != {"designation", "raw", "pages", "kinds", "reason"}:
        return False
    return (
        _valid_string(item.get("designation"), 100, allow_empty=False)
        and _valid_string(item.get("raw"), 100, allow_empty=False)
        and _valid_pages(item.get("pages"), MAX_EVIDENCE_PAGES)
        and isinstance(item.get("kinds"), list) and item["kinds"]
        and all(_member(kind, KINDS) for kind in item["kinds"])
        and item["kinds"] == sorted(set(item["kinds"]))
        and _member(item.get("reason"), REASONS)
    )


def valid_details(details, known_files, issue):
    """Strict parser-worker validation for the KD-PE-105 details schema."""
    if not isinstance(details, dict) or not isinstance(issue, dict):
        return False
    required = {
        "schema_version", "kind", "status", "reason", "e3_document", "pe3_document",
        "e3_pages", "pe3_page", "pe3_table_row", "pe3_row_band_pt", "pe3_page_size_pt",
        "pe3_quantity", "pe3_entries_count", "pe3_name", "pe3_name_confidence",
        "pe3_source", "type_family", "type_name", "confidence", "partial", "truncated",
        "missing", "ambiguous", "limits",
    }
    if set(details) != required:
        return False
    if details.get("schema_version") != DETAILS_SCHEMA_VERSION or details.get("kind") != DETAILS_KIND:
        return False
    if not _member(details.get("status"), STATUSES) or not _member(details.get("reason"), REASONS):
        return False
    e3_document, pe3_document = details.get("e3_document"), details.get("pe3_document")
    if (not _valid_string(e3_document, MAX_DOCUMENT, allow_empty=False)
            or not _valid_string(pe3_document, MAX_DOCUMENT, allow_empty=False)
            or e3_document not in known_files or pe3_document not in known_files
            or designation_type(e3_document) != "Э3" or designation_type(pe3_document) != "ПЭ3"
            or pair_key(e3_document) != pair_key(pe3_document)
            or issue.get("file") != pe3_document):
        return False
    if not _valid_pages(details.get("e3_pages"), MAX_DETAIL_PAGES):
        return False
    if details["e3_pages"] == [] and details["status"] == "evaluated":
        return False
    pe3_page, pe3_table_row = details.get("pe3_page"), details.get("pe3_table_row")
    if pe3_page is not None and (type(pe3_page) is not int or pe3_page < 1):
        return False
    if pe3_table_row is not None and (type(pe3_table_row) is not int or pe3_table_row < 1):
        return False
    row_band, page_size = details.get("pe3_row_band_pt"), details.get("pe3_page_size_pt")
    if row_band is not None:
        if (not isinstance(row_band, list) or len(row_band) != 2
                or not all(_number(value) for value in row_band)
                or not row_band[0] < row_band[1]
                or not _page_size(page_size) or row_band[1] > page_size[1]):
            return False
    elif page_size is not None and not _page_size(page_size):
        return False
    quantity, entries_count = details.get("pe3_quantity"), details.get("pe3_entries_count")
    if quantity is not None and (type(quantity) is not int or quantity < 0):
        return False
    if entries_count is not None and (type(entries_count) is not int or entries_count < 0):
        return False
    if (not _valid_string(details.get("pe3_name"), MAX_NAME)
            or not _member(details.get("pe3_name_confidence"), CONFIDENCE_LEVELS)
            or not _valid_string(details.get("pe3_source"), MAX_SOURCE)
            or not _valid_string(details.get("type_family"), 20)
            or not _valid_string(details.get("type_name"), MAX_TYPE_NAME)
            or not _member(details.get("confidence"), CONFIDENCE_LEVELS)
            or type(details.get("partial")) is not bool
            or type(details.get("truncated")) is not bool):
        return False
    missing, ambiguous = details.get("missing"), details.get("ambiguous")
    if not isinstance(missing, list) or len(missing) > MAX_LISTED:
        return False
    if not isinstance(ambiguous, list) or len(ambiguous) > MAX_AMBIGUOUS:
        return False
    if not all(_valid_missing_item(item, details["e3_pages"]) for item in missing):
        return False
    if not all(_valid_ambiguous_item(item) for item in ambiguous):
        return False
    designations = [item["designation"] for item in missing]
    if designations != sorted(set(designations)):
        return False
    ambiguous_designations = [item["designation"] for item in ambiguous]
    if ambiguous_designations != sorted(set(ambiguous_designations)):
        return False
    limits = details.get("limits")
    if (not isinstance(limits, dict)
            or set(limits) != {"max_pages", "max_listed", "truncated_list"}
            or type(limits.get("max_pages")) is not int or limits["max_pages"] != MAX_PDF_PAGES
            or type(limits.get("max_listed")) is not int or limits["max_listed"] != MAX_LISTED
            or type(limits.get("truncated_list")) is not bool):
        return False
    severity = issue.get("severity")
    if severity not in ("info", "warning"):
        return False
    reason = details["reason"]
    if details["status"] == "not_evaluated" and (missing or severity != "info"):
        return False
    if details["status"] == "evaluated" and not missing and not ambiguous:
        return False
    # Central decision table: reject contradictory reason/confidence/severity
    # instead of silently repairing persisted reports.
    if not validate_combination(
        status=details["status"],
        reason=reason,
        confidence=details["confidence"],
        severity=severity,
        partial=details["partial"],
        truncated=details["truncated"],
        pe3_page=details["pe3_page"],
        pe3_table_row=details["pe3_table_row"],
    ):
        return False
    try:
        size = len(json.dumps(details, ensure_ascii=False, allow_nan=False,
                              sort_keys=True, separators=(",", ":")).encode("utf-8"))
    except (TypeError, ValueError):
        return False
    return size <= MAX_DETAILS_BYTES
