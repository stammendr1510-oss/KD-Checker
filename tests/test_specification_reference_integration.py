from engine import analyze
from extractors import Document
from app import load_cfg


def _specification(reference):
    return Document(
        "items.csv",
        ".csv",
        sheets=[{"name": "CSV", "rows": [
            ["позиция", "обозначение", "наименование"],
            ["1", reference, "Деталь"],
        ]}],
    )


def test_partial_designation_does_not_satisfy_specification_reference():
    docs = [_specification("AB.2"), Document("AB.20.pdf", ".pdf", text="enough text")]
    findings = analyze(docs, load_cfg())
    assert [item["code"] for item in findings].count("KD-012") == 1


def test_complete_designation_still_satisfies_specification_reference():
    docs = [_specification("AB.2"), Document("Drawing (AB.2).pdf", ".pdf", text="enough text")]
    findings = analyze(docs, load_cfg())
    assert "KD-012" not in [item["code"] for item in findings]
