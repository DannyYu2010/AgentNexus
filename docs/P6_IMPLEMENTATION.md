# P6：并发、纠错与备份恢复

## 范围

原设计的 P6 通过条件为：

- 并发版本冲突正确；
- 纠错可追溯；
- 备份可恢复。

P5 额外实测发现，多个 stdio MCP 进程不能同时打开同一个 Qdrant Local 目录。
因此 P6 同时将向量存储切换为只监听本机的 Qdrant Server，满足 Codex 与
WorkBuddy 并发访问这一 V1 实际目标。

## 1. Qdrant Server

- 版本：Qdrant `1.19.0`，官方 macOS ARM64 Release 二进制；
- 二进制：`~/.local/bin/qdrant`；
- SHA-256：`4e279a80cc1ebe73e859318ff86375af54c123887dd7ae46605c0eb6cb7c44e8`；
- HTTP：`http://127.0.0.1:6333`；
- gRPC：关闭；
- 遥测：关闭；
- 数据：`data/qdrant-server/storage`；
- 快照：`data/qdrant-server/snapshots`；
- LaunchAgent：`~/Library/LaunchAgents/com.agentnexus.qdrant.plist`；
- 日志：`~/Library/Logs/AgentNexus/qdrant.out.log` 和 `qdrant.err.log`。

项目配置见 `config/qdrant.yaml`，安装/重载命令：

```bash
./scripts/install_qdrant_launch_agent.sh
curl -fsS http://127.0.0.1:6333/healthz
```

`.env` 使用：

```dotenv
QDRANT_MODE=server
QDRANT_HOST=127.0.0.1
QDRANT_PORT=6333
```

`QDRANT_MODE=local` 仍保留给隔离测试和回退使用。Mem0、Project State、Qwen 和
embedding 的其他配置不变。

## 2. 数据迁移

迁移脚本：

```bash
uv run python scripts/migrate_qdrant_local_to_server.py
```

2026-09-02 实际迁移 3 个 points。迁移后逐条核对原 memory ID、文本和
`project_id` 均不变。旧 `data/qdrant` 目录未删除，可作为迁移前回退副本。

脚本默认拒绝覆盖已存在的 Server collection；只有人工明确传入 `--replace` 才
会替换目标集合。

## 3. 并发验收

验收时保持一个 Codex 侧 `MemoryEngine` 客户端持续连接，同时启动 WorkBuddy
CLI（`deepseek-v4-flash`）调用同一 MCP：

```json
{"ok":true,"status":"ok","memory_count":3,"error_code":null}
```

WorkBuddy 成功召回 Codex A/P4 记忆；不再出现
`Storage folder ... already accessed` 或 `MEM0_UNAVAILABLE`。WorkBuddy 同时发现
全部 7 个 tools，包括新增的 `correct_memory`。

## 4. correct_memory

新增第七个 MCP tool：

```text
correct_memory(
  project_id,
  memory_id,
  correction,
  action="update" | "delete",
  session_id?,
  agent_id?,
  state_updates?,
  expected_state_version?
)
```

语义：

- 纠错前验证 memory 的 `metadata.project_id`，禁止跨项目按 ID 修改；
- `update` 原位更新文本，Mem0 history 保留 `old_memory/new_memory`；
- `delete` 删除错误 memory，并用 `infer=false` 写入用户给出的明确纠正记录；
- 每次请求先保存 raw event，成功标 `done`，失败标 `pending`；
- 成功或 Mem0 降级都会写 work log；
- 可选 State 更新必须携带 `expected_state_version`，冲突返回 `STATE_CONFLICT`，
  不调用 Mem0，也不写成功 work log；
- 稳定错误包含 `MEMORY_NOT_FOUND`、`MEMORY_PROJECT_MISMATCH`、
  `MEM0_UNAVAILABLE` 和 `MEM0_DISABLED`。

真实纠错验收：memory
`d69eac4a-8830-42a3-81d0-1f6ad7070971` 从 `alpha` 更新为 `beta`；Mem0 history
同时保存 ADD 和 UPDATE 两条记录，raw event 与 work log 均存在。

## 5. 备份与恢复

创建在线一致性备份：

```bash
uv run python scripts/backup.py
```

内容包括：

- SQLite online backup：`project_state.db`；
- SQLite online backup：`mem0_history.db`；
- Qdrant Server collection snapshot；
- `manifest.json`：文件大小和 SHA-256。

snapshot 下载并校验写入备份目录后，脚本会删除 Qdrant Server 侧的临时 snapshot，
避免每次备份在运行目录中再遗留一份大文件。

恢复必须显式指定目标，默认拒绝覆盖：

```bash
uv run python scripts/restore_backup.py backups/<stamp> \
  --project-db-target data/restore/project_state.db \
  --history-db-target data/restore/mem0_history.db \
  --qdrant-collection agent_memories_restore_test
```

2026-09-02 已恢复到隔离目标：两个 SQLite `PRAGMA integrity_check=ok`，Project
DB 含 1 个项目，Mem0 history 含 3 条迁移前记录，恢复集合含 3 个 points。未
覆盖正式数据库或正式 collection。

P6 状态落库后的最终正式备份为 `backups/20260902-013229`，包含
`implementation_phase=P6 complete`、8 条 Mem0 history 和 5 个正式 collection
points。其 Qdrant snapshot SHA-256 为
`fee602b24d0abb12a50ef605e4a3716c1ed953d1ccd31c00ce4d40f1ff8aec9c`。
验收用的两套临时 collection、临时恢复数据库、旧测试备份和 Server 侧临时
snapshot 已删除，只保留正式 collection 与这份最终备份。

## 6. 最终回归

```text
uv run pytest                         60 passed
uv run ruff check .                  All checks passed
uv run python scripts/smoke_test.py  P0 smoke test passed
uv run python scripts/test_mcp_stdio.py
  7 tools discovered
  structured PROJECT_NOT_FOUND verified
```

Qdrant Server、WorkBuddy 并发 search、真实 correct_memory、备份恢复均另做了
真实组件验收，不只是 mock 测试。
