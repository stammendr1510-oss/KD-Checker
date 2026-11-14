"""Offline, read-only task selector for KD Checker.

No AI, network, tokens, secrets, paid APIs, or GitHub mutations.
This is not a scheduler or autonomous coding agent.
"""
import argparse
import json
from pathlib import Path

VALID_STATES = {"ready", "blocked", "in_progress", "done"}


def plan(payload):
    tasks = payload.get("tasks")
    if not isinstance(tasks, list):
        raise ValueError("tasks must be a list")
    by_id = {}
    for task in tasks:
        if not isinstance(task, dict) or not isinstance(task.get("id"), str):
            raise ValueError("each task must have a string id")
        task_id = task["id"]
        if not task_id or task_id in by_id:
            raise ValueError("missing or duplicate task id")
        if task.get("state") not in VALID_STATES:
            raise ValueError("invalid task state")
        if not isinstance(task.get("dependencies", []), list):
            raise ValueError("dependencies must be a list")
        by_id[task_id] = task
    for task in tasks:
        for dep in task.get("dependencies", []):
            if not isinstance(dep, str) or dep not in by_id or dep == task["id"]:
                raise ValueError("unknown, invalid or self dependency")
    if payload.get("pause", True):
        return {"status": "paused", "next_task": None, "reason": "manual pause / fail-closed default"}
    if payload.get("budget_verified") is not True:
        return {"status": "blocked", "next_task": None, "reason": "zero-spend budget not verified"}
    if payload.get("active_agent") is True:
        return {"status": "blocked", "next_task": None, "reason": "agent already active"}
    candidates = []
    for task in tasks:
        if task["state"] != "ready":
            continue
        if task.get("human_approval_required", True):
            continue
        if all(by_id[dep]["state"] == "done" for dep in task.get("dependencies", [])):
            candidates.append(task)
    if not candidates:
        return {"status": "idle", "next_task": None, "reason": "no approved ready task"}
    candidates.sort(key=lambda item: (item.get("priority", 100), item["id"]))
    return {"status": "ready", "next_task": candidates[0]["id"], "reason": "advisory only: no execution"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path, help="synthetic or reviewed task JSON")
    args = parser.parse_args()
    try:
        result = plan(json.loads(args.snapshot.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError) as exc:
        parser.exit(2, f"invalid snapshot: {exc}\n")
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
