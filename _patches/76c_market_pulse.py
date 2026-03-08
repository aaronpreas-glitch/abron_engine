"""
Patch 76c — Market Pulse: Fear & Greed + CoinGecko Trending
Backend: GET /api/social/market-pulse
  - Fear & Greed Index from alternative.me (free, no key)
  - Trending coins from CoinGecko public API (free, no key)
  - Highlights watchlist coins in trending list
  - 15-min cache
  - Completely free, no API credentials needed
"""

import subprocess, sys
from pathlib import Path

ROOT = Path("/root/memecoin_engine")
MAIN = ROOT / "dashboard/backend/main.py"

main_text = MAIN.read_text()

ANCHOR = '@app.get("/api/journal/learnings")'
assert ANCHOR in main_text, f"anchor not found: {ANCHOR}"
assert "_market_pulse_cache" not in main_text, "market-pulse already inserted"

NEW_ENDPOINT = '''
# ── Market Pulse: Fear & Greed + CoinGecko Trending ───────────────────────────
_market_pulse_cache: dict = {}
_MARKET_PULSE_TTL = 900  # 15 min

_MP_WATCHLIST = {
    "WIF", "BONK", "POPCAT", "FARTCOIN", "GRIFFAIN", "GOAT", "MOODENG", "PNUT",
    "MOTHER", "ACT", "NEIRO", "MEW", "BILLY", "MOG", "BRETT", "GIGA", "TURBO",
    "PENGU", "AURA", "CHILLGUY", "BOME", "WEN", "SELFIE",
}


@app.get("/api/social/market-pulse")
async def social_market_pulse(_: str = Depends(get_current_user)):
    """Market Pulse: Fear & Greed Index + CoinGecko trending coins. 15-min cache."""
    import time as _t
    import requests as _req

    now = _t.time()
    cached = _market_pulse_cache.get("default")
    if cached and now - cached.get("ts", 0) < _MARKET_PULSE_TTL:
        return JSONResponse(cached["data"])

    async def _fetch_fg() -> dict:
        try:
            return await asyncio.to_thread(lambda: _req.get(
                "https://api.alternative.me/fng/?limit=7", timeout=8
            ).json())
        except Exception:
            return {}

    async def _fetch_trending() -> dict:
        try:
            return await asyncio.to_thread(lambda: _req.get(
                "https://api.coingecko.com/api/v3/search/trending", timeout=8
            ).json())
        except Exception:
            return {}

    try:
        fg_data, cg_data = await asyncio.gather(_fetch_fg(), _fetch_trending())

        # Fear & Greed
        history = []
        for entry in fg_data.get("data", []):
            ts_int = int(entry.get("timestamp", 0) or 0)
            date   = datetime.utcfromtimestamp(ts_int).strftime("%m/%d") if ts_int else ""
            history.append({
                "value": int(entry.get("value", 0) or 0),
                "label": entry.get("value_classification", ""),
                "date":  date,
            })

        # CoinGecko trending
        trending = []
        watchlist_trending = []
        for c in cg_data.get("coins", [])[:15]:
            item = c.get("item", {})
            sym  = (item.get("symbol") or "").upper()
            name = item.get("name", "") or ""
            rank = item.get("market_cap_rank", 0) or 0
            score = item.get("score", 0) or 0
            pch_raw = (item.get("data", {}) or {}).get("price_change_percentage_24h", {})
            if isinstance(pch_raw, dict):
                pch24 = round(float(pch_raw.get("usd", 0) or 0), 2)
            else:
                pch24 = round(float(pch_raw or 0), 2)
            in_wl = sym in _MP_WATCHLIST
            if in_wl:
                watchlist_trending.append(sym)
            trending.append({
                "symbol":         sym,
                "name":           name,
                "rank":           rank,
                "trending_score": score,
                "price_change_24h": pch24,
                "in_watchlist":   in_wl,
            })

        result = {
            "fear_greed":        history[0] if history else None,
            "fg_history":        history[:7],
            "trending":          trending,
            "watchlist_trending": watchlist_trending,
            "source":            "coingecko+alternative.me",
            "ts":                datetime.utcnow().isoformat() + "Z",
        }
        _market_pulse_cache["default"] = {"data": result, "ts": now}
        return JSONResponse(result)

    except Exception as exc:
        log.warning("market_pulse error: %s", exc)
        cached_stale = _market_pulse_cache.get("default")
        if cached_stale:
            return JSONResponse(cached_stale["data"])
        return JSONResponse({
            "fear_greed": None, "fg_history": [], "trending": [],
            "watchlist_trending": [],
            "error": f"Market pulse unavailable: {exc}",
            "source": "coingecko+alternative.me",
        })


'''

main_text = main_text.replace(ANCHOR, NEW_ENDPOINT + ANCHOR)
assert "_market_pulse_cache" in main_text, "endpoint not inserted"
MAIN.write_text(main_text)
print("✓ main.py — /api/social/market-pulse inserted")

r = subprocess.run([sys.executable, "-m", "py_compile", str(MAIN)], capture_output=True, text=True)
if r.returncode != 0:
    print("✗ compile error:", r.stderr); sys.exit(1)
print("✓ main.py compiles OK")
