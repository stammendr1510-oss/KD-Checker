"""Offline synthetic source-page tests for mixed Latin/Cyrillic E3 designators."""
import fitz
import kd_crosscheck as cross


def blank_pdf(pages):
    with fitz.open() as doc:
        for _ in range(pages):
            doc.new_page()
        return doc.tobytes()


def mixed_issues(monkeypatch, values):
    monkeypatch.setattr(cross, "get_spec_rows", lambda _: [{
        "page": 1, "source": "KM1", "entries": {"KM1"},
        "quantity": 1, "unknown": [], "row_band": None, "table_row": None,
    }])
    # The page text is generated and blank; synthetic extracted labels
    # simulate PDF text spans without an installed Cyrillic font.
    monkeypatch.setattr(cross, "scheme_designations",
                        lambda page: iter(values[page.number]))
    findings = cross.run("synthetic.Э3.pdf", blank_pdf(len(values)),
                         "synthetic.ПЭ3.pdf", blank_pdf(1))
    return [x for x in findings if x["code"] == "KD-PE-103"]


def test_each_original_spelling_has_exact_source_pdf_page(monkeypatch):
    findings = mixed_issues(monkeypatch, [["KM1"], ["КM1"]])
    assert len(findings) == 1
    finding = findings[0]
    assert finding["severity"] == "warning"
    assert "KM1 — лист(ы) PDF 1" in finding["message"]
    assert "КM1 — лист(ы) PDF 2" in finding["message"]
    assert "проверьте, не разные ли это объекты" in finding["recommendation"]


def test_repeated_form_pages_are_unique_and_sorted(monkeypatch):
    findings = mixed_issues(monkeypatch, [["КM1", "КM1"], ["KM1"], ["КM1"]])
    assert len(findings) == 1
    assert "КM1 — лист(ы) PDF 1, 3" in findings[0]["message"]


def test_long_page_list_is_bounded_per_spelling(monkeypatch):
    findings = mixed_issues(monkeypatch, [["KM1"], *[["КM1"] for _ in range(6)]])
    assert len(findings) == 1
    assert "КM1 — лист(ы) PDF 2, 3, 4 (+3 листов)" in findings[0]["message"]


def test_single_latin_or_single_cyrillic_spelling_does_not_trigger_warning(monkeypatch):
    assert mixed_issues(monkeypatch, [["KM1"], ["KM1"]]) == []
    assert mixed_issues(monkeypatch, [["КM1"], ["КM1"]]) == []


def test_no_confusion_across_distinct_normalized_designators(monkeypatch):
    assert mixed_issues(monkeypatch, [["KM1"], ["КM2"]]) == []
