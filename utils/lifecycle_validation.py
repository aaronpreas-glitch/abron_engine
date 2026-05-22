from __future__ import annotations

"""
Lifecycle Forward-Validation + Attribution — Patches 243 / 245

Public functions called from _memecoin_scan_loop after compute_lifecycle():

  snapshot_lifecycle_lane()
      Reads symbol_lifecycle, computes rank, and inserts a
      lifecycle_rank_snapshots row when (lifecycle_state, priority) changes.
      Records is_early_watch and freshness_bucket at snapshot time — these
      cannot be recomputed later once state_entered_at ages.

  link_lifecycle_outcomes()
      For snapshots >24h old with no outcome, finds the first COMPLETE MSO row
      within 7 days and writes outcome_return_pct / outcome_is_win /
      outcome_at / outcome_hours / liq_at_outcome.

  compute_lifecycle_attribution()   [Patch 245]
      Reads all resolved snapshots and produces attribution breakdown tables
      by lifecycle_state, priority, freshness_bucket, and surface (confirmed
      vs early_watch).  Returns a dict ready for JSON serialisation.
"""

import logging
import sqlite3
from datetime import datetime, timezone

from utils.db import parse_utc_ts

log = logging.getLogger(__name__)

_WIN_THRESHOLD      = 10.0    # return_24h_pct >= 10%  → outcome_is_win
_MIN_HOURS_LINK     = 24.0    # snapshot must be ≥ 24h old before outcome linking
_MAX_HOURS_LINK     = 168.0   # look forward at most 7 days for an outcome
_SURVIVAL_FLOOR     = 0.25    # liq_at_outcome >= 25% of liq_current → survived
_MEANINGFUL_RETURN  = 20.0    # return_24h_pct >= 20% → meaningful
_MIN_SIGNAL_N       = 20      # warn below this resolved count per cell


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _parse_dt(s: str) -> datetime:
    parsed = parse_utc_ts(s)
    if parsed is not None:
        return parsed
    raise ValueError(f"lifecycle_validation: cannot parse {s!r}")


def _hours(earlier: datetime, later: datetime) -> float:
    return (later - earlier).total_seconds() / 3600.0


def _freshness_bucket(state_entered_at: str | None, now_s: str) -> str:
    """Compute freshness_bucket from hours in current state at snapshot time."""
    if not state_entered_at:
        return "neutral"
    try:
        h = _hours(_parse_dt(state_entered_at), _parse_dt(now_s))
        if h < 24:
            return "new<24h"
        if h < 72:
            return "fresh<3d"
        if h < 120:
            return "neutral"
        return "stale5d"
    except (ValueError, TypeError):
        return "neutral"


# ── snapshot_lifecycle_lane ───────────────────────────────────────────────────

def snapshot_lifecycle_lane() -> int:
    """
    Insert lifecycle_rank_snapshots rows for symbols whose (lifecycle_state,
    priority) pair changed since the last snapshot.  Records is_early_watch
    and freshness_bucket at insert time.  Returns rows inserted.
    """
    import os, sys
    _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _root not in sys.path:
        sys.path.insert(0, _root)

    from utils.db import get_conn                         # type: ignore  # noqa: PLC0415
    from utils.lifecycle_engine import compute_rank       # type: ignore  # noqa: PLC0415

    inserted = 0
    now_s = _now_str()

    with get_conn() as conn:
        conn.row_factory = sqlite3.Row

        lc_rows = conn.execute("""
            SELECT symbol, lifecycle_state, n_windows, best_return_pct,
                   liq_floor, liq_current, liq_trend, pullback_depth_pct,
                   hours_since_last_window, hours_since_last_scan,
                   vol_acc_current, first_leg_confirmed, multi_leg_confirmed,
                   survivor_confirmed, last_computed_at, state_entered_at
            FROM symbol_lifecycle
            ORDER BY symbol ASC
        """).fetchall()

        # Latest (state, priority) per symbol
        last_snaps = {
            r["symbol"]: (r["lifecycle_state"], r["priority"])
            for r in conn.execute("""
                SELECT symbol, lifecycle_state, priority
                FROM lifecycle_rank_snapshots
                WHERE id IN (
                    SELECT MAX(id) FROM lifecycle_rank_snapshots GROUP BY symbol
                )
            """).fetchall()
        }

        for row in lc_rows:
            rec    = dict(row)
            symbol = rec["symbol"]
            score, priority, _ = compute_rank(rec)
            state  = rec["lifecycle_state"]

            prev = last_snaps.get(symbol)
            if prev and prev == (state, priority):
                continue   # no change — skip

            # Patch 245: attribution dimensions captured at snapshot time
            is_ew  = 0 if int(rec.get("first_leg_confirmed") or 0) else 1
            f_buck = _freshness_bucket(rec.get("state_entered_at"), now_s)

            conn.execute("""
                INSERT INTO lifecycle_rank_snapshots (
                    symbol, snapped_at, lifecycle_state, priority, rank_score,
                    liq_current, liq_trend, pullback_depth_pct,
                    n_windows, best_return_pct,
                    survivor_confirmed, multi_leg_confirmed,
                    is_early_watch, freshness_bucket
                ) VALUES (
                    :symbol, :snapped_at, :lifecycle_state, :priority, :rank_score,
                    :liq_current, :liq_trend, :pullback_depth_pct,
                    :n_windows, :best_return_pct,
                    :survivor_confirmed, :multi_leg_confirmed,
                    :is_early_watch, :freshness_bucket
                )
            """, {
                "symbol":            symbol,
                "snapped_at":        now_s,
                "lifecycle_state":   state,
                "priority":          priority,
                "rank_score":        score,
                "liq_current":       rec.get("liq_current"),
                "liq_trend":         rec.get("liq_trend"),
                "pullback_depth_pct": rec.get("pullback_depth_pct"),
                "n_windows":         rec.get("n_windows"),
                "best_return_pct":   rec.get("best_return_pct"),
                "survivor_confirmed":  rec.get("survivor_confirmed", 0),
                "multi_leg_confirmed": rec.get("multi_leg_confirmed", 0),
                "is_early_watch":    is_ew,
                "freshness_bucket":  f_buck,
            })
            inserted += 1

        conn.commit()

    log.debug("lifecycle_validation: %d snapshots inserted", inserted)
    return inserted


