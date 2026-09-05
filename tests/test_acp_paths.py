from __future__ import annotations

import asyncio

import pytest
from acp.schema import ToolCallLocation, ToolCallProgress, ToolCallStart

from agent_bridge.adapters.acp import (
    AcpAdapter,
    RpcTimeoutError,
    _BridgeClient,
    should_collect_tool_paths,
)
from agent_bridge.config import AgentConfig
from agent_bridge.models import Session
from agent_bridge.transcript import read_events


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
async def test_tool_call_events_record_kind_status_and_input(tmp_path):
    client = _BridgeClient("sess_tool", tmp_path)
    start = ToolCallStart(
        sessionUpdate="tool_call",
        toolCallId="t1",
        title="edit a.py",
        kind="edit",
        status="in_progress",
        rawInput={"path": "src/a.py", "content": "x" * 2000},
        locations=[ToolCallLocation(path="src/a.py")],
    )
    await client.session_update("sess_tool", start)
    event = read_events("sess_tool", tmp_path)[-1]
    assert event["type"] == "tool_call"
    data = event["data"]
    assert data["tool_call_id"] == "t1"
    assert data["kind"] == "edit"
    assert data["status"] == "in_progress"
    assert data["locations"] == ["src/a.py"]
    assert len(data["input"]) == 501
    assert data["input"].endswith("…")

    failed = ToolCallProgress(
        sessionUpdate="tool_call_update",
        toolCallId="t1",
        status="failed",
        rawOutput="boom",
    )
    await client.session_update("sess_tool", failed)
    update = read_events("sess_tool", tmp_path)[-1]
    assert update["type"] == "tool_call_update"
    assert update["data"]["status"] == "failed"
    assert update["data"]["output"] == "boom"

    done = ToolCallProgress(
        sessionUpdate="tool_call_update",
        toolCallId="t1",
        status="completed",
        rawOutput="done",
    )
    await client.session_update("sess_tool", done)
    completed = read_events("sess_tool", tmp_path)[-1]
    assert completed["type"] == "tool_call_update"
    assert completed["data"]["status"] == "completed"
    assert "output" not in completed["data"]
