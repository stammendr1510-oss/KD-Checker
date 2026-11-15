import asyncio
import tempfile
import pytest
from body_budget import BodyBudget


def invoke(messages, *, limit=16, declared=None, seconds=1, delay=0, fail=False):
    sent, calls, buffers = [], [], []
    real = tempfile.SpooledTemporaryFile
    async def run():
        queue = list(messages)
        async def receive():
            if delay: await asyncio.sleep(delay)
            return queue.pop(0) if queue else {'type':'http.disconnect'}
        async def send(message): sent.append(message)
        async def app(scope,receive,send):
            data=b''
            while True:
                message=await receive()
                data+=message.get('body',b'')
                if not message.get('more_body',False): break
            calls.append(data)
            if fail: raise RuntimeError('synthetic failure')
            await send({'type':'http.response.start','status':200,'headers':[]})
            await send({'type':'http.response.body','body':b'ok'})
        scope={'type':'http','path':'/api/check','headers':[]}
        if declared is not None: scope['headers']=[(b'content-length',declared)]
        middleware=BodyBudget(app,limit=limit,seconds=seconds,memory=4)
        await middleware(scope,receive,send)
    return run,sent,calls

@pytest.mark.parametrize('messages,declared,status',[
    ([{'type':'http.request','body':b'a'*17}],b'17',413),
    ([{'type':'http.request','body':b'a'*10,'more_body':True},{'type':'http.request','body':b'b'*7}],None,413),
    ([{'type':'http.request','body':b'abc'}],b'4',400),
    ([],b'not-a-number',400),
])
def test_reject_before_parser(messages,declared,status):
    run,sent,calls=invoke(messages,declared=declared)
    asyncio.run(run())
    assert sent[0]['status']==status and calls==[]

def test_receive_deadline():
    run,sent,calls=invoke([{'type':'http.request','body':b'x'}],seconds=.001,delay=.02)
    asyncio.run(run())
    assert sent[0]['status']==408 and calls==[]

@pytest.mark.parametrize('fail',[False,True])
def test_spool_replays_exact_bytes_and_always_closes(monkeypatch,fail):
    buffers=[]
    original=tempfile.SpooledTemporaryFile
    def tracked(*args,**kwargs):
        obj=original(*args,**kwargs)
        buffers.append(obj)
        return obj
    monkeypatch.setattr(tempfile,'SpooledTemporaryFile',tracked)
    run,sent,calls=invoke([{'type':'http.request','body':b'abcdef','more_body':True},{'type':'http.request','body':b'ghijklmnop'}],fail=fail)
    if fail:
        with pytest.raises(RuntimeError): asyncio.run(run())
    else:
        asyncio.run(run())
        assert sent[0]['status']==200
    assert calls==[b'abcdefghijklmnop']
    assert buffers and all(b.closed and b._rolled for b in buffers)


def test_authenticated_multipart_is_rejected_before_run_creation(tmp_path):
    from fastapi.testclient import TestClient
    from secure_api import create_secure_app
    from secure_backend import ScopedBackend
    from scoped_history import Repository,Scope
    from secure_sessions import Sessions,Identity
    repo=Repository.create(tmp_path/'repo.sqlite3')
    repo.provision('synthetic',Scope('org','project'),'engineer')
    class Verifier:
        def verify(self,ticket): return Identity('synthetic','org','project')
    origin='https://kd.test'
    app=create_secure_app(verifier=Verifier(),backend=ScopedBackend(repo),sessions=Sessions(),origin=origin,body_limit=128)
    with TestClient(app,base_url=origin) as client:
        login=client.post('/auth/login',json={'credential':'synthetic'},headers={'origin':origin})
        headers={'origin':origin,'x-csrf-token':login.json()['csrf_token'],
                 'content-type':'multipart/form-data; boundary=synthetic'}
        response=client.post('/api/check',content=(b'x'*80 for _ in range(2)),headers=headers)
        assert response.status_code==413
    assert repo.list('synthetic',Scope('org','project'))==[]
