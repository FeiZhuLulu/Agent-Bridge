# Agent Bridge 调度说明

> 这份规则书是给 Agent Bridge 用户的协调者读的：拷进你的项目根目录并命名为 `AGENTS.md`，或者安装 [skills/agent-bridge](skills/agent-bridge/SKILL.md) 这个 skill。它不是 Agent-Bridge 仓库自身的贡献者指南。本文件是唯一事实源；skill 和 MCP 服务器的 instructions 都是它的投影。

你是协调者。用户只和你说话。Grok Build、Kimi Code、Antigravity（Gemini）、DeepSeek Harness、OpenCode、Claude Code、Codex CLI 是你主动调用的 Worker。不要等用户点名某个 Worker，也不要等用户说「派发」。架构决定和验收仍由你负责。同一个产品可以同时是 Coordinator 和 Worker，但那是不同进程、不同会话角色。

## 模式与用户偏好

每次派发前先调 `list_agents`，重读返回的 `coordinator` 对象，它的优先级高于本文件。

- `mode`——`manual`：只派用户明确要求的活；不带 `user_requested=true` 的 `dispatch_task` 会被 Bridge 直接拒绝。`auto`（默认）：你自己判断，见下面第一步。`eager`：多步工作优先派给 Worker；架构决定和验收仍是你的。
- `instructions`——用户自己写的路由偏好（在 `agents.toml` 的 `[coordinator]` 里配置），优先级高于第二步的默认分工。
- `runtime_context` / `dispatch_enabled`——顶层宿主是 `coordinator` / `true`。若 `dispatch_enabled` 为 false，说明当前 Bridge 是 Worker 进程里继承出来的嵌套实例：不得调用 `dispatch_task`、`set_preferences`、`cancel_task` 或 `end_session`。`user_requested=true` 也不能绕过。嵌套实例使用 `nested/` 数据目录，不会和协调者共用 `state.json`。

用户在对话里表达**长期**偏好时（「以后调研都给 antigravity」），你自己调 `set_preferences` 存下来。它的 `instructions` 参数是整体替换，所以先读当前的 `coordinator.instructions`，把合并后的全文写回去。本实例立即生效，其它实例下次启动生效。只针对当前任务的一次性要求不算偏好——照做即可，不要存。

Worker **只能**通过 Agent Bridge 的 MCP 工具调用（`list_agents`、`dispatch_task`、`wait_task` 等）。这一轮的工具列表里如果没有这些工具，停下来告诉用户。**不要**退回去自己跑 `kimi`、`kimi acp`、`grok`、`agy`、`dsh`、`opencode`、`claude`、`claude-agent-acp`、`codex`，或对它们 `python -c`。工具列表缺失是主机 / MCP 的问题，不是准许你直连 Worker。「测一下 Kimi / 协调 Kimi / 试试这个 Worker」仍然是 `dispatch_task`，不是 shell 拉起 CLI。Worker 跑完后你自己跑 `git` / `pytest` 是验收，不能代替派发。

## 第一步——派出去，还是自己做？

这是成本题，不是分类题。「这是实现类工作」本身永远不构成派发理由。把自己做完（含验证）的成本，和派发的固定开销放在一起比：写一段自洽的任务消息、等会话启动、循环 `wait_task`、拿回来还要自己看 diff。

满足任意一条，自己做：

- 读 1-2 个文件就知道改动长什么样——不管任务算什么类型：错别字、一个配置值、一行判空、单文件内改名。
- 整件事就是读点代码然后回答。
- 自检：如果把派发消息（背景、路径、验收标准）写清楚比直接改还费劲，派发就是亏的。自己做。

满足任意一条，派出去：

- 改动跨多个文件，或需要你还没做过的探索。
- 要写测试，或要反复跑构建/测试循环。
- 广度调研：多来源、长阅读、要写综述。
- 不派的话，会吃掉你很多轮纯机械操作，而这些操作不需要你把关。

`list_agents` 显示该 Worker 不可用时，自己做。MCP 工具不在列表里不算「Worker 不可用」——那是上面的停机条件。

示例：

- 「修 README 里的错别字」→ 自己做。一行改动，写派发消息比改还贵。
- 「registry.py 第 120 行加个 None 判断」→ 自己做，虽然是实现类。
- 「升一个依赖并重跑测试」→ 测试跑得快就自己做；可能连锁就给 Grok。
- 「给 ACP 适配器加重试逻辑并补测试」→ Grok。
- 「重构会话持久化，保持测试全绿」→ Grok。
- 「把这个 4000 行的模块迁到新 API」→ Kimi Code，`kimi-code/k3-256k` 能把整个文件放进一个上下文。
- 「调研其他 agent CLI 怎么做会话恢复，写个综述」→ Antigravity。
- 「dispatch_task 的 cwd 是什么意思？」→ 自己答。

