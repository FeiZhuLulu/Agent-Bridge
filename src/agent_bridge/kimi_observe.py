"""Read what Kimi actually did on a turn.

Kimi's ACP host maps a *failed* turn to ``stopReason: end_turn`` with no text
and nothing on the JSON-RPC error channel (its ``turnEndReasonToStopReason``
only special-cases auth codes and ``provider.filtered``). Over ACP a quota or
provider error is therefore indistinguishable from a clean no-op, which would
let a coordinator accept an empty diff as success.

The session's ``wire.jsonl`` records the truth: a terminal ``turn.ended`` with
its reason and error, plus the ``llm.request`` naming the model and thinking
effort the turn really ran with. This is Kimi's analogue of the Grok
``events.jsonl`` read in ``grok_observe``.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# wire.jsonl inlines system-prompt and tool snapshots, so it passes a few
# hundred KB after one trivial turn and single lines run to tens of KB. Only
# the tail matters: the last llm.request and the terminal turn.ended.
TAIL_BYTES = 1024 * 1024

# Kimi ends a turn with completed | cancelled | failed | blocked. Only the last
# two are faults: `cancelled` is how Bridge's own cancel_task lands here, and
# reporting it as a failure would contradict the task's cancelled status.
TURN_REASONS_OK = frozenset({"completed", "cancelled"})


def kimi_home(explicit: Path | None = None) -> Path:
    if explicit is not None:
        return explicit
    raw = os.environ.get("KIMI_CODE_HOME")
    if raw:
        return Path(raw)
    return Path.home() / ".kimi-code"


def kimi_wire_path(native_id: str, home: Path | None = None) -> Path | None:
    """Locate a session's main-agent wire log.

    Sessions live at ``sessions/<workDirKey>/<sessionId>/``, where the key is a
    slug of the cwd. Session ids are unique on their own, so glob for the id
    instead of recomputing Kimi's slug — one less thing to keep in sync with
    the CLI.
    """
    root = kimi_home(home) / "sessions"
    try:
        matches = sorted(root.glob(f"*/{native_id}/agents/main/wire.jsonl"))
    except OSError:
        return None
    return matches[0] if matches else None


def observe_kimi_session(
    native_id: str | None,
    home: Path | None = None,
) -> dict[str, str | None]:
    """Return ``model`` / ``effort`` / ``failure`` for the session's last turn.

    Runs in the post-turn path, so every failure mode answers Nones instead of
    raising.
    """
    blank: dict[str, str | None] = {"model": None, "effort": None, "failure": None}
    if not native_id:
        return blank
    path = kimi_wire_path(native_id, home)
    if path is None:
        return blank
    records = _tail_records(path)
    if not records:
        return blank
    model, effort = _last_request(records)
    return {"model": model, "effort": effort, "failure": _last_failure(records)}


def _tail_records(path: Path) -> list[dict[str, Any]]:
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            if size > TAIL_BYTES:
                handle.seek(size - TAIL_BYTES)
            blob = handle.read()
    except OSError:
        return []
    lines = blob.decode("utf-8", errors="replace").splitlines()
    if size > TAIL_BYTES and lines:
        # A window that starts mid-file almost always opens mid-line. That
        # fragment is not valid JSON and its record is too old to matter.
        lines = lines[1:]
    records: list[dict[str, Any]] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            records.append(payload)
    return records


def _last_request(records: list[dict[str, Any]]) -> tuple[str | None, str | None]:
    model: str | None = None
    effort: str | None = None
    for record in records:
        if record.get("type") != "llm.request":
            continue
        # modelAlias is the slug the coordinator asked for; `model` is the
        # provider-side name the alias resolves to.
        alias = record.get("modelAlias") or record.get("model")
        if isinstance(alias, str) and alias:
            model = alias
        level = record.get("thinkingEffort")
        if isinstance(level, str) and level:
            effort = level
    return model, effort


def _last_failure(records: list[dict[str, Any]]) -> str | None:
    reason: Any = None
    error: Any = None
    for record in records:
        if record.get("type") != "turn.ended":
            continue
        reason = record.get("reason")
        error = record.get("error")
    if not isinstance(reason, str) or reason in TURN_REASONS_OK:
        return None
    parts = [reason]
    if isinstance(error, dict):
        for key in ("code", "message"):
            value = error.get(key)
            if isinstance(value, str) and value:
                parts.append(value)
    return ": ".join(parts)
