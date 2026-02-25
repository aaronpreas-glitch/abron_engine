#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
#  deploy.sh — one-command deploy for memecoin_engine
#
#  Usage:
#    ./deploy.sh                    # deploys main.py + jupiter_perps.py
#    ./deploy.sh main.py            # deploys only main.py
#    ./deploy.sh jupiter_perps.py   # deploys only jupiter_perps.py
#    ./deploy.sh main.py jupiter_perps.py utils/format.py
# ─────────────────────────────────────────────────────────────

set -e

SERVER="root@68.183.148.183"
SERVER_KEY="$HOME/.ssh/memecoin_deploy"
PROJECT_LOCAL="/Users/abron/memecoin_engine"
PROJECT_REMOTE="/root/memecoin_engine"
PATCH_TMP="/tmp/patch_deploy_$$.py"

# SSH opts — use key if available, else password prompt
SSH_OPTS="-o StrictHostKeyChecking=no -o ConnectTimeout=15"
if [ -f "$SERVER_KEY" ]; then
    SSH_OPTS="$SSH_OPTS -i $SERVER_KEY"
fi

# Default files if none specified
if [ $# -eq 0 ]; then
    FILES=("main.py" "jupiter_perps.py")
else
    FILES=("$@")
fi

echo "🚀 Memecoin Engine Deploy"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "   Server : $SERVER"
echo "   Files  : ${FILES[*]}"
echo ""

# ── Build patch script ────────────────────────────────────────
python3 -c "
import base64, os, sys

project_local  = '$PROJECT_LOCAL'
project_remote = '$PROJECT_REMOTE'
file_list      = '${FILES[*]}'.split()

entries = {}
for fname in file_list:
    local_path = os.path.join(project_local, fname)
    if not os.path.exists(local_path):
        print(f'  ❌ Not found: {local_path}')
        sys.exit(1)
    with open(local_path, 'rb') as fh:
        entries[fname] = base64.b64encode(fh.read()).decode()
    kb = os.path.getsize(local_path) / 1024
    print(f'  ✓ {fname}  ({kb:.1f} KB)')

lines = [
    '#!/usr/bin/env python3',
    'import base64, os',
    f'PROJECT = \"{project_remote}\"',
    'FILES = {',
]
for fname, enc in entries.items():
    lines.append(f'    \"{fname}\": \"\"\"{enc}\"\"\",')
lines += [
    '}',
    'for fname, enc in FILES.items():',
    '    path = os.path.join(PROJECT, fname)',
    '    os.makedirs(os.path.dirname(path), exist_ok=True)',
    '    with open(path, \"wb\") as f:',
    '        f.write(base64.b64decode(enc))',
    '    print(f\"  [OK] {fname}\")',
    'print(\"patch done\")',
]
with open('$PATCH_TMP', 'w') as f:
    f.write('\n'.join(lines))
"

echo ""
echo "📤 Uploading patch..."
scp $SSH_OPTS "$PATCH_TMP" "$SERVER:/tmp/patch_deploy.py"

echo "⚙️  Applying patch on server..."
ssh $SSH_OPTS "$SERVER" "python3 /tmp/patch_deploy.py && rm -f /tmp/patch_deploy.py"

echo ""
echo "🔄 Restarting service..."
ssh $SSH_OPTS "$SERVER" "systemctl restart memecoin-engine && sleep 3 && systemctl status memecoin-engine --no-pager | head -4"

rm -f "$PATCH_TMP"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "✅ Done"
