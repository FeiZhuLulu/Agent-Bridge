# Agent Bridge

[English](README.md)

Agent Bridge 是一个联通各个本地 Agent 的连接器。目前支持 Codex 作为协调者，指挥 Antigravity CLI、Grok Build、DeepSeek Harness 进行工作。后续将推出更多 Agent 支持。

```text
用户 → Codex → Agent Bridge (MCP) → Antigravity CLI
                                 → Grok Build
                                 → DeepSeek Harness
```

它不操作图形界面。用户只和 Codex 对话。

## 安装

需要 Python 3.11+ 和 [uv](https://docs.astral.sh/uv/)：

```powershell
git clone https://github.com/FeiZhuLulu/Agent-Bridge.git
cd Agent-Bridge
uv sync --extra dev
```

启动 MCP 服务：

```powershell
uv run agent-bridge
```

## 接到 Codex

```powershell
codex mcp add agent_bridge -- uv --directory "C:\path\to\Agent-Bridge" run agent-bridge
```

把 [AGENTS.md](AGENTS.md) 拷到目标仓库或 `~/.codex/AGENTS.md`。改完配置后重启 Codex。

## 工具

| 工具 | 作用 |
| --- | --- |
| `list_agents` | 探测 worker，并报告代理 / 环境 |
| `dispatch_task` | 在项目 `cwd` 里开始或续上一次回合 |
| `wait_task` | 最多等待 `timeout_sec`（默认 180） |
| `check_task` | 非阻塞状态查询 |
| `get_result` | 截断后的结果 + 改过的文件 |
| `get_transcript` | 分页会话日志 |
| `cancel_task` | 取消进行中的回合 |
| `list_sessions` | 已知会话 |
| `end_session` | 关掉 worker 进程 |

## 测试

```powershell
uv run pytest
```

## 许可证

[MIT](LICENSE) © FeiZhuLulu
