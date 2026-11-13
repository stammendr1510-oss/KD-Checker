"""Synthetic migration, decision history, matching, API and workbook regressions."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from copy import deepcopy
from io import BytesIO
import json
import sqlite3

from fastapi.testclient import TestClient
from openpyxl import load_workbook
import pytest

import app as web
from issue_tracking import finding_identity
import storage


def finding(**changes):
    return dict({'severity': 'warning', 'code': 'DEMO-100', 'file': 'demo.pdf',
                 'location': 'Лист 2', 'message': 'Искусственное замечание',
                 'recommendation': 'Проверить вручную'}, **changes)


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, 'DB', tmp_path / 'history.sqlite3')
    return storage.DB


def first_state(run_id):
    return storage.get_run(run_id)['finding_states'][0]


def raw_runs(path):
    with closing(sqlite3.connect(path)) as con:
        return con.execute('SELECT * FROM runs ORDER BY id').fetchall()


def make_legacy(db):
    with closing(sqlite3.connect(db)) as con, con:
        con.execute('''CREATE TABLE runs(id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL,
                       filenames TEXT NOT NULL, issues TEXT NOT NULL)''')
        for index in (4, 9):
            con.execute('INSERT INTO runs VALUES(?,?,?,?)',
                        (index, '2025-01-01T00:00:00+00:00', json.dumps(['demo.pdf']),
                         json.dumps([finding(extra='legacy field')], ensure_ascii=False)))


def test_migration_preserves_legacy_bytes_and_creates_verified_backup(db):
    make_legacy(db)
    before = raw_runs(db)
    record = storage.get_run(9)
    assert record['issues'] == [finding(extra='legacy field')]
    assert first_state(9)['status'] == 'new'
    assert first_state(9)['updated_at'] is None
    assert first_state(9)['previous']['run_id'] == 4
    assert storage.finding_history(9, first_state(9)['id']) == []
    assert raw_runs(db) == before
    backups = list(db.parent.glob('*.before-issue-management-*.bak'))
    assert len(backups) == 1
    assert raw_runs(backups[0]) == before
    with closing(storage.connect()) as con:
        assert con.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert not con.execute('PRAGMA foreign_key_check').fetchall()
    storage.list_runs()
    assert len(list(db.parent.glob('*.before-issue-management-*.bak'))) == 1
    assert storage.save_run(['demo.pdf'], []) == 10


def test_failed_migration_rolls_back_all_schema_changes(db, monkeypatch):
    make_legacy(db)
    before = raw_runs(db)
    def fail(*args):
        raise RuntimeError('Synthetic migration failure')
    monkeypatch.setattr(storage, '_add_states', fail)
    with pytest.raises(RuntimeError):
        storage.get_run(9)
    assert raw_runs(db) == before
    with closing(sqlite3.connect(db)) as con:
        tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert tables == {'runs', 'sqlite_sequence'}
    assert raw_runs(next(db.parent.glob('*.before-issue-management-*.bak'))) == before


def test_backup_failure_prevents_migration(db, monkeypatch):
    make_legacy(db)
    before = raw_runs(db)
    def fail(*args):
        raise OSError('Synthetic full disk')
    monkeypatch.setattr(storage, '_backup', fail)
    with pytest.raises(OSError):
        storage.get_run(4)
    assert raw_runs(db) == before
    with closing(sqlite3.connect(db)) as con:
        assert not con.execute("SELECT 1 FROM sqlite_master WHERE name='finding_states'").fetchone()


def test_decision_history_and_original_results_are_independent(db):
    issues = [finding()]
    source = deepcopy(issues)
    run_id = storage.save_run(['demo.pdf'], issues)
    original = raw_runs(db)
    state = first_state(run_id)
    assert (state['status'], state['comment'], state['updated_at'], state['version']) == ('new', '', None, 0)
    for version, (status, comment) in enumerate([
        ('in_progress', 'Начата проверка'), ('in_progress', 'Дополнен комментарий'),
        ('resolved', 'Проверено инженером'), ('rejected', 'Обоснование отклонения'),
    ]):
        updated = storage.update_finding(run_id, state['id'], status, comment, version)
        assert updated['version'] == version+1
        assert updated['updated_at']
    events = storage.finding_history(run_id, state['id'])
    assert len(events) == 4
    assert [(e['old_version'], e['new_version']) for e in events] == [(0,1), (1,2), (2,3), (3,4)]
    assert events[1]['old_status'] == events[1]['new_status'] == 'in_progress'
    assert events[1]['old_comment'] == 'Начата проверка'
    assert events[1]['new_comment'] == 'Дополнен комментарий'
    assert events[-1]['changed_at'] == updated['updated_at']
    assert first_state(run_id)['comment'] == 'Обоснование отклонения'
    assert storage.get_run(run_id)['issues'] == source == issues
    assert raw_runs(db) == original
    assert storage.update_finding(run_id, state['id'], 'rejected', updated['comment'], 4)['version'] == 4
    assert len(storage.finding_history(run_id, state['id'])) == 4


def test_concurrent_updates_cannot_overwrite_each_other(db):
    run_id = storage.save_run(['demo.pdf'], [finding()])
    state = first_state(run_id)
    def update(comment):
        try:
            storage.update_finding(run_id, state['id'], 'in_progress', comment, 0)
            return 'saved'
        except storage.FindingConflict:
            return 'conflict'
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(update, ['Первый', 'Второй'])) == ['conflict', 'saved']
    assert len(storage.finding_history(run_id, state['id'])) == 1
    assert first_state(run_id)['version'] == 1


def test_update_and_audit_are_atomic(db):
    run_id = storage.save_run(['demo.pdf'], [finding()])
    state = first_state(run_id)
    with closing(storage.connect()) as con, con:
        con.execute("CREATE TRIGGER fail_update BEFORE UPDATE ON finding_states BEGIN SELECT RAISE(ABORT,'synthetic'); END")
    with pytest.raises(sqlite3.IntegrityError):
        storage.update_finding(run_id, state['id'], 'resolved', 'Комментарий', 0)
    assert first_state(run_id)['version'] == 0
    assert storage.finding_history(run_id, state['id']) == []


def test_failed_save_does_not_leave_a_partly_managed_run(db, monkeypatch):
    first = storage.save_run(['demo.pdf'], [finding()])
    before = raw_runs(db)
    original = storage._add_states
    def fail(con, *args):
        original(con, *args)
        raise RuntimeError('Synthetic save failure after creating states')
    monkeypatch.setattr(storage, '_add_states', fail)
    with pytest.raises(RuntimeError):
        storage.save_run(['demo.pdf'], [finding()])
    assert raw_runs(db) == before
    assert len(storage.get_run(first)['finding_states']) == 1
    with closing(storage.connect()) as con:
        assert con.execute('SELECT count(*) FROM issue_runs').fetchone()[0] == 1
        assert con.execute('SELECT count(*) FROM finding_states').fetchone()[0] == 1


def test_unknown_future_schema_is_not_changed(db):
    storage.save_run(['demo.pdf'], [finding()])
    with closing(sqlite3.connect(db)) as con, con:
        con.execute('UPDATE issue_schema SET version=2')
    before = raw_runs(db)
    with pytest.raises(RuntimeError):
        storage.get_run(1)
    with pytest.raises(RuntimeError):
        storage.rollback_issue_management()
    assert raw_runs(db) == before
    with closing(sqlite3.connect(db)) as con:
        assert con.execute('SELECT version FROM issue_schema').fetchone()[0] == 2


@pytest.mark.parametrize('status', ['new', 'in_progress', 'resolved', 'rejected'])
def test_recheck_matches_identity_but_never_copies_a_decision(db, status):
    a, b = finding(), finding(code='DEMO-200', message='Другое замечание')
    first = storage.save_run(['demo.pdf', 'other.csv'], [a, b])
    prior = first_state(first)
    storage.update_finding(first, prior['id'], status, 'Ранее принятое решение', 0)
    second = storage.save_run(['OTHER.CSV', 'DEMO.PDF'], [b, a])
    current = storage.get_run(second)['finding_states'][1]
    assert current['fingerprint'] == prior['fingerprint']
    assert current['id'] != prior['id']
    assert current['previous']['id'] == prior['id']
    assert current['previous']['status'] == status
    assert current['match_kind'] == 'exact'
    assert (current['status'], current['comment'], current['version']) == ('new', '', 0)
    storage.update_finding(second, current['id'], 'resolved', 'Подтверждение в текущей проверке', 0)
    assert first_state(first)['status'] == status


@pytest.mark.parametrize('changes', [
    {'code': 'DEMO-101'}, {'location': 'Лист 3'}, {'message': 'Изменённый результат'},
    {'severity': 'error'}, {'recommendation': 'Новая рекомендация'}, {'file': 'other.pdf'},
])
def test_changed_engineering_result_is_not_an_exact_match(db, changes):
    storage.save_run(['demo.pdf'], [finding()])
    run_id = storage.save_run(['demo.pdf'], [finding(**changes)])
    assert first_state(run_id)['previous'] is None


def test_scope_duplicates_and_latest_check_are_conservative(db):
    a = finding()
    storage.save_run(['demo.pdf'], [a])
    other = storage.save_run(['demo.pdf', 'extra.pdf'], [a])
    assert first_state(other)['previous'] is None
    duplicate = storage.save_run(['demo.pdf'], [a, a])
    assert all(s['match_kind'] == 'ambiguous' and s['previous'] is None for s in storage.get_run(duplicate)['finding_states'])
    single = storage.save_run(['demo.pdf'], [a])
    assert first_state(single)['match_kind'] == 'ambiguous'
    empty = storage.save_run(['demo.pdf'], [])
    recurrence = storage.save_run(['demo.pdf'], [a])
    assert first_state(recurrence)['previous'] is None
    assert storage.get_run(empty)['finding_states'] == []


def test_identity_normalizes_only_filename_and_text_whitespace():
    a = finding()
    b = finding(file='C:\\samples\\DEMO.PDF', message='Искусственное\n  замечание')
    assert finding_identity(a) == finding_identity(b)
    assert finding_identity(a) != finding_identity(finding(message='Искусственное замечание 2'))


def test_rollback_preserves_all_checks_and_backs_up_decisions(db):
    make_legacy(db)
    state = first_state(9)
    storage.update_finding(9, state['id'], 'resolved', 'Решение после миграции', 0)
    storage.save_run(['new.csv'], [finding(file='new.csv')])
    before = raw_runs(db)
    backup = storage.rollback_issue_management()
    assert backup.exists()
    assert raw_runs(db) == raw_runs(backup) == before
    with closing(sqlite3.connect(db)) as con:
        assert not con.execute("SELECT 1 FROM sqlite_master WHERE name='finding_states'").fetchone()
    with closing(sqlite3.connect(backup)) as con:
        assert con.execute('SELECT comment FROM finding_states WHERE id=?', (state['id'],)).fetchone()[0] == 'Решение после миграции'
        assert con.execute('SELECT count(*) FROM finding_events').fetchone()[0] == 1
    assert storage.rollback_issue_management() is None


def test_api_validation_scope_conflicts_and_excel(db):
    run_id = storage.save_run(['demo.pdf'], [finding()])
    other = storage.save_run(['other.pdf'], [finding(file='other.pdf')])
    state = first_state(run_id)
    url = f'/api/runs/{run_id}/findings/{state["id"]}'
    with TestClient(web.app) as client:
        csv_before = client.get(f'/api/runs/{run_id}/csv').content
        assert client.get(url+'/history').json() == []
        for body in [
            {'status': 'bad', 'comment': '', 'expected_version': 0},
            {'status': 'rejected', 'comment': ' \n\t', 'expected_version': 0},
            {'status': 'new', 'comment': 'x'*4001, 'expected_version': 0},
            {'status': 'new', 'comment': ''},
            {'status': 'new', 'comment': '', 'expected_version': 0, 'severity': 'info'},
        ]:
            assert client.patch(url, json=body).status_code == 422
        body = {'status': 'rejected', 'comment': '=HYPERLINK("https://example.invalid","test")', 'expected_version': 0}
        assert client.patch(f'/api/runs/{other}/findings/{state["id"]}', json=body).status_code == 404
        assert client.get(f'/api/runs/{other}/findings/{state["id"]}/history').status_code == 404
        response = client.patch(url, json=body)
        assert response.status_code == 200
        assert response.json()['status'] == 'rejected'
        assert client.patch(url, json=body).status_code == 409
        history = client.get(url+'/history').json()
        assert len(history) == 1 and history[0]['new_comment'] == body['comment']
        assert client.get(f'/api/runs/{run_id}/csv').content == csv_before
        record = client.get(f'/api/runs/{run_id}').json()
        assert record['issues'] == [finding()]
        with closing(load_workbook(BytesIO(client.get(f'/api/runs/{run_id}/xlsx').content))) as book:
            sheet = book['Замечания']
            assert [c.value for c in sheet[1]][-3:] == ['Статус', 'Комментарий инженера', 'Дата изменения']
            assert sheet['H2'].value == 'Отклонено с обоснованием'
            assert sheet['I2'].value == body['comment'] and sheet['I2'].data_type == 's'
            assert sheet['J2'].value == response.json()['updated_at']
            assert sheet.auto_filter.ref == 'A1:J2'
            assert book['Общие сведения']['B6'].value == 1
