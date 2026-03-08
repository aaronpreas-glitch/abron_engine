"""
Patch 104 — Portfolio Watchman
Creates portfolio_signals table + injects background loop + API endpoint into dashboard backend.
"""
import sys, sqlite3
from pathlib import Path

ENGINE_ROOT = Path("/root/memecoin_engine")
DB_PATH = ENGINE_ROOT / "data_storage" / "engine.db"
BACKEND = ENGINE_ROOT / "dashboard" / "backend" / "main.py"

# ── Step 1: Create DB table ───────────────────────────────────────────────────

conn = sqlite3.connect(str(DB_PATH))
conn.execute("""
CREATE TABLE IF NOT EXISTS portfolio_signals (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_utc       TEXT NOT NULL,
    coin         TEXT NOT NULL,
    signal       TEXT NOT NULL,
    price_usd    REAL,
    reason       TEXT,
    fear_greed   INTEGER,
    btc_dom_pct  REAL,
    regime       TEXT,
    regime_score REAL,
    chg_4w_pct   REAL
)
""")
conn.commit()
conn.close()
print("✅ portfolio_signals table created")

# ── Step 2: Build code blocks to inject ──────────────────────────────────────

LOOP_AND_ENDPOINT = r'''

# ── Portfolio Watchman (Patch 104) ────────────────────────────────────────────

async def _portfolio_watchman_loop():
    """Every 4h: compute long-term hold signals for BTC/ETH/SOL/SUI/HYPE.
    Sends Telegram alert only when a signal changes (ACCUMULATE/HOLD/REDUCE).
    """
    import asyncio, sqlite3 as _sq, logging
    from datetime import datetime, timezone
    from pathlib import Path as _P
    import requests as _req

    _log = logging.getLogger("portfolio_watchman")
    _db = _P(__file__).resolve().parents[2] / "data_storage" / "engine.db"

    _KRAKEN = {
        "BTC": ("XBTUSD",  "XXBTZUSD"),
        "ETH": ("ETHUSD",  "XETHZUSD"),
        "SOL": ("SOLUSD",  "SOLUSD"),
        "SUI": ("SUIUSD",  "SUIUSD"),
    }

    def _fear_greed():
        try:
            r = _req.get("https://api.alternative.me/fng/?limit=1", timeout=8)
            d = r.json().get("data", [{}])[0]
            return int(d.get("value", 50)), str(d.get("value_classification", "Neutral"))
        except Exception:
            return 50, "Neutral"

    def _btc_dom():
        try:
            r = _req.get("https://api.coingecko.com/api/v3/global", timeout=8)
            return float(r.json().get("data", {}).get("market_cap_percentage", {}).get("btc", 52.0))
        except Exception:
            return 52.0

    def _kraken_weekly(qpair, rpair):
        try:
            r = _req.get(
                f"https://api.kraken.com/0/public/OHLC?pair={qpair}&interval=10080",
                timeout=14,
            )
            candles = r.json().get("result", {}).get(rpair, [])
            if len(candles) < 6:
                return None, None
            price = float(candles[-2][4])
            price_4w = float(candles[-6][4])
            chg4w = (price - price_4w) / price_4w * 100 if price_4w else None
            return price, chg4w
        except Exception:
            return None, None

    def _hype_price():
        try:
            r = _req.get(
                "https://api.coingecko.com/api/v3/simple/price"
                "?ids=hyperliquid&vs_currencies=usd",
                timeout=8,
            )
            p = r.json().get("hyperliquid", {}).get("usd")
            return float(p) if p else None, None
        except Exception:
            return None, None

    def _compute_signal(rs, fg, dom, chg4w):
        rs = rs or 35.0
        fg = fg or 50
        dom = dom or 52.0
        chg = chg4w if chg4w is not None else 0.0
        if rs < 25 or fg > 85 or dom > 65 or chg < -35:
            parts = []
            if rs < 25:   parts.append(f"Regime bearish ({rs:.0f})")
            if fg > 85:   parts.append(f"F&G extreme ({fg})")
            if dom > 65:  parts.append(f"BTC dom high ({dom:.1f}%)")
            if chg < -35: parts.append(f"4w drop {chg:.1f}%")
            return "REDUCE", " · ".join(parts)
        if rs >= 45 and fg < 75 and dom < 58 and chg > -20:
            parts = []
            if rs >= 60:   parts.append("Regime bullish")
            elif rs >= 45: parts.append("Regime improving")
            if fg < 40:    parts.append("Fear = opportunity")
            elif fg < 75:  parts.append("F&G neutral")
            if chg > 5:    parts.append(f"Momentum +{chg:.1f}%")
            return "ACCUMULATE", " · ".join(parts) or "Conditions align"
        parts = []
        if 25 <= rs < 45:   parts.append(f"Regime transition ({rs:.0f})")
        if 75 <= fg <= 85:  parts.append(f"F&G elevated ({fg})")
        if 58 <= dom <= 65: parts.append(f"BTC dom {dom:.1f}%")
        return "HOLD", " · ".join(parts) or "Monitoring conditions"

    def _last_signal(coin):
        try:
            c = _sq.connect(str(_db))
            row = c.execute(
                "SELECT signal FROM portfolio_signals WHERE coin=? ORDER BY id DESC LIMIT 1",
                (coin,)
            ).fetchone()
            c.close()
            return row[0] if row else None
        except Exception:
            return None

    def _insert(coin, signal, price, reason, fg, dom, regime, rs, chg4w):
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        c = _sq.connect(str(_db))
        c.execute(
            "INSERT INTO portfolio_signals "
            "(ts_utc,coin,signal,price_usd,reason,fear_greed,btc_dom_pct,regime,regime_score,chg_4w_pct) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (ts, coin, signal, price, reason, fg, dom, regime, rs, chg4w)
        )
        c.commit()
        c.close()

    def _alert(coin, old, new, price, chg4w, fg, dom, regime, rs, reason):
        try:
            from utils.telegram_alerts import send_telegram_sync, should_rate_limit
            if should_rate_limit(f"pw_{coin}", limit_s=3600):
                return
            icons = {"ACCUMULATE": "\U0001f4e5", "HOLD": "\U0001f7e1", "REDUCE": "\U0001f4e4"}
            icon = icons.get(new, "\U0001f514")
            price_s = f"${price:,.2f}" if price else "N/A"
            chg_s = f"{chg4w:+.1f}%" if chg4w is not None else "\u2014"
            body = (
                f"{old or '\u2014'} \u2192 {new}\n"
                f"Price: {price_s}  \u00b7  4w: {chg_s}\n"
                f"F&G: {fg}  \u00b7  BTC dom: {dom:.1f}%  \u00b7  Regime: {regime} ({rs:.0f})\n"
                f"Reason: {reason}"
            )
            send_telegram_sync(f"PORTFOLIO WATCHMAN \u2014 {coin} changed", body, icon)
        except Exception as _e:
            _log.debug("Portfolio Telegram failed: %s", _e)

    await asyncio.sleep(15)  # startup delay

    while True:
        try:
            _log.info("Portfolio Watchman: running cycle")

            fg_val, fg_label = await asyncio.to_thread(_fear_greed)
            dom_val = await asyncio.to_thread(_btc_dom)

            regime_label, regime_score = "TRANSITION", 35.0
            try:
                c = _sq.connect(str(_db))
                row = c.execute(
                    "SELECT regime_label, regime_score FROM regime_snapshots "
                    "ORDER BY ts_utc DESC LIMIT 1"
                ).fetchone()
                c.close()
                if row:
                    regime_label = row[0] or "TRANSITION"
                    regime_score = float(row[1] or 35)
            except Exception:
                pass

            coins_data = {}
            for coin, (qp, rp) in _KRAKEN.items():
                price, chg4w = await asyncio.to_thread(_kraken_weekly, qp, rp)
                coins_data[coin] = (price, chg4w)
            hp, _ = await asyncio.to_thread(_hype_price)
            coins_data["HYPE"] = (hp, None)

            summary = []
            for coin, (price, chg4w) in coins_data.items():
                signal, reason = _compute_signal(regime_score, fg_val, dom_val, chg4w)
                old = await asyncio.to_thread(_last_signal, coin)
                if old is not None and old != signal:
                    await asyncio.to_thread(
                        _alert, coin, old, signal, price, chg4w,
                        fg_val, dom_val, regime_label, regime_score, reason
                    )
                await asyncio.to_thread(
                    _insert, coin, signal, price, reason,
                    fg_val, dom_val, regime_label, regime_score, chg4w
                )
                summary.append(f"{coin}={signal}")

            try:
                import sys as _sys
                _er = str(_P(__file__).resolve().parents[2])
                if _er not in _sys.path:
                    _sys.path.insert(0, _er)
                from utils.orchestrator import append_memory
                append_memory(
                    "PORTFOLIO_WATCHMAN",
                    f"PORTFOLIO_WATCHMAN: {' '.join(summary)} | "
                    f"F&G={fg_val}({fg_label}) BTC_DOM={dom_val:.1f}%"
                )
            except Exception as _me:
                _log.debug("Memory write failed: %s", _me)

            _log.info("Portfolio Watchman cycle done: %s", ", ".join(summary))

        except Exception as exc:
            _log.warning("Portfolio Watchman error: %s", exc)

        await asyncio.sleep(14400)  # 4 hours


@app.get("/api/portfolio/signals")
async def portfolio_signals_ep(_: str = Depends(get_current_user)):
    """Latest hold signal per coin for Portfolio Watchman dashboard section."""
    import sqlite3 as _sq
    from pathlib import Path as _P
    _db = _P(__file__).resolve().parents[2] / "data_storage" / "engine.db"
    try:
        c = _sq.connect(str(_db))
        c.row_factory = _sq.Row
        rows = c.execute(
            "SELECT * FROM portfolio_signals "
            "WHERE id IN (SELECT MAX(id) FROM portfolio_signals GROUP BY coin) "
            "ORDER BY coin"
        ).fetchall()
        c.close()
        signals = [dict(r) for r in rows]
        return {
            "signals": signals,
            "fear_greed": signals[0]["fear_greed"] if signals else None,
            "btc_dom_pct": signals[0]["btc_dom_pct"] if signals else None,
            "last_updated": signals[0]["ts_utc"] if signals else None,
        }
    except Exception as exc:
        log.warning("portfolio_signals_ep error: %s", exc)
        return {"signals": [], "fear_greed": None, "btc_dom_pct": None, "last_updated": None}

'''

