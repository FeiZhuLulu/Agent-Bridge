"""Turn-scoped workspace changes for get_result.

ACP workers such as DSH execute file tools themselves and often send no
tool_call updates. Protocol scraping alone then reports files_changed=[].
"""

from __future__ import annotations

import os
from pathlib import Path

SKIP_DIR_NAMES = {
    ".git",
    ".sessions",
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".tox",
}

_PATH_KEYS = (
    "path",
    "file",
    "filePath",
    "file_path",
    "targetFile",
    "TargetFile",
    "AbsolutePath",
    "absolutePath",
)


def snapshot_workspace(cwd: str | Path) -> dict[str, tuple[int, int]]:
    root = Path(cwd)
    if not root.is_dir():
        return {}
    found: dict[str, tuple[int, int]] = {}
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = [name for name in dirnames if name not in SKIP_DIR_NAMES]
        current = Path(dirpath)
        for name in filenames:
            path = current / name
            rel = path.relative_to(root).as_posix()
            if _ignored_rel(rel):
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            found[rel] = (stat.st_mtime_ns, stat.st_size)
    return found


def changed_since(cwd: str | Path, before: dict[str, tuple[int, int]]) -> list[str]:
    after = snapshot_workspace(cwd)
    changed = set()
    for rel, meta in after.items():
        if before.get(rel) != meta:
            changed.add(rel)
    for rel in before:
        if rel not in after:
            changed.add(rel)
    return sorted(changed)


def normalize_changed_paths(cwd: str | Path, paths: list[str]) -> list[str]:
    root = Path(cwd).resolve()
    seen: set[str] = set()
    ordered: list[str] = []
    for raw in paths:
        if not raw or not isinstance(raw, str):
            continue
        rel = _relative_to_cwd(raw, root)
        if not rel or _ignored_rel(rel):
            continue
        if rel not in seen:
            seen.add(rel)
            ordered.append(rel)
    return ordered


def merge_files_changed(
    cwd: str | Path,
    reported: list[str],
    before: dict[str, tuple[int, int]],
) -> list[str]:
    return normalize_changed_paths(cwd, [*reported, *changed_since(cwd, before)])


def _relative_to_cwd(raw: str, root: Path) -> str | None:
    text = raw.strip()
    if text.startswith("file://"):
        text = text[7:]
        if text.startswith("/") and len(text) >= 3 and text[2] == ":":
            text = text[1:]
    path = Path(text)
    try:
        resolved = path.resolve() if path.is_absolute() else (root / path).resolve()
        rel = resolved.relative_to(root).as_posix()
    except (OSError, ValueError):
        rel = path.as_posix().lstrip("./") if not path.is_absolute() else None
        if rel in {".", "..", ""}:
            return None
        return rel
    if rel in {".", "..", ""}:
        return None
    return rel


def _ignored_rel(rel: str) -> bool:
    first = rel.split("/", 1)[0]
    return first in SKIP_DIR_NAMES


def collect_update_paths(obj: object, into: set[str]) -> None:
    if obj is None:
        return
    if hasattr(obj, "model_dump"):
        obj = obj.model_dump(mode="json", by_alias=True, exclude_none=True)
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key in _PATH_KEYS and isinstance(value, str) and value.strip():
                into.add(value.strip())
            else:
                collect_update_paths(value, into)
    elif isinstance(obj, list):
        for item in obj:
            collect_update_paths(item, into)
