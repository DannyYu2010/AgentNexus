#!/bin/zsh
set -euo pipefail

ROOT="${0:A:h:h}"
PACKAGE_DIR="$ROOT/macos/AgentNexusMemory"
# .noindex keeps Spotlight out: an indexed build artifact shows up next to
# the installed app under the same name, and launching the wrong one means
# debugging a stale build.
DIST_DIR="$ROOT/dist.noindex"
APP_NAME="AgentNexus Memory.app"
OUTPUT_APP="$DIST_DIR/$APP_NAME"
STAGING_DIR="$(mktemp -d)"
STAGED_APP="$STAGING_DIR/$APP_NAME"
ICONSET="$STAGING_DIR/AppIcon.iconset"

trap 'rm -rf "$STAGING_DIR"' EXIT

swift build -c release --package-path "$PACKAGE_DIR"
BIN_DIR="$(swift build -c release --package-path "$PACKAGE_DIR" --show-bin-path)"

mkdir -p "$STAGED_APP/Contents/MacOS" "$STAGED_APP/Contents/Resources" "$ICONSET"
install -m 0755 "$BIN_DIR/AgentNexusMemory" "$STAGED_APP/Contents/MacOS/AgentNexusMemory"
install -m 0644 "$PACKAGE_DIR/Info.plist" "$STAGED_APP/Contents/Info.plist"
install -m 0755 "$ROOT/scripts/agent_memory_control.py" "$STAGED_APP/Contents/Resources/agent-memory-control"
install -m 0755 "$ROOT/scripts/agent_memory_launcher.py" "$STAGED_APP/Contents/Resources/agent-memory-launcher"

SOURCE_ICON="$PACKAGE_DIR/Assets/AppIcon.png"
for size in 16 32 128 256 512; do
  sips -z "$size" "$size" "$SOURCE_ICON" --out "$ICONSET/icon_${size}x${size}.png" >/dev/null
  double_size=$((size * 2))
  sips -z "$double_size" "$double_size" "$SOURCE_ICON" --out "$ICONSET/icon_${size}x${size}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o "$STAGED_APP/Contents/Resources/AppIcon.icns"

plutil -lint "$STAGED_APP/Contents/Info.plist"
codesign --force --deep --sign - "$STAGED_APP"

mkdir -p "$DIST_DIR"
if [[ -e "$OUTPUT_APP" ]]; then
  rm -rf "$OUTPUT_APP"
fi
ditto "$STAGED_APP" "$OUTPUT_APP"
codesign --verify --deep --strict "$OUTPUT_APP"

echo "$OUTPUT_APP"
