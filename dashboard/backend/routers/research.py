"""
Research / Candidates workspace — continuation-aligned research lane

Routes:
  GET /api/research/candidates         — ranked lifecycle candidate list for operator research
  GET /api/research/pending-decisions  — unresolved ACT_SURFACE decision_journal rows (last 48h)
  GET /api/research/calibration        — read-only outcome calibration stats from surface_log

Reuses the lifecycle scoring formula and move-type classifier from home.py as a
self-contained copy (no cross-import). SQL broadened vs. home.py:
  - entry_window filter relaxed to also include RELOAD-state second-leg setups
    regardless of current entry_window (RELOAD + first_leg_confirmed + n_windows >= 3)
  - limit raised to 30 (home.py action queue caps at 5)
"""
from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import statistics
import sys
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends

from auth import get_current_user  # type: ignore

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/research", tags=["research"])


def _ensure_path() -> None:
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
    if root not in sys.path:
        sys.path.insert(0, root)


def _get_db_path() -> str:
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
    return os.path.join(root, "data_storage", "engine.db")


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        return {str(r[1]) for r in rows}
    except Exception:
        return set()


def _should_fetch_lunarcrush_candidate(item: dict) -> bool:
    if not str(item.get("symbol") or "").strip():
        return False
    if str(item.get("action") or "").upper() in ("ACT", "RESEARCH"):
        return True
    if int(item.get("scanner_case_count") or 0) > 0:
        return True
    return int(item.get("case_count") or 0) >= 3


def _should_fetch_lunarcrush_emerging(item: dict) -> bool:
    if not str(item.get("symbol") or "").strip():
        return False
    if int(item.get("scanner_case_count") or 0) > 0:
        return True
    watch_reason = str(item.get("watch_reason") or "").upper()
    if watch_reason == "EARLY_WATCH" and float(item.get("peak_score") or 0) >= 60:
        return True
    return False


def _load_lunarcrush_map(items: list[dict], predicate, max_symbols: int = 12) -> tuple[dict[str, dict], bool]:
    symbols = []
    for item in items:
        sym = str(item.get("symbol") or "").strip().upper()
        if not sym or not predicate(item):
            continue
        if sym not in symbols:
            symbols.append(sym)
    if not symbols:
        return {}, False
    try:
        from utils.lunarcrush_client import ensure_lunarcrush_tables, get_topic_intel, is_lunarcrush_configured  # type: ignore

        conn = sqlite3.connect(_get_db_path())
        conn.row_factory = sqlite3.Row
        ensure_lunarcrush_tables(conn)
        mapping = {sym: get_topic_intel(conn, sym) for sym in symbols[:max(1, int(max_symbols))]}
        conn.close()
        return mapping, bool(is_lunarcrush_configured())
    except Exception as exc:
        log.warning("[RESEARCH] lunarcrush enrichment error: %s", exc)
        return {}, False


def _lunarcrush_live_count(items: list[dict], field: str = "lunarcrush") -> int:
    return sum(
        1
        for item in items
        if isinstance(item.get(field), dict) and str(item[field].get("status") or "").upper() in {"LIVE", "PARTIAL"}
    )


# ── Scoring tables (continuation-first; mirrors current home.py intent) ──────

_FQ = {"STRONG": 100, "MODERATE": 60, "WEAK": 20, "TRAP": -999}
_EW = {"OPEN": 30, "CLOSING": 15, "CLOSED": -50}
_MP = {"RELOAD": 34, "MID": 18, "EARLY": 6, "IGNITION": 0, "EXTENDED": -20, "CHURN": -40}
_LS = {"REVIVAL": 14, "RELOAD": 10, "ACTIVE": 2, "COOLING": -4, "DORMANT": -10}
_AQ = {"STRONG": 20, "MODERATE": 10, "WEAK": 5, "NONE": 0}

_RESEARCH_BUDGET = {"HIGH": "15m", "MEDIUM": "5m", "QUICK_GLANCE": "30s"}


def _budget_to_priority(budget: str | None) -> str:
    b = str(budget or "").strip().lower()
    if b == "15m":
        return "HIGH"
    if b == "5m":
        return "MEDIUM"
    return "QUICK_GLANCE"


def _reinforcement_bucket(score: float | int | None, tags: str | None = None) -> str:
    """Shared reinforcement family for calibration / trust reporting."""
    val = float(score or 0.0)
    raw_tags = str(tags or "")
    tag_list = [t.strip() for t in raw_tags.replace("[", "").replace("]", "").replace('"', "").split(",") if t.strip()]
    if val >= 8.0 or len(tag_list) >= 2:
        return "STRONG_REINFORCED"
    if val >= 3.0 or bool(tag_list):
        return "REINFORCED"
    return "PLAIN"


def _capital_pressure_bucket(value: str | None) -> str:
    bucket = str(value or "UNKNOWN").strip().upper()
    if bucket in ("LOW", "MEDIUM", "HIGH"):
        return bucket
    return "UNKNOWN"


def _capital_regime_bucket(value: str | None) -> str:
    bucket = str(value or "UNKNOWN").strip().upper()
    if bucket in ("RISK_ON", "MIXED", "RISK_OFF"):
        return bucket
    return "UNKNOWN"


def _capital_mix_bucket(value: str | None) -> str:
    bucket = str(value or "UNKNOWN").strip().upper()
    if bucket in ("LIGHT", "SPOT_HEAVY", "PERP_HEAVY", "BALANCED"):
        return bucket
    return "UNKNOWN"


def _capital_allocator_stance(value: str | None) -> str:
    stance = str(value or "UNKNOWN").strip().upper()
    if stance in ("PAPER_ONLY", "DISABLED", "SATURATED", "TIGHT", "DISCIPLINED", "OPEN"):
        return stance
    return "UNKNOWN"


def _marginal_route(value: str | None) -> str:
    route = str(value or "UNKNOWN").strip().upper()
    if route in ("PERP_DEFENSE", "SPOT_DCA", "MEMECOIN_PROBE", "MEMECOIN_SCALE", "HOLD_CASH"):
        return route
    return "UNKNOWN"


def _allocator_posture(value: str | None) -> str:
    posture = str(value or "UNKNOWN").strip().upper()
    if posture in ("DEFENSIVE", "TIGHT", "DISCIPLINED", "OPEN"):
        return posture
    return "UNKNOWN"


def _dominant_book(value: str | None) -> str:
    book = str(value or "UNKNOWN").strip().upper()
    if book in ("UNDEPLOYED", "SPOT", "PERP", "BALANCED"):
        return book
    return "UNKNOWN"


def _queue_system(value: str | None) -> str:
    system = str(value or "UNKNOWN").strip().upper()
    if system in ("MEMECOINS", "SPOT", "PERP", "WHALE", "CONFLUENCE", "RESEARCH"):
        return system
    return "UNKNOWN"


def _queue_priority_bucket(value: str | None) -> str:
    priority = str(value or "UNKNOWN").strip().upper()
    if priority in ("URGENT", "NORMAL", "LOW", "BACKGROUND", "INFO"):
        return priority
    return "UNKNOWN"


def _marginal_route_authority(value: str | None) -> str:
    authority = str(value or "UNKNOWN").strip().upper()
    if authority in ("STACKED", "CONTEXT_BACKED", "UNPROVEN", "FRAGILE"):
        return authority
    return "UNKNOWN"


def _continuation_memory_authority(value: str | None) -> str:
    authority = str(value or "UNKNOWN").strip().upper()
    if authority in ("FORCEFUL", "BACKED", "TENTATIVE", "ADVERSE"):
        return authority
    return "UNKNOWN"


def _proof_stack_authority(value: str | None) -> str:
    authority = str(value or "UNKNOWN").strip().upper()
    if authority in ("FORCEFUL", "BACKED", "TENTATIVE", "ADVERSE"):
        return authority
    return "UNKNOWN"


def _promotion_authority(value: str | None) -> str:
    authority = str(value or "UNKNOWN").strip().upper()
    if authority in ("STACKED", "SUPPORTED", "TENTATIVE", "BLOCKED"):
        return authority
    return "UNKNOWN"


def _deployment_authority(value: str | None) -> str:
    authority = str(value or "UNKNOWN").strip().upper()
    if authority in ("EXECUTABLE", "CONDITIONALLY_READY", "PLANNING_ONLY", "BLOCKED"):
        return authority
    return "UNKNOWN"


def _decision_authority(value: str | None) -> str:
    authority = str(value or "UNKNOWN").strip().upper()
    if authority in ("STACKED", "SUPPORTED", "TENTATIVE", "BLOCKED"):
        return authority
    return "UNKNOWN"


def _reinforcement_authority(value: str | None) -> str:
    authority = str(value or "UNKNOWN").strip().upper()
    if authority in ("FORCEFUL", "BACKED", "TENTATIVE", "ABSENT"):
        return authority
    return "UNKNOWN"


def _marginal_route_family(row: dict) -> str:
    posture = _allocator_posture(row.get("allocator_posture"))
    dominant = _dominant_book(row.get("dominant_book"))
    route = _marginal_route(row.get("marginal_route"))
    return f"{posture}|{dominant}|{route}"


def _marginal_route_lane_family(row: dict) -> str:
    posture = _allocator_posture(row.get("allocator_posture"))
    system = _queue_system(row.get("queue_system"))
    route = _marginal_route(row.get("marginal_route"))
    return f"{posture}|{system}|{route}"


def _marginal_route_action_family(row: dict) -> str:
    posture = _allocator_posture(row.get("allocator_posture"))
    system = _queue_system(row.get("queue_system"))
    action = str(row.get("action") or "UNKNOWN").strip().upper() or "UNKNOWN"
    route = _marginal_route(row.get("marginal_route"))
    return f"{posture}|{system}|{action}|{route}"


def _marginal_route_priority_family(row: dict) -> str:
    posture = _allocator_posture(row.get("allocator_posture"))
    system = _queue_system(row.get("queue_system"))
    priority = _queue_priority_bucket(row.get("queue_priority_bucket"))
    route = _marginal_route(row.get("marginal_route"))
    return f"{posture}|{system}|{priority}|{route}"


def _capital_routing_family(row: dict) -> str:
    stance = _capital_allocator_stance(row.get("capital_allocator_stance"))
    route = _capital_route_bucket(row)
    marginal = _marginal_route(row.get("marginal_route"))
    return f"{stance}|{route}|{marginal}"


def _capital_headroom_bucket(value: str | None) -> str:
    bucket = str(value or "UNKNOWN").strip().upper()
    if bucket in ("EXHAUSTED", "THIN", "WORKABLE", "AMPLE"):
        return bucket
    return "UNKNOWN"


def _capital_route_bucket(row: dict) -> str:
    bucket = str(row.get("capital_route_bucket") or "").strip().upper()
    if bucket in ("LOCKED", "MICRO_PROBE_ONLY", "DISCIPLINED_PROBE", "SCALE_READY"):
        return bucket
    ready = str(row.get("capital_ready_state") or "UNKNOWN").strip().upper() or "UNKNOWN"
    headroom = _capital_headroom_bucket(row.get("capital_headroom_bucket"))
    if ready != "READY":
        return "LOCKED"
    if headroom in ("EXHAUSTED", "THIN"):
        return "MICRO_PROBE_ONLY" if headroom == "THIN" else "LOCKED"
    return "DISCIPLINED_PROBE"


def _capital_deployment_family(row: dict) -> str:
    posture = str(row.get("capital_posture") or "UNKNOWN").strip().upper() or "UNKNOWN"
    ready = str(row.get("capital_ready_state") or "UNKNOWN").strip().upper() or "UNKNOWN"
    pressure = _capital_pressure_bucket(row.get("capital_pressure_bucket"))
    regime = _capital_regime_bucket(row.get("capital_regime_bucket"))
    return f"{posture}|{ready}|{pressure}|{regime}"


