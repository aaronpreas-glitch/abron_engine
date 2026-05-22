"""
Confluence Engine API — Patch 143

Routes:
  GET /api/confluence/events?limit=50
  GET /api/confluence/stats
"""
from __future__ import annotations

from datetime import datetime
import logging
import re

from fastapi import APIRouter, Depends
from auth import get_current_user
from snapshot_cache import snapshot_or_build

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/confluence", tags=["confluence"])


def _get_db():
    import sys, os
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    if root not in sys.path:
        sys.path.insert(0, root)
    from utils.db import get_conn  # type: ignore
    return get_conn


def _confluence_data_penalty(conn) -> tuple[float, list[str]]:
    penalty = 0.0
    reasons: list[str] = []
    try:
        row = conn.execute("SELECT value FROM kv_store WHERE key='provider_status:dexscreener'").fetchone()
        import json
        payload = json.loads(row[0]) if row and row[0] else {}
        if str(payload.get("status") or "ACTIVE").upper() != "ACTIVE":
            penalty += 1.0
            reasons.append("dexscreener_degraded")
    except Exception:
        pass
    try:
        row = conn.execute("SELECT value FROM kv_store WHERE key='wallet_tx_stream_status'").fetchone()
        import json
        payload = json.loads(row[0]) if row and row[0] else {}
        last_frame = str(payload.get("last_frame_ts") or "")
        last_type = str(payload.get("last_message_type") or "").upper()
        if last_type == "ERROR" or not last_frame:
            penalty += 1.5
            reasons.append("wallet_stream_untrusted")
    except Exception:
        pass
    return penalty, reasons


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


def _pct(num: int | float, den: int | float) -> float | None:
    try:
        den_f = float(den or 0)
        if den_f <= 0:
            return None
        return round(float(num or 0) / den_f * 100, 1)
    except Exception:
        return None


def _norm_symbol(value: str | None) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    raw = str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.strptime(raw, fmt)
        except Exception:
            pass
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


def _reinforcement_level(score: float) -> str:
    if score >= 9.0:
        return "STRONG"
    if score >= 5.0:
        return "MODERATE"
    if score >= 2.0:
        return "LIGHT"
    return "NONE"


def _reinforcement_thresholds() -> dict:
    return {
        "light_min": 2.0,
        "moderate_min": 5.0,
        "strong_min": 9.0,
    }


def _reinforcement_progress(score: float) -> dict:
    thresholds = _reinforcement_thresholds()
    if score >= thresholds["strong_min"]:
        return {
            "current_level": "STRONG",
            "next_level": None,
            "points_to_next": 0.0,
            "thresholds": thresholds,
        }
    if score >= thresholds["moderate_min"]:
        return {
            "current_level": "MODERATE",
            "next_level": "STRONG",
            "points_to_next": round(thresholds["strong_min"] - score, 2),
            "thresholds": thresholds,
        }
    if score >= thresholds["light_min"]:
        return {
            "current_level": "LIGHT",
            "next_level": "MODERATE",
            "points_to_next": round(thresholds["moderate_min"] - score, 2),
            "thresholds": thresholds,
        }
    return {
        "current_level": "NONE",
        "next_level": "LIGHT",
        "points_to_next": round(thresholds["light_min"] - score, 2),
        "thresholds": thresholds,
    }


def _minutes_since(ts_value: str | None) -> int | None:
    dt = _parse_ts(ts_value)
    if dt is None:
        return None
    return max(0, int(round((datetime.utcnow() - dt).total_seconds() / 60.0)))


def _candidate_scanner_support(row: dict) -> tuple[float, list[str], dict]:
    score = 0.0
    reasons: list[str] = []
    breakdown = {
        "scanner_regime": str(row.get("scanner_regime") or "NORMAL"),
        "trust_label": str(row.get("trust_label") or ""),
        "triage_state": str(row.get("triage_state") or ""),
        "score": 0.0,
    }

    regime = str(row.get("scanner_regime") or "NORMAL").upper()
    if regime == "NORMAL":
        score += 3.0
        reasons.append("normal_scanner_regime")
    elif regime == "RELAXED_NEAR_MISS":
        score += 1.5
        reasons.append("relaxed_scanner_path")

    trust = str(row.get("trust_label") or "").upper()
    if trust == "HIGH_TRUST":
        score += 2.0
        reasons.append("high_trust")
    elif trust == "CONDITIONAL_TRUST":
        score += 1.0
        reasons.append("conditional_trust")

    triage = str(row.get("triage_state") or "").upper()
    if triage == "DO_NOT_TOUCH":
        score -= 1.0
        reasons.append("triage_do_not_touch")
    elif triage == "MONITOR":
        score += 0.5
        reasons.append("triage_monitor")

    proof_score = float(row.get("proof_score") or 0.0)
    if proof_score >= 80.0:
        score += 1.5
        reasons.append("proof_ready_candidate")
    elif proof_score > 0.0:
        score += 0.5
        reasons.append("proof_tracked")

    breakdown["score"] = round(score, 2)
    return score, reasons, breakdown


def _candidate_lifecycle_support(row: dict, lifecycle: dict | None) -> tuple[float, list[str], dict]:
    score = 0.0
    reasons: list[str] = []
    lc = dict(lifecycle or {})
    breakdown = {
        "lifecycle_state": lc.get("lifecycle_state"),
        "entry_window": lc.get("entry_window"),
        "fuel_quality": lc.get("fuel_quality"),
        "move_phase": lc.get("move_phase"),
        "first_leg_confirmed": int(lc.get("first_leg_confirmed") or 0),
        "score": 0.0,
    }

    if str(lc.get("entry_window") or "").upper() == "OPEN":
        score += 1.5
        reasons.append("entry_window_open")
    if str(lc.get("fuel_quality") or "").upper() == "STRONG":
        score += 1.5
        reasons.append("fuel_strong")
    elif str(lc.get("fuel_quality") or "").upper() == "MODERATE":
        score += 1.0
        reasons.append("fuel_moderate")
    if int(lc.get("first_leg_confirmed") or 0) == 1:
        score += 1.0
        reasons.append("first_leg_confirmed")
    if str(lc.get("lifecycle_state") or "").upper() in ("ACTIVE", "RELOAD", "REVIVAL"):
        score += 1.0
        reasons.append("constructive_lifecycle")

    breakdown["score"] = round(score, 2)
    return score, reasons, breakdown


