from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

from agent_bridge.adapters.codex import CodexAdapter
from agent_bridge.codex_exec import (
    CodexTurnState,
    apply_codex_event,
    build_codex_exec_argv,
    codex_command_problem,
    codex_effort,
    discovered_codex_commands,
    exec_help_problem,
    finalize_codex_turn,
    read_codex_cli_path,
    resolve_codex_command,
    yolo_requested,
    _codex_exe_name,
)
from agent_bridge.config import AgentConfig
from agent_bridge.models import Session, Task
from agent_bridge.transcript import read_events

FAKE = Path(__file__).resolve().parent / "fake_codex.py"


def _launcher(tmp_path: Path, script: Path = FAKE) -> Path:
    if os.name == "nt":
        path = tmp_path / "codex.cmd"
        path.write_text(
            f'@echo off\r\n"{sys.executable}" "{script}" %*\r\n',
            encoding="utf-8",
        )
        return path
    path = tmp_path / "codex"
    path.write_text(
        f"#!/usr/bin/env bash\nexec {sys.executable!r} {str(script)!r} \"$@\"\n",
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


def test_codex_effort_maps_off_to_none():
    assert codex_effort("off") == "none"
    assert codex_effort("low") == "low"
    assert codex_effort("max") == "max"
    assert codex_effort(None) is None


def test_yolo_is_explicit_only():
    assert yolo_requested({}) is False
    assert yolo_requested({"yolo": True}) is True
    assert yolo_requested({"yolo": "true"}) is True
    assert yolo_requested({"yolo": False}) is False


def test_build_argv_default_is_approve_for_me_and_stdin_dash():
    cmd = build_codex_exec_argv(["codex.exe"], cwd=r"E:\proj", model="gpt-5.6-sol", effort="off")
    assert cmd[:5] == ["codex.exe", "exec", "--json", "--ignore-user-config", "-c"]
    assert 'cli_auth_credentials_store="auto"' in cmd
    assert "--approve-for-me" in cmd
    assert "--yolo" not in cmd
    assert "--thread-source" in cmd
    assert cmd[cmd.index("--thread-source") + 1] == "subagent"
    assert cmd[-1] == "-"
    assert "resume" not in cmd
    assert 'model_reasoning_effort="none"' in cmd
    assert "-m" in cmd


def test_build_argv_resume_and_yolo():
    cmd = build_codex_exec_argv(
        ["codex.exe"],
        cwd="/tmp/p",
        resume_id="thread-1",
        yolo=True,
    )
    assert "--yolo" in cmd
    assert "--approve-for-me" not in cmd
    assert cmd[-3:] == ["resume", "thread-1", "-"]


def test_jsonl_takes_last_agent_message_and_requires_turn_completed():
    state = CodexTurnState()
    apply_codex_event(state, {"type": "thread.started", "thread_id": "t1"})
    apply_codex_event(
        state,
        {
            "type": "item.completed",
            "item": {"id": "1", "type": "agent_message", "text": "one"},
        },
    )
    apply_codex_event(
        state,
        {
            "type": "item.completed",
            "item": {
                "id": "2",
                "type": "file_change",
                "changes": [{"path": "a.py", "kind": "add"}],
            },
        },
    )
    apply_codex_event(
        state,
        {
            "type": "item.completed",
            "item": {"id": "3", "type": "agent_message", "text": "two"},
        },
    )
    assert state.text == "two"
    assert state.files == {"a.py"}
    assert state.turn_completed is False
    apply_codex_event(state, {"type": "turn.completed", "usage": {"input_tokens": 3}})
    assert state.turn_completed is True
    assert state.usage["input_tokens"] == 3


def test_jsonl_turn_failed_and_error():
    state = CodexTurnState()
    apply_codex_event(state, {"type": "turn.failed", "error": {"message": "nope"}})
    apply_codex_event(state, {"type": "error", "message": "boom"})
    assert state.turn_failed is True
    assert state.error == "boom"


def test_finalize_error_does_not_override_turn_completed():
    state = CodexTurnState()
    apply_codex_event(state, {"type": "error", "message": "retryable"})
    apply_codex_event(state, {"type": "item.completed", "item": {"type": "agent_message", "text": "ok"}})
    apply_codex_event(state, {"type": "turn.completed", "usage": {"input_tokens": 1}})
    reason, error, warnings = finalize_codex_turn(state, returncode=0)
    assert reason == "end_turn"
    assert error is None
    assert "retryable" in warnings


def test_finalize_turn_failed_wins():
    state = CodexTurnState()
    apply_codex_event(state, {"type": "turn.failed", "error": {"message": "quota exceeded"}})
    reason, error, warnings = finalize_codex_turn(state, returncode=1)
    assert reason == "error"
    assert error == "quota exceeded"
    assert warnings == ["codex exited with code 1"]


def test_finalize_error_without_completed_is_failure():
    state = CodexTurnState()
    apply_codex_event(state, {"type": "error", "message": "boom"})
    reason, error, _warnings = finalize_codex_turn(state)
    assert reason == "error"
    assert error == "boom"


def test_finalize_uses_stderr_when_jsonl_never_started():
    state = CodexTurnState()
    reason, error, warnings = finalize_codex_turn(
        state,
        returncode=2,
        stderr="codex startup failed: invalid config\n",
    )
    assert reason == "error"
    assert error == "codex startup failed: invalid config"
    assert warnings == ["codex exited with code 2"]


def test_exec_help_requires_capability_flags():
    assert exec_help_problem("--json --approve-for-me") is not None
    assert (
        exec_help_problem(
            "--json --ignore-user-config --approve-for-me --skip-git-repo-check --thread-source"
        )
        is None
    )


def test_reads_codex_cli_path_from_codex_home_env(tmp_path: Path):
    home = tmp_path / "custom-home"
    home.mkdir()
    (home / "config.toml").write_text(
        'CODEX_CLI_PATH = "C:\\\\custom\\\\codex.exe"\n',
        encoding="utf-8",
    )
    assert read_codex_cli_path(env={"CODEX_HOME": str(home)}) == r"C:\custom\codex.exe"


def test_reads_codex_cli_path_from_nested_config(tmp_path: Path):
    home = tmp_path / ".codex"
    home.mkdir()
    (home / "config.toml").write_text(
        '[mcp_servers.node_repl.env]\nCODEX_CLI_PATH = "C:\\\\Desktop\\\\codex.exe"\n',
        encoding="utf-8",
    )
    assert read_codex_cli_path(home) == r"C:\Desktop\codex.exe"


def test_discovery_prefers_cli_path_then_desktop_mtime(tmp_path: Path, monkeypatch):
    bin_root = tmp_path / "OpenAI" / "Codex" / "bin"
    old = bin_root / "oldhash"
    new = bin_root / "newhash"
    old.mkdir(parents=True)
    new.mkdir()
    exe_name = _codex_exe_name()
    (old / exe_name).write_text("old", encoding="utf-8")
    (new / exe_name).write_text("new", encoding="utf-8")
    os.utime(old / exe_name, (1, 1))
    os.utime(new / exe_name, (100, 100))
    pinned = tmp_path / "pinned.exe"
    pinned.write_text("pin", encoding="utf-8")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("CODEX_CLI_PATH", str(pinned))
    found = discovered_codex_commands()
    assert found[0] == [str(pinned.resolve())]
    names = [Path(item[0]).parent.name for item in found[1:]]
    assert names[0] == "newhash"


def test_resolve_validates_exec_help(tmp_path: Path):
    launcher = _launcher(tmp_path)
    resolved = resolve_codex_command([str(launcher)])
    assert Path(resolved[0]).name.startswith("codex")
    assert codex_command_problem(resolved) is None


def test_resolve_rejects_binary_missing_flags(tmp_path: Path):
    stub = tmp_path / "not-codex.py"
    stub.write_text("print('hello')\n", encoding="utf-8")
    launcher = _launcher(tmp_path, stub)
    with pytest.raises(FileNotFoundError):
        resolve_codex_command([str(launcher)], extra=[])


@pytest.mark.asyncio
async def test_adapter_new_resume_fail_and_long_stdin(tmp_path: Path, monkeypatch):
    dump = tmp_path / "dump.json"
    monkeypatch.setenv("FAKE_CODEX_DUMP", str(dump))
    monkeypatch.setattr(
        "agent_bridge.adapters.codex.resolve_codex_command",
        lambda *args, **kwargs: [sys.executable, str(FAKE)],
    )
    adapter = CodexAdapter(
        AgentConfig(name="codex", protocol="codex", command=["codex"]),
        tmp_path,
    )
    session = Session(session_id="s1", agent="codex", cwd=str(tmp_path), model="gpt-5.6-sol", effort="off")
    long_prompt = "x" * 8000
    first = await adapter.run_turn(
        session,
        Task(task_id="t1", session_id="s1", agent="codex", message=long_prompt, cwd=str(tmp_path)),
    )
    assert first.stop_reason == "end_turn"
    assert first.native_session_id == "thread-new"
    assert first.text.startswith("done:")
    assert first.files_changed == ["src/app.py"]
    assert first.observed_effort == "none"
    payload = json.loads(dump.read_text(encoding="utf-8"))
    assert payload["prompt"] == long_prompt
    assert payload["argv"][-1] == "-"
    assert "--approve-for-me" in payload["argv"]
    assert "resume" not in payload["argv"]
    events = read_events("s1", tmp_path)
    assert sum(event["type"] == "turn_end" for event in events) == 1
    assert any(
        event["type"] == "raw"
        and event["data"].get("payload", {}).get("type") == "turn.completed"
        for event in events
    )

    session.native_session_id = first.native_session_id
    second = await adapter.run_turn(
        session,
        Task(task_id="t2", session_id="s1", agent="codex", message="again", cwd=str(tmp_path)),
    )
    payload = json.loads(dump.read_text(encoding="utf-8"))
    assert payload["argv"][-3:] == ["resume", "thread-new", "-"]
    assert second.stop_reason == "end_turn"

    monkeypatch.setenv("FAKE_CODEX_FAIL", "1")
    failed = await adapter.run_turn(
        session,
        Task(task_id="t3", session_id="s1", agent="codex", message="no", cwd=str(tmp_path)),
    )
    assert failed.stop_reason == "error"
    assert failed.error == "quota exceeded"


@pytest.mark.asyncio
async def test_adapter_surfaces_stderr_when_codex_exits_before_jsonl(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("FAKE_CODEX_STDERR_FAIL", "1")
    monkeypatch.setattr(
        "agent_bridge.adapters.codex.resolve_codex_command",
        lambda *args, **kwargs: [sys.executable, str(FAKE)],
    )
    adapter = CodexAdapter(
        AgentConfig(name="codex", protocol="codex", command=["codex"]),
        tmp_path,
    )
    session = Session(session_id="s-stderr", agent="codex", cwd=str(tmp_path))
    result = await adapter.run_turn(
        session,
        Task(
            task_id="t-stderr",
            session_id=session.session_id,
            agent="codex",
            message="fail before jsonl",
            cwd=str(tmp_path),
        ),
    )
    assert result.stop_reason == "error"
    assert result.error == "codex startup failed: invalid config"
    assert result.warnings == ["codex exited with code 2"]


@pytest.mark.asyncio
async def test_adapter_error_then_completed_is_success(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("FAKE_CODEX_ERROR_THEN_COMPLETE", "1")
    monkeypatch.setattr(
        "agent_bridge.adapters.codex.resolve_codex_command",
        lambda *args, **kwargs: [sys.executable, str(FAKE)],
    )
    adapter = CodexAdapter(
        AgentConfig(name="codex", protocol="codex", command=["codex"]),
        tmp_path,
    )
    session = Session(session_id="s3", agent="codex", cwd=str(tmp_path))
    result = await adapter.run_turn(
        session,
        Task(task_id="t1", session_id="s3", agent="codex", message="ok", cwd=str(tmp_path)),
    )
    assert result.stop_reason == "end_turn"
    assert result.error is None
    assert result.text == "recovered"
    assert "retryable" in result.warnings


@pytest.mark.asyncio
async def test_adapter_cancel_interrupts(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("FAKE_CODEX_SLEEP", "20")
    monkeypatch.setattr(
        "agent_bridge.adapters.codex.resolve_codex_command",
        lambda *args, **kwargs: [sys.executable, str(FAKE)],
    )
    adapter = CodexAdapter(
        AgentConfig(name="codex", protocol="codex", command=["codex"]),
        tmp_path,
    )
    session = Session(session_id="s2", agent="codex", cwd=str(tmp_path))
    task = asyncio.create_task(
        adapter.run_turn(
            session,
            Task(task_id="t1", session_id="s2", agent="codex", message="slow", cwd=str(tmp_path)),
        )
    )
    await asyncio.sleep(0.3)
    await adapter.cancel(session)
    result = await asyncio.wait_for(task, timeout=10)
    assert result.stop_reason == "cancelled"
