"""Manual MiniMax Code smoke: write a file, resume the same session, reject a bogus model.

Run from repo root after `uv sync`, with `mcode` on PATH and `mcode login` done:

    uv run python scripts/smoke_minimax.py
"""

from __future__ import annotations

import argparse
import asyncio
import tempfile
from pathlib import Path

from agent_bridge.logging_setup import setup_logging
from agent_bridge.registry import Registry


def report(label: str, waited: dict) -> None:
    print(
        label,
        waited["status"],
        waited.get("stop_reason"),
        "model=", waited.get("observed_model"),
        "effort=", waited.get("observed_effort"),
    )
    for warning in waited.get("warnings") or []:
        print("   warning:", warning)
    if waited.get("error"):
        print("   error:", waited["error"])


async def main(cwd: Path) -> int:
    setup_logging()
    registry = Registry.create()
    await registry.start()
    try:
        agents = await registry.list_agents()
        row = next((item for item in agents if item["agent"] == "minimax"), None)
        if row is None or not row["available"]:
            print("minimax unavailable:", row)
            return 2
        print("probe:", row["version"], "|", row["detail"])

        first = await registry.dispatch_task(
            "minimax",
            "Create a file named smoke.txt in the working directory containing exactly the text hello-bridge. Do not do anything else.",
            cwd=str(cwd),
            title="smoke-minimax",
            effort="high",
        )
        waited = await registry.wait_task(first["task_id"], timeout_sec=240)
        report("turn1", waited)
        if not (cwd / "smoke.txt").is_file():
            print("missing smoke.txt")
            return 1

        second = await registry.dispatch_task(
            "minimax",
            "Append a second line `round-two` to smoke.txt. Keep the first line unchanged.",
            cwd=str(cwd),
            session_id=first["session_id"],
            effort="low",
        )
        waited2 = await registry.wait_task(second["task_id"], timeout_sec=240)
        report("turn2", waited2)
        if first["session_id"] != second["session_id"]:
            print("turn2 opened a new session")
            return 1
        print("smoke.txt:", (cwd / "smoke.txt").read_text(encoding="utf-8", errors="replace"))

        bogus = await registry.dispatch_task(
            "minimax",
            "Say ok.",
            cwd=str(cwd),
            session_id=first["session_id"],
            model="not-a-provider/not-a-model",
        )
        waited3 = await registry.wait_task(bogus["task_id"], timeout_sec=120)
        report("turn3 (bogus model)", waited3)
        if waited3["status"] != "failed":
            print("expected the bogus model to fail the turn")
            return 1
        return 0 if waited["status"] == "completed" and waited2["status"] == "completed" else 1
    finally:
        await registry.stop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--cwd", type=Path, default=None)
    args = parser.parse_args()
    work = args.cwd or Path(tempfile.mkdtemp(prefix="agent-bridge-minimax-"))
    work.mkdir(parents=True, exist_ok=True)
    print("work dir", work)
    raise SystemExit(asyncio.run(main(work.resolve())))
