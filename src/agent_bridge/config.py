from __future__ import annotations

import json
import os
import re
import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from agent_bridge.paths import bridge_home, bundled_agents_toml
from agent_bridge.persist import atomic_write_text

DEFAULT_INHERIT_KEYS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "GROK_WEB_FETCH_PROXY",
    "AGENT_BRIDGE_HTTP_PROXY",
    "AGENT_BRIDGE_HTTPS_PROXY",
    "AGENT_BRIDGE_NO_PROXY",
    "DSH_HOME",
    "KIMI_CODE_HOME",
    "KIMI_SHELL_PATH",
    "KIMI_API_KEY",
    "KIMI_BASE_URL",
    "KIMI_CODE_BASE_URL",
    "MOONSHOT_API_KEY",
    "DEEPSEEK_API_KEY",
    "OPENCODE_API_KEY",
    "XAI_API_KEY",
    "GROK_API_KEY",
    "OPENAI_API_KEY",
    "CODEX_API_KEY",
    "CODEX_CLI_PATH",
    "CODEX_HOME",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    "CLAUDE_CONFIG_DIR",
    "CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY",
    "OPENROUTER_API_KEY",
    "GOOGLE_API_KEY",
    "GEMINI_API_KEY",
    "CLOUDFLARE_API_TOKEN",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "REQUESTS_CA_BUNDLE",
    "CURL_CA_BUNDLE",
    "NODE_EXTRA_CA_CERTS",
    "PYTHONUTF8",
    "PYTHONIOENCODING",
    "LANG",
    "LC_ALL",
)


class AgentConfig(BaseModel):
    name: str
    protocol: str
    command: list[str]
    fallback_commands: list[list[str]] = Field(default_factory=list)
    cwd: str | None = None
    env: dict[str, str] = Field(default_factory=dict)
    session_meta: dict[str, Any] = Field(default_factory=dict)
    revivable: bool = False
    idle_unload_sec: int = 0
    print_timeout: str = "120m"


class EnvConfig(BaseModel):
    """How Bridge rebuilds the environment Codex (or another MCP host) stripped."""

    inherit: list[str] = Field(default_factory=lambda: list(DEFAULT_INHERIT_KEYS))
    discover_proxy: bool = True
    set: dict[str, str] = Field(default_factory=dict)
    proxy_url: str | None = None
    no_proxy: str | None = None


class ServerConfig(BaseModel):
    """Process-level server behavior (idle self-exit for abandoned MCP instances)."""

    idle_exit_sec: int = 7200


COORDINATOR_MODES = ("manual", "auto", "eager")
# The notes that inspired this used safe/yolo; yolo already means
# "auto-approve tool calls" for Kimi and Grok, so the canonical names differ.
_MODE_ALIASES = {"safe": "manual", "yolo": "eager"}

# Sent back on every list_agents call so the coordinator re-reads the policy
# at the moment it matters, instead of relying on rules it saw turns ago.
COORDINATOR_MODE_HINTS = {
    "manual": (
        "dispatch only when the user explicitly asked for a worker; "
        "dispatch_task is blocked unless user_requested=true"
    ),
    "auto": "dispatch at your own judgment; weigh dispatch overhead against doing it yourself",
    "eager": (
        "prefer dispatching workers over doing multi-step work yourself; "
        "you keep architecture decisions and acceptance"
    ),
}


def normalize_coordinator_mode(raw: str | None, *, strict: bool = False) -> str:
    text = (raw or "").strip().lower()
    text = _MODE_ALIASES.get(text, text)
    if text in COORDINATOR_MODES:
        return text
    if strict:
        raise ValueError(
            f"unknown coordinator mode {raw!r}; use one of "
            f"{', '.join(COORDINATOR_MODES)} (aliases: safe, yolo)"
        )
    return "auto"


class CoordinatorConfig(BaseModel):
    """How eagerly the coordinator should dispatch, plus user routing preferences.

    Bridge does not interpret ``instructions``; it relays the text through
    ``list_agents`` for the coordinator LLM to read. ``mode`` is the only key
    Bridge acts on itself: ``manual`` hard-blocks dispatch_task without
    ``user_requested=true``.
    """

    mode: str = "auto"
    instructions: str = ""


