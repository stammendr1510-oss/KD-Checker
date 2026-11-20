"""Synthetic worker isolation/termination tests; no real documents or credentials."""
from io import BytesIO, StringIO
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest
import fitz
from openpyxl import Workbook
import ezdxf

import parser_process as worker
from secure_backend import check_documents

CONFIG=json.loads((Path(__file__).parents[1]/'config.json').read_text(encoding='utf-8'))


def synthetic(kind):
    if kind=='pdf':
        with fitz.open() as pdf:
            pdf.new_page().insert_text((72,72),'Synthetic only text')
            return pdf.tobytes()
    if kind=='xlsx':
        book=Workbook()
        book.active.append(['позиция','обозначение','наименование'])
        book.active.append(['1','SYNTH.001','Synthetic'])
        out=BytesIO()
        book.save(out)
        book.close()
        return out.getvalue()
    if kind=='dxf':
        document=ezdxf.new()
        out=StringIO()
        document.write(out)
        return out.getvalue().encode()
    return 'позиция;обозначение;наименование\n1;SYNTH.001;A\n1;SYNTH.002;B\n'.encode()


@pytest.mark.parametrize('kind',['pdf','csv','xlsx','dxf'])
def test_real_formats_preserve_existing_deterministic_results(tmp_path,monkeypatch,kind):
    monkeypatch.setattr(worker,'_TEMP_ROOT',tmp_path)
    files=[('synthetic.'+kind,synthetic(kind))]
    assert worker.run_check(files,CONFIG)==check_documents(files,CONFIG)
    assert list(tmp_path.iterdir())==[]


def helper(tmp_path,text):
    script=tmp_path/'synthetic_worker.py'
    script.write_text('import sys,time,json,os\nfrom pathlib import Path\nroot=Path(sys.argv[2])\nwhile not (root/"go").exists(): time.sleep(.01)\n'+text,encoding='utf-8')
    return script


def test_hard_timeout_kills_process_and_cleans_private_directory(tmp_path,monkeypatch):
    private=tmp_path/'private'
    private.mkdir()
    monkeypatch.setattr(worker,'_TEMP_ROOT',private)
    monkeypatch.setattr(worker,'_WORKER',helper(tmp_path,'while True: time.sleep(1)\n'))
    created=[]
    original=subprocess.Popen
    def track(*args,**kwargs):
        process=original(*args,**kwargs)
        created.append(process)
        return process
    monkeypatch.setattr(worker.subprocess,'Popen',track)
    with pytest.raises(worker.ProcessingUnavailable):
        worker.run_check([('synthetic.csv',b'a;b')],CONFIG,worker.Limits(seconds=.2))
    assert created and created[0].poll() is not None
    assert list(private.iterdir())==[]


@pytest.mark.parametrize('result', ['{}', '{"status":"ok","issues":[{}]}', 'x'*2048])
def test_parent_rejects_invalid_or_oversized_output(tmp_path,monkeypatch,result):
    script=helper(tmp_path,f"(root/'result.json').write_text({result!r},encoding='utf-8')\n")
    monkeypatch.setattr(worker,'_WORKER',script)
    with pytest.raises(worker.ProcessingUnavailable):
        worker.run_check([('synthetic.csv',b'a;b')],CONFIG,worker.Limits(output_bytes=1024))


def test_child_does_not_inherit_credential_environment(tmp_path,monkeypatch):
    secret='SYNTHETIC-PRIVATE-ENVIRONMENT'
    monkeypatch.setenv('KD_PRIVATE_TEST_SECRET',secret)
    script=helper(tmp_path,"assert 'KD_PRIVATE_TEST_SECRET' not in os.environ\n(root/'result.json').write_text(json.dumps({'status':'ok','issues':[]}))\n")
    monkeypatch.setattr(worker,'_WORKER',script)
    assert worker.run_check([('synthetic.csv',b'a;b')],CONFIG)==[]
    assert __import__('os').environ['KD_PRIVATE_TEST_SECRET']==secret


@pytest.mark.skipif(os.name!='nt',reason='Windows Job Object bootstrap only')
def test_windows_worker_runs_inside_single_process_job(tmp_path,monkeypatch):
    monkeypatch.setattr(worker,'_TEMP_ROOT',tmp_path)
    command=worker._worker_command(str(tmp_path))
    assert Path(command[0])==Path(sys._base_executable)
    assert command[1:3]==['-I','-S']
    assert str(Path(sys.prefix)/'Lib'/'site-packages') in command
    files=[('synthetic.csv',synthetic('csv'))]
    assert worker.run_check(files,CONFIG)==check_documents(files,CONFIG)
    assert list(tmp_path.iterdir())==[]


def test_capacity_refuses_work_without_queueing(tmp_path,monkeypatch):
    monkeypatch.setattr(worker,'_TEMP_ROOT',tmp_path)
    assert worker._GATE.acquire(blocking=False)
    try:
        with pytest.raises(worker.ProcessingUnavailable):
            worker.run_check([('synthetic.csv',b'a;b')],CONFIG)
        assert list(tmp_path.iterdir())==[]
    finally:
        worker._GATE.release()


@pytest.mark.skipif(__import__('os').name!='posix',reason='POSIX resource API only')
def test_posix_caps_are_applied_in_child_not_parent(tmp_path,monkeypatch):
    import resource
    before=resource.getrlimit(resource.RLIMIT_AS)
    root=str(Path(worker.__file__).parent)
    script=helper(tmp_path,f'''sys.path.insert(0,{root!r})
from parser_process import _posix_limits,Limits
import resource
_posix_limits(Limits())
assert resource.getrlimit(resource.RLIMIT_AS)==(1024*1024*1024,)*2
assert resource.getrlimit(resource.RLIMIT_CPU)==(10,10)
assert resource.getrlimit(resource.RLIMIT_CORE)==(0,0)
(root/'result.json').write_text(json.dumps({{'status':'ok','issues':[]}}))
''')
    monkeypatch.setattr(worker,'_WORKER',script)
    assert worker.run_check([('synthetic.csv',b'a;b')],CONFIG)==[]
    assert resource.getrlimit(resource.RLIMIT_AS)==before


def test_worker_timeout_never_saves_partial_http_run(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from secure_api import create_secure_app
    from secure_backend import ScopedBackend
    from scoped_history import Repository,Scope
    from secure_sessions import Sessions,Identity
    monkeypatch.setattr(worker,'_WORKER',helper(tmp_path,'while True: time.sleep(1)\n'))
    repo=Repository.create(tmp_path/'repo.sqlite3')
    repo.provision('synthetic',Scope('org','project'),'engineer')
    class Verifier:
        def verify(self,credential): return Identity('synthetic','org','project')
    origin='https://kd.test'
    app=create_secure_app(verifier=Verifier(),backend=ScopedBackend(repo,parser_limits=worker.Limits(seconds=.2)),sessions=Sessions(),origin=origin)
    with TestClient(app,base_url=origin) as client:
        login=client.post('/auth/login',json={'credential':'synthetic'},headers={'origin':origin})
        response=client.post('/api/check',files={'files':('synthetic.csv',b'a;b','text/csv')},data={'revision':'rev'},
                             headers={'origin':origin,'x-csrf-token':login.json()['csrf_token']})
        assert response.status_code==503
        assert 'synthetic_worker' not in response.text
    assert repo.list('synthetic',Scope('org','project'))==[]
