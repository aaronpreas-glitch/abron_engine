"""
ws_price_feed.py — WebSocket-driven price feed for open positions.

Architecture:
  • Primary:  Birdeye WebSocket (wss://public-api.birdeye.so/socket?chain=solana)
              Subscribes to PRICE_SUBSCRIBE messages for each registered mint.
  • Fallback: Fast HTTP polling via Jupiter Price API (every POLL_INTERVAL_SEC)
              if the WebSocket is disconnected or Birdeye is unavailable.

Public API:
  register_mint(mint)       — start receiving price updates for this mint
  unregister_mint(mint)     — stop tracking this mint
  get_price(mint)           — latest cached price (float | None)
  subscribe(mint)           — returns asyncio.Queue that receives (mint, price) tuples
  unsubscribe(mint, queue)  — remove queue from subscribers
  start()                   — start background tasks (call once at engine startup)
  stop()                    — cancel all background tasks

Design goals:
  • When a price update arrives → all subscriber queues get (mint, price) immediately
  • Executor replaces 60s poll loop with: await queue.get() → check_exit_conditions()
  • Max latency to exit decision: ~1-2s (WS tick) vs up to 60s (old poll)
"""

import asyncio
import json
import logging
import os
import time
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# ── Config ─────────────────────────────────────────────────────────────────────

BIRDEYE_API_KEY   = os.getenv("BIRDEYE_API_KEY", "")
BIRDEYE_WS_URL    = "wss://public-api.birdeye.so/socket?chain=solana"
JUPITER_PRICE_URL = "https://api.jup.ag/price/v2"
POLL_INTERVAL_SEC = int(os.getenv("WS_PRICE_POLL_INTERVAL", "8"))   # fallback poll rate
WS_RECONNECT_SEC  = int(os.getenv("WS_PRICE_RECONNECT_SEC", "5"))   # delay before WS reconnect
WS_PING_INTERVAL  = 20   # send WS ping every N seconds to keep alive

# ── State ──────────────────────────────────────────────────────────────────────

_price_cache:  dict[str, float]       = {}   # mint → latest price
_timestamp:    dict[str, float]       = {}   # mint → unix timestamp of last update
_registered:   set[str]               = set()
_subscribers:  dict[str, list[asyncio.Queue]] = {}   # mint → list of queues

_ws_connected  = False
_ws_task:      Optional[asyncio.Task] = None
_poll_task:    Optional[asyncio.Task] = None
_started       = False


# ── Public API ─────────────────────────────────────────────────────────────────

def register_mint(mint: str) -> None:
    """Start tracking price updates for this mint."""
    if mint and mint not in _registered:
        _registered.add(mint)
        logger.info("ws_price_feed: registered %s  (total=%d)", mint[:12], len(_registered))


def unregister_mint(mint: str) -> None:
    """Stop tracking this mint and clear its cache."""
    _registered.discard(mint)
    _price_cache.pop(mint, None)
    _timestamp.pop(mint, None)
    _subscribers.pop(mint, None)
    logger.info("ws_price_feed: unregistered %s  (total=%d)", mint[:12], len(_registered))


def get_price(mint: str) -> Optional[float]:
    """Return the latest cached price for this mint, or None if unknown."""
    return _price_cache.get(mint)


def get_price_age(mint: str) -> Optional[float]:
    """Return seconds since last price update for this mint."""
    ts = _timestamp.get(mint)
    return (time.monotonic() - ts) if ts is not None else None


def subscribe(mint: str) -> asyncio.Queue:
    """
    Return a new asyncio.Queue that will receive (mint, price) tuples
    whenever a price update arrives for this mint.
    """
    q: asyncio.Queue = asyncio.Queue()
    _subscribers.setdefault(mint, []).append(q)
    return q


def unsubscribe(mint: str, q: asyncio.Queue) -> None:
    """Remove a queue from the subscriber list."""
    subs = _subscribers.get(mint, [])
    if q in subs:
        subs.remove(q)


def is_ws_connected() -> bool:
    return _ws_connected


def status() -> dict:
    return {
        "ws_connected":    _ws_connected,
        "registered_mints": list(_registered),
        "cached_prices":   {m: p for m, p in _price_cache.items()},
        "poll_interval":   POLL_INTERVAL_SEC,
    }


# ── Internal: price push ────────────────────────────────────────────────────────

def _push_price(mint: str, price: float) -> None:
    """Update cache and notify all subscriber queues."""
    if price <= 0:
        return
    changed = _price_cache.get(mint) != price
    _price_cache[mint] = price
    _timestamp[mint] = time.monotonic()

    if changed:
        for q in list(_subscribers.get(mint, [])):
            try:
                q.put_nowait((mint, price))
            except asyncio.QueueFull:
                pass   # subscriber isn't draining — skip


# ── Birdeye WebSocket ───────────────────────────────────────────────────────────

