"""Write project-scoped MCP configs for the live coordinator workspace."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LAB = ROOT / "lab"


def resolve_command(root: Path = ROOT) -> tuple[str, list[str]]:
    for relative in (Path(".venv") / "Scripts" / "python.exe", Path(".venv") / "bin" / "python"):
        python = root / relative
        if python.is_file():
            # Keep the venv launcher. Path.resolve() follows the Linux
            # .venv/bin/python -> /usr/bin/python3 symlink and then
            # `python -m agent_bridge` misses the venv site-packages.
            return str(python), ["-m", "agent_bridge"]
    exe = shutil.which("agent-bridge")
    if exe:
        return str(Path(exe).resolve()), []
    raise SystemExit(
        "no checkout .venv python and no agent-bridge on PATH; "
        "run uv sync --extra dev from the Agent Bridge root first"
    )


def _toml_str(value: str) -> str:
    return "'" + value.replace("'", "\\'") + "'"


def write_host_configs(
    lab: Path,
    command: str,
    args: list[str],
    *,
    home: Path | None = None,
) -> list[Path]:
    lab.mkdir(parents=True, exist_ok=True)
    bridge_home = str((home or (lab / "_bridge_home")).resolve())
    env = {"AGENT_BRIDGE_HOME": bridge_home}
    written: list[Path] = []

    zcode = lab / ".zcode" / "config.json"
    zcode.parent.mkdir(parents=True, exist_ok=True)
    zcode.write_text(
        json.dumps(
            {
                "mcp": {
                    "servers": {
                        "agent_bridge": {
                            "type": "stdio",
                            "command": command,
                            "args": args,
                            "timeoutMs": 600000,
                            "env": env,
                        }
                    }
                }
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    written.append(zcode)

    grok = lab / ".grok" / "config.toml"
    grok.parent.mkdir(parents=True, exist_ok=True)
    arg_items = ", ".join(_toml_str(item) for item in args)
    grok.write_text(
        "\n".join(
            [
                "[mcp_servers.agent_bridge]",
                f"command = {_toml_str(command)}",
                f"args = [{arg_items}]",
                "enabled = true",
                "startup_timeout_sec = 30",
                "tool_timeout_sec = 600",
                "",
                "[mcp_servers.agent_bridge.env]",
                f"AGENT_BRIDGE_HOME = {_toml_str(bridge_home)}",
                "",
                "[[permission.rules]]",
                'action = "allow"',
                'tool = "mcp"',
                'pattern = "agent_bridge__*"',
                "",
                "[compat.cursor]",
                "mcps = false",
                "",
                "[compat.claude]",
                "mcps = false",
                "",
            ]
        ),
        encoding="utf-8",
    )
    written.append(grok)

    cursor = lab / ".cursor" / "mcp.json"
    cursor.parent.mkdir(parents=True, exist_ok=True)
    cursor.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "agent-bridge": {
                        "command": command,
                        "args": args,
                        "env": env,
                    }
                }
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    written.append(cursor)

    kimi = lab / ".kimi-code" / "mcp.json"
    kimi.parent.mkdir(parents=True, exist_ok=True)
    kimi.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "agent-bridge": {
                        "command": command,
                        "args": args,
                        "toolTimeoutMs": 600000,
                        "startupTimeoutMs": 60000,
                        "env": env,
                    }
                }
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    written.append(kimi)

    claude_mcp = lab / ".mcp.json"
    claude_mcp.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "agent-bridge": {
                        "type": "stdio",
                        "command": command,
                        "args": args,
                        "timeout": 600000,
                        "env": env,
                    }
                }
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    written.append(claude_mcp)

    claude_settings = lab / ".claude" / "settings.json"
    claude_settings.parent.mkdir(parents=True, exist_ok=True)
    claude_settings.write_text(
        json.dumps(
            {
                "permissions": {
                    "allow": ["mcp__agent-bridge__*"]
                }
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    written.append(claude_settings)
    return written


def ensure_lab_git(lab: Path = LAB) -> None:
    git = shutil.which("git")
    if not git:
        return
    if not (lab / ".git").exists():
        completed = subprocess.run([git, "init"], cwd=str(lab), check=False)
        if completed.returncode != 0:
            return
    head = subprocess.run(
        [git, "rev-parse", "--verify", "HEAD"],
        cwd=str(lab),
        check=False,
        capture_output=True,
    )
    if head.returncode == 0:
        return
    subprocess.run([git, "add", "README.md", "PROMPT.md", ".gitignore"], cwd=str(lab), check=False)
    subprocess.run(
        [git, "commit", "-m", "Initialize the live coordinator workspace."],
        cwd=str(lab),
        check=False,
    )


def main() -> None:
    command, args = resolve_command()
    written = write_host_configs(LAB, command, args)
    ensure_lab_git()
    print(f"lab: {LAB}")
    print(f"command: {command} {' '.join(args)}".rstrip())
    for path in written:
        print(path)


if __name__ == "__main__":
    sys.exit(main())
