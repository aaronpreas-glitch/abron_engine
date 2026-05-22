"""
LunarCrush social intelligence enrichment helpers.

Designed as a fail-open enrichment layer:
  - if LUNARCRUSH_API_KEY is missing, callers get a NOT_CONFIGURED result
  - if LunarCrush errors, callers get an ERROR/PARTIAL result and core system
    behavior continues

Current integration scope:
  - Solana-focused topic search / summary
  - topic time-series for simple social velocity
  - top creator snapshot
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

log = logging.getLogger(__name__)

_BASE_URL = os.getenv("LUNARCRUSH_API_BASE_URL", "https://lunarcrush.ai").rstrip("/")
_TIMEOUT_S = float(os.getenv("LUNARCRUSH_TIMEOUT_S", "15") or 15)
_CACHE_HOURS = float(os.getenv("LUNARCRUSH_CACHE_HOURS", "12") or 12)
_MAX_CREATORS = int(os.getenv("LUNARCRUSH_MAX_CREATORS", "5") or 5)
_ERROR_CACHE_MINUTES = float(os.getenv("LUNARCRUSH_ERROR_CACHE_MINUTES", "90") or 90)
_RATE_LIMIT_CACHE_MINUTES = float(os.getenv("LUNARCRUSH_RATE_LIMIT_CACHE_MINUTES", "240") or 240)


def is_lunarcrush_configured() -> bool:
    return bool(os.getenv("LUNARCRUSH_API_KEY", "").strip())


def ensure_lunarcrush_tables(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS lunarcrush_topic_intel (
            symbol TEXT PRIMARY KEY,
            topic TEXT,
            title TEXT,
            topic_rank REAL,
            topic_rank_prev_24h REAL,
            topic_rank_change_24h REAL,
            num_contributors REAL,
            num_posts REAL,
            interactions_24h REAL,
            galaxy_score REAL,
            alt_rank REAL,
            social_dominance REAL,
            social_velocity_score REAL,
            creator_count INTEGER DEFAULT 0,
            top_creators_json TEXT,
            supportive_summary TEXT,
            critical_summary TEXT,
            summary TEXT,
            narrative_state TEXT,
            status TEXT NOT NULL DEFAULT 'PENDING',
            raw_json TEXT,
            last_error TEXT,
            updated_ts_utc TEXT NOT NULL
        )
        """
    )


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {os.getenv('LUNARCRUSH_API_KEY', '').strip()}",
        "User-Agent": "memecoin-engine/lunarcrush-enrichment",
        "Accept": "application/json",
    }


def _request(path: str, **params) -> Any:
    try:
        resp = requests.get(
            f"{_BASE_URL}{path}",
            headers=_headers(),
            timeout=_TIMEOUT_S,
            params={**params, "format": "json"},
        )
        resp.raise_for_status()
        return resp.json()
    except requests.HTTPError as exc:
        status = getattr(exc.response, "status_code", None)
        if status == 429:
            raise RuntimeError("rate_limited:429") from exc
        raise


def _scalar(obj: Any, *keys: str, default: float | int | str | None = None):
    if not isinstance(obj, dict):
        return default
    for key in keys:
        if key in obj and obj[key] is not None:
            return obj[key]
    return default


def _to_float(value: Any) -> float:
    try:
        if value in (None, ""):
            return 0.0
        return float(value)
    except Exception:
        return 0.0


def _extract_rows(payload: Any) -> list[dict]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        for key in ("data", "rows", "series", "items", "results"):
            rows = payload.get(key)
            if isinstance(rows, list):
                return [x for x in rows if isinstance(x, dict)]
    return []


def _normalize_search_symbol(symbol: str) -> str:
    return str(symbol or "").strip().replace("$", "").lower()


