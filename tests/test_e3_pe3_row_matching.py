"""Block 2: evidence-based row matching for KD-PE-105.

The tests cover exact matching, ranges/lists/wrapping, quantity as supporting
evidence, ambiguity preservation and the required control cases A (TA),
B (KM) and C (similar device types).  Every scenario is synthetic: no
enterprise PDF, no report file and no network.
"""
import fitz
import pytest

import kd_crosscheck as cross
from e3_pe3_inventory import (
    MATCH_FAMILY_GROUP,
    MATCH_UNDER_EXPANDED,
    Mention,
    _attribute_component,
    compare_inventories,
    valid_details,
)
from kd_crosscheck import expand_notation


E3 = "ABCD.123456.001.Э3.pdf"
PE3 = "ABCD.123456.001.ПЭ3.pdf"
TRANSFORMERS = {f"{group}TA{number}" for group in (1, 2, 3) for number in (1, 2, 3)}


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


def ta_control_entries():
    return [f"{group}TA{number}" for group in (1, 2, 3) for number in (1, 2, 3)
            if not (group == 3 and number in (2, 3))]


def warning_issue(details):
    return {
        "severity": "warning",
        "code": "KD-PE-105",
        "file": PE3,
        "location": "Э3 ↔ ПЭ3",
        "message": "На схеме обнаружены обозначения, отсутствующие в перечне элементов.",
        "recommendation": "Проверьте перечень.",
        "details": details,
    }


# ---------------------------------------------------------------------------
# PE3 notation extraction: ranges, lists, separators and wrapping
# ---------------------------------------------------------------------------

def test_ranges_lists_and_wrapped_fragments_are_expanded():
    # Comma and semicolon are explicit list separators; a newline may only
    # complete a wrapped range, not silently split two designations.
    assert expand_notation("1TA1...1TA3, 2TA1...2TA3; 3TA1\n...3TA3") == (
        TRANSFORMERS, []
    )
    assert expand_notation("1TA1;1TA2;1TA3") == ({"1TA1", "1TA2", "1TA3"}, [])
    assert expand_notation("1TA1,1TA3") == ({"1TA1", "1TA3"}, [])


def test_numeric_gap_is_not_invented():
    entries, unknown = expand_notation("1TA1;1TA3")
    assert entries == {"1TA1", "1TA3"}
    assert unknown == []
    assert "1TA2" not in entries


def test_complete_designation_is_not_split_from_an_ambiguous_numeric_fragment():
    # Without an explicit separator the whole fragment is ambiguous.  It must
    # not yield XT1 (a partial designation) or XT12 (an invented one).
    entries, unknown = expand_notation("XT1 2")
    assert entries == set()
    assert unknown == ["XT1 2"]
    assert "XT1" not in entries and "XT12" not in entries


def test_unknown_range_does_not_become_a_row_candidate():
    # A row whose range could not be expanded must not prove coverage.
    row = pe3_row(["3TA1"], quantity=9, unknown=["3TA2...3TA9"])
    row_obj, reason, evidence, _ = _attribute_component(label("3TA3"), {"TA": [row]})
    assert row_obj is None
    assert reason == "no_matching_pe3_row"
    assert evidence != MATCH_UNDER_EXPANDED


# ---------------------------------------------------------------------------
# Case A - TA: 3TA2 and 3TA3 are detected without guessing by quantity
# ---------------------------------------------------------------------------

def test_case_a_detects_3ta2_and_3ta3_without_quantity_guessing():
    mentions = [label("3TA1"), label("3TA2"), label("3TA3")]
    row = pe3_row(ta_control_entries(), quantity=9)
    issue = only(compare(mentions, [row]))
    details = issue["details"]

    assert issue["severity"] == "warning"
    assert details["reason"] == "quantity_mismatch"
    assert details["confidence"] == "high"
    assert [item["designation"] for item in details["missing"]] == ["3TA2", "3TA3"]
    assert details["pe3_page"] == 2
    assert details["pe3_table_row"] == 2
    assert details["pe3_quantity"] == 9
    assert details["pe3_entries_count"] == 7
    assert valid_details(details, {E3, PE3}, issue) is True


def test_case_a_does_not_invent_designations_from_a_numeric_gap():
    # Only 3TA2 exists in E3; quantity 9 must not create 3TA3 or any other item.
    row = pe3_row(ta_control_entries(), quantity=9)
    issue = only(compare([label("3TA2")], [row]))
    details = issue["details"]

    assert [item["designation"] for item in details["missing"]] == ["3TA2"]
    assert "3TA3" not in [item["designation"] for item in details["missing"]]
    assert details["pe3_quantity"] == 9


# ---------------------------------------------------------------------------
# Case B - KM: a one-to-one row cannot prove ownership of 17 designations
# ---------------------------------------------------------------------------

def test_case_b_km_one_to_one_row_is_not_a_confirmed_match():
    mentions = [label(f"3KM{number}") for number in range(2, 19)]  # 17 items
    row = pe3_row(["3KM1"], quantity=1, duplicate=True, table_row=7,
                  name="Контактор")
    issue = only(compare(mentions, [row]))
    details = issue["details"]

    assert issue["severity"] == "info"
    assert details["reason"] == "row_matched_without_coverage"
    assert details["confidence"] == "medium"
    assert len(details["missing"]) == 17
    assert details["pe3_quantity"] == 1
    assert details["pe3_entries_count"] == 1
    assert details["pe3_table_row"] == 7
    assert valid_details(details, {E3, PE3}, issue) is True
    # The schema must reject promoting this tentative match to a warning.
    assert valid_details(details, {E3, PE3}, warning_issue(details)) is False


