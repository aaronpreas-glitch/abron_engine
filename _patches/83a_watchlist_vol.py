"""
Patch 83a — main.py: Solana Utility Watchlist + Volume Pattern Analytics
1. GET /api/watchlist/utility — top Solana utility/infra coins via DexScreener
2. GET /api/analytics/volume-patterns — day-of-week volume/change analysis per symbol
"""
import subprocess, sys, os
from pathlib import Path

ROOT = Path("/root/memecoin_engine")
MAIN = ROOT / "dashboard" / "backend" / "main.py"

text = MAIN.read_text()

# ─────────────────────────────────────────────────────────────────────────────
# R1 — Solana Utility Watchlist + Volume Patterns endpoints
#      Insert before journal/learnings anchor
# ─────────────────────────────────────────────────────────────────────────────
ANCHOR = '@app.get("/api/brain/edge-stats")'
assert ANCHOR in text, f"R1 anchor '{ANCHOR}' not found"

NEW_ENDPOINTS = '''# ─── Solana Utility Watchlist (Patch 83) ────────────────────────────────────────

_SOLANA_UTILITY_TOKENS = {
    "JUP":    "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN",
    "RAY":    "4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R",
    "JTO":    "jtojtomepa8beP8AuQc6eXt5FriJwfFMwQx2v2f9mCL",
    "PYTH":   "HZ1JovNiVvGrGNiiYvEozEVgZ58xaU3RKwX8eACQBCt3",
    "W":      "85VBFQZC9TZkfaptBWjvUw7YbZjy52A6mjtPGjstQAmQ",
    "ORCA":   "orcaEKTdK7LKz57vaAYr9QeNsVEPfiu6QeMU1kektZE",
    "DRIFT":  "DriFtupJYLTosbwoN8koMbEYSx54aFAVLddWsbksjwg7",
    "MNDE":   "MNDEFzGvMt87ueuHvVU9VcTqsAP5b3fTGPsHuuPA5ey",
    "RNDR":   "rndrizKT3MK1iimdxRdWabcF7Zg7AR5T4nud4EkHBof",
    "HNT":    "hntyVP6YFm1Hg25TN9WGLqM12b8TQmcknKrdu1oxWux",
    "SHDW":   "SHDWyBxihqiCj6YekG2GUr7wqKLeLAMK1gHZck9pL6y",
    "TENSOR": "TNSRxcUxoT9xBG3de7PiJyTDYu7kskLqcpddxnEJAS6",
}

_utility_wl_cache = {"data": None, "ts": 0}


def _fetch_utility_watchlist():
    """Fetch Solana utility coins from DexScreener, cached 120s."""
    import requests as _req
    import time as _time

    now = _time.time()
    if _utility_wl_cache["data"] and now - _utility_wl_cache["ts"] < 120:
        return _utility_wl_cache["data"]

    addresses = list(_SOLANA_UTILITY_TOKENS.values())
    try:
        addr_str = ",".join(addresses)
        r = _req.get(
            f"https://api.dexscreener.com/latest/dex/tokens/{addr_str}",
            timeout=12,
        )
        pairs = r.json().get("pairs", [])
    except Exception as _e:
        log.warning("DexScreener utility fetch error: %s", _e)
        pairs = []

    # Group by base token address, keep highest-liquidity pair
    best = {}
    for p in pairs:
        if p.get("chainId") != "solana":
            continue
        addr = p.get("baseToken", {}).get("address", "")
        liq = float(p.get("liquidity", {}).get("usd", 0) or 0)
        prev_liq = float(best.get(addr, {}).get("liquidity", {}).get("usd", 0) or 0)
        if addr not in best or liq > prev_liq:
            best[addr] = p

    cards = []
    for symbol, addr in _SOLANA_UTILITY_TOKENS.items():
        p = best.get(addr)
        if not p:
            cards.append({
                "symbol": symbol, "address": addr, "status": "NoData",
                "reason": "No DEX pair found", "has_live_data": False,
                "price": None, "market_cap": None, "liquidity": None,
                "volume_24h": None, "change_1h": None, "change_24h": None,
                "txns_h1": None,
            })
            continue

        price = float(p.get("priceUsd", 0) or 0)
        mc = float(p.get("fdv", 0) or 0)
        liq = float(p.get("liquidity", {}).get("usd", 0) or 0)
        vol = float(p.get("volume", {}).get("h24", 0) or 0)
        chg1h = float(p.get("priceChange", {}).get("h1", 0) or 0)
        chg24h = float(p.get("priceChange", {}).get("h24", 0) or 0)
        buys = int(p.get("txns", {}).get("h1", {}).get("buys", 0) or 0)
        sells = int(p.get("txns", {}).get("h1", {}).get("sells", 0) or 0)
        txns = buys + sells
        vol_liq = round(vol / liq, 2) if liq > 0 else 0

        # Status classification
        if abs(chg24h) > 10:
            status = "Volatile"
        elif chg24h > 5:
            status = "Momentum"
        elif chg24h < -10:
            status = "Breakdown"
        elif vol_liq > 0.5:
            status = "Reclaim"
        else:
            status = "Range"

        # Heat
        if vol_liq > 2:
            heat = "HOT"
        elif vol_liq > 1:
            heat = "ACTIVE"
        elif vol_liq > 0.3:
            heat = "MOVING"
        else:
            heat = "COLD"

        reason = f"24h: {chg24h:+.1f}% | Vol/Liq: {vol_liq:.2f}"

        cards.append({
            "symbol": symbol, "address": addr, "status": status,
            "reason": reason, "has_live_data": True, "heat": heat,
            "vol_to_liq": vol_liq, "price": price, "market_cap": mc,
            "liquidity": liq, "volume_24h": vol, "change_1h": chg1h,
            "change_24h": chg24h, "txns_h1": txns,
        })

    # Sort: Momentum first, then by volume desc
    status_order = {"Momentum": 0, "Reclaim": 1, "Volatile": 2, "Range": 3, "Breakdown": 4, "Illiquid": 5, "NoData": 6}
    cards.sort(key=lambda c: (status_order.get(c["status"], 9), -(c["volume_24h"] or 0)))

    _utility_wl_cache["data"] = cards
    _utility_wl_cache["ts"] = now
    return cards


@app.get("/api/watchlist/utility")
async def watchlist_utility(_: str = Depends(get_current_user)):
    """Top Solana utility/infrastructure coins — DexScreener, cached 120s."""
    try:
        cards = _fetch_utility_watchlist()
        return JSONResponse(cards)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.get("/api/analytics/volume-patterns")
async def analytics_volume_patterns(_: str = Depends(get_current_user)):
    """Day-of-week volume + price change analysis per symbol (90-day lookback)."""
    import requests as _req
    from collections import defaultdict
    from datetime import datetime

    _KRAKEN_PAIRS = {
        "SOL": ("SOLUSD",  "SOLUSD"),
        "BTC": ("XBTUSD",  "XXBTZUSD"),
        "ETH": ("ETHUSD",  "XETHZUSD"),
        "SUI": ("SUIUSD",  "SUIUSD"),
        "AVAX": ("AVAXUSD", "AVAXUSD"),
        "ARB": ("ARBUSD",  "ARBUSD"),
        "OP":  ("OPUSD",   "OPUSD"),
        "TON": ("TONUSD",  "TONUSD"),
    }

    result = {}
    day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

    for symbol, (pair, rkey) in _KRAKEN_PAIRS.items():
        try:
            r = _req.get(
                f"https://api.kraken.com/0/public/OHLC?pair={pair}&interval=1440",
                timeout=10,
            )
            candles = r.json().get("result", {}).get(rkey, [])

            day_vols = defaultdict(list)
            day_changes = defaultdict(list)
            for c in candles[-90:]:
                ts = datetime.utcfromtimestamp(int(c[0]))
                dow = ts.weekday()
                vol = float(c[6])
                o_price, c_price = float(c[1]), float(c[4])
                chg = (c_price - o_price) / o_price * 100 if o_price > 0 else 0
                day_vols[dow].append(vol)
                day_changes[dow].append(chg)

            result[symbol] = [
                {
                    "day": day_names[d],
                    "avg_volume": round(sum(day_vols[d]) / len(day_vols[d]), 2),
                    "max_volume": round(max(day_vols[d]), 2),
                    "avg_change": round(sum(day_changes[d]) / len(day_changes[d]), 3),
                    "positive_pct": round(
                        sum(1 for c in day_changes[d] if c > 0) / len(day_changes[d]) * 100, 1
                    ),
                    "sample_days": len(day_vols[d]),
                }
                for d in range(7)
                if d in day_vols
            ]
        except Exception as _e:
            log.warning("volume_patterns %s error: %s", symbol, _e)
            result[symbol] = []

    return JSONResponse(result)


'''

text = text.replace(ANCHOR, NEW_ENDPOINTS + ANCHOR)
print("✓ R1: Utility watchlist + volume patterns endpoints added")

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
print("✓ Patch 83a complete — Watchlist + Volume endpoints deployed")
