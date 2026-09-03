from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import shutil
import signal
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

import psutil

from agent_bridge.paths import pids_path
from agent_bridge.persist import atomic_write_json, read_json

log = logging.getLogger(__name__)


def resolve_executable(name: str) -> str | None:
    path = Path(name)
    if path.is_file():
        return str(path)
    return shutil.which(name)


def resolve_command(
    command: list[str],
    fallbacks: list[list[str]] | None = None,
    extra: Sequence[list[str]] | None = None,
    validate: Callable[[list[str]], str | None] | None = None,
) -> list[str]:
    candidates = [*(extra or []), command, *(fallbacks or [])]
    errors: list[str] = []
    for cand in candidates:
        if not cand:
            continue
        resolved_exe = resolve_executable(cand[0])
        if not resolved_exe:
            errors.append(f"{cand[0]} not found")
            continue
        if cand[0] in {"agent", "agent.exe"} and not _looks_like_cursor(resolved_exe):
            errors.append(f"{resolved_exe} (not Cursor)")
            continue
        resolved = [resolved_exe, *cand[1:]]
        if validate:
            problem = validate(resolved)
            if problem:
                errors.append(problem)
                continue
        return resolved
    raise FileNotFoundError("; ".join(errors) or f"command not found: {command}")


def _looks_like_cursor(resolved: str) -> bool:
    return "cursor" in resolved.replace("\\", "/").lower()


def process_image_name(pid: int) -> str | None:
    try:
        proc = psutil.Process(pid)
        return Path(proc.exe()).name
    except (psutil.Error, OSError):
        return None


def process_create_time(pid: int) -> float | None:
    try:
        return psutil.Process(pid).create_time()
    except (psutil.Error, OSError):
        return None


def _signal_asyncio_proc(proc: Any | None, *, force: bool = False) -> None:
    """Send terminate/kill through an asyncio subprocess handle.

    Used when psutil cannot see the pid — typical of restricted containers
    whose /proc is hidden or belongs to another PID namespace.
    """
    if proc is None or getattr(proc, "returncode", None) is not None:
        return
    try:
        if force:
            proc.kill()
        else:
            proc.terminate()
    except (ProcessLookupError, PermissionError, OSError):
        pass


def _process_tree(pid: int) -> list[Any]:
    try:
        parent = psutil.Process(pid)
    except psutil.Error:
        return []
    try:
        children = parent.children(recursive=True)
    except psutil.Error:
        children = []
    return [parent, *children]


def _signal_tree(pid: int, *, force: bool) -> bool:
    """Signal pid and descendants. True if the root process accepted the signal."""
    tree = _process_tree(pid)
    if not tree:
        return False
    parent, *children = tree
    for child in children:
        try:
            if force:
                child.kill()
            else:
                child.terminate()
        except psutil.Error:
            pass
    try:
        if force:
            parent.kill()
        else:
            parent.terminate()
    except psutil.Error:
        return False
    return True


def kill_tree(pid: int | None, handle: Any | None = None, *, force: bool = False) -> None:
    """Signal a process tree. Does not wait — callers that can await should."""
    if pid and _signal_tree(pid, force=force):
        return
    _signal_asyncio_proc(handle, force=force)


def _wait_and_kill_tree(pid: int, timeout: float = 5.0) -> None:
    """Sync follow-up for paths that have no asyncio handle (orphan reap)."""
    tree = _process_tree(pid)
    if not tree:
        return
    _gone, alive = psutil.wait_procs(tree, timeout=timeout)
    for leftover in alive:
        with contextlib.suppress(psutil.Error):
            leftover.kill()


async def interrupt_then_reap(proc: Any | None, timeout: float = 3.0) -> None:
    """Ask the process to stop, then escalate to terminate/kill.

    Windows workers started with ``CREATE_NEW_PROCESS_GROUP`` get
    ``CTRL_BREAK_EVENT`` first so Codex exec can run ``turn/interrupt``.
    Unix workers get ``SIGINT``. If that does not finish the process,
    ``reap_subprocess`` terminates then kills the tree.
    If the interrupt cannot be delivered (no console on Windows), escalate at once.
    """
    if proc is None or getattr(proc, "returncode", None) is not None:
        return
    pid = getattr(proc, "pid", None)
    signalled = False
    try:
        if sys.platform == "win32" and pid:
            os.kill(pid, signal.CTRL_BREAK_EVENT)
            signalled = True
        elif hasattr(proc, "send_signal"):
            proc.send_signal(signal.SIGINT)
            signalled = True
        else:
            kill_tree(pid, handle=proc)
    except (ProcessLookupError, PermissionError, OSError, ValueError) as exc:
        log.debug("graceful interrupt of pid %s failed (%s); terminating instead", pid, exc)
    if signalled:
        try:
            await asyncio.wait_for(proc.wait(), timeout=timeout)
            return
        except TimeoutError:
            pass
    await reap_subprocess(proc, timeout=timeout)


