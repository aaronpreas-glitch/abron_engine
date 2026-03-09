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


# ── Patch 190: Next Best Move — unified cross-system recommendation ────────────

@router.get("/next-best-move")
def get_next_best_move(_user=Depends(get_current_user)):
    """
    Cross-system 'what to do next' recommendation. P190.
    Aggregates PERP buffer health, MEMECOIN gate state, and SPOT portfolio gap
    into a single ranked action + 2 alternatives.
    Decision support only — no auto-trading changes.

    Actions: MANAGE (urgent) > BUY (meme signal) > DCA (spot gap) >
             WATCH (gates open, no signal) > WAIT (gate closed) > HOLD (nothing to do)
    """
    import sys
    import json as _j
    from datetime import datetime, timezone

    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
    if root not in sys.path:
        sys.path.insert(0, root)

    from utils.db import get_conn  # type: ignore

    candidates = []

    # ── 1. Perp — profit buffer health ────────────────────────────────────────
    try:
        import sqlite3
        _db = os.path.join(root, "data_storage", "engine.db")
        _cp = sqlite3.connect(_db)
        _cp.row_factory = sqlite3.Row
        from utils.tier_manager import get_profit_buffer  # type: ignore
        _buf  = get_profit_buffer(_cp)
        _npos = _cp.execute(
            "SELECT COUNT(*) FROM perp_positions WHERE status='OPEN' AND notes LIKE '%TIER%'"
        ).fetchone()[0]
        _cp.close()

        if _buf < 0:
            candidates.append({
                "_rank": 100, "action": "MANAGE", "system": "PERP", "symbol": None,
                "priority": "URGENT",
                "reason": (
                    f"Profit buffer is negative (${_buf:.0f}). "
                    "Check open positions — consider reducing exposure or adding collateral."
                ),
                "blockers": [f"BUFFER=${_buf:.0f}"], "confidence": "high",
            })
        elif _npos > 0:
            candidates.append({
                "_rank": 5, "action": "HOLD", "system": "PERP", "symbol": None,
                "priority": "LOW",
                "reason": f"{_npos} open position(s), buffer=${_buf:.0f}. Positions healthy — hold.",
                "blockers": [], "confidence": "high",
            })
    except Exception as exc:
        log.debug("next_best_move perp: %s", exc)

    # ── 2. Memecoin — gate state ───────────────────────────────────────────────
    try:
        _auto_buy = os.getenv("MEMECOIN_AUTO_BUY", "false").lower() == "true"
        _dry_run  = os.getenv("MEMECOIN_DRY_RUN",  "true").lower()  == "true"
        _max_open = int(os.getenv("MEMECOIN_MAX_OPEN", "3"))
        _fg_val    = None
        _open_cnt  = 0
        _bands     = []
        _multi     = False

        with get_conn() as _cm:
            _fg_row = _cm.execute(
                "SELECT value FROM kv_store WHERE key='shared_fear_greed'"
            ).fetchone()
            if _fg_row:
                try:
                    _fg_val = _j.loads(_fg_row[0]).get("value")
                except Exception:
                    pass

            _open_cnt = _cm.execute(
                "SELECT COUNT(DISTINCT token_mint) FROM memecoin_signal_outcomes WHERE status='OPEN'"
            ).fetchone()[0]

            _lt_row = _cm.execute(
                "SELECT value FROM kv_store WHERE key='memecoin_learned_thresholds'"
            ).fetchone()
            if _lt_row:
                try:
                    _lt    = _j.loads(_lt_row[0])
                    _bands = _lt.get("bands", [])
                    _multi = bool(_lt.get("multi_band_mode", False))
                except Exception:
                    pass

        _fg_thr = 35 if not _dry_run else 25
        _fg_ok  = _fg_val is not None and _fg_val > _fg_thr
        _cap_ok = _open_cnt < _max_open

        _m_blk = []
        if not _auto_buy:
            _m_blk.append("AUTO_BUY=false")
        if not _fg_ok:
            _m_blk.append(f"F&G={_fg_val or '?'} (need >{_fg_thr})")
        if not _cap_ok:
            _m_blk.append(f"CAPACITY {_open_cnt}/{_max_open}")

        _best_sig = None
        try:
            from utils.memecoin_scanner import get_cached_signals  # type: ignore
            _sigs = sorted(
                get_cached_signals(), key=lambda s: s.get("score", 0), reverse=True
            )
            if _sigs:
                _s0 = _sigs[0]
                _sc = _s0.get("score", 0)
                if _multi and _bands:
                    _in = any(b["lo"] <= _sc < b["hi"] for b in _bands)
                else:
                    _in = _sc >= float(os.getenv("MEMECOIN_BUY_SCORE_MIN", "65"))
                if _in:
                    _best_sig = _s0
        except Exception:
            pass

        if not _m_blk and _best_sig:
            candidates.append({
                "_rank": 60, "action": "BUY", "system": "MEMECOINS",
                "symbol": _best_sig.get("symbol"), "priority": "NORMAL",
                "reason": (
                    f"All gates pass — {_best_sig.get('symbol')} "
                    f"score={_best_sig.get('score', 0):.0f} in active band. Auto-buy would fire."
                ),
                "blockers": [], "confidence": "high",
            })
        elif not _m_blk:
            candidates.append({
                "_rank": 20, "action": "WATCH", "system": "MEMECOINS", "symbol": None,
                "priority": "LOW",
                "reason": "All system gates open — no signal in active band right now. Check at next scan.",
                "blockers": [], "confidence": "medium",
            })
        elif not _fg_ok:
            _bstr = ""
            if _bands:
                _bstr = " Active bands: " + " + ".join(
                    f"{b['lo']}-{b['hi']}" for b in _bands[:3]
                ) + "."
            candidates.append({
                "_rank": 15, "action": "WAIT", "system": "MEMECOINS", "symbol": None,
                "priority": "LOW",
                "reason": (
                    f"F&G={_fg_val or '?'} — below {'pilot' if not _dry_run else 'paper'} "
                    f"gate (>{_fg_thr}). Extreme fear — wait for recovery.{_bstr}"
                ),
                "blockers": _m_blk, "confidence": "high",
            })
        elif not _auto_buy:
            candidates.append({
                "_rank": 8, "action": "WAIT", "system": "MEMECOINS", "symbol": None,
                "priority": "LOW",
                "reason": "MEMECOIN_AUTO_BUY=false — scanner in advisory mode, no buys execute.",
                "blockers": _m_blk, "confidence": "high",
            })
        else:
            candidates.append({
                "_rank": 10, "action": "HOLD", "system": "MEMECOINS", "symbol": None,
                "priority": "LOW",
                "reason": f"Position capacity full ({_open_cnt}/{_max_open}). Wait for resolutions.",
                "blockers": _m_blk, "confidence": "high",
            })
    except Exception as exc:
        log.debug("next_best_move meme: %s", exc)

    # ── 3. Spot — portfolio gap ────────────────────────────────────────────────
    try:
        with get_conn() as _cs:
            _sr = _cs.execute(
                "SELECT value FROM kv_store WHERE key='spot_current_signals'"
            ).fetchone()
        if _sr:
            _spot_map = _j.loads(_sr[0])
            _dca = sorted(
                [(sym, d) for sym, d in _spot_map.items() if (d.get("portfolio_gap") or 0) > 0],
                key=lambda x: x[1].get("portfolio_gap", 0), reverse=True,
            )
            if _dca:
                _sym2, _d2 = _dca[0]
                _gap2 = _d2.get("portfolio_gap", 0)
                _sig2 = _d2.get("signal_type", "WATCH")
                candidates.append({
                    "_rank": 35 if _sig2 == "DCA_NOW" else 12,
                    "action": "DCA", "system": "SPOT", "symbol": _sym2,
                    "priority": "NORMAL" if _sig2 == "DCA_NOW" else "LOW",
                    "reason": (
                        f"{_sym2} is {_gap2:+.1f}% under target allocation. "
                        f"Signal: {_sig2}. Manual buy at discretion."
                    ),
                    "blockers": ["MANUAL_ONLY"], "confidence": "medium",
                })
    except Exception as exc:
        log.debug("next_best_move spot: %s", exc)

    # ── Rank + assemble ────────────────────────────────────────────────────────
    candidates.sort(key=lambda c: c["_rank"], reverse=True)
    for c in candidates:
        c.pop("_rank", None)

    no_action = not candidates or candidates[0]["action"] == "HOLD"

    if not candidates:
        best = {
            "action": "HOLD", "system": None, "symbol": None, "priority": "LOW",
            "reason": "All systems healthy — no immediate action required. Monitor positions.",
            "blockers": [], "confidence": "medium",
        }
        alts = []
    else:
        best = candidates[0]
        alts = candidates[1:3]

    return {
        "next_best_move":        best,
        "alternatives":          alts,
        "no_action_recommended": no_action,
        "generated_at":          datetime.now(timezone.utc).isoformat(),
    }