def _capital_intensity_bucket(value: float | int | str | None) -> str:
    try:
        val = float(value or 0.0)
    except Exception:
        val = 0.0
    if val <= 0:
        return "ZERO"
    if val < 6:
        return "MICRO"
    if val < 12:
        return "SMALL"
    if val < 20:
        return "MEDIUM"
    return "LARGE"


def _capital_window_bucket(row: dict) -> str:
    ready = str(row.get("capital_ready_state") or "UNKNOWN").strip().upper() or "UNKNOWN"
    intensity = _capital_intensity_bucket(row.get("capital_suggested_entry_usd"))
    if ready != "READY":
        return "CLOSED"
    if intensity in ("ZERO", "MICRO"):
        return "MICRO_WINDOW"
    if intensity == "SMALL":
        return "LIMITED_WINDOW"
    return "OPEN_WINDOW"


def _fresh_reinforced_family(row: dict) -> str:
    """More precise continuation family used by late-stage calibration/trust."""
    arch = str(row.get("continuation_archetype") or row.get("move_type") or "UNKNOWN").strip() or "UNKNOWN"
    fresh = str(row.get("freshness_bucket") or "UNKNOWN").strip().upper() or "UNKNOWN"
    reinf = str(row.get("reinforcement_bucket") or "PLAIN").strip().upper() or "PLAIN"
    return f"{arch}|{fresh}|{reinf}"


def _fresh_catalyst_bucket(value: str | None) -> str:
    bucket = str(value or "UNKNOWN").strip().upper()
    if bucket in ("REACTIVATION_DRIVEN", "SCAN_MOMO", "PERSISTENCE_DRIVEN", "THIN_CATALYST"):
        return bucket
    return "UNKNOWN"


def _fresh_discovery_authority(value: str | None) -> str:
    authority = str(value or "UNKNOWN").strip().upper()
    if authority in ("FORCEFUL", "BACKED", "TENTATIVE", "ADVERSE"):
        return authority
    return "UNKNOWN"


def _research_candidates_from_live_memecoin_engine(limit: int) -> list[dict]:
    """Fallback candidate projection from the live memecoin continuation engine.

    Research should not depend exclusively on an abandoned lifecycle DB path.
    When the historical SQL source is empty, inherit the current memecoin
    research pool so the workspace stays aligned with the actual continuation
    engine the rest of the system uses.
    """
    try:
        from dashboard.backend.routers.memecoins import next_best_action_ep  # type: ignore

        nba = asyncio.run(next_best_action_ep(_="system"))
    except Exception as e:
        log.warning("[RESEARCH] memecoin-engine fallback unavailable: %s", e)
        return []

    pool: list[dict] = []
    candidate = nba.get("candidate")
    if isinstance(candidate, dict):
        pool.append(candidate)
    pool.extend([r for r in (nba.get("research_pool") or []) if isinstance(r, dict)])

    seen: set[str] = set()
    out: list[dict] = []
    for c in pool:
        sym = str(c.get("symbol") or "").upper()
        if not sym or sym in seen:
            continue
        seen.add(sym)

        triage = str(c.get("triage_state") or "")
        if triage == "DO_NOT_TOUCH":
            continue

        action = {
            "INVESTIGATE_NOW": "RESEARCH",
            "MONITOR": "MONITOR",
            "BLOCKED": "MONITOR",
        }.get(triage, "MONITOR")
        budget = str(c.get("research_budget") or "30s")
        priority = _budget_to_priority(budget)
        move_type = (
            c.get("move_type")
            or c.get("candidate_origin")
            or c.get("focus_context")
            or "CONTINUATION"
        )

        triage_tags: list[str] = []
        if c.get("focus_context") == "FRESH_QUALIFIED":
            triage_tags.append("above-floor continuation")
        if c.get("freshness_bucket"):
            triage_tags.append(str(c.get("freshness_bucket")).lower())
        if c.get("triage_reason"):
            triage_tags.append(str(c.get("triage_reason")))

        out.append({
            "symbol": sym,
            "mint": c.get("mint"),
            "score": round(float(c.get("score") or 0.0), 1),
            "action": action,
            "entry_window": str(c.get("entry_window") or "CLOSED"),
            "fuel_quality": str(c.get("fuel_quality") or "WEAK"),
            "move_phase": str(c.get("move_phase") or "RELOAD"),
            "lifecycle_state": str(c.get("focus_context") or ""),
            "n_windows": 0,
            "best_return_pct": round(float(c.get("avg_return") or 0.0), 1),
            "move_type": str(move_type),
            "continuation_archetype": str(c.get("continuation_archetype") or move_type),
            "freshness_bucket": str(c.get("freshness_bucket") or "UNKNOWN"),
            "research_priority": priority,
            "research_budget": budget,
            "triage_tags": triage_tags,
            "is_second_leg": str(c.get("move_phase") or "") == "RELOAD",
            "vacc": 0.0,
            "first_leg_confirmed": bool(c.get("first_leg_confirmed")),
            "multi_leg_confirmed": False,
            "mcap_at_scan": None,
        })

    out.sort(key=lambda x: x["score"], reverse=True)
    return out[: min(limit, 50)]


def _classify_move_type(
    mp: str,  ls: str,  flc: int, mlc: int, surv: int,
    nw: int,  hrslw: float, bp: float, pd: float,
    liq_floor: float, liq_current: float, vacc: float,
) -> tuple:
    """Continuation-first move archetype classifier for the Research lane."""
    liq_ratio = round(liq_current / liq_floor, 2) if liq_floor > 0 else 1.0

    # Rule 1: RELOAD_CONTINUATION — already-real name resetting for another leg
    if mp == "RELOAD" and flc and nw >= 2 and hrslw < 96.0:
        tags = [
            f"{nw}w",
            "first+multi leg" if mlc else "first leg",
            f"{hrslw:.0f}h ago",
        ]
        return "RELOAD_CONTINUATION", "MEDIUM", tags

    # Rule 2: REVIVAL_CONTINUATION — revival state with proof of prior structure
    if ls == "REVIVAL" and flc and nw >= 2 and hrslw < 120.0:
        tags = [
            f"{nw}w",
            "revival",
            "first+multi leg" if mlc else "first leg",
        ]
        return "REVIVAL_CONTINUATION", "MEDIUM", tags

    # Rule 3: DORMANT_SECOND_LEG — dormant but liq has expanded, confirmed legs
    if ls == "DORMANT" and flc and nw >= 3 and liq_ratio >= 2.0:
        tags = [
            f"{nw}w",
            "first+multi leg" if mlc else "first leg",
            f"{liq_ratio:.1f}x liq expansion",
            f"{hrslw:.0f}h dormant",
        ]
        return "DORMANT_SECOND_LEG", "MEDIUM", tags

    # Rule 4: DEEP_PULLBACK — significant peak with confirmed first leg
    if bp >= 50.0 and pd >= 25.0 and flc:
        tags = [f"{bp:.0f}% peak return", f"{pd:.0f}% pullback", f"{nw}w"]
        return "DEEP_PULLBACK", "MEDIUM", tags

    # Rule 5: STALE_MOMENTUM — low history, no legs, very stale
    if nw <= 1 and not flc and hrslw > 200.0:
        tags = ["1 window", "no legs confirmed", f"{hrslw:.0f}h stale"]
        return "STALE_MOMENTUM", "QUICK_GLANCE", tags

    # Rule 6: STRUCTURAL_PASS — passes gates but no strong continuation archetype
    tags = []
    if nw > 1:
        tags.append(f"{nw}w")
    if flc:
        tags.append("first leg" + (" + multi" if mlc else ""))
    if liq_ratio > 1.3:
        tags.append(f"{liq_ratio:.1f}x liq")
    if vacc > 2.0:
        tags.append(f"vacc {vacc:.1f}x")
    return "STRUCTURAL_PASS", "QUICK_GLANCE", tags or ["gate pass"]


def _emerging_watch_reason(case_count: int, mint_count: int, scanner_case_count: int) -> str:
    if scanner_case_count > 0 and mint_count >= 3:
        return "REPEATED_RELAUNCH_WITH_SCANNER_HISTORY"
    if scanner_case_count > 0:
        return "SCANNER_HISTORY"
    if mint_count >= 3:
        return "SYMBOL_REUSE"
    if case_count >= 3:
        return "REPEAT_DISCOVERY"
    return "EARLY_WATCH"


# ── Endpoint ──────────────────────────────────────────────────────────────────

