from __future__ import annotations

"""
Memecoin Manager — Patch 117
Buy / sell / monitor / status / tune for memecoin spot trades.
Reuses jupiter_swap.py for execution, db.py for persistence.
"""

import asyncio
import copy
import json
import logging
import os
import re
import requests
import time
from collections import Counter
from datetime import datetime, timezone, timedelta

from utils.db import get_conn, record_capital_event
from utils.memecoin_scanner import get_cached_signals, get_last_nonempty_cached_signals
from utils import orchestrator

log = logging.getLogger(__name__)
logger = log

_PROOF_SNAPSHOT_CACHE_TTL_SECONDS = float(os.getenv("PROOF_SNAPSHOT_CACHE_TTL_SECONDS", "15"))
_proof_snapshot_cache: dict[tuple, tuple[float, dict]] = {}
_TOKEN_PRICE_CACHE_SECONDS = float(os.getenv("MEMECOIN_TOKEN_PRICE_CACHE_SECONDS", "90"))
_TOKEN_INTEL_PRICE_MAX_AGE_SECONDS = float(os.getenv("MEMECOIN_TOKEN_INTEL_PRICE_MAX_AGE_SECONDS", "600"))
_token_price_cache: dict[str, tuple[float, float]] = {}
_WATCH_TO_ENTRY_STATE_KV = "memecoin_watch_to_entry_state"
_WATCH_TO_ENTRY_STATUS_KV = "memecoin_watch_to_entry_status"
_WATCH_TO_ENTRY_RECENT_KV = "memecoin_watch_to_entry_recent"


def _env_bool(name: str, default: bool = False) -> bool:
    raw = str(os.getenv(name, "true" if default else "false") or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _watch_to_entry_track_blockers() -> set[str]:
    raw = os.getenv(
        "WATCH_TO_ENTRY_TRACK_BLOCKERS",
        "vol_acceleration_low,buy_pressure_low,entry_window_not_open,"
        "first_leg_unconfirmed,readiness_below_threshold,"
        "runner_momentum_unconfirmed,runner_lifecycle_unconfirmed,"
        "runner_extension_risk,runner_quality_below_policy,"
        "runner_manual_review_required",
    )
    return {part.strip() for part in str(raw or "").split(",") if part.strip()}


def _kv_json_get(key: str, default):
    try:
        with get_conn() as conn:
            row = conn.execute("SELECT value FROM kv_store WHERE key=?", (key,)).fetchone()
        if row and row[0]:
            parsed = json.loads(row[0])
            return parsed if parsed is not None else default
    except Exception:
        pass
    return default


def _kv_json_set(key: str, payload) -> None:
    try:
        with get_conn() as conn:
            conn.execute(
                """
                INSERT INTO kv_store (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
                """,
                (key, json.dumps(payload, separators=(",", ":"))),
            )
    except Exception as exc:
        log.debug("[WATCH_TO_ENTRY] kv write skipped for %s: %s", key, exc)


def _parse_utc_ts(value) -> datetime | None:
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


def _proof_data_freshness_context() -> dict:
    """Decision-time data confidence used to taper stale proof inputs."""
    ctx = {
        "status": "HIGH",
        "penalty_points": 0.0,
        "wallet_multiplier": 1.0,
        "token_stats_multiplier": 1.0,
        "issues": [],
    }
    try:
        from utils.db import get_provider_status  # type: ignore
        dex_state = dict(get_provider_status("dexscreener") or {})
        if str(dex_state.get("status") or "ACTIVE").upper() != "ACTIVE":
            ctx["penalty_points"] += 5.0
            ctx["issues"].append("dexscreener_degraded")
    except Exception:
        pass

    try:
        with get_conn() as conn:
            wallet_row = conn.execute(
                "SELECT value FROM kv_store WHERE key='wallet_tx_stream_status'"
            ).fetchone()
            token_row = conn.execute(
                "SELECT MAX(ts_utc) AS latest_ts FROM memecoin_token_stats_snapshots"
            ).fetchone()
        wallet_payload = json.loads(wallet_row[0]) if wallet_row and wallet_row[0] else {}
        last_frame = _parse_utc_ts(wallet_payload.get("last_frame_ts"))
        last_event = _parse_utc_ts(wallet_payload.get("last_event_ts"))
        now = datetime.now(timezone.utc)
        frame_age_min = ((now - last_frame).total_seconds() / 60.0) if last_frame else None
        event_age_min = ((now - last_event).total_seconds() / 60.0) if last_event else None
        if frame_age_min is None or frame_age_min > 180 or str(wallet_payload.get("last_message_type") or "").upper() == "ERROR":
            ctx["wallet_multiplier"] = 0.35
            ctx["penalty_points"] += 6.0
            ctx["issues"].append("wallet_stream_stale")
        elif event_age_min is None or event_age_min > 1440:
            ctx["wallet_multiplier"] = 0.55
            ctx["penalty_points"] += 3.0
            ctx["issues"].append("wallet_events_stale")

        latest_stats = _parse_utc_ts(token_row["latest_ts"] if token_row else None)
        stats_age_min = ((now - latest_stats).total_seconds() / 60.0) if latest_stats else None
        if stats_age_min is None or stats_age_min > 60:
            ctx["token_stats_multiplier"] = 0.7
            ctx["penalty_points"] += 4.0
            ctx["issues"].append("token_stats_stale")
    except Exception:
        pass

    penalty = float(ctx["penalty_points"] or 0.0)
    ctx["penalty_points"] = round(penalty, 1)
    if penalty >= 10.0:
        ctx["status"] = "LOW"
    elif penalty > 0.0:
        ctx["status"] = "MEDIUM"
    return ctx


def _runner_heartbeat_signals(max_age_minutes: float = 20.0) -> list[dict]:
    payload = _kv_json_get("memecoin_runner_heartbeat_signals", {})
    if not isinstance(payload, dict):
        return []
    generated_at = _parse_utcish_ts(payload.get("generated_at"))
    if generated_at is None:
        return []
    age_minutes = max(0.0, (datetime.now(timezone.utc) - generated_at).total_seconds() / 60.0)
    if age_minutes > max_age_minutes:
        return []
    rows = []
    for item in list(payload.get("signals") or []):
        if not isinstance(item, dict):
            continue
        row = dict(item)
        row.setdefault("source", "RUNNER_HEARTBEAT")
        row.setdefault("scanner_regime", "RUNNER_HEARTBEAT")
        row.setdefault("scanner_relaxation_reason", "established_runner_active_coverage")
        rows.append(row)
    return rows


# ── Pilot mode ────────────────────────────────────────────────────────────────

def get_memecoin_mode() -> str:
    """
    Returns the current memecoin execution mode as a single string.

    PAPER  — MEMECOIN_DRY_RUN=true (default). No real swaps. Safe at all times.
    PILOT  — MEMECOIN_DRY_RUN=false + MEMECOIN_PILOT_MODE=true.
             Live execution with hard guardrails (position cap, size cap, exposure cap).
    LIVE   — MEMECOIN_DRY_RUN=false + MEMECOIN_PILOT_MODE=false.
             Unrestricted live. Requires explicit double opt-in (both flags).

    Safety hierarchy: PAPER > PILOT > LIVE.
    Moving from PAPER→PILOT requires setting both DRY_RUN=false AND PILOT_MODE=true.
    Moving from PILOT→LIVE requires unsetting PILOT_MODE (an additional deliberate step).
    """
    dry_run = os.getenv("MEMECOIN_DRY_RUN",   "true").lower()  == "true"
    pilot   = os.getenv("MEMECOIN_PILOT_MODE", "false").lower() == "true"
    if dry_run:
        return "PAPER"
    if pilot:
        return "PILOT"
    return "LIVE"


def get_pilot_stats() -> dict:
    """
    Returns bookkeeping data for pilot-tagged trades (is_pilot=1) only.
    Safe to call in any mode. Returns zero values if no pilot trades exist.
    """
    try:
        with get_conn() as conn:
            open_rows = conn.execute("""
                SELECT id, amount_usd, symbol, mint, opened_ts_utc
                FROM memecoin_trades
                WHERE is_pilot = 1 AND status = 'OPEN'
                ORDER BY opened_ts_utc DESC
            """).fetchall()
            closed_rows = conn.execute("""
                SELECT pnl_usd, pnl_pct, symbol, closed_ts_utc, exit_reason
                FROM memecoin_trades
                WHERE is_pilot = 1 AND status = 'CLOSED'
                ORDER BY closed_ts_utc DESC
            """).fetchall()
        open_count     = len(open_rows)
        total_exposure = sum(float(r["amount_usd"] or 0) for r in open_rows)
        realized_pnl   = sum(float(r["pnl_usd"]   or 0) for r in closed_rows)
        closed_count   = len(closed_rows)
        return {
            "open_count":         open_count,
            "total_exposure_usd": round(total_exposure, 2),
            "closed_count":       closed_count,
            "realized_pnl_usd":   round(realized_pnl, 4),
            "open_trades": [
                {
                    "symbol":   r["symbol"],
                    "mint":     r["mint"],
                    "size_usd": float(r["amount_usd"] or 0),
                    "opened":   r["opened_ts_utc"],
                }
                for r in open_rows
            ],
            "recent_closed": [
                {
                    "symbol":      r["symbol"],
                    "pnl_usd":     float(r["pnl_usd"] or 0),
                    "pnl_pct":     float(r["pnl_pct"] or 0),
                    "exit_reason": r["exit_reason"] or "MANUAL",
                    "closed_at":   r["closed_ts_utc"],
                }
                for r in closed_rows[:5]
            ],
        }
    except Exception:
        return {
            "open_count": 0, "total_exposure_usd": 0.0,
            "closed_count": 0, "realized_pnl_usd": 0.0,
            "open_trades": [], "recent_closed": [],
        }


def _current_regime_label() -> str:
    try:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT value FROM kv_store WHERE key='last_regime_label'"
            ).fetchone()
        return str((row[0] if row else "UNKNOWN") or "UNKNOWN").strip().upper()
    except Exception:
        return "UNKNOWN"


def _parse_utcish_ts(value: object) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        if raw.endswith("Z"):
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        else:
            dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(raw[:19], fmt).replace(tzinfo=timezone.utc)
        except Exception:
            continue
    return None


def _get_scan_detection_context(mint: str) -> dict:
    mint = str(mint or "").strip()
    if not mint:
        return {}
    try:
        with get_conn() as conn:
            row = conn.execute(
                """
                SELECT scanned_at, price_at_scan, mcap_at_scan, score
                FROM memecoin_signal_outcomes
                WHERE mint = ?
                  AND scanned_at >= datetime('now', '-24 hours')
                ORDER BY scanned_at ASC, id ASC
                LIMIT 1
                """,
                (mint,),
            ).fetchone()
            if row is None:
                row = conn.execute(
                    """
                    SELECT scanned_at, price_at_scan, mcap_at_scan, score
                    FROM memecoin_signal_outcomes
                    WHERE mint = ?
                    ORDER BY scanned_at ASC, id ASC
                    LIMIT 1
                    """,
                    (mint,),
                ).fetchone()
        if not row:
            return {}
        return {
            "scan_detected_at": str(row["scanned_at"] or ""),
            "scan_price": float(row["price_at_scan"]) if row["price_at_scan"] is not None else None,
            "scan_mcap": float(row["mcap_at_scan"]) if row["mcap_at_scan"] is not None else None,
            "scan_score": float(row["score"]) if row["score"] is not None else None,
        }
    except Exception:
        return {}


def _load_proof_ready_first_seen() -> dict:
    try:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT value FROM kv_store WHERE key='memecoin_proof_ready_first_seen'"
            ).fetchone()
        if row and row[0]:
            parsed = json.loads(row[0])
            return dict(parsed) if isinstance(parsed, dict) else {}
    except Exception:
        pass
    return {}


def _store_proof_ready_first_seen(payload: dict) -> None:
    try:
        with get_conn() as conn:
            conn.execute(
                """
                INSERT INTO kv_store (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
                """,
                ("memecoin_proof_ready_first_seen", json.dumps(payload, separators=(",", ":"))),
            )
    except Exception:
        pass


def _candidate_price_markers(candidate: dict) -> tuple[float | None, float | None]:
    proof_components = dict(candidate.get("proof_components") or {})
    scanner = dict(proof_components.get("scanner") or {})
    live_stats = dict(scanner.get("live_token_stats") or {})
    price = live_stats.get("price")
    if price is None:
        price = scanner.get("price_at_scan") or candidate.get("entry_price")
    marketcap = live_stats.get("marketcap")
    if marketcap is None:
        marketcap = scanner.get("mcap_at_scan") or candidate.get("mcap_at_scan")
    try:
        price_f = float(price) if price is not None else None
    except Exception:
        price_f = None
    try:
        marketcap_f = float(marketcap) if marketcap is not None else None
    except Exception:
        marketcap_f = None
    return price_f, marketcap_f


def _touch_proof_ready_first_seen(snapshot: dict) -> dict:
    now = datetime.now(timezone.utc)
    payload = _load_proof_ready_first_seen()
    changed = False
    stale_before = now - timedelta(hours=48)
    next_payload: dict[str, dict] = {}

    for mint, item in payload.items():
        ts = _parse_utcish_ts((item or {}).get("proof_ready_at"))
        if ts and ts >= stale_before:
            next_payload[str(mint)] = dict(item or {})

    for candidate in list(snapshot.get("candidates") or []):
        if str(candidate.get("proof_status") or "").upper() != "PROOF_READY":
            continue
        mint = str(candidate.get("mint") or "").strip()
        if not mint or mint in next_payload:
            continue
        price, marketcap = _candidate_price_markers(candidate)
        next_payload[mint] = {
            "symbol": str(candidate.get("symbol") or ""),
            "proof_ready_at": now.isoformat(),
            "proof_ready_price": price,
            "proof_ready_mcap": marketcap,
            "readiness_score": candidate.get("readiness_score"),
            "proof_score": candidate.get("proof_score"),
        }
        changed = True

    if changed or next_payload.keys() != payload.keys():
        _store_proof_ready_first_seen(next_payload)
    return next_payload


def _entry_timing_bucket(minutes_scan_to_entry: float | None) -> str | None:
    if minutes_scan_to_entry is None:
        return None
    if minutes_scan_to_entry <= 5.0:
        return "IMMEDIATE"
    if minutes_scan_to_entry <= 30.0:
        return "FAST"
    if minutes_scan_to_entry <= 120.0:
        return "LATE"
    return "VERY_LATE"


def _entry_window_position_pct(
    *,
    entry_window: str | None,
    move_phase: str | None,
    minutes_scan_to_entry: float | None,
) -> float | None:
    ew = str(entry_window or "").upper()
    mp = str(move_phase or "").upper()
    if not ew and not mp and minutes_scan_to_entry is None:
        return None

    base = {
        "OPEN": 25.0,
        "CLOSING": 70.0,
        "CLOSED": 95.0,
    }.get(ew, 45.0)
    phase_adj = {
        "IGNITION": -10.0,
        "EARLY": -8.0,
        "RELOAD": -2.0,
        "MID": 10.0,
        "EXTENDED": 20.0,
        "CHURN": 25.0,
    }.get(mp, 0.0)
    if minutes_scan_to_entry is None:
        latency_adj = 0.0
    elif minutes_scan_to_entry <= 5.0:
        latency_adj = -5.0
    elif minutes_scan_to_entry <= 30.0:
        latency_adj = 0.0
    elif minutes_scan_to_entry <= 120.0:
        latency_adj = 8.0
    else:
        latency_adj = 18.0
    return round(max(0.0, min(100.0, base + phase_adj + latency_adj)), 1)


def _signal_window_phase(position_pct: float | None) -> str | None:
    if position_pct is None:
        return None
    if position_pct <= 30.0:
        return "EARLY"
    if position_pct <= 65.0:
        return "MID"
    if position_pct <= 85.0:
        return "LATE"
    return "EXHAUSTED"


def _confirmation_wait_minutes(
    *,
    minutes_scan_to_entry: float | None,
    minutes_proof_to_entry: float | None,
) -> tuple[bool | None, float | None]:
    wait_minutes = minutes_proof_to_entry
    if wait_minutes is None:
        wait_minutes = minutes_scan_to_entry
    if wait_minutes is None:
        return None, None
    wait_minutes = round(max(0.0, float(wait_minutes)), 2)
    if minutes_proof_to_entry is not None:
        return (wait_minutes >= 5.0), wait_minutes
    return (wait_minutes >= 15.0), wait_minutes


def _augment_entry_latency_context(entry_attr: dict, *, mint: str, candidate: dict | None = None) -> dict:
    out = dict(entry_attr or {})
    scan_ctx = _get_scan_detection_context(mint)
    if scan_ctx:
        out.setdefault("scan_detected_at", scan_ctx.get("scan_detected_at"))
        out.setdefault("scan_price", scan_ctx.get("scan_price"))
        out.setdefault("scan_mcap", scan_ctx.get("scan_mcap"))
    if candidate is not None:
        first_seen = _load_proof_ready_first_seen().get(str(mint).strip()) or {}
        if first_seen:
            out.setdefault("proof_ready_at", first_seen.get("proof_ready_at"))
            out.setdefault("proof_ready_price", first_seen.get("proof_ready_price"))
            out.setdefault("proof_ready_mcap", first_seen.get("proof_ready_mcap"))
    return out


def _finalize_entry_latency(entry_attr: dict, *, entry_price: float | None, entry_fired_at: str) -> dict:
    out = dict(entry_attr or {})
    out["entry_fired_at"] = entry_fired_at

    scan_dt = _parse_utcish_ts(out.get("scan_detected_at"))
    proof_dt = _parse_utcish_ts(out.get("proof_ready_at"))
    entry_dt = _parse_utcish_ts(entry_fired_at)

    minutes_scan_to_proof = None
    minutes_proof_to_entry = None
    minutes_scan_to_entry = None
    if scan_dt and proof_dt:
        minutes_scan_to_proof = round(max(0.0, (proof_dt - scan_dt).total_seconds() / 60.0), 2)
    if proof_dt and entry_dt:
        minutes_proof_to_entry = round(max(0.0, (entry_dt - proof_dt).total_seconds() / 60.0), 2)
    if scan_dt and entry_dt:
        minutes_scan_to_entry = round(max(0.0, (entry_dt - scan_dt).total_seconds() / 60.0), 2)

    scan_price = out.get("scan_price")
    proof_price = out.get("proof_ready_price")
    pct_move_scan_to_entry = None
    pct_move_proof_to_entry = None
    try:
        if scan_price is not None and entry_price and float(scan_price) > 0:
            pct_move_scan_to_entry = round((float(entry_price) - float(scan_price)) / float(scan_price) * 100.0, 2)
    except Exception:
        pct_move_scan_to_entry = None
    try:
        if proof_price is not None and entry_price and float(proof_price) > 0:
            pct_move_proof_to_entry = round((float(entry_price) - float(proof_price)) / float(proof_price) * 100.0, 2)
    except Exception:
        pct_move_proof_to_entry = None

    out["minutes_scan_to_proof_ready"] = minutes_scan_to_proof
    out["minutes_proof_ready_to_entry"] = minutes_proof_to_entry
    out["minutes_scan_to_entry"] = minutes_scan_to_entry
    out["pct_move_scan_to_entry"] = pct_move_scan_to_entry
    out["pct_move_proof_ready_to_entry"] = pct_move_proof_to_entry
    out["entry_timing_bucket"] = _entry_timing_bucket(minutes_scan_to_entry)
    window_position_pct = _entry_window_position_pct(
        entry_window=out.get("entry_window"),
        move_phase=out.get("move_phase"),
        minutes_scan_to_entry=minutes_scan_to_entry,
    )
    confirmation_wait_used, confirmation_wait_minutes = _confirmation_wait_minutes(
        minutes_scan_to_entry=minutes_scan_to_entry,
        minutes_proof_to_entry=minutes_proof_to_entry,
    )
    out["entry_window_position_pct"] = window_position_pct
    out["signal_window_phase"] = _signal_window_phase(window_position_pct)
    out["confirmation_wait_used"] = confirmation_wait_used
    out["confirmation_wait_minutes"] = confirmation_wait_minutes
    return out


def _build_trade_entry_attribution(candidate: dict, proof_snapshot: dict | None = None) -> dict:
    proof_components = dict(candidate.get("proof_components") or {})
    scanner = dict(proof_components.get("scanner") or {})
    reinforcement = dict(proof_components.get("reinforcement") or {})
    readiness = dict(proof_components.get("readiness") or {})
    wallet_behavior = dict(reinforcement.get("wallet_behavior") or {})
    lane_authority = str(((proof_snapshot or {}).get("lane_state") or {}).get("deployment_authority") or "UNKNOWN").upper()
    wallet_signalers: list[dict] = []
    try:
        from utils.db import get_recent_wallet_signalers_for_mint  # type: ignore[import]

        wallet_signalers = get_recent_wallet_signalers_for_mint(
            str(candidate.get("mint") or ""),
            max_age_hours=24,
            limit=8,
        ) or []
    except Exception:
        wallet_signalers = []

    attribution = {
        "timing_score": candidate.get("timing_score") or scanner.get("timing_score"),
        "safety_score": candidate.get("safety_score") or scanner.get("safety_score"),
        "market_quality_score": candidate.get("market_quality_score") or scanner.get("market_quality_score"),
        "support_score": candidate.get("support_score") or reinforcement.get("support_score"),
        "readiness_score": candidate.get("readiness_score") or readiness.get("readiness_score"),
        "profit_room_score": candidate.get("profit_room_score") or readiness.get("profit_room_score") or scanner.get("profit_room_score"),
        "profit_room_label": candidate.get("profit_room_label") or readiness.get("profit_room_label") or scanner.get("profit_room_label"),
        "market_quality_verdict": candidate.get("market_quality_verdict") or scanner.get("market_quality_verdict"),
        "wallet_behavior_state": candidate.get("wallet_behavior_state") or wallet_behavior.get("wallet_behavior_state"),
        "smart_money_quality": candidate.get("smart_money_quality") or wallet_behavior.get("smart_money_quality"),
        "regime_label": _current_regime_label(),
        "route": str(scanner.get("scanner_regime") or candidate.get("scanner_regime") or "NORMAL"),
        "lane_authority": lane_authority,
    }
    attribution["proof_status"] = candidate.get("proof_status")
    attribution["proof_reason"] = candidate.get("proof_reason")
    attribution["scanner_regime"] = scanner.get("scanner_regime") or candidate.get("scanner_regime")
    attribution["support_level"] = candidate.get("support_level") or reinforcement.get("support_level")
    attribution["market_quality_reasons"] = list(candidate.get("market_quality_reasons") or scanner.get("market_quality_reasons") or [])
    attribution["profit_room_reasons"] = list(candidate.get("profit_room_reasons") or readiness.get("profit_room_reasons") or scanner.get("profit_room_reasons") or [])
    attribution["readiness_reasons"] = list(readiness.get("readiness_reasons") or [])
    attribution["entry_window"] = candidate.get("entry_window")
    attribution["move_phase"] = candidate.get("move_phase")
    attribution["wallet_signalers"] = wallet_signalers
    attribution["wallet_signaler_count"] = len(wallet_signalers)
    return _augment_entry_latency_context(attribution, mint=str(candidate.get("mint") or ""), candidate=candidate)


# ── Price fetch ──────────────────────────────────────────────────────────────

def _fetch_sol_price() -> float:
    """Fetch current SOL price (Jupiter → Kraken fallback)."""
    try:
        r = requests.get(
            "https://price.jup.ag/v4/price?ids=SOL",
            timeout=6,
        )
        return float(r.json()["data"]["SOL"]["price"])
    except Exception:
        pass
    try:
        r = requests.get(
            "https://api.kraken.com/0/public/Ticker?pair=SOLUSD",
            timeout=6,
        )
        return float(r.json()["result"]["SOLUSD"]["c"][0])
    except Exception:
        return 0.0


def _cached_token_price(mint: str) -> float:
    cached = _token_price_cache.get(str(mint or "").strip())
    if not cached:
        return 0.0
    ts, price = cached
    if time.monotonic() - ts > max(1.0, _TOKEN_PRICE_CACHE_SECONDS):
        return 0.0
    return float(price or 0.0)


def _remember_token_price(mint: str, price: float) -> float:
    mint = str(mint or "").strip()
    price = float(price or 0.0)
    if mint and price > 0:
        _token_price_cache[mint] = (time.monotonic(), price)
        if len(_token_price_cache) > 256:
            oldest = min(_token_price_cache, key=lambda k: _token_price_cache[k][0])
            _token_price_cache.pop(oldest, None)
    return price


def _token_intelligence_price(mint: str) -> float:
    mint = str(mint or "").strip()
    if not mint:
        return 0.0
    try:
        with get_conn() as conn:
            row = conn.execute(
                """
                SELECT price, updated_at, data_freshness, data_confidence
                FROM token_intelligence_current
                WHERE mint=?
                """,
                (mint,),
            ).fetchone()
        if not row:
            return 0.0
        price = float(row["price"] or 0.0)
        if price <= 0:
            return 0.0
        updated = _parse_utc_ts(row["updated_at"])
        if updated is None:
            return 0.0
        age_s = (datetime.now(timezone.utc) - updated).total_seconds()
        freshness = str(row["data_freshness"] or "").upper()
        confidence = str(row["data_confidence"] or "").upper()
        if (
            age_s <= max(30.0, _TOKEN_INTEL_PRICE_MAX_AGE_SECONDS)
            and freshness in {"LIVE", "RECENT"}
            and confidence in {"HIGH", "MEDIUM"}
        ):
            return _remember_token_price(mint, price)
    except Exception:
        return 0.0
    return 0.0


def _fetch_token_price(mint: str, *, allow_dex_fallback: bool = True) -> float:
    """Fetch current price for a token mint.

    Primary:  Jupiter v6 price API (better micro-cap coverage than v4)
    Fallback: DexScreener pair data (always has prices for active Solana pairs)
    """
    mint = str(mint or "").strip()
    if not mint:
        return 0.0
    cached_price = _cached_token_price(mint)
    if cached_price > 0:
        return cached_price
    intel_price = _token_intelligence_price(mint)
    if intel_price > 0:
        return intel_price

    # 1. Jupiter v6
    try:
        r = requests.get(
            f"https://price.jup.ag/v6/price?ids={mint}",
            timeout=6,
        )
        data = r.json().get("data", {})
        td = data.get(mint) or next(iter(data.values()), None)
        if td:
            return _remember_token_price(mint, float(td["price"]))
    except Exception:
        pass
    if not allow_dex_fallback:
        return 0.0
    # 2. DexScreener fallback — reliable for any active Solana pair
    try:
        from data.dexscreener import fetch_token_pairs  # type: ignore

        pairs = fetch_token_pairs(mint, reason="memecoin_manager_price_429")
        sol_pairs = [p for p in pairs if p.get("chainId") == "solana"]
        if sol_pairs:
            best = max(
                sol_pairs,
                key=lambda p: float((p.get("liquidity") or {}).get("usd", 0) or 0),
            )
            return _remember_token_price(mint, float(best.get("priceUsd", 0) or 0))
    except Exception:
        pass
    return 0.0


