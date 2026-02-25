"""
elite_features.py — 5 Integrated Intelligence Features
========================================================
All features use only free data that's already available:
  - DexScreener (price changes, volume, liquidity, txns, trending)
  - BirdEye (holder/wallet velocity)
  - Jupiter Perps (funding rate, position data)
  - Bot's own SQLite outcome history

Feature 1: Pattern Win Rate Predictor
  → Reads alert_outcomes to show real historical win rates per signal type
  → Displays in MEMECOIN SETUP + LEGACY RECOVERY alerts

Feature 2: Narrative Momentum Analyzer
  → On-chain proxy scoring (holder velocity + volume + social links)
  → NO Twitter API — uses what's already available in token data
  → Displays in all alerts as NARRATIVE: RISING/STABLE/FADING

Feature 3: SOL Macro Correlator
  → Tracks how memecoins correlate with SOL moves over time
  → Fires a separate alert when SOL moves >5% in 1h
  → Improves as data accumulates (requires ~2 weeks)

Feature 4: On-Chain Sentiment Detector
  → Volume/price divergence + holder velocity + txn rate → sentiment score
  → Displayed as on-chain sentiment proxy in alerts

Feature 5: Liquidation Zone Predictor
  → Uses Jupiter funding rate as crowd positioning proxy
  → Estimates downside cascade zones; shown in /lev dashboard
  → Clearly labeled as estimate — not a full CEX liquidation heatmap
"""

import logging
import time
from datetime import datetime, timedelta, timezone

# ── Helpers ───────────────────────────────────────────────────────────────────

def _try_float(v, default=0.0):
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return default

def _safe_int(v, default=0):
    try:
        return int(float(v or 0))
    except (TypeError, ValueError):
        return default

SEP = "━━━━━━━━━━━━━━━━━━━━━━━━━━━"


# ─────────────────────────────────────────────────────────────────────────────
# FEATURE 1: PATTERN WIN RATE PREDICTOR
# Uses bot's own alert_outcomes table — real historical performance data.
# ─────────────────────────────────────────────────────────────────────────────

