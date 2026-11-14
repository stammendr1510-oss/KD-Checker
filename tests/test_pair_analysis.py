"""Synthetic pair-selection cases: ambiguity must never be guessed away."""
from document_identity import unique_pdf_analysis_sources
from pair_analysis import CODE, pair_analysis_plan
from kd_crosscheck import designation_type, pair_key


def src(name, content=b"same"):
    return (name, designation_type(name), pair_key(name), content)


def test_complete_one_to_one_pair_is_analyzed():
    scheme, spec = src("demo.Э3.pdf", b"E3"), src("demo.ПЭ3.pdf", b"PE3")
    pairs, findings = pair_analysis_plan([scheme, spec])
    assert pairs == [(scheme, spec)]
    assert findings == []


def test_separate_keys_are_not_cross_paired():
    pairs, findings = pair_analysis_plan([
        src("first.Э3.pdf"), src("second.ПЭ3.pdf")
    ])
    assert pairs == [] and findings == []


def test_identical_copies_maintain_one_pair_after_existing_dedup():
    raw = [src("demo.Э3.pdf", b"E"), src("DEMO.Э3.pdf", b"E"), src("demo.ПЭ3.pdf", b"P")]
    pairs, findings = pair_analysis_plan(unique_pdf_analysis_sources(raw))
    assert len(pairs) == 1
    assert findings == []


def test_multiple_versions_e3_block_only_that_pair_and_keep_other_groups():
    inputs = [
        src("demo.Э3.pdf", b"E1"),
        src("demo.Э3.pdf", b"E2"),
        src("demo.ПЭ3.pdf", b"P"),
        src("other.Э3.pdf", b"O"),
        src("other.ПЭ3.pdf", b"OP"),
    ]
    pairs, findings = pair_analysis_plan(unique_pdf_analysis_sources(inputs))
    assert len(pairs) == 1
    assert pairs[0][0][0] == "other.Э3.pdf"
    assert len(findings) == 1
    assert findings[0]["code"] == CODE
    assert findings[0]["severity"] == "info"
    assert "Э3: 2, ПЭ3: 1" in findings[0]["message"]
    assert "пропущена" in findings[0]["message"]


def test_multiple_versions_pe3_block_only_that_pair():
    pairs, findings = pair_analysis_plan([
        src("demo.Э3.pdf", b"E"),
        src("demo.ПЭ3.pdf", b"P1"),
        src("demo.ПЭ3.pdf", b"P2"),
    ])
    assert pairs == []
    assert len(findings) == 1
    assert "Э3: 1, ПЭ3: 2" in findings[0]["message"]


def test_two_versions_of_both_types_emit_one_explanation():
    pairs, findings = pair_analysis_plan([
        src("demo.Э3.pdf", b"E1"),
        src("demo.Э3.pdf", b"E2"),
        src("demo.ПЭ3.pdf", b"P1"),
        src("demo.ПЭ3.pdf", b"P2"),
    ])
    assert pairs == []
    assert len(findings) == 1
    assert "Э3: 2, ПЭ3: 2" in findings[0]["message"]


def test_input_order_does_not_change_group_result():
    inputs = [
        src("z.Э3.pdf", b"z"), src("z.ПЭ3.pdf", b"zp"),
        src("a.Э3.pdf", b"a1"), src("a.Э3.pdf", b"a2"),
        src("a.ПЭ3.pdf", b"ap"),
    ]
    pairs1, findings1 = pair_analysis_plan(inputs)
    pairs2, findings2 = pair_analysis_plan(list(reversed(inputs)))
    assert [pair[0][0] for pair in pairs1] == [pair[0][0] for pair in pairs2]
    assert [(i["code"],i["message"]) for i in findings1] == [(i["code"],i["message"]) for i in findings2]
