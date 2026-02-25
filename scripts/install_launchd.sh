#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEMPLATE="$PROJECT_DIR/deploy/com.memecoin.engine.plist.template"
PLIST_NAME="com.memecoin.engine.plist"
LABEL="com.memecoin.engine"
TARGET_PLIST="$HOME/Library/LaunchAgents/$PLIST_NAME"
USER_ID="$(id -u)"
LAUNCHD_TARGET="gui/$USER_ID/$LABEL"

mkdir -p "$PROJECT_DIR/logs" "$HOME/Library/LaunchAgents"

if [[ ! -f "$TEMPLATE" ]]; then
  echo "Template not found: $TEMPLATE"
  exit 1
fi

sed "s|__PROJECT_DIR__|$PROJECT_DIR|g" "$TEMPLATE" > "$TARGET_PLIST"

launchctl bootout "gui/$USER_ID" "$TARGET_PLIST" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$USER_ID" "$TARGET_PLIST"
launchctl kickstart -k "$LAUNCHD_TARGET" >/dev/null 2>&1 || true

echo "Installed launchd agent: $TARGET_PLIST"

# launchctl state can take a moment to appear after bootstrap/kickstart.
status_dump=""
for _ in 1 2 3 4 5; do
  status_dump="$(launchctl print "$LAUNCHD_TARGET" 2>/dev/null || true)"
  if [[ "$status_dump" == *"state = running"* ]]; then
    break
  fi
  sleep 1
done

if [[ "$status_dump" == *"state = running"* ]]; then
  pid_line="$(awk -F'= ' '/^[[:space:]]*pid = / {print $2; exit}' <<<"$status_dump")"
  echo "Status:"
  if [[ -n "$pid_line" ]]; then
    echo "$pid_line 0 $LABEL"
  else
    echo "running $LABEL"
  fi
else
  echo "Status: $LABEL not running yet (check: launchctl print $LAUNCHD_TARGET)"
fi