def _historical_price_from_birdeye(
    mint: str,
    scanned_at: datetime,
    tolerance_minutes: int = 45,
) -> float:
    """
    Try to reconstruct a defensible near-scan entry price from Birdeye OHLCV.

    We intentionally keep this conservative:
    - only use candles close to the original scan time
    - prefer the nearest candle, not an arbitrary later/current price
    - return 0.0 when the history is too sparse or too far away
    """
    try:
        from data.birdeye import fetch_birdeye_ohlcv  # type: ignore
    except Exception:
        return 0.0

    now = datetime.now(timezone.utc)
    if scanned_at.tzinfo is None:
        scanned_at = scanned_at.replace(tzinfo=timezone.utc)
    age_h = max(1.0, (now - scanned_at).total_seconds() / 3600.0)
    lookback_h = max(12, min(24 * 7, int(age_h) + 6))

    try:
        candles = fetch_birdeye_ohlcv(mint, candle_type="15m", lookback_hours=lookback_h)
    except Exception:
        return 0.0
    if not candles:
        return 0.0

    target_ts = int(scanned_at.timestamp())
    tolerance_s = max(60, int(tolerance_minutes) * 60)
    best = None
    best_delta = None
    for candle in candles:
        try:
            ts = int(candle.get("unixTime") or 0)
            close_px = float(candle.get("c") or 0)
        except Exception:
            continue
        if ts <= 0 or close_px <= 0:
            continue
        delta = abs(ts - target_ts)
        if delta > tolerance_s:
            continue
        if best_delta is None or delta < best_delta:
            best = close_px
            best_delta = delta

    return float(best or 0.0)


def backfill_discovery_entry_prices(
    limit: int = 250,
    max_age_days: int = 7,
    tolerance_minutes: int = 45,
    promote_outcomes: bool = True,
) -> dict:
    """
    One-time repair pass for historical DISCOVERY rows that never captured
    `price_at_scan`. Uses Birdeye OHLCV to backfill only when a near-scan candle
    exists, leaving unrecoverable rows untouched.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT id, mint, symbol, status, scanned_at
            FROM memecoin_signal_outcomes
            WHERE source='DISCOVERY'
              AND mint IS NOT NULL AND mint != ''
              AND (price_at_scan IS NULL OR price_at_scan <= 0)
              AND status IN ('WATCH', 'STALE')
              AND scanned_at >= ?
            ORDER BY scanned_at ASC
            LIMIT ?
            """,
            (cutoff, int(limit)),
        ).fetchall()

    attempted = 0
    rescued = 0
    stale_rescued = 0
    watch_rescued = 0
    sample: list[dict] = []
    for row in rows:
        attempted += 1
        try:
            scanned = datetime.fromisoformat(str(row["scanned_at"]).replace(" ", "T"))
            if scanned.tzinfo is None:
                scanned = scanned.replace(tzinfo=timezone.utc)
        except Exception:
            continue

        price = _historical_price_from_birdeye(
            str(row["mint"]),
            scanned,
            tolerance_minutes=tolerance_minutes,
        )
        if price <= 0:
            continue

        new_status = "WATCH" if str(row["status"] or "").upper() == "STALE" else row["status"]
        try:
            with get_conn() as conn:
                conn.execute(
                    """
                    UPDATE memecoin_signal_outcomes
                    SET price_at_scan = ?, status = ?
                    WHERE id = ?
                    """,
                    (round(price, 12), new_status, int(row["id"])),
                )
        except Exception:
            continue

        rescued += 1
        if str(row["status"] or "").upper() == "STALE":
            stale_rescued += 1
        else:
            watch_rescued += 1
        if len(sample) < 10:
            sample.append(
                {
                    "id": int(row["id"]),
                    "symbol": str(row["symbol"] or ""),
                    "status": str(row["status"] or ""),
                    "rescued_price": round(price, 12),
                    "scanned_at": str(row["scanned_at"] or ""),
                }
            )

    if promote_outcomes and rescued > 0:
        memecoin_outcome_step()

    return {
        "attempted": attempted,
        "rescued": rescued,
        "stale_rescued": stale_rescued,
        "watch_rescued": watch_rescued,
        "sample": sample,
    }


def _load_auto_buy_thresholds() -> dict:
    threshold = float(os.getenv("MEMECOIN_BUY_SCORE_MIN", "65"))
    max_score = 999.0
    vacc_min = 5.0
    holder_max = 35.0
    confidence = "low"
    try:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT value FROM kv_store WHERE key='memecoin_learned_thresholds'"
            ).fetchone()
        if row and row[0]:
            learned = json.loads(row[0])
            confidence = str(learned.get("confidence") or "low").lower()
            thresholds = learned.get("thresholds", {})
            if confidence in ("medium", "high"):
                threshold = float(thresholds.get("min_score", threshold))
                max_score = float(thresholds.get("max_score", max_score))
                vacc_min = float(thresholds.get("min_vol_acceleration", vacc_min))
                holder_max = float(thresholds.get("max_top_holder_pct", holder_max))
    except Exception:
        pass
    return {
        "min_score": threshold,
        "max_score": max_score,
        "min_vol_acceleration": vacc_min,
        "max_top_holder_pct": holder_max,
        "confidence": confidence,
    }


def _load_lifecycle_gate_map() -> dict:
    out: dict = {}
    try:
        with get_conn() as conn:
            for row in conn.execute(
                "SELECT mint, entry_window, fuel_quality, move_phase, first_leg_confirmed, "
                "COALESCE(provisional_first_leg, 0), COALESCE(provisional_reason, ''), COALESCE(provisional_score, 0.0) "
                "FROM symbol_lifecycle WHERE mint IS NOT NULL"
            ).fetchall():
                out[row[0]] = {
                    "entry_window": row[1],
                    "fuel_quality": row[2],
                    "move_phase": row[3],
                    "first_leg_confirmed": int(row[4] or 0),
                    "provisional_first_leg": int(row[5] or 0),
                    "provisional_reason": str(row[6] or ""),
                    "provisional_score": float(row[7] or 0.0),
                }
    except Exception as exc:
        log.warning("[PROOF] lifecycle map load failed: %s", exc)
    return out


def _recent_loss_info(lock_days: int = 7) -> dict[str, str]:
    try:
        with get_conn() as conn:
            rows = conn.execute(
                """
                SELECT mint, MAX(closed_ts_utc) AS last_loss_ts
                FROM memecoin_trades
                WHERE mint IS NOT NULL
                  AND mint != ''
                  AND pnl_pct < 0
                  AND closed_ts_utc > datetime('now', ?)
                GROUP BY mint
                """,
                (f"-{int(lock_days)} days",),
            ).fetchall()
        return {str(r[0]): str(r[1]) for r in rows if r and r[0] and r[1]}
    except Exception:
        return {}


def _recent_scanner_signals(
    *,
    statuses: tuple[str, ...] = ("PENDING",),
    limit: int = 50,
    max_age_hours: int = 24,
) -> list[dict]:
    """Fallback proof input when the live scanner cache is empty.

    By default this stays close to current scanner intent and only uses recent
    `PENDING` scanner rows. Observe/reporting surfaces can opt into including
    recent `COMPLETE` rows without changing execution behavior.
    """
    statuses = tuple(str(s or "").upper() for s in statuses if str(s or "").strip())
    if not statuses:
        statuses = ("PENDING",)
    placeholders = ",".join("?" for _ in statuses)
    try:
        with get_conn() as conn:
            rows = conn.execute(
                f"""
                SELECT
                    mint,
                    symbol,
                    score,
                    COALESCE(scanner_rank_score, score) AS scanner_rank_score,
                    COALESCE(timing_score, 0.0) AS timing_score,
                    COALESCE(safety_score, 0.0) AS safety_score,
                    COALESCE(market_quality_score, 0.0) AS market_quality_score,
                    COALESCE(score_contract_version, 0) AS score_contract_version,
                    COALESCE(score_components_json, '{{}}') AS score_components_json,
                    rug_label,
                    top_holder_pct,
                    top5_holder_pct,
                    lp_locked_pct,
                    mint_revoked,
                    freeze_revoked,
                    holder_quality_score,
                    holder_quality_level,
                    vol_acceleration,
                    buy_pressure_at_scan,
                    scanner_regime,
                    scanner_relaxation_reason,
                    scanned_at
                FROM memecoin_signal_outcomes
                WHERE source='SCANNER'
                  AND UPPER(status) IN ({placeholders})
                  AND mint IS NOT NULL
                  AND mint != ''
                  AND scanned_at >= datetime('now', ?)
                ORDER BY scanned_at DESC
                LIMIT ?
                """,
                (*statuses, f"-{int(max_age_hours)} hours", int(limit)),
            ).fetchall()
    except Exception as exc:
        log.debug("[PROOF] recent pending scanner fallback load failed: %s", exc)
        return []

    seen_mints: set[str] = set()
    out: list[dict] = []
    for row in rows:
        mint = str(row["mint"] or "")
        if not mint or mint in seen_mints:
            continue
        seen_mints.add(mint)
        out.append(
            {
                "mint": mint,
                "symbol": str(row["symbol"] or "UNKNOWN"),
                "score": float(row["score"] or 0.0),
                "scanner_rank_score": float(row["scanner_rank_score"] or row["score"] or 0.0),
                "timing_score": float(row["timing_score"] or 0.0),
                "safety_score": float(row["safety_score"] or 0.0),
                "market_quality_score": float(row["market_quality_score"] or 0.0),
                "score_contract_version": int(row["score_contract_version"] or 0),
                "score_components": json.loads(str(row["score_components_json"] or "{}")),
                "rug_label": str(row["rug_label"] or "UNKNOWN"),
                "top_holder_pct": float(row["top_holder_pct"] or 0.0),
                "top5_holder_pct": float(row["top5_holder_pct"] or 0.0),
                "lp_locked_pct": float(row["lp_locked_pct"] or 0.0),
                "mint_revoked": bool(row["mint_revoked"] or 0),
                "freeze_revoked": bool(row["freeze_revoked"] or 0),
                "holder_quality_score": float(row["holder_quality_score"] or 0.0),
                "holder_quality_level": str(row["holder_quality_level"] or ""),
                "vol_acceleration": float(row["vol_acceleration"] or 0.0),
                "buy_pressure": float(row["buy_pressure_at_scan"] or 0.0),
                "scanner_regime": str(row["scanner_regime"] or "NORMAL"),
                "scanner_relaxation_reason": str(row["scanner_relaxation_reason"] or ""),
                "scanned_at": str(row["scanned_at"] or ""),
            }
        )
    return out


def _recent_pending_scanner_signals(limit: int = 50, max_age_hours: int = 24) -> list[dict]:
    return _recent_scanner_signals(statuses=("PENDING",), limit=limit, max_age_hours=max_age_hours)


def _warm_cache_signals(limit: int = 50, max_age_hours: int = 6) -> list[dict]:
    signals = list(get_last_nonempty_cached_signals(max_age_hours=max_age_hours) or [])
    if not signals:
        return []
    return sorted(
        signals,
        key=lambda s: float(s.get("score") or 0.0),
        reverse=True,
    )[: max(int(limit), 1)]


def _merge_signal_lists(primary: list[dict], secondary: list[dict], *, limit: int) -> list[dict]:
    merged: list[dict] = []
    seen: set[str] = set()
    for sig in list(primary or []) + list(secondary or []):
        mint = str(sig.get("mint") or "").strip()
        symbol = re.sub(r"[^A-Z0-9]", "", str(sig.get("symbol") or "").upper())
        key = mint or symbol
        if not key or key in seen:
            continue
        seen.add(key)
        merged.append(dict(sig))
    merged.sort(
        key=lambda s: (
            -float(s.get("score") or 0.0),
            str(s.get("scanned_at") or ""),
            str(s.get("symbol") or ""),
        )
    )
    return merged[: max(int(limit), 1)]


def _score_ceiling_context(
    *,
    scanner_score: float,
    scanner_regime: str,
    thresholds: dict,
    hard_max_score: float,
    support_score: float,
    entry_window: str,
    fuel_quality: str,
    move_phase: str,
    first_leg_confirmed: int,
    provisional_first_leg: int,
    rug_label: str,
    mint_revoked: bool,
    vol_acceleration: float,
    top_holder_pct: float,
    buy_pressure: float,
    market_quality_score: float = 0.0,
    marketcap_usd: float = 0.0,
) -> dict:
    base_max = float(thresholds["max_score"])
    extension = 0.0
    reasons: list[str] = []

    if scanner_regime == "NORMAL":
        extension += 3.0
        reasons.append("normal_scanner_regime")
    if support_score >= 8.0:
        extension += 4.0
        reasons.append("reinforcement_backed")
    elif support_score >= 4.0:
        extension += 2.0
        reasons.append("reinforcement_present")
    if entry_window == "OPEN":
        extension += 2.0
        reasons.append("entry_window_open")
    if fuel_quality == "STRONG":
        extension += 2.0
        reasons.append("fuel_strong")
    elif fuel_quality == "MODERATE":
        extension += 1.0
        reasons.append("fuel_moderate")
    if move_phase in ("RELOAD", "MID", "EARLY"):
        extension += 2.0
        reasons.append("move_phase_constructive")
    if int(first_leg_confirmed or 0) == 1:
        extension += 2.0
        reasons.append("first_leg_confirmed")
    elif int(provisional_first_leg or 0) == 1:
        extension += 1.0
        reasons.append("provisional_first_leg")
    if (
        rug_label == "GOOD"
        and mint_revoked
        and vol_acceleration >= float(thresholds["min_vol_acceleration"])
        and top_holder_pct <= float(thresholds["max_top_holder_pct"])
        and buy_pressure >= 50.0
    ):
        extension += 1.0
        reasons.append("scanner_quality_intact")
    if buy_pressure >= 60.0:
        extension += 1.0
        reasons.append("buy_pressure_strong")

    established_quality = (
        market_quality_score >= float(os.getenv("MEMECOIN_ESTABLISHED_MIN_MARKET_QUALITY", "75"))
        and marketcap_usd >= float(os.getenv("MEMECOIN_ESTABLISHED_MIN_MCAP_USD", "5000000"))
        and rug_label == "GOOD"
        and mint_revoked
        and buy_pressure >= 55.0
        and vol_acceleration >= float(thresholds["min_vol_acceleration"])
        and top_holder_pct <= float(thresholds["max_top_holder_pct"])
        and entry_window == "OPEN"
        and fuel_quality in ("STRONG", "MODERATE")
        and move_phase not in ("EXTENDED", "CHURN")
    )
    if established_quality:
        extension += float(os.getenv("MEMECOIN_ESTABLISHED_SCORE_CEILING_EXTENSION", "28"))
        reasons.append("established_market_quality")
        if market_quality_score >= 88.0:
            extension += 4.0
            reasons.append("elite_market_quality")
        if marketcap_usd >= 20_000_000:
            extension += 3.0
            reasons.append("established_marketcap")

    dynamic_max = min(hard_max_score, base_max + extension)
    override = (
        scanner_regime == "NORMAL"
        and scanner_score > base_max
        and scanner_score <= dynamic_max
        and rug_label == "GOOD"
        and mint_revoked
        and buy_pressure >= 55.0
        and vol_acceleration >= float(thresholds["min_vol_acceleration"])
        and top_holder_pct <= float(thresholds["max_top_holder_pct"])
        and entry_window == "OPEN"
        and fuel_quality in ("STRONG", "MODERATE")
        and move_phase not in ("EXTENDED", "CHURN")
        and (
            int(first_leg_confirmed or 0) == 1
            or int(provisional_first_leg or 0) == 1
            or support_score >= 4.0
            or established_quality
        )
        and extension >= 5.0
    )
    return {
        "base_max_score": base_max,
        "dynamic_max_score": round(dynamic_max, 1),
        "extension_points": round(extension, 1),
        "override": override,
        "reasons": reasons,
    }


def _legacy_score_ceiling_override(
    *,
    sig: dict,
    lifecycle: dict | None,
    max_score: float,
    vacc_min: float,
    holder_max: float,
    hard_max_score: float = 95.0,
) -> dict:
    score = float(sig.get("score") or 0.0)
    market_quality_score = float(sig.get("market_quality_score") or 0.0)
    marketcap_usd = float(sig.get("mcap_at_scan") or sig.get("mcap_usd") or 0.0)
    rug = str(sig.get("rug_label") or "").upper()
    bp = float(sig.get("buy_pressure") or 0.0)
    vacc = float(sig.get("vol_acceleration") or 0.0)
    holder_pct = float(sig.get("top_holder_pct") or 0.0)
    revoked = bool(sig.get("mint_revoked"))
    ew = str((lifecycle or {}).get("entry_window") or "").upper()
    fq = str((lifecycle or {}).get("fuel_quality") or "").upper()
    mp = str((lifecycle or {}).get("move_phase") or "").upper()
    flc = int((lifecycle or {}).get("first_leg_confirmed") or 0)
    pfl = int((lifecycle or {}).get("provisional_first_leg") or 0)
    extension = float(os.getenv("MEMECOIN_ESTABLISHED_SCORE_CEILING_EXTENSION", "28"))
    dynamic_max = min(float(hard_max_score), float(max_score) + extension)
    established_quality = (
        market_quality_score >= float(os.getenv("MEMECOIN_ESTABLISHED_MIN_MARKET_QUALITY", "75"))
        and marketcap_usd >= float(os.getenv("MEMECOIN_ESTABLISHED_MIN_MCAP_USD", "5000000"))
        and rug == "GOOD"
        and revoked
        and bp >= 55.0
        and vacc >= float(vacc_min)
        and holder_pct <= float(holder_max)
        and ew == "OPEN"
        and fq in ("STRONG", "MODERATE")
        and mp not in ("EXTENDED", "CHURN")
        and (flc == 1 or pfl == 1 or market_quality_score >= 85.0)
    )
    reviewable_quality = (
        market_quality_score >= 85.0
        and marketcap_usd >= float(os.getenv("MEMECOIN_ESTABLISHED_MIN_MCAP_USD", "5000000"))
        and rug == "GOOD"
        and revoked
        and bp >= 55.0
        and holder_pct <= float(holder_max)
    )
    return {
        "override": bool(score > float(max_score) and score <= dynamic_max and established_quality),
        "reviewable": bool(score > float(max_score) and reviewable_quality),
        "dynamic_max_score": round(dynamic_max, 1),
        "market_quality_score": round(market_quality_score, 1),
        "marketcap_usd": round(marketcap_usd, 2),
    }


def _recent_signal_reinforcement(window_hours: int = 48) -> dict[str, dict]:
    """
    Primarily exact-mint support, with a conservative symbol-family fallback.

    Symbol-family support is only attached when a recent scanner symbol maps to a
    single active mint. This keeps ticker-reuse noise out of the proof lane while
    still rescuing obvious one-mint overlaps.
    """
    support_by_mint: dict[str, dict] = {}
    active_symbol_to_mints: dict[str, set[str]] = {}

    def _ctx(mint: str) -> dict:
        return support_by_mint.setdefault(
            mint,
            {
                "support_score": 0.0,
                "support_tags": [],
                "support_details": [],
                "whale_ready": 0,
                "arkham_meaningful": 0,
                "confluence_events": 0,
                "symbol_family_support": 0,
                "large_trade_buy_count": 0,
                "large_trade_sell_count": 0,
                "large_trade_buy_volume_usd": 0.0,
                "large_trade_sell_volume_usd": 0.0,
                "large_trade_unique_wallets": 0,
                "large_trade_strong_labels": 0,
                "wallet_behavior_state": "MIXED",
                "wallet_conviction_score": 0.0,
                "wallet_cluster_score": 0.0,
                "wallet_behavior_quality": "NONE",
            },
        )

    def _unique_active_mint(symbol: str) -> str:
        symbol = re.sub(r"[^A-Z0-9]", "", str(symbol or "").upper())
        mints = active_symbol_to_mints.get(symbol) or set()
        return next(iter(mints)) if len(mints) == 1 else ""

    try:
        with get_conn() as conn:
            tables = {
                str(r[0])
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }

            active_rows = conn.execute(
                """
                SELECT DISTINCT symbol, mint
                FROM memecoin_signal_outcomes
                WHERE source='SCANNER'
                  AND status IN ('PENDING', 'COMPLETE')
                  AND mint IS NOT NULL
                  AND mint != ''
                  AND symbol IS NOT NULL
                  AND symbol != ''
                  AND scanned_at >= datetime('now', ?)
                """,
                (f"-{max(int(window_hours), 72)} hours",),
            ).fetchall()
            for row in active_rows:
                symbol = re.sub(r"[^A-Z0-9]", "", str(row["symbol"] or "").upper())
                mint = str(row["mint"] or "").strip()
                if symbol and mint:
                    active_symbol_to_mints.setdefault(symbol, set()).add(mint)
            active_mints = sorted({mint for mints in active_symbol_to_mints.values() for mint in mints if mint})

            if "whale_watch_alerts" in tables:
                whale_cols = {
                    str(r[1])
                    for r in conn.execute("PRAGMA table_info(whale_watch_alerts)").fetchall()
                }
                whale_select = [
                    "token_mint",
                    "token_symbol",
                    "scanner_pass",
                    "mc_in_range",
                ]
                if "arkham_signal_quality" in whale_cols:
                    whale_select.append("arkham_signal_quality")
                whale_rows = conn.execute(
                    f"""
                    SELECT {", ".join(whale_select)}
                    FROM whale_watch_alerts
                    WHERE token_mint IS NOT NULL
                      AND token_mint != ''
                      AND ts_utc >= datetime('now', ?)
                    """,
                    (f"-{int(window_hours)} hours",),
                ).fetchall()
                for row in whale_rows:
                    mint = str(row["token_mint"] or "")
                    if not mint:
                        mint = _unique_active_mint(row["token_symbol"])
                    if not mint:
                        continue
                    ctx = _ctx(mint)
                    is_symbol_family = str(row["token_mint"] or "") != mint
                    if int(row["scanner_pass"] or 0) == 1:
                        ctx["whale_ready"] += 1
                        tag = "whale_family_overlap" if is_symbol_family else "whale_overlap"
                        detail = (
                            "recent whale symbol-family overlap (unique active mint)"
                            if is_symbol_family else
                            "recent whale scanner-pass overlap"
                        )
                        score_bump = 2.0 if is_symbol_family else 6.0
                        if tag not in ctx["support_tags"]:
                            ctx["support_tags"].append(tag)
                            ctx["support_details"].append(detail)
                            ctx["support_score"] += score_bump
                            if is_symbol_family:
                                ctx["symbol_family_support"] += 1
                    if "arkham_signal_quality" in row.keys():
                        quality = str(row["arkham_signal_quality"] or "").upper()
                        if quality in ("HIGH", "MEDIUM", "LOW"):
                            ctx["arkham_meaningful"] += 1
                            if "arkham_meaningful" not in ctx["support_tags"]:
                                ctx["support_tags"].append("arkham_meaningful")
                                ctx["support_details"].append("recent Arkham meaningful context")
                                ctx["support_score"] += 4.0

            if "confluence_events" in tables:
                conf_cols = {
                    str(r[1])
                    for r in conn.execute("PRAGMA table_info(confluence_events)").fetchall()
                }
                conf_select = ["token_mint", "token_symbol", "confluence_type", "confluence_score"]
                if "overlap_scope" in conf_cols:
                    conf_select.append("overlap_scope")
                conf_rows = conn.execute(
                    f"""
                    SELECT {", ".join(conf_select)}
                    FROM confluence_events
                    WHERE ts_utc >= datetime('now', ?)
                    """,
                    (f"-{int(window_hours)} hours",),
                ).fetchall()
                for row in conf_rows:
                    mint = str(row["token_mint"] or "")
                    overlap_scope = str(row["overlap_scope"] or "").upper() if "overlap_scope" in row.keys() else ""
                    if not mint and overlap_scope == "SYMBOL_FAMILY":
                        mint = _unique_active_mint(row["token_symbol"])
                    if not mint:
                        continue
                    ctx = _ctx(mint)
                    ctx["confluence_events"] += 1
                    is_symbol_family = overlap_scope == "SYMBOL_FAMILY" or str(row["token_mint"] or "") != mint
                    tag = "confluence_family_overlap" if is_symbol_family else "confluence_overlap"
                    if tag not in ctx["support_tags"]:
                        ctx["support_tags"].append(tag)
                        ctx["support_details"].append(
                            (
                                f"recent {str(row['confluence_type'] or 'confluence').lower()} symbol-family event"
                                if is_symbol_family else
                                f"recent {str(row['confluence_type'] or 'confluence').lower()} event"
                            )
                        )
                        ctx["support_score"] += 3.0 if is_symbol_family else 8.0
                        if is_symbol_family:
                            ctx["symbol_family_support"] += 1

            if "memecoin_large_trade_snapshots" in tables:
                from utils.db import get_recent_large_trade_support_for_mints  # type: ignore

                large_trade_support = get_recent_large_trade_support_for_mints(
                    active_mints,
                    max_age_minutes=max(30, int(window_hours * 60)),
                )
                for mint, trade_ctx in large_trade_support.items():
                    if not mint:
                        continue
                    ctx = _ctx(mint)
                    buy_count = int(trade_ctx.get("buy_count") or 0)
                    sell_count = int(trade_ctx.get("sell_count") or 0)
                    buy_volume = float(trade_ctx.get("buy_volume_usd") or 0.0)
                    sell_volume = float(trade_ctx.get("sell_volume_usd") or 0.0)
                    unique_wallets = int(trade_ctx.get("unique_wallets") or 0)
                    strong_labels = int(trade_ctx.get("strong_labels") or 0)
                    ctx["large_trade_buy_count"] = buy_count
                    ctx["large_trade_sell_count"] = sell_count
                    ctx["large_trade_buy_volume_usd"] = buy_volume
                    ctx["large_trade_sell_volume_usd"] = sell_volume
                    ctx["large_trade_unique_wallets"] = unique_wallets
                    ctx["large_trade_strong_labels"] = strong_labels

                    if buy_count > 0 and buy_volume > sell_volume:
                        if "large_trade_buy_flow" not in ctx["support_tags"]:
                            ctx["support_tags"].append("large_trade_buy_flow")
                            ctx["support_details"].append(
                                f"recent large-buy flow ${buy_volume:,.0f} across {buy_count} print{'s' if buy_count != 1 else ''}"
                            )
                            ctx["support_score"] += 6.0 if buy_volume >= 25000 else 3.0
                    if strong_labels > 0 and "large_trade_sponsorship" not in ctx["support_tags"]:
                        ctx["support_tags"].append("large_trade_sponsorship")
                        ctx["support_details"].append("recent larger trade sponsorship is showing up")
                        ctx["support_score"] += 6.0

            if "mint_wallet_behavior_snapshots" in tables and active_mints:
                from utils.db import get_latest_wallet_behavior_for_mints  # type: ignore

                behavior_by_mint = get_latest_wallet_behavior_for_mints(
                    active_mints,
                    max_age_minutes=max(60, int(window_hours * 60)),
                )
                for mint, behavior in behavior_by_mint.items():
                    ctx = _ctx(mint)
                    conviction = float(behavior.get("wallet_conviction_score") or 0.0)
                    cluster = float(behavior.get("wallet_cluster_score") or 0.0)
                    state = str(behavior.get("wallet_behavior_state") or "MIXED").upper()
                    quality = str(behavior.get("smart_money_quality") or "NONE").upper()
                    ctx["wallet_behavior_state"] = state
                    ctx["wallet_conviction_score"] = conviction
                    ctx["wallet_cluster_score"] = cluster
                    ctx["wallet_behavior_quality"] = quality

                    if quality in ("LIGHT", "MODERATE", "STRONG"):
                        tag = "wallet_live_conviction"
                        if tag not in ctx["support_tags"]:
                            ctx["support_tags"].append(tag)
                            ctx["support_details"].append(
                                f"live wallet behavior {state.lower()} with {quality.lower()} smart-money quality"
                            )
                            ctx["support_score"] += 4.0 if quality == "LIGHT" else 8.0 if quality == "MODERATE" else 12.0
                    if state in ("ENTERING", "ADDING") and conviction >= 55.0:
                        tag = "wallet_live_add"
                        if tag not in ctx["support_tags"]:
                            ctx["support_tags"].append(tag)
                            ctx["support_details"].append("tracked wallets are adding in real time")
                            ctx["support_score"] += 6.0
    except Exception as exc:
        log.debug("[PROOF] reinforcement context load failed: %s", exc)

    return support_by_mint


