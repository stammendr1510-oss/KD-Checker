"""Synthetic verifier and dispatch spy; no enterprise login or live storage."""
import pytest
from fastapi.testclient import TestClient

from design.access_policy import Role
from secure_sessions import Identity, Sessions
from secure_api import COOKIE, ROUTES, create_secure_app

ORIGIN = 'https://kd.test'


class Verifier:
    def verify(self, credential):
        return Identity('synthetic-user', 'org-a', 'project-a') if credential == 'synthetic-ticket' else None


class Backend:
    current_role = Role.ENGINEER
    calls = None

    def __init__(self):
        self.calls = []

    def role(self, identity):
        return self.current_role

    def run_scope(self, run_id):
        return {1: ('org-a', 'project-a'), 2: ('org-a', 'project-b'),
                3: ('org-b', 'project-a')}.get(run_id)

    def finding_run(self, finding_id):
        return {10: 1, 20: 2}.get(finding_id)

    async def dispatch(self, method, path, identity, request):
        self.calls.append((method, path, identity))
        return {'synthetic': 'protected-data'}


@pytest.fixture
def setup():
    now = [100.0]
    sessions = Sessions(ttl=100, idle=30, clock=lambda: now[0])
    backend = Backend()
    app = create_secure_app(verifier=Verifier(), backend=backend, sessions=sessions, origin=ORIGIN)
    with TestClient(app, base_url=ORIGIN) as client:
        yield client, backend, sessions, now


def login(client):
    response = client.post('/auth/login', json={'credential': 'synthetic-ticket'}, headers={'origin': ORIGIN})
    assert response.status_code == 200
    return response.json()['csrf_token']


def path(template, **ids):
    return template.format(run_id=ids.get('run_id', 1), previous_run_id=ids.get('previous_run_id', 1),
                           finding_id=ids.get('finding_id', 10))


@pytest.mark.parametrize('method,template', ROUTES)
def test_all_local_api_operations_deny_guest_before_backend(setup, method, template):
    client, backend, _, _ = setup
    response = client.request(method, path(template), json={})
    assert response.status_code == 401
    assert backend.calls == []


@pytest.mark.parametrize('role', list(Role))
@pytest.mark.parametrize('method,template', ROUTES)
def test_server_role_matrix_on_every_route(setup, role, method, template):
    client, backend, _, _ = setup
    backend.current_role = role
    csrf = login(client)
    response = client.request(method, path(template), json={}, headers={'origin': ORIGIN, 'x-csrf-token': csrf})
    allowed = method == 'GET' or role == Role.ADMIN or (role == Role.ENGINEER and method != 'PUT')
    assert response.status_code == (200 if allowed else 403)
    assert bool(backend.calls) is allowed


@pytest.mark.parametrize('method,template', [key for key in ROUTES if '{run_id}' in key[1]])
@pytest.mark.parametrize('foreign', [2, 3, 999])
def test_foreign_project_org_and_missing_resources_are_indistinguishable(setup, method, template, foreign):
    client, backend, _, _ = setup
    csrf = login(client)
    response = client.request(method, path(template, run_id=foreign), json={},
                              headers={'origin': ORIGIN, 'x-csrf-token': csrf})
    assert response.status_code == 404
    assert response.json() == {'detail': 'Not found'}
    assert backend.calls == []


@pytest.mark.parametrize('template', [route for method, route in ROUTES if 'previous_run_id' in route])
def test_comparison_checks_second_run(setup, template):
    client, backend, _, _ = setup
    login(client)
    assert client.get(path(template, previous_run_id=2)).status_code == 404
    assert backend.calls == []


def test_finding_binding(setup):
    client, backend, _, _ = setup
    login(client)
    assert client.get('/api/runs/1/findings/20/history').status_code == 404
    assert backend.calls == []


@pytest.mark.parametrize('failure', ['absolute', 'idle', 'revoke', 'logout', 'membership'])
def test_expiry_revocation_logout_and_membership_rechecked(setup, failure):
    client, backend, sessions, now = setup
    csrf = login(client)
    old = client.cookies.get(COOKIE)
    if failure == 'absolute':
        now[0] += 100
    elif failure == 'idle':
        now[0] += 30
    elif failure == 'revoke':
        sessions.revoke(old)
    elif failure == 'logout':
        assert client.post('/auth/logout', headers={'origin': ORIGIN, 'x-csrf-token': csrf}).status_code == 200
        client.cookies.set(COOKIE, old)
    else:
        backend.current_role = None
    assert client.get('/api/runs').status_code == (403 if failure == 'membership' else 401)
    assert backend.calls == []


@pytest.mark.parametrize('origin,csrf', [(None, 'valid'), ('https://foreign.test', 'valid'), (ORIGIN, 'invalid')])
def test_csrf_and_origin_prevent_dispatch_and_logout(setup, origin, csrf):
    client, backend, sessions, _ = setup
    valid = login(client)
    token = client.cookies.get(COOKIE)
    headers = {'x-csrf-token': valid if csrf == 'valid' else csrf}
    if origin:
        headers['origin'] = origin
    assert client.post('/api/check', json={}, headers=headers).status_code == 403
    assert client.post('/auth/logout', headers=headers).status_code == 403
    assert sessions.resolve(token) is not None
    assert backend.calls == []


