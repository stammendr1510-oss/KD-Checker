"""Block 4: central confidence/severity model for KD-PE-105.

The tests cover the required positive and negative control cases, the
executable decision table, the minimum-of-mandatory-evidence rule and the
backward-compatible schema v1 / Excel / history behaviour.

Every scenario is synthetic: no enterprise PDF, no report file, no network.
"""
from copy import deepcopy
from io import BytesIO

import pytest
from openpyxl import load_workbook

from e3_pe3_inventory import (
    Mention,
    build_components,
    compare_inventories,
    extract_mentions,
    valid_details,
)
from excel_reports import build_excel_report
from issue_tracking import finding_identity
from kd_pe_105_confidence import (
    HIGH,
    LOW,
    MEDIUM,
    MATCH_FAMILY_GROUP,
    MATCH_NONE,
    MATCH_UNDER_EXPANDED,
    Evidence,
    aggregate_confidence,
    assess_finding,
    validate_combination,
)


E3 = "ABCD.123456.001.Э3.pdf"
PE3 = "ABCD.123456.001.ПЭ3.pdf"
FILES = {E3, PE3}
TA_ENTRIES = {
    f"{group}TA{number}" for group in (1, 2, 3) for number in (1, 2, 3)
    if not (group == 3 and number in (2, 3))
}


def label(designation, *, page=1, kind="label", confidence="high"):
    return Mention(
        designation=designation,
        raw=designation,
        page=page,
        kind=kind,
        context=designation,
        confidence=confidence,
        bbox=None,
        page_size=None,
    )


def pe3_row(entries, quantity=None, *, page=2, table_row=2, band=(70.0, 130.0),
            duplicate=False, unknown=None, name="", source=None):
    entries = set(entries)
    if quantity is None:
        quantity = len(entries)
    return {
        "page": page,
        "source": source if source is not None else ";".join(sorted(entries)),
        "quantity": quantity,
        "entries": entries,
        "unknown": list(unknown or []),
        "row_band": band,
        "table_row": table_row if band is not None else None,
        "name": name,
        "name_confidence": "medium" if name else "low",
        "page_size_pt": [600.0, 800.0],
        "pdf_truncated": False,
        "duplicate_eligible": bool(duplicate),
    }


def compare(mentions, rows, **kwargs):
    pages = max((mention.page for mention in mentions), default=1)
    return compare_inventories(
        E3, PE3, mentions, rows,
        e3_word_count=len(mentions),
        e3_pages_processed=pages,
        e3_pages_with_text=pages,
        **kwargs,
    )


def only(findings):
    assert len(findings) == 1, [finding["details"] for finding in findings]
    return findings[0]


# ---------------------------------------------------------------------------
# Required control cases
# ---------------------------------------------------------------------------

def test_confirmed_ta_defect_can_be_high():
    """A unique row with an explicit quantity surplus is a confirmed defect."""
    mentions = [label("3TA2"), label("3TA3")]
    issue = only(compare(mentions, [pe3_row(TA_ENTRIES, quantity=9)]))

    assert issue["severity"] == "warning"
    assert issue["details"]["reason"] == "quantity_mismatch"
    assert issue["details"]["confidence"] == "high"
    assert valid_details(issue["details"], FILES, issue) is True


def test_ambiguous_km_row_never_gets_high_without_independent_proof():
    """A one-to-one row cannot prove ownership of a different KM designation."""
    row = pe3_row(["3KM1"], quantity=1, duplicate=True, table_row=7)
    issue = only(compare([label("3KM2")], [row]))
    details = issue["details"]

    assert issue["severity"] == "info"
    assert details["reason"] == "row_matched_without_coverage"
    assert details["confidence"] == MEDIUM

    promoted = deepcopy(details)
    promoted["confidence"] = HIGH
    assert valid_details(promoted, FILES, issue) is False


def test_contact_without_confirmed_device_is_not_a_standalone_error():
    """A pin is presence evidence only; it must not become a missing product."""
    words = [(60, 90, 120, 96, "1KM1:A1", 0, 0, 0)]
    mentions = extract_mentions(words, 1, page_size=(600, 800))
    issue = only(compare(mentions, [pe3_row(["3KM1"], quantity=1)]))
    details = issue["details"]

    assert issue["severity"] == "info"
    assert details["status"] == "not_evaluated"
    assert details["reason"] == "reference_only"
    assert details["missing"] == []
    assert [item["designation"] for item in details["ambiguous"]] == ["1KM1"]


