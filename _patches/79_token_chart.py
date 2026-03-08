"""
Patch 79 — Token Chart + Live Trades (GeckoTerminal, free, no key)
Adds two read-only endpoints:
  GET /api/token/chart-data?address={mint}&resolution={1m|5m|15m|1h|4h}
    → OHLCV candle data via GeckoTerminal (cache 1–5 min)
  GET /api/token/recent-trades?address={mint}
    → Last 40 on-chain trades via GeckoTerminal (cache 15s)
Flow: mint address → GeckoTerminal pools → pool address → OHLCV / trades
"""

import subprocess, sys
from pathlib import Path

ROOT = Path("/root/memecoin_engine")
MAIN = ROOT / "dashboard/backend/main.py"

main_text = MAIN.read_text()

ANCHOR = '@app.get("/api/journal/learnings")'
assert ANCHOR in main_text, f"anchor not found: {ANCHOR}"
assert "token/chart-data" not in main_text, "patch already applied"

NEW_ENDPOINTS = '''
# ── Token Chart + Trades (GeckoTerminal) ──────────────────────────────────────
_token_pool_cache:   dict = {}   # {mint: {"pool": addr, "exp": ts}}
_token_ohlcv_cache:  dict = {}   # {f"{mint}_{res}": {"data": ..., "exp": ts}}
_token_trades_cache: dict = {}   # {mint: {"data": ..., "exp": ts}}

def _gt_get_pool(token_address: str):
    """Return highest-liquidity Solana pool address for a token (GeckoTerminal)."""
    cached = _token_pool_cache.get(token_address)
    if cached and cached["exp"] > time.time():
        return cached["pool"]
    try:
        r = requests.get(
            f"https://api.geckoterminal.com/api/v2/networks/solana/tokens/{token_address}/pools",
            params={"page": 1},
            headers={"Accept": "application/json"},
            timeout=10,
        )
        pools = r.json().get("data", [])
        if not pools:
            return None
        pool_addr = pools[0]["attributes"]["address"]
        _token_pool_cache[token_address] = {"pool": pool_addr, "exp": time.time() + 3600}
        return pool_addr
    except Exception as exc:
        log.warning("_gt_get_pool error: %s", exc)
        return None


@app.get("/api/token/chart-data")
async def token_chart_data(address: str, resolution: str = "5m", _: str = Depends(get_current_user)):
    """OHLCV candles for a Solana token via GeckoTerminal. resolution: 1m/5m/15m/1h/4h"""
    cache_key = f"{address}_{resolution}"
    cached = _token_ohlcv_cache.get(cache_key)
    if cached and cached["exp"] > time.time():
        return JSONResponse(cached["data"])

    pool = _gt_get_pool(address)
    if not pool:
        return JSONResponse({"candles": [], "error": "No pool found for this token"})

    RESOLUTION_MAP = {
        "1m":  ("minute", 1),
        "5m":  ("minute", 5),
        "15m": ("minute", 15),
        "1h":  ("hour",   1),
        "4h":  ("hour",   4),
    }
    timeframe, agg = RESOLUTION_MAP.get(resolution, ("minute", 5))

    try:
        r = requests.get(
            f"https://api.geckoterminal.com/api/v2/networks/solana/pools/{pool}/ohlcv/{timeframe}",
            params={"aggregate": agg, "limit": 300, "currency": "usd"},
            headers={"Accept": "application/json"},
            timeout=12,
        )
        raw = r.json().get("data", {}).get("attributes", {}).get("ohlcv_list", [])
        # raw entries: [unix_seconds, open, high, low, close, volume]
        candles = []
        for item in raw:
            if len(item) == 6 and item[1] and item[4]:
                candles.append({
                    "time":   int(item[0]),
                    "open":   float(item[1]),
                    "high":   float(item[2]),
                    "low":    float(item[3]),
                    "close":  float(item[4]),
                    "volume": float(item[5] or 0),
                })
        candles.sort(key=lambda x: x["time"])

        result = {"candles": candles, "pool": pool, "resolution": resolution}
        ttl = 60 if resolution in ("1m", "5m") else 300
        _token_ohlcv_cache[cache_key] = {"data": result, "exp": time.time() + ttl}
        return JSONResponse(result)
    except Exception as exc:
        log.warning("token_chart_data error: %s", exc)
        return JSONResponse({"candles": [], "error": str(exc)})


@app.get("/api/token/recent-trades")
async def token_recent_trades(address: str, _: str = Depends(get_current_user)):
    """Last 40 on-chain trades for a Solana token via GeckoTerminal (cache 15s)."""
    cached = _token_trades_cache.get(address)
    if cached and cached["exp"] > time.time():
        return JSONResponse(cached["data"])

    pool = _gt_get_pool(address)
    if not pool:
        return JSONResponse({"trades": [], "error": "No pool found for this token"})

    try:
        r = requests.get(
            f"https://api.geckoterminal.com/api/v2/networks/solana/pools/{pool}/trades",
            params={"trade_volume_in_usd_greater_than": 0},
            headers={"Accept": "application/json"},
            timeout=12,
        )
        items = r.json().get("data", [])
        trades = []
        for item in items[:40]:
            attr = item.get("attributes", {})
            # price: use from_usd first, fallback to to_usd
            price = float(attr.get("price_from_in_usd") or attr.get("price_to_in_usd") or 0)
            trades.append({
                "time":       attr.get("block_timestamp"),
                "kind":       attr.get("kind", "unknown"),
                "price_usd":  price,
                "volume_usd": float(attr.get("volume_in_usd") or 0),
                "tx_hash":    attr.get("tx_hash", ""),
            })

        result = {"trades": trades, "pool": pool}
        _token_trades_cache[address] = {"data": result, "exp": time.time() + 15}
        return JSONResponse(result)
    except Exception as exc:
        log.warning("token_recent_trades error: %s", exc)
        return JSONResponse({"trades": [], "error": str(exc)})


'''

main_text = main_text.replace(ANCHOR, NEW_ENDPOINTS + ANCHOR)
assert "token/chart-data" in main_text, "endpoints not inserted"
MAIN.write_text(main_text)
print("✓ main.py — /api/token/chart-data + /api/token/recent-trades inserted")

r = subprocess.run([sys.executable, "-m", "py_compile", str(MAIN)], capture_output=True, text=True)
if r.returncode != 0:
    print("✗ compile error:", r.stderr)
    sys.exit(1)
print("✓ main.py compiles OK")
