"""Tenant-scoped repository prototype on a new, server-local SQLite database."""
from contextlib import contextmanager, closing
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3

from issue_tracking import run_scope, validate_decision
from run_comparison import compare_runs
from run_provenance import normalize as normalize_provenance, UNKNOWN, comparison as compare_provenance


class Denied(Exception): pass
class Missing(Exception): pass
class Conflict(Exception): pass


@dataclass(frozen=True)
class Scope:
    organization: str
    project: str

    def __post_init__(self):
        if any(not isinstance(v, str) or not v.strip() or len(v) > 200
               for v in (self.organization, self.project)):
            raise ValueError('Explicit organization/project required')


SCHEMA = """
CREATE TABLE schema_version(version INTEGER NOT NULL);
INSERT INTO schema_version VALUES(1);
CREATE TABLE projects(org TEXT, project TEXT, PRIMARY KEY(org,project));
CREATE TABLE memberships(subject TEXT, org TEXT, project TEXT, role TEXT NOT NULL
 CHECK(role IN ('viewer','engineer','admin')), PRIMARY KEY(subject,org,project),
 FOREIGN KEY(org,project) REFERENCES projects(org,project));
CREATE TABLE runs(id INTEGER PRIMARY KEY AUTOINCREMENT, org TEXT NOT NULL, project TEXT NOT NULL,
 actor TEXT NOT NULL, created_at TEXT NOT NULL, revision TEXT NOT NULL, scope TEXT NOT NULL,
 files TEXT NOT NULL, issues TEXT NOT NULL, previous_id INTEGER,
 UNIQUE(org,project,id), FOREIGN KEY(org,project) REFERENCES projects(org,project),
 FOREIGN KEY(org,project,previous_id) REFERENCES runs(org,project,id));
CREATE TABLE decisions(org TEXT, project TEXT, run_id INTEGER, ordinal INTEGER,
 status TEXT NOT NULL DEFAULT 'new' CHECK(status IN ('new','in_progress','resolved','rejected')),
 comment TEXT NOT NULL DEFAULT '', version INTEGER NOT NULL DEFAULT 0 CHECK(version>=0), updated_at TEXT,
 PRIMARY KEY(org,project,run_id,ordinal),
 FOREIGN KEY(org,project,run_id) REFERENCES runs(org,project,id));
CREATE TABLE events(id INTEGER PRIMARY KEY AUTOINCREMENT, org TEXT, project TEXT, run_id INTEGER,
 ordinal INTEGER, actor TEXT NOT NULL, changed_at TEXT NOT NULL,
 old_status TEXT NOT NULL, new_status TEXT NOT NULL, old_comment TEXT NOT NULL, new_comment TEXT NOT NULL,
 old_version INTEGER NOT NULL, new_version INTEGER NOT NULL,
 UNIQUE(org,project,run_id,ordinal,new_version), CHECK(new_version=old_version+1),
 FOREIGN KEY(org,project,run_id,ordinal) REFERENCES decisions(org,project,run_id,ordinal));
"""