def _compute_support_score(
    *,
    raw_support_score: float,
    support_tags: list[str],
    whale_ready: int,
    arkham_meaningful: int,
    confluence_events: int,
    symbol_family_support: int,
    wallet_overlap_score: float,
    wallet_support_level: str,
    wallet_confidence: float,
    wallet_repeat_wallets: int,
    wallet_high_quality_wallets: int,
    wallet_recent_activity: bool,
    large_trade_buy_count: int,
    large_trade_sell_count: int,
    large_trade_buy_volume_usd: float,
    large_trade_sell_volume_usd: float,
    large_trade_unique_wallets: int,
    large_trade_strong_labels: int,
    wallet_behavior_state: str,
    wallet_conviction_score: float,
    wallet_cluster_score: float,
    wallet_behavior_quality: str,
) -> dict:
    score = 0.0
    reasons: list[str] = []

    exact_overlap = any(tag in ("whale_overlap", "confluence_overlap") for tag in support_tags)
    family_overlap = any(tag in ("whale_family_overlap", "confluence_family_overlap") for tag in support_tags)

    if exact_overlap:
        score += 26.0
        reasons.append("exact overlap present")
    elif family_overlap:
        score += 10.0
        reasons.append("symbol-family overlap present")

    if whale_ready > 0:
        score += min(16.0, 6.0 + (whale_ready - 1) * 3.0)
        reasons.append(f"whale support x{whale_ready}")

    if confluence_events > 0:
        score += min(18.0, 8.0 + (confluence_events - 1) * 3.0)
        reasons.append(f"confluence support x{confluence_events}")

    if arkham_meaningful > 0:
        score += min(10.0, 4.0 + (arkham_meaningful - 1) * 2.0)
        reasons.append("Arkham context present")

    if symbol_family_support > 0 and not exact_overlap:
        score += min(6.0, float(symbol_family_support) * 2.0)

    if large_trade_buy_count > 0 and large_trade_buy_volume_usd > large_trade_sell_volume_usd:
        score += min(16.0, 4.0 + max(0.0, large_trade_buy_volume_usd - large_trade_sell_volume_usd) / 15000.0)
        reasons.append(f"large-buy support x{large_trade_buy_count}")
    if large_trade_unique_wallets > 1:
        score += min(8.0, float(large_trade_unique_wallets - 1) * 2.0)
    if large_trade_strong_labels > 0:
        score += min(10.0, float(large_trade_strong_labels) * 3.0)
        reasons.append("large-trade sponsorship present")

    if wallet_behavior_quality in ("LIGHT", "MODERATE", "STRONG"):
        score += {"LIGHT": 5.0, "MODERATE": 11.0, "STRONG": 18.0}.get(wallet_behavior_quality, 0.0)
        if wallet_behavior_state in ("ENTERING", "ADDING"):
            score += min(10.0, max(wallet_conviction_score - 45.0, 0.0) * 0.2)
            reasons.append(f"wallets {wallet_behavior_state.lower()}")
        if wallet_cluster_score >= 45.0:
            score += min(8.0, (wallet_cluster_score - 40.0) * 0.15)
            reasons.append("wallet cluster is converging")

    wallet_base = {
        "NONE": 0.0,
        "LIGHT": 8.0,
        "MODERATE": 16.0,
        "STRONG": 24.0,
    }.get(str(wallet_support_level or "").upper(), 0.0)
    wallet_conf_multiplier = 0.5 if wallet_confidence < 35.0 else 0.8 if wallet_confidence < 60.0 else 1.0
    wallet_component = wallet_base * wallet_conf_multiplier
    wallet_component += min(10.0, max(wallet_overlap_score - 50.0, 0.0) * 0.20)
    if wallet_repeat_wallets > 0:
        wallet_component += min(8.0, wallet_repeat_wallets * 2.0)
    if wallet_high_quality_wallets > 0:
        wallet_component += min(8.0, wallet_high_quality_wallets * 2.0)
    if wallet_recent_activity:
        wallet_component += 4.0
    if wallet_component > 0:
        score += wallet_component
        reasons.append(f"wallet support {str(wallet_support_level or 'none').lower()}")

    score += min(10.0, max(raw_support_score, 0.0) * 0.20)
    support_score = round(max(0.0, min(100.0, score)), 1)

    if support_score >= 75.0:
        level = "STRONG"
    elif support_score >= 45.0:
        level = "MODERATE"
    elif support_score >= 20.0:
        level = "LIGHT"
    else:
        level = "NONE"

    return {
        "support_score": support_score,
        "support_level": level,
        "support_reasons": reasons[:5],
        "has_exact_support": exact_overlap,
        "has_family_support": family_overlap,
        "wallet_component": round(wallet_component, 1),
        "raw_overlap_score": round(raw_support_score, 1),
    }


def _compute_readiness_score(
    *,
    timing_score: float,
    safety_score: float,
    market_quality_score: float,
    market_quality_verdict: str,
    support_score: float,
    profit_room_score: float,
    profit_room_label: str,
    proof_status: str,
    blocker_key: str,
    entry_window: str,
    fuel_quality: str,
    move_phase: str,
    first_leg_confirmed: int,
    provisional_first_leg: int,
    scanner_regime: str,
    score_ceiling_override: bool,
    clean_warn_probe_ready: bool,
    recent_loss: bool,
) -> dict:
    base = (
        float(timing_score) * 0.30
        + float(safety_score) * 0.30
        + float(market_quality_score) * 0.20
        + float(support_score) * 0.20
    )
    modifiers = 0.0
    reasons: list[str] = []

    if entry_window == "OPEN":
        modifiers += 6.0
        reasons.append("entry window open")
    elif entry_window == "CLOSING":
        modifiers -= 10.0
        reasons.append("entry window closing")
    elif entry_window == "CLOSED":
        modifiers -= 16.0

    if fuel_quality == "STRONG":
        modifiers += 6.0
        reasons.append("fuel strong")
    elif fuel_quality == "MODERATE":
        modifiers += 3.0
    elif fuel_quality == "TRAP":
        modifiers -= 16.0
    else:
        modifiers -= 12.0
        reasons.append("fuel still weak")

    modifiers += {
        "RELOAD": 6.0,
        "MID": 4.0,
        "EARLY": 4.0,
        "IGNITION": 2.0,
        "EXTENDED": -18.0,
        "CHURN": -18.0,
    }.get(str(move_phase or "").upper(), 0.0)

    if int(first_leg_confirmed or 0) == 1:
        modifiers += 6.0
        reasons.append("first leg confirmed")
    elif int(provisional_first_leg or 0) == 1:
        modifiers -= 12.0
        reasons.append("only provisional first leg")
    else:
        modifiers -= 18.0
        reasons.append("first leg not confirmed")

    if scanner_regime == "RELAXED_NEAR_MISS":
        modifiers -= 6.0
    mq_verdict = str(market_quality_verdict or "").upper()
    if mq_verdict == "CLEAN":
        modifiers += 6.0
        reasons.append("market quality clean")
    elif mq_verdict == "WATCH":
        modifiers += 1.0
        reasons.append("market quality watchable")
    elif mq_verdict == "UNSTABLE":
        modifiers -= 14.0
        reasons.append("market still unstable")
    elif mq_verdict == "AVOID":
        modifiers -= 22.0
        reasons.append("market quality too weak")
    if float(market_quality_score) < 45.0:
        modifiers -= 8.0
        reasons.append("market quality score still low")
    pr_label = str(profit_room_label or "").upper()
    if pr_label == "EARLY":
        modifiers += 6.0
        reasons.append("profit room still early")
    elif pr_label == "WORKABLE":
        modifiers += 2.0
        reasons.append("profit room still workable")
    elif pr_label == "STRETCHED":
        modifiers -= 10.0
        reasons.append("profit room already stretched")
    elif pr_label == "TOO_LATE":
        modifiers -= 20.0
        reasons.append("profit room already too late")
    if float(profit_room_score) < 35.0:
        modifiers -= 4.0
    if score_ceiling_override:
        modifiers += 2.0
    if clean_warn_probe_ready:
        modifiers -= 3.0
        reasons.append("clean WARN review path")
    if recent_loss:
        modifiers -= 8.0

    score = base + modifiers

    status = str(proof_status or "").upper()
    if status == "REJECTED":
        score = min(score, 39.0)
    elif status == "RESEARCH_ONLY":
        score = min(score, 64.0)
    elif status == "PROOF_READY":
        score = max(score, 70.0)
    if pr_label == "TOO_LATE":
        score = min(score, 59.0)
    elif pr_label == "STRETCHED" and status == "PROOF_READY":
        score = min(score, 74.0)

    readiness_score = round(max(0.0, min(100.0, score)), 1)
    if readiness_score >= 80.0:
        level = "READY"
    elif readiness_score >= 60.0:
        level = "NEARLY_READY"
    elif readiness_score >= 40.0:
        level = "DEVELOPING"
    else:
        level = "EARLY"

    if blocker_key:
        reasons.append(f"blocked by {blocker_key}")

    return {
        "readiness_score": readiness_score,
        "readiness_level": level,
        "readiness_reasons": reasons[:6],
        "base_score": round(base, 1),
        "modifier_points": round(modifiers, 1),
    }


def _legacy_score_dimension_fallback(
    *,
    scanner_score: float,
    rug_label: str,
    holder_quality_score: float,
    buy_pressure: float,
    vol_acceleration: float,
    top_holder_pct: float,
) -> dict:
    """Backfill dimension scores for pre-migration scanner rows."""
    from utils.trade_quality import assess_trade_quality_from_snapshot

    timing_score = max(0.0, min(100.0, scanner_score))
    safety_score = holder_quality_score * 0.75
    safety_score += {"GOOD": 20.0, "WARN": 8.0, "UNKNOWN": 0.0, "DANGER": -30.0, "RUGGED": -50.0}.get(
        str(rug_label or "").upper(),
        0.0,
    )
    if float(top_holder_pct or 0.0) > 5.0:
        safety_score -= 10.0
    trade_quality = assess_trade_quality_from_snapshot(
        liquidity_usd=0.0,
        volume_24h=0.0,
        change_1h=0.0,
        vol_acceleration=float(vol_acceleration or 0.0),
        buy_pressure=float(buy_pressure or 50.0),
        txns_h1=0,
    )
    market_quality_score = float(trade_quality.get("execution_quality_score") or 0.0)
    return {
        "timing_score": round(max(0.0, min(100.0, timing_score)), 1),
        "safety_score": round(max(0.0, min(100.0, safety_score)), 1),
        "market_quality_score": round(max(0.0, min(100.0, market_quality_score)), 1),
    }


def _market_quality_profile(sig: dict, market_quality_score: float) -> dict:
    score_components = dict(sig.get("score_components") or {})
    mq = dict(score_components.get("market_quality") or {})
    snapshot = dict(sig.get("trade_quality_snapshot") or {})
    verdict = (
        str(sig.get("market_quality_verdict") or "")
        or str(snapshot.get("quality_verdict") or "")
        or str(mq.get("quality_verdict") or "")
    ).upper()
    reasons = list(sig.get("market_quality_reasons") or []) or list(snapshot.get("reasons") or []) or list(mq.get("reasons") or [])
    if verdict not in ("CLEAN", "WATCH", "UNSTABLE", "AVOID"):
        if market_quality_score >= 75.0:
            verdict = "CLEAN"
        elif market_quality_score >= 58.0:
            verdict = "WATCH"
        elif market_quality_score >= 40.0:
            verdict = "UNSTABLE"
        else:
            verdict = "AVOID"
    return {
        "market_quality_verdict": verdict,
        "market_quality_reasons": [str(r or "").strip() for r in reasons if str(r or "").strip()][:5],
    }


def _established_runner_policy_v2(
    *,
    profile: dict,
    sig: dict,
    scanner_score: float,
    scanner_regime: str,
    rug_label: str,
    mint_revoked: bool,
    buy_pressure: float,
    vol_acceleration: float,
    holder_pct: float,
    max_holder_pct: float,
    market_quality_score: float,
    market_quality_verdict: str,
    lifecycle: dict | None,
    support_score: float,
    support_level: str,
    data_freshness: dict,
) -> dict:
    """Policy layer for known runners/recovery names.

    These names should not be forced through every fresh-launch lifecycle gate,
    but they still need safety, market quality, and renewed momentum.
    """
    enabled = _env_bool("ESTABLISHED_RUNNER_POLICY_V2_ENABLED", True) and bool(profile)
    if not enabled:
        return {"enabled": False}

    min_score = float(os.getenv("ESTABLISHED_RUNNER_READY_MIN_SCORE", "62"))
    min_mq = float(os.getenv("ESTABLISHED_RUNNER_READY_MIN_MARKET_QUALITY", "70"))
    min_bp = float(os.getenv("ESTABLISHED_RUNNER_READY_MIN_BUY_PRESSURE", "50"))
    min_vacc = float(os.getenv("ESTABLISHED_RUNNER_READY_MIN_VOL_ACCEL", "2"))
    allow_lifecycle_missing = _env_bool("ESTABLISHED_RUNNER_ALLOW_LIFECYCLE_MISSING", True)
    proof_ready_enabled = _env_bool("ESTABLISHED_RUNNER_PROOF_READY_ENABLED", False)

    runner_verdict = str(sig.get("runner_verdict") or "").upper()
    runner_money_state = str(sig.get("runner_money_state") or "").upper()
    profile_label = str(profile.get("profile") or "").upper()
    mq_verdict = str(market_quality_verdict or "").upper()
    data_status = str(data_freshness.get("status") or "HIGH").upper()
    ew = str((lifecycle or {}).get("entry_window") or "").upper()
    fq = str((lifecycle or {}).get("fuel_quality") or "").upper()
    mp = str((lifecycle or {}).get("move_phase") or "").upper()
    flc = int((lifecycle or {}).get("first_leg_confirmed") or 0)
    provisional_first_leg = int((lifecycle or {}).get("provisional_first_leg") or 0)

    market_quality_ok = mq_verdict in ("CLEAN", "WATCH") and float(market_quality_score) >= min_mq
    safety_ok = (
        str(rug_label or "").upper() == "GOOD"
        and bool(mint_revoked)
        and float(holder_pct) <= float(max_holder_pct)
        and data_status != "LOW"
    )

    support_ok = str(support_level or "").upper() in ("MODERATE", "STRONG") or float(support_score) >= 45.0
    runner_confirming = runner_verdict in {
        "SCOUT_READY",
        "WATCH_CLOSE",
        "WATCH_RELOAD",
        "ACCUMULATION_ZONE",
    } or runner_money_state in {
        "BUYABLE_NOW",
        "WAITING_CONFIRMATION",
        "WAITING_TRIGGER",
    }
    momentum_ok = (
        (float(buy_pressure) >= min_bp and float(vol_acceleration) >= min_vacc)
        or float(buy_pressure) >= min_bp + 6.0
        or float(vol_acceleration) >= min_vacc + 2.0
        or (support_ok and runner_confirming and (float(buy_pressure) >= min_bp - 2.0 or float(vol_acceleration) >= min_vacc - 0.75))
    )

    extension_risk = (
        runner_verdict in {"SELL_INTO_STRENGTH", "DISTRIBUTION", "FADE_RISK", "DANGER"}
        or runner_money_state in {"PROTECT_PROFIT", "AVOID_CHASE", "DISTRIBUTING"}
        or ew == "CLOSED"
        or fq == "TRAP"
        or mp in {"EXTENDED", "CHURN"}
    )

    if lifecycle is None:
        lifecycle_ok = bool(allow_lifecycle_missing)
    else:
        lifecycle_ok = (
            ew in ("", "OPEN", "CLOSING")
            and fq in ("", "STRONG", "MODERATE")
            and mp not in {"EXTENDED", "CHURN"}
            and (flc == 1 or provisional_first_leg == 1 or allow_lifecycle_missing)
        )

    score_ok = float(scanner_score) >= min_score
    ready_path = (
        score_ok
        and safety_ok
        and market_quality_ok
        and momentum_ok
        and lifecycle_ok
        and not extension_risk
    )
    watch_close = (
        score_ok
        and safety_ok
        and market_quality_ok
        and not extension_risk
        and (runner_confirming or support_ok or momentum_ok)
    )

    if not safety_ok:
        state = "RUNNER_SAFETY_REVIEW"
        blocker = "runner_safety_review"
    elif not market_quality_ok:
        state = "RUNNER_QUALITY_LOW"
        blocker = "runner_quality_below_policy"
    elif extension_risk:
        state = "RUNNER_EXTENSION_RISK"
        blocker = "runner_extension_risk"
    elif not momentum_ok:
        state = "RUNNER_NEEDS_MOMENTUM"
        blocker = "runner_momentum_unconfirmed"
    elif not lifecycle_ok:
        state = "RUNNER_LIFECYCLE_UNCONFIRMED"
        blocker = "runner_lifecycle_unconfirmed"
    elif ready_path:
        state = "RUNNER_READY"
        blocker = "" if proof_ready_enabled else "runner_manual_review_required"
    else:
        state = "RUNNER_WATCH_CLOSE" if watch_close else "RUNNER_WATCHING"
        blocker = "runner_momentum_unconfirmed"

    return {
        "enabled": True,
        "version": 2,
        "state": state,
        "blocker_key": blocker,
        "ready_path": ready_path,
        "watch_close": watch_close,
        "proof_ready_enabled": proof_ready_enabled,
        "manual_review_required": ready_path and not proof_ready_enabled,
        "score_ok": score_ok,
        "safety_ok": safety_ok,
        "market_quality_ok": market_quality_ok,
        "momentum_ok": momentum_ok,
        "lifecycle_ok": lifecycle_ok,
        "extension_risk": extension_risk,
        "runner_verdict": runner_verdict or None,
        "runner_money_state": runner_money_state or None,
        "profile": profile_label or None,
        "min_score": min_score,
        "min_market_quality": min_mq,
        "min_buy_pressure": min_bp,
        "min_vol_acceleration": min_vacc,
        "allow_lifecycle_missing": allow_lifecycle_missing,
        "data_status": data_status,
    }


def _merge_live_token_stats_profile(
    *,
    sig: dict,
    token_stats: dict | None,
    timing_score: float,
    market_quality_score: float,
    market_quality_verdict: str,
    market_quality_reasons: list[str],
) -> dict:
    if not token_stats:
        return {
            "timing_score": timing_score,
            "market_quality_score": market_quality_score,
            "market_quality_verdict": market_quality_verdict,
            "market_quality_reasons": market_quality_reasons,
            "live_token_stats": None,
        }

    try:
        from utils.memecoin_scanner import _compute_timing_score  # type: ignore
        from utils.token_stats import build_pair_like_from_token_stats, derive_live_candidate_metrics  # type: ignore
    except Exception:
        return {
            "timing_score": timing_score,
            "market_quality_score": market_quality_score,
            "market_quality_verdict": market_quality_verdict,
            "market_quality_reasons": market_quality_reasons,
            "live_token_stats": None,
        }

    metrics = derive_live_candidate_metrics(token_stats)
    pair_like = build_pair_like_from_token_stats(token_stats)
    live_timing_score, timing_components = _compute_timing_score(
        pair_like,
        float(metrics.get("vol_acceleration") or 0.0),
        float(metrics.get("marketcap") or 0.0),
        float(metrics.get("buy_pressure") or 50.0),
    )
    trade_quality = dict(token_stats.get("trade_quality") or {})
    live_market_quality_score = float(trade_quality.get("execution_quality_score") or market_quality_score or 0.0)
    merged_timing = round((float(timing_score or 0.0) * 0.4) + (live_timing_score * 0.6), 1)
    merged_market_quality = round((float(market_quality_score or 0.0) * 0.35) + (live_market_quality_score * 0.65), 1)

    if merged_market_quality >= 75.0:
        merged_verdict = "CLEAN"
    elif merged_market_quality >= 58.0:
        merged_verdict = "WATCH"
    elif merged_market_quality >= 40.0:
        merged_verdict = "UNSTABLE"
    else:
        merged_verdict = "AVOID"

    live_reasons = [str(r or "").strip() for r in list(trade_quality.get("reasons") or []) if str(r or "").strip()]
    merged_reasons = list(dict.fromkeys((live_reasons + list(market_quality_reasons or []))))[:5]

    return {
        "timing_score": merged_timing,
        "market_quality_score": merged_market_quality,
        "market_quality_verdict": merged_verdict,
        "market_quality_reasons": merged_reasons,
        "live_token_stats": {
            "ts_utc": token_stats.get("ts_utc"),
            "price": token_stats.get("price"),
            "marketcap": metrics.get("marketcap"),
            "price_change_1h_percent": token_stats.get("price_change_1h_percent"),
            "price_change_24h_percent": token_stats.get("price_change_24h_percent"),
            "volume_1h_usd": token_stats.get("volume_1h_usd"),
            "volume_24h_usd": token_stats.get("volume_24h_usd"),
            "trade_1h": token_stats.get("trade_1h"),
            "unique_wallet_1h": token_stats.get("unique_wallet_1h"),
            "buy_pressure_1h": metrics.get("buy_pressure"),
            "vol_acceleration_est": metrics.get("vol_acceleration"),
            "timing_score_live": round(live_timing_score, 1),
            "timing_components_live": timing_components,
            "market_quality_score_live": round(live_market_quality_score, 1),
            "market_quality_verdict_live": str(trade_quality.get("quality_verdict") or merged_verdict),
            "market_quality_reasons_live": live_reasons[:5],
        },
    }


def _compute_profit_room_profile(
    *,
    sig: dict,
    lifecycle: dict | None,
    support: dict,
    live_token_stats: dict | None,
    market_quality_score: float,
) -> dict:
    try:
        from utils.memecoin_scanner import _compute_profit_room_score  # type: ignore
    except Exception:
        return {
            "profit_room_score": 50.0,
            "profit_room_label": "WORKABLE",
            "profit_room_reasons": [],
        }

    score_components = dict(sig.get("score_components") or {})
    existing_profit = dict(score_components.get("profit_room") or {})
    change_1h = float((live_token_stats or {}).get("price_change_1h_percent") or sig.get("change_1h") or 0.0)
    change_24h = float((live_token_stats or {}).get("price_change_24h_percent") or sig.get("change_24h") or 0.0)
    fresh_score, _, fresh_reasons, fresh_components = _compute_profit_room_score(
        change_1h=change_1h,
        change_24h=change_24h,
        mcap_usd=float(sig.get("mcap_at_scan") or sig.get("mcap_usd") or 0.0),
        buy_pressure=float((live_token_stats or {}).get("buy_pressure_1h") or sig.get("buy_pressure") or 50.0),
        vol_acceleration=float((live_token_stats or {}).get("vol_acceleration_est") or sig.get("vol_acceleration") or 0.0),
        entry_window=str((lifecycle or {}).get("entry_window") or ""),
        move_phase=str((lifecycle or {}).get("move_phase") or ""),
        fuel_quality=str((lifecycle or {}).get("fuel_quality") or ""),
        first_leg_confirmed=int((lifecycle or {}).get("first_leg_confirmed") or 0),
        provisional_first_leg=int((lifecycle or {}).get("provisional_first_leg") or 0),
        wallet_behavior_state=str(support.get("wallet_behavior_state") or ""),
        wallet_conviction_score=float(support.get("wallet_conviction_score") or 0.0),
        large_trade_buy_count=int(support.get("large_trade_buy_count") or 0),
        large_trade_sell_count=int(support.get("large_trade_sell_count") or 0),
        market_quality_score=float(market_quality_score or 0.0),
    )
    existing_score = float(existing_profit.get("score") or 0.0)
    merged_score = round(
        fresh_score if existing_score <= 0.0 else ((existing_score * 0.30) + (fresh_score * 0.70)),
        1,
    )
    if merged_score >= 75.0:
        merged_label = "EARLY"
    elif merged_score >= 55.0:
        merged_label = "WORKABLE"
    elif merged_score >= 35.0:
        merged_label = "STRETCHED"
    else:
        merged_label = "TOO_LATE"
    merged_reasons = list(
        dict.fromkeys(
            [
                str(reason or "").strip()
                for reason in list(fresh_reasons or []) + list(existing_profit.get("reasons") or [])
                if str(reason or "").strip()
            ]
        )
    )[:5]
    return {
        "profit_room_score": merged_score,
        "profit_room_label": merged_label,
        "profit_room_reasons": merged_reasons,
        "profit_room_components": fresh_components,
    }


