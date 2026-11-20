"""Offline contract tests for the opt-in KD-PE-100 worker response."""
from contextlib import closing
from copy import deepcopy
from io import BytesIO
import json
from types import SimpleNamespace

import fitz
from openpyxl import load_workbook
import pytest

import parser_process as worker
from kd_crosscheck import run as crosscheck
from pdf_match_evidence import source
from scoped_history import Repository, Scope
from secure_backend import ScopedBackend
from secure_sessions import Identity
from test_e3_pe3_match_source_pages import pdf_pages
from test_parser_worker_pdf_bundle import BASE, TESTS, bundle
from test_pe3_duplicates import pe3


SCHEME = f"{BASE}.Э3.pdf"
ELEMENTS = f"{BASE}.ПЭ3.pdf"
CONFIG = json.loads((TESTS.parent / "config.json").read_bytes())


def match(issues):
    return next(issue for issue in issues if issue["code"] == "KD-PE-100")


def test_legacy_worker_manifest_and_result(tmp_path, bundle):
    files = []
    for index, (name, data) in enumerate(bundle.items()):
        path = f"input-{index}.bin"
        (tmp_path / path).write_bytes(data)
        files.append({"name": name, "path": path})
    # The old parent sends no version and requires the exact v1 keys.
    assert worker._parse(tmp_path, {"files": files, "config": CONFIG}, worker.Limits()) == 0
    payload = json.loads((tmp_path / "result.json").read_bytes())
    expected = json.loads((TESTS / "fixtures/parser-worker-pdf-findings.json").read_bytes())
    assert set(payload) == {"status", "issues"}
    assert payload["status"] == "ok"
    assert sorted(payload["issues"], key=lambda issue: issue["code"]) == sorted(
        expected, key=lambda issue: issue["code"]
    )


def test_separate_worker_negotiates_evidence_without_changing_default(tmp_path, monkeypatch, bundle):
    monkeypatch.setattr(worker, "_TEMP_ROOT", tmp_path)
    legacy = worker.run_check(list(bundle.items()), CONFIG)
    enriched = worker.run_check(list(bundle.items()), CONFIG, result_version=2)
    assert [{k: v for k, v in issue.items() if k != "evidence"} for issue in enriched] == legacy
    finding = match(enriched)
    assert finding["severity"] == "info"
    evidence = finding["evidence"]
    assert evidence["schema_version"] == 1
    assert evidence["kind"] == "pdf_text_match"
    assert evidence["truncated"] is False
    assert [item["designation"] for item in evidence["matches"]] == ["1KM1", "XT1"]
    first = evidence["matches"][0]
    assert [(s["document"], s["pdf_page"]) for s in first["scheme"]] == [(SCHEME, 1)]
    assert [(s["document"], s["pdf_page"], s["table_row"]) for s in first["element_list"]] == [
        (ELEMENTS, 1, 3), (ELEMENTS, 1, 4),
    ]
    assert all(s["boxes_pt"] for s in first["scheme"] + first["element_list"])
    assert not list(tmp_path.iterdir())


def test_missing_coordinates_keep_page_and_row_without_guessing():
    issue = match(crosscheck(SCHEME, pdf_pages("1KM\n2"), ELEMENTS,
                             pe3([[("1KM1...1KM3", 3)]]), include_evidence=True))
    item, = issue["evidence"]["matches"]
    assert item["designation"] == "1KM2"
    assert item["scheme"] == [{"document": SCHEME, "pdf_page": 1}]
    assert item["element_list"] == [{"document": ELEMENTS, "pdf_page": 1, "table_row": 3}]


@pytest.mark.parametrize("box", [[0, 0, float("nan"), 4], [0, 0, float("inf"), 4],
                                 [-1, 0, 3, 4], [3, 0, 2, 4], [0, 0, 901, 4],
                                 [False, 0, 3, 4], [0, 0, 3]])
def test_parent_rejects_invalid_coordinates(box):
    issue = match(crosscheck(SCHEME, pdf_pages("1KM1"), ELEMENTS,
                             pe3([[("1KM1", 1)]]), include_evidence=True))
    issue["evidence"]["matches"][0]["scheme"][0]["boxes_pt"] = [box]
    with pytest.raises(worker.ProcessingUnavailable):
        worker._validate_result({"status": "ok", "issues": [issue], "result_version": 2},
                                2, {SCHEME, ELEMENTS})


