"""Immutable check results with separate, audited engineering decisions in SQLite."""
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import threading

from issue_tracking import finding_identity, run_scope, validate_decision

DB = Path(__file__).resolve().parent / 'data' / 'history.sqlite3'
_migration_lock = threading.Lock()


class FindingNotFound(Exception):
    pass


class FindingConflict(Exception):
    pass


def _now():
    return datetime.now(timezone.utc).isoformat()


def _backup(con, reason):
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    path = DB.with_name(f'{DB.name}.{reason}-{stamp}.bak')
    # Refuse to overwrite backups. SQLite backup also handles journal files.
    with path.open('xb'):
        pass
    with closing(sqlite3.connect(path)) as target:
        con.backup(target)
    return path


def _version(con):
    exists = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='issue_schema'").fetchone()
    return con.execute('SELECT version FROM issue_schema').fetchone()[0] if exists else 0


def _add_states(con, run_id, files, issues):
    scope = run_scope(files)
    prior = con.execute('SELECT run_id FROM issue_runs WHERE scope=? AND run_id<? ORDER BY run_id DESC LIMIT 1',
                        (scope, run_id)).fetchone()
    previous = {}
    if prior:
        for state in con.execute('SELECT id,fingerprint FROM finding_states WHERE run_id=?', (prior[0],)):
            previous.setdefault(state['fingerprint'], []).append(state['id'])
    fingerprints = [finding_identity(item) for item in issues]
    counts = Counter(fingerprints)
    con.execute('INSERT INTO issue_runs(run_id,scope) VALUES(?,?)', (run_id, scope))
    for index, fingerprint in enumerate(fingerprints):
        candidates = previous.get(fingerprint, [])
        ambiguous = counts[fingerprint] > 1 or len(candidates) > 1
        previous_id = candidates[0] if len(candidates) == 1 and not ambiguous else None
        kind = 'ambiguous' if ambiguous else ('exact' if previous_id else 'none')
        # Each occurrence starts new, even if a previous one was resolved.
        con.execute('''INSERT INTO finding_states(run_id,issue_index,fingerprint,previous_id,match_kind)
                       VALUES(?,?,?,?,?)''', (run_id, index, fingerprint, previous_id, kind))


def _migrate(con):
    version = _version(con)
    if version == 1:
        return
    if version != 0:
        raise RuntimeError('Неизвестная версия схемы замечаний; требуется совместимая версия приложения')
    if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='runs'").fetchone():
        _backup(con, 'before-issue-management')
    con.execute('BEGIN IMMEDIATE')
    try:
        if _version(con) == 1:
            con.commit()
            return
        con.execute('''CREATE TABLE IF NOT EXISTS runs
            (id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL,
             filenames TEXT NOT NULL, issues TEXT NOT NULL)''')
        con.execute('CREATE TABLE issue_schema(version INTEGER NOT NULL, applied_at TEXT NOT NULL)')
        con.execute('CREATE TABLE issue_runs(run_id INTEGER PRIMARY KEY REFERENCES runs(id), scope TEXT NOT NULL)')
        con.execute('CREATE INDEX issue_runs_scope ON issue_runs(scope,run_id)')
        con.execute('''CREATE TABLE finding_states(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL REFERENCES runs(id), issue_index INTEGER NOT NULL,
            fingerprint TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'new' CHECK(status IN ('new','in_progress','resolved','rejected')),
            comment TEXT NOT NULL DEFAULT '', updated_at TEXT,
            version INTEGER NOT NULL DEFAULT 0,
            previous_id INTEGER REFERENCES finding_states(id),
            match_kind TEXT NOT NULL CHECK(match_kind IN ('none','exact','ambiguous')),
            UNIQUE(run_id,issue_index), CHECK(status!='rejected' OR length(trim(comment))>0))''')
        con.execute('''CREATE TABLE finding_events(
            id INTEGER PRIMARY KEY AUTOINCREMENT, finding_id INTEGER NOT NULL REFERENCES finding_states(id),
            changed_at TEXT NOT NULL, old_status TEXT NOT NULL, new_status TEXT NOT NULL,
            old_comment TEXT NOT NULL, new_comment TEXT NOT NULL,
            old_version INTEGER NOT NULL, new_version INTEGER NOT NULL)''')
        con.execute('CREATE INDEX finding_events_finding ON finding_events(finding_id,id)')
        for row in con.execute('SELECT id,filenames,issues FROM runs ORDER BY id').fetchall():
            _add_states(con, row['id'], json.loads(row['filenames']), json.loads(row['issues']))
        con.execute('INSERT INTO issue_schema VALUES(1,?)', (_now(),))
        con.commit()
    except Exception:
        con.rollback()
        raise


