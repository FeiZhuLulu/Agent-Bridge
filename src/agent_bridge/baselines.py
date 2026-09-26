"""Immutable baseline records and persistent per-scope default selectors.

Storage layout under the Bridge home::

    baselines/records/<baseline_id>.json   # one immutable record per file
    baselines/defaults/<scope_hash>.json   # selector for one project+agent scope

Records are never rewritten: :meth:`BaselineStore.add` refuses an existing ID.
The default selector for a scope lives in its own file, so two Bridge
instances working on different projects (or with different workers) never
overwrite each other. Every read goes to disk on demand, which gives
independent instances cross-instance visibility without a lock or cache.
Corrupt files raise :class:`BaselineCorruptionError` instead of being folded
into an empty state.
"""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from agent_bridge.models import iso
from agent_bridge.persist import atomic_write_json, read_json

_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_MISSING = object()
_SCOPE_SEPARATOR = "\x00"


class BaselineCorruptionError(RuntimeError):
    """A baseline file exists but cannot be read as valid baseline state."""


class Baseline(BaseModel):
    """One immutable saved baseline, captured from a completed DSH turn."""

    baseline_id: str
    name: str
    description: str | None = None
    agent: str
    backend: str | None = None
    cwd: str
    native_session_id: str
    source_session_id: str
    model: str | None = None
    effort: str | None = None
    created_at: str = Field(default_factory=iso)


def _validate_id(baseline_id: object) -> str:
    """Reject anything that is not a safe single-segment filename component."""
    if not isinstance(baseline_id, str) or not _ID_PATTERN.fullmatch(baseline_id) or ".." in baseline_id:
        raise ValueError(f"invalid baseline id {baseline_id!r}")
    return baseline_id


def _canonical_cwd(cwd: str | os.PathLike[str]) -> str:
    if isinstance(cwd, os.PathLike):
        cwd = os.fspath(cwd)
    if not isinstance(cwd, str) or not cwd.strip():
        raise ValueError("cwd must be a non-empty path")
    return os.path.normcase(str(Path(cwd).expanduser().resolve()))


def _agent_key(agent: str) -> str:
    if not isinstance(agent, str) or not agent.strip():
        raise ValueError("agent must be a non-empty name")
    return agent.strip()


def _scope_identity(cwd: str | os.PathLike[str], agent: str) -> tuple[str, str]:
    return _canonical_cwd(cwd), _agent_key(agent)


def _scope_filename(cwd: str | os.PathLike[str], agent: str) -> str:
    material = f"{_canonical_cwd(cwd)}{_SCOPE_SEPARATOR}{_agent_key(agent)}"
    return f"{hashlib.sha256(material.encode('utf-8')).hexdigest()}.json"


def _read_strict(path: Path) -> tuple[bool, Any]:
    """Return ``(exists, payload)``; raise when the file is present but corrupt."""
    if not path.is_file():
        return False, None
    payload = read_json(path, _MISSING)
    if payload is _MISSING:
        raise BaselineCorruptionError(f"unreadable baseline state: {path}")
    return True, payload


class BaselineStore:
    """File-backed store for baseline records and per-scope default selectors."""

    def __init__(self, home: Path) -> None:
        self.home = Path(home)

    @property
    def records_dir(self) -> Path:
        return self.home / "baselines" / "records"

    @property
    def defaults_dir(self) -> Path:
        return self.home / "baselines" / "defaults"

    def _record_path(self, baseline_id: str) -> Path:
        return self.records_dir / f"{baseline_id}.json"

    def _default_path(self, cwd: str | os.PathLike[str], agent: str) -> Path:
        return self.defaults_dir / _scope_filename(cwd, agent)

    def _load_record(self, path: Path) -> Baseline:
        exists, payload = _read_strict(path)
        if not exists:
            raise KeyError(path.stem)
        try:
            return Baseline.model_validate(payload)
        except ValidationError as exc:
            raise BaselineCorruptionError(f"invalid baseline record {path}: {exc}") from exc

    @staticmethod
    def _validate_record(record: Baseline) -> None:
        if not record.name.strip():
            raise ValueError("baseline name must not be empty")
        if not record.native_session_id.strip():
            raise ValueError("baseline native_session_id must not be empty")
        if not record.source_session_id.strip():
            raise ValueError("baseline source_session_id must not be empty")
        _scope_identity(record.cwd, record.agent)

    def add(self, record: Baseline) -> None:
        """Persist a new immutable record; refuse to overwrite an existing ID."""
        baseline_id = _validate_id(record.baseline_id)
        self._validate_record(record)
        path = self._record_path(baseline_id)
        if path.exists():
            raise FileExistsError(f"baseline {baseline_id} already exists")
        atomic_write_json(path, record.model_dump(mode="json"))

    def get(self, baseline_id: str) -> Baseline:
        """Return one record; a missing ID raises :class:`KeyError`."""
        validated = _validate_id(baseline_id)
        path = self._record_path(validated)
        if not path.is_file():
            raise KeyError(baseline_id)
        return self._load_record(path)

    def list(self, cwd: str | os.PathLike[str], agent: str) -> list[Baseline]:
        """Return every record for this canonical project+agent scope, oldest first."""
        scope = _scope_identity(cwd, agent)
        records: list[Baseline] = []
        if self.records_dir.is_dir():
            for path in sorted(self.records_dir.glob("*.json")):
                record = self._load_record(path)
                if _scope_identity(record.cwd, record.agent) == scope:
                    records.append(record)
        records.sort(key=lambda item: (item.created_at, item.baseline_id))
        return records

    def get_default(self, cwd: str | os.PathLike[str], agent: str) -> str | None:
        """Return the saved default baseline id for a scope, or ``None``."""
        path = self._default_path(cwd, agent)
        exists, payload = _read_strict(path)
        if not exists:
            return None
        if not isinstance(payload, dict):
            raise BaselineCorruptionError(f"invalid baseline default {path}")
        baseline_id = payload.get("baseline_id")
        if baseline_id is None:
            return None
        try:
            return _validate_id(baseline_id)
        except ValueError as exc:
            raise BaselineCorruptionError(f"invalid baseline default {path}") from exc

    def set_default(self, cwd: str | os.PathLike[str], agent: str, baseline_id: str | None) -> None:
        """Set or clear the default for one scope without touching any record."""
        path = self._default_path(cwd, agent)
        if baseline_id is None:
            atomic_write_json(path, {"baseline_id": None})
            return
        validated = _validate_id(baseline_id)
        record = self.get(validated)
        if _scope_identity(record.cwd, record.agent) != _scope_identity(cwd, agent):
            raise ValueError("baseline belongs to a different project or worker")
        atomic_write_json(path, {"baseline_id": validated})
