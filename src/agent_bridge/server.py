from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from mcp.server.mcpserver import Context, MCPServer
from mcp_types import ToolAnnotations

from agent_bridge.logging_setup import setup_logging
from agent_bridge.models import DEFAULT_WAIT_SEC
from agent_bridge.paths import ensure_home
from agent_bridge.registry import Registry

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


mcp = MCPServer[Registry]("agent-bridge", lifespan=lifespan)


def _registry(ctx: Context) -> Registry:
    lifespan_ctx = ctx.request_context.lifespan_context
    if isinstance(lifespan_ctx, Registry):
        return lifespan_ctx
    if isinstance(lifespan_ctx, dict) and "registry" in lifespan_ctx:
        return lifespan_ctx["registry"]
    registry = getattr(lifespan_ctx, "registry", None)
    if isinstance(registry, Registry):
        return registry
    raise RuntimeError("Agent Bridge registry is not available")


def _error(exc: Exception) -> dict[str, Any]:
    return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


@mcp.tool(annotations=READ_ONLY)
async def list_agents(ctx: Context) -> dict[str, Any]:
    """List configured workers, plus the reconstructed host/proxy environment Codex would otherwise strip."""
    try:
        registry = _registry(ctx)
        agents = await registry.list_agents()
        return {"ok": True, "agents": agents, "env": registry.env_status()}
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
) -> dict[str, Any]:
    """Start a worker turn. cwd is this Codex conversation's project (absolute). model/effort are optional coordinator choices (agy: --model/--effort/--new-project; grok: session/setModel after /new; dsh: spawn env, respawn if they change). Pass session_id to continue. Returns immediately."""
    try:
        result = await _registry(ctx).dispatch_task(
            agent=agent,
            message=message,
            cwd=cwd,
            session_id=session_id,
            model=model,
            effort=effort,
            title=title,
        )
        return {"ok": True, **result}
    except Exception as exc:
        return _error(exc)


@mcp.tool(annotations=READ_ONLY)
async def wait_task(ctx: Context, task_id: str, timeout_sec: float = DEFAULT_WAIT_SEC) -> dict[str, Any]:
    """Wait until a task finishes or timeout_sec elapses (default 180). Timeout is not failure; call wait_task again. Stay under the host MCP tool timeout (Codex tool_timeout_sec, typically 600)."""
    try:
        result = await _registry(ctx).wait_task(task_id, timeout_sec=timeout_sec)
        return {"ok": True, **result}
    except Exception as exc:
        return _error(exc)


@mcp.tool(annotations=READ_ONLY)
async def check_task(ctx: Context, task_id: str) -> dict[str, Any]:
    """Non-blocking status, elapsed time, and recent activity for a task."""
    try:
        return {"ok": True, **_registry(ctx).check_task(task_id)}
    except Exception as exc:
        return _error(exc)


@mcp.tool(annotations=READ_ONLY)
async def get_result(ctx: Context, task_id: str) -> dict[str, Any]:
    """Return the truncated worker result, changed files, usage, and requested/observed model. For Grok, observed_model is the live sampler; the worker saying it is Grok 4.6 is not."""
    try:
        return {"ok": True, **_registry(ctx).get_result(task_id)}
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
    """Cancel an in-flight worker turn. ACP sessions are cancelled; agy processes are killed."""
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
    """Shut down a worker session process and mark it dead."""
    try:
        return {"ok": True, **await _registry(ctx).end_session(session_id)}
    except Exception as exc:
        return _error(exc)


def main() -> None:
    import json
    import sys

    from agent_bridge.config import load_config
    from agent_bridge.worker_env import describe_env

    if len(sys.argv) > 1 and sys.argv[1] in {"--env", "env", "--print-env"}:
        status = describe_env(load_config().env)
        print(json.dumps(status, indent=2, ensure_ascii=False))
        return
    setup_logging(ensure_home())
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
