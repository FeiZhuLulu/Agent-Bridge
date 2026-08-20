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


def _as_dict(obj: Any) -> dict[str, Any] | None:
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "model_dump"):
        dumped = obj.model_dump(mode="json", by_alias=True, exclude_none=True)
        return dumped if isinstance(dumped, dict) else None
    return None


def config_option_values(config_options: Any, option_id: str) -> tuple[str | None, list[str]]:
    """Return ``(current_value, offered_values)`` for one Kimi config option.

    Accepts either the SDK models carried by ``session/new`` /
    ``session/resume`` / ``session/set_config_option`` responses or plain
    dicts. An option id the session does not advertise yields ``(None, [])``.
    """
    if not isinstance(config_options, (list, tuple)):
        return None, []
    for option in config_options:
        dumped = _as_dict(option)
        if dumped is None or dumped.get("id") != option_id:
            continue
        current = dumped.get("currentValue", dumped.get("current_value"))
        return (current if isinstance(current, str) else None), _flatten(dumped.get("options"))
    return None, []


def _flatten(raw: Any) -> list[str]:
    """Collect option values, descending into groups.

    Kimi ships a flat ``[{value, name}]`` list today, but ACP types
    ``options`` as either that or a list of ``{group, name, options}``. A
    grouped payload would otherwise read as "this model offers nothing" and
    silently disable effort mapping.
    """
    if not isinstance(raw, (list, tuple)):
        return []
    values: list[str] = []
    for entry in raw:
        entry_dict = _as_dict(entry)
        if entry_dict is None:
            continue
        value = entry_dict.get("value")
        if isinstance(value, str):
            values.append(value)
        elif "options" in entry_dict:
            values.extend(_flatten(entry_dict.get("options")))
    return values


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
