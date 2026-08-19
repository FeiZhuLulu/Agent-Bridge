from __future__ import annotations

import sys
from pathlib import Path

import pytest

from agent_bridge.config import AgentConfig
from agent_bridge.adapters.acp import AcpAdapter
from agent_bridge.models import Session, Task


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
        await adapter.shutdown(session)
