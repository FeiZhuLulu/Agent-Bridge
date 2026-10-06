"""Cursor-shaped ACP wrapper used by adapter integration tests."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Any

from acp import run_agent
from acp.schema import NewSessionResponse, SetSessionConfigOptionResponse

from echo_agent import EchoAgent

MODELS = {
    "cursor-model-a": ("model-a", "high", "false"),
    "cursor-model-a-medium": ("model-a", "medium", "false"),
    "cursor-model-a-high-fast": ("model-a", "high", "true"),
    "cursor-model-b": ("model-b", "low", "false"),
}
LABELS = {
    "cursor-model-a": "Cursor Model A",
    "cursor-model-a-medium": "Cursor Model A Medium",
    "cursor-model-a-high-fast": "Cursor Model A High Fast",
    "cursor-model-b": "Cursor Model B",
}


def _record_model_list() -> None:
    marker = os.environ.get("CURSOR_AGENT_LIST_MARKER")
    if marker:
        with Path(marker).open("a", encoding="utf-8") as handle:
            handle.write("listed\n")


class CursorAgent(EchoAgent):
    def __init__(self, selected: str | None) -> None:
        super().__init__()
        self.model, self.effort, self.fast = MODELS.get(
            selected or "cursor-model-a",
            ("model-a", "high", "false"),
        )

    def _options(self) -> list[dict[str, Any]]:
        return [
            {
                "id": "model",
                "name": "Model",
                "type": "select",
                "category": "model",
                "currentValue": self.model,
                "options": [
                    {"value": "model-a", "name": "Cursor Model A"},
                    {"value": "model-b", "name": "Cursor Model B"},
                ],
            },
            {
                "id": "effort",
                "name": "Effort",
                "type": "select",
                "category": "thought_level",
                "currentValue": self.effort,
                "options": [
                    {"value": "low", "name": "Low"},
                    {"value": "medium", "name": "Medium"},
                    {"value": "high", "name": "High"},
                ],
            },
            {
                "id": "fast",
                "name": "Fast",
                "type": "select",
                "category": "model_config",
                "currentValue": self.fast,
                "options": [
                    {"value": "false", "name": "Off"},
                    {"value": "true", "name": "Fast"},
                ],
            },
        ]

    async def initialize(self, protocol_version: int, client_capabilities=None, **kwargs: Any):
        marker = os.environ.get("CURSOR_AGENT_CAPABILITIES_MARKER")
        if marker:
            dumped = client_capabilities.model_dump(mode="json", by_alias=True, exclude_none=True)
            Path(marker).write_text(str(dumped.get("_meta", {})), encoding="utf-8")
        return await super().initialize(protocol_version, **kwargs)

    async def new_session(self, cwd: str, mcp_servers=None, **kwargs: Any) -> NewSessionResponse:
        await super().new_session(cwd, mcp_servers, **kwargs)
        return NewSessionResponse.model_validate(
            {"sessionId": self._session_id, "configOptions": self._options()}
        )

    async def set_config_option(
        self,
        session_id: str,
        config_id: str,
        value: str | bool,
        **kwargs: Any,
    ) -> SetSessionConfigOptionResponse:
        offered = {
            "model": {"model-a", "model-b"},
            "effort": {"low", "medium", "high"},
            "fast": {"false", "true"},
        }
        value = str(value).lower() if isinstance(value, bool) else value
        if config_id not in offered or value not in offered[config_id]:
            raise ValueError(f"invalid {config_id}={value}")
        setattr(self, config_id, value)
        return SetSessionConfigOptionResponse.model_validate(
            {"configOptions": self._options()}
        )


if "--list-models" in sys.argv:
    _record_model_list()
    print("Available models")
    print()
    for model, label in LABELS.items():
        print(f"{model} - {label}")
elif "acp" in sys.argv:
    selected = sys.argv[sys.argv.index("--model") + 1] if "--model" in sys.argv else None
    if selected is not None and selected not in MODELS:
        print(f"unknown model: {selected}", file=sys.stderr)
        raise SystemExit(2)
    asyncio.run(run_agent(CursorAgent(selected)))
else:
    raise SystemExit("expected --list-models or acp")
