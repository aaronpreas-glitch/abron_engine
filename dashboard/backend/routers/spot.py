"""
Spot Accumulation endpoints — Patch 128 + Patch 134.

Routes:
  GET  /api/spot/status        — holdings + live prices + PnL + basket config
  GET  /api/spot/advice        — allocation advice for ?amount=N budget
  GET  /api/spot/history       — last 50 transactions from spot_buys audit log
  GET  /api/spot/signals       — per-token DCA signal scores + learning progress (Patch 134)
  GET  /api/spot/analytics     — signal performance breakdowns + history (Patch 134)
  POST /api/spot/buy           — buy {symbol, mint, amount_usd}
  POST /api/spot/sell/{symbol} — sell position (manual, optional ?pct=50)
"""
from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Query

from auth import get_current_user
from routers._shared import _ensure_engine_path

log    = logging.getLogger("dashboard")
router = APIRouter(prefix="/api/spot", tags=["spot"])


@router.get("/status")
async def spot_status_ep(_: str = Depends(get_current_user)):
    """Holdings + live prices + PnL + basket config."""
    _ensure_engine_path()
    try:
        from utils.spot_accumulator import get_portfolio_state  # type: ignore
        return await asyncio.to_thread(get_portfolio_state)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/advice")
async def spot_advice_ep(
    amount: float = Query(..., gt=0, description="Budget in USD"),
    _: str = Depends(get_current_user),
):
    """Allocation advice — how to split $amount across underweight basket positions."""
    _ensure_engine_path()
    try:
        from utils.spot_accumulator import get_allocation_advice  # type: ignore
        advice = await asyncio.to_thread(get_allocation_advice, amount)
        return {"amount_usd": amount, "advice": advice}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/history")
async def spot_history_ep(
    limit: int = Query(default=50, ge=1, le=200, description="Max rows to return"),
    _: str = Depends(get_current_user),
):
    """Last N transactions from the spot_buys audit log."""
    _ensure_engine_path()
    try:
        from utils.db import get_conn  # type: ignore
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT id, ts_utc, symbol, side, amount_usd, token_amount, "
                "price_usd, tx_sig, dry_run FROM spot_buys "
                "ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return {"transactions": [dict(r) for r in rows]}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/buy")
