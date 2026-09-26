"""Official DeepSeek Harness Desktop Session Controller transport."""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from pathlib import Path
from urllib.parse import urlparse

import httpx
import websockets

from agent_bridge.adapters.base import Adapter
from agent_bridge.models import Session, Task, TurnResult
from agent_bridge.transcript import append_event, mark_worker_activity


class DesktopUnavailable(RuntimeError):
    pass


def handoff_path(home: Path) -> Path:
    return home / "dsh-desktop.json"


def _read_handoff(home: Path) -> tuple[str, str]:
    try:
        handoff = json.loads(handoff_path(home).read_text(encoding="utf-8"))
        origin = handoff["origin"]
        auth_url = handoff["authenticatedUrl"]
        parsed = urlparse(origin)
        launch = urlparse(auth_url)
        if (handoff.get("version") != 1 or parsed.scheme != "http"
                or parsed.hostname != "127.0.0.1" or not parsed.port
                or (launch.scheme, launch.netloc, launch.path) != (parsed.scheme, parsed.netloc, "/")
                or not launch.query):
            raise ValueError("invalid Desktop Host handoff")
        return origin, auth_url
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise DesktopUnavailable("DeepSeek Harness Desktop connector is unavailable") from exc


class DesktopClient:
    def __init__(self, origin: str, cookie: str):
        self.origin = origin
        self.cookie = cookie
        self.http = httpx.AsyncClient(timeout=10, headers={"Cookie": cookie}, trust_env=False)

    @classmethod
    async def connect(cls, home: Path) -> DesktopClient:
        origin, auth_url = _read_handoff(home)
        try:
            async with httpx.AsyncClient(timeout=3, follow_redirects=False, trust_env=False) as client:
                response = await client.get(auth_url)
            if response.status_code != 303:
                raise DesktopUnavailable("DeepSeek Harness Desktop authentication failed")
            cookie = response.headers.get("set-cookie", "").split(";", 1)[0]
            if not cookie or "=" not in cookie:
                raise DesktopUnavailable("DeepSeek Harness Desktop returned no session cookie")
            connected = cls(origin, cookie)
            try:
                await connected.rpc("session/list", {}, argument="_request")
            except Exception:
                await connected.close()
                raise
            return connected
        except (httpx.HTTPError, ValueError) as exc:
            raise DesktopUnavailable("DeepSeek Harness Desktop Host is disconnected") from exc

    async def rpc(self, endpoint: str, request: dict | None, *, argument: str = "request") -> dict:
        rpc_id = str(uuid.uuid4())
        envelope = {"type": "client-request", "rpcId": rpc_id, "method": endpoint,
                    "payload": {"args": {} if request is None else {argument: request}}}
        try:
            response = await self.http.post(f"{self.origin}/api/{endpoint}", json=envelope)
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise DesktopUnavailable("DeepSeek Harness Desktop Host disconnected during RPC") from exc
        if not isinstance(body, dict):
            raise RuntimeError("DeepSeek Harness Desktop returned an invalid RPC envelope")
        if body.get("rpcId") != rpc_id or body.get("type") != "server-response":
            raise RuntimeError("DeepSeek Harness Desktop returned an invalid RPC envelope")
        result = body.get("result", {})
        if not isinstance(result, dict):
            raise RuntimeError("DeepSeek Harness Desktop returned an invalid RPC result")
        if not result.get("ok"):
            error = result.get("error", {})
            raise RuntimeError(f"DeepSeek Harness {endpoint}: {error.get('message', 'unknown error')}")
        return result.get("value") or {}

    async def close(self) -> None:
        await self.http.aclose()


async def desktop_available(home: Path) -> bool:
    try:
        client = await DesktopClient.connect(home)
    except (DesktopUnavailable, RuntimeError):
        return False
    await client.close()
    return True


def _message_text(event: dict) -> str:
    content = event.get("data", {}).get("message", {}).get("content", [])
    return "".join(part.get("text", "") for part in content if part.get("type") == "text")


