"""Block 5: engineer-facing KD-PE-105 evidence, Excel and history contracts.

All scenarios are synthetic.  No enterprise PDF, no network and no confidential
document content is used.
"""
from contextlib import closing
from copy import deepcopy
import csv
from io import BytesIO, StringIO
import json
from pathlib import Path

from fastapi.testclient import TestClient
from openpyxl import load_workbook
import pytest

import app as web
from e3_pe3_inventory import Mention, compare_inventories
from excel_reports import build_excel_report
from kd_pe_105_evidence import (
    build_evidence_view,
    enrich_run,
    format_evidence_text,
)
import storage


ROOT = Path(__file__).resolve().parents[1]
E3 = "ABCD.123456.001.Э3.pdf"
PE3 = "ABCD.123456.001.ПЭ3.pdf"


def missing_item(designation, *, pages=(1, 2), kinds=("label",), contexts=None):
    contexts = list(contexts or [designation])
    return {
        "designation": designation,
        "raw": designation,
        "pages": list(pages),
        "kinds": list(kinds),
        "evidence": [
            {"page": pages[0], "kind": kinds[0], "context": context}
            for context in contexts
        ],
    }


def details(**changes):
    base = {
        "schema_version": 1,
        "kind": "e3_pe3_missing_designations",
        "status": "evaluated",
        "reason": "quantity_mismatch",
        "e3_document": E3,
        "pe3_document": PE3,
        "e3_pages": [1, 2],
        "pe3_page": 2,
        "pe3_table_row": 5,
        "pe3_row_band_pt": [70.0, 130.0],
        "pe3_page_size_pt": [600.0, 800.0],
        "pe3_quantity": 9,
        "pe3_entries_count": 7,
        "pe3_name": "Трансформатор тока измерительный",
        "pe3_name_confidence": "medium",
        "pe3_source": "1TA1...1TA3; 2TA1...2TA3; 3TA1",
        "type_family": "TA",
        "type_name": "трансформатор тока",
        "confidence": "high",
        "partial": False,
        "truncated": False,
        "missing": [missing_item("3TA2"), missing_item("3TA3")],
        "ambiguous": [],
        "limits": {"max_pages": 100, "max_listed": 1000, "truncated_list": False},
    }
    base.update(changes)
    return base


def issue(details_value=None, *, message=None, location=None, recommendation=None):
    details_value = details_value or details()
    if message is None:
        message = (
            "На схеме обнаружены обозначения, отсутствующие в перечне элементов.\n"
            "Обнаружено обозначений: 2.\n"
            "Первые обозначения: 3TA2, 3TA3."
        )
    if location is None:
        location = "Э3: листы PDF 1, 2; ПЭ3: лист PDF 2, строка таблицы 5"
    if recommendation is None:
        recommendation = "Проверьте перечень и подтвердите решение инженером."
    return {
        "severity": "warning",
        "code": "KD-PE-105",
        "file": PE3,
        "location": location,
        "message": message,
        "recommendation": recommendation,
        "details": details_value,
    }


def row(entries, *, page=2, table_row=5, quantity=None):
    entries = set(entries)
    return {
        "page": page,
        "source": ";".join(sorted(entries)),
        "quantity": len(entries) if quantity is None else quantity,
        "entries": entries,
        "unknown": [],
        "row_band": (70.0, 130.0),
        "table_row": table_row,
        "name": "",
        "name_confidence": "low",
        "page_size_pt": [600.0, 800.0],
        "pdf_truncated": False,
        "duplicate_eligible": False,
    }


# ---------------------------------------------------------------------------
# Control examples: TA, KM, X/XT
# ---------------------------------------------------------------------------

