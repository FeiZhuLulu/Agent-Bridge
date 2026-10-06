from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from agent_bridge.adapters.acp import AcpAdapter
from agent_bridge.config import AgentConfig
from agent_bridge.models import Session, Task
from agent_bridge.paths import pids_path
from agent_bridge.persist import read_json
from agent_bridge.registry import Registry


@pytest.mark.asyncio
async def test_acp_echo_roundtrip(bridge_home, tmp_path):
    echo = Path(__file__).resolve().parent / "echo_agent.py"
    work = tmp_path / "work"
    work.mkdir()
    cfg = AgentConfig(
        name="echo",
        protocol="acp",
        command=[sys.executable, str(echo)],
        revivable=True,
        idle_unload_sec=0,
    )
    adapter = AcpAdapter(cfg, bridge_home)
    session = Session(session_id="sess_echo", agent="echo", cwd=str(work.resolve()))
    task = Task(
        task_id="task_echo",
        session_id=session.session_id,
        agent="echo",
        message="hello-bridge",
        cwd=str(work.resolve()),
    )
    try:
        result = await adapter.run_turn(session, task)
        assert result.stop_reason == "end_turn"
        assert "echo:hello-bridge" in result.text
        assert result.usage.get("inputTokens") == 1 or result.usage.get("input_tokens") == 1
        assert result.usage.get("outputTokens") == 2 or result.usage.get("output_tokens") == 2
        assert session.native_session_id
        follow = Task(
            task_id="task_echo2",
            session_id=session.session_id,
            agent="echo",
            message="second",
            cwd=str(work.resolve()),
        )
        result2 = await adapter.run_turn(session, follow)
        assert "echo:second" in result2.text
    finally:
        live = adapter._live.get(session.session_id)
        proc = live.proc if live else None
        stderr_task = live.stderr_task if live else None
        await adapter.shutdown(session)
        assert session.session_id not in adapter._live
        assert session.pid is None
        if stderr_task is not None:
            assert stderr_task.done()
        if proc is not None:
            assert proc.returncode is not None
        table = read_json(pids_path(bridge_home), {})
        assert session.session_id not in table


@pytest.mark.parametrize(("mode", "operation", "possibly_sent", "kind", "exit_code"), [
    ("initialize", "initialize", False, "auth_required", None),
    ("new", "session/new", False, "auth_required", None),
    ("initialize_exit", "initialize", False, "worker_exit", 9),
    ("prompt_exit", "session/prompt", True, "worker_exit", 7),
])
async def test_acp_failures_survive_tools_and_restart(
    bridge_home, tmp_path, mode, operation, possibly_sent, kind, exit_code
):
    marker = tmp_path / "effects.txt"
    cfg = AgentConfig(
        name="echo", protocol="acp",
        command=[sys.executable, str(Path(__file__).with_name("echo_agent.py"))],
        env={"BRIDGE_ECHO_FAILURE": mode, "BRIDGE_ECHO_MARKER": str(marker)},
    )
    registry = Registry.create(bridge_home)
    registry.config.agents["echo"] = cfg
    await registry.start()
    try:
        dispatched = await registry.dispatch_task("echo", "run once", cwd=str(tmp_path))
        task_id = dispatched["task_id"]
        result = await registry.wait_task(task_id, timeout_sec=10)
        assert result["status"] == "failed"
        assert isinstance(result["error"], str) and result["error"]
        failure = result["failure"]
        assert failure["operation"] == operation
        assert failure["prompt_may_have_been_sent"] is possibly_sent
        assert failure["kind"] == kind
        assert failure["exit_code"] == exit_code
        assert registry.check_task(task_id)["failure"] == failure
        assert registry.get_result(task_id)["failure"] == failure
        if possibly_sent:
            assert marker.read_text(encoding="utf-8") == "executed\n"
        else:
            assert not marker.exists()
    finally:
        await registry.stop()

    restored = Registry.create(bridge_home)
    restored.config.agents["echo"] = cfg
    await restored.start()
    try:
        assert restored.get_result(task_id)["failure"] == failure
        assert restored.check_task(task_id)["status"] == "failed"
    finally:
        await restored.stop()


@pytest.mark.asyncio
async def test_cancel_of_cancel_ignoring_worker_ends_cancelled(bridge_home, tmp_path, monkeypatch):
    marker = tmp_path / "effects.txt"
    cfg = AgentConfig(
        name="echo", protocol="acp",
        command=[sys.executable, str(Path(__file__).with_name("echo_agent.py"))],
        env={"BRIDGE_ECHO_FAILURE": "prompt_hang", "BRIDGE_ECHO_MARKER": str(marker)},
    )
    monkeypatch.setattr("agent_bridge.adapters.acp.PROMPT_CANCEL_GRACE_SEC", 0.5)
    registry = Registry.create(bridge_home)
    registry.config.agents["echo"] = cfg
    await registry.start()
    try:
        dispatched = await registry.dispatch_task("echo", "hang", cwd=str(tmp_path))
        for _ in range(100):
            if marker.exists():
                break
            await asyncio.sleep(0.05)
        assert marker.exists()
        cancelled = await registry.cancel_task(dispatched["task_id"])
        assert cancelled["status"] == "cancelled"
        assert cancelled["stop_reason"] == "cancelled"
        assert cancelled["failure"] is None
        waited = await registry.wait_task(dispatched["task_id"], timeout_sec=10)
        assert waited["status"] == "cancelled"
        assert registry.check_task(dispatched["task_id"])["failure"] is None
        session = registry.sessions[dispatched["session_id"]]
        assert session.pid is None
        assert session.session_id not in read_json(pids_path(bridge_home), {})
    finally:
        await registry.stop()


