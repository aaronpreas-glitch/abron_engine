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
WS_RECONNECT_SEC = int(os.getenv("WALLET_TX_WS_RECONNECT_SEC", "5"))
WS_PING_INTERVAL = 20
STALE_WORKER_RESTART_SEC = int(os.getenv("WALLET_TX_STALE_WORKER_RESTART_SEC", "600"))

_tracked: dict[str, dict] = {}
_latest_by_wallet: dict[str, dict] = {}
_stats = {
    "frames_received": 0,
    "non_json_frames": 0,
    "non_data_frames": 0,
    "normalize_dropped": 0,
    "duplicate_events": 0,
    "persist_failures": 0,
    "matched_events": 0,
    "persisted_events": 0,
    "last_frame_ts": None,
    "last_event_ts": None,
    "last_message_type": None,
    "last_drop_reason": None,
    "last_persist_error": None,
    "last_matched_sample": None,
    "last_ignored_sample": None,
    "last_recovery_ts": None,
    "recovery_count": 0,
}
_worker_tasks: dict[str, asyncio.Task] = {}
_worker_started_at: dict[str, datetime] = {}
_started = False
_state_lock = asyncio.Lock()


def set_tracked_wallets(items: list[dict] | None) -> None:
    clean: dict[str, dict] = {}
    for item in list(items or []):
        wallet = str((item or {}).get("wallet_address") or "").strip()
        if wallet:
            clean[wallet] = dict(item)
    global _tracked
    _tracked = clean


def status() -> dict:
    active_workers = sum(1 for task in _worker_tasks.values() if not task.done())
    return {
        "tracked_count": len(_tracked),
        "worker_count": active_workers,
        "cached_count": len(_latest_by_wallet),
        **dict(_stats),
    }


def _subscription_message(wallet_address: str) -> str:
    return json.dumps(
        {
            "type": "SUBSCRIBE_WALLET_TXS",
            "data": {
                "address": wallet_address,
            },
        }
    )


async def _persist_wallet_tx(data: dict, *, wallet_address: str) -> None:
    try:
        from utils.wallet_reinforcement import record_live_wallet_tracking_events  # type: ignore
        from utils.wallet_tx import inspect_birdeye_wallet_tx  # type: ignore

        ts_utc = datetime.now(timezone.utc).isoformat()
        inspection = inspect_birdeye_wallet_tx(
            dict(data),
            wallet_address=wallet_address,
            ts_utc=ts_utc,
        )
        normalized = inspection.get("normalized")
        sample = inspection.get("sample")
        if not normalized:
            async with _state_lock:
                _stats["normalize_dropped"] = int(_stats.get("normalize_dropped") or 0) + 1
                _stats["last_event_ts"] = ts_utc
                _stats["last_drop_reason"] = str(inspection.get("drop_reason") or "unknown")
                _stats["last_ignored_sample"] = sample
            return
        async with _state_lock:
            _latest_by_wallet[wallet_address] = normalized
            _stats["matched_events"] = int(_stats.get("matched_events") or 0) + 1
            _stats["last_event_ts"] = ts_utc
            _stats["last_drop_reason"] = None
            _stats["last_matched_sample"] = sample
        inserted = await asyncio.to_thread(record_live_wallet_tracking_events, [normalized])
        async with _state_lock:
            if inserted:
                _stats["persisted_events"] = int(_stats.get("persisted_events") or 0) + int(inserted)
            else:
                _stats["duplicate_events"] = int(_stats.get("duplicate_events") or 0) + 1
    except Exception as exc:
        async with _state_lock:
            _stats["persist_failures"] = int(_stats.get("persist_failures") or 0) + 1
            _stats["last_persist_error"] = str(exc)
        logger.debug("wallet_tx_feed: persist failed for %s: %s", wallet_address[:10], exc)


