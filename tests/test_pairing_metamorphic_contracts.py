"""Deterministic, synthetic invariants of E3/PE3 upload membership.

Never interprets an unuploaded file as proof that released KD is incomplete.
"""
import pytest

from pair_analysis import pair_analysis_plan
from set_completeness import unpaired_document_findings


A = "ABCD.123456.001"
B = "EFGH.123456.002"


def name(base, kind):
    return f"{base}.{kind}.pdf"


def record(base, kind, suffix=""):
    filename = name(base, kind) if not suffix else f"{base}.{kind} {suffix}.pdf"
    return filename, kind, base.lower(), b"synthetic pdf bytes"


@pytest.mark.parametrize("names", [
    [],
    [name(A, "Э3")],
    [name(A, "ПЭ3")],
    [name(A, "Э3"), name(A, "ПЭ3")],
    [name(A, "Э3"), "random.pdf"],
])
def test_no_unpaired_hint_without_cross_group_evidence(names):
    assert unpaired_document_findings(names) == []


def test_absent_upload_pair_is_informational_only_and_order_independent():
    source = [name(A, "Э3"), name(B, "ПЭ3")]
    first = unpaired_document_findings(source)
    assert {x["file"] for x in first} == set(source)
    assert all(x["severity"] == "info" and x["code"] == "KD-SET-101" for x in first)
    assert sorted(first, key=lambda x: x["file"]) == sorted(
        unpaired_document_findings(list(reversed(source))),
        key=lambda x: x["file"],
    )
    assert all("Отсутствие файла в загрузке не означает" in x["recommendation"] for x in first)


def test_exact_pairs_suppress_missing_hints_without_cross_project_guessing():
    src = [name(A, "Э3"), name(A, "ПЭ3"), name(B, "Э3"), name(B, "ПЭ3")]
    assert unpaired_document_findings(src) == []
    assert unpaired_document_findings(src + ["broken.pdf", "document.xlsx"]) == []


def test_pair_analysis_accepts_one_of_each_per_key():
    src = [record(A, "Э3"), record(A, "ПЭ3"), record(B, "Э3"), record(B, "ПЭ3")]
    pairs, issues = pair_analysis_plan(list(reversed(src)))
    assert issues == []
    assert len(pairs) == 2
    assert {pair[0][2] for pair in pairs} == {A.lower(), B.lower()}
    assert all(pair[0][1] == "Э3" and pair[1][1] == "ПЭ3" for pair in pairs)


@pytest.mark.parametrize("duplicate_kind", ["Э3", "ПЭ3"])
def test_ambiguous_versions_block_only_one_pair(duplicate_kind):
    records = [
        record(A, "Э3"), record(A, "ПЭ3"),
        record(A, duplicate_kind, "other"),
        record(B, "Э3"), record(B, "ПЭ3"),
    ]
    pairs, issues = pair_analysis_plan(records)
    assert len(pairs) == 1 and pairs[0][0][2] == B.lower()
    assert len(issues) == 1
    assert issues[0]["code"] == "KD-SET-103"
    assert issues[0]["severity"] == "info"
    assert A.lower() in issues[0]["message"]


def test_orphan_kind_cannot_become_an_invented_pair():
    src = [record(A, "Э3"), record(B, "ПЭ3")]
    assert pair_analysis_plan(src) == ([], [])


def test_duplicate_exact_source_occurrences_require_prior_deduplication():
    # The planner contract expects already deduplicated sources: never claim
    # that equal byte content implies independent valid revisions.
    src = [record(A, "Э3"), record(A, "ПЭ3"), record(A, "Э3")]
    pairs, issues = pair_analysis_plan(src)
    assert not pairs
    assert [issue["code"] for issue in issues] == ["KD-SET-103"]
