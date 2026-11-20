"""Adversarial JSON acceptance for PR #65 using real HTTP and scoped storage.

All input and identities are synthetic; no native EPLAN contract is asserted.
"""
from copy import deepcopy
import json

import pytest
from fastapi.testclient import TestClient

from secure_api import create_secure_app
from secure_backend import ScopedBackend
from secure_sessions import Identity, Sessions
from scoped_history import Repository, Scope


ORIGIN = "https://kd.test"
SCOPE = Scope("org-synthetic", "project-synthetic")
RECORD = {
    "wire": "W1", "cable": "", "source": {"device": "X1", "terminal": "1"},
    "target": {"device": "X2", "terminal": "2"},
    "document": "synthetic.json", "location": "row 1",
}
PAYLOAD = {
    "schema_version": 1, "project": SCOPE.project, "revision": "rev-1",
    "records": [RECORD, dict(RECORD, location="row 2")],
}


class Verifier:
    def verify(self, credential):
        return {
            "engineer": Identity("engineer", SCOPE.organization, SCOPE.project),
            "viewer": Identity("viewer", SCOPE.organization, SCOPE.project),
            "foreign": Identity("foreign", "other-org", SCOPE.project),
        }.get(credential)


@pytest.fixture
def service(tmp_path):
    repo = Repository.create(tmp_path / "scoped.sqlite3")
    repo.provision("engineer", SCOPE, "engineer")
    repo.provision("viewer", SCOPE, "viewer")
    repo.provision("foreign", Scope("other-org", SCOPE.project), "engineer")
    app = create_secure_app(
        verifier=Verifier(), backend=ScopedBackend(repo),
        sessions=Sessions(), origin=ORIGIN,
    )
    with TestClient(app, base_url=ORIGIN) as client:
        yield repo, client


def login(client, name="engineer"):
    response = client.post(
        "/auth/login", json={"credential": name}, headers={"origin": ORIGIN}
    )
    assert response.status_code == 200
    return {"origin": ORIGIN, "x-csrf-token": response.json()["csrf_token"]}


def counts(repo):
    with repo.transaction() as con:
        return tuple(
            con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in ("runs", "decisions", "events")
        )


@pytest.mark.parametrize("raw", [
    b"[]", b"null", b"{}",
    b'{"project":"a","project":"b"}',
    b'{"schema_version":1,"project":"project-synthetic","revision":"rev",'
    b'"records":[],"unknown":1}',
    b'{"schema_version":NaN}',
    b'{"schema_version":Infinity}',
    b'\xff\xfe',
    b'{"schema_version":1,"project":"project-synthetic","revision":"rev",'
    b'"records":[{"wire":"W","wire":"W2"}]}',
])
def test_invalid_or_duplicate_key_json_never_creates_partial_runs(service, raw):
    repo, client = service
    headers = login(client)
    before = counts(repo)
    response = client.post(
        "/api/connections/check", content=raw,
        headers=headers | {"content-type": "application/json"},
    )
    assert response.status_code == 422, (response.status_code, response.text)
    assert counts(repo) == before


@pytest.mark.parametrize("damage", ["foreign-project", "bad-device", "missing-target", "bad-cable", "nonstring-location"])
def test_rejected_records_have_no_audit_or_run_side_effect(service, damage):
    repo, client = service
    headers = login(client)
    payload = deepcopy(PAYLOAD)
    record = payload["records"][0]
    if damage == "foreign-project":
        payload["project"] = "other-project"
    elif damage == "bad-device":
        record["source"]["device"] = " "
    elif damage == "missing-target":
        del record["target"]
    elif damage == "bad-cable":
        record["cable"] = 17
    elif damage == "nonstring-location":
        record["location"] = ["row 1"]
    result = client.post("/api/connections/check", json=payload, headers=headers)
    assert result.status_code in (404, 422)
    assert counts(repo) == (0, 0, 0)


def test_content_type_and_exact_body_limit_are_enforced_before_persistence(service):
    repo, client = service
    headers = login(client)
    not_json = client.post(
        "/api/connections/check", content=json.dumps(PAYLOAD),
        headers=headers | {"content-type": "text/plain"},
    )
    assert not_json.status_code == 415
    oversized = client.post(
        "/api/connections/check", content=b"x" * (512 * 1024 + 1),
        headers=headers | {"content-type": "application/json"},
    )
    assert oversized.status_code == 413
    assert counts(repo) == (0, 0, 0)


def test_valid_connection_run_is_project_scoped_and_does_not_claim_electrical_defect(service):
    repo, client = service
    headers = login(client)
    response = client.post("/api/connections/check", json=PAYLOAD, headers=headers)
    assert response.status_code == 200, response.text
    saved = response.json()
    assert saved["id"] > 0
    assert {f["code"] for f in saved["issues"]} == {"KD-CON-101"}
    assert all(f["severity"] == "info" for f in saved["issues"])
    assert saved["revision"] == "rev-1"
    assert counts(repo)[0] == 1
    # The current session is changed through a trusted synthetic verifier.
    login(client, "foreign")
    assert client.get("/api/runs").json() == []
    assert client.get(f"/api/runs/{saved['id']}").status_code == 404
    assert client.get(f"/api/runs/{saved['id']}/csv").status_code == 404
