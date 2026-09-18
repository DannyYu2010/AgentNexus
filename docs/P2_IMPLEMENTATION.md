# P2：Qdrant 持久化与重启验收

## 目标

P2 只验证本地长期记忆存储的可靠性，不引入 MCP 或 Web 服务：

- 写入记忆的 Python 进程退出后，新进程仍能检索到原记忆
- 不同 `project_id` 之间的记忆互不可见
- 已有集合的向量维度与当前配置不一致时，启动立即返回稳定错误
- 提供一条可重复执行的验收命令

## 进度记录

- [x] 2026-09-01：核对 P1 配置、现有数据目录和 Qdrant 集合；确认
  `agent_memories` 使用 1024 维 Cosine 向量并启用磁盘存储。
- [x] 2026-09-01：新增 `scripts/test_persistence.py`；写入子进程退出后，
  全新读取子进程重新打开同一 Qdrant 并成功召回记忆。
- [x] 2026-09-01：读取进程使用另一个 `project_id` 检索，结果为空，
  确认项目隔离生效。
- [x] 2026-09-01：实现向量维度预检；不匹配时返回稳定错误
  `EMBEDDING_DIMENSION_MISMATCH`，匹配与不匹配测试均已通过。
- [x] 2026-09-01：全量回归完成；`ruff` 无问题，14 项单元测试通过，
  P0 smoke test 通过。
- [x] 2026-09-01：完成 P2 真实联调；重开后召回“固定 7 秒重连”，
  `source_event_id=evt-p2-persistence`，项目 B 无越界结果。

## 验收命令

```bash
uv run python scripts/test_persistence.py
```

脚本使用独立目录 `data/p2-smoke`，不会修改 P1 的
`data/qdrant/agent_memories` 数据。

## 验收行为

1. `write` 子进程通过本地千问提取记忆并写入 Qdrant。
2. `write` 子进程退出，释放 Qdrant Local 文件锁。
3. 全新的 `read` 子进程重新加载相同目录并检索项目 A。
4. `read` 子进程检索项目 B，必须返回空结果。

重复执行时，Mem0 可能识别出已有相同记忆并返回空的新增列表；脚本以
“重开后能否召回”为验收依据，因此重复运行仍然有效。

spaCy 和 fastembed/BM25 是可选的关键词检索增强。未安装时 Mem0 会输出
提示，本阶段的 Qwen3-Embedding 稠密向量检索仍可正常完成验收。
