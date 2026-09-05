"""Devin CLI selection helpers.

``devin acp`` is the CLI's own ACP server (the same binary Devin Desktop,
Zed and JetBrains launch). A fresh session starts in ``accept-edits`` —
``DEVIN_PERMISSION_MODE`` is parsed but not applied to ACP sessions — so
Bridge switches it to ``bypass`` through ``session/set_mode``. Model is the
typed ``model`` config option; the id already carries the level
(``swe-1-7-medium``, ``claude-opus-5-high``), there is no separate effort.
Only ``session/load`` exists (it replays history); ``session/resume`` is
not a method.

Auth is ``devin auth login`` on disk or ``WINDSURF_API_KEY`` — but only
while ``ACP_BACKEND`` is absent. Devin Desktop stamps that variable on its
children; the CLI then trusts host-supplied credentials alone and refuses
``session/new``. Bridge is not that host, so it drops the mark.
"""

from __future__ import annotations

from collections.abc import Mapping

DEVIN_MODE_BYPASS = "bypass"
DEVIN_HOST_MARK = "ACP_BACKEND"


def apply_devin_env(env: Mapping[str, str]) -> dict[str, str]:
    out = dict(env)
    out.pop(DEVIN_HOST_MARK, None)
    return out
