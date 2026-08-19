from __future__ import annotations

import json
import os
import re
import shutil
from collections.abc import Mapping
from pathlib import Path

from agent_bridge.models import dsh_effort
from agent_bridge.paths import bridge_home, bundled_dsh_cordis
from agent_bridge.processes import resolve_command
from agent_bridge.worker_env import (
    pick_env_keys,
    read_windows_machine_env,
    read_windows_user_env,
)

_OFFICIAL_DEMO_CORDIS = re.compile(
    r"(?:^|[/\\])examples[/\\]acp-agent[/\\]cordis\.yml$",
    re.IGNORECASE,
)
_DEFAULT_MODEL_BLOCK = re.compile(
    r"^agent-default-model:\s*\n((?:[ \t]+.*\n?)*)",
    re.MULTILINE,
)
_BLOCK_FIELD = re.compile(r"^[ \t]+(provider|model):\s*(\S+)", re.MULTILINE)
_API_KEY_ENV = re.compile(r"^[ \t]*apiKeyEnv:\s*(\S+)", re.MULTILINE)


def dsh_home(env: Mapping[str, str] | None = None) -> Path:
    raw = (env or os.environ).get("DSH_HOME", "").strip()
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".dsh"


def settings_text(home: Path | None = None, env: Mapping[str, str] | None = None) -> str:
    path = (home or dsh_home(env)) / "settings.yaml"
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def default_model(text: str | None = None, *, env: Mapping[str, str] | None = None) -> tuple[str, str] | None:
    source = text if text is not None else settings_text(env=env)
    match = _DEFAULT_MODEL_BLOCK.search(source)
    if not match:
        return None
    provider = ""
    model = ""
    for field in _BLOCK_FIELD.finditer(match.group(1)):
        value = field.group(2).strip().strip("'\"")
        if field.group(1) == "provider":
            provider = value
        else:
            model = value
    if provider and model:
        return provider, model
    return None


def api_key_env_names(text: str | None = None, *, env: Mapping[str, str] | None = None) -> list[str]:
    source = text if text is not None else settings_text(env=env)
    names: list[str] = []
    for match in _API_KEY_ENV.finditer(source):
        name = match.group(1).strip().strip("'\"")
        if name and name not in names:
            names.append(name)
    return names


_HARNESS_ENV = ("DSH_HARNESS", "DEEPSEEK_HARNESS", "DSH_CHECKOUT")
_DSH_ACP_PLUGINS = (
    "dsh-acp-demo",
    "dsh-acp",
    "dsh-app-boot",
    "dsh-agent-spine-demo",
    "dsh-tools",
    "dsh-invariants",
    "dsh-agent-instructions",
    "dsh-session-query",
    "dsh-session-query-sqlite",
    "dsh-session-checkpoint-policy",
    "dsh-session-persistence-jsonl",
    "dsh-settings-file",
    "dsh-credentials-local",
    "dsh-agent-default-model",
    "dsh-llm-pi-ai",
    "dsh-llm-deepseek",
    "dsh-sandbox-local",
    "dsh-sandbox-policy",
    "dsh-subprocess-local",
    "dsh-bash-sandbox",
    "dsh-user-approval",
    "dsh-token-meter",
    "dsh-compaction-basic",
    "dsh-session-projection",
    "dsh-subagent",
    "dsh-subagent-spawn-in-process",
    "dsh-subagent-fork-in-process",
    "dsh-tool-subagent-control",
    "dsh-tool-subagent-report",
    "dsh-tool-subagent",
    "dsh-workflow-worker-thread",
    "dsh-tool-workflow",
    "dsh-tool-ralph",
    "dsh-tool-todo",
    "dsh-repeat-tool-reminder",
    "dsh-fs-sandbox",
    "dsh-fs-observation-policy",
    "dsh-tool-fs",
    "dsh-hooks-claude-code",
    "dsh-hooks-codex",
)
_DSH_ACP_HOST = (
    "@deepseek-ai/cordis@^4.0.1",
    "@deepseek-ai/schemastery@^3.18.1",
    "@deepseek-ai/cordis-plugin-loader@^1.0.2",
    "@deepseek-ai/cordis-plugin-include@^1.0.6",
)


