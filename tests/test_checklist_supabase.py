"""Offline-only contract tests for optional authorized Supabase REST checklist reads."""
import json
from urllib import error

import pytest

import checklist_supabase as source
import checklist_run


PROJECT = "https://example.supabase.co"
KEY = "sb_publishable_synthetic_test"
JWT = "synthetic.user.jwt"


def rows():
    return [
        {"id": 23, "section_id": 3, "title": "Проверить схему",
         "comment": "Сверить соединения", "url": "https://example.invalid/help",
         "position": 2, "sections": {"id": 3, "tab": 2, "title": "Схемы", "position": 1}},
        {"id": 11, "section_id": 2, "title": "Проверить комплект",
         "comment": None, "url": None, "position": 1,
         "sections": {"id": 2, "tab": 1, "title": "Состав", "position": 1}},
    ]


class Response:
    status = 206
    def __init__(self, body, count):
        self.body = body
        self.headers = {"Content-Range": count}
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def read(self, length):
        return self.body[:length]


class Opener:
    def __init__(self, body, count="0-1/2"):
        self.body, self.count = body, count
        self.calls = 0
        self.request = None
    def open(self, req, timeout):
        self.calls += 1
        self.request = req
        assert timeout == 8
        return Response(self.body, self.count)


def install(monkeypatch, data=None, count="0-1/2"):
    opener = Opener(json.dumps(rows() if data is None else data).encode("utf-8"), count)
    monkeypatch.setattr(source.request, "build_opener", lambda *args: opener)
    return opener


def test_single_joined_read_with_authenticated_user(monkeypatch):
    opener = install(monkeypatch)
    snapshot = source.fetch_supabase_checklist(PROJECT, KEY, JWT)
    assert opener.calls == 1
    assert opener.request.get_method() == "GET"
    assert "/rest/v1/items?" in opener.request.full_url
    assert "sections%21inner" in opener.request.full_url
    assert opener.request.get_header("Authorization") == "Bearer " + JWT
    assert opener.request.get_header("Apikey") == KEY
    assert opener.request.get_header("Prefer") == "count=exact"
    assert [item["id"] for item in snapshot["items"]] == [11, 23]
    assert snapshot["items"][1]["comment"] == "Сверить соединения"
    assert len(snapshot["revision"]) == 64


def test_revision_updates_after_comments_change(monkeypatch):
    opener = install(monkeypatch)
    first = source.fetch_supabase_checklist(PROJECT, KEY, JWT)
    modified = rows()
    modified[0]["comment"] = "Новое замечание"
    opener.body = json.dumps(modified).encode("utf-8")
    second = source.fetch_supabase_checklist(PROJECT, KEY, JWT)
    assert opener.calls == 2 and first["revision"] != second["revision"]


@pytest.mark.parametrize("count,contents", [
    ("0-0/1", rows()),
    ("0-1/3", rows()),
    ("", rows()),
    ("*/0", []),
    ("0-1/2", [rows()[0]]),
])
def test_declines_incomplete_or_empty_snapshots(monkeypatch, count, contents):
    install(monkeypatch, contents, count)
    with pytest.raises(source.ChecklistUnavailable):
        source.fetch_supabase_checklist(PROJECT, KEY, JWT)


@pytest.mark.parametrize("project,key,jwt", [
    ("http://example.supabase.co", KEY, JWT),
    ("https://example.supabase.co.evil.invalid", KEY, JWT),
    ("https://example.supabase.co/unsafe", KEY, JWT),
    (PROJECT, "sb_secret_fake", JWT),
    (PROJECT, "legacy-service-role", JWT),
    (PROJECT, KEY, ""),
    (PROJECT, KEY, "sb_publishable_bad"),
    (PROJECT, KEY, "bad\nuser.jwt"),
])
def test_rejects_unsafe_host_keys_and_tokens(project, key, jwt):
    with pytest.raises(source.ChecklistUnavailable):
        source.fetch_supabase_checklist(project, key, jwt)


def test_rejects_untrusted_section_mismatch(monkeypatch):
    data = rows()
    data[0]["sections"]["id"] = 999
    install(monkeypatch, data)
    with pytest.raises(source.ChecklistUnavailable):
        source.fetch_supabase_checklist(PROJECT, KEY, JWT)


def test_error_does_not_leak_user_token(monkeypatch):
    class Failure:
        def open(self, *a, **k):
            raise error.HTTPError(PROJECT, 401, "no session", {}, None)
    monkeypatch.setattr(source.request, "build_opener", lambda *args: Failure())
    with pytest.raises(source.ChecklistUnavailable) as raised:
        source.fetch_supabase_checklist(PROJECT, KEY, JWT)
    assert JWT not in str(raised.value)


def test_opt_in_mode_uses_user_jwt_without_feed(monkeypatch):
    monkeypatch.delenv("KD_CHECKLIST_FEED_URL", raising=False)
    monkeypatch.delenv("KD_CHECKLIST_FEED_TOKEN", raising=False)
    monkeypatch.setenv("KD_CHECKLIST_SUPABASE_URL", PROJECT)
    monkeypatch.setenv("KD_CHECKLIST_SUPABASE_PUBLISHABLE_KEY", KEY)
    monkeypatch.setenv("KD_CHECKLIST_SUPABASE_USER_JWT", JWT)
    opener = install(monkeypatch)
    state = checklist_run.current_checklist_state()
    assert state["status"] == "verified"
    assert opener.calls == 1
    assert all(r["status"] == "manual_review" for r in state["reviews"])


def test_incomplete_or_conflicting_sources_fail_closed(monkeypatch):
    monkeypatch.setenv("KD_CHECKLIST_SUPABASE_URL", PROJECT)
    monkeypatch.delenv("KD_CHECKLIST_SUPABASE_PUBLISHABLE_KEY", raising=False)
    monkeypatch.delenv("KD_CHECKLIST_SUPABASE_USER_JWT", raising=False)
    monkeypatch.delenv("KD_CHECKLIST_FEED_URL", raising=False)
    monkeypatch.delenv("KD_CHECKLIST_FEED_TOKEN", raising=False)
    assert checklist_run.current_checklist_state()["status"] == "unavailable"
    monkeypatch.setenv("KD_CHECKLIST_SUPABASE_PUBLISHABLE_KEY", KEY)
    monkeypatch.setenv("KD_CHECKLIST_SUPABASE_USER_JWT", JWT)
    monkeypatch.setenv("KD_CHECKLIST_FEED_URL", "https://feed.example.invalid")
    monkeypatch.setenv("KD_CHECKLIST_FEED_TOKEN", "fake")
    assert checklist_run.current_checklist_state()["status"] == "unavailable"
