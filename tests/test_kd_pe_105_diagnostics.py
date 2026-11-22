"""Diagnostic baseline for KD-PE-105 (block 1, no algorithm changes).

These tests intentionally describe the **current** behaviour of the published
KD-PE-105 implementation.  They are a reproduction harness for the disputed
findings, not an approval of that behaviour.

Every scenario is synthetic: no enterprise PDF, no report file and no network.
Where the expected behaviour is a hypothesis, it is recorded in
``docs/kd-pe-105-diagnostics.md`` and is deliberately *not* asserted here.
"""
import pytest

from e3_pe3_inventory import Mention, compare_inventories, extract_mentions, valid_details
from e3_pe3_inventory import _row_confidence
from kd_crosscheck import expand_notation


E3 = "ABCD.123456.001.Э3.pdf"
PE3 = "ABCD.123456.001.ПЭ3.pdf"


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


def compare(mentions, rows):
    pages = max((mention.page for mention in mentions), default=1)
    return compare_inventories(
        E3, PE3, mentions, rows,
        e3_word_count=len(mentions),
        e3_pages_processed=pages,
        e3_pages_with_text=pages,
    )


def only(findings):
    assert len(findings) == 1, [finding["details"] for finding in findings]
    return findings[0]


# ---------------------------------------------------------------------------
# Finding #1/#2: TA quantity 9 versus 7 expanded designations
# ---------------------------------------------------------------------------

def test_ta_quantity_mismatch_is_warning_high_with_quantity_reason():
    mentions = [label("3TA2"), label("3TA3")]
    row = pe3_row(
        [f"{group}TA{number}" for group in (1, 2, 3) for number in (1, 2, 3)
         if not (group == 3 and number in (2, 3))],
        quantity=9,
        duplicate=False,
    )
    issue = only(compare(mentions, [row]))
    details = issue["details"]

    assert issue["severity"] == "warning"
    assert details["reason"] == "quantity_mismatch"
    assert details["confidence"] == "high"
    assert details["pe3_quantity"] == 9
    assert details["pe3_entries_count"] == 7
    assert [item["designation"] for item in details["missing"]] == ["3TA2", "3TA3"]


# ---------------------------------------------------------------------------
# Finding #7: KM row with quantity 1, reason=ambiguous_row, confidence=high
# ---------------------------------------------------------------------------

def test_km_quantity_one_with_duplicate_evidence_is_not_a_confirmed_match():
    """Block 2 fixes finding #7: extraction quality is not row ownership.

    ``duplicate_eligible`` still means the row was extracted reliably, but a
    one-to-one row with the same letter family and group cannot prove that the
    missing designation belongs to it.  The finding stays informational and
    carries the explicit reason ``row_matched_without_coverage``.
    """
    row = pe3_row(["3KM1"], quantity=1, duplicate=True, table_row=7)
    assert _row_confidence(row) == "high"

    issue = only(compare([label("3KM2")], [row]))
    details = issue["details"]

    assert issue["severity"] == "info"
    assert details["reason"] == "row_matched_without_coverage"
    assert details["confidence"] == "medium"
    assert details["pe3_quantity"] == 1
    assert details["pe3_entries_count"] == 1
    assert details["pe3_table_row"] == 7
    assert [item["designation"] for item in details["missing"]] == ["3KM2"]

    assert valid_details(details, {E3, PE3}, issue) is True


def test_km_quantity_one_without_duplicate_evidence_is_info_medium():
    row = pe3_row(["3KM1"], quantity=1, duplicate=False, table_row=7)
    assert _row_confidence(row) == "medium"

    issue = only(compare([label("3KM2")], [row]))
    details = issue["details"]

    assert issue["severity"] == "info"
    assert details["reason"] == "row_matched_without_coverage"
    assert details["confidence"] == "medium"
    assert details["pe3_quantity"] == 1


def test_quantity_below_expanded_entries_is_not_a_quantity_mismatch():
    """Only ``quantity > len(entries)`` is treated as a mismatch.

    A row that expands to more items than its quantity cannot confirm that a
    different designation belongs to it; the match stays tentative.
    """
    row = pe3_row(["3KM1", "3KM2"], quantity=1, duplicate=True, table_row=7)
    issue = only(compare([label("3KM3")], [row]))
    details = issue["details"]

    assert issue["severity"] == "info"
    assert details["reason"] == "row_matched_without_coverage"
    assert details["confidence"] == "medium"
    assert details["pe3_quantity"] == 1
    assert details["pe3_entries_count"] == 2


# ---------------------------------------------------------------------------
# Finding classes HLG / KK / QF / SB: no matching PE3 row
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("designation,family", [
    ("3HLG1", "HLG"),
    ("3KK1", "KK"),
    ("3QF1", "QF"),
    ("3SB1", "SB"),
])
def test_missing_family_without_pe3_row_is_info_medium(designation, family):
    rows = [pe3_row(["1KM1"], quantity=1)]
    issue = only(compare([label(designation)], rows))
    details = issue["details"]

    assert issue["severity"] == "info"
    assert details["reason"] == "no_matching_pe3_row"
    assert details["confidence"] == "medium"
    assert details["type_family"] == family
    assert details["pe3_page"] is None
    assert details["pe3_table_row"] is None
    assert details["pe3_quantity"] is None


