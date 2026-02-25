"""
db_read.py — Read-only query layer for the dashboard.

All connections use sqlite3 URI mode=ro so this process can NEVER
write to the engine database. WAL-mode SQLite allows concurrent reads
alongside the engine writer with zero blocking.

Imports and re-exports functions from utils/db.py where they already exist.
New queries (not in db.py) are implemented here.
"""
from __future__ import annotations

import sqlite3
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from typing import Any, Generator

# Add engine root to path so we can import utils.db
_ENGINE_ROOT = Path(__file__).resolve().parents[2]
if str(_ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(_ENGINE_ROOT))

_DB_PATH = _ENGINE_ROOT / "data_storage" / "engine.db"


@contextmanager
def _ro_conn() -> Generator[sqlite3.Connection, None, None]:
    conn = sqlite3.connect(f"file:{_DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def _rw_conn() -> Generator[sqlite3.Connection, None, None]:
    """Read-write connection — used ONLY for manual position open/close."""
    conn = sqlite3.connect(str(_DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Re-export from utils.db — these already exist and work
# ---------------------------------------------------------------------------
try:
    from utils.db import (
        get_performance_summary,
        get_portfolio_simulation_metrics,
        get_open_positions,
        get_risk_pause_state,
        get_symbol_controls_summary,
        get_risk_mode,
        get_weekly_tuning_report,
    )
    _UTILS_AVAILABLE = True
except ImportError:
    _UTILS_AVAILABLE = False
    # Stub fallbacks so the dashboard never crashes on import
    def get_performance_summary(*a, **kw): return {"scans": 0, "alerts": 0, "alert_rate": 0.0, "avg_score": 0.0, "max_score": 0.0, "top_alert_symbols": []}
    def get_portfolio_simulation_metrics(*a, **kw): return {"trades": 0, "avg_return_pct": 0.0, "median_return_pct": 0.0, "win_rate_pct": 0.0, "payoff_ratio": 0.0, "expectancy_pct": 0.0, "max_drawdown_pct": 0.0, "equity_end": 1.0}
    def get_open_positions(*a, **kw): return []
    def get_risk_pause_state(*a, **kw): return {}
    def get_symbol_controls_summary(*a, **kw): return []
    def get_risk_mode(*a, **kw): return {"mode": "NORMAL", "emoji": "🟢", "streak": 0, "threshold_delta": 0, "size_multiplier": 1.0, "min_confidence": None, "paused": False}
    def get_weekly_tuning_report(*a, **kw): return {}


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

def get_db_health() -> dict[str, Any]:
    try:
        with _ro_conn() as conn:
            tables = {}
            for tbl in ("signals", "trades", "alert_outcomes", "regime_snapshots"):
                row = conn.execute(f"SELECT COUNT(*) AS c FROM {tbl}").fetchone()
                tables[tbl] = int(row["c"])
            last_sig = conn.execute(
                "SELECT ts_utc FROM signals ORDER BY id DESC LIMIT 1"
            ).fetchone()
            last_ts = last_sig["ts_utc"] if last_sig else None
            return {"status": "ok", "tables": tables, "last_signal_ts": last_ts}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


# ---------------------------------------------------------------------------
# Signals
# ---------------------------------------------------------------------------

def get_recent_signals(limit: int = 50, decisions: list[str] | None = None) -> list[dict]:
    dec = decisions or ["ALERT", "ALERT_DRY_RUN", "SCAN_BEST", "WATCHLIST_ALERT", "RUNNER_WATCH_ALERT", "LEGACY_RECOVERY_ALERT"]
    placeholders = ",".join("?" * len(dec))
    with _ro_conn() as conn:
        rows = conn.execute(
            f"""
            SELECT id, ts_utc, symbol, mint, pair_address, score_total, decision,
                   regime_score, regime_label, liquidity_usd, volume_24h,
                   price_usd, change_24h, rel_strength_vs_sol,
                   conviction, setup_type, category, notes, helius_grade
            FROM signals
            WHERE decision IN ({placeholders})
            ORDER BY id DESC
            LIMIT ?
            """,
            (*dec, max(1, int(limit))),
        ).fetchall()
        return [dict(r) for r in rows]


def get_signal_by_id(signal_id: int) -> dict | None:
    """Return full signal row including all fields for breakdown modal."""
    with _ro_conn() as conn:
        row = conn.execute(
            """
            SELECT id, ts_utc, symbol, mint, pair_address, score_total, decision,
                   regime_score, regime_label, liquidity_usd, volume_24h,
                   price_usd, change_24h, rel_strength_vs_sol, liquidity_change_24h,
                   conviction, setup_type, category, chain, notes
            FROM signals WHERE id = ?
            """,
            (signal_id,),
        ).fetchone()
        return dict(row) if row else None


def get_signal_outcome(signal_id: int) -> dict | None:
    """Return alert_outcome row for a given signal if it exists."""
    try:
        with _ro_conn() as conn:
            row = conn.execute(
                """
                SELECT return_1h_pct, return_4h_pct, return_24h_pct,
                       status, last_error,
                       evaluated_1h_ts_utc, evaluated_4h_ts_utc, evaluated_24h_ts_utc
                FROM alert_outcomes
                WHERE signal_id = ? OR (symbol = (
                    SELECT symbol FROM signals WHERE id = ? LIMIT 1
                ) AND ABS(JULIANDAY(created_ts_utc) - JULIANDAY(
                    (SELECT ts_utc FROM signals WHERE id = ? LIMIT 1)
                )) < 0.01)
                ORDER BY id DESC LIMIT 1
                """,
                (signal_id, signal_id, signal_id),
            ).fetchone()
            return dict(row) if row else None
    except Exception:
        return None


def get_symbol_history(symbol: str, limit: int = 50) -> list[dict]:
    """All signals for a given symbol, most recent first."""
    with _ro_conn() as conn:
        rows = conn.execute(
            """
            SELECT id, ts_utc, symbol, mint, pair_address, score_total, decision,
                   regime_score, regime_label, liquidity_usd, volume_24h,
                   price_usd, change_24h, rel_strength_vs_sol,
                   conviction, setup_type, category, notes
            FROM signals
            WHERE symbol = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (symbol.upper(), max(1, int(limit))),
        ).fetchall()
        return [dict(r) for r in rows]


def get_symbol_outcomes(symbol: str, lookback_days: int = 30) -> dict:
    """Aggregate 1h/4h/24h outcomes for a specific symbol."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).isoformat()
    try:
        with _ro_conn() as conn:
            row = conn.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    COUNT(return_1h_pct) AS n1,
                    AVG(return_1h_pct) AS avg1,
                    SUM(CASE WHEN return_1h_pct > 0 THEN 1 ELSE 0 END) AS w1,
                    COUNT(return_4h_pct) AS n4,
                    AVG(return_4h_pct) AS avg4,
                    SUM(CASE WHEN return_4h_pct > 0 THEN 1 ELSE 0 END) AS w4,
                    COUNT(return_24h_pct) AS n24,
                    AVG(return_24h_pct) AS avg24,
                    SUM(CASE WHEN return_24h_pct > 0 THEN 1 ELSE 0 END) AS w24,
                    MAX(return_4h_pct) AS best_4h,
                    MIN(return_4h_pct) AS worst_4h
                FROM alert_outcomes
                WHERE symbol = ? AND created_ts_utc >= ?
                """,
                (symbol.upper(), cutoff),
            ).fetchone()
            if not row:
                return {}
            n1, n4, n24 = int(row["n1"] or 0), int(row["n4"] or 0), int(row["n24"] or 0)
            return {
                "total": int(row["total"] or 0),
                "outcomes_1h": {"n": n1, "avg": float(row["avg1"] or 0), "win_rate": (float(row["w1"] or 0) / n1 * 100) if n1 else 0},
                "outcomes_4h": {"n": n4, "avg": float(row["avg4"] or 0), "win_rate": (float(row["w4"] or 0) / n4 * 100) if n4 else 0,
                                "best": float(row["best_4h"] or 0), "worst": float(row["worst_4h"] or 0)},
                "outcomes_24h": {"n": n24, "avg": float(row["avg24"] or 0), "win_rate": (float(row["w24"] or 0) / n24 * 100) if n24 else 0},
            }
    except Exception:
        return {}


def get_max_signal_id() -> int:
    with _ro_conn() as conn:
        row = conn.execute("SELECT COALESCE(MAX(id), 0) AS m FROM signals").fetchone()
        return int(row["m"])


def get_symbol_controls_detail(limit: int = 50) -> list[dict]:
    """Full list of active cooldowns and blacklists with time remaining."""
    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        with _ro_conn() as conn:
            rows = conn.execute(
                """
                SELECT symbol, cooldown_until_utc, blacklist_until_utc, reason, updated_ts_utc
                FROM symbol_controls
                WHERE (cooldown_until_utc IS NOT NULL AND cooldown_until_utc > ?)
                   OR (blacklist_until_utc IS NOT NULL AND blacklist_until_utc > ?)
                ORDER BY COALESCE(blacklist_until_utc, cooldown_until_utc) DESC
                LIMIT ?
                """,
                (now_iso, now_iso, max(1, int(limit))),
            ).fetchall()
            result = []
            for r in rows:
                d = dict(r)
                # Calculate minutes remaining
                if d.get("blacklist_until_utc") and d["blacklist_until_utc"] > now_iso:
                    d["type"] = "BLACKLIST"
                    d["until"] = d["blacklist_until_utc"]
                    try:
                        mins = int((datetime.fromisoformat(d["blacklist_until_utc"]) -
                                    datetime.now(timezone.utc)).total_seconds() / 60)
                        d["mins_remaining"] = max(0, mins)
                    except Exception:
                        d["mins_remaining"] = None
                elif d.get("cooldown_until_utc") and d["cooldown_until_utc"] > now_iso:
                    d["type"] = "COOLDOWN"
                    d["until"] = d["cooldown_until_utc"]
                    try:
                        mins = int((datetime.fromisoformat(d["cooldown_until_utc"]) -
                                    datetime.now(timezone.utc)).total_seconds() / 60)
                        d["mins_remaining"] = max(0, mins)
                    except Exception:
                        d["mins_remaining"] = None
                result.append(d)
            return result
    except Exception:
        return []


def get_outcome_recap(lookback_hours: int = 48, limit: int = 15) -> list[dict]:
    """Per-symbol 4h win rate + avg return for recent alerts."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=max(1, int(lookback_hours)))).isoformat()
    try:
        with _ro_conn() as conn:
            rows = conn.execute(
                """
                SELECT
                    symbol,
                    COUNT(*) AS alerts,
                    AVG(return_1h_pct) AS avg_1h,
                    AVG(return_4h_pct) AS avg_4h,
                    AVG(return_24h_pct) AS avg_24h,
                    SUM(CASE WHEN return_4h_pct > 0 THEN 1 ELSE 0 END) AS wins_4h,
                    SUM(CASE WHEN return_4h_pct IS NOT NULL THEN 1 ELSE 0 END) AS n_4h
                FROM alert_outcomes
                WHERE created_ts_utc >= ?
                GROUP BY symbol
                ORDER BY alerts DESC, COALESCE(avg_4h, -9999) DESC
                LIMIT ?
                """,
                (cutoff, max(1, int(limit))),
            ).fetchall()
            return [dict(r) for r in rows]
    except Exception:
        return []


def get_leaderboard(lookback_hours: int = 24, limit: int = 20) -> list[dict]:
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=lookback_hours)).isoformat()
    with _ro_conn() as conn:
        rows = conn.execute(
            """
            SELECT symbol,
                   MAX(score_total) AS score,
                   regime_label,
                   change_24h,
                   COUNT(*) AS appearances,
                   MAX(ts_utc) AS last_seen
            FROM signals
            WHERE decision IN ('SCAN_BEST', 'ALERT', 'ALERT_DRY_RUN')
              AND score_total IS NOT NULL
              AND ts_utc >= ?
            GROUP BY symbol
            ORDER BY score DESC
            LIMIT ?
            """,
            (cutoff, max(1, int(limit))),
        ).fetchall()
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Performance / Outcomes
# ---------------------------------------------------------------------------

def get_outcome_winrates(lookback_days: int = 7) -> dict[str, Any]:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).isoformat()
    with _ro_conn() as conn:
        row = conn.execute(
            """
            SELECT
                COUNT(return_1h_pct) AS n1,
                COALESCE(AVG(return_1h_pct), 0) AS avg1,
                COALESCE(SUM(CASE WHEN return_1h_pct > 0 THEN 1 ELSE 0 END), 0) AS w1,
                COUNT(return_4h_pct) AS n4,
                COALESCE(AVG(return_4h_pct), 0) AS avg4,
                COALESCE(SUM(CASE WHEN return_4h_pct > 0 THEN 1 ELSE 0 END), 0) AS w4,
                COUNT(return_24h_pct) AS n24,
                COALESCE(AVG(return_24h_pct), 0) AS avg24,
                COALESCE(SUM(CASE WHEN return_24h_pct > 0 THEN 1 ELSE 0 END), 0) AS w24
            FROM alert_outcomes
            WHERE created_ts_utc >= ?
            """,
            (cutoff,),
        ).fetchone()
    n1, n4, n24 = int(row["n1"]), int(row["n4"]), int(row["n24"])
    return {
        "lookback_days": lookback_days,
        "outcomes_1h": {"n": n1, "wins": int(row["w1"]), "avg": float(row["avg1"]), "win_rate": (float(row["w1"]) / n1 * 100) if n1 else 0.0},
        "outcomes_4h": {"n": n4, "wins": int(row["w4"]), "avg": float(row["avg4"]), "win_rate": (float(row["w4"]) / n4 * 100) if n4 else 0.0},
        "outcomes_24h": {"n": n24, "wins": int(row["w24"]), "avg": float(row["avg24"]), "win_rate": (float(row["w24"]) / n24 * 100) if n24 else 0.0},
    }


def get_equity_curve(lookback_days: int = 30, horizon_hours: int = 4) -> list[dict]:
    ret_col = {1: "return_1h_pct", 4: "return_4h_pct", 24: "return_24h_pct"}.get(horizon_hours, "return_4h_pct")
    ts_col = {1: "evaluated_1h_ts_utc", 4: "evaluated_4h_ts_utc", 24: "evaluated_24h_ts_utc"}.get(horizon_hours, "evaluated_4h_ts_utc")
    cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).isoformat()
    with _ro_conn() as conn:
        rows = conn.execute(
            f"""
            SELECT symbol, {ret_col} AS ret, {ts_col} AS ts
            FROM alert_outcomes
            WHERE {ret_col} IS NOT NULL AND {ts_col} IS NOT NULL AND created_ts_utc >= ?
            ORDER BY {ts_col} ASC
            """,
            (cutoff,),
        ).fetchall()
    equity = 1.0
    result = []
    for row in rows:
        ret = float(row["ret"])
        equity *= 1.0 + (ret / 100.0)
        result.append({"ts": row["ts"], "equity": round(equity, 4), "ret": round(ret, 2), "symbol": row["symbol"]})
    return result


