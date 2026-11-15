"""Executable acceptance launcher; no production files, users, or LAN listener."""
import json
from pathlib import Path
import shutil
import socket
import ssl
import subprocess
import threading
import time

import httpx
import pytest
import uvicorn

from synthetic_stand import (KIND, create_new_stand, main, run_acceptance,
                             serve_new, source_fingerprint)


def test_one_command_acceptance_produces_explicit_limited_receipt(tmp_path, capsys):
    root = tmp_path / 'acceptance'
    assert main(['accept', '--synthetic-only', '--workspace', str(root)]) == 0
    receipt = json.loads((root / 'acceptance-result.json').read_text(encoding='utf-8'))
    assert receipt['kind'] == KIND and receipt['status'] == 'synthetic_pass'
    assert receipt['source_fingerprint'] == source_fingerprint()
    assert receipt['production_ready'] is False and receipt['ci'] == 'not_run'
    assert receipt['tls_handshake'] == 'not_run_ASGI_test_transport'
    assert receipt['two_windows_pc'] == 'not_run'
    assert {'decision_and_actor_audit', 'project_and_organization_isolation',
            'backup_restore_integrity_and_audit', 'restored_sessions_start_empty'} <= set(receipt['checks'])
    encoded = (root / 'acceptance-result.json').read_text(encoding='utf-8')
    assert 'csrf_token' not in encoded and '__Host-kd_session' not in encoded
    assert 'synthetic-engineer' not in encoded
    assert 'synthetic-engineer' not in capsys.readouterr().out
    before = (root / 'history.sqlite3').read_bytes()
    with pytest.raises(FileExistsError):
        run_acceptance(root, synthetic_only=True)
    assert (root / 'history.sqlite3').read_bytes() == before


@pytest.mark.parametrize('port', [0, 80, True, 65536, '8766'])
def test_invalid_configuration_does_not_create_workspace(tmp_path, port):
    root = tmp_path / 'invalid'
    with pytest.raises(ValueError):
        create_new_stand(root, synthetic_only=True, port=port)
    assert not root.exists()


def test_explicit_synthetic_acknowledgement_and_valid_tls_required(tmp_path):
    root = tmp_path / 'stand'
    with pytest.raises(ValueError):
        create_new_stand(root)
    with pytest.raises((OSError, ssl.SSLError)):
        serve_new(root, synthetic_only=True, certfile=tmp_path/'missing-cert', keyfile=tmp_path/'missing-key')
    assert not root.exists()
    with pytest.raises(SystemExit):
        main(['serve', '--synthetic-only', '--workspace', str(root), '--certfile', str(tmp_path/'cert'),
              '--keyfile', str(tmp_path/'key'), '--host', '0.0.0.0'])


def test_parser_preflight_failure_refuses_stand_before_creation(tmp_path, monkeypatch):
    import synthetic_stand
    def fail(*args):
        raise RuntimeError('Synthetic preflight failure')
    monkeypatch.setattr(synthetic_stand, 'run_check', fail)
    root = tmp_path/'stand'
    with pytest.raises(RuntimeError):
        create_new_stand(root, synthetic_only=True)
    assert not root.exists()


@pytest.fixture
def tls_pair(tmp_path):
    openssl = shutil.which('openssl')
    if openssl is None:
        pytest.skip('openssl not installed; real TLS certificate fixture not exercised')
    cert, key = tmp_path/'test-cert.pem', tmp_path/'test-key.pem'
    subprocess.run([openssl, 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
                    '-keyout', str(key), '-out', str(cert), '-days', '1',
                    '-subj', '/CN=127.0.0.1', '-addext', 'subjectAltName=IP:127.0.0.1'],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)
    return cert, key


def test_serve_keeps_loopback_tls_and_single_worker_despite_environment(tmp_path, tls_pair, monkeypatch):
    cert, key = tls_pair
    calls = []
    monkeypatch.setenv('WEB_CONCURRENCY', '99')
    monkeypatch.setenv('UVICORN_HOST', '0.0.0.0')
    monkeypatch.setattr(uvicorn, 'run', lambda app, **options: calls.append(options))
    serve_new(tmp_path/'serve-stand', synthetic_only=True, certfile=cert, keyfile=key)
    assert len(calls) == 1
    options = calls[0]
    assert options['host'] == '127.0.0.1' and options['workers'] == 1
    assert options['ssl_certfile'] == str(cert) and options['ssl_keyfile'] == str(key)
    assert options['proxy_headers'] is False and options['access_log'] is False


def test_trusted_test_certificate_real_loopback_https(tmp_path, tls_pair):
    cert, key = tls_pair
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    stand = create_new_stand(tmp_path/'tls-stand', synthetic_only=True, port=port)
    config = uvicorn.Config(stand.app, host='127.0.0.1', port=port, log_level='error',
                            access_log=False, proxy_headers=False, ssl_certfile=str(cert),
                            ssl_keyfile=str(key), ssl_keyfile_password='')
    server = uvicorn.Server(config)
    thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    thread.start()
    try:
        deadline = time.monotonic()+10
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(.02)
        assert server.started
        with httpx.Client(base_url=stand.origin, trust_env=False, timeout=5) as untrusted:
            with pytest.raises(httpx.ConnectError):
                untrusted.get('/workbench')
        trusted = ssl.create_default_context(cafile=str(cert))
        assert trusted.verify_mode == ssl.CERT_REQUIRED and trusted.check_hostname
        with httpx.Client(base_url=stand.origin, verify=trusted, trust_env=False, timeout=10) as client:
            assert client.get('/workbench').status_code == 200
            assert client.get('/api/runs').status_code == 401
            response = client.post('/auth/login', json={'credential':'synthetic-engineer'},
                                   headers={'Origin':stand.origin})
            assert response.status_code == 200
            assert client.get('/auth/context').json()['project'] == 'synthetic-project-a'
            assert client.get('/api/runs').json() == []
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()
        assert not thread.is_alive()
