"""Synthetic Render demo tests. Only the ASGI integration uses an event loop."""
import base64
from contextlib import closing
from io import BytesIO
import json
from pathlib import Path

from fastapi import HTTPException
from fastapi.testclient import TestClient
from openpyxl import load_workbook
import pytest

import parser_process
import render_demo as demo
from test_parser_worker_pdf_bundle import TESTS, bundle


# Test fixture only. Deployment requires a unique random value.
TOKEN = "SYNTHETIC-DEMO-TEST-FIXTURE-NOT-A-REAL-TOKEN-0"
HOST = "synthetic-kd-test.onrender.com"
ORIGIN = f"https://{HOST}"
ENV = {"RENDER_EXTERNAL_HOSTNAME": HOST, "KD_DEMO_TOKEN": TOKEN,
       "KD_DEMO_ACK": "anonymized-pdf-only"}
AUTH = b"Basic " + base64.b64encode(f"demo:{TOKEN}".encode())
CONFIG = json.loads((Path(demo.__file__).with_name("config.json")).read_bytes())


def request_without_loop(app, path, *, method="GET", headers=()):
    """Drive synchronous ASGI paths and fail if they try to read a body."""
    output = []

    async def receive():
        pytest.fail("The request reached body admission")

    async def send(message):
        output.append(message)

    scope = {"type": "http", "method": method, "path": path, "root_path": "",
             "headers": [(b"host", HOST.encode()), *headers], "scheme": "https",
             "query_string": b"", "server": (HOST, 443)}
    with pytest.raises(StopIteration):
        app(scope, receive, send).send(None)
    return output[0]["status"], dict(output[0]["headers"]), output[-1]["body"]


@pytest.mark.parametrize("path", ["/", "/api/runs/1", "/api/runs/1/csv", "/api/runs/1/xlsx"])
def test_interface_and_data_require_authorization(path):
    status, headers, body = request_without_loop(demo.create_app(ENV), path)
    assert (status, body) == (401, b"")
    assert headers[b"www-authenticate"].startswith(b"Basic")
    assert headers[b"cache-control"] == b"no-store"


def test_auth_and_origin_are_checked_before_reading_upload():
    app = demo.create_app(ENV)
    assert request_without_loop(app, "/api/check", method="POST")[0] == 401
    headers = [(b"authorization", AUTH)]
    assert request_without_loop(app, "/", headers=headers)[0] == 200
    assert request_without_loop(app, "/healthz")[0] == 204
    assert request_without_loop(app, "/api/check", method="POST", headers=headers)[0] == 403
    declared = headers + [(b"origin", ORIGIN.encode()), (b"content-length", b"7000000")]
    assert request_without_loop(app, "/api/check", method="POST", headers=declared)[0] == 413
    assert not demo.authorized("Basic invalid!", TOKEN.encode())
    assert not demo.authorized("Basic " + base64.b64encode(b"demo:wrong").decode(), TOKEN.encode())


def test_browser_form_keeps_origin_for_same_origin_post():
    status, headers, body = request_without_loop(
        demo.create_app(ENV), "/", headers=[(b"authorization", AUTH)]
    )
    assert status == 200
    assert b'<form action="/check" method="post" enctype="multipart/form-data">' in body
    assert headers[b"referrer-policy"] == b"same-origin"


@pytest.mark.parametrize("path", ["/check", "/api/check", "/api/reset"])
@pytest.mark.parametrize("origins", [
    [], [b"null"], [b"https://another.onrender.com"],
    [ORIGIN.replace("https:", "http:").encode()], [ORIGIN.encode(), ORIGIN.encode()],
])
def test_post_rejects_missing_opaque_duplicate_and_cross_site_origins(path, origins):
    headers = [(b"authorization", AUTH), (b"referer", (ORIGIN + "/").encode())]
    headers += [(b"origin", origin) for origin in origins]
    assert request_without_loop(demo.create_app(ENV), path, method="POST", headers=headers)[0] == 403


def test_same_origin_form_passes_csrf_and_keeps_authentication_and_limits():
    app = demo.create_app(ENV)
    headers = [(b"origin", ORIGIN.encode()), (b"content-length", b"7000000")]
    assert request_without_loop(app, "/check", method="POST", headers=headers)[0] == 401
    assert request_without_loop(app, "/check", method="POST",
                                headers=headers + [(b"authorization", AUTH)])[0] == 413
    assert request_without_loop(app, "/api/reset", method="POST",
                                headers=headers + [(b"authorization", AUTH)])[0] == 303


