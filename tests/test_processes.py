import os
from pathlib import Path

import pytest

from agent_bridge.paths import pids_path
from agent_bridge.persist import atomic_write_json, read_json
from agent_bridge.processes import reap_orphans, resolve_command


def test_resolve_python(tmp_path):
    script = tmp_path / "x.py"
    script.write_text("print(1)\n", encoding="utf-8")
    import sys

    resolved = resolve_command([sys.executable, str(script)])
    assert Path(resolved[0]).exists()


def test_skips_grok_agent_as_cursor(monkeypatch, tmp_path):
    grok_agent = tmp_path / ".grok" / "bin" / "agent.exe"
    grok_agent.parent.mkdir(parents=True)
    grok_agent.write_text("fake", encoding="utf-8")

    def fake_which(name: str):
        if name in {"agent", "agent.exe"}:
            return str(grok_agent)
        return None

    monkeypatch.setattr("agent_bridge.processes.resolve_executable", fake_which)
    with pytest.raises(FileNotFoundError, match="not Cursor"):
        resolve_command(["cursor-agent", "acp"], [["agent", "acp"]])


def test_resolve_command_skips_invalid_candidate(tmp_path: Path):
    import sys

    script = tmp_path / "ok.py"
    script.write_text("print(1)\n", encoding="utf-8")
    missing = tmp_path / "missing.py"
    resolved = resolve_command(
        [sys.executable, str(missing)],
        [[sys.executable, str(script)]],
        validate=lambda cmd: "missing script" if not Path(cmd[-1]).is_file() else None,
    )
    assert resolved[-1] == str(script)


def test_reap_orphans_drops_stale_records_without_killing(tmp_path: Path):
    # A live pid (this test process) whose recorded identity does not match:
    # simulates the OS recycling a dead worker's pid for an unrelated process.
    # It must not be killed, and the stale record must not survive the pass.
    atomic_write_json(
        pids_path(tmp_path),
        {
            "sess_recycled": {
                "pid": os.getpid(),
                "create_time": 1.0,  # wrong on purpose
                "image_name": "definitely-not-this-process.exe",
            },
            "sess_gone": {
                "pid": 2_000_000_001,  # valid pid range, almost surely unused
                "create_time": 1.0,
                "image_name": "ghost.exe",
            },
        },
    )
    killed = reap_orphans(tmp_path)
    assert killed == []
    assert read_json(pids_path(tmp_path), {}) == {}
