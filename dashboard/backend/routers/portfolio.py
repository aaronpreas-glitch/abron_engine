"""
Portfolio Watchman, Orchestrator, System Health, and Journal endpoints.
Patches 91, 104, 116, 118, 120, 122.

Routes:
  GET /api/orchestrator/status  — health status for all registered agents
  GET /api/orchestrator/memory  — last N lines of MEMORY.md as text block
  GET /api/health/status        — system health watchdog: DB, agents, scan age, F&G
  GET /api/journal/learnings    — aggregated learnings from brain + exits + outcomes
  GET /api/portfolio/capital-allocation — cross-arm capital snapshot + recent ledger events
"""
from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from auth import get_current_user
from db_read import get_weekly_tuning_report
from config_editor import get_config
from routers._shared import _ensure_engine_path, _db_path

log = logging.getLogger("dashboard")
router = APIRouter(tags=["portfolio"])


def _allocation_action(delta_pct: float) -> str:
    if delta_pct >= 10:
        return "INCREASE"
    if delta_pct <= -10:
        return "REDUCE"
    return "HOLD"


def _build_allocation_change_summary(history: list[dict]) -> dict:
    if len(history) < 2:
        current = dict(history[0]) if history else {}
        return {
            "changed": False,
            "current_posture": current.get("posture"),
            "previous_posture": None,
            "changed_arms": [],
        }
    current = dict(history[0] or {})
    previous = dict(history[1] or {})
    current_recs = {str(item.get("arm") or ""): dict(item) for item in list(current.get("recommendations") or [])}
    previous_recs = {str(item.get("arm") or ""): dict(item) for item in list(previous.get("recommendations") or [])}
    changed_arms = []
    for arm in sorted(set(current_recs) | set(previous_recs)):
        cur = current_recs.get(arm, {})
        prev = previous_recs.get(arm, {})
        if (
            str(cur.get("action") or "") != str(prev.get("action") or "")
            or round(float(cur.get("target_pct") or 0.0), 1) != round(float(prev.get("target_pct") or 0.0), 1)
        ):
            changed_arms.append({
                "arm": arm,
                "from_action": prev.get("action"),
                "to_action": cur.get("action"),
                "from_target_pct": prev.get("target_pct"),
                "to_target_pct": cur.get("target_pct"),
            })
    return {
        "changed": bool(changed_arms) or str(current.get("posture") or "") != str(previous.get("posture") or ""),
        "current_posture": current.get("posture"),
        "previous_posture": previous.get("posture"),
        "changed_arms": changed_arms,
    }


def _current_share(arms: list[dict], arm_name: str, total_deployed: float) -> float:
    if total_deployed <= 0:
        return 0.0
    for arm in arms:
        if str(arm.get("arm") or "") == arm_name:
            return round((float(arm.get("deployed_usd") or 0.0) / total_deployed) * 100.0, 1)
    return 0.0


