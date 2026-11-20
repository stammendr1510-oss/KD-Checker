"""Synthetic evidence checks for offline legacy-to-scoped staging imports."""
from contextlib import closing
import sqlite3

import pytest

import storage
from scoped_history import Scope
from staging_import import stage_history
from quality.staging_import_preflight import verify_staging_import


SCOPE = Scope("org-synthetic", "project-synthetic")
ISSUE = dict(
    severity="info", code="SYN-001", file="synthetic.pdf",
    location="page 1", message="Synthetic only", recommendation="Review",
)


@pytest.fixture
def migrated(tmp_path, monkeypatch):
    legacy = tmp_path / "legacy.sqlite3"
    monkeypatch.setattr(storage, "DB", legacy)
    first = storage.save_run(["synthetic.pdf"], [ISSUE])
    finding = storage.get_run(first)["finding_states"][0]
    storage.update_finding(first, finding["id"], "resolved", "Synthetic approval", 0)
    storage.save_run(["synthetic.pdf"], [ISSUE])
    copy = tmp_path / "verified-copy.sqlite3"
    staging = tmp_path / "staging.sqlite3"
    stage_history(
        legacy, copy, staging, scope=SCOPE,
        revision="explicit-revision", operator="synthetic-operator",
    )
    return staging, copy, legacy


def mutate(path, statement, params=()):
    with closing(sqlite3.connect(path)) as conn:
        conn.execute(statement, params)
        conn.commit()


def issues(report):
    return {item["code"] for item in report["problems"]}


def test_complete_stage_passes_and_uses_no_sensitive_report_content(migrated):
    staging, backup, _ = migrated
    report = verify_staging_import(staging, verified_backup=backup)
    assert report["ok"] is True and report["problems"] == []
    assert report["backup_checked"] is True
    assert {key: report["counts"][key] for key in ("runs", "decisions", "events")} == {
        "runs": 2, "decisions": 2, "events": 1,
    }
    assert report["counts"]["archive_rows"] >= 8
    public = repr(report)
    for sensitive in ("org-synthetic", "project-synthetic", "synthetic-operator",
                      "Synthetic approval", "synthetic.pdf"):
        assert sensitive not in public


def test_stage_without_backup_is_structurally_checked_but_not_proven(migrated):
    staging, _, _ = migrated
    report = verify_staging_import(staging)
    assert report["ok"] is True
    assert report["backup_checked"] is False


@pytest.mark.parametrize("statement,code", [
    ("UPDATE import_manifest SET revision=''", "manifest"),
    ("UPDATE runs SET revision='spoof'", "run_identity"),
    ("UPDATE runs SET actor='synthetic-operator'", "run_identity"),
    ("UPDATE decisions SET version=77 WHERE ordinal=0", "audit_final"),
    ("DELETE FROM legacy_archive WHERE table_name='runs' AND ordinal=0", "archive_order"),
    ("UPDATE legacy_archive SET payload='not-json' WHERE table_name='runs'", "archive_payload"),
    ("UPDATE legacy_archive SET payload='{}' WHERE table_name='runs'", "run_cardinality"),
])
def test_tampered_stage_is_detected_without_modification(migrated, statement, code):
    staging, backup, _ = migrated
    mutate(staging, statement)
    report = verify_staging_import(staging, verified_backup=backup)
    assert not report["ok"] and code in issues(report)
    assert verify_staging_import(staging, verified_backup=backup) == report


def test_unexpected_archive_table_fails_closed(migrated):
    staging, backup, _ = migrated
    mutate(
        staging, "INSERT INTO legacy_archive VALUES(?,?,?)",
        ("made_up_table", 0, '{"synthetic":true}'),
    )
    report = verify_staging_import(staging, verified_backup=backup)
    assert report["ok"] is False
    assert "archive_table" in issues(report)


def test_mismatched_backup_hash_is_detected(migrated, tmp_path):
    staging, _, _ = migrated
    foreign = tmp_path / "unrelated-backup.sqlite3"
    with closing(sqlite3.connect(foreign)) as conn:
        conn.execute("CREATE TABLE synthetic(value)")
        conn.commit()
    report = verify_staging_import(staging, verified_backup=foreign)
    assert not report["ok"] and "backup_digest" in issues(report)


def test_missing_backup_does_not_create_any_file(migrated, tmp_path):
    staging, _, _ = migrated
    absent = tmp_path / "missing.sqlite3"
    with pytest.raises(FileNotFoundError):
        verify_staging_import(staging, verified_backup=absent)
    assert not absent.exists()


def test_missing_staging_is_never_created(tmp_path):
    absent = tmp_path / "missing.sqlite3"
    with pytest.raises(FileNotFoundError):
        verify_staging_import(absent)
    assert not absent.exists()


def test_verifier_leaves_original_and_staged_files_unchanged(migrated):
    staging, backup, legacy = migrated
    before_stage, before_backup, before_legacy = (
        staging.read_bytes(), backup.read_bytes(), legacy.read_bytes()
    )
    assert verify_staging_import(staging, verified_backup=backup)["ok"] is True
    assert staging.read_bytes() == before_stage
    assert backup.read_bytes() == before_backup
    assert legacy.read_bytes() == before_legacy


def test_changed_archive_row_is_detected_even_if_counts_are_intact(migrated):
    staging, backup, _ = migrated
    mutate(
        staging,
        "UPDATE legacy_archive SET payload='{}' "
        "WHERE table_name='finding_states' AND ordinal=0",
    )
    report = verify_staging_import(staging, verified_backup=backup)
    assert not report["ok"] and "archive_mismatch" in issues(report)
