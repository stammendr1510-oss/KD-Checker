"""Synthetic transport disconnects terminate real child processes, not API spies."""
import asyncio
from contextlib import suppress
from pathlib import Path
import subprocess
import threading
import time

from fastapi.testclient import TestClient
import httpx
import pytest

from body_budget import BodyBudget
from parser_capacity import claim
import parser_process as worker
from project_configuration import DEFAULT
from scoped_history import Repository, Scope
from secure_api import create_secure_app, COOKIE
from secure_backend import ScopedBackend
from secure_sessions import Identity, Sessions

ORIGIN='https://kd.test'
FILES=[('SYNTH.001.csv',b'position;designation;name\n1;SYNTH.001;Synthetic\n')]


def test_precancel_refuses_without_spawning_or_creating_temp(tmp_path,monkeypatch):
    cancel=threading.Event()
    cancel.set()
    monkeypatch.setattr(worker,'_TEMP_ROOT',tmp_path)
    def forbidden(*args,**kwargs): pytest.fail('Cancelled work must not spawn')
    monkeypatch.setattr(worker.subprocess,'Popen',forbidden)
    with pytest.raises(worker.ProcessingUnavailable): worker.run_check(FILES,DEFAULT,cancelled=cancel)
    assert list(tmp_path.iterdir())==[]
    with pytest.raises(ValueError): worker.run_check(FILES,DEFAULT,cancelled=True)


@pytest.mark.parametrize('signal',['disconnect','transport_failure'])
def test_transport_signal_cancels_and_broadcasts_to_downstream_receivers(signal):
    async def run():
        release=asyncio.Event()
        sent_body=False
        scope={'type':'http','path':'/api/check','headers':[]}
        async def receive():
            nonlocal sent_body
            if not sent_body:
                sent_body=True
                return {'type':'http.request','body':b'synthetic'}
            await release.wait()
            if signal=='transport_failure': raise OSError('SYNTHETIC-PRIVATE-TRANSPORT')
            return {'type':'http.disconnect'}
        async def send(message): pass
        async def app(scope,receive,send):
            assert (await receive())['body']==b'synthetic'
            assert not scope['state']['parser_cancelled'].is_set()
            a,b=asyncio.create_task(receive()),asyncio.create_task(receive())
            release.set()
            assert (await a)['type']=='http.disconnect'
            assert (await b)['type']=='http.disconnect'
            assert scope['state']['parser_cancelled'].is_set()
        await asyncio.wait_for(BodyBudget(app)(scope,receive,send),timeout=2)
    asyncio.run(run())


def test_normal_response_cancels_pending_watcher_without_false_disconnect():
    async def run():
        body=False
        monitor_finished=asyncio.Event()
        scope={'type':'http','path':'/api/check','headers':[]}
        async def receive():
            nonlocal body
            if not body:
                body=True
                return {'type':'http.request','body':b'ok'}
            try: await asyncio.Event().wait()
            finally: monitor_finished.set()
        async def send(message): pass
        async def app(scope,receive,send):
            assert (await receive())['body']==b'ok'
            await asyncio.sleep(.01)
            assert not scope['state']['parser_cancelled'].is_set()
        await BodyBudget(app)(scope,receive,send)
        assert monitor_finished.is_set()
        assert scope['state']['parser_cancelled'].is_set()
    asyncio.run(run())


