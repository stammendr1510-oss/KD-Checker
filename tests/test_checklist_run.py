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
    assert "esc(item.section_title)" in html
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
