# Agent Bridge

[中文](README.zh-CN.md)

Agent Bridge is a connector for local coding agents. Codex is the coordinator today: it can direct Antigravity CLI, Grok Build, and DeepSeek Harness. More agents will follow.

```text
User → Codex → Agent Bridge (MCP) → Antigravity CLI
                                 → Grok Build
                                 → DeepSeek Harness
```

It does not drive GUIs. The user talks only to Codex.

## Install

Python 3.11+ and [uv](https://docs.astral.sh/uv/):

```powershell
git clone https://github.com/FeiZhuLulu/Agent-Bridge.git
cd Agent-Bridge
uv sync --extra dev
```

Run the MCP server:

```powershell
uv run agent-bridge
```

## Connect Codex

See [docs/codex-setup.md](docs/codex-setup.md). Short version: register `agent-bridge` in `~/.codex/config.toml`, and copy [AGENTS.md](AGENTS.md) into the target repo or `~/.codex/AGENTS.md`.

## Tools

| Tool | Role |
| --- | --- |
| `list_agents` | Probe workers and report proxy/env |
| `dispatch_task` | Start or resume a turn in the project `cwd` |
| `wait_task` | Block up to `timeout_sec` (default 180) |
| `check_task` | Non-blocking status |
| `get_result` | Truncated result + changed files |
| `get_transcript` | Paged session log |
| `cancel_task` | Cancel the in-flight turn |
| `list_sessions` | Known sessions |
| `end_session` | Shut down a worker process |

## Tests

```powershell
uv run pytest
```

## License

[MIT](LICENSE) © FeiZhuLulu
