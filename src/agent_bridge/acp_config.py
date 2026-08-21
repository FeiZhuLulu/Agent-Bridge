"""Shared ACP ``configOptions`` readers.

Kimi and OpenCode both publish model/effort through the typed ACP config
surface. The snapshot is either SDK models or plain dicts; both must flatten
the same way or Bridge silently treats a live session as having no options.
"""

from __future__ import annotations

from typing import Any


def _as_dict(obj: Any) -> dict[str, Any] | None:
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "model_dump"):
        dumped = obj.model_dump(mode="json", by_alias=True, exclude_none=True)
        return dumped if isinstance(dumped, dict) else None
    return None


def config_option_values(config_options: Any, option_id: str) -> tuple[str | None, list[str]]:
    """Return ``(current_value, offered_values)`` for one ACP config option.

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

    ACP types ``options`` as either a flat ``[{value, name}]`` list or a list
    of ``{group, name, options}``. A grouped payload would otherwise read as
    "this model offers nothing" and silently disable effort mapping.
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
