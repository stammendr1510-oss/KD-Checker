"""KD-PE-107: universal visually-confusable designation candidates."""
import pytest

import kd_crosscheck as cross
from confusable_designations import (
    confusable_findings,
    confusable_label,
    find_candidates,
    model_tokens,
)
from kd_pe_107_reference import scheme_pdf, table_pdf


@pytest.mark.parametrize("left, right, label", [
    ("SO1.PLC.01", "S01.PLC.01", "O/0"),
    ("DI1.MOD.02", "D11.MOD.02", "I/1"),
    ("S5A.MOD.01", "SSA.MOD.01", "5/S"),
    ("B8A.MOD.01", "BBA.MOD.01", "8/B"),
    ("АBC.MOD.01", "ABC.MOD.01", "А/A"),
    ("СDE.MOD.01", "CDE.MOD.01", "С/C"),
    ("КLM.MOD.01", "KLM.MOD.01", "К/K"),
])
def test_confusable_pairs_are_detected(left, right, label):
    assert confusable_label(left, right) == label


@pytest.mark.parametrize("left, right", [
    ("SO1.PLC.01", "SO1.PLC.01"),
    ("SO1.PLC.01", "SO2.PLC.01"),
    ("SO1.PLC.01", "SO1.PLC.02"),
    ("SO1.PLC", "SO1.PLC.01"),
    ("SO1.PLC.01", "S01.PLC.02"),
])
def test_true_matches_and_unrelated_values_are_not_candidates(left, right):
    assert confusable_label(left, right) is None


def test_document_codes_and_numeric_versions_are_not_model_tokens():
    assert model_tokens("ТНБВ.666666.666") == set()
    assert model_tokens("1.2.3") == set()
    assert model_tokens("SO1.PLC.01") == {"SO1.PLC.01"}


def test_both_spellings_on_both_sides_are_not_a_mismatch():
    assert find_candidates({"SO1.PLC.01", "S01.PLC.01"},
                           {"SO1.PLC.01", "S01.PLC.01"}) == []


def test_candidate_is_informational_and_preserves_source_values():
    e3 = scheme_pdf(["SO1.PLC.01"])
    pe3 = table_pdf([("K1", 1, "PLC module S01.PLC.01")])
    findings = cross.run("ref.Э3.pdf", e3, "ref.ПЭ3.pdf", pe3)
    candidates = [finding for finding in findings if finding["code"] == "KD-PE-107"]
    assert len(candidates) == 1
    finding = candidates[0]
    assert finding["severity"] == "info"
    assert "SO1.PLC.01" in finding["message"]
    assert "S01.PLC.01" in finding["message"]
    assert "O/0" in finding["message"]
    assert "не заменяются автоматически" in finding["message"]
    assert "details" not in finding


def test_no_candidate_without_a_model_token_in_pe3():
    rows = [{
        "page": 1, "entries": {"K1"}, "quantity": 1, "unknown": [],
        "row_band": (70.0, 120.0), "table_row": 1, "name": "",
        "name_confidence": "low", "source": "K1", "page_size_pt": [600.0, 800.0],
    }]
    assert confusable_findings("a.Э3.pdf", "a.ПЭ3.pdf", {"SO1.PLC.01": {1}}, rows) == []
