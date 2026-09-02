import pytest

from agent_bridge.adapters.antigravity import (
    AgyAdapter,
    argv_too_long,
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


def test_argv_too_long_only_on_windows():
    long_cmd = ["agy", "-p", "x" * 40000]
    assert isinstance(argv_too_long(long_cmd, platform="win32"), int)
    assert argv_too_long(["agy", "-p", "x" * 30], platform="win32") is None
    assert argv_too_long(long_cmd, platform="linux") is None


@pytest.mark.asyncio
async def test_run_turn_rejects_overlong_argv(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "agent_bridge.adapters.antigravity.resolve_command",
        lambda command, fallbacks=None: ["agy"],
    )
    monkeypatch.setattr(
        "agent_bridge.adapters.antigravity.argv_too_long",
        lambda cmd, **kwargs: 40000,
    )

    def boom(*args, **kwargs):
        raise AssertionError("create_subprocess_exec should not run")

    monkeypatch.setattr(
        "agent_bridge.adapters.antigravity.asyncio.create_subprocess_exec",
        boom,
    )
    adapter = AgyAdapter(
        AgentConfig(name="antigravity", protocol="agy", command=["agy"]),
        tmp_path,
    )
    session = Session(session_id="sess_long", agent="antigravity", cwd=str(tmp_path))
    task = Task(
        task_id="task_long",
        session_id=session.session_id,
        agent="antigravity",
        message="too long",
        cwd=str(tmp_path),
    )
    with pytest.raises(ValueError, match="Shorten the task"):
        await adapter.run_turn(session, task)
