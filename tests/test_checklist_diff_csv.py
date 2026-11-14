"""Synthetic-only regression tests for archived checklist CSV audit export."""
import csv
from io import StringIO
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

import app as web
import storage
from checklist_comparison import compare_checklist_snapshots
from checklist_diff_csv import build_checklist_diff_csv


def snapshot(title="Схема", comment=""):
    return {
        "status": "verified", "revision": "e" * 64,
        "retrieved_at": "2026-11-14T00:00:00+00:00",
        "items": [{
            "id": 3, "section_id": 1, "section_title": "Раздел",
            "tab": 1, "position": 1, "title": title,
            "comment": comment, "url": "",
        }],
        "reviews": [],
    }


def parsed(data):
    assert data.startswith(b"\xef\xbb\xbf")
    return list(csv.reader(StringIO(data.decode("utf-8-sig")), delimiter=";"))


def test_deterministic_csv_lists_changes_and_escapes_formulas():
    before = snapshot(title="=HYPERLINK(\"https://invalid.example\",\"click\")")
    after = snapshot(title="+SUM(1,2)", comment="@unsafe()")
    after["items"].append({
        "id": 5, "section_id": 1, "section_title": "Раздел",
        "tab": 1, "position": 2, "title": "=1+2", "comment": "", "url": "",
    })
    diff = compare_checklist_snapshots(before, after)
    data = build_checklist_diff_csv(diff)
    assert data == build_checklist_diff_csv(diff)
    table = parsed(data)
    assert table[0] == ["Тип изменения", "ID требования", "Поле", "Было", "Стало"]
    assert ["Добавлено", "5", "Формулировка", "", "'=1+2"] in table
    assert ["Изменено", "3", "Формулировка", "'=HYPERLINK(\"https://invalid.example\",\"click\")", "'+SUM(1,2)"] in table
    assert ["Изменено", "3", "Примечание", "", "'@unsafe()"] in table


def test_removed_requirement_is_reported_from_previous_snapshot():
    before = snapshot()
    after = snapshot()
    after["items"] = []
    table = parsed(build_checklist_diff_csv(compare_checklist_snapshots(before, after)))
    assert table[1] == ["Удалено", "3", "Формулировка", "Схема", ""]


def test_unavailable_versions_cannot_be_exported():
    old, new = snapshot(), snapshot()
    old["status"] = "unavailable"
    with pytest.raises(ValueError):
        build_checklist_diff_csv(compare_checklist_snapshots(old, new))


def test_csv_api_reads_history_without_network(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DB", tmp_path / "history.sqlite3")
    previous = storage.save_run(["synthetic-old.pdf"], [], checklist=snapshot())
    latest = storage.save_run(["synthetic-new.pdf"], [], checklist=snapshot(title="Другая схема"))
    with TestClient(web.app) as client:
        response = client.get(f"/api/runs/{latest}/checklist-compare/{previous}/csv")
        assert response.status_code == 200
        assert "text/csv" in response.headers["content-type"]
        assert f"checklist-diff-{previous}-{latest}.csv" in response.headers["content-disposition"]
        assert ["Изменено", "3", "Формулировка", "Схема", "Другая схема"] in parsed(response.content)
        assert client.get(f"/api/runs/{previous}/checklist-compare/{latest}/csv").status_code == 400
        assert client.get(f"/api/runs/999/checklist-compare/{previous}/csv").status_code == 404


def test_csv_api_denies_unverified_history(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DB", tmp_path / "history.sqlite3")
    previous = storage.save_run(["synthetic-old.pdf"], [])
    latest = storage.save_run(["synthetic-new.pdf"], [], checklist=snapshot())
    with TestClient(web.app) as client:
        result = client.get(f"/api/runs/{latest}/checklist-compare/{previous}/csv")
    assert result.status_code == 409


def test_csv_link_only_renders_after_verified_comparison():
    html = (Path(__file__).resolve().parents[1] / "static" / "index.html").read_text("utf-8")
    assert "if(!result.available){container.textContent=result.reason;return;}" in html
    assert "'/checklist-compare/'+previous+'/csv" in html
