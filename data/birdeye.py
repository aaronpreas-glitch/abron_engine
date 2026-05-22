import logging
import os
import threading
import time
from collections import deque

import requests

from config import (
    BIRDEYE_API_KEY,
    BIRDEYE_API_URL,
    BIRDEYE_CHAIN,
    INDEPENDENT_SOURCE_MODE,
    MAX_TOKENS_PER_SCAN,
    MIN_LIQUIDITY,
    MIN_VOLUME_24H,
    NO_BIRDEYE_MODE,
)

EXCLUDED_SYMBOLS = {
    "USDC", "USDT", "USDS", "USD1", "USDE", "DAI", "FDUSD", "PYUSD", "USDC.E",
    "SOL", "WSOL", "WBTC", "WETH", "ETH", "BTC",
}


# ── BirdEye API rate limiter ─────────────────────────────────────
# Premium Plus is 3,000 rpm / 50 rps for this account, but we still keep
# a conservative client-side limiter so one noisy loop does not burn the
# monthly CU budget or create bursts that trip provider-side protection.
_RATE_LIMIT_MAX_PER_MINUTE = int(os.getenv("BIRDEYE_RATE_LIMIT_PER_MINUTE", "600"))
_RATE_LIMIT_MIN_GAP_SECONDS = float(os.getenv("BIRDEYE_RATE_LIMIT_MIN_GAP_SECONDS", "0.1"))
_HTTP_429_BACKOFF_SECONDS = float(os.getenv("BIRDEYE_429_BACKOFF_SECONDS", "30"))
_COMPUTE_BACKOFF_SECONDS = float(os.getenv("BIRDEYE_COMPUTE_BACKOFF_SECONDS", "300"))
_AUTH_BACKOFF_SECONDS = float(os.getenv("BIRDEYE_AUTH_BACKOFF_SECONDS", "3600"))
_rate_lock = threading.Lock()
_rate_timestamps: deque[float] = deque()
_rate_429_backoff_until: float = 0.0
_independent_mode_logged = False


class _BirdEyeBackoffError(Exception):
    """Raised when BirdEye is in backoff — caller should skip and use fallback."""
    pass


def is_in_backoff() -> bool:
    """Return True if BirdEye compute-unit backoff is active."""
    with _rate_lock:
        return time.monotonic() < _rate_429_backoff_until


def independent_mode_enabled() -> bool:
    return bool(NO_BIRDEYE_MODE or INDEPENDENT_SOURCE_MODE)


def _mark_provider_disabled(endpoint: str = "independent_mode") -> None:
    try:
        from utils.db import mark_provider_disabled  # type: ignore
        mark_provider_disabled(
            "birdeye",
            reason="independent_mode",
            detail=f"BirdEye disabled intentionally; skipped {endpoint}.",
        )
    except Exception:
        pass


def _independent_skip(endpoint: str) -> bool:
    global _independent_mode_logged
    if not independent_mode_enabled():
        return False
    if not _independent_mode_logged:
        logging.info("BirdEye disabled intentionally by NO_BIRDEYE_MODE/INDEPENDENT_SOURCE_MODE.")
        _independent_mode_logged = True
    _mark_provider_disabled(endpoint)
    return True


def _provider_cooldown_active() -> bool:
    try:
        from utils.db import provider_in_cooldown  # type: ignore
        active, state = provider_in_cooldown("birdeye")
        if active:
            logging.debug(
                "BirdEye provider cooldown active (%s): %s",
                state.get("reason"),
                state.get("cooldown_until"),
            )
        return bool(active)
    except Exception:
        return False