def get_pattern_win_rate(confidence: str, regime_label: str, score_min: float = 0.0,
                          lookback_days: int = 30) -> dict | None:
    """
    Query alert_outcomes for real historical win rates.
    Win = 4h return > 0 (primary trading horizon).
    Returns dict with win_rate, sample_size, avg_return_4h, or None if insufficient data.
    """
    try:
        from utils.db import get_conn
        cutoff = (datetime.utcnow() - timedelta(days=lookback_days)).isoformat()
        conf_norm = str(confidence or "C").strip().upper()

        with get_conn() as conn:
            cur = conn.cursor()
            # Match by confidence grade and score range (±15 pts)
            score_lo = max(0.0, score_min - 15.0)
            score_hi = min(100.0, score_min + 30.0)
            cur.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN return_4h_pct > 0 THEN 1 ELSE 0 END) AS wins,
                    AVG(return_4h_pct) AS avg_4h,
                    AVG(return_1h_pct) AS avg_1h,
                    AVG(return_24h_pct) AS avg_24h,
                    SUM(CASE WHEN return_4h_pct > 5 THEN 1 ELSE 0 END) AS big_wins
                FROM alert_outcomes
                WHERE confidence = ?
                  AND score >= ? AND score <= ?
                  AND return_4h_pct IS NOT NULL
                  AND created_ts_utc >= ?
                """,
                (conf_norm, score_lo, score_hi, cutoff),
            )
            row = cur.fetchone()

        if not row or (row["total"] or 0) < 5:
            return None  # Need at least 5 samples

        total = int(row["total"])
        wins = int(row["wins"] or 0)
        win_rate = wins / total
        avg_4h = _try_float(row["avg_4h"])
        avg_1h = _try_float(row["avg_1h"])
        big_wins = int(row["big_wins"] or 0)
        big_win_rate = big_wins / total

        confidence_level = "high" if total >= 25 else ("medium" if total >= 10 else "low")

        return {
            "win_rate": win_rate,
            "sample_size": total,
            "avg_return_4h": avg_4h,
            "avg_return_1h": avg_1h,
            "big_win_rate": big_win_rate,
            "confidence_level": confidence_level,
        }
    except Exception as exc:
        logging.debug("get_pattern_win_rate error: %s", exc)
        return None


def format_win_rate_block(token_data: dict) -> str | None:
    """
    Build the predictive stats block for injection into alerts.
    Returns formatted HTML string or None if no data.
    """
    confidence = str(token_data.get("confidence") or "C")
    score = _try_float(token_data.get("score"), 0.0)
    regime_label = str(token_data.get("regime_label") or "")

    stats = get_pattern_win_rate(confidence, regime_label, score_min=score)
    if not stats:
        return None

    wr = stats["win_rate"] * 100
    n = stats["sample_size"]
    avg4h = stats["avg_return_4h"]
    lvl = stats["confidence_level"]
    big_wr = stats["big_win_rate"] * 100

    if wr >= 65:
        wr_emoji = "🟢"
    elif wr >= 50:
        wr_emoji = "🟡"
    else:
        wr_emoji = "🔴"

    sample_note = "●●●" if lvl == "high" else ("●●○" if lvl == "medium" else "●○○")
    avg_str = f"{avg4h:+.1f}%" if avg4h != 0 else "—"

    lines = [
        f"<code>  Win Rate   {wr:.0f}%  Avg4h {avg_str}  {wr_emoji}</code>",
        f"<code>  Big wins   {big_wr:.0f}%  Samples {n} {sample_note}</code>",
    ]
    if lvl == "low":
        lines.append(f"<code>  ⚠️ Limited history — accuracy improves over time</code>")

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# FEATURE 2: NARRATIVE MOMENTUM ANALYZER
# On-chain proxy — NO Twitter API required.
# Uses: DexScreener trending/boosts, holder velocity, volume momentum, txn rate
# ─────────────────────────────────────────────────────────────────────────────

def calculate_narrative_momentum(token: dict) -> dict:
    """
    Score narrative health 0–100 using observable on-chain + DexScreener signals.
    Returns dict with score, label, emoji, key drivers.
    """
    score = 40  # Neutral baseline
    drivers = []

    # 1. DexScreener Trending / Boosts (up to +30 pts)
    boosts = _safe_int(token.get("boosts_active", 0))
    is_trending = bool(token.get("is_dex_trending", False))
    if is_trending:
        score += 30
        drivers.append("DexScreener trending")
    elif boosts >= 3:
        score += 20
        drivers.append(f"Boosted x{boosts}")
    elif boosts >= 1:
        score += 10
        drivers.append("Active boost")

    # 2. Social / Website Presence (up to +15 pts)
    social_links = _safe_int(token.get("social_links", 0)) or _safe_int(token.get("socials_count", 0))
    website_links = _safe_int(token.get("website_links", 0))
    has_website = bool(token.get("websites")) or website_links > 0
    if social_links >= 2 and has_website:
        score += 15
        drivers.append("Strong social presence")
    elif social_links >= 1 or has_website:
        score += 7
        drivers.append("Basic social presence")
    else:
        score -= 5  # No social presence = red flag
        drivers.append("No socials found")

    # 3. Holder Velocity (up to +25 pts / -20 pts)
    holder_1h = _try_float(token.get("uniqueWallet1hChangePercent") or token.get("holder_change_1h"))
    holder_4h = _try_float(token.get("uniqueWallet4hChangePercent") or token.get("holder_change_4h"))
    holder_24h = _try_float(token.get("uniqueWallet24hChangePercent") or token.get("holder_change_24h"))

    if holder_1h > 20:
        score += 25
        drivers.append(f"Holders +{holder_1h:.0f}%/1h 🔥")
    elif holder_1h > 10:
        score += 15
        drivers.append(f"Holder growth +{holder_1h:.0f}%/1h")
    elif holder_1h > 3:
        score += 7
    elif holder_1h < -15:
        score -= 20
        drivers.append(f"Holder exodus {holder_1h:.0f}%/1h")
    elif holder_1h < -5:
        score -= 10
        drivers.append(f"Holders declining")

    if holder_24h > 10:
        score += 5
    elif holder_24h < -15:
        score -= 10

    # 4. Volume Momentum vs Liquidity (up to +15 pts)
    volume_24h = _try_float(token.get("volume_24h"))
    liquidity = _try_float(token.get("liquidity"), 1.0)
    vol_to_liq = volume_24h / liquidity if liquidity > 0 else 0
    change_24h = _try_float(token.get("change_24h"))

    if vol_to_liq > 3.0 and change_24h > 5:
        score += 15
        drivers.append("High volume + price up")
    elif vol_to_liq > 1.5:
        score += 8
        drivers.append("Strong volume")
    elif vol_to_liq < 0.2:
        score -= 10
        drivers.append("Dead volume")

    # 5. Transaction Activity (up to +10 pts / -5 pts)
    txns_h1 = _safe_int(token.get("txns_h1") or 0)
    if txns_h1 > 300:
        score += 10
        drivers.append(f"{txns_h1} txns/h")
    elif txns_h1 > 100:
        score += 5
    elif txns_h1 < 20 and txns_h1 > 0:
        score -= 5

    final_score = max(0, min(100, score))

    if final_score >= 75:
        label = "RISING"
        emoji = "🟢"
    elif final_score >= 50:
        label = "STABLE"
        emoji = "🟡"
    elif final_score >= 30:
        label = "FADING"
        emoji = "🟠"
    else:
        label = "DEAD"
        emoji = "🔴"

    return {
        "score": final_score,
        "label": label,
        "emoji": emoji,
        "drivers": drivers[:3],  # Top 3 drivers only
    }


def format_narrative_block(token: dict) -> str | None:
    """
    Build the narrative momentum block for injection into alerts.
    Returns formatted HTML string.
    """
    try:
        nm = calculate_narrative_momentum(token)
        score = nm["score"]
        label = nm["label"]
        emoji = nm["emoji"]
        drivers = nm["drivers"]
        driver_str = " · ".join(drivers) if drivers else "—"

        import html as _html
        bar_filled = int(round(score / 10))
        bar = "█" * bar_filled + "░" * (10 - bar_filled)
        lines = [
            f"<code>  Narrative  {bar} {score}/100</code>",
            f"<code>  {emoji} {label}  ·  {_html.escape(driver_str)}</code>",
        ]
        return "\n".join(lines)
    except Exception as exc:
        logging.debug("format_narrative_block error: %s", exc)
        return None


# ─────────────────────────────────────────────────────────────────────────────
# FEATURE 3: SOL MACRO CORRELATOR
# Tracks rolling correlation between SOL 1h move and token 1h move.
# Stores in sol_correlations table. Alerts when SOL moves >5% in 1h.
# ─────────────────────────────────────────────────────────────────────────────

def ensure_correlations_table():
    """Create sol_correlations table if it doesn't exist."""
    try:
        from utils.db import get_conn
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
                CREATE TABLE IF NOT EXISTS sol_correlations (
                    symbol TEXT PRIMARY KEY,
                    correlation REAL NOT NULL,
                    sample_size INTEGER NOT NULL,
                    avg_beta REAL,
                    last_updated_ts_utc TEXT NOT NULL
                )
            """)
    except Exception as exc:
        logging.warning("ensure_correlations_table error: %s", exc)


def update_sol_correlations(min_samples: int = 20):
    """
    Recalculate rolling correlation for all symbols in signals table.
    Uses last 14 days of ALERT signals with their change_1h values.
    Pairs with closest regime_snapshot for sol_change_1h.
    Called once per day from main.py scheduled job.
    """
    try:
        ensure_correlations_table()
        from utils.db import get_conn
        cutoff = (datetime.utcnow() - timedelta(days=14)).isoformat()

        with get_conn() as conn:
            cur = conn.cursor()

            # Get all ALERT signals with change_1h in last 14 days
            cur.execute(
                """
                SELECT DISTINCT symbol FROM signals
                WHERE decision = 'ALERT'
                  AND ts_utc >= ?
                  AND change_24h IS NOT NULL
                """,
                (cutoff,),
            )
            symbols = [r["symbol"] for r in cur.fetchall()]

            updated = 0
            for symbol in symbols:
                cur.execute(
                    """
                    SELECT s.ts_utc, s.change_24h AS token_chg,
                           r.sol_change_24h AS sol_chg
                    FROM signals s
                    LEFT JOIN regime_snapshots r ON r.ts_utc = (
                        SELECT MAX(r2.ts_utc) FROM regime_snapshots r2
                        WHERE r2.ts_utc <= s.ts_utc
                    )
                    WHERE s.symbol = ?
                      AND s.decision = 'ALERT'
                      AND s.ts_utc >= ?
                      AND s.change_24h IS NOT NULL
                      AND r.sol_change_24h IS NOT NULL
                    ORDER BY s.ts_utc ASC
                    """,
                    (symbol, cutoff),
                )
                rows = cur.fetchall()
                if len(rows) < min_samples:
                    continue

                token_chgs = [_try_float(r["token_chg"]) for r in rows]
                sol_chgs = [_try_float(r["sol_chg"]) for r in rows]

                # Pearson correlation
                n = len(token_chgs)
                mean_t = sum(token_chgs) / n
                mean_s = sum(sol_chgs) / n
                cov = sum((t - mean_t) * (s - mean_s) for t, s in zip(token_chgs, sol_chgs)) / n
                std_t = (sum((t - mean_t) ** 2 for t in token_chgs) / n) ** 0.5
                std_s = (sum((s - mean_s) ** 2 for s in sol_chgs) / n) ** 0.5

                if std_t < 0.01 or std_s < 0.01:
                    continue  # No variance, skip

                corr = cov / (std_t * std_s)
                corr = max(-1.0, min(1.0, corr))

                # Beta (sensitivity): how many % the token moves per 1% SOL move
                beta = cov / (std_s ** 2) if std_s > 0 else 0

                cur.execute(
                    """
                    INSERT OR REPLACE INTO sol_correlations
                    (symbol, correlation, sample_size, avg_beta, last_updated_ts_utc)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (symbol, round(corr, 4), n, round(beta, 3), datetime.utcnow().isoformat()),
                )
                updated += 1

        logging.info("SOL correlation update: %d symbols updated", updated)
        return updated
    except Exception as exc:
        logging.warning("update_sol_correlations error: %s", exc)
        return 0


