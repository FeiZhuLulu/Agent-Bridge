from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent_bridge.models import TranscriptEvent
from agent_bridge.paths import transcript_path

log = logging.getLogger(__name__)

PAGE_BYTE_BUDGET = 8000
BUFFER_BYTE_LIMIT = 64 * 1024
BUFFER_MAX_AGE_SEC = 30.0
_PARSE_CACHE_MAX = 4
_parse_cache: dict[Path, tuple[tuple[int, int, int], list[dict[str, Any]]]] = {}


@dataclass
class _Buffer:
    lines: list[str] = field(default_factory=list)
    size: int = 0
    first_pending_at: float | None = None


_buffers: dict[Path, _Buffer] = {}
_buffers_lock = threading.Lock()


def _append_batch(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(text)


def _flush_locked(path: Path, buffer: _Buffer) -> None:
    if not buffer.lines:
        return
    text = "".join(buffer.lines)
    _append_batch(path, text)
    buffer.lines.clear()
    buffer.size = 0
    buffer.first_pending_at = None
    _parse_cache.pop(path, None)


def _pending_text(path: Path) -> str:
    with _buffers_lock:
        buffer = _buffers.get(path)
        return "".join(buffer.lines) if buffer else ""


def append_event(session_id: str, event_type: str, data: dict[str, Any] | None = None, home: Path | None = None) -> TranscriptEvent:
    event = TranscriptEvent(type=event_type, data=data or {})
    path = transcript_path(session_id, home)
    line = event.model_dump_json() + "\n"
    line_size = len(line.encode("utf-8"))
    now = time.monotonic()
    with _buffers_lock:
        buffer = _buffers.setdefault(path, _Buffer())
        if not buffer.lines:
            buffer.first_pending_at = now
        buffer.lines.append(line)
        buffer.size += line_size
        aged = (
            buffer.first_pending_at is not None
            and now - buffer.first_pending_at >= BUFFER_MAX_AGE_SEC
        )
        terminal = event_type in {"turn_end", "error"}
        if buffer.size >= BUFFER_BYTE_LIMIT or aged or terminal:
            _flush_locked(path, buffer)
        if terminal and not buffer.lines:
            _buffers.pop(path, None)
    return event


def flush_pending(home: Path | None = None) -> None:
    """Flush buffered transcript events, normally during Bridge shutdown."""
    root = home / "transcripts" if home is not None else None
    with _buffers_lock:
        for path, buffer in list(_buffers.items()):
            if root is not None and path.parent != root:
                continue
            _flush_locked(path, buffer)
            if not buffer.lines:
                _buffers.pop(path, None)


def flush_session(session_id: str, home: Path | None = None) -> None:
    """Flush one session after a turn, including adapter exception paths."""
    path = transcript_path(session_id, home)
    with _buffers_lock:
        buffer = _buffers.get(path)
        if buffer is None:
            return
        _flush_locked(path, buffer)
        if not buffer.lines:
            _buffers.pop(path, None)


def read_events(session_id: str, home: Path | None = None) -> list[dict[str, Any]]:
    """Parse a session transcript.

    Hits return the cached list object; callers must not mutate it.
    ``page_events`` and ``recent_activity`` only read the list.
    """
    path = transcript_path(session_id, home)
    pending = _pending_text(path)
    if path.is_file():
        st = path.stat()
        key = (st.st_size, st.st_mtime_ns, len(pending))
    else:
        if not pending:
            return []
        key = (0, 0, len(pending))
    cached = _parse_cache.get(path)
    if cached is not None and cached[0] == key:
        return cached[1]
    events: list[dict[str, Any]] = []
    persisted = path.read_text(encoding="utf-8") if path.is_file() else ""
    for line in (persisted + pending).splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            log.warning("skipping malformed transcript line in %s", path)
    if path not in _parse_cache and len(_parse_cache) >= _PARSE_CACHE_MAX:
        _parse_cache.pop(next(iter(_parse_cache)))
    _parse_cache[path] = (key, events)
    return events


def read_events_tail(
    session_id: str,
    home: Path | None = None,
    max_bytes: int = 16384,
) -> list[dict[str, Any]]:
    """Parse only the last ``max_bytes`` of a transcript.

    Snapshot polling (wait_task/check_task) only needs the last few events;
    re-reading a multi-megabyte transcript on every poll is wasted IO.
    """
    if max_bytes < 1:
        raise ValueError("max_bytes must be at least 1")
    path = transcript_path(session_id, home)
    pending = _pending_text(path).encode("utf-8")
    if not path.is_file() and not pending:
        return []
    persisted_size = path.stat().st_size if path.is_file() else 0
    total_size = persisted_size + len(pending)
    start = max(0, total_size - max_bytes)
    discard_first_line = False
    if path.is_file() and start < persisted_size:
        with path.open("rb") as fh:
            if start > 0:
                fh.seek(start - 1)
                discard_first_line = fh.read(1) != b"\n"
            fh.seek(start)
            blob = fh.read() + pending
    else:
        pending_start = max(0, start - persisted_size)
        if start > 0:
            if pending_start > 0:
                discard_first_line = pending[pending_start - 1 : pending_start] != b"\n"
            elif path.is_file() and persisted_size:
                with path.open("rb") as fh:
                    fh.seek(persisted_size - 1)
                    discard_first_line = fh.read(1) != b"\n"
        blob = pending[pending_start:]
    lines = blob.decode("utf-8", errors="replace").splitlines()
    if discard_first_line and lines:
        lines = lines[1:]  # the first line is cut mid-record
    events: list[dict[str, Any]] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def page_events(
    events: list[dict[str, Any]],
    offset: int = 0,
    limit: int = 50,
    kinds: Iterable[str] | None = None,
    max_bytes: int = PAGE_BYTE_BUDGET,
) -> dict[str, Any]:
    if offset < 0:
        raise ValueError("offset must be non-negative")
    if limit < 1:
        raise ValueError("limit must be at least 1")
    kind_set = set(kinds) if kinds else None
    selected = events
    if kind_set:
        selected = [e for e in selected if e.get("type") in kind_set]
    window = selected[offset:]
    page: list[dict[str, Any]] = []
    size = 2  # []
    for event in window:
        if len(page) >= limit:
            break
        encoded = json.dumps(event, ensure_ascii=False)
        extra = len(encoded.encode("utf-8")) + (1 if page else 0)
        if page and size + extra > max_bytes:
            break
        page.append(event)
        size += extra
    next_offset = offset + len(page)
    return {
        "events": page,
        "offset": offset,
        "count": len(page),
        "next_offset": next_offset,
        "has_more": next_offset < len(selected),
        "total_matching": len(selected),
    }


def recent_activity(events: list[dict[str, Any]], limit: int = 5) -> list[str]:
    summaries: list[str] = []
    for event in reversed(events):
        kind = event.get("type", "event")
        data = event.get("data") or {}
        text = data.get("text") or data.get("title") or data.get("path") or data.get("summary")
        if text:
            summaries.append(f"{kind}: {str(text)[:160]}")
        else:
            summaries.append(kind)
        if len(summaries) >= limit:
            break
    summaries.reverse()
    return summaries
