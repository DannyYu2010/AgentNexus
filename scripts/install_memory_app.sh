#!/bin/zsh
set -euo pipefail

ROOT="${0:A:h:h}"
APP_NAME="AgentNexus Memory.app"
BUILT_APP="$ROOT/dist.noindex/$APP_NAME"
INSTALLED_APP="/Applications/$APP_NAME"
SUPPORT_DIR="$HOME/Library/Application Support/AgentNexus Memory"

"$ROOT/scripts/build_memory_app.sh" >/dev/null

if [[ -e "$INSTALLED_APP" ]]; then
  stamp="$(date +%Y%m%d-%H%M%S)"
  backup_dir="$SUPPORT_DIR/app-backups.noindex/$stamp"
  mkdir -p "$backup_dir"
  mv "$INSTALLED_APP" "$backup_dir/$APP_NAME"
fi

ditto "$BUILT_APP" "$INSTALLED_APP"
codesign --verify --deep --strict "$INSTALLED_APP"

"$INSTALLED_APP/Contents/Resources/agent-memory-control" \
  --json install-clients \
  --launcher "$INSTALLED_APP/Contents/Resources/agent-memory-launcher"

open "$INSTALLED_APP"
echo "$INSTALLED_APP"
