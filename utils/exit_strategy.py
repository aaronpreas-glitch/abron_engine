"""
exit_strategy.py — Adaptive exit rule engine.

Learns from alert_outcomes which exit timing produced the best historical
returns for a given (regime_label, score_range, confidence) profile.

Entry point: build_exit_plan(signal) → exit config dict
Check loop:  should_exit(trade, current_price, peak_price) → { exit, pct_to_sell, reason }
Learning:    update_exit_learnings(trade_id, exit_reason, pnl_pct)
"""

import json
import logging
import os
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ── Paths ──────────────────────────────────────────────────────────────────────

_DB_PATH = Path(__file__).parent.parent / "data_storage" / "engine.db"
_LEARNINGS_PATH = Path(__file__).parent.parent / "data_storage" / "exit_outcomes.json"
_PROFILES_PATH  = Path(__file__).parent.parent / "data_storage" / "exit_profiles.json"

# ── Defaults (conservative) ───────────────────────────────────────────────────

DEFAULT_STOP_LOSS_PCT   = float(os.getenv("STOP_LOSS_PCT", "0.18"))   # -18%
DEFAULT_TP1_PCT         = 0.25      # +25% → sell 40% of position
DEFAULT_TP1_SELL_PCT    = 0.40
DEFAULT_TP2_PCT         = 0.60      # +60% → sell another 40%
DEFAULT_TP2_SELL_PCT    = 0.40
DEFAULT_TRAILING_PCT    = 0.12      # 12% trail after TP1
DEFAULT_MAX_HOLD_HOURS  = float(os.getenv("MAX_HOLD_HOURS", "24"))
MIN_SAMPLES_TO_LEARN    = 5         # need at least 5 outcomes before adapting

# ── Scalp override — hard-coded tight parameters, bypass all learning ──────────
_SCALP_STOP_PCT     = -0.008   # -0.8% stop loss
_SCALP_TP1_PCT      =  0.015   # +1.5% take profit
_SCALP_TP1_SELL_PCT =  1.0     # 100% exit at TP1 (no partial)
_SCALP_TP2_PCT      =  0.015   # same as TP1 (signals no second level)
_SCALP_TP2_SELL_PCT =  0.0
_SCALP_TRAILING_PCT =  0.008   # 0.8% tight trail
_SCALP_MAX_HOLD_H   =  0.33    # 20 minutes max hold


# ── DB helper ─────────────────────────────────────────────────────────────────

_STALENESS_CUTOFF_DAYS = 30   # outcomes older than this get 0.8× weight in learning
_STALENESS_WEIGHT      = 0.80


def _query_outcomes(
    regime_label: str,
    score_min: float,
    score_max: float,
    confidence: str,
    lookback_days: int = 90,
) -> list[dict]:
    """
    Pull alert_outcomes matching the signal's profile.
    Outcomes older than _STALENESS_CUTOFF_DAYS are tagged with weight=0.8
    for use in weighted median calculations (staleness decay — A4).
    """
    if not _DB_PATH.exists():
        return []
    cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).isoformat()
    staleness_cutoff = (
        datetime.now(timezone.utc) - timedelta(days=_STALENESS_CUTOFF_DAYS)
    ).isoformat()
    try:
        con = sqlite3.connect(str(_DB_PATH))
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        cur.execute(
            """
            SELECT
                return_1h_pct,
                return_4h_pct,
                return_24h_pct,
                created_ts_utc
            FROM alert_outcomes
            WHERE regime_label = ?
              AND score >= ? AND score <= ?
              AND confidence = ?
              AND created_ts_utc >= ?
              AND status = 'COMPLETE'
            ORDER BY created_ts_utc DESC
            LIMIT 200
            """,
            (regime_label, score_min, score_max, confidence, cutoff),
        )
        rows = []
        for r in cur.fetchall():
            row = dict(r)
            ts = row.get("created_ts_utc", "")
            row["_weight"] = _STALENESS_WEIGHT if ts < staleness_cutoff else 1.0
            rows.append(row)
        con.close()
        return rows
    except Exception as exc:
        logger.warning("exit_strategy DB query failed: %s", exc)
        return []


def _load_exit_profiles() -> dict:
    """Load exit_profiles.json written by auto_tune.py weekly pass."""
    if not _PROFILES_PATH.exists():
        return {}
    try:
        return json.loads(_PROFILES_PATH.read_text())
    except Exception:
        return {}


