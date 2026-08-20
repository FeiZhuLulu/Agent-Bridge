# Coordinator setup for Agent Bridge

Codex, Cursor, and Kimi Code can all act as the coordinator. Register the same stdio server in whichever host you use; the orchestration rules file is shared.

## Register the MCP server (Codex)

From a trusted project, or edit `%USERPROFILE%\.codex\config.toml`:

```powershell
codex mcp add agent_bridge -- uv --directory "C:\path\to\Agent-Bridge" run --no-sync agent-bridge
```

Then add the tuning keys. Codex **clears** the MCP child environment, so list every variable workers need:

```toml
[mcp_servers.agent_bridge]
command = "uv"
args = ["--directory", "C:\\path\\to\\Agent-Bridge", "run", "--no-sync", "agent-bridge"]
startup_timeout_sec = 30
tool_timeout_sec = 600
supports_parallel_tool_calls = true
default_tools_approval_mode = "approve"
env_vars = [
  "HTTP_PROXY",
  "HTTPS_PROXY",
  "ALL_PROXY",
  "NO_PROXY",
  "AGENT_BRIDGE_HTTP_PROXY",
  "DSH_HOME",
  "SSL_CERT_FILE",
]
```

`uv` must be on the PATH Codex uses (`%USERPROFILE%\.local\bin` after the standalone installer). If Codex cannot find `uv`, set `command` to the full `uv.exe` path.

DSH does **not** require `DEEPSEEK_API_KEY`. It uses whatever provider the user already configured in DSH (`%USERPROFILE%\.dsh\settings.yaml` and `.credentials.yaml`). Add extra key names to `env_vars` / `[env.inherit]` only if that user's DSH `apiKeyEnv` points at a process environment variable instead of the credentials file.

The product `dsh` CLI is not an ACP server (DeepSeek ships ACP as `@deepseek-ai/dsh-acp-demo`). Any user needs that published package — Bridge does not vendor a checkout path. Discovery order: `DSH_ACP_BIN`, PATH `dsh-acp-demo`, the user's npm global prefix, `$AGENT_BRIDGE_HOME/dsh-acp`, then a *built* `$DSH_HARNESS` checkout.

```powershell
npm install -g @deepseek-ai/dsh-acp-demo
# or, without writing the global prefix:
.\.venv\Scripts\python.exe scripts\install_dsh_acp.py
```

The helper writes `$AGENT_BRIDGE_HOME/dsh-acp` (default `~/.agent-bridge/dsh-acp`) and installs the ACP peers the cordis file imports. Bridge copies that cordis file next to the chosen `node_modules` so ESM can resolve plugins. An unbuilt checkout `src/bin.ts` is ignored unless `tsx` is installed. DSH persistence is `$AGENT_BRIDGE_HOME/dsh-sessions/<session_id>` so a user project does not get `./.sessions`. `get_result.files_changed` is a turn-scoped workspace diff, not only ACP tool_call events (DSH often sends none).

Restart Codex after editing `config.toml`.

Inspect what Bridge reconstructed:

```powershell
uv --directory "C:\path\to\Agent-Bridge" run agent-bridge --env
```

## Register the MCP server (Cursor)

`%USERPROFILE%\.cursor\mcp.json` applies to every project; `<repo>\.cursor\mcp.json` applies to one repo:

```json
{
  "mcpServers": {
    "agent-bridge": {
      "command": "uv",
      "args": ["--directory", "C:/path/to/Agent-Bridge", "run", "--no-sync", "agent-bridge"]
    }
  }
}
```

- Keep `--no-sync`. A plain `uv run` re-syncs the project on every spawn; if any other Bridge instance is alive (for example one held by a Codex session), Windows cannot replace the locked `agent-bridge.exe` and the spawn dies before the MCP handshake. Source edits are live anyway (editable install); run `uv sync --extra dev` yourself after changing dependencies or `pyproject.toml`.
- Cursor usually picks up `mcp.json` edits without a restart, though the docs still recommend restarting after changes. If the server gets stuck in an error state after a failed spawn, rename the server key — a new identity forces a fresh connection; a full Cursor restart also works.
- Cursor forwards more of the desktop environment than Codex, but Bridge rebuilds proxy/env itself either way; `agents.toml [env]` still applies.
- Cursor's MCP tool timeout is around one minute (not configurable like Codex `tool_timeout_sec`). From Cursor, call `wait_task` with `timeout_sec` ≈ 45 and loop; the default 180 gets killed by the host first.

## Register the MCP server (Kimi Code)

`%USERPROFILE%\.kimi-code\mcp.json` applies to every project; `<repo>\.kimi-code\mcp.json` applies to one repo:

