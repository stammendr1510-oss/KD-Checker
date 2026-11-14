"""Synthetic tests for changes between archived checklist revisions."""
from copy import deepcopy

from fastapi.testclient import TestClient
import pytest

import app as web
import storage
from checklist_comparison import compare_checklist_snapshots


def snapshot(title="Проверить соединения", comment="", url="", item_id=10):
    return {
        "status": "verified", "revision": "1" * 64, "retrieved_at": "2026-11-14T10:00:00+00:00",
        "reason": "Актуальный список получен", "reviews": [],
        "items": [{
            "id": item_id, "section_id": 2, "section_title": "Раздел",
            "tab": 1, "position": 1, "title": title, "comment": comment, "url": url,
        }],
    }


def test_add_remove_and_modify_requirements_without_mutating_inputs():
    previous = snapshot()
    current = snapshot(comment="Новое пояснение", url="https://example.invalid/info")
    current["items"].append({
        "id": 11, "section_id": 2, "section_title": "Раздел",
        "tab": 1, "position": 2, "title": "Дополнительный пункт",
        "comment": "", "url": "",
    })
    old_copy, new_copy = deepcopy(previous), deepcopy(current)
    outcome = compare_checklist_snapshots(previous, current)
    assert outcome["available"] is True
    assert outcome["summary"] == {"added": 1, "removed": 0, "changed": 1}
    assert outcome["added"][0]["id"] == 11
    assert outcome["changed"][0]["item_id"] == 10
    assert outcome["changed"][0]["fields"] == ["comment", "url"]
    assert previous == old_copy and current == new_copy


def test_deleted_item_and_changed_title():
    first = snapshot()
    first["items"].append(deepcopy(snapshot(item_id=12)["items"][0]))
    second = snapshot(title="Другая формулировка")
    outcome = compare_checklist_snapshots(first, second)
    assert outcome["summary"] == {"added": 0, "removed": 1, "changed": 1}
    assert outcome["removed"][0]["id"] == 12
    assert outcome["changed"][0]["fields"] == ["title"]


@pytest.mark.parametrize("old_status,new_status", [
    ("unavailable", "verified"),
    ("verified", "not_configured"),
    ("not_configured", "unavailable"),
])
def test_offline_or_unconfigured_must_not_fake_revision_comparison(old_status, new_status):
    old, new = snapshot(), snapshot()
    old["status"], new["status"] = old_status, new_status
    result = compare_checklist_snapshots(old, new)
    assert result["available"] is False
    assert result["summary"] == {"added": 0, "removed": 0, "changed": 0}


def test_change_order_is_detected_even_when_title_is_unchanged():
    old, new = snapshot(), snapshot()
    new["items"][0]["position"] = 4
    result = compare_checklist_snapshots(old, new)
    assert result["changed"][0]["fields"] == ["position"]


def test_api_compares_immutable_saved_snapshots(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DB", tmp_path / "history.sqlite3")
    previous = storage.save_run(["old.pdf"], [], checklist=snapshot())
    latest = storage.save_run(["new.pdf"], [], checklist=snapshot(title="Новое название"))
    with TestClient(web.app) as client:
        response = client.get(f"/api/runs/{latest}/checklist-compare/{previous}")
        assert response.status_code == 200
        result = response.json()
        assert result["previous_run_id"] == previous
        assert result["current_run_id"] == latest
        assert result["changed"][0]["fields"] == ["title"]
        assert client.get(f"/api/runs/{previous}/checklist-compare/{latest}").status_code == 400
        assert client.get(f"/api/runs/999/checklist-compare/{previous}").status_code == 404


def test_api_cannot_compare_without_verified_source(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DB", tmp_path / "history.sqlite3")
    previous = storage.save_run(["old.pdf"], [])
    latest = storage.save_run(["new.pdf"], [], checklist=snapshot())
    with TestClient(web.app) as client:
        response = client.get(f"/api/runs/{latest}/checklist-compare/{previous}")
    assert response.status_code == 200
    assert response.json()["available"] is False


def test_history_ui_contains_safe_checklist_comparison_controls():
    from pathlib import Path
    html = (Path(__file__).resolve().parents[1] / "static" / "index.html").read_text("utf-8")
    assert "function showChecklistComparison(runId)" in html
    assert "/checklist-compare/" in html
    assert "row.fields.map(esc)" in html
    assert "esc(row.current.title)" in html
