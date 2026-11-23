"""KD-PE-107 reporting contract: JSON validation, Excel export, rule catalog."""
from io import BytesIO

from openpyxl import load_workbook

from excel_reports import build_excel_report
from parser_process import _validate_result
from quality.rule_catalog import RULES


def issue(code, severity="warning", file="demo.ПЭ3.pdf"):
    return {
        "severity": severity,
        "code": code,
        "file": file,
        "location": "ПЭ3: лист PDF 1, строка таблицы 2",
        "message": f"{code}: synthetic engineering message",
        "recommendation": "Check the source document; no automatic change is performed.",
    }


def test_parser_worker_accepts_plain_new_codes():
    result = {
        "status": "ok",
        "issues": [issue("KD-PE-106"), issue("KD-PE-107", severity="info")],
    }
    accepted = _validate_result(result, result_version=1, known_files={"demo.ПЭ3.pdf"})
    assert [item["code"] for item in accepted] == ["KD-PE-106", "KD-PE-107"]


def test_new_codes_are_registered_in_the_rule_catalog():
    assert RULES["KD-PE-106"].direction == "duplicates"
    assert RULES["KD-PE-107"].direction == "designations"


def test_excel_report_contains_every_required_engineering_field():
    run = {
        "id": 107,
        "created_at": "2026-11-23T00:00:00Z",
        "files": ["demo.Э3.pdf", "demo.ПЭ3.pdf"],
        "issues": [issue("KD-PE-106"), issue("KD-PE-107", severity="info")],
        "finding_states": [],
    }
    with BytesIO(build_excel_report(run)) as stream:
        workbook = load_workbook(stream)
    sheet = workbook["Замечания"]
    rows = list(sheet.iter_rows(min_row=2, max_col=7, values_only=True))
    by_code = {row[2]: row for row in rows}
    for code in ("KD-PE-106", "KD-PE-107"):
        row = by_code[code]
        assert row[3] == "demo.ПЭ3.pdf"
        assert "лист PDF 1" in row[4]
        assert code in row[5]
        assert "Check the source document" in row[6]
