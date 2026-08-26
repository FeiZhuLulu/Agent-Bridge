from __future__ import annotations

import logging
import os
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from agent_bridge.config import DEFAULT_INHERIT_KEYS, EnvConfig
from agent_bridge.paths import (
    WORKER_CONTEXT_ENV,
    WORKER_CONTEXT_VALUE,
    bridge_home,
    nested_bridge_home,
    parent_context_is_worker,
)

log = logging.getLogger(__name__)


def is_worker_context(env: Mapping[str, str] | None = None) -> bool:
    """True only when AGENT_BRIDGE_PARENT_CONTEXT is exactly ``worker``."""
    return parent_context_is_worker(env)

PROXY_KEYS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "GROK_WEB_FETCH_PROXY",
)
DEFAULT_NO_PROXY = "localhost,127.0.0.1,::1"
_PROXY_KEY_ALIASES = {key.lower(): key for key in PROXY_KEYS}
_PROXY_KEY_ALIASES.update(
    {
        "http_proxy": "HTTP_PROXY",
        "https_proxy": "HTTPS_PROXY",
        "all_proxy": "ALL_PROXY",
        "no_proxy": "NO_PROXY",
    }
)

_PS_PROXY_ASSIGN = re.compile(
    r"""\$env:(?P<key>HTTPS?_PROXY|ALL_PROXY|NO_PROXY)\s*=\s*["'](?P<val>[^"']+)["']""",
    re.IGNORECASE,
)

_SECRET_MARKERS = ("_API_KEY", "_TOKEN", "_SECRET", "_PASSWORD", "PASSWORD")


def redact_proxy_url(url: str) -> str:
    parts = urlsplit(url.strip())
    if not parts.scheme:
        return url.strip()
    host = parts.hostname or ""
    port = f":{parts.port}" if parts.port else ""
    userinfo = "***@" if parts.username else ""
    netloc = f"{userinfo}{host}{port}"
    return urlunsplit((parts.scheme, netloc, "", "", ""))


def _present(env: Mapping[str, str], key: str) -> bool:
    return bool(str(env.get(key, "")).strip())


def _is_secret_key(key: str) -> bool:
    upper = key.upper()
    return any(marker in upper for marker in _SECRET_MARKERS)


