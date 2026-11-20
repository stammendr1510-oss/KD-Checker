"""Independent synthetic tests of scoped audit chains, isolation and restore.

Run against new local SQLite files only. No enterprise or Checklist data.
"""
from contextlib import closing
import sqlite3

import pytest

from scoped_history import Conflict, Denied, Missing, Repository, Scope


A = Scope("synthetic-org", "project-A")
B = Scope("synthetic-org", "project-B")
C = Scope("other-org", "project-A")
FINDING = dict(
    severity="info", code="SYN-001", file="synthetic.pdf",
    location="row 1", message="Synthetic only", recommendation="Review",
)


@pytest.fixture
def repo(tmp_path):
    result = Repository.create(tmp_path / "scoped.sqlite3")
    for scope in (A, B, C):
        result.provision("engineer", scope, "engineer")
        result.provision("viewer", scope, "viewer")
    return result


def save(repo, scope=A):
    return repo.save(
        "engineer", scope, ["synthetic.pdf"], [FINDING], revision="test-revision"
    )


def test_multi_step_audit_chain_has_exact_versions_and_actor(repo):
    rid = save(repo)
    v1 = repo.update("engineer", A, rid, 0, "in_progress", "Investigating", 0)
    v2 = repo.update("engineer", A, rid, 0, "resolved", "Verified synthetically", v1)
    assert (v1, v2) == (1, 2)
    history = repo.history("viewer", A, rid, 0)
    assert len(history) == 2
    assert [(x["old_version"], x["new_version"]) for x in history] == [(0, 1), (1, 2)]
    assert [(x["old_status"], x["new_status"]) for x in history] == [
        ("new", "in_progress"), ("in_progress", "resolved")
    ]
    assert history[0]["new_comment"] == history[1]["old_comment"]
    assert all(x["actor"] == "engineer" for x in history)
    state = repo.get("viewer", A, rid)["decisions"][0]
    assert (state["version"], state["status"], state["comment"]) == (
        2, "resolved", "Verified synthetically"
    )
    assert state["updated_at"] == history[-1]["changed_at"]
    with pytest.raises(Conflict):
        repo.update("engineer", A, rid, 0, "rejected", "Stale version", 1)
    assert repo.history("viewer", A, rid, 0) == history


def test_project_revocation_is_scoped_and_does_not_erase_other_memberships(repo):
    rid_a, rid_b, rid_c = save(repo, A), save(repo, B), save(repo, C)
    repo.revoke("engineer", A)
    with pytest.raises(Denied):
        repo.get("engineer", A, rid_a)
    with pytest.raises(Denied):
        repo.list("engineer", A)
    assert repo.get("engineer", B, rid_b)["id"] == rid_b
    assert repo.get("engineer", C, rid_c)["id"] == rid_c
    assert repo.get("viewer", A, rid_a)["id"] == rid_a
    with pytest.raises(Missing):
        repo.get("viewer", A, rid_b)
    with pytest.raises(Missing):
        repo.get("viewer", B, rid_c)


def test_scoped_backup_preserves_history_and_isolated_access(repo, tmp_path):
    first = save(repo, A)
    repo.update("engineer", A, first, 0, "resolved", "Stored decision", 0)
    second = save(repo, A)
    foreign = save(repo, B)
    before = {
        "record": repo.get("viewer", A, first),
        "history": repo.history("viewer", A, first, 0),
        "comparison": repo.compare("viewer", A, first, second),
        "foreign": repo.get("viewer", B, foreign),
    }

    restored_path = tmp_path / "restored.sqlite3"
    with closing(sqlite3.connect(repo.path)) as source:
        with closing(sqlite3.connect(restored_path)) as target:
            source.backup(target)
    restored = Repository(restored_path)
    assert restored.get("viewer", A, first) == before["record"]
    assert restored.history("viewer", A, first, 0) == before["history"]
    assert restored.compare("viewer", A, first, second) == before["comparison"]
    assert restored.get("viewer", B, foreign) == before["foreign"]
    with pytest.raises(Missing):
        restored.get("viewer", A, foreign)
    with restored.transaction() as con:
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []


def test_failed_cross_scope_audit_link_is_rolled_back(repo):
    rid_a, rid_b = save(repo, A), save(repo, B)
    repo.update("engineer", A, rid_a, 0, "in_progress", "A", 0)
    audit_before = repo.history("viewer", A, rid_a, 0)
    with pytest.raises(sqlite3.IntegrityError):
        with repo.transaction(True) as con:
            con.execute(
                "INSERT INTO events(org, project, run_id, ordinal, actor, changed_at,"
                "old_status, new_status, old_comment, new_comment, old_version, new_version)"
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (A.organization, A.project, rid_b, 0, "synthetic", "now",
                 "new", "resolved", "", "invalid", 0, 1),
            )
    assert repo.history("viewer", A, rid_a, 0) == audit_before
    with repo.transaction() as con:
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []
