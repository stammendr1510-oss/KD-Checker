"""Contextual classification of E3 identifiers for KD-PE-105 (block 3).

The module is deliberately isolated and pure: it receives already extracted
identifiers (text, line context, source structure) and never opens PDF files,
never uses the network and never writes data.

The classification answers a narrow question: *may this identifier be checked
as a standalone product in PE3?*  A device designation, a pin of a device, a
connection address and a cross-reference are different things.  Treating a pin
as a standalone product creates false "missing product" findings, so pins are
kept as presence evidence only.

Categories
----------
``device_candidate``
    A full designation that can be checked for presence in PE3
    (``1KM1``, ``3TA2``, ``XT1``).
``terminal_or_pin``
    A pin/terminal point of a device (``1KM1:A1``, ``1HLW:X1``, ``XT4.2``).
    It is evidence that the parent device may exist, but it is not a
    standalone product.
``connection_reference``
    An endpoint of a connection/address expression
    (``SF1:1 - XT1:2``).  It is presence evidence only.
``cross_reference``
    A reference to a device elsewhere (``см. QF1``, ``QF1 (лист 3)``).
    It is presence evidence only.
``ambiguous``
    Insufficient evidence to decide.  Unknown identifiers are preserved, not
    deleted, and are marked as requiring additional context.

Only ``device_candidate`` is eligible for the PE3 presence check.  The
remaining categories may be used as *presence evidence* but must never be
turned into a standalone "missing product" finding on their own.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import re

from kd_crosscheck import TAG, normalized


DEVICE_CANDIDATE = "device_candidate"
TERMINAL_OR_PIN = "terminal_or_pin"
CONNECTION_REFERENCE = "connection_reference"
CROSS_REFERENCE = "cross_reference"
AMBIGUOUS = "ambiguous"

CATEGORIES = frozenset({
    DEVICE_CANDIDATE,
    TERMINAL_OR_PIN,
    CONNECTION_REFERENCE,
    CROSS_REFERENCE,
    AMBIGUOUS,
})

#: Categories whose identifiers may be checked for presence in PE3.
PRESENCE_CHECK_CATEGORIES = frozenset({DEVICE_CANDIDATE})

#: Categories that can prove that a device exists, but never prove that the
#: identifier itself is a standalone product.
REFERENCE_CATEGORIES = frozenset({
    TERMINAL_OR_PIN,
    CONNECTION_REFERENCE,
    CROSS_REFERENCE,
})

#: Categories that must be kept for an engineer because the available evidence
#: is insufficient.
REQUIRES_CONTEXT_CATEGORIES = frozenset({AMBIGUOUS})

# The existing domain model (``TYPE_NAMES``) documents ``X`` as
# "terminal block / connection".  A bare single-letter token of that family is
# inherently ambiguous: it may be a terminal block device or a connection
# point.  Multi-letter terminal families (XT/XN/XL/XG) are terminal blocks and
# remain device candidates.  This is the only family-based rule; every other
# decision is structural (pin separator, dotted suffix, connection expression,
# cross-reference marker, line context).  No rule is tied to a concrete
# device code such as KM, QF or TA.
AMBIGUOUS_TERMINAL_FAMILIES = frozenset({"X"})

MAX_CONTEXT = 300

REASON_BARE_DESIGNATION = "bare_designation"
REASON_EXPLICIT_PIN_SEPARATOR = "explicit_pin_separator"
REASON_DOTTED_PIN_SUFFIX = "dotted_pin_suffix"
REASON_PIN_REFERENCE_IN_CONTEXT = "pin_reference_in_context"
REASON_CONNECTION_ENDPOINT = "connection_endpoint"
REASON_CROSS_REFERENCE_MARKER = "cross_reference_marker"
REASON_AMBIGUOUS_TERMINAL_FAMILY = "ambiguous_terminal_family"
REASON_NON_TAG_TOKEN = "non_tag_token"
REASON_NOTE_OR_TABLE_CONTEXT = "note_or_table_context"
REASON_UNKNOWN_CONTEXT = "unknown_context"

REASON_LABELS = {
    REASON_BARE_DESIGNATION: "самостоятельное обозначение без признаков вывода или ссылки",
    REASON_EXPLICIT_PIN_SEPARATOR: "токен содержит явный разделитель вывода «:»",
    REASON_DOTTED_PIN_SUFFIX: "токен содержит точечный суффикс вывода/контакта",
    REASON_PIN_REFERENCE_IN_CONTEXT: "в строке после обозначения указан вывод или контакт",
    REASON_CONNECTION_ENDPOINT: "обозначение является концом электрического соединения",
    REASON_CROSS_REFERENCE_MARKER: "обозначение встречено в перекрёстной ссылке или примечании",
    REASON_AMBIGUOUS_TERMINAL_FAMILY: "одиночное обозначение клеммного/соединительного семейства без контекста",
    REASON_NON_TAG_TOKEN: "токен не является полным позиционным обозначением",
    REASON_NOTE_OR_TABLE_CONTEXT: "токен находится в примечании или табличном фрагменте",
    REASON_UNKNOWN_CONTEXT: "недостаточно контекста для классификации",
}

# A token that explicitly contains a pin separator, e.g. ``1KM1:A1``.
PIN_RE = re.compile(r"^(.+?)\s*:\s*([A-Za-zА-Яа-я0-9][A-Za-zА-Яа-я0-9._/-]*)$")
# A token with a dotted contact/pin suffix, e.g. ``XT4.2`` or ``3TA2.1``.
DOTTED_RE = re.compile(r"^(.+)\.(\d+)$")
# Two pinned endpoints joined by a dash/arrow, e.g. ``SF1:1 - XT1:2``.
CONNECTION_RE = re.compile(
    r"[A-ZА-Я0-9.]+\s*:\s*[A-ZА-Я0-9.]+\s*[-–—→]\s*[A-ZА-Я0-9.]+\s*:",
    re.I,
)
# Explicit cross-reference / note markers.  These are structural cues, not
# assumptions about a concrete device family.
CROSS_REF_RE = re.compile(
    r"(?:\bсм\.?\b|\bсмотри\b|\bпоз\.?\b|\bпозици|\bприм(?:еч)?|\bтабл|"
    r"\bлист|\bдокумент|\bсхем|\bЭ3\b|\bПЭ3\b)",
    re.I,
)
DOC_REF_RE = re.compile(r"\b[А-ЯA-Z]{2,}\.\d{3,}\.\d{3,}\b", re.I)


@dataclass(frozen=True)
class ClassifiedIdentifier:
    """A single identifier with its category and classification evidence."""

    raw: str
    designation: str
    category: str
    parent_device: str | None
    pin: str | None
    reason: str
    confidence: str
    context: str
    eligible_for_presence_check: bool
    presence_evidence: bool
    requires_context: bool


def _clean(value, limit=MAX_CONTEXT):
    text = str(value if value is not None else "")
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def family_of(designation):
    """Return the letter family of a normalized designation."""
    match = re.match(r"^\d*([A-Z]+)", normalized(designation))
    return match.group(1) if match else ""


def _make(raw, designation, category, parent, pin, reason, confidence, context):
    return ClassifiedIdentifier(
        raw=_clean(raw, 100),
        designation=designation,
        category=category,
        parent_device=parent,
        pin=pin,
        reason=reason,
        confidence=confidence,
        context=_clean(context),
        eligible_for_presence_check=category in PRESENCE_CHECK_CATEGORIES,
        presence_evidence=category != AMBIGUOUS,
        requires_context=category in REQUIRES_CONTEXT_CATEGORIES,
    )


def _occurrence_end(text, token):
    if not text or not token:
        return None
    match = re.search(re.escape(token), text, re.I)
    return match.end() if match else None


def _pin_after(text, end):
    match = re.match(r"\s*:\s*([A-Za-zА-Яа-я0-9][A-Za-zА-Яа-я0-9._/-]*)", text[end:])
    return match.group(1) if match else None


def _split_explicit_pin(text):
    match = PIN_RE.fullmatch(text.strip())
    if not match:
        return None
    parent, pin = match.group(1).strip(), match.group(2).strip()
    parent_norm = normalized(parent)
    if TAG.fullmatch(parent_norm) and pin:
        return parent_norm, pin
    return None


def _split_dotted(value):
    match = DOTTED_RE.fullmatch(value)
    if not match:
        return None
    parent, pin = match.group(1), match.group(2)
    if TAG.fullmatch(parent):
        return parent, pin
    return None


def _is_connection(text):
    return bool(text and CONNECTION_RE.search(text))


def _is_cross_reference(text):
    return bool(text and (CROSS_REF_RE.search(text) or DOC_REF_RE.search(text)))


def classify_identifier(raw, *, kind=None, context="", line_text=None, structure=None):
    """Classify one identifier using its text and available line context.

    ``raw`` is the original token (for example ``1KM1`` or ``1KM1:A1``).
    ``kind`` is the existing mention kind (``label``, ``reference``,
    ``note``, ``table``, ``unknown``).  ``line_text`` should contain the full
    source line when available; it lets the classifier see a pin suffix even
    when the PDF word is split.  ``structure`` is reserved for future source
    structure hints and is intentionally not required.
    """
    text = str(raw or "").strip()
    designation = normalized(text)
    full_context = str(line_text if line_text is not None else context or "")

    if not designation:
        return _make(text, "", AMBIGUOUS, None, None,
                     REASON_UNKNOWN_CONTEXT, "low", full_context)

    explicit = _split_explicit_pin(text)
    if explicit:
        parent, pin = explicit
        if _is_connection(full_context):
            return _make(text, designation, CONNECTION_REFERENCE, parent, pin,
                         REASON_CONNECTION_ENDPOINT, "high", full_context)
        return _make(text, designation, TERMINAL_OR_PIN, parent, pin,
                     REASON_EXPLICIT_PIN_SEPARATOR, "high", full_context)

    dotted = _split_dotted(designation)
    if dotted:
        parent, pin = dotted
        if _is_connection(full_context):
            return _make(text, designation, CONNECTION_REFERENCE, parent, pin,
                         REASON_CONNECTION_ENDPOINT, "high", full_context)
        return _make(text, designation, TERMINAL_OR_PIN, parent, pin,
                     REASON_DOTTED_PIN_SUFFIX, "high", full_context)

    end = _occurrence_end(full_context, text)
    if end is not None:
        pin = _pin_after(full_context, end)
        if pin:
            if _is_connection(full_context):
                return _make(text, designation, CONNECTION_REFERENCE, designation, pin,
                             REASON_CONNECTION_ENDPOINT, "high", full_context)
            return _make(text, designation, TERMINAL_OR_PIN, designation, pin,
                         REASON_PIN_REFERENCE_IN_CONTEXT, "medium", full_context)

    if _is_connection(full_context):
        return _make(text, designation, CONNECTION_REFERENCE, designation, None,
                     REASON_CONNECTION_ENDPOINT, "medium", full_context)

    if _is_cross_reference(full_context):
        return _make(text, designation, CROSS_REFERENCE, None, None,
                     REASON_CROSS_REFERENCE_MARKER, "medium", full_context)

    if kind in ("note", "table", "unknown"):
        return _make(text, designation, AMBIGUOUS, None, None,
                     REASON_NOTE_OR_TABLE_CONTEXT, "low", full_context)

    if TAG.fullmatch(designation):
        if family_of(designation) in AMBIGUOUS_TERMINAL_FAMILIES:
            return _make(text, designation, AMBIGUOUS, None, None,
                         REASON_AMBIGUOUS_TERMINAL_FAMILY, "low", full_context)
        confidence = "high" if kind in (None, "label", "split_label") else "medium"
        return _make(text, designation, DEVICE_CANDIDATE, None, None,
                     REASON_BARE_DESIGNATION, confidence, full_context)

    return _make(text, designation, AMBIGUOUS, None, None,
                 REASON_NON_TAG_TOKEN, "low", full_context)


def classify_mention(mention):
    """Classify a mention-like object (``raw``, ``kind``, ``context``, ...)."""
    line = getattr(mention, "line_text", "") or getattr(mention, "context", "") or ""
    return classify_identifier(
        getattr(mention, "raw", "") or getattr(mention, "designation", ""),
        kind=getattr(mention, "kind", None),
        context=getattr(mention, "context", "") or "",
        line_text=line,
    )


def classify_component(component):
    """Aggregate mention classifications for one scheme component.

    A standalone device candidate dominates: if the same designation is used
    once as a bare label and once as a pin, it remains a device candidate.
    Otherwise the most specific reference category is kept.
    """
    items = [classify_mention(mention) for mention in component.mentions]
    if not items:
        return _make(component.designation, component.designation, AMBIGUOUS,
                     None, None, REASON_UNKNOWN_CONTEXT, "low", "")
    priority = (DEVICE_CANDIDATE, CONNECTION_REFERENCE, TERMINAL_OR_PIN, CROSS_REFERENCE)
    chosen = None
    for category in priority:
        for item in items:
            if item.category == category:
                chosen = item
                break
        if chosen is not None:
            break
    if chosen is None:
        chosen = items[0]
    return replace(chosen, designation=component.designation)


def classification_report(mentions):
    """Report which identifiers are eligible for PE3 presence checks.

    Returns a mapping with three explicit lists:

    * ``eligible_for_presence_check`` - standalone device candidates;
    * ``presence_evidence_only`` - pins, connection addresses and
      cross-references (evidence that a device may exist, never a standalone
      product on their own);
    * ``requires_context`` - ambiguous identifiers preserved for an engineer.
    """
    return summarize_classification([classify_mention(mention) for mention in mentions])


def summarize_classification(classified):
    by_category = {category: set() for category in CATEGORIES}
    for item in classified:
        by_category[item.category].add(item.designation)
    ordered = {category: sorted(values) for category, values in by_category.items()}
    # A designation that appears once as a standalone device and once as a pin
    # is still eligible for the presence check; do not duplicate it in the
    # evidence-only list.  The same priority keeps the report unambiguous.
    eligible = set(ordered[DEVICE_CANDIDATE])
    evidence_only = set(
        ordered[TERMINAL_OR_PIN]
        + ordered[CONNECTION_REFERENCE]
        + ordered[CROSS_REFERENCE]
    ) - eligible
    requires_context = set(ordered[AMBIGUOUS]) - eligible - evidence_only
    return {
        "eligible_for_presence_check": sorted(eligible),
        "presence_evidence_only": sorted(evidence_only),
        "requires_context": sorted(requires_context),
        "by_category": ordered,
    }