def _rate_limit_wait():
    """Block until the next BirdEye API call is safe to make.
    Raises _BirdEyeBackoffError if compute-unit backoff is active (don't sleep, just skip)."""
    with _rate_lock:
        if _independent_skip("rate_limit_wait"):
            raise _BirdEyeBackoffError("BirdEye disabled by independent source mode")
        if _provider_cooldown_active():
            raise _BirdEyeBackoffError("BirdEye provider cooldown active")
        now = time.monotonic()

        # If in compute-unit backoff, raise immediately — don't sleep 1800s
        if now < _rate_429_backoff_until:
            wait = _rate_429_backoff_until - now
            raise _BirdEyeBackoffError(f"BirdEye in backoff for {wait:.0f}s more")

        # Purge timestamps older than 60s.
        cutoff = now - 60.0
        while _rate_timestamps and _rate_timestamps[0] < cutoff:
            _rate_timestamps.popleft()

        # If at capacity, wait until oldest call expires.
        if len(_rate_timestamps) >= _RATE_LIMIT_MAX_PER_MINUTE:
            wait = _rate_timestamps[0] - cutoff
            if wait > 0:
                logging.debug("BirdEye rate-limit window full: sleeping %.1fs", wait)
                time.sleep(wait)
                now = time.monotonic()

        # Enforce minimum gap between consecutive calls.
        if _rate_timestamps:
            gap = now - _rate_timestamps[-1]
            if gap < _RATE_LIMIT_MIN_GAP_SECONDS:
                sleep_for = _RATE_LIMIT_MIN_GAP_SECONDS - gap
                time.sleep(sleep_for)
                now = time.monotonic()

        _rate_timestamps.append(now)


def _record_429():
    """Record a 429 response to trigger backoff on subsequent calls."""
    global _rate_429_backoff_until
    with _rate_lock:
        _rate_429_backoff_until = time.monotonic() + _HTTP_429_BACKOFF_SECONDS
    logging.warning("BirdEye 429 received — backing off for %.0fs", _HTTP_429_BACKOFF_SECONDS)


def _record_compute_limit():
    """Record a compute-unit exhaustion (400) — back off briefly and retry later."""
    global _rate_429_backoff_until
    with _rate_lock:
        _rate_429_backoff_until = time.monotonic() + _COMPUTE_BACKOFF_SECONDS
    logging.warning(
        "BirdEye compute units exhausted — backing off for %.0fs, using DexScreener",
        _COMPUTE_BACKOFF_SECONDS,
    )


def _mark_provider_degraded(reason: str, detail: str | None = None, *, status: str = "DEGRADED") -> None:
    try:
        from utils.db import mark_provider_degraded  # type: ignore
        mark_provider_degraded(
            "birdeye",
            cooldown_seconds=int(_AUTH_BACKOFF_SECONDS if status == "ERROR" else _COMPUTE_BACKOFF_SECONDS),
            reason=reason,
            detail=detail or reason,
            status=status,
        )
    except Exception:
        pass


def _mark_provider_active(detail: str) -> None:
    try:
        from utils.db import mark_provider_active  # type: ignore
        mark_provider_active("birdeye", detail=detail)
    except Exception:
        pass


def _record_auth_error(endpoint: str) -> None:
    logging.error("BirdEye auth failed for %s — check BIRDEYE_API_KEY/env on the running service", endpoint)
    _mark_provider_degraded("auth_401", detail=endpoint, status="ERROR")


def _handle_provider_status(response, endpoint: str) -> bool:
    if response.status_code in (401, 403):
        _record_auth_error(endpoint)
        return True
    if response.status_code == 429:
        _record_429()
        _mark_provider_degraded("http_429", detail=endpoint)
        return True
    return False


# ── Helpers ───────────────────────────────────────────────────────

def _birdeye_headers():
    return {
        "X-API-KEY": BIRDEYE_API_KEY,
        "x-chain": BIRDEYE_CHAIN,
    }


def _to_float(value, default=0.0):
    try:
        if value is None or value == "":
            return default
        if isinstance(value, str):
            value = value.strip().replace("%", "")
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_int(value, default=0):
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _pick_first(item, keys):
    for key in keys:
        value = item.get(key)
        if value is not None and value != "":
            return value
    return None


