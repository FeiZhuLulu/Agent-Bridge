"""Manual Claude Code smoke: write a file, resume the same session, reject a bogus model.

Run from repo root after `uv sync`:

    uv run python scripts/smoke_claude.py

Product ``claude`` is not an ACP server. This script needs ``claude-agent-acp``
on PATH (``npm i -g @agentclientprotocol/claude-agent-acp``) plus auth:
``claude auth login``, ``ANTHROPIC_API_KEY``, or a gateway
(``ANTHROPIC_AUTH_TOKEN`` + ``ANTHROPIC_BASE_URL``, e.g. OpenRouter).
``list_agents`` cannot see a missing login; the first prompt will fail.
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
        "session=", waited.get("session_id"),
        "model=", waited.get("observed_model"),
        "effort=", waited.get("observed_effort"),
    )
    for warning in waited.get("warnings") or []:
        print("   warning:", warning)
    if waited.get("error"):
        print("   error:", waited["error"])


async def main(cwd: Path, model: str | None) -> int:
    setup_logging()
    registry = Registry.create()
    await registry.start()
    try:
        agents = await registry.list_agents()
        row = next((item for item in agents if item["agent"] == "claude"), None)
        if row is None or not row["available"]:
            print("claude unavailable:", row)
            return 2
        print("probe:", row["version"], "|", row["detail"])
        env = registry.env_status()
        print("env.proxy:", env.get("proxy"), env.get("proxy_source"))

        first = await registry.dispatch_task(
            "claude",
            "Create a file named smoke.txt in the working directory containing exactly the text hello-bridge. Do not do anything else.",
            cwd=str(cwd),
            title="smoke-claude",
            model=model,
        )
        waited = await registry.wait_task(first["task_id"], timeout_sec=240)
        report("turn1", waited)
        if not (cwd / "smoke.txt").is_file():
            print("missing smoke.txt")
            return 1
        native = next(
            (
                item["native_session_id"]
                for item in registry.list_sessions()
                if item["session_id"] == first["session_id"]
            ),
            None,
        )
        print("native_session_id", native)

        second = await registry.dispatch_task(
            "claude",
            "Append a second line `round-two` to smoke.txt. Keep the first line unchanged.",
            cwd=str(cwd),
            session_id=first["session_id"],
        )
        waited2 = await registry.wait_task(second["task_id"], timeout_sec=240)
        report("turn2", waited2)
        print("smoke.txt:", (cwd / "smoke.txt").read_text(encoding="utf-8", errors="replace"))
        native2 = next(
            (
                item["native_session_id"]
                for item in registry.list_sessions()
                if item["session_id"] == first["session_id"]
            ),
            None,
        )
        print("native_session_id after turn2", native2)
        if native and native2 not in {None, native}:
            print("native_session_id changed across turns:", native, "->", native2)
            return 1

        try:
            bogus = await registry.dispatch_task(
                "claude",
                "Say ok.",
                cwd=str(cwd),
                session_id=first["session_id"],
                model="claude-does-not-exist-xyz",
            )
            waited3 = await registry.wait_task(bogus["task_id"], timeout_sec=120)
            report("turn3 (bogus model)", waited3)
            if waited3["status"] != "failed":
                print("expected the bogus model to fail the turn")
                return 1
        except ValueError as exc:
            print("turn3 rejected at dispatch:", exc)

        await registry.end_session(first["session_id"])
        return 0 if waited["status"] == "completed" and waited2["status"] == "completed" else 1
    finally:
        await registry.stop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--cwd", type=Path, default=None)
    parser.add_argument(
        "--model",
        default=None,
        help="model slug the session advertises (needed on a gateway with no Anthropic default)",
    )
    args = parser.parse_args()
    work = args.cwd or Path(tempfile.mkdtemp(prefix="agent-bridge-claude-"))
    work.mkdir(parents=True, exist_ok=True)
    print("work dir", work)
    print("model", args.model or "(session default)")
    raise SystemExit(asyncio.run(main(work.resolve(), args.model)))
