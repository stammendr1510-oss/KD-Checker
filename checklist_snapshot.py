"""Read-only, fail-closed adapter for a future authenticated EPLAN Checklist feed.

No automatic approval of checklist requirements is possible here. The module
does not access Supabase directly or require an administrator/service-role key.
A separately authorized HTTPS endpoint must supply the feed contract.
"""
from datetime import datetime, timezone
import hashlib
import json
import os
from urllib import error, request
from urllib.parse import urlsplit

MAX_RESPONSE_BYTES = 512_000
MAX_ITEMS = 2_000


class ChecklistUnavailable(RuntimeError):
    """Checklist cannot be verified as current for this attempt."""


class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _text(value, label, max_length=2000):
    if not isinstance(value, str) or not value.strip() or len(value) > max_length:
        raise ChecklistUnavailable(f"Некорректное поле чек-листа: {label}")
    return value.strip()


def _optional_text(value, label, max_length=12000):
    if value is None:
        return ""
    if not isinstance(value, str) or len(value) > max_length:
        raise ChecklistUnavailable(f"Некорректное поле чек-листа: {label}")
    return value.strip()


def validate_checklist_payload(payload):
    """Validate and normalize the complete server-side current-item snapshot.

    Input contract: {'items': [{id:int, section_id:int, title:str,
    section_title:str, tab:int, position:int, comment:str|null, url:str|null}, ...]}.
    Inactive/deleted items MUST be filtered by the trusted upstream endpoint.
    """
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        raise ChecklistUnavailable("Некорректный формат ответа EPLAN Checklist")
    rows = payload["items"]
    if len(rows) > MAX_ITEMS:
        raise ChecklistUnavailable("Слишком много пунктов чек-листа")
    items, ids = [], set()
    for raw in rows:
        if not isinstance(raw, dict):
            raise ChecklistUnavailable("Некорректный пункт чек-листа")
        item_id = raw.get("id")
        section_id = raw.get("section_id")
        tab = raw.get("tab")
        position = raw.get("position")
        if any(type(value) is not int or value < 0 for value in (item_id, section_id, tab, position)):
            raise ChecklistUnavailable("Некорректный идентификатор или порядок пункта")
        if item_id == 0 or section_id == 0 or tab == 0 or item_id in ids:
            raise ChecklistUnavailable("Некорректные или повторяющиеся идентификаторы пунктов")
        ids.add(item_id)
        items.append({
            "id": item_id,
            "section_id": section_id,
            "section_title": _text(raw.get("section_title"), "section_title", 500),
            "title": _text(raw.get("title"), "title"),
            "comment": _optional_text(raw.get("comment"), "comment"),
            "url": _optional_text(raw.get("url"), "url", 4000),
            "tab": tab,
            "position": position,
        })
    items.sort(key=lambda i: (i["tab"], i["section_id"], i["position"], i["id"]))
    normalized = json.dumps(items, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {
        "revision": hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
        "items": items,
    }


def fetch_current_checklist(url=None, token=None, *, timeout=8):
    """Fetch on every invocation; never silently substitute an old snapshot.

    Caller must treat ChecklistUnavailable as an unverified check, not success.
    """
    url = url if url is not None else os.environ.get("KD_CHECKLIST_FEED_URL", "")
    token = token if token is not None else os.environ.get("KD_CHECKLIST_FEED_TOKEN", "")
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ChecklistUnavailable("Нужен настроенный защищённый HTTPS-адрес чек-листа")
    if not token or not isinstance(token, str) or "\r" in token or "\n" in token:
        raise ChecklistUnavailable("Не настроен ключ доступа к чек-листу")
    req = request.Request(
        url, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        method="GET",
    )
    opener = request.build_opener(_NoRedirect())
    try:
        with opener.open(req, timeout=timeout) as response:
            if response.status != 200:
                raise ChecklistUnavailable("Сервис чек-листа вернул ошибку")
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise ChecklistUnavailable("Ответ чек-листа превышает допустимый размер")
    except (error.URLError, OSError, TimeoutError, ValueError) as exc:
        raise ChecklistUnavailable("Не удалось получить актуальный чек-лист") from exc
    try:
        payload = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ChecklistUnavailable("Сервис вернул некорректный JSON") from exc
    snapshot = validate_checklist_payload(payload)
    snapshot["retrieved_at"] = datetime.now(timezone.utc).isoformat()
    snapshot["source"] = "EPLAN Checklist"
    return snapshot


def planned_checklist_reviews(snapshot):
    """Safe baseline: all requirements visible, none falsely auto-verified."""
    return [{
        "item_id": item["id"],
        "title": item["title"],
        "comment": item.get("comment", ""),
        "url": item.get("url", ""),
        "section_title": item["section_title"],
        "status": "manual_review",
        "explanation": "Автоматическая проверка этого пункта пока не реализована",
    } for item in snapshot["items"]]
