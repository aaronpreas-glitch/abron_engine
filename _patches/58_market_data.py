"""
Patch 58 — Market Data integrations
=====================================
Adds 3 read-only market data endpoints to main.py:
  GET /api/market/global   — CoinGecko /global (15 min cache)
  GET /api/market/movers   — OKX tickers top-20 gainers/losers (10 min cache)
  GET /api/market/trending — CoinGecko /search/trending (30 min cache)

All endpoints:
  - require auth (Bearer token)
  - have graceful fallback to last cached data
  - check MARKET_DATA_ENABLED env var (default true)
  - are pure read-only — zero effect on trading logic
"""

from pathlib import Path
import py_compile, tempfile, shutil

MAIN = Path(__file__).resolve().parent.parent / "dashboard" / "backend" / "main.py"
main = MAIN.read_text()

# ── Cache declarations — insert after _prices_cache block ──────────────────
CACHE_ANCHOR = '_prices_cache: dict = {"data": {}, "ts": 0.0}\n_PRICES_TTL = 55'
assert CACHE_ANCHOR in main, "cache anchor not found"

CACHE_BLOCK = (
    '_market_global_cache:   dict = {"data": None, "ts": 0.0}\n'
    '_market_movers_cache:   dict = {"data": None, "ts": 0.0}\n'
    '_market_trending_cache: dict = {"data": None, "ts": 0.0}\n'
    '_MARKET_GLOBAL_TTL   = 900    # 15 min\n'
    '_MARKET_MOVERS_TTL   = 600    # 10 min\n'
    '_MARKET_TRENDING_TTL = 1800   # 30 min\n'
)
main = main.replace(CACHE_ANCHOR, CACHE_BLOCK + CACHE_ANCHOR)

# ── 3 new endpoints — insert before /api/journal/learnings ─────────────────
INSERT_ANCHOR = '@app.get("/api/journal/learnings")'
assert INSERT_ANCHOR in main, "journal/learnings anchor not found"

