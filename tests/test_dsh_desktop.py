from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from agent_bridge.adapters import dsh_desktop
from agent_bridge.adapters.fake import FakeAdapter
from agent_bridge.models import Session, Task
from agent_bridge.registry import Registry


@pytest.mark.asyncio
async def test_desktop_rpc_uses_official_connection_envelope():
    seen = []
    async def handler(request):
        seen.append(request)
        body = json.loads(request.content)
        assert body["type"] == "client-request"
        assert body["method"] == "session/list"
        assert body["payload"] == {"args": {"_request": {}}}
        return httpx.Response(200, json={"type": "server-response", "rpcId": body["rpcId"],
                                         "result": {"ok": True, "value": {"items": []}}})
    client = dsh_desktop.DesktopClient("http://127.0.0.1:19387", "dsh-auth=test")
    await client.http.aclose()
    client.http = httpx.AsyncClient(transport=httpx.MockTransport(handler), headers={"Cookie": client.cookie})
    try:
        assert await client.rpc("session/list", {}, argument="_request") == {"items": []}
        assert seen[0].headers["cookie"] == "dsh-auth=test"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_desktop_turn_uses_official_create_follow_prompt(monkeypatch, tmp_path):
    calls = []
    queue = asyncio.Queue()

    class Client:
        origin = "http://127.0.0.1:19387"
        cookie = "dsh-auth=test"

        async def rpc(self, endpoint, request, **_kwargs):
            calls.append((endpoint, request))
            if endpoint == "session/create":
                return {"sessionId": "native-1"}
            if endpoint == "session/prompt":
                events = [
                    {"type": "turn/start", "data": {"turn": 1}},
                    {"type": "user/message", "data": {"source": {"rpcId": request["requestId"]}}},
                    {"type": "assistant/message", "data": {"message": {"content": [
                        {"type": "text", "text": "done"}]}}},
                    {"type": "turn/end", "data": {"turn": 1, "reason": {"kind": "completed"}}},
                ]
                for event in events:
                    await queue.put(json.dumps({"type": "item", "streamId": socket.stream_id,
                                                "value": {"type": "event", "event": event}}))
            return {"accepted": True}

    class Socket:
        stream_id = None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def send(self, text):
            self.stream_id = json.loads(text)["streamId"]
            await queue.put(json.dumps({"type": "item", "streamId": self.stream_id,
                                        "value": {"type": "snapshot"}}))

        async def recv(self):
            return await queue.get()

    socket = Socket()
    connect_kwargs = {}

    def fake_connect(*_args, **kwargs):
        connect_kwargs.update(kwargs)
        return socket

    monkeypatch.setattr(dsh_desktop.websockets, "connect", fake_connect)
    adapter = dsh_desktop.DesktopAdapter(SimpleNamespace(name="dsh"), tmp_path)
    adapter._client = Client()
    session = Session(session_id="bridge-1", agent="dsh", backend="desktop", cwd=str(tmp_path))
    task = Task(task_id="task-1", session_id="bridge-1", agent="dsh", cwd=str(tmp_path), message="check")
    result = await asyncio.wait_for(adapter.run_turn(session, task), 2)
    assert result.text == "done"
    assert result.native_session_id == "native-1"
    assert [name for name, _ in calls] == ["session/create", "session/prompt"]
    # Forked sessions replay >1 MiB of history in the first follow frame.
    assert "max_size" in connect_kwargs and connect_kwargs["max_size"] is None


@pytest.mark.asyncio
async def test_disconnected_desktop_falls_back_to_acp_and_disables_fork(monkeypatch, tmp_path):
    home = tmp_path / "bridge"
    home.mkdir()
    (home / "agents.toml").write_text('[agents.dsh]\nprotocol = "fake"\n', encoding="utf-8")
    project = tmp_path / "project"
    project.mkdir()
    async def disconnected(_home):
        return False
    monkeypatch.setattr("agent_bridge.registry.desktop_available", disconnected)
    registry = Registry.create(home)
    await registry.start()
    try:
        result = await registry.dispatch_task("dsh", "ordinary", str(project), baseline="empty")
        assert registry.sessions[result["session_id"]].backend is None
        assert (await registry.wait_task(result["task_id"], timeout_sec=5))["status"] == "completed"
        with pytest.raises(ValueError, match="Desktop connection is required"):
            await registry.create_baseline(result["session_id"], "baseline")
    finally:
        await registry.stop()


@pytest.mark.asyncio
async def test_host_drop_before_create_retries_ordinary_turn_on_acp(monkeypatch, tmp_path):
    home = tmp_path / "bridge"
    home.mkdir()
    (home / "agents.toml").write_text('[agents.dsh]\nprotocol = "fake"\n', encoding="utf-8")
    project = tmp_path / "project"
    project.mkdir()
    async def connected(_home):
        return True
    monkeypatch.setattr("agent_bridge.registry.desktop_available", connected)
    registry = Registry.create(home)

    class DroppedDesktop(FakeAdapter):
        async def run_turn(self, session, task):
            raise dsh_desktop.DesktopUnavailable("Host down")

    def adapter_for(session):
        return (DroppedDesktop if session.backend == "desktop" else FakeAdapter)(registry.config.get("dsh"), home)

    monkeypatch.setattr(registry, "_adapter_for", adapter_for)
    await registry.start()
    try:
        sent = await registry.dispatch_task("dsh", "ordinary", str(project), baseline="empty")
        result = await registry.wait_task(sent["task_id"], timeout_sec=5)
        assert result["status"] == "completed"
        assert result["backend"] == "acp"
        assert "used ACP" in result["warnings"][0]
    finally:
        await registry.stop()