def get_proof_candidate_snapshot(
    limit: int = 50,
    open_mints: set[str] | None = None,
    include_recent_complete: bool = False,
) -> dict:
    if open_mints is None:
        try:
            with get_conn() as conn:
                open_mints = {
                    str(r[0])
                    for r in conn.execute(
                        "SELECT mint FROM memecoin_trades WHERE status='OPEN'"
                    ).fetchall()
                    if r and r[0]
                }
        except Exception:
            open_mints = set()
    cache_key = (
        int(limit),
        bool(include_recent_complete),
        tuple(sorted(str(m or "") for m in (open_mints or set()) if str(m or "").strip())),
    )
    now_monotonic = time.monotonic()
    cached = _proof_snapshot_cache.get(cache_key)
    if cached and now_monotonic - cached[0] <= _PROOF_SNAPSHOT_CACHE_TTL_SECONDS:
        return copy.deepcopy(cached[1])

    thresholds = _load_auto_buy_thresholds()
    hard_max_score = 95.0
    lifecycle_map = _load_lifecycle_gate_map()
    loss_info = _recent_loss_info()
    support_by_mint = _recent_signal_reinforcement()
    data_freshness = _proof_data_freshness_context()

    def _reason_from_blocker(
        blocker: str,
        sig: dict,
        lifecycle: dict | None,
    ) -> str:
        ew = str((lifecycle or {}).get("entry_window") or "")
        fq = str((lifecycle or {}).get("fuel_quality") or "")
        mp = str((lifecycle or {}).get("move_phase") or "")
        flc = int((lifecycle or {}).get("first_leg_confirmed") or 0)
        pfl = int((lifecycle or {}).get("provisional_first_leg") or 0)
        pfl_reason = str((lifecycle or {}).get("provisional_reason") or "")
        bp = float(sig.get("buy_pressure") or 0.0)
        vacc = float(sig.get("vol_acceleration") or 0.0)
        top_h = float(sig.get("top_holder_pct") or 0.0)
        score = float(sig.get("score") or 0.0)
        dynamic_max = float(sig.get("dynamic_max_score") or 0.0)
        hq_level = str(sig.get("holder_quality_level") or "")
        market_quality_verdict = str(sig.get("market_quality_verdict") or "")
        return {
            "missing_mint": "scanner row has no mint",
            "already_open": "mint already has an open trade",
            "score_below_floor": f"score={score:.1f} below tuned floor",
            "score_above_ceiling": (
                f"score={score:.1f} above tuned ceiling"
                + (f" (dynamic max {dynamic_max:.1f})" if dynamic_max > 0 else "")
            ),
            "rug_not_good": f"rug_label={sig.get('rug_label')}",
            "rug_danger": f"rug_label={sig.get('rug_label')}",
            "rug_warning": f"rug_label={sig.get('rug_label')}",
            "rug_warn_review": f"rug_label={sig.get('rug_label')} with clean safety profile",
            "rug_data_missing": f"rug telemetry missing (hq={hq_level or 'UNKNOWN'})",
            "buy_pressure_low": f"buy_pressure={bp:.1f}% below gate",
            "mint_not_revoked": "mint authority still active",
            "vol_acceleration_low": f"vol_acceleration={vacc:.1f} below gate",
            "holder_concentration": f"top_holder={top_h:.1f}% above gate",
            "market_quality_low": f"market_quality={float(sig.get('market_quality_score') or 0.0):.1f} too weak",
            "market_quality_unstable": f"market_quality_verdict={market_quality_verdict or 'UNSTABLE'}",
            "market_quality_avoid": f"market_quality_verdict={market_quality_verdict or 'AVOID'}",
            "lifecycle_missing": "lifecycle row missing",
            "entry_window_closed": "entry_window=CLOSED",
            "fuel_trap": "fuel_quality=TRAP",
            "move_phase_extended": f"move_phase={mp}",
            "entry_window_not_open": f"entry_window={ew}",
            "fuel_not_actionable": f"fuel_quality={fq}",
            "first_leg_unconfirmed": f"first_leg_confirmed={flc}",
            "provisional_first_leg_only": (
                f"provisional_first_leg={pfl}"
                + (f" ({pfl_reason})" if pfl_reason else "")
            ),
            "readiness_below_threshold": "profile not ready enough yet",
            "data_confidence_low": "live provider freshness is too weak for a deployable proof call",
            "relaxed_path_tightened": "relaxed path requires exact reinforcement + stronger proof structure",
            "recent_loss_lock": "recent loss lock active for this mint",
            "runner_safety_review": "established runner safety still needs review",
            "runner_quality_below_policy": (
                f"established runner market_quality={float(sig.get('market_quality_score') or 0.0):.1f} below policy"
            ),
            "runner_momentum_unconfirmed": (
                f"established runner needs renewed momentum "
                f"(buy_pressure={bp:.1f}%, vol_acceleration={vacc:.1f})"
            ),
            "runner_lifecycle_unconfirmed": "established runner needs a cleaner entry window or first-leg confirmation",
            "runner_extension_risk": "established runner is extended or in protect-profit mode; avoid chasing",
            "runner_manual_review_required": "established runner is ready, but manual review is required before deployment",
        }.get(blocker, blocker.replace("_", " "))

    candidates: list[dict] = []
    blocker_counts: Counter = Counter()
    remaining_blocker_counts: Counter = Counter()
    proof_ready_count = 0

    signals = sorted(
        list(get_cached_signals() or []),
        key=lambda s: float(s.get("score") or 0.0),
        reverse=True,
    )
    proof_input_source = "LIVE_CACHE"
    if not signals:
        signals = _warm_cache_signals(limit=max(int(limit), 1), max_age_hours=6)
        if signals:
            proof_input_source = "WARM_CACHE_FALLBACK"
            log.info("[PROOF] using recent non-empty cache fallback (%d rows)", len(signals))
    if not signals:
        fallback_statuses = ("PENDING", "COMPLETE") if include_recent_complete else ("PENDING",)
        signals = sorted(
            _recent_scanner_signals(
                statuses=fallback_statuses,
                limit=max(int(limit), 1),
                max_age_hours=72 if include_recent_complete else 24,
            ),
            key=lambda s: float(s.get("score") or 0.0),
            reverse=True,
        )
        if signals:
            proof_input_source = (
                "RECENT_SCANNER_FALLBACK"
                if include_recent_complete else
                "RECENT_PENDING_FALLBACK"
            )
            log.info("[PROOF] using recent scanner fallback (%d rows)", len(signals))
    elif include_recent_complete:
        recent_signals = _recent_scanner_signals(
            statuses=("PENDING", "COMPLETE"),
            limit=max(int(limit) * 3, 30),
            max_age_hours=72,
        )
        if recent_signals:
            signals = _merge_signal_lists(
                signals,
                recent_signals,
                limit=max(int(limit) * 3, 30),
            )
            proof_input_source = f"{proof_input_source}_AUGMENTED"
            log.info("[PROOF] augmented %s with recent scanner review rows (%d total)", proof_input_source, len(signals))

    runner_signals = _runner_heartbeat_signals(
        max_age_minutes=float(os.getenv("RUNNER_HEARTBEAT_PROOF_MAX_AGE_MINUTES", "20"))
    )
    if runner_signals:
        signals = _merge_signal_lists(
            signals,
            runner_signals,
            limit=max(int(limit) * 3, 30),
        )
        proof_input_source = f"{proof_input_source}+RUNNER_HEARTBEAT"
        log.info("[PROOF] merged runner heartbeat signals (%d total)", len(signals))

    wallet_reinforcement_by_mint: dict[str, dict] = {}
    try:
        from utils.wallet_reinforcement import build_wallet_reinforcement_map

        wallet_reinforcement_by_mint = build_wallet_reinforcement_map(
            signals[: max(min(int(limit), 12), 1)],
            max_snapshot_age_minutes=20,
            trade_limit=35,
            lookback_days=21,
        ) or {}
    except Exception as exc:
        log.debug("[PROOF] wallet reinforcement unavailable: %s", exc)

    established_runner_by_mint: dict[str, dict] = {}
    try:
        from utils.conviction_recovery import established_runner_profile_map  # type: ignore

        established_runner_by_mint = established_runner_profile_map() or {}
    except Exception as exc:
        log.debug("[PROOF] established runner profile unavailable: %s", exc)

    token_stats_by_mint: dict[str, dict] = {}
    try:
        from utils.db import get_latest_memecoin_token_stats_for_mints

        token_stats_by_mint = get_latest_memecoin_token_stats_for_mints(
            [str(s.get("mint") or "") for s in signals[: max(min(int(limit), 20), 1)]],
            max_age_minutes=20,
        ) or {}
    except Exception as exc:
        log.debug("[PROOF] token stats unavailable: %s", exc)

    for sig in signals[: max(int(limit), 1)]:
        mint = str(sig.get("mint") or "")
        symbol = str(sig.get("symbol") or "UNKNOWN")
        scanner_score = float(sig.get("score") or 0.0)
        rug = str(sig.get("rug_label") or "UNKNOWN").upper()
        bp = float(sig.get("buy_pressure") or 0.0)
        revoked = bool(sig.get("mint_revoked", False))
        freeze_revoked = bool(sig.get("freeze_revoked", False))
        vacc = float(sig.get("vol_acceleration") or 0.0)
        holder_pct = float(sig.get("top_holder_pct") or 0.0)
        top5_holder_pct = float(sig.get("top5_holder_pct") or 0.0)
        lp_locked_pct = float(sig.get("lp_locked_pct") or 0.0)
        holder_quality_score = float(sig.get("holder_quality_score") or 0.0)
        holder_quality_level = str(sig.get("holder_quality_level") or "").upper()
        lifecycle = lifecycle_map.get(mint)
        ew = str((lifecycle or {}).get("entry_window") or "")
        fq = str((lifecycle or {}).get("fuel_quality") or "")
        mp = str((lifecycle or {}).get("move_phase") or "")
        flc = int((lifecycle or {}).get("first_leg_confirmed") or 0)
        provisional_first_leg = int((lifecycle or {}).get("provisional_first_leg") or 0)
        provisional_reason = str((lifecycle or {}).get("provisional_reason") or "")
        provisional_score = float((lifecycle or {}).get("provisional_score") or 0.0)
        support = support_by_mint.get(mint, {})
        raw_support_score = float(support.get("support_score") or 0.0)
        support_tags = list(support.get("support_tags") or [])
        support_details = list(support.get("support_details") or [])
        has_exact_support = any(tag in ("whale_overlap", "confluence_overlap") for tag in support_tags)
        timing_score = float(sig.get("timing_score") or 0.0)
        safety_score = float(sig.get("safety_score") or 0.0)
        market_quality_score = float(sig.get("market_quality_score") or 0.0)
        scanner_rank_score = float(sig.get("scanner_rank_score") or scanner_score or 0.0)
        if timing_score <= 0.0 and safety_score <= 0.0 and market_quality_score <= 0.0 and scanner_score > 0.0:
            legacy_dims = _legacy_score_dimension_fallback(
                scanner_score=scanner_rank_score or scanner_score,
                rug_label=rug,
                holder_quality_score=holder_quality_score,
                buy_pressure=bp,
                vol_acceleration=vacc,
                top_holder_pct=holder_pct,
            )
            timing_score = float(legacy_dims["timing_score"])
            safety_score = float(legacy_dims["safety_score"])
            market_quality_score = float(legacy_dims["market_quality_score"])
        market_quality_profile = _market_quality_profile(sig, market_quality_score)
        market_quality_verdict = str(market_quality_profile.get("market_quality_verdict") or "UNSTABLE").upper()
        market_quality_reasons = list(market_quality_profile.get("market_quality_reasons") or [])
        live_stats_profile = _merge_live_token_stats_profile(
            sig=sig,
            token_stats=token_stats_by_mint.get(mint),
            timing_score=timing_score,
            market_quality_score=market_quality_score,
            market_quality_verdict=market_quality_verdict,
            market_quality_reasons=market_quality_reasons,
        )
        timing_score = float(live_stats_profile.get("timing_score") or timing_score)
        market_quality_score = float(live_stats_profile.get("market_quality_score") or market_quality_score)
        market_quality_verdict = str(live_stats_profile.get("market_quality_verdict") or market_quality_verdict).upper()
        market_quality_reasons = list(live_stats_profile.get("market_quality_reasons") or market_quality_reasons)
        live_token_stats = dict(live_stats_profile.get("live_token_stats") or {})
        if float(data_freshness.get("token_stats_multiplier") or 1.0) < 1.0:
            token_stats_multiplier = float(data_freshness.get("token_stats_multiplier") or 1.0)
            timing_score *= token_stats_multiplier
            market_quality_score *= token_stats_multiplier
            market_quality_reasons.append("live token telemetry stale; timing and quality tapered")
        wallet_support = dict(wallet_reinforcement_by_mint.get(mint) or {})
        established_runner_profile = dict(established_runner_by_mint.get(mint) or {})
        if established_runner_profile:
            profile_label = str(established_runner_profile.get("profile") or "").strip()
            if profile_label and f"established_profile:{profile_label}" not in support_tags:
                support_tags.append(f"established_profile:{profile_label}")
                support_details.append(
                    str(established_runner_profile.get("thesis") or "Known runner/recovery profile is on the system watchlist.")
                )
        wallet_level = str(wallet_support.get("wallet_support_level") or "NONE").upper()
        wallet_overlap_score = float(wallet_support.get("wallet_overlap_score") or 0.0)
        wallet_confidence = float(wallet_support.get("wallet_confidence") or 0.0)
        wallet_unique_wallets = int(wallet_support.get("unique_wallets") or 0)
        wallet_repeat_wallets = int(wallet_support.get("repeat_wallets") or 0)
        wallet_high_quality_wallets = int(wallet_support.get("high_quality_wallets") or 0)
        wallet_recent_activity = bool(wallet_support.get("recent_wallet_activity"))
        wallet_reason_texts = [str(r or "").strip() for r in list(wallet_support.get("reasons") or []) if str(r or "").strip()]
        if float(data_freshness.get("wallet_multiplier") or 1.0) < 1.0:
            wallet_multiplier = float(data_freshness.get("wallet_multiplier") or 1.0)
            wallet_overlap_score *= wallet_multiplier
            wallet_confidence *= wallet_multiplier
            if wallet_level == "STRONG":
                wallet_level = "MODERATE"
            elif wallet_level == "MODERATE" and wallet_multiplier <= 0.5:
                wallet_level = "LIGHT"
            wallet_reason_texts.append("wallet signal tapered because the live wallet stream is stale")
        wallet_support_bonus = {"LIGHT": 2.0, "MODERATE": 5.0, "STRONG": 8.0}.get(wallet_level, 0.0)
        if wallet_confidence < 35.0:
            wallet_support_bonus = min(wallet_support_bonus, 2.0)
        wallet_tags: list[str] = []
        wallet_details: list[str] = []
        if wallet_level in ("LIGHT", "MODERATE", "STRONG"):
            wallet_tags.append("wallet_backed")
            wallet_details.append(
                f"wallet reinforcement {wallet_level.lower()} ({wallet_unique_wallets} recent wallet{'s' if wallet_unique_wallets != 1 else ''})"
            )
        if wallet_repeat_wallets > 0:
            wallet_tags.append("wallet_repeat_overlap")
            wallet_details.append(
                f"{wallet_repeat_wallets} wallet{'s' if wallet_repeat_wallets != 1 else ''} showed repeat flow"
            )
        if wallet_high_quality_wallets > 0:
            wallet_tags.append("wallet_high_quality_support")
            wallet_details.append(
                f"{wallet_high_quality_wallets} higher-quality wallet{'s' if wallet_high_quality_wallets != 1 else ''} supported the move"
            )
        if wallet_recent_activity:
            wallet_tags.append("wallet_recent_activity")
            wallet_details.append("wallet activity is still fresh")
        for tag, detail in zip(wallet_tags, wallet_details):
            if tag not in support_tags:
                support_tags.append(tag)
                support_details.append(detail)
        support_profile = _compute_support_score(
            raw_support_score=raw_support_score,
            support_tags=support_tags,
            whale_ready=int(support.get("whale_ready") or 0),
            arkham_meaningful=int(support.get("arkham_meaningful") or 0),
            confluence_events=int(support.get("confluence_events") or 0),
            symbol_family_support=int(support.get("symbol_family_support") or 0),
            wallet_overlap_score=wallet_overlap_score,
            wallet_support_level=wallet_level,
            wallet_confidence=wallet_confidence,
            wallet_repeat_wallets=wallet_repeat_wallets,
            wallet_high_quality_wallets=wallet_high_quality_wallets,
            wallet_recent_activity=wallet_recent_activity,
            large_trade_buy_count=int(support.get("large_trade_buy_count") or 0),
            large_trade_sell_count=int(support.get("large_trade_sell_count") or 0),
            large_trade_buy_volume_usd=float(support.get("large_trade_buy_volume_usd") or 0.0),
            large_trade_sell_volume_usd=float(support.get("large_trade_sell_volume_usd") or 0.0),
            large_trade_unique_wallets=int(support.get("large_trade_unique_wallets") or 0),
            large_trade_strong_labels=int(support.get("large_trade_strong_labels") or 0),
            wallet_behavior_state=str(support.get("wallet_behavior_state") or "MIXED"),
            wallet_conviction_score=float(support.get("wallet_conviction_score") or 0.0),
            wallet_cluster_score=float(support.get("wallet_cluster_score") or 0.0),
            wallet_behavior_quality=str(support.get("wallet_behavior_quality") or "NONE"),
        )
        support_score = float(support_profile["support_score"])
        support_level = str(support_profile["support_level"] or "NONE")
        recent_loss = bool(mint and mint in loss_info)
        loss_age_hours = None
        if recent_loss:
            try:
                raw = str(loss_info.get(mint) or "")
                if raw:
                    if raw.endswith("Z"):
                        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                    else:
                        dt = datetime.fromisoformat(raw)
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=timezone.utc)
                    loss_age_hours = (datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds() / 3600.0
            except Exception:
                loss_age_hours = None
        scanner_regime = str(sig.get("scanner_regime") or "NORMAL")
        relaxed_path_override = (
            scanner_regime == "RELAXED_NEAR_MISS"
            and rug == "GOOD"
            and revoked
            and bp >= 60.0
            and vacc >= max(float(thresholds["min_vol_acceleration"]), 5.0)
            and holder_pct <= float(thresholds["max_top_holder_pct"])
            and ew == "OPEN"
            and fq == "STRONG"
            and mp in ("RELOAD", "MID", "EARLY")
            and int(flc or 0) == 1
            and has_exact_support
        )
        score_ceiling = _score_ceiling_context(
            scanner_score=scanner_score,
            scanner_regime=scanner_regime,
            thresholds=thresholds,
            hard_max_score=hard_max_score,
            support_score=support_score,
            entry_window=ew,
            fuel_quality=fq,
            move_phase=mp,
            first_leg_confirmed=flc,
            provisional_first_leg=provisional_first_leg,
            rug_label=rug,
            mint_revoked=revoked,
            vol_acceleration=vacc,
            top_holder_pct=holder_pct,
            buy_pressure=bp,
            market_quality_score=market_quality_score,
            marketcap_usd=float(live_token_stats.get("marketcap") or sig.get("mcap_at_scan") or sig.get("mcap_usd") or 0.0),
        )
        score_ceiling_override = bool(score_ceiling["override"])
        runner_policy = _established_runner_policy_v2(
            profile=established_runner_profile,
            sig=sig,
            scanner_score=scanner_score,
            scanner_regime=scanner_regime,
            rug_label=rug,
            mint_revoked=revoked,
            buy_pressure=bp,
            vol_acceleration=vacc,
            holder_pct=holder_pct,
            max_holder_pct=float(thresholds["max_top_holder_pct"]),
            market_quality_score=market_quality_score,
            market_quality_verdict=market_quality_verdict,
            lifecycle=lifecycle,
            support_score=support_score,
            support_level=support_level,
            data_freshness=data_freshness,
        )
        runner_v2_enabled = bool(runner_policy.get("enabled"))
        active_min_score = (
            float(runner_policy.get("min_score") or thresholds["min_score"])
            if runner_v2_enabled
            else float(thresholds["min_score"])
        )
        sig_for_reason = {
            **sig,
            "dynamic_max_score": score_ceiling["dynamic_max_score"],
            "market_quality_score": market_quality_score,
            "market_quality_verdict": market_quality_verdict,
        }
        _bp_bypass = (
            bp >= 50.0
            and scanner_regime == "NORMAL"
            and ew == "OPEN"
            and fq == "STRONG"
            and int(flc or 0) == 1
            and vacc >= float(thresholds["min_vol_acceleration"])
            and holder_pct <= float(thresholds["max_top_holder_pct"])
        )
        rug_telemetry_missing = (
            rug == "UNKNOWN"
            and holder_pct <= 0.0
            and top5_holder_pct <= 0.0
            and lp_locked_pct <= 0.0
            and not revoked
            and not freeze_revoked
        )
        rug_warn_reviewable = (
            rug == "WARN"
            and holder_quality_level in ("CLEAN", "CAUTION")
            and holder_quality_score >= 75.0
            and holder_pct > 0.0
            and top5_holder_pct > 0.0
            and lp_locked_pct >= 80.0
            and revoked
            and freeze_revoked
            and market_quality_verdict in ("CLEAN", "WATCH")
            and market_quality_score >= 55.0
        )
        clean_warn_probe_ready = (
            rug == "WARN"
            and holder_quality_level == "CLEAN"
            and holder_quality_score >= 85.0
            and holder_pct > 0.0
            and top5_holder_pct > 0.0
            and lp_locked_pct >= 90.0
            and revoked
            and freeze_revoked
            and scanner_regime == "NORMAL"
            and bp >= 60.0
            and vacc >= float(thresholds["min_vol_acceleration"])
            and ew == "OPEN"
            and fq == "STRONG"
            and int(flc or 0) == 1
            and mp in ("RELOAD", "MID", "EARLY")
            and market_quality_verdict == "CLEAN"
            and market_quality_score >= 68.0
        )
        lifecycle_headwind_key = ""
        loss_override = (
            recent_loss
            and loss_age_hours is not None
            and loss_age_hours >= float(os.getenv("MEMECOIN_PROOF_LOSS_OVERRIDE_HOURS", "72"))
            and scanner_regime == "NORMAL"
            and scanner_score >= float(thresholds["min_score"])
            and ew == "OPEN"
            and fq == "STRONG"
            and mp in ("RELOAD", "MID", "EARLY")
            and int(flc or 0) == 1
            and rug == "GOOD"
            and revoked
            and vacc >= float(thresholds["min_vol_acceleration"])
            and holder_pct <= float(thresholds["max_top_holder_pct"])
        )

        remaining_blocker_keys: list[str] = []

        def _add_remaining_blocker(key: str) -> None:
            if key and key not in remaining_blocker_keys:
                remaining_blocker_keys.append(key)

        if not mint:
            _add_remaining_blocker("missing_mint")
        elif mint in (open_mints or set()):
            _add_remaining_blocker("already_open")
        if scanner_score < active_min_score:
            _add_remaining_blocker("score_below_floor")
        if scanner_score > hard_max_score:
            _add_remaining_blocker("score_above_hard_ceiling")
        if rug in ("DANGER", "RUGGED"):
            _add_remaining_blocker("rug_danger")
        elif clean_warn_probe_ready:
            pass
        elif rug == "WARN" and rug_warn_reviewable:
            _add_remaining_blocker("rug_warn_review")
        elif rug == "WARN":
            _add_remaining_blocker("rug_warning")
        elif rug == "UNKNOWN" and rug_telemetry_missing:
            _add_remaining_blocker("rug_data_missing")
        elif rug != "GOOD":
            _add_remaining_blocker("rug_not_good")
        if market_quality_verdict == "AVOID":
            _add_remaining_blocker("market_quality_avoid")
        elif market_quality_verdict == "UNSTABLE":
            _add_remaining_blocker("market_quality_unstable")
        elif market_quality_score < 45.0:
            _add_remaining_blocker("market_quality_low")
        elif runner_v2_enabled and not bool(runner_policy.get("market_quality_ok")):
            _add_remaining_blocker("runner_quality_below_policy")
        if runner_v2_enabled:
            if bool(runner_policy.get("extension_risk")):
                _add_remaining_blocker("runner_extension_risk")
            elif not bool(runner_policy.get("momentum_ok")):
                _add_remaining_blocker("runner_momentum_unconfirmed")
        elif bp < 55.0 and not _bp_bypass:
            _add_remaining_blocker("buy_pressure_low")
        if not revoked:
            _add_remaining_blocker("mint_not_revoked")
        if not runner_v2_enabled and vacc < float(thresholds["min_vol_acceleration"]):
            _add_remaining_blocker("vol_acceleration_low")
        if holder_pct > float(thresholds["max_top_holder_pct"]):
            _add_remaining_blocker("holder_concentration")
        if runner_v2_enabled:
            if not bool(runner_policy.get("lifecycle_ok")):
                _add_remaining_blocker("runner_lifecycle_unconfirmed")
        elif lifecycle is None:
            _add_remaining_blocker("lifecycle_missing")
        else:
            if not runner_v2_enabled:
                if ew == "CLOSED":
                    _add_remaining_blocker("entry_window_closed")
                elif ew != "OPEN":
                    _add_remaining_blocker("entry_window_not_open")
                if fq == "TRAP":
                    _add_remaining_blocker("fuel_trap")
                elif fq not in ("STRONG", "MODERATE"):
                    _add_remaining_blocker("fuel_not_actionable")
                if mp in ("EXTENDED", "CHURN"):
                    _add_remaining_blocker("move_phase_extended")
                if not flc and provisional_first_leg:
                    _add_remaining_blocker("provisional_first_leg_only")
                elif not flc:
                    _add_remaining_blocker("first_leg_unconfirmed")
        if recent_loss and not loss_override:
            _add_remaining_blocker("recent_loss_lock")
        if not runner_v2_enabled and scanner_regime == "RELAXED_NEAR_MISS" and not relaxed_path_override:
            _add_remaining_blocker("relaxed_path_tightened")
        if runner_v2_enabled and bool(runner_policy.get("manual_review_required")):
            _add_remaining_blocker("runner_manual_review_required")
        elif not runner_v2_enabled and scanner_score > float(thresholds["max_score"]) and not score_ceiling_override:
            _add_remaining_blocker("score_above_ceiling")

        blocker_key = ""
        proof_status = "REJECTED"
        if not mint:
            blocker_key = "missing_mint"
            proof_status = "REJECTED"
        elif mint in (open_mints or set()):
            blocker_key = "already_open"
            proof_status = "REJECTED"
        elif scanner_score < active_min_score:
            blocker_key = "score_below_floor"
            proof_status = "REJECTED"
        elif scanner_score > hard_max_score:
            blocker_key = "score_above_hard_ceiling"
            proof_status = "REJECTED"
        elif rug in ("DANGER", "RUGGED"):
            blocker_key = "rug_danger"
            proof_status = "REJECTED"
        elif clean_warn_probe_ready:
            proof_status = "PROOF_READY"
        elif rug == "WARN" and rug_warn_reviewable:
            blocker_key = "rug_warn_review"
            proof_status = "RESEARCH_ONLY"
        elif rug == "WARN":
            blocker_key = "rug_warning"
            proof_status = "REJECTED"
        elif rug == "UNKNOWN" and rug_telemetry_missing:
            blocker_key = "rug_data_missing"
            proof_status = "RESEARCH_ONLY"
        elif rug != "GOOD":
            blocker_key = "rug_not_good"
            proof_status = "REJECTED"
        elif market_quality_verdict == "AVOID":
            blocker_key = "market_quality_avoid"
            proof_status = "REJECTED"
        elif market_quality_verdict == "UNSTABLE":
            blocker_key = "market_quality_unstable"
            proof_status = "RESEARCH_ONLY"
        elif market_quality_score < 45.0:
            blocker_key = "market_quality_low"
            proof_status = "RESEARCH_ONLY"
        elif runner_v2_enabled and not bool(runner_policy.get("market_quality_ok")):
            blocker_key = "runner_quality_below_policy"
            proof_status = "RESEARCH_ONLY"
        elif runner_v2_enabled and not revoked:
            blocker_key = "mint_not_revoked"
            proof_status = "REJECTED"
        elif runner_v2_enabled and bool(runner_policy.get("extension_risk")):
            blocker_key = "runner_extension_risk"
            proof_status = "RESEARCH_ONLY"
        elif runner_v2_enabled and not bool(runner_policy.get("momentum_ok")):
            blocker_key = "runner_momentum_unconfirmed"
            proof_status = "RESEARCH_ONLY"
        elif not runner_v2_enabled and bp < 55.0 and not _bp_bypass:
            blocker_key = "buy_pressure_low"
            proof_status = "REJECTED"
        elif not revoked:
            blocker_key = "mint_not_revoked"
            proof_status = "REJECTED"
        elif not runner_v2_enabled and vacc < float(thresholds["min_vol_acceleration"]):
            blocker_key = "vol_acceleration_low"
            proof_status = "REJECTED"
        elif holder_pct > float(thresholds["max_top_holder_pct"]):
            blocker_key = "holder_concentration"
            proof_status = "REJECTED"
        elif runner_v2_enabled and not bool(runner_policy.get("lifecycle_ok")):
            blocker_key = "runner_lifecycle_unconfirmed"
            proof_status = "RESEARCH_ONLY"
        elif not runner_v2_enabled and lifecycle is None:
            blocker_key = "lifecycle_missing"
            proof_status = "RESEARCH_ONLY"
        elif not runner_v2_enabled and ew == "CLOSED":
            blocker_key = "entry_window_closed"
            proof_status = "REJECTED"
        elif not runner_v2_enabled and fq == "TRAP":
            blocker_key = "fuel_trap"
            proof_status = "REJECTED"
        elif recent_loss:
            # Allow a narrow cooldown override after enough time has passed
            # and the candidate is otherwise strong.
            if loss_override:
                recent_loss = False
            else:
                blocker_key = "recent_loss_lock"
                proof_status = "RESEARCH_ONLY"
        elif not runner_v2_enabled and scanner_regime == "RELAXED_NEAR_MISS" and not relaxed_path_override:
            blocker_key = "relaxed_path_tightened"
            proof_status = "RESEARCH_ONLY"
        elif runner_v2_enabled and bool(runner_policy.get("manual_review_required")):
            blocker_key = "runner_manual_review_required"
            proof_status = "RESEARCH_ONLY"
        elif not runner_v2_enabled and scanner_score > float(thresholds["max_score"]) and not score_ceiling_override:
            blocker_key = "score_above_ceiling"
            proof_status = "REJECTED"
        else:
            proof_status = "PROOF_READY"

        if not blocker_key:
            if runner_v2_enabled:
                if bool(runner_policy.get("extension_risk")):
                    lifecycle_headwind_key = "runner_extension_risk"
                elif not bool(runner_policy.get("momentum_ok")):
                    lifecycle_headwind_key = "runner_momentum_unconfirmed"
                elif not bool(runner_policy.get("lifecycle_ok")):
                    lifecycle_headwind_key = "runner_lifecycle_unconfirmed"
            elif mp in ("EXTENDED", "CHURN"):
                lifecycle_headwind_key = "move_phase_extended"
            elif ew != "OPEN":
                lifecycle_headwind_key = "entry_window_not_open"
            elif fq not in ("STRONG", "MODERATE"):
                lifecycle_headwind_key = "fuel_not_actionable"
            elif not flc and provisional_first_leg:
                lifecycle_headwind_key = "provisional_first_leg_only"
            elif not flc:
                lifecycle_headwind_key = "first_leg_unconfirmed"

        base_proof_score = scanner_score
        base_proof_score += {"STRONG": 8.0, "MODERATE": 4.0}.get(str(fq or "").upper(), 0.0)
        base_proof_score += {"OPEN": 6.0, "CLOSING": 1.5}.get(str(ew or "").upper(), 0.0)
        base_proof_score += {"RELOAD": 6.0, "MID": 4.0, "EARLY": 4.0, "IGNITION": 2.0}.get(str(mp or "").upper(), 0.0)
        if int(flc or 0) == 1:
            base_proof_score += 5.0
        if clean_warn_probe_ready:
            base_proof_score -= 4.0
        data_penalty = float(data_freshness.get("penalty_points") or 0.0)
        proof_score = round(max(0.0, base_proof_score + support_score * 0.25 - data_penalty), 1)
        profit_room_profile = _compute_profit_room_profile(
            sig=sig,
            lifecycle=lifecycle,
            support=support,
            live_token_stats=live_token_stats,
            market_quality_score=market_quality_score,
        )
        profit_room_score = float(profit_room_profile.get("profit_room_score") or 0.0)
        profit_room_label = str(profit_room_profile.get("profit_room_label") or "WORKABLE").upper()
        profit_room_reasons = list(profit_room_profile.get("profit_room_reasons") or [])
        readiness_profile = _compute_readiness_score(
            timing_score=timing_score,
            safety_score=safety_score,
            market_quality_score=market_quality_score,
            market_quality_verdict=market_quality_verdict,
            support_score=support_score,
            profit_room_score=profit_room_score,
            profit_room_label=profit_room_label,
            proof_status=proof_status,
            blocker_key=blocker_key,
            entry_window=ew,
            fuel_quality=fq,
            move_phase=mp,
            first_leg_confirmed=flc,
            provisional_first_leg=provisional_first_leg,
            scanner_regime=scanner_regime,
            score_ceiling_override=score_ceiling_override,
            clean_warn_probe_ready=clean_warn_probe_ready,
            recent_loss=recent_loss,
        )
        readiness_score = float(readiness_profile.get("readiness_score") or 0.0)
        if data_penalty > 0.0:
            readiness_score = max(0.0, readiness_score - data_penalty)
            readiness_profile["readiness_score"] = round(readiness_score, 1)
            reasons = list(readiness_profile.get("readiness_reasons") or [])
            if data_freshness.get("issues"):
                reasons.append(f"data freshness penalty -{data_penalty:.0f}")
            readiness_profile["readiness_reasons"] = reasons
            readiness_profile["modifier_points"] = float(readiness_profile.get("modifier_points") or 0.0) - data_penalty
        if proof_status == "PROOF_READY" and str(data_freshness.get("status") or "HIGH") == "LOW":
            _add_remaining_blocker("data_confidence_low")
            blocker_key = "data_confidence_low"
            proof_status = "RESEARCH_ONLY"
        readiness_min = (
            float(os.getenv("ESTABLISHED_RUNNER_READINESS_MIN", "68"))
            if runner_v2_enabled
            else 75.0
        )
        if proof_status == "PROOF_READY" and readiness_score < readiness_min:
            _add_remaining_blocker(lifecycle_headwind_key or "readiness_below_threshold")
            blocker_key = lifecycle_headwind_key or "readiness_below_threshold"
            proof_status = "RESEARCH_ONLY"

        remaining_blockers = [
            {
                "key": key,
                "reason": _reason_from_blocker(key, sig_for_reason, lifecycle),
                "primary": key == blocker_key,
            }
            for key in remaining_blocker_keys
        ]

        proof_reason = (
            "scanner quality + open lifecycle + confirmed first leg"
            if proof_status == "PROOF_READY"
            else _reason_from_blocker(blocker_key, sig_for_reason, lifecycle)
        )
        if proof_status == "PROOF_READY" and clean_warn_probe_ready:
            proof_reason = f"{proof_reason} + clean WARN review path"
        if proof_status == "PROOF_READY" and score_ceiling_override:
            proof_reason = f"{proof_reason} + normal-score extension"
        if proof_status == "PROOF_READY" and support_tags:
            proof_reason = f"{proof_reason} + {'/'.join(support_tags)}"

        proof_components = {
            "scanner": {
                "score": round(scanner_score, 1),
                "scanner_rank_score": round(scanner_rank_score, 1),
                "timing_score": round(timing_score, 1),
                "safety_score": round(safety_score, 1),
                "market_quality_score": round(market_quality_score, 1),
                "market_quality_verdict": market_quality_verdict,
                "market_quality_reasons": market_quality_reasons,
                "profit_room_score": round(profit_room_score, 1),
                "profit_room_label": profit_room_label,
                "profit_room_reasons": profit_room_reasons,
                "score_contract_version": int(sig.get("score_contract_version") or 0),
                "score_components": dict(sig.get("score_components") or {}),
                "scanner_regime": scanner_regime,
                "live_token_stats": live_token_stats or None,
                "min_score": float(thresholds["min_score"]),
                "max_score": float(thresholds["max_score"]),
                "dynamic_max_score": float(score_ceiling["dynamic_max_score"]),
                "score_ceiling_extension": float(score_ceiling["extension_points"]),
                "score_ceiling_reasons": list(score_ceiling["reasons"]),
                "hard_max_score": hard_max_score,
                "rug_label": rug,
                "top5_holder_pct": round(top5_holder_pct, 2),
                "lp_locked_pct": round(lp_locked_pct, 2),
                "buy_pressure": round(bp, 1),
                "mint_revoked": revoked,
                "freeze_revoked": freeze_revoked,
                "vol_acceleration": round(vacc, 2),
                "min_vol_acceleration": float(thresholds["min_vol_acceleration"]),
                "top_holder_pct": round(holder_pct, 2),
                "max_top_holder_pct": float(thresholds["max_top_holder_pct"]),
                "holder_quality_score": round(holder_quality_score, 1),
                "holder_quality_level": holder_quality_level or None,
                "rug_telemetry_missing": rug_telemetry_missing,
                "clean_warn_probe_ready": clean_warn_probe_ready,
                "rug_warn_reviewable": rug_warn_reviewable,
                "data_freshness": data_freshness,
            },
            "lifecycle": {
                "present": lifecycle is not None,
                "entry_window": ew,
                "fuel_quality": fq,
                "move_phase": mp,
                "first_leg_confirmed": int(flc or 0),
                "provisional_first_leg": provisional_first_leg,
                "provisional_reason": provisional_reason or None,
                "provisional_score": round(provisional_score, 1),
            },
            "memory": {
                "recent_loss_lock": recent_loss,
                "recent_loss_age_hours": round(float(loss_age_hours), 1) if loss_age_hours is not None else None,
                "already_open": mint in (open_mints or set()),
            },
            "reinforcement": {
                "support_score": round(support_score, 1),
                "support_level": support_level,
                "support_reasons": list(support_profile.get("support_reasons") or []),
                "wallet_support_score": round(float(support_profile.get("wallet_component") or 0.0), 1),
                "raw_overlap_score": round(float(support_profile.get("raw_overlap_score") or 0.0), 1),
                "support_tags": support_tags,
                "support_details": support_details,
                "whale_ready": int(support.get("whale_ready") or 0),
                "arkham_meaningful": int(support.get("arkham_meaningful") or 0),
                "confluence_events": int(support.get("confluence_events") or 0),
                "symbol_family_support": int(support.get("symbol_family_support") or 0),
                "wallet": {
                    "wallet_overlap_score": round(wallet_overlap_score, 1),
                    "wallet_confidence": round(wallet_confidence, 1),
                    "wallet_support_level": wallet_level,
                    "unique_wallets": wallet_unique_wallets,
                    "repeat_wallets": wallet_repeat_wallets,
                    "high_quality_wallets": wallet_high_quality_wallets,
                    "recent_wallet_activity": wallet_recent_activity,
                    "reasons": wallet_reason_texts,
                },
                "wallet_behavior": {
                    "wallet_behavior_state": str(support.get("wallet_behavior_state") or "MIXED"),
                    "wallet_conviction_score": round(float(support.get("wallet_conviction_score") or 0.0), 1),
                    "wallet_cluster_score": round(float(support.get("wallet_cluster_score") or 0.0), 1),
                    "smart_money_quality": str(support.get("wallet_behavior_quality") or "NONE"),
                },
            },
            "readiness": {
                "readiness_score": round(float(readiness_profile.get("readiness_score") or 0.0), 1),
                "readiness_level": readiness_profile.get("readiness_level"),
                "readiness_reasons": list(readiness_profile.get("readiness_reasons") or []),
                "base_score": round(float(readiness_profile.get("base_score") or 0.0), 1),
                "modifier_points": round(float(readiness_profile.get("modifier_points") or 0.0), 1),
                "lifecycle_headwind_key": lifecycle_headwind_key or None,
                "profit_room_score": round(profit_room_score, 1),
                "profit_room_label": profit_room_label,
                "profit_room_reasons": profit_room_reasons,
            },
            "established_runner": established_runner_profile or None,
            "established_runner_policy": runner_policy if runner_v2_enabled else None,
            "proof_status": proof_status,
            "blocker_key": blocker_key or None,
            "remaining_blocker_keys": remaining_blocker_keys,
            "remaining_blockers": remaining_blockers,
            "remaining_blocker_count": len(remaining_blocker_keys),
            "true_last_blocker": len(remaining_blocker_keys) == 1,
            "score_ceiling_override": score_ceiling_override,
        }

        item = {
            "symbol": symbol,
            "mint": mint,
            "source": "SCANNER",
            "score": round(scanner_score, 1),
            "scanner_rank_score": round(scanner_rank_score, 1),
            "timing_score": round(timing_score, 1),
            "safety_score": round(safety_score, 1),
            "market_quality_score": round(market_quality_score, 1),
            "market_quality_verdict": market_quality_verdict,
            "market_quality_reasons": market_quality_reasons,
            "profit_room_score": round(profit_room_score, 1),
            "profit_room_label": profit_room_label,
            "profit_room_reasons": profit_room_reasons,
            "live_token_stats": live_token_stats or None,
            "data_source": sig.get("candidate_data_source") or sig.get("source") or "scanner",
            "data_freshness_state": (
                "STALE_PENALIZED"
                if str(data_freshness.get("status") or "").upper() == "LOW"
                else (sig.get("candidate_freshness_state") or "LIVE")
            ),
            "data_freshness_issues": list(data_freshness.get("issues") or []),
            "proof_score": proof_score,
            "readiness_score": round(float(readiness_profile.get("readiness_score") or 0.0), 1),
            "readiness_level": readiness_profile.get("readiness_level"),
            "proof_status": proof_status,
            "proof_candidate": 1 if proof_status == "PROOF_READY" else 0,
            "proof_reason": proof_reason,
            "proof_components": proof_components,
            "entry_window": ew or None,
            "fuel_quality": fq or None,
            "move_phase": mp or None,
            "first_leg_confirmed": int(flc or 0),
            "provisional_first_leg": provisional_first_leg,
            "provisional_reason": provisional_reason or None,
            "provisional_score": round(provisional_score, 1),
            "support_score": round(support_score, 1),
            "support_level": support_level,
            "support_overlap_score": round(raw_support_score, 1),
            "support_overlap_tags": support_tags,
            "wallet_reinforcement_level": wallet_level,
            "wallet_overlap_score": round(wallet_overlap_score, 1),
            "wallet_confidence": round(wallet_confidence, 1),
            "wallet_unique_wallets": wallet_unique_wallets,
            "wallet_repeat_wallets": wallet_repeat_wallets,
            "wallet_high_quality_wallets": wallet_high_quality_wallets,
            "wallet_recent_activity": wallet_recent_activity,
            "wallet_reasons": wallet_reason_texts,
            "established_runner_profile": established_runner_profile or None,
            "established_runner_policy": runner_policy if runner_v2_enabled else None,
            "runner_policy_state": runner_policy.get("state") if runner_v2_enabled else None,
            "data_source": sig.get("candidate_data_source") or sig.get("source") or "scanner",
            "data_freshness_state": (
                "STALE_PENALIZED"
                if str(data_freshness.get("status") or "").upper() == "LOW"
                else (sig.get("candidate_freshness_state") or "LIVE")
            ),
            "data_freshness_issues": list(data_freshness.get("issues") or []),
            "wallet_behavior_state": str(support.get("wallet_behavior_state") or "MIXED"),
            "wallet_conviction_score": round(float(support.get("wallet_conviction_score") or 0.0), 1),
            "wallet_cluster_score": round(float(support.get("wallet_cluster_score") or 0.0), 1),
            "smart_money_quality": str(support.get("wallet_behavior_quality") or "NONE"),
            "blocker_key": blocker_key or None,
            "remaining_blocker_keys": remaining_blocker_keys,
            "remaining_blockers": remaining_blockers,
            "remaining_blocker_count": len(remaining_blocker_keys),
            "true_last_blocker": len(remaining_blocker_keys) == 1,
        }
        candidates.append(item)
        if blocker_key:
            blocker_counts[blocker_key] += 1
        for key in remaining_blocker_keys:
            remaining_blocker_counts[key] += 1
        if proof_status == "PROOF_READY":
            proof_ready_count += 1

    status_rank = {"PROOF_READY": 0, "RESEARCH_ONLY": 1, "REJECTED": 2}
    candidates.sort(
        key=lambda c: (
            status_rank.get(str(c.get("proof_status") or "REJECTED"), 3),
            -float(c.get("readiness_score") or 0.0),
            -float(c.get("proof_score") or 0.0),
            -float(c.get("score") or 0.0),
            str(c.get("symbol") or ""),
        )
    )

    payload = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "thresholds": thresholds,
        "proof_input_source": proof_input_source,
        "data_freshness": data_freshness,
        "proof_ready_count": proof_ready_count,
        "candidates": candidates,
        "blocker_counts": [
            {
                "key": key,
                "count": int(count),
                "reason": _reason_from_blocker(key, {}, None),
            }
            for key, count in blocker_counts.most_common()
        ],
        "remaining_blocker_counts": [
            {
                "key": key,
                "count": int(count),
                "reason": _reason_from_blocker(key, {}, None),
            }
            for key, count in remaining_blocker_counts.most_common()
        ],
    }
    _proof_snapshot_cache[cache_key] = (time.monotonic(), copy.deepcopy(payload))
    if len(_proof_snapshot_cache) > 12:
        oldest_key = min(_proof_snapshot_cache, key=lambda key: _proof_snapshot_cache[key][0])
        _proof_snapshot_cache.pop(oldest_key, None)
    return payload


