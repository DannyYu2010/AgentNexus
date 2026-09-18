#!/bin/zsh
set -euo pipefail

ROOT="${0:A:h:h}"
QDRANT_BIN="${QDRANT_BIN:-$HOME/.local/bin/qdrant}"
PLIST="$HOME/Library/LaunchAgents/com.agentnexus.qdrant.plist"
LOG_DIR="$HOME/Library/Logs/AgentNexus"
UID_VALUE="$(id -u)"

if [[ ! -x "$QDRANT_BIN" ]]; then
  echo "Qdrant binary is missing or not executable: $QDRANT_BIN" >&2
  exit 1
fi

mkdir -p "${PLIST:h}" "$LOG_DIR" "$ROOT/data/qdrant-server"

TMP_PLIST="$(mktemp)"
trap 'rm -f "$TMP_PLIST"' EXIT

sed \
  -e "s|__QDRANT_BIN__|$QDRANT_BIN|g" \
  -e "s|__PROJECT_ROOT__|$ROOT|g" \
  -e "s|__LOG_DIR__|$LOG_DIR|g" \
  "$ROOT/config/com.agentnexus.qdrant.plist.template" > "$TMP_PLIST"

plutil -lint "$TMP_PLIST"
install -m 0644 "$TMP_PLIST" "$PLIST"

launchctl bootout "gui/$UID_VALUE/com.agentnexus.qdrant" 2>/dev/null || true
for _ in {1..40}; do
  if ! launchctl print "gui/$UID_VALUE/com.agentnexus.qdrant" >/dev/null 2>&1; then
    break
  fi
  sleep 0.1
done

for attempt in {1..20}; do
  if launchctl bootstrap "gui/$UID_VALUE" "$PLIST"; then
    break
  fi
  if [[ "$attempt" -eq 20 ]]; then
    echo "Qdrant LaunchAgent could not be loaded after 20 attempts" >&2
    exit 1
  fi
  sleep 0.1
done
launchctl kickstart -k "gui/$UID_VALUE/com.agentnexus.qdrant"

for _ in {1..30}; do
  if curl -fsS http://127.0.0.1:6333/healthz >/dev/null 2>&1; then
    echo "Qdrant Server is ready at http://127.0.0.1:6333"
    exit 0
  fi
  sleep 0.2
done

echo "Qdrant did not become ready; inspect $LOG_DIR/qdrant.err.log" >&2
exit 1