# ── Step 3: Inject into main.py ───────────────────────────────────────────────

text = BACKEND.read_text()

ENDPOINT_MARKER = '@app.get("/api/journal/learnings")'
if ENDPOINT_MARKER not in text:
    print("❌ journal/learnings marker not found — cannot inject endpoint")
    sys.exit(1)

text = text.replace(ENDPOINT_MARKER, LOOP_AND_ENDPOINT + "\n" + ENDPOINT_MARKER, 1)
print("✅ Loop + endpoint injected before /api/journal/learnings")

# ── Step 4: Add task to lifespan ─────────────────────────────────────────────

TASK_MARKER = "task_spot_scan   = asyncio.create_task(_spot_signal_scan_loop())"
if TASK_MARKER not in text:
    print("❌ lifespan task marker not found")
    sys.exit(1)

NEW_TASKS = (
    "task_spot_scan   = asyncio.create_task(_spot_signal_scan_loop())\n"
    "    task_pw          = asyncio.create_task(_portfolio_watchman_loop())"
)
text = text.replace(TASK_MARKER, NEW_TASKS, 1)
print("✅ task_pw added to lifespan task creation")

# Add task_pw to the cancel tuple
OLD_TUPLE = (
    "task_scalp_mon, task_scalp_scan, task_spot_mon, task_spot_scan)"
)
NEW_TUPLE = (
    "task_scalp_mon, task_scalp_scan, task_spot_mon, task_spot_scan, task_pw)"
)
if OLD_TUPLE not in text:
    print("⚠️  Could not find all_tasks tuple to extend — add task_pw manually if needed")
else:
    text = text.replace(OLD_TUPLE, NEW_TUPLE, 1)
    print("✅ task_pw added to all_tasks cancel tuple")

BACKEND.write_text(text)
print("✅ main.py written")

print("\n✅ Patch 104 complete! Run: systemctl restart memecoin-dashboard")
