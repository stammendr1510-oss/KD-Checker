"""Synthetic adversarial acceptance for offline legacy-to-scoped staging migration.

No access to an enterprise filesystem, live SQLite, or production identities.
"""
from contextlib import closing
import hashlib
import json
import sqlite3

import pytest

import storage
from scoped_history import Scope
from staging_import import stage_history


SCOPE = Scope("synthetic-organization", "synthetic-project")
ISSUE = {
    "severity": "info", "code": "SYN-001", "file": "synthetic.pdf",
    "location": "page 1", "message": "Synthetic only",
    "recommendation": "Review",
}


@pytest.fixture
def legacy(tmp_path, monkeypatch):
    source = tmp_path / "legacy.sqlite3"
    monkeypatch.setattr(storage, "DB", source)
    first = storage.save_run(["synthetic.pdf"], [ISSUE])
    state = storage.get_run(first)["finding_states"][0]
    storage.update_finding(first, state["id"], "in_progress", "Initial review", 0)
    storage.update_finding(first, state["id"], "resolved", "Synthetic approval", 1)
    second = storage.save_run(["synthetic.pdf"], [ISSUE])
    return source, first, second


def stage(source, tmp_path, *, operator="synthetic-importer", revision="synthetic-rev"):
    return stage_history(
        source, tmp_path / "verified-copy.sqlite3", tmp_path / "new-stage.sqlite3",
        scope=SCOPE, revision=revision, operator=operator,
    )


def modify(path, sql, args=()):
    with closing(sqlite3.connect(path)) as db:
        db.execute(sql, args)
        db.commit()


@pytest.mark.parametrize("mutation", [
    "bad_files_json",
    "wrong_issues_shape",
    "missing_finding_state",
    "bad_event_version",
    "bad_event_status",
    "orphan_issue_scope",
])
def test_corrupt_source_never_creates_staging_or_mutates_original(legacy, tmp_path, mutation):
    source, first, _ = legacy
    if mutation == "bad_files_json":
        modify(source, "UPDATE runs SET filenames='not-json' WHERE id=?", (first,))
    elif mutation == "wrong_issues_shape":
        modify(source, "UPDATE runs SET issues='{}' WHERE id=?", (first,))
    elif mutation == "missing_finding_state":
        modify(source, "DELETE FROM finding_states WHERE run_id=?", (first,))
    elif mutation == "bad_event_version":
        modify(source, "UPDATE finding_events SET new_version=42 WHERE id=1")
    elif mutation == "bad_event_status":
        modify(source, "UPDATE finding_events SET old_status='resolved' WHERE id=1")
    else:
        modify(source, "DELETE FROM issue_runs WHERE run_id=?", (first,))
    original = source.read_bytes()
    with pytest.raises((ValueError, KeyError, TypeError, sqlite3.DatabaseError)):
        stage(source, tmp_path)
    assert source.read_bytes() == original
    assert not (tmp_path / "new-stage.sqlite3").exists()


@pytest.mark.parametrize("operator,revision", [
    ("", "synthetic-rev"), ("synthetic-importer", ""),
    ("synthetic-importer", " " * 3),
    ("x" * 201, "synthetic-rev"),
    ("synthetic-importer", "y" * 201),
])
def test_invalid_trusted_assignment_fails_before_creating_any_output(
    legacy, tmp_path, operator, revision,
):
    source, _, _ = legacy
    original = source.read_bytes()
    with pytest.raises(ValueError):
        stage(source, tmp_path, operator=operator, revision=revision)
    assert source.read_bytes() == original
    assert not (tmp_path / "verified-copy.sqlite3").exists()
    assert not (tmp_path / "new-stage.sqlite3").exists()


def test_manifest_matches_verified_copy_and_archives_optional_snapshot(legacy, tmp_path):
    source, first, second = legacy
    with closing(sqlite3.connect(source)) as db:
        db.execute("CREATE TABLE checklist_run_snapshots(run_id INTEGER, label TEXT)")
        db.execute(
            "INSERT INTO checklist_run_snapshots VALUES (?,?)",
            (first, "synthetic-untrusted-archive"),
        )
        db.commit()
    original = source.read_bytes()
    target = stage(source, tmp_path)
    copy = tmp_path / "verified-copy.sqlite3"
    assert source.read_bytes() == original
    with target.transaction() as db:
        manifest = db.execute(
            "SELECT source_sha256,org,project,revision,operator FROM import_manifest"
        ).fetchone()
        assert manifest[0] == hashlib.sha256(copy.read_bytes()).hexdigest()
        assert tuple(manifest)[1:] == (
            SCOPE.organization, SCOPE.project,
            "synthetic-rev", "synthetic-importer",
        )
        archive = [
            json.loads(row[0])
            for row in db.execute(
                "SELECT payload FROM legacy_archive "
                "WHERE table_name='checklist_run_snapshots' ORDER BY ordinal"
            )
        ]
        assert archive == [{
            "run_id": first,
            "label": "synthetic-untrusted-archive",
        }]
    assert target.get("synthetic-importer", SCOPE, second)["previous_id"] == first
    assert all(
        event["actor"] == "legacy_unknown"
        for event in target.history("synthetic-importer", SCOPE, first, 0)
    )


def test_existing_backup_and_stage_paths_remain_untouched(legacy, tmp_path):
    source, _, _ = legacy
    backup = tmp_path / "verified-copy.sqlite3"
    staging = tmp_path / "new-stage.sqlite3"
    backup.write_bytes(b"existing-backup")
    original = source.read_bytes()
    with pytest.raises(FileExistsError):
        stage(source, tmp_path)
    assert backup.read_bytes() == b"existing-backup"
    assert not staging.exists()
    backup.unlink()
    staging.write_bytes(b"existing-stage")
    with pytest.raises(FileExistsError):
        stage(source, tmp_path)
    assert staging.read_bytes() == b"existing-stage"
    assert source.read_bytes() == original


def test_staging_preserves_revisions_and_can_continue_audit(legacy, tmp_path):
    source, first, second = legacy
    target = stage(source, tmp_path)
    run = target.get("synthetic-importer", SCOPE, first)
    assert run["revision"] == "synthetic-rev"
    assert run["decisions"][0]["version"] == 2
    assert target.get("synthetic-importer", SCOPE, second)["previous_id"] == first
    assert target.update(
        "synthetic-importer", SCOPE, first, 0,
        "in_progress", "Post-import synthetic review", 2,
    ) == 3
    events = target.history("synthetic-importer", SCOPE, first, 0)
    assert [event["new_version"] for event in events] == [1, 2, 3]
    assert [event["actor"] for event in events] == [
        "legacy_unknown", "legacy_unknown", "synthetic-importer",
    ]
    with target.transaction() as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
