# Agent Memory Hub

本仓库按《共享 Agent 记忆系统设计文档》和《部署与联调手册》分阶段实现。

日常使用请直接看 [`docs/QUICK_START.md`](docs/QUICK_START.md)。Codex 与
WorkBuddy 均已安装用户级 `shared-agent-memory` Skill，新项目或新会话可直接
显式调用。

当前完成范围为 **P0 + P1 + P2 + P3 + P4 + P5 + P6（V1 完成）**：

- SQLite Project State 与原始事件、任务、决策、工作日志
- WAL、外键与 `busy_timeout=5000`
- Project State 项目级乐观锁
- 同一 topic 的新决策自动 supersede 旧 active 决策
- Pydantic 输入输出模型
- Mem0 OSS 2.x 适配层
- 本地千问记忆提取
- Qwen3-Embedding-0.6B 本地向量化
- Qdrant Local 持久化目录与 Mem0 SQLite 历史
- Qdrant 跨进程关闭/重开持久化验收
- 基于 `project_id` 的记忆隔离验收
- 启动时校验已有集合的向量维度
- `MemoryService` 编排、Mem0 故障降级与结构化错误
- 本地 stdio MCP Server（6 个 P3 tools）
- 真实 MCP Client/Server 握手、工具发现与调用验收
- Codex A 注册、项目 bootstrap、记忆写入/召回与 finish_turn 真实联调
- 独立 Codex B 的 Project State/长期记忆接手与 finish_turn 回写验证
- WorkBuddy MCP 注册、工具发现与 Project State bootstrap 真实验收
- 本机 Qdrant Server 与 Codex/WorkBuddy 并发语义检索
- macOS 控制 App 启动时按持久化模式自动补启 Qdrant、校验并修复 MCP 客户端配置
- 可追溯 `correct_memory`（更新/删除、跨项目保护、State 乐观锁）
- SQLite 在线备份、Qdrant snapshot 与隔离恢复验收

当前 V1 已完成。仍不包含 Web API、跨机器部署、后台 pending extraction worker
或 Web UI；这些属于后续增强，不影响 Codex 与 WorkBuddy 在本机共享状态和记忆。

`AgentNexus Memory.app` 每次打开都会执行一次状态协调。电脑重启后，在“完整记忆”
模式下会自动补启缺失的 Qdrant，并检查 MCP 启动器和四个客户端配置。MCP 使用
stdio 按需启动；没有 Codex、WorkBuddy 或 Claude 会话连接时，进程数为 0 是正常的，
App 会显示“已就绪 · 等待客户端”，不代表 MCP 启动失败。

## 开发命令

```bash
uv sync
uv run pytest
uv run ruff check .
uv run python scripts/smoke_test.py
uv run python scripts/test_mcp_stdio.py
```

启动本地千问后运行 P1 真实联调：

```bash
cp .env.example .env
uv run python scripts/test_mem0.py
```

默认千问地址是 `http://127.0.0.1:8080/v1`。脚本会从 `/v1/models`
读取真实模型 ID，然后验证 Mem0 `add/search`。

运行 P2 持久化与项目隔离验收：

```bash
uv run python scripts/test_persistence.py
```

该脚本先启动独立写入进程，进程退出后再启动一个全新的读取进程，验证
Qdrant 重开后仍可检索；随后用另一个 `project_id` 验证记忆不可见。它使用
独立目录 `data/p2-smoke`，不会修改 P1 的 `data/qdrant` 数据。重复运行时，
Mem0 可能因去重返回 `ADD: {'memories': []}`，只要后续读取验证通过即为正常。

spaCy 和 fastembed/BM25 当前是可选增强项，未安装时会显示提示，但不影响
本阶段使用的 Qwen3-Embedding 稠密向量检索。

P2 的验收范围和逐步记录见
[`docs/P2_IMPLEMENTATION.md`](docs/P2_IMPLEMENTATION.md)。

P3 将按 Service 编排层与 MCP 适配层分工开发，文件边界、执行顺序和可直接
交给 Claude/WorkBuddy 的提示词见
[`docs/P3_WORK_SPLIT.md`](docs/P3_WORK_SPLIT.md)。

直接启动 stdio MCP Server：

```bash
uv run python -m agent_memory_hub.mcp_server
```

MCP Inspector 需要系统 PATH 中存在 Node.js、npm 和 `npx`：

```bash
uv run mcp dev src/agent_memory_hub/mcp_server.py
```

如果只需自动验收协议，不需要打开浏览器，优先运行
`scripts/test_mcp_stdio.py`。该脚本会启动真实子进程、完成 MCP 握手、确认
七个工具可发现，并验证结构化 `PROJECT_NOT_FOUND` 返回值。

首次使用前登记项目（可重复执行）：

```bash
uv run python scripts/register_project.py \
  agentnexus AgentNexus \
  --repo-path "$(pwd)"
```

P4 已将服务器以 `agent-memory` 注册到 Codex 的共享 MCP 配置，并由全新的
Codex 进程完成 `bootstrap/start_turn/search/finish_turn` 真实联调。配置步骤和
证据见 [`docs/P4_CODEX_A_INTEGRATION.md`](docs/P4_CODEX_A_INTEGRATION.md)。

P5 已由全新的 Codex B 在不依赖 Codex A 聊天上下文的情况下完成接手。验收
证据与 Qdrant Local 并发限制见
[`docs/P5_CODEX_B_HANDOFF.md`](docs/P5_CODEX_B_HANDOFF.md)。

WorkBuddy 已连接同一个 `agent-memory` MCP，并用 `deepseek-v4-flash` 实际完成
`bootstrap`。P6 切换 Qdrant Server 后，并发 `search` 已返回 `status=ok` 并召回
3 条长期记忆；配置、证据和权限边界见
[`docs/WORKBUDDY_INTEGRATION.md`](docs/WORKBUDDY_INTEGRATION.md)。

P6 的 Qdrant Server、纠错、备份恢复与最终回归见
[`docs/P6_IMPLEMENTATION.md`](docs/P6_IMPLEMENTATION.md)。

创建备份：

```bash
uv run python scripts/backup.py
```

初始化本地数据库：

```bash
uv run python scripts/init_db.py
```