def _candidate_whale_support(candidate: dict, whale_rows: list[dict]) -> tuple[float, list[str], dict]:
    score = 0.0
    reasons: list[str] = []
    exact_rows = [r for r in whale_rows if str(r.get("token_mint") or "") == str(candidate.get("mint") or "")]
    symbol_rows = [r for r in whale_rows if _norm_symbol(r.get("token_symbol")) == _norm_symbol(candidate.get("symbol"))]

    relevant_rows = exact_rows if exact_rows else symbol_rows
    support_kind = "EXACT_MINT" if exact_rows else "SYMBOL_FAMILY" if symbol_rows else "NONE"
    latest_ts = None
    latest_age_minutes = None
    if relevant_rows:
        latest_ts = max(
            (str(r.get("ts_utc") or "") for r in relevant_rows if str(r.get("ts_utc") or "").strip()),
            default=None,
        )
        latest_age_minutes = _minutes_since(latest_ts)

    if exact_rows:
        score += 4.0
        reasons.append("exact_whale_overlap")
    elif symbol_rows:
        score += 1.5
        reasons.append("same_symbol_whale_support")

    scanner_pass_n = sum(1 for r in relevant_rows if int(r.get("scanner_pass") or 0) == 1)
    if scanner_pass_n >= 2:
        score += 1.0
        reasons.append("multiple_whale_passes")
    elif scanner_pass_n == 1:
        score += 0.5

    meaningful = 0
    high_quality = 0
    for row in relevant_rows:
        quality = str(row.get("arkham_signal_quality") or "").upper()
        status = str(row.get("arkham_status") or "").upper()
        if status in ("LIVE", "PARTIAL") and quality in ("LOW", "MEDIUM", "HIGH"):
            meaningful += 1
        if status in ("LIVE", "PARTIAL") and quality in ("MEDIUM", "HIGH"):
            high_quality += 1

    if meaningful > 0:
        score += 1.0
        reasons.append("arkham_live_context")
    if high_quality > 0:
        score += 1.0
        reasons.append("arkham_meaningful")

    max_scanner_score = 0.0
    if relevant_rows:
        max_scanner_score = max(float(r.get("scanner_score") or 0.0) for r in relevant_rows)
        if max_scanner_score >= 75.0:
            score += 0.5
            reasons.append("whale_scanner_strength")

    breakdown = {
        "kind": support_kind,
        "exact_overlap": bool(exact_rows),
        "exact_overlap_count": len(exact_rows),
        "symbol_family_count": len(symbol_rows),
        "recent_whale_rows": len(relevant_rows),
        "scanner_pass_count": scanner_pass_n,
        "meaningful_arkham_count": meaningful,
        "high_quality_arkham_count": high_quality,
        "max_scanner_score": round(max_scanner_score, 1),
        "latest_event_ts": latest_ts,
        "age_minutes": latest_age_minutes,
        "score": round(score, 2),
    }
    return score, reasons, breakdown