def _build_allocation_recommendation() -> dict:
    _ensure_engine_path()
    from utils.db import get_capital_allocation_snapshot, get_risk_mode  # type: ignore
    from utils.speculation_heat import build_speculation_heat_snapshot  # type: ignore
    from routers.home import _home_memecoin_allocator_context, _build_cross_system_allocator_summary  # type: ignore
    from routers.memecoins import get_memecoin_v3_lane_state, get_memecoin_v3_queue  # type: ignore

    capital = get_capital_allocation_snapshot()
    memecoin_capital = _home_memecoin_allocator_context()
    allocator_summary = _build_cross_system_allocator_summary(memecoin_capital)
    lane = get_memecoin_v3_lane_state(limit=8, lookback_days=30)
    queue = get_memecoin_v3_queue(limit=12, lookback_days=30)
    risk_mode = get_risk_mode()
    speculation_heat = build_speculation_heat_snapshot()

    totals = dict(capital.get("totals") or {})
    arms = list(capital.get("arms") or [])
    total_deployed = float(totals.get("deployed_usd") or 0.0)
    allocatable_base = round(max(total_deployed, 100.0), 2)

    lane_state = dict(lane.get("lane_state") or {})
    proof_slots = dict(lane.get("proof_slots") or {})
    system_health = dict(lane.get("system_health") or {})
    queue_counts = dict(queue.get("counts") or {})

    memecoin_mode = str(memecoin_capital.get("memecoins_mode") or "PAPER").upper()
    memecoin_stance = str(memecoin_capital.get("memecoin_allocator_stance") or "UNKNOWN").upper()
    memecoin_regime = str(memecoin_capital.get("memecoins_capital_regime_bucket") or "UNKNOWN").upper()
    memecoin_deploy_auth = str(lane_state.get("deployment_authority") or "UNKNOWN").upper()
    memecoin_grad = str(lane_state.get("graduation_state") or "UNKNOWN").upper()
    discovery_summary = dict(system_health.get("discovery_summary") or {})
    heat_state = str(speculation_heat.get("heat_state") or "UNKNOWN").upper()
    heat_score = float(speculation_heat.get("heat_score") or 0.0)
    heat_quality = float(speculation_heat.get("quality_score") or 0.0)
    heat_momentum = str(speculation_heat.get("momentum") or "STEADY").upper()

    spot_live = os.getenv("SPOT_DRY_RUN", "true").lower() != "true"
    spot_mode = "LIVE" if spot_live else "PAPER"
    perp_live = os.getenv("PERP_DRY_RUN", "true").lower() != "true"
    perp_mode = "LIVE" if perp_live else "PAPER"

    focus_mode = os.getenv("SYSTEM_PRIMARY_FOCUS", "MEMECOINS_SPOT").strip().upper()
    memecoin_spot_focus = focus_mode in ("MEMECOINS_SPOT", "MEMECOINS+SPOT", "MEMECOINS,SPOT")

    perps_score = 1.15 if perp_live else (0.08 if memecoin_spot_focus else 0.35)
    perps_reasons = [
        f"Perp arm is {perp_mode.lower()}",
        f"Risk mode is {str(risk_mode.get('mode') or 'UNKNOWN').lower()}",
    ]
    if memecoin_spot_focus:
        perps_reasons.append("Perps are background-only while the system focus is memecoins and spot.")
    if str(risk_mode.get("mode") or "") == "DEFENSIVE":
        perps_score *= 0.55
        perps_reasons.append("Recent losing streak is forcing defensive sizing.")
    elif str(risk_mode.get("mode") or "") == "CAUTIOUS":
        perps_score *= 0.78
        perps_reasons.append("Recent losses are keeping perps cautious.")
    else:
        perps_reasons.append("Perp risk mode is still supportive.")
    if int(next(((arm.get("open_positions") or 0) for arm in arms if str(arm.get("arm")) == "perps"), 0)) > 0:
        perps_score *= 0.95
        perps_reasons.append("Perp capital is already partially deployed.")

    memecoins_score = 0.2 if memecoin_mode == "PAPER" else 1.0
    memecoin_reasons = [
        f"Memecoin arm is {memecoin_mode.lower()}",
        f"Allocator stance is {memecoin_stance.lower().replace('_', ' ')}",
        f"Deployment authority is {memecoin_deploy_auth.lower()}",
    ]
    stance_mult = {
        "OPEN": 1.0,
        "DISCIPLINED": 0.82,
        "TIGHT": 0.55,
        "SATURATED": 0.35,
        "DISABLED": 0.2,
        "PAPER_ONLY": 0.12,
    }.get(memecoin_stance, 0.6)
    deploy_mult = {
        "FORCEFUL": 1.05,
        "SUPPORTED": 0.95,
        "EXECUTABLE": 1.0,
        "CONDITIONALLY_READY": 0.82,
        "PLANNING_ONLY": 0.55,
        "BLOCKED": 0.38,
    }.get(memecoin_deploy_auth, 0.6)
    regime_mult = {
        "RISK_ON": 1.0,
        "MIXED": 0.8,
        "RISK_OFF": 0.55,
    }.get(memecoin_regime, 0.75)
    memecoins_score *= stance_mult * deploy_mult * regime_mult
    if memecoin_grad == "EARNING_TRUST":
        memecoins_score *= 1.08
        memecoin_reasons.append("Lane is still earning trust, which supports measured capital growth.")
    if int(queue_counts.get("proof_ready") or 0) > 0:
        memecoins_score *= 1.18
        memecoin_reasons.append("Proof-ready names are live in queue.")
    elif int(queue_counts.get("reinforced_pending") or 0) > 0:
        memecoins_score *= 1.08
        memecoin_reasons.append("Reinforced names are present, but not yet proof-ready.")
    if int(discovery_summary.get("promoted_count") or 0) > 0:
        memecoins_score *= 1.04
        memecoin_reasons.append("Discovery is now feeding promoted names into the queue.")
    if int(proof_slots.get("used_slots") or 0) >= int(proof_slots.get("total_slots") or 0) and int(proof_slots.get("total_slots") or 0) > 0:
        memecoins_score *= 0.8
        memecoin_reasons.append("Proof slot capacity is currently full.")
    if heat_state == "OVERHEATED":
        memecoins_score *= 0.72
        memecoin_reasons.append(f"Speculation heat is overheated ({heat_score:.0f}), so memecoin capital should stay disciplined.")
    elif heat_state == "HOT":
        memecoins_score *= 0.84
        memecoin_reasons.append(f"Speculation heat is hot ({heat_score:.0f}), so memecoin size should be tapered.")
    elif heat_state == "WARM" and heat_quality >= 60.0:
        memecoin_reasons.append("Speculation heat is active but quality is still holding up.")

    spot_score = 0.42 if (not spot_live and memecoin_spot_focus) else (0.18 if not spot_live else 0.8)
    spot_reasons = [
        f"Spot arm is {spot_mode.lower()}",
    ]
    if not spot_live:
        if memecoin_spot_focus:
            spot_reasons.append("Spot is part of the active planning focus even while execution remains paper.")
        else:
            spot_reasons.append("Spot is still waiting behind memecoin proof and should stay backgrounded.")
    else:
        spot_reasons.append("Spot is live and eligible for steady accumulation capital.")
    if spot_live:
        if heat_state == "OVERHEATED":
            spot_score *= 1.10
            spot_reasons.append("Overheated speculation makes steadier spot deployment relatively more attractive.")
        elif heat_state == "HOT" and heat_quality < 60.0:
            spot_score *= 1.06
            spot_reasons.append("Hot speculation with mixed quality slightly favors steadier spot deployment.")

    raw_scores = {
        "perps": max(0.05, perps_score),
        "memecoins": max(0.05, memecoins_score),
        "spot": max(0.02, spot_score),
    }
    total_score = sum(raw_scores.values()) or 1.0
    target_pct = {
        arm: round((score / total_score) * 100.0, 1)
        for arm, score in raw_scores.items()
    }
    pct_sum = round(sum(target_pct.values()), 1)
    if pct_sum != 100.0:
        target_pct["perps"] = round(target_pct["perps"] + (100.0 - pct_sum), 1)

    current_pct = {
        "perps": _current_share(arms, "perps", total_deployed),
        "memecoins": _current_share(arms, "memecoins", total_deployed),
        "spot": _current_share(arms, "spot", total_deployed),
    }

    arm_reason_map = {
        "perps": perps_reasons,
        "memecoins": memecoin_reasons,
        "spot": spot_reasons,
    }
    recommendations = []
    for arm in ("perps", "memecoins", "spot"):
        delta = round(target_pct[arm] - current_pct[arm], 1)
        recommendations.append({
            "arm": arm,
            "current_pct": current_pct[arm],
            "target_pct": target_pct[arm],
            "delta_pct": delta,
            "target_usd": round((target_pct[arm] / 100.0) * allocatable_base, 2),
            "action": _allocation_action(delta),
            "reasons": arm_reason_map[arm][:4],
        })

    current_dominant_book = "UNDEPLOYED"
    if total_deployed > 0:
        current_dominant_book = max(current_pct.items(), key=lambda item: item[1])[0].upper()

    top_target_arm, top_target_value = max(target_pct.items(), key=lambda item: item[1])
    posture = "BALANCED_HOLD"
    summary_note = "Portfolio capital is broadly balanced against current arm trust."
    if top_target_arm == "perps" and top_target_value >= 45 and not memecoin_spot_focus:
        posture = "LEAN_PERPS"
        summary_note = "Perps deserve the larger share while memecoin proof flow is still thin and spot remains non-live."
    elif top_target_arm == "memecoins" and top_target_value >= 35:
        posture = "LEAN_MEMECOINS"
        summary_note = "Memecoin lane deserves the larger share when proof trust and regime stay supportive."
    elif top_target_arm == "spot" and (spot_live or memecoin_spot_focus) and top_target_value >= 35:
        posture = "LEAN_SPOT"
        summary_note = "Spot deserves the larger planning share while it offers the cleanest steady deployment path."
    if spot_mode == "PAPER":
        summary_note += " Spot execution remains paper, but it is no longer backgrounded in planning."
    if memecoin_spot_focus:
        summary_note += " Perps stay as reference data while memecoin and spot quality drive the roadmap."
    if heat_state in ("HOT", "OVERHEATED"):
        summary_note += (
            f" Speculation heat is {heat_state.lower()} ({heat_score:.0f})"
            + (" and still rising." if heat_momentum == "RISING" else ".")
        )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "posture": posture,
        "note": summary_note,
        "allocatable_base_usd": allocatable_base,
        "inputs": {
            "perp_mode": perp_mode,
            "focus_mode": focus_mode,
            "risk_mode": str(risk_mode.get("mode") or "UNKNOWN"),
            "memecoin_mode": memecoin_mode,
            "memecoin_allocator_stance": memecoin_stance,
            "memecoin_regime_bucket": memecoin_regime,
            "memecoin_deployment_authority": memecoin_deploy_auth,
            "memecoin_graduation_state": memecoin_grad,
            "proof_ready": int(queue_counts.get("proof_ready") or 0),
            "reinforced_pending": int(queue_counts.get("reinforced_pending") or 0),
            "discovery_promoted": int(discovery_summary.get("promoted_count") or 0),
            "spot_mode": spot_mode,
            "dominant_book": current_dominant_book,
            "speculation_heat_state": heat_state,
            "speculation_heat_score": heat_score,
            "speculation_heat_quality": heat_quality,
            "speculation_heat_momentum": heat_momentum,
        },
        "recommendations": recommendations,
    }


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


