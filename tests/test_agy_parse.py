import sys
from pathlib import Path

import pytest

from agent_bridge.adapters.antigravity import (
    AgyAdapter,
    _scoped_usage,
    collect_tool_paths,
    conversation_id_of,
    is_agy_tool_schema_error,
    is_result_event,
    recovered_agy_tool_error,
    result_error_of,
    result_text_of,
    text_delta_of,
)
from agent_bridge.config import AgentConfig
from agent_bridge.models import Session, Task

FAKE_AGY = Path(__file__).resolve().parent / "fake_agy.py"

INIT = {
    "event": "init",
    "conversation_id": "c3b66b04-872b-4fbe-a3a4-058a026ef20a",
    "init": {"cwd": "/home/user/project", "tools": ["write_to_file"], "permission_mode": "always-proceed"},
}
STEP_TEXT = {
    "event": "step_update",
    "step_update": {
        "conversation_id": "c3b66b04-872b-4fbe-a3a4-058a026ef20a",
        "step_index": 3,
        "state": "DONE",
        "step_type": "agent_response",
        "text_delta": "Git rebase rewrites history.\n",
    },
}
RESULT = {
    "event": "result",
    "result": {
        "conversation_id": "c3b66b04-872b-4fbe-a3a4-058a026ef20a",
        "status": "SUCCESS",
        "response": "Git rebase rewrites history.\n",
        "usage": {"total_tokens": 11007},
    },
}
ERROR = {
    "event": "result",
    "result": {"conversation_id": "", "status": "ERROR", "response": "", "error": "authentication required"},
}


def test_init_is_not_a_result():
    assert conversation_id_of(INIT) == "c3b66b04-872b-4fbe-a3a4-058a026ef20a"
    assert is_result_event(INIT) is False
    assert result_text_of(INIT) == ""


def test_text_delta_and_result_envelope():
    assert text_delta_of(STEP_TEXT) == "Git rebase rewrites history.\n"
    assert is_result_event(RESULT) is True
    assert result_text_of(RESULT) == "Git rebase rewrites history.\n"
    assert result_error_of(RESULT) is None


def test_error_result():
    assert is_result_event(ERROR) is True
    assert result_error_of(ERROR) == "authentication required"


CODECONTENT_ERROR = (
    "declaring permissions: cortex tool write_to_file: convert tool call for "
    "permissions: model output error: invalid tool call error (invalid_signature) "
    "CodeContent is a required parameter. Please follow the function call schema exactly."
)


def test_tool_schema_error_is_not_a_hard_failure_when_the_turn_recovered():
    assert is_agy_tool_schema_error(CODECONTENT_ERROR) is True
    assert is_agy_tool_schema_error("authentication required") is False
    recovered = {
        "event": "result",
        "result": {
            "status": "ERROR",
            "response": "Implemented scoreboard and 26 tests passed.\n",
            "error": CODECONTENT_ERROR,
        },
    }
    assert recovered_agy_tool_error(recovered, recovered["result"]["response"], 0) is True
    assert recovered_agy_tool_error(recovered, "", 0) is False
    assert recovered_agy_tool_error(ERROR, "authentication required", 1) is False


def test_collect_tool_paths():
    files: set[str] = set()
    collect_tool_paths(
        {
            "event": "step_update",
            "step_update": {
                "step_type": "tool",
                "tool_info": {"name": "write_to_file", "parameters": {"Path": "/tmp/a.txt"}},
            },
        },
        files,
    )
    assert files == {"/tmp/a.txt"}


def test_collect_tool_paths_skips_read_only_tools():
    files: set[str] = set()
    for name, key in (("view_file", "AbsolutePath"), ("list_dir", "Path"), ("grep_search", "Path")):
        collect_tool_paths(
            {
                "event": "step_update",
                "step_update": {
                    "step_type": "tool",
                    "tool_name": name,
                    "tool_info": {"name": name, "parameters": {key: "/tmp/read-only.txt"}},
                },
            },
            files,
        )
    assert files == set()
    collect_tool_paths(
        {
            "event": "step_update",
            "step_update": {
                "step_type": "tool",
                "tool_info": {"name": "replace_file_content", "parameters": {"TargetFile": "/tmp/b.txt"}},
            },
        },
        files,
    )
    assert files == {"/tmp/b.txt"}


