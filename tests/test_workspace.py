from pathlib import Path

from agent_bridge.workspace import (
    collect_update_paths,
    merge_files_changed,
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
