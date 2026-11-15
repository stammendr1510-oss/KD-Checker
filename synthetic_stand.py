"""Explicit synthetic-only acceptance/loopback launcher. Never opens existing data."""
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from importlib.metadata import version
import json
import os
from pathlib import Path
import sqlite3
import ssl
import sys

from durable_sessions import DurableSessions
from parser_process import run_check
from scoped_history import Repository, Scope
from secure_api import COOKIE, create_secure_app
from secure_backend import ScopedBackend
from secure_sessions import Identity

KIND = 'kd-checker-synthetic-acceptance-v1'
SCOPE = Scope('synthetic-org-a', 'synthetic-project-a')
CSV = 'позиция;обозначение;наименование\n1;SYNTH.002;Synthetic A\n1;SYNTH.003;Synthetic B\n'.encode('utf-8')
# Public fixtures, NOT secret passwords. This verifier must never protect real data.
FIXTURES = {
    'synthetic-engineer': ('synthetic-engineer', 'synthetic-org-a', 'synthetic-project-a', 'engineer'),
    'synthetic-viewer': ('synthetic-viewer', 'synthetic-org-a', 'synthetic-project-a', 'viewer'),
    'synthetic-admin': ('synthetic-admin', 'synthetic-org-a', 'synthetic-project-a', 'admin'),
    'synthetic-other-project': ('synthetic-other-project', 'synthetic-org-a', 'synthetic-project-b', 'engineer'),
    'synthetic-other-org': ('synthetic-other-org', 'synthetic-org-b', 'synthetic-project-a', 'engineer'),
}


class SyntheticVerifier:
    def verify(self, credential):
        row = FIXTURES.get(credential)
        return Identity(*row[:3]) if row else None


def _origin(port):
    if type(port) is not int or not 1024 <= port <= 65535:
        raise ValueError('Explicit unprivileged port required')
    return f'https://127.0.0.1:{port}'


def _json_new(path, payload):
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2, allow_nan=False)


def source_fingerprint():
    root = Path(__file__).resolve().parent
    paths = list(root.glob('*.py')) + list((root/'design').rglob('*.py'))
    paths += [p for p in (root/'secure_static').iterdir() if p.is_file()]
    paths += [root/'config.json', root/'requirements.txt']
    names = sorted(p.relative_to(root).as_posix() for p in paths)
    digest = hashlib.sha256()
    for name in names:
        content = (root / name).read_bytes()
        digest.update(name.encode() + b'\0' + str(len(content)).encode() + b'\0' + content)
    return digest.hexdigest()


@dataclass
class Stand:
    workspace: Path
    repository: Repository
    sessions: DurableSessions
    app: object
    origin: str


