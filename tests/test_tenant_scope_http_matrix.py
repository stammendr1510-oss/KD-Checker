"""Synthetic three-scope HTTP noninterference matrix.

Uses the real protected FastAPI adapter and a temporary server-local SQLite.
Distinct organizations/projects use independent session cookies; no enterprise
identities, real documents, EPLAN native data or external services.
"""
from contextlib import ExitStack
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from project_configuration import DEFAULT
from scoped_history import Repository, Scope
from secure_api import create_secure_app
from secure_backend import ScopedBackend
from secure_sessions import Identity, Sessions


ORIGIN = "https://kd.test"
A = Scope("synthetic-org-a", "shared-project-name")
B = Scope("synthetic-org-a", "other-project")
C = Scope("synthetic-org-c", "shared-project-name")
SCOPE_BY_CREDENTIAL = {
    "a-engineer": A,
    "a-admin": A,
    "a-viewer": A,
    "b-engineer": B,
    "c-engineer": C,
}
RECORD = {
    "wire": "W-SYN", "cable": "",
    "source": {"device": "X1", "terminal": "1"},
    "target": {"device": "X1", "terminal": "1"},
    "document": "synthetic.json", "location": "entry-1",
}


class Verifier:
    def verify(self, credential):
        scope = SCOPE_BY_CREDENTIAL.get(credential)
        return (
            Identity(credential, scope.organization, scope.project)
            if scope is not None else None
        )


@pytest.fixture
def environment(tmp_path):
    repo = Repository.create(tmp_path / "scoped.sqlite3")
    for user, scope in SCOPE_BY_CREDENTIAL.items():
        role = "admin" if user.endswith("-admin") else (
            "viewer" if user.endswith("-viewer") else "engineer"
        )
        repo.provision(user, scope, role)
    application = create_secure_app(
        verifier=Verifier(), backend=ScopedBackend(repo),
        sessions=Sessions(), origin=ORIGIN,
    )
    with ExitStack() as stack:
        clients = {
            user: stack.enter_context(TestClient(application, base_url=ORIGIN))
            for user in SCOPE_BY_CREDENTIAL
        }
        headers = {}
        for user, client in clients.items():
            response = client.post(
                "/auth/login", json={"credential": user},
                headers={"origin": ORIGIN},
            )
            assert response.status_code == 200, response.text
            headers[user] = {
                "origin": ORIGIN,
                "x-csrf-token": response.json()["csrf_token"],
            }
        yield repo, clients, headers


def connection_payload(scope, *, marker):
    row = deepcopy(RECORD)
    row["document"] = f"synthetic-{marker}.json"
    return {
        "schema_version": 1, "project": scope.project,
        "revision": f"revision-{marker}",
        "records": [row, dict(row, location="entry-2")],
    }


def check(clients, headers, user, scope, marker):
    response = clients[user].post(
        "/api/connections/check",
        json=connection_payload(scope, marker=marker),
        headers=headers[user],
    )
    assert response.status_code == 200, response.text
    saved = response.json()
    assert saved["issues"] and saved["finding_states"]
    assert set(item["severity"] for item in saved["issues"]) == {"info"}
    assert {"KD-CON-101", "KD-CON-102"} == {
        item["code"] for item in saved["issues"]
    }
    return saved


def persisted_counts(repo):
    with repo.transaction() as db:
        return {
            table: db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in ("runs", "decisions", "events", "project_config_events")
        }


def test_identical_project_names_in_different_orgs_are_isolated_everywhere(environment):
    repo, clients, headers = environment
    a = check(clients, headers, "a-engineer", A, "org-a")
    b = check(clients, headers, "b-engineer", B, "project-b")
    c = check(clients, headers, "c-engineer", C, "org-c")
    all_runs = (a, b, c)
    for user, own in (("a-engineer", a), ("b-engineer", b), ("c-engineer", c)):
        client = clients[user]
        own_list = client.get("/api/runs")
        assert own_list.status_code == 200
        assert [run["id"] for run in own_list.json()] == [own["id"]]
        assert client.get(f"/api/runs/{own['id']}").status_code == 200
        for foreign in all_runs:
            if foreign["id"] == own["id"]:
                continue
            base = f"/api/runs/{foreign['id']}"
            fid = foreign["finding_states"][0]["id"]
            for suffix in ("", "/csv", "/xlsx", f"/findings/{fid}/history"):
                denied = client.get(base + suffix)
                assert denied.status_code == 404
                assert denied.json() == {"detail": "Not found"}
            assert client.get(
                f"/api/runs/{own['id']}/compare/{foreign['id']}"
            ).status_code == 404
            assert client.get(
                f"/api/runs/{own['id']}/checklist-compare/{foreign['id']}"
            ).status_code == 404
    assert persisted_counts(repo)["runs"] == 3


