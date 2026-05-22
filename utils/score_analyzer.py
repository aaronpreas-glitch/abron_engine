"""
score_analyzer.py — Feature-level win rate analysis for the Brain.

Runs weekly (called from auto_tune.py) to determine:
  1. Which scoring components (holder_momentum, price_action, etc.) most predict wins
  2. Which narrative keywords (AI, TRUMP, PEPE) have highest win rates THIS month
  3. Optimal scoring weight adjustments (capped at ±30% per component)
  4. Dynamic keyword list updates

Results written to data_storage/score_analysis.json.
The engine reads SCORE_WEIGHTS and DYNAMIC_HOT_KEYWORDS env vars on startup.

Env vars written by this module (via auto_tune.py):
  SCORE_WEIGHTS         JSON dict of component weight multipliers, e.g. {"holder_momentum": 1.25}
  DYNAMIC_HOT_KEYWORDS  Comma-separated list of keywords that are hot this month
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("score_analyzer")

_BASE_DIR   = Path(__file__).resolve().parents[1]
_OUTPUT_PATH = _BASE_DIR / "data_storage" / "score_analysis.json"

# Hard limits on weight adjustments to avoid runaway tuning
_MAX_WEIGHT_MULTIPLIER = 1.30   # +30% max boost
_MIN_WEIGHT_MULTIPLIER = 0.70   # -30% max reduction
_MIN_N_FOR_WEIGHTS = 15         # Need ≥15 alerts to start adjusting weights
_MIN_CONSISTENCY_WEEKS = 3      # Need ≥3 consistent weekly runs before writing SCORE_WEIGHTS

# Keywords to track win rates for (supplements the static list in scoring/model.py)
_TRACKED_KEYWORDS = [
    "AI", "AGENT", "BOT", "GPT",
    "TRUMP", "BIDEN", "MAGA",
    "PEPE", "FROG", "WOJAK",
    "CAT", "DOG", "MONKEY", "APE",
    "MEME", "MOON", "GEM",
    "BONK", "WIF", "MYRO",
    "SOL", "SOLANA",
    "GAME", "PLAY",
]


def _load_existing() -> dict:
    """Load existing analysis results (for consistency tracking)."""
    try:
        if _OUTPUT_PATH.exists():
            return json.loads(_OUTPUT_PATH.read_text())
    except Exception:
        pass
    return {}


def _save(data: dict):
    """Save analysis results to JSON."""
    _OUTPUT_PATH.parent.mkdir(exist_ok=True)
    data["updated_ts_utc"] = datetime.now(timezone.utc).isoformat()
    _OUTPUT_PATH.write_text(json.dumps(data, indent=2))


def analyze_score_components(lookback_days: int = 60, min_n: int = 10) -> dict:
    """
    Correlate score components with 4h outcome returns.
    Reads from the DB via get_score_breakdown_stats().

    Returns:
    {
      "n": int,
      "component_correlations": {
        "holder_momentum":  {"correlation_4h": 0.41, "win_rate_when_high": 0.62, ...},
        ...
      },
      "lookback_days": int,
      "recommended_weight_changes": {
        "holder_momentum": 1.15,   # boost by 15%
        "social_narrative": 0.85,  # reduce by 15%
        ...
      },
      "consistency_weeks": int,    # how many consecutive weeks this pattern held
      "ready_to_apply": bool,      # True when consistent for MIN_CONSISTENCY_WEEKS
    }
    """
    try:
        from utils.db import get_score_breakdown_stats
        result = get_score_breakdown_stats(lookback_days=lookback_days, min_n=min_n)
    except Exception as e:
        log.warning("get_score_breakdown_stats failed: %s", e)
        return {"error": str(e)}

    if result.get("insufficient_data"):
        log.info("Score analyzer: insufficient data (n=%d, need %d)", result.get("n", 0), min_n)
        return result

    correlations = result.get("component_correlations", {})
    existing = _load_existing()
    prev_corr = existing.get("component_correlations", {})

    # Determine recommended weight changes based on correlation
    # Higher correlation → boost; lower (or negative) → reduce
    recommended = {}
    for comp, stats in correlations.items():
        corr = stats.get("correlation_4h", 0)
        # Map correlation [-1, 1] to weight multiplier [0.70, 1.30]
        # corr > 0.3 → boost; corr < -0.1 → reduce; in between → neutral
        if corr > 0.3:
            multiplier = min(_MAX_WEIGHT_MULTIPLIER, 1.0 + corr * 0.6)
        elif corr < -0.1:
            multiplier = max(_MIN_WEIGHT_MULTIPLIER, 1.0 + corr * 0.3)
        else:
            multiplier = 1.0
        recommended[comp] = round(multiplier, 3)

    # Consistency tracking: how many weeks did this same direction hold?
    consistency_weeks = int(existing.get("consistency_weeks", 0))
    direction_same = True
    for comp, new_mult in recommended.items():
        prev_mult = prev_corr.get(comp, {}).get("recommended_multiplier", 1.0)
        # Direction: both boost (>1) or both reduce (<1) or both neutral
        if (new_mult > 1.0) != (float(prev_mult) > 1.0):
            direction_same = False
            break

    if direction_same and result.get("n", 0) >= min_n:
        consistency_weeks = min(consistency_weeks + 1, 99)
    else:
        consistency_weeks = 1  # Reset if directions flip

    ready_to_apply = (
        consistency_weeks >= _MIN_CONSISTENCY_WEEKS
        and result.get("n", 0) >= _MIN_N_FOR_WEIGHTS
    )

    # Embed recommended multipliers into correlations dict for storage
    for comp in correlations:
        correlations[comp]["recommended_multiplier"] = recommended.get(comp, 1.0)

    final = {
        **result,
        "component_correlations": correlations,
        "recommended_weight_changes": recommended,
        "consistency_weeks": consistency_weeks,
        "ready_to_apply": ready_to_apply,
    }
    _save(final)
    log.info(
        "Score analysis complete: n=%d, consistency_weeks=%d, ready_to_apply=%s",
        result.get("n", 0), consistency_weeks, ready_to_apply,
    )
    return final


def analyze_keyword_win_rates(lookback_days: int = 30, min_n: int = 5) -> dict:
    """
    For each tracked keyword, find alerts containing that keyword in symbol/name
    and compute the win rate. Returns sorted list: hottest keywords first.

    Uses alert_outcomes joined with signals table (symbol match).
    """
    try:
        from utils.db import get_conn
    except Exception as e:
        return {"error": str(e)}

    from datetime import timedelta
    cutoff_iso = (datetime.utcnow() - timedelta(days=lookback_days)).isoformat()

    results = {}

    with get_conn() as conn:
        cur = conn.cursor()
        # Fetch all COMPLETE outcomes in window with their symbol
        cur.execute(
            """
            SELECT symbol, return_4h_pct, return_1h_pct
            FROM alert_outcomes
            WHERE status = 'COMPLETE'
              AND return_4h_pct IS NOT NULL
              AND created_ts_utc >= ?
            """,
            (cutoff_iso,),
        )
        outcomes = [dict(r) for r in cur.fetchall()]

    if not outcomes:
        return {"keywords": {}, "hot": [], "cold": [], "n_total": 0}

    for kw in _TRACKED_KEYWORDS:
        kw_upper = kw.upper()
        matches = [o for o in outcomes if kw_upper in o["symbol"].upper()]
        if len(matches) < min_n:
            continue
        rets = [m["return_4h_pct"] for m in matches]
        wins = sum(1 for v in rets if v > 0)
        results[kw] = {
            "n":      len(rets),
            "wr_4h":  round(wins / len(rets), 3),
            "avg_4h": round(sum(rets) / len(rets), 2),
        }

    # Sort by win rate
    hot  = [k for k, v in sorted(results.items(), key=lambda x: x[1]["wr_4h"], reverse=True) if v["wr_4h"] > 0.55]
    cold = [k for k, v in sorted(results.items(), key=lambda x: x[1]["wr_4h"])              if v["wr_4h"] < 0.40]

    return {
        "keywords":    results,
        "hot":         hot[:5],    # Top 5 hottest
        "cold":        cold[:3],   # Bottom 3 coldest (to warn against)
        "n_total":     len(outcomes),
        "lookback_days": lookback_days,
    }


def build_env_updates(component_analysis: dict, keyword_analysis: dict) -> dict:
    """
    Return a dict of env var updates to apply if the analysis supports them.
    Called from auto_tune.py after analysis.

    Returns: {
      "SCORE_WEIGHTS": '{"holder_momentum": 1.15, "price_action": 1.22}',
      "DYNAMIC_HOT_KEYWORDS": "AI,TRUMP,PEPE",
    }
    or {} if not yet ready.
    """
    updates = {}

    # SCORE_WEIGHTS — only apply if consistent for 3+ weeks
    if component_analysis.get("ready_to_apply"):
        weight_changes = component_analysis.get("recommended_weight_changes", {})
        # Only include non-neutral weights (multiplier != 1.0)
        meaningful = {k: v for k, v in weight_changes.items() if abs(v - 1.0) >= 0.05}
        if meaningful:
            updates["SCORE_WEIGHTS"] = json.dumps(meaningful)
            log.info("Recommending SCORE_WEIGHTS update: %s", meaningful)

    # DYNAMIC_HOT_KEYWORDS — update every week based on recent win rates
    hot_kws = keyword_analysis.get("hot", [])
    if hot_kws:
        updates["DYNAMIC_HOT_KEYWORDS"] = ",".join(hot_kws)
        log.info("Recommending DYNAMIC_HOT_KEYWORDS: %s", hot_kws)

    return updates


def get_analysis_summary() -> dict:
    """Return the most recent analysis for dashboard display."""
    return _load_existing()


# ── Memecoin outcome attribution ─────────────────────────────────────────────

_MEMECOIN_WEIGHT_STATE_PATH = Path(__file__).parent.parent / "data_storage" / "memecoin_weight_state.json"
_MIN_MEMECOIN_N = 30          # min outcomes needed to recommend weight changes
_MEMECOIN_CONSISTENCY_WEEKS = 3  # consecutive weeks same direction before applying


def _load_memecoin_state() -> dict:
    try:
        return json.loads(_MEMECOIN_WEIGHT_STATE_PATH.read_text())
    except Exception:
        return {}


def _save_memecoin_state(state: dict) -> None:
    try:
        _MEMECOIN_WEIGHT_STATE_PATH.write_text(json.dumps(state, indent=2))
    except Exception:
        pass


def _reconstruct_components(row: dict) -> dict | None:
    """Reconstruct the four scoring components from raw inputs stored in memecoin_signal_outcomes."""
    try:
        ch1  = float(row.get("change_1h_at_scan") or 0.0)
        top  = float(row.get("top_holder_pct")     or 0.0)
        accel = float(row.get("vol_acceleration")  or 0.0)
        mcap  = float(row.get("mcap_at_scan")      or 0.0)
    except (TypeError, ValueError):
        return None

    # h1_score — lower spike = better
    abs_ch1 = abs(ch1)
    if abs_ch1 <= 5:
        h1 = 40.0
    elif abs_ch1 <= 10:
        h1 = 35.0
    elif abs_ch1 <= 15:
        h1 = 25.0
    elif abs_ch1 <= 20:
        h1 = 10.0
    else:
        h1 = 0.0

    # holder_score — lower concentration = better
    if top < 2:
        ho = 25.0
    elif top < 4:
        ho = 15.0
    elif top < 8:
        ho = 5.0
    else:
        ho = 0.0

    # accel_score — moderate acceleration sweet spot
    if 2 <= accel < 5:
        ac = 20.0
    elif 5 <= accel < 10:
        ac = 15.0
    elif 10 <= accel < 20:
        ac = 10.0
    elif accel >= 20:
        ac = 0.0
    else:
        ac = 5.0

    # mcap_score — Patch 277 bands (mcap in USD)
    mcap_m = mcap / 1_000_000
    if 3 <= mcap_m < 10:
        mc = 15.0
    elif 1.5 <= mcap_m < 3:
        mc = 10.0
    elif 10 <= mcap_m < 25:
        mc = 7.0
    elif 25 <= mcap_m < 50:
        mc = 4.0
    else:
        mc = 0.0

    return {"h1_score": h1, "holder_score": ho, "accel_score": ac, "mcap_score": mc}


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    """Compute Pearson correlation coefficient between two equal-length lists."""
    n = len(xs)
    if n < 5:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    num   = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    denom = (sum((x - mx) ** 2 for x in xs) * sum((y - my) ** 2 for y in ys)) ** 0.5
    if denom == 0:
        return None
    return num / denom


def analyze_memecoin_score_components(lookback_days: int = 60) -> dict:
    """
    Compute per-component Pearson correlation against 4h returns using raw inputs
    already stored in memecoin_signal_outcomes. Tracks consistency across weekly
    runs and recommends MEMECOIN_SCORE_WEIGHTS multipliers when stable.
    """
    import sqlite3
    from datetime import datetime, timezone, timedelta

    db_path = Path(__file__).parent.parent / "data_storage" / "engine.db"
    cutoff  = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).isoformat()

    try:
        con = sqlite3.connect(str(db_path))
        con.row_factory = sqlite3.Row
        cur = con.execute("""
            SELECT change_1h_at_scan, top_holder_pct, vol_acceleration, mcap_at_scan,
                   return_4h_pct
            FROM   memecoin_signal_outcomes
            WHERE  status = 'COMPLETE'
              AND  return_4h_pct IS NOT NULL
              AND  change_1h_at_scan IS NOT NULL
              AND  top_holder_pct IS NOT NULL
              AND  vol_acceleration IS NOT NULL
              AND  mcap_at_scan IS NOT NULL
              AND  mcap_at_scan > 0
              AND  scanned_at >= ?
        """, (cutoff,))
        rows = [dict(r) for r in cur.fetchall()]
        con.close()
    except Exception as exc:
        return {"error": str(exc), "components": [], "ready_to_apply": False}

    n = len(rows)
    if n < _MIN_MEMECOIN_N:
        return {"n": n, "components": [], "ready_to_apply": False,
                "note": f"insufficient data ({n} < {_MIN_MEMECOIN_N})"}

    component_keys = ["h1_score", "holder_score", "accel_score", "mcap_score"]
    buckets: dict[str, list[float]] = {k: [] for k in component_keys}
    returns: list[float] = []

    for row in rows:
        comps = _reconstruct_components(row)
        if comps is None:
            continue
        for k in component_keys:
            buckets[k].append(comps[k])
        returns.append(float(row["return_4h_pct"]))

    components = []
    directions: dict[str, str] = {}
    for k in component_keys:
        corr = _pearson(buckets[k], returns)
        if corr is None:
            multiplier, direction = 1.0, "neutral"
        elif corr > 0.15:
            multiplier = round(min(1.30, 1.0 + corr * 0.6), 4)
            direction  = "up"
        elif corr < -0.05:
            multiplier = round(max(0.70, 1.0 + corr * 0.3), 4)
            direction  = "down"
        else:
            multiplier, direction = 1.0, "neutral"

        components.append({
            "component":  k,
            "correlation": round(corr, 4) if corr is not None else None,
            "multiplier":  multiplier,
            "direction":   direction,
            "n":           len(buckets[k]),
        })
        directions[k] = direction

    # ── Consistency tracking ──────────────────────────────────────────────────
    state = _load_memecoin_state()
    prev_dirs = state.get("directions", {})
    consistency = state.get("consistency_weeks", 0)

    if prev_dirs and all(directions.get(k) == prev_dirs.get(k) for k in component_keys):
        consistency += 1
    else:
        consistency = 1

    state.update({
        "directions":         directions,
        "consistency_weeks":  consistency,
        "last_run":           datetime.now(timezone.utc).isoformat(),
        "n":                  n,
    })
    _save_memecoin_state(state)

    ready = consistency >= _MEMECOIN_CONSISTENCY_WEEKS and n >= _MIN_MEMECOIN_N

    return {
        "n":                  n,
        "components":         components,
        "consistency_weeks":  consistency,
        "ready_to_apply":     ready,
    }


def build_memecoin_env_updates(analysis: dict) -> dict:
    """
    Return env var updates for MEMECOIN_SCORE_WEIGHTS when analysis is ready.
    Returns empty dict if not ready or no meaningful changes.
    """
    if not analysis.get("ready_to_apply"):
        return {}

    weights = {}
    for c in analysis.get("components", []):
        mult = c.get("multiplier", 1.0)
        if abs(mult - 1.0) >= 0.05:   # only update if meaningful shift
            weights[c["component"]] = mult

    if not weights:
        return {}

    import json
    return {"MEMECOIN_SCORE_WEIGHTS": json.dumps(weights)}
