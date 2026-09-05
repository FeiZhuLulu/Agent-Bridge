import types

import pytest

from agent_bridge.registry import Registry
from agent_bridge.server import INSTRUCTIONS, _error, _registry, mcp


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


def test_registry_rejects_dict_lifespan(bridge_home):
    registry = Registry.create(bridge_home)
    ctx = types.SimpleNamespace(
        request_context=types.SimpleNamespace(lifespan_context={"registry": registry})
    )
    with pytest.raises(RuntimeError, match="not available"):
        _registry(ctx)
