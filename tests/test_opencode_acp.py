from __future__ import annotations

import pytest

from agent_bridge.adapters.acp import AcpAdapter, _Live
from agent_bridge.config import AgentConfig
from agent_bridge.models import Session

# Shape taken from OpenCode ACP session/new: model is provider/model,
# effort is that model's variants and may be omitted entirely.
MODEL_OPTION = {
    "id": "model",
    "currentValue": "opencode/gpt-5",
    "options": [
        {"value": "opencode/gpt-5"},
        {"value": "opencode/grok-code"},
        {"value": "xai/grok-4"},
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
    ],
}


class FakeResponse:
    def __init__(self, config_options=None, session_id=None):
        self.config_options = config_options
        self.session_id = session_id


class FakeConn:
    def __init__(self, config_options=None, reject_ids=()):
        self.calls: list[tuple] = []
        self.config_options = config_options if config_options is not None else [
            MODEL_OPTION,
            EFFORT_OPTION,
        ]
        self.reject_ids = set(reject_ids)

    async def set_session_mode(self, session_id, mode_id):
        self.calls.append(("set_mode", mode_id))
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


def build(agent_name="opencode", conn=None, session_kwargs=None):
    adapter = AcpAdapter(
        AgentConfig(name=agent_name, protocol="acp", command=[agent_name]),
        __import__("pathlib").Path("."),
    )
    live = _Live()
    live.conn = conn or FakeConn()
    session = Session(
        session_id="sess_opencode",
        agent=agent_name,
        cwd=".",
        native_session_id="ses_abc",
        **(session_kwargs or {}),
    )
    return adapter, live, session


@pytest.mark.asyncio
async def test_does_not_force_a_mode():
    adapter, live, session = build()
    await adapter._sync_opencode_selection(live, session)
    assert not any(call[0] == "set_mode" for call in live.conn.calls)


@pytest.mark.asyncio
async def test_model_and_effort_are_applied():
    adapter, live, session = build(session_kwargs={"model": "xai/grok-4", "effort": "high"})
    live.config_options = [MODEL_OPTION, EFFORT_OPTION]
    await adapter._sync_opencode_selection(live, session)
    assert ("model", "xai/grok-4") in live.conn.calls
    assert ("effort", "high") in live.conn.calls
    assert live.applied_model == "xai/grok-4"
    assert live.applied_effort == "high"


@pytest.mark.asyncio
async def test_model_already_current_is_not_resent():
    adapter, live, session = build(session_kwargs={"model": "opencode/gpt-5"})
    live.config_options = [MODEL_OPTION, EFFORT_OPTION]
    await adapter._sync_opencode_selection(live, session)
    assert not any(call[0] == "model" for call in live.conn.calls)
    assert live.applied_model == "opencode/gpt-5"


@pytest.mark.asyncio
async def test_effort_already_current_is_not_resent():
    adapter, live, session = build(session_kwargs={"effort": "medium"})
    live.config_options = [MODEL_OPTION, EFFORT_OPTION]
    await adapter._sync_opencode_selection(live, session)
    assert not any(call[0] == "effort" for call in live.conn.calls)
    assert live.applied_effort == "medium"


@pytest.mark.asyncio
async def test_effort_degrades_max_onto_high():
    adapter, live, session = build(session_kwargs={"effort": "max"})
    live.config_options = [MODEL_OPTION, EFFORT_OPTION]
    await adapter._sync_opencode_selection(live, session)
    assert ("effort", "high") in live.conn.calls
    assert live.pending_warnings == []


@pytest.mark.asyncio
async def test_missing_effort_option_warns_and_sends_no_rpc():
    adapter, live, session = build(session_kwargs={"effort": "high"})
    live.config_options = [MODEL_OPTION]
    await adapter._sync_opencode_selection(live, session)
    assert not any(call[0] == "effort" for call in live.conn.calls)
    assert len(live.pending_warnings) == 1
    assert "no counterpart" in live.pending_warnings[0]


@pytest.mark.asyncio
async def test_a_rejected_model_fails_the_turn_and_names_the_real_options():
    conn = FakeConn(reject_ids={"model"})
    adapter, live, session = build(conn=conn, session_kwargs={"model": "opencode/nope"})
    live.config_options = [MODEL_OPTION, EFFORT_OPTION]
    with pytest.raises(RuntimeError, match="opencode/gpt-5"):
        await adapter._sync_opencode_selection(live, session)


@pytest.mark.asyncio
async def test_a_rejected_effort_only_warns():
    conn = FakeConn(reject_ids={"effort"})
    adapter, live, session = build(conn=conn, session_kwargs={"effort": "high"})
    live.config_options = [MODEL_OPTION, EFFORT_OPTION]
    await adapter._sync_opencode_selection(live, session)
    assert len(live.pending_warnings) == 1
    assert "rejected effort=high" in live.pending_warnings[0]
    assert live.applied_effort != "high"


@pytest.mark.asyncio
async def test_revive_uses_resume_to_skip_history_replay():
    adapter, live, session = build()
    await adapter._call_load_session(live.conn, ".", "ses_abc")
    assert ("resume", "ses_abc") in live.conn.calls
    assert not any(call[0] == "load" for call in live.conn.calls)


@pytest.mark.asyncio
async def test_other_agents_still_use_load_session():
    adapter, live, session = build(agent_name="grok")
    await adapter._call_load_session(live.conn, ".", "ses_abc")
    assert ("load", "ses_abc") in live.conn.calls


@pytest.mark.asyncio
async def test_non_opencode_agents_are_untouched_by_the_opencode_path():
    adapter, live, session = build(
        agent_name="cursor", session_kwargs={"model": "x", "effort": "high"}
    )
    await adapter._sync_opencode_selection(live, session)
    assert live.conn.calls == []
    adapter._remember_config_options(live, FakeResponse(config_options=[MODEL_OPTION]))
    assert live.config_options == []


@pytest.mark.asyncio
async def test_option_snapshot_is_cached_from_lifecycle_responses():
    adapter, live, _ = build()
    adapter._remember_config_options(live, FakeResponse(config_options=[EFFORT_OPTION]))
    assert live.config_options == [EFFORT_OPTION]
    adapter._remember_config_options(live, FakeResponse())
    assert live.config_options == [EFFORT_OPTION]
