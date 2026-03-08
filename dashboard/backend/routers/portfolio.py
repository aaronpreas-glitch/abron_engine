"""
Portfolio Watchman, Orchestrator, System Health, and Journal endpoints.
Patches 91, 104, 116, 118, 120, 122.

Routes:
  GET /api/portfolio/signals    — latest hold signal per coin for Portfolio Watchman
  GET /api/orchestrator/status  — health status for all registered agents
  GET /api/orchestrator/memory  — last N lines of MEMORY.md as text block
  GET /api/health/status        — system health watchdog: DB, agents, scan age, F&G
  GET /api/journal/learnings    — aggregated learnings from brain + exits + outcomes
"""
from __future__ import annotations

import asyncio
import logging
import os
import sqlite3

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from auth import get_current_user
from db_read import get_weekly_tuning_report
from config_editor import get_config
from routers._shared import _ensure_engine_path, _db_path

log = logging.getLogger("dashboard")
router = APIRouter(tags=["portfolio"])


@router.get("/api/portfolio/signals")
async def portfolio_signals_ep(_: str = Depends(get_current_user)):
    """Latest hold signal per coin for Portfolio Watchman dashboard section."""
    _db = _db_path()
    try:
        c = sqlite3.connect(str(_db))
        c.row_factory = sqlite3.Row
        rows = c.execute(
            "SELECT * FROM portfolio_signals "
            "WHERE id IN (SELECT MAX(id) FROM portfolio_signals GROUP BY coin) "
            "ORDER BY coin"
        ).fetchall()
        c.close()
        signals = [dict(r) for r in rows]
        # Use live F&G from agent_coordinator (same source as health/status bar)
        # so both widgets always agree — stale DB value caused mismatch
        live_fg = None
        try:
            _ensure_engine_path()
            from utils.agent_coordinator import get_fear_greed as _gfg  # type: ignore
            live_fg = _gfg().get("value")
        except Exception:
            live_fg = signals[0]["fear_greed"] if signals else None

        return {
            "signals":      signals,
            "fear_greed":   live_fg,
            "btc_dom_pct":  signals[0]["btc_dom_pct"]  if signals else None,
            "last_updated": signals[0]["ts_utc"]        if signals else None,
        }
    except Exception as exc:
        log.warning("portfolio_signals_ep error: %s", exc)
        return {"signals": [], "fear_greed": None, "btc_dom_pct": None, "last_updated": None}


@router.get("/api/orchestrator/status")
async def orchestrator_status_ep(_: str = Depends(get_current_user)):
    """Return health status for all registered agents.  # Patch 91+116"""
    from datetime import datetime as _dtos, timezone as _tzos
    _ensure_engine_path()
    from utils import orchestrator as _orch  # type: ignore
    return {"agents": _orch.get_status(), "ts": _dtos.now(_tzos.utc).isoformat() + "Z"}


@router.get("/api/orchestrator/memory")
async def orchestrator_memory_ep(lines: int = 50, _: str = Depends(get_current_user)):
    """Return the last N lines of MEMORY.md as a text block. (Patch 147)"""
    _ensure_engine_path()
    from utils import orchestrator as _orch  # type: ignore
    return {"memory": _orch.read_memory(max(1, min(lines, 500)))}


@router.get("/api/health/status")
async def health_status_ep(_: str = Depends(get_current_user)):
    """System health watchdog status — DB, agents, scan freshness, F&G. (Patches 118+122)"""
    import json as _json
    _ensure_engine_path()
    from utils.db import get_conn as _gc  # type: ignore
    result: dict = {"status": "UNKNOWN", "ts": None, "issues": [], "warnings": [], "db": None}
    try:
        with _gc() as _conn:
            row = _conn.execute("SELECT value FROM kv_store WHERE key='system_health'").fetchone()
            if row:
                result = _json.loads(row[0])
    except Exception:
        pass
    # Patch 122 — enrich with live F&G and auto-buy state
    try:
        from utils.agent_coordinator import get_fear_greed as _gfg  # type: ignore
        fg = _gfg()
        result["fear_greed"] = {
            "value":     fg.get("value"),
            "label":     fg.get("label", "UNKNOWN"),
            "favorable": fg.get("favorable", True),
        }
    except Exception:
        result["fear_greed"] = None
    result["auto_buy_enabled"] = os.getenv("MEMECOIN_AUTO_BUY", "false").lower() == "true"
    return result


