from pathlib import Path

from agent_bridge.config import load_config
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
