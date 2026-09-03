from __future__ import annotations

import sys
from pathlib import Path

import pytest

from agent_bridge.adapters.acp import (
    AcpAdapter,
    cursor_list_models_command,
    parse_cursor_models,
    with_cursor_cli_model,
)
from agent_bridge.config import AgentConfig
from agent_bridge.models import Session, Task


CURSOR_AGENT = Path(__file__).with_name("cursor_agent.py")


def test_cursor_commands_put_global_model_before_acp():
    command = ["cursor-agent", "--trust", "acp"]
    assert with_cursor_cli_model(command, "cursor-model-a") == [
        "cursor-agent",
        "--trust",
        "--model",
        "cursor-model-a",
        "acp",
    ]
    assert cursor_list_models_command(command) == [
        "cursor-agent",
        "--trust",
        "--list-models",
    ]
    assert cursor_list_models_command(["C:/Cursor/bin/agent.exe", "acp"]) == [
        "C:/Cursor/bin/agent.exe",
        "models",
    ]


def test_parse_cursor_models_uses_ids_not_labels_or_help_text():
    output = """Available models

cursor-model-a - Cursor Model A
cursor-model-b - Cursor Model B

Tip: use --model <id> to switch.
"""
    assert parse_cursor_models(output) == ["cursor-model-a", "cursor-model-b"]


@pytest.mark.asyncio
async def test_cursor_model_is_discovered_once_pinned_and_reported(tmp_path):
    marker = tmp_path / "model-list-calls.txt"
    adapter = AcpAdapter(
        AgentConfig(
            name="cursor",
            protocol="acp",
            command=[sys.executable, str(CURSOR_AGENT), "acp"],
            env={"CURSOR_AGENT_LIST_MARKER": str(marker)},
        ),
        tmp_path / "bridge-home",
    )
    session = Session(
        session_id="sess_cursor",
        agent="cursor",
        cwd=str(tmp_path),
        model="cursor-model-a",
        effort="high",
    )
    first = Task(
        task_id="task_cursor_1",
        session_id=session.session_id,
        agent="cursor",
        message="first",
        cwd=session.cwd,
        model=session.model,
        effort=session.effort,
    )
    second = first.model_copy(
        update={"task_id": "task_cursor_2", "message": "second"}
    )
    try:
        first_result = await adapter.run_turn(session, first)
        second_result = await adapter.run_turn(session, second)
    finally:
        await adapter.shutdown(session)

    assert first_result.text == "echo:first"
    assert second_result.text == "echo:second"
    assert first_result.observed_model == "cursor-model-a"
    assert first_result.observed_effort is None
    assert "no separate effort setting" in first_result.warnings[0]
    assert marker.read_text(encoding="utf-8").splitlines() == ["listed"]


@pytest.mark.asyncio
async def test_cursor_unavailable_model_fails_before_acp_start(tmp_path):
    adapter = AcpAdapter(
        AgentConfig(
            name="cursor",
            protocol="acp",
            command=[sys.executable, str(CURSOR_AGENT), "acp"],
        ),
        tmp_path / "bridge-home",
    )
    session = Session(
        session_id="sess_cursor_bad",
        agent="cursor",
        cwd=str(tmp_path),
        model="made-up-model",
    )
    task = Task(
        task_id="task_cursor_bad",
        session_id=session.session_id,
        agent="cursor",
        message="do not run",
        cwd=session.cwd,
        model=session.model,
    )

    with pytest.raises(ValueError, match="available model IDs: cursor-model-a, cursor-model-b"):
        await adapter.run_turn(session, task)
    assert session.pid is None