@router.get("/api/journal/learnings")
async def journal_learnings(_: str = Depends(get_current_user)):
    """
    Aggregate learnings from brain suggestions, exit strategy, and outcome data
    into a unified feed for the trading journal.
    """
    _db = _db_path()
    learnings: list[dict] = []
    try:
        _ensure_engine_path()

        # 1. Brain suggestions
        try:
            cfg = get_config()
            report = get_weekly_tuning_report(
                lookback_days=14,
                current_alert_threshold=int(cfg.get("ALERT_THRESHOLD", 72)),
                current_regime_min_score=int(cfg.get("REGIME_MIN_SCORE", 35)),
                current_min_confidence_to_alert=str(cfg.get("MIN_CONFIDENCE_TO_ALERT", "B")),
                min_outcomes_4h=3,
            )
            if report.get("recommendations"):
                for rec in report["recommendations"]:
                    learnings.append({
                        "type":     "brain_suggestion",
                        "icon":     "\U0001f9e0",
                        "title":    rec.get("param", "Config"),
                        "detail":   f"Current: {rec.get('current')} \u2192 Suggested: {rec.get('suggested')}. {rec.get('reason', '')}",
                        "priority": "high" if rec.get("impact", 0) > 5 else "medium",
                        "ts":       report.get("generated_at", ""),
                    })
        except Exception:
            pass

        # 2. Exit learnings summary
        try:
            from utils.exit_strategy import get_exit_summary  # type: ignore
            summary = get_exit_summary()
            if summary.get("total_learnings", 0) > 0:
                learnings.append({
                    "type":     "exit_learning",
                    "icon":     "\U0001f4ca",
                    "title":    "Exit Strategy Data",
                    "detail":   (
                        f"{summary.get('total_learnings', 0)} exit profiles learned. "
                        f"Best horizon: {summary.get('best_avg_horizon_h', '?')}h avg."
                    ),
                    "priority": "info",
                    "ts":       "",
                })
        except Exception:
            pass

        # 3. Recent outcome patterns
        try:
            with sqlite3.connect(f"file:{_db}?mode=ro", uri=True) as conn:
                conn.row_factory = sqlite3.Row

                # Check for consistent winners/losers
                rows = conn.execute("""
                    SELECT symbol,
                           COUNT(*) as n,
                           AVG(CASE WHEN pnl_pct > 0 THEN 1.0 ELSE 0.0 END) as wr,
                           AVG(pnl_pct) as avg_pnl
                    FROM perp_positions
                    WHERE status='CLOSED' AND closed_ts_utc > datetime('now', '-7 days')
                    GROUP BY symbol
                    HAVING COUNT(*) >= 2
                    ORDER BY avg_pnl DESC
                """).fetchall()
                for r in rows:
                    wr  = (r["wr"] or 0) * 100
                    avg = r["avg_pnl"] or 0
                    if wr >= 70:
                        learnings.append({
                            "type":     "pattern",
                            "icon":     "\U0001f525",
                            "title":    f"{r['symbol']} \u2014 Strong performer",
                            "detail":   f"{wr:.0f}% win rate over {r['n']} trades, avg {avg:+.2f}%. Consider increasing position size.",
                            "priority": "high",
                            "ts":       "",
                        })
                    elif wr <= 30 and r["n"] >= 3:
                        learnings.append({
                            "type":     "pattern",
                            "icon":     "\u26a0\ufe0f",
                            "title":    f"{r['symbol']} \u2014 Weak performer",
                            "detail":   f"{wr:.0f}% win rate over {r['n']} trades, avg {avg:+.2f}%. Consider reducing exposure.",
                            "priority": "high",
                            "ts":       "",
                        })

                # Check for common exit reasons
                reasons = conn.execute("""
                    SELECT exit_reason, COUNT(*) as n,
                           AVG(pnl_pct) as avg_pnl
                    FROM perp_positions
                    WHERE status='CLOSED' AND exit_reason IS NOT NULL
                          AND closed_ts_utc > datetime('now', '-7 days')
                    GROUP BY exit_reason
                    ORDER BY n DESC
                """).fetchall()
                for r in reasons:
                    if r["n"] >= 3:
                        learnings.append({
                            "type":     "exit_pattern",
                            "icon":     "\U0001f4dd",
                            "title":    f"Exit: {r['exit_reason']}",
                            "detail":   f"{r['n']} exits via {r['exit_reason']}, avg PnL {(r['avg_pnl'] or 0):+.2f}%.",
                            "priority": "info",
                            "ts":       "",
                        })
        except Exception:
            pass

        return {"learnings": learnings}
    except Exception as exc:
        return JSONResponse({"learnings": [], "error": str(exc)}, status_code=200)