async def _birdeye_ws_loop() -> None:
    """
    Maintain a persistent WebSocket connection to Birdeye.
    Subscribes to PRICE_SUBSCRIBE for all registered mints.
    Re-subscribes when new mints are registered.
    Reconnects automatically on any error.
    """
    global _ws_connected

    if not BIRDEYE_API_KEY:
        logger.warning("ws_price_feed: BIRDEYE_API_KEY not set — using HTTP fallback only")
        return

    try:
        import websockets  # type: ignore
    except ImportError:
        logger.warning("ws_price_feed: 'websockets' package not installed — using HTTP fallback")
        return

    subscribed_mints: set[str] = set()

    while True:
        try:
            headers = {"x-chain": "solana", "X-API-KEY": BIRDEYE_API_KEY}
            async with websockets.connect(
                BIRDEYE_WS_URL,
                additional_headers=headers,
                ping_interval=WS_PING_INTERVAL,
                ping_timeout=30,
                close_timeout=5,
            ) as ws:
                _ws_connected = True
                logger.info("ws_price_feed: Birdeye WS connected")

                # Subscribe to all currently registered mints
                for mint in list(_registered):
                    await _subscribe_birdeye(ws, mint)
                    subscribed_mints.add(mint)

                # Message loop
                while True:
                    # Check for newly registered mints to subscribe
                    new_mints = _registered - subscribed_mints
                    for mint in new_mints:
                        await _subscribe_birdeye(ws, mint)
                        subscribed_mints.add(mint)

                    # Remove unregistered mints from subscribed set
                    gone = subscribed_mints - _registered
                    subscribed_mints -= gone

                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=30.0)
                    except asyncio.TimeoutError:
                        # Send a keep-alive ping
                        try:
                            await ws.send(json.dumps({"type": "ping"}))
                        except Exception:
                            break
                        continue

                    try:
                        msg = json.loads(raw)
                    except json.JSONDecodeError:
                        continue

                    # Birdeye price tick format:
                    # { "type": "PRICE_UPDATE", "data": { "address": "...", "value": 0.000123 } }
                    msg_type = msg.get("type", "")
                    if msg_type in ("PRICE_UPDATE", "price"):
                        data = msg.get("data", {})
                        mint = data.get("address") or data.get("mint") or data.get("baseAddress")
                        price_raw = data.get("value") or data.get("price")
                        if mint and price_raw is not None:
                            price = float(price_raw)
                            _push_price(mint, price)
                            logger.debug("WS price: %s → %.8g", mint[:12], price)

        except Exception as exc:
            _ws_connected = False
            subscribed_mints.clear()
            logger.warning("ws_price_feed: Birdeye WS error: %s — reconnecting in %ds",
                           exc, WS_RECONNECT_SEC)

        await asyncio.sleep(WS_RECONNECT_SEC)


async def _subscribe_birdeye(ws, mint: str) -> None:
    """Send a PRICE_SUBSCRIBE message for a given mint."""
    try:
        msg = json.dumps({
            "type": "SUBSCRIBE_PRICE",
            "data": {
                "chartType": "1",
                "currency": "usd",
                "address": mint,
            },
        })
        await ws.send(msg)
        logger.debug("ws_price_feed: subscribed Birdeye WS for %s", mint[:12])
    except Exception as exc:
        logger.warning("ws_price_feed: subscribe failed for %s: %s", mint[:12], exc)


# ── HTTP Fallback Poller ────────────────────────────────────────────────────────

async def _http_poll_loop() -> None:
    """
    Poll Jupiter Price API every POLL_INTERVAL_SEC for all registered mints.
    Always runs — fills gaps even when WS is connected (belt-and-suspenders).
    Uses batched requests: Jupiter supports up to 100 mints per call.
    """
    logger.info("ws_price_feed: HTTP fallback poller started (interval=%ds)", POLL_INTERVAL_SEC)
    while True:
        mints = list(_registered)
        if mints:
            try:
                await _fetch_jupiter_prices(mints)
            except Exception as exc:
                logger.debug("ws_price_feed: HTTP poll error: %s", exc)
        await asyncio.sleep(POLL_INTERVAL_SEC)


async def _fetch_jupiter_prices(mints: list[str]) -> None:
    """Fetch prices for a list of mints from Jupiter Price API v2."""
    if not mints:
        return
    # Jupiter supports comma-separated mints in ids param
    ids = ",".join(mints)
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(JUPITER_PRICE_URL, params={"ids": ids})
        r.raise_for_status()
        data = r.json().get("data", {})
        for mint, info in data.items():
            price_raw = info.get("price")
            if price_raw is not None:
                price = float(price_raw)
                # Only push if WS hasn't updated recently (< 5s ago)
                age = get_price_age(mint)
                if age is None or age > 5.0:
                    _push_price(mint, price)


# ── Lifecycle ──────────────────────────────────────────────────────────────────

def start() -> None:
    """
    Start background tasks. Call once at engine startup (inside async context).
    Safe to call multiple times — idempotent.
    """
    global _ws_task, _poll_task, _started
    if _started:
        return
    _started = True
    loop = asyncio.get_event_loop()
    _ws_task   = loop.create_task(_birdeye_ws_loop(),  name="ws_price_feed_ws")
    _poll_task = loop.create_task(_http_poll_loop(),   name="ws_price_feed_poll")
    logger.info("ws_price_feed: started (WS + HTTP fallback)")


def stop() -> None:
    """Cancel all background tasks."""
    global _started
    _started = False
    for task in (_ws_task, _poll_task):
        if task and not task.done():
            task.cancel()
    logger.info("ws_price_feed: stopped")