def _emit_proof_stack_surfaces(snapshot: dict, max_items: int = 5) -> None:
    proof_ready = [
        c for c in list(snapshot.get("candidates") or [])
        if str(c.get("proof_status") or "") == "PROOF_READY"
    ][: max(int(max_items), 0)]
    if not proof_ready:
        return
    try:
        from utils.surface_log import surface_log_write  # type: ignore
        rows = []
        for cand in proof_ready:
            rows.append(
                {
                    "symbol": cand.get("symbol"),
                    "mint": cand.get("mint"),
                    "action": "PROOF_READY",
                    "move_type": cand.get("move_phase"),
                    "research_priority": "HIGH",
                    "fuel_quality": cand.get("fuel_quality"),
                    "entry_window": cand.get("entry_window"),
                    "move_phase": cand.get("move_phase"),
                    "score": cand.get("score"),
                    "first_leg_confirmed": cand.get("first_leg_confirmed"),
                    "support_overlap_score": cand.get("support_overlap_score"),
                    "support_overlap_tags": cand.get("support_overlap_tags"),
                    "proof_candidate": 1,
                    "proof_reason": cand.get("proof_reason"),
                    "proof_score": cand.get("proof_score"),
                    "proof_snapshot_json": json.dumps(cand.get("proof_components") or {}),
                }
            )
        surface_log_write(rows, "PROOF_STACK")
    except Exception as exc:
        log.debug("[PROOF] proof-stack surface write skipped: %s", exc)


def _watch_to_entry_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value if value is not None else default)
    except Exception:
        return default


def _watch_to_entry_quality(candidate: dict) -> bool:
    proof_components = dict(candidate.get("proof_components") or {})
    scanner = dict(proof_components.get("scanner") or {})
    established_profile = dict(
        candidate.get("established_runner_profile")
        or proof_components.get("established_runner")
        or {}
    )
    runner_policy = dict(
        candidate.get("established_runner_policy")
        or proof_components.get("established_runner_policy")
        or {}
    )
    is_established_runner = bool(established_profile.get("is_established_runner"))
    min_market_quality = float(os.getenv("WATCH_TO_ENTRY_MIN_MARKET_QUALITY", "80"))
    min_score = float(os.getenv("WATCH_TO_ENTRY_MIN_SCORE", "65"))
    if is_established_runner:
        min_market_quality = float(os.getenv("WATCH_TO_ENTRY_ESTABLISHED_MIN_MARKET_QUALITY", "70"))
        min_score = float(os.getenv("WATCH_TO_ENTRY_ESTABLISHED_MIN_SCORE", str(min_score)))
    score = _watch_to_entry_float(candidate.get("score") or scanner.get("score"))
    market_quality = _watch_to_entry_float(
        candidate.get("market_quality_score") or scanner.get("market_quality_score")
    )
    verdict = str(
        candidate.get("market_quality_verdict") or scanner.get("market_quality_verdict") or ""
    ).upper()
    rug = str(scanner.get("rug_label") or "").upper()
    revoked = bool(scanner.get("mint_revoked"))
    holder = _watch_to_entry_float(scanner.get("top_holder_pct"))
    max_holder = _watch_to_entry_float(scanner.get("max_top_holder_pct"), 35.0)
    if not str(candidate.get("mint") or "").strip():
        return False
    if score < min_score or market_quality < min_market_quality:
        return False
    if rug != "GOOD":
        return False
    if verdict and verdict not in {"CLEAN", "WATCH"}:
        return False
    if not revoked:
        return False
    return holder <= max_holder


def _watch_to_entry_row(candidate: dict) -> dict:
    proof_components = dict(candidate.get("proof_components") or {})
    scanner = dict(proof_components.get("scanner") or {})
    lifecycle = dict(proof_components.get("lifecycle") or {})
    readiness = dict(proof_components.get("readiness") or {})
    established_profile = dict(
        candidate.get("established_runner_profile")
        or proof_components.get("established_runner")
        or {}
    )
    runner_policy = dict(
        candidate.get("established_runner_policy")
        or proof_components.get("established_runner_policy")
        or {}
    )
    blocker = str(candidate.get("blocker_key") or proof_components.get("blocker_key") or "").strip()
    proof_status = str(candidate.get("proof_status") or "").upper()
    remaining_blockers = list(candidate.get("remaining_blockers") or proof_components.get("remaining_blockers") or [])
    remaining_keys = [
        str(item.get("key") if isinstance(item, dict) else item or "").strip()
        for item in list(candidate.get("remaining_blocker_keys") or proof_components.get("remaining_blocker_keys") or [])
    ]
    if not remaining_keys:
        remaining_keys = [
            str(item.get("key") or "").strip()
            for item in remaining_blockers
            if isinstance(item, dict) and str(item.get("key") or "").strip()
        ]
    remaining_keys = [key for key in remaining_keys if key]
    return {
        "symbol": str(candidate.get("symbol") or "UNKNOWN").upper(),
        "mint": str(candidate.get("mint") or "").strip(),
        "proof_status": proof_status,
        "blocker_key": blocker or None,
        "remaining_blocker_keys": remaining_keys,
        "remaining_blockers": remaining_blockers,
        "remaining_blocker_count": len(remaining_keys),
        "true_last_blocker": len(remaining_keys) == 1,
        "established_runner_profile": established_profile or None,
        "established_runner_policy": runner_policy or None,
        "runner_policy_state": runner_policy.get("state") if runner_policy else None,
        "runner_manual_review_required": bool(runner_policy.get("manual_review_required")),
        "is_established_runner": bool(established_profile.get("is_established_runner")),
        "proof_reason": str(candidate.get("proof_reason") or ""),
        "score": round(_watch_to_entry_float(candidate.get("score") or scanner.get("score")), 1),
        "proof_score": round(_watch_to_entry_float(candidate.get("proof_score")), 1),
        "readiness_score": round(
            _watch_to_entry_float(candidate.get("readiness_score") or readiness.get("readiness_score")),
            1,
        ),
        "market_quality_score": round(
            _watch_to_entry_float(candidate.get("market_quality_score") or scanner.get("market_quality_score")),
            1,
        ),
        "buy_pressure": round(_watch_to_entry_float(scanner.get("buy_pressure")), 1),
        "vol_acceleration": round(_watch_to_entry_float(scanner.get("vol_acceleration")), 2),
        "min_vol_acceleration": round(_watch_to_entry_float(scanner.get("min_vol_acceleration")), 2),
        "top_holder_pct": round(_watch_to_entry_float(scanner.get("top_holder_pct")), 2),
        "entry_window": candidate.get("entry_window") or lifecycle.get("entry_window"),
        "fuel_quality": candidate.get("fuel_quality") or lifecycle.get("fuel_quality"),
        "move_phase": candidate.get("move_phase") or lifecycle.get("move_phase"),
        "first_leg_confirmed": int(candidate.get("first_leg_confirmed") or lifecycle.get("first_leg_confirmed") or 0),
    }


def _watch_to_entry_emit_surface(event: dict, candidate: dict) -> None:
    try:
        from utils.surface_log import surface_log_write  # type: ignore

        surface_log_write(
            [
                {
                    "symbol": event.get("symbol"),
                    "mint": event.get("mint"),
                    "action": "ENTRY_TRIGGER",
                    "move_type": candidate.get("move_phase"),
                    "research_priority": "HIGH",
                    "fuel_quality": candidate.get("fuel_quality"),
                    "entry_window": candidate.get("entry_window"),
                    "move_phase": candidate.get("move_phase"),
                    "score": candidate.get("score"),
                    "first_leg_confirmed": candidate.get("first_leg_confirmed"),
                    "support_overlap_score": candidate.get("support_overlap_score"),
                    "support_overlap_tags": candidate.get("support_overlap_tags"),
                    "proof_candidate": 1,
                    "proof_reason": event.get("proof_reason"),
                    "proof_score": event.get("proof_score"),
                    "proof_snapshot_json": json.dumps(candidate.get("proof_components") or {}),
                }
            ],
            "WATCH_TO_ENTRY",
        )
    except Exception as exc:
        log.debug("[WATCH_TO_ENTRY] surface write skipped: %s", exc)


