from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from datetime import datetime
from pathlib import Path

from agent_bridge.adapters import build_adapter
from agent_bridge.adapters.base import Adapter
from agent_bridge.config import (
    COORDINATOR_MODE_HINTS,
    AppConfig,
    load_config,
    normalize_coordinator_mode,
    write_coordinator_overlay,
)
from agent_bridge.grok_observe import observe_grok_session
from agent_bridge.kimi_observe import observe_kimi_session
from agent_bridge.models import (
    DEFAULT_WAIT_SEC,
    TERMINAL_STATUSES,
    ProcState,
    Session,
    Task,
    TaskStatus,
    iso,
    normalize_effort,
)
from agent_bridge.paths import ensure_home, state_path
from agent_bridge.persist import atomic_write_json, read_json
from agent_bridge.probes import probe_agent
from agent_bridge.processes import count_sibling_servers, owner_alive, process_create_time, reap_orphans
from agent_bridge.transcript import page_events, read_events, read_events_tail, recent_activity
from agent_bridge.worker_env import describe_env, install_host_env
from agent_bridge.workspace import merge_files_changed, snapshot_workspace

log = logging.getLogger(__name__)

RESULT_TAIL = 6000
# result_text kept on the Task (and persisted in state.json). get_result only
# ever returns the RESULT_TAIL suffix; the full log lives in the transcript.
RESULT_STORE_MAX = 30000
# Terminal tasks kept per session; older ones are pruned so state.json does
# not grow without bound over a long-lived Bridge.
TASK_KEEP_PER_SESSION = 20


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def _tail(text: str, limit: int = RESULT_TAIL) -> str:
    if len(text.encode("utf-8")) <= limit:
        return text
    encoded = text.encode("utf-8")
    return encoded[-limit:].decode("utf-8", errors="ignore")


