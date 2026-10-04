"""Minimal ACP echo agent used for offline adapter tests."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Any

from acp import run_agent, update_agent_message_text
from acp.exceptions import RequestError
from acp.schema import (
    AgentCapabilities,
    Implementation,
    InitializeResponse,
    NewSessionResponse,
    PromptCapabilities,
    PromptResponse,
    Usage,
)


def _record_effect(path: str) -> None:
    with Path(path).open("a", encoding="utf-8") as marker:
        marker.write("executed\n")


class EchoAgent:
    def __init__(self) -> None:
        self._conn: Any = None
        self._session_id = "echo-session"
        self._ready = False

    def on_connect(self, conn: Any) -> None:
        self._conn = conn

    async def initialize(self, protocol_version: int, **kwargs: Any) -> InitializeResponse:
        failure = os.environ.get("BRIDGE_ECHO_FAILURE")
        if failure == "initialize":
            raise RequestError.auth_required()
        if failure == "initialize_exit":
            os._exit(9)
        if failure == "stderr_flood":
            sys.stderr.write("x" * (17 * 1024 * 1024))
            sys.stderr.write("\nauthentication required\n")
            sys.stderr.flush()
            await asyncio.sleep(0.5)
            raise RequestError.auth_required()
        return InitializeResponse(
            protocol_version=protocol_version,
            agent_capabilities=AgentCapabilities(
                prompt_capabilities=PromptCapabilities(image=False, audio=False, embedded_context=False),
                load_session=True,
            ),
            agent_info=Implementation(name="echo", version="0.0.1"),
        )

    async def new_session(self, cwd: str, mcp_servers=None, **kwargs: Any) -> NewSessionResponse:
        if os.environ.get("BRIDGE_ECHO_FAILURE") == "new":
            raise RequestError.auth_required()
        self._ready = True
        return NewSessionResponse(session_id=self._session_id)

    async def load_session(self, cwd: str, session_id: str, mcp_servers=None, **kwargs: Any) -> None:
        if os.environ.get("BRIDGE_ECHO_FAILURE") == "load":
            raise RequestError.auth_required()
        self._session_id = session_id
        self._ready = True
        return None

    async def prompt(self, session_id: str, prompt: list[Any], **kwargs: Any) -> PromptResponse:
        if not self._ready:
            raise RequestError.resource_not_found(session_id)
        if os.environ.get("BRIDGE_ECHO_FAILURE") == "prompt_exit":
            await asyncio.to_thread(_record_effect, os.environ["BRIDGE_ECHO_MARKER"])
            print("Connection closed", file=sys.stderr, flush=True)
            os._exit(7)
        if os.environ.get("BRIDGE_ECHO_FAILURE") == "prompt_hang":
            await asyncio.to_thread(_record_effect, os.environ["BRIDGE_ECHO_MARKER"])
            await asyncio.Event().wait()
        text = ""
        for block in prompt:
            piece = getattr(block, "text", None)
            if isinstance(piece, str):
                text += piece
            elif isinstance(block, dict) and isinstance(block.get("text"), str):
                text += block["text"]
        if self._conn is not None:
            try:
                await self._conn.session_update(
                    session_id=session_id,
                    update=update_agent_message_text(f"echo:{text}"),
                )
            except TypeError:
                await self._conn.session_update(
                    session_id,
                    update_agent_message_text(f"echo:{text}"),
                )
        return PromptResponse(
            stop_reason="end_turn",
            usage=Usage(total_tokens=3, input_tokens=1, output_tokens=2),
        )

    async def cancel(self, session_id: str, **kwargs: Any) -> None:
        return None

    async def authenticate(self, method_id: str, **kwargs: Any) -> None:
        return None

    async def ext_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        return {}

    async def ext_notification(self, method: str, params: dict[str, Any]) -> None:
        return None


async def _main() -> None:
    await run_agent(EchoAgent())


if __name__ == "__main__":
    asyncio.run(_main())
