"""Central evidence model for KD-PE-105 confidence and severity (block 4).

The model deliberately uses **no numeric weights**.  Every factor is a
discrete evidence level (``low`` < ``medium`` < ``high``) and the final
confidence is the minimum over the mandatory factors.  This makes every
``high`` explainable: it is possible only when *all* mandatory evidence is
``high`` and the decision table allows it.

The model separates:

* quality of E3 extraction;
* confidence of the contextual identifier classification;
* quality of PE3 row extraction;
* precision of the row attribution;
* completeness of the analysis (``partial`` / ``truncated``);
* ambiguity of candidate rows;
* the decision-table constraint for the selected ``reason``.

It is a pure module: no PDF, no network, no persistence.
"""
from __future__ import annotations

from dataclasses import dataclass

from identifier_classification import AMBIGUOUS, classify_component


LOW = "low"
MEDIUM = "medium"
HIGH = "high"
LEVELS = (LOW, MEDIUM, HIGH)
_RANK = {level: index for index, level in enumerate(LEVELS)}

MATCH_EXACT = "exact"
MATCH_UNDER_EXPANDED = "under_expanded"
MATCH_FAMILY_GROUP = "family_group"
MATCH_FAMILY_ONLY = "family_only"
MATCH_NONE = "none"

EVALUATED = "evaluated"
NOT_EVALUATED = "not_evaluated"

DECISION_CONFIRMED = "confirmed_quantity_mismatch"
DECISION_TENTATIVE = "tentative_row_match"
DECISION_AMBIGUOUS = "ambiguous_candidates"
DECISION_NO_ROW = "no_matching_row"
DECISION_INCOMPLETE = "incomplete_analysis"
DECISION_REFERENCE_ONLY = "reference_only"
DECISION_NOT_EVALUATED = "not_evaluated"

#: Reasons that must always be reported as ``not_evaluated``.
REASONS_REQUIRING_NOT_EVALUATED = frozenset({
    "reference_only", "no_text_layer", "pe3_not_parsed", "truncated",
    "present_in_unparsed_pe3_text",
})

#: Human-readable names of the evidence factors.  Used in messages only.
COMPONENT_LABELS = {
    "e3_extraction": "качество извлечения Э3",
    "identifier_classification": "уверенность классификации обозначения",
    "pe3_extraction": "качество извлечения строки ПЭ3",
    "row_match": "точность соответствия строки",
    "extraction_completeness": "полнота извлечения",
    "candidate_ambiguity": "неоднозначность кандидатов",
    "reason_constraint": "ограничение причины",
}


def _level(value):
    return value if value in _RANK else LOW


def _weakest(levels):
    return min(levels, key=lambda value: _RANK[value])


@dataclass(frozen=True)
class Evidence:
    """One evidence factor with an explicit, non-numeric level."""

    name: str
    level: str
    mandatory: bool = True
    basis: str = ""

    def __post_init__(self):
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("Evidence.name must be a non-empty string")
        if self.level not in LEVELS:
            raise ValueError(f"Unsupported evidence level: {self.level!r}")
        if type(self.mandatory) is not bool:
            raise ValueError("Evidence.mandatory must be bool")


@dataclass(frozen=True)
class ConfidenceDecision:
    """Final confidence/severity decision with its full evidence trail."""

    confidence: str
    severity: str
    status: str
    decision: str
    reason: str
    limiting: str
    components: tuple[Evidence, ...]
    explanation: str


def aggregate_confidence(components):
    """Return ``(level, limiting_name)`` using the minimum rule.

    Only mandatory factors constrain the result.  Non-mandatory factors are
    kept for diagnostics but never raise or lower the answer.  There is no
    averaging and no numeric weight, so two ``medium`` factors can never
    produce ``high``.
    """
    mandatory = [item for item in components if item.mandatory]
    if not mandatory:
        return LOW, "no_mandatory_evidence"
    weakest = min(mandatory, key=lambda item: _RANK[item.level])
    return weakest.level, weakest.name