@pytest.mark.parametrize("override", [
    {"KD_DEMO_TOKEN": ""}, {"KD_DEMO_ACK": ""}, {"RENDER_EXTERNAL_HOSTNAME": "localhost"},
    {"KD_CHECKLIST_FEED_URL": ""}, {"SUPABASE_URL": ""},
])
def test_startup_fails_closed(override):
    with pytest.raises(RuntimeError, match="Invalid demo environment"):
        demo.create_app({**ENV, **override})


@pytest.mark.parametrize(("files", "status"), [
    ([], 400),
    ([("file.pdf", "application/pdf", b"%PDF-1.7")] * 5, 400),
    ([("file.csv", "application/pdf", b"%PDF-1.7")], 415),
    ([("file.pdf", "text/plain", b"%PDF-1.7")], 415),
    ([("file.pdf", "application/pdf", b"spoof")], 415),
    ([("dir/file.pdf", "application/pdf", b"%PDF-1.7")], 415),
    ([("file.pdf", "application/pdf", b"%PDF-" + bytes(demo.MAX_FILE))], 413),
    ([("file.pdf", "application/pdf", b"%PDF-" + bytes(demo.MAX_FILE - 5))] * 4, 413),
])
def test_upload_limits(files, status):
    with pytest.raises(HTTPException) as failure:
        demo.validate_pdfs(files)
    assert failure.value.status_code == status


def test_real_analysis_and_exports(bundle, monkeypatch, tmp_path):
    monkeypatch.setattr(parser_process, "_TEMP_ROOT", tmp_path)
    files = demo.validate_pdfs([(name, "application/pdf", data) for name, data in bundle.items()])
    issues = demo.analyze_upload(files, CONFIG)
    expected = json.loads((TESTS / "fixtures/parser-worker-pdf-findings.json").read_bytes())
    assert sorted(issues, key=lambda item: item["code"]) == sorted(expected, key=lambda item: item["code"])
    record = {"id": 1, "created_at": "2026-01-01T00:00:00Z", "files": list(bundle), "issues": issues}
    assert b"KD-PE-100" in demo.csv_bytes(record)
    with closing(load_workbook(BytesIO(demo.build_excel_report(record)))) as book:
        assert {row[2] for row in list(book["Замечания"].values)[1:]} == {item["code"] for item in issues}
    with pytest.raises(HTTPException) as failure:
        demo.analyze_upload([("broken.pdf", b"%PDF-1.7\ntruncated")], CONFIG)
    assert failure.value.status_code == 422
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("upload_path", ["/api/check", "/check"])
def test_asgi_upload_export_and_reset(bundle, monkeypatch, tmp_path, upload_path):
    monkeypatch.setattr(parser_process, "_TEMP_ROOT", tmp_path)
    auth = {"Authorization": AUTH.decode(), "Origin": ORIGIN}
    parts = [("files", (name, data, "application/pdf")) for name, data in bundle.items()]
    app = demo.create_app(ENV)
    with TestClient(app, base_url=ORIGIN, follow_redirects=False) as client:
        page = client.get("/", headers=auth)
        assert page.headers["referrer-policy"] == "same-origin"
        assert client.post(upload_path, files=parts).status_code == 401
        assert client.post(upload_path, files=parts, headers={**auth, "Origin": "null"}).status_code == 403
        assert client.post(upload_path, files=parts * 2, headers=auth).status_code == 400
        response = client.post(upload_path, files=parts, headers=auth)
        if upload_path == "/check":
            assert response.status_code == 303
            location = response.headers["location"]
            assert location == "/?run=1"
            assert "KD-PE-100" in client.get(location, headers=auth).text
            path = "/api/runs/1"
        else:
            assert response.status_code == 200
            path = f'/api/runs/{response.json()["id"]}'
        record = client.get(path, headers=auth)
        assert record.status_code == 200
        expected = json.loads((TESTS / "fixtures/parser-worker-pdf-findings.json").read_bytes())
        assert sorted(record.json()["issues"], key=lambda item: item["code"]) == sorted(
            expected, key=lambda item: item["code"]
        )
        assert client.get(path + "/csv", headers=auth).content.startswith(b"\xef\xbb\xbf")
        assert client.get(path + "/xlsx", headers=auth).content.startswith(b"PK")
        assert client.post("/api/reset", headers=auth).status_code == 303
        assert client.get(path, headers=auth).status_code == 404
