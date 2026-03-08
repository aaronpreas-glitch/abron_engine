"""
Whale Watch API endpoints — Patch 141

Routes:
  GET /api/whale-watch/alerts?limit=25&tier=all  — recent alerts, optional MC tier filter
  GET /api/whale-watch/stats                      — full analytics: phase, tier breakdown, signals
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends

from auth import get_current_user

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/whale-watch", tags=["whale_watch"])

# ── Learning loop milestones ───────────────────────────────────────────────────

MILESTONES = [
    {"outcomes": 0,   "phase": 1, "label": "OBSERVE",   "desc": "Logging all alerts across all MC tiers"},
    {"outcomes": 50,  "phase": 2, "label": "ANALYZE",   "desc": "Tier win rates emerge — identify which MC tiers have signal"},
    {"outcomes": 100, "phase": 3, "label": "INTEGRATE", "desc": "Cross-confirm sweet spot whale buys with Memecoin Scanner"},
    {"outcomes": 250, "phase": 4, "label": "ENRICH",    "desc": "Mid/large whale flow influences Spot allocation weighting"},
    {"outcomes": 500, "phase": 5, "label": "SIGNAL",    "desc": "Whale Watch is a first-class signal source across all arms"},
]


def _current_phase(outcomes: int) -> dict:
    phase_info = MILESTONES[0]
    for ms in MILESTONES:
        if outcomes >= ms["outcomes"]:
            phase_info = ms
    next_ms = next((ms["outcomes"] for ms in MILESTONES if ms["outcomes"] > outcomes), None)
    return {**phase_info, "next_milestone": next_ms}


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/alerts")
def get_whale_alerts(limit: int = 25, tier: str = "all", _user=Depends(get_current_user)):
    """Recent whale watch alerts, newest first. Filter by mc_tier if tier != 'all'."""
    try:
        import sys, os
        root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
        if root not in sys.path:
            sys.path.insert(0, root)
        from utils.db import get_conn
        with get_conn() as conn:
            base = """
                SELECT id, ts_utc, alert_type, kol_name, token_symbol, token_mint,
                       buy_amount_usd, market_cap_usd, mc_tier, mc_in_range,
                       scanner_pass, scanner_score, scanner_rug_label,
                       alert_sent, price_at_alert,
                       return_1h_pct, return_4h_pct, return_24h_pct,
                       outcome_status
                FROM whale_watch_alerts
            """
            if tier == "all":
                rows = conn.execute(
                    base + " ORDER BY id DESC LIMIT ?",
                    (min(limit, 100),)
                ).fetchall()
            else:
                rows = conn.execute(
                    base + " WHERE mc_tier=? ORDER BY id DESC LIMIT ?",
                    (tier, min(limit, 100))
                ).fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        log.warning("whale_watch alerts error: %s", e)
        return []


@router.get("/stats")
def get_whale_stats(_user=Depends(get_current_user)):
    """
    Full analytics for the Whale tab:
      - Total alerts, complete outcomes, current learning phase
      - Per-tier breakdown: counts, win rates, avg returns
      - Recent cross-agent signals from the signal bus
    """
    try:
        import sys, os
        root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
        if root not in sys.path:
            sys.path.insert(0, root)
        from utils.db import get_conn
        with get_conn() as conn:

            # ── Totals ──────────────────────────────────────────────────────
            total        = conn.execute("SELECT COUNT(*) FROM whale_watch_alerts").fetchone()[0]
            outcomes     = conn.execute("SELECT COUNT(*) FROM whale_watch_alerts WHERE outcome_status='COMPLETE'").fetchone()[0]
            in_range     = conn.execute("SELECT COUNT(*) FROM whale_watch_alerts WHERE mc_in_range=1").fetchone()[0]
            scanner_pass = conn.execute("SELECT COUNT(*) FROM whale_watch_alerts WHERE scanner_pass=1").fetchone()[0]
            alerts_sent  = conn.execute("SELECT COUNT(*) FROM whale_watch_alerts WHERE alert_sent=1").fetchone()[0]

            # ── Phase ───────────────────────────────────────────────────────
            phase_info = _current_phase(outcomes)

            # ── Tier breakdown ──────────────────────────────────────────────
            tier_rows = conn.execute("""
                SELECT
                    COALESCE(mc_tier, 'unknown')                             AS tier,
                    COUNT(*)                                                  AS total,
                    SUM(CASE WHEN mc_in_range=1    THEN 1 ELSE 0 END)        AS in_range,
                    SUM(CASE WHEN scanner_pass=1   THEN 1 ELSE 0 END)        AS scanner_pass,
                    SUM(CASE WHEN alert_sent=1     THEN 1 ELSE 0 END)        AS alerts_sent,
                    COUNT(CASE WHEN outcome_status='COMPLETE' THEN 1 END)     AS complete,
                    ROUND(AVG(CASE
                        WHEN outcome_status='COMPLETE' AND return_24h_pct > 0 THEN 1.0
                        WHEN outcome_status='COMPLETE' THEN 0.0
                    END) * 100, 1)                                            AS wr_24h,
                    ROUND(AVG(CASE WHEN outcome_status='COMPLETE' THEN return_24h_pct END), 2) AS avg_return_24h,
                    ROUND(AVG(CASE WHEN outcome_status='COMPLETE' THEN return_1h_pct  END), 2) AS avg_return_1h
                FROM whale_watch_alerts
                GROUP BY COALESCE(mc_tier, 'unknown')
            """).fetchall()

            tiers: dict = {}
            for r in tier_rows:
                d = dict(r)
                tiers[d.pop("tier")] = d

            _empty = {
                "total": 0, "in_range": 0, "scanner_pass": 0, "alerts_sent": 0,
                "complete": 0, "wr_24h": None, "avg_return_24h": None, "avg_return_1h": None,
            }
            for t in ("micro", "sweet_spot", "mid", "large"):
                if t not in tiers:
                    tiers[t] = dict(_empty)

            # ── Cross-agent signals (signal bus) ────────────────────────────
            cross_signals: list = []
            try:
                cs_rows = conn.execute("""
                    SELECT id, ts_utc, source, target, signal_type,
                           token_symbol, mc_tier, buy_amount_usd, market_cap_usd,
                           scanner_score, consumed, ref_alert_id
                    FROM cross_agent_signals
                    ORDER BY id DESC
                    LIMIT 10
                """).fetchall()
                cross_signals = [dict(r) for r in cs_rows]
            except Exception:
                pass  # table may not exist on legacy installs — patch 141 creates it

            # ── Last alert ts ───────────────────────────────────────────────
            last_row = conn.execute(
                "SELECT ts_utc FROM whale_watch_alerts ORDER BY id DESC LIMIT 1"
            ).fetchone()
            last_ts = last_row[0] if last_row else None

        return {
            "total":          total,
            "outcomes":       outcomes,
            "in_range":       in_range,
            "scanner_pass":   scanner_pass,
            "alerts_sent":    alerts_sent,
            "phase":          phase_info["phase"],
            "phase_label":    phase_info["label"],
            "phase_desc":     phase_info["desc"],
            "next_milestone": phase_info["next_milestone"],
            "milestones":     MILESTONES,
            "tiers":          tiers,
            "cross_signals":  cross_signals,
            "last_ts":        last_ts,
        }

    except Exception as e:
        log.warning("whale_stats error: %s", e)
        _e = {
            "total": 0, "in_range": 0, "scanner_pass": 0, "alerts_sent": 0,
            "complete": 0, "wr_24h": None, "avg_return_24h": None, "avg_return_1h": None,
        }
        return {
            "total": 0, "outcomes": 0, "in_range": 0, "scanner_pass": 0, "alerts_sent": 0,
            "phase": 1, "phase_label": "OBSERVE",
            "phase_desc": "Logging all alerts across all MC tiers",
            "next_milestone": 50, "milestones": MILESTONES,
            "tiers": {t: dict(_e) for t in ("micro", "sweet_spot", "mid", "large")},
            "cross_signals": [], "last_ts": None,
        }
