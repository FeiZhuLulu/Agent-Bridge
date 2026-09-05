"""Claude Code selection helpers.

Product ``claude`` has no ACP profile. Bridge drives the published adapter
``claude-agent-acp`` (``@agentclientprotocol/claude-agent-acp``), which wraps
the Claude Agent SDK. A fresh session starts in ``default`` (manual) mode;
Bridge switches it to ``bypassPermissions``. Model and effort are typed ACP
config options — effort is per model and may be omitted entirely.

Gateway auth (OpenRouter and other Anthropic-compatible endpoints) uses
``ANTHROPIC_AUTH_TOKEN`` + ``ANTHROPIC_BASE_URL``. Claude Code treats a
non-empty ``ANTHROPIC_API_KEY`` as a direct-Anthropic credential, so Bridge
blanks it when a gateway token and base URL are both present.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

CLAUDE_MODE_BYPASS = "bypassPermissions"

# Live sessions commonly advertise ``default`` plus a subset of
# ``low|medium|high|xhigh|max`` (and ``ultracode`` on some models). Bridge
# ``off`` lands on ``default`` so the model's own baseline is left alone.
CLAUDE_EFFORT_PREFERENCE: dict[str, tuple[str, ...]] = {
    "off": ("default", "off", "minimal", "low"),
    "low": ("low", "default", "minimal"),
    "medium": ("medium", "high", "low", "default"),
    "high": ("high", "xhigh", "max", "medium"),
    "max": ("max", "ultracode", "xhigh", "high", "medium"),
}

OPENROUTER_ANTHROPIC_BASE = "https://openrouter.ai/api"


def resolve_claude_effort(effort: str | None, offered: Any) -> str | None:
    """Map a Bridge effort onto a variant this Claude model advertises.

    ``None`` means the session has nothing comparable. The caller warns
    rather than failing the turn: the mismatch is Bridge's mapping problem.
    """
    if effort is None:
        return None
    if not isinstance(offered, (list, tuple)):
        return None
    available = [value for value in offered if isinstance(value, str)]
    for candidate in CLAUDE_EFFORT_PREFERENCE.get(effort, (effort,)):
        if candidate in available:
            return candidate
    return None


def claude_config_home(env: Mapping[str, str] | None = None) -> Path:
    raw = (env or {}).get("CLAUDE_CONFIG_DIR", "").strip()
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".claude"


def apply_claude_gateway_env(env: Mapping[str, str]) -> dict[str, str]:
    """Fill OpenRouter-style gateway vars and blank a conflicting API key.

    Claude Code sends ``ANTHROPIC_API_KEY`` as ``x-api-key`` and treats it as
    a direct Anthropic login. An empty string is required so a gateway
    ``ANTHROPIC_AUTH_TOKEN`` is what actually authenticates.

    ``OPENROUTER_API_KEY`` is borrowed only when the user is not already on
    a direct Anthropic key, or they already set ``ANTHROPIC_BASE_URL``.
    A machine that uses OpenRouter for OpenCode and Anthropic for Claude
    must keep the Anthropic key.
    """
    out = dict(env)
    token = (out.get("ANTHROPIC_AUTH_TOKEN") or "").strip()
    api_key = (out.get("ANTHROPIC_API_KEY") or "").strip()
    base_url = (out.get("ANTHROPIC_BASE_URL") or "").strip()
    openrouter = (out.get("OPENROUTER_API_KEY") or "").strip()
    can_map_openrouter = bool(openrouter) and not token and (not api_key or bool(base_url))
    if can_map_openrouter:
        out["ANTHROPIC_AUTH_TOKEN"] = openrouter
        token = openrouter
        if not base_url:
            out["ANTHROPIC_BASE_URL"] = OPENROUTER_ANTHROPIC_BASE
            base_url = OPENROUTER_ANTHROPIC_BASE
    if token and base_url:
        out["ANTHROPIC_API_KEY"] = ""
    return out


def describe_claude_auth(env: Mapping[str, str] | None = None) -> str:
    """Describe auth without deciding availability.

    A missing login only fails on the first prompt. The probe answers "is
    the command present", not "will a turn succeed".
    """
    raw = env or {}
    resolved = apply_claude_gateway_env(raw)
    if (resolved.get("ANTHROPIC_AUTH_TOKEN") or "").strip() and (
        resolved.get("ANTHROPIC_BASE_URL") or ""
    ).strip():
        notes: list[str] = []
        if (raw.get("ANTHROPIC_API_KEY") or "").strip() and not (
            resolved.get("ANTHROPIC_API_KEY") or ""
        ).strip():
            notes.append("ANTHROPIC_API_KEY blanked for the worker")
        if not (raw.get("ANTHROPIC_AUTH_TOKEN") or "").strip() and (
            resolved.get("ANTHROPIC_AUTH_TOKEN") or ""
        ).strip():
            notes.append("token borrowed from OPENROUTER_API_KEY")
        return "gateway" if not notes else f"gateway ({'; '.join(notes)})"
    if (resolved.get("ANTHROPIC_API_KEY") or "").strip():
        return "api-key"
    if (resolved.get("ANTHROPIC_AUTH_TOKEN") or "").strip():
        return "auth-token"
    home = claude_config_home(resolved)
    for path in (home / ".credentials.json", home / "credentials.json"):
        if _is_file(path):
            return "oauth"
    # ~/.claude.json is the coordinator MCP/user file. Existence is not a
    # login. Isolated CLAUDE_CONFIG_DIR must not peek at the real home file.
    if _json_has_oauth(home / ".claude.json"):
        return "oauth"
    isolated = bool((env or {}).get("CLAUDE_CONFIG_DIR", "").strip())
    if not isolated and _json_has_oauth(Path.home() / ".claude.json"):
        return "oauth"
    return "missing (run `claude auth login`, or set ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN)"


def _is_file(path: Path) -> bool:
    try:
        return path.is_file()
    except OSError:
        return False


def _json_has_oauth(path: Path) -> bool:
    """True only when a JSON object actually carries an oauth payload."""
    if not _is_file(path):
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    if not isinstance(data, dict):
        return False
    for key in ("oauthAccount", "claudeAiOauth"):
        value = data.get(key)
        if isinstance(value, dict) and value:
            return True
    return False
