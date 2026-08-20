from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path


def bridge_home() -> Path:
    override = os.environ.get("AGENT_BRIDGE_HOME")
    if override:
        return Path(override).expanduser().resolve()
    return Path.home() / ".agent-bridge"


def ensure_home(home: Path | None = None) -> Path:
    path = home or bridge_home()
    (path / "transcripts").mkdir(parents=True, exist_ok=True)
    (path / "logs").mkdir(parents=True, exist_ok=True)
    return path


def state_path(home: Path | None = None) -> Path:
    return ensure_home(home) / "state.json"


def pids_path(home: Path | None = None) -> Path:
    return ensure_home(home) / "pids.json"


def transcript_path(session_id: str, home: Path | None = None) -> Path:
    return ensure_home(home) / "transcripts" / f"{session_id}.jsonl"


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
