---
name: agent-bridge
description: Coordinate local worker agents (Grok Build, Kimi Code, Antigravity, DeepSeek Harness, OpenCode, Claude Code, Codex CLI, Devin CLI) through the Agent Bridge MCP tools. Use when dispatching implementation, research, or test work to a worker; when the user mentions Agent Bridge, dispatch_task, or a worker by name; or when deciding whether to delegate a coding task instead of doing it yourself.
---

# Dispatching workers through Agent Bridge

You are the coordinator: users talk only to you, and you call the workers. Workers are reached **only** through the Agent Bridge MCP tools (`list_agents`, `set_preferences`, `dispatch_task`, `wait_task`, `check_task`, `get_result`, `get_transcript`, `cancel_task`, `list_sessions`, `end_session`). If those tools are missing from this session, stop and say so — never run `grok`, `kimi`, `agy`, `dsh`, `opencode`, `claude`, `claude-agent-acp`, `codex`, or `devin` CLIs directly, and never drive their GUIs. The same product can be a coordinator and a worker; those are different processes.

## Before anything: read the policy

Call `list_agents` first. Its result carries:

- `coordinator.mode` — `manual`: dispatch only what the user explicitly asked for (`dispatch_task` requires `user_requested=true`); `auto`: your judgment via Step 1; `eager`: prefer dispatching multi-step work.
- `coordinator.instructions` — the user's persistent routing preferences. They override Step 2 below. When the user states a lasting preference in chat, persist it with `set_preferences` (its `instructions` argument replaces the stored text — read the current value first and write the merged result; effective immediately in this instance, elsewhere at next start). One-off wishes are not preferences; follow them without persisting.
- `coordinator.runtime_context` / `coordinator.dispatch_enabled` — a top-level host is `coordinator` / `true`. If `dispatch_enabled` is false, this Bridge was inherited inside a worker: stop dispatching immediately. Do not call `dispatch_task`, `set_preferences`, `cancel_task`, or `end_session`. `user_requested=true` does not bypass that. Nested instances write under a `nested/` data directory so they cannot share the coordinator's `state.json`.
- `env.proxy` / `env.warnings` — a null proxy on a direct network is normal; on a machine that needs one, fix `[env.proxy]` in `agents.toml` instead of retrying a failed dispatch.

## Step 1 — dispatch, or do it yourself?

A cost question, not a category question; "it is implementation work" is never by itself a reason to dispatch.

Do it yourself when: after reading 1–2 files you already know the exact edit; the whole job is reading a little code and answering; or writing the dispatch message would cost more than making the change.

Dispatch when: the change spans several files or needs exploration you have not done; tests must be written or a build/test loop iterated; breadth research across many sources; or not dispatching would eat many turns of mechanical work.

Examples: "fix the README typo" → yourself. "Add a None check at line 120" → yourself, even though it is implementation. "Add retry logic to the ACP adapter with tests" → Grok. "Port this 4000-line module" → Kimi (`kimi-code/k3-256k`). "Survey how other CLIs handle session resume" → Antigravity.

## Step 2 — which worker (user instructions override this)

- **Antigravity (Gemini):** research, surveys, breadth-heavy or lightweight tasks.
- **Grok Build:** default implementer — features, refactors, tests, multi-file code.
- **Kimi Code:** second implementer — Grok busy or wrong, independent second take, or big single-context jobs.
- **OpenCode:** optional third implementer — user asked for it, wants a connected provider/model, or Grok and Kimi are busy.
- **Claude Code:** optional implementer — user asked, or Grok and Kimi are busy. Worker binary is `claude-agent-acp`.
- **Codex CLI:** optional implementer — user asked, or others are busy. Desktop-bundled `codex exec`, not the Desktop GUI; startup failures before JSONL are returned in `get_result.error`.
- **Devin CLI:** optional implementer — user asked, or others are busy. `devin acp`, not Devin Desktop; model ids carry the level (`swe-1-7-medium`), `effort` is ignored with a warning.
- **DeepSeek Harness:** only when others are unavailable or the user asks.

## The dispatch loop

1. `dispatch_task` with `cwd` = **this conversation's project folder** (absolute) — never the Agent Bridge install path, never a temp dir. The `message` must be self-contained: background, absolute paths, acceptance criteria, things not to do. Leave `model`/`effort` unset unless you have a reason; slugs and effort mappings per worker are documented in ORCHESTRATION.md in the Agent-Bridge repo.
2. Loop `wait_task` until terminal. A timeout is **not** failure — call it again. `silent_for_sec` in the payload is the time since the worker's last output; a turn silent past `stall_timeout_sec` (default 1800) ends `failed` / `stop_reason="stalled"` — raise that worker's limit in `agents.toml` if the step was legitimately long. Size `timeout_sec` under the host MCP tool timeout:
   - Codex: `tool_timeout_sec` 600; default 180 is fine.
   - Cursor: host ~45–60 s; pass ~30 and loop.
   - Kimi Code: configure `toolTimeoutMs` 600000; otherwise ~45 s polls.
   - ZCode: configure `timeoutMs` 600000; otherwise ~15–20 s polls.
   - Grok Build: official default `tool_timeout_sec` is 6000; set 600. If unsure or the host kills the call, ~30–45 s polls.
   - Claude Code: per-server `timeout` 600000 (ms) in `.mcp.json`. CLI default is long; if unsure, ~45 s polls.
3. `get_result`; while `has_more` is true, call it again with `cursor=next_cursor` and concatenate the pages. Then verify yourself: `git status` / `git diff`, run the relevant build and tests. Never trust the worker's self-report. Grok's real model is `observed_model` (its "I am Grok X" banner is baked at `/new` and does not track model switches). OpenCode and Claude Code `observed_model` / `observed_effort` are the last values Bridge set after mapping, not a live sampler. An empty Kimi result with non-empty `warnings` is a failed turn, not a no-op.
4. If review fails, `dispatch_task` again on the same `session_id` with a concrete problem list — at most three follow-ups, then fix it yourself and tell the user.
5. Summarize the diff, leftover risk, and worker usage. `end_session` when the worker is no longer needed.

Optional UUID `request_id` on `dispatch_task` deduplicates identical retries (`reused=true`) only within the same Bridge instance while the task is retained. Different arguments are rejected, and normal dispatch validation still applies. Restart, another instance, or task pruning loses the binding; this does not guarantee exactly-once worker side effects.
