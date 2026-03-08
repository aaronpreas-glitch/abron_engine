#!/usr/bin/env python3
"""
Patch main.py to:
1. Add RSI-14 helper function (compute_rsi_from_candles)
2. Add RSI to scalp and MID scan signals
3. Add post-exit monitoring loop (_post_exit_monitor_loop)
4. Add post_exit_tracking table creation
5. Register post-exit loop in lifespan
"""
import re
import sys

MAIN_PY = "/root/memecoin_engine/dashboard/backend/main.py"

with open(MAIN_PY, "r") as f:
    code = f.read()

# ── 1. Add RSI helper function right before _scalp_signal_scan_loop ──
RSI_HELPER = '''
def _compute_rsi(candles: list, period: int = 14) -> float | None:
    """Compute RSI-14 from OHLC candle list.
    Each candle: [time, open, high, low, close, vwap, volume, count].
    Returns RSI 0-100 or None if not enough data.
    """
    if len(candles) < period + 2:
        return None
    # Use closing prices from completed candles (skip the last one which is forming)
    closes = [float(c[4]) for c in candles[-(period + 2):-1]]
    if len(closes) < period + 1:
        return None
    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0.0 for d in deltas]
    losses = [-d if d < 0 else 0.0 for d in deltas]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    # Smooth with Wilder's method for remaining
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)


'''

# Insert before "async def _scalp_signal_scan_loop"
if '_compute_rsi' not in code:
    code = code.replace(
        'async def _scalp_signal_scan_loop():',
        RSI_HELPER + 'async def _scalp_signal_scan_loop():',
    )
    print("✓ Added _compute_rsi helper")
else:
    print("⚠ _compute_rsi already exists, skipping")

# ── 2. Add RSI computation to scalp scan loop ──
# Find the scalp signal fire and add RSI before it
# We need to add RSI calculation after candles are fetched, before signal fire

# In the scalp loop, after "chg_5m = (curr_close - prev_close) ..." add RSI calc
if 'rsi_14' not in code.split('_scalp_signal_scan_loop')[1].split('_mid_monitor_loop')[0]:
    # Add RSI calc right after chg_5m computation in scalp loop
    old_scalp_log = '''                    log.info(
                        "[SCALP SCAN] %s  5m=%.3f%%  threshold=±%.2f%%  price=$%.2f",
                        symbol, chg_5m, threshold, price_now,
                    )'''
    new_scalp_log = '''                    rsi = _compute_rsi(candles)
                    log.info(
                        "[SCALP SCAN] %s  5m=%.3f%%  threshold=±%.2f%%  price=$%.2f  RSI=%s",
                        symbol, chg_5m, threshold, price_now, rsi,
                    )'''
    code = code.replace(old_scalp_log, new_scalp_log, 1)

    # Add rsi_14 to the LONG signal dict
    old_scalp_long = '''                        await execute_perp_signal({
                            "symbol": symbol, "side": "LONG",
                            "regime_label": phase or "SCALP", "source": "scalp",
                            "momentum_5m": round(chg_5m, 3),
                            "price_at_signal": price_now,
                        })'''
    new_scalp_long = '''                        await execute_perp_signal({
                            "symbol": symbol, "side": "LONG",
                            "regime_label": phase or "SCALP", "source": "scalp",
                            "momentum_5m": round(chg_5m, 3),
                            "price_at_signal": price_now,
                            "rsi_14": rsi,
                        })'''
    code = code.replace(old_scalp_long, new_scalp_long, 1)

    # Add rsi_14 to the SHORT signal dict
    old_scalp_short = '''                        await execute_perp_signal({
                            "symbol": symbol, "side": "SHORT",
                            "regime_label": phase or "SCALP", "source": "scalp",
                            "momentum_5m": round(chg_5m, 3),
                            "price_at_signal": price_now,
                        })'''
    new_scalp_short = '''                        await execute_perp_signal({
                            "symbol": symbol, "side": "SHORT",
                            "regime_label": phase or "SCALP", "source": "scalp",
                            "momentum_5m": round(chg_5m, 3),
                            "price_at_signal": price_now,
                            "rsi_14": rsi,
                        })'''
    code = code.replace(old_scalp_short, new_scalp_short, 1)
    print("✓ Added RSI to scalp scan signals")
