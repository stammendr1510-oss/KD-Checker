"""Two independent secured ASGI apps sharing local SQLite: synthetic acceptance.

Not a LAN, PostgreSQL, distributed lock, or physical two-PC test.
Uses no enterprise credentials or production databases.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
import threading

import fitz
import pytest
from fastapi.testclient import TestClient

from durable_sessions import DurableSessions
from project_configuration import DEFAULT
from scoped_history import Repository, Scope
from secure_api import COOKIE, create_secure_app
from secure_backend import ScopedBackend
from secure_sessions import Identity


ORIGIN = "https://kd.test"
SCOPE = Scope("synthetic-org", "project-one")


class Verifier:
    def verify(self, credential):
        if credential in ("engineer", "admin", "viewer"):
            return Identity(credential, SCOPE.organization, SCOPE.project)
        return None


@pytest.fixture
def instances(tmp_path):
    repository = Repository.create(tmp_path / "history.sqlite3")
    for subject, role in (
        ("engineer", "engineer"), ("admin", "admin"), ("viewer", "viewer")
    ):
        repository.provision(subject, SCOPE, role)
    session_path = tmp_path / "sessions.sqlite3"
    DurableSessions.create(session_path)
    def app():
        return create_secure_app(
            verifier=Verifier(), backend=ScopedBackend(Repository(repository.path)),
            sessions=DurableSessions(session_path), origin=ORIGIN,
        )
    with ExitStack() as stack:
        clients = [
            stack.enter_context(TestClient(app(), base_url=ORIGIN))
            for _ in range(2)
        ]
        yield repository, clients


def login(client, subject):
    response = client.post(
        "/auth/login", json={"credential": subject}, headers={"origin": ORIGIN}
    )
    assert response.status_code == 200, response.text
    return {"origin": ORIGIN, "x-csrf-token": response.json()["csrf_token"]}


def pdf():
    with fitz.open() as document:
        document.new_page().insert_text((72, 72), "Synthetic two client run")
        return document.tobytes()


def one_run(client, headers):
    response = client.post(
        "/api/check",
        files={"files": ("synthetic.pdf", pdf(), "application/pdf")},
        data={"revision": "test-revision"}, headers=headers,
    )
    assert response.status_code == 200, response.text
    record = response.json()
    assert record["finding_states"]
    return record["id"], record["finding_states"][0]["id"]


def concurrent(client_a, client_b, operation):
    """Separate clients, shared local backend, simultaneous HTTP requests."""
    barrier = threading.Barrier(2, timeout=15)
    def run(pair):
        client, headers = pair
        barrier.wait()
        response = operation(client, headers)
        return response.status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        return sorted(pool.map(run, [client_a, client_b]))


def test_two_http_instances_conflicting_finding_update_create_exactly_one_audit(instances):
    repo, (a, b) = instances
    a_headers, b_headers = login(a, "engineer"), login(b, "engineer")
    run_id, finding_id = one_run(a, a_headers)
    url = f"/api/runs/{run_id}/findings/{finding_id}"
    statuses = concurrent(
        (a, a_headers), (b, b_headers),
        lambda client, headers: client.patch(
            url,
            json={"status": "in_progress", "comment": "Synthetic concurrent decision",
                  "expected_version": 0},
            headers=headers,
        ),
    )
    assert statuses == [200, 409]
    first = a.get(f"/api/runs/{run_id}").json()
    second = b.get(f"/api/runs/{run_id}").json()
    assert first["finding_states"] == second["finding_states"]
    assert first["finding_states"][0]["version"] == 1
    history_a = a.get(url + "/history")
    history_b = b.get(url + "/history")
    assert history_a.status_code == history_b.status_code == 200
    assert len(history_a.json()) == len(history_b.json()) == 1
    with repo.transaction() as db:
        assert db.execute("SELECT count(*) FROM events").fetchone()[0] == 1
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []


def test_two_http_instances_config_writers_conflict_and_keep_one_version(instances):
    repo, (a, b) = instances
    a_headers, b_headers = login(a, "admin"), login(b, "admin")
    settings = dict(DEFAULT, min_pdf_text_chars=104)
    statuses = concurrent(
        (a, a_headers), (b, b_headers),
        lambda client, headers: client.put(
            "/api/config",
            json={"settings": settings, "expected_version": 0},
            headers=headers,
        ),
    )
    assert statuses == [200, 409]
    assert a.get("/api/config").headers["x-config-version"] == "1"
    assert b.get("/api/config").headers["x-config-version"] == "1"
    assert a.get("/api/config").json() == settings
    assert b.get("/api/config").json() == settings
    assert len(a.get("/api/config/history").json()) == 1
    assert a.get("/api/config/history").json() == b.get("/api/config/history").json()
    with repo.transaction() as db:
        assert db.execute("SELECT count(*) FROM project_config_events").fetchone()[0] == 1


def test_shared_logout_revokes_same_token_across_independent_http_apps(instances):
    _, (a, b) = instances
    headers = login(a, "engineer")
    old_token = a.cookies.get(COOKIE)
    b.cookies.set(COOKIE, old_token)
    assert a.get("/api/runs").status_code == 200
    assert b.get("/api/runs").status_code == 200
    assert b.post("/auth/logout", headers=headers).status_code == 200
    assert a.get("/api/runs").status_code == 401
    b.cookies.set(COOKIE, old_token)
    assert b.get("/api/runs").status_code == 401


def test_live_membership_downgrade_is_seen_by_second_instance(instances):
    repo, (a, b) = instances
    a_headers = login(a, "engineer")
    login(b, "engineer")
    run_id, finding_id = one_run(a, a_headers)
    repo.provision("engineer", SCOPE, "viewer")
    assert a.get(f"/api/runs/{run_id}").status_code == 200
    assert b.get(f"/api/runs/{run_id}").status_code == 200
    response = a.patch(
        f"/api/runs/{run_id}/findings/{finding_id}",
        json={"status": "resolved", "comment": "should be denied",
              "expected_version": 0},
        headers=a_headers,
    )
    assert response.status_code == 403
    with repo.transaction() as db:
        assert db.execute("SELECT count(*) FROM events").fetchone()[0] == 0


def test_separate_instances_do_not_accept_other_users_csrf(instances):
    repo, (a, b) = instances
    csrf_a = login(a, "engineer")
    csrf_b = login(b, "admin")
    denied = a.post(
        "/api/check", files={"files": ("synthetic.pdf", pdf(), "application/pdf")},
        data={"revision": "test"}, headers=csrf_b,
    )
    assert denied.status_code == 403
    assert b.get("/api/runs").status_code == 200
    with repo.transaction() as db:
        assert db.execute("SELECT count(*) FROM runs").fetchone()[0] == 0
