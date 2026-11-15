from contextlib import closing
import json
import sqlite3

import pytest
import storage
from scoped_history import Scope
from staging_import import stage_history

SCOPE = Scope('synthetic-org', 'synthetic-project')
ISSUE = dict(severity='info', code='DEMO-1', file='synthetic.pdf', location='PDF 1',
             message='Synthetic', recommendation='Review')


@pytest.fixture
def source(tmp_path, monkeypatch):
    path = tmp_path / 'source.sqlite3'
    monkeypatch.setattr(storage, 'DB', path)
    first = storage.save_run(['synthetic.pdf'], [ISSUE])
    state = storage.get_run(first)['finding_states'][0]
    storage.update_finding(first, state['id'], 'in_progress', 'Synthetic first', 0)
    storage.update_finding(first, state['id'], 'resolved', 'Synthetic final', 1)
    second = storage.save_run(['synthetic.pdf'], [ISSUE])
    return path, first, second


def migrate(path, tmp_path, **kwargs):
    return stage_history(path, tmp_path / 'copy.sqlite3', tmp_path / 'stage.sqlite3',
                         scope=SCOPE, revision='operator-assigned-revision', operator='operator', **kwargs)


def test_stage_preserves_results_decisions_journal_and_source_rows(source, tmp_path):
    path, first, second = source
    original = path.read_bytes()
    expected = storage.get_run(first)
    target = migrate(path, tmp_path)
    assert path.read_bytes() == original
    record = target.get('operator', SCOPE, first)
    assert (record['created_at'], record['issues'], record['files']) == (expected['created_at'], expected['issues'], expected['files'])
    decision = record['decisions'][0]
    prior = expected['finding_states'][0]
    assert (decision['status'], decision['comment'], decision['version'], decision['updated_at']) == (
        prior['status'], prior['comment'], prior['version'], prior['updated_at'])
    events = target.history('operator', SCOPE, first, 0)
    assert len(events) == 2 and all(e['actor'] == 'legacy_unknown' for e in events)
    assert events[-1]['changed_at'] == decision['updated_at']
    assert target.get('operator', SCOPE, second)['previous_id'] == first
    assert target.update('operator', SCOPE, first, 0, 'in_progress', 'After staging', 2) == 3
    assert target.save('operator', SCOPE, ['synthetic.pdf'], [ISSUE], revision='new') > second
    with target.transaction() as con, closing(sqlite3.connect(path)) as legacy:
        archived = [json.loads(r[0]) for r in con.execute("SELECT payload FROM legacy_archive WHERE table_name='finding_states' ORDER BY ordinal")]
        legacy.row_factory = sqlite3.Row
        assert archived == [dict(r) for r in legacy.execute('SELECT * FROM finding_states ORDER BY rowid')]
        assert len(con.execute('SELECT * FROM import_manifest').fetchall()) == 1
    assert path.read_bytes() == original


@pytest.mark.parametrize('damage', ['audit', 'identity', 'state', 'scope', 'unknown_table'])
def test_invalid_source_refuses_staging_without_silent_loss(source, tmp_path, damage):
    path, _, _ = source
    with closing(sqlite3.connect(path)) as con, con:
        if damage == 'audit':
            con.execute('DELETE FROM finding_events WHERE id=1')
        elif damage == 'identity':
            con.execute("UPDATE finding_states SET fingerprint='invalid'")
        elif damage == 'state':
            con.execute("UPDATE finding_states SET comment='out of audit'")
        elif damage == 'scope':
            con.execute("UPDATE issue_runs SET scope='wrong'")
        else:
            con.execute('CREATE TABLE unexpected(value TEXT)')
    before = path.read_bytes()
    with pytest.raises(ValueError):
        migrate(path, tmp_path)
    assert not (tmp_path / 'stage.sqlite3').exists()
    assert path.read_bytes() == before
    assert (tmp_path / 'copy.sqlite3').exists()


def test_existing_destination_and_backup_are_never_replaced(source, tmp_path):
    path, _, _ = source
    target = tmp_path / 'stage.sqlite3'
    target.write_bytes(b'preserve')
    with pytest.raises(FileExistsError):
        migrate(path, tmp_path)
    assert target.read_bytes() == b'preserve'
    target.unlink()
    copied = tmp_path / 'copy.sqlite3'
    copied.write_bytes(b'preserve copy')
    with pytest.raises(FileExistsError):
        migrate(path, tmp_path)
    assert copied.read_bytes() == b'preserve copy'
    assert not target.exists()


def test_sequence_high_water_marks_survive_staging(source, tmp_path):
    path, first, _ = source
    with closing(sqlite3.connect(path)) as con, con:
        con.execute("UPDATE sqlite_sequence SET seq=1000 WHERE name='runs'")
        con.execute("UPDATE sqlite_sequence SET seq=2000 WHERE name='finding_events'")
    target = migrate(path, tmp_path)
    assert target.save('operator', SCOPE, ['synthetic.pdf'], [ISSUE], revision='new') > 1000
    target.update('operator', SCOPE, first, 0, 'in_progress', 'Continued', 2)
    assert target.history('operator', SCOPE, first, 0)[-1]['id'] > 2000


def test_empty_history_retains_reserved_identifiers(source, tmp_path):
    path, _, _ = source
    with closing(sqlite3.connect(path)) as con, con:
        for table in ('finding_events', 'finding_states', 'issue_runs', 'runs'):
            con.execute(f'DELETE FROM {table}')
        con.execute("UPDATE sqlite_sequence SET seq=1000 WHERE name='runs'")
        con.execute("UPDATE sqlite_sequence SET seq=2000 WHERE name='finding_events'")
    target = migrate(path, tmp_path)
    identifier = target.save('operator', SCOPE, ['synthetic.pdf'], [ISSUE], revision='new')
    assert identifier > 1000
    target.update('operator', SCOPE, identifier, 0, 'resolved', 'Synthetic', 0)
    assert target.history('operator', SCOPE, identifier, 0)[0]['id'] > 2000


def test_mid_import_failure_cleans_only_new_staging(source, tmp_path, monkeypatch):
    from scoped_history import Repository
    path, _, _ = source
    before = path.read_bytes()
    def fail(*args):
        raise RuntimeError('Synthetic provisioning failure')
    monkeypatch.setattr(Repository, 'provision', fail)
    with pytest.raises(RuntimeError):
        migrate(path, tmp_path)
    assert not (tmp_path / 'stage.sqlite3').exists()
    assert (tmp_path / 'copy.sqlite3').exists()
    assert path.read_bytes() == before