class Repository:
    def __init__(self, path):
        self.path = Path(path)
        if not self.path.is_file():
            raise FileNotFoundError(self.path)
        with self.transaction() as con:
            if [r[0] for r in con.execute('SELECT version FROM schema_version')] != [1]:
                raise ValueError('Unsupported scoped schema')

    @classmethod
    def create(cls, path):
        path = Path(path)
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        try:
            with closing(sqlite3.connect(path)) as con:
                con.executescript(SCHEMA)
            return cls(path)
        except BaseException:
            path.unlink(missing_ok=True)
            raise

    @contextmanager
    def transaction(self, write=False):
        with closing(sqlite3.connect(self.path, timeout=30)) as con, con:
            con.row_factory = sqlite3.Row
            con.execute('PRAGMA foreign_keys=ON')
            con.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
            yield con

    def provision(self, subject, scope, role):
        """Trusted offline provisioning ONLY; never expose through user HTTP."""
        if not isinstance(subject, str) or not subject.strip() or len(subject) > 200 or role not in ('viewer', 'engineer', 'admin'):
            raise ValueError('Invalid membership')
        with self.transaction(True) as con:
            con.execute('INSERT OR IGNORE INTO projects VALUES(?,?)', (scope.organization, scope.project))
            con.execute('INSERT OR REPLACE INTO memberships VALUES(?,?,?,?)',
                        (subject, scope.organization, scope.project, role))

    def revoke(self, subject, scope):
        with self.transaction(True) as con:
            con.execute('DELETE FROM memberships WHERE subject=? AND org=? AND project=?',
                        (subject, scope.organization, scope.project))

    def _member(self, con, subject, scope, write=False):
        row = con.execute('SELECT role FROM memberships WHERE subject=? AND org=? AND project=?',
                          (subject, scope.organization, scope.project)).fetchone()
        if row is None or (write and row['role'] not in ('engineer', 'admin')):
            raise Denied()

    def save(self, subject, scope, files, issues, *, revision, provenance=None):
        if not isinstance(revision, str) or not revision.strip() or len(revision) > 200:
            raise ValueError('Explicit revision required')
        required = ('severity', 'code', 'file', 'location', 'message', 'recommendation')
        if (not isinstance(files, list) or len(files) > 100
                or any(not isinstance(name, str) or not name or len(name) > 1000 for name in files)
                or not isinstance(issues, list) or len(issues) > 10000
                or any(not isinstance(item, dict) or any(not isinstance(item.get(k), str) for k in required)
                       or item['severity'] not in ('info', 'warning', 'error') for item in issues)):
            raise ValueError('Invalid bounded run payload')
        if len(json.dumps(issues, ensure_ascii=False, allow_nan=False)) > 10_000_000:
            raise ValueError('Run payload too large')
        evidence = normalize_provenance(provenance) if provenance is not None else None
        with self.transaction(True) as con:
            self._member(con, subject, scope, True)
            previous = con.execute('SELECT id FROM runs WHERE org=? AND project=? AND scope=? ORDER BY id DESC LIMIT 1',
                        (scope.organization, scope.project, run_scope(files))).fetchone()
            row = con.execute('''INSERT INTO runs(org,project,actor,created_at,revision,scope,files,issues,previous_id)
                       VALUES(?,?,?,?,?,?,?,?,?)''', (scope.organization, scope.project, subject,
                       datetime.now(timezone.utc).isoformat(), revision, run_scope(files),
                       json.dumps(files, ensure_ascii=False), json.dumps(issues, ensure_ascii=False),
                       previous['id'] if previous else None))
            identifier = row.lastrowid
            con.executemany('INSERT INTO decisions(org,project,run_id,ordinal) VALUES(?,?,?,?)',
                            [(scope.organization, scope.project, identifier, n) for n in range(len(issues))])
            if evidence is not None:
                con.execute('''CREATE TABLE IF NOT EXISTS run_provenance(
                    org TEXT, project TEXT, run_id INTEGER, payload TEXT NOT NULL CHECK(length(CAST(payload AS BLOB))<=16384),
                    PRIMARY KEY(org,project,run_id),
                    FOREIGN KEY(org,project,run_id) REFERENCES runs(org,project,id))''')
                con.execute('''CREATE TRIGGER IF NOT EXISTS run_provenance_no_update
                    BEFORE UPDATE ON run_provenance BEGIN SELECT RAISE(ABORT,'immutable run provenance'); END''')
                con.execute('''CREATE TRIGGER IF NOT EXISTS run_provenance_no_delete
                    BEFORE DELETE ON run_provenance BEGIN SELECT RAISE(ABORT,'immutable run provenance'); END''')
                con.execute('INSERT INTO run_provenance VALUES(?,?,?,?)',
                            (scope.organization,scope.project,identifier,
                             json.dumps(evidence,ensure_ascii=False,sort_keys=True,allow_nan=False)))
            return identifier

    def _get(self, con, scope, identifier):
        row = con.execute('SELECT * FROM runs WHERE org=? AND project=? AND id=?',
                          (scope.organization, scope.project, identifier)).fetchone()
        if row is None:
            raise Missing()
        result = dict(row)
        result['files'], result['issues'] = json.loads(row['files']), json.loads(row['issues'])
        result['decisions'] = [dict(r) for r in con.execute(
            'SELECT * FROM decisions WHERE org=? AND project=? AND run_id=? ORDER BY ordinal',
            (scope.organization, scope.project, identifier))]
        result['provenance'] = dict(UNKNOWN)
        exists = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='run_provenance'").fetchone()
        if exists:
            evidence = con.execute('SELECT CASE WHEN length(CAST(payload AS BLOB))<=16384 THEN payload ELSE NULL END FROM run_provenance WHERE org=? AND project=? AND run_id=?',
                                   (scope.organization,scope.project,identifier)).fetchone()
            if evidence:
                try:
                    result['provenance'] = {'status':'recorded','evidence':normalize_provenance(json.loads(evidence[0]))}
                except (TypeError,ValueError):
                    raise RuntimeError('Invalid stored provenance') from None
        return result

    def get(self, subject, scope, identifier):
        with self.transaction() as con:
            self._member(con, subject, scope)
            return self._get(con, scope, identifier)

    def list(self, subject, scope):
        with self.transaction() as con:
            self._member(con, subject, scope)
            return [r['id'] for r in con.execute('SELECT id FROM runs WHERE org=? AND project=? ORDER BY id DESC LIMIT 40',
                                                (scope.organization, scope.project))]

    def compare(self, subject, scope, previous, current):
        with self.transaction() as con:
            self._member(con, subject, scope)
            before,after = self._get(con,scope,previous),self._get(con,scope,current)
            result = compare_runs(before,after)
            result['provenance_comparison'] = compare_provenance(before['provenance'],after['provenance'])
            return result

    def update(self, subject, scope, run_id, ordinal, status, comment, expected_version):
        validate_decision(status, comment)
        if type(expected_version) is not int or expected_version < 0:
            raise ValueError('Invalid version')
        with self.transaction(True) as con:
            self._member(con, subject, scope, True)
            key = (scope.organization, scope.project, run_id, ordinal)
            row = con.execute('SELECT * FROM decisions WHERE org=? AND project=? AND run_id=? AND ordinal=?', key).fetchone()
            if row is None:
                raise Missing()
            if row['version'] != expected_version:
                raise Conflict()
            if (row['status'], row['comment']) == (status, comment):
                return expected_version
            changed_at = datetime.now(timezone.utc).isoformat()
            con.execute('''INSERT INTO events(org,project,run_id,ordinal,actor,changed_at,
                old_status,new_status,old_comment,new_comment,old_version,new_version)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''', (*key, subject, changed_at,
                row['status'], status, row['comment'], comment, expected_version, expected_version+1))
            con.execute('UPDATE decisions SET status=?,comment=?,version=version+1,updated_at=? WHERE org=? AND project=? AND run_id=? AND ordinal=?',
                        (status, comment, changed_at, *key))
            return expected_version+1

    def history(self, subject, scope, run_id, ordinal):
        with self.transaction() as con:
            self._member(con, subject, scope)
            self._get(con, scope, run_id)
            return [dict(row) for row in con.execute('SELECT * FROM events WHERE org=? AND project=? AND run_id=? AND ordinal=? ORDER BY id',
                        (scope.organization, scope.project, run_id, ordinal))]
