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

## System State — Mar 8, 2026 (Patch 175 deployed)

### Trading Arms

| Arm | Mode | Status | Blocker |
|-----|------|--------|---------|
| **Perp Tiers** | LIVE (`PERP_DRY_RUN=false`) | 3 open positions, ~$2,275 col | ETH buf 13% — watch liq |
| **Memecoins** | PAPER (`MEMECOIN_DRY_RUN=true`) | Gate CLOSED | F&G=20 (needs >25) |
| **Spot DCA** | LIVE signals, manual buys | 10-token basket active | User places buys manually |

### Perp Positions (as of end of Mar 6 session)
| Tier | Market | Col | Liq | Buf | Action |
|------|--------|-----|-----|-----|--------|
| 3x | SOL LONG | $1,271 | $60.85 | 27.7% | Diamond hands |
| 5x | BTC LONG | $807 | $57,010 | 16.1% | TP +20% → ~$86,976 |
| 5.7x | ETH LONG (blended) | $197 | $1,714 | 13.0% | HOLD — tightest buffer |

### Memecoin Readiness
- **Outcomes**: 717 complete | WR=39.2% | avg=+2.0% — solid baseline
- **Learned thresholds**: active (MEDIUM CONF range)
- **Gate**: F&G > 25 required before auto-buy flips ON
- **Auto-buy**: OFF. Will micro-buy 0.05 SOL per signal once gate opens
- **Smart wallets**: 6 active (IDs 11, 13–17) | Aligned-06 best (WR=22.2%) | Aligned-03 on notice (WR=10%, 10 samples — cull if stays below 15% at 20 samples)
- **TRIPLE confluences**: 23 logged, 21 pending outcome resolution (fire ~Mar 6/7)
- **EarlyBuyers 01-03**: 188 buys each, 24h outcomes fire ~midnight Mar 6/7 UTC

### Spot Basket (10 tokens, JUP removed)
WIF 15% | JTO 14% | RAY 13% | BONK 12% | PENGU 12% | ORCA 9% | MEW 8% | POPCAT 6% | W 6% | PYTH 5%

### Agents
11/11 alive | uvicorn under systemd | `MEMORY.md` line count at limit (200L) — trim if needed

---

## Next Up

### Immediate Watch Items
- [ ] **ETH liq watch**: buf=13%, liq=$1,714. If ETH drops toward $1,800 → alert
- [ ] **Aligned-03 cull decision**: revisit at 20 samples — if WR still < 15%, remove
- [ ] **TRIPLE confluence outcomes**: 21 pending → due to resolve by Mar 7/8. Review results
- [ ] **EarlyBuyer 01-03 outcomes**: 24h window fire ~Mar 6-7. Check quality vs EarlyBuyer-04 (culled)
- [ ] **Spot learning outcomes**: DCA signals logged Mar 2+ → 7d outcomes fill ~Mar 9. 20 needed for tuner

### Gate Watch: Memecoin Pilot
**Runbook**: `tasks/memecoin_pilot_runbook.md`

Current status: **NOT_PILOT_READY** (2 blockers)
- F&G=18, needs >35 (+17pt gap) — market-driven, wait
- Post-cooldown sample: 0/20 trades needed (guard activated 2026-03-07, ~3–5 days to fill once F&G opens)

Edge gates (all passing): WR=95%, exp=+16.97%, risk mode=NORMAL ✓

Check status: `GET /api/brain/memecoin-pilot-readiness`
Enable: set `MEMECOIN_MAX_OPEN=1`, `MEMECOIN_DRY_RUN=false` → restart

> Note: Paper WR=95% over last 20 is likely a favorable window — overall baseline is 41.6% over 841 outcomes. Do not use 95% as the live benchmark.
> Pre-pilot decision needed: score threshold (env=65 vs tuner-optimal=20–25). See runbook §10.

---

## Next Patch Candidates (priority order)

| # | Patch | Rationale |
|---|-------|-----------|
| ~~**P177**~~ | ~~**Memecoin pilot runbook**~~ | ✅ Done — `tasks/memecoin_pilot_runbook.md` |
| ~~**P178**~~ | ~~**Smart wallet dashboard UX**~~ | ✅ Done — cull/watch badges, n+avg_24h per wallet, dedup buy feed (×N badge), TRIPLE table, EXPIRED amber, /api/wallets/triples |
| ~~**P179**~~ | ~~**Perp health alerts**~~ | ✅ Done — ETH CRITICAL (5.8%) + BTC WARN (13.4%) firing |
| **P180** | **Frontend cleanup: MemecoinsSection.tsx + MarketOverviewBar.tsx** | 338L + 315L — same glass/responsive treatment as P174/P175 |
| **P181** | **Spot basket rebalance tooling** | Dashboard button to show current on-chain allocations vs target %, flag drift > 5% |

---

## Backlog (parked, no blocker yet)

- [ ] Memecoin TP/SL automation — define exits for live buys (pending pilot data)
- [ ] Tier 10x → 5.7x ETH blended migration is done. Confirm 10x is retired from tier config.
- [ ] MEMORY.md trim — currently at 203L (limit 200). Move detail to topic files.
- [ ] `main.py` at 4,901L — modularise endpoint groups into routers (ongoing)

---

## Completed (Patches 135–175)

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
| HomePage.tsx | 383 | Untouched — candidate |
| MemecoinsSection.tsx | 338 | P180 candidate |
| MarketOverviewBar.tsx | 315 | P180 candidate |
| TierSection.tsx | 298 | Untouched — manageable |

**CSS utilities in index.css** (added P174):
- `.pos-table-wrap` — horizontal scroll + touch scroll + thin scrollbar
- `.glass-card` — backdrop-filter blur(24px) + webkit prefix
- `.grid-auto-2/3/4` — responsive auto-fit grids

**Backend** (main.py — 4,901L):
- 7 routers: auth, orchestrator, tiers, wallets, brain, whales, memecoins (inline)
- systemd: `memecoin-dashboard.service` owns uvicorn process
