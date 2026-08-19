# Codex setup for Agent Bridge

## Register the MCP server

From a trusted project, or edit `%USERPROFILE%\.codex\config.toml`:

```powershell
codex mcp add agent_bridge -- uv --directory "C:\path\to\Agent-Bridge" run agent-bridge
```

Then add the tuning keys. Codex **clears** the MCP child environment, so list every variable workers need:

```toml
[mcp_servers.agent_bridge]
command = "uv"
args = ["--directory", "C:\\path\\to\\Agent-Bridge", "run", "agent-bridge"]
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

## Environment and proxy

This is not an optional extra. Worker CLIs (Grok, DSH, agy) read `HTTPS_PROXY` / API keys from **their** process environment. Two things strip that:

1. Codex env-clears the MCP server.
2. Bridge launches `grok.exe` / `agy` directly, so PowerShell functions that wrap those CLIs never run.

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

`list_agents` returns an `env` object (`proxy`, `proxy_source`, `present`, `missing`, `warnings`). If `env.proxy` is null, Grok will likely fail talking to `cli-chat-proxy.grok.com`.

## Orchestration rules

Copy [AGENTS.md](AGENTS.md) to:

- the target repository root, or
- `%USERPROFILE%\.codex\AGENTS.md` for a global default

Codex reads the English file. [AGENTS.zh-CN.md](AGENTS.zh-CN.md) is a human-readable translation.

Keep it under 32 KiB. Codex concatenates the home file with per-directory `AGENTS.md` files from the git root down to cwd.

## End-to-end drill

1. Open Codex in a throwaway git checkout.
2. From **that same folder**, ask: `用 grok 在当前目录写一个 smoke.txt，内容 hello-bridge，做完后你自己 git diff 验收。` Codex must pass its current project as `dispatch_task.cwd` (not the Agent Bridge install path).
3. Confirm Codex calls `dispatch_task` → loops `wait_task` → `get_result` → inspects the diff. `wait_task` defaults to 180 seconds; a timeout is not failure.
4. Ask it to find a nit and send a follow-up on the same `session_id`.
5. Confirm the second turn does not start a new Grok session.

If a worker is missing, `list_agents` reports `available: false` and Codex should pick another worker or do the work itself.

Codex can pin a worker model and thinking intensity on `dispatch_task`:

```text
dispatch_task(agent="antigravity", model="gemini-3.7-flash", effort="low", ...)
```

`agy models` lists slugs such as `gemini-3.7-flash-low`. Either pass that full slug, or pass the family plus `effort=low|medium|high`. New agy sessions get `--new-project` and `--add-dir <cwd>` so work stays in the requested repo, not `~\.gemini\antigravity-cli\scratch`. Grok accepts a `grok models` slug plus `effort=off|low|medium|high|max` (`off` maps to Grok `none`, `max` to Grok `xhigh`). Grok `/new` still starts on the campaign default (currently grok-4.6 xhigh); Bridge calls `session/setModel` after the session exists. Accept Grok model selection from `get_result.observed_model` (Grok `turn_started.model_id`), not from the worker quoting `You are Grok 4.6`. DSH accepts `model="deepseek-official/deepseek-v4-flash"` and `effort=low|high|max`. Changing DSH model/effort on an existing session respawns the process.

## Permissions

Worker CLIs run in always-approve / skip-permissions mode. Review is Codex's job: `git diff`, build, tests. Cap follow-up turns at three, then Codex patches the rest.
