"""
Patch 76 — Reddit Sentiment Feed (free, no API key)
Backend: GET /api/social/reddit-feed
  - No authentication required — uses Reddit public JSON API
  - Params: ?filter=memecoins (default) | ?symbol=WIF
  - Searches r/CryptoMoonShots + r/solana for Solana memecoin posts
  - VADER sentiment scoring (reuses _x_sentiment_score from Patch 75)
  - 15-min cache per (filter, symbol) key
  - Returns same items[] format as x-feed for drop-in frontend compatibility
"""

import subprocess, sys
from pathlib import Path

ROOT = Path("/root/memecoin_engine")
MAIN = ROOT / "dashboard/backend/main.py"

main_text = MAIN.read_text()

ANCHOR = '@app.get("/api/journal/learnings")'
assert ANCHOR in main_text, f"anchor not found: {ANCHOR}"

NEW_ENDPOINT = '''
# ── Reddit Sentiment Feed ──────────────────────────────────────────────────────
_reddit_feed_cache: dict = {}   # keyed by (filter_key, symbol_key) → {data, ts}
_REDDIT_FEED_TTL = 900          # 15 min

_REDDIT_MEME_COINS = [
    "WIF", "BONK", "POPCAT", "FARTCOIN", "GRIFFAIN", "GOAT", "MOODENG", "PNUT",
    "MOTHER", "ACT", "NEIRO", "MEW", "BILLY", "MOG", "BRETT", "GIGA", "TURBO",
    "PENGU", "AURA", "CHILLGUY", "BOME", "WEN", "SELFIE",
]


@app.get("/api/social/reddit-feed")
async def social_reddit_feed(
    filter: str = "memecoins",
    symbol: str | None = None,
    _: str = Depends(get_current_user),
):
    """Reddit sentiment feed for Solana memecoins. 15-min cache. No API key required."""
    import time as _t
    import requests as _req

    sym_key   = (symbol or "").upper().strip()
    cache_key = (filter.lower(), sym_key)
    now       = _t.time()
    cached    = _reddit_feed_cache.get(cache_key)
    if cached and now - cached.get("ts", 0) < _REDDIT_FEED_TTL:
        return JSONResponse(cached["data"])

    headers = {"User-Agent": "MemecoinEngine/1.0 (crypto trading dashboard)"}

    try:
        if sym_key and sym_key in _REDDIT_MEME_COINS:
            urls = [
                f"https://www.reddit.com/search.json?q={sym_key}+solana&sort=new&t=day&limit=25",
                f"https://www.reddit.com/r/CryptoMoonShots/search.json?q={sym_key}&sort=new&t=day&restrict_sr=1&limit=15",
            ]
        else:
            top_q = "WIF+OR+BONK+OR+POPCAT+OR+FARTCOIN+OR+MOODENG+OR+PNUT+OR+GIGA+OR+PENGU"
            urls = [
                f"https://www.reddit.com/r/CryptoMoonShots/search.json?q={top_q}&sort=new&t=day&restrict_sr=1&limit=25",
                "https://www.reddit.com/r/solana/search.json?q=WIF+OR+BONK+OR+FARTCOIN+OR+MOODENG+OR+PNUT&sort=new&t=day&restrict_sr=1&limit=15",
            ]

        async def _fetch_reddit(url: str) -> list:
            try:
                resp = await asyncio.to_thread(
                    lambda: _req.get(url, headers=headers, timeout=8).json()
                )
                return resp.get("data", {}).get("children", [])
            except Exception:
                return []

        all_children = await asyncio.gather(*[_fetch_reddit(u) for u in urls])

        seen_ids: set = set()
        posts: list   = []
        for children in all_children:
            for child in children:
                d       = child.get("data", {})
                post_id = d.get("id", "")
                if not post_id or post_id in seen_ids:
                    continue
                seen_ids.add(post_id)

                title     = d.get("title", "") or ""
                selftext  = (d.get("selftext", "") or "")[:200]
                full_text = f"{title}. {selftext}".strip(". ")
                text_up   = f" {full_text.upper()} "

                mentioned = [
                    s for s in _REDDIT_MEME_COINS
                    if f"${s}" in text_up or f" {s} " in text_up
                ]
                if not mentioned and not sym_key:
                    continue   # skip posts that don't mention any of our coins

                author       = d.get("author", "unknown") or "unknown"
                score        = int(d.get("score", 0) or 0)
                num_comments = int(d.get("num_comments", 0) or 0)
                created_utc  = float(d.get("created_utc", 0) or 0)
                permalink    = d.get("permalink", "") or ""
                subreddit    = d.get("subreddit", "") or ""
                ts           = (
                    datetime.utcfromtimestamp(created_utc).isoformat() + "Z"
                ) if created_utc else ""

                sentiment_label, sentiment_score = _x_sentiment_score(full_text)

                posts.append({
                    "id":              post_id,
                    "text":            full_text[:280],
                    "author_handle":   author,
                    "author_name":     f"u/{author}",
                    "author_verified": False,
                    "created_at":      ts,
                    "likes":           score,        # upvote score
                    "retweets":        num_comments,  # repurposed as comment count
                    "url":             f"https://reddit.com{permalink}",
                    "sentiment":       sentiment_label,
                    "sentiment_score": sentiment_score,
                    "coins":           mentioned[:4],
                    "subreddit":       subreddit,
                    "source":          "reddit",
                })

        posts.sort(key=lambda x: x["likes"] + x["retweets"] * 2, reverse=True)
        items = posts[:10]

        result = {
            "items":  items,
            "total":  len(items),
            "filter": filter,
            "symbol": sym_key or None,
            "source": "reddit",
            "ts":     datetime.utcnow().isoformat() + "Z",
        }
        _reddit_feed_cache[cache_key] = {"data": result, "ts": now}
        return JSONResponse(result)

    except Exception as exc:
        log.warning("reddit_feed error: %s", exc)
        cached_stale = _reddit_feed_cache.get(cache_key)
        if cached_stale:
            return JSONResponse(cached_stale["data"])
        return JSONResponse({
            "items": [], "total": 0,
            "error": f"Reddit API unavailable: {exc}",
            "source": "reddit",
        })


'''

main_text = main_text.replace(ANCHOR, NEW_ENDPOINT + ANCHOR)
assert "_reddit_feed_cache" in main_text, "endpoint not inserted"
MAIN.write_text(main_text)
print("✓ main.py — /api/social/reddit-feed inserted")

# Compile check
r = subprocess.run([sys.executable, "-m", "py_compile", str(MAIN)], capture_output=True, text=True)
if r.returncode != 0:
    print("✗ compile error:", r.stderr); sys.exit(1)
print("✓ main.py compiles OK")
