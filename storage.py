"""SQLite run history (metadata and findings, not uploaded CAD documents)."""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB = Path(__file__).resolve().parent / "data" / "history.sqlite3"

def connect():
    DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB)
    con.execute("CREATE TABLE IF NOT EXISTS runs (id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, filenames TEXT NOT NULL, issues TEXT NOT NULL)")
    return con

def save_run(names, issues):
    with connect() as con:
        cur = con.execute("INSERT INTO runs(created_at, filenames, issues) VALUES(?,?,?)", (datetime.now(timezone.utc).isoformat(), json.dumps(names, ensure_ascii=False), json.dumps(issues, ensure_ascii=False)))
        return cur.lastrowid

def get_run(run_id):
    with connect() as con:
        row = con.execute("SELECT id, created_at, filenames, issues FROM runs WHERE id=?", (run_id,)).fetchone()
    return {"id":row[0], "created_at":row[1], "files":json.loads(row[2]), "issues":json.loads(row[3])} if row else None

def list_runs():
    with connect() as con:
        rows = con.execute("SELECT id, created_at, filenames, issues FROM runs ORDER BY id DESC LIMIT 40").fetchall()
    return [{"id":r[0], "created_at":r[1], "file_count":len(json.loads(r[2])), "issue_count":len(json.loads(r[3]))} for r in rows]