def test_foreign_finding_id_cannot_modify_local_run_or_history(environment):
    repo, clients, headers = environment
    mine = check(clients, headers, "a-engineer", A, "mine")
    foreign = check(clients, headers, "b-engineer", B, "foreign")
    other_id = foreign["finding_states"][0]["id"]
    mine_id = mine["finding_states"][0]["id"]
    a = clients["a-engineer"]
    before = persisted_counts(repo)
    wrong_binding = a.patch(
        f"/api/runs/{mine['id']}/findings/{other_id}",
        json={"status": "resolved", "comment": "Synthetic", "expected_version": 0},
        headers=headers["a-engineer"],
    )
    assert wrong_binding.status_code == 404
    cross_run = a.patch(
        f"/api/runs/{foreign['id']}/findings/{other_id}",
        json={"status": "resolved", "comment": "Synthetic", "expected_version": 0},
        headers=headers["a-engineer"],
    )
    assert cross_run.status_code == 404
    assert persisted_counts(repo) == before
    valid = a.patch(
        f"/api/runs/{mine['id']}/findings/{mine_id}",
        json={"status": "resolved", "comment": "Reviewed synthetic", "expected_version": 0},
        headers=headers["a-engineer"],
    )
    assert valid.status_code == 200
    assert valid.json()["version"] == 1
    assert len(a.get(
        f"/api/runs/{mine['id']}/findings/{mine_id}/history"
    ).json()) == 1
    assert persisted_counts(repo)["events"] == 1


def test_viewer_can_read_same_project_but_cannot_edit_or_import(environment):
    repo, clients, headers = environment
    run = check(clients, headers, "a-engineer", A, "viewer")
    viewer = clients["a-viewer"]
    assert viewer.get("/api/runs").status_code == 200
    assert viewer.get(f"/api/runs/{run['id']}/xlsx").status_code == 200
    fid = run["finding_states"][0]["id"]
    before = persisted_counts(repo)
    denied_patch = viewer.patch(
        f"/api/runs/{run['id']}/findings/{fid}",
        json={"status": "resolved", "comment": "Synthetic", "expected_version": 0},
        headers=headers["a-viewer"],
    )
    assert denied_patch.status_code == 403
    denied_import = viewer.post(
        "/api/connections/check",
        json=connection_payload(A, marker="viewer-denied"),
        headers=headers["a-viewer"],
    )
    assert denied_import.status_code == 403
    assert persisted_counts(repo) == before


def test_admin_config_and_audit_are_not_visible_to_other_tenants(environment):
    repo, clients, headers = environment
    admin = clients["a-admin"]
    settings = dict(DEFAULT, min_pdf_text_chars=73)
    changed = admin.put(
        "/api/config", headers=headers["a-admin"],
        json={"expected_version": 0, "settings": settings},
    )
    assert changed.status_code == 200
    assert changed.json()["version"] == 1
    assert admin.get("/api/config").headers["x-config-version"] == "1"
    assert len(admin.get("/api/config/history").json()) == 1
    for user in ("b-engineer", "c-engineer"):
        client = clients[user]
        response = client.get("/api/config")
        assert response.status_code == 200
        assert response.headers["x-config-version"] == "0"
        assert response.json() == DEFAULT
        assert client.get("/api/config/history").json() == []
    assert persisted_counts(repo)["project_config_events"] == 1


def test_forged_project_in_connection_json_is_rejected_without_run(environment):
    repo, clients, headers = environment
    before = persisted_counts(repo)
    for user, scope in (("a-engineer", A), ("b-engineer", B), ("c-engineer", C)):
        foreign = B if scope != B else A
        payload = connection_payload(foreign, marker=user)
        response = clients[user].post(
            "/api/connections/check", json=payload, headers=headers[user],
        )
        assert response.status_code == 404
    assert persisted_counts(repo) == before
