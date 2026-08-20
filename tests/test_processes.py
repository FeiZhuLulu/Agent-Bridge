import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_bridge.paths import pids_path
from agent_bridge.persist import atomic_write_json, read_json
from agent_bridge.processes import (
    count_sibling_servers,
    drop_pid,
    process_create_time,
    process_image_name,
    reap_orphans,
    record_pid,
    resolve_command,
)

SLEEPER = [sys.executable, "-c", "import time; time.sleep(60)"]


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


def test_reap_orphans_spares_workers_of_live_owner(tmp_path: Path):
    # Regression: a freshly booting Bridge instance must not kill a worker
    # that another, still-running instance is actively using.
    child = subprocess.Popen(SLEEPER)
    try:
        # record_pid stamps this test process as the owner, which is alive.
        record_pid(
            tmp_path,
            "sess_live",
            child.pid,
            process_create_time(child.pid),
            process_image_name(child.pid),
        )
        killed = reap_orphans(tmp_path)
        assert killed == []
        assert child.poll() is None
        table = read_json(pids_path(tmp_path), {})
        assert "sess_live" in table
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=10)


def test_reap_orphans_kills_orphaned_workers(tmp_path: Path):
    orphan_a = subprocess.Popen(SLEEPER)
    orphan_b = subprocess.Popen(SLEEPER)
    dead_owner = subprocess.Popen([sys.executable, "-c", "pass"])
    dead_owner_create = process_create_time(dead_owner.pid)
    dead_owner.wait(timeout=30)
    try:
        atomic_write_json(
            pids_path(tmp_path),
            {
                # The owning Bridge instance exited: its worker is a true orphan.
                "sess_dead_owner": {
                    "pid": orphan_a.pid,
                    "create_time": process_create_time(orphan_a.pid),
                    "image_name": process_image_name(orphan_a.pid),
                    "owner_pid": dead_owner.pid,
                    "owner_create_time": dead_owner_create,
                },
                # Pre-upgrade record without owner fields gets the same treatment.
                "sess_legacy": {
                    "pid": orphan_b.pid,
                    "create_time": process_create_time(orphan_b.pid),
                    "image_name": process_image_name(orphan_b.pid),
                },
            },
        )
        killed = reap_orphans(tmp_path)
        assert sorted(killed) == sorted([orphan_a.pid, orphan_b.pid])
        orphan_a.wait(timeout=10)
        orphan_b.wait(timeout=10)
        assert read_json(pids_path(tmp_path), {}) == {}
    finally:
        for proc in (orphan_a, orphan_b):
            if proc.poll() is None:
                proc.kill()


def test_count_sibling_servers_with_injected_processes(monkeypatch):
    me = os.getpid()
    fake_parent = SimpleNamespace(pid=me + 1)
    monkeypatch.setattr(
        "agent_bridge.processes.psutil.Process",
        lambda: SimpleNamespace(pid=me, parents=lambda: [fake_parent]),
    )
    procs = [
        # Our own launcher and uv wrapper: excluded via pid / ancestor chain.
        SimpleNamespace(pid=me, info={"pid": me, "ppid": me + 1, "name": "agent-bridge.exe", "cmdline": []}),
        SimpleNamespace(
            pid=me + 1,
            info={"pid": me + 1, "ppid": 1, "name": "uv.exe", "cmdline": ["uv", "run", "agent-bridge"]},
        ),
        # One foreign instance = a full uv -> launcher -> python chain: counts once.
        SimpleNamespace(
            pid=me + 10,
            info={"pid": me + 10, "ppid": 1, "name": "uv.exe", "cmdline": ["uv", "run", "agent-bridge"]},
        ),
        SimpleNamespace(
            pid=me + 11,
            info={"pid": me + 11, "ppid": me + 10, "name": "agent-bridge.exe", "cmdline": []},
        ),
        SimpleNamespace(
            pid=me + 12,
            info={
                "pid": me + 12,
                "ppid": me + 11,
                "name": "python.exe",
                "cmdline": ["python.exe", "C:/venv/Scripts/agent-bridge.exe"],
            },
        ),
        # A dev-mode instance run as `python -m agent_bridge`: counts once.
        SimpleNamespace(
            pid=me + 3,
            info={"pid": me + 3, "ppid": 2, "name": "python.exe", "cmdline": ["python", "-m", "agent_bridge"]},
        ),
        SimpleNamespace(pid=me + 4, info={"pid": me + 4, "ppid": 1, "name": "notepad.exe", "cmdline": ["notepad"]}),
    ]
    assert count_sibling_servers(procs) == 2
    assert count_sibling_servers([]) == 0

    def boom(_attrs=None):
        raise RuntimeError("boom")

    assert count_sibling_servers(boom) == 0


def test_record_and_drop_pid_swallow_oserror(tmp_path: Path, monkeypatch):
    record_pid(tmp_path, "sess_keep", os.getpid(), 1.0, "python.exe")
    assert "sess_keep" in read_json(pids_path(tmp_path), {})

    def boom(*_args, **_kwargs):
        raise OSError("locked")

    monkeypatch.setattr("agent_bridge.processes.atomic_write_json", boom)
    record_pid(tmp_path, "sess_new", os.getpid(), 1.0, "python.exe")
    drop_pid(tmp_path, "sess_keep")
