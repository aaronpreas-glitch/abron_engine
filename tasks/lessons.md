# Lessons Learned — Memecoin Engine

> After ANY bug, failed deploy, or user correction: add the pattern here.
> Review at session start. Never repeat the same mistake.

---

## ⚡ Workflow Principles (READ FIRST — non-negotiable)

### Plan → Build → Verify → Ship
1. **Plan First** — Enter plan mode for ANY 3+ step task. Write specs to `tasks/todo.md`. If something goes sideways mid-task, STOP and re-plan immediately — never keep pushing blind.
2. **Subagents** — Use subagents liberally: one task per subagent, offload all research/exploration/parallel analysis. Keep main context clean.
3. **Self-Improvement Loop** — After ANY correction: update this file with the pattern. Write a rule that prevents the same mistake. Iterate ruthlessly until mistake rate drops.
4. **Verification Before Done** — Never mark a task complete without proof. Check logs, diff behavior, ask "would a staff engineer approve this?" Run the actual path, don't assume it works.
5. **Demand Elegance (Balanced)** — For non-trivial changes: ask "is there a more elegant way?" If a fix feels hacky: implement the proper solution. Skip for simple obvious fixes — don't over-engineer.
6. **Autonomous Bug Fixing** — Given a bug report: just fix it. Point at logs/errors/tests and resolve them. Zero hand-holding from user, zero context-switching.

### Task Management
1. Write plan to `tasks/todo.md` with checkable items before starting
2. Check in before beginning implementation on multi-step tasks
3. Mark items complete as you go — one `in_progress` at a time
4. Give a high-level summary at each step so the user stays oriented
5. Add a review section to `tasks/todo.md` when done
6. Update `tasks/lessons.md` immediately after any correction

### Core Principles
- **Simplicity First** — Every change as simple as possible. Minimal code impact.
- **No Laziness** — Find root causes. No temporary fixes. Senior developer standards.
- **Minimal Impact** — Only touch what's necessary. Every extra line is a potential bug.

### Standing Deploy Rules
- Frontend change → rebuild + rsync + restart (no exceptions, ever)
- Every Python patch → `py_compile` check before declaring success
- End of session → update `SESSION_LOG.md`

---

## Code Patterns

### Python / perp_executor.py
- **Column names**: Use `opened_ts_utc`, `closed_ts_utc`, `pnl_pct`, `pnl_usd` — NEVER `entry_ts_utc` or `realized_pnl_pct`
- **Sync vs Async**: `_fetch_price()` is SYNC. Never `await` it. Check every function before adding await.
- **Variable init in both branches**: If a variable is set inside `if side == "LONG":`, it MUST also be set in the `else:` branch. The `_sent_score` crash taught this (Patch 69).
- **Patch anchors**: Always read from VPS (2800+ lines), not local (720 lines). Local code is unpatched.
- **Lambda configs**: All config values use `lambda: _float(...)` pattern. Never bare globals.
- **Env var naming**: Match env var names between scanner code and ALLOWED_KEYS. Mismatch found in Patch 82: `MID_5M_THRESHOLD` vs `MID_15M_THRESHOLD` — tuning had no effect.

### Frontend / React+TS
- **Auth token key**: Response from `/api/auth/login` returns `"token"`, NOT `"access_token"`
- **Timestamps**: Always handle missing `Z` suffix — `ts.endsWith('Z') ? ts : ts + 'Z'`
- **Notes parsing**: Always use `(pos.notes || '')` before `.includes()` or `.match()` — notes can be null
- **MONO style**: Define `const MONO: React.CSSProperties = { fontFamily: 'JetBrains Mono, monospace' }` in every component that needs it
- **Edit uniqueness**: When editing Brain.tsx (3300+ lines), use highly unique anchor strings. Generic patterns like `)}  </div>  )  }` match 14+ locations.
- **React.useState vs useState**: Files that use `import { useState }` (not `import React`) need destructured hooks. Never use `React.useState` in those files.
- **TradingView widget**: Script injected via `useEffect`. Map to `BINANCE:{SYM}USDT`. Use unique container ID per instance. Poll for `window.TradingView` existence if script tag exists but may still be loading.
- **Shell.tsx overflow visible**: TopBar dropdown (search, notifications) requires `overflow: visible` on the TopBar div or dropdowns will be clipped.

### Deployment
- **Order matters**: patch → compile check → build frontend → rsync → restart → smoke test
- **Compile check in patch**: Every patch must `py_compile` the target file before declaring success
- **ALLOWED_KEYS**: When adding new config lambdas to perp_executor, always add matching keys to ALLOWED_KEYS in main.py

## Architecture Mistakes

### Don't Append Endlessly
- perp_executor.py is 2800+ lines, main.py is 8600+. Every patch adds more.
- Before adding code: ask "can this be a separate module?" or "can I replace existing code instead?"
- Target: break perp_executor into modules (entry logic, exit logic, monitoring, helpers)

