"""
crypto_news.py — Daily Crypto News Digest + Intraday Updates
Fetches real data from free APIs: CoinGecko, Fear & Greed, RSS feeds, DexScreener.
No API keys required.

Schedule:
  09:00 UTC — Full morning digest (market overview + gainers/losers + headlines)
  Every 3h  — Intraday headline check (only sends if new stories found)
  Next 09:00 — Full digest again with prior 24h recap
"""

import logging
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import requests

# ── Cache & state ──────────────────────────────────────────────────────────────
_cache = {
    "digest": None,
    "ts": 0.0,
}
_CACHE_TTL = 3600 * 8  # rebuild every 8 hours

# Track which headline titles we've already sent, so intraday updates only
# send genuinely new stories. Reset at midnight UTC.
_seen_headlines: set = set()
_seen_reset_day: int = -1  # UTC day number of last reset

# ── Constants ──────────────────────────────────────────────────────────────────
SEP = "━━━━━━━━━━━━━━━━━━━━━━━━━━━"
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; CryptoNewsBot/1.0)"}

RSS_FEEDS = [
    ("CoinTelegraph", "https://cointelegraph.com/rss"),
    ("CoinDesk",      "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("TheBlock",      "https://www.theblock.co/rss.xml"),
]

STABLECOINS = {"usdt","usdc","dai","busd","tusd","usdp","usdd","frax","lusd","susd","xaut","paxg"}

# ── HTTP helper ────────────────────────────────────────────────────────────────

def _get(url, params=None, timeout=10):
    try:
        r = requests.get(url, params=params, headers=_HEADERS, timeout=timeout)
        if r.status_code == 200:
            return r
    except Exception as e:
        logging.warning("crypto_news fetch error %s: %s", url, e)
    return None


# ── Formatters ────────────────────────────────────────────────────────────────

def _fmt_price(v):
    if v is None:
        return "N/A"
    v = float(v)
    if v >= 10000:
        return f"${v:,.0f}"
    if v >= 100:
        return f"${v:,.1f}"
    return f"${v:,.2f}"


def _fmt_vol(v):
    if v is None:
        return "N/A"
    v = float(v)
    if v >= 1e9:
        return f"${v/1e9:.1f}B"
    if v >= 1e6:
        return f"${v/1e6:.1f}M"
    return f"${v:,.0f}"


def _fmt_cap(v):
    if v is None:
        return "N/A"
    v = float(v)
    if v >= 1e12:
        return f"${v/1e12:.2f}T"
    if v >= 1e9:
        return f"${v/1e9:.1f}B"
    return f"${v/1e6:.0f}M"


# ── Data fetchers ──────────────────────────────────────────────────────────────

def fetch_market_overview():
    """BTC, ETH, SOL prices + global market cap + dominance + Fear & Greed."""
    prices = {}
    try:
        r = _get("https://api.coingecko.com/api/v3/simple/price", params={
            "ids": "bitcoin,ethereum,solana",
            "vs_currencies": "usd",
            "include_24hr_change": "true",
            "include_24hr_vol": "true",
            "include_market_cap": "true",
        })
        if r:
            prices = r.json()
    except Exception:
        pass

    global_data = {}
    try:
        r = _get("https://api.coingecko.com/api/v3/global")
        if r:
            global_data = r.json().get("data", {})
    except Exception:
        pass

    fg = {}
    try:
        r = _get("https://api.alternative.me/fng/?limit=1")
        if r:
            d = r.json().get("data", [{}])[0]
            fg = {"value": d.get("value"), "label": d.get("value_classification")}
    except Exception:
        pass

    return {"prices": prices, "global": global_data, "fear_greed": fg}


def fetch_top_movers():
    """Top 5 gainers and losers from top 100 coins by market cap."""
    try:
        r = _get("https://api.coingecko.com/api/v3/coins/markets", params={
            "vs_currency": "usd",
            "order": "market_cap_desc",
            "per_page": 100,
            "page": 1,
            "price_change_percentage": "24h",
        })
        if not r:
            return None
        coins = [c for c in r.json() if c.get("symbol", "").lower() not in STABLECOINS]
        sorted_coins = sorted(coins, key=lambda c: c.get("price_change_percentage_24h") or 0, reverse=True)
        return {"gainers": sorted_coins[:5], "losers": sorted_coins[-5:][::-1]}
    except Exception as e:
        logging.warning("fetch_top_movers error: %s", e)
        return None


def fetch_sol_ecosystem():
    """SOL price + trending DexScreener tokens on Solana."""
    sol_price = sol_change = sol_vol = None
    try:
        r = _get("https://api.coingecko.com/api/v3/simple/price", params={
            "ids": "solana",
            "vs_currencies": "usd",
            "include_24hr_change": "true",
            "include_24hr_vol": "true",
        })
        if r:
            d = r.json().get("solana", {})
            sol_price = d.get("usd")
            sol_change = d.get("usd_24h_change")
            sol_vol = d.get("usd_24h_vol")
    except Exception:
        pass

    trending = []
    try:
        r = _get("https://api.dexscreener.com/token-profiles/latest/v1")
        if r:
            items = r.json() if isinstance(r.json(), list) else []
            trending = [i for i in items if i.get("chainId") == "solana"][:5]
    except Exception:
        pass

    return {"sol_price": sol_price, "sol_change": sol_change, "sol_vol": sol_vol, "trending": trending}


def fetch_crypto_news(limit=8, mark_seen=False):
    """
    Fetch latest headlines from RSS feeds.
    mark_seen=True: add all fetched titles to _seen_headlines (used at morning digest time).
    Returns list of {"source", "title"} dicts.
    """
    global _seen_headlines, _seen_reset_day

    # Reset seen set at midnight UTC
    today = datetime.now(timezone.utc).timetuple().tm_yday
    if today != _seen_reset_day:
        _seen_headlines = set()
        _seen_reset_day = today

    headlines = []
    for source, url in RSS_FEEDS:
        try:
            r = _get(url, timeout=8)
            if not r:
                continue
            root = ET.fromstring(r.text)
            for item in root.findall(".//item")[:5]:
                title = item.findtext("title", "").strip()
                if title:
                    headlines.append({"source": source, "title": title})
        except Exception as e:
            logging.warning("RSS error %s: %s", source, e)

    if mark_seen:
        for h in headlines:
            _seen_headlines.add(h["title"])

    return headlines[:limit]


def fetch_new_headlines():
    """
    Return only headlines not yet seen since last reset.
    Used for intraday updates — only pushes truly new stories.
    """
    global _seen_headlines

    all_headlines = fetch_crypto_news(limit=15, mark_seen=False)
    new = [h for h in all_headlines if h["title"] not in _seen_headlines]

    # Mark newly discovered ones as seen
    for h in new:
        _seen_headlines.add(h["title"])

    return new


def fetch_perps_data(position=None):
    """Build perps section dict from normalized Jupiter position."""
    result = {
        "sol_funding": None, "liq_distance": None, "liq_price": None,
        "pnl": None, "leverage": None, "entry": None, "mark": None,
    }
    if not position:
        return result
    from jupiter_perps import calc_liq_distance_pct
    mark = position.get("mark_price")
    liq  = position.get("liq_price")
    result.update({
        "sol_funding":  position.get("funding_rate"),
        "liq_distance": calc_liq_distance_pct(mark, liq),
        "liq_price":    liq,
        "pnl":          position.get("pnl"),
        "leverage":     position.get("leverage"),
        "entry":        position.get("entry_price"),
        "mark":         mark,
    })
    return result


# ── Digest builders ────────────────────────────────────────────────────────────

def build_digest(position=None):
    """
    Full morning digest — market overview, gainers/losers, SOL, headlines, position.
    Returns list of 2 message strings.
    Also marks all fetched headlines as seen so intraday only sends NEW ones.
    """
    now      = datetime.now(timezone.utc)
    date_str = now.strftime("%B %d, %Y  |  %H:%M UTC")

    market     = fetch_market_overview()
    movers     = fetch_top_movers()
    sol_eco    = fetch_sol_ecosystem()
    headlines  = fetch_crypto_news(limit=6, mark_seen=True)   # ← marks seen
    perps      = fetch_perps_data(position)

    prices   = market.get("prices", {})
    global_d = market.get("global", {})
    fg       = market.get("fear_greed", {})

    btc = prices.get("bitcoin", {})
    eth = prices.get("ethereum", {})
    sol = prices.get("solana", {})

    total_cap = global_d.get("total_market_cap", {}).get("usd")
    cap_chg   = global_d.get("market_cap_change_percentage_24h_usd")
    btc_dom   = global_d.get("market_cap_percentage", {}).get("btc")

    fg_val   = fg.get("value", "N/A")
    fg_label = fg.get("label", "N/A")
    try:
        fg_int = int(fg_val)
    except (TypeError, ValueError):
        fg_int = 50
    fg_emoji = "😱" if fg_int < 25 else ("😨" if fg_int < 45 else ("😐" if fg_int < 55 else ("😊" if fg_int < 75 else "🤑")))

    # ── Message 1: Market overview + movers ───────────────────
    lines1 = [
        f"<b>📰 DAILY CRYPTO DIGEST</b>",
        f"<code>{SEP}</code>",
        f"<code>📅 {date_str}</code>",
        f"<code>{SEP}</code>",
        f"<b>🔥 MARKET OVERVIEW</b>",
        f"<code>{SEP}</code>",
    ]

    if btc:
        btc_chg = btc.get("usd_24h_change", 0) or 0
        lines1.append(f"<code>₿  BTC  {_fmt_price(btc.get('usd'))} ({btc_chg:+.1f}%)  Vol {_fmt_vol(btc.get('usd_24h_vol'))}</code>")
    if eth:
        eth_chg = eth.get("usd_24h_change", 0) or 0
        lines1.append(f"<code>Ξ  ETH  {_fmt_price(eth.get('usd'))} ({eth_chg:+.1f}%)  Vol {_fmt_vol(eth.get('usd_24h_vol'))}</code>")
    if sol:
        sol_chg = sol.get("usd_24h_change", 0) or 0
        lines1.append(f"<code>◎  SOL  {_fmt_price(sol.get('usd'))} ({sol_chg:+.1f}%)  Vol {_fmt_vol(sol.get('usd_24h_vol'))}</code>")
    if total_cap:
        cap_str = f"{cap_chg:+.1f}%" if cap_chg else ""
        lines1.append(f"<code>Market Cap : {_fmt_cap(total_cap)} {cap_str}</code>")
    if btc_dom:
        lines1.append(f"<code>BTC Dom    : {btc_dom:.1f}%</code>")
    if fg_val and fg_val != "N/A":
        lines1.append(f"<code>{fg_emoji} Fear &amp; Greed : {fg_val}/100 ({fg_label})</code>")

    if movers and movers.get("gainers"):
        lines1 += [f"<code>{SEP}</code>", f"<b>📈 TOP GAINERS (24h)</b>", f"<code>{SEP}</code>"]
        for i, c in enumerate(movers["gainers"], 1):
            sym = c.get("symbol", "?").upper()
            chg = c.get("price_change_percentage_24h", 0) or 0
            lines1.append(f"<code>{i}. ${sym:<8} {chg:+.1f}%  {_fmt_price(c.get('current_price'))}</code>")

    if movers and movers.get("losers"):
        lines1 += [f"<code>{SEP}</code>", f"<b>📉 TOP LOSERS (24h)</b>", f"<code>{SEP}</code>"]
        for i, c in enumerate(movers["losers"], 1):
            sym = c.get("symbol", "?").upper()
            chg = c.get("price_change_percentage_24h", 0) or 0
            lines1.append(f"<code>{i}. ${sym:<8} {chg:+.1f}%  {_fmt_price(c.get('current_price'))}</code>")

    msg1 = "\n".join(lines1)

    # ── Message 2: SOL + headlines + position ─────────────────
    lines2 = [f"<b>🟢 SOL ECOSYSTEM</b>", f"<code>{SEP}</code>"]

    sol_p = sol_eco.get("sol_price")
    sol_c = sol_eco.get("sol_change")
    if sol_p:
        chg_str = f"{sol_c:+.1f}%" if sol_c else ""
        lines2.append(f"<code>SOL Price : {_fmt_price(sol_p)} {chg_str}</code>")
        lines2.append(f"<code>24h Vol   : {_fmt_vol(sol_eco.get('sol_vol'))}</code>")

    trending = sol_eco.get("trending", [])
    if trending:
        lines2.append(f"<code>🔥 Trending on Solana:</code>")
        for t in trending[:4]:
            addr = t.get("tokenAddress", "")[:8]
            desc = str(t.get("description") or "")[:35]
            lines2.append(f"<code>  {addr}...  {desc}</code>")

    if headlines:
        lines2 += [f"<code>{SEP}</code>", f"<b>📰 HEADLINES</b>", f"<code>{SEP}</code>"]
        for h in headlines:
            src   = h["source"][:2].upper()
            title = h["title"][:68]
            lines2.append(f"<code>[{src}] {title}</code>")

    perps_mark = perps.get("mark")
    if perps_mark:
        pnl    = perps.get("pnl", 0) or 0
        liq_d  = perps.get("liq_distance")
        liq_p  = perps.get("liq_price", 0)
        lev    = perps.get("leverage", 0)
        fund   = perps.get("sol_funding", 0)
        entry  = perps.get("entry", 0)
        pnl_e  = "🟢" if pnl >= 0 else "🔴"
        liq_e  = "🔴" if (liq_d and liq_d < 20) else ("🟡" if (liq_d and liq_d < 35) else "🟢")
        lines2 += [
            f"<code>{SEP}</code>",
            f"<b>🎯 YOUR SOL POSITION</b>",
            f"<code>{SEP}</code>",
            f"<code>Entry    : {_fmt_price(entry)}  |  Mark: {_fmt_price(perps_mark)}</code>",
            f"<code>Leverage : {lev:.2f}x</code>",
            f"<code>{pnl_e} PnL     : ${pnl:+,.2f}</code>",
        ]
        if liq_d is not None:
            lines2.append(f"<code>{liq_e} Liq dist: {liq_d:.1f}%  (liq {_fmt_price(liq_p)})</code>")
        if fund:
            fe = "⚠️" if fund > 0.05 else "💤"
            lines2.append(f"<code>{fe} Funding : {fund:.4f}%/day</code>")

    lines2.append(f"<code>{SEP}</code>")
    msg2 = "\n".join(lines2)

    return [msg1, msg2]


def build_intraday_update(new_headlines, position=None):
    """
    Compact intraday headline update card.
    Only called when there are new stories since the last send.
    Includes a quick SOL price + position snapshot.
    """
    now     = datetime.now(timezone.utc)
    time_str = now.strftime("%H:%M UTC")
    perps   = fetch_perps_data(position)

    # Quick SOL price
    sol_price = None
    try:
        r = _get("https://api.coingecko.com/api/v3/simple/price", params={
            "ids": "solana", "vs_currencies": "usd", "include_24hr_change": "true"
        })
        if r:
            d = r.json().get("solana", {})
            sol_price = d.get("usd")
            sol_chg   = d.get("usd_24h_change", 0) or 0
    except Exception:
        sol_price = None
        sol_chg   = 0

    lines = [
        f"<b>📡 CRYPTO UPDATE — {time_str}</b>",
        f"<code>{SEP}</code>",
    ]

    if sol_price:
        chg_e = "🟢" if sol_chg >= 0 else "🔴"
        lines.append(f"<code>{chg_e} SOL {_fmt_price(sol_price)} ({sol_chg:+.1f}%)</code>")

    mark = perps.get("mark")
    if mark:
        pnl   = perps.get("pnl", 0) or 0
        pnl_e = "🟢" if pnl >= 0 else "🔴"
        liq_d = perps.get("liq_distance")
        lines.append(f"<code>{pnl_e} Your PnL: ${pnl:+,.2f}  |  Liq: {liq_d:.1f}% away</code>" if liq_d else f"<code>{pnl_e} Your PnL: ${pnl:+,.2f}</code>")

    lines += [f"<code>{SEP}</code>", f"<b>📰 NEW HEADLINES</b>", f"<code>{SEP}</code>"]

    for h in new_headlines[:6]:
        src   = h["source"][:2].upper()
        title = h["title"][:68]
        lines.append(f"<code>[{src}] {title}</code>")

    lines.append(f"<code>{SEP}</code>")
    return "\n".join(lines)


def check_news_updates(position=None):
    """
    Called by scheduler every 3 hours.
    Returns intraday update message string if new headlines found, else None.
    """
    try:
        new = fetch_new_headlines()
        if not new:
            return None
        return build_intraday_update(new, position=position)
    except Exception as e:
        logging.warning("check_news_updates error: %s", e)
        return None


def get_digest(position=None, force=False):
    """Return cached digest or rebuild if stale. Returns list of message strings."""
    now = time.time()
    if not force and _cache["digest"] and now - _cache["ts"] < _CACHE_TTL:
        return _cache["digest"]
    try:
        msgs = build_digest(position=position)
        _cache["digest"] = msgs
        _cache["ts"] = now
        return msgs
    except Exception as e:
        logging.warning("build_digest error: %s", e)
        return [f"<b>📰 DAILY CRYPTO DIGEST</b>\n<code>⚠️ Error: {e}</code>"]