def _watch_to_entry_send_alert(event: dict) -> None:
    try:
        import html
        from utils.telegram_alerts import send_telegram_sync  # type: ignore

        symbol = html.escape(str(event.get("symbol") or "UNKNOWN"))
        mint = html.escape(str(event.get("mint") or ""))
        reason = html.escape(str(event.get("proof_reason") or "Proof stack is entry-ready."))
        from_blocker = html.escape(str(event.get("from_blocker") or "watch"))
        body = (
            f"{symbol} is now PROOF_READY\n"
            f"CA: {mint}\n"
            f"cleared: {from_blocker}\n"
            f"score={event.get('score')} proof={event.get('proof_score')} readiness={event.get('readiness_score')}\n"
            f"vol_accel={event.get('vol_acceleration')} gate={event.get('min_vol_acceleration')}\n"
            f"bp={event.get('buy_pressure')} holder={event.get('top_holder_pct')}%\n"
            f"{reason}"
        )
        send_telegram_sync("Watch-to-Entry Trigger", body, "🟢")
    except Exception as exc:
        log.debug("[WATCH_TO_ENTRY] telegram send skipped: %s", exc)


def _watch_to_entry_trigger(
    snapshot: dict,
    *,
    send_alerts: bool = True,
    write_state: bool = True,
) -> dict:
    if not _env_bool("WATCH_TO_ENTRY_ENABLED", True):
        status = {
            "enabled": False,
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "tracking_count": 0,
            "tracked": [],
            "recent_events": _kv_json_get(_WATCH_TO_ENTRY_RECENT_KV, []),
        }
        if write_state:
            _kv_json_set(_WATCH_TO_ENTRY_STATUS_KV, status)
        return status

    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    tracked_blockers = _watch_to_entry_track_blockers()
    cooldown_s = int(os.getenv("WATCH_TO_ENTRY_ALERT_COOLDOWN_SECONDS", "1800"))
    alert_new_ready = _env_bool("WATCH_TO_ENTRY_ALERT_NEW_READY", False)
    previous = _kv_json_get(_WATCH_TO_ENTRY_STATE_KV, {})
    if not isinstance(previous, dict):
        previous = {}
    recent_events = _kv_json_get(_WATCH_TO_ENTRY_RECENT_KV, [])
    if not isinstance(recent_events, list):
        recent_events = []

    stale_cutoff = now - timedelta(hours=float(os.getenv("WATCH_TO_ENTRY_STATE_TTL_HOURS", "24")))
    next_state: dict[str, dict] = {}
    for mint, item in previous.items():
        if not isinstance(item, dict):
            continue
        updated_at = _parse_utcish_ts(item.get("updated_at"))
        if updated_at and updated_at >= stale_cutoff:
            next_state[str(mint)] = dict(item)

    events: list[dict] = []
    armed: list[dict] = []
    true_last_count = 0
    candidates = list(snapshot.get("candidates") or [])
    for candidate in candidates:
        if not _watch_to_entry_quality(dict(candidate or {})):
            continue
        row = _watch_to_entry_row(dict(candidate or {}))
        mint = str(row.get("mint") or "")
        if not mint:
            continue
        proof_status = str(row.get("proof_status") or "").upper()
        blocker = str(row.get("blocker_key") or "").strip()
        remaining_keys = [str(key or "").strip() for key in list(row.get("remaining_blocker_keys") or []) if str(key or "").strip()]
        true_last_blocker = bool(
            len(remaining_keys) == 1
            and remaining_keys[0] in tracked_blockers
            and blocker == remaining_keys[0]
        )
        row["true_last_blocker"] = true_last_blocker
        is_ready = proof_status == "PROOF_READY"
        is_watched = bool(blocker and blocker in tracked_blockers)
        if not is_ready and not is_watched:
            continue
        if true_last_blocker:
            true_last_count += 1

        prev = dict(previous.get(mint) or {})
        prev_status = str(prev.get("proof_status") or "").upper()
        prev_blocker = str(prev.get("blocker_key") or "").strip()
        prev_remaining_keys = [
            str(key or "").strip()
            for key in list(prev.get("remaining_blocker_keys") or [])
            if str(key or "").strip()
        ]
        prev_true_last = bool(
            prev.get("true_last_blocker")
            and len(prev_remaining_keys) == 1
            and prev_remaining_keys[0] in tracked_blockers
            and prev_blocker == prev_remaining_keys[0]
        )
        event_kind = ""
        if is_ready and prev_status != "PROOF_READY":
            if prev_true_last:
                event_kind = "LAST_BLOCKER_CLEARED"
            elif alert_new_ready and not prev:
                event_kind = "NEW_READY_NAME"
            elif alert_new_ready and prev_status:
                event_kind = "PROOF_READY_TRANSITION"

        merged = {
            **row,
            "updated_at": now_iso,
            "watched_at": prev.get("watched_at") or now_iso,
            "last_alert_at": prev.get("last_alert_at"),
            "last_event": prev.get("last_event"),
        }
        if is_watched:
            armed.append(merged)

        if event_kind:
            from utils.db import persistent_rate_limit_check  # type: ignore

            alert_key = f"watch_to_entry:{mint}:{event_kind}"
            suppressed = persistent_rate_limit_check(alert_key, cooldown_s) if send_alerts else False
            event = {
                **row,
                "event": event_kind,
                "from_status": prev_status or None,
                "from_blocker": prev_blocker or None,
                "from_remaining_blocker_keys": prev_remaining_keys,
                "to_status": proof_status,
                "ts_utc": now_iso,
                "alert_suppressed": bool(suppressed),
            }
            events.append(event)
            recent_events.insert(0, event)
            if not suppressed and send_alerts:
                _watch_to_entry_send_alert(event)
                _watch_to_entry_emit_surface(event, dict(candidate or {}))
                log.info(
                    "[WATCH_TO_ENTRY] %s ready — %s cleared (score=%.1f proof=%.1f readiness=%.1f)",
                    row.get("symbol"),
                    prev_blocker or event_kind,
                    float(row.get("score") or 0.0),
                    float(row.get("proof_score") or 0.0),
                    float(row.get("readiness_score") or 0.0),
                )
            merged["last_alert_at"] = now_iso if not suppressed else merged.get("last_alert_at")
            merged["last_event"] = event_kind

        next_state[mint] = merged

    recent_events = recent_events[:20]
    armed.sort(
        key=lambda item: (
            -float(item.get("readiness_score") or 0.0),
            -float(item.get("proof_score") or 0.0),
            -float(item.get("score") or 0.0),
        )
    )
    status = {
        "enabled": True,
        "checked_at": now_iso,
        "snapshot_generated_at": snapshot.get("generated_at"),
        "proof_input_source": snapshot.get("proof_input_source"),
        "tracked_blockers": sorted(tracked_blockers),
        "tracking_count": len(armed),
        "true_last_blocker_count": true_last_count,
        "tracked": armed[:10],
        "events_this_cycle": events,
        "recent_events": recent_events[:10],
        "alert_cooldown_seconds": cooldown_s,
        "detail": (
            "Watching high-quality names and alerting when a tracked final blocker clears into PROOF_READY."
            if armed or recent_events
            else "No high-quality final-blocker watch candidates in the latest proof snapshot."
        ),
    }
    if write_state:
        _kv_json_set(_WATCH_TO_ENTRY_STATE_KV, next_state)
        _kv_json_set(_WATCH_TO_ENTRY_RECENT_KV, recent_events)
        _kv_json_set(_WATCH_TO_ENTRY_STATUS_KV, status)
    return status


def memecoin_watch_to_entry_step(*, send_alerts: bool = True) -> dict:
    try:
        with get_conn() as conn:
            open_mints = {
                str(row[0])
                for row in conn.execute(
                    "SELECT mint FROM memecoin_trades WHERE status='OPEN'"
                ).fetchall()
                if row and row[0]
            }
    except Exception:
        open_mints = set()
    limit = int(os.getenv("WATCH_TO_ENTRY_SNAPSHOT_LIMIT", "50"))
    snapshot = get_proof_candidate_snapshot(limit=limit, open_mints=open_mints)
    return _watch_to_entry_trigger(snapshot, send_alerts=send_alerts)


def _pilot_proof_bootstrap_enabled() -> bool:
    return os.getenv("MEMECOIN_PILOT_PROOF_BOOTSTRAP", "false").lower() == "true"


def _select_pilot_bootstrap_candidate(
    snapshot: dict,
    *,
    threshold: float,
    vacc_min: float,
    holder_max: float,
    open_mints: set[str],
) -> dict | None:
    for cand in list(snapshot.get("candidates") or []):
        if str(cand.get("proof_status") or "").upper() != "PROOF_READY":
            continue
        mint = str(cand.get("mint") or "")
        if not mint or mint in open_mints:
            continue

        proof_components = dict(cand.get("proof_components") or {})
        scanner = dict(proof_components.get("scanner") or {})
        lifecycle = dict(proof_components.get("lifecycle") or {})
        blockers = {
            str(item.get("key") or "").strip()
            for item in list(cand.get("hard_blockers") or [])
            if str(item.get("key") or "").strip()
        }
        blockers.discard("deployment_blocked")
        blockers.discard("paper_mode")
        blockers.discard("proof_slots_full")
        blockers.discard("recommended_slots_full")
        if blockers:
            continue

        rug = str(scanner.get("rug_label") or "UNKNOWN").upper()
        clean_warn = bool(scanner.get("clean_warn_probe_ready"))
        if rug == "WARN" and not clean_warn:
            continue
        if rug not in ("GOOD", "WARN"):
            continue

        score = float(cand.get("score") or 0.0)
        readiness = float(cand.get("readiness_score") or 0.0)
        market_quality_score = float(scanner.get("market_quality_score") or cand.get("market_quality_score") or 0.0)
        market_quality_verdict = str(scanner.get("market_quality_verdict") or cand.get("market_quality_verdict") or "")
        bp = float(scanner.get("buy_pressure") or 0.0)
        vacc = float(scanner.get("vol_acceleration") or 0.0)
        holder_pct = float(scanner.get("top_holder_pct") or 0.0)
        entry_window = str(lifecycle.get("entry_window") or "")
        fuel_quality = str(lifecycle.get("fuel_quality") or "")
        move_phase = str(lifecycle.get("move_phase") or "")
        flc = int(lifecycle.get("first_leg_confirmed") or 0)
        revoked = bool(scanner.get("mint_revoked"))
        freeze_revoked = bool(scanner.get("freeze_revoked"))

        if score < max(threshold, 70.0):
            continue
        if readiness < 80.0:
            continue
        if market_quality_verdict != "CLEAN":
            continue
        if market_quality_score < 68.0:
            continue
        if bp < 60.0:
            continue
        if vacc < vacc_min:
            continue
        if holder_pct > holder_max:
            continue
        if not revoked or not freeze_revoked:
            continue
        if entry_window != "OPEN":
            continue
        if fuel_quality != "STRONG":
            continue
        if flc != 1:
            continue
        if move_phase not in ("RELOAD", "MID", "EARLY"):
            continue
        return cand
    return None


# ── Buy / Sell ────────────────────────────────────────────────────────────────

