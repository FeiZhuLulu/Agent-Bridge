from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from setup_lab import resolve_command, write_host_configs


def test_resolve_command_prefers_checkout_venv():
    command, args = resolve_command(ROOT)
    assert Path(command).is_file()
    assert ".venv" in Path(command).as_posix()
    if args:
        assert args == ["-m", "agent_bridge"]


def test_write_host_configs_uses_isolated_home(tmp_path: Path):
    lab = tmp_path / "lab"
    written = write_host_configs(lab, r"C:\bridge\python.exe", ["-m", "agent_bridge"])
    by_rel = {path.relative_to(lab).as_posix(): path for path in written}
    zcode = json.loads(by_rel[".zcode/config.json"].read_text(encoding="utf-8"))
    server = zcode["mcp"]["servers"]["agent_bridge"]
    assert server["command"].endswith("python.exe")
    assert server["timeoutMs"] == 600000
    assert server["env"]["AGENT_BRIDGE_HOME"].endswith("_bridge_home")
    grok = by_rel[".grok/config.toml"].read_text(encoding="utf-8")
    assert "agent_bridge__*" in grok
    assert "[ui]" not in grok
    assert "[compat.cursor]" in grok
    assert "mcps = false" in grok
    cursor = json.loads(by_rel[".cursor/mcp.json"].read_text(encoding="utf-8"))
    assert "mcpServers" in cursor
    kimi = json.loads(by_rel[".kimi-code/mcp.json"].read_text(encoding="utf-8"))
    assert kimi["mcpServers"]["agent-bridge"]["toolTimeoutMs"] == 600000
    claude = json.loads(by_rel[".mcp.json"].read_text(encoding="utf-8"))
    assert claude["mcpServers"]["agent-bridge"]["timeout"] == 600000
    assert claude["mcpServers"]["agent-bridge"]["type"] == "stdio"
    settings = json.loads(by_rel[".claude/settings.json"].read_text(encoding="utf-8"))
    assert "mcp__agent-bridge__*" in settings["permissions"]["allow"]
    assert "bypassPermissions" not in settings["permissions"]
