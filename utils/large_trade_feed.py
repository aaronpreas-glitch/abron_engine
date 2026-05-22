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
WS_RECONNECT_SEC = int(os.getenv("LARGE_TRADE_WS_RECONNECT_SEC", "5"))
WS_PING_INTERVAL = 20
MIN_VOLUME_USD = float(os.getenv("LARGE_TRADE_WS_MIN_VOLUME", "10000"))
MAX_VOLUME_USD = float(os.getenv("LARGE_TRADE_WS_MAX_VOLUME", "0"))

_tracked: dict[str, str | None] = {}
_latest_by_mint: dict[str, dict] = {}
_stats = {
    "matched_events": 0,
    "persisted_events": 0,
    "last_event_ts": None,
}
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
    return dict(_latest_by_mint.get(str(mint or "").strip()) or {}) or None


def status() -> dict:
    return {
        "ws_connected": _ws_connected,
        "tracked_count": len(_tracked),
        "cached_count": len(_latest_by_mint),
        **dict(_stats),
    }


def _subscription_message() -> str:
    payload = {
        "type": "SUBSCRIBE_LARGE_TRADE_TXS",
        "min_volume": MIN_VOLUME_USD,
    }
    if MAX_VOLUME_USD > MIN_VOLUME_USD:
        payload["max_volume"] = MAX_VOLUME_USD
    return json.dumps(payload)


async def _persist_large_trade(data: dict) -> None:
    try:
        from utils.db import record_memecoin_large_trade_snapshots  # type: ignore
        from utils.large_trade import normalize_birdeye_large_trade  # type: ignore

        ts_utc = datetime.now(timezone.utc).isoformat()
        normalized = normalize_birdeye_large_trade(
            dict(data),
            tracked_mints=dict(_tracked),
            ts_utc=ts_utc,
        )
        if not normalized:
            return
        mint = str(normalized.get("mint") or "")
        async with _state_lock:
            _latest_by_mint[mint] = normalized
            _stats["matched_events"] = int(_stats.get("matched_events") or 0) + 1
            _stats["last_event_ts"] = ts_utc
        inserted = await asyncio.to_thread(record_memecoin_large_trade_snapshots, [normalized])
        if inserted:
            async with _state_lock:
                _stats["persisted_events"] = int(_stats.get("persisted_events") or 0) + int(inserted)
    except Exception as exc:
        logger.debug("large_trade_feed: persist failed: %s", exc)


async def _ws_loop() -> None:
    global _ws_connected
    if not BIRDEYE_API_KEY:
        logger.warning("large_trade_feed: BIRDEYE_API_KEY not set — stream disabled")
        return

    try:
        import websockets  # type: ignore
    except ImportError:
        logger.warning("large_trade_feed: websockets package not installed")
        return

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
                await ws.send(_subscription_message())
                logger.info("large_trade_feed: Birdeye WS connected")

                while True:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=30.0)
                    except asyncio.TimeoutError:
                        await ws.send(json.dumps({"type": "ping"}))
                        continue

                    try:
                        msg = json.loads(raw)
                    except json.JSONDecodeError:
                        continue

                    if str(msg.get("type") or "").upper() != "TXS_LARGE_TRADE_DATA":
                        continue
                    await _persist_large_trade(dict(msg.get("data") or {}))
        except Exception as exc:
            _ws_connected = False
            logger.warning("large_trade_feed: WS error: %s — reconnecting in %ds", exc, WS_RECONNECT_SEC)
        await asyncio.sleep(WS_RECONNECT_SEC)


def start() -> None:
    global _started, _ws_task
    if _started:
        return
    _started = True
    loop = asyncio.get_event_loop()
    _ws_task = loop.create_task(_ws_loop(), name="large_trade_feed_ws")
    logger.info("large_trade_feed: started")


def stop() -> None:
    global _started
    _started = False
    if _ws_task and not _ws_task.done():
        _ws_task.cancel()
    logger.info("large_trade_feed: stopped")
