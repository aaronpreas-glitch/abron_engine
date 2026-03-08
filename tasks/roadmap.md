# Abron Engine — Product Roadmap

> Living document. Updated as new ideas surface through sessions, market observations, and system gaps.
> No phase starts until the previous phase is stable and consistently profitable.

---

## Token Lifecycle Framework
*How every token is classified and handled by the engine as it grows.*

```
$0 - $5M        → Too small, too risky — ignore
$5M - $50M      → Tier 1 Scanner — quick momentum plays, $15/trade, 2x TP
$50M - $200M    → Tier 2 Scanner — slower moves, $30-50/trade, 30-50% TP
$200M+          → Spot Page candidate — DCA and hold through full cycles
```

**Lifecycle rules:**
- A token in Tier 1 that runs into Tier 2 range → position held, system doesn't auto-exit on cap crossing, exits on TP/momentum signal
- A token in Tier 2 that crosses $200M+ → graduates to spot page consideration (manual review required)
- Spot page tokens that collapse back below $50M → removed from spot, reassessed
- Re-entry: if a token pulls back into range, scanner picks it up again on next cycle

**Real examples:**
- Fartcoin ($161.5M) → Tier 2 candidate (once built)
- White Whale ($46.6M) → Tier 1 upper range, watch closely
- BONK, WIF → already graduated to spot page
- testicle, Barking Puppy → Tier 1 territory but failing signal gates today

---

## Phase 1 — Foundation (CURRENT)
*Goal: Get all three arms to maturity. Prove the system works before scaling.*

### Perp Tiers

#### Infrastructure
- [x] 3x/5x/10x tier architecture deployed
- [x] Diamond hands mode (no SL, TP only)
- [x] Profit buffer → auto re-entry logic
- [x] Atomic TP commit (no half-close bugs)
- [x] Kraken + CoinGecko price fallback
- [x] Telegram TP alerts (entry/exit/profit/buffer)

#### TP Cycle Milestone Ladder
*TP cycles are the natural market recovery signal — they can only accumulate when the market is moving up. Cycle count IS the confidence gate.*

- [ ] **First TP cycle** — system proven end-to-end in production ← CURRENT GATE
- [ ] **5 TP cycles** — buffer self-funding confirmed; review ETH liquidation rate
- [ ] **10 TP cycles, no consecutive ETH liquidations** → review leverage raise to 4x/6x/10x
- [ ] **25 TP cycles, buffer > $100** → review leverage raise to 5x/7x/10x (target state)

#### Leverage Path (advisory — manual confirm always required)
```
Current:    3x SOL │ 5x BTC │ 10x ETH
After 10 cycles:   4x SOL │ 6x BTC │ 10x ETH
After 25 cycles:   5x SOL │ 7x BTC │ 10x ETH  ← target
```
*Key insight: TP cycles can only accumulate in a rising market. Reaching the cycle gates means the market has already recovered — no need to predict timing.*
*ETH stays at 10x — re-entry after liquidation is the strategy, not leverage reduction.*
*Secondary gate: no consecutive ETH liquidations in the last 5 cycles before any raise.*

### Memecoin Scanner

#### Infrastructure
- [x] 10+ signal scoring system
- [x] Paper mode with outcome logging (1h/4h/24h)
- [x] GOOD/WARN/UNKNOWN rug filter confirmed working
- [x] F&G gate (blocks buys in extreme fear)
- [x] Live criteria doc (6 mechanical gates)
- [x] All 4 tuner thresholds enforced in auto-buy (max_score, vol_acceleration, top_holder_pct)
- [x] Recent performance gate (last 30 GOOD outcomes < 40% WR → pause live buys)

#### Outcome Milestone Ladder
*Learning is continuous. Each milestone revalidates thresholds, surfaces new patterns, and unlocks the next research question. There is always a next milestone.*

