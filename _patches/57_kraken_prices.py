"""
Patch 57 — Switch /api/prices to Kraken (CoinGecko was 429-rate-limited on VPS)
Also: only cache when prices are valid (non-None), so the cache never goes stale with null values.
"""

from pathlib import Path
import py_compile, tempfile, shutil

MAIN = Path(__file__).resolve().parent.parent / "dashboard" / "backend" / "main.py"
main = MAIN.read_text()

OLD = '''@app.get("/api/prices")
async def crypto_prices(_: str = Depends(get_current_user)):
    import time, requests as _req
    now = time.time()
    if now - _prices_cache["ts"] < _PRICES_TTL and _prices_cache["data"]:
        return _prices_cache["data"]
    try:
        r = await asyncio.to_thread(
            lambda: _req.get(
                "https://api.coingecko.com/api/v3/simple/price"
                "?ids=bitcoin,ethereum,solana"
                "&vs_currencies=usd"
                "&include_24hr_change=true",
                timeout=8,
            ).json()
        )
        data = {
            "BTC": {
                "price": r.get("bitcoin", {}).get("usd"),
                "change_24h": r.get("bitcoin", {}).get("usd_24h_change"),
            },
            "ETH": {
                "price": r.get("ethereum", {}).get("usd"),
                "change_24h": r.get("ethereum", {}).get("usd_24h_change"),
            },
            "SOL": {
                "price": r.get("solana", {}).get("usd"),
                "change_24h": r.get("solana", {}).get("usd_24h_change"),
            },
        }
        _prices_cache["data"] = data
        _prices_cache["ts"] = now
        return data
    except Exception as exc:
        log.warning("crypto_prices error: %s", exc)
        return _prices_cache.get("data") or {}'''

NEW = '''@app.get("/api/prices")
async def crypto_prices(_: str = Depends(get_current_user)):
    import time, requests as _req
    now = time.time()
    if now - _prices_cache["ts"] < _PRICES_TTL and _prices_cache["data"]:
        return _prices_cache["data"]

    # Fetch from Kraken (no rate limits, no API key needed)
    try:
        r = await asyncio.to_thread(
            lambda: _req.get(
                "https://api.kraken.com/0/public/Ticker?pair=XBTUSD,ETHUSD,SOLUSD",
                timeout=8,
            ).json()
        )
        res = r.get("result", {})
        def _kraken_price(key):
            v = res.get(key, {})
            last  = float(v["c"][0]) if v.get("c") else None
            open_ = float(v["o"])    if v.get("o") else None
            chg   = round((last - open_) / open_ * 100, 2) if last and open_ and open_ != 0 else None
            return {"price": last, "change_24h": chg}

        data = {
            "BTC": _kraken_price("XXBTZUSD"),
            "ETH": _kraken_price("XETHZUSD"),
            "SOL": _kraken_price("SOLUSD"),
        }
        # Only cache when we have valid prices
        if any(data[s]["price"] for s in data):
            _prices_cache["data"] = data
            _prices_cache["ts"] = now
        return data
    except Exception as exc:
        log.warning("crypto_prices (kraken) error: %s", exc)

    # Fallback: return last cached data if available
    return _prices_cache.get("data") or {}'''

assert OLD in main, "Anchor not found — prices endpoint may have changed"
main = main.replace(OLD, NEW)
MAIN.write_text(main)
print("✅ main.py patched — Kraken prices endpoint")

tmp = Path(tempfile.mktemp(suffix=".py"))
shutil.copy(MAIN, tmp)
py_compile.compile(str(tmp), doraise=True)
tmp.unlink()
print("✅ main.py compiles OK")
