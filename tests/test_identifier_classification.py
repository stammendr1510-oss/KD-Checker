"""Block 3: contextual classification of E3 identifiers for KD-PE-105.

Every scenario is synthetic: no enterprise PDF, no report file and no network.
The tests cover the six examples from the task, the device families required by
the acceptance criteria, the classification report and the integration rule
"a pin is presence evidence, never a standalone product".
"""
import pytest

from e3_pe3_inventory import (
    Mention,
    build_components,
    classification_summary,
    compare_inventories,
    extract_mentions,
    valid_details,
)
from identifier_classification import (
    AMBIGUOUS,
    CATEGORIES,
    CONNECTION_REFERENCE,
    CROSS_REFERENCE,
    DEVICE_CANDIDATE,
    TERMINAL_OR_PIN,
    classification_report,
    classify_identifier,
)


E3 = "ABCD.123456.001.Э3.pdf"
PE3 = "ABCD.123456.001.ПЭ3.pdf"


def mention(designation, *, raw=None, page=1, kind="label", context=None, line_text=""):
    return Mention(
        designation=designation,
        raw=raw or designation,
        page=page,
        kind=kind,
        context=context if context is not None else designation,
        confidence="high" if kind == "label" else "medium",
        bbox=None,
        page_size=None,
        line_text=line_text,
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


def compare(mentions, rows):
    pages = max((item.page for item in mentions), default=1)
    return compare_inventories(
        E3, PE3, mentions, rows,
        e3_word_count=len(mentions),
        e3_pages_processed=pages,
        e3_pages_with_text=pages,
    )


def only(findings):
    assert len(findings) == 1, [finding["details"] for finding in findings]
    return findings[0]


def ta_control_entries():
    return {
        f"{group}TA{number}" for group in (1, 2, 3) for number in (1, 2, 3)
        if not (group == 3 and number in (2, 3))
    }


# ---------------------------------------------------------------------------
# Required categories and the six task examples
# ---------------------------------------------------------------------------

def test_required_categories_are_declared():
    assert CATEGORIES == {
        DEVICE_CANDIDATE,
        TERMINAL_OR_PIN,
        CONNECTION_REFERENCE,
        CROSS_REFERENCE,
        AMBIGUOUS,
    }


@pytest.mark.parametrize("raw,expected,parent,pin", [
    ("1KM1", DEVICE_CANDIDATE, None, None),
    ("1KM1:A1", TERMINAL_OR_PIN, "1KM1", "A1"),
    ("XT4.2", TERMINAL_OR_PIN, "XT4", "2"),
    ("3TA2", DEVICE_CANDIDATE, None, None),
])
def test_task_examples_are_classified(raw, expected, parent, pin):
    result = classify_identifier(raw)

    assert result.category == expected
    assert result.parent_device == parent
    assert result.pin == pin
    assert result.raw == raw
    assert result.designation == raw.upper()


def test_hlw_pin_is_not_a_standalone_lamp():
    result = classify_identifier(
        "1HLW", kind="reference", context="1HLW:X1", line_text="1HLW:X1"
    )

    assert result.category == TERMINAL_OR_PIN
    assert result.parent_device == "1HLW"
    assert result.pin == "X1"
    assert result.eligible_for_presence_check is False
    assert result.presence_evidence is True


def test_bare_x1_is_ambiguous_and_preserved():
    result = classify_identifier("X1", kind="label", context="X1", line_text="X1")

    assert result.category == AMBIGUOUS
    assert result.requires_context is True
    assert result.eligible_for_presence_check is False
    assert result.designation == "X1"


def test_classification_preserves_raw_parent_and_reason():
    pin = classify_identifier("1KM1:A1")
    assert pin.reason == "explicit_pin_separator"
    assert pin.parent_device == "1KM1"

    dotted = classify_identifier("XT4.2")
    assert dotted.reason == "dotted_pin_suffix"
    assert dotted.parent_device == "XT4"
    assert dotted.pin == "2"


# ---------------------------------------------------------------------------
# Device families required by the acceptance criteria
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("designation", [
    "1KM1",   # electromagnetic contactor
    "1QF1",   # automatic circuit breaker
    "1TA1",   # current transformer
    "1SF1",   # switch
    "1HLG1",  # signal lamp
    "1HLW1",  # signal lamp
    "1XT1",   # terminal block
    "1XN1",   # terminal block
])
def test_device_families_are_standalone_candidates(designation):
    result = classify_identifier(designation)

    assert result.category == DEVICE_CANDIDATE
    assert result.eligible_for_presence_check is True
    assert result.requires_context is False


def test_only_the_documented_terminal_family_is_ambiguous():
    # ``X`` is documented as "terminal block / connection", so a bare token is
    # ambiguous.  Multi-letter terminal families remain device candidates.
    # No device code (KM, QF, TA, SF, HLG, ...) has a special rule.
    assert classify_identifier("X1").category == AMBIGUOUS
    assert classify_identifier("XT1").category == DEVICE_CANDIDATE
    assert classify_identifier("XN1").category == DEVICE_CANDIDATE


def test_unknown_token_is_preserved_as_ambiguous():
    result = classify_identifier("ABC")

    assert result.category == AMBIGUOUS
    assert result.designation == "ABC"
    assert result.requires_context is True


# ---------------------------------------------------------------------------
# Connection and cross-reference contexts
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("designation,pin", [("SF1", "1"), ("XT1", "2")])
def test_connection_endpoints_are_references_not_devices(designation, pin):
    line = "SF1:1 - XT1:2"
    result = classify_identifier(
        designation, kind="reference", context=line, line_text=line
    )

    assert result.category == CONNECTION_REFERENCE
    assert result.parent_device == designation
    assert result.pin == pin
    assert result.eligible_for_presence_check is False
    assert result.presence_evidence is True


@pytest.mark.parametrize("line", [
    "QF1 (см. лист 3)",
    "см. QF1",
    "QF1 поз. 5",
    "QF1 ТНБВ.656451.162",
])
def test_cross_references_are_not_standalone_devices(line):
    result = classify_identifier("QF1", kind="reference", context=line, line_text=line)

    assert result.category == CROSS_REFERENCE
    assert result.eligible_for_presence_check is False
    assert result.presence_evidence is True


# ---------------------------------------------------------------------------
# Classification report
# ---------------------------------------------------------------------------

def test_classification_report_separates_presence_check_and_context():
    mentions = [
        mention("1KM1"),
        mention("1KM1", kind="reference", context="1KM1:A1", line_text="1KM1:A1"),
        mention("X1"),
        mention("SF1", kind="reference",
                context="SF1:1 - XT1:2", line_text="SF1:1 - XT1:2"),
    ]
    report = classification_report(mentions)

    assert report["eligible_for_presence_check"] == ["1KM1"]
    assert report["presence_evidence_only"] == ["SF1"]
    assert report["requires_context"] == ["X1"]
    assert report["by_category"][DEVICE_CANDIDATE] == ["1KM1"]
    assert report["by_category"][TERMINAL_OR_PIN] == ["1KM1"]
    assert report["by_category"][AMBIGUOUS] == ["X1"]


def test_inventory_exposes_the_classification_summary():
    report = classification_summary([mention("1KM1"), mention("X1")])

    assert report["eligible_for_presence_check"] == ["1KM1"]
    assert report["requires_context"] == ["X1"]


def test_build_components_exposes_classification_metadata():
    components, _ = build_components([
        mention("1KM1"),
        mention("1KM1", kind="reference", context="1KM1:A1", line_text="1KM1:A1"),
        mention("XT4.2"),
        mention("X1"),
    ])

    assert components["1KM1"].category == DEVICE_CANDIDATE
    assert components["1KM1"].eligible_for_presence_check is True
    assert components["XT4.2"].category == TERMINAL_OR_PIN
    assert components["XT4.2"].parent_device == "XT4"
    assert components["XT4.2"].eligible_for_presence_check is False
    assert components["X1"].category == AMBIGUOUS
    assert components["X1"].requires_context is True


# ---------------------------------------------------------------------------
# Integration: pins are presence evidence, never standalone products
# ---------------------------------------------------------------------------

def test_pin_reference_does_not_create_a_missing_product_finding():
    words = [(60, 90, 120, 96, "1KM1:A1", 0, 0, 0)]
    mentions = extract_mentions(words, 1, page_size=(600, 800))
    row = pe3_row(["3KM1"], quantity=1, table_row=7)

    issue = only(compare(mentions, [row]))
    details = issue["details"]

    assert issue["severity"] == "info"
    assert details["status"] == "not_evaluated"
    assert details["reason"] == "reference_only"
    assert details["missing"] == []
    assert [item["designation"] for item in details["ambiguous"]] == ["1KM1"]
    assert valid_details(details, {E3, PE3}, issue) is True


def test_standalone_device_is_checked_while_pin_is_only_context():
    words = [
        (60, 90, 90, 96, "1KM1", 0, 0, 0),
        (60, 110, 120, 116, "1KM2:A1", 0, 1, 0),
    ]
    mentions = extract_mentions(words, 1, page_size=(600, 800))
    row = pe3_row(["3KM1"], quantity=1, table_row=7)

    findings = compare(mentions, [row])
    assert findings
    missing = [item["designation"] for finding in findings
               for item in finding["details"]["missing"]]
    ambiguous = [item["designation"] for finding in findings
                 for item in finding["details"]["ambiguous"]]
    assert "1KM1" in missing
    assert "1KM2" not in missing
    assert "1KM2" in ambiguous


def test_dotted_terminal_point_is_not_a_missing_terminal_block():
    mentions = [mention("XT4.2")]
    row = pe3_row(["XT9"], quantity=1)

    assert compare(mentions, [row]) == []


def test_needs_context_reason_reaches_details_for_ambiguous_identifier():
    mentions = [mention("KM1", kind="note", context="KM1", line_text="KM1")]
    row = pe3_row(["3KM1"], quantity=1)

    issue = only(compare(mentions, [row]))
    details = issue["details"]

    assert details["status"] == "not_evaluated"
    assert details["reason"] == "reference_only"
    assert details["ambiguous"][0]["designation"] == "KM1"
    assert details["ambiguous"][0]["reason"] == "needs_context"
    assert valid_details(details, {E3, PE3}, issue) is True


# ---------------------------------------------------------------------------
# Acceptance control: 3TA2 and 3TA3 are still detected
# ---------------------------------------------------------------------------

def test_ta_control_is_preserved_with_pin_references():
    mentions = [
        mention("3TA1"),
        mention("3TA2"),
        mention("3TA3"),
        mention("3TA2", kind="reference", page=2,
                context="3TA2:И1 - 3X1:3", line_text="3TA2:И1 - 3X1:3"),
        mention("3TA3", kind="reference", page=2,
                context="3TA3:И1 - 3X1:3", line_text="3TA3:И1 - 3X1:3"),
    ]
    row = pe3_row(ta_control_entries(), quantity=9, table_row=2)

    issue = only(compare(mentions, [row]))
    details = issue["details"]

    assert issue["severity"] == "warning"
    assert details["reason"] == "quantity_mismatch"
    assert [item["designation"] for item in details["missing"]] == ["3TA2", "3TA3"]
    assert valid_details(details, {E3, PE3}, issue) is True
