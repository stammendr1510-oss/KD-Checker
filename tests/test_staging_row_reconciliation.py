"""Row-level staging migration reconciliation on synthetic local SQLite only."""
from contextlib import closing
import sqlite3

import pytest

import storage
from scoped_history import Scope
from staging_import import stage_history
from quality.staging_row_reconciliation import reconcile_staging_rows


SCOPE = Scope("synthetic-org", "synthetic-project")
ISSUE = dict(
    severity="info", code="SYN-001", file="synthetic.pdf",
    location="page 1", message="synthetic message", recommendation="Review",
)


@pytest.fixture
def migrated(tmp_path, monkeypatch):
    legacy = tmp_path / "legacy.sqlite3"
    monkeypatch.setattr(storage, "DB", legacy)
    first = storage.save_run(["synthetic.pdf"], [ISSUE])
    fid = storage.get_run(first)["finding_states"][0]["id"]
    storage.update_finding(first, fid, "in_progress", "First review", 0)
    storage.update_finding(first, fid, "resolved", "Synthetic approval", 1)
    second = storage.save_run(["synthetic.pdf"], [ISSUE])
    backup = tmp_path / "copy.sqlite3"
    stage = tmp_path / "staging.sqlite3"
    stage_history(
        legacy, backup, stage, scope=SCOPE,
        revision="synthetic-revision", operator="synthetic-operator",
    )
    return stage, backup, legacy, first, second


def change(path, sql, params=()):
    with closing(sqlite3.connect(path)) as db:
        db.execute(sql, params)
        db.commit()


def problem_codes(report):
    return {r["code"] for r in report["problems"]}


def test_all_staged_rows_match_the_verified_source(migrated):
    stage, backup, legacy, first, second = migrated
    before = (stage.read_bytes(), backup.read_bytes(), legacy.read_bytes())
    report = reconcile_staging_rows(stage, verified_backup=backup)
    assert report["ok"] and report["reconciled"] and report["backup_checked"]
    assert report["problems"] == []
    assert report["counts"]["runs"] == 2
    assert (stage.read_bytes(), backup.read_bytes(), legacy.read_bytes()) == before


def test_without_backup_only_internal_consistency_can_be_attested(migrated):
    stage, _, _, _, _ = migrated
    report = reconcile_staging_rows(stage)
    assert report["ok"] and report["reconciled"]
    assert report["backup_checked"] is False


@pytest.mark.parametrize("sql,expected", [
    ("UPDATE runs SET created_at='forged date' WHERE id=1", "run_mapping"),
    ("UPDATE runs SET files='[]' WHERE id=1", "run_mapping"),
    ("UPDATE runs SET issues='[]' WHERE id=1", "finding_cardinality"),
    ("UPDATE runs SET previous_id=NULL WHERE id=2", "run_mapping"),
    ("UPDATE runs SET scope='wrong' WHERE id=1", "run_mapping"),
    ("UPDATE events SET id=id+1000 WHERE id=1", "event_mapping"),
])
def test_mapped_values_and_event_ids_cannot_silently_diverge(migrated, sql, expected):
    stage, backup, _, _, _ = migrated
    change(stage, sql)
    report = reconcile_staging_rows(stage, verified_backup=backup)
    assert not report["ok"] and not report["reconciled"]
    assert expected in problem_codes(report)
    assert reconcile_staging_rows(stage, verified_backup=backup) == report


def test_corruption_detected_by_lower_level_preflight_stays_rejected(migrated):
    stage, backup, _, _, _ = migrated
    change(stage, "UPDATE decisions SET version=100 WHERE run_id=1")
    report = reconcile_staging_rows(stage, verified_backup=backup)
    assert not report["ok"] and not report["reconciled"]
    assert "audit_final" in problem_codes(report)


def test_changed_archive_without_verified_backup_is_detected_if_mapping_diverges(migrated):
    stage, _, _, _, _ = migrated
    change(
        stage,
        "UPDATE legacy_archive SET payload='{}' "
        "WHERE table_name='runs' AND ordinal=0",
    )
    report = reconcile_staging_rows(stage)
    assert not report["ok"] and not report["reconciled"]


def test_mismatched_backup_blocks_reconciliation(migrated, tmp_path):
    stage, _, _, _, _ = migrated
    alternate = tmp_path / "foreign.sqlite3"
    with closing(sqlite3.connect(alternate)) as db:
        db.execute("CREATE TABLE synthetic(value)")
        db.commit()
    report = reconcile_staging_rows(stage, verified_backup=alternate)
    assert not report["ok"] and not report["reconciled"]
    assert "backup_digest" in problem_codes(report)


def test_missing_stage_does_not_create_file(tmp_path):
    missing = tmp_path / "absent.sqlite3"
    with pytest.raises(FileNotFoundError):
        reconcile_staging_rows(missing)
    assert not missing.exists()
