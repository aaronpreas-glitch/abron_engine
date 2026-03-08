# Memecoin Pilot Runbook
> Patch 177 — Operator-grade procedure for starting, monitoring, pausing, and stopping the live pilot.
> Created: 2026-03-08 | Status: WAITING (NOT_PILOT_READY)

---

## 1. Current Status

```
Verdict: NOT_PILOT_READY
F&G: 18 (Extreme Fear) — needs >35 (+17pt gap)
Post-cooldown sample: 0/20 clean 24h trades (guard activated 2026-03-07)
Risk mode: NORMAL ✓
24h expectancy: +16.97% over 20 trades ✓
24h win rate: 95.0% ✓ (treat with caution — only ~1 loss in sample)
```

**Both blockers are time-gated, not action-gated. No code changes needed — just wait.**

Path to ready:
1. F&G recovers to >35 (market-driven, wait)
2. 20 clean post-cooldown 24h-evaluated trades accumulate (~4–6/day once scanner fires → 3–5 days after F&G opens)

---

## 2. Pilot Parameters (hard limits — not enforced by engine)

| Parameter | Value | Source |
|-----------|-------|--------|
| Max concurrent live positions | **1** | `pilot_constraints` |
| Capital per trade | **0.1 SOL (~$15)** | `pilot_constraints` |
| Daily loss cap | **$25** | `pilot_constraints` |
| Stop loss per trade | **-10%** | `pilot_constraints` |
| Evaluation horizon | **24h** | `pilot_constraints` |
| Pilot duration | **14 days** | `pilot_constraints` |
| Max pilot trades total | **20** | `pilot_constraints` |
| Human review checkpoint | **after 10 trades** | `pilot_constraints` |
| Min F&G at entry | **35** | `pilot_constraints` |
| Excluded regimes | **UNKNOWN** | `pilot_constraints` |

> **Warning:** Engine env has `MEMECOIN_MAX_OPEN=3` and `MEMECOIN_BUY_USD=15`. These must be manually aligned with pilot constraints before enabling (see Step 3 below). The engine does **not** auto-enforce pilot_constraints — the operator does.

---

## 3. Pre-Flight Gate Check

Run this before any enable decision:

```bash
# SSH into VPS
ssh -i ~/.ssh/memecoin_deploy root@68.183.148.183

# Get auth token
TOKEN=$(curl -s -X POST http://localhost:8000/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"password":"HArden978ab"}' | python3 -c 'import sys,json; print(json.load(sys.stdin)["token"])')

# --- Primary check: pilot readiness endpoint ---
curl -s -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/brain/memecoin-pilot-readiness \
  | python3 -m json.tool | grep -A3 '"verdict"\|"active_blockers"\|"path_to"'

# --- Supporting checks ---
cat /root/memecoin_engine/cb_state.txt 2>/dev/null || echo "CB: clear"
curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/orchestrator/status \
  | python3 -c "import sys,json; bad=[a['name'] for a in json.load(sys.stdin)['agents'] if a['health']!='alive']; print('Dead agents:',bad or 'none')"
```

**Do not enable if:**
- Verdict is `NOT_PILOT_READY`
- Any agent is not `alive`
- Circuit breaker (`cb_state.txt`) is not `clear`
- ETH position buffer < 10% (check dashboard — capital preservation mode)

**`PILOT_WATCH`** = all hard gates pass, soft gates warn. Still safe to enable, but suboptimal timing. Use judgment.
**`PILOT_READY`** = all gates pass including soft (F&G >50). Optimal timing.

---

## 4. Pre-Enable Config Alignment

Before flipping to live, verify env vars match pilot constraints:

```bash
grep -E 'MEMECOIN_MAX_OPEN|MEMECOIN_BUY_USD|MEMECOIN_BUY_SCORE_MIN|MEMECOIN_DRY_RUN|MEMECOIN_AUTO_BUY' \
  /root/memecoin_engine/.env
```

**Required before enable:**

