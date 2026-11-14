"""Conservative comparison of two immutable KD Checker runs.

Only unambiguous one-to-one fingerprints are classified as persistent.
Matching duplicates remain unclassified so a user is not misled by guessed
correspondences. No engineering decision status is changed or inherited.
"""
from collections import Counter, defaultdict

from issue_tracking import finding_identity, run_scope


class IncompatibleRuns(ValueError):
    pass


def compare_runs(previous, current):
    """Compare full get_run() dictionaries, preserving source issue indices."""
    if previous["id"] >= current["id"]:
        raise ValueError("Выберите предыдущую проверку с меньшим номером")
    if run_scope(previous["files"]) != run_scope(current["files"]):
        raise IncompatibleRuns(
            "Состав файлов различается. Для достоверного сравнения "
            "повторите проверку того же набора файлов."
        )

    def indexed(run):
        items = defaultdict(list)
        for index, issue in enumerate(run["issues"]):
            items[finding_identity(issue)].append({
                "issue_index": index,
                "issue": issue,
            })
        return items

    before = indexed(previous)
    after = indexed(current)
    results = {key: [] for key in ("new", "persisting", "disappeared", "ambiguous")}
    for fingerprint in sorted(before.keys() | after.keys()):
        old, now = before.get(fingerprint, []), after.get(fingerprint, [])
        if old and now and (len(old) != 1 or len(now) != 1):
            results["ambiguous"].append({
                "previous": old,
                "current": now,
                "reason": "Повторяющиеся одинаковые замечания нельзя надёжно сопоставить по экземплярам",
            })
        elif old and now:
            results["persisting"].append({
                "previous": old[0],
                "current": now[0],
            })
        elif now:
            # Even two identical findings on one side are genuinely new
            # relative to an entirely absent fingerprint.
            results["new"].extend(now)
        else:
            results["disappeared"].extend(old)

    # Order by displayed issue order, not by a fingerprint hash.
    results["new"].sort(key=lambda x: x["issue_index"])
    results["persisting"].sort(key=lambda x: x["current"]["issue_index"])
    results["disappeared"].sort(key=lambda x: x["issue_index"])
    results["ambiguous"].sort(
        key=lambda x: min(i["issue_index"] for i in x["current"])
        if x["current"] else 10**9
    )
    return {
        "previous_run_id": previous["id"],
        "current_run_id": current["id"],
        "summary": {key: len(results[key]) for key in results},
        **results,
        "notice": (
            "Исчезновение замечания не подтверждает исправление: "
            "оно может быть связано с изменением PDF или распознавания. "
            "Решения инженера не переносятся автоматически."
        ),
    }
