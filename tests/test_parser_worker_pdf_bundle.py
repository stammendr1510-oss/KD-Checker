"""Exercise the real parser subprocess on generated EPLAN-like PDF inputs."""
from collections import Counter
import json
from pathlib import Path

import pytest

import parser_process
from test_e3_pe3_match_source_pages import pdf_pages
from test_full_set_completeness import pdf as specification_pdf
from test_pe3_duplicates import pe3


BASE = "ABCD.123456.001"
TESTS = Path(__file__).resolve().parent


@pytest.fixture
def bundle():
    return {
        f"{BASE}.ВО.pdf": pdf_pages("SYNTHETIC ONLY - general arrangement"),
        f"{BASE}.Э3.pdf": pdf_pages("SYNTHETIC ONLY - 1KM1 XT1"),
        f"{BASE}.ПЭ3.pdf": pe3([[("1KM1", 1), ("1KM1", 1), ("XT1", 1)]]),
        f"{BASE}.СП.pdf": specification_pdf(["DEFG.987654.002.ВО"]),
    }


@pytest.fixture
def run_worker(tmp_path, monkeypatch):
    monkeypatch.setattr(parser_process, "_TEMP_ROOT", tmp_path)
    config = json.loads((TESTS.parent / "config.json").read_bytes())

    def run(files):
        findings = parser_process.run_check(files, config)
        assert not list(tmp_path.iterdir())
        return findings

    return run


def test_worker_returns_exact_expected_pdf_findings(bundle, run_worker):
    expected = json.loads((TESTS / "fixtures/parser-worker-pdf-findings.json").read_bytes())
    actual = run_worker(list(bundle.items()))
    assert len(expected) == len(actual) == 5
    assert sorted(actual, key=lambda item: item["code"]) == sorted(
        expected, key=lambda item: item["code"]
    )


@pytest.mark.parametrize("broken_pdf", [b"", b"%PDF-1.7\ntruncated"])
def test_worker_reports_unreadable_sp_without_claiming_missing_reference(bundle, run_worker, broken_pdf):
    filename = f"{BASE}.СП.pdf"
    bundle[filename] = broken_pdf
    findings = run_worker(list(bundle.items()))
    assert Counter(item["code"] for item in findings) == {
        "KD-000": 1, "KD-SET-201": 1, "KD-PE-100": 1, "KD-PE-104": 1,
    }
    issue = next(item for item in findings if item["code"] == "KD-000")
    assert issue == {
        "code": "KD-000",
        "severity": "error",
        "file": filename,
        "location": "Загрузка",
        "message": "Не удалось прочитать файл",
        "recommendation": "Проверьте формат и повреждение",
    }


def test_worker_skips_pair_comparison_when_two_e3_documents_differ(bundle, run_worker):
    name = f"{BASE}.Э3.pdf"
    alternate = (name, pdf_pages("SYNTHETIC ONLY - alternate scheme 9KM9"))
    findings = run_worker([*bundle.items(), alternate])
    assert Counter(item["code"] for item in findings) == {
        "KD-020": 2, "KD-SET-102": 1, "KD-SET-103": 1, "KD-SET-201": 1,
        "KD-SET-202": 1, "KD-SET-203": 1, "KD-PE-104": 1,
        "KD-EM-108-2": 1, "KD-EM-109-2": 1,
    }
    ambiguity = next(item for item in findings if item["code"] == "KD-SET-103")
    assert ambiguity["severity"] == "info"
    assert "Э3: 2, ПЭ3: 1" in ambiguity["message"]
    assert "не доказывает" in ambiguity["recommendation"]
    assert all(item["severity"] != "error" for item in findings)