def test_component_label_confidence_does_not_reach_details_confidence():
    """A high-confidence E3 label still yields details.confidence=medium on info."""
    issue = only(compare([label("3HLG1", confidence="high")],
                         [pe3_row(["1KM1"], quantity=1)]))
    assert issue["severity"] == "info"
    assert issue["details"]["confidence"] == "medium"


# ---------------------------------------------------------------------------
# Row attribution edge cases
# ---------------------------------------------------------------------------

def test_equal_scored_rows_are_preserved_as_ambiguous():
    """Two same-family rows with equal evidence must not be merged or chosen."""
    rows = [
        pe3_row(["3KM1"], quantity=1, band=None, table_row=None),
        pe3_row(["3KM2"], quantity=1, band=None, table_row=None),
    ]
    issue = only(compare([label("3KM3")], rows))
    details = issue["details"]

    assert issue["severity"] == "info"
    assert details["reason"] == "row_ambiguous"
    assert details["confidence"] == "low"
    assert details["pe3_page"] is None
    assert details["pe3_table_row"] is None


def test_table_row_is_hidden_without_a_pdf_row_band():
    row = pe3_row(["3KM1"], quantity=1, duplicate=True, band=None, table_row=7)
    issue = only(compare([label("3KM2")], [row]))
    details = issue["details"]

    assert details["pe3_page"] == 2
    assert details["pe3_table_row"] is None
    assert details["pe3_row_band_pt"] is None


# ---------------------------------------------------------------------------
# Finding classes SF / X / XT: terminals and references
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("designation,family", [("X1", "X"), ("XT1", "XT")])
def test_terminal_families_are_information_only_even_with_verified_row(designation, family):
    row = pe3_row([f"{family}9"], quantity=1, duplicate=True, table_row=7)
    issue = only(compare([label(designation)], [row]))
    details = issue["details"]

    assert issue["severity"] == "info"
    assert details["reason"] == "row_matched_without_coverage"
    assert details["confidence"] == "medium"
    assert details["type_family"] == family


def test_contact_suffix_matches_the_base_device():
    mentions = [label("XT1.1"), label("XT1.2"), label("3TA2.1")]
    rows = [pe3_row(["XT1"], quantity=1), pe3_row(["3TA2"], quantity=1)]
    assert compare(mentions, rows) == []


def test_contact_reference_is_classified_as_reference_not_label():
    words = [
        (60, 90, 90, 96, "SF1:1", 0, 0, 0),
        (95, 90, 105, 96, "-", 0, 0, 1),
        (110, 90, 140, 96, "XT1:2", 0, 0, 2),
    ]
    mentions = extract_mentions(words, 1, page_size=(600, 800))
    by_designation = {mention.designation: mention for mention in mentions}

    assert by_designation["SF1"].kind == "reference"
    assert by_designation["SF1"].confidence == "medium"
    assert by_designation["XT1"].kind == "reference"


def test_reference_only_mention_is_silent_without_a_matching_family():
    """Reference-only SF mention with no SF row produces no KD-PE-105 finding."""
    words = [(60, 90, 90, 96, "SF1:1", 0, 0, 0)]
    mentions = extract_mentions(words, 1, page_size=(600, 800))
    assert compare(mentions, [pe3_row(["1KM1"], quantity=1)]) == []


def test_reference_only_mention_with_matching_family_is_not_evaluated():
    words = [(60, 90, 90, 96, "SF1:1", 0, 0, 0)]
    mentions = extract_mentions(words, 1, page_size=(600, 800))
    issue = only(compare(mentions, [pe3_row(["SF9"], quantity=1)]))
    details = issue["details"]

    assert issue["severity"] == "info"
    assert details["status"] == "not_evaluated"
    assert details["reason"] == "reference_only"
    assert [item["designation"] for item in details["ambiguous"]] == ["SF1"]


# ---------------------------------------------------------------------------
# PE3 notation: ranges, wrapping and grouping
# ---------------------------------------------------------------------------

def test_expand_notation_keeps_group_prefix_and_short_range_form():
    assert expand_notation("1KM1...1KM3;2KM1..2") == (
        {"1KM1", "1KM2", "1KM3", "2KM1", "2KM2"}, []
    )


def test_expand_notation_joins_wrapped_fragments():
    assert expand_notation("1TA1...\n1TA3") == ({"1TA1", "1TA2", "1TA3"}, [])


def test_expand_notation_rejects_oversized_range():
    entries, unknown = expand_notation("1KM1...1KM200")
    assert entries == set()
    assert unknown == ["1KM1...1KM200"]
