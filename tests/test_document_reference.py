from document_reference import has_document_reference


def test_matches_a_complete_reference_inside_filename_stem():
    assert has_document_reference("AB.2", ["panel (AB.2) revision B.pdf"])


def test_does_not_match_a_longer_or_embedded_identifier():
    assert not has_document_reference("AB.2", ["AB.20.pdf", "XAB.2.pdf", "AB.2A.pdf"])


def test_punctuation_is_literal_and_case_is_ignored():
    assert has_document_reference("km-1.a", ["KM-1-A.pdf"]) is False
    assert has_document_reference("km-1.a", ["Drawing (KM-1.A).pdf"])


def test_uses_unicode_compatibility_normalization():
    assert has_document_reference("ＡＢ.２", ["ab.2.pdf"])


def test_empty_or_non_text_reference_never_matches():
    assert not has_document_reference("  ", ["anything.pdf"])
    assert not has_document_reference(None, ["anything.pdf"])


def test_empty_document_list_never_matches():
    assert not has_document_reference("AB.2", [])


def test_numeric_continuation_is_a_distinct_designation():
    assert not has_document_reference("AB.2", ["AB.2.1.pdf"])
    assert has_document_reference("AB.2", ["AB.2.ВО.pdf"])
