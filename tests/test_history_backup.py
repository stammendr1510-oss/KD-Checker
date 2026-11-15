from contextlib import closing
import sqlite3

import pytest
import storage
import history_backup as backup


@pytest.fixture
def source(tmp_path, monkeypatch):
    path = tmp_path / 'source #1.sqlite3'
    monkeypatch.setattr(storage, 'DB', path)
    issue = dict(severity='info', code='TEST-1', file='synthetic.pdf',
                 location='PDF', message='Synthetic', recommendation='Review')
    run = storage.save_run(['synthetic.pdf'], [issue])
    state = storage.get_run(run)['finding_states'][0]
    storage.update_finding(run, state['id'], 'resolved', 'Synthetic decision', 0)
    return path, run, state['id']


def test_copy_restore_preserve_all_data_and_allow_continued_work(source, tmp_path, monkeypatch):
    path, run, finding = source
    record, events = storage.get_run(run), storage.finding_history(run, finding)
    before = path.read_bytes()
    # Unknown additive tables must also survive without application knowledge.
    with closing(sqlite3.connect(path)) as con, con:
        con.execute('CREATE TABLE synthetic_extra(value TEXT)')
        con.execute("INSERT INTO synthetic_extra VALUES('synthetic')")
    before = path.read_bytes()
    saved = backup.copy_history(path, tmp_path / 'backup.sqlite3')
    restored = backup.copy_history(saved, tmp_path / 'restored.sqlite3')
    monkeypatch.setattr(storage, 'DB', restored)
    assert storage.get_run(run) == record
    assert storage.finding_history(run, finding) == events
    assert storage.update_finding(run, finding, 'in_progress', 'Reopened', 1)['version'] == 2
    with closing(sqlite3.connect(restored)) as con:
        assert con.execute('SELECT value FROM synthetic_extra').fetchall() == [('synthetic',)]
    assert path.read_bytes() == before


def test_never_overwrite_or_copy_onto_source(source, tmp_path):
    path, _, _ = source
    before = path.read_bytes()
    with pytest.raises(FileExistsError):
        backup.copy_history(path, path)
    target = tmp_path / 'existing'
    target.write_bytes(b'preserve')
    with pytest.raises(FileExistsError):
        backup.copy_history(path, target)
    assert target.read_bytes() == b'preserve'
    assert path.read_bytes() == before


@pytest.mark.parametrize('kind', ['missing', 'corrupt', 'foreign', 'future'])
def test_reject_invalid_source_without_creating_destination(source, tmp_path, kind):
    path, _, _ = source
    target = tmp_path / 'target'
    if kind == 'missing':
        path = tmp_path / 'missing'
    elif kind == 'corrupt':
        path = tmp_path / 'corrupt'
        path.write_bytes(b'not SQLite')
    elif kind == 'foreign':
        path = tmp_path / 'foreign'
        with closing(sqlite3.connect(path)) as con:
            con.execute('CREATE TABLE unrelated(value)')
    else:
        with closing(sqlite3.connect(path)) as con, con:
            con.execute('UPDATE issue_schema SET version=999')
    with pytest.raises((ValueError, sqlite3.DatabaseError, FileNotFoundError)):
        backup.copy_history(path, target)
    assert not target.exists()


def test_failed_destination_validation_removes_only_new_copy(source, tmp_path, monkeypatch):
    path, _, _ = source
    original = backup._verify
    calls = []
    def verify(con):
        calls.append(1)
        original(con)
        if len(calls) == 2:
            raise ValueError('Synthetic destination validation failure')
    monkeypatch.setattr(backup, '_verify', verify)
    before = path.read_bytes()
    target = tmp_path / 'failed'
    with pytest.raises(ValueError):
        backup.copy_history(path, target)
    assert not target.exists()
    assert path.read_bytes() == before


def test_broken_foreign_keys_are_rejected(source, tmp_path):
    path, _, finding = source
    with closing(sqlite3.connect(path)) as con, con:
        con.execute('UPDATE finding_states SET run_id=999 WHERE id=?', (finding,))
    with pytest.raises(ValueError, match='foreign key'):
        backup.copy_history(path, tmp_path / 'bad-copy')
    assert not (tmp_path / 'bad-copy').exists()
