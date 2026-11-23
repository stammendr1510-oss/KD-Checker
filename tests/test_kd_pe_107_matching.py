"""KD-PE-107 group-designation matching and PE3 text-evidence guard."""
import fitz
import pytest

import kd_crosscheck as cross
from e3_pe3_inventory import Mention, compare_inventories
from kd_pe_107_reference import scheme_pdf, table_pdf


E3 = "ABCD.123456.001.Э3.pdf"
PE3 = "ABCD.123456.001.ПЭ3.pdf"


@pytest.mark.parametrize("notation, expected", [
    ("TA2;TA3", {"TA2", "TA3"}),
    ("TA2; TA3", {"TA2", "TA3"}),
    ("TA2,TA3", {"TA2", "TA3"}),
    ("TA2...TA3", {"TA2", "TA3"}),
    ("TA2–TA3", {"TA2", "TA3"}),
    ("TA2\nTA3", {"TA2", "TA3"}),
    ("KA2;KA3", {"KA2", "KA3"}),
    ("HL1;HL2", {"HL1", "HL2"}),
    ("KLD4;KLD5", {"KLD4", "KLD5"}),
])
def test_universal_group_separators_expand(notation, expected):
    assert cross.expand_notation(notation) == (expected, [])


@pytest.mark.parametrize("notation", ["TA2\n2", "TA2 2", "1KM1...1KM3\n4"])
def test_ambiguous_gaps_are_not_invented(notation):
    entries, unknown = cross.expand_notation(notation)
    assert entries == set()
    assert unknown == [notation]


def test_multiline_group_row_is_parsed_and_matched():
    pe3 = table_pdf([("TA2\nTA3", 2, "Current sensor")])
    rows = cross.get_spec_rows(pe3)
    assert [row["entries"] for row in rows] == [{"TA2", "TA3"}]
    findings = cross.run(E3, scheme_pdf(["TA2", "TA3"]), PE3, pe3)
    assert "KD-PE-105" not in [finding["code"] for finding in findings]
    info = next(finding for finding in findings if finding["code"] == "KD-PE-100")
    assert "совпадений — 2" in info["message"]


@pytest.mark.parametrize("separator", [";", "; ", ",", "...", "–", "\n"])
def test_group_row_variants_do_not_create_missing_findings(separator):
    designation = f"KA2{separator}KA3"
    pe3 = table_pdf([(designation, 2, "Rele module")])
    findings = cross.run(E3, scheme_pdf(["KA2", "KA3"]), PE3, pe3)
    assert "KD-PE-105" not in [finding["code"] for finding in findings]


def test_quantity_not_matching_expanded_group_stays_a_warning():
    pe3 = table_pdf([("K1;K2", 3, "Rele module")])
    findings = cross.run(E3, scheme_pdf(["K1", "K2"]), PE3, pe3)
    quantity = [finding for finding in findings if finding["code"] == "KD-PE-102"]
    assert quantity and quantity[0]["severity"] == "warning"


def test_pe3_text_evidence_prevents_a_false_missing_product():
    mentions = [
        Mention("TA2", "TA2", 1, "label", "TA2", "high", None, None),
        Mention("TA3", "TA3", 1, "label", "TA3", "high", None, None),
    ]
    row = {
        "page": 1, "entries": {"XT1"}, "quantity": 1, "unknown": [],
        "row_band": (70.0, 120.0), "table_row": 1, "name": "Terminal block",
        "name_confidence": "medium", "source": "XT1", "page_size_pt": [600.0, 800.0],
    }
    findings = compare_inventories(
        E3, PE3, mentions, [row],
        e3_word_count=2, e3_pages_processed=1, e3_pages_with_text=1,
        pe3_text_designations={"TA2", "TA3"},
    )
    assert len(findings) == 1
    details = findings[0]["details"]
    assert details["status"] == "not_evaluated"
    assert details["reason"] == "present_in_unparsed_pe3_text"
    assert details["missing"] == []
    assert {item["designation"] for item in details["ambiguous"]} == {"TA2", "TA3"}


def test_different_families_in_one_group_row_are_not_merged():
    entries, unknown = cross.expand_notation("TA2;KM2")
    assert entries == {"TA2", "KM2"}
    assert unknown == []
