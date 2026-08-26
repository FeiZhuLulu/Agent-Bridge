from __future__ import annotations

import pytest

from agent_bridge.adapters.acp import AcpAdapter, _Live
from agent_bridge.claude_meta import CLAUDE_MODE_BYPASS
from agent_bridge.config import AgentConfig
from agent_bridge.models import Session

MODEL_OPTION = {
    "id": "model",
    "currentValue": "sonnet",
    "options": [
        {"value": "sonnet"},
        {"value": "opus"},
        {"value": "haiku"},
        {"value": "claude-sonnet-4-6"},
    ],
}
EFFORT_OPTION = {
    "id": "effort",
    "currentValue": "medium",
    "options": [
        {"value": "default"},
        {"value": "low"},
        {"value": "medium"},
        {"value": "high"},
        {"value": "xhigh"},
    ],
}
MODE_OPTION = {
    "id": "mode",
    "currentValue": "default",
    "options": [
        {"value": "default"},
        {"value": "acceptEdits"},
        {"value": "plan"},
        {"value": "bypassPermissions"},
    ],
}


class FakeResponse:
    def __init__(self, config_options=None, session_id=None):
        self.config_options = config_options
        self.session_id = session_id


class FakeConn:
    def __init__(self, config_options=None, reject_ids=(), reject_mode=False, after_model_options=None):
        self.calls: list[tuple] = []
        self.config_options = config_options if config_options is not None else [
            MODE_OPTION,
            MODEL_OPTION,
            EFFORT_OPTION,
        ]
        self.reject_ids = set(reject_ids)
        self.reject_mode = reject_mode
        self.after_model_options = after_model_options

    async def set_session_mode(self, session_id, mode_id):
        self.calls.append(("set_mode", mode_id))
        if self.reject_mode:
            raise RuntimeError("mode rejected")
        return FakeResponse()

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


def build(agent_name="claude", conn=None, session_kwargs=None):
    adapter = AcpAdapter(
        AgentConfig(name=agent_name, protocol="acp", command=["claude-agent-acp"]),
        __import__("pathlib").Path("."),
    )
    live = _Live()
    live.conn = conn or FakeConn()
    session = Session(
        session_id="sess_claude",
        agent=agent_name,
        cwd=".",
        native_session_id="ses_abc",
        **(session_kwargs or {}),
    )
    return adapter, live, session


@pytest.mark.asyncio
async def test_new_session_is_forced_into_bypass():
    adapter, live, session = build()
    live.config_options = [MODE_OPTION, MODEL_OPTION, EFFORT_OPTION]
    await adapter._sync_claude_selection(live, session)
    assert ("set_mode", CLAUDE_MODE_BYPASS) in live.conn.calls
    assert live.applied_mode == CLAUDE_MODE_BYPASS


@pytest.mark.asyncio
async def test_mode_already_bypass_is_not_resent():
    adapter, live, session = build()
    live.config_options = [{**MODE_OPTION, "currentValue": CLAUDE_MODE_BYPASS}, MODEL_OPTION]
    await adapter._sync_claude_selection(live, session)
    assert not any(call[0] == "set_mode" for call in live.conn.calls)
    assert live.applied_mode == CLAUDE_MODE_BYPASS


@pytest.mark.asyncio
async def test_missing_bypass_mode_warns_and_still_runs():
    adapter, live, session = build()
    live.config_options = [
        {
            "id": "mode",
            "currentValue": "default",
            "options": [{"value": "default"}, {"value": "acceptEdits"}],
        },
        MODEL_OPTION,
    ]
    await adapter._sync_claude_selection(live, session)
    assert not any(call[0] == "set_mode" for call in live.conn.calls)
    assert live.pending_warnings
    assert "not advertised" in live.pending_warnings[0]


@pytest.mark.asyncio
async def test_rejected_bypass_only_warns():
    conn = FakeConn(reject_mode=True)
    adapter, live, session = build(conn=conn)
    live.config_options = [MODE_OPTION, MODEL_OPTION]
    await adapter._sync_claude_selection(live, session)
    assert live.pending_warnings
    assert "rejected mode=" in live.pending_warnings[0]
    assert live.applied_mode != CLAUDE_MODE_BYPASS


@pytest.mark.asyncio
async def test_model_and_effort_are_applied():
    adapter, live, session = build(session_kwargs={"model": "opus", "effort": "high"})
    live.config_options = [MODE_OPTION, MODEL_OPTION, EFFORT_OPTION]
    await adapter._sync_claude_selection(live, session)
    assert ("model", "opus") in live.conn.calls
    assert ("effort", "high") in live.conn.calls
    assert live.applied_model == "opus"
    assert live.applied_effort == "high"


