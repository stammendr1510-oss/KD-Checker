"""Server-local SQLite session store for the synthetic secure application."""
from contextlib import contextmanager, closing
from pathlib import Path
import os
import secrets
import sqlite3
import time

from secure_sessions import Sessions, Session, Identity, digest


class DurableSessions(Sessions):
    def __init__(self, path, *, ttl=1800, idle=600, clock=time.time, capacity=1000):
        super().__init__(ttl=ttl, idle=idle, clock=clock, capacity=capacity)
        self.path = Path(path)
        if not self.path.is_file():
            raise FileNotFoundError(self.path)
        with self._transaction() as con:
            if con.execute('SELECT version FROM session_schema').fetchall() != [(1,)]:
                raise ValueError('Unsupported session schema')

    @classmethod
    def create(cls, path, **options):
        path = Path(path)
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        try:
            with closing(sqlite3.connect(path)) as con:
                con.executescript('''CREATE TABLE session_schema(version INTEGER);
                    INSERT INTO session_schema VALUES(1);
                    CREATE TABLE sessions(token_hash TEXT PRIMARY KEY, subject TEXT, org TEXT,
                    project TEXT, expires REAL, last_seen REAL, idle REAL, csrf_hash TEXT);''')
            return cls(path, **options)
        except BaseException:
            path.unlink(missing_ok=True)
            raise

    @contextmanager
    def _transaction(self):
        with closing(sqlite3.connect(self.path, timeout=30)) as con, con:
            con.execute('BEGIN IMMEDIATE')
            yield con

    def issue(self, identity):
        if not isinstance(identity, Identity):
            raise ValueError('Verified identity required')
        token, csrf, now = secrets.token_urlsafe(32), secrets.token_urlsafe(32), self.clock()
        with self._transaction() as con:
            con.execute('DELETE FROM sessions WHERE expires<=? OR last_seen+idle<=?', (now,now))
            if con.execute('SELECT count(*) FROM sessions').fetchone()[0] >= self.capacity:
                raise RuntimeError('Session capacity reached')
            con.execute('INSERT INTO sessions VALUES(?,?,?,?,?,?,?,?)',
                        (digest(token),identity.subject,identity.organization,identity.project,
                         now+self.ttl,now,self.idle,digest(csrf)))
        return token, csrf

    def resolve(self, token):
        if not isinstance(token,str) or not 20 <= len(token) <= 200:
            return None
        now = self.clock()
        with self._transaction() as con:
            row = con.execute('SELECT subject,org,project,expires,last_seen,idle,csrf_hash FROM sessions WHERE token_hash=?',
                              (digest(token),)).fetchone()
            if row is None:
                return None
            if now >= row[3] or now-row[4] >= row[5] or now < row[4]:
                con.execute('DELETE FROM sessions WHERE token_hash=?',(digest(token),))
                return None
            return Session(Identity(*row[:3]),row[3],row[4],row[6])

    def touch(self, token):
        if not isinstance(token,str):
            return
        now = self.clock()
        with self._transaction() as con:
            con.execute('UPDATE sessions SET last_seen=? WHERE token_hash=? AND expires>? AND last_seen+idle>? AND last_seen<=?',
                        (now,digest(token),now,now,now))

    def revoke(self, token):
        if isinstance(token,str):
            with self._transaction() as con:
                con.execute('DELETE FROM sessions WHERE token_hash=?',(digest(token),))
