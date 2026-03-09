# Active Tasks — Memecoin Engine

> Persistent task list. Updated every session. Review at start.
>
> **How to use this file:**
> 1. Write plan here before starting any 3+ step task
> 2. Check in before implementation begins
> 3. Mark `[x]` complete as you go — one item in progress at a time
> 4. Add a `## Review` section when done with the task
> 5. Update `tasks/lessons.md` after any correction
>
> If something goes sideways mid-task → STOP, update plan, re-check in.

---

## System State — Mar 8, 2026 (Patch 190 deployed)

### Trading Arms

| Arm | Mode | Status | Blocker |
|-----|------|--------|---------|
| **Perp Tiers** | LIVE (`PERP_DRY_RUN=false`) | 3 open positions, ~$2,275 col | ETH buf — watch liq |
| **Memecoins** | PAPER (`MEMECOIN_DRY_RUN=true`) | Gate CLOSED | F&G=17 (needs >35 for pilot, >25 for paper) |
| **Spot DCA** | LIVE signals, manual buys | 10-token basket active | User places buys manually |

### Perp Positions (as of Mar 8 session)
| Tier | Market | Col | Liq | Buf | Action |
|------|--------|-----|-----|-----|--------|
| 3x | SOL LONG | $1,271 | $60.85 | 27.7% | Diamond hands |
| 5x | BTC LONG | $807 | $57,010 | 16.1% | TP +20% → ~$86,976 |
| 5.7x | ETH LONG (blended) | $197 | $1,714 | 13.0% | HOLD — tightest buffer |

### Memecoin Readiness
- **Outcomes**: 906 complete | multi_band_mode=True | optimization_horizon=24h (P185)
- **Tuner bands (24h)**: score 5-20 (WR=72.4%, avg=+36.2%) + score 20-25 (WR=65.9%, avg=+33.6%) + score 40-45 (WR=67.3%, avg=+28.0%)
- **Gate**: F&G=17. Paper gate needs >25. Pilot gate needs >35 (currently -18pt gap). Wait.
- **Pilot readiness**: NOT_PILOT_READY — F&G blocker + post-cooldown sample 0/20 (guard activated 2026-03-07, ~4-6 days to fill once F&G gate opens)
- **Smart wallets**: 6 active (IDs 11, 13–17) | TRIPLE/confluence: 27 clean events (P187 removed 14 duplicates)
- **Auto-buy gate**: now checks score ∈ {5-20 OR 20-25 OR 40-45} in multi-band mode

### Spot Basket (10 tokens, JUP removed)
WIF 15% | JTO 14% | RAY 13% | BONK 12% | PENGU 12% | ORCA 9% | MEW 8% | POPCAT 6% | W 6% | PYTH 5%

### Agents
11/11 alive | uvicorn under systemd | `MEMORY.md` line count at limit (200L) — trim if needed

---

## Next Up

### Immediate Watch Items
- [ ] **ETH liq watch**: buf unknown — check current buffer. Liq=$1,714. If ETH drops toward $1,800 → alert
- [ ] **Aligned-03 cull decision**: revisit at 20 samples — if WR still < 15%, remove
- [ ] **TRIPLE confluence outcomes**: check dashboard — outcomes should be resolving
- [ ] **EarlyBuyer 01-03 outcomes**: check quality post-resolution
- [ ] **Spot learning outcomes**: DCA signals logged Mar 2+ → 7d outcomes fill ~Mar 9. 20 needed for tuner
- [x] **P184 → P185 done**: 24h tuner switch complete. score 20-25 now in auto-buy gate.

### Gate Watch: Memecoin Pilot
**Runbook**: `tasks/memecoin_pilot_runbook.md`

Current status: **NOT_PILOT_READY** (2 blockers)
- F&G=16, needs >35 (+19pt gap) — market-driven, wait
- Post-cooldown sample: 0/20 trades needed (guard activated 2026-03-07, ~4-6 days to fill once F&G opens)

Edge gates (all passing): WR=95%, exp=+16.97%, payoff ratio=209.74x, risk mode=NORMAL ✓

Check status: `GET /api/brain/memecoin-pilot-readiness`
Enable: set `MEMECOIN_MAX_OPEN=1`, `MEMECOIN_DRY_RUN=false` → restart

> Note: Paper WR=95% over last 20 is a favorable window — overall baseline is ~42% over 900 outcomes. Do not use 95% as the live benchmark.
> P184 finding: score 20-25 band is SWITCH_RECOMMENDED (WR=66%, avg 24h=+33.6%). Tuner currently misses this because it trains on 4h. P185 decision needed before pilot flip.
> Pre-pilot decisions needed: (1) switch tuner to 24h horizon? (2) score threshold (env=65 vs tuner bands 5-20 and 40-45). See runbook §10.

