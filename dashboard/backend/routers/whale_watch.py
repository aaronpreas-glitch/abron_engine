"""
Whale Watch API endpoints — Patch 141

Routes:
  GET /api/whale-watch/alerts?limit=25&tier=all  — recent alerts, optional MC tier filter
  GET /api/whale-watch/stats                      — full analytics: phase, tier breakdown, signals
"""
from __future__ import annotations

import logging
import os
import sys

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


def _pct(num: int | float, den: int | float) -> float | None:
    try:
        den_f = float(den or 0)
        if den_f <= 0:
            return None
        return round(float(num or 0) / den_f * 100, 1)
    except Exception:
        return None


def _arkham_effectiveness(conn) -> dict:
    cache_rows = conn.execute("SELECT COUNT(*) FROM arkham_token_intel").fetchone()[0] or 0
    cache_live = conn.execute(
        "SELECT COUNT(*) FROM arkham_token_intel WHERE status='LIVE'"
    ).fetchone()[0] or 0
    cache_partial = conn.execute(
        "SELECT COUNT(*) FROM arkham_token_intel WHERE status='PARTIAL'"
    ).fetchone()[0] or 0

    alert_live = conn.execute(
        "SELECT COUNT(*) FROM whale_watch_alerts WHERE arkham_status='LIVE'"
    ).fetchone()[0] or 0
    alert_partial = conn.execute(
        "SELECT COUNT(*) FROM whale_watch_alerts WHERE arkham_status='PARTIAL'"
    ).fetchone()[0] or 0
    alert_error = conn.execute(
        "SELECT COUNT(*) FROM whale_watch_alerts WHERE arkham_status='ERROR'"
    ).fetchone()[0] or 0
    alert_missing = conn.execute(
        "SELECT COUNT(*) FROM whale_watch_alerts WHERE arkham_status IS NULL OR arkham_status=''"
    ).fetchone()[0] or 0

    q_high = conn.execute(
        "SELECT COUNT(*) FROM whale_watch_alerts WHERE arkham_signal_quality='HIGH'"
    ).fetchone()[0] or 0
    q_medium = conn.execute(
        "SELECT COUNT(*) FROM whale_watch_alerts WHERE arkham_signal_quality='MEDIUM'"
    ).fetchone()[0] or 0
    q_low = conn.execute(
        "SELECT COUNT(*) FROM whale_watch_alerts WHERE arkham_signal_quality='LOW'"
    ).fetchone()[0] or 0
    q_none = conn.execute(
        "SELECT COUNT(*) FROM whale_watch_alerts WHERE arkham_signal_quality='NONE'"
    ).fetchone()[0] or 0
    meaningful = q_high + q_medium + q_low

    recent_live = conn.execute(
        "SELECT COUNT(*) FROM whale_watch_alerts WHERE arkham_status='LIVE' AND ts_utc >= datetime('now','-24 hours')"
    ).fetchone()[0] or 0
    recent_meaningful = conn.execute(
        """
        SELECT COUNT(*) FROM whale_watch_alerts
        WHERE arkham_signal_quality IN ('HIGH','MEDIUM','LOW')
          AND ts_utc >= datetime('now','-24 hours')
        """
    ).fetchone()[0] or 0

    in_range_live = conn.execute(
        "SELECT COUNT(*) FROM whale_watch_alerts WHERE mc_in_range=1 AND arkham_status='LIVE'"
    ).fetchone()[0] or 0
    in_range_meaningful = conn.execute(
        """
        SELECT COUNT(*) FROM whale_watch_alerts
        WHERE mc_in_range=1 AND arkham_signal_quality IN ('HIGH','MEDIUM','LOW')
        """
    ).fetchone()[0] or 0
    scanner_pass_live = conn.execute(
        "SELECT COUNT(*) FROM whale_watch_alerts WHERE scanner_pass=1 AND arkham_status='LIVE'"
    ).fetchone()[0] or 0
    scanner_pass_meaningful = conn.execute(
        """
        SELECT COUNT(*) FROM whale_watch_alerts
        WHERE scanner_pass=1 AND arkham_signal_quality IN ('HIGH','MEDIUM','LOW')
        """
    ).fetchone()[0] or 0
    avg_signal = conn.execute(
        "SELECT ROUND(AVG(arkham_signal_score), 1) FROM whale_watch_alerts WHERE arkham_status='LIVE'"
    ).fetchone()[0]

    return {
        "cache_rows": cache_rows,
        "cache_live": cache_live,
        "cache_partial": cache_partial,
        "alert_live": alert_live,
        "alert_partial": alert_partial,
        "alert_error": alert_error,
        "alert_missing": alert_missing,
        "quality_high": q_high,
        "quality_medium": q_medium,
        "quality_low": q_low,
        "quality_none": q_none,
        "meaningful_alerts": meaningful,
        "meaningful_rate_pct": _pct(meaningful, alert_live),
        "recent_live_24h": recent_live,
        "recent_meaningful_24h": recent_meaningful,
        "in_range_live": in_range_live,
        "in_range_meaningful": in_range_meaningful,
        "scanner_pass_live": scanner_pass_live,
        "scanner_pass_meaningful": scanner_pass_meaningful,
        "scanner_pass_rate_pct": _pct(scanner_pass_live, alert_live),
        "meaningful_pass_rate_pct": _pct(scanner_pass_meaningful, meaningful),
        "avg_signal_score": avg_signal,
    }


