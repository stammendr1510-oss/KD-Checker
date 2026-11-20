"""Additional ASGI admission boundary tests on purely synthetic request bytes.

Tests BodyBudget directly; not a production reverse proxy or multipart parser.
"""
import asyncio
import tempfile

import pytest

from body_budget import BodyBudget


def exercise(chunks, *, path="/api/check", limit=16, route_limit=None, declared=None, memory=4):
    messages = [
        {"type": "http.request", "body": body, "more_body": index < len(chunks) - 1}
        for index, body in enumerate(chunks)
    ]
    if not messages:
        messages = [{"type": "http.request", "body": b"", "more_body": False}]
    outputs, received = [], []
    async def receive():
        return messages.pop(0) if messages else {"type": "http.disconnect"}
    async def send(message):
        outputs.append(message)
    async def app(scope, replay, send):
        payload = bytearray()
        while True:
            item = await replay()
            payload.extend(item.get("body", b""))
            if not item.get("more_body", False):
                break
        received.append(bytes(payload))
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})
    scope = {"type": "http", "path": path, "headers": []}
    if declared is not None:
        scope["headers"].append((b"content-length", declared))
    limiter = BodyBudget(
        app, limit=limit, seconds=5, memory=memory,
        path_limits=route_limit or {},
    )
    asyncio.run(limiter(scope, receive, send))
    return outputs[0]["status"], received


@pytest.mark.parametrize("chunks", [
    [b"x" * 16],
    [b"x" * 3, b"x" * 9, b"x" * 4],
    [b"", b"x" * 16],
])
def test_exact_global_limit_is_admitted_and_replayed_byte_for_byte(chunks):
    status, received = exercise(chunks, declared=b"16")
    assert status == 200
    assert received == [b"".join(chunks)]


@pytest.mark.parametrize("chunks", [
    [b"x" * 17],
    [b"x" * 16, b"x"],
    [b"x" * 5, b"x" * 12],
])
def test_incremental_overflow_rejected_before_downstream(chunks):
    status, received = exercise(chunks)
    assert status == 413
    assert received == []


def test_route_limit_is_stricter_than_global_limit():
    budget = {"/api/config": 8}
    assert exercise([b"abcd", b"efgh"], path="/api/config",
                    route_limit=budget)[0] == 200
    status, received = exercise([b"abcd", b"efghi"], path="/api/config",
                                route_limit=budget)
    assert status == 413
    assert received == []
    # The unrelated route retains the broader global budget.
    assert exercise([b"abcd", b"efghi"], path="/api/check",
                    route_limit=budget)[0] == 200


@pytest.mark.parametrize("declared", [b"-1", b"not-a-number", b"17"])
def test_bad_or_oversized_declared_length_is_rejected_before_read(declared):
    status, received = exercise([b"content"], declared=declared)
    assert status in (400, 413)
    assert received == []


def test_mismatch_between_declared_and_actual_length_rejected():
    status, received = exercise([b"synthetic"], declared=b"10")
    assert status == 400
    assert received == []


def test_zero_length_and_empty_body_can_be_handled_safely():
    status, received = exercise([], declared=b"0")
    assert status == 200 and received == [b""]


def test_spill_to_disk_is_closed_after_declared_mismatch(monkeypatch):
    buffers = []
    original = tempfile.SpooledTemporaryFile
    def tracking(*args, **kwargs):
        instance = original(*args, **kwargs)
        buffers.append(instance)
        return instance
    monkeypatch.setattr(tempfile, "SpooledTemporaryFile", tracking)
    status, received = exercise([b"x" * 10], declared=b"11", memory=3)
    assert status == 400 and received == []
    assert buffers and all(buffer.closed and buffer._rolled for buffer in buffers)


def test_spill_to_disk_is_closed_after_incremental_overflow(monkeypatch):
    buffers = []
    original = tempfile.SpooledTemporaryFile
    def tracking(*args, **kwargs):
        instance = original(*args, **kwargs)
        buffers.append(instance)
        return instance
    monkeypatch.setattr(tempfile, "SpooledTemporaryFile", tracking)
    status, received = exercise([b"x" * 10, b"y" * 7], memory=3)
    assert status == 413 and received == []
    assert buffers and all(buffer.closed and buffer._rolled for buffer in buffers)
