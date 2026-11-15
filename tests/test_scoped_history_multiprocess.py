"""Independent SQLite clients for scoped history, strictly synthetic.

This covers multiple local *processes*, not distributed sessions, multi-PC
operation, a network filesystem or PostgreSQL transaction semantics.
"""
import multiprocessing
from pathlib import Path
from queue import Empty

import pytest

from scoped_history import Conflict, Denied, Missing, Repository, Scope


SCOPE = Scope("synthetic-organization", "synthetic-project")
ISSUE = {
    "severity": "info", "code": "SYNTHETIC-1", "file": "synthetic.pdf",
    "location": "Synthetic record", "message": "No real documents",
    "recommendation": "Review synthetic record",
}


def _worker(path, ready, results, operation, run_id, label):
    """Top-level picklable worker for Windows multiprocessing spawn."""
    try:
        repository = Repository(path)
        ready.wait(timeout=20)
        if operation == "edit":
            version = repository.update(
                "engineer", SCOPE, run_id, 0, "in_progress", label, 0
            )
            results.put(("ok", version))
        elif operation == "save":
            identifier = repository.save(
                "engineer", SCOPE, ["synthetic.pdf"], [ISSUE], revision="rev-1"
            )
            results.put(("ok", identifier))
        elif operation == "foreign":
            repository.get("viewer", SCOPE, run_id)
            results.put(("ok", None))
        elif operation == "revoked":
            repository.get("engineer", SCOPE, run_id)
            results.put(("ok", None))
        else:
            results.put(("error", "unknown operation"))
    except Conflict:
        results.put(("conflict", None))
    except Denied:
        results.put(("denied", None))
    except Missing:
        results.put(("missing", None))
    except Exception as exc:
        results.put(("error", type(exc).__name__))


def _run_workers(path, operation, run_id, count=2, *, before_release=None):
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    results = context.Queue()
    processes = [
        context.Process(
            target=_worker,
            args=(str(path), start, results, operation, run_id, f"engineer-{i}"),
        )
        for i in range(count)
    ]
    try:
        for process in processes:
            process.start()
        if before_release is not None:
            before_release()
        start.set()
        outcomes = []
        for _ in processes:
            outcomes.append(results.get(timeout=45))
        for process in processes:
            process.join(timeout=15)
            assert process.exitcode == 0
        assert not any(status == "error" for status, _ in outcomes), outcomes
        return outcomes
    finally:
        start.set()
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(timeout=10)
        results.close()
        results.join_thread()


@pytest.fixture
def scoped_repository(tmp_path):
    path = tmp_path / "scoped-history.sqlite3"
    repository = Repository.create(path)
    repository.provision("engineer", SCOPE, "engineer")
    repository.provision("viewer", SCOPE, "viewer")
    return repository


def _save(repository):
    return repository.save(
        "engineer", SCOPE, ["synthetic.pdf"], [ISSUE], revision="rev-1"
    )


def test_two_processes_edit_same_version_one_audit_event(scoped_repository):
    repo = scoped_repository
    identifier = _save(repo)
    outcomes = _run_workers(repo.path, "edit", identifier)
    assert sorted(status for status, _ in outcomes) == ["conflict", "ok"]
    assert [value for status, value in outcomes if status == "ok"] == [1]
    state = repo.get("viewer", SCOPE, identifier)["decisions"][0]
    assert state["version"] == 1
    assert state["status"] == "in_progress"
    events = repo.history("viewer", SCOPE, identifier, 0)
    assert len(events) == 1
    assert (events[0]["old_version"], events[0]["new_version"]) == (0, 1)
    assert events[0]["new_comment"] == state["comment"]


def test_two_processes_create_serialized_previous_chain(scoped_repository):
    repo = scoped_repository
    original = _save(repo)
    outcomes = _run_workers(repo.path, "save", original)
    assert [status for status, _ in outcomes] == ["ok", "ok"]
    created = sorted(value for _, value in outcomes)
    assert original < created[0] < created[1]
    assert repo.list("viewer", SCOPE) == [created[1], created[0], original]
    assert repo.get("viewer", SCOPE, created[0])["previous_id"] == original
    assert repo.get("viewer", SCOPE, created[1])["previous_id"] == created[0]
    for identifier in created:
        run = repo.get("viewer", SCOPE, identifier)
        assert run["issues"] == [ISSUE]
        assert (run["decisions"][0]["status"], run["decisions"][0]["version"]) == ("new", 0)
    with repo.transaction() as con:
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert not con.execute("PRAGMA foreign_key_check").fetchall()


def test_revocation_visible_to_new_process_transactions(scoped_repository):
    repo = scoped_repository
    identifier = _save(repo)
    outcomes = _run_workers(
        repo.path, "revoked", identifier, count=1,
        before_release=lambda: repo.revoke("engineer", SCOPE),
    )
    assert outcomes == [("denied", None)]
    assert repo.get("viewer", SCOPE, identifier)["issues"] == [ISSUE]


def test_foreign_project_id_is_not_readable_by_a_separate_process(scoped_repository):
    repo = scoped_repository
    foreign = Scope("synthetic-organization", "other-project")
    repo.provision("engineer", foreign, "engineer")
    repo.provision("viewer", foreign, "viewer")
    foreign_run = repo.save(
        "engineer", foreign, ["synthetic.pdf"], [ISSUE], revision="rev-1"
    )
    assert repo.get("viewer", foreign, foreign_run)["issues"] == [ISSUE]
    assert _run_workers(repo.path, "foreign", foreign_run, count=1) == [
        ("missing", None)
    ]
