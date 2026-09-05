import asyncio
import contextlib
import time
from pathlib import Path

import pytest

from agent_bridge.config import AgentConfig, EnvConfig
from agent_bridge.probes import command_exists, probe_agent
from agent_bridge.worker_env import WORKER_CONTEXT_ENV, WORKER_CONTEXT_VALUE


def _fake_resolve(command, fallbacks=None):
    return list(command)


def _patch_dsh_version(monkeypatch, version=None):
    monkeypatch.setattr("agent_bridge.probes.installed_dsh_version", lambda: version)


@pytest.mark.asyncio
async def test_dsh_probe_does_not_require_official_api_key(monkeypatch):
    monkeypatch.setattr("agent_bridge.probes.resolve_dsh_command", _fake_resolve)
    monkeypatch.setattr("agent_bridge.probes.build_worker_env", lambda *args, **kwargs: {})
    monkeypatch.setattr("agent_bridge.dsh_home.settings_text", lambda *args, **kwargs: "")
    _patch_dsh_version(monkeypatch)
    row = await probe_agent(
        AgentConfig(name="dsh", protocol="acp", command=["dsh-acp-demo"]),
        EnvConfig(discover_proxy=False, inherit=[]),
    )
    assert row["available"] is True
    assert "DEEPSEEK_API_KEY" not in (row["detail"] or "")


@pytest.mark.asyncio
async def test_dsh_probe_reports_user_default_model(tmp_path: Path, monkeypatch):
    (tmp_path / "settings.yaml").write_text(
        """
agent-default-model:
  provider: acme-gateway
  model: acme-large
""",
        encoding="utf-8",
    )
    monkeypatch.setattr("agent_bridge.probes.resolve_dsh_command", _fake_resolve)
    monkeypatch.setattr(
        "agent_bridge.probes.build_worker_env",
        lambda *args, **kwargs: {"DSH_HOME": str(tmp_path)},
    )
    _patch_dsh_version(monkeypatch)
    row = await probe_agent(
        AgentConfig(name="dsh", protocol="acp", command=["dsh-acp-demo"]),
        EnvConfig(discover_proxy=False, inherit=[]),
    )
    assert row["available"] is True
    assert "dsh-model=acme-gateway/acme-large" in row["detail"]


@pytest.mark.asyncio
async def test_dsh_probe_reports_dsh_version_not_node(tmp_path: Path, monkeypatch):
    bin_path = tmp_path / "dsh-acp-demo.js"
    bin_path.write_text("// stub\n", encoding="utf-8")

    async def fake_version(_exe):
        return "v22.0.0"

    monkeypatch.setattr(
        "agent_bridge.probes.resolve_dsh_command",
        lambda command, fallbacks=None: ["node", str(bin_path)],
    )
    monkeypatch.setattr("agent_bridge.probes.build_worker_env", lambda *args, **kwargs: {})
    monkeypatch.setattr("agent_bridge.dsh_home.settings_text", lambda *args, **kwargs: "")
    monkeypatch.setattr("agent_bridge.probes.shutil.which", lambda name: "node" if name == "node" else None)
    monkeypatch.setattr("agent_bridge.probes._version_string", fake_version)
    _patch_dsh_version(monkeypatch, "0.1.0-rc.9")
    row = await probe_agent(
        AgentConfig(name="dsh", protocol="acp", command=["dsh-acp-demo"]),
        EnvConfig(discover_proxy=False, inherit=[]),
    )
    assert row["available"] is True
    assert row["version"] == "0.1.0-rc.9"
    assert "node=v22.0.0" in row["detail"]


@pytest.mark.asyncio
async def test_dsh_probe_keeps_node_version_when_dsh_package_is_missing(monkeypatch):
    async def fake_version(_exe):
        return "v22.0.0"

    monkeypatch.setattr("agent_bridge.probes.resolve_dsh_command", _fake_resolve)
    monkeypatch.setattr("agent_bridge.probes.build_worker_env", lambda *args, **kwargs: {})
    monkeypatch.setattr("agent_bridge.dsh_home.settings_text", lambda *args, **kwargs: "")
    monkeypatch.setattr("agent_bridge.probes._version_string", fake_version)
    _patch_dsh_version(monkeypatch)
    row = await probe_agent(
        AgentConfig(name="dsh", protocol="acp", command=["dsh-acp-demo"]),
        EnvConfig(discover_proxy=False, inherit=[]),
    )
    assert row["available"] is True
    assert row["version"] == "v22.0.0"


