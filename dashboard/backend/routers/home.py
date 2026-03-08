"""
Home Summary endpoint — Patch 140

Routes:
  GET /api/home/summary  — compact status for all 4 systems in one call
"""
from __future__ import annotations

import json
import logging
import os

from fastapi import APIRouter, Depends

from auth import get_current_user

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/home", tags=["home"])


@router.get("/summary")
def get_home_summary(_user=Depends(get_current_user)):
    """
    Single endpoint for the HOME tab.
    Returns compact status for: Perp Tiers, Memecoins, Spot, Whale Watch.
    """
    import sys
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
    if root not in sys.path:
        sys.path.insert(0, root)

    from utils.db import get_conn

    result = {
        "tiers":       _tiers_summary(root),
        "memecoins":   _memecoins_summary(),
        "spot":        _spot_summary(),
        "whale_watch": _whale_summary(),
    }
    return result


def _tiers_summary(root: str) -> dict:
    try:
        from utils.db import get_conn
        from utils.tier_manager import get_profit_buffer  # type: ignore
        import sqlite3

        db_path = os.path.join(root, "data_storage", "engine.db")
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row

        positions = conn.execute("""
            SELECT symbol, collateral_usd, notes
            FROM perp_positions
            WHERE status='OPEN' AND notes LIKE '%TIER%'
        """).fetchall()

        collateral = sum(float(p["collateral_usd"] or 0) for p in positions)
        buffer_usd = get_profit_buffer(conn)

        # Count TP cycles from kv_store
        row = conn.execute(
            "SELECT value FROM kv_store WHERE key='tier_tp_cycles'"
        ).fetchone()
        tp_cycles = int(row["value"]) if row else 0

        conn.close()
        return {
            "mode":          "LIVE" if os.getenv("PERP_DRY_RUN", "true").lower() == "false" else "SIM",
            "positions":     len(positions),
            "collateral_usd": round(collateral, 2),
            "buffer_usd":    round(buffer_usd, 2),
            "tp_cycles":     tp_cycles,
        }
    except Exception as e:
        log.debug("tiers_summary error: %s", e)
        return {"mode": "?", "positions": 0, "collateral_usd": 0, "buffer_usd": 0, "tp_cycles": 0}


def _memecoins_summary() -> dict:
    try:
        from utils.db import get_conn
        with get_conn() as conn:
            # Outcome count
            complete = conn.execute(
                "SELECT COUNT(*) FROM memecoin_signal_outcomes WHERE status='COMPLETE'"
            ).fetchone()[0]

            # GOOD bucket win rate (24h)
            wr_row = conn.execute("""
                SELECT ROUND(AVG(CASE WHEN return_24h_pct > 0 THEN 1.0 ELSE 0.0 END)*100, 1)
                FROM memecoin_signal_outcomes
                WHERE status='COMPLETE' AND rug_label='GOOD'
            """).fetchone()
            wr = wr_row[0] if wr_row and wr_row[0] is not None else None

            # F&G from kv_store
            fg_row = conn.execute(
                "SELECT value FROM kv_store WHERE key='shared_fear_greed'"
            ).fetchone()
            fg_val = None
            if fg_row:
                try:
                    fg_val = json.loads(fg_row["value"]).get("value")
                except Exception:
                    pass

            # Next milestone (same ladder as memecoins router)
            if complete >= 1000:
                next_ms = ((complete // 500) + 1) * 500
            elif complete >= 500:
                next_ms = 1000
            elif complete >= 200:
                next_ms = 500
            elif complete >= 50:
                next_ms = 200
            elif complete >= 20:
                next_ms = 50
            else:
                next_ms = 20

        return {
            "mode":          "PAPER" if os.getenv("MEMECOIN_DRY_RUN", "true").lower() != "false" else "LIVE",
            "outcomes":      complete,
            "next_milestone": next_ms,
            "wr_pct":        wr,
            "fg_value":      fg_val,
            "fg_ok":         fg_val is not None and fg_val > 25,
        }
    except Exception as e:
        log.debug("memecoins_summary error: %s", e)
        return {"mode": "?", "outcomes": 0, "next_milestone": 20, "wr_pct": None, "fg_value": None, "fg_ok": False}


def _spot_summary() -> dict:
    try:
        from utils.db import get_conn
        with get_conn() as conn:
            # Count spot signal outcomes
            outcomes = conn.execute(
                "SELECT COUNT(*) FROM spot_signal_outcomes WHERE status='COMPLETE'"
            ).fetchone()
            outcome_count = outcomes[0] if outcomes else 0

            # Live buys
            live_row = conn.execute(
                "SELECT COUNT(*) FROM spot_buys WHERE status='ACTIVE'"
            ).fetchone()
            live_buys = live_row[0] if live_row else 0

        return {
            "mode":         "PAPER",  # spot live is manual — always shown as advisory
            "outcomes":     outcome_count,
            "live_buys":    live_buys,
            "basket_size":  11,       # fixed basket of 11 tokens (WIF/BONK/etc.)
        }
    except Exception as e:
        log.debug("spot_summary error: %s", e)
        return {"mode": "PAPER", "outcomes": 0, "live_buys": 0, "basket_size": 11}


def _whale_summary() -> dict:
    try:
        from utils.db import get_conn
        with get_conn() as conn:
            # Check table exists first
            tbl = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='whale_watch_alerts'"
            ).fetchone()
            if not tbl:
                return {"total": 0, "in_range": 0, "scanner_pass": 0, "alerts_sent": 0, "last_ts": None}

            total    = conn.execute("SELECT COUNT(*) FROM whale_watch_alerts").fetchone()[0]
            in_range = conn.execute("SELECT COUNT(*) FROM whale_watch_alerts WHERE mc_in_range=1").fetchone()[0]
            passed   = conn.execute("SELECT COUNT(*) FROM whale_watch_alerts WHERE scanner_pass=1").fetchone()[0]
            sent     = conn.execute("SELECT COUNT(*) FROM whale_watch_alerts WHERE alert_sent=1").fetchone()[0]
            last_row = conn.execute("SELECT ts_utc FROM whale_watch_alerts ORDER BY id DESC LIMIT 1").fetchone()
            last_ts  = last_row[0] if last_row else None

        return {
            "total":        total,
            "in_range":     in_range,
            "scanner_pass": passed,
            "alerts_sent":  sent,
            "last_ts":      last_ts,
        }
    except Exception as e:
        log.debug("whale_summary error: %s", e)
        return {"total": 0, "in_range": 0, "scanner_pass": 0, "alerts_sent": 0, "last_ts": None}
