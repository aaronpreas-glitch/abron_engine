from __future__ import annotations

import json
import os
from datetime import datetime, timezone, timedelta
from typing import Any

from utils.db import get_conn, get_latest_memecoin_token_stats_for_mints

MARKET_MAX_AGE_MINUTES = int(os.getenv("RUNNER_REVIEW_MARKET_MAX_AGE_MINUTES", "360"))
DEX_FALLBACK_ENABLED = os.getenv("RUNNER_REVIEW_DEX_FALLBACK_ENABLED", "true").lower() in {"1", "true", "yes"}
DEX_FALLBACK_EVAL_LIMIT = int(os.getenv("RUNNER_REVIEW_DEX_FALLBACK_EVAL_LIMIT", "12"))
AUTO_PAPER_COOLDOWN_HOURS = float(os.getenv("RUNNER_REVIEW_AUTO_PAPER_COOLDOWN_HOURS", "18"))
AUTO_PAPER_MAX_PER_STEP = int(os.getenv("RUNNER_REVIEW_AUTO_PAPER_MAX_PER_STEP", "5"))
BUYABLE_AUTO_PAPER_ENABLED = os.getenv("RUNNER_REVIEW_BUYABLE_AUTO_PAPER_ENABLED", "true").lower() in {"1", "true", "yes"}
BUYABLE_AUTO_PAPER_MAX_PER_STEP = int(os.getenv("RUNNER_REVIEW_BUYABLE_AUTO_PAPER_MAX_PER_STEP", "5"))
BUYABLE_AUTO_PAPER_MIN_PRIORITY = float(os.getenv("RUNNER_REVIEW_BUYABLE_AUTO_PAPER_MIN_PRIORITY", "75"))
PROTECT_PROFIT_RETURN_PCT = float(os.getenv("RUNNER_REVIEW_PROTECT_PROFIT_RETURN_PCT", "20"))
STRONG_PROFIT_RETURN_PCT = float(os.getenv("RUNNER_REVIEW_STRONG_PROFIT_RETURN_PCT", "30"))
GIVEBACK_WARNING_PCT = float(os.getenv("RUNNER_REVIEW_GIVEBACK_WARNING_PCT", "12"))
EXIT_GIVEBACK_PCT = float(os.getenv("RUNNER_REVIEW_EXIT_GIVEBACK_PCT", "18"))
MOMENTUM_FADE_BUY_PRESSURE = float(os.getenv("RUNNER_REVIEW_MOMENTUM_FADE_BUY_PRESSURE", "45"))
MOMENTUM_FADE_VOL_ACCEL = float(os.getenv("RUNNER_REVIEW_MOMENTUM_FADE_VOL_ACCEL", "2"))

HORIZONS: tuple[tuple[str, float], ...] = (
    ("1h", 1.0),
    ("4h", 4.0),
    ("24h", 24.0),
    ("72h", 72.0),
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS runner_review_decisions (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    created_ts            TEXT NOT NULL,
    symbol                TEXT,
    mint                  TEXT NOT NULL,
    runner_state          TEXT,
    decision              TEXT NOT NULL,
    operator_note         TEXT,
    blocker_key           TEXT,
    score                 REAL,
    proof_score           REAL,
    readiness_score       REAL,
    market_quality_score  REAL,
    buy_pressure          REAL,
    vol_acceleration      REAL,
    entry_window          TEXT,
    fuel_quality          TEXT,
    move_phase            TEXT,
    first_leg_confirmed   INTEGER,
    snapshot_json         TEXT
)
"""

MIGRATIONS: tuple[str, ...] = (
    "ALTER TABLE runner_review_decisions ADD COLUMN entry_price REAL",
    "ALTER TABLE runner_review_decisions ADD COLUMN entry_marketcap REAL",
    "ALTER TABLE runner_review_decisions ADD COLUMN entry_stats_ts TEXT",
    "ALTER TABLE runner_review_decisions ADD COLUMN current_price REAL",
    "ALTER TABLE runner_review_decisions ADD COLUMN current_marketcap REAL",
    "ALTER TABLE runner_review_decisions ADD COLUMN current_stats_ts TEXT",
    "ALTER TABLE runner_review_decisions ADD COLUMN return_1h_pct REAL",
    "ALTER TABLE runner_review_decisions ADD COLUMN return_4h_pct REAL",
    "ALTER TABLE runner_review_decisions ADD COLUMN return_24h_pct REAL",
    "ALTER TABLE runner_review_decisions ADD COLUMN return_72h_pct REAL",
    "ALTER TABLE runner_review_decisions ADD COLUMN max_return_pct REAL",
    "ALTER TABLE runner_review_decisions ADD COLUMN min_return_pct REAL",
    "ALTER TABLE runner_review_decisions ADD COLUMN outcome_status TEXT NOT NULL DEFAULT 'PENDING'",
    "ALTER TABLE runner_review_decisions ADD COLUMN outcome_label TEXT",
    "ALTER TABLE runner_review_decisions ADD COLUMN evaluated_ts TEXT",
    "ALTER TABLE runner_review_decisions ADD COLUMN follow_up_status TEXT",
    "ALTER TABLE runner_review_decisions ADD COLUMN follow_up_due_ts TEXT",
    "ALTER TABLE runner_review_decisions ADD COLUMN follow_up_reason TEXT",
    "ALTER TABLE runner_review_decisions ADD COLUMN suppressed_until_ts TEXT",
    "ALTER TABLE runner_review_decisions ADD COLUMN exit_plan_json TEXT",
    "ALTER TABLE runner_review_decisions ADD COLUMN resolved_ts TEXT",
    "ALTER TABLE runner_review_decisions ADD COLUMN suggested_decision TEXT",
    "ALTER TABLE runner_review_decisions ADD COLUMN suggestion_confidence REAL",
    "ALTER TABLE runner_review_decisions ADD COLUMN suggestion_reason TEXT",
    "ALTER TABLE runner_review_decisions ADD COLUMN entry_capture_status TEXT",
    "ALTER TABLE runner_review_decisions ADD COLUMN entry_market_source TEXT",
    "ALTER TABLE runner_review_decisions ADD COLUMN entry_data_age_seconds REAL",
    "ALTER TABLE runner_review_decisions ADD COLUMN current_market_source TEXT",
    "ALTER TABLE runner_review_decisions ADD COLUMN current_data_age_seconds REAL",
    "ALTER TABLE runner_review_decisions ADD COLUMN decision_alignment TEXT",
    "ALTER TABLE runner_review_decisions ADD COLUMN best_horizon TEXT",
    "ALTER TABLE runner_review_decisions ADD COLUMN decision_source TEXT",
    "ALTER TABLE runner_review_decisions ADD COLUMN current_return_pct REAL",
    "ALTER TABLE runner_review_decisions ADD COLUMN drawdown_from_max_pct REAL",
    "ALTER TABLE runner_review_decisions ADD COLUMN exit_signal TEXT",
    "ALTER TABLE runner_review_decisions ADD COLUMN exit_urgency TEXT",
    "ALTER TABLE runner_review_decisions ADD COLUMN exit_signal_reason TEXT",
    "ALTER TABLE runner_review_decisions ADD COLUMN exit_signal_ts TEXT",
    "ALTER TABLE runner_review_decisions ADD COLUMN conviction_band TEXT",
    "ALTER TABLE runner_review_decisions ADD COLUMN good_coin_score REAL",
    "ALTER TABLE runner_review_decisions ADD COLUMN good_coin_status TEXT",
    "ALTER TABLE runner_review_decisions ADD COLUMN catalyst_strength_score REAL",
    "ALTER TABLE runner_review_decisions ADD COLUMN catalyst_strength_label TEXT",
    "ALTER TABLE runner_review_decisions ADD COLUMN too_late_score REAL",
    "ALTER TABLE runner_review_decisions ADD COLUMN too_late_label TEXT",
    "ALTER TABLE runner_review_decisions ADD COLUMN operator_priority REAL",
    "ALTER TABLE runner_review_decisions ADD COLUMN research_score REAL",
    "ALTER TABLE runner_review_decisions ADD COLUMN conviction_attribution_source TEXT",
    "ALTER TABLE runner_review_decisions ADD COLUMN entry_quality_score REAL",
    "ALTER TABLE runner_review_decisions ADD COLUMN entry_quality_label TEXT",
    "ALTER TABLE runner_review_decisions ADD COLUMN entry_quality_reason TEXT",
    "ALTER TABLE runner_review_decisions ADD COLUMN signal_decay_score REAL",
    "ALTER TABLE runner_review_decisions ADD COLUMN signal_decay_label TEXT",
    "ALTER TABLE runner_review_decisions ADD COLUMN paper_position_units REAL",
    "ALTER TABLE runner_review_decisions ADD COLUMN paper_position_label TEXT",
    "ALTER TABLE runner_review_decisions ADD COLUMN simulated_pnl_units REAL",
    "ALTER TABLE runner_review_decisions ADD COLUMN management_action TEXT",
    "ALTER TABLE runner_review_decisions ADD COLUMN management_reason TEXT",
)


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_ts(value: object) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _f(value: object, default: float = 0.0) -> float:
    try:
        return float(value if value is not None else default)
    except Exception:
        return default


def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


def _stats_age_seconds(ts: object) -> float | None:
    dt = _parse_ts(ts)
    if dt is None:
        return None
    return max(0.0, (datetime.now(timezone.utc) - dt).total_seconds())


def _is_fresh_market(stats: dict[str, Any]) -> bool:
    if not stats:
        return False
    if _f(stats.get("marketcap")) <= 0 and _f(stats.get("price")) <= 0:
        return False
    age = _stats_age_seconds(stats.get("ts_utc"))
    if age is None:
        return False
    return age <= max(60, MARKET_MAX_AGE_MINUTES * 60)


def _latest_research_attribution(conn, mint: str) -> dict[str, Any]:
    mint = str(mint or "").strip()
    if not mint:
        return {}
    try:
        has_table = conn.execute(
            """
            SELECT 1
            FROM sqlite_master
            WHERE type='table'
              AND name='memecoin_research_dossiers'
            LIMIT 1
            """
        ).fetchone()
        if not has_table:
            return {}
        row = conn.execute(
            """
            SELECT conviction_band, good_coin_score, good_coin_status,
                   catalyst_strength_score, catalyst_strength_label,
                   too_late_score, too_late_label, operator_priority,
                   research_score, generated_at
            FROM memecoin_research_dossiers
            WHERE mint=?
            LIMIT 1
            """,
            (mint,),
        ).fetchone()
        return dict(row) if row else {}
    except Exception:
        return {}


def _decision_attribution(payload: dict[str, Any], conn, mint: str) -> dict[str, Any]:
    dossier = _latest_research_attribution(conn, mint)

    def pick(key: str, default=None):
        value = payload.get(key)
        if value is not None and value != "":
            return value
        return dossier.get(key, default)

    source = str(payload.get("conviction_attribution_source") or "").strip().upper()
    if not source:
        source = "PAYLOAD" if payload.get("conviction_band") else "RESEARCH_DOSSIER" if dossier else "NONE"
    return {
        "conviction_band": str(pick("conviction_band") or "").upper() or None,
        "good_coin_score": pick("good_coin_score"),
        "good_coin_status": str(pick("good_coin_status") or "").upper() or None,
        "catalyst_strength_score": pick("catalyst_strength_score"),
        "catalyst_strength_label": str(pick("catalyst_strength_label") or "").upper() or None,
        "too_late_score": pick("too_late_score"),
        "too_late_label": str(pick("too_late_label") or "").upper() or None,
        "operator_priority": pick("operator_priority"),
        "research_score": pick("research_score"),
        "conviction_attribution_source": source,
    }


def _normalize_market(raw: dict[str, Any] | None, *, source: str) -> dict[str, Any]:
    data = dict(raw or {})
    marketcap = _f(data.get("marketcap") or data.get("market_cap") or data.get("fdv"))
    price = _f(data.get("price") or data.get("price_usd"))
    ts_utc = str(data.get("ts_utc") or "") or _iso()
    return {
        **data,
        "price": price or None,
        "marketcap": marketcap or None,
        "ts_utc": ts_utc,
        "market_source": source,
        "data_age_seconds": _stats_age_seconds(ts_utc),
    }


def _ensure_schema(conn) -> None:
    conn.execute(SCHEMA)
    for ddl in MIGRATIONS:
        try:
            conn.execute(ddl)
        except Exception:
            pass
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_runner_review_mint_created
        ON runner_review_decisions(mint, created_ts)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_runner_review_outcome_status
        ON runner_review_decisions(outcome_status, created_ts)
        """
    )
    conn.commit()