def fetch_birdeye_market_data():
    """
    Fetch and normalize token market data from BirdEye.
    Returns token dicts compatible with the scoring + alert pipeline.
    """
    if _independent_skip("market_data"):
        return []
    if not BIRDEYE_API_KEY:
        logging.warning("BIRDEYE_API_KEY missing. Skipping BirdEye fetch.")
        _mark_provider_degraded("missing_api_key", detail="BIRDEYE_API_KEY missing", status="ERROR")
        return []

    try:
        _rate_limit_wait()
    except _BirdEyeBackoffError as e:
        logging.debug("BirdEye skipped (backoff active): %s", e)
        return []

    endpoint = f"{BIRDEYE_API_URL.rstrip('/')}/defi/tokenlist"
    headers = _birdeye_headers()
    params = {
        "sort_by": "v24hUSD",
        "sort_type": "desc",
        "offset": 0,
        # Fetch a larger page first so hard filters can still leave candidates.
        # BirdEye tokenlist currently rejects overly large limits (e.g. 100).
        "limit": min(50, max(20, MAX_TOKENS_PER_SCAN * 10)),
    }

    try:
        response = requests.get(endpoint, headers=headers, params=params, timeout=20)
        if _handle_provider_status(response, endpoint):
            return []
        # BirdEye returns 400 when compute unit quota is exhausted
        if response.status_code == 400:
            try:
                err_body = response.json()
                msg = str(err_body.get("message", "")).lower()
            except Exception:
                msg = ""
            if "compute unit" in msg or "limit exceeded" in msg:
                _record_compute_limit()
                return []
        response.raise_for_status()
        payload = response.json()
        _mark_provider_active("market_data_ok")
    except requests.exceptions.RequestException as exc:
        logging.error("BirdEye request failed: %s", exc)
        return []
    except ValueError as exc:
        logging.error("BirdEye JSON parse failed: %s", exc)
        return []

    data = payload.get("data", {})
    if isinstance(data, list):
        raw_tokens = data
    elif isinstance(data, dict):
        raw_tokens = data.get("tokens") or data.get("items") or []
    else:
        raw_tokens = []

    filtered = []
    fallback = []
    fallback_including_excluded = []
    excluded_count = 0

    for item in raw_tokens:
        symbol = (item.get("symbol") or item.get("name") or "UNKNOWN").upper()
        address = item.get("address") or item.get("mintAddress") or item.get("tokenAddress")
        if not address:
            continue
        is_excluded = symbol in EXCLUDED_SYMBOLS
        if is_excluded:
            excluded_count += 1

        liquidity = _to_float(
            _pick_first(item, ["liquidity", "liquidityUsd", "liquidity_usd"])
        )
        volume_24h = _to_float(
            _pick_first(item, ["v24hUSD", "volume24hUSD", "volume24h", "volume_24h"])
        )
        price = _to_float(_pick_first(item, ["price", "priceUsd", "price_usd"]))

        change_raw = _pick_first(
            item,
            [
                "priceChange24hPercent",
                "price24hChangePercent",
                "change24hPercent",
                "v24hChangePercent",
                "priceChange24h",
                "price_change_24h",
            ],
        )
        change_24h = _to_float(change_raw)
        # Some feeds encode percent as fraction (0.12 == 12%).
        if change_raw is not None and abs(change_24h) <= 1:
            change_24h *= 100.0
        change_1h = _to_float(
            _pick_first(
                item,
                ["priceChange1hPercent", "price1hChangePercent", "change1hPercent", "v1hChangePercent"],
            ),
            default=None,
        )
        change_6h = _to_float(
            _pick_first(
                item,
                ["priceChange6hPercent", "price6hChangePercent", "change6hPercent", "v6hChangePercent"],
            ),
            default=None,
        )
        holders = _to_int(item.get("holder") or item.get("holders"))
        last_trade_unix = _to_int(item.get("lastTradeUnixTime"), default=None)
        market_cap = _to_float(_pick_first(item, ["mc", "marketCap", "market_cap"]), default=None)
        fdv = _to_float(_pick_first(item, ["fdv", "fullyDilutedValuation"]), default=None)

        token = {
            "symbol": symbol,
            "address": address,
            "liquidity": liquidity,
            "volume_24h": volume_24h,
            "price": price,
            "change_24h": change_24h,
            "change_6h": change_6h,
            "change_1h": change_1h,
            "holders": holders if holders > 0 else "N/A",
            "last_trade_unix": last_trade_unix,
            "market_cap": market_cap,
            "fdv": fdv,
            "source": "birdeye",
        }

        # Keep broad fallback set for all non-excluded tokens.
        if not is_excluded:
            fallback.append(token)
        if abs(change_24h) > 0:
            fallback_including_excluded.append(token)

        if (
            not is_excluded
            and
            liquidity >= MIN_LIQUIDITY
            and volume_24h >= MIN_VOLUME_24H
        ):
            filtered.append(token)

    if filtered:
        filtered.sort(key=lambda t: (abs(t["change_24h"]), t["volume_24h"]), reverse=True)
        logging.info(
            "BirdEye candidates: raw=%d excluded=%d selected=%d",
            len(raw_tokens),
            excluded_count,
            len(filtered),
        )
        return filtered[:MAX_TOKENS_PER_SCAN]

    fallback.sort(key=lambda t: (abs(t["change_24h"]), t["volume_24h"]), reverse=True)
    if fallback:
        logging.info(
            "BirdEye fallback candidates: raw=%d excluded=%d selected=%d",
            len(raw_tokens),
            excluded_count,
            len(fallback),
        )
        return fallback[:MAX_TOKENS_PER_SCAN]

    fallback_including_excluded.sort(
        key=lambda t: (abs(t["change_24h"]), t["volume_24h"]), reverse=True
    )
    if fallback_including_excluded:
        logging.info(
            "BirdEye returned only excluded majors/stables. Using limited fallback set."
        )
        return fallback_including_excluded[:MAX_TOKENS_PER_SCAN]

    logging.warning(
        "BirdEye empty after normalization. raw=%d excluded=%d",
        len(raw_tokens),
        excluded_count,
    )
    return []


