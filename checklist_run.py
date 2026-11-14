"""Per-check checklist acquisition, independent of PDF analysis.

No network request is made unless an authenticated feed is configured.
An unavailable configured feed is never represented as a verified snapshot.
"""
import os

from checklist_snapshot import (
    ChecklistUnavailable, fetch_current_checklist, planned_checklist_reviews
)


def current_checklist_state():
    url = os.environ.get("KD_CHECKLIST_FEED_URL", "").strip()
    token = os.environ.get("KD_CHECKLIST_FEED_TOKEN", "").strip()
    if not url or not token:
        return {
            "status": "not_configured",
            "reason": "Источник EPLAN Checklist ещё не настроен; актуальность пунктов не проверена",
            "revision": None,
            "retrieved_at": None,
            "items": [],
            "reviews": [],
        }
    try:
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
