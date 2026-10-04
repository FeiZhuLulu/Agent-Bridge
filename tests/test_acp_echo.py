from __future__ import annotations

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


@pytest.mark.parametrize(("mode", "operation", "possibly_sent"), [
    ("initialize", "initialize", False),
    ("new", "session/new", False),
    ("prompt_exit", "session/prompt", True),
])
async def test_acp_failures_survive_tools_and_restart(bridge_home, tmp_path, mode, operation, possibly_sent):
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
        assert failure["kind"] in ({"connection_closed", "worker_exit"} if possibly_sent else {"auth_required"})
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
