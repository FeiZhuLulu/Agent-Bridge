"""Kimi Code selection helpers.

Kimi declares its thinking vocabulary **per model**, not once for the product.
The ``thinking`` config option a live session advertises for
``kimi-code/k3-256k`` is ``low|high|max`` — no ``off``, no ``medium`` — and ACP
also allows a model to publish the option as a boolean. A static table like
``grok_effort`` therefore cannot be right for every model, so Bridge keeps an
ordered preference per Bridge effort and takes the first level the live session
actually offers. The degradation is in the same spirit as ``agy_effort``
mapping ``off`` to ``low`` because agy has no off switch.
"""

from __future__ import annotations

from typing import Any

from agent_bridge.acp_config import config_option_values

# Re-exported so existing imports and tests stay put.
__all__ = (
    "KIMI_MODE_YOLO",
    "KIMI_EFFORT_PREFERENCE",
    "config_option_values",
    "resolve_kimi_thinking",
)

# Kimi's ACP modes are default | plan | auto | yolo; a new session starts on
# `default`, which asks for approval per tool call. Bridge runs headless.
KIMI_MODE_YOLO = "yolo"

KIMI_EFFORT_PREFERENCE: dict[str, tuple[str, ...]] = {
    "off": ("off", "low", "on"),
    "low": ("low", "on", "medium", "high"),
    "medium": ("medium", "high", "on", "low"),
    "high": ("high", "on", "max", "medium", "low"),
    "max": ("max", "xhigh", "high", "on"),
}


def resolve_kimi_thinking(effort: str | None, offered: Any) -> str | None:
    """Map a Bridge effort onto a thinking level this model advertises.

    ``None`` means the model has nothing comparable to offer; the caller warns
    rather than failing the turn, because the vocabulary mismatch is Bridge's
    mapping problem, not something wrong with the task.
    """
    if effort is None:
        return None
    if not isinstance(offered, (list, tuple)):
        return None
    available = [value for value in offered if isinstance(value, str)]
    for candidate in KIMI_EFFORT_PREFERENCE.get(effort, (effort,)):
        if candidate in available:
            return candidate
    return None