else:
    print("⚠ rsi_14 already in scalp scan, skipping")

# ── 3. Add RSI to MID scan loop ──
if 'rsi_14' not in code.split('_mid_signal_scan_loop')[1].split('_spot_monitor_loop')[0]:
    # MID log line
    old_mid_log = '''                    log.info(
                        "[MID SCAN] %s  15m=%.3f%%  threshold=+-%.2f%%  price=$%.2f",
                        symbol, chg_15m, threshold, price_now,
                    )'''
    new_mid_log = '''                    rsi = _compute_rsi(candles)
                    log.info(
                        "[MID SCAN] %s  15m=%.3f%%  threshold=+-%.2f%%  price=$%.2f  RSI=%s",
                        symbol, chg_15m, threshold, price_now, rsi,
                    )'''
    code = code.replace(old_mid_log, new_mid_log, 1)

    # MID LONG signal
    old_mid_long = '''                        await execute_perp_signal({
                            "symbol": symbol, "side": "LONG",
                            "regime_label": phase or "MID", "source": "mid",
                            "momentum_15m": round(chg_15m, 3),
                            "price_at_signal": price_now,
                        })'''
    new_mid_long = '''                        await execute_perp_signal({
                            "symbol": symbol, "side": "LONG",
                            "regime_label": phase or "MID", "source": "mid",
                            "momentum_15m": round(chg_15m, 3),
                            "price_at_signal": price_now,
                            "rsi_14": rsi,
                        })'''
    code = code.replace(old_mid_long, new_mid_long, 1)

    # MID SHORT signal
    old_mid_short = '''                        await execute_perp_signal({
                            "symbol": symbol, "side": "SHORT",
                            "regime_label": phase or "MID", "source": "mid",
                            "momentum_15m": round(chg_15m, 3),
                            "price_at_signal": price_now,
                        })'''
    new_mid_short = '''                        await execute_perp_signal({
                            "symbol": symbol, "side": "SHORT",
                            "regime_label": phase or "MID", "source": "mid",
                            "momentum_15m": round(chg_15m, 3),
                            "price_at_signal": price_now,
                            "rsi_14": rsi,
                        })'''
    code = code.replace(old_mid_short, new_mid_short, 1)
    print("✓ Added RSI to MID scan signals")
else:
    print("⚠ rsi_14 already in MID scan, skipping")

