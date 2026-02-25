"""
sim_tracker.py — Virtual P&L simulation from alert_outcomes.

Uses the actual return data already tracked by the outcome tracker
to compute a realistic simulated P&L, factoring in:
  - Entry at alert price (entry_price in alert_outcomes)
  - Exit at the 4h mark price (return_4h_pct)
  - Simulated round-trip fee of 0.5% (0.25% each leg, typical for Raydium/Orca)
  - Optional position sizing (flat $100 per trade by default)
  - Max drawdown tracking
  - Per-regime, per-lane, per-confidence breakdowns

Key functions:
    get_equity_curve(lookback_days, horizon_hours, fee_pct) → list[dict]
    get_readiness_score(lookback_days) → dict
    get_sim_summary(lookback_days, horizon_hours) → dict
    export_outcomes_csv(lookback_days) → str
"""
from __future__ import annotations

import csv
import io
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Generator

_ENGINE_ROOT = Path(__file__).resolve().parent.parent
_DB_PATH = _ENGINE_ROOT / "data_storage" / "engine.db"

# Default simulated fee per round trip (buy + sell)
DEFAULT_FEE_PCT = 0.50   # 0.5% total (0.25% each leg)
DEFAULT_POSITION_USD = 100.0


@contextmanager
def _ro_conn() -> Generator[sqlite3.Connection, None, None]:
    conn = sqlite3.connect(f"file:{_DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def _ret_col(horizon_hours: int) -> str:
    return {1: "return_1h_pct", 4: "return_4h_pct", 24: "return_24h_pct"}.get(
        horizon_hours, "return_4h_pct"
    )


def _ts_col(horizon_hours: int) -> str:
    return {1: "evaluated_1h_ts_utc", 4: "evaluated_4h_ts_utc", 24: "evaluated_24h_ts_utc"}.get(
        horizon_hours, "evaluated_4h_ts_utc"
    )


# ── Equity Curve ──────────────────────────────────────────────────────────────

def get_equity_curve(
    lookback_days: int = 30,
    horizon_hours: int = 4,
    fee_pct: float = DEFAULT_FEE_PCT,
    position_usd: float = DEFAULT_POSITION_USD,
) -> list[dict]:
    """
    Returns a time-series equity curve from alert_outcomes data.

    Each point = one completed alert outcome, showing:
      - ts: timestamp of the exit evaluation
      - gross_ret: raw return from alert_outcomes (e.g. +12.5%)
      - net_ret: gross_ret minus round-trip fee
      - equity: cumulative equity multiplier (starts at 1.0)
      - equity_pct: (equity - 1) * 100 for display
      - drawdown_pct: current drawdown from peak (negative number)
      - symbol: which token
      - trade_n: sequential trade number
    """
    rc = _ret_col(horizon_hours)
    tc = _ts_col(horizon_hours)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).isoformat()

    with _ro_conn() as conn:
        rows = conn.execute(
            f"""
            SELECT
                symbol,
                {rc} AS gross_ret,
                {tc} AS ts,
                regime_label,
                confidence,
                score,
                lane,
                source
            FROM alert_outcomes
            WHERE {rc} IS NOT NULL
              AND {tc} IS NOT NULL
              AND created_ts_utc >= ?
            ORDER BY {tc} ASC
            """,
            (cutoff,),
        ).fetchall()

    equity = 1.0
    peak   = 1.0
    result = []

    for i, row in enumerate(rows):
        gross = float(row["gross_ret"])
        net   = gross - fee_pct
        equity *= 1.0 + (net / 100.0)

        if equity > peak:
            peak = equity

        dd_pct = (equity - peak) / peak * 100.0 if peak > 0 else 0.0

        result.append({
            "trade_n":      i + 1,
            "ts":           row["ts"],
            "symbol":       row["symbol"],
            "gross_ret":    round(gross, 2),
            "net_ret":      round(net, 2),
            "equity":       round(equity, 5),
            "equity_pct":   round((equity - 1.0) * 100, 2),
            "drawdown_pct": round(dd_pct, 2),
            "regime_label": row["regime_label"],
            "confidence":   row["confidence"],
            "score":        float(row["score"]) if row["score"] is not None else None,
            "lane":         row["lane"],
        })

    return result