def test_unrecognized_pe3_row_is_not_reported_as_a_match():
    """An unexpanded PE3 row cannot confirm coverage of a designation."""
    row = pe3_row(["3TA1"], quantity=9, unknown=["3TA2...3TA9"])
    issue = only(compare([label("3TA3")], [row]))
    details = issue["details"]

    assert details["reason"] == "no_matching_pe3_row"
    assert details["confidence"] == MEDIUM
    assert details["pe3_page"] is None
    assert details["pe3_table_row"] is None
    assert [item["designation"] for item in details["missing"]] == ["3TA3"]


def test_unparsed_pe3_is_not_evaluated_instead_of_silently_empty():
    row = pe3_row([], quantity=1)
    issue = only(compare([label("3TA2")], [row]))
    details = issue["details"]

    assert issue["severity"] == "info"
    assert details["status"] == "not_evaluated"
    assert details["reason"] == "pe3_not_parsed"
    assert details["missing"] == []


def test_partially_processed_document_does_not_create_a_categorical_conclusion():
    mentions = [label("3TA2")]
    findings = compare(mentions, [pe3_row(TA_ENTRIES, quantity=9)], e3_truncated=True)
    issue = only(findings)
    details = issue["details"]

    assert issue["severity"] == "info"
    assert details["partial"] is True
    assert details["truncated"] is True
    assert details["confidence"] == LOW
    assert details["reason"] == "quantity_mismatch"

    promoted = deepcopy(details)
    promoted["confidence"] = HIGH
    assert valid_details(promoted, FILES, issue) is False


def test_many_identical_designations_do_not_raise_no_matching_confidence():
    """The count of missing designations is not evidence of row ownership."""
    mentions = [label(f"9HLG{number}") for number in range(1, 101)]
    issue = only(compare(mentions, [pe3_row(["1KM1"], quantity=1)]))
    details = issue["details"]

    assert details["reason"] == "no_matching_pe3_row"
    assert details["confidence"] == MEDIUM
    assert len(details["missing"]) == 100
    assert valid_details(details, FILES, issue) is True


# ---------------------------------------------------------------------------
# Minimum of mandatory evidence
# ---------------------------------------------------------------------------

def test_weakest_mandatory_evidence_caps_confidence():
    components, _ = build_components([
        Mention("3TA2", "3TA2", 1, "split_label", "3TA2", "medium"),
    ])
    decision = assess_finding(
        group=[components["3TA2"]],
        row=pe3_row(TA_ENTRIES, quantity=9),
        reason="quantity_mismatch",
        evidence=MATCH_UNDER_EXPANDED,
        partial=False,
        truncated=False,
        candidates=1,
        family="TA",
        family_allowed=True,
    )

    assert decision.confidence == MEDIUM
    assert decision.limiting == "e3_extraction"
    assert decision.severity == "warning"


def test_weakest_mandatory_evidence_is_visible_in_details():
    mentions = [Mention("3TA2", "3TA2", 1, "split_label", "3TA2", "medium")]
    issue = only(compare(mentions, [pe3_row(TA_ENTRIES, quantity=9)]))
    details = issue["details"]

    assert details["confidence"] == MEDIUM
    assert issue["severity"] == "warning"
    assert "качество извлечения Э3" in issue["message"]


def test_two_medium_factors_never_produce_high():
    """The model is a minimum, not an average or a weighted score."""
    components = (
        Evidence("first", MEDIUM),
        Evidence("second", MEDIUM),
        Evidence("third", HIGH),
    )
    assert aggregate_confidence(components) == (MEDIUM, "first")


def test_non_mandatory_evidence_does_not_change_the_result():
    components = (
        Evidence("mandatory", HIGH),
        Evidence("not_applicable", LOW, mandatory=False),
    )
    assert aggregate_confidence(components) == (HIGH, "mandatory")