### Verify Logic, Not Just Endpoints
- Smoke testing API responses is necessary but not sufficient
- After deploying engine logic: tail logs to confirm new code paths are actually executing
- Example: Patch 81 deployed EV filter but we never confirmed a signal actually got EV-filtered
- Patch 82 verified: MACD filter catching TON/OP despite momentum > threshold. Volume filter reducing signal count drastically.

## Trading Logic Lessons

### The Real Problem (Feb 27 2026 baseline, pre-Patch 82)
- 83-85% TIME_LIMIT exits = the system isn't making decisions, it's just waiting
- Win/loss ratio is ~1:1 (avg_win $1.79 vs avg_loss $1.87) — zero edge
- Kelly correctly returns f=0% — the math confirms no edge yet
- STOP_LOSS exits at -3.4% avg are the main PnL drag
- SOL performs best (50% WR, -0.04% avg), BTC worst (45.2% WR, -0.22% avg)

### What Manual Trading Gets Right That The System Doesn't
- Cut losers FAST (1-1.5%, not 3.4%)
- Let winners run with trailing stops, not fixed TP
- Size up dramatically on high-conviction setups
- React to momentum in real-time, don't just set-and-forget
- Scan for runner of the day, wait for the dip, enter with conviction
- Take profit in chunks (12-33 sell txns), not all at once

### Patch 82 Fixes Applied
- **Exit**: Persistent trailing stops (DB-persisted), breakeven at +0.3% MFE, early cut at 5min/-0.3%, winner extension at TIME_LIMIT, tighter SL 0.5% (was 0.8%)
- **Entry**: Volume filter 1.5× avg, RSI > 75 skip LONG / RSI < 25 skip SHORT, MACD histogram confirmation, dip-buy detection (1h trend + candle pullback), threshold 0.25% (was 0.15%)
- **Observed**: Drastically fewer signals. MACD filtering out weak momentum. Volume filter cutting noise. First signal (AVAX SHORT) had vol_ratio=16.3x.

### North Star
- Target: 3:1+ win/loss ratio, not 1:1
- Target: 60%+ win rate with tighter entry filters
- Target: <50% TIME_LIMIT exits — trades should resolve via TP or SL, not timeout
- 100x gains come from position management (adding to winners, trailing stops), not entry signals alone

### Jupiter Perps Close — receiveToken Must Match Asset
- `close_perp_sync` must pass the market token as `receiveToken`, NOT always "SOL"
- SOL position → receiveToken="SOL", BTC → "BTC", ETH → "ETH"
- Wrong token returns HTTP 400: "Positions can only be closed in USDC and the market token"
- Fix: `_RECEIVE = {"SOL":"SOL","BTC":"BTC","ETH":"ETH"}; receive_token = _RECEIVE.get(symbol.upper(), "SOL")`
- And: `_close_perp_position` must pass `symbol=pos.get("symbol","SOL")` when calling close_perp_sync

### Conditional TIME_LIMIT (Patch 112)
- Hard time limits cut profitable positions before they can reach TP — always check raw_pnl before exiting
- LONG: `_raw_pnl_pct = (price - entry) / entry * 100; if _raw_pnl_pct <= 0: exit_reason = "TIME_LIMIT"`
- SHORT: `_raw_pnl_pct = (entry - price) / entry * 100; if _raw_pnl_pct <= 0: exit_reason = "TIME_LIMIT"`
- Scalp mode: leave TIME_LIMIT hard (correct for short-duration trades, max 45min)

### Martingale = Blow Up
- Adding to losing positions is how traders go broke — never automate this
- Pyramiding into WINNERS (adding after first TP hit) is the correct approach
- Lesson: if position is below entry, the thesis is not confirmed yet

### SWING_4H_THRESHOLD Requires Service Restart
- `os.getenv("SWING_4H_THRESHOLD", "0.5")` is called at the start of each scan loop
- Simply appending to .env does NOT take effect — must `systemctl restart memecoin-dashboard`

### Collateral Math
- PERP_SIZE_USD=100 with PERP_DEFAULT_LEVERAGE=5 → collateral_usd = 100/5 = $20
- DB stores collateral (deposited), not notional size. Both are important to track.

### Jupiter: One Position Per Market Per Wallet
- Jupiter only allows ONE position per market (e.g. SOL-LONG) per wallet at a time
- Opening SOL-LONG twice ADDS to the same position (blended leverage, same pubkey)
- Multi-tier system must use DIFFERENT assets: 3x=SOL, 5x=BTC, 10x=ETH
- To "add more" to a tier: open same market again → Jupiter stacks it onto existing position

### Jupiter Positions API — `dataList` Key
- `GET https://perps-api.jup.ag/v2/positions?walletAddress={wallet}` returns `{"dataList": [...], "count": N}`
- The key is `dataList` NOT `positions` or `data` — parse accordingly
- Each position's pubkey field: `positionPubkey` (primary), fallback to `address`, `pubkey`
- The `positionPubkey` from the QUOTE response (`/positions/increase`) may differ from the actual on-chain pubkey after tx confirms → add 10-min grace period before liquidation detection fires