async def _probe_kimi(monkeypatch, env):
    monkeypatch.setattr("agent_bridge.probes.resolve_command", _fake_resolve)
    monkeypatch.setattr("agent_bridge.probes.build_worker_env", lambda *args, **kwargs: env)
    return await probe_agent(
        AgentConfig(name="kimi", protocol="acp", command=["kimi", "acp"]),
        EnvConfig(discover_proxy=False, inherit=[]),
    )


@pytest.mark.asyncio
async def test_kimi_probe_reports_oauth_login(tmp_path: Path, monkeypatch):
    creds = tmp_path / "credentials"
    creds.mkdir()
    (creds / "kimi-code.json").write_text("{}", encoding="utf-8")
    row = await _probe_kimi(monkeypatch, {"KIMI_CODE_HOME": str(tmp_path)})
    assert row["available"] is True
    assert "auth=oauth" in row["detail"]
    assert f"kimi-home={tmp_path}" in row["detail"]
    assert "mode forced to yolo" in row["detail"]


@pytest.mark.asyncio
async def test_kimi_probe_flags_a_missing_login_without_hiding_the_agent(tmp_path: Path, monkeypatch):
    """session/new answers auth_required, but that is a warning, not unavailability."""
    row = await _probe_kimi(monkeypatch, {"KIMI_CODE_HOME": str(tmp_path)})
    assert row["available"] is True
    assert "auth=missing (run `kimi login`)" in row["detail"]


@pytest.mark.asyncio
async def test_kimi_probe_accepts_an_api_key_instead(tmp_path: Path, monkeypatch):
    row = await _probe_kimi(
        monkeypatch, {"KIMI_CODE_HOME": str(tmp_path), "MOONSHOT_API_KEY": "sk-x"}
    )
    assert "auth=api-key" in row["detail"]


@pytest.mark.asyncio
async def test_dsh_probe_unavailable_when_launcher_cannot_start(monkeypatch):
    def _boom(command, fallbacks=None):
        raise FileNotFoundError("Cannot find package 'tsx'; npm install --prefix missing")

    monkeypatch.setattr("agent_bridge.probes.resolve_dsh_command", _boom)
    row = await probe_agent(
        AgentConfig(name="dsh", protocol="acp", command=["dsh-acp-demo"]),
        EnvConfig(discover_proxy=False, inherit=[]),
    )
    assert row["available"] is False
    assert "tsx" in (row["detail"] or "")


@pytest.mark.asyncio
async def test_codex_probe_routes_custom_agent_name_by_protocol(monkeypatch):
    captured: dict = {}

    def fake_resolve(command, fallbacks=None, *, env=None):
        captured["env"] = env
        return ["codex-test"]

    async def fake_version(_exe):
        return "codex-cli test"

    worker_env = {"CODEX_CLI_PATH": "desktop-codex"}
    monkeypatch.setattr("agent_bridge.probes.build_worker_env", lambda *args, **kwargs: worker_env)
    monkeypatch.setattr("agent_bridge.probes.resolve_codex_command", fake_resolve)
    monkeypatch.setattr("agent_bridge.probes._version_string", fake_version)
    cfg = AgentConfig(name="codex-alt", protocol="codex", command=["codex"])

    row = await probe_agent(cfg, EnvConfig(discover_proxy=False, inherit=[]))

    assert row["available"] is True
    assert captured["env"] is worker_env
    assert "model=codex slugs" in row["detail"]
    assert command_exists(cfg) is True


@pytest.mark.asyncio
async def test_probe_does_not_inject_worker_context(monkeypatch):
    captured: dict = {}

    def spy(*args, **kwargs):
        from agent_bridge.worker_env import build_worker_env

        captured["kwargs"] = kwargs
        env = build_worker_env(*args, **kwargs)
        captured["env"] = env
        return env

    monkeypatch.delenv(WORKER_CONTEXT_ENV, raising=False)
    monkeypatch.setattr("agent_bridge.probes.build_worker_env", spy)
    monkeypatch.setattr("agent_bridge.probes.resolve_command", _fake_resolve)
    async def fake_version(_exe):
        return "probe"

    monkeypatch.setattr("agent_bridge.probes._version_string", fake_version)
    row = await probe_agent(
        AgentConfig(name="grok", protocol="acp", command=["grok"]),
        EnvConfig(discover_proxy=False, inherit=[]),
    )
    assert row["available"] is True
    assert captured["kwargs"].get("worker_context") in (None, False)
    assert captured["env"].get(WORKER_CONTEXT_ENV) != WORKER_CONTEXT_VALUE


