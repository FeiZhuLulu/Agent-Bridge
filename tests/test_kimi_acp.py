from __future__ import annotations

import pytest

from agent_bridge.adapters.acp import AcpAdapter, _Live
from agent_bridge.config import AgentConfig
from agent_bridge.models import Session

# Shape taken from a live `kimi acp` session/new: flat select options, and a
# `thinking` vocabulary of low|high|max on kimi-code/k3-256k.
MODEL_OPTION = {
    "id": "model",
    "currentValue": "kimi-code/k3-256k",
    "options": [{"value": "kimi-code/k3"}, {"value": "kimi-code/k3-256k"}],
}
THINKING_OPTION = {
    "id": "thinking",
    "currentValue": "high",
    "options": [{"value": "low"}, {"value": "high"}, {"value": "max"}],
}


class FakeResponse:
    def __init__(self, config_options=None, session_id=None):
        self.config_options = config_options
        self.session_id = session_id


class FakeConn:
    """Records the config surface calls the adapter makes."""

    def __init__(self, config_options=None, reject_ids=(), after_model_options=None):
        self.calls: list[tuple] = []
        self.config_options = config_options if config_options is not None else [
            MODEL_OPTION,
            THINKING_OPTION,
        ]
        self.reject_ids = set(reject_ids)
        self.after_model_options = after_model_options

    async def set_session_mode(self, session_id, mode_id):
        self.calls.append(("set_mode", mode_id))
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


def build(agent_name="kimi", conn=None, session_kwargs=None):
    adapter = AcpAdapter(
        AgentConfig(name=agent_name, protocol="acp", command=[agent_name]),
        __import__("pathlib").Path("."),
    )
    live = _Live()
    live.conn = conn or FakeConn()
    session = Session(
        session_id="sess_kimi",
        agent=agent_name,
        cwd=".",
        native_session_id="session_abc",
        **(session_kwargs or {}),
    )
    return adapter, live, session


@pytest.mark.asyncio
async def test_new_session_is_forced_into_yolo():
    adapter, live, session = build()
    await adapter._sync_kimi_selection(live, session)
    assert ("set_mode", "yolo") in live.conn.calls
    assert live.applied_mode == "yolo"


@pytest.mark.asyncio
async def test_mode_is_not_reapplied_on_a_later_turn():
    adapter, live, session = build()
    await adapter._sync_kimi_selection(live, session)
    live.conn.calls.clear()
    await adapter._sync_kimi_selection(live, session)
    assert live.conn.calls == []


@pytest.mark.asyncio
async def test_model_and_effort_are_applied():
    adapter, live, session = build(session_kwargs={"model": "kimi-code/k3", "effort": "max"})
    live.config_options = [MODEL_OPTION, THINKING_OPTION]
    await adapter._sync_kimi_selection(live, session)
    assert ("model", "kimi-code/k3") in live.conn.calls
    assert ("thinking", "max") in live.conn.calls
    assert live.applied_model == "kimi-code/k3"
    assert live.applied_effort == "max"


@pytest.mark.asyncio
async def test_model_already_current_is_not_resent():
    adapter, live, session = build(session_kwargs={"model": "kimi-code/k3-256k"})
    live.config_options = [MODEL_OPTION, THINKING_OPTION]
    await adapter._sync_kimi_selection(live, session)
    assert not any(call[0] == "model" for call in live.conn.calls)
    assert live.applied_model == "kimi-code/k3-256k"


@pytest.mark.asyncio
async def test_effort_already_current_is_not_resent():
    """thinking already reads `high`, so effort=high must cost no RPC."""
    adapter, live, session = build(session_kwargs={"effort": "high"})
    live.config_options = [MODEL_OPTION, THINKING_OPTION]
    await adapter._sync_kimi_selection(live, session)
    assert not any(call[0] == "thinking" for call in live.conn.calls)
    assert live.applied_effort == "high"


@pytest.mark.asyncio
async def test_effort_degrades_onto_the_offered_levels():
    """k3-256k publishes no `medium`; Bridge picks `high` rather than warning."""
    adapter, live, session = build(session_kwargs={"effort": "medium"})
    live.config_options = [MODEL_OPTION, {**THINKING_OPTION, "currentValue": "low"}]
    await adapter._sync_kimi_selection(live, session)
    assert ("thinking", "high") in live.conn.calls
    assert live.pending_warnings == []


