"""No private engineering documents: synthetic finding records only."""
import pytest
from fastapi.testclient import TestClient

import app as web
from run_comparison import compare_runs, IncompatibleRuns


def issue(code, message="Test finding", file="panel.Э3.pdf"):
    return {
        "severity": "warning", "code": code, "file": file,
        "location": "Лист 1", "message": message,
        "recommendation": "Проверить",
    }


def run(run_id, issues, files=None):
    return {
        "id": run_id, "files": files or ["panel.Э3.pdf", "panel.ПЭ3.pdf"],
        "issues": issues,
    }


def test_compare_new_persisting_disappeared_in_source_order():
    old = run(1, [issue("KD-OLD"), issue("KD-SAME")])
    new = run(2, [issue("KD-SAME"), issue("KD-NEW")])
    result = compare_runs(old, new)
    assert result["summary"] == {
        "new": 1, "persisting": 1, "disappeared": 1, "ambiguous": 0
    }
    assert result["new"][0]["issue"]["code"] == "KD-NEW"
    assert result["persisting"][0]["previous"]["issue_index"] == 1
    assert result["persisting"][0]["current"]["issue_index"] == 0
    assert result["disappeared"][0]["issue"]["code"] == "KD-OLD"
    assert "не подтверждает исправление" in result["notice"]


def test_same_code_with_different_evidence_does_not_auto_match():
    result = compare_runs(
        run(1, [issue("KD-PE-102", "7 of 9")]),
        run(2, [issue("KD-PE-102", "8 of 9")]),
    )
    assert result["summary"] == {
        "new": 1, "persisting": 0, "disappeared": 1, "ambiguous": 0
    }


def test_duplicated_fingerprint_stays_ambiguous_even_if_counts_match():
    duplicated = issue("KD-DUP")
    result = compare_runs(
        run(1, [duplicated, duplicated]),
        run(2, [duplicated, duplicated]),
    )
    assert result["summary"] == {
        "new": 0, "persisting": 0, "disappeared": 0, "ambiguous": 1
    }
    assert len(result["ambiguous"][0]["previous"]) == 2
    assert len(result["ambiguous"][0]["current"]) == 2


def test_duplicated_fingerprint_after_single_finding_is_ambiguous():
    a = issue("KD-DUP")
    result = compare_runs(run(1, [a]), run(2, [a, a]))
    assert result["summary"]["ambiguous"] == 1
    assert result["summary"]["persisting"] == 0


def test_new_duplicates_with_no_prior_match_count_as_new():
    a = issue("KD-DUP")
    result = compare_runs(run(1, []), run(2, [a, a]))
    assert result["summary"]["new"] == 2
    assert result["summary"]["ambiguous"] == 0


def test_normalized_filenames_allow_case_differences_and_order():
    a = issue("KD-X")
    result = compare_runs(
        run(1, [a], ["one.Э3.pdf", "two.ПЭ3.pdf"]),
        run(2, [a], ["TWO.ПЭ3.pdf", "ONE.Э3.PDF"]),
    )
    assert result["summary"]["persisting"] == 1


def test_different_file_lists_are_refused():
    with pytest.raises(IncompatibleRuns):
        compare_runs(
            run(1, [issue("KD-X")], ["one.Э3.pdf"]),
            run(2, [issue("KD-X")], ["other.Э3.pdf"]),
        )


def test_old_and_new_run_order_must_be_forward():
    with pytest.raises(ValueError):
        compare_runs(run(3, []), run(3, []))


def test_comparison_is_read_only_and_has_no_status_transfer(monkeypatch):
    prior = run(1, [issue("KD-OLD")])
    current = run(2, [issue("KD-NEW")])
    prior["finding_states"] = [{"status": "resolved", "comment": "Reviewed"}]
    current["finding_states"] = [{"status": "new", "comment": ""}]
    monkeypatch.setattr(web, "get_run", lambda rid: {1: prior, 2: current}.get(rid))
    with TestClient(web.app) as client:
        response = client.get("/api/runs/2/compare/1")
    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["new"] == 1
    assert body["summary"]["disappeared"] == 1
    assert "finding_states" not in body
    assert current["finding_states"][0]["status"] == "new"


def test_comparison_endpoint_refuses_different_compositions(monkeypatch):
    previous = run(1, [], ["first.Э3.pdf"])
    current = run(2, [], ["second.Э3.pdf"])
    monkeypatch.setattr(web, "get_run", lambda rid: {1: previous, 2: current}.get(rid))
    with TestClient(web.app) as client:
        assert client.get("/api/runs/2/compare/1").status_code == 409
        assert client.get("/api/runs/2/compare/2").status_code == 400
        assert client.get("/api/runs/2/compare/999").status_code == 400
        assert client.get("/api/runs/3/compare/1").status_code == 404
