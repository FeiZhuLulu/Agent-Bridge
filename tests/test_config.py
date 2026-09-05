import tomllib
from pathlib import Path

import pytest
from pydantic import ValidationError

from agent_bridge.config import (
    AgentConfig,
    AppConfig,
    _coordinator_span,
    load_config,
    write_coordinator_overlay,
)
from agent_bridge.paths import bundled_agents_toml


def test_loads_bundled_agents():
    cfg = load_config(Path("."))
    assert "grok" in cfg.agents
    assert cfg.agents["grok"].protocol == "acp"
    assert cfg.agents["grok"].revivable is True
    assert cfg.agents["cursor"].fallback_commands
    assert cfg.agents["antigravity"].protocol == "agy"
    assert cfg.env.discover_proxy is True
    assert "HTTPS_PROXY" in cfg.env.inherit
    assert "DEEPSEEK_API_KEY" in cfg.env.inherit
    assert "DSH_HOME" in cfg.env.inherit
    assert "OPENCODE_API_KEY" in cfg.env.inherit
    assert cfg.agents["kimi"].protocol == "acp"
    assert cfg.agents["kimi"].command == ["kimi", "acp"]
    assert cfg.agents["kimi"].revivable is True
    assert cfg.agents["opencode"].protocol == "acp"
    assert cfg.agents["opencode"].command == ["opencode", "acp"]
    assert cfg.agents["opencode"].revivable is True
    assert cfg.agents["claude"].protocol == "acp"
    assert cfg.agents["claude"].command == ["claude-agent-acp"]
    assert cfg.agents["claude"].fallback_commands == [["claude-code-acp"]]
    assert cfg.agents["claude"].revivable is True
    assert cfg.agents["codex"].protocol == "codex"
    assert cfg.agents["codex"].command == ["codex"]
    assert cfg.agents["codex"].revivable is True
    assert cfg.agents["codex"].idle_unload_sec == 0
    assert "CODEX_API_KEY" in cfg.env.inherit
    assert "CODEX_CLI_PATH" in cfg.env.inherit
    assert "CODEX_HOME" in cfg.env.inherit
    assert "ANTHROPIC_AUTH_TOKEN" in cfg.env.inherit
    assert "ANTHROPIC_BASE_URL" in cfg.env.inherit
    assert "CLAUDE_CONFIG_DIR" in cfg.env.inherit
    assert "CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY" in cfg.env.inherit
    assert "OPENROUTER_API_KEY" in cfg.env.inherit
    # An explicit [env] inherit in agents.toml replaces DEFAULT_INHERIT_KEYS
    # wholesale, so Kimi's keys have to be listed there too.
    assert "KIMI_CODE_HOME" in cfg.env.inherit
    assert "KIMI_SHELL_PATH" in cfg.env.inherit
    assert cfg.agents["dsh"].command[0] == "dsh-acp-demo"
    assert cfg.agents["dsh"].fallback_commands == []
    assert cfg.agents["dsh"].cwd is None
    bundled = bundled_agents_toml().read_text(encoding="utf-8")
    assert "外源项目库" not in bundled
    assert "E:/" not in bundled
    assert "E:\\" not in bundled


def test_user_overlay(tmp_path):
    (tmp_path / "agents.toml").write_text(
        """
[agents.grok]
idle_unload_sec = 12
""",
        encoding="utf-8",
    )
    cfg = load_config(tmp_path)
    assert cfg.agents["grok"].idle_unload_sec == 12
    assert cfg.agents["grok"].command[0] == "grok"


def test_user_overlay_merges_env(tmp_path):
    (tmp_path / "agents.toml").write_text(
        """
[agents.dsh.env]
HTTP_PROXY = "http://127.0.0.1:7897"
""",
        encoding="utf-8",
    )
    cfg = load_config(tmp_path)
    assert cfg.agents["dsh"].env["DSH_PERMISSION_MODE"] == "danger-full-access"
    assert cfg.agents["dsh"].env["HTTP_PROXY"] == "http://127.0.0.1:7897"


def test_env_overlay_proxy(tmp_path):
    (tmp_path / "agents.toml").write_text(
        """
[env.proxy]
url = "http://127.0.0.1:9"
no_proxy = "localhost"
""",
        encoding="utf-8",
    )
    cfg = load_config(tmp_path)
    assert cfg.env.proxy_url == "http://127.0.0.1:9"
    assert cfg.env.no_proxy == "localhost"
    assert "HTTPS_PROXY" in cfg.env.inherit


