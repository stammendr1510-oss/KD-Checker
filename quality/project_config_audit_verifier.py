"""Read-only structural verification of versioned *project configuration* audit.

This is an offline diagnostic for a trusted, quiescent, server-local SQLite copy.
It cannot prove authenticity against an attacker able to rewrite all records.
It must never be exposed as a user-accessible HTTP endpoint.
"""
from contextlib import closing
import json
from pathlib import Path
import sqlite3

from project_configuration import DEFAULT


MAX_PROBLEMS = 100


def inspect_project_config_history(path):
    """Inspect config revisions without opening the database for writing.

    Returns only counts and bounded structural findings, not settings or actors.
    A schema that predates project-configuration auditing is unsupported.
    """
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(source)

    findings = []

    def fail(code, detail):
        if len(findings) < MAX_PROBLEMS:
            findings.append({"code": code, "detail": detail})

    def result(counts=None):
        return {"ok": not findings, "problems": findings,
                "counts": counts if counts is not None else {}}

    def decode(raw, code):
        try:
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise ValueError("not an object")
            return value
        except (TypeError, ValueError):
            fail(code, "Invalid configuration JSON object")
            return None

    with closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        try:
            if [r[0] for r in db.execute("PRAGMA integrity_check").fetchall()] != ["ok"]:
                fail("sqlite_integrity", "SQLite integrity_check failed")
            if db.execute("PRAGMA foreign_key_check").fetchone() is not None:
                fail("foreign_keys", "SQLite foreign_key_check failed")
            try:
                versions = [r[0] for r in db.execute("SELECT version FROM schema_version")]
                if versions != [1]:
                    fail("schema", "Unexpected scoped_history schema version")
                    return result()
                configurations = db.execute("""
                    SELECT org, project, payload, version
                    FROM project_config ORDER BY org, project
                """).fetchall()
                history = db.execute("""
                    SELECT org, project, actor, changed_at, old_version, new_version,
                           old_payload, new_payload
                    FROM project_config_events
                    ORDER BY org, project, new_version
                """).fetchall()
            except sqlite3.DatabaseError:
                fail("schema", "Versioned configuration audit tables are unavailable")
                return result()

            changes = {}
            for item in history:
                key = (item["org"], item["project"])
                changes.setdefault(key, []).append(item)

            config_keys = set()
            for item in configurations:
                key = (item["org"], item["project"])
                config_keys.add(key)
                current = decode(item["payload"], "config_payload")
                events = changes.get(key, [])

                if not events:
                    # Existing pre-audit settings may have been brought forward
                    # as version zero; do not falsely attest to their history.
                    if item["version"] != 0 or current != DEFAULT:
                        fail("unaudited_config", "Stored configuration has no verifiable change chain")
                    continue

                version = 0
                previous = DEFAULT
                last_valid = True
                for event in events:
                    old = decode(event["old_payload"], "audit_payload")
                    new = decode(event["new_payload"], "audit_payload")
                    if (event["old_version"] != version
                            or event["new_version"] != version + 1
                            or old != previous or old is None or new is None):
                        fail("config_audit_chain", "Config version or audit payload chain mismatch")
                        last_valid = False
                        break
                    if new == old:
                        fail("config_audit_noop", "No-op update has an audit event")
                    if not isinstance(event["actor"], str) or not event["actor"].strip() or not event["changed_at"]:
                        fail("config_audit_actor", "Missing config audit actor or timestamp")
                    previous, version = new, version + 1

                if last_valid and (item["version"] != version or current != previous):
                    fail("config_final_state", "Current config differs from audited final state")

            if set(changes) - config_keys:
                fail("config_orphan_events", "Config audit exists without a current config record")

            return result({
                "projects_with_config": len(configurations),
                "config_events": len(history),
            })
        except sqlite3.DatabaseError:
            fail("sqlite_error", "Could not inspect the SQLite snapshot")
            return result()
        finally:
            db.rollback()
