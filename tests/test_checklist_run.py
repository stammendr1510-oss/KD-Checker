"""Synthetic checklist snapshots and local HTTP API regression coverage."""
import fitz
from fastapi.testclient import TestClient

import app as web
import checklist_run
import storage


def sample_state(label="Проверить листность"):
    return {
        "status": "verified",
        "reason": "Актуальные пункты получены",
        "revision": "a" * 64,
        "retrieved_at": "2026-11-14T10:00:00+00:00",
        "items": [
            {"id": 41, "section_id": 4, "section_title": "Документы",
             "title": label, "tab": 2, "position": 1}
        ],
        "reviews": [
            {"item_id": 41, "title": label, "section_title": "Документы",
             "status": "manual_review",
             "explanation": "Правило ещё не реализовано"}
        ],
    }


def pdf_bytes():
    with fitz.open() as pdf:
        page = pdf.new_page()
        page.insert_text((72, 72), "Synthetic PDF test")
        return pdf.tobytes()


def test_unconfigured_mode_requires_no_network(monkeypatch):
    monkeypatch.delenv("KD_CHECKLIST_FEED_URL", raising=False)
    monkeypatch.delenv("KD_CHECKLIST_FEED_TOKEN", raising=False)
    monkeypatch.setattr(checklist_run, "fetch_current_checklist",
                        lambda **kw: (_ for _ in ()).throw(AssertionError("network")))
    state = checklist_run.current_checklist_state()
    assert state["status"] == "not_configured"
    assert state["revision"] is None


def test_configured_mode_refetches_each_time(monkeypatch):
    monkeypatch.setenv("KD_CHECKLIST_FEED_URL", "https://example.org/checklist")
    monkeypatch.setenv("KD_CHECKLIST_FEED_TOKEN", "fake-token")
    calls = []
    def source(**kw):
        calls.append(kw)
        item = sample_state(f"Пункт {len(calls)}")
        return {"items": item["items"], "revision": str(len(calls)),
                "retrieved_at": item["retrieved_at"]}
    monkeypatch.setattr(checklist_run, "fetch_current_checklist", source)
    old = checklist_run.current_checklist_state()
    new = checklist_run.current_checklist_state()
    assert len(calls) == 2
    assert old["items"][0]["title"] != new["items"][0]["title"]
    assert old["reviews"][0]["status"] == "manual_review"


def test_unavailable_configuration_never_looks_verified(monkeypatch):
    monkeypatch.setenv("KD_CHECKLIST_FEED_URL", "https://example.org/checklist")
    monkeypatch.setenv("KD_CHECKLIST_FEED_TOKEN", "fake-token")
    def offline(**kw):
        raise checklist_run.ChecklistUnavailable("network error: secret")
    monkeypatch.setattr(checklist_run, "fetch_current_checklist", offline)
    state = checklist_run.current_checklist_state()
    assert state["status"] == "unavailable"
    assert state["items"] == []
    assert "secret" not in state["reason"]


