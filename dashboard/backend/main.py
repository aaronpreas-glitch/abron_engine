"""
main.py — FastAPI dashboard for the Memecoin Engine.

Run locally:
    cd dashboard/backend
    uvicorn main:app --reload --port 8080

On VPS: managed by memecoin-dashboard.service (uvicorn, 1 worker, port 8080).
"""
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, WebSocket, WebSocketDisconnect, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from auth import create_access_token, get_current_user, validate_ws_token, verify_password
from config_editor import get_config, validate_and_update
from db_read import (
    open_manual_position,
    close_manual_position,
    get_alerts_for_overlay,
    get_closed_trades,
    get_current_regime,
    get_db_health,
    get_equity_curve,
    get_leaderboard,
    get_open_positions,
    get_outcome_winrates,
    get_outcome_recap,
    get_performance_summary,
    get_portfolio_simulation_metrics,
    get_recent_signals,
    get_regime_timeline,
    get_risk_mode,
    get_risk_pause_state,
    get_score_histogram,
    get_signal_by_id,
    get_signal_outcome,
    get_symbol_controls_summary,
    get_symbol_controls_detail,
    get_symbol_history,
    get_symbol_outcomes,
    get_trade_summary,
    get_weekly_tuning_report,
)
from dex_proxy import get_watchlist_cards_async
from jupiter_proxy import get_perps_position, get_sol_price, get_dca_summary, add_dca_entry_proxy, clear_dca_entries
from news_feed import fetch_news
from ws_manager import manager, signal_poller
from outcome_tracker import outcome_tracker_loop

# ── Router modules (Patch 126 refactor) ──────────────────────────────────────
from routers.memecoins   import router as _router_memecoins
from routers.wallet      import router as _router_wallet
from routers.tiers       import router as _router_tiers
from routers.portfolio   import router as _router_portfolio
from routers.spot        import router as _router_spot        # Patch 128
from routers.home        import router as _router_home        # Patch 140
from routers.whale_watch import router as _router_whale_watch # Patch 140
from routers.confluence  import router as _router_confluence  # Patch 143
from routers.funding     import router as _router_funding     # Patch 144
from routers.wallets     import router as _router_wallets     # Patch 145

log = logging.getLogger("dashboard")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

# Patch 120 — cycle counter for data integrity cadence (every 5 min = every 5th 60s cycle)
_monitor_cycle: int = 0


# ---------------------------------------------------------------------------
# App lifecycle — start signal poller on startup
# ---------------------------------------------------------------------------

async def _perp_monitor_loop():
    """Background: check open perp positions every 60s and close on stop/TP/time."""
    global _monitor_cycle
    import sys
    root = _engine_root()
    if root not in sys.path:
        sys.path.insert(0, root)
    while True:
        _monitor_cycle += 1
        try:
            from utils.perp_executor import perp_monitor_step  # type: ignore
            await perp_monitor_step()
        except Exception as _e:
            log.debug("perp_monitor_step error: %s", _e)
        try:
            from utils.memecoin_manager import memecoin_monitor_step as _mm_step  # type: ignore
            await asyncio.to_thread(_mm_step)
        except Exception as _e:
            log.debug("memecoin_monitor_step error: %s", _e)
        try:  # Patch 114 — tier monitor
            from utils.tier_manager import tier_monitor_step as _tier_step  # type: ignore
            await asyncio.to_thread(_tier_step)
        except Exception as _te:
            log.debug("tier_monitor_step error: %s", _te)
        if _monitor_cycle % 5 == 0:  # Patch 130 — spot: every 5min matches cache TTL, avoids DexScreener 429
            try:  # Patch 128 — spot accumulation price refresh
                from utils.spot_accumulator import spot_monitor_step as _spot_acc_step  # type: ignore
                await asyncio.to_thread(_spot_acc_step)
            except Exception as _sae:
                log.debug("spot_monitor_step error: %s", _sae)
        try:  # Patch 118 — health watchdog
            from utils.health_monitor import health_watchdog_step as _hw_step  # type: ignore
            await asyncio.to_thread(_hw_step)
        except Exception as _hwe:
            log.debug("health_watchdog_step error: %s", _hwe)
        if _monitor_cycle % 5 == 0:  # Patch 120 — data integrity every 5 min
            try:
                from utils.agent_coordinator import data_integrity_step as _di_step  # type: ignore
                await asyncio.to_thread(_di_step)
            except Exception as _die:
                log.debug("data_integrity_step error: %s", _die)
        if _monitor_cycle % 5 == 0:  # Patch 139 — whale watch outcome tracking every 5 min
            try:
                from utils.whale_watch import whale_watch_outcome_step as _ww_outcome  # type: ignore
                await asyncio.to_thread(_ww_outcome)
            except Exception as _wwe:
                log.debug("whale_watch_outcome_step error: %s", _wwe)
        if _monitor_cycle % 5 == 0:  # Patch 143 — confluence engine every 5 min
            try:
                from utils.confluence_engine import confluence_step as _cf_step  # type: ignore
                await asyncio.to_thread(_cf_step)
            except Exception as _cfe:
                log.debug("confluence_step error: %s", _cfe)
        if _monitor_cycle % 30 == 0 or _monitor_cycle == 1:  # Patch 144 — funding rates (30 min + startup)
            try:
                from utils.funding_monitor import funding_step as _fund_step  # type: ignore
                await asyncio.to_thread(_fund_step)
            except Exception as _fde:
                log.debug("funding_step error: %s", _fde)
        if _monitor_cycle % 5 == 0:  # Patch 145 — smart wallet tracker every 5 min
            try:
                from utils.smart_wallet_tracker import smart_wallet_step as _swt_step  # type: ignore
                await asyncio.to_thread(_swt_step)
            except Exception as _swte:
                log.debug("smart_wallet_step error: %s", _swte)
        await asyncio.sleep(60)


async def _research_loop():
    """Background: synthesize memecoin learning data into MEMORY.md every 4h (Patch 120)."""
    import sys
    root = _engine_root()
    if root not in sys.path:
        sys.path.insert(0, root)
    await asyncio.sleep(60)   # brief startup delay — let agents settle first
    while True:
        try:
            from utils.agent_coordinator import research_step as _rs  # type: ignore
            await asyncio.to_thread(_rs)
            log.debug("research_step completed")
        except Exception as _e:
            log.debug("research_loop error: %s", _e)
        await asyncio.sleep(14400)   # 4 hours


async def _memecoin_scan_loop():
    """Background: scan DexScreener for trending Solana memecoins every 5 min."""
    import sys
    root = _engine_root()
    if root not in sys.path:
        sys.path.insert(0, root)
    await asyncio.sleep(30)  # startup delay
    while True:
        try:
            from utils.memecoin_scanner import scan_trending_solana, cache_signals  # type: ignore
            from utils import orchestrator  # type: ignore
            signals = await asyncio.to_thread(scan_trending_solana)
            await asyncio.to_thread(cache_signals, signals)
            orchestrator.heartbeat("memecoin_scan")
            log.debug("memecoin_scan: cached %d signals", len(signals))
        except Exception as _e:
            log.debug("memecoin_scan_loop error: %s", _e)
        await asyncio.sleep(300)  # every 5 min


async def _perp_signal_scan_loop():
    """Background: auto-fire paper perp trades every 2 min on SOL/BTC/ETH.

    Paper mode is intentionally aggressive — low threshold, many assets, short cooldown.
    Goal: maximum learning data. Tighten thresholds when switching to live.
    """
    import sys
    root = _engine_root()
    if root not in sys.path:
        sys.path.insert(0, root)

    # CoinGecko IDs for each symbol
    _CG_IDS = {"SOL": "solana", "BTC": "bitcoin", "ETH": "ethereum"}

    while True:
        await asyncio.sleep(120)  # 2 min — more data, faster learning
        try:
            perp_enabled = os.getenv("PERP_EXECUTOR_ENABLED", "false").lower() == "true"
            if not perp_enabled:
                continue

            # Configurable 1h change threshold (default 0.3% for paper — very sensitive)
            try:
                threshold = float(os.getenv("PERP_1H_THRESHOLD", "0.3"))
            except Exception:
                threshold = 0.3

            # Fetch BTC + ETH + SOL prices + 24h change in one request
            import requests as _req
            try:
                ids = ",".join(_CG_IDS.values())
                r = _req.get(
                    f"https://api.coingecko.com/api/v3/simple/price"
                    f"?ids={ids}&vs_currencies=usd&include_24hr_change=true",
                    timeout=8,
                )
                price_data = r.json()
            except Exception as fe:
                log.debug("perp_scan price fetch error: %s", fe)
                continue

            # Get current regime
            try:
                from utils.market_cycle import get_cycle_phase  # type: ignore
                phase = get_cycle_phase()
            except Exception:
                phase = "TRANSITION"

            from utils.perp_executor import execute_perp_signal  # type: ignore

            # Scan all three assets
            for symbol, cg_id in _CG_IDS.items():
                try:
                    asset = price_data.get(cg_id, {})
                    chg_24h = float(asset.get("usd_24h_change", 0))
                    chg_1h  = chg_24h / 6.0  # rough 1h proxy from 24h

                    # LONG signal: price moving up + regime not BEAR
                    if chg_1h > threshold and phase != "BEAR":
                        regime_label = phase if phase else "BULL"
                        await execute_perp_signal({
                            "symbol": symbol, "side": "LONG",
                            "regime_label": regime_label, "source": "auto_scan",
                        })
                        log.info(
                            "[PERP SCAN] LONG %s  phase=%s  1h=+%.2f%%  threshold=%.1f%%",
                            symbol, phase, chg_1h, threshold,
                        )

                    # SHORT signal: price moving down + regime not BULL
                    elif chg_1h < -threshold and phase != "BULL":
                        regime_label = phase if phase else "BEAR"
                        await execute_perp_signal({
                            "symbol": symbol, "side": "SHORT",
                            "regime_label": regime_label, "source": "auto_scan",
                        })
                        log.info(
                            "[PERP SCAN] SHORT %s  phase=%s  1h=%.2f%%  threshold=%.1f%%",
                            symbol, phase, chg_1h, threshold,
                        )

                except Exception as sym_e:
                    log.debug("perp_scan %s error: %s", symbol, sym_e)

        except Exception as _e:
            log.debug("perp_signal_scan error: %s", _e)


async def _scalp_monitor_loop():
    """Background: check open SCALP positions every 5s for fast exit on tiny TP/SL."""
    import sys
    root = _engine_root()
    if root not in sys.path:
        sys.path.insert(0, root)
    while True:
        try:
            from utils.perp_executor import scalp_monitor_step  # type: ignore
            await scalp_monitor_step()
        except Exception as _e:
            log.warning("scalp_monitor_step error: %s", _e)
        await asyncio.sleep(5)


async def _scalp_signal_scan_loop():
    """Background: auto-fire paper scalp perp trades every 30s on SOL/BTC/ETH.

    Uses real 5-minute OHLC data from Kraken (no API key, no geo-block, generous limits).
    Kraken OHLC?interval=5 returns 5-minute candles: compare last closed candle vs
    the one before it for an accurate 5-minute price move.
    High frequency → many trades → rapid learning data accumulation.
    """
    import sys
    root = _engine_root()
    if root not in sys.path:
        sys.path.insert(0, root)

    # Kraken pair names: (query_pair, result_key)
    # Kraken normalises result keys differently from query params (e.g. XBTUSD → XXBTZUSD)
    _KRAKEN_PAIRS = {
        "SOL": ("SOLUSD",  "SOLUSD"),
        "BTC": ("XBTUSD",  "XXBTZUSD"),
        "ETH": ("ETHUSD",  "XETHZUSD"),
    }

    while True:
        await asyncio.sleep(30)
        try:
            scalp_enabled = os.getenv("SCALP_ENABLED", "false").lower() == "true"
            if not scalp_enabled:
                continue

            try:
                threshold = float(os.getenv("SCALP_5M_THRESHOLD", "0.15"))
            except Exception:
                threshold = 0.15

            import requests as _req
            from utils.perp_executor import execute_perp_signal  # type: ignore

            try:
                from utils.market_cycle import get_cycle_phase  # type: ignore
                phase = get_cycle_phase()
            except Exception:
                phase = "TRANSITION"

            for symbol, (kraken_pair, result_key) in _KRAKEN_PAIRS.items():
                try:
                    # Fetch 5-minute OHLC candles from Kraken (no API key required)
                    # Each candle: [time, open, high, low, close, vwap, volume, count]
                    r = _req.get(
                        f"https://api.kraken.com/0/public/OHLC?pair={kraken_pair}&interval=5",
                        timeout=8,
                    )
                    data = r.json()
                    errors = data.get("error", [])
                    if errors:
                        log.warning("scalp_scan Kraken error for %s: %s", symbol, errors)
                        continue

                    candles = data.get("result", {}).get(result_key, [])
                    if len(candles) < 3:
                        log.debug("scalp_scan: not enough candles for %s (%d)", symbol, len(candles))
                        continue

                    # Use the last two *closed* candles (index -2 and -3)
                    # [-1] is the still-forming candle, [-2] is the last completed one
                    prev_close = float(candles[-3][4])  # close of 2 candles ago
                    curr_close = float(candles[-2][4])  # close of last completed candle
                    price_now  = float(candles[-2][4])

                    if prev_close <= 0:
                        continue

                    chg_5m = (curr_close - prev_close) / prev_close * 100  # signed %

                    log.info(
                        "[SCALP SCAN] %s  5m=%.3f%%  threshold=±%.2f%%  price=$%.2f",
                        symbol, chg_5m, threshold, price_now,
                    )

                    if chg_5m > threshold:
                        await execute_perp_signal({
                            "symbol": symbol, "side": "LONG",
                            "regime_label": phase or "SCALP", "source": "scalp",
                        })
                        log.info("[SCALP SCAN] → LONG signal fired for %s", symbol)
                    elif chg_5m < -threshold:
                        await execute_perp_signal({
                            "symbol": symbol, "side": "SHORT",
                            "regime_label": phase or "SCALP", "source": "scalp",
                        })
                        log.info("[SCALP SCAN] → SHORT signal fired for %s", symbol)

                except Exception as sym_e:
                    log.warning("scalp_scan %s error: %s", symbol, sym_e)

        except Exception as _e:
            log.warning("scalp_signal_scan error: %s", _e)


async def _spot_monitor_loop():
    """Background: check open spot (memecoin) positions every 30s.

    Wraps executor.monitor_positions() — evaluates stop loss, TP1, TP2,
    trailing stop, and max_hold_hours for every open trade row.
    Works for both the scalp-style fast exits AND the swing-style hold strategy
    since exit_strategy.build_exit_plan() returns different params per signal profile.
    """
    import sys
    root = _engine_root()
    if root not in sys.path:
        sys.path.insert(0, root)
    while True:
        try:
            spot_enabled = os.getenv("EXECUTOR_ENABLED", "false").lower() == "true"
            if spot_enabled:
                from utils.executor import monitor_positions  # type: ignore
                await monitor_positions()
        except Exception as _e:
            log.warning("spot_monitor error: %s", _e)
        await asyncio.sleep(30)


# Track last signal id processed by spot scanner so we don't re-fire on every poll
_spot_last_signal_id: int = 0


async def _spot_signal_scan_loop():
    """Background: poll DB for new ALERT signals every 60s and fire spot paper trades.

    Two strategies run simultaneously for every qualifying signal:
      1. Scalp track  — build_exit_plan() gets scalp_mode=True → fast TP/SL (2%/0.8%, 20min hold)
      2. Swing track  — build_exit_plan() uses learned exit profile → adaptive TP, trailing stop

    Both write to the 'trades' table and queue an alert_outcome row.
    Data from both tracks feeds the learning loop (exit_strategy + outcome_tracker).

    Signals must have a mint address, score >= MIN_SCORE_TO_EXECUTE, and decision=ALERT.
    Cooldown: one position per symbol at a time (executor guards against duplicates).
    """
    global _spot_last_signal_id
    import sys
    root = _engine_root()
    if root not in sys.path:
        sys.path.insert(0, root)

    # Initialise last_id to current max so we don't replay historical signals on boot
    try:
        import sqlite3 as _sq
        _db_path = root + "/data_storage/engine.db"
        with _sq.connect(f"file:{_db_path}?mode=ro", uri=True) as _c:
            row = _c.execute("SELECT COALESCE(MAX(id),0) FROM signals").fetchone()
            _spot_last_signal_id = int(row[0])
        log.info("[SPOT SCAN] Initialised. Watching for signals after id=%d", _spot_last_signal_id)
    except Exception as _init_e:
        log.warning("[SPOT SCAN] Could not init last_id: %s", _init_e)

    while True:
        await asyncio.sleep(60)
        try:
            spot_enabled = os.getenv("EXECUTOR_ENABLED", "false").lower() == "true"
            if not spot_enabled:
                continue

            try:
                min_score = float(os.getenv("MIN_SCORE_TO_EXECUTE", "55"))
            except Exception:
                min_score = 55.0

            import sqlite3 as _sq
            _db_path = root + "/data_storage/engine.db"
            with _sq.connect(f"file:{_db_path}?mode=ro", uri=True) as _c:
                _c.row_factory = _sq.Row
                rows = _c.execute(
                    """
                    SELECT id, ts_utc, symbol, mint, score_total, decision,
                           regime_score, regime_label, price_usd, conviction, notes
                    FROM signals
                    WHERE id > ?
                      AND mint IS NOT NULL AND mint != ''
                      AND (score_total >= ? OR score_total IS NULL)
                      AND decision LIKE '%ALERT%'
                      AND decision NOT LIKE '%DRY%'
                      AND (notes IS NULL OR notes NOT LIKE '%status=Breakdown%')
                      AND (notes IS NULL OR notes NOT LIKE '%status=Illiquid%')
                    ORDER BY id ASC
                    LIMIT 20
                    """,
                    (_spot_last_signal_id, min_score),
                ).fetchall()

            if not rows:
                continue

            from utils.executor import execute_signal  # type: ignore

            for row in rows:
                sig_id  = row["id"]
                symbol  = row["symbol"]
                mint    = row["mint"]
                score   = float(row["score_total"] or 0)
                regime  = row["regime_label"] or "UNKNOWN"
                price   = float(row["price_usd"] or 0)
                conv_raw = row["conviction"]
                conviction = {3: "A", 2: "B", 1: "C"}.get(int(conv_raw), "C") if conv_raw else "C"

                _spot_last_signal_id = max(_spot_last_signal_id, sig_id)

                if price <= 0:
                    log.debug("[SPOT SCAN] Skipping %s — no price in signal", symbol)
                    continue

                # ── Portfolio sizing ───────────────────────────────────────────
                try:
                    portfolio = float(os.getenv("PORTFOLIO_USD", "1000"))
                except Exception:
                    portfolio = 1000.0
                # Use 5% of portfolio per position (capped at $50 for paper safety)
                position_usd = min(portfolio * 0.05, 50.0)

                base_signal = {
                    "symbol":       symbol,
                    "mint":         mint,
                    "entry_price":  price,
                    "score":        score,
                    "confidence":   conviction,
                    "regime_label": regime,
                    "position_usd": position_usd,
                }

                # ── Track 1: Scalp — fast exit, many trades, rapid data ────────
                scalp_signal = {**base_signal, "scalp_mode": True, "source": "spot_scalp"}
                try:
                    fired = await execute_signal(scalp_signal)
                    if fired:
                        log.info(
                            "[SPOT SCALP] Opened %s @ $%.6g  score=%.0f  pos=$%.0f",
                            symbol, price, score, position_usd,
                        )
                except Exception as _se:
                    log.warning("[SPOT SCALP] execute_signal error for %s: %s", symbol, _se)

                # ── Track 2: Swing — adaptive exits, learned hold time ─────────
                # Only fire swing if scalp didn't already create a position
                # (executor.has_open_position guards duplicates per symbol)
                swing_signal = {**base_signal, "scalp_mode": False, "source": "spot_swing"}
                try:
                    fired = await execute_signal(swing_signal)
                    if fired:
                        log.info(
                            "[SPOT SWING] Opened %s @ $%.6g  score=%.0f  pos=$%.0f",
                            symbol, price, score, position_usd,
                        )
                except Exception as _se:
                    log.warning("[SPOT SWING] execute_signal error for %s: %s", symbol, _se)

                await asyncio.sleep(0.5)  # small gap between symbols

        except Exception as _outer_e:
            log.warning("[SPOT SCAN] outer error: %s", _outer_e)


async def _whale_watch_loop():
    """Background: Telethon userbot reading Moby Whale Watch channel (Patch 139)."""
    import sys
    root = _engine_root()
    if root not in sys.path:
        sys.path.insert(0, root)
    await asyncio.sleep(15)  # brief startup delay
    try:
        from utils.whale_watch import start_whale_watch as _ww_start  # type: ignore
        await _ww_start()
    except Exception as _wwe:
        log.warning("whale_watch_loop fatal: %s", _wwe)


@asynccontextmanager
async def lifespan(app: FastAPI):
    task_poller        = asyncio.create_task(signal_poller())
    task_tracker       = asyncio.create_task(outcome_tracker_loop())
    task_perp_mon      = asyncio.create_task(_perp_monitor_loop())
    task_perp_scan     = asyncio.create_task(_perp_signal_scan_loop())
    task_scalp_mon     = asyncio.create_task(_scalp_monitor_loop())
    task_scalp_scan    = asyncio.create_task(_scalp_signal_scan_loop())
    task_spot_mon      = asyncio.create_task(_spot_monitor_loop())
    task_spot_scan     = asyncio.create_task(_spot_signal_scan_loop())
    task_memecoin_scan = asyncio.create_task(_memecoin_scan_loop())  # Patch 115
    task_research      = asyncio.create_task(_research_loop())        # Patch 120
    task_whale_watch   = asyncio.create_task(_whale_watch_loop())     # Patch 139
    log.info("Dashboard started — perp swing + scalp + spot paper bots running.")
    yield
    all_tasks = (task_poller, task_tracker, task_perp_mon, task_perp_scan,
                 task_scalp_mon, task_scalp_scan, task_spot_mon, task_spot_scan,
                 task_memecoin_scan, task_research, task_whale_watch)
    for t in all_tasks:
        t.cancel()
    for t in all_tasks:
        try:
            await t
        except asyncio.CancelledError:
            pass
    log.info("Dashboard shutdown.")


app = FastAPI(title="Memecoin Engine Dashboard", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static files — React dist (served directly, no nginx needed)
_DIST = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "frontend", "dist"))
if os.path.isdir(_DIST):
    app.mount("/assets", StaticFiles(directory=os.path.join(_DIST, "assets")), name="assets")


# Cache-control middleware — Patch 124
# /assets/* are content-hashed by Vite → safe to cache forever (immutable).
# All other responses inherit their own headers (index.html uses no-store above).
from starlette.middleware.base import BaseHTTPMiddleware  # noqa: E402

class _CacheMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/assets/"):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response

app.add_middleware(_CacheMiddleware)

