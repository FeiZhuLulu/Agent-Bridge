"""Manual Grok Build smoke: create a file, follow up in the same session, then cancel-safety check.

Run from repo root after `uv sync`:

    uv run python scripts/smoke_grok.py
"""

from __future__ import annotations

import argparse
import asyncio
import tempfile
from pathlib import Path

from agent_bridge.logging_setup import setup_logging
from agent_bridge.registry import Registry


async def main(cwd: Path) -> int:
    setup_logging()
    registry = Registry.create()
    await registry.start()
    try:
        agents = await registry.list_agents()
        grok = next((row for row in agents if row["agent"] == "grok"), None)
        if grok is None or not grok["available"]:
            print("grok unavailable:", grok)
            return 2
        first = await registry.dispatch_task(
            "grok",
            "Create a file named smoke.txt in the working directory containing exactly the text hello-bridge. Do not do anything else.",
            cwd=str(cwd),
            title="smoke-grok",
        )
        waited = await registry.wait_task(first["task_id"], timeout_sec=240)
        print("turn1", waited["status"], waited.get("stop_reason"), waited.get("error"))
        if not (cwd / "smoke.txt").is_file():
            print("missing smoke.txt")
            return 1
        second = await registry.dispatch_task(
            "grok",
            "Append a second line `round-two` to smoke.txt. Keep the first line unchanged.",
            cwd=str(cwd),
            session_id=first["session_id"],
        )
        waited2 = await registry.wait_task(second["task_id"], timeout_sec=240)
        print("turn2", waited2["status"], (cwd / "smoke.txt").read_text(encoding="utf-8", errors="replace"))
        return 0 if waited["status"] == "completed" and waited2["status"] == "completed" else 1
    finally:
        await registry.stop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--cwd", type=Path, default=None)
    args = parser.parse_args()
    work = args.cwd or Path(tempfile.mkdtemp(prefix="agent-bridge-grok-"))
    work.mkdir(parents=True, exist_ok=True)
    print("work dir", work)
    raise SystemExit(asyncio.run(main(work.resolve())))