def _whale_pipeline_diagnostics(conn) -> dict:
    in_range_total = conn.execute(
        "SELECT COUNT(*) FROM whale_watch_alerts WHERE mc_in_range=1"
    ).fetchone()[0] or 0
    mint_resolved = conn.execute(
        "SELECT COUNT(*) FROM whale_watch_alerts WHERE mc_in_range=1 AND token_mint IS NOT NULL AND token_mint != ''"
    ).fetchone()[0] or 0
    mint_resolved_primary = conn.execute(
        """
        SELECT COUNT(*) FROM whale_watch_alerts
        WHERE mc_in_range=1
          AND token_mint IS NOT NULL AND token_mint != ''
          AND COALESCE(mint_resolution_source, '') IN ('exact_symbol', 'exact_name')
        """
    ).fetchone()[0] or 0
    mint_resolved_fallback = conn.execute(
        """
        SELECT COUNT(*) FROM whale_watch_alerts
        WHERE mc_in_range=1
          AND token_mint IS NOT NULL AND token_mint != ''
          AND COALESCE(mint_resolution_source, '') NOT IN ('', 'exact_symbol', 'exact_name')
        """
    ).fetchone()[0] or 0
    priced = conn.execute(
        "SELECT COUNT(*) FROM whale_watch_alerts WHERE mc_in_range=1 AND price_at_alert > 0"
    ).fetchone()[0] or 0
    priced_primary = conn.execute(
        """
        SELECT COUNT(*) FROM whale_watch_alerts
        WHERE mc_in_range=1 AND price_at_alert > 0 AND COALESCE(price_source, '')='dex_pair'
        """
    ).fetchone()[0] or 0
    priced_fallback = conn.execute(
        """
        SELECT COUNT(*) FROM whale_watch_alerts
        WHERE mc_in_range=1 AND price_at_alert > 0 AND COALESCE(price_source, '') NOT IN ('', 'dex_pair')
        """
    ).fetchone()[0] or 0
    scanner_checked = conn.execute(
        "SELECT COUNT(*) FROM whale_watch_alerts WHERE mc_in_range=1 AND scanner_pass IS NOT NULL"
    ).fetchone()[0] or 0
    scanner_failed = conn.execute(
        "SELECT COUNT(*) FROM whale_watch_alerts WHERE mc_in_range=1 AND scanner_pass=0"
    ).fetchone()[0] or 0
    scanner_pass = conn.execute(
        "SELECT COUNT(*) FROM whale_watch_alerts WHERE mc_in_range=1 AND scanner_pass=1"
    ).fetchone()[0] or 0
    arkham_live = conn.execute(
        "SELECT COUNT(*) FROM whale_watch_alerts WHERE mc_in_range=1 AND arkham_status='LIVE'"
    ).fetchone()[0] or 0
    arkham_meaningful = conn.execute(
        """
        SELECT COUNT(*) FROM whale_watch_alerts
        WHERE mc_in_range=1 AND arkham_signal_quality IN ('HIGH','MEDIUM','LOW')
        """
    ).fetchone()[0] or 0

    fail_rows = conn.execute(
        """
        SELECT
            CASE
                WHEN (token_mint IS NULL OR token_mint = '')
                     AND mint_resolution_reason IS NOT NULL AND TRIM(mint_resolution_reason) != ''
                    THEN mint_resolution_reason
                WHEN (price_at_alert IS NULL OR price_at_alert <= 0)
                     AND price_resolution_reason IS NOT NULL AND TRIM(price_resolution_reason) != ''
                    THEN price_resolution_reason
                WHEN scanner_reason IS NOT NULL AND TRIM(scanner_reason) != '' THEN scanner_reason
                WHEN scanner_pass = 1 THEN 'ok'
                WHEN scanner_rug_label IN ('DANGER', 'RUGGED') THEN 'rug=' || scanner_rug_label
                WHEN price_at_alert IS NULL OR price_at_alert <= 0 THEN 'price_unavailable'
                WHEN token_mint IS NULL OR token_mint = '' THEN 'mint_unresolved'
                ELSE 'scanner_fail'
            END AS reason,
            COUNT(*) AS n
        FROM whale_watch_alerts
        WHERE mc_in_range=1
        GROUP BY reason
        ORDER BY n DESC, reason ASC
        LIMIT 5
        """
    ).fetchall()

    top_fail_reasons = [
        {"reason": r["reason"], "count": int(r["n"] or 0)}
        for r in fail_rows
        if r["reason"] not in ("ok", None, "")
    ]

    if in_range_total <= 0:
        current_bottleneck = "NO_IN_RANGE_ALERTS"
        current_bottleneck_detail = "No whale alerts in the $5M-$50M focus band yet."
    elif mint_resolved < in_range_total:
        current_bottleneck = "MINT_RESOLUTION"
        current_bottleneck_detail = f"{mint_resolved}/{in_range_total} in-range alerts resolved a mint."
    elif priced < mint_resolved:
        current_bottleneck = "PRICE_QUALITY"
        current_bottleneck_detail = f"{priced}/{mint_resolved} mint-resolved alerts captured a usable entry price."
    elif scanner_checked < mint_resolved:
        current_bottleneck = "SCANNER_INCOMPLETE"
        current_bottleneck_detail = f"{scanner_checked}/{mint_resolved} mint-resolved alerts completed scanner evaluation."
    elif scanner_pass <= 0 and top_fail_reasons:
        current_bottleneck = "SCANNER_QUALITY"
        current_bottleneck_detail = f"Scanner is running, but current bottleneck is {top_fail_reasons[0]['reason']}."
    elif scanner_pass <= 0:
        current_bottleneck = "SCANNER_QUALITY"
        current_bottleneck_detail = "Scanner is running, but no in-range whale alert has passed yet."
    elif arkham_live < scanner_pass:
        current_bottleneck = "ARKHAM_COVERAGE"
        current_bottleneck_detail = f"{arkham_live}/{scanner_pass} scanner-pass alerts have Arkham context."
    elif arkham_meaningful <= 0:
        current_bottleneck = "ARKHAM_QUALITY"
        current_bottleneck_detail = "Arkham is live, but current whale context is weak or exchange-heavy."
    else:
        current_bottleneck = "OVERLAP_WAIT"
        current_bottleneck_detail = "Whale inputs are healthy; confluence is waiting on overlap timing."

    return {
        "in_range_total": in_range_total,
        "mint_resolved": mint_resolved,
        "mint_resolved_primary": mint_resolved_primary,
        "mint_resolved_fallback": mint_resolved_fallback,
        "priced": priced,
        "priced_primary": priced_primary,
        "priced_fallback": priced_fallback,
        "scanner_checked": scanner_checked,
        "scanner_failed": scanner_failed,
        "scanner_pass": scanner_pass,
        "arkham_live": arkham_live,
        "arkham_meaningful": arkham_meaningful,
        "mint_resolution_rate_pct": _pct(mint_resolved, in_range_total),
        "priced_rate_pct": _pct(priced, in_range_total),
        "scanner_checked_rate_pct": _pct(scanner_checked, in_range_total),
        "scanner_pass_rate_pct": _pct(scanner_pass, scanner_checked),
        "arkham_meaningful_rate_pct": _pct(arkham_meaningful, arkham_live),
        "top_fail_reasons": top_fail_reasons,
        "current_bottleneck": current_bottleneck,
        "current_bottleneck_detail": current_bottleneck_detail,
    }


