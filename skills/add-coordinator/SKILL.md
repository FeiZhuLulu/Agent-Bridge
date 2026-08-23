---
name: add-coordinator
description: Verify that a new host can act as an Agent Bridge coordinator over stdio MCP. Use when adding a coordinator host, checking MCP/skill/timeout/permission setup for Codex, Cursor, Kimi Code, ZCode, or Grok Build, or when a host can also run as a worker and nested dispatch must stay disabled.
---

# Adding a coordinator host to Agent Bridge

This is **not** [add-worker](../add-worker/SKILL.md).

- **add-worker** connects a new *execution* CLI to Registry / adapters / `agents.toml`.
- **add-coordinator** proves a new *host* can drive the existing workers through the same MCP tools.

Do not add an adapter, a worker block, or a new dispatch protocol for a coordinator. Do not use GUI automation. Do not treat "the config file was written" as an end-to-end pass.

## Before writing anything

Answer every item below from the host's current official docs and, when possible, its source. Record units (seconds vs milliseconds) and scope (user vs project).

1. **stdio MCP config shape, scopes, and precedence.** User file vs project file. Whether a native config and a `.agents/mcp.json` fallback merge or replace each other. Whether the settings UI paste format differs from the on-disk file.
2. **Whether MCP server `instructions` reach the model.** Handshake text is one channel; if the host hides it, the skill and `ORCHESTRATION.md` must still be discoverable.
3. **AGENTS.md / rules loading and size limits.** Path, walk order, concatenation, and any byte/token cap.
4. **Skill directories.** Native user dir, project dir, generic `~/.agents/skills`, plugin/marketplace paths, and whether discovery is automatic.
5. **MCP startup timeout and tool-call timeout, with units.** Recommend a value that leaves room for `wait_task` default 180 s (Bridge recommends 600 s / 600000 ms). Document the host default and the short-poll fallback when the default is too short or unknown.
6. **MCP tool permission / approval.** How to pre-allow only Agent Bridge tools without turning the whole host into always-approve. Note which keys are user-scoped and must not go in a project example.
7. **Environment inheritance and executable PATH.** Does the host clear the MCP child env (Codex does)? Can it resolve a PowerShell function, or must `command` be a real executable absolute path?
8. **Can this product also be a Worker?** If yes, a Worker that inherits the user's Agent Bridge MCP config will start a nested Bridge. Prove the server-side gate: nested `list_agents` shows `runtime_context=worker` and `dispatch_enabled=false`; nested `dispatch_task` (even `user_requested=true`) returns `nested dispatch is disabled`.
9. **Real closed loop on the live host:** `list_agents` → `dispatch_task` → `wait_task` → `get_result` → same `session_id` continue → `end_session`. Verify the file on disk yourself. A timeout is not failure.
10. **Separate automation from human steps.** Unit tests and a project-level config fixture are automation. Opening a GUI host and sending one prompt is human. Never call the latter "E2E passed".

## Current hosts (verified against their public docs)

| Host | Native MCP file | Skill discovery | Tool timeout field | Notes |
| --- | --- | --- | --- | --- |
| Codex | `~/.codex/config.toml` `[mcp_servers.*]` | `~/.codex/skills` | `tool_timeout_sec` (seconds) | Clears MCP child env; list `env_vars`. |
| Cursor | `~/.cursor/mcp.json` or `<repo>/.cursor/mcp.json` | `~/.cursor/skills` | ~45–60 s, not configurable | Short-poll `wait_task`. |
| Kimi Code | `~/.kimi-code/mcp.json` | `~/.kimi-code/skills` | `toolTimeoutMs` (ms) | Default 60 s without override. |
| ZCode | user `~/.zcode/cli/config.json` (`mcp.servers`); project `<repo>/.zcode/config.json` | `~/.zcode/skills` | `timeoutMs` (ms) | UI "完整配置" accepts `{name:{...}}` or `{mcpServers:{...}}`. That is **not** the native file shape. Same-scope `.zcode` MCP skips `.agents/mcp.json` entirely. |
| Grok Build | user `~/.grok/config.toml`; project `<repo>/.grok/config.toml` | `~/.grok/skills`, `./.grok/skills`, and `~/.agents/skills` | `startup_timeout_sec` / `tool_timeout_sec` (seconds; tool default 6000) | Also a Worker. MCP stdio inherits the process env (no `env_clear`). Official permission form is `[[permission.rules]]` with `tool = "mcp"` and `{server}__{tool}` names. `[ui] permission_mode` is user-scoped only. |

Bridge already installs the coordinator skill into `.agents`, `.cursor`, `.codex`, `.kimi-code`, and `.zcode`. Grok reads `~/.agents/skills`; do not add a `.grok` copy unless a future host stops reading `.agents`.

## Implementation checklist (coordinator only)

1. Keep the MCP tool names and arguments unchanged.
2. Document the host in `SETUP.md` with a copy-paste config that uses a **resolved** `agent-bridge` absolute path (`Get-Command agent-bridge | Select-Object -ExpandProperty Source`). Do not invent a home directory.
3. Update every user-facing coordinator list (README, SETUP, ORCHESTRATION, skill, CLI HELP, package description) so it cannot still say "Codex / Cursor / Kimi only".
4. If the host can also be a Worker, add or keep the nested-dispatch tests; do not rely on the model "being careful".
5. If `ORCHESTRATION.md` is copied as `AGENTS.md` (Grok does this), keep the English file ≤ 9500 characters. Chinese can stay longer; behavior must match.

## Definition of done

Automation:

- Unit tests cover worker-context refusal (`dispatch_task` / `set_preferences` / `cancel_task` / `end_session`), nested `AGENT_BRIDGE_HOME`, skill destinations, and docs that distinguish config shapes.
- `uv run pytest` and `uv build` pass.

Human or live-host:

- A local `lab/` workspace from `scripts/setup_lab.py` (its own git repo, project-scoped host config; not committed). Do not edit the user's global Codex/Cursor/Kimi/ZCode/Grok files for the test, and do not use `tests/` or a Temp folder.
- `list_agents` shows `dispatch_enabled=true` at the top-level host.
- Two worker turns on one `session_id`; the file on disk has both lines.
- If the host is also a Worker: nested `dispatch_task` is rejected with `nested dispatch is disabled`; nested `cancel_task` / `end_session` are also rejected; nested state lives under `AGENT_BRIDGE_HOME/nested`. Top-level `end_session` still tears down the worker tree and its nested Bridge. Top-level `env_status` does not count that nested Bridge as a sibling.
- If the host has no stable headless CLI, say "config and unit tests done; UI E2E waiting on one human prompt". Do not claim full verification.

## Common failures

- **Treating a coordinator as a new worker.** No adapter, no `agents.toml` worker block.
- **Mixing UI JSON with native file JSON** (ZCode).
- **Mixing seconds and milliseconds.**
- **Putting user-only keys in a project example** (Grok `[ui] permission_mode`).
- **Claiming `.agents/mcp.json` always merges** with a native `.zcode` file. It does not.
- **Calling `grok` / `kimi` from the coordinator shell** because MCP tools "looked missing".
- **Using `user_requested=true` to bypass a nested Bridge.**
- **Trusting the worker's self-report** instead of `git diff`.