def buy_memecoin(                          # Patch 272: lifecycle context params
    mint: str,
    symbol: str,
    amount_usd: float,
    is_pilot: bool = False,
    entry_fuel_quality: "str | None" = None,
    entry_window: "str | None" = None,
    entry_move_phase: "str | None" = None,
    entry_score: "float | None" = None,
    is_proof_build: bool = False,
    proof_status: "str | None" = None,
    proof_reason: "str | None" = None,
    proof_score: "float | None" = None,
    proof_snapshot_json: "str | None" = None,
    entry_attribution: "dict | None" = None,
) -> dict:
    """
    Execute a spot buy via Jupiter swap.
    Logs the trade to memecoin_trades with status='OPEN'.

    MEMECOIN_DRY_RUN=true (default) → paper trade only, no real swap.
    is_pilot=True → trade is tagged as a pilot trade (is_pilot=1 in DB).

    Patch 272: entry_fuel_quality / entry_window / entry_move_phase / entry_score
    snapshot the lifecycle gate conditions at buy time for post-trade attribution.
    """
    import sys
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    if root not in sys.path:
        sys.path.insert(0, root)

    # ── Roadmap 3: authority bridge check (covers direct API / bot calls) ─────
    try:
        from authority import resolve_action  # type: ignore[import]
        _auth = resolve_action("new_entry", "memecoins", executor="memecoins_buy")
        if _auth["verdict"] == "BLOCK":
            logger.debug(
                "AUTHORITY_BLOCK: buy_memecoin %s skipped — %s",
                symbol, "; ".join(_auth["reasons"]),
            )
            return {"success": False, "error": "authority_block", "reasons": _auth["reasons"]}
    except Exception as _auth_exc:
        logger.debug("authority check skipped in buy_memecoin: %s", _auth_exc)
    # ── End authority bridge ──────────────────────────────────────────────────

    # ── PAPER MODE — intercept before any real swap (Patch 121) ──────────────
    if os.getenv("MEMECOIN_DRY_RUN", "true").lower() == "true":
        sim_price  = _fetch_token_price(mint)
        sim_tokens = round(amount_usd / sim_price, 4) if sim_price > 0 else 0.0
        ts_now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        base_entry_attr = dict(entry_attribution or {})
        if entry_window and not base_entry_attr.get("entry_window"):
            base_entry_attr["entry_window"] = entry_window
        if entry_move_phase and not base_entry_attr.get("move_phase"):
            base_entry_attr["move_phase"] = entry_move_phase
        entry_attr = _finalize_entry_latency(
            base_entry_attr,
            entry_price=sim_price,
            entry_fired_at=ts_now,
        )
        with get_conn() as conn:
            conn.execute("""
                INSERT INTO memecoin_trades
                    (opened_ts_utc, symbol, mint, entry_price, amount_usd, initial_amount_usd,
                     token_amount, status, tx_sig_open, is_pilot,
                     entry_fuel_quality, entry_window, entry_move_phase, entry_score,
                     is_proof_build, proof_status, proof_reason, proof_score, proof_snapshot_json,
                     entry_timing_score, entry_safety_score, entry_market_quality_score, entry_support_score,
                     entry_readiness_score, entry_profit_room_score, entry_profit_room_label,
                     entry_market_quality_verdict, entry_wallet_behavior_state, entry_smart_money_quality,
                     entry_regime_label, entry_route, entry_lane_authority,
                     scan_detected_at, proof_ready_at, entry_fired_at,
                     minutes_scan_to_proof_ready, minutes_proof_ready_to_entry, minutes_scan_to_entry,
                     scan_price, proof_ready_price, scan_mcap, proof_ready_mcap,
                     pct_move_scan_to_entry, pct_move_proof_ready_to_entry, entry_timing_bucket,
                     entry_window_position_pct, signal_window_phase, confirmation_wait_used, confirmation_wait_minutes,
                     entry_attribution_json)
                VALUES (?, ?, ?, ?, ?, ?, 'OPEN', 'PAPER', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (ts_now, symbol.upper(), mint, sim_price, amount_usd, amount_usd, sim_tokens,
                  1 if is_pilot else 0,
                  entry_fuel_quality, entry_window, entry_move_phase, entry_score,
                  1 if is_proof_build else 0, proof_status, proof_reason, proof_score, proof_snapshot_json,
                  entry_attr.get("timing_score"), entry_attr.get("safety_score"), entry_attr.get("market_quality_score"), entry_attr.get("support_score"),
                  entry_attr.get("readiness_score"), entry_attr.get("profit_room_score"), entry_attr.get("profit_room_label"),
                  entry_attr.get("market_quality_verdict"), entry_attr.get("wallet_behavior_state"), entry_attr.get("smart_money_quality"),
                  entry_attr.get("regime_label"), entry_attr.get("route"), entry_attr.get("lane_authority"),
                  entry_attr.get("scan_detected_at"), entry_attr.get("proof_ready_at"), entry_attr.get("entry_fired_at"),
                  entry_attr.get("minutes_scan_to_proof_ready"), entry_attr.get("minutes_proof_ready_to_entry"), entry_attr.get("minutes_scan_to_entry"),
                  entry_attr.get("scan_price"), entry_attr.get("proof_ready_price"), entry_attr.get("scan_mcap"), entry_attr.get("proof_ready_mcap"),
                  entry_attr.get("pct_move_scan_to_entry"), entry_attr.get("pct_move_proof_ready_to_entry"), entry_attr.get("entry_timing_bucket"),
                  entry_attr.get("entry_window_position_pct"), entry_attr.get("signal_window_phase"),
                  int(bool(entry_attr.get("confirmation_wait_used"))) if entry_attr.get("confirmation_wait_used") is not None else None,
                  entry_attr.get("confirmation_wait_minutes"),
                  json.dumps(entry_attr)))
            conn.execute("""
                UPDATE memecoin_signal_outcomes
                SET bought = 1,
                    is_proof_build = ?,
                    proof_reason = ?,
                    proof_score = ?,
                    proof_snapshot_json = ?
                WHERE mint = ? AND id = (
                    SELECT id FROM memecoin_signal_outcomes
                    WHERE mint = ? ORDER BY scanned_at DESC LIMIT 1
                )
            """, (1 if is_proof_build else 0, proof_reason, proof_score, proof_snapshot_json, mint, mint))
            trade_id = conn.execute(
                "SELECT id FROM memecoin_trades WHERE mint=? AND opened_ts_utc=? ORDER BY id DESC LIMIT 1",
                (mint, ts_now),
            ).fetchone()[0]
        record_capital_event(
            "memecoins",
            "DEPLOY",
            amount_usd,
            f"PAPER buy {symbol.upper()}",
            symbol=symbol,
            ref_table="memecoin_trades",
            ref_id=int(trade_id),
            dry_run=True,
            ts_utc=ts_now,
        )
        orchestrator.append_memory(
            "memecoin_scan",
            f"BUY [PAPER] {symbol.upper()} mint={mint[:8]}… ${amount_usd:.0f} "
            f"entry={sim_price:.8g}",
        )
        return {
            "success":      True,
            "tx_sig":       "PAPER",
            "entry_price":  sim_price,
            "token_amount": sim_tokens,
            "dry_run":      True,
        }
    # ── LIVE path — only reached when MEMECOIN_DRY_RUN=false ─────────────────

    sol_price = _fetch_sol_price()
    if sol_price <= 0:
        return {"success": False, "error": "Could not fetch SOL price"}

    try:
        from utils.jupiter_swap import execute_buy  # type: ignore
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(execute_buy(mint, amount_usd, sol_price))
        finally:
            loop.close()
    except Exception as exc:
        return {"success": False, "error": str(exc)}

    if not result:
        return {"success": False, "error": "execute_buy returned None"}

    entry_price   = float(result.get("filled_price") or result.get("entry_price") or 0)
    token_amount  = float(result.get("token_amount")  or result.get("out_amount_ui") or 0)
    tx_sig        = result.get("tx_sig", "")
    dry_run       = result.get("dry_run", False)

    ts_now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    base_entry_attr = dict(entry_attribution or {})
    if entry_window and not base_entry_attr.get("entry_window"):
        base_entry_attr["entry_window"] = entry_window
    if entry_move_phase and not base_entry_attr.get("move_phase"):
        base_entry_attr["move_phase"] = entry_move_phase
    entry_attr = _finalize_entry_latency(
        base_entry_attr,
        entry_price=entry_price,
        entry_fired_at=ts_now,
    )
    with get_conn() as conn:
        cur = conn.execute("""
            INSERT INTO memecoin_trades
                (opened_ts_utc, symbol, mint, entry_price, amount_usd, initial_amount_usd,
                 token_amount, status, tx_sig_open, is_pilot,
                 entry_fuel_quality, entry_window, entry_move_phase, entry_score,
                 is_proof_build, proof_status, proof_reason, proof_score, proof_snapshot_json,
                 entry_timing_score, entry_safety_score, entry_market_quality_score, entry_support_score,
                 entry_readiness_score, entry_profit_room_score, entry_profit_room_label,
                 entry_market_quality_verdict, entry_wallet_behavior_state, entry_smart_money_quality,
                 entry_regime_label, entry_route, entry_lane_authority,
                 scan_detected_at, proof_ready_at, entry_fired_at,
                 minutes_scan_to_proof_ready, minutes_proof_ready_to_entry, minutes_scan_to_entry,
                 scan_price, proof_ready_price, scan_mcap, proof_ready_mcap,
                 pct_move_scan_to_entry, pct_move_proof_ready_to_entry, entry_timing_bucket,
                 entry_window_position_pct, signal_window_phase, confirmation_wait_used, confirmation_wait_minutes,
                 entry_attribution_json)
            VALUES (?, ?, ?, ?, ?, ?, 'OPEN', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (ts_now, symbol.upper(), mint, entry_price, amount_usd, amount_usd, token_amount,
              tx_sig, 1 if is_pilot else 0,
              entry_fuel_quality, entry_window, entry_move_phase, entry_score,
              1 if is_proof_build else 0, proof_status, proof_reason, proof_score, proof_snapshot_json,
              entry_attr.get("timing_score"), entry_attr.get("safety_score"), entry_attr.get("market_quality_score"), entry_attr.get("support_score"),
              entry_attr.get("readiness_score"), entry_attr.get("profit_room_score"), entry_attr.get("profit_room_label"),
              entry_attr.get("market_quality_verdict"), entry_attr.get("wallet_behavior_state"), entry_attr.get("smart_money_quality"),
              entry_attr.get("regime_label"), entry_attr.get("route"), entry_attr.get("lane_authority"),
              entry_attr.get("scan_detected_at"), entry_attr.get("proof_ready_at"), entry_attr.get("entry_fired_at"),
              entry_attr.get("minutes_scan_to_proof_ready"), entry_attr.get("minutes_proof_ready_to_entry"), entry_attr.get("minutes_scan_to_entry"),
              entry_attr.get("scan_price"), entry_attr.get("proof_ready_price"), entry_attr.get("scan_mcap"), entry_attr.get("proof_ready_mcap"),
              entry_attr.get("pct_move_scan_to_entry"), entry_attr.get("pct_move_proof_ready_to_entry"), entry_attr.get("entry_timing_bucket"),
              entry_attr.get("entry_window_position_pct"), entry_attr.get("signal_window_phase"),
              int(bool(entry_attr.get("confirmation_wait_used"))) if entry_attr.get("confirmation_wait_used") is not None else None,
              entry_attr.get("confirmation_wait_minutes"),
              json.dumps(entry_attr)))
        trade_id = cur.lastrowid

        # Flag the most-recent outcome row for this mint as bought
        conn.execute("""
            UPDATE memecoin_signal_outcomes
            SET bought = 1,
                is_proof_build = ?,
                proof_reason = ?,
                proof_score = ?,
                proof_snapshot_json = ?
            WHERE mint = ? AND id = (
                SELECT id FROM memecoin_signal_outcomes
                WHERE mint = ? ORDER BY scanned_at DESC LIMIT 1
            )
        """, (1 if is_proof_build else 0, proof_reason, proof_score, proof_snapshot_json, mint, mint))
    record_capital_event(
        "memecoins",
        "DEPLOY",
        amount_usd,
        f"LIVE buy {symbol.upper()}",
        symbol=symbol,
        ref_table="memecoin_trades",
        ref_id=int(trade_id),
        dry_run=bool(dry_run),
        ts_utc=ts_now,
    )

    orchestrator.append_memory(
        "memecoin_scan",
        f"BUY {symbol.upper()} mint={mint[:8]}… ${amount_usd:.0f} "
        f"entry={entry_price:.8g} {'DRY' if dry_run else 'LIVE'}",
    )

    return {
        "success":      True,
        "tx_sig":       tx_sig,
        "entry_price":  entry_price,
        "token_amount": token_amount,
        "dry_run":      dry_run,
    }


def sell_memecoin(mint: str, reason: str = "MANUAL", pct: float = 100.0) -> dict:
    """
    Execute a spot sell via Jupiter swap.
    Supports full exits and partial de-risking.
    """
    import os, sys
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    if root not in sys.path:
        sys.path.insert(0, root)

    # ── Roadmap 3: authority observation (exits never blocked, just logged) ──
    try:
        from utils.authority import resolve_action  # type: ignore[import]
        resolve_action(
            "reduce_risk" if pct < 95.0 else "close_position",
            "memecoins",
            executor="memecoins_sell",
        )
    except Exception:
        pass

    pct = max(1.0, min(float(pct or 100.0), 100.0))

    with get_conn() as conn:
        row = conn.execute("""
            SELECT id, symbol, entry_price, amount_usd, token_amount,
                   initial_amount_usd, realized_release_usd, realized_pnl_usd,
                   opened_ts_utc, entry_regime_label, entry_attribution_json
            FROM memecoin_trades
            WHERE mint = ? AND status = 'OPEN'
            ORDER BY opened_ts_utc DESC LIMIT 1
        """, (mint,)).fetchone()

    if not row:
        return {"success": False, "error": "No open trade found for this mint"}

    trade_id     = row["id"]
    symbol       = row["symbol"]
    entry_price  = float(row["entry_price"] or 0)
    amount_usd   = float(row["amount_usd"]  or 0)
    token_amount = float(row["token_amount"] or 0)
    initial_amount_usd = float(row["initial_amount_usd"] or 0) if row["initial_amount_usd"] is not None else 0.0
    realized_release_usd = float(row["realized_release_usd"] or 0.0)
    realized_pnl_usd = float(row["realized_pnl_usd"] or 0.0)
    opened_ts_utc = str(row["opened_ts_utc"] or "") or None
    entry_regime_label = str(row["entry_regime_label"] or "") or None
    try:
        entry_attribution = json.loads(str(row["entry_attribution_json"] or "{}"))
        if not isinstance(entry_attribution, dict):
            entry_attribution = {}
    except Exception:
        entry_attribution = {}

    if token_amount <= 0:
        return {"success": False, "error": "Token amount is 0 — cannot sell"}
    if amount_usd <= 0:
        return {"success": False, "error": "Amount is 0 — cannot sell"}

    sell_fraction = pct / 100.0
    principal_to_sell = round(amount_usd * sell_fraction, 4)
    tokens_to_sell = round(token_amount * sell_fraction, 8)
    is_full_exit = pct >= 99.5 or principal_to_sell >= amount_usd - 1e-6

    # ── PAPER MODE — close DB trade without a real swap ──────────────────────
    if os.getenv("MEMECOIN_DRY_RUN", "true").lower() == "true":
        exit_price = _fetch_token_price(mint)
        pnl_pct = 0.0
        realized_pnl_delta = 0.0
        if entry_price > 0 and exit_price > 0:
            pnl_pct = round((exit_price - entry_price) / entry_price * 100, 2)
            realized_pnl_delta = round(principal_to_sell * pnl_pct / 100, 4)
        ts_now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        with get_conn() as conn:
            if is_full_exit:
                base_initial = initial_amount_usd or (amount_usd + realized_release_usd)
                total_realized_pnl = round(realized_pnl_usd + realized_pnl_delta, 4)
                total_realized_release = round(realized_release_usd + principal_to_sell, 4)
                overall_pnl_pct = round((total_realized_pnl / base_initial) * 100, 2) if base_initial > 0 else pnl_pct
                conn.execute("""
                    UPDATE memecoin_trades
                    SET status='CLOSED', exit_price=?, exit_reason=?,
                        pnl_pct=?, pnl_usd=?, realized_release_usd=?, realized_pnl_usd=?,
                        closed_ts_utc=?, tx_sig_close=?, amount_usd=0, token_amount=0
                    WHERE id=?
                """, (
                    exit_price, reason, overall_pnl_pct, total_realized_pnl,
                    total_realized_release, total_realized_pnl, ts_now, "PAPER", trade_id,
                ))
                try:
                    from utils.db import update_memecoin_trade_excursions  # type: ignore[import]

                    update_memecoin_trade_excursions(
                        trade_id=int(trade_id),
                        current_return_pct=overall_pnl_pct,
                        ts_utc=ts_now,
                    )
                except Exception:
                    pass
                wallet_signalers = list(entry_attribution.get("wallet_signalers") or [])
                if wallet_signalers:
                    try:
                        from utils.db import record_wallet_signal_trade_outcomes  # type: ignore[import]

                        record_wallet_signal_trade_outcomes(
                            trade_id=int(trade_id),
                            mint=mint,
                            symbol=symbol,
                            wallets=wallet_signalers,
                            pnl_pct=overall_pnl_pct,
                            closed_ts_utc=ts_now,
                            opened_ts_utc=opened_ts_utc,
                            regime_label=entry_regime_label,
                        )
                    except Exception:
                        pass
            else:
                remaining_amount_usd = round(max(0.0, amount_usd - principal_to_sell), 4)
                remaining_token_amount = round(max(0.0, token_amount - tokens_to_sell), 8)
                conn.execute("""
                    UPDATE memecoin_trades
                    SET amount_usd=?, token_amount=?, realized_release_usd=?, realized_pnl_usd=?,
                        tx_sig_close=?
                    WHERE id=?
                """, (
                    remaining_amount_usd,
                    remaining_token_amount,
                    round(realized_release_usd + principal_to_sell, 4),
                    round(realized_pnl_usd + realized_pnl_delta, 4),
                    "PAPER",
                    trade_id,
                ))
                try:
                    from utils.db import update_memecoin_trade_excursions  # type: ignore[import]

                    update_memecoin_trade_excursions(
                        trade_id=int(trade_id),
                        current_return_pct=pnl_pct,
                        ts_utc=ts_now,
                    )
                except Exception:
                    pass
        record_capital_event(
            "memecoins",
            "RELEASE",
            principal_to_sell,
            f"PAPER sell {symbol} [{reason}] {pct:.0f}%",
            symbol=symbol,
            ref_table="memecoin_trades",
            ref_id=int(trade_id),
            dry_run=True,
            ts_utc=ts_now,
        )
        record_capital_event(
            "memecoins",
            "REALIZED_PNL",
            realized_pnl_delta,
            f"PAPER sell {symbol} [{reason}] {pct:.0f}%",
            symbol=symbol,
            ref_table="memecoin_trades",
            ref_id=int(trade_id),
            dry_run=True,
            ts_utc=ts_now,
        )
        sign = "+" if pnl_pct >= 0 else ""
        orchestrator.append_memory(
            "memecoin_scan",
            f"{'SELL' if is_full_exit else 'DE-RISK'} [PAPER] {symbol} [{reason}] {pct:.0f}% "
            f"exit={exit_price:.8g} pnl={sign}{pnl_pct:.1f}% (${realized_pnl_delta:+.2f})",
        )
        base_initial = initial_amount_usd or (amount_usd + realized_release_usd)
        total_realized_pnl = round(realized_pnl_usd + realized_pnl_delta, 4)
        overall_pnl_pct = round((total_realized_pnl / base_initial) * 100, 2) if base_initial > 0 else pnl_pct
        return {
            "success": True,
            "pnl_pct": overall_pnl_pct if is_full_exit else pnl_pct,
            "pnl_usd": total_realized_pnl if is_full_exit else realized_pnl_delta,
            "realized_pnl_delta_usd": realized_pnl_delta,
            "exit_price": exit_price,
            "reason": reason,
            "dry_run": True,
            "pct_sold": pct,
            "remaining_amount_usd": 0.0 if is_full_exit else round(max(0.0, amount_usd - principal_to_sell), 4),
            "remaining_token_amount": 0.0 if is_full_exit else round(max(0.0, token_amount - tokens_to_sell), 8),
            "closed": is_full_exit,
        }
    # ── LIVE path — only reached when MEMECOIN_DRY_RUN=false ─────────────────

    try:
        from utils.jupiter_swap import execute_sell  # type: ignore
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(execute_sell(mint, tokens_to_sell))
        finally:
            loop.close()
    except Exception as exc:
        return {"success": False, "error": str(exc)}

    exit_price = float(result.get("filled_price") or result.get("exit_price") or 0)
    if exit_price <= 0:
        exit_price = _fetch_token_price(mint)

    pnl_pct = 0.0
    realized_pnl_delta = 0.0
    if entry_price > 0 and exit_price > 0:
        pnl_pct = round((exit_price - entry_price) / entry_price * 100, 2)
        realized_pnl_delta = round(principal_to_sell * pnl_pct / 100, 4)

    tx_sig = result.get("tx_sig", "")
    ts_now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    with get_conn() as conn:
        if is_full_exit:
            base_initial = initial_amount_usd or (amount_usd + realized_release_usd)
            total_realized_pnl = round(realized_pnl_usd + realized_pnl_delta, 4)
            total_realized_release = round(realized_release_usd + principal_to_sell, 4)
            overall_pnl_pct = round((total_realized_pnl / base_initial) * 100, 2) if base_initial > 0 else pnl_pct
            conn.execute("""
                UPDATE memecoin_trades
                SET status='CLOSED', exit_price=?, exit_reason=?,
                    pnl_pct=?, pnl_usd=?, realized_release_usd=?, realized_pnl_usd=?,
                    closed_ts_utc=?, tx_sig_close=?, amount_usd=0, token_amount=0
                WHERE id=?
            """, (
                exit_price, reason, overall_pnl_pct, total_realized_pnl,
                total_realized_release, total_realized_pnl, ts_now, tx_sig, trade_id,
            ))
            try:
                from utils.db import update_memecoin_trade_excursions  # type: ignore[import]

                update_memecoin_trade_excursions(
                    trade_id=int(trade_id),
                    current_return_pct=overall_pnl_pct,
                    ts_utc=ts_now,
                )
            except Exception:
                pass
            wallet_signalers = list(entry_attribution.get("wallet_signalers") or [])
            if wallet_signalers:
                try:
                    from utils.db import record_wallet_signal_trade_outcomes  # type: ignore[import]

                    record_wallet_signal_trade_outcomes(
                        trade_id=int(trade_id),
                        mint=mint,
                        symbol=symbol,
                        wallets=wallet_signalers,
                        pnl_pct=overall_pnl_pct,
                        closed_ts_utc=ts_now,
                        opened_ts_utc=opened_ts_utc,
                        regime_label=entry_regime_label,
                    )
                except Exception:
                    pass
        else:
            remaining_amount_usd = round(max(0.0, amount_usd - principal_to_sell), 4)
            remaining_token_amount = round(max(0.0, token_amount - tokens_to_sell), 8)
            conn.execute("""
                UPDATE memecoin_trades
                SET amount_usd=?, token_amount=?, realized_release_usd=?, realized_pnl_usd=?,
                    tx_sig_close=?
                WHERE id=?
            """, (
                remaining_amount_usd,
                remaining_token_amount,
                round(realized_release_usd + principal_to_sell, 4),
                round(realized_pnl_usd + realized_pnl_delta, 4),
                tx_sig,
                trade_id,
            ))
            try:
                from utils.db import update_memecoin_trade_excursions  # type: ignore[import]

                update_memecoin_trade_excursions(
                    trade_id=int(trade_id),
                    current_return_pct=pnl_pct,
                    ts_utc=ts_now,
                )
            except Exception:
                pass
    record_capital_event(
        "memecoins",
        "RELEASE",
        principal_to_sell,
        f"LIVE sell {symbol} [{reason}] {pct:.0f}%",
        symbol=symbol,
        ref_table="memecoin_trades",
        ref_id=int(trade_id),
        dry_run=False,
        ts_utc=ts_now,
    )
    record_capital_event(
        "memecoins",
        "REALIZED_PNL",
        realized_pnl_delta,
        f"LIVE sell {symbol} [{reason}] {pct:.0f}%",
        symbol=symbol,
        ref_table="memecoin_trades",
        ref_id=int(trade_id),
        dry_run=False,
        ts_utc=ts_now,
    )

    sign = "+" if pnl_pct >= 0 else ""
    orchestrator.append_memory(
        "memecoin_scan",
        f"{'SELL' if is_full_exit else 'DE-RISK'} {symbol} [{reason}] {pct:.0f}% "
        f"exit={exit_price:.8g} pnl={sign}{pnl_pct:.1f}% (${realized_pnl_delta:+.2f})",
    )

    # Telegram alert on auto-exit (Patch 121)
    try:
        from utils.telegram_alerts import send_telegram_sync, should_rate_limit  # type: ignore
        emoji = "💰" if reason == "TP_2X" else "🛑"
        if not should_rate_limit("meme_exit", 60):
            send_telegram_sync(
                f"Memecoin {reason} {emoji}",
                f"{symbol}  {pct:.0f}%  pnl={sign}{pnl_pct:.1f}% (${realized_pnl_delta:+.2f})",
                emoji,
            )
    except Exception:
        pass

    base_initial = initial_amount_usd or (amount_usd + realized_release_usd)
    total_realized_pnl = round(realized_pnl_usd + realized_pnl_delta, 4)
    overall_pnl_pct = round((total_realized_pnl / base_initial) * 100, 2) if base_initial > 0 else pnl_pct
    return {
        "success":    True,
        "pnl_pct":    overall_pnl_pct if is_full_exit else pnl_pct,
        "pnl_usd":    total_realized_pnl if is_full_exit else realized_pnl_delta,
        "realized_pnl_delta_usd": realized_pnl_delta,
        "exit_price": exit_price,
        "reason":     reason,
        "pct_sold":   pct,
        "remaining_amount_usd": 0.0 if is_full_exit else round(max(0.0, amount_usd - principal_to_sell), 4),
        "remaining_token_amount": 0.0 if is_full_exit else round(max(0.0, token_amount - tokens_to_sell), 8),
        "closed": is_full_exit,
    }


# ── Outcome tracker (Patch 116) ───────────────────────────────────────────────

def memecoin_outcome_step():
    """
    Called every 60s alongside monitor_step.
    Fills in 1h / 4h / 24h price returns for every PENDING signal in
    memecoin_signal_outcomes. This is the core learning loop.

    Patch 294: also processes WATCH (DISCOVERY) rows that have a valid
    price_at_scan — previously these expired to STALE without generating
    any learning data. Priced WATCH rows are treated identically to PENDING
    rows and promoted to COMPLETE after the 24h fill. This expands perf_tier
    coverage from scored-signal-only (~15 symbols) to all monitored tokens.
    """
    now    = datetime.now(timezone.utc)
    ts_now = now.strftime("%Y-%m-%d %H:%M:%S")

    with get_conn() as conn:
        pending = conn.execute("""
            SELECT id, mint, source, status, price_at_scan, scanned_at,
                   return_1h_pct, return_4h_pct, return_24h_pct
            FROM memecoin_signal_outcomes
            WHERE status = 'PENDING'
               OR (status = 'WATCH'
                   AND price_at_scan IS NOT NULL
                   AND price_at_scan > 0)
               OR (
                   source = 'DISCOVERY'
                   AND status = 'WATCH'
                   AND (price_at_scan IS NULL OR price_at_scan <= 0)
                   AND mint IS NOT NULL
                   AND scanned_at >= datetime('now', '-6 hours')
               )
        """).fetchall()

    for row in pending:
        mint          = row["mint"]
        source        = str(row["source"] or "").upper()
        status        = str(row["status"] or "").upper()
        price_at_scan = float(row["price_at_scan"] or 0)

        try:
            raw_ts  = row["scanned_at"]
            scanned = datetime.fromisoformat(raw_ts.replace(" ", "T"))
            if scanned.tzinfo is None:
                scanned = scanned.replace(tzinfo=timezone.utc)
        except Exception:
            continue

        age = now - scanned

        # Rescue fresh DISCOVERY rows that missed price capture at scan time.
        # Using the current price as a proxy is only acceptable while the row is
        # still very young; after that, the recovered "entry" would be fiction.
        if price_at_scan <= 0:
            if source == "DISCOVERY" and status == "WATCH" and mint and age <= timedelta(hours=6):
                rescued = _fetch_token_price(mint, allow_dex_fallback=False)
                if rescued > 0:
                    price_at_scan = rescued
                    try:
                        with get_conn() as conn:
                            conn.execute(
                                "UPDATE memecoin_signal_outcomes SET price_at_scan = ? WHERE id = ?",
                                (round(rescued, 12), row["id"]),
                            )
                    except Exception:
                        pass
                else:
                    continue
            else:
                continue

        if age < timedelta(hours=1):
            continue

        current = _fetch_token_price(mint, allow_dex_fallback=False)
        if current <= 0:
            continue

        ret = round((current - price_at_scan) / price_at_scan * 100, 2)

        updates: dict = {}
        if age >= timedelta(hours=1)  and row["return_1h_pct"]  is None:
            updates["return_1h_pct"]       = ret
            updates["evaluated_1h_ts_utc"] = ts_now
        if age >= timedelta(hours=4)  and row["return_4h_pct"]  is None:
            updates["return_4h_pct"]       = ret
            updates["evaluated_4h_ts_utc"] = ts_now
        if age >= timedelta(hours=24) and row["return_24h_pct"] is None:
            updates["return_24h_pct"]       = ret
            updates["evaluated_24h_ts_utc"] = ts_now
            updates["status"]               = "COMPLETE"

        if not updates:
            continue

        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values     = list(updates.values()) + [row["id"]]
        try:
            with get_conn() as conn:
                conn.execute(
                    f"UPDATE memecoin_signal_outcomes SET {set_clause} WHERE id = ?",
                    values,
                )
        except Exception:
            pass

    # Mark rows with dead/unlisted tokens as STALE after 48h
    # Prevents orphaned PENDING rows from clogging the learning query forever
    try:
        with get_conn() as conn:
            conn.execute("""
                UPDATE memecoin_signal_outcomes SET status = 'STALE'
                WHERE status = 'PENDING'
                  AND scanned_at < datetime('now', '-48 hours')
            """)
    except Exception:
        pass

    # TTL cleanup for WATCH (DISCOVERY) rows — only expire rows without a
    # captured entry price (these can never generate return data and accumulate
    # indefinitely at ~50-300 rows/day).  Patch 294: rows WITH price_at_scan
    # are eligible for outcome fill and must NOT be expired here — they will
    # be promoted to COMPLETE by the fill loop above after ≥24h.
    try:
        with get_conn() as conn:
            conn.execute("""
                UPDATE memecoin_signal_outcomes SET status = 'STALE'
                WHERE status = 'WATCH'
                  AND (price_at_scan IS NULL OR price_at_scan = 0)
                  AND scanned_at < datetime('now', '-72 hours')
            """)
    except Exception:
        pass

    # Patch 267: fill decision-journal outcomes for operator-resolved entries.
    # Runs every 60s alongside MSO fill — zero overhead, fire-and-forget.
    # This closes the accountability loop: ACT surfaced → operator decides →
    # 24h outcome fills automatically → verdict computed.
    try:
        from utils.surface_log import dj_outcome_fill  # type: ignore
        dj_outcome_fill()
    except Exception:
        pass

    # Patch 270: fill research_surface_log outcomes periodically.
    # Previously only triggered on GET /api/research/candidates (page visit).
    # Wired here so resolution happens automatically ~24h after surfacing
    # regardless of whether the operator visits the Research page.
    try:
        from utils.surface_log import surface_log_fill_outcomes  # type: ignore
        surface_log_fill_outcomes()
    except Exception:
        pass


# ── Auto-tuner (Patch 117) ────────────────────────────────────────────────────

def _tune_thresholds_step():
    """
    Analyze completed signal outcomes to auto-tune scanner thresholds.
    Requires >= 20 complete samples. Writes to kv_store['memecoin_learned_thresholds'].
    This is how the agents get smarter over time.
    Gate: set MEMECOIN_TUNER_ENABLED=false to disable and clear stale thresholds.
    """
    # Patch 254: gate tuner so old-model MSO data can't poison new model thresholds.
    # When disabled, write a low-confidence marker to clear any stale high-confidence entry.
    if os.getenv("MEMECOIN_TUNER_ENABLED", "true").lower() != "true":
        try:
            with get_conn() as conn:
                conn.execute("""
                    INSERT INTO kv_store (key, value) VALUES ('memecoin_learned_thresholds', ?)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """, (json.dumps({"confidence": "disabled", "thresholds": {}, "note": "tuner gated by MEMECOIN_TUNER_ENABLED=false"}),))
        except Exception:
            pass
        return

    ts_now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    try:
        with get_conn() as conn:
            rows = conn.execute("""
                SELECT score, rug_label, top_holder_pct, mcap_at_scan,
                       token_age_days, vol_acceleration,
                       return_1h_pct, return_4h_pct, return_24h_pct, bought
                FROM memecoin_signal_outcomes
                WHERE status = 'COMPLETE' AND return_4h_pct IS NOT NULL
            """).fetchall()
    except Exception:
        return

    if len(rows) < 20:
        return  # not enough data yet

    total      = len(rows)
    winners    = [r for r in rows if (r["return_4h_pct"] or 0) > 0]   # Patch 132: any profit counts
    losers     = [r for r in rows if (r["return_4h_pct"] or 0) <= 0]
    win_rate   = round(len(winners) / total * 100, 1)

    # Win rates by rug label
    rug_stats: dict = {}
    for label in ("GOOD", "WARN", "UNKNOWN"):
        subset = [r for r in rows if r["rug_label"] == label]
        if subset:
            w = sum(1 for r in subset if (r["return_4h_pct"] or 0) > 0)  # Patch 132
            rug_stats[label] = {
                "count":    len(subset),
                "win_rate": round(w / len(subset) * 100, 1),
            }

    # Optimal score threshold — Patch 132
    # Search the FULL score range (not just 55-90) and find the floor that
    # maximises win rate. Win = 4h return > 0 (any profit — >30% too strict
    # at small sample sizes). Data shows low-score tokens outperform high-score
    # tokens, so we must search downward too; don't assume high score = better.
    best_score_min = 30   # conservative default — cast wide until data settles
    best_wr = 0.0
    for threshold in range(20, 76, 5):
        above = [r for r in rows if (r["score"] or 0) >= threshold]
        if len(above) >= 5:
            wr = sum(1 for r in above if (r["return_4h_pct"] or 0) > 0) / len(above)
            if wr > best_wr:
                best_wr = wr
                best_score_min = threshold

    # Optimal vol_acceleration: use 25th percentile of winners
    best_vacc = 5.0
    if winners:
        vaccs = sorted(float(r["vol_acceleration"] or 0) for r in winners)
        p25   = vaccs[max(0, len(vaccs) // 4 - 1)]
        best_vacc = max(5.0, round(p25, 1))

    # Max top holder: stay below average of winning signals + 50% buffer
    best_holder_max = 35.0
    win_holders = [float(r["top_holder_pct"] or 0) for r in winners if r["top_holder_pct"]]
    if win_holders:
        avg_winner_holder = sum(win_holders) / len(win_holders)
        best_holder_max   = min(50.0, round(avg_winner_holder * 1.5, 1))

    # Max score ceiling — tokens scoring too high are over-excited/FOMO and dump
    # Find the lowest ceiling above which WR drops below 40%
    best_score_max = 999  # no ceiling by default
    for ceiling in range(best_score_min + 5, 80, 5):
        above = [r for r in rows if (r["score"] or 0) > ceiling]
        if len(above) < 5:
            break
        wr = sum(1 for r in above if (r["return_4h_pct"] or 0) > 0) / len(above)
        if wr < 0.40:
            best_score_max = ceiling
            break

    thresholds = {
        "min_score":            best_score_min,
        "max_score":            best_score_max,
        "min_vol_acceleration": best_vacc,
        "max_top_holder_pct":   best_holder_max,
    }

    confidence = "low" if total < 50 else "medium" if total < 200 else "high"

    payload = {
        "thresholds":  thresholds,
        "sample_size": total,
        "win_rate":    win_rate,
        "rug_stats":   rug_stats,
        "updated_at":  ts_now,
        "confidence":  confidence,
    }

    try:
        with get_conn() as conn:
            conn.execute("""
                INSERT INTO kv_store (key, value) VALUES ('memecoin_learned_thresholds', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """, (json.dumps(payload),))
        orchestrator.append_memory(
            "memecoin_scan",
            f"TUNE {total} samples | win={win_rate:.0f}% | "
            f"score_min={best_score_min} vacc_min={best_vacc:.0f}% "
            f"confidence={confidence}",
        )
    except Exception:
        pass


# ── Monitor ───────────────────────────────────────────────────────────────────

def _auto_buy_step() -> None:
    """Auto-buy top qualifying signal each cycle when gate is enabled (Patch 121/135).

    Gates (all must pass):
      MEMECOIN_AUTO_BUY=true — master switch
      open positions < MEMECOIN_MAX_OPEN — capacity check
      F&G favorable (>25) — live mode only; skipped in paper mode to collect data at all conditions
      recent WR >= 40% (last 30 GOOD outcomes) — live mode only; pauses buys in bad market regimes
      score >= min_score threshold — signal quality floor
      score <= max_score threshold — avoids over-excited/FOMO tokens (historically poor WR above ceiling)
      rug_label == GOOD — safety
      buy_pressure >= 55% — momentum
      vol_acceleration >= min_vol_acceleration — tuner-learned momentum gate
      top_holder_pct <= max_top_holder_pct — concentration/whale dump protection
      mint_revoked == True — no inflation risk
    """
    if os.getenv("MEMECOIN_AUTO_BUY", "false").lower() != "true":
        return

    # ── Roadmap 3: authority bridge observe-mode check ────────────────────────
    try:
        from authority import resolve_action  # type: ignore[import]
        _auth = resolve_action("new_entry", "memecoins", executor="memecoins")
        if _auth["verdict"] == "BLOCK":
            logger.debug(
                "AUTHORITY_BLOCK: memecoin_manager _auto_buy_step skipped — %s",
                "; ".join(_auth["reasons"]),
            )
            return
    except Exception as _auth_exc:
        logger.debug("authority check skipped: %s", _auth_exc)
    # ── End authority bridge ──────────────────────────────────────────────────

    # Resolve execution mode (PAPER | PILOT | LIVE)
    mode = get_memecoin_mode()

    # Capacity check
    with get_conn() as conn:
        open_count = conn.execute(
            "SELECT COUNT(*) FROM memecoin_trades WHERE status='OPEN'"
        ).fetchone()[0]
        open_mints = {
            r[0] for r in conn.execute(
                "SELECT mint FROM memecoin_trades WHERE status='OPEN'"
            ).fetchall()
        }
    max_open = int(os.getenv("MEMECOIN_MAX_OPEN", "3"))
    if open_count >= max_open:
        return

    # ── PILOT MODE: enforce hard guardrails before any live execution ─────────
    # These caps are independent of the general MAX_OPEN check above.
    # All three must pass for a pilot buy to proceed.
    is_pilot_trade = False
    if mode == "PILOT":
        pilot_max_open  = int(  os.getenv("MEMECOIN_PILOT_MAX_OPEN",       "2"))
        pilot_max_usd   = float(os.getenv("MEMECOIN_PILOT_MAX_USD",         "10"))
        pilot_total_cap = float(os.getenv("MEMECOIN_PILOT_TOTAL_CAP_USD",   "50"))
        with get_conn() as conn:
            pilot_open = conn.execute(
                "SELECT COUNT(*) FROM memecoin_trades WHERE is_pilot=1 AND status='OPEN'"
            ).fetchone()[0]
            pilot_exposure = float(conn.execute(
                "SELECT COALESCE(SUM(amount_usd), 0) FROM memecoin_trades "
                "WHERE is_pilot=1 AND status='OPEN'"
            ).fetchone()[0] or 0)
        if pilot_open >= pilot_max_open:
            return  # pilot position cap hit — hard stop
        if pilot_exposure >= pilot_total_cap:
            return  # pilot total exposure ceiling hit — hard stop
        is_pilot_trade = True

    # F&G check — live mode only. In paper mode we want data at all F&G levels
    # so the learning loop accumulates outcomes across market conditions.
    dry_run = (mode == "PAPER")
    if not dry_run:
        try:
            from utils.agent_coordinator import get_fear_greed  # type: ignore
            if not get_fear_greed().get("favorable", True):
                return
        except Exception:
            pass

    # Load all tuner thresholds when confidence is medium/high (Patch 135)
    threshold       = float(os.getenv("MEMECOIN_BUY_SCORE_MIN", "65"))
    max_score       = 999    # no ceiling unless tuner has data
    vacc_min        = 5.0
    holder_max      = 35.0
    try:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT value FROM kv_store WHERE key='memecoin_learned_thresholds'"
            ).fetchone()
        if row:
            lt = json.loads(row[0])
            if lt.get("confidence") in ("medium", "high"):
                t = lt.get("thresholds", {})
                threshold  = float(t.get("min_score",            threshold))
                max_score  = float(t.get("max_score",            999))
                vacc_min   = float(t.get("min_vol_acceleration", vacc_min))
                holder_max = float(t.get("max_top_holder_pct",   holder_max))
    except Exception:
        pass

    # Recent performance gate — live mode only. If last 30 GOOD outcomes < 40% WR,
    # market conditions are poor → pause buying to protect capital (Patch 135)
    if not dry_run:
        try:
            with get_conn() as conn:
                recent = conn.execute("""
                    SELECT return_4h_pct FROM memecoin_signal_outcomes
                    WHERE rug_label='GOOD' AND return_4h_pct IS NOT NULL
                    ORDER BY scanned_at DESC LIMIT 30
                """).fetchall()
            if len(recent) >= 30:
                recent_wr = sum(1 for r in recent if r[0] > 0) / len(recent)
                if recent_wr < 0.40:
                    log.info("[MEME] Recent WR %.1f%% < 40%% — pausing live buys", recent_wr * 100)
                    return
        except Exception:
            pass

    # Loop signals by score descending — buy first qualifying one.
    # Follow the proof lane's warm-cache fallback so brief live-cache gaps do not
    # leave auto-buy blind when there is still recent scanner signal context.
    signals = sorted(get_cached_signals(), key=lambda s: s.get("score", 0), reverse=True)
    signal_source = "LIVE_CACHE"
    if not signals:
        signals = _warm_cache_signals(limit=50, max_age_hours=6)
        if signals:
            signal_source = "WARM_CACHE_FALLBACK"
            log.info("[AUTO_BUY] using recent non-empty cache fallback (%d rows)", len(signals))
    # Pilot mode uses its own size cap; live/paper use the global BUY_USD setting
    if mode == "PILOT":
        amount_usd = float(os.getenv("MEMECOIN_PILOT_MAX_USD", "10"))
    else:
        amount_usd = float(os.getenv("MEMECOIN_BUY_USD", "15"))
    top_reject: dict | None = None
    buy_attempted = False
    buy_succeeded = False

    if dry_run:
        proof_snapshot = get_proof_candidate_snapshot(limit=50, open_mints=open_mints)
        _touch_proof_ready_first_seen(proof_snapshot)
        _emit_proof_stack_surfaces(proof_snapshot)
        proof_ready = [
            c for c in list(proof_snapshot.get("candidates") or [])
            if str(c.get("proof_status") or "") == "PROOF_READY"
        ]
        if not proof_ready:
            blockers = ", ".join(
                f"{b.get('key')}={b.get('count')}"
                for b in list(proof_snapshot.get("blocker_counts") or [])[:3]
            ) or "none"
            log.debug("[AUTO_BUY] no PROOF_READY candidates — blockers: %s", blockers)
            return

        chosen = proof_ready[0]
        proof_snapshot_json = json.dumps(chosen.get("proof_components") or {})
        entry_attribution = _build_trade_entry_attribution(chosen, proof_snapshot)
        result = buy_memecoin(
            str(chosen.get("mint") or ""),
            str(chosen.get("symbol") or "UNKNOWN"),
            amount_usd,
            is_pilot=is_pilot_trade,
            entry_fuel_quality=chosen.get("fuel_quality"),
            entry_window=chosen.get("entry_window"),
            entry_move_phase=chosen.get("move_phase"),
            entry_score=float(chosen.get("score") or 0.0),
            is_proof_build=True,
            proof_status=str(chosen.get("proof_status") or "PROOF_READY"),
            proof_reason=str(chosen.get("proof_reason") or ""),
            proof_score=float(chosen.get("proof_score") or 0.0),
            proof_snapshot_json=proof_snapshot_json,
            entry_attribution=entry_attribution,
        )
        if result.get("success"):
            scanner_ctx = (chosen.get("proof_components") or {}).get("scanner") or {}
            bp = float(scanner_ctx.get("buy_pressure") or 0.0)
            log.info(
                "[AUTO_BUY] %s PAPER proof build — proof_score=%.1f reason=%s",
                chosen.get("symbol"),
                float(chosen.get("proof_score") or 0.0),
                chosen.get("proof_reason") or "proof-ready",
            )
            try:
                from utils.telegram_alerts import send_telegram_sync, should_rate_limit  # type: ignore
                if not should_rate_limit("meme_buy", 60):
                    send_telegram_sync(
                        "Memecoin Buy [PAPER][PROOF] 🟢",
                        f"{chosen.get('symbol')}  score={float(chosen.get('score') or 0.0):.1f}  "
                        f"proof={float(chosen.get('proof_score') or 0.0):.1f}\n"
                        f"bp={bp:.0f}%  tags={','.join(chosen.get('support_overlap_tags') or []) or 'none'}\n"
                        f"${amount_usd:.0f}  entry={result.get('entry_price', 0):.8g}\n"
                        f"{chosen.get('proof_reason') or 'Proof-ready paper build.'}",
                        "🟢",
                    )
            except Exception:
                pass
        return

    if mode == "PILOT" and _pilot_proof_bootstrap_enabled():
        proof_snapshot = get_proof_candidate_snapshot(limit=50, open_mints=open_mints)
        _touch_proof_ready_first_seen(proof_snapshot)
        _emit_proof_stack_surfaces(proof_snapshot)
        bootstrap_ready = [
            c for c in list(proof_snapshot.get("candidates") or [])
            if str(c.get("proof_status") or "").upper() == "PROOF_READY"
        ]
        chosen = _select_pilot_bootstrap_candidate(
            proof_snapshot,
            threshold=threshold,
            vacc_min=vacc_min,
            holder_max=holder_max,
            open_mints=open_mints,
        )
        if chosen is None:
            if bootstrap_ready:
                top = bootstrap_ready[0]
                scanner_ctx = dict((top.get("proof_components") or {}).get("scanner") or {})
                lifecycle_ctx = dict((top.get("proof_components") or {}).get("lifecycle") or {})
                log.info(
                    "[AUTO_BUY] PILOT bootstrap candidate absent — top proof-ready=%s rug=%s bp=%.1f vacc=%.1f top_holder=%.1f ew=%s fq=%s mp=%s flc=%s",
                    top.get("symbol"),
                    str(scanner_ctx.get("rug_label") or "UNKNOWN"),
                    float(scanner_ctx.get("buy_pressure") or 0.0),
                    float(scanner_ctx.get("vol_acceleration") or 0.0),
                    float(scanner_ctx.get("top_holder_pct") or 0.0),
                    str(lifecycle_ctx.get("entry_window") or ""),
                    str(lifecycle_ctx.get("fuel_quality") or ""),
                    str(lifecycle_ctx.get("move_phase") or ""),
                    int(lifecycle_ctx.get("first_leg_confirmed") or 0),
                )
            else:
                blockers = ", ".join(
                    f"{b.get('key')}={b.get('count')}"
                    for b in list(proof_snapshot.get("blocker_counts") or [])[:3]
                ) or "none"
                log.info("[AUTO_BUY] PILOT bootstrap candidate absent — no PROOF_READY names (blockers: %s)", blockers)
        if chosen is not None:
            proof_snapshot_json = json.dumps(chosen.get("proof_components") or {})
            entry_attribution = _build_trade_entry_attribution(chosen, proof_snapshot)
            result = buy_memecoin(
                str(chosen.get("mint") or ""),
                str(chosen.get("symbol") or "UNKNOWN"),
                amount_usd,
                is_pilot=is_pilot_trade,
                entry_fuel_quality=chosen.get("fuel_quality"),
                entry_window=chosen.get("entry_window"),
                entry_move_phase=chosen.get("move_phase"),
                entry_score=float(chosen.get("score") or 0.0),
                is_proof_build=True,
                proof_status=str(chosen.get("proof_status") or "PROOF_READY"),
                proof_reason=str(chosen.get("proof_reason") or ""),
                proof_score=float(chosen.get("proof_score") or 0.0),
                proof_snapshot_json=proof_snapshot_json,
                entry_attribution=entry_attribution,
            )
            if result.get("success"):
                scanner_ctx = (chosen.get("proof_components") or {}).get("scanner") or {}
                bp = float(scanner_ctx.get("buy_pressure") or 0.0)
                warn_tag = " [CLEAN_WARN]" if str(scanner_ctx.get("rug_label") or "").upper() == "WARN" else ""
                log.info(
                    "[AUTO_BUY] %s PILOT bootstrap proof build%s — proof_score=%.1f reason=%s",
                    chosen.get("symbol"),
                    warn_tag,
                    float(chosen.get("proof_score") or 0.0),
                    chosen.get("proof_reason") or "proof-ready bootstrap",
                )
                try:
                    from utils.telegram_alerts import send_telegram_sync, should_rate_limit  # type: ignore
                    if not should_rate_limit("meme_buy", 60):
                        send_telegram_sync(
                            f"Memecoin Buy [PILOT][BOOTSTRAP]{warn_tag} 🟢",
                            f"{chosen.get('symbol')}  score={float(chosen.get('score') or 0.0):.1f}  "
                            f"proof={float(chosen.get('proof_score') or 0.0):.1f}\n"
                            f"bp={bp:.0f}%  tags={','.join(chosen.get('support_overlap_tags') or []) or 'none'}\n"
                            f"${amount_usd:.0f}  entry={result.get('entry_price', 0):.8g}\n"
                            f"{chosen.get('proof_reason') or 'Pilot bootstrap proof build.'}",
                            "🟢",
                        )
                except Exception:
                    pass
                return

    # Patch 250/251: batch-load lifecycle intelligence keyed by mint (Patch 251 upgrade)
    # Patch 251: symbol_lifecycle now stores mint — key by mint for safe ticker-collision handling
    _lc_map: dict = {}
    try:
        with get_conn() as conn:
            for _r in conn.execute(
                "SELECT mint, entry_window, fuel_quality, move_phase, first_leg_confirmed, "
                "COALESCE(provisional_first_leg, 0), COALESCE(provisional_reason, ''), COALESCE(provisional_score, 0.0) "
                "FROM symbol_lifecycle WHERE mint IS NOT NULL"
            ).fetchall():
                _lc_map[_r[0]] = {
                    "entry_window": _r[1],
                    "fuel_quality": _r[2],
                    "move_phase": _r[3],
                    "first_leg_confirmed": int(_r[4] or 0),
                    "provisional_first_leg": int(_r[5] or 0),
                    "provisional_reason": str(_r[6] or ""),
                    "provisional_score": float(_r[7] or 0.0),
                }
    except Exception as _e:
        log.warning("[AUTO_BUY] lifecycle map load failed: %s", _e)

    for sig in signals:
        mint       = sig.get("mint", "")
        symbol     = sig.get("symbol", "UNKNOWN")
        score      = sig.get("score", 0)
        rug        = sig.get("rug_label", "UNKNOWN")
        bp         = sig.get("buy_pressure") or 50.0
        revoked    = sig.get("mint_revoked", False)
        vacc       = float(sig.get("vol_acceleration") or 0.0)
        holder_pct = float(sig.get("top_holder_pct") or 0.0)
        score_ceiling_soft_block = ""

        def _remember_reject(reason: str) -> None:
            nonlocal top_reject
            if top_reject is None:
                top_reject = {
                    "symbol": symbol,
                    "score": float(score or 0.0),
                    "reason": reason,
                    "rug": rug,
                    "bp": float(bp or 0.0),
                    "vacc": float(vacc or 0.0),
                    "holder_pct": float(holder_pct or 0.0),
                    "signal_source": signal_source,
                }

        if not mint or mint in open_mints:
            _remember_reject("already_open_or_missing_mint")
            continue
        if score < threshold:
            _remember_reject(f"score_below_floor ({float(score or 0.0):.1f} < {threshold:.1f})")
            break          # sorted descending — nothing better below
        if score > max_score:
            _lc = _lc_map.get(mint)
            ceiling_ctx = _legacy_score_ceiling_override(
                sig=sig,
                lifecycle=_lc,
                max_score=max_score,
                vacc_min=vacc_min,
                holder_max=holder_max,
            )
            if not ceiling_ctx["override"]:
                if ceiling_ctx.get("reviewable"):
                    score_ceiling_soft_block = f"score_above_ceiling ({float(score or 0.0):.1f} > {max_score:.1f})"
                else:
                    _remember_reject(f"score_above_ceiling ({float(score or 0.0):.1f} > {max_score:.1f})")
                    continue       # over-excited token — historically poor WR above ceiling
            else:
                log.info(
                    "[AUTO_BUY] score ceiling extended for %s — score=%.1f dynamic_max=%.1f mq=%.1f",
                    symbol,
                    float(score or 0.0),
                    float(ceiling_ctx.get("dynamic_max_score") or max_score),
                    float(ceiling_ctx.get("market_quality_score") or 0.0),
                )
        if rug != "GOOD":
            _remember_reject(f"rug_not_good ({rug})")
            continue
        if bp < 55:
            _remember_reject(f"buy_pressure_low ({float(bp or 0.0):.1f} < 55.0)")
            continue
        if not revoked:    # mint authority still live = inflation risk
            _remember_reject("mint_not_revoked")
            continue
        if vacc < vacc_min:
            _remember_reject(f"vol_acceleration_low ({vacc:.1f} < {vacc_min:.1f})")
            continue       # insufficient volume acceleration
        if holder_pct > holder_max:
            _remember_reject(f"holder_too_concentrated ({holder_pct:.1f}% > {holder_max:.1f}%)")
            continue       # too concentrated — whale dump risk
        if score_ceiling_soft_block:
            _remember_reject(score_ceiling_soft_block)
            continue

        # Patch 250/251/274: execution-grade conviction gate — paper mode only
        # Patch 251: lookup by mint (not symbol) to prevent recycled-ticker collisions
        # Patch 274: upgraded from negative-only filter to positive-conviction gate
        if dry_run:
            _lc = _lc_map.get(mint)
            if _lc is None:
                _remember_reject("lifecycle_missing")
                log.info("[AUTO_BUY] %s research-only — lifecycle missing", symbol)
                continue

            _ew = _lc.get("entry_window")
            _fq = _lc.get("fuel_quality")
            _mp = _lc.get("move_phase")
            _flc = int(_lc.get("first_leg_confirmed") or 0)

            # Hard rejections (negative gate — unchanged from Patch 250/251)
            if _ew == "CLOSED":
                _remember_reject("entry_window_closed")
                log.debug("[AUTO_BUY] %s skip — entry_window=CLOSED", symbol)
                continue
            if _fq == "TRAP":
                _remember_reject("fuel_trap")
                log.debug("[AUTO_BUY] %s skip — fuel_quality=TRAP", symbol)
                continue
            if _mp in ("EXTENDED", "CHURN"):
                _remember_reject(f"move_phase_{str(_mp or '').lower()}")
                log.debug("[AUTO_BUY] %s skip — move_phase=%s", symbol, _mp)
                continue

            # Positive conviction requirements (Patch 274)
            # "Not rejected" is not execution-grade — require affirmative structural evidence
            if _ew != "OPEN":
                _remember_reject(f"entry_window_not_open ({_ew})")
                log.info("[AUTO_BUY] %s research-only — entry_window=%s (not OPEN)", symbol, _ew)
                continue
            if _fq not in ("STRONG", "MODERATE"):
                _remember_reject(f"fuel_not_ready ({_fq})")
                log.info("[AUTO_BUY] %s research-only — fuel_quality=%s (not STRONG/MODERATE)", symbol, _fq)
                continue

            # Structural history requirement (Patch 280)
            # Require confirmed first leg — token must have demonstrated a real move
            # and reload structure, not just a good-looking current state.
            # Outcome data: first_leg_confirmed=1 → 47% win5_4h vs sub-30% for debut tokens.
            if not _flc:
                _remember_reject("first_leg_unconfirmed")
                log.info("[AUTO_BUY] %s research-only — first_leg_confirmed=0 (no structural history)", symbol)
                continue

            # Anti-repeat loss protection (Patch 274)
            # Skip if this mint lost a paper trade within the last 7 days
            try:
                with get_conn() as _arc:
                    _recent_loss = _arc.execute(
                        "SELECT 1 FROM memecoin_trades "
                        "WHERE mint=? AND pnl_pct<0 "
                        "AND closed_ts_utc > datetime('now', '-7 days') LIMIT 1",
                        (mint,),
                    ).fetchone()
                if _recent_loss:
                    _remember_reject("recent_loss_lock")
                    log.info("[AUTO_BUY] %s research-only — recent loss within 7d", symbol)
                    continue
            except Exception as _are:
                log.warning("[AUTO_BUY] anti-repeat check failed for %s: %s", symbol, _are)

        # Patch 290: guaranteed entry context capture
        # Paper mode: _lc was verified non-None by the conviction gate above.
        #   Reuse _lc directly — eliminates redundant map lookup and makes
        #   provenance explicit: same tuple that passed the gate enters the DB.
        # Live mode: _lc is not set (if-dry_run block was skipped); look up
        #   _lc_map instead.  Warn loudly if missing — live trades should not
        #   silently lose entry context.
        _lc_at_buy = _lc if dry_run else _lc_map.get(mint)
        if not dry_run and _lc_at_buy is None:
            log.warning(
                "[AUTO_BUY] %s LIVE trade — mint not in symbol_lifecycle; "
                "entry_window/fuel_quality/move_phase will be NULL for this trade",
                symbol,
            )
        _entry_window = _lc_at_buy.get("entry_window") if _lc_at_buy else None
        _entry_fuel_quality = _lc_at_buy.get("fuel_quality") if _lc_at_buy else None
        _entry_move_phase = _lc_at_buy.get("move_phase") if _lc_at_buy else None
        buy_attempted = True
        entry_attribution = {
            "timing_score": sig.get("timing_score"),
            "safety_score": sig.get("safety_score"),
            "market_quality_score": sig.get("market_quality_score"),
            "support_score": None,
            "readiness_score": None,
            "profit_room_score": sig.get("profit_room_score"),
            "profit_room_label": sig.get("profit_room_label"),
            "market_quality_verdict": sig.get("market_quality_verdict"),
            "wallet_behavior_state": None,
            "smart_money_quality": None,
            "regime_label": _current_regime_label(),
            "route": "SCANNER_ONLY",
            "lane_authority": "N/A",
            "scanner_regime": sig.get("scanner_regime"),
        }
        try:
            from utils.db import get_recent_wallet_signalers_for_mint  # type: ignore[import]

            wallet_signalers = get_recent_wallet_signalers_for_mint(mint, max_age_hours=24, limit=8) or []
        except Exception:
            wallet_signalers = []
        entry_attribution["wallet_signalers"] = wallet_signalers
        entry_attribution["wallet_signaler_count"] = len(wallet_signalers)
        entry_attribution = _augment_entry_latency_context(entry_attribution, mint=mint, candidate=None)
        result = buy_memecoin(
            mint, symbol, amount_usd, is_pilot=is_pilot_trade,
            entry_fuel_quality=_entry_fuel_quality,
            entry_window=_entry_window,
            entry_move_phase=_entry_move_phase,
            entry_score=float(score),
            entry_attribution=entry_attribution,
        )
        if result.get("success"):
            buy_succeeded = True
            log.info(
                "[AUTO_BUY] %s %s buy confirmed — "
                "entry_context: ew=%s fq=%s mp=%s score=%.1f",
                symbol,
                "PAPER" if dry_run else "LIVE",
                _entry_window or "NULL",
                _entry_fuel_quality or "NULL",
                _entry_move_phase or "NULL",
                float(score),
            )
            try:
                from utils.telegram_alerts import send_telegram_sync, should_rate_limit  # type: ignore
                if not should_rate_limit("meme_buy", 60):
                    trade_mode = "PAPER" if result.get("dry_run") else ("PILOT" if is_pilot_trade else "LIVE")
                    send_telegram_sync(
                        f"Memecoin Buy [{trade_mode}] 🟢",
                        f"{symbol}  score={score:.1f}  rug=GOOD  bp={bp:.0f}%\n"
                        f"${amount_usd:.0f}  entry={result.get('entry_price', 0):.8g}\n"
                        f"Studying mechanics — {trade_mode} trade logged.",
                        "🟢",
                    )
            except Exception:
                pass
        else:
            log.warning(
                "[AUTO_BUY] %s %s buy attempt failed — %s",
                symbol,
                "PAPER" if dry_run else "LIVE",
                result.get("error") or "unknown_error",
            )
        break   # one buy per cycle max

    if not buy_attempted and top_reject is not None:
        log.info(
            "[AUTO_BUY] top candidate blocked — %s src=%s sc=%.1f rug=%s bp=%.1f vacc=%.1f holder=%.1f%% reason=%s",
            str(top_reject.get("symbol") or "UNKNOWN"),
            str(top_reject.get("signal_source") or "UNKNOWN"),
            float(top_reject.get("score") or 0.0),
            str(top_reject.get("rug") or "UNKNOWN"),
            float(top_reject.get("bp") or 0.0),
            float(top_reject.get("vacc") or 0.0),
            float(top_reject.get("holder_pct") or 0.0),
            str(top_reject.get("reason") or "unknown"),
        )


def memecoin_monitor_step():
    """
    Called every 60s from background loop.
    1. Auto-buys qualifying signals when MEMECOIN_AUTO_BUY=true (Patch 121).
    2. Auto-exits open positions at +100% (TP 2x) or -50% (SL).
    3. Fills in 1h/4h/24h returns on tracked signals (learning loop).
    4. Runs threshold tuner every cycle (no-ops until 20+ complete samples).
    """
    orchestrator.heartbeat("memecoin_scan")
    orchestrator.heartbeat("memecoin_monitor")

    # Auto-buy qualifying signals (Patch 121 — PAPER MODE while MEMECOIN_DRY_RUN=true)
    try:
        _auto_buy_step()
    except Exception:
        pass

    with get_conn() as conn:
        rows = conn.execute("""
            SELECT id, mint, symbol, entry_price, amount_usd, opened_ts_utc,
                   COALESCE(is_proof_build, 0) AS is_proof_build
            FROM memecoin_trades
            WHERE status = 'OPEN'
        """).fetchall()

    for row in rows:
        mint        = row["mint"]
        symbol      = str(row["symbol"] or "")
        entry_price = float(row["entry_price"] or 0)
        is_proof    = int(row["is_proof_build"] or 0) == 1
        if entry_price <= 0:
            continue

        current_price = _fetch_token_price(mint)
        if current_price <= 0:
            continue

        pnl_pct = (current_price - entry_price) / entry_price * 100

        lifecycle = None
        with get_conn() as conn:
            lifecycle = conn.execute("""
                SELECT entry_window, fuel_quality, move_phase, first_leg_confirmed,
                       COALESCE(provisional_first_leg, 0) AS provisional_first_leg
                FROM symbol_lifecycle
                WHERE mint = ?
                ORDER BY last_computed_at DESC
                LIMIT 1
            """, (mint,)).fetchone()
            if lifecycle is None:
                lifecycle = conn.execute("""
                    SELECT entry_window, fuel_quality, move_phase, first_leg_confirmed,
                           COALESCE(provisional_first_leg, 0) AS provisional_first_leg
                    FROM symbol_lifecycle
                    WHERE symbol = ?
                    ORDER BY last_computed_at DESC
                    LIMIT 1
                """, (symbol,)).fetchone()

        try:
            opened_dt = datetime.fromisoformat(str(row["opened_ts_utc"]).replace("Z", "+00:00"))
            age_h = (datetime.now(timezone.utc) - opened_dt).total_seconds() / 3600
        except Exception:
            age_h = 0.0

        if is_proof:
            proof_stop_pct = float(os.getenv("MEMECOIN_PROOF_STOP_PCT", "25"))
            proof_review_h = float(os.getenv("MEMECOIN_PROOF_REVIEW_HOURS", "24"))
            entry_window = str(lifecycle["entry_window"] or "").upper() if lifecycle else ""

            if pnl_pct >= 100.0:
                sell_memecoin(mint, "TP_2X")
                continue
            if pnl_pct <= -abs(proof_stop_pct):
                sell_memecoin(mint, f"PROOF_SL_{int(abs(proof_stop_pct))}")
                continue
            if entry_window and entry_window != "OPEN" and pnl_pct <= 0:
                sell_memecoin(mint, "PROOF_WINDOW_LOST")
                continue
            if age_h >= proof_review_h and pnl_pct <= 0:
                sell_memecoin(mint, "PROOF_TIME_REVIEW")
                continue
            continue

        if pnl_pct >= 100.0:
            sell_memecoin(mint, "TP_2X")
        elif pnl_pct <= -50.0:
            sell_memecoin(mint, "SL_50")

    # Learning loop — fill in actual returns for tracked signals
    try:
        memecoin_outcome_step()
    except Exception:
        pass

    # Auto-tuner — adjusts thresholds once 20+ complete samples exist
    try:
        _tune_thresholds_step()
    except Exception:
        pass


# ── Status ────────────────────────────────────────────────────────────────────

def memecoin_status() -> dict:
    """
    Returns current scanner signals + open positions + stats +
    learned thresholds + recent closed trades.
    Called by GET /api/memecoins/status.
    """
    signals = get_cached_signals()

    # Open positions with live P&L
    positions = []
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT id, opened_ts_utc, symbol, mint, entry_price, amount_usd, token_amount
            FROM memecoin_trades
            WHERE status = 'OPEN'
            ORDER BY opened_ts_utc DESC
        """).fetchall()

    for row in rows:
        mint        = row["mint"]
        entry_price = float(row["entry_price"] or 0)
        current     = _fetch_token_price(mint)
        pnl_pct     = 0.0
        pnl_usd     = 0.0
        if entry_price > 0 and current > 0:
            pnl_pct = round((current - entry_price) / entry_price * 100, 2)
            pnl_usd = round(float(row["amount_usd"] or 0) * pnl_pct / 100, 4)

        positions.append({
            "id":            row["id"],
            "mint":          mint,
            "symbol":        row["symbol"],
            "entry_price":   entry_price,
            "current_price": current,
            "pnl_pct":       pnl_pct,
            "pnl_usd":       pnl_usd,
            "amount_usd":    float(row["amount_usd"] or 0),
            "opened":        row["opened_ts_utc"],
        })

    # Stats from closed trades
    with get_conn() as conn:
        closed = conn.execute("""
            SELECT pnl_pct, pnl_usd FROM memecoin_trades
            WHERE status = 'CLOSED'
        """).fetchall()

    total_pnl    = sum(float(r["pnl_usd"] or 0) for r in closed)
    wins         = sum(1 for r in closed if (r["pnl_pct"] or 0) > 0)
    closed_count = len(closed)
    win_rate     = round(wins / closed_count * 100, 1) if closed_count > 0 else 0.0

    # Recent closed trades (last 8) with PnL
    recent_closed = []
    with get_conn() as conn:
        rc_rows = conn.execute("""
            SELECT symbol, mint, pnl_pct, pnl_usd, exit_reason, closed_ts_utc
            FROM memecoin_trades
            WHERE status = 'CLOSED'
            ORDER BY closed_ts_utc DESC LIMIT 8
        """).fetchall()
    for r in rc_rows:
        recent_closed.append({
            "symbol":      r["symbol"],
            "mint":        r["mint"],
            "pnl_pct":     float(r["pnl_pct"] or 0),
            "pnl_usd":     float(r["pnl_usd"] or 0),
            "exit_reason": r["exit_reason"] or "MANUAL",
            "closed_at":   r["closed_ts_utc"],
        })

    # Learned thresholds from kv_store
    learned_thresholds = None
    try:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT value FROM kv_store WHERE key='memecoin_learned_thresholds'"
            ).fetchone()
            if row:
                learned_thresholds = json.loads(row["value"])
    except Exception:
        pass

    return {
        "signals":             signals,
        "positions":           positions,
        "stats": {
            "win_rate":     win_rate,
            "total_pnl":    round(total_pnl, 2),
            "closed_count": closed_count,
        },
        "recent_closed":       recent_closed,
        "learned_thresholds":  learned_thresholds,
    }
