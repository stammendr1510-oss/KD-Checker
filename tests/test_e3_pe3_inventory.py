"""Synthetic-only regressions for KD-PE-105 (E3 designators absent from PE3)."""
import fitz
import pytest

import kd_crosscheck as cross
from e3_pe3_inventory import Mention, compare_inventories, extract_mentions


E3 = "ABCD.123456.001.Э3.pdf"
PE3 = "ABCD.123456.001.ПЭ3.pdf"
CONTROL_PE3 = [("1TA1...1TA3;2TA1...2TA3;3TA1", 9, "Current transformer")]


def scheme_pdf(pages):
    with fitz.open() as pdf:
        for lines in pages:
            page = pdf.new_page(width=600, height=800)
            for index, text in enumerate(lines):
                page.insert_text((60, 90 + index * 12), text, fontsize=8)
        return pdf.tobytes()


def pe3_pdf(rows):
    with fitz.open() as pdf:
        pdf.new_page(width=600, height=800)
        page = pdf.new_page(width=600, height=800)
        for index, (notation, quantity, name) in enumerate(rows):
            y = 90 + index * 60
            if notation:
                page.insert_text((60, y), notation, fontsize=4)
            page.insert_text((438, y), str(quantity), fontsize=6)
            if name:
                page.insert_text((250, y), name, fontsize=6)
        for index in range(len(rows) + 1):
            y = 70 + index * 60
            page.draw_line((50, y), (480, y))
        return pdf.tobytes()


def run_pair(e3_pages, pe3_rows):
    return cross.run(E3, scheme_pdf(e3_pages), PE3, pe3_pdf(pe3_rows))


def control_e3():
    labels = [f"{group}TA{number}" for group in (1, 2, 3) for number in (1, 2, 3)]
    return [labels, ["3TA2:И1 - 3X1:3", "3TA3:И1 - 3X1:3"]]


def kd_105(findings):
    return [finding for finding in findings if finding["code"] == "KD-PE-105"]


def test_control_reports_exactly_3ta2_and_3ta3():
    findings = run_pair(control_e3(), CONTROL_PE3)
    assert "KD-PE-102" in [finding["code"] for finding in findings]
    issues = kd_105(findings)
    assert len(issues) == 1
    issue = issues[0]
    assert issue["severity"] == "warning"
    details = issue["details"]
    assert details["schema_version"] == 1
    assert details["kind"] == "e3_pe3_missing_designations"
    assert details["status"] == "evaluated"
    assert [item["designation"] for item in details["missing"]] == ["3TA2", "3TA3"]
    assert details["pe3_quantity"] == 9
    assert details["pe3_entries_count"] == 7
    assert details["pe3_page"] == 2
    assert details["pe3_table_row"] is not None
    assert "Current" in details["pe3_name"]
    assert details["confidence"] == "high"
    assert details["e3_pages"] == [1, 2]
    label_evidence = [
        evidence for evidence in details["missing"][0]["evidence"]
        if evidence["kind"] == "label"
    ]
    assert label_evidence and all("bbox_pt" in item for item in label_evidence)


def test_corrected_pe3_removes_kd_105_and_quantity_warning():
    findings = run_pair(
        control_e3(),
        [("1TA1...1TA3;2TA1...2TA3;3TA1...3TA3", 9, "Current transformer")],
    )
    codes = [finding["code"] for finding in findings]
    assert "KD-PE-105" not in codes
    assert "KD-PE-102" not in codes


def test_repeated_mentions_do_not_multiply_components():
    pages = [["3TA2"] * 10 + ["3TA3"], ["3TA2"]]
    issues = kd_105(run_pair(pages, CONTROL_PE3))
    assert len(issues) == 1
    assert [item["designation"] for item in issues[0]["details"]["missing"]] == ["3TA2", "3TA3"]


def test_reference_only_mention_is_not_categorical():
    issues = kd_105(run_pair([["3TA2:И1 - 3X1:3"]], CONTROL_PE3))
    assert len(issues) == 1
    issue = issues[0]
    assert issue["severity"] == "info"
    assert issue["details"]["status"] == "not_evaluated"
    assert issue["details"]["reason"] == "reference_only"
    assert issue["details"]["missing"] == []
    assert "3TA2" in [item["designation"] for item in issue["details"]["ambiguous"]]


