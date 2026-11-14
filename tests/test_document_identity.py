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


def test_api_ambiguous_pair_preserves_independent_pe3_analysis_and_history(monkeypatch):
    """An ambiguous E3 pair cannot suppress PE3-only checks or uploaded-file history."""
    def pdf_bytes(label):
        with fitz.open() as pdf:
            page = pdf.new_page()
            page.insert_text((72, 72), label)
            return pdf.tobytes()

    scheme_a = pdf_bytes("Synthetic scheme version A")
    scheme_b = pdf_bytes("Synthetic scheme version B")
    spec = pdf_bytes("Synthetic specification")
    standalone_calls, crosscheck_calls, saved = [], [], []

    monkeypatch.setattr(web, "analyze", lambda docs, cfg: [])
    monkeypatch.setattr(web, "check_spec_duplicates",
                        lambda name, data: standalone_calls.append((name, data)) or [{
                            "severity": "warning", "code": "KD-PE-104", "file": name,
                            "location": "ПЭ3", "message": "Synthetic duplicate",
                            "recommendation": "Review the source",
                        }])
    monkeypatch.setattr(web, "crosscheck",
                        lambda *args: crosscheck_calls.append(args) or [])
    monkeypatch.setattr(web, "save_run",
                        lambda names, issues: saved.append((names, issues)) or 23)

    with TestClient(web.app) as client:
        response = client.post("/api/check", files=[
            ("files", ("demo.Э3.pdf", scheme_a, "application/pdf")),
            ("files", ("demo.Э3.pdf", scheme_b, "application/pdf")),
            ("files", ("demo.ПЭ3.pdf", spec, "application/pdf")),
        ])
    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == 23
    assert len(payload["files"]) == 3
    assert len(standalone_calls) == 1
    assert standalone_calls[0][0] == "demo.ПЭ3.pdf"
    assert crosscheck_calls == []
    codes = [item["code"] for item in payload["issues"]]
    assert codes.count("KD-SET-102") == 1
    assert codes.count("KD-SET-103") == 1
    assert codes.count("KD-PE-104") == 1
    assert len(saved) == 1
    assert len(saved[0][0]) == 3
    assert saved[0][1] == payload["issues"]


def test_api_ambiguous_pe3_versions_preserve_independent_scans(monkeypatch):
    """Different PE3 bytes are scanned separately, without guessing a scheme pair."""
    def pdf_bytes(label):
        with fitz.open() as pdf:
            page = pdf.new_page()
            page.insert_text((72, 72), label)
            return pdf.tobytes()

    scheme = pdf_bytes("Synthetic scheme")
    spec_a = pdf_bytes("Synthetic specification A")
    spec_b = pdf_bytes("Synthetic specification B")
    scanned, compared = [], []
    monkeypatch.setattr(web, "analyze", lambda docs, cfg: [])
    monkeypatch.setattr(web, "save_run", lambda names, issues: 37)
    monkeypatch.setattr(web, "check_spec_duplicates",
                        lambda name, data: scanned.append((name, data)) or [])
    monkeypatch.setattr(web, "crosscheck",
                        lambda *args: compared.append(args) or [])

    with TestClient(web.app) as client:
        response = client.post("/api/check", files=[
            ("files", ("demo.Э3.pdf", scheme, "application/pdf")),
            ("files", ("demo.ПЭ3.pdf", spec_a, "application/pdf")),
            ("files", ("demo.ПЭ3.pdf", spec_b, "application/pdf")),
        ])
    assert response.status_code == 200
    assert [data for _, data in scanned] == [spec_a, spec_b]
    assert compared == []
    assert [item["code"] for item in response.json()["issues"]] == [
        "KD-SET-102", "KD-SET-103"
    ]


def test_api_ambiguous_group_does_not_suppress_another_valid_pair(monkeypatch):
    """Only the ambiguous identity is skipped; unrelated one-to-one pairs run."""
    def pdf_bytes(label):
        with fitz.open() as pdf:
            page = pdf.new_page()
            page.insert_text((72, 72), label)
            return pdf.tobytes()

    a1, a2 = pdf_bytes("Synthetic scheme A1"), pdf_bytes("Synthetic scheme A2")
    b_scheme, b_spec = pdf_bytes("Synthetic scheme B"), pdf_bytes("Synthetic spec B")
    compared = []
    monkeypatch.setattr(web, "analyze", lambda docs, cfg: [])
    monkeypatch.setattr(web, "save_run", lambda names, issues: 38)
    monkeypatch.setattr(web, "check_spec_duplicates", lambda *args: [])
    monkeypatch.setattr(web, "crosscheck",
                        lambda *args: compared.append(args) or [])

    with TestClient(web.app) as client:
        response = client.post("/api/check", files=[
            ("files", ("alpha.Э3.pdf", a1, "application/pdf")),
            ("files", ("alpha.Э3.pdf", a2, "application/pdf")),
            ("files", ("alpha.ПЭ3.pdf", pdf_bytes("Synthetic spec A"), "application/pdf")),
            ("files", ("beta.Э3.pdf", b_scheme, "application/pdf")),
            ("files", ("beta.ПЭ3.pdf", b_spec, "application/pdf")),
        ])
    assert response.status_code == 200
    assert compared == [("beta.Э3.pdf", b_scheme, "beta.ПЭ3.pdf", b_spec)]
    assert [item["code"] for item in response.json()["issues"]] == [
        "KD-SET-102", "KD-SET-103"
    ]



def test_api_both_sides_ambiguous_keep_all_standalone_pe3_scans(monkeypatch):
    """Two distinct versions on each side must never create a guessed pairing."""
    def pdf_bytes(label):
        with fitz.open() as pdf:
            page = pdf.new_page()
            page.insert_text((72, 72), label)
            return pdf.tobytes()

    e1, e2 = pdf_bytes("Synthetic scheme A"), pdf_bytes("Synthetic scheme B")
    p1, p2 = pdf_bytes("Synthetic spec A"), pdf_bytes("Synthetic spec B")
    scanned, compared = [], []
    monkeypatch.setattr(web, "analyze", lambda docs, cfg: [])
    monkeypatch.setattr(web, "save_run", lambda names, issues: 39)
    monkeypatch.setattr(web, "check_spec_duplicates",
                        lambda name, data: scanned.append(data) or [])
    monkeypatch.setattr(web, "crosscheck",
                        lambda *args: compared.append(args) or [])

    with TestClient(web.app) as client:
        response = client.post("/api/check", files=[
            ("files", ("demo.Э3.pdf", e1, "application/pdf")),
            ("files", ("demo.Э3.pdf", e2, "application/pdf")),
            ("files", ("demo.ПЭ3.pdf", p1, "application/pdf")),
            ("files", ("demo.ПЭ3.pdf", p2, "application/pdf")),
        ])

    assert response.status_code == 200
    assert scanned == [p1, p2]
    assert compared == []
    assert len(response.json()["files"]) == 4
    codes = [issue["code"] for issue in response.json()["issues"]]
    assert codes.count("KD-SET-102") == 2
    assert codes.count("KD-SET-103") == 1