def _e3_extraction_level(group):
    levels = [_level(getattr(component, "confidence", LOW)) for component in group]
    return _weakest(levels) if levels else LOW


def _classification_level(group):
    """Aggregate the contextual classification confidence.

    An ``ambiguous`` identifier cannot support ``high`` confidence, but it is
    not treated as an absence of evidence: block 3 defines a medium ceiling for
    it.  This keeps an ambiguous ``X1`` informational while still preventing a
    categorical conclusion.
    """
    levels = []
    for component in group:
        if getattr(component, "category", "") == AMBIGUOUS:
            levels.append(MEDIUM)
            continue
        stored = getattr(component, "classification_confidence", None)
        if stored in _RANK:
            levels.append(stored)
            continue
        item = classify_component(component)
        levels.append(_level(item.confidence))
    return _weakest(levels) if levels else LOW


def _pe3_extraction_level(row):
    """Return ``(level, mandatory, basis)`` for the selected PE3 row."""
    if row is None:
        return MEDIUM, False, "строка ПЭ3 не выбрана"
    entries = row.get("entries") or set()
    quantity = row.get("quantity")
    if row.get("unknown"):
        return LOW, True, "в строке ПЭ3 есть нераскрытые фрагменты"
    if not entries or not isinstance(quantity, int) or quantity < 0:
        return LOW, True, "обозначения или количество строки ПЭ3 не извлечены"
    if row.get("row_band") is None:
        return MEDIUM, True, "строка ПЭ3 не ограничена сеткой PDF"
    if len(entries) <= 1 and quantity > 1:
        return LOW, True, "одно раскрытое обозначение при количестве больше 1"
    return HIGH, True, "строка, обозначения и количество извлечены"


def _row_match_level(reason, evidence):
    if reason == "quantity_mismatch" and evidence == MATCH_UNDER_EXPANDED:
        return HIGH
    if reason == "row_matched_without_coverage" and evidence == MATCH_FAMILY_GROUP:
        return MEDIUM
    if reason == "no_matching_pe3_row":
        return MEDIUM
    if reason == "row_ambiguous":
        return LOW
    if evidence == MATCH_EXACT:
        return HIGH
    return LOW


def _candidate_ambiguity_level(reason, candidates):
    if reason == "row_ambiguous":
        return LOW
    if reason == "quantity_mismatch":
        return HIGH if candidates <= 1 else LOW
    if reason == "row_matched_without_coverage":
        return MEDIUM if candidates <= 1 else LOW
    if reason == "no_matching_pe3_row":
        # The number of identical designations is not evidence of row
        # ownership.  It must never raise confidence.
        return MEDIUM
    return MEDIUM


def _reason_level(reason):
    """Decision-table ceiling for the selected reason."""
    if reason == "quantity_mismatch":
        return HIGH
    if reason in ("row_matched_without_coverage", "no_matching_pe3_row"):
        return MEDIUM
    if reason == "row_ambiguous":
        return LOW
    if reason in REASONS_REQUIRING_NOT_EVALUATED:
        return LOW
    return MEDIUM


def _quantity_surplus(row):
    if row is None:
        return False
    entries = row.get("entries") or set()
    quantity = row.get("quantity")
    return isinstance(quantity, int) and quantity > len(entries)


def _has_ambiguous_category(group):
    return any(getattr(component, "category", "") == AMBIGUOUS for component in group)


def _decision_code(reason, partial, truncated):
    if partial or truncated:
        return DECISION_INCOMPLETE
    if reason == "quantity_mismatch":
        return DECISION_CONFIRMED
    if reason == "row_matched_without_coverage":
        return DECISION_TENTATIVE
    if reason == "row_ambiguous":
        return DECISION_AMBIGUOUS
    if reason == "no_matching_pe3_row":
        return DECISION_NO_ROW
    if reason == "reference_only":
        return DECISION_REFERENCE_ONLY
    return DECISION_NOT_EVALUATED


