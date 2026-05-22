# Debug Template — Memecoin Engine

> Copy this template for every non-trivial bug investigation.
> Do not write Fix Applied until Root Cause is confirmed.
> Do not close the investigation until Verification passes.

---

## Investigation: [short description]

**Date:**
**Surface/endpoint affected:**

---

### 1. Invariant

> What should be true? Reference `INVARIANTS.md` or the endpoint contract.

```
[state the rule or expected behavior]
```

---

### 2. Live State

> What does the system actually show right now? Paste the query and its output.

```bash
# query or curl used
```

```
# actual output
```

---

### 3. Root Cause

> What is the minimal explanation for the gap between (1) and (2)?
> State one cause. If multiple causes are possible, eliminate them one at a time with live queries before naming the root.

```
[root cause statement — one sentence]
```

---

### 4. Fix Applied

> What was changed, in which file, at which line?
> Do not fill this in until Root Cause is confirmed.

| File | Function / location | Change |
|------|--------------------|---------|
| | | |

---

### 5. Verification

> What exact assertion confirmed the fix? Paste the query and its output.

```bash
# query or curl used
```

```
# output confirming fix
```

---

### 6. Final Verdict

> One sentence: was the invariant restored?

```
[yes/no — and what the system now correctly returns]
```

---

## Examples of past investigations (reference)

### CHIBI not caught by scanner (resolved)
1. **Invariant**: All new tokens from trending feeds should be scanned if age ≥ 1 day
2. **Live State**: CHIBI appears 7 times in MSO with `source='DISCOVERY'`, 0 times with `source='SCANNER'`
3. **Root Cause**: DISCOVERY and SCANNER are separate pipelines with no escalation path. DISCOVERY writes `status='WATCH', score=0` and stops. SCANNER requires `min_age_days=1.0` and profile/boost presence — CHIBI had neither.
4. **Fix Applied**: None — behavior was correct. CHIBI was seen and correctly rejected.
5. **Verification**: Lifecycle engine labeled CHIBI `TRAP/COOLING`. Confirmed via DB query.
6. **Final Verdict**: Invariant intact. CHIBI was not a scanner miss; it was a discovery-only token with serial-rug characteristics across 7 mint addresses.

---

### NBA top candidate showed DISTRUST (resolved — Patch 306)
1. **Invariant**: Primary NBA candidate should not show `triage_state=DO_NOT_TOUCH` when a non-DISTRUST candidate is available
2. **Live State**: TRIPLET (PROVEN_POSITIVE, DISTRUST/CASUALTY) was ranked above BUTTCOIN (TESTED_NEUTRAL, CONDITIONAL_TRUST) because `perf_rank=2` dominated the sort before trust was evaluated
3. **Root Cause**: `_compute_trust_cached` is defined at line ~4927, but `lc_pass_list` sort happens at line ~4194. Trust cannot be a sort key. The sort selected TRIPLET correctly by performance rank; the trust evaluation happened afterward.
4. **Fix Applied**: Post-sort substitution after `_compute_trust_cached` definition — if `best` is DISTRUST and a non-DISTRUST lc_pass candidate exists, substitute.
5. **Verification**: `curl .../next-best-action` returned `BUTTCOIN | TESTED_NEUTRAL | CONDITIONAL_TRUST | MONITOR`
6. **Final Verdict**: Invariant restored. Primary candidate is now the highest-ranked non-DISTRUST token.

---

### Spot F&G reading null (resolved)
1. **Invariant**: Spot signal engine should read F&G value from shared cache
2. **Live State**: `spot_current_signals.fg_value` was `null` in the dashboard
3. **Root Cause**: `get_market_context()` was imported from `agent_coordinator.py` but that function did not exist on the server (added in a later patch that wasn't deployed). Import silently failed.
4. **Fix Applied**: Deployed the missing `get_market_context()` function to the server.
5. **Verification**: `curl .../spot/signals` returned `fg_value: 12` (correct F&G at that time)
6. **Final Verdict**: Invariant restored. F&G now flows correctly through all signal surfaces.
