from pathlib import Path

import pytest

from agent_bridge.workspace import (
    collect_update_paths,
    merge_files_changed,
    snapshot_workspace,
)
from agent_bridge.workspace_policy import WorkspacePolicy, WorkspacePolicyError


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


def test_workspace_policy_rejects_missing_relative_and_file(tmp_path: Path):
    policy = WorkspacePolicy()
    with pytest.raises(WorkspacePolicyError, match="absolute"):
        policy.validate_cwd("relative")
    with pytest.raises(WorkspacePolicyError, match="does not exist"):
        policy.validate_cwd(tmp_path / "missing")
    file = tmp_path / "file.txt"
    file.write_text("x", encoding="utf-8")
    with pytest.raises(WorkspacePolicyError, match="not a directory"):
        policy.validate_cwd(file)


def test_workspace_policy_rejects_cwd_outside_allowed_root(tmp_path: Path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    policy = WorkspacePolicy(allowed_roots=[str(allowed)])
    assert policy.validate_cwd(allowed) == allowed.resolve()
    with pytest.raises(WorkspacePolicyError, match="outside configured allowed_roots"):
        policy.validate_cwd(outside)


def test_workspace_policy_rejects_symlink_component(tmp_path: Path):
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable: {exc}")
    with pytest.raises(WorkspacePolicyError, match="symlink or reparse"):
        WorkspacePolicy().validate_cwd(link)


def test_workspace_policy_rejects_windows_reparse_attribute(tmp_path: Path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    original_lstat = Path.lstat

    class ReparseStat:
        st_file_attributes = 0x0400
        st_mode = 0

    def reparse_lstat(path: Path):
        if path == workspace:
            return ReparseStat()
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", reparse_lstat)
    with pytest.raises(WorkspacePolicyError, match="symlink or reparse"):
        WorkspacePolicy().validate_cwd(workspace)


def test_workspace_policy_requires_configured_cwd_to_match_session(tmp_path: Path):
    first = tmp_path / "first"
    first.mkdir()
    second = tmp_path / "second"
    second.mkdir()
    with pytest.raises(WorkspacePolicyError, match="must equal session cwd"):
        WorkspacePolicy().validate_configured_cwd(str(first), second)
