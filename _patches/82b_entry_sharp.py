"""
Patch 82b — main.py: Entry Sharpening + Edge Stats API
1. Raise SCALP_5M_THRESHOLD default from 0.15 to 0.25
2. Add volume filter + RSI + MACD + dip-buy to scalp scanner
3. Add volume filter + RSI + MACD + dip-buy to mid scanner
4. Fix MID threshold env var (MID_5M_THRESHOLD → MID_15M_THRESHOLD)
5. Extend ALLOWED_KEYS with Patch 82 config keys
6. New GET /api/brain/edge-stats endpoint
7. Update .env: SCALP_5M_THRESHOLD=0.25
"""
import subprocess, sys, os
from pathlib import Path

ROOT = Path("/root/memecoin_engine")
MAIN = ROOT / "dashboard" / "backend" / "main.py"

text = MAIN.read_text()

# ─────────────────────────────────────────────────────────────────────────────
# R1 — Raise SCALP_5M_THRESHOLD default from 0.15 to 0.25
# ─────────────────────────────────────────────────────────────────────────────
OLD_R1 = 'threshold = float(os.getenv("SCALP_5M_THRESHOLD", "0.15"))'
NEW_R1 = 'threshold = float(os.getenv("SCALP_5M_THRESHOLD", "0.25"))'

assert OLD_R1 in text, "R1 anchor (SCALP_5M_THRESHOLD 0.15) not found"
text = text.replace(OLD_R1, NEW_R1)
print("✓ R1: SCALP_5M_THRESHOLD default raised to 0.25")

# ─────────────────────────────────────────────────────────────────────────────
# R2 — Scalp scanner: volume filter + RSI + MACD + dip-buy
# ─────────────────────────────────────────────────────────────────────────────
OLD_R2 = (
    '                    _sent_score = 0.0\n'
    '                    _sent_boost = 0\n'
    '                    if chg_5m > threshold:\n'
    '                        # ── Fetch sentiment ──\n'
    '                        try:\n'
    '                            _ensure_engine_path()\n'
    '                            from utils.x_sentiment import get_sentiment\n'
    '                            _sent = await get_sentiment(symbol)\n'
    '                            _sent_score = _sent.get("sentiment_score", 0)\n'
    '                            _sent_boost = _sent.get("boost", 0)\n'
    '                            if _sent_boost != 0:\n'
    '                                logger.info("[SENTIMENT] %s score=%.2f boost=%+d vol_spike=%s",\n'
    '                                            symbol, _sent_score, _sent_boost, _sent.get("volume_spike"))\n'
    '                        except Exception as _se:\n'
    '                            pass\n'
    '\n'
    '                        await execute_perp_signal({\n'
    '                            "symbol": symbol, "side": "LONG",\n'
    '                            "regime_label": phase or "SCALP", "source": "scalp",\n'
    '                    "sentiment_score": _sent_score,\n'
    '                    "sentiment_boost": _sent_boost,\n'
    '                            "momentum_5m": round(chg_5m, 3),\n'
    '                            "price_at_signal": price_now,\n'
    '                            "rsi_14": rsi,\n'
    '                            "macd_hist": macd_hist,\n'
    '                            "macd_cross": macd_cross,\n'
    '                            "atr_pct": atr,\n'
    '                        })\n'
    '                        log.info("[SCALP SCAN] -> LONG %s  5m=%.3f%%  RSI=%s MACD_cross=%s", symbol, chg_5m, rsi, macd_cross)\n'
    '                    elif chg_5m < -threshold:\n'
    '                        await execute_perp_signal({\n'
    '                            "symbol": symbol, "side": "SHORT",\n'
    '                            "regime_label": phase or "SCALP", "source": "scalp",\n'
    '                    "sentiment_score": _sent_score,\n'
    '                    "sentiment_boost": _sent_boost,\n'
    '                            "momentum_5m": round(chg_5m, 3),\n'
    '                            "price_at_signal": price_now,\n'
    '                            "rsi_14": rsi,\n'
    '                            "macd_hist": macd_hist,\n'
    '                            "macd_cross": macd_cross,\n'
    '                            "atr_pct": atr,\n'
    '                        })\n'
    '                        log.info("[SCALP SCAN] -> SHORT %s  5m=%.3f%%  RSI=%s MACD_cross=%s", symbol, chg_5m, rsi, macd_cross)'
)

