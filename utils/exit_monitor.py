"""
utils/exit_monitor.py — Phase 5 Step 2: Spot & Memecoin Exit Monitor

Periodically checks open spot holdings and memecoin trades for exit conditions.
When a condition fires, logs an execution intent and optionally auto-executes.

Discipline:
  - Exits are always rank-0 (never blocked by authority), but still logged for audit
  - OBSERVE mode (default): log intent, do not execute
  - EXECUTE mode: log intent + call sell function
  - Controlled by env vars: SPOT_AUTO_EXIT, MEMECOIN_AUTO_EXIT

Exit conditions (conservative, price-based):
  Spot:
    - stop_loss: unrealized loss > SPOT_EXIT_STOP_PCT (default 15%)
  Memecoin:
    - stop_loss: unrealized loss > MEMECOIN_EXIT_STOP_PCT (default 30%)
    - rug_emergency: price dropped > 80% from entry (fast-exit)

Design principles:
  - Never blocks on errors (fail-open for monitoring)
  - Logs every intent via record_execution_intent
  - Calls resolve_action for authority audit trail
  - Respects cooldown (one check per symbol per 5 min)
  - Does NOT duplicate or replace manual sell paths
"""

import logging
import os
import sqlite3
import json
from datetime import datetime, timezone

from utils.db import (
    get_latest_memecoin_token_stats_for_mints,
    get_recent_large_trade_support_for_mints,
    record_memecoin_exit_review,
    record_memecoin_exit_signal_snapshot,
    update_memecoin_trade_excursions,
)

logger = logging.getLogger(__name__)

# ── Exit condition thresholds (from env, with conservative defaults) ──────────

def _spot_stop_pct() -> float:
    return float(os.getenv("SPOT_EXIT_STOP_PCT", "15"))

def _memecoin_stop_pct() -> float:
    return float(os.getenv("MEMECOIN_EXIT_STOP_PCT", "30"))

def _spot_auto_exit() -> bool:
    return os.getenv("SPOT_AUTO_EXIT", "false").lower() == "true"

def _memecoin_auto_exit() -> bool:
    return os.getenv("MEMECOIN_AUTO_EXIT", "false").lower() == "true"

def _memecoin_review_interval_s() -> int:
    try:
        return max(300, int(float(os.getenv("MEMECOIN_EXIT_REVIEW_INTERVAL_MINUTES", "30"))) * 60)
    except Exception:
        return 1800

def _memecoin_tp_pct() -> float:
    return float(os.getenv("MEMECOIN_PROOF_TP_PCT", "12"))

def _memecoin_tp_min_h() -> float:
    return float(os.getenv("MEMECOIN_PROOF_TP_MIN_HOURS", "4"))

def _memecoin_stale_exit_h() -> float:
    return float(os.getenv("MEMECOIN_PROOF_MAX_HOLD_HOURS", "24"))

def _memecoin_stale_win_pct() -> float:
    return float(os.getenv("MEMECOIN_PROOF_STALE_WIN_PCT", "5"))

def _memecoin_score_drop_exit() -> float:
    return float(os.getenv("MEMECOIN_PROOF_SCORE_DROP_EXIT", "20"))

def _memecoin_readiness_drop_exit() -> float:
    return float(os.getenv("MEMECOIN_READINESS_DROP_EXIT", "18"))

def _memecoin_derisk_pct() -> float:
    try:
        return max(5.0, min(95.0, float(os.getenv("MEMECOIN_PROOF_DE_RISK_PCT", "50"))))
    except Exception:
        return 50.0

def _memecoin_tp1_pct() -> float:
    return float(os.getenv("MEMECOIN_PROOF_TP1_PCT", "12"))

def _memecoin_tp1_min_h() -> float:
    return float(os.getenv("MEMECOIN_PROOF_TP1_MIN_HOURS", "2"))

def _memecoin_tp1_sell_pct() -> float:
    try:
        return max(5.0, min(95.0, float(os.getenv("MEMECOIN_PROOF_TP1_SELL_PCT", "35"))))
    except Exception:
        return 35.0

def _memecoin_tp2_pct() -> float:
    return float(os.getenv("MEMECOIN_PROOF_TP2_PCT", "25"))

def _memecoin_tp2_min_h() -> float:
    return float(os.getenv("MEMECOIN_PROOF_TP2_MIN_HOURS", "6"))

def _memecoin_tp2_sell_pct() -> float:
    try:
        return max(5.0, min(95.0, float(os.getenv("MEMECOIN_PROOF_TP2_SELL_PCT", "35"))))
    except Exception:
        return 35.0

def _memecoin_runner_hold_pullback_pct() -> float:
    return float(os.getenv("MEMECOIN_RUNNER_HOLD_PULLBACK_PCT", "8"))

def _memecoin_runner_pullback_exit_pct() -> float:
    return float(os.getenv("MEMECOIN_RUNNER_PULLBACK_EXIT_PCT", "10"))

def _memecoin_runner_hold_readiness_floor() -> float:
    return float(os.getenv("MEMECOIN_RUNNER_HOLD_READINESS_FLOOR", "-8"))

