from __future__ import annotations

from pathlib import Path

import pytest

from agent_bridge.adapters.acp import AcpAdapter, _Live
from agent_bridge.config import AgentConfig, load_config
from agent_bridge.minimax_meta import MINIMAX_PERMISSION_BYPASS, encode_minimax_model
from agent_bridge.models import Session
from agent_bridge.probes import probe_agent

PLAIN = encode_minimax_model("minimax", "MiniMax-M2.7")
VARIANT = encode_minimax_model("minimax", "MiniMax-M3", "1M")
PERMISSION_OPTION = {
    "id": "permissionMode",
    "currentValue": "default",
    "options": [
        {"value": "default"},
        {"value": "auto"},
        {"value": "bypassPermissions"},
    ],
}
MODEL_OPTION = {
    "id": "model",
    "currentValue": PLAIN,
    "options": [{"value": PLAIN}, {"value": VARIANT}],
}
EFFORT_OPTION = {
    "id": "thinkingEffort",
    "currentValue": "default",
    "options": [
        {"value": "default"},
        {"value": "low"},
        {"value": "medium"},
        {"value": "high"},
        {"value": "xhigh"},
        {"value": "max"},
    ],
}


class FakeResponse:
    def __init__(self, config_options=None, session_id=None):
        self.config_options = config_options
        self.session_id = session_id


class FakeConn:
    def __init__(self, config_options=None, reject_ids=(), after_model_options=None):
        self.calls: list[tuple] = []
        self.config_options = list(config_options if config_options is not None else [
            PERMISSION_OPTION, MODEL_OPTION, EFFORT_OPTION
        ])
        self.reject_ids = set(reject_ids)
        self.after_model_options = after_model_options

    async def set_config_option(self, config_id, session_id, value):
        self.calls.append((config_id, value))
        if config_id in self.reject_ids:
            raise RuntimeError(f"{config_id} rejected")
        if config_id == "model" and self.after_model_options is not None:
            self.config_options = self.after_model_options
        return FakeResponse(config_options=self.config_options)

    async def resume_session(self, session_id, cwd, mcp_servers=None):
        self.calls.append(("resume", session_id))
        return FakeResponse(config_options=self.config_options)

    async def load_session(self, cwd, session_id, mcp_servers=None, noReplay=None):
        self.calls.append(("load", session_id))
        return FakeResponse(config_options=self.config_options)


def build(agent_name="minimax", conn=None, session_kwargs=None):
    adapter = AcpAdapter(
        AgentConfig(name=agent_name, protocol="acp", command=["mcode", "acp"]),
        Path("."),
    )
    live = _Live()
    live.conn = conn or FakeConn()
    live.config_options = list(live.conn.config_options)
    session = Session(
        session_id="sess_minimax",
        agent=agent_name,
        cwd=".",
        native_session_id="mm_1",
        **(session_kwargs or {}),
    )
    return adapter, live, session


@pytest.mark.asyncio
async def test_permission_is_forced_to_bypass_and_effort_maps():
    adapter, live, session = build(session_kwargs={"effort": "high"})
    await adapter._sync_minimax_selection(live, session)
    assert ("permissionMode", MINIMAX_PERMISSION_BYPASS) in live.conn.calls
    assert ("thinkingEffort", "high") in live.conn.calls
    assert live.applied_mode == MINIMAX_PERMISSION_BYPASS
    assert live.applied_effort == "high"


@pytest.mark.asyncio
async def test_human_model_is_sent_encoded():
    adapter, live, session = build(session_kwargs={"model": "minimax/MiniMax-M3#1M", "effort": "max"})
    await adapter._sync_minimax_selection(live, session)
    assert ("model", VARIANT) in live.conn.calls
    assert ("thinkingEffort", "max") in live.conn.calls
    assert live.applied_model == "minimax/MiniMax-M3#1M"


@pytest.mark.asyncio
async def test_unknown_model_fails_and_names_human_labels():
    adapter, live, session = build(session_kwargs={"model": "nope/nope"})
    with pytest.raises(RuntimeError, match=r"minimax/MiniMax-M2\.7"):
        await adapter._sync_minimax_selection(live, session)


@pytest.mark.asyncio
async def test_model_switch_reapplies_thinking_effort():
    after = [
        {**PERMISSION_OPTION, "currentValue": MINIMAX_PERMISSION_BYPASS},
        {**MODEL_OPTION, "currentValue": VARIANT},
        {**EFFORT_OPTION, "currentValue": "low"},
    ]
    conn = FakeConn(after_model_options=after)
    adapter, live, session = build(
        conn=conn, session_kwargs={"model": "minimax/MiniMax-M3#1M", "effort": "high"}
    )
    live.applied_model = "minimax/MiniMax-M2.7"
    live.applied_effort = "max"
    live.applied_mode = MINIMAX_PERMISSION_BYPASS
    await adapter._sync_minimax_selection(live, session)
    assert ("model", VARIANT) in live.conn.calls
    assert ("thinkingEffort", "high") in live.conn.calls
    assert live.applied_effort == "high"


@pytest.mark.asyncio
async def test_rejected_effort_only_warns():
    conn = FakeConn(reject_ids={"thinkingEffort"})
    adapter, live, session = build(conn=conn, session_kwargs={"effort": "high"})
    await adapter._sync_minimax_selection(live, session)
    assert any("rejected thinkingEffort=high" in item for item in live.pending_warnings)
    assert live.applied_effort != "high"


@pytest.mark.asyncio
async def test_missing_permission_option_warns():
    conn = FakeConn(config_options=[MODEL_OPTION, EFFORT_OPTION])
    adapter, live, session = build(conn=conn)
    await adapter._sync_minimax_selection(live, session)
    assert any("bypassPermissions is not advertised" in item for item in live.pending_warnings)


@pytest.mark.asyncio
async def test_revive_uses_resume():
    adapter, live, _session = build()
    await adapter._call_load_session(live.conn, ".", "mm_1")
    assert ("resume", "mm_1") in live.conn.calls


@pytest.mark.asyncio
async def test_non_minimax_agents_are_untouched():
    adapter, live, session = build(agent_name="opencode", session_kwargs={"model": "x", "effort": "high"})
    await adapter._sync_minimax_selection(live, session)
    assert live.conn.calls == []


def test_bundled_config_lists_minimax(tmp_path: Path):
    cfg = load_config(home=tmp_path)
    minimax = cfg.agents["minimax"]
    assert minimax.command == ["mcode", "acp"]
    assert "MINIMAX_DATA_DIR" in cfg.env.inherit


@pytest.mark.asyncio
async def test_probe_reports_login_and_model_rules(monkeypatch, tmp_path: Path):
    home = tmp_path / "minimax"
    auth = home / "auth" / "prod" / "cn"
    auth.mkdir(parents=True)
    (auth / "auth.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr("agent_bridge.probes.resolve_command", lambda command, fallbacks=None: ["mcode", "acp"])

    async def version(_executable):
        return "mcode 0.5.7"

    monkeypatch.setattr("agent_bridge.probes._version_string", version)
    monkeypatch.setattr(
        "agent_bridge.probes.build_worker_env",
        lambda *args, **kwargs: {"MINIMAX_DATA_DIR": str(home)},
    )
    row = await probe_agent(AgentConfig(name="minimax", protocol="acp", command=["mcode", "acp"]))
    assert row["available"] is True
    assert "bypassPermissions" in row["detail"]
    assert "auth=oauth" in row["detail"]
    assert f"minimax-home={home}" in row["detail"]
