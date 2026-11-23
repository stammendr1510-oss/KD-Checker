"""Run the protected parser's analysis entry point with synthetic file bytes."""
import json
from pathlib import Path

import pytest

from secure_backend import check_documents
from test_e3_pe3_match_source_pages import pdf_pages
from test_full_set_completeness import pdf as specification_pdf
from test_pe3_duplicates import pe3


BASE = "ABCD.123456.001"
SP_REFERENCE = "DEFG.987654.002.ВО"


@pytest.fixture
def config():
    return json.loads((Path(__file__).resolve().parents[1] / "config.json").read_text(encoding="utf-8"))


@pytest.fixture
def bundle():
    return [
        (f"{BASE}.ВО.pdf", pdf_pages("SYNTHETIC ONLY - general arrangement")),
        (f"{BASE}.Э3.pdf", pdf_pages("SYNTHETIC ONLY - 1KM1 XT1")),
        (f"{BASE}.ПЭ3.pdf", pe3([[("1KM1", 1), ("1KM1", 1), ("XT1", 1)]])),
        (f"{BASE}.СП.pdf", specification_pdf([SP_REFERENCE])),
    ]


def test_four_pdf_documents_produce_source_backed_findings(bundle, config):
    findings = check_documents(bundle, config)
    assert sorted(item["code"] for item in findings) == [
        "KD-EM-108-2", "KD-PE-100", "KD-PE-104", "KD-SET-201", "KD-SET-203",
    ]
    expected = {
        "KD-SET-201": ("ВО", "info", "Состав загруженного набора", "4 загруженных PDF",
                       "машинная сводка не подтверждает полноту проекта"),
        "KD-SET-203": ("СП", "info", "Лист PDF 1, строка таблицы 2", SP_REFERENCE,
                       "не доказывает отсутствия документа"),
        "KD-PE-104": ("ПЭ3", "warning", "Лист 1, строка таблицы 3; Лист 1, строка таблицы 4",
                      "«1KM1» распознано в 2 разных строках", "не подтверждённая ошибка КД"),
        "KD-PE-100": ("ПЭ3", "info", "Сопоставление Э3 ↔ ПЭ3",
                      "1KM1: лист(ы) PDF 1; XT1: лист(ы) PDF 1", "выборочная машинная сверка"),
        "KD-EM-108-2": ("СП", "warning", "лист 1, строка 2", SP_REFERENCE,
                        "Проверьте комплектность загрузки"),
    }
    for item in findings:
        kind, severity, location, reason, qualification = expected[item["code"]]
        assert item["file"] == f"{BASE}.{kind}.pdf"
        assert item["severity"] == severity
        assert location in item["location"]
        assert reason in item["message"]
        assert qualification in item["recommendation"]


def test_ambiguous_scheme_versions_skip_pair_analysis(bundle, config):
    bundle.append((f"{BASE}.Э3.pdf", pdf_pages("SYNTHETIC ONLY - different version 2KM1")))
    findings = check_documents(bundle, config)
    codes = {item["code"] for item in findings}
    assert {"KD-SET-103", "KD-PE-104"} <= codes
    assert not {"KD-PE-100", "KD-PE-101", "KD-PE-102"} & codes
    ambiguity = next(item for item in findings if item["code"] == "KD-SET-103")
    assert ambiguity["severity"] == "info"
    assert "Сверка Э3 ↔ ПЭ3" in ambiguity["message"]
    assert "Э3: 2, ПЭ3: 1" in ambiguity["message"]
    assert "не доказывает" in ambiguity["recommendation"]