class AppConfig(BaseModel):
    agents: dict[str, AgentConfig] = Field(default_factory=dict)
    env: EnvConfig = Field(default_factory=EnvConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)
    coordinator: CoordinatorConfig = Field(default_factory=CoordinatorConfig)

    def get(self, name: str) -> AgentConfig:
        if name not in self.agents:
            known = ", ".join(sorted(self.agents)) or "(none)"
            raise KeyError(f"unknown agent {name!r}; known: {known}")
        return self.agents[name]


def _load_toml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    with path.open("rb") as fh:
        return tomllib.load(fh)


def _raw_agents(raw: dict[str, Any]) -> dict[str, dict[str, Any]]:
    block = raw.get("agents") or {}
    out: dict[str, dict[str, Any]] = {}
    for name, spec in block.items():
        if isinstance(spec, dict):
            out[name] = dict(spec)
    return out


def _merge_agent_spec(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in overlay.items():
        if value in (None, [], {}):
            continue
        if key == "env" and isinstance(out.get("env"), dict) and isinstance(value, dict):
            out["env"] = {**out["env"], **value}
        else:
            out[key] = value
    return out


def _coerce_env(raw: dict[str, Any]) -> dict[str, Any]:
    block = raw.get("env")
    if not isinstance(block, dict):
        return {}
    proxy = block.get("proxy") if isinstance(block.get("proxy"), dict) else {}
    out: dict[str, Any] = {}
    if "inherit" in block and block["inherit"] is not None:
        out["inherit"] = [str(item) for item in block["inherit"]]
    if "discover_proxy" in block:
        out["discover_proxy"] = bool(block["discover_proxy"])
    if isinstance(block.get("set"), dict):
        out["set"] = {str(key): str(value) for key, value in block["set"].items() if value is not None}
    url = proxy.get("url") or block.get("proxy_url")
    if url:
        out["proxy_url"] = str(url).strip()
    no_proxy = proxy.get("no_proxy") or proxy.get("no") or block.get("no_proxy")
    if no_proxy:
        out["no_proxy"] = str(no_proxy).strip()
    return out


def _merge_env(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in overlay.items():
        if key == "set" and isinstance(value, dict):
            current = out.get("set") if isinstance(out.get("set"), dict) else {}
            out["set"] = {**current, **value}
        else:
            out[key] = value
    return out


def _coerce_server(raw: dict[str, Any]) -> dict[str, Any]:
    block = raw.get("server")
    if not isinstance(block, dict):
        return {}
    out: dict[str, Any] = {}
    if "idle_exit_sec" in block and block["idle_exit_sec"] is not None:
        out["idle_exit_sec"] = int(block["idle_exit_sec"])
    return out


def _coerce_coordinator(raw: dict[str, Any]) -> dict[str, Any]:
    block = raw.get("coordinator")
    if not isinstance(block, dict):
        return {}
    out: dict[str, Any] = {}
    if block.get("mode") is not None:
        out["mode"] = str(block["mode"])
    if block.get("instructions") is not None:
        out["instructions"] = str(block["instructions"]).strip()
    return out


def _toml_string(value: str) -> str:
    if not value:
        return '""'
    if "\n" not in value and '"' not in value and "\\" not in value:
        return f'"{value}"'
    # Literal multiline: no escape processing, so Windows paths survive.
    if "'''" not in value and not value.endswith("'"):
        return f"'''\n{value}\n'''"
    return json.dumps(value, ensure_ascii=False)


_COORDINATOR_HEADER = re.compile(r"(?m)^\[coordinator\][ \t]*(#[^\n]*)?\n?")
_NEXT_TABLE = re.compile(r"(?m)^\[")


def _coordinator_span(text: str) -> tuple[int, int] | None:
    """Return the [coordinator] table span, skipping `[` lines inside strings."""
    header = _COORDINATOR_HEADER.search(text)
    if header is None:
        return None
    start = header.start()
    header_end = header.end()
    ends = [header_end + match.start() for match in _NEXT_TABLE.finditer(text[header_end:])]
    ends.append(len(text))
    for end in ends:
        block = text[start:end]
        rest = text[:start] + text[end:]
        try:
            parsed_block = tomllib.loads(block)
            parsed_rest = tomllib.loads(rest) if rest.strip() else {}
        except tomllib.TOMLDecodeError:
            continue
        if set(parsed_block) == {"coordinator"} and "coordinator" not in parsed_rest:
            return (start, end)
    raise ValueError("could not isolate the [coordinator] table")


def write_coordinator_overlay(
    home: Path,
    *,
    mode: str | None = None,
    instructions: str | None = None,
) -> Path:
    """Rewrite only the [coordinator] section of the user overlay, keeping the rest.

    Values not passed here keep whatever the overlay already pins (or stay
    unset so repo defaults keep flowing). Raises on a malformed overlay instead
    of clobbering a file the user hand-edited.
    """
    path = home / "agents.toml"
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    try:
        original = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"{path} is not valid TOML ({exc}); fix it by hand first") from exc

    existing = original.get("coordinator", {}) if isinstance(original.get("coordinator"), dict) else {}
    new_mode = mode if mode is not None else existing.get("mode")
    new_instructions = instructions if instructions is not None else existing.get("instructions")

    lines = ["[coordinator]"]
    written_mode: str | None = None
    if new_mode is not None:
        written_mode = normalize_coordinator_mode(str(new_mode), strict=True)
        lines.append(f'mode = "{written_mode}"')
    if new_instructions is not None:
        lines.append(f"instructions = {_toml_string(str(new_instructions))}")
    block = "\n".join(lines) + "\n"

    try:
        span = _coordinator_span(text)
    except ValueError as exc:
        raise ValueError(f"could not isolate the [coordinator] table in {path}") from exc
    if span:
        start, end = span
        new_text = text[:start] + block + text[end:]
    else:
        new_text = block if not text.strip() else text.rstrip() + "\n\n" + block

    try:
        parsed = tomllib.loads(new_text)
        coord = parsed.get("coordinator")
        if not isinstance(coord, dict):
            raise ValueError("missing coordinator table")
        if written_mode is not None and coord.get("mode") != written_mode:
            raise ValueError("mode mismatch")
        if new_instructions is not None:
            got = (coord.get("instructions") or "").strip()
            expected = str(new_instructions or "").strip()
            if got != expected:
                raise ValueError("instructions mismatch")
        original_other = {key: value for key, value in original.items() if key != "coordinator"}
        parsed_other = {key: value for key, value in parsed.items() if key != "coordinator"}
        if original_other != parsed_other:
            raise ValueError("other tables changed")
    except (tomllib.TOMLDecodeError, ValueError) as exc:
        raise ValueError(
            "refusing to write agents.toml: rewritten [coordinator] block failed self-check"
        ) from exc

    atomic_write_text(path, new_text)
    return path


def _merge_server(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    out.update(overlay)
    return out


def load_config(home: Path | None = None) -> AppConfig:
    bundled_raw = _load_toml(bundled_agents_toml())
    user_home = home or bridge_home()
    overlay_path = user_home / "agents.toml"
    try:
        overlay_raw = _load_toml(overlay_path)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(
            f"{overlay_path} is not valid TOML: {exc}. "
            "Fix or delete the file, then restart the Bridge."
        ) from exc
    bundled = _raw_agents(bundled_raw)
    overlay = _raw_agents(overlay_raw)
    merged: dict[str, dict[str, Any]] = {name: dict(spec) for name, spec in bundled.items()}
    for name, spec in overlay.items():
        merged[name] = _merge_agent_spec(merged.get(name, {}), spec)
    agents = {
        name: AgentConfig.model_validate({**spec, "name": name})
        for name, spec in merged.items()
    }
    if os.environ.get("AGENT_BRIDGE_ENABLE_FAKE") == "1":
        agents["fake"] = AgentConfig(
            name="fake",
            protocol="fake",
            command=["fake"],
            revivable=True,
            idle_unload_sec=0,
        )
    env = EnvConfig.model_validate(_merge_env(_coerce_env(bundled_raw), _coerce_env(overlay_raw)))
    server = ServerConfig.model_validate(
        _merge_server(_coerce_server(bundled_raw), _coerce_server(overlay_raw))
    )
    coord_raw = {**_coerce_coordinator(bundled_raw), **_coerce_coordinator(overlay_raw)}
    # Per-host override: each MCP host entry can set its own mode via env
    # (e.g. Codex manual, Cursor eager) without a second config file.
    env_mode = os.environ.get("AGENT_BRIDGE_MODE")
    if env_mode:
        coord_raw["mode"] = env_mode
    coord_raw["mode"] = normalize_coordinator_mode(coord_raw.get("mode"))
    coordinator = CoordinatorConfig.model_validate(coord_raw)
    return AppConfig(agents=agents, env=env, server=server, coordinator=coordinator)
