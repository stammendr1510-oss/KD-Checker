from concurrent.futures import ThreadPoolExecutor
import sqlite3

import pytest
from scoped_history import Repository, Scope, Denied, Missing, Conflict

A, B, C = Scope('org-a', 'project-a'), Scope('org-a', 'project-b'), Scope('org-b', 'project-a')
ISSUE = dict(severity='info', code='DEMO-1', file='synthetic.pdf', location='PDF 1',
             message='Synthetic', recommendation='Review')


@pytest.fixture
def repo(tmp_path):
    value = Repository.create(tmp_path / 'scoped.sqlite3')
    for scope in (A, B, C):
        value.provision('engineer', scope, 'engineer')
        value.provision('viewer', scope, 'viewer')
    return value


def save(repo, scope=A):
    return repo.save('engineer', scope, ['synthetic.pdf'], [ISSUE], revision='synthetic-rev-1')


def test_scoped_lists_reads_decisions_and_both_compare_inputs(repo):
    first, second, other, foreign = save(repo), save(repo), save(repo, B), save(repo, C)
    assert repo.list('viewer', A) == [second, first]
    for identifier in (other, foreign, 999):
        with pytest.raises(Missing):
            repo.get('viewer', A, identifier)
        with pytest.raises(Missing):
            repo.update('engineer', A, identifier, 0, 'resolved', 'Synthetic', 0)
        with pytest.raises(Missing):
            repo.history('viewer', A, identifier, 0)
        with pytest.raises(Missing):
            repo.compare('viewer', A, first, identifier)
    assert repo.compare('viewer', A, first, second)['summary']['persisting'] == 1
    assert repo.get('viewer', A, second)['previous_id'] == first


def test_current_membership_roles_and_revocation(repo):
    identifier = save(repo)
    with pytest.raises(Denied):
        repo.save('viewer', A, [], [], revision='rev')
    with pytest.raises(Denied):
        repo.update('viewer', A, identifier, 0, 'resolved', 'Synthetic', 0)
    repo.revoke('engineer', A)
    for operation in (lambda: repo.get('engineer', A, identifier),
                      lambda: repo.list('engineer', A), lambda: save(repo)):
        with pytest.raises(Denied):
            operation()
    assert repo.get('viewer', A, identifier)['issues'] == [ISSUE]


def test_atomic_optimistic_decisions_and_server_actor(repo):
    identifier = save(repo)
    original = repo.get('viewer', A, identifier)
    def update(comment):
        try:
            repo.update('engineer', A, identifier, 0, 'in_progress', comment, 0)
            return 'saved'
        except Conflict:
            return 'conflict'
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(update, ['first', 'second'])) == ['conflict', 'saved']
    events = repo.history('viewer', A, identifier, 0)
    assert len(events) == 1
    assert (events[0]['actor'], events[0]['old_version'], events[0]['new_version']) == ('engineer', 0, 1)
    current = repo.get('viewer', A, identifier)
    assert current['issues'] == original['issues']
    assert current['decisions'][0]['comment'] == events[0]['new_comment']
    assert current['decisions'][0]['updated_at'] == events[0]['changed_at']
    # Repeated identical decision is idempotent only at the current version.
    repo.update('engineer', A, identifier, 0, 'in_progress', events[0]['new_comment'], 1)
    assert len(repo.history('viewer', A, identifier, 0)) == 1


def test_failure_after_audit_insert_rolls_back_both(repo):
    identifier = save(repo)
    with repo.transaction(True) as con:
        con.execute("CREATE TRIGGER fail_update BEFORE UPDATE ON decisions BEGIN SELECT RAISE(ABORT,'synthetic'); END")
    with pytest.raises(sqlite3.IntegrityError):
        repo.update('engineer', A, identifier, 0, 'resolved', 'Synthetic', 0)
    assert repo.history('viewer', A, identifier, 0) == []
    assert repo.get('viewer', A, identifier)['decisions'][0]['version'] == 0


def test_recheck_preserves_history_and_starts_new_decisions(repo):
    first = save(repo)
    repo.update('engineer', A, first, 0, 'resolved', 'Synthetic resolution', 0)
    second = save(repo)
    decision = repo.get('viewer', A, second)['decisions'][0]
    assert (decision['status'], decision['comment'], decision['version']) == ('new', '', 0)
    assert repo.get('viewer', A, first)['decisions'][0]['status'] == 'resolved'
    with repo.transaction() as con:
        assert con.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert not con.execute('PRAGMA foreign_key_check').fetchall()


def test_database_rejects_cross_project_links(repo):
    first, other = save(repo), save(repo, B)
    with pytest.raises(sqlite3.IntegrityError), repo.transaction(True) as con:
        con.execute('UPDATE runs SET previous_id=? WHERE id=?', (other, first))


def test_create_never_replaces_existing_file(repo):
    before = repo.path.read_bytes()
    with pytest.raises(FileExistsError):
        Repository.create(repo.path)
    assert repo.path.read_bytes() == before


@pytest.mark.parametrize('changes', [{'revision': ''}, {'files': 'not a list'}, {'issues': [{}]},
                                    {'issues': [dict(ISSUE, severity='unknown')]}])
def test_invalid_input_leaves_no_partial_run(repo, changes):
    data = dict(files=['synthetic.pdf'], issues=[ISSUE], revision='synthetic-rev') | changes
    with pytest.raises(ValueError):
        repo.save('engineer', A, **data)
    assert repo.list('viewer', A) == []
