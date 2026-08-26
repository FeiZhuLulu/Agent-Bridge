from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path

from agent_bridge.config import AgentConfig, EnvConfig
from agent_bridge.dsh_home import apply_dsh_worker_env, default_model, dsh_home, resolve_dsh_command
from agent_bridge.kimi_observe import kimi_home
from agent_bridge.processes import kill_tree, resolve_command
from agent_bridge.traits import LaunchResolver, ProbeProfile, traits_for
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
    traits = traits_for(cfg)
    traits_payload = traits.model_dump(mode="json")
    if cfg.protocol == "fake":
        return {
            "agent": cfg.name,
            "available": True,
            "version": "fake",
            "detail": "in-process fake adapter",
            "traits": traits_payload,
        }
    try:
        if traits.launch_resolver is LaunchResolver.dsh:
            command = resolve_dsh_command(cfg.command, cfg.fallback_commands)
        else:
            command = resolve_command(cfg.command, cfg.fallback_commands)
    except FileNotFoundError as exc:
        return {
            "agent": cfg.name,
            "available": False,
            "version": None,
            "detail": str(exc),
            "traits": traits_payload,
        }

    details: list[str] = [f"command={command[0]}"]
    version = await _version_string(command[0])
    resolved = build_worker_env(
        cfg.env,
        config=env_config,
        log_fill=False,
    )

    if traits.probe_profile is ProbeProfile.dsh:
        bin_path = next((arg for arg in command if arg.endswith((".js", ".ts"))), None)
        if bin_path and not Path(bin_path).is_file():
            return {
                "agent": cfg.name,
                "available": False,
                "version": version or None,
                "detail": f"dsh-acp-demo bin missing: {bin_path}",
                "traits": traits_payload,
            }
        if command[0].lower().endswith(("node", "node.exe")) or any(
            arg.endswith((".js", ".ts")) for arg in command
        ):
            node = shutil.which("node")
            if not node:
                return {
                    "agent": cfg.name,
                    "available": False,
                    "version": None,
                    "detail": "node not found",
                    "traits": traits_payload,
                }
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
    details.extend(traits.probe_notes)

    if traits.probe_profile is ProbeProfile.kimi:
        details.append(f"kimi-home={_kimi_home(resolved)}")
        details.append(f"auth={_kimi_auth(resolved)}")

    if traits.probe_profile is ProbeProfile.opencode:
        if resolved.get("OPENCODE_API_KEY"):
            details.append("OPENCODE_API_KEY=set")

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
        "traits": traits_payload,
    }


def command_exists(cfg: AgentConfig) -> bool:
    if cfg.protocol == "fake":
        return True
    try:
        if traits_for(cfg).launch_resolver is LaunchResolver.dsh:
            resolve_dsh_command(cfg.command, cfg.fallback_commands)
        else:
            resolve_command(cfg.command, cfg.fallback_commands)
        return True
    except FileNotFoundError:
        return False
