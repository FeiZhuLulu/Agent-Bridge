from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_bridge.cli import (
    checkout_root,
    detect_install,
    ensure_coordinator_skill,
    install_skill,
    main,
    skill_destinations,
    upgrade,
)
from agent_bridge.paths import bundled_skill


def test_bundled_skill_is_present_in_checkout():
    path = bundled_skill()
    assert path.is_file()
    assert path.name == "SKILL.md"
    assert "dispatch_task" in path.read_text(encoding="utf-8")


def test_detect_uv_tool_install(tmp_path):
    exe = tmp_path / "uv" / "tools" / "agent-bridge" / "Scripts" / "python.exe"
    exe.parent.mkdir(parents=True)
    exe.write_text("", encoding="utf-8")
    assert detect_install(executable=exe, start=tmp_path / "nope.py") == "uv-tool"


def test_detect_checkout():
    assert detect_install() == "checkout"
    assert checkout_root() is not None
    assert (checkout_root() / "pyproject.toml").is_file()


def test_skill_destinations_include_zcode_and_claude(tmp_path):
    dests = skill_destinations(tmp_path)
    assert any(part == ".zcode" for dest in dests for part in dest.parts)
    assert any(part == ".claude" for dest in dests for part in dest.parts)
    assert dests[-1] == tmp_path / ".claude" / "skills" / "agent-bridge" / "SKILL.md"


def test_install_skill_writes_host_dirs(tmp_path):
    written = install_skill(home=tmp_path)
    expected = skill_destinations(tmp_path)
    assert written == expected
    assert any(path.parts[-4:-3] == (".zcode",) or ".zcode" in path.parts for path in written)
    zcode = tmp_path / ".zcode" / "skills" / "agent-bridge" / "SKILL.md"
    assert zcode.is_file()
    for dest in expected:
        assert dest.is_file()
        assert dest.read_bytes() == bundled_skill().read_bytes()


def test_install_skill_only_existing(tmp_path):
    dest = tmp_path / ".cursor" / "skills" / "agent-bridge" / "SKILL.md"
    dest.parent.mkdir(parents=True)
    dest.write_text("old", encoding="utf-8")
    written = install_skill(home=tmp_path, only_existing=True)
    assert written == [dest]
    assert dest.read_bytes() == bundled_skill().read_bytes()
    assert not (tmp_path / ".codex" / "skills" / "agent-bridge" / "SKILL.md").exists()


def test_upgrade_refuses_while_siblings_live():
    with pytest.raises(RuntimeError, match="still running"):
        upgrade(siblings=2, kind="uv-tool")


def test_upgrade_uv_tool(monkeypatch, tmp_path):
    dest = tmp_path / ".agents" / "skills" / "agent-bridge" / "SKILL.md"
    dest.parent.mkdir(parents=True)
    dest.write_text("old", encoding="utf-8")
    monkeypatch.setattr("agent_bridge.cli.Path.home", lambda: tmp_path)
    monkeypatch.setattr("agent_bridge.cli.shutil.which", lambda name: f"/bin/{name}")
    calls: list[list[str]] = []

    def fake_run(command, **_kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0)

    notes = upgrade(kind="uv-tool", siblings=0, run=fake_run)
    assert calls == [["/bin/uv", "tool", "upgrade", "agent-bridge"]]
    assert dest.read_bytes() == bundled_skill().read_bytes()
    assert any("uv tool upgrade" in note for note in notes)


def test_upgrade_unknown_tells_user_to_install():
    with pytest.raises(RuntimeError, match="uv tool install"):
        upgrade(kind="unknown", siblings=0)


def test_help_and_version(capsys):
    main(["help"])
    out = capsys.readouterr().out
    assert "uv tool install" in out
    assert "Claude Code" in out
    assert "install-skill" not in out.split("Install:")[1].split("Update:")[0]
    main(["--version"])
    assert capsys.readouterr().out.strip()


def test_ensure_coordinator_skill_skips_under_pytest(tmp_path, monkeypatch):
    monkeypatch.setattr("agent_bridge.cli.Path.home", lambda: tmp_path)
    assert ensure_coordinator_skill() == []
    assert not any(path.exists() for path in skill_destinations(tmp_path))
    written = ensure_coordinator_skill(home=tmp_path)
    assert written
    assert all(path.is_file() for path in written)


def test_ensure_coordinator_skill_skips_worker_context(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_BRIDGE_PARENT_CONTEXT", "worker")
    assert ensure_coordinator_skill(home=tmp_path) == []
    assert not any(path.exists() for path in skill_destinations(tmp_path))


def test_install_skill_works_in_worker_context(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_BRIDGE_PARENT_CONTEXT", "worker")
    written = install_skill(home=tmp_path)
    assert written
    assert (tmp_path / ".zcode" / "skills" / "agent-bridge" / "SKILL.md").is_file()
