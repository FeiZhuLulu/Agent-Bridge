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
    # Bridge/worker bookkeeping dirs. A worker CLI keeps its own state under
    # these and churns them every turn; counting that as project changes
    # misattributes Bridge-internal writes to the worker (H-06).
    ".agent-bridge",
    ".codex",
    ".claude",
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
                            # Windows junctions pass is_dir(follow_symlinks=False)
                            # but point outside cwd; traversing them attributes
                            # foreign files as workspace writes (E2).
                            is_junction = getattr(entry, "is_junction", lambda: False)()
                            if entry.name not in SKIP_DIR_NAMES and not is_junction:
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
    inside, _outside = classify_changed_paths(cwd, paths)
    return inside


def classify_changed_paths(
    cwd: str | Path, paths: list[str]
) -> tuple[list[str], list[str]]:
    """Split reported paths into (inside-cwd rel, outside-cwd abs).

    A path that resolves outside the workspace is reported as its resolved
    absolute form in the second list — never folded into an inside path the
    way ``../x`` used to collapse to ``x`` (E3).
    """
    root = Path(cwd).resolve()
    seen_in: set[str] = set()
    seen_out: set[str] = set()
    inside: list[str] = []
    outside: list[str] = []
    for raw in paths:
        if not raw or not isinstance(raw, str):
            continue
        rel, out = _classify_path(raw, root)
        if rel and not _ignored_rel(rel, reported=True) and rel not in seen_in:
            seen_in.add(rel)
            inside.append(rel)
        elif out and out not in seen_out:
            seen_out.add(out)
            outside.append(out)
    return inside, outside


def merge_files_changed(
    cwd: str | Path,
    reported: list[str],
    before: dict[str, tuple[int, int]],
) -> tuple[list[str], list[str]]:
    """(inside-cwd rel paths, outside-cwd abs paths)."""
    inside, outside = classify_changed_paths(cwd, reported)
    disk = changed_since(cwd, before)
    merged = inside + [rel for rel in disk if rel not in set(inside)]
    return merged, outside



def _classify_path(raw: str, root: Path) -> tuple[str | None, str | None]:
    """(inside rel | None, outside absolute | None). Both None = unusable."""
    text = raw.strip()
    if text.startswith("file://"):
        text = text[7:]
        if text.startswith("/") and len(text) >= 3 and text[2] == ":":
            text = text[1:]
    path = Path(text)
    try:
        resolved = path.resolve() if path.is_absolute() else (root / path).resolve()
    except (OSError, ValueError, RuntimeError):
        resolved = None
    if resolved is not None:
        try:
            rel = resolved.relative_to(root).as_posix()
        except ValueError:
            return None, resolved.as_posix()
        if rel in {".", "..", ""}:
            return None, None
        return rel, None
    # Unresolvable: keep prior lenient behavior for relative strings, but a
    # leading ../ can never be proven inside — report it as outside instead
    # of folding it into the workspace (E3). Check the traversal BEFORE
    # lstrip, which would otherwise eat the leading ../ as well.
    if not path.is_absolute():
        posix = path.as_posix()
        if posix.startswith("../") or posix == "..":
            return None, posix
        rel = posix.lstrip("./")
        if rel in {".", "..", ""}:
            return None, None
        return rel, None
    return None, path.as_posix()


def _root_prefix_len(root: str) -> int:
    return len(root.rstrip(os.sep)) + 1


# Reported paths skip less than the disk snapshot: .codex/.claude churn is
# filtered from the snapshot walker, but a path a worker *explicitly reports*
# there is an intentional project-config edit and must surface (review).
_REPORTED_SKIP = SKIP_DIR_NAMES - {".codex", ".claude"}


def _ignored_rel(rel: str, *, reported: bool = False) -> bool:
    names = _REPORTED_SKIP if reported else SKIP_DIR_NAMES
    return any(part in names for part in rel.split("/")[:-1])


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
