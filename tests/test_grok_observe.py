from __future__ import annotations

import json
from pathlib import Path

from agent_bridge.grok_observe import grok_session_dir, observe_grok_session


def _write_session(home: Path, cwd: str, native_id: str) -> Path:
    folder = grok_session_dir(cwd, native_id, home)
    folder.mkdir(parents=True)
    events = [
        {"type": "session_created"},
        {
            "type": "turn_started",
            "turn_number": 0,
            "model_id": "grok-4.5",
        },
        {"type": "phase_changed", "phase": "streaming_reasoning"},
        {
            "type": "turn_started",
            "turn_number": 1,
            "model_id": "grok-4.6",
        },
    ]
    (folder / "events.jsonl").write_text(
        "".join(json.dumps(event) + "\n" for event in events),
        encoding="utf-8",
    )
    (folder / "summary.json").write_text(
        json.dumps({"current_model_id": "grok-4.6", "reasoning_effort": "medium"}),
        encoding="utf-8",
    )
    (folder / "system_prompt.txt").write_text("You are Grok 4.6 released by xAI.\n", encoding="utf-8")
    return folder


def test_session_dir_percent_encodes_windows_cwd(tmp_path):
    cwd = r"C:\Users\someone\Documents\work"
    folder = grok_session_dir(cwd, "native-1", tmp_path)
    assert folder.parent.name.startswith("C%3A%5CUsers%5C")
    assert folder.name == "native-1"


def test_observe_uses_last_turn_started_not_system_prompt(tmp_path):
    cwd = r"C:\work\grok-new-45"
    observed = observe_grok_session(cwd, "native-1", tmp_path)
    assert observed == {"model": None, "effort": None}
    _write_session(tmp_path, cwd, "native-1")
    observed = observe_grok_session(cwd, "native-1", tmp_path)
    assert observed == {"model": "grok-4.6", "effort": "medium"}


def test_observe_reads_first_turn_when_only_one_exists(tmp_path):
    cwd = r"C:\work\only-45"
    folder = grok_session_dir(cwd, "native-2", tmp_path)
    folder.mkdir(parents=True)
    (folder / "events.jsonl").write_text(
        json.dumps({"type": "turn_started", "model_id": "grok-4.5"}) + "\n",
        encoding="utf-8",
    )
    (folder / "summary.json").write_text(
        json.dumps({"current_model_id": "grok-4.5", "reasoning_effort": "medium"}),
        encoding="utf-8",
    )
    assert observe_grok_session(cwd, "native-2", tmp_path) == {
        "model": "grok-4.5",
        "effort": "medium",
    }