async def _wallet_worker(wallet_address: str) -> None:
    if not BIRDEYE_API_KEY:
        logger.warning("wallet_tx_feed: BIRDEYE_API_KEY not set — stream disabled")
        return

    try:
        import websockets  # type: ignore
    except ImportError:
        logger.warning("wallet_tx_feed: websockets package not installed")
        return

    while True:
        if wallet_address not in _tracked:
            return
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
                await ws.send(_subscription_message(wallet_address))
                logger.info("wallet_tx_feed: Birdeye WS connected for %s", wallet_address[:10])
                while True:
                    if wallet_address not in _tracked:
                        return
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=30.0)
                    except asyncio.TimeoutError:
                        await ws.send(json.dumps({"type": "ping"}))
                        continue

                    ts_utc = datetime.now(timezone.utc).isoformat()
                    async with _state_lock:
                        _stats["frames_received"] = int(_stats.get("frames_received") or 0) + 1
                        _stats["last_frame_ts"] = ts_utc
                    try:
                        msg = json.loads(raw)
                    except json.JSONDecodeError:
                        async with _state_lock:
                            _stats["non_json_frames"] = int(_stats.get("non_json_frames") or 0) + 1
                            _stats["last_ignored_sample"] = {
                                "wallet_address": wallet_address,
                                "drop_reason": "non_json_frame",
                                "raw_preview": str(raw)[:240],
                            }
                        continue

                    message_type = str(msg.get("type") or "").upper()
                    async with _state_lock:
                        _stats["last_message_type"] = message_type or None
                    if message_type != "WALLET_TXS_DATA":
                        async with _state_lock:
                            _stats["non_data_frames"] = int(_stats.get("non_data_frames") or 0) + 1
                            _stats["last_ignored_sample"] = {
                                "wallet_address": wallet_address,
                                "drop_reason": "non_wallet_txs_data",
                                "message_type": message_type or None,
                            }
                        continue
                    await _persist_wallet_tx(dict(msg.get("data") or {}), wallet_address=wallet_address)
        except Exception as exc:
            logger.warning(
                "wallet_tx_feed: WS error for %s: %s — reconnecting in %ds",
                wallet_address[:10],
                exc,
                WS_RECONNECT_SEC,
            )
        await asyncio.sleep(WS_RECONNECT_SEC)


async def _sync_workers() -> None:
    tracked_wallets = set(_tracked.keys())
    now_ts = datetime.now(timezone.utc)
    for wallet, task in list(_worker_tasks.items()):
        if task.done():
            _worker_tasks.pop(wallet, None)
            continue
        last_frame = _stats.get("last_frame_ts")
        stale = False
        if last_frame:
            try:
                dt = datetime.fromisoformat(str(last_frame).replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                stale = (now_ts - dt.astimezone(timezone.utc)).total_seconds() > STALE_WORKER_RESTART_SEC
            except Exception:
                stale = False
        elif int(_stats.get("frames_received") or 0) == 0:
            started = _worker_started_at.get(wallet)
            stale = bool(started and (now_ts - started).total_seconds() > STALE_WORKER_RESTART_SEC)
        if stale:
            logger.warning("wallet_tx_feed: stale worker for %s — recycling subscription", wallet[:10])
            task.cancel()
            _worker_tasks.pop(wallet, None)
            _worker_started_at.pop(wallet, None)
            async with _state_lock:
                _stats["last_recovery_ts"] = now_ts.isoformat()
                _stats["recovery_count"] = int(_stats.get("recovery_count") or 0) + 1
    existing_wallets = set(_worker_tasks.keys())

    for wallet in existing_wallets - tracked_wallets:
        task = _worker_tasks.pop(wallet, None)
        if task and not task.done():
            task.cancel()
        _latest_by_wallet.pop(wallet, None)
        _worker_started_at.pop(wallet, None)

    for wallet in tracked_wallets - existing_wallets:
        loop = asyncio.get_event_loop()
        _worker_tasks[wallet] = loop.create_task(_wallet_worker(wallet), name=f"wallet_tx_feed_{wallet[:8]}")
        _worker_started_at[wallet] = datetime.now(timezone.utc)


async def maintain_workers() -> None:
    if not _started:
        return
    await _sync_workers()


def start() -> None:
    global _started
    if _started:
        return
    _started = True
    logger.info("wallet_tx_feed: started")


def stop() -> None:
    global _started
    _started = False
    for task in list(_worker_tasks.values()):
        if not task.done():
            task.cancel()
    _worker_tasks.clear()
    _worker_started_at.clear()
    logger.info("wallet_tx_feed: stopped")
