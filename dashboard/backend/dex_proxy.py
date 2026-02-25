"""
dex_proxy.py — Fetches live data for watchlist tokens from DexScreener.

Parses WATCHLIST_ENTRIES from env, fetches each token by mint address,
classifies status (Momentum / Reclaim / Range / Breakdown / Volatile / Illiquid),
and returns normalized card data for the dashboard watchlist panel.
"""
from __future__ import annotations

import asyncio
import logging
import os
from functools import lru_cache
from typing import Any

import requests

log = logging.getLogger("dashboard.dex")

_DEX_BASE = "https://api.dexscreener.com"
_HEADERS = {"User-Agent": "memecoin-dashboard/1.0"}
_TIMEOUT = 8


def _parse_watchlist_entries() -> list[dict[str, str]]:
    """Parse WATCHLIST_ENTRIES from env: 'SYM:mint,SYM2:mint2,...'"""
    raw = os.getenv("WATCHLIST_ENTRIES", "")
    entries = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        if ":" in item:
            sym, addr = item.split(":", 1)
            entries.append({"symbol": sym.strip().upper(), "address": addr.strip()})
        elif item:
            entries.append({"symbol": "WATCH", "address": item})
    return entries


def _classify_status(row: dict) -> tuple[str, str]:
    """Return (status, reason) based on price action."""
    liq = float(row.get("liquidity") or 0)
    vol = float(row.get("volume_24h") or 0)
    ch1 = float(row.get("change_1h") or 0)
    ch6 = float(row.get("change_6h") or 0)
    ch24 = float(row.get("change_24h") or 0)
    vol_to_liq = vol / liq if liq > 0 else 0.0

    if liq < 50_000 or vol < 25_000:
        return "Illiquid", "Below minimum liquidity/volume floor."
    if ch24 >= 12 and ch6 >= 4 and ch1 >= 1 and vol_to_liq >= 0.35:
        return "Momentum", "Multi-timeframe strength with sustained volume."
    if ch24 >= 2 and ch6 <= -2 and ch1 >= 0.8 and vol_to_liq >= 0.20:
        return "Reclaim", "Short-term reclaim after pullback; buyers stepping in."
    if ch1 <= -2.5 and ch6 <= -6:
        return "Breakdown", "Downside pressure accelerating; structure weakening."
    if abs(ch1) <= 1.2 and abs(ch6) <= 4 and abs(ch24) <= 12:
        return "Range", "Range-bound conditions; no confirmed break."
    return "Volatile", "Mixed signals; momentum and mean-reversion conflict."


def _fetch_token(address: str) -> dict[str, Any] | None:
    """Fetch token pair data from DexScreener by mint address."""
    try:
        url = f"{_DEX_BASE}/latest/dex/tokens/{address}"
        resp = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        pairs = data.get("pairs") or []
        if not pairs:
            return None
        # Pick the pair with the most liquidity
        best = max(pairs, key=lambda p: float((p.get("liquidity") or {}).get("usd") or 0))
        liq = best.get("liquidity") or {}
        vol = best.get("volume") or {}
        price_change = best.get("priceChange") or {}
        txns = best.get("txns") or {}
        txns_h1 = (txns.get("h1") or {})
        txns_h1_count = int(txns_h1.get("buys", 0)) + int(txns_h1.get("sells", 0))
        mc = best.get("marketCap") or best.get("fdv")
        return {
            "price": float(best.get("priceUsd") or 0),
            "market_cap": float(mc) if mc else None,
            "fdv": float(best.get("fdv") or 0) or None,
            "liquidity": float(liq.get("usd") or 0),
            "volume_24h": float(vol.get("h24") or 0),
            "change_1h": float(price_change.get("h1") or 0),
            "change_6h": float(price_change.get("h6") or 0),
            "change_24h": float(price_change.get("h24") or 0),
            "txns_h1": txns_h1_count,
            "pair_address": best.get("pairAddress"),
            "dex_id": best.get("dexId"),
            "url": best.get("url"),
        }
    except Exception as exc:
        log.warning("DexScreener fetch failed for %s: %s", address, exc)
        return None


def get_watchlist_cards() -> list[dict[str, Any]]:
    """Fetch and classify all watchlist tokens. Called from API route."""
    entries = _parse_watchlist_entries()
    results = []
    for entry in entries:
        symbol = entry["symbol"]
        address = entry["address"]
        raw = _fetch_token(address)
        if raw is None:
            results.append({
                "symbol": symbol,
                "address": address,
                "status": "NoData",
                "reason": "Live data unavailable from DexScreener.",
                "has_live_data": False,
                "price": None,
                "market_cap": None,
                "liquidity": None,
                "volume_24h": None,
                "change_1h": None,
                "change_24h": None,
                "txns_h1": None,
            })
        else:
            status, reason = _classify_status(raw)
            liq = float(raw.get("liquidity") or 0)
            vol = float(raw.get("volume_24h") or 0)
            vol_to_liq = vol / liq if liq > 0 else 0.0
            if vol_to_liq >= 3.0:
                heat = "HOT"
            elif vol_to_liq >= 1.5:
                heat = "ACTIVE"
            elif vol_to_liq >= 0.5:
                heat = "MOVING"
            else:
                heat = "COLD"
            results.append({
                "symbol": symbol,
                "address": address,
                "status": status,
                "reason": reason,
                "has_live_data": True,
                "heat": heat,
                "vol_to_liq": round(vol_to_liq, 2),
                **raw,
            })
    return results


async def get_watchlist_cards_async() -> list[dict[str, Any]]:
    """Async wrapper so the FastAPI route doesn't block."""
    return await asyncio.to_thread(get_watchlist_cards)
