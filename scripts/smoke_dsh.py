"""Manual DeepSeek Harness ACP smoke.

    uv run python scripts/smoke_dsh.py
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
        dsh = next((row for row in agents if row["agent"] == "dsh"), None)
        if dsh is None or not dsh["available"]:
            print("dsh unavailable:", dsh)
            return 2
        first = await registry.dispatch_task(
            "dsh",
            "Create smoke-dsh.txt containing hello-dsh. Do not do anything else.",
            cwd=str(cwd),
            title="smoke-dsh",
        )
        waited = await registry.wait_task(first["task_id"], timeout_sec=240)
        print("turn1", waited["status"], waited.get("error"))
        second = await registry.dispatch_task(
            "dsh",
            "Append round-two to smoke-dsh.txt. You already created this file in this conversation.",
            cwd=str(cwd),
            session_id=first["session_id"],
        )
        waited2 = await registry.wait_task(second["task_id"], timeout_sec=240)
        print("turn2", waited2["status"])
        return 0 if waited["status"] == "completed" and waited2["status"] == "completed" else 1
    finally:
        await registry.stop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--cwd", type=Path, default=None)
    args = parser.parse_args()
    work = args.cwd or Path(tempfile.mkdtemp(prefix="agent-bridge-dsh-"))
    work.mkdir(parents=True, exist_ok=True)
    print("work dir", work)
    raise SystemExit(asyncio.run(main(work.resolve())))
