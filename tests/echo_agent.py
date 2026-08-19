"""Minimal ACP echo agent used for offline adapter tests."""

from __future__ import annotations

import asyncio
from typing import Any

from acp import run_agent, update_agent_message_text
from acp.schema import (
    AgentCapabilities,
    Implementation,
    InitializeResponse,
    NewSessionResponse,
    PromptCapabilities,
    PromptResponse,
)


class EchoAgent:
    def __init__(self) -> None:
        self._conn: Any = None
        self._session_id = "echo-session"

    def on_connect(self, conn: Any) -> None:
        self._conn = conn

    async def initialize(self, protocol_version: int, **kwargs: Any) -> InitializeResponse:
        return InitializeResponse(
            protocol_version=protocol_version,
            agent_capabilities=AgentCapabilities(
                prompt_capabilities=PromptCapabilities(image=False, audio=False, embedded_context=False),
                load_session=True,
            ),
            agent_info=Implementation(name="echo", version="0.0.1"),
        )

    async def new_session(self, cwd: str, mcp_servers=None, **kwargs: Any) -> NewSessionResponse:
        return NewSessionResponse(session_id=self._session_id)

    async def load_session(self, cwd: str, session_id: str, mcp_servers=None, **kwargs: Any) -> None:
        self._session_id = session_id
        return None

    async def prompt(self, session_id: str, prompt: list[Any], **kwargs: Any) -> PromptResponse:
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
        return PromptResponse(stop_reason="end_turn")

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
