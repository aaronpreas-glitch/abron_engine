from .model import score_token
from config import (
    ENGINE_PROFILE,
    TACTICAL_MACD_HIST_MIN,
    TACTICAL_MIN_MOMENTUM_CHANGE_1H,
    TACTICAL_RSI_MAX,
    TACTICAL_RSI_MIN,
    TACTICAL_MIN_VOL_TO_LIQ_RATIO,
    TACTICAL_PULLBACK_MAX_PCT,
    TACTICAL_PULLBACK_MIN_PCT,
    TACTICAL_TREND_MIN_CHANGE_24H,
)


def calculate_token_score_with_breakdown(token) -> tuple:
    """
    Same as calculate_token_score() but also returns the component breakdown dict.
    Used at ALERT time to store breakdown for win-rate analysis.
    Returns (score: float, breakdown: dict)
    """
    import json as _json
    import os as _os
    score, breakdown = score_token(token)
    active_profile = str(token.get("engine_profile") or ENGINE_PROFILE).strip().lower()
    if active_profile == "tactical":
        tactical_bonus = _compute_tactical_bonus(token)
        score += tactical_bonus

    # Apply Helius on-chain safety adjustment and surface it in breakdown
    helius_adj = _compute_helius_adjustment(token)
    if helius_adj != 0.0:
        score += helius_adj
        breakdown["helius_safety"] = round(helius_adj, 1)
        breakdown["helius_grade"]  = token.get("helius_grade", "UNKNOWN")
        breakdown["helius_flags"]  = token.get("helius_flags", [])

    return score, breakdown


def _compute_tactical_bonus(token) -> float:
    """Extract tactical bonus calculation for reuse."""
    change_1h  = token.get("change_1h") or token.get("priceChange1hPercent")
    change_6h  = token.get("change_6h") or token.get("priceChange6hPercent")
    change_24h = float(token.get("change_24h", 0) or 0)
    liquidity  = float(token.get("liquidity", 0) or 0)
    volume_24h = float(token.get("volume_24h", 0) or 0)
    tactical_bonus = 0.0
    if change_24h >= TACTICAL_TREND_MIN_CHANGE_24H:
        tactical_bonus += 4
    if isinstance(change_1h, (int, float)) and change_1h >= TACTICAL_MIN_MOMENTUM_CHANGE_1H:
        tactical_bonus += 3
    if isinstance(change_6h, (int, float)) and change_6h > 0:
        tactical_bonus += 2
    pullback_pct = abs(change_24h) if change_24h < 0 else 0.0
    if TACTICAL_PULLBACK_MIN_PCT <= pullback_pct <= TACTICAL_PULLBACK_MAX_PCT:
        tactical_bonus += 4
        if isinstance(change_1h, (int, float)) and change_1h >= 0:
            tactical_bonus += 2
    volume_to_liquidity = (volume_24h / liquidity) if liquidity > 0 else 0
    if volume_to_liquidity >= TACTICAL_MIN_VOL_TO_LIQ_RATIO:
        tactical_bonus += 3
    rsi_value = token.get("rsi")
    if isinstance(rsi_value, (int, float)) and TACTICAL_RSI_MIN <= rsi_value <= TACTICAL_RSI_MAX:
        tactical_bonus += 2
    macd_hist = token.get("macd_hist")
    if isinstance(macd_hist, (int, float)) and macd_hist >= TACTICAL_MACD_HIST_MIN:
        tactical_bonus += 2
    return tactical_bonus


def _compute_helius_adjustment(token) -> float:
    """
    Apply on-chain safety penalty/bonus from Helius enrichment.

    Penalties (negative):
      MINT_AUTHORITY_LIVE     -15   dev can still print tokens
      FREEZE_AUTHORITY_LIVE   -10   dev can freeze wallets
      CONCENTRATION_CRITICAL  -20   top-5 wallets own >70%
      CONCENTRATION_HIGH      -12   top-5 wallets own 50-70%
      CONCENTRATION_MEDIUM    -5    top-5 wallets own 35-50%
      SINGLE_WHALE            -8    one wallet owns >30%

    Bonus (positive):
      Both authorities revoked AND LOW concentration  +5  genuinely clean token
    """
    grade = token.get("helius_grade")
    if not grade or grade == "UNKNOWN":
        return 0.0   # no data — neutral, don't penalise

    flags = token.get("helius_flags") or []
    adjustment = 0.0

    flag_set = set(f.split("_top")[0] for f in flags)  # strip pct suffixes

    if "MINT_AUTHORITY_LIVE" in flag_set:
        adjustment -= 15
    if "FREEZE_AUTHORITY_LIVE" in flag_set:
        adjustment -= 10
    if "CONCENTRATION_CRITICAL" in flag_set:
        adjustment -= 20
    elif "CONCENTRATION_HIGH" in flag_set:
        adjustment -= 12
    elif "CONCENTRATION_MEDIUM" in flag_set:
        adjustment -= 5
    if "SINGLE_WHALE" in flag_set:
        adjustment -= 8
    elif "LARGE_HOLDER" in flag_set:
        adjustment -= 3

    # Bonus for genuinely clean tokens
    if (
        token.get("mint_authority_revoked")
        and token.get("freeze_authority_revoked")
        and token.get("concentration_risk") == "LOW"
    ):
        adjustment += 5

    return adjustment


def calculate_token_score(token):
    """
    Wrapper used by main.py to score tokens using the real scoring model.
    The new model uses actual BirdEye/DexScreener data - no fake derivations.

    Returns only the numeric score expected by the engine.
    """
    # Get base score from the real model
    score, breakdown = score_token(token)

    # Apply tactical profile bonus if enabled
    active_profile = str(token.get("engine_profile") or ENGINE_PROFILE).strip().lower()
    if active_profile == "tactical":
        score += _compute_tactical_bonus(token)

    # Apply Helius on-chain safety adjustment
    score += _compute_helius_adjustment(token)

    return score