def fetch_birdeye_scanner_candidates(
    *,
    limit: int = 90,
    min_liquidity_usd: float = 5_000,
    min_volume_24h: float = 10_000,
    max_mcap: float = 100_000_000,
):
    """
    Fetch a broader BirdEye token universe for the memecoin scanner seed set.

    This is intentionally looser than fetch_birdeye_market_data():
    - it is used to discover candidate mints for the memecoin scanner
    - final safety/quality gating still happens downstream via DexScreener pair
      details + RugCheck + scanner scoring
    """
    if _independent_skip("scanner_candidates"):
        return []
    if not BIRDEYE_API_KEY:
        logging.warning("BIRDEYE_API_KEY missing. Skipping BirdEye scanner seed fetch.")
        _mark_provider_degraded("missing_api_key", detail="BIRDEYE_API_KEY missing", status="ERROR")
        return []

    try:
        _rate_limit_wait()
    except _BirdEyeBackoffError as e:
        logging.debug("BirdEye scanner seeds skipped (backoff active): %s", e)
        return []

    endpoint = f"{BIRDEYE_API_URL.rstrip('/')}/defi/tokenlist"
    headers = _birdeye_headers()
    params = {
        "sort_by": "v24hUSD",
        "sort_type": "desc",
        "offset": 0,
        # BirdEye tokenlist rejects larger limits on this endpoint/account shape.
        "limit": min(50, max(20, int(limit))),
    }

    try:
        response = requests.get(endpoint, headers=headers, params=params, timeout=20)
        if _handle_provider_status(response, endpoint):
            return []
        if response.status_code == 400:
            try:
                err_body = response.json()
                msg = str(err_body.get("message", "")).lower()
            except Exception:
                msg = ""
            if "compute unit" in msg or "limit exceeded" in msg:
                _record_compute_limit()
                return []
        response.raise_for_status()
        payload = response.json()
        _mark_provider_active("scanner_seed_ok")
    except requests.exceptions.RequestException as exc:
        logging.error("BirdEye scanner seed request failed: %s", exc)
        return []
    except ValueError as exc:
        logging.error("BirdEye scanner seed JSON parse failed: %s", exc)
        return []

    data = payload.get("data", {})
    if isinstance(data, list):
        raw_tokens = data
    elif isinstance(data, dict):
        raw_tokens = data.get("tokens") or data.get("items") or []
    else:
        raw_tokens = []

    selected = []
    seen = set()
    excluded_count = 0
    min_liquidity_usd = float(min_liquidity_usd or 0.0)
    min_volume_24h = float(min_volume_24h or 0.0)
    max_mcap = float(max_mcap or 0.0)

    for item in raw_tokens:
        symbol = (item.get("symbol") or item.get("name") or "UNKNOWN").upper()
        address = item.get("address") or item.get("mintAddress") or item.get("tokenAddress")
        if not address or address in seen:
            continue
        if symbol in EXCLUDED_SYMBOLS:
            excluded_count += 1
            continue

        liquidity = _to_float(
            _pick_first(item, ["liquidity", "liquidityUsd", "liquidity_usd"])
        )
        volume_24h = _to_float(
            _pick_first(item, ["v24hUSD", "volume24hUSD", "volume24h", "volume_24h"])
        )
        price = _to_float(_pick_first(item, ["price", "priceUsd", "price_usd"]))
        change_24h = _to_float(
            _pick_first(
                item,
                [
                    "priceChange24hPercent",
                    "price24hChangePercent",
                    "change24hPercent",
                    "v24hChangePercent",
                    "priceChange24h",
                    "price_change_24h",
                ],
            )
        )
        if abs(change_24h) <= 1 and change_24h != 0:
            change_24h *= 100.0
        market_cap = _to_float(_pick_first(item, ["mc", "marketCap", "market_cap"]), default=None)
        fdv = _to_float(_pick_first(item, ["fdv", "fullyDilutedValuation"]), default=None)
        effective_mcap = market_cap or fdv or 0.0

        if price <= 0:
            continue
        if liquidity < min_liquidity_usd or volume_24h < min_volume_24h:
            continue
        if max_mcap > 0 and effective_mcap > max_mcap:
            continue

        seen.add(address)
        selected.append({
            "symbol": symbol,
            "address": address,
            "liquidity": liquidity,
            "volume_24h": volume_24h,
            "price": price,
            "change_24h": change_24h,
            "market_cap": market_cap,
            "fdv": fdv,
            "source": "birdeye_scanner_seed",
        })

    selected.sort(
        key=lambda t: (t["volume_24h"], abs(t["change_24h"])),
        reverse=True,
    )
    logging.info(
        "BirdEye scanner seeds: raw=%d excluded=%d selected=%d",
        len(raw_tokens),
        excluded_count,
        len(selected),
    )
    return selected[: max(1, int(limit))]


