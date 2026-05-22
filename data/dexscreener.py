from __future__ import annotations

import copy
import os
import time

import requests

from config import (
    DEXSCREENER_CHAIN_ID,
    DEXSCREENER_API_URL,
    DEXSCREENER_PAIRS_PER_QUERY,
    DEXSCREENER_SEARCH_QUERIES,
    MAX_TOKENS_PER_SCAN,
    NEW_RUNNER_PROFILE_LIMIT,
    NEW_RUNNER_PROFILE_SAMPLE,
    NEW_RUNNER_USE_LATEST_PROFILES,
    SOL_PROXY_MINT,
)

_STABLE_SYMBOLS = {"USDC", "USDT", "USDS", "USD1", "DAI", "FDUSD", "PYUSD"}
_SOL_SYMBOLS = {"SOL", "WSOL"}
_PROVIDER = "dexscreener"
_COOLDOWN_SECONDS = int(os.getenv("DEXSCREENER_COOLDOWN_SECONDS", "900"))
_CACHE_TTL_SECONDS = int(os.getenv("DEXSCREENER_CACHE_TTL_SECONDS", "600"))
_CACHE: dict[str, tuple[float, object]] = {}


def _to_float(value, default=0.0):
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _base_url():
    return DEXSCREENER_API_URL or "https://api.dexscreener.com"


def _cache_get(key: str):
    cached = _CACHE.get(key)
    if not cached:
        return None
    ts, value = cached
    if time.monotonic() - ts > _CACHE_TTL_SECONDS:
        return None
    return copy.deepcopy(value)


def _cache_set(key: str, value):
    _CACHE[key] = (time.monotonic(), copy.deepcopy(value))
    if len(_CACHE) > 64:
        oldest = min(_CACHE, key=lambda k: _CACHE[k][0])
        _CACHE.pop(oldest, None)


def _provider_in_cooldown(provider: str = _PROVIDER) -> bool:
    try:
        from utils.db import provider_in_cooldown  # type: ignore
        active, _state = provider_in_cooldown(provider)
        return bool(active)
    except Exception:
        return False


def _mark_degraded(reason: str, detail: str | None = None, provider: str = _PROVIDER) -> None:
    try:
        from utils.db import mark_provider_degraded  # type: ignore
        mark_provider_degraded(
            provider,
            cooldown_seconds=max(300, _COOLDOWN_SECONDS),
            reason=reason,
            detail=detail or reason,
        )
    except Exception:
        pass


def _mark_active(detail: str, provider: str = _PROVIDER) -> None:
    try:
        from utils.db import mark_provider_active  # type: ignore
        mark_provider_active(provider, detail=detail)
    except Exception:
        pass


def _request_json(
    endpoint: str,
    *,
    params: dict | None = None,
    cache_key: str,
    reason: str,
    provider: str = _PROVIDER,
    reserve: bool = False,
):
    cached = _cache_get(cache_key)
    provider = str(provider or _PROVIDER).strip().lower()
    if _provider_in_cooldown(provider):
        return cached
    try:
        from utils.provider_budget import provider_budget_allow  # type: ignore
        budget = provider_budget_allow(provider, lane=reason, reserve=reserve)
        if not budget.get("allowed"):
            return cached
    except Exception:
        pass
    try:
        response = requests.get(
            endpoint,
            params=params,
            timeout=15,
            headers={"User-Agent": "memecoin-engine/1.0"},
        )
        if response.status_code == 429:
            _mark_degraded(reason, detail=f"{endpoint} {params or ''}".strip(), provider=provider)
            return cached
        response.raise_for_status()
        payload = response.json() or {}
        _cache_set(cache_key, payload)
        _mark_active(f"{reason}_ok", provider=provider)
        return payload
    except requests.exceptions.RequestException:
        return cached
    except ValueError:
        return cached


