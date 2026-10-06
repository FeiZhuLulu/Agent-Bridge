import os
from pathlib import Path

import pytest

from agent_bridge.workspace import (
    _root_prefix_len,
    collect_update_paths,
    merge_files_changed,
    normalize_changed_paths,
    snapshot_workspace,
)


def test_snapshot_sees_new_file_and_ignores_sessions(tmp_path: Path):
    before = snapshot_workspace(tmp_path)
    (tmp_path / "smoke.txt").write_text("hello-bridge\n", encoding="utf-8")
    sessions = tmp_path / ".sessions"
    sessions.mkdir()
    (sessions / "log.jsonl").write_text("{}\n", encoding="utf-8")
    changed, outside = merge_files_changed(tmp_path, [], before)
    assert changed == ["smoke.txt"]
    assert outside == []


def test_merges_protocol_paths_as_cwd_relative(tmp_path: Path):
    before = snapshot_workspace(tmp_path)
    target = tmp_path / "src" / "app.py"
    target.parent.mkdir()
    target.write_text("print(1)\n", encoding="utf-8")
    changed, outside = merge_files_changed(tmp_path, [str(target)], before)
    assert changed == ["src/app.py"]
    assert outside == []


def test_drops_cwd_dot_and_root_paths(tmp_path: Path):
    before = snapshot_workspace(tmp_path)
    (tmp_path / "ok.txt").write_text("x\n", encoding="utf-8")
    changed, outside = merge_files_changed(
        tmp_path, [".", str(tmp_path), str(tmp_path / "ok.txt")], before
    )
    assert changed == ["ok.txt"]
    assert outside == []


def test_snapshot_skips_build_dirs_and_tracks_changes(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("print(1)\n", encoding="utf-8")
    (tmp_path / "dist").mkdir()
    (tmp_path / "dist" / "x.js").write_text("export {}\n", encoding="utf-8")
    (tmp_path / ".next").mkdir()
    (tmp_path / ".next" / "y").write_text("cache\n", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "z").write_text("pkg\n", encoding="utf-8")
    nested = tmp_path / "nested" / "deep"
    nested.mkdir(parents=True)
    (nested / "b.txt").write_text("keep\n", encoding="utf-8")

    before = snapshot_workspace(tmp_path)
    assert set(before) == {"src/a.py", "nested/deep/b.txt"}
    assert "\\" not in "".join(before)

    # A same-length rewrite can keep both size and mtime_ns on a coarse
    # filesystem clock, so the payload length has to change.
    (tmp_path / "src" / "a.py").write_text("print(22)\n", encoding="utf-8")
    assert merge_files_changed(tmp_path, [], before)[0] == ["src/a.py"]

    (nested / "b.txt").unlink()
    (tmp_path / "src" / "c.py").write_text("print(3)\n", encoding="utf-8")
    changed = set(merge_files_changed(tmp_path, [], before)[0])
    assert "nested/deep/b.txt" in changed
    assert "src/c.py" in changed
    assert "src/a.py" in changed


def test_root_prefix_len_at_drive_root_and_normal_path(tmp_path: Path):
    if os.sep == "\\":
        assert _root_prefix_len("C:" + os.sep) == 3
    assert _root_prefix_len(str(tmp_path)) == len(str(tmp_path)) + 1


def test_normalize_ignores_skip_dirs_at_any_depth(tmp_path: Path):
    assert normalize_changed_paths(
        tmp_path,
        ["src/dist/x.js", "src/build", "node_modules/a.js", "src/ok.py"],
    ) == ["src/build", "src/ok.py"]


def test_collect_nested_tool_paths():
    found: set[str] = set()
    collect_update_paths(
        {
            "toolCall": {
                "parameters": {"TargetFile": "README.md"},
                "locations": [{"path": "src/main.py"}],
            }
        },
        found,
    )
    assert "README.md" in found
    assert "src/main.py" in found


def test_classify_splits_inside_and_outside_paths(tmp_path: Path):
    from agent_bridge.workspace import classify_changed_paths

    inside, outside = classify_changed_paths(
        tmp_path,
        [
            "src/ok.py",
            "../escape.txt",
            str(tmp_path.parent / "sibling.py"),
            str(tmp_path / "in.txt"),
        ],
    )
    assert inside == ["src/ok.py", "in.txt"]
    assert len(outside) == 2
    assert not any(".." in p for p in inside)
    # E3 regression: ../escape.txt must NOT fold to escape.txt inside cwd.
    assert "escape.txt" not in inside


def test_dotdot_absolute_outside_goes_to_outside_list(tmp_path: Path):
    inside, outside = merge_files_changed(
        tmp_path, [os.path.join("..", "..", "far_away.txt")], {}
    )
    assert inside == []
    assert outside and outside[0].endswith("far_away.txt")


def test_bridge_dirs_are_skipped_in_snapshot(tmp_path: Path):
    for d in (".agent-bridge", ".codex", ".claude"):
        sub = tmp_path / d
        sub.mkdir()
        (sub / "state.json").write_text("{}\n", encoding="utf-8")
    (tmp_path / "real.py").write_text("x\n", encoding="utf-8")
    assert set(snapshot_workspace(tmp_path)) == {"real.py"}


@pytest.mark.skipif(os.name != "nt", reason="Windows junction repro")
def test_junction_inside_cwd_is_not_traversed(tmp_path: Path):
    import subprocess

    target = tmp_path.parent / "junction_target"
    target.mkdir(exist_ok=True)
    (target / "foreign.txt").write_text("out\n", encoding="utf-8")
    link = tmp_path / "link"
    subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        check=True,
        capture_output=True,
    )
    snap = snapshot_workspace(tmp_path)
    assert "link/foreign.txt" not in snap
