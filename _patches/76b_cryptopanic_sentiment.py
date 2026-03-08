"""
Patch 76b — CryptoPanic + VADER Sentiment Feed
Backend: GET /api/social/sentiment-feed
  - Uses CryptoPanic API (existing CRYPTOPANIC_AUTH_TOKEN)
  - Fetches Solana memecoin headlines, VADER-scores each title
  - Returns XPost-compatible items[] format (same as x-feed / reddit-feed)
  - 15-min cache per (filter, symbol) key
  - Free with existing CryptoPanic token — no new credentials needed
"""

import subprocess, sys
from pathlib import Path

ROOT = Path("/root/memecoin_engine")
MAIN = ROOT / "dashboard/backend/main.py"

main_text = MAIN.read_text()

ANCHOR = '@app.get("/api/journal/learnings")'
assert ANCHOR in main_text, f"anchor not found: {ANCHOR}"

# Make sure we don't double-insert
assert "_sentiment_feed_cache" not in main_text, "sentiment-feed already inserted"

NEW_ENDPOINT = '''
# ── CryptoPanic + VADER Sentiment Feed ──────────────────────────────────────────
_sentiment_feed_cache: dict = {}   # keyed by (filter_key, symbol_key) → {data, ts}
_SENTIMENT_FEED_TTL = 900          # 15 min

_SENT_MEME_COINS = [
    "WIF", "BONK", "POPCAT", "FARTCOIN", "GRIFFAIN", "GOAT", "MOODENG", "PNUT",
    "MOTHER", "ACT", "NEIRO", "MEW", "BILLY", "MOG", "BRETT", "GIGA", "TURBO",
    "PENGU", "AURA", "CHILLGUY", "BOME", "WEN", "SELFIE",
]


@app.get("/api/social/sentiment-feed")
async def social_sentiment_feed(
    filter: str = "memecoins",
    symbol: str | None = None,
    _: str = Depends(get_current_user),
):
    """CryptoPanic headline sentiment feed. VADER-scored. 15-min cache."""
    import time as _t, os as _os
    import requests as _req

    token = _os.environ.get("CRYPTOPANIC_AUTH_TOKEN", "").strip()
    if not token:
        return JSONResponse({
            "items": [], "total": 0,
            "error": "CRYPTOPANIC_AUTH_TOKEN not set",
            "source": "news",
        })

    sym_key   = (symbol or "").upper().strip()
    cache_key = (filter.lower(), sym_key)
    now       = _t.time()
    cached    = _sentiment_feed_cache.get(cache_key)
    if cached and now - cached.get("ts", 0) < _SENTIMENT_FEED_TTL:
        return JSONResponse(cached["data"])

    try:
        if sym_key and sym_key in _SENT_MEME_COINS:
            currencies = sym_key
        else:
            currencies = "WIF,BONK,POPCAT,FARTCOIN,PNUT,MOODENG,MEW,GOAT,GIGA,ACT,CHILLGUY,AURA,PENGU,BOME,SOL"

        params = {
            "auth_token": token,
            "currencies": currencies,
            "public":     "true",
            "kind":       "news",
            "page_size":  25,
        }

        resp = await asyncio.to_thread(lambda: _req.get(
            "https://cryptopanic.com/api/v1/posts/",
            params=params,
            timeout=10,
        ).json())

        raw   = resp.get("results", [])
        items = []
        for post in raw[:20]:
            title      = post.get("title", "") or ""
            url        = post.get("url", "") or ""
            pub_at     = post.get("published_at", "") or ""
            post_id    = str(post.get("id", ""))
            votes      = post.get("votes", {}) or {}
            votes_pos  = int(votes.get("positive", 0) or 0)
            votes_neg  = int(votes.get("negative", 0) or 0)
            votes_imp  = int(votes.get("important", 0) or 0)
            source     = post.get("source", {}) or {}
            src_domain = source.get("domain", "") or ""
            src_title  = source.get("title", "") or src_domain
            coins      = [
                c.get("code", "") for c in (post.get("currencies") or [])
                if c.get("code")
            ]

            sentiment_label, sentiment_score = _x_sentiment_score(title)

            items.append({
                "id":              post_id,
                "text":            title,
                "author_handle":   src_domain,
                "author_name":     src_title,
                "author_verified": False,
                "created_at":      pub_at,
                "likes":           votes_pos + votes_imp,   # bullish votes
                "retweets":        votes_neg,               # bearish votes
                "url":             url,
                "sentiment":       sentiment_label,
                "sentiment_score": sentiment_score,
                "coins":           coins[:4],
                "subreddit":       src_domain,              # repurposed: news source
                "source":          "news",
            })

        # Sort: most-discussed first (bullish + bearish engagement)
        items.sort(key=lambda x: x["likes"] + x["retweets"] * 2, reverse=True)
        items = items[:10]

        result = {
            "items":  items,
            "total":  len(items),
            "filter": filter,
            "symbol": sym_key or None,
            "source": "news",
            "ts":     datetime.utcnow().isoformat() + "Z",
        }
        _sentiment_feed_cache[cache_key] = {"data": result, "ts": now}
        return JSONResponse(result)

    except Exception as exc:
        log.warning("sentiment_feed error: %s", exc)
        cached_stale = _sentiment_feed_cache.get(cache_key)
        if cached_stale:
            return JSONResponse(cached_stale["data"])
        return JSONResponse({
            "items": [], "total": 0,
            "error": f"Sentiment feed unavailable: {exc}",
            "source": "news",
        })


'''

main_text = main_text.replace(ANCHOR, NEW_ENDPOINT + ANCHOR)
assert "_sentiment_feed_cache" in main_text, "endpoint not inserted"
MAIN.write_text(main_text)
print("✓ main.py — /api/social/sentiment-feed inserted")

# Compile check
r = subprocess.run([sys.executable, "-m", "py_compile", str(MAIN)], capture_output=True, text=True)
if r.returncode != 0:
    print("✗ compile error:", r.stderr); sys.exit(1)
print("✓ main.py compiles OK")