def test_ta_evidence_shows_designations_documents_and_confirmed_row():
    view = build_evidence_view(issue())

    assert view["detected"] == ["3TA2", "3TA3"]
    assert view["detected_count"] == 2
    assert view["e3"]["document"] == E3
    assert view["e3"]["pages_text"] == "1, 2"
    assert view["pe3"]["document"] == PE3
    assert view["pe3"]["page"] == 2
    assert view["pe3"]["table_row"] == 5
    assert view["pe3"]["row_confirmed"] is True
    assert view["reason"] == "quantity_mismatch"
    assert view["confidence"] == "high"
    assert view["classification"]["result_kind"] == "product"
    assert any("Количество в строке ПЭ3 (9)" in item for item in view["confidence_basis"])
    assert view["recommendations"]

    text = format_evidence_text(issue())
    assert "3TA2" in text and "3TA3" in text
    assert "лист PDF 2" in text and "строка таблицы 5" in text
    assert "Характер расхождения" in text
    assert "Почему такая достоверность" in text


def test_km_evidence_explains_why_the_row_is_not_conclusive():
    value = details(
        reason="row_matched_without_coverage",
        confidence="medium",
        pe3_quantity=1,
        pe3_entries_count=1,
        pe3_source="3KM1",
        missing=[missing_item("3KM2")],
        type_family="KM",
        type_name="контактор",
    )
    view = build_evidence_view(issue(value))

    assert view["reason"] == "row_matched_without_coverage"
    assert "не доказывает принадлежность" in view["reason_label"]
    assert view["confidence"] == "medium"
    assert view["pe3"]["table_row"] == 5
    missing = " ".join(view["what_is_missing"])
    assert "принадлежност" in missing
    assert "диапазон" in missing


def test_reference_only_is_explained_as_contact_or_terminal():
    value = details(
        status="not_evaluated",
        reason="reference_only",
        confidence="low",
        pe3_page=None,
        pe3_table_row=None,
        pe3_row_band_pt=None,
        pe3_page_size_pt=None,
        pe3_quantity=None,
        pe3_entries_count=None,
        pe3_name="",
        pe3_source="",
        type_family="X",
        type_name="клеммник/соединение",
        missing=[],
        ambiguous=[{
            "designation": "1KM1",
            "raw": "1KM1:A1",
            "pages": [1],
            "kinds": ["reference"],
            "reason": "needs_context",
        }],
    )
    view = build_evidence_view(issue(value))

    assert view["detected"] == []
    assert view["classification"]["result_kind"] == "contact_or_terminal"
    assert "Вывод, контакт" in view["classification"]["label"]
    assert view["ambiguous"][0]["designation"] == "1KM1"
    assert "контекст" in view["ambiguous"][0]["reason_label"]
    assert any("самостоятельным изделием" in item for item in view["what_is_missing"])


def test_undefined_identifier_is_not_presented_as_a_product():
    value = details(
        status="not_evaluated",
        reason="needs_context",
        confidence="low",
        pe3_page=None,
        pe3_table_row=None,
        pe3_row_band_pt=None,
        pe3_page_size_pt=None,
        pe3_quantity=None,
        pe3_entries_count=None,
        pe3_name="",
        pe3_source="",
        type_family="X",
        type_name="клеммник/соединение",
        missing=[],
        ambiguous=[{
            "designation": "X1",
            "raw": "X1",
            "pages": [1],
            "kinds": ["label"],
            "reason": "needs_context",
        }],
    )
    view = build_evidence_view(issue(value))

    assert view["classification"]["result_kind"] == "undefined"
    assert "Неопределённый случай" in view["classification"]["label"]
    assert "контекст" in " ".join(view["what_is_missing"])


def test_terminal_family_product_result_keeps_an_information_note():
    value = details(
        reason="no_matching_pe3_row",
        confidence="medium",
        pe3_page=None,
        pe3_table_row=None,
        pe3_row_band_pt=None,
        pe3_page_size_pt=None,
        pe3_quantity=None,
        pe3_entries_count=None,
        pe3_name="",
        pe3_source="",
        type_family="XT",
        type_name="клеммник",
        missing=[missing_item("XT1")],
    )
    view = build_evidence_view(issue(value))

    assert view["classification"]["result_kind"] == "product"
    assert "клеммникам" in view["classification"]["note"]
    assert view["what_is_missing"]


# ---------------------------------------------------------------------------
# Ambiguity and candidate rows
# ---------------------------------------------------------------------------

