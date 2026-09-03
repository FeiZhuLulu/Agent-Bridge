---
name: add-worker
description: Connect a new worker agent CLI to Agent Bridge. Use when the user wants to add a custom, unsupported, or in-house agent as an Agent Bridge worker; register a worker in agents.toml; write or debug an Agent Bridge adapter; or asks why a newly added worker does not appear in list_agents, ignores model/effort, or starts a fresh session on every turn.
---

# Adding a worker to Agent Bridge

Agent Bridge dispatches work to local agent CLIs. This skill covers connecting a new one.

Two tiers. Establish which one you are in **before** writing anything, because tier 1 needs no code at all and is where most agents land.

## Tier 0 — is there a programmatic interface?

Check, in order:

1. `<cli> --help` for an `acp`, `agent`, `stdio`, or `serve` subcommand.
2. The agent's source or package manifest for an `agent-client-protocol` dependency.
3. A non-interactive "print" mode that streams JSON and can resume a conversation by id.

If none of these exist, stop and tell the user. Agent Bridge integrates through real interfaces; GUI automation and computer use are not acceptable substitutes.

## Tier 1 — config only, no code

**A plain ACP server needs no source changes and no fork.** Add a block to the user overlay at `~/.agent-bridge/agents.toml` (`%USERPROFILE%\.agent-bridge\agents.toml` on Windows):

```toml
[agents.mycustom]
protocol = "acp"
command = ["mycustom-cli", "acp", "--yolo"]
revivable = true
idle_unload_sec = 900
```

Fields (all optional except `protocol` and `command`): `fallback_commands` (list of alternative argv lists), `cwd`, `env` (per-worker overlay), `session_meta` (extra params for `session/new`), `revivable`, `idle_unload_sec`, `stall_timeout_sec`, `print_timeout`.

Verify end to end before doing anything else:

```
list_agents        # the worker appears with available: true
dispatch_task      # agent="mycustom", cwd = an absolute scratch folder
wait_task          # loop until terminal
get_result         # text came back
```

Tier 1 gets you: spawn and handshake, `session/new`, prompting, streaming text, tool-call and workspace-diff file tracking, cancel, idle unload, transcripts, and `session/load` revive when `revivable = true`. Auto-approval works if the agent uses ACP `requestPermission` (Bridge picks allow-always) or accepts a yolo flag you put in `command`.

**Go to tier 2 only if one of these is true:**

| Symptom | Why |
| --- | --- |
| `model` / `effort` come back as an ignored-parameter warning | Selection is per-agent; needs a sync path |
| Revive is slow or times out | The agent replays history on `session/load` and needs `session/resume` |
| Every turn asks for tool approval | The agent needs an explicit mode switch after `session/new` |
| The binary is not on PATH under a stable name | Needs a discovery routine |
| A failed turn is indistinguishable from a clean no-op | Needs an observer and a `get_result` hint |

## Tier 2 — five-axis survey

Tier 2 edits Bridge's source, so it needs a git checkout of Agent Bridge. A `uv tool` install has no source tree to edit — clone the repo first, and expect to open a pull request rather than patching an installed package.

This is the only part that is genuinely different for every agent, and the only part you cannot template. Answer all five before editing source. Read the target agent's own source for its ACP handler and config-option construction; do not rely on blog posts or memory.

**Axis 1 — launch and discovery.** What argv runs headless? Is the product CLI actually the ACP server, or is the server a separate package? (One existing worker ships ACP as a different npm package than the product CLI, which is why it has a dedicated multi-step discovery routine.) Does it need a runtime like `node` to launch a script?

**Axis 2 — auth.** Product-level login, per-provider API keys, an external config file, or nothing? The decisive question is **where an unauthenticated agent fails**. If it only fails on the first prompt, the probe cannot see it: report auth state in the probe `detail` string, but never set `available: false` from it. A probe answers "is the command present", not "will a turn succeed". Any new environment variable must be added to *both* the bundled `agents.toml` `[env] inherit` list and `DEFAULT_INHERIT_KEYS` in `config.py`.

**Axis 3 — session revival.** Does it advertise `session/load`, `session/resume`, or neither? Does `session/load` replay the whole history as `session/update` notifications before it answers? If so it can outlast the handshake timeout on a long session — use resume. If revival is unsupported, set `revivable = false`.

**Axis 4 — model and effort.** Four sub-questions, each one changes code:

