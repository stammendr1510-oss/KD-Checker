"""Read-only preflight for a completed *offline* legacy staging import.

Checks structural evidence, not provenance/authenticity. The trusted operator
must use quiescent local copies. Never opens a SQLite database for writing.
Diagnostic output intentionally excludes organization, actor and document data.
"""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3


MAX_FINDINGS = 100
KNOWN_SOURCE = {
    "runs", "issue_runs", "finding_states", "finding_events", "issue_schema",
    "sqlite_sequence", "checklist_run_snapshots",
}
REQUIRED_SOURCE = {
    "runs", "issue_runs", "finding_states", "finding_events", "issue_schema",
}


def verify_staging_import(staging_path, *, verified_backup=None):
    """Report bounded metadata from a completed staging SQLite, without writes.

    A supplied verified_backup is read and hashed solely to compare its bytes
    to the import manifest. Without it, provenance cannot be established.
    Both inputs must be trusted, quiescent, regular local files.
    """
    source = Path(staging_path)
    if not source.is_file():
        raise FileNotFoundError(source)
    findings = []

    def flag(code, message):
        if len(findings) < MAX_FINDINGS:
            findings.append({"code": code, "detail": message})

    def output(counts=None):
        return {"ok": not findings, "problems": findings,
                "counts": counts or {},
                "backup_checked": verified_backup is not None}

    try:
        with closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA query_only=ON")
            db.execute("BEGIN")
            try:
                if [r[0] for r in db.execute("PRAGMA integrity_check")] != ["ok"]:
                    flag("sqlite_integrity", "Staging SQLite integrity failure")
                if db.execute("PRAGMA foreign_key_check").fetchone() is not None:
                    flag("foreign_keys", "Staging foreign key violation")
                try:
                    version = [r[0] for r in db.execute("SELECT version FROM schema_version")]
                    if version != [1]:
                        flag("schema", "Unsupported staging schema")
                        return output()
                    manifests = db.execute(
                        "SELECT source_sha256, org, project, revision, operator FROM import_manifest"
                    ).fetchall()
                    rows = db.execute(
                        "SELECT table_name, ordinal, payload FROM legacy_archive "
                        "ORDER BY table_name, ordinal"
                    ).fetchall()
                    runs = db.execute(
                        "SELECT id,org,project,actor,revision,files,issues FROM runs"
                    ).fetchall()
                    decisions = db.execute(
                        "SELECT org,project,run_id,ordinal,status,comment,version,updated_at "
                        "FROM decisions"
                    ).fetchall()
                    events = db.execute(
                        "SELECT org,project,run_id,ordinal,actor,changed_at,"
                        "old_status,new_status,old_comment,new_comment,old_version,new_version "
                        "FROM events ORDER BY org,project,run_id,ordinal,new_version"
                    ).fetchall()
                except sqlite3.DatabaseError:
                    flag("schema", "Staging import tables or required columns are missing")
                    return output()

                if len(manifests) != 1:
                    flag("manifest", "Expected exactly one import manifest")
                    return output({"runs": len(runs), "decisions": len(decisions),
                                   "events": len(events), "archive_rows": len(rows)})
                m = manifests[0]
                if (any(not isinstance(m[key], str) or not m[key].strip()
                        for key in ("org", "project", "revision", "operator"))
                        or not isinstance(m["source_sha256"], str)
                        or len(m["source_sha256"]) != 64
                        or any(c not in "0123456789abcdef" for c in m["source_sha256"])):
                    flag("manifest", "Invalid import manifest metadata or SHA-256 digest")
                scope = (m["org"], m["project"])
                archive = {}
                for row in rows:
                    name = row["table_name"]
                    if name not in KNOWN_SOURCE:
                        flag("archive_table", "Unexpected legacy archive table")
                        continue
                    try:
                        payload = json.loads(row["payload"])
                        if not isinstance(payload, dict):
                            raise ValueError("Not a source row")
                    except (ValueError, TypeError):
                        flag("archive_payload", "Malformed archived legacy source row")
                        continue
                    archive.setdefault(name, []).append((row["ordinal"], payload))
                for name, entries in archive.items():
                    if [i for i, _ in entries] != list(range(len(entries))):
                        flag("archive_order", "Missing or non-contiguous archive ordinal")
                if not {r["table_name"] for r in rows} <= KNOWN_SOURCE:
                    flag("archive_table", "Unrecognized legacy source table")

                if any((run["org"], run["project"]) != scope
                       or run["actor"] != "legacy_unknown"
                       or run["revision"] != m["revision"] for run in runs):
                    flag("run_identity", "Staged run differs from import manifest assignment")
                if any((row["org"], row["project"]) != scope for row in decisions):
                    flag("decision_scope", "Staged decision outside assigned scope")
                if any((row["org"], row["project"]) != scope
                       or row["actor"] != "legacy_unknown" for row in events):
                    flag("event_identity", "Imported event must retain legacy_unknown author")

                archived_run_ids = {
                    entry["id"] for _, entry in archive.get("runs", [])
                    if type(entry.get("id")) is int
                }
                staged_run_ids = {row["id"] for row in runs}
                if archived_run_ids != staged_run_ids or len(archive.get("runs", [])) != len(runs):
                    flag("run_cardinality", "Archived and staged runs differ")
                if len(archive.get("finding_states", [])) != len(decisions):
                    flag("decision_cardinality", "Archive and staged finding counts differ")
                if len(archive.get("finding_events", [])) != len(events):
                    flag("event_cardinality", "Archive and staged event counts differ")
                if not archive.get("issue_schema"):
                    flag("archive_schema", "Source issue schema not archived")

                issue_counts = {}
                for run in runs:
                    try:
                        items = json.loads(run["issues"])
                        files = json.loads(run["files"])
                        if not isinstance(items, list) or not isinstance(files, list):
                            raise ValueError("Unexpected JSON types")
                        issue_counts[run["id"]] = len(items)
                    except (ValueError, TypeError):
                        flag("run_payload", "Malformed staged run JSON")
                grouped = {}
                for d in decisions:
                    grouped.setdefault(d["run_id"], []).append(d["ordinal"])
                for run_id, n in issue_counts.items():
                    if sorted(grouped.get(run_id, [])) != list(range(n)):
                        flag("finding_cardinality", "Decisions not aligned with run issues")

                by_finding = {}
                for e in events:
                    by_finding.setdefault((e["org"], e["project"], e["run_id"], e["ordinal"]), []).append(e)
                for d in decisions:
                    key = (d["org"], d["project"], d["run_id"], d["ordinal"])
                    state, comment, version, timestamp = "new", "", 0, None
                    broken = False
                    for e in by_finding.get(key, []):
                        if ((e["old_status"], e["old_comment"], e["old_version"])
                            != (state, comment, version)
                                or e["new_version"] != version + 1):
                            flag("audit_chain", "Broken staged decision audit history")
                            broken = True
                            break
                        state, comment, version, timestamp = (
                            e["new_status"], e["new_comment"],
                            e["new_version"], e["changed_at"],
                        )
                    if not broken and (d["status"], d["comment"], d["version"], d["updated_at"]) != (
                        state, comment, version, timestamp
                    ):
                        flag("audit_final", "Decision does not match latest audit state")

                if verified_backup is not None:
                    backup = Path(verified_backup)
                    if not backup.is_file():
                        raise FileNotFoundError(backup)
                    digest = hashlib.sha256()
                    with backup.open("rb") as stream:
                        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                            digest.update(chunk)
                    if digest.hexdigest() != m["source_sha256"]:
                        flag("backup_digest", "Backup does not match import manifest")
                    else:
                        # Check source archive completeness, including optional tables.
                        with closing(sqlite3.connect(backup.resolve().as_uri() + "?mode=ro", uri=True)) as original:
                            original.row_factory = sqlite3.Row
                            original.execute("PRAGMA query_only=ON")
                            names = {r[0] for r in original.execute(
                                "SELECT name FROM sqlite_master WHERE type='table'"
                            )}
                            if names - KNOWN_SOURCE or not REQUIRED_SOURCE <= names:
                                flag("backup_schema", "Unexpected legacy source schema")
                            else:
                                for name in names:
                                    actual = [dict(r) for r in original.execute(
                                        f'SELECT * FROM "{name}" ORDER BY rowid'
                                    )]
                                    stored = [value for _, value in archive.get(name, [])]
                                    if actual != stored:
                                        flag("archive_mismatch", "Archive differs from verified source")
                return output({
                    "runs": len(runs), "decisions": len(decisions),
                    "events": len(events), "archive_rows": len(rows),
                })
            finally:
                db.rollback()
    except sqlite3.DatabaseError:
        flag("sqlite_error", "Unable to verify the staging database or backup")
        return output()