| Env var | Should be | Why |
|---------|-----------|-----|
| `MEMECOIN_MAX_OPEN` | `1` | Pilot allows only 1 concurrent position |
| `MEMECOIN_BUY_USD` | `15` | Already correct — 0.1 SOL equiv |
| `MEMECOIN_AUTO_BUY` | `true` | Already correct |
| `MEMECOIN_DRY_RUN` | `false` | The enable switch |

> **Score threshold note:** `MEMECOIN_BUY_SCORE_MIN=65` is currently configured. The tuner's `learned_thresholds` suggests a 20–25 window is optimal. Before enabling, review the score bucket data in the dashboard (MEMECOINS → Learning System) and confirm whether 65 is intentional. The score 70+ bucket (WR=34.1%, avg_4h=-6.6%) performs *worse* than lower score buckets — this warrants a pre-pilot decision.

---

## 5. Enable Procedure

**Only proceed if:** verdict is `PILOT_WATCH` or `PILOT_READY` AND all pre-flight checks pass.

```bash
ssh -i ~/.ssh/memecoin_deploy root@68.183.148.183

# 1. Align pilot constraints in .env
nano /root/memecoin_engine/.env
# Set: MEMECOIN_MAX_OPEN=1
# Confirm: MEMECOIN_DRY_RUN=true (still paper — do not change yet)

# 2. Restart to apply config change
systemctl restart memecoin-dashboard
sleep 5

# 3. Verify config took effect
TOKEN=$(curl -s -X POST http://localhost:8000/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"password":"HArden978ab"}' | python3 -c 'import sys,json; print(json.load(sys.stdin)["token"])')
curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/memecoins/analytics \
  | python3 -c "import sys,json; d=json.load(sys.stdin); ab=d.get('auto_buy',{}); print('max_open:',ab.get('max_open'), 'dry_run:',ab.get('dry_run'))"
# Expect: max_open: 1, dry_run: True

# 4. Confirm pilot readiness one more time
curl -s -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/brain/memecoin-pilot-readiness \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print('Verdict:',d['verdict']); [print(' ✗',b) for b in d['active_blockers']]"
# Must show: Verdict: PILOT_WATCH or PILOT_READY with no blockers

# 5. THE ENABLE SWITCH — only run after steps 1-4
sed -i 's/MEMECOIN_DRY_RUN=true/MEMECOIN_DRY_RUN=false/' /root/memecoin_engine/.env
systemctl restart memecoin-dashboard
sleep 5

# 6. Confirm live mode
curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/memecoins/analytics \
  | python3 -c "import sys,json; d=json.load(sys.stdin); ab=d.get('auto_buy',{}); print('LIVE MODE — dry_run:',ab.get('dry_run'), '| max_open:',ab.get('max_open'))"
# Expect: dry_run: False, max_open: 1

# 7. Record enable timestamp
echo "PILOT ENABLED: $(date -u +%Y-%m-%dT%H:%M:%SZ)" >> /root/memecoin_engine/pilot_log.txt
```

**Record in SESSION_LOG:** date, F&G at enable, post_cooldown_n at enable, initial config.

---

## 6. Daily Monitoring Checklist

Run each morning. Expected time: ~2 min.