def _match_topic(symbol: str, payload: Any) -> tuple[str, str]:
    target = _normalize_search_symbol(symbol)
    rows = _extract_rows(payload)
    for row in rows:
        topic = str(_scalar(row, "topic", "name", "symbol", default="") or "").strip()
        title = str(_scalar(row, "title", "name", default=topic) or topic).strip()
        if _normalize_search_symbol(topic) == target or _normalize_search_symbol(title) == target:
            return topic.lower(), title
    return target, symbol.upper()


def _creator_name(row: dict) -> str | None:
    for key in ("display_name", "displayName", "name", "username", "handle"):
        val = row.get(key)
        if val:
            return str(val)
    return None


def _velocity_from_rows(rows: list[dict]) -> float:
    if len(rows) < 2:
        return 0.0
    latest = rows[-1]
    prev = rows[-2]
    latest_val = max(
        _to_float(_scalar(latest, "interactions_24h", "interactions", "engagements")),
        _to_float(_scalar(latest, "num_posts", "posts")),
        _to_float(_scalar(latest, "num_contributors", "contributors")),
    )
    prev_val = max(
        _to_float(_scalar(prev, "interactions_24h", "interactions", "engagements")),
        _to_float(_scalar(prev, "num_posts", "posts")),
        _to_float(_scalar(prev, "num_contributors", "contributors")),
    )
    if latest_val <= 0:
        return 0.0
    if prev_val <= 0:
        return 100.0
    return round(((latest_val - prev_val) / prev_val) * 100.0, 1)


def _pick_summary_text(payload: Any) -> tuple[str | None, str | None, str | None]:
    if not isinstance(payload, dict):
        return None, None, None
    summary = payload.get("summary")
    supportive = payload.get("supportive")
    critical = payload.get("critical")

    def _desc(rows: Any) -> str | None:
        if not isinstance(rows, list):
            return None
        parts: list[str] = []
        for item in rows[:2]:
            if not isinstance(item, dict):
                continue
            txt = item.get("title") or item.get("description")
            if txt:
                parts.append(str(txt).strip())
        return " | ".join(parts) if parts else None

    return (
        str(summary).strip() if summary else None,
        _desc(supportive),
        _desc(critical),
    )


def _narrative_state(topic_rank: float, interactions_24h: float, social_velocity: float, creator_count: int) -> str:
    if interactions_24h >= 100_000 and social_velocity >= 25 and 0 < topic_rank <= 250:
        return "SOCIAL_BREAKOUT"
    if social_velocity >= 10 or creator_count >= 3:
        return "EARLY_NARRATIVE"
    if interactions_24h > 0 and creator_count <= 1:
        return "LOW_QUALITY_CHATTER"
    return "QUIET"


def _upsert_cache(conn, symbol: str, data: dict) -> None:
    ensure_lunarcrush_tables(conn)
    conn.execute(
        """
        INSERT INTO lunarcrush_topic_intel (
            symbol, topic, title, topic_rank, topic_rank_prev_24h, topic_rank_change_24h,
            num_contributors, num_posts, interactions_24h, galaxy_score, alt_rank,
            social_dominance, social_velocity_score, creator_count, top_creators_json,
            supportive_summary, critical_summary, summary, narrative_state, status,
            raw_json, last_error, updated_ts_utc
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(symbol) DO UPDATE SET
            topic=excluded.topic,
            title=excluded.title,
            topic_rank=excluded.topic_rank,
            topic_rank_prev_24h=excluded.topic_rank_prev_24h,
            topic_rank_change_24h=excluded.topic_rank_change_24h,
            num_contributors=excluded.num_contributors,
            num_posts=excluded.num_posts,
            interactions_24h=excluded.interactions_24h,
            galaxy_score=excluded.galaxy_score,
            alt_rank=excluded.alt_rank,
            social_dominance=excluded.social_dominance,
            social_velocity_score=excluded.social_velocity_score,
            creator_count=excluded.creator_count,
            top_creators_json=excluded.top_creators_json,
            supportive_summary=excluded.supportive_summary,
            critical_summary=excluded.critical_summary,
            summary=excluded.summary,
            narrative_state=excluded.narrative_state,
            status=excluded.status,
            raw_json=excluded.raw_json,
            last_error=excluded.last_error,
            updated_ts_utc=excluded.updated_ts_utc
        """,
        (
            symbol,
            data.get("topic"),
            data.get("title"),
            data.get("topic_rank"),
            data.get("topic_rank_prev_24h"),
            data.get("topic_rank_change_24h"),
            data.get("num_contributors"),
            data.get("num_posts"),
            data.get("interactions_24h"),
            data.get("galaxy_score"),
            data.get("alt_rank"),
            data.get("social_dominance"),
            data.get("social_velocity_score"),
            data.get("creator_count", 0),
            json.dumps(data.get("top_creators") or []),
            data.get("supportive_summary"),
            data.get("critical_summary"),
            data.get("summary"),
            data.get("narrative_state"),
            data.get("status", "PENDING"),
            json.dumps(data.get("raw_json") or {}),
            data.get("last_error"),
            data.get("updated_ts_utc"),
        ),
    )
    conn.commit()


