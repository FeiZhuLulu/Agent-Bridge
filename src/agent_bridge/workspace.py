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
    "dist",
    "build",
    "target",
    ".next",
    ".nuxt",
    ".turbo",
    ".cache",
    ".ruff_cache",
    ".gradle",
    ".idea",
    "coverage",
    ".parcel-cache",
    ".svelte-kit",
    ".terraform",
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
    """Map posix-relative paths to (mtime_ns, size). Skip SKIP_DIR_NAMES at
    any depth. Symlink files are not followed and are omitted.
    """
    root = Path(cwd)
    if not root.is_dir():
        return {}
    found: dict[str, tuple[int, int]] = {}
    stack = [str(root)]
    prefix_len = _root_prefix_len(str(root))
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as it:
                for entry in it:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            if entry.name not in SKIP_DIR_NAMES:
                                stack.append(entry.path)
                        elif entry.is_file(follow_symlinks=False):
                            st = entry.stat(follow_symlinks=False)
                            rel = entry.path[prefix_len:].replace(os.sep, "/")
                            found[rel] = (st.st_mtime_ns, st.st_size)
                    except OSError:
                        continue
        except OSError:
            continue
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
    rel: str | None
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


def _root_prefix_len(root: str) -> int:
    return len(root.rstrip(os.sep)) + 1


def _ignored_rel(rel: str) -> bool:
    return any(part in SKIP_DIR_NAMES for part in rel.split("/")[:-1])


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
