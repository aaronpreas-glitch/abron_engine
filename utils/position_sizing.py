"""
Kelly-based position sizing for memecoin signals.

Uses outcome history to calculate optimal position size based on:
1. Win rate from historical alerts
2. Average win/loss magnitude
3. Confidence tier (A/B/C)
4. Liquidity constraints (slippage impact)
5. Portfolio heat limits
"""

import logging
from typing import Optional


def calculate_kelly_fraction(win_rate: float, avg_win: float, avg_loss: float) -> float:
    """
    Calculate Kelly Criterion fraction.

    Formula: f* = (p * b - q) / b
    where:
      p = win probability
      q = loss probability (1 - p)
      b = win/loss ratio (avg_win / avg_loss)

    Returns fraction between 0 and 1, or 0 if negative edge.
    """
    if win_rate <= 0 or win_rate >= 1:
        return 0.0
    if avg_win <= 0 or avg_loss <= 0:
        return 0.0

    p = win_rate
    q = 1.0 - win_rate
    b = avg_win / avg_loss

    kelly = (p * b - q) / b

    # Negative kelly = negative edge = no bet
    if kelly <= 0:
        return 0.0

    # Cap at 25% to avoid overleveraging (fractional Kelly)
    return min(0.25, kelly)


def estimate_slippage_bps(
    position_usd: float,
    liquidity_usd: float,
    volume_24h: float,
    coefficient: float = 0.65,
) -> float:
    """
    Estimate slippage in basis points based on position size vs liquidity.

    Simple model: slippage ~ (position / liquidity)^coefficient * 10000

    Args:
        position_usd: Size of position in USD
        liquidity_usd: Pool liquidity in USD
        volume_24h: 24h volume in USD
        coefficient: Impact curve exponent (default 0.65)

    Returns:
        Estimated slippage in basis points (100 bps = 1%)
    """
    if liquidity_usd <= 0 or position_usd <= 0:
        return 0.0

    impact_ratio = position_usd / liquidity_usd

    # Higher volume relative to liquidity = better depth = lower slippage
    vol_to_liq = volume_24h / liquidity_usd if liquidity_usd > 0 else 0
    depth_adjustment = max(0.5, min(1.5, 1.0 / (1.0 + vol_to_liq)))

    # Slippage in bps
    slippage_bps = (impact_ratio ** coefficient) * 10000 * depth_adjustment

    return slippage_bps


def calculate_position_size(
    token: dict,
    portfolio_usd: float,
    outcome_stats: Optional[dict] = None,
    min_pct: float = 0.75,
    max_pct: float = 8.0,
    max_slippage_bps: float = 70.0,
    slippage_coefficient: float = 0.65,
) -> dict:
    """
    Calculate optimal position size for a signal using Kelly + constraints.

    Args:
        token: Token data including score, confidence, liquidity, volume
        portfolio_usd: Portfolio size in USD (e.g., 10000)
        outcome_stats: Historical outcome statistics (win_rate, avg_win, avg_loss)
        min_pct: Minimum position % of portfolio (default 0.75%)
        max_pct: Maximum position % of portfolio (default 8%)
        max_slippage_bps: Maximum acceptable slippage in bps (default 70)
        slippage_coefficient: Slippage impact curve exponent

    Returns:
        dict with:
            - position_usd: Recommended position size in USD
            - position_pct: Position as % of portfolio
            - kelly_fraction: Kelly criterion result
            - slippage_bps: Estimated slippage
            - reason: Sizing rationale
            - limited_by: What constraint limited the size (kelly/slippage/max_pct)
    """
    liquidity = float(token.get("liquidity", 0) or 0)
    volume_24h = float(token.get("volume_24h", 0) or 0)
    score = float(token.get("score", 0) or 0)
    confidence = str(token.get("confidence", "C"))

    # Base Kelly calculation from outcome stats
    kelly_fraction = 0.0
    if outcome_stats and outcome_stats.get("sample_size", 0) >= 10:
        win_rate = outcome_stats.get("win_rate", 0.5)
        avg_win = outcome_stats.get("avg_win_pct", 5.0) / 100
        avg_loss = abs(outcome_stats.get("avg_loss_pct", -3.0)) / 100
        kelly_fraction = calculate_kelly_fraction(win_rate, avg_win, avg_loss)
    else:
        # Default conservative estimates if no history
        # Assume 55% win rate, 8% avg win, 4% avg loss for A-tier
        base_win_rate = {"A": 0.55, "B": 0.50, "C": 0.45}.get(confidence, 0.50)
        base_avg_win = {"A": 0.08, "B": 0.06, "C": 0.04}.get(confidence, 0.06)
        base_avg_loss = {"A": 0.04, "B": 0.05, "C": 0.06}.get(confidence, 0.05)
        kelly_fraction = calculate_kelly_fraction(base_win_rate, base_avg_win, base_avg_loss)

    # Fractional Kelly (use 1/2 or 1/3 Kelly for safety)
    fractional_kelly = kelly_fraction * 0.5

    # Confidence-based multiplier
    confidence_mult = {"A": 1.0, "B": 0.85, "C": 0.7}.get(confidence, 0.7)
    fractional_kelly *= confidence_mult

    # Score-based multiplier (higher score = more conviction)
    if score >= 85:
        score_mult = 1.2
    elif score >= 75:
        score_mult = 1.0
    elif score >= 65:
        score_mult = 0.8
    else:
        score_mult = 0.6
    fractional_kelly *= score_mult

    # Convert Kelly fraction to position %
    kelly_pct = fractional_kelly * 100

    # Apply min/max constraints
    target_pct = max(min_pct, min(max_pct, kelly_pct))
    target_usd = portfolio_usd * (target_pct / 100)

    # Check slippage constraint
    estimated_slippage = estimate_slippage_bps(
        target_usd,
        liquidity,
        volume_24h,
        slippage_coefficient,
    )

    limited_by = "kelly"

    # If slippage too high, reduce position size
    if estimated_slippage > max_slippage_bps:
        # Binary search to find max position that fits slippage budget
        low, high = 0.0, target_usd
        while high - low > 1.0:
            mid = (low + high) / 2
            test_slip = estimate_slippage_bps(mid, liquidity, volume_24h, slippage_coefficient)
            if test_slip <= max_slippage_bps:
                low = mid
            else:
                high = mid
        target_usd = low
        target_pct = (target_usd / portfolio_usd) * 100
        estimated_slippage = estimate_slippage_bps(
            target_usd,
            liquidity,
            volume_24h,
            slippage_coefficient,
        )
        limited_by = "slippage"

    # Final bounds check
    if target_pct >= max_pct:
        limited_by = "max_pct"
    elif target_pct <= min_pct:
        limited_by = "min_pct"

    # Build reason
    reasons = []
    if kelly_fraction > 0:
        reasons.append(f"Kelly {kelly_fraction:.1%}")
    reasons.append(f"conf {confidence}")
    reasons.append(f"score {score:.0f}")
    if limited_by == "slippage":
        reasons.append(f"slippage-limited")

    return {
        "position_usd": round(target_usd, 2),
        "position_pct": round(target_pct, 2),
        "kelly_fraction": round(kelly_fraction, 3),
        "slippage_bps": round(estimated_slippage, 1),
        "reason": " | ".join(reasons),
        "limited_by": limited_by,
    }
