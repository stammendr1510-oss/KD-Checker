"""Small synthetic fixtures; kernel locks, real worker and real HTTP backend."""
from contextlib import contextmanager
import os
from pathlib import Path
import subprocess
import sys
import time

from fastapi.testclient import TestClient
import pytest

import parser_process
from parser_capacity import prepare, claim, CapacityUnavailable
from project_configuration import DEFAULT
from scoped_history import Repository, Scope
from secure_api import create_secure_app
from secure_backend import ScopedBackend
from secure_sessions import Identity, Sessions

SOURCE = str(Path(__file__).resolve().parents[1])
FILES = [('SYNTH.001.csv', b'position;designation;name\n1;SYNTH.001;Synthetic\n')]


@contextmanager
def holder(tmp_path, path):
    """A distinct interpreter, not another reference in this Python process."""
    script = tmp_path / 'synthetic_capacity_holder.py'
    ready = tmp_path / 'holder.ready'
    script.write_text(f'''import sys,time
from pathlib import Path
sys.path.insert(0,{SOURCE!r})
from parser_capacity import claim
with claim(sys.argv[1]):
    Path(sys.argv[2]).touch(exist_ok=False)
    while True: time.sleep(.1)
''', encoding='utf-8')
    process = subprocess.Popen([sys.executable, '-I', str(script), str(path), str(ready)],
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 10
        while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(.01)
        assert ready.exists(), 'Synthetic holder failed before lock acquisition'
        yield process
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)


def test_other_process_denied_and_kernel_releases_after_holder_killed(tmp_path):
    repo = Repository.create(tmp_path/'history.sqlite3')
    path = prepare(repo.path)
    before = path.stat()
    with holder(tmp_path, path) as process:
        with pytest.raises(CapacityUnavailable):
            with claim(path):
                pytest.fail('Second process must not enter')
        process.kill()
        process.wait(timeout=5)
        with claim(path):
            pass
    # No unlink/stale-lease cleanup and no replacement inode.
    assert path.read_bytes() == b''
    assert path.stat().st_ino == before.st_ino


def test_real_http_instances_share_quota_and_busy_run_is_not_saved(tmp_path):
    repo = Repository.create(tmp_path/'history.sqlite3')
    scope = Scope('org', 'project')
    repo.provision('engineer', scope, 'engineer')
    first, second = ScopedBackend(repo), ScopedBackend(Repository(repo.path))
    assert first.parser_capacity == second.parser_capacity
    class Verifier:
        def verify(self, credential):
            return Identity('engineer', 'org', 'project') if credential == 'synthetic' else None
    origin = 'https://kd.test'
    apps = [create_secure_app(verifier=Verifier(), backend=backend, sessions=Sessions(), origin=origin)
            for backend in (first, second)]
    with TestClient(apps[0], base_url=origin) as a, TestClient(apps[1], base_url=origin) as b:
        headers=[]
        for client in (a,b):
            login = client.post('/auth/login', json={'credential':'synthetic'}, headers={'Origin':origin})
            assert login.status_code == 200
            headers.append({'Origin':origin, 'X-CSRF-Token':login.json()['csrf_token']})
        def upload(client, header):
            return client.post('/api/check', files={'files':(FILES[0][0], FILES[0][1], 'text/csv')},
                               data={'revision':'synthetic'}, headers=header)
        with holder(tmp_path, first.parser_capacity):
            for client,header in zip((a,b),headers):
                response = upload(client,header)
                assert response.status_code == 503
                assert str(repo.path) not in response.text
                assert client.get('/api/runs').json() == []
        response = upload(b,headers[1])
        assert response.status_code == 200, response.text
        assert response.json()['provenance']['status'] == 'recorded'
        assert len(a.get('/api/runs').json()) == 1


def test_missing_gate_never_recreated_by_worker_and_cleanup_is_bounded(tmp_path, monkeypatch):
    private=tmp_path/'private'
    private.mkdir()
    monkeypatch.setattr(parser_process, '_TEMP_ROOT', private)
    missing=tmp_path/'not-provisioned'
    with pytest.raises(parser_process.ProcessingUnavailable):
        parser_process.run_check(FILES, DEFAULT, capacity=missing)
    assert not missing.exists()
    assert list(private.iterdir()) == []


def test_timeout_terminates_lock_owner_before_capacity_can_be_reused(tmp_path, monkeypatch):
    repo=Repository.create(tmp_path/'history.sqlite3')
    gate=prepare(repo.path)
    script=tmp_path/'sleeping_parser.py'
    script.write_text(f'''import sys,time
sys.path.insert(0,{SOURCE!r})
import parser_process
def stalled(*args):
    while True: time.sleep(.1)
parser_process._parse=stalled
sys.exit(parser_process._worker(sys.argv[2]))
''',encoding='utf-8')
    actual=parser_process._WORKER
    monkeypatch.setattr(parser_process,'_WORKER',script)
    with pytest.raises(parser_process.ProcessingUnavailable):
        parser_process.run_check(FILES,DEFAULT,parser_process.Limits(seconds=.5),capacity=gate)
    with claim(gate):
        pass
    monkeypatch.setattr(parser_process,'_WORKER',actual)
    assert isinstance(parser_process.run_check(FILES,DEFAULT,capacity=gate),list)


def test_nonempty_gate_is_rejected_without_overwrite(tmp_path):
    repo=Repository.create(tmp_path/'history.sqlite3')
    gate=prepare(repo.path)
    gate.write_bytes(b'SYNTHETIC-DO-NOT-REPLACE')
    with pytest.raises(CapacityUnavailable): prepare(repo.path)
    with pytest.raises(CapacityUnavailable):
        with claim(gate): pass
    assert gate.read_bytes()==b'SYNTHETIC-DO-NOT-REPLACE'


@pytest.mark.parametrize('kind',['symlink','hardlink'])
def test_gate_does_not_follow_links_or_accept_shared_inodes(tmp_path,kind):
    repo=Repository.create(tmp_path/'history.sqlite3')
    target=tmp_path/'other-file'
    target.touch(mode=0o600)
    gate=repo.path.with_name(repo.path.name+'.parser-capacity')
    try:
        if kind=='symlink': gate.symlink_to(target)
        else: os.link(target,gate)
    except OSError:
        pytest.skip('OS does not permit creating synthetic links')
    with pytest.raises(CapacityUnavailable): prepare(repo.path)
    assert target.read_bytes()==b''


@pytest.mark.skipif(os.name!='posix',reason='POSIX owner/mode API; Windows needs ACL acceptance')
def test_gate_requires_private_mode(tmp_path):
    repo=Repository.create(tmp_path/'history.sqlite3')
    gate=prepare(repo.path)
    gate.chmod(0o644)
    try:
        with pytest.raises(CapacityUnavailable): prepare(repo.path)
    finally:
        gate.chmod(0o600)


def test_capacity_is_bound_to_repository_and_limits_reject_bool(tmp_path):
    a=Repository.create(tmp_path/'a.sqlite3')
    b=Repository.create(tmp_path/'b.sqlite3')
    with claim(prepare(a.path)),claim(prepare(b.path)):
        pass
    with pytest.raises(ValueError): parser_process.Limits(seconds=True)
