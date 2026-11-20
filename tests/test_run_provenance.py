"""Saved effective settings, never retrospective inference from current config."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from io import BytesIO
import json
import sqlite3
import threading

import fitz
from fastapi.testclient import TestClient
from openpyxl import load_workbook
import pytest

from parser_process import Limits
from project_configuration import DEFAULT
from run_provenance import capture, normalize
from scoped_history import Repository, Scope, Missing
from secure_api import create_secure_app
from secure_backend import ScopedBackend
from secure_sessions import Identity, Sessions

ORIGIN='https://kd.test'
SCOPE=Scope('org-a','project-a')
OTHER=Scope('org-b','project-a')
ISSUE=dict(severity='info',code='DEMO-1',file='SYNTH.001.pdf',location='PDF 1',
           message='=SYNTHETIC()',recommendation='Review')

@pytest.fixture
def system(tmp_path):
    repo=Repository.create(tmp_path/'repo.sqlite3')
    for user,role,scope in [('engineer','engineer',SCOPE),('admin','admin',SCOPE),('foreign','engineer',OTHER)]:
        repo.provision(user,scope,role)
    class Verifier:
        def verify(self,ticket):
            if ticket not in ('engineer','admin','foreign'): return None
            scope=OTHER if ticket=='foreign' else SCOPE
            return Identity(ticket,scope.organization,scope.project)
    backend=ScopedBackend(repo)
    app=create_secure_app(verifier=Verifier(),backend=backend,sessions=Sessions(),origin=ORIGIN)
    return repo,backend,app


def login(client,user):
    response=client.post('/auth/login',json={'credential':user},headers={'Origin':ORIGIN})
    assert response.status_code==200
    return {'Origin':ORIGIN,'X-CSRF-Token':response.json()['csrf_token']}


def pdf():
    with fitz.open() as doc:
        doc.new_page().insert_text((72,72),'Synthetic text longer than twenty five chars')
        return doc.tobytes()


def evidence():
    return capture('documents',configuration=DEFAULT,configuration_version=0,parser_limits=Limits())


def test_configuration_change_after_selection_retains_actual_worker_snapshot(system,monkeypatch):
    import secure_backend
    repo,backend,app=system
    selected,release=threading.Event(),threading.Event()
    actual=secure_backend.run_check
    def paused(files,cfg,limits):
        # Real worker is paused just after HTTP selected its config; no fake results.
        selected.set()
        if not release.wait(timeout=10): raise RuntimeError('Synthetic gate timeout')
        return actual(files,cfg,limits)
    monkeypatch.setattr(secure_backend,'run_check',paused)
    with TestClient(app,base_url=ORIGIN) as engineer, TestClient(app,base_url=ORIGIN) as admin:
        headers=login(engineer,'engineer')
        admin_headers=login(admin,'admin')
        def check():
            return engineer.post('/api/check',files={'files':('SYNTH.001.pdf',pdf(),'application/pdf')},
                                 data={'revision':'synthetic-rev'},headers=headers)
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending=pool.submit(check)
            try:
                assert selected.wait(timeout=10)
                config=admin.get('/api/config').json()
                config['min_pdf_text_chars']=100
                response=admin.put('/api/config',json={'expected_version':0,'settings':config},headers=admin_headers)
                assert response.status_code==200
            finally:
                release.set()
            first_response=pending.result(timeout=30)
        assert first_response.status_code==200,first_response.text
        first=first_response.json()
        assert 'KD-002' not in [i['code'] for i in first['issues']]
        saved=first['provenance']['evidence']
        assert saved['configuration_version']==0
        assert saved['configuration']['min_pdf_text_chars']==25
        second=check().json()
        assert 'KD-002' in [i['code'] for i in second['issues']]
        assert second['provenance']['evidence']['configuration_version']==1
        assert second['provenance']['evidence']['configuration']['min_pdf_text_chars']==100
        compare=engineer.get(f"/api/runs/{second['id']}/compare/{first['id']}").json()['provenance_comparison']
        assert compare['same_configuration'] is False and compare['same_source'] is True
        assert compare['same_runtime'] is True
        config['min_pdf_text_chars']=200
        assert admin.put('/api/config',json={'expected_version':1,'settings':config},headers=admin_headers).status_code==200
        assert engineer.get(f"/api/runs/{first['id']}").json()['provenance']==first['provenance']
        assert engineer.get(f"/api/runs/{second['id']}").json()['provenance']==second['provenance']


def test_atomic_write_immutability_cross_scope_and_rollback(system):
    repo,_,_=system
    first=repo.save('engineer',SCOPE,['SYNTH.001.pdf'],[ISSUE],revision='rev',provenance=evidence())
    record=repo.get('engineer',SCOPE,first)
    # Caller changes its returned dict, not the saved evidence.
    record['provenance']['evidence']['configuration']['min_pdf_text_chars']=999
    assert repo.get('engineer',SCOPE,first)['provenance']['evidence']['configuration']['min_pdf_text_chars']==25
    with pytest.raises(Missing): repo.get('foreign',OTHER,first)
    for sql in ('UPDATE run_provenance SET payload=payload','DELETE FROM run_provenance'):
        with pytest.raises(sqlite3.IntegrityError),repo.transaction(True) as con:
            con.execute(sql)
    with pytest.raises(sqlite3.IntegrityError),repo.transaction(True) as con:
        con.execute('INSERT INTO run_provenance VALUES(?,?,?,?)',(OTHER.organization,OTHER.project,first,json.dumps(evidence())))
    with repo.transaction(True) as con:
        con.execute("CREATE TRIGGER fail_provenance BEFORE INSERT ON run_provenance BEGIN SELECT RAISE(ABORT,'synthetic failure'); END")
    with pytest.raises(sqlite3.IntegrityError):
        repo.save('engineer',SCOPE,['SYNTH.001.pdf'],[ISSUE],revision='rev-2',provenance=evidence())
    with repo.transaction() as con:
        assert con.execute('SELECT count(*) FROM runs').fetchone()[0]==1
        assert con.execute('SELECT count(*) FROM decisions').fetchone()[0]==1
        assert con.execute('SELECT count(*) FROM run_provenance').fetchone()[0]==1
        assert not con.execute('PRAGMA foreign_key_check').fetchall()


def test_historical_unknown_is_not_backfilled_and_comparison_is_explicit(system):
    repo,backend,_=system
    old=repo.save('engineer',SCOPE,['SYNTH.001.pdf'],[ISSUE],revision='old')
    assert repo.get('engineer',SCOPE,old)['provenance']=={'status':'unknown','reason':'not_recorded'}
    with repo.transaction() as con:
        assert not con.execute("SELECT 1 FROM sqlite_master WHERE name='run_provenance'").fetchone()
    backend.project_configuration.update(Identity('admin',SCOPE.organization,SCOPE.project),
        {'settings':dict(DEFAULT,min_pdf_text_chars=100),'expected_version':0})
    new=repo.save('engineer',SCOPE,['SYNTH.001.pdf'],[ISSUE],revision='new',provenance=evidence())
    assert repo.get('engineer',SCOPE,old)['provenance']['status']=='unknown'
    comparison=repo.compare('engineer',SCOPE,old,new)['provenance_comparison']
    assert comparison['same_configuration'] is None and comparison['same_source'] is None


def test_read_and_excel_use_inert_historical_data_not_current_policy(system):
    repo,backend,app=system
    snapshot=evidence()
    snapshot['configuration']['designation_pattern']='(historical-pattern)+$'
    snapshot['configuration']['specification_headers']=['=SYNTHETIC()']
    # It is safe to read legacy regex as text; never execute or revalidate it
    # against today's administrator regex allowlist during readback.
    identifier=repo.save('engineer',SCOPE,['SYNTH.001.pdf'],[ISSUE],revision='rev',provenance=snapshot)
    with TestClient(app,base_url=ORIGIN) as client:
        login(client,'engineer')
        assert client.get(f'/api/runs/{identifier}').json()['provenance']['evidence']==normalize(snapshot)
        response=client.get(f'/api/runs/{identifier}/xlsx')
        assert response.status_code==200
        with closing(load_workbook(BytesIO(response.content))) as book:
            assert 'Происхождение' in book.sheetnames
            cells=[cell for row in book['Происхождение'] for cell in row]
            assert all(cell.data_type!='f' for cell in cells)
            assert any('=SYNTHETIC()' in str(cell.value) for cell in cells)
        assert "'=SYNTHETIC()" in client.get(f'/api/runs/{identifier}/csv').text
        login(client,'foreign')
        for suffix in ('','/xlsx','/csv'):
            assert client.get(f'/api/runs/{identifier}{suffix}').status_code==404


@pytest.mark.parametrize('damage',['extra','bool_version','fingerprint','config','limits','dependencies'])
def test_invalid_evidence_never_creates_partial_run(system,damage):
    repo,_,_=system
    value=evidence()
    if damage=='extra': value['token']='synthetic-private'
    elif damage=='bool_version': value['configuration_version']=True
    elif damage=='fingerprint': value['source_fingerprint']='not-a-hash'
    elif damage=='config': value['configuration']['min_pdf_text_chars']='25'
    elif damage=='limits': value['parser_limits']['seconds']=True
    else: value['dependencies']['secret']='synthetic-private'
    with pytest.raises(ValueError):
        repo.save('engineer',SCOPE,['SYNTH.001.pdf'],[ISSUE],revision='rev',provenance=value)
    assert repo.list('engineer',SCOPE)==[]


def test_source_drift_discards_result_without_saving_run(system,monkeypatch):
    import secure_backend
    repo,_,app=system
    monkeypatch.setattr(secure_backend,'source_fingerprint',lambda:'0'*64)
    with TestClient(app,base_url=ORIGIN) as client:
        headers=login(client,'engineer')
        response=client.post('/api/check',files={'files':('SYNTH.001.pdf',pdf(),'application/pdf')},
                             data={'revision':'rev'},headers=headers)
        assert response.status_code==503
    assert repo.list('engineer',SCOPE)==[]


def test_connection_evidence_never_claims_document_settings(system):
    repo,_,_=system
    value=capture('connections')
    identifier=repo.save('engineer',SCOPE,['SYNTH.001.json'],[ISSUE],revision='rev',provenance=value)
    saved=repo.get('engineer',SCOPE,identifier)['provenance']['evidence']
    assert all(saved[k] is None for k in ('configuration','configuration_version','parser_limits'))


def test_corrupt_record_is_not_replaced_with_unknown_or_current_settings(system):
    repo,_,app=system
    identifier=repo.save('engineer',SCOPE,['SYNTH.001.pdf'],[ISSUE],revision='rev',provenance=evidence())
    # Simulate an offline DB-owner corruption, not an available application API.
    with repo.transaction(True) as con:
        con.execute('DROP TRIGGER run_provenance_no_update')
        invalid=evidence() | {'secret':'SYNTHETIC-PRIVATE-CORRUPTION'}
        con.execute('UPDATE run_provenance SET payload=?',(json.dumps(invalid),))
    with TestClient(app,base_url=ORIGIN) as client:
        login(client,'engineer')
        response=client.get(f'/api/runs/{identifier}')
        assert response.status_code==503
        assert 'SYNTHETIC-PRIVATE-CORRUPTION' not in response.text


def test_optional_staging_keeps_imported_history_unknown(tmp_path,monkeypatch):
    staging=pytest.importorskip('staging_import',reason='Optional #56 is exercised in combined acceptance')
    import storage
    source=tmp_path/'legacy.sqlite3'
    monkeypatch.setattr(storage,'DB',source)
    old=storage.save_run(['SYNTH.001.pdf'],[ISSUE])
    target=staging.stage_history(source,tmp_path/'copy.sqlite3',tmp_path/'stage.sqlite3',
                                 scope=SCOPE,revision='assigned',operator='operator')
    backend=ScopedBackend(target)
    identity=Identity('operator',SCOPE.organization,SCOPE.project)
    backend.project_configuration.update(identity,{'settings':dict(DEFAULT,min_pdf_text_chars=999),'expected_version':0})
    assert backend.record(identity,old)['provenance']=={'status':'unknown','reason':'not_recorded'}
    with target.transaction() as con:
        assert not con.execute("SELECT 1 FROM sqlite_master WHERE name='run_provenance'").fetchone()
        assert con.execute('SELECT count(*) FROM legacy_archive').fetchone()[0]>0
