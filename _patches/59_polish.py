"""
Patch 59 — Dashboard Polish
============================
1. /api/market/trending — add `id` field to each coin (needed for detail modal)
2. /api/market/movers   — add ?filter=memecoins query param
3. /api/market/coin-detail/{coin_id} — new endpoint (CoinGecko, 15 min cache)
"""

from pathlib import Path
import py_compile, tempfile, shutil

MAIN = Path(__file__).resolve().parent.parent / "dashboard" / "backend" / "main.py"
main = MAIN.read_text()

# ── 1. Trending: add "id" field ─────────────────────────────────────────────

OLD_TRENDING_APPEND = '''\
            result.append({
                "name":            item.get("name"),
                "symbol":          item.get("symbol"),
                "market_cap_rank": item.get("market_cap_rank"),
                "change_24h":      round(chg, 2) if chg is not None else None,
                "price_usd":       data.get("price"),
                "score":           item.get("score", 0),
            })'''

NEW_TRENDING_APPEND = '''\
            result.append({
                "id":              item.get("id"),
                "name":            item.get("name"),
                "symbol":          item.get("symbol"),
                "market_cap_rank": item.get("market_cap_rank"),
                "change_24h":      round(chg, 2) if chg is not None else None,
                "price_usd":       data.get("price"),
                "score":           item.get("score", 0),
            })'''

assert OLD_TRENDING_APPEND in main, "trending append anchor not found"
main = main.replace(OLD_TRENDING_APPEND, NEW_TRENDING_APPEND)
print("✅ trending id field added")

# ── 2. Movers: add ?filter=memecoins param ──────────────────────────────────

OLD_MOVERS_SIG = '@app.get("/api/market/movers")\nasync def market_movers(request: Request, _: str = Depends(get_current_user)):'
NEW_MOVERS_SIG = (
    '@app.get("/api/market/movers")\n'
    'async def market_movers(request: Request, filter: str = "all", _: str = Depends(get_current_user)):'
)
assert OLD_MOVERS_SIG in main, "movers sig anchor not found"
main = main.replace(OLD_MOVERS_SIG, NEW_MOVERS_SIG)

# Insert memecoin filter logic after the existing gainers/losers sort lines
OLD_MOVERS_RESULT = '''\
        gainers = sorted(parsed, key=lambda x: x["change_24h"], reverse=True)[:20]
        losers  = sorted(parsed, key=lambda x: x["change_24h"])[:20]
        result = {
            "gainers": gainers,
            "losers":  losers,
            "source":  "OKX",
            "ts":      datetime.utcnow().isoformat() + "Z",
        }'''

NEW_MOVERS_RESULT = '''\
        gainers = sorted(parsed, key=lambda x: x["change_24h"], reverse=True)[:20]
        losers  = sorted(parsed, key=lambda x: x["change_24h"])[:20]

        # Memecoin filter
        _MEMECOINS = {
            "DOGE","SHIB","PEPE","FLOKI","BONK","WIF","POPCAT","MEW","BRETT","MOG",
            "NEIRO","TURBO","WOJAK","LADYS","MEME","COQ","APU","GIGA","PNUT","SLERF",
            "PONKE","BOME","SILLY","MYRO","SMOL","DEGEN","BABYDOGE","ELON","SAMO",
            "MAGA","REDO","NOOT","CAT","DOG","RATS","SNEK","MANEKI","MICHI",
            "MOODENG","CHILLGUY","ACT","GRASS","ZEREBRO","GRIFFAIN","FARTCOIN",
            "VINE","HARAMBE","GUMMY","SPX","BITCOIN","MOTHER","SIGMA","QUACK",
            "HAMSTER","PORK","ELMO","BODEN","GOAT","FWOG","RETARDIO","GORK",
            "BORK","CHEEMS","BABYSATS","TRUMP","MELANIA","JAILSTOOL","POPO",
            "BIRD","SUNDOG","MICHI","CATE","KEKIUS","VIRTUAL","COQ",
        }
        def _is_memecoin(row):
            if row["symbol"] in _MEMECOINS:
                return True
            # Heuristic: very low price + high volume = likely memecoin
            return row["price"] < 0.10 and row["volume_usd"] > 500_000

        if filter == "memecoins":
            mem_pool = [r for r in parsed if _is_memecoin(r)]
            gainers = sorted(mem_pool, key=lambda x: x["change_24h"], reverse=True)[:15]
            losers  = sorted(mem_pool, key=lambda x: x["change_24h"])[:15]

        result = {
            "gainers": gainers,
            "losers":  losers,
            "source":  "OKX",
            "filter":  filter,
            "ts":      datetime.utcnow().isoformat() + "Z",
        }'''

