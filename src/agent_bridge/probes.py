from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path

from agent_bridge.claude_meta import apply_claude_gateway_env, claude_config_home, describe_claude_auth
from agent_bridge.codex_exec import resolve_codex_command
from agent_bridge.config import AgentConfig, EnvConfig
from agent_bridge.devin_meta import apply_devin_env
from agent_bridge.dsh_home import (
    apply_dsh_worker_env,
    default_model,
    dsh_home,
    installed_dsh_version,
    resolve_dsh_command,
)
from agent_bridge.kimi_observe import kimi_home
from agent_bridge.processes import reap_subprocess, resolve_command
from agent_bridge.worker_env import build_worker_env

log = logging.getLogger(__name__)


async def _version_string(executable: str) -> str:
    for args in ([executable, "--version"], [executable, "-V"], [executable, "version"]):
        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError:
            continue
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=6)
        except TimeoutError:
            await reap_subprocess(proc)
            continue
        text = (out or b"").decode("utf-8", errors="replace").strip() or (
            err or b""
        ).decode("utf-8", errors="replace").strip()
        if text:
            return text.splitlines()[0][:200]
    return ""


async def _devin_auth(executable: str, env: dict[str, str]) -> str:
    """First line of ``devin auth status``: ``Logged in (via Devin).`` or ``Not logged in.``

    The CLI reports "Not logged in" whenever ``ACP_BACKEND`` is present, so
    the probe runs with the same env the worker will get.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            executable,
            "auth",
            "status",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        out, _err = await asyncio.wait_for(proc.communicate(), timeout=10)
    except OSError:
        return "unknown"
    except TimeoutError:
        await reap_subprocess(proc)
        return "unknown"
    first = (out or b"").decode("utf-8", errors="replace").strip().splitlines()
    return first[0][:80] if first else "unknown"


def _kimi_home(env: dict[str, str]) -> Path:
    raw = env.get("KIMI_CODE_HOME")
    return kimi_home(Path(raw) if raw else None)


def _kimi_auth(env: dict[str, str]) -> str:
    """Describe Kimi's login state without deciding availability.

    Kimi keeps managed-provider OAuth at ``credentials/<name>.json``; without
    one, ``session/new`` answers ``auth_required``. An API key in the
    environment is the other accepted path, so a missing credential file is a
    warning to surface, not grounds for calling the agent unavailable.
    """
    try:
        signed_in = any((_kimi_home(env) / "credentials").glob("*.json"))
    except OSError:
        signed_in = False
    if signed_in:
        return "oauth"
    if env.get("KIMI_API_KEY") or env.get("MOONSHOT_API_KEY"):
        return "api-key"
    return "missing (run `kimi login`)"


async def probe_agent(cfg: AgentConfig, env_config: EnvConfig | None = None) -> dict:
    if cfg.protocol == "fake":
        return {"agent": cfg.name, "available": True, "version": "fake", "detail": "in-process fake adapter"}
    resolved = build_worker_env(
        cfg.env,
        config=env_config,
        log_fill=False,
    )
    def _resolve_probe_command() -> list[str]:
        if cfg.name == "dsh":
            return resolve_dsh_command(cfg.command, cfg.fallback_commands)
        if cfg.protocol == "codex":
            return resolve_codex_command(cfg.command, cfg.fallback_commands, env=resolved)
        return resolve_command(cfg.command, cfg.fallback_commands)

    try:
        command = await asyncio.to_thread(_resolve_probe_command)
    except FileNotFoundError as exc:
        return {"agent": cfg.name, "available": False, "version": None, "detail": str(exc)}

    details: list[str] = [f"command={command[0]}"]
    version = await _version_string(command[0])

    if cfg.name == "dsh":
        bin_path = next((arg for arg in command if arg.endswith((".js", ".ts"))), None)
        if bin_path and not Path(bin_path).is_file():
            return {
                "agent": cfg.name,
                "available": False,
                "version": version or None,
                "detail": f"dsh-acp-demo bin missing: {bin_path}",
            }
        if command[0].lower().endswith(("node", "node.exe")) or any(
            arg.endswith((".js", ".ts")) for arg in command
        ):
            node = shutil.which("node")
            if not node:
                return {"agent": cfg.name, "available": False, "version": None, "detail": "node not found"}
            node_ver = await _version_string(node)
            details.append(f"node={node_ver or 'unknown'}")
        dsh_version = await asyncio.to_thread(installed_dsh_version)
        if dsh_version:
            version = dsh_version
        dsh_env = apply_dsh_worker_env(resolved, command=command)
        home = dsh_home(dsh_env)
        details.append(f"dsh-home={home}")
        selection = default_model(env=dsh_env)
        if selection:
            details.append(f"dsh-model={selection[0]}/{selection[1]}")
        else:
            details.append("dsh-model=unset (DSH composition default)")
        details.append("effort=off|low|high|max via dispatch_task.effort; same session model change respawns")

    if cfg.name == "antigravity":
        details.append("model=agy models slugs e.g. gemini-3.7-flash; effort=low|medium|high")
        details.append("prompt via stdin (stream-json)")

    if cfg.name == "grok":
        details.append(
            "model=grok models slugs via session/setModel after /new; "
            "effort=off|low|medium|high|max (off->none, max->xhigh)"
        )

    if cfg.name == "kimi":
        details.append(
            "model=slugs the session advertises e.g. kimi-code/k3, kimi-code/k3-256k; "
            "effort mapped onto that model's thinking levels; mode forced to yolo"
        )
        details.append(f"kimi-home={_kimi_home(resolved)}")
        details.append(f"auth={_kimi_auth(resolved)}")

    if cfg.name == "opencode":
        details.append(
            "model=provider/model slugs the session advertises e.g. opencode/..., "
            "xai/...; effort mapped onto that model's variants"
        )
        details.append(
            "auth=provider API keys via `opencode auth` "
            "(official OpenCode Zen / Go, or any connected provider); "
            "no product login"
        )
        if resolved.get("OPENCODE_API_KEY"):
            details.append("OPENCODE_API_KEY=set")

    if cfg.name == "claude":
        auth = describe_claude_auth(resolved)
        resolved = apply_claude_gateway_env(resolved)
        details.append(
            "model=slugs the session advertises (sonnet, opus, haiku, or full ids); "
            "effort mapped onto that model's levels; mode forced to bypassPermissions"
        )
        details.append(f"claude-home={claude_config_home(resolved)}")
        details.append(f"auth={auth}")
        details.append(
            "product `claude` is not ACP; worker is claude-agent-acp "
            "(@agentclientprotocol/claude-agent-acp)"
        )

    if cfg.name == "devin":
        resolved = apply_devin_env(resolved)
        details.append(
            "model=ids the session advertises (`devin models list`), level is part of the id "
            "e.g. swe-1-7-medium, claude-opus-5-high; no effort option; mode forced to bypass"
        )
        details.append(f"auth={await _devin_auth(command[0], resolved)}")
        if resolved.get("WINDSURF_API_KEY"):
            details.append("WINDSURF_API_KEY=set")

    if cfg.protocol == "codex":
        details.append(
            "model=codex slugs e.g. gpt-5.6-sol; "
            "effort=off|low|medium|high|max (off->none); "
            "default --approve-for-me, prompt via stdin"
        )
        details.append(
            "auth=$CODEX_HOME or ~/.codex ChatGPT login or CODEX_API_KEY / OPENAI_API_KEY"
        )

    proxy = resolved.get("HTTPS_PROXY") or resolved.get("HTTP_PROXY")
    if proxy:
        details.append("proxy=set")
    else:
        details.append("proxy=missing")

    return {
        "agent": cfg.name,
        "available": True,
        "version": version or None,
        "detail": "; ".join(details),
    }


def command_exists(cfg: AgentConfig) -> bool:
    if cfg.protocol == "fake":
        return True
    try:
        if cfg.name == "dsh":
            resolve_dsh_command(cfg.command, cfg.fallback_commands)
        elif cfg.protocol == "codex":
            resolve_codex_command(
                cfg.command,
                cfg.fallback_commands,
                env=build_worker_env(cfg.env, log_fill=False),
            )
        else:
            resolve_command(cfg.command, cfg.fallback_commands)
        return True
    except FileNotFoundError:
        return False
