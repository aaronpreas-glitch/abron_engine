from __future__ import annotations

import json
import math
import os
import time
from datetime import datetime, timezone
from typing import Any

_ALLOCATOR_WRITE_THROTTLE: dict[str, float] = {"last_write": 0.0}


def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except Exception:
        return default


def _now_iso(ts: float | None = None) -> str:
    return datetime.fromtimestamp(ts or time.time(), timezone.utc).isoformat()


def _clean_provider(provider: str) -> str:
    return str(provider or "").strip().lower()


def _provider_limit(provider: str) -> tuple[int, int, int]:
    key = _clean_provider(provider).upper().replace("-", "_")
    clean = _clean_provider(provider)
    default_profiles = {
        "dexscreener": (30, 5),
        "dexscreener_spot": (24, 4),
        "geckoterminal": (12, 3),
        "helius": (12, 3),
    }
    default_max, default_reserve = default_profiles.get(clean, (24, 4))
    window_s = _env_int(f"{key}_BUDGET_WINDOW_SECONDS", _env_int("PROVIDER_BUDGET_WINDOW_SECONDS", 60))
    max_requests = _env_int(f"{key}_BUDGET_MAX_REQUESTS", _env_int("PROVIDER_BUDGET_MAX_REQUESTS", default_max))
    reserve = _env_int(f"{key}_BUDGET_RESERVE_REQUESTS", _env_int("PROVIDER_BUDGET_RESERVE_REQUESTS", default_reserve))
    return window_s, max_requests, min(reserve, max_requests - 1 if max_requests > 1 else 0)


def _clean_lane(lane: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in str(lane or "default").strip().lower()).strip("_") or "default"


def _split_env_csv(name: str, default: str) -> set[str]:
    raw = os.getenv(name, default)
    return {_clean_lane(item) for item in str(raw or "").split(",") if _clean_lane(item)}


def _lane_matches(lane: str, patterns: set[str]) -> bool:
    clean_lane = _clean_lane(lane)
    return any(pattern and pattern in clean_lane for pattern in patterns)


def _critical_lane(lane: str) -> bool:
    patterns = _split_env_csv(
        "PROVIDER_BUDGET_CRITICAL_LANES",
        "token_pairs,token_identity,identity_guard,watch_to_entry,runner,manual_review,entry,proof,wallet_cluster_funding",
    )
    return _lane_matches(lane, patterns)


def _dashboard_lane(lane: str) -> bool:
    patterns = _split_env_csv(
        "PROVIDER_BUDGET_DASHBOARD_LANES",
        "dashboard,system_audit,home,ui,summary,status",
    )
    return _lane_matches(lane, patterns)


def _lane_cap(provider: str, lane: str, *, usable_limit: int, max_requests: int) -> int:
    clean_provider = _clean_provider(provider).upper().replace("-", "_")
    clean_lane = _clean_lane(lane).upper()
    for key in (
        f"{clean_provider}_BUDGET_LANE_CAP_{clean_lane}",
        f"PROVIDER_BUDGET_LANE_CAP_{clean_provider}_{clean_lane}",
    ):
        raw = os.getenv(key)
        if raw:
            try:
                return max(1, min(int(raw), max_requests))
            except Exception:
                pass
    try:
        caps = json.loads(os.getenv("PROVIDER_BUDGET_LANE_CAPS_JSON", "{}") or "{}")
        if isinstance(caps, dict):
            provider_caps = caps.get(_clean_provider(provider)) or caps.get(clean_provider) or {}
            if isinstance(provider_caps, dict):
                raw_cap = provider_caps.get(str(lane)) or provider_caps.get(_clean_lane(lane)) or provider_caps.get(clean_lane)
                if raw_cap is not None:
                    return max(1, min(int(raw_cap), max_requests))
    except Exception:
        pass
    default_fraction = 0.90 if _critical_lane(lane) else (0.35 if _dashboard_lane(lane) else 0.75)
    try:
        fraction = float(os.getenv("PROVIDER_BUDGET_DEFAULT_LANE_MAX_FRACTION", str(default_fraction)))
    except Exception:
        fraction = default_fraction
    fraction = max(0.10, min(1.0, fraction))
    return max(1, min(usable_limit, int(math.ceil(max(1, usable_limit) * fraction))))


