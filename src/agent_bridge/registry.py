from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import psutil

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
from agent_bridge.paths import ensure_home, result_path, state_path, transcript_path
from agent_bridge.persist import atomic_write_json, atomic_write_text, read_json
from agent_bridge.probes import probe_agent
from agent_bridge.processes import count_sibling_servers, owner_alive, process_create_time, reap_orphans
from agent_bridge.transcript import (
    append_event,
    flush_pending,
    flush_session,
    forget_worker_activity,
    mark_worker_activity,
    page_events,
    read_events,
    read_events_tail,
    recent_activity,
    worker_silence_sec,
)
from agent_bridge.worker_env import describe_env, install_host_env, is_worker_context
from agent_bridge.workspace import merge_files_changed, snapshot_workspace

log = logging.getLogger(__name__)

RuntimeContext = Literal["coordinator", "worker"]
NESTED_DISPATCH_ERROR = (
    "nested dispatch is disabled: this Agent Bridge instance was inherited inside a worker process"
)
NESTED_PREFERENCES_ERROR = (
    "preference updates are disabled: this Agent Bridge instance was inherited inside a worker process"
)
NESTED_CANCEL_ERROR = (
    "task cancellation is disabled: this Agent Bridge instance was inherited inside a worker process"
)
NESTED_END_SESSION_ERROR = (
    "session shutdown is disabled: this Agent Bridge instance was inherited inside a worker process"
)

RESULT_TAIL = 6000
# Explicit get_result calls can read up to this many characters per page.
RESULT_PAGE_MAX_CHARS = 60000
# Retained only as a fallback if the one-time result artifact write fails.
RESULT_STORE_MAX = 30000
# Terminal tasks kept per session; older ones are pruned so state.json does
# not grow without bound over a long-lived Bridge.
TASK_KEEP_PER_SESSION = 20
FILES_CHANGED_MAX = 200
SESSION_KEEP_INACTIVE = 50
SESSION_RETAIN_SEC = 14 * 86400
TASK_KEEP_TOTAL = 200
STOP_TASK_GRACE_SEC = 15
STALL_POLL_SEC = 30
STALL_CANCEL_GRACE_SEC = 15


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def _resolve_runtime_context(runtime_context: RuntimeContext | None) -> RuntimeContext:
    if runtime_context is None:
        return "worker" if is_worker_context() else "coordinator"
    if runtime_context not in ("coordinator", "worker"):
        raise ValueError(f"unknown runtime_context {runtime_context!r}; use coordinator or worker")
    return runtime_context


