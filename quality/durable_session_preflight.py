"""Offline structural preflight for a trusted copy of the durable session database.

Never authenticates a caller, never recovers tokens and never mutates the source.
No account identifiers, session hashes or secrets appear in diagnostic output.
This is NOT a production forensic or cryptographic attestation tool.
"""
from contextlib import closing
import math
from pathlib import Path
import sqlite3
import string


_MAX_PROBLEMS = 100
_HEX = frozenset(string.hexdigits.lower())


def inspect_durable_sessions(path):
    """Read an offline SQLite copy in mode=ro and report bounded generic findings."""
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(source)
    problems = []
    counts = {}

    def report(code, detail):
        if len(problems) < _MAX_PROBLEMS:
            problems.append({"code": code, "detail": detail})

    def output():
        return {"ok": not problems, "problems": problems, "counts": counts}

    try:
        with closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as con:
            con.row_factory = sqlite3.Row
            con.execute("PRAGMA query_only=ON")
            con.execute("BEGIN")
            try:
                integrity = [row[0] for row in con.execute("PRAGMA integrity_check")]
                if integrity != ["ok"]:
                    report("sqlite_integrity", "SQLite integrity check failed")
                if con.execute("PRAGMA foreign_key_check").fetchone() is not None:
                    report("foreign_keys", "SQLite foreign key check failed")
                try:
                    versions = [row[0] for row in con.execute("SELECT version FROM session_schema")]
                    if versions != [1]:
                        report("session_schema", "Unsupported session schema")
                        return output()
                    rows = con.execute("""
                        SELECT token_hash, csrf_hash, subject, org, project,
                               expires, last_seen, idle
                        FROM sessions
                    """).fetchall()
                except sqlite3.DatabaseError:
                    report("session_schema", "Missing or invalid session tables")
                    return output()
                counts["session_records"] = len(rows)
                for row in rows:
                    for name in ("token_hash", "csrf_hash"):
                        value = row[name]
                        if (not isinstance(value, str) or len(value) != 64
                                or any(letter not in _HEX for letter in value)
                                or value != value.lower()):
                            report("token_digest", "Session token digest is not lowercase SHA-256")
                    for name in ("subject", "org", "project"):
                        value = row[name]
                        if not isinstance(value, str) or not value.strip():
                            report("session_identity", "Missing session identity binding")
                    timestamps = (row["expires"], row["last_seen"], row["idle"])
                    if (any(type(value) not in (int, float) or not math.isfinite(value)
                            for value in timestamps)
                            or row["idle"] <= 0
                            or row["expires"] <= row["last_seen"]):
                        report("session_time", "Invalid expiry, last_seen or idle window")
                return output()
            finally:
                con.rollback()
    except sqlite3.DatabaseError:
        report("sqlite_error", "Cannot inspect offline session database")
        return output()