def dsh_acp_install_dir(home: Path | None = None) -> Path:
    return (home or bridge_home()) / "dsh-acp"


def find_dsh_package_dir() -> Path | None:
    exe = shutil.which("dsh")
    if not exe:
        return None
    path = Path(exe).resolve()
    for parent in (path.parent, *path.parents):
        pkg = parent / "node_modules" / "@deepseek-ai" / "dsh"
        if (pkg / "package.json").is_file():
            return pkg
    return None


def installed_dsh_version() -> str | None:
    pkg = find_dsh_package_dir()
    if pkg is None:
        return None
    try:
        data = json.loads(pkg.joinpath("package.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    version = data.get("version")
    return version if isinstance(version, str) and version else None


def dsh_acp_packages() -> tuple[str, ...]:
    version = installed_dsh_version() or "0.1.0-rc.7"
    plugins = tuple(f"@deepseek-ai/{name}@{version}" for name in _DSH_ACP_PLUGINS)
    return (*plugins, *_DSH_ACP_HOST)


def dsh_acp_install_hint() -> str:
    version = installed_dsh_version() or "<same-as-dsh>"
    prefix = dsh_acp_install_dir()
    return (
        "product `dsh` has no ACP profile; any user installs the published "
        f"`@deepseek-ai/dsh-acp-demo@{version}` on PATH "
        f'(npm -g) or under "{prefix}" (see scripts/install_dsh_acp.py); '
        "optional DSH_ACP_BIN / DSH_HARNESS override a built checkout"
    )


def find_tsx() -> str | None:
    found = shutil.which("tsx")
    if found:
        return found
    node = shutil.which("node")
    if not node:
        return None
    for parent in Path(node).resolve().parents:
        candidate = parent / "node_modules" / "tsx" / "package.json"
        if candidate.is_file():
            return str(candidate.parent)
    return None


def unwrap_npm_shim(command: list[str]) -> list[str] | None:
    """Turn a Windows npm ``.cmd`` shim into ``[node, bin.js]`` so exec() works."""
    if not command:
        return None
    first = Path(command[0])
    if first.suffix.lower() not in {".cmd", ".bat"}:
        return None
    node = shutil.which("node")
    if not node:
        return None
    candidates = [
        first.parent / "node_modules" / "@deepseek-ai" / "dsh-acp-demo" / "lib" / "bin.js",
        first.parent.parent / "@deepseek-ai" / "dsh-acp-demo" / "lib" / "bin.js",
    ]
    for js in candidates:
        if js.is_file():
            return [node, str(js), *command[1:]]
    return None


def canonicalize_dsh_command(command: list[str]) -> list[str]:
    return unwrap_npm_shim(command) or command


def dsh_command_problem(command: list[str]) -> str | None:
    command = canonicalize_dsh_command(command)
    first = Path(command[0]) if command else Path()
    if first.suffix.lower() in {".cmd", ".bat"}:
        return f"Windows npm shim cannot be exec'd directly: {first}"
    for index, arg in enumerate(command):
        if arg == "--import" and index + 1 < len(command):
            spec = command[index + 1]
            if spec == "tsx" and not find_tsx():
                return "Cannot find package 'tsx' (needed for dsh-acp-demo TypeScript source)"
            if spec not in {"tsx"} and not Path(spec).is_file() and not shutil.which(spec):
                return f"node --import {spec} is not available"
        if arg.lower().endswith((".js", ".ts", ".mjs", ".cjs")) and not Path(arg).is_file():
            return f"dsh-acp-demo bin missing: {arg}"
    return None


def _acp_demo_js(prefix: Path) -> Path | None:
    js = prefix / "node_modules" / "@deepseek-ai" / "dsh-acp-demo" / "lib" / "bin.js"
    return js if js.is_file() else None


def npm_global_prefixes() -> list[Path]:
    found: list[Path] = []
    seen: set[str] = set()

    def add(path: Path) -> None:
        try:
            key = str(path.expanduser().resolve())
        except OSError:
            key = str(path)
        if key in seen:
            return
        seen.add(key)
        found.append(Path(key))

    for name in ("dsh-acp-demo", "dsh", "npm"):
        exe = shutil.which(name)
        if exe:
            add(Path(exe).resolve().parent)
    for key in ("NPM_CONFIG_PREFIX", "npm_config_prefix"):
        raw = os.environ.get(key, "").strip()
        if raw:
            add(Path(raw))
    appdata = os.environ.get("APPDATA", "").strip()
    if appdata:
        add(Path(appdata) / "npm")
    add(Path.home() / ".npm-global")
    return found


def harness_checkouts(env: Mapping[str, str] | None = None) -> list[Path]:
    source = env if env is not None else os.environ
    roots: list[Path] = []
    for key in _HARNESS_ENV:
        raw = source.get(key, "").strip()
        if raw:
            roots.append(Path(raw).expanduser())
    return roots


def discovered_dsh_acp_commands() -> list[list[str]]:
    found: list[list[str]] = []
    seen: set[tuple[str, ...]] = set()
    node = shutil.which("node")

    def add(command: list[str]) -> None:
        command = canonicalize_dsh_command(command)
        if dsh_command_problem(command):
            return
        key = tuple(command)
        if key in seen:
            return
        seen.add(key)
        found.append(command)

    explicit = os.environ.get("DSH_ACP_BIN", "").strip()
    if explicit:
        path = Path(explicit).expanduser()
        if path.suffix.lower() in {".js", ".mjs", ".cjs", ".ts"} and path.is_file() and node:
            add([node, str(path)])
        else:
            add([explicit])

    for name in ("dsh-acp-demo", "dsh-acp-demo.cmd"):
        exe = shutil.which(name)
        if exe:
            add([exe])
            break

    if node:
        for prefix in npm_global_prefixes():
            js = _acp_demo_js(prefix)
            if js:
                add([node, str(js)])
        js = _acp_demo_js(dsh_acp_install_dir())
        if js:
            add([node, str(js)])
        for checkout in harness_checkouts():
            built = checkout / "packages" / "examples" / "acp-demo" / "lib" / "bin.js"
            source = checkout / "packages" / "examples" / "acp-demo" / "src" / "bin.ts"
            if built.is_file():
                add([node, str(built)])
            elif source.is_file() and find_tsx():
                add([node, "--import", "tsx", str(source)])
    return found


def resolve_dsh_command(command: list[str], fallbacks: list[list[str]] | None = None) -> list[str]:
    try:
        resolved = resolve_command(
            command,
            fallbacks,
            extra=discovered_dsh_acp_commands(),
            validate=dsh_command_problem,
        )
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"{exc}; {dsh_acp_install_hint()}") from exc
    return canonicalize_dsh_command(resolved)


def find_dsh_node_modules() -> Path | None:
    pkg = find_dsh_package_dir()
    if pkg is not None and (pkg / "node_modules").is_dir():
        return pkg / "node_modules"
    exe = shutil.which("dsh")
    if not exe:
        return None
    path = Path(exe).resolve()
    for parent in (path.parent, *path.parents):
        nested = parent / "node_modules" / "@deepseek-ai" / "dsh" / "node_modules"
        if nested.is_dir():
            return nested
        if parent.name == "dsh" and (parent / "node_modules").is_dir():
            return parent / "node_modules"
    return None


def _node_module_roots(command: list[str]) -> list[Path]:
    roots: list[Path] = []
    local = dsh_acp_install_dir() / "node_modules"
    if local.is_dir():
        roots.append(local)
    installed = find_dsh_node_modules()
    if installed:
        roots.append(installed)
    for arg in command:
        if not arg.endswith((".js", ".ts")):
            continue
        for parent in Path(arg).parents:
            candidate = parent / "node_modules"
            if candidate.is_dir() and candidate not in roots:
                roots.append(candidate)
                break
    return roots


def command_has_config(command: list[str]) -> bool:
    for arg in command:
        if arg in {"--config", "-c"} or arg.startswith("--config="):
            return True
    return False


def launch_prefix_for_command(command: list[str]) -> Path | None:
    for arg in command:
        path = Path(arg)
        if path.suffix.lower() not in {".js", ".ts", ".mjs", ".cjs"}:
            continue
        for parent in path.parents:
            scoped = parent / "node_modules" / "@deepseek-ai"
            if scoped.is_dir():
                return parent
    prefix = dsh_acp_install_dir()
    if (prefix / "node_modules" / "@deepseek-ai").is_dir():
        return prefix
    return None


def dsh_cordis_for_launch(command: list[str] | None = None) -> Path:
    """Put cordis beside the ACP node_modules so ESM can resolve plugins.

    Cordis plugin names resolve from the config file's directory, not NODE_PATH.
    """
    bundled = bundled_dsh_cordis()
    prefix = launch_prefix_for_command(command or [])
    if prefix is None:
        return bundled
    dest = prefix / "dsh-acp.cordis.yml"
    text = bundled.read_text(encoding="utf-8")
    try:
        if dest.is_file() and dest.read_text(encoding="utf-8") == text:
            return dest
        dest.write_text(text, encoding="utf-8")
    except OSError:
        return bundled
    return dest


def with_bridge_cordis(command: list[str]) -> list[str]:
    cordis = str(dsh_cordis_for_launch(command))
    rewritten = []
    replaced = False
    for arg in command:
        if _OFFICIAL_DEMO_CORDIS.search(arg.replace("\\", "/")):
            rewritten.append(cordis)
            replaced = True
        else:
            rewritten.append(arg)
    if replaced or command_has_config(rewritten):
        return rewritten
    return [*rewritten, "--config", cordis]


def apply_dsh_worker_env(
    env: Mapping[str, str],
    *,
    command: list[str] | None = None,
    session_id: str | None = None,
    model: str | None = None,
    effort: str | None = None,
    user_env: Mapping[str, str] | None = None,
    machine_env: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Attach DSH home, the user's saved default model, and credential env refs.

    No provider is required. Official DeepSeek is only one of the routes DSH
    already knows; users configure providers in ``$DSH_HOME/settings.yaml``.
    """
    out = dict(env)
    home = dsh_home(out)
    out.setdefault("DSH_HOME", str(home))
    text = settings_text(home, env=out)
    selection = default_model(text)
    if selection:
        settings_provider, settings_model = selection
        out.setdefault("DSH_ACP_PROVIDER", settings_provider)
        out.setdefault("DSH_ACP_MODEL", settings_model)
    extra_keys = api_key_env_names(text)
    if extra_keys:
        machine = machine_env if machine_env is not None else read_windows_machine_env()
        user = user_env if user_env is not None else read_windows_user_env()
        for incoming in (machine, user):
            for key, value in pick_env_keys(incoming, extra_keys).items():
                out.setdefault(key, value)
    roots = _node_module_roots(command or [])
    if roots:
        sep = os.pathsep
        existing = out.get("NODE_PATH", "")
        prefix = sep.join(str(path) for path in roots)
        out["NODE_PATH"] = prefix if not existing else f"{prefix}{sep}{existing}"
    if session_id:
        sessions = (bridge_home() / "dsh-sessions" / session_id).resolve()
        sessions.mkdir(parents=True, exist_ok=True)
        out.setdefault("DSH_SNAPSHOT_SESSIONS_ROOT", str(sessions))
    if model:
        text = model.strip()
        if "/" in text and not text.startswith("@"):
            provider, name = text.split("/", 1)
            if provider and name:
                out["DSH_ACP_PROVIDER"] = provider
                out["DSH_ACP_MODEL"] = name
            else:
                out["DSH_ACP_MODEL"] = text
        else:
            out["DSH_ACP_MODEL"] = text
    mapped = dsh_effort(effort)
    if mapped:
        out["DSH_ACP_REASONING_EFFORT"] = mapped
    return out


def prepare_dsh_launch(
    command: list[str],
    env: Mapping[str, str],
    *,
    session_id: str | None = None,
    model: str | None = None,
    effort: str | None = None,
    user_env: Mapping[str, str] | None = None,
    machine_env: Mapping[str, str] | None = None,
) -> tuple[list[str], dict[str, str]]:
    return with_bridge_cordis(command), apply_dsh_worker_env(
        env,
        command=command,
        session_id=session_id,
        model=model,
        effort=effort,
        user_env=user_env,
        machine_env=machine_env,
    )