@pytest.mark.asyncio
async def test_stderr_flood_keeps_auth_diagnostics(bridge_home, tmp_path):
    cfg = AgentConfig(
        name="echo", protocol="acp",
        command=[sys.executable, str(Path(__file__).with_name("echo_agent.py"))],
        env={"BRIDGE_ECHO_FAILURE": "stderr_flood"},
    )
    registry = Registry.create(bridge_home)
    registry.config.agents["echo"] = cfg
    await registry.start()
    try:
        dispatched = await registry.dispatch_task("echo", "flood", cwd=str(tmp_path))
        result = await registry.wait_task(dispatched["task_id"], timeout_sec=30)
        assert result["timed_out"] is False
        assert result["status"] == "failed"
        assert result["failure"]["kind"] == "auth_required"
        assert "authentication required" in result["failure"]["stderr_summary"]
    finally:
        await registry.stop()


async def test_missing_worker_command_reports_unsent_failure(bridge_home, tmp_path):
    registry = Registry.create(bridge_home)
    registry.config.agents["missing"] = AgentConfig(
        name="missing", protocol="acp", command=[str(tmp_path / "no-such-worker")],
    )
    await registry.start()
    try:
        dispatched = await registry.dispatch_task("missing", "never sent", cwd=str(tmp_path))
        result = await registry.wait_task(dispatched["task_id"], timeout_sec=5)
        assert result["failure"]["operation"] == "process_start"
        assert result["failure"]["prompt_may_have_been_sent"] is False
        assert result["failure"]["exit_code"] is None
    finally:
        await registry.stop()


@pytest.mark.parametrize(("mode", "operation"), [("load", "session/load"), ("initialize", "initialize")])
async def test_resume_auth_failure_does_not_create_a_blank_session(bridge_home, tmp_path, mode, operation):
    cfg = AgentConfig(
        name="echo", protocol="acp", revivable=True,
        command=[sys.executable, str(Path(__file__).with_name("echo_agent.py"))],
    )
    registry = Registry.create(bridge_home)
    registry.config.agents["echo"] = cfg
    await registry.start()
    try:
        first = await registry.dispatch_task("echo", "remember", cwd=str(tmp_path))
        assert (await registry.wait_task(first["task_id"], timeout_sec=10))["status"] == "completed"
        session = registry.sessions[first["session_id"]]
        await registry._adapter_for(session).shutdown(session)
        cfg.env["BRIDGE_ECHO_FAILURE"] = mode
        follow = await registry.dispatch_task("echo", "continue", cwd=str(tmp_path), session_id=session.session_id)
        result = await registry.wait_task(follow["task_id"], timeout_sec=10)
        assert result["status"] == "failed"
        assert result["failure"]["operation"] == operation
        assert result["failure"]["kind"] == "auth_required"
        assert result["failure"]["prompt_may_have_been_sent"] is False
        assert session.turns == 1
        cfg.env.pop("BRIDGE_ECHO_FAILURE")
        retry = await registry.dispatch_task("echo", "continue after login", cwd=str(tmp_path), session_id=session.session_id)
        recovered = await registry.wait_task(retry["task_id"], timeout_sec=10)
        assert recovered["status"] == "completed"
        assert recovered["failure"] is None
        assert session.turns == 2
    finally:
        await registry.stop()


def test_legacy_task_has_no_failure_evidence():
    task = Task.model_validate({
        "task_id": "old", "session_id": "old", "agent": "echo", "cwd": ".",
        "message": "old", "status": "failed", "error": "Connection closed",
    })
    assert task.failure is None


@pytest.mark.asyncio
async def test_acp_load_failure_surfaces_session_recreated(bridge_home, tmp_path, monkeypatch):
    """H-05: session/load -> session/new fallback must be visible to the
    coordinator (task warning) and auditable later (transcript event)."""
    echo = Path(__file__).resolve().parent / "echo_agent.py"
    work = tmp_path / "work"
    work.mkdir()
    cfg = AgentConfig(
        name="echo",
        protocol="acp",
        command=[sys.executable, str(echo)],
        revivable=True,
        idle_unload_sec=0,
    )
    adapter = AcpAdapter(cfg, bridge_home)
    session = Session(session_id="sess_reload", agent="echo", cwd=str(work.resolve()))
    first = Task(
        task_id="task_reload_1",
        session_id=session.session_id,
        agent="echo",
        message="one",
        cwd=str(work.resolve()),
    )
    try:
        r1 = await adapter.run_turn(session, first)
        assert r1.stop_reason == "end_turn"
        assert session.native_session_id
        await adapter.shutdown(session)
        # Make the worker's session/load fail (generic, non-unavailable) on
        # the next spawn so Bridge takes the session/new fallback.
        cfg.env["BRIDGE_ECHO_FAILURE"] = "load_internal"
        second = Task(
            task_id="task_reload_2",
            session_id=session.session_id,
            agent="echo",
            message="two",
            cwd=str(work.resolve()),
        )
        r2 = await adapter.run_turn(session, second)
        assert r2.stop_reason == "end_turn"
        assert any("context is lost" in w for w in r2.warnings)
        from agent_bridge.transcript import read_events

        types = {event["type"] for event in read_events(session.session_id, bridge_home)}
        assert "session_recreated" in types
    finally:
        cfg.env.pop("BRIDGE_ECHO_FAILURE", None)
        await adapter.shutdown(session)
