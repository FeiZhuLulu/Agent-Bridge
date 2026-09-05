from __future__ import annotations

import os
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path

# Forced into every real worker process so a nested Agent Bridge MCP
# (inherited from the user's host config) can refuse writes and use a
# separate data directory.
WORKER_CONTEXT_ENV = "AGENT_BRIDGE_PARENT_CONTEXT"
WORKER_CONTEXT_VALUE = "worker"
NESTED_HOME_NAME = "nested"


def parent_context_is_worker(env: Mapping[str, str] | None = None) -> bool:
    source = os.environ if env is None else env
    return source.get(WORKER_CONTEXT_ENV) == WORKER_CONTEXT_VALUE


def nested_bridge_home(base: Path) -> Path:
    """Return ``base/nested``, or ``base`` if it is already that leaf."""
    resolved = base.expanduser().resolve()
    if resolved.name == NESTED_HOME_NAME:
        return resolved
    return (resolved / NESTED_HOME_NAME).resolve()


def bridge_home() -> Path:
    override = os.environ.get("AGENT_BRIDGE_HOME")
    base = Path(override).expanduser().resolve() if override else Path.home() / ".agent-bridge"
    if parent_context_is_worker():
        return nested_bridge_home(base)
    return base


def ensure_home(home: Path | None = None) -> Path:
    path = home or bridge_home()
    (path / "transcripts").mkdir(parents=True, exist_ok=True)
    (path / "results").mkdir(parents=True, exist_ok=True)
    (path / "logs").mkdir(parents=True, exist_ok=True)
    return path


def state_path(home: Path | None = None) -> Path:
    return ensure_home(home) / "state.json"


def pids_path(home: Path | None = None) -> Path:
    return ensure_home(home) / "pids.json"


def transcript_path(session_id: str, home: Path | None = None) -> Path:
    return (home or bridge_home()) / "transcripts" / f"{session_id}.jsonl"


def result_path(task_id: str, home: Path | None = None) -> Path:
    return (home or bridge_home()) / "results" / f"{task_id}.txt"


def log_path(home: Path | None = None) -> Path:
    day = datetime.now().strftime("%Y%m%d")
    return ensure_home(home) / "logs" / f"bridge-{day}.log"


def bundled_agents_toml() -> Path:
    here = Path(__file__).resolve()
    for candidate in (
        here.parent / "agents.toml",
        here.parents[1] / "agents.toml",
        here.parents[2] / "agents.toml",
    ):
        if candidate.is_file():
            return candidate
    return here.parents[2] / "agents.toml"


def bundled_skill() -> Path:
    here = Path(__file__).resolve()
    for candidate in (
        here.parent / "share" / "skills" / "agent-bridge" / "SKILL.md",
        here.parents[2] / "skills" / "agent-bridge" / "SKILL.md",
    ):
        if candidate.is_file():
            return candidate
    return here.parent / "share" / "skills" / "agent-bridge" / "SKILL.md"


def bundled_dsh_cordis() -> Path:
    here = Path(__file__).resolve()
    for candidate in (
        here.parent / "share" / "dsh-acp.cordis.yml",
        here.parents[1] / "share" / "dsh-acp.cordis.yml",
        here.parents[2] / "src" / "agent_bridge" / "share" / "dsh-acp.cordis.yml",
    ):
        if candidate.is_file():
            return candidate
    return here.parent / "share" / "dsh-acp.cordis.yml"
