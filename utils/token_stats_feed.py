from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

BIRDEYE_API_KEY = os.getenv("BIRDEYE_API_KEY", "")
BIRDEYE_WS_URL = "wss://public-api.birdeye.so/socket/solana"
WS_RECONNECT_SEC = int(os.getenv("TOKEN_STATS_WS_RECONNECT_SEC", "5"))
WS_PING_INTERVAL = 20
MAX_ADDRESSES_PER_CALL = 100

_tracked: dict[str, str | None] = {}
_latest: dict[str, dict] = {}
_ws_connected = False
_ws_task: Optional[asyncio.Task] = None
_started = False
_state_lock = asyncio.Lock()


def set_tracked_mints(items: dict[str, str | None]) -> None:
    clean: dict[str, str | None] = {}
    for mint, symbol in (items or {}).items():
        mint_key = str(mint or "").strip()
        if mint_key:
            clean[mint_key] = str(symbol or "").upper() or None
    global _tracked
    _tracked = clean


def get_snapshot(mint: str) -> dict | None:
    return dict(_latest.get(str(mint or "").strip()) or {}) or None


def status() -> dict:
    return {
        "ws_connected": _ws_connected,
        "tracked_count": len(_tracked),
        "tracked_mints": list(_tracked.keys())[:25],
        "cached_count": len(_latest),
    }


def _subscription_message(addresses: list[str]) -> str:
    return json.dumps(
        {
            "type": "SUBSCRIBE_TOKEN_STATS",
            "data": {
                "address": addresses if len(addresses) > 1 else addresses[0],
                "select": {
                    "price": True,
                    "trade_data": {
                        "volume": True,
                        "trade": True,
                        "price_change": True,
                        "trade_change": True,
                        "volume_change": True,
                        "unique_wallet": True,
                        "intervals": ["30m", "1h", "24h"],
                    },
                    "fdv": True,
                    "marketcap": True,
                    "supply": True,
                    "last_trade": True,
                    "liquidity": True,
                },
            },
        }
    )


def _unsubscribe_message(addresses: list[str]) -> str:
    return json.dumps(
        {
            "type": "UNSUBSCRIBE_TOKEN_STATS",
            "data": {
                "address": addresses if len(addresses) > 1 else addresses[0],
            },
        }
    )


async def _persist_token_stats(data: dict, *, symbol: str | None = None) -> None:
    try:
        from utils.db import record_memecoin_token_stats_snapshots  # type: ignore
        from utils.token_stats import normalize_birdeye_token_stats  # type: ignore

        normalized = normalize_birdeye_token_stats(
            {**dict(data), "ts_utc": datetime.now(timezone.utc).isoformat()},
            symbol=symbol,
        )
        mint = str(normalized.get("mint") or "")
        if not mint:
            return
        async with _state_lock:
            _latest[mint] = normalized
        await asyncio.to_thread(
            record_memecoin_token_stats_snapshots,
            [normalized],
            min_interval_seconds=120,
        )
    except Exception as exc:
        logger.debug("token_stats_feed: persist failed: %s", exc)


async def _sync_subscriptions(ws, subscribed: set[str]) -> None:
    tracked = set(_tracked.keys())
    new = [mint for mint in tracked if mint not in subscribed]
    gone = [mint for mint in subscribed if mint not in tracked]

    if gone:
        for idx in range(0, len(gone), MAX_ADDRESSES_PER_CALL):
            batch = gone[idx : idx + MAX_ADDRESSES_PER_CALL]
            await ws.send(_unsubscribe_message(batch))
        for mint in gone:
            subscribed.discard(mint)

    if new:
        for idx in range(0, len(new), MAX_ADDRESSES_PER_CALL):
            batch = new[idx : idx + MAX_ADDRESSES_PER_CALL]
            await ws.send(_subscription_message(batch))
        for mint in new:
            subscribed.add(mint)
        logger.info("token_stats_feed: subscribed %d mint(s)", len(new))


async def _ws_loop() -> None:
    global _ws_connected
    if not BIRDEYE_API_KEY:
        logger.warning("token_stats_feed: BIRDEYE_API_KEY not set — stream disabled")
        return

    try:
        import websockets  # type: ignore
    except ImportError:
        logger.warning("token_stats_feed: websockets package not installed")
        return

    subscribed: set[str] = set()

    while True:
        try:
            ws_url = f"{BIRDEYE_WS_URL}?x-api-key={BIRDEYE_API_KEY}"
            async with websockets.connect(
                ws_url,
                ping_interval=WS_PING_INTERVAL,
                ping_timeout=30,
                close_timeout=5,
                subprotocols=["echo-protocol"],
                origin="ws://public-api.birdeye.so",
            ) as ws:
                _ws_connected = True
                subscribed.clear()
                logger.info("token_stats_feed: Birdeye WS connected")

                while True:
                    await _sync_subscriptions(ws, subscribed)
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=30.0)
                    except asyncio.TimeoutError:
                        await ws.send(json.dumps({"type": "ping"}))
                        continue

                    try:
                        msg = json.loads(raw)
                    except json.JSONDecodeError:
                        continue

                    if str(msg.get("type") or "").upper() != "TOKEN_STATS_DATA":
                        continue
                    data = dict(msg.get("data") or {})
                    mint = str(data.get("address") or "").strip()
                    if not mint:
                        continue
                    symbol = _tracked.get(mint)
                    await _persist_token_stats(data, symbol=symbol)
        except Exception as exc:
            _ws_connected = False
            subscribed.clear()
            logger.warning("token_stats_feed: WS error: %s — reconnecting in %ds", exc, WS_RECONNECT_SEC)
        await asyncio.sleep(WS_RECONNECT_SEC)


def start() -> None:
    global _started, _ws_task
    if _started:
        return
    _started = True
    loop = asyncio.get_event_loop()
    _ws_task = loop.create_task(_ws_loop(), name="token_stats_feed_ws")
    logger.info("token_stats_feed: started")


def stop() -> None:
    global _started
    _started = False
    if _ws_task and not _ws_task.done():
        _ws_task.cancel()
    logger.info("token_stats_feed: stopped")
