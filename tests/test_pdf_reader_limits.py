"""PDF extraction budgets, using synthetic documents only."""
import fitz
import pytest

from extractors import extract


@pytest.mark.parametrize("pages", [1, 99, 100, 101])
def test_pdf_page_count_text_and_limit_notice(pages):
    with fitz.open() as pdf:
        for number in range(1, pages + 1):
            pdf.new_page().insert_text((72, 72), f"SYNTHETIC-PAGE-{number:03d}")
        content = pdf.tobytes()
    result = extract("synthetic.pdf", content)
    assert result.pages == pages
    assert "SYNTHETIC-PAGE-001" in result.text
    assert f"SYNTHETIC-PAGE-{min(pages, 100):03d}" in result.text
    assert "SYNTHETIC-PAGE-101" not in result.text
    assert result.notes == (["Прочитаны только первые 100 страниц PDF"] if pages > 100 else [])


def test_pdf_never_requests_page_beyond_extraction_budget(monkeypatch):
    class BudgetedPdf:
        closed = False
        requested = 0

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.closed = True

        def __len__(self):
            return 10000

        def __iter__(self):
            for _ in range(10000):
                self.requested += 1
                if self.requested > 100:
                    raise AssertionError("PDF extraction requested an out-of-budget page")
                yield self

        def get_text(self):
            return "SYNTHETIC "

    pdf = BudgetedPdf()
    monkeypatch.setattr(fitz, "open", lambda **kwargs: pdf)
    result = extract("synthetic.pdf", b"synthetic test double")
    assert pdf.requested == 100
    assert pdf.closed
    assert result.pages == 10000
    assert result.text.count("SYNTHETIC") == 100
