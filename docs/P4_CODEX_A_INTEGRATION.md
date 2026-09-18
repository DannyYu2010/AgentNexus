# P4：Codex A 接入

## 目标

- 把 `agent-memory-hub` 注册为本机 Codex 的 stdio MCP Server。
- 为 AgentNexus 登记稳定 `project_id=agentnexus`。
- 通过 `AGENTS.md` 规定 bootstrap、start_turn、search、finish_turn 的使用时机。
- 用一个全新的 Codex 进程验证能够发现并调用共享记忆工具。

## 进度记录

- [x] 2026-09-02：依据 OpenAI 官方 MCP 文档核对配置方式；本地 Codex 客户端
  支持 stdio，并与桌面端、CLI、IDE 扩展共享 MCP 配置。
- [x] 2026-09-02：通过 `codex mcp add` 注册全局服务器 `agent-memory`；命令与
  `uv` 均使用绝对路径，状态为 enabled，设置 `MEM0_TELEMETRY=false`。
- [x] 2026-09-02：新增幂等项目登记脚本与 `AGENTS.md` Shared Memory Rules。
- [x] 2026-09-02：登记 `agentnexus`；重复执行返回 `already registered`。
- [x] 2026-09-02：首次新 Codex 进程发现 MCP，但调用被默认审批策略阻止；
  将 `default_tools_approval_mode` 设为 `approve`，并把启动/工具超时设为
  20/120 秒。
- [x] 2026-09-02：第二个全新 Codex 进程成功调用 `bootstrap`，返回
  `project_id=agentnexus`、`state_version=0`。
- [x] 2026-09-02：完整 Codex A 联调通过：`bootstrap → start_turn → search →
  finish_turn` 全部成功。记忆抽取状态为 `done`，search 召回 P4 接入事实，
  score 约 0.733；work log 成功写入。
- [x] 2026-09-02：README 更新完成；Ruff、51 项测试、P0 smoke、真实 MCP
  stdio smoke 全部通过，`codex mcp get agent-memory` 确认配置 enabled。
- [x] 2026-09-02：通过 `finish_turn` 写入 P4 最终 work log，并把权威 Project
  State 更新为 `implementation_phase=P4 complete`、`state_version=3`，下一阶段
  为 P5 Codex B 接手验证。

## 注册配置

```bash
codex mcp add agent-memory \
  --env MEM0_TELEMETRY=false \
  -- /opt/homebrew/bin/uv \
  --directory "$HOME/Src/AgentNexus" \
  run python -m agent_memory_hub.mcp_server
```

检查配置：

```bash
codex mcp get agent-memory
codex mcp list
```

## 项目登记

```bash
uv run python scripts/register_project.py \
  agentnexus AgentNexus \
  --repo-path "$HOME/Src/AgentNexus"
```

脚本可重复运行；已存在时只报告 `already registered`，不会覆盖项目记录。

## Codex MCP 策略

`~/.codex/config.toml` 的 `agent-memory` 配置还包含：

```toml
default_tools_approval_mode = "approve"
startup_timeout_sec = 20
tool_timeout_sec = 120
```

共享记忆工具在本机自动调用，不反复弹审批；120 秒工具超时用于覆盖首次加载
本地 embedding/Qwen 的冷启动时间。

## 真实验收结果

- Codex A MCP server：`agent-memory`
- Project：`agentnexus`
- Raw event：`evt_63714ec9f7db4769abe62a9373f156fb`
- Memory：`5a9a019c-75f9-436e-a164-558549260e75`
- Work log：`work_ae6f614b52a547588e53f9385f97c695`
- Final work log：`work_640a0581205f44ada2994ec9c2e86e3d`
- 召回内容：`P4 验收事实：Codex A 已成功接入 agent-memory MCP。`
- 最终 state version：`3`
