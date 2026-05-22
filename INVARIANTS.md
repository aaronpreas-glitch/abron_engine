# System Invariants — Memecoin Engine

> Authoritative threshold and rule reference. Update this file in the same commit that changes any value below.
> No narrative. No changelog. No rationale. Current values only.

---

## Performance Tier (all-time, `memecoin_signal_outcomes`)

| Condition | Tier |
|-----------|------|
| n ≥ 15 AND win_rate ≥ 50% AND avg_return ≥ +5% | `PROVEN_POSITIVE` |
| n ≥ 15 AND avg_return < −10% | `PROVEN_NEGATIVE` |
| n ≥ 8 | `TESTED_NEUTRAL` |
| else | `UNPROVEN` |

- `win_rate` = % of COMPLETE outcomes where `return_24h_pct ≥ 10`
- Source: `memecoin_signal_outcomes WHERE status='COMPLETE' AND return_24h_pct IS NOT NULL`

---

## Correction Tier (14-day rolling window)

| Condition | Tier |
|-----------|------|
| n ≥ 2 AND avg > −5% AND survival_rate ≥ 0.70 | `SURVIVOR` |
| n ≥ 2 AND avg < −30% | `CASUALTY` |
| n ≥ 2 | `TRACKER` |
| else | `UNRATED` |

- Window: `scanned_at >= date('now', '-14 days')`
- `survival_rate` = fraction of outcomes where `return_24h_pct > −20.0`

---

## Trust Label Decision Tree

```
PROVEN_NEGATIVE  OR  correction_tier == CASUALTY  →  DISTRUST
PROVEN_POSITIVE  AND SURVIVOR  AND (WORKING or INSUFFICIENT_DATA setup)  →  HIGH_TRUST
UNPROVEN  AND UNRATED  AND INSUFFICIENT_DATA setup  →  LOW_TRUST
else  →  CONDITIONAL_TRUST
```

---

## Setup Status (trailing n = min(10, total trades for this setup key))

| Condition | Status |
|-----------|--------|
| trailing WR ≥ 55% AND trailing n ≥ 5 | `WORKING` |
| trailing WR < (lifetime WR − 20pp) AND trailing n ≥ 5 | `DEGRADING` |
| trailing WR < 35% AND trailing n ≥ 5 | `FAILING` |
| else | `INSUFFICIENT_DATA` |

---

## Triage State (priority order — first match wins)

| Condition | State |
|-----------|-------|
| `trust_label == DISTRUST` | `DO_NOT_TOUCH` |
| `perf_tier == PROVEN_NEGATIVE` | `DO_NOT_TOUCH` |
| `correction_tier == CASUALTY` | `DO_NOT_TOUCH` |
| `hard_block == True` | `DO_NOT_TOUCH` |
| `all_pass == True` AND trust in (HIGH_TRUST, CONDITIONAL_TRUST) | `INVESTIGATE_NOW` |
| `lc_pass == True` AND trust in (HIGH_TRUST, CONDITIONAL_TRUST) | `MONITOR` |
| `all_pass == True` AND trust == LOW_TRUST | `BLOCKED` |
| else | `BLOCKED` |

---

## NBA Candidate Selection

- **Sort key** (lc-only path): `(perf_rank, fuel_rank, phase_rank)` descending
- **Perf rank map**: `PROVEN_POSITIVE=2, TESTED_NEUTRAL=0, UNPROVEN=0, PROVEN_NEGATIVE=−3`
- **DISTRUST veto (Patch 306)**: if `full_pass` is empty and `best` is DISTRUST, substitute first non-DISTRUST lc_pass candidate

---

## Market / Macro Gates

| Rule | Value |
|------|-------|
| F&G favorable threshold | `value > 25` (≤ 25 = extreme fear → auto-deploy paused) |
| F&G cache TTL | 15 min (`kv_store['shared_fear_greed']`) |
| F&G alert crossing | fires once per hour per direction (25 boundary) |

---

## Capital / Token Gates

| Rule | Value |
|------|-------|
| Min mcap (scanner + second-leg) | 1,500,000 USD |
| Second-leg top-holder veto | `top_holder_pct ≥ 6.0%` → watching only |
| Second-leg PROVEN_NEGATIVE veto | `perf_n ≥ 15 AND perf_avg < −10` → watching only |
| Second-leg CASUALTY veto | `corr_n ≥ 2 AND corr_avg < −30%` → watching only |

---

## Scanner Ingress Gates

### SCANNER pipeline
| Parameter | Value |
|-----------|-------|
| `min_age_days` | 1.0 |
| `min_mcap` | 1,500,000 |
| `min_vol_acceleration` | 3.0 |

### DISCOVERY pipeline
| Parameter | Value |
|-----------|-------|
| `LIQ_MIN` | 15,000 |
| `LIQ_MAX` | 2,000,000 |
| `VOL_H24_MIN` | 100 |
| `AGE_MAX_DAYS` | 30 |
| `FDV_MIN` | 10,000 |
| `FDV_MAX` | 100,000,000 |
| `VOL_LIQ_RATIO_MIN` | 1.5 |

> DISCOVERY writes `status='WATCH', score=0`. It does NOT escalate to SCANNER. These are separate pipelines.

---

## Intel Validation Verdicts

| Condition | Verdict |
|-----------|---------|
| n < 20 COMPLETE outcomes | `NOT_YET_PROVEN` |
| n ≥ 20 AND gradient correct (DISTRUST avg < CONDITIONAL avg < HIGH_TRUST avg) | `EARNING_ITS_PLACE` |
| n ≥ 20 AND gradient inverted | `FALSIFIED` |

---

## Agent Intervals

| Agent | Interval |
|-------|----------|
| `memecoin_scan` staleness threshold | > 10 min = issue |
| `data_integrity_step` | every 5 min |
| `research_step` | every 4h |
| Auto-tuner activation threshold | ≥ 20 COMPLETE MSO rows |
