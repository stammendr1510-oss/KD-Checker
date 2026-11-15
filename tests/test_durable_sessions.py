from concurrent.futures import ThreadPoolExecutor
import multiprocessing
import sqlite3
from contextlib import closing

from durable_sessions import DurableSessions
from secure_sessions import Identity


def revoke_in_process(path, token):
    DurableSessions(path).revoke(token)


def test_restart_process_revoke_and_no_token_storage(tmp_path):
    path = tmp_path/'sessions.sqlite3'
    first = DurableSessions.create(path)
    token, csrf = first.issue(Identity('synthetic','org','project'))
    second = DurableSessions(path)
    assert second.resolve(token).identity.subject == 'synthetic'
    with closing(sqlite3.connect(path)) as con:
        data = repr(con.execute('SELECT * FROM sessions').fetchall())
        assert token not in data and csrf not in data
    process = multiprocessing.get_context('spawn').Process(target=revoke_in_process,args=(str(path),token))
    process.start()
    process.join(timeout=20)
    try:
        assert process.exitcode == 0
        assert first.resolve(token) is None
        second.touch(token)
        assert first.resolve(token) is None
    finally:
        if process.is_alive():
            process.terminate()
            process.join()


def test_idle_absolute_expiry_and_concurrent_touch_revoke(tmp_path):
    now = [100.0]
    store = DurableSessions.create(tmp_path/'sessions.sqlite3',clock=lambda:now[0],ttl=100,idle=30)
    token,_ = store.issue(Identity('synthetic','org','project'))
    for _ in range(4):
        now[0] += 20
        store.touch(token)
        assert store.resolve(token)
    now[0] += 20
    assert store.resolve(token) is None
    token,_ = store.issue(Identity('synthetic','org','project'))
    now[0] += 30
    assert store.resolve(token) is None
    token,_ = store.issue(Identity('synthetic','org','project'))
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda action: action(token),[store.touch,store.revoke]))
    assert store.resolve(token) is None


def test_real_http_restart_and_shared_logout(tmp_path):
    from fastapi.testclient import TestClient
    from secure_api import create_secure_app, COOKIE
    from secure_backend import ScopedBackend
    from scoped_history import Repository, Scope
    repo = Repository.create(tmp_path/'repo.sqlite3')
    repo.provision('synthetic',Scope('org','project'),'engineer')
    class Verifier:
        def verify(self, credential):
            return Identity('synthetic','org','project') if credential == 'synthetic-ticket' else None
    origin = 'https://kd.test'
    session_path = tmp_path/'sessions.sqlite3'
    first = DurableSessions.create(session_path)
    def app(store):
        return create_secure_app(verifier=Verifier(),backend=ScopedBackend(repo),sessions=store,origin=origin)
    with TestClient(app(first),base_url=origin) as a:
        response = a.post('/auth/login',json={'credential':'synthetic-ticket'},headers={'origin':origin})
        token, csrf = a.cookies.get(COOKIE), response.json()['csrf_token']
        with TestClient(app(DurableSessions(session_path)),base_url=origin) as b:
            b.cookies.set(COOKIE,token)
            assert b.get('/api/runs').status_code == 200
            assert b.post('/auth/logout',headers={'origin':origin,'x-csrf-token':csrf}).status_code == 200
        assert a.get('/api/runs').status_code == 401
