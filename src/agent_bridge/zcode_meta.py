"""ZCode selection helpers.

Product ``zcode`` speaks a private app-server protocol, not ACP. Bridge
drives the published adapter ``zcode-acp-server`` (npm ``zcode-acp-server``;
``zcode-acp server`` is the same binary). A fresh session starts in
``yolo`` unless ``ZCODE_ACP_MODE`` or the user's session mode says
otherwise, so Bridge re-asserts ``yolo`` through ``session/set_config_option``.

Model values are ``providerId\\modelId``. Thought level (config id
``thought``) is per model — GLM coding-plan models commonly advertise
``low|high|max``. ``session/load`` replays history; ``session/resume`` does
not for a non-TUI client, so Bridge revives with resume.

Auth is the ZCode desktop login on disk (``~/.zcode/v2``), overridable with
``ZCODE_HOME``. ``ZCODE_BIN`` points the adapter at a CLI that is not on
PATH.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

ZCODE_MODE_YOLO = "yolo"

# Live GLM coding-plan models advertise low/high/max. ``off`` lands on the
# lowest real level the session lists; ``medium`` climbs to ``high`` when
# that is the nearest neighbour.
ZCODE_EFFORT_PREFERENCE: dict[str, tuple[str, ...]] = {
    "off": ("off", "nothink", "none", "disabled", "minimal", "low"),
    "low": ("low", "minimal", "medium"),
    "medium": ("medium", "high", "low"),
    "high": ("high", "max", "medium"),
    "max": ("max", "xhigh", "high"),
}


def resolve_zcode_effort(effort: str | None, offered: Any) -> str | None:
    """Map a Bridge effort onto a thought level this ZCode model advertises.

    ``None`` means the session has nothing comparable. The caller warns
    rather than failing the turn.
    """
    if effort is None:
        return None
    if not isinstance(offered, (list, tuple)):
        return None
    available = [value for value in offered if isinstance(value, str)]
    for candidate in ZCODE_EFFORT_PREFERENCE.get(effort, (effort,)):
        if candidate in available:
            return candidate
    return None


def zcode_model_candidates(requested: str, offered: list[str]) -> list[str]:
    """Advertised ``provider\\model`` values that ``requested`` could mean.

    Exact ACP values, a unique bare model id, and ``provider/model`` (model
    ids may themselves contain ``/``) all count. An exact advertised value
    is returned alone even when a bare id would also match something else.
    """
    wanted = requested.strip()
    if not wanted:
        return []
    if wanted in offered:
        return [wanted]
    matches: list[str] = []
    for option in offered:
        if "\\" not in option:
            continue
        provider, model = option.split("\\", 1)
        if model == wanted or f"{provider}/{model}" == wanted:
            matches.append(option)
    return matches


def resolve_zcode_model(requested: str, offered: list[str]) -> str | None:
    """Return one advertised model value, or ``None`` if missing or ambiguous."""
    matches = zcode_model_candidates(requested, offered)
    if len(matches) == 1:
        return matches[0]
    return None


def zcode_home(env: Mapping[str, str] | None = None) -> Path:
    raw = (env or {}).get("ZCODE_HOME", "").strip()
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".zcode"


def describe_zcode_auth(env: Mapping[str, str] | None = None) -> str:
    """Describe login state without deciding availability.

    A missing desktop login only fails on the first prompt. The probe
    answers "is the command present".
    """
    home = zcode_home(env)
    for path in (home / "v2" / "credentials.json", home / "v2" / "config.json"):
        if _is_file(path):
            return "login"
    return "missing (sign in with the ZCode app; expects ~/.zcode/v2/config.json)"


def _is_file(path: Path) -> bool:
    try:
        return path.is_file()
    except OSError:
        return False
