from __future__ import annotations

from pathlib import Path

import pytest

from agent_bridge.adapters.acp import AcpAdapter, _Live
from agent_bridge.config import AgentConfig, EnvConfig, load_config
from agent_bridge.devin_meta import DEVIN_MODE_BYPASS, apply_devin_env
from agent_bridge.models import Session
from agent_bridge.probes import probe_agent

# Shape taken from a live `devin acp` session/new (CLI 3000.6.14): the mode
# select starts on accept-edits, and the model select lists the account's ids
# with the level baked into the id — there is no separate effort option.
MODE_OPTION = {
    "id": "mode",
    "currentValue": "accept-edits",
    "options": [
        {"value": "accept-edits"},
        {"value": "smart"},
        {"value": "ask"},
        {"value": "plan"},
        {"value": "bypass"},
    ],
}
MODEL_OPTION = {
    "id": "model",
    "currentValue": "swe-1-7-medium",
    "options": [
        {"value": "swe-1-7"},
        {"value": "swe-1-7-medium"},
        {"value": "claude-opus-5-high"},
    ],
}


class FakeResponse:
    def __init__(self, config_options=None, session_id=None):
        self.config_options = config_options
        self.session_id = session_id


class FakeConn:
    def __init__(self, config_options=None, reject_ids=(), reject_mode=False):
        self.calls: list[tuple] = []
        self.config_options = config_options if config_options is not None else [MODE_OPTION, MODEL_OPTION]
        self.reject_ids = set(reject_ids)
        self.reject_mode = reject_mode

    async def set_session_mode(self, session_id, mode_id):
        self.calls.append(("set_mode", mode_id))
        if self.reject_mode:
            raise RuntimeError("mode rejected")
        return FakeResponse()

    async def set_config_option(self, config_id, session_id, value):
        self.calls.append((config_id, value))
        if config_id in self.reject_ids:
            raise RuntimeError(f"{config_id} rejected")
        return FakeResponse(config_options=self.config_options)

    async def resume_session(self, session_id, cwd, mcp_servers=None):
        self.calls.append(("resume", session_id))
        return FakeResponse(config_options=self.config_options)

    async def load_session(self, cwd, session_id, mcp_servers=None, noReplay=None):
        self.calls.append(("load", session_id))
        return FakeResponse(config_options=self.config_options)


def build(agent_name="devin", conn=None, session_kwargs=None):
    adapter = AcpAdapter(
        AgentConfig(name=agent_name, protocol="acp", command=["devin", "acp"]),
        Path("."),
    )
    live = _Live()
    live.conn = conn or FakeConn()
    live.config_options = [MODE_OPTION, MODEL_OPTION]
    session = Session(
        session_id="sess_devin",
        agent=agent_name,
        cwd=".",
        native_session_id="gamy-today",
        **(session_kwargs or {}),
    )
    return adapter, live, session


@pytest.mark.asyncio
async def test_new_session_is_forced_into_bypass():
    adapter, live, session = build()
    await adapter._sync_devin_selection(live, session)
    assert live.conn.calls == [("set_mode", DEVIN_MODE_BYPASS)]
    assert live.applied_mode == DEVIN_MODE_BYPASS


@pytest.mark.asyncio
async def test_revived_session_already_in_bypass_is_not_resent():
    adapter, live, session = build()
    live.config_options = [{**MODE_OPTION, "currentValue": DEVIN_MODE_BYPASS}, MODEL_OPTION]
    await adapter._sync_devin_selection(live, session)
    assert live.conn.calls == []
    assert live.applied_mode == DEVIN_MODE_BYPASS


@pytest.mark.asyncio
async def test_rejected_bypass_only_warns():
    adapter, live, session = build(conn=FakeConn(reject_mode=True))
    await adapter._sync_devin_selection(live, session)
    assert any("devin rejected mode=bypass" in item for item in live.pending_warnings)
    assert live.applied_mode != DEVIN_MODE_BYPASS


@pytest.mark.asyncio
async def test_model_is_applied_through_the_config_option():
    adapter, live, session = build(session_kwargs={"model": "swe-1-7"})
    await adapter._sync_devin_selection(live, session)
    assert ("model", "swe-1-7") in live.conn.calls
    assert live.applied_model == "swe-1-7"
    assert live.applied_effort is None


