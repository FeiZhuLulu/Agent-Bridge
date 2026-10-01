from __future__ import annotations

import contextlib
import importlib
import json
import os
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any, BinaryIO


class FileLockTimeout(TimeoutError):
    pass


def _try_lock(handle: BinaryIO) -> bool:
    handle.seek(0)
    if os.name == "nt":
        msvcrt = importlib.import_module("msvcrt")

        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True

    fcntl = importlib.import_module("fcntl")

    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    return True


def _unlock(handle: BinaryIO) -> None:
    handle.seek(0)
    if os.name == "nt":
        msvcrt = importlib.import_module("msvcrt")

        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return

    fcntl = importlib.import_module("fcntl")

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextlib.contextmanager
def file_lock(path: Path, *, timeout_sec: float = 5.0) -> Iterator[None]:
    """Hold an advisory lock that is shared by processes using this path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout_sec
    with path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        while not _try_lock(handle):
            if time.monotonic() >= deadline:
                raise FileLockTimeout(f"timed out waiting for lock {path}")
            time.sleep(0.05)
        try:
            yield
        finally:
            _unlock(handle)


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    except Exception:
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)
        raise


def atomic_write_json(path: Path, payload: Any) -> None:
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def read_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def read_json_strict(path: Path, default: Any) -> Any:
    """Read JSON without treating unreadable or invalid data as an empty file."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return default
    return json.loads(text)
