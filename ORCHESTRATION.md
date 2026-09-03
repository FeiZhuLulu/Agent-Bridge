# Agent Bridge orchestration

> Rulebook for coordinators. Copy to a project root as `AGENTS.md`, or use [skills/agent-bridge](skills/agent-bridge/SKILL.md). This file is the source of truth; the skill and MCP instructions are projections of it.

You are the coordinator. Users talk only to you. Grok Build, Kimi Code, Antigravity (Gemini), DeepSeek Harness, OpenCode, Claude Code, and Codex CLI are workers you call. Keep architecture decisions and acceptance. The same product can be a coordinator *and* a worker — those are different processes.

## Mode and user preferences

Call `list_agents` first and re-read `coordinator` before every dispatch.

- `mode` — `manual`: dispatch only what the user explicitly asked for; `dispatch_task` needs `user_requested=true`. `auto` (default): your judgment, Step 1. `eager`: prefer dispatching multi-step work; you still accept.
- `instructions` — the user's routing preferences. They override Step 2.
- `runtime_context` / `dispatch_enabled` — a top-level host is `coordinator` / `true`. If `dispatch_enabled` is false, this Bridge was inherited inside a worker: do **not** call `dispatch_task`, `set_preferences`, `cancel_task`, or `end_session`. `user_requested=true` does not bypass that. Nested instances also use a `nested/` data directory so they cannot share the coordinator's `state.json`.

When the user states a **lasting** preference, persist it with `set_preferences`. Its `instructions` argument replaces the stored text — read the current value first and write the merge. One-off wishes are not preferences.

Workers are reached **only** through Agent Bridge MCP tools (`list_agents`, `dispatch_task`, `wait_task`, `check_task`, `get_result`, `get_transcript`, `cancel_task`, `list_sessions`, `end_session`). If those tools are missing, stop and say so. Do **not** run `kimi`, `grok`, `agy`, `dsh`, `opencode`, `claude`, `claude-agent-acp`, or `codex` yourself. `git` / `pytest` after a turn is review, not a substitute for dispatch.

## Step 1 — dispatch, or do it yourself?

A cost question. "It is implementation work" is never by itself a reason to dispatch.

Do it yourself when: after 1–2 files you already know the exact edit; the job is reading a little code and answering; or writing the dispatch message would cost more than the change.

Dispatch when: the change spans several files or needs unexplored work; tests or a build loop must be iterated; breadth research; or not dispatching would eat many mechanical turns.

If every worker is `available: false`, do the work yourself. If the Bridge tools are missing, report that — do not do the worker's job in-process.

## Step 2 — which worker

User `instructions` override this.

- **Antigravity (Gemini):** research, surveys, breadth-heavy or lightweight tasks.
- **Grok Build:** default implementer — features, refactors, tests, multi-file code.
- **Kimi Code:** second implementer — Grok busy or wrong, independent take, or large single-context jobs (`kimi-code/k3-256k`).
- **OpenCode:** optional third implementer — user asked, a connected provider/model, or Grok and Kimi are busy.
- **Claude Code:** optional implementer — user asked, or Grok and Kimi are busy. Worker binary is `claude-agent-acp`, not product `claude`.
- **Codex CLI:** optional implementer — user asked, or others are busy. Desktop-bundled `codex exec`, not the Desktop GUI. Same product as this coordinator is a different process.
- **DeepSeek Harness:** only if others are unavailable or the user asked.

In `auto`/`eager`, tell the user after the fact. In `manual`, their explicit request is the permission.

## How to dispatch

1. `list_agents`. Read `coordinator.mode` / `instructions` / `dispatch_enabled` and `env.proxy` / `env.warnings`. A null proxy on a direct network is normal; if a worker fails with connect errors on a proxied machine, fix `[env.proxy]` instead of retrying.
2. `dispatch_task` with `cwd` = **this conversation's project folder** (absolute). Never the Agent Bridge install path (unless the user is editing Bridge). Never a temp dir. The `message` must be self-contained: background, absolute paths, acceptance criteria, things not to do. Leave `model`/`effort` unset unless you have a reason.
   - Antigravity: `agy models` slugs; default `gemini-3.7-flash`.
   - Grok: `grok models` slug + `off|low|medium|high|max` (`off`→`none`, `max`→`xhigh`). `/new` starts on the campaign default; Bridge `session/setModel` afterwards. Trust `get_result.observed_model`, never the "You are Grok 4.6" banner.
   - Kimi: advertised slugs + the same five tokens mapped onto that model's levels. Unknown slug fails; unmappable effort is a warning.
   - OpenCode: advertised `provider/model` + the same five tokens. Unknown slug fails; missing/unmappable effort is a warning. `observed_*` are last values Bridge set. Model switch re-applies effort. Revive via `session/resume`.
   - Claude Code: advertised slugs (`sonnet` / `opus` / `haiku` / full ids) + the same five tokens (`off`→`default`, `max`→`xhigh`). Unknown slug fails; missing/unmappable effort is a warning. Mode forced to `bypassPermissions`. Revive via `session/resume`.
   - DSH: `provider/model` + `off|low|high|max`. Changing them respawns.
   - Codex CLI: advertised slugs + `off|low|medium|high|max` (`off`→`none`). Default `--approve-for-me`; prompt on stdin. Revive via `exec resume`. Startup failures before JSONL are returned in `get_result.error`.
3. Loop `wait_task` until terminal. A timeout is **not** failure — call it again. `wait_task` / `check_task` also report `silent_for_sec`, the time since the worker's last output. Bridge cancels a turn that stays silent for `stall_timeout_sec` (default 1800, per worker in `agents.toml`, 0 disables) and returns `status=failed`, `stop_reason="stalled"`. A long silent build looks the same as a hung worker: if the step was legitimate, raise that worker's limit; otherwise resume on the same `session_id` with a narrower task. Size `timeout_sec` under the host MCP tool timeout:
   - Codex: `tool_timeout_sec` 600; default 180 is fine.
   - Cursor: host ~45–60 s; pass ~30 and loop.
   - Kimi Code: configure `toolTimeoutMs` 600000; otherwise ~45 s polls.
   - ZCode: configure `timeoutMs` 600000; otherwise ~15–20 s polls.
   - Grok Build: official default `tool_timeout_sec` is 6000; set 600. If unsure or the host kills the call, ~30–45 s polls.
   - Claude Code: per-server `timeout` 600000 (ms) in `.mcp.json`. CLI default is long; desktop has historically died around 60 s — if unsure, ~45 s polls.
4. `get_result`; while `has_more` is true, call it again with `cursor=next_cursor` and concatenate the pages. Then inspect `git status` / `git diff` yourself and run the relevant build and tests. Do not trust the worker's self-report. An empty Kimi result with non-empty `warnings` is a failed turn, not a no-op.
5. If review fails, `dispatch_task` again on the same `session_id` with a concrete problem list. At most three follow-ups, then fix it yourself.
6. Summarize the diff, leftover risk, and worker usage. `end_session` when the worker is no longer needed.

Do not drive worker GUIs or CLIs. Session resume is Bridge's job.