# ── Include routers (Patch 126) ───────────────────────────────────────────────
app.include_router(_router_memecoins)
app.include_router(_router_wallet)
app.include_router(_router_tiers)
app.include_router(_router_portfolio)
app.include_router(_router_spot)           # Patch 128
app.include_router(_router_home)           # Patch 140
app.include_router(_router_whale_watch)    # Patch 140
app.include_router(_router_confluence)     # Patch 143
app.include_router(_router_funding)        # Patch 144
app.include_router(_router_wallets)        # Patch 145


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class LoginRequest(BaseModel):
    password: str


class ConfigUpdateRequest(BaseModel):
    ALERT_THRESHOLD: int | None = None
    REGIME_MIN_SCORE: int | None = None
    MIN_CONFIDENCE_TO_ALERT: str | None = None
    MAX_ALERTS_PER_CYCLE: int | None = None
    PORTFOLIO_USD: float | None = None


class OpenPositionRequest(BaseModel):
    symbol: str
    mint: str | None = None
    pair_address: str | None = None
    entry_price: float
    stop_price: float | None = None
    notes: str | None = None


class ClosePositionRequest(BaseModel):
    symbol: str
    mint: str | None = None
    exit_price: float | None = None


class AddDcaRequest(BaseModel):
    amount_usd: float
    leverage: float = 1.0
    price: float | None = None   # None = use live SOL price


# ---------------------------------------------------------------------------
# Health (no auth)
# ---------------------------------------------------------------------------

@app.get("/api/health")
async def health():
    return get_db_health()


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