@router.get("/candidates")
def get_candidates(limit: int = 30, _user=Depends(get_current_user)):
    """Ranked lifecycle candidate list for the Research workspace.

    Broader than the Home action queue:
    - Includes RELOAD-state second-leg setups regardless of entry_window
    - Limit default 30 (vs. 5 for Home queue)
    - Returns is_second_leg flag so frontend can visually mark them
    """
    _ensure_path()
    try:
        now = datetime.now(timezone.utc)
        cutoff_30d = (now - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
        cutoff_14d = (now - timedelta(days=14)).strftime("%Y-%m-%d %H:%M:%S")
        corr_start = (now - timedelta(days=14)).strftime("%Y-%m-%d")
        conn = sqlite3.connect(_get_db_path())
        conn.row_factory = sqlite3.Row
        try:
            from utils.arkham_client import ensure_arkham_tables  # type: ignore
            ensure_arkham_tables(conn)
        except Exception:
            pass
        mso_cols = _table_columns(conn, "memecoin_signal_outcomes")
        rows = conn.execute("""
            SELECT
                lc.symbol,
                lc.mint,
                lc.entry_window,
                lc.fuel_quality,
                lc.move_phase,
                lc.lifecycle_state,
                lc.vol_acc_current,
                lc.liq_current,
                lc.liq_floor,
                lc.first_leg_confirmed,
                lc.multi_leg_confirmed,
                lc.survivor_confirmed,
                lc.n_windows,
                lc.best_return_pct,
                lc.pullback_depth_pct,
                lc.hours_since_last_window,
                mso.attention_quality,
                mso.boost_active,
                mc_scan.mcap_at_scan AS mcap_at_scan
            FROM symbol_lifecycle lc
            LEFT JOIN (
                SELECT symbol, attention_quality, boost_active
                FROM memecoin_signal_outcomes
                WHERE source = 'DISCOVERY'
                GROUP BY symbol
                HAVING MAX(scanned_at)
            ) mso ON mso.symbol = lc.symbol
            LEFT JOIN (
                SELECT symbol, mcap_at_scan
                FROM memecoin_signal_outcomes
                WHERE source = 'SCANNER'
                GROUP BY symbol
                HAVING MAX(scanned_at)
            ) mc_scan ON mc_scan.symbol = lc.symbol
            WHERE lc.fuel_quality NOT IN ('TRAP')
              AND lc.move_phase NOT IN ('CHURN', 'EXTENDED')
              AND (
                lc.entry_window IN ('OPEN', 'CLOSING')
                OR (
                    lc.lifecycle_state = 'RELOAD'
                    AND lc.first_leg_confirmed = 1
                    AND lc.n_windows >= 3
                )
              )
        """).fetchall()
        perf_rows = conn.execute("""
            SELECT symbol,
                   COUNT(*) AS n,
                   AVG(return_24h_pct) AS avg_return
            FROM memecoin_signal_outcomes
            WHERE status = 'COMPLETE' AND return_24h_pct IS NOT NULL
            GROUP BY symbol
        """).fetchall()
        scan_rows = conn.execute("""
            SELECT symbol,
                   COUNT(*)        AS cnt_30d,
                   MAX(scanned_at) AS last_scan
            FROM memecoin_signal_outcomes
            WHERE scanned_at >= ?
            GROUP BY symbol
        """, (cutoff_30d,)).fetchall()
        last_scan_rows = conn.execute("""
            SELECT symbol, mcap_at_scan, liquidity_usd, rug_label,
                   top_holder_pct, top5_holder_pct,
                   volume_24h, token_age_days, holder_quality_level, scanned_at
            FROM memecoin_signal_outcomes
            WHERE source = 'SCANNER'
              AND scanned_at = (
                  SELECT MAX(scanned_at) FROM memecoin_signal_outcomes m2
                  WHERE m2.symbol = memecoin_signal_outcomes.symbol
                    AND m2.source = 'SCANNER'
              )
        """).fetchall()
        corr_rows = conn.execute("""
            SELECT symbol, return_24h_pct
            FROM memecoin_signal_outcomes
            WHERE status = 'COMPLETE'
              AND return_24h_pct IS NOT NULL
              AND scanned_at >= ?
        """, (corr_start,)).fetchall()
        arkham_rows = conn.execute("""
            SELECT token_mint, top_entity_name, top_entity_type, top_entity_pct_of_cap,
                   top_flow_entity_name, top_flow_entity_type, net_flow_usd,
                   signal_quality, signal_score, status, updated_ts_utc
            FROM arkham_token_intel
        """).fetchall()
        scanner_case_sql = (
            "SUM(CASE WHEN source = 'SCANNER' THEN 1 ELSE 0 END) AS scanner_case_count"
            if "source" in mso_cols
            else "0 AS scanner_case_count"
        )
        case_rows = conn.execute(f"""
            SELECT
                symbol,
                COUNT(*) AS case_count,
                COUNT(DISTINCT mint) AS mint_count,
                {scanner_case_sql},
                MAX(scanned_at) AS last_case_ts
            FROM memecoin_signal_outcomes
            GROUP BY symbol
        """).fetchall()
        conn.close()
    except Exception as e:
        log.warning("[RESEARCH] candidates query error: %s", e)
        _fallback = _research_candidates_from_live_memecoin_engine(limit)
        return {"candidates": _fallback, "error": str(e), "total": len(_fallback),
                "second_leg_count": sum(1 for r in _fallback if r["is_second_leg"]),
                "act_count": sum(1 for r in _fallback if r["action"] == "ACT"),
                "research_count": sum(1 for r in _fallback if r["action"] == "RESEARCH"),
                "monitor_count": sum(1 for r in _fallback if r["action"] == "MONITOR"),
                "data_source": "memecoin_engine_fallback",
                "fallback_reason": "research_sql_unavailable"}

    if not rows:
        _fallback = _research_candidates_from_live_memecoin_engine(limit)
        return {"candidates": _fallback, "total": len(_fallback),
                "second_leg_count": sum(1 for r in _fallback if r["is_second_leg"]),
                "act_count": sum(1 for r in _fallback if r["action"] == "ACT"),
                "research_count": sum(1 for r in _fallback if r["action"] == "RESEARCH"),
                "monitor_count": sum(1 for r in _fallback if r["action"] == "MONITOR"),
                "data_source": "memecoin_engine_fallback",
                "fallback_reason": "research_sql_empty"}

    try:
        perf_map = {
            r["symbol"]: {
                "n": int(r["n"] or 0),
                "avg": float(r["avg_return"] or 0) if r["avg_return"] is not None else 0.0,
            }
            for r in perf_rows
        }
        scan_stats = {
            r["symbol"]: {"cnt": int(r["cnt_30d"] or 0), "last": r["last_scan"]}
            for r in scan_rows
        }
        last_scan_sig = {
            r["symbol"]: {
                "mcap_usd":             float(r["mcap_at_scan"] or 0),
                "liquidity_usd":        float(r["liquidity_usd"] or 0),
                "rug_label":            r["rug_label"],
                "top_holder_pct":       float(r["top_holder_pct"] or 0),
                "top5_holder_pct":      float(r["top5_holder_pct"] or 0),
                "volume_24h":           float(r["volume_24h"] or 0),
                "token_age_days":       float(r["token_age_days"] or 0),
                "holder_quality_level": r["holder_quality_level"],
                "scanned_at":           r["scanned_at"],
            }
            for r in last_scan_rows
        }
        arkham_by_mint = {
            r["token_mint"]: {
                "status": r["status"],
                "signal_quality": r["signal_quality"],
                "signal_score": float(r["signal_score"] or 0),
                "top_entity_name": r["top_entity_name"],
                "top_entity_type": r["top_entity_type"],
                "top_entity_pct_of_cap": float(r["top_entity_pct_of_cap"] or 0),
                "top_flow_entity_name": r["top_flow_entity_name"],
                "top_flow_entity_type": r["top_flow_entity_type"],
                "net_flow_usd": float(r["net_flow_usd"] or 0),
                "updated_ts_utc": r["updated_ts_utc"],
            }
            for r in arkham_rows
        }
        case_stats = {
            r["symbol"]: {
                "case_count": int(r["case_count"] or 0),
                "mint_count": int(r["mint_count"] or 0),
                "scanner_case_count": int(r["scanner_case_count"] or 0),
                "last_case_ts": r["last_case_ts"],
            }
            for r in case_rows
        }
    except Exception as e:
        log.warning("[RESEARCH] candidates map-build error: %s", e)
        _fallback = _research_candidates_from_live_memecoin_engine(limit)
        return {"candidates": _fallback, "error": str(e), "total": len(_fallback),
                "second_leg_count": 0, "act_count": 0,
                "research_count": sum(1 for r in _fallback if r["action"] == "RESEARCH"),
                "monitor_count": sum(1 for r in _fallback if r["action"] == "MONITOR"),
                "data_source": "memecoin_engine_fallback",
                "fallback_reason": "research_sql_map_error"}
    corr_by_sym: dict[str, list[float]] = {}
    for cr in corr_rows:
        corr_by_sym.setdefault(cr["symbol"], []).append(float(cr["return_24h_pct"]))
    corr_tier: dict[str, str] = {}
    for sym, rets in corr_by_sym.items():
        cn = len(rets)
        avg = round(sum(rets) / cn, 1)
        if cn >= 2 and avg < -30.0:
            corr_tier[sym] = "CASUALTY"
        elif cn >= 2 and avg > -5.0 and (sum(1 for rr in rets if rr > -20.0) / cn) >= 0.70:
            corr_tier[sym] = "SURVIVOR"
        elif cn >= 2:
            corr_tier[sym] = "TRACKER"
        else:
            corr_tier[sym] = "UNRATED"

    def _coin_quality(sig: dict) -> str:
        rug   = str(sig.get("rug_label") or "").upper()
        top1  = float(sig.get("top_holder_pct") or 0)
        top5  = float(sig.get("top5_holder_pct") or 0)
        liq   = float(sig.get("liquidity_usd") or 0)
        vol24 = float(sig.get("volume_24h") or 0)
        age   = float(sig.get("token_age_days") or 0)
        hq    = str(sig.get("holder_quality_level") or "").upper()
        vl    = (vol24 / liq) if liq > 1000 else 0.0
        if rug == "DANGER" or top1 >= 80 or top5 >= 90 or (0 < liq < 30_000) or vl > 25:
            return "WEAK"
        if rug in ("WARN", "UNKNOWN") or (50 <= top1 < 80) or (70 <= top5 < 90) or (0 < liq < 75_000) or (0 < age < 7) or (10 < vl <= 25) or hq == "RISKY":
            return "QUESTIONABLE"
        return "CLEAR"

    results = []
    for r in rows:
        sym = r["symbol"]
        stat = scan_stats.get(sym, {})
        last = (stat.get("last") or "")
        sig = last_scan_sig.get(sym)
        perf = perf_map.get(sym, {"n": 0, "avg": 0.0})
        cs = case_stats.get(sym, {})
        if int(stat.get("cnt", 0) or 0) < 3:
            continue
        if not last or last < cutoff_14d:
            continue
        if not sig:
            continue
        if float(sig.get("mcap_usd") or 0) < 1_500_000:
            continue
        if float(sig.get("liquidity_usd") or 0) < 50_000:
            continue
        if _coin_quality(sig) == "WEAK":
            continue
        if corr_tier.get(sym) == "CASUALTY":
            continue
        if int(perf.get("n", 0) or 0) >= 15 and float(perf.get("avg", 0.0) or 0.0) < -10:
            continue

        fq    = r["fuel_quality"]    or "WEAK"
        ew    = r["entry_window"]    or "CLOSED"
        mp    = r["move_phase"]      or "RELOAD"
        ls    = r["lifecycle_state"] or "COOLING"
        aq    = r["attention_quality"] or "NONE"
        flc   = int(r["first_leg_confirmed"]  or 0)
        mlc   = int(r["multi_leg_confirmed"]  or 0)
        surv  = int(r["survivor_confirmed"]   or 0)
        nw    = int(r["n_windows"]            or 0)
        boost = int(r["boost_active"]         or 0)
        vacc  = r["vol_acc_current"]
        hrslw = float(r["hours_since_last_window"] if r["hours_since_last_window"] is not None else 9999)
        bp    = float(r["best_return_pct"]    or 0)
        pd    = float(r["pullback_depth_pct"] or 0)
        liq_f = float(r["liq_floor"]          or 0)
        liq_c = float(r["liq_current"]        or 0)

        _vacc_boost = round(min(float(vacc or 0), 10.0) * 1.5) if vacc else 0

        # Patch 279: mcap promotion modifier — aligns Research ranking with post-floor active universe.
        # Unknown mcap → 0 (neutral, don't penalise missing data).
        _mc = float(r["mcap_at_scan"] or 0)
        if _mc <= 0:
            _mcap_mod = 0
        elif _mc < 1_500_000:
            _mcap_mod = -20   # below scanner floor — deprioritise
        elif _mc < 3_000_000:
            _mcap_mod = 7     # scanner 10pt band
        elif _mc < 10_000_000:
            _mcap_mod = 15    # scanner 15pt peak — highest operator priority
        elif _mc < 25_000_000:
            _mcap_mod = 5     # scanner 7pt band
        elif _mc < 50_000_000:
            _mcap_mod = 3     # scanner 4pt band
        else:
            _mcap_mod = 0

        score = (
            _FQ.get(fq, 0)
            + _EW.get(ew, 0)
            + _MP.get(mp, 0)
            + _LS.get(ls, 0)
            + _AQ.get(aq, 0)
            + (10 if flc else 0)
            + (5  if boost else 0)
            + _vacc_boost
            + _mcap_mod
        )

        # Continuation-first roadmap: do not let same-day/lightly-formed ignition
        # names enter the Research lane just because they passed coarse score gates.
        if (
            hrslw < 48.0
            and flc == 0
            and nw < 2
            and mp in ("IGNITION", "EARLY")
            and ls not in ("RELOAD", "REVIVAL")
        ):
            continue

        if score < 60:
            continue

        # is_second_leg: RELOAD lifecycle + confirmed first leg + multi-window history
        is_sl = bool(ls == "RELOAD" and flc == 1 and nw >= 3)

        if score >= 150:
            action = "ACT"
        elif score >= 100:
            action = "RESEARCH"
        else:
            action = "MONITOR"

        move_type, rp, triage_tags = _classify_move_type(
            mp, ls, flc, mlc, surv, nw, hrslw, bp, pd, liq_f, liq_c, float(vacc or 0)
        )
        budget = _RESEARCH_BUDGET[rp]

        results.append({
            "symbol":             r["symbol"],
            "mint":               r["mint"],          # Patch 266: mint for surface_log identity
            "score":              score,
            "action":             action,
            "entry_window":       ew,
            "fuel_quality":       fq,
            "move_phase":         mp,
            "lifecycle_state":    ls,
            "n_windows":          nw,
            "best_return_pct":    round(bp, 1),
            "move_type":          move_type,
            "continuation_archetype": move_type,
            "research_priority":  rp,
            "research_budget":    budget,
            "triage_tags":        triage_tags,
            "is_second_leg":      is_sl,
            "vacc":               round(float(vacc or 0), 2),
            "first_leg_confirmed":  bool(flc),
            "multi_leg_confirmed":  bool(mlc),
            # Patch 281: expose mcap for operator visibility (modifier already baked into score)
            "mcap_at_scan":       _mc if _mc > 0 else None,
            "arkham":             arkham_by_mint.get(r["mint"]),
            "case_count":         int(cs.get("case_count") or 0),
            "mint_count":         int(cs.get("mint_count") or 0),
            "scanner_case_count": int(cs.get("scanner_case_count") or 0),
            "last_case_ts":       cs.get("last_case_ts"),
        })

    results.sort(key=lambda x: x["score"], reverse=True)
    capped = results[:min(limit, 50)]
    lunarcrush_by_symbol, lunarcrush_configured = _load_lunarcrush_map(capped, _should_fetch_lunarcrush_candidate, max_symbols=6)
    for item in capped:
        item["lunarcrush"] = lunarcrush_by_symbol.get(item["symbol"])

    if not capped:
        _fallback = _research_candidates_from_live_memecoin_engine(limit)
        return {
            "candidates": _fallback,
            "total": len(_fallback),
            "second_leg_count": sum(1 for r in _fallback if r["is_second_leg"]),
            "act_count": sum(1 for r in _fallback if r["action"] == "ACT"),
            "research_count": sum(1 for r in _fallback if r["action"] == "RESEARCH"),
            "monitor_count": sum(1 for r in _fallback if r["action"] == "MONITOR"),
            "data_source": "memecoin_engine_fallback",
            "fallback_reason": "research_sql_no_ranked_candidates",
            "lunarcrush_configured": False,
            "lunarcrush_enriched_count": 0,
        }

    # Patch 263: surface log — snapshot classification labels at generation time,
    # then lazily fill outcomes for rows that are now >= 24h old.
    try:
        from utils.surface_log import surface_log_write, surface_log_fill_outcomes  # type: ignore
        surface_log_write(capped, "RESEARCH_PAGE")
        surface_log_fill_outcomes()
    except Exception:
        pass

    return {
        "candidates":      capped,
        "total":           len(results),
        "second_leg_count": sum(1 for r in results if r["is_second_leg"]),
        "act_count":       sum(1 for r in capped if r["action"] == "ACT"),
        "research_count":  sum(1 for r in capped if r["action"] == "RESEARCH"),
        "monitor_count":   sum(1 for r in capped if r["action"] == "MONITOR"),
        "data_source":     "research_sql",
        "lunarcrush_configured": lunarcrush_configured,
        "lunarcrush_enriched_count": _lunarcrush_live_count(capped),
    }


@router.get("/candidate-history")
def get_candidate_history(symbol: str, limit: int = 25, _user=Depends(get_current_user)):
    """Underlying memecoin outcome rows for a single research symbol.

    Research candidates are intentionally symbol-level and deduped. This endpoint
    exposes the raw case history behind a collapsed candidate so the UI can show
    why a symbol like VDOR appears once despite many underlying cases/mints.
    """
    _ensure_path()
    symbol = str(symbol or "").strip().upper()
    if not symbol:
        return {"symbol": "", "cases": [], "total": 0, "mint_count": 0, "scanner_case_count": 0}

    try:
        conn = sqlite3.connect(_get_db_path())
        conn.row_factory = sqlite3.Row
        cols = _table_columns(conn, "memecoin_signal_outcomes")
        source_sql = "source" if "source" in cols else "NULL AS source"
        status_sql = "status" if "status" in cols else "NULL AS status"
        returns_sql = (
            "return_1h_pct, return_4h_pct, return_24h_pct"
            if {"return_1h_pct", "return_4h_pct", "return_24h_pct"}.issubset(cols)
            else "NULL AS return_1h_pct, NULL AS return_4h_pct, NULL AS return_24h_pct"
        )

        rows = conn.execute(f"""
            SELECT
                id,
                symbol,
                mint,
                scanned_at,
                score,
                {source_sql},
                {status_sql},
                price_at_scan,
                mcap_at_scan,
                liquidity_usd,
                rug_label,
                holder_quality_level,
                {returns_sql}
            FROM memecoin_signal_outcomes
            WHERE symbol = ?
            ORDER BY scanned_at DESC, id DESC
            LIMIT ?
        """, (symbol, min(max(limit, 1), 100))).fetchall()

        scanner_case_sql = (
            "SUM(CASE WHEN source = 'SCANNER' THEN 1 ELSE 0 END)"
            if "source" in cols
            else "0"
        )
        summary = conn.execute(f"""
            SELECT
                COUNT(*) AS total,
                COUNT(DISTINCT mint) AS mint_count,
                {scanner_case_sql} AS scanner_case_count,
                MIN(scanned_at) AS first_seen,
                MAX(scanned_at) AS last_seen
            FROM memecoin_signal_outcomes
            WHERE symbol = ?
        """, (symbol,)).fetchone()
        conn.close()
    except Exception as e:
        log.warning("[RESEARCH] candidate-history query error for %s: %s", symbol, e)
        return {"symbol": symbol, "cases": [], "total": 0, "mint_count": 0, "scanner_case_count": 0, "error": str(e)}

    return {
        "symbol": symbol,
        "total": int(summary["total"] or 0) if summary else 0,
        "mint_count": int(summary["mint_count"] or 0) if summary else 0,
        "scanner_case_count": int(summary["scanner_case_count"] or 0) if summary else 0,
        "first_seen": summary["first_seen"] if summary else None,
        "last_seen": summary["last_seen"] if summary else None,
        "cases": [dict(r) for r in rows],
    }


@router.get("/emerging-cases")
def get_emerging_cases(limit: int = 25, _user=Depends(get_current_user)):
    """Lower-authority research feed for repeated / relaunching symbols.

    This intentionally sits below the strict main candidate list. It surfaces
    symbols with repeated case activity, many distinct mints, or any scanner
    history so the Research page can feel alive without weakening the main
    candidate filters.
    """
    _ensure_path()
    try:
        cutoff_14d = (datetime.now(timezone.utc) - timedelta(days=14)).strftime("%Y-%m-%d %H:%M:%S")
        conn = sqlite3.connect(_get_db_path())
        conn.row_factory = sqlite3.Row
        cols = _table_columns(conn, "memecoin_signal_outcomes")

        source_col = "source" if "source" in cols else "NULL"
        status_col = "status" if "status" in cols else "NULL"
        scanner_case_sql = (
            "SUM(CASE WHEN source = 'SCANNER' THEN 1 ELSE 0 END)"
            if "source" in cols else
            "0"
        )
        discovery_case_sql = (
            "SUM(CASE WHEN source = 'DISCOVERY' THEN 1 ELSE 0 END)"
            if "source" in cols else
            "0"
        )

        rows = conn.execute(f"""
            WITH recent AS (
                SELECT
                    id,
                    symbol,
                    mint,
                    scanned_at,
                    score,
                    {source_col} AS source,
                    {status_col} AS status,
                    liquidity_usd,
                    mcap_at_scan,
                    rug_label
                FROM memecoin_signal_outcomes
                WHERE scanned_at >= ?
            ),
            agg AS (
                SELECT
                    symbol,
                    COUNT(*) AS case_count,
                    COUNT(DISTINCT mint) AS mint_count,
                    {scanner_case_sql} AS scanner_case_count,
                    {discovery_case_sql} AS discovery_case_count,
                    MAX(scanned_at) AS last_case_ts,
                    ROUND(MAX(score), 1) AS peak_score
                FROM recent
                GROUP BY symbol
            ),
            latest AS (
                SELECT
                    *,
                    ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY scanned_at DESC, id DESC) AS rn
                FROM recent
            )
            SELECT
                a.symbol,
                a.case_count,
                a.mint_count,
                a.scanner_case_count,
                a.discovery_case_count,
                a.last_case_ts,
                a.peak_score,
                l.mint AS latest_mint,
                l.source AS latest_source,
                l.status AS latest_status,
                ROUND(COALESCE(l.score, 0), 1) AS latest_score,
                l.liquidity_usd,
                l.mcap_at_scan,
                l.rug_label
            FROM agg a
            JOIN latest l
              ON l.symbol = a.symbol AND l.rn = 1
            WHERE a.case_count >= 2 OR a.mint_count >= 2 OR a.scanner_case_count >= 1
            ORDER BY
                a.scanner_case_count DESC,
                a.mint_count DESC,
                a.case_count DESC,
                a.last_case_ts DESC
            LIMIT ?
        """, (cutoff_14d, min(max(limit, 1), 100))).fetchall()
        conn.close()
    except Exception as e:
        log.warning("[RESEARCH] emerging-cases query error: %s", e)
        return {"cases": [], "total": 0, "error": str(e)}

    items = []
    for r in rows:
        case_count = int(r["case_count"] or 0)
        mint_count = int(r["mint_count"] or 0)
        scanner_case_count = int(r["scanner_case_count"] or 0)
        items.append({
            "symbol": r["symbol"],
            "latest_mint": r["latest_mint"],
            "case_count": case_count,
            "mint_count": mint_count,
            "scanner_case_count": scanner_case_count,
            "discovery_case_count": int(r["discovery_case_count"] or 0),
            "last_case_ts": r["last_case_ts"],
            "latest_source": r["latest_source"],
            "latest_status": r["latest_status"],
            "latest_score": float(r["latest_score"] or 0),
            "peak_score": float(r["peak_score"] or 0),
            "liquidity_usd": float(r["liquidity_usd"] or 0),
            "mcap_at_scan": float(r["mcap_at_scan"] or 0) if r["mcap_at_scan"] is not None else None,
            "rug_label": r["rug_label"],
            "watch_reason": _emerging_watch_reason(case_count, mint_count, scanner_case_count),
        })

    lunarcrush_by_symbol, lunarcrush_configured = _load_lunarcrush_map(items, _should_fetch_lunarcrush_emerging, max_symbols=4)
    for item in items:
        item["lunarcrush"] = lunarcrush_by_symbol.get(item["symbol"])

    return {
        "cases": items,
        "total": len(items),
        "lunarcrush_configured": lunarcrush_configured,
        "lunarcrush_enriched_count": _lunarcrush_live_count(items),
    }


# ── Pending decisions ──────────────────────────────────────────────────────────

@router.get("/pending-decisions")
def get_pending_decisions(_user=Depends(get_current_user)):
    """Unresolved ACT_SURFACE decision_journal entries from the last 48h.

    Sorted by created_ts descending (newest / most urgent first).
    Only returns rows where resolution_status = 'PENDING' and
    source_surface = 'ACT_SURFACE'.
    """
    _ensure_path()
    try:
        conn = sqlite3.connect(_get_db_path())
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT
                id,
                created_ts,
                COALESCE(last_seen_ts, created_ts) AS last_seen_ts,
                symbol,
                mint,
                COALESCE(surface_count, 1) AS surface_count,
                recommended_action,
                priority,
                reason,
                snapshot_json,
                operator_decision,
                operator_note,
                resolution_status
            FROM decision_journal
            WHERE source_surface     = 'ACT_SURFACE'
              AND resolution_status  = 'PENDING'
              AND COALESCE(last_seen_ts, created_ts) >= datetime('now', '-48 hours')
            ORDER BY COALESCE(last_seen_ts, created_ts) DESC, id DESC
            LIMIT 50
        """).fetchall()
        conn.close()
    except Exception as e:
        log.warning("[RESEARCH] pending-decisions query error: %s", e)
        return {"decisions": [], "total": 0, "error": str(e)}

    decisions = [
        {
            "id":                 r["id"],
            "created_ts":         r["created_ts"],
            "last_seen_ts":       r["last_seen_ts"],
            "symbol":             r["symbol"],
            "mint":               r["mint"],
            "surface_count":      r["surface_count"],
            "recommended_action": r["recommended_action"],
            "priority":           r["priority"],
            "reason":             r["reason"],
            "snapshot_json":      r["snapshot_json"],
            "operator_decision":  r["operator_decision"],
            "operator_note":      r["operator_note"],
            "resolution_status":  r["resolution_status"],
        }
        for r in rows
    ]

    return {
        "decisions": decisions,
        "total":     len(decisions),
    }


# ── Calibration ───────────────────────────────────────────────────────────────

_THIN_THRESHOLD = 10


def _bucket_stats(rows: list[dict], key: str) -> list[dict]:
    """Aggregate resolved rows by a single key field.

    Returns list of dicts sorted by win_rate_pct descending, each containing:
      key value, n, win_rate_pct, avg_return_24h_pct, median_return_24h_pct, thin
    """
    buckets: dict[str, list[float]] = {}
    for r in rows:
        k = r.get(key) or "UNKNOWN"
        buckets.setdefault(k, []).append(r["return_24h_pct"])

    result = []
    for k, returns in buckets.items():
        n = len(returns)
        wins = sum(1 for v in returns if v > 0)
        wr = round(wins / n * 100, 1) if n > 0 else 0.0
        avg = round(sum(returns) / n, 2) if n > 0 else 0.0
        med = round(statistics.median(returns), 2) if n > 0 else 0.0
        result.append({
            key:                      k,
            "n":                      n,
            "win_rate_pct":           wr,
            "avg_return_24h_pct":     avg,
            "median_return_24h_pct":  med,
            "thin":                   n < _THIN_THRESHOLD,
        })
    result.sort(key=lambda x: x["win_rate_pct"], reverse=True)
    return result


def _fuel_window_stats(rows: list[dict]) -> list[dict]:
    """Aggregate resolved rows by (fuel_quality, entry_window) composite key."""
    buckets: dict[tuple, list[float]] = {}
    for r in rows:
        fq = r.get("fuel_quality") or "UNKNOWN"
        ew = r.get("entry_window") or "UNKNOWN"
        buckets.setdefault((fq, ew), []).append(r["return_24h_pct"])

    result = []
    for (fq, ew), returns in buckets.items():
        n = len(returns)
        wins = sum(1 for v in returns if v > 0)
        wr = round(wins / n * 100, 1) if n > 0 else 0.0
        result.append({
            "fuel_quality":   fq,
            "entry_window":   ew,
            "n":              n,
            "win_rate_pct":   wr,
            "thin":           n < _THIN_THRESHOLD,
        })
    result.sort(key=lambda x: x["win_rate_pct"], reverse=True)
    return result


def _build_window_stats(rows: list[dict]) -> dict:
    if not rows:
        return {
            "action_tier": [],
            "move_type":   [],
            "continuation_archetype": [],
            "freshness_bucket": [],
            "fresh_catalyst_bucket": [],
            "fresh_discovery_authority": [],
            "reinforcement_bucket": [],
            "fresh_reinforced_family": [],
            "capital_posture": [],
            "capital_ready_state": [],
            "capital_pressure_bucket": [],
            "capital_regime_bucket": [],
            "capital_mix_bucket": [],
            "capital_allocator_stance": [],
            "capital_headroom_bucket": [],
            "capital_route_bucket": [],
            "marginal_route": [],
            "allocator_posture": [],
            "dominant_book": [],
            "queue_system": [],
            "queue_priority_bucket": [],
            "marginal_route_authority": [],
            "proof_stack_authority": [],
            "reinforcement_authority": [],
            "promotion_authority": [],
            "deployment_authority": [],
            "decision_authority": [],
            "continuation_memory_authority": [],
            "marginal_route_family": [],
            "marginal_route_lane_family": [],
            "marginal_route_action_family": [],
            "marginal_route_priority_family": [],
            "capital_routing_family": [],
            "capital_deployment_family": [],
            "capital_intensity_bucket": [],
            "capital_window_bucket": [],
            "fuel_window": [],
        }
    returns = [r["return_24h_pct"] for r in rows]
    wins = sum(1 for v in returns if v > 0)
    overall_wr = round(wins / len(returns) * 100, 1) if returns else 0.0
    return {
        "action_tier":  _bucket_stats(rows, "action"),
        "move_type":    _bucket_stats(rows, "move_type"),
        "continuation_archetype": _bucket_stats(rows, "continuation_archetype"),
        "freshness_bucket": _bucket_stats(rows, "freshness_bucket"),
        "fresh_catalyst_bucket": _bucket_stats(rows, "fresh_catalyst_bucket"),
        "fresh_discovery_authority": _bucket_stats(rows, "fresh_discovery_authority"),
        "reinforcement_bucket": _bucket_stats(rows, "reinforcement_bucket"),
        "fresh_reinforced_family": _bucket_stats(rows, "fresh_reinforced_family"),
        "capital_posture": _bucket_stats(rows, "capital_posture"),
        "capital_ready_state": _bucket_stats(rows, "capital_ready_state"),
        "capital_pressure_bucket": _bucket_stats(rows, "capital_pressure_bucket"),
        "capital_regime_bucket": _bucket_stats(rows, "capital_regime_bucket"),
        "capital_mix_bucket": _bucket_stats(rows, "capital_mix_bucket"),
        "capital_allocator_stance": _bucket_stats(rows, "capital_allocator_stance"),
        "capital_headroom_bucket": _bucket_stats(rows, "capital_headroom_bucket"),
        "capital_route_bucket": _bucket_stats(rows, "capital_route_bucket"),
        "marginal_route": _bucket_stats(rows, "marginal_route"),
        "allocator_posture": _bucket_stats(rows, "allocator_posture"),
        "dominant_book": _bucket_stats(rows, "dominant_book"),
        "queue_system": _bucket_stats(rows, "queue_system"),
        "queue_priority_bucket": _bucket_stats(rows, "queue_priority_bucket"),
        "marginal_route_authority": _bucket_stats(rows, "marginal_route_authority"),
        "proof_stack_authority": _bucket_stats(rows, "proof_stack_authority"),
        "reinforcement_authority": _bucket_stats(rows, "reinforcement_authority"),
        "promotion_authority": _bucket_stats(rows, "promotion_authority"),
        "deployment_authority": _bucket_stats(rows, "deployment_authority"),
        "decision_authority": _bucket_stats(rows, "decision_authority"),
        "continuation_memory_authority": _bucket_stats(rows, "continuation_memory_authority"),
        "marginal_route_family": _bucket_stats(rows, "marginal_route_family"),
        "marginal_route_lane_family": _bucket_stats(rows, "marginal_route_lane_family"),
        "marginal_route_action_family": _bucket_stats(rows, "marginal_route_action_family"),
        "marginal_route_priority_family": _bucket_stats(rows, "marginal_route_priority_family"),
        "capital_routing_family": _bucket_stats(rows, "capital_routing_family"),
        "capital_deployment_family": _bucket_stats(rows, "capital_deployment_family"),
        "capital_intensity_bucket": _bucket_stats(rows, "capital_intensity_bucket"),
        "capital_window_bucket": _bucket_stats(rows, "capital_window_bucket"),
        "fuel_window":  _fuel_window_stats(rows),
        "overall_win_rate_pct": overall_wr,
        "n": len(rows),
    }


@router.get("/calibration")
def get_calibration(_user=Depends(get_current_user)):
    """Read-only outcome calibration stats from research_surface_log.

    Returns all-time and recent-14d breakdowns for action tier, move type,
    continuation archetype, and fuel×window composites. Only RESOLVED rows
    (return_24h_pct filled) are included. Thin buckets (n < 10) are flagged
    but not suppressed.
    """
    _ensure_path()
    try:
        conn = sqlite3.connect(_get_db_path())
        conn.row_factory = sqlite3.Row
        all_rows = conn.execute("""
            SELECT action, move_type, continuation_archetype, freshness_bucket, fresh_catalyst_bucket, fresh_discovery_authority, fuel_quality, entry_window,
                   support_overlap_score, support_overlap_tags, capital_posture, capital_band,
                   capital_ready_state, capital_suggested_entry_usd, capital_pressure_bucket, capital_regime_bucket, capital_mix_bucket, capital_allocator_stance, capital_headroom_bucket, capital_route_bucket, marginal_route, allocator_posture, dominant_book, queue_system, queue_priority_bucket, marginal_route_authority, proof_stack_authority, reinforcement_authority, promotion_authority, deployment_authority, decision_authority, continuation_memory_authority,
                   return_24h_pct, surfaced_at
            FROM research_surface_log
            WHERE outcome_status = 'RESOLVED'
              AND return_24h_pct IS NOT NULL
            ORDER BY surfaced_at ASC
        """).fetchall()
        conn.close()
    except Exception as e:
        log.warning("[CALIBRATION] query error: %s", e)
        return {
            "total_resolved": 0, "error": str(e),
            "data_window_start": None, "data_window_end": None,
            "thin_threshold": _THIN_THRESHOLD,
            "all_time": {}, "recent_14d": {},
        }

    if not all_rows:
        return {
            "total_resolved": 0,
            "data_window_start": None,
            "data_window_end": None,
            "thin_threshold": _THIN_THRESHOLD,
            "all_time": _build_window_stats([]),
            "recent_14d": _build_window_stats([]),
        }

    all_dicts = [dict(r) for r in all_rows]
    for row in all_dicts:
        row["continuation_archetype"] = (
            str(row.get("continuation_archetype") or "").strip()
            or str(row.get("move_type") or "UNKNOWN").strip()
            or "UNKNOWN"
        )
        row["freshness_bucket"] = str(row.get("freshness_bucket") or "UNKNOWN").strip() or "UNKNOWN"
        row["fresh_catalyst_bucket"] = _fresh_catalyst_bucket(row.get("fresh_catalyst_bucket"))
        row["fresh_discovery_authority"] = _fresh_discovery_authority(row.get("fresh_discovery_authority"))
        row["reinforcement_bucket"] = _reinforcement_bucket(
            row.get("support_overlap_score"),
            row.get("support_overlap_tags"),
        )
        row["fresh_reinforced_family"] = _fresh_reinforced_family(row)
        row["capital_posture"] = str(row.get("capital_posture") or "UNKNOWN").strip() or "UNKNOWN"
        row["capital_band"] = str(row.get("capital_band") or "UNKNOWN").strip() or "UNKNOWN"
        row["capital_ready_state"] = str(row.get("capital_ready_state") or "UNKNOWN").strip() or "UNKNOWN"
        row["capital_pressure_bucket"] = _capital_pressure_bucket(row.get("capital_pressure_bucket"))
        row["capital_regime_bucket"] = _capital_regime_bucket(row.get("capital_regime_bucket"))
        row["capital_mix_bucket"] = _capital_mix_bucket(row.get("capital_mix_bucket"))
        row["capital_allocator_stance"] = _capital_allocator_stance(row.get("capital_allocator_stance"))
        row["capital_headroom_bucket"] = _capital_headroom_bucket(row.get("capital_headroom_bucket"))
        row["capital_route_bucket"] = _capital_route_bucket(row)
        row["marginal_route"] = _marginal_route(row.get("marginal_route"))
        row["allocator_posture"] = _allocator_posture(row.get("allocator_posture"))
        row["dominant_book"] = _dominant_book(row.get("dominant_book"))
        row["queue_system"] = _queue_system(row.get("queue_system"))
        row["queue_priority_bucket"] = _queue_priority_bucket(row.get("queue_priority_bucket"))
        row["marginal_route_authority"] = _marginal_route_authority(row.get("marginal_route_authority"))
        row["proof_stack_authority"] = _proof_stack_authority(row.get("proof_stack_authority"))
        row["reinforcement_authority"] = _reinforcement_authority(row.get("reinforcement_authority"))
        row["promotion_authority"] = _promotion_authority(row.get("promotion_authority"))
        row["deployment_authority"] = _deployment_authority(row.get("deployment_authority"))
        row["decision_authority"] = _decision_authority(row.get("decision_authority"))
        row["continuation_memory_authority"] = _continuation_memory_authority(row.get("continuation_memory_authority"))
        row["marginal_route_family"] = _marginal_route_family(row)
        row["marginal_route_lane_family"] = _marginal_route_lane_family(row)
        row["marginal_route_action_family"] = _marginal_route_action_family(row)
        row["marginal_route_priority_family"] = _marginal_route_priority_family(row)
        row["capital_routing_family"] = _capital_routing_family(row)
        row["capital_deployment_family"] = _capital_deployment_family(row)
        row["capital_intensity_bucket"] = _capital_intensity_bucket(row.get("capital_suggested_entry_usd"))
        row["capital_window_bucket"] = _capital_window_bucket(row)

    # Date boundaries (surfaced_at is ISO string, lexicographic sort is valid)
    window_start = all_dicts[0]["surfaced_at"]
    window_end   = all_dicts[-1]["surfaced_at"]

    # Recent 14d subset
    cutoff_14d = (datetime.now(timezone.utc) - timedelta(days=14)).strftime("%Y-%m-%dT%H:%M:%S")
    recent_14d = [r for r in all_dicts if r["surfaced_at"] >= cutoff_14d]

    return {
        "total_resolved":   len(all_dicts),
        "data_window_start": window_start,
        "data_window_end":   window_end,
        "thin_threshold":    _THIN_THRESHOLD,
        "all_time":          _build_window_stats(all_dicts),
        "recent_14d":        _build_window_stats(recent_14d),
    }


# ── Trust state (Patch 268) ────────────────────────────────────────────────────

# Minimum resolved rows before any confidence level can be assigned
_MIN_RESOLVED   = 20
# Minimum 14d rows in a bucket before it can be used for a pattern assertion
_MIN_14D_BUCKET = 5
# pp drop from all-time ACT win rate that triggers CAUTIOUS
_CAUTION_DELTA  = 8.0
# Absolute 14d win rate below which LOW fires regardless of all-time
_LOW_THRESHOLD  = 40.0
# pp above all-time win rate required for "working lately" label
_WORKING_DELTA  = 5.0
# pp below all-time win rate required for "failing lately" label
_FAILING_DELTA  = 10.0


@router.get("/trust-state")
def get_trust_state(_user=Depends(get_current_user)):
    """Patch 268 — Interpret calibration data into compact operator trust guidance.

    Inputs:
      research_surface_log — all RESOLVED rows (same as calibration endpoint)
      decision_journal     — CLOSED rows with FOLLOWED/SKIPPED verdicts

    Outputs:
      system_confidence    NORMAL | CAUTIOUS | LOW | INSUFFICIENT_DATA
      confidence_reason    plain-text explanation of the confidence level
      working_lately       patterns where 14d win rate outperforms all-time by >= 5pp
      failing_lately       patterns where 14d win rate underperforms all-time by >= 10pp
      caution_flags        named flags for specific detectable failures
      operator_accuracy    FOLLOWED/SKIPPED decision accuracy from decision journal
      review_notes         short actionable summary for the operator
      data_coverage        total_resolved, recent_14d_resolved

    Confidence logic (transparent, deterministic):
      Primary signal: ACT tier 14d win rate vs ACT tier all-time win rate.
      Fallback (if ACT 14d bucket < 5 rows): overall 14d win rate vs overall all-time.
      LOW      — primary 14d win rate < 40%
      CAUTIOUS — primary 14d win rate < all-time - 8pp
      NORMAL   — primary 14d win rate >= all-time - 8pp
      INSUFFICIENT_DATA — total resolved < 20, or 14d bucket too small

      Working/failing: derived from move_type, continuation archetype,
      and fuel×window buckets.
    Buckets with < 5 rows in 14d are skipped (too thin for assertions).
    """
    _ensure_path()
    cutoff_14d = (
        datetime.now(timezone.utc) - timedelta(days=14)
    ).strftime("%Y-%m-%dT%H:%M:%S")

    try:
        conn = sqlite3.connect(_get_db_path())
        conn.row_factory = sqlite3.Row

        all_rows = conn.execute("""
            SELECT action, move_type, continuation_archetype, freshness_bucket, fresh_catalyst_bucket, fresh_discovery_authority, fuel_quality, entry_window,
                   support_overlap_score, support_overlap_tags, capital_posture, capital_band,
                   capital_ready_state, capital_suggested_entry_usd, capital_pressure_bucket, capital_regime_bucket, capital_mix_bucket, capital_allocator_stance, capital_headroom_bucket, capital_route_bucket, marginal_route, allocator_posture, dominant_book, queue_system, queue_priority_bucket, marginal_route_authority, proof_stack_authority, reinforcement_authority, promotion_authority, deployment_authority, decision_authority, continuation_memory_authority,
                   return_24h_pct, surfaced_at
            FROM research_surface_log
            WHERE outcome_status = 'RESOLVED'
              AND return_24h_pct IS NOT NULL
            ORDER BY surfaced_at ASC
        """).fetchall()

        # DJ operator accuracy — RESOLVED rows (Patch 283: was 'CLOSED', unified to RESOLVED)
        try:
            dj_rows = conn.execute("""
                SELECT operator_decision, verdict
                FROM decision_journal
                WHERE resolution_status IN ('RESOLVED', 'CLOSED')
                  AND operator_decision IN ('FOLLOWED', 'SKIPPED')
                  AND verdict IS NOT NULL
            """).fetchall()
        except Exception:
            dj_rows = []

        conn.close()
    except Exception as e:
        log.warning("[TRUST_STATE] query error: %s", e)
        return {
            "system_confidence": "INSUFFICIENT_DATA",
            "confidence_reason": f"Query error: {e}",
            "working_lately": [], "failing_lately": [],
            "caution_flags": [], "operator_accuracy": {},
            "review_notes": "Data unavailable.",
            "data_coverage": {"total_resolved": 0, "recent_14d_resolved": 0},
        }

    all_dicts = [dict(r) for r in all_rows]
    for row in all_dicts:
        row["continuation_archetype"] = (
            str(row.get("continuation_archetype") or "").strip()
            or str(row.get("move_type") or "UNKNOWN").strip()
            or "UNKNOWN"
        )
        row["freshness_bucket"] = str(row.get("freshness_bucket") or "UNKNOWN").strip() or "UNKNOWN"
        row["fresh_catalyst_bucket"] = _fresh_catalyst_bucket(row.get("fresh_catalyst_bucket"))
        row["fresh_discovery_authority"] = _fresh_discovery_authority(row.get("fresh_discovery_authority"))
        row["reinforcement_bucket"] = _reinforcement_bucket(
            row.get("support_overlap_score"),
            row.get("support_overlap_tags"),
        )
        row["fresh_reinforced_family"] = _fresh_reinforced_family(row)
        row["capital_posture"] = str(row.get("capital_posture") or "UNKNOWN").strip() or "UNKNOWN"
        row["capital_band"] = str(row.get("capital_band") or "UNKNOWN").strip() or "UNKNOWN"
        row["capital_ready_state"] = str(row.get("capital_ready_state") or "UNKNOWN").strip() or "UNKNOWN"
        row["capital_pressure_bucket"] = _capital_pressure_bucket(row.get("capital_pressure_bucket"))
        row["capital_regime_bucket"] = _capital_regime_bucket(row.get("capital_regime_bucket"))
        row["capital_mix_bucket"] = _capital_mix_bucket(row.get("capital_mix_bucket"))
        row["capital_allocator_stance"] = _capital_allocator_stance(row.get("capital_allocator_stance"))
        row["capital_headroom_bucket"] = _capital_headroom_bucket(row.get("capital_headroom_bucket"))
        row["capital_route_bucket"] = _capital_route_bucket(row)
        row["marginal_route"] = _marginal_route(row.get("marginal_route"))
        row["allocator_posture"] = _allocator_posture(row.get("allocator_posture"))
        row["dominant_book"] = _dominant_book(row.get("dominant_book"))
        row["queue_system"] = _queue_system(row.get("queue_system"))
        row["queue_priority_bucket"] = _queue_priority_bucket(row.get("queue_priority_bucket"))
        row["marginal_route_authority"] = _marginal_route_authority(row.get("marginal_route_authority"))
        row["proof_stack_authority"] = _proof_stack_authority(row.get("proof_stack_authority"))
        row["reinforcement_authority"] = _reinforcement_authority(row.get("reinforcement_authority"))
        row["promotion_authority"] = _promotion_authority(row.get("promotion_authority"))
        row["deployment_authority"] = _deployment_authority(row.get("deployment_authority"))
        row["decision_authority"] = _decision_authority(row.get("decision_authority"))
        row["continuation_memory_authority"] = _continuation_memory_authority(row.get("continuation_memory_authority"))
        row["marginal_route_family"] = _marginal_route_family(row)
        row["marginal_route_lane_family"] = _marginal_route_lane_family(row)
        row["marginal_route_action_family"] = _marginal_route_action_family(row)
        row["marginal_route_priority_family"] = _marginal_route_priority_family(row)
        row["capital_routing_family"] = _capital_routing_family(row)
        row["capital_deployment_family"] = _capital_deployment_family(row)
        row["capital_intensity_bucket"] = _capital_intensity_bucket(row.get("capital_suggested_entry_usd"))
        row["capital_window_bucket"] = _capital_window_bucket(row)
    recent    = [r for r in all_dicts if r["surfaced_at"] >= cutoff_14d]
    total     = len(all_dicts)
    recent_n  = len(recent)

    # ── System confidence ──────────────────────────────────────────────────────
    confidence        = "INSUFFICIENT_DATA"
    confidence_reason = f"{total}/{_MIN_RESOLVED} resolved rows — building calibration data"
    primary_14d_wr    = None
    primary_all_wr    = None

    if total >= _MIN_RESOLVED:
        act_all  = [r["return_24h_pct"] for r in all_dicts if r.get("action") == "ACT"]
        act_14d  = [r["return_24h_pct"] for r in recent   if r.get("action") == "ACT"]

        # Prefer ACT-tier signals; fall back to overall if ACT bucket is thin
        if len(act_14d) >= _MIN_14D_BUCKET and act_all:
            primary_14d_wr = sum(1 for v in act_14d if v > 0) / len(act_14d) * 100
            primary_all_wr = sum(1 for v in act_all if v > 0) / len(act_all) * 100
            src = "ACT"
        elif recent_n >= _MIN_14D_BUCKET:
            primary_14d_wr = sum(1 for r in recent     if r["return_24h_pct"] > 0) / recent_n * 100
            primary_all_wr = sum(1 for r in all_dicts  if r["return_24h_pct"] > 0) / total   * 100
            src = "OVERALL"
        else:
            confidence_reason = (
                f"Recent 14d sample too small ({recent_n} rows) — "
                f"need {_MIN_14D_BUCKET} for rating"
            )
            src = None

        if primary_14d_wr is not None:
            wr14   = round(primary_14d_wr, 1)
            wr_all = round(primary_all_wr, 1)
            if primary_14d_wr < _LOW_THRESHOLD:
                confidence        = "LOW"
                confidence_reason = (
                    f"{src} 14d win rate {wr14}% — critically low (threshold {_LOW_THRESHOLD:.0f}%)"
                )
            elif primary_14d_wr < primary_all_wr - _CAUTION_DELTA:
                confidence        = "CAUTIOUS"
                confidence_reason = (
                    f"{src} 14d {wr14}% vs all-time {wr_all}% "
                    f"(delta -{round(primary_all_wr - primary_14d_wr, 1)}pp)"
                )
            else:
                confidence        = "NORMAL"
                confidence_reason = (
                    f"{src} 14d {wr14}% vs all-time {wr_all}% — at or above baseline"
                )

    # ── Working / failing patterns ─────────────────────────────────────────────
    working: list[dict] = []
    failing: list[dict] = []

    if total >= _MIN_RESOLVED:
        def _pattern_entries(
            all_buckets: dict[str, list[float]],
            recent_buckets: dict[str, list[float]],
            bucket_type: str,
        ) -> None:
            for k, all_returns in all_buckets.items():
                r14 = recent_buckets.get(k, [])
                if len(r14) < _MIN_14D_BUCKET:
                    continue
                wr_a = sum(1 for v in all_returns if v > 0) / len(all_returns) * 100
                wr_r = sum(1 for v in r14 if v > 0) / len(r14) * 100
                delta = wr_r - wr_a
                entry = {
                    "label":            k.replace("_", " "),
                    "type":             bucket_type,
                    "win_rate_14d":     round(wr_r, 1),
                    "win_rate_alltime": round(wr_a, 1),
                    "delta_pp":         round(delta, 1),
                    "n_14d":            len(r14),
                }
                if delta >= _WORKING_DELTA:
                    working.append(entry)
                elif delta <= -_FAILING_DELTA:
                    failing.append(entry)

        # By move_type
        mt_all: dict[str, list] = {}
        mt_14d: dict[str, list] = {}
        for r in all_dicts:
            mt_all.setdefault(r.get("move_type") or "UNKNOWN", []).append(r["return_24h_pct"])
        for r in recent:
            mt_14d.setdefault(r.get("move_type") or "UNKNOWN", []).append(r["return_24h_pct"])
        _pattern_entries(mt_all, mt_14d, "move_type")

        # By continuation archetype
        arch_all: dict[str, list] = {}
        arch_14d: dict[str, list] = {}
        for r in all_dicts:
            arch_all.setdefault(
                r.get("continuation_archetype") or r.get("move_type") or "UNKNOWN",
                []
            ).append(r["return_24h_pct"])
        for r in recent:
            arch_14d.setdefault(
                r.get("continuation_archetype") or r.get("move_type") or "UNKNOWN",
                []
            ).append(r["return_24h_pct"])
        _pattern_entries(arch_all, arch_14d, "continuation_archetype")

        # By freshness bucket
        fresh_all: dict[str, list] = {}
        fresh_14d: dict[str, list] = {}
        for r in all_dicts:
            fresh_all.setdefault(
                r.get("freshness_bucket") or "UNKNOWN",
                []
            ).append(r["return_24h_pct"])
        for r in recent:
            fresh_14d.setdefault(
                r.get("freshness_bucket") or "UNKNOWN",
                []
            ).append(r["return_24h_pct"])
        _pattern_entries(fresh_all, fresh_14d, "freshness_bucket")

        # By fresh catalyst bucket
        fresh_catalyst_all: dict[str, list] = {}
        fresh_catalyst_14d: dict[str, list] = {}
        for r in all_dicts:
            fresh_catalyst_all.setdefault(
                r.get("fresh_catalyst_bucket") or "UNKNOWN",
                []
            ).append(r["return_24h_pct"])
        for r in recent:
            fresh_catalyst_14d.setdefault(
                r.get("fresh_catalyst_bucket") or "UNKNOWN",
                []
            ).append(r["return_24h_pct"])
        _pattern_entries(fresh_catalyst_all, fresh_catalyst_14d, "fresh_catalyst_bucket")

        # By fresh discovery authority
        fresh_discovery_all: dict[str, list] = {}
        fresh_discovery_14d: dict[str, list] = {}
        for r in all_dicts:
            fresh_discovery_all.setdefault(
                r.get("fresh_discovery_authority") or "UNKNOWN",
                []
            ).append(r["return_24h_pct"])
        for r in recent:
            fresh_discovery_14d.setdefault(
                r.get("fresh_discovery_authority") or "UNKNOWN",
                []
            ).append(r["return_24h_pct"])
        _pattern_entries(fresh_discovery_all, fresh_discovery_14d, "fresh_discovery_authority")

        # By reinforcement bucket
        reinf_all: dict[str, list] = {}
        reinf_14d: dict[str, list] = {}
        for r in all_dicts:
            reinf_all.setdefault(
                r.get("reinforcement_bucket") or "PLAIN",
                []
            ).append(r["return_24h_pct"])
        for r in recent:
            reinf_14d.setdefault(
                r.get("reinforcement_bucket") or "PLAIN",
                []
            ).append(r["return_24h_pct"])
        _pattern_entries(reinf_all, reinf_14d, "reinforcement_bucket")

        # By combined freshness × reinforcement × archetype family
        fra_all: dict[str, list] = {}
        fra_14d: dict[str, list] = {}
        for r in all_dicts:
            fra_all.setdefault(
                r.get("fresh_reinforced_family") or "UNKNOWN|UNKNOWN|PLAIN",
                []
            ).append(r["return_24h_pct"])
        for r in recent:
            fra_14d.setdefault(
                r.get("fresh_reinforced_family") or "UNKNOWN|UNKNOWN|PLAIN",
                []
            ).append(r["return_24h_pct"])
        _pattern_entries(fra_all, fra_14d, "fresh_reinforced_family")

        # By capital posture
        cap_all: dict[str, list] = {}
        cap_14d: dict[str, list] = {}
        for r in all_dicts:
            cap_all.setdefault(
                r.get("capital_posture") or "UNKNOWN",
                []
            ).append(r["return_24h_pct"])
        for r in recent:
            cap_14d.setdefault(
                r.get("capital_posture") or "UNKNOWN",
                []
            ).append(r["return_24h_pct"])
        _pattern_entries(cap_all, cap_14d, "capital_posture")

        # By capital readiness state
        ready_all: dict[str, list] = {}
        ready_14d: dict[str, list] = {}
        for r in all_dicts:
            ready_all.setdefault(
                r.get("capital_ready_state") or "UNKNOWN",
                []
            ).append(r["return_24h_pct"])
        for r in recent:
            ready_14d.setdefault(
                r.get("capital_ready_state") or "UNKNOWN",
                []
            ).append(r["return_24h_pct"])
        _pattern_entries(ready_all, ready_14d, "capital_ready_state")

        # By capital pressure bucket
        pressure_all: dict[str, list] = {}
        pressure_14d: dict[str, list] = {}
        for r in all_dicts:
            pressure_all.setdefault(
                r.get("capital_pressure_bucket") or "UNKNOWN",
                []
            ).append(r["return_24h_pct"])
        for r in recent:
            pressure_14d.setdefault(
                r.get("capital_pressure_bucket") or "UNKNOWN",
                []
            ).append(r["return_24h_pct"])
        _pattern_entries(pressure_all, pressure_14d, "capital_pressure_bucket")

        # By capital regime bucket
        regime_all: dict[str, list] = {}
        regime_14d: dict[str, list] = {}
        for r in all_dicts:
            regime_all.setdefault(
                r.get("capital_regime_bucket") or "UNKNOWN",
                []
            ).append(r["return_24h_pct"])
        for r in recent:
            regime_14d.setdefault(
                r.get("capital_regime_bucket") or "UNKNOWN",
                []
            ).append(r["return_24h_pct"])
        _pattern_entries(regime_all, regime_14d, "capital_regime_bucket")

        # By capital mix bucket
        mix_all: dict[str, list] = {}
        mix_14d: dict[str, list] = {}
        for r in all_dicts:
            mix_all.setdefault(
                r.get("capital_mix_bucket") or "UNKNOWN",
                []
            ).append(r["return_24h_pct"])
        for r in recent:
            mix_14d.setdefault(
                r.get("capital_mix_bucket") or "UNKNOWN",
                []
            ).append(r["return_24h_pct"])
        _pattern_entries(mix_all, mix_14d, "capital_mix_bucket")

        # By capital allocator stance
        alloc_all: dict[str, list] = {}
        alloc_14d: dict[str, list] = {}
        for r in all_dicts:
            alloc_all.setdefault(
                r.get("capital_allocator_stance") or "UNKNOWN",
                []
            ).append(r["return_24h_pct"])
        for r in recent:
            alloc_14d.setdefault(
                r.get("capital_allocator_stance") or "UNKNOWN",
                []
            ).append(r["return_24h_pct"])
        _pattern_entries(alloc_all, alloc_14d, "capital_allocator_stance")

        # By capital headroom bucket
        headroom_all: dict[str, list] = {}
        headroom_14d: dict[str, list] = {}
        for r in all_dicts:
            headroom_all.setdefault(
                r.get("capital_headroom_bucket") or "UNKNOWN",
                []
            ).append(r["return_24h_pct"])
        for r in recent:
            headroom_14d.setdefault(
                r.get("capital_headroom_bucket") or "UNKNOWN",
                []
            ).append(r["return_24h_pct"])
        _pattern_entries(headroom_all, headroom_14d, "capital_headroom_bucket")

        # By capital route bucket
        route_all: dict[str, list] = {}
        route_14d: dict[str, list] = {}
        for r in all_dicts:
            route_all.setdefault(
                r.get("capital_route_bucket") or "LOCKED",
                []
            ).append(r["return_24h_pct"])
        for r in recent:
            route_14d.setdefault(
                r.get("capital_route_bucket") or "LOCKED",
                []
            ).append(r["return_24h_pct"])
        _pattern_entries(route_all, route_14d, "capital_route_bucket")

        # By marginal capital route
        marginal_all: dict[str, list] = {}
        marginal_14d: dict[str, list] = {}
        for r in all_dicts:
            marginal_all.setdefault(
                r.get("marginal_route") or "UNKNOWN",
                []
            ).append(r["return_24h_pct"])
        for r in recent:
            marginal_14d.setdefault(
                r.get("marginal_route") or "UNKNOWN",
                []
            ).append(r["return_24h_pct"])
        _pattern_entries(marginal_all, marginal_14d, "marginal_route")

        # By marginal route authority
        marginal_authority_all: dict[str, list] = {}
        marginal_authority_14d: dict[str, list] = {}
        for r in all_dicts:
            marginal_authority_all.setdefault(
                r.get("marginal_route_authority") or "UNKNOWN",
                []
            ).append(r["return_24h_pct"])
        for r in recent:
            marginal_authority_14d.setdefault(
                r.get("marginal_route_authority") or "UNKNOWN",
                []
            ).append(r["return_24h_pct"])
        _pattern_entries(marginal_authority_all, marginal_authority_14d, "marginal_route_authority")

        # By proof stack authority
        proof_stack_authority_all: dict[str, list] = {}
        proof_stack_authority_14d: dict[str, list] = {}
        for r in all_dicts:
            proof_stack_authority_all.setdefault(
                r.get("proof_stack_authority") or "UNKNOWN",
                []
            ).append(r["return_24h_pct"])
        for r in recent:
            proof_stack_authority_14d.setdefault(
                r.get("proof_stack_authority") or "UNKNOWN",
                []
            ).append(r["return_24h_pct"])
        _pattern_entries(proof_stack_authority_all, proof_stack_authority_14d, "proof_stack_authority")

        # By reinforcement authority
        reinforcement_authority_all: dict[str, list] = {}
        reinforcement_authority_14d: dict[str, list] = {}
        for r in all_dicts:
            reinforcement_authority_all.setdefault(
                r.get("reinforcement_authority") or "UNKNOWN",
                []
            ).append(r["return_24h_pct"])
        for r in recent:
            reinforcement_authority_14d.setdefault(
                r.get("reinforcement_authority") or "UNKNOWN",
                []
            ).append(r["return_24h_pct"])
        _pattern_entries(reinforcement_authority_all, reinforcement_authority_14d, "reinforcement_authority")

        # By promotion authority
        promotion_authority_all: dict[str, list] = {}
        promotion_authority_14d: dict[str, list] = {}
        for r in all_dicts:
            promotion_authority_all.setdefault(
                r.get("promotion_authority") or "UNKNOWN",
                []
            ).append(r["return_24h_pct"])
        for r in recent:
            promotion_authority_14d.setdefault(
                r.get("promotion_authority") or "UNKNOWN",
                []
            ).append(r["return_24h_pct"])
        _pattern_entries(promotion_authority_all, promotion_authority_14d, "promotion_authority")

        # By deployment authority
        deployment_authority_all: dict[str, list] = {}
        deployment_authority_14d: dict[str, list] = {}
        for r in all_dicts:
            deployment_authority_all.setdefault(
                r.get("deployment_authority") or "UNKNOWN",
                []
            ).append(r["return_24h_pct"])
        for r in recent:
            deployment_authority_14d.setdefault(
                r.get("deployment_authority") or "UNKNOWN",
                []
            ).append(r["return_24h_pct"])
        _pattern_entries(deployment_authority_all, deployment_authority_14d, "deployment_authority")

        # By decision authority
        decision_authority_all: dict[str, list] = {}
        decision_authority_14d: dict[str, list] = {}
        for r in all_dicts:
            decision_authority_all.setdefault(
                r.get("decision_authority") or "UNKNOWN",
                []
            ).append(r["return_24h_pct"])
        for r in recent:
            decision_authority_14d.setdefault(
                r.get("decision_authority") or "UNKNOWN",
                []
            ).append(r["return_24h_pct"])
        _pattern_entries(decision_authority_all, decision_authority_14d, "decision_authority")

        # By continuation memory authority
        continuation_authority_all: dict[str, list] = {}
        continuation_authority_14d: dict[str, list] = {}
        for r in all_dicts:
            continuation_authority_all.setdefault(
                r.get("continuation_memory_authority") or "UNKNOWN",
                []
            ).append(r["return_24h_pct"])
        for r in recent:
            continuation_authority_14d.setdefault(
                r.get("continuation_memory_authority") or "UNKNOWN",
                []
            ).append(r["return_24h_pct"])
        _pattern_entries(continuation_authority_all, continuation_authority_14d, "continuation_memory_authority")

        # By allocator posture
        alloc_posture_all: dict[str, list] = {}
        alloc_posture_14d: dict[str, list] = {}
        for r in all_dicts:
            alloc_posture_all.setdefault(
                r.get("allocator_posture") or "UNKNOWN",
                []
            ).append(r["return_24h_pct"])
        for r in recent:
            alloc_posture_14d.setdefault(
                r.get("allocator_posture") or "UNKNOWN",
                []
            ).append(r["return_24h_pct"])
        _pattern_entries(alloc_posture_all, alloc_posture_14d, "allocator_posture")

        # By dominant deployed book
        dominant_all: dict[str, list] = {}
        dominant_14d: dict[str, list] = {}
        for r in all_dicts:
            dominant_all.setdefault(
                r.get("dominant_book") or "UNKNOWN",
                []
            ).append(r["return_24h_pct"])
        for r in recent:
            dominant_14d.setdefault(
                r.get("dominant_book") or "UNKNOWN",
                []
            ).append(r["return_24h_pct"])
        _pattern_entries(dominant_all, dominant_14d, "dominant_book")

        # By marginal route family
        marginal_family_all: dict[str, list] = {}
        marginal_family_14d: dict[str, list] = {}
        for r in all_dicts:
            marginal_family_all.setdefault(
                r.get("marginal_route_family") or "UNKNOWN|UNKNOWN|UNKNOWN",
                []
            ).append(r["return_24h_pct"])
        for r in recent:
            marginal_family_14d.setdefault(
                r.get("marginal_route_family") or "UNKNOWN|UNKNOWN|UNKNOWN",
                []
            ).append(r["return_24h_pct"])
        _pattern_entries(marginal_family_all, marginal_family_14d, "marginal_route_family")

        # By queue system
        queue_system_all: dict[str, list] = {}
        queue_system_14d: dict[str, list] = {}
        for r in all_dicts:
            queue_system_all.setdefault(
                r.get("queue_system") or "UNKNOWN",
                []
            ).append(r["return_24h_pct"])
        for r in recent:
            queue_system_14d.setdefault(
                r.get("queue_system") or "UNKNOWN",
                []
            ).append(r["return_24h_pct"])
        _pattern_entries(queue_system_all, queue_system_14d, "queue_system")

        # By marginal route lane family
        marginal_lane_family_all: dict[str, list] = {}
        marginal_lane_family_14d: dict[str, list] = {}
        for r in all_dicts:
            marginal_lane_family_all.setdefault(
                r.get("marginal_route_lane_family") or "UNKNOWN|UNKNOWN|UNKNOWN",
                []
            ).append(r["return_24h_pct"])
        for r in recent:
            marginal_lane_family_14d.setdefault(
                r.get("marginal_route_lane_family") or "UNKNOWN|UNKNOWN|UNKNOWN",
                []
            ).append(r["return_24h_pct"])
        _pattern_entries(marginal_lane_family_all, marginal_lane_family_14d, "marginal_route_lane_family")

        # By marginal route action family
        marginal_action_family_all: dict[str, list] = {}
        marginal_action_family_14d: dict[str, list] = {}
        for r in all_dicts:
            marginal_action_family_all.setdefault(
                r.get("marginal_route_action_family") or "UNKNOWN|UNKNOWN|UNKNOWN|UNKNOWN",
                []
            ).append(r["return_24h_pct"])
        for r in recent:
            marginal_action_family_14d.setdefault(
                r.get("marginal_route_action_family") or "UNKNOWN|UNKNOWN|UNKNOWN|UNKNOWN",
                []
            ).append(r["return_24h_pct"])
        _pattern_entries(marginal_action_family_all, marginal_action_family_14d, "marginal_route_action_family")

        # By marginal route priority family
        marginal_priority_family_all: dict[str, list] = {}
        marginal_priority_family_14d: dict[str, list] = {}
        for r in all_dicts:
            marginal_priority_family_all.setdefault(
                r.get("marginal_route_priority_family") or "UNKNOWN|UNKNOWN|UNKNOWN|UNKNOWN",
                []
            ).append(r["return_24h_pct"])
        for r in recent:
            marginal_priority_family_14d.setdefault(
                r.get("marginal_route_priority_family") or "UNKNOWN|UNKNOWN|UNKNOWN|UNKNOWN",
                []
            ).append(r["return_24h_pct"])
        _pattern_entries(marginal_priority_family_all, marginal_priority_family_14d, "marginal_route_priority_family")

        # By capital routing family
        routing_all: dict[str, list] = {}
        routing_14d: dict[str, list] = {}
        for r in all_dicts:
            routing_all.setdefault(
                r.get("capital_routing_family") or "UNKNOWN|UNKNOWN|UNKNOWN",
                []
            ).append(r["return_24h_pct"])
        for r in recent:
            routing_14d.setdefault(
                r.get("capital_routing_family") or "UNKNOWN|UNKNOWN|UNKNOWN",
                []
            ).append(r["return_24h_pct"])
        _pattern_entries(routing_all, routing_14d, "capital_routing_family")

        # By combined capital deployment family
        deploy_all: dict[str, list] = {}
        deploy_14d: dict[str, list] = {}
        for r in all_dicts:
            deploy_all.setdefault(
                r.get("capital_deployment_family") or "UNKNOWN|UNKNOWN|UNKNOWN|UNKNOWN",
                []
            ).append(r["return_24h_pct"])
        for r in recent:
            deploy_14d.setdefault(
                r.get("capital_deployment_family") or "UNKNOWN|UNKNOWN|UNKNOWN|UNKNOWN",
                []
            ).append(r["return_24h_pct"])
        _pattern_entries(deploy_all, deploy_14d, "capital_deployment_family")

        # By capital intensity bucket
        intensity_all: dict[str, list] = {}
        intensity_14d: dict[str, list] = {}
        for r in all_dicts:
            intensity_all.setdefault(
                r.get("capital_intensity_bucket") or "ZERO",
                []
            ).append(r["return_24h_pct"])
        for r in recent:
            intensity_14d.setdefault(
                r.get("capital_intensity_bucket") or "ZERO",
                []
            ).append(r["return_24h_pct"])
        _pattern_entries(intensity_all, intensity_14d, "capital_intensity_bucket")

        # By capital window bucket
        window_all: dict[str, list] = {}
        window_14d: dict[str, list] = {}
        for r in all_dicts:
            window_all.setdefault(
                r.get("capital_window_bucket") or "CLOSED",
                []
            ).append(r["return_24h_pct"])
        for r in recent:
            window_14d.setdefault(
                r.get("capital_window_bucket") or "CLOSED",
                []
            ).append(r["return_24h_pct"])
        _pattern_entries(window_all, window_14d, "capital_window_bucket")

        # By fuel × entry_window composite
        fw_all: dict[str, list] = {}
        fw_14d: dict[str, list] = {}
        for r in all_dicts:
            k = f"{r.get('fuel_quality') or 'UNK'}/{r.get('entry_window') or 'UNK'}"
            fw_all.setdefault(k, []).append(r["return_24h_pct"])
        for r in recent:
            k = f"{r.get('fuel_quality') or 'UNK'}/{r.get('entry_window') or 'UNK'}"
            fw_14d.setdefault(k, []).append(r["return_24h_pct"])
        _pattern_entries(fw_all, fw_14d, "fuel_window")

        working.sort(key=lambda x: x["win_rate_14d"], reverse=True)
        failing.sort(key=lambda x: x["win_rate_14d"])
        working = working[:3]
        failing = failing[:3]

    # ── Caution flags ──────────────────────────────────────────────────────────
    caution_flags: list[str] = []
    if confidence == "LOW":
        caution_flags.append("ACT_CRITICAL")
    elif confidence == "CAUTIOUS":
        caution_flags.append("ACT_DEGRADING")
    for f in failing:
        if f["win_rate_14d"] < 40.0:
            tag = f["label"].replace(" ", "_").replace("/", "_").upper()
            caution_flags.append(f"{tag}_WEAK")

    # ── Operator accuracy ──────────────────────────────────────────────────────
    followed = [r for r in dj_rows if r["operator_decision"] == "FOLLOWED"]
    skipped  = [r for r in dj_rows if r["operator_decision"] == "SKIPPED"]

    good_follow_n = sum(1 for r in followed if r["verdict"] == "GOOD_FOLLOW")
    good_skip_n   = sum(1 for r in skipped  if r["verdict"] == "GOOD_SKIP")

    gf_pct = round(good_follow_n / len(followed) * 100, 1) if followed else None
    gs_pct = round(good_skip_n   / len(skipped)  * 100, 1) if skipped  else None

    op_accuracy = {
        "followed_n":      len(followed),
        "good_follow_pct": gf_pct,
        "skipped_n":       len(skipped),
        "good_skip_pct":   gs_pct,
    }

    if len(followed) >= 5 and gf_pct is not None and gf_pct < 30.0:
        caution_flags.append("OPERATOR_ACCURACY_LOW")

    # ── Review notes ───────────────────────────────────────────────────────────
    if confidence == "INSUFFICIENT_DATA":
        review_notes = (
            f"Building calibration data — {total}/{_MIN_RESOLVED} resolved outcomes. "
            "Check back as 24h returns accumulate."
        )
    elif confidence == "LOW":
        review_notes = (
            "ACT signals critically underperforming 14d baseline. "
            "Require manual confirmation before following any ACT entry."
        )
    elif confidence == "CAUTIOUS":
        review_notes = (
            "System below 14d baseline. "
            "Tighten manual review — avoid borderline ACT entries."
        )
    else:
        review_notes = (
            "System at or above baseline. "
            "Normal confidence — apply standard review criteria."
        )

    if failing:
        labels = ", ".join(f["label"] for f in failing[:2])
        review_notes += f" Underperforming recently: {labels}."
    if working:
        labels = ", ".join(f["label"] for f in working[:2])
        review_notes += f" Strong recently: {labels}."

    return {
        "system_confidence":  confidence,
        "confidence_reason":  confidence_reason,
        "working_lately":     working,
        "failing_lately":     failing,
        "caution_flags":      caution_flags,
        "operator_accuracy":  op_accuracy,
        "review_notes":       review_notes,
        "data_coverage": {
            "total_resolved":      total,
            "recent_14d_resolved": recent_n,
        },
    }