def get_whale_summary_data() -> dict:
    """
    Canonical compact whale-lane snapshot shared across Home and Whale Watch.
    Keeps summary counts and recent-activity posture in one place so operator
    surfaces do not drift.
    """
    _empty = {
        "total": 0,
        "in_range": 0,
        "scanner_pass": 0,
        "alerts_sent": 0,
        "last_ts": None,
        "recent_alerts_6h": 0,
        "recent_pass_2h": 0,
        "posture": "QUIET",
        "detail": "no data",
        "ingest_status": "UNKNOWN",
        "ingest_detail": "status unavailable",
        "configured": False,
        "session_ready": False,
        "connected": False,
        "arkham_configured": False,
        "arkham_enriched": 0,
        "arkham_high_quality": 0,
        "arkham_effectiveness": {
            "cache_rows": 0,
            "cache_live": 0,
            "cache_partial": 0,
            "alert_live": 0,
            "alert_partial": 0,
            "alert_error": 0,
            "alert_missing": 0,
            "quality_high": 0,
            "quality_medium": 0,
            "quality_low": 0,
            "quality_none": 0,
            "meaningful_alerts": 0,
            "meaningful_rate_pct": None,
            "recent_live_24h": 0,
            "recent_meaningful_24h": 0,
            "in_range_live": 0,
            "in_range_meaningful": 0,
            "scanner_pass_live": 0,
            "scanner_pass_meaningful": 0,
            "scanner_pass_rate_pct": None,
            "meaningful_pass_rate_pct": None,
            "avg_signal_score": None,
        },
        "pipeline_diagnostics": {
            "in_range_total": 0,
            "mint_resolved": 0,
            "mint_resolved_primary": 0,
            "mint_resolved_fallback": 0,
            "priced": 0,
            "priced_primary": 0,
            "priced_fallback": 0,
            "scanner_checked": 0,
            "scanner_failed": 0,
            "scanner_pass": 0,
            "arkham_live": 0,
            "arkham_meaningful": 0,
            "mint_resolution_rate_pct": None,
            "priced_rate_pct": None,
            "scanner_checked_rate_pct": None,
            "scanner_pass_rate_pct": None,
            "arkham_meaningful_rate_pct": None,
            "top_fail_reasons": [],
            "current_bottleneck": "UNKNOWN",
            "current_bottleneck_detail": "diagnostics unavailable",
        },
    }
    try:
        root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
        if root not in sys.path:
            sys.path.insert(0, root)
        from utils.db import get_conn
        from utils import orchestrator

        channel = os.getenv("WHALE_WATCH_CHANNEL", "").strip()
        api_id = os.getenv("TELEGRAM_API_ID", "").strip()
        api_hash = os.getenv("TELEGRAM_API_HASH", "").strip()
        configured = bool(channel and api_id and api_hash)
        arkham_configured = bool(os.getenv("ARKHAM_API_KEY", "").strip())
        session_path = os.path.join(root, "data_storage", "whale_watch.session")
        session_ready = os.path.exists(session_path)
        agent = next((a for a in orchestrator.get_status() if a.get("name") == "whale_watch"), None)
        agent_health = str(agent.get("health") or "init") if agent else "init"
        connected = agent_health == "alive"

        with get_conn() as conn:
            tbl = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='whale_watch_alerts'"
            ).fetchone()
            if not tbl:
                data = dict(_empty)
                data.update({
                    "configured": configured,
                    "session_ready": session_ready,
                    "connected": connected,
                    "arkham_configured": arkham_configured,
                })
                return data

            total = conn.execute("SELECT COUNT(*) FROM whale_watch_alerts").fetchone()[0] or 0
            in_range = conn.execute(
                "SELECT COUNT(*) FROM whale_watch_alerts WHERE mc_in_range=1"
            ).fetchone()[0] or 0
            scanner_pass = conn.execute(
                "SELECT COUNT(*) FROM whale_watch_alerts WHERE scanner_pass=1"
            ).fetchone()[0] or 0
            alerts_sent = conn.execute(
                "SELECT COUNT(*) FROM whale_watch_alerts WHERE alert_sent=1"
            ).fetchone()[0] or 0
            arkham_enriched = conn.execute(
                "SELECT COUNT(*) FROM whale_watch_alerts WHERE arkham_status='LIVE'"
            ).fetchone()[0] or 0
            arkham_high_quality = conn.execute(
                "SELECT COUNT(*) FROM whale_watch_alerts WHERE arkham_signal_quality='HIGH'"
            ).fetchone()[0] or 0
            arkham_effectiveness = _arkham_effectiveness(conn)
            pipeline_diagnostics = _whale_pipeline_diagnostics(conn)
            recent_pass_2h = conn.execute("""
                SELECT COUNT(*) FROM whale_watch_alerts
                WHERE scanner_pass=1 AND mc_tier='sweet_spot'
                  AND ts_utc >= datetime('now','-2 hours')
            """).fetchone()[0] or 0
            recent_alerts_6h = conn.execute("""
                SELECT COUNT(*) FROM whale_watch_alerts
                WHERE ts_utc >= datetime('now','-6 hours')
            """).fetchone()[0] or 0
            recent_alerts_30m = conn.execute("""
                SELECT COUNT(*) FROM whale_watch_alerts
                WHERE ts_utc >= datetime('now','-30 minutes')
            """).fetchone()[0] or 0
            last_row = conn.execute(
                "SELECT ts_utc FROM whale_watch_alerts ORDER BY id DESC LIMIT 1"
            ).fetchone()
            last_ts = last_row[0] if last_row else None
            cache_rows = arkham_effectiveness.get("cache_rows") or 0

        if recent_pass_2h > 0:
            posture = "SIGNAL_ACTIVE"
            detail = f"{recent_pass_2h} scanner-pass alert(s) <2h"
        elif recent_alerts_6h > 0:
            posture = "TRACKING"
            detail = f"{recent_alerts_6h} alert(s) last 6h · none scanner-pass"
        else:
            posture = "QUIET"
            detail = "no recent alerts"

        # Trust live runtime evidence over a brittle env-only read. If the lane has
        # a valid session and is actively receiving alerts, it is configured enough
        # for operator purposes even when env vars are not visible in this process.
        inferred_live = session_ready and (recent_alerts_30m > 0 or agent_health in ("alive", "slow"))
        configured = configured or session_ready or total > 0
        connected = connected or inferred_live
        arkham_configured = arkham_configured or arkham_enriched > 0 or cache_rows > 0

        if not configured:
            ingest_status = "NOT_CONFIGURED"
            ingest_detail = "missing Telegram API credentials or channel"
        elif not session_ready:
            ingest_status = "AUTH_REQUIRED"
            ingest_detail = "Telethon session not created"
        elif connected and total == 0:
            ingest_status = "CONNECTED_NO_DATA"
            ingest_detail = "connected to channel · waiting for first new alert"
        elif connected:
            ingest_status = "LIVE"
            ingest_detail = "connected and ingesting whale alerts"
        elif agent_health in ("init", "slow"):
            ingest_status = "STARTING"
            ingest_detail = "configured and authenticated · agent warming up"
        else:
            ingest_status = "DISCONNECTED"
            ingest_detail = "configured but whale agent is not connected"

        return {
            "total": total,
            "in_range": in_range,
            "scanner_pass": scanner_pass,
            "alerts_sent": alerts_sent,
            "last_ts": last_ts,
            "recent_alerts_6h": recent_alerts_6h,
            "recent_pass_2h": recent_pass_2h,
            "posture": posture,
            "detail": detail,
            "ingest_status": ingest_status,
            "ingest_detail": ingest_detail,
            "configured": configured,
            "session_ready": session_ready,
            "connected": connected,
            "arkham_configured": arkham_configured,
            "arkham_enriched": arkham_enriched,
            "arkham_high_quality": arkham_high_quality,
            "arkham_effectiveness": arkham_effectiveness,
            "pipeline_diagnostics": pipeline_diagnostics,
        }
    except Exception as e:
        log.warning("whale_watch summary error: %s", e)
        return dict(_empty)


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/summary")
def get_whale_summary(_user=Depends(get_current_user)):
    """Compact whale-lane summary/status for Home and lightweight consumers."""
    return get_whale_summary_data()


