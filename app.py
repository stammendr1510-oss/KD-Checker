"""Self-hosted KD Checker MVP. Start: python app.py"""
from __future__ import annotations
import csv
import io
import json
import re
from typing import Literal
from pathlib import Path
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, model_validator
from extractors import SUPPORTED, extract
from engine import analyze
from kd_crosscheck import designation_type, pair_key, run as crosscheck, check_spec_duplicates
from storage import (get_run, list_runs, save_run, update_finding, finding_history,
                     FindingConflict, FindingNotFound)
from issue_tracking import MAX_COMMENT, validate_decision
from excel_reports import build_excel_report
from set_completeness import unpaired_document_findings

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "config.json"
MAX_FILE = 20 * 1024 * 1024
MAX_TOTAL = 100 * 1024 * 1024
MAX_FILES = 100
app = FastAPI(title="Проверка КД", version="0.2.0")
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")

class ConfigUpdate(BaseModel):
    require_designation_in_filename: bool
    designation_pattern: str
    detect_duplicate_designations: bool
    require_pdf_text: bool
    min_pdf_text_chars: int
    require_excel_headers: bool
    specification_headers: list[str]
    check_specification_references: bool
    check_duplicate_positions: bool
    warn_empty_dxf: bool


class FindingUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    status: Literal['new', 'in_progress', 'resolved', 'rejected']
    comment: str = Field(max_length=MAX_COMMENT)
    expected_version: int = Field(ge=0, strict=True)

    @model_validator(mode='after')
    def validate_status(self):
        validate_decision(self.status, self.comment)
        return self


def load_cfg():
    return json.loads(CONFIG.read_text(encoding="utf-8"))

@app.get("/")
def index():
    return FileResponse(ROOT / "static" / "index.html")

@app.get("/api/config")
def get_config():
    return load_cfg()

@app.put("/api/config")
def set_config(cfg: ConfigUpdate):
    data = cfg.model_dump()
    if len(data["designation_pattern"]) > 160 or not data["designation_pattern"]:
        raise HTTPException(400, "Шаблон должен содержать 1–160 символов")
    try:
        re.compile(data["designation_pattern"])
    except re.error as e:
        raise HTTPException(400, "Некорректное регулярное выражение") from e
    if not (0 <= data["min_pdf_text_chars"] <= 10000):
        raise HTTPException(400, "Порог PDF должен быть от 0 до 10000")
    if not (1 <= len(data["specification_headers"]) <= 12) or any(not x.strip() or len(x) > 60 for x in data["specification_headers"]):
        raise HTTPException(400, "Укажите 1–12 названий столбцов")
    CONFIG.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True}

@app.post("/api/check")
async def check(files: list[UploadFile] = File(...)):
    if len(files) > MAX_FILES:
        raise HTTPException(413, f"Не более {MAX_FILES} файлов за проверку")
    docs, errors, total = [], [], 0
    pdf_sources = []
    for uploaded in files:
        name = Path((uploaded.filename or "").replace("\\", "/")).name
        ext = Path(name).suffix.lower()
        if not name or ext not in SUPPORTED:
            errors.append({"file": name or "(без имени)", "message": "Формат не поддерживается"})
            continue
        content = await uploaded.read(MAX_FILE + 1)
        total += len(content)
        if len(content) > MAX_FILE or total > MAX_TOTAL:
            raise HTTPException(413, "Превышен лимит: 20 МБ на файл, 100 МБ суммарно")
        try:
            docs.append(extract(name, content))
            if ext == ".pdf" and designation_type(name):
                pdf_sources.append((name, designation_type(name), pair_key(name), content))
        except Exception:
            errors.append({"file":name, "message":"Не удалось прочитать файл: проверьте формат или повреждение"})
        finally:
            await uploaded.close()
    if not docs and not errors:
        raise HTTPException(400, "Выберите файлы")
    issues = analyze(docs, load_cfg())
    issues.extend(unpaired_document_findings([name for name, _, _, _ in pdf_sources]))
    for pe_name, pe_kind, _, pe_content in pdf_sources:
        if pe_kind == 'ПЭ3':
            issues.extend(check_spec_duplicates(pe_name, pe_content))
    for name, kind, key, content in pdf_sources:
        if kind != "Э3":
            continue
        for pe_name, pe_kind, pe_key, pe_content in pdf_sources:
            if pe_kind == "ПЭ3" and pe_key == key:
                issues.extend(crosscheck(name, content, pe_name, pe_content))
    for err in errors:
        issues.append({"severity":"error", "code":"KD-000", "file":err["file"], "location":"Загрузка", "message":err["message"], "recommendation":"Предоставьте корректный файл поддерживаемого формата"})
    run_id = save_run([d.name for d in docs] + [e["file"] for e in errors], issues)
    summary = {k:sum(i["severity"] == k for i in issues) for k in ("error", "warning", "info")}
    return {"id":run_id, "files":[{"name":d.name, "type":d.ext, "pages":d.pages, "sheets":len(d.sheets), "entities":d.entities} for d in docs], "issues":issues, "summary":summary}

@app.get("/api/runs")
def runs():
    return list_runs()

@app.get("/api/runs/{run_id}")
def run(run_id: int):
    r = get_run(run_id)
    if r is None:
        raise HTTPException(404, "Проверка не найдена")
    return r

@app.get("/api/runs/{run_id}/csv")
def export_csv(run_id: int):
    r = get_run(run_id)
    if r is None:
        raise HTTPException(404, "Проверка не найдена")
    out = io.StringIO()
    writer = csv.writer(out, delimiter=";")
    writer.writerow(["Важность", "Код", "Файл", "Место", "Замечание", "Рекомендация"])
    for item in r["issues"]:
        # Prefix spreadsheet formulas to prevent CSV formula injection.
        fields = [item[k] for k in ("severity", "code", "file", "location", "message", "recommendation")]
        writer.writerow(["'"+v if isinstance(v, str) and v.lstrip().startswith(("=", "+", "-", "@")) else v for v in fields])
    data = "\ufeff" + out.getvalue()
    return StreamingResponse(iter([data.encode("utf-8")]), media_type="text/csv; charset=utf-8", headers={"Content-Disposition":f'attachment; filename="kd-report-{run_id}.csv"'})

@app.patch('/api/runs/{run_id}/findings/{finding_id}')
def change_finding(run_id: int, finding_id: int, change: FindingUpdate):
    try:
        return update_finding(run_id, finding_id, change.status, change.comment, change.expected_version)
    except FindingNotFound:
        raise HTTPException(404, 'Замечание не найдено в этой проверке')
    except FindingConflict:
        raise HTTPException(409, 'Замечание уже изменено. Откройте проверку заново и сравните изменения перед сохранением')


@app.get('/api/runs/{run_id}/findings/{finding_id}/history')
def get_finding_history(run_id: int, finding_id: int):
    try:
        return finding_history(run_id, finding_id)
    except FindingNotFound:
        raise HTTPException(404, 'Замечание не найдено в этой проверке')


@app.get("/api/runs/{run_id}/xlsx")
def export_excel(run_id: int):
    r = get_run(run_id)
    if r is None:
        raise HTTPException(404, "Проверка не найдена")
    return Response(
        content=build_excel_report(r),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="kd-report-{run_id}.xlsx"'},
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8765)


