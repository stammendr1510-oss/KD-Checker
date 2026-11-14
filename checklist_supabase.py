"""Optional authenticated, read-only Supabase REST source for EPLAN Checklist.

Requires a real signed-in user's short-lived JWT and a publishable key.
Never uses secret/service_role keys, database writes or password login.
A single embedded REST query avoids mixing separate section/item versions.
"""
from datetime import datetime, timezone
import json
import re
from urllib import request, error
from urllib.parse import urlencode, urlsplit

from checklist_snapshot import (
    ChecklistUnavailable, MAX_RESPONSE_BYTES, MAX_ITEMS,
    _NoRedirect, validate_checklist_payload,
)

_COUNT = re.compile(r"^(?:\d+-\d+|\*)/(\d+)$")


def fetch_supabase_checklist(project_url, publishable_key, user_jwt, *, timeout=8):
    parsed = urlsplit(project_url)
    if (parsed.scheme != "https" or not parsed.hostname or
            not parsed.hostname.endswith(".supabase.co") or
            parsed.hostname == ".supabase.co" or parsed.username or parsed.password or
            parsed.port not in (None, 443) or parsed.path not in ("", "/") or
            parsed.query or parsed.fragment):
        raise ChecklistUnavailable("Нужен корректный HTTPS URL проекта Supabase")
    if (not isinstance(publishable_key, str) or
            not publishable_key.startswith("sb_publishable_") or
            any(ch in publishable_key for ch in "\r\n") or len(publishable_key) > 300):
        raise ChecklistUnavailable("Требуется только publishable key Supabase")
    if (not isinstance(user_jwt, str) or len(user_jwt) > 8192 or
            len(user_jwt.split(".")) != 3 or
            any(ch in user_jwt for ch in "\r\n\t ")):
        raise ChecklistUnavailable("Требуется JWT авторизованного пользователя")
    query = urlencode({
        "select": "id,section_id,title,comment,url,position,sections!inner(id,tab,title,position)",
        "order": "position.asc,id.asc",
        "limit": str(MAX_ITEMS + 1),
    })
    url = project_url.rstrip("/") + "/rest/v1/items?" + query
    req = request.Request(url, headers={
        "apikey": publishable_key,
        "Authorization": "Bearer " + user_jwt,
        "Accept": "application/json",
        "Prefer": "count=exact",
        "Range": "0-" + str(MAX_ITEMS),
    }, method="GET")
    try:
        with request.build_opener(_NoRedirect()).open(req, timeout=timeout) as res:
            if res.status not in (200, 206):
                raise ChecklistUnavailable("Supabase не подтвердил получение списка")
            count_header = res.headers.get("Content-Range", "")
            raw = res.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise ChecklistUnavailable("Ответ превышает допустимый размер")
    except (error.URLError, OSError, TimeoutError, ValueError) as exc:
        raise ChecklistUnavailable("Не удалось подтвердить актуальный список Supabase") from exc
    count = _COUNT.fullmatch(count_header.strip())
    if count is None:
        raise ChecklistUnavailable("Supabase не подтвердил полноту выборки")
    total = int(count[1])
    if total == 0 or total > MAX_ITEMS:
        raise ChecklistUnavailable("Пустой или превышающий лимит список требований")
    try:
        records = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ChecklistUnavailable("Supabase вернул некорректный JSON") from exc
    if not isinstance(records, list) or len(records) != total:
        raise ChecklistUnavailable("Получен неполный список требований")
    items = []
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get("sections"), dict):
            raise ChecklistUnavailable("Некорректная связь пункта с разделом")
        section = record["sections"]
        if section.get("id") != record.get("section_id"):
            raise ChecklistUnavailable("Неоднозначная связь пункта и раздела")
        items.append({
            "id": record.get("id"),
            "section_id": record.get("section_id"),
            "section_title": section.get("title"),
            "title": record.get("title"),
            "comment": record.get("comment"),
            "url": record.get("url"),
            "tab": section.get("tab"),
            "position": record.get("position"),
        })
    snapshot = validate_checklist_payload({"items": items})
    snapshot["retrieved_at"] = datetime.now(timezone.utc).isoformat()
    snapshot["source"] = "EPLAN Checklist (Supabase authenticated read-only)"
    return snapshot