def _get_reinforcement_summary(conn, limit: int = 8) -> dict:
    from utils.scanner_labels import resolve_scanner_labels  # type: ignore

    candidate_rows = [
        dict(r) for r in conn.execute(
            """
            SELECT id, scanned_at, symbol, mint, score, status, scanner_regime,
                   scanner_relaxation_reason, trust_label, triage_state,
                   proof_score, proof_reason
            FROM memecoin_signal_outcomes
            WHERE source='SCANNER'
              AND rug_label='GOOD'
              AND status IN ('PENDING', 'COMPLETE')
              AND scanned_at >= datetime('now','-72 hours')
            ORDER BY scanned_at DESC, score DESC
            LIMIT 30
            """
        ).fetchall()
    ]
    whale_rows = [
        dict(r) for r in conn.execute(
            """
            SELECT ts_utc, token_symbol, token_mint, scanner_pass, scanner_score,
                   arkham_status, arkham_signal_quality, arkham_signal_score,
                   arkham_top_entity_name, arkham_net_flow_usd
            FROM whale_watch_alerts
            WHERE ts_utc >= datetime('now','-72 hours')
              AND mc_in_range=1
            ORDER BY ts_utc DESC
            """
        ).fetchall()
    ]
    lifecycle_rows = {
        str(r["mint"] or ""): dict(r)
        for r in conn.execute(
            """
            SELECT mint, symbol, lifecycle_state, entry_window, fuel_quality,
                   move_phase, vol_acc_current, first_leg_confirmed, state_entered_at
            FROM symbol_lifecycle
            WHERE mint IS NOT NULL AND mint != ''
            """
        ).fetchall()
    }

    seen: set[str] = set()
    candidates: list[dict] = []
    level_counts = {"STRONG": 0, "MODERATE": 0, "LIGHT": 0, "NONE": 0}
    exact_overlap_count = 0
    symbol_family_count = 0
    relaxed_count = 0
    data_penalty, data_penalty_reasons = _confluence_data_penalty(conn)

    for row in candidate_rows:
        row.update(resolve_scanner_labels(conn, row, persist=True))
        key = str(row.get("mint") or row.get("symbol") or row.get("id"))
        if key in seen:
            continue
        seen.add(key)

        lifecycle = lifecycle_rows.get(str(row.get("mint") or ""))
        scanner_score, scanner_reasons, scanner_breakdown = _candidate_scanner_support(row)
        lifecycle_score, lifecycle_reasons, lifecycle_breakdown = _candidate_lifecycle_support(row, lifecycle)
        whale_score, whale_reasons, whale_breakdown = _candidate_whale_support(row, whale_rows)

        total_score = round(max(0.0, scanner_score + lifecycle_score + whale_score - data_penalty), 2)
        if (
            str(row.get("scanner_regime") or "").upper() == "RELAXED_NEAR_MISS"
            and str((whale_breakdown or {}).get("kind") or "NONE").upper() != "EXACT_MINT"
            and total_score > 4.5
        ):
            total_score = 4.5
            if "relaxed_path_capped_without_exact_overlap" not in whale_reasons:
                whale_reasons.append("relaxed_path_capped_without_exact_overlap")
        level = _reinforcement_level(total_score)
        progress = _reinforcement_progress(total_score)
        level_counts[level] += 1
        if whale_breakdown.get("exact_overlap"):
            exact_overlap_count += 1
        elif whale_breakdown.get("kind") == "SYMBOL_FAMILY":
            symbol_family_count += 1
        if str(row.get("scanner_regime") or "").upper() == "RELAXED_NEAR_MISS":
            relaxed_count += 1

        reasons = list(dict.fromkeys(scanner_reasons + lifecycle_reasons + whale_reasons + data_penalty_reasons))
        missing_reasons: list[str] = []
        if scanner_breakdown.get("scanner_regime") == "RELAXED_NEAR_MISS" and whale_breakdown.get("kind") != "EXACT_MINT":
            missing_reasons.append("relaxed path capped until exact mint overlap appears")
        if whale_breakdown.get("kind") == "NONE":
            missing_reasons.append("no whale overlap detected")
        elif whale_breakdown.get("kind") == "SYMBOL_FAMILY":
            missing_reasons.append("only symbol-family whale overlap is present")
        if int(whale_breakdown.get("meaningful_arkham_count") or 0) <= 0:
            missing_reasons.append("no live Arkham context attached")
        if str(lifecycle_breakdown.get("entry_window") or "").upper() != "OPEN":
            missing_reasons.append("entry window is not open")
        if str(lifecycle_breakdown.get("fuel_quality") or "").upper() not in ("STRONG", "MODERATE"):
            missing_reasons.append("fuel quality is not actionable")
        if int(lifecycle_breakdown.get("first_leg_confirmed") or 0) != 1:
            missing_reasons.append("first leg is still unconfirmed")
        missing_reasons = list(dict.fromkeys(missing_reasons))
        candidate = {
            "id": row.get("id"),
            "symbol": row.get("symbol"),
            "mint": row.get("mint"),
            "scanned_at": row.get("scanned_at"),
            "status": row.get("status"),
            "score": row.get("score"),
            "scanner_regime": row.get("scanner_regime") or "NORMAL",
            "scanner_relaxation_reason": row.get("scanner_relaxation_reason"),
            "trust_label": row.get("trust_label"),
            "triage_state": row.get("triage_state"),
            "proof_score": row.get("proof_score"),
            "proof_reason": row.get("proof_reason"),
            "reinforcement_level": level,
            "reinforcement_score": total_score,
            "data_penalty": round(data_penalty, 2),
            "reinforcement_reasons": reasons,
            "reinforcement_next_level": progress.get("next_level"),
            "reinforcement_points_to_next": progress.get("points_to_next"),
            "reinforcement_thresholds": progress.get("thresholds"),
            "reinforcement_missing_reasons": missing_reasons[:5],
            "exact_overlap": bool(whale_breakdown.get("exact_overlap")),
            "support_breakdown": {
                "scanner": scanner_breakdown,
                "lifecycle": lifecycle_breakdown,
                "whale": whale_breakdown,
            },
        }
        candidates.append(candidate)

    candidates.sort(
        key=lambda row: (
            {"STRONG": 3, "MODERATE": 2, "LIGHT": 1, "NONE": 0}.get(str(row.get("reinforcement_level")), 0),
            float(row.get("reinforcement_score") or 0.0),
            float(row.get("score") or 0.0),
            str(row.get("scanned_at") or ""),
        ),
        reverse=True,
    )
    top_candidates = candidates[: max(1, min(limit, 20))]
    summary_status = "INACTIVE"
    if level_counts["STRONG"] > 0:
        summary_status = "STRONG_SIGNALING"
    elif level_counts["MODERATE"] > 0:
        summary_status = "ACTIVE"
    elif level_counts["LIGHT"] > 0:
        summary_status = "LIGHT_ONLY"

    return {
        "status": summary_status,
        "detail": (
            "Reinforcement is being attached to primary memecoin candidates."
            if candidates else
            "No recent scanner-quality memecoin candidates are available for reinforcement review."
        ),
        "candidate_count": len(candidates),
        "top_candidates": top_candidates,
        "level_counts": level_counts,
        "exact_overlap_candidates": exact_overlap_count,
        "symbol_family_candidates": symbol_family_count,
        "relaxed_candidates": relaxed_count,
        "thresholds": _reinforcement_thresholds(),
    }