def test_ambiguous_identifier_caps_classification_at_medium():
    components, _ = build_components([label("X1")])
    decision = assess_finding(
        group=[components["X1"]],
        row=pe3_row(["X9"], quantity=1, duplicate=True),
        reason="row_matched_without_coverage",
        evidence=MATCH_FAMILY_GROUP,
        partial=False,
        truncated=False,
        candidates=1,
        family="X",
        family_allowed=True,
    )

    assert decision.confidence == MEDIUM
    assert decision.limiting == "identifier_classification"
    assert decision.severity == "info"


# ---------------------------------------------------------------------------
# Executable decision table
# ---------------------------------------------------------------------------

def combination(**changes):
    base = {
        "status": "evaluated",
        "reason": "quantity_mismatch",
        "confidence": "high",
        "severity": "warning",
        "partial": False,
        "truncated": False,
        "pe3_page": 2,
        "pe3_table_row": 5,
    }
    base.update(changes)
    return base


@pytest.mark.parametrize("changes,expected", [
    # Positive combinations.
    ({"reason": "quantity_mismatch", "confidence": "high",
      "severity": "warning"}, True),
    ({"reason": "quantity_mismatch", "confidence": "medium",
      "severity": "warning"}, True),
    ({"reason": "quantity_mismatch", "confidence": "low",
      "severity": "info"}, True),
    ({"reason": "row_matched_without_coverage", "confidence": "medium",
      "severity": "info"}, True),
    ({"reason": "row_matched_without_coverage", "confidence": "low",
      "severity": "info"}, True),
    ({"reason": "row_ambiguous", "confidence": "low",
      "severity": "info", "pe3_page": None, "pe3_table_row": None}, True),
    ({"reason": "no_matching_pe3_row", "confidence": "medium",
      "severity": "info", "pe3_page": None, "pe3_table_row": None}, True),
    ({"reason": "no_matching_pe3_row", "confidence": "low",
      "severity": "info", "pe3_page": None, "pe3_table_row": None}, True),
    ({"status": "not_evaluated", "reason": "reference_only",
      "confidence": "low", "severity": "info",
      "pe3_page": None, "pe3_table_row": None}, True),
    ({"status": "not_evaluated", "reason": "no_text_layer",
      "confidence": "low", "severity": "info",
      "pe3_page": None, "pe3_table_row": None}, True),
    # Contradictions.
    ({"reason": "row_matched_without_coverage", "confidence": "high",
      "severity": "info"}, False),
    ({"reason": "row_ambiguous", "confidence": "medium",
      "severity": "info", "pe3_page": None, "pe3_table_row": None}, False),
    ({"reason": "row_ambiguous", "confidence": "low",
      "severity": "warning", "pe3_page": None, "pe3_table_row": None}, False),
    ({"reason": "no_matching_pe3_row", "confidence": "high",
      "severity": "info", "pe3_page": None, "pe3_table_row": None}, False),
    ({"reason": "no_matching_pe3_row", "confidence": "medium",
      "severity": "info"}, False),
    ({"reason": "quantity_mismatch", "confidence": "high",
      "severity": "warning", "partial": True}, False),
    ({"reason": "quantity_mismatch", "confidence": "high",
      "severity": "warning", "truncated": True}, False),
    ({"reason": "quantity_mismatch", "confidence": "low",
      "severity": "warning"}, False),
    ({"reason": "quantity_mismatch", "confidence": "high",
      "severity": "warning", "pe3_page": None}, False),
    ({"reason": "reference_only", "confidence": "high",
      "severity": "warning"}, False),
    ({"reason": "ambiguous_row", "confidence": "low",
      "severity": "info"}, False),
    ({"status": "not_evaluated", "reason": "quantity_mismatch",
      "confidence": "high", "severity": "warning"}, False),
])
def test_executable_decision_table(changes, expected):
    assert validate_combination(**combination(**changes)) is expected


def test_high_confidence_requires_quantity_mismatch_and_complete_analysis():
    issue = only(compare([label("3TA2")], [pe3_row(TA_ENTRIES, quantity=9)]))
    details = issue["details"]
    assert details["confidence"] == HIGH

    for change in (
        {"reason": "no_matching_pe3_row", "pe3_page": None, "pe3_table_row": None},
        {"reason": "row_matched_without_coverage"},
        {"reason": "row_ambiguous", "pe3_page": None, "pe3_table_row": None},
        {"partial": True},
        {"truncated": True},
        {"status": "not_evaluated"},
    ):
        broken = deepcopy(details)
        broken.update(change)
        assert valid_details(broken, FILES, issue) is False


