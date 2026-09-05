from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from mcp.server.mcpserver import Context, MCPServer
from mcp_types import ToolAnnotations

from agent_bridge.logging_setup import setup_logging
from agent_bridge.models import DEFAULT_WAIT_SEC
from agent_bridge.paths import ensure_home
from agent_bridge.registry import RESULT_PAGE_MAX_CHARS, Registry

READ_ONLY = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    open_world_hint=False,
    idempotent_hint=True,
)


@asynccontextmanager
async def lifespan(_server: MCPServer[Registry]) -> AsyncIterator[Registry]:
    home = ensure_home()
    setup_logging(home)
    registry = Registry.create(home)
    await registry.start()
    try:
        yield registry
    finally:
        await registry.stop()


# Injected into the coordinator's context at the MCP handshake — the one
# channel that needs no copied rules file and no skill install.
INSTRUCTIONS = (
    "Agent Bridge dispatches tasks to local worker CLIs (Grok, Kimi Code, "
    "Antigravity, DeepSeek Harness, OpenCode, Claude Code, Codex CLI, Devin CLI) and keeps their "
    "sessions resumable.\n"
    "Hard rules: workers are reached only through these tools — never drive the "
    "worker CLIs or GUIs directly. dispatch_task.cwd is this conversation's "
    "project folder (absolute), not the Agent Bridge install path. A wait_task "
    "timeout is not failure; call it again. Verify results with get_result plus "
    "your own git diff — do not trust a worker's self-report. An empty Kimi "
    "result with non-empty warnings is a failed turn, not a no-op.\n"
    "Call list_agents first. Read coordinator.mode, coordinator.instructions, "
    "coordinator.runtime_context, and coordinator.dispatch_enabled. User "
    "preferences in instructions override default worker routing. If "
    "dispatch_enabled is false, this Bridge was inherited inside a worker "
    "process — do not call dispatch_task, set_preferences, cancel_task, "
    "or end_session. When "
    "dispatch_enabled is true and the user states a lasting preference, "
    "persist it with set_preferences."
)

mcp = MCPServer[Registry]("agent-bridge", instructions=INSTRUCTIONS, lifespan=lifespan)


def _registry(ctx: Context) -> Registry:
    lifespan_ctx = ctx.request_context.lifespan_context
    if isinstance(lifespan_ctx, Registry):
        lifespan_ctx.touch_activity()
        return lifespan_ctx
    raise RuntimeError("Agent Bridge registry is not available")


def _error(exc: Exception) -> dict[str, Any]:
    return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "error_type": type(exc).__name__}


@mcp.tool(annotations=READ_ONLY)
async def list_agents(ctx: Context) -> dict[str, Any]:
    """List configured workers, the reconstructed host/proxy environment, and the coordinator policy (mode, instructions, runtime_context, dispatch_enabled). Call this first. If dispatch_enabled is false, this is a nested worker-inherited instance — do not dispatch or set_preferences."""
    try:
        registry = _registry(ctx)
        agents = await registry.list_agents()
        return {
            "ok": True,
            "agents": agents,
            "env": await registry.env_status(),
            "coordinator": registry.coordinator_status(),
        }
    except Exception as exc:
        return _error(exc)


@mcp.tool()
async def set_preferences(
    ctx: Context,
    mode: str | None = None,
    instructions: str | None = None,
) -> dict[str, Any]:
    """Persist the coordinator policy when the user states a lasting preference (e.g. "from now on, research goes to antigravity"). mode is manual/auto/eager; instructions is free routing-preference text that REPLACES the stored text — read coordinator.instructions from list_agents first and write the merged result. Applies to this instance immediately and to others at their next start. Do not call for one-off, this-task-only wishes. Rejected when coordinator.dispatch_enabled is false (nested worker-inherited Bridge)."""
    try:
        result = _registry(ctx).set_preferences(mode=mode, instructions=instructions)
        return {"ok": True, **result}
    except Exception as exc:
        return _error(exc)


