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

log = logging.getLogger("dashboard")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")


# ---------------------------------------------------------------------------
# App lifecycle — start signal poller on startup
# ---------------------------------------------------------------------------

async def _perp_monitor_loop():
    """Background: check open perp positions every 60s and close on stop/TP/time."""
    import sys
    root = _engine_root()
    if root not in sys.path:
        sys.path.insert(0, root)
    while True:
        try:
            from utils.perp_executor import perp_monitor_step  # type: ignore
            await perp_monitor_step()
        except Exception as _e:
            log.debug("perp_monitor_step error: %s", _e)
        await asyncio.sleep(60)


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
            log.debug("scalp_monitor_step error: %s", _e)
        await asyncio.sleep(5)


async def _scalp_signal_scan_loop():
    """Background: auto-fire paper scalp perp trades every 30s on SOL/BTC/ETH.

    Uses real 5-minute price movement from CoinGecko market_chart endpoint
    rather than the 24h/6 proxy used by the swing scanner.
    High frequency → many trades → rapid learning data accumulation.
    """
    import sys
    root = _engine_root()
    if root not in sys.path:
        sys.path.insert(0, root)

    _CG_IDS = {"SOL": "solana", "BTC": "bitcoin", "ETH": "ethereum"}

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

            for symbol, cg_id in _CG_IDS.items():
                try:
                    # Fetch ~60 min of minutely price data — days=0.042 ≈ 60 minutes
                    r = _req.get(
                        f"https://api.coingecko.com/api/v3/coins/{cg_id}/market_chart"
                        f"?vs_currency=usd&days=0.042&interval=minutely",
                        timeout=8,
                    )
                    chart = r.json()
                    prices = chart.get("prices", [])  # [[timestamp_ms, price], ...]

                    if len(prices) < 6:
                        log.debug("scalp_scan: not enough data for %s (%d points)", symbol, len(prices))
                        continue

                    price_now = float(prices[-1][1])
                    price_5m  = float(prices[-6][1])   # ~5 minutes ago

                    if price_5m <= 0:
                        continue

                    chg_5m = (price_now - price_5m) / price_5m * 100  # signed %

                    if chg_5m > threshold:
                        await execute_perp_signal({
                            "symbol": symbol, "side": "LONG",
                            "regime_label": phase or "SCALP", "source": "scalp",
                        })
                        log.info(
                            "[SCALP SCAN] LONG %s  5m=+%.3f%%  threshold=%.2f%%",
                            symbol, chg_5m, threshold,
                        )
                    elif chg_5m < -threshold:
                        await execute_perp_signal({
                            "symbol": symbol, "side": "SHORT",
                            "regime_label": phase or "SCALP", "source": "scalp",
                        })
                        log.info(
                            "[SCALP SCAN] SHORT %s  5m=%.3f%%  threshold=%.2f%%",
                            symbol, chg_5m, threshold,
                        )

                except Exception as sym_e:
                    log.debug("scalp_scan %s error: %s", symbol, sym_e)

        except Exception as _e:
            log.debug("scalp_signal_scan error: %s", _e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    task_poller      = asyncio.create_task(signal_poller())
    task_tracker     = asyncio.create_task(outcome_tracker_loop())
    task_perp_mon    = asyncio.create_task(_perp_monitor_loop())
    task_perp_scan   = asyncio.create_task(_perp_signal_scan_loop())
    task_scalp_mon   = asyncio.create_task(_scalp_monitor_loop())
    task_scalp_scan  = asyncio.create_task(_scalp_signal_scan_loop())
    log.info("Dashboard started — swing perp monitor + scalp monitor + signal poller running.")
    yield
    all_tasks = (task_poller, task_tracker, task_perp_mon, task_perp_scan,
                 task_scalp_mon, task_scalp_scan)
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


# ---------------------------------------------------------------------------
# SPA catch-all — serve index.html for all non-API paths (React Router)
# Must be defined LAST so it doesn't shadow any /api/ or /ws/ routes.
# ---------------------------------------------------------------------------

@app.get("/{full_path:path}", include_in_schema=False)
async def spa_fallback(full_path: str):
    index = os.path.join(_DIST, "index.html")
    if os.path.isfile(index):
        return FileResponse(index, media_type="text/html")
    return JSONResponse({"detail": "Not Found"}, status_code=404)
