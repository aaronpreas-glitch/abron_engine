import logging
import json
from datetime import datetime, timezone

from data.birdeye import fetch_birdeye_market_data
from data.dexscreener import fetch_market_data as fetch_dexscreener_market_data
from data.geckoterminal import fetch_geckoterminal_trending
from config import INDEPENDENT_SOURCE_MODE, NO_BIRDEYE_MODE

# Maximum candidates to pass to the tactical pipeline in fallback mode.
# Larger than MAX_TOKENS_PER_SCAN intentionally — the quality/exec/tactical
# filters downstream decide what is actionable; we just supply the universe.
_FALLBACK_MERGED_LIMIT = 50


def _write_market_source_status(payload: dict) -> None:
    try:
        from utils.db import get_conn  # type: ignore
        with get_conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO kv_store (key, value) VALUES (?, ?)",
                (
                    "market_data_source_status",
                    json.dumps(payload, separators=(",", ":")),
                ),
            )
    except Exception:
        pass


def fetch_market_data():
    """
    Source priority:

    1. BirdEye  — preferred; broadest universe, richest fields (uniqueWallet,
                  holder counts, sorted by volume/momentum).  Returned as-is.

    2. GeckoTerminal trending + DexScreener merged  — used when BirdEye is
       unavailable.  GeckoTerminal provides ~20 activity-ranked Solana pools;
       DexScreener provides keyword-search results.  Merged by token address,
       deduped (DexScreener wins on conflict to preserve its richer field set),
       sorted by volume_24h, returned up to _FALLBACK_MERGED_LIMIT.

    Increasing candidate count in fallback mode (vs. the old DexScreener-only
    10-token cap) is intentional — it gives the quality/exec/tactical filters
    a wider population to work with.
    """
    independent_mode = bool(NO_BIRDEYE_MODE or INDEPENDENT_SOURCE_MODE)
    birdeye_tokens = [] if independent_mode else fetch_birdeye_market_data()
    if birdeye_tokens:
        logging.info("Using BirdEye feed (%d tokens)", len(birdeye_tokens))
        _write_market_source_status({
            "status": "PRIMARY",
            "primary": "birdeye",
            "fallback_active": False,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "counts": {"birdeye": len(birdeye_tokens), "geckoterminal": 0, "dexscreener": 0},
            "detail": "BirdEye market data is primary.",
        })
        return birdeye_tokens

    # BirdEye unavailable or intentionally disabled — build merged fallback universe.
    gecko_tokens = fetch_geckoterminal_trending(limit=_FALLBACK_MERGED_LIMIT)
    dex_tokens = fetch_dexscreener_market_data()

    if not gecko_tokens and not dex_tokens:
        logging.warning("All market data sources unavailable.")
        _write_market_source_status({
            "status": "UNAVAILABLE",
            "primary": None,
            "fallback_active": False,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "counts": {"birdeye": 0, "geckoterminal": 0, "dexscreener": 0},
            "detail": "All market data sources are unavailable.",
        })
        return []

    # Merge by mint address.
    # DexScreener wins on address conflicts (boosts_active, social_links,
    # website_links, quote_symbol are absent from GeckoTerminal).
    # GeckoTerminal contributes net-new trending tokens not surfaced by
    # DexScreener keyword search.
    merged: dict = {}

    # Load GeckoTerminal first so DexScreener overwrites on conflicts
    for token in (gecko_tokens or []):
        addr = token.get("address")
        if addr:
            merged[addr] = token

    for token in (dex_tokens or []):
        addr = token.get("address")
        if addr:
            merged[addr] = token  # DexScreener takes precedence

    result = sorted(
        merged.values(),
        key=lambda t: float(t.get("volume_24h") or 0),
        reverse=True,
    )
    result = result[:_FALLBACK_MERGED_LIMIT]

    gecko_count = len(gecko_tokens or [])
    dex_count = len(dex_tokens or [])
    new_from_gecko = sum(
        1 for t in (gecko_tokens or [])
        if t.get("address") not in {d.get("address") for d in (dex_tokens or [])}
    )
    logging.info(
        "Fallback feed: GeckoTerminal=%d DexScreener=%d merged=%d (gecko_new=%d)",
        gecko_count, dex_count, len(result), new_from_gecko,
    )
    _write_market_source_status({
        "status": "INDEPENDENT_FALLBACK" if independent_mode else "FALLBACK",
        "primary": "geckoterminal" if gecko_count else "dexscreener",
        "fallback_active": True,
        "independent_mode": independent_mode,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "counts": {"birdeye": 0, "geckoterminal": gecko_count, "dexscreener": dex_count, "merged": len(result)},
        "detail": (
            f"BirdEye disabled intentionally; using GeckoTerminal + DexScreener merged fallback ({len(result)} candidates)."
            if independent_mode
            else f"BirdEye unavailable; using GeckoTerminal + DexScreener merged fallback ({len(result)} candidates)."
        ),
    })
    return result
