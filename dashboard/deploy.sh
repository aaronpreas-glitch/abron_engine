#!/usr/bin/env bash
# dashboard/deploy.sh — Build React and push dashboard to VPS
# Usage (from project root):
#   bash dashboard/deploy.sh              # full deploy
#   bash dashboard/deploy.sh --backend    # backend only, restart service
#   bash dashboard/deploy.sh --frontend   # build + upload dist only

set -euo pipefail

SERVER="root@68.183.148.183"
SSH_KEY="$HOME/.ssh/memecoin_deploy"
REMOTE_ROOT="/root/memecoin_engine/dashboard"
LOCAL_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "🚀 Memecoin Dashboard Deploy"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

MODE="${1:-all}"

if [[ "$MODE" == "all" || "$MODE" == "--frontend" ]]; then
    echo "📦 Building React..."
    (cd "$LOCAL_DIR/frontend" && npm run build)
    echo "📤 Uploading dist/ → VPS..."
    ssh -i "$SSH_KEY" "$SERVER" "mkdir -p '$REMOTE_ROOT/frontend/dist/assets'"
    # Keep prior hashed assets available for stale browser bundles during rollout.
    rsync -avz -e "ssh -i $SSH_KEY" --delete --exclude 'assets/' \
        "$LOCAL_DIR/frontend/dist/" "$SERVER:$REMOTE_ROOT/frontend/dist/"
    rsync -avz -e "ssh -i $SSH_KEY" \
        "$LOCAL_DIR/frontend/dist/assets/" "$SERVER:$REMOTE_ROOT/frontend/dist/assets/"
    echo "  ✓ frontend/dist uploaded (hashed assets preserved for cache-safe rollout)"
fi

if [[ "$MODE" == "all" || "$MODE" == "--backend" ]]; then
    echo "📤 Uploading backend/ → VPS..."
    rsync -avz -e "ssh -i $SSH_KEY" --exclude '__pycache__' --exclude '*.pyc' \
        "$LOCAL_DIR/backend/" "$SERVER:$REMOTE_ROOT/backend/"
    echo "  ✓ backend/ uploaded"
    echo "🔄 Restarting memecoin-dashboard..."
    ssh -i "$SSH_KEY" "$SERVER" "systemctl restart memecoin-dashboard"
    sleep 4
    ssh -i "$SSH_KEY" "$SERVER" "systemctl is-active memecoin-dashboard && echo OK || echo FAILED"
    ssh -i "$SSH_KEY" "$SERVER" "journalctl -u memecoin-dashboard -n 6 --no-pager 2>&1"
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "✅ Done → http://$SERVER:8888"