def normalize_proxy_map(raw: Mapping[str, str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in raw.items():
        canonical = _PROXY_KEY_ALIASES.get(key) or _PROXY_KEY_ALIASES.get(key.lower())
        if not canonical:
            continue
        text = str(value).strip()
        if not text:
            continue
        out.setdefault(canonical, text)
    return out


def pick_env_keys(raw: Mapping[str, str], wanted: Sequence[str]) -> dict[str, str]:
    index = {key.lower(): str(value).strip() for key, value in raw.items() if str(value).strip()}
    out: dict[str, str] = {}
    for key in wanted:
        value = index.get(key.lower())
        if value:
            out[key] = value
    return out


def _as_proxy_url(server: str) -> str:
    text = server.strip()
    if "://" in text:
        return text
    return f"http://{text}"


def parse_win_inet_proxy_server(server: str) -> dict[str, str]:
    text = server.strip()
    if not text:
        return {}
    if "=" not in text:
        url = _as_proxy_url(text)
        return {"HTTP_PROXY": url, "HTTPS_PROXY": url, "ALL_PROXY": url}
    scheme_map: dict[str, str] = {}
    for item in text.split(";"):
        piece = item.strip()
        if not piece or "=" not in piece:
            continue
        name, value = piece.split("=", 1)
        scheme_map[name.strip().lower()] = value.strip()
    https = scheme_map.get("https") or scheme_map.get("http")
    http = scheme_map.get("http") or scheme_map.get("https")
    if not http and not https:
        return {}
    out: dict[str, str] = {}
    if http:
        out["HTTP_PROXY"] = _as_proxy_url(http)
    if https:
        out["HTTPS_PROXY"] = _as_proxy_url(https)
    chosen = out.get("HTTPS_PROXY") or out.get("HTTP_PROXY")
    if chosen:
        out["ALL_PROXY"] = chosen
    return out


def extract_powershell_function(text: str, name: str) -> str | None:
    lowered = text.lower()
    needle = f"function {name.lower()}"
    idx = lowered.find(needle)
    if idx < 0:
        return None
    brace = text.find("{", idx)
    if brace < 0:
        return None
    depth = 0
    for i, ch in enumerate(text[brace:], start=brace):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[brace + 1 : i]
    return None


def _proxy_assigns(text: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for match in _PS_PROXY_ASSIGN.finditer(text):
        canonical = _PROXY_KEY_ALIASES[match.group("key").lower()]
        found.setdefault(canonical, match.group("val").strip())
    return found


def parse_powershell_grok_proxy(text: str) -> dict[str, str]:
    """Read proxy assignments from PowerShell worker wrappers.

    Interactive wrappers (``function grok { ... }``, ``function opencode``)
    never run when Bridge spawn the raw executable. Scan those function
    bodies first so a machine that only set the proxy there still works.
    """
    for name in ("grok", "opencode", "kimi", "agy", "claude"):
        body = extract_powershell_function(text, name)
        if not body:
            continue
        found = _proxy_assigns(body)
        if found:
            return normalize_proxy_map(found)
    return normalize_proxy_map(_proxy_assigns(text))


def _documents_dirs() -> list[Path]:
    home = Path.home()
    dirs = [home / "Documents"]
    one_drive = os.environ.get("OneDrive")
    if one_drive:
        dirs.append(Path(one_drive) / "Documents")
    return dirs


def powershell_profile_paths() -> list[Path]:
    names = (
        Path("PowerShell") / "Microsoft.PowerShell_profile.ps1",
        Path("WindowsPowerShell") / "Microsoft.PowerShell_profile.ps1",
    )
    paths: list[Path] = []
    for docs in _documents_dirs():
        for name in names:
            path = docs / name
            if path not in paths:
                paths.append(path)
    return paths


def read_powershell_grok_proxy() -> dict[str, str]:
    merged: dict[str, str] = {}
    for path in powershell_profile_paths():
        try:
            text = path.read_text(encoding="utf-8-sig")
        except OSError:
            continue
        parsed = parse_powershell_grok_proxy(text)
        for key, value in parsed.items():
            merged.setdefault(key, value)
    return merged


def read_registry_environment(hive: int, subkey: str) -> dict[str, str]:
    if os.name != "nt":
        return {}
    import winreg

    try:
        key = winreg.OpenKey(hive, subkey)
    except OSError:
        return {}
    raw: dict[str, str] = {}
    try:
        index = 0
        while True:
            try:
                name, value, _typ = winreg.EnumValue(key, index)
            except OSError:
                break
            index += 1
            if isinstance(value, str) and value.strip():
                raw[name] = os.path.expandvars(value)
    finally:
        key.Close()
    return raw


def read_windows_user_env() -> dict[str, str]:
    if os.name != "nt":
        return {}
    import winreg

    return read_registry_environment(winreg.HKEY_CURRENT_USER, "Environment")


def read_windows_machine_env() -> dict[str, str]:
    if os.name != "nt":
        return {}
    import winreg

    return read_registry_environment(
        winreg.HKEY_LOCAL_MACHINE,
        r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
    )


def read_windows_user_proxy() -> dict[str, str]:
    return normalize_proxy_map(read_windows_user_env())


def read_windows_machine_proxy() -> dict[str, str]:
    return normalize_proxy_map(read_windows_machine_env())


def read_internet_settings_proxy() -> dict[str, str]:
    if os.name != "nt":
        return {}
    import winreg

    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
        )
    except OSError:
        return {}
    try:
        enable, _ = winreg.QueryValueEx(key, "ProxyEnable")
        server, _ = winreg.QueryValueEx(key, "ProxyServer")
    except OSError:
        return {}
    finally:
        key.Close()
    if not enable:
        return {}
    return parse_win_inet_proxy_server(str(server))


def collect_proxy_fallbacks(
    sources: list[tuple[str, Mapping[str, str]]] | None = None,
) -> tuple[dict[str, str], dict[str, str]]:
    if sources is None:
        sources = [
            ("powershell-grok", read_powershell_grok_proxy()),
            ("win-inet", read_internet_settings_proxy()),
        ]
    merged: dict[str, str] = {}
    origin: dict[str, str] = {}
    for name, raw in sources:
        for key, value in normalize_proxy_map(raw).items():
            if key not in merged:
                merged[key] = value
                origin[key] = name
    return merged, origin


def _mirror_proxy_keys(env: dict[str, str], origin: dict[str, str]) -> None:
    chosen = ""
    chosen_from = ""
    for key in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY"):
        if _present(env, key):
            chosen = env[key].strip()
            chosen_from = origin.get(key, "mirror")
            break
    if not chosen:
        return
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        if not _present(env, key):
            env[key] = chosen
            origin[key] = f"mirror:{chosen_from}"
    if not _present(env, "GROK_WEB_FETCH_PROXY"):
        env["GROK_WEB_FETCH_PROXY"] = chosen
        origin["GROK_WEB_FETCH_PROXY"] = f"mirror:{chosen_from}"


def _fill_missing(
    env: dict[str, str],
    origin: dict[str, str],
    incoming: Mapping[str, str],
    source: str,
) -> None:
    for key, value in incoming.items():
        text = str(value).strip()
        if not text:
            continue
        if not _present(env, key):
            env[key] = text
            origin[key] = source


def _assign(
    env: dict[str, str],
    origin: dict[str, str],
    incoming: Mapping[str, str],
    source: str,
) -> None:
    for key, value in incoming.items():
        text = str(value).strip()
        if not text:
            continue
        env[key] = text
        origin[key] = source


def _proxy_bundle(url: str) -> dict[str, str]:
    return {
        "HTTP_PROXY": url,
        "HTTPS_PROXY": url,
        "ALL_PROXY": url,
        "GROK_WEB_FETCH_PROXY": url,
    }


def resolve_env(
    config: EnvConfig | None = None,
    *,
    base: Mapping[str, str] | None = None,
    agent_env: Mapping[str, str] | None = None,
    fallbacks: Mapping[str, str] | None = None,
    fallback_origin: Mapping[str, str] | None = None,
    user_env: Mapping[str, str] | None = None,
    machine_env: Mapping[str, str] | None = None,
) -> tuple[dict[str, str], dict[str, str]]:
    cfg = config or EnvConfig()
    inherit = cfg.inherit or list(DEFAULT_INHERIT_KEYS)
    env = dict(os.environ if base is None else base)
    origin: dict[str, str] = {}
    for key in inherit:
        if _present(env, key):
            origin[key] = "process"
    for key in PROXY_KEYS:
        if _present(env, key):
            origin.setdefault(key, "process")

    if cfg.discover_proxy:
        if fallbacks is None:
            discovered, discovered_origin = collect_proxy_fallbacks()
        else:
            discovered = normalize_proxy_map(fallbacks)
            discovered_origin = dict(fallback_origin or {key: "fallback" for key in discovered})
        for key, value in discovered.items():
            if not _present(env, key):
                env[key] = value
                origin[key] = discovered_origin.get(key, "discover")

    machine = machine_env if machine_env is not None else read_windows_machine_env()
    _fill_missing(env, origin, pick_env_keys(machine, inherit), "machine-env")
    user = user_env if user_env is not None else read_windows_user_env()
    _fill_missing(env, origin, pick_env_keys(user, inherit), "user-env")

    bridge_proxy = (
        env.get("AGENT_BRIDGE_HTTPS_PROXY") or env.get("AGENT_BRIDGE_HTTP_PROXY") or ""
    ).strip()
    if bridge_proxy:
        _fill_missing(env, origin, _proxy_bundle(bridge_proxy), "AGENT_BRIDGE_HTTP_PROXY")
    bridge_no = (env.get("AGENT_BRIDGE_NO_PROXY") or "").strip()
    if bridge_no:
        _fill_missing(env, origin, {"NO_PROXY": bridge_no}, "AGENT_BRIDGE_NO_PROXY")

    if cfg.proxy_url:
        _assign(env, origin, _proxy_bundle(cfg.proxy_url.strip()), "config.proxy")
    if cfg.no_proxy:
        _assign(env, origin, {"NO_PROXY": cfg.no_proxy.strip()}, "config.proxy")
    if cfg.set:
        _assign(env, origin, {str(k): str(v) for k, v in cfg.set.items()}, "config.set")
    if agent_env:
        _assign(env, origin, {str(k): str(v) for k, v in agent_env.items()}, "agent.env")

    _mirror_proxy_keys(env, origin)
    if _present(env, "HTTPS_PROXY") or _present(env, "HTTP_PROXY"):
        if not _present(env, "NO_PROXY"):
            env["NO_PROXY"] = DEFAULT_NO_PROXY
            origin["NO_PROXY"] = "default"
    env.setdefault("PYTHONIOENCODING", "utf-8")
    origin.setdefault("PYTHONIOENCODING", origin.get("PYTHONIOENCODING", "default"))
    return env, origin


def apply_proxy_fallbacks(
    env: dict[str, str],
    fallbacks: Mapping[str, str],
) -> dict[str, str]:
    resolved, _origin = resolve_env(
        EnvConfig(discover_proxy=True, inherit=[]),
        base=env,
        fallbacks=fallbacks,
        user_env={},
        machine_env={},
    )
    return resolved


def describe_env(
    config: EnvConfig | None = None,
    *,
    base: Mapping[str, str] | None = None,
    agent_env: Mapping[str, str] | None = None,
    env: Mapping[str, str] | None = None,
    origin: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    cfg = config or EnvConfig()
    if env is None or origin is None:
        env, origin = resolve_env(cfg, base=base, agent_env=agent_env)
    proxy = (env.get("HTTPS_PROXY") or env.get("HTTP_PROXY") or "").strip() or None
    proxy_source = None
    if proxy:
        proxy_source = origin.get("HTTPS_PROXY") or origin.get("HTTP_PROXY")
    present: list[str] = []
    missing: list[str] = []
    for key in cfg.inherit:
        if _is_secret_key(key) or key in PROXY_KEYS:
            if _present(env, key):
                present.append(key)
            elif not key.startswith("AGENT_BRIDGE_"):
                missing.append(key)
    for key in ("AGENT_BRIDGE_HTTP_PROXY", "AGENT_BRIDGE_HTTPS_PROXY", "AGENT_BRIDGE_NO_PROXY"):
        if _present(env, key) and key not in present:
            present.append(key)
    for key in PROXY_KEYS:
        if _present(env, key) and key not in present:
            present.append(key)
    warnings: list[str] = []
    return {
        "proxy": redact_proxy_url(proxy) if proxy else None,
        "proxy_source": proxy_source,
        "no_proxy": env.get("NO_PROXY") or None,
        "discover_proxy": cfg.discover_proxy,
        "present": present,
        "missing": missing,
        "warnings": warnings,
    }


def format_env_status(status: Mapping[str, Any]) -> str:
    proxy = status.get("proxy") or "none"
    source = status.get("proxy_source")
    text = f"proxy={proxy}"
    if source:
        text += f" ({source})"
    present = status.get("present") or []
    if present:
        text += f" present={','.join(present)}"
    warnings = status.get("warnings") or []
    if warnings:
        text += f" warnings={'; '.join(warnings)}"
    return text


def install_host_env(config: EnvConfig, *, base: Mapping[str, str] | None = None) -> dict[str, Any]:
    original = dict(os.environ if base is None else base)
    env, origin = resolve_env(config, base=original)
    for key, value in env.items():
        if original.get(key) != value:
            os.environ[key] = value
    status = describe_env(config, env=env, origin=origin)
    log.info("host environment: %s", format_env_status(status))
    return status


def build_worker_env(
    overrides: Mapping[str, str] | None = None,
    *,
    config: EnvConfig | None = None,
    base: Mapping[str, str] | None = None,
    fallbacks: Mapping[str, str] | None = None,
    fallback_origin: Mapping[str, str] | None = None,
    user_env: Mapping[str, str] | None = None,
    machine_env: Mapping[str, str] | None = None,
    log_fill: bool = True,
    worker_context: bool = False,
) -> dict[str, str]:
    env, origin = resolve_env(
        config,
        base=base,
        agent_env=overrides,
        fallbacks=fallbacks,
        fallback_origin=fallback_origin,
        user_env=user_env,
        machine_env=machine_env,
    )
    # After every merge so agents.toml / inherited env cannot clear the mark.
    # Also pin a nested data dir: if the worker inherits this MCP server, the
    # nested Bridge must not share the coordinator's state.json. A host MCP
    # config may overwrite AGENT_BRIDGE_HOME; bridge_home() still appends
    # /nested when the parent-context mark is present. A host that env-clears
    # the MCP child drops both — that residual is documented in SETUP.md.
    if worker_context:
        env[WORKER_CONTEXT_ENV] = WORKER_CONTEXT_VALUE
        origin[WORKER_CONTEXT_ENV] = "worker-context"
        env["AGENT_BRIDGE_HOME"] = str(nested_bridge_home(bridge_home()))
        origin["AGENT_BRIDGE_HOME"] = "worker-context"
    if log_fill:
        status = describe_env(config, env=env, origin=origin)
        log.info("worker environment: %s", format_env_status(status))
    return env