@mcp.tool()
async def dispatch_task(
    ctx: Context,
    agent: str,
    message: str,
    cwd: str,
    session_id: str | None = None,
    model: str | None = None,
    effort: str | None = None,
    title: str | None = None,
    user_requested: bool = False,
) -> dict[str, Any]:
    """Start a worker turn. cwd is this coordinator conversation's project (absolute). model/effort are optional coordinator choices (agy: --model/--effort/--new-project; grok: session/setModel after /new; kimi/opencode/claude/devin: session/set_config_option after new/resume, devin has no effort; dsh: spawn env, respawn if they change; codex: exec -m / -c model_reasoning_effort, off->none). Pass session_id to continue. Set user_requested=true only when the user explicitly asked for a worker (required in manual mode). Rejected when coordinator.dispatch_enabled is false, even with user_requested=true. Returns immediately."""
    try:
        result = await _registry(ctx).dispatch_task(
            agent=agent,
            message=message,
            cwd=cwd,
            session_id=session_id,
            model=model,
            effort=effort,
            title=title,
            user_requested=user_requested,
        )
        return {"ok": True, **result}
    except Exception as exc:
        return _error(exc)


@mcp.tool(annotations=READ_ONLY)
async def wait_task(ctx: Context, task_id: str, timeout_sec: float = DEFAULT_WAIT_SEC) -> dict[str, Any]:
    """Wait until a task finishes or timeout_sec elapses (default 180). Timeout is not failure; call wait_task again. Stay under the host MCP tool timeout (Codex tool_timeout_sec, typically 600). Payloads carry silent_for_sec / stall_timeout_sec."""
    try:
        result = await _registry(ctx).wait_task(task_id, timeout_sec=timeout_sec)
        return {"ok": True, **result}
    except Exception as exc:
        return _error(exc)


@mcp.tool(annotations=READ_ONLY)
async def check_task(ctx: Context, task_id: str) -> dict[str, Any]:
    """Non-blocking status, elapsed time, and recent activity for a task. files_changed is capped at 200 paths; files_changed_total carries the real count. silent_for_sec is the time since the worker's last output; Bridge fails the task with stop_reason "stalled" once it passes stall_timeout_sec."""
    try:
        return {"ok": True, **_registry(ctx).check_task(task_id)}
    except Exception as exc:
        return _error(exc)


@mcp.tool(annotations=READ_ONLY)
async def get_result(
    ctx: Context,
    task_id: str,
    cursor: int = 0,
    max_chars: int = RESULT_PAGE_MAX_CHARS,
) -> dict[str, Any]:
    """Return a page of the complete worker result plus changed files, usage, and requested/observed model. Continue with next_cursor while has_more is true. max_chars is capped at 60000. files_changed is capped at 200 paths; files_changed_total carries the real count. For Grok, observed_model is the live sampler; the worker saying it is Grok 4.6 is not."""
    try:
        return {
            "ok": True,
            **_registry(ctx).get_result(task_id, cursor=cursor, max_chars=max_chars),
        }
    except Exception as exc:
        return _error(exc)


@mcp.tool(annotations=READ_ONLY)
async def get_transcript(
    ctx: Context,
    session_id: str,
    offset: int = 0,
    limit: int = 50,
    kinds: str | None = None,
) -> dict[str, Any]:
    """Paged session transcript. kinds is an optional comma-separated event type list."""
    try:
        kind_list = [item.strip() for item in kinds.split(",") if item.strip()] if kinds else None
        return {
            "ok": True,
            **_registry(ctx).get_transcript(session_id, offset=offset, limit=limit, kinds=kind_list),
        }
    except Exception as exc:
        return _error(exc)


@mcp.tool()
async def cancel_task(ctx: Context, task_id: str) -> dict[str, Any]:
    """Cancel an in-flight worker turn. ACP sessions are cancelled; agy processes are killed. Rejected when coordinator.dispatch_enabled is false."""
    try:
        return {"ok": True, **await _registry(ctx).cancel_task(task_id)}
    except Exception as exc:
        return _error(exc)


@mcp.tool(annotations=READ_ONLY)
async def list_sessions(ctx: Context, active_only: bool = False) -> dict[str, Any]:
    """List known worker sessions."""
    try:
        return {"ok": True, "sessions": _registry(ctx).list_sessions(active_only=active_only)}
    except Exception as exc:
        return _error(exc)


@mcp.tool()
async def end_session(ctx: Context, session_id: str) -> dict[str, Any]:
    """Shut down a worker session process and mark it dead. Rejected when coordinator.dispatch_enabled is false."""
    try:
        return {"ok": True, **await _registry(ctx).end_session(session_id)}
    except Exception as exc:
        return _error(exc)


def main() -> None:
    from agent_bridge.cli import main as cli_main

    cli_main()


if __name__ == "__main__":
    main()