### Liquidation Detection Grace Period
- Liquidation detector must wait ≥10 min after position open before checking Jupiter
- On-chain confirmation lag causes false positives: position exists in DB but not yet visible via Jupiter API
- Pattern: `if age_minutes < LIQUIDATION_GRACE_MINUTES: continue`

### Jupiter Minimum Collateral: $10
- New positions require at least $10 collateral (in SOL equivalent)
- Error: `{"code":"collateral_size_below_minimum","message":"Collateral size must be at least $10 for new positions"}`
- At 10x leverage: minimum notional = $100 (not $70)

### Disabling Signal Scanner Without Disabling Tier Opens
- `tier_manager.open_tier_position` calls `open_perp_sync(dry_run=False)` directly — bypasses PERP_DRY_RUN env var
- To disable signal-based auto-entries while keeping tier opens live: set `SWING_4H_THRESHOLD=99`
- PERP_DRY_RUN=false must stay set for tier opens to work

### close_all.py / Standalone Scripts Require env Sourcing
- `systemctl restart` services load env via EnvironmentFile directive
- Standalone python scripts run manually do NOT inherit this env
- Pattern: `set -a && source .env && set +a && python3 script.py`

### .env Tier Config Reference (Patch 114)
```
TIER_3X_LEVERAGE=3      TIER_3X_NOTIONAL=150  TIER_3X_SYMBOL=SOL
TIER_5X_LEVERAGE=5      TIER_5X_NOTIONAL=100  TIER_5X_SYMBOL=BTC  TIER_5X_TP_PCT=20
TIER_10X_LEVERAGE=10    TIER_10X_NOTIONAL=100 TIER_10X_SYMBOL=ETH TIER_10X_TP_PCT=10
PROFIT_BUFFER_3X_THRESHOLD=50   PROFIT_BUFFER_5X_THRESHOLD=20
MAX_OPEN_PERPS=10               SWING_4H_THRESHOLD=99
```

### User's Memecoin Edge (to be automated)
- Whale screenshots show $1K-$2.3K buys turning into $13K-$107K (40-75x returns)
- Pattern: buy early (1-3 txns), sell in many chunks (12-33 txns), hold for days to weeks
- Memecoins on Solana = highest asymmetry. BTC/ETH will never do 40-75x.
- Volume spikes + insider activity = entry signals worth building
- Track high/low volume days across the week — better entries happen on high-volume days, better setups form on low-volume days before the move

---

## Frontend Patterns (added P174–P175, Mar 2026)

### CSS Utility Classes in index.css
- `.pos-table-wrap` — always wrap wide tables (>5 cols). Provides `overflow-x: auto` + `-webkit-overflow-scrolling: touch` + `scrollbar-width: thin`
- `.glass-card` — use instead of repeating inline `backdrop-filter` on every card. Includes webkit prefix.
- `.grid-auto-2/3/4` — use `className="grid-auto-N"` instead of `gridTemplateColumns: 'repeat(N, 1fr)'`. Makes grids responsive automatically.
- `replace_all: true` only catches literal style property patterns (e.g. `color: '#hex'`). Ternary-embedded colors need targeted edits. Always do a second pass with Grep after replace_all.

### Component Extraction Rules
- Extract when: a JSX block is 60+ lines, has a clear name, and has well-defined props
- IIFE anti-pattern `{(() => { ... })()}` → always extract to a named component
- Thread all closed-over state as explicit props — TypeScript will catch mismatches at build time
- One TypeScript build error = one focused fix. Don't batch or guess.
- After extraction: run `npm run build` immediately. Don't wait until all components are extracted.

### Smart Wallet Tracker Patterns (P145–P160)
- Helius Enhanced Transactions API: `api.helius.xyz/v0` (different from JSON-RPC endpoint)
- Buy detection: `tokenTransfers` where `toUserAccount == wallet_address` AND `mint ∉ STABLE_MINTS`
- Always guard `_resolve_token()` — if DexScreener returns no price, skip the buy (NULL-price orphan rows break outcome tracking)
- `smart_wallets.total_buys` scope: count active-wallet buys only for phase gating
- Soft-delete wallets (active=0) retains historical buy data — never hard-delete

### Health Monitor Patterns (P159)
- `COUNT(*)` on perp_positions rows ≠ count of distinct open positions (Jupiter stacks buys onto same pubkey)
- Always use `COUNT(DISTINCT jupiter_position_key)` for OPEN_PERP_HIGH checks
- Test position counts via dashboard `/api/orchestrator/status` after any position open/close

### Memecoin Auto-Buy Gate Checklist (before flipping MEMECOIN_DRY_RUN=false)
1. F&G > 25
2. learnedT exists (tuner has fired at least once)
3. WR > 35% over at least 100 complete outcomes
4. Buy size set to 0.05 SOL (micro-pilot)
5. Kill switch confirmed: `MEMECOIN_DRY_RUN=true` in .env flips it back immediately, requires service restart
