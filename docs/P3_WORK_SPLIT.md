# P3 分工与交接方案

## P3 边界

设计文档把 P3 定义为 MCP Server；部署手册要求先实现 Service 编排，再接
MCP。为避免工具层直接拼接数据库与 Mem0，P3 分为两个连续子阶段：

1. P3-A：`MemoryService` 编排层。
2. P3-B：stdio MCP Server 与 Inspector 验收。

P3 不接入 Codex A/B，不做备份恢复，也不引入 FastAPI、消息队列或网络服务。

## 文件所有权

### Claude：P3-A Service 编排

只负责以下文件：

- `src/agent_memory_hub/service.py`
- `src/agent_memory_hub/schemas.py`
- 必要时 `src/agent_memory_hub/errors.py`
- `tests/test_service.py`

要求：组合现有 `ProjectStore` 与 `MemoryEngine`，实现结构化的
`bootstrap/start_turn/search/get_state/update_state/finish_turn`；Mem0 写入失败时
必须保留 raw event 并标记 pending；所有 public 方法使用 Pydantic 输入输出；
不得修改 Project Store 和 Memory Engine 的既有语义，不实现 MCP。

### WorkBuddy：P3-B MCP 适配

在 P3-A 接口稳定后，只负责以下文件：

- `src/agent_memory_hub/mcp_server.py`
- `tests/test_mcp_tools.py`
- `pyproject.toml` 与 `uv.lock` 中的 MCP 依赖
- MCP Inspector 操作说明（追加到本文件的“验收记录”）

要求：MCP tools 只做参数解析、调用 `MemoryService` 和 Pydantic JSON 序列化；
不得复制 Service 业务逻辑；使用 lockfile 实际安装版本对应的 MCP SDK API；
默认 stdio，不监听网络端口。

### Codex：接口把关与本机总验收

Codex 不承担大段实现，只负责：

- 开工前确认两层接口和文件边界。
- 审查 Claude 与 WorkBuddy 的变更，处理少量集成冲突。
- 在本机运行 `ruff`、全量 `pytest`、P0/P1/P2 回归。
- 启动 MCP Inspector，验证全部 tools 可发现、可调用、返回结构化 JSON。
- 每个验收步骤同步更新本文件和 README。

## 执行顺序

1. Claude 完成 P3-A，提交文件清单、测试结果和未解决问题。
2. Codex 做一次轻量接口审查；不通过时只把明确问题退回 Claude。
3. WorkBuddy 基于已稳定的 Service 实现 P3-B。
4. Codex 做最终本机联调并记录验收结果。

不要让 Claude 与 WorkBuddy 同时修改 `schemas.py`、`README.md` 或同一测试文件。

## 给 Claude 的任务提示词

```text
项目：`<AgentNexus 仓库绝对路径>`
当前 P0-P2 已完成。请只实现 P3-A Memory Service 编排层，不实现 MCP。

允许修改：
- src/agent_memory_hub/service.py（新增）
- src/agent_memory_hub/schemas.py
- src/agent_memory_hub/errors.py（仅确有必要）
- tests/test_service.py（新增）

要求：
1. 复用现有 ProjectStore 和 MemoryEngine，不重写它们。
2. public service 方法全部使用 Pydantic 输入/输出。
3. 实现 bootstrap、start_turn、search、get_state、update_state、finish_turn。
4. start_turn 必须先 record_raw_event，再调用 MemoryEngine.add；Mem0 失败时保留
   raw event、标记 extract pending，并返回可识别的降级结果；成功则标记 done。
5. bootstrap 返回 state、最近 decisions、open tasks、recent work，默认限额分别
   10/20/20；不因 Mem0 不可用而影响 Project State 读取。
6. finish_turn 记录 work log，并按 expected_state_version 原子更新 state；沿用
   STATE_CONFLICT，不静默覆盖。
7. 所有外部依赖使用 fake/stub 写单元测试，不启动真实 Qwen/Qdrant。
8. 运行 uv run pytest tests/test_service.py 和 uv run ruff check .。
9. 不修改 README、pyproject.toml、uv.lock、project_store.py、memory_engine.py。
10. 完成后给出：修改文件、接口列表、测试结果、待 WorkBuddy 使用的调用方式。
```

## 给 WorkBuddy 的任务提示词

```text
项目：`<AgentNexus 仓库绝对路径>`
Claude 已完成 P3-A MemoryService。请只实现 P3-B stdio MCP 适配与测试。

允许修改：
- src/agent_memory_hub/mcp_server.py（新增）
- tests/test_mcp_tools.py（新增）
- pyproject.toml、uv.lock（只增加并锁定 mcp[cli]）
- docs/P3_WORK_SPLIT.md（只追加“验收记录”）

要求：
1. 先 uv add "mcp[cli]"，检查实际锁定版本，再按该版本官方 API 实现；不要
   混用 FastMCP v1 与 MCPServer v2 示例。
2. 默认 stdio，不监听 HTTP 端口。
3. 暴露 bootstrap、start_turn、search、get_state、update_state、finish_turn。
4. tools 只能解析参数、调用 MemoryService、返回 model_dump(mode="json")；
   不复制数据库或 Mem0 业务逻辑。
5. 错误返回稳定 code 和 details，不返回 Python traceback 给调用者。
6. 测试使用 fake service，不加载真实 embedding/Qwen/Qdrant。
7. 运行 uv run pytest tests/test_mcp_tools.py 和 uv run ruff check .。
8. 验证 CLI 能加载 server 并列出 tools；把准确命令和结果追加到文档。
9. 不修改 service.py、schemas.py、errors.py、README.md。
10. 完成后给出修改文件、MCP SDK 版本、测试结果和 Inspector 命令。
11. 本阶段不要自行实现 correct_memory；纠错涉及 Mem0 更新/删除和审计链，按
    总体阶段表留到 P6。不要在 MCP 层放占位业务逻辑。
```

