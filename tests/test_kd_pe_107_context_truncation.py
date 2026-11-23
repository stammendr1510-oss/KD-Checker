"""KD106-06: truncated context keeps an explicit marker and its source."""
from e3_pe3_inventory import (
    Mention,
    compare_inventories,
    extract_mentions,
    valid_details,
)
from kd_pe_107_reference import table_pdf


E3 = "ABCD.123456.001.Э3.pdf"
PE3 = "ABCD.123456.001.ПЭ3.pdf"


def test_long_reference_context_is_marked_and_bounded():
    words = [(60, 90, 80, 96, "см.", 0, 0, 0), (85, 90, 110, 96, "QF1", 0, 0, 0)]
    words += [
        (115 + index * 10, 90, 125 + index * 10, 96, "X" * 20, 0, 0, 0)
        for index in range(30)
    ]
    mentions = extract_mentions(words, 1, page_size=(1000, 800))
    mention = next(item for item in mentions if item.designation == "QF1")
    assert mention.kind == "reference"
    assert len(mention.context) <= 300
    assert mention.context.endswith("…")
    assert len(mention.line_text) <= 300
    assert mention.line_text.endswith("…")
    assert "X" * 40 not in mention.context


def test_truncated_context_remains_schema_valid():
    long_line = "см. QF1 " + " ".join("X" * 20 for _ in range(30))
    mention = Mention(
        designation="QF1", raw="QF1", page=1, kind="reference",
        context=long_line[:299] + "…", confidence="medium",
        bbox=None, page_size=None, line_text=long_line[:299] + "…",
    )
    row = {
        "page": 1, "entries": {"QF9"}, "quantity": 1, "unknown": [],
        "row_band": (70.0, 120.0), "table_row": 1, "name": "Breaker",
        "name_confidence": "medium", "source": "QF9", "page_size_pt": [600.0, 800.0],
    }
    findings = compare_inventories(
        E3, PE3, [mention], [row],
        e3_word_count=1, e3_pages_processed=1, e3_pages_with_text=1,
    )
    assert len(findings) == 1
    details = findings[0]["details"]
    assert details["status"] == "not_evaluated"
    assert details["ambiguous"][0]["designation"] == "QF1"
    assert valid_details(details, {E3, PE3}, findings[0]) is True


def test_pe3_name_never_exceeds_the_schema_limit():
    from kd_crosscheck import get_spec_rows
    long_name = "Rele module " + "KPR-SCE-24VDC-1C " * 30
    content = table_pdf([("K1", 1, long_name)])
    row = get_spec_rows(content)[0]
    assert len(row["name"]) <= 300
