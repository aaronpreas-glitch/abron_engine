#!/usr/bin/env python3
"""
Create utils/x_sentiment.py — X/Twitter sentiment scoring.

Features:
1. X API v2 recent search for cashtag mentions ($SOL, $BTC, etc.)
2. VADER sentiment analysis on tweet text
3. Volume spike detection (vs rolling average)
4. Score caching (5-min TTL to respect rate limits)
5. Graceful fallback when no X_BEARER_TOKEN is set
"""

SENTIMENT_CODE = r'''"""
X (Twitter) Sentiment Scorer

Fetches recent tweets mentioning crypto tokens, scores sentiment via VADER,
detects volume spikes, and returns a composite sentiment score.

Env:
    X_BEARER_TOKEN  — X API v2 bearer token (optional; returns neutral if missing)
    SENTIMENT_TTL   — Cache TTL in seconds (default: 300 = 5 min)

Usage:
    from utils.x_sentiment import get_sentiment, get_sentiment_batch
    score = await get_sentiment("SOL")
    # Returns: {"symbol": "SOL", "sentiment_score": 0.35, "mention_count": 42,
    #           "volume_spike": True, "avg_sentiment": 0.35, "boost": 8, ...}
"""
import os, time, logging, asyncio
from datetime import datetime, timezone, timedelta

log = logging.getLogger("x_sentiment")

# ── Config ──
_BEARER = lambda: os.environ.get("X_BEARER_TOKEN", "")
_TTL = lambda: int(os.environ.get("SENTIMENT_TTL", "300"))

# ── Cache ──
_cache: dict[str, dict] = {}
_cache_ts: dict[str, float] = {}

# ── VADER init (lazy) ──
_vader = None

def _get_vader():
    global _vader
    if _vader is None:
        try:
            from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
            _vader = SentimentIntensityAnalyzer()
            # Add crypto-specific lexicon
            _vader.lexicon.update({
                "moon": 2.5, "mooning": 3.0, "bullish": 2.0, "bearish": -2.0,
                "pump": 1.5, "dump": -2.5, "dumping": -3.0, "pumping": 2.0,
                "rug": -3.5, "rugpull": -4.0, "scam": -3.0, "gem": 2.0,
                "send": 1.5, "sending": 2.0, "rip": -1.5, "ripping": 2.0,
                "ath": 2.5, "dip": -1.0, "buy": 1.5, "sell": -1.0,
                "fomo": 1.0, "fud": -2.0, "wagmi": 2.0, "ngmi": -2.0,
                "hodl": 1.5, "whale": 1.0, "bag": 0.5, "100x": 3.0,
                "1000x": 3.0, "airdrop": 1.5, "alpha": 2.0,
                "dead": -3.0, "dying": -2.5, "crash": -3.0, "crashing": -3.0,
                "explode": 2.0, "exploding": 2.5, "rocket": 2.5,
                "broke": -2.0, "profit": 2.0, "loss": -1.5, "gains": 2.0,
            })
            log.info("[SENTIMENT] VADER initialized with crypto lexicon")
        except ImportError:
            log.warning("[SENTIMENT] vaderSentiment not installed, using fallback scoring")
            _vader = "FALLBACK"
    return _vader


def _score_text(text: str) -> float:
    """Score a single text. Returns -1 to +1."""
    vader = _get_vader()
    if vader == "FALLBACK" or vader is None:
        return _keyword_score(text)
    try:
        scores = vader.polarity_scores(text)
        return scores["compound"]
    except Exception:
        return _keyword_score(text)


def _keyword_score(text: str) -> float:
    """Simple keyword-based fallback scorer."""
    t = text.lower()
    pos_words = ["moon", "pump", "bullish", "send", "gem", "ath", "100x", "wagmi", "buy", "profit", "gains"]
    neg_words = ["dump", "rug", "scam", "bearish", "dead", "crash", "fud", "ngmi", "sell", "loss", "rip"]
    pos = sum(1 for w in pos_words if w in t)
    neg = sum(1 for w in neg_words if w in t)
    total = pos + neg
    if total == 0:
        return 0.0
    return round((pos - neg) / total, 4)


# ── Volume tracking (rolling averages) ──
_volume_history: dict[str, list[tuple[float, int]]] = {}  # symbol -> [(timestamp, count)]

def _record_volume(symbol: str, count: int):
    """Record mention count for volume spike detection."""
    now = time.time()
    if symbol not in _volume_history:
        _volume_history[symbol] = []
    _volume_history[symbol].append((now, count))
    # Keep last 24h
    cutoff = now - 86400
    _volume_history[symbol] = [(t, c) for t, c in _volume_history[symbol] if t > cutoff]


def _is_volume_spike(symbol: str, current_count: int) -> bool:
    """Check if current mention count is a spike vs rolling average."""
    history = _volume_history.get(symbol, [])
    if len(history) < 3:
        return False  # Not enough data
    avg = sum(c for _, c in history[:-1]) / max(1, len(history) - 1)
    return current_count > avg * 2.0  # 2x average = spike


# ── X API v2 search ──
async def _fetch_tweets(symbol: str, max_results: int = 30) -> list[dict]:
    """Fetch recent tweets mentioning a symbol from X API v2."""
    bearer = _BEARER()
    if not bearer:
        return []

    import httpx

    # Build query: cashtag + symbol name
    queries = [f"${symbol.upper()}", f"#{symbol.upper()}"]
    query = " OR ".join(queries) + " -is:retweet lang:en"

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                "https://api.twitter.com/2/tweets/search/recent",
                params={
                    "query": query,
                    "max_results": min(max_results, 100),
                    "tweet.fields": "created_at,public_metrics,text",
                },
                headers={"Authorization": f"Bearer {bearer}"},
            )

            if resp.status_code == 401:
                log.warning("[SENTIMENT] X API 401 — invalid bearer token")
                return []
            if resp.status_code == 429:
                log.warning("[SENTIMENT] X API rate limited")
                return []
            if resp.status_code != 200:
                log.warning("[SENTIMENT] X API %d: %s", resp.status_code, resp.text[:200])
                return []

            data = resp.json()
            tweets = data.get("data", [])
            return tweets

    except Exception as e:
        log.debug("[SENTIMENT] X API error: %s", e)
        return []


def _compute_boost(sentiment_score: float, volume_spike: bool) -> int:
    """Compute signal score boost from sentiment.
    Returns +5 to +15 for strong positive, -5 to -10 for strong negative, 0 for neutral."""
    if volume_spike and sentiment_score > 0.3:
        return 15  # Strong positive + volume spike = max boost
    elif volume_spike and sentiment_score > 0.1:
        return 10
    elif sentiment_score > 0.4:
        return 10
    elif sentiment_score > 0.2:
        return 5
    elif sentiment_score < -0.3 and volume_spike:
        return -10  # Negative sentiment + volume = danger
    elif sentiment_score < -0.2:
        return -5
    return 0


# ── Public API ──

async def get_sentiment(symbol: str) -> dict:
    """Get sentiment data for a symbol. Uses cache to respect rate limits.

    Returns:
        {
            "symbol": "SOL",
            "sentiment_score": 0.35,     # -1 to +1
            "mention_count": 42,
            "volume_spike": True,
            "avg_sentiment": 0.35,
            "boost": 8,                   # signal score adjustment
            "positive_pct": 0.65,
            "negative_pct": 0.15,
            "neutral_pct": 0.20,
            "top_positive": "SOL to the moon!",
            "top_negative": "SOL dumping hard",
            "source": "x_api" | "cached" | "no_api",
            "cached_at": "2026-02-26T...",
        }
    """
    sym = symbol.upper()

    # Check cache
    if sym in _cache and time.time() - _cache_ts.get(sym, 0) < _TTL():
        cached = dict(_cache[sym])
        cached["source"] = "cached"
        return cached

    # Fetch tweets
    tweets = await _fetch_tweets(sym)
    source = "x_api" if tweets else "no_api"

    if tweets:
        # Score each tweet
        scores = []
        for tw in tweets:
            text = tw.get("text", "")
            score = _score_text(text)
            scores.append(score)

        mention_count = len(tweets)
        avg_sent = sum(scores) / len(scores) if scores else 0
        pos_pct = sum(1 for s in scores if s > 0.05) / len(scores) if scores else 0
        neg_pct = sum(1 for s in scores if s < -0.05) / len(scores) if scores else 0
        neu_pct = 1 - pos_pct - neg_pct

        # Track volume
        _record_volume(sym, mention_count)
        vol_spike = _is_volume_spike(sym, mention_count)

        # Find best/worst tweets
        best_idx = max(range(len(scores)), key=lambda i: scores[i]) if scores else 0
        worst_idx = min(range(len(scores)), key=lambda i: scores[i]) if scores else 0

        result = {
            "symbol": sym,
            "sentiment_score": round(avg_sent, 4),
            "mention_count": mention_count,
            "volume_spike": vol_spike,
            "avg_sentiment": round(avg_sent, 4),
            "boost": _compute_boost(avg_sent, vol_spike),
            "positive_pct": round(pos_pct, 3),
            "negative_pct": round(neg_pct, 3),
            "neutral_pct": round(neu_pct, 3),
            "top_positive": tweets[best_idx].get("text", "")[:120] if tweets else "",
            "top_negative": tweets[worst_idx].get("text", "")[:120] if tweets else "",
            "source": source,
            "cached_at": datetime.now(timezone.utc).isoformat(),
        }

    else:
        # No API access — return neutral
        result = {
            "symbol": sym,
            "sentiment_score": 0.0,
            "mention_count": 0,
            "volume_spike": False,
            "avg_sentiment": 0.0,
            "boost": 0,
            "positive_pct": 0,
            "negative_pct": 0,
            "neutral_pct": 1.0,
            "top_positive": "",
            "top_negative": "",
            "source": source,
            "cached_at": datetime.now(timezone.utc).isoformat(),
        }

    # Cache
    _cache[sym] = result
    _cache_ts[sym] = time.time()

    return result


async def get_sentiment_batch(symbols: list[str]) -> dict[str, dict]:
    """Get sentiment for multiple symbols concurrently."""
    tasks = [get_sentiment(s) for s in symbols]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    out = {}
    for sym, res in zip(symbols, results):
        if isinstance(res, Exception):
            out[sym.upper()] = {
                "symbol": sym.upper(), "sentiment_score": 0, "mention_count": 0,
                "volume_spike": False, "boost": 0, "source": "error",
            }
        else:
            out[sym.upper()] = res
    return out


def get_cached_sentiment(symbol: str) -> dict | None:
    """Get cached sentiment without async. Returns None if not cached."""
    sym = symbol.upper()
    if sym in _cache and time.time() - _cache_ts.get(sym, 0) < _TTL():
        return _cache[sym]
    return None
'''

MODULE_PATH = "/root/memecoin_engine/utils/x_sentiment.py"
with open(MODULE_PATH, "w") as f:
    f.write(SENTIMENT_CODE)
print(f"✅ Written {MODULE_PATH}")
