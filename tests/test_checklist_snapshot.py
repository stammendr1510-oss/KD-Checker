"""Synthetic-only contract and failure-mode tests for checklist integration."""
import json
from urllib import error

import pytest

import checklist_snapshot as adapter


def payload():
    return {"items": [
        {"id": 7, "section_id": 3, "section_title": "Оформление КД",
         "title": "Проверить листы", "tab": 2, "position": 2},
        {"id": 4, "section_id": 1, "section_title": "Общие данные",
         "title": "Проверить экспорт", "tab": 1, "position": 1},
    ]}


def test_revision_stable_and_sorted_independent_of_transport_order():
    first = adapter.validate_checklist_payload(payload())
    modified = payload()
    modified["items"].reverse()
    second = adapter.validate_checklist_payload(modified)
    assert first == second
    assert [item["id"] for item in first["items"]] == [4, 7]
    assert len(first["revision"]) == 64


def test_added_removed_or_edited_requirement_changes_revision():
    original = adapter.validate_checklist_payload(payload())["revision"]
    cases = []
    edited = payload()
    edited["items"][0]["title"] = "Новые требования"
    cases.append(edited)
    removed = payload()
    removed["items"].pop()
    cases.append(removed)
    added = payload()
    added["items"].append({
        "id": 100, "section_id": 3, "section_title": "Оформление КД",
        "title": "Добавленный пункт", "tab": 2, "position": 3,
    })
    cases.append(added)
    assert all(adapter.validate_checklist_payload(x)["revision"] != original for x in cases)


@pytest.mark.parametrize("rows", [
    [{"id": 1}],
    [{"id": True, "section_id": 1, "tab": 1, "position": 1,
      "section_title": "Раздел", "title": "Текст"}],
    [{"id": 1, "section_id": 1, "tab": 1, "position": 1,
      "section_title": "Раздел", "title": " "}],
    [{"id": 1, "section_id": 1, "tab": 1, "position": 1,
      "section_title": "Раздел", "title": "Текст"}] * 2,
])
def test_rejects_malformed_or_duplicate_items(rows):
    with pytest.raises(adapter.ChecklistUnavailable):
        adapter.validate_checklist_payload({"items": rows})


def test_unknown_rule_is_manual_not_passed():
    snapshot = adapter.validate_checklist_payload(payload())
    rows = adapter.planned_checklist_reviews(snapshot)
    assert len(rows) == 2
    assert all(row["status"] == "manual_review" for row in rows)
    assert all("Автоматическая проверка" in row["explanation"] for row in rows)


class FakeResponse:
    status = 200

    def __init__(self, data):
        self.data = data

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, length):
        return self.data[:length]


class FakeOpener:
    def __init__(self, data):
        self.data = data
        self.request_count = 0
        self.headers = None

    def open(self, req, timeout):
        self.request_count += 1
        self.headers = dict(req.header_items())
        assert timeout == 8
        return FakeResponse(self.data)


def test_each_invocation_refetches_current_data(monkeypatch):
    opener = FakeOpener(json.dumps(payload()).encode("utf-8"))
    monkeypatch.setattr(adapter.request, "build_opener", lambda *args: opener)
    a = adapter.fetch_current_checklist("https://checklist.example.org/feed", "test-token")
    changed = payload()
    changed["items"][0]["title"] = "Редактировано на сайте"
    opener.data = json.dumps(changed).encode("utf-8")
    b = adapter.fetch_current_checklist("https://checklist.example.org/feed", "test-token")
    assert opener.request_count == 2
    assert a["revision"] != b["revision"]
    assert a["retrieved_at"] and b["retrieved_at"]
    assert opener.headers["Authorization"] == "Bearer test-token"


def test_offline_fails_closed_instead_of_reusing_old_snapshot(monkeypatch):
    class Offline:
        def open(self, *args, **kwargs):
            raise error.URLError("offline")

    monkeypatch.setattr(adapter.request, "build_opener", lambda *args: Offline())
    with pytest.raises(adapter.ChecklistUnavailable, match="Не удалось"):
        adapter.fetch_current_checklist("https://checklist.example.org/feed", "token")


