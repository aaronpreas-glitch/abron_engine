#!/usr/bin/env bash
set -euo pipefail

LABEL="com.memecoin.engine"
USER_ID="$(id -u)"
TARGET="gui/$USER_ID/$LABEL"

status_dump="$(launchctl print "$TARGET" 2>/dev/null || true)"

if [[ -z "$status_dump" ]]; then
  echo "Label: $LABEL"
  echo "State: not loaded"
  exit 1
fi

extract_field() {
  local pattern="$1"
  awk -F'= ' -v pat="$pattern" '$0 ~ pat {print $2; exit}' <<<"$status_dump" | sed 's/[[:space:]]*$//'
}

state="$(extract_field "^[[:space:]]*state = ")"
pid="$(extract_field "^[[:space:]]*pid = ")"
last_exit="$(extract_field "^[[:space:]]*last exit code = ")"
runs="$(extract_field "^[[:space:]]*runs = ")"
execs="$(extract_field "^[[:space:]]*execs = ")"

echo "Label: $LABEL"
echo "State: ${state:-unknown}"
echo "PID: ${pid:-N/A}"
echo "Last Exit: ${last_exit:-N/A}"
echo "Runs: ${runs:-N/A}"
echo "Execs: ${execs:-N/A}"

if [[ "${state:-}" == "running" ]]; then
  exit 0
fi

exit 1