@app.post("/api/auth/login")
async def login(body: LoginRequest):
    if not verify_password(body.password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid password")
    token = create_access_token()
    return {"token": token, "expires_in_hours": 12}


@app.get("/api/auth/me")
async def me(user: str = Depends(get_current_user)):
    return {"authenticated": True, "user": user}


# ---------------------------------------------------------------------------
# Signals
# ---------------------------------------------------------------------------

@app.get("/api/signals/recent")
async def signals_recent(
    limit: int = 50,
    _: str = Depends(get_current_user),
):
    return get_recent_signals(limit=min(200, limit))


@app.get("/api/symbols/{symbol}/history")
async def symbol_history(
    symbol: str,
    limit: int = 50,
    _: str = Depends(get_current_user),
):
    return get_symbol_history(symbol=symbol, limit=min(200, limit))


@app.get("/api/symbols/{symbol}/outcomes")
async def symbol_outcomes(
    symbol: str,
    lookback_days: int = 30,
    _: str = Depends(get_current_user),
):
    return get_symbol_outcomes(symbol=symbol, lookback_days=lookback_days)


@app.get("/api/symbols/{symbol}/full")
async def symbol_full(
    symbol: str,
    limit: int = 50,
    _: str = Depends(get_current_user),
):
    """
    Full symbol profile in one call:
      - signals (last N) joined LEFT OUTER with alert_outcomes
      - aggregate outcome stats (1h/4h/24h)
      - live Jupiter price if mint available
    """
    import sqlite3 as _sq
    import httpx as _httpx
    from pathlib import Path as _P
    sym = symbol.upper().strip()
    db  = _P(__file__).resolve().parents[2] / "data_storage" / "engine.db"

    def _fetch():
        conn = _sq.connect(f"file:{db}?mode=ro", uri=True)
        conn.row_factory = _sq.Row
        try:
            rows = conn.execute(
                """
                SELECT
                    s.id, s.ts_utc, s.symbol, s.mint, s.pair_address,
                    s.score_total, s.decision, s.regime_score, s.regime_label,
                    s.liquidity_usd, s.volume_24h, s.price_usd, s.change_24h,
                    s.rel_strength_vs_sol, s.conviction, s.setup_type, s.notes,
                    o.return_1h_pct, o.return_4h_pct, o.return_24h_pct,
                    o.status AS outcome_status
                FROM signals s
                LEFT JOIN alert_outcomes o
                    ON o.symbol = s.symbol
                   AND ABS(JULIANDAY(o.created_ts_utc) - JULIANDAY(s.ts_utc)) < 0.02
                WHERE s.symbol = ?
                ORDER BY s.id DESC
                LIMIT ?
                """,
                (sym, max(1, int(limit))),
            ).fetchall()

            # Aggregate outcomes
            agg = conn.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    COUNT(return_1h_pct) AS n1, AVG(return_1h_pct) AS avg1,
                    SUM(CASE WHEN return_1h_pct  > 0 THEN 1 ELSE 0 END) AS w1,
                    COUNT(return_4h_pct) AS n4, AVG(return_4h_pct) AS avg4,
                    SUM(CASE WHEN return_4h_pct  > 0 THEN 1 ELSE 0 END) AS w4,
                    MAX(return_4h_pct) AS best4, MIN(return_4h_pct) AS worst4,
                    COUNT(return_24h_pct) AS n24, AVG(return_24h_pct) AS avg24,
                    SUM(CASE WHEN return_24h_pct > 0 THEN 1 ELSE 0 END) AS w24
                FROM alert_outcomes
                WHERE symbol = ?
                """,
                (sym,),
            ).fetchone()
            return [dict(r) for r in rows], dict(agg) if agg else {}
        finally:
            conn.close()

    signals, agg = await asyncio.to_thread(_fetch)

    # Live price from Jupiter if mint available
    mint = next((s["mint"] for s in signals if s.get("mint")), None)
    mark_price = None
    if mint:
        try:
            async with _httpx.AsyncClient(timeout=8) as client:
                r = await client.get("https://api.jup.ag/price/v2", params={"ids": mint})
                price_str = r.json().get("data", {}).get(mint, {}).get("price")
                if price_str:
                    mark_price = float(price_str)
        except Exception:
            pass

    def _wr(w, n):
        return round(float(w or 0) / int(n) * 100, 1) if n else 0

    n1, n4, n24 = int(agg.get("n1") or 0), int(agg.get("n4") or 0), int(agg.get("n24") or 0)
    outcomes_agg = {
        "total": int(agg.get("total") or 0),
        "outcomes_1h":  {"n": n1,  "avg": round(float(agg.get("avg1")  or 0), 2), "win_rate": _wr(agg.get("w1"),  n1)},
        "outcomes_4h":  {"n": n4,  "avg": round(float(agg.get("avg4")  or 0), 2), "win_rate": _wr(agg.get("w4"),  n4),
                         "best": round(float(agg.get("best4") or 0), 2), "worst": round(float(agg.get("worst4") or 0), 2)},
        "outcomes_24h": {"n": n24, "avg": round(float(agg.get("avg24") or 0), 2), "win_rate": _wr(agg.get("w24"), n24)},
    }

    return {
        "symbol":      sym,
        "mark_price":  mark_price,
        "signals":     signals,
        "outcomes":    outcomes_agg,
    }


@app.get("/api/signals/leaderboard")
async def signals_leaderboard(
    lookback_hours: int = 24,
    limit: int = 20,
    _: str = Depends(get_current_user),
):
    return get_leaderboard(lookback_hours=lookback_hours, limit=min(50, limit))


# ---------------------------------------------------------------------------
# Performance
# ---------------------------------------------------------------------------

@app.get("/api/performance/summary")
async def perf_summary(
    lookback_hours: int = 168,
    _: str = Depends(get_current_user),
):
    return get_performance_summary(lookback_hours=lookback_hours)


@app.get("/api/performance/outcomes")
async def perf_outcomes(
    lookback_days: int = 7,
    _: str = Depends(get_current_user),
):
    return get_outcome_winrates(lookback_days=lookback_days)


@app.get("/api/performance/equity-curve")
async def perf_equity_curve(
    lookback_days: int = 30,
    horizon_hours: int = 4,
    _: str = Depends(get_current_user),
):
    return get_equity_curve(lookback_days=lookback_days, horizon_hours=horizon_hours)


@app.get("/api/performance/portfolio")
async def perf_portfolio(
    lookback_days: int = 7,
    horizon_hours: int = 4,
    _: str = Depends(get_current_user),
):
    return get_portfolio_simulation_metrics(lookback_days=lookback_days, horizon_hours=horizon_hours)


@app.get("/api/performance/score-distribution")
async def perf_score_dist(
    lookback_hours: int = 168,
    _: str = Depends(get_current_user),
):
    return get_score_histogram(lookback_hours=lookback_hours)


@app.get("/api/performance/outcome-recap")
async def perf_outcome_recap(
    lookback_hours: int = 48,
    limit: int = 15,
    _: str = Depends(get_current_user),
):
    return get_outcome_recap(lookback_hours=lookback_hours, limit=min(50, limit))


@app.get("/api/performance/week")
async def perf_week(
    lookback_days: int = 7,
    _: str = Depends(get_current_user),
):
    from config_editor import _read_env
    env = _read_env()
    return get_weekly_tuning_report(
        lookback_days=lookback_days,
        current_alert_threshold=int(env.get("ALERT_THRESHOLD", 70)),
        current_regime_min_score=int(env.get("REGIME_MIN_SCORE", 35)),
        current_min_confidence_to_alert=env.get("MIN_CONFIDENCE_TO_ALERT", "B"),
    )


# ---------------------------------------------------------------------------
# Regime
# ---------------------------------------------------------------------------

@app.get("/api/regime/timeline")
async def regime_timeline(
    hours: int = 168,
    _: str = Depends(get_current_user),
):
    return get_regime_timeline(hours=hours)


@app.get("/api/regime/current")
async def regime_current(_: str = Depends(get_current_user)):
    return get_current_regime()


@app.get("/api/regime/alerts-overlay")
async def regime_alerts(
    hours: int = 168,
    _: str = Depends(get_current_user),
):
    return get_alerts_for_overlay(hours=hours)


# ---------------------------------------------------------------------------
# Risk
# ---------------------------------------------------------------------------

@app.get("/api/risk/state")
async def risk_state(_: str = Depends(get_current_user)):
    mode = get_risk_mode()
    pause = get_risk_pause_state()
    return {**mode, "pause": pause}


@app.get("/api/risk/symbol-controls")
async def risk_symbol_controls(_: str = Depends(get_current_user)):
    return get_symbol_controls_summary()


@app.get("/api/risk/symbol-controls/detail")
async def risk_symbol_controls_detail(
    limit: int = 50,
    _: str = Depends(get_current_user),
):
    return get_symbol_controls_detail(limit=min(200, limit))


# ---------------------------------------------------------------------------
# Trades / Journal
# ---------------------------------------------------------------------------

@app.get("/api/trades/open")
async def trades_open(_: str = Depends(get_current_user)):
    return get_open_positions(limit=50)


@app.get("/api/trades/closed")
async def trades_closed(
    limit: int = 50,
    _: str = Depends(get_current_user),
):
    return get_closed_trades(limit=min(200, limit))


@app.get("/api/trades/summary")
async def trades_summary(_: str = Depends(get_current_user)):
    return get_trade_summary()


# ---------------------------------------------------------------------------
# Watchlist
# ---------------------------------------------------------------------------

@app.get("/api/watchlist")
async def watchlist(_: str = Depends(get_current_user)):
    cards = await get_watchlist_cards_async()
    return cards


# ---------------------------------------------------------------------------
# News feed (aggregated RSS)
# ---------------------------------------------------------------------------

@app.get("/api/news")
async def news(
    limit: int = 40,
    tag: str | None = None,
    _: str = Depends(get_current_user),
):
    items = await asyncio.to_thread(fetch_news, min(80, limit), tag)
    return items


# ---------------------------------------------------------------------------
# Position management — open / close manual trades from dashboard
# ---------------------------------------------------------------------------

@app.post("/api/trades/open")
async def trade_open(
    body: OpenPositionRequest,
    _: str = Depends(get_current_user),
):
    stop = body.stop_price if body.stop_price else (body.entry_price * 0.9)
    result = await asyncio.to_thread(
        open_manual_position,
        symbol=body.symbol.upper().strip(),
        mint=body.mint,
        pair_address=body.pair_address,
        entry_price=body.entry_price,
        stop_price=stop,
        notes=body.notes or "manual_dashboard_buy",
    )
    return result


@app.post("/api/trades/close")
async def trade_close(
    body: ClosePositionRequest,
    _: str = Depends(get_current_user),
):
    closed = await asyncio.to_thread(
        close_manual_position,
        symbol=body.symbol.upper().strip(),
        mint=body.mint,
        exit_price=body.exit_price,
        notes="manual_dashboard_sold",
    )
    if closed == 0:
        raise HTTPException(status_code=404, detail=f"No open position found for {body.symbol.upper()}")
    return {"closed": closed, "symbol": body.symbol.upper()}


@app.get("/api/trades/live-pnl")
async def trades_live_pnl(_: str = Depends(get_current_user)):
    """
    For every open position that has a mint address, fetch the current price
    from Jupiter Price API v2 and return unrealized PnL.

    Returns a dict keyed by trade id:
      { "42": { "mark_price": 0.0821, "pnl_pct": 12.4, "pnl_usd": null } }

    Positions without a mint are skipped (price cannot be fetched).
    Falls back gracefully — missing prices just won't appear in the result.
    """
    import httpx as _httpx

    positions = await asyncio.to_thread(get_open_positions, 50)
    if not positions:
        return {}

    # Gather unique mints that have an entry price
    mint_to_ids: dict[str, list[tuple[int, float]]] = {}
    for p in positions:
        mint = (p.get("mint") or "").strip()
        entry = float(p.get("entry_price") or 0)
        if mint and entry > 0:
            mint_to_ids.setdefault(mint, []).append((int(p["id"]), entry))

    if not mint_to_ids:
        return {}

    # Jupiter Price API v2 — batch up to 100 mints per call
    mints_csv = ",".join(mint_to_ids.keys())
    result: dict[str, Any] = {}
    try:
        async with _httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                "https://api.jup.ag/price/v2",
                params={"ids": mints_csv},
            )
            data = r.json().get("data", {})

        for mint, trades in mint_to_ids.items():
            price_str = data.get(mint, {}).get("price")
            if not price_str:
                continue
            mark = float(price_str)
            for trade_id, entry in trades:
                pnl_pct = ((mark - entry) / entry) * 100.0
                result[str(trade_id)] = {
                    "mark_price": mark,
                    "pnl_pct": round(pnl_pct, 3),
                }
    except Exception as exc:
        log.warning("live-pnl fetch error: %s", exc)

    return result


# ---------------------------------------------------------------------------
# Jupiter Perps — live SOL leverage position
# ---------------------------------------------------------------------------

@app.get("/api/perps/position")
async def perps_position(_: str = Depends(get_current_user)):
    pos = await asyncio.to_thread(get_perps_position)
    sol = await asyncio.to_thread(get_sol_price)
    return {"position": pos, "sol_price": sol}


# ---------------------------------------------------------------------------
# DCA Tracker
# ---------------------------------------------------------------------------

@app.get("/api/dca")
async def dca_get(_: str = Depends(get_current_user)):
    sol = await asyncio.to_thread(get_sol_price)
    summary = await asyncio.to_thread(get_dca_summary, sol or 0)
    return {"sol_price": sol, **summary}


@app.post("/api/dca")
async def dca_add(
    body: AddDcaRequest,
    _: str = Depends(get_current_user),
):
    if body.amount_usd <= 0:
        raise HTTPException(status_code=400, detail="amount_usd must be positive")
    # Get price: use provided or fetch live
    price = body.price
    if not price:
        price = await asyncio.to_thread(get_sol_price)
    if not price:
        raise HTTPException(status_code=503, detail="Could not fetch SOL price. Provide price manually.")
    entry = await asyncio.to_thread(add_dca_entry_proxy, body.amount_usd, price, body.leverage)
    sol = price
    summary = await asyncio.to_thread(get_dca_summary, sol)
    return {"entry": entry, "sol_price": sol, **summary}


@app.delete("/api/dca")
async def dca_clear(_: str = Depends(get_current_user)):
    await asyncio.to_thread(clear_dca_entries)
    return {"cleared": True}


# ---------------------------------------------------------------------------
# Snapshot — live market overview (regime + top picks + SOL + fear/greed)
# ---------------------------------------------------------------------------

@app.get("/api/snapshot")
async def snapshot(_: str = Depends(get_current_user)):
    import requests as _req

    # Regime + risk
    regime   = get_current_regime()
    risk     = get_risk_mode()
    pause    = get_risk_pause_state()

    # Jupiter perps + SOL price
    pos      = await asyncio.to_thread(get_perps_position)
    sol_price = await asyncio.to_thread(get_sol_price)

    # Fear & Greed
    fg = {}
    try:
        r = await asyncio.to_thread(
            lambda: _req.get("https://api.alternative.me/fng/?limit=1", timeout=6).json()
        )
        fg = (r.get("data") or [{}])[0]
    except Exception:
        pass

    # Open spot positions
    open_pos = get_open_positions(limit=5)

    # Top signals (last 6h, top 5 by score)
    from db_read import get_leaderboard
    top_picks = get_leaderboard(lookback_hours=6, limit=5)

    return {
        "regime":     regime,
        "risk":       {**risk, "pause": pause},
        "sol_price":  sol_price,
        "perps":      pos,
        "fear_greed": {
            "value":          fg.get("value"),
            "classification": fg.get("value_classification"),
        },
        "open_positions": open_pos,
        "top_picks":      top_picks,
    }


# ---------------------------------------------------------------------------
# Global market metrics (market cap, dominance, fear & greed, altcoin season)
# ---------------------------------------------------------------------------

_global_cache: dict = {"data": {}, "ts": 0.0}
_GLOBAL_TTL = 60  # seconds

@app.get("/api/market-global")
async def market_global(_: str = Depends(get_current_user)):
    import time, requests as _req
    now = time.time()
    if now - _global_cache["ts"] < _GLOBAL_TTL and _global_cache["data"]:
        return _global_cache["data"]
    try:
        # CoinGecko global
        cg = await asyncio.to_thread(
            lambda: _req.get("https://api.coingecko.com/api/v3/global", timeout=8).json()
        )
        cg_data = cg.get("data", {})
        total_mcap = cg_data.get("total_market_cap", {}).get("usd")
        mcap_change = cg_data.get("market_cap_change_percentage_24h_usd")
        btc_dom = cg_data.get("market_cap_percentage", {}).get("btc")
        eth_dom = cg_data.get("market_cap_percentage", {}).get("eth")

        # Fear & Greed
        fg_raw = await asyncio.to_thread(
            lambda: _req.get("https://api.alternative.me/fng/?limit=1", timeout=6).json()
        )
        fg = (fg_raw.get("data") or [{}])[0]

        # Top 10 coins 24h change for simple RSI proxy (% positive = altcoin heat)
        top = await asyncio.to_thread(
            lambda: _req.get(
                "https://api.coingecko.com/api/v3/coins/markets"
                "?vs_currency=usd&order=market_cap_desc&per_page=20&page=1"
                "&price_change_percentage=24h",
                timeout=8,
            ).json()
        )
        changes = [c.get("price_change_percentage_24h") or 0 for c in top if c.get("id") != "tether" and c.get("id") != "usdc" and c.get("id") != "usdt"]
        positive = sum(1 for x in changes if x > 0)
        avg_change = sum(changes) / len(changes) if changes else 0
        # Altcoin season: % of top 20 (ex-stables) outperforming → scale 0-100
        btc_change = next((c.get("price_change_percentage_24h", 0) for c in top if c.get("id") == "bitcoin"), 0)
        outperforming = sum(1 for x in changes if x > (btc_change or 0))
        altcoin_season = round((outperforming / len(changes)) * 100) if changes else 0

        # Simplified RSI proxy: map avg 24h change to 0-100 (0% = 50, +10% = 80, -10% = 20)
        rsi_proxy = max(0, min(100, 50 + avg_change * 3))

        data = {
            "market_cap_usd":       total_mcap,
            "market_cap_change_24h": mcap_change,
            "btc_dominance":        btc_dom,
            "eth_dominance":        eth_dom,
            "fear_greed_value":     fg.get("value"),
            "fear_greed_label":     fg.get("value_classification"),
            "altcoin_season":       altcoin_season,
            "top20_positive_pct":   round(positive / len(changes) * 100) if changes else 0,
            "avg_change_24h":       round(avg_change, 2),
            "rsi_proxy":            round(rsi_proxy, 1),
        }
        _global_cache["data"] = data
        _global_cache["ts"] = now
        return data
    except Exception as exc:
        log.warning("market_global error: %s", exc)
        return _global_cache.get("data") or {}


# Market cycle endpoints (Phase 3)
# ---------------------------------------------------------------------------

@app.get("/api/market/cycle-summary")
async def market_cycle_summary(_: str = Depends(get_current_user)):
    """Return current market cycle phase, playbooks, and 14d history."""
    try:
        from utils.market_cycle import get_cycle_summary  # type: ignore
        return await asyncio.to_thread(get_cycle_summary)
    except Exception as exc:
        log.warning("market_cycle_summary error: %s", exc)
        return {"current_phase": "TRANSITION", "phase_emoji": "↔", "playbooks": {}, "history_14d": []}


@app.get("/api/market/cycle-history")
async def market_cycle_history(days: int = 90, _: str = Depends(get_current_user)):
    """Return daily phase labels + avg regime score for the last N days."""
    try:
        from utils.market_cycle import get_cycle_history  # type: ignore
        return await asyncio.to_thread(get_cycle_history, min(days, 365))
    except Exception as exc:
        log.warning("market_cycle_history error: %s", exc)
        return []


@app.get("/api/market/cycle-playbooks")
async def market_cycle_playbooks(_: str = Depends(get_current_user)):
    """Return learned playbook parameters for all three cycle phases."""
    try:
        from utils.market_cycle import get_all_playbooks  # type: ignore
        return await asyncio.to_thread(get_all_playbooks)
    except Exception as exc:
        log.warning("market_cycle_playbooks error: %s", exc)
        return {}


# Live crypto prices — BTC / ETH / SOL
# ---------------------------------------------------------------------------

_prices_cache: dict = {"data": {}, "ts": 0.0}
_PRICES_TTL = 30  # seconds

@app.get("/api/prices")
async def crypto_prices(_: str = Depends(get_current_user)):
    import time, requests as _req
    now = time.time()
    if now - _prices_cache["ts"] < _PRICES_TTL and _prices_cache["data"]:
        return _prices_cache["data"]
    try:
        r = await asyncio.to_thread(
            lambda: _req.get(
                "https://api.coingecko.com/api/v3/simple/price"
                "?ids=bitcoin,ethereum,solana"
                "&vs_currencies=usd"
                "&include_24hr_change=true",
                timeout=8,
            ).json()
        )
        data = {
            "BTC": {
                "price": r.get("bitcoin", {}).get("usd"),
                "change_24h": r.get("bitcoin", {}).get("usd_24h_change"),
            },
            "ETH": {
                "price": r.get("ethereum", {}).get("usd"),
                "change_24h": r.get("ethereum", {}).get("usd_24h_change"),
            },
            "SOL": {
                "price": r.get("solana", {}).get("usd"),
                "change_24h": r.get("solana", {}).get("usd_24h_change"),
            },
        }
        _prices_cache["data"] = data
        _prices_cache["ts"] = now
        return data
    except Exception as exc:
        log.warning("crypto_prices error: %s", exc)
        return _prices_cache.get("data") or {}


# ---------------------------------------------------------------------------
# Config editor
# ---------------------------------------------------------------------------

@app.get("/api/config")
async def config_get(_: str = Depends(get_current_user)):
    return get_config()


@app.post("/api/config")
async def config_update(
    body: ConfigUpdateRequest,
    _: str = Depends(get_current_user),
):
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(status_code=400, detail="No values provided.")
    result = validate_and_update(updates)
    if not result["success"]:
        raise HTTPException(status_code=422, detail=result["errors"])
    return result


# ---------------------------------------------------------------------------
# WebSocket — live signal feed
# ---------------------------------------------------------------------------

@app.websocket("/ws/signals")
async def ws_signals(ws: WebSocket):
    await manager.connect(ws)
    try:
        # First frame must be auth token
        auth_msg = await asyncio.wait_for(ws.receive_json(), timeout=10.0)
        token = (auth_msg or {}).get("token", "")
        if not validate_ws_token(token):
            await ws.send_json({"type": "error", "message": "Unauthorized"})
            await ws.close(code=4001)
            manager.disconnect(ws)
            return

        await ws.send_json({"type": "connected", "message": "Signal feed active"})

        # Send last 20 signals on connect
        recent = get_recent_signals(limit=20)
        for sig in reversed(recent):
            await ws.send_json({"type": "signal", "data": sig})

        # Keep alive — receive pings, stay in broadcast pool
        while True:
            try:
                data = await asyncio.wait_for(ws.receive_json(), timeout=60.0)
                if (data or {}).get("type") == "ping":
                    await ws.send_json({"type": "pong"})
            except asyncio.TimeoutError:
                await ws.send_json({"type": "ping"})
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        log.exception("WS error: %s", exc)
    finally:
        manager.disconnect(ws)


# ---------------------------------------------------------------------------
# Brain endpoints — outcome analytics for self-learning
# ---------------------------------------------------------------------------

def _ro_conn_brain():
    """Return a read-only sqlite3 connection to engine.db."""
    import sqlite3 as _sq
    from pathlib import Path as _P
    db = _P(__file__).resolve().parents[2] / "data_storage" / "engine.db"
    conn = _sq.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = _sq.Row
    return conn


@app.get("/api/brain/status")
async def brain_status(_: str = Depends(get_current_user)):
    """Queue health + last evaluated timestamp."""
    conn = _ro_conn_brain()
    try:
        row = conn.execute(
            "SELECT COUNT(*) as total,"
            " SUM(CASE WHEN status='PENDING' THEN 1 ELSE 0 END) as pending,"
            " SUM(CASE WHEN status='COMPLETE' THEN 1 ELSE 0 END) as complete"
            " FROM alert_outcomes"
        ).fetchone()
        last = conn.execute(
            "SELECT MAX(evaluated_4h_ts_utc) as ts FROM alert_outcomes WHERE return_4h_pct IS NOT NULL"
        ).fetchone()
        return {
            "total": int(row["total"] or 0),
            "pending": int(row["pending"] or 0),
            "complete": int(row["complete"] or 0),
            "last_evaluated_4h": last["ts"] if last else None,
        }
    finally:
        conn.close()


@app.get("/api/brain/score-vs-return")
async def brain_score_vs_return(
    lookback_days: int = 30,
    horizon: int = 4,
    _: str = Depends(get_current_user),
):
    """
    Returns scatter-plot data: signal score → outcome return.
    Also returns bucketed averages (score bands of 5 pts).
    """
    ret_col = {1: "return_1h_pct", 4: "return_4h_pct", 24: "return_24h_pct"}.get(horizon, "return_4h_pct")
    from datetime import datetime, timedelta, timezone
    cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).isoformat()
    conn = _ro_conn_brain()
    try:
        rows = conn.execute(
            f"""
            SELECT score, {ret_col} AS ret, symbol, confidence, regime_label, created_ts_utc
            FROM alert_outcomes
            WHERE score IS NOT NULL AND {ret_col} IS NOT NULL AND created_ts_utc >= ?
            ORDER BY created_ts_utc ASC
            """,
            (cutoff,),
        ).fetchall()
        points = [
            {
                "score": round(float(r["score"]), 1),
                "ret": round(float(r["ret"]), 2),
                "symbol": r["symbol"],
                "confidence": r["confidence"],
                "regime_label": r["regime_label"],
                "ts": r["created_ts_utc"],
            }
            for r in rows
        ]
        # Bucketed averages (every 5-pt score band)
        from collections import defaultdict
        buckets: dict[int, list[float]] = defaultdict(list)
        for p in points:
            band = int(p["score"] // 5) * 5
            buckets[band].append(p["ret"])
        bands = []
        for band in sorted(buckets):
            rets = buckets[band]
            wins = [r for r in rets if r > 0]
            bands.append({
                "score_band": f"{band}–{band+5}",
                "score_mid": band + 2.5,
                "n": len(rets),
                "avg_ret": round(sum(rets) / len(rets), 2),
                "win_rate": round(len(wins) / len(rets) * 100, 1),
            })
        return {"points": points, "bands": bands, "horizon_hours": horizon, "n": len(points)}
    finally:
        conn.close()


@app.get("/api/brain/regime-edge")
async def brain_regime_edge(
    lookback_days: int = 30,
    horizon: int = 4,
    _: str = Depends(get_current_user),
):
    """Win rate and avg return segmented by regime label."""
    ret_col = {1: "return_1h_pct", 4: "return_4h_pct", 24: "return_24h_pct"}.get(horizon, "return_4h_pct")
    from datetime import datetime, timedelta, timezone
    cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).isoformat()
    conn = _ro_conn_brain()
    try:
        rows = conn.execute(
            f"""
            SELECT regime_label,
                   COUNT(*) AS n,
                   AVG({ret_col}) AS avg_ret,
                   SUM(CASE WHEN {ret_col} > 0 THEN 1 ELSE 0 END) AS wins,
                   MAX({ret_col}) AS best,
                   MIN({ret_col}) AS worst
            FROM alert_outcomes
            WHERE {ret_col} IS NOT NULL AND created_ts_utc >= ?
            GROUP BY regime_label
            ORDER BY avg_ret DESC
            """,
            (cutoff,),
        ).fetchall()
        return [
            {
                "regime_label": r["regime_label"] or "UNKNOWN",
                "n": int(r["n"]),
                "avg_ret": round(float(r["avg_ret"] or 0), 2),
                "win_rate": round(float(r["wins"] or 0) / int(r["n"]) * 100, 1) if r["n"] else 0,
                "best": round(float(r["best"] or 0), 2),
                "worst": round(float(r["worst"] or 0), 2),
            }
            for r in rows
        ]
    finally:
        conn.close()


@app.get("/api/brain/confidence-calibration")
async def brain_confidence_calibration(
    lookback_days: int = 30,
    horizon: int = 4,
    _: str = Depends(get_current_user),
):
    """Win rate and avg return by confidence tier (A / B / C)."""
    ret_col = {1: "return_1h_pct", 4: "return_4h_pct", 24: "return_24h_pct"}.get(horizon, "return_4h_pct")
    from datetime import datetime, timedelta, timezone
    cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).isoformat()
    conn = _ro_conn_brain()
    try:
        rows = conn.execute(
            f"""
            SELECT confidence,
                   COUNT(*) AS n,
                   AVG({ret_col}) AS avg_ret,
                   SUM(CASE WHEN {ret_col} > 0 THEN 1 ELSE 0 END) AS wins
            FROM alert_outcomes
            WHERE {ret_col} IS NOT NULL AND created_ts_utc >= ?
            GROUP BY confidence
            ORDER BY confidence ASC
            """,
            (cutoff,),
        ).fetchall()
        return [
            {
                "confidence": r["confidence"] or "?",
                "n": int(r["n"]),
                "avg_ret": round(float(r["avg_ret"] or 0), 2),
                "win_rate": round(float(r["wins"] or 0) / int(r["n"]) * 100, 1) if r["n"] else 0,
            }
            for r in rows
        ]
    finally:
        conn.close()


@app.get("/api/brain/horizon-decay")
async def brain_horizon_decay(
    lookback_days: int = 30,
    _: str = Depends(get_current_user),
):
    """Compare avg return across 1h / 4h / 24h for the same signals."""
    from datetime import datetime, timedelta, timezone
    cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).isoformat()
    conn = _ro_conn_brain()
    try:
        row = conn.execute(
            """
            SELECT
              COUNT(return_1h_pct)  AS n1,
              AVG(return_1h_pct)    AS avg1,
              SUM(CASE WHEN return_1h_pct  > 0 THEN 1 ELSE 0 END) AS w1,
              COUNT(return_4h_pct)  AS n4,
              AVG(return_4h_pct)    AS avg4,
              SUM(CASE WHEN return_4h_pct  > 0 THEN 1 ELSE 0 END) AS w4,
              COUNT(return_24h_pct) AS n24,
              AVG(return_24h_pct)   AS avg24,
              SUM(CASE WHEN return_24h_pct > 0 THEN 1 ELSE 0 END) AS w24
            FROM alert_outcomes
            WHERE created_ts_utc >= ?
            """,
            (cutoff,),
        ).fetchone()
        def _wr(w, n): return round(float(w or 0) / int(n) * 100, 1) if n else 0
        return [
            {"horizon": "1h",  "n": int(row["n1"]  or 0), "avg_ret": round(float(row["avg1"]  or 0), 2), "win_rate": _wr(row["w1"],  row["n1"])},
            {"horizon": "4h",  "n": int(row["n4"]  or 0), "avg_ret": round(float(row["avg4"]  or 0), 2), "win_rate": _wr(row["w4"],  row["n4"])},
            {"horizon": "24h", "n": int(row["n24"] or 0), "avg_ret": round(float(row["avg24"] or 0), 2), "win_rate": _wr(row["w24"], row["n24"])},
        ]
    finally:
        conn.close()


@app.get("/api/brain/symbol-edge")
async def brain_symbol_edge(
    lookback_days: int = 30,
    min_signals: int = 2,
    horizon: int = 4,
    _: str = Depends(get_current_user),
):
    """Per-symbol win rate and avg return — ranked by edge."""
    ret_col = {1: "return_1h_pct", 4: "return_4h_pct", 24: "return_24h_pct"}.get(horizon, "return_4h_pct")
    from datetime import datetime, timedelta, timezone
    cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).isoformat()
    conn = _ro_conn_brain()
    try:
        rows = conn.execute(
            f"""
            SELECT symbol,
                   COUNT(*) AS n,
                   AVG({ret_col}) AS avg_ret,
                   SUM(CASE WHEN {ret_col} > 0 THEN 1 ELSE 0 END) AS wins,
                   MAX({ret_col}) AS best,
                   MIN({ret_col}) AS worst
            FROM alert_outcomes
            WHERE {ret_col} IS NOT NULL AND created_ts_utc >= ?
            GROUP BY symbol
            HAVING COUNT(*) >= ?
            ORDER BY avg_ret DESC
            LIMIT 30
            """,
            (cutoff, min_signals),
        ).fetchall()
        return [
            {
                "symbol": r["symbol"],
                "n": int(r["n"]),
                "avg_ret": round(float(r["avg_ret"] or 0), 2),
                "win_rate": round(float(r["wins"] or 0) / int(r["n"]) * 100, 1) if r["n"] else 0,
                "best": round(float(r["best"] or 0), 2),
                "worst": round(float(r["worst"] or 0), 2),
            }
            for r in rows
        ]
    finally:
        conn.close()


@app.get("/api/brain/weekly-drift")
async def brain_weekly_drift(_: str = Depends(get_current_user)):
    """
    Win rate and avg 4h return per calendar week for the last 8 weeks.
    Lets you see if the engine is improving or degrading over time.
    """
    conn = _ro_conn_brain()
    try:
        rows = conn.execute(
            """
            SELECT
                strftime('%Y-W%W', created_ts_utc) AS week,
                COUNT(return_4h_pct) AS n,
                AVG(return_4h_pct) AS avg_ret,
                SUM(CASE WHEN return_4h_pct > 0 THEN 1 ELSE 0 END) AS wins
            FROM alert_outcomes
            WHERE return_4h_pct IS NOT NULL
              AND created_ts_utc >= datetime('now', '-56 days')
            GROUP BY week
            ORDER BY week ASC
            """
        ).fetchall()
        return [
            {
                "week": r["week"],
                "n": int(r["n"]),
                "avg_ret": round(float(r["avg_ret"] or 0), 2),
                "win_rate": round(float(r["wins"] or 0) / int(r["n"]) * 100, 1) if r["n"] else 0,
            }
            for r in rows
        ]
    finally:
        conn.close()


@app.get("/api/brain/suggest")
async def brain_suggest(
    lookback_days: int = 30,
    _: str = Depends(get_current_user),
):
    """
    Run the optimizer and return current config vs recommended config.
    Uses the existing get_weekly_tuning_report logic from utils/db.py.
    """
    try:
        from config_editor import get_config
        cfg = get_config()
        report = get_weekly_tuning_report(
            lookback_days=lookback_days,
            current_alert_threshold=int(cfg.get("ALERT_THRESHOLD", 72)),
            current_regime_min_score=int(cfg.get("REGIME_MIN_SCORE", 35)),
            current_min_confidence_to_alert=str(cfg.get("MIN_CONFIDENCE_TO_ALERT", "B")),
            min_outcomes_4h=5,
        )
        return report
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ---------------------------------------------------------------------------
# AI Chat — Claude-powered query interface over live DB snapshot
# ---------------------------------------------------------------------------

class ChatRequest(BaseModel):
    message: str


def _build_db_context() -> str:
    """
    Pull a compact snapshot of the engine's live state from SQLite.
    Returns a plain-text block that fits comfortably in Claude's context.
    """
    import sqlite3 as _sq
    from pathlib import Path as _P
    from datetime import datetime, timedelta, timezone

    db   = _P(__file__).resolve().parents[2] / "data_storage" / "engine.db"
    now  = datetime.now(timezone.utc)
    d7   = (now - timedelta(days=7)).isoformat()
    d30  = (now - timedelta(days=30)).isoformat()

    lines: list[str] = []

    try:
        conn = _sq.connect(f"file:{db}?mode=ro", uri=True)
        conn.row_factory = _sq.Row

        # ── Engine config ──────────────────────────────────────────────────
        try:
            from config_editor import get_config
            cfg = get_config()
            lines.append("=== ENGINE CONFIG ===")
            lines.append(f"ALERT_THRESHOLD: {cfg.get('ALERT_THRESHOLD')}")
            lines.append(f"REGIME_MIN_SCORE: {cfg.get('REGIME_MIN_SCORE')}")
            lines.append(f"MIN_CONFIDENCE_TO_ALERT: {cfg.get('MIN_CONFIDENCE_TO_ALERT')}")
            lines.append(f"MAX_ALERTS_PER_CYCLE: {cfg.get('MAX_ALERTS_PER_CYCLE')}")
            lines.append("")
        except Exception:
            pass

        # ── Current regime + risk ──────────────────────────────────────────
        try:
            regime = get_current_regime()
            risk   = get_risk_mode()
            lines.append("=== CURRENT STATE ===")
            lines.append(f"Regime: {regime.get('label','?')} (score {regime.get('score','?')})")
            lines.append(f"Risk mode: {risk.get('mode','?')} | Size multiplier: {risk.get('size_multiplier','?')}")
            lines.append("")
        except Exception:
            pass

        # ── Signal stats last 7d ───────────────────────────────────────────
        row = conn.execute(
            """
            SELECT COUNT(*) as total,
                   SUM(CASE WHEN decision LIKE '%ALERT%' AND decision NOT LIKE '%DRY%' THEN 1 ELSE 0 END) as alerts,
                   AVG(score_total) as avg_score,
                   MAX(score_total) as max_score
            FROM signals WHERE ts_utc >= ?
            """, (d7,)
        ).fetchone()
        if row:
            lines.append("=== SIGNAL STATS (last 7 days) ===")
            lines.append(f"Total scans: {row['total']} | Alerts fired: {row['alerts']} | Avg score: {round(row['avg_score'] or 0, 1)} | Best score: {round(row['max_score'] or 0, 1)}")

        # Top 10 symbols by alert count (7d)
        top = conn.execute(
            """
            SELECT symbol, COUNT(*) as n, MAX(score_total) as best
            FROM signals
            WHERE ts_utc >= ? AND decision LIKE '%ALERT%' AND decision NOT LIKE '%DRY%'
            GROUP BY symbol ORDER BY n DESC LIMIT 10
            """, (d7,)
        ).fetchall()
        if top:
            lines.append(f"Top alert symbols (7d): " + ", ".join(f"{r['symbol']}({r['n']}x best={round(r['best'] or 0,0)})" for r in top))
        lines.append("")

        # ── Recent signals (last 20) ───────────────────────────────────────
        sigs = conn.execute(
            """
            SELECT ts_utc, symbol, score_total, decision, regime_label, conviction
            FROM signals ORDER BY id DESC LIMIT 20
            """).fetchall()
        if sigs:
            lines.append("=== RECENT SIGNALS (last 20) ===")
            conv_map = {3: "A", 2: "B", 1: "C"}
            for s in sigs:
                conv = conv_map.get(s["conviction"], "?")
                lines.append(
                    f"{s['ts_utc'][:16]} | {s['symbol']:<10} | score={round(s['score_total'] or 0,0):<4} "
                    f"| {s['decision']:<25} | regime={s['regime_label'] or '?'} | conv={conv}"
                )
        lines.append("")

        # ── Outcome performance (all time) ─────────────────────────────────
        out = conn.execute(
            """
            SELECT symbol,
                   COUNT(*) as n,
                   AVG(return_4h_pct) as avg4,
                   SUM(CASE WHEN return_4h_pct > 0 THEN 1 ELSE 0 END) as w4,
                   AVG(return_1h_pct) as avg1,
                   AVG(return_24h_pct) as avg24
            FROM alert_outcomes
            WHERE return_4h_pct IS NOT NULL
            GROUP BY symbol ORDER BY avg4 DESC
            """
        ).fetchall()
        if out:
            lines.append("=== OUTCOME PERFORMANCE BY SYMBOL (all evaluated, 4h) ===")
            for o in out:
                wr = round(float(o["w4"] or 0) / int(o["n"]) * 100, 0) if o["n"] else 0
                lines.append(
                    f"{o['symbol']:<10} n={o['n']} | avg4h={round(o['avg4'] or 0,2)}% | wr={wr}% "
                    f"| avg1h={round(o['avg1'] or 0,2)}% | avg24h={round(o['avg24'] or 0,2)}%"
                )
        lines.append("")

        # ── Regime edge (outcomes by regime) ──────────────────────────────
        rout = conn.execute(
            """
            SELECT regime_label,
                   COUNT(*) as n,
                   AVG(return_4h_pct) as avg4,
                   SUM(CASE WHEN return_4h_pct > 0 THEN 1 ELSE 0 END) as wins
            FROM alert_outcomes
            WHERE return_4h_pct IS NOT NULL
            GROUP BY regime_label ORDER BY avg4 DESC
            """
        ).fetchall()
        if rout:
            lines.append("=== OUTCOME PERFORMANCE BY REGIME (4h) ===")
            for r in rout:
                wr = round(float(r["wins"] or 0) / int(r["n"]) * 100, 0) if r["n"] else 0
                lines.append(f"{(r['regime_label'] or 'UNKNOWN'):<20} n={r['n']} | avg4h={round(r['avg4'] or 0,2)}% | wr={wr}%")
        lines.append("")

        # ── Pending outcomes ───────────────────────────────────────────────
        pend = conn.execute(
            "SELECT COUNT(*) as n FROM alert_outcomes WHERE status='PENDING'"
        ).fetchone()
        comp = conn.execute(
            "SELECT COUNT(*) as n FROM alert_outcomes WHERE status='COMPLETE'"
        ).fetchone()
        lines.append(f"=== OUTCOME TRACKER ===")
        lines.append(f"Complete: {comp['n']} | Pending: {pend['n']}")
        lines.append("")

        # ── Open positions ─────────────────────────────────────────────────
        positions = get_open_positions(limit=20)
        if positions:
            lines.append("=== OPEN POSITIONS ===")
            for p in positions:
                lines.append(f"{p.get('symbol')} entry={p.get('entry_price')} stop={p.get('stop_price')} opened={p.get('opened_ts_utc','')[:10]}")
        else:
            lines.append("=== OPEN POSITIONS ===\nNone currently open.")
        lines.append("")

        # ── Trade journal summary ──────────────────────────────────────────
        summary = get_trade_summary()
        if summary:
            lines.append("=== TRADE JOURNAL SUMMARY ===")
            lines.append(
                f"Closed trades: {summary.get('total_closed',0)} | "
                f"Win rate: {round(summary.get('win_rate',0),1)}% | "
                f"Avg PnL: {round(summary.get('avg_pnl',0),2)}% | "
                f"Avg R: {round(summary.get('avg_r',0),2)}R | "
                f"Total PnL: {round(summary.get('total_pnl',0),2)}%"
            )
        lines.append("")

        conn.close()
    except Exception as exc:
        lines.append(f"[DB context error: {exc}]")

    return "\n".join(lines)


_SYSTEM_PROMPT = """\
You are the Abrons Engine AI — a personal trading analyst embedded inside a \
Solana memecoin signal dashboard. You have direct access to the engine's live \
database snapshot provided below.

Your role:
- Answer questions about signal performance, outcome data, regime edge, and config
- Give concise, data-driven answers — use exact numbers from the context
- If the data is sparse (few outcomes), say so honestly and suggest waiting
- Never make up numbers not present in the context
- Keep answers short and direct — the user is an active trader, not a student
- You can give recommendations (e.g. "raise your threshold") when the data supports it
- Format with short paragraphs or bullet points, no markdown headers
- Today's date/time (UTC): {now}

DB SNAPSHOT:
{context}
"""


@app.post("/api/chat")
async def ai_chat(
    body: ChatRequest,
    _: str = Depends(get_current_user),
):
    """
    Claude-powered chat over live engine DB snapshot.
    Requires ANTHROPIC_API_KEY in environment.
    Returns streaming plain-text via StreamingResponse.
    """
    import httpx as _httpx
    from fastapi.responses import StreamingResponse
    from datetime import datetime, timezone

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise HTTPException(status_code=503, detail="ANTHROPIC_API_KEY not configured on server.")

    message = (body.message or "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="message is required")
    if len(message) > 1000:
        raise HTTPException(status_code=400, detail="message too long (max 1000 chars)")

    # Build context in a thread (DB reads)
    context  = await asyncio.to_thread(_build_db_context)
    now_str  = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    sys_prompt = _SYSTEM_PROMPT.format(context=context, now=now_str)

    async def _stream():
        try:
            async with _httpx.AsyncClient(timeout=60) as client:
                async with client.stream(
                    "POST",
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key":         api_key,
                        "anthropic-version": "2023-06-01",
                        "content-type":      "application/json",
                    },
                    json={
                        "model":      "claude-haiku-4-5",
                        "max_tokens": 512,
                        "stream":     True,
                        "system":     sys_prompt,
                        "messages":   [{"role": "user", "content": message}],
                    },
                ) as resp:
                    if resp.status_code != 200:
                        body_text = await resp.aread()
                        log.warning("Claude API error %s: %s", resp.status_code, body_text[:200])
                        yield f"Error from Claude API: {resp.status_code}"
                        return
                    async for line in resp.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        chunk = line[6:]
                        if chunk == "[DONE]":
                            break
                        try:
                            import json as _json
                            evt = _json.loads(chunk)
                            # Anthropic streaming: content_block_delta events
                            if evt.get("type") == "content_block_delta":
                                text = evt.get("delta", {}).get("text", "")
                                if text:
                                    yield text
                        except Exception:
                            pass
        except Exception as exc:
            log.warning("Chat stream error: %s", exc)
            yield f"\n[Stream error: {exc}]"

    return StreamingResponse(_stream(), media_type="text/plain")


# ---------------------------------------------------------------------------
# Auto-tune history — reads data_storage/tuning_log.json written by auto_tune.py
# ---------------------------------------------------------------------------

_TUNING_LOG = os.path.join(os.path.dirname(__file__), "..", "..", "data_storage", "tuning_log.json")


# ---------------------------------------------------------------------------
# Outcome-Gated Signal Feed — signals annotated with their actual outcomes
# ---------------------------------------------------------------------------

@app.get("/api/signals/outcome-feed")
async def signals_outcome_feed(
    lookback_days: int = 7,
    horizon: int = 4,
    min_signals: int = 1,
    decision: str = "",
    _: str = Depends(get_current_user),
):
    """
    Returns signals that have resolved outcome data, annotated with actual returns.
    Used by the outcome-gated feed and calibration panel.
    Horizon: 1, 4, or 24 (hours).
    """
    col = {1: "return_1h_pct", 4: "return_4h_pct", 24: "return_24h_pct"}.get(horizon, "return_4h_pct")
    cutoff = f"datetime('now', '-{lookback_days} days')"
    decision_filter = ""
    if decision:
        decision_filter = "AND s.decision = :decision"

    import sqlite3 as _sqlite3, os as _os
    _DB = _os.path.normpath(_os.path.join(_os.path.dirname(__file__), "..", "..", "data_storage", "engine.db"))
    conn = _sqlite3.connect(f"file:{_DB}?mode=ro", uri=True)
    conn.row_factory = _sqlite3.Row
    try:
        rows = conn.execute(f"""
            SELECT
                s.id,
                s.ts_utc,
                s.symbol,
                s.score_total,
                s.decision,
                s.conviction,
                s.regime_label,
                s.change_24h,
                o.{col}                AS outcome_ret,
                o.return_1h_pct       AS ret_1h,
                o.return_4h_pct       AS ret_4h,
                o.return_24h_pct      AS ret_24h,
                o.status              AS outcome_status,
                o.confidence          AS outcome_conf
            FROM signals s
            INNER JOIN alert_outcomes o
                ON  o.symbol = s.symbol
                AND ABS(JULIANDAY(o.created_ts_utc) - JULIANDAY(s.ts_utc)) < 0.02
            WHERE s.ts_utc >= {cutoff}
              AND o.{col} IS NOT NULL
              AND s.score_total IS NOT NULL
              {decision_filter}
            ORDER BY s.ts_utc DESC
            LIMIT 200
        """, {"decision": decision} if decision else {}).fetchall()

        return [dict(r) for r in rows]
    finally:
        conn.close()


@app.get("/api/signals/calibration")
async def signals_calibration(
    lookback_days: int = 14,
    _: str = Depends(get_current_user),
):
    """
    Score-band calibration: for each 5-pt score band, returns win rates and avg returns
    across all three horizons. Powers the calibration recommendations panel.
    """
    import sqlite3 as _sqlite3, os as _os
    _DB = _os.path.normpath(_os.path.join(_os.path.dirname(__file__), "..", "..", "data_storage", "engine.db"))
    conn = _sqlite3.connect(f"file:{_DB}?mode=ro", uri=True)
    conn.row_factory = _sqlite3.Row
    cutoff = f"datetime('now', '-{lookback_days} days')"
    try:
        rows = conn.execute(f"""
            SELECT
                CAST(ROUND(s.score_total / 5) * 5 AS INTEGER) AS band_mid,
                COUNT(*)                        AS n,
                AVG(o.return_1h_pct)            AS avg_1h,
                AVG(o.return_4h_pct)            AS avg_4h,
                AVG(o.return_24h_pct)           AS avg_24h,
                SUM(CASE WHEN o.return_1h_pct  > 0 THEN 1 ELSE 0 END) * 100.0 / COUNT(*) AS wr_1h,
                SUM(CASE WHEN o.return_4h_pct  > 0 THEN 1 ELSE 0 END) * 100.0 / COUNT(*) AS wr_4h,
                SUM(CASE WHEN o.return_24h_pct > 0 THEN 1 ELSE 0 END) * 100.0 / COUNT(*) AS wr_24h
            FROM signals s
            INNER JOIN alert_outcomes o
                ON  o.symbol = s.symbol
                AND ABS(JULIANDAY(o.created_ts_utc) - JULIANDAY(s.ts_utc)) < 0.02
            WHERE s.ts_utc >= {cutoff}
              AND s.score_total IS NOT NULL
              AND o.return_4h_pct IS NOT NULL
            GROUP BY band_mid
            HAVING n >= 3
            ORDER BY band_mid ASC
        """).fetchall()

        # Find the optimal threshold (band_mid with best wr_4h and positive avg_4h)
        bands = [dict(r) for r in rows]
        best_band = None
        best_score = -999
        for b in bands:
            if b["avg_4h"] is not None and b["wr_4h"] is not None and b["n"] >= 5:
                score = (b["wr_4h"] or 0) + (b["avg_4h"] or 0) * 2
                if score > best_score:
                    best_score = score
                    best_band = b

        return {
            "bands": bands,
            "optimal_threshold": best_band["band_mid"] if best_band else None,
            "optimal_wr_4h": round(best_band["wr_4h"], 1) if best_band else None,
            "optimal_avg_4h": round(best_band["avg_4h"], 2) if best_band else None,
            "lookback_days": lookback_days,
            "total_outcomes": sum(b["n"] for b in bands),
        }
    finally:
        conn.close()


# NOTE: /api/signals/{signal_id} MUST be registered AFTER all named /api/signals/* routes
# so FastAPI doesn't match "outcome-feed", "calibration", "leaderboard" as signal_ids.
@app.get("/api/signals/{signal_id}")
async def signal_detail(
    signal_id: int,
    _: str = Depends(get_current_user),
):
    sig = get_signal_by_id(signal_id)
    if not sig:
        raise HTTPException(status_code=404, detail=f"Signal {signal_id} not found")
    outcome = get_signal_outcome(signal_id)
    return {"signal": sig, "outcome": outcome}


# ---------------------------------------------------------------------------
# Regime Heatmap — hourly regime scores as a calendar heatmap + momentum
# ---------------------------------------------------------------------------

@app.get("/api/regime/heatmap")
async def regime_heatmap(
    days: int = 30,
    _: str = Depends(get_current_user),
):
    """
    Returns hourly regime scores bucketed by day x hour for a heatmap grid.
    Also returns per-day aggregate (avg score, dominant label, alert count).
    """
    import sqlite3 as _sqlite3, os as _os
    _DB = _os.path.normpath(_os.path.join(_os.path.dirname(__file__), "..", "..", "data_storage", "engine.db"))
    conn = _sqlite3.connect(f"file:{_DB}?mode=ro", uri=True)
    conn.row_factory = _sqlite3.Row
    try:
        # Daily aggregates from regime_snapshots
        daily = conn.execute(f"""
            SELECT
                DATE(ts_utc)                            AS day,
                AVG(regime_score)                       AS avg_score,
                MIN(regime_score)                       AS min_score,
                MAX(regime_score)                       AS max_score,
                COUNT(*)                                AS snapshots,
                -- dominant label = most common non-null label that day
                (SELECT regime_label FROM regime_snapshots r2
                 WHERE DATE(r2.ts_utc) = DATE(r.ts_utc) AND r2.regime_label IS NOT NULL
                 GROUP BY r2.regime_label ORDER BY COUNT(*) DESC LIMIT 1) AS dominant_label
            FROM regime_snapshots r
            WHERE ts_utc >= datetime('now', '-{days} days')
              AND regime_score IS NOT NULL
            GROUP BY day
            ORDER BY day ASC
        """).fetchall()

        # Alert counts per day
        alerts_per_day = conn.execute(f"""
            SELECT DATE(ts_utc) AS day, COUNT(*) AS alerts
            FROM signals
            WHERE ts_utc >= datetime('now', '-{days} days')
              AND decision = 'ALERT'
            GROUP BY day
        """).fetchall()
        alert_map = {r["day"]: r["alerts"] for r in alerts_per_day}

        daily_rows = []
        for r in daily:
            d = dict(r)
            d["alerts"] = alert_map.get(d["day"], 0)
            daily_rows.append(d)

        # Momentum metrics: slope of regime_score over last 7 days
        last7 = conn.execute("""
            SELECT AVG(regime_score) AS avg_score, DATE(ts_utc) AS day
            FROM regime_snapshots
            WHERE ts_utc >= datetime('now', '-7 days')
              AND regime_score IS NOT NULL
            GROUP BY day
            ORDER BY day ASC
        """).fetchall()

        momentum = None
        if len(last7) >= 3:
            scores = [r["avg_score"] for r in last7]
            n = len(scores)
            # Simple linear regression slope
            x_mean = (n - 1) / 2
            y_mean = sum(scores) / n
            num = sum((i - x_mean) * (scores[i] - y_mean) for i in range(n))
            den = sum((i - x_mean) ** 2 for i in range(n))
            slope = num / den if den != 0 else 0
            momentum = {
                "slope_per_day": round(slope, 2),
                "direction": "rising" if slope > 0.5 else "falling" if slope < -0.5 else "flat",
                "current_avg": round(scores[-1], 1) if scores else None,
                "week_avg": round(y_mean, 1),
                "days": n,
            }

        return {
            "daily": daily_rows,
            "momentum": momentum,
        }
    finally:
        conn.close()


@app.get("/api/regime/momentum")
async def regime_momentum(_: str = Depends(get_current_user)):
    """
    Current momentum snapshot: regime score trend + signal velocity + breadth velocity.
    Used by the momentum panel on the regime heatmap page.
    """
    import sqlite3 as _sqlite3, os as _os
    _DB = _os.path.normpath(_os.path.join(_os.path.dirname(__file__), "..", "..", "data_storage", "engine.db"))
    conn = _sqlite3.connect(f"file:{_DB}?mode=ro", uri=True)
    conn.row_factory = _sqlite3.Row
    try:
        # Last 48h of regime snapshots (hourly-ish)
        regime_trend = conn.execute("""
            SELECT ts_utc, regime_score, regime_label, breadth_pct, sol_change_24h, volume_score, liquidity_score
            FROM regime_snapshots
            WHERE ts_utc >= datetime('now', '-48 hours')
              AND regime_score IS NOT NULL
            ORDER BY ts_utc ASC
        """).fetchall()

        # Signal velocity: alerts per 6h window for last 48h
        signal_velocity = conn.execute("""
            SELECT
                CAST(strftime('%s', ts_utc) / 21600 AS INTEGER) AS bucket,
                MIN(ts_utc) AS bucket_ts,
                COUNT(*) AS total,
                SUM(CASE WHEN decision = 'ALERT' THEN 1 ELSE 0 END) AS alerts,
                SUM(CASE WHEN decision = 'WATCHLIST' THEN 1 ELSE 0 END) AS watchlist,
                AVG(score_total) AS avg_score
            FROM signals
            WHERE ts_utc >= datetime('now', '-48 hours')
            GROUP BY bucket
            ORDER BY bucket ASC
        """).fetchall()

        return {
            "regime_trend": [dict(r) for r in regime_trend],
            "signal_velocity": [dict(r) for r in signal_velocity],
        }
    finally:
        conn.close()


@app.get("/api/brain/tuning-history")
async def brain_tuning_history(_: str = Depends(get_current_user)):
    """
    Return last 20 entries from the auto-tuner audit log.
    Written by auto_tune.py on every weekly run (applied, skipped, or insufficient data).
    """
    import json as _json
    path = os.path.normpath(_TUNING_LOG)
    if not os.path.isfile(path):
        return []
    try:
        entries = _json.loads(open(path).read())
        # Newest first, cap at 20
        return list(reversed(entries))[:20]
    except Exception as exc:
        log.warning("tuning_log.json read error: %s", exc)
        return []


# ---------------------------------------------------------------------------
# Signal → Trade map — links signals to trades by symbol + timestamp proximity
# ---------------------------------------------------------------------------

@app.get("/api/symbols/{symbol}/signal-trade-map")
async def symbol_signal_trade_map(
    symbol: str,
    limit: int = 100,
    _: str = Depends(get_current_user),
):
    """
    For each signal for a symbol, find the nearest trade opened within 30 minutes.
    Returns a list keyed by signal_id for the frontend to build a Map.
    """
    sym = symbol.upper().strip()
    conn = _ro_conn_brain()
    try:
        rows = conn.execute(
            """
            SELECT
                s.id                AS signal_id,
                s.ts_utc            AS signal_ts,
                s.price_usd         AS signal_price,
                t.id                AS trade_id,
                t.entry_price,
                t.exit_price,
                t.pnl_pct,
                t.status            AS trade_status,
                t.opened_ts_utc,
                CASE
                    WHEN s.price_usd IS NOT NULL AND s.price_usd > 0 AND t.entry_price IS NOT NULL
                    THEN ROUND(((t.entry_price - s.price_usd) / s.price_usd) * 100, 3)
                    ELSE NULL
                END                 AS slippage_pct
            FROM signals s
            LEFT JOIN trades t
                ON  t.symbol = s.symbol
                AND t.status IN ('OPEN', 'CLOSED')
                AND ABS(JULIANDAY(t.opened_ts_utc) - JULIANDAY(s.ts_utc)) < 0.021
            WHERE s.symbol = ?
            ORDER BY s.id DESC
            LIMIT ?
            """,
            (sym, max(1, int(limit))),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Score Threshold Simulator — replay outcomes at different thresholds
# ---------------------------------------------------------------------------

@app.get("/api/brain/threshold-sim")
async def brain_threshold_sim(
    threshold: int = 72,
    lookback_days: int = 30,
    horizon: int = 4,
    _: str = Depends(get_current_user),
):
    """
    Simulate win rate and alert volume at a given score threshold.
    Compares against current ALERT_THRESHOLD config value.
    """
    from config_editor import get_config
    ret_col = {1: "return_1h_pct", 4: "return_4h_pct", 24: "return_24h_pct"}.get(horizon, "return_4h_pct")
    cfg = get_config()
    current_threshold = int(cfg.get("ALERT_THRESHOLD", 72))
    threshold = max(50, min(99, int(threshold)))

    conn = _ro_conn_brain()
    try:
        cutoff = f"datetime('now', '-{int(lookback_days)} days')"

        def _query(t: int) -> dict:
            row = conn.execute(
                f"""
                SELECT
                    COUNT(*)                                                    AS n,
                    AVG({ret_col})                                              AS avg_ret,
                    SUM(CASE WHEN {ret_col} > 0 THEN 1 ELSE 0 END)             AS wins
                FROM alert_outcomes
                WHERE score >= ?
                  AND created_ts_utc >= {cutoff}
                  AND {ret_col} IS NOT NULL
                """,
                (t,),
            ).fetchone()
            n = int(row["n"] or 0)
            wins = int(row["wins"] or 0)
            return {
                "n": n,
                "win_rate": round(wins / n * 100, 1) if n else 0.0,
                "avg_ret": round(float(row["avg_ret"] or 0), 2),
            }

        sim = _query(threshold)
        cur = _query(current_threshold)

        return {
            "threshold": threshold,
            **{f"sim_{k}": v for k, v in sim.items()},
            "current_threshold": current_threshold,
            **{f"current_{k}": v for k, v in cur.items()},
            "lookback_days": lookback_days,
            "horizon": horizon,
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Sell Signal Quality — effectiveness of SELL_ALERT signals
# ---------------------------------------------------------------------------

@app.get("/api/brain/sell-signals")
async def brain_sell_signals(
    lookback_days: int = 30,
    limit: int = 50,
    _: str = Depends(get_current_user),
):
    """
    Returns recent SELL_ALERT signals joined to alert_outcomes for the same symbol
    (within ±24h) to show what price did after the sell signal fired.
    A 'correct' sell = price fell within 4h (return_4h_pct < 0).
    """
    conn = _ro_conn_brain()
    try:
        rows = conn.execute(
            f"""
            SELECT
                s.id, s.ts_utc, s.symbol, s.decision, s.price_usd,
                s.score_total, s.regime_label, s.notes,
                o.return_1h_pct  AS price_1h_after,
                o.return_4h_pct  AS price_4h_after,
                o.entry_price    AS buy_entry_price,
                o.created_ts_utc AS outcome_ts
            FROM signals s
            LEFT JOIN alert_outcomes o
                ON  o.symbol = s.symbol
                AND ABS(JULIANDAY(o.created_ts_utc) - JULIANDAY(s.ts_utc)) < 1.0
            WHERE s.decision LIKE 'SELL_ALERT_%'
              AND s.ts_utc >= datetime('now', '-{int(lookback_days)} days')
            ORDER BY s.id DESC
            LIMIT ?
            """,
            (max(1, int(limit)),),
        ).fetchall()
        return {
            "signals": [dict(r) for r in rows],
            "note": "correct_sell = price_4h_after < 0 (price fell after sell)",
        }
    finally:
        conn.close()


@app.get("/api/brain/sell-signal-stats")
async def brain_sell_signal_stats(
    lookback_days: int = 30,
    _: str = Depends(get_current_user),
):
    """
    Per-type sell signal quality stats. Groups SELL_ALERT decisions by type,
    computes correct rate (price fell 4h after), avg price move 1h/4h after.
    """
    conn = _ro_conn_brain()
    try:
        rows = conn.execute(
            f"""
            SELECT
                s.decision,
                COUNT(*)                                                     AS total,
                COUNT(o.return_4h_pct)                                       AS with_data,
                AVG(o.return_1h_pct)                                         AS avg_1h,
                AVG(o.return_4h_pct)                                         AS avg_4h,
                SUM(CASE WHEN o.return_4h_pct < 0 THEN 1 ELSE 0 END)        AS correct,
                SUM(CASE WHEN o.return_4h_pct IS NOT NULL THEN 1 ELSE 0 END) AS evaluated
            FROM signals s
            LEFT JOIN alert_outcomes o
                ON  o.symbol = s.symbol
                AND ABS(JULIANDAY(o.created_ts_utc) - JULIANDAY(s.ts_utc)) < 1.0
            WHERE s.decision LIKE 'SELL_ALERT_%'
              AND s.ts_utc >= datetime('now', '-{int(lookback_days)} days')
            GROUP BY s.decision
            ORDER BY total DESC
            """,
        ).fetchall()

        result = []
        for r in rows:
            n_eval = int(r["evaluated"] or 0)
            correct = int(r["correct"] or 0)
            type_label = str(r["decision"]).replace("SELL_ALERT_", "").replace("_", " ")
            result.append({
                "decision": r["decision"],
                "type": type_label,
                "total": int(r["total"]),
                "with_data": int(r["with_data"] or 0),
                "evaluated": n_eval,
                "correct": correct,
                "correct_rate": round(correct / n_eval * 100, 1) if n_eval else None,
                "avg_1h": round(float(r["avg_1h"] or 0), 2) if r["avg_1h"] is not None else None,
                "avg_4h": round(float(r["avg_4h"] or 0), 2) if r["avg_4h"] is not None else None,
            })

        # Overall stats
        overall_eval = sum(r["evaluated"] for r in result)
        overall_correct = sum(r["correct"] for r in result)
        overall_total = sum(r["total"] for r in result)

        return {
            "by_type": result,
            "overall": {
                "total_sell_signals": overall_total,
                "evaluated": overall_eval,
                "correct": overall_correct,
                "correct_rate": round(overall_correct / overall_eval * 100, 1) if overall_eval else None,
            },
            "lookback_days": lookback_days,
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Executor endpoints
# ---------------------------------------------------------------------------

def _engine_root():
    """Absolute path to the engine root (two levels above dashboard/backend)."""
    from pathlib import Path
    return str(Path(__file__).resolve().parents[2])


def _ensure_engine_path():
    import sys
    root = _engine_root()
    if root not in sys.path:
        sys.path.insert(0, root)


@app.get("/api/executor/status")
async def executor_status(_: str = Depends(get_current_user)):
    """Return executor state: enabled, open positions, win rate, PnL."""
    try:
        _ensure_engine_path()
        from utils.executor import get_executor_status  # type: ignore
        return get_executor_status()
    except Exception as exc:
        return JSONResponse(
            {"enabled": False, "error": str(exc), "open_positions": 0, "positions": [], "exit_summary": {}},
            status_code=200,
        )


@app.post("/api/executor/force-sell")
async def executor_force_sell(body: dict, _: str = Depends(get_current_user)):
    """Force-sell all tokens for a given symbol."""
    symbol = str(body.get("symbol", "")).strip().upper()
    if not symbol:
        return JSONResponse({"success": False, "message": "symbol required"}, status_code=400)
    try:
        _ensure_engine_path()
        from utils.executor import force_sell  # type: ignore
        result = await force_sell(symbol)
        return result
    except Exception as exc:
        return JSONResponse({"success": False, "message": str(exc)}, status_code=500)


@app.post("/api/executor/toggle")
async def executor_toggle(body: dict, _: str = Depends(get_current_user)):
    """Enable or disable the executor at runtime by writing to the env file.
    body: { enabled: bool }
    """
    import re
    enabled = bool(body.get("enabled", False))
    env_path = os.path.join(_engine_root(), ".env")
    try:
        if os.path.exists(env_path):
            text = open(env_path).read()
            text = re.sub(
                r"^EXECUTOR_ENABLED=.*$",
                f"EXECUTOR_ENABLED={'true' if enabled else 'false'}",
                text,
                flags=re.MULTILINE,
            )
            open(env_path, "w").write(text)
        # Also set in current process env so subsequent requests reflect it
        os.environ["EXECUTOR_ENABLED"] = "true" if enabled else "false"
        return {"success": True, "enabled": enabled}
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)


@app.post("/api/executor/set-dry-run")
async def executor_set_dry_run(body: dict, _: str = Depends(get_current_user)):
    """Enable or disable dry-run mode. body: { dry_run: bool }"""
    import re
    dry_run = bool(body.get("dry_run", True))
    env_path = os.path.join(_engine_root(), ".env")
    try:
        if os.path.exists(env_path):
            text = open(env_path).read()
            text = re.sub(
                r"^EXECUTOR_DRY_RUN=.*$",
                f"EXECUTOR_DRY_RUN={'true' if dry_run else 'false'}",
                text,
                flags=re.MULTILINE,
            )
            open(env_path, "w").write(text)
        os.environ["EXECUTOR_DRY_RUN"] = "true" if dry_run else "false"
        return {"success": True, "dry_run": dry_run}
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)


@app.post("/api/executor/set-portfolio")
async def executor_set_portfolio(body: dict, _: str = Depends(get_current_user)):
    """Update PORTFOLIO_USD. body: { portfolio_usd: number }"""
    import re
    try:
        val = float(body.get("portfolio_usd", 0))
        if val <= 0:
            return JSONResponse({"success": False, "error": "Must be > 0"}, status_code=400)
        env_path = os.path.join(_engine_root(), ".env")
        if os.path.exists(env_path):
            text = open(env_path).read()
            text = re.sub(
                r"^PORTFOLIO_USD=.*$",
                f"PORTFOLIO_USD={int(val)}",
                text,
                flags=re.MULTILINE,
            )
            open(env_path, "w").write(text)
        os.environ["PORTFOLIO_USD"] = str(int(val))
        return {"success": True, "portfolio_usd": int(val)}
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)


class ManualBuyRequest(BaseModel):
    symbol: str
    mint: str
    position_usd: float | None = None   # None = auto-size via Kelly


@app.post("/api/executor/manual-buy")
async def executor_manual_buy(body: ManualBuyRequest, _: str = Depends(get_current_user)):
    """
    Trigger a buy directly from the dashboard (paper or live depending on DRY_RUN).
    The executor handles price fetching, position sizing, DB recording, and monitoring.
    """
    try:
        _ensure_engine_path()
        from utils.executor import execute_signal  # type: ignore

        symbol = body.symbol.strip().upper()
        mint   = body.mint.strip()
        if not symbol or not mint:
            return JSONResponse({"success": False, "error": "symbol and mint required"}, status_code=400)

        portfolio_usd = float(os.getenv("PORTFOLIO_USD", "1000"))
        pos_usd = body.position_usd
        if not pos_usd or pos_usd <= 0:
            try:
                from utils.position_sizing import calculate_position_size  # type: ignore
                pos = calculate_position_size({"symbol": symbol}, portfolio_usd)
                pos_usd = pos.get("position_usd", portfolio_usd * 0.03)
            except Exception:
                pos_usd = portfolio_usd * 0.03   # fallback: 3% of portfolio

        signal = {
            "symbol":       symbol,
            "mint":         mint,
            "entry_price":  0.0,        # executor fetches live price from Jupiter
            "score":        75,
            "confidence":   "B",
            "regime_label": "MANUAL",
            "position_usd": float(pos_usd),
            "lane":         "manual_dashboard",
            "source":       "dashboard",
            "cycle_phase":  "TRANSITION",
        }
        asyncio.create_task(execute_signal(signal))
        return {"success": True, "symbol": symbol, "position_usd": round(float(pos_usd), 2)}
    except Exception as exc:
        log.warning("manual_buy error: %s", exc)
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)


@app.get("/api/executor/exit-learnings")
async def executor_exit_learnings(_: str = Depends(get_current_user)):
    """Return exit strategy learnings summary."""
    try:
        _ensure_engine_path()
        from utils.exit_strategy import get_exit_summary, load_exit_learnings  # type: ignore
        summary = get_exit_summary()
        records = load_exit_learnings()
        # Return last 30 records for the table
        recent = sorted(records, key=lambda r: r.get("ts", ""), reverse=True)[:30]
        return {"summary": summary, "recent": recent}
    except Exception as exc:
        return JSONResponse({"summary": {}, "recent": [], "error": str(exc)}, status_code=200)


# ---------------------------------------------------------------------------
# Launch Feed — real-time new token detections from launch_listener.py
# ---------------------------------------------------------------------------

@app.get("/api/launches/recent")
async def launches_recent(
    limit: int = 50,
    _: str = Depends(get_current_user),
):
    """Return recent token launch detections from the launch listener."""
    try:
        _ensure_engine_path()
        from utils.launch_listener import get_recent_launches  # type: ignore
        return {"launches": get_recent_launches(limit=min(limit, 200))}
    except Exception as exc:
        return JSONResponse({"launches": [], "error": str(exc)}, status_code=200)


@app.get("/api/arb/opportunities")
async def arb_opportunities(
    limit: int = 100,
    min_spread: float = 0.0,
    _: str = Depends(get_current_user),
):
    """Return recent cross-DEX arb opportunities from arb_feed.jsonl."""
    try:
        _ensure_engine_path()
        from utils.dex_price_monitor import get_recent_arb_opportunities  # type: ignore
        opps = get_recent_arb_opportunities(limit=min(limit, 500))
        if min_spread > 0:
            opps = [o for o in opps if (o.get("spread_pct") or 0) >= min_spread]
        arb_enabled = os.environ.get("ARB_ENABLED", "false").lower() == "true"
        return {
            "opportunities": opps,
            "total": len(opps),
            "arb_enabled": arb_enabled,
            "min_spread_pct": float(os.environ.get("ARB_MIN_SPREAD_PCT", "4.0")),
        }
    except Exception as exc:
        return JSONResponse({"opportunities": [], "error": str(exc)}, status_code=200)


@app.get("/api/brain/lane-win-rates")
async def brain_lane_win_rates(
    lookback_days: int = 30,
    min_n: int = 5,
    _: str = Depends(get_current_user),
):
    """Return per-lane and per-source win rates for the Brain dashboard."""
    try:
        _ensure_engine_path()
        from utils.db import get_lane_win_rates, init_db  # type: ignore
        init_db()
        data = get_lane_win_rates(lookback_days=lookback_days, min_n=min_n)
        return data
    except Exception as exc:
        return JSONResponse({"error": str(exc), "lanes": [], "by_source": []}, status_code=200)


@app.get("/api/brain/score-analysis")
async def brain_score_analysis(_: str = Depends(get_current_user)):
    """Return latest score component analysis from score_analysis.json."""
    try:
        _ensure_engine_path()
        from utils.score_analyzer import get_analysis_summary  # type: ignore
        summary = get_analysis_summary()
        return summary
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=200)


# ---------------------------------------------------------------------------
# Patch 166 — Memecoin paper-to-live promotion readiness
# ---------------------------------------------------------------------------

@app.get("/api/brain/memecoin-readiness")
async def brain_memecoin_readiness(_: str = Depends(get_current_user)):
    """
    Patch 170 — 24h-aware promotion readiness for the memecoin scanner arm.

    The strategy is classified by its data-supported evaluation horizon (4h vs 24h).
    Blockers are separated into three categories:
      market — external conditions (F&G, risk mode) — wait for market
      data   — sample quality issues — wait for data to mature
      edge   — strategy underperforming at the recommended horizon — fix the scanner

    The primary edge gate tracks the recommended horizon, not blindly 4h.
    Verdict (READY / WATCH / NOT_READY) remains conservative — market gates are hard.

    Key fields:
      strategy_horizon    — "4h" | "24h" | "unclear"
      promotion_category  — "MARKET_GATED" | "EDGE_GATED" | "DATA_GATED" | "MULTI_GATED" | "CLEAR"
      blocker_categories  — {market: [...], data: [...], edge: [...]}
      promotion_scenario  — if market gate cleared today, would 24h metrics justify promotion?
    """
    _ensure_engine_path()

    # ── 1. F&G (market gate) ─────────────────────────────────────────────────
    fg_value: int | None = None
    fg_label  = "UNKNOWN"
    fg_open   = True   # fail open — never block on API failure
    try:
        from utils.agent_coordinator import get_fear_greed as _gfg  # type: ignore
        fg = _gfg(cache_ttl_min=5)
        fg_value = fg.get("value")
        fg_label = fg.get("label", "UNKNOWN")
        fg_open  = bool(fg.get("favorable", True))
    except Exception:
        pass

    # ── 2. Risk mode (market gate) ────────────────────────────────────────────
    risk: dict = {"mode": "NORMAL", "streak": 0}
    try:
        from utils.db import get_risk_mode as _grm  # type: ignore
        risk = _grm()
    except Exception:
        pass

    # ── 3. Edge metrics — last 20 completed trades at 4h and 24h ─────────────
    # _exp_stats is defined below (Patch 167 helper). Safe to call at runtime.
    stats_4h  = {"n": 0, "win_rate_pct": None, "expectancy_pct": None,
                 "avg_return_pct": None, "payoff_ratio": None,
                 "avg_win_pct": None, "avg_loss_pct": None}
    stats_24h = dict(stats_4h)
    try:
        from utils.db import get_conn as _gc  # type: ignore
        with _gc() as _conn:
            _r4 = _conn.execute(
                "SELECT return_4h_pct FROM alert_outcomes "
                "WHERE return_4h_pct IS NOT NULL "
                "ORDER BY evaluated_4h_ts_utc DESC LIMIT 20"
            ).fetchall()
        stats_4h = _exp_stats([float(r[0]) for r in _r4])
    except Exception:
        pass
    try:
        from utils.db import get_conn as _gc  # type: ignore
        with _gc() as _conn24:
            _r24 = _conn24.execute(
                "SELECT return_24h_pct FROM alert_outcomes "
                "WHERE return_24h_pct IS NOT NULL "
                "ORDER BY evaluated_24h_ts_utc DESC LIMIT 20"
            ).fetchall()
        stats_24h = _exp_stats([float(r[0]) for r in _r24])
    except Exception:
        pass

    sample_n_4h  = stats_4h["n"]
    sample_n_24h = stats_24h["n"]
    exp_4h  = stats_4h["expectancy_pct"]  if stats_4h["expectancy_pct"]  is not None else 0.0
    exp_24h = stats_24h["expectancy_pct"] if stats_24h["expectancy_pct"] is not None else 0.0
    wr_4h   = stats_4h["win_rate_pct"]    if stats_4h["win_rate_pct"]    is not None else 0.0
    wr_24h  = stats_24h["win_rate_pct"]   if stats_24h["win_rate_pct"]   is not None else 0.0

    # ── 4. Data quality — sample concentration ────────────────────────────────
    top_symbol       = None
    top_symbol_pct   = 0.0
    distinct_symbols = 0
    total_sample_n   = 0
    try:
        from utils.db import get_conn as _gc  # type: ignore
        with _gc() as _cq:
            _sym_rows = _cq.execute(
                "SELECT symbol FROM alert_outcomes "
                "WHERE return_4h_pct IS NOT NULL "
                "ORDER BY evaluated_4h_ts_utc DESC LIMIT 60"
            ).fetchall()
        if _sym_rows:
            from collections import Counter as _Counter
            _sym_counts  = _Counter(r[0] for r in _sym_rows if r[0])
            total_sample_n   = len(_sym_rows)
            distinct_symbols = len(_sym_counts)
            _top = _sym_counts.most_common(1)
            if _top:
                top_symbol     = _top[0][0]
                top_symbol_pct = round(_top[0][1] / total_sample_n * 100, 1)
    except Exception:
        pass

    # ── 5. Horizon classification ─────────────────────────────────────────────
    # Classify by the performance gap between 4h and 24h over the last 20 trades.
    # A gap >5pp with positive 24h expectancy means alpha materialises after 4h.
    _horizon_gap: float | None = (
        round(exp_24h - exp_4h, 2)
        if sample_n_4h >= 20 and sample_n_24h >= 20
        else None
    )
    if _horizon_gap is not None and _horizon_gap > 5.0 and exp_24h > 0:
        strategy_horizon  = "24h"
        horizon_rationale = (
            f"24h expectancy ({exp_24h:+.2f}%) exceeds 4h ({exp_4h:+.2f}%) by "
            f"{_horizon_gap:+.2f}pp and is positive. Signal alpha materialises after "
            "the 4h window — strategy classified as 24h-evaluated."
        )
    elif _horizon_gap is not None and abs(_horizon_gap) < 2.0:
        strategy_horizon  = "4h"
        horizon_rationale = (
            f"4h and 24h expectancy differ by only {abs(_horizon_gap):.2f}pp. "
            "No meaningful horizon advantage — defaulting to 4h (faster feedback)."
        )
    elif sample_n_4h >= 20 and exp_4h >= 0:
        strategy_horizon  = "4h"
        horizon_rationale = "4h expectancy is non-negative. 4h horizon is sufficient."
    else:
        strategy_horizon  = "unclear"
        horizon_rationale = (
            "Insufficient data or ambiguous signals to determine the correct horizon. "
            "Defaulting to conservative 4h edge gate until a clearer pattern emerges."
        )

    # Edge gate uses the recommended horizon. "unclear" defaults to 4h (conservative).
    if strategy_horizon == "24h":
        edge_exp   = exp_24h
        edge_n     = sample_n_24h
        edge_label = "expectancy_24h"
    else:
        edge_exp   = exp_4h
        edge_n     = sample_n_4h
        edge_label = "expectancy_4h"

    # ── 6. Gate evaluation — market, data, edge ──────────────────────────────
    mkt_blockers:  list[str] = []
    edge_blockers: list[str] = []
    data_warnings: list[str] = []
    mkt_warnings:  list[str] = []

    # Market gates (hard — external, cannot be influenced by scanner changes)
    if not fg_open:
        mkt_blockers.append(
            f"F&G {fg_value} ({fg_label}) — market must recover above 25 before any promotion"
        )
    if risk["mode"] == "DEFENSIVE":
        mkt_blockers.append(
            f"Risk mode DEFENSIVE ({risk['streak']} consecutive 4h losses) — "
            "resets when next 4h trade wins"
        )

    # Edge gate at recommended horizon (hard — strategy must demonstrate edge here)
    if edge_n >= 20 and edge_exp < -2.0:
        edge_blockers.append(
            f"Expectancy at {strategy_horizon} horizon ({edge_exp:+.2f}%) "
            f"below -2% gate over last {edge_n} trades"
        )

    # Data warnings (non-blocking — flag quality issues in the evaluation sample)
    if top_symbol_pct >= 30.0 and top_symbol:
        data_warnings.append(
            f"{top_symbol} is {top_symbol_pct}% of the last-{total_sample_n} evaluation sample "
            "— results may be skewed; cooldown guard (Patch 168) will diversify this over time"
        )
    if sample_n_4h < 20:
        data_warnings.append(
            f"4h sample only {sample_n_4h}/20 trades — edge gate inactive until 20 complete"
        )
    if sample_n_24h < 20:
        data_warnings.append(
            f"24h sample only {sample_n_24h}/20 trades — horizon classification may be premature"
        )

    # Soft market warnings
    if risk["mode"] == "CAUTIOUS":
        mkt_warnings.append(
            f"Risk mode CAUTIOUS ({risk['streak']} consecutive 4h losses) — cautious sizing advised"
        )
    if fg_value is not None and fg_open and fg_value <= 35:
        mkt_warnings.append(
            f"F&G recovering ({fg_value}) — sentiment is tentative; target >35 for confident deploy"
        )

    # ── 7. Verdict ────────────────────────────────────────────────────────────
    hard_blockers = mkt_blockers + edge_blockers
    all_warnings  = data_warnings + mkt_warnings

    if hard_blockers:
        verdict = "NOT_READY"
    elif all_warnings:
        verdict = "WATCH"
    else:
        verdict = "READY"

    # ── 8. Promotion category ─────────────────────────────────────────────────
    if mkt_blockers and edge_blockers:
        promotion_category = "MULTI_GATED"
    elif mkt_blockers:
        promotion_category = "MARKET_GATED"
    elif edge_blockers:
        promotion_category = "EDGE_GATED"
    elif data_warnings:
        promotion_category = "DATA_GATED"
    else:
        promotion_category = "CLEAR"

    # ── 9. Promotion scenario ─────────────────────────────────────────────────
    # Hypothetical: if market gates cleared today, would the arm be promotable?
    _would_promote_at_24h = sample_n_24h >= 20 and exp_24h >= -2.0
    _would_promote_at_4h  = sample_n_4h  >= 20 and exp_4h  >= -2.0
    _would_promote_recommended = (
        _would_promote_at_24h if strategy_horizon == "24h" else _would_promote_at_4h
    )

    missing_confirmation: list[str] = []
    if not fg_open:
        missing_confirmation.append(
            f"F&G must recover above 25 (currently {fg_value}). "
            "This is the primary hard gate — no other action matters until this clears."
        )
    if risk["mode"] == "DEFENSIVE":
        missing_confirmation.append(
            "Risk mode must exit DEFENSIVE (next 4h winning trade resets the streak)"
        )
    if not _would_promote_recommended and edge_n >= 20:
        missing_confirmation.append(
            f"{strategy_horizon} expectancy must reach ≥-2% (currently {edge_exp:+.2f}%). "
            "Run /api/brain/policy-comparison to understand the horizon breakdown."
        )
    missing_confirmation.append(
        "Post-cooldown sample: dedup guard activated Mar 7 2026 (Patch 168). "
        "Need ≥20 completed 24h trades after that date to confirm that removing "
        "duplicate entries preserves the observed 24h edge."
    )

    if _would_promote_recommended:
        _scenario_summary = (
            f"If the market gate cleared today (F&G >25), the arm WOULD be promotable "
            f"under {strategy_horizon} evaluation (edge gate: {edge_exp:+.2f}% ≥ -2%). "
            f"Only the market gate is blocking."
        )
    else:
        _scenario_summary = (
            f"Even if market gate cleared, the {strategy_horizon} edge gate would still block "
            f"(expectancy {edge_exp:+.2f}%, needs ≥-2%)."
        )

    # ── 10. Gate detail ───────────────────────────────────────────────────────
    gate_detail = [
        {
            "gate":        "fg_value",
            "category":    "market",
            "description": "Fear & Greed must be >25 (not extreme fear)",
            "value":       fg_value,
            "threshold":   25,
            "passing":     fg_open,
            "delta":       round(fg_value - 25, 1) if fg_value is not None else None,
            "notes":       f"Currently {fg_value} ({fg_label}). Gate reopens when value >25.",
        },
        {
            "gate":        "risk_mode",
            "category":    "market",
            "description": "Risk mode must not be DEFENSIVE",
            "value":       risk["mode"],
            "threshold":   "not DEFENSIVE",
            "passing":     risk["mode"] != "DEFENSIVE",
            "delta":       None,
            "notes":       (
                f"Streak: {risk['streak']} consecutive losses. "
                "Resets on next winning 4h trade."
            ),
        },
        {
            "gate":        edge_label,
            "category":    "edge",
            "horizon":     strategy_horizon if strategy_horizon in ("4h", "24h") else "4h",
            "description": (
                f"Expectancy at {strategy_horizon} horizon ≥-2% over last 20 trades "
                f"(primary edge gate — strategy classified as {strategy_horizon})"
            ),
            "value":       edge_exp if edge_n >= 20 else None,
            "threshold":   -2.0,
            "passing":     edge_n < 20 or edge_exp >= -2.0,
            "delta":       round(edge_exp - (-2.0), 2) if edge_n >= 20 else None,
            "notes":       (
                f"Currently {edge_exp:+.2f}% over {edge_n} trades. "
                f"This is the primary edge gate — horizon is {strategy_horizon}."
            ),
        },
        {
            "gate":        "expectancy_4h_reference",
            "category":    "informational" if strategy_horizon == "24h" else "edge",
            "horizon":     "4h",
            "description": (
                "4h expectancy — shown for reference; not the primary gate at 24h horizon"
                if strategy_horizon == "24h"
                else "4h expectancy ≥-2% over last 20 completed trades"
            ),
            "value":       exp_4h if sample_n_4h >= 20 else None,
            "threshold":   -2.0,
            "passing":     (
                True  # informational — never hard-blocks when primary gate is 24h
                if strategy_horizon == "24h"
                else (sample_n_4h < 20 or exp_4h >= -2.0)
            ),
            "delta":       round(exp_4h - (-2.0), 2) if sample_n_4h >= 20 else None,
            "notes":       (
                f"Currently {exp_4h:+.2f}% over {sample_n_4h} trades. "
                f"Strategy is {strategy_horizon}-classified so this is informational only."
                if strategy_horizon == "24h"
                else f"Currently {exp_4h:+.2f}% over {sample_n_4h} trades."
            ),
        },
        {
            "gate":        "sample_concentration",
            "category":    "data",
            "description": "No single symbol should exceed 30% of the evaluation sample",
            "value":       f"{top_symbol} {top_symbol_pct}%" if top_symbol else None,
            "threshold":   "≤30%",
            "passing":     top_symbol_pct < 30.0,
            "delta":       None,
            "notes":       (
                f"{top_symbol} is {top_symbol_pct}% of last {total_sample_n} trades. "
                f"{distinct_symbols} distinct symbols in sample."
                if top_symbol else "No concentration data."
            ),
        },
    ]

    # ── 11. Path guidance ─────────────────────────────────────────────────────
    path_to_watch: list[str] = []
    path_to_ready: list[str] = []

    if not fg_open:
        path_to_watch.append(f"F&G must recover above 25 (currently {fg_value})")
        path_to_ready.append(
            f"F&G should reach >35 before deploying capital (currently {fg_value})"
        )
    elif fg_value is not None and fg_value <= 35:
        path_to_ready.append(
            f"F&G should ideally be >35 for confident deployment (currently {fg_value})"
        )

    if risk["mode"] == "DEFENSIVE":
        path_to_watch.append("Risk mode must exit DEFENSIVE (next 4h win breaks streak)")
        path_to_ready.append("Risk mode should return to NORMAL (2+ consecutive 4h wins)")
    elif risk["mode"] == "CAUTIOUS":
        path_to_ready.append(
            "Risk mode should return to NORMAL (1 more 4h win to clear streak)"
        )

    if edge_blockers:
        path_to_watch.append(
            f"{strategy_horizon} expectancy must reach ≥-2% (currently {edge_exp:+.2f}%). "
            "Run /api/brain/policy-comparison for breakdown."
        )
        path_to_ready.append(
            f"{strategy_horizon} expectancy must turn positive (currently {edge_exp:+.2f}%). "
            "Run /api/brain/loss-clustering to identify damage source."
        )

    if top_symbol_pct >= 30.0 and top_symbol:
        path_to_ready.append(
            f"Reduce {top_symbol} concentration ({top_symbol_pct}% of sample). "
            "Cooldown guard (Patch 168) will gradually diversify the evaluation sample."
        )

    # ── 12. Return ────────────────────────────────────────────────────────────
    return {
        "verdict":             verdict,
        "strategy_horizon":    strategy_horizon,
        "horizon_rationale":   horizon_rationale,
        "promotion_category":  promotion_category,
        "blocker_categories": {
            "market": mkt_blockers,
            "data":   data_warnings,
            "edge":   edge_blockers,
        },
        "active_blockers": hard_blockers,
        "warnings":        all_warnings,
        "gate_detail":     gate_detail,
        "path_to_watch":   path_to_watch,
        "path_to_ready":   path_to_ready,
        "promotion_scenario": {
            "would_promote_at_recommended_horizon": _would_promote_recommended,
            "would_promote_at_24h": _would_promote_at_24h,
            "would_promote_at_4h":  _would_promote_at_4h,
            "recommended_horizon":  strategy_horizon,
            "missing_confirmation": missing_confirmation,
            "summary": _scenario_summary,
        },
        "horizon_context": {
            "4h": {
                "trades_sampled": sample_n_4h,
                "win_rate_pct":   wr_4h,
                "expectancy_pct": exp_4h,
                "avg_return_pct": stats_4h.get("avg_return_pct"),
                "payoff_ratio":   stats_4h.get("payoff_ratio"),
                "is_primary_gate": strategy_horizon == "4h",
            },
            "24h": {
                "trades_sampled": sample_n_24h,
                "win_rate_pct":   wr_24h,
                "expectancy_pct": exp_24h,
                "avg_return_pct": stats_24h.get("avg_return_pct"),
                "payoff_ratio":   stats_24h.get("payoff_ratio"),
                "is_primary_gate": strategy_horizon == "24h",
                "note": (
                    "This is the primary edge gate horizon."
                    if strategy_horizon == "24h"
                    else "Informational — not the primary edge gate."
                ),
            },
            "horizon_gap_pct": _horizon_gap,
            "recommended":     strategy_horizon,
        },
        "metrics": {
            "fg_value":         fg_value,
            "fg_label":         fg_label,
            "fg_gate_open":     fg_open,
            "risk_mode":        risk["mode"],
            "risk_streak":      risk["streak"],
            "sample_n_4h":      sample_n_4h,
            "sample_n_24h":     sample_n_24h,
            "expectancy_4h":    exp_4h,
            "expectancy_24h":   exp_24h,
            "win_rate_4h":      wr_4h,
            "win_rate_24h":     wr_24h,
            "top_symbol":       top_symbol,
            "top_symbol_pct":   top_symbol_pct,
            "distinct_symbols": distinct_symbols,
        },
    }


# ---------------------------------------------------------------------------
# Patch 167 — Memecoin expectancy diagnostics (A, B, C)
# ---------------------------------------------------------------------------

def _exp_stats(rets: list) -> dict:
    """
    Compute win-rate, avg-win, avg-loss, payoff-ratio, and expectancy
    from a list of return floats. Pure helper — no DB access.
    """
    if not rets:
        return {
            "n": 0, "win_rate_pct": None, "avg_win_pct": None,
            "avg_loss_pct": None, "payoff_ratio": None, "expectancy_pct": None,
            "avg_return_pct": None,
        }
    wins   = [r for r in rets if r > 0]
    losses = [r for r in rets if r < 0]
    wr     = len(wins) / len(rets)
    avg_w  = sum(wins)   / len(wins)   if wins   else 0.0
    avg_l  = sum(losses) / len(losses) if losses else 0.0
    pr     = (avg_w / abs(avg_l)) if avg_l < 0 else None
    exp    = wr * avg_w + (1 - wr) * avg_l
    return {
        "n":             len(rets),
        "win_rate_pct":  round(wr * 100, 1),
        "avg_win_pct":   round(avg_w, 2),
        "avg_loss_pct":  round(avg_l, 2),
        "payoff_ratio":  round(pr, 2) if pr is not None else None,
        "expectancy_pct": round(exp, 2),
        "avg_return_pct": round(sum(rets) / len(rets), 2),
    }


@app.get("/api/brain/expectancy-decomposition")
async def brain_expectancy_decomposition(
    lookback: int = 20,
    _: str = Depends(get_current_user),
):
    """
    Patch 167 (A) — Full expectancy decomposition for the memecoin paper strategy.

    Breaks down 4h expectancy into:
      - overall: win_rate, avg_win, avg_loss, payoff_ratio, expectancy
      - by_confidence: per confidence label
      - by_horizon: 1h / 4h / 24h comparison
      - rolling: rolling expectancy over last N trades (shows trend direction)

    lookback controls how many completed trades to include (default 20, max 200).
    """
    _ensure_engine_path()
    lookback = max(5, min(200, lookback))

    try:
        from utils.db import get_conn as _gc  # type: ignore

        with _gc() as _conn:
            # Fetch last N completed trades (all three horizons)
            rows = [
                dict(r) for r in _conn.execute(
                    """
                    SELECT return_1h_pct, return_4h_pct, return_24h_pct,
                           confidence, evaluated_4h_ts_utc, symbol, score
                    FROM   alert_outcomes
                    WHERE  return_4h_pct IS NOT NULL
                    ORDER  BY evaluated_4h_ts_utc DESC
                    LIMIT  ?
                    """,
                    (lookback,),
                ).fetchall()
            ]

        if not rows:
            return {"error": "No completed trades found", "n": 0}

        rows_asc = list(reversed(rows))  # chronological for rolling calc

        # A. Overall (4h)
        rets_4h  = [r["return_4h_pct"]  for r in rows]
        rets_1h  = [r["return_1h_pct"]  for r in rows if r["return_1h_pct"]  is not None]
        rets_24h = [r["return_24h_pct"] for r in rows if r["return_24h_pct"] is not None]

        overall = _exp_stats(rets_4h)

        # B. By confidence
        from collections import defaultdict
        by_conf: dict = defaultdict(list)
        for r in rows:
            by_conf[r["confidence"] or "(none)"].append(r["return_4h_pct"])
        by_confidence = {
            conf: _exp_stats(vals)
            for conf, vals in sorted(by_conf.items())
        }

        # C. Horizon comparison
        by_horizon = {
            "1h":  _exp_stats(rets_1h),
            "4h":  _exp_stats(rets_4h),
            "24h": _exp_stats(rets_24h),
        }

        # D. Rolling expectancy (window=10)
        WINDOW = 10
        rolling = []
        for i, r in enumerate(rows_asc):
            window_rets = [rr["return_4h_pct"] for rr in rows_asc[max(0, i - WINDOW + 1): i + 1]]
            s = _exp_stats(window_rets)
            rolling.append({
                "trade_n":        i + 1,
                "ts":             r["evaluated_4h_ts_utc"],
                "symbol":         r["symbol"],
                "return_4h_pct":  round(r["return_4h_pct"], 2),
                "rolling_exp_pct": s["expectancy_pct"],
                "rolling_wr_pct":  s["win_rate_pct"],
            })

        return {
            "lookback":       lookback,
            "overall":        overall,
            "by_confidence":  by_confidence,
            "by_horizon":     by_horizon,
            "rolling":        rolling,
        }
    except Exception as exc:
        log.warning("expectancy-decomposition error: %s", exc)
        return JSONResponse({"error": str(exc)}, status_code=200)


@app.get("/api/brain/loss-clustering")
async def brain_loss_clustering(
    lookback: int = 60,
    _: str = Depends(get_current_user),
):
    """
    Patch 167 (B) — Cluster completed trades to find where losses concentrate.

    Breaks last N completed 4h trades by:
      - score_band (0/10/20/…/90 → each covers a 10-pt range)
      - regime_label
      - lane
      - cycle_phase
      - worst_20: individual worst single-trade losses

    Helps identify whether negative expectancy is driven by a specific signal
    type, market condition, or execution lane.

    lookback: number of completed trades to include (default 60, max 500).
    """
    _ensure_engine_path()
    lookback = max(5, min(500, lookback))

    try:
        from utils.db import get_conn as _gc  # type: ignore
        from collections import defaultdict

        with _gc() as _conn:
            rows = [
                dict(r) for r in _conn.execute(
                    """
                    SELECT return_4h_pct, score, regime_label, lane,
                           cycle_phase, confidence, symbol, created_ts_utc,
                           evaluated_4h_ts_utc
                    FROM   alert_outcomes
                    WHERE  return_4h_pct IS NOT NULL
                    ORDER  BY evaluated_4h_ts_utc DESC
                    LIMIT  ?
                    """,
                    (lookback,),
                ).fetchall()
            ]

        if not rows:
            return {"error": "No completed trades found", "n": 0}

        def _cluster(key_fn) -> list:
            groups: dict = defaultdict(list)
            for r in rows:
                groups[key_fn(r)].append(r["return_4h_pct"])
            out = []
            for k, vals in sorted(groups.items(), key=lambda x: _exp_stats(x[1]).get("expectancy_pct") or 0):
                s = _exp_stats(vals)
                s["group"] = k
                out.append(s)
            return out

        # Score bands (10-pt)
        def _score_band(r) -> str:
            sc = r.get("score")
            if sc is None:
                return "no_score"
            b = int(float(sc) // 10) * 10
            return f"{b}-{b+9}"

        by_score_band  = _cluster(_score_band)
        by_regime      = _cluster(lambda r: r.get("regime_label") or "(none)")
        by_lane        = _cluster(lambda r: r.get("lane")         or "(none)")
        by_cycle_phase = _cluster(lambda r: r.get("cycle_phase")  or "(none)")

        # Worst 20 individual losses
        worst_20 = sorted(
            [
                {
                    "symbol":          r["symbol"],
                    "return_4h_pct":   round(r["return_4h_pct"], 2),
                    "score":           r.get("score"),
                    "regime_label":    r.get("regime_label"),
                    "lane":            r.get("lane"),
                    "cycle_phase":     r.get("cycle_phase"),
                    "created_ts_utc":  r["created_ts_utc"],
                }
                for r in rows
                if r["return_4h_pct"] < 0
            ],
            key=lambda x: x["return_4h_pct"],
        )[:20]

        return {
            "lookback":       lookback,
            "total_trades":   len(rows),
            "by_score_band":  by_score_band,
            "by_regime":      by_regime,
            "by_lane":        by_lane,
            "by_cycle_phase": by_cycle_phase,
            "worst_20":       worst_20,
        }
    except Exception as exc:
        log.warning("loss-clustering error: %s", exc)
        return JSONResponse({"error": str(exc)}, status_code=200)


@app.get("/api/brain/regime-diagnosis")
async def brain_regime_diagnosis(
    lookback: int = 60,
    _: str = Depends(get_current_user),
):
    """
    Patch 167 (C) — Regime-aware diagnosis of memecoin scanner performance.

    Surfaces how performance differs across:
      - regime_label (Momentum / Breakdown / WATCHLIST / Reclaim / UNKNOWN)
      - cycle_phase (BULL / BEAR / TRANSITION)
      - weekly trend (last 8 weeks — shows whether performance is improving or degrading)

    Also produces a concise diagnosis_notes list highlighting the most
    important findings (e.g. which regime is toxic, which is the edge).
    """
    _ensure_engine_path()
    lookback = max(5, min(500, lookback))

    try:
        from utils.db import get_conn as _gc  # type: ignore
        from collections import defaultdict

        with _gc() as _conn:
            rows = [
                dict(r) for r in _conn.execute(
                    """
                    SELECT return_4h_pct, regime_label, cycle_phase,
                           confidence, lane, score, symbol,
                           created_ts_utc, evaluated_4h_ts_utc
                    FROM   alert_outcomes
                    WHERE  return_4h_pct IS NOT NULL
                    ORDER  BY evaluated_4h_ts_utc ASC
                    LIMIT  ?
                    """,
                    (lookback,),
                ).fetchall()
            ]

        if not rows:
            return {"error": "No completed trades found", "n": 0}

        def _breakdown(key_fn) -> list:
            groups: dict = defaultdict(list)
            for r in rows:
                groups[key_fn(r)].append(r["return_4h_pct"])
            out = []
            for k, vals in sorted(groups.items(), key=lambda x: -(x[1] and _exp_stats(x[1])["expectancy_pct"] or 0)):
                s = _exp_stats(vals)
                s["group"] = k
                out.append(s)
            return out

        by_regime      = _breakdown(lambda r: r.get("regime_label") or "(none)")
        by_cycle_phase = _breakdown(lambda r: r.get("cycle_phase")  or "(none)")

        # Weekly trend — ISO week bucket
        from datetime import datetime, timezone as _tz
        week_groups: dict = defaultdict(list)
        for r in rows:
            try:
                dt  = datetime.fromisoformat(r["evaluated_4h_ts_utc"].replace("Z", "+00:00"))
                key = dt.strftime("%Y-W%V")
            except Exception:
                key = "unknown"
            week_groups[key].append(r["return_4h_pct"])

        weekly_trend = []
        for week in sorted(week_groups)[-8:]:
            s = _exp_stats(week_groups[week])
            s["week"] = week
            weekly_trend.append(s)

        # Diagnosis notes — automatic narrative from the data
        notes: list[str] = []

        # Best and worst regimes
        sorted_regimes = sorted(
            by_regime,
            key=lambda x: x.get("expectancy_pct") or -999,
            reverse=True,
        )
        if sorted_regimes:
            best = sorted_regimes[0]
            worst = sorted_regimes[-1]
            if best.get("n", 0) >= 5 and (best.get("expectancy_pct") or 0) > 0:
                notes.append(
                    f"✅ Best regime: {best['group']} — "
                    f"{best['win_rate_pct']}% WR, expectancy {best['expectancy_pct']:+.2f}% "
                    f"({best['n']} trades)"
                )
            if worst.get("n", 0) >= 3 and (worst.get("expectancy_pct") or 0) < -3.0:
                notes.append(
                    f"🔴 Worst regime: {worst['group']} — "
                    f"{worst['win_rate_pct']}% WR, expectancy {worst['expectancy_pct']:+.2f}% "
                    f"({worst['n']} trades) — consider filtering out"
                )

        # Trend direction (last 3 weeks vs prior 3 weeks)
        if len(weekly_trend) >= 6:
            recent_exp = [w.get("expectancy_pct") or 0 for w in weekly_trend[-3:] if w.get("n")]
            older_exp  = [w.get("expectancy_pct") or 0 for w in weekly_trend[-6:-3] if w.get("n")]
            if recent_exp and older_exp:
                delta = sum(recent_exp) / len(recent_exp) - sum(older_exp) / len(older_exp)
                direction = "improving ↑" if delta > 0.5 else ("degrading ↓" if delta < -0.5 else "stable →")
                notes.append(
                    f"📈 Weekly trend: {direction} "
                    f"(recent 3w avg {sum(recent_exp)/len(recent_exp):+.2f}% vs "
                    f"prior 3w {sum(older_exp)/len(older_exp):+.2f}%)"
                )

        # Payoff ratio check
        overall = _exp_stats([r["return_4h_pct"] for r in rows])
        if overall.get("payoff_ratio") is not None and overall["payoff_ratio"] < 0.5:
            notes.append(
                f"⚠️  Low payoff ratio ({overall['payoff_ratio']:.2f}x): "
                "avg wins are less than half of avg losses — "
                "consider tighter position sizing or earlier exits"
            )

        return {
            "lookback":        lookback,
            "total_trades":    len(rows),
            "overall":         overall,
            "by_regime":       by_regime,
            "by_cycle_phase":  by_cycle_phase,
            "weekly_trend":    weekly_trend,
            "diagnosis_notes": notes,
        }
    except Exception as exc:
        log.warning("regime-diagnosis error: %s", exc)
        return JSONResponse({"error": str(exc)}, status_code=200)


# ---------------------------------------------------------------------------
# Patch 168 — Duplicate-entry analysis and horizon comparison (B, C)
# ---------------------------------------------------------------------------

@app.get("/api/brain/horizon-comparison")
async def brain_horizon_comparison(
    lookback: int = 60,
    _: str = Depends(get_current_user),
):
    """
    Patch 168 (B) — Compare the same paper trades across 1h / 4h / 24h horizons.

    Only includes trades where all three horizons are populated.

    Key metrics:
      by_horizon        — win rate, avg win/loss, payoff ratio, expectancy per horizon
      turnaround_count  — trades where 4h<0 but 24h>0 (lost at 4h eval, recovered by 24h)
      deteriorate_count — trades where 4h>0 but 24h<0 (won at 4h, degraded by 24h)
      early_exit_delta  — avg(return_24h - return_4h): positive = holding longer would help
      consistent_wins   — trades positive at all three horizons
      consistent_losses — trades negative at all three horizons

    Answers: is the 4h exit hypothesis cutting winners short?
    """
    _ensure_engine_path()
    lookback = max(5, min(500, lookback))

    try:
        from utils.db import get_conn as _gc  # type: ignore

        with _gc() as _conn:
            rows = [
                dict(r) for r in _conn.execute(
                    """
                    SELECT return_1h_pct, return_4h_pct, return_24h_pct,
                           symbol, created_ts_utc, regime_label, lane, source, score
                    FROM   alert_outcomes
                    WHERE  return_1h_pct  IS NOT NULL
                      AND  return_4h_pct  IS NOT NULL
                      AND  return_24h_pct IS NOT NULL
                    ORDER  BY created_ts_utc DESC
                    LIMIT  ?
                    """,
                    (lookback,),
                ).fetchall()
            ]

        if not rows:
            return {"error": "No trades with all three horizons complete", "n": 0}

        r1h  = [r["return_1h_pct"]  for r in rows]
        r4h  = [r["return_4h_pct"]  for r in rows]
        r24h = [r["return_24h_pct"] for r in rows]

        by_horizon = {
            "1h":  _exp_stats(r1h),
            "4h":  _exp_stats(r4h),
            "24h": _exp_stats(r24h),
        }

        # Turnaround: lost at 4h but recovered by 24h
        turnaround    = [r for r in rows if r["return_4h_pct"] < 0 and r["return_24h_pct"] > 0]
        # Deterioration: positive at 4h but negative by 24h
        deteriorate   = [r for r in rows if r["return_4h_pct"] > 0 and r["return_24h_pct"] < 0]
        # Consistent outcomes
        consisten_win = [r for r in rows if r["return_4h_pct"] > 0 and r["return_24h_pct"] > 0]
        consisten_los = [r for r in rows if r["return_4h_pct"] < 0 and r["return_24h_pct"] < 0]

        # Early-exit delta: how much MORE (or less) each trade would have returned at 24h vs 4h
        delta_24_vs_4 = [r["return_24h_pct"] - r["return_4h_pct"] for r in rows]
        avg_delta     = round(sum(delta_24_vs_4) / len(delta_24_vs_4), 2) if delta_24_vs_4 else 0.0

        n = len(rows)
        return {
            "lookback":           lookback,
            "n":                  n,
            "by_horizon":         by_horizon,
            "turnaround_count":   len(turnaround),
            "turnaround_pct":     round(len(turnaround) / n * 100, 1),
            "deteriorate_count":  len(deteriorate),
            "deteriorate_pct":    round(len(deteriorate) / n * 100, 1),
            "consistent_wins":    len(consisten_win),
            "consistent_losses":  len(consisten_los),
            "early_exit_delta_avg_pct": avg_delta,
            "interpretation": (
                "Holding to 24h would have added an average of "
                f"{avg_delta:+.2f}% per trade vs 4h exit."
                if avg_delta > 0
                else "Holding to 24h would have cost an average of "
                f"{abs(avg_delta):.2f}% per trade vs 4h exit."
            ),
        }
    except Exception as exc:
        log.warning("horizon-comparison error: %s", exc)
        return JSONResponse({"error": str(exc)}, status_code=200)


@app.get("/api/brain/dedup-counterfactual")
async def brain_dedup_counterfactual(
    lookback: int = 60,
    dedup_window_h: float = 4.0,
    _: str = Depends(get_current_user),
):
    """
    Patch 168 (C) — Counterfactual: what would expectancy look like if duplicates
    were removed, and if 24h were used instead of 4h?

    A "duplicate" is defined as: same symbol entered more than once within
    dedup_window_h hours of the first entry in that symbol's current streak.
    Only the first entry per symbol per window is kept.

    Returns four expectancy scenarios:
      all_4h        — current reality: all entries, 4h horizon (the baseline)
      deduped_4h    — duplicates removed, 4h horizon
      all_24h       — all entries, 24h horizon
      deduped_24h   — duplicates removed, 24h horizon (best case)

    Also reports:
      duplicate_count    — how many rows are classified as duplicates
      attribution        — how much of the gap is from dedup vs horizon change
    """
    _ensure_engine_path()
    lookback      = max(5, min(500, lookback))
    dedup_window_h = max(0.5, min(48.0, dedup_window_h))

    try:
        from utils.db import get_conn as _gc  # type: ignore
        from datetime import timedelta as _td

        with _gc() as _conn:
            rows = [
                dict(r) for r in _conn.execute(
                    """
                    SELECT id, symbol, created_ts_utc,
                           return_4h_pct, return_24h_pct,
                           regime_label, lane, source, score
                    FROM   alert_outcomes
                    WHERE  return_4h_pct IS NOT NULL
                    ORDER  BY created_ts_utc ASC
                    LIMIT  ?
                    """,
                    (lookback,),
                ).fetchall()
            ]

        if not rows:
            return {"error": "No completed trades found", "n": 0}

        # Mark duplicates: for each symbol, track last seen ts.
        # If a new entry is within dedup_window_h of the previous one, it's a dup.
        from datetime import datetime as _dt
        sym_last_ts: dict = {}
        window_s = dedup_window_h * 3600

        for r in rows:
            try:
                ts = _dt.fromisoformat(r["created_ts_utc"].replace("Z", "+00:00"))
            except Exception:
                try:
                    ts = _dt.fromisoformat(r["created_ts_utc"])
                except Exception:
                    ts = _dt.utcnow()
            r["_ts"] = ts

        for r in rows:
            sym  = r["symbol"]
            ts   = r["_ts"]
            prev = sym_last_ts.get(sym)
            if prev is not None and (ts - prev).total_seconds() < window_s:
                r["_is_dup"] = True
            else:
                r["_is_dup"] = False
                sym_last_ts[sym] = ts

        all_rows    = rows
        deduped     = [r for r in rows if not r["_is_dup"]]
        dup_count   = len([r for r in rows if r["_is_dup"]])

        # 24h-capable subset (trades where 24h return is also available)
        all_with_24h    = [r for r in all_rows  if r.get("return_24h_pct") is not None]
        deduped_with_24h = [r for r in deduped  if r.get("return_24h_pct") is not None]

        all_4h      = _exp_stats([r["return_4h_pct"]  for r in all_rows])
        deduped_4h  = _exp_stats([r["return_4h_pct"]  for r in deduped])
        all_24h     = _exp_stats([r["return_24h_pct"] for r in all_with_24h])
        deduped_24h = _exp_stats([r["return_24h_pct"] for r in deduped_with_24h])

        # Attribution: how much of the gap comes from each lever
        baseline_exp   = all_4h["expectancy_pct"]     or 0.0
        dedup_only_exp = deduped_4h["expectancy_pct"] or 0.0
        horizon_24_exp = all_24h["expectancy_pct"]    or 0.0
        best_exp       = deduped_24h["expectancy_pct"] or 0.0

        dedup_gain   = round(dedup_only_exp  - baseline_exp, 2)
        horizon_gain = round(horizon_24_exp  - baseline_exp, 2)
        combined_gain = round(best_exp       - baseline_exp, 2)

        # Worst duplicates: symbols with the most dup entries
        from collections import Counter
        dup_symbols = Counter(r["symbol"] for r in rows if r["_is_dup"])

        return {
            "lookback":          lookback,
            "dedup_window_h":    dedup_window_h,
            "total_trades":      len(all_rows),
            "duplicate_count":   dup_count,
            "duplicate_pct":     round(dup_count / len(all_rows) * 100, 1) if all_rows else 0,
            "scenarios": {
                "all_4h":       all_4h,
                "deduped_4h":   deduped_4h,
                "all_24h":      all_24h,
                "deduped_24h":  deduped_24h,
            },
            "attribution": {
                "baseline_4h_expectancy":        round(baseline_exp,   2),
                "dedup_only_gain":               dedup_gain,
                "horizon_24h_gain":              horizon_gain,
                "combined_dedup_plus_24h_gain":  combined_gain,
                "deduped_24h_expectancy":         round(best_exp, 2),
                "note": (
                    f"Removing {dup_count} duplicate entries adds {dedup_gain:+.2f}pp. "
                    f"Switching to 24h evaluation adds {horizon_gain:+.2f}pp. "
                    f"Both together: {combined_gain:+.2f}pp improvement."
                ),
            },
            "top_duplicate_symbols": [
                {"symbol": sym, "dup_count": cnt}
                for sym, cnt in dup_symbols.most_common(10)
            ],
        }
    except Exception as exc:
        log.warning("dedup-counterfactual error: %s", exc)
        return JSONResponse({"error": str(exc)}, status_code=200)


# ---------------------------------------------------------------------------
# Patch 169 — Policy comparison and horizon-based decision support (A, C)
# ---------------------------------------------------------------------------

@app.get("/api/brain/policy-comparison")
async def brain_policy_comparison(
    lookback: int = 60,
    stop_threshold: float = -5.0,
    _: str = Depends(get_current_user),
):
    """
    Patch 169 (A + C) — Compare three evaluation policies using existing paper data.

    Policies:
      current_4h — evaluate all at 4h (current reality)
      hold_24h   — evaluate all at 24h (hold for full day)
      hybrid     — if return_4h < stop_threshold: exit at 4h (stopped out);
                   otherwise hold to 24h (capture recovery)

    Only trades with BOTH 4h and 24h returns are included (ensuring a fair comparison
    across all three policies from the same sample).

    Parameters:
      lookback        — max trades to include (default 60)
      stop_threshold  — hybrid stop-loss trigger in % (default -5.0)

    Returns:
      policies             — full stats for each of the three policies
      decision_support     — which policy wins, horizon-change impact,
                             turnaround analysis, and a direct answer to
                             "would switching to 24h materially change readiness?"
      per_trade_comparison — list of {symbol, return_4h, return_24h, policy_hybrid,
                             category} for every trade in the sample
    """
    _ensure_engine_path()
    lookback = max(5, min(500, lookback))
    stop_threshold = max(-50.0, min(-0.1, stop_threshold))  # keep it sensibly negative

    try:
        from utils.db import get_conn as _gc  # type: ignore

        with _gc() as _conn:
            rows = [
                dict(r) for r in _conn.execute(
                    """
                    SELECT id, symbol, created_ts_utc,
                           return_4h_pct, return_24h_pct,
                           regime_label, lane, source, score
                    FROM   alert_outcomes
                    WHERE  return_4h_pct  IS NOT NULL
                      AND  return_24h_pct IS NOT NULL
                    ORDER  BY created_ts_utc DESC
                    LIMIT  ?
                    """,
                    (lookback,),
                ).fetchall()
            ]

        if not rows:
            return {"error": "No trades with both 4h and 24h data", "n": 0}

        n = len(rows)

        # Policy A: current 4h
        rets_4h  = [r["return_4h_pct"]  for r in rows]
        # Policy B: full 24h hold
        rets_24h = [r["return_24h_pct"] for r in rows]
        # Policy C: hybrid — use 4h when stopped out, 24h otherwise
        rets_hybrid = []
        for r in rows:
            r4h  = r["return_4h_pct"]
            r24h = r["return_24h_pct"]
            rets_hybrid.append(r4h if r4h < stop_threshold else r24h)

        current_4h = _exp_stats(rets_4h)
        hold_24h   = _exp_stats(rets_24h)
        hybrid     = _exp_stats(rets_hybrid)

        # Categorise each trade
        per_trade = []
        stops_triggered = 0
        for i, r in enumerate(rows):
            r4h   = r["return_4h_pct"]
            r24h  = r["return_24h_pct"]
            hyb   = rets_hybrid[i]
            if r4h < stop_threshold:
                cat = "stopped_out"
                stops_triggered += 1
            elif r4h < 0 and r24h > 0:
                cat = "turnaround"
            elif r4h > 0 and r24h < 0:
                cat = "deterioration"
            elif r4h >= 0 and r24h >= 0:
                cat = "consistent_win"
            else:
                cat = "consistent_loss"
            per_trade.append({
                "symbol":        r["symbol"],
                "return_4h_pct": round(r4h,  2),
                "return_24h_pct": round(r24h, 2),
                "return_hybrid_pct": round(hyb, 2),
                "category":      cat,
                "regime_label":  r.get("regime_label"),
                "lane":          r.get("lane"),
            })

        cats = [t["category"] for t in per_trade]
        cat_counts = {
            "stopped_out":      cats.count("stopped_out"),
            "turnaround":       cats.count("turnaround"),
            "deterioration":    cats.count("deterioration"),
            "consistent_win":   cats.count("consistent_win"),
            "consistent_loss":  cats.count("consistent_loss"),
        }

        # Turnaround rescue: avg 24h return for trades that would have been 4h-losers
        turnarounds = [r for r in per_trade if r["category"] == "turnaround"]
        avg_recovery = (
            round(sum(t["return_24h_pct"] for t in turnarounds) / len(turnarounds), 2)
            if turnarounds else None
        )

        # Rank policies by expectancy
        ranked = sorted(
            [
                ("4h",    current_4h.get("expectancy_pct") or 0.0),
                ("24h",   hold_24h.get("expectancy_pct")   or 0.0),
                ("hybrid", hybrid.get("expectancy_pct")    or 0.0),
            ],
            key=lambda x: x[1], reverse=True,
        )
        best_policy, best_exp = ranked[0]

        # Would switching to 24h materially change the readiness verdict?
        exp_4h    = current_4h.get("expectancy_pct") or 0.0
        exp_24h   = hold_24h.get("expectancy_pct")   or 0.0
        exp_hybrid = hybrid.get("expectancy_pct")    or 0.0

        wr_24h    = hold_24h.get("win_rate_pct") or 0.0

        # Readiness verdict under each policy (using same gates as /api/brain/memecoin-readiness)
        def _policy_verdict(exp: float, wr: float, n_trades: int, fg_open: bool, risk_mode: str) -> str:
            blocking = (
                not fg_open
                or risk_mode == "DEFENSIVE"
                or (n_trades >= 20 and exp < -2.0)
            )
            warning  = (
                risk_mode == "CAUTIOUS"
                or (n_trades >= 20 and wr < 30.0)
                or (n_trades >= 20 and -2.0 <= exp < 0.0)
            )
            if blocking:
                return "NOT_READY"
            if warning:
                return "WATCH"
            return "READY"

        # Reuse live F&G / risk from earlier queries — approximate here with DB-only call
        fg_open_approx = True
        risk_mode_approx = "NORMAL"
        try:
            from utils.agent_coordinator import get_fear_greed as _gfg2  # type: ignore
            fg_open_approx = bool(_gfg2(cache_ttl_min=5).get("favorable", True))
            from utils.db import get_risk_mode as _grm2  # type: ignore
            risk_mode_approx = _grm2()["mode"]
        except Exception:
            pass

        verdict_4h     = _policy_verdict(exp_4h,    current_4h.get("win_rate_pct") or 0.0, n, fg_open_approx, risk_mode_approx)
        verdict_24h    = _policy_verdict(exp_24h,   wr_24h,                                 n, fg_open_approx, risk_mode_approx)
        verdict_hybrid = _policy_verdict(exp_hybrid, hybrid.get("win_rate_pct") or 0.0,     n, fg_open_approx, risk_mode_approx)

        verdict_changes = verdict_4h != verdict_24h

        return {
            "lookback":       n,
            "stop_threshold": stop_threshold,
            "policies": {
                "current_4h": {**current_4h, "readiness_verdict": verdict_4h},
                "hold_24h":   {**hold_24h,   "readiness_verdict": verdict_24h},
                "hybrid":     {
                    **hybrid,
                    "readiness_verdict":   verdict_hybrid,
                    "stops_triggered":     stops_triggered,
                    "stop_threshold_pct":  stop_threshold,
                    "note": (
                        f"Hybrid exits at 4h when return_4h < {stop_threshold}% "
                        f"({stops_triggered} of {n} trades stopped). "
                        "Otherwise holds to 24h."
                    ),
                },
            },
            "decision_support": {
                "best_policy":            best_policy,
                "best_policy_expectancy": round(best_exp, 2),
                "policy_ranking":         [{"policy": p, "expectancy_pct": round(e, 2)} for p, e in ranked],
                "verdict_changes_at_24h": verdict_changes,
                "verdict_4h":             verdict_4h,
                "verdict_24h":            verdict_24h,
                "verdict_hybrid":         verdict_hybrid,
                "horizon_gain_pct":       round(exp_24h - exp_4h, 2),
                "hybrid_vs_24h_pct":      round(exp_hybrid - exp_24h, 2),
                "turnaround_count":       cat_counts["turnaround"],
                "turnaround_pct":         round(cat_counts["turnaround"] / n * 100, 1),
                "avg_turnaround_recovery_pct": avg_recovery,
                "stops_triggered_count":  stops_triggered,
                "stops_triggered_pct":    round(stops_triggered / n * 100, 1),
                "summary": (
                    f"Best policy from existing data: {best_policy.upper()} "
                    f"(expectancy {best_exp:+.2f}%). "
                    f"Switching from 4h to 24h adds {exp_24h - exp_4h:+.2f}pp. "
                    f"Adding the {abs(stop_threshold):.0f}% stop to 24h "
                    f"({'gains' if exp_hybrid > exp_24h else 'costs'} "
                    f"{abs(exp_hybrid - exp_24h):.2f}pp vs pure 24h). "
                    + (
                        f"Readiness verdict WOULD change from {verdict_4h} to {verdict_24h} at 24h."
                        if verdict_changes
                        else f"Readiness verdict remains {verdict_4h} regardless of horizon."
                    )
                ),
            },
            "trade_categories":      cat_counts,
            "per_trade_comparison":  per_trade,
        }
    except Exception as exc:
        log.warning("policy-comparison error: %s", exc)
        return JSONResponse({"error": str(exc)}, status_code=200)


# ---------------------------------------------------------------------------
# Patch 171 — Live-pilot readiness framework (A, B, C, D)
# ---------------------------------------------------------------------------

@app.get("/api/brain/memecoin-pilot-readiness")
async def brain_memecoin_pilot_readiness(_: str = Depends(get_current_user)):
    """
    Patch 171 — Conservative live-pilot readiness for the memecoin scanner arm.

    Three-tier promotion framework made explicit:

      Tier 1 — paper_readiness  (/api/brain/memecoin-readiness)
               Confirms the paper strategy has expected value at the correct horizon.
               Verdict: NOT_READY | WATCH | READY

      Tier 2 — live_pilot       (/api/brain/memecoin-pilot-readiness)   ← THIS
               Confirms conditions are safe for a tiny, capped, human-monitored pilot.
               Stricter gates: F&G >35, NORMAL mode only, positive 24h edge,
               post-cooldown clean sample confirmed.
               Verdict: NOT_PILOT_READY | PILOT_WATCH | PILOT_READY

      Tier 3 — full_live        (not yet implemented — described only)
               Multi-week consistency, ≥50 clean post-cooldown trades, F&G ≥50,
               explicit human sign-off. Not gated in this patch.
               Verdict: (reserved)

    This endpoint does NOT change live trading behavior.
    pilot_constraints is decision support only — not enforced by the engine.
    """
    _ensure_engine_path()

    # ── 1. Market data ────────────────────────────────────────────────────────
    fg_value: int | None = None
    fg_label  = "UNKNOWN"
    fg_open   = True
    try:
        from utils.agent_coordinator import get_fear_greed as _gfg  # type: ignore
        fg = _gfg(cache_ttl_min=5)
        fg_value = fg.get("value")
        fg_label = fg.get("label", "UNKNOWN")
        fg_open  = bool(fg.get("favorable", True))
    except Exception:
        pass

    risk: dict = {"mode": "NORMAL", "streak": 0}
    try:
        from utils.db import get_risk_mode as _grm  # type: ignore
        risk = _grm()
    except Exception:
        pass

    # ── 2. Edge metrics — last 20 completed 24h trades ────────────────────────
    # _exp_stats is defined in the Patch 167 section above; safe to call at runtime.
    stats_24h  = {"n": 0, "win_rate_pct": None, "expectancy_pct": None,
                  "avg_return_pct": None, "payoff_ratio": None,
                  "avg_win_pct": None, "avg_loss_pct": None}
    rolling_w1 = {"n": 0, "expectancy_pct": None}   # most-recent 10
    rolling_w2 = {"n": 0, "expectancy_pct": None}   # prior 10
    try:
        from utils.db import get_conn as _gc  # type: ignore
        with _gc() as _conn:
            _r = _conn.execute(
                "SELECT return_24h_pct FROM alert_outcomes "
                "WHERE return_24h_pct IS NOT NULL "
                "ORDER BY evaluated_24h_ts_utc DESC LIMIT 20"
            ).fetchall()
        rets = [float(r[0]) for r in _r]
        stats_24h  = _exp_stats(rets)
        if len(rets) >= 10:
            rolling_w1 = _exp_stats(rets[:10])
        if len(rets) >= 20:
            rolling_w2 = _exp_stats(rets[10:20])
    except Exception:
        pass

    sample_24h = stats_24h["n"]
    exp_24h    = stats_24h["expectancy_pct"] if stats_24h["expectancy_pct"] is not None else 0.0
    wr_24h     = stats_24h["win_rate_pct"]   if stats_24h["win_rate_pct"]   is not None else 0.0
    pr_24h     = stats_24h["payoff_ratio"]   # may be None if all trades are wins
    rw1_exp    = rolling_w1["expectancy_pct"] if rolling_w1["expectancy_pct"] is not None else 0.0
    rw2_exp    = rolling_w2["expectancy_pct"] if rolling_w2["expectancy_pct"] is not None else 0.0

    # ── 3. Post-cooldown sample ───────────────────────────────────────────────
    # Dedup guard (Patch 168A) activated 2026-03-07.
    # Count 24h-evaluated trades entered after that date — these are "clean"
    # (no duplicate-entry inflation from the pre-cooldown ARC/GRASS clusters).
    COOLDOWN_DATE = "2026-03-07T00:00:00"
    post_cooldown_n = 0
    try:
        from utils.db import get_conn as _gc  # type: ignore
        with _gc() as _cpc:
            _pc = _cpc.execute(
                "SELECT COUNT(*) FROM alert_outcomes "
                "WHERE created_ts_utc >= ? AND return_24h_pct IS NOT NULL",
                (COOLDOWN_DATE,),
            ).fetchone()
        post_cooldown_n = int(_pc[0]) if _pc else 0
    except Exception:
        pass

    # ── 4. Gate evaluation ────────────────────────────────────────────────────
    # Thresholds — stricter than paper readiness on every axis
    _PILOT_FG_MIN       = 35    # paper gate is 25; pilot adds +10pt safety buffer
    _PILOT_EXP_MIN      = 2.0   # positive, not just non-negative (-2% is paper gate)
    _PILOT_WR_MIN       = 55.0  # majority of trades must win
    _PILOT_PR_MIN       = 1.0   # wins must outweigh losses on average
    _PILOT_COOLDOWN_MIN = 20    # clean post-dedup-guard 24h trades required
    _PILOT_FG_OPTIMAL   = 50    # soft: neutral/greedy territory preferred

    mkt_blockers:    list[str] = []
    edge_blockers:   list[str] = []
    sample_blockers: list[str] = []
    soft_warnings:   list[str] = []

    # Market hard gates
    _fg_pilot_ok    = fg_value is not None and fg_value > _PILOT_FG_MIN
    _risk_normal_ok = risk["mode"] == "NORMAL"

    if not _fg_pilot_ok:
        mkt_blockers.append(
            f"F&G {fg_value} ({fg_label}) — pilot requires F&G >{_PILOT_FG_MIN} "
            f"(paper gate is >25; pilot adds a +10pt buffer)"
        )
    if not _risk_normal_ok:
        mkt_blockers.append(
            f"Risk mode is {risk['mode']} (streak={risk['streak']}) — "
            "pilot requires NORMAL; CAUTIOUS and DEFENSIVE are both blocked"
        )

    # Edge hard gates
    if sample_24h < 20:
        edge_blockers.append(
            f"Insufficient 24h sample ({sample_24h}/20 trades to evaluate edge)"
        )
    else:
        if exp_24h < _PILOT_EXP_MIN:
            edge_blockers.append(
                f"24h expectancy {exp_24h:+.2f}% below pilot threshold "
                f"(+{_PILOT_EXP_MIN:.1f}%). Strategy must show positive EV before live exposure."
            )
        if wr_24h < _PILOT_WR_MIN:
            edge_blockers.append(
                f"24h win rate {wr_24h:.1f}% below pilot threshold ({_PILOT_WR_MIN:.0f}%)"
            )
        # payoff_ratio is None when all trades are wins (infinite payoff — gate passes)
        _pr_ok = pr_24h is None or pr_24h >= _PILOT_PR_MIN
        if not _pr_ok:
            edge_blockers.append(
                f"24h payoff ratio {pr_24h:.2f}x below pilot threshold ({_PILOT_PR_MIN:.1f}x) — "
                "average win must exceed average loss"
            )

    # Sample hard gate
    if post_cooldown_n < _PILOT_COOLDOWN_MIN:
        sample_blockers.append(
            f"Post-cooldown sample {post_cooldown_n}/{_PILOT_COOLDOWN_MIN} clean 24h trades. "
            f"Dedup guard activated {COOLDOWN_DATE[:10]}. "
            "Need ≥20 fully 24h-evaluated trades entered after that date to confirm "
            "the 24h edge holds without duplicate-entry inflation."
        )

    # Soft gates — PILOT_WATCH if any fail (hard gates all passing)
    _rolling_ok = (
        rolling_w1["n"] >= 10 and rw1_exp > 0 and
        rolling_w2["n"] >= 10 and rw2_exp > 0
    )
    if not _rolling_ok:
        if rolling_w1["n"] < 10 or rolling_w2["n"] < 10:
            soft_warnings.append(
                f"Rolling consistency: insufficient data ({sample_24h}/20 trades needed)"
            )
        else:
            soft_warnings.append(
                f"Rolling consistency: not both 10-trade windows positive "
                f"(recent: {rw1_exp:+.2f}%, prior: {rw2_exp:+.2f}%)"
            )

    _fg_optimal_ok = fg_value is not None and fg_value > _PILOT_FG_OPTIMAL
    if _fg_pilot_ok and not _fg_optimal_ok:
        soft_warnings.append(
            f"F&G {fg_value} is above pilot minimum ({_PILOT_FG_MIN}) but below optimal "
            f"({_PILOT_FG_OPTIMAL}). Fear conditions may dampen signal quality."
        )

    # ── 5. Verdict ────────────────────────────────────────────────────────────
    hard_blockers = mkt_blockers + edge_blockers + sample_blockers
    if hard_blockers:
        verdict = "NOT_PILOT_READY"
    elif soft_warnings:
        verdict = "PILOT_WATCH"
    else:
        verdict = "PILOT_READY"

    # ── 6. Gate detail ────────────────────────────────────────────────────────
    gate_detail = [
        {
            "gate":        "fg_pilot",
            "category":    "market",
            "tier":        "pilot_hard",
            "description": f"F&G must be >{_PILOT_FG_MIN} (pilot adds +10pt over paper's >25 gate)",
            "value":       fg_value,
            "threshold":   _PILOT_FG_MIN,
            "passing":     _fg_pilot_ok,
            "delta":       round(fg_value - _PILOT_FG_MIN, 1) if fg_value is not None else None,
            "notes":       (
                f"Currently {fg_value} ({fg_label}). "
                f"Needs +{_PILOT_FG_MIN - fg_value}pt recovery to open pilot gate."
                if not _fg_pilot_ok
                else f"Currently {fg_value} ({fg_label}) — pilot market gate open."
            ),
        },
        {
            "gate":        "risk_mode_normal",
            "category":    "market",
            "tier":        "pilot_hard",
            "description": "Risk mode must be NORMAL (pilot blocks CAUTIOUS and DEFENSIVE)",
            "value":       risk["mode"],
            "threshold":   "NORMAL",
            "passing":     _risk_normal_ok,
            "delta":       None,
            "notes":       (
                f"Streak: {risk['streak']} consecutive losses. "
                "Pilot requires a clean NORMAL state — no recent loss streak tolerance."
            ),
        },
        {
            "gate":        "edge_expectancy_24h",
            "category":    "edge",
            "tier":        "pilot_hard",
            "description": f"24h expectancy ≥ +{_PILOT_EXP_MIN:.1f}% over last 20 trades (positive EV required)",
            "value":       exp_24h if sample_24h >= 20 else None,
            "threshold":   _PILOT_EXP_MIN,
            "passing":     sample_24h >= 20 and exp_24h >= _PILOT_EXP_MIN,
            "delta":       round(exp_24h - _PILOT_EXP_MIN, 2) if sample_24h >= 20 else None,
            "notes":       (
                f"Currently {exp_24h:+.2f}% over {sample_24h} trades — gate passing."
                if sample_24h >= 20 and exp_24h >= _PILOT_EXP_MIN
                else f"Currently {exp_24h:+.2f}% over {sample_24h} trades."
            ),
        },
        {
            "gate":        "edge_win_rate_24h",
            "category":    "edge",
            "tier":        "pilot_hard",
            "description": f"24h win rate ≥ {_PILOT_WR_MIN:.0f}% over last 20 trades",
            "value":       wr_24h if sample_24h >= 20 else None,
            "threshold":   _PILOT_WR_MIN,
            "passing":     sample_24h >= 20 and wr_24h >= _PILOT_WR_MIN,
            "delta":       round(wr_24h - _PILOT_WR_MIN, 1) if sample_24h >= 20 else None,
            "notes":       f"Currently {wr_24h:.1f}% over {sample_24h} trades.",
        },
        {
            "gate":        "edge_payoff_ratio_24h",
            "category":    "edge",
            "tier":        "pilot_hard",
            "description": f"24h payoff ratio ≥ {_PILOT_PR_MIN:.1f}x (avg win / avg loss); None = all wins = passes",
            "value":       round(pr_24h, 2) if pr_24h is not None and sample_24h >= 20 else ("all_wins" if pr_24h is None and sample_24h >= 20 else None),
            "threshold":   _PILOT_PR_MIN,
            "passing":     sample_24h >= 20 and (pr_24h is None or pr_24h >= _PILOT_PR_MIN),
            "delta":       round(pr_24h - _PILOT_PR_MIN, 2) if pr_24h is not None and sample_24h >= 20 else None,
            "notes":       (
                f"Currently {pr_24h:.2f}x — very high ratio likely reflects a small loss "
                "sample (≤3 loss trades). Gate passes but treat ratio as unreliable; "
                "rely on expectancy and win rate instead."
                if pr_24h is not None and pr_24h > 50 and sample_24h >= 20
                else f"Currently {round(pr_24h, 2) if pr_24h else 'all wins'}x over {sample_24h} trades."
            ),
        },
        {
            "gate":        "post_cooldown_sample",
            "category":    "sample",
            "tier":        "pilot_hard",
            "description": (
                f"≥{_PILOT_COOLDOWN_MIN} fully 24h-evaluated trades entered after "
                f"{COOLDOWN_DATE[:10]} (confirms edge is not duplicate-inflated)"
            ),
            "value":       post_cooldown_n,
            "threshold":   _PILOT_COOLDOWN_MIN,
            "passing":     post_cooldown_n >= _PILOT_COOLDOWN_MIN,
            "delta":       post_cooldown_n - _PILOT_COOLDOWN_MIN,
            "notes":       (
                f"{post_cooldown_n} clean trades so far, "
                f"{_PILOT_COOLDOWN_MIN - post_cooldown_n} more needed. "
                "At ~4-6 new scanner entries/day this takes roughly "
                f"{max(1, (_PILOT_COOLDOWN_MIN - post_cooldown_n) // 5)}–"
                f"{max(1, (_PILOT_COOLDOWN_MIN - post_cooldown_n) // 4 + 1)} days "
                "once F&G gate is also open."
                if post_cooldown_n < _PILOT_COOLDOWN_MIN
                else f"{post_cooldown_n} clean post-cooldown trades — gate satisfied."
            ),
        },
        {
            "gate":        "rolling_consistency",
            "category":    "edge",
            "tier":        "pilot_soft",
            "description": "Both most-recent 10-trade and prior 10-trade 24h windows must be positive",
            "value":       {
                "window_recent_10": round(rw1_exp, 2) if rolling_w1["n"] >= 10 else None,
                "window_prior_10":  round(rw2_exp, 2) if rolling_w2["n"] >= 10 else None,
            },
            "threshold":   ">0 in both windows",
            "passing":     _rolling_ok,
            "delta":       None,
            "notes":       (
                f"Recent-10: {rw1_exp:+.2f}%  |  Prior-10: {rw2_exp:+.2f}%"
                if rolling_w1["n"] >= 10 and rolling_w2["n"] >= 10
                else "Insufficient data for rolling check."
            ),
        },
        {
            "gate":        "fg_optimal",
            "category":    "market",
            "tier":        "pilot_soft",
            "description": f"F&G >{_PILOT_FG_OPTIMAL} (neutral/greedy) — optimal timing for first live entry",
            "value":       fg_value,
            "threshold":   _PILOT_FG_OPTIMAL,
            "passing":     _fg_optimal_ok,
            "delta":       round(fg_value - _PILOT_FG_OPTIMAL, 1) if fg_value is not None else None,
            "notes":       (
                f"F&G {fg_value} — above pilot minimum but below optimal. "
                "Fear tends to suppress signal quality and momentum capture."
                if _fg_pilot_ok and not _fg_optimal_ok
                else f"F&G {fg_value} — not yet at pilot minimum ({_PILOT_FG_MIN})."
                if not _fg_pilot_ok
                else f"F&G {fg_value} — optimal range."
            ),
        },
    ]

    # ── 7. Three-tier framework (C) ───────────────────────────────────────────
    # Compute paper verdict for tier_1 reference (simplified — check main gate only)
    _paper_fg_ok  = fg_value is not None and fg_value > 25
    _paper_exp_ok = sample_24h < 20 or exp_24h >= -2.0
    if not _paper_fg_ok or not _paper_exp_ok:
        _paper_verdict = "NOT_READY"
    else:
        _paper_verdict = "READY" if exp_24h >= 0 else "WATCH"

    promotion_tiers = {
        "tier_1_paper_readiness": {
            "endpoint":     "/api/brain/memecoin-readiness",
            "verdict_scale": ["NOT_READY", "WATCH", "READY"],
            "description": (
                "Confirms the paper strategy has non-negative expected value at the "
                "data-supported evaluation horizon (24h). No capital at risk. "
                "Market gates must be open, edge gate uses -2% floor."
            ),
            "key_gates": [
                "F&G > 25",
                "risk_mode != DEFENSIVE",
                "24h expectancy ≥ -2% (primary edge gate since Patch 170)",
            ],
            "current_verdict": _paper_verdict,
            "gate_differences_vs_pilot": {
                "fg_threshold":     {"paper": 25,   "pilot": _PILOT_FG_MIN},
                "risk_mode":        {"paper": "not DEFENSIVE", "pilot": "must be NORMAL"},
                "edge_threshold":   {"paper": -2.0, "pilot": _PILOT_EXP_MIN},
                "win_rate_gate":    {"paper": "none", "pilot": f"≥{_PILOT_WR_MIN:.0f}%"},
                "payoff_gate":      {"paper": "none", "pilot": f"≥{_PILOT_PR_MIN:.1f}x"},
                "sample_gate":      {"paper": "none", "pilot": f"≥{_PILOT_COOLDOWN_MIN} post-cooldown"},
            },
        },
        "tier_2_live_pilot": {
            "endpoint":     "/api/brain/memecoin-pilot-readiness",
            "verdict_scale": ["NOT_PILOT_READY", "PILOT_WATCH", "PILOT_READY"],
            "description": (
                "Confirms conditions are safe for a tiny controlled live pilot. "
                "Stricter market gate (F&G >35), NORMAL mode only (no streak tolerance), "
                "positive expected value at 24h, and ≥20 clean post-cooldown trades. "
                "Capital at risk is capped by pilot_constraints. Human review required "
                "at 10 and 20 trade checkpoints."
            ),
            "key_gates": [
                f"F&G >{_PILOT_FG_MIN}",
                "risk_mode == NORMAL",
                f"24h expectancy ≥ +{_PILOT_EXP_MIN:.1f}%",
                f"24h win rate ≥ {_PILOT_WR_MIN:.0f}%",
                f"24h payoff ratio ≥ {_PILOT_PR_MIN:.1f}x",
                f"≥{_PILOT_COOLDOWN_MIN} post-cooldown clean 24h trades",
            ],
            "current_verdict": verdict,
            "current_blockers": hard_blockers,
        },
        "tier_3_full_live": {
            "endpoint":     "(not yet implemented)",
            "verdict_scale": ["(reserved — not gated in Patch 171)"],
            "description": (
                "Full unrestricted live rollout with no capital cap. "
                "Requires multi-week consistency across regimes, a large post-cooldown "
                "sample, F&G in neutral or greedy territory, and explicit human "
                "sign-off. This tier must be a deliberate future decision — "
                "it is not reachable via any automated path."
            ),
            "key_gates": [
                f"F&G ≥ {_PILOT_FG_OPTIMAL} (neutral or greedy)",
                "24h expectancy ≥ +5.0% over last 30 trades",
                "≥ 50 post-cooldown clean 24h trades",
                "4 consecutive weeks of positive 24h rolling expectancy",
                "Live pilot completed with ≥20 trades and positive P&L",
                "Human review and explicit live-mode enable (ENV change required)",
            ],
            "current_verdict": "(not evaluated — reserved for future patch)",
        },
    }

    # ── 8. Pilot constraints (decision support — not enforced by engine) ───────
    pilot_constraints = {
        "max_concurrent_live_positions": 1,
        "max_capital_per_trade_sol":     0.1,
        "max_capital_per_trade_usd_approx": 15,
        "max_daily_loss_usd":            25,
        "min_fg_at_entry":               _PILOT_FG_MIN,
        "excluded_regimes":              ["UNKNOWN"],
        "exit_policy":                   "hold_24h_with_stop",
        "stop_loss_pct":                 -10.0,
        "evaluation_horizon":            "24h",
        "trial_duration_days":           14,
        "max_pilot_trades_total":        20,
        "human_review_after_trades":     10,
        "enforcement":                   "decision_support_only — not enforced by the engine",
        "rationale": [
            "0.1 SOL per trade caps worst-case loss to <$15 — affordable learning tax",
            "UNKNOWN regime excluded: where pre-cooldown duplicate damage concentrated",
            "14-day / 20-trade cap creates a hard review gate before continuation",
            "10-trade checkpoint gives early signal on live vs paper slippage",
            "-10% stop prevents one catastrophic trade from dominating pilot P&L",
            "Single concurrent position removes correlation and sizing decisions",
        ],
    }

    # ── 9. Path to PILOT_READY ────────────────────────────────────────────────
    path_to_pilot: list[str] = []
    if not _fg_pilot_ok:
        gap = (_PILOT_FG_MIN - fg_value) if fg_value is not None else "?"
        path_to_pilot.append(
            f"F&G must recover {gap}pt to >{_PILOT_FG_MIN} (currently {fg_value}). "
            "Market-driven — no scanner action needed, only wait."
        )
    if not _risk_normal_ok:
        path_to_pilot.append(
            f"Risk mode must return to NORMAL (currently {risk['mode']}, "
            f"streak={risk['streak']}). Requires winning 4h trades to clear."
        )
    if edge_blockers:
        path_to_pilot.extend(edge_blockers)
    if sample_blockers:
        remaining = _PILOT_COOLDOWN_MIN - post_cooldown_n
        path_to_pilot.append(
            f"Post-cooldown sample: {remaining} more clean 24h trades needed. "
            f"At ~4-6 new scanner entries per day, this takes approximately "
            f"{max(1, remaining // 6)}–{max(1, remaining // 4)} days "
            "(once F&G gate is also open and scanner is firing)."
        )
    if not path_to_pilot and soft_warnings:
        path_to_pilot.extend(soft_warnings)

    return {
        "verdict":          verdict,
        "active_blockers":  hard_blockers,
        "warnings":         soft_warnings,
        "blocker_categories": {
            "market": mkt_blockers,
            "edge":   edge_blockers,
            "sample": sample_blockers,
        },
        "gate_detail":         gate_detail,
        "path_to_pilot_ready": path_to_pilot,
        "promotion_tiers":     promotion_tiers,
        "pilot_constraints":   pilot_constraints,
        "horizon_context": {
            "strategy_horizon":      "24h",
            "sample_n_24h":          sample_24h,
            "expectancy_24h":        exp_24h,
            "win_rate_24h":          wr_24h,
            "payoff_ratio_24h":      round(pr_24h, 2) if pr_24h is not None else None,
            "rolling_recent_10_exp": round(rw1_exp, 2) if rolling_w1["n"] >= 10 else None,
            "rolling_prior_10_exp":  round(rw2_exp, 2) if rolling_w2["n"] >= 10 else None,
        },
        "metrics": {
            "fg_value":        fg_value,
            "fg_label":        fg_label,
            "risk_mode":       risk["mode"],
            "risk_streak":     risk["streak"],
            "post_cooldown_n": post_cooldown_n,
            "cooldown_date":   COOLDOWN_DATE[:10],
        },
    }


# ---------------------------------------------------------------------------
# Phase 2.5 — Sim Tracker: readiness score, full equity curve, CSV export
# ---------------------------------------------------------------------------

def _ensure_sim_tracker():
    """Add engine root to sys.path so sim_tracker can be imported."""
    import sys
    from pathlib import Path as _P
    root = str(_P(__file__).resolve().parents[2])
    if root not in sys.path:
        sys.path.insert(0, root)


@app.get("/api/performance/readiness-score")
async def perf_readiness_score(
    lookback_days: int = 30,
    _: str = Depends(get_current_user),
):
    """
    Composite 'Live Readiness Score' (0–100).
    Measures if the engine is ready to trade live capital based on:
    sample size, win rate, expectancy, drawdown resilience, horizon consistency.
    """
    try:
        _ensure_sim_tracker()
        from utils.sim_tracker import get_readiness_score  # type: ignore
        return await asyncio.to_thread(get_readiness_score, lookback_days)
    except Exception as exc:
        log.warning("readiness-score error: %s", exc)
        return JSONResponse({"score": 0, "status": "NOT_READY", "error": str(exc)}, status_code=200)


@app.get("/api/performance/sim-summary")
async def perf_sim_summary(
    lookback_days: int = 30,
    horizon_hours: int = 4,
    fee_pct: float = 0.5,
    _: str = Depends(get_current_user),
):
    """
    Fee-adjusted P&L simulation summary from alert_outcomes.
    Includes: win_rate, avg_net_ret, max_drawdown, equity_end, by_regime, by_lane.
    """
    try:
        _ensure_sim_tracker()
        from utils.sim_tracker import get_sim_summary  # type: ignore
        return await asyncio.to_thread(
            get_sim_summary,
            max(1, int(lookback_days)),
            int(horizon_hours),
            float(fee_pct),
        )
    except Exception as exc:
        log.warning("sim-summary error: %s", exc)
        return JSONResponse({"error": str(exc), "trades": 0}, status_code=200)


@app.get("/api/performance/equity-curve-v2")
async def perf_equity_curve_v2(
    lookback_days: int = 30,
    horizon_hours: int = 4,
    fee_pct: float = 0.5,
    _: str = Depends(get_current_user),
):
    """
    Full fee-adjusted equity curve with drawdown at each point.
    Returns list of { trade_n, ts, symbol, gross_ret, net_ret, equity, equity_pct, drawdown_pct }.
    """
    try:
        _ensure_sim_tracker()
        from utils.sim_tracker import get_equity_curve  # type: ignore
        return await asyncio.to_thread(
            get_equity_curve,
            max(1, int(lookback_days)),
            int(horizon_hours),
            float(fee_pct),
        )
    except Exception as exc:
        log.warning("equity-curve-v2 error: %s", exc)
        return JSONResponse([], status_code=200)


@app.get("/api/performance/export-csv")
async def perf_export_csv(
    lookback_days: int = 90,
    horizon_hours: int = 4,
    fee_pct: float = 0.5,
    _: str = Depends(get_current_user),
):
    """
    Download all alert_outcomes as a CSV file (fee-adjusted net return included).
    """
    from fastapi.responses import StreamingResponse as _SR
    import io as _io
    try:
        _ensure_sim_tracker()
        from utils.sim_tracker import export_outcomes_csv  # type: ignore
        csv_str = await asyncio.to_thread(
            export_outcomes_csv,
            max(1, int(lookback_days)),
            int(horizon_hours),
            float(fee_pct),
        )
        filename = f"memecoin_outcomes_{lookback_days}d.csv"
        return _SR(
            iter([csv_str]),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )
    except Exception as exc:
        log.warning("export-csv error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/sniper/second-leg")
async def get_second_leg_candidates_api():
    """Return tokens currently in second-leg territory from ATH tracker."""
    try:
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
        from utils.ath_tracker import get_second_leg_candidates
        from utils.db import get_conn

        # Prime candidates (75%+ drawdown)
        candidates = get_second_leg_candidates(min_drawdown_pct=75.0, limit=20)

        # Approaching zone (60-74%)
        approaching = []
        try:
            with get_conn() as conn:
                rows = conn.execute(
                    """SELECT mint, symbol, ath_price, last_price, pct_from_ath, leg, ath_ts_utc, last_seen_utc
                       FROM token_ath
                       WHERE pct_from_ath >= 0.60 AND pct_from_ath < 0.75
                       ORDER BY pct_from_ath DESC LIMIT 10"""
                ).fetchall()
                approaching = [
                    {
                        "mint": r[0], "symbol": r[1], "ath_price": r[2],
                        "last_price": r[3], "drawdown_pct": round(r[4] * 100, 1),
                        "leg": r[5], "ath_ts_utc": r[6], "last_seen_utc": r[7],
                    }
                    for r in rows
                ]
        except Exception:
            pass

        # Total ATH tracking count
        total_tracked = 0
        try:
            with get_conn() as conn:
                total_tracked = conn.execute("SELECT COUNT(*) FROM token_ath").fetchone()[0]
        except Exception:
            pass

        return {
            "prime_candidates": candidates,
            "approaching": approaching,
            "total_tracked": total_tracked,
            "updated_at": datetime.utcnow().isoformat(),
        }
    except Exception as e:
        return {"prime_candidates": [], "approaching": [], "total_tracked": 0, "error": str(e)}


# ---------------------------------------------------------------------------
# Perpetuals Executor Endpoints — /api/perps/*
# ---------------------------------------------------------------------------

def _ensure_perp_executor():
    """Add engine root to sys.path so perp_executor can be imported."""
    import sys
    root = _engine_root()
    if root not in sys.path:
        sys.path.insert(0, root)


@app.get("/api/perps/status")
async def perps_status(_: str = Depends(get_current_user)):
    """Return perp executor state + open positions."""
    try:
        _ensure_perp_executor()
        from utils.perp_executor import get_perp_status  # type: ignore
        return get_perp_status()
    except Exception as exc:
        log.warning("perps_status error: %s", exc)
        return JSONResponse(
            {"enabled": False, "dry_run": True, "open_positions": 0, "positions": [], "error": str(exc)},
            status_code=200,
        )


@app.post("/api/perps/toggle")
async def perps_toggle(body: dict, _: str = Depends(get_current_user)):
    """Enable or disable perp executor. body: { enabled: bool }"""
    import re
    enabled  = bool(body.get("enabled", False))
    env_path = os.path.join(_engine_root(), ".env")
    try:
        if os.path.exists(env_path):
            text = open(env_path).read()
            if "PERP_EXECUTOR_ENABLED=" in text:
                text = re.sub(
                    r"^PERP_EXECUTOR_ENABLED=.*$",
                    f"PERP_EXECUTOR_ENABLED={'true' if enabled else 'false'}",
                    text, flags=re.MULTILINE,
                )
            else:
                text += f"\nPERP_EXECUTOR_ENABLED={'true' if enabled else 'false'}\n"
            open(env_path, "w").write(text)
        os.environ["PERP_EXECUTOR_ENABLED"] = "true" if enabled else "false"
        return {"success": True, "enabled": enabled}
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)


@app.post("/api/perps/set-dry-run")
async def perps_set_dry_run(body: dict, _: str = Depends(get_current_user)):
    """Set PERP_DRY_RUN. body: { dry_run: bool }"""
    import re
    dry_run  = bool(body.get("dry_run", True))
    env_path = os.path.join(_engine_root(), ".env")
    try:
        if os.path.exists(env_path):
            text = open(env_path).read()
            if "PERP_DRY_RUN=" in text:
                text = re.sub(
                    r"^PERP_DRY_RUN=.*$",
                    f"PERP_DRY_RUN={'true' if dry_run else 'false'}",
                    text, flags=re.MULTILINE,
                )
            else:
                text += f"\nPERP_DRY_RUN={'true' if dry_run else 'false'}\n"
            open(env_path, "w").write(text)
        os.environ["PERP_DRY_RUN"] = "true" if dry_run else "false"
        return {"success": True, "dry_run": dry_run}
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)


@app.post("/api/perps/force-close")
async def perps_force_close(body: dict, _: str = Depends(get_current_user)):
    """Force-close a perp position. body: { position_id: int }"""
    position_id = int(body.get("position_id", 0))
    if not position_id:
        return JSONResponse({"success": False, "error": "position_id required"}, status_code=400)
    try:
        _ensure_perp_executor()
        from utils.perp_executor import force_close_perp  # type: ignore
        return await force_close_perp(position_id)
    except Exception as exc:
        log.warning("perps_force_close error: %s", exc)
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)


class ManualPerpRequest(BaseModel):
    symbol: str = "SOL"
    side: str = "LONG"
    size_usd: float | None = None
    leverage: float = 2.0


@app.post("/api/perps/manual-open")
async def perps_manual_open(body: ManualPerpRequest, _: str = Depends(get_current_user)):
    """Open a manual perp position from the dashboard."""
    try:
        _ensure_perp_executor()
        from utils.perp_executor import execute_perp_signal  # type: ignore

        symbol = body.symbol.strip().upper()
        side   = body.side.strip().upper()
        if symbol not in ("SOL", "BTC", "ETH"):
            return JSONResponse({"success": False, "error": "symbol must be SOL, BTC, or ETH"}, status_code=400)
        if side not in ("LONG", "SHORT"):
            return JSONResponse({"success": False, "error": "side must be LONG or SHORT"}, status_code=400)

        signal = {
            "symbol":       symbol,
            "side":         side,
            "size_usd":     body.size_usd or float(os.getenv("PERP_SIZE_USD", "100")),
            "leverage":     body.leverage,
            "regime_label": "MANUAL",
            "source":       "dashboard",
        }
        asyncio.create_task(execute_perp_signal(signal))
        return {"success": True, "symbol": symbol, "side": side, "leverage": body.leverage}
    except Exception as exc:
        log.warning("perps_manual_open error: %s", exc)
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)


@app.get("/api/perps/equity-curve")
async def perps_equity_curve(
    lookback_days: int = 30,
    _: str = Depends(get_current_user),
):
    """Cumulative PnL curve from closed perp positions."""
    try:
        _ensure_perp_executor()
        from utils.perp_executor import get_perp_equity_curve  # type: ignore
        return await asyncio.to_thread(get_perp_equity_curve, lookback_days)
    except Exception as exc:
        log.warning("perps_equity_curve error: %s", exc)
        return JSONResponse([], status_code=200)


@app.get("/api/perps/closed")
async def perps_closed(
    limit: int = 30,
    _: str = Depends(get_current_user),
):
    """Return recently closed perp positions for the journal."""
    try:
        _ensure_perp_executor()
        import sqlite3
        from pathlib import Path
        db = Path(__file__).resolve().parents[1] / ".." / "data_storage" / "engine.db"
        with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT id, symbol, side, entry_price, exit_price, pnl_pct,
                       stop_price, tp1_price, tp2_price, exit_reason,
                       opened_ts_utc, closed_ts_utc, size_usd, leverage,
                       dry_run, notes
                FROM perp_positions
                WHERE status = 'CLOSED'
                ORDER BY closed_ts_utc DESC
                LIMIT ?
            """, (min(limit, 100),)).fetchall()
            return [dict(r) for r in rows]
    except Exception as exc:
        log.warning("perps_closed error: %s", exc)
        return JSONResponse([], status_code=200)


# ── Patches 104–125: Memecoins / Wallet / Tiers / Portfolio ──────────────────
# These endpoints have been extracted into routers/ modules (Patch 126).
# They are mounted above via app.include_router().
# ─────────────────────────────────────────────────────────────────────────────

# (placeholder — keep this comment so the section marker is searchable)
_ROUTERS_MOUNTED = True  # memecoins, wallet, tiers, portfolio




# ---------------------------------------------------------------------------
# SPA catch-all — serve index.html for all non-API paths (React Router)
# Must be defined LAST so it doesn't shadow any /api/ or /ws/ routes.
# ---------------------------------------------------------------------------

@app.get("/{full_path:path}", include_in_schema=False)
async def spa_fallback(full_path: str):
    index = os.path.join(_DIST, "index.html")
    if os.path.isfile(index):
        # no-store: browser must re-fetch index.html every time so it always
        # picks up the latest content-hashed JS/CSS bundle after a deploy.
        return FileResponse(
            index,
            media_type="text/html",
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate",
                "Pragma":        "no-cache",
                "Expires":       "0",
            },
        )
    return JSONResponse({"detail": "Not Found"}, status_code=404)