- [x] **200 outcomes → HIGH CONF tier unlocked** — tuner takes control of score_min
- [x] **367 outcomes → HIGH CONF confirmed ✅ (Mar 3, 2026)** — score ceiling (35-45) discovered, all gates tightened
- [ ] **500 outcomes** — validate tightened gates under current regime; confirm score ceiling held ← NEXT
- [ ] **750 outcomes** — enough samples to split bull vs bear regime performance; check if thresholds need regime-specific versions
- [ ] **1000 outcomes** — research viability of lower-score entry (25-35 bucket): needs this sample depth + market recovery to analyze cleanly

#### Live Transition (gates, not milestones — must ALL pass simultaneously)
- [ ] F&G > 25 at moment of flip
- [ ] Recent WR gate (last 30 GOOD outcomes ≥ 40%) passing
- [ ] Manual confirm → flip MEMECOIN_DRY_RUN=false

#### Live Scaling
- [ ] First 30 live trades at $15/trade
- [ ] After 30 live trades: if live WR ≥ paper WR → raise to $25/trade
- [ ] After 100 live trades: re-evaluate score window with live (not paper) outcomes as ground truth

#### Research Queue
*Ideas that need data before they can be built. Revisit at the milestone that unlocks them.*

- **Lower-score early entry (score 25-35)** — currently filtered out. These tokens are earlier in momentum curve and potentially cleaner entries. Requires: 1000+ outcomes in current window + bull market conditions (behavior changes across regimes). Research at 1000 milestone.
- **Regime-aware thresholds** — single set of thresholds may not hold across fear/greed regimes. At 750+ outcomes, check if separate threshold sets for F&G<30 vs F&G>50 improve performance.
- **Signal weight scoring** — not all 10+ signals are equal. At 500+ live outcomes, build per-signal WR breakdown to weight signals dynamically rather than counting them equally.

### Spot Accumulation
- [x] DCA signal engine (11 tokens, 5-min scan)
- [x] UPTREND/DOWNTREND/NEUTRAL trend detection
- [x] Signal outcomes logging (1d/3d/7d)
- [x] Tuner pending (needs 20 complete outcomes)
- [ ] 20 signal outcomes → tuner active
- [ ] Allocation per token optimized by tuner
- [ ] First live DCA buys executed

**Phase 1 complete when:** All three arms are live, profitable, and self-improving.

---

## Phase 2 — Extensions
*Goal: Add tools that make the system more useful for manual decisions and hybrid trading.*

### Manual Trade Tracker
- User inputs a manual buy (token, entry price, size)
- System monitors price every 5 minutes
- Telegram alerts at key levels (e.g. +50%, +100%, -20% from high)
- Outcome logged into learning loop
- **Trigger:** After Phase 1 complete + first memecoin live trades done

### Real-Time Momentum Advisor
- For any open position (manual or system-opened)
- Monitors buy pressure, volume trend, price action continuously
- Sends Telegram reads: "Still has legs — hold" / "Distribution forming — exit zone" / "Momentum peak likely — scale out"
- Not forced sells — informed alerts that let user decide
- Combines existing scanner signals applied to open positions
- **Trigger:** After manual trade tracker is proven working

### Signal Quality Dashboard
- Visual breakdown of which signals are contributing most to wins vs losses
- Per-signal win rate over time
- Helps identify which signals to weight higher in tuner
- **Trigger:** After 200+ live memecoin outcomes accumulated

### Whale Watch Integration (Moby)
- Monitor Moby's Whale Watch Telegram channel via Telethon userbot running on VPS
- Runs as user's own Telegram account — no new member added to channel
- Parse each alert for: token symbol, buy amount, market cap, KOL vs whale
- Filter: MC $5M–$50M only (Tier 1 range). Discard everything outside.
- On match: immediately hit DexScreener + run scanner gates on that token
- If BOTH whale alert + scanner pass → Telegram alert:
  `"🐋 WHALE + ✅ SCANNER: $TOKEN | MC $11.7M | buy pressure 61% | whale bought $30K"`
