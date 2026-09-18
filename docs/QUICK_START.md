# 共享 Agent 记忆系统日常使用速查

## 你现在能做什么

Codex 和 WorkBuddy 共用一个 Memory Hub。每个工程使用独立、稳定的
`project_id` 隔离状态和长期记忆；换模型、换任务或重新打开会话后，只要告诉
Agent 正确的 `project_id`，它就能接着之前的工作。

## 最推荐：使用 shared-agent-memory Skill

Skill 已安装为用户级能力，新项目不需要复制 Skill 文件，新会话也不需要重新
安装。每次只需在第一句话显式调用。

Codex：

```text
使用 $shared-agent-memory，接手 project_id=agentnexus。
```

WorkBuddy：

```text
请使用 Skill 工具加载 shared-agent-memory，接手 project_id=agentnexus。
```

新项目可以说：

```text
使用 $shared-agent-memory，把当前工程登记为新项目。先检查 ID 冲突，候选
project_id 是 my-project；没有确认前不要写入。
```

Skill 会自动区分“接手现有项目”和“登记新项目”，并遵守
`bootstrap → start_turn → 工作 → finish_turn` 流程。

安装位置：

```text
Codex:          ~/.codex/skills/shared-agent-memory
WorkBuddy App:  ~/.workbuddy/skills/shared-agent-memory
WorkBuddy CLI:  ~/.codebuddy/skills/shared-agent-memory
```

已登记的例子：

```text
工程：AgentNexus
project_id：agentnexus
目录：`$HOME/Src/AgentNexus`（按你的实际克隆位置调整）
```

## 1. 新项目第一次怎么接入

### 选择 project_id

建议使用简短、稳定的小写英文 ID，例如：

```text
freeconnect
etf-research-pro
fdm-g1-next
```

不要使用会经常变化的阶段名、分支名或日期。项目登记后持续使用同一个 ID，
不要把同一个 ID 分配给不同工程。

### 登记项目

假设 AgentNexus 克隆在 `$HOME/Src/AgentNexus`，新工程目录是
`$HOME/Src/MyProject`：

```bash
cd "$HOME/Src/AgentNexus"
uv run python scripts/register_project.py \
  my-project "My Project" \
  --repo-path "$HOME/Src/MyProject"
```

正常输出类似：

```text
Project registered: my-project (My Project)
Database: $HOME/Src/AgentNexus/data/project_state.db
```

重复执行同一个命令是安全的，会显示 `already registered`，不会重复创建。

### 在新工程记录项目 ID

建议在新工程的 `AGENTS.md` 中加入：

```markdown
## Shared Agent Memory

- 本项目的共享记忆 project_id 是 `my-project`。
- 新会话开始先调用 agent-memory bootstrap。
- 用户提出的重要要求先调用 start_turn 保存。
- 当前状态以 get_state/bootstrap 为准，历史通过 search 查询。
- 工作完成后调用 finish_turn 回写结果和 changed_files。
```

如果不想修改新工程文件，也可以在每个新会话的第一句话中明确告诉 Agent
`project_id`。

## 2. 平时怎么开始

打开 Codex 或 WorkBuddy，新会话直接说：

```text
这个工程的 project_id 是 <你的-project_id>。
请先通过 agent-memory bootstrap 接手当前状态，再继续工作。
```

如果需要查以前的决定，再说：

```text
请用 agent-memory search 查一下这个项目以前关于 XXX 的决定。
回答当前状态时，以 bootstrap/get_state 的 Project State 为准。
```

正常情况下不需要你手工运行 Memory Hub。Codex 和 WorkBuddy 会各自启动 MCP
进程，并共同连接本机 Qdrant Server。

电脑重启后，打开 `AgentNexus Memory.app` 即可。App 会读取上次保存的模式并自动
协调组件：完整记忆模式会补启 Qdrant，并检查 MCP 启动器及四个客户端配置；若配置
缺失，会自动恢复。MCP 是 stdio 按需进程，只有客户端真正连接时才会出现进程，
因此“0 个进程”并不表示故障；配置完整时 App 会显示“已就绪 · 等待客户端”。

## 3. 交代任务时怎么说

建议包含三项：项目 ID、明确要求、需要记住的事实。例如：

```text
项目 ID 是 <你的-project_id>。开始处理 XXX。
请记录这条要求：日常优先使用 Codex 和 WorkBuddy，Claude 作为补充。
完成后用 finish_turn 回写改动文件、结果和最新状态。
```

Agent 应按以下顺序工作：

1. `bootstrap`：读取当前状态和最近工作；
2. `start_turn`：保存你的原始要求并提取长期记忆；
3. 必要时 `search` / `get_state`；
4. 完成工作后调用 `finish_turn`。

## 4. 换 Agent 怎么接手

不需要复制旧聊天记录。新开 Codex 或 WorkBuddy 后说：

```text
接手 project_id=<你的-project_id>。先 bootstrap，并告诉我当前阶段、最近完成的工作、
未完成事项和下一步，然后再继续。
```

目前已验证 Codex 与 WorkBuddy 可以同时进行长期记忆搜索。

## 5. 发现记忆写错了

