"""
Confluence Engine API — Patch 143

Routes:
  GET /api/confluence/events?limit=50
  GET /api/confluence/stats
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/confluence", tags=["confluence"])


def _get_db():
    import sys, os
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    if root not in sys.path:
        sys.path.insert(0, root)
    from utils.db import get_conn  # type: ignore
    return get_conn


def _phase_label(total: int) -> str:
    if total < 20:
        return "OBSERVE"
    if total < 50:
        return "ANALYZE"
    if total < 100:
        return "VALIDATE"
    return "INTEGRATE"


def _next_milestone(total: int) -> int | None:
    for m in (20, 50, 100):
        if total < m:
            return m
    return None


@router.get("/events")
def get_confluence_events(limit: int = 50):
    """Return recent confluence events ordered by newest first."""
    try:
        from auth import get_current_user  # type: ignore  # noqa: F401 — endpoint is public for now
    except Exception:
        pass

    get_conn = _get_db()
    try:
        with get_conn() as conn:
            rows = conn.execute("""
                SELECT id, ts_utc, confluence_type, token_symbol, token_mint,
                       source_count, sources, whale_alert_id, memecoin_scan_id,
                       whale_score, memecoin_score, confluence_score,
                       market_cap_usd, price_at_event, alert_sent,
                       price_1h, return_1h_pct,
                       price_4h, return_4h_pct,
                       price_24h, return_24h_pct,
                       outcome_status
                FROM confluence_events
                ORDER BY ts_utc DESC
                LIMIT ?
            """, (max(1, min(limit, 200)),)).fetchall()
        return {"events": [dict(r) for r in rows]}
    except Exception as e:
        log.warning("[CONF] /events error: %s", e)
        return {"events": [], "error": str(e)}


@router.get("/stats")
def get_confluence_stats():
    """Return win rates by timeframe, current phase, and event counts."""
    get_conn = _get_db()
    try:
        with get_conn() as conn:
            total = conn.execute(
                "SELECT COUNT(*) FROM confluence_events"
            ).fetchone()[0] or 0

            complete = conn.execute(
                "SELECT COUNT(*) FROM confluence_events WHERE outcome_status='COMPLETE'"
            ).fetchone()[0] or 0

            wr_1h = conn.execute("""
                SELECT ROUND(AVG(CASE WHEN return_1h_pct > 0 THEN 1.0 ELSE 0.0 END)*100, 1)
                FROM confluence_events WHERE return_1h_pct IS NOT NULL
            """).fetchone()[0]

            wr_4h = conn.execute("""
                SELECT ROUND(AVG(CASE WHEN return_4h_pct > 0 THEN 1.0 ELSE 0.0 END)*100, 1)
                FROM confluence_events WHERE return_4h_pct IS NOT NULL
            """).fetchone()[0]

            wr_24h = conn.execute("""
                SELECT ROUND(AVG(CASE WHEN return_24h_pct > 0 THEN 1.0 ELSE 0.0 END)*100, 1)
                FROM confluence_events WHERE return_24h_pct IS NOT NULL
            """).fetchone()[0]

            avg_conf_score = conn.execute(
                "SELECT ROUND(AVG(confluence_score), 1) FROM confluence_events"
            ).fetchone()[0]

            pending = total - complete

        return {
            "total_events": total,
            "complete_events": complete,
            "pending_events": pending,
            "phase": _phase_label(total),
            "next_milestone": _next_milestone(total),
            "wr_1h": wr_1h,
            "wr_4h": wr_4h,
            "wr_24h": wr_24h,
            "avg_confluence_score": avg_conf_score,
        }
    except Exception as e:
        log.warning("[CONF] /stats error: %s", e)
        return {
            "total_events": 0, "complete_events": 0, "pending_events": 0,
            "phase": "OBSERVE", "next_milestone": 20,
            "wr_1h": None, "wr_4h": None, "wr_24h": None,
            "avg_confluence_score": None,
            "error": str(e),
        }
