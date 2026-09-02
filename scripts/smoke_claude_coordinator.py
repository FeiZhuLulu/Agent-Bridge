"""Live Claude Code *coordinator* smoke: host MCP dispatches OpenCode.

This is not the worker path (``claude-agent-acp``). Product ``claude`` loads
project ``.mcp.json``, calls Agent Bridge tools, and OpenCode writes the file.

Run from repo root after ``uv sync``, with ``claude`` and ``opencode`` on PATH:

    uv run python scripts/smoke_claude_coordinator.py

Claude Code needs gateway or Anthropic auth (``OPENROUTER_API_KEY`` /
``ANTHROPIC_AUTH_TOKEN``). OpenCode uses the same ``OPENROUTER_API_KEY``.
Pass ``--model`` for the OpenCode slug (default ``openrouter/stealth/ox-alpha``).

The script uses ``lab/`` (not ``tests/``), an isolated ``AGENT_BRIDGE_HOME``,
and ``--mcp-config`` so the host does not need a trusted global ``~/.claude.json``.
It denies Write/Edit/Bash so a pass cannot be the coordinator writing the file.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from setup_lab import LAB, resolve_command, write_host_configs  # noqa: E402

DEFAULT_OPENCODE_MODEL = "openrouter/stealth/ox-alpha"
COORD_FILE = "coord.txt"


def _required_env() -> None:
    if not shutil.which("claude"):
        raise SystemExit("product `claude` is not on PATH; npm i -g @anthropic-ai/claude-code")
    if not shutil.which("opencode"):
        raise SystemExit("`opencode` is not on PATH; npm i -g opencode-ai")
    if not (
        os.environ.get("OPENROUTER_API_KEY", "").strip()
        or os.environ.get("ANTHROPIC_AUTH_TOKEN", "").strip()
        or os.environ.get("ANTHROPIC_API_KEY", "").strip()
    ):
        raise SystemExit("set OPENROUTER_API_KEY or ANTHROPIC_AUTH_TOKEN / ANTHROPIC_API_KEY")


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def prepare_lab(lab: Path, opencode_model: str) -> tuple[Path, Path]:
    command, args = resolve_command(ROOT)
    bridge_home = (lab / "_bridge_home").resolve()
    xdg_data = (lab / "_xdg_data").resolve()
    xdg_config = (lab / "_xdg_config").resolve()
    xdg_data.mkdir(parents=True, exist_ok=True)
    xdg_config.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("XDG_DATA_HOME", str(xdg_data))
    os.environ.setdefault("XDG_CONFIG_HOME", str(xdg_config))
    auth = xdg_data / "opencode" / "auth.json"
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if key:
        _write_json(auth, {"openrouter": {"type": "api", "key": key}})
    written = write_host_configs(lab, command, args, home=bridge_home)
    mcp_path = next(path for path in written if path.name == ".mcp.json")
    mcp = json.loads(mcp_path.read_text(encoding="utf-8"))
    env = mcp["mcpServers"]["agent-bridge"].setdefault("env", {})
    env["AGENT_BRIDGE_HOME"] = str(bridge_home)
    env["PATH"] = os.environ.get("PATH", "")
    venv = ROOT / ".venv"
    if venv.is_dir():
        env["VIRTUAL_ENV"] = str(venv)
    xdg_data_home = os.environ.get("XDG_DATA_HOME")
    xdg_config_home = os.environ.get("XDG_CONFIG_HOME")
    if xdg_data_home:
        env["XDG_DATA_HOME"] = xdg_data_home
    if xdg_config_home:
        env["XDG_CONFIG_HOME"] = xdg_config_home
    # Do not copy API keys into .mcp.json. Claude Code inherits process env.
    _write_json(mcp_path, mcp)

    trust = {
        "permissions": {"allow": ["mcp__agent-bridge__*"]},
        "enableAllProjectMcpServers": True,
        "enabledMcpjsonServers": ["agent-bridge"],
    }
    _write_json(lab / ".claude" / "settings.json", trust)
    # Claude Code persists project-MCP approval in local settings.
    _write_json(lab / ".claude" / "settings.local.json", trust)

    (lab / "CLAUDE.md").write_text(
        "\n".join(
            [
                "# Coordinator rules",
                "",
                "You are the Agent Bridge coordinator in this folder.",
                "Workers are reached only through MCP tools named",
                "`mcp__agent-bridge__list_agents`, `dispatch_task`, `wait_task`,",
                "`get_result`, `list_sessions`, and `end_session`.",
                "Do not run `opencode`, `claude`, or `claude-agent-acp`.",
                "Do not create worker files yourself.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    _write_json(lab / "opencode.json", {"model": opencode_model})
    gitignore = lab / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text("_bridge_home/\n_claude_home/\n", encoding="utf-8")
    return mcp_path, bridge_home


def collect_tools(stream_text: str) -> list[str]:
    names: list[str] = []
    for line in stream_text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        blob = json.dumps(event)
        if "mcp__agent-bridge__" not in blob:
            continue
        message = event.get("message") or event.get("event") or event
        content = []
        if isinstance(message, dict):
            content = message.get("content") or message.get("tool_calls") or []
        if isinstance(event.get("content"), list):
            content = event["content"]
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict):
                    name = item.get("name") or item.get("toolName") or ""
                    if isinstance(name, str) and name.startswith("mcp__agent-bridge__"):
                        names.append(name)
        # Fallback: any tool-shaped name in the line
        for token in (
            "mcp__agent-bridge__list_agents",
            "mcp__agent-bridge__dispatch_task",
            "mcp__agent-bridge__wait_task",
            "mcp__agent-bridge__get_result",
            "mcp__agent-bridge__end_session",
            "mcp__agent-bridge__check_task",
            "mcp__agent-bridge__list_sessions",
        ):
            if token in blob and token not in names:
                names.append(token)
    return names


def run_claude(lab: Path, mcp_path: Path, prompt: str, claude_home: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["CLAUDE_CONFIG_DIR"] = str(claude_home)
    stdout_path = lab / "_claude_coordinator.stream.jsonl"
    stderr_path = lab / "_claude_coordinator.stderr.txt"
    argv = [
        "claude",
        "-p",
        prompt,
        "--mcp-config",
        str(mcp_path),
        "--strict-mcp-config",
        "--permission-mode",
        "bypassPermissions",
        "--dangerously-skip-permissions",
        "--disable-slash-commands",
        "--output-format",
        "stream-json",
        "--verbose",
        "--disallowedTools",
        "Write,Edit,NotebookEdit,Bash,Skill",
        "--allowedTools",
        "mcp__agent-bridge__list_agents,mcp__agent-bridge__dispatch_task,mcp__agent-bridge__wait_task,mcp__agent-bridge__check_task,mcp__agent-bridge__get_result,mcp__agent-bridge__get_transcript,mcp__agent-bridge__list_sessions,mcp__agent-bridge__end_session,Read",
    ]
    print("run:", " ".join(argv[:4]), "...")
    with stdout_path.open("w", encoding="utf-8") as stdout, stderr_path.open("w", encoding="utf-8") as stderr:
        completed = subprocess.run(
            argv,
            cwd=str(lab),
            env=env,
            text=True,
            stdout=stdout,
            stderr=stderr,
            timeout=720,
        )
    completed.stdout = stdout_path.read_text(encoding="utf-8", errors="replace")
    completed.stderr = stderr_path.read_text(encoding="utf-8", errors="replace")
    return completed


def prompt_for(lab: Path, opencode_model: str) -> str:
    target = (lab / COORD_FILE).resolve()
    return f"""You are the coordinator. Users talk only to you.

