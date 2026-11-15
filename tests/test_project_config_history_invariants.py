"""Independent synthetic HTTP regression for versioned project configuration.

Exercises real ScopedBackend, ProjectConfiguration and transaction audit.
Never changes local config.json or enterprise configuration.
"""
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from project_configuration import DEFAULT
from scoped_history import Repository, Scope
from secure_api import create_secure_app
from secure_backend import ScopedBackend
from secure_sessions import Identity, Sessions


ORIGIN = "https://kd.test"
A = Scope("synthetic-org", "project-A")
B = Scope("synthetic-org", "project-B")


class Verifier:
    def verify(self, ticket):
        identities = {
            "admin-a": Identity("admin-a", A.organization, A.project),
            "reader-a": Identity("reader-a", A.organization, A.project),
            "admin-b": Identity("admin-b", B.organization, B.project),
        }
        return identities.get(ticket)


@pytest.fixture
def service(tmp_path):
    repository = Repository.create(tmp_path / "scoped.sqlite3")
    for name, scope, role in [
        ("admin-a", A, "admin"),
        ("reader-a", A, "viewer"),
        ("admin-b", B, "admin"),
    ]:
        repository.provision(name, scope, role)
    backend = ScopedBackend(repository)
    app = create_secure_app(
        verifier=Verifier(), backend=backend,
        sessions=Sessions(), origin=ORIGIN,
    )
    with TestClient(app, base_url=ORIGIN) as client:
        yield repository, client


def login(client, credential):
    response = client.post(
        "/auth/login", json={"credential": credential}, headers={"origin": ORIGIN},
    )
    assert response.status_code == 200
    return {"origin": ORIGIN, "x-csrf-token": response.json()["csrf_token"]}


def proposal(chars):
    return {**deepcopy(DEFAULT), "min_pdf_text_chars": chars}


def test_config_noop_does_not_increment_version_or_emit_an_audit_event(service):
    repo, client = service
    csrf = login(client, "admin-a")
    original = client.get("/api/config")
    assert original.status_code == 200
    assert original.headers["x-config-version"] == "0"
    same = client.put(
        "/api/config", json={"settings": original.json(), "expected_version": 0},
        headers=csrf,
    )
    assert same.status_code == 200 and same.json()["version"] == 0
    assert client.get("/api/config/history").json() == []
    updated = client.put(
        "/api/config", json={"settings": proposal(101), "expected_version": 0},
        headers=csrf,
    )
    assert updated.status_code == 200 and updated.json()["version"] == 1
    same_again = client.put(
        "/api/config", json={"settings": proposal(101), "expected_version": 1},
        headers=csrf,
    )
    assert same_again.status_code == 200 and same_again.json()["version"] == 1
    history = client.get("/api/config/history").json()
    assert len(history) == 1
    assert history[0]["old_version"] == 0 and history[0]["new_version"] == 1
    with repo.transaction() as con:
        assert con.execute("SELECT count(*) FROM project_config_events").fetchone()[0] == 1


def test_two_projects_keep_independent_versions_and_audit_chains(service):
    repo, client = service
    a = login(client, "admin-a")
    assert client.put(
        "/api/config",
        json={"settings": proposal(71), "expected_version": 0}, headers=a,
    ).status_code == 200
    assert client.put(
        "/api/config",
        json={"settings": proposal(72), "expected_version": 1}, headers=a,
    ).status_code == 200
    a_history = client.get("/api/config/history").json()
    assert [event["new_version"] for event in a_history] == [1, 2]
    b = login(client, "admin-b")
    assert client.get("/api/config").headers["x-config-version"] == "0"
    assert client.get("/api/config/history").json() == []
    assert client.put(
        "/api/config",
        json={"settings": proposal(83), "expected_version": 0}, headers=b,
    ).status_code == 200
    assert client.get("/api/config").headers["x-config-version"] == "1"
    assert len(client.get("/api/config/history").json()) == 1
    login(client, "admin-a")
    assert client.get("/api/config").json()["min_pdf_text_chars"] == 72
    assert client.get("/api/config").headers["x-config-version"] == "2"
    assert client.get("/api/config/history").json() == a_history
    with repo.transaction() as con:
        assert con.execute("SELECT count(*) FROM project_config_events").fetchone()[0] == 3


def test_stale_version_does_not_change_audit_or_effective_configuration(service):
    repo, client = service
    csrf = login(client, "admin-a")
    ok = client.put(
        "/api/config", json={"settings": proposal(90), "expected_version": 0},
        headers=csrf,
    )
    assert ok.status_code == 200
    stale = client.put(
        "/api/config", json={"settings": proposal(200), "expected_version": 0},
        headers=csrf,
    )
    assert stale.status_code == 409
    assert client.get("/api/config").json()["min_pdf_text_chars"] == 90
    assert len(client.get("/api/config/history").json()) == 1
    with repo.transaction() as con:
        assert con.execute("SELECT count(*) FROM project_config_events").fetchone()[0] == 1


def test_admin_role_downgrade_blocks_config_write_but_retains_read(service):
    repo, client = service
    csrf = login(client, "admin-a")
    repo.provision("admin-a", A, "viewer")
    denied = client.put(
        "/api/config", json={"settings": proposal(111), "expected_version": 0},
        headers=csrf,
    )
    assert denied.status_code == 403
    assert client.get("/api/config").status_code == 200
    assert client.get("/api/config/history").json() == []
    with repo.transaction() as con:
        assert con.execute("SELECT count(*) FROM project_config_events").fetchone()[0] == 0


@pytest.mark.parametrize("damage", ["missing", "unknown", "string_version", "duplicate_headers"])
def test_invalid_config_does_not_create_audit_or_version(service, damage):
    repo, client = service
    csrf = login(client, "admin-a")
    data = {"settings": proposal(25), "expected_version": 0}
    if damage == "missing":
        del data["settings"]["min_pdf_text_chars"]
    elif damage == "unknown":
        data["settings"]["access_token"] = "synthetic"
    elif damage == "string_version":
        data["expected_version"] = "0"
    else:
        data["settings"]["specification_headers"] = ["Позиция", " позиция "]
    response = client.put("/api/config", json=data, headers=csrf)
    assert response.status_code == 422
    assert client.get("/api/config").headers["x-config-version"] == "0"
    assert client.get("/api/config/history").json() == []
