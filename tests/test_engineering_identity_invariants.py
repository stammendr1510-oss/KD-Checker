"""Metamorphic invariants for immutable finding identity and engineering decisions.

No database, external documents, network or application configuration is used.
"""
import pytest

from issue_tracking import (
    MAX_COMMENT, finding_identity, run_scope, validate_decision,
)


ISSUE = {
    "severity": "warning",
    "code": "KD-012",
    "file": "C:/examples/Synthetic.PDF",
    "location": "Лист   PDF  1",
    "message": "Проверить   позицию",
    "recommendation": "Проверить вручную",
}


def test_run_scope_is_order_independent_but_not_multiplicity_blind():
    a = ["C:/engineering/АБВ.001.Э3.pdf", "D:\\other\\АБВ.001.ПЭ3.pdf"]
    b = ["абв.001.пэ3.PDF", "АБВ.001.Э3.PDF"]
    assert run_scope(a) == run_scope(b)
    assert run_scope(a + [a[0]]) != run_scope(a)


def test_run_scope_uses_unicode_nfc_without_collapsing_distinct_names():
    assert run_scope(["A\u0301.PDF"]) == run_scope(["Á.pdf"])
    assert run_scope(["AB.pdf"]) != run_scope(["АB.pdf"])  # Latin A vs Cyrillic А


def test_finding_identity_normalizes_whitespace_and_filename_only():
    changed = dict(ISSUE, file="synthetic.pdf", location="Лист PDF 1",
                   message="Проверить позицию")
    assert finding_identity(ISSUE) == finding_identity(changed)
    assert finding_identity(dict(ISSUE, file="other.pdf")) != finding_identity(ISSUE)


@pytest.mark.parametrize("field,value", [
    ("severity", "error"),
    ("code", "KD-011"),
    ("location", "Лист PDF 2"),
    ("message", "Другая причина"),
    ("recommendation", "Другой совет"),
])
def test_finding_identity_keeps_material_changes(field, value):
    assert finding_identity(dict(ISSUE, **{field: value})) != finding_identity(ISSUE)


def test_decisions_validate_rejected_reason_and_length():
    validate_decision("new", "")
    validate_decision("in_progress", "Синтетический комментарий")
    validate_decision("resolved", "")
    validate_decision("rejected", "Есть обоснование")
    with pytest.raises(ValueError):
        validate_decision("rejected", "   ")
    with pytest.raises(ValueError):
        validate_decision("not-a-state", "comment")
    with pytest.raises(ValueError):
        validate_decision("new", "x" * (MAX_COMMENT + 1))
    with pytest.raises(ValueError):
        validate_decision("new", None)


def test_finding_identity_is_not_a_permission_or_transfer_token():
    # Same identity is for comparing results, never for reusing engineer decisions.
    assert finding_identity(ISSUE) == finding_identity(dict(ISSUE))
    assert len(finding_identity(ISSUE)) == 64
