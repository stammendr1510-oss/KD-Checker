"""Single-instance Render demo for anonymized PDF only."""
import asyncio
import base64
import binascii
import csv
from datetime import datetime, timezone
from html import escape
import io
import json
import os
from pathlib import Path
import re
import secrets
import tempfile

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from starlette.datastructures import Headers, UploadFile
from starlette.middleware.trustedhost import TrustedHostMiddleware

from body_budget import BodyBudget
from excel_reports import build_excel_report
from parser_process import Limits, ProcessingUnavailable, run_check


MAX_FILES = 4
MAX_FILE = 2 * 1024 * 1024
MAX_TOTAL = 6 * 1024 * 1024
MAX_RUNS = 12
UPLOADS = {"/api/check", "/check"}
FIELDS = ("severity", "code", "file", "location", "message", "recommendation")


def authorized(header, token):
    if len(header) > 256:
        return False
    scheme, _, encoded = header.partition(" ")
    if scheme.lower() != "basic":
        return False
    try:
        supplied = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        return False
    return secrets.compare_digest(supplied, b"demo:" + token)


class DemoBoundary:
    def __init__(self, app, *, host, token):
        self.app = app
        self.origin = f"https://{host}"
        self.token = token
        self.gate = asyncio.Lock()
        self.upload = BodyBudget(app, limit=MAX_TOTAL + 32 * 1024, seconds=15)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = Headers(scope=scope)
        path, method = scope["path"], scope["method"]

        async def secure_send(message):
            if message["type"] == "http.response.start":
                message["headers"] += [
                    (b"cache-control", b"no-store"),
                    (b"x-content-type-options", b"nosniff"),
                    # A native form POST under no-referrer can send Origin: null.
                    (b"referrer-policy", b"same-origin"),
                    (b"content-security-policy",
                     b"default-src 'none'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"),
                ]
            await send(message)

        if (method, path) != ("GET", "/healthz"):
            credentials = headers.getlist("authorization")
            if len(credentials) != 1 or not authorized(credentials[0], self.token):
                reply = Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="KD Checker Demo"'})
                return await reply(scope, receive, secure_send)
            if method not in {"GET", "HEAD"} and headers.getlist("origin") != [self.origin]:
                return await Response(status_code=403)(scope, receive, secure_send)
        if method == "POST" and (path in UPLOADS or path == "/api/reset"):
            if self.gate.locked():
                return await Response(status_code=429)(scope, receive, secure_send)
            async with self.gate:
                handler = self.upload if path in UPLOADS else self.app
                return await handler(scope, receive, secure_send)
        return await self.app(scope, receive, secure_send)


def validate_pdfs(files):
    if not 1 <= len(files) <= MAX_FILES:
        raise HTTPException(400, "Выберите от одного до четырёх PDF")
    if any(len(data) > MAX_FILE for _, _, data in files) or sum(len(data) for _, _, data in files) > MAX_TOTAL:
        raise HTTPException(413, "До 2 МиБ на PDF и 6 МиБ на комплект")
    if any(not name or len(name) > 200 or re.search(r"[/\\\x00-\x1f\x7f]", name)
           or not name.lower().endswith(".pdf") or media != "application/pdf"
           or not data.startswith(b"%PDF-") for name, media, data in files):
        raise HTTPException(415, "Допускаются только PDF")
    return [(name, data) for name, _, data in files]


def analyze_upload(files, config, cancelled=None):
    try:
        findings = run_check(files, config, Limits(seconds=12, memory_mb=256, cpu_seconds=8,
                                                  output_bytes=256 * 1024), cancelled=cancelled)
    except ProcessingUnavailable:
        raise HTTPException(503, "Обработка PDF недоступна") from None
    if any(item["code"] == "KD-000" for item in findings):
        raise HTTPException(422, "Один из PDF не удалось прочитать")
    return findings


def csv_bytes(record):
    out = io.StringIO(newline="")
    writer = csv.writer(out, delimiter=";")
    writer.writerow(FIELDS)
    for issue in record["issues"]:
        writer.writerow(["'" + issue[key] if issue[key].lstrip().startswith(("=", "+", "-", "@"))
                         else issue[key] for key in FIELDS])
    return out.getvalue().encode("utf-8-sig")