class DesktopAdapter(Adapter):
    resident = False

    def __init__(self, agent, home, env_config=None):
        super().__init__(agent, home, env_config)
        self._client: DesktopClient | None = None

    def can_fork(self) -> bool:
        return True

    async def _connection(self) -> DesktopClient:
        if self._client is None:
            self._client = await DesktopClient.connect(self.home)
        return self._client

    async def ensure_session(self, session: Session) -> None:
        if session.native_session_id:
            return
        client = await self._connection()
        if session.fork_source_id:
            created = await client.rpc("session/fork", {"sessionId": session.fork_source_id})
        else:
            created = await client.rpc("session/create", {"cwd": session.cwd})
        session.native_session_id = created["sessionId"]
        if session.title or session.fork_label:
            await client.rpc("session/rename", {
                "sessionId": session.native_session_id,
                "title": session.fork_label or session.title,
            })

    async def run_turn(self, session: Session, task: Task) -> TurnResult:
        await self.ensure_session(session)
        client = await self._connection()
        native_id = session.native_session_id
        assert native_id is not None
        if task.model or task.effort:
            if task.model:
                if "/" not in task.model:
                    raise ValueError("Desktop model selection must be provider/model")
                provider, model = task.model.split("/", 1)
            else:
                default = (await client.rpc("session/modelCatalog", None))["default"]
                provider, model = default["provider"], default["model"]
            selection = {"sessionId": native_id, "provider": provider, "model": model}
            if task.effort:
                selection["reasoningEffort"] = task.effort
            await client.rpc("session/selectModel", selection)

        stream_id = str(uuid.uuid4())
        uri = f"{client.origin.replace('http:', 'ws:')}/api/remote.mux"
        text_parts: list[str] = []
        request_id = str(uuid.uuid4())
        active_turn: int | None = None
        latest_turn: int | None = None
        append_event(session.session_id, "prompt_sent", {"text": task.message}, self.home)
        try:
            async with websockets.connect(uri, additional_headers={"Cookie": client.cookie}) as socket:
                await socket.send(json.dumps({"type": "open", "streamId": stream_id,
                                             "endpoint": "session/follow",
                                             "payload": {"args": {"request": {
                                                 "address": {"kind": "session", "sessionId": native_id},
                                                 "assistantStream": True,
                                             }}}}))
                opening = json.loads(await asyncio.wait_for(socket.recv(), 10))
                if opening.get("type") != "item" or opening.get("value", {}).get("type") != "snapshot":
                    raise RuntimeError("DeepSeek Harness follow stream did not open")
                await client.rpc("session/prompt", {"requestId": request_id, "sessionId": native_id,
                                                    "mode": "queue", "content": [{"type": "text", "text": task.message}]})
                while True:
                    frame = json.loads(await socket.recv())
                    if frame.get("streamId") != stream_id:
                        continue
                    if frame.get("type") == "error":
                        raise RuntimeError(frame.get("error", {}).get("message", "Desktop follow stream failed"))
                    if frame.get("type") == "end":
                        raise DesktopUnavailable("DeepSeek Harness follow stream closed before turn ended")
                    if frame.get("type") != "item":
                        continue
                    value = frame.get("value", {})
                    if value.get("type") != "event":
                        continue
                    event = value.get("event", {})
                    kind = event.get("type")
                    data = event.get("data", {})
                    if kind == "turn/start":
                        latest_turn = data.get("turn")
                    if kind == "user/message" and data.get("source", {}).get("rpcId") == request_id:
                        active_turn = latest_turn
                    if active_turn is None:
                        continue
                    mark_worker_activity(session.session_id, self.home)
                    if kind == "assistant/message":
                        text_parts.append(_message_text(event))
                    elif kind == "turn/end" and data.get("turn") == active_turn:
                        reason = data.get("reason", {})
                        stop = reason.get("kind", "completed")
                        if stop == "aborted":
                            stop = "cancelled"
                        append_event(session.session_id, "turn_end", {"stop_reason": stop}, self.home)
                        return TurnResult(text="".join(text_parts), stop_reason=stop,
                                          native_session_id=native_id, observed_model=task.model,
                                          observed_effort=task.effort,
                                          error=reason.get("error", {}).get("message") if stop == "error" else None)
        except (OSError, websockets.exceptions.WebSocketException) as exc:
            raise DesktopUnavailable("DeepSeek Harness Desktop disconnected during turn") from exc

    async def cancel(self, session: Session) -> None:
        if session.native_session_id:
            with contextlib.suppress(DesktopUnavailable):
                await (await self._connection()).rpc("session/cancel", {"sessionId": session.native_session_id})

    async def shutdown(self, session: Session) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None
