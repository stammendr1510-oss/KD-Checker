"""KD-PE-107 independent regression matrix: confirmed, false and ambiguous."""
import fitz

import kd_crosscheck as cross
from identifier_classification import AMBIGUOUS, classify_identifier
from kd_pe_107_reference import (
    all_passed,
    render_table,
    run_scenarios,
    scheme_pdf,
    table_pdf,
)


E3 = "ABCD.123456.001.Э3.pdf"
PE3 = "ABCD.123456.001.ПЭ3.pdf"


def _codes(findings):
    return [finding["code"] for finding in findings]


def _kd_105(findings):
    return [finding for finding in findings if finding["code"] == "KD-PE-105"]


def test_reference_bundle_passes_every_scenario():
    results = run_scenarios()
    assert all_passed(results) is True
    assert all(item["result"] == "PASS" for item in results)
    table = render_table(results)
    for key in ("group_row", "conflict", "confusable", "families", "markers"):
        assert key in table


# ---------------------------------------------------------------------------
# Confirmed defects
# ---------------------------------------------------------------------------

def test_confirmed_conflict_of_two_incompatible_products():
    pe3 = table_pdf([
        ("KLD1...KLD3", 3, "Rele module KPR-SCE-24VDC-1C"),
        ("KLD3", 1, "Rele module KPR-SCE-230VAC-1C"),
    ])
    findings = cross.check_spec_duplicates(PE3, pe3)
    conflicts = [finding for finding in findings if finding["code"] == "KD-PE-106"]
    assert conflicts and conflicts[0]["severity"] == "warning"
    assert conflicts[0]["location"].count("ПЭ3:") == 2


def test_confirmed_missing_device_is_still_reported():
    pe3 = table_pdf([("XT1", 1, "Terminal block")])
    findings = cross.run(E3, scheme_pdf(["K1"]), PE3, pe3)
    missing = _kd_105(findings)
    assert missing
    assert "K1" in [item["designation"] for item in missing[0]["details"]["missing"]]


def test_confirmed_quantity_contradiction_is_a_warning():
    pe3 = table_pdf([("KA1...KA3", 4, "Rele module")])
    findings = cross.run(E3, scheme_pdf(["KA1", "KA2", "KA3"]), PE3, pe3)
    quantity = [finding for finding in findings if finding["code"] == "KD-PE-102"]
    assert quantity and quantity[0]["severity"] == "warning"


# ---------------------------------------------------------------------------
# False remarks
# ---------------------------------------------------------------------------

def test_false_missing_for_grouped_ta_row():
    pe3 = table_pdf([("TA2;TA3", 2, "Current sensor")])
    findings = cross.run(E3, scheme_pdf(["TA2", "TA3"]), PE3, pe3)
    assert "KD-PE-105" not in _codes(findings)


def test_false_duplicate_for_compatible_multisheet_reference():
    with fitz.open() as pdf:
        for page_index in range(2):
            page = pdf.new_page(width=600, height=800)
            for index in range(2):
                page.draw_line((50, 70 + index * 50), (480, 70 + index * 50))
            for x in (50, 115, 420, 455, 480):
                page.draw_line((x, 70), (x, 120))
            page.insert_text((60, 88), "HL1", fontsize=6)
            page.insert_text((438, 88), "1", fontsize=6)
            page.insert_text((200, 88), "Lamp 24VDC", fontsize=6)
        content = pdf.tobytes()
    findings = cross.check_spec_duplicates(PE3, content)
    repeats = [finding for finding in findings if finding["code"] == "KD-PE-104"]
    assert repeats and repeats[0]["severity"] == "info"
    assert not [finding for finding in findings
                if finding["code"] == "KD-PE-106" and finding["severity"] == "warning"]


def test_false_conflict_for_terminal_accessories():
    pe3 = table_pdf([
        ("XT4", 1, "Terminal 2.5"),
        ("XT4", 1, "Terminal 4"),
    ])
    findings = cross.check_spec_duplicates(PE3, pe3)
    assert not [finding for finding in findings
                if finding["code"] == "KD-PE-106" and finding["severity"] == "warning"]


# ---------------------------------------------------------------------------
# Ambiguous situations
# ---------------------------------------------------------------------------

def test_ambiguous_confusable_characters_are_informational():
    pe3 = table_pdf([("K1", 1, "PLC module S01.PLC.01")])
    findings = cross.run(E3, scheme_pdf(["SO1.PLC.01"]), PE3, pe3)
    candidates = [finding for finding in findings if finding["code"] == "KD-PE-107"]
    assert candidates and candidates[0]["severity"] == "info"


def test_ambiguous_missing_text_layer_is_not_evaluated():
    with fitz.open() as pdf:
        pdf.new_page(width=600, height=800)
        blank = pdf.tobytes()
    pe3 = table_pdf([("XT1", 1, "Terminal block")])
    findings = cross.run(E3, blank, PE3, pe3)
    missing = _kd_105(findings)
    assert missing and missing[0]["details"]["status"] == "not_evaluated"
    assert missing[0]["details"]["reason"] == "no_text_layer"


def test_ambiguous_line_break_gap_stays_unknown():
    entries, unknown = cross.expand_notation("XT1\n2")
    assert entries == set()
    assert unknown == ["XT1\n2"]


def test_ambiguous_bare_x_identifier_requires_context():
    item = classify_identifier("X1", kind="label", line_text="X1")
    assert item.category == AMBIGUOUS
    assert item.requires_context is True
