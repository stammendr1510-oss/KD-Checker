"""Restore a synthetic pre-rollback backup into a separate local database.

This verifies existing backup contents, not a production recovery procedure,
live backup scheduling, disaster recovery, or a deployment on several PCs.
"""
from contextlib import closing
import sqlite3

import storage


def test_restore_preserves_results_decisions_audit_and_allows_new_work(tmp_path, monkeypatch):
    source = tmp_path / 'source.sqlite3'
    restored = tmp_path / 'restored.sqlite3'
    monkeypatch.setattr(storage, 'DB', source)
    issue = dict(severity='info', code='SYNTHETIC-1', file='synthetic.pdf',
                 location='PDF', message='Synthetic only', recommendation='Review')
    first = storage.save_run(['synthetic.pdf'], [issue])
    first_state = storage.get_run(first)['finding_states'][0]
    storage.update_finding(first, first_state['id'], 'in_progress', 'First review', 0)
    storage.update_finding(first, first_state['id'], 'resolved', 'Synthetic resolution', 1)
    second = storage.save_run(['synthetic.pdf'], [issue])
    second_state = storage.get_run(second)['finding_states'][0]
    storage.update_finding(second, second_state['id'], 'rejected', 'Synthetic reason', 0)
    records = [storage.get_run(identifier) for identifier in (first, second)]
    histories = [storage.finding_history(record['id'], record['finding_states'][0]['id'])
                 for record in records]
    listing = storage.list_runs()

    # Exercise the existing backup-before-rollback path on the temporary source.
    backup = storage.rollback_issue_management()
    assert backup and backup.exists()
    backup_bytes = backup.read_bytes()
    source_bytes = source.read_bytes()
    # Restore using SQLite's backup API, with closed connections before access
    # through storage. Never replace a database while the application is running.
    with closing(sqlite3.connect(backup)) as saved, closing(sqlite3.connect(restored)) as target:
        saved.backup(target)
    monkeypatch.setattr(storage, 'DB', restored)
    assert storage.list_runs() == listing
    for expected, history in zip(records, histories):
        assert storage.get_run(expected['id']) == expected
        assert storage.finding_history(expected['id'], expected['finding_states'][0]['id']) == history

    updated = storage.update_finding(second, second_state['id'], 'in_progress', 'Reopened after restore', 1)
    assert updated['version'] == 2
    events = storage.finding_history(second, second_state['id'])
    assert events[:-1] == histories[1]
    assert (events[-1]['old_version'], events[-1]['new_version']) == (1, 2)
    assert events[-1]['id'] > histories[1][-1]['id']
    third = storage.save_run(['synthetic.pdf'], [issue])
    assert third > second
    third_record = storage.get_run(third)
    state = third_record['finding_states'][0]
    assert state['id'] > second_state['id']
    assert state['previous']['run_id'] == second
    assert (state['status'], state['version']) == ('new', 0)
    assert third_record['issues'] == [issue]
    assert storage.get_run(first) == records[0]
    with closing(storage.connect()) as con:
        assert con.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert not con.execute('PRAGMA foreign_key_check').fetchall()
    assert backup.read_bytes() == backup_bytes
    assert source.read_bytes() == source_bytes
