"""
Patch 62 — CryptoPanic News Integration
=========================================
1. Cache globals: _cp_news_cache keyed by (currencies, filter) — 15 min TTL
2. New GET /api/news/cryptopanic endpoint
   - ?currencies=BTC,ETH,PEPE  (comma-separated, default top memecoins)
   - ?filter=all|bullish|bearish|important|hot  (default=all)
   - Auth token from CRYPTOPANIC_AUTH_TOKEN env var
   - Returns top 20 posts: title, url, published_at, votes (pos/neg/important),
     kind, instruments (coin codes), source domain
   - Cache 15 min per (currencies, filter) combo
   - Graceful: returns [] if token missing or API error
"""

from pathlib import Path
import py_compile, tempfile, shutil

MAIN = Path(__file__).resolve().parent.parent / "dashboard" / "backend" / "main.py"
main = MAIN.read_text()

# ── 1. Cache globals — insert after _COIN_DETAIL_TTL line ────────────────────

CACHE_ANCHOR = "_COIN_DETAIL_TTL   = 900   # 15 min"
assert CACHE_ANCHOR in main, "coin detail TTL anchor not found"

CACHE_NEW = (
    "_COIN_DETAIL_TTL   = 900   # 15 min\n"
    "_cp_news_cache: dict = {}  # keyed by (currencies_str, filter_str)\n"
    "_CP_NEWS_TTL       = 900   # 15 min\n"
)
main = main.replace(CACHE_ANCHOR, CACHE_NEW)
print("✅ CryptoPanic cache globals inserted")

# ── 2. Endpoint — insert before @app.get("/api/journal/learnings") ────────────

INSERT_ANCHOR = '@app.get("/api/journal/learnings")'
assert INSERT_ANCHOR in main, "learnings anchor not found"

CP_ENDPOINT = r'''@app.get("/api/news/cryptopanic")
async def news_cryptopanic(
    request: Request,
    currencies: str = "BTC,ETH,SOL,PEPE,DOGE,SHIB,TRUMP,WIF,BONK,FLOKI,MEW,BRETT,POPCAT,PNUT,FARTCOIN",
    filter: str = "all",
    _: str = Depends(get_current_user),
):
    """CryptoPanic news feed — sentiment-tagged crypto news. Cache 15 min per query."""
    import time as _time
    import os as _os
    token = _os.environ.get("CRYPTOPANIC_AUTH_TOKEN", "").strip()
    if not token:
        return JSONResponse({"items": [], "error": "CRYPTOPANIC_AUTH_TOKEN not set", "source": "cryptopanic"})

    cache_key = (currencies.upper(), filter.lower())
    now = _time.time()
    cached = _cp_news_cache.get(cache_key)
    if cached and now - cached.get("ts", 0) < _CP_NEWS_TTL:
        return JSONResponse(cached["data"])

    try:
        import requests as _req
        _filter = filter.lower() if filter.lower() in ("rising", "hot", "bullish", "bearish", "important", "saved", "lol") else None
        params = {
            "auth_token": token,
            "currencies": currencies.upper(),
            "kind": "news",
            "public": "true",
        }
        if _filter and _filter != "all":
            params["filter"] = _filter

        resp = await asyncio.to_thread(
            lambda: _req.get(
                "https://cryptopanic.com/api/v1/posts/",
                params=params,
                timeout=10,
            ).json()
        )

        raw = resp.get("results", [])
        items = []
        for post in raw[:25]:
            votes = post.get("votes", {})
            pos = votes.get("positive", 0) or 0
            neg = votes.get("negative", 0) or 0
            imp = votes.get("important", 0) or 0
            coins = [c.get("code", "") for c in (post.get("currencies") or []) if c.get("code")]
            source = post.get("source", {}) or {}
            items.append({
                "title":        post.get("title", ""),
                "url":          post.get("url", ""),
                "published_at": post.get("published_at", ""),
                "kind":         post.get("kind", "news"),
                "votes_pos":    pos,
                "votes_neg":    neg,
                "votes_imp":    imp,
                "sentiment":    "bullish" if pos > neg + 1 else "bearish" if neg > pos + 1 else "neutral",
                "instruments":  coins[:6],
                "source_title": source.get("title", ""),
                "source_domain": source.get("domain", ""),
            })

        result = {"items": items, "total": len(items), "filter": filter, "source": "cryptopanic",
                  "ts": datetime.utcnow().isoformat() + "Z"}
        _cp_news_cache[cache_key] = {"data": result, "ts": now}
        return JSONResponse(result)

    except Exception as exc:
        log.warning("news_cryptopanic error: %s", exc)
        if cache_key in _cp_news_cache:
            return JSONResponse(_cp_news_cache[cache_key]["data"])
        return JSONResponse({"items": [], "error": str(exc), "source": "cryptopanic"})


'''

main = main.replace(INSERT_ANCHOR, CP_ENDPOINT + INSERT_ANCHOR)
print("✅ /api/news/cryptopanic endpoint inserted")

MAIN.write_text(main)

tmp = Path(tempfile.mktemp(suffix=".py"))
shutil.copy(MAIN, tmp)
py_compile.compile(str(tmp), doraise=True)
tmp.unlink()
print("✅ main.py compiles OK")
print("\nPatch 62 complete — deploy with scp + python3 + systemctl restart")
