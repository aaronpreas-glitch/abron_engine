# Memecoin Engine — VPS Operations Guide

> **VPS**: `root@68.183.148.183` · key: `~/.ssh/memecoin_deploy`
> **Engine root**: `/root/memecoin_engine/`
> **Dashboard**: `http://68.183.148.183:8000`
> **Password**: `HArden978ab`

---

## Quick Reference

```bash
# SSH in
ssh -i ~/.ssh/memecoin_deploy root@68.183.148.183

# Service
systemctl status memecoin-dashboard
systemctl restart memecoin-dashboard
journalctl -u memecoin-dashboard -n 50 --no-pager

# Get auth token
TOKEN=$(curl -s -X POST http://localhost:8000/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"password":"HArden978ab"}' | python3 -c 'import sys,json; print(json.load(sys.stdin)["token"])')

# System health
curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/health/status | python3 -m json.tool

# Agent status
curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/orchestrator/status \
  | python3 -c "import sys,json; [print(a['name'], a['health']) for a in json.load(sys.stdin)['agents']]"

# Tier positions
curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/tiers/status | python3 -m json.tool
```

---

## Tier System

```
3x  SOL LONG  $50 col  Diamond hands — no TP, holds forever
5x  BTC LONG  $20 col  TP +20% raw → closes → re-enters → profits go to buffer
10x ETH LONG  $10 col  TP +10% raw → closes → re-enters → re-enters after liq
```

**Open tiers via dashboard buttons or API:**
```bash
curl -s -X POST -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/tiers/open/3x
curl -s -X POST -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/tiers/open/5x
curl -s -X POST -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/tiers/open/10x
curl -s -X POST -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/tiers/open-all
```

**Profit buffer**: 5x/10x TP profits accumulate → auto-opens new 3x at $50, new 5x at $20.

---

## Memecoin System (Paper Mode)

**Current mode**: `MEMECOIN_DRY_RUN=true` — ALL buys are paper trades (no real money ever).

```
MEMECOIN_AUTO_BUY=false       Gate — set true to enable auto-buying
MEMECOIN_DRY_RUN=true         Paper mode hardlock — NEVER change without deliberate decision
MEMECOIN_BUY_USD=15           USD per paper buy
MEMECOIN_MAX_OPEN=3           Max concurrent paper positions
MEMECOIN_BUY_SCORE_MIN=65     Score gate (tuner overrides when confident)
```

**Auto-buy gates** (ALL must pass for a buy to fire):
1. `MEMECOIN_AUTO_BUY=true`
2. Open positions < `MEMECOIN_MAX_OPEN`
3. Fear & Greed > 25 (not extreme fear)
4. Score >= threshold (from tuner or env default)
5. rug_label == "GOOD"
6. buy_pressure >= 55%
7. mint_revoked == True

**Learning loop**: Scanner runs every 5min → logs outcomes → 1h/4h/24h returns fill automatically → once 20+ COMPLETE rows exist, auto-tuner adjusts thresholds.

---

## Agent System

6 agents run in a continuous loop inside `_perp_monitor_loop`:

| Agent | Interval | What it does |
|-------|----------|-------------|
| `trading` | 60s | Main perp signal scanner (disabled: SWING_4H_THRESHOLD=99) |
| `monitoring` | 60s | Monitors open perp positions for TP/SL/TIME_LIMIT |
| `memecoin_monitor` | 60s | Paper buys + monitors open memecoin trades |
| `health_watchdog` | 60s | DB/agent/scan freshness checks → kv_store['system_health'] |
| `data_integrity` | 5min | Outcome fill rate + scan freshness + position sanity |
| `research` | 4h | Synthesizes learning data → writes to MEMORY.md |

**Telegram alerts fire on**: memecoin buy/sell, tier TP hit, buffer deploy, research synthesis, CRITICAL/DEGRADED health.

---

## Deploy Workflow

