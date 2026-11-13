"""Upload-set pair presence: conservative clues, never a completeness verdict."""
import pytest

from set_completeness import CODE, unpaired_document_findings


@pytest.mark.parametrize("names", [
    [],
    ["demo.Э3.pdf"],
    ["demo.ПЭ3.pdf"],
    ["demo.Э3.pdf", "other.Э3.pdf"],
    ["demo.ПЭ3.pdf", "other.ПЭ3.pdf"],
    ["demo.Э3.pdf", "demo.ПЭ3.pdf"],
    ["DEMO.Э3.pdf", "demo.ПЭ3.pdf"],
    ["demo.Э3.pdf", "demo.ПЭ3.pdf", "drawing.pdf"],
])
def test_no_warning_for_single_type_or_complete_pair(names):
    assert unpaired_document_findings(names) == []


def test_mismatched_pair_produces_informational_upload_hints():
    findings = unpaired_document_findings(["alpha.Э3.pdf", "beta.ПЭ3.pdf"])
    assert len(findings) == 2
    assert {f["file"] for f in findings} == {"alpha.Э3.pdf", "beta.ПЭ3.pdf"}
    assert all(f["code"] == CODE and f["severity"] == "info" for f in findings)
    assert all("загруженного" in f["message"] or "загрузке" in f["recommendation"] for f in findings)
    assert all("не означает его отсутствия в КД" in f["recommendation"] for f in findings)


def test_completed_pair_does_not_hide_another_unpaired_document():
    findings = unpaired_document_findings([
        "alpha.Э3.pdf", "alpha.ПЭ3.pdf", "beta.Э3.pdf",
    ])
    assert [(f["file"], f["code"]) for f in findings] == [("beta.Э3.pdf", CODE)]
    assert "ПЭ3" in findings[0]["message"]


def test_duplicate_identical_names_do_not_duplicate_hints():
    findings = unpaired_document_findings([
        "alpha.Э3.pdf", "ALPHA.Э3.pdf", "beta.ПЭ3.pdf",
    ])
    assert len(findings) == 2


def test_non_pdf_or_non_document_names_are_not_candidates():
    assert unpaired_document_findings([
        "alpha.Э3.xlsx", "beta.ПЭ3.csv", "drawing.pdf",
    ]) == []


def test_order_does_not_change_results():
    names = ["beta.ПЭ3.pdf", "alpha.Э3.pdf", "paired.ПЭ3.pdf", "paired.Э3.pdf"]
    assert unpaired_document_findings(names) == unpaired_document_findings(list(reversed(names)))


def test_api_adds_hint_only_for_unmatched_mixed_pdf_uploads(monkeypatch):
    from fastapi.testclient import TestClient
    import fitz
    import app as web

    with fitz.open() as pdf:
        page = pdf.new_page()
        page.insert_text((72, 72), "Synthetic drawing for test only")
        content = pdf.tobytes()

    monkeypatch.setattr(web, "analyze", lambda docs, config: [])
    monkeypatch.setattr(web, "save_run", lambda names, issues: 77)
    monkeypatch.setattr(web, "crosscheck", lambda *args: [])
    monkeypatch.setattr(web, "check_spec_duplicates", lambda *args: [])
    with TestClient(web.app) as client:
        r = client.post("/api/check", files=[
            ("files", ("alpha.Э3.pdf", content, "application/pdf")),
            ("files", ("beta.ПЭ3.pdf", content, "application/pdf")),
        ])
    assert r.status_code == 200
    assert r.json()["summary"]["info"] == 2
    assert [f["code"] for f in r.json()["issues"]] == [CODE, CODE]
