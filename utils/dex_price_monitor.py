"""
dex_price_monitor.py — Cross-DEX price spread detection for new launches.

When launch_listener detects a high-score launch, prices can diverge by 3–15%
across DEXs (Pump.fun bonding curve vs. Raydium vs. Orca/Meteora) for a brief
window. This module fetches prices from multiple sources simultaneously and
alerts when spread exceeds ARB_MIN_SPREAD_PCT (default: 4%).

This is price intelligence (not execution arb) — it identifies which DEX
has the better price, logs it, and optionally sends a Telegram alert.
Actual execution via Jupiter uses the best route anyway, but this data
helps understand launch mechanics and validate entry timing.

Key functions:
    fetch_multi_dex_prices(mint) → dict
    monitor_launch_for_arb(mint, entry_price, source) → None (async task)
    arb_monitor_loop() → None (background loop, subscribes to launch feed)
    get_recent_arb_opportunities(limit) → list[dict]

Configuration (.env):
    ARB_ENABLED=false               # master kill switch
    ARB_MIN_SPREAD_PCT=4.0          # alert threshold (%)
    ARB_MIN_SPREAD_TO_LOG=1.0       # minimum spread to bother logging
    ARB_MONITOR_DURATION_SECONDS=300  # how long to watch each launch
    ARB_POLL_INTERVAL_SECONDS=10    # price check frequency
    ARB_MAX_CONCURRENT_MONITORS=3   # max simultaneous launch monitors
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# ── Paths ─────────────────────────────────────────────────────────────────────

_BASE_DIR       = Path(__file__).parent.parent
_ARB_FEED_PATH  = _BASE_DIR / "data_storage" / "arb_feed.jsonl"

# ── Config ────────────────────────────────────────────────────────────────────

def _cfg(key: str, default: str) -> str:
    return os.environ.get(key, default)

def _cfgf(key: str, default: float) -> float:
    try:
        return float(os.environ.get(key, str(default)))
    except (ValueError, TypeError):
        return default

def _cfgi(key: str, default: int) -> int:
    try:
        return int(os.environ.get(key, str(default)))
    except (ValueError, TypeError):
        return default


ARB_ENABLED           = lambda: _cfg("ARB_ENABLED", "false").lower() == "true"
ARB_MIN_SPREAD_PCT    = lambda: _cfgf("ARB_MIN_SPREAD_PCT", 4.0)
ARB_MIN_SPREAD_TO_LOG = lambda: _cfgf("ARB_MIN_SPREAD_TO_LOG", 1.0)
MONITOR_DURATION      = lambda: _cfgi("ARB_MONITOR_DURATION_SECONDS", 300)
POLL_INTERVAL         = lambda: _cfgi("ARB_POLL_INTERVAL_SECONDS", 10)
MAX_CONCURRENT        = lambda: _cfgi("ARB_MAX_CONCURRENT_MONITORS", 3)

# Jupiter price API endpoint
_JUPITER_PRICE_URL = "https://price.jup.ag/v6/price"
# DexScreener pairs endpoint
_DEXSCREENER_PAIRS_URL = "https://api.dexscreener.com/latest/dex/tokens/{mint}"

# ── Active monitor semaphore (module-level) ───────────────────────────────────

_active_monitors: set[str] = set()  # mints currently being monitored
_arb_queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=50)  # launch events for arb monitoring


# ── Price fetching ────────────────────────────────────────────────────────────

async def fetch_multi_dex_prices(mint: str) -> dict:
    """
    Fetch prices from Jupiter and DexScreener simultaneously.

    Returns:
    {
        "mint": str,
        "ts_utc": str,
        "jupiter_price": float | None,       # Jupiter aggregated best price
        "dex_prices": {                       # per-DEX prices from DexScreener
            "Raydium": float,
            "Orca": float,
            "Pump.fun": float,
            ...
        },
        "best_price": float | None,           # lowest ask across all sources
        "worst_price": float | None,          # highest ask (most expensive)
        "spread_pct": float | None,           # (worst - best) / best * 100
        "best_dex": str | None,               # DEX name with lowest price
        "worst_dex": str | None,              # DEX name with highest price
        "sources_checked": int,
        "error": str | None,
    }
    """
    ts = datetime.now(timezone.utc).isoformat()
    result: dict = {
        "mint": mint,
        "ts_utc": ts,
        "jupiter_price": None,
        "dex_prices": {},
        "best_price": None,
        "worst_price": None,
        "spread_pct": None,
        "best_dex": None,
        "worst_dex": None,
        "sources_checked": 0,
        "error": None,
    }

    async with httpx.AsyncClient(timeout=8.0) as client:
        # Fire both requests concurrently
        jup_task = client.get(_JUPITER_PRICE_URL, params={"ids": mint})
        dex_task  = client.get(_DEXSCREENER_PAIRS_URL.format(mint=mint))

        try:
            jup_resp, dex_resp = await asyncio.gather(jup_task, dex_task, return_exceptions=True)
        except Exception as exc:
            result["error"] = str(exc)
            return result

        # Parse Jupiter price
        jup_price: Optional[float] = None
        if not isinstance(jup_resp, Exception) and jup_resp.status_code == 200:
            try:
                jup_data = jup_resp.json()
                price_info = jup_data.get("data", {}).get(mint, {})
                if price_info and price_info.get("price"):
                    jup_price = float(price_info["price"])
                    result["jupiter_price"] = jup_price
                    result["sources_checked"] += 1
            except Exception as exc:
                logger.debug("Jupiter price parse error for %s: %s", mint, exc)

        # Parse DexScreener per-DEX prices
        dex_prices: dict[str, float] = {}
        if not isinstance(dex_resp, Exception) and dex_resp.status_code == 200:
            try:
                dex_data = dex_resp.json()
                pairs = dex_data.get("pairs") or []
                for pair in pairs:
                    dex_name = pair.get("dexId", "unknown")
                    price_str = pair.get("priceUsd")
                    if price_str:
                        try:
                            price_val = float(price_str)
                            if price_val > 0:
                                # Keep the pair with highest volume per DEX
                                if dex_name not in dex_prices or price_val > dex_prices[dex_name]:
                                    dex_prices[dex_name] = price_val
                        except (ValueError, TypeError):
                            pass
                if dex_prices:
                    result["dex_prices"] = dex_prices
                    result["sources_checked"] += len(dex_prices)
            except Exception as exc:
                logger.debug("DexScreener parse error for %s: %s", mint, exc)

    # Combine all prices for spread calculation
    all_prices: dict[str, float] = {}
    if jup_price:
        all_prices["Jupiter"] = jup_price
    all_prices.update(dex_prices)

    if len(all_prices) >= 2:
        best_dex  = min(all_prices, key=all_prices.get)   # type: ignore
        worst_dex = max(all_prices, key=all_prices.get)   # type: ignore
        best_price  = all_prices[best_dex]
        worst_price = all_prices[worst_dex]

        if best_price > 0:
            spread_pct = (worst_price - best_price) / best_price * 100
            result["best_price"]  = best_price
            result["worst_price"] = worst_price
            result["spread_pct"]  = round(spread_pct, 3)
            result["best_dex"]    = best_dex
            result["worst_dex"]   = worst_dex

    return result


# ── Arb opportunity logger ────────────────────────────────────────────────────

def _log_arb_opportunity(record: dict) -> None:
    """Append an arb opportunity to the jsonl feed file."""
    try:
        _ARB_FEED_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _ARB_FEED_PATH.open("a") as f:
            f.write(json.dumps(record) + "\n")
    except Exception as exc:
        logger.warning("Failed to log arb opportunity: %s", exc)


def get_recent_arb_opportunities(limit: int = 100) -> list[dict]:
    """
    Read recent arb opportunities from the jsonl feed.
    Returns most recent `limit` entries, newest first.
    """
    if not _ARB_FEED_PATH.exists():
        return []
    try:
        lines = _ARB_FEED_PATH.read_text().splitlines()
        records = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except Exception:
                pass
        # Newest first
        records.reverse()
        return records[:limit]
    except Exception as exc:
        logger.warning("get_recent_arb_opportunities error: %s", exc)
        return []


# ── Per-launch monitor ────────────────────────────────────────────────────────

async def monitor_launch_for_arb(
    mint: str,
    symbol: str,
    entry_price: float,
    source: str,
    score: float,
    app=None,  # FastAPI app for Telegram (optional)
) -> None:
    """
    Monitor a newly detected launch for cross-DEX price spread.
    Runs for ARB_MONITOR_DURATION_SECONDS, checking every ARB_POLL_INTERVAL_SECONDS.
    Logs opportunities to arb_feed.jsonl and optionally sends Telegram alerts.
    """
    if not ARB_ENABLED():
        return

    if mint in _active_monitors:
        logger.debug("arb: %s already being monitored — skipping", mint)
        return

    _active_monitors.add(mint)
    logger.info("arb: starting monitor for %s (%s) — score=%.0f source=%s", symbol, mint[:8], score, source)

    duration  = MONITOR_DURATION()
    interval  = POLL_INTERVAL()
    min_log   = ARB_MIN_SPREAD_TO_LOG()
    min_alert = ARB_MIN_SPREAD_PCT()
    checks    = 0
    alerts_sent = 0
    max_spread_seen = 0.0
    start_ts = datetime.now(timezone.utc)

    try:
        deadline = asyncio.get_event_loop().time() + duration
        while asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(interval)
            checks += 1

            prices = await fetch_multi_dex_prices(mint)
            spread = prices.get("spread_pct")

            if spread is None or spread < min_log:
                continue

            if spread > max_spread_seen:
                max_spread_seen = spread

            record = {
                "ts_utc":      prices["ts_utc"],
                "mint":        mint,
                "symbol":      symbol,
                "score":       round(score, 1),
                "source":      source,
                "entry_price": entry_price,
                "spread_pct":  spread,
                "best_dex":    prices.get("best_dex"),
                "worst_dex":   prices.get("worst_dex"),
                "best_price":  prices.get("best_price"),
                "worst_price": prices.get("worst_price"),
                "jupiter_price": prices.get("jupiter_price"),
                "dex_prices":  prices.get("dex_prices", {}),
                "alerted":     spread >= min_alert,
                "check_n":     checks,
                "elapsed_s":   round((datetime.now(timezone.utc) - start_ts).total_seconds()),
            }
            _log_arb_opportunity(record)

            if spread >= min_alert:
                logger.info(
                    "arb: %s spread=%.1f%% best=%s@%.8f worst=%s@%.8f",
                    symbol, spread,
                    prices.get("best_dex", "?"), prices.get("best_price", 0),
                    prices.get("worst_dex", "?"), prices.get("worst_price", 0),
                )
                # Send Telegram alert (respect a cooldown: max 1 per launch per 60s)
                if alerts_sent == 0 and app is not None:
                    try:
                        await _send_arb_telegram(symbol, mint, spread, prices, score, app)
                        alerts_sent += 1
                    except Exception as exc:
                        logger.warning("arb: Telegram alert failed: %s", exc)

    finally:
        _active_monitors.discard(mint)
        logger.info(
            "arb: monitor done for %s — checks=%d max_spread=%.1f%%",
            symbol, checks, max_spread_seen,
        )


async def _send_arb_telegram(
    symbol: str,
    mint: str,
    spread_pct: float,
    prices: dict,
    score: float,
    app,
) -> None:
    """Send arb alert via Telegram."""
    try:
        import telegram
        token   = os.environ.get("TELEGRAM_TOKEN", "")
        chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
        if not token or not chat_id:
            return

        best_dex   = prices.get("best_dex", "?")
        worst_dex  = prices.get("worst_dex", "?")
        best_price = prices.get("best_price")
        worst_price = prices.get("worst_price")

        text = (
            f"🔀 <b>Arb Spread Detected</b>\n\n"
            f"<b>${symbol}</b> | score={score:.0f}\n"
            f"Spread: <b>{spread_pct:.1f}%</b>\n"
            f"Buy on: <b>{best_dex}</b> @ {best_price:.8f}\n"
            f"Sell on: <b>{worst_dex}</b> @ {worst_price:.8f}\n"
            f"<a href=\"https://dexscreener.com/solana/{mint}\">DexScreener</a>"
        )
        bot = telegram.Bot(token=token)
        await bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")
    except Exception as exc:
        logger.warning("arb: Telegram send failed: %s", exc)


# ── Background loop ───────────────────────────────────────────────────────────

async def arb_monitor_loop(app=None) -> None:
    """
    Background task started from main.py lifespan.
    Reads from _arb_queue (populated by launch_listener after high-score detections)
    and spawns per-launch monitors, respecting MAX_CONCURRENT_MONITORS.
    """
    logger.info("arb_monitor_loop started (ARB_ENABLED=%s)", ARB_ENABLED())
    while True:
        try:
            event = await asyncio.wait_for(_arb_queue.get(), timeout=30.0)
        except asyncio.TimeoutError:
            continue
        except asyncio.CancelledError:
            break

        if not ARB_ENABLED():
            continue

        mint   = event.get("mint", "")
        symbol = event.get("symbol", "?")
        score  = float(event.get("score", 0))

        if not mint:
            continue

        # Respect concurrency cap
        if len(_active_monitors) >= MAX_CONCURRENT():
            logger.debug(
                "arb: at max concurrent monitors (%d) — skipping %s",
                MAX_CONCURRENT(), symbol,
            )
            continue

        # Spawn monitor as a fire-and-forget task
        asyncio.create_task(
            monitor_launch_for_arb(
                mint=mint,
                symbol=symbol,
                entry_price=float(event.get("entry_price", 0)),
                source=event.get("source", "unknown"),
                score=score,
                app=app,
            )
        )


def enqueue_launch_for_arb(
    mint: str,
    symbol: str,
    score: float,
    entry_price: float,
    source: str,
) -> None:
    """
    Called from launch_listener after _send_launch_alert() for high-score launches.
    Non-blocking — drops the event if the queue is full.
    """
    if not ARB_ENABLED():
        return
    try:
        _arb_queue.put_nowait({
            "mint":        mint,
            "symbol":      symbol,
            "score":       score,
            "entry_price": entry_price,
            "source":      source,
        })
        logger.debug("arb: enqueued %s (%s) score=%.0f", symbol, mint[:8], score)
    except asyncio.QueueFull:
        logger.debug("arb: queue full — dropped %s", symbol)
