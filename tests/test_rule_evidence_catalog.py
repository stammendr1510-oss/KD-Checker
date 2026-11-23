"""Offline governance checks for the existing finding code vocabulary.

They do NOT certify the correctness of the engineering checks. A new code
requires explicit evidence/scope review instead of silently entering reports.
"""
from pathlib import Path
import re

import pytest

from quality.rule_catalog import NOT_YET_VERIFIED, RULES
from engine import issue as engine_issue
from kd_crosscheck import issue as pe_issue


ROOT = Path(__file__).resolve().parents[1]
RULE_SOURCES = (
    "app.py",
    "engine.py",
    "kd_crosscheck.py",
    "e3_pe3_inventory.py",
    "designation_conflicts.py",
    "confusable_designations.py",
    "document_identity.py",
    "pair_analysis.py",
    "set_completeness.py",
    "full_set_completeness.py",
    "connection_checks.py",  # present only after PR #47 is merged
)
CODE = re.compile(r"\bKD-(?:(?:PE|SET|CON)-)?\d{3}\b")
REQUIRED_FINDING_FIELDS = {
    "severity", "code", "file", "location", "message", "recommendation",
}


def test_inventory_recognizes_every_catalog_code_including_staged_families():
    # A staged code must already be recognizable before its source module lands.
    assert all(CODE.fullmatch(code) for code in RULES)


def test_all_current_rule_codes_have_evidence_classification():
    observed = set()
    for relative_path in RULE_SOURCES:
        source = ROOT / relative_path
        if source.exists():
            observed.update(CODE.findall(source.read_text(encoding="utf-8")))
    staged = set() if (ROOT / "connection_checks.py").exists() else {"KD-CON-101", "KD-CON-102"}
    assert observed == set(RULES) - staged, (
        "Rule catalog drift: review all added/retired codes, their evidence "
        "and coverage before updating quality/rule_catalog.py"
    )


def test_catalog_does_not_claim_unverified_topology_or_gost():
    assert {"connections_cables_terminals", "standards", "multi_pc"} <= set(NOT_YET_VERIFIED)
    assert {"KD-CON-101", "KD-CON-102"} <= set(RULES)
    assert all(rule.evidence in {"direct", "heuristic", "informational"} for rule in RULES.values())
    assert all(rule.direction and rule.note for rule in RULES.values())


@pytest.mark.parametrize("factory", [engine_issue, pe_issue])
def test_finding_constructors_keep_report_fields(factory):
    value = factory("info", "KD-SET-201", "synthetic.pdf", "PDF 1", "Synthetic message", "Review")
    assert set(value) == REQUIRED_FINDING_FIELDS
    assert value == {
        "severity": "info",
        "code": "KD-SET-201",
        "file": "synthetic.pdf",
        "location": "PDF 1",
        "message": "Synthetic message",
        "recommendation": "Review",
    }
