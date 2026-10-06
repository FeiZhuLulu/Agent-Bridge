from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest

from agent_bridge.adapters.acp import (
    AcpAdapter,
    AcpError,
    _cursor_base_model,
    _cursor_effort_option_ids,
    _cursor_parameter_targets,
    cursor_list_models_command,
    parse_cursor_models,
    with_cursor_cli_model,
)
from agent_bridge.config import AgentConfig
from agent_bridge.models import Session, Task
from agent_bridge.processes import reap_subprocess

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
    assert parse_cursor_models(output) == {
        "cursor-model-a": "Cursor Model A",
        "cursor-model-b": "Cursor Model B",
    }


def test_cursor_full_id_maps_to_parameterized_acp_options():
    options = [
        {
            "id": "model",
            "currentValue": "default",
            "options": [
                {"value": "grok-4.6", "name": "Cursor Grok 4.6"},
                {"value": "gemini-3.8-flash", "name": "Gemini 3.8 Flash"},
                {"value": "claude-opus-4-6", "name": "Claude Opus 4.6"},
            ],
        },
        {
            "id": "effort",
            "category": "thought_level",
            "currentValue": "medium",
            "options": [
                {"value": "low", "name": "Low"},
                {"value": "medium", "name": "Medium"},
                {"value": "high", "name": "High"},
            ],
        },
        {
            "id": "fast",
            "currentValue": "false",
            "options": [
                {"value": "false", "name": "Off"},
                {"value": "true", "name": "Fast"},
            ],
        },
    ]
    base, name = _cursor_base_model(
        "cursor-grok-4.6-high-fast",
        "Cursor Grok 4.6 Fast",
        options,
    )
    assert (base, name) == ("grok-4.6", "Cursor Grok 4.6")
    assert _cursor_parameter_targets(
        "cursor-grok-4.6-high-fast",
        "Cursor Grok 4.6 Fast",
        base,
        name,
        None,
        options,
    ) == {"effort": "high", "fast": "true"}
    assert _cursor_parameter_targets(
        "cursor-grok-4.6-high-fast",
        "Cursor Grok 4.6 Fast",
        base,
        name,
        "low",
        options,
    ) == {"effort": "low", "fast": "true"}
    gemini_base, gemini_name = _cursor_base_model(
        "gemini-3.8-flash-low",
        "Gemini 3.8 Flash Low",
        options,
    )
    assert _cursor_parameter_targets(
        "gemini-3.8-flash-low",
        "Gemini 3.8 Flash Low",
        gemini_base,
        gemini_name,
        None,
        options,
    ) == {"effort": "low", "fast": "false"}


def test_cursor_label_maps_reordered_model_and_parameters():
    options = [
        {
            "id": "model",
            "currentValue": "default",
            "options": [
                {"value": "claude-opus-4-6", "name": "Claude Opus 4.6"},
            ],
        },
        {
            "id": "context",
            "currentValue": "200k",
            "options": [
                {"value": "200k", "name": "200K"},
                {"value": "1m", "name": "1M"},
            ],
        },
        {
            "id": "thinking",
            "category": "thought_level",
            "currentValue": "false",
            "options": [
                {"value": "false", "name": "Off"},
                {"value": "true", "name": "Thinking"},
            ],
        },
        {
            "id": "effort",
            "category": "thought_level",
            "currentValue": "medium",
            "options": [
                {"value": "medium", "name": "Medium"},
                {"value": "high", "name": "High"},
            ],
        },
    ]
    model = "claude-4.6-opus-high-thinking"
    label = "Claude Opus 4.6 1M Thinking"
    base, name = _cursor_base_model(model, label, options)
    assert base == "claude-opus-4-6"
    assert _cursor_effort_option_ids(options) == ["effort"]
    expected = {
        "context": "1m",
        "thinking": "true",
        "effort": "high",
    }
    assert _cursor_parameter_targets(model, label, base, name, None, options) == expected
    assert _cursor_parameter_targets(model, label, base, name, "high", options) == expected


