"""
Elite memecoin scoring model.

Scores tokens 0-100 based on:
1. Social/Narrative fit (20pts) - does it have legs?
2. Holder momentum (20pts) - are buyers accelerating?
3. Liquidity health (15pts) - can you actually trade it?
4. Volume structure (15pts) - is smart money accumulating?
5. Price action (15pts) - technical setup quality
6. Age/maturity (10pts) - fresh vs. established
7. Transaction velocity (5pts) - activity level

NO fake derived fields. All components use real data from BirdEye/DexScreener.

Dynamic tuning (set by auto_tune.py weekly):
  SCORE_WEIGHTS         JSON dict of per-component multipliers, e.g. {"holder_momentum": 1.25}
  DYNAMIC_HOT_KEYWORDS  Comma-separated keywords with high recent win rates, added to hot_keywords
"""
import json as _json
import os as _os


def _get_score_weights() -> dict:
    """Read SCORE_WEIGHTS env var — returns component multiplier overrides."""
    raw = _os.getenv("SCORE_WEIGHTS", "")
    if not raw:
        return {}
    try:
        weights = _json.loads(raw)
        return {k: float(v) for k, v in weights.items() if 0.5 <= float(v) <= 2.0}
    except Exception:
        return {}


def _get_dynamic_keywords() -> list[str]:
    """Read DYNAMIC_HOT_KEYWORDS env var — returns extra hot keywords."""
    raw = _os.getenv("DYNAMIC_HOT_KEYWORDS", "")
    if not raw:
        return []
    return [k.strip().upper() for k in raw.split(",") if k.strip()]


