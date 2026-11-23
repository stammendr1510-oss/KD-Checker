"""KD-PE-106 independent engineering acceptance: repeatable synthetic checks.

Every scenario is synthetic.  The tests pin:
* the eight engineering scenarios A-H and their machine-checkable criteria;
* the exit-code contract (PASS=0, FAIL=1, NOT_VERIFIED=2);
* the difference between a documented conservative rule and a defect;
* that a damaged document or a failed pair analysis is reported explicitly and
  never looks like a successful check.
"""
import fitz
import pytest
from fastapi.testclient import TestClient

import acceptance_stand as stand
import app as web
import storage
from e3_pe3_inventory import valid_details
from identifier_classification import AMBIGUOUS, DEVICE_CANDIDATE, classify_identifier
from kd_pe_105_confidence import assess_finding, validate_combination

BASE = "ABCD.123456.001"


def _pdf_with_text(text="SYNTHETIC ACCEPTANCE PDF"):
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((72, 72), text)
        return doc.tobytes()


@pytest.fixture
def offline_app(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DB", tmp_path / "acceptance.sqlite3")
    monkeypatch.setattr(web, "analyze", lambda docs, cfg: [])
    monkeypatch.setattr(web, "current_checklist_state", lambda: {
        "status": "not_configured", "reason": "Synthetic offline mode",
        "revision": None, "retrieved_at": None, "items": [], "reviews": [],
    })
    return TestClient(web.app)


# ---------------------------------------------------------------------------
# Engineering scenarios A-H
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("key", sorted(stand.SCENARIOS))
def test_engineering_scenario_passes(key):
    result = stand.SCENARIOS[key]()
    assert result.status == stand.PASS, (
        f"{key}: {result.actual}\nожидалось: {result.expected}"
    )
    assert result.engineering_rule
    assert result.criterion
    assert result.expected and result.actual


def test_scenario_b_produces_valid_evidence_for_the_missing_designation():
    result = stand.scenario_b()
    assert result.status == stand.PASS
    assert "missing=['1QF3']" in result.actual
    assert "valid=[True]" in result.actual


def test_scenario_h_keeps_the_source_sheet_of_the_finding():
    result = stand.scenario_h()
    assert result.status == stand.PASS
    assert "e3_pages=[2]" in result.actual


# ---------------------------------------------------------------------------
# Machine-readable report and exit codes
# ---------------------------------------------------------------------------

def test_report_is_machine_readable_and_deterministic():
    first = stand.build_report()
    second = stand.build_report()
    assert first["kind"] == stand.KIND
    assert first["synthetic_only"] is True
    assert first["verdict"] == stand.PASS
    assert first["counts"] == {"total": 8, "passed": 8, "failed": 0, "not_verified": 0}
    # Verdicts must not depend on the generation timestamp.
    assert first["verdict_digest"] == second["verdict_digest"]
    assert stand.exit_code(first) == 0
    assert first["windows11"].startswith("NOT VERIFIED")
    assert first["real_documents"].startswith("NOT VERIFIED")
    assert first["multi_pc"].startswith("NOT VERIFIED")


def test_exit_code_is_non_zero_for_a_failed_scenario(monkeypatch):
    def failed():
        return stand.CaseResult(
            scenario="A", title="Сломанный сценарий",
            engineering_rule="Проверка должна падать явно.",
            expected="PASS", actual="FAIL", criterion="status == PASS",
            status=stand.FAIL,
        )
    monkeypatch.setitem(stand.SCENARIOS, "A", failed)
    report = stand.build_report(["A"])
    assert report["verdict"] == stand.FAIL
    assert stand.exit_code(report) == 1


def test_exit_code_marks_not_verified_instead_of_pass(monkeypatch):
    def broken():
        raise RuntimeError("synthetic failure")
    monkeypatch.setitem(stand.SCENARIOS, "A", broken)
    report = stand.build_report(["A"])
    assert report["verdict"] == stand.NOT_VERIFIED
    assert report["scenarios"][0]["status"] == stand.NOT_VERIFIED
    assert stand.exit_code(report) == 2


# ---------------------------------------------------------------------------
# Documented conservative rules (pinned, not silently "fixed")
# ---------------------------------------------------------------------------

def test_note_word_in_the_same_line_demotes_a_device_and_is_a_known_risk():
    # Pinned behaviour: a note/table word in the extracted line makes the token
    # evidence-only.  This is conservative by design (fewer false warnings) but
    # it can hide a real missing designation if a PDF merges a column header
    # with a data row.  See docs/kd-pe-106-known-limitations.md (KD106-L-01).
    item = classify_identifier("QF1", kind="label", line_text="QF1 Примечание")
    assert item.category != DEVICE_CANDIDATE
    assert item.eligible_for_presence_check is False


def test_unrecognized_families_are_preserved_as_ambiguous_not_guessed():
    # KD106-L-02: TAG does not include K, KA, HL.  These common GOST families
    # are therefore not checked at all.  The token is preserved, never invented.
    for raw in ("K1", "KA1", "HL1"):
        item = classify_identifier(raw, kind="label", line_text=raw)
        assert item.category == AMBIGUOUS
        assert item.requires_context is True


def test_warning_without_a_confirmed_row_band_stays_medium_confidence():
    class Component:
        designation = "1TA1"
        confidence = "high"
        category = DEVICE_CANDIDATE
        classification_confidence = "high"
        mentions = ()

    with_band = {
        "entries": {"1TA1", "1TA2"}, "quantity": 9, "unknown": False,
        "row_band": [(0, 0), (1, 1)], "page": 2, "table_row": 2,
    }
    without_band = dict(with_band, row_band=None, table_row=None)
    confirmed = assess_finding(group=[Component()], row=with_band, reason="quantity_mismatch",
                               evidence="under_expanded", partial=False, truncated=False,
                               candidates=1, family="TA", family_allowed=True)
    unconfirmed = assess_finding(group=[Component()], row=without_band, reason="quantity_mismatch",
                                 evidence="under_expanded", partial=False, truncated=False,
                                 candidates=1, family="TA", family_allowed=True)
    assert (confirmed.severity, confirmed.confidence) == ("warning", "high")
    assert (unconfirmed.severity, unconfirmed.confidence) == ("warning", "medium")
    assert validate_combination(
        status=unconfirmed.status, reason=unconfirmed.reason, confidence=unconfirmed.confidence,
        severity=unconfirmed.severity, partial=False, truncated=False,
        pe3_page=2, pe3_table_row=None,
    ) is True


# ---------------------------------------------------------------------------
# Failed documents and failed analysis are never a silent success
# ---------------------------------------------------------------------------

def test_corrupted_pe3_is_reported_as_an_explicit_limitation(offline_app):
    files = [
        ("files", (f"{BASE}.Э3.pdf", _pdf_with_text(), "application/pdf")),
        ("files", (f"{BASE}.ПЭ3.pdf", b"not a pdf at all", "application/pdf")),
    ]
    response = offline_app.post("/api/check", files=files)
    assert response.status_code == 200
    payload = response.json()
    blocking = [item for item in payload["issues"] if item["severity"] == "error"]
    assert blocking, payload["issues"]
    assert any(item["code"] == "KD-000" for item in blocking)
    assert all(item["code"] != "KD-PE-105" for item in payload["issues"])


def test_failed_pair_analysis_is_reported_instead_of_http_500(offline_app, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("private parser detail that must not leak")

    monkeypatch.setattr(web, "crosscheck", boom)
    files = [
        ("files", (f"{BASE}.Э3.pdf", _pdf_with_text("1QF1"), "application/pdf")),
        ("files", (f"{BASE}.ПЭ3.pdf", _pdf_with_text("1QF1"), "application/pdf")),
    ]
    response = offline_app.post("/api/check", files=files)
    assert response.status_code == 200
    payload = response.json()
    failed = [item for item in payload["issues"] if item.get("location") == "Сверка Э3 и ПЭ3"]
    assert len(failed) == 1
    assert failed[0]["code"] == "KD-000"
    assert failed[0]["severity"] == "error"
    assert "private parser detail" not in str(payload)


def test_details_of_the_acceptance_scenario_are_schema_valid():
    # The stand must exercise the same validator the parser worker uses.
    findings = stand._run(stand.build_e3_pages([["1QF1 1QF2 1QF3"]]),
                          stand.build_pe3([{"entries": "1QF1;1QF2", "quantity": "3"}]))
    issues = stand._issues105(findings)
    assert issues
    assert all(valid_details(item["details"], {stand.E3_NAME, stand.PE3_NAME}, item)
               for item in issues)
