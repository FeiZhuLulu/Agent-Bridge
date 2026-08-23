from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_readme_and_setup_list_zcode_and_grok_coordinators():
    readme = _read("README.md")
    setup = _read("SETUP.md")
    for text in (readme, setup):
        assert "ZCode" in text
        assert "Grok Build" in text
        assert "Codex" in text
        assert "Cursor" in text
        assert "Kimi Code" in text


def test_setup_keeps_zcode_ui_and_native_shapes_distinct():
    setup = _read("SETUP.md")
    assert '"mcpServers"' in setup
    assert '"mcp"' in setup
    assert '"servers"' in setup
    assert "mcp.servers" in setup
    assert "完整配置" in setup or "Full configuration" in setup
    assert "on-disk native file is a different object" in setup
    assert "Do **not** paste the dialog JSON" in setup


def test_setup_grok_uses_official_toml_and_permission_rules():
    setup = _read("SETUP.md")
    assert "[mcp_servers.agent_bridge]" in setup
    assert "tool_timeout_sec" in setup
    assert "[[permission.rules]]" in setup
    assert 'tool = "mcp"' in setup
    assert "agent_bridge__*" in setup
    assert "[ui] permission_mode" in setup
    assert "[compat.cursor]" in setup
    assert "docs.x.ai/build/features/mcp-servers" in setup
    assert "docs.x.ai/build/features/permissions" in setup
    assert "docs.x.ai/build/features/skills-plugins-marketplaces" in setup
    assert "cancel_task" in setup
    assert "end_session" in setup
    assert "nested/" in setup
    assert "read-only" not in setup


def test_setup_zcode_points_at_mcp_settings_not_plugin_marketplace():
    setup = _read("SETUP.md")
    assert "zcode.z.ai/cn/docs/mcp-services" in setup
    assert "zcode.z.ai/cn/docs/plugin" in setup
    assert "zcode.z.ai/en/docs/skill" in setup
    assert "MCP 服务器" in setup
    assert "marketplace plugin" in setup


def test_orchestration_english_stays_under_char_budget():
    text = _read("ORCHESTRATION.md")
    assert len(text) <= 9500


def test_live_workspace_is_local_lab_not_pytest():
    readme = _read("README.md")
    setup = _read("SETUP.md")
    contributing = _read("CONTRIBUTING.md")
    for text in (readme, setup, contributing):
        assert "lab/" in text
        assert "scripts/setup_lab.py" in text
        assert "tests/" in text
    assert "not in git" in readme
    assert "not in git" in setup
    assert "pytest" in setup


def test_orchestration_keeps_worker_loop_rules_in_both_languages():
    en = _read("ORCHESTRATION.md")
    zh = _read("ORCHESTRATION.zh-CN.md")
    for text in (en, zh):
        for token in (
            "list_agents",
            "dispatch_task",
            "wait_task",
            "get_result",
            "session_id",
            "end_session",
            "dispatch_enabled",
            "runtime_context",
            "git diff",
        ):
            assert token in text
    assert "timeout" in en.lower()
    assert "超时" in zh
    assert "same `session_id`" in en
    assert "同一个 `session_id`" in zh
    assert "three follow-up" in en
    assert "跟进三轮" in zh
    assert "empty Kimi" in en
    assert "空文本" in zh or "warnings" in zh
    assert "cancel_task" in en
    assert "nested/" in en
    assert "nested/" in zh