def score_token(token: dict) -> tuple[float, dict]:
    """
    Score a token based on real market data.

    Args:
        token: Market data from BirdEye/DexScreener with optional enrichment

    Returns:
        (score, breakdown) where score is 0-100 and breakdown shows component scores
    """
    score = 0.0
    breakdown = {}

    # ═══════════════════════════════════════════════════════════
    # 1. SOCIAL & NARRATIVE FIT (20 pts)
    # ═══════════════════════════════════════════════════════════
    social_score = 0.0

    # Has social presence (10pts)
    social_links = int(token.get("social_links", 0))
    website_links = int(token.get("website_links", 0))
    has_twitter = bool(token.get("twitter"))
    has_website = bool(token.get("website"))
    has_coingecko = bool(token.get("coingeckoId"))

    if has_twitter:
        social_score += 5
    if has_website:
        social_score += 3
    if has_coingecko:
        social_score += 2
    elif social_links > 0 or website_links > 0:
        social_score += min(5, (social_links + website_links) * 1.5)

    # Narrative fit (10pts) - based on symbol/name keywords
    # This is basic but can be enhanced with external trend data
    symbol = str(token.get("symbol", "")).upper()
    name = str(token.get("name", "")).upper()

    # Current meta (as of 2026): AI, agents, gaming, politifi still strong
    _static_hot = ["AI", "AGENT", "BOT", "GAME", "TRUMP", "BIDEN", "MAGA", "PEPE", "WOJAK", "DOGE", "SHIB"]
    hot_keywords = list(dict.fromkeys(_static_hot + _get_dynamic_keywords()))  # merge, deduplicate
    evergreen_keywords = ["BONK", "WIF", "MYRO", "POPCAT"]

    for kw in hot_keywords:
        if kw in symbol or kw in name:
            social_score += 7
            break
    else:
        for kw in evergreen_keywords:
            if kw in symbol or kw in name:
                social_score += 4
                break

    social_score = min(20, social_score)
    score += social_score
    breakdown["social_narrative"] = social_score

    # ═══════════════════════════════════════════════════════════
    # 2. HOLDER MOMENTUM (20 pts)
    # ═══════════════════════════════════════════════════════════
    holder_score = 0.0

    # Unique wallet growth (20pts)
    # BirdEye gives us uniqueWallet1h, uniqueWallet4h, uniqueWallet24h
    uw_1h = token.get("uniqueWallet1h")
    uw_4h = token.get("uniqueWallet4h")
    uw_1h_change = token.get("uniqueWallet1hChangePercent")
    uw_4h_change = token.get("uniqueWallet4hChangePercent")

    # Recent holder acceleration (1h growth)
    if uw_1h_change is not None:
        if uw_1h_change > 20:  # 20%+ holder growth in 1h = strong
            holder_score += 10
        elif uw_1h_change > 10:
            holder_score += 6
        elif uw_1h_change > 5:
            holder_score += 3
        elif uw_1h_change < -10:  # Holders exiting = bad
            holder_score -= 5

    # Sustained holder growth (4h)
    if uw_4h_change is not None:
        if uw_4h_change > 15:
            holder_score += 10
        elif uw_4h_change > 8:
            holder_score += 5
        elif uw_4h_change > 3:
            holder_score += 2

    # Absolute holder count (quality threshold)
    if uw_1h and uw_1h >= 200:  # Active holder base
        holder_score += 3
    elif uw_1h and uw_1h < 50:  # Too small
        holder_score -= 3

    holder_score = max(0, min(20, holder_score))
    score += holder_score
    breakdown["holder_momentum"] = holder_score

    # ═══════════════════════════════════════════════════════════
    # 3. LIQUIDITY HEALTH (15 pts)
    # ═══════════════════════════════════════════════════════════
    liq_score = 0.0

    liquidity = float(token.get("liquidity", 0) or 0)
    volume_24h = float(token.get("volume_24h", 0) or 0)
    market_cap = float(token.get("market_cap", 0) or 0)

    # Absolute liquidity check (5pts)
    if liquidity >= 2_000_000:  # Deep liquidity
        liq_score += 5
    elif liquidity >= 1_000_000:
        liq_score += 4
    elif liquidity >= 500_000:
        liq_score += 3
    elif liquidity >= 200_000:
        liq_score += 1
    else:
        liq_score -= 2  # Illiquid = risky

    # Liquidity to market cap ratio (5pts)
    if market_cap > 0 and liquidity > 0:
        liq_to_cap = liquidity / market_cap
        if liq_to_cap >= 0.05:  # 5%+ = very healthy
            liq_score += 5
        elif liq_to_cap >= 0.03:
            liq_score += 3
        elif liq_to_cap >= 0.01:
            liq_score += 1

    # Volume to liquidity (turnover) (5pts)
    if liquidity > 0 and volume_24h > 0:
        vol_to_liq = volume_24h / liquidity
        if 0.3 <= vol_to_liq <= 2.0:  # Healthy turnover range
            liq_score += 5
        elif 0.15 <= vol_to_liq <= 3.0:
            liq_score += 3
        elif vol_to_liq > 5.0:  # Overheated
            liq_score -= 3

    liq_score = max(0, min(15, liq_score))
    score += liq_score
    breakdown["liquidity_health"] = liq_score

    # ═══════════════════════════════════════════════════════════
    # 4. VOLUME STRUCTURE (15 pts)
    # ═══════════════════════════════════════════════════════════
    vol_score = 0.0

    # 24h volume quality (10pts)
    if volume_24h >= 5_000_000:  # Strong volume
        vol_score += 10
    elif volume_24h >= 2_000_000:
        vol_score += 7
    elif volume_24h >= 1_000_000:
        vol_score += 5
    elif volume_24h >= 500_000:
        vol_score += 3
    elif volume_24h < 200_000:
        vol_score -= 2

    # Transaction count (activity) (5pts)
    txns_h1 = int(token.get("txns_h1", 0) or 0)
    txns_h24 = int(token.get("txns_h24", 0) or 0)

    if txns_h1 >= 200:  # High activity
        vol_score += 3
    elif txns_h1 >= 100:
        vol_score += 2
    elif txns_h1 >= 50:
        vol_score += 1

    if txns_h24 >= 2000:
        vol_score += 2
    elif txns_h24 >= 1000:
        vol_score += 1

    vol_score = max(0, min(15, vol_score))
    score += vol_score
    breakdown["volume_structure"] = vol_score

    # ═══════════════════════════════════════════════════════════
    # 5. PRICE ACTION (15 pts)
    # ═══════════════════════════════════════════════════════════
    price_score = 0.0

    # Multi-timeframe momentum (10pts)
    change_1h = token.get("priceChange1hPercent") or token.get("change_1h")
    change_4h = token.get("priceChange4hPercent") or token.get("change_4h")
    change_24h = token.get("priceChange24hPercent") or token.get("change_24h")

    if change_1h is not None:
        change_1h = float(change_1h)
    if change_4h is not None:
        change_4h = float(change_4h)
    if change_24h is not None:
        change_24h = float(change_24h)

    # Look for EARLY moves, not late pumps
    # Best signal: 4h up, 1h accelerating
    if change_4h and change_1h:
        if change_4h > 5 and change_1h > change_4h / 3:  # Accelerating
            price_score += 7
        elif change_4h > 3 and change_1h > 0:
            price_score += 4
        elif change_4h < -8:  # Dumping
            price_score -= 5

    # 24h move context (avoid chasing late pumps)
    if change_24h:
        if -5 <= change_24h <= 15:  # Controlled move
            price_score += 3
        elif 15 < change_24h <= 30:  # Strong but not parabolic
            price_score += 5
        elif change_24h > 50:  # Late pump, too risky
            price_score -= 3
        elif change_24h < -20:  # Dumping hard
            price_score -= 5

    # RSI/MACD if available (5pts)
    rsi = token.get("rsi")
    macd_hist = token.get("macd_hist")

    if rsi is not None:
        rsi = float(rsi)
        if 45 <= rsi <= 65:  # Healthy range
            price_score += 3
        elif 30 <= rsi <= 45:  # Oversold bounce setup
            price_score += 2
        elif rsi > 75:  # Overbought
            price_score -= 2

    if macd_hist is not None:
        macd_hist = float(macd_hist)
        if macd_hist > 0:  # Bullish
            price_score += 2

    price_score = max(0, min(15, price_score))
    score += price_score
    breakdown["price_action"] = price_score

    # ═══════════════════════════════════════════════════════════
    # 6. AGE & MATURITY (10 pts)
    # ═══════════════════════════════════════════════════════════
    age_score = 0.0

    # Fresh listings have edge if quality is there
    pair_created_at = token.get("pair_created_at")
    age_hours = token.get("age_hours")

    if age_hours is not None:
        age_hours = float(age_hours)
    elif pair_created_at:
        import time
        now = time.time() * 1000  # ms
        age_ms = now - float(pair_created_at)
        age_hours = age_ms / (1000 * 3600)

    if age_hours is not None:
        if 4 <= age_hours <= 48:  # Sweet spot: fresh but not brand new
            age_score += 10
        elif 2 <= age_hours < 4:  # Very fresh
            age_score += 7
        elif 48 < age_hours <= 168:  # 2-7 days
            age_score += 5
        elif age_hours > 720:  # > 30 days = established
            age_score += 3

    age_score = min(10, age_score)
    score += age_score
    breakdown["age_maturity"] = age_score

    # ═══════════════════════════════════════════════════════════
    # 7. TRANSACTION VELOCITY (5 pts)
    # ═══════════════════════════════════════════════════════════
    velocity_score = 0.0

    # Velocity = txns per hour trending
    if txns_h1 and txns_h24:
        avg_txns_per_hour = txns_h24 / 24
        if txns_h1 > avg_txns_per_hour * 1.5:  # Accelerating activity
            velocity_score += 5
        elif txns_h1 > avg_txns_per_hour:
            velocity_score += 3
        elif txns_h1 < avg_txns_per_hour * 0.5:  # Dying
            velocity_score -= 2

    velocity_score = max(0, min(5, velocity_score))
    score += velocity_score
    breakdown["transaction_velocity"] = velocity_score

    # ═══════════════════════════════════════════════════════════
    # 8. WHALE ACCUMULATION SIGNAL (10 pts)
    # Proxy for "whales buying quietly before hype hits"
    # Sources: vol/mcap ratio at low cap, buy/sell txn imbalance,
    # uniqueWallet growth diverging from price (smart accumulation)
    # ═══════════════════════════════════════════════════════════
    whale_score = 0.0

    # Low mcap + rising volume = something brewing (core signal)
    # This is the "transaction volume = real demand" signal:
    # small cap with disproportionate volume = whale loading
    if market_cap > 0 and volume_24h > 0:
        vol_to_mcap = volume_24h / market_cap
        if market_cap < 10_000_000:      # Small cap context
            if vol_to_mcap >= 0.5:       # Volume > 50% of mcap in 24h = very active
                whale_score += 6
            elif vol_to_mcap >= 0.25:    # 25%+ still significant
                whale_score += 4
            elif vol_to_mcap >= 0.10:
                whale_score += 2
        elif market_cap < 100_000_000:   # Mid cap — needs less relative volume
            if vol_to_mcap >= 0.15:
                whale_score += 4
            elif vol_to_mcap >= 0.08:
                whale_score += 2

    # Holder growth WITHOUT corresponding price spike = quiet accumulation
    # Price flat/down but wallets growing = whales accumulating, retail not yet aware
    uw_1h_chg = token.get("uniqueWallet1hChangePercent") or 0
    change_1h_val = float(token.get("change_1h") or token.get("priceChange1hPercent") or 0)
    if uw_1h_chg and isinstance(uw_1h_chg, (int, float)):
        if uw_1h_chg > 5 and abs(change_1h_val) < 3:
            # Wallets growing fast but price not moving = stealth accumulation
            whale_score += 4
        elif uw_1h_chg > 10 and change_1h_val < 0:
            # Wallets growing while price dips = strong hands buying the dip
            whale_score += 6

    whale_score = max(0, min(10, whale_score))
    score += whale_score
    breakdown["whale_accumulation"] = whale_score

    # ═══════════════════════════════════════════════════════════
    # 9. BREAKOUT TIMING SIGNAL (8 pts)
    # "Buy before the breakout, most retail FOMOs after"
    # Detects consolidation zones and early breakout conditions
    # ═══════════════════════════════════════════════════════════
    breakout_score = 0.0

    change_24h_val = float(token.get("change_24h") or token.get("priceChange24hPercent") or 0)
    change_6h_val  = float(token.get("change_6h")  or token.get("priceChange6hPercent")  or 0)

    # Consolidation = 24h flat, then 1h or 6h starting to move
    # This is the setup: range compression before breakout
    is_consolidating_24h = abs(change_24h_val) <= 8   # 24h range tight
    is_moving_1h  = change_1h_val  > 2                # 1h starting to break up
    is_moving_6h  = change_6h_val  > 3                # 6h building momentum

    if is_consolidating_24h and is_moving_1h and is_moving_6h:
        # Classic early breakout setup: flat 24h, both 1h and 6h turning up
        breakout_score += 8
    elif is_consolidating_24h and is_moving_1h:
        # 24h consolidation + 1h impulse — early signal
        breakout_score += 5
    elif is_consolidating_24h and is_moving_6h:
        # 6h momentum building in a tight range
        breakout_score += 3

    # Penalise parabolic — retail is already in, the breakout already happened
    if change_24h_val > 40:
        breakout_score -= 5   # Late to the party
    elif change_24h_val > 25:
        breakout_score -= 2   # Getting late

    breakout_score = max(0, min(8, breakout_score))
    score += breakout_score
    breakdown["breakout_timing"] = breakout_score

    # ═══════════════════════════════════════════════════════════
    # 10. SECOND LEG / ATH DRAWDOWN SIGNAL (12 pts)
    # "Ape conviction bags AFTER 80-90% drawdown from ATH"
    # The Murad/Sniper framework: CT thinks it's dead = your entry.
    # First leg = exit liquidity. Second leg = life-changing gains.
    # Third leg = whale territory, trade with them or exit gracefully.
    #
    # Scoring:
    #   SECOND_LEG (75-95% from ATH) = max bonus — ideal entry zone
    #   DRAWDOWN (30-75%) = small bonus — too early, whales still exiting
    #   FIRST_LEG (0-30%) = neutral — could be pumping or just launched
    #   THIRD_LEG (recovering back to ATH) = penalty — already played out
    # ═══════════════════════════════════════════════════════════
    leg_score   = 0.0
    leg         = token.get("leg", "UNKNOWN")
    drawdown    = float(token.get("drawdown_pct") or 0)
    is_2nd_leg  = token.get("is_second_leg", False)

    if leg == "SECOND_LEG":
        # Core entry zone — graduated by depth of drawdown
        if drawdown >= 90:
            leg_score = 12     # 90%+ down, maximum conviction entry
        elif drawdown >= 85:
            leg_score = 10     # Ideal zone
        elif drawdown >= 80:
            leg_score = 8      # Good zone
        else:
            leg_score = 5      # 75-80% — entering the zone
    elif leg == "DRAWDOWN":
        # Still distributing — whales haven't fully exited yet
        # Small bonus: moving in right direction but too early
        if drawdown >= 60:
            leg_score = 2      # Getting close
        else:
            leg_score = 0      # Too early
    elif leg == "FIRST_LEG":
        # At or near ATH — first pump or newly launched
        # Neutral: could be valid new launch, don't penalize
        leg_score = 0
    elif leg == "THIRD_LEG":
        # Already recovered — this is whale territory
        # If we're in third leg and price is running, it's late
        leg_score = -4

    leg_score = max(-4, min(12, leg_score))
    score += leg_score
    breakdown["second_leg"] = leg_score
    breakdown["leg_phase"]  = leg
    breakdown["drawdown_pct_from_ath"] = round(drawdown, 1)

    # ═══════════════════════════════════════════════════════════
    # 11. MARKETCAP TIER CONTEXT
    # Not a scored component — sets tier label and applies tier-aware
    # adjustments based on the risk/opportunity framework:
    #   Sub $1M   — high rug risk, dump risk from early investors
    #   $1M-$10M  — volatile but tradeable; favor 2-5% liq/mcap ratio
    #   $10M-$100M— sweet spot: holder growth, profit-taking opps
    #   $100M-$1B — limit size; seek market leaders, avoid beta pumps
    #   >$1B      — established; low upside for memecoin strategy
    # ═══════════════════════════════════════════════════════════
    mcap_tier       = "UNKNOWN"
    mcap_adjustment = 0.0

    if market_cap > 0:
        if market_cap < 1_000_000:
            mcap_tier = "MICRO"          # Sub $1M — rug/dump risk
            # Penalise heavily: high volatility, early investor dumps
            mcap_adjustment = -12.0
        elif market_cap < 10_000_000:
            mcap_tier = "SMALL"          # $1M-$10M — volatile but opportunity
            # Check liq/mcap ratio — prefer 2-5% range per framework
            liq_to_cap_pct = (liquidity / market_cap * 100) if market_cap > 0 else 0
            if 2.0 <= liq_to_cap_pct <= 5.0:
                mcap_adjustment = +4.0   # Sweet spot for expansion potential
            elif liq_to_cap_pct < 1.0:
                mcap_adjustment = -6.0   # Too illiquid relative to cap
            else:
                mcap_adjustment = +1.0
        elif market_cap < 100_000_000:
            mcap_tier = "MID"            # $10M-$100M — best risk/reward tier
            # Strong holder growth + bullish PA here = high conviction
            # Bonus for tokens in this range that also have good momentum
            if isinstance(change_24h, (int, float)) and change_24h > 0:
                mcap_adjustment = +5.0   # Bullish PA in best tier
            else:
                mcap_adjustment = +3.0   # Still best tier, just consolidating
        elif market_cap < 1_000_000_000:
            mcap_tier = "LARGE"          # $100M-$1B — limit size, market leaders only
            # Reduced upside; needs strong fundamentals to justify position
            if isinstance(change_24h, (int, float)) and change_24h > 20:
                mcap_adjustment = -5.0   # Chasing a late pump in large cap = bad
            else:
                mcap_adjustment = -2.0   # Modest penalty — upside is capped
        else:
            mcap_tier = "MEGA"           # >$1B — low memecoin upside
            mcap_adjustment = -8.0

    breakdown["mcap_tier"]       = mcap_tier
    breakdown["mcap_adjustment"] = round(mcap_adjustment, 1)
    score += mcap_adjustment

    # ═══════════════════════════════════════════════════════════
    # DYNAMIC WEIGHT APPLICATION (from auto_tune.py weekly learning)
    # Applied before final normalization. Multipliers are capped [0.5, 2.0].
    # ═══════════════════════════════════════════════════════════
    _weights = _get_score_weights()
    if _weights:
        # Map component names to their max pts (for proportional adjustment)
        _max_pts = {
            "social_narrative":     20,
            "holder_momentum":      20,
            "liquidity_health":     15,
            "volume_structure":     15,
            "price_action":         15,
            "age_maturity":         10,
            "transaction_velocity":  5,
            "whale_accumulation":   10,
            "breakout_timing":       8,
            "second_leg":           12,
        }
        # Recompute score from weighted breakdown
        # Skip metadata fields (strings or mcap context) — only numeric score components
        _SKIP = {"mcap_tier", "mcap_adjustment"}
        _adj_score = 0.0
        for _comp, _base in breakdown.items():
            if _comp in _SKIP or not isinstance(_base, (int, float)):
                continue
            _mult = _weights.get(_comp, 1.0)
            _adj = _base * _mult
            # Soft-cap at original max to prevent one component from dominating
            _adj = min(_adj, _max_pts.get(_comp, _base) * 1.3)
            _adj_score += _adj
        # Scale so total weight stays proportional to original 100-pt scale
        _numeric_breakdown = {k: v for k, v in breakdown.items()
                              if k not in _SKIP and isinstance(v, (int, float))}
        _orig_total = sum(_numeric_breakdown.values())
        if _orig_total > 0:
            score = _adj_score * (score / _orig_total) if score > 0 else _adj_score

    # ═══════════════════════════════════════════════════════════
    # FINAL NORMALIZATION
    # ═══════════════════════════════════════════════════════════
    score = max(0, min(100, score))

    return score, breakdown
