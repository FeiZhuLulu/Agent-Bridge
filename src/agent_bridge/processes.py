from __future__ import annotations

import logging
import shutil
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

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


def kill_tree(pid: int, timeout: float = 5.0) -> None:
    try:
        parent = psutil.Process(pid)
    except psutil.Error:
        return
    children = parent.children(recursive=True)
    for child in children:
        try:
            child.terminate()
        except psutil.Error:
            pass
    try:
        parent.terminate()
    except psutil.Error:
        return
    gone, alive = psutil.wait_procs([parent, *children], timeout=timeout)
    for leftover in alive:
        try:
            leftover.kill()
        except psutil.Error:
            pass


def record_pid(home, session_id: str, pid: int, create_time: float | None, image_name: str | None) -> None:
    path = pids_path(home)
    table = read_json(path, {})
    if not isinstance(table, dict):
        table = {}
    table[session_id] = {
        "pid": pid,
        "create_time": create_time,
        "image_name": image_name,
    }
    atomic_write_json(path, table)


def drop_pid(home, session_id: str) -> None:
    path = pids_path(home)
    table = read_json(path, {})
    if isinstance(table, dict) and session_id in table:
        table.pop(session_id, None)
        atomic_write_json(path, table)


def reap_orphans(home) -> list[int]:
    """Kill workers left over from a previous Bridge instance.

    Runs at startup, before any new session spawns, so every recorded pid is
    either ours (identity verified via create_time/image name, then killed)
    or stale (the process exited, or the OS recycled the pid for an unrelated
    process). pid + create_time uniquely identify a process, so a mismatch
    proves the original worker is gone and the record can never trigger a
    kill again. Either way the record has served its purpose: the table is
    always cleared instead of re-checking dead entries on every startup.
    """
    path = pids_path(home)
    table = read_json(path, {})
    if not isinstance(table, dict):
        table = {}
    killed: list[int] = []
    for session_id, info in table.items():
        if not isinstance(info, dict):
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
            killed.append(pid)
        else:
            log.info(
                "dropping stale pid record pid=%s session=%s (pid recycled by another process)",
                pid,
                session_id,
            )
    if table:
        atomic_write_json(path, {})
    return killed


def python_executable() -> str:
    return sys.executable