# ── link_lifecycle_outcomes ───────────────────────────────────────────────────

def link_lifecycle_outcomes() -> int:
    """
    For each unresolved snapshot older than 24h, find the first COMPLETE MSO
    row within 7 days and write outcome columns including liq_at_outcome.
    Returns rows updated.
    """
    import os, sys
    _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _root not in sys.path:
        sys.path.insert(0, _root)

    from utils.db import get_conn   # type: ignore  # noqa: PLC0415

    updated = 0
    now = datetime.now(timezone.utc)

    with get_conn() as conn:
        conn.row_factory = sqlite3.Row

        pending = conn.execute("""
            SELECT id, symbol, snapped_at
            FROM lifecycle_rank_snapshots
            WHERE outcome_return_pct IS NULL
        """).fetchall()

        for snap in pending:
            snap_dt = _parse_dt(snap["snapped_at"])
            age_h   = _hours(snap_dt, now)

            if age_h < _MIN_HOURS_LINK:
                continue   # too fresh

            # First COMPLETE MSO row for this symbol within 7d of snapshot
            outcome_row = conn.execute("""
                SELECT return_24h_pct, liquidity_usd, scanned_at
                FROM memecoin_signal_outcomes
                WHERE symbol = ?
                  AND status = 'COMPLETE'
                  AND scanned_at >= ?
                  AND scanned_at <= datetime(?, '+7 days')
                ORDER BY scanned_at ASC
                LIMIT 1
            """, (snap["symbol"], snap["snapped_at"], snap["snapped_at"])).fetchone()

            if not outcome_row:
                if age_h >= _MAX_HOURS_LINK:
                    # Window expired with no outcome — mark as expired (no return, no survival)
                    conn.execute("""
                        UPDATE lifecycle_rank_snapshots
                        SET outcome_return_pct = 0,
                            outcome_is_win     = 0,
                            outcome_at         = :at,
                            outcome_hours      = :hrs,
                            liq_at_outcome     = NULL
                        WHERE id = :id
                    """, {"at": now.strftime("%Y-%m-%d %H:%M:%S"), "hrs": round(age_h, 1), "id": snap["id"]})
                    updated += 1
                continue

            ret     = float(outcome_row["return_24h_pct"] or 0)
            liq_out = float(outcome_row["liquidity_usd"]  or 0) if outcome_row["liquidity_usd"] is not None else None
            o_dt    = _parse_dt(outcome_row["scanned_at"])
            hrs     = round(_hours(snap_dt, o_dt), 1)

            conn.execute("""
                UPDATE lifecycle_rank_snapshots
                SET outcome_return_pct = :ret,
                    outcome_is_win     = :win,
                    outcome_at         = :at,
                    outcome_hours      = :hrs,
                    liq_at_outcome     = :liq_out
                WHERE id = :id
            """, {
                "ret":     round(ret, 2),
                "win":     int(ret >= _WIN_THRESHOLD),
                "at":      outcome_row["scanned_at"],
                "hrs":     hrs,
                "liq_out": liq_out,
                "id":      snap["id"],
            })
            updated += 1

        conn.commit()

    log.debug("lifecycle_validation: %d outcomes linked", updated)
    return updated


# ── compute_lifecycle_attribution ─────────────────────────────────────────────

