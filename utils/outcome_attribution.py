"""
utils/outcome_attribution.py — Phase 4, Step 1: Outcome Attribution Layer

Aggregates per-lane outcome performance from the four outcome tables:
  - perp_outcomes       → perps lane (signal-level 1h/4h/24h returns)
  - perp_positions      → perps lane (closed-trade PnL)
  - alert_outcomes      → spot lane (signal-level 1h/4h/24h returns)
  - memecoin_signal_outcomes → memecoins lane (signal-level 1h/4h/24h returns)

Design principles:
  - Read-only: never writes to the DB
  - Transparent: every lane shows source tables, sample count, sufficient flag
  - Staleness decay: outcomes > staleness_days old are weighted at 80%
  - Minimum-N: lanes below min_n are reported but flagged as insufficient
  - No auto-tuning: this is a pure measurement function
"""

import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)


def _get_conn():
    """Shared DB connection (same pattern as utils/db.py)."""
    import os
    import sqlite3
    db_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "data_storage", "engine.db"
    )
    conn = sqlite3.connect(db_path, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=3000")
    return conn


def _weighted_stats(
    rows: list[dict],
    return_col: str,
    staleness_cutoff: str,
    ts_col: str = "created_ts_utc",
) -> dict:
    """
    Compute weighted win rate and average return for a list of outcome rows.
    Applies 80% weight to rows older than staleness_cutoff.
    """
    if not rows:
        return {"n": 0, "wr": None, "avg_ret": None}

    weighted_wins = 0.0
    weighted_total = 0.0
    weighted_sum = 0.0
    n = 0

    for r in rows:
        val = r.get(return_col)
        if val is None:
            continue
        val = float(val)
        n += 1
        ts = r.get(ts_col, "")
        w = 0.80 if ts < staleness_cutoff else 1.0
        weighted_total += w
        weighted_sum += val * w
        if val > 0:
            weighted_wins += w

    if weighted_total < 1e-6:
        return {"n": n, "wr": None, "avg_ret": None}

    return {
        "n":       n,
        "wr":      round(weighted_wins / weighted_total * 100, 1),
        "avg_ret": round(weighted_sum / weighted_total, 2),
    }


def build_outcome_attribution(
    lookback_days: int = 30,
    min_n: int = 5,
    staleness_days: int = 30,
) -> dict:
    """
    Build per-lane outcome attribution from all outcome tables.

    Returns:
    {
      "perps": {
        "signal_outcomes": { n, wr_4h, avg_ret_4h, wr_24h, avg_ret_24h },
        "closed_trades":   { n, wr, avg_pnl_pct },
        "n_total":         int,       # signal + trade outcomes
        "wr_4h":           float|None,
        "avg_ret_4h":      float|None,
        "sufficient":      bool,
        "sources":         ["perp_outcomes", "perp_positions"]
      },
      "memecoins": { ... },
      "spot":      { ... },
      "aggregate": { n_total, wr_4h, avg_ret_4h, sufficient },
      "lookback_days": int,
      "min_n":         int,
      "computed_at":   str,
    }
    """
    now = datetime.now(tz=timezone.utc)
    cutoff_iso = (now - timedelta(days=lookback_days)).isoformat()
    staleness_iso = (now - timedelta(days=staleness_days)).isoformat()

    try:
        conn = _get_conn()
    except Exception as exc:
        logger.warning("outcome_attribution: DB connect failed: %s", exc)
        return _empty_result(lookback_days, min_n, now)

    try:
        # ── Perps: signal outcomes ────────────────────────────────────────
        perp_signal_rows = [
            dict(r) for r in conn.execute(
                """SELECT return_1h_pct, return_4h_pct, return_24h_pct, created_ts_utc
                   FROM perp_outcomes
                   WHERE status = 'COMPLETE'
                     AND return_4h_pct IS NOT NULL
                     AND created_ts_utc >= ?""",
                (cutoff_iso,),
            ).fetchall()
        ]
        perp_sig_4h = _weighted_stats(perp_signal_rows, "return_4h_pct", staleness_iso)
        perp_sig_24h = _weighted_stats(perp_signal_rows, "return_24h_pct", staleness_iso)

        # ── Perps: closed trades ──────────────────────────────────────────
        perp_trade_rows = [
            dict(r) for r in conn.execute(
                """SELECT pnl_pct, opened_ts_utc AS created_ts_utc
                   FROM perp_positions
                   WHERE status = 'CLOSED'
                     AND pnl_pct IS NOT NULL
                     AND opened_ts_utc >= ?""",
                (cutoff_iso,),
            ).fetchall()
        ]
        perp_trades = _weighted_stats(perp_trade_rows, "pnl_pct", staleness_iso)

        # ── Spot: alert outcomes ──────────────────────────────────────────
        spot_rows = [
            dict(r) for r in conn.execute(
                """SELECT return_1h_pct, return_4h_pct, return_24h_pct, created_ts_utc
                   FROM alert_outcomes
                   WHERE status = 'COMPLETE'
                     AND return_4h_pct IS NOT NULL
                     AND created_ts_utc >= ?""",
                (cutoff_iso,),
            ).fetchall()
        ]
        spot_4h = _weighted_stats(spot_rows, "return_4h_pct", staleness_iso)
        spot_24h = _weighted_stats(spot_rows, "return_24h_pct", staleness_iso)

        # ── Memecoins: signal outcomes ────────────────────────────────────
        mc_rows = [
            dict(r) for r in conn.execute(
                """SELECT return_1h_pct, return_4h_pct, return_24h_pct, scanned_at AS created_ts_utc
                   FROM memecoin_signal_outcomes
                   WHERE status = 'COMPLETE'
                     AND return_4h_pct IS NOT NULL
                     AND scanned_at >= ?""",
                (cutoff_iso,),
            ).fetchall()
        ]
        mc_4h = _weighted_stats(mc_rows, "return_4h_pct", staleness_iso)
        mc_24h = _weighted_stats(mc_rows, "return_24h_pct", staleness_iso)

    finally:
        conn.close()

    # ── Assemble per-lane summaries ───────────────────────────────────────
    perps_n_total = perp_sig_4h["n"] + perp_trades["n"]
    perps = {
        "signal_outcomes": {
            "n":          perp_sig_4h["n"],
            "wr_4h":      perp_sig_4h["wr"],
            "avg_ret_4h": perp_sig_4h["avg_ret"],
            "wr_24h":     perp_sig_24h["wr"],
            "avg_ret_24h": perp_sig_24h["avg_ret"],
        },
        "closed_trades": {
            "n":           perp_trades["n"],
            "wr":          perp_trades["wr"],
            "avg_pnl_pct": perp_trades["avg_ret"],
        },
        "n_total":    perps_n_total,
        "wr_4h":      perp_sig_4h["wr"],
        "avg_ret_4h": perp_sig_4h["avg_ret"],
        "sufficient": perps_n_total >= min_n,
        "sources":    ["perp_outcomes", "perp_positions"],
    }

    spot = {
        "signal_outcomes": {
            "n":          spot_4h["n"],
            "wr_4h":      spot_4h["wr"],
            "avg_ret_4h": spot_4h["avg_ret"],
            "wr_24h":     spot_24h["wr"],
            "avg_ret_24h": spot_24h["avg_ret"],
        },
        "n_total":    spot_4h["n"],
        "wr_4h":      spot_4h["wr"],
        "avg_ret_4h": spot_4h["avg_ret"],
        "sufficient": spot_4h["n"] >= min_n,
        "sources":    ["alert_outcomes"],
    }

    memecoins = {
        "signal_outcomes": {
            "n":          mc_4h["n"],
            "wr_4h":      mc_4h["wr"],
            "avg_ret_4h": mc_4h["avg_ret"],
            "wr_24h":     mc_24h["wr"],
            "avg_ret_24h": mc_24h["avg_ret"],
        },
        "n_total":    mc_4h["n"],
        "wr_4h":      mc_4h["wr"],
        "avg_ret_4h": mc_4h["avg_ret"],
        "sufficient": mc_4h["n"] >= min_n,
        "sources":    ["memecoin_signal_outcomes"],
    }

    # ── Aggregate across lanes ────────────────────────────────────────────
    all_4h_rows = perp_signal_rows + spot_rows + mc_rows
    agg_4h = _weighted_stats(all_4h_rows, "return_4h_pct", staleness_iso)
    agg_n = perps_n_total + spot_4h["n"] + mc_4h["n"]

    aggregate = {
        "n_total":    agg_n,
        "wr_4h":      agg_4h["wr"],
        "avg_ret_4h": agg_4h["avg_ret"],
        "sufficient": agg_n >= min_n,
    }

    return {
        "perps":          perps,
        "memecoins":      memecoins,
        "spot":           spot,
        "aggregate":      aggregate,
        "lookback_days":  lookback_days,
        "min_n":          min_n,
        "computed_at":    now.isoformat(),
    }


def _empty_result(lookback_days: int, min_n: int, now: datetime) -> dict:
    """Return an empty attribution when DB is unavailable."""
    _empty_lane = {
        "signal_outcomes": {"n": 0, "wr_4h": None, "avg_ret_4h": None, "wr_24h": None, "avg_ret_24h": None},
        "n_total": 0, "wr_4h": None, "avg_ret_4h": None,
        "sufficient": False, "sources": [],
    }
    return {
        "perps":     {**_empty_lane, "closed_trades": {"n": 0, "wr": None, "avg_pnl_pct": None}, "sources": ["perp_outcomes", "perp_positions"]},
        "memecoins": {**_empty_lane, "sources": ["memecoin_signal_outcomes"]},
        "spot":      {**_empty_lane, "sources": ["alert_outcomes"]},
        "aggregate": {"n_total": 0, "wr_4h": None, "avg_ret_4h": None, "sufficient": False},
        "lookback_days": lookback_days,
        "min_n":         min_n,
        "computed_at":   now.isoformat(),
    }