def fetch_birdeye_trending_candidates(*, limit: int = 10, sort_by: str = "rank") -> list[dict]:
    """
    Fetch Birdeye trending tokens for Solana.
    Useful as a cleaner seed universe than the generic tokenlist feed.
    """
    if _independent_skip("trending_candidates"):
        return []
    if not BIRDEYE_API_KEY:
        return []

    try:
        _rate_limit_wait()
    except _BirdEyeBackoffError as e:
        logging.debug("BirdEye trending skipped (backoff active): %s", e)
        return []

    endpoint = f"{BIRDEYE_API_URL.rstrip('/')}/defi/token_trending"
    params = {
        "sort_by": sort_by,
        "sort_type": "asc" if sort_by == "rank" else "desc",
        "offset": 0,
        "limit": min(20, max(5, int(limit))),
    }
    try:
        response = requests.get(endpoint, headers=_birdeye_headers(), params=params, timeout=15)
        if _handle_provider_status(response, endpoint):
            return []
        response.raise_for_status()
        payload = response.json()
        _mark_provider_active("trending_ok")
    except (requests.exceptions.RequestException, ValueError) as exc:
        logging.debug("BirdEye trending request failed: %s", exc)
        return []

    data = payload.get("data", {})
    raw_tokens = data.get("tokens") or []
    results = []
    seen = set()
    for item in raw_tokens:
        symbol = (item.get("symbol") or item.get("name") or "UNKNOWN").upper()
        address = item.get("address") or item.get("mintAddress") or item.get("tokenAddress")
        if not address or address in seen or symbol in EXCLUDED_SYMBOLS:
            continue
        seen.add(address)
        results.append({
            "symbol": symbol,
            "address": address,
            "liquidity": _to_float(_pick_first(item, ["liquidity", "liquidityUsd", "liquidity_usd"])),
            "volume_24h": _to_float(_pick_first(item, ["volume24hUSD", "v24hUSD", "volume24h", "volume_24h"])),
            "price": _to_float(_pick_first(item, ["price", "priceUsd", "price_usd"]), default=0.0),
            "change_24h": 0.0,
            "market_cap": _to_float(_pick_first(item, ["mc", "marketCap", "market_cap"]), default=0.0),
            "fdv": _to_float(_pick_first(item, ["fdv", "fullyDilutedValuation"]), default=0.0),
            "source": "birdeye_trending",
            "rank": _to_int(item.get("rank"), default=None),
        })
    logging.info("BirdEye trending seeds: selected=%d", len(results))
    return results