1. *Where is it set?* `session/new` `_meta`, process argv, `session/set_config_option` after the session exists, or only by respawning the process.
2. *Does it stick?* Verify on the real CLI. At least one existing worker accepts a model hint at session creation and then silently lands on its campaign default anyway, so Bridge has to re-send it afterwards. Documentation will not tell you this; only a real turn will.
3. *Is the effort vocabulary global or per model?* Per model is the common case. A static mapping table is then wrong on some models and wrong **silently**. Write an ordered preference list per Bridge effort level and pick the first value the live session actually advertises in its `configOptions`.
4. *Does switching model reset effort?* Usually yes. Clear the cached applied-effort whenever the applied model changes, or the next sync sees "already high", skips the call, and the turn quietly runs on the new model's default.

Two rules on failure behavior: an effort level that cannot be mapped is **a warning, not a turn failure** — the mismatch is Bridge's mapping problem and the turn should still run on the model's default. A model the coordinator named explicitly that the session rejects **must fail the turn**, with the real available options in the error; running silently on a different model means the coordinator reviews the work on a false premise.

**Axis 5 — permissions and observability.** Auto-approval via ACP `requestPermission`, an explicit mode switch, or a CLI flag? Is there an on-disk log that reveals which model *actually* ran, or can you only report what Bridge last set? And critically: **what does a failed turn look like on the wire?** One existing worker maps failures to `stopReason: end_turn` with empty text — identical to a clean no-op. Provoke a real failure (revoke the key, kill the network) and look. Anything a coordinator could misread belongs in the `get_result` hint for that agent.

## Tier 2 — build order

The order matters; do not reorder steps 3 and 7.

1. **Declare** the agent in every bundled `agents.toml` copy the repo ships, plus the example file. Keep them identical — otherwise you test one config and users get another.
2. **Probe.** Add a branch in `probe_agent` that puts model syntax, effort vocabulary, auth method, and home path into `detail`. Do not gate `available` on auth.
3. **Prove the protocol against the fake agent first.** The repo ships a minimal in-process ACP echo agent used by tests. Get spawn, initialize, session creation, prompt, and a second turn passing there **before touching the real CLI**. Reversed, handshake timeouts, auth failures, and mapping bugs all look the same.
4. **Selection module.** For a per-model vocabulary, add `<agent>_meta.py`: a preference table plus a `resolve_<agent>_effort(effort, offered)`. Read `configOptions` through the shared helper rather than parsing it again — ACP allows both a flat `[{value, name}]` list and grouped `[{group, name, options}]`, and mis-parsing the grouped form reads as "this model offers nothing".
5. **Adapter wiring.** In the ACP adapter: the resume-agents set, the config-option-agents set, the model/effort-agents set, any spawn-time command or env branch, `session/new` meta, and a `_sync_<agent>_selection` coroutine. Model the sync coroutine on an existing one: compare against the advertised `currentValue` first and skip the RPC when it already matches, remember the applied model in a way that invalidates stale effort, then sync effort.
6. **Registry wiring.** Decide where `observed_model` / `observed_effort` come from — an on-disk observer, or the values the adapter last applied. Add a `get_result` hint for any failure mode a coordinator would misread.
7. **Smoke script against the real CLI.** Five fixed parts: probe and print version/detail; turn one writes a file, then **check the file on disk** rather than believing the worker; turn two continues the same `session_id` and asserts the native session id did not change; turn three passes a bogus model and **must fail**; then end the session.
8. **MCP end to end** from a real coordinator, across two turns. This catches what unit tests and the smoke script cannot: host tool timeouts, a `cwd` accidentally pointing at the Bridge install directory, and concurrency. Some hosts kill MCP tool calls after about a minute — call `wait_task` with a smaller `timeout_sec` and loop.
9. **Docs.** Update only what would otherwise mislead users: the worker section of the setup guide, the orchestration rules the coordinator reads, the coordinator skill's worker list, and the README worker list.

## Definition of done

- Unit tests pass, including the new agent's adapter and selection tests.
- The smoke script exits 0 against the real CLI.
- Two turns from a real coordinator, and the second does **not** create a new session.
- `get_result.observed_model` matches what was requested.
- A nonexistent model fails the turn and the error names the real options.
- An unmappable effort still runs and says so in `warnings`.
- With the CLI absent from PATH, `list_agents` reports `available: false` without raising.
- Every failure mode a coordinator could misread is stated in the `get_result` hint.

## Common failures

- **Probe used as a health check.** It answers "is the command present". Gating `available` on login state hides a working agent.
- **One config copy updated.** Your local run and the installed package then disagree.
- **A static effort table against a per-model vocabulary.** Wrong on some models, silently.
- **Effort not re-sent after a model switch.** The state machine thinks it already applied; the turn runs on the new default. Test it explicitly with a fake connection whose `configOptions` change after the model is set.
- **Trusting the worker's self-report.** Banner text is often baked in at session creation and does not track later model switches. `observed_model` is the answer.
- **Debugging the protocol on the real CLI.** Use the echo agent first.
- **A new environment variable added in only one of the two required places.**
