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
    "document_identity.py",
    "pair_analysis.py",
    "set_completeness.py",
    "full_set_completeness.py",
)
CODE = re.compile(r"\bKD-(?:(?:PE|SET)-)?\d{3}\b")
REQUIRED_FINDING_FIELDS = {
    "severity", "code", "file", "location", "message", "recommendation",
}


def test_all_current_rule_codes_have_evidence_classification():
    observed = set()
    for relative_path in RULE_SOURCES:
        observed.update(CODE.findall((ROOT / relative_path).read_text(encoding="utf-8")))
    staged = {"KD-CON-101", "KD-CON-102"}  # PR #47 remains unmerged
    assert observed <= set(RULES) and set(RULES) - observed <= staged, (
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