def fetch_search_pairs(query: str, *, reason: str = "search_429", provider: str = _PROVIDER, reserve: bool = False) -> list[dict]:
    """
    Budgeted DexScreener search helper for resolver/scanner paths that need raw pairs.
    """
    query = str(query or "").strip()
    if not query:
        return []
    endpoint = f"{_base_url().rstrip('/')}/latest/dex/search"
    data = _request_json(
        endpoint,
        params={"q": query},
        cache_key=f"{provider}:search_pairs:{query}",
        reason=reason,
        provider=provider,
        reserve=reserve,
    )
    if not data:
        return []
    pairs = data.get("pairs") or []
    return [p for p in pairs if isinstance(p, dict)]


def fetch_token_pairs(addresses, *, reason: str = "token_pairs_429", provider: str = _PROVIDER, reserve: bool = False) -> list[dict]:
    """
    Budgeted raw pair fetch for one mint or a comma/list batch of mints.
    """
    if isinstance(addresses, (list, tuple, set)):
        address = ",".join(str(x).strip() for x in addresses if str(x or "").strip())
    else:
        address = str(addresses or "").strip()
    if not address:
        return []
    endpoint = f"{_base_url().rstrip('/')}/latest/dex/tokens/{address}"
    data = _request_json(
        endpoint,
        cache_key=f"{provider}:token_pairs:{address}",
        reason=reason,
        provider=provider,
        reserve=reserve,
    )
    if not data:
        return []
    pairs = data.get("pairs") or []
    return [p for p in pairs if isinstance(p, dict)]


def fetch_latest_profiles(*, reason: str = "profiles_latest_429", provider: str = _PROVIDER) -> list[dict]:
    endpoint = f"{_base_url().rstrip('/')}/token-profiles/latest/v1"
    data = _request_json(
        endpoint,
        cache_key=f"{provider}:token_profiles_latest",
        reason=reason,
        provider=provider,
    )
    return data if isinstance(data, list) else []


def fetch_token_boosts(kind: str = "top", *, reason: str | None = None, provider: str = _PROVIDER) -> list[dict]:
    clean_kind = str(kind or "top").strip().lower()
    if clean_kind not in {"top", "latest"}:
        clean_kind = "top"
    endpoint = f"{_base_url().rstrip('/')}/token-boosts/{clean_kind}/v1"
    data = _request_json(
        endpoint,
        cache_key=f"{provider}:token_boosts:{clean_kind}",
        reason=reason or f"boosts_{clean_kind}_429",
        provider=provider,
    )
    return data if isinstance(data, list) else []


def _is_chain_match(pair):
    chain_id = str((pair or {}).get("chainId") or "").strip().lower()
    if DEXSCREENER_CHAIN_ID and chain_id and chain_id != DEXSCREENER_CHAIN_ID:
        return False
    return True


def _normalize_pair(pair):
    pair = pair or {}
    if not _is_chain_match(pair):
        return None

    base_token = pair.get("baseToken", {}) or {}
    quote_token = pair.get("quoteToken", {}) or {}
    info = pair.get("info", {}) or {}
    txns = pair.get("txns", {}) or {}
    txns_h1 = txns.get("h1", {}) or {}
    txns_h24 = txns.get("h24", {}) or {}
    socials = info.get("socials") or []
    websites = info.get("websites") or []
    address = base_token.get("address")
    symbol = base_token.get("symbol", "UNKNOWN")
    if not address:
        return None

    return {
        "symbol": symbol,
        "name": base_token.get("name") or symbol,
        "address": address,
        "pair_address": pair.get("pairAddress"),
        "quote_symbol": str(quote_token.get("symbol") or "").upper(),
        "liquidity": _to_float((pair.get("liquidity", {}) or {}).get("usd", 0)),
        "volume_24h": _to_float((pair.get("volume", {}) or {}).get("h24", 0)),
        "price": _to_float(pair.get("priceUsd", 0)),
        "change_24h": _to_float((pair.get("priceChange", {}) or {}).get("h24", 0)),
        "change_6h": _to_float((pair.get("priceChange", {}) or {}).get("h6", 0)),
        "change_1h": _to_float((pair.get("priceChange", {}) or {}).get("h1", 0)),
        "pair_created_at": _to_float(pair.get("pairCreatedAt"), default=None),
        "txns_h1": int(_to_float(txns_h1.get("buys"), 0) + _to_float(txns_h1.get("sells"), 0)),
        "txns_h24": int(_to_float(txns_h24.get("buys"), 0) + _to_float(txns_h24.get("sells"), 0)),
        "boosts_active": int(_to_float((pair.get("boosts", {}) or {}).get("active"), 0)),
        "social_links": len(socials) if isinstance(socials, list) else 0,
        "website_links": len(websites) if isinstance(websites, list) else 0,
        "market_cap": _to_float(pair.get("marketCap"), default=None),
        "fdv": _to_float(pair.get("fdv"), default=None),
        "source": "dexscreener",
    }