def _session_last_active_ts(last_active_at: str) -> float:
    try:
        return datetime.fromisoformat(last_active_at).timestamp()
    except (ValueError, TypeError, OSError, OverflowError):
        return 0.0


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
        runtime_context: RuntimeContext | None = None,
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
        self.runtime_context = _resolve_runtime_context(runtime_context)
        self.dispatch_enabled = self.runtime_context == "coordinator"
        self._sibling_cache: tuple[float, int] | None = None
        self._stopping = False
        self._pending_state: dict[str, list[dict]] | None = None
        self._flush_task: asyncio.Task[None] | None = None

    @classmethod
    def create(
        cls,
        home: Path | None = None,
        config: AppConfig | None = None,
        *,
        owner_pid: int | None = None,
        owner_create_time: float | None = None,
        runtime_context: RuntimeContext | None = None,
    ) -> Registry:
        resolved = ensure_home(home)
        return cls(
            resolved,
            config or load_config(resolved),
            owner_pid=owner_pid,
            owner_create_time=owner_create_time,
            runtime_context=runtime_context,
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

    def _own_rows(self) -> dict[str, list[dict]]:
        return {
            "sessions": [s.model_dump(mode="json") for s in self.sessions.values()],
            "tasks": [t.model_dump(mode="json") for t in self.tasks.values()],
        }

    def _write_state(self, own: dict[str, list[dict]]) -> None:
        # Live siblings may interleave a read-merge-write; each instance only
        # rewrites its own records, so the next save converges. Uses the
        # snapshot in `own` plus owner identity, never self.sessions / self.tasks.
        path = state_path(self.home)
        disk = read_json(path, {})
        if not isinstance(disk, dict):
            disk = {}
        mine_sessions = {
            row["session_id"]: row
            for row in own.get("sessions") or []
            if isinstance(row, dict) and isinstance(row.get("session_id"), str)
        }
        mine_tasks = {
            row["task_id"]: row
            for row in own.get("tasks") or []
            if isinstance(row, dict) and isinstance(row.get("task_id"), str)
        }
        atomic_write_json(
            path,
            {
                "sessions": self._merge_owned(
                    disk.get("sessions") or [],
                    mine_sessions,
                    "session_id",
                ),
                "tasks": self._merge_owned(
                    disk.get("tasks") or [],
                    mine_tasks,
                    "task_id",
                ),
            },
        )

    def save(self) -> None:
        self._pending_state = self._own_rows()
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            own, self._pending_state = self._pending_state, None
            self._write_state(own)
            return
        if self._flush_task is None or self._flush_task.done():
            self._flush_task = loop.create_task(self._flush_state_loop(), name="state-flush")

    async def _flush_state_loop(self) -> None:
        # No await between seeing _pending_state is None and returning, so
        # save() cannot observe a still-running flush task and skip creating
        # a new one after this loop has already decided to exit.
        while self._pending_state is not None:
            own, self._pending_state = self._pending_state, None
            try:
                await asyncio.to_thread(self._write_state, own)
            except Exception:
                # A failed write must not kill the flush task: stop() awaits
                # it, and the next save() has to be able to retry.
                log.exception("could not write state.json")

    async def flush_state(self) -> None:
        """Wait until every save() so far is on disk."""
        task = self._flush_task
        if task is not None and not task.done():
            await task

    def touch_activity(self) -> None:
        self._last_activity = time.monotonic()

    def idle_exit_due(self) -> bool:
        idle_sec = self.config.server.idle_exit_sec
        if idle_sec <= 0:
            return False
        if time.monotonic() - self._last_activity < idle_sec:
            return False
        return all(
            task.status not in {TaskStatus.queued, TaskStatus.running} for task in self.tasks.values()
        )

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
        self._stopping = False
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
        # Stamp adopted owners onto disk first so a following prune is not
        # undone by _merge_owned treating the old unowned rows as foreign.
        self.save()
        await self.flush_state()
        self._prune()
        self.save()
        await self.flush_state()
        self.touch_activity()
        if self.config.server.idle_exit_sec > 0:
            self._watchdog = asyncio.create_task(
                self._idle_exit_watchdog(),
                name="idle-exit-watchdog",
            )

    async def stop(self) -> None:
        self._stopping = True
        watchdog = self._watchdog
        self._watchdog = None
        if watchdog is not None:
            watchdog.cancel()
        for idle in list(self._idle.values()):
            idle.cancel()
        self._idle.clear()
        bgs = [task for task in self._bg.values() if not task.done()]
        for bg in bgs:
            bg.cancel()
        if bgs:
            _done, pending = await asyncio.wait(bgs, timeout=STOP_TASK_GRACE_SEC)
            if pending:
                log.warning(
                    "%d task(s) did not finish cancelling within %ss",
                    len(pending),
                    STOP_TASK_GRACE_SEC,
                )
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
        try:
            flush_pending(self.home)
        except OSError:
            log.exception("could not flush transcripts during shutdown")
        self.save()
        await self.flush_state()

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

    SIBLING_CACHE_SEC = 60

    async def _sibling_count(self) -> int:
        now = time.monotonic()
        if self._sibling_cache is not None:
            cached_at, count = self._sibling_cache
            if now - cached_at < self.SIBLING_CACHE_SEC:
                return count
        try:
            count = await asyncio.to_thread(count_sibling_servers)
        except (psutil.Error, OSError) as exc:
            log.warning("could not count sibling agent-bridge servers: %s", exc)
            count = 0
        self._sibling_cache = (time.monotonic(), count)
        return count

    async def env_status(self) -> dict:
        status = describe_env(self.config.env)
        siblings = await self._sibling_count()
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
            "runtime_context": self.runtime_context,
            "dispatch_enabled": self.dispatch_enabled,
        }

    def set_preferences(
        self,
        *,
        mode: str | None = None,
        instructions: str | None = None,
    ) -> dict:
        if not self.dispatch_enabled:
            raise RuntimeError(NESTED_PREFERENCES_ERROR)
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
        if not self.dispatch_enabled:
            raise RuntimeError(NESTED_DISPATCH_ERROR)
        if self.config.coordinator.mode == "manual" and not user_requested:
            raise RuntimeError(
                "coordinator mode is manual: dispatch only when the user explicitly "
                "asked for a worker on this task. If they did, retry with "
                "user_requested=true; otherwise do the work yourself."
            )
        cwd_path = Path(cwd)
        if not cwd_path.is_absolute():
            raise ValueError("cwd must be an absolute path")
        if not cwd_path.exists():
            raise ValueError(f"cwd does not exist: {cwd_path}")
        if not cwd_path.is_dir():
            raise ValueError(f"cwd is not a directory: {cwd_path}")
        effort = normalize_effort(effort)
        self.config.get(agent)
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
            self._prune()
            self.save()
            log.info(
                "task_dispatched task_id=%s session_id=%s agent=%s",
                task.task_id,
                session.session_id,
                agent,
            )
            self._bg[task.task_id] = asyncio.create_task(self._run_task(task.task_id), name=f"task-{task.task_id}")
        return {
            "task_id": task.task_id,
            "session_id": session.session_id,
            "agent": agent,
            "model": session.model,
            "effort": session.effort,
        }

    async def _run_task(self, task_id: str) -> None:
        started_monotonic = time.monotonic()
        task = self.tasks[task_id]
        session = self.sessions[task.session_id]
        adapter = self._adapter_for(session)
        task.status = TaskStatus.running
        task.started_at = iso()
        session.proc_state = ProcState.busy
        session.last_active_at = iso()
        self.save()
        watch = None
        try:
            mark_worker_activity(session.session_id, self.home)
            before = await asyncio.to_thread(snapshot_workspace, task.cwd)
            limit = self.config.get(session.agent).stall_timeout_sec
            watch = (
                asyncio.create_task(
                    self._stall_watch(task, session, adapter, limit),
                    name=f"stall-{task_id}",
                )
                if limit > 0
                else None
            )
            result = await adapter.run_turn(session, task)
            if result.native_session_id:
                session.native_session_id = result.native_session_id
            task.result_chars = len(result.text)
            task.warnings = list(result.warnings)
            try:
                atomic_write_text(result_path(task.task_id, self.home), result.text)
                task.result_text = _tail(result.text)
            except OSError as exc:
                task.result_text = _tail(result.text, RESULT_STORE_MAX)
                task.warnings.append(
                    f"full result persistence failed: {type(exc).__name__}: {exc}"
                )
                log.exception("could not persist full result for task %s", task.task_id)
            full_changed = await asyncio.to_thread(
                merge_files_changed, task.cwd, result.files_changed, before
            )
            task.files_changed_total = len(full_changed)
            task.files_changed = full_changed[:FILES_CHANGED_MAX]
            task.files_changed_truncated = len(full_changed) > FILES_CHANGED_MAX
            task.usage = result.usage
            if session.agent == "grok":
                observed = await asyncio.to_thread(observe_grok_session, session.cwd, session.native_session_id)
                task.observed_model = observed["model"]
                task.observed_effort = observed["effort"]
            elif session.agent == "kimi":
                observed = await asyncio.to_thread(observe_kimi_session, session.native_session_id)
                task.observed_model = observed["model"]
                task.observed_effort = observed["effort"]
                if observed["failure"]:
                    # Kimi answered end_turn, so nothing above this line knows
                    # the turn failed. Say so where the coordinator looks.
                    task.warnings.append(
                        f"kimi reported end_turn but the turn failed: {observed['failure']}"
                    )
            else:
                # OpenCode (and any later ACP worker) has no on-disk sampler
                # log. Report the last model/effort the adapter applied.
                task.observed_model = result.observed_model
                task.observed_effort = result.observed_effort
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
            if watch is not None:
                watch.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await watch
            if task.finished_at is None:
                task.finished_at = iso()
            session.last_active_at = iso()
            if session.proc_state != ProcState.dead:
                session.proc_state = ProcState.ready if adapter.resident else ProcState.idle_unloaded
            try:
                flush_session(session.session_id, self.home)
            except OSError:
                log.exception("could not flush transcript for task %s", task.task_id)
            self._done[task_id].set()
            self._bg.pop(task_id, None)
            self.save()
            duration_ms = int((time.monotonic() - started_monotonic) * 1000)
            log_method = log.warning if task.status == TaskStatus.failed else log.info
            log_method(
                "task_finished task_id=%s session_id=%s agent=%s status=%s "
                "duration_ms=%s stop_reason=%s error=%r",
                task.task_id,
                task.session_id,
                task.agent,
                task.status.value,
                duration_ms,
                task.stop_reason,
                (task.error or "")[:500],
            )
            self._schedule_idle(session.session_id)

    async def _stall_watch(
        self,
        task: Task,
        session: Session,
        adapter: Adapter,
        limit: int,
    ) -> None:
        while True:
            silence = worker_silence_sec(session.session_id, self.home) or 0.0
            remaining = limit - silence
            if remaining <= 0:
                break
            await asyncio.sleep(min(remaining, STALL_POLL_SEC))
        if task.status in TERMINAL_STATUSES:
            return
        log.warning(
            "task %s stalled: no worker output for %ss; cancelling the turn",
            task.task_id,
            limit,
        )
        task.status = TaskStatus.failed
        task.stop_reason = "stalled"
        task.error = (
            f"worker produced no output for {limit}s (stall_timeout_sec); "
            "Bridge cancelled the turn"
        )
        append_event(
            session.session_id,
            "error",
            {"error": task.error, "stalled": True, "stall_timeout_sec": limit},
            self.home,
        )
        # The turn normally returns (and _run_task tears this watch down)
        # while cancel() is still reaping the worker; shield so that
        # cleanup runs to completion instead of dying with the watch.
        await asyncio.shield(self._cancel_stalled(adapter, session, task.task_id))
        try:
            await asyncio.wait_for(self._done[task.task_id].wait(), timeout=STALL_CANCEL_GRACE_SEC)
        except TimeoutError:
            bg = self._bg.get(task.task_id)
            if bg is not None:
                bg.cancel()

    async def _cancel_stalled(self, adapter: Adapter, session: Session, task_id: str) -> None:
        try:
            await adapter.cancel(session)
        except Exception:
            log.exception("stall cancel failed for task %s", task_id)

    def _drop_task(self, task_id: str) -> None:
        self.tasks.pop(task_id, None)
        self._done.pop(task_id, None)
        try:
            result_path(task_id, self.home).unlink(missing_ok=True)
        except OSError:
            log.warning("could not remove pruned result for task %s", task_id)

    def _prune_sessions(self) -> None:
        now = time.time()
        candidates = [
            session
            for session in self.sessions.values()
            if session.proc_state in {ProcState.dead, ProcState.idle_unloaded}
            and session.session_id not in self._adapters
            and self._busy_task(session.session_id) is None
        ]
        candidates.sort(key=lambda item: _session_last_active_ts(item.last_active_at), reverse=True)
        drop = [
            session
            for index, session in enumerate(candidates)
            if index >= SESSION_KEEP_INACTIVE
            or now - _session_last_active_ts(session.last_active_at) > SESSION_RETAIN_SEC
        ]
        for session in drop:
            session_id = session.session_id
            self.sessions.pop(session_id, None)
            forget_worker_activity(session_id, self.home)
            self._cancel_idle(session_id)
            for task in [item for item in self.tasks.values() if item.session_id == session_id]:
                self._drop_task(task.task_id)
            log.info(
                "pruned session %s (%s, last active %s)",
                session_id,
                session.proc_state.value,
                session.last_active_at,
            )

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
                self._drop_task(old.task_id)
        terminal_all = [task for task in self.tasks.values() if task.status in TERMINAL_STATUSES]
        if len(terminal_all) <= TASK_KEEP_TOTAL:
            return
        terminal_all.sort(key=lambda item: item.created_at)
        for old in terminal_all[: len(terminal_all) - TASK_KEEP_TOTAL]:
            self._drop_task(old.task_id)

    def _prune(self) -> None:
        self._prune_sessions()
        self._prune_tasks()

    def _cancel_idle(self, session_id: str) -> None:
        idle = self._idle.pop(session_id, None)
        if idle:
            idle.cancel()

    def _schedule_idle(self, session_id: str) -> None:
        if self._stopping:
            return
        session = self.sessions.get(session_id)
        if session is None:
            return
        adapter = self._adapters.get(session_id)
        if adapter is not None and not adapter.resident:
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

    @staticmethod
    def _result_hint(task: Task, prefix: str) -> str:
        hint = prefix
        if task.agent == "grok":
            hint += (
                " Grok system-prompt identity is not the selected model; "
                "use observed_model from this payload."
            )
        if task.agent == "kimi":
            hint += (
                " Kimi reports a failed turn as end_turn with empty text; "
                "an empty result is only clean if warnings is empty."
            )
        if task.agent == "opencode":
            hint += (
                " OpenCode observed_model/effort are the last values Bridge "
                "successfully set on the session after mapping, not a live sampler."
            )
        if task.agent == "claude":
            hint += (
                " Claude Code observed_model/effort are the last values Bridge "
                "successfully set on the session after mapping, not a live sampler."
            )
        if task.files_changed_truncated:
            hint += (
                f" files_changed lists the first {FILES_CHANGED_MAX} of "
                f"{task.files_changed_total} paths; run git status in cwd for the full set."
            )
        if task.stop_reason == "stalled":
            hint += (
                " The worker went silent for stall_timeout_sec and Bridge cancelled the turn; "
                "read get_transcript for its last activity, then either dispatch a narrower "
                f"task on the same session_id or raise [agents.{task.agent}] stall_timeout_sec "
                "if that step was legitimately long."
            )
        return hint

    def _task_snapshot(self, task: Task, include_result: bool = False) -> dict:
        events = read_events_tail(task.session_id, self.home)
        payload: dict[str, Any] = {
            "task_id": task.task_id,
            "session_id": task.session_id,
            "agent": task.agent,
            "status": task.status.value,
            "stop_reason": task.stop_reason,
            "error": task.error,
            "warnings": task.warnings,
            "files_changed": task.files_changed,
            "files_changed_total": task.files_changed_total,
            "files_changed_truncated": task.files_changed_truncated,
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
            preview = _tail(task.result_text)
            total_chars = task.result_chars or len(task.result_text)
            payload["result_text"] = preview
            payload["result_total_chars"] = total_chars
            payload["result_truncated"] = total_chars > len(preview)
            payload["usage"] = task.usage
            payload["hint"] = self._result_hint(
                task,
                "Use get_result for the complete final result and get_transcript "
                "for the detailed turn log.",
            )
        payload["silent_for_sec"] = (
            int(worker_silence_sec(task.session_id, self.home) or 0)
            if task.status == TaskStatus.running
            else None
        )
        cfg = self.config.agents.get(task.agent)
        payload["stall_timeout_sec"] = cfg.stall_timeout_sec if cfg is not None else None
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

    def get_result(
        self,
        task_id: str,
        cursor: int = 0,
        max_chars: int = RESULT_PAGE_MAX_CHARS,
    ) -> dict:
        if cursor < 0:
            raise ValueError("cursor must be non-negative")
        if not 1 <= max_chars <= RESULT_PAGE_MAX_CHARS:
            raise ValueError(f"max_chars must be between 1 and {RESULT_PAGE_MAX_CHARS}")
        task = self._require_task(task_id)
        path = result_path(task.task_id, self.home)
        artifact = path.is_file()
        try:
            text = path.read_text(encoding="utf-8") if artifact else task.result_text
        except OSError as exc:
            log.warning("could not read full result for task %s: %s", task.task_id, exc)
            artifact = False
            text = task.result_text
        if cursor > len(text):
            raise ValueError(f"cursor exceeds result length ({len(text)})")
        end = min(len(text), cursor + max_chars)
        has_more = end < len(text)
        payload = self._task_snapshot(task)
        payload.update(
            {
                "result_text": text[cursor:end],
                "result_offset": cursor,
                "result_total_chars": len(text) if artifact else (task.result_chars or len(text)),
                "next_cursor": end if has_more else None,
                "has_more": has_more,
                "result_truncated": has_more,
                "result_complete": artifact,
                "result_source": "artifact" if artifact else "legacy_state",
                "usage": task.usage,
                "hint": self._result_hint(
                    task,
                    "Continue with next_cursor while has_more is true. "
                    "Use get_transcript for the detailed turn log.",
                ),
            }
        )
        return payload

    def get_transcript(self, session_id: str, offset: int = 0, limit: int = 50, kinds: list[str] | None = None) -> dict:
        if session_id not in self.sessions and not transcript_path(session_id, self.home).is_file():
            raise KeyError(f"unknown session {session_id}")
        events = read_events(session_id, self.home)
        return page_events(events, offset=offset, limit=limit, kinds=kinds)

    async def cancel_task(self, task_id: str) -> dict:
        if not self.dispatch_enabled:
            raise RuntimeError(NESTED_CANCEL_ERROR)
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
            await asyncio.wait({bg}, timeout=15)
        else:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._done[task_id].wait(), timeout=15)
        if not self._done[task_id].is_set():
            # _run_task never ran (cancelled before its first step) or did not
            # finish in time; close the record here.
            task.status = TaskStatus.cancelled
            task.stop_reason = "cancelled"
            task.finished_at = iso()
            if task.started_at is None and session.proc_state == ProcState.spawning:
                session.proc_state = ProcState.idle_unloaded
            self._bg.pop(task_id, None)
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
        if not self.dispatch_enabled:
            raise RuntimeError(NESTED_END_SESSION_ERROR)
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
        forget_worker_activity(session_id, self.home)
        session.proc_state = ProcState.dead
        session.pid = None
        self.save()
        return {"session_id": session_id, "proc_state": session.proc_state.value}