def _empty_snapshot(provider: str) -> dict[str, Any]:
    window_s, max_requests, reserve = _provider_limit(provider)
    now = time.time()
    window_start = int(now // window_s) * window_s
    return {
        "provider": _clean_provider(provider),
        "window_seconds": window_s,
        "max_requests": max_requests,
        "reserve_requests": reserve,
        "window_start": window_start,
        "reset_at": _now_iso(window_start + window_s),
        "used": 0,
        "remaining": max_requests,
        "lanes": {},
        "last_lane": None,
        "last_reason": None,
        "updated_at": _now_iso(now),
    }


def _load_budget(conn, provider: str) -> dict[str, Any]:
    key = f"provider_budget:{_clean_provider(provider)}"
    row = conn.execute("SELECT value FROM kv_store WHERE key=?", (key,)).fetchone()
    if not row or not row[0]:
        return _empty_snapshot(provider)
    try:
        payload = json.loads(row[0])
    except Exception:
        return _empty_snapshot(provider)
    return payload if isinstance(payload, dict) else _empty_snapshot(provider)


def _save_budget(conn, provider: str, payload: dict[str, Any]) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO kv_store (key, value) VALUES (?, ?)",
        (f"provider_budget:{_clean_provider(provider)}", json.dumps(payload, separators=(",", ":"))),
    )