@router.get("/api/portfolio/capital-allocation")
async def capital_allocation_ep(_: str = Depends(get_current_user)):
    """Cross-arm capital snapshot from live state plus capital_events ledger."""
    try:
        _ensure_engine_path()
        from utils.db import get_capital_allocation_snapshot  # type: ignore

        payload = await asyncio.to_thread(get_capital_allocation_snapshot)
        return payload
    except Exception as exc:
        log.warning("capital_allocation_ep error: %s", exc)
        return {
            "generated_at": None,
            "totals": {
                "deployed_usd": 0.0,
                "realized_pnl_usd": 0.0,
                "deploy_events_usd": 0.0,
                "release_events_usd": 0.0,
                "open_positions": 0,
            },
            "external_flows": {
                "deposits_usd": 0.0,
                "withdrawals_usd": 0.0,
                "net_external_flow_usd": 0.0,
            },
            "arms": [],
            "ledger": {
                "total_events": 0,
                "first_event_ts": None,
                "last_event_ts": None,
                "backfill_inserted_total": 0,
                "backfill_inserted_by_arm": {},
            },
            "recent_events": [],
            "error": str(exc),
        }


@router.get("/api/portfolio/allocation-recommendation")
async def allocation_recommendation_ep(_: str = Depends(get_current_user)):
    """Current-vs-target capital mix recommendation for the three arms."""
    try:
        _ensure_engine_path()
        from utils.db import get_recent_allocation_recommendations, record_allocation_recommendation  # type: ignore

        payload = await asyncio.to_thread(_build_allocation_recommendation)
        await asyncio.to_thread(record_allocation_recommendation, payload)
        payload["recent_history"] = await asyncio.to_thread(get_recent_allocation_recommendations, 8)
        payload["latest_change"] = _build_allocation_change_summary(payload["recent_history"])
        return payload
    except Exception as exc:
        log.warning("allocation_recommendation_ep error: %s", exc)
        return JSONResponse(
            status_code=500,
            content={
                "generated_at": None,
                "posture": "UNAVAILABLE",
                "note": "Allocation recommendation is unavailable right now.",
                "allocatable_base_usd": 0.0,
                "inputs": {},
                "recommendations": [],
                "recent_history": [],
                "latest_change": {
                    "changed": False,
                    "current_posture": None,
                    "previous_posture": None,
                    "changed_arms": [],
                },
                "error": str(exc),
            },
        )


@router.get("/api/health/status")
async def health_status_ep(_: str = Depends(get_current_user)):
    """System health watchdog status — DB, agents, scan freshness, F&G. (Patches 118+122)"""
    import json as _json
    _ensure_engine_path()
    from utils.db import get_conn as _gc, get_recent_watchdog_events as _get_watchdog_events  # type: ignore
    result: dict = {"status": "UNKNOWN", "ts": None, "issues": [], "warnings": [], "db": None}
    try:
        with _gc() as _conn:
            row = _conn.execute("SELECT value FROM kv_store WHERE key='system_health'").fetchone()
            if row:
                result = _json.loads(row[0])
            wd_row = _conn.execute("SELECT value FROM kv_store WHERE key='pipeline_watchdog_status'").fetchone()
            if wd_row and wd_row[0]:
                result["pipeline_watchdog"] = _json.loads(wd_row[0])
            alloc_row = _conn.execute("SELECT value FROM kv_store WHERE key='allocator_stream_status'").fetchone()
            if alloc_row and alloc_row[0]:
                result["allocator_stream"] = _json.loads(alloc_row[0])
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
    result["watchdog_events"] = _get_watchdog_events(watchdog="PIPELINE_EVAL", limit=8)
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
