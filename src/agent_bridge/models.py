from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None = None) -> str:
    return (dt or utcnow()).isoformat()


class TaskStatus(StrEnum):
    queued = "queued"
    running = "running"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class ProcState(StrEnum):
    spawning = "spawning"
    ready = "ready"
    busy = "busy"
    idle_unloaded = "idle_unloaded"
    dead = "dead"


TERMINAL_STATUSES = {TaskStatus.completed, TaskStatus.failed, TaskStatus.cancelled}
DEFAULT_WAIT_SEC = 180.0
EFFORTS = ("off", "low", "medium", "high", "max")


def normalize_effort(raw: str | None) -> str | None:
    if raw is None:
        return None
    value = raw.strip().lower()
    aliases = {"l": "low", "m": "medium", "med": "medium", "h": "high"}
    value = aliases.get(value, value)
    if value not in EFFORTS:
        raise ValueError(f"effort must be one of off, low, medium, high, max (got {raw!r})")
    return value


def agy_effort(effort: str | None) -> str | None:
    if effort in {None, "low", "medium", "high"}:
        return effort
    if effort == "off":
        return "low"
    if effort == "max":
        return "high"
    return None


def dsh_effort(effort: str | None) -> str | None:
    if effort in {None, "off", "low", "high", "max"}:
        return effort
    if effort == "medium":
        return "high"
    return None


def grok_effort(effort: str | None) -> str | None:
    """Map Bridge effort onto Grok `_meta.reasoningEffort` wire values.

    Grok Build (`ReasoningEffort::from_str`) accepts
    ``none|minimal|low|medium|high|xhigh|max``. Bridge ``off`` is Grok
    ``none``. Bridge ``max`` maps to ``xhigh``: that is the top tier the
    grok-4.6 catalog actually advertises, whereas ``max`` is not listed for
    any current model and gets clamped by the worker.
    """
    if effort in {None, "low", "medium", "high"}:
        return effort
    if effort == "max":
        return "xhigh"
    if effort == "off":
        return "none"
    return None


class Session(BaseModel):
    session_id: str
    agent: str
    cwd: str
    native_session_id: str | None = None
    proc_state: ProcState = ProcState.idle_unloaded
    model: str | None = None
    effort: str | None = None
    title: str | None = None
    turns: int = 0
    created_at: str = Field(default_factory=iso)
    last_active_at: str = Field(default_factory=iso)
    pid: int | None = None
    pid_create_time: float | None = None
    image_name: str | None = None
    owner_pid: int | None = None
    owner_create_time: float | None = None


class Task(BaseModel):
    task_id: str
    session_id: str
    agent: str
    message: str
    cwd: str
    model: str | None = None
    effort: str | None = None
    observed_model: str | None = None
    observed_effort: str | None = None
    status: TaskStatus = TaskStatus.queued
    stop_reason: str | None = None
    result_text: str = ""
    result_chars: int = 0
    files_changed: list[str] = Field(default_factory=list)
    usage: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    warnings: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=iso)
    started_at: str | None = None
    finished_at: str | None = None
    owner_pid: int | None = None
    owner_create_time: float | None = None


class TranscriptEvent(BaseModel):
    ts: str = Field(default_factory=iso)
    type: str
    data: dict[str, Any] = Field(default_factory=dict)


class TurnResult(BaseModel):
    text: str = ""
    files_changed: list[str] = Field(default_factory=list)
    stop_reason: str = "end_turn"
    usage: dict[str, Any] = Field(default_factory=dict)
    native_session_id: str | None = None
    error: str | None = None
    warnings: list[str] = Field(default_factory=list)
    # Last model/effort the adapter successfully applied this turn. Grok and
    # Kimi observers in Registry overwrite these with on-disk sampler truth.
    observed_model: str | None = None
    observed_effort: str | None = None