def test_row_ambiguous_evidence_lists_multiple_candidates():
    value = details(
        reason="row_ambiguous",
        confidence="low",
        pe3_page=None,
        pe3_table_row=None,
        pe3_row_band_pt=None,
        pe3_page_size_pt=None,
        pe3_quantity=None,
        pe3_entries_count=None,
        pe3_name="",
        pe3_source="",
        type_family="KM",
        type_name="контактор",
        missing=[missing_item("3KM2")],
    )
    candidate_message = (
        "Найдено несколько подходящих строк ПЭ3 (кандидатов: 2); "
        "автоматический выбор строки не выполнен.\n"
        "Кандидаты ПЭ3: лист PDF 2, строка таблицы 5; лист PDF 2, строка таблицы 7."
    )
    candidate_location = (
        "Э3: листы PDF 1; ПЭ3: строка не сопоставлена; "
        "кандидаты: лист PDF 2, строка таблицы 5; лист PDF 2, строка таблицы 7"
    )
    view = build_evidence_view(issue(
        value, message=candidate_message, location=candidate_location,
    ))

    assert view["reason"] == "row_ambiguous"
    assert view["candidate_count"] == 2
    assert len(view["candidates"]) == 2
    assert "строка таблицы 5" in view["candidates"][0]
    assert "строка таблицы 7" in view["candidates"][1]
    assert any("Не хватает единственной подтверждённой строки" in item
               for item in view["what_is_missing"])


def test_producer_adds_candidate_rows_to_message_and_location():
    mentions = [
        Mention("3KM2", "3KM2", 1, "label", "3KM2", "high"),
    ]
    rows = [
        row(["3KM1"], table_row=5),
        row(["3KM3"], table_row=7),
    ]
    findings = compare_inventories(
        E3, PE3, mentions, rows,
        e3_word_count=1,
        e3_pages_processed=1,
        e3_pages_with_text=1,
    )
    assert len(findings) == 1
    finding = findings[0]
    assert finding["details"]["reason"] == "row_ambiguous"
    assert "Кандидаты ПЭ3:" in finding["message"]
    assert "строка таблицы 5" in finding["message"]
    assert "строка таблицы 7" in finding["message"]
    assert "кандидаты:" in finding["location"]

    view = build_evidence_view(finding)
    assert view["candidate_count"] >= 2
    assert len(view["candidates"]) >= 2


# ---------------------------------------------------------------------------
# No unconfirmed coordinates, bounded long lists
# ---------------------------------------------------------------------------

def test_evidence_text_never_exposes_unconfirmed_pdf_coordinates():
    value = details()
    value["missing"][0]["evidence"][0].update({
        "bbox_pt": [11.0, 22.0, 33.0, 44.0],
        "page_size_pt": [600.0, 800.0],
    })
    text = format_evidence_text(issue(value))
    for token in ("bbox", "row_band", "11.0", "22.0", "33.0", "44.0", "70.0", "130.0"):
        assert token not in text

    view = build_evidence_view(issue(value))
    serialized = json.dumps(view, ensure_ascii=False)
    assert "bbox_pt" not in serialized
    assert "pe3_row_band_pt" not in serialized


def test_unconfirmed_table_row_is_stated_instead_of_guessed():
    value = details(pe3_table_row=None, pe3_row_band_pt=None)
    view = build_evidence_view(issue(value))

    assert view["pe3"]["page"] == 2
    assert view["pe3"]["table_row"] is None
    assert view["pe3"]["row_confirmed"] is False
    assert any("не подтверждена" in item for item in view["notes"])
    text = format_evidence_text(issue(value))
    assert "строка таблицы не подтверждена" in text
    assert "строка таблицы 5" not in text


def test_long_designations_and_many_items_are_bounded():
    long_name = "3TA" + "ОченьДлинноеОбозначениеБезПробелов" * 6
    value = details(
        missing=[missing_item(f"{long_name}{number}") for number in range(300)],
    )
    view = build_evidence_view(issue(value))

    assert view["detected_count"] == 300
    assert len(view["detected"]) == 50
    assert len(view["items"]) == 200
    assert "Показаны первые 50" in view["detected_note"]
    text = format_evidence_text(issue(value))
    assert len(text) <= 30000
    assert "Показаны первые 20 из 300" in text
    assert long_name[:40] in text