def create_app(environ=None):
    env = os.environ if environ is None else environ
    host = env.get("RENDER_EXTERNAL_HOSTNAME", "")
    token = env.get("KD_DEMO_TOKEN", "")
    if (not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.onrender\.com", host)
            or not re.fullmatch(r"[A-Za-z0-9_-]{43,128}", token)
            or env.get("KD_DEMO_ACK") != "anonymized-pdf-only"
            or any(name.startswith(("KD_CHECKLIST_", "SUPABASE_")) for name in env)):
        raise RuntimeError("Invalid demo environment")

    config = json.loads(Path(__file__).with_name("config.json").read_bytes())
    records = {}
    next_id = 1
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(DemoBoundary, host=host, token=token.encode("ascii"))
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=[host], www_redirect=False)

    @app.get("/healthz")
    async def health():
        return Response(status_code=204)

    @app.get("/", response_class=HTMLResponse)
    async def index(run: int | None = None):
        history = "".join(f'<li><a href="/?run={number}">Проверка {number}</a></li>' for number in reversed(records))
        result = ""
        if run in records:
            rows = "".join(
                "<li>" + " · ".join(escape(item[field]) for field in FIELDS) + "</li>"
                for item in records[run]["issues"]
            )
            result = (f'<h2>Проверка {run}</h2><p><a href="/api/runs/{run}/csv">CSV</a> '
                      f'<a href="/api/runs/{run}/xlsx">XLSX</a></p><ul>{rows}</ul>')
        return ('<!doctype html><html lang="ru"><meta charset="utf-8"><title>KD Checker Demo</title>'
                '<main><h1>KD Checker Demo</h1><p>Только обезличенные или синтетические PDF. '
                'До 4 файлов, 2 МиБ на файл, 6 МиБ на комплект.</p>'
                '<form action="/check" method="post" enctype="multipart/form-data">'
                '<label>PDF комплекта <input type="file" name="files" accept="application/pdf,.pdf" '
                'multiple required></label><button>Проверить</button></form>'
                f'<h2>Последние проверки</h2><ul>{history}</ul>{result}'
                '<form action="/api/reset" method="post"><button>Очистить всю историю стенда</button></form>'
                '</main></html>')

    @app.post("/check")
    @app.post("/api/check")
    async def check(request: Request):
        nonlocal next_id
        if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "multipart/form-data":
            raise HTTPException(415, "Требуется multipart с PDF")
        async with request.form(max_files=MAX_FILES, max_fields=0) as form:
            parts = list(form.multi_items())
            if any(key != "files" or not isinstance(item, UploadFile) for key, item in parts):
                raise HTTPException(400, "Требуется поле files")
            files = validate_pdfs([(item.filename, item.content_type, await item.read(MAX_FILE + 1))
                                   for _, item in parts])
        cancelled = request.state.parser_cancelled
        findings = await asyncio.to_thread(analyze_upload, files, config, cancelled)
        if cancelled.is_set():
            raise HTTPException(503, "Обработка прервана")
        number = next_id
        next_id += 1
        record = {"id": number, "created_at": datetime.now(timezone.utc).isoformat(),
                  "files": [name for name, _ in files], "issues": findings}
        records[number] = record
        if len(records) > MAX_RUNS:
            records.pop(next(iter(records)))
        if request.url.path == "/check":
            return RedirectResponse(f"/?run={number}", status_code=303)
        return record

    @app.post("/api/reset")
    async def reset():
        records.clear()
        return RedirectResponse("/", status_code=303)

    @app.get("/api/runs/{number}")
    async def run(number: int):
        if number not in records:
            raise HTTPException(404)
        return records[number]

    @app.get("/api/runs/{number}/{kind}")
    async def export(number: int, kind: str):
        record = await run(number)
        if kind == "csv":
            content, media = csv_bytes(record), "text/csv"
        elif kind == "xlsx":
            content = await asyncio.to_thread(build_excel_report, record)
            media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        else:
            raise HTTPException(404)
        return Response(content, media_type=media,
                        headers={"Content-Disposition": f'attachment; filename="kd-demo.{kind}"'})

    return app


def main():
    import uvicorn
    app = create_app()
    with tempfile.TemporaryDirectory(prefix="kd-render-demo-") as root:
        # Dedicated server process only. Multipart spool and parser work share
        # this instance-private directory, never a desktop or production path.
        previous = tempfile.tempdir
        tempfile.tempdir = root
        try:
            uvicorn.run(app, host="0.0.0.0", port=int(os.environ["PORT"]), workers=1,
                        proxy_headers=False, access_log=False, limit_concurrency=12,
                        timeout_keep_alive=5, timeout_graceful_shutdown=20)
        finally:
            tempfile.tempdir = previous


if __name__ == "__main__":
    main()