def test_parent_rejects_unsolicited_or_missing_protocol_version():
    for payload, requested in [
        ({"status": "ok", "issues": [], "result_version": 2}, 1),
        ({"status": "ok", "issues": []}, 2),
        ({"status": "ok", "issues": [], "result_version": True}, 2),
    ]:
        with pytest.raises(worker.ProcessingUnavailable):
            worker._validate_result(payload, requested)


def test_ambiguous_pair_has_no_evidence(tmp_path, monkeypatch, bundle):
    monkeypatch.setattr(worker, "_TEMP_ROOT", tmp_path)
    items = [*bundle.items(), (SCHEME, pdf_pages("SYNTHETIC ONLY distinct scheme 9KM9"))]
    issues = worker.run_check(items, CONFIG, result_version=2)
    assert "KD-SET-103" in {item["code"] for item in issues}
    assert "KD-PE-100" not in {item["code"] for item in issues}
    assert all("evidence" not in item for item in issues)


def test_sources_are_sorted_deduplicated_and_bounded():
    labels = "XT6 XT5 XT4 XT3 XT2 XT1 XT1 XT1 XT1 XT1 XT1"
    issues = crosscheck(SCHEME, pdf_pages(*[labels] * 4), ELEMENTS,
                        pe3([[("XT1...XT6", 6)] * 4]), include_evidence=True)
    evidence = match(issues)["evidence"]
    assert evidence["truncated"] is True
    assert [item["designation"] for item in evidence["matches"]] == [f"XT{i}" for i in range(1, 6)]
    first = evidence["matches"][0]
    assert [item["pdf_page"] for item in first["scheme"]] == [1, 2, 3]
    boxes = first["scheme"][0]["boxes_pt"]
    assert len(boxes) == 4
    assert boxes == [list(box) for box in sorted({tuple(box) for box in boxes})]
    assert [item["table_row"] for item in first["element_list"]] == [3, 4, 5]


def test_identical_boxes_are_deduplicated_before_the_limit():
    boxes = [(index, 0, index + 1, 1) for index in range(5)]
    location, truncated = source(SCHEME, 1, size=(10, 10), boxes=boxes[::-1] + boxes)
    assert truncated
    assert location["boxes_pt"] == [list(box) for box in boxes[:4]]


def test_scheme_boxes_use_unrotated_cropped_page_coordinates():
    with fitz.open() as pdf:
        page = pdf.new_page(width=350, height=270)
        page.insert_text((80, 100), "1KM1")
        page.set_cropbox(fitz.Rect(20, 30, 320, 230))
        page.set_rotation(90)
        scheme = pdf.tobytes()
    with fitz.open(stream=scheme, filetype="pdf") as pdf:
        expected = list(pdf[0].get_text("words")[0][:4])
    issue = match(crosscheck(SCHEME, scheme, ELEMENTS, pe3([[("1KM1", 1)]]),
                             include_evidence=True))
    location, = issue["evidence"]["matches"][0]["scheme"]
    assert location["page_size_pt"] == [300, 200]
    assert location["boxes_pt"] == [expected]


def test_legacy_saved_findings_and_comparison_stay_compatible(tmp_path):
    repo = Repository.create(tmp_path / "synthetic.sqlite3")
    scope = Scope("org", "project")
    repo.provision("engineer", scope, "engineer")
    args = (SCHEME, pdf_pages("1KM1"), ELEMENTS, pe3([[("1KM1", 1)]]))
    history = crosscheck(*args)
    first = repo.save("engineer", scope, [SCHEME, ELEMENTS], history, revision="synthetic")
    modern = deepcopy(crosscheck(*args, include_evidence=True))
    second = repo.save("engineer", scope, [SCHEME, ELEMENTS], modern, revision="synthetic")
    assert repo.get("engineer", scope, first)["issues"] == history
    assert repo.get("engineer", scope, second)["issues"] == modern
    assert repo.compare("engineer", scope, first, second)["summary"]["persisting"] == len(history)

    backend = ScopedBackend(repo)
    identity = Identity("engineer", scope.organization, scope.project)

    def export(identifier, extension):
        request = SimpleNamespace(path_params={"run_id": identifier})
        route = f"/api/runs/{identifier}/{extension}"
        # These export branches complete synchronously, with no transport or socket.
        with pytest.raises(StopIteration) as done:
            backend.dispatch("GET", route, identity, request).send(None)
        return done.value.value.body

    assert export(first, "csv") == export(second, "csv")
    with closing(load_workbook(BytesIO(export(first, "xlsx")))) as old_book:
        old_findings = list(old_book["Замечания"].values)
    with closing(load_workbook(BytesIO(export(second, "xlsx")))) as new_book:
        assert list(new_book["Замечания"].values) == old_findings