async def reap_subprocess(proc: Any | None, timeout: float = 5.0) -> None:
    """Stop a spawned asyncio subprocess without blocking the event loop."""
    if proc is None:
        return
    pid = getattr(proc, "pid", None)
    if getattr(proc, "returncode", None) is None:
        kill_tree(pid, handle=proc)
    try:
        await asyncio.wait_for(proc.wait(), timeout=timeout)
        return
    except TimeoutError:
        pass
    kill_tree(pid, handle=proc, force=True)
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(proc.wait(), timeout=2)


def record_pid(home, session_id: str, pid: int, create_time: float | None, image_name: str | None) -> None:
    path = pids_path(home)
    try:
        table = read_json(path, {})
        if not isinstance(table, dict):
            table = {}
        table[session_id] = {
            "pid": pid,
            "create_time": create_time,
            "image_name": image_name,
            # Owner identity lets a concurrently booting Bridge instance tell a
            # live sibling's worker apart from a true orphan (see reap_orphans).
            "owner_pid": os.getpid(),
            "owner_create_time": process_create_time(os.getpid()),
        }
        atomic_write_json(path, table)
    except OSError:
        log.warning("could not record pid for session %s", session_id, exc_info=True)


def drop_pid(home, session_id: str) -> None:
    path = pids_path(home)
    try:
        table = read_json(path, {})
        if isinstance(table, dict) and session_id in table:
            table.pop(session_id, None)
            atomic_write_json(path, table)
    except OSError:
        log.warning("could not drop pid for session %s", session_id, exc_info=True)


def owner_alive(pid: int | None, create_time: float | None) -> bool:
    if not isinstance(pid, int):
        return False
    try:
        proc = psutil.Process(pid)
        if not proc.is_running():
            return False
        if create_time is not None and abs(proc.create_time() - float(create_time)) >= 1.0:
            return False
    except (psutil.Error, TypeError, ValueError):
        return False
    return True


def _owner_alive(info: dict[str, Any]) -> bool:
    """True if the Bridge instance that recorded this worker is still running."""
    owner_pid = info.get("owner_pid")
    if not isinstance(owner_pid, int):
        return False
    return owner_alive(owner_pid, info.get("owner_create_time"))


def reap_orphans(home) -> list[int]:
    """Kill workers whose owning Bridge instance is gone.

    Runs at startup, before any new session spawns. Several Bridge instances
    can coexist (Codex and Cursor spawn their own), so a recorded worker is an
    orphan only when the instance that recorded it (owner_pid +
    owner_create_time) is no longer running; records with a live owner are
    kept untouched. For orphaned records, pid + create_time / image name
    verify the worker's identity before killing, so a recycled pid can never
    trigger a kill. Processed records are dropped either way; only live-owner
    records survive the rewrite.
    """
    path = pids_path(home)
    table = read_json(path, {})
    if not isinstance(table, dict):
        table = {}
    killed: list[int] = []
    kept: dict[str, Any] = {}
    for session_id, info in table.items():
        if not isinstance(info, dict):
            continue
        if _owner_alive(info):
            kept[session_id] = info
            continue
        pid = info.get("pid")
        create_time = info.get("create_time")
        image_name = info.get("image_name")
        if not isinstance(pid, int):
            continue
        try:
            proc = psutil.Process(pid)
        except psutil.Error:
            continue
        if not proc.is_running():
            continue
        match_image = True
        if image_name:
            try:
                match_image = Path(proc.exe()).name.lower() == str(image_name).lower()
            except (psutil.Error, OSError):
                match_image = False
        match_time = True
        if create_time is not None:
            try:
                match_time = abs(proc.create_time() - float(create_time)) < 1.0
            except (psutil.Error, TypeError, ValueError):
                match_time = False
        if match_image and match_time:
            log.warning("reaping orphan worker pid=%s session=%s", pid, session_id)
            kill_tree(pid)
            _wait_and_kill_tree(pid)
            killed.append(pid)
        else:
            log.info(
                "dropping stale pid record pid=%s session=%s (pid recycled by another process)",
                pid,
                session_id,
            )
    if table != kept:
        try:
            atomic_write_json(path, kept)
        except OSError:
            # A transient Windows file lock (antivirus, racing sibling boot)
            # must not abort server startup; the next boot retries.
            log.warning("could not rewrite pid table %s", path, exc_info=True)
    return killed


def python_executable() -> str:
    return sys.executable


def _proc_pid(proc: Any) -> int | None:
    pid = getattr(proc, "pid", None)
    if isinstance(pid, int):
        return pid
    info = getattr(proc, "info", None)
    if isinstance(info, dict) and isinstance(info.get("pid"), int):
        return info["pid"]
    return None


