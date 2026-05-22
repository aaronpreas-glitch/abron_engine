"""
GeckoTerminal trending pools — Solana tactical discovery source.

Provides activity-ranked Solana token candidates via the GeckoTerminal
public API (free, no key required).  Used as a supplementary source
alongside DexScreener when BirdEye is unavailable.

Endpoint: GET /api/v2/networks/solana/trending_pools?include=base_token
Rate limit: conservative 10 req/min cap (free tier).  At scan cadence
(900s) we issue ≤1 call per cycle — well within limits.
"""

from __future__ import annotations

import copy
import logging
import os
import threading
import time
from datetime import datetime

import requests

# ── Constants ────────────────────────────────────────────────────────────────

_GECKO_BASE = "https://api.geckoterminal.com/api/v2"
_DEFAULT_HEADERS = {"Accept": "application/json;version=20230302"}
_PROVIDER = "geckoterminal"
_COOLDOWN_SECONDS = int(os.getenv("GECKOTERMINAL_COOLDOWN_SECONDS", "900"))
_CACHE_TTL_SECONDS = int(os.getenv("GECKOTERMINAL_CACHE_TTL_SECONDS", "600"))
_CACHE: dict[str, tuple[float, object]] = {}

# Conservative rate limit — one call every 6 seconds maximum (10/min).
# In practice we call once per 900s scan cycle so this is never hit.
_RATE_MIN_GAP_SECONDS = 6.0
_rate_lock = threading.Lock()
_last_call_ts: float = 0.0