# ---------------------------------------------------------------------------
# Excel, CSV, API and history compatibility
# ---------------------------------------------------------------------------

def test_excel_keeps_two_additive_columns_and_human_readable_evidence():
    run = {
        "id": 7,
        "created_at": "2026-01-02T03:04:05+00:00",
        "files": [E3, PE3],
        "issues": [issue()],
    }
    with closing(load_workbook(BytesIO(build_excel_report(run)))) as book:
        sheet = book["Замечания"]
        header = list(sheet.values)[0]
        assert header[:10] == (
            "№", "Уровень", "Код", "Документ", "Лист/место", "Описание",
            "Рекомендация", "Статус", "Комментарий инженера", "Дата изменения",
        )
        assert header[10:] == ("Доказательства KD-PE-105", "Детали KD-PE-105 (JSON)")
        text = sheet["K2"].value
        assert "3TA2" in text and "3TA3" in text
        assert "Характер расхождения" in text
        assert "Почему такая достоверность" in text
        assert "Рекомендации инженеру" in text
        assert sheet["K2"].data_type == "s"
        assert json.loads(sheet["L2"].value)["missing"][0]["designation"] == "3TA2"
        assert sheet.auto_filter.ref == "A1:L2"


def test_csv_stays_strictly_six_columns(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DB", tmp_path / "history.sqlite3")
    run_id = storage.save_run([E3, PE3], [issue()])
    with TestClient(web.app) as client:
        response = client.get(f"/api/runs/{run_id}/csv")
        assert response.status_code == 200
        rows = list(csv.reader(StringIO(response.content.decode("utf-8-sig")), delimiter=";"))
    assert rows[0] == ["Важность", "Код", "Файл", "Место", "Замечание", "Рекомендация"]
    assert len(rows[1]) == 6
    assert "3TA2" in rows[1][4]


def test_api_returns_evidence_view_without_changing_stored_schema(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DB", tmp_path / "history.sqlite3")
    source = issue()
    run_id = storage.save_run([E3, PE3], [source])

    with TestClient(web.app) as client:
        payload = client.get(f"/api/runs/{run_id}").json()
    enriched = payload["issues"][0]
    assert enriched["details"] == source["details"]
    assert enriched["evidence_view"]["detected"] == ["3TA2", "3TA3"]

    stored = storage.get_run(run_id)["issues"][0]
    assert stored == source
    assert "evidence_view" not in stored


def test_engineer_decision_and_history_are_preserved(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DB", tmp_path / "history.sqlite3")
    run_id = storage.save_run([E3, PE3], [issue()])
    state = storage.get_run(run_id)["finding_states"][0]
    storage.update_finding(run_id, state["id"], "resolved", "Проверено инженером", 0)

    events = storage.finding_history(run_id, state["id"])
    assert len(events) == 1
    assert events[0]["new_status"] == "resolved"
    assert events[0]["new_comment"] == "Проверено инженером"

    with TestClient(web.app) as client:
        record = client.get(f"/api/runs/{run_id}").json()
        history = client.get(f"/api/runs/{run_id}/findings/{state['id']}/history").json()
    assert record["issues"][0]["evidence_view"]["reason"] == "quantity_mismatch"
    assert record["finding_states"][0]["status"] == "resolved"
    assert history[0]["new_comment"] == "Проверено инженером"


def test_enrich_run_does_not_mutate_the_input_record():
    source = {"id": 1, "issues": [issue()]}
    snapshot = deepcopy(source)
    enriched = enrich_run(source)

    assert source == snapshot
    assert "evidence_view" not in source["issues"][0]
    assert enriched["issues"][0]["evidence_view"]["detected"] == ["3TA2", "3TA3"]


def test_both_interfaces_reference_the_evidence_view():
    legacy = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    workbench = (ROOT / "secure_static" / "workbench.js").read_text(encoding="utf-8")

    for marker in (
        "evidence_view",
        "Характер расхождения",
        "Почему такая достоверность",
        "Чего не хватило для достоверного вывода",
        "Кандидаты ПЭ3",
        "Рекомендации инженеру",
    ):
        assert marker in legacy
        assert marker in workbench
