#!/usr/bin/env bash
set -euo pipefail

ENGINE_ROOT="${MEMECOIN_ENGINE_ROOT:-/root/memecoin_engine}"
BACKUP_ROOT="${MEMECOIN_BACKUP_ROOT:-$ENGINE_ROOT/backups}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
DEST="$BACKUP_ROOT/$STAMP"

mkdir -p "$DEST"
chmod 700 "$BACKUP_ROOT" "$DEST"

if [[ -f "$ENGINE_ROOT/data_storage/engine.db" ]]; then
  if command -v sqlite3 >/dev/null 2>&1; then
    sqlite3 "$ENGINE_ROOT/data_storage/engine.db" ".backup '$DEST/engine.db'"
  else
    cp "$ENGINE_ROOT/data_storage/engine.db" "$DEST/engine.db"
  fi
fi

if [[ -f "$ENGINE_ROOT/.env" ]]; then
  cp "$ENGINE_ROOT/.env" "$DEST/env.snapshot"
  chmod 600 "$DEST/env.snapshot"
fi

if [[ -d "$ENGINE_ROOT/logs" ]]; then
  tar -C "$ENGINE_ROOT" -czf "$DEST/logs.tgz" logs
fi

find "$BACKUP_ROOT" -mindepth 1 -maxdepth 1 -type d -mtime +14 -exec rm -rf {} +
echo "backup_created=$DEST"
