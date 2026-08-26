import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from smoke_claude_coordinator import (
    COORD_FILE,
    DEFAULT_OPENCODE_MODEL,
    collect_tools,
    prepare_lab,
    prompt_for,
)


def test_prompt_requires_mcp_dispatch_to_opencode():
    text = prompt_for(ROOT / "lab", DEFAULT_OPENCODE_MODEL)
    assert "list_agents" in text
    assert "dispatch_task" in text
    assert 'agent="opencode"' in text
    assert DEFAULT_OPENCODE_MODEL in text
    assert COORD_FILE in text
    assert "Do not create" in text


def test_collect_tools_reads_stream_json_names():
    stream = "\n".join(
        [
            '{"type":"assistant","message":{"content":[{"type":"tool_use","name":"mcp__agent-bridge__list_agents"}]}}',
            '{"type":"assistant","message":{"content":[{"type":"tool_use","name":"mcp__agent-bridge__dispatch_task"}]}}',
            '{"type":"result","result":"ok"}',
        ]
    )
    names = collect_tools(stream)
    assert "mcp__agent-bridge__list_agents" in names
    assert "mcp__agent-bridge__dispatch_task" in names


def test_prepare_lab_writes_headless_mcp_trust(tmp_path, monkeypatch):
    monkeypatch.chdir(ROOT)
    lab = tmp_path / "lab"
    mcp_path, bridge_home = prepare_lab(lab, DEFAULT_OPENCODE_MODEL)
    assert mcp_path.is_file()
    assert mcp_path.name == ".mcp.json"
    body = mcp_path.read_text(encoding="utf-8")
    assert "agent-bridge" in body
    assert str(bridge_home) in body
    settings = (lab / ".claude" / "settings.json").read_text(encoding="utf-8")
    assert "enableAllProjectMcpServers" in settings
    assert "mcp__agent-bridge__*" in settings
    assert (lab / "CLAUDE.md").is_file()
    assert (lab / ".claude" / "skills" / "agent-bridge" / "SKILL.md").is_file()
    assert DEFAULT_OPENCODE_MODEL in (lab / "opencode.json").read_text(encoding="utf-8")
