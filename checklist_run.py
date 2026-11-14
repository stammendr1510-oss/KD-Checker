"""Per-check checklist acquisition, independent of PDF analysis.

No network request is made unless an authenticated feed is configured.
An unavailable configured feed is never represented as a verified snapshot.
"""
import os

from checklist_supabase import fetch_supabase_checklist

from checklist_snapshot import (
    ChecklistUnavailable, fetch_current_checklist, planned_checklist_reviews
)


def current_checklist_state():
    url = os.environ.get("KD_CHECKLIST_FEED_URL", "").strip()
    token = os.environ.get("KD_CHECKLIST_FEED_TOKEN", "").strip()
    project_url = os.environ.get("KD_CHECKLIST_SUPABASE_URL", "").strip()
    publishable = os.environ.get("KD_CHECKLIST_SUPABASE_PUBLISHABLE_KEY", "").strip()
    user_jwt = os.environ.get("KD_CHECKLIST_SUPABASE_USER_JWT", "").strip()
    direct_selected = any((project_url, publishable, user_jwt))
    feed_selected = any((url, token))
    if not direct_selected and not feed_selected:
        return {
            "status": "not_configured",
            "reason": "Источник EPLAN Checklist ещё не настроен; актуальность пунктов не проверена",
            "revision": None,
            "retrieved_at": None,
            "items": [],
            "reviews": [],
        }
    try:
        if direct_selected and feed_selected:
            raise ChecklistUnavailable("Одновременно заданы разные источники")
        if direct_selected:
            if not all((project_url, publishable, user_jwt)):
                raise ChecklistUnavailable("Не настроен доступ")
            snapshot = fetch_supabase_checklist(project_url, publishable, user_jwt)
        else:
            if not url or not token:
                raise ChecklistUnavailable("Не настроен доступ")
            snapshot = fetch_current_checklist(url=url, token=token)
    except ChecklistUnavailable:
        return {
            "status": "unavailable",
            "reason": "Не удалось подтвердить актуальные пункты EPLAN Checklist при этой проверке",
            "revision": None,
            "retrieved_at": None,
            "items": [],
            "reviews": [],
        }
    return {
        "status": "verified",
        "reason": "Актуальные пункты получены; автоматическая проверка пунктов ещё не подключена",
        "revision": snapshot["revision"],
        "retrieved_at": snapshot["retrieved_at"],
        "items": snapshot["items"],
        "reviews": planned_checklist_reviews(snapshot),
    }
