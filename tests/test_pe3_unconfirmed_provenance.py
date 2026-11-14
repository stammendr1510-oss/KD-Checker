"""Synthetic local-only provenance tests for informational PE3 versus E3 gaps."""
import fitz

import kd_crosscheck as cross


def blank_pdf():
    with fitz.open() as pdf:
        pdf.new_page()
        return pdf.tobytes()


def source_row(page, entries, *, number=None, band=None):
    return {"page": page, "entries": set(entries), "quantity": len(entries),
            "unknown": [], "source": ", ".join(sorted(entries)),
            "table_row": number, "row_band": band}


def finding(monkeypatch, rows):
    monkeypatch.setattr(cross, "get_spec_rows", lambda _: rows)
    issues = cross.run("test.Э3.pdf", blank_pdf(), "test.ПЭ3.pdf", blank_pdf())
    return next(issue for issue in issues if issue["code"] == "KD-PE-101")


def test_complete_grid_has_source_row_and_preserves_informational_status(monkeypatch):
    issue = finding(monkeypatch, [source_row(2, ["KM1", "KM2"], number=7, band=(70, 90))])
    assert issue["severity"] == "info"
    assert "KM1: лист PDF 2, строка таблицы 7" in issue["message"]
    assert "KM2: лист PDF 2, строка таблицы 7" in issue["message"]
    assert "Это не считается ошибкой КД" in issue["message"]


def test_unknown_grid_never_invents_row_even_if_number_supplied(monkeypatch):
    issue = finding(monkeypatch, [source_row(3, ["KM1"], number=23)])
    assert "KM1: лист PDF 3" in issue["message"]
    assert "строка таблицы 23" not in issue["message"]


def test_same_designator_multiple_locations_are_reported_without_multiplication(monkeypatch):
    issue = finding(monkeypatch, [
        source_row(2, ["KM1"], number=4, band=(30, 60)),
        source_row(2, ["KM1"], number=4, band=(30, 60)),
        source_row(3, ["KM1"]),
    ])
    assert issue["message"].count("лист PDF 2, строка таблицы 4") == 1
    assert "лист PDF 3" in issue["message"]


def test_location_examples_are_bounded_to_eight(monkeypatch):
    entries = [f"KM{i}" for i in range(1, 11)]
    issue = finding(monkeypatch, [source_row(1, entries, number=2, band=(60, 90))])
    assert "10 обозначений" in issue["message"]
    assert "KM10" in issue["message"]  # Lexical first eight include KM10.
    assert issue["message"].count(": лист PDF") == 8


def test_no_unconfirmed_designators_does_not_emit_hint(monkeypatch):
    monkeypatch.setattr(cross, "get_spec_rows", lambda _: [source_row(1, ["KM1"])])
    # A scheme PDF containing KM1 matches the only PE3 designation.
    with fitz.open() as pdf:
        page = pdf.new_page()
        page.insert_text((72, 72), "KM1")
        scheme = pdf.tobytes()
    issues = cross.run("test.Э3.pdf", scheme, "test.ПЭ3.pdf", blank_pdf())
    assert not any(issue["code"] == "KD-PE-101" for issue in issues)