def connect():
    DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA foreign_keys=ON')
    try:
        with _migration_lock:
            _migrate(con)
    except Exception:
        con.close()
        raise
    return con


def save_run(names, issues, checklist=None, *, extracted_entities=None, app_metadata=None):
    """Snapshot findings and per-document completeness together, including reasons.

    Historical extraction statistics and build identity are never recomputed on read.
    """
    with closing(connect()) as con, con:
        con.execute('BEGIN IMMEDIATE')
        cur = con.execute('INSERT INTO runs(created_at,filenames,issues) VALUES(?,?,?)',
                          (_now(), json.dumps(names, ensure_ascii=False), json.dumps(issues, ensure_ascii=False)))
        run_id = cur.lastrowid
        _add_states(con, run_id, names, issues)
        if checklist is not None:
            # Optional additive side table: no change to historic runs or decisions.
            con.execute("""CREATE TABLE IF NOT EXISTS checklist_run_snapshots(
                run_id INTEGER PRIMARY KEY REFERENCES runs(id),
                snapshot TEXT NOT NULL
            )""")
            con.execute("INSERT INTO checklist_run_snapshots(run_id,snapshot) VALUES(?,?)",
                        (run_id, json.dumps(checklist, ensure_ascii=False)))
        if extracted_entities is not None or app_metadata is not None:
            con.execute("""CREATE TABLE IF NOT EXISTS run_observability(
                run_id INTEGER PRIMARY KEY REFERENCES runs(id),
                extracted_entities TEXT NOT NULL,
                app_metadata TEXT NOT NULL
            )""")
            con.execute("""INSERT INTO run_observability
                (run_id,extracted_entities,app_metadata) VALUES(?,?,?)""", (
                run_id,
                json.dumps(extracted_entities or {}, ensure_ascii=False),
                json.dumps(app_metadata or {}, ensure_ascii=False),
            ))
        return run_id


def _states(con, run_id):
    records = []
    for row in con.execute('SELECT * FROM finding_states WHERE run_id=? ORDER BY issue_index', (run_id,)):
        state = dict(row)
        state['previous'] = None
        if row['previous_id']:
            prior = con.execute('SELECT id,run_id,status,comment,updated_at,version FROM finding_states WHERE id=?',
                                (row['previous_id'],)).fetchone()
            state['previous'] = dict(prior) if prior else None
        records.append(state)
    return records


def get_run(run_id):
    with closing(connect()) as con, con:
        con.execute('BEGIN')
        row = con.execute('SELECT id,created_at,filenames,issues FROM runs WHERE id=?', (run_id,)).fetchone()
        if row is None:
            return None
        exists = con.execute("""SELECT 1 FROM sqlite_master
                                WHERE type='table' AND name='checklist_run_snapshots'""").fetchone()
        checklist_row = (con.execute(
            "SELECT snapshot FROM checklist_run_snapshots WHERE run_id=?", (run_id,)
        ).fetchone() if exists else None)
        checklist = (json.loads(checklist_row["snapshot"]) if checklist_row else {
            "status": "not_configured",
            "reason": "При этой проверке снимок EPLAN Checklist не был сохранён",
            "revision": None, "retrieved_at": None, "items": [], "reviews": [],
        })
        observability_exists = con.execute("""SELECT 1 FROM sqlite_master
            WHERE type='table' AND name='run_observability'""").fetchone()
        observability = (con.execute(
            "SELECT extracted_entities,app_metadata FROM run_observability WHERE run_id=?",
            (run_id,),
        ).fetchone() if observability_exists else None)
        return {'id': row['id'], 'created_at': row['created_at'], 'files': json.loads(row['filenames']),
                'issues': json.loads(row['issues']), 'finding_states': _states(con, run_id),
                'checklist': checklist,
                'extracted_entities': (json.loads(observability['extracted_entities'])
                                       if observability else {}),
                'app_metadata': (json.loads(observability['app_metadata'])
                                 if observability else {})}


