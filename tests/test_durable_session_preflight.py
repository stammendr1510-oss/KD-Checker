"""Synthetic tests of offline session database structural verification."""
from contextlib import closing
import sqlite3

import pytest

from durable_sessions import DurableSessions
from quality.durable_session_preflight import inspect_durable_sessions
from secure_sessions import Identity


IDENTITY = Identity("synthetic-engineer", "synthetic-org", "synthetic-project")


@pytest.fixture
def store(tmp_path):
    path = tmp_path / "sessions.sqlite3"
    return DurableSessions.create(path, ttl=300, idle=60, clock=lambda: 1000.0)


def change(path, statement, args=()):
    with closing(sqlite3.connect(path)) as db:
        db.execute(statement, args)
        db.commit()


def codes(report):
    return {entry["code"] for entry in report["problems"]}


def test_empty_session_database_and_issued_session_are_valid(store):
    assert inspect_durable_sessions(store.path) == {
        "ok": True, "problems": [], "counts": {"session_records": 0},
    }
    token, csrf = store.issue(IDENTITY)
    report = inspect_durable_sessions(store.path)
    assert report == {
        "ok": True, "problems": [], "counts": {"session_records": 1},
    }
    # Diagnostic output must not expose identity fields, raw tokens or hashes.
    text = repr(report)
    assert all(secret not in text for secret in (
        token, csrf, "synthetic-engineer", "synthetic-org", "synthetic-project"
    ))


@pytest.mark.parametrize("mutation,expected", [
    ("UPDATE sessions SET token_hash='not-a-digest'", "token_digest"),
    ("UPDATE sessions SET csrf_hash='not-a-digest'", "token_digest"),
    ("UPDATE sessions SET subject=''", "session_identity"),
    ("UPDATE sessions SET org=''", "session_identity"),
    ("UPDATE sessions SET project=''", "session_identity"),
    ("UPDATE sessions SET idle=0", "session_time"),
    ("UPDATE sessions SET expires=last_seen", "session_time"),
    ("UPDATE sessions SET expires='not-a-number'", "session_time"),
    ("UPDATE sessions SET last_seen='not-a-number'", "session_time"),
])
def test_corrupted_session_record_is_detected_without_repair(store, mutation, expected):
    store.issue(IDENTITY)
    change(store.path, mutation)
    first = inspect_durable_sessions(store.path)
    assert not first["ok"]
    assert expected in codes(first)
    assert inspect_durable_sessions(store.path) == first


def test_unknown_schema_version_is_rejected_and_unchanged(store):
    change(store.path, "UPDATE session_schema SET version=2")
    result = inspect_durable_sessions(store.path)
    assert not result["ok"] and "session_schema" in codes(result)
    with closing(sqlite3.connect(store.path)) as db:
        assert db.execute("SELECT version FROM session_schema").fetchone()[0] == 2


def test_missing_session_table_fails_closed(store):
    change(store.path, "DROP TABLE sessions")
    result = inspect_durable_sessions(store.path)
    assert not result["ok"] and "session_schema" in codes(result)


def test_revoked_session_does_not_appear_in_offline_report(store):
    token, _ = store.issue(IDENTITY)
    assert inspect_durable_sessions(store.path)["counts"]["session_records"] == 1
    store.revoke(token)
    assert inspect_durable_sessions(store.path) == {
        "ok": True, "problems": [], "counts": {"session_records": 0},
    }


def test_missing_db_is_never_created_by_read_only_verifier(tmp_path):
    absent = tmp_path / "not-created.sqlite3"
    with pytest.raises(FileNotFoundError):
        inspect_durable_sessions(absent)
    assert not absent.exists()


def test_verification_does_not_consume_or_invalidate_session(store):
    token, _ = store.issue(IDENTITY)
    before = store.resolve(token)
    assert inspect_durable_sessions(store.path)["ok"] is True
    assert store.resolve(token) == before
