import asyncio
import contextlib
import time
import types

import pytest

from agent_bridge.registry import Registry
from agent_bridge.server import INSTRUCTIONS, _error, _registry, list_sessions, mcp


def test_ten_tools_registered():
    names = sorted(mcp._tool_manager._tools)
    assert names == [
        "cancel_task",
        "check_task",
        "dispatch_task",
        "end_session",
        "get_result",
        "get_transcript",
        "list_agents",
        "list_sessions",
        "set_preferences",
        "wait_task",
    ]


def test_handshake_instructions_carry_hard_rules():
    assert mcp.instructions == INSTRUCTIONS
    for phrase in (
        "dispatch_task.cwd",
        "wait_task",
        "coordinator.mode",
        "dispatch_enabled",
        "runtime_context",
        "cancel_task",
        "end_session",
    ):
        assert phrase in INSTRUCTIONS


def test_error_exposes_exception_type():
    assert _error(ValueError("bad cwd")) == {
        "ok": False,
        "error": "ValueError: bad cwd",
        "error_type": "ValueError",
    }


def test_registry_from_lifespan_context_touches_activity(bridge_home):
    registry = Registry.create(bridge_home)
    registry._last_activity = registry._last_activity - 1
    before = registry._last_activity
    ctx = types.SimpleNamespace(
        request_context=types.SimpleNamespace(lifespan_context=registry)
    )
    got = _registry(ctx)
    assert got is registry
    assert registry._last_activity > before


@pytest.mark.asyncio
async def test_list_sessions_does_not_block_event_loop(bridge_home, monkeypatch):
    def slow_count() -> int:
        time.sleep(0.5)
        return 3

    monkeypatch.setattr("agent_bridge.registry.count_sibling_servers", slow_count)
    ticks = 0

    async def counter() -> None:
        nonlocal ticks
        while True:
            ticks += 1
            await asyncio.sleep(0.02)

    registry = Registry.create(bridge_home)
    ctx = types.SimpleNamespace(
        request_context=types.SimpleNamespace(lifespan_context=registry)
    )
    task = asyncio.create_task(counter())
    try:
        result = await list_sessions(ctx)
        assert result == {
            "ok": True,
            "scope": "current_instance",
            "other_live_instances": 3,
            "sessions": [],
        }
        assert ticks >= 10
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


def test_registry_rejects_dict_lifespan(bridge_home):
    registry = Registry.create(bridge_home)
    ctx = types.SimpleNamespace(
        request_context=types.SimpleNamespace(lifespan_context={"registry": registry})
    )
    with pytest.raises(RuntimeError, match="not available"):
        _registry(ctx)