---

## Next Patch Candidates (priority order)

| # | Patch | Rationale |
|---|-------|-----------|
| ~~**P177–P190**~~ | ~~(see Completed below)~~ | ✅ All done |
| **P191** | **Frontend cleanup: MemecoinsSection.tsx + MarketOverviewBar.tsx** | 338L + 315L — same glass/responsive treatment as P174/P175 |
| **P192** | **Spot basket rebalance tooling** | Dashboard button to show current on-chain allocations vs target %, flag drift > 5% |

---

## Backlog (parked, no blocker yet)

- [ ] Memecoin TP/SL automation — define exits for live buys (pending pilot data)
- [ ] Tier 10x → 5.7x ETH blended migration is done. Confirm 10x is retired from tier config.
- [ ] MEMORY.md trim — currently at 203L (limit 200). Move detail to topic files.
- [ ] `main.py` at 4,901L — modularise endpoint groups into routers (ongoing)

---

## Completed (Patches 135–190)

### P190: Next Best Move panel (Mar 2026)
- [x] **P190**: Operator-facing "Next Best Move" panel — unified cross-system action recommendation
  - Backend: `GET /api/home/next-best-move` — aggregates PERP buffer, MEMECOIN gate state, SPOT portfolio gap
    - PERP: checks profit buffer via `get_profit_buffer()` — MANAGE if negative, HOLD if positions healthy
    - MEMECOINS: checks AUTO_BUY, F&G (threshold=25 paper/35 pilot), capacity, scanner signal in active band
    - SPOT: finds most underallocated token from `spot_current_signals` kv_store with positive portfolio_gap
    - Ranked: MANAGE(100) > BUY(60) > DCA(35) > WATCH(20) > WAIT(15) > HOLD(5)
    - Returns: `next_best_move`, `alternatives[2]`, `no_action_recommended`, `generated_at`
    - Decision support only — no auto-trading changes
  - Frontend: `NextBestMovePanel` in `HomePage.tsx`
    - Panel header: "NEXT BEST MOVE · P190" + age of last update
    - **Inserted ABOVE the 4 system cards** (after page header) — guaranteed above the fold
    - Shows: 18px action badge (color-coded), system/symbol/priority chips, reason text, blocker pills
    - Alternatives row: compact alt actions with system + reason preview
    - Top-border color tracks action: green=BUY, amber=DCA, blue=WATCH, red=MANAGE, dim=WAIT/HOLD
    - Query: `nbmQ` 30s refetch, 15s stale
  - Current state will show: `WAIT · MEMECOINS · F&G=17 (need >25)` with HOLD PERP + DCA SPOT alts

### P189: Top Buys decision-support panel (Mar 2026)
- [x] **P189**: Operator-facing "Top Buys" panel — memecoins + spot, backed by real gate logic
  - Backend: `GET /api/memecoins/top-candidates` — read-only replication of `_auto_buy_step()` gates
    - Evaluates every cached signal against ALL gates (AUTO_BUY, capacity, F&G, score/bands, rug, BP, mint_revoked, vacc, holder_pct)
    - Returns `BUY_NOW` (all gates pass) / `WATCH` (signal clean, system-level blocker only) / `BLOCKED` (signal-level gate fails)
    - Surfaces signal_blockers separately so frontend can show first blocker reason
    - Handles empty scan cache explicitly: `signal_count=0`
    - Truth audit: F&G=17 (extreme fear, favorable=False), scanner cache empty in current state — surfaced honestly
  - Frontend: `TopBuysPanel` in `HomePage.tsx`
    - Panel header: "TOP BUYS · DECISION SUPPORT · P189"
    - Left column (MEMECOINS): context badges (PAPER/LIVE, pos count, F&G, active bands), candidate rows with status + score + first blocker
    - Right column (SPOT ACCUMULATION): badges (MANUAL BUYS, token count, signal age), tokens sorted by portfolio_gap DESC
    - Empty states: "scanner cache empty — waiting for next scan" / "none reach candidate threshold"
    - Queries: topCandQ (30s refetch) + spotSigsQ (60s refetch)
    - Inserted between system cards and FundingPanel
  - No auto-buy behavior changes; read-only gate simulation only

