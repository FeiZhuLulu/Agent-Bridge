# 参与贡献

[English](CONTRIBUTING.md)

## 环境

这是源码检出。最终用户用 `uv tool install git+https://github.com/FeiZhuLulu/Agent-Bridge.git` 安装，不需要这份仓库。

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
