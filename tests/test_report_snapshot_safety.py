"""Offline regression checks for saved Excel report provenance and literal text.

All content is synthetic. Never retrieves live Checklist or opens real KD.
"""
from contextlib import closing
from io import BytesIO

from openpyxl import load_workbook
import pytest

from excel_reports import build_excel_report


PREFIXES = ("=", "+", "-", "@")


def saved_run(*, checklist=None):
    issue = {
        "severity": "warning",
        "code": "KD-012",
        "file": "=HYPERLINK(\"https://invalid.example\",\"synthetic\").pdf",
        "location": "+1+2",
        "message": "-1+2",
        "recommendation": "@SUM(1,1)",
    }
    result = {
        "id": 7,
        "created_at": "2026-01-01T00:00:00+00:00",
        "files": ["=HYPERLINK(\"https://invalid.example\",\"synthetic\").pdf"],
        "issues": [issue],
        "finding_states": [{
            "issue_index": 0,
            "status": "rejected",
            "comment": "=1+1",
            "updated_at": "2026-01-02T00:00:00+00:00",
        }],
    }
    if checklist is not None:
        result["checklist"] = checklist
    return result


def synthetic_checklist(status):
    return {
        "status": status,
        "reason": "=UNTRUSTED",
        "revision": "+unexpected",
        "retrieved_at": "@timestamp",
        "reviews": [{
            "item_id": 17,
            "section_title": "=Section",
            "title": "+Requirement",
            "status": "manual_review",
            "explanation": "-Untrusted",
            "comment": "@Comment",
            "url": "=HYPERLINK(\"https://invalid.example\")",
        }],
    }


def test_saved_run_findings_are_literal_and_keep_engineer_decision():
    with closing(load_workbook(BytesIO(build_excel_report(saved_run())))) as book:
        sheet = book["Замечания"]
        assert sheet["B2"].value == "Предупреждение"
        assert sheet["C2"].value == "KD-012"
        assert sheet["D2"].value.startswith("=")
        assert sheet["E2"].value == "+1+2"
        assert sheet["F2"].value == "-1+2"
        assert sheet["G2"].value == "@SUM(1,1)"
        assert sheet["H2"].value == "Отклонено с обоснованием"
        assert sheet["I2"].value == "=1+1"
        assert sheet["J2"].value == "2026-01-02T00:00:00+00:00"
        for row in sheet.iter_rows(min_row=2, max_row=2, min_col=2, max_col=10):
            assert all(cell.data_type == "s" for cell in row)
        assert book["Общие сведения"]["B3"].value == "2026-01-01T00:00:00+00:00"
        assert not any(cell.data_type == "f" for table in book for row in table for cell in row)


@pytest.mark.parametrize("prefix", PREFIXES)
def test_every_untrusted_prefix_stays_string_in_finding(prefix):
    sample = saved_run()
    for field in ("file", "location", "message", "recommendation"):
        sample["issues"][0][field] = prefix + "1+2"
    sample["finding_states"][0]["comment"] = prefix + "1+2"
    with closing(load_workbook(BytesIO(build_excel_report(sample)))) as book:
        sheet = book["Замечания"]
        for column in ("D", "E", "F", "G", "I"):
            cell = sheet[f"{column}2"]
            assert cell.value == prefix + "1+2"
            assert cell.data_type == "s"


def test_verified_checklist_is_historical_and_all_fields_are_inert():
    run = saved_run(checklist=synthetic_checklist("verified"))
    with closing(load_workbook(BytesIO(build_excel_report(run)))) as book:
        checklist = book["EPLAN Checklist"]
        assert checklist["B2"].value == "Актуальный список получен на момент проверки"
        assert checklist["B3"].value == "=UNTRUSTED"
        assert checklist["B4"].value == "+unexpected"
        assert checklist["B5"].value == "@timestamp"
        assert checklist["B8"].value == "=Section"
        assert checklist["C8"].value == "+Requirement"
        assert checklist["E8"].value == "-Untrusted"
        assert checklist["F8"].value == "@Comment"
        assert checklist["G8"].value.startswith("=HYPERLINK")
        for position in ("B3", "B4", "B5", "B8", "C8", "E8", "F8", "G8"):
            assert checklist[position].data_type == "s"
        assert not any(cell.data_type == "f" for row in checklist for cell in row)


@pytest.mark.parametrize("status", ["unavailable", "not_configured"])
def test_failed_or_unconfigured_checklist_never_exports_old_review_rows(status):
    run = saved_run(checklist=synthetic_checklist(status))
    with closing(load_workbook(BytesIO(build_excel_report(run)))) as book:
        checklist = book["EPLAN Checklist"]
        assert checklist.max_row == 7  # Metadata + headings only, no stale rows
        assert checklist["B3"].value == "=UNTRUSTED"
        assert checklist["B3"].data_type == "s"
        assert not any(cell.value == "+Requirement" for row in checklist for cell in row)


def test_illegal_xml_controls_removed_but_literal_formula_prefix_preserved():
    run = saved_run()
    run["issues"][0]["message"] = "=bad\x00\x01\r\nnext\rline"
    with closing(load_workbook(BytesIO(build_excel_report(run)))) as book:
        value = book["Замечания"]["F2"]
        assert value.value == "=bad\nnext\nline"
        assert value.data_type == "s"