@pytest.mark.asyncio
async def test_cursor_model_is_discovered_once_pinned_switched_and_reported(tmp_path):
    marker = tmp_path / "model-list-calls.txt"
    capabilities = tmp_path / "capabilities.txt"
    adapter = AcpAdapter(
        AgentConfig(
            name="cursor",
            protocol="acp",
            command=[sys.executable, str(CURSOR_AGENT), "acp"],
            env={
                "CURSOR_AGENT_LIST_MARKER": str(marker),
                "CURSOR_AGENT_CAPABILITIES_MARKER": str(capabilities),
            },
        ),
        tmp_path / "bridge-home",
    )
    session = Session(
        session_id="sess_cursor",
        agent="cursor",
        cwd=str(tmp_path),
        model="cursor-model-a-medium",
    )
    first = Task(
        task_id="task_cursor_1",
        session_id=session.session_id,
        agent="cursor",
        message="first",
        cwd=session.cwd,
        model=session.model,
    )
    second = first.model_copy(
        update={"task_id": "task_cursor_2", "message": "second"}
    )
    try:
        first_result = await adapter.run_turn(session, first)
        second_result = await adapter.run_turn(session, second)
        session.model = "cursor-model-a-high-fast"
        third = second.model_copy(
            update={
                "task_id": "task_cursor_3",
                "message": "third",
                "model": session.model,
            }
        )
        third_result = await adapter.run_turn(session, third)
        session.model = "cursor-model-b"
        session.effort = "high"
        fourth = third.model_copy(
            update={
                "task_id": "task_cursor_4",
                "message": "fourth",
                "model": session.model,
                "effort": session.effort,
            }
        )
        fourth_result = await adapter.run_turn(session, fourth)
        session.model = "made-up-model"
        invalid = fourth.model_copy(
            update={
                "task_id": "task_cursor_5",
                "message": "fifth",
                "model": session.model,
            }
        )
        with pytest.raises(AcpError, match="available model IDs") as caught:
            await adapter.run_turn(session, invalid)
        assert caught.value.failure.kind == "unsupported_model"
        assert caught.value.failure.prompt_may_have_been_sent is False
        live = adapter._live[session.session_id]
        assert live.applied_model == "cursor-model-b"
    finally:
        await adapter.shutdown(session)

    assert first_result.text == "echo:first"
    assert second_result.text == "echo:second"
    assert third_result.text == "echo:third"
    assert fourth_result.text == "echo:fourth"
    assert first_result.observed_model == "cursor-model-a-medium"
    assert first_result.observed_effort == "medium"
    assert third_result.observed_model == "cursor-model-a-high-fast"
    assert third_result.observed_effort == "high"
    assert fourth_result.observed_model == "cursor-model-b"
    assert fourth_result.observed_effort == "high"
    assert not first_result.warnings
    assert marker.read_text(encoding="utf-8").splitlines() == ["listed"]
    assert capabilities.read_text(encoding="utf-8") == "{'parameterizedModelPicker': True}"


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

    with pytest.raises(AcpError, match="available model IDs: cursor-model-a, cursor-model-a-medium") as caught:
        await adapter.run_turn(session, task)
    assert caught.value.failure.operation == "model_discovery"
    assert caught.value.failure.kind == "unsupported_model"
    assert caught.value.failure.prompt_may_have_been_sent is False
    assert session.pid is None


@pytest.mark.asyncio
async def test_cursor_model_discovery_reports_its_own_exit(tmp_path):
    command = [
        sys.executable, "-c",
        "import sys; print('Authentication required', file=sys.stderr); "
        "print('Authorization: Bearer test-discovery-secret', file=sys.stderr); sys.exit(7)",
        "acp",
    ]
    adapter = AcpAdapter(AgentConfig(name="cursor", protocol="acp", command=command), tmp_path)
    with pytest.raises(AcpError) as caught:
        await adapter._cursor_models(command, dict(os.environ), str(tmp_path))
    failure = caught.value.failure
    assert failure.operation == "model_discovery"
    assert failure.exit_code == 7
    assert failure.kind == "worker_exit"
    assert failure.prompt_may_have_been_sent is False
    assert "authentication required" in failure.stderr_summary.lower()
    assert "test-discovery-secret" not in str(caught.value) + failure.stderr_summary


@pytest.mark.asyncio
async def test_cursor_model_discovery_reaps_process_on_cancel(tmp_path, monkeypatch):
    reaped: list[object] = []
    started = asyncio.Event()
    real_exec = asyncio.create_subprocess_exec
    real_reap = reap_subprocess

    async def wrapping_exec(*args, **kwargs):
        proc = await real_exec(*args, **kwargs)
        started.set()
        return proc

    async def tracking_reap(proc, timeout=5.0):
        reaped.append(proc)
        await real_reap(proc, timeout=timeout)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", wrapping_exec)
    monkeypatch.setattr("agent_bridge.adapters.acp.reap_subprocess", tracking_reap)

    adapter = AcpAdapter(
        AgentConfig(
            name="cursor",
            protocol="acp",
            command=[sys.executable, "-c", "import time; time.sleep(30)", "acp"],
        ),
        tmp_path / "bridge-home",
    )
    task = asyncio.create_task(
        adapter._cursor_models(adapter.agent.command, adapter._env(), str(tmp_path))
    )
    await asyncio.wait_for(started.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert reaped
    proc = reaped[0]
    assert proc.returncode is not None
