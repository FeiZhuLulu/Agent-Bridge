"""Read the model Grok actually used on a turn.

Grok ``/new`` bakes a campaign identity into ``system_prompt.txt``
(currently ``You are Grok 4.6``). ``session/setModel`` changes the sampler
but does not rewrite that banner. Worker prose that quotes the banner is
not evidence of the selected model.

The live sampler is ``turn_started.model_id`` in the native session
``events.jsonl``. Effort, when present, is on ``summary.json``.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any
from urllib.parse import quote

log = logging.getLogger(__name__)


def grok_home(explicit: Path | None = None) -> Path:
    if explicit is not None:
        return explicit
    raw = os.environ.get("GROK_HOME")
    if raw:
        return Path(raw)
    return Path.home() / ".grok"


def grok_session_dir(cwd: str, native_id: str, home: Path | None = None) -> Path:
    encoded = quote(cwd, safe="")
    return grok_home(home) / "sessions" / encoded / native_id


def observe_grok_session(
    cwd: str | None,
    native_id: str | None,
    home: Path | None = None,
) -> dict[str, str | None]:
    """Return ``model`` / ``effort`` from Grok's on-disk session, or Nones."""
    if not cwd or not native_id:
        return {"model": None, "effort": None}
    folder = grok_session_dir(cwd, native_id, home)
    model = _last_turn_model(folder / "events.jsonl")
    effort = _summary_effort(folder / "summary.json")
    if model is None:
        model = _summary_model(folder / "summary.json")
    return {"model": model, "effort": effort}


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


_EVENTS_TAIL_BYTES = 1024 * 1024


def _last_turn_model(events_path: Path) -> str | None:
    try:
        size = events_path.stat().st_size
        with events_path.open("rb") as handle:
            if size > _EVENTS_TAIL_BYTES:
                handle.seek(size - _EVENTS_TAIL_BYTES)
            blob = handle.read()
    except OSError:
        return None
    lines = blob.decode("utf-8", errors="replace").splitlines()
    if size > _EVENTS_TAIL_BYTES and lines:
        lines = lines[1:]  # window start is almost always mid-line; drop that half-line
    for line in reversed(lines):
        if '"turn_started"' not in line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") != "turn_started":
            continue
        value = event.get("model_id")
        if isinstance(value, str) and value:
            return value
    return None


def _summary_model(path: Path) -> str | None:
    payload = _read_json(path)
    if payload is None:
        return None
    value = payload.get("current_model_id")
    return value if isinstance(value, str) and value else None


def _summary_effort(path: Path) -> str | None:
    payload = _read_json(path)
    if payload is None:
        return None
    value = payload.get("reasoning_effort")
    return value if isinstance(value, str) and value else None