def list_runs():
    with closing(connect()) as con:
        rows = con.execute('SELECT id,created_at,filenames,issues FROM runs ORDER BY id DESC LIMIT 40').fetchall()
        exists = con.execute("""SELECT 1 FROM sqlite_master
                                WHERE type='table' AND name='checklist_run_snapshots'""").fetchone()
        saved = {}
        if exists and rows:
            identifiers = [row['id'] for row in rows]
            placeholders = ','.join('?' for _ in identifiers)
            for record in con.execute(
                f'SELECT run_id,snapshot FROM checklist_run_snapshots WHERE run_id IN ({placeholders})',
                identifiers,
            ):
                saved[record['run_id']] = json.loads(record['snapshot'])
    return [{'id': r['id'], 'created_at': r['created_at'],
             'file_count': len(json.loads(r['filenames'])),
             'issue_count': len(json.loads(r['issues'])),
             'checklist_status': saved[r['id']].get('status', 'unavailable')
                 if r['id'] in saved else 'not_configured',
             'checklist_revision': saved[r['id']].get('revision') if r['id'] in saved else None}
            for r in rows]


def update_finding(run_id, finding_id, status, comment, expected_version):
    validate_decision(status, comment)
    with closing(connect()) as con, con:
        con.execute('BEGIN IMMEDIATE')
        row = con.execute('SELECT * FROM finding_states WHERE id=? AND run_id=?', (finding_id, run_id)).fetchone()
        if row is None:
            raise FindingNotFound()
        if row['version'] != expected_version:
            raise FindingConflict()
        if (row['status'], row['comment']) == (status, comment):
            return dict(row)
        changed_at = _now()
        con.execute('''INSERT INTO finding_events
            (finding_id,changed_at,old_status,new_status,old_comment,new_comment,old_version,new_version)
            VALUES(?,?,?,?,?,?,?,?)''',
            (finding_id, changed_at, row['status'], status, row['comment'], comment, row['version'], row['version']+1))
        con.execute('UPDATE finding_states SET status=?,comment=?,updated_at=?,version=version+1 WHERE id=?',
                    (status, comment, changed_at, finding_id))
        return dict(con.execute('SELECT * FROM finding_states WHERE id=?', (finding_id,)).fetchone())


def finding_history(run_id, finding_id):
    with closing(connect()) as con:
        if not con.execute('SELECT 1 FROM finding_states WHERE id=? AND run_id=?', (finding_id, run_id)).fetchone():
            raise FindingNotFound()
        return [dict(row) for row in con.execute('SELECT * FROM finding_events WHERE finding_id=? ORDER BY id',
                                                (finding_id,))]


def rollback_issue_management():
    """Maintenance only: retain all checks and back up decisions before dropping side tables."""
    if not DB.exists():
        return None
    with closing(sqlite3.connect(DB, timeout=30)) as con:
        con.row_factory = sqlite3.Row
        version = _version(con)
        if version == 0:
            return None
        if version != 1:
            raise RuntimeError('Откат неизвестной версии схемы запрещён')
        backup = _backup(con, 'before-issue-rollback')
        con.execute('BEGIN IMMEDIATE')
        try:
            for table in ('finding_events', 'finding_states', 'issue_runs', 'issue_schema'):
                con.execute(f'DROP TABLE {table}')
            con.commit()
        except Exception:
            con.rollback()
            raise
        return backup


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Миграция дополнительных таблиц управления замечаниями')
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--rollback-issue-management', action='store_true')
    group.add_argument('--migrate-issue-management', action='store_true')
    args = parser.parse_args()
    if args.rollback_issue_management:
        print(rollback_issue_management() or 'Миграция не применена')
    else:
        with closing(connect()):
            pass
        print('Схема управления замечаниями готова')