### P188: Live-buy gate horizon alignment (Mar 2026)
- [x] **P188**: Align `_auto_buy_step()` recent-performance gate to 24h
  - Gate column: `return_4h_pct → return_24h_pct` in WHERE + SELECT
  - Threshold kept at 40% (unchanged semantic: pause if recent 24h WR < 40%)
  - Minimum sample (≥30) kept — gate bypasses if <30 GOOD 24h outcomes available (conservative for sparse envs)
  - LIMIT 30 kept (equivalent lookback at 24h cadence)
  - Docstring + log message updated to say "24h"
  - No frontend changes: gate is `if not dry_run` only, no operator-facing API surface
  - Zero current impact: pilot is PAPER mode (gate never runs); takes effect on live flip

### P186–187: Horizon diagnostic + TRIPLE data integrity (Mar 2026)
- [x] **P186**: Align horizon-comparison output with live tuner state
  - `horizon_comparison` verdict now reads `tuner.optimization_horizon` to branch correctly
  - New states: `ALREADY_SWITCHED` (tuner on 24h, missed bands confirm why) · `CURRENTLY_OPTIMAL` (tuner on 24h, horizons agree)
  - `active_horizon` field added to `horizon_comparison` dict
  - Frontend: `active_horizon` displayed in section header; `ALREADY_SWITCHED` → green ✓ badge; verdict chip colors updated
  - **Live result**: `ALREADY_SWITCHED · ACTIVE: 24H` — score 20-25 now marked ✓ (confirmed active)
- [x] **P187**: Fix confluence duplicate-row creation
  - **Root cause**: Consecutive whale_alert_ids (same-token whale pairs from same scanner pass) processed in one `_detect_confluences()` loop call; P181 4h-mint DB dedup SELECT doesn't reliably see the same-call committed INSERT (SQLite WAL snapshot semantics)
  - **Fix A**: `mints_inserted_this_run: set` added before whale loop in `_detect_confluences` — infallible in-process dedup; skips mint if already inserted this call
  - **Fix B**: `INSERT → INSERT OR IGNORE` as secondary safety net
  - **Fix C**: `CREATE UNIQUE INDEX uq_conf_mint_ts ON confluence_events (token_mint, ts_utc)` — DB-level constraint
  - **Cleanup**: 14 duplicate rows deleted from DB (40 → 26 total; 0 duplicate groups remaining)
  - No frontend changes; event table counts now reflect clean deduplicated data automatically
  - Verified: new post-P187 event (id=41, PUNCH DUAL) created without duplicate ✓

### P180–185: Tuner intelligence + 24h horizon (Mar 2026)
- [x] **P180**: Perp dashboard prefers live Jupiter truth over DB cache for position health display
- [x] **P181**: TRIPLE confluence outcome pipeline fixed; `expire_reason` column added; EXPIRED states handled correctly
- [x] **P182**: Score-analysis endpoint (`GET /api/memecoins/score-analysis`) — proved MEMECOIN_BUY_SCORE_MIN=65 is badly misaligned; bands + threshold_sim + verdict
- [x] **P183**: Multi-modal tuner — replaced cumulative floor search with exhaustive 5-point window search; found bimodal bands at score 5-19 and 40-44; `multi_band_mode` auto-buy gate; `score_bands` + `multi_band_mode` stored in kv_store
- [x] **P184**: Horizon comparison — `_exh_bands_h()` runs exhaustive search on BOTH 4h and 24h; returns `horizon_comparison` with `bands_4h`, `bands_24h`, `bands_missed_by_4h`, verdict
  - **Finding**: score 20-25 is a MISSED BAND (WR=66%, avg 24h=+33.6%) — invisible to 4h tuner → **SWITCH_RECOMMENDED**
  - n_both=901 dual-outcome samples; bands_4h=[5-20, 40-45]; bands_24h=[5-20, **20-25**, 40-45]
  - Frontend pre-built in MemecoinsPage.tsx — renders automatically when `horizon_comparison` present
  - P185 acted on the finding; P186 fixed the diagnostic (see below)
- [x] **P185**: Switch tuner optimization horizon from 4h to 24h
  - `_tune_thresholds_step()`: query, winners/losers, rug_stats, window loop all now use `return_24h_pct`
  - Band field renamed `avg_4h` → `avg_24h` in kv_store + router + frontend interface
  - `optimization_horizon: "24h"` added to payload and surfaced in score-analysis tuner object
  - **New bands (24h)**: score 5-20 (WR=72.4%, +36.2%) + **20-25 (WR=65.9%, +33.6%)** + 40-45 (WR=67.3%, +28.0%)
  - Frontend: band header label now reads "24H-OPTIMIZED", band chips show avg 24h return
  - `MemecoinsPage.tsx` TypeScript interface: `avg_4h → avg_24h`, added `optimization_horizon?: string`