def _normalize_sol_proxy_pair(pair):
    pair = pair or {}
    if not _is_chain_match(pair):
        return None

    base_token = pair.get("baseToken", {}) or {}
    quote_token = pair.get("quoteToken", {}) or {}
    base_symbol = str(base_token.get("symbol") or "").upper()
    quote_symbol = str(quote_token.get("symbol") or "").upper()

    is_sol_stable = (
        (base_symbol in _SOL_SYMBOLS and quote_symbol in _STABLE_SYMBOLS)
        or (quote_symbol in _SOL_SYMBOLS and base_symbol in _STABLE_SYMBOLS)
    )
    is_sol_pair = base_symbol in _SOL_SYMBOLS or quote_symbol in _SOL_SYMBOLS
    if not (is_sol_stable or is_sol_pair):
        return None

    sol_token = base_token if base_symbol in _SOL_SYMBOLS else quote_token
    if not sol_token:
        return None

    return {
        "symbol": str(sol_token.get("symbol") or "SOL").upper(),
        "address": sol_token.get("address"),
        "pair_address": pair.get("pairAddress"),
        "liquidity": _to_float((pair.get("liquidity", {}) or {}).get("usd", 0)),
        "volume_24h": _to_float((pair.get("volume", {}) or {}).get("h24", 0)),
        "price": _to_float(pair.get("priceUsd", 0)),
        "change_24h": _to_float((pair.get("priceChange", {}) or {}).get("h24", 0)),
        "change_6h": _to_float((pair.get("priceChange", {}) or {}).get("h6", 0)),
        "change_1h": _to_float((pair.get("priceChange", {}) or {}).get("h1", 0)),
        "market_cap": _to_float(pair.get("marketCap"), default=None),
        "fdv": _to_float(pair.get("fdv"), default=None),
        "is_sol_stable": bool(is_sol_stable),
        "source": "dexscreener_sol_proxy",
    }


def fetch_token_snapshot(address):
    """
    Fetch live token snapshot from DexScreener for a specific token address.
    Chooses the best Solana pair by liquidity and then volume.
    """
    if not address:
        return None
    pairs = fetch_token_pairs(address, reason="token_snapshot_429")
    normalized = [p for p in (_normalize_pair(pair) for pair in pairs) if p]
    if not normalized:
        return None
    normalized.sort(key=lambda t: (t["liquidity"], t["volume_24h"]), reverse=True)
    return normalized[0]


def fetch_sol_market_proxy(query="SOL"):
    """
    Fetch a liquid SOL pair snapshot from DexScreener to use as market proxy.
    Uses SOL/stable pairs only to avoid distorted moves from SOL-quoted meme pairs.
    """
    pairs = []

    if SOL_PROXY_MINT:
        endpoint = f"{_base_url().rstrip('/')}/latest/dex/tokens/{SOL_PROXY_MINT}"
        data = _request_json(
            endpoint,
            cache_key=f"sol_proxy_token:{SOL_PROXY_MINT}",
            reason="sol_proxy_token_429",
        )
        if data:
            pairs = data.get("pairs", []) or []

    if not pairs:
        endpoint = f"{_base_url().rstrip('/')}/latest/dex/search"
        data = _request_json(
            endpoint,
            params={"q": query or "SOL"},
            cache_key=f"sol_proxy_search:{query or 'SOL'}",
            reason="sol_proxy_search_429",
        )
        if data:
            pairs = data.get("pairs", []) or []
        else:
            return None

    normalized = [p for p in (_normalize_sol_proxy_pair(pair) for pair in pairs) if p]
    if not normalized:
        return None

    stable_pairs = [p for p in normalized if p.get("is_sol_stable")]
    if not stable_pairs:
        return None

    stable_pairs.sort(key=lambda t: (t["liquidity"], t["volume_24h"]), reverse=True)
    return stable_pairs[0]


