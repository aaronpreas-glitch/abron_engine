"""
Patch 75 — X (Twitter) Sentiment Feed
Backend: GET /api/social/x-feed
  - Params: ?filter=memecoins (default) | ?symbol=WIF
  - X API v2 bearer token from X_BEARER_TOKEN env var
  - Searches recent tweets for our 23 Solana watchlist coins
  - Returns top 10 posts with text, author, likes, retweets, url, sentiment
  - VADER sentiment scoring
  - 15-min cache per (filter, symbol) key
  - Fallback: {"items":[], "error":"X_BEARER_TOKEN not set"} when token absent
"""

import subprocess, sys
from pathlib import Path

ROOT = Path("/root/memecoin_engine")
MAIN = ROOT / "dashboard/backend/main.py"

main_text = MAIN.read_text()

ANCHOR = '@app.get("/api/journal/learnings")'
assert ANCHOR in main_text, f"anchor not found: {ANCHOR}"

NEW_ENDPOINT = '''
# ── X (Twitter) Sentiment Feed ─────────────────────────────────────────────────
_x_feed_cache: dict = {}   # keyed by (filter_key, symbol_key) → {data, ts}
_X_FEED_TTL = 900          # 15 min

# Solana memecoin set for X queries (matches Patch 74 watchlist)
_X_MEME_COINS = [
    "WIF", "BONK", "POPCAT", "FARTCOIN", "GRIFFAIN", "GOAT", "MOODENG", "PNUT",
    "MOTHER", "ACT", "NEIRO", "MEW", "BILLY", "MOG", "BRETT", "GIGA", "TURBO",
    "PENGU", "AURA", "CHILLGUY", "BOME", "WEN", "SELFIE",
]

def _x_sentiment_score(text: str) -> tuple[str, float]:
    """Return (sentiment_label, compound_score) using VADER."""
    try:
        from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer  # type: ignore
        _va = SentimentIntensityAnalyzer()
        scores = _va.polarity_scores(text)
        compound = scores["compound"]
        if compound >= 0.08:
            return "positive", round(compound, 3)
        elif compound <= -0.08:
            return "negative", round(compound, 3)
        else:
            return "neutral", round(compound, 3)
    except Exception:
        # Keyword fallback when VADER unavailable
        low = text.lower()
        _bull = ("moon", "pump", "bullish", "ath", "buy", "100x", "gem", "breakout",
                 "rocket", "launch", "surge", "run", "up", "gm", "lfg", "ape")
        _bear = ("dump", "crash", "bearish", "sell", "rug", "scam", "down",
                 "rekt", "dead", "ponzi", "falling", "broke", "trap")
        b = sum(1 for w in _bull if w in low)
        d = sum(1 for w in _bear if w in low)
        if b > d:
            return "positive", round((b - d) * 0.1, 3)
        elif d > b:
            return "negative", round((d - b) * -0.1, 3)
        return "neutral", 0.0


@app.get("/api/social/x-feed")
async def social_x_feed(
    filter: str = "memecoins",
    symbol: str | None = None,
    _: str = Depends(get_current_user),
):
    """X (Twitter) sentiment feed for Solana memecoins. 15-min cache."""
    import time as _t, os as _os
    import requests as _req

    token = _os.environ.get("X_BEARER_TOKEN", "").strip()
    if not token:
        return JSONResponse({
            "items": [], "total": 0,
            "error": "X_BEARER_TOKEN not set — add your X API v2 bearer token to .env",
            "source": "x",
        })

    # Build cache key
    sym_key = (symbol or "").upper().strip()
    cache_key = (filter.lower(), sym_key)
    now = _t.time()
    cached = _x_feed_cache.get(cache_key)
    if cached and now - cached.get("ts", 0) < _X_FEED_TTL:
        return JSONResponse(cached["data"])

    try:
        # Build search query
        if sym_key and sym_key in _X_MEME_COINS:
            # Single symbol query — include cashtag and keyword
            query = f"(${sym_key} OR {sym_key} solana) lang:en -is:retweet min_faves:3"
        else:
            # Multi-symbol memecoin query (top 12 by volume for readability)
            top_syms = ["WIF", "BONK", "POPCAT", "FARTCOIN", "MOODENG", "PNUT",
                        "GIGA", "PENGU", "CHILLGUY", "GOAT", "GRIFFAIN", "MEW"]
            sym_terms = " OR ".join(f"${s}" for s in top_syms[:10])
            query = f"({sym_terms}) lang:en -is:retweet min_faves:5"

        resp = await asyncio.to_thread(lambda: _req.get(
            "https://api.twitter.com/2/tweets/search/recent",
            headers={"Authorization": f"Bearer {token}"},
            params={
                "query":        query,
                "max_results":  25,
                "tweet.fields": "created_at,public_metrics,author_id,text",
                "expansions":   "author_id",
                "user.fields":  "username,name,verified",
                "sort_order":   "relevancy",
            },
            timeout=12,
        ).json())

        # Check for API errors
        if "errors" in resp and "data" not in resp:
            err = resp["errors"][0].get("message", "X API error") if resp.get("errors") else "X API error"
            return JSONResponse({"items": [], "total": 0, "error": err, "source": "x"})
        if "title" in resp and resp.get("title") == "Unauthorized":
            return JSONResponse({"items": [], "total": 0,
                "error": "X_BEARER_TOKEN invalid — check your token", "source": "x"})

        tweets = resp.get("data", [])
        # Build author lookup from includes
        users = {u["id"]: u for u in resp.get("includes", {}).get("users", [])}

        items = []
        for tw in tweets[:10]:
            tw_id   = tw.get("id", "")
            text    = tw.get("text", "")
            metrics = tw.get("public_metrics", {})
            likes   = int(metrics.get("like_count", 0))
            rts     = int(metrics.get("retweet_count", 0))
            created = tw.get("created_at", "")
            uid     = tw.get("author_id", "")
            user    = users.get(uid, {})
            handle  = user.get("username", "unknown")
            name    = user.get("name", handle)
            verified = bool(user.get("verified", False))

            sentiment_label, sentiment_score = _x_sentiment_score(text)

            # Detect which coins are mentioned
            mentioned = [s for s in _X_MEME_COINS if f"${s}" in text.upper() or
                         (f" {s} " in f" {text.upper()} ")]

            items.append({
                "id":              tw_id,
                "text":            text,
                "author_handle":   handle,
                "author_name":     name,
                "author_verified": verified,
                "created_at":      created,
                "likes":           likes,
                "retweets":        rts,
                "url":             f"https://x.com/{handle}/status/{tw_id}",
                "sentiment":       sentiment_label,
                "sentiment_score": sentiment_score,
                "coins":           mentioned[:4],
            })

        # Sort: highest engagement first
        items.sort(key=lambda x: x["likes"] + x["retweets"] * 2, reverse=True)

        result = {
            "items":  items,
            "total":  len(items),
            "query":  query,
            "filter": filter,
            "symbol": sym_key or None,
            "source": "x",
            "ts":     datetime.utcnow().isoformat() + "Z",
        }
        _x_feed_cache[cache_key] = {"data": result, "ts": now}
        return JSONResponse(result)

    except Exception as exc:
        log.warning("x_feed error: %s", exc)
        # Return stale cache if available
        cached_stale = _x_feed_cache.get(cache_key)
        if cached_stale:
            return JSONResponse(cached_stale["data"])
        return JSONResponse({
            "items": [], "total": 0,
            "error": f"X API unavailable: {exc}",
            "source": "x",
        })

'''

main_text = main_text.replace(ANCHOR, NEW_ENDPOINT + ANCHOR)
assert "_x_feed_cache" in main_text, "endpoint not inserted"
MAIN.write_text(main_text)
print("✓ main.py — /api/social/x-feed inserted")

# Compile check
r = subprocess.run([sys.executable, "-m", "py_compile", str(MAIN)], capture_output=True, text=True)
if r.returncode != 0:
    print("✗ compile error:", r.stderr); sys.exit(1)
print("✓ main.py compiles OK")
