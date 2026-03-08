"""
Funding Rate Monitor — Patch 144

Fetches perpetual futures funding rates for SOL, BTC, ETH from OKX
(no geo-restriction on DigitalOcean NYC VPS — Binance/Bybit are blocked).
Stores snapshots every 30 min. Caches current state in kv_store.
Fires Telegram alerts at extreme thresholds.

Called from monitor loop: first cycle + every 30 min.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone

import requests

log = logging.getLogger(__name__)

FUNDING_KEY = "shared_funding_rates"

_SYMBOLS: dict[str, str] = {
    "SOL": "SOL-USDT-SWAP",
    "BTC": "BTC-USDT-SWAP",
    "ETH": "ETH-USDT-SWAP",
}

# Alert thresholds (per-8h rate as decimal)
_ALERT_HIGH = 0.001    # +0.1% — longs overheated
_ALERT_LOW  = -0.0003  # -0.03% — shorts dominating


# ── KV helpers (mirrors agent_coordinator.py pattern) ─────────────────────────

def _get_conn():
    from utils.db import get_conn  # type: ignore
    return get_conn()


def _kv_get(key: str):
    try:
        with _get_conn() as conn:
            row = conn.execute(
                "SELECT value FROM kv_store WHERE key=?", (key,)
            ).fetchone()
            return json.loads(row[0]) if row else None
    except Exception:
        return None


def _kv_set(key: str, value: dict) -> None:
    try:
        with _get_conn() as conn:
            conn.execute(
                "INSERT INTO kv_store (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(value)),
            )
    except Exception:
        pass


# ── API fetch (OKX — no geo-restriction on DO NYC VPS) ────────────────────────

def _fetch_okx(inst_id: str) -> dict | None:
    """Fetch current funding rate from OKX perpetual swap endpoint."""
    try:
        r = requests.get(
            f"https://www.okx.com/api/v5/public/funding-rate?instId={inst_id}",
            timeout=8,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        if r.status_code != 200:
            log.debug("[FUND] OKX %s → HTTP %d", inst_id, r.status_code)
            return None
        d = r.json()
        if d.get("code") != "0":
            log.debug("[FUND] OKX %s → code=%s msg=%s", inst_id, d.get("code"), d.get("msg"))
            return None
        lst = d.get("data", [])
        if not lst:
            return None
        item = lst[0]
        next_ts = int(item["fundingTime"]) if item.get("fundingTime") else None
        return {
            "rate":            float(item["fundingRate"]),
            "mark_price":      None,   # OKX funding-rate endpoint doesn't include mark price
            "next_funding_ts": next_ts,
            "source":          "okx",
        }
    except Exception as e:
        log.debug("[FUND] OKX fetch failed for %s: %s", inst_id, e)
        return None


def _fetch_one(label: str, inst_id: str) -> dict | None:
    return _fetch_okx(inst_id)


# ── DB snapshot ───────────────────────────────────────────────────────────────

def _store_snapshot(label: str, data: dict) -> None:
    try:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        with _get_conn() as conn:
            conn.execute(
                "INSERT INTO funding_snapshots "
                "(ts_utc, symbol, rate, mark_price, next_funding_ts) "
                "VALUES (?, ?, ?, ?, ?)",
                (ts, label, data["rate"], data.get("mark_price"), data.get("next_funding_ts")),
            )
    except Exception as e:
        log.debug("[FUND] snapshot store error for %s: %s", label, e)


# ── Telegram alert ────────────────────────────────────────────────────────────

def _check_alert(label: str, rate: float) -> None:
    try:
        from utils.telegram_alerts import send_telegram_sync, should_rate_limit  # type: ignore
        pct = rate * 100

        if rate > _ALERT_HIGH:
            if not should_rate_limit(f"funding_high_{label}", 28800):
                send_telegram_sync(
                    f"Funding Overheated ⚠️ {label}",
                    f"{label} perp funding rate: +{pct:.3f}%/8h\n"
                    "Longs paying heavily — avoid opening new longs.",
                    "⚠️",
                )
                log.warning("[FUND] %s overheated rate=%.4f%%/8h", label, pct)
        elif rate < _ALERT_LOW:
            if not should_rate_limit(f"funding_low_{label}", 28800):
                send_telegram_sync(
                    f"Funding Negative 🔵 {label}",
                    f"{label} perp funding rate: {pct:.3f}%/8h\n"
                    "Shorts dominating — longs earning funding.",
                    "🔵",
                )
                log.info("[FUND] %s negative rate=%.4f%%/8h", label, pct)
    except Exception as e:
        log.debug("[FUND] alert error for %s: %s", label, e)


# ── Public interface ──────────────────────────────────────────────────────────

def get_funding_rates() -> dict:
    """Return cached funding rates for SOL, BTC, ETH. TTL: 35 min. Returns {} if stale."""
    cached = _kv_get(FUNDING_KEY)
    if cached:
        age_min = (time.time() - cached.get("_ts", 0)) / 60
        if age_min < 35:
            return cached
    return {}


def funding_step() -> None:
    """
    Fetch current funding rates and update kv_store + DB snapshots.
    Called on monitor cycle 1 (startup) and every 30 min thereafter.
    """
    from utils import orchestrator  # type: ignore

    result: dict = {"_ts": time.time()}
    fetched = 0

    for label, inst_id in _SYMBOLS.items():
        data = _fetch_one(label, inst_id)
        if not data:
            log.warning("[FUND] Could not fetch %s from OKX", label)
            continue
        result[label] = {
            "rate":            data["rate"],
            "mark_price":      data.get("mark_price"),
            "next_funding_ts": data.get("next_funding_ts"),
            "source":          data.get("source", "okx"),
        }
        _store_snapshot(label, data)
        _check_alert(label, data["rate"])
        fetched += 1
        log.debug("[FUND] %s rate=%.5f next_ts=%s src=%s",
                  label, data["rate"],
                  data.get("next_funding_ts"), data.get("source"))

    if fetched > 0:
        _kv_set(FUNDING_KEY, result)
        log.info("[FUND] funding_step complete: %d/%d symbols (OKX)", fetched, len(_SYMBOLS))
    else:
        log.warning("[FUND] funding_step: no symbols fetched — OKX unavailable")

    orchestrator.heartbeat("funding_monitor")