NEW_R2 = (
    '                    # ── Volume filter (Patch 82) ──────────────────────────\n'
    '                    _volumes = [float(c[6]) for c in candles[-22:-1]] if len(candles) >= 22 else [float(c[6]) for c in candles[:-1]]\n'
    '                    _avg_vol = sum(_volumes) / len(_volumes) if _volumes else 0\n'
    '                    _curr_vol = float(candles[-2][6])\n'
    '                    _vol_ratio = round(_curr_vol / _avg_vol, 2) if _avg_vol > 0 else 0\n'
    '                    if _avg_vol > 0 and _curr_vol < _avg_vol * 1.5:\n'
    '                        log.debug("[SCALP] %s SKIP vol %.1f < 1.5x avg %.1f (r=%.2f)", symbol, _curr_vol, _avg_vol, _vol_ratio)\n'
    '                        continue\n'
    '\n'
    '                    # ── Dip-buy detection (Patch 82) ─────────────────────\n'
    '                    _signal_source = "scalp"\n'
    '                    _dip_buy_long = False\n'
    '                    _dip_buy_short = False\n'
    '                    if len(candles) >= 24:\n'
    '                        _older_avg = sum(float(c[4]) for c in candles[-24:-12]) / 12\n'
    '                        _recent_avg = sum(float(c[4]) for c in candles[-12:-1]) / 11\n'
    '                        _trend_1h = (_recent_avg - _older_avg) / _older_avg * 100 if _older_avg > 0 else 0\n'
    '                        if _trend_1h > 0.3 and chg_5m < -0.1 and (rsi is None or rsi < 65):\n'
    '                            _dip_buy_long = True\n'
    '                            _signal_source = "dip_buy"\n'
    '                            log.info("[SCALP] %s DIP_BUY LONG 1h=+%.2f%% dip=%.3f%% RSI=%s vol_r=%.1f", symbol, _trend_1h, chg_5m, rsi, _vol_ratio)\n'
    '                        elif _trend_1h < -0.3 and chg_5m > 0.1 and (rsi is None or rsi > 35):\n'
    '                            _dip_buy_short = True\n'
    '                            _signal_source = "dip_buy"\n'
    '                            log.info("[SCALP] %s DIP_BUY SHORT 1h=%.2f%% bounce=+%.3f%% RSI=%s vol_r=%.1f", symbol, _trend_1h, chg_5m, rsi, _vol_ratio)\n'
    '\n'
    '                    _sent_score = 0.0\n'
    '                    _sent_boost = 0\n'
    '                    if chg_5m > threshold or _dip_buy_long:\n'
    '                        # ── RSI filter (Patch 82) ──\n'
    '                        if rsi is not None and rsi > 75 and not _dip_buy_long:\n'
    '                            log.info("[SCALP] %s SKIP LONG: RSI=%.1f overbought", symbol, rsi)\n'
    '                            continue\n'
    '                        # ── MACD filter (Patch 82) ──\n'
    '                        if macd_hist is not None and macd_hist < 0 and macd_cross != "BULLISH" and not _dip_buy_long:\n'
    '                            log.info("[SCALP] %s SKIP LONG: MACD bearish h=%.4f", symbol, macd_hist)\n'
    '                            continue\n'
    '\n'
    '                        # ── Fetch sentiment ──\n'
    '                        try:\n'
    '                            _ensure_engine_path()\n'
    '                            from utils.x_sentiment import get_sentiment\n'
    '                            _sent = await get_sentiment(symbol)\n'
    '                            _sent_score = _sent.get("sentiment_score", 0)\n'
    '                            _sent_boost = _sent.get("boost", 0)\n'
    '                            if _sent_boost != 0:\n'
    '                                logger.info("[SENTIMENT] %s score=%.2f boost=%+d vol_spike=%s",\n'
    '                                            symbol, _sent_score, _sent_boost, _sent.get("volume_spike"))\n'
    '                        except Exception as _se:\n'
    '                            pass\n'
    '\n'
    '                        await execute_perp_signal({\n'
    '                            "symbol": symbol, "side": "LONG",\n'
    '                            "regime_label": phase or "SCALP", "source": _signal_source,\n'
    '                    "sentiment_score": _sent_score,\n'
    '                    "sentiment_boost": _sent_boost,\n'
    '                            "momentum_5m": round(chg_5m, 3),\n'
    '                            "price_at_signal": price_now,\n'
    '                            "rsi_14": rsi,\n'
    '                            "macd_hist": macd_hist,\n'
    '                            "macd_cross": macd_cross,\n'
    '                            "atr_pct": atr,\n'
    '                            "vol_ratio": _vol_ratio,\n'
    '                        })\n'
    '                        log.info("[SCALP] -> LONG %s 5m=%.3f%% RSI=%s MACD=%s src=%s vol=%.1f", symbol, chg_5m, rsi, macd_cross, _signal_source, _vol_ratio)\n'
    '                    elif chg_5m < -threshold or _dip_buy_short:\n'
    '                        # ── RSI filter (Patch 82) ──\n'
    '                        if rsi is not None and rsi < 25 and not _dip_buy_short:\n'
    '                            log.info("[SCALP] %s SKIP SHORT: RSI=%.1f oversold", symbol, rsi)\n'
    '                            continue\n'
    '                        # ── MACD filter (Patch 82) ──\n'
    '                        if macd_hist is not None and macd_hist > 0 and macd_cross != "BEARISH" and not _dip_buy_short:\n'
    '                            log.info("[SCALP] %s SKIP SHORT: MACD bullish h=%.4f", symbol, macd_hist)\n'
    '                            continue\n'
    '\n'
    '                        await execute_perp_signal({\n'
    '                            "symbol": symbol, "side": "SHORT",\n'
    '                            "regime_label": phase or "SCALP", "source": _signal_source,\n'
    '                    "sentiment_score": _sent_score,\n'
    '                    "sentiment_boost": _sent_boost,\n'
    '                            "momentum_5m": round(chg_5m, 3),\n'
    '                            "price_at_signal": price_now,\n'
    '                            "rsi_14": rsi,\n'
    '                            "macd_hist": macd_hist,\n'
    '                            "macd_cross": macd_cross,\n'
    '                            "atr_pct": atr,\n'
    '                            "vol_ratio": _vol_ratio,\n'
    '                        })\n'
    '                        log.info("[SCALP] -> SHORT %s 5m=%.3f%% RSI=%s MACD=%s src=%s vol=%.1f", symbol, chg_5m, rsi, macd_cross, _signal_source, _vol_ratio)'
)

