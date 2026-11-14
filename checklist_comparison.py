"""Deterministic comparison of historical EPLAN Checklist requirement snapshots.

This compares source requirements, never engineering approval or fulfilment.
Only independently verified snapshots can be compared.
"""
from copy import deepcopy


FIELDS = ("section_id", "section_title", "title", "comment", "url", "tab", "position")


def compare_checklist_snapshots(previous, current):
    if previous.get("status") != "verified" or current.get("status") != "verified":
        return {
            "available": False,
            "reason": "Для сравнения нужны два запуска с подтверждённым снимком EPLAN Checklist",
            "previous_revision": previous.get("revision"),
            "current_revision": current.get("revision"),
            "added": [], "removed": [], "changed": [],
            "summary": {"added": 0, "removed": 0, "changed": 0},
        }

    def indexed(snapshot):
        return {row["id"]: row for row in snapshot["items"]}

    old = indexed(previous)
    new = indexed(current)
    added = [deepcopy(new[id_]) for id_ in sorted(new.keys() - old.keys())]
    removed = [deepcopy(old[id_]) for id_ in sorted(old.keys() - new.keys())]
    changed = []
    for id_ in sorted(old.keys() & new.keys()):
        changed_fields = [
            field for field in FIELDS if old[id_].get(field, "") != new[id_].get(field, "")
        ]
        if changed_fields:
            changed.append({
                "item_id": id_,
                "fields": changed_fields,
                "previous": deepcopy(old[id_]),
                "current": deepcopy(new[id_]),
            })
    return {
        "available": True,
        "reason": "Сопоставлены только формулировки и метаданные требований; соответствие КД не оценивалось",
        "previous_revision": previous.get("revision"),
        "current_revision": current.get("revision"),
        "added": added,
        "removed": removed,
        "changed": changed,
        "summary": {"added": len(added), "removed": len(removed), "changed": len(changed)},
    }