def fetch_birdeye_smart_money_candidates(*, limit: int = 10, interval: str = "1d", trader_style: str = "all") -> list[dict]:
    """
    Fetch Birdeye smart-money token list for Solana.
    This adds a different premium signal source to the scanner seed universe.
    """
    if _independent_skip("smart_money_candidates"):
        return []
    if not BIRDEYE_API_KEY:
        return []

    try:
        _rate_limit_wait()
    except _BirdEyeBackoffError as e:
        logging.debug("BirdEye smart-money skipped (backoff active): %s", e)
        return []

    endpoint = f"{BIRDEYE_API_URL.rstrip('/')}/smart-money/v1/token/list"
    params = {
        "interval": interval,
        "trader_style": trader_style,
        "sort_by": "smart_traders_no",
        "sort_type": "desc",
        "offset": 0,
        "limit": min(20, max(5, int(limit))),
    }
    try:
        response = requests.get(endpoint, headers=_birdeye_headers(), params=params, timeout=15)
        if _handle_provider_status(response, endpoint):
            return []
        response.raise_for_status()
        payload = response.json()
        _mark_provider_active("smart_money_ok")
    except (requests.exceptions.RequestException, ValueError) as exc:
        logging.debug("BirdEye smart-money request failed: %s", exc)
        return []

    data = payload.get("data", payload)
    raw_tokens = []
    if isinstance(data, dict):
        raw_tokens = data.get("items") or data.get("tokens") or data.get("list") or []
    elif isinstance(data, list):
        raw_tokens = data

    results = []
    seen = set()
    for item in raw_tokens:
        symbol = (item.get("symbol") or item.get("name") or "UNKNOWN").upper()
        address = item.get("address") or item.get("mintAddress") or item.get("tokenAddress") or item.get("token")
        if not address or address in seen or symbol in EXCLUDED_SYMBOLS:
            continue
        seen.add(address)
        results.append({
            "symbol": symbol,
            "address": address,
            "liquidity": _to_float(_pick_first(item, ["liquidity", "liquidityUsd", "liquidity_usd"])),
            "volume_24h": _to_float(_pick_first(item, ["volume_usd", "volume24hUSD", "v24hUSD", "volume24h", "volume_24h"])),
            "price": _to_float(_pick_first(item, ["price", "priceUsd", "price_usd"]), default=0.0),
            "change_24h": _to_float(_pick_first(item, ["price_change_percent", "priceChange24hPercent", "change24hPercent", "priceChange24h"]), default=0.0),
            "market_cap": _to_float(_pick_first(item, ["mc", "marketCap", "market_cap"]), default=0.0),
            "fdv": _to_float(_pick_first(item, ["fdv", "fullyDilutedValuation"]), default=0.0),
            "source": "birdeye_smart_money",
            "smart_traders_no": _to_int(item.get("smart_traders_no"), default=0),
            "net_flow": _to_float(item.get("net_flow"), default=0.0),
        })
    logging.info("BirdEye smart-money seeds: selected=%d", len(results))
    return results


def fetch_birdeye_price(address):
    """
    Fetch current token price from BirdEye by token address/mint.
    Returns float price or None.
    """
    if _independent_skip("price"):
        return None
    if not BIRDEYE_API_KEY or not address:
        return None

    try:
        _rate_limit_wait()
    except _BirdEyeBackoffError as e:
        logging.debug("BirdEye price fetch skipped (backoff active): %s", e)
        return None

    endpoint = f"{BIRDEYE_API_URL.rstrip('/')}/defi/price"
    try:
        response = requests.get(
            endpoint,
            headers=_birdeye_headers(),
            params={"address": address},
            timeout=15,
        )
        if _handle_provider_status(response, endpoint):
            return None
        response.raise_for_status()
        payload = response.json()
        _mark_provider_active("price_ok")
    except (requests.exceptions.RequestException, ValueError):
        return None

    # Handle multiple payload shapes defensively.
    data = payload.get("data", payload)
    if isinstance(data, dict):
        price = _pick_first(data, ["value", "price", "priceUsd", "price_usd"])
        return _to_float(price, default=None)
    return None