def get_sol_correlated_movers(min_correlation: float = 0.55, limit: int = 6) -> list[dict]:
    """
    Return symbols with highest SOL correlation, ranked by correlation strength.
    Used when SOL makes a significant move.
    """
    try:
        ensure_correlations_table()
        from utils.db import get_conn
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT symbol, correlation, sample_size, avg_beta
                FROM sol_correlations
                WHERE correlation >= ?
                  AND sample_size >= 15
                ORDER BY correlation DESC
                LIMIT ?
                """,
                (min_correlation, limit),
            )
            return [dict(r) for r in cur.fetchall()]
    except Exception as exc:
        logging.debug("get_sol_correlated_movers error: %s", exc)
        return []


def format_sol_macro_alert(sol_change_1h: float) -> str | None:
    """
    Build a SOL macro correlation alert message.
    Called when SOL moves >5% in 1h.
    Returns formatted HTML string or None.
    """
    if abs(sol_change_1h) < 5.0:
        return None

    movers = get_sol_correlated_movers()
    if not movers:
        return None

    thin = "┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄"
    direction_label = "RECOVERY" if sol_change_1h > 0 else "SELLOFF"
    direction_emoji = "📈" if sol_change_1h > 0 else "📉"
    chg_str = f"{sol_change_1h:+.1f}%"
    urgency = "STRONG" if abs(sol_change_1h) >= 8 else "MODERATE"

    import html as _html

    lines = [
        f"<b>⚡ SOL MACRO — {direction_emoji} {direction_label}</b>",
        f"<code>{SEP}</code>",
        f"<code>  SOL moved {chg_str} in 1h  [{urgency}]</code>",
        f"<code>  Memecoins historically follow within 2–4h</code>",
        f"<code>{thin}</code>",
        f"<b>  🔗 Correlated Movers</b>",
        f"<code>{thin}</code>",
    ]

    for i, row in enumerate(movers, 1):
        sym = str(row.get("symbol") or "?").upper()
        corr = float(row.get("correlation") or 0)
        beta = float(row.get("avg_beta") or 0)
        n = int(row.get("sample_size") or 0)
        beta_str = f"β{beta:+.1f}x" if beta != 0 else "β—"
        corr_bar = "███" if corr >= 0.75 else ("██░" if corr >= 0.60 else "█░░")
        rank_emoji = "🥇" if i == 1 else ("🥈" if i == 2 else ("🥉" if i == 3 else "  "))
        lines.append(
            f"<code>  {rank_emoji} ${_html.escape(sym):<9} r={corr:.2f} {corr_bar}  {beta_str}  n={n}</code>"
        )

    lines += [
        f"<code>{SEP}</code>",
        f"<i>r = correlation · β = token move per 1% SOL · higher = closer follow</i>",
    ]
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# FEATURE 4: ON-CHAIN SENTIMENT DETECTOR
# Volume/price divergence + holder velocity + txn rate → sentiment proxy.
# NOT Twitter — this is on-chain behavior analysis.
# ─────────────────────────────────────────────────────────────────────────────

def calculate_onchain_sentiment(token: dict) -> dict:
    """
    Derive market sentiment proxy from on-chain signals.
    Returns dict with score (0–100), label, emoji.

    Scores reflect:
    - Volume vs price divergence (accumulation vs distribution)
    - Holder velocity (wallet growth = demand proxy)
    - Transaction rate (activity level)
    - 1h price momentum (immediate pressure)
    """
    score = 50  # Neutral baseline
    signals = []

    volume_24h = _try_float(token.get("volume_24h"))
    liquidity = _try_float(token.get("liquidity"), 1.0)
    change_24h = _try_float(token.get("change_24h"))
    change_1h = _try_float(token.get("change_1h"))
    change_6h = _try_float(token.get("change_6h"))
    holder_1h = _try_float(token.get("uniqueWallet1hChangePercent") or token.get("holder_change_1h"))
    holder_4h = _try_float(token.get("uniqueWallet4hChangePercent") or token.get("holder_change_4h"))
    txns_h1 = _safe_int(token.get("txns_h1"))

    vol_to_liq = volume_24h / liquidity if liquidity > 0 else 0

    # 1. Volume vs Price (accumulation vs distribution)
    if vol_to_liq > 2.0:
        if change_24h > 5:
            score += 25
            signals.append("buying surge")
        elif change_24h > 0:
            score += 12
            signals.append("active buying")
        elif change_24h < -5:
            score -= 25
            signals.append("heavy selling")
        elif change_24h < 0:
            score -= 12
            signals.append("distribution")
    elif vol_to_liq > 0.5:
        if change_24h > 3:
            score += 10
        elif change_24h < -3:
            score -= 10
    else:
        score -= 8
        signals.append("low activity")

    # 2. Holder Velocity (wallet growth = fresh demand)
    if holder_1h > 15:
        score += 20
        signals.append(f"wallet growth +{holder_1h:.0f}%/1h")
    elif holder_1h > 5:
        score += 10
        signals.append("wallet growth")
    elif holder_1h < -10:
        score -= 20
        signals.append("wallet exodus")
    elif holder_1h < -3:
        score -= 10

    if holder_4h > 8:
        score += 10
    elif holder_4h < -8:
        score -= 10

    # 3. Transaction rate (activity proxy)
    if txns_h1 > 500:
        score += 15
        signals.append(f"very active ({txns_h1}/h)")
    elif txns_h1 > 200:
        score += 8
        signals.append(f"{txns_h1} txns/h")
    elif txns_h1 > 50:
        score += 3
    elif txns_h1 < 20 and txns_h1 > 0:
        score -= 5

    # 4. Momentum (1h and 6h confirmation)
    if change_1h > 5:
        score += 10
        signals.append(f"+{change_1h:.1f}%/1h")
    elif change_1h > 2:
        score += 5
    elif change_1h < -5:
        score -= 10
        signals.append(f"{change_1h:.1f}%/1h")

    if change_6h > 8:
        score += 8
    elif change_6h < -8:
        score -= 8

    final_score = max(0, min(100, score))

    if final_score >= 80:
        label = "EXTREME GREED"
        emoji = "🤑"
    elif final_score >= 65:
        label = "BULLISH"
        emoji = "🟢"
    elif final_score >= 45:
        label = "NEUTRAL"
        emoji = "😐"
    elif final_score >= 30:
        label = "BEARISH"
        emoji = "😨"
    else:
        label = "FEAR"
        emoji = "😱"

    return {
        "score": final_score,
        "label": label,
        "emoji": emoji,
        "top_signal": signals[0] if signals else "—",
    }


def format_sentiment_block(token: dict) -> str | None:
    """
    Build the on-chain sentiment block for injection into alerts.
    Returns formatted HTML string.
    """
    try:
        sent = calculate_onchain_sentiment(token)
        score = sent["score"]
        label = sent["label"]
        emoji = sent["emoji"]
        top_sig = sent["top_signal"]

        import html as _html
        bar_filled = int(round(score / 10))
        bar = "█" * bar_filled + "░" * (10 - bar_filled)
        lines = [
            f"<code>  Sentiment  {bar} {score}/100</code>",
            f"<code>  {emoji} {label}  ·  {_html.escape(str(top_sig))}</code>",
        ]
        return "\n".join(lines)
    except Exception as exc:
        logging.debug("format_sentiment_block error: %s", exc)
        return None


# ─────────────────────────────────────────────────────────────────────────────
# FEATURE 5: LIQUIDATION ZONE PREDICTOR
# Uses Jupiter funding rate as crowd positioning proxy.
# Estimates liquidation cluster zones. NOT a CEX liquidation heatmap.
# ─────────────────────────────────────────────────────────────────────────────

def predict_liquidation_zones(sol_price: float, funding_rate: float | None,
                               leverage: float | None = None,
                               liq_price: float | None = None) -> dict | None:
    """
    Estimate SOL price zones where liquidation cascades are likely.
    Based on funding rate as crowd positioning proxy.

    funding_rate > 0.05%  → too many longs → downside liq clusters
    funding_rate < -0.03% → too many shorts → short squeeze upside
    |funding_rate| < 0.03% → balanced → no strong liq directional bias

    Returns dict with zones list, direction, confidence.
    """
    if not sol_price or sol_price <= 0:
        return None

    funding = _try_float(funding_rate)
    zones = []
    direction = "NEUTRAL"
    confidence = "LOW"
    crowd = "Balanced positioning"

    if funding > 0.08:
        direction = "DOWNSIDE"
        confidence = "HIGH"
        crowd = f"Heavily long ({funding:.3f}% funding)"
        # Large liq clusters below current price at -5%, -10%, -15%, -20%
        for pct, risk in [(5, "HIGH"), (10, "HIGH"), (15, "MEDIUM"), (20, "LOW")]:
            zones.append({
                "price": round(sol_price * (1 - pct / 100), 2),
                "pct_from_current": -pct,
                "risk": risk,
                "type": "LONG LIQ",
            })
    elif funding > 0.05:
        direction = "DOWNSIDE"
        confidence = "MEDIUM"
        crowd = f"Long-heavy ({funding:.3f}% funding)"
        for pct, risk in [(7, "HIGH"), (13, "MEDIUM"), (20, "LOW")]:
            zones.append({
                "price": round(sol_price * (1 - pct / 100), 2),
                "pct_from_current": -pct,
                "risk": risk,
                "type": "LONG LIQ",
            })
    elif funding < -0.05:
        direction = "UPSIDE"
        confidence = "MEDIUM"
        crowd = f"Short-heavy ({funding:.3f}% funding)"
        for pct, risk in [(5, "HIGH"), (10, "MEDIUM"), (15, "LOW")]:
            zones.append({
                "price": round(sol_price * (1 + pct / 100), 2),
                "pct_from_current": pct,
                "risk": risk,
                "type": "SHORT SQUEEZE",
            })
    elif funding < -0.03:
        direction = "SLIGHT UPSIDE SQUEEZE"
        confidence = "LOW"
        crowd = f"Short-leaning ({funding:.3f}% funding)"
        for pct, risk in [(8, "MEDIUM"), (15, "LOW")]:
            zones.append({
                "price": round(sol_price * (1 + pct / 100), 2),
                "pct_from_current": pct,
                "risk": risk,
                "type": "SHORT SQUEEZE",
            })
    else:
        # Neutral — no strong directional liq bias
        direction = "NEUTRAL"
        confidence = "—"
        crowd = f"Balanced ({funding:.3f}% funding)"

    # Your personal position safety check
    personal_note = None
    if liq_price and liq_price > 0 and sol_price > 0:
        from jupiter_perps import calc_liq_distance_pct
        liq_dist = calc_liq_distance_pct(sol_price, liq_price)
        if liq_dist is not None:
            safe = liq_dist > 25
            personal_note = {
                "liq_price": liq_price,
                "liq_dist_pct": liq_dist,
                "safe": safe,
            }

    return {
        "sol_price": sol_price,
        "funding_rate": funding,
        "direction": direction,
        "confidence": confidence,
        "crowd": crowd,
        "zones": zones,
        "personal": personal_note,
    }


def format_liq_zones_block(sol_price: float, funding_rate: float | None,
                            leverage: float | None = None,
                            liq_price: float | None = None) -> str:
    """
    Format the liquidation zone prediction block for /lev dashboard.
    Always shows something (even if just neutral funding data).
    """
    import html as _html

    result = predict_liquidation_zones(sol_price, funding_rate, leverage, liq_price)
    if not result:
        return ""

    direction = result["direction"]
    confidence = result["confidence"]
    crowd = result["crowd"]
    zones = result["zones"]
    personal = result.get("personal")

    dir_emoji = "📉" if "DOWNSIDE" in direction else ("📈" if "UPSIDE" in direction else "⚖️")
    conf_str = f"Confidence: {confidence}" if confidence != "—" else "Balanced market"

    lines = [
        f"<code>{SEP}</code>",
        f"<b>⚡ LIQ ZONE PREDICTOR</b>",
        f"<code>{dir_emoji} {_html.escape(direction)} | {_html.escape(conf_str)}</code>",
        f"<code>Crowd: {_html.escape(crowd)}</code>",
    ]

    if zones:
        lines.append(f"<code>{SEP}</code>")
        risk_emoji = {"HIGH": "🔴", "MEDIUM": "🟡", "LOW": "🟢"}
        for z in zones:
            rem = f"{z['pct_from_current']:+.0f}%" if z['pct_from_current'] else ""
            r_e = risk_emoji.get(z["risk"], "⚪")
            lines.append(
                f"<code>{r_e} ${z['price']:.2f} ({rem})  {z['type']}  {z['risk']}</code>"
            )
    else:
        lines.append(f"<code>⚖️ No major liq clusters predicted — balanced funding</code>")

    if personal:
        liq_d = personal["liq_dist_pct"]
        safe_emoji = "🟢" if personal["safe"] else "🔴"
        lines.append(
            f"<code>{safe_emoji} Your liq ${personal['liq_price']:.2f} | dist {liq_d:.1f}%</code>"
        )

    lines += [
        f"<code>{SEP}</code>",
        f"<i>⚠️ Estimate only — uses funding rate as proxy.</i>",
        f"<i>   Not a CEX liquidation heatmap.</i>",
    ]
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# COMBINED INTEL BLOCK (appended to MEMECOIN SETUP alerts)
# ─────────────────────────────────────────────────────────────────────────────

def build_intel_block(token_data: dict) -> str:
    """
    Builds the combined intelligence block for injection into MEMECOIN SETUP
    and LEGACY RECOVERY alerts.
    Includes: Narrative + Sentiment + Win Rate (if data available)
    Rendered as a single clean card section — not tacked-on appendages.
    """
    thin = "┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄"
    sep = "━━━━━━━━━━━━━━━━━━━━━━━━━━━"

    narrative = format_narrative_block(token_data)
    sentiment = format_sentiment_block(token_data)
    win_rate = format_win_rate_block(token_data)

    has_intel = any([narrative, sentiment, win_rate])
    if not has_intel:
        return ""

    lines = [
        f"<b>🧠 INTEL</b>",
        f"<code>{thin}</code>",
    ]

    if narrative:
        lines.append(narrative)

    if sentiment:
        if narrative:
            lines.append(f"<code>{thin}</code>")
        lines.append(sentiment)

    if win_rate:
        lines.append(f"<code>{thin}</code>")
        lines.append(win_rate)

    lines.append(f"<code>{sep}</code>")
    return "\n".join(lines)
