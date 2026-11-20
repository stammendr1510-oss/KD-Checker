"""Synthetic read-only integrity diagnostics; no production database access."""
from contextlib import closing
import sqlite3

import pytest

from quality.scoped_audit_verifier import inspect_scoped_history
from scoped_history import Repository, Scope


SCOPE = Scope("org-synthetic", "project-synthetic")
FINDING = dict(
    severity="info", code="SYN-001", file="synthetic.pdf",
    location="row 1", message="No real KD", recommendation="Review",
)


@pytest.fixture
def history(tmp_path):
    path = tmp_path / "scoped.sqlite3"
    repo = Repository.create(path)
    repo.provision("engineer", SCOPE, "engineer")
    rid = repo.save("engineer", SCOPE, ["synthetic.pdf"], [FINDING], revision="rev")
    return repo, rid


def mutate(repo, sql, params=()):
    with repo.transaction(True) as con:
        con.execute(sql, params)


def test_pristine_history_and_two_step_decision_pass(history):
    repo, rid = history
    base = inspect_scoped_history(repo.path)
    assert base == {
        "ok": True, "problems": [],
        "counts": {"runs": 1, "decisions": 1, "events": 0},
    }
    repo.update("engineer", SCOPE, rid, 0, "in_progress", "Investigate", 0)
    repo.update("engineer", SCOPE, rid, 0, "resolved", "Evidence", 1)
    result = inspect_scoped_history(repo.path)
    assert result["ok"] is True
    assert result["counts"] == {"runs": 1, "decisions": 1, "events": 2}


@pytest.mark.parametrize("sql,expected", [
    ("UPDATE events SET old_comment='injected' WHERE new_version=2", "audit_chain"),
    ("UPDATE decisions SET comment='forged' WHERE ordinal=0", "decision_state"),
    ("DELETE FROM events WHERE new_version=1", "audit_chain"),
    ("UPDATE decisions SET version=123 WHERE ordinal=0", "decision_state"),
    ("UPDATE events SET actor='' WHERE new_version=1", "audit_actor_time"),
])
def test_corrupted_audit_is_detected_without_repair(history, sql, expected):
    repo, rid = history
    repo.update("engineer", SCOPE, rid, 0, "in_progress", "Investigate", 0)
    repo.update("engineer", SCOPE, rid, 0, "resolved", "Evidence", 1)
    mutate(repo, sql)
    diagnostic = inspect_scoped_history(repo.path)
    assert diagnostic["ok"] is False
    assert expected in {item["code"] for item in diagnostic["problems"]}
    # The verifier must not modify the corruption that it detects.
    assert inspect_scoped_history(repo.path) == diagnostic


def test_unsupported_schema_fails_closed_without_rewriting(history):
    repo, _ = history
    mutate(repo, "UPDATE schema_version SET version=999")
    result = inspect_scoped_history(repo.path)
    assert result["ok"] is False
    assert result["problems"][0]["code"] == "schema"
    with closing(sqlite3.connect(repo.path)) as con:
        assert con.execute("SELECT version FROM schema_version").fetchone()[0] == 999


def test_cross_project_fk_violation_is_detected(history):
    repo, rid = history
    # Deliberately insert an invalid row using a separate offline connection.
    with closing(sqlite3.connect(repo.path)) as con:
        con.execute("PRAGMA foreign_keys=OFF")
        con.execute(
            "INSERT INTO decisions(org,project,run_id,ordinal,status,comment,version)"
            "VALUES(?,?,?,?,?,?,?)",
            ("other-org", "other-project", rid, 88, "new", "", 0),
        )
        con.commit()
    result = inspect_scoped_history(repo.path)
    assert result["ok"] is False
    assert "foreign_keys" in {x["code"] for x in result["problems"]}


def test_readonly_verifier_never_creates_missing_database(tmp_path):
    missing = tmp_path / "absent.sqlite3"
    with pytest.raises(FileNotFoundError):
        inspect_scoped_history(missing)
    assert not missing.exists()


def test_readonly_verifier_preserves_record_and_audit(history):
    repo, rid = history
    repo.update("engineer", SCOPE, rid, 0, "resolved", "Approved synthetically", 0)
    before = repo.get("engineer", SCOPE, rid)
    audit_before = repo.history("engineer", SCOPE, rid, 0)
    assert inspect_scoped_history(repo.path)["ok"]
    assert repo.get("engineer", SCOPE, rid) == before
    assert repo.history("engineer", SCOPE, rid, 0) == audit_before


def test_missing_finding_decision_detected_even_when_foreign_keys_pass(history):
    repo, rid = history
    mutate(repo, "DELETE FROM decisions WHERE run_id=?", (rid,))
    result = inspect_scoped_history(repo.path)
    assert result["ok"] is False
    assert "finding_cardinality" in {x["code"] for x in result["problems"]}


def test_invalid_run_payload_json_is_detected(history):
    repo, rid = history
    mutate(repo, "UPDATE runs SET issues='not-json' WHERE id=?", (rid,))
    result = inspect_scoped_history(repo.path)
    assert result["ok"] is False
    assert "run_payload" in {x["code"] for x in result["problems"]}


def test_previous_run_link_with_different_files_scope_is_detected(history):
    repo, first = history
    second = repo.save(
        "engineer", SCOPE, ["another-synthetic.pdf"], [FINDING], revision="rev"
    )
    mutate(repo, "UPDATE runs SET previous_id=? WHERE id=?", (first, second))
    result = inspect_scoped_history(repo.path)
    assert result["ok"] is False
    assert "previous_link" in {x["code"] for x in result["problems"]}


def test_future_previous_run_link_is_detected(history):
    repo, first = history
    second = repo.save(
        "engineer", SCOPE, ["synthetic.pdf"], [FINDING], revision="rev"
    )
    mutate(repo, "UPDATE runs SET previous_id=? WHERE id=?", (second, first))
    result = inspect_scoped_history(repo.path)
    assert result["ok"] is False
    assert "previous_link" in {x["code"] for x in result["problems"]}
