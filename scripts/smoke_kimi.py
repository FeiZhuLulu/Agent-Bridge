"""Manual Kimi Code smoke: write a file, resume the same session, check model/effort and the silent-failure guard.

Run from repo root after `uv sync`:

    uv run python scripts/smoke_kimi.py

Kimi reports a failed turn as `end_turn` with empty text, so this script
reports `warnings` on every turn — an empty result with no warning is the only
clean no-op.
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
        kimi = next((row for row in agents if row["agent"] == "kimi"), None)
        if kimi is None or not kimi["available"]:
            print("kimi unavailable:", kimi)
            return 2
        print("probe:", kimi["version"], "|", kimi["detail"])

        first = await registry.dispatch_task(
            "kimi",
            "Create a file named smoke.txt in the working directory containing exactly the text hello-bridge. Do not do anything else.",
            cwd=str(cwd),
            title="smoke-kimi",
            effort="max",
        )
        waited = await registry.wait_task(first["task_id"], timeout_sec=240)
        report("turn1", waited)
        if not (cwd / "smoke.txt").is_file():
            print("missing smoke.txt")
            return 1

        # Same session id: exercises the session/resume revive path, which must
        # not replay the whole history back at us.
        second = await registry.dispatch_task(
            "kimi",
            "Append a second line `round-two` to smoke.txt. Keep the first line unchanged.",
            cwd=str(cwd),
            session_id=first["session_id"],
            model="kimi-code/k3-256k",
            effort="low",
        )
        waited2 = await registry.wait_task(second["task_id"], timeout_sec=240)
        report("turn2", waited2)
        print("smoke.txt:", (cwd / "smoke.txt").read_text(encoding="utf-8", errors="replace"))

        # A slug the session does not advertise must fail loudly instead of
        # quietly running on the default model.
        try:
            bogus = await registry.dispatch_task(
                "kimi",
                "Say ok.",
                cwd=str(cwd),
                session_id=first["session_id"],
                model="kimi-code/does-not-exist",
            )
            waited3 = await registry.wait_task(bogus["task_id"], timeout_sec=120)
            report("turn3 (bogus model)", waited3)
            if waited3["status"] != "failed":
                print("expected the bogus model to fail the turn")
                return 1
        except ValueError as exc:
            print("turn3 rejected at dispatch:", exc)

        return 0 if waited["status"] == "completed" and waited2["status"] == "completed" else 1
    finally:
        await registry.stop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--cwd", type=Path, default=None)
    args = parser.parse_args()
    work = args.cwd or Path(tempfile.mkdtemp(prefix="agent-bridge-kimi-"))
    work.mkdir(parents=True, exist_ok=True)
    print("work dir", work)
    raise SystemExit(asyncio.run(main(work.resolve())))
