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

from pathlib import Path
from typing import Any, Mapping

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
    """
    out = dict(env)
    token = (out.get("ANTHROPIC_AUTH_TOKEN") or "").strip()
    openrouter = (out.get("OPENROUTER_API_KEY") or "").strip()
    if not token and openrouter:
        out["ANTHROPIC_AUTH_TOKEN"] = openrouter
        token = openrouter
        if not (out.get("ANTHROPIC_BASE_URL") or "").strip():
            out["ANTHROPIC_BASE_URL"] = OPENROUTER_ANTHROPIC_BASE
    if token and (out.get("ANTHROPIC_BASE_URL") or "").strip():
        out["ANTHROPIC_API_KEY"] = ""
    return out


def describe_claude_auth(env: Mapping[str, str] | None = None) -> str:
    """Describe auth without deciding availability.

    A missing login only fails on the first prompt. The probe answers "is
    the command present", not "will a turn succeed".
    """
    resolved = apply_claude_gateway_env(env or {})
    if (resolved.get("ANTHROPIC_AUTH_TOKEN") or "").strip() and (
        resolved.get("ANTHROPIC_BASE_URL") or ""
    ).strip():
        return "gateway"
    if (resolved.get("ANTHROPIC_API_KEY") or "").strip():
        return "api-key"
    if (resolved.get("ANTHROPIC_AUTH_TOKEN") or "").strip():
        return "auth-token"
    home = claude_config_home(resolved)
    candidates = (
        home / ".credentials.json",
        home / "credentials.json",
        home / ".claude.json",
        Path.home() / ".claude.json",
    )
    for path in candidates:
        try:
            if path.is_file():
                return "oauth"
        except OSError:
            continue
    return "missing (run `claude auth login`, or set ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN)"