# ── 4. Add post-exit monitoring loop ──
POST_EXIT_LOOP = '''

async def _post_exit_monitor_loop():
    """Background: track price 5/15/30 min after closing a position.

    This quantifies "how much more could we have made" (missed upside)
    or "how much worse would it have gotten" (avoided downside).
    Critical data for calibrating TP/SL placement.

    Creates the post_exit_tracking table if needed and fills in
    price_5m, price_15m, price_30m at the appropriate times.
    """
    import sys, sqlite3
    root = _engine_root()
    if root not in sys.path:
        sys.path.insert(0, root)
    db_path = os.path.join(root, "data_storage", "engine.db")

    # Ensure table exists
    with sqlite3.connect(db_path) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS post_exit_tracking (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                position_id INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                mode TEXT NOT NULL DEFAULT 'SWING',
                exit_price REAL NOT NULL,
                exit_reason TEXT,
                exit_ts TEXT NOT NULL,
                price_5m REAL,
                price_15m REAL,
                price_30m REAL,
                checked_5m_ts TEXT,
                checked_15m_ts TEXT,
                checked_30m_ts TEXT,
                missed_pct_5m REAL,
                missed_pct_15m REAL,
                missed_pct_30m REAL,
                would_have_continued TEXT,
                UNIQUE(position_id)
            )
        """)
        conn.commit()
    log.info("[POST-EXIT] post_exit_tracking table ready")

    import requests as _req
    from datetime import datetime, timezone, timedelta

    _KRAKEN_MAP = {"SOL": "SOLUSD", "BTC": "XBTUSD", "ETH": "ETHUSD"}
    _KRAKEN_RESULT = {"SOL": "SOLUSD", "BTC": "XXBTZUSD", "ETH": "XETHZUSD"}

    def _fetch_current(sym: str) -> float | None:
        pair = _KRAKEN_MAP.get(sym.upper())
        if not pair:
            return None
        try:
            r = _req.get(f"https://api.kraken.com/0/public/Ticker?pair={pair}", timeout=8)
            data = r.json()
            result_key = _KRAKEN_RESULT.get(sym.upper(), pair)
            return float(data["result"][result_key]["c"][0])
        except Exception:
            return None

    while True:
        await asyncio.sleep(60)  # check every minute
        try:
            now = datetime.now(timezone.utc)

            with sqlite3.connect(db_path) as conn:
                conn.row_factory = sqlite3.Row

                # Find rows that still need price checks
                pending = conn.execute("""
                    SELECT * FROM post_exit_tracking
                    WHERE price_5m IS NULL OR price_15m IS NULL OR price_30m IS NULL
                    ORDER BY exit_ts DESC
                    LIMIT 50
                """).fetchall()

            for row in pending:
                row = dict(row)
                exit_ts = datetime.fromisoformat(row["exit_ts"].replace("Z", "+00:00"))
                age_min = (now - exit_ts).total_seconds() / 60
                symbol = row["symbol"]
                side = row["side"]
                exit_price = row["exit_price"]

                updates = {}

                # 5-minute check
                if row["price_5m"] is None and age_min >= 5:
                    price = _fetch_current(symbol)
                    if price:
                        if side == "LONG":
                            missed = (price - exit_price) / exit_price * 100
                        else:
                            missed = (exit_price - price) / exit_price * 100
                        updates["price_5m"] = price
                        updates["checked_5m_ts"] = now.isoformat()
                        updates["missed_pct_5m"] = round(missed, 4)
                        log.info("[POST-EXIT] %s %s 5m: exit=$%.4f now=$%.4f missed=%.3f%%",
                                 symbol, side, exit_price, price, missed)

                # 15-minute check
                if row["price_15m"] is None and age_min >= 15:
                    price = _fetch_current(symbol)
                    if price:
                        if side == "LONG":
                            missed = (price - exit_price) / exit_price * 100
                        else:
                            missed = (exit_price - price) / exit_price * 100
                        updates["price_15m"] = price
                        updates["checked_15m_ts"] = now.isoformat()
                        updates["missed_pct_15m"] = round(missed, 4)
                        log.info("[POST-EXIT] %s %s 15m: exit=$%.4f now=$%.4f missed=%.3f%%",
                                 symbol, side, exit_price, price, missed)

                # 30-minute check
                if row["price_30m"] is None and age_min >= 30:
                    price = _fetch_current(symbol)
                    if price:
                        if side == "LONG":
                            missed = (price - exit_price) / exit_price * 100
                        else:
                            missed = (exit_price - price) / exit_price * 100
                        updates["price_30m"] = price
                        updates["checked_30m_ts"] = now.isoformat()
                        updates["missed_pct_30m"] = round(missed, 4)

                        # Determine if price continued in favorable direction
                        all_missed = [
                            updates.get("missed_pct_5m") or row.get("missed_pct_5m") or 0,
                            updates.get("missed_pct_15m") or row.get("missed_pct_15m") or 0,
                            missed,
                        ]
                        if all(m > 0.5 for m in all_missed):
                            updates["would_have_continued"] = "YES_STRONG"
                        elif missed > 0.5:
                            updates["would_have_continued"] = "YES_MILD"
                        elif missed < -0.5:
                            updates["would_have_continued"] = "NO_REVERSED"
                        else:
                            updates["would_have_continued"] = "FLAT"

                        log.info("[POST-EXIT] %s %s 30m: missed=%.3f%% verdict=%s",
                                 symbol, side, missed, updates["would_have_continued"])

                if updates:
                    set_clause = ", ".join(f"{k}=?" for k in updates)
                    vals = list(updates.values()) + [row["id"]]
                    with sqlite3.connect(db_path) as conn:
                        conn.execute(f"UPDATE post_exit_tracking SET {set_clause} WHERE id=?", vals)
                        conn.commit()

        except Exception as _e:
            log.debug("post_exit_monitor error: %s", _e)

'''

