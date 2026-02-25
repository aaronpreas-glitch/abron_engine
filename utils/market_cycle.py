"""
market_cycle.py — Long-term market cycle phase classifier + playbook manager.

The engine lives through bear markets, transition zones, and bull markets.
This module tracks which phase we are in and maintains separate learned
playbooks for each phase so exit parameters, thresholds, and position sizing
continuously improve based on what actually worked in that environment.

Phase definitions (based on 7-day rolling median of regime_score 0-100):
  BEAR        — median < 42   (meme market broadly down, high failure rate)
  TRANSITION  — median 42-58  (mixed / mid-cycle / consolidation)
  BULL        — median > 58   (meme market broadly up, momentum favourable)

Public API:
  classify_phase_from_scores(scores)  — pure function, no I/O
  get_current_cycle_phase()           — reads DB, returns current phase
  get_recent_regime_scores(n)         — last N scores from DB
  get_cycle_playbook(phase)           — load playbook dict for a phase
  get_all_playbooks()                 — all 3 phases
  update_cycle_playbooks()            — rebuild from alert_outcomes (weekly)
  get_cycle_history(days)             — daily phase labels for dashboard chart
"""

from __future__ import annotations

import json
import logging
import sqlite3
import statistics
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ── Paths ──────────────────────────────────────────────────────────────────────

_BASE_DIR       = Path(__file__).resolve().parent.parent
_DB_PATH        = _BASE_DIR / "data_storage" / "engine.db"
_PLAYBOOKS_PATH = _BASE_DIR / "data_storage" / "cycle_playbooks.json"

# ── Phase thresholds ───────────────────────────────────────────────────────────

BEAR_THRESHOLD       = 42.0   # regime_score 7d median below this → BEAR
BULL_THRESHOLD       = 58.0   # regime_score 7d median above this → BULL
PHASE_WINDOW         = 14     # number of recent regime_scores used to determine phase
MIN_SCORES_FOR_CLASS = 7      # minimum data points before classifying (else TRANSITION)

PHASES = ("BEAR", "TRANSITION", "BULL")

# ── Phase-specific defaults (before we have enough data) ──────────────────────
# These encode structural knowledge about bear/bull market behaviour:
#   BEAR:       tight stops (cut losers fast), small TP1 (take profits quickly),
#               short hold (memes dump hard in downtrends)
#   TRANSITION: balanced defaults (same as existing engine defaults)
#   BULL:       wide stops (let winners breathe), large TP2 (let momentum run),
#               long hold (memes can 10x in uptrends)

_PHASE_DEFAULTS: dict[str, dict] = {
    "BEAR": {
        "stop_loss_pct":  0.12,   # -12% stop (tighter — bear moves fast)
        "tp1_pct":        0.20,   # +20% TP1
        "tp1_sell_pct":   0.50,   # sell more at TP1 in bear (take profits)
        "tp2_pct":        0.45,   # +45% TP2 (lower bar — unlikely to extend)
        "tp2_sell_pct":   0.40,
        "trailing_pct":   0.10,   # tighter trailing after TP1
        "max_hold_hours": 8.0,    # exit sooner
        "threshold_delta": +10,   # require higher score in bear
        "size_multiplier": 0.60,  # 60% of normal sizing
    },
    "TRANSITION": {
        "stop_loss_pct":  0.18,   # -18% stop (standard)
        "tp1_pct":        0.25,   # +25% TP1
        "tp1_sell_pct":   0.40,
        "tp2_pct":        0.60,   # +60% TP2
        "tp2_sell_pct":   0.40,
        "trailing_pct":   0.12,
        "max_hold_hours": 24.0,
        "threshold_delta": 0,
        "size_multiplier": 1.0,
    },
    "BULL": {
        "stop_loss_pct":  0.22,   # -22% stop (wider — bull volatility OK)
        "tp1_pct":        0.35,   # +35% TP1 (hold longer before first exit)
        "tp1_sell_pct":   0.30,   # sell less at TP1 (let it run)
        "tp2_pct":        0.90,   # +90% TP2 (big extension expected)
        "tp2_sell_pct":   0.40,
        "trailing_pct":   0.15,   # wider trailing in bull
        "max_hold_hours": 36.0,   # hold longer
        "threshold_delta": -3,    # slightly lower bar OK in bull
        "size_multiplier": 1.0,
    },
}

