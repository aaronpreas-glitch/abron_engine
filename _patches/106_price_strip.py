"""
Patch 106 — Price Strip
Adds GET /api/prices endpoint: BTC, ETH, SOL, SUI, HYPE with 24h change.
Uses Kraken ticker (already used everywhere, no rate limits, no key needed).
"""
import sys, py_compile
from pathlib import Path

BACKEND = Path("/root/memecoin_engine/dashboard/backend/main.py")
text = BACKEND.read_text()

ENDPOINT = r'''

# ── Price Strip (Patch 106) ───────────────────────────────────────────────────

@app.get("/api/prices")
async def prices_ep(_: str = Depends(get_current_user)):
    """Live prices + 24h change for BTC, ETH, SOL, SUI, HYPE via Kraken ticker."""
    import requests as _req

    # Kraken: query_pair -> result_key
    _PAIRS = {
        "BTC":  ("XBTUSD",  "XXBTZUSD"),
        "ETH":  ("ETHUSD",  "XETHZUSD"),
        "SOL":  ("SOLUSD",  "SOLUSD"),
        "SUI":  ("SUIUSD",  "SUIUSD"),
        "HYPE": ("HYPEUSD", "HYPEUSD"),
    }

    query = ",".join(p[0] for p in _PAIRS.values())

    def _fetch():
        r = _req.get(
            f"https://api.kraken.com/0/public/Ticker?pair={query}",
            timeout=8,
        )
        return r.json().get("result", {})

    try:
        result = await asyncio.to_thread(_fetch)
        prices = []
        for coin, (_, rkey) in _PAIRS.items():
            d = result.get(rkey, {})
            last  = float(d["c"][0]) if d.get("c") else 0.0
            open_ = float(d["o"])    if d.get("o") else last
            chg24 = round((last - open_) / open_ * 100, 2) if open_ else 0.0
            prices.append({"coin": coin, "price": last, "chg24": chg24})
        return {"prices": prices}
    except Exception as exc:
        log.warning("prices_ep error: %s", exc)
        return {"prices": []}

'''

MARKER = '@app.get("/api/journal/learnings")'
if MARKER not in text:
    print("❌ Marker not found")
    sys.exit(1)

text = text.replace(MARKER, ENDPOINT + "\n" + MARKER, 1)
BACKEND.write_text(text)

try:
    py_compile.compile(str(BACKEND), doraise=True)
    print("✅ Syntax OK — /api/prices endpoint injected")
except py_compile.PyCompileError as e:
    print(f"❌ {e}")
    sys.exit(1)