- Log ALL whale alerts to DB (pass and fail) — track 1h/4h/24h price outcome for every alert
- Accumulation pattern detection: same token bought 2+ times in 10 min = stronger signal
- **Observation mode first** — same philosophy as memecoin scanner paper mode. Run, log, learn. After 50+ outcomes with data, assess signal quality before acting on it.
- **Not auto-buy ever** — advisory alert only. User decides whether to act.
- **Trigger:** Build now in observation mode. Needs Telegram API credentials (my.telegram.org) to start.

### Mid-Cap Memecoin Tier (Tier 2 Scanner)
- Current scanner targets $5-50M cap (Tier 1) — high momentum, quick flips, $15/trade
- Gap identified: $50-200M established memecoins (e.g. Fartcoin, White Whale) fall between scanner and spot page
- Tier 2 would have different parameters:
  - Market cap range: $50-200M
  - Larger position size (e.g. $30-50/trade)
  - Longer hold time (days not hours)
  - Lower TP target (2x unlikely — target 30-50% gains)
  - Separate outcome logging + separate tuner
- Tokens that earn Tier 2 consideration: survived multiple months, $1M+ liquidity, real community
- **Trigger:** After Tier 1 scanner is live and profitable with 30+ live trades

---

## Phase 3 — Cycle Intelligence
*Goal: Make the system aware of macro cycle position, not just individual trade signals.*

### Cycle Top Detector
- Monitors: F&G extreme greed + total market cap at resistance + tuner win rate degrading + volume exhaustion signals
- When threshold met: Telegram alert "Cycle top conditions forming — consider scaling out"
- Not automatic — advisory only, user makes final call
- Gradually reduce auto-buy exposure as signals stack up
- **Trigger:** When all three arms have been live and profitable for 30+ days

### Bear Market Mode
- Flips system into capital preservation posture
- Memecoins: back to paper mode
- Spot: DCA paused, watchlist only
- Tiers: reduced collateral, tighter TP targets
- Profit buffer used to DCA spot at extreme fear levels
- **Trigger:** Cycle top detector fires + user manual confirm

### Extreme Fear Re-Entry Engine
- Monitors total market cap approaching historical accumulation zones ($1.6T, $1.2T)
- F&G sustained below 20 for 14+ days
- Signals: "Accumulation zone reached — deploy capital"
- Coordinates spot DCA acceleration + tier re-entry
- **Trigger:** Bear market mode confirmed + capital preserved from previous cycle

---

## Phase 4 — Full Automation
*Goal: Minimal manual input. System handles full cycle autonomously with user oversight.*

### Full Cycle Automation
- System detects cycle phase (accumulation / expansion / distribution / contraction)
- Automatically adjusts all three arm parameters based on phase
- User role: approve phase transitions, set capital limits, override if needed
- **Trigger:** Phase 3 proven through at least one full market cycle

### Capital Compounding Engine
- Tier profits → buffer → new positions → more profits
- Spot profits → reinvested into next accumulation cycle
- Memecoin profits → partially reinvested, partially withdrawn to stables
- Fully tracked compounding curve with projections
- **Trigger:** Phase 3 complete, system has proven multi-cycle reliability

---

## Ideas Backlog
*Captured but not yet phased. Revisit when relevant.*

- Portfolio rebalancer (auto-rebalance spot holdings % at set intervals)
- Narrative momentum scorer (tracks trending crypto narratives, weights tokens accordingly)
- Multi-wallet support (separate wallets for tiers vs spot vs memecoins)
- Tax reporting export (P&L per trade, per year, exportable CSV)
- Mobile-friendly dashboard view

---

## How Features Get Added
1. Gap spotted during a live session ("the system missed this")
2. Market observation surfaces a need ("what if we could detect X")
3. Conversation reveals a blind spot ("I was thinking about Y")
4. Post-cycle review ("we should have done Z at the top")

> Last updated: Mar 3, 2026 (session 2)