def test_fake_agent_opt_in(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_BRIDGE_ENABLE_FAKE", "1")
    cfg = load_config(tmp_path)
    assert "fake" in cfg.agents
    assert cfg.agents["fake"].protocol == "fake"


def test_stall_timeout_default_and_overlay(tmp_path):
    assert AgentConfig(name="x", protocol="fake", command=["x"]).stall_timeout_sec == 1800
    (tmp_path / "agents.toml").write_text(
        "[agents.grok]\nstall_timeout_sec = 60\n",
        encoding="utf-8",
    )
    cfg = load_config(tmp_path)
    assert cfg.agents["grok"].stall_timeout_sec == 60


def test_stall_timeout_rejects_negative(tmp_path):
    (tmp_path / "agents.toml").write_text(
        "[agents.grok]\nstall_timeout_sec = -1\n",
        encoding="utf-8",
    )
    with pytest.raises(ValidationError):
        load_config(tmp_path)


def test_server_idle_exit_defaults(tmp_path):
    assert AppConfig().server.idle_exit_sec == 7200
    cfg = load_config(tmp_path)
    assert cfg.server.idle_exit_sec == 7200


def test_server_idle_exit_overlay(tmp_path):
    (tmp_path / "agents.toml").write_text(
        """
[server]
idle_exit_sec = 30
""",
        encoding="utf-8",
    )
    cfg = load_config(tmp_path)
    assert cfg.server.idle_exit_sec == 30


def test_server_idle_exit_disabled(tmp_path):
    (tmp_path / "agents.toml").write_text(
        """
[server]
idle_exit_sec = 0
""",
        encoding="utf-8",
    )
    cfg = load_config(tmp_path)
    assert cfg.server.idle_exit_sec == 0


def test_coordinator_defaults(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENT_BRIDGE_MODE", raising=False)
    cfg = load_config(tmp_path)
    assert cfg.coordinator.mode == "auto"
    assert cfg.coordinator.instructions == ""


def test_coordinator_overlay_and_safe_alias(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENT_BRIDGE_MODE", raising=False)
    (tmp_path / "agents.toml").write_text(
        """
[coordinator]
mode = "safe"
instructions = "Research goes to antigravity."
""",
        encoding="utf-8",
    )
    cfg = load_config(tmp_path)
    assert cfg.coordinator.mode == "manual"
    assert cfg.coordinator.instructions == "Research goes to antigravity."


def test_coordinator_invalid_mode_falls_back_to_auto(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENT_BRIDGE_MODE", raising=False)
    (tmp_path / "agents.toml").write_text('[coordinator]\nmode = "turbo"\n', encoding="utf-8")
    cfg = load_config(tmp_path)
    assert cfg.coordinator.mode == "auto"


def test_coordinator_env_override_wins_with_yolo_alias(tmp_path, monkeypatch):
    (tmp_path / "agents.toml").write_text('[coordinator]\nmode = "manual"\n', encoding="utf-8")
    monkeypatch.setenv("AGENT_BRIDGE_MODE", "yolo")
    cfg = load_config(tmp_path)
    assert cfg.coordinator.mode == "eager"


def test_write_overlay_creates_file(tmp_path):
    path = write_coordinator_overlay(tmp_path, instructions="Research goes to agy.")
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    assert data["coordinator"]["instructions"] == "Research goes to agy."
    assert "mode" not in data["coordinator"]  # unset keys keep flowing from repo defaults


def test_write_overlay_preserves_other_sections_and_merges(tmp_path):
    (tmp_path / "agents.toml").write_text(
        """# my proxy, do not lose this comment
[env.proxy]
url = "http://127.0.0.1:7897"

[coordinator]
mode = "safe"

[agents.grok]
idle_unload_sec = 12
""",
        encoding="utf-8",
    )
    path = write_coordinator_overlay(tmp_path, instructions="Coding goes to grok.")
    text = path.read_text(encoding="utf-8")
    assert "do not lose this comment" in text
    data = tomllib.loads(text)
    assert data["env"]["proxy"]["url"] == "http://127.0.0.1:7897"
    assert data["agents"]["grok"]["idle_unload_sec"] == 12
    # untouched mode survives (canonicalized from the safe alias), new text lands
    assert data["coordinator"]["mode"] == "manual"
    assert data["coordinator"]["instructions"] == "Coding goes to grok."


def test_write_overlay_multiline_and_backslashes_roundtrip(tmp_path):
    text = 'Ports go to kimi.\nUse E:\\repos\\big "legacy" tree carefully.'
    path = write_coordinator_overlay(tmp_path, instructions=text)
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    assert data["coordinator"]["instructions"].strip() == text


def test_write_overlay_rejects_bad_mode_and_bad_toml(tmp_path):
    with pytest.raises(ValueError, match="unknown coordinator mode"):
        write_coordinator_overlay(tmp_path, mode="turbo")
    (tmp_path / "agents.toml").write_text("[coordinator\nmode=", encoding="utf-8")
    with pytest.raises(ValueError, match="not valid TOML"):
        write_coordinator_overlay(tmp_path, mode="manual")


def test_overlay_survives_bracket_lines_in_instructions(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENT_BRIDGE_MODE", raising=False)
    instructions = "Rules:\n[1] research -> antigravity\n[2] code -> grok"
    write_coordinator_overlay(tmp_path, instructions=instructions)
    data = tomllib.loads((tmp_path / "agents.toml").read_text(encoding="utf-8"))
    assert data["coordinator"]["instructions"].strip() == instructions

    write_coordinator_overlay(tmp_path, mode="eager")
    data = tomllib.loads((tmp_path / "agents.toml").read_text(encoding="utf-8"))
    assert data["coordinator"]["mode"] == "eager"
    assert data["coordinator"]["instructions"].strip() == instructions

    updated = instructions + "\n[3] tests stay local"
    write_coordinator_overlay(tmp_path, instructions=updated)
    data = tomllib.loads((tmp_path / "agents.toml").read_text(encoding="utf-8"))
    cfg = load_config(tmp_path)
    assert data["coordinator"]["instructions"].strip() == updated
    assert cfg.coordinator.mode == "eager"
    assert cfg.coordinator.instructions == updated


def test_overlay_keeps_other_tables_and_comments(tmp_path):
    original = (
        "# proxy comment, do not lose\n"
        "[env.proxy]\n"
        'url = "http://127.0.0.1:9"\n'
        "\n"
        "[coordinator]\n"
        'mode = "auto"\n'
        "\n"
        "# grok comment, keep this too\n"
        "[agents.grok]\n"
        "idle_unload_sec = 12\n"
    )
    (tmp_path / "agents.toml").write_text(original, encoding="utf-8")
    before_span = _coordinator_span(original)
    assert before_span is not None
    write_coordinator_overlay(tmp_path, mode="manual")
    text = (tmp_path / "agents.toml").read_text(encoding="utf-8")
    after_span = _coordinator_span(text)
    assert after_span is not None
    assert text[: after_span[0]] == original[: before_span[0]]
    assert text[after_span[1] :] == original[before_span[1] :]
    assert "# proxy comment, do not lose" in text
    assert "# grok comment, keep this too" in text
    assert text.find("# grok comment, keep this too") < text.find("[agents.grok]")
    data = tomllib.loads(text)
    assert data["env"]["proxy"]["url"] == "http://127.0.0.1:9"
    assert data["agents"]["grok"]["idle_unload_sec"] == 12
    assert data["coordinator"]["mode"] == "manual"


def test_overlay_keeps_hash_inside_multiline_string(tmp_path):
    original = (
        "[coordinator]\n"
        'mode = "auto"\n'
        "instructions = '''\n"
        "# not a comment\n"
        "keep this\n"
        "'''\n"
        "\n"
        "# grok comment, keep this too\n"
        "[agents.grok]\n"
        "idle_unload_sec = 12\n"
    )
    (tmp_path / "agents.toml").write_text(original, encoding="utf-8")
    write_coordinator_overlay(tmp_path, mode="manual")
    text = (tmp_path / "agents.toml").read_text(encoding="utf-8")
    data = tomllib.loads(text)
    assert "# not a comment" in data["coordinator"]["instructions"]
    assert "keep this" in data["coordinator"]["instructions"]
    assert data["coordinator"]["mode"] == "manual"
    assert "# grok comment, keep this too" in text
    assert text.find("# grok comment, keep this too") < text.find("[agents.grok]")
    assert data["agents"]["grok"]["idle_unload_sec"] == 12


def test_overlay_isolates_basic_multiline_string(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENT_BRIDGE_MODE", raising=False)
    (tmp_path / "agents.toml").write_text(
        '[coordinator]\n'
        'mode = "auto"\n'
        'instructions = """\n'
        "[x] one\n"
        "[y] two\n"
        '"""\n'
        "\n"
        "[agents.kimi]\n"
        "idle_unload_sec = 5\n",
        encoding="utf-8",
    )
    write_coordinator_overlay(tmp_path, mode="manual")
    text = (tmp_path / "agents.toml").read_text(encoding="utf-8")
    data = tomllib.loads(text)
    assert "[x] one" in data["coordinator"]["instructions"]
    assert "[y] two" in data["coordinator"]["instructions"]
    assert data["agents"]["kimi"]["idle_unload_sec"] == 5
    assert data["coordinator"]["mode"] == "manual"


def test_overlay_missing_table_appends(tmp_path):
    original = '# keep me\n[env.proxy]\nurl = "http://127.0.0.1:9"\n'
    (tmp_path / "agents.toml").write_text(original, encoding="utf-8")
    write_coordinator_overlay(tmp_path, mode="manual")
    text = (tmp_path / "agents.toml").read_text(encoding="utf-8")
    assert text.startswith(original.rstrip())
    data = tomllib.loads(text)
    assert data["coordinator"]["mode"] == "manual"
    assert data["env"]["proxy"]["url"] == "http://127.0.0.1:9"


def test_load_config_reports_overlay_path_on_bad_toml(tmp_path):
    path = tmp_path / "agents.toml"
    path.write_text("[coordinator\nmode=", encoding="utf-8")
    with pytest.raises(ValueError, match="is not valid TOML") as exc_info:
        load_config(tmp_path)
    assert str(path) in str(exc_info.value)
    assert "Fix or delete the file" in str(exc_info.value)