def _memecoin_runner_hold_support_floor() -> float:
    return float(os.getenv("MEMECOIN_RUNNER_HOLD_SUPPORT_FLOOR", "-6"))

# ── Cooldown tracking (in-memory, per-symbol) ────────────────────────────────

_last_check: dict[str, float] = {}
_COOLDOWN_S = 300  # default: 5 minutes between checks per symbol


def _on_cooldown(key: str, window_s: int | None = None) -> bool:
    now = datetime.now(tz=timezone.utc).timestamp()
    last = _last_check.get(key, 0)
    effective_window = max(1, int(window_s or _COOLDOWN_S))
    if now - last < effective_window:
        return True
    _last_check[key] = now
    return False


# ── DB helpers ────────────────────────────────────────────────────────────────

def _get_conn():
    db_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "data_storage", "engine.db"
    )
    conn = sqlite3.connect(db_path, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=3000")
    return conn


def _get_open_spot_holdings() -> list[dict]:
    """Read spot holdings that have tokens (non-zero balance)."""
    try:
        conn = _get_conn()
        try:
            rows = conn.execute(
                "SELECT symbol, mint, token_amount, avg_cost_usd FROM spot_holdings WHERE token_amount > 0"
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()
    except Exception:
        return []


def _get_open_memecoin_trades() -> list[dict]:
    """Read open memecoin trades."""
    try:
        conn = _get_conn()
        try:
            rows = conn.execute(
                "SELECT id, symbol, mint, entry_price, amount_usd, token_amount, opened_ts_utc, "
                "proof_score, proof_reason, proof_snapshot_json, initial_amount_usd, realized_release_usd, "
                "entry_support_score, entry_market_quality_score, entry_market_quality_verdict, "
                "entry_profit_room_score, entry_profit_room_label, entry_wallet_behavior_state, "
                "entry_smart_money_quality, entry_attribution_json, "
                "max_favorable_excursion_pct, max_adverse_excursion_pct "
                "FROM memecoin_trades WHERE status = 'OPEN'"
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()
    except Exception:
        return []


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    raw = str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S.%f"):
        try:
            dt = datetime.strptime(raw, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except Exception:
            continue
    try:
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _get_latest_proof_context(mints: list[str]) -> dict[str, dict]:
    if not mints:
        return {}
    try:
        from utils.memecoin_manager import get_proof_candidate_snapshot  # type: ignore[import]
        snapshot = get_proof_candidate_snapshot(limit=max(25, len(mints) * 8))
        out: dict[str, dict] = {}
        for candidate in list(snapshot.get("candidates") or []):
            mint = str(candidate.get("mint") or "").strip()
            if mint and mint in mints:
                out[mint] = dict(candidate)
        return out
    except Exception:
        return {}


def _readiness_from_snapshot(payload: object) -> tuple[float | None, str | None]:
    try:
        parsed = json.loads(str(payload or "")) if payload else {}
        if not isinstance(parsed, dict):
            return None, None
        readiness = dict((parsed.get("readiness") or {}))
        score = readiness.get("readiness_score")
        level = readiness.get("readiness_level")
        return (float(score) if score is not None else None), (str(level) if level else None)
    except Exception:
        return None, None


def _entry_attribution_from_trade(trade: dict) -> dict:
    raw = trade.get("entry_attribution_json")
    if not raw:
        return {}
    try:
        parsed = json.loads(str(raw))
        return dict(parsed) if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _volume_trend_label(token_stats: dict | None, large_trade_ctx: dict | None) -> str:
    stats = dict(token_stats or {})
    trade_ctx = dict(large_trade_ctx or {})
    vol_change = float(stats.get("volume_1h_change_percent") or 0.0)
    trade_change = float(stats.get("trade_1h_change_percent") or 0.0)
    buy_volume = float(stats.get("volume_buy_1h_usd") or 0.0)
    sell_volume = float(stats.get("volume_sell_1h_usd") or 0.0)
    if buy_volume <= 0.0 and sell_volume <= 0.0:
        buy_volume = float(trade_ctx.get("buy_volume_usd") or 0.0)
        sell_volume = float(trade_ctx.get("sell_volume_usd") or 0.0)

    if vol_change >= 25.0 or trade_change >= 20.0:
        if buy_volume > sell_volume * 1.05:
            return "ACCELERATING"
        if sell_volume > buy_volume * 1.05:
            return "ACTIVE_SELLING"
        return "ACTIVE"
    if vol_change <= -20.0 or trade_change <= -20.0:
        return "ROLLING_OVER"
    if buy_volume > sell_volume * 1.15:
        return "BUY_DOMINANT"
    if sell_volume > buy_volume * 1.15:
        return "SELL_DOMINANT"
    if stats or trade_ctx:
        return "STEADY"
    return "UNKNOWN"


def _scanner_still_actionable(
    latest: dict,
    *,
    current_readiness_score: float | None,
    current_market_quality_verdict: str | None,
    current_profit_room_label: str | None,
) -> bool:
    status = str(latest.get("proof_status") or latest.get("status") or "").upper()
    if status in ("PROOF_READY", "IN_PROOF_TRADE", "REINFORCED_PENDING"):
        return True
    if current_readiness_score is None:
        return False
    if current_readiness_score < 60.0:
        return False
    if str(current_market_quality_verdict or "").upper() in ("AVOID", "UNSTABLE"):
        return False
    if str(current_profit_room_label or "").upper() == "TOO_LATE":
        return False
    return True


def _build_memecoin_exit_intelligence(
    *,
    current_return_pct: float,
    max_favorable_excursion_pct: float | None,
    age_hours: float | None,
    tp1_min_h: float,
    tp1_pct: float,
    volume_trend_label: str,
    scanner_still_actionable: bool,
    current_market_quality_verdict: str | None,
    current_profit_room_label: str | None,
    readiness_delta: float | None,
    support_score_delta: float | None,
    profit_room_score_delta: float | None,
) -> dict:
    mfe_pct = float(max_favorable_excursion_pct) if max_favorable_excursion_pct is not None else None
    pullback_from_peak_pct = None
    capture_ratio = None
    if mfe_pct is not None and mfe_pct > 0:
        pullback_from_peak_pct = round(max(0.0, mfe_pct - current_return_pct), 2)
        capture_ratio = round(current_return_pct / mfe_pct, 3)

    weak_volume = volume_trend_label in {"ROLLING_OVER", "SELL_DOMINANT", "ACTIVE_SELLING"}
    healthy_volume = volume_trend_label in {"ACCELERATING", "BUY_DOMINANT", "ACTIVE", "STEADY"}
    market_ok = str(current_market_quality_verdict or "").upper() not in {"AVOID", "UNSTABLE"}
    profit_room_ok = str(current_profit_room_label or "").upper() != "TOO_LATE"
    readiness_ok = readiness_delta is None or readiness_delta >= _memecoin_runner_hold_readiness_floor()
    support_ok = support_score_delta is None or support_score_delta >= _memecoin_runner_hold_support_floor()
    modest_pullback = pullback_from_peak_pct is None or pullback_from_peak_pct <= _memecoin_runner_hold_pullback_pct()

    can_hold_runner = all([
        scanner_still_actionable,
        market_ok,
        profit_room_ok,
        healthy_volume,
        readiness_ok,
        support_ok,
        modest_pullback,
    ])

    meets_pullback_review_window = (
        current_return_pct > 0
        and mfe_pct is not None
        and mfe_pct >= max(tp1_pct, 10.0)
        and (age_hours is None or age_hours >= tp1_min_h)
        and pullback_from_peak_pct is not None
        and pullback_from_peak_pct >= _memecoin_runner_pullback_exit_pct()
    )
    fade_present = (
        (not scanner_still_actionable)
        or weak_volume
        or (readiness_delta is not None and readiness_delta <= -10.0)
        or (support_score_delta is not None and support_score_delta <= -8.0)
        or (profit_room_score_delta is not None and profit_room_score_delta <= -10.0)
    )
    should_lock_runner = meets_pullback_review_window and fade_present

    return {
        "mfe_pct": mfe_pct,
        "pullback_from_peak_pct": pullback_from_peak_pct,
        "capture_ratio": capture_ratio,
        "can_hold_runner": can_hold_runner,
        "should_lock_runner": should_lock_runner,
        "weak_volume": weak_volume,
        "healthy_volume": healthy_volume,
    }


def _get_cached_price(symbol: str) -> float | None:
    """Read cached price from kv_store (written by spot_monitor_step)."""
    try:
        conn = _get_conn()
        try:
            import json
            row = conn.execute(
                "SELECT value FROM kv_store WHERE key = 'spot_enriched_cache'"
            ).fetchone()
            if not row:
                return None
            cache = json.loads(row["value"])
            for item in cache.get("holdings", []):
                if str(item.get("symbol", "")).upper() == symbol.upper():
                    price = item.get("current_price") or item.get("price")
                    return float(price) if price else None
            return None
        finally:
            conn.close()
    except Exception:
        return None


# ── Core monitor steps ────────────────────────────────────────────────────────

def spot_exit_monitor_step() -> int:
    """
    Check open spot holdings for exit conditions.
    Returns number of intents logged.
    """
    holdings = _get_open_spot_holdings()
    if not holdings:
        return 0

    stop_pct = _spot_stop_pct()
    auto_exec = _spot_auto_exit()
    mode = "EXECUTE" if auto_exec else "OBSERVE"
    intents_logged = 0

    for h in holdings:
        symbol = h.get("symbol", "")
        mint = h.get("mint", "")
        avg_cost = h.get("avg_cost_usd")
        if not symbol or not avg_cost or float(avg_cost) <= 0:
            continue

        if _on_cooldown(f"spot_exit_{symbol}"):
            continue

        current_price = _get_cached_price(symbol)
        if current_price is None or current_price <= 0:
            continue

        avg_cost = float(avg_cost)
        unrealized_pct = ((current_price - avg_cost) / avg_cost) * 100

        # ── Evaluate exit conditions ──────────────────────────────────────
        exit_reason = None
        if unrealized_pct <= -stop_pct:
            exit_reason = f"stop_loss — unrealized {unrealized_pct:+.1f}% exceeds -{stop_pct}% threshold"

        if not exit_reason:
            continue

        # ── Pipeline: authorize → execute → audit ────────────────────────
        _exec_fn = None
        if auto_exec:
            _sym, _mnt = symbol, mint
            def _exec_fn():
                from utils.spot_accumulator import sell_spot  # type: ignore[import]
                return sell_spot(_sym, _mnt, pct=100.0)

        try:
            from execution_pipeline import propose_and_execute
            r = propose_and_execute(
                source="spot_exit_monitor",
                lane="spot",
                action_type="close_position",
                symbol=symbol,
                mode=mode,
                execute_fn=_exec_fn,
                notes=exit_reason,
            )
            intents_logged += 1
        except Exception:
            pass

    return intents_logged


def memecoin_exit_monitor_step() -> int:
    """
    Check open memecoin trades for exit conditions.
    Returns number of intents logged.
    """
    trades = _get_open_memecoin_trades()
    if not trades:
        return 0

    stop_pct = _memecoin_stop_pct()
    auto_exec = _memecoin_auto_exit()
    mode = "EXECUTE" if auto_exec else "OBSERVE"
    intents_logged = 0
    active_mints = [str(t.get("mint") or "") for t in trades if str(t.get("mint") or "").strip()]
    latest_context = _get_latest_proof_context(active_mints)
    try:
        token_stats_by_mint = get_latest_memecoin_token_stats_for_mints(active_mints, max_age_minutes=90) or {}
    except Exception:
        token_stats_by_mint = {}
    try:
        large_trade_support_by_mint = get_recent_large_trade_support_for_mints(active_mints, max_age_minutes=180) or {}
    except Exception:
        large_trade_support_by_mint = {}
    review_interval_s = _memecoin_review_interval_s()
    tp_pct = _memecoin_tp_pct()
    tp_min_h = _memecoin_tp_min_h()
    stale_exit_h = _memecoin_stale_exit_h()
    stale_win_pct = _memecoin_stale_win_pct()
    score_drop_exit = _memecoin_score_drop_exit()
    readiness_drop_exit = _memecoin_readiness_drop_exit()
    derisk_pct = _memecoin_derisk_pct()
    tp1_pct = _memecoin_tp1_pct()
    tp1_min_h = _memecoin_tp1_min_h()
    tp1_sell_pct = _memecoin_tp1_sell_pct()
    tp2_pct = _memecoin_tp2_pct()
    tp2_min_h = _memecoin_tp2_min_h()
    tp2_sell_pct = _memecoin_tp2_sell_pct()

    for t in trades:
        symbol = t.get("symbol", "")
        mint = t.get("mint", "")
        entry_price = t.get("entry_price")
        if not mint or not entry_price or float(entry_price) <= 0:
            continue

        if _on_cooldown(f"mc_exit_review_{mint}:{t.get('id')}", review_interval_s):
            continue

        # Try to get current price via cached data or simple fetch
        current_price = _get_memecoin_price(mint)
        if current_price is None or current_price <= 0:
            continue

        entry_price = float(entry_price)
        unrealized_pct = ((current_price - entry_price) / entry_price) * 100
        opened_ts = _parse_ts(str(t.get("opened_ts_utc") or ""))
        age_hours = None
        if opened_ts is not None:
            age_hours = max(0.0, (datetime.now(tz=timezone.utc) - opened_ts).total_seconds() / 3600.0)
        age_minutes = round(age_hours * 60.0, 2) if age_hours is not None else None
        entry_proof_score = float(t.get("proof_score") or 0.0) if t.get("proof_score") is not None else None
        entry_readiness_score, _entry_readiness_level = _readiness_from_snapshot(t.get("proof_snapshot_json"))
        latest = latest_context.get(mint, {})
        entry_attr = _entry_attribution_from_trade(t)
        token_stats = dict(token_stats_by_mint.get(mint) or {})
        large_trade_support = dict(large_trade_support_by_mint.get(mint) or {})
        current_scanner_score = (
            float(latest.get("score") or latest.get("scanner_score") or 0.0)
            if (latest.get("score") is not None or latest.get("scanner_score") is not None)
            else None
        )
        current_timing_score = float(latest.get("timing_score") or 0.0) if latest.get("timing_score") is not None else None
        current_proof_score = float(latest.get("proof_score") or 0.0) if latest.get("proof_score") is not None else None
        proof_score_delta = None
        if entry_proof_score is not None and current_proof_score is not None:
            proof_score_delta = round(current_proof_score - entry_proof_score, 2)
        current_proof_reason = str(latest.get("proof_reason") or "") or None
        current_readiness_score = float(latest.get("readiness_score") or 0.0) if latest.get("readiness_score") is not None else None
        current_readiness_level = str(latest.get("readiness_level") or "") or None
        readiness_delta = None
        if entry_readiness_score is not None and current_readiness_score is not None:
            readiness_delta = round(current_readiness_score - entry_readiness_score, 2)
        entry_support_score = float(t.get("entry_support_score")) if t.get("entry_support_score") is not None else (
            float(entry_attr.get("support_score")) if entry_attr.get("support_score") is not None else None
        )
        current_support_score = float(latest.get("support_score") or 0.0) if latest.get("support_score") is not None else None
        support_score_delta = round(current_support_score - entry_support_score, 2) if (entry_support_score is not None and current_support_score is not None) else None
        entry_market_quality_score = float(t.get("entry_market_quality_score")) if t.get("entry_market_quality_score") is not None else (
            float(entry_attr.get("market_quality_score")) if entry_attr.get("market_quality_score") is not None else None
        )
        current_market_quality_score = float(latest.get("market_quality_score") or 0.0) if latest.get("market_quality_score") is not None else None
        market_quality_score_delta = round(current_market_quality_score - entry_market_quality_score, 2) if (entry_market_quality_score is not None and current_market_quality_score is not None) else None
        entry_market_quality_verdict = str(t.get("entry_market_quality_verdict") or entry_attr.get("market_quality_verdict") or "") or None
        current_market_quality_verdict = str(latest.get("market_quality_verdict") or "") or None
        entry_profit_room_score = float(t.get("entry_profit_room_score")) if t.get("entry_profit_room_score") is not None else (
            float(entry_attr.get("profit_room_score")) if entry_attr.get("profit_room_score") is not None else None
        )
        current_profit_room_score = float(latest.get("profit_room_score") or 0.0) if latest.get("profit_room_score") is not None else None
        profit_room_score_delta = round(current_profit_room_score - entry_profit_room_score, 2) if (entry_profit_room_score is not None and current_profit_room_score is not None) else None
        entry_profit_room_label = str(t.get("entry_profit_room_label") or entry_attr.get("profit_room_label") or "") or None
        current_profit_room_label = str(latest.get("profit_room_label") or "") or None
        entry_wallet_behavior_state = str(t.get("entry_wallet_behavior_state") or entry_attr.get("wallet_behavior_state") or "") or None
        current_wallet_behavior_state = str(latest.get("wallet_behavior_state") or "") or None
        entry_smart_money_quality = str(t.get("entry_smart_money_quality") or entry_attr.get("smart_money_quality") or "") or None
        current_smart_money_quality = str(latest.get("smart_money_quality") or "") or None
        scanner_regime = str(latest.get("scanner_regime") or "NORMAL")
        volume_trend_label = _volume_trend_label(token_stats, large_trade_support)
        scanner_still_actionable = _scanner_still_actionable(
            latest,
            current_readiness_score=current_readiness_score,
            current_market_quality_verdict=current_market_quality_verdict,
            current_profit_room_label=current_profit_room_label,
        )
        initial_amount_usd = float(t.get("initial_amount_usd") or 0.0) if t.get("initial_amount_usd") is not None else float(t.get("amount_usd") or 0.0)
        realized_release_usd = float(t.get("realized_release_usd") or 0.0)
        released_pct = (realized_release_usd / initial_amount_usd * 100.0) if initial_amount_usd > 0 else 0.0
        remaining_pct_capacity = max(0.0, 100.0 - released_pct)
        max_favorable_excursion_pct = float(t.get("max_favorable_excursion_pct")) if t.get("max_favorable_excursion_pct") is not None else None
        max_adverse_excursion_pct = float(t.get("max_adverse_excursion_pct")) if t.get("max_adverse_excursion_pct") is not None else None
        exit_intelligence = _build_memecoin_exit_intelligence(
            current_return_pct=float(unrealized_pct),
            max_favorable_excursion_pct=max_favorable_excursion_pct,
            age_hours=age_hours,
            tp1_min_h=tp1_min_h,
            tp1_pct=tp1_pct,
            volume_trend_label=volume_trend_label,
            scanner_still_actionable=scanner_still_actionable,
            current_market_quality_verdict=current_market_quality_verdict,
            current_profit_room_label=current_profit_room_label,
            readiness_delta=readiness_delta,
            support_score_delta=support_score_delta,
            profit_room_score_delta=profit_room_score_delta,
        )

        # ── Evaluate exit conditions ──────────────────────────────────────
        exit_reason = None
        review_state = "HOLD"
        recommended_action = "hold_position"
        recommended_pct = None
        if unrealized_pct <= -80:
            exit_reason = f"rug_emergency — price dropped {unrealized_pct:+.1f}% from entry (>80% loss)"
            review_state = "EXIT_NOW"
            recommended_action = "close_position"
            recommended_pct = 100.0
        elif unrealized_pct <= -stop_pct:
            exit_reason = f"stop_loss — unrealized {unrealized_pct:+.1f}% exceeds -{stop_pct}% threshold"
            review_state = "EXIT_NOW"
            recommended_action = "close_position"
            recommended_pct = 100.0
        elif (
            age_hours is not None
            and age_hours >= tp2_min_h
            and unrealized_pct >= tp2_pct
            and released_pct + 1e-6 < tp1_sell_pct + tp2_sell_pct
            and remaining_pct_capacity > 0
        ):
            tp_pct_now = min(tp2_sell_pct, remaining_pct_capacity)
            exit_reason = (
                f"tier_two_take_profit — open {age_hours:.1f}h and return {unrealized_pct:+.1f}% "
                f"clears TP2 {tp2_pct:.1f}%"
            )
            review_state = "TAKE_PROFIT"
            recommended_action = "reduce_risk"
            recommended_pct = tp_pct_now
        elif (
            age_hours is not None
            and age_hours >= tp1_min_h
            and unrealized_pct >= tp1_pct
            and released_pct + 1e-6 < tp1_sell_pct
            and remaining_pct_capacity > 0
        ):
            tp_pct_now = min(tp1_sell_pct, remaining_pct_capacity)
            exit_reason = (
                f"tier_one_take_profit — open {age_hours:.1f}h and return {unrealized_pct:+.1f}% "
                f"clears TP1 {tp1_pct:.1f}%"
            )
            review_state = "TAKE_PROFIT"
            recommended_action = "reduce_risk"
            recommended_pct = tp_pct_now
        elif exit_intelligence.get("should_lock_runner") and remaining_pct_capacity > 0:
            pullback_pct = float(exit_intelligence.get("pullback_from_peak_pct") or 0.0)
            mfe_pct = float(exit_intelligence.get("mfe_pct") or unrealized_pct)
            exit_reason = (
                f"winner_rollover — return {unrealized_pct:+.1f}% has given back {pullback_pct:.1f}% "
                f"from peak excursion {mfe_pct:+.1f}% while follow-through weakens"
            )
            review_state = "DE_RISK"
            recommended_action = "reduce_risk"
            recommended_pct = min(derisk_pct, remaining_pct_capacity)
        elif age_hours is not None and age_hours >= stale_exit_h and unrealized_pct >= stale_win_pct:
            if exit_intelligence.get("can_hold_runner"):
                exit_reason = (
                    f"runner_healthy — open {age_hours:.1f}h with return {unrealized_pct:+.1f}% "
                    f"and follow-through still intact despite stale timer"
                )
                review_state = "HOLD"
                recommended_action = "hold_position"
                recommended_pct = None
            else:
                exit_reason = (
                    f"stale_winner — open {age_hours:.1f}h and return {unrealized_pct:+.1f}% "
                    f"clears stale-win floor {stale_win_pct:.1f}%"
                )
                review_state = "EXIT_READY"
                recommended_action = "close_position"
                recommended_pct = 100.0
        elif age_hours is not None and age_hours >= tp_min_h and unrealized_pct >= tp_pct:
            if exit_intelligence.get("can_hold_runner"):
                exit_reason = (
                    f"runner_healthy — return {unrealized_pct:+.1f}% has cleared profit target "
                    f"but signal still looks actionable"
                )
                review_state = "HOLD"
                recommended_action = "hold_position"
                recommended_pct = None
            else:
                exit_reason = (
                    f"time_target_hit — open {age_hours:.1f}h and return {unrealized_pct:+.1f}% "
                    f"clears profit target {tp_pct:.1f}%"
                )
                review_state = "EXIT_READY"
                recommended_action = "close_position"
                recommended_pct = 100.0
        elif readiness_delta is not None and readiness_delta <= -readiness_drop_exit:
            if unrealized_pct > 0:
                exit_reason = (
                    f"readiness_fade — readiness {readiness_delta:+.0f} from entry "
                    f"while return is {unrealized_pct:+.1f}%"
                )
                review_state = "DE_RISK"
                recommended_action = "reduce_risk"
                recommended_pct = min(derisk_pct, remaining_pct_capacity) if remaining_pct_capacity > 0 else None
            elif unrealized_pct <= 0:
                exit_reason = (
                    f"readiness_fade_loss — readiness {readiness_delta:+.0f} from entry "
                    f"with return {unrealized_pct:+.1f}%"
                )
                review_state = "EXIT_NOW"
                recommended_action = "close_position"
                recommended_pct = 100.0
        elif proof_score_delta is not None and proof_score_delta <= -score_drop_exit:
            if unrealized_pct > 0:
                exit_reason = (
                    f"legacy_proof_fade — proof score {proof_score_delta:+.0f} from entry "
                    f"while return is {unrealized_pct:+.1f}%"
                )
                review_state = "DE_RISK"
                recommended_action = "reduce_risk"
                recommended_pct = min(derisk_pct, remaining_pct_capacity) if remaining_pct_capacity > 0 else None
            elif unrealized_pct <= 0:
                exit_reason = (
                    f"legacy_proof_fade_loss — proof score {proof_score_delta:+.0f} from entry "
                    f"with return {unrealized_pct:+.1f}%"
                )
                review_state = "EXIT_NOW"
                recommended_action = "close_position"
                recommended_pct = 100.0

        should_exit = bool(exit_reason and (recommended_pct or 0) > 0)
        notes = (
            f"return={unrealized_pct:+.2f}%"
            + (f" | age={age_hours:.1f}h" if age_hours is not None else "")
            + (f" | mfe={max_favorable_excursion_pct:+.1f}%" if max_favorable_excursion_pct is not None else "")
            + (f" | mae={max_adverse_excursion_pct:+.1f}%" if max_adverse_excursion_pct is not None else "")
            + (f" | pullback={float(exit_intelligence.get('pullback_from_peak_pct')):+.1f}%" if exit_intelligence.get("pullback_from_peak_pct") is not None else "")
            + (f" | proof_delta={proof_score_delta:+.1f}" if proof_score_delta is not None else "")
            + (f" | ready_delta={readiness_delta:+.1f}" if readiness_delta is not None else "")
            + (f" | room_delta={profit_room_score_delta:+.1f}" if profit_room_score_delta is not None else "")
            + (f" | support_delta={support_score_delta:+.1f}" if support_score_delta is not None else "")
            + (f" | ready={current_readiness_score:.1f}" if current_readiness_score is not None else "")
            + (f" | released={released_pct:.1f}%" if initial_amount_usd > 0 else "")
        )
        comparison_snapshot = json.dumps({
            "entry": {
                "readiness_score": entry_readiness_score,
                "support_score": entry_support_score,
                "market_quality_score": entry_market_quality_score,
                "market_quality_verdict": entry_market_quality_verdict,
                "profit_room_score": entry_profit_room_score,
                "profit_room_label": entry_profit_room_label,
                "wallet_behavior_state": entry_wallet_behavior_state,
                "smart_money_quality": entry_smart_money_quality,
            },
            "current": {
                "readiness_score": current_readiness_score,
                "readiness_level": current_readiness_level,
                "support_score": current_support_score,
                "market_quality_score": current_market_quality_score,
                "market_quality_verdict": current_market_quality_verdict,
                "profit_room_score": current_profit_room_score,
                "profit_room_label": current_profit_room_label,
                "wallet_behavior_state": current_wallet_behavior_state,
                "smart_money_quality": current_smart_money_quality,
            },
            "deltas": {
                "readiness": readiness_delta,
                "support": support_score_delta,
                "market_quality": market_quality_score_delta,
                "profit_room": profit_room_score_delta,
            },
            "excursions": {
                "max_favorable_excursion_pct": max_favorable_excursion_pct,
                "max_adverse_excursion_pct": max_adverse_excursion_pct,
                "pullback_from_peak_pct": exit_intelligence.get("pullback_from_peak_pct"),
                "capture_ratio": exit_intelligence.get("capture_ratio"),
            },
            "exit_intelligence": {
                "can_hold_runner": exit_intelligence.get("can_hold_runner"),
                "should_lock_runner": exit_intelligence.get("should_lock_runner"),
                "weak_volume": exit_intelligence.get("weak_volume"),
                "healthy_volume": exit_intelligence.get("healthy_volume"),
            },
        })
        signal_snapshot_notes = {
            "review_state": review_state,
            "recommended_action": recommended_action,
            "recommended_pct": recommended_pct,
            "scanner_regime": scanner_regime,
            "exit_intelligence": {
                "can_hold_runner": exit_intelligence.get("can_hold_runner"),
                "should_lock_runner": exit_intelligence.get("should_lock_runner"),
                "pullback_from_peak_pct": exit_intelligence.get("pullback_from_peak_pct"),
                "capture_ratio": exit_intelligence.get("capture_ratio"),
                "max_favorable_excursion_pct": max_favorable_excursion_pct,
                "max_adverse_excursion_pct": max_adverse_excursion_pct,
            },
            "token_stats": {
                "ts_utc": token_stats.get("ts_utc"),
                "price_change_1h_percent": token_stats.get("price_change_1h_percent"),
                "price_change_24h_percent": token_stats.get("price_change_24h_percent"),
                "volume_1h_usd": token_stats.get("volume_1h_usd"),
                "volume_1h_change_percent": token_stats.get("volume_1h_change_percent"),
                "trade_1h": token_stats.get("trade_1h"),
                "trade_1h_change_percent": token_stats.get("trade_1h_change_percent"),
                "unique_wallet_1h": token_stats.get("unique_wallet_1h"),
            },
            "large_trades": {
                "trade_count": large_trade_support.get("trade_count"),
                "buy_count": large_trade_support.get("buy_count"),
                "sell_count": large_trade_support.get("sell_count"),
                "buy_volume_usd": large_trade_support.get("buy_volume_usd"),
                "sell_volume_usd": large_trade_support.get("sell_volume_usd"),
                "buy_share_pct": large_trade_support.get("buy_share_pct"),
                "strong_labels": large_trade_support.get("strong_labels"),
                "last_seen_utc": large_trade_support.get("last_seen_utc"),
            },
        }
        record_memecoin_exit_signal_snapshot(
            trade_id=int(t["id"]),
            mint=mint,
            symbol=symbol,
            age_minutes=age_minutes,
            current_price=float(current_price),
            current_return_pct=round(float(unrealized_pct), 2),
            current_scanner_score=current_scanner_score,
            current_timing_score=current_timing_score,
            current_market_quality_score=current_market_quality_score,
            current_support_score=current_support_score,
            current_readiness_score=current_readiness_score,
            current_profit_room_score=current_profit_room_score,
            current_profit_room_label=current_profit_room_label,
            current_market_quality_verdict=current_market_quality_verdict,
            current_wallet_behavior_state=current_wallet_behavior_state,
            current_smart_money_quality=current_smart_money_quality,
            current_proof_score=current_proof_score,
            current_proof_reason=current_proof_reason,
            volume_trend_label=volume_trend_label,
            scanner_still_actionable=scanner_still_actionable,
            large_trade_buy_volume=float(large_trade_support.get("buy_volume_usd") or 0.0),
            large_trade_sell_volume=float(large_trade_support.get("sell_volume_usd") or 0.0),
            large_trade_buy_count=int(large_trade_support.get("buy_count") or 0),
            large_trade_sell_count=int(large_trade_support.get("sell_count") or 0),
            token_stats_ts_utc=str(token_stats.get("ts_utc") or "") or None,
            notes_json=signal_snapshot_notes,
        )
        update_memecoin_trade_excursions(
            trade_id=int(t["id"]),
            current_return_pct=round(float(unrealized_pct), 2),
            ts_utc=datetime.now(timezone.utc).isoformat(),
        )

        executed = False
        intent_id = None
        if should_exit:
            _exec_fn = None
            action_type = "reduce_risk" if recommended_action == "reduce_risk" else "close_position"
            if auto_exec:
                _mnt, _reason = mint, f"AUTO_EXIT:{exit_reason.split(' — ')[0]}"

                def _exec_fn():
                    from utils.memecoin_manager import sell_memecoin  # type: ignore[import]
                    return sell_memecoin(
                        _mnt,
                        reason=_reason,
                        pct=float(recommended_pct or (derisk_pct if action_type == "reduce_risk" else 100.0)),
                    )

            try:
                from execution_pipeline import propose_and_execute
                r = propose_and_execute(
                    source="memecoin_exit_monitor",
                    lane="memecoins",
                    action_type=action_type,
                    symbol=symbol,
                    mode=mode,
                    execute_fn=_exec_fn,
                    notes=exit_reason,
                )
                intents_logged += 1
                executed = bool(r.get("executed"))
                intent_id = r.get("intent_id")
            except Exception:
                pass

        record_memecoin_exit_review(
            trade_id=int(t["id"]),
            mint=mint,
            symbol=symbol,
            scanner_regime=scanner_regime,
            current_price=float(current_price),
            current_return_pct=round(float(unrealized_pct), 2),
            age_hours=round(float(age_hours), 2) if age_hours is not None else None,
            entry_proof_score=entry_proof_score,
            current_proof_score=current_proof_score,
            proof_score_delta=proof_score_delta,
            current_proof_reason=current_proof_reason,
            entry_readiness_score=entry_readiness_score,
            current_readiness_score=current_readiness_score,
            readiness_delta=readiness_delta,
            current_readiness_level=current_readiness_level,
            entry_support_score=entry_support_score,
            current_support_score=current_support_score,
            support_score_delta=support_score_delta,
            entry_market_quality_score=entry_market_quality_score,
            current_market_quality_score=current_market_quality_score,
            market_quality_score_delta=market_quality_score_delta,
            entry_market_quality_verdict=entry_market_quality_verdict,
            current_market_quality_verdict=current_market_quality_verdict,
            entry_profit_room_score=entry_profit_room_score,
            current_profit_room_score=current_profit_room_score,
            profit_room_score_delta=profit_room_score_delta,
            entry_profit_room_label=entry_profit_room_label,
            current_profit_room_label=current_profit_room_label,
            entry_wallet_behavior_state=entry_wallet_behavior_state,
            current_wallet_behavior_state=current_wallet_behavior_state,
            entry_smart_money_quality=entry_smart_money_quality,
            current_smart_money_quality=current_smart_money_quality,
            comparison_snapshot_json=comparison_snapshot,
            review_state=review_state,
            recommended_action=recommended_action,
            recommended_pct=recommended_pct,
            exit_reason=exit_reason,
            should_exit=should_exit,
            auto_exit_enabled=auto_exec,
            executed=executed,
            intent_id=int(intent_id) if intent_id else None,
            notes=notes,
        )

    return intents_logged


def _get_memecoin_price(mint: str) -> float | None:
    """Try to get memecoin price from Jupiter price API (cached in kv_store or fresh)."""
    try:
        import json
        conn = _get_conn()
        try:
            # Check kv_store for recent price cache
            row = conn.execute(
                "SELECT value FROM kv_store WHERE key = ?",
                (f"mc_price_{mint[:16]}",),
            ).fetchone()
            if row:
                cached = json.loads(row["value"])
                age_s = (datetime.now(tz=timezone.utc).timestamp() -
                         datetime.fromisoformat(cached["ts"]).timestamp())
                if age_s < 600:  # 10 min cache
                    return float(cached["price"])
        finally:
            conn.close()
    except Exception:
        pass

    # Fallback: fetch from Jupiter price API
    try:
        import requests
        resp = requests.get(
            f"https://api.jup.ag/price/v2?ids={mint}",
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json().get("data", {}).get(mint, {})
        price = data.get("price")
        if price:
            price = float(price)
            # Cache it
            try:
                import json as _json
                conn = _get_conn()
                try:
                    conn.execute(
                        "INSERT OR REPLACE INTO kv_store (key, value) VALUES (?, ?)",
                        (f"mc_price_{mint[:16]}",
                         _json.dumps({"price": price, "ts": datetime.now(tz=timezone.utc).isoformat()})),
                    )
                    conn.commit()
                finally:
                    conn.close()
            except Exception:
                pass
            return price
    except Exception:
        pass

    return None