def _initial_follow_up(decision: str) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    decision = str(decision or "").upper()
    if decision == "MANUAL_BUY":
        return {
            "follow_up_status": "MANAGED_OBSERVATION",
            "follow_up_due_ts": (now + timedelta(minutes=15)).isoformat(),
            "follow_up_reason": "Manual buy marked; monitor exit warnings, momentum loss, and profit protection.",
            "suppressed_until_ts": None,
            "exit_plan": {
                "mode": "manual_buy_observation",
                "check_every_minutes": 15,
                "warn_if": ["runner_extension_risk", "market_quality_unstable", "return_drawdown_from_max_gt_15pct"],
                "take_profit_context": ["consider scale-out if max_return_pct >= 25", "protect if momentum flips"],
            },
        }
    if decision == "WATCH":
        return {
            "follow_up_status": "ACTIVE_WATCH",
            "follow_up_due_ts": (now + timedelta(hours=1)).isoformat(),
            "follow_up_reason": "Watch decision active; check whether the blocker cleared or the setup expired.",
            "suppressed_until_ts": None,
            "exit_plan": {},
        }
    if decision == "TOO_LATE":
        return {
            "follow_up_status": "SUPPRESSED",
            "follow_up_due_ts": (now + timedelta(hours=12)).isoformat(),
            "follow_up_reason": "Too-late call suppresses chase; review only after reset or material state improvement.",
            "suppressed_until_ts": (now + timedelta(hours=12)).isoformat(),
            "exit_plan": {},
        }
    return {
        "follow_up_status": "SUPPRESSED",
        "follow_up_due_ts": (now + timedelta(hours=24)).isoformat(),
        "follow_up_reason": "Pass decision suppresses repeat review unless the runner state materially improves.",
        "suppressed_until_ts": (now + timedelta(hours=24)).isoformat(),
        "exit_plan": {},
    }


def _snapshot_live_stats(payload: dict[str, Any]) -> dict[str, Any]:
    snapshot = dict(payload.get("snapshot") or {})
    candidate = dict(snapshot.get("candidate") or {})
    components = dict(candidate.get("proof_components") or {})
    scanner = dict(components.get("scanner") or {})
    live = dict(candidate.get("live_token_stats") or scanner.get("live_token_stats") or {})
    if live:
        return _normalize_market(live, source="snapshot")
    return {}


def _dex_market(mint: str) -> dict[str, Any]:
    if not DEX_FALLBACK_ENABLED or not mint:
        return {}
    try:
        from data.dexscreener import fetch_token_snapshot  # type: ignore

        snap = fetch_token_snapshot(mint)
    except Exception:
        snap = None
    if not snap:
        return {}
    return _normalize_market(
        {
            "price": snap.get("price"),
            "marketcap": snap.get("market_cap") or snap.get("fdv"),
            "liquidity": snap.get("liquidity"),
            "volume_24h_usd": snap.get("volume_24h"),
            "price_change_1h_percent": snap.get("change_1h"),
            "price_change_24h_percent": snap.get("change_24h"),
            "ts_utc": _iso(),
        },
        source="dexscreener",
    )


def _latest_market(mint: str, *, allow_fallback: bool = True) -> dict[str, Any]:
    if not mint:
        return {}
    latest = get_latest_memecoin_token_stats_for_mints([mint], max_age_minutes=MARKET_MAX_AGE_MINUTES)
    if latest.get(mint):
        return _normalize_market(dict(latest.get(mint) or {}), source="token_stats")
    if allow_fallback:
        return _dex_market(mint)
    return {}


def _entry_market_context(payload: dict[str, Any]) -> dict[str, Any]:
    mint = str(payload.get("mint") or "").strip()
    live = _snapshot_live_stats(payload)
    latest = _latest_market(mint)
    explicit_price = _f(payload.get("entry_price"))
    explicit_marketcap = _f(payload.get("entry_marketcap"))
    if explicit_price > 0 or explicit_marketcap > 0:
        chosen = _normalize_market(
            {
                "price": explicit_price,
                "marketcap": explicit_marketcap,
                "ts_utc": payload.get("entry_stats_ts") or _iso(),
            },
            source="payload",
        )
    elif _is_fresh_market(live):
        chosen = live
    else:
        chosen = latest
    price = _f(chosen.get("price"))
    marketcap = _f(chosen.get("marketcap"))
    stats_ts = str(chosen.get("ts_utc") or "") or None
    source = str(chosen.get("market_source") or "missing")
    age = chosen.get("data_age_seconds")
    if price > 0 or marketcap > 0:
        capture_status = "FRESH" if (age is None or float(age) <= MARKET_MAX_AGE_MINUTES * 60) else "STALE"
    else:
        capture_status = "MISSING"
    return {
        "entry_price": price or None,
        "entry_marketcap": marketcap or None,
        "entry_stats_ts": stats_ts,
        "entry_capture_status": capture_status,
        "entry_market_source": source,
        "entry_data_age_seconds": age,
    }