async def spot_buy_ep(body: dict, _: str = Depends(get_current_user)):
    """Buy a basket token. Body: {symbol, mint, amount_usd}."""
    _ensure_engine_path()
    symbol     = str(body.get("symbol", "")).strip().upper()
    mint       = str(body.get("mint",   "")).strip()
    amount_usd = float(body.get("amount_usd", 0))

    if not symbol or not mint:
        raise HTTPException(status_code=400, detail="symbol and mint are required")
    if amount_usd < 1:
        raise HTTPException(status_code=400, detail="amount_usd must be >= 1")

    try:
        from utils.spot_accumulator import buy_spot  # type: ignore
        result = await asyncio.to_thread(buy_spot, symbol, mint, amount_usd)
        if not result.get("success"):
            raise HTTPException(status_code=422, detail=result.get("error", "buy failed"))
        return result
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/signals")
async def spot_signals_ep(_: str = Depends(get_current_user)):
    """
    Per-token DCA signal scores + learning loop analytics. Patch 134.
    Reads cached scores from kv_store (written hourly by signal engine).
    """
    _ensure_engine_path()
    try:
        from utils.db import get_conn  # type: ignore

        # ── Score cache ───────────────────────────────────────────────────────
        signals: dict = {}
        signals_updated_at: str | None = None
        try:
            with get_conn() as conn:
                row = conn.execute(
                    "SELECT value FROM kv_store WHERE key='spot_current_signals'"
                ).fetchone()
            if row:
                cached = json.loads(row[0])
                signals = cached.get("data") or {}
                signals_updated_at = cached.get("updated_at")
        except Exception:
            pass

        # ── Tuner thresholds ──────────────────────────────────────────────────
        tuner_data: dict = {}
        try:
            with get_conn() as conn:
                row = conn.execute(
                    "SELECT value FROM kv_store WHERE key='spot_signal_thresholds'"
                ).fetchone()
            if row:
                tuner_data = json.loads(row[0])
        except Exception:
            pass

        # ── Learning analytics ────────────────────────────────────────────────
        total    = 0
        complete = 0
        try:
            with get_conn() as conn:
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS spot_signals ("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT,"
                    "ts_utc TEXT, symbol TEXT, score REAL, signal_type TEXT,"
                    "price_at_signal REAL, h24_at_signal REAL, h6_at_signal REAL,"
                    "fg_at_signal INTEGER, trend_at_signal TEXT, portfolio_gap REAL,"
                    "price_7d REAL, return_7d_pct REAL, outcome_7d_ts TEXT,"
                    "price_30d REAL, return_30d_pct REAL, outcome_30d_ts TEXT,"
                    "status TEXT DEFAULT 'PENDING')"
                )
                total    = conn.execute("SELECT COUNT(*) FROM spot_signals").fetchone()[0]
                complete = conn.execute(
                    "SELECT COUNT(*) FROM spot_signals WHERE status='COMPLETE'"
                ).fetchone()[0]
        except Exception:
            pass

        # Milestone ladder: 10→20→50→100, then perpetual +50 increments forever.
        # Spot outcomes take 7 days each — ladder is scaled accordingly (vs memecoin's +500).
        if complete >= 100:
            tuner_threshold = ((complete // 50) + 1) * 50  # perpetual: next 50 boundary
        elif complete >= 50:
            tuner_threshold = 100
        elif complete >= 20:
            tuner_threshold = 50
        elif complete >= 10:
            tuner_threshold = 20
        else:
            tuner_threshold = 10

        complete_pct = round(min(complete / tuner_threshold * 100, 100.0), 1)

        confidence   = tuner_data.get("confidence", "pending")
        min_score    = tuner_data.get("min_score", 3)

        return {
            "signals": signals,
            "signals_updated_at": signals_updated_at,
            "learning": {
                "total":            total,
                "complete":         complete,
                "tuner_threshold":  tuner_threshold,
                "complete_pct":     complete_pct,
                "confidence":       confidence,
                "min_score":        min_score,
                "win_rate":         tuner_data.get("win_rate"),
                "sample_size":      tuner_data.get("sample_size"),
            },
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/analytics")
async def spot_analytics_ep(_: str = Depends(get_current_user)):
    """
    Signal outcome analytics — breakdowns by signal type, F&G bucket, and token.
    Plus tuner output and recent signal history. Patch 134.
    """
    _ensure_engine_path()

    def _run():
        import sqlite3 as _sqlite3
        from utils.db import get_conn  # type: ignore

        # Ensure table exists (idempotent)
        _CREATE = (
            "CREATE TABLE IF NOT EXISTS spot_signals ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, ts_utc TEXT, symbol TEXT, "
            "score REAL, signal_type TEXT, price_at_signal REAL, h24_at_signal REAL, "
            "h6_at_signal REAL, fg_at_signal INTEGER, trend_at_signal TEXT, portfolio_gap REAL, "
            "price_7d REAL, return_7d_pct REAL, outcome_7d_ts TEXT, "
            "price_30d REAL, return_30d_pct REAL, outcome_30d_ts TEXT, "
            "status TEXT DEFAULT 'PENDING')"
        )
        with get_conn() as conn:
            conn.execute(_CREATE)
            total    = conn.execute("SELECT COUNT(*) FROM spot_signals").fetchone()[0]
            complete = conn.execute(
                "SELECT COUNT(*) FROM spot_signals WHERE status='COMPLETE'"
            ).fetchone()[0]
            pending  = total - complete

        # Load completed rows for analytics
        with get_conn() as conn:
            conn.row_factory = _sqlite3.Row
            rows = conn.execute("""
                SELECT symbol, score, signal_type, fg_at_signal, h24_at_signal,
                       trend_at_signal, return_7d_pct, return_30d_pct
                FROM spot_signals
                WHERE status='COMPLETE' AND return_7d_pct IS NOT NULL
            """).fetchall()
        rows = [dict(r) for r in rows]

        def _stats(subset: list) -> dict:
            if not subset:
                return {"count": 0, "win_rate_7d": None, "avg_return_7d": None}
            wins = sum(1 for r in subset if (r.get("return_7d_pct") or 0) > 0)
            avg  = sum((r.get("return_7d_pct") or 0) for r in subset) / len(subset)
            return {
                "count":        len(subset),
                "win_rate_7d":  round(wins / len(subset) * 100, 1),
                "avg_return_7d": round(avg, 2),
            }

        # ── Signal type breakdown ─────────────────────────────────────────────
        signal_breakdown = []
        for stype in ("DCA_NOW", "WATCH"):
            subset = [r for r in rows if r["signal_type"] == stype]
            signal_breakdown.append({"label": stype, **_stats(subset)})

        # ── F&G bucket breakdown ──────────────────────────────────────────────
        fg_buckets = [
            ("<15  XFEAR",    None, 15),
            ("15–25  FEAR",   15,   25),
            ("25–40  CAUTIOUS", 25, 40),
            (">40  NEUTRAL+", 40, None),
        ]
        fg_breakdown = []
        for label, lo, hi in fg_buckets:
            subset = [
                r for r in rows
                if r.get("fg_at_signal") is not None
                and (lo is None or int(r["fg_at_signal"]) >= lo)
                and (hi is None or int(r["fg_at_signal"]) <  hi)
            ]
            fg_breakdown.append({"label": label, **_stats(subset)})

        # ── Token breakdown ───────────────────────────────────────────────────
        from utils.spot_accumulator import BASKET  # type: ignore
        token_breakdown = []
        for token in BASKET:
            sym    = token["symbol"]
            subset = [r for r in rows if r["symbol"] == sym]
            token_breakdown.append({"symbol": sym, **_stats(subset)})

        # ── Tuner thresholds ──────────────────────────────────────────────────
        tuner: dict | None = None
        try:
            with get_conn() as conn:
                row = conn.execute(
                    "SELECT value FROM kv_store WHERE key='spot_signal_thresholds'"
                ).fetchone()
            if row:
                tuner = json.loads(row[0])
        except Exception:
            pass

        # ── Recent signal history (last 30, newest first) ────────────────────
        recent: list[dict] = []
        try:
            with get_conn() as conn:
                conn.row_factory = _sqlite3.Row
                rrows = conn.execute("""
                    SELECT id, ts_utc, symbol, score, signal_type,
                           price_at_signal, h24_at_signal, h6_at_signal,
                           fg_at_signal, trend_at_signal, portfolio_gap,
                           return_7d_pct, return_30d_pct, status
                    FROM spot_signals
                    ORDER BY id DESC LIMIT 30
                """).fetchall()
            recent = [dict(r) for r in rrows]
        except Exception:
            pass

        return {
            "total":            total,
            "complete":         complete,
            "pending":          pending,
            "signal_breakdown": signal_breakdown,
            "fg_breakdown":     fg_breakdown,
            "token_breakdown":  token_breakdown,
            "tuner":            tuner,
            "recent_signals":   recent,
        }

    try:
        return await asyncio.to_thread(_run)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/sell/{symbol}")
async def spot_sell_ep(
    symbol: str,
    pct: float = Query(default=100.0, ge=1, le=100, description="Percentage to sell (1–100)"),
    _: str = Depends(get_current_user),
):
    """Sell pct% of a basket token holding (default 100 = full sell)."""
    _ensure_engine_path()
    symbol = symbol.upper()

    try:
        from utils.spot_accumulator import BASKET, sell_spot  # type: ignore
        # Look up mint from basket
        mint = next((b["mint"] for b in BASKET if b["symbol"] == symbol), None)
        if not mint:
            raise HTTPException(status_code=404, detail=f"{symbol} not in basket")

        result = await asyncio.to_thread(sell_spot, symbol, mint, pct)
        if not result.get("success"):
            raise HTTPException(status_code=422, detail=result.get("error", "sell failed"))
        return result
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