def _get_pre_confluence_diagnostics(conn, limit: int = 5) -> dict:
    whale_rows = [dict(r) for r in conn.execute(
        """
        SELECT id, ts_utc, token_symbol, token_mint, buy_amount_usd, scanner_score, market_cap_usd
        FROM whale_watch_alerts
        WHERE scanner_pass=1
          AND token_mint IS NOT NULL AND token_mint != ''
          AND ts_utc >= datetime('now','-48 hours')
        ORDER BY ts_utc DESC
        """
    ).fetchall()]
    meme_rows = [dict(r) for r in conn.execute(
        """
        SELECT id, scanned_at, symbol, mint, score, status, liquidity_usd, mcap_at_scan
        FROM memecoin_signal_outcomes
        WHERE source='SCANNER'
          AND rug_label='GOOD'
          AND status IN ('PENDING', 'COMPLETE')
          AND mint IS NOT NULL AND mint != ''
          AND scanned_at >= datetime('now','-48 hours')
        ORDER BY scanned_at DESC
        """
    ).fetchall()]

    exact_overlap_48h = conn.execute(
        """
        WITH whale_ready AS (
            SELECT DISTINCT token_mint
            FROM whale_watch_alerts
            WHERE scanner_pass=1
              AND token_mint IS NOT NULL AND token_mint != ''
              AND ts_utc >= datetime('now','-48 hours')
        ),
        meme_ready AS (
            SELECT DISTINCT mint
            FROM memecoin_signal_outcomes
            WHERE source='SCANNER'
              AND rug_label='GOOD'
              AND status IN ('PENDING', 'COMPLETE')
              AND mint IS NOT NULL AND mint != ''
              AND scanned_at >= datetime('now','-48 hours')
        )
        SELECT COUNT(*) FROM whale_ready w
        JOIN meme_ready m ON m.mint = w.token_mint
        """
    ).fetchone()[0] or 0

    exact_overlap_7d = conn.execute(
        """
        WITH whale_ready AS (
            SELECT DISTINCT token_mint
            FROM whale_watch_alerts
            WHERE scanner_pass=1
              AND token_mint IS NOT NULL AND token_mint != ''
              AND ts_utc >= datetime('now','-7 days')
        ),
        meme_ready AS (
            SELECT DISTINCT mint
            FROM memecoin_signal_outcomes
            WHERE source='SCANNER'
              AND rug_label='GOOD'
              AND status IN ('PENDING', 'COMPLETE')
              AND mint IS NOT NULL AND mint != ''
              AND scanned_at >= datetime('now','-7 days')
        )
        SELECT COUNT(*) FROM whale_ready w
        JOIN meme_ready m ON m.mint = w.token_mint
        """
    ).fetchone()[0] or 0

    memes_by_symbol: dict[str, list[dict]] = {}
    for row in meme_rows:
        norm = _norm_symbol(row.get("symbol"))
        if not norm:
            continue
        memes_by_symbol.setdefault(norm, []).append(row)

    near_candidates: list[dict] = []
    seen_pairs: set[tuple[str, str, str]] = set()
    symbol_families: set[str] = set()
    closest_gap_hours: float | None = None

    for whale in whale_rows:
        whale_symbol_norm = _norm_symbol(whale.get("token_symbol"))
        if not whale_symbol_norm:
            continue
        whale_ts = _parse_ts(whale.get("ts_utc"))
        whale_mint = str(whale.get("token_mint") or "")
        for meme in memes_by_symbol.get(whale_symbol_norm, []):
            meme_mint = str(meme.get("mint") or "")
            if not meme_mint or meme_mint == whale_mint:
                continue
            key = (whale_symbol_norm, whale_mint, meme_mint)
            if key in seen_pairs:
                continue
            seen_pairs.add(key)
            symbol_families.add(whale_symbol_norm)
            meme_ts = _parse_ts(meme.get("scanned_at"))
            gap_hours = None
            if whale_ts and meme_ts:
                gap_hours = round(abs((whale_ts - meme_ts).total_seconds()) / 3600.0, 1)
                if closest_gap_hours is None or gap_hours < closest_gap_hours:
                    closest_gap_hours = gap_hours
            near_candidates.append({
                "symbol_family": whale_symbol_norm,
                "whale_alert_id": whale.get("id"),
                "whale_symbol": whale.get("token_symbol"),
                "whale_mint": whale_mint,
                "whale_ts": whale.get("ts_utc"),
                "whale_buy_amount_usd": whale.get("buy_amount_usd"),
                "whale_scanner_score": whale.get("scanner_score"),
                "memecoin_scan_id": meme.get("id"),
                "memecoin_symbol": meme.get("symbol"),
                "memecoin_mint": meme_mint,
                "memecoin_ts": meme.get("scanned_at"),
                "memecoin_score": meme.get("score"),
                "memecoin_status": meme.get("status"),
                "gap_hours": gap_hours,
                "reason": "same_symbol_different_mint",
            })

    near_candidates.sort(
        key=lambda row: (
            row["gap_hours"] if row["gap_hours"] is not None else 9999,
            -(float(row.get("whale_buy_amount_usd") or 0)),
            -(float(row.get("memecoin_score") or 0)),
        )
    )
    examples = near_candidates[:max(1, min(limit, 10))]

    symbol_family_overlap_count = len(near_candidates)
    symbol_family_overlap_distinct = len(symbol_families)
    window_miss_count = max(0, int(exact_overlap_7d) - int(exact_overlap_48h))

    if exact_overlap_48h > 0:
        status = "EXACT_OVERLAP_READY"
        detail = "Exact mint overlap already exists in the last 48h."
    elif exact_overlap_7d > 0:
        status = "WINDOW_MISS"
        detail = (
            f"Exact mint overlap exists in the last 7d, but not in the last 48h."
        )
    elif symbol_family_overlap_count > 0:
        status = "SYMBOL_REUSE_MISMATCH"
        detail = (
            f"{symbol_family_overlap_count} same-symbol near miss(es) found across "
            f"{symbol_family_overlap_distinct} symbol families, but mints differ."
        )
    elif whale_rows and meme_rows:
        status = "NO_SHARED_NAMES"
        detail = "Whale and memecoin inputs are both active, but there are no shared symbol families in the last 48h."
    elif whale_rows:
        status = "WAITING_ON_MEMECOIN"
        detail = "Whale inputs exist, but there are no confluence-eligible memecoin inputs yet."
    elif meme_rows:
        status = "WAITING_ON_WHALE"
        detail = "Memecoin inputs exist, but there are no confluence-ready whale inputs yet."
    else:
        status = "NO_INPUTS"
        detail = "No pre-confluence inputs are available yet."

    return {
        "status": status,
        "detail": detail,
        "exact_mint_overlap_48h": int(exact_overlap_48h),
        "exact_mint_overlap_7d": int(exact_overlap_7d),
        "window_miss_count": window_miss_count,
        "symbol_family_overlap_count": symbol_family_overlap_count,
        "symbol_family_overlap_distinct": symbol_family_overlap_distinct,
        "closest_gap_hours": closest_gap_hours,
        "examples": examples,
    }


