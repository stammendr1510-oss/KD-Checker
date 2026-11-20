"""Offline SQLite history copies. No application configuration or live DB defaults."""
from contextlib import closing
from pathlib import Path
import sqlite3
import os


REQUIRED = {'runs', 'issue_schema', 'issue_runs', 'finding_states', 'finding_events'}


def _verify(con):
    if con.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
        raise ValueError('History integrity check failed')
    if con.execute('PRAGMA foreign_key_check').fetchall():
        raise ValueError('History foreign key check failed')
    tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not REQUIRED <= tables:
        raise ValueError('Not a migrated KD Checker history')
    if con.execute('SELECT version FROM issue_schema').fetchall() != [(1,)]:
        raise ValueError('Unsupported history schema')
    # Require the columns used by existing readers, without running migrations.
    con.execute('SELECT id,created_at,filenames,issues FROM runs LIMIT 0')
    con.execute('SELECT id,run_id,issue_index,fingerprint,status,comment,version,previous_id,match_kind FROM finding_states LIMIT 0')
    con.execute('SELECT finding_id,changed_at,old_version,new_version FROM finding_events LIMIT 0')


def copy_history(source, destination):
    """Copy a migrated history into a NEW file; refuse every existing destination.

    Caller must stop the application and control both directories. SQLite backup
    copies all tables, including optional side tables, without serializing data.
    A failed copy is removed; no source, existing destination or working settings
    are replaced. This is a local maintenance tool, not online backup scheduling.
    """
    source, destination = Path(source).resolve(), Path(destination).absolute()
    if not source.is_file():
        raise FileNotFoundError(source)
    # mode=ro prevents accidentally creating or modifying the source database.
    with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True, timeout=30)) as saved:
        _verify(saved)
        # Exclusive creation also rejects symlinks and aliases to an existing DB.
        fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        try:
            with closing(sqlite3.connect(destination, timeout=30)) as target:
                saved.backup(target)
                _verify(target)
        except BaseException:
            destination.unlink(missing_ok=True)
            raise
    return destination


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Copy/restore stopped KD Checker history into a new file')
    parser.add_argument('source', type=Path)
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    copy_history(args.source, args.destination)
    print('History copy verified; working database was not replaced')
