from io import BytesIO
from contextlib import closing
import fitz
from openpyxl import load_workbook
from fastapi.testclient import TestClient
import pytest

from secure_api import create_secure_app, COOKIE
from secure_sessions import Identity, Sessions
from scoped_history import Repository, Scope
from secure_backend import ScopedBackend

ORIGIN = 'https://kd.test'

class Verifier:
    def verify(self, ticket):
        return {'engineer': Identity('engineer','org','project'),
                'viewer': Identity('viewer','org','project'),
                'foreign': Identity('foreign','org','other'),
                'external': Identity('external','other-org','project')}.get(ticket)

def login(client, ticket):
    r = client.post('/auth/login', json={'credential':ticket}, headers={'origin':ORIGIN})
    assert r.status_code == 200
    return {'origin':ORIGIN,'x-csrf-token':r.json()['csrf_token']}

def pdf():
    with fitz.open() as doc:
        doc.new_page().insert_text((72,72),'SYNTHETIC ONLY')
        return doc.tobytes()

def test_real_http_pipeline_and_isolation(tmp_path):
    repo = Repository.create(tmp_path/'scoped.sqlite3')
    scope = Scope('org','project')
    for user, role, project in [('engineer','engineer','project'),('viewer','viewer','project'),('foreign','engineer','other'),('external','engineer','project')]:
        repo.provision(user,Scope('other-org' if user == 'external' else 'org',project),role)
    app = create_secure_app(verifier=Verifier(), backend=ScopedBackend(repo), sessions=Sessions(), origin=ORIGIN)
    with TestClient(app,base_url=ORIGIN) as client:
        assert client.get('/api/runs').status_code == 401
        headers = login(client,'engineer')
        assert client.post('/api/check',files={'files':('synthetic.pdf',pdf(),'application/pdf')},
                           data={'revision':'rev','organization_id':'spoof'},headers=headers).status_code == 422
        def check():
            r = client.post('/api/check', files={'files':('synthetic.pdf',pdf(),'application/pdf')},
                            data={'revision':'synthetic-rev'},headers=headers)
            assert r.status_code == 200, r.text
            return r.json()
        first = check()
        assert first['issues'] and first['finding_states']
        fid = first['finding_states'][0]['id']
        url = f"/api/runs/{first['id']}/findings/{fid}"
        r = client.patch(url, json={'status':'resolved','comment':'=SYNTHETIC','expected_version':0},headers=headers)
        assert r.status_code == 200 and r.json()['version'] == 1
        events = client.get(url+'/history').json()
        assert len(events) == 1 and events[0]['actor'] == 'engineer'
        second = check()
        assert all(s['status'] == 'new' for s in second['finding_states'])
        assert client.get(f"/api/runs/{second['id']}/findings/{fid}/history").status_code == 404
        assert client.patch(url,json={'status':'new','comment':'stale','expected_version':0},headers=headers).status_code == 409
        assert client.get(f"/api/runs/{second['id']}/compare/{first['id']}").json()['summary']['persisting'] > 0
        assert client.get(f"/api/runs/{first['id']}/csv").status_code == 200
        report = client.get(f"/api/runs/{first['id']}/xlsx")
        with closing(load_workbook(BytesIO(report.content))) as book:
            assert book['Замечания']['I2'].value == '=SYNTHETIC'
            assert book['Замечания']['I2'].data_type == 's'
        reader = login(client,'viewer')
        assert client.patch(url,json={'status':'new','comment':'','expected_version':1},headers=reader).status_code == 403
        foreign = login(client,'foreign')
        assert client.get('/api/runs').json() == []
        for suffix in ('','/csv','/xlsx',f'/findings/{fid}/history'):
            assert client.get(f"/api/runs/{first['id']}{suffix}").status_code == 404
        other = client.post('/api/check',files={'files':('other.pdf',pdf(),'application/pdf')},data={'revision':'rev'},headers=foreign).json()
        assert client.get(f"/api/runs/{other['id']}/compare/{first['id']}").status_code == 404
        foreign = login(client,'external')
        assert client.get('/api/runs').json() == []
        assert client.get(f"/api/runs/{first['id']}/xlsx").status_code == 404
        old = client.cookies.get(COOKIE)
        assert client.post('/auth/logout',headers=foreign).status_code == 200
        client.cookies.set(COOKIE,old)
        assert client.get('/api/runs').status_code == 401

def test_membership_revoked_between_http_gate_and_repository_write(tmp_path, monkeypatch):
    repo = Repository.create(tmp_path/'scoped.sqlite3')
    scope = Scope('org','project')
    repo.provision('engineer',scope,'engineer')
    backend = ScopedBackend(repo)
    app = create_secure_app(verifier=Verifier(),backend=backend,sessions=Sessions(),origin=ORIGIN)
    original = repo.save
    def revoke_then_save(*args, **kwargs):
        repo.revoke('engineer',scope)
        return original(*args,**kwargs)
    monkeypatch.setattr(repo,'save',revoke_then_save)
    with TestClient(app,base_url=ORIGIN) as client:
        headers = login(client,'engineer')
        r = client.post('/api/check',files={'files':('synthetic.pdf',pdf(),'application/pdf')},data={'revision':'rev'},headers=headers)
        assert r.status_code == 403
    with repo.transaction() as con:
        assert con.execute('SELECT count(*) FROM runs').fetchone()[0] == 0
