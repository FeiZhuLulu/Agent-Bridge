from __future__ import annotations

import sys
from pathlib import Path

import pytest

from agent_bridge.adapters.acp import AcpAdapter
from agent_bridge.config import AgentConfig
from agent_bridge.models import Session, Task
from agent_bridge.paths import pids_path
from agent_bridge.persist import read_json


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
