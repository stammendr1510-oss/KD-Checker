"""Synthetic CSV boundary regressions; no external services or enterprise files."""
import csv

import pytest
from fastapi.testclient import TestClient

import app as web
from extractors import extract
import storage


def oversized_tail():
    # A valid prefix followed by a field that would fail if parsed. Keep the
    # process-global parser limit unchanged (other requests can run concurrently).
    return ("value\n" * 5000 + "x" * (csv.field_size_limit() + 1)).encode()


def test_csv_does_not_parse_records_beyond_limit():
    document = extract("synthetic.csv", oversized_tail())
    assert document.sheets == [{"name": "CSV", "rows": [["value"]] * 5000}]


@pytest.mark.parametrize("delimiter", [";", ",", "\t"])
def test_csv_limit_counts_logical_records_and_preserves_quoted_newlines(delimiter):
    content = (f'"first\nsecond"{delimiter}tail\r\n' * 5001).encode("utf-8-sig")
    rows = extract("synthetic.csv", content).sheets[0]["rows"]
    assert len(rows) == 5000
    assert rows[0] == rows[-1] == ["first\nsecond", "tail"]


@pytest.mark.parametrize("count", [0, 1, 4999, 5000, 5001])
def test_csv_record_and_column_boundaries(count):
    content = ((";".join(str(i) for i in range(41)) + "\n") * count).encode()
    rows = extract("synthetic.csv", content).sheets[0]["rows"]
    assert len(rows) == min(count, 5000)
    assert all(row == [str(i) for i in range(40)] for row in rows)


def test_csv_parser_error_within_limit_is_not_hidden():
    with pytest.raises(csv.Error):
        extract("synthetic.csv", b"x" * (csv.field_size_limit() + 1))


def test_bounded_csv_upload_is_saved_as_readable_document(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DB", tmp_path / "history.sqlite3")
    monkeypatch.setattr(web, "current_checklist_state", lambda: {
        "status": "not_configured", "reason": "Synthetic offline mode",
        "revision": None, "retrieved_at": None, "items": [], "reviews": [],
    })
    with TestClient(web.app) as client:
        response = client.post("/api/check", files={
            "files": ("synthetic.csv", oversized_tail(), "text/csv"),
        })
        assert response.status_code == 200
        result = response.json()
        assert [item["name"] for item in result["files"]] == ["synthetic.csv"]
        assert "KD-000" not in [item["code"] for item in result["issues"]]
        saved = client.get(f"/api/runs/{result['id']}").json()
        assert saved["files"] == ["synthetic.csv"]
        assert saved["issues"] == result["issues"]