ENDPOINTS = r'''# ---------------------------------------------------------------------------
# Market Data — /api/market/*  (read-only, no effect on trading logic)
# ---------------------------------------------------------------------------

@app.get("/api/market/global")
async def market_global(request: Request, _: str = Depends(get_current_user)):
    """CoinGecko /global — total market cap, BTC dominance, 24h change. Cache 15 min."""
    if os.environ.get("MARKET_DATA_ENABLED", "true").lower() == "false":
        return JSONResponse({"enabled": False})
    import time as _time
    now = _time.time()
    if now - _market_global_cache["ts"] < _MARKET_GLOBAL_TTL and _market_global_cache["data"]:
        return JSONResponse(_market_global_cache["data"])
    try:
        import requests as _req
        r = await asyncio.to_thread(
            lambda: _req.get("https://api.coingecko.com/api/v3/global", timeout=8).json()
        )
        d = r.get("data", {})
        result = {
            "total_market_cap_usd": d.get("total_market_cap", {}).get("usd"),
            "btc_dominance":        round(d.get("market_cap_percentage", {}).get("btc", 0), 2),
            "eth_dominance":        round(d.get("market_cap_percentage", {}).get("eth", 0), 2),
            "market_cap_change_24h": round(d.get("market_cap_change_percentage_24h_usd", 0), 2),
            "total_volume_usd":     d.get("total_volume", {}).get("usd"),
            "active_cryptocurrencies": d.get("active_cryptocurrencies"),
        }
        if result["total_market_cap_usd"]:
            _market_global_cache["data"] = result
            _market_global_cache["ts"] = now
        return JSONResponse(result)
    except Exception as exc:
        log.warning("market_global error: %s", exc)
        return JSONResponse(_market_global_cache.get("data") or {})


@app.get("/api/market/movers")
async def market_movers(request: Request, _: str = Depends(get_current_user)):
    """OKX spot tickers — top 20 gainers + top 20 losers by 24h %. Cache 10 min."""
    if os.environ.get("MARKET_DATA_ENABLED", "true").lower() == "false":
        return JSONResponse({"gainers": [], "losers": []})
    import time as _time
    now = _time.time()
    if now - _market_movers_cache["ts"] < _MARKET_MOVERS_TTL and _market_movers_cache["data"]:
        return JSONResponse(_market_movers_cache["data"])
    try:
        import requests as _req
        r = await asyncio.to_thread(
            lambda: _req.get("https://www.okx.com/api/v5/market/tickers?instType=SPOT", timeout=10).json()
        )
        tickers = r.get("data", [])
        STABLES = {"USDC", "USDT", "BUSD", "DAI", "TUSD", "USDP", "FDUSD", "PYUSD", "USDE", "FRAX"}
        parsed = []
        for t in tickers:
            if not t.get("instId", "").endswith("-USDT"):
                continue
            try:
                sym   = t["instId"].replace("-USDT", "")
                if sym in STABLES:
                    continue
                last  = float(t["last"])
                open_ = float(t["open24h"])
                vol   = float(t.get("volCcy24h", 0))
                if open_ <= 0 or vol < 50_000:
                    continue
                chg = (last - open_) / open_ * 100
                parsed.append({
                    "symbol":     sym,
                    "price":      last,
                    "change_24h": round(chg, 2),
                    "volume_usd": round(vol * last, 0),
                })
            except Exception:
                continue
        gainers = sorted(parsed, key=lambda x: x["change_24h"], reverse=True)[:20]
        losers  = sorted(parsed, key=lambda x: x["change_24h"])[:20]
        result = {
            "gainers": gainers,
            "losers":  losers,
            "source":  "OKX",
            "ts":      datetime.utcnow().isoformat() + "Z",
        }
        _market_movers_cache["data"] = result
        _market_movers_cache["ts"]   = now
        return JSONResponse(result)
    except Exception as exc:
        log.warning("market_movers error: %s", exc)
        return JSONResponse(_market_movers_cache.get("data") or {"gainers": [], "losers": []})


@app.get("/api/market/trending")
async def market_trending(request: Request, _: str = Depends(get_current_user)):
    """CoinGecko /search/trending — top 7 coins by 24h search volume. Cache 30 min."""
    if os.environ.get("MARKET_DATA_ENABLED", "true").lower() == "false":
        return JSONResponse({"coins": []})
    import time as _time
    now = _time.time()
    if now - _market_trending_cache["ts"] < _MARKET_TRENDING_TTL and _market_trending_cache["data"]:
        return JSONResponse(_market_trending_cache["data"])
    try:
        import requests as _req
        r = await asyncio.to_thread(
            lambda: _req.get("https://api.coingecko.com/api/v3/search/trending", timeout=8).json()
        )
        coins = r.get("coins", [])
        result = []
        for c in coins[:7]:
            item = c.get("item", {})
            data = item.get("data", {})
            chg = None
            try:
                chg_map = data.get("price_change_percentage_24h", {})
                if isinstance(chg_map, dict):
                    chg = chg_map.get("usd")
            except Exception:
                pass
            result.append({
                "name":            item.get("name"),
                "symbol":          item.get("symbol"),
                "market_cap_rank": item.get("market_cap_rank"),
                "change_24h":      round(chg, 2) if chg is not None else None,
                "price_usd":       data.get("price"),
                "score":           item.get("score", 0),
            })
        out = {"coins": result, "ts": datetime.utcnow().isoformat() + "Z"}
        _market_trending_cache["data"] = out
        _market_trending_cache["ts"]   = now
        return JSONResponse(out)
    except Exception as exc:
        log.warning("market_trending error: %s", exc)
        return JSONResponse(_market_trending_cache.get("data") or {"coins": []})


'''
main = main.replace(INSERT_ANCHOR, ENDPOINTS + INSERT_ANCHOR)

MAIN.write_text(main)
print("✅ main.py patched (3 market endpoints + cache blocks)")

tmp = Path(tempfile.mktemp(suffix=".py"))
shutil.copy(MAIN, tmp)
py_compile.compile(str(tmp), doraise=True)
tmp.unlink()
print("✅ main.py compiles OK")
print("\nDeploy: scp + python3 + systemctl restart")
