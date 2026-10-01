"""MiniMax Code selection helpers.

``mcode acp`` (npm ``@minimax-ai/code``) is the CLI's own ACP server.
Permission and model are separate config options: ``permissionMode`` is
process-scoped (``default`` / ``auto`` / ``bypassPermissions``) and a fresh
process starts on ``default`` (ask). Session mode (``session/set_mode``) is
only ``default`` / ``plan`` and is left alone.

The ``model`` option value is not the slug users type. The CLI encodes
``provider/model`` as ``m:<provider>:<model>:u`` and a variant as
``m:<provider>:<model>:v:<variant>`` (``encodeURIComponent`` on each part).
Bridge accepts the human slug (``provider/model`` or
``provider/model#variant``, matching ``mcode exec --model`` / ``--effort``)
or that encoded value, and sends the encoded form.

``thinkingEffort`` is per model. MiniMax-hosted models commonly advertise
``default|low|medium|high|xhigh|max``. ``session/load`` replays history;
``session/resume`` does not.

Auth is ``mcode login`` under ``MINIMAX_DATA_DIR`` or ``~/.minimax``
(``auth/<build>/<region>/auth.json``).
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote

MINIMAX_PERMISSION_BYPASS = "bypassPermissions"

# Catalog thinking.effortOptions for MiniMax-hosted models. ``off`` leaves
# the model's own ``default``; ``max`` stays ``max`` when the session lists it.
MINIMAX_EFFORT_PREFERENCE: dict[str, tuple[str, ...]] = {
    "off": ("default", "off", "minimal", "low"),
    "low": ("low", "minimal", "default"),
    "medium": ("medium", "high", "low", "default"),
    "high": ("high", "xhigh", "max", "medium"),
    "max": ("max", "xhigh", "high"),
}


def resolve_minimax_effort(effort: str | None, offered: Any) -> str | None:
    """Map a Bridge effort onto a thinkingEffort this model advertises.

    ``None`` means the session has nothing comparable. The caller warns
    rather than failing the turn.
    """
    if effort is None:
        return None
    if not isinstance(offered, (list, tuple)):
        return None
    available = [value for value in offered if isinstance(value, str)]
    for candidate in MINIMAX_EFFORT_PREFERENCE.get(effort, (effort,)):
        if candidate in available:
            return candidate
    return None


def encode_minimax_model(provider_id: str, model_id: str, variant: str | None = None) -> str:
    """ACP ``model`` value for one provider/model selection."""
    provider = _encode_uri_component(provider_id)
    model = _encode_uri_component(model_id)
    if variant is None:
        return f"m:{provider}:{model}:u"
    return f"m:{provider}:{model}:v:{_encode_uri_component(variant)}"


def parse_minimax_model_value(value: str) -> tuple[str, str, str | None] | None:
    """Decode an ACP model value into ``(provider, model, variant)``.

    ``None`` when ``value`` is not the ``m:`` encoding ``mcode acp`` emits.
    """
    parts = value.split(":")
    if len(parts) == 4 and parts[0] == "m" and parts[3] == "u":
        provider, model = unquote(parts[1]), unquote(parts[2])
        if provider and model:
            return provider, model, None
        return None
    if len(parts) == 5 and parts[0] == "m" and parts[3] == "v" and parts[4]:
        provider, model, variant = unquote(parts[1]), unquote(parts[2]), unquote(parts[4])
        if provider and model:
            return provider, model, variant
    return None


def minimax_model_label(provider_id: str, model_id: str, variant: str | None = None) -> str:
    """Human slug: ``provider/model`` or ``provider/model#variant``."""
    label = f"{provider_id}/{model_id}"
    if variant:
        return f"{label}#{variant}"
    return label


def parse_minimax_model_slug(value: str) -> tuple[str, str, str | None] | None:
    """Parse a coordinator slug or an already-encoded ACP value."""
    encoded = parse_minimax_model_value(value)
    if encoded is not None:
        return encoded
    text = value.strip()
    if "/" not in text:
        return None
    variant: str | None = None
    if "#" in text:
        text, variant = text.rsplit("#", 1)
        if not text or not variant:
            return None
    provider, model = text.split("/", 1)
    if not provider or not model:
        return None
    return provider, model, variant


def minimax_model_labels(offered: list[str]) -> list[str]:
    labels: list[str] = []
    for option in offered:
        parsed = parse_minimax_model_value(option)
        if parsed is None:
            labels.append(option)
        else:
            labels.append(minimax_model_label(*parsed))
    return labels


def resolve_minimax_model(requested: str, offered: list[str]) -> str | None:
    """Return the advertised ACP model value, or ``None`` if it is not offered."""
    wanted = requested.strip()
    if not wanted:
        return None
    if wanted in offered:
        return wanted
    parsed = parse_minimax_model_slug(wanted)
    if parsed is None:
        return None
    encoded = encode_minimax_model(*parsed)
    if encoded in offered:
        return encoded
    for option in offered:
        if parse_minimax_model_value(option) == parsed:
            return option
    return None


def minimax_data_dir(env: Mapping[str, str] | None = None) -> Path:
    raw_env = env or {}
    for key in ("MINIMAX_DATA_DIR", "MAVIS_DATA_DIR"):
        raw = raw_env.get(key, "").strip()
        if raw:
            return Path(raw).expanduser()
    profile = raw_env.get("MAVIS_PROFILE", "").strip()
    name = f".minimax-{profile}" if profile else ".minimax"
    return Path.home() / name


def describe_minimax_auth(env: Mapping[str, str] | None = None) -> str:
    """Describe login state without deciding availability."""
    raw = env or {}
    root = minimax_data_dir(raw) / "auth"
    region = raw.get("MAVIS_REGION", "").strip() or "cn"
    build = raw.get("MAVIS_BUILD_ENV", "").strip() or "prod"
    candidates = [
        root / build / region / "auth.json",
        root / "prod" / "cn" / "auth.json",
        root / "prod" / "en" / "auth.json",
        root / "credentials.enc",
    ]
    if any(_is_file(path) for path in candidates):
        return "oauth"
    return "missing (run `mcode login`)"


def _encode_uri_component(value: str) -> str:
    # Matches JS encodeURIComponent: unreserved plus ! ~ * ' ( )
    return quote(value, safe="-_.!~*'()")


def _is_file(path: Path) -> bool:
    try:
        return path.is_file()
    except OSError:
        return False