def create_new_stand(workspace, *, synthetic_only=False, port=8766):
    if synthetic_only is not True:
        raise ValueError('Explicit synthetic-only acknowledgement required')
    origin = _origin(port)
    root = Path(workspace).absolute()
    if root.exists() or root.is_symlink():
        raise FileExistsError(root)
    if not root.parent.is_dir():
        raise FileNotFoundError(root.parent)
    # Fail before creating a workspace if OS parser budgets cannot be applied.
    config = json.loads((Path(__file__).parent / 'config.json').read_text(encoding='utf-8'))
    run_check([('SYNTH.001.csv', CSV)], config)
    root.mkdir(mode=0o700)
    # Partial synthetic workspaces are retained for diagnosis, never auto-replaced.
    repository = Repository.create(root / 'history.sqlite3')
    for subject, organization, project, role in FIXTURES.values():
        repository.provision(subject, Scope(organization, project), role)
    sessions = DurableSessions.create(root / 'sessions.sqlite3')
    app = create_secure_app(verifier=SyntheticVerifier(), backend=ScopedBackend(repository),
                            sessions=sessions, origin=origin)
    _json_new(root / 'stand-manifest.json', {'kind': KIND, 'source_fingerprint': source_fingerprint(),
        'created_at': datetime.now(timezone.utc).isoformat(), 'bind': '127.0.0.1',
        'origin': origin, 'data': 'synthetic only', 'production_ready': False})
    fd = os.open(root / 'SYNTH.001.csv', os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(CSV)
    return Stand(root, repository, sessions, app, origin)


def validate_tls(certfile, keyfile):
    # Empty password suppresses OpenSSL's interactive encrypted-key password prompt.
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(certfile=str(certfile), keyfile=str(keyfile), password='')
    return context


def serve_new(workspace, *, certfile, keyfile, synthetic_only=False, port=8766):
    if synthetic_only is not True:
        raise ValueError('Synthetic-only acknowledgement required')
    _origin(port)
    if Path(workspace).exists() or Path(workspace).is_symlink():
        raise FileExistsError(workspace)
    validate_tls(certfile, keyfile)
    stand = create_new_stand(workspace, synthetic_only=True, port=port)
    import uvicorn
    # No host/reload/proxy overrides and no HTTP fallback. One server process.
    uvicorn.run(stand.app, host='127.0.0.1', port=port, workers=1, access_log=False, log_level='error',
                proxy_headers=False, ssl_certfile=str(certfile), ssl_keyfile=str(keyfile),
                ssl_keyfile_password='', ssl_version=ssl.PROTOCOL_TLS_SERVER)


def _require(condition):
    if not condition:
        raise RuntimeError('Synthetic acceptance check failed')


def run_acceptance(workspace, *, synthetic_only=False):
    from fastapi.testclient import TestClient
    stand = create_new_stand(workspace, synthetic_only=synthetic_only)
    fingerprint = source_fingerprint()
    checks = []
    def request(client, method, path, *, expected=200, **kwargs):
        response = client.request(method, path, **kwargs)
        _require(response.status_code == expected)
        return response
    def login(client, ticket):
        response = request(client, 'POST', '/auth/login', json={'credential': ticket},
                           headers={'Origin': stand.origin})
        return {'Origin': stand.origin, 'X-CSRF-Token': response.json()['csrf_token']}
    receipt = {'kind': KIND, 'source_fingerprint': fingerprint, 'python': sys.version.split()[0],
               'platform': sys.platform, 'production_ready': False, 'ci': 'not_run',
               'dependencies': {name: version(name) for name in
                                ('fastapi','starlette','uvicorn','PyMuPDF','openpyxl','ezdxf')},
               'tls_handshake': 'not_run_ASGI_test_transport', 'two_windows_pc': 'not_run',
               'worker_preflight': 'positive_path_passed', 'filesystem_network_sandbox': 'not_provided',
               'enterprise_identity_provider': 'not_configured',
               'checks': checks, 'status': 'failed'}
    try:
        with TestClient(stand.app, base_url=stand.origin) as client:
            request(client, 'GET', '/api/runs', expected=401)
            headers = login(client, 'synthetic-engineer')
            def upload():
                return request(client, 'POST', '/api/check', headers=headers,
                    files={'files': ('SYNTH.001.csv', CSV, 'text/csv')},
                    data={'revision': 'synthetic-rev-1'}).json()
            first = upload()
            _require(bool(first['issues']) and bool(first['finding_states']))
            identifier = first['id']
            finding = first['finding_states'][0]['id']
            decision = f'/api/runs/{identifier}/findings/{finding}'
            request(client, 'PATCH', decision, headers=headers,
                    json={'status': 'resolved', 'comment': 'Synthetic acceptance decision', 'expected_version': 0})
            audit = request(client, 'GET', decision + '/history').json()
            _require(len(audit) == 1 and audit[0]['actor'] == 'synthetic-engineer')
            checks.extend(['authenticated_engine_check', 'decision_and_actor_audit'])
            second = upload()
            _require(all(state['status'] == 'new' for state in second['finding_states']))
            comparison = request(client, 'GET', f"/api/runs/{second['id']}/compare/{identifier}").json()
            _require(comparison['summary']['persisting'] > 0)
            _require(bool(request(client, 'GET', f'/api/runs/{identifier}/csv').content))
            _require(bool(request(client, 'GET', f'/api/runs/{identifier}/xlsx').content))
            checks.extend(['recheck_no_decision_transfer', 'compare_and_exports'])
            admin = login(client, 'synthetic-admin')
            config_response = request(client, 'GET', '/api/config')
            settings = config_response.json()
            settings['require_designation_in_filename'] = False
            request(client, 'PUT', '/api/config', headers=admin,
                    json={'settings':settings, 'expected_version':int(config_response.headers['X-Config-Version'])})
            config_audit = request(client, 'GET', '/api/config/history').json()
            _require(len(config_audit) == 1 and config_audit[0]['actor'] == 'synthetic-admin')
            row = {'wire':'SYNTH-W1', 'cable':'', 'source':{'device':'SYNTH-X1','terminal':'1'},
                   'target':{'device':'SYNTH-X1','terminal':'1'}, 'document':'SYNTH.001.json', 'location':'record 1'}
            connections = request(client, 'POST', '/api/connections/check', headers=admin,
                json={'schema_version':1, 'project':SCOPE.project, 'revision':'synthetic-rev-1',
                      'records':[row, dict(row,location='record 2')]}).json()
            _require({i['code'] for i in connections['issues']} == {'KD-CON-101','KD-CON-102'})
            _require(all(i['severity'] == 'info' for i in connections['issues']))
            checks.extend(['project_config_admin_audit', 'structured_connections_saved'])
            viewer = login(client, 'synthetic-viewer')
            request(client, 'PATCH', decision, expected=403, headers=viewer,
                    json={'status': 'new', 'comment': '', 'expected_version': 1})
            for ticket in ('synthetic-other-project', 'synthetic-other-org'):
                login(client, ticket)
                _require(request(client, 'GET', '/api/runs').json() == [])
                _require(request(client, 'GET', '/api/config/history').json() == [])
                for suffix in ('', '/csv', '/xlsx', f'/findings/{finding}/history'):
                    request(client, 'GET', f'/api/runs/{identifier}{suffix}', expected=404)
            checks.extend(['viewer_write_denied', 'project_and_organization_isolation'])
            headers = login(client, 'synthetic-engineer')
            old_token = client.cookies.get(COOKIE)
            request(client, 'POST', '/auth/logout', headers=headers)
            client.cookies.set(COOKIE, old_token)
            request(client, 'GET', '/api/runs', expected=401)
            checks.append('logout_replay_denied')
        # All clients stopped. Restore only into NEW files; sessions are never cloned.
        restored_path = stand.workspace / 'restored-history.sqlite3'
        fd = os.open(restored_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        with closing(sqlite3.connect(stand.repository.path.as_uri() + '?mode=ro', uri=True)) as source:
            with closing(sqlite3.connect(restored_path)) as target:
                source.backup(target)
        restored = Repository(restored_path)
        with restored.transaction() as con:
            _require(con.execute('PRAGMA integrity_check').fetchone()[0] == 'ok')
            _require(not con.execute('PRAGMA foreign_key_check').fetchall())
        fresh_sessions = DurableSessions.create(stand.workspace / 'restored-sessions.sqlite3')
        restored_app = create_secure_app(verifier=SyntheticVerifier(), backend=ScopedBackend(restored),
                                         sessions=fresh_sessions, origin=stand.origin)
        with TestClient(restored_app, base_url=stand.origin) as client:
            client.cookies.set(COOKIE, old_token)
            request(client, 'GET', f'/api/runs/{identifier}', expected=401)
            client.cookies.clear()
            login(client, 'synthetic-engineer')
            saved = request(client, 'GET', f'/api/runs/{identifier}').json()
            _require(saved['finding_states'][0]['comment'] == 'Synthetic acceptance decision')
            _require(saved['finding_states'][0]['version'] == 1)
            restored_audit = request(client, 'GET', decision + '/history').json()
            _require(restored_audit == audit)
            _require(request(client, 'GET', '/api/config/history').json() == config_audit)
            _require(request(client, 'GET', f"/api/runs/{connections['id']}").json()['issues'] == connections['issues'])
            request(client, 'GET', f'/api/runs/{identifier}/xlsx')
        checks.extend(['backup_restore_integrity_and_audit', 'restored_sessions_start_empty'])
        _require(fingerprint == source_fingerprint())
        receipt['status'] = 'synthetic_pass'
    finally:
        # No cookies, tickets, hashes, exception text or document contents in receipt.
        _json_new(stand.workspace / 'acceptance-result.json', receipt)
    return receipt


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description='KD Checker: только синтетический стенд, без LAN и production')
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('accept', 'serve'):
        command = commands.add_parser(name)
        command.add_argument('--synthetic-only', action='store_true', required=True)
        command.add_argument('--workspace', type=Path, required=True)
        if name == 'serve':
            command.add_argument('--certfile', type=Path, required=True)
            command.add_argument('--keyfile', type=Path, required=True)
            command.add_argument('--port', type=int, default=8766)
    args = parser.parse_args(argv)
    try:
        if args.command == 'accept':
            result = run_acceptance(args.workspace, synthetic_only=args.synthetic_only)
            print('Синтетическая приёмка выполнена. CI, TLS и два Windows ПК этим отчётом не подтверждаются.')
            return 0 if result['status'] == 'synthetic_pass' else 1
        serve_new(args.workspace, certfile=args.certfile, keyfile=args.keyfile,
                  synthetic_only=args.synthetic_only, port=args.port)
        return 0
    except (Exception, KeyboardInterrupt):
        print('Стенд остановлен или проверка не пройдена. Рабочие базы не переключались. Существующий каталог не заменяйте.')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