```json
{
  "mcpServers": {
    "agent-bridge": {
      "command": "uv",
      "args": ["--directory", "C:/path/to/Agent-Bridge", "run", "--no-sync", "agent-bridge"],
      "toolTimeoutMs": 600000,
      "startupTimeoutMs": 60000
    }
  }
}
```

- Keep `--no-sync`, for the same reason as Cursor.
- Kimi Code's default single-tool-call timeout is 60 s, so a default `wait_task(timeout_sec=180)` gets killed by the host. `toolTimeoutMs` is the per-server override; `[mcp] tool_timeout_ms` in `config.toml` or `KIMI_MCP_TOOL_TIMEOUT_MS` moves the global default. With the value above, `wait_task` behaves like it does under Codex; without it, pass ≈ 45 and loop.
- Kimi asks for approval per MCP tool call unless the run is in YOLO mode. To pre-approve Bridge only, add to `%USERPROFILE%\.kimi-code\config.toml`:

```toml
[[permission.rules]]
decision = "allow"
pattern = "mcp__agent-bridge__*"
```

- A project-level `.kimi-code/mcp.json` only activates after you trust the folder at the workspace trust prompt.
- Servers added mid-session do not join open sessions. Restart `kimi` after editing `mcp.json`.

## Environment and proxy

Worker CLIs (Grok, Kimi, DSH, agy) read API keys — and, on machines that need one, `HTTPS_PROXY` — from **their** process environment. Two things strip that:

1. Codex env-clears the MCP server.
2. Bridge launches `grok.exe` / `kimi` / `agy` directly, so PowerShell functions that wrap those CLIs never run.

Bridge rebuilds the environment **once at startup** (and again for each worker spawn) in this order, later wins:

| Layer | Source |
| --- | --- |
| Discovery | PowerShell `grok` wrapper, Windows system proxy (`discover_proxy`) |
| Inherit | Windows user then machine env, keys in `[env.inherit]` |
| Process | Whatever Codex still forwarded |
| `[env.proxy]` | `url` / `no_proxy` in `agents.toml` |
| `[env.set]` | Explicit key/value map |
| `[agents.<name>.env]` | Per-worker overlay |

Do **not** put secrets in the repo `agents.toml`. Pin machine-local values in `%USERPROFILE%\.agent-bridge\agents.toml` (see [agents.toml.example](agents.toml.example)):

```toml
[env.proxy]
url = "http://127.0.0.1:7897"
no_proxy = "localhost,127.0.0.1,::1"
```

`list_agents` returns an `env` object (`proxy`, `proxy_source`, `present`, `missing`, `warnings`). A null `env.proxy` is normal on a direct network. Behind a firewall, configure `[env.proxy]`; Grok talking to `cli-chat-proxy.grok.com` is usually the first casualty.

## Multiple coordinators on one checkout

Running Codex, Cursor, and Kimi Code coordinators at the same time is supported: each host spawns its own Bridge process, and `list_agents` merely counts the siblings. What must not run twice is the **installer**. A plain `uv run` syncs the project before executing, and that sync rewrites `.venv\Scripts\agent-bridge.exe` — on Windows a file every running instance holds open. The second host's spawn then dies before the MCP handshake with:

```text
error: failed to remove file `...\.venv\Lib\site-packages\../../Scripts/agent-bridge.exe` (os error 32)
```

That is why every host entry above says `run --no-sync`. Two launch styles avoid the lock:

- `uv --directory C:/path/to/Agent-Bridge run --no-sync agent-bridge` — portable path, but remember to run `uv sync --extra dev` yourself after cloning or changing dependencies, while no instance is running.
- `C:/path/to/Agent-Bridge/.venv/Scripts/python.exe -m agent_bridge` — no `uv` at spawn time at all, so it can never touch the lock; the path is machine-specific.

POSIX hosts can replace a running binary, so this failure is Windows-only.

Instances share the `~/.agent-bridge` state directory but not sessions: every session and task record carries the identity of the Bridge instance that owns it. `list_sessions` shows only the calling instance's records, saves leave a live sibling's records untouched on disk, and records whose owning instance has exited are adopted at the next boot — their in-flight tasks surface as `failed` / `bridge_restarted`. A session started from one host is continued from that host; it does not appear in another host's `list_sessions` while its owner is alive.

## Server lifecycle

Abandoned server instances self-exit: after `server.idle_exit_sec` (default 7200 s) with no MCP requests and no queued or running tasks, the process shuts its workers down and exits. Configure in `[server]` (repo `agents.toml` or `%USERPROFILE%\.agent-bridge\agents.toml`); `idle_exit_sec = 0` disables it. `list_agents` also warns when other Bridge instances are running on this machine — one per coordinator host is normal, a pile-up means a host keeps abandoning spawns.

