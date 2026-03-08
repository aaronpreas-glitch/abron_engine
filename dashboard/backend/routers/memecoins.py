"""
Memecoin scanner endpoints — Patches 115, 116, 117, 125.

Routes:
  GET  /api/memecoins/status     — scanner signals + open positions + stats
  POST /api/memecoins/buy        — buy a memecoin by mint address
  POST /api/memecoins/sell/{mint} — sell an open position
  GET  /api/memecoins/analytics  — score buckets, rug breakdown, tuner progress
"""
from __future__ import annotations

import asyncio
import logging
import os
import sqlite3

from fastapi import APIRouter, Depends, HTTPException

from auth import get_current_user
from routers._shared import _ensure_engine_path, _db_path

log = logging.getLogger("dashboard")
router = APIRouter(prefix="/api/memecoins", tags=["memecoins"])


@router.get("/status")
async def memecoins_status_ep(_: str = Depends(get_current_user)):
    """Scanner signals + open positions + stats."""
    _ensure_engine_path()
    try:
        from utils.memecoin_manager import memecoin_status as _ms  # type: ignore
        return await asyncio.to_thread(_ms)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/buy")
async def memecoins_buy_ep(body: dict, _: str = Depends(get_current_user)):
    """Buy a memecoin by mint address."""
    _ensure_engine_path()
    mint       = str(body.get("mint",       "")).strip()
    symbol     = str(body.get("symbol",     "")).strip().upper()
    amount_usd = float(body.get("amount_usd", 10))
    if not mint or not symbol:
        raise HTTPException(status_code=400, detail="mint and symbol required")
    try:
        from utils.memecoin_manager import buy_memecoin as _bm  # type: ignore
        return await asyncio.to_thread(_bm, mint, symbol, amount_usd)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/sell/{mint}")
async def memecoins_sell_ep(mint: str, _: str = Depends(get_current_user)):
    """Sell an open memecoin position by mint address."""
    _ensure_engine_path()
    try:
        from utils.memecoin_manager import sell_memecoin as _sm  # type: ignore
        return await asyncio.to_thread(_sm, mint, "MANUAL")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/trending")
async def memecoins_trending_ep(_: str = Depends(get_current_user)):
    """
    Narrative momentum trending data — CoinGecko + DexScreener Solana boosted.
    Returns cached payload refreshed every 4h by the research loop. (Patch 127)
    """
    _ensure_engine_path()
    try:
        from utils.narrative_momentum import get_narrative_data  # type: ignore
        data = get_narrative_data()
        return data or {"coingecko": [], "dexscreener": [], "updated_at": None}
    except Exception as exc:
        return {"coingecko": [], "dexscreener": [], "updated_at": None, "error": str(exc)}


