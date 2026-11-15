"""Synthetic fail-closed regression for server-local durable sessions.

No application, enterprise identity, network listener or production database.
"""
from contextlib import closing
import sqlite3

import pytest

from durable_sessions import DurableSessions
from secure_sessions import Identity


IDENTITY_A = Identity("engineer-a", "org-a", "project-a")
IDENTITY_B = Identity("engineer-b", "org-b", "project-b")


def _count(path):
    with closing(sqlite3.connect(path)) as connection:
        return connection.execute("SELECT count(*) FROM sessions").fetchone()[0]


def test_exclusive_create_never_overwrites_existing_session_database(tmp_path):
    path = tmp_path / "sessions.sqlite3"
    first = DurableSessions.create(path)
    token, _ = first.issue(IDENTITY_A)
    before = path.read_bytes()
    with pytest.raises(FileExistsError):
        DurableSessions.create(path)
    assert path.read_bytes() == before
    assert DurableSessions(path).resolve(token).identity == IDENTITY_A


def test_unsupported_schema_version_is_not_silently_migrated(tmp_path):
    path = tmp_path / "sessions.sqlite3"
    DurableSessions.create(path)
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("UPDATE session_schema SET version=999")
        connection.commit()
    with pytest.raises(ValueError, match="Unsupported session schema"):
        DurableSessions(path)
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute("SELECT version FROM session_schema").fetchone()[0] == 999


def test_session_capacity_rejects_overflow_without_eviction(tmp_path):
    path = tmp_path / "sessions.sqlite3"
    first = DurableSessions.create(path, capacity=1)
    token, _ = first.issue(IDENTITY_A)
    with pytest.raises(RuntimeError, match="Session capacity"):
        first.issue(IDENTITY_B)
    assert _count(path) == 1
    assert first.resolve(token).identity == IDENTITY_A


@pytest.mark.parametrize("invalid", [None, "", "short", 3, "x" * 201])
def test_invalid_tokens_do_not_authenticate_or_revoke_valid_session(tmp_path, invalid):
    path = tmp_path / "sessions.sqlite3"
    store = DurableSessions.create(path)
    good, _ = store.issue(IDENTITY_A)
    assert store.resolve(invalid) is None
    store.revoke(invalid)
    store.touch(invalid)
    assert store.resolve(good).identity == IDENTITY_A
    assert _count(path) == 1


def test_session_isolation_between_users_and_server_instances(tmp_path):
    path = tmp_path / "sessions.sqlite3"
    left = DurableSessions.create(path)
    first, csrf_a = left.issue(IDENTITY_A)
    second, csrf_b = left.issue(IDENTITY_B)
    right = DurableSessions(path)
    assert right.resolve(first).identity == IDENTITY_A
    assert right.resolve(second).identity == IDENTITY_B
    with closing(sqlite3.connect(path)) as connection:
        dump = repr(connection.execute("SELECT * FROM sessions").fetchall())
    for secret in (first, second, csrf_a, csrf_b):
        assert secret not in dump
    right.revoke(first)
    left.touch(first)
    assert left.resolve(first) is None
    assert left.resolve(second).identity == IDENTITY_B
    assert _count(path) == 1


def test_rollback_of_server_clock_refuses_session_and_does_not_resurrect(tmp_path):
    now = [1000.0]
    path = tmp_path / "sessions.sqlite3"
    store = DurableSessions.create(path, clock=lambda: now[0], ttl=100, idle=60)
    token, _ = store.issue(IDENTITY_A)
    assert store.resolve(token).identity == IDENTITY_A
    now[0] = 999.0
    assert store.resolve(token) is None
    now[0] = 1001.0
    store.touch(token)
    assert store.resolve(token) is None


@pytest.mark.parametrize("boundary", [("idle", 30), ("absolute", 100)])
def test_exact_expiry_boundaries_are_denied(tmp_path, boundary):
    now = [2000.0]
    clock = lambda: now[0]
    store = DurableSessions.create(
        tmp_path / "sessions.sqlite3", clock=clock, ttl=100, idle=30
    )
    token, _ = store.issue(IDENTITY_A)
    mode, seconds = boundary
    if mode == "absolute":
        for _ in range(4):
            now[0] += 20
            store.touch(token)
    else:
        now[0] += seconds
    assert store.resolve(token) is None
    assert _count(store.path) == 0
