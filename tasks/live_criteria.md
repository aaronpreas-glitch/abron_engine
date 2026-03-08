# Memecoin Live Transition Criteria

> Last updated: 2026-03-04 (session 3 — WARN analysis complete)
> This is the mechanical checklist for flipping `MEMECOIN_DRY_RUN=false`.
> ALL conditions must be true simultaneously. No partial approvals.

---

## Gate Conditions

| # | Condition | Current | Target | Status |
|---|-----------|---------|--------|--------|
| 1 | Complete outcomes ≥ 200 (HIGH CONF tier) | **403** (507 total) | 200 | ✅ HIGH CONF |
| 2 | GOOD bucket win rate ≥ 40% | **59.2%** (331 samples) | ≥40% | ✅ passing |
| 3 | WARN bucket win rate < 5% | **5.0%** (60 samples) | <5% | ⚠️ one outcome away |
| 4 | F&G > 25 at moment of flip | **14** (Extreme Fear) | >25 | ❌ blocking |
| 5 | Circuit breaker clear | clear | clear | ✅ clear |
| 6 | Recent WR (last 30 GOOD outcomes) ≥ 40% | **23.3%** (7/30) | ≥40% | ❌ market regime |
| 7 | Manual operator confirmation | — | required | — |

**Current verdict: NOT READY — F&G gate + recent WR blocking (both market-driven, not code issues)**

---

## WARN Bucket Analysis — 2026-03-04

Full SQL analysis run at 403 complete outcomes (60 WARN samples).

### Summary
- **WR: 5.0%** (3 wins / 60 samples) — exactly at the gate, needs one more failure to clear
- **Avg return: -27.59%** — filter is working, WARN tokens are reliably bad
- **3 total wins**: FOMO +5.9%, WOR +5.1% ×2 (same token, scanned twice same minute → effectively 2 unique wins)

### Key Findings

**High-score WARN = worst performers:**
| Score range | n | WR | Avg return |
|-------------|---|-----|-----------|
| 45+ | 6 | 0.0% | -33.3% |
| 40-44 | 6 | 0.0% | -34.9% |
| 35-39 | 5 | 0.0% | -38.0% |
| <35 | 43 | 7.0% | -24.6% |

High score does NOT rescue WARN tokens. OIL (score 40-54) was scanned 12 times — lost every single time. WAR (score 42) lost -51.3%. The WARN filter is correct, do not create a WARN+score exception.

**No action needed on WARN gate:** One more WARN scan failure clears it. At current rate (~5-10 WARN tokens per day) this could happen today. Do not lower the threshold — wait it out.

### Recent GOOD WR (23.3%)
The last 30 GOOD outcomes show 23.3% WR, down from 59.2% all-time. This is market-regime driven (F&G=14, Extreme Fear). When F&G rises above 25, recent WR will recover with it. Not a code issue — both gates (#4 and #6) will unlock together when market conditions improve.

---

## How to Check

```bash
# 1. Complete outcomes count
sqlite3 /root/memecoin_engine/data_storage/engine.db \
  "SELECT COUNT(*) FROM memecoin_signal_outcomes WHERE status='COMPLETE'"

# 2 + 3. Win rates by rug bucket
sqlite3 /root/memecoin_engine/data_storage/engine.db "
SELECT rug_label, COUNT(*) as n,
       ROUND(AVG(CASE WHEN return_24h_pct > 0 THEN 1.0 ELSE 0.0 END)*100, 1) as win_rate_pct
FROM memecoin_signal_outcomes
WHERE status='COMPLETE'
GROUP BY rug_label;"

# 4. F&G (must be > 25 at flip time)
sqlite3 /root/memecoin_engine/data_storage/engine.db \
  "SELECT SUBSTR(value,1,80) FROM kv_store WHERE key='shared_fear_greed'"

# 5. Circuit breaker
cat /root/memecoin_engine/cb_state.txt 2>/dev/null || echo "CB: clear"

# 6. Recent WR — last 30 GOOD outcomes
sqlite3 /root/memecoin_engine/data_storage/engine.db "
SELECT COUNT(*) as wins, 30 as total,
       ROUND(COUNT(*)*100.0/30, 1) as wr_pct
FROM (
  SELECT return_24h_pct FROM memecoin_signal_outcomes
  WHERE rug_label='GOOD' AND status='COMPLETE' AND return_24h_pct IS NOT NULL
  ORDER BY scanned_at DESC LIMIT 30
) WHERE return_24h_pct > 0;"
```

---

## Post-Flip Constraints

These apply for the first **30 live trades** after flipping:

1. `MEMECOIN_BUY_USD` stays at **$15** — no size increase until first 30 trades reviewed
2. `MEMECOIN_MAX_OPEN` stays at **3** — no more than 3 concurrent live positions
3. Review after 30 live trades: if live WR ≥ paper WR, can consider raising position size
4. If live WR drops below 25% in first 30 trades → revert to `MEMECOIN_DRY_RUN=true`

---

## How to Flip

```bash
# On VPS
ssh -i ~/.ssh/memecoin_deploy root@68.183.148.183
cd /root/memecoin_engine

# Edit .env — change MEMECOIN_DRY_RUN=true → MEMECOIN_DRY_RUN=false
nano .env

# Restart
systemctl restart memecoin-dashboard

# Verify
grep MEMECOIN_DRY_RUN .env
```

---

## Notes

- The tuner auto-overrides env `MEMECOIN_BUY_SCORE_MIN` when confidence is medium/high.
  At flip time, effective score_min will be `learned_thresholds.min_score` (currently 35).
  Do not manually change `MEMECOIN_BUY_SCORE_MIN` — let the tuner control it.

- The F&G gate (favourable = F&G > 25) is active in live mode. Even after flip,
  no buys will execute during extreme fear (F&G ≤ 25). This is intentional.

- The rug filter (`rug == GOOD`) is hard-coded in `_auto_buy_step()` and cannot be
  disabled without a code change. WARN/UNKNOWN tokens will never be auto-bought.
