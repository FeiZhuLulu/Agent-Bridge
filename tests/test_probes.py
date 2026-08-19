from pathlib import Path

import pytest

from agent_bridge.config import AgentConfig, EnvConfig
from agent_bridge.probes import probe_agent


def _fake_resolve(command, fallbacks=None):
    return list(command)


@pytest.mark.asyncio
async def test_dsh_probe_does_not_require_official_api_key(monkeypatch):
    monkeypatch.setattr("agent_bridge.probes.resolve_dsh_command", _fake_resolve)
    monkeypatch.setattr("agent_bridge.probes.build_worker_env", lambda *args, **kwargs: {})
    monkeypatch.setattr("agent_bridge.dsh_home.settings_text", lambda *args, **kwargs: "")
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
    row = await probe_agent(
        AgentConfig(name="dsh", protocol="acp", command=["dsh-acp-demo"]),
        EnvConfig(discover_proxy=False, inherit=[]),
    )
    assert row["available"] is True
    assert "dsh-model=acme-gateway/acme-large" in row["detail"]


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