# Tokens to exclude from discovery (stables, wrapped natives)
_EXCLUDED_SYMBOLS = {
    "USDC", "USDT", "USDS", "USD1", "USDE", "DAI", "FDUSD", "PYUSD",
    "SOL", "WSOL", "WBTC", "WETH", "ETH", "BTC",
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _to_float(value, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _rate_limit_wait() -> None:
    global _last_call_ts
    with _rate_lock:
        elapsed = time.time() - _last_call_ts
        if elapsed < _RATE_MIN_GAP_SECONDS:
            time.sleep(_RATE_MIN_GAP_SECONDS - elapsed)
        _last_call_ts = time.time()


def _cache_get(key: str):
    cached = _CACHE.get(key)
    if not cached:
        return None
    ts, value = cached
    if time.monotonic() - ts > _CACHE_TTL_SECONDS:
        return None
    return copy.deepcopy(value)


def _cache_set(key: str, value) -> None:
    _CACHE[key] = (time.monotonic(), copy.deepcopy(value))
    if len(_CACHE) > 32:
        oldest = min(_CACHE, key=lambda k: _CACHE[k][0])
        _CACHE.pop(oldest, None)


def _provider_in_cooldown() -> bool:
    try:
        from utils.db import provider_in_cooldown  # type: ignore

        active, _state = provider_in_cooldown(_PROVIDER)
        return bool(active)
    except Exception:
        return False


def _mark_degraded(reason: str, detail: str | None = None) -> None:
    try:
        from utils.db import mark_provider_degraded  # type: ignore

        mark_provider_degraded(
            _PROVIDER,
            cooldown_seconds=max(300, _COOLDOWN_SECONDS),
            reason=reason,
            detail=detail or reason,
        )
    except Exception:
        pass


def _mark_active(detail: str) -> None:
    try:
        from utils.db import mark_provider_active  # type: ignore

        mark_provider_active(_PROVIDER, detail=detail)
    except Exception:
        pass


def _request_json(path: str, *, params: dict | None = None, cache_key: str, reason: str):
    cached = _cache_get(cache_key)
    if _provider_in_cooldown():
        return cached

    try:
        from utils.provider_budget import provider_budget_allow  # type: ignore

        budget = provider_budget_allow(_PROVIDER, lane=reason)
        if not budget.get("allowed"):
            return cached
    except Exception:
        pass

    _rate_limit_wait()
    endpoint = f"{_GECKO_BASE}{path}"
    try:
        resp = requests.get(
            endpoint,
            params=params or {},
            headers=_DEFAULT_HEADERS,
            timeout=15,
        )
        if resp.status_code == 429:
            _mark_degraded(reason, detail=f"{endpoint} {params or ''}".strip())
            logging.warning("GeckoTerminal provider cooldown triggered by 429 (%s)", reason)
            return cached
        resp.raise_for_status()
        data = resp.json()
        _cache_set(cache_key, data)
        _mark_active(f"{reason}_ok")
        return data
    except requests.exceptions.RequestException as exc:
        logging.warning("GeckoTerminal fetch failed: %s", exc)
        return cached
    except ValueError as exc:
        logging.warning("GeckoTerminal JSON parse error: %s", exc)
        return cached


# ── Public API ────────────────────────────────────────────────────────────────

def fetch_geckoterminal_trending(limit: int = 50) -> list:
    """
    Fetch Solana trending pools from GeckoTerminal and return a list of
    normalised token dicts compatible with the tactical pipeline.

    Fields populated:
      symbol, name, address (mint), pair_address (pool), price,
      liquidity (reserve_in_usd), volume_24h/1h/6h/5m, change_24h/1h/6h,
      market_cap, fdv, txns_h1, txns_h24, source="geckoterminal"

    Fields absent from GeckoTerminal (set to None/0 so filters skip them):
      holders, uniqueWallet*, boosts_active, social_links, website_links
    """
    data = _request_json(
        "/networks/solana/trending_pools",
        params={"include": "base_token", "page": 1},
        cache_key="trending_pools:page=1:base_token",
        reason="trending_pools_429",
    )
    if not isinstance(data, dict):
        return []

    # Build token metadata lookup from included array.
    # Each item id is "solana_<mint_address>".
    token_meta: dict = {}
    for item in (data.get("included") or []):
        if item.get("type") != "token":
            continue
        attrs = item.get("attributes") or {}
        item_id = item.get("id", "")
        # Prefer attributes.address; fall back to splitting the id
        address = attrs.get("address") or (
            item_id.split("_", 1)[1] if "_" in item_id else ""
        )
        if address:
            token_meta[item_id] = {
                "address": address,
                "symbol": str(attrs.get("symbol") or "UNKNOWN").upper(),
                "name": attrs.get("name") or "",
            }

    results = []
    for pool in (data.get("data") or []):
        attrs = pool.get("attributes") or {}
        rels = pool.get("relationships") or {}

        # Resolve base token
        base_ref = (rels.get("base_token") or {}).get("data") or {}
        base_id = base_ref.get("id", "")
        meta = token_meta.get(base_id)
        if not meta:
            # Graceful fallback: extract address from id directly
            addr = base_id.split("_", 1)[1] if "_" in base_id else ""
            if not addr:
                continue
            meta = {"address": addr, "symbol": "UNKNOWN", "name": ""}

        symbol = meta["symbol"]
        address = meta["address"]

        if not address:
            continue
        if symbol in _EXCLUDED_SYMBOLS:
            continue

        price_change = attrs.get("price_change_percentage") or {}
        volume_usd = attrs.get("volume_usd") or {}
        txns = attrs.get("transactions") or {}
        txns_h1 = txns.get("h1") or {}
        txns_h24 = txns.get("h24") or {}

        token = {
            "symbol": symbol,
            "name": meta["name"],
            "address": address,
            "pair_address": attrs.get("address"),   # pool address, not mint
            "price": _to_float(attrs.get("base_token_price_usd")),
            "liquidity": _to_float(attrs.get("reserve_in_usd")),
            "volume_24h": _to_float(volume_usd.get("h24")),
            "volume_6h": _to_float(volume_usd.get("h6")),
            "volume_1h": _to_float(volume_usd.get("h1")),
            "volume_5m": _to_float(volume_usd.get("m5")),
            "change_24h": _to_float(price_change.get("h24")),
            "change_6h": _to_float(price_change.get("h6")),
            "change_1h": _to_float(price_change.get("h1")),
            "market_cap": _to_float(attrs.get("market_cap_usd")) or None,
            "fdv": _to_float(attrs.get("fdv_usd")) or None,
            "txns_h1": int(
                _to_float(txns_h1.get("buys")) + _to_float(txns_h1.get("sells"))
            ),
            "txns_h24": int(
                _to_float(txns_h24.get("buys")) + _to_float(txns_h24.get("sells"))
            ),
            # Fields not available from GeckoTerminal — left as None so that
            # holder-based quality/exec filter conditions are skipped (not failed)
            "holders": None,
            "uniqueWallet1h": None,
            "uniqueWallet1hChangePercent": None,
            "uniqueWallet4hChangePercent": None,
            "boosts_active": 0,
            "social_links": 0,
            "website_links": 0,
            "source": "geckoterminal",
        }
        results.append(token)
        if len(results) >= limit:
            break

    logging.info(
        "GeckoTerminal trending: %d Solana candidates (of %d pools returned)",
        len(results),
        len(data.get("data") or []),
    )
    return results


def fetch_geckoterminal_new_pools(*, pages: tuple[int, ...] = (1, 2), limit: int = 40) -> list[dict]:
    """
    Fetch recently-created Solana pools through the same budgeted GeckoTerminal
    gateway used by the trending client.
    """
    results: list[dict] = []
    seen: set[str] = set()

    for page in pages:
        data = _request_json(
            "/networks/solana/new_pools",
            params={"page": int(page)},
            cache_key=f"new_pools:page={int(page)}",
            reason=f"new_pools_page_{int(page)}_429",
        )
        if not isinstance(data, dict):
            continue
        for pool in data.get("data") or []:
            try:
                attrs = pool.get("attributes") or {}
                rels = pool.get("relationships") or {}
                token_id = ((rels.get("base_token") or {}).get("data") or {}).get("id", "")
                if not str(token_id).startswith("solana_"):
                    continue
                mint = str(token_id)[7:].strip()
                if not mint or mint in seen:
                    continue

                pool_name = str(attrs.get("name") or "")
                symbol = pool_name.split(" / ")[0].strip() if " / " in pool_name else ""
                if not symbol or symbol.upper() in _EXCLUDED_SYMBOLS:
                    continue

                created_ms = None
                created_str = attrs.get("pool_created_at")
                if created_str:
                    try:
                        created_ms = int(datetime.fromisoformat(str(created_str).replace("Z", "+00:00")).timestamp() * 1000)
                    except Exception:
                        pass

                results.append(
                    {
                        "baseToken": {"address": mint, "symbol": symbol},
                        "liquidity": {"usd": _to_float(attrs.get("reserve_in_usd"))},
                        "volume": {"h24": _to_float((attrs.get("volume_usd") or {}).get("h24"))},
                        "fdv": _to_float(attrs.get("fdv_usd")),
                        "pairCreatedAt": created_ms,
                        "chainId": "solana",
                        "source": "geckoterminal_new_pools",
                    }
                )
                seen.add(mint)
                if len(results) >= limit:
                    return results
            except Exception:
                continue

    logging.debug("GeckoTerminal new_pools: %d Solana pools fetched", len(results))
    return results