# ── Simulation Summary ────────────────────────────────────────────────────────

def get_sim_summary(
    lookback_days: int = 30,
    horizon_hours: int = 4,
    fee_pct: float = DEFAULT_FEE_PCT,
) -> dict:
    """
    Compute comprehensive simulation stats from alert_outcomes.
    Returns key metrics: trades, win_rate, avg_net_return, max_drawdown,
    equity_end, best_trade, worst_trade, by_regime, by_lane, by_confidence.
    """
    curve = get_equity_curve(
        lookback_days=lookback_days,
        horizon_hours=horizon_hours,
        fee_pct=fee_pct,
    )

    if not curve:
        return _empty_summary(lookback_days, horizon_hours)

    net_rets = [p["net_ret"] for p in curve]
    wins     = [r for r in net_rets if r > 0]
    losses   = [r for r in net_rets if r < 0]

    win_rate    = len(wins) / len(net_rets) * 100 if net_rets else 0.0
    avg_net     = sum(net_rets) / len(net_rets) if net_rets else 0.0
    avg_win     = sum(wins)   / len(wins)   if wins   else 0.0
    avg_loss    = sum(losses) / len(losses) if losses else 0.0
    payoff      = (avg_win / abs(avg_loss)) if avg_loss < 0 else 0.0
    expectancy  = (win_rate / 100.0 * avg_win) + ((1 - win_rate / 100.0) * avg_loss)
    equity_end  = curve[-1]["equity"] if curve else 1.0
    max_dd      = min((p["drawdown_pct"] for p in curve), default=0.0)
    best_trade  = max(net_rets) if net_rets else 0.0
    worst_trade = min(net_rets) if net_rets else 0.0

    # Per-regime breakdown
    by_regime: dict[str, list[float]] = {}
    for p in curve:
        k = p.get("regime_label") or "unknown"
        by_regime.setdefault(k, []).append(p["net_ret"])

    regime_stats = []
    for regime, rets in sorted(by_regime.items()):
        ws  = [r for r in rets if r > 0]
        regime_stats.append({
            "regime":   regime,
            "n":        len(rets),
            "win_rate": round(len(ws) / len(rets) * 100, 1),
            "avg_ret":  round(sum(rets) / len(rets), 2),
        })

    # Per-lane breakdown
    by_lane: dict[str, list[float]] = {}
    for p in curve:
        k = p.get("lane") or "unknown"
        by_lane.setdefault(k, []).append(p["net_ret"])

    lane_stats = []
    for lane, rets in sorted(by_lane.items()):
        ws = [r for r in rets if r > 0]
        lane_stats.append({
            "lane":     lane,
            "n":        len(rets),
            "win_rate": round(len(ws) / len(rets) * 100, 1),
            "avg_ret":  round(sum(rets) / len(rets), 2),
        })

    # Per-confidence breakdown
    by_conf: dict[str, list[float]] = {}
    for p in curve:
        k = p.get("confidence") or "?"
        by_conf.setdefault(k, []).append(p["net_ret"])

    conf_stats = []
    for conf, rets in sorted(by_conf.items()):
        ws = [r for r in rets if r > 0]
        conf_stats.append({
            "confidence": conf,
            "n":          len(rets),
            "win_rate":   round(len(ws) / len(rets) * 100, 1),
            "avg_ret":    round(sum(rets) / len(rets), 2),
        })

    return {
        "lookback_days":  lookback_days,
        "horizon_hours":  horizon_hours,
        "fee_pct":        fee_pct,
        "trades":         len(net_rets),
        "wins":           len(wins),
        "losses":         len(losses),
        "win_rate_pct":   round(win_rate, 1),
        "avg_net_ret":    round(avg_net, 2),
        "avg_win":        round(avg_win, 2),
        "avg_loss":       round(avg_loss, 2),
        "payoff_ratio":   round(payoff, 2),
        "expectancy_pct": round(expectancy, 2),
        "equity_end":     round(equity_end, 4),
        "equity_pct":     round((equity_end - 1.0) * 100, 2),
        "max_drawdown_pct": round(max_dd, 2),
        "best_trade":     round(best_trade, 2),
        "worst_trade":    round(worst_trade, 2),
        "by_regime":      regime_stats,
        "by_lane":        lane_stats,
        "by_confidence":  conf_stats,
    }


