#!/bin/zsh
# Collects everything needed to explain why Qdrant or the MCP server is not
# running. Read-only: it starts and stops nothing.
set -u

ROOT="${0:A:h:h}"
UID_VALUE="$(id -u)"
SUPPORT="$HOME/Library/Application Support/AgentNexus Memory"
APP="/Applications/AgentNexus Memory.app"
CTL="$APP/Contents/Resources/agent-memory-control"
LAUNCHER="$APP/Contents/Resources/agent-memory-launcher"

section() { print -r -- ""; print -r -- "===== $1 ====="; }

section "recorded intent"
for f in mode.json qwen-switch.json qwen-owner.json; do
  print -r -- "--- $f"
  cat "$SUPPORT/$f" 2>&1
done

section "launchd disabled overrides (persists across reboot)"
launchctl print-disabled "gui/$UID_VALUE" 2>&1 | grep -i agentnexus

section "launchd jobs"
launchctl list 2>&1 | grep -i -e agentnexus -e mlx -e qwen

section "qdrant service detail"
launchctl print "gui/$UID_VALUE/com.agentnexus.qdrant" 2>&1 | head -30

section "LaunchAgents plists"
ls -l "$HOME/Library/LaunchAgents" 2>&1

section "ports"
lsof -nP -iTCP:6333 -sTCP:LISTEN 2>&1
lsof -nP -iTCP:8080 -sTCP:LISTEN 2>&1

section "health"
curl -sS -m 3 -o /dev/null -w 'qdrant healthz http=%{http_code}\n' http://127.0.0.1:6333/healthz 2>&1
curl -sS -m 3 -o /dev/null -w 'qwen models  http=%{http_code}\n' http://127.0.0.1:8080/v1/models 2>&1

section "qdrant logs (tail)"
tail -20 "$HOME/Library/Logs/AgentNexus/qdrant.err.log" 2>&1
print -r -- "--- stdout"
tail -10 "$HOME/Library/Logs/AgentNexus/qdrant.out.log" 2>&1

section "installed app"
ls -ld "$APP" 2>&1
print -r -- "control mtime:"; ls -l "$CTL" 2>&1
print -r -- "repo control mtime:"; ls -l "$ROOT/scripts/agent_memory_control.py" 2>&1
print -r -- "identical to repo: $(cmp -s "$CTL" "$ROOT/scripts/agent_memory_control.py" && print yes || print NO)"

section "controller status"
"$CTL" --json status 2>&1

section "launcher direct spawn"
"$LAUNCHER" </dev/null >/tmp/agentnexus-launcher.out 2>/tmp/agentnexus-launcher.err
print -r -- "exit=$?"
print -r -- "--- stdout"; head -5 /tmp/agentnexus-launcher.out
print -r -- "--- stderr"; head -20 /tmp/agentnexus-launcher.err

section "codex mcp config"
sed -n '/\[mcp_servers.agent-memory\]/,/^\[/p' "$HOME/.codex/config.toml" 2>&1

section "uv"
ls -l /opt/homebrew/bin/uv 2>&1
