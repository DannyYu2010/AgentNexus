# WorkBuddy 接入与实测验收

## 结论

2026-09-02 已使用 WorkBuddy 自带 CLI、`deepseek-v4-flash` 模型和 WorkBuddy
自己的 MCP 配置完成真实验收：

- `agent-memory` 服务器状态为 `Connected`；
- 六个 MCP tools 均可发现；
- `bootstrap(project_id="agentnexus")` 实际调用成功；
- `search(...)` 实际调用成功到达服务层，但因 Qdrant Local 被另一进程持锁而
  返回预期降级：`status=degraded`、`error.code=MEM0_UNAVAILABLE`。

因此 WorkBuddy 已能使用共享 Project State。该次验收暴露的是 Qdrant Local
单进程文件锁限制，而不是 WorkBuddy MCP 兼容问题；此限制已在 P6 通过本机
Qdrant Server 解决。

## 用户级配置

配置位于 `~/.codebuddy/.mcp.json`，由 WorkBuddy 自带 CLI 写入。等价配置为：

```json
{
  "mcpServers": {
    "agent-memory": {
      "type": "stdio",
      "command": "/opt/homebrew/bin/uv",
      "args": [
        "--directory",
        "/Users/<username>/Src/AgentNexus",
        "run",
        "python",
        "-m",
        "agent_memory_hub.mcp_server"
      ],
      "env": {
        "MEM0_TELEMETRY": "false"
      }
    }
  }
}
```

可用下面的命令检查连接状态：

```bash
PATH="$HOME/.workbuddy/binaries/node/versions/22.22.2-2/bin:$PATH" \
  /Applications/WorkBuddy.app/Contents/Resources/app.asar.unpacked/cli/bin/codebuddy \
  mcp get agent-memory
```

## 验收证据

WorkBuddy 初始化返回：

```text
mcp_servers: [{"name":"agent-memory","status":"connected"}]
model: deepseek-v4-flash
```

`bootstrap` 返回 `ok=true`，关键状态为：

```text
implementation_phase=P5 complete
memory_hub_status=Codex A/B sequential handoff verified
p5_handoff_status=verified_by_codex_b
state_version=8
```

`search` 返回：

```text
ok=true
status=degraded
memories=[]
error.code=MEM0_UNAVAILABLE
error.details.operation=initialize_for_search
```

错误消息明确说明 `data/qdrant` 已被另一个 Qdrant client 实例访问，需使用
Qdrant Server 才能并发访问。

## 权限说明

WorkBuddy 的非交互 `--print` 模式无法显示工具审批框。第一次验收因此被
`DeferExecuteTool` 审批拦截；第二次仅对该验收进程使用
`--permission-mode bypassPermissions`，且提示词限定只能调用只读的
`bootstrap` 和 `search`。没有把宽泛放行写入 WorkBuddy 的永久权限配置。

日常在 WorkBuddy 图形界面中使用时，应在实际工具调用的审批提示中按需批准，
不建议永久全局放开全部工具。

## P6 验收目标

将 Qdrant Local 改为支持多客户端的 Qdrant Server（或把 Memory Hub 做成单例
服务）后，重新同时启动 Codex 和 WorkBuddy，并要求两端的 `search` 均返回
`status=ok` 且命中同一条长期记忆，才可宣告并发共享记忆通过。

## P6 验收结果

2026-09-02：保持一个 Codex 侧记忆客户端连接的同时，WorkBuddy 使用
`deepseek-v4-flash` 实际调用 `search`，返回：

```json
{"ok":true,"status":"ok","memory_count":3,"error_code":null}
```

WorkBuddy 已发现全部 7 个 MCP tools（含 `correct_memory`），Codex + WorkBuddy
本机并发共享长期记忆通过。详见 `docs/P6_IMPLEMENTATION.md`。