## Orchestration rules

Copy [AGENTS.md](AGENTS.md) to:

- the target repository root (Codex, Cursor, and Kimi Code all auto-apply it there), or
- `%USERPROFILE%\.codex\AGENTS.md` for a Codex-global default; `%USERPROFILE%\.kimi-code\AGENTS.md` for a Kimi-global default; the Cursor-global equivalent is User Rules in Cursor settings

Codex reads the English file. [AGENTS.zh-CN.md](AGENTS.zh-CN.md) is a human-readable translation.

Keep it under 32 KiB. Codex concatenates the home file with per-directory `AGENTS.md` files from the git root down to cwd; Kimi Code does the same from the git root down and warns past 32 KiB.

## End-to-end drill

1. Open the coordinator (Codex, Cursor, or Kimi Code) in a throwaway git checkout.
2. From **that same folder**, ask: `用 grok 在当前目录写一个 smoke.txt，内容 hello-bridge，做完后你自己 git diff 验收。` The coordinator must pass its current project as `dispatch_task.cwd` (not the Agent Bridge install path).
3. Confirm it calls `dispatch_task` → loops `wait_task` → `get_result` → inspects the diff. `wait_task` defaults to 180 seconds; a timeout is not failure.
4. Ask it to find a nit and send a follow-up on the same `session_id`.
5. Confirm the second turn does not start a new Grok session.

If a worker is missing, `list_agents` reports `available: false` and the coordinator should pick another worker or do the work itself.

The coordinator can pin a worker model and thinking intensity on `dispatch_task`:

```text
dispatch_task(agent="antigravity", model="gemini-3.7-flash", effort="low", ...)
```

`agy models` lists slugs such as `gemini-3.7-flash-low`. Either pass that full slug, or pass the family plus `effort=low|medium|high`. New agy sessions get `--new-project` and `--add-dir <cwd>` so work stays in the requested repo, not `~\.gemini\antigravity-cli\scratch`. Grok accepts a `grok models` slug plus `effort=off|low|medium|high|max` (`off` maps to Grok `none`, `max` to Grok `xhigh`). Grok `/new` still starts on the campaign default (currently grok-4.6 xhigh); Bridge calls `session/setModel` after the session exists. Accept Grok model selection from `get_result.observed_model` (Grok `turn_started.model_id`), not from the worker quoting `You are Grok 4.6`. Kimi accepts one of the slugs its own session advertises (`kimi-code/k3`, `kimi-code/k3-256k`, `kimi-code/kimi-for-coding`, ...) plus `effort=off|low|medium|high|max`. DSH accepts `model="deepseek-official/deepseek-v4-flash"` and `effort=low|high|max`. Changing DSH model/effort on an existing session respawns the process.

## Worker: Kimi Code

`kimi acp` is a first-class ACP server, so Bridge drives it exactly like Grok — no extra shim to install:

```powershell
npm install -g @moonshot-ai/kimi-code
kimi login
```

`kimi login` is not optional. Kimi's ACP host gates every `session/new` on auth and answers `auth_required` when no token is on disk; `list_agents` cannot see that, so it will still report `available: true`. On Windows Kimi's Bash tool needs Git Bash — set `KIMI_SHELL_PATH` if it is not in a standard location, and Bridge will pass it through.

Two behaviors worth knowing before you read a Kimi result:

- **Thinking levels are declared per model, not global.** Bridge maps `effort` onto whatever the selected model advertises. `kimi-code/k3-256k` only advertises `low` / `high` / `max`, so `medium` lands on `high` and `off` on `low`; a boolean model gets `off` / `on` instead. When nothing maps, `get_result.warnings` says so and the turn still runs on the model's own default.
- **A failed Kimi turn does not look failed.** Its ACP host maps a failed turn to `stopReason: end_turn` with empty text, so a quota or provider error is indistinguishable on the wire from a clean no-op. Bridge reads Kimi's `wire.jsonl` after every turn and puts the real reason in `get_result.warnings`, alongside `observed_model` / `observed_effort`. Never read an empty Kimi turn as success without checking there.

Bridge also forces the session into Kimi's `yolo` mode after creating it (Kimi starts in manual-approval `default` mode), and revives sessions with `session/resume` rather than `session/load`, because `load` replays the entire persisted history before it answers.

## Permissions

Worker CLIs run in always-approve / skip-permissions mode. Review is the coordinator's job: `git diff`, build, tests. Cap follow-up turns at three, then the coordinator patches the rest.
