"""Read-only structural and audit-chain checks for synthetic scoped_history databases.

This is an integrity diagnostic, NOT cryptographic attestation, ESKD validation,
a migration utility, or a repair mechanism. Never writes to the source database.
"""
from contextlib import closing
from pathlib import Path
import json
import sqlite3


EXPECTED_SCHEMA = 1
MAX_REPORTED_PROBLEMS = 100


def inspect_scoped_history(path):
    """Return a bounded diagnostic result; reject unexpected schema and corruption.

    Use a trusted, quiescent local copy of the database, not a live database
    concurrently modified by other processes. SQLite mode=ro forbids writes.
    """
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(source)
    problems = []

    def report(code, detail):
        if len(problems) < MAX_REPORTED_PROBLEMS:
            problems.append({"code": code, "detail": detail})

    with closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as con:
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA query_only=ON")
        con.execute("BEGIN")
        try:
            integrity = con.execute("PRAGMA integrity_check").fetchall()
            if [r[0] for r in integrity] != ["ok"]:
                report("sqlite_integrity", "SQLite integrity_check did not report ok")
            foreign = con.execute("PRAGMA foreign_key_check").fetchall()
            if foreign:
                report("foreign_keys", "SQLite foreign_key_check reported violations")
            try:
                versions = [r[0] for r in con.execute("SELECT version FROM schema_version")]
            except sqlite3.DatabaseError:
                report("schema", "Missing or invalid schema_version")
                return {"ok": False, "problems": problems, "counts": {}}
            if versions != [EXPECTED_SCHEMA]:
                report("schema", "Unsupported scoped schema_version")
                return {"ok": False, "problems": problems, "counts": {}}

            try:
                decisions = con.execute("""
                    SELECT org, project, run_id, ordinal, status, comment, version, updated_at
                    FROM decisions ORDER BY org, project, run_id, ordinal
                """).fetchall()
                events = con.execute("""
                    SELECT org, project, run_id, ordinal, actor, changed_at,
                           old_status, new_status, old_comment, new_comment,
                           old_version, new_version
                    FROM events ORDER BY org, project, run_id, ordinal, new_version
                """).fetchall()
                run_rows = con.execute("""
                    SELECT id, org, project, previous_id, files, issues, scope
                    FROM runs ORDER BY id
                """).fetchall()
                runs = len(run_rows)
            except sqlite3.DatabaseError:
                report("schema", "Required scoped history tables or columns unavailable")
                return {"ok": False, "problems": problems, "counts": {}}

            # Detect internally inconsistent snapshots even when every SQL FK passes.
            decisions_by_run = {}
            for item in decisions:
                key = (item["org"], item["project"], item["run_id"])
                decisions_by_run.setdefault(key, []).append(item["ordinal"])
            by_run_id = {run["id"]: run for run in run_rows}
            for run in run_rows:
                key = (run["org"], run["project"], run["id"])
                try:
                    issues = json.loads(run["issues"])
                    files = json.loads(run["files"])
                    if not isinstance(issues, list) or not isinstance(files, list):
                        raise ValueError("Unexpected JSON shapes")
                except (ValueError, TypeError):
                    report("run_payload", "Invalid run files or issues JSON")
                    continue
                ordinals = sorted(decisions_by_run.get(key, []))
                if ordinals != list(range(len(issues))):
                    report("finding_cardinality", "Finding decisions do not match issue indices")
                if run["previous_id"] is not None:
                    previous = by_run_id.get(run["previous_id"])
                    if (previous is None or previous["id"] >= run["id"]
                            or (previous["org"], previous["project"], previous["scope"])
                            != (run["org"], run["project"], run["scope"])):
                        report("previous_link", "Previous run does not belong to same ordered file scope")

            by_finding = {}
            for row in events:
                key = (row["org"], row["project"], row["run_id"], row["ordinal"])
                by_finding.setdefault(key, []).append(row)
            decision_keys = set()
            for item in decisions:
                key = (item["org"], item["project"], item["run_id"], item["ordinal"])
                decision_keys.add(key)
                history = by_finding.get(key, [])
                current_status, current_comment, current_version = "new", "", 0
                current_timestamp = None
                for event in history:
                    if (event["old_version"] != current_version
                            or event["new_version"] != current_version + 1
                            or event["old_status"] != current_status
                            or event["old_comment"] != current_comment):
                        report("audit_chain", "Non-contiguous or inconsistent transition")
                        # Do not infer a corrected chain from untrusted entries.
                        break
                    if not event["actor"] or not event["changed_at"]:
                        report("audit_actor_time", "Missing actor or change timestamp")
                    current_status = event["new_status"]
                    current_comment = event["new_comment"]
                    current_version = event["new_version"]
                    current_timestamp = event["changed_at"]
                else:
                    if (item["status"] != current_status
                            or item["comment"] != current_comment
                            or item["version"] != current_version
                            or item["updated_at"] != current_timestamp):
                        report("decision_state", "Decision differs from final audit event")
            if set(by_finding) - decision_keys:
                report("orphan_events", "Event has no matching decision")
            counts = {"runs": runs, "decisions": len(decisions), "events": len(events)}
            return {"ok": not problems, "problems": problems, "counts": counts}
        except sqlite3.DatabaseError:
            report("sqlite_error", "Database could not be read consistently")
            return {"ok": False, "problems": problems, "counts": {}}
        finally:
            con.rollback()
