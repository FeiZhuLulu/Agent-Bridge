"""Sanitization for worker-derived diagnostic text before it crosses into
trusted surfaces (state.json, transcripts, MCP responses, logs).

Worker output is untrusted: a worker can print anything, including secrets
it read from its environment. Anything persisted or returned to the
coordinator must pass through ``redact_diagnostic`` first. Only diagnostic
channels (error text, warnings, stderr tails) are sanitized — result bodies
and structured event payloads are the product's output and are left alone.
"""

from __future__ import annotations

import re

_REDACT_LIMIT = 4096


def redact_diagnostic(text: str) -> str:
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
    text = re.sub(r"(?im)^.*(?:authorization|(?:set-)?cookie)\s*[:=].*$", "[redacted]", text)
    text = re.sub(
        r"""(?i)(\b(?:[\w-]*(?:token|secret|password|api[_-]?key)|authorization|cookie)\b["']?\s*[:=]\s*)(?:"[^"]*"|'[^']*'|[^\s,;]+)""",
        r"\1[redacted]",
        text,
    )
    text = re.sub(r"(?i)\b(?:bearer|basic)\s+\S+", "[redacted]", text)
    text = re.sub(r"""(?i)\b(?:https?|wss?)://[^\s<>"']+""", "[redacted URL]", text)
    text = re.sub(r"\b(?:sk-[\w-]+|gh[pousr]_\w+|tp-[A-Za-z0-9_-]{16,})\b", "[redacted]", text)
    return text[:_REDACT_LIMIT]
