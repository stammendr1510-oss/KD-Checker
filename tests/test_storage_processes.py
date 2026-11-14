"""Separate SQLite clients on synthetic data; no network or live databases."""
from contextlib import closing
import multiprocessing
from pathlib import Path

import pytest
import storage


ISSUE = dict(severity='info', code='DEMO-1', file='synthetic.pdf',
             location='PDF', message='Synthetic finding', recommendation='Review')


def worker(path, barrier, results, operation, run_id, finding_id, label):
    storage.DB = Path(path)
    try:
        barrier.wait(timeout=20)
        if operation == 'update':
            result = storage.update_finding(run_id, finding_id, 'in_progress', label, 0)
            results.put(('saved', result['comment']))
        else:
            results.put(('saved', storage.save_run(['synthetic.pdf'], [ISSUE])))
    except storage.FindingConflict:
        results.put(('conflict', label))
    except Exception as exc:
        results.put(('error', repr(exc)))


def race(path, operation, run_id, finding_id):
    # spawn also on Linux: no inherited SQLite connections or migration lock.
    ctx = multiprocessing.get_context('spawn')
    barrier, results = ctx.Barrier(2), ctx.Queue()
    processes = [ctx.Process(target=worker, args=(
        str(path), barrier, results, operation, run_id, finding_id, label,
    )) for label in ('first engineer', 'second engineer')]
    try:
        for process in processes:
            process.start()
        outcomes = [results.get(timeout=40) for _ in processes]
        for process in processes:
            process.join(timeout=10)
            assert process.exitcode == 0
        return outcomes
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(timeout=10)
        results.close()
        results.join_thread()


@pytest.fixture
def database(tmp_path, monkeypatch):
    path = tmp_path / 'history.sqlite3'
    monkeypatch.setattr(storage, 'DB', path)
    # Migration is deliberately complete before concurrent clients start.
    # This test does not claim to verify simultaneous migration or restore.
    run_id = storage.save_run(['synthetic.pdf'], [ISSUE])
    return path, run_id, storage.get_run(run_id)['finding_states'][0]['id']


def test_separate_processes_preserve_conflict_and_single_audit_event(database):
    path, run_id, finding_id = database
    outcomes = race(path, 'update', run_id, finding_id)
    assert sorted(status for status, _ in outcomes) == ['conflict', 'saved']
    winner = next(value for status, value in outcomes if status == 'saved')
    record = storage.get_run(run_id)
    state = record['finding_states'][0]
    assert (state['version'], state['comment'], state['status']) == (1, winner, 'in_progress')
    assert record['issues'] == [ISSUE]
    events = storage.finding_history(run_id, finding_id)
    assert len(events) == 1
    assert (events[0]['old_version'], events[0]['new_version']) == (0, 1)
    assert events[0]['new_comment'] == winner


def test_separate_processes_save_complete_serialized_history(database):
    path, original_id, _ = database
    outcomes = race(path, 'save', original_id, None)
    assert all(status == 'saved' for status, _ in outcomes), outcomes
    identifiers = sorted(value for _, value in outcomes)
    assert original_id < identifiers[0] < identifiers[1]
    for current_id, previous_id in zip(identifiers, [original_id, identifiers[0]]):
        record = storage.get_run(current_id)
        assert record['files'] == ['synthetic.pdf']
        assert record['issues'] == [ISSUE]
        assert len(record['finding_states']) == 1
        state = record['finding_states'][0]
        assert state['previous']['run_id'] == previous_id
        assert (state['status'], state['version']) == ('new', 0)
        assert storage.finding_history(current_id, state['id']) == []
    with closing(storage.connect()) as con:
        assert con.execute('SELECT count(*) FROM runs').fetchone()[0] == 3
        assert con.execute('SELECT count(*) FROM issue_runs').fetchone()[0] == 3
        assert con.execute('SELECT count(*) FROM finding_states').fetchone()[0] == 3
        assert con.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert not con.execute('PRAGMA foreign_key_check').fetchall()