@pytest.mark.parametrize("url,token", [
    ("http://example.org/feed", "token"),
    ("https://example.org/feed", ""),
    ("https://user:pass@example.org/feed", "token"),
    ("https://example.org/feed#fragment", "token"),
    ("", "token"),
])
def test_requires_authenticated_https_feed(url, token):
    with pytest.raises(adapter.ChecklistUnavailable):
        adapter.fetch_current_checklist(url, token)


def test_oversized_response_rejected(monkeypatch):
    fake = FakeOpener(b"x" * (adapter.MAX_RESPONSE_BYTES + 1))
    monkeypatch.setattr(adapter.request, "build_opener", lambda *args: fake)
    with pytest.raises(adapter.ChecklistUnavailable, match="превышает"):
        adapter.fetch_current_checklist("https://checklist.example.org/feed", "token")


def test_invalid_json_rejected(monkeypatch):
    fake = FakeOpener(b"not JSON")
    monkeypatch.setattr(adapter.request, "build_opener", lambda *args: fake)
    with pytest.raises(adapter.ChecklistUnavailable, match="JSON"):
        adapter.fetch_current_checklist("https://checklist.example.org/feed", "token")



def test_untrusted_requirement_text_is_data_never_executed(tmp_path):
    """A new checklist title must not become Python code or a rule."""
    marker = tmp_path / "should-not-exist"
    malicious = (
        "__import__('pathlib').Path(" + repr(str(marker)) + ").touch()"
    )
    incoming = payload()
    incoming["items"][0]["title"] = malicious
    snapshot = adapter.validate_checklist_payload(incoming)
    reviews = adapter.planned_checklist_reviews(snapshot)
    assert not marker.exists()
    assert reviews[1]["title"] == malicious
    assert all(item["status"] == "manual_review" for item in reviews)
    assert not marker.exists()


def test_repeated_call_cannot_reuse_snapshot_after_service_failure(monkeypatch):
    """A successful earlier read must not disguise a later offline request."""
    opener = FakeOpener(json.dumps(payload()).encode("utf-8"))
    monkeypatch.setattr(adapter.request, "build_opener", lambda *args: opener)
    first = adapter.fetch_current_checklist("https://checklist.example.org/feed", "token")
    assert len(first["items"]) == 2

    class Offline:
        def open(self, *args, **kwargs):
            raise error.URLError("offline")

    monkeypatch.setattr(adapter.request, "build_opener", lambda *args: Offline())
    with pytest.raises(adapter.ChecklistUnavailable):
        adapter.fetch_current_checklist("https://checklist.example.org/feed", "token")


def test_comment_and_reference_url_changes_create_new_revision():
    first = payload()
    first["items"][0]["comment"] = "Уточнение: напряжение 24 В"
    first["items"][0]["url"] = "https://reference.invalid/one"
    original = adapter.validate_checklist_payload(first)
    edited_comment = json.loads(json.dumps(first))
    edited_comment["items"][0]["comment"] = "Уточнение: напряжение 48 В"
    edited_url = json.loads(json.dumps(first))
    edited_url["items"][0]["url"] = "https://reference.invalid/two"
    assert adapter.validate_checklist_payload(edited_comment)["revision"] != original["revision"]
    assert adapter.validate_checklist_payload(edited_url)["revision"] != original["revision"]
    assert original["items"][1]["comment"] == ""
    assert original["items"][0]["url"] == "https://reference.invalid/one"


@pytest.mark.parametrize("field,value", [
    ("comment", 7), ("comment", "a" * 12001),
    ("url", ["https://example.com"]), ("url", "a" * 4001),
])
def test_reject_invalid_checklist_details(field, value):
    incoming = payload()
    incoming["items"][0][field] = value
    with pytest.raises(adapter.ChecklistUnavailable):
        adapter.validate_checklist_payload(incoming)