```bash
# Auth
TOKEN=$(curl -s -X POST http://localhost:8000/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"password":"HArden978ab"}' | python3 -c 'import sys,json; print(json.load(sys.stdin)["token"])')

# 1. Pilot readiness (catches automatic gate violations)
curl -s -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/brain/memecoin-pilot-readiness \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print('Verdict:',d['verdict'],'| F&G:',d['metrics']['fg_value'],'| mode:',d['metrics']['risk_mode'],'| post_cd:',d['metrics']['post_cooldown_n'])"

# 2. Live P&L check (bought signals)
sqlite3 /root/memecoin_engine/data_storage/engine.db "
  SELECT COUNT(*) as n,
         ROUND(SUM(CASE WHEN return_24h_pct > 0 THEN 1 ELSE 0 END)*100.0/COUNT(*),1) as wr_pct,
         ROUND(AVG(return_24h_pct),2) as avg_24h,
         ROUND(SUM(buy_amount_usd),2) as capital_deployed
  FROM memecoin_signals
  WHERE bought=1 AND outcome_status='COMPLETE'
  ORDER BY scanned_at DESC;"

# 3. Open position check
sqlite3 /root/memecoin_engine/data_storage/engine.db "
  SELECT symbol, ROUND(buy_amount_usd,2) as usd, scanned_at, outcome_status
  FROM memecoin_signals WHERE bought=1 AND outcome_status='PENDING';"

# 4. Circuit breaker
cat /root/memecoin_engine/cb_state.txt 2>/dev/null || echo "CB: clear"

# 5. Agent health
curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/orchestrator/status \
  | python3 -c "import sys,json; bad=[a['name'] for a in json.load(sys.stdin)['agents'] if a['health']!='alive']; print('Agents:','ALL OK' if not bad else 'DEAD: '+str(bad))"
```

**Daily decision tree:**
- F&G drops to ≤35 → pause (see Section 7)
- Risk mode becomes CAUTIOUS or DEFENSIVE → pause immediately
- Daily loss > $25 → pause for 24h
- Any agent dead → pause until fixed
- CB fires → pause, investigate

---

## 7. Pause and Rollback

### Immediate pause (engine still running — just stops new buys)
```bash
ssh -i ~/.ssh/memecoin_deploy root@68.183.148.183
sed -i 's/MEMECOIN_DRY_RUN=false/MEMECOIN_DRY_RUN=true/' /root/memecoin_engine/.env
systemctl restart memecoin-dashboard
echo "PILOT PAUSED: $(date -u +%Y-%m-%dT%H:%M:%SZ)" >> /root/memecoin_engine/pilot_log.txt
```

Existing open positions continue to hold and evaluate at 24h. No manual unwinding needed.

### Pause triggers (any one = pause)

| Trigger | Threshold | Action |
|---------|-----------|--------|
| F&G drops | ≤35 | Pause immediately |
| Risk mode | CAUTIOUS or DEFENSIVE | Pause immediately |
| Daily loss | > $25 | Pause 24h |
| Cumulative loss in pilot | > $75 (5 SOL) | Pause + human review |
| Win rate degradation | Live WR < 40% over 10+ trades | Pause + review |
| Circuit breaker fires | any | Pause until CB clears |
| Agent down | any | Pause until healthy |
| Score threshold concern | if buyable signals look low-quality | Manual pause |

### Full rollback (return to paper permanently)
```bash
# Same as pause — no other action required.
# Data is retained. Pilot log reflects the outcome.
# Update .env: MEMECOIN_MAX_OPEN=3 (restore original) if desired.
```

---

## 8. Checkpoint Reviews

### 10-Trade Checkpoint
Check after 10 live trades resolve (24h horizon):

```bash
sqlite3 /root/memecoin_engine/data_storage/engine.db "
  SELECT COUNT(*) as n,
         ROUND(SUM(CASE WHEN return_24h_pct > 0 THEN 1 ELSE 0 END)*100.0/COUNT(*),1) as live_wr_pct,
         ROUND(AVG(return_24h_pct),2) as live_avg_24h,
         ROUND(MIN(return_24h_pct),2) as worst,
         ROUND(MAX(return_24h_pct),2) as best
  FROM memecoin_signals WHERE bought=1 AND outcome_status='COMPLETE';"
```

**Decision at 10 trades:**
- WR ≥ 40% AND avg_24h ≥ 0% → continue
- WR 30–40% → continue with caution, flag for review
- WR < 30% OR avg_24h < -5% → pause and investigate paper vs live slippage
- Any single trade > -20% → investigate (stop loss should have fired at -10%)

Compare live metrics to the paper baseline (WR=95%, exp=+16.97% over last 20 paper signals) — expect live to be lower. Live WR ≥ 55% at 10 trades would be excellent. WR ≥ 40% is acceptable.

