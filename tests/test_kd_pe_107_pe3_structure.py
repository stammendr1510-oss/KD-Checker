"""KD-PE-107 audit of PE3 table structure: no false conflict or missing rows."""
import fitz

import kd_crosscheck as cross
from kd_pe_107_reference import scheme_pdf, table_pdf


E3 = "ABCD.123456.001.Э3.pdf"
PE3 = "ABCD.123456.001.ПЭ3.pdf"


def two_page_table(rows):
    with fitz.open() as pdf:
        for page_rows in rows:
            page = pdf.new_page(width=600, height=800)
            for index in range(len(page_rows) + 1):
                y = 70 + index * 50
                page.draw_line((50, y), (480, y))
            for x in (50, 115, 420, 455, 480):
                page.draw_line((x, 70), (x, 70 + len(page_rows) * 50))
            for index, (designation, quantity, name) in enumerate(page_rows):
                y = 70 + index * 50 + 18
                if designation:
                    page.insert_text((60, y), designation, fontsize=6)
                page.insert_text((438, y), str(quantity), fontsize=6)
                if name:
                    page.insert_text((200, y), name, fontsize=6)
        return pdf.tobytes()


def test_multiline_cell_and_semicolon_group_are_one_row():
    pe3 = table_pdf([("KA2;KA3", 2, "Rele module")])
    rows = cross.get_spec_rows(pe3)
    assert len(rows) == 1
    assert rows[0]["entries"] == {"KA2", "KA3"}
    assert rows[0]["quantity"] == 2


def test_empty_designator_accessory_row_is_not_an_error():
    pe3 = table_pdf([("", 1, "Mounting accessory"), ("XT1", 1, "Terminal block")])
    rows = cross.get_spec_rows(pe3)
    assert [row["entries"] for row in rows] == [{"XT1"}]
    findings = cross.run(E3, scheme_pdf(["XT1"]), PE3, pe3)
    assert "KD-PE-000" not in [finding["code"] for finding in findings]
    assert "KD-PE-105" not in [finding["code"] for finding in findings]


def test_terminal_block_with_mixed_terminals_is_not_a_conflict():
    pe3 = table_pdf([
        ("XT0", 1, "Terminal 2.5"),
        ("XT0", 1, "Terminal 4"),
        ("XT1", 1, "Terminal 2.5"),
        ("XT2", 1, "Terminal 4"),
    ])
    findings = cross.check_spec_duplicates(PE3, pe3)
    assert not [finding for finding in findings
                if finding["code"] == "KD-PE-106" and finding["severity"] == "warning"]


def test_compatible_multisheet_repeat_is_informational_not_warning():
    pe3 = two_page_table([
        [("KLD3", 1, "Rele module KPR-SCE-24VDC-1C")],
        [("KLD3", 1, "Rele module KPR-SCE-24VDC-1C")],
    ])
    findings = cross.check_spec_duplicates(PE3, pe3)
    repeats = [finding for finding in findings if finding["code"] == "KD-PE-104"]
    assert repeats and repeats[0]["severity"] == "info"
    assert "совпадает" in repeats[0]["message"]
    assert not [finding for finding in findings
                if finding["code"] == "KD-PE-106" and finding["severity"] == "warning"]


def test_merged_rect_cells_are_parsed_without_false_quantity_warning():
    with fitz.open() as pdf:
        page = pdf.new_page(width=600, height=800)
        for top in (70, 120):
            page.draw_rect(fitz.Rect(50, top, 115, top + 50))
            page.draw_rect(fitz.Rect(115, top, 420, top + 50))
            page.insert_text((60, top + 18), "XT1", fontsize=6)
            page.insert_text((438, top + 18), "1", fontsize=6)
        content = pdf.tobytes()
    rows = cross.get_spec_rows(content)
    assert [row["entries"] for row in rows] == [{"XT1"}, {"XT1"}]
    findings = cross.check_spec_duplicates(PE3, content)
    assert not [finding for finding in findings if finding["code"] == "KD-PE-102"]


def test_range_quantity_mismatch_remains_visible():
    pe3 = table_pdf([("KA1...KA3", 4, "Rele module")])
    findings = cross.run(E3, scheme_pdf(["KA1", "KA2", "KA3"]), PE3, pe3)
    quantity = [finding for finding in findings if finding["code"] == "KD-PE-102"]
    assert quantity and quantity[0]["severity"] == "warning"