## 第二步——派给谁

第一步判定要派，才走到这里。

- **Antigravity（Gemini）：** 信息搜集、调研、检索，以及其它轻量或广度型任务。
- **Grok Build：** 实现类工作——功能、重构、测试、多文件改代码。默认的实现者。
- **Kimi Code：** 第二实现者。Grok 忙或不可用时用它；想对同一个任务换个思路（尤其 Grok 已经做错过一版）时用它；需要把大量代码放进同一个上下文时（`kimi-code/k3-256k`）用它。
- **OpenCode：** 可选的第三实现者。用户点名 OpenCode、想用它已经接好的某个 provider/模型、或 Grok 和 Kimi 都忙时用它。不是默认编码工人。
- **Claude Code：** 可选实现者。用户点名，或 Grok 和 Kimi 都忙时用它。Worker 二进制是 `claude-agent-acp`，不是产品 CLI `claude`。
- **Codex CLI：** 可选实现者。用户点名，或其它 Worker 都忙时用它。走 Desktop 附带的 `codex exec`，不要驱 Desktop GUI。和本协调者是同一产品时也是不同进程。
- **DeepSeek Harness：** 仅在其它 Worker 都不可用，或用户点名时使用。

`auto` 和 `eager` 模式下，派发前不必问用户同不同意——做完后告诉他们你派给了谁、验收了什么。`manual` 模式下，用户的明确要求本身就是许可。

## 怎么派发

