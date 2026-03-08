"""Telegram alert helper for Abrons Orchestrator (Patch 91).

Provides sync (for use in sync _fire_alert) and async (for use in async loops)
send functions with per-alert-type rate limiting.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time

log = logging.getLogger(__name__)

# Minimum seconds between consecutive sends of the same alert_type
RATE_LIMIT_S: int = 300   # 5 minutes

_tg_last: dict[str, float] = {}   # alert_type → epoch of last send


def should_rate_limit(alert_type: str, limit_s: int = RATE_LIMIT_S) -> bool:
    """Return True (suppress) if this alert_type was sent too recently.

    Side-effect: records the new send timestamp when NOT rate-limited.
    """
    last = _tg_last.get(alert_type, 0.0)
    if time.time() - last < limit_s:
        return True
    _tg_last[alert_type] = time.time()
    return False


def send_telegram_sync(title: str, body: str, emoji: str = "🤖") -> bool:
    """Synchronous Telegram send — for use inside sync functions.

    Uses the `requests` library (already installed).
    Returns True on success, False on failure or if not configured.
    """
    token = os.getenv("TELEGRAM_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        return False

    import requests  # noqa: PLC0415 (lazy import fine here)
    text = f"{emoji} <b>{title}</b>\n{body}"
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            timeout=8,
        )
        if r.status_code != 200:
            log.debug("telegram HTTP %s: %s", r.status_code, r.text[:200])
            return False
        return True
    except Exception as exc:
        log.debug("telegram send_sync error: %s", exc)
        return False


async def send_telegram(title: str, body: str, emoji: str = "🤖") -> bool:
    """Async Telegram send — for use inside async loops.

    Delegates to send_telegram_sync() in a thread pool to avoid blocking
    the event loop on the HTTP call.
    """
    return await asyncio.to_thread(send_telegram_sync, title, body, emoji)