assert OLD_MOVERS_RESULT in main, "movers result anchor not found"
main = main.replace(OLD_MOVERS_RESULT, NEW_MOVERS_RESULT)
print("✅ movers filter param added")

# ── 3. Coin detail endpoint + cache ─────────────────────────────────────────

# Cache declaration — insert after _market_trending_cache block
CACHE_ANCHOR = '_MARKET_TRENDING_TTL = 1800   # 30 min'
assert CACHE_ANCHOR in main, "trending TTL anchor not found"
main = main.replace(
    CACHE_ANCHOR,
    CACHE_ANCHOR + "\n"
    "_coin_detail_cache: dict = {}         # keyed by coin_id\n"
    "_COIN_DETAIL_TTL   = 900   # 15 min\n"
)

# Endpoint — insert before /api/journal/learnings
INSERT_ANCHOR = '@app.get("/api/journal/learnings")'
assert INSERT_ANCHOR in main, "learnings anchor not found"

COIN_DETAIL_EP = r'''@app.get("/api/market/coin-detail/{coin_id}")
async def market_coin_detail(coin_id: str, request: Request, _: str = Depends(get_current_user)):
    """CoinGecko coin detail — price, market cap, 24h sparkline. Cache 15 min per coin."""
    import time as _time
    now = _time.time()
    cached = _coin_detail_cache.get(coin_id)
    if cached and now - cached.get("ts", 0) < _COIN_DETAIL_TTL:
        return JSONResponse(cached["data"])
    try:
        import requests as _req
        # Coin info + 7d sparkline (last 24 pts = 24h)
        r = await asyncio.to_thread(
            lambda: _req.get(
                f"https://api.coingecko.com/api/v3/coins/{coin_id}"
                "?localization=false&tickers=false&market_data=true"
                "&community_data=false&developer_data=false&sparkline=true",
                timeout=10,
            ).json()
        )
        if "error" in r:
            return JSONResponse({"error": r["error"]}, status_code=404)
        md = r.get("market_data", {})
        sparkline_raw = r.get("sparkline_in_7d", {}).get("price", [])
        # Last 25 points ≈ 24 h (CoinGecko gives ~168 pts for 7d, hourly)
        sparkline_24h = sparkline_raw[-25:] if len(sparkline_raw) >= 25 else sparkline_raw
        result = {
            "id":          coin_id,
            "name":        r.get("name"),
            "symbol":      r.get("symbol", "").upper(),
            "price_usd":   md.get("current_price", {}).get("usd"),
            "change_24h":  md.get("price_change_percentage_24h"),
            "market_cap":  md.get("market_cap", {}).get("usd"),
            "volume_24h":  md.get("total_volume", {}).get("usd"),
            "sparkline":   sparkline_24h,
        }
        _coin_detail_cache[coin_id] = {"data": result, "ts": now}
        return JSONResponse(result)
    except Exception as exc:
        log.warning("market_coin_detail(%s) error: %s", coin_id, exc)
        if coin_id in _coin_detail_cache:
            return JSONResponse(_coin_detail_cache[coin_id]["data"])
        return JSONResponse({"error": str(exc)}, status_code=502)


'''

main = main.replace(INSERT_ANCHOR, COIN_DETAIL_EP + INSERT_ANCHOR)
print("✅ coin-detail endpoint added")

MAIN.write_text(main)

tmp = Path(tempfile.mktemp(suffix=".py"))
shutil.copy(MAIN, tmp)
py_compile.compile(str(tmp), doraise=True)
tmp.unlink()
print("✅ main.py compiles OK")
print("\nPatch 59 complete — deploy with scp + python3 + systemctl restart")
