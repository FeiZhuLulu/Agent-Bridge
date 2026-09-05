import asyncio
import types
import uuid

import pytest

from agent_bridge.registry import Registry
from agent_bridge.server import dispatch_task


async def test_concurrent_retry_runs_once_and_replays_original_selection(bridge_home, tmp_path):
    registry = Registry.create(bridge_home)
    await registry.start()
    request_id = str(uuid.uuid4())
    args = dict(agent="fake", message="hello", cwd=str(tmp_path), model="first", request_id=request_id)
    try:
        first, retry = await asyncio.gather(registry.dispatch_task(**args), registry.dispatch_task(**args))
        assert first["reused"] is False
        assert retry == {**first, "reused": True}
        assert len(registry.tasks) == 1
        await registry.wait_task(first["task_id"], timeout_sec=5)
        followup = await registry.dispatch_task(
            agent="fake", message="next", cwd=str(tmp_path), session_id=first["session_id"], model="second"
        )
        await registry.wait_task(followup["task_id"], timeout_sec=5)
        assert await registry.dispatch_task(**args) == retry
        assert registry.sessions[first["session_id"]].model == "second"
        assert len(registry.tasks) == 2
    finally:
        await registry.stop()


@pytest.mark.parametrize("change", [
    {"message": "different"}, {"model": "different"}, {"effort": "high"},
    {"title": "different"}, {"user_requested": True}, {"session_id": "missing"},
])
async def test_changed_request_rejected_without_mutation(bridge_home, tmp_path, change):
    registry = Registry.create(bridge_home)
    await registry.start()
    args = dict(agent="fake", message="hello", cwd=str(tmp_path), request_id=str(uuid.uuid4()))
    try:
        first = await registry.dispatch_task(**args)
        with pytest.raises(ValueError, match="different dispatch request"):
            await registry.dispatch_task(**{**args, **change})
        assert len(registry.tasks) == len(registry.sessions) == len(registry._requests) == 1
        assert registry.sessions[first["session_id"]].model is None
    finally:
        await registry.stop()


async def test_busy_followup_retry_reuses_task_and_new_id_is_rejected(bridge_home, tmp_path):
    registry = Registry.create(bridge_home)
    await registry.start()
    try:
        first = await registry.dispatch_task(agent="fake", message="one", cwd=str(tmp_path))
        await registry.wait_task(first["task_id"], timeout_sec=5)
        args = dict(agent="fake", message="two", cwd=str(tmp_path),
                    session_id=first["session_id"], request_id=str(uuid.uuid4()))
        followup = await registry.dispatch_task(**args)
        assert (await registry.dispatch_task(**args))["task_id"] == followup["task_id"]
        with pytest.raises(RuntimeError, match="busy"):
            await registry.dispatch_task(**{**args, "request_id": str(uuid.uuid4())})
        assert len(registry._requests) == 1
    finally:
        await registry.stop()


async def test_pruning_drops_binding(bridge_home, tmp_path, monkeypatch):
    registry = Registry.create(bridge_home)
    await registry.start()
    monkeypatch.setattr("agent_bridge.registry.TASK_KEEP_PER_SESSION", 1)
    args = dict(agent="fake", message="one", cwd=str(tmp_path), request_id=str(uuid.uuid4()))
    try:
        first = await registry.dispatch_task(**args)
        await registry.wait_task(first["task_id"], timeout_sec=5)
        for message in ("two", "three"):
            next_task = await registry.dispatch_task(
                agent="fake", message=message, cwd=str(tmp_path), session_id=first["session_id"]
            )
            await registry.wait_task(next_task["task_id"], timeout_sec=5)
        registry._prune()
        assert first["task_id"] not in registry.tasks
        assert args["request_id"] not in registry._requests
        replay = await registry.dispatch_task(**args)
        assert replay["reused"] is False
        assert replay["task_id"] != first["task_id"]
    finally:
        await registry.stop()


async def test_uuid_validation_and_canonicalization(bridge_home, tmp_path):
    registry = Registry.create(bridge_home)
    await registry.start()
    args = dict(agent="fake", message="hello", cwd=str(tmp_path))
    try:
        with pytest.raises(ValueError, match="UUID"):
            await registry.dispatch_task(**args, request_id="invalid")
        assert not registry.tasks and not registry._requests
        request_id = uuid.uuid4()
        first = await registry.dispatch_task(**args, request_id=request_id.hex.upper())
        retry = await registry.dispatch_task(**args, request_id=str(request_id))
        assert retry == {**first, "reused": True}
        assert first["request_id"] == str(request_id)
    finally:
        await registry.stop()


async def test_tool_forwards_request_id_and_reports_payload_conflict(bridge_home, tmp_path):
    registry = Registry.create(bridge_home)
    await registry.start()
    ctx = types.SimpleNamespace(request_context=types.SimpleNamespace(lifespan_context=registry))
    args = dict(ctx=ctx, agent="fake", message="hello", cwd=str(tmp_path), request_id=str(uuid.uuid4()))
    try:
        first = await dispatch_task(**args)
        assert first["ok"] is True
        assert await dispatch_task(**args) == {**first, "reused": True}
        conflict = await dispatch_task(**{**args, "message": "different"})
        assert conflict["ok"] is False
        assert conflict["error_type"] == "ValueError"
    finally:
        await registry.stop()
