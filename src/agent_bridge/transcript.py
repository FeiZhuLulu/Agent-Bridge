from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Iterable

from agent_bridge.models import TranscriptEvent
from agent_bridge.paths import transcript_path
from agent_bridge.persist import read_json

log = logging.getLogger(__name__)

PAGE_BYTE_BUDGET = 8000


def append_event(session_id: str, event_type: str, data: dict[str, Any] | None = None, home: Path | None = None) -> TranscriptEvent:
    event = TranscriptEvent(type=event_type, data=data or {})
    path = transcript_path(session_id, home)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(event.model_dump_json() + "\n")
    return event


def read_events(session_id: str, home: Path | None = None) -> list[dict[str, Any]]:
    path = transcript_path(session_id, home)
    if not path.is_file():
        return []
    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            log.warning("skipping malformed transcript line in %s", path)
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
    path = transcript_path(session_id, home)
    if not path.is_file():
        return []
    size = path.stat().st_size
    with path.open("rb") as fh:
        if size > max_bytes:
            fh.seek(size - max_bytes)
        blob = fh.read()
    lines = blob.decode("utf-8", errors="replace").splitlines()
    if size > max_bytes and lines:
        lines = lines[1:]  # the first line is almost certainly cut mid-record
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