## 验收记录

- [x] 2026-09-01：确认 P3 边界、执行顺序与文件所有权。
- [x] 2026-09-01：Claude 完成 P3-A；Codex 本机审查通过。Service 专项
  19 项测试通过，全项目 33 项测试通过，Ruff 与 P0 smoke test 通过。
- [x] 2026-09-01：确认 `start_turn` 只接收 `user/assistant`；`system/tool`
  不进入 Mem0 抽取管线。`correct_memory` 按总体阶段表留到 P6。
- [x] 2026-09-01：WorkBuddy 完成 P3-B MCP 适配。MCP SDK 锁定
  `mcp==2.1.1`（v2 API：`MCPServer` + `@server.tool()` + `run_stdio_async`，
  非 FastMCP v1）。新增 `src/agent_memory_hub/mcp_server.py` 与
  `tests/test_mcp_tools.py`；`pyproject.toml` 仅追加 `mcp[cli]>=2.1.1`，
  `uv.lock` 相应锁定。工具层仅做参数解析 → 调 `MemoryService` →
  `model_dump(mode="json")`，未复制任何数据库/Mem0 逻辑，未实现
  `correct_memory`（留 P6）。错误统一返回
  `{"ok": false, "error": {"code", "message", "details"}}`，域错误映射
  `PROJECT_NOT_FOUND` / `STATE_CONFLICT` 等稳定 code，意外异常折叠为
  `INTERNAL_ERROR`，不向调用者泄漏 traceback。
- [x] 2026-09-01：P3-B 测试通过。`tests/test_mcp_tools.py` 17 项全过
  （fake service + 真实 ProjectStore，memory=None 降级，不加载
  embedding/Qwen/Qdrant）；全项目 50 项（33 P0-P2 回归 + 17 P3-B）通过；
  `uv run ruff check .` 通过。
- [x] 2026-09-01：CLI 加载验证通过。`uv run mcp run src/agent_memory_hub/mcp_server.py`
  可正常加载并以 stdio 阻塞运行；stdio client 端到端验证 6 个 tools
  （bootstrap/start_turn/search/get_state/update_state/finish_turn）全部可
  发现、可调用、返回结构化 JSON。
- [x] 2026-09-01：Codex 本机复核并修正懒加载边界：Project State 工具
  不再初始化 embedding/Qdrant，只有首次 `start_turn/search` 才初始化 Mem0。
- [x] 2026-09-01：修正后 MCP 专项 18 项通过，全项目 51 项通过，Ruff 通过。
- [x] 2026-09-01：真实 stdio MCP Client 完成握手，发现全部 6 个 P3 tools，
  调用 `bootstrap` 得到结构化 `PROJECT_NOT_FOUND`，无 traceback。
- [x] 2026-09-01：MCP Inspector 2.4.0 在本机成功启动。系统 PATH 缺少
  `npx`，因此使用 Codex bundled Node/pnpm 启动；确认 Web Inspector 正常运行后停止。
- [x] P0-P2 单元回归通过；P3-B 完成后全量回归 51 项通过。

### P3-B 验收细节（WorkBuddy，2026-09-01）

- 修改文件：`src/agent_memory_hub/mcp_server.py`（新增）、
  `tests/test_mcp_tools.py`（新增）、`pyproject.toml`（仅加 `mcp[cli]`）、
  `uv.lock`（mcp 2.1.1 + mcp-types 2.1.1 等锁定）。
- 未修改：`service.py`、`schemas.py`、`errors.py`、`README.md`。
- MCP SDK：`mcp==2.1.1`（v2），`mcp-types==2.1.1`。
- 测试结果：`uv run pytest tests/test_mcp_tools.py` → 17 passed；
  `uv run pytest` → 50 passed；`uv run ruff check .` → All checks passed。
- CLI / Inspector 命令：
  - 直接运行（stdio）：`uv run python -m agent_memory_hub.mcp_server`
  - CLI 加载：`uv run mcp run src/agent_memory_hub/mcp_server.py`
  - Inspector：`uv run mcp dev src/agent_memory_hub/mcp_server.py`
    （首次运行会经 npm 拉取 `@modelcontextprotocol/inspector`）
- 供 Codex 联调的服务装配：真实 Mem0 在首次 `add/search` 时懒加载，失败返回
  `MEM0_UNAVAILABLE` 降级结果（Project State 不受影响）；测试注入
  fake service 的方式为 `create_server(fake_service)`。

### Codex 最终复核（2026-09-01）

- 新增 `scripts/test_mcp_stdio.py`，将真实 stdio 握手、工具发现与结构化错误
  验证固化为一键 smoke test。
- WorkBuddy 原实现会在第一个任意 tool 调用时初始化 Mem0。现已增加线程安全的
  `LazyMemoryEngine`，把初始化推迟到真正的 `add/search`，保证 bootstrap 不受
  HF 网络或 embedding 加载延迟影响。
- 最终结果：MCP 专项 18 passed；全项目 51 passed；Ruff passed；stdio smoke
  passed；Inspector 启动通过。