def test_family_and_group_coincidence_is_tentative_not_proof():
    row = pe3_row(["3KM1"], quantity=1, duplicate=True, table_row=7)
    row_obj, reason, evidence, _ = _attribute_component(label("3KM2"), {"KM": [row]})
    assert row_obj is row
    assert reason == "row_matched_without_coverage"
    assert evidence == MATCH_FAMILY_GROUP


def test_quantity_surplus_without_group_match_is_not_enough():
    # quantity 5 > entries 1, but the row belongs to group 1 while the missing
    # designation is in group 3.  Quantity alone must not confirm the row.
    row = pe3_row(["1KM1"], quantity=5, table_row=7)
    issue = only(compare([label("3KM2")], [row]))
    details = issue["details"]

    assert issue["severity"] == "info"
    assert details["reason"] == "no_matching_pe3_row"
    assert details["pe3_page"] is None
    assert details["pe3_table_row"] is None


# ---------------------------------------------------------------------------
# Case C - similar device types must not be merged into one row
# ---------------------------------------------------------------------------

def test_case_c_similar_rows_are_preserved_as_ambiguous():
    rows = [
        pe3_row(["3KM1"], quantity=1, table_row=2, name="Контактор тип A"),
        pe3_row(["3KM2"], quantity=1, table_row=3, name="Контактор тип B"),
    ]
    issue = only(compare([label("3KM3")], rows))
    details = issue["details"]

    assert issue["severity"] == "info"
    assert details["reason"] == "row_ambiguous"
    assert details["confidence"] == "low"
    assert details["pe3_page"] is None
    assert details["pe3_table_row"] is None
    assert "несколько подходящих строк" in issue["message"]
    assert valid_details(details, {E3, PE3}, issue) is True


def test_multiple_confirmed_rows_are_also_ambiguous():
    rows = [
        pe3_row(["3TA1"], quantity=9, table_row=2),
        pe3_row(["3TA1"], quantity=9, table_row=3),
    ]
    issue = only(compare([label("3TA2")], rows))
    details = issue["details"]

    assert details["reason"] == "row_ambiguous"
    assert details["confidence"] == "low"
    assert details["pe3_page"] is None
    assert details["pe3_table_row"] is None


def test_ambiguous_details_must_not_carry_a_row_number():
    rows = [
        pe3_row(["3KM1"], quantity=1, table_row=2),
        pe3_row(["3KM2"], quantity=1, table_row=3),
    ]
    issue = only(compare([label("3KM3")], rows))
    details = dict(issue["details"])
    details["pe3_page"] = 2
    details["pe3_table_row"] = 2
    assert valid_details(details, {E3, PE3}, issue) is False


# ---------------------------------------------------------------------------
# Zero / one / several suitable rows
# ---------------------------------------------------------------------------

def test_zero_suitable_rows_has_an_explicit_reason():
    issue = only(compare([label("3HLG1")], [pe3_row(["1KM1"], quantity=1)]))
    details = issue["details"]

    assert issue["severity"] == "info"
    assert details["reason"] == "no_matching_pe3_row"
    assert details["pe3_page"] is None
    assert details["pe3_table_row"] is None
    assert "Причина:" in issue["message"]


def test_single_suitable_row_is_located():
    issue = only(compare([label("3KM2")],
                         [pe3_row(["3KM1"], quantity=1, table_row=7)]))
    details = issue["details"]

    assert details["reason"] == "row_matched_without_coverage"
    assert details["pe3_page"] == 2
    assert details["pe3_table_row"] == 7


def test_exact_designation_is_not_reported_as_missing():
    assert compare([label("3TA1")], [pe3_row(["3TA1"], quantity=1)]) == []


# ---------------------------------------------------------------------------
# End-to-end PE3 extraction with comma/newline separated ranges
# ---------------------------------------------------------------------------

def scheme_pdf(lines):
    with fitz.open() as pdf:
        page = pdf.new_page(width=600, height=800)
        for index, text in enumerate(lines):
            page.insert_text((60, 90 + index * 12), text, fontsize=8)
        return pdf.tobytes()


def pe3_pdf(notation, quantity, name="Current transformer"):
    with fitz.open() as pdf:
        pdf.new_page(width=600, height=800)
        page = pdf.new_page(width=600, height=800)
        page.insert_text((60, 90), notation, fontsize=4)
        page.insert_text((438, 90), str(quantity), fontsize=6)
        page.insert_text((250, 90), name, fontsize=6)
        for index in range(2):
            y = 70 + index * 60
            page.draw_line((50, y), (480, y))
        return pdf.tobytes()


def test_end_to_end_comma_separated_pe3_keeps_ta_control():
    # The PE3 row really lacks 3TA2 and 3TA3; the comma is a list separator,
    # not a reason to merge the two ranges or invent a gap.
    findings = cross.run(
        E3,
        scheme_pdf([f"{group}TA{number}" for group in (1, 2, 3) for number in (1, 2, 3)]),
        PE3,
        pe3_pdf("1TA1...1TA3, 2TA1...2TA3; 3TA1", 9),
    )
    issues = [finding for finding in findings if finding["code"] == "KD-PE-105"]
    assert len(issues) == 1
    details = issues[0]["details"]
    assert details["reason"] == "quantity_mismatch"
    assert [item["designation"] for item in details["missing"]] == ["3TA2", "3TA3"]
