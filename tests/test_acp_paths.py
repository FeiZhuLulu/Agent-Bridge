from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from acp.exceptions import RequestError
from acp.schema import ToolCallLocation, ToolCallProgress, ToolCallStart

from agent_bridge.adapters.acp import (
    AcpAdapter,
    RpcTimeoutError,
    _BridgeClient,
    _Live,
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
@pytest.mark.parametrize(("error", "exit_code", "kind"), [
    (RequestError.auth_required(), None, "auth_required"),
    (RequestError.internal_error(), None, "protocol_error"),
    (RequestError.internal_error(), 7, "protocol_error"),
    (ConnectionError("Connection closed"), None, "connection_closed"),
    (ConnectionError("Connection closed"), 7, "worker_exit"),
])
async def test_rpc_failure_keeps_operation_and_process_facts(tmp_path, monkeypatch, error, exit_code, kind):
    adapter = AcpAdapter(AgentConfig(name="echo", protocol="acp", command=["echo"]), tmp_path)
    monkeypatch.setattr(adapter, "shutdown", AsyncMock())
    session = Session(session_id="sess_error", agent="echo", cwd=str(tmp_path))
    live = _Live()
    live.proc = SimpleNamespace(returncode=exit_code)
    adapter._live[session.session_id] = live

    async def fail():
        raise error

    with pytest.raises(Exception) as caught:
        await adapter._rpc(fail(), "session/new", session)
    failure = caught.value.failure
    assert failure.operation == "session/new"
    assert failure.kind == kind
    assert failure.exit_code == exit_code
    assert failure.prompt_may_have_been_sent is False


@pytest.mark.asyncio
async def test_timeout_captures_diagnostics_before_cleanup(tmp_path, monkeypatch, caplog):
    adapter = AcpAdapter(AgentConfig(name="echo", protocol="acp", command=["echo"]), tmp_path)
    session = Session(session_id="sess_timeout", agent="echo", cwd=str(tmp_path))
    live = _Live()
    live.proc = SimpleNamespace(returncode=None)
    live.stderr_tail = "Authentication required\nAuthorization: Bearer test-token\nCookie: session=test-cookie"
    adapter._live[session.session_id] = live

    async def shutdown(_session):
        live.proc.returncode = -15
        live.stderr_tail = ""
        adapter._live.pop(session.session_id)

    monkeypatch.setattr(adapter, "shutdown", shutdown)
    with pytest.raises(RpcTimeoutError) as caught:
        await adapter._rpc(asyncio.Event().wait(), "initialize", session, timeout=0.01)
    failure = caught.value.failure
    assert failure.kind == "timeout"
    assert failure.operation == "initialize"
    assert failure.exit_code is None
    assert failure.prompt_may_have_been_sent is False
    assert "authentication required" in failure.stderr_summary.lower()
    assert "test-token" not in failure.stderr_summary + caplog.text
    assert "test-cookie" not in failure.stderr_summary + caplog.text


@pytest.mark.asyncio
async def test_rpc_failure_redacts_and_bounds_untrusted_diagnostics(tmp_path, caplog):
    adapter = AcpAdapter(AgentConfig(name="echo", protocol="acp", command=["echo"]), tmp_path)
    session = Session(session_id="sess_redact", agent="echo", cwd=str(tmp_path))
    live = _Live()
    live.stderr_tail = (
        "Authentication required\n"
        "api_key = 'test-api-secret'\n"
        "https://test-user:test-pass@example.invalid/path?token=test-query-secret\n"
        "Cookie: session=test-cookie\n"
        + "untrusted diagnostic " * 2000
    )
    adapter._live[session.session_id] = live

    async def fail():
        raise RequestError(-32000, "Authentication required; token=test-error-secret")

    with pytest.raises(Exception) as caught:
        await adapter._rpc(fail(), "initialize", session)
    failure = caught.value.failure
    rendered = str(caught.value) + (failure.stderr_summary or "") + caplog.text
    for secret in ("test-api-secret", "test-user", "test-pass", "test-query-secret", "test-cookie", "test-error-secret"):
        assert secret not in rendered
    assert len(failure.stderr_summary or "") <= 2048


@pytest.mark.asyncio
async def test_rpc_cancellation_is_not_wrapped_as_failure(tmp_path):
    adapter = AcpAdapter(AgentConfig(name="echo", protocol="acp", command=["echo"]), tmp_path)
    session = Session(session_id="sess_cancel", agent="echo", cwd=str(tmp_path))

    async def cancel():
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await adapter._rpc(cancel(), "initialize", session)


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