@pytest.mark.parametrize('signal',['disconnect','cancel_asgi_task','repeated_cancel'])
def test_protected_http_cancellation_kills_worker_without_run_and_releases_capacity(tmp_path,monkeypatch,signal):
    repo=Repository.create(tmp_path/'history.sqlite3')
    scope=Scope('org','project')
    repo.provision('engineer',scope,'engineer')
    class Verifier:
        def verify(self,credential):
            return Identity('engineer','org','project') if credential=='synthetic' else None
    backend=ScopedBackend(repo)
    app=create_secure_app(verifier=Verifier(),backend=backend,sessions=Sessions(),origin=ORIGIN)
    with TestClient(app,base_url=ORIGIN) as client:
        login=client.post('/auth/login',json={'credential':'synthetic'},headers={'Origin':ORIGIN})
        assert login.status_code==200
        token=client.cookies.get(COOKIE)
        csrf=login.json()['csrf_token']
    ready=tmp_path/'parser.ready'
    helper=tmp_path/'stalled_parser.py'
    source=str(Path(worker.__file__).parent)
    helper.write_text(f'''import sys,time
from pathlib import Path
sys.path.insert(0,{source!r})
import parser_process
def stalled(*args):
    Path({str(ready)!r}).touch(exist_ok=False)
    while True: time.sleep(.1)
parser_process._parse=stalled
sys.exit(parser_process._worker(sys.argv[2]))
''',encoding='utf-8')
    private=tmp_path/'private'
    private.mkdir()
    monkeypatch.setattr(worker,'_TEMP_ROOT',private)
    actual=worker._WORKER
    monkeypatch.setattr(worker,'_WORKER',helper)
    children=[]
    popen=subprocess.Popen
    def tracked(*args,**kwargs):
        child=popen(*args,**kwargs)
        children.append(child)
        return child
    monkeypatch.setattr(worker.subprocess,'Popen',tracked)
    req=httpx.Request('POST',ORIGIN+'/api/check',files={'files':(FILES[0][0],FILES[0][1],'text/csv')},
                      data={'revision':'synthetic'},headers={'Origin':ORIGIN,'X-CSRF-Token':csrf,'Cookie':f'{COOKIE}={token}'})
    raw=req.read()
    async def run():
        disconnect=asyncio.Event()
        delivered=False
        sent=[]
        async def receive():
            nonlocal delivered
            if not delivered:
                delivered=True
                return {'type':'http.request','body':raw,'more_body':False}
            await disconnect.wait()
            return {'type':'http.disconnect'}
        async def send(message): sent.append(message)
        asgi_scope={'type':'http','asgi':{'version':'3.0','spec_version':'2.3'},'http_version':'1.1',
                    'method':'POST','scheme':'https','path':'/api/check','raw_path':b'/api/check',
                    'query_string':b'','root_path':'','headers':[(name.lower(),value) for name,value in req.headers.raw],
                    'client':('127.0.0.1',1234),'server':('kd.test',443)}
        task=asyncio.create_task(app(asgi_scope,receive,send))
        try:
            deadline=time.monotonic()+10
            while not ready.exists() and not task.done() and time.monotonic()<deadline:
                await asyncio.sleep(.01)
            assert ready.exists(), ('Request did not reach the real parser process', sent, len(children))
            if signal=='disconnect':
                disconnect.set()
                await asyncio.wait_for(task,timeout=5)
            else:
                task.cancel()
                if signal=='repeated_cancel':
                    await asyncio.sleep(.001)
                    task.cancel()
                with pytest.raises(asyncio.CancelledError): await asyncio.wait_for(task,timeout=5)
            # Cancellation can end the ASGI task before a shielded thread's final
            # cleanup, but the thread must kill/reap promptly and free its temp.
            deadline=time.monotonic()+5
            while (any(c.poll() is None for c in children) or list(private.iterdir())) and time.monotonic()<deadline:
                await asyncio.sleep(.01)
            assert children and all(c.poll() is not None for c in children)
            assert list(private.iterdir())==[]
            assert repo.list('engineer',scope)==[]
            with claim(backend.parser_capacity): pass
            assert all(b'SYNTHETIC-PRIVATE' not in m.get('body',b'') for m in sent)
        finally:
            disconnect.set()
            if not task.done(): task.cancel()
            with suppress(asyncio.CancelledError): await task
    asyncio.run(run())
    # Subsequent real HTTP work uses the actual parser and can reuse both gates.
    monkeypatch.setattr(worker,'_WORKER',actual)
    with TestClient(app,base_url=ORIGIN) as client:
        client.cookies.set(COOKIE,token,domain='kd.test',path='/')
        response=client.post('/api/check',files={'files':(FILES[0][0],FILES[0][1],'text/csv')},
                             data={'revision':'retry'},headers={'Origin':ORIGIN,'X-CSRF-Token':csrf})
        assert response.status_code==200,response.text
        assert len(repo.list('engineer',scope))==1


def test_cancellation_during_result_validation_never_returns_findings(tmp_path,monkeypatch):
    cancel=threading.Event()
    actual=worker._validate_result
    def cancelled_result(value):
        issues=actual(value)
        cancel.set()
        return issues
    monkeypatch.setattr(worker,'_validate_result',cancelled_result)
    monkeypatch.setattr(worker,'_TEMP_ROOT',tmp_path)
    with pytest.raises(worker.ProcessingUnavailable): worker.run_check(FILES,DEFAULT,cancelled=cancel)
    assert list(tmp_path.iterdir())==[]
