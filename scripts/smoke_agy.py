"""Manual Antigravity smoke: two-turn --conversation resume.

    uv run python scripts/smoke_agy.py
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
        agy = next((row for row in agents if row["agent"] == "antigravity"), None)
        if agy is None or not agy["available"]:
            print("antigravity unavailable:", agy)
            return 2
        first = await registry.dispatch_task(
            "antigravity",
            "Create smoke-agy.txt containing hello-agy. Do not do anything else.",
            cwd=str(cwd),
            title="smoke-agy",
        )
        waited = await registry.wait_task(first["task_id"], timeout_sec=240)
        print("turn1", waited["status"], waited.get("error"))
        second = await registry.dispatch_task(
            "antigravity",
            "Append round-two to smoke-agy.txt.",
            cwd=str(cwd),
            session_id=first["session_id"],
        )
        waited2 = await registry.wait_task(second["task_id"], timeout_sec=240)
        print("turn2", waited2["status"], registry.sessions[first["session_id"]].native_session_id)
        return 0 if waited["status"] == "completed" and waited2["status"] == "completed" else 1
    finally:
        await registry.stop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--cwd", type=Path, default=None)
    args = parser.parse_args()
    work = args.cwd or Path(tempfile.mkdtemp(prefix="agent-bridge-agy-"))
    work.mkdir(parents=True, exist_ok=True)
    print("work dir", work)
    raise SystemExit(asyncio.run(main(work.resolve())))