assert OLD_R2 in text, "R2 anchor (scalp signal block) not found"
text = text.replace(OLD_R2, NEW_R2)
print("✓ R2: Scalp scanner: volume + RSI + MACD + dip-buy filters")

# ─────────────────────────────────────────────────────────────────────────────
# R3 — Mid scanner: volume filter + RSI + MACD + dip-buy
# ─────────────────────────────────────────────────────────────────────────────
OLD_R3 = (
    '                    _sent_score = 0.0\n'
    '                    _sent_boost = 0\n'
    '                    if chg_15m > threshold:\n'
    '                        # ── Fetch sentiment ──\n'
    '                        try:\n'
    '                            _ensure_engine_path()\n'
    '                            from utils.x_sentiment import get_sentiment\n'
    '                            _sent = await get_sentiment(symbol)\n'
    '                            _sent_score = _sent.get("sentiment_score", 0)\n'
    '                            _sent_boost = _sent.get("boost", 0)\n'
    '                            if _sent_boost != 0:\n'
    '                                logger.info("[SENTIMENT] %s score=%.2f boost=%+d vol_spike=%s",\n'
    '                                            symbol, _sent_score, _sent_boost, _sent.get("volume_spike"))\n'
    '                        except Exception as _se:\n'
    '                            pass\n'
    '\n'
    '                        await execute_perp_signal({\n'
    '                            "symbol": symbol, "side": "LONG",\n'
    '                            "regime_label": phase or "MID", "source": "mid",\n'
    '                            "sentiment_score": _sent_score,\n'
    '                            "sentiment_boost": _sent_boost,\n'
    '                            "momentum_15m": round(chg_15m, 3),\n'
    '                            "price_at_signal": price_now,\n'
    '                            "rsi_14": rsi,\n'
    '                            "macd_hist": macd_hist,\n'
    '                            "macd_cross": macd_cross,\n'
    '                            "atr_pct": atr,\n'
    '                        })\n'
    '                        log.info("[MID SCAN] -> LONG %s  15m=%.3f%%  RSI=%s MACD_cross=%s", symbol, chg_15m, rsi, macd_cross)\n'
    '                    elif chg_15m < -threshold:\n'
    '                        await execute_perp_signal({\n'
    '                            "symbol": symbol, "side": "SHORT",\n'
    '                            "regime_label": phase or "MID", "source": "mid",\n'
    '                            "sentiment_score": _sent_score,\n'
    '                            "sentiment_boost": _sent_boost,\n'
    '                            "momentum_15m": round(chg_15m, 3),\n'
    '                            "price_at_signal": price_now,\n'
    '                            "rsi_14": rsi,\n'
    '                            "macd_hist": macd_hist,\n'
    '                            "macd_cross": macd_cross,\n'
    '                            "atr_pct": atr,\n'
    '                        })\n'
    '                        log.info("[MID SCAN] -> SHORT %s  15m=%.3f%%  RSI=%s MACD_cross=%s", symbol, chg_15m, rsi, macd_cross)'
)

