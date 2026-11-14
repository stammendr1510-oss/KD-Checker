"""Synthetic, offline source-page evidence for E3/PE3 matched designators."""
import fitz
import kd_crosscheck as cross


def pdf_pages(*texts):
    with fitz.open() as pdf:
        for value in texts:
            page = pdf.new_page()
            if value:
                page.insert_text((72, 72), value)
        return pdf.tobytes()


def match_issue(monkeypatch, entries, pages):
    monkeypatch.setattr(cross, "get_spec_rows", lambda _: [{
        "page": 1, "source": "; ".join(sorted(entries)), "quantity": len(entries),
        "entries": set(entries), "unknown": [], "row_band": None,
        "table_row": None,
    }])
    issues = cross.run("test.Э3.pdf", pdf_pages(*pages), "test.ПЭ3.pdf", pdf_pages(""))
    return next(item for item in issues if item["code"] == "KD-PE-100")


def test_matches_include_real_e3_page_references(monkeypatch):
    item = match_issue(monkeypatch, {"KM1", "XT2"}, ["KM1", "XT2"])
    assert item["severity"] == "info"
    assert "совпадений — 2" in item["message"]
    assert "KM1: лист(ы) PDF 1" in item["message"]
    assert "XT2: лист(ы) PDF 2" in item["message"]
    assert "отсутствие замечаний не подтверждает полноту" in item["recommendation"]


def test_unmatched_pe3_has_no_fabricated_e3_source_page(monkeypatch):
    item = match_issue(monkeypatch, {"KM1"}, ["No designations"])
    assert "совпадений — 0" in item["message"]
    assert "Примеры совпадений в Э3" not in item["message"]


def test_duplicate_occurrences_deduplicated_across_pdf_pages(monkeypatch):
    item = match_issue(monkeypatch, {"KM1"}, ["KM1 KM1", "KM1"])
    assert "KM1: лист(ы) PDF 1, 2" in item["message"]


def test_page_examples_are_capped_and_ordered(monkeypatch):
    item = match_issue(monkeypatch, {"KM1"}, ["KM1"] * 7)
    assert "KM1: лист(ы) PDF 1, 2, 3 (+4 листов)" in item["message"]
    assert "PDF 4" not in item["message"]


def test_designation_examples_are_capped_at_five(monkeypatch):
    codes = {f"KM{i}" for i in range(1, 8)}
    item = match_issue(monkeypatch, codes, [" ".join(sorted(codes))])
    assert item["message"].count(": лист(ы) PDF") == 5
    assert "совпадений — 7" in item["message"]