def _load_cached(conn, symbol: str) -> dict | None:
    ensure_lunarcrush_tables(conn)
    row = conn.execute(
        "SELECT * FROM lunarcrush_topic_intel WHERE symbol=?",
        (symbol.upper(),),
    ).fetchone()
    if not row:
        return None
    updated = None
    try:
        updated = datetime.fromisoformat(str(row["updated_ts_utc"]).replace("Z", "+00:00"))
    except Exception:
        updated = None
    fresh = bool(updated and updated >= datetime.now(timezone.utc) - timedelta(hours=_CACHE_HOURS))
    data = dict(row)
    try:
        data["top_creators"] = json.loads(data.get("top_creators_json") or "[]")
    except Exception:
        data["top_creators"] = []
    data["fresh"] = fresh
    return data


def _parse_cached_updated(cached: dict | None) -> datetime | None:
    if not isinstance(cached, dict):
        return None
    try:
        raw = str(cached.get("updated_ts_utc") or "").replace("Z", "+00:00")
        return datetime.fromisoformat(raw) if raw else None
    except Exception:
        return None


def _error_retry_due(cached: dict | None) -> bool:
    updated = _parse_cached_updated(cached)
    if not updated:
        return True
    last_error = str((cached or {}).get("last_error") or "").lower()
    cooldown_minutes = _RATE_LIMIT_CACHE_MINUTES if "rate_limited" in last_error or "429" in last_error else _ERROR_CACHE_MINUTES
    return updated <= datetime.now(timezone.utc) - timedelta(minutes=cooldown_minutes)


