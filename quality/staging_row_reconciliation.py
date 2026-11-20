"""Read-only row-level reconciliation of a completed legacy staging import.

Complements structural checks by comparing the original normalized legacy rows
to staged runs, decisions, events and previous-run links. Never changes storage.
Use only trusted, quiescent offline local SQLite copies.
"""
from contextlib import closing
import json
from pathlib import Path
import sqlite3

from issue_tracking import run_scope
from quality.staging_import_preflight import verify_staging_import


MAX_PROBLEMS = 100


def reconcile_staging_rows(staging_path, *, verified_backup=None):
    """Return anonymized, bounded diagnostics; no source values in the report.

    Without a verified backup, the result only attests *internal* consistency
    between the untrusted legacy_archive and normalized staging tables.
    """
    baseline = verify_staging_import(staging_path, verified_backup=verified_backup)
    problems = list(baseline["problems"])
    result = dict(baseline)
    result["reconciled"] = False

    def flag(code):
        if len(problems) < MAX_PROBLEMS:
            problems.append({"code": code, "detail": "Staged row differs from archived legacy evidence"})

    if not baseline["ok"]:
        return result

    try:
        path = Path(staging_path)
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA query_only=ON")
            db.execute("BEGIN")
            try:
                archive = {}
                for record in db.execute(
                    "SELECT table_name,ordinal,payload FROM legacy_archive ORDER BY table_name,ordinal"
                ):
                    archive.setdefault(record["table_name"], []).append(json.loads(record["payload"]))
                source_runs = archive.get("runs", [])
                source_states = archive.get("finding_states", [])
                source_events = archive.get("finding_events", [])
                source_scopes = archive.get("issue_runs", [])
                manifests = db.execute(
                    "SELECT org,project,revision FROM import_manifest"
                ).fetchall()
                if len(manifests) != 1:
                    flag("manifest")
                    return dict(result, ok=False, problems=problems)
                manifest = manifests[0]
                runs = {r["id"]: r for r in db.execute(
                    "SELECT id,org,project,actor,created_at,revision,scope,files,issues,previous_id FROM runs"
                )}
                decisions = {
                    (r["run_id"], r["ordinal"]): r for r in db.execute(
                        "SELECT org,project,run_id,ordinal,status,comment,version,updated_at FROM decisions"
                    )
                }
                events = {r["id"]: r for r in db.execute(
                    "SELECT id,org,project,run_id,ordinal,actor,changed_at,"
                    "old_status,new_status,old_comment,new_comment,old_version,new_version FROM events"
                )}

                # Source rows must have a one-to-one identity-preserving mapping.
                original = {r["id"]: r for r in source_runs}
                states = {r["id"]: r for r in source_states}
                if len(original) != len(source_runs) or set(original) != set(runs):
                    flag("run_mapping")
                if len(states) != len(source_states) or len(decisions) != len(source_states):
                    flag("decision_mapping")
                legacy_scopes = {r["run_id"]: r["scope"] for r in source_scopes}
                if len(legacy_scopes) != len(source_scopes) or set(legacy_scopes) != set(original):
                    flag("scope_mapping")

                last_by_scope = {}
                for run_id in sorted(original):
                    row = original[run_id]
                    staged = runs.get(run_id)
                    if staged is None:
                        continue
                    files = json.loads(row["filenames"])
                    scope_key = run_scope(files)
                    prior = last_by_scope.get(scope_key)
                    expected = (
                        manifest["org"], manifest["project"], "legacy_unknown",
                        row["created_at"], manifest["revision"], scope_key,
                        row["filenames"], row["issues"], prior,
                    )
                    actual = tuple(staged[key] for key in (
                        "org", "project", "actor", "created_at", "revision",
                        "scope", "files", "issues", "previous_id",
                    ))
                    if actual != expected or legacy_scopes.get(run_id) != scope_key:
                        flag("run_mapping")
                    last_by_scope[scope_key] = run_id

                expected_decision_keys = set()
                for state in source_states:
                    key = (state["run_id"], state["issue_index"])
                    expected_decision_keys.add(key)
                    row = decisions.get(key)
                    if row is None:
                        flag("decision_mapping")
                        continue
                    expected = (
                        manifest["org"], manifest["project"], state["run_id"],
                        state["issue_index"], state["status"], state["comment"],
                        state["version"], state["updated_at"],
                    )
                    actual = tuple(row[name] for name in (
                        "org", "project", "run_id", "ordinal",
                        "status", "comment", "version", "updated_at",
                    ))
                    if actual != expected:
                        flag("decision_mapping")
                if set(decisions) != expected_decision_keys:
                    flag("decision_mapping")

                expected_event_ids = set()
                for event in source_events:
                    expected_event_ids.add(event["id"])
                    source_state = states.get(event["finding_id"])
                    staged = events.get(event["id"])
                    if source_state is None or staged is None:
                        flag("event_mapping")
                        continue
                    expected = (
                        manifest["org"], manifest["project"],
                        source_state["run_id"], source_state["issue_index"],
                        "legacy_unknown", event["changed_at"],
                        event["old_status"], event["new_status"],
                        event["old_comment"], event["new_comment"],
                        event["old_version"], event["new_version"],
                    )
                    actual = tuple(staged[key] for key in (
                        "org", "project", "run_id", "ordinal", "actor", "changed_at",
                        "old_status", "new_status", "old_comment", "new_comment",
                        "old_version", "new_version",
                    ))
                    if actual != expected:
                        flag("event_mapping")
                if set(events) != expected_event_ids or len(expected_event_ids) != len(source_events):
                    flag("event_mapping")

                result["ok"] = not problems
                result["problems"] = problems
                result["reconciled"] = not problems
                return result
            finally:
                db.rollback()
    except (sqlite3.DatabaseError, KeyError, TypeError, ValueError, IndexError):
        flag("reconciliation_failure")
        return dict(result, ok=False, problems=problems)
