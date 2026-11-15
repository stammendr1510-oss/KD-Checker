"""Synthetic, no-network invariants for historical requirement comparisons."""
from copy import deepcopy

import pytest

from checklist_comparison import FIELDS, compare_checklist_snapshots
from run_comparison import IncompatibleRuns, compare_runs


def row(id_, **changes):
    source = {
        "id": id_, "section_id": 1, "section_title": "Раздел",
        "title": "Требование", "comment": "", "url": "",
        "tab": "Список", "position": id_,
    }
    return source | changes


def snapshot(revision, items, status="verified"):
    return {"status": status, "revision": revision, "items": items}


def finding(code, message="Проверить"):
    return {
        "severity": "info", "code": code, "file": "synthetic.Э3.pdf",
        "location": "Лист 1", "message": message,
        "recommendation": "Сверить источник",
    }


def run(number, issues, files=None):
    return {
        "id": number,
        "files": ["synthetic.Э3.pdf"] if files is None else files,
        "issues": issues,
        "finding_states": [{"status": "resolved", "comment": "old decision"}],
    }


@pytest.mark.parametrize("old_status,new_status", [
    ("unavailable", "verified"), ("verified", "unavailable"),
    ("not_configured", "verified"), ("verified", "not_configured"),
    ("unavailable", "unavailable"),
])
def test_unverified_requirements_do_not_use_saved_stale_items(old_status, new_status):
    old = snapshot("old", [row(1)], old_status)
    new = snapshot("new", [row(2)], new_status)
    result = compare_checklist_snapshots(old, new)
    assert result["available"] is False
    assert result["summary"] == {"added": 0, "removed": 0, "changed": 0}
    assert not result["added"] and not result["removed"] and not result["changed"]


def test_requirement_diff_is_sorted_and_preserves_every_changed_field():
    old = snapshot("sha-old", [row(9), row(3, title="Старое", url="old"), row(1)])
    new = snapshot("sha-new", [row(3, title="Новое", url="new"), row(2), row(8)])
    result = compare_checklist_snapshots(old, new)
    assert result["available"] is True
    assert result["previous_revision"] == "sha-old"
    assert result["current_revision"] == "sha-new"
    assert [x["id"] for x in result["added"]] == [2, 8]
    assert [x["id"] for x in result["removed"]] == [1, 9]
    assert [x["item_id"] for x in result["changed"]] == [3]
    assert result["changed"][0]["fields"] == ["title", "url"]
    assert result["summary"] == {"added": 2, "removed": 2, "changed": 1}
    assert "соответствие КД не оценивалось" in result["reason"]


@pytest.mark.parametrize("field", FIELDS)
def test_each_normative_metadata_field_is_compared_without_approval_claim(field):
    value = 7 if field in {"section_id", "position"} else "Изменено"
    result = compare_checklist_snapshots(
        snapshot("1", [row(1)]),
        snapshot("2", [row(1, **{field: value})]),
    )
    assert result["summary"] == {"added": 0, "removed": 0, "changed": 1}
    assert result["changed"][0]["fields"] == [field]


def test_diff_does_not_modify_historical_snapshots_or_expose_live_references():
    before = snapshot("sha1", [row(4)])
    after = snapshot("sha2", [row(4, title="Обновлено")])
    original_before, original_after = deepcopy(before), deepcopy(after)
    result = compare_checklist_snapshots(before, after)
    result["changed"][0]["previous"]["title"] = "mutated by caller"
    result["changed"][0]["current"]["title"] = "mutated by caller"
    assert before == original_before and after == original_after


def test_kd_comparison_does_not_transfer_engineer_decision_or_mutate_history():
    previous = run(1, [finding("KD-PE-100")])
    current = run(2, [finding("KD-PE-100")])
    previous_snapshot, current_snapshot = deepcopy(previous), deepcopy(current)
    result = compare_runs(previous, current)
    assert result["summary"]["persisting"] == 1
    assert "finding_states" not in result
    assert previous == previous_snapshot and current == current_snapshot
    assert "Решения инженера не переносятся" in result["notice"]


def test_kd_comparison_rejects_changed_file_multiplicity():
    previous = run(1, [], ["A.pdf", "B.pdf"])
    current = run(2, [], ["A.pdf", "B.pdf", "B.pdf"])
    with pytest.raises(IncompatibleRuns):
        compare_runs(previous, current)


def test_identical_duplicates_never_become_silent_one_to_one_matches():
    a = finding("KD-020")
    result = compare_runs(run(1, [a, a]), run(2, [a, a]))
    assert result["summary"]["ambiguous"] == 1
    assert result["summary"]["persisting"] == 0
    assert [x["issue_index"] for x in result["ambiguous"][0]["previous"]] == [0, 1]
    assert [x["issue_index"] for x in result["ambiguous"][0]["current"]] == [0, 1]
