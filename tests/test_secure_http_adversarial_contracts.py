"""Adversarial, synthetic HTTP regression for the integrated secure backend.

No enterprise credentials, actual KD documents, network or production storage.
Every rejected request must leave runs and audit unchanged.
"""
from contextlib import closing
from io import BytesIO

import fitz
import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from scoped_history import Repository, Scope
from secure_api import create_secure_app
from secure_backend import ScopedBackend
from secure_sessions import Identity, Sessions


ORIGIN = "https://kd.test"
SCOPE = Scope("synthetic-org", "project-1")
ISSUE = {
    "severity": "warning", "code": "KD-012", "file": "=SUM(1,2).pdf",
    "location": "+2+3", "message": "-4+5", "recommendation": "@SUM(1,2)",
}


class Verifier:
    def verify(self, ticket):
        return {
            "engineer": Identity("engineer", SCOPE.organization, SCOPE.project),
            "viewer": Identity("viewer", SCOPE.organization, SCOPE.project),
            "admin": Identity("admin", SCOPE.organization, SCOPE.project),
        }.get(ticket)


@pytest.fixture
def system(tmp_path):
    repo = Repository.create(tmp_path / "scoped.sqlite3")
    for subject, role in (("engineer", "engineer"), ("viewer", "viewer"), ("admin", "admin")):
        repo.provision(subject, SCOPE, role)
    app = create_secure_app(
        verifier=Verifier(), backend=ScopedBackend(repo),
        sessions=Sessions(), origin=ORIGIN,
    )
    with TestClient(app, base_url=ORIGIN) as client:
        yield repo, client


def login(client, user="engineer"):
    response = client.post(
        "/auth/login", json={"credential": user}, headers={"origin": ORIGIN}
    )
    assert response.status_code == 200
    return {"origin": ORIGIN, "x-csrf-token": response.json()["csrf_token"]}


def counts(repo):
    with repo.transaction() as con:
        return tuple(
            con.execute(f"SELECT count(*) FROM {name}").fetchone()[0]
            for name in ("runs", "decisions", "events")
        )


def test_rejected_upload_requests_never_create_runs(system):
    repo, client = system
    csrf = login(client)
    requests = [
        dict(files={"files": ("example.exe", b"synthetic", "application/octet-stream")},
             data={"revision": "synthetic-rev"}),
        dict(files={"files": ("example.pdf", b"synthetic", "application/pdf")},
             data={"revision": ""}),
        dict(files={"files": ("example.pdf", b"synthetic", "application/pdf")},
             data={}),
        dict(files={"files": ("example.pdf", b"synthetic", "application/pdf")},
             data={"revision": "rev", "organization_id": "spoof"}),
    ]
    for payload in requests:
        before = counts(repo)
        response = client.post("/api/check", headers=csrf, **payload)
        assert response.status_code == 422, (response.status_code, response.text)
        assert counts(repo) == before


def test_invalid_revision_or_unknown_route_cannot_change_history(system):
    repo, client = system
    csrf = login(client)
    baseline = counts(repo)
    response = client.post(
        "/api/check", files={"files": ("synthetic.pdf", b"fake", "application/pdf")},
        data={"revision": "rev", "extra": "unused"}, headers=csrf,
    )
    assert response.status_code == 422
    assert client.post("/api/unknown", headers=csrf).status_code == 404
    assert client.delete("/api/runs/1", headers=csrf).status_code == 405
    assert counts(repo) == baseline


@pytest.mark.parametrize("header", [
    "x-user-id", "x-role", "x-organization-id", "x-project-id",
])
def test_forged_identity_header_fails_before_any_write(system, header):
    repo, client = system
    csrf = login(client)
    csrf[header] = "admin"
    response = client.post(
        "/api/check", files={"files": ("synthetic.pdf", b"broken", "application/pdf")},
        data={"revision": "rev"}, headers=csrf,
    )
    assert response.status_code == 403
    assert counts(repo) == (0, 0, 0)


def test_nonadmin_and_admin_config_writes_are_not_silently_applied(system):
    repo, client = system
    viewer = login(client, "viewer")
    assert client.put("/api/config", json={}, headers=viewer).status_code == 403
    admin = login(client, "admin")
    assert client.put("/api/config", json={}, headers=admin).status_code == 409
    with repo.transaction() as con:
        assert con.execute("SELECT count(*) FROM project_config").fetchone()[0] == 0
    assert counts(repo) == (0, 0, 0)


def test_saved_untrusted_findings_are_inert_in_both_exports(system):
    repo, client = system
    run_id = repo.save("engineer", SCOPE, [ISSUE["file"]], [ISSUE], revision="synthetic-rev")
    login(client)
    csv_response = client.get(f"/api/runs/{run_id}/csv")
    assert csv_response.status_code == 200
    import csv
    from io import StringIO
    rows = list(csv.reader(StringIO(csv_response.text.lstrip("\ufeff")), delimiter=";"))
    assert rows[1] == ["warning", "KD-012",
                       "'=SUM(1,2).pdf", "'+2+3", "'-4+5", "'@SUM(1,2)"]
    excel = client.get(f"/api/runs/{run_id}/xlsx")
    assert excel.status_code == 200
    with closing(load_workbook(BytesIO(excel.content))) as book:
        sheet = book["Замечания"]
        assert sheet["D2"].value == "=SUM(1,2).pdf"
        assert sheet["E2"].value == "+2+3"
        assert sheet["F2"].value == "-4+5"
        assert sheet["G2"].value == "@SUM(1,2)"
        assert all(sheet[f"{col}2"].data_type == "s" for col in "DEFG")
        assert not any(cell.data_type == "f" for ws in book for row in ws for cell in row)
