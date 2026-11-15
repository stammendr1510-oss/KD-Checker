from copy import deepcopy
import pytest
from fastapi.testclient import TestClient
from secure_api import create_secure_app
from secure_sessions import Sessions, Identity
from secure_backend import ScopedBackend
from scoped_history import Repository, Scope

ORIGIN = 'https://kd.test'
ROW = dict(wire='W1', cable='', source=dict(device='X1',terminal='1'),
           target=dict(device='X1',terminal='1'),document='synthetic.json', location='record 1')
PAYLOAD = dict(schema_version=1,project='project',revision='rev',records=[ROW,dict(ROW,location='record 2')])


@pytest.fixture
def setup(tmp_path):
    repo = Repository.create(tmp_path/'repo.sqlite3')
    for name, role in [('engineer','engineer'),('viewer','viewer')]:
        repo.provision(name,Scope('org','project'),role)
    class Verifier:
        def verify(self, ticket):
            return Identity(ticket,'org','project') if ticket in ('engineer','viewer') else None
    with TestClient(create_secure_app(verifier=Verifier(),backend=ScopedBackend(repo),sessions=Sessions(),origin=ORIGIN),base_url=ORIGIN) as client:
        yield client, repo


def login(client, name='engineer'):
    r = client.post('/auth/login',json={'credential':name},headers={'origin':ORIGIN})
    return {'origin':ORIGIN,'x-csrf-token':r.json()['csrf_token']}


def test_real_saved_findings_history_exports_and_compare(setup):
    client, repo = setup
    headers = login(client)
    def check():
        r = client.post('/api/connections/check',json=PAYLOAD,headers=headers)
        assert r.status_code == 200, r.text
        return r.json()
    first = check()
    assert {i['code'] for i in first['issues']} == {'KD-CON-101','KD-CON-102'}
    assert all(i['severity'] == 'info' for i in first['issues'])
    fid = first['finding_states'][0]['id']
    url = f"/api/runs/{first['id']}/findings/{fid}"
    assert client.patch(url,json={'status':'resolved','comment':'Synthetic review','expected_version':0},headers=headers).status_code == 200
    assert len(client.get(url+'/history').json()) == 1
    second = check()
    assert all(s['status'] == 'new' for s in second['finding_states'])
    assert client.get(f"/api/runs/{second['id']}/compare/{first['id']}").status_code == 200
    for suffix in ('/csv','/xlsx'):
        assert client.get(f"/api/runs/{first['id']}{suffix}").status_code == 200


@pytest.mark.parametrize('damage', ['project','extra','empty','type','source','version','many'])
def test_strict_rejection_never_saves_partial_run(setup, damage):
    client, repo = setup
    headers = login(client)
    data = deepcopy(PAYLOAD)
    if damage == 'project': data['project'] = 'foreign'
    elif damage == 'extra': data['organization_id'] = 'spoof'
    elif damage == 'empty': data['records'] = []
    elif damage == 'type': data['records'][0]['wire'] = 42
    elif damage == 'source': data['records'][0]['location'] = ' '
    elif damage == 'version': data['schema_version'] = True
    else: data['records'] = [ROW]*1001
    assert client.post('/api/connections/check',json=data,headers=headers).status_code in (404,422)
    assert repo.list('engineer',Scope('org','project')) == []


def test_auth_csrf_and_bounded_duplicate_json(setup, monkeypatch):
    import secure_api
    client, repo = setup
    assert client.post('/api/connections/check',json=PAYLOAD).status_code == 401
    headers = login(client,'viewer')
    assert client.post('/api/connections/check',json=PAYLOAD,headers=headers).status_code == 403
    headers = login(client)
    assert client.post('/api/connections/check',json=PAYLOAD).status_code == 403
    headers['content-type'] = 'application/json'
    assert client.post('/api/connections/check',content=b'{"project":"a","project":"b"}',headers=headers).status_code == 422
    monkeypatch.setattr(secure_api,'MAX_CONNECTION_BODY',32)
    assert client.post('/api/connections/check',content=(b'x'*40 for _ in range(2)),headers=headers).status_code == 413
    assert repo.list('engineer',Scope('org','project')) == []
