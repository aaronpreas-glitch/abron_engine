"""
Narrative Momentum — Patch 127.

Fetches CoinGecko and DexScreener trending data, stores in kv_store.
Provides is_trending() for scorer integration and get_narrative_data() for API.

Sources (all free, no API key required):
  - CoinGecko  /api/v3/search/trending     — top 7 globally trending coins
  - DexScreener /token-boosts/top/v1       — boosted Solana tokens (active projects)
"""
from __future__ import annotations

import json
import logging

import requests

from datetime import datetime, timezone

log = logging.getLogger("narrative_momentum")

COINGECKO_TRENDING_URL = "https://api.coingecko.com/api/v3/search/trending"
DEXSCREENER_BOOSTS_URL = "https://api.dexscreener.com/token-boosts/top/v1"
REQUEST_TIMEOUT        = 8
KV_KEY                 = "narrative_trending"


def _get_conn():
    from utils.db import get_conn  # type: ignore
    return get_conn()


# ── Fetchers ──────────────────────────────────────────────────────────────────

def fetch_coingecko_trending() -> list:
    """Fetch CoinGecko top trending coins. Returns list of {symbol, name, rank, source}."""
    try:
        r = requests.get(
            COINGECKO_TRENDING_URL,
            timeout=REQUEST_TIMEOUT,
            headers={"User-Agent": "memecoin-engine/1.0"},
        )
        if r.status_code != 200:
            log.warning("CoinGecko trending HTTP %s", r.status_code)
            return []
        coins = r.json().get("coins", [])
        result = []
        for item in coins:
            coin = item.get("item", {})
            sym  = (coin.get("symbol", "") or "").upper()
            if sym:
                result.append({
                    "symbol": sym,
                    "name":   coin.get("name", ""),
                    "rank":   coin.get("market_cap_rank"),
                    "source": "coingecko",
                })
        return result
    except Exception as exc:
        log.warning("CoinGecko trending fetch error: %s", exc)
        return []


def fetch_dexscreener_trending_solana() -> list:
    """Fetch DexScreener top-boosted Solana tokens. Returns list of {symbol, mint, boosts, source}."""
    try:
        r = requests.get(
            DEXSCREENER_BOOSTS_URL,
            timeout=REQUEST_TIMEOUT,
            headers={"User-Agent": "memecoin-engine/1.0"},
        )
        if r.status_code != 200:
            log.warning("DexScreener boosts HTTP %s", r.status_code)
            return []
        items = r.json()
        if not isinstance(items, list):
            return []
        result = []
        for item in items:
            if item.get("chainId") != "solana":
                continue
            mint   = item.get("tokenAddress", "")
            symbol = (item.get("symbol", "") or "").upper()
            boosts = int(item.get("totalAmount", 0) or 0)
            if not mint:
                continue
            result.append({
                "symbol": symbol,
                "mint":   mint,
                "boosts": boosts,
                "source": "dexscreener",
            })
        return result[:20]   # top 20 Solana boosted tokens
    except Exception as exc:
        log.warning("DexScreener trending fetch error: %s", exc)
        return []


# ── Cache update ──────────────────────────────────────────────────────────────

def update_narrative_momentum() -> dict:
    """
    Fetch trending data from both sources, merge, and persist in kv_store.
    Called every 4h from research_step(). Returns the stored payload.
    """
    cg_coins   = fetch_coingecko_trending()
    dex_tokens = fetch_dexscreener_trending_solana()

    # Build fast-lookup sets for is_trending()
    cg_symbols  = list({c["symbol"] for c in cg_coins})
    dex_mints   = list({t["mint"]   for t in dex_tokens})
    dex_symbols = list({t["symbol"] for t in dex_tokens})

    payload = {
        "updated_at":  datetime.now(timezone.utc).isoformat(),
        "coingecko":   cg_coins,
        "dexscreener": dex_tokens,
        "cg_symbols":  cg_symbols,
        "dex_mints":   dex_mints,
        "dex_symbols": dex_symbols,
    }

    try:
        with _get_conn() as conn:
            conn.execute(
                "INSERT INTO kv_store (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (KV_KEY, json.dumps(payload)),
            )
    except Exception as exc:
        log.warning("narrative_momentum kv_store write error: %s", exc)

    log.info(
        "Narrative updated: CoinGecko=%d trending, DexScreener=%d Solana boosted",
        len(cg_coins),
        len(dex_tokens),
    )
    return payload


# ── Lookups ───────────────────────────────────────────────────────────────────

def get_narrative_data() -> dict:
    """Read cached narrative data from kv_store. Returns empty dict if not found."""
    try:
        with _get_conn() as conn:
            row = conn.execute(
                "SELECT value FROM kv_store WHERE key=?", (KV_KEY,)
            ).fetchone()
            if row:
                return json.loads(row["value"])
    except Exception:
        pass
    return {}


def is_trending(symbol: str, mint: str) -> dict:
    """
    Check if a token is trending on CoinGecko or DexScreener.

    Returns:
        {trending: bool, sources: list[str], bonus: int}

    Bonus values:
        +5  — DexScreener Solana boost (project is actively spending on promotion)
        +3  — CoinGecko global trending only (broader market attention)
        +8  — both sources (cap applied by scorer)
    """
    data = get_narrative_data()
    if not data:
        return {"trending": False, "sources": [], "bonus": 0}

    sym_upper = (symbol or "").upper()
    sources   = []

    if sym_upper in (data.get("cg_symbols") or []):
        sources.append("coingecko")

    if (mint in (data.get("dex_mints") or []) or
            sym_upper in (data.get("dex_symbols") or [])):
        sources.append("dexscreener")

    bonus = 0
    if "dexscreener" in sources:
        bonus += 5
    if "coingecko" in sources:
        bonus += 3

    return {
        "trending": bool(sources),
        "sources":  sources,
        "bonus":    bonus,
    }