# Minimum samples before we override defaults with learned values
MIN_SAMPLES_TO_LEARN = 10


# ── Pure classifier ────────────────────────────────────────────────────────────

def classify_phase_from_scores(scores: list[float]) -> str:
    """
    Pure function — classify market cycle phase from a list of regime_scores.

    Uses the most recent PHASE_WINDOW scores as the rolling window.
    Returns 'BEAR' | 'TRANSITION' | 'BULL'.

    Falls back to 'TRANSITION' if fewer than MIN_SCORES_FOR_CLASS data points.
    """
    if not scores or len(scores) < MIN_SCORES_FOR_CLASS:
        return "TRANSITION"

    window = scores[-PHASE_WINDOW:]
    med = statistics.median(window)

    if med < BEAR_THRESHOLD:
        return "BEAR"
    if med > BULL_THRESHOLD:
        return "BULL"
    return "TRANSITION"


# ── DB helpers ─────────────────────────────────────────────────────────────────

def _get_conn():
    if not _DB_PATH.exists():
        return None
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def get_recent_regime_scores(n: int = 50) -> list[float]:
    """Return the last N regime_score values from regime_snapshots (ascending ts)."""
    conn = _get_conn()
    if not conn:
        return []
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT regime_score FROM regime_snapshots
            WHERE regime_score IS NOT NULL
            ORDER BY ts_utc DESC
            LIMIT ?
            """,
            (n,),
        )
        rows = cur.fetchall()
        return [float(r["regime_score"]) for r in reversed(rows)]
    except Exception as exc:
        logger.warning("get_recent_regime_scores failed: %s", exc)
        return []
    finally:
        conn.close()


def get_current_cycle_phase() -> str:
    """
    Read the last PHASE_WINDOW regime_snapshots and classify current cycle phase.
    Returns 'BEAR' | 'TRANSITION' | 'BULL'.
    """
    scores = get_recent_regime_scores(n=PHASE_WINDOW * 2)
    return classify_phase_from_scores(scores)


# ── Playbook I/O ───────────────────────────────────────────────────────────────

def _default_playbook(phase: str) -> dict:
    """Return a default playbook dict (pre-learning) for a phase."""
    d = dict(_PHASE_DEFAULTS[phase])
    d["phase"]        = phase
    d["win_rate_4h"]  = None
    d["avg_return_4h"]= None
    d["sample_size"]  = 0
    d["last_updated"] = None
    return d


def _load_playbooks_file() -> dict:
    """Load cycle_playbooks.json, creating with defaults if missing."""
    if _PLAYBOOKS_PATH.exists():
        try:
            return json.loads(_PLAYBOOKS_PATH.read_text())
        except Exception as exc:
            logger.warning("Failed to parse cycle_playbooks.json: %s", exc)

    # Bootstrap with defaults
    data = {p: _default_playbook(p) for p in PHASES}
    _save_playbooks_file(data)
    return data


def _save_playbooks_file(data: dict) -> None:
    _PLAYBOOKS_PATH.parent.mkdir(parents=True, exist_ok=True)
    _PLAYBOOKS_PATH.write_text(json.dumps(data, indent=2, default=str))


def get_cycle_playbook(phase: str) -> dict:
    """
    Return the learned (or default) playbook for a given phase.

    Merged output: default values as base, overridden by learned values
    when sample_size >= MIN_SAMPLES_TO_LEARN.
    """
    phase = phase.upper()
    if phase not in PHASES:
        phase = "TRANSITION"

    data = _load_playbooks_file()
    stored = data.get(phase, {})

    base = _default_playbook(phase)

    # Only override exit params if we have enough samples
    sample_size = stored.get("sample_size", 0) or 0
    if sample_size >= MIN_SAMPLES_TO_LEARN:
        for key in ("stop_loss_pct", "tp1_pct", "tp1_sell_pct",
                    "tp2_pct", "tp2_sell_pct", "trailing_pct", "max_hold_hours"):
            if stored.get(key) is not None:
                base[key] = stored[key]

    # Always pass through stats fields
    base["win_rate_4h"]   = stored.get("win_rate_4h")
    base["avg_return_4h"] = stored.get("avg_return_4h")
    base["sample_size"]   = sample_size
    base["last_updated"]  = stored.get("last_updated")

    return base


def get_all_playbooks() -> dict:
    """Return playbooks for all three phases."""
    return {p: get_cycle_playbook(p) for p in PHASES}


# ── Weekly learning ────────────────────────────────────────────────────────────

def update_cycle_playbooks(lookback_days: int = 90) -> dict:
    """
    Re-derive playbooks from alert_outcomes grouped by cycle_phase.

    Called by auto_tune.py weekly. For each phase:
      1. Pull completed alert_outcomes with that cycle_phase
      2. Compute win_rate_4h, avg_return_4h
      3. Derive optimal exit params (stop/tp/hold) from return percentiles
      4. Update cycle_playbooks.json only if sample_size >= MIN_SAMPLES_TO_LEARN

    Returns a summary dict for the Telegram report.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).isoformat()

    conn = _get_conn()
    if not conn:
        logger.warning("update_cycle_playbooks: DB not available")
        return {}

    summary: dict = {}

    try:
        data = _load_playbooks_file()
        cur = conn.cursor()

        for phase in PHASES:
            cur.execute(
                """
                SELECT
                    return_1h_pct,
                    return_4h_pct,
                    return_24h_pct,
                    created_ts_utc
                FROM alert_outcomes
                WHERE cycle_phase = ?
                  AND status = 'COMPLETE'
                  AND return_4h_pct IS NOT NULL
                  AND created_ts_utc >= ?
                ORDER BY created_ts_utc ASC
                """,
                (phase, cutoff),
            )
            rows = [dict(r) for r in cur.fetchall()]
            n = len(rows)

            phase_summary = {"phase": phase, "sample_size": n}

            if n < 3:
                phase_summary["status"] = "insufficient_data"
                summary[phase] = phase_summary
                # Keep defaults in playbook but update sample_size
                stored = data.get(phase, _default_playbook(phase))
                stored["sample_size"] = n
                stored["last_updated"] = datetime.now(timezone.utc).isoformat()
                data[phase] = stored
                continue

            returns_4h = [float(r["return_4h_pct"]) for r in rows]
            wins_4h    = [r for r in returns_4h if r > 0]
            losses_4h  = [r for r in returns_4h if r <= 0]

            win_rate_4h   = len(wins_4h) / n * 100
            avg_return_4h = sum(returns_4h) / n

            # Derive exit params from distribution:
            # TP1 = 60th percentile of winning returns (realistic first target)
            # TP2 = 85th percentile of winning returns (extended target)
            # Stop = 1.5× average losing return magnitude (generous but bounded)
            def pct(lst, p):
                if not lst:
                    return None
                lst = sorted(lst)
                idx = int(len(lst) * p / 100)
                return lst[min(idx, len(lst)-1)]

            tp1_derived  = pct(wins_4h, 60) if wins_4h else None
            tp2_derived  = pct(wins_4h, 85) if wins_4h else None
            avg_loss_mag = abs(sum(losses_4h) / len(losses_4h)) if losses_4h else None
            stop_derived = min(avg_loss_mag * 1.5, 0.35) if avg_loss_mag else None

            stored = data.get(phase, _default_playbook(phase))
            defaults = _PHASE_DEFAULTS[phase]

            # Determine hold time: use 4h if median 4h > 0, else prefer 1h
            best_hold = 24.0   # default
            if rows:
                r1h_all = [float(r["return_1h_pct"]) for r in rows if r["return_1h_pct"] is not None]
                r4h_all = [float(r["return_4h_pct"]) for r in rows if r["return_4h_pct"] is not None]
                med_1h  = statistics.median(r1h_all) if r1h_all else 0
                med_4h  = statistics.median(r4h_all) if r4h_all else 0
                if med_1h > med_4h and med_1h > 0:
                    best_hold = 4.0    # 1h is better horizon
                elif med_4h > 0:
                    best_hold = 24.0   # 4h is best, allow 24h hold
                else:
                    best_hold = defaults["max_hold_hours"]

            # Only update learned params if we have enough samples
            if n >= MIN_SAMPLES_TO_LEARN:
                if tp1_derived and tp1_derived > 0.05:
                    stored["tp1_pct"] = round(min(tp1_derived / 100, 1.0), 3)
                if tp2_derived and tp2_derived > 0.10:
                    stored["tp2_pct"] = round(min(tp2_derived / 100, 1.0), 3)
                if stop_derived and stop_derived > 0.05:
                    stored["stop_loss_pct"] = round(min(stop_derived / 100, 0.35), 3)
                stored["max_hold_hours"] = best_hold
            else:
                # Preserve defaults
                stored["tp1_pct"]        = defaults["tp1_pct"]
                stored["tp2_pct"]        = defaults["tp2_pct"]
                stored["stop_loss_pct"]  = defaults["stop_loss_pct"]
                stored["max_hold_hours"] = defaults["max_hold_hours"]

            stored["win_rate_4h"]    = round(win_rate_4h, 1)
            stored["avg_return_4h"]  = round(avg_return_4h, 2)
            stored["sample_size"]    = n
            stored["last_updated"]   = datetime.now(timezone.utc).isoformat()
            stored["phase"]          = phase
            data[phase] = stored

            phase_summary.update({
                "status":        "updated" if n >= MIN_SAMPLES_TO_LEARN else "defaults_kept",
                "win_rate_4h":   round(win_rate_4h, 1),
                "avg_return_4h": round(avg_return_4h, 2),
                "tp1_pct":       stored["tp1_pct"],
                "tp2_pct":       stored["tp2_pct"],
                "stop_loss_pct": stored["stop_loss_pct"],
                "max_hold_hours": stored["max_hold_hours"],
            })
            summary[phase] = phase_summary

        _save_playbooks_file(data)
        logger.info("update_cycle_playbooks: saved to %s", _PLAYBOOKS_PATH)

    except Exception as exc:
        logger.error("update_cycle_playbooks failed: %s", exc)
    finally:
        conn.close()

    return summary