async def _probe_claude(monkeypatch, env):
    monkeypatch.setattr("agent_bridge.probes.resolve_command", _fake_resolve)
    monkeypatch.setattr("agent_bridge.probes.build_worker_env", lambda *args, **kwargs: env)
    return await probe_agent(
        AgentConfig(name="claude", protocol="acp", command=["claude-agent-acp"]),
        EnvConfig(discover_proxy=False, inherit=[]),
    )


@pytest.mark.asyncio
async def test_claude_probe_reports_gateway_auth_without_hiding_the_agent(tmp_path, monkeypatch):
    row = await _probe_claude(
        monkeypatch,
        {
            "CLAUDE_CONFIG_DIR": str(tmp_path),
            "ANTHROPIC_AUTH_TOKEN": "sk-or-x",
            "ANTHROPIC_BASE_URL": "https://openrouter.ai/api",
        },
    )
    assert row["available"] is True
    assert "auth=gateway" in row["detail"]
    assert "claude-agent-acp" in row["detail"]
    assert "bypassPermissions" in row["detail"]
    assert f"claude-home={tmp_path}" in row["detail"]


@pytest.mark.asyncio
async def test_claude_probe_maps_openrouter_key_as_gateway(tmp_path, monkeypatch):
    row = await _probe_claude(
        monkeypatch,
        {"CLAUDE_CONFIG_DIR": str(tmp_path), "OPENROUTER_API_KEY": "sk-or-x"},
    )
    assert row["available"] is True
    assert "auth=gateway" in row["detail"]


@pytest.mark.asyncio
async def test_claude_probe_reports_when_gateway_blanks_anthropic_api_key(tmp_path, monkeypatch):
    row = await _probe_claude(
        monkeypatch,
        {
            "CLAUDE_CONFIG_DIR": str(tmp_path),
            "ANTHROPIC_AUTH_TOKEN": "sk-or-x",
            "ANTHROPIC_BASE_URL": "https://openrouter.ai/api",
            "ANTHROPIC_API_KEY": "sk-ant-x",
        },
    )
    assert row["available"] is True
    assert "auth=gateway" in row["detail"]
    assert "blanked" in row["detail"]


@pytest.mark.asyncio
async def test_claude_probe_keeps_direct_anthropic_key_when_openrouter_is_set(tmp_path, monkeypatch):
    row = await _probe_claude(
        monkeypatch,
        {
            "CLAUDE_CONFIG_DIR": str(tmp_path),
            "OPENROUTER_API_KEY": "sk-or-x",
            "ANTHROPIC_API_KEY": "sk-ant-x",
        },
    )
    assert row["available"] is True
    assert "auth=api-key" in row["detail"]
    assert "auth=gateway" not in row["detail"]


@pytest.mark.asyncio
async def test_claude_probe_does_not_treat_mcp_config_as_oauth(tmp_path, monkeypatch):
    (tmp_path / ".claude.json").write_text(
        '{"mcpServers": {"agent-bridge": {"command": "agent-bridge"}}}',
        encoding="utf-8",
    )
    row = await _probe_claude(monkeypatch, {"CLAUDE_CONFIG_DIR": str(tmp_path)})
    assert row["available"] is True
    assert "auth=oauth" not in row["detail"]
    assert "auth=missing" in row["detail"]


@pytest.mark.asyncio
async def test_probe_agent_resolves_command_off_loop(monkeypatch):
    def slow_resolve(command, fallbacks=None):
        time.sleep(0.3)
        return ["fake-bin"]

    async def fake_version(_exe):
        return "v1"

    monkeypatch.setattr("agent_bridge.probes.resolve_command", slow_resolve)
    monkeypatch.setattr("agent_bridge.probes._version_string", fake_version)
    monkeypatch.setattr("agent_bridge.probes.build_worker_env", lambda *args, **kwargs: {})
    ticks = 0

    async def counter() -> None:
        nonlocal ticks
        while True:
            ticks += 1
            await asyncio.sleep(0.02)

    task = asyncio.create_task(counter())
    try:
        row = await probe_agent(
            AgentConfig(name="grok", protocol="acp", command=["grok"]),
            EnvConfig(discover_proxy=False, inherit=[]),
        )
        assert ticks >= 10
        assert row["available"] is True
        assert row["version"] == "v1"
        assert "command=fake-bin" in row["detail"]
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