@pytest.mark.asyncio
async def test_unmappable_effort_warns_and_still_runs():
    adapter, live, session = build(session_kwargs={"effort": "high"})
    live.config_options = [{"id": "thinking", "currentValue": "turbo", "options": [{"value": "turbo"}]}]
    await adapter._sync_kimi_selection(live, session)
    assert not any(call[0] == "thinking" for call in live.conn.calls)
    assert len(live.pending_warnings) == 1
    assert "no counterpart" in live.pending_warnings[0]


@pytest.mark.asyncio
async def test_a_rejected_model_fails_the_turn_and_names_the_real_options():
    conn = FakeConn(reject_ids={"model"})
    adapter, live, session = build(conn=conn, session_kwargs={"model": "kimi-code/nope"})
    live.config_options = [MODEL_OPTION, THINKING_OPTION]
    with pytest.raises(RuntimeError, match="kimi-code/k3-256k"):
        await adapter._sync_kimi_selection(live, session)


@pytest.mark.asyncio
async def test_model_switch_reapplies_thinking_after_vocab_reset():
    after = [
        {**MODEL_OPTION, "currentValue": "kimi-code/k3"},
        {
            "id": "thinking",
            "currentValue": "low",
            "options": [{"value": "low"}, {"value": "high"}, {"value": "max"}],
        },
    ]
    conn = FakeConn(after_model_options=after)
    adapter, live, session = build(
        conn=conn, session_kwargs={"model": "kimi-code/k3", "effort": "max"}
    )
    live.applied_model = "kimi-code/k3-256k"
    live.applied_effort = "max"
    live.applied_mode = "yolo"
    live.config_options = [MODEL_OPTION, THINKING_OPTION]
    await adapter._sync_kimi_selection(live, session)
    assert ("model", "kimi-code/k3") in live.conn.calls
    assert ("thinking", "max") in live.conn.calls
    assert live.applied_effort == "max"


@pytest.mark.asyncio
async def test_a_rejected_thinking_level_only_warns():
    """Bridge chose the level by mapping, so its rejection is not the task's fault."""
    conn = FakeConn(reject_ids={"thinking"})
    adapter, live, session = build(conn=conn, session_kwargs={"effort": "max"})
    live.config_options = [MODEL_OPTION, THINKING_OPTION]
    await adapter._sync_kimi_selection(live, session)
    assert len(live.pending_warnings) == 1
    assert "rejected thinking=max" in live.pending_warnings[0]
    assert live.applied_effort != "max"


@pytest.mark.asyncio
async def test_revive_uses_resume_to_skip_history_replay():
    adapter, live, _session = build()
    await adapter._call_load_session(live.conn, ".", "session_abc")
    assert ("resume", "session_abc") in live.conn.calls
    assert not any(call[0] == "load" for call in live.conn.calls)


@pytest.mark.asyncio
async def test_other_agents_still_use_load_session():
    adapter, live, _session = build(agent_name="grok")
    await adapter._call_load_session(live.conn, ".", "session_abc")
    assert ("load", "session_abc") in live.conn.calls


@pytest.mark.asyncio
async def test_non_kimi_agents_are_untouched_by_the_kimi_path():
    adapter, live, session = build(
        agent_name="cursor", session_kwargs={"model": "x", "effort": "high"}
    )
    await adapter._sync_kimi_selection(live, session)
    assert live.conn.calls == []
    adapter._remember_config_options(live, FakeResponse(config_options=[MODEL_OPTION]))
    assert live.config_options == []


@pytest.mark.asyncio
async def test_option_snapshot_is_cached_from_lifecycle_responses():
    adapter, live, _ = build()
    adapter._remember_config_options(live, FakeResponse(config_options=[THINKING_OPTION]))
    assert live.config_options == [THINKING_OPTION]
    # A response without the field must not wipe a good snapshot.
    adapter._remember_config_options(live, FakeResponse())
    assert live.config_options == [THINKING_OPTION]
