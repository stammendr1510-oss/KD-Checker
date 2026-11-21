"""Strict contract tests for the KD-PE-105 details schema v1."""
import copy

import pytest

from e3_pe3_inventory import valid_details
from parser_process import ProcessingUnavailable, _validate_result


E3 = "ABCD.123456.001.Э3.pdf"
PE3 = "ABCD.123456.001.ПЭ3.pdf"
FILES = {E3, PE3}


def issue(**changes):
    base = {
        "severity": "warning",
        "code": "KD-PE-105",
        "file": PE3,
        "location": "Э3: листы PDF 1, 2; ПЭ3: лист PDF 2, строка таблицы 5",
        "message": "На схеме обнаружены обозначения, отсутствующие в перечне элементов.",
        "recommendation": "Проверьте перечень и подтвердите решение инженером.",
    }
    base.update(changes)
    return base


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
        "missing": [{
            "designation": "3TA2",
            "raw": "3TA2",
            "pages": [1, 2],
            "kinds": ["label", "reference"],
            "evidence": [
                {
                    "page": 1,
                    "kind": "label",
                    "context": "3TA2",
                    "bbox_pt": [60.0, 90.0, 78.0, 96.0],
                    "page_size_pt": [600.0, 800.0],
                },
                {"page": 2, "kind": "reference", "context": "3TA2:И1 - 3X1:3"},
            ],
        }],
        "ambiguous": [],
        "limits": {"max_pages": 100, "max_listed": 1000, "truncated_list": False},
    }
    base.update(changes)
    return base


def test_valid_details_are_accepted():
    assert valid_details(details(), FILES, issue()) is True


@pytest.mark.parametrize("mutate", [
    lambda value: value.update(schema_version=2),
    lambda value: value.update(kind="other"),
    lambda value: value.update(status="other"),
    lambda value: value.update(missing=[]),
    lambda value: value["missing"][0].update(evidence=[
        {"page": 3, "kind": "label", "context": "3TA2"}
    ]),
    lambda value: value["missing"][0]["evidence"][0].update(bbox_pt=[700.0, 90.0, 720.0, 96.0]),
    lambda value: value.update(pe3_row_band_pt=[70.0, 900.0]),
    lambda value: value.update(type_family="TA" * 20),
    lambda value: value.update(limits={"max_pages": 1, "max_listed": 1, "truncated_list": False}),
    lambda value: value.update(partial=True),
    lambda value: value.update(status="not_evaluated"),
    lambda value: value.update(e3_document="ABCD.123456.001.ПЭ3.pdf"),
    lambda value: value.update(pe3_document="ABCD.123456.002.ПЭ3.pdf"),
])
def test_invalid_details_are_rejected(mutate):
    value = details()
    mutate(value)
    assert valid_details(value, FILES, issue()) is False


def test_details_require_matching_issue_file_and_severity_consistency():
    assert valid_details(details(), FILES, issue(file=E3)) is False
    assert valid_details(details(partial=True), FILES, issue(severity="warning")) is False
    assert valid_details(details(status="not_evaluated"), FILES, issue(severity="warning")) is False
    assert valid_details(details(), FILES, issue(severity="error")) is False


def test_worker_accepts_valid_kd_105_details():
    value = {"status": "ok", "issues": [issue(details=details())]}
    result = _validate_result(value, 1, FILES)
    assert result[0]["code"] == "KD-PE-105"
    assert result[0]["details"]["schema_version"] == 1


@pytest.mark.parametrize("mutate", [
    lambda value: value["issues"][0].pop("details"),
    lambda value: value["issues"][0].update(evidence={"schema_version": 1}),
    lambda value: value["issues"][0]["details"].update(schema_version=2),
    lambda value: value["issues"][0]["details"].update(missing=[]),
    lambda value: value["issues"][0]["details"].update(e3_document="other.pdf"),
])
def test_worker_rejects_missing_or_malformed_kd_105_details(mutate):
    value = {"status": "ok", "issues": [issue(details=details())]}
    mutate(value)
    with pytest.raises(ProcessingUnavailable):
        _validate_result(value, 1, FILES)


def test_worker_rejects_details_on_other_codes():
    value = {"status": "ok", "issues": [issue(code="KD-PE-104", details=details())]}
    with pytest.raises(ProcessingUnavailable):
        _validate_result(value, 1, FILES)


def test_details_copy_is_independent():
    first = details()
    second = copy.deepcopy(first)
    second["missing"][0]["designation"] = "3TA3"
    assert first["missing"][0]["designation"] == "3TA2"