def get_confluence_summary_data() -> dict:
    """Canonical compact confluence summary shared by stats/summary consumers."""
    get_conn = _get_db()
    try:
        from utils import orchestrator  # type: ignore
        from utils.confluence_engine import ensure_confluence_tables  # type: ignore
        agent = next((a for a in orchestrator.get_status() if a.get("name") == "confluence_engine"), None)
        confluence_wired = agent is not None
        agent_health = str(agent.get("health") or "init") if agent else "init"
        with get_conn() as conn:
            ensure_confluence_tables(conn)
            pre_confluence = _get_pre_confluence_diagnostics(conn, limit=5)
            reinforcement = _get_reinforcement_summary(conn, limit=6)

            whale_total = conn.execute(
                "SELECT COUNT(*) FROM whale_watch_alerts"
            ).fetchone()[0] or 0
            whale_ready = conn.execute(
                "SELECT COUNT(*) FROM whale_watch_alerts WHERE scanner_pass=1 AND token_mint IS NOT NULL AND token_mint != ''"
            ).fetchone()[0] or 0
            whale_arkham_high = conn.execute(
                "SELECT COUNT(*) FROM whale_watch_alerts WHERE arkham_signal_quality='HIGH'"
            ).fetchone()[0] or 0
            whale_arkham_meaningful = conn.execute(
                """
                SELECT COUNT(*) FROM whale_watch_alerts
                WHERE scanner_pass=1 AND arkham_signal_quality IN ('HIGH','MEDIUM','LOW')
                  AND token_mint IS NOT NULL AND token_mint != ''
                """
            ).fetchone()[0] or 0
            whale_arkham_none = conn.execute(
                """
                SELECT COUNT(*) FROM whale_watch_alerts
                WHERE scanner_pass=1 AND arkham_status='LIVE' AND arkham_signal_quality='NONE'
                  AND token_mint IS NOT NULL AND token_mint != ''
                """
            ).fetchone()[0] or 0
            whale_without_arkham = conn.execute(
                """
                SELECT COUNT(*) FROM whale_watch_alerts
                WHERE scanner_pass=1
                  AND (arkham_status IS NULL OR arkham_status NOT IN ('LIVE','PARTIAL'))
                  AND token_mint IS NOT NULL AND token_mint != ''
                """
            ).fetchone()[0] or 0
            memecoin_ready = conn.execute("""
                SELECT COUNT(*) FROM memecoin_signal_outcomes
                WHERE source='SCANNER'
                  AND rug_label='GOOD'
                  AND status IN ('PENDING', 'COMPLETE')
                  AND scanned_at >= datetime('now','-48 hours')
            """).fetchone()[0] or 0
            exact_overlap_ready = conn.execute("""
                WITH whale_ready AS (
                    SELECT DISTINCT token_mint
                    FROM whale_watch_alerts
                    WHERE scanner_pass=1
                      AND token_mint IS NOT NULL AND token_mint != ''
                      AND ts_utc >= datetime('now','-48 hours')
                ),
                meme_ready AS (
                    SELECT DISTINCT mint
                    FROM memecoin_signal_outcomes
                    WHERE source='SCANNER'
                      AND rug_label='GOOD'
                      AND status IN ('PENDING', 'COMPLETE')
                      AND mint IS NOT NULL AND mint != ''
                      AND scanned_at >= datetime('now','-48 hours')
                )
                SELECT COUNT(*) FROM whale_ready w
                JOIN meme_ready m ON m.mint = w.token_mint
            """).fetchone()[0] or 0
            whale_in_range = conn.execute(
                "SELECT COUNT(*) FROM whale_watch_alerts WHERE mc_in_range=1"
            ).fetchone()[0] or 0
            whale_mint_resolved = conn.execute(
                "SELECT COUNT(*) FROM whale_watch_alerts WHERE mc_in_range=1 AND token_mint IS NOT NULL AND token_mint != ''"
            ).fetchone()[0] or 0
            whale_scanner_failed = conn.execute(
                "SELECT COUNT(*) FROM whale_watch_alerts WHERE mc_in_range=1 AND scanner_pass=0"
            ).fetchone()[0] or 0
            whale_priced = conn.execute(
                "SELECT COUNT(*) FROM whale_watch_alerts WHERE mc_in_range=1 AND price_at_alert > 0"
            ).fetchone()[0] or 0
            whale_fail_row = conn.execute(
                """
                SELECT
                    CASE
                        WHEN scanner_reason IS NOT NULL AND TRIM(scanner_reason) != '' THEN scanner_reason
                        WHEN scanner_rug_label IN ('DANGER', 'RUGGED') THEN 'rug=' || scanner_rug_label
                        WHEN price_at_alert IS NULL OR price_at_alert <= 0 THEN 'price_unavailable'
                        WHEN token_mint IS NULL OR token_mint = '' THEN 'mint_unresolved'
                        ELSE 'scanner_fail'
                    END AS reason,
                    COUNT(*) AS n
                FROM whale_watch_alerts
                WHERE mc_in_range=1 AND scanner_pass=0
                GROUP BY reason
                ORDER BY n DESC, reason ASC
                LIMIT 1
                """
            ).fetchone()

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

            true_total = conn.execute("""
                SELECT COUNT(*) FROM confluence_events
                WHERE confluence_type IN ('DUAL', 'TRIPLE')
            """).fetchone()[0] or 0
            true_complete = conn.execute("""
                SELECT COUNT(*) FROM confluence_events
                WHERE confluence_type IN ('DUAL', 'TRIPLE')
                  AND outcome_status='COMPLETE'
            """).fetchone()[0] or 0
            true_wr_1h = conn.execute("""
                SELECT ROUND(AVG(CASE WHEN return_1h_pct > 0 THEN 1.0 ELSE 0.0 END)*100, 1)
                FROM confluence_events
                WHERE confluence_type IN ('DUAL', 'TRIPLE') AND return_1h_pct IS NOT NULL
            """).fetchone()[0]
            true_wr_4h = conn.execute("""
                SELECT ROUND(AVG(CASE WHEN return_4h_pct > 0 THEN 1.0 ELSE 0.0 END)*100, 1)
                FROM confluence_events
                WHERE confluence_type IN ('DUAL', 'TRIPLE') AND return_4h_pct IS NOT NULL
            """).fetchone()[0]
            true_wr_24h = conn.execute("""
                SELECT ROUND(AVG(CASE WHEN return_24h_pct > 0 THEN 1.0 ELSE 0.0 END)*100, 1)
                FROM confluence_events
                WHERE confluence_type IN ('DUAL', 'TRIPLE') AND return_24h_pct IS NOT NULL
            """).fetchone()[0]
            true_avg_score = conn.execute("""
                SELECT ROUND(AVG(confluence_score), 1) FROM confluence_events
                WHERE confluence_type IN ('DUAL', 'TRIPLE')
            """).fetchone()[0]

            structural_total = conn.execute("""
                SELECT COUNT(*) FROM confluence_events
                WHERE confluence_type IN ('STRUCTURAL_DUAL', 'STRUCTURAL_TRIPLE')
            """).fetchone()[0] or 0
            structural_complete = conn.execute("""
                SELECT COUNT(*) FROM confluence_events
                WHERE confluence_type IN ('STRUCTURAL_DUAL', 'STRUCTURAL_TRIPLE')
                  AND outcome_status='COMPLETE'
            """).fetchone()[0] or 0
            structural_wr_1h = conn.execute("""
                SELECT ROUND(AVG(CASE WHEN return_1h_pct > 0 THEN 1.0 ELSE 0.0 END)*100, 1)
                FROM confluence_events
                WHERE confluence_type IN ('STRUCTURAL_DUAL', 'STRUCTURAL_TRIPLE')
                  AND return_1h_pct IS NOT NULL
            """).fetchone()[0]
            structural_wr_4h = conn.execute("""
                SELECT ROUND(AVG(CASE WHEN return_4h_pct > 0 THEN 1.0 ELSE 0.0 END)*100, 1)
                FROM confluence_events
                WHERE confluence_type IN ('STRUCTURAL_DUAL', 'STRUCTURAL_TRIPLE')
                  AND return_4h_pct IS NOT NULL
            """).fetchone()[0]
            structural_wr_24h = conn.execute("""
                SELECT ROUND(AVG(CASE WHEN return_24h_pct > 0 THEN 1.0 ELSE 0.0 END)*100, 1)
                FROM confluence_events
                WHERE confluence_type IN ('STRUCTURAL_DUAL', 'STRUCTURAL_TRIPLE')
                  AND return_24h_pct IS NOT NULL
            """).fetchone()[0]
            structural_avg_score = conn.execute("""
                SELECT ROUND(AVG(confluence_score), 1) FROM confluence_events
                WHERE confluence_type IN ('STRUCTURAL_DUAL', 'STRUCTURAL_TRIPLE')
            """).fetchone()[0]

            recent_dual = conn.execute("""
                SELECT COUNT(*) FROM confluence_events
                WHERE confluence_type='DUAL' AND ts_utc >= datetime('now','-48 hours')
            """).fetchone()[0] or 0

            recent_triple = conn.execute("""
                SELECT COUNT(*) FROM confluence_events
                WHERE confluence_type='TRIPLE' AND ts_utc >= datetime('now','-48 hours')
            """).fetchone()[0] or 0

            recent_structural = conn.execute("""
                SELECT COUNT(*) FROM confluence_events
                WHERE confluence_type IN ('STRUCTURAL_DUAL', 'STRUCTURAL_TRIPLE')
                  AND ts_utc >= datetime('now','-48 hours')
            """).fetchone()[0] or 0

            last_row = conn.execute(
                "SELECT ts_utc FROM confluence_events ORDER BY ts_utc DESC LIMIT 1"
            ).fetchone()
            last_ts = last_row[0] if last_row else None

            pending = total - complete

        if not confluence_wired:
            ingest_status = "NOT_WIRED"
            ingest_detail = "confluence engine is not registered with the runtime"
        elif total > 0:
            ingest_status = "LIVE"
            ingest_detail = "confluence engine is producing events"
        elif agent_health in ("init", "slow"):
            ingest_status = "STARTING"
            ingest_detail = "engine is wired and waiting for its next cycle"
        elif whale_ready <= 0 and memecoin_ready <= 0:
            ingest_status = "NO_INPUTS"
            ingest_detail = "waiting for whale and memecoin source data"
        elif whale_ready <= 0:
            ingest_status = "WAITING_ON_WHALE"
            ingest_detail = "memecoin source is ready but whale input is not"
        elif memecoin_ready <= 0:
            ingest_status = "WAITING_ON_MEMECOIN"
            ingest_detail = "whale source is ready but memecoin source is not"
        elif exact_overlap_ready <= 0:
            ingest_status = "WAITING_ON_OVERLAP"
            if pre_confluence.get("status") == "WINDOW_MISS":
                ingest_detail = "both sides are active; exact mint overlap exists in 7d but not the last 48h"
            elif pre_confluence.get("status") == "SYMBOL_REUSE_MISMATCH":
                ingest_detail = "both sides are active; same-symbol near misses exist, but the mints differ"
            else:
                ingest_detail = "both sides are producing eligible inputs, but no exact mint overlap exists in the last 48h"
        else:
            ingest_status = "READY_NO_EVENTS"
            ingest_detail = "inputs exist but no confluence overlap has been detected yet"

        if whale_in_range <= 0:
            whale_bottleneck = "NO_IN_RANGE_ALERTS"
            whale_bottleneck_detail = "No whale alerts in the focus band yet."
        elif whale_mint_resolved < whale_in_range:
            whale_bottleneck = "MINT_RESOLUTION"
            whale_bottleneck_detail = f"{whale_mint_resolved}/{whale_in_range} in-range whale alerts resolved a mint."
        elif whale_ready > 0 and memecoin_ready > 0 and exact_overlap_ready <= 0:
            if pre_confluence.get("status") == "WINDOW_MISS":
                whale_bottleneck = "WINDOW_MISS"
                whale_bottleneck_detail = "Exact mint overlap exists in 7d, but nothing has aligned inside the 48h confluence window."
            elif pre_confluence.get("status") == "SYMBOL_REUSE_MISMATCH":
                whale_bottleneck = "SYMBOL_REUSE_MISMATCH"
                whale_bottleneck_detail = "Whale and memecoin are seeing the same symbol family, but on different mints."
            else:
                whale_bottleneck = "OVERLAP_WAIT"
                whale_bottleneck_detail = "Whale and memecoin inputs both exist, but no exact mint overlap has appeared in the last 48h."
        elif whale_ready > 0 and memecoin_ready > 0:
            whale_bottleneck = "CONFLUENCE_WRITE"
            whale_bottleneck_detail = "Exact mint overlap exists; if events stay at zero, inspect confluence insert/runtime behavior."
        elif whale_ready <= 0 and whale_scanner_failed > 0:
            whale_bottleneck = "SCANNER_QUALITY"
            whale_bottleneck_detail = (
                f"No whale inputs are confluence-ready yet; current top fail reason is "
                f"{whale_fail_row['reason']}."
                if whale_fail_row else
                "No whale inputs are confluence-ready yet."
            )
        elif whale_ready <= 0 and whale_priced < whale_in_range:
            whale_bottleneck = "PRICE_QUALITY"
            whale_bottleneck_detail = f"{whale_priced}/{whale_in_range} in-range whale alerts have usable price-at-alert."
        elif whale_ready <= 0:
            whale_bottleneck = "WHALE_QUALITY"
            whale_bottleneck_detail = "Whale alerts are flowing, but none are strong enough yet."
        else:
            whale_bottleneck = "WAITING_ON_MEMECOIN_OVERLAP"
            whale_bottleneck_detail = "Whale inputs are ready; memecoin overlap has not landed yet."

        return {
            "total": total,
            "total_events": total,
            "complete_events": complete,
            "pending_events": pending,
            "phase": _phase_label(total),
            "next_milestone": _next_milestone(total),
            "wr_1h": wr_1h,
            "wr_4h": wr_4h,
            "wr_24h": wr_24h,
            "avg_confluence_score": avg_conf_score,
            "recent_dual_48h": recent_dual,
            "recent_triple_48h": recent_triple,
            "recent_structural_48h": recent_structural,
            "last_ts": last_ts,
            "ingest_status": ingest_status,
            "ingest_detail": ingest_detail,
            "engine_wired": confluence_wired,
            "whale_input_total": whale_total,
            "whale_in_range": whale_in_range,
            "whale_mint_resolved": whale_mint_resolved,
            "whale_priced": whale_priced,
            "whale_input_ready": whale_ready,
            "whale_scanner_failed": whale_scanner_failed,
            "whale_arkham_high": whale_arkham_high,
            "whale_arkham_meaningful": whale_arkham_meaningful,
            "whale_arkham_none": whale_arkham_none,
            "whale_without_arkham": whale_without_arkham,
            "whale_meaningful_share_pct": _pct(whale_arkham_meaningful, whale_ready),
            "whale_top_fail_reason": whale_fail_row["reason"] if whale_fail_row else None,
            "whale_bottleneck": whale_bottleneck,
            "whale_bottleneck_detail": whale_bottleneck_detail,
            "memecoin_input_ready": memecoin_ready,
            "exact_mint_overlap_ready": exact_overlap_ready,
            "pre_confluence": pre_confluence,
            "reinforcement": reinforcement,
            "validation_tracks": {
                "true_confluence": {
                    "scope": "fresh DUAL/TRIPLE only",
                    "total_events": true_total,
                    "complete_events": true_complete,
                    "pending_events": true_total - true_complete,
                    "wr_1h": true_wr_1h,
                    "wr_4h": true_wr_4h,
                    "wr_24h": true_wr_24h,
                    "avg_confluence_score": true_avg_score,
                },
                "structural_confluence": {
                    "scope": "STRUCTURAL_DUAL/TRIPLE only",
                    "total_events": structural_total,
                    "complete_events": structural_complete,
                    "pending_events": structural_total - structural_complete,
                    "wr_1h": structural_wr_1h,
                    "wr_4h": structural_wr_4h,
                    "wr_24h": structural_wr_24h,
                    "avg_confluence_score": structural_avg_score,
                },
            },
        }
    except Exception as e:
        log.warning("[CONF] summary error: %s", e)
        return {
            "total": 0,
            "total_events": 0,
            "complete_events": 0,
            "pending_events": 0,
            "phase": "OBSERVE",
            "next_milestone": 20,
            "wr_1h": None,
            "wr_4h": None,
            "wr_24h": None,
            "avg_confluence_score": None,
            "recent_dual_48h": 0,
            "recent_triple_48h": 0,
            "recent_structural_48h": 0,
            "last_ts": None,
            "ingest_status": "ERROR",
            "ingest_detail": str(e),
            "engine_wired": False,
            "whale_input_total": 0,
            "whale_in_range": 0,
            "whale_mint_resolved": 0,
            "whale_priced": 0,
            "whale_input_ready": 0,
            "whale_scanner_failed": 0,
            "whale_arkham_high": 0,
            "whale_arkham_meaningful": 0,
            "whale_arkham_none": 0,
            "whale_without_arkham": 0,
            "whale_meaningful_share_pct": None,
            "whale_top_fail_reason": None,
            "whale_bottleneck": "UNKNOWN",
            "whale_bottleneck_detail": "diagnostics unavailable",
            "memecoin_input_ready": 0,
            "exact_mint_overlap_ready": 0,
            "pre_confluence": {
                "status": "UNKNOWN",
                "detail": "pre-confluence diagnostics unavailable",
                "exact_mint_overlap_48h": 0,
                "exact_mint_overlap_7d": 0,
                "window_miss_count": 0,
                "symbol_family_overlap_count": 0,
                "symbol_family_overlap_distinct": 0,
                "closest_gap_hours": None,
                "examples": [],
            },
            "reinforcement": {
                "status": "INACTIVE",
                "detail": "reinforcement diagnostics unavailable",
                "candidate_count": 0,
                "top_candidates": [],
                "level_counts": {"STRONG": 0, "MODERATE": 0, "LIGHT": 0, "NONE": 0},
                "exact_overlap_candidates": 0,
                "symbol_family_candidates": 0,
                "relaxed_candidates": 0,
            },
            "validation_tracks": {
                "true_confluence": {
                    "scope": "fresh DUAL/TRIPLE only",
                    "total_events": 0,
                    "complete_events": 0,
                    "pending_events": 0,
                    "wr_1h": None,
                    "wr_4h": None,
                    "wr_24h": None,
                    "avg_confluence_score": None,
                },
                "structural_confluence": {
                    "scope": "STRUCTURAL_DUAL/TRIPLE only",
                    "total_events": 0,
                    "complete_events": 0,
                    "pending_events": 0,
                    "wr_1h": None,
                    "wr_4h": None,
                    "wr_24h": None,
                    "avg_confluence_score": None,
                },
            },
            "error": str(e),
        }


