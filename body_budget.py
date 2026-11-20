"""Bounded request admission before multipart/JSON parsing, no global monkeypatch."""
import asyncio
import tempfile
from fastapi.responses import Response


class BodyBudget:
    def __init__(self, app, *, limit=105*1024*1024, seconds=30, memory=256*1024, path_limits=None):
        if type(limit) is not int or limit <= 0 or seconds <= 0 or memory <= 0:
            raise ValueError('Invalid request budget')
        self.app,self.limit,self.seconds,self.memory = app,limit,seconds,memory
        self.path_limits = path_limits or {}

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope,receive,send)
        limit = min(self.limit,self.path_limits.get(scope['path'],self.limit))
        headers = dict(scope.get('headers',[]))
        declared = headers.get(b'content-length')
        if declared is not None:
            try:
                if int(declared) < 0:
                    raise ValueError()
                if int(declared) > limit:
                    return await Response(status_code=413)(scope,receive,send)
            except ValueError:
                return await Response(status_code=400)(scope,receive,send)
        total = 0
        deadline = asyncio.get_running_loop().time()+self.seconds
        # Closed on every admission/parse/response failure. No named persisted file.
        with tempfile.SpooledTemporaryFile(max_size=self.memory,mode='w+b') as buffered:
            while True:
                try:
                    remaining = deadline-asyncio.get_running_loop().time()
                    if remaining <= 0:
                        raise TimeoutError()
                    message = await asyncio.wait_for(receive(),timeout=remaining)
                except TimeoutError:
                    return await Response(status_code=408)(scope,receive,send)
                if message['type'] == 'http.disconnect':
                    return
                if message['type'] != 'http.request':
                    return await Response(status_code=400)(scope,receive,send)
                chunk = message.get('body',b'')
                total += len(chunk)
                if total > limit:
                    return await Response(status_code=413)(scope,receive,send)
                buffered.write(chunk)
                if not message.get('more_body',False):
                    break
            if declared is not None and total != int(declared):
                return await Response(status_code=400)(scope,receive,send)
            buffered.seek(0)
            exhausted = False
            async def replay():
                nonlocal exhausted
                if exhausted:
                    return await receive()
                chunk = buffered.read(65536)
                more = buffered.tell() < total
                exhausted = not more
                return {'type':'http.request','body':chunk,'more_body':more}
            await self.app(scope,replay,send)