@pytest.mark.asyncio
async def test_default_model_named_explicitly_is_not_resent():
    adapter, live, session = build(session_kwargs={"model": "swe-1-7-medium"})
    await adapter._sync_devin_selection(live, session)
    assert not any(call[0] == "model" for call in live.conn.calls)
    assert live.applied_model == "swe-1-7-medium"


@pytest.mark.asyncio
async def test_a_rejected_model_fails_the_turn_and_names_the_real_options():
    adapter, live, session = build(conn=FakeConn(reject_ids={"model"}), session_kwargs={"model": "opus"})
    with pytest.raises(RuntimeError, match=r"devin rejected model 'opus'.*swe-1-7-medium"):
        await adapter._sync_devin_selection(live, session)


@pytest.mark.asyncio
async def test_effort_is_reported_as_ignored_not_mapped():
    adapter, live, session = build(session_kwargs={"effort": "high"})
    await adapter._sync_devin_selection(live, session)
    assert not any(call[0] == "effort" for call in live.conn.calls)
    assert any("effort=high ignored" in item for item in live.pending_warnings)


@pytest.mark.asyncio
async def test_revive_uses_load_because_devin_has_no_resume():
    adapter, live, _session = build()
    await adapter._call_load_session(live.conn, ".", "gamy-today")
    assert live.conn.calls == [("load", "gamy-today")]


@pytest.mark.asyncio
async def test_non_devin_agents_are_untouched_by_the_devin_path():
    adapter, live, session = build(agent_name="cursor", session_kwargs={"model": "x", "effort": "high"})
    await adapter._sync_devin_selection(live, session)
    assert live.conn.calls == []
    assert live.pending_warnings == []


def test_worker_env_drops_the_desktop_host_mark():
    """Devin Desktop stamps ACP_BACKEND on children; with it set the CLI refuses
    session/new until the host calls authenticate. Bridge is not that host."""
    env = apply_devin_env({"ACP_BACKEND": "windsurf", "WINDSURF_API_KEY": "k", "PATH": "p"})
    assert env == {"WINDSURF_API_KEY": "k", "PATH": "p"}


def test_adapter_env_strips_acp_backend(monkeypatch):
    monkeypatch.setenv("ACP_BACKEND", "windsurf")
    adapter = AcpAdapter(
        AgentConfig(name="devin", protocol="acp", command=["devin", "acp"]),
        Path("."),
        EnvConfig(discover_proxy=False, inherit=[]),
    )
    assert "ACP_BACKEND" not in adapter._env()


def test_bundled_config_lists_devin(tmp_path: Path):
    cfg = load_config(home=tmp_path)
    devin = cfg.agents["devin"]
    assert devin.protocol == "acp"
    assert devin.command == ["devin", "acp"]
    assert devin.revivable is True
    assert "WINDSURF_API_KEY" in cfg.env.inherit


@pytest.mark.asyncio
async def test_probe_reports_login_state_and_model_rules(monkeypatch):
    async def fake_auth(executable, env):
        assert "ACP_BACKEND" not in env
        return "Logged in (via Devin)."

    monkeypatch.setattr("agent_bridge.probes.resolve_command", lambda command, fallbacks=None: ["devin", "acp"])
    monkeypatch.setattr(
        "agent_bridge.probes.build_worker_env",
        lambda *args, **kwargs: {"ACP_BACKEND": "windsurf", "WINDSURF_API_KEY": "k"},
    )
    monkeypatch.setattr("agent_bridge.probes._devin_auth", fake_auth)
    row = await probe_agent(
        AgentConfig(name="devin", protocol="acp", command=["devin", "acp"]),
        EnvConfig(discover_proxy=False, inherit=[]),
    )
    assert row["available"] is True
    assert "auth=Logged in (via Devin)." in row["detail"]
    assert "WINDSURF_API_KEY=set" in row["detail"]
    assert "mode forced to bypass" in row["detail"]
    assert "no effort option" in row["detail"]
