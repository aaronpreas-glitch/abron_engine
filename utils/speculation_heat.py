from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from utils.db import (
    get_conn,
    get_recent_speculation_heat_snapshots,
)


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, float(value)))


def _state_for(score: float) -> str:
    if score >= 80.0:
        return "OVERHEATED"
    if score >= 62.0:
        return "HOT"
    if score >= 42.0:
        return "WARM"
    return "COOL"


def _note_for(state: str, momentum: str) -> str:
    state = str(state or "COOL").upper()
    momentum = str(momentum or "STEADY").upper()
    if state == "OVERHEATED":
        return "Speculative conditions are stretched. Favor discipline and treat late-stage breakouts carefully."
    if state == "HOT":
        return (
            "Speculative participation is elevated."
            if momentum != "RISING"
            else "Speculative participation is accelerating. Watch for froth and narrowing quality."
        )
    if state == "WARM":
        return "Speculation is active but still controlled. Good time to compare quality against heat."
    return "Speculative activity is subdued. Favor patience over forcing lower-energy setups."


def _momentum(current_score: float, history: list[dict]) -> str:
    if len(history) < 2:
        return "STEADY"
    prev = float((history[1] or {}).get("heat_score") or 0.0)
    delta = float(current_score) - prev
    if delta >= 8.0:
        return "RISING"
    if delta <= -8.0:
        return "COOLING"
    return "STEADY"


