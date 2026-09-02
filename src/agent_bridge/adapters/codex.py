from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import sys
from pathlib import Path
from typing import Any

from agent_bridge.adapters.base import STDIO_LIMIT, Adapter
from agent_bridge.codex_exec import (
    CodexTurnState,
    apply_codex_event,
    build_codex_exec_argv,
    codex_effort,
    finalize_codex_turn,
    resolve_codex_command,
    yolo_requested,
)
from agent_bridge.models import Session, Task, TurnResult
from agent_bridge.processes import (
    drop_pid,
    interrupt_then_reap,
    process_create_time,
    process_image_name,
    reap_subprocess,
    record_pid,
)
from agent_bridge.transcript import append_event
from agent_bridge.worker_env import build_worker_env

log = logging.getLogger(__name__)

STDERR_TAIL_LIMIT = 16000


class CodexAdapter(Adapter):
    def __init__(self, agent, home: Path, env_config=None) -> None:
        super().__init__(agent, home, env_config)
        self._procs: dict[str, asyncio.subprocess.Process] = {}
        self._cancelled: set[str] = set()

    async def ensure_session(self, session: Session) -> None:
        return None

    def _worker_env(self) -> dict[str, str]:
        return build_worker_env(self.agent.env, config=self.env_config, worker_context=True)

    def _base_cmd(self, env: dict[str, str] | None = None) -> list[str]:
        return resolve_codex_command(self.agent.command, self.agent.fallback_commands, env=env)

    def _build_cmd(self, session: Session, task: Task, env: dict[str, str] | None = None) -> list[str]:
        return build_codex_exec_argv(
            self._base_cmd(env),
            cwd=task.cwd or session.cwd,
            model=task.model or session.model,
            effort=task.effort or session.effort,
            resume_id=session.native_session_id,
            yolo=yolo_requested(self.agent.session_meta),
        )

    async def _drain_stderr(self, proc: asyncio.subprocess.Process, session_id: str) -> str:
        if proc.stderr is None:
            return ""
        tail = ""
        try:
            while True:
                line = await proc.stderr.readline()
                if not line:
                    return tail
                text = line.decode("utf-8", errors="replace").rstrip()
                if text:
                    tail = f"{tail}\n{text}"[-STDERR_TAIL_LIMIT:]
        except (ValueError, OSError):
            log.warning("stderr drain aborted for %s", session_id, exc_info=True)
            return tail

    async def _write_prompt(self, proc: asyncio.subprocess.Process, message: str) -> None:
        if proc.stdin is None:
            return
        try:
            proc.stdin.write(message.encode("utf-8"))
            await proc.stdin.drain()
        finally:
            try:
                proc.stdin.close()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass

    async def run_turn(self, session: Session, task: Task) -> TurnResult:
        env = self._worker_env()
        cmd = self._build_cmd(session, task, env)
        kwargs: dict[str, Any] = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        append_event(
            session.session_id,
            "prompt_sent",
            {"text": task.message, "cmd": cmd[:8]},
            self.home,
        )
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            cwd=task.cwd or session.cwd,
            limit=STDIO_LIMIT,
            **kwargs,
        )
        self._procs[session.session_id] = proc
        self._cancelled.discard(session.session_id)
        if proc.pid:
            record_pid(
                self.home,
                session.session_id,
                proc.pid,
                process_create_time(proc.pid),
                process_image_name(proc.pid),
            )
        stderr_task = asyncio.create_task(self._drain_stderr(proc, session.session_id))
        state = CodexTurnState()
        if session.native_session_id:
            state.thread_id = session.native_session_id
        observed_model = task.model or session.model
        observed_effort = codex_effort(task.effort or session.effort)
        try:
            await self._write_prompt(proc, task.message)
            assert proc.stdout is not None
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                raw = line.decode("utf-8", errors="replace").rstrip()
                if not raw:
                    continue
                try:
                    obj = json.loads(raw)
                except json.JSONDecodeError:
                    append_event(session.session_id, "raw", {"text": raw[:2000]}, self.home)
                    continue
                if not isinstance(obj, dict):
                    append_event(session.session_id, "raw", {"value": obj}, self.home)
                    continue
                apply_codex_event(state, obj)
                event = str(obj.get("type") or "")
                event_type = "raw"
                if event == "item.completed":
                    item = obj.get("item") if isinstance(obj.get("item"), dict) else {}
                    item_type = str(item.get("type") or "")
                    if item_type == "agent_message":
                        event_type = "message_chunk"
                    elif item_type == "file_change":
                        event_type = "tool_call"
                elif event in {"turn.failed", "error"}:
                    event_type = "error"
                append_event(
                    session.session_id,
                    event_type,
                    {"payload": obj} if len(raw) < 4000 else {"truncated": True},
                    self.home,
                )
            try:
                await asyncio.wait_for(proc.wait(), timeout=60)
            except TimeoutError:
                log.warning("codex stdout closed but process lingered; killing %s", proc.pid)
                await reap_subprocess(proc)
            stderr_tail = await stderr_task
            stop_reason, error, warnings = finalize_codex_turn(
                state,
                cancelled=session.session_id in self._cancelled,
                returncode=proc.returncode,
                stderr=stderr_tail,
            )
            if stop_reason == "cancelled":
                append_event(session.session_id, "turn_end", {"stop_reason": "cancelled"}, self.home)
                return TurnResult(
                    text=state.text,
                    files_changed=sorted(state.files),
                    stop_reason="cancelled",
                    native_session_id=state.thread_id,
                    usage=state.usage,
                    observed_model=observed_model,
                    observed_effort=observed_effort,
                )
            if stop_reason == "error":
                append_event(session.session_id, "error", {"error": error}, self.home)
                return TurnResult(
                    text=state.text,
                    files_changed=sorted(state.files),
                    stop_reason="error",
                    error=error,
                    usage=state.usage,
                    native_session_id=state.thread_id,
                    warnings=warnings,
                    observed_model=observed_model,
                    observed_effort=observed_effort,
                )
            session.native_session_id = state.thread_id
            append_event(session.session_id, "turn_end", {"stop_reason": "end_turn"}, self.home)
            return TurnResult(
                text=state.text,
                files_changed=sorted(state.files),
                stop_reason="end_turn",
                usage=state.usage,
                native_session_id=state.thread_id,
                warnings=warnings,
                observed_model=observed_model,
                observed_effort=observed_effort,
            )
        except asyncio.CancelledError:
            await interrupt_then_reap(proc)
            append_event(session.session_id, "turn_end", {"stop_reason": "cancelled"}, self.home)
            return TurnResult(
                text=state.text,
                files_changed=sorted(state.files),
                stop_reason="cancelled",
                native_session_id=state.thread_id,
                usage=state.usage,
                observed_model=observed_model,
                observed_effort=observed_effort,
            )
        except Exception:
            await reap_subprocess(proc)
            raise
        finally:
            if not stderr_task.done():
                stderr_task.cancel()
            try:
                await stderr_task
            except asyncio.CancelledError:
                pass
            self._procs.pop(session.session_id, None)
            self._cancelled.discard(session.session_id)
            drop_pid(self.home, session.session_id)

    async def cancel(self, session: Session) -> None:
        self._cancelled.add(session.session_id)
        proc = self._procs.get(session.session_id)
        if proc is not None:
            await interrupt_then_reap(proc)

    async def shutdown(self, session: Session) -> None:
        await self.cancel(session)
        self._procs.pop(session.session_id, None)