@router.get("/status")
def get_whale_status(_user=Depends(get_current_user)):
    """Alias for /summary so whale status has a stable obvious endpoint."""
    return get_whale_summary_data()

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
                       token_pair_address, mint_resolution_source, mint_resolution_reason,
                       buy_amount_usd, market_cap_usd, mc_tier, mc_in_range,
                       scanner_pass, scanner_score, scanner_rug_label, scanner_reason,
                       alert_sent, price_at_alert, price_source, price_resolution_reason,
                       arkham_status, arkham_signal_quality, arkham_signal_score,
                       arkham_entity_holder_count, arkham_top_entity_name, arkham_top_entity_type,
                       arkham_top_entity_pct_of_cap, arkham_top_flow_entity_name,
                       arkham_top_flow_entity_type, arkham_net_flow_usd, arkham_enriched_at,
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
            arkham_enriched = conn.execute("SELECT COUNT(*) FROM whale_watch_alerts WHERE arkham_status='LIVE'").fetchone()[0]
            arkham_high_quality = conn.execute("SELECT COUNT(*) FROM whale_watch_alerts WHERE arkham_signal_quality='HIGH'").fetchone()[0]
            arkham_effectiveness = _arkham_effectiveness(conn)
            pipeline_diagnostics = _whale_pipeline_diagnostics(conn)

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
                    SUM(CASE WHEN arkham_status='LIVE' THEN 1 ELSE 0 END)    AS arkham_enriched,
                    SUM(CASE WHEN arkham_signal_quality='HIGH' THEN 1 ELSE 0 END) AS arkham_high_quality,
                    COUNT(CASE WHEN outcome_status='COMPLETE'   THEN 1 END)   AS complete,
                    COUNT(CASE WHEN outcome_status='UNRESOLVED' THEN 1 END)   AS unresolved,
                    ROUND(AVG(CASE
                        WHEN outcome_status='COMPLETE' AND return_24h_pct > 0 THEN 1.0
                        WHEN outcome_status='COMPLETE' THEN 0.0
                    END) * 100, 1)                                            AS wr_24h,
                    ROUND(AVG(CASE WHEN outcome_status='COMPLETE' THEN return_24h_pct END), 2) AS avg_return_24h,
                    ROUND(AVG(CASE WHEN outcome_status='COMPLETE' THEN return_1h_pct  END), 2) AS avg_return_1h
                FROM whale_watch_alerts
                GROUP BY COALESCE(mc_tier, 'unknown')
            """).fetchall()

            # Patch 219: trackability per tier
            # TRACKABLE  = complete >= 30 AND complete/(complete+unresolved) >= 70%
            # PARTIAL    = complete >= 5  AND complete/(complete+unresolved) >= 30%
            # UNTRACKABLE= otherwise (cross-chain tokens, no Solana mint resolved)
            tiers: dict = {}
            for r in tier_rows:
                d          = dict(r)
                tier_name  = d.pop("tier")
                raw_unres  = d.pop("unresolved", 0) or 0
                complete   = d.get("complete", 0) or 0
                total_cls  = complete + raw_unres
                if total_cls > 0:
                    ts_pct = round(complete / total_cls * 100, 1)
                    if complete >= 30 and ts_pct >= 70.0:
                        ts_status = "TRACKABLE"
                    elif complete >= 5 and ts_pct >= 30.0:
                        ts_status = "PARTIAL"
                    else:
                        ts_status = "UNTRACKABLE"
                else:
                    ts_pct, ts_status = 0.0, "UNTRACKABLE"
                d["unresolved_count"]    = raw_unres
                d["trackability_status"] = ts_status
                d["trackable_pct"]       = ts_pct
                tiers[tier_name] = d

            _empty = {
                "total": 0, "in_range": 0, "scanner_pass": 0, "alerts_sent": 0,
                "complete": 0, "wr_24h": None, "avg_return_24h": None, "avg_return_1h": None,
                "unresolved_count": 0, "trackability_status": "UNTRACKABLE", "trackable_pct": 0.0,
            }
            for t in ("micro", "sweet_spot", "mid", "large"):
                if t not in tiers:
                    tiers[t] = dict(_empty)

            # ── Sweet-spot alert-type split ──────────────────────────────────
            sweet_by_type: dict = {}
            try:
                type_rows = conn.execute("""
                    SELECT
                        COALESCE(alert_type, 'UNKNOWN')                           AS alert_type,
                        COUNT(*)                                                   AS n,
                        COUNT(CASE WHEN outcome_status='COMPLETE' THEN 1 END)     AS complete,
                        ROUND(AVG(CASE
                            WHEN outcome_status='COMPLETE' AND return_24h_pct > 0 THEN 1.0
                            WHEN outcome_status='COMPLETE' THEN 0.0
                        END) * 100, 1)                                             AS wr_24h,
                        ROUND(AVG(CASE WHEN outcome_status='COMPLETE' THEN return_24h_pct END), 2) AS avg_return_24h
                    FROM whale_watch_alerts
                    WHERE mc_tier = 'sweet_spot'
                    GROUP BY COALESCE(alert_type, 'UNKNOWN')
                """).fetchall()
                for r in type_rows:
                    sweet_by_type[r["alert_type"]] = {
                        "n":              r["n"],
                        "complete":       r["complete"],
                        "wr_24h":         r["wr_24h"],
                        "avg_return_24h": r["avg_return_24h"],
                    }
            except Exception:
                pass
            if "sweet_spot" in tiers:
                tiers["sweet_spot"]["by_type"] = sweet_by_type

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
            "arkham_enriched": arkham_enriched,
            "arkham_high_quality": arkham_high_quality,
            "arkham_effectiveness": arkham_effectiveness,
            "pipeline_diagnostics": pipeline_diagnostics,
            "phase":          phase_info["phase"],
            "phase_label":    phase_info["label"],
            "phase_desc":     phase_info["desc"],
            "next_milestone": phase_info["next_milestone"],
            "milestones":     MILESTONES,
            "tiers":          tiers,
            "cross_signals":  cross_signals,
            "last_ts":        last_ts,
            **{k: get_whale_summary_data().get(k) for k in (
                "ingest_status", "ingest_detail", "configured", "session_ready",
                "connected", "arkham_configured"
            )},
        }

    except Exception as e:
        log.warning("whale_stats error: %s", e)
        _e = {
            "total": 0, "in_range": 0, "scanner_pass": 0, "alerts_sent": 0,
            "complete": 0, "wr_24h": None, "avg_return_24h": None, "avg_return_1h": None,
            "unresolved_count": 0, "trackability_status": "UNTRACKABLE", "trackable_pct": 0.0,
        }
        return {
            "total": 0, "outcomes": 0, "in_range": 0, "scanner_pass": 0, "alerts_sent": 0,
            "phase": 1, "phase_label": "OBSERVE",
            "phase_desc": "Logging all alerts across all MC tiers",
            "next_milestone": 50, "milestones": MILESTONES,
            "tiers": {t: dict(_e) for t in ("micro", "sweet_spot", "mid", "large")},
            "cross_signals": [], "last_ts": None,
            "arkham_effectiveness": {
                "cache_rows": 0,
                "cache_live": 0,
                "cache_partial": 0,
                "alert_live": 0,
                "alert_partial": 0,
                "alert_error": 0,
                "alert_missing": 0,
                "quality_high": 0,
                "quality_medium": 0,
                "quality_low": 0,
                "quality_none": 0,
                "meaningful_alerts": 0,
                "meaningful_rate_pct": None,
                "recent_live_24h": 0,
                "recent_meaningful_24h": 0,
                "in_range_live": 0,
                "in_range_meaningful": 0,
                "scanner_pass_live": 0,
                "scanner_pass_meaningful": 0,
                "scanner_pass_rate_pct": None,
                "meaningful_pass_rate_pct": None,
                "avg_signal_score": None,
            },
            "pipeline_diagnostics": {
                "in_range_total": 0,
                "mint_resolved": 0,
                "mint_resolved_primary": 0,
                "mint_resolved_fallback": 0,
                "priced": 0,
                "priced_primary": 0,
                "priced_fallback": 0,
                "scanner_checked": 0,
                "scanner_failed": 0,
                "scanner_pass": 0,
                "arkham_live": 0,
                "arkham_meaningful": 0,
                "mint_resolution_rate_pct": None,
                "priced_rate_pct": None,
                "scanner_checked_rate_pct": None,
                "scanner_pass_rate_pct": None,
                "arkham_meaningful_rate_pct": None,
                "top_fail_reasons": [],
                "current_bottleneck": "UNKNOWN",
                "current_bottleneck_detail": "diagnostics unavailable",
            },
        }
