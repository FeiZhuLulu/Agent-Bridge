from pathlib import Path

from agent_bridge.persist import atomic_write_text


def test_atomic_write_text_replaces_and_leaves_no_tmp(tmp_path: Path):
    target = tmp_path / "note.txt"
    atomic_write_text(target, "one")
    atomic_write_text(target, "two")
    assert target.read_text(encoding="utf-8") == "two"
    assert list(tmp_path.glob("*.tmp*")) == []