def assess_finding(*, group, row, reason, evidence, partial, truncated,
                   candidates=0, family="", family_allowed=True):
    """Build the single confidence/severity decision for a KD-PE-105 group.

    ``group`` is an iterable of ``SchemeComponent``-like objects with
    ``confidence``, ``category`` and ``mentions``.  ``evidence`` is one of the
    ``MATCH_*`` classes.  ``candidates`` is the number of equally suitable PE3
    rows (0 when no row exists, 1 for a unique row, >1 for ambiguity).
    """
    e3_level = _e3_extraction_level(group)
    classification_level = _classification_level(group)
    pe3_level, pe3_mandatory, pe3_basis = _pe3_extraction_level(row)
    match_level = _row_match_level(reason, evidence)
    completeness_level = LOW if (partial or truncated) else HIGH
    ambiguity_level = _candidate_ambiguity_level(reason, candidates)
    reason_level = _reason_level(reason)

    components = (
        Evidence("e3_extraction", e3_level, True,
                 "надёжность извлечения обозначения из текста Э3"),
        Evidence("identifier_classification", classification_level, True,
                 "уверенность, что обозначение является самостоятельным изделием"),
        Evidence("pe3_extraction", pe3_level, pe3_mandatory, pe3_basis),
        Evidence("row_match", match_level, True,
                 "точность привязки обозначения к строке ПЭ3"),
        Evidence("extraction_completeness", completeness_level, True,
                 "полный или частичный анализ документов"),
        Evidence("candidate_ambiguity", ambiguity_level, True,
                 "единственность и неоднозначность строк-кандидатов"),
        Evidence("reason_constraint", reason_level, True,
                 f"таблица решений для reason={reason}"),
    )
    confidence, limiting = aggregate_confidence(components)

    warning = (
        reason == "quantity_mismatch"
        and evidence == MATCH_UNDER_EXPANDED
        and row is not None
        and _quantity_surplus(row)
        and not partial
        and not truncated
        and family_allowed
        and pe3_level in (HIGH, MEDIUM)
        and e3_level in (HIGH, MEDIUM)
        and confidence in (HIGH, MEDIUM)
        and not _has_ambiguous_category(group)
    )
    severity = "warning" if warning else "info"
    status = EVALUATED
    decision = _decision_code(reason, partial, truncated)
    explanation = (
        f"{COMPONENT_LABELS.get(limiting, limiting)} "
        f"({confidence}); причина — {reason}"
    )
    return ConfidenceDecision(
        confidence=confidence,
        severity=severity,
        status=status,
        decision=decision,
        reason=reason,
        limiting=limiting,
        components=components,
        explanation=explanation,
    )


def validate_combination(*, status, reason, confidence, severity, partial,
                         truncated, pe3_page, pe3_table_row):
    """Validate the persisted reason/confidence/severity combination.

    This is the executable decision table used by ``valid_details``.  It
    rejects contradictory combinations instead of silently repairing them.
    """
    if status not in (EVALUATED, NOT_EVALUATED):
        return False
    if confidence not in LEVELS:
        return False
    if severity not in ("info", "warning"):
        return False
    if reason == "ambiguous_row":
        return False
    if severity == "warning":
        if status != EVALUATED or reason != "quantity_mismatch":
            return False
        if confidence not in (HIGH, MEDIUM):
            return False
        if partial or truncated:
            return False
    if confidence == HIGH:
        if status != EVALUATED or reason != "quantity_mismatch":
            return False
        if partial or truncated:
            return False
    if reason == "row_ambiguous" and confidence != LOW:
        return False
    if reason == "row_matched_without_coverage" and confidence == HIGH:
        return False
    if reason == "no_matching_pe3_row" and confidence == HIGH:
        return False
    if reason in ("row_ambiguous", "no_matching_pe3_row") and (
            pe3_page is not None or pe3_table_row is not None):
        return False
    if reason in REASONS_REQUIRING_NOT_EVALUATED:
        if status != NOT_EVALUATED or severity != "info" or confidence != LOW:
            return False
    if status == NOT_EVALUATED and severity != "info":
        return False
    if partial or truncated:
        if confidence != LOW or severity != "info":
            return False
    if reason == "quantity_mismatch" and pe3_page is None:
        return False
    return True
