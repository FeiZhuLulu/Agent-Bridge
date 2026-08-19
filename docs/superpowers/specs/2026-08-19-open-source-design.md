# Agent Bridge 开源设计

日期：2026-08-19  
状态：待用户审阅后进入实现计划

## 目标

把本仓库整理成可公开的标准开源项目，发布到 GitHub：

https://github.com/FeiZhuLulu/Agent-Bridge

别人能 clone、读懂、跑测试、提 issue。这一轮不上 PyPI。

## 已确认决策

| 项 | 决定 |
| --- | --- |
| 托管 | GitHub 公开仓库，账号 `FeiZhuLulu` |
| 仓库名 | `agent-bridge` |
| 许可证 | MIT，版权行 `Copyright (c) 2026 FeiZhuLulu` |
| 首页语言 | 英文 `README.md` + 中文 `README.zh-CN.md`，页顶互链 |
| 档次 | 标准开源仓库：LICENSE、清理本机痕迹、CI、CONTRIBUTING、issue 模板 |
| Git 作者 | 只用本机已有身份 `FeiZhuLulu`。提交不得使用 Cursor 或任何 agent 作为 author / committer。GitHub Contributors 里只能出现 FeiZhuLulu |

## 产品定位（公开文案）

公开首页用下面这段，不要扩写成宣传稿。中英各写一次，意思对齐。

中文：

> Agent Bridge 是一个联通各个本地 Agent 的连接器。目前支持 Codex 作为协调者，指挥 Antigravity CLI、Grok Build、DeepSeek Harness 进行工作。后续将推出更多 Agent 支持。

英文（同义，不另加卖点）：

> Agent Bridge is a connector for local coding agents. Codex is the coordinator today: it can direct Antigravity CLI, Grok Build, and DeepSeek Harness. More agents will follow.

公开首页**不写 Cursor** 作为已支持 worker，也不把它列进工具表或架构图。代码里的 Cursor adapter 可以保留，不在这一轮删除，也不在 README / CONTRIBUTING / 仓库描述里宣传。

## 公开仓库包含什么

留下：

- `src/agent_bridge/`、`tests/`、`scripts/`
- `docs/codex-setup.md`（安装与接线。可以提到树里仍有 Cursor adapter，但不要写成已公开支持的 worker）
- `AGENTS.md`（给 Codex 的调度规则；对外描述与 README 一致，不把 Cursor 写成已支持对象）
- `agents.toml`、`agents.toml.example`
- 新补的 `LICENSE`、双语 README、双语 CONTRIBUTING、CI、issue 模板、`SECURITY.md`

不公开：

- `docs/plan.md`（含本机路径、会话 ID、个人验收记录）。删除或移出仓库，不进 git
- `.venv/`、缓存、`.env`（已在 `.gitignore`）

测试里的本机路径改成中性路径，避免把本机用户名写进公开历史。

## README 结构（中英各一份，各保持一屏）

1. 语言切换链接
2. 上面那段定位（一段话）
3. 安装：`uv sync --extra dev`，Python 3.11+
4. 接到 Codex：链到 `docs/codex-setup.md`
5. 工具表：现有 MCP 工具，去掉排障长文
6. 测试：`uv run pytest`

排障、代理、模型选择细节只放 `docs/codex-setup.md`。

仓库 GitHub 描述用英文一句，与定位一致，例如：`Connect local coding agents. Codex coordinates Antigravity CLI, Grok Build, and DeepSeek Harness.`

## CI

`.github/workflows/ci.yml`：

- 触发：`push`、`pull_request`
- 矩阵：Python 3.11 / 3.12 × Ubuntu / Windows
- 用 `uv` 安装 `dev` extra，跑 `pytest`
- 不跑需要登录的 `scripts/smoke_*.py`

## 贡献与安全

- `CONTRIBUTING.md` + `CONTRIBUTING.zh-CN.md`：各半页。装环境、跑测试、PR 要带测试、不要提交密钥和本机路径
- Issue 模板：Bug、功能建议。字段英文，标题可以中文
- `SECURITY.md`：安全问题走 GitHub Security Advisory，不要把密钥贴进 issue

## `pyproject.toml`

补上：

- `license = "MIT"`
- `authors = [{ name = "FeiZhuLulu" }]`
- `urls.Homepage` / `Repository` 指向该 GitHub 仓库

不在 authors 或元数据里写 Cursor。

## Git 与发布

1. 本地写完上述文件（实现阶段，不在本文档里改代码）
2. `git init -b main`（仓库目前还没有 `.git`）
3. 确认每次 `git commit` 的 author / committer 都是 `FeiZhuLulu`（沿用本机 `user.name` / `user.email`）。禁止 `--author=Cursor`，禁止让工具改写 committer
4. 用 GitHub API 创建公开仓库 `FeiZhuLulu/agent-bridge`（若已存在则停下来问，不覆盖）
5. 推 `main`

第一轮不发 release、不上 PyPI。

## 明确不做

- 不翻译整本 `docs/codex-setup.md`
- 不删 Cursor adapter 代码
- 不把 Cursor 写进公开“已支持”列表
- 不做官网、badge 堆砌、Code of Conduct
- 不把 Cursor 或任何 agent 显示为 GitHub Contributor