NEW_R3 = (
    '                    # ── Volume filter (Patch 82) ──────────────────────────\n'
    '                    _volumes = [float(c[6]) for c in candles[-22:-1]] if len(candles) >= 22 else [float(c[6]) for c in candles[:-1]]\n'
    '                    _avg_vol = sum(_volumes) / len(_volumes) if _volumes else 0\n'
    '                    _curr_vol = float(candles[-2][6])\n'
    '                    _vol_ratio = round(_curr_vol / _avg_vol, 2) if _avg_vol > 0 else 0\n'
    '                    if _avg_vol > 0 and _curr_vol < _avg_vol * 1.5:\n'
    '                        log.debug("[MID] %s SKIP vol %.1f < 1.5x avg %.1f (r=%.2f)", symbol, _curr_vol, _avg_vol, _vol_ratio)\n'
    '                        continue\n'
    '\n'
    '                    # ── Dip-buy detection (Patch 82) — 4h trend on 15m candles ──\n'
    '                    _signal_source = "mid"\n'
    '                    _dip_buy_long = False\n'
    '                    _dip_buy_short = False\n'
    '                    if len(candles) >= 32:\n'
    '                        _older_avg = sum(float(c[4]) for c in candles[-32:-16]) / 16\n'
    '                        _recent_avg = sum(float(c[4]) for c in candles[-16:-1]) / 15\n'
    '                        _trend_4h = (_recent_avg - _older_avg) / _older_avg * 100 if _older_avg > 0 else 0\n'
    '                        if _trend_4h > 0.5 and chg_15m < -0.15 and (rsi is None or rsi < 65):\n'
    '                            _dip_buy_long = True\n'
    '                            _signal_source = "dip_buy"\n'
    '                            log.info("[MID] %s DIP_BUY LONG 4h=+%.2f%% dip=%.3f%% RSI=%s vol_r=%.1f", symbol, _trend_4h, chg_15m, rsi, _vol_ratio)\n'
    '                        elif _trend_4h < -0.5 and chg_15m > 0.15 and (rsi is None or rsi > 35):\n'
    '                            _dip_buy_short = True\n'
    '                            _signal_source = "dip_buy"\n'
    '                            log.info("[MID] %s DIP_BUY SHORT 4h=%.2f%% bounce=+%.3f%% RSI=%s vol_r=%.1f", symbol, _trend_4h, chg_15m, rsi, _vol_ratio)\n'
    '\n'
    '                    _sent_score = 0.0\n'
    '                    _sent_boost = 0\n'
    '                    if chg_15m > threshold or _dip_buy_long:\n'
    '                        # ── RSI filter (Patch 82) ──\n'
    '                        if rsi is not None and rsi > 75 and not _dip_buy_long:\n'
    '                            log.info("[MID] %s SKIP LONG: RSI=%.1f overbought", symbol, rsi)\n'
    '                            continue\n'
    '                        # ── MACD filter (Patch 82) ──\n'
    '                        if macd_hist is not None and macd_hist < 0 and macd_cross != "BULLISH" and not _dip_buy_long:\n'
    '                            log.info("[MID] %s SKIP LONG: MACD bearish h=%.4f", symbol, macd_hist)\n'
    '                            continue\n'
    '\n'
    '                        # ── Fetch sentiment ──\n'
    '                        try:\n'
    '                            _ensure_engine_path()\n'
    '                            from utils.x_sentiment import get_sentiment\n'
    '                            _sent = await get_sentiment(symbol)\n'
    '                            _sent_score = _sent.get("sentiment_score", 0)\n'
    '                            _sent_boost = _sent.get("boost", 0)\n'
    '                            if _sent_boost != 0:\n'
    '                                logger.info("[SENTIMENT] %s score=%.2f boost=%+d vol_spike=%s",\n'
    '                                            symbol, _sent_score, _sent_boost, _sent.get("volume_spike"))\n'
    '                        except Exception as _se:\n'
    '                            pass\n'
    '\n'
    '                        await execute_perp_signal({\n'
    '                            "symbol": symbol, "side": "LONG",\n'
    '                            "regime_label": phase or "MID", "source": _signal_source,\n'
    '                            "sentiment_score": _sent_score,\n'
    '                            "sentiment_boost": _sent_boost,\n'
    '                            "momentum_15m": round(chg_15m, 3),\n'
    '                            "price_at_signal": price_now,\n'
    '                            "rsi_14": rsi,\n'
    '                            "macd_hist": macd_hist,\n'
    '                            "macd_cross": macd_cross,\n'
    '                            "atr_pct": atr,\n'
    '                            "vol_ratio": _vol_ratio,\n'
    '                        })\n'
    '                        log.info("[MID] -> LONG %s 15m=%.3f%% RSI=%s MACD=%s src=%s vol=%.1f", symbol, chg_15m, rsi, macd_cross, _signal_source, _vol_ratio)\n'
    '                    elif chg_15m < -threshold or _dip_buy_short:\n'
    '                        # ── RSI filter (Patch 82) ──\n'
    '                        if rsi is not None and rsi < 25 and not _dip_buy_short:\n'
    '                            log.info("[MID] %s SKIP SHORT: RSI=%.1f oversold", symbol, rsi)\n'
    '                            continue\n'
    '                        # ── MACD filter (Patch 82) ──\n'
    '                        if macd_hist is not None and macd_hist > 0 and macd_cross != "BEARISH" and not _dip_buy_short:\n'
    '                            log.info("[MID] %s SKIP SHORT: MACD bullish h=%.4f", symbol, macd_hist)\n'
    '                            continue\n'
    '\n'
    '                        await execute_perp_signal({\n'
    '                            "symbol": symbol, "side": "SHORT",\n'
    '                            "regime_label": phase or "MID", "source": _signal_source,\n'
    '                            "sentiment_score": _sent_score,\n'
    '                            "sentiment_boost": _sent_boost,\n'
    '                            "momentum_15m": round(chg_15m, 3),\n'
    '                            "price_at_signal": price_now,\n'
    '                            "rsi_14": rsi,\n'
    '                            "macd_hist": macd_hist,\n'
    '                            "macd_cross": macd_cross,\n'
    '                            "atr_pct": atr,\n'
    '                            "vol_ratio": _vol_ratio,\n'
    '                        })\n'
    '                        log.info("[MID] -> SHORT %s 15m=%.3f%% RSI=%s MACD=%s src=%s vol=%.1f", symbol, chg_15m, rsi, macd_cross, _signal_source, _vol_ratio)'
)

