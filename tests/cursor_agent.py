"""Cursor-shaped ACP wrapper used by adapter integration tests."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from echo_agent import _main as echo_main


MODELS = ("cursor-model-a", "cursor-model-b")


def _record_model_list() -> None:
    marker = os.environ.get("CURSOR_AGENT_LIST_MARKER")
    if marker:
        with Path(marker).open("a", encoding="utf-8") as handle:
            handle.write("listed\n")


if "--list-models" in sys.argv:
    _record_model_list()
    print("Available models")
    print()
    print("cursor-model-a - Cursor Model A")
    print("cursor-model-b - Cursor Model B")
elif "acp" in sys.argv:
    if "--model" in sys.argv:
        selected = sys.argv[sys.argv.index("--model") + 1]
        if selected not in MODELS:
            print(f"unknown model: {selected}", file=sys.stderr)
            raise SystemExit(2)
    asyncio.run(echo_main())
else:
    raise SystemExit("expected --list-models or acp")