# ── Exit plan builder ─────────────────────────────────────────────────────────

def build_exit_plan(signal: dict) -> dict:
    """
    Compute an adaptive exit plan for a new trade based on historical outcomes
    that share the same (regime_label, score ±10, confidence) profile.

    Falls back to conservative defaults if fewer than MIN_SAMPLES_TO_LEARN
    historical outcomes are found.

    Special case: if signal contains scalp_mode=True, returns hard-coded scalp
    parameters (tight TP/SL, short hold) bypassing the learning system entirely.
    """
    # ── Scalp mode: hard-coded tight parameters, no learning ──────────────────
    if signal.get("scalp_mode") is True:
        return {
            "stop_loss_pct":     _SCALP_STOP_PCT,
            "tp1_pct":           _SCALP_TP1_PCT,
            "tp1_sell_pct":      _SCALP_TP1_SELL_PCT,
            "tp2_pct":           _SCALP_TP2_PCT,
            "tp2_sell_pct":      _SCALP_TP2_SELL_PCT,
            "trailing_stop_pct": _SCALP_TRAILING_PCT,
            "max_hold_hours":    _SCALP_MAX_HOLD_H,
            "learned_from":      0,
            "best_horizon_h":    0,
            "profile_key":       "SCALP|fixed",
            "cycle_phase":       "SCALP",
        }

    Returns:
    {
        stop_loss_pct    — negative float, e.g. -0.18
        tp1_pct          — positive float, e.g. 0.25
        tp1_sell_pct     — fraction to sell at TP1, e.g. 0.40
        tp2_pct          — positive float, e.g. 0.60
        tp2_sell_pct     — fraction to sell at TP2, e.g. 0.40
        trailing_stop_pct — trail after TP1, e.g. 0.12
        max_hold_hours   — time-based failsafe
        learned_from     — n outcomes used to calibrate
        best_horizon_h   — 1 / 4 / 24 (whichever horizon had best median return)
        profile_key      — human-readable profile string
    }
    """
    regime       = signal.get("regime_label", "UNKNOWN")
    score        = float(signal.get("score", 0) or 0)
    conf         = signal.get("confidence", "C")
    cycle_phase  = signal.get("cycle_phase", "TRANSITION")   # Phase 3: market cycle

    score_min = max(0, score - 10)
    score_max = min(100, score + 10)

    outcomes = _query_outcomes(regime, score_min, score_max, conf)
    n = len(outcomes)

    profile_key = f"{regime}|score{score_min:.0f}-{score_max:.0f}|conf{conf}|{cycle_phase}"

    if n < MIN_SAMPLES_TO_LEARN:
        # Fall back to exit_profiles.json baseline for this regime (A4)
        profiles = _load_exit_profiles()
        regime_profile = profiles.get("by_regime", {}).get(regime)
        if regime_profile and regime_profile.get("count", 0) >= 3:
            logger.info(
                "exit_strategy: n=%d < %d for %s — using exit_profiles.json baseline",
                n, MIN_SAMPLES_TO_LEARN, profile_key,
            )
        else:
            logger.info(
                "exit_strategy: n=%d < %d for %s — using cycle defaults (%s)",
                n, MIN_SAMPLES_TO_LEARN, profile_key, cycle_phase,
            )
        return _default_plan(profile_key, n, cycle_phase=cycle_phase)

    # Extract non-null returns for each horizon (with staleness weights for medians)
    r1h  = [(float(r["return_1h_pct"]),  r["_weight"]) for r in outcomes if r.get("return_1h_pct")  is not None]
    r4h  = [(float(r["return_4h_pct"]),  r["_weight"]) for r in outcomes if r.get("return_4h_pct")  is not None]
    r24h = [(float(r["return_24h_pct"]), r["_weight"]) for r in outcomes if r.get("return_24h_pct") is not None]

    def median(pairs: list) -> float:
        """Staleness-weighted approximate median."""
        if not pairs:
            return 0.0
        # Weighted sorted: sort by value, accumulate weights until 50%
        sorted_pairs = sorted(pairs, key=lambda x: x[0])
        total_w = sum(w for _, w in sorted_pairs)
        cumulative = 0.0
        for val, w in sorted_pairs:
            cumulative += w
            if cumulative >= total_w / 2:
                return val
        return sorted_pairs[-1][0]

    def percentile(pairs: list, p: float) -> float:
        """Staleness-weighted approximate percentile."""
        if not pairs:
            return 0.0
        sorted_pairs = sorted(pairs, key=lambda x: x[0])
        total_w = sum(w for _, w in sorted_pairs)
        target_w = total_w * p / 100
        cumulative = 0.0
        for val, w in sorted_pairs:
            cumulative += w
            if cumulative >= target_w:
                return val
        return sorted_pairs[-1][0]

    med1h  = median(r1h)
    med4h  = median(r4h)
    med24h = median(r24h)

    # Best horizon = whichever has the highest weighted median return
    horizons = [(1, med1h, r1h), (4, med4h, r4h), (24, med24h, r24h)]
    best_h, best_med, best_returns = max(
        [(h, m, r) for h, m, r in horizons if r],
        key=lambda x: x[1],
        default=(24, 0.0, r24h or []),
    )

    # Extract plain values for percentile calculations (weights already applied in median)
    best_vals = [v for v, _ in best_returns]
    wins   = [(v, w) for v, w in best_returns if v > 0]
    losses = [(v, w) for v, w in best_returns if v < 0]

    # TP1 = 60th percentile of wins (conservative capture)
    tp1_pct = percentile(wins, 60) / 100 if wins else DEFAULT_TP1_PCT
    tp1_pct = max(0.10, min(1.0, tp1_pct))   # clamp 10–100%

    # TP2 = 85th percentile of wins (let runners run)
    tp2_pct = percentile(wins, 85) / 100 if wins else DEFAULT_TP2_PCT
    tp2_pct = max(tp1_pct + 0.10, min(3.0, tp2_pct))  # at least TP1+10%

    # Stop = 1.5× weighted avg loss (wider = gives more room; stale losses count less)
    if losses:
        total_loss_w = sum(w for _, w in losses)
        avg_loss = abs(sum(v * w for v, w in losses) / total_loss_w) if total_loss_w > 0 else DEFAULT_STOP_LOSS_PCT
    else:
        avg_loss = DEFAULT_STOP_LOSS_PCT
    stop_pct = -min(0.35, avg_loss * 1.5 / 100)   # cap at -35%
    stop_pct = min(-0.08, stop_pct)               # floor at -8%

    # Trailing stop = 60% of TP1 move (trail tightly after partial)
    trailing_pct = max(0.06, tp1_pct * 0.6)

    # Max hold based on best horizon
    max_hold = float(best_h) * 1.25   # give 25% extra time beyond best horizon

    plan = {
        "stop_loss_pct":     stop_pct,
        "tp1_pct":           tp1_pct,
        "tp1_sell_pct":      DEFAULT_TP1_SELL_PCT,
        "tp2_pct":           tp2_pct,
        "tp2_sell_pct":      DEFAULT_TP2_SELL_PCT,
        "trailing_stop_pct": trailing_pct,
        "max_hold_hours":    max_hold,
        "learned_from":      n,
        "best_horizon_h":    best_h,
        "profile_key":       profile_key,
        "cycle_phase":       cycle_phase,
    }

    logger.info(
        "exit_strategy plan: %s  n=%d  stop=%.1f%%  tp1=%.1f%%  tp2=%.1f%%  max_hold=%.1fh",
        profile_key, n,
        stop_pct * 100, tp1_pct * 100, tp2_pct * 100, max_hold,
    )
    return plan


def _default_plan(profile_key: str, learned_from: int = 0,
                  cycle_phase: str = "TRANSITION") -> dict:
    """
    Return conservative defaults, adjusted by market cycle phase (Phase 3).

    If the cycle playbook has enough samples (≥10), learned values override defaults.
    Otherwise, use the phase-specific hard-coded defaults from market_cycle.py.
    """
    try:
        from utils.market_cycle import get_cycle_playbook  # type: ignore
        pb = get_cycle_playbook(cycle_phase)
    except Exception:
        pb = {}

    # Use learned playbook if available, otherwise phase-specific defaults
    stop_pct     = -(pb.get("stop_loss_pct") or DEFAULT_STOP_LOSS_PCT)
    tp1_pct      = pb.get("tp1_pct")   or DEFAULT_TP1_PCT
    tp2_pct      = pb.get("tp2_pct")   or DEFAULT_TP2_PCT
    tp1_sell_pct = pb.get("tp1_sell_pct") or DEFAULT_TP1_SELL_PCT
    tp2_sell_pct = pb.get("tp2_sell_pct") or DEFAULT_TP2_SELL_PCT
    trailing_pct = pb.get("trailing_pct") or DEFAULT_TRAILING_PCT
    max_hold     = pb.get("max_hold_hours") or DEFAULT_MAX_HOLD_HOURS

    return {
        "stop_loss_pct":     stop_pct,
        "tp1_pct":           tp1_pct,
        "tp1_sell_pct":      tp1_sell_pct,
        "tp2_pct":           tp2_pct,
        "tp2_sell_pct":      tp2_sell_pct,
        "trailing_stop_pct": trailing_pct,
        "max_hold_hours":    max_hold,
        "learned_from":      learned_from,
        "best_horizon_h":    4,
        "profile_key":       profile_key,
        "cycle_phase":       cycle_phase,
    }


# ── Exit condition checker ────────────────────────────────────────────────────

def should_exit(
    trade: dict,
    current_price: float,
    peak_price: float,
    exit_plan: dict,
    tp1_hit: bool = False,
) -> dict:
    """
    Evaluate whether the position should be exited (fully or partially).

    trade:         { id, symbol, entry_price, opened_ts_utc, ... }
    current_price: latest fetched price
    peak_price:    highest price seen since entry
    exit_plan:     from build_exit_plan()
    tp1_hit:       whether TP1 was already triggered

    Returns: { exit: bool, pct_to_sell: float (0-1), reason: str }
    """
    entry = float(trade.get("entry_price", 0))
    if entry <= 0 or current_price <= 0:
        return {"exit": False, "pct_to_sell": 0.0, "reason": "no_price"}

    pct_from_entry = (current_price - entry) / entry  # signed % change

    stop_pct        = float(exit_plan.get("stop_loss_pct", -DEFAULT_STOP_LOSS_PCT))
    tp1_pct         = float(exit_plan.get("tp1_pct", DEFAULT_TP1_PCT))
    tp1_sell        = float(exit_plan.get("tp1_sell_pct", DEFAULT_TP1_SELL_PCT))
    tp2_pct         = float(exit_plan.get("tp2_pct", DEFAULT_TP2_PCT))
    tp2_sell        = float(exit_plan.get("tp2_sell_pct", DEFAULT_TP2_SELL_PCT))
    trailing_pct    = float(exit_plan.get("trailing_stop_pct", DEFAULT_TRAILING_PCT))
    max_hold_hours  = float(exit_plan.get("max_hold_hours", DEFAULT_MAX_HOLD_HOURS))

    # 1. Stop-loss
    if pct_from_entry <= stop_pct:
        return {
            "exit": True,
            "pct_to_sell": 1.0,
            "reason": f"STOP_LOSS ({pct_from_entry*100:.1f}% <= {stop_pct*100:.1f}%)",
        }

    # 2. TP2 (only if TP1 was already hit)
    if tp1_hit and pct_from_entry >= tp2_pct:
        return {
            "exit": True,
            "pct_to_sell": tp2_sell,
            "reason": f"TP2 ({pct_from_entry*100:.1f}% >= {tp2_pct*100:.1f}%)",
        }

    # 3. TP1 (not yet hit)
    if not tp1_hit and pct_from_entry >= tp1_pct:
        return {
            "exit": True,
            "pct_to_sell": tp1_sell,
            "reason": f"TP1 ({pct_from_entry*100:.1f}% >= {tp1_pct*100:.1f}%)",
        }

    # 4. Trailing stop (after TP1 was hit)
    if tp1_hit and peak_price > 0:
        pct_from_peak = (current_price - peak_price) / peak_price
        if pct_from_peak <= -trailing_pct:
            return {
                "exit": True,
                "pct_to_sell": 1.0,
                "reason": f"TRAILING_STOP ({pct_from_peak*100:.1f}% from peak)",
            }

    # 5. Max hold time
    opened_str = trade.get("opened_ts_utc", "")
    if opened_str:
        try:
            opened_dt = datetime.fromisoformat(opened_str.replace("Z", "+00:00"))
            if opened_dt.tzinfo is None:
                opened_dt = opened_dt.replace(tzinfo=timezone.utc)
            age_hours = (datetime.now(timezone.utc) - opened_dt).total_seconds() / 3600
            if age_hours >= max_hold_hours:
                return {
                    "exit": True,
                    "pct_to_sell": 1.0,
                    "reason": f"MAX_HOLD ({age_hours:.1f}h >= {max_hold_hours:.1f}h)",
                }
        except Exception:
            pass

    return {"exit": False, "pct_to_sell": 0.0, "reason": "hold"}


# ── Exit learnings sidecar ────────────────────────────────────────────────────

def update_exit_learnings(
    trade_id: int,
    symbol: str,
    exit_reason: str,
    entry_price: float,
    exit_price: float,
    position_usd: float,
    exit_plan: dict,
) -> None:
    """
    Append a closed trade outcome to exit_outcomes.json.
    auto_tune.py can read this to improve strategy calibration over time.
    """
    pnl_pct = ((exit_price - entry_price) / entry_price * 100) if entry_price > 0 else 0.0
    pnl_usd = position_usd * pnl_pct / 100

    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "trade_id": trade_id,
        "symbol": symbol,
        "exit_reason": exit_reason,
        "entry_price": entry_price,
        "exit_price": exit_price,
        "pnl_pct": round(pnl_pct, 4),
        "pnl_usd": round(pnl_usd, 4),
        "position_usd": position_usd,
        "profile_key": exit_plan.get("profile_key", ""),
        "learned_from": exit_plan.get("learned_from", 0),
        "plan_stop": exit_plan.get("stop_loss_pct"),
        "plan_tp1": exit_plan.get("tp1_pct"),
        "plan_tp2": exit_plan.get("tp2_pct"),
        "plan_max_hold_h": exit_plan.get("max_hold_hours"),
        "best_horizon_h": exit_plan.get("best_horizon_h"),
    }

    try:
        _LEARNINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        existing = []
        if _LEARNINGS_PATH.exists():
            try:
                existing = json.loads(_LEARNINGS_PATH.read_text())
            except Exception:
                existing = []
        existing.append(record)
        _LEARNINGS_PATH.write_text(json.dumps(existing, indent=2))
        logger.info(
            "exit_learnings: saved trade %d %s pnl=%.2f%%  reason=%s",
            trade_id, symbol, pnl_pct, exit_reason,
        )
    except Exception as exc:
        logger.error("Failed to save exit learnings: %s", exc)


def load_exit_learnings() -> list[dict]:
    """Load all saved exit outcomes for analysis."""
    if not _LEARNINGS_PATH.exists():
        return []
    try:
        return json.loads(_LEARNINGS_PATH.read_text())
    except Exception:
        return []


def get_exit_summary() -> dict:
    """Compute summary stats from exit outcomes for the dashboard."""
    records = load_exit_learnings()
    if not records:
        return {"total": 0, "win_rate": None, "avg_pnl_pct": None, "by_reason": {}}

    total = len(records)
    wins = [r for r in records if r.get("pnl_pct", 0) > 0]
    win_rate = len(wins) / total * 100 if total else None
    avg_pnl = sum(r.get("pnl_pct", 0) for r in records) / total if total else None

    by_reason: dict[str, dict] = {}
    for r in records:
        reason_key = r.get("exit_reason", "UNKNOWN").split(" ")[0]
        if reason_key not in by_reason:
            by_reason[reason_key] = {"count": 0, "wins": 0, "avg_pnl": 0.0, "_pnls": []}
        by_reason[reason_key]["count"] += 1
        if r.get("pnl_pct", 0) > 0:
            by_reason[reason_key]["wins"] += 1
        by_reason[reason_key]["_pnls"].append(r.get("pnl_pct", 0))

    for key, v in by_reason.items():
        pnls = v.pop("_pnls", [])
        v["avg_pnl"] = round(sum(pnls) / len(pnls), 2) if pnls else 0.0
        v["win_rate"] = round(v["wins"] / v["count"] * 100, 1) if v["count"] else None

    return {
        "total": total,
        "win_rate": round(win_rate, 1) if win_rate is not None else None,
        "avg_pnl_pct": round(avg_pnl, 2) if avg_pnl is not None else None,
        "by_reason": by_reason,
    }
