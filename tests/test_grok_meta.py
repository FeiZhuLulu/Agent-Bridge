from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent_bridge.adapters.acp import (
    AcpAdapter,
    _Live,
    dsh_needs_respawn,
    grok_session_meta,
    with_grok_cli_selection,
)
from agent_bridge.config import AgentConfig
from agent_bridge.models import Session, grok_effort


def test_grok_cli_flags_precede_stdio():
    cmd = with_grok_cli_selection(
        ["grok", "--no-auto-update", "agent", "--always-approve", "--no-leader", "stdio"],
        "grok-4.5",
        "low",
    )
    assert cmd.index("--model") < cmd.index("stdio")
    assert cmd.index("--effort") < cmd.index("stdio")
    assert cmd[cmd.index("--model") + 1] == "grok-4.5"
    assert cmd[cmd.index("--effort") + 1] == "low"


def test_dsh_needs_respawn_when_model_changes():
    assert dsh_needs_respawn("mimo/mimo-v2.5-free", "max", "opencode/mimo-v2.5-free", "max")
    assert not dsh_needs_respawn(
        "opencode/mimo-v2.5-free",
        "max",
        "opencode/mimo-v2.5-free",
        "max",
    )
    assert dsh_needs_respawn("deepseek-official/deepseek-v4-flash", "off", "deepseek-official/deepseek-v4-flash", "high")


def test_grok_effort_maps_bridge_tokens():
    assert grok_effort(None) is None
    assert grok_effort("off") == "none"
    assert grok_effort("low") == "low"
    assert grok_effort("medium") == "medium"
    assert grok_effort("high") == "high"
    # "max" is not in any current Grok model catalog; xhigh is the real top tier.
    assert grok_effort("max") == "xhigh"


def test_grok_session_meta_keeps_yolo_and_adds_model_effort():
    base = {"yoloMode": True}
    meta = grok_session_meta(base, model="grok-4", effort="high")
    assert meta["yoloMode"] is True
    assert meta["modelId"] == "grok-4"
    assert meta["reasoningEffort"] == "high"
    assert base == {"yoloMode": True}


def test_grok_session_meta_maps_off_to_none_without_inventing_model():
    meta = grok_session_meta({"yoloMode": True}, model=None, effort="off")
    assert meta == {"yoloMode": True, "reasoningEffort": "none"}
    assert "modelId" not in meta


def test_grok_session_meta_empty_keeps_yolo_only():
    assert grok_session_meta({"yoloMode": True}, None, None) == {"yoloMode": True}


def test_adapter_new_session_meta_is_grok_only(tmp_path):
    grok = AcpAdapter(
        AgentConfig(name="grok", protocol="acp", command=["grok"], session_meta={"yoloMode": True}),
        tmp_path,
    )
    cursor = AcpAdapter(
        AgentConfig(name="cursor", protocol="acp", command=["cursor-agent"]),
        tmp_path,
    )
    session = Session(
        session_id="sess_g",
        agent="grok",
        cwd=str(tmp_path),
        model="grok-4",
        effort="low",
    )
    assert grok._new_session_meta(session) == {
        "yoloMode": True,
        "modelId": "grok-4",
        "reasoningEffort": "low",
    }
    session.agent = "cursor"
    assert cursor._new_session_meta(session) == {}


@pytest.mark.asyncio
async def test_grok_new_session_then_set_model(tmp_path, monkeypatch):
    adapter = AcpAdapter(
        AgentConfig(name="grok", protocol="acp", command=["grok"], session_meta={"yoloMode": True}),
        tmp_path,
    )
    calls: list[tuple[str, dict]] = []

    class FakeConn:
        async def send_request(self, method: str, params: dict) -> None:
            calls.append((method, params))

    live = _Live()
    live.proc = SimpleNamespace(returncode=None)
    live.conn = SimpleNamespace(_conn=FakeConn())

    async def fake_spawn(session: Session) -> _Live:
        adapter._live[session.session_id] = live
        return live

    async def fake_new_session(conn, cwd, meta):
        assert meta["yoloMode"] is True
        assert meta["modelId"] == "grok-4.5"
        assert meta["reasoningEffort"] == "low"
        return SimpleNamespace(session_id="native-new")

    monkeypatch.setattr(adapter, "_spawn", fake_spawn)
    monkeypatch.setattr(adapter, "_call_new_session", fake_new_session)
    session = Session(
        session_id="sess_new",
        agent="grok",
        cwd=str(tmp_path),
        model="grok-4.5",
        effort="low",
    )
    await adapter.ensure_session(session)
    assert session.native_session_id == "native-new"
    assert calls == [
        (
            "session/setModel",
            {
                "sessionId": "native-new",
                "modelId": "grok-4.5",
                "_meta": {"reasoningEffort": "low"},
            },
        )
    ]
    await adapter.ensure_session(session)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_live_grok_session_calls_set_model_when_slug_changes(tmp_path):
    adapter = AcpAdapter(
        AgentConfig(name="grok", protocol="acp", command=["grok"], session_meta={"yoloMode": True}),
        tmp_path,
    )
    calls: list[tuple[str, dict]] = []

    class FakeConn:
        async def send_request(self, method: str, params: dict) -> None:
            calls.append((method, params))

    live = _Live()
    live.proc = SimpleNamespace(returncode=None)
    live.conn = SimpleNamespace(_conn=FakeConn())
    live.applied_model = None
    live.applied_effort = None
    session = Session(
        session_id="sess_g",
        agent="grok",
        cwd=str(tmp_path),
        native_session_id="native-1",
        model="grok-4",
        effort="max",
    )
    adapter._live[session.session_id] = live
    await adapter.ensure_session(session)
    assert calls == [
        (
            "session/setModel",
            {"sessionId": "native-1", "modelId": "grok-4", "_meta": {"reasoningEffort": "xhigh"}},
        )
    ]
    await adapter.ensure_session(session)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_live_grok_effort_without_model_does_not_invent_set_model(tmp_path):
    adapter = AcpAdapter(
        AgentConfig(name="grok", protocol="acp", command=["grok"], session_meta={"yoloMode": True}),
        tmp_path,
    )
    calls: list[tuple[str, dict]] = []

    class FakeConn:
        async def send_request(self, method: str, params: dict) -> None:
            calls.append((method, params))

    live = _Live()
    live.proc = SimpleNamespace(returncode=None)
    live.conn = SimpleNamespace(_conn=FakeConn())
    session = Session(
        session_id="sess_e",
        agent="grok",
        cwd=str(tmp_path),
        native_session_id="native-2",
        effort="high",
    )
    adapter._live[session.session_id] = live
    await adapter.ensure_session(session)
    assert calls == []
    assert live.pending_warnings and "ignored" in live.pending_warnings[0]


@pytest.mark.asyncio
async def test_grok_set_model_falls_back_when_camel_method_missing(tmp_path):
    from acp.exceptions import RequestError

    adapter = AcpAdapter(
        AgentConfig(name="grok", protocol="acp", command=["grok"]),
        tmp_path,
    )
    methods: list[str] = []

    class FakeConn:
        async def send_request(self, method: str, params: dict) -> None:
            methods.append(method)
            if method == "session/setModel":
                raise RequestError.method_not_found(method)

    await adapter._call_grok_set_model(SimpleNamespace(_conn=FakeConn()), "sid", "grok-4", "low")
    assert methods == ["session/setModel", "session/set_model"]