assert OLD_R3 in text, "R3 anchor (mid signal block) not found"
text = text.replace(OLD_R3, NEW_R3)
print("✓ R3: Mid scanner: volume + RSI + MACD + dip-buy filters")

# ─────────────────────────────────────────────────────────────────────────────
# R4 — Fix MID threshold env var name (MID_5M_THRESHOLD → MID_15M_THRESHOLD)
# ─────────────────────────────────────────────────────────────────────────────
OLD_R4 = '"MID_5M_THRESHOLD", "0.30"'
NEW_R4 = '"MID_15M_THRESHOLD", "0.30"'

if OLD_R4 in text:
    text = text.replace(OLD_R4, NEW_R4)
    print("✓ R4: Fixed MID threshold env var to MID_15M_THRESHOLD")
else:
    print("⚠ R4: MID_5M_THRESHOLD not found (may already be fixed)")

# ─────────────────────────────────────────────────────────────────────────────
# R5 — Extend ALLOWED_KEYS with Patch 82 config keys
# ─────────────────────────────────────────────────────────────────────────────
OLD_R5 = '        "PORTFOLIO_MAX_EXPOSURE",\n    }'
NEW_R5 = (
    '        "PORTFOLIO_MAX_EXPOSURE",\n'
    '        # Edge Acceleration keys (Patch 82)\n'
    '        "SCALP_BREAKEVEN_TRIGGER", "EARLY_CUT_MINUTES",\n'
    '        "EARLY_CUT_LOSS_PCT", "WINNER_EXTEND_TRAIL_PCT",\n'
    '    }'
)