def record_runner_review_decision(payload: dict[str, Any]) -> dict[str, Any]:
    decision = str(payload.get("decision") or "").strip().upper()
    if decision not in {"WATCH", "PASS", "MANUAL_BUY", "TOO_LATE"}:
        raise ValueError("invalid runner review decision")
    mint = str(payload.get("mint") or "").strip()
    if not mint:
        raise ValueError("mint is required")
    entry = _entry_market_context(payload)
    follow_up = _initial_follow_up(decision)
    suggested_decision = str(payload.get("suggested_decision") or "").strip().upper() or None
    decision_alignment = (
        "MATCHED_SYSTEM"
        if suggested_decision and suggested_decision == decision
        else "OVERRIDDEN_SYSTEM"
        if suggested_decision
        else "NO_SYSTEM_SUGGESTION"
    )
    decision_source = str(payload.get("decision_source") or "OPERATOR").strip().upper() or "OPERATOR"
    now = _iso()
    with get_conn() as conn:
        _ensure_schema(conn)
        attribution = _decision_attribution(payload, conn, mint)
        cur = conn.execute(
            """
            INSERT INTO runner_review_decisions
            (created_ts, symbol, mint, runner_state, decision, operator_note, blocker_key,
             score, proof_score, readiness_score, market_quality_score, buy_pressure,
             vol_acceleration, entry_window, fuel_quality, move_phase, first_leg_confirmed,
             snapshot_json, entry_price, entry_marketcap, entry_stats_ts, outcome_status,
             follow_up_status, follow_up_due_ts, follow_up_reason, suppressed_until_ts,
             exit_plan_json, suggested_decision, suggestion_confidence, suggestion_reason,
             entry_capture_status, entry_market_source, entry_data_age_seconds, decision_alignment,
             decision_source, conviction_band, good_coin_score, good_coin_status,
             catalyst_strength_score, catalyst_strength_label, too_late_score,
             too_late_label, operator_priority, research_score, conviction_attribution_source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING',
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                now,
                payload.get("symbol"),
                mint,
                payload.get("runner_state"),
                decision,
                payload.get("operator_note"),
                payload.get("blocker_key"),
                payload.get("score"),
                payload.get("proof_score"),
                payload.get("readiness_score"),
                payload.get("market_quality_score"),
                payload.get("buy_pressure"),
                payload.get("vol_acceleration"),
                payload.get("entry_window"),
                payload.get("fuel_quality"),
                payload.get("move_phase"),
                int(payload.get("first_leg_confirmed") or 0),
                json.dumps(payload.get("snapshot") or {}, separators=(",", ":")),
                entry["entry_price"],
                entry["entry_marketcap"],
                entry["entry_stats_ts"],
                follow_up["follow_up_status"],
                follow_up["follow_up_due_ts"],
                follow_up["follow_up_reason"],
                follow_up["suppressed_until_ts"],
                json.dumps(follow_up["exit_plan"], separators=(",", ":")),
                suggested_decision,
                payload.get("suggestion_confidence"),
                payload.get("suggestion_reason"),
                entry["entry_capture_status"],
                entry["entry_market_source"],
                entry["entry_data_age_seconds"],
                decision_alignment,
                decision_source,
                attribution.get("conviction_band"),
                attribution.get("good_coin_score"),
                attribution.get("good_coin_status"),
                attribution.get("catalyst_strength_score"),
                attribution.get("catalyst_strength_label"),
                attribution.get("too_late_score"),
                attribution.get("too_late_label"),
                attribution.get("operator_priority"),
                attribution.get("research_score"),
                attribution.get("conviction_attribution_source"),
            ),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM runner_review_decisions WHERE id=?", (cur.lastrowid,)).fetchone()
    return row_to_dict(row)


def _runner_candidate_payload(candidate: dict[str, Any]) -> dict[str, Any] | None:
    components = dict(candidate.get("proof_components") or {})
    scanner = dict(components.get("scanner") or {})
    lifecycle = dict(components.get("lifecycle") or {})
    policy = dict(candidate.get("established_runner_policy") or components.get("established_runner_policy") or {})
    profile = candidate.get("established_runner_profile") or components.get("established_runner")
    state = str(policy.get("state") or candidate.get("runner_policy_state") or "").upper()
    mint = str(candidate.get("mint") or "").strip()
    symbol = str(candidate.get("symbol") or "").strip().upper()
    if not profile or not mint or not symbol or state != "RUNNER_READY":
        return None

    market_quality = _f(candidate.get("market_quality_score") or scanner.get("market_quality_score"))
    buy_pressure = _f(scanner.get("buy_pressure"))
    vol_accel = _f(scanner.get("vol_acceleration"))
    if market_quality < 70.0:
        return None
    if buy_pressure < 50.0 and vol_accel < 3.0:
        return None

    blocker = str(candidate.get("blocker_key") or components.get("blocker_key") or policy.get("blocker_key") or "").strip()
    blocked_safety = {
        "rug_warning",
        "rug_danger",
        "rug_not_good",
        "mint_not_revoked",
        "holder_concentration",
        "market_quality_avoid",
        "market_quality_unstable",
    }
    if blocker in blocked_safety:
        return None

    confidence = 86
    first_leg = int(candidate.get("first_leg_confirmed") or lifecycle.get("first_leg_confirmed") or 0)
    if market_quality >= 80 and buy_pressure >= 60 and vol_accel >= 4 and first_leg:
        confidence = 92

    return {
        "symbol": symbol,
        "mint": mint,
        "runner_state": state,
        "decision": "MANUAL_BUY",
        "decision_source": "SYSTEM_PAPER",
        "operator_note": "System-created paper runner entry from RUNNER_READY manual-buy recommendation; no live capital deployed.",
        "blocker_key": blocker or None,
        "score": candidate.get("score"),
        "proof_score": candidate.get("proof_score"),
        "readiness_score": candidate.get("readiness_score"),
        "market_quality_score": candidate.get("market_quality_score") or scanner.get("market_quality_score"),
        "buy_pressure": scanner.get("buy_pressure"),
        "vol_acceleration": scanner.get("vol_acceleration"),
        "entry_window": candidate.get("entry_window") or lifecycle.get("entry_window"),
        "fuel_quality": candidate.get("fuel_quality") or lifecycle.get("fuel_quality"),
        "move_phase": candidate.get("move_phase") or lifecycle.get("move_phase"),
        "first_leg_confirmed": first_leg,
        "suggested_decision": "MANUAL_BUY",
        "suggestion_confidence": confidence,
        "suggestion_reason": "Auto-paper entry: runner is ready, quality is acceptable, and real authority remains cautious.",
        "snapshot": {
            "candidate": candidate,
            "policy": policy,
            "profile": profile,
            "auto_paper": {
                "reason": "authority_aware_paper_entry_bridge",
                "market_quality_score": market_quality,
                "buy_pressure": buy_pressure,
                "vol_acceleration": vol_accel,
            },
        },
    }


def _recent_runner_decision_exists(conn, mint: str, *, cooldown_hours: float) -> bool:
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=max(0.25, cooldown_hours))).isoformat()
    row = conn.execute(
        """
        SELECT id
        FROM runner_review_decisions
        WHERE mint=?
          AND created_ts >= ?
          AND decision IN ('MANUAL_BUY', 'WATCH', 'PASS', 'TOO_LATE')
        ORDER BY id DESC
        LIMIT 1
        """,
        (mint, cutoff),
    ).fetchone()
    return bool(row)


def _buyable_dossier_payload(dossier: dict[str, Any]) -> dict[str, Any] | None:
    mint = str(dossier.get("mint") or "").strip()
    symbol = str(dossier.get("symbol") or "").strip().upper()
    if not mint or not symbol:
        return None
    if str(dossier.get("conviction_band") or "").upper() != "BUYABLE":
        return None
    if str(dossier.get("good_coin_status") or "").upper() != "PASS":
        return None
    if str(dossier.get("catalyst_strength_label") or "").upper() != "STRONG":
        return None
    if str(dossier.get("too_late_label") or "").upper() != "NORMAL":
        return None
    if _f(dossier.get("operator_priority")) < BUYABLE_AUTO_PAPER_MIN_PRIORITY:
        return None

    confidence = int(min(98, max(86, _f(dossier.get("operator_priority")))))
    return {
        "symbol": symbol,
        "mint": mint,
        "runner_state": dossier.get("runner_state") or "BUYABLE_CONVICTION",
        "decision": "MANUAL_BUY",
        "decision_source": "SYSTEM_PAPER",
        "operator_note": "System-created paper entry from BUYABLE conviction; no live capital deployed.",
        "blocker_key": dossier.get("blocker_key"),
        "score": dossier.get("research_score"),
        "proof_score": dossier.get("research_score"),
        "readiness_score": dossier.get("operator_priority"),
        "market_quality_score": dossier.get("good_coin_score"),
        "buy_pressure": dossier.get("buy_pressure"),
        "vol_acceleration": dossier.get("vol_acceleration"),
        "entry_window": dossier.get("entry_window"),
        "fuel_quality": dossier.get("fuel_quality"),
        "move_phase": dossier.get("move_phase"),
        "first_leg_confirmed": 1,
        "suggested_decision": "MANUAL_BUY",
        "suggestion_confidence": confidence,
        "suggestion_reason": "Auto-paper entry: BUYABLE conviction passed good-coin, catalyst, freshness, and too-late gates.",
        "conviction_band": dossier.get("conviction_band"),
        "good_coin_score": dossier.get("good_coin_score"),
        "good_coin_status": dossier.get("good_coin_status"),
        "catalyst_strength_score": dossier.get("catalyst_strength_score"),
        "catalyst_strength_label": dossier.get("catalyst_strength_label"),
        "too_late_score": dossier.get("too_late_score"),
        "too_late_label": dossier.get("too_late_label"),
        "operator_priority": dossier.get("operator_priority"),
        "research_score": dossier.get("research_score"),
        "conviction_attribution_source": "BUYABLE_DOSSIER",
        "snapshot": {
            "dossier": dossier,
            "auto_paper": {
                "reason": "buyable_conviction_auto_paper",
                "conviction_band": dossier.get("conviction_band"),
                "operator_priority": dossier.get("operator_priority"),
                "catalyst_strength_label": dossier.get("catalyst_strength_label"),
                "too_late_label": dossier.get("too_late_label"),
            },
        },
    }


def _buyable_dossier_payloads(limit: int | None = None) -> list[dict[str, Any]]:
    if not BUYABLE_AUTO_PAPER_ENABLED:
        return []
    try:
        from utils.memecoin_research import refresh_memecoin_research_dossiers  # type: ignore

        payload = refresh_memecoin_research_dossiers(limit=max(int(limit or BUYABLE_AUTO_PAPER_MAX_PER_STEP) * 4, 20), record=True)
    except Exception:
        return []
    dossiers = [dict(item or {}) for item in list(payload.get("dossiers") or [])]
    out = [p for p in (_buyable_dossier_payload(dossier) for dossier in dossiers) if p]
    out.sort(
        key=lambda item: (
            -_f(item.get("operator_priority")),
            -_f(item.get("catalyst_strength_score")),
            str(item.get("symbol") or ""),
        )
    )
    return out


def auto_seed_runner_paper_entries(limit: int | None = None) -> dict[str, Any]:
    """Create deduped paper entries for runner-ready and BUYABLE conviction ideas."""
    max_to_create = max(1, min(int(limit or AUTO_PAPER_MAX_PER_STEP), 20))
    now = _iso()
    try:
        from utils.memecoin_manager import get_proof_candidate_snapshot  # type: ignore

        snapshot = get_proof_candidate_snapshot(limit=max(max_to_create * 6, 40), include_recent_complete=True)
    except Exception as exc:
        return {
            "generated_at": now,
            "status": "ERROR",
            "created": 0,
            "skipped": 0,
            "detail": f"snapshot error: {exc}",
        }

    candidates = list(snapshot.get("candidates") or [])
    runner_payloads = [p for p in (_runner_candidate_payload(dict(c or {})) for c in candidates) if p]
    buyable_payloads = _buyable_dossier_payloads(limit=BUYABLE_AUTO_PAPER_MAX_PER_STEP)
    deduped: dict[str, dict[str, Any]] = {}
    for payload in runner_payloads + buyable_payloads:
        mint = str(payload.get("mint") or "")
        existing = deduped.get(mint)
        if existing is None or _f(payload.get("operator_priority")) > _f(existing.get("operator_priority")):
            deduped[mint] = payload
    payloads = list(deduped.values())
    payloads.sort(
        key=lambda item: (
            -_f(item.get("operator_priority")),
            -_f(item.get("suggestion_confidence")),
            -_f(item.get("readiness_score")),
            -_f(item.get("market_quality_score")),
            str(item.get("symbol") or ""),
        )
    )

    created: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    with get_conn() as conn:
        _ensure_schema(conn)
        for payload in payloads:
            mint = str(payload.get("mint") or "")
            if len(created) >= max_to_create:
                skipped.append({"symbol": payload.get("symbol"), "mint": mint, "reason": "step_limit"})
                continue
            if _recent_runner_decision_exists(conn, mint, cooldown_hours=AUTO_PAPER_COOLDOWN_HOURS):
                skipped.append({"symbol": payload.get("symbol"), "mint": mint, "reason": "recent_decision"})
                continue

            entry = _entry_market_context(payload)
            if str(entry.get("entry_capture_status") or "").upper() != "FRESH":
                skipped.append({"symbol": payload.get("symbol"), "mint": mint, "reason": "entry_market_not_fresh"})
                continue

            try:
                row = record_runner_review_decision(payload)
                created.append(row)
            except Exception as exc:
                skipped.append({"symbol": payload.get("symbol"), "mint": mint, "reason": f"record_error:{exc}"})

    status = {
        "generated_at": now,
        "status": "OK",
        "created": len(created),
        "skipped": len(skipped),
        "eligible": len(payloads),
        "eligible_runner_ready": len(runner_payloads),
        "eligible_buyable": len(buyable_payloads),
        "created_items": [
            {
                "symbol": item.get("symbol"),
                "mint": item.get("mint"),
                "entry_marketcap": item.get("entry_marketcap"),
                "entry_market_source": item.get("entry_market_source"),
                "conviction_band": item.get("conviction_band"),
                "operator_priority": item.get("operator_priority"),
            }
            for item in created[:10]
        ],
        "skipped_reasons": {},
    }
    for item in skipped:
        reason = str(item.get("reason") or "unknown")
        status["skipped_reasons"][reason] = int(status["skipped_reasons"].get(reason, 0)) + 1

    try:
        with get_conn() as conn:
            _ensure_schema(conn)
            conn.execute(
                "INSERT OR REPLACE INTO kv_store (key, value) VALUES (?, ?)",
                ("runner_review_auto_paper_status", json.dumps(status, separators=(",", ":"))),
            )
            conn.commit()
    except Exception:
        pass
    return status


def backfill_runner_review_conviction_attribution(limit: int = 200) -> dict[str, Any]:
    """Attach best-known conviction context to older runner decisions for calibration."""
    now = _iso()
    checked = 0
    updated = 0
    skipped = 0
    with get_conn() as conn:
        _ensure_schema(conn)
        rows = conn.execute(
            """
            SELECT id, mint, created_ts
            FROM runner_review_decisions
            WHERE COALESCE(conviction_band, '') = ''
            ORDER BY id DESC
            LIMIT ?
            """,
            (max(1, min(int(limit), 1000)),),
        ).fetchall()
        for row in rows:
            checked += 1
            mint = str(row["mint"] or "").strip()
            created_ts = str(row["created_ts"] or "").strip()
            if not mint:
                skipped += 1
                continue
            has_snapshot_table = conn.execute(
                """
                SELECT 1
                FROM sqlite_master
                WHERE type='table'
                  AND name='memecoin_conviction_snapshots'
                LIMIT 1
                """
            ).fetchone()
            snap = (
                conn.execute(
                    """
                    SELECT conviction_band, good_coin_score, good_coin_status,
                           catalyst_strength_score, catalyst_strength_label,
                           too_late_score, too_late_label, operator_priority,
                           research_score, generated_at
                    FROM memecoin_conviction_snapshots
                    WHERE mint=?
                      AND generated_at <= ?
                    ORDER BY generated_at DESC
                    LIMIT 1
                    """,
                    (mint, created_ts or now),
                ).fetchone()
                if has_snapshot_table
                else None
            )
            source = "CONVICTION_SNAPSHOT_BACKFILL"
            data = dict(snap) if snap else {}
            if not data:
                data = _latest_research_attribution(conn, mint)
                source = "RESEARCH_DOSSIER_BACKFILL" if data else "NONE"
            if not data or not data.get("conviction_band"):
                skipped += 1
                continue
            conn.execute(
                """
                UPDATE runner_review_decisions
                SET conviction_band=?,
                    good_coin_score=?,
                    good_coin_status=?,
                    catalyst_strength_score=?,
                    catalyst_strength_label=?,
                    too_late_score=?,
                    too_late_label=?,
                    operator_priority=?,
                    research_score=?,
                    conviction_attribution_source=?
                WHERE id=?
                """,
                (
                    data.get("conviction_band"),
                    data.get("good_coin_score"),
                    data.get("good_coin_status"),
                    data.get("catalyst_strength_score"),
                    data.get("catalyst_strength_label"),
                    data.get("too_late_score"),
                    data.get("too_late_label"),
                    data.get("operator_priority"),
                    data.get("research_score"),
                    source,
                    row["id"],
                ),
            )
            updated += 1
        conn.commit()
    return {"generated_at": now, "checked": checked, "updated": updated, "skipped": skipped}


def _return_pct(row: dict[str, Any], current: dict[str, Any]) -> float | None:
    entry_mcap = _f(row.get("entry_marketcap"))
    current_mcap = _f(current.get("marketcap"))
    if entry_mcap > 0 and current_mcap > 0:
        return ((current_mcap - entry_mcap) / entry_mcap) * 100.0
    entry_price = _f(row.get("entry_price"))
    current_price = _f(current.get("price"))
    if entry_price > 0 and current_price > 0:
        return ((current_price - entry_price) / entry_price) * 100.0
    return None


def _label_decision(decision: str, max_return: float | None, min_return: float | None, r24: float | None, r72: float | None) -> str | None:
    decision = str(decision or "").upper()
    best = max(_f(max_return, -999.0), _f(r24, -999.0), _f(r72, -999.0))
    worst = min(_f(min_return, 999.0), _f(r24, 999.0), _f(r72, 999.0))
    if decision == "MANUAL_BUY":
        if best >= 25.0:
            return "GOOD_MANUAL_BUY"
        if worst <= -15.0 and best < 10.0:
            return "BAD_MANUAL_BUY"
        return "MANUAL_BUY_NEUTRAL"
    if decision == "PASS":
        if best >= 25.0:
            return "BAD_PASS"
        if best <= 8.0 or worst <= -12.0:
            return "GOOD_PASS"
        return "PASS_NEUTRAL"
    if decision == "TOO_LATE":
        if worst <= -12.0:
            return "GOOD_TOO_LATE"
        if best >= 25.0:
            return "BAD_TOO_LATE"
        return "TOO_LATE_NEUTRAL"
    if decision == "WATCH":
        if best >= 25.0:
            return "WATCH_CONFIRMED"
        if worst <= -15.0 and best < 10.0:
            return "GOOD_WATCH_NO_BUY"
        return "WATCH_NEUTRAL"
    return None


def _best_horizon_label(values: dict[str, float | None], max_return: float | None) -> str | None:
    best_label = None
    best_value = _f(max_return, -999999.0)
    for label, value in values.items():
        if value is not None and _f(value, -999999.0) >= best_value:
            best_label = label
            best_value = _f(value)
    if best_label:
        return best_label
    if max_return is not None:
        return "max_observed"
    return None


def _entry_quality_context(row: dict[str, Any], *, age_h: float) -> dict[str, Any]:
    good = _f(row.get("good_coin_score"))
    catalyst = _f(row.get("catalyst_strength_score"))
    too_late = _f(row.get("too_late_score"))
    priority = _f(row.get("operator_priority"))
    conviction = str(row.get("conviction_band") or "").upper()
    score = _clamp((good * 0.24) + (catalyst * 0.28) + ((100.0 - too_late) * 0.26) + (priority * 0.22))
    reasons: list[str] = []
    if conviction == "BUYABLE":
        reasons.append("BUYABLE conviction")
    elif conviction:
        reasons.append(f"{conviction} conviction")
    if catalyst >= 85:
        reasons.append("strong catalyst")
    elif catalyst >= 65:
        reasons.append("medium catalyst")
    if too_late >= 55:
        reasons.append("late-risk elevated")
    elif too_late <= 15:
        reasons.append("low late-risk")
    if age_h <= 1:
        reasons.append("freshly logged")
    elif age_h >= 24:
        reasons.append("aged entry")

    if too_late >= 70:
        label = "CHASE_RISK"
    elif too_late >= 45 or score < 55:
        label = "LATE"
    elif score >= 82 and too_late <= 20:
        label = "EARLY_QUALITY"
    elif score >= 68:
        label = "FAIR_QUALITY"
    else:
        label = "REVIEW_QUALITY"
    return {
        "entry_quality_score": round(score, 1),
        "entry_quality_label": label,
        "entry_quality_reason": "; ".join(reasons[:5]) or "Entry quality uses conviction, catalyst, late-risk, and priority.",
    }


def _signal_decay_context(row: dict[str, Any], *, age_h: float) -> dict[str, Any]:
    catalyst = _f(row.get("catalyst_strength_score"))
    conviction = str(row.get("conviction_band") or "").upper()
    if age_h <= 4:
        label = "FRESH"
        score = 100.0
    elif age_h <= 12:
        label = "ACTIVE"
        score = 82.0
    elif age_h <= 24:
        label = "AGING"
        score = 62.0
    elif age_h <= 48:
        label = "STALE"
        score = 38.0
    else:
        label = "EXPIRED"
        score = 15.0
    if conviction == "BUYABLE" and catalyst >= 85 and label == "AGING":
        score += 8.0
    return {"signal_decay_label": label, "signal_decay_score": round(_clamp(score), 1)}


def _paper_position_context(row: dict[str, Any]) -> dict[str, Any]:
    conviction = str(row.get("conviction_band") or "").upper()
    priority = _f(row.get("operator_priority"))
    entry_quality = _f(row.get("entry_quality_score"))
    too_late = _f(row.get("too_late_score"))
    if conviction == "BUYABLE" and priority >= 95 and too_late <= 15:
        units = 1.0
        label = "FULL_UNIT"
    elif conviction == "BUYABLE" and priority >= 88:
        units = 0.65
        label = "CORE_UNIT"
    elif conviction == "BUYABLE" and priority >= 80:
        units = 0.35
        label = "STARTER_UNIT"
    elif conviction == "TRIGGERED" and entry_quality >= 65:
        units = 0.25
        label = "PROBE_UNIT"
    elif conviction in {"WATCH", "TRIGGERED"}:
        units = 0.10
        label = "TRACK_ONLY"
    else:
        units = 0.0
        label = "NO_SIZE"
    return {"paper_position_units": round(units, 2), "paper_position_label": label}


def _management_action_context(
    row: dict[str, Any],
    *,
    exit_ctx: dict[str, Any],
    decay_ctx: dict[str, Any],
    current_return: float,
    max_return: float,
) -> dict[str, Any]:
    signal = str(exit_ctx.get("exit_signal") or "").upper()
    urgency = str(exit_ctx.get("exit_urgency") or "").upper()
    decay = str(decay_ctx.get("signal_decay_label") or "").upper()
    if signal in {"INVALIDATE"}:
        action = "EXIT_NOW"
        reason = "Setup invalidated; paper hold should be closed."
    elif signal in {"EXIT_REVIEW", "GIVEBACK_WARNING"} and urgency == "HIGH":
        action = "EXIT_NOW"
        reason = str(exit_ctx.get("exit_signal_reason") or "High-urgency exit review.")
    elif signal in {"MOMENTUM_FADE", "GIVEBACK_WARNING", "EXIT_REVIEW"}:
        action = "SCALE_OUT"
        reason = str(exit_ctx.get("exit_signal_reason") or "Risk rose after entry; scale down paper exposure.")
    elif signal == "PROTECT_PROFIT":
        action = "PROTECT"
        reason = str(exit_ctx.get("exit_signal_reason") or "Protect profitable paper runner.")
    elif decay in {"STALE", "EXPIRED"} and max_return < 8.0:
        action = "EXPIRE_SIGNAL"
        reason = f"Signal is {decay.lower()} without useful upside."
    elif current_return >= 10.0:
        action = "HOLD_WINNER"
        reason = "Paper entry is working; hold while exit triggers remain quiet."
    else:
        action = "HOLD"
        reason = "No exit, protection, or expiration trigger yet."
    return {"management_action": action, "management_reason": reason}


def _runner_exit_signal(
    row: dict[str, Any],
    *,
    current_runner: dict[str, Any] | None,
    current_return: float,
    max_return: float,
    min_return: float,
) -> dict[str, Any]:
    decision = str(row.get("decision") or "").upper()
    if decision != "MANUAL_BUY":
        return {
            "exit_signal": "WATCH_ONLY",
            "exit_urgency": "LOW",
            "exit_signal_reason": "Not a manual-buy paper entry; track as opportunity cost.",
            "drawdown_from_max_pct": round(current_return - max_return, 2),
        }

    runner = dict(current_runner or {})
    runner_state = str(runner.get("runner_state") or row.get("runner_state") or "").upper()
    blocker = str(runner.get("blocker_key") or row.get("blocker_key") or "").strip()
    buy_pressure = _f(runner.get("buy_pressure"), _f(row.get("buy_pressure")))
    vol_accel = _f(runner.get("vol_acceleration"), _f(row.get("vol_acceleration")))
    drawdown = current_return - max_return
    giveback = abs(drawdown) if drawdown < 0 else 0.0

    if max_return >= STRONG_PROFIT_RETURN_PCT and giveback >= EXIT_GIVEBACK_PCT:
        signal = "EXIT_REVIEW"
        urgency = "HIGH"
        reason = f"Strong runner gave back {giveback:.1f}% from max after reaching +{max_return:.1f}%."
    elif max_return >= PROTECT_PROFIT_RETURN_PCT and giveback >= GIVEBACK_WARNING_PCT:
        signal = "GIVEBACK_WARNING"
        urgency = "HIGH" if giveback >= EXIT_GIVEBACK_PCT else "MEDIUM"
        reason = f"Profit protection warning: gave back {giveback:.1f}% from max."
    elif max_return >= PROTECT_PROFIT_RETURN_PCT and (buy_pressure < MOMENTUM_FADE_BUY_PRESSURE or vol_accel < MOMENTUM_FADE_VOL_ACCEL):
        signal = "MOMENTUM_FADE"
        urgency = "MEDIUM"
        reason = f"Momentum faded while profitable: buy pressure {buy_pressure:.1f}, vol accel {vol_accel:.2f}."
    elif max_return >= STRONG_PROFIT_RETURN_PCT:
        signal = "PROTECT_PROFIT"
        urgency = "MEDIUM"
        reason = f"Strong unrealized runner: max return +{max_return:.1f}%; protect gains."
    elif max_return >= PROTECT_PROFIT_RETURN_PCT:
        signal = "PROTECT_PROFIT"
        urgency = "LOW"
        reason = f"Runner reached +{max_return:.1f}%; monitor for giveback or momentum fade."
    elif min_return <= -12.0 and max_return < 8.0:
        signal = "INVALIDATE"
        urgency = "HIGH"
        reason = f"Entry moved against the setup: min return {min_return:.1f}% without useful upside."
    elif runner_state in {"RUNNER_EXTENSION_RISK", "RUNNER_QUALITY_LOW"} and max_return >= 8.0:
        signal = "EXIT_REVIEW"
        urgency = "MEDIUM"
        reason = f"Runner state weakened to {runner_state.replace('_', ' ')} after entry."
    elif blocker in {"runner_extension_risk", "market_quality_unstable", "market_quality_avoid"}:
        signal = "EXIT_REVIEW"
        urgency = "MEDIUM"
        reason = f"Current blocker is {blocker}; review whether the paper hold should be closed."
    else:
        signal = "HOLD_OBSERVE"
        urgency = "LOW"
        reason = "No profit-protection or invalidation trigger yet."

    return {
        "exit_signal": signal,
        "exit_urgency": urgency,
        "exit_signal_reason": reason,
        "drawdown_from_max_pct": round(drawdown, 2),
    }


def evaluate_runner_review_outcomes(limit: int = 100) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    with get_conn() as conn:
        _ensure_schema(conn)
        rows = conn.execute(
            """
            SELECT *
            FROM runner_review_decisions
            WHERE created_ts >= datetime('now', '-10 days')
              AND COALESCE(outcome_status, 'PENDING') != 'COMPLETE'
            ORDER BY id DESC
            LIMIT ?
            """,
            (max(1, int(limit)),),
        ).fetchall()
        row_dicts = [dict(row) for row in rows]

    mints = sorted({str(row.get("mint") or "") for row in row_dicts if str(row.get("mint") or "")})
    latest_by_mint = get_latest_memecoin_token_stats_for_mints(mints, max_age_minutes=MARKET_MAX_AGE_MINUTES) if mints else {}
    latest_by_mint = {
        mint: _normalize_market(stats, source="token_stats")
        for mint, stats in latest_by_mint.items()
    }
    if DEX_FALLBACK_ENABLED and mints:
        missing = [mint for mint in mints if mint not in latest_by_mint][: max(0, DEX_FALLBACK_EVAL_LIMIT)]
        for mint in missing:
            dex_stats = _dex_market(mint)
            if dex_stats:
                latest_by_mint[mint] = dex_stats
    current_runner_by_mint = _current_runner_map(limit=max(80, len(mints) * 3))
    reviewed = 0
    updated = 0
    skipped_no_market = 0
    completed = 0
    now_iso = now.isoformat()

    with get_conn() as conn:
        _ensure_schema(conn)
        for row in row_dicts:
            reviewed += 1
            row_id = int(row.get("id") or 0)
            created = _parse_ts(row.get("created_ts"))
            current = dict(latest_by_mint.get(str(row.get("mint") or "")) or {})
            ret = _return_pct(row, current)
            if not row_id or created is None or ret is None:
                skipped_no_market += 1
                continue

            age_h = max(0.0, (now - created).total_seconds() / 3600.0)
            max_return = max(_f(row.get("max_return_pct"), ret), ret)
            min_return = min(_f(row.get("min_return_pct"), ret), ret)
            current_runner = current_runner_by_mint.get(str(row.get("mint") or "")) or {}
            entry_quality_ctx = _entry_quality_context(row, age_h=age_h)
            sizing_row = {**row, **entry_quality_ctx}
            position_ctx = _paper_position_context(sizing_row)
            decay_ctx = _signal_decay_context(row, age_h=age_h)
            exit_ctx = _runner_exit_signal(
                row,
                current_runner=current_runner,
                current_return=ret,
                max_return=max_return,
                min_return=min_return,
            )
            management_ctx = _management_action_context(
                row,
                exit_ctx=exit_ctx,
                decay_ctx=decay_ctx,
                current_return=ret,
                max_return=max_return,
            )
            assignments: list[str] = [
                "current_price=?",
                "current_marketcap=?",
                "current_stats_ts=?",
                "current_market_source=?",
                "current_data_age_seconds=?",
                "max_return_pct=?",
                "min_return_pct=?",
                "current_return_pct=?",
                "drawdown_from_max_pct=?",
                "exit_signal=?",
                "exit_urgency=?",
                "exit_signal_reason=?",
                "exit_signal_ts=?",
                "entry_quality_score=?",
                "entry_quality_label=?",
                "entry_quality_reason=?",
                "signal_decay_score=?",
                "signal_decay_label=?",
                "paper_position_units=?",
                "paper_position_label=?",
                "simulated_pnl_units=?",
                "management_action=?",
                "management_reason=?",
                "evaluated_ts=?",
            ]
            params: list[Any] = [
                _f(current.get("price")) or None,
                _f(current.get("marketcap")) or None,
                str(current.get("ts_utc") or "") or None,
                str(current.get("market_source") or "") or None,
                current.get("data_age_seconds"),
                round(max_return, 2),
                round(min_return, 2),
                round(ret, 2),
                exit_ctx.get("drawdown_from_max_pct"),
                exit_ctx.get("exit_signal"),
                exit_ctx.get("exit_urgency"),
                exit_ctx.get("exit_signal_reason"),
                now_iso,
                entry_quality_ctx.get("entry_quality_score"),
                entry_quality_ctx.get("entry_quality_label"),
                entry_quality_ctx.get("entry_quality_reason"),
                decay_ctx.get("signal_decay_score"),
                decay_ctx.get("signal_decay_label"),
                position_ctx.get("paper_position_units"),
                position_ctx.get("paper_position_label"),
                round(_f(position_ctx.get("paper_position_units")) * (ret / 100.0), 4),
                management_ctx.get("management_action"),
                management_ctx.get("management_reason"),
                now_iso,
            ]
            complete = age_h >= 72.0
            horizon_values: dict[str, float | None] = {}
            for label, hours in HORIZONS:
                col = f"return_{label}_pct"
                existing = row.get(col)
                if existing is None and age_h >= hours:
                    assignments.append(f"{col}=?")
                    params.append(round(ret, 2))
                    horizon_values[label] = ret
                else:
                    horizon_values[label] = _f(existing) if existing is not None else None
                    if existing is None:
                        complete = False

            r24 = horizon_values.get("24h")
            r72 = horizon_values.get("72h")
            outcome_label = _label_decision(
                str(row.get("decision") or ""),
                max_return,
                min_return,
                r24,
                r72,
            )
            assignments.append("outcome_label=?")
            params.append(outcome_label)
            assignments.append("best_horizon=?")
            params.append(_best_horizon_label(horizon_values, max_return))
            assignments.append("outcome_status=?")
            params.append("COMPLETE" if complete else "ACTIVE")
            follow_up_status = str(row.get("follow_up_status") or "").upper()
            decision = str(row.get("decision") or "").upper()
            if complete:
                assignments.extend(["follow_up_status=?", "resolved_ts=?"])
                params.extend(["RESOLVED", now_iso])
            elif decision == "MANUAL_BUY":
                exit_signal = str(exit_ctx.get("exit_signal") or "")
                exit_urgent = exit_signal in {"EXIT_REVIEW", "GIVEBACK_WARNING", "MOMENTUM_FADE", "INVALIDATE"}
                management_action = str(management_ctx.get("management_action") or "")
                assignments.extend(["follow_up_status=?", "follow_up_due_ts=?", "follow_up_reason=?"])
                params.extend([
                    "FOLLOW_UP_DUE" if exit_urgent or management_action in {"EXIT_NOW", "SCALE_OUT", "EXPIRE_SIGNAL"} or max_return >= 25.0 or min_return <= -12.0 else "MANAGED_OBSERVATION",
                    (now + timedelta(minutes=15)).isoformat(),
                    (
                        str(management_ctx.get("management_reason") or exit_ctx.get("exit_signal_reason") or "Manual-buy follow-up due.")
                        if exit_urgent or management_action in {"EXIT_NOW", "SCALE_OUT", "EXPIRE_SIGNAL"} or max_return >= 25.0 or min_return <= -12.0
                        else "Manual-buy observation active; keep checking exit warnings."
                    ),
                ])
            elif decision == "WATCH" and max_return >= 25.0:
                assignments.extend(["follow_up_status=?", "follow_up_due_ts=?", "follow_up_reason=?"])
                params.extend([
                    "FOLLOW_UP_DUE",
                    now_iso,
                    "Watch decision moved at least +25%; review whether this was a missed or confirmed entry.",
                ])
            elif follow_up_status in {"", "PENDING"}:
                assignments.append("follow_up_status=?")
                params.append("ACTIVE_WATCH" if decision == "WATCH" else "SUPPRESSED")
            params.append(row_id)
            conn.execute(
                f"UPDATE runner_review_decisions SET {', '.join(assignments)} WHERE id=?",
                params,
            )
            updated += 1
            completed += int(complete)
        conn.commit()

    return {
        "evaluated_at": now_iso,
        "reviewed": reviewed,
        "updated": updated,
        "completed": completed,
        "skipped_no_market": skipped_no_market,
    }


def row_to_dict(row) -> dict[str, Any]:
    if row is None:
        return {}
    keys = set(row.keys())

    def get(key: str, default=None):
        return row[key] if key in keys else default

    return {
        "id": get("id"),
        "created_ts": get("created_ts"),
        "symbol": get("symbol"),
        "mint": get("mint"),
        "runner_state": get("runner_state"),
        "decision": get("decision"),
        "operator_note": get("operator_note"),
        "blocker_key": get("blocker_key"),
        "score": get("score"),
        "proof_score": get("proof_score"),
        "readiness_score": get("readiness_score"),
        "market_quality_score": get("market_quality_score"),
        "buy_pressure": get("buy_pressure"),
        "vol_acceleration": get("vol_acceleration"),
        "entry_window": get("entry_window"),
        "fuel_quality": get("fuel_quality"),
        "move_phase": get("move_phase"),
        "first_leg_confirmed": get("first_leg_confirmed"),
        "entry_price": get("entry_price"),
        "entry_marketcap": get("entry_marketcap"),
        "entry_stats_ts": get("entry_stats_ts"),
        "entry_capture_status": get("entry_capture_status"),
        "entry_market_source": get("entry_market_source"),
        "entry_data_age_seconds": get("entry_data_age_seconds"),
        "current_price": get("current_price"),
        "current_marketcap": get("current_marketcap"),
        "current_stats_ts": get("current_stats_ts"),
        "current_market_source": get("current_market_source"),
        "current_data_age_seconds": get("current_data_age_seconds"),
        "return_1h_pct": get("return_1h_pct"),
        "return_4h_pct": get("return_4h_pct"),
        "return_24h_pct": get("return_24h_pct"),
        "return_72h_pct": get("return_72h_pct"),
        "current_return_pct": get("current_return_pct"),
        "max_return_pct": get("max_return_pct"),
        "min_return_pct": get("min_return_pct"),
        "drawdown_from_max_pct": get("drawdown_from_max_pct"),
        "best_horizon": get("best_horizon"),
        "exit_signal": get("exit_signal"),
        "exit_urgency": get("exit_urgency"),
        "exit_signal_reason": get("exit_signal_reason"),
        "exit_signal_ts": get("exit_signal_ts"),
        "outcome_status": get("outcome_status"),
        "outcome_label": get("outcome_label"),
        "evaluated_ts": get("evaluated_ts"),
        "follow_up_status": get("follow_up_status"),
        "follow_up_due_ts": get("follow_up_due_ts"),
        "follow_up_reason": get("follow_up_reason"),
        "suppressed_until_ts": get("suppressed_until_ts"),
        "resolved_ts": get("resolved_ts"),
        "suggested_decision": get("suggested_decision"),
        "suggestion_confidence": get("suggestion_confidence"),
        "suggestion_reason": get("suggestion_reason"),
        "decision_alignment": get("decision_alignment"),
        "decision_source": get("decision_source"),
        "conviction_band": get("conviction_band"),
        "good_coin_score": get("good_coin_score"),
        "good_coin_status": get("good_coin_status"),
        "catalyst_strength_score": get("catalyst_strength_score"),
        "catalyst_strength_label": get("catalyst_strength_label"),
        "too_late_score": get("too_late_score"),
        "too_late_label": get("too_late_label"),
        "operator_priority": get("operator_priority"),
        "research_score": get("research_score"),
        "conviction_attribution_source": get("conviction_attribution_source"),
        "entry_quality_score": get("entry_quality_score"),
        "entry_quality_label": get("entry_quality_label"),
        "entry_quality_reason": get("entry_quality_reason"),
        "signal_decay_score": get("signal_decay_score"),
        "signal_decay_label": get("signal_decay_label"),
        "paper_position_units": get("paper_position_units"),
        "paper_position_label": get("paper_position_label"),
        "simulated_pnl_units": get("simulated_pnl_units"),
        "management_action": get("management_action"),
        "management_reason": get("management_reason"),
    }


def _current_runner_map(limit: int = 80) -> dict[str, dict[str, Any]]:
    try:
        from utils.memecoin_manager import get_proof_candidate_snapshot  # type: ignore

        snapshot = get_proof_candidate_snapshot(limit=limit, include_recent_complete=True)
    except Exception:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for candidate in list(snapshot.get("candidates") or []):
        mint = str(candidate.get("mint") or "").strip()
        if not mint:
            continue
        components = dict(candidate.get("proof_components") or {})
        policy = dict(candidate.get("established_runner_policy") or components.get("established_runner_policy") or {})
        profile = candidate.get("established_runner_profile") or components.get("established_runner")
        if not profile:
            continue
        out[mint] = {
            "symbol": candidate.get("symbol"),
            "mint": mint,
            "runner_state": str(policy.get("state") or candidate.get("runner_policy_state") or "").upper(),
            "blocker_key": candidate.get("blocker_key"),
            "remaining_blocker_keys": list(candidate.get("remaining_blocker_keys") or components.get("remaining_blocker_keys") or []),
            "proof_status": candidate.get("proof_status"),
            "readiness_score": candidate.get("readiness_score"),
            "market_quality_score": candidate.get("market_quality_score"),
            "buy_pressure": (components.get("scanner") or {}).get("buy_pressure"),
            "vol_acceleration": (components.get("scanner") or {}).get("vol_acceleration"),
        }
    return out


def build_runner_follow_up_queue(limit: int = 50, *, include_current: bool = False) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    current_by_mint = _current_runner_map() if include_current else {}
    with get_conn() as conn:
        _ensure_schema(conn)
        rows = conn.execute(
            """
            SELECT *
            FROM runner_review_decisions
            WHERE COALESCE(follow_up_status, '') NOT IN ('RESOLVED', 'EXPIRED')
              AND created_ts >= datetime('now', '-10 days')
            ORDER BY id DESC
            LIMIT ?
            """,
            (max(1, int(limit)),),
        ).fetchall()
    items: list[dict[str, Any]] = []
    due_count = 0
    suppressed_count = 0
    managed_count = 0
    watch_count = 0
    for row in rows:
        item = row_to_dict(row)
        decision = str(item.get("decision") or "").upper()
        mint = str(item.get("mint") or "")
        current = current_by_mint.get(mint) or {}
        due_ts = _parse_ts(item.get("follow_up_due_ts"))
        suppressed_until = _parse_ts(item.get("suppressed_until_ts"))
        is_due = bool(due_ts and due_ts <= now)
        material_change = False
        current_state = str(current.get("runner_state") or "").upper()
        original_state = str(item.get("runner_state") or "").upper()
        if current_state and current_state != original_state:
            if current_state == "RUNNER_READY" or original_state in {"RUNNER_EXTENSION_RISK", "RUNNER_NEEDS_MOMENTUM"}:
                material_change = True
        if decision in {"PASS", "TOO_LATE"} and suppressed_until and suppressed_until > now and not material_change:
            suppressed_count += 1
        if decision == "MANUAL_BUY":
            managed_count += 1
        if decision == "WATCH":
            watch_count += 1
        if is_due or material_change or str(item.get("follow_up_status") or "").upper() == "FOLLOW_UP_DUE":
            due_count += 1
        item["current_runner"] = current or None
        item["follow_up_due"] = bool(is_due or material_change or str(item.get("follow_up_status") or "").upper() == "FOLLOW_UP_DUE")
        item["material_change"] = material_change
        item["recommended_follow_up"] = (
            "Review now: runner state changed materially."
            if material_change
            else "Review now: follow-up window is due."
            if is_due
            else "Continue managed observation."
            if decision == "MANUAL_BUY"
            else "Keep watching."
            if decision == "WATCH"
            else "Suppressed unless state materially improves."
        )
        items.append(item)
    items.sort(
        key=lambda item: (
            0 if item.get("follow_up_due") else 1,
            str(item.get("follow_up_due_ts") or "9999"),
            -float(item.get("max_return_pct") or 0.0),
        )
    )
    return {
        "generated_at": _iso(),
        "summary": {
            "total": len(items),
            "due": due_count,
            "active_watch": watch_count,
            "managed": managed_count,
            "suppressed": suppressed_count,
        },
        "followups": items[: max(1, min(int(limit), 50))],
    }


def _quality_label_for_tracker(item: dict[str, Any]) -> str:
    decision = str(item.get("decision") or "").upper()
    exit_signal = str(item.get("exit_signal") or "").upper()
    outcome = str(item.get("outcome_label") or "")
    max_ret = _f(item.get("max_return_pct"))
    min_ret = _f(item.get("min_return_pct"))
    if exit_signal in {"EXIT_REVIEW", "GIVEBACK_WARNING", "MOMENTUM_FADE", "INVALIDATE"}:
        return exit_signal
    if outcome.startswith("GOOD") or outcome == "WATCH_CONFIRMED":
        return "WORKING"
    if outcome.startswith("BAD"):
        return "FAILING"
    if decision == "MANUAL_BUY" and max_ret >= 25:
        return "PROTECT_PROFIT"
    if decision == "MANUAL_BUY" and min_ret <= -12:
        return "DEFEND"
    if decision == "WATCH" and max_ret >= 20:
        return "MISSED_RUNNER_CHECK"
    return "TRACKING"


def build_runner_trade_tracker(limit: int = 50, *, refresh: bool = False) -> dict[str, Any]:
    """Dashboard-facing tracker for logged runner decisions as paper trades."""
    evaluate = (
        evaluate_runner_review_outcomes(limit=max(50, int(limit)))
        if refresh
        else {
            "evaluated_at": _iso(),
            "reviewed": 0,
            "updated": 0,
            "completed": 0,
            "skipped_no_market": 0,
            "mode": "READ_ONLY",
        }
    )
    with get_conn() as conn:
        _ensure_schema(conn)
        rows = conn.execute(
            """
            SELECT *
            FROM runner_review_decisions
            WHERE created_ts >= datetime('now', '-14 days')
            ORDER BY id DESC
            LIMIT ?
            """,
            (max(1, min(int(limit), 100)),),
        ).fetchall()
    items = [row_to_dict(row) for row in rows]
    trackable = [item for item in items if str(item.get("entry_capture_status") or "").upper() == "FRESH"]
    active = [item for item in items if str(item.get("outcome_status") or "").upper() in {"PENDING", "ACTIVE"}]
    complete = [item for item in items if str(item.get("outcome_status") or "").upper() == "COMPLETE"]
    manual = [item for item in items if str(item.get("decision") or "").upper() == "MANUAL_BUY"]
    watch = [item for item in items if str(item.get("decision") or "").upper() == "WATCH"]
    system_paper = [item for item in items if str(item.get("decision_source") or "").upper() == "SYSTEM_PAPER"]
    missing_market = [
        item for item in items
        if str(item.get("entry_capture_status") or "").upper() in {"", "MISSING", "STALE"}
    ]
    protect = [item for item in items if str(item.get("exit_signal") or "").upper() == "PROTECT_PROFIT"]
    scale_out = [item for item in items if str(item.get("management_action") or "").upper() == "SCALE_OUT"]
    exit_now = [item for item in items if str(item.get("management_action") or "").upper() == "EXIT_NOW"]
    expired_signal = [item for item in items if str(item.get("management_action") or "").upper() == "EXPIRE_SIGNAL"]
    exit_review = [
        item for item in items
        if str(item.get("exit_signal") or "").upper() in {"EXIT_REVIEW", "GIVEBACK_WARNING", "MOMENTUM_FADE", "INVALIDATE"}
    ]
    hold_observe = [item for item in items if str(item.get("exit_signal") or "").upper() == "HOLD_OBSERVE"]
    good = sum(
        1 for item in items
        if str(item.get("outcome_label") or "").startswith("GOOD")
        or str(item.get("outcome_label") or "") == "WATCH_CONFIRMED"
    )
    bad = sum(1 for item in items if str(item.get("outcome_label") or "").startswith("BAD"))
    avg_max = round(sum(_f(item.get("max_return_pct")) for item in trackable) / max(1, len(trackable)), 2)
    avg_min = round(sum(_f(item.get("min_return_pct")) for item in trackable) / max(1, len(trackable)), 2)
    simulated_pnl_units = round(sum(_f(item.get("simulated_pnl_units")) for item in trackable), 4)
    total_paper_units = round(sum(_f(item.get("paper_position_units")) for item in trackable), 2)

    for item in items:
        item["tracker_label"] = _quality_label_for_tracker(item)
        item["paper_trade_state"] = (
            "NOT_TRACKABLE"
            if str(item.get("entry_capture_status") or "").upper() in {"", "MISSING", "STALE"}
            else "COMPLETE"
            if str(item.get("outcome_status") or "").upper() == "COMPLETE"
            else "ACTIVE"
        )

    return {
        "generated_at": _iso(),
        "summary": {
            "total": len(items),
            "trackable": len(trackable),
            "active": len(active),
            "complete": len(complete),
            "manual_buy": len(manual),
            "watch": len(watch),
            "system_paper": len(system_paper),
            "missing_market": len(missing_market),
            "protect_profit": len(protect),
            "scale_out": len(scale_out),
            "exit_now": len(exit_now),
            "expired_signal": len(expired_signal),
            "exit_review": len(exit_review),
            "hold_observe": len(hold_observe),
            "good": good,
            "bad": bad,
            "avg_max_return_pct": avg_max,
            "avg_min_return_pct": avg_min,
            "paper_units": total_paper_units,
            "simulated_pnl_units": simulated_pnl_units,
        },
        "headline": (
            "Runner decisions are now being tracked as paper outcomes."
            if items
            else "No runner decisions logged yet. First labels will start the paper tracker."
        ),
        "evaluation": evaluate,
        "active": active[:12],
        "recent": items[: max(1, min(int(limit), 50))],
    }


def build_runner_review_outcome_summary(limit: int = 100) -> dict[str, Any]:
    with get_conn() as conn:
        _ensure_schema(conn)
        rows = conn.execute(
            """
            SELECT *
            FROM runner_review_decisions
            ORDER BY id DESC
            LIMIT ?
            """,
            (max(1, int(limit)),),
        ).fetchall()
    items = [row_to_dict(row) for row in rows]
    by_decision: dict[str, dict[str, Any]] = {}
    by_label: dict[str, int] = {}
    for item in items:
        decision = str(item.get("decision") or "UNKNOWN")
        bucket = by_decision.setdefault(
            decision,
            {"count": 0, "complete": 0, "avg_max_return_pct": 0.0, "good": 0, "bad": 0},
        )
        bucket["count"] += 1
        label = str(item.get("outcome_label") or "")
        if label:
            by_label[label] = by_label.get(label, 0) + 1
        if str(item.get("outcome_status") or "") == "COMPLETE":
            bucket["complete"] += 1
        max_ret = item.get("max_return_pct")
        if max_ret is not None:
            bucket["avg_max_return_pct"] += _f(max_ret)
        if label.startswith("GOOD") or label in {"WATCH_CONFIRMED"}:
            bucket["good"] += 1
        if label.startswith("BAD"):
            bucket["bad"] += 1
    for bucket in by_decision.values():
        bucket["avg_max_return_pct"] = round(bucket["avg_max_return_pct"] / max(1, int(bucket["count"])), 2)
        bucket["quality_score"] = int(bucket["good"]) - int(bucket["bad"])
    follow_up = build_runner_follow_up_queue(limit=50)
    return {
        "generated_at": _iso(),
        "summary": {
            "total": len(items),
            "active": sum(1 for i in items if str(i.get("outcome_status") or "") in {"PENDING", "ACTIVE"}),
            "complete": sum(1 for i in items if str(i.get("outcome_status") or "") == "COMPLETE"),
            "good": sum(1 for i in items if str(i.get("outcome_label") or "").startswith("GOOD") or str(i.get("outcome_label") or "") == "WATCH_CONFIRMED"),
            "bad": sum(1 for i in items if str(i.get("outcome_label") or "").startswith("BAD")),
        },
        "by_decision": by_decision,
        "by_label": by_label,
        "recent": items[:12],
        "follow_up": follow_up,
    }