def fetch_birdeye_token_overview(address):
    """
    Fetch comprehensive token overview from BirdEye including unique wallet metrics.
    Returns dict with enrichment data or empty dict on failure.
    """
    if _independent_skip("token_overview"):
        return {}
    if not BIRDEYE_API_KEY or not address:
        return {}

    try:
        _rate_limit_wait()
    except _BirdEyeBackoffError as e:
        logging.debug("BirdEye token_overview skipped (backoff active): %s", e)
        return {}

    endpoint = f"{BIRDEYE_API_URL.rstrip('/')}/defi/token_overview"
    try:
        response = requests.get(
            endpoint,
            headers=_birdeye_headers(),
            params={"address": address},
            timeout=15,
        )
        if _handle_provider_status(response, endpoint):
            return {}
        if response.status_code == 400:
            try:
                err_msg = str(response.json().get("message", "")).lower()
            except Exception:
                err_msg = ""
            if "compute unit" in err_msg or "limit exceeded" in err_msg:
                _record_compute_limit()
                return {}
        response.raise_for_status()
        payload = response.json()
        _mark_provider_active("token_overview_ok")
    except (requests.exceptions.RequestException, ValueError):
        return {}

    data = payload.get("data", {})
    if not isinstance(data, dict):
        return {}

    # Extract unique wallet metrics
    enrichment = {}

    # Unique wallet counts at various timeframes
    enrichment["uniqueWallet1m"] = _to_int(data.get("uniqueWallet1m"), default=None)
    enrichment["uniqueWallet5m"] = _to_int(data.get("uniqueWallet5m"), default=None)
    enrichment["uniqueWallet30m"] = _to_int(data.get("uniqueWallet30m"), default=None)
    enrichment["uniqueWallet1h"] = _to_int(data.get("uniqueWallet1h"), default=None)
    enrichment["uniqueWallet2h"] = _to_int(data.get("uniqueWallet2h"), default=None)
    enrichment["uniqueWallet4h"] = _to_int(data.get("uniqueWallet4h"), default=None)
    enrichment["uniqueWallet8h"] = _to_int(data.get("uniqueWallet8h"), default=None)
    enrichment["uniqueWallet24h"] = _to_int(data.get("uniqueWallet24h"), default=None)

    # Unique wallet change percentages
    enrichment["uniqueWallet1hChangePercent"] = _to_float(data.get("uniqueWallet1hChangePercent"), default=None)
    enrichment["uniqueWallet2hChangePercent"] = _to_float(data.get("uniqueWallet2hChangePercent"), default=None)
    enrichment["uniqueWallet4hChangePercent"] = _to_float(data.get("uniqueWallet4hChangePercent"), default=None)
    enrichment["uniqueWallet8hChangePercent"] = _to_float(data.get("uniqueWallet8hChangePercent"), default=None)
    enrichment["uniqueWallet24hChangePercent"] = _to_float(data.get("uniqueWallet24hChangePercent"), default=None)

    # Additional price change metrics
    enrichment["priceChange1hPercent"] = _to_float(data.get("priceChange1hPercent"), default=None)
    enrichment["priceChange2hPercent"] = _to_float(data.get("priceChange2hPercent"), default=None)
    enrichment["priceChange4hPercent"] = _to_float(data.get("priceChange4hPercent"), default=None)
    enrichment["priceChange8hPercent"] = _to_float(data.get("priceChange8hPercent"), default=None)
    enrichment["priceChange24hPercent"] = _to_float(data.get("priceChange24hPercent"), default=None)

    # Social links and metadata
    enrichment["twitter"] = data.get("twitter") or data.get("extensions", {}).get("twitter")
    enrichment["website"] = data.get("website") or data.get("extensions", {}).get("website")
    enrichment["coingeckoId"] = data.get("coingeckoId")
    enrichment["logoURI"] = data.get("logoURI")

    # Count social/website links
    extensions = data.get("extensions", {})
    social_links = sum([
        1 for k in ["twitter", "telegram", "discord", "reddit"]
        if extensions.get(k)
    ])
    website_links = sum([
        1 for k in ["website", "blog", "medium"]
        if extensions.get(k)
    ])
    enrichment["social_links"] = social_links
    enrichment["website_links"] = website_links

    # Transaction counts
    enrichment["txns_h1"] = _to_int(data.get("trade1h"), default=None)
    enrichment["txns_h24"] = _to_int(data.get("trade24h"), default=None)

    return enrichment


