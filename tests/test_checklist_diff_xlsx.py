"""Offline synthetic XLSX revision audit tests, including injection hardening."""
from contextlib import closing
from io import BytesIO
from pathlib import Path

from fastapi.testclient import TestClient
from openpyxl import load_workbook
import pytest

import app as web
import storage
from checklist_comparison import compare_checklist_snapshots
from checklist_diff_xlsx import build_checklist_diff_xlsx


def snapshot(title="Исходный пункт", comment=""):
    return {
        "status": "verified", "revision": "a" * 64,
        "retrieved_at": "2026-11-14T10:00:00+00:00",
        "items": [{
            "id": 17, "section_id": 2, "section_title": "Схемы",
            "title": title, "comment": comment, "url": "",
            "tab": 1, "position": 2,
        }],
        "reviews": [],
    }


def test_xlsx_preserves_all_added_removed_fields_and_inert_text():
    before = snapshot(title='=HYPERLINK("https://example.invalid","click")', comment="=SUM(1)")
    after = snapshot(title="@danger", comment="+SUM(2)")
    after["items"][0]["id"] = 18
    diff = compare_checklist_snapshots(before, after)
    with closing(load_workbook(BytesIO(build_checklist_diff_xlsx(diff)))) as book:
        sheet = book["Изменения Checklist"]
        assert sheet.freeze_panes == "A2"
        assert sheet.auto_filter.ref == "A1:E15"
        records = [list(row) for row in sheet.iter_rows(min_row=2, values_only=True)]
        assert len(records) == 14
        removed = [r for r in records if r[0] == "Удалено"]
        added = [r for r in records if r[0] == "Добавлено"]
        assert ["Удалено", 17, "Формулировка", before["items"][0]["title"], ""] in removed
        assert ["Добавлено", 18, "Формулировка", "", "@danger"] in added
        for row in sheet.iter_rows(min_row=2):
            for cell in (row[0], row[2], row[3], row[4]):
                if cell.value is not None:
                    assert cell.data_type == "s"
                assert cell.hyperlink is None
        assert sheet["D4"].data_type == "s"


def test_xlsx_changed_fields_are_before_after():
    before = snapshot()
    after = snapshot(title="Другое название", comment="Новый комментарий")
    with closing(load_workbook(BytesIO(build_checklist_diff_xlsx(compare_checklist_snapshots(before, after))))) as book:
        sheet = book.active
        rows = list(sheet.iter_rows(min_row=2, values_only=True))
        assert rows == [
            ("Изменено", 17, "Формулировка", "Исходный пункт", "Другое название"),
            ("Изменено", 17, "Примечание", "", "Новый комментарий"),
        ]


def test_refuses_missing_verified_snapshot():
    old, new = snapshot(), snapshot()
    old["status"] = "unavailable"
    with pytest.raises(ValueError):
        build_checklist_diff_xlsx(compare_checklist_snapshots(old, new))


def test_xlsx_comparison_api_and_history_guards(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DB", tmp_path / "history.sqlite3")
    old = storage.save_run(["old.pdf"], [], checklist=snapshot())
    new = storage.save_run(["new.pdf"], [], checklist=snapshot(title="Новое"))
    with TestClient(web.app) as client:
        r = client.get(f"/api/runs/{new}/checklist-compare/{old}/xlsx")
        assert r.status_code == 200
        assert "spreadsheetml.sheet" in r.headers["content-type"]
        assert f"checklist-diff-{old}-{new}.xlsx" in r.headers["content-disposition"]
        with closing(load_workbook(BytesIO(r.content))) as book:
            assert book.active["E2"].value == "Новое"
        assert client.get(f"/api/runs/{old}/checklist-compare/{new}/xlsx").status_code == 400
        assert client.get(f"/api/runs/999/checklist-compare/{old}/xlsx").status_code == 404
        absent = storage.save_run(["legacy.pdf"], [])
        assert client.get(f"/api/runs/{absent}/checklist-compare/{new}/xlsx").status_code == 409


def test_ui_only_offers_excel_after_confirmed_comparison():
    html = (Path(__file__).resolve().parents[1] / "static" / "index.html").read_text("utf-8")
    assert "if(!result.available){container.textContent=result.reason;return;}" in html
    assert "'/checklist-compare/'+previous+'/xlsx" in html
