from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from agent_bridge.adapters.acp import (
    AcpAdapter,
    RpcTimeoutError,
    should_collect_tool_paths,
)
from agent_bridge.config import AgentConfig
from agent_bridge.models import Session


def test_read_tool_calls_do_not_count_as_files_changed():
    kinds: dict[str, str] = {}
    read_start = {"toolCallId": "t1", "kind": "read", "locations": [{"path": "a.py"}]}
    assert should_collect_tool_paths("ToolCallStart", read_start, kinds) is False
    # Progress updates usually omit kind; they inherit it from the start event.
    read_progress = {"toolCallId": "t1", "locations": [{"path": "a.py"}]}
    assert should_collect_tool_paths("ToolCallProgress", read_progress, kinds) is False


def test_edit_tool_calls_count_including_progress():
    kinds: dict[str, str] = {}
    edit_start = {"toolCallId": "t2", "kind": "edit", "locations": [{"path": "b.py"}]}
    assert should_collect_tool_paths("ToolCallStart", edit_start, kinds) is True
    edit_progress = {"toolCallId": "t2", "locations": [{"path": "b.py"}]}
    assert should_collect_tool_paths("ToolCallProgress", edit_progress, kinds) is True


def test_diff_content_counts_even_without_kind():
    update = {"toolCallId": "t3", "content": [{"type": "diff", "path": "c.py", "newText": "x"}]}
    assert should_collect_tool_paths("ToolCallUpdate", update, {}) is True


def test_non_tool_updates_never_count():
    chunk = {"content": {"type": "text", "text": "reading a.py"}, "path": "a.py"}
    assert should_collect_tool_paths("AgentMessageChunk", chunk, {}) is False
    assert should_collect_tool_paths("ToolCallStart", None, {}) is False


@pytest.mark.asyncio
async def test_rpc_timeout_raises_clear_error(tmp_path):
    adapter = AcpAdapter(
        AgentConfig(name="cursor", protocol="acp", command=["cursor-agent"]),
        tmp_path,
    )
    session = Session(session_id="sess_rpc", agent="cursor", cwd=str(tmp_path))
    with pytest.raises(RpcTimeoutError, match="session/new timed out"):
        await adapter._rpc(asyncio.sleep(30), "session/new", session, timeout=0.05)


@pytest.mark.asyncio
async def test_spawn_uses_session_cwd_not_agent_override(tmp_path, monkeypatch):
    configured = tmp_path / "configured"
    configured.mkdir()
    requested = tmp_path / "requested"
    requested.mkdir()
    adapter = AcpAdapter(
        AgentConfig(
            name="cursor", protocol="acp", command=["cursor-agent"], cwd=str(configured)
        ),
        tmp_path,
    )
    session = Session(session_id="sess_cwd", agent="cursor", cwd=str(requested))
    seen: dict[str, str] = {}

    async def fake_spawn(*args, **kwargs):
        seen["cwd"] = kwargs["cwd"]
        return SimpleNamespace(stdin=object(), stdout=object(), stderr=None, pid=None)

    async def no_rpc(*args, **kwargs):
        return None

    class Connection:
        def initialize(self, **kwargs):
            return None

    monkeypatch.setattr("agent_bridge.adapters.acp.asyncio.create_subprocess_exec", fake_spawn)
    monkeypatch.setattr("agent_bridge.adapters.acp.connect_to_agent", lambda *args: Connection())
    monkeypatch.setattr(adapter, "_rpc", no_rpc)
    await adapter._spawn(session)
    assert seen["cwd"] == str(requested)
