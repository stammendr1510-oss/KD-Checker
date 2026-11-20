"""Isolated HTTP authorization boundary; never imports or mounts local app.py.

Verifier/backend are trusted server adapters. No runnable production provider is
bundled. The prototype protects dispatch, not the internals of an arbitrary adapter.
"""
from typing import Protocol
from urllib.parse import urlsplit
import secrets
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, Field
from fastapi.middleware.trustedhost import TrustedHostMiddleware

from design.access_policy import Action, Principal, Resource, Role, is_allowed
from secure_sessions import Identity, Sessions, digest
from body_budget import BodyBudget

COOKIE = '__Host-kd_session'
WORKBENCH_ASSETS = {'/workbench': ('workbench.html', 'text/html'),
                    '/workbench.js': ('workbench.js', 'text/javascript'),
                    '/workbench.css': ('workbench.css', 'text/css')}
CONNECTION_PATH = '/api/connections/check'
EXTRA_ROUTES = {('POST', CONNECTION_PATH): Action.CHECK,
                ('GET', '/api/config/history'): Action.READ}
MAX_CONNECTION_BODY = 512 * 1024
# Every current local API operation, reviewed against PR #38.
ROUTES = {
    ('GET', '/api/config'): Action.READ,
    ('PUT', '/api/config'): Action.MANAGE_CONFIG,
    ('POST', '/api/check'): Action.CHECK,
    ('GET', '/api/runs'): Action.READ,
    ('GET', '/api/runs/{run_id}'): Action.READ,
    ('GET', '/api/runs/{run_id}/compare/{previous_run_id}'): Action.READ,
    ('GET', '/api/runs/{run_id}/checklist-compare/{previous_run_id}'): Action.READ,
    ('GET', '/api/runs/{run_id}/checklist-compare/{previous_run_id}/csv'): Action.READ,
    ('GET', '/api/runs/{run_id}/checklist-compare/{previous_run_id}/xlsx'): Action.READ,
    ('GET', '/api/runs/{run_id}/csv'): Action.READ,
    ('PATCH', '/api/runs/{run_id}/findings/{finding_id}'): Action.EDIT_FINDING,
    ('GET', '/api/runs/{run_id}/findings/{finding_id}/history'): Action.READ,
    ('GET', '/api/runs/{run_id}/xlsx'): Action.READ,
}


class Verifier(Protocol):
    def verify(self, credential: str) -> Identity | None: ...


class Backend(Protocol):
    # All lookups must use current trusted server records; none are request claims.
    def role(self, identity: Identity) -> Role | None: ...
    def run_scope(self, run_id: int) -> tuple[str, str] | None: ...
    def finding_run(self, finding_id: int) -> int | None: ...
    async def dispatch(self, method: str, path: str, identity: Identity, request: Request): ...


class Login(BaseModel):
    model_config = ConfigDict(extra='forbid')
    credential: str = Field(min_length=1, max_length=4096, repr=False)