assert OLD_R5 in text, "R5 anchor (ALLOWED_KEYS closing) not found"
text = text.replace(OLD_R5, NEW_R5)
print("✓ R5: ALLOWED_KEYS extended with 4 Edge Acceleration keys")

# ─────────────────────────────────────────────────────────────────────────────
# R6 — New GET /api/brain/edge-stats endpoint (before journal/learnings)
# ─────────────────────────────────────────────────────────────────────────────
ANCHOR_R6 = '@app.get("/api/journal/learnings")'
assert ANCHOR_R6 in text, f"R6 anchor '{ANCHOR_R6}' not found"

EDGE_STATS_EP = '''@app.get("/api/brain/edge-stats")
async def brain_edge_stats(_: str = Depends(get_current_user)):
    """Patch 82 edge metrics: exit types, dip-buys, filter skips, trail stats."""
    import sqlite3 as _sq
    from datetime import datetime, timedelta
    db_path = os.path.join(_engine_root(), "data_storage", "engine.db")
    cutoff = (datetime.utcnow() - timedelta(hours=24)).isoformat()
    out = {
        "exit_breakdown_24h": {},
        "exit_breakdown_all": {},
        "dip_buy_count_24h": 0,
        "dip_buy_count_all": 0,
        "dip_buy_win_rate": 0,
        "trail_active_count": 0,
        "avg_winner_pnl": 0,
        "avg_loser_pnl": 0,
        "breakeven_saves_24h": 0,
        "ts": datetime.utcnow().isoformat() + "Z",
    }
    try:
        with _sq.connect(db_path) as c:
            # Exit reason breakdown — last 24h
            rows24 = c.execute(
                "SELECT exit_reason, COUNT(*), AVG(pnl_pct) FROM perp_positions "
                "WHERE status=\'CLOSED\' AND dry_run=1 AND closed_ts_utc >= ? "
                "GROUP BY exit_reason ORDER BY COUNT(*) DESC", (cutoff,)
            ).fetchall()
            out["exit_breakdown_24h"] = {
                r[0] or "UNKNOWN": {"count": r[1], "avg_pnl": round(float(r[2] or 0), 3)}
                for r in rows24
            }

            # Exit reason breakdown — all time
            rows_all = c.execute(
                "SELECT exit_reason, COUNT(*), AVG(pnl_pct) FROM perp_positions "
                "WHERE status=\'CLOSED\' AND dry_run=1 AND pnl_pct IS NOT NULL "
                "GROUP BY exit_reason ORDER BY COUNT(*) DESC"
            ).fetchall()
            out["exit_breakdown_all"] = {
                r[0] or "UNKNOWN": {"count": r[1], "avg_pnl": round(float(r[2] or 0), 3)}
                for r in rows_all
            }

            # Dip-buy trades (source=dip_buy in notes)
            try:
                db24 = c.execute(
                    "SELECT COUNT(*) FROM perp_positions "
                    "WHERE dry_run=1 AND notes LIKE \'%dip_buy%\' AND opened_ts_utc >= ?",
                    (cutoff,)
                ).fetchone()[0]
                out["dip_buy_count_24h"] = db24

                db_all = c.execute(
                    "SELECT pnl_pct FROM perp_positions "
                    "WHERE dry_run=1 AND status=\'CLOSED\' AND notes LIKE \'%dip_buy%\' "
                    "AND pnl_pct IS NOT NULL"
                ).fetchall()
                out["dip_buy_count_all"] = len(db_all)
                if db_all:
                    wins = sum(1 for r in db_all if float(r[0]) > 0)
                    out["dip_buy_win_rate"] = round(wins / len(db_all) * 100, 1)
            except Exception:
                pass

            # Active trailing stops
            try:
                trail_ct = c.execute(
                    "SELECT COUNT(*) FROM perp_positions "
                    "WHERE status=\'OPEN\' AND trail_stop_price IS NOT NULL AND trail_stop_price > 0"
                ).fetchone()[0]
                out["trail_active_count"] = trail_ct
            except Exception:
                pass

            # Avg winner vs loser PnL (last 50 trades)
            try:
                recent = c.execute(
                    "SELECT pnl_pct FROM perp_positions "
                    "WHERE status=\'CLOSED\' AND dry_run=1 AND pnl_pct IS NOT NULL "
                    "ORDER BY closed_ts_utc DESC LIMIT 50"
                ).fetchall()
                if recent:
                    winners = [float(r[0]) for r in recent if float(r[0]) > 0]
                    losers = [float(r[0]) for r in recent if float(r[0]) < 0]
                    out["avg_winner_pnl"] = round(sum(winners) / len(winners), 3) if winners else 0
                    out["avg_loser_pnl"] = round(sum(losers) / len(losers), 3) if losers else 0
            except Exception:
                pass

            # Breakeven saves — trades where trail_stop_price was set and exited at ~0% loss
            try:
                be_saves = c.execute(
                    "SELECT COUNT(*) FROM perp_positions "
                    "WHERE status=\'CLOSED\' AND dry_run=1 AND exit_reason=\'TRAIL_STOP\' "
                    "AND pnl_pct >= -0.1 AND closed_ts_utc >= ?", (cutoff,)
                ).fetchone()[0]
                out["breakeven_saves_24h"] = be_saves
            except Exception:
                pass

    except Exception as exc:
        out["error"] = str(exc)

    return JSONResponse(out)


'''