def get_topic_intel(conn, symbol: str) -> dict:
    symbol = str(symbol or "").strip().upper()
    ensure_lunarcrush_tables(conn)
    cached = _load_cached(conn, symbol)
    if cached and cached.get("fresh"):
        cached_status = str(cached.get("status") or "").upper()
        if cached_status == "NOT_CONFIGURED" and is_lunarcrush_configured():
            cached = None
        elif cached_status == "ERROR" and is_lunarcrush_configured() and _error_retry_due(cached):
            # Retry stale error rows, but stop hammering fresh failures and rate limits.
            cached = None
    if cached and cached.get("fresh"):
        return cached

    if not is_lunarcrush_configured():
        data = {
            "symbol": symbol,
            "topic": _normalize_search_symbol(symbol),
            "title": symbol,
            "status": "NOT_CONFIGURED",
            "last_error": "missing_lunarcrush_api_key",
            "updated_ts_utc": datetime.now(timezone.utc).isoformat(),
            "raw_json": {},
        }
        _upsert_cache(conn, symbol, data)
        return data

    errors: list[str] = []
    search_payload: Any = {}
    summary_payload: Any = {}
    time_series_payload: Any = {}
    creators_payload: Any = {}
    topic = _normalize_search_symbol(symbol)
    title = symbol
    rate_limited = False

    try:
        search_payload = _request(f"/search/{topic}")
        topic, title = _match_topic(symbol, search_payload)
    except Exception as exc:
        errors.append(f"search:{exc}")
        rate_limited = "rate_limited" in str(exc).lower() or "429" in str(exc)

    if not rate_limited:
        try:
            summary_payload = _request(f"/topic/{topic}")
        except Exception as exc:
            errors.append(f"topic:{exc}")
            rate_limited = "rate_limited" in str(exc).lower() or "429" in str(exc)

    if not rate_limited:
        try:
            time_series_payload = _request(f"/topic/{topic}/time-series", interval="1m")
        except Exception as exc:
            errors.append(f"time-series:{exc}")
            rate_limited = "rate_limited" in str(exc).lower() or "429" in str(exc)

    if not rate_limited:
        try:
            creators_payload = _request(f"/creators/{topic}", limit=_MAX_CREATORS)
        except Exception as exc:
            errors.append(f"creators:{exc}")
            rate_limited = "rate_limited" in str(exc).lower() or "429" in str(exc)

    summary_obj = summary_payload.get("data") if isinstance(summary_payload, dict) and isinstance(summary_payload.get("data"), dict) else summary_payload
    summary_text, supportive_summary, critical_summary = _pick_summary_text(summary_payload)
    creator_rows = _extract_rows(creators_payload)
    top_creators = [name for name in (_creator_name(r) for r in creator_rows[:_MAX_CREATORS]) if name]
    ts_rows = _extract_rows(time_series_payload)
    velocity = _velocity_from_rows(ts_rows)

    topic_rank = _to_float(_scalar(summary_obj, "topic_rank", "topicRank"))
    topic_rank_prev_24h = _to_float(_scalar(summary_obj, "topic_rank_24h_previous", "topicRank24hPrevious"))
    interactions_24h = _to_float(_scalar(summary_obj, "interactions_24h", "interactions24h"))
    num_contributors = _to_float(_scalar(summary_obj, "num_contributors", "contributors"))
    num_posts = _to_float(_scalar(summary_obj, "num_posts", "posts"))
    galaxy_score = _to_float(_scalar(summary_obj, "galaxy_score", "galaxyScore"))
    alt_rank = _to_float(_scalar(summary_obj, "alt_rank", "altRank"))
    social_dominance = _to_float(_scalar(summary_obj, "social_dominance", "socialDominance"))
    creator_count = len(top_creators)

    change_24h = None
    if topic_rank and topic_rank_prev_24h:
        try:
            change_24h = round(topic_rank_prev_24h - topic_rank, 1)
        except Exception:
            change_24h = None

    status = "LIVE"
    if errors and (summary_obj or creator_rows or ts_rows):
        status = "PARTIAL"
    elif errors and not (summary_obj or creator_rows or ts_rows):
        status = "ERROR"

    data = {
        "symbol": symbol,
        "topic": topic,
        "title": str(_scalar(summary_obj, "title", "name", default=title) or title),
        "topic_rank": topic_rank or None,
        "topic_rank_prev_24h": topic_rank_prev_24h or None,
        "topic_rank_change_24h": change_24h,
        "num_contributors": num_contributors or None,
        "num_posts": num_posts or None,
        "interactions_24h": interactions_24h or None,
        "galaxy_score": galaxy_score or None,
        "alt_rank": alt_rank or None,
        "social_dominance": social_dominance or None,
        "social_velocity_score": velocity,
        "creator_count": creator_count,
        "top_creators": top_creators,
        "supportive_summary": supportive_summary,
        "critical_summary": critical_summary,
        "summary": summary_text,
        "narrative_state": _narrative_state(topic_rank, interactions_24h, velocity, creator_count),
        "status": status,
        "last_error": "; ".join(errors) if errors else None,
        "updated_ts_utc": datetime.now(timezone.utc).isoformat(),
        "raw_json": {
            "search": search_payload,
            "summary": summary_payload,
            "time_series": time_series_payload,
            "creators": creators_payload,
        },
    }
    _upsert_cache(conn, symbol, data)
    return data