def _proc_ppid(proc: Any) -> int | None:
    info = getattr(proc, "info", None)
    if isinstance(info, dict) and isinstance(info.get("ppid"), int):
        return info["ppid"]
    ppid = getattr(proc, "ppid", None)
    if isinstance(ppid, int):
        return ppid
    if callable(ppid):
        try:
            value = ppid()
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            return None
        return value if isinstance(value, int) else None
    return None


def _proc_name(proc: Any) -> str:
    info = getattr(proc, "info", None)
    if isinstance(info, dict) and info.get("name") is not None:
        return str(info["name"])
    name = getattr(proc, "name", None)
    if callable(name):
        try:
            return str(name() or "")
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            return ""
    return str(name or "")


def _proc_cmdline(proc: Any) -> list[str]:
    info = getattr(proc, "info", None)
    if isinstance(info, dict) and "cmdline" in info:
        raw = info.get("cmdline") or []
        return [str(part) for part in raw]
    cmdline = getattr(proc, "cmdline", None)
    if callable(cmdline):
        try:
            raw = cmdline() or []
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            return []
        return [str(part) for part in raw]
    if isinstance(cmdline, (list, tuple)):
        return [str(part) for part in cmdline]
    return []


def _looks_like_agent_bridge(proc: Any) -> bool:
    name = _proc_name(proc).lower()
    if name.startswith("agent-bridge"):
        return True
    cmdline = _proc_cmdline(proc)
    if not cmdline:
        return False
    for part in cmdline:
        token = str(part).replace("\\", "/").lower()
        base = Path(token).name
        if base.startswith("agent-bridge"):
            return True
        if "agent_bridge" in token:
            return True
    return False


def _has_matched_bridge_ancestor(
    pid: int,
    matched: set[int],
    ppid_by_pid: Mapping[int, int | None],
) -> bool:
    """True if any ancestor of ``pid`` is also a counted Bridge process."""
    seen: set[int] = set()
    current = ppid_by_pid.get(pid)
    while current is not None and current not in seen:
        if current in matched:
            return True
        seen.add(current)
        current = ppid_by_pid.get(current)
    return False


def _descendant_pids(root: int, ppid_by_pid: Mapping[int, int | None]) -> set[int]:
    """Return every pid reachable from ``root`` by following child→parent links."""
    children: dict[int, list[int]] = {}
    for pid, ppid in ppid_by_pid.items():
        if ppid is None:
            continue
        children.setdefault(ppid, []).append(pid)
    found: set[int] = set()
    stack = list(children.get(root, []))
    while stack:
        pid = stack.pop()
        if pid in found:
            continue
        found.add(pid)
        stack.extend(children.get(pid, []))
    return found


def count_sibling_servers(
    processes: Iterable[Any] | Callable[..., Iterable[Any]] | None = None,
) -> int:
    """Count independent Agent Bridge server instances on this machine.

    An independent instance is another Coordinator (or an abandoned spawn),
    not a nested Bridge that this process started by launching a worker.
    Excludes the current process, its ancestor chain (launcher / uv wrapper),
    and every descendant — including a worker CLI that then inherited MCP
    and started a nested Bridge.

    One instance is a whole process tree (uv -> agent-bridge.exe -> python),
    so only tree roots among the remaining matches are counted.
    Fail-open: returns 0 on unexpected errors.

    An ``agent-bridge upgrade`` process is not an ancestor of a live top-level
    or nested Bridge, so those instances still count and still block upgrade.
    """
    try:
        me = psutil.Process()
        my_pid = me.pid
        ancestors: set[int] = set()
        with contextlib.suppress(psutil.NoSuchProcess, psutil.AccessDenied):
            ancestors.update(parent.pid for parent in me.parents())

        if processes is None:
            proc_iter: Iterable[Any] = psutil.process_iter(["pid", "ppid", "name", "cmdline"])
        elif callable(processes):
            proc_iter = processes(["pid", "ppid", "name", "cmdline"])
        else:
            proc_iter = processes

        ppid_by_pid: dict[int, int | None] = {}
        bridge_ppid: dict[int, int | None] = {}
        for proc in proc_iter:
            try:
                pid = _proc_pid(proc)
                if pid is None:
                    continue
                ppid = _proc_ppid(proc)
                ppid_by_pid[pid] = ppid
                if _looks_like_agent_bridge(proc):
                    bridge_ppid[pid] = ppid
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        exclude = {my_pid} | ancestors | _descendant_pids(my_pid, ppid_by_pid)
        matched = {pid for pid in bridge_ppid if pid not in exclude}
        return sum(
            1
            for pid in matched
            if not _has_matched_bridge_ancestor(pid, matched, ppid_by_pid)
        )
    except Exception:
        return 0