def test_sqlite_retains_independent_snapshots_and_old_checks(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DB", tmp_path / "history.sqlite3")
    first = storage.save_run(["one.pdf"], [], checklist=sample_state())
    second = storage.save_run(["one.pdf"], [],
                              checklist=sample_state("Другая редакция требования"))
    third = storage.save_run(["one.pdf"], [])
    assert storage.get_run(first)["checklist"]["items"][0]["title"] == "Проверить листность"
    assert storage.get_run(second)["checklist"]["items"][0]["title"] == "Другая редакция требования"
    assert storage.get_run(third)["checklist"]["status"] == "not_configured"
    assert storage.get_run(first)["finding_states"] == []


def test_api_check_persists_verified_then_unavailable_without_stale_reuse(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DB", tmp_path / "history.sqlite3")
    monkeypatch.setattr(web, "analyze", lambda docs, cfg: [])
    monkeypatch.setattr(web, "check_spec_duplicates", lambda *a: [])
    monkeypatch.setattr(web, "crosscheck", lambda *a: [])
    monkeypatch.setattr(web, "current_checklist_state", lambda: sample_state())
    blob = pdf_bytes()
    with TestClient(web.app) as client:
        response = client.post("/api/check", files=[
            ("files", ("synthetic.Э3.pdf", blob, "application/pdf"))
        ])
        assert response.status_code == 200
        first = response.json()
        assert first["checklist"]["status"] == "verified"
        old = client.get(f"/api/runs/{first['id']}").json()
        assert old["checklist"]["items"][0]["title"] == "Проверить листность"

        monkeypatch.setattr(web, "current_checklist_state", lambda: {
            "status": "unavailable",
            "reason": "Источник недоступен", "revision": None,
            "retrieved_at": None, "items": [], "reviews": [],
        })
        new_response = client.post("/api/check", files=[
            ("files", ("synthetic.Э3.pdf", blob, "application/pdf"))
        ])
        assert new_response.status_code == 200
        latest = client.get(f"/api/runs/{new_response.json()['id']}").json()
        assert latest["checklist"]["status"] == "unavailable"
        assert latest["checklist"]["items"] == []
        assert client.get(f"/api/runs/{first['id']}").json()["checklist"] == old["checklist"]


def test_ui_escapes_checklist_titles():
    from pathlib import Path
    html = (Path(__file__).resolve().parents[1] / "static" / "index.html").read_text("utf-8")
    assert "function checklistHtml(checklist)" in html
    assert "esc(item.title)" in html
    assert "esc(group.title)" in html
    assert "esc(value.reason" in html
    assert "checklistHtml(saved.checklist||r.checklist)" in html
    assert "checklistHtml(r.checklist)" in html



def test_api_persists_unconfigured_state_without_claiming_verified(tmp_path, monkeypatch):
    """A missing feed must remain explicitly unverified in API and history."""
    monkeypatch.setattr(storage, "DB", tmp_path / "history.sqlite3")
    monkeypatch.delenv("KD_CHECKLIST_FEED_URL", raising=False)
    monkeypatch.delenv("KD_CHECKLIST_FEED_TOKEN", raising=False)
    monkeypatch.setattr(web, "analyze", lambda docs, cfg: [])
    monkeypatch.setattr(web, "check_spec_duplicates", lambda *a: [])
    monkeypatch.setattr(web, "crosscheck", lambda *a: [])
    monkeypatch.setattr(checklist_run, "fetch_current_checklist",
                        lambda **kw: (_ for _ in ()).throw(AssertionError("unexpected network")))
    blob = pdf_bytes()
    with TestClient(web.app) as client:
        response = client.post("/api/check", files=[
            ("files", ("synthetic.Э3.pdf", blob, "application/pdf"))
        ])
        assert response.status_code == 200
        result = response.json()
        assert result["checklist"]["status"] == "not_configured"
        assert result["checklist"]["items"] == []
        historical = client.get(f"/api/runs/{result['id']}").json()
        assert historical["checklist"]["status"] == "not_configured"
        assert historical["checklist"]["retrieved_at"] is None


def test_api_persists_feed_failure_without_exposing_secret(tmp_path, monkeypatch):
    """An upstream failure must not leak authentication details into history."""
    monkeypatch.setattr(storage, "DB", tmp_path / "history.sqlite3")
    monkeypatch.setenv("KD_CHECKLIST_FEED_URL", "https://example.org/checklist")
    monkeypatch.setenv("KD_CHECKLIST_FEED_TOKEN", "synthetic-secret-123")
    monkeypatch.setattr(web, "analyze", lambda docs, cfg: [])
    monkeypatch.setattr(web, "check_spec_duplicates", lambda *a: [])
    monkeypatch.setattr(web, "crosscheck", lambda *a: [])
    def failed(**kwargs):
        raise checklist_run.ChecklistUnavailable("synthetic-secret-123")
    monkeypatch.setattr(checklist_run, "fetch_current_checklist", failed)
    with TestClient(web.app) as client:
        response = client.post("/api/check", files=[
            ("files", ("synthetic.Э3.pdf", pdf_bytes(), "application/pdf"))
        ])
        assert response.status_code == 200
        result = response.json()
        assert result["checklist"]["status"] == "unavailable"
        assert "synthetic-secret-123" not in response.text
        history = client.get(f"/api/runs/{result['id']}")
        assert history.status_code == 200
        assert history.json()["checklist"]["status"] == "unavailable"
        assert "synthetic-secret-123" not in history.text


def test_history_list_surfaces_verified_and_unavailable_states(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DB", tmp_path / "history.sqlite3")
    verified = sample_state()
    first = storage.save_run(["a.pdf"], [], checklist=verified)
    second = storage.save_run(["b.pdf"], [], checklist={
        "status": "unavailable", "reason": "Источник недоступен",
        "revision": None, "retrieved_at": None, "items": [], "reviews": [],
    })
    third = storage.save_run(["c.pdf"], [])
    rows = {r["id"]: r for r in storage.list_runs()}
    assert rows[first]["checklist_status"] == "verified"
    assert rows[first]["checklist_revision"] == verified["revision"]
    assert rows[second]["checklist_status"] == "unavailable"
    assert rows[second]["checklist_revision"] is None
    assert rows[third]["checklist_status"] == "not_configured"


def test_legacy_history_without_checklist_table(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DB", tmp_path / "history.sqlite3")
    run_id = storage.save_run(["legacy.pdf"], [])
    import sqlite3
    with sqlite3.connect(storage.DB) as con:
        assert con.execute(
            "SELECT name FROM sqlite_master WHERE name='checklist_run_snapshots'"
        ).fetchone() is None
    rows = storage.list_runs()
    assert rows[0]["id"] == run_id
    assert rows[0]["checklist_status"] == "not_configured"


def test_ui_history_has_checklist_verification_column():
    from pathlib import Path
    html = (Path(__file__).resolve().parents[1] / "static" / "index.html").read_text("utf-8")
    assert "<th>EPLAN Checklist</th>" in html
    assert "r.checklist_status==='verified'" in html
    assert "r.checklist_status==='unavailable'" in html


def test_xlsx_exports_historical_checklist_with_safe_literal_text(tmp_path, monkeypatch):
    """The Excel report must never refresh the checklist or execute a title."""
    from contextlib import closing
    from io import BytesIO
    from openpyxl import load_workbook
    from excel_reports import build_excel_report
    monkeypatch.setattr(storage, "DB", tmp_path / "history.sqlite3")
    source = sample_state("=HYPERLINK(\"https://invalid.example\",\"click\")")
    run_id = storage.save_run(["test.pdf"], [], checklist=source)
    saved = storage.get_run(run_id)
    with closing(load_workbook(BytesIO(build_excel_report(saved)))) as book:
        sheet = book["EPLAN Checklist"]
        assert sheet["B2"].value == "Актуальный список получен на момент проверки"
        assert sheet["B4"].value == source["revision"]
        assert sheet["A7"].value == "ID пункта"
        assert sheet["A8"].value == 41
        assert sheet["C8"].value == source["reviews"][0]["title"]
        assert sheet["C8"].data_type == "s"
        assert sheet["C8"].hyperlink is None
        assert sheet["D8"].value == "Требует проверки инженером"
        assert sheet.freeze_panes == "A2"


def test_xlsx_does_not_claim_outage_was_verified(tmp_path, monkeypatch):
    from contextlib import closing
    from io import BytesIO
    from openpyxl import load_workbook
    from excel_reports import build_excel_report
    monkeypatch.setattr(storage, "DB", tmp_path / "history.sqlite3")
    run_id = storage.save_run(["test.pdf"], [], checklist={
        "status": "unavailable", "reason": "Невозможно подключиться",
        "revision": None, "retrieved_at": None, "items": [], "reviews": [],
    })
    with closing(load_workbook(BytesIO(build_excel_report(storage.get_run(run_id))))) as book:
        sheet = book["EPLAN Checklist"]
        assert "не подтверждена" in sheet["B2"].value
        assert sheet["B3"].value == "Невозможно подключиться"
        assert sheet.max_row == 7
        assert sheet["A8"].value is None


def test_ui_escapes_optional_checklist_comments_and_sources():
    from pathlib import Path
    html = (Path(__file__).resolve().parents[1] / "static" / "index.html").read_text("utf-8")
    assert "esc(item.comment)" in html
    assert "esc(item.url)" in html
    assert "href=\"'+item.url" not in html


def test_xlsx_preserves_comments_and_urls_as_plain_text(tmp_path, monkeypatch):
    from io import BytesIO
    from contextlib import closing
    from openpyxl import load_workbook
    from excel_reports import build_excel_report
    monkeypatch.setattr(storage, "DB", tmp_path / "history.sqlite3")
    source = sample_state()
    source["reviews"][0]["comment"] = "=2+2"
    source["reviews"][0]["url"] = "javascript:alert(1)"
    run_id = storage.save_run(["synthetic.pdf"], [], checklist=source)
    with closing(load_workbook(BytesIO(build_excel_report(storage.get_run(run_id))))) as book:
        sheet = book["EPLAN Checklist"]
        assert sheet["F8"].value == "=2+2"
        assert sheet["F8"].data_type == "s"
        assert sheet["G8"].value == "javascript:alert(1)"
        assert sheet["G8"].hyperlink is None


def test_checklist_section_search_is_local_and_does_not_mutate_history():
    from pathlib import Path
    html = (Path(__file__).resolve().parents[1] / "static" / "index.html").read_text("utf-8")
    assert "const groups=new Map()" in html
    assert "String(item.tab)+'/'+String(item.section_id)" in html
    assert "class=\"checklist-filter\"" in html
    assert "event.target.closest('.checklist-view')" in html
    assert "section.hidden=visible===0" in html
    assert "item.hidden=!match" in html
    assert "item.textContent.toLocaleLowerCase('ru-RU')" in html
    assert "esc(group.title)" in html
    assert "esc(item.title)" in html