def _empty_summary(lookback_days: int, horizon_hours: int) -> dict:
    return {
        "lookback_days": lookback_days, "horizon_hours": horizon_hours,
        "fee_pct": DEFAULT_FEE_PCT, "trades": 0, "wins": 0, "losses": 0,
        "win_rate_pct": 0.0, "avg_net_ret": 0.0, "avg_win": 0.0, "avg_loss": 0.0,
        "payoff_ratio": 0.0, "expectancy_pct": 0.0,
        "equity_end": 1.0, "equity_pct": 0.0, "max_drawdown_pct": 0.0,
        "best_trade": 0.0, "worst_trade": 0.0,
        "by_regime": [], "by_lane": [], "by_confidence": [],
    }


# ── Readiness Score ───────────────────────────────────────────────────────────

def get_readiness_score(lookback_days: int = 30) -> dict:
    """
    Compute a composite 'Live Readiness Score' (0–100).

    Gates:
      • n_outcomes  ≥ 20   (sample size gate)
      • win_rate_4h ≥ 52%  (better than coin flip)
      • max_drawdown < 30% (survivable risk)
      • expectancy   > 0   (positive expected value)
      • avg_net_ret  > 0   (after fees, still positive)

    Score components (each 0–20):
      1. Sample size: min(n_outcomes / 50, 1) × 20
      2. Win rate: scale 50–70% → 0–20
      3. Expectancy: scale 0–5% → 0–20
      4. Drawdown resilience: scale 0–30% dd → 20–0
      5. Consistency: win_rate_4h vs 24h alignment → 0–20

    Status:
      < 40  → NOT_READY (red)
      40–59 → BUILDING  (amber)
      60–79 → PROMISING (yellow-green)
      ≥ 80  → READY     (green)
    """
    summary = get_sim_summary(lookback_days=lookback_days, horizon_hours=4)
    rc = _ret_col(1)
    tc = _ts_col(1)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).isoformat()

    # Also fetch 24h win rate for consistency check
    with _ro_conn() as conn:
        r24 = conn.execute(
            """
            SELECT
                COUNT(return_24h_pct) AS n24,
                SUM(CASE WHEN return_24h_pct > 0 THEN 1 ELSE 0 END) AS w24
            FROM alert_outcomes
            WHERE return_24h_pct IS NOT NULL AND created_ts_utc >= ?
            """,
            (cutoff,),
        ).fetchone()

    n = summary["trades"]
    wr4  = summary["win_rate_pct"]
    exp  = summary["expectancy_pct"]
    dd   = abs(summary["max_drawdown_pct"])  # positive for easier math
    avg  = summary["avg_net_ret"]

    n24  = int(r24["n24"] or 0) if r24 else 0
    wr24 = float(r24["w24"] or 0) / n24 * 100 if n24 else 0.0

    # Gate checks
    gates = {
        "sample_size":    n >= 20,
        "win_rate":       wr4 >= 52,
        "drawdown":       dd < 30,
        "expectancy":     exp > 0,
        "avg_net_return": avg > 0,
    }

    # Component scores
    s1 = min(n / 50.0, 1.0) * 20.0                             # sample size
    s2 = max(0.0, min((wr4 - 50.0) / 20.0, 1.0)) * 20.0       # win rate 50→70%
    s3 = max(0.0, min(exp / 5.0, 1.0)) * 20.0                  # expectancy 0→5%
    s4 = max(0.0, (30.0 - dd) / 30.0) * 20.0                   # drawdown resilience
    # Consistency: 4h and 24h win rates within 10 points of each other
    alignment = max(0.0, 1.0 - abs(wr4 - wr24) / 20.0) * 20.0 if n24 >= 5 else 10.0
    s5 = alignment

    raw_score = s1 + s2 + s3 + s4 + s5

    # Gate penalty: each failed gate costs 10 pts
    gate_failures = sum(1 for ok in gates.values() if not ok)
    final_score = max(0.0, raw_score - gate_failures * 10.0)
    final_score = round(final_score, 1)

    if final_score >= 80:
        status, color = "READY", "var(--green)"
    elif final_score >= 60:
        status, color = "PROMISING", "#a3e635"
    elif final_score >= 40:
        status, color = "BUILDING", "var(--amber)"
    else:
        status, color = "NOT_READY", "var(--red)"

    return {
        "score":         final_score,
        "status":        status,
        "color":         color,
        "gates":         gates,
        "gates_passed":  sum(1 for ok in gates.values() if ok),
        "gates_total":   len(gates),
        "components": {
            "sample_size":   round(s1, 1),
            "win_rate":      round(s2, 1),
            "expectancy":    round(s3, 1),
            "drawdown":      round(s4, 1),
            "consistency":   round(s5, 1),
        },
        "metrics": {
            "n_outcomes":      n,
            "win_rate_4h":     round(wr4, 1),
            "win_rate_24h":    round(wr24, 1),
            "expectancy_pct":  round(exp, 2),
            "max_drawdown_pct": round(-dd, 2),
            "avg_net_ret":     round(avg, 2),
        },
        "lookback_days": lookback_days,
    }


