#!/usr/bin/env python3
"""Patch 86a — Basic S/R Zones from Kraken OHLC (Analytics Only)

Adds:
  - _compute_sr_zones_sync(symbol)  — 90 daily + 24 4h candles → swing H/L → cluster → score
  - GET /api/brain/sr-zones/{symbol} — returns top 3 support + 3 resistance, 15-min cache
"""
from pathlib import Path
import subprocess

MAIN = Path("/root/memecoin_engine/dashboard/backend/main.py")
text = MAIN.read_text()

# ─────────────────────────────────────────────────────────────────────────────
# R1: Insert S/R zones cache dict + helper + endpoint
#     Anchor: @app.get("/api/journal/learnings")  (unique, count=1)
# ─────────────────────────────────────────────────────────────────────────────
ANCHOR = '@app.get("/api/journal/learnings")'
assert text.count(ANCHOR) == 1, f"R1: expected 1 anchor, found {text.count(ANCHOR)}"

SR_CODE = r'''# ---------------------------------------------------------------------------
# S/R Zones from Kraken OHLC (Patch 86a)
# ---------------------------------------------------------------------------
_sr_zones_cache: dict = {}   # keyed by symbol uppercase
_SR_ZONES_TTL = 900          # 15-minute cache

_SR_KRAKEN_PAIRS: dict = {
    "SOL": ("SOLUSD",  "SOLUSD"),
    "BTC": ("XBTUSD",  "XXBTZUSD"),
    "ETH": ("ETHUSD",  "XETHZUSD"),
}


def _compute_sr_zones_sync(symbol: str) -> dict:
    """Detect S/R zones from 90 daily + 24 4h Kraken candles (blocking)."""
    import requests as _rq

    sym = symbol.upper()
    pair_info = _SR_KRAKEN_PAIRS.get(sym)
    if not pair_info:
        raise ValueError(f"Unsupported symbol for S/R zones: {symbol}")
    pair, result_key = pair_info

    def _fetch(interval: int, limit: int) -> list:
        r = _rq.get(
            f"https://api.kraken.com/0/public/OHLC?pair={pair}&interval={interval}",
            timeout=10,
        )
        candles = r.json().get("result", {}).get(result_key, [])
        return candles[-limit:]

    daily = _fetch(1440, 90)   # 90 daily candles
    h4    = _fetch(240,  24)   # 24 × 4h candles

    current_price = float(daily[-1][4]) if daily else 0.0

    # Detect swing highs (local max) and swing lows (local min)
    # Candle format: [time, open, high, low, close, vwap, volume, count]
    all_c = daily + h4
    highs: list = []
    lows:  list = []
    for i in range(1, len(all_c) - 1):
        h  = float(all_c[i][2])
        lo = float(all_c[i][3])
        if h  > float(all_c[i - 1][2]) and h  > float(all_c[i + 1][2]):
            highs.append(h)
        if lo < float(all_c[i - 1][3]) and lo < float(all_c[i + 1][3]):
            lows.append(lo)

    def _cluster(levels: list, tol: float = 0.012) -> list:
        """Group price levels within tol% of each other."""
        if not levels:
            return []
        srt = sorted(levels)
        clusters: list = [[srt[0]]]
        for lvl in srt[1:]:
            if (lvl - clusters[-1][0]) / clusters[-1][0] < tol:
                clusters[-1].append(lvl)
            else:
                clusters.append([lvl])
        return clusters

    def _score(cluster: list) -> dict:
        touches  = len(cluster)
        strength = min(100, touches * 20)  # 5+ touches → 100
        price    = round(sum(cluster) / len(cluster), 4)
        dist     = round((price - current_price) / current_price * 100, 2) if current_price else 0.0
        return {"price": price, "strength": strength, "touches": touches, "distance_pct": dist}

    resistance = sorted(
        [_score(c) for c in _cluster([h for h in highs if h > current_price])],
        key=lambda z: z["price"],
    )[:3]

    support = sorted(
        [_score(c) for c in _cluster([lo for lo in lows if lo < current_price])],
        key=lambda z: z["price"],
        reverse=True,
    )[:3]

    return {
        "symbol":        sym,
        "current_price": round(current_price, 4),
        "resistance":    resistance,
        "support":       support,
        "daily_candles": len(daily),
        "h4_candles":    len(h4),
        "computed_at":   datetime.utcnow().isoformat() + "Z",
    }


@app.get("/api/brain/sr-zones/{symbol}")
async def brain_sr_zones(symbol: str, _: str = Depends(get_current_user)):
    """Return S/R zones for a symbol from Kraken OHLC. Cached 15 min."""
    import time as _t
    sym = symbol.upper()
    now = _t.time()
    cached = _sr_zones_cache.get(sym)
    if cached and (now - cached["ts"]) < _SR_ZONES_TTL:
        return cached["data"]
    try:
        result = await asyncio.to_thread(_compute_sr_zones_sync, sym)
        _sr_zones_cache[sym] = {"data": result, "ts": now}
        return result
    except Exception as exc:
        log.warning("sr_zones %s error: %s", sym, exc)
        if cached:
            return cached["data"]
        raise HTTPException(status_code=500, detail=str(exc))


'''

new_text = text.replace(ANCHOR, SR_CODE + ANCHOR)
assert new_text != text, "R1 had no effect — anchor matched but replacement was identical"
MAIN.write_text(new_text)
print("86a R1: S/R zones endpoint inserted ✓")

# ─────────────────────────────────────────────────────────────────────────────
# Compile check
# ─────────────────────────────────────────────────────────────────────────────
result = subprocess.run(
    ["python3", "-m", "py_compile", str(MAIN)],
    capture_output=True, text=True,
)
if result.returncode != 0:
    print("COMPILE ERROR:", result.stderr)
    raise SystemExit(1)
print("86a compile OK ✓")