def get_score_histogram(lookback_hours: int = 168) -> dict[str, Any]:
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=lookback_hours)).isoformat()
    with _ro_conn() as conn:
        rows = conn.execute(
            """
            SELECT score_total FROM signals
            WHERE decision = 'SCAN_BEST' AND score_total IS NOT NULL AND ts_utc >= ?
            ORDER BY score_total ASC
            """,
            (cutoff,),
        ).fetchall()
    values = [float(r["score_total"]) for r in rows]
    buckets_def = [(0, 50), (50, 60), (60, 70), (70, 80), (80, 90), (90, 101)]
    buckets = []
    for lo, hi in buckets_def:
        label = f"{lo}–{hi if hi < 101 else 100}"
        count = sum(1 for v in values if lo <= v < hi)
        buckets.append({"range": label, "count": count})
    p50 = median(values) if values else 0.0
    def _pct(vals: list[float], p: float) -> float:
        if not vals: return 0.0
        idx = int(len(vals) * p / 100)
        return vals[min(idx, len(vals) - 1)]
    return {"buckets": buckets, "p50": round(p50, 1), "p75": round(_pct(values, 75), 1), "p90": round(_pct(values, 90), 1), "total": len(values)}


# ---------------------------------------------------------------------------
# Regime
# ---------------------------------------------------------------------------