### Backend-only change
```bash
# 1. Edit file locally
# 2. SCP to VPS
scp -i ~/.ssh/memecoin_deploy utils/some_file.py root@68.183.148.183:/root/memecoin_engine/utils/some_file.py

# 3. Restart
ssh -i ~/.ssh/memecoin_deploy root@68.183.148.183 "systemctl restart memecoin-dashboard"

# 4. Verify
ssh -i ~/.ssh/memecoin_deploy root@68.183.148.183 "systemctl is-active memecoin-dashboard"
```

### Frontend change (ALWAYS required when any .tsx changes)
```bash
# 1. Build
cd /Users/abron/memecoin_engine/dashboard/frontend && npm run build

# 2. Rsync (replaces dist/ entirely)
rsync -av --delete -e "ssh -i ~/.ssh/memecoin_deploy" \
  dist/ root@68.183.148.183:/root/memecoin_engine/dashboard/frontend/dist/

# 3. Restart
ssh -i ~/.ssh/memecoin_deploy root@68.183.148.183 "systemctl restart memecoin-dashboard"
```

> **index.html is served with `no-store`** — browser always fetches fresh. Stale JS cache is impossible.

### .env changes
```bash
# Edit on VPS
ssh -i ~/.ssh/memecoin_deploy root@68.183.148.183 "echo 'KEY=value' >> /root/memecoin_engine/.env"

# ALWAYS restart after .env change — env vars are read at startup
ssh -i ~/.ssh/memecoin_deploy root@68.183.148.183 "systemctl restart memecoin-dashboard"
```

---

## Key .env Variables

```bash
# Perps
PERP_DRY_RUN=false            # LIVE — real money
SWING_4H_THRESHOLD=99         # Signal scanner disabled (set 0.5 to re-enable)
MAX_OPEN_PERPS=10

# Tiers
TIER_3X_SYMBOL=SOL  TIER_3X_LEVERAGE=3   TIER_3X_NOTIONAL=150
TIER_5X_SYMBOL=BTC  TIER_5X_LEVERAGE=5   TIER_5X_NOTIONAL=100  TIER_5X_TP_PCT=20
TIER_10X_SYMBOL=ETH TIER_10X_LEVERAGE=10 TIER_10X_NOTIONAL=100 TIER_10X_TP_PCT=10
PROFIT_BUFFER_3X_THRESHOLD=50
PROFIT_BUFFER_5X_THRESHOLD=20

# Memecoins (paper mode)
MEMECOIN_AUTO_BUY=false
MEMECOIN_DRY_RUN=true         # NEVER flip without deliberate decision
MEMECOIN_BUY_USD=15
MEMECOIN_MAX_OPEN=3
MEMECOIN_BUY_SCORE_MIN=65

# Notifications
TELEGRAM_TOKEN=...
TELEGRAM_CHAT_ID=...
```

---

## Database

```bash
# Connect
sqlite3 /root/memecoin_engine/data_storage/engine.db

# Key tables
SELECT * FROM perp_positions WHERE status='OPEN';
SELECT * FROM memecoin_trades WHERE status='OPEN';
SELECT * FROM memecoin_signal_outcomes ORDER BY scanned_at DESC LIMIT 20;
SELECT key, value FROM kv_store WHERE key IN ('system_health','shared_fear_greed','tier_profit_buffer');
```

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| Dashboard not loading | `systemctl restart memecoin-dashboard` |
| Stale JS in browser | Hard refresh (`Cmd+Shift+R`) — but this is now impossible (no-store header) |
| Agent shows stalled | Check `journalctl -u memecoin-dashboard -n 100` for Python errors |
| Tier open fails | Check Jupiter API status; verify wallet has SOL for fees |
| Liq detection false positive | Check if position is < 10 min old (grace period) |
| Standalone script fails | `set -a && source .env && set +a && python3 script.py` |
| Circuit breaker stale | `cat /root/memecoin_engine/cb_state.txt` — delete if stale |
