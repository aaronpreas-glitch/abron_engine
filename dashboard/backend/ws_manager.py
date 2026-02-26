"""
ws_manager.py — WebSocket connection manager + DB signal poller.

Polls the signals table every 3 seconds for new rows and broadcasts
to all authenticated WebSocket clients. Never writes to the DB.
"""
from __future__ import annotations

import asyncio
import logging
import sqlite3
from pathlib import Path
from typing import Any

from fastapi import WebSocket

log = logging.getLogger("dashboard.ws")

# Resolve DB path relative to this file (../../data_storage/engine.db)
_DB_PATH = Path(__file__).resolve().parents[2] / "data_storage" / "engine.db"
_POLL_INTERVAL = 3  # seconds


def _get_max_id() -> int:
    try:
        with sqlite3.connect(f"file:{_DB_PATH}?mode=ro", uri=True) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT COALESCE(MAX(id), 0) AS m FROM signals").fetchone()
            return int(row["m"])
    except Exception:
        return 0


def _get_signals_since(last_id: int) -> list[dict]:
    """Return all signal rows with id > last_id, limited to alert-type decisions."""
    try:
        with sqlite3.connect(f"file:{_DB_PATH}?mode=ro", uri=True) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT id, ts_utc, symbol, mint, score_total, decision,
                       regime_score, regime_label, liquidity_usd, volume_24h,
                       price_usd, change_24h, notes
                FROM signals
                WHERE id > ?
                ORDER BY id ASC
                LIMIT 50
                """,
                (last_id,),
            ).fetchall()
            return [dict(r) for r in rows]
    except Exception as exc:
        log.warning("signal poll error: %s", exc)
        return []


class ConnectionManager:
    def __init__(self) -> None:
        self._active: list[WebSocket] = []

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._active.append(ws)
        log.info("WS client connected. Total: %d", len(self._active))

    def disconnect(self, ws: WebSocket) -> None:
        if ws in self._active:
            self._active.remove(ws)
        log.info("WS client disconnected. Total: %d", len(self._active))

    async def broadcast(self, message: dict[str, Any]) -> None:
        dead: list[WebSocket] = []
        for ws in list(self._active):
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

    def count(self) -> int:
        return len(self._active)


manager = ConnectionManager()


async def broadcast_trade_event(
    event: str,           # "trade_open" | "trade_close"
    mode: str,            # "SCALP" | "SWING" | "SPOT"
    symbol: str,
    side: str,            # "LONG" | "SHORT"
    entry_price: float,
    exit_price: float | None = None,
    pnl_pct: float | None = None,
    exit_reason: str | None = None,
    size_usd: float | None = None,
    leverage: float | None = None,
) -> None:
    """Broadcast a trade open/close event to all connected WS clients."""
    await manager.broadcast({
        "type": event,
        "data": {
            "mode":        mode,
            "symbol":      symbol,
            "side":        side,
            "entry_price": entry_price,
            "exit_price":  exit_price,
            "pnl_pct":     round(pnl_pct, 2) if pnl_pct is not None else None,
            "exit_reason": exit_reason,
            "size_usd":    size_usd,
            "leverage":    leverage,
            "ts":          __import__("datetime").datetime.utcnow().isoformat() + "Z",
        },
    })


async def signal_poller() -> None:
    """Background task — polls DB for new signals, broadcasts to WS clients."""
    last_id = _get_max_id()
    log.info("Signal poller started. Last known signal id=%d", last_id)
    while True:
        await asyncio.sleep(_POLL_INTERVAL)
        try:
            new_rows = _get_signals_since(last_id)
            for row in new_rows:
                last_id = row["id"]
                await manager.broadcast({"type": "signal", "data": row})
            if new_rows:
                log.debug("Broadcast %d new signal(s)", len(new_rows))
        except Exception as exc:
            log.exception("signal_poller error: %s", exc)