def get_regime_timeline(hours: int = 168) -> list[dict]:
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    with _ro_conn() as conn:
        rows = conn.execute(
            """
            SELECT ts_utc, sol_change_24h, breadth_pct, liquidity_score,
                   volume_score, regime_score, regime_label
            FROM regime_snapshots
            WHERE ts_utc >= ?
            ORDER BY ts_utc ASC
            """,
            (cutoff,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_current_regime() -> dict[str, Any]:
    with _ro_conn() as conn:
        row = conn.execute(
            "SELECT * FROM regime_snapshots ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else {}


def get_alerts_for_overlay(hours: int = 168) -> list[dict]:
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    with _ro_conn() as conn:
        rows = conn.execute(
            """
            SELECT ts_utc, symbol, score_total
            FROM signals
            WHERE decision IN ('ALERT', 'ALERT_DRY_RUN') AND ts_utc >= ?
            ORDER BY ts_utc ASC
            """,
            (cutoff,),
        ).fetchall()
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Trades / Journal
# ---------------------------------------------------------------------------

def get_closed_trades(limit: int = 50) -> list[dict]:
    with _ro_conn() as conn:
        rows = conn.execute(
            """
            SELECT id, symbol, entry_price, exit_price, stop_price,
                   pnl_pct, r_multiple, opened_ts_utc, closed_ts_utc,
                   setup_type, regime_label, notes
            FROM trades
            WHERE status = 'CLOSED'
            ORDER BY closed_ts_utc DESC
            LIMIT ?
            """,
            (max(1, int(limit)),),
        ).fetchall()
        return [dict(r) for r in rows]


def get_trade_summary() -> dict[str, Any]:
    with _ro_conn() as conn:
        row = conn.execute(
            """
            SELECT
                COUNT(*) AS total,
                COALESCE(AVG(pnl_pct), 0) AS avg_pnl,
                COALESCE(AVG(r_multiple), 0) AS avg_r,
                COALESCE(SUM(CASE WHEN pnl_pct > 0 THEN 1 ELSE 0 END), 0) AS wins,
                COALESCE(SUM(pnl_pct), 0) AS total_pnl
            FROM trades
            WHERE status = 'CLOSED'
            """,
        ).fetchone()
        total = int(row["total"])
        wins = int(row["wins"])
        return {
            "total_closed": total,
            "wins": wins,
            "losses": total - wins,
            "win_rate": round(wins / total * 100, 1) if total else 0.0,
            "avg_pnl": round(float(row["avg_pnl"]), 2),
            "avg_r": round(float(row["avg_r"]), 2),
            "total_pnl": round(float(row["total_pnl"]), 2),
        }


# ---------------------------------------------------------------------------
# Position write operations (used by dashboard open/close forms)
# ---------------------------------------------------------------------------

def open_manual_position(
    symbol: str,
    entry_price: float,
    stop_price: float,
    mint: str | None = None,
    pair_address: str | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """
    Open a manually tracked position. Mirrors utils/db.py open_manual_position logic.
    Returns {"created": bool, "position": dict}.
    """
    now = datetime.now(timezone.utc).isoformat()
    with _rw_conn() as conn:
        # Check if already open
        existing = conn.execute(
            "SELECT * FROM trades WHERE symbol = ? AND status = 'OPEN' ORDER BY id DESC LIMIT 1",
            (symbol,),
        ).fetchone()
        if existing:
            return {"created": False, "position": dict(existing)}

        conn.execute(
            """
            INSERT INTO trades
                (opened_ts_utc, symbol, mint, pair_address, entry_price, stop_price, status, notes)
            VALUES (?, ?, ?, ?, ?, ?, 'OPEN', ?)
            """,
            (now, symbol, mint, pair_address, entry_price, stop_price, notes or "manual_dashboard"),
        )
        row = conn.execute(
            "SELECT * FROM trades WHERE symbol = ? AND status = 'OPEN' ORDER BY id DESC LIMIT 1",
            (symbol,),
        ).fetchone()
        return {"created": True, "position": dict(row) if row else {}}


def close_manual_position(
    symbol: str,
    exit_price: float | None = None,
    mint: str | None = None,
    notes: str | None = None,
) -> int:
    """
    Close open position(s) for a symbol. Returns count of rows closed.
    Calculates pnl_pct and r_multiple if entry_price is known.
    """
    now = datetime.now(timezone.utc).isoformat()
    with _rw_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM trades WHERE symbol = ? AND status = 'OPEN'",
            (symbol,),
        ).fetchall()
        if not rows:
            return 0

        closed = 0
        for row in rows:
            entry = float(row["entry_price"] or 0)
            stop  = float(row["stop_price"] or 0)
            ex    = float(exit_price) if exit_price else None

            pnl_pct   = None
            r_multiple = None
            if entry and entry > 0 and ex and ex > 0:
                pnl_pct = (ex - entry) / entry * 100.0
                risk = entry - stop if stop and stop < entry else entry * 0.1
                if risk > 0:
                    r_multiple = ((ex - entry) / risk)

            conn.execute(
                """
                UPDATE trades
                SET status = 'CLOSED',
                    closed_ts_utc = ?,
                    exit_price = ?,
                    pnl_pct = ?,
                    r_multiple = ?,
                    notes = COALESCE(notes, '') || ?
                WHERE id = ?
                """,
                (now, ex, pnl_pct, r_multiple,
                 f" | {notes or 'manual_dashboard_sold'}", row["id"]),
            )
            closed += 1

        return closed