# ── Cycle history (for dashboard) ──────────────────────────────────────────────

def get_cycle_history(days: int = 90) -> list[dict]:
    """
    Return daily phase label + avg regime_score for the last N days.
    Used by the dashboard to draw a phase timeline.

    Returns list of dicts:
      { date: str, phase: str, avg_regime_score: float, n_snapshots: int }
    sorted ascending by date.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    conn = _get_conn()
    if not conn:
        return []

    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT
                DATE(ts_utc) AS day,
                AVG(regime_score) AS avg_score,
                COUNT(*) AS n
            FROM regime_snapshots
            WHERE ts_utc >= ? AND regime_score IS NOT NULL
            GROUP BY DATE(ts_utc)
            ORDER BY day ASC
            """,
            (cutoff,),
        )
        rows = cur.fetchall()
    except Exception as exc:
        logger.warning("get_cycle_history query failed: %s", exc)
        return []
    finally:
        conn.close()

    result = []
    # Build rolling phase from cumulative scores
    cumulative_scores: list[float] = []
    for r in rows:
        avg = float(r["avg_score"] or 50.0)
        cumulative_scores.append(avg)
        phase = classify_phase_from_scores(cumulative_scores)
        result.append({
            "date":             r["day"],
            "phase":            phase,
            "avg_regime_score": round(avg, 1),
            "n_snapshots":      int(r["n"]),
        })

    return result


# ── Convenience summary ────────────────────────────────────────────────────────

def get_cycle_summary() -> dict:
    """
    Return a compact summary for dashboard display:
    {
      current_phase: str,
      phase_emoji: str,
      phase_color: str,
      playbooks: { BEAR: {...}, TRANSITION: {...}, BULL: {...} },
      history_14d: [ {date, phase, avg_regime_score}, ... ]
    }
    """
    current = get_current_cycle_phase()
    emoji_map = {"BEAR": "🐻", "TRANSITION": "↔", "BULL": "🐂"}
    color_map = {
        "BEAR":       "var(--red)",
        "TRANSITION": "var(--amber)",
        "BULL":       "var(--green)",
    }
    return {
        "current_phase": current,
        "phase_emoji":   emoji_map.get(current, "?"),
        "phase_color":   color_map.get(current, "var(--muted)"),
        "playbooks":     get_all_playbooks(),
        "history_14d":   get_cycle_history(days=14),
    }
