# 参与贡献

[English](CONTRIBUTING.md)

## 环境

```powershell
uv sync --extra dev
uv run pytest
```

## Pull request

- 改动保持小而明确。
- 行为有变化时补测试。
- 不要提交密钥、`.env`、本机的 `~/.agent-bridge/agents.toml`，或带用户名的本机路径。
- 不要把 API key 写进 issue 或提交说明。

## 许可证

提交即表示同意以 [MIT License](LICENSE) 发布。