def compute_lifecycle_attribution() -> dict:
    """
    Patch 245 — Read all resolved lifecycle_rank_snapshots and produce
    attribution breakdown tables by:
      - lifecycle_state
      - priority tier
      - freshness_bucket
      - surface (confirmed lane vs early watch)

    For each cell: resolved N, survival_rate, positive_return_rate,
    meaningful_return_rate, avg_return.

    Survival  = liq_at_outcome >= 25% of liq_current at snapshot time.
    Positive  = outcome_return_pct > 0.
    Meaningful = outcome_return_pct >= 20%.

    Returns a dict ready for JSON serialisation.
    """
    import os, sys
    _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _root not in sys.path:
        sys.path.insert(0, _root)

    from utils.db import get_conn   # type: ignore  # noqa: PLC0415

    # Survival CASE expression (reused across queries)
    _SURV = (
        f"CASE WHEN liq_at_outcome IS NOT NULL "
        f"AND liq_current IS NOT NULL AND liq_current > 0 "
        f"AND liq_at_outcome >= {_SURVIVAL_FLOOR} * liq_current "
        f"THEN 1.0 ELSE 0.0 END"
    )
    _POS  = f"CASE WHEN outcome_return_pct > 0 THEN 1.0 ELSE 0.0 END"
    _MEAN = f"CASE WHEN outcome_return_pct >= {_MEANINGFUL_RETURN} THEN 1.0 ELSE 0.0 END"

    def _row_to_cell(r: sqlite3.Row) -> dict:
        return {
            "label":                  r["label"] or "unknown",
            "resolved":               int(r["resolved"]),
            "survival_rate":          round(float(r["survival_rate"]          or 0), 1),
            "positive_return_rate":   round(float(r["positive_return_rate"]   or 0), 1),
            "meaningful_return_rate": round(float(r["meaningful_return_rate"] or 0), 1),
            "avg_return":             round(float(r["avg_return"]             or 0), 1),
            "low_n":                  int(r["resolved"]) < _MIN_SIGNAL_N,
        }

    with get_conn() as conn:
        conn.row_factory = sqlite3.Row

        # ── Totals ────────────────────────────────────────────────────────────
        totals = conn.execute("""
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN outcome_return_pct IS NOT NULL THEN 1 ELSE 0 END) AS resolved
            FROM lifecycle_rank_snapshots
        """).fetchone()
        total_snap = int(totals["total"]    or 0)
        total_res  = int(totals["resolved"] or 0)

        # ── Generic breakdown helper ──────────────────────────────────────────
        def _breakdown(dim_col: str, order_expr: str | None = None) -> list:
            order = order_expr or dim_col
            rows = conn.execute(f"""
                SELECT
                    {dim_col}                          AS label,
                    COUNT(*)                           AS resolved,
                    AVG({_SURV}) * 100                 AS survival_rate,
                    AVG({_POS})  * 100                 AS positive_return_rate,
                    AVG({_MEAN}) * 100                 AS meaningful_return_rate,
                    AVG(outcome_return_pct)            AS avg_return
                FROM lifecycle_rank_snapshots
                WHERE outcome_return_pct IS NOT NULL
                GROUP BY {dim_col}
                ORDER BY {order}
            """).fetchall()
            return [_row_to_cell(r) for r in rows]

        by_state     = _breakdown("lifecycle_state",
                                  "CASE lifecycle_state "
                                  "WHEN 'RELOAD' THEN 1 WHEN 'REVIVAL' THEN 2 "
                                  "WHEN 'ACTIVE' THEN 3 WHEN 'DORMANT' THEN 4 ELSE 5 END")
        by_priority  = _breakdown("priority",
                                  "CASE priority WHEN 'HIGH' THEN 1 WHEN 'MEDIUM' THEN 2 ELSE 3 END")
        by_freshness = _breakdown("freshness_bucket",
                                  "CASE freshness_bucket "
                                  "WHEN 'new<24h' THEN 1 WHEN 'fresh<3d' THEN 2 "
                                  "WHEN 'neutral' THEN 3 ELSE 4 END")

        # ── Surface breakdown (confirmed vs early watch) ──────────────────────
        surf_rows = conn.execute(f"""
            SELECT
                CASE WHEN is_early_watch = 1 THEN 'EARLY WATCH' ELSE 'CONFIRMED' END  AS label,
                COUNT(*)                           AS resolved,
                AVG({_SURV}) * 100                 AS survival_rate,
                AVG({_POS})  * 100                 AS positive_return_rate,
                AVG({_MEAN}) * 100                 AS meaningful_return_rate,
                AVG(outcome_return_pct)            AS avg_return
            FROM lifecycle_rank_snapshots
            WHERE outcome_return_pct IS NOT NULL
            GROUP BY is_early_watch
            ORDER BY is_early_watch ASC
        """).fetchall()
        by_surface = [_row_to_cell(r) for r in surf_rows]

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "by_state":        by_state,
        "by_priority":     by_priority,
        "by_freshness":    by_freshness,
        "by_surface":      by_surface,
        "total_snapshots": total_snap,
        "total_resolved":  total_res,
        "min_for_signal":  _MIN_SIGNAL_N,
        "generated_at":    now_iso,
    }