def test_warning_requires_quantity_mismatch_and_not_partial():
    issue = deepcopy(only(compare([label("3TA2")], [pe3_row(TA_ENTRIES, quantity=9)])))
    issue["severity"] = "warning"
    details = issue["details"]
    assert details["confidence"] == HIGH

    wrong_reason = deepcopy(details)
    wrong_reason["reason"] = "row_matched_without_coverage"
    assert valid_details(wrong_reason, FILES, issue) is False

    no_row = deepcopy(details)
    no_row["reason"] = "no_matching_pe3_row"
    no_row["pe3_page"] = None
    no_row["pe3_table_row"] = None
    assert valid_details(no_row, FILES, issue) is False

    incomplete = deepcopy(details)
    incomplete["partial"] = True
    assert valid_details(incomplete, FILES, issue) is False

    low_confidence = deepcopy(details)
    low_confidence["confidence"] = LOW
    assert valid_details(low_confidence, FILES, issue) is False


# ---------------------------------------------------------------------------
# Schema, Excel and history compatibility
# ---------------------------------------------------------------------------

SCHEMA_V1_KEYS = {
    "schema_version", "kind", "status", "reason", "e3_document", "pe3_document",
    "e3_pages", "pe3_page", "pe3_table_row", "pe3_row_band_pt", "pe3_page_size_pt",
    "pe3_quantity", "pe3_entries_count", "pe3_name", "pe3_name_confidence",
    "pe3_source", "type_family", "type_name", "confidence", "partial", "truncated",
    "missing", "ambiguous", "limits",
}


def test_details_schema_v1_is_not_extended():
    issue = only(compare([label("3TA2")], [pe3_row(TA_ENTRIES, quantity=9)]))
    details = issue["details"]

    assert set(details) == SCHEMA_V1_KEYS
    assert details["schema_version"] == 1
    assert details["kind"] == "e3_pe3_missing_designations"


def test_excel_keeps_two_additive_columns_and_history_identity_is_stable():
    issue = only(compare([label("3TA2")], [pe3_row(TA_ENTRIES, quantity=9)]))
    run = {
        "id": 1,
        "created_at": "2026-11-21T00:00:00Z",
        "files": [E3, PE3],
        "issues": [issue],
        "finding_states": [],
    }
    with BytesIO(build_excel_report(run)) as stream:
        book = load_workbook(stream)
        try:
            header = list(book["Замечания"].values)[0]
            assert header[:10] == (
                "№", "Уровень", "Код", "Документ", "Лист/место", "Описание",
                "Рекомендация", "Статус", "Комментарий инженера", "Дата изменения",
            )
            assert header[10:] == (
                "Доказательства KD-PE-105", "Детали KD-PE-105 (JSON)",
            )
        finally:
            book.close()
    assert finding_identity(issue) == finding_identity(deepcopy(issue))


def test_legacy_v1_report_with_quantity_mismatch_is_still_valid():
    """Previously stored high/warning quantity_mismatch remains readable."""
    issue = only(compare([label("3TA2")], [pe3_row(TA_ENTRIES, quantity=9)]))
    legacy = deepcopy(issue["details"])
    legacy["confidence"] = "high"
    legacy["status"] = "evaluated"
    legacy["reason"] = "quantity_mismatch"
    assert valid_details(legacy, FILES, issue) is True


def test_legacy_contradiction_is_rejected_instead_of_reinterpreted():
    """The historical ``ambiguous_row``/high combination is never accepted."""
    issue = only(compare([label("3TA2")], [pe3_row(TA_ENTRIES, quantity=9)]))
    legacy = deepcopy(issue["details"])
    legacy["reason"] = "ambiguous_row"
    legacy["confidence"] = "high"
    assert valid_details(legacy, FILES, issue) is False


def test_no_matching_evidence_uses_medium_even_with_many_candidates():
    decision = assess_finding(
        group=build_components([label("9HLG1")])[0].values(),
        row=None,
        reason="no_matching_pe3_row",
        evidence=MATCH_NONE,
        partial=False,
        truncated=False,
        candidates=0,
        family="HLG",
        family_allowed=True,
    )

    assert decision.confidence == MEDIUM
    assert decision.severity == "info"
