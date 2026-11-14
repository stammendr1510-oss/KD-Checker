"""Synthetic-document checks for upload identity collisions."""
from fastapi.testclient import TestClient
import fitz

from document_identity import CODE, pdf_identity_findings
import app as web


def source(name, content=b"same"):
    from kd_crosscheck import designation_type, pair_key
    return (name, designation_type(name), pair_key(name), content)


def test_single_pdf_or_distinct_keys_never_warn():
    assert pdf_identity_findings([]) == []
    assert pdf_identity_findings([source("alpha.Э3.pdf")]) == []
    assert pdf_identity_findings([source("alpha.Э3.pdf"), source("alpha.ПЭ3.pdf")]) == []
    assert pdf_identity_findings([source("alpha.Э3.pdf"), source("beta.Э3.pdf")]) == []


def test_exact_duplicates_and_case_variants_are_reported_once():
    issues = pdf_identity_findings([source("alpha.Э3.pdf"), source("ALPHA.Э3.pdf")])
    assert len(issues) == 1
    assert issues[0]["code"] == CODE and issues[0]["severity"] == "info"
    assert "полностью совпадающим" in issues[0]["message"]
    assert "2 документа" in issues[0]["message"]


def test_distinct_content_with_same_document_identity_is_only_information():
    issues = pdf_identity_findings([
        source("alpha.Э3.pdf", b"first"), source("alpha.Э3.pdf", b"second"),
    ])
    assert len(issues) == 1
    assert "различающимся содержимым" in issues[0]["message"]
    assert issues[0]["severity"] == "info"
    assert "ошибку в КД" in issues[0]["recommendation"]


def test_multiple_groups_do_not_merge_different_kinds_or_names():
    issues = pdf_identity_findings([
        source("alpha.Э3.pdf"), source("alpha.Э3.pdf"),
        source("alpha.ПЭ3.pdf"), source("alpha.ПЭ3.pdf"),
        source("beta.ПЭ3.pdf"), source("beta.ПЭ3.pdf"),
    ])
    assert len(issues) == 3
    assert all(i["code"] == CODE for i in issues)


def test_api_reports_repeated_pdf_as_info_without_changing_crosscheck(monkeypatch):
    with fitz.open() as pdf:
        page = pdf.new_page()
        page.insert_text((72, 72), "Synthetic drawing")
        content = pdf.tobytes()
    monkeypatch.setattr(web, "analyze", lambda docs, cfg: [])
    monkeypatch.setattr(web, "save_run", lambda names, issues: 19)
    monkeypatch.setattr(web, "check_spec_duplicates", lambda *args: [])
    monkeypatch.setattr(web, "crosscheck", lambda *args: [])
    with TestClient(web.app) as client:
        response = client.post("/api/check", files=[
            ("files", ("demo.Э3.pdf", content, "application/pdf")),
            ("files", ("demo.Э3.pdf", content, "application/pdf")),
        ])
    assert response.status_code == 200
    assert response.json()["summary"] == {"error": 0, "warning": 0, "info": 1}
    assert [i["code"] for i in response.json()["issues"]] == [CODE]


def test_empty_or_untyped_pdf_sources_are_ignored():
    assert pdf_identity_findings([
        ("drawing.pdf", None, "drawing", b"x"),
        ("drawing.pdf", None, "drawing", b"x"),
    ]) == []



def test_analysis_sources_dedup_only_identical_bytes_of_same_document():
    from document_identity import unique_pdf_analysis_sources

    inputs = [
        source("alpha.Э3.pdf", b"one"),
        source("ALPHA.Э3.pdf", b"one"),
        source("alpha.Э3.pdf", b"two"),
        source("alpha.ПЭ3.pdf", b"one"),
        source("beta.Э3.pdf", b"one"),
    ]
    retained = unique_pdf_analysis_sources(inputs)
    assert retained == [inputs[0], inputs[2], inputs[3], inputs[4]]


def test_api_scans_identical_pe3_once_but_reports_repeated_upload(monkeypatch):
    with fitz.open() as pdf:
        page = pdf.new_page()
        page.insert_text((72, 72), "Synthetic PE3 drawing")
        pe_content = pdf.tobytes()
    calls = []
    monkeypatch.setattr(web, "analyze", lambda docs, cfg: [])
    monkeypatch.setattr(web, "save_run", lambda names, issues: 19)
    monkeypatch.setattr(web, "check_spec_duplicates", lambda name, data: calls.append(name) or [])
    monkeypatch.setattr(web, "crosscheck", lambda *args: [])
    with TestClient(web.app) as client:
        response = client.post("/api/check", files=[
            ("files", ("demo.ПЭ3.pdf", pe_content, "application/pdf")),
            ("files", ("demo.ПЭ3.pdf", pe_content, "application/pdf")),
        ])
    assert response.status_code == 200
    assert calls == ["demo.ПЭ3.pdf"]
    assert [i["code"] for i in response.json()["issues"]] == [CODE]
    assert len(response.json()["files"]) == 2


def test_api_crosscheck_ambiguous_versions_are_not_automatically_paired(monkeypatch):
    with fitz.open() as pdf:
        page = pdf.new_page()
        page.insert_text((72, 72), "Synthetic PDF")
        content1 = pdf.tobytes()
    with fitz.open() as pdf:
        page = pdf.new_page()
        page.insert_text((72, 72), "Different synthetic PDF")
        content2 = pdf.tobytes()
    calls = []
    monkeypatch.setattr(web, "analyze", lambda docs, cfg: [])
    monkeypatch.setattr(web, "save_run", lambda names, issues: 19)
    monkeypatch.setattr(web, "check_spec_duplicates", lambda *args: [])
    monkeypatch.setattr(web, "crosscheck", lambda *args: calls.append(args) or [])
    with TestClient(web.app) as client:
        response = client.post("/api/check", files=[
            ("files", ("demo.Э3.pdf", content1, "application/pdf")),
            ("files", ("demo.Э3.pdf", content2, "application/pdf")),
            ("files", ("demo.ПЭ3.pdf", content1, "application/pdf")),
        ])
    assert response.status_code == 200
    assert len(calls) == 0
    assert [i["code"] for i in response.json()["issues"]] == [CODE, "KD-SET-103"]
    assert response.json()["summary"]["info"] == 2
