from __future__ import annotations

import asyncio
import os
from pathlib import Path

from agent_bridge.adapters.base import Adapter
from agent_bridge.config import AgentConfig
from agent_bridge.models import Session, Task, TurnResult
from agent_bridge.transcript import append_event


class FakeAdapter(Adapter):
    def __init__(self, agent: AgentConfig, home: Path, env_config=None) -> None:
        super().__init__(agent, home, env_config)
        self._alive: set[str] = set()
        self._cancel: dict[str, asyncio.Event] = {}

    async def ensure_session(self, session: Session) -> None:
        self._alive.add(session.session_id)
        session.native_session_id = session.native_session_id or f"fake-{session.session_id}"
        self._cancel.setdefault(session.session_id, asyncio.Event())

    async def run_turn(self, session: Session, task: Task) -> TurnResult:
        await self.ensure_session(session)
        append_event(session.session_id, "prompt_sent", {"text": task.message}, self.home)
        cancel = self._cancel[session.session_id]
        delay = float(os.environ.get("AGENT_BRIDGE_FAKE_DELAY", "0.05"))
        try:
            await asyncio.wait_for(cancel.wait(), timeout=delay)
            append_event(session.session_id, "turn_end", {"stop_reason": "cancelled"}, self.home)
            return TurnResult(text="", stop_reason="cancelled")
        except TimeoutError:
            pass
        text = f"[fake:{self.agent.name}] {task.message}"
        append_event(session.session_id, "message_chunk", {"text": text}, self.home)
        append_event(session.session_id, "turn_end", {"stop_reason": "end_turn"}, self.home)
        return TurnResult(text=text, files_changed=[], stop_reason="end_turn", native_session_id=session.native_session_id)

    async def cancel(self, session: Session) -> None:
        event = self._cancel.setdefault(session.session_id, asyncio.Event())
        event.set()

    async def shutdown(self, session: Session) -> None:
        self._alive.discard(session.session_id)
        self._cancel.pop(session.session_id, None)