### 20-Trade / 14-Day Checkpoint (pilot conclusion)
After 20 live trades resolve OR 14 calendar days, run full review:

```bash
# Full pilot summary
sqlite3 /root/memecoin_engine/data_storage/engine.db "
  SELECT
    COUNT(*) as total_trades,
    ROUND(SUM(CASE WHEN return_24h_pct > 0 THEN 1 ELSE 0 END)*100.0/COUNT(*),1) as wr_pct,
    ROUND(AVG(return_24h_pct),2) as avg_return_24h,
    ROUND(SUM(buy_amount_usd),2) as total_deployed,
    ROUND(SUM(buy_amount_usd * return_24h_pct / 100.0),2) as total_pnl_usd
  FROM memecoin_signals WHERE bought=1 AND outcome_status='COMPLETE';"
```

---

## 9. Graduation / Extension / Failure Criteria

| Outcome | Criteria | Next Action |
|---------|----------|-------------|
| **GRADUATE** | WR ≥ 55% AND avg_24h ≥ +2% over 20 live trades | Increase to 2 concurrent positions, increase buy to $25. Requires new pilot decision. |
| **EXTEND** | WR 40–55% AND avg_24h ≥ 0% | Continue at same parameters for another 14 days |
| **PAUSE & REVIEW** | WR 30–40% OR avg_24h < 0% | Return to paper. Investigate score threshold, entry timing, market regime. |
| **FAIL** | WR < 30% OR avg_24h < -5% OR total P&L < -$75 | Return to paper indefinitely. Tuner retraining required before next attempt. |

**Graduation to Tier 3 (full live)** is a separate future decision requiring:
- F&G ≥ 50 (neutral/greedy territory)
- 24h expectancy ≥ +5% over 30 trades
- ≥50 post-cooldown clean trades
- 4 consecutive weeks of positive rolling expectancy
- Live pilot completed with ≥20 trades and positive P&L
- Explicit human sign-off (env change required, no automation)

---

## 10. Known Risks and Open Questions

**Score threshold discrepancy (pre-pilot decision required):**
- `MEMECOIN_BUY_SCORE_MIN=65` in env (currently buying score ≥65)
- Tuner's `learned_thresholds` says optimal window is 20–25
- Dashboard score buckets show: score <50 has WR=45.2% and avg_24h=+4.15%; score 70+ has WR=34.1% and avg_4h=-6.6%
- Decision required before enabling: keep 65 (higher confidence signals) or lower to 20–25 (tuner-recommended)? Recommend reviewing MEMECOINS → Learning System in dashboard before enabling.

**Paper 24h WR=95% is suspiciously high:**
- Last 20 paper signals: WR=95%, payoff ratio=209.74x — implies ~1 loss in 20 trades
- This may reflect a favorable short window, not long-run performance
- Overall 841-outcome WR is 41.6% — this is the reliable baseline
- Do not use the 95% figure as a benchmark for live performance

**Pilot constraints are advisory only:**
- The engine does not automatically enforce `max_concurrent_live_positions=1` or `max_daily_loss_usd=$25`
- These must be monitored manually via the daily checklist
- A future patch could hardcode enforcement into the scanner/monitor

---

## Quick Reference

```bash
# Check pilot status (run any time)
TOKEN=$(curl -s -X POST http://localhost:8000/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"password":"HArden978ab"}' | python3 -c 'import sys,json; print(json.load(sys.stdin)["token"])')
curl -s -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/brain/memecoin-pilot-readiness \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['verdict']); [print(' ✗',b) for b in d.get('active_blockers',[])]"

# Enable (only when PILOT_WATCH or PILOT_READY)
sed -i 's/MEMECOIN_DRY_RUN=true/MEMECOIN_DRY_RUN=false/' /root/memecoin_engine/.env && systemctl restart memecoin-dashboard

# Disable (instant — no data loss)
sed -i 's/MEMECOIN_DRY_RUN=false/MEMECOIN_DRY_RUN=true/' /root/memecoin_engine/.env && systemctl restart memecoin-dashboard
```
