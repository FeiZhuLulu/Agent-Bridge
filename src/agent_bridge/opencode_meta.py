"""OpenCode selection helpers.

OpenCode ACP advertises ``model`` as ``provider/model`` and ``effort`` as the
current model's variants — not a product-wide scale. Official providers
include OpenCode Zen and OpenCode Go; other providers are API-key connections
via ``opencode auth``. A model with no variants omits the ``effort`` option
entirely (see OpenCode ``buildEffortSelectOption``).

The preference table is taken from OpenCode 1.18 ACP tests
(``packages/opencode/test/acp/service-session.test.ts``): live models commonly
offer ``default|low|medium|high``. ``max`` is often rejected as
``InvalidEffortError``, so Bridge ``max`` degrades to ``high`` unless the
session actually lists ``max``.
"""

from __future__ import annotations

from typing import Any

OPENCODE_EFFORT_PREFERENCE: dict[str, tuple[str, ...]] = {
    "off": ("default", "off", "minimal", "low"),
    "low": ("low", "default", "minimal"),
    "medium": ("medium", "high", "low", "default"),
    "high": ("high", "medium", "max", "default"),
    "max": ("max", "xhigh", "high", "medium"),
}


def resolve_opencode_effort(effort: str | None, offered: Any) -> str | None:
    """Map a Bridge effort onto a variant this OpenCode model advertises.

    ``None`` means the session has nothing comparable (no ``effort`` option,
    or a vocabulary with no neighbour). The caller warns rather than failing
    the turn: the mismatch is Bridge's mapping problem.
    """
    if effort is None:
        return None
    if not isinstance(offered, (list, tuple)):
        return None
    available = [value for value in offered if isinstance(value, str)]
    for candidate in OPENCODE_EFFORT_PREFERENCE.get(effort, (effort,)):
        if candidate in available:
            return candidate
    return None
