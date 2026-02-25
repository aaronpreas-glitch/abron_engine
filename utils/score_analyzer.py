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
