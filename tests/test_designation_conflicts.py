"""KD-PE-106: universal conflict detection for repeated PE3 designations."""
import pytest

from designation_conflicts import (
    AMBIGUOUS,
    COMPATIBLE,
    TYPE_CONFLICT,
    VOLTAGE_CONFLICT,
    compare_names,
    conflict_findings,
)


def row(entries, quantity, name="", *, page=1, band=(70.0, 120.0), table_row=1, unknown=None):
    return {
        "page": page,
        "entries": set(entries),
        "quantity": quantity,
        "unknown": list(unknown or []),
        "row_band": band,
        "table_row": table_row,
        "name": name,
        "name_confidence": "medium" if name else "low",
        "source": ";".join(sorted(entries)),
        "page_size_pt": [600.0, 800.0],
    }


@pytest.mark.parametrize("left, right, expected", [
    ("Rele module KPR-SCE-24VDC-1C", "Rele module KPR-SCE-230VAC-1C", VOLTAGE_CONFLICT),
    ("Rele module KPR-SCE-24VDC-1C", "Timer module XYZ-24VDC-1C", TYPE_CONFLICT),
    ("Rele module KPR-SCE-24VDC-1C", "rele module kpr-sce-24vdc-1c", COMPATIBLE),
    ("Module A", "Module B", AMBIGUOUS),
    ("", "Module B", "unknown"),
])
def test_name_comparison_is_attribute_based(left, right, expected):
    assert compare_names(left, right) == expected


def test_voltage_conflict_reports_both_rows_without_choosing_a_winner():
    rows = [
        row(["KLD1", "KLD2", "KLD3"], 3, "Rele module KPR-SCE-24VDC-1C", table_row=1),
        row(["KLD3"], 1, "Rele module KPR-SCE-230VAC-1C", band=(120.0, 170.0), table_row=2),
    ]
    findings = conflict_findings(rows, "demo.ПЭ3.pdf")
    assert len(findings) == 1
    finding = findings[0]
    assert finding["code"] == "KD-PE-106"
    assert finding["severity"] == "warning"
    assert "KLD3" in finding["message"]
    assert "24VDC" in finding["message"] and "230VAC" in finding["message"]
    assert finding["location"].count("ПЭ3:") == 2
    assert "details" not in finding


def test_type_conflict_is_universal_for_other_families():
    rows = [
        row(["KA7"], 1, "Rele module KPR-SCE-24VDC-1C", table_row=1),
        row(["KA7"], 1, "Timer module XYZ-24VDC-1C", band=(120.0, 170.0), table_row=2),
    ]
    findings = conflict_findings(rows, "demo.ПЭ3.pdf")
    assert findings and findings[0]["severity"] == "warning"
    assert "KA7" in findings[0]["message"]


def test_compatible_repeat_is_not_a_conflict():
    rows = [
        row(["HL3"], 1, "Lamp 24VDC", table_row=1),
        row(["HL3"], 1, "Lamp 24VDC", band=(120.0, 170.0), table_row=2),
    ]
    assert conflict_findings(rows, "demo.ПЭ3.pdf") == []


def test_ambiguous_repeat_is_informational():
    rows = [
        row(["K5"], 1, "Rele module type A", table_row=1),
        row(["K5"], 1, "Rele module type B", band=(120.0, 170.0), table_row=2),
    ]
    findings = conflict_findings(rows, "demo.ПЭ3.pdf")
    assert len(findings) == 1
    assert findings[0]["severity"] == "info"
    assert "K5" in findings[0]["message"]


def test_terminal_assembly_repeats_are_not_conflict_warnings():
    rows = [
        row(["XT1"], 1, "Terminal 2.5", table_row=1),
        row(["XT1"], 1, "Terminal 4", band=(120.0, 170.0), table_row=2),
    ]
    findings = conflict_findings(rows, "demo.ПЭ3.pdf")
    assert all(finding["severity"] != "warning" for finding in findings)


def test_range_and_singleton_intersection_is_detected():
    rows = [
        row(["KLD1", "KLD2", "KLD3"], 3, "Rele module KPR-SCE-24VDC-1C", table_row=1),
        row(["KLD3"], 1, "Rele module KPR-SCE-230VAC-1C", band=(120.0, 170.0), table_row=2),
    ]
    findings = conflict_findings(rows, "demo.ПЭ3.pdf")
    assert [finding["code"] for finding in findings] == ["KD-PE-106"]


def test_two_ranges_intersection_is_detected():
    rows = [
        row(["KLD1", "KLD2", "KLD3"], 3, "Rele module KPR-SCE-24VDC-1C", table_row=1),
        row(["KLD3", "KLD4"], 2, "Rele module KPR-SCE-230VAC-1C", band=(120.0, 170.0), table_row=2),
    ]
    findings = conflict_findings(rows, "demo.ПЭ3.pdf")
    assert findings and findings[0]["severity"] == "warning"


def test_rows_without_grid_bands_are_not_conflict_evidence():
    rows = [
        row(["KLD3"], 1, "Rele module KPR-SCE-24VDC-1C", band=None),
        row(["KLD3"], 1, "Rele module KPR-SCE-230VAC-1C", band=(120.0, 170.0), table_row=2),
    ]
    assert conflict_findings(rows, "demo.ПЭ3.pdf") == []


def test_header_area_is_not_a_product_row():
    rows = [
        row(["KLD3"], 1, "Document title", band=(20.0, 50.0), table_row=1),
        row(["KLD3"], 1, "Rele module KPR-SCE-230VAC-1C", band=(70.0, 120.0), table_row=2),
    ]
    assert conflict_findings(rows, "demo.ПЭ3.pdf") == []