text = text.replace(ANCHOR_R6, EDGE_STATS_EP + ANCHOR_R6)
print("✓ R6: /api/brain/edge-stats endpoint added")

# ─────────────────────────────────────────────────────────────────────────────
# R7 — Update .env: SCALP_5M_THRESHOLD=0.25
# ─────────────────────────────────────────────────────────────────────────────
env_path = ROOT / ".env"
env_text = env_path.read_text()

if "SCALP_5M_THRESHOLD" in env_text:
    import re
    env_text = re.sub(r'SCALP_5M_THRESHOLD=\S*', 'SCALP_5M_THRESHOLD=0.25', env_text)
else:
    env_text += "\nSCALP_5M_THRESHOLD=0.25\n"
env_path.write_text(env_text)
print("✓ R7: .env SCALP_5M_THRESHOLD=0.25")

# ─────────────────────────────────────────────────────────────────────────────
# Write + compile
# ─────────────────────────────────────────────────────────────────────────────
MAIN.write_text(text)

r = subprocess.run(
    [sys.executable, "-m", "py_compile", str(MAIN)],
    capture_output=True, text=True
)
if r.returncode != 0:
    print("✗ compile error:", r.stderr)
    sys.exit(1)
print("✓ main.py compiles OK")
print("✓ Patch 82b complete — Entry Sharpening deployed")