# ── CSV Export ────────────────────────────────────────────────────────────────

def export_outcomes_csv(
    lookback_days: int = 90,
    horizon_hours: int = 4,
    fee_pct: float = DEFAULT_FEE_PCT,
) -> str:
    """
    Export alert_outcomes as a CSV string for download.
    Includes all fields + computed net_ret after fees.
    """
    rc = _ret_col(horizon_hours)
    tc = _ts_col(horizon_hours)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).isoformat()

    with _ro_conn() as conn:
        rows = conn.execute(
            f"""
            SELECT
                created_ts_utc,
                symbol,
                score,
                regime_label,
                confidence,
                lane,
                source,
                entry_price,
                return_1h_pct,
                return_4h_pct,
                return_24h_pct,
                status
            FROM alert_outcomes
            WHERE created_ts_utc >= ?
            ORDER BY created_ts_utc ASC
            """,
            (cutoff,),
        ).fetchall()

    buf = io.StringIO()
    writer = csv.writer(buf)

    # Header
    writer.writerow([
        "timestamp_utc", "symbol", "score", "regime", "confidence",
        "lane", "source", "entry_price",
        "return_1h_pct", "return_4h_pct", "return_24h_pct",
        f"net_ret_{horizon_hours}h_pct", "status",
    ])

    for row in rows:
        r4 = row["return_4h_pct"]
        net = round(float(r4) - fee_pct, 3) if r4 is not None else None
        writer.writerow([
            row["created_ts_utc"],
            row["symbol"],
            row["score"],
            row["regime_label"],
            row["confidence"],
            row["lane"] or "",
            row["source"] or "",
            row["entry_price"],
            row["return_1h_pct"],
            row["return_4h_pct"],
            row["return_24h_pct"],
            net,
            row["status"],
        ])

    return buf.getvalue()