@router.get("/analytics")
async def memecoins_analytics_ep(_: str = Depends(get_current_user)):
    """
    Signal outcome analytics — score buckets, rug label breakdown, win rates,
    avg returns, and auto-buy config with tuner progress. (Patches 116+117+125)
    """
    _db = _db_path()

    def _run():
        c = sqlite3.connect(str(_db))
        c.row_factory = sqlite3.Row

        total    = c.execute("SELECT COUNT(*) FROM memecoin_signal_outcomes").fetchone()[0]
        complete = c.execute(
            "SELECT COUNT(*) FROM memecoin_signal_outcomes WHERE status='COMPLETE'"
        ).fetchone()[0]
        bought   = c.execute(
            "SELECT COUNT(*) FROM memecoin_signal_outcomes WHERE bought=1"
        ).fetchone()[0]

        # Score buckets
        buckets = []
        for label, lo, hi in [("70+", 70, 101), ("50\u201369", 50, 70), ("<50", 0, 50)]:
            rows = c.execute("""
                SELECT return_1h_pct, return_4h_pct, return_24h_pct, bought
                FROM memecoin_signal_outcomes
                WHERE score >= ? AND score < ? AND status = 'COMPLETE'
            """, (lo, hi)).fetchall()

            if not rows:
                buckets.append({
                    "label": label, "count": 0, "win_rate_4h": None,
                    "avg_return_1h": None, "avg_return_4h": None,
                    "avg_return_24h": None, "buy_rate": None,
                })
                continue

            r1   = [float(r["return_1h_pct"])  for r in rows if r["return_1h_pct"]  is not None]
            r4   = [float(r["return_4h_pct"])  for r in rows if r["return_4h_pct"]  is not None]
            r24  = [float(r["return_24h_pct"]) for r in rows if r["return_24h_pct"] is not None]
            win4 = sum(1 for x in r4 if x > 0)
            buckets.append({
                "label":          label,
                "count":          len(rows),
                "win_rate_4h":    round(win4 / len(r4) * 100, 1) if r4 else None,
                "avg_return_1h":  round(sum(r1)  / len(r1),  2)  if r1  else None,
                "avg_return_4h":  round(sum(r4)  / len(r4),  2)  if r4  else None,
                "avg_return_24h": round(sum(r24) / len(r24), 2)  if r24 else None,
                "buy_rate":       round(sum(1 for r in rows if r["bought"]) / len(rows) * 100, 1),
            })

        # Rug label breakdown (Patch 117)
        rug_breakdown = []
        for rug_label in ("GOOD", "WARN", "UNKNOWN"):
            rows = c.execute("""
                SELECT return_4h_pct, return_24h_pct, bought
                FROM memecoin_signal_outcomes
                WHERE rug_label = ? AND status = 'COMPLETE'
            """, (rug_label,)).fetchall()
            if rows:
                r4  = [float(r["return_4h_pct"]) for r in rows if r["return_4h_pct"] is not None]
                win = sum(1 for x in r4 if x > 0)
                rug_breakdown.append({
                    "label":         rug_label,
                    "count":         len(rows),
                    "win_rate_4h":   round(win / len(r4) * 100, 1) if r4 else None,
                    "avg_return_4h": round(sum(r4) / len(r4), 2)   if r4 else None,
                })

        # Best 10 by 4h return
        top = c.execute("""
            SELECT symbol, mint, score, rug_label, mcap_at_scan,
                   token_age_days, vol_acceleration, top_holder_pct,
                   return_1h_pct, return_4h_pct, return_24h_pct, bought, scanned_at
            FROM memecoin_signal_outcomes
            WHERE return_4h_pct IS NOT NULL
            ORDER BY return_4h_pct DESC
            LIMIT 10
        """).fetchall()

        # Learned thresholds
        lt_row = c.execute(
            "SELECT value FROM kv_store WHERE key='memecoin_learned_thresholds'"
        ).fetchone()
        learned = None
        if lt_row:
            try:
                import json as _j
                learned = _j.loads(lt_row["value"])
            except Exception:
                pass

        # Auto-buy config + tuner progress (Patch 125, updated Patch 138)
        # Milestone ladder: 20→50→200→500→1000, then perpetual +500 increments forever.
        # Progress bar always advances — learning never stops.
        if complete >= 1000:
            _tuner_needed = ((complete // 500) + 1) * 500  # perpetual: next 500 boundary
        elif complete >= 500:
            _tuner_needed = 1000
        elif complete >= 200:
            _tuner_needed = 500
        elif complete >= 50:
            _tuner_needed = 200
        elif complete >= 20:
            _tuner_needed = 50
        else:
            _tuner_needed = 20
        auto_buy = {
            "enabled":         os.getenv("MEMECOIN_AUTO_BUY",    "false").lower() == "true",
            "dry_run":         os.getenv("MEMECOIN_DRY_RUN",     "true").lower()  == "true",
            "score_min":       float(os.getenv("MEMECOIN_BUY_SCORE_MIN", "65")),
            "max_open":        int(os.getenv("MEMECOIN_MAX_OPEN", "3")),
            "buy_usd":         float(os.getenv("MEMECOIN_BUY_USD", "15")),
            "tuner_threshold": _tuner_needed,
            "complete_pct":    round(min(complete / _tuner_needed * 100, 100.0), 1),
        }

        c.close()
        return {
            "total_tracked":      total,
            "complete":           complete,
            "pending":            total - complete,
            "bought_count":       bought,
            "score_buckets":      buckets,
            "rug_breakdown":      rug_breakdown,
            "top_performers":     [dict(r) for r in top],
            "learned_thresholds": learned,
            "auto_buy":           auto_buy,
        }

    try:
        return await asyncio.to_thread(_run)
    except Exception as exc:
        log.warning("memecoins_analytics_ep error: %s", exc)
        return {
            "total_tracked": 0, "complete": 0, "pending": 0, "bought_count": 0,
            "score_buckets": [], "rug_breakdown": [], "top_performers": [],
            "learned_thresholds": None,
        }
