from __future__ import annotations

import asyncio
import copy
import json
import logging
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger(__name__)

_locks: dict[str, asyncio.Lock] = {}
_last_refresh_started: dict[str, float] = {}
_memory_snapshots: dict[str, dict] = {}
_READ_TIMEOUT_SECONDS = max(0.2, float(os.getenv("DASHBOARD_SNAPSHOT_READ_TIMEOUT_SECONDS", "1.0")))
_WRITE_TIMEOUT_SECONDS = max(1.0, float(os.getenv("DASHBOARD_SNAPSHOT_WRITE_TIMEOUT_SECONDS", "6.0")))
_MEMORY_READ_THROUGH_SECONDS = max(1.0, float(os.getenv("DASHBOARD_SNAPSHOT_MEMORY_READ_THROUGH_SECONDS", "10.0")))
_WRITE_RETRIES = max(1, int(os.getenv("DASHBOARD_SNAPSHOT_WRITE_RETRIES", "5")))
_LIVE_BUILD_TIMEOUT_SECONDS = max(0.2, float(os.getenv("DASHBOARD_SNAPSHOT_LIVE_BUILD_TIMEOUT_SECONDS", "1.5")))
_STORE_LIVE_BUILDS_ENABLED = os.getenv("DASHBOARD_SNAPSHOT_STORE_LIVE_BUILDS_ENABLED", "true").lower() in (
    "1",
    "true",
    "yes",
)
_SCHEDULE_REFRESH_ENABLED = os.getenv("DASHBOARD_SNAPSHOT_SCHEDULE_REFRESH_ENABLED", "false").lower() in (
    "1",
    "true",
    "yes",
)


def _db_path() -> Path:
    return Path(__file__).resolve().parents[2] / "data_storage" / "engine.db"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_ts(value: Any) -> float | None:
    if not value:
        return None
    try:
        text = str(value).replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception:
        return None


def _snapshot_key(name: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in (":", "_", "-", ".") else "_" for ch in str(name))
    return f"dashboard_snapshot:{safe}"


def _snapshot_with_age(payload: dict) -> dict | None:
    if not isinstance(payload, dict) or "data" not in payload:
        return None
    updated_ts = _parse_ts(payload.get("updated_at"))
    payload = copy.deepcopy(payload)
    payload["_age_seconds"] = None if updated_ts is None else max(0.0, time.time() - updated_ts)
    return payload


def load_snapshot(name: str) -> dict | None:
    cached = _snapshot_with_age(_memory_snapshots.get(name) or {})
    if cached is not None and float(cached.get("_age_seconds") or 10**9) <= _MEMORY_READ_THROUGH_SECONDS:
        return cached

    try:
        db_path = _db_path()
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=_READ_TIMEOUT_SECONDS) as conn:
            conn.execute(f"PRAGMA busy_timeout={int(_READ_TIMEOUT_SECONDS * 1000)}")
            row = conn.execute("SELECT value FROM kv_store WHERE key=?", (_snapshot_key(name),)).fetchone()
            if not row or not row[0]:
                return None
            payload = json.loads(row[0])
            payload = _snapshot_with_age(payload)
            if payload is None:
                return None
            _memory_snapshots[name] = copy.deepcopy(payload)
            return payload
    except Exception as exc:
        log.debug("snapshot load failed for %s: %s", name, exc)
        return cached


def store_snapshot(name: str, data: Any, *, status: str = "OK", error: str | None = None) -> dict:
    payload = {
        "name": name,
        "updated_at": _utc_now_iso(),
        "status": status,
        "error": error,
        "data": data,
    }
    _memory_snapshots[name] = copy.deepcopy(payload)
    encoded = json.dumps(payload, default=str, separators=(",", ":"))
    last_exc: Exception | None = None
    for attempt in range(_WRITE_RETRIES):
        try:
            try:
                from utils.db import db_process_lock  # type: ignore
            except Exception:
                from contextlib import nullcontext
                db_process_lock = nullcontext  # type: ignore

            with db_process_lock():
                with sqlite3.connect(str(_db_path()), timeout=_WRITE_TIMEOUT_SECONDS) as conn:
                    conn.execute(f"PRAGMA busy_timeout={int(_WRITE_TIMEOUT_SECONDS * 1000)}")
                    conn.execute(
                        "INSERT OR REPLACE INTO kv_store (key, value) VALUES (?, ?)",
                        (_snapshot_key(name), encoded),
                    )
                    conn.commit()
            last_exc = None
            break
        except Exception as exc:
            last_exc = exc
            if "database is locked" not in str(exc).lower() and "database table is locked" not in str(exc).lower():
                break
            time.sleep(0.2 * (2 ** attempt))
    if last_exc is not None:
        log.warning("snapshot store failed for %s after %d attempt(s): %s", name, _WRITE_RETRIES, last_exc)
    return payload


