import os
from pathlib import Path

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
    changed = merge_files_changed(tmp_path, [], before)
    assert changed == ["smoke.txt"]


def test_merges_protocol_paths_as_cwd_relative(tmp_path: Path):
    before = snapshot_workspace(tmp_path)
    target = tmp_path / "src" / "app.py"
    target.parent.mkdir()
    target.write_text("print(1)\n", encoding="utf-8")
    changed = merge_files_changed(tmp_path, [str(target)], before)
    assert changed == ["src/app.py"]


def test_drops_cwd_dot_and_root_paths(tmp_path: Path):
    before = snapshot_workspace(tmp_path)
    (tmp_path / "ok.txt").write_text("x\n", encoding="utf-8")
    changed = merge_files_changed(tmp_path, [".", str(tmp_path), str(tmp_path / "ok.txt")], before)
    assert changed == ["ok.txt"]


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

    (tmp_path / "src" / "a.py").write_text("print(2)\n", encoding="utf-8")
    assert merge_files_changed(tmp_path, [], before) == ["src/a.py"]

    (nested / "b.txt").unlink()
    (tmp_path / "src" / "c.py").write_text("print(3)\n", encoding="utf-8")
    changed = set(merge_files_changed(tmp_path, [], before))
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
