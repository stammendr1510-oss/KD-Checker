"""Offline acceptance smoke: local upload -> archived history -> exports -> comparison.

Only generated PDFs and isolated temporary SQLite. No service credentials,
production EPLAN Checklist traffic or real engineering documents.
"""
from contextlib import closing
from io import BytesIO

import fitz
from fastapi.testclient import TestClient
from openpyxl import load_workbook

import app as web
import storage

BASE = "ABCD.123456.001"


def synthetic_pdf():
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((72, 72), "SYNTHETIC ACCEPTANCE PDF")
        return doc.tobytes()


def test_offline_smoke_upload_history_decision_comparison_and_exports(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DB", tmp_path / "acceptance.sqlite3")
    monkeypatch.setattr(web, "analyze", lambda docs, cfg: [])
    monkeypatch.setattr(web, "current_checklist_state", lambda: {
        "status": "not_configured", "reason": "Synthetic offline mode",
        "revision": None, "retrieved_at": None, "items": [], "reviews": [],
    })
    files = [
        ("files", (f"{BASE}.ВО.pdf", synthetic_pdf(), "application/pdf")),
        ("files", (f"{BASE}.СП.pdf", synthetic_pdf(), "application/pdf")),
    ]
    with TestClient(web.app) as client:
        first_response = client.post("/api/check", files=files)
        assert first_response.status_code == 200
        first = first_response.json()
        assert first["checklist"]["status"] == "not_configured"
        assert [item["code"] for item in first["issues"]] == ["KD-SET-201", "KD-EM-000"]
        first_id = first["id"]
        history = client.get(f"/api/runs/{first_id}")
        assert history.status_code == 200
        saved = history.json()
        assert [issue["code"] for issue in saved["issues"]] == ["KD-SET-201", "KD-EM-000"]
        assert len(saved["finding_states"]) == 2
        state = saved["finding_states"][0]
        updated = client.patch(
            f"/api/runs/{first_id}/findings/{state['id']}",
            json={"status": "in_progress", "comment": "Synthetic engineer review",
                  "expected_version": 0},
        )
        assert updated.status_code == 200
        assert client.get(
            f"/api/runs/{first_id}/findings/{state['id']}/history"
        ).status_code == 200

        second_response = client.post("/api/check", files=files)
        assert second_response.status_code == 200
        second_id = second_response.json()["id"]
        assert second_id > first_id
        assert client.get(f"/api/runs/{second_id}").json()[
            "finding_states"
        ][0]["status"] == "new"
        comparison = client.get(f"/api/runs/{second_id}/compare/{first_id}")
        assert comparison.status_code == 200
        assert comparison.json()["summary"]["persisting"] == 2
        assert "finding_states" not in comparison.json()

        csv = client.get(f"/api/runs/{first_id}/csv")
        assert csv.status_code == 200
        assert "text/csv" in csv.headers["content-type"]
        assert b"KD-SET-201" in csv.content
        xlsx = client.get(f"/api/runs/{first_id}/xlsx")
        assert xlsx.status_code == 200
        with closing(load_workbook(BytesIO(xlsx.content))) as book:
            assert "Замечания" in book.sheetnames
            assert book["Замечания"]["C2"].value == "KD-SET-201"
            assert book["Замечания"]["I2"].value == "Synthetic engineer review"

        assert client.get(f"/api/runs/{first_id}").json()[
            "finding_states"
        ][0]["status"] == "in_progress"


def test_offline_smoke_broken_pdf_is_reported_without_claiming_valid_analysis(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DB", tmp_path / "broken.sqlite3")
    monkeypatch.setattr(web, "current_checklist_state", lambda: {
        "status": "not_configured", "reason": "Synthetic offline mode",
        "revision": None, "retrieved_at": None, "items": [], "reviews": [],
    })
    with TestClient(web.app) as client:
        r = client.post("/api/check", files=[
            ("files", (f"{BASE}.СП.pdf", b"not-a-pdf", "application/pdf"))
        ])
        assert r.status_code == 200
        data = r.json()
        assert any(issue["code"] == "KD-000" for issue in data["issues"])
        assert not any(issue["code"].startswith("KD-SET-2") for issue in data["issues"])
        assert client.get(f"/api/runs/{data['id']}").status_code == 200
