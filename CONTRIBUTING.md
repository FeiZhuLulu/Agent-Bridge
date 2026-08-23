# Contributing

[中文](CONTRIBUTING.zh-CN.md)

## Setup

This is the source checkout. End users install with `uv tool install git+https://github.com/FeiZhuLulu/Agent-Bridge.git` and do not need this repo.

```powershell
uv sync --extra dev
uv run pytest
```

## Adding a worker

Connecting a new agent CLI has its own process: [skills/add-worker/SKILL.md](skills/add-worker/SKILL.md). A plain ACP CLI usually needs no code at all — just a block in `~/.agent-bridge/agents.toml`. Read it before opening a PR that touches the adapters.

## Pull requests

- Keep changes focused.
- Add or update tests when behavior changes.
- Do not commit secrets, `.env`, machine-local `~/.agent-bridge/agents.toml`, or personal paths.
- Do not put API keys in issues or commit messages.

## License

By contributing you agree the work is released under the [MIT License](LICENSE).
