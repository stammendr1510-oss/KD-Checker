"""Synthetic corruption fixtures for read-only project config audit verification."""
from contextlib import closing
import json
import sqlite3

import pytest

from project_configuration import DEFAULT
from quality.project_config_audit_verifier import inspect_project_config_history
from scoped_history import Repository, Scope
from secure_backend import ScopedBackend
from secure_sessions import Identity


A = Scope("synthetic-org", "project-a")
B = Scope("synthetic-org", "project-b")
ADMIN_A = Identity("admin-a", A.organization, A.project)
ADMIN_B = Identity("admin-b", B.organization, B.project)


@pytest.fixture
def scenario(tmp_path):
    repo = Repository.create(tmp_path / "synthetic-scoped.sqlite3")
    repo.provision("admin-a", A, "admin")
    repo.provision("admin-b", B, "admin")
    backend = ScopedBackend(repo)
    return repo, backend.project_configuration


def settings(threshold):
    return dict(DEFAULT, min_pdf_text_chars=threshold)


def change(configuration, identity, threshold, expected):
    return configuration.update(
        identity, {"settings": settings(threshold), "expected_version": expected}
    )


def corrupt(repo, sql, args=()):
    with repo.transaction(True) as db:
        db.execute(sql, args)


def codes(report):
    return {p["code"] for p in report["problems"]}


def test_empty_new_scoped_database_is_clean(scenario):
    repo, _ = scenario
    assert inspect_project_config_history(repo.path) == {
        "ok": True, "problems": [],
        "counts": {"projects_with_config": 0, "config_events": 0},
    }


def test_two_projects_with_independent_audit_chains_are_clean(scenario):
    repo, config = scenario
    assert change(config, ADMIN_A, 70, 0)[1] == 1
    assert change(config, ADMIN_A, 90, 1)[1] == 2
    assert change(config, ADMIN_B, 42, 0)[1] == 1
    clean = inspect_project_config_history(repo.path)
    assert clean == {
        "ok": True, "problems": [],
        "counts": {"projects_with_config": 2, "config_events": 3},
    }
    assert change(config, ADMIN_A, 90, 2)[1] == 2  # No-op is not audited.
    assert inspect_project_config_history(repo.path) == clean


@pytest.mark.parametrize("sql,expected", [
    (
        "UPDATE project_config_events SET old_payload='{}' "
        "WHERE project='project-a' AND new_version=2",
        "config_audit_chain",
    ),
    (
        "DELETE FROM project_config_events "
        "WHERE project='project-a' AND new_version=1",
        "config_audit_chain",
    ),
    (
        "UPDATE project_config SET payload='{}' WHERE project='project-a'",
        "config_final_state",
    ),
    (
        "UPDATE project_config SET version=99 WHERE project='project-a'",
        "config_final_state",
    ),
    (
        "UPDATE project_config_events SET actor='' "
        "WHERE project='project-a' AND new_version=1",
        "config_audit_actor",
    ),
])
def test_tampering_is_reported_without_repair(scenario, sql, expected):
    repo, config = scenario
    change(config, ADMIN_A, 70, 0)
    change(config, ADMIN_A, 90, 1)
    corrupt(repo, sql)
    first = inspect_project_config_history(repo.path)
    assert not first["ok"]
    assert expected in codes(first)
    assert inspect_project_config_history(repo.path) == first


def test_noop_audit_event_is_detected(scenario):
    repo, config = scenario
    change(config, ADMIN_A, 70, 0)
    payload = json.dumps(settings(70), ensure_ascii=False)
    corrupt(repo, """
        INSERT INTO project_config_events
          (org,project,actor,changed_at,old_version,new_version,old_payload,new_payload)
        VALUES (?,?,?,?,?,?,?,?)
    """, (A.organization, A.project, "admin-a", "synthetic timestamp", 1, 2, payload, payload))
    corrupt(repo, "UPDATE project_config SET version=2 WHERE project=?", (A.project,))
    assert "config_audit_noop" in codes(inspect_project_config_history(repo.path))


def test_audit_without_current_config_is_detected(scenario):
    repo, config = scenario
    change(config, ADMIN_A, 70, 0)
    corrupt(repo, "DELETE FROM project_config WHERE project=?", (A.project,))
    report = inspect_project_config_history(repo.path)
    assert not report["ok"] and "config_orphan_events" in codes(report)


def test_nonbaseline_configuration_without_audit_is_unverified(scenario):
    repo, _ = scenario
    corrupt(
        repo,
        "INSERT INTO project_config(org,project,payload,version) VALUES(?,?,?,?)",
        (A.organization, A.project, json.dumps(settings(99)), 0),
    )
    assert "unaudited_config" in codes(inspect_project_config_history(repo.path))


def test_unsupported_scoped_schema_is_rejected_without_modification(scenario):
    repo, _ = scenario
    corrupt(repo, "UPDATE schema_version SET version=999")
    report = inspect_project_config_history(repo.path)
    assert not report["ok"] and "schema" in codes(report)
    with closing(sqlite3.connect(repo.path)) as db:
        assert db.execute("SELECT version FROM schema_version").fetchone()[0] == 999


def test_readonly_inspector_never_creates_missing_file(tmp_path):
    missing = tmp_path / "missing.sqlite3"
    with pytest.raises(FileNotFoundError):
        inspect_project_config_history(missing)
    assert not missing.exists()


def test_readonly_inspector_does_not_change_verified_data(scenario):
    repo, config = scenario
    change(config, ADMIN_A, 72, 0)
    before = config.read(ADMIN_A), config.history(ADMIN_A)
    assert inspect_project_config_history(repo.path)["ok"]
    assert (config.read(ADMIN_A), config.history(ADMIN_A)) == before