if '_post_exit_monitor_loop' not in code:
    # Insert before the lifespan function
    # Find the lifespan function marker
    lifespan_match = code.find('@asynccontextmanager')
    if lifespan_match == -1:
        lifespan_match = code.find('async def lifespan')
    if lifespan_match > 0:
        code = code[:lifespan_match] + POST_EXIT_LOOP + code[lifespan_match:]
        print("✓ Added _post_exit_monitor_loop")
    else:
        print("✗ Could not find lifespan insertion point")
else:
    print("⚠ _post_exit_monitor_loop already exists, skipping")

# ── 5. Register post-exit loop in lifespan ──
if 'task_post_exit' not in code:
    # Find where task_mid_scan is created
    if 'task_mid_scan' in code:
        old_mid_task = 'task_mid_scan = asyncio.create_task(_mid_signal_scan_loop())'
        new_mid_task = old_mid_task + '\n        task_post_exit = asyncio.create_task(_post_exit_monitor_loop())'
        code = code.replace(old_mid_task, new_mid_task, 1)
        print("✓ Registered task_post_exit in lifespan")

        # Also add to the cancel block — find where task_mid_scan is cancelled
        # Look for the cancel pattern
        if 'task_mid_scan.cancel()' in code:
            code = code.replace(
                'task_mid_scan.cancel()',
                'task_mid_scan.cancel()\n            task_post_exit.cancel()',
                1,
            )
            print("✓ Added task_post_exit to cancel block")
    else:
        print("✗ Could not find task_mid_scan for registration")
else:
    print("⚠ task_post_exit already registered, skipping")

# ── 6. Add /api/perps/post-exit endpoint ──
POST_EXIT_ENDPOINT = '''
@app.get("/api/perps/post-exit")
async def get_post_exit_data():
    """Return post-exit tracking data for the dashboard."""
    import sqlite3
    db_path = os.path.join(_engine_root(), "data_storage", "engine.db")
    try:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT * FROM post_exit_tracking
                WHERE price_30m IS NOT NULL
                ORDER BY exit_ts DESC LIMIT 100
            """).fetchall()

        data = [dict(r) for r in rows]

        # Compute aggregates by mode
        from collections import defaultdict
        by_mode = defaultdict(lambda: {"n": 0, "avg_missed_5m": 0, "avg_missed_15m": 0,
                                        "avg_missed_30m": 0, "continued_count": 0})
        for r in data:
            m = r.get("mode", "SWING")
            by_mode[m]["n"] += 1
            by_mode[m]["avg_missed_5m"] += (r.get("missed_pct_5m") or 0)
            by_mode[m]["avg_missed_15m"] += (r.get("missed_pct_15m") or 0)
            by_mode[m]["avg_missed_30m"] += (r.get("missed_pct_30m") or 0)
            if r.get("would_have_continued", "").startswith("YES"):
                by_mode[m]["continued_count"] += 1

        summary = {}
        for mode, v in by_mode.items():
            n = v["n"]
            summary[mode] = {
                "n": n,
                "avg_missed_5m": round(v["avg_missed_5m"] / n, 3) if n else 0,
                "avg_missed_15m": round(v["avg_missed_15m"] / n, 3) if n else 0,
                "avg_missed_30m": round(v["avg_missed_30m"] / n, 3) if n else 0,
                "pct_would_have_continued": round(v["continued_count"] / n * 100, 1) if n else 0,
            }

        return {"trades": data[-20:], "summary": summary}
    except Exception as e:
        return {"error": str(e), "trades": [], "summary": {}}

'''

if '/api/perps/post-exit' not in code:
    # Find the last @app.get endpoint and add after it
    # Insert before the final WebSocket handler or at end
    if '@app.get("/api/perps/stats")' in code:
        idx = code.find('@app.get("/api/perps/stats")')
        # Find the end of that function
        next_at = code.find('\n@app.', idx + 10)
        if next_at > 0:
            code = code[:next_at] + POST_EXIT_ENDPOINT + code[next_at:]
        else:
            code += POST_EXIT_ENDPOINT
    else:
        code += POST_EXIT_ENDPOINT
    print("✓ Added /api/perps/post-exit endpoint")
else:
    print("⚠ post-exit endpoint already exists, skipping")

with open(MAIN_PY, "w") as f:
    f.write(code)

print("\n✅ main.py patched successfully")
