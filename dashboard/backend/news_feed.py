"""
news_feed.py — Aggregate crypto news from public RSS feeds.

Feeds used (all free, no API key required):
  • CoinDesk       https://www.coindesk.com/arc/outboundfeeds/rss/
  • CoinTelegraph  https://cointelegraph.com/rss
  • Decrypt        https://decrypt.co/feed
  • The Block      https://www.theblock.co/rss.xml
  • Solana News    https://solana.com/news/rss.xml  (lighter fallback)

Returns up to `limit` items sorted by published date descending.
Results are cached for `CACHE_TTL` seconds to avoid hammering feeds.
"""
from __future__ import annotations

import re
import time
import threading
import logging
import concurrent.futures
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Optional
from xml.etree import ElementTree as ET

import requests

log = logging.getLogger("dashboard.news")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

FEEDS: list[tuple[str, str]] = [
    ("CoinDesk",      "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("CoinTelegraph", "https://cointelegraph.com/rss"),
    ("Decrypt",       "https://decrypt.co/feed"),
    ("The Block",     "https://www.theblock.co/rss.xml"),
]

FETCH_TIMEOUT = 8          # seconds per feed
CACHE_TTL     = 300        # 5 min cache
MAX_ITEMS     = 60         # max items to cache per fetch

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; MemecoinDashboard/1.0; +https://github.com)",
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
}

# Solana / meme keywords for relevance tagging
_SOL_KEYWORDS = re.compile(
    r"\b(solana|sol\b|memecoin|meme coin|pump\.fun|dex|defi|nft|bonk|jup|wif|fartcoin|"
    r"raydi|raydium|orca|jito|drift|marinade|phantom|jupiter|squads|tensor|magic eden)\b",
    re.I,
)
_MARKET_KEYWORDS = re.compile(
    r"\b(bitcoin|btc|ethereum|eth|altcoin|crypto|market|rally|dump|bull|bear|"
    r"liquidat|fed|interest rate|inflation|etf|sec|cftc|regulate)\b",
    re.I,
)

# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

_cache_lock   = threading.Lock()
_cached_items: list[dict] = []
_cached_at    = 0.0


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

_NS = {
    "media":   "http://search.yahoo.com/mrss/",
    "content": "http://purl.org/rss/1.0/modules/content/",
    "dc":      "http://purl.org/dc/elements/1.1/",
}


def _strip_html(text: str) -> str:
    """Remove HTML tags and collapse whitespace."""
    clean = re.sub(r"<[^>]+>", "", text or "")
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean


def _parse_date(raw: Optional[str]) -> Optional[str]:
    """Parse RFC-2822 or ISO-8601 date → ISO-8601 UTC string."""
    if not raw:
        return None
    try:
        dt = parsedate_to_datetime(raw.strip())
        return dt.astimezone(timezone.utc).isoformat()
    except Exception:
        pass
    # Try ISO-8601 fallback
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(raw.strip()[:25], fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc).isoformat()
        except Exception:
            pass
    return None


def _tag(text: str) -> str:
    """Return relevance tag: SOL, MARKET, or CRYPTO."""
    if _SOL_KEYWORDS.search(text):
        return "SOL"
    if _MARKET_KEYWORDS.search(text):
        return "MARKET"
    return "CRYPTO"


def _fetch_feed(source: str, url: str) -> list[dict]:
    """Fetch + parse a single RSS feed. Returns list of item dicts."""
    try:
        resp = requests.get(url, timeout=FETCH_TIMEOUT, headers=_HEADERS)
        resp.raise_for_status()
    except Exception as exc:
        log.warning("Feed fetch failed [%s]: %s", source, exc)
        return []

    try:
        root = ET.fromstring(resp.content)
    except ET.ParseError as exc:
        log.warning("Feed parse failed [%s]: %s", source, exc)
        return []

    # Handle both RSS <channel><item> and Atom <entry>
    channel = root.find("channel")
    items_el = (channel or root).findall("item") or root.findall(
        "{http://www.w3.org/2005/Atom}entry"
    )

    results = []
    for el in items_el:
        def txt(tag: str) -> str:
            node = el.find(tag)
            return (node.text or "").strip() if node is not None else ""

        title   = _strip_html(txt("title"))
        link    = txt("link") or txt("guid")
        pubdate = _parse_date(txt("pubDate") or txt("published") or txt("dc:date"))
        desc    = _strip_html(txt("description") or txt("summary") or "")
        # Trim description to ~200 chars
        if len(desc) > 220:
            desc = desc[:220].rsplit(" ", 1)[0] + "…"

        if not title or not link:
            continue

        combined = title + " " + desc
        results.append({
            "id":      f"{source}::{link}",
            "source":  source,
            "title":   title,
            "link":    link,
            "summary": desc,
            "pub_ts":  pubdate,
            "tag":     _tag(combined),
        })

    log.debug("Feed [%s] → %d items", source, len(results))
    return results


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _sort_key(item: dict) -> str:
    return item.get("pub_ts") or "1970-01-01T00:00:00+00:00"


def fetch_news(limit: int = 40, tag: Optional[str] = None) -> list[dict]:
    """
    Return up to `limit` news items, newest first.
    Optionally filter by tag: 'SOL', 'MARKET', or 'CRYPTO'.
    Uses a 5-minute in-process cache.
    """
    global _cached_items, _cached_at

    now = time.time()
    with _cache_lock:
        if now - _cached_at < CACHE_TTL and _cached_items:
            items = list(_cached_items)
        else:
            items = None

    if items is None:
        # Fetch all feeds concurrently
        all_items: list[dict] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(FEEDS)) as pool:
            futures = {pool.submit(_fetch_feed, src, url): src for src, url in FEEDS}
            for fut in concurrent.futures.as_completed(futures):
                all_items.extend(fut.result())

        # Deduplicate by title (feeds sometimes cross-post)
        seen_titles: set[str] = set()
        deduped = []
        for item in sorted(all_items, key=_sort_key, reverse=True):
            norm = item["title"].lower()[:80]
            if norm not in seen_titles:
                seen_titles.add(norm)
                deduped.append(item)

        items = deduped[:MAX_ITEMS]

        with _cache_lock:
            _cached_items = items
            _cached_at    = now
        log.info("News cache refreshed — %d items from %d feeds", len(items), len(FEEDS))

    if tag:
        items = [i for i in items if i["tag"] == tag.upper()]

    return items[:limit]
