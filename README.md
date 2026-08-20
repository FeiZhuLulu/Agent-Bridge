# Agent Bridge

[English](#english) · [中文](#中文)

## English

Agent Bridge is a connector for local coding agents. A coordinator — Codex, Cursor, or Kimi Code — directs Antigravity CLI, Grok Build, Kimi Code, and DeepSeek Harness. More agents will follow.

```text
User → Coordinator (Codex / Cursor / Kimi Code) → Agent Bridge (MCP) → Antigravity CLI
                                                                     → Grok Build
                                                                     → Kimi Code
                                                                     → DeepSeek Harness
```

It does not drive GUIs. The user talks only to the coordinator.

### Install

Python 3.11+ and [uv](https://docs.astral.sh/uv/):

```powershell
git clone https://github.com/FeiZhuLulu/Agent-Bridge.git
cd Agent-Bridge
uv sync --extra dev
uv run agent-bridge
```

### Connect Codex

```powershell
codex mcp add agent_bridge -- uv --directory "C:\path\to\Agent-Bridge" run --no-sync agent-bridge
```

Copy [AGENTS.md](AGENTS.md) into the target repo or `~/.codex/AGENTS.md` (Chinese: [AGENTS.zh-CN.md](AGENTS.zh-CN.md)). Restart Codex after changing the config. Proxy, env, and a longer drill are in [SETUP.md](SETUP.md).

### Connect Cursor

Add the server to `%USERPROFILE%\.cursor\mcp.json` (all projects) or `<repo>\.cursor\mcp.json` (one project):

```json
{
  "mcpServers": {
    "agent-bridge": {
      "command": "uv",
      "args": ["--directory", "C:/path/to/Agent-Bridge", "run", "--no-sync", "agent-bridge"]
    }
  }
}
```

`--no-sync` keeps spawns from re-installing the package at startup (on Windows a running instance locks the launcher exe); run `uv sync` yourself after updating the Bridge. Cursor applies a repo-root `AGENTS.md` automatically, so the same orchestration rules work unchanged. Details in [SETUP.md](SETUP.md).

### Connect Kimi Code

Add the server to `%USERPROFILE%\.kimi-code\mcp.json` (all projects) or `<repo>\.kimi-code\mcp.json` (one project):

```json
{
  "mcpServers": {
    "agent-bridge": {
      "command": "uv",
      "args": ["--directory", "C:/path/to/Agent-Bridge", "run", "--no-sync", "agent-bridge"],
      "toolTimeoutMs": 600000
    }
  }
}
```

Kimi Code's default MCP tool timeout is 60 s, which is shorter than a `wait_task` poll; `toolTimeoutMs` raises it. Kimi reads a repo-root `AGENTS.md` too, so the orchestration rules apply unchanged. Details in [SETUP.md](SETUP.md).

### Tools

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

### Tests

```powershell
uv run pytest
```

## 中文

Agent Bridge 是一个联通各个本地 Agent 的连接器。由协调者——Codex、Cursor 或 Kimi Code——指挥 Antigravity CLI、Grok Build、Kimi Code、DeepSeek Harness 进行工作。后续将推出更多 Agent 支持。

```text
用户 → 协调者（Codex / Cursor / Kimi Code）→ Agent Bridge (MCP) → Antigravity CLI
                                                              → Grok Build
                                                              → Kimi Code
                                                              → DeepSeek Harness
```

它不操作图形界面。用户只和协调者对话。

### 安装

需要 Python 3.11+ 和 [uv](https://docs.astral.sh/uv/)：

```powershell
git clone https://github.com/FeiZhuLulu/Agent-Bridge.git
cd Agent-Bridge
uv sync --extra dev
uv run agent-bridge
```

### 接到 Codex

```powershell
codex mcp add agent_bridge -- uv --directory "C:\path\to\Agent-Bridge" run --no-sync agent-bridge
```

把 [AGENTS.md](AGENTS.md) 拷到目标仓库或 `~/.codex/AGENTS.md`（中文对照：[AGENTS.zh-CN.md](AGENTS.zh-CN.md)）。改完配置后重启 Codex。代理、环境和更完整的验收步骤见 [SETUP.md](SETUP.md)。

### 接到 Cursor

把服务器写进 `%USERPROFILE%\.cursor\mcp.json`（对所有项目生效）或 `<仓库>\.cursor\mcp.json`（只对单个项目）：

```json
{
  "mcpServers": {
    "agent-bridge": {
      "command": "uv",
      "args": ["--directory", "C:/path/to/Agent-Bridge", "run", "--no-sync", "agent-bridge"]
    }
  }
}
```

`--no-sync` 让每次拉起不再重装包（Windows 上运行中的实例会锁住启动 exe）；更新 Bridge 后自己跑一次 `uv sync`。Cursor 会自动应用仓库根目录的 `AGENTS.md`，同一份调度规则无需改动即可生效。细节见 [SETUP.md](SETUP.md)。

### 接到 Kimi Code

把服务器写进 `%USERPROFILE%\.kimi-code\mcp.json`（对所有项目生效）或 `<仓库>\.kimi-code\mcp.json`（只对单个项目）：

```json
{
  "mcpServers": {
    "agent-bridge": {
      "command": "uv",
      "args": ["--directory", "C:/path/to/Agent-Bridge", "run", "--no-sync", "agent-bridge"],
      "toolTimeoutMs": 600000
    }
  }
}
```

Kimi Code 默认的 MCP 工具超时是 60 秒，比一次 `wait_task` 轮询还短，用 `toolTimeoutMs` 调高。Kimi 同样会读仓库根目录的 `AGENTS.md`，调度规则无需改动。细节见 [SETUP.md](SETUP.md)。

### 工具

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

### 测试

```powershell
uv run pytest
```

## License

[MIT](LICENSE) © FeiZhuLulu