告诉 Agent：

```text
请先搜索并确认那条错误记忆，然后使用 correct_memory 纠正。
错误内容是：XXX
正确内容是：YYY
如果它同时影响当前项目状态，也要带 expected_state_version 更新 State。
```

`correct_memory` 会保留原始事件、修改历史和工作日志。不要直接修改 Qdrant 或
SQLite 文件。

## 6. 查看已经登记的项目

```bash
cd "$HOME/Src/AgentNexus"
sqlite3 -header -column data/project_state.db \
  'SELECT id, name, repo_path FROM projects ORDER BY id;'
```

先从这里确认 ID，再让 Agent `bootstrap`，可避免把内容写入错误项目。

## 7. 每天需要检查吗

通常不需要。怀疑服务有问题时运行：

```bash
cd "$HOME/Src/AgentNexus"
curl -fsS http://127.0.0.1:6333/healthz
```

正常输出：

```text
healthz check passed
```

检查 WorkBuddy 连接：

```bash
PATH="$HOME/.workbuddy/binaries/node/versions/22.22.2-2/bin:$PATH" \
  /Applications/WorkBuddy.app/Contents/Resources/app.asar.unpacked/cli/bin/codebuddy \
  mcp get agent-memory
```

正常状态应为 `Connected`。

## 8. 手工备份

重要阶段完成后运行：

```bash
cd "$HOME/Src/AgentNexus"
uv run python scripts/backup.py
```

命令会输出新备份目录，例如：

```text
$HOME/Src/AgentNexus/backups/20260902-013229
```

备份包括 Project State、Mem0 history、Qdrant snapshot 和 SHA-256 清单。备份
文件较大，建议只保留重要节点。

一次备份会包含所有已登记项目的 Project State、Mem0 history，以及共享 Qdrant
collection，不需要按项目分别备份。

## 9. 常见问题

### 重启后 MCP 或 Qdrant 看起来没有启动

先打开 `AgentNexus Memory.app`，等待几秒，然后点一次刷新。完整记忆模式下，App
启动时会自动补启缺失的 Qdrant，并修复 MCP 客户端启动配置。也可以点“补启缺失组件”
立即重新协调。

MCP 与 Qdrant 的运行方式不同：Qdrant 是常驻服务；MCP 是由 Codex、WorkBuddy、
Claude 等客户端按需拉起的 stdio 进程。没有客户端连接时 MCP 显示“已就绪 · 等待
客户端”属于正常状态。

需要进一步排查时运行：

```bash
CTL="/Applications/AgentNexus Memory.app/Contents/Resources/agent-memory-control"
"$CTL" --json status
tail -n 20 "$HOME/Library/Logs/AgentNexus/reconcile.log"
```

`reconcile.log` 会记录 App 启动前后的状态、实际补启的组件和警告。

### bootstrap 正常，但 search 显示 degraded

先检查 Qdrant：

```bash
curl -fsS http://127.0.0.1:6333/healthz
```

如果失败，重载服务：

```bash
cd "$HOME/Src/AgentNexus"
./scripts/install_qdrant_launch_agent.sh
```

### WorkBuddy 看不到 agent-memory

先完全退出并重新打开 WorkBuddy，再执行上面的 `mcp get agent-memory` 检查。

### bootstrap 返回 PROJECT_NOT_FOUND

表示这个 `project_id` 还没有登记，或者名字输入错了。先查看项目列表；确实是
新工程时，执行第 1 节的 `register_project.py`。

### 不小心用了错误的 project_id

立即停止继续写入，不要自行修改数据库。告诉 Agent 错误 ID、正确 ID和刚才做过
的操作，再决定是否需要用 `correct_memory` 清理错误项目中的记忆。

### 出现 spaCy 或 fastembed 未安装提示

它们是可选增强项，不影响当前使用的 Qwen embedding 稠密向量检索，可以忽略。

### Qwen 没启动

Project State 和 `bootstrap` 仍可用，但新增长期记忆会进入 `pending`。启动本地
Qwen 后，再重新记录或补处理相关内容。

Qwen 有独立开关，不必为了它切换整个模式。在 AgentNexus Memory.app 里直接拨
「Qwen3.8 模型服务」那一行，或者用控制器：

```bash
CTL="/Applications/AgentNexus Memory.app/Contents/Resources/agent-memory-control"
"$CTL" set-qwen on     # 仅启动模型服务，不动模式，也不动 Qdrant
"$CTL" set-qwen off    # 释放显存/内存，其余组件照常
```

开关状态记在 `~/Library/Application Support/AgentNexus Memory/qwen-switch.json`，
不会被状态刷新覆盖。切到「完整记忆」会自动把它打开，切到关闭或仅项目状态会关掉它；
在完整记忆下手动关掉是允许的，App 会以状态偏差的形式提示 Mem0 此时没有模型可用。

## 最简记法

新会话先说：

```text
project_id=<你的-project_id>，先 bootstrap 再干活，完成后 finish_turn。
```

重要决定补一句：

```text
这条需要写入长期记忆。
```

重要阶段结束后运行一次：

```bash
uv run python scripts/backup.py
```