def test_scanned_scheme_is_not_evaluated():
    issues = kd_105(run_pair([[]], CONTROL_PE3))
    assert len(issues) == 1
    issue = issues[0]
    assert issue["severity"] == "info"
    assert issue["details"]["status"] == "not_evaluated"
    assert issue["details"]["reason"] == "no_text_layer"
    assert issue["details"]["missing"] == []


def test_cyrillic_lookalike_is_normalized_in_mentions():
    words = [(60, 90, 90, 96, "3ТА2", 0, 0, 0)]
    mentions = extract_mentions(words, 1, page_size=(600, 800))
    assert any(mention.designation == "3TA2" and mention.kind == "label" for mention in mentions)


def test_terminal_contacts_use_base_device():
    findings = run_pair([["XT1.1", "XT1.2"]], [("XT1", 2, "")])
    assert not kd_105(findings)


def test_wrapped_and_ellipsis_ranges_do_not_create_false_missing():
    from test_crosscheck import spec_pdf

    pe = spec_pdf(
        ["1TA1...1TA3;", "2TA1...2TA3;", "3TA1", "...3TA3"],
        quantity_line=2,
    )
    findings = cross.run(E3, scheme_pdf(control_e3()), PE3, pe)
    assert not kd_105(findings)


def test_multiple_pe3_groups_attribute_only_ta():
    e3 = [[
        *[f"{group}TA{number}" for group in (1, 2, 3) for number in (1, 2, 3)],
        *[f"1KM{number}" for number in range(1, 9)],
    ]]
    rows = [
        ("1TA1...1TA3;2TA1...2TA3;3TA1", 9, ""),
        ("1KM1...1KM8", 8, ""),
    ]
    issues = kd_105(run_pair(e3, rows))
    assert len(issues) == 1
    assert [item["designation"] for item in issues[0]["details"]["missing"]] == ["3TA2", "3TA3"]


def test_auxiliary_row_without_designation_is_ignored():
    issues = kd_105(run_pair([["3TA2"]], [("", 1, ""), CONTROL_PE3[0]]))
    assert len(issues) == 1
    assert [item["designation"] for item in issues[0]["details"]["missing"]] == ["3TA2"]


def test_similar_groups_are_not_confused():
    # Same numeric suffixes in three groups: only the designations actually
    # present in E3 may be reported, and they must stay in group 3.
    issues = kd_105(run_pair([["1TA1", "2TA1", "3TA1", "3TA2", "3TA3"]], CONTROL_PE3))
    assert len(issues) == 1
    assert [item["designation"] for item in issues[0]["details"]["missing"]] == ["3TA2", "3TA3"]


def test_truncated_analysis_downgrades_to_info():
    mentions = [Mention("3TA2", "3TA2", 1, "label", "3TA2", "high", (60, 90, 90, 96), (600, 800))]
    rows = [{
        "page": 2, "entries": {"3TA1"}, "quantity": 9, "unknown": [],
        "row_band": (70.0, 130.0), "table_row": 2, "name": "", "name_confidence": "low",
        "source": "3TA1", "page_size_pt": [600.0, 800.0],
    }]
    findings = compare_inventories(
        E3, PE3, mentions, rows,
        e3_word_count=1, e3_pages_processed=1, e3_pages_with_text=1,
        e3_truncated=True,
    )
    assert len(findings) == 1
    assert findings[0]["severity"] == "info"
    assert findings[0]["details"]["truncated"] is True


def test_long_missing_list_is_bounded_in_details_and_message():
    mentions = [
        Mention(f"9TA{index}", f"9TA{index}", 1, "label", f"9TA{index}", "high", None, None)
        for index in range(1, 1101)
    ]
    rows = [{
        "page": 2, "entries": {"9TA0"}, "quantity": 1100, "unknown": [],
        "row_band": (70.0, 130.0), "table_row": 2, "name": "", "name_confidence": "low",
        "source": "9TA0", "page_size_pt": [600.0, 800.0],
    }]
    findings = compare_inventories(
        E3, PE3, mentions, rows,
        e3_word_count=1100, e3_pages_processed=1, e3_pages_with_text=1,
    )
    assert len(findings) == 1
    issue = findings[0]
    assert len(issue["details"]["missing"]) == 1000
    assert issue["details"]["limits"]["truncated_list"] is True
    assert len(issue["message"]) <= 14000


def test_details_are_present_only_for_kd_105():
    for issue in run_pair(control_e3(), CONTROL_PE3):
        assert ("details" in issue) is (issue["code"] == "KD-PE-105")