- [x] **P186**: Align horizon-comparison output with live tuner state
  - `horizon_comparison` verdict now reads `tuner.optimization_horizon` to branch correctly
  - New states: `ALREADY_SWITCHED` (tuner on 24h, missed bands confirm why) · `CURRENTLY_OPTIMAL` (tuner on 24h, horizons agree)
  - `active_horizon` field added to `horizon_comparison` dict
  - Frontend: `active_horizon` displayed in section header; `ALREADY_SWITCHED` → green ✓ badge (was: amber ★ warning); verdict chip colors updated
  - **Live result**: `ALREADY_SWITCHED · ACTIVE: 24H` — score 20-25 now marked ✓ (confirmed active)

### P174–175: Frontend maintainability (Mar 2026)
- [x] **P174**: Overflow fixes + responsive grids across WhalePage, MemecoinsPage, ConfluencePage
  - `pos-table-wrap` class, `glass-card`, `grid-auto-2/3/4` added to index.css
  - Mass color replacement: hardcoded `#1e2d3d`/`#2d4060`/`#3d5a78` → CSS vars
  - Table min-widths, flex fixes, webkit prefixes
- [x] **P175**: Component extraction sprint
  - MemecoinsPage.tsx: 5 subcomponents extracted (NarrativeStrip, LearningEngineStatus, OpenPositionsTable, ScannerSignalsPanel, LearningSystem) — render reduced 68%
  - SpotPage.tsx: wrColor helper, CARD constant, HoldingsTable, AnalyticsPanel extracted — render reduced ~50%

### P155–173: Smart wallet system + dashboard (Feb–Mar 2026)
- [x] **P145**: Smart wallet tracker — WALLETS tab (violet #8b5cf6), Helius Enhanced API, accumulation detection
- [x] **P146–147**: Wallet candidate discovery, cross_agent_signals wire, TRIPLE confluence detection
- [x] **P148–154**: Smart wallet WR tracking, phase progress bar, EarlyBuyer discovery (01-03)
- [x] **P155–158**: Dashboard glass overhaul (7 tabs, glass header, news ticker)
- [x] **P159**: health_monitor.py — OPEN_PERP_HIGH counts DISTINCT jupiter_position_key (was raw rows)
- [x] **P160**: smart_wallet_tracker.py — skip buy if DexScreener returns no price (NULL-price guard)
- [x] **P161–173**: (Various bug fixes, VPS infra: uvicorn to systemd, EarlyBuyer-04 cull)

### P135–144: Perp hardening + confidence system (Jan–Feb 2026)
- [x] Health monitor, circuit breaker, multi-tier open/close workflow
- [x] ETH re-entry tier (10x → blended 5.7x), collateral top-ups ($3 SOL + $1.5 SOL + $0.9 SOL)
- [x] 23 stuck smart_wallet_buys resolved (21 EXPIRED, 6 manually evaluated)

### Completed (archived — Patches 128–134)
- [x] P128: Co-founder mode, 5-phase framework, session memory system
- [x] P129: TP code review (tier_manager.py)
- [x] P130–131: Spot trend indicators, overextension gates
- [x] P132–133: Tuner score fix, dashboard milestone progress bar
- [x] P134: Spot DCA signal engine (score formula, DB logging, outcome tracking, tuner, dashboard)

---

## Dashboard / Frontend Structure (post-P175)

**7 tabs** (Terminal.tsx — 629L):
`HOME` | `MEMECOINS` | `CONFLUENCE` | `WHALES` | `WALLETS` | `SPOTS` | `TIERS`

**Section file sizes** (Mar 8, 2026):
| File | Lines | Status |
|------|-------|--------|
| SpotPage.tsx | 1,491 | Improved (P175) — HoldingsTable + AnalyticsPanel extracted |
| MemecoinsPage.tsx | 1,226 | Improved (P175) — 5 components extracted |
| WhalePage.tsx | 658 | Clean (P174) |
| WalletsPage.tsx | 591 | Clean |
| ConfluencePage.tsx | 391 | Clean (P174) |
| HomePage.tsx | ~870 | P190 — NextBestMovePanel above fold; P189 TopBuysPanel below |
| MemecoinsSection.tsx | 338 | P190 candidate |
| MarketOverviewBar.tsx | 315 | P190 candidate |
| TierSection.tsx | 298 | Untouched — manageable |

**CSS utilities in index.css** (added P174):
- `.pos-table-wrap` — horizontal scroll + touch scroll + thin scrollbar
- `.glass-card` — backdrop-filter blur(24px) + webkit prefix
- `.grid-auto-2/3/4` — responsive auto-fit grids

**Backend** (main.py — 4,901L):
- 7 routers: auth, orchestrator, tiers, wallets, brain, whales, memecoins (inline)
- systemd: `memecoin-dashboard.service` owns uvicorn process