def _with_snapshot_meta(payload: Any, snapshot: dict, *, stale: bool) -> Any:
    data = copy.deepcopy(payload)
    if isinstance(data, dict):
        data["_snapshot"] = {
            "name": snapshot.get("name"),
            "updated_at": snapshot.get("updated_at"),
            "age_seconds": snapshot.get("_age_seconds"),
            "status": "STALE" if stale else "FRESH",
            "source": "kv_snapshot",
        }
    return data


def _with_live_meta(payload: Any, name: str) -> Any:
    data = copy.deepcopy(payload)
    if isinstance(data, dict):
        data["_snapshot"] = {
            "name": name,
            "updated_at": _utc_now_iso(),
            "age_seconds": 0.0,
            "status": "FRESH",
            "source": "live_no_store",
        }
    return data


async def _refresh_snapshot(name: str, builder: Callable[[], Any]) -> None:
    lock = _locks.setdefault(name, asyncio.Lock())
    if lock.locked():
        return
    async with lock:
        _last_refresh_started[name] = time.monotonic()
        try:
            result = await asyncio.to_thread(builder)
            store_snapshot(name, result, status="OK")
        except Exception as exc:
            snap = load_snapshot(name)
            store_snapshot(name, snap.get("data") if snap else {}, status="ERROR", error=str(exc))
            log.warning("snapshot refresh failed for %s: %s", name, exc)


def schedule_refresh(name: str, builder: Callable[[], Any], *, min_interval_s: float = 10.0) -> None:
    if not _SCHEDULE_REFRESH_ENABLED:
        return
    now = time.monotonic()
    if now - _last_refresh_started.get(name, 0.0) < min_interval_s:
        return
    try:
        asyncio.get_running_loop().create_task(_refresh_snapshot(name, builder))
    except RuntimeError:
        pass


async def snapshot_or_build(
    name: str,
    builder: Callable[[], Any],
    *,
    fresh_s: float,
    stale_s: float,
    wait_timeout_s: float = 8.0,
) -> Any:
    snap = load_snapshot(name)
    age = float(snap.get("_age_seconds") if snap else 10**9)
    if snap and age <= fresh_s:
        return _with_snapshot_meta(snap.get("data"), snap, stale=False)
    if not _SCHEDULE_REFRESH_ENABLED:
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(builder),
                timeout=min(float(wait_timeout_s), _LIVE_BUILD_TIMEOUT_SECONDS),
            )
            if _STORE_LIVE_BUILDS_ENABLED:
                try:
                    await asyncio.wait_for(
                        asyncio.to_thread(store_snapshot, name, result, status="OK"),
                        timeout=_WRITE_TIMEOUT_SECONDS + 1.0,
                    )
                except Exception as store_exc:
                    log.debug("snapshot live store failed for %s: %s", name, store_exc)
            return _with_live_meta(result, name)
        except Exception as exc:
            if snap and snap.get("data") not in (None, {}, []):
                log.debug("snapshot live build failed for %s, serving stale: %s", name, exc)
                return _with_snapshot_meta(snap.get("data"), snap, stale=True)
            raise
    if snap and age <= stale_s:
        schedule_refresh(name, builder)
        return _with_snapshot_meta(snap.get("data"), snap, stale=True)
    if snap and snap.get("data") not in (None, {}, []):
        schedule_refresh(name, builder, min_interval_s=1.0)
        return _with_snapshot_meta(snap.get("data"), snap, stale=True)

    schedule_refresh(name, builder, min_interval_s=1.0)
    raise TimeoutError(f"snapshot_warming:{name}:retry_shortly")
