"""Independent HTTP acceptance of live membership and unsupported endpoints.

This file exercises the real ScopedBackend and repository, not a backend spy.
Everything uses synthetic PDFs and temporary local SQLite databases.
"""
from contextlib import closing
from io import BytesIO

import fitz
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from scoped_history import Repository, Scope
from secure_api import COOKIE, create_secure_app
from secure_backend import ScopedBackend
from secure_sessions import Identity, Sessions


ORIGIN = "https://kd.test"
SCOPE = Scope("synthetic-org", "synthetic-project")


class Verifier:
    def verify(self, ticket):
        return {
            "engineer": Identity("engineer", SCOPE.organization, SCOPE.project),
            "viewer": Identity("viewer", SCOPE.organization, SCOPE.project),
            "outsider": Identity("outsider", SCOPE.organization, "other-project"),
        }.get(ticket)


def _setup(tmp_path):
    repo = Repository.create(tmp_path / "scoped.sqlite3")
    repo.provision("engineer", SCOPE, "engineer")
    repo.provision("viewer", SCOPE, "viewer")
    other = Scope(SCOPE.organization, "other-project")
    repo.provision("outsider", other, "engineer")
    app = create_secure_app(
        verifier=Verifier(), backend=ScopedBackend(repo),
        sessions=Sessions(), origin=ORIGIN,
    )
    return repo, app


def _login(client, ticket):
    response = client.post(
        "/auth/login", json={"credential": ticket}, headers={"origin": ORIGIN}
    )
    assert response.status_code == 200
    return {"origin": ORIGIN, "x-csrf-token": response.json()["csrf_token"]}


def _pdf():
    with fitz.open() as pdf:
        pdf.new_page().insert_text((72, 72), "SYNTHETIC ONLY")
        return pdf.tobytes()


def _check(client, headers, filename="synthetic.pdf"):
    response = client.post(
        "/api/check",
        files={"files": (filename, _pdf(), "application/pdf")},
        data={"revision": "test-revision"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["issues"] and result["finding_states"]
    return result


def test_role_downgrade_after_login_prevents_writes_without_erasing_read_access(tmp_path):
    repo, app = _setup(tmp_path)
    with TestClient(app, base_url=ORIGIN) as client:
        csrf = _login(client, "engineer")
        result = _check(client, csrf)
        run_id, fid = result["id"], result["finding_states"][0]["id"]
        repo.provision("engineer", SCOPE, "viewer")
        url = f"/api/runs/{run_id}/findings/{fid}"
        denied = client.patch(
            url,
            json={"status": "resolved", "comment": "should not persist", "expected_version": 0},
            headers=csrf,
        )
        assert denied.status_code == 403
        assert client.post(
            "/api/check",
            files={"files": ("synthetic.pdf", _pdf(), "application/pdf")},
            data={"revision": "test-revision"},
            headers=csrf,
        ).status_code == 403
        assert client.get(f"/api/runs/{run_id}").status_code == 200
        assert client.get(url + "/history").json() == []
        saved = repo.get("engineer", SCOPE, run_id)
        assert saved["decisions"][0]["status"] == "new"
        assert saved["decisions"][0]["version"] == 0
        assert repo.list("engineer", SCOPE) == [run_id]


def test_membership_revoked_after_login_refuses_reads_and_exports(tmp_path):
    repo, app = _setup(tmp_path)
    with TestClient(app, base_url=ORIGIN) as client:
        csrf = _login(client, "engineer")
        run = _check(client, csrf)
        repo.revoke("engineer", SCOPE)
        for suffix in ("", "/csv", "/xlsx"):
            response = client.get(f"/api/runs/{run['id']}{suffix}")
            assert response.status_code == 403
        assert client.get("/api/runs").status_code == 403
        with repo.transaction() as con:
            assert con.execute("SELECT count(*) FROM runs").fetchone()[0] == 1
            assert con.execute("SELECT count(*) FROM events").fetchone()[0] == 0


def test_unimplemented_historical_checklist_endpoints_fail_explicitly(tmp_path):
    repo, app = _setup(tmp_path)
    with TestClient(app, base_url=ORIGIN) as client:
        csrf = _login(client, "engineer")
        previous = _check(client, csrf)
        current = _check(client, csrf)
        for suffix in ("", "/csv", "/xlsx"):
            path = (
                f"/api/runs/{current['id']}/checklist-compare/"
                f"{previous['id']}{suffix}"
            )
            assert client.get(path).status_code == 409
        # The implemented ordinary KD comparison remains operational.
        assert client.get(
            f"/api/runs/{current['id']}/compare/{previous['id']}"
        ).status_code == 200
        # The unsupported operation cannot write an audit entry.
        with repo.transaction() as con:
            assert con.execute("SELECT count(*) FROM events").fetchone()[0] == 0


def test_fresh_session_cannot_inherit_a_prior_engineers_decision(tmp_path):
    repo, app = _setup(tmp_path)
    with TestClient(app, base_url=ORIGIN) as client:
        csrf = _login(client, "engineer")
        old = _check(client, csrf)
        fid = old["finding_states"][0]["id"]
        url = f"/api/runs/{old['id']}/findings/{fid}"
        changed = client.patch(
            url, json={
                "status": "resolved", "comment": "=SYNTHETIC",
                "expected_version": 0,
            }, headers=csrf,
        )
        assert changed.status_code == 200
        old_cookie = client.cookies.get(COOKIE)
        assert client.post("/auth/logout", headers=csrf).status_code == 200
        client.cookies.set(COOKIE, old_cookie)
        assert client.get(f"/api/runs/{old['id']}").status_code == 401
        fresh = _login(client, "engineer")
        new = _check(client, fresh)
        assert all(state["status"] == "new" for state in new["finding_states"])
        assert all(state["version"] == 0 for state in new["finding_states"])
        assert len(client.get(url + "/history").json()) == 1
        with closing(load_workbook(BytesIO(
            client.get(f"/api/runs/{old['id']}/xlsx").content
        ))) as workbook:
            assert workbook["Замечания"]["I2"].value == "=SYNTHETIC"
            assert workbook["Замечания"]["I2"].data_type == "s"
