"""Boundary matrix of existing deterministic rules on synthetic in-memory documents.

No access to live engineering data, config.json, services or SQLite.
"""
from copy import deepcopy

import pytest

from engine import analyze
from extractors import Document


CONFIG = {
    "designation_pattern": r"[A-Z]{3}\.\d{3}",
    "require_designation_in_filename": True,
    "detect_duplicate_designations": True,
    "require_pdf_text": True,
    "min_pdf_text_chars": 12,
    "require_excel_headers": True,
    "specification_headers": ["Позиция", "Обозначение"],
    "check_specification_references": True,
    "check_duplicate_positions": True,
    "warn_empty_dxf": True,
}


def codes(documents, **overrides):
    return [
        result["code"]
        for result in analyze(documents, CONFIG | overrides)
    ]


def spreadsheet(rows, name="ABC.001.csv", sheet="Таблица"):
    return Document(name=name, ext=".csv", sheets=[{"name": sheet, "rows": rows}])


def test_recognized_valid_filename_does_not_trigger_filename_warning():
    doc = Document(name="ABC.001.pdf", ext=".pdf", text="Synthetic PDF text layer")
    assert "KD-001" not in codes([doc])
    assert "KD-002" not in codes([doc])


def test_filename_rule_can_be_switched_off_without_hiding_pdf_warning():
    doc = Document(name="unknown.pdf", ext=".pdf", text="")
    assert {"KD-001", "KD-002"} <= set(codes([doc]))
    result = codes([doc], require_designation_in_filename=False)
    assert "KD-001" not in result and "KD-002" in result


@pytest.mark.parametrize("count,expected", [(0, True), (11, True), (12, False), (25, False)])
def test_pdf_text_boundary(count, expected):
    doc = Document(name="ABC.001.pdf", ext=".pdf", text="x" * count)
    assert ("KD-002" in codes([doc])) is expected


def test_reader_limit_note_is_information_not_engineering_defect():
    doc = Document(
        name="ABC.001.pdf", ext=".pdf",
        text="Enough synthetic text", notes=["Synthetic: only first 100 pages"],
    )
    result = analyze([doc], CONFIG)
    notes = [x for x in result if x["code"] == "KD-005"]
    assert len(notes) == 1
    assert notes[0]["severity"] == "info"
    assert notes[0]["location"] == "Ограничение"


def test_empty_dxf_warning_and_dwg_limited_analysis_are_separate():
    docs = [
        Document(name="ABC.001.dxf", ext=".dxf", entities=0),
        Document(name="DEF.002.dwg", ext=".dwg"),
    ]
    assert "KD-003" in codes(docs)
    assert "KD-004" in codes(docs)
    assert "KD-003" not in codes(docs, warn_empty_dxf=False)


def test_duplicate_positions_are_scoped_to_each_sheet_not_whole_workbook():
    doc = Document(name="ABC.001.xlsx", ext=".xlsx", sheets=[
        {"name": "Лист 1", "rows": [
            ["Позиция", "Обозначение"], ["1", ""], ["1", ""]]},
        {"name": "Лист 2", "rows": [
            ["Позиция", "Обозначение"], ["1", ""]]},
    ])
    result = analyze([doc], CONFIG)
    repeats = [x for x in result if x["code"] == "KD-011"]
    assert len(repeats) == 1
    assert repeats[0]["location"] == "Лист 1!3"
    assert repeats[0]["severity"] == "error"


def test_header_absence_is_a_warning_only_when_enabled():
    doc = spreadsheet([["Артикул", "Название"], ["1", "Тест"]])
    assert "KD-010" in codes([doc])
    assert "KD-010" not in codes([doc], require_excel_headers=False)


def test_reference_hint_depends_on_explicit_file_name_match():
    spec = spreadsheet([["Позиция", "Обозначение"], ["1", "DEF.002"]])
    attached = Document(name="DEF.002.pdf", ext=".pdf", text="Enough synthetic text")
    assert "KD-012" in codes([spec])
    assert "KD-012" not in codes([spec, attached])
    assert "KD-012" not in codes([spec], check_specification_references=False)


def test_cross_format_files_do_not_trigger_duplicate_designation():
    first = Document(name="ABC.001.pdf", ext=".pdf", text="Enough synthetic text")
    second = Document(name="ABC.001.dxf", ext=".dxf", entities=1)
    assert "KD-020" not in codes([first, second])


def test_same_format_duplicate_is_warning_but_does_not_modify_inputs():
    documents = [
        Document(name="ABC.001.pdf", ext=".pdf", text="Enough synthetic text"),
        Document(name="ABC.001 copy.pdf", ext=".pdf", text="Enough synthetic text"),
    ]
    original = deepcopy(documents)
    result = analyze(documents, CONFIG)
    repeats = [x for x in result if x["code"] == "KD-020"]
    assert len(repeats) == 2
    assert all(x["severity"] == "warning" for x in repeats)
    assert documents == original
    assert "KD-020" not in codes(documents, detect_duplicate_designations=False)


def test_all_findings_include_actionable_location_and_recommendation():
    docs = [
        Document(name="unexpected.pdf", ext=".pdf", text=""),
        spreadsheet([["Позиция", "Обозначение"], ["1", ""]]),
    ]
    findings = analyze(docs, CONFIG)
    assert findings
    for item in findings:
        assert set(item) == {
            "severity", "code", "file", "location", "message", "recommendation",
        }
        assert item["severity"] in {"error", "warning", "info"}
        assert all(isinstance(value, str) and value for value in item.values())
