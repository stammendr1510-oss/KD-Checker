"""Synthetic-only coverage for precise PE3 quantity mismatch locations."""

import fitz

import kd_crosscheck as cross


def synthetic_pdf():
    with fitz.open() as document:
        document.new_page()
        return document.tobytes()


def check_with_rows(monkeypatch, rows):
    monkeypatch.setattr(cross, "get_spec_rows", lambda _: rows)
    return [issue for issue in cross.run(
        "ABCD.123456.001.Э3.pdf", synthetic_pdf(),
        "ABCD.123456.001.ПЭ3.pdf", synthetic_pdf(),
    ) if issue["code"] == "KD-PE-102"]


def row(*, number, band):
    return {
        "page": 2,
        "source": "KM1...KM3",
        "quantity": 2,
        "entries": {"KM1", "KM2", "KM3"},
        "unknown": [],
        "row_band": band,
        "table_row": number,
    }


def test_verified_grid_row_is_displayed_alongside_pdf_page(monkeypatch):
    findings = check_with_rows(monkeypatch, [row(number=7, band=(110.0, 140.0))])
    assert len(findings) == 1
    assert findings[0]["location"] == "Лист PDF 2, строка таблицы 7"
    assert findings[0]["severity"] == "warning"
    assert "не подтверждённая ошибка КД" in findings[0]["recommendation"]


def test_without_grid_the_row_number_is_not_invented(monkeypatch):
    findings = check_with_rows(monkeypatch, [row(number=None, band=None)])
    assert len(findings) == 1
    assert findings[0]["location"] == "Лист PDF 2"


def test_unproven_row_number_does_not_escape_grid_guard(monkeypatch):
    findings = check_with_rows(monkeypatch, [row(number=7, band=None)])
    assert findings[0]["location"] == "Лист PDF 2"


def test_equal_counts_have_no_quantity_mismatch(monkeypatch):
    source = row(number=7, band=(110.0, 140.0))
    source["quantity"] = 3
    assert check_with_rows(monkeypatch, [source]) == []
