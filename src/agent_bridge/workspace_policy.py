"""Validation for a worker workspace before it reaches a process spawn.

This is an input policy, not a filesystem sandbox.  It makes the workspace
identity explicit and rejects paths whose component traversal is ambiguous.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

from pydantic import BaseModel, Field


class WorkspacePolicyError(ValueError):
    """Raised when a requested worker workspace is unsafe or out of policy."""


class WorkspacePolicy(BaseModel):
    """Validate dispatch workspaces against optional local configuration."""

    allowed_roots: list[str] = Field(default_factory=list)
    reject_reparse: bool = True

    def validate_cwd(self, cwd: str | Path) -> Path:
        """Return a normalized existing directory or fail closed."""
        raw = str(cwd)
        path = Path(raw)
        if not path.is_absolute():
            raise WorkspacePolicyError("cwd must be an absolute path")
        if not path.exists():
            raise WorkspacePolicyError(f"cwd does not exist: {path}")
        if not path.is_dir():
            raise WorkspacePolicyError(f"cwd is not a directory: {path}")
        if self.reject_reparse:
            _reject_reparse_components(path, label="cwd")
        normalized = path.resolve(strict=True)
        for root in self.allowed_roots:
            allowed = self._validate_root(root)
            try:
                normalized.relative_to(allowed)
            except ValueError:
                continue
            break
        else:
            if self.allowed_roots:
                roots = ", ".join(self.allowed_roots)
                raise WorkspacePolicyError(f"cwd is outside configured allowed_roots: {roots}")
        return normalized

    def validate_configured_cwd(self, configured_cwd: str | None, session_cwd: Path) -> None:
        """Ensure an optional per-agent cwd cannot override the session cwd."""
        if not configured_cwd:
            return
        configured = self.validate_cwd(configured_cwd)
        if _normalized_compare(configured) != _normalized_compare(session_cwd):
            raise WorkspacePolicyError(
                f"configured agent cwd {configured} must equal session cwd {session_cwd}"
            )

    def _validate_root(self, raw: str) -> Path:
        root = Path(raw)
        if not root.is_absolute():
            raise WorkspacePolicyError(f"allowed root must be an absolute path: {root}")
        if not root.exists() or not root.is_dir():
            raise WorkspacePolicyError(f"allowed root is not an existing directory: {root}")
        if self.reject_reparse:
            _reject_reparse_components(root, label="allowed root")
        return root.resolve(strict=True)


def _normalized_compare(path: Path) -> str:
    return os.path.normcase(os.path.normpath(str(path.resolve(strict=True))))


def _reject_reparse_components(path: Path, *, label: str) -> None:
    """Reject every symlink or Windows reparse point in a lexical path walk."""
    current = Path(path.anchor)
    for part in path.parts[1:]:
        if part in ("", "."):
            continue
        if part == "..":
            current = current.parent
            continue
        current /= part
        try:
            info = current.lstat()
        except OSError as exc:
            raise WorkspacePolicyError(f"{label} component is not accessible: {current}") from exc
        attrs = getattr(info, "st_file_attributes", 0)
        reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x0400)
        if current.is_symlink() or attrs & reparse_flag:
            raise WorkspacePolicyError(f"{label} contains a symlink or reparse point: {current}")