def fetch_market_data():
    """
    Fetch market data from DexScreener API based on a query (e.g., "SOL").
    The function assumes an API URL that provides token data in a structured format.
    """
    endpoint = f"{_base_url().rstrip('/')}/latest/dex/search"
    queries = DEXSCREENER_SEARCH_QUERIES or ["SOL"]
    unique_tokens = {}

    for query in queries:
        data = _request_json(
            endpoint,
            params={"q": query},
            cache_key=f"market_search:{query}",
            reason="market_search_429",
        )
        if not data:
            continue
        pairs = data.get("pairs", []) or []

        for pair in pairs[:max(1, DEXSCREENER_PAIRS_PER_QUERY)]:
            token = _normalize_pair(pair)
            if not token:
                continue

            existing = unique_tokens.get(token["address"])
            if not existing or token["volume_24h"] > existing["volume_24h"]:
                unique_tokens[token["address"]] = token

    tokens = list(unique_tokens.values())
    tokens.sort(key=lambda t: (t["volume_24h"], t["liquidity"]), reverse=True)
    return tokens[:max(1, MAX_TOKENS_PER_SCAN)]


def fetch_runner_watch_candidates(queries, pairs_per_query: int = 24, limit: int = 100):
    """
    Fetch broad DexScreener candidates intended for watchlist-style new-runner alerts.
    """
    endpoint = f"{_base_url().rstrip('/')}/latest/dex/search"
    unique_tokens = {}

    for query in (queries or ["SOL"]):
        data = _request_json(
            endpoint,
            params={"q": query},
            cache_key=f"runner_search:{query}",
            reason="runner_search_429",
        )
        if not data:
            continue
        pairs = data.get("pairs", []) or []
        for pair in pairs[:max(1, pairs_per_query)]:
            token = _normalize_pair(pair)
            if not token:
                continue

            existing = unique_tokens.get(token["address"])
            if not existing:
                unique_tokens[token["address"]] = token
                continue

            if (token.get("liquidity") or 0) > (existing.get("liquidity") or 0):
                unique_tokens[token["address"]] = token
                continue
            if (token.get("volume_24h") or 0) > (existing.get("volume_24h") or 0):
                unique_tokens[token["address"]] = token

    if NEW_RUNNER_USE_LATEST_PROFILES:
        profiles_endpoint = f"{_base_url().rstrip('/')}/token-profiles/latest/v1"
        profiles = _request_json(
            profiles_endpoint,
            cache_key="runner_profiles_latest",
            reason="runner_profiles_429",
        ) or []
        if not isinstance(profiles, list):
            profiles = []

        picked = []
        for p in profiles[:max(1, NEW_RUNNER_PROFILE_LIMIT)]:
            if str(p.get("chainId") or "").strip().lower() != DEXSCREENER_CHAIN_ID:
                continue
            token_address = p.get("tokenAddress")
            if not token_address:
                continue
            picked.append(p)
            if len(picked) >= max(1, NEW_RUNNER_PROFILE_SAMPLE):
                break

        profile_by_address = {
            str(profile.get("tokenAddress") or "").strip(): profile
            for profile in picked
            if str(profile.get("tokenAddress") or "").strip()
        }
        profile_snapshots: dict[str, dict] = {}
        if profile_by_address:
            pairs = fetch_token_pairs(
                list(profile_by_address.keys()),
                reason="runner_profile_batch_429",
            )
            for pair in pairs:
                snapshot = _normalize_pair(pair)
                if not snapshot:
                    continue
                address = str(snapshot.get("address") or "").strip()
                existing = profile_snapshots.get(address)
                if not existing or (
                    (snapshot.get("liquidity") or 0),
                    (snapshot.get("volume_24h") or 0),
                ) > (
                    (existing.get("liquidity") or 0),
                    (existing.get("volume_24h") or 0),
                ):
                    profile_snapshots[address] = snapshot

        for token_address, profile in profile_by_address.items():
            snapshot = profile_snapshots.get(token_address)
            if not snapshot:
                continue
            links = profile.get("links") or []
            if isinstance(links, list):
                social_count = len([x for x in links if isinstance(x, dict) and x.get("type") in {"twitter", "telegram", "discord"}])
                website_count = len([x for x in links if isinstance(x, dict) and x.get("type") == "website"])
            else:
                social_count = 0
                website_count = 0

            snapshot["description"] = profile.get("description") or ""
            snapshot["social_links"] = max(int(snapshot.get("social_links") or 0), social_count)
            snapshot["website_links"] = max(int(snapshot.get("website_links") or 0), website_count)
            snapshot["source"] = "dexscreener_profile+snapshot"

            existing = unique_tokens.get(snapshot["address"])
            if not existing:
                unique_tokens[snapshot["address"]] = snapshot
                continue
            if (snapshot.get("liquidity") or 0) > (existing.get("liquidity") or 0):
                unique_tokens[snapshot["address"]] = snapshot
                continue
            if (snapshot.get("volume_24h") or 0) > (existing.get("volume_24h") or 0):
                unique_tokens[snapshot["address"]] = snapshot

    tokens = list(unique_tokens.values())
    tokens.sort(
        key=lambda t: (
            t.get("volume_24h") or 0,
            t.get("liquidity") or 0,
            t.get("txns_h1") or 0,
        ),
        reverse=True,
    )
    return tokens[:max(1, limit)]