@router.get("/events")
def get_confluence_events(limit: int = 50, _user=Depends(get_current_user)):
    """Return recent confluence events ordered by newest first."""
    get_conn = _get_db()
    try:
        from utils.confluence_engine import ensure_confluence_tables  # type: ignore
        with get_conn() as conn:
            ensure_confluence_tables(conn)
            rows = conn.execute("""
                SELECT id, ts_utc, confluence_type, overlap_scope, token_symbol, token_mint,
                       source_count, sources, whale_alert_id, memecoin_scan_id,
                       whale_score, memecoin_score, arkham_score, arkham_signal_quality,
                       arkham_entity_name, arkham_entity_type, arkham_net_flow_usd, confluence_score,
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


@router.get("/opportunities")
def get_confluence_opportunities(limit: int = 50, _user=Depends(get_current_user)):
    """
    Stable operator-facing alias for recent confluence opportunities.
    Uses the same payload shape as /events so old callers don't fall through
    to the frontend app on missing routes.
    """
    return get_confluence_events(limit=limit, _user=_user)


@router.get("/pre-confluence")
def get_pre_confluence(limit: int = 10, _user=Depends(get_current_user)):
    """Return compact near-overlap diagnostics without loosening strict confluence rules."""
    get_conn = _get_db()
    try:
        with get_conn() as conn:
            return _get_pre_confluence_diagnostics(conn, limit=max(1, min(limit, 20)))
    except Exception as e:
        log.warning("[CONF] /pre-confluence error: %s", e)
        return {
            "status": "ERROR",
            "detail": str(e),
            "exact_mint_overlap_48h": 0,
            "exact_mint_overlap_7d": 0,
            "window_miss_count": 0,
            "symbol_family_overlap_count": 0,
            "symbol_family_overlap_distinct": 0,
            "closest_gap_hours": None,
            "examples": [],
            "error": str(e),
        }


@router.get("/stats")
def get_confluence_stats(_user=Depends(get_current_user)):
    """Return win rates by timeframe, current phase, and event counts."""
    return get_confluence_summary_data()


@router.get("/summary")
def get_confluence_summary(_user=Depends(get_current_user)):
    """Canonical compact summary/status for confluence consumers."""
    return get_confluence_summary_data()


def _build_reinforcement_summary(limit: int = 10) -> dict:
    """Return reinforcement scoring for primary memecoin candidates without requiring exact overlap."""
    get_conn = _get_db()
    try:
        with get_conn() as conn:
            return _get_reinforcement_summary(conn, limit=max(1, min(limit, 20)))
    except Exception as e:
        log.warning("[CONF] /reinforcement error: %s", e)
        return {
            "status": "ERROR",
            "detail": str(e),
            "candidate_count": 0,
            "top_candidates": [],
            "level_counts": {"STRONG": 0, "MODERATE": 0, "LIGHT": 0, "NONE": 0},
            "exact_overlap_candidates": 0,
            "relaxed_candidates": 0,
            "thresholds": _reinforcement_thresholds(),
            "error": str(e),
        }


@router.get("/reinforcement")
async def get_reinforcement_summary(limit: int = 10, _user=Depends(get_current_user)):
    """Return cached reinforcement scoring for primary memecoin candidates."""
    safe_limit = max(1, min(int(limit), 20))
    return await snapshot_or_build(
        f"confluence:reinforcement:{safe_limit}",
        lambda: _build_reinforcement_summary(safe_limit),
        fresh_s=120,
        stale_s=900,
        wait_timeout_s=3,
    )