def provider_budget_snapshot(provider: str) -> dict[str, Any]:
    clean = _clean_provider(provider)
    if not clean:
        return {}
    try:
        from utils.db import get_conn  # type: ignore

        with get_conn() as conn:
            payload = _load_budget(conn, clean)
        window_s, max_requests, reserve = _provider_limit(clean)
        now = time.time()
        window_start = int(now // window_s) * window_s
        if int(payload.get("window_start") or 0) != window_start:
            return _empty_snapshot(clean)
        used = int(payload.get("used") or 0)
        payload.update(
            {
                "provider": clean,
                "window_seconds": window_s,
                "max_requests": max_requests,
                "reserve_requests": reserve,
                "reset_at": _now_iso(window_start + window_s),
                "remaining": max(0, max_requests - used),
            }
        )
        payload["pressure_pct"] = round(used / max(max_requests, 1) * 100.0, 1)
        return payload
    except Exception:
        return _empty_snapshot(clean)


def provider_budget_allow(
    provider: str,
    *,
    cost: int = 1,
    lane: str = "default",
    reserve: bool = False,
) -> dict[str, Any]:
    """
    Persistent provider request budget.

    Denies before 429s happen. Fails open only on local bookkeeping errors so the
    engine does not silently brick if kv_store is unavailable.
    """
    clean = _clean_provider(provider)
    if not clean:
        return {"allowed": False, "reason": "missing_provider"}
    cost = max(1, int(cost or 1))
    lane = str(lane or "default").strip()[:80] or "default"
    critical_lane = _critical_lane(lane)
    dashboard_lane = _dashboard_lane(lane)
    effective_reserve = bool(reserve or critical_lane)
    try:
        from utils.db import get_conn, provider_in_cooldown  # type: ignore

        cooldown_active, cooldown_state = provider_in_cooldown(clean)
        if cooldown_active:
            snap = provider_budget_snapshot(clean)
            return {
                **snap,
                "allowed": False,
                "reason": "provider_cooldown",
                "cooldown_until": cooldown_state.get("cooldown_until"),
                "lane": lane,
            }

        window_s, max_requests, reserve_requests = _provider_limit(clean)
        now = time.time()
        window_start = int(now // window_s) * window_s
        with get_conn() as conn:
            payload = _load_budget(conn, clean)
            if int(payload.get("window_start") or 0) != window_start:
                payload = _empty_snapshot(clean)

            used = int(payload.get("used") or 0)
            usable_limit = max_requests if effective_reserve else max(0, max_requests - reserve_requests)
            lanes = payload.get("lanes") if isinstance(payload.get("lanes"), dict) else {}
            lane_used = int(lanes.get(lane) or 0)
            lane_cap = max_requests if effective_reserve else _lane_cap(clean, lane, usable_limit=usable_limit, max_requests=max_requests)
            allowed = used + cost <= usable_limit and (effective_reserve or lane_used + cost <= lane_cap)
            reason = "allowed" if allowed else "budget_exhausted"
            if not effective_reserve and used + cost <= usable_limit and lane_used + cost > lane_cap:
                reason = "lane_budget_exhausted"
            if allowed:
                lanes[lane] = int(lanes.get(lane) or 0) + cost
                used += cost
                payload.update(
                    {
                        "used": used,
                        "remaining": max(0, max_requests - used),
                        "lanes": lanes,
                        "last_lane": lane,
                        "last_reason": reason,
                        "last_lane_cap": lane_cap,
                        "updated_at": _now_iso(now),
                    }
                )
                _save_budget(conn, clean, payload)

        return {
            **payload,
            "allowed": bool(allowed),
            "reason": reason,
            "lane": lane,
            "window_seconds": window_s,
            "max_requests": max_requests,
            "reserve_requests": reserve_requests,
            "reset_at": _now_iso(window_start + window_s),
            "remaining": max(0, max_requests - used),
            "pressure_pct": round(used / max(max_requests, 1) * 100.0, 1),
            "lane_used": lane_used,
            "lane_cap": lane_cap,
            "reserve": effective_reserve,
            "critical_lane": critical_lane,
            "dashboard_lane": dashboard_lane,
            "relief_policy": "critical_reserve" if critical_lane else ("dashboard_throttled" if dashboard_lane else "standard"),
        }
    except Exception as exc:
        return {
            **_empty_snapshot(clean),
            "allowed": True,
            "reason": "budget_fail_open",
            "detail": str(exc),
            "lane": lane,
        }


def provider_budget_allocator_status(providers: list[str] | None = None) -> dict[str, Any]:
    names = providers or ["dexscreener", "dexscreener_spot", "geckoterminal"]
    snapshots = {name: provider_budget_snapshot(name) for name in names}
    hot: list[str] = []
    constrained: list[str] = []
    for name, snap in snapshots.items():
        remaining = int(snap.get("remaining") or 0)
        reserve = int(snap.get("reserve_requests") or 0)
        pressure = float(snap.get("pressure_pct") or 0.0)
        if pressure >= 80.0:
            hot.append(name)
        if remaining <= reserve:
            constrained.append(name)
    status = "CONSTRAINED" if constrained else ("HOT" if hot else "HEALTHY")
    return {
        "watchdog": "PROVIDER_BUDGET_ALLOCATOR",
        "status": status,
        "checked_at": _now_iso(),
        "providers": snapshots,
        "hot_providers": hot,
        "constrained_providers": constrained,
        "default_lane_max_fraction": float(os.getenv("PROVIDER_BUDGET_DEFAULT_LANE_MAX_FRACTION", "0.75")),
        "relief": {
            "critical_lanes": sorted(_split_env_csv(
                "PROVIDER_BUDGET_CRITICAL_LANES",
                "token_pairs,token_identity,identity_guard,watch_to_entry,runner,manual_review,entry,proof,wallet_cluster_funding",
            )),
            "dashboard_lanes": sorted(_split_env_csv(
                "PROVIDER_BUDGET_DASHBOARD_LANES",
                "dashboard,system_audit,home,ui,summary,status",
            )),
            "allocator_write_min_seconds": float(os.getenv("PROVIDER_BUDGET_ALLOCATOR_WRITE_MIN_SECONDS", "20")),
        },
        "detail": (
            f"Provider budget constrained: {', '.join(constrained)}"
            if constrained else
            f"Provider budget hot: {', '.join(hot)}"
            if hot else
            "Provider budget allocator healthy."
        ),
    }


def refresh_provider_budget_allocator_status() -> dict[str, Any]:
    payload = provider_budget_allocator_status()
    try:
        from utils.db import get_conn, with_db_retry  # type: ignore

        min_write_s = max(1.0, float(os.getenv("PROVIDER_BUDGET_ALLOCATOR_WRITE_MIN_SECONDS", "20")))
        now = time.monotonic()
        if now - float(_ALLOCATOR_WRITE_THROTTLE.get("last_write") or 0.0) < min_write_s:
            payload["persisted"] = False
            payload["persist_skip_reason"] = "write_throttled"
            return payload

        def _write() -> None:
            encoded = json.dumps(payload, separators=(",", ":"))
            with get_conn() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO kv_store (key, value) VALUES (?, ?)",
                    ("provider_budget_allocator_status", encoded),
                )
                conn.execute(
                    "INSERT OR REPLACE INTO kv_store (key, value) VALUES (?, ?)",
                    ("provider_budget_status", encoded),
                )

        with_db_retry(_write, retries=2, base_sleep_s=0.1)
        _ALLOCATOR_WRITE_THROTTLE["last_write"] = now
        payload["persisted"] = True
    except Exception:
        payload["persisted"] = False
        payload["persist_skip_reason"] = "write_failed"
        pass
    return payload