Use Agent Bridge MCP tools only. The tools are named mcp__agent-bridge__list_agents, mcp__agent-bridge__dispatch_task, mcp__agent-bridge__wait_task, mcp__agent-bridge__get_result, mcp__agent-bridge__list_sessions, mcp__agent-bridge__end_session.

Do not run opencode or any other CLI. Do not create {COORD_FILE} yourself.

1. Call list_agents. Confirm opencode is available and dispatch_enabled is true.
2. dispatch_task with agent="opencode", cwd="{lab.resolve()}", model="{opencode_model}", user_requested=true, message exactly:
Create a file named {COORD_FILE} in the working directory containing exactly the text hello-bridge. Do not do anything else.
3. Loop wait_task until the task is terminal. A timeout is not failure; call wait_task again.
4. get_result.
5. dispatch_task again on the same session_id with message:
Append a second line `round-two` to {COORD_FILE}. Keep the first line unchanged.
6. wait_task until terminal, then get_result, then end_session.

The file on disk that must exist afterwards is {target}. You do not write it.
"""


def bridge_saw_opencode(bridge_home: Path) -> bool:
    state = bridge_home / "state.json"
    if not state.is_file():
        return False
    try:
        payload = json.loads(state.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    if any(session.get("agent") == "opencode" for session in payload.get("sessions") or []):
        return True
    return any(task.get("agent") == "opencode" for task in payload.get("tasks") or [])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lab", type=Path, default=LAB)
    parser.add_argument("--model", default=DEFAULT_OPENCODE_MODEL)
    args = parser.parse_args()
    _required_env()

    lab = args.lab.resolve()
    lab.mkdir(parents=True, exist_ok=True)
    coord = lab / COORD_FILE
    if coord.exists():
        coord.unlink()

    mcp_path, bridge_home = prepare_lab(lab, args.model)
    claude_home = (lab / "_claude_home").resolve()
    claude_home.mkdir(parents=True, exist_ok=True)
    user_cfg = claude_home / ".claude.json"
    existing: dict = {}
    if user_cfg.is_file():
        try:
            existing = json.loads(user_cfg.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            existing = {}
    projects = existing.setdefault("projects", {})
    projects[str(lab)] = {
        **(projects.get(str(lab)) or {}),
        "hasTrustDialogAccepted": True,
        "hasCompletedProjectOnboarding": True,
        "enableAllProjectMcpServers": True,
        "enabledMcpjsonServers": ["agent-bridge"],
    }
    _write_json(user_cfg, existing)

    print("lab", lab)
    print("mcp", mcp_path)
    print("bridge_home", bridge_home)
    print("opencode model", args.model)

    try:
        completed = run_claude(lab, mcp_path, prompt_for(lab, args.model), claude_home)
    except subprocess.TimeoutExpired:
        print("claude timed out")
        completed = subprocess.CompletedProcess(
            args=["claude"],
            returncode=124,
            stdout=(lab / "_claude_coordinator.stream.jsonl").read_text(encoding="utf-8", errors="replace")
            if (lab / "_claude_coordinator.stream.jsonl").is_file()
            else "",
            stderr=(lab / "_claude_coordinator.stderr.txt").read_text(encoding="utf-8", errors="replace")
            if (lab / "_claude_coordinator.stderr.txt").is_file()
            else "",
        )
    out = (completed.stdout or "") + "\n" + (completed.stderr or "")
    print("claude exit", completed.returncode)
    tools = collect_tools(out)
    print("mcp tools seen:", tools)

    text = coord.read_text(encoding="utf-8", errors="replace") if coord.is_file() else ""
    print("coord.txt:", repr(text))
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    saw_list = any(name.endswith("list_agents") for name in tools)
    saw_dispatch = any(name.endswith("dispatch_task") for name in tools)
    saw_wait = any(name.endswith("wait_task") for name in tools)
    opencode_state = bridge_saw_opencode(bridge_home)
    print("bridge opencode session/task:", opencode_state)

    ok = (
        saw_list
        and saw_dispatch
        and saw_wait
        and opencode_state
        and lines[:2] == ["hello-bridge", "round-two"]
    )
    if not ok:
        print("coordinator smoke failed")
        if completed.stdout:
            print("--- stdout tail ---")
            print("\n".join(completed.stdout.splitlines()[-40:]))
        if completed.stderr:
            print("--- stderr tail ---")
            print("\n".join(completed.stderr.splitlines()[-40:]))
        return 1
    print("coordinator smoke passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
