from __future__ import annotations

from pathlib import Path

import pytest

from agent_bridge.adapters.acp import AcpAdapter, _Live
from agent_bridge.config import AgentConfig, load_config
from agent_bridge.models import Session
from agent_bridge.probes import probe_agent
from agent_bridge.zcode_meta import ZCODE_MODE_YOLO

GLM = r"builtin:bigmodel-coding-plan\GLM-5.3"
GLM_TURBO = r"builtin:bigmodel-coding-plan\GLM-5-Turbo"
MODE_OPTION = {
    "id": "mode",
    "currentValue": "build",
    "options": [
        {"value": "plan"},
        {"value": "build"},
        {"value": "edit"},
        {"value": "yolo"},
        {"value": "auto"},
    ],
}
MODEL_OPTION = {
    "id": "model",
    "currentValue": GLM,
    "options": [{"value": GLM}, {"value": GLM_TURBO}],
}
THOUGHT_OPTION = {
    "id": "thought",
    "currentValue": "high",
    "options": [{"value": "low"}, {"value": "high"}, {"value": "max"}],
}


class FakeResponse:
    def __init__(self, config_options=None, session_id=None):
        self.config_options = config_options
        self.session_id = session_id


class FakeConn:
    def __init__(self, config_options=None, reject_ids=(), after_model_options=None):
        self.calls: list[tuple] = []
        self.config_options = list(config_options if config_options is not None else [
            MODE_OPTION, MODEL_OPTION, THOUGHT_OPTION
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


def build(agent_name="zcode", conn=None, session_kwargs=None):
    adapter = AcpAdapter(
        AgentConfig(name=agent_name, protocol="acp", command=["zcode-acp-server"]),
        Path("."),
    )
    live = _Live()
    live.conn = conn or FakeConn()
    live.config_options = list(live.conn.config_options)
    session = Session(
        session_id="sess_zcode",
        agent=agent_name,
        cwd=".",
        native_session_id="zc_1",
        **(session_kwargs or {}),
    )
    return adapter, live, session


@pytest.mark.asyncio
async def test_new_session_is_forced_into_yolo_and_maps_effort():
    adapter, live, session = build(session_kwargs={"effort": "off"})
    await adapter._sync_zcode_selection(live, session)
    assert ("mode", ZCODE_MODE_YOLO) in live.conn.calls
    assert ("thought", "low") in live.conn.calls
    assert live.applied_mode == ZCODE_MODE_YOLO
    assert live.applied_effort == "low"


@pytest.mark.asyncio
async def test_yolo_already_current_is_not_resent():
    conn = FakeConn(config_options=[{**MODE_OPTION, "currentValue": "yolo"}, MODEL_OPTION, THOUGHT_OPTION])
    adapter, live, session = build(conn=conn)
    await adapter._sync_zcode_selection(live, session)
    assert not any(call[0] == "mode" for call in live.conn.calls)
    assert live.applied_mode == ZCODE_MODE_YOLO


@pytest.mark.asyncio
async def test_bare_model_id_is_sent_as_the_advertised_value():
    adapter, live, session = build(session_kwargs={"model": "GLM-5-Turbo", "effort": "max"})
    await adapter._sync_zcode_selection(live, session)
    assert ("model", GLM_TURBO) in live.conn.calls
    assert ("thought", "max") in live.conn.calls
    assert live.applied_model == "GLM-5-Turbo"
    assert live.applied_effort == "max"


@pytest.mark.asyncio
async def test_ambiguous_model_fails_and_names_both_options():
    offered = [r"builtin:plan-a\GLM-5.3", r"builtin:plan-b\GLM-5.3"]
    conn = FakeConn(config_options=[
        MODE_OPTION,
        {"id": "model", "currentValue": offered[0], "options": [{"value": item} for item in offered]},
        THOUGHT_OPTION,
    ])
    adapter, live, session = build(conn=conn, session_kwargs={"model": "GLM-5.3"})
    with pytest.raises(RuntimeError, match=r"more than one"):
        await adapter._sync_zcode_selection(live, session)


@pytest.mark.asyncio
async def test_unknown_model_fails_and_names_the_real_options():
    adapter, live, session = build(session_kwargs={"model": "nope"})
    with pytest.raises(RuntimeError, match=r"GLM-5\.3"):
        await adapter._sync_zcode_selection(live, session)


@pytest.mark.asyncio
async def test_model_switch_reapplies_thought_after_the_level_resets():
    after = [
        {**MODE_OPTION, "currentValue": "yolo"},
        {**MODEL_OPTION, "currentValue": GLM_TURBO},
        {**THOUGHT_OPTION, "currentValue": "low"},
    ]
    conn = FakeConn(after_model_options=after)
    adapter, live, session = build(conn=conn, session_kwargs={"model": "GLM-5-Turbo", "effort": "max"})
    live.applied_model = "GLM-5.3"
    live.applied_effort = "high"
    live.applied_mode = ZCODE_MODE_YOLO
    await adapter._sync_zcode_selection(live, session)
    assert ("model", GLM_TURBO) in live.conn.calls
    assert ("thought", "max") in live.conn.calls
    assert live.applied_effort == "max"


@pytest.mark.asyncio
async def test_unmappable_effort_warns_and_does_not_fail():
    conn = FakeConn(config_options=[MODE_OPTION, MODEL_OPTION, {"id": "thought", "currentValue": "on", "options": [{"value": "on"}]}])
    adapter, live, session = build(conn=conn, session_kwargs={"effort": "max"})
    await adapter._sync_zcode_selection(live, session)
    assert any("no counterpart" in item for item in live.pending_warnings)
    assert not any(call[0] == "thought" for call in live.conn.calls)


@pytest.mark.asyncio
async def test_rejected_thought_only_warns():
    conn = FakeConn(reject_ids={"thought"})
    adapter, live, session = build(conn=conn, session_kwargs={"effort": "low"})
    await adapter._sync_zcode_selection(live, session)
    assert any("rejected thought=low" in item for item in live.pending_warnings)
    assert live.applied_effort != "low"


@pytest.mark.asyncio
async def test_revive_uses_resume():
    adapter, live, _session = build()
    await adapter._call_load_session(live.conn, ".", "zc_1")
    assert ("resume", "zc_1") in live.conn.calls
    assert not any(call[0] == "load" for call in live.conn.calls)


@pytest.mark.asyncio
async def test_non_zcode_agents_are_untouched():
    adapter, live, session = build(agent_name="kimi", session_kwargs={"model": "x", "effort": "high"})
    await adapter._sync_zcode_selection(live, session)
    assert live.conn.calls == []


def test_bundled_config_lists_zcode(tmp_path: Path):
    cfg = load_config(home=tmp_path)
    zcode = cfg.agents["zcode"]
    assert zcode.command == ["zcode-acp-server"]
    assert "ZCODE_BIN" in cfg.env.inherit


@pytest.mark.asyncio
async def test_probe_reports_login_and_model_rules(monkeypatch, tmp_path: Path):
    home = tmp_path / ".zcode"
    (home / "v2").mkdir(parents=True)
    (home / "v2" / "config.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr("agent_bridge.probes.resolve_command", lambda command, fallbacks=None: ["zcode-acp-server"])

    async def version(_executable):
        return "zcode-acp-server 0.49.1"

    monkeypatch.setattr("agent_bridge.probes._version_string", version)
    monkeypatch.setattr(
        "agent_bridge.probes.build_worker_env",
        lambda *args, **kwargs: {"ZCODE_HOME": str(home)},
    )
    row = await probe_agent(AgentConfig(name="zcode", protocol="acp", command=["zcode-acp-server"]))
    assert row["available"] is True
    assert "mode forced to yolo" in row["detail"]
    assert "auth=login" in row["detail"]
    assert "zcode-acp-server" in row["detail"]