def build_speculation_heat_snapshot(
    *,
    lookback_hours: int = 12,
    candidate_limit: int = 25,
) -> dict:
    """
    Build a compact speculation-heat snapshot from live memecoin activity.

    The score is intentionally interpretable:
    - speculation_heat_score: participation / throughput
    - froth_score: overheated or low-quality participation
    - sponsorship_score: wallet / proof sponsorship behind current names
    - quality_score: average trade quality of the recent candidate flow
    """
    from utils.funding_monitor import get_funding_rates
    from utils.memecoin_manager import get_proof_candidate_snapshot

    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(hours=max(1, int(lookback_hours)))).strftime("%Y-%m-%d %H:%M:%S")

    scanner_count = 0
    scanner_unique = 0
    high_score_count = 0
    warn_count = 0
    danger_count = 0
    avg_scanner_score = 0.0

    tq_count = 0
    tq_avg = 0.0
    verdict_counts = {"CLEAN": 0, "WATCH": 0, "UNSTABLE": 0, "AVOID": 0}

    wallet_counts = {"STRONG": 0, "MODERATE": 0, "LIGHT": 0, "NONE": 0}
    wallet_behavior_counts = {"ENTERING": 0, "ADDING": 0, "HOLDING": 0, "EXITING": 0, "MIXED": 0}
    wallet_behavior_quality_counts = {"STRONG": 0, "MODERATE": 0, "LIGHT": 0, "NONE": 0}
    wallet_behavior_conviction_avg = 0.0
    wallet_behavior_cluster_avg = 0.0
    large_trade_count = 0
    large_trade_buy_volume = 0.0
    large_trade_sell_volume = 0.0
    large_trade_unique_wallets = 0
    large_trade_strong_labels = 0

    with get_conn() as conn:
        conn.row_factory = sqlite3.Row
        scanner_row = conn.execute(
            """
            SELECT
                COUNT(*) AS n,
                COUNT(DISTINCT mint) AS unique_mints,
                SUM(CASE WHEN score >= 70 THEN 1 ELSE 0 END) AS high_score_n,
                SUM(CASE WHEN UPPER(COALESCE(rug_label, 'UNKNOWN')) = 'WARN' THEN 1 ELSE 0 END) AS warn_n,
                SUM(CASE WHEN UPPER(COALESCE(rug_label, 'UNKNOWN')) IN ('DANGER', 'RUGGED') THEN 1 ELSE 0 END) AS danger_n,
                AVG(COALESCE(score, 0)) AS avg_score
            FROM memecoin_signal_outcomes
            WHERE source='SCANNER'
              AND scanned_at >= ?
            """,
            (cutoff,),
        ).fetchone()
        if scanner_row:
            scanner_count = int(scanner_row["n"] or 0)
            scanner_unique = int(scanner_row["unique_mints"] or 0)
            high_score_count = int(scanner_row["high_score_n"] or 0)
            warn_count = int(scanner_row["warn_n"] or 0)
            danger_count = int(scanner_row["danger_n"] or 0)
            avg_scanner_score = float(scanner_row["avg_score"] or 0.0)

        tq_row = conn.execute(
            """
            SELECT
                COUNT(*) AS n,
                AVG(COALESCE(execution_quality_score, 0)) AS avg_execution_quality,
                SUM(CASE WHEN UPPER(COALESCE(quality_verdict, 'WATCH')) = 'CLEAN' THEN 1 ELSE 0 END) AS clean_n,
                SUM(CASE WHEN UPPER(COALESCE(quality_verdict, 'WATCH')) = 'WATCH' THEN 1 ELSE 0 END) AS watch_n,
                SUM(CASE WHEN UPPER(COALESCE(quality_verdict, 'WATCH')) = 'UNSTABLE' THEN 1 ELSE 0 END) AS unstable_n,
                SUM(CASE WHEN UPPER(COALESCE(quality_verdict, 'WATCH')) = 'AVOID' THEN 1 ELSE 0 END) AS avoid_n
            FROM memecoin_trade_quality_snapshots
            WHERE ts_utc >= ?
            """,
            (cutoff,),
        ).fetchone()
        if tq_row:
            tq_count = int(tq_row["n"] or 0)
            tq_avg = float(tq_row["avg_execution_quality"] or 0.0)
            verdict_counts = {
                "CLEAN": int(tq_row["clean_n"] or 0),
                "WATCH": int(tq_row["watch_n"] or 0),
                "UNSTABLE": int(tq_row["unstable_n"] or 0),
                "AVOID": int(tq_row["avoid_n"] or 0),
            }

        wallet_rows = conn.execute(
            """
            SELECT wallet_support_level, COUNT(*) AS n
            FROM mint_wallet_reinforcement_snapshots
            WHERE ts_utc >= ?
            GROUP BY wallet_support_level
            """,
            (cutoff,),
        ).fetchall()
        for row in wallet_rows:
            level = str(row["wallet_support_level"] or "NONE").upper()
            wallet_counts[level if level in wallet_counts else "NONE"] = int(row["n"] or 0)

        large_trade_row = conn.execute(
            """
            SELECT
                COUNT(*) AS n,
                SUM(CASE WHEN UPPER(COALESCE(trade_side, 'BUY'))='BUY' THEN COALESCE(volume_usd, 0) ELSE 0 END) AS buy_volume,
                SUM(CASE WHEN UPPER(COALESCE(trade_side, 'BUY'))='SELL' THEN COALESCE(volume_usd, 0) ELSE 0 END) AS sell_volume,
                COUNT(DISTINCT owner) AS unique_wallets,
                SUM(CASE WHEN UPPER(COALESCE(sponsorship_label, ''))='STRONG' THEN 1 ELSE 0 END) AS strong_labels
            FROM memecoin_large_trade_snapshots
            WHERE ts_utc >= ?
            """,
            (cutoff,),
        ).fetchone()
        if large_trade_row:
            large_trade_count = int(large_trade_row["n"] or 0)
            large_trade_buy_volume = float(large_trade_row["buy_volume"] or 0.0)
            large_trade_sell_volume = float(large_trade_row["sell_volume"] or 0.0)
            large_trade_unique_wallets = int(large_trade_row["unique_wallets"] or 0)
            large_trade_strong_labels = int(large_trade_row["strong_labels"] or 0)

        wallet_behavior_row = conn.execute(
            """
            SELECT
                COUNT(*) AS n,
                AVG(COALESCE(wallet_conviction_score, 0)) AS avg_conviction,
                AVG(COALESCE(wallet_cluster_score, 0)) AS avg_cluster,
                SUM(CASE WHEN UPPER(COALESCE(wallet_behavior_state, 'MIXED'))='ENTERING' THEN 1 ELSE 0 END) AS entering_n,
                SUM(CASE WHEN UPPER(COALESCE(wallet_behavior_state, 'MIXED'))='ADDING' THEN 1 ELSE 0 END) AS adding_n,
                SUM(CASE WHEN UPPER(COALESCE(wallet_behavior_state, 'MIXED'))='HOLDING' THEN 1 ELSE 0 END) AS holding_n,
                SUM(CASE WHEN UPPER(COALESCE(wallet_behavior_state, 'MIXED'))='EXITING' THEN 1 ELSE 0 END) AS exiting_n,
                SUM(CASE WHEN UPPER(COALESCE(wallet_behavior_state, 'MIXED'))='MIXED' THEN 1 ELSE 0 END) AS mixed_n,
                SUM(CASE WHEN UPPER(COALESCE(smart_money_quality, 'NONE'))='STRONG' THEN 1 ELSE 0 END) AS strong_q,
                SUM(CASE WHEN UPPER(COALESCE(smart_money_quality, 'NONE'))='MODERATE' THEN 1 ELSE 0 END) AS moderate_q,
                SUM(CASE WHEN UPPER(COALESCE(smart_money_quality, 'NONE'))='LIGHT' THEN 1 ELSE 0 END) AS light_q,
                SUM(CASE WHEN UPPER(COALESCE(smart_money_quality, 'NONE'))='NONE' THEN 1 ELSE 0 END) AS none_q
            FROM mint_wallet_behavior_snapshots
            WHERE ts_utc >= ?
            """,
            (cutoff,),
        ).fetchone()
        if wallet_behavior_row:
            wallet_behavior_conviction_avg = float(wallet_behavior_row["avg_conviction"] or 0.0)
            wallet_behavior_cluster_avg = float(wallet_behavior_row["avg_cluster"] or 0.0)
            wallet_behavior_counts = {
                "ENTERING": int(wallet_behavior_row["entering_n"] or 0),
                "ADDING": int(wallet_behavior_row["adding_n"] or 0),
                "HOLDING": int(wallet_behavior_row["holding_n"] or 0),
                "EXITING": int(wallet_behavior_row["exiting_n"] or 0),
                "MIXED": int(wallet_behavior_row["mixed_n"] or 0),
            }
            wallet_behavior_quality_counts = {
                "STRONG": int(wallet_behavior_row["strong_q"] or 0),
                "MODERATE": int(wallet_behavior_row["moderate_q"] or 0),
                "LIGHT": int(wallet_behavior_row["light_q"] or 0),
                "NONE": int(wallet_behavior_row["none_q"] or 0),
            }

    proof_snapshot = get_proof_candidate_snapshot(limit=max(5, int(candidate_limit)))
    candidates = list(proof_snapshot.get("candidates") or [])
    proof_ready = [
        c for c in candidates
        if str(c.get("proof_status") or "").upper() == "PROOF_READY"
    ]
    near_ready = [
        c for c in candidates
        if str(c.get("proof_status") or "").upper() == "RESEARCH_ONLY"
        and float(c.get("readiness_score") or 0.0) >= 60.0
    ]

    strong_support_now = sum(1 for c in candidates if float(c.get("support_score") or 0.0) >= 70.0)
    strong_wallet_now = sum(
        1
        for c in candidates
        if str(c.get("wallet_reinforcement_level") or "NONE").upper() in ("MODERATE", "STRONG")
    )
    low_quality_now = sum(
        1
        for c in candidates
        if str(c.get("market_quality_verdict") or "").upper() in ("UNSTABLE", "AVOID")
        or float(c.get("market_quality_score") or 0.0) < 45.0
    )

    scanner_scale = _clamp(scanner_count / 10.0 * 35.0)
    unique_scale = _clamp(scanner_unique / 8.0 * 20.0)
    high_score_scale = _clamp(high_score_count * 4.0, 0.0, 20.0)
    proof_scale = _clamp(len(proof_ready) * 8.0 + len(near_ready) * 3.0, 0.0, 25.0)
    speculation_heat_score = round(_clamp(scanner_scale + unique_scale + high_score_scale + proof_scale), 1)

    warn_share = (warn_count / scanner_count) if scanner_count else 0.0
    danger_share = (danger_count / scanner_count) if scanner_count else 0.0
    unstable_share = ((verdict_counts["UNSTABLE"] + verdict_counts["AVOID"]) / tq_count) if tq_count else 0.0
    quality_heat_divergence = max(0.0, avg_scanner_score - max(tq_avg, 0.0))
    froth_score = 0.0
    froth_score += _clamp(warn_share * 35.0, 0.0, 35.0)
    froth_score += _clamp(danger_share * 40.0, 0.0, 25.0)
    froth_score += _clamp(unstable_share * 45.0, 0.0, 25.0)
    froth_score += _clamp(quality_heat_divergence * 0.7, 0.0, 15.0)

    funding_rates = get_funding_rates() or {}
    funding_points = 0.0
    funding_values: dict[str, float] = {}
    for sym in ("SOL", "BTC", "ETH"):
        try:
            rate = float(((funding_rates.get(sym) or {}).get("rate")) or 0.0)
        except Exception:
            rate = 0.0
        funding_values[sym] = rate
        if rate > 0:
            funding_points += min(6.0, rate * 10_000.0)
    froth_score = round(_clamp(froth_score + funding_points), 1)

    sponsorship_score = round(
        _clamp(
            wallet_counts["STRONG"] * 12.0
            + wallet_counts["MODERATE"] * 7.0
            + strong_support_now * 5.0
            + strong_wallet_now * 4.0
            + min(18.0, large_trade_buy_volume / 25000.0 * 4.0)
            + min(10.0, large_trade_unique_wallets * 1.5)
            + min(12.0, large_trade_strong_labels * 4.0)
            + wallet_behavior_quality_counts["STRONG"] * 6.0
            + wallet_behavior_quality_counts["MODERATE"] * 3.5
            + min(12.0, max(wallet_behavior_conviction_avg - 50.0, 0.0) * 0.25)
        ),
        1,
    )

    clean_share = (verdict_counts["CLEAN"] / tq_count) if tq_count else 0.0
    watch_share = (verdict_counts["WATCH"] / tq_count) if tq_count else 0.0
    quality_score = 0.0
    quality_score += _clamp(tq_avg * 0.6, 0.0, 60.0)
    quality_score += _clamp(clean_share * 25.0, 0.0, 25.0)
    quality_score += _clamp(watch_share * 10.0, 0.0, 10.0)
    quality_score -= _clamp(low_quality_now * 5.0, 0.0, 20.0)
    quality_score = round(_clamp(quality_score), 1)

    heat_score = round(
        _clamp(
            speculation_heat_score * 0.45
            + froth_score * 0.30
            + sponsorship_score * 0.15
            + quality_score * 0.10
        ),
        1,
    )

    history = get_recent_speculation_heat_snapshots(limit=3)
    momentum = _momentum(heat_score, history)
    heat_state = _state_for(heat_score)

    reasons: list[str] = []
    if speculation_heat_score >= 60.0:
        reasons.append("scanner participation is elevated")
    elif speculation_heat_score <= 25.0:
        reasons.append("scanner participation is subdued")
    if len(proof_ready) > 0:
        reasons.append(f"{len(proof_ready)} proof-ready name{'s' if len(proof_ready) != 1 else ''} on deck")
    if sponsorship_score >= 50.0:
        reasons.append("wallet sponsorship is meaningfully present")
    if wallet_behavior_counts["ENTERING"] + wallet_behavior_counts["ADDING"] > wallet_behavior_counts["EXITING"]:
        reasons.append("tracked wallets are still leaning into names")
    if large_trade_count > 0 and large_trade_buy_volume > large_trade_sell_volume:
        reasons.append("large-trade sponsorship is leaning net-buy")
    if froth_score >= 60.0:
        reasons.append("froth is rising faster than quality")
    elif froth_score <= 25.0 and quality_score >= 60.0:
        reasons.append("quality is holding up better than the heat")
    if funding_points >= 8.0:
        reasons.append("perp funding is leaning euphoric")

    top_candidates = [
        {
            "symbol": str(c.get("symbol") or ""),
            "readiness_score": float(c.get("readiness_score") or 0.0),
            "support_score": float(c.get("support_score") or 0.0),
            "market_quality_score": float(c.get("market_quality_score") or 0.0),
            "market_quality_verdict": str(c.get("market_quality_verdict") or ""),
            "proof_status": str(c.get("proof_status") or ""),
        }
        for c in candidates[:3]
    ]

    return {
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "heat_state": heat_state,
        "heat_score": heat_score,
        "momentum": momentum,
        "speculation_heat_score": speculation_heat_score,
        "froth_score": froth_score,
        "sponsorship_score": sponsorship_score,
        "quality_score": quality_score,
        "note": _note_for(heat_state, momentum),
        "reasons": reasons[:5],
        "inputs": {
            "lookback_hours": int(lookback_hours),
            "scanner_count": scanner_count,
            "scanner_unique": scanner_unique,
            "high_score_count": high_score_count,
            "avg_scanner_score": round(avg_scanner_score, 1),
            "warn_count": warn_count,
            "danger_count": danger_count,
            "trade_quality_count": tq_count,
            "trade_quality_avg": round(tq_avg, 1),
            "trade_quality_verdicts": verdict_counts,
            "wallet_support_counts": wallet_counts,
            "wallet_behavior_counts": wallet_behavior_counts,
            "wallet_behavior_quality_counts": wallet_behavior_quality_counts,
            "wallet_behavior_conviction_avg": round(wallet_behavior_conviction_avg, 1),
            "wallet_behavior_cluster_avg": round(wallet_behavior_cluster_avg, 1),
            "large_trade_count": large_trade_count,
            "large_trade_buy_volume_usd": round(large_trade_buy_volume, 2),
            "large_trade_sell_volume_usd": round(large_trade_sell_volume, 2),
            "large_trade_unique_wallets": large_trade_unique_wallets,
            "large_trade_strong_labels": large_trade_strong_labels,
            "proof_ready_count": len(proof_ready),
            "near_ready_count": len(near_ready),
            "strong_support_now": strong_support_now,
            "strong_wallet_now": strong_wallet_now,
            "low_quality_now": low_quality_now,
            "funding_rates": funding_values,
            "top_candidates": top_candidates,
        },
    }