class Registry:
    def __init__(
        self,
        home: Path,
        config: AppConfig,
        *,
        owner_pid: int | None = None,
        owner_create_time: float | None = None,
    ) -> None:
        self.home = home
        self.config = config
        self.sessions: dict[str, Session] = {}
        self.tasks: dict[str, Task] = {}
        self._adapters: dict[str, Adapter] = {}
        self._done: dict[str, asyncio.Event] = {}
        self._idle: dict[str, asyncio.Task[None]] = {}
        self._bg: dict[str, asyncio.Task[None]] = {}
        self._lock = asyncio.Lock()
        self._last_activity = time.monotonic()
        self._watchdog: asyncio.Task[None] | None = None
        self._owner_pid = os.getpid() if owner_pid is None else owner_pid
        self._owner_create_time = (
            process_create_time(os.getpid()) if owner_create_time is None else owner_create_time
        )

    @classmethod
    def create(
        cls,
        home: Path | None = None,
        config: AppConfig | None = None,
        *,
        owner_pid: int | None = None,
        owner_create_time: float | None = None,
    ) -> Registry:
        resolved = ensure_home(home)
        return cls(
            resolved,
            config or load_config(resolved),
            owner_pid=owner_pid,
            owner_create_time=owner_create_time,
        )

    def _stamp_owner(self, record: Session | Task) -> None:
        record.owner_pid = self._owner_pid
        record.owner_create_time = self._owner_create_time

    def _is_mine(self, owner_pid: int | None, owner_create_time: float | None) -> bool:
        return owner_pid == self._owner_pid and owner_create_time == self._owner_create_time

    def _foreign_live(self, owner_pid: int | None, owner_create_time: float | None) -> bool:
        if owner_pid is None and owner_create_time is None:
            return False
        if self._is_mine(owner_pid, owner_create_time):
            return False
        return owner_alive(owner_pid, owner_create_time)

    def _merge_owned(
        self,
        disk_rows: list,
        mine: dict[str, dict],
        id_key: str,
    ) -> list[dict]:
        merged: dict[str, dict] = {}
        for raw in disk_rows:
            if not isinstance(raw, dict):
                continue
            key = raw.get(id_key)
            if not isinstance(key, str):
                continue
            owner_pid = raw.get("owner_pid")
            owner_create_time = raw.get("owner_create_time")
            if self._is_mine(owner_pid, owner_create_time):
                continue
            if self._foreign_live(owner_pid, owner_create_time) or key not in mine:
                merged[key] = raw
        merged.update(mine)
        return list(merged.values())

    def save(self) -> None:
        # Live siblings may interleave a read-merge-write; each instance only
        # rewrites its own records, so the next save converges.
        path = state_path(self.home)
        disk = read_json(path, {})
        if not isinstance(disk, dict):
            disk = {}
        atomic_write_json(
            path,
            {
                "sessions": self._merge_owned(
                    disk.get("sessions") or [],
                    {session.session_id: session.model_dump(mode="json") for session in self.sessions.values()},
                    "session_id",
                ),
                "tasks": self._merge_owned(
                    disk.get("tasks") or [],
                    {task.task_id: task.model_dump(mode="json") for task in self.tasks.values()},
                    "task_id",
                ),
            },
        )

    def touch_activity(self) -> None:
        self._last_activity = time.monotonic()

    def idle_exit_due(self) -> bool:
        idle_sec = self.config.server.idle_exit_sec
        if idle_sec <= 0:
            return False
        if time.monotonic() - self._last_activity < idle_sec:
            return False
        for task in self.tasks.values():
            if task.status in {TaskStatus.queued, TaskStatus.running}:
                return False
        return True

    async def _idle_exit_watchdog(self) -> None:
        try:
            while True:
                await asyncio.sleep(60)
                if not self.idle_exit_due():
                    continue
                idle_sec = self.config.server.idle_exit_sec
                log.info(
                    "no MCP activity for %s seconds and no queued/running tasks; self-exiting",
                    idle_sec,
                )
                # Clear before stop() so a CancelledError from stop cancelling
                # this task cannot skip os._exit once the exit decision is made.
                self._watchdog = None
                try:
                    await self.stop()
                finally:
                    os._exit(0)
        except asyncio.CancelledError:
            raise

    async def start(self) -> None:
        install_host_env(self.config.env)
        reap_orphans(self.home)
        payload = read_json(state_path(self.home), {})
        for raw in payload.get("sessions") or []:
            session = Session.model_validate(raw)
            if self._foreign_live(session.owner_pid, session.owner_create_time):
                continue
            self._stamp_owner(session)
            if session.proc_state in {ProcState.busy, ProcState.spawning, ProcState.ready}:
                session.proc_state = ProcState.idle_unloaded
            session.pid = None
            self.sessions[session.session_id] = session
        for raw in payload.get("tasks") or []:
            task = Task.model_validate(raw)
            if self._foreign_live(task.owner_pid, task.owner_create_time):
                continue
            self._stamp_owner(task)
            if task.status in {TaskStatus.queued, TaskStatus.running}:
                task.status = TaskStatus.failed
                task.error = "bridge_restarted"
                task.finished_at = iso()
            self.tasks[task.task_id] = task
            done = asyncio.Event()
            done.set()
            self._done[task.task_id] = done
        self.save()
        self.touch_activity()
        if self.config.server.idle_exit_sec > 0:
            self._watchdog = asyncio.create_task(
                self._idle_exit_watchdog(),
                name="idle-exit-watchdog",
            )

    async def stop(self) -> None:
        watchdog = self._watchdog
        self._watchdog = None
        if watchdog is not None:
            watchdog.cancel()
        for idle in list(self._idle.values()):
            idle.cancel()
        for bg in list(self._bg.values()):
            bg.cancel()
        for session_id, adapter in list(self._adapters.items()):
            session = self.sessions.get(session_id)
            if session is not None:
                try:
                    await adapter.shutdown(session)
                except Exception:
                    log.exception("shutdown failed for %s", session_id)
                if session.proc_state != ProcState.dead:
                    session.proc_state = ProcState.idle_unloaded
        self._adapters.clear()
        self.save()

    def _adapter_for(self, session: Session) -> Adapter:
        existing = self._adapters.get(session.session_id)
        if existing is not None:
            return existing
        adapter = build_adapter(self.config.get(session.agent), self.home, self.config.env)
        self._adapters[session.session_id] = adapter
        return adapter

    def _busy_task(self, session_id: str) -> Task | None:
        for task in self.tasks.values():
            if task.session_id == session_id and task.status in {TaskStatus.queued, TaskStatus.running}:
                return task
        return None

    async def list_agents(self) -> list[dict]:
        probes = [probe_agent(cfg, self.config.env) for cfg in self.config.agents.values()]
        return list(await asyncio.gather(*probes))

    def env_status(self) -> dict:
        status = describe_env(self.config.env)
        siblings = count_sibling_servers()
        if siblings > 0:
            warnings = status.setdefault("warnings", [])
            warnings.append(
                f"{siblings} other agent-bridge server instance(s) running on this machine "
                "(each coordinator host holds its own; abandoned ones self-exit after "
                "server.idle_exit_sec)"
            )
        return status

    def coordinator_status(self) -> dict:
        cfg = self.config.coordinator
        return {
            "mode": cfg.mode,
            "hint": COORDINATOR_MODE_HINTS.get(cfg.mode, COORDINATOR_MODE_HINTS["auto"]),
            "instructions": cfg.instructions or None,
        }

    def set_preferences(
        self,
        *,
        mode: str | None = None,
        instructions: str | None = None,
    ) -> dict:
        if mode is None and instructions is None:
            raise ValueError("provide mode and/or instructions")
        if mode is not None:
            mode = normalize_coordinator_mode(mode, strict=True)
        path = write_coordinator_overlay(self.home, mode=mode, instructions=instructions)
        # The running instance applies the change immediately; the file makes
        # it stick for every Bridge instance started after this.
        if mode is not None:
            self.config.coordinator.mode = mode
        if instructions is not None:
            self.config.coordinator.instructions = instructions.strip()
        notes = [
            "active in this Bridge instance now; other running instances pick it up at their next start"
        ]
        if mode is not None and os.environ.get("AGENT_BRIDGE_MODE"):
            notes.append(
                "this host pins mode via AGENT_BRIDGE_MODE, which outranks the file "
                "after a restart; the saved mode applies to hosts without that pin"
            )
        return {"coordinator": self.coordinator_status(), "path": str(path), "notes": notes}

    async def dispatch_task(
        self,
        agent: str,
        message: str,
        cwd: str,
        session_id: str | None = None,
        model: str | None = None,
        effort: str | None = None,
        title: str | None = None,
        user_requested: bool = False,
    ) -> dict:
        if self.config.coordinator.mode == "manual" and not user_requested:
            raise RuntimeError(
                "coordinator mode is manual: dispatch only when the user explicitly "
                "asked for a worker on this task. If they did, retry with "
                "user_requested=true; otherwise do the work yourself."
            )
        cwd_path = Path(cwd)
        if not cwd_path.is_absolute():
            raise ValueError("cwd must be an absolute path")
        effort = normalize_effort(effort)
        cfg = self.config.get(agent)
        async with self._lock:
            if session_id:
                session = self.sessions.get(session_id)
                if session is None:
                    raise KeyError(f"unknown session {session_id}")
                if session.agent != agent:
                    raise ValueError(f"session {session_id} belongs to agent {session.agent}, not {agent}")
                if Path(session.cwd).resolve() != cwd_path.resolve():
                    raise ValueError(
                        f"session {session.session_id} is bound to {session.cwd}; "
                        "follow-up cwd must be the same project folder"
                    )
                busy = self._busy_task(session.session_id)
                if busy is not None:
                    raise RuntimeError(
                        f"session {session.session_id} is busy with {busy.task_id}; call wait_task first"
                    )
            else:
                session = Session(
                    session_id=_new_id("sess"),
                    agent=agent,
                    cwd=str(cwd_path.resolve()),
                    model=model,
                    effort=effort,
                    title=title,
                    proc_state=ProcState.spawning,
                )
                self._stamp_owner(session)
                self.sessions[session.session_id] = session
            if model:
                session.model = model
            if effort:
                session.effort = effort
            if title:
                session.title = title
            if session_id is None:
                session.cwd = str(cwd_path.resolve())
            session.last_active_at = iso()
            task = Task(
                task_id=_new_id("task"),
                session_id=session.session_id,
                agent=agent,
                message=message,
                cwd=session.cwd,
                model=model or session.model,
                effort=effort or session.effort,
                status=TaskStatus.queued,
            )
            self._stamp_owner(task)
            self.tasks[task.task_id] = task
            self._done[task.task_id] = asyncio.Event()
            self._cancel_idle(session.session_id)
            self._prune_tasks()
            self.save()
            self._bg[task.task_id] = asyncio.create_task(self._run_task(task.task_id), name=f"task-{task.task_id}")
        return {
            "task_id": task.task_id,
            "session_id": session.session_id,
            "agent": agent,
            "model": session.model,
            "effort": session.effort,
        }

    async def _run_task(self, task_id: str) -> None:
        task = self.tasks[task_id]
        session = self.sessions[task.session_id]
        adapter = self._adapter_for(session)
        task.status = TaskStatus.running
        task.started_at = iso()
        session.proc_state = ProcState.busy
        session.last_active_at = iso()
        self.save()
        try:
            before = snapshot_workspace(task.cwd)
            result = await adapter.run_turn(session, task)
            if result.native_session_id:
                session.native_session_id = result.native_session_id
            task.result_text = _tail(result.text, RESULT_STORE_MAX)
            task.files_changed = merge_files_changed(task.cwd, result.files_changed, before)
            task.usage = result.usage
            task.warnings = list(result.warnings)
            if session.agent == "grok":
                observed = observe_grok_session(session.cwd, session.native_session_id)
                task.observed_model = observed["model"]
                task.observed_effort = observed["effort"]
            elif session.agent == "kimi":
                observed = observe_kimi_session(session.native_session_id)
                task.observed_model = observed["model"]
                task.observed_effort = observed["effort"]
                if observed["failure"]:
                    # Kimi answered end_turn, so nothing above this line knows
                    # the turn failed. Say so where the coordinator looks.
                    task.warnings.append(
                        f"kimi reported end_turn but the turn failed: {observed['failure']}"
                    )
            # cancel_task's timeout path may already have finalized this task
            # as cancelled; a late turn result must not overwrite that.
            if task.status not in TERMINAL_STATUSES:
                task.stop_reason = result.stop_reason
                if result.error:
                    task.status = TaskStatus.failed
                    task.error = result.error
                elif result.stop_reason == "cancelled":
                    task.status = TaskStatus.cancelled
                else:
                    task.status = TaskStatus.completed
            session.turns += 1
        except asyncio.CancelledError:
            if task.status not in TERMINAL_STATUSES:
                task.status = TaskStatus.cancelled
                task.stop_reason = "cancelled"
        except Exception as exc:
            log.exception("task %s failed", task_id)
            if task.status not in TERMINAL_STATUSES:
                task.status = TaskStatus.failed
                task.error = str(exc)
                task.stop_reason = "error"
        finally:
            if task.finished_at is None:
                task.finished_at = iso()
            session.last_active_at = iso()
            if session.proc_state != ProcState.dead:
                session.proc_state = ProcState.ready
            self._done[task_id].set()
            self._bg.pop(task_id, None)
            self.save()
            self._schedule_idle(session.session_id)

    def _prune_tasks(self) -> None:
        by_session: dict[str, list[Task]] = {}
        for task in self.tasks.values():
            if task.status in TERMINAL_STATUSES:
                by_session.setdefault(task.session_id, []).append(task)
        for terminal in by_session.values():
            if len(terminal) <= TASK_KEEP_PER_SESSION:
                continue
            terminal.sort(key=lambda item: item.created_at)
            for old in terminal[: len(terminal) - TASK_KEEP_PER_SESSION]:
                self.tasks.pop(old.task_id, None)
                self._done.pop(old.task_id, None)

    def _cancel_idle(self, session_id: str) -> None:
        idle = self._idle.pop(session_id, None)
        if idle:
            idle.cancel()

    def _schedule_idle(self, session_id: str) -> None:
        session = self.sessions.get(session_id)
        if session is None:
            return
        try:
            cfg = self.config.get(session.agent)
        except KeyError:
            return
        if cfg.idle_unload_sec <= 0:
            return
        self._cancel_idle(session_id)

        async def _idle() -> None:
            await asyncio.sleep(cfg.idle_unload_sec)
            current = self.sessions.get(session_id)
            if current is None or self._busy_task(session_id):
                return
            adapter = self._adapters.get(session_id)
            if adapter is not None:
                try:
                    await adapter.shutdown(current)
                except Exception:
                    log.exception("idle unload failed for %s", session_id)
            current.proc_state = ProcState.idle_unloaded
            self.save()

        self._idle[session_id] = asyncio.create_task(_idle(), name=f"idle-{session_id}")

    def _require_task(self, task_id: str) -> Task:
        task = self.tasks.get(task_id)
        if task is None:
            raise KeyError(f"unknown task {task_id}")
        return task

    def _task_snapshot(self, task: Task, include_result: bool = False) -> dict:
        events = read_events_tail(task.session_id, self.home)
        payload = {
            "task_id": task.task_id,
            "session_id": task.session_id,
            "agent": task.agent,
            "status": task.status.value,
            "stop_reason": task.stop_reason,
            "error": task.error,
            "warnings": task.warnings,
            "files_changed": task.files_changed,
            "model": task.model,
            "effort": task.effort,
            "observed_model": task.observed_model,
            "observed_effort": task.observed_effort,
            "created_at": task.created_at,
            "started_at": task.started_at,
            "finished_at": task.finished_at,
            "recent_activity": recent_activity(events),
        }
        if task.started_at:
            start = datetime.fromisoformat(task.started_at)
            end = datetime.fromisoformat(task.finished_at) if task.finished_at else datetime.fromisoformat(iso())
            payload["elapsed_sec"] = max(0, int((end - start).total_seconds()))
        else:
            payload["elapsed_sec"] = 0
        if include_result:
            payload["result_text"] = _tail(task.result_text)
            payload["result_truncated"] = len(task.result_text.encode("utf-8")) > RESULT_TAIL
            payload["usage"] = task.usage
            payload["hint"] = "Use get_transcript for the full turn log."
            if task.agent == "grok":
                payload["hint"] += (
                    " Grok system-prompt identity is not the selected model; "
                    "use observed_model from this payload."
                )
            if task.agent == "kimi":
                payload["hint"] += (
                    " Kimi reports a failed turn as end_turn with empty text; "
                    "an empty result is only clean if warnings is empty."
                )
        return payload

    async def wait_task(self, task_id: str, timeout_sec: float = DEFAULT_WAIT_SEC) -> dict:
        task = self._require_task(task_id)
        event = self._done.setdefault(task_id, asyncio.Event())
        if task.status not in TERMINAL_STATUSES:
            try:
                await asyncio.wait_for(event.wait(), timeout=timeout_sec)
            except TimeoutError:
                return {"timed_out": True, **self._task_snapshot(self.tasks[task_id])}
        return {"timed_out": False, **self._task_snapshot(self.tasks[task_id], include_result=True)}

    def check_task(self, task_id: str) -> dict:
        return self._task_snapshot(self._require_task(task_id))

    def get_result(self, task_id: str) -> dict:
        return self._task_snapshot(self._require_task(task_id), include_result=True)

    def get_transcript(self, session_id: str, offset: int = 0, limit: int = 50, kinds: list[str] | None = None) -> dict:
        if session_id not in self.sessions:
            raise KeyError(f"unknown session {session_id}")
        events = read_events(session_id, self.home)
        return page_events(events, offset=offset, limit=limit, kinds=kinds)

    async def cancel_task(self, task_id: str) -> dict:
        task = self._require_task(task_id)
        if task.status in TERMINAL_STATUSES:
            return self._task_snapshot(task)
        session = self.sessions[task.session_id]
        adapter = self._adapters.get(session.session_id)
        if adapter is not None:
            await adapter.cancel(session)
        bg = self._bg.get(task_id)
        if bg is not None:
            bg.cancel()
        try:
            await asyncio.wait_for(self._done[task_id].wait(), timeout=15)
        except TimeoutError:
            task.status = TaskStatus.cancelled
            task.stop_reason = "cancelled"
            task.finished_at = iso()
            self._done[task_id].set()
            self.save()
        return self._task_snapshot(self.tasks[task_id])

    def list_sessions(self, active_only: bool = False) -> list[dict]:
        rows = []
        for session in self.sessions.values():
            if active_only and session.proc_state in {ProcState.dead, ProcState.idle_unloaded}:
                continue
            rows.append(
                {
                    "session_id": session.session_id,
                    "agent": session.agent,
                    "cwd": session.cwd,
                    "native_session_id": session.native_session_id,
                    "proc_state": session.proc_state.value,
                    "turns": session.turns,
                    "title": session.title,
                    "model": session.model,
                    "effort": session.effort,
                    "last_active_at": session.last_active_at,
                }
            )
        return rows

    async def end_session(self, session_id: str) -> dict:
        session = self.sessions.get(session_id)
        if session is None:
            raise KeyError(f"unknown session {session_id}")
        busy = self._busy_task(session_id)
        if busy is not None:
            await self.cancel_task(busy.task_id)
        adapter = self._adapters.pop(session_id, None)
        if adapter is not None:
            await adapter.shutdown(session)
        self._cancel_idle(session_id)
        session.proc_state = ProcState.dead
        session.pid = None
        self.save()
        return {"session_id": session_id, "proc_state": session.proc_state.value}