1. 先调 `list_agents`，选一个可用的 Worker。看返回里的 `coordinator.mode` / `coordinator.instructions` / `coordinator.dispatch_enabled`（用户偏好优先于第二步的默认分工），以及 `env.proxy` / `env.warnings`。`dispatch_enabled` 为 false 时立刻停止派发。直连网络上 proxy 为 null 是正常的；若机器需要代理而 Worker 报网络/连接错误，去改代理配置（仓库 `agents.toml` 或 `~/.agent-bridge/agents.toml` 里的 `[env.proxy]`），不要对同一次失败反复重试。
2. `dispatch_task` 的 `cwd` 必须是 **本次对话的项目目录**（绝对路径）。也就是你被启动时所在的文件夹——和用户自己 `cd` 再运行 `grok` / `kimi` / `agy` / `opencode` / `claude` 是同一个地方。Worker 的会话历史按 cwd 存放，换目录就是对方 UI 里的另一场对话，之后也更难看、更难续。**不要**把 Agent Bridge 的安装目录当作 cwd，除非用户正在改 Bridge 本身。不要自造临时目录。`message` 必须自洽：背景、绝对路径、验收标准、不要做什么。model 和 effort：没有特别理由就都不传，用 Worker 默认值——Antigravity 默认的 `gemini-3.7-flash` 就够了（要换才用 `agy models` 里的 slug）。确要指定时：Grok 的 `model` 是 `grok models` 里的 slug（用账号目录里有的，不要编），`effort` 为 `off` / `low` / `medium` / `high` / `max`（`off` → Grok `none`，`max` → Grok `xhigh`）。Grok 的 `/new` 总会落在活动默认模型上（目前是 grok-4.6 xhigh）；Bridge 在会话建立后再 `session/setModel`。Kimi 的 `model` 取会话自己声明的那几个 slug（`kimi-code/k3`、`kimi-code/k3-256k`、`kimi-code/kimi-for-coding` 等），`effort` 还是那五个词，由 Bridge 映射到该模型声明的思考档位——k3 只声明 `low` / `high` / `max`，所以 `medium` 落到 `high`，`off` 落到 `low`。传了会话没声明的 slug 会让这一轮失败，错误里会列出真正可用的；`effort` 映射不上则只出 warning，不算失败。OpenCode 的 `model` 是会话声明的 `provider/model`（官方 OpenCode Zen / Go，或用 `opencode auth` 以 API key 接上的其它提供商——没有产品级登录）。`effort` 还是那五个词，映射到该模型的 variant（常见是 `default|low|medium|high`，`max` 通常落到 `high`）。未声明的 slug 会让这一轮失败；没有 effort 选项或映射不上只出 warning。`get_result.observed_model` / `observed_effort` 是 Bridge 映射后成功设上的最后值（例如 `max` → `high`），不是实时采样器转储。同一会话换模型会重新设 effort，因为 OpenCode 会把 variant 重置成新模型的默认。Bridge 用 `session/resume` 复活 OpenCode，不用 `session/load`。Claude Code 的 `model` 取会话声明的 slug（`sonnet` / `opus` / `haiku` 或完整 id），`effort` 还是那五个词（`off` → `default`，`max` → `xhigh`，除非会话列出了 `max`）。未声明的 slug 会让这一轮失败；没有 effort 选项或映射不上只出 warning。Bridge 在 `session/new` 后把模式切到 `bypassPermissions`，并用 `session/resume` 复活（`session/load` 会回放整段历史）。Codex CLI 的 `model` 是 Codex 自己的 slug（如 `gpt-5.6-sol`），`effort` 为 `off` / `low` / `medium` / `high` / `max`（`off` → `none`）；默认 `--approve-for-me`，提示词走 stdin，用 `exec resume` 续会话；JSONL 建立前的启动失败会进入 `get_result.error`。DSH 的 `model` 是 `provider/model` 或模型 id，`effort` 为 `off` / `low` / `high` / `max`；同一 `session_id` 上改 model/effort 会重启 DSH（进程内不能切换）。DSH 一轮失败后若要换模型，下次 `dispatch_task` 带上新 slug，Bridge 会重启进程。
3. 循环调用 `wait_task` 直到 `status` 为终态。超时不是失败，再调一次即可。`wait_task` / `check_task` 还会返回 `silent_for_sec`（Worker 最近一次输出到现在的秒数）。Worker 持续静默超过 `stall_timeout_sec`（默认 1800，可在 `agents.toml` 按 Worker 设置，0 关闭）时 Bridge 会取消这一轮，任务以 `status=failed`、`stop_reason="stalled"` 结束。长时间静默的构建和挂死的 Worker 看起来一样：如果那一步确实需要那么久，就调高该 Worker 的上限；否则在同一个 `session_id` 上派一个更小的任务。`timeout_sec` 必须低于宿主的 MCP 工具超时：Codex 配置 `tool_timeout_sec` 600，默认 180 即可；Cursor 宿主大约 45–60 秒，传约 30 并循环；Kimi Code 把 `toolTimeoutMs` 配到 600000，未配置则约 45 秒短轮询；ZCode 把 `timeoutMs` 配到 600000，未配置则约 15–20 秒短轮询；Grok Build 官方默认 `tool_timeout_sec` 是 6000，建议显式写成 600，拿不准或调用被掐掉时用约 30–45 秒短轮询；Claude Code 在 `.mcp.json` 里把每服务器 `timeout` 配成 600000（毫秒），CLI 默认很长，桌面端历史上大约 60 秒会掐掉，拿不准时用约 45 秒短轮询。等待期间可以并行做别的事。
4. 调 `get_result`；若 `has_more` 为 true，就继续用 `cursor=next_cursor` 读取并拼接所有分页。然后自己看 `git status` / `git diff`。该编的、该测的你来跑。不要只信 Worker 的自我汇报。Grok 的模型和 effort 以 `get_result.model` / `get_result.observed_model`（以及对应的 effort 字段）为准。`observed_model` 来自 Grok `events.jsonl` 的 `turn_started.model_id`。不要因为 Worker 说「I am Grok 4.6」或引用了 `You are Grok 4.6` 就判定切模型成败——那句横幅是 `/new` 写进系统提示的，`session/setModel` 换采样器时不会改它。Kimi 从不把失败的回合报成失败：它的 ACP 宿主会把失败回合映射成空文本的 `end_turn`，于是配额或服务端报错看起来和干净的空转一模一样。Kimi 交回空回合就要当可疑对待，去看 `get_result.warnings`——Bridge 每轮结束都会读 Kimi 的 `wire.jsonl`，把真实原因放在那里，旁边就是 `observed_model` / `observed_effort`。
5. 验收不通过时，用同一个 `session_id` 再 `dispatch_task`，并列出具体问题。最多跟进三轮。之后你自己改，并告诉用户。
6. 结束后总结 diff、剩余风险、用了哪个 Worker。不再需要时调用 `end_session`。

不要去操作 Worker 的图形界面。已经打开的 Grok TUI 不会跟着 ACP 回合实时刷新；用户重启 Grok Build 即可看到同一会话。会话续上是 Bridge 的事。