@pytest.mark.parametrize('field', ['user_id', 'role', 'organization_id', 'project_id'])
def test_client_identity_override_rejected(setup, field):
    client, backend, _, _ = setup
    csrf = login(client)
    assert client.post('/api/check', json={field: 'spoof'}, headers={'origin': ORIGIN, 'x-csrf-token': csrf}).status_code == 403
    assert client.get('/api/runs', params={field: 'spoof'}).status_code == 403
    assert client.get('/api/runs', headers={'x-' + field.replace('_', '-'): 'spoof'}).status_code == 403
    assert backend.calls == []


def test_cookie_rotation_and_validation_redaction(setup):
    client, _, sessions, _ = setup
    first = client.post('/auth/login', json={'credential': 'synthetic-ticket'}, headers={'origin': ORIGIN})
    cookie = first.headers['set-cookie']
    assert all(flag in cookie.lower() for flag in ('secure', 'httponly', 'samesite=strict', 'path=/'))
    old = client.cookies.get(COOKIE)
    login(client)
    assert client.cookies.get(COOKIE) != old
    assert sessions.resolve(old) is None
    secret = 'SYNTHETIC-PRIVATE-CREDENTIAL'
    response = client.post('/auth/login', json={'credential': secret, 'role': 'admin'}, headers={'origin': ORIGIN})
    assert response.status_code == 422 and secret not in response.text


def test_unknown_routes_methods_and_transport_do_not_dispatch(setup):
    client, backend, _, _ = setup
    login(client)
    assert client.get('/openapi.json').status_code == 404
    assert client.get('/api/unknown').status_code == 404
    assert client.delete('/api/runs/1').status_code == 405
    assert client.get('http://kd.test/api/runs').status_code == 400
    assert client.get('https://foreign.test/api/runs').status_code in (400, 401)
    assert backend.calls == []


def test_verifier_failure_is_redacted(setup):
    client, backend, _, _ = setup
    assert client.post('/auth/login', json={'credential': 'unknown'}, headers={'origin': ORIGIN}).status_code == 401
    assert client.post('/auth/login', json={'credential': 'synthetic-ticket'}).status_code == 403
    assert backend.calls == []


def test_inventory_matches_existing_app_without_mounting_it():
    import app as local
    from fastapi.routing import APIRoute
    routes = {(method, route.path) for route in local.app.routes if isinstance(route, APIRoute)
              and route.path.startswith('/api/') for method in route.methods}
    assert routes == set(ROUTES)


def test_sessions_are_individual_and_internal_tokens_are_hashed():
    store = Sessions(capacity=2)
    first, csrf = store.issue(Identity('a', 'org', 'project'))
    second, _ = store.issue(Identity('b', 'org', 'project'))
    assert first not in repr(store._records) and csrf not in repr(store._records)
    store.revoke(first)
    assert store.resolve(first) is None
    assert store.resolve(second).identity.subject == 'b'


def test_absolute_expiry_cannot_be_extended_by_activity(setup):
    client, backend, _, now = setup
    login(client)
    for _ in range(4):
        now[0] += 20
        assert client.get('/api/runs').status_code == 200
    # Last activity was only 20 seconds ago, within the 30 second idle limit.
    now[0] += 20
    assert client.get('/api/runs').status_code == 401
    assert len(backend.calls) == 4


def test_csrf_and_logout_are_bound_to_individual_session(setup):
    client, backend, sessions, _ = setup
    first_csrf = login(client)
    first_token = client.cookies.get(COOKIE)
    with TestClient(client.app, base_url=ORIGIN) as other:
        other_csrf = login(other)
        other_token = other.cookies.get(COOKIE)
        assert client.post('/api/check', json={}, headers={'origin': ORIGIN, 'x-csrf-token': other_csrf}).status_code == 403
        assert backend.calls == []
        assert client.post('/auth/logout', headers={'origin': ORIGIN, 'x-csrf-token': first_csrf}).status_code == 200
        assert sessions.resolve(first_token) is None
        assert sessions.resolve(other_token) is not None
        assert other.get('/api/runs').status_code == 200


@pytest.mark.parametrize('adapter', ['verify', 'role', 'dispatch'])
def test_trusted_adapter_exceptions_never_echo_private_details(setup, adapter, monkeypatch):
    client, backend, _, _ = setup
    secret = 'SYNTHETIC-PRIVATE-ADAPTER-DETAIL'
    def fail(*args):
        raise RuntimeError(secret)
    if adapter == 'verify':
        monkeypatch.setattr(Verifier, 'verify', fail)
        response = client.post('/auth/login', json={'credential': 'synthetic-ticket'}, headers={'origin': ORIGIN})
    else:
        login(client)
        if adapter == 'role':
            monkeypatch.setattr(backend, 'role', fail)
        else:
            async def async_fail(*args):
                raise RuntimeError(secret)
            monkeypatch.setattr(backend, 'dispatch', async_fail)
        response = client.get('/api/runs')
    assert response.status_code == 503
    assert secret not in response.text
