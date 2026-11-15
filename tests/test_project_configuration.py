from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import pytest
from fastapi.testclient import TestClient
from secure_api import create_secure_app
from secure_backend import ScopedBackend
from secure_sessions import Identity, Sessions
from scoped_history import Repository,Scope,Conflict
from project_configuration import DEFAULT

ORIGIN='https://kd.test'

@pytest.fixture
def setup(tmp_path):
    repo=Repository.create(tmp_path/'repo.sqlite3')
    for name,role,project in [('admin','admin','a'),('viewer','viewer','a'),('other','admin','b')]:
        repo.provision(name,Scope('org',project),role)
    class Verifier:
        def verify(self,ticket):
            return Identity(ticket,'org','b' if ticket=='other' else 'a') if ticket in ('admin','viewer','other') else None
    backend=ScopedBackend(repo)
    with TestClient(create_secure_app(verifier=Verifier(),backend=backend,sessions=Sessions(),origin=ORIGIN),base_url=ORIGIN) as client:
        yield client,repo,backend

def login(client,name='admin'):
    r=client.post('/auth/login',json={'credential':name},headers={'origin':ORIGIN})
    return {'origin':ORIGIN,'x-csrf-token':r.json()['csrf_token']}

def changed():
    return dict(DEFAULT,min_pdf_text_chars=100)

def test_versioned_admin_update_audit_and_project_isolation(setup):
    client,repo,backend=setup
    baseline=Path('config.json').read_bytes()
    headers=login(client)
    assert client.get('/api/config').headers['x-config-version']=='0'
    data={'settings':changed(),'expected_version':0}
    r=client.put('/api/config',json=data,headers=headers)
    assert r.status_code==200 and r.json()['version']==1
    assert client.get('/api/config').json()==changed()
    assert client.put('/api/config',json=data,headers=headers).status_code==409
    history=client.get('/api/config/history').json()
    assert len(history)==1 and history[0]['actor']=='admin' and history[0]['old_version']==0
    # Engine consumes the scoped setting: original baseline requires only 25 chars.
    import fitz
    with fitz.open() as pdf:
        pdf.new_page().insert_text((72,72),'Synthetic text longer than twenty five chars')
        content=pdf.tobytes()
    run=client.post('/api/check',files={'files':('synthetic.pdf',content,'application/pdf')},data={'revision':'rev'},headers=headers)
    assert run.status_code==200
    assert 'KD-002' in [i['code'] for i in run.json()['issues']]
    other=login(client,'other')
    assert client.get('/api/config').json()==DEFAULT
    assert client.get('/api/config/history').json()==[]
    run=client.post('/api/check',files={'files':('synthetic.pdf',content,'application/pdf')},data={'revision':'rev'},headers=other)
    assert 'KD-002' not in [i['code'] for i in run.json()['issues']]
    headers=login(client,'viewer')
    assert client.get('/api/config').json()==changed()
    assert client.put('/api/config',json=data,headers=headers).status_code==403
    assert Path('config.json').read_bytes()==baseline

@pytest.mark.parametrize('damage',['bool','extra','headers','regex','negative'])
def test_strict_schema_refuses_invalid_without_audit(setup,damage):
    client,repo,_=setup
    headers=login(client)
    data={'settings':changed(),'expected_version':0}
    if damage=='bool': data['settings']['require_pdf_text']='false'
    elif damage=='extra': data['settings']['secret']='synthetic'
    elif damage=='headers': data['settings']['specification_headers']=['x',' X ']
    elif damage=='regex': data['settings']['designation_pattern']='(a+)+$'
    else: data['expected_version']=-1
    assert client.put('/api/config',json=data,headers=headers).status_code==422
    assert client.get('/api/config/history').json()==[]

def test_transaction_conflict_and_audit_failure_rollback(setup):
    _,repo,backend=setup
    identity=Identity('admin','org','a')
    data={'settings':changed(),'expected_version':0}
    def update(_):
        try:
            backend.project_configuration.update(identity,data)
            return 'saved'
        except Conflict:
            return 'conflict'
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(update,range(2)))==['conflict','saved']
    assert len(backend.project_configuration.history(identity))==1
    with repo.transaction(True) as con:
        con.execute("CREATE TRIGGER fail_config BEFORE UPDATE ON project_config BEGIN SELECT RAISE(ABORT,'synthetic'); END")
    with pytest.raises(sqlite3.IntegrityError):
        backend.project_configuration.update(identity,{'settings':dict(changed(),min_pdf_text_chars=200),'expected_version':1})
    assert backend.project_configuration.read(identity)[1]==1
    assert len(backend.project_configuration.history(identity))==1

def test_revocation_between_gate_and_config_write(setup,monkeypatch):
    client,repo,backend=setup
    headers=login(client)
    original=backend.project_configuration.update
    def revoked(identity,data):
        repo.revoke(identity.subject,Scope(identity.organization,identity.project))
        return original(identity,data)
    monkeypatch.setattr(backend.project_configuration,'update',revoked)
    assert client.put('/api/config',json={'settings':changed(),'expected_version':0},headers=headers).status_code==403
    with repo.transaction() as con:
        assert con.execute('SELECT count(*) FROM project_config_events').fetchone()[0]==0