def create_secure_app(*, verifier: Verifier, backend: Backend, sessions: Sessions, origin: str,
                      body_limit=105*1024*1024, body_seconds=30):
    url = urlsplit(origin)
    if (url.scheme != 'https' or not url.hostname or url.username or url.password
            or url.path or url.query or url.fragment or origin != f'https://{url.netloc}'):
        raise ValueError('An exact HTTPS origin without path is required')
    if verifier is None or backend is None or not isinstance(sessions, Sessions):
        raise ValueError('Explicit trusted adapters and session store required')
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=[url.hostname])
    app.add_middleware(BodyBudget, limit=body_limit, seconds=body_seconds,
                       path_limits={**{path: 0 for path in WORKBENCH_ASSETS}, CONNECTION_PATH: MAX_CONNECTION_BODY,
                                    '/auth/login':8192, '/api/config':65536})

    @app.exception_handler(RequestValidationError)
    async def invalid_input(request, exc):
        # Validation errors must not echo credentials or arbitrary request fields.
        return Response(status_code=422)

    def csrf_origin(request, record=None):
        if request.headers.get('origin') != origin:
            raise HTTPException(403, 'Request denied')
        if record is not None:
            csrf = request.headers.get('x-csrf-token', '')
            if len(csrf) > 200 or not secrets.compare_digest(digest(csrf), record.csrf_hash):
                raise HTTPException(403, 'Request denied')

    @app.middleware('http')
    async def boundary(request, call_next):
        # HTTPS is observed from trusted ASGI transport, never a user header.
        if request.url.scheme != 'https':
            return Response(status_code=400)
        token = request.cookies.get(COOKIE)
        record = sessions.resolve(token)
        public_asset = request.method == 'GET' and request.url.path in WORKBENCH_ASSETS
        if request.url.path != '/auth/login' and not public_asset and record is None:
            return Response(status_code=401)
        if any(h in request.headers for h in ('x-user-id', 'x-role', 'x-organization-id', 'x-project-id')):
            return Response(status_code=403)
        try:
            response = await call_next(request)
        except Exception:
            return Response(status_code=503)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Content-Security-Policy'] = "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; base-uri 'none'; object-src 'none'; frame-ancestors 'none'; form-action 'self'"
        return response

    @app.get('/workbench')
    @app.get('/workbench.js')
    @app.get('/workbench.css')
    def workbench(request: Request):
        filename, media_type = WORKBENCH_ASSETS[request.url.path]
        content = (Path(__file__).parent / 'secure_static' / filename).read_bytes()
        return Response(content, media_type=media_type)

    @app.get('/auth/context')
    def current_context(request: Request):
        record = sessions.resolve(request.cookies.get(COOKIE))
        if record is None:
            raise HTTPException(401, 'Authentication required')
        identity = record.identity
        try:
            role = backend.role(identity)
        except Exception:
            raise HTTPException(503, 'Authorization unavailable') from None
        if not isinstance(role, Role):
            raise HTTPException(403, 'Request denied')
        return {'subject':identity.subject, 'organization':identity.organization,
                'project':identity.project, 'role':role.value}

    @app.post('/auth/login')
    def login(data: Login, request: Request, response: Response):
        csrf_origin(request)
        try:
            identity = verifier.verify(data.credential)
            if not isinstance(identity, Identity) or not isinstance(backend.role(identity), Role):
                raise HTTPException(401, 'Authentication required')
            token, csrf = sessions.issue(identity)
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(503, 'Authentication unavailable') from None
        # Reauthentication rotates, and revokes the caller's old session.
        sessions.revoke(request.cookies.get(COOKIE))
        response.set_cookie(COOKIE, token, secure=True, httponly=True, samesite='strict',
                            path='/', max_age=int(sessions.ttl))
        return {'csrf_token': csrf}

    @app.post('/auth/logout')
    def logout(request: Request, response: Response):
        token = request.cookies.get(COOKIE)
        record = sessions.resolve(token)
        if record is None:
            raise HTTPException(401, 'Authentication required')
        csrf_origin(request, record)
        sessions.revoke(token)
        response.delete_cookie(COOKIE, path='/', secure=True, httponly=True, samesite='strict')
        return {'ok': True}

    def endpoint(method, path, action):
        async def guarded(request: Request):
            token = request.cookies.get(COOKIE)
            record = sessions.resolve(token)
            if record is None:
                raise HTTPException(401, 'Authentication required')
            identity = record.identity
            try:
                role = backend.role(identity)
            except Exception:
                raise HTTPException(503, 'Authorization unavailable') from None
            principal = Principal(identity.subject, identity.organization, role, True)
            if not is_allowed(principal, action, Resource(identity.organization)):
                raise HTTPException(403, 'Request denied')
            if method not in ('GET', 'HEAD'):
                csrf_origin(request, record)
                if path == CONNECTION_PATH:
                    if request.headers.get('content-type', '').split(';')[0].lower() != 'application/json':
                        raise HTTPException(415, 'JSON required')
                    raw = bytearray()
                    async for chunk in request.stream():
                        if len(raw) + len(chunk) > MAX_CONNECTION_BODY:
                            raise HTTPException(413, 'Input budget exceeded')
                        raw.extend(chunk)
                    request.state.connection_input = bytes(raw)
                elif request.headers.get('content-type', '').split(';')[0] == 'application/json':
                    try:
                        data = await request.json()
                    except ValueError:
                        raise HTTPException(400, 'Invalid request') from None
                    if isinstance(data, dict) and any(key in data for key in ('user_id', 'role', 'organization_id', 'project_id')):
                        raise HTTPException(403, 'Request denied')
            if any(key in request.query_params for key in ('user_id', 'role', 'organization_id', 'project_id')):
                raise HTTPException(403, 'Request denied')
            identifiers = {}
            for name in ('run_id', 'previous_run_id', 'finding_id'):
                if name in request.path_params:
                    try:
                        identifiers[name] = int(request.path_params[name])
                        if identifiers[name] <= 0:
                            raise ValueError()
                    except ValueError:
                        raise HTTPException(404, 'Not found') from None
            for name in ('run_id', 'previous_run_id'):
                if name in identifiers:
                    if backend.run_scope(identifiers[name]) != (identity.organization, identity.project):
                        raise HTTPException(404, 'Not found')
            if 'finding_id' in identifiers and backend.finding_run(identifiers['finding_id']) != identifiers['run_id']:
                raise HTTPException(404, 'Not found')
            # Backend must also scope list/create/config and transact any writes.
            result = await backend.dispatch(method, path, identity, request)
            sessions.touch(token)
            return result
        return guarded

    for (method, path), action in (ROUTES | EXTRA_ROUTES).items():
        app.add_api_route(path, endpoint(method, path, action), methods=[method])
    return app