# Broad keyword set that covers the established Solana memecoin universe.
# DexScreener search returns up to ~30 pairs per query — these terms collectively
# surface hundreds of old/established tokens without a hardcoded whitelist.
_LEGACY_BROAD_QUERIES = [
    "SOL", "BONK", "WIF", "PEPE", "DOGE", "SHIB", "FLOKI",
    "POPCAT", "BOME", "MYRO", "NEIRO", "MOODENG", "PNUT", "GOAT",
    "MEW", "BRETT", "TURBO", "SLERF", "SAMO", "COPE", "JUP",
    "RENDER", "PYTH", "ORCA", "STEP", "MNGO", "CATS", "FETCH",
    "AI", "MEME", "CAT", "DOG", "FROG", "MONKEY", "TRUMP", "PONKE",
]


def fetch_legacy_recovery_candidates(
    queries=None,
    pairs_per_query: int = 10,
    limit: int = 300,
):
    """
    Fetch broad DexScreener candidates for the Legacy Recovery scanner.
    Uses a wide keyword sweep to find ALL established Solana tokens,
    not just a hardcoded 10-coin list.
    Falls back to _LEGACY_BROAD_QUERIES when no custom queries provided.
    """
    use_queries = queries if queries else _LEGACY_BROAD_QUERIES
    endpoint = f"{_base_url().rstrip('/')}/latest/dex/search"
    unique_tokens = {}

    for query in use_queries:
        data = _request_json(
            endpoint,
            params={"q": query},
            cache_key=f"legacy_search:{query}",
            reason="legacy_search_429",
        )
        if not data:
            continue
        pairs = data.get("pairs", []) or []
        for pair in pairs[:max(1, pairs_per_query)]:
            token = _normalize_pair(pair)
            if not token:
                continue
            addr = token["address"]
            existing = unique_tokens.get(addr)
            if not existing:
                unique_tokens[addr] = token
            elif (token.get("liquidity") or 0) > (existing.get("liquidity") or 0):
                unique_tokens[addr] = token

    tokens = list(unique_tokens.values())
    tokens.sort(
        key=lambda t: (t.get("liquidity") or 0, t.get("volume_24h") or 0),
        reverse=True,
    )
    return tokens[:max(1, limit)]