@pytest.mark.asyncio
async def test_model_already_current_is_not_resent():
    adapter, live, session = build(session_kwargs={"model": "sonnet"})
    live.config_options = [MODE_OPTION, MODEL_OPTION, EFFORT_OPTION]
    await adapter._sync_claude_selection(live, session)
    assert not any(call[0] == "model" for call in live.conn.calls)
    assert live.applied_model == "sonnet"


@pytest.mark.asyncio
async def test_effort_already_current_is_not_resent():
    adapter, live, session = build(session_kwargs={"effort": "medium"})
    live.config_options = [MODE_OPTION, MODEL_OPTION, EFFORT_OPTION]
    await adapter._sync_claude_selection(live, session)
    assert not any(call[0] == "effort" for call in live.conn.calls)
    assert live.applied_effort == "medium"


@pytest.mark.asyncio
async def test_effort_degrades_max_onto_xhigh():
    adapter, live, session = build(session_kwargs={"effort": "max"})
    live.config_options = [MODE_OPTION, MODEL_OPTION, EFFORT_OPTION]
    await adapter._sync_claude_selection(live, session)
    assert ("effort", "xhigh") in live.conn.calls
    assert not any("no counterpart" in item for item in live.pending_warnings)


@pytest.mark.asyncio
async def test_missing_effort_option_warns_and_sends_no_rpc():
    adapter, live, session = build(session_kwargs={"effort": "high"})
    live.config_options = [MODE_OPTION, MODEL_OPTION]
    await adapter._sync_claude_selection(live, session)
    assert not any(call[0] == "effort" for call in live.conn.calls)
    assert any("no counterpart" in item for item in live.pending_warnings)


@pytest.mark.asyncio
async def test_a_rejected_model_fails_the_turn_and_names_the_real_options():
    conn = FakeConn(reject_ids={"model"})
    adapter, live, session = build(conn=conn, session_kwargs={"model": "nope"})
    live.config_options = [MODE_OPTION, MODEL_OPTION, EFFORT_OPTION]
    with pytest.raises(RuntimeError, match="sonnet"):
        await adapter._sync_claude_selection(live, session)


@pytest.mark.asyncio
async def test_model_switch_reapplies_effort_after_variant_reset():
    after = [
        MODE_OPTION,
        {**MODEL_OPTION, "currentValue": "opus"},
        {
            "id": "effort",
            "currentValue": "low",
            "options": [
                {"value": "default"},
                {"value": "low"},
                {"value": "medium"},
                {"value": "high"},
                {"value": "xhigh"},
            ],
        },
    ]
    conn = FakeConn(after_model_options=after)
    adapter, live, session = build(conn=conn, session_kwargs={"model": "opus", "effort": "max"})
    live.applied_model = "sonnet"
    live.applied_effort = "high"
    live.applied_mode = CLAUDE_MODE_BYPASS
    live.config_options = [MODE_OPTION, MODEL_OPTION, EFFORT_OPTION]
    await adapter._sync_claude_selection(live, session)
    assert ("model", "opus") in live.conn.calls
    assert ("effort", "xhigh") in live.conn.calls
    assert live.applied_effort == "xhigh"


@pytest.mark.asyncio
async def test_a_rejected_effort_only_warns():
    conn = FakeConn(reject_ids={"effort"})
    adapter, live, session = build(conn=conn, session_kwargs={"effort": "high"})
    live.applied_mode = CLAUDE_MODE_BYPASS
    live.config_options = [MODE_OPTION, MODEL_OPTION, EFFORT_OPTION]
    await adapter._sync_claude_selection(live, session)
    assert any("rejected effort=high" in item for item in live.pending_warnings)
    assert live.applied_effort != "high"


@pytest.mark.asyncio
async def test_revive_uses_resume_to_skip_history_replay():
    adapter, live, session = build()
    await adapter._call_load_session(live.conn, ".", "ses_abc")
    assert ("resume", "ses_abc") in live.conn.calls
    assert not any(call[0] == "load" for call in live.conn.calls)


@pytest.mark.asyncio
async def test_non_claude_agents_are_untouched_by_the_claude_path():
    adapter, live, session = build(
        agent_name="cursor", session_kwargs={"model": "x", "effort": "high"}
    )
    await adapter._sync_claude_selection(live, session)
    assert live.conn.calls == []
    adapter._remember_config_options(live, FakeResponse(config_options=[MODEL_OPTION]))
    assert live.config_options == []