def fetch_birdeye_token_trades(
    address: str,
    *,
    limit: int = 40,
    tx_type: str = "swap",
    sort_by: str = "block_unix_time",
    sort_type: str = "desc",
) -> list[dict]:
    """
    Fetch recent token transactions from BirdEye v3.
    We use these rows to build wallet participation snapshots for active mints.
    """
    if _independent_skip("token_trades"):
        return []
    if not BIRDEYE_API_KEY or not address:
        return []

    try:
        _rate_limit_wait()
    except _BirdEyeBackoffError as e:
        logging.debug("BirdEye token trades skipped (backoff active): %s", e)
        return []

    endpoint = f"{BIRDEYE_API_URL.rstrip('/')}/defi/v3/token/txs"
    params = {
        "address": address,
        "limit": min(60, max(10, int(limit))),
        "tx_type": tx_type,
        "sort_by": sort_by,
        "sort_type": sort_type,
    }
    try:
        response = requests.get(
            endpoint,
            headers=_birdeye_headers(),
            params=params,
            timeout=20,
        )
        if _handle_provider_status(response, endpoint):
            return []
        if response.status_code == 400:
            try:
                err_msg = str(response.json().get("message", "")).lower()
            except Exception:
                err_msg = ""
            if "compute unit" in err_msg or "limit exceeded" in err_msg:
                _record_compute_limit()
                return []
        response.raise_for_status()
        payload = response.json()
        _mark_provider_active("token_trades_ok")
    except (requests.exceptions.RequestException, ValueError) as exc:
        logging.debug("BirdEye token trades request failed: %s", exc)
        return []

    data = payload.get("data", payload)
    if isinstance(data, dict):
        items = data.get("items") or []
    elif isinstance(data, list):
        items = data
    else:
        items = []

    results: list[dict] = []
    for item in items:
        owner = str(item.get("owner") or "").strip()
        tx_hash = str(item.get("tx_hash") or "").strip()
        block_unix_time = _to_int(item.get("block_unix_time"), default=None)
        if not owner or not tx_hash or not block_unix_time:
            continue
        results.append(
            {
                "wallet_address": owner,
                "tx_hash": tx_hash,
                "side": str(item.get("side") or item.get("tx_type") or "").lower() or None,
                "source": str(item.get("source") or "").strip() or None,
                "volume_usd": _to_float(item.get("volume_usd"), default=0.0),
                "block_unix_time": int(block_unix_time),
                "block_number": _to_int(item.get("block_number"), default=None),
                "tx_type": str(item.get("tx_type") or "").strip() or None,
                "alias": str(item.get("alias") or "").strip() or None,
            }
        )
    return results


def fetch_birdeye_ohlcv(address, candle_type="15m", lookback_hours=36):
    """
    Fetch OHLCV candles from BirdEye for a token address.
    Returns normalized candle list sorted by unixTime ascending.
    """
    if _independent_skip("ohlcv"):
        return []
    if not BIRDEYE_API_KEY or not address:
        return []

    try:
        _rate_limit_wait()
    except _BirdEyeBackoffError as e:
        logging.debug("BirdEye OHLCV skipped (backoff active): %s", e)
        return []

    endpoint = f"{BIRDEYE_API_URL.rstrip('/')}/defi/ohlcv"
    now = int(time.time())
    lookback_seconds = max(1, int(lookback_hours)) * 3600
    params = {
        "address": address,
        "type": candle_type,
        "time_from": max(0, now - lookback_seconds),
        "time_to": now,
    }

    try:
        response = requests.get(
            endpoint,
            headers=_birdeye_headers(),
            params=params,
            timeout=20,
        )
        if _handle_provider_status(response, endpoint):
            return []
        response.raise_for_status()
        payload = response.json()
        _mark_provider_active("ohlcv_ok")
    except (requests.exceptions.RequestException, ValueError):
        return []

    data = payload.get("data", payload)
    if isinstance(data, dict):
        items = data.get("items") or data.get("candles") or []
    elif isinstance(data, list):
        items = data
    else:
        items = []

    candles = []
    for item in items:
        ts = _to_int(_pick_first(item, ["unixTime", "time", "timestamp"]), default=None)
        o = _to_float(item.get("o"), default=None)
        h = _to_float(item.get("h"), default=None)
        l = _to_float(item.get("l"), default=None)
        c = _to_float(item.get("c"), default=None)
        v = _to_float(item.get("v"), default=None)
        if ts is None or c is None:
            continue
        candles.append({
            "unixTime": ts,
            "o": o,
            "h": h,
            "l": l,
            "c": c,
            "v": v if v is not None else 0.0,
        })

    candles.sort(key=lambda x: x["unixTime"])
    return candles