def test_agy_model_and_effort_precede_print(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "agent_bridge.adapters.antigravity.resolve_command",
        lambda command, fallbacks=None: ["agy"],
    )
    adapter = AgyAdapter(
        AgentConfig(name="antigravity", protocol="agy", command=["agy"]),
        tmp_path,
    )
    session = Session(
        session_id="sess_m",
        agent="antigravity",
        cwd=str(tmp_path),
        model="gemini-3.7-flash",
        effort="low",
    )
    task = Task(
        task_id="task_m",
        session_id=session.session_id,
        agent="antigravity",
        message="do it",
        cwd=str(tmp_path),
        model="gemini-3.7-flash",
        effort="low",
    )
    cmd = adapter._build_cmd(session, task)
    assert cmd.index("--model") < cmd.index("-p")
    assert cmd.index("--effort") < cmd.index("-p")
    assert cmd.index("--add-dir") < cmd.index("-p")
    assert cmd.index("--new-project") < cmd.index("-p")
    assert cmd[cmd.index("--model") + 1] == "gemini-3.7-flash"
    assert cmd[cmd.index("--effort") + 1] == "low"
    assert cmd[cmd.index("--add-dir") + 1] == str(tmp_path)
    assert "--conversation" not in cmd
    assert "--input-format" in cmd
    assert cmd[cmd.index("-p") + 1] == ""
    assert task.message not in cmd


def test_agy_follow_up_uses_conversation_not_new_project(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "agent_bridge.adapters.antigravity.resolve_command",
        lambda command, fallbacks=None: ["agy"],
    )
    adapter = AgyAdapter(
        AgentConfig(name="antigravity", protocol="agy", command=["agy"]),
        tmp_path,
    )
    session = Session(
        session_id="sess_c",
        agent="antigravity",
        cwd=str(tmp_path),
        native_session_id="conv-1",
    )
    task = Task(
        task_id="task_c",
        session_id=session.session_id,
        agent="antigravity",
        message="again",
        cwd=str(tmp_path),
    )
    cmd = adapter._build_cmd(session, task)
    assert cmd.index("--conversation") < cmd.index("-p")
    assert cmd[cmd.index("--conversation") + 1] == "conv-1"
    assert "--new-project" not in cmd
    assert "--input-format" in cmd
    assert cmd[cmd.index("-p") + 1] == ""
    assert task.message not in cmd


def _agy_adapter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AgyAdapter:
    monkeypatch.setattr(
        "agent_bridge.adapters.antigravity.resolve_command",
        lambda command, fallbacks=None: [sys.executable, str(FAKE_AGY)],
    )
    return AgyAdapter(
        AgentConfig(name="antigravity", protocol="agy", command=["agy"]),
        tmp_path,
    )


@pytest.mark.asyncio
async def test_run_turn_sends_prompt_over_stdin(tmp_path, monkeypatch):
    adapter = _agy_adapter(tmp_path, monkeypatch)
    report = tmp_path / "len.txt"
    monkeypatch.setenv("FAKE_AGY_REPORT", str(report))
    session = Session(session_id="sess_stdin", agent="antigravity", cwd=str(tmp_path))
    task = Task(
        task_id="task_stdin",
        session_id=session.session_id,
        agent="antigravity",
        message="x" * 50000,
        cwd=str(tmp_path),
    )
    result = await adapter.run_turn(session, task)
    assert result.stop_reason == "end_turn"
    assert result.text.startswith("echo:")
    assert report.read_text(encoding="utf-8") == "50000"
    assert session.native_session_id == "conv-fake-agy"
    assert result.usage["scope"] == "turn"


@pytest.mark.asyncio
async def test_run_turn_labels_resumed_usage_as_conversation(tmp_path, monkeypatch):
    adapter = _agy_adapter(tmp_path, monkeypatch)
    session = Session(
        session_id="sess_resume",
        agent="antigravity",
        cwd=str(tmp_path),
        native_session_id="conv-1",
    )
    task = Task(
        task_id="task_resume",
        session_id=session.session_id,
        agent="antigravity",
        message="again",
        cwd=str(tmp_path),
    )
    result = await adapter.run_turn(session, task)
    assert result.stop_reason == "end_turn"
    assert result.usage["scope"] == "conversation"


def test_scoped_usage_leaves_empty_dict_alone():
    assert _scoped_usage({}, True) == {}


@pytest.mark.asyncio
async def test_run_turn_early_exit_reports_stderr(tmp_path, monkeypatch):
    adapter = _agy_adapter(tmp_path, monkeypatch)
    monkeypatch.setenv("FAKE_AGY_MODE", "early_exit")
    session = Session(session_id="sess_early", agent="antigravity", cwd=str(tmp_path))
    task = Task(
        task_id="task_early",
        session_id=session.session_id,
        agent="antigravity",
        message="hello",
        cwd=str(tmp_path),
    )
    result = await adapter.run_turn(session, task)
    assert result.stop_reason == "error"
    assert result.error is not None
    assert "agy exit 3" in result.error
    assert "fake agy refused to start" in result.error
