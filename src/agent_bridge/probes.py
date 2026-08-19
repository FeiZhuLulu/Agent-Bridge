from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path

from agent_bridge.config import AgentConfig, EnvConfig
from agent_bridge.dsh_home import apply_dsh_worker_env, default_model, dsh_home, resolve_dsh_command
from agent_bridge.processes import kill_tree, resolve_command
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
            if proc.pid:
                kill_tree(proc.pid)
            continue
        text = (out or b"").decode("utf-8", errors="replace").strip() or (
            err or b""
        ).decode("utf-8", errors="replace").strip()
        if text:
            return text.splitlines()[0][:200]
    return ""


async def probe_agent(cfg: AgentConfig, env_config: EnvConfig | None = None) -> dict:
    if cfg.protocol == "fake":
        return {"agent": cfg.name, "available": True, "version": "fake", "detail": "in-process fake adapter"}
    try:
        if cfg.name == "dsh":
            command = resolve_dsh_command(cfg.command, cfg.fallback_commands)
        else:
            command = resolve_command(cfg.command, cfg.fallback_commands)
    except FileNotFoundError as exc:
        return {"agent": cfg.name, "available": False, "version": None, "detail": str(exc)}

    details: list[str] = [f"command={command[0]}"]
    version = await _version_string(command[0])
    resolved = build_worker_env(
        cfg.env,
        config=env_config,
        log_fill=False,
    )

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

    if cfg.name == "grok":
        details.append(
            "model=grok models slugs via session/setModel after /new; "
            "effort=off|low|medium|high|max (off->none, max->xhigh)"
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
        else:
            resolve_command(cfg.command, cfg.fallback_commands)
        return True
    except FileNotFoundError:
        return False
