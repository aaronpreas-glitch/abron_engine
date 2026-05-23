"""
Arkham token intelligence enrichment helpers.

Designed as a fail-open enrichment layer:
  - if ARKHAM_API_KEY is missing, callers get a NOT_CONFIGURED result
  - if Arkham errors, callers get an ERROR result and core system behavior continues

Current integration scope:
  - token intelligence
  - top holders grouped by entity
  - top token flow over 24h
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone

import requests

log = logging.getLogger(__name__)

_BASE_URL = os.getenv("ARKHAM_API_BASE_URL", "https://api.arkm.com").rstrip("/")
_TIMEOUT_S = float(os.getenv("ARKHAM_TIMEOUT_S", "15") or 15)
_CACHE_HOURS = float(os.getenv("ARKHAM_CACHE_HOURS", "24") or 24)
_FLOW_WINDOW = os.getenv("ARKHAM_TOP_FLOW_WINDOW", "24h").strip() or "24h"
_LOG_PAYMENT_REQUIRED = os.getenv("ARKHAM_LOG_PAYMENT_REQUIRED", "false").lower() == "true"


def is_arkham_configured() -> bool:
    return bool(os.getenv("ARKHAM_API_KEY", "").strip())


def ensure_arkham_tables(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS arkham_token_intel (
            token_mint TEXT PRIMARY KEY,
            chain TEXT NOT NULL DEFAULT 'solana',
            token_name TEXT,
            token_symbol TEXT,
            top_entity_name TEXT,
            top_entity_type TEXT,
            top_entity_pct_of_cap REAL,
            top_entity_usd REAL,
            entity_holder_count INTEGER DEFAULT 0,
            holder_entities_json TEXT,
            top_flow_entity_name TEXT,
            top_flow_entity_type TEXT,
            top_flow_in_usd REAL,
            top_flow_out_usd REAL,
            net_flow_usd REAL,
            signal_quality TEXT,
            signal_score REAL,
            status TEXT NOT NULL DEFAULT 'PENDING',
            raw_json TEXT,
            last_error TEXT,
            updated_ts_utc TEXT NOT NULL
        )
        """
    )


def _headers() -> dict[str, str]:
    return {
        "API-Key": os.getenv("ARKHAM_API_KEY", "").strip(),
        "User-Agent": "memecoin-engine/arkham-enrichment",
        "Accept": "application/json",
    }


def _request(method: str, path: str, **kwargs):
    return requests.request(
        method,
        f"{_BASE_URL}{path}",
        headers=_headers(),
        timeout=_TIMEOUT_S,
        **kwargs,
    )


def _entity_bucket(name: str | None, typ: str | None) -> str:
    hay = f"{name or ''} {typ or ''}".lower()
    if not hay.strip():
        return "UNKNOWN"
    if any(x in hay for x in ("binance", "coinbase", "kraken", "okx", "bybit", "mexc", "gate.io", "kucoin", "upbit")):
        return "EXCHANGE"
    if any(x in hay for x in ("wintermute", "market maker", "maker", "gsr", "dflow", "amber")):
        return "MARKET_MAKER"
    if any(x in hay for x in ("capital", "ventures", "fund", "dao", "labs", "foundation")):
        return "FUND"
    if any(x in hay for x in ("government", "treasury", "bridge", "custody")):
        return "INSTITUTIONAL"
    return "OTHER"


def _pick_entity_name(obj: dict | None) -> tuple[str | None, str | None]:
    if not isinstance(obj, dict):
        return None, None
    for key in ("arkhamEntity", "predictedEntity", "userEntity", "entity"):
        ent = obj.get(key)
        if isinstance(ent, dict):
            return ent.get("name"), ent.get("type")
    label = obj.get("arkhamLabel") or obj.get("userLabel")
    if isinstance(label, dict):
        return label.get("name"), "LABEL"
    return None, None


def _normalize_top_holders(payload: dict | None) -> tuple[list[dict], dict]:
    holders: list[dict] = []
    if not isinstance(payload, dict):
        return holders, {}
    entity_map = payload.get("entityTopHolders") or {}
    if isinstance(entity_map, dict):
        for entries in entity_map.values():
            if not isinstance(entries, list):
                continue
            for item in entries:
                if not isinstance(item, dict):
                    continue
                ent = item.get("entity") if isinstance(item.get("entity"), dict) else {}
                holders.append(
                    {
                        "name": ent.get("name"),
                        "type": _entity_bucket(ent.get("name"), ent.get("type")),
                        "raw_type": ent.get("type"),
                        "pct_of_cap": float(item.get("pctOfCap") or 0),
                        "usd": float(item.get("usd") or 0),
                    }
                )
    holders.sort(key=lambda x: (x.get("pct_of_cap") or 0, x.get("usd") or 0), reverse=True)
    top = holders[0] if holders else {}
    return holders[:5], top


def _normalize_top_flow(payload) -> dict:
    rows = payload if isinstance(payload, list) else []
    best = {
        "entity_name": None,
        "entity_type": None,
        "in_usd": 0.0,
        "out_usd": 0.0,
        "net_usd": 0.0,
    }
    ranked: list[dict] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        addr = item.get("address") if isinstance(item.get("address"), dict) else {}
        name, raw_type = _pick_entity_name(addr)
        in_usd = float(item.get("inUSD") or 0)
        out_usd = float(item.get("outUSD") or 0)
        ranked.append(
            {
                "entity_name": name,
                "entity_type": _entity_bucket(name, raw_type),
                "raw_type": raw_type,
                "in_usd": in_usd,
                "out_usd": out_usd,
                "net_usd": in_usd - out_usd,
                "magnitude": abs(in_usd - out_usd) + max(in_usd, out_usd),
            }
        )
    ranked.sort(key=lambda x: x["magnitude"], reverse=True)
    if ranked:
        best = ranked[0]
    return best


def _score_signal(top_holder: dict, holder_count: int, flow: dict) -> tuple[float, str]:
    score = 0.0
    holder_type = str(top_holder.get("type") or "UNKNOWN")
    pct = float(top_holder.get("pct_of_cap") or 0)
    net = float(flow.get("net_usd") or 0)

    if holder_count >= 3:
        score += 10
    elif holder_count >= 1:
        score += 5

    if holder_type in ("MARKET_MAKER", "FUND"):
        score += 25
    elif holder_type == "OTHER":
        score += 10
    elif holder_type == "EXCHANGE":
        score -= 5

    if pct >= 10:
        score += 10
    elif pct >= 3:
        score += 5

    if net >= 250_000:
        score += 25
    elif net >= 50_000:
        score += 15
    elif net > 0:
        score += 5
    elif net <= -250_000:
        score -= 15
    elif net < 0:
        score -= 5

    score = max(0.0, min(100.0, round(score, 1)))
    if score >= 55:
        quality = "HIGH"
    elif score >= 25:
        quality = "MEDIUM"
    elif score > 0:
        quality = "LOW"
    else:
        quality = "NONE"
    return score, quality


def _upsert_cache(conn, token_mint: str, data: dict) -> None:
    ensure_arkham_tables(conn)
    conn.execute(
        """
        INSERT INTO arkham_token_intel (
            token_mint, chain, token_name, token_symbol,
            top_entity_name, top_entity_type, top_entity_pct_of_cap, top_entity_usd,
            entity_holder_count, holder_entities_json,
            top_flow_entity_name, top_flow_entity_type,
            top_flow_in_usd, top_flow_out_usd, net_flow_usd,
            signal_quality, signal_score, status, raw_json, last_error, updated_ts_utc
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(token_mint) DO UPDATE SET
            chain=excluded.chain,
            token_name=excluded.token_name,
            token_symbol=excluded.token_symbol,
            top_entity_name=excluded.top_entity_name,
            top_entity_type=excluded.top_entity_type,
            top_entity_pct_of_cap=excluded.top_entity_pct_of_cap,
            top_entity_usd=excluded.top_entity_usd,
            entity_holder_count=excluded.entity_holder_count,
            holder_entities_json=excluded.holder_entities_json,
            top_flow_entity_name=excluded.top_flow_entity_name,
            top_flow_entity_type=excluded.top_flow_entity_type,
            top_flow_in_usd=excluded.top_flow_in_usd,
            top_flow_out_usd=excluded.top_flow_out_usd,
            net_flow_usd=excluded.net_flow_usd,
            signal_quality=excluded.signal_quality,
            signal_score=excluded.signal_score,
            status=excluded.status,
            raw_json=excluded.raw_json,
            last_error=excluded.last_error,
            updated_ts_utc=excluded.updated_ts_utc
        """,
        (
            token_mint,
            data.get("chain", "solana"),
            data.get("token_name"),
            data.get("token_symbol"),
            data.get("top_entity_name"),
            data.get("top_entity_type"),
            data.get("top_entity_pct_of_cap"),
            data.get("top_entity_usd"),
            data.get("entity_holder_count", 0),
            data.get("holder_entities_json"),
            data.get("top_flow_entity_name"),
            data.get("top_flow_entity_type"),
            data.get("top_flow_in_usd"),
            data.get("top_flow_out_usd"),
            data.get("net_flow_usd"),
            data.get("signal_quality"),
            data.get("signal_score"),
            data.get("status", "PENDING"),
            data.get("raw_json"),
            data.get("last_error"),
            data.get("updated_ts_utc"),
        ),
    )


def get_cached_token_intel(conn, token_mint: str, max_age_hours: float | None = None) -> dict | None:
    ensure_arkham_tables(conn)
    row = conn.execute(
        "SELECT * FROM arkham_token_intel WHERE token_mint=?",
        (token_mint,),
    ).fetchone()
    if not row:
        return None
    data = dict(row)
    try:
        age_limit = _CACHE_HOURS if max_age_hours is None else float(max_age_hours)
        updated = datetime.fromisoformat(str(data.get("updated_ts_utc")).replace("Z", "+00:00"))
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) - updated > timedelta(hours=age_limit):
            return None
    except Exception:
        return None
    return data


def enrich_token(token_mint: str, chain: str = "solana", force: bool = False) -> dict:
    from utils.db import get_conn

    if not token_mint:
        return {"status": "SKIPPED", "last_error": "missing_token_mint"}

    with get_conn() as conn:
        if not force:
            cached = get_cached_token_intel(conn, token_mint)
            if cached:
                return cached

    if not is_arkham_configured():
        data = {
            "token_mint": token_mint,
            "chain": chain,
            "status": "NOT_CONFIGURED",
            "signal_quality": "NONE",
            "signal_score": 0.0,
            "updated_ts_utc": datetime.now(timezone.utc).isoformat(),
            "last_error": "missing_arkham_api_key",
        }
        with get_conn() as conn:
            _upsert_cache(conn, token_mint, data)
        return data

    try:
        token_payload: dict = {}
        holders_payload: dict = {}
        flow_payload: list | dict = []
        errors: list[str] = []

        try:
            token_r = _request("GET", f"/intelligence/token/{chain}/{token_mint}")
            token_r.raise_for_status()
            token_payload = token_r.json() if token_r.content else {}
        except Exception as exc:
            errors.append(f"token:{exc}")

        try:
            holders_r = _request("GET", f"/token/holders/{chain}/{token_mint}", params={"groupByEntity": "true"})
            holders_r.raise_for_status()
            holders_payload = holders_r.json() if holders_r.content else {}
        except Exception as exc:
            errors.append(f"holders:{exc}")

        try:
            flow_r = _request("GET", f"/token/top_flow/{chain}/{token_mint}", params={"timeLast": _FLOW_WINDOW})
            flow_r.raise_for_status()
            flow_payload = flow_r.json() if flow_r.content else []
        except Exception as exc:
            errors.append(f"flow:{exc}")

        if not token_payload and not holders_payload and not flow_payload:
            raise RuntimeError("; ".join(errors) if errors else "no_arkham_payload")

        holder_entities, top_holder = _normalize_top_holders(holders_payload)
        top_flow = _normalize_top_flow(flow_payload)
        score, quality = _score_signal(top_holder, len(holder_entities), top_flow)
        status = "LIVE" if not errors else "PARTIAL"

        data = {
            "token_mint": token_mint,
            "chain": chain,
            "token_name": token_payload.get("name"),
            "token_symbol": token_payload.get("symbol"),
            "top_entity_name": top_holder.get("name"),
            "top_entity_type": top_holder.get("type"),
            "top_entity_pct_of_cap": top_holder.get("pct_of_cap"),
            "top_entity_usd": top_holder.get("usd"),
            "entity_holder_count": len(holder_entities),
            "holder_entities_json": json.dumps(holder_entities[:5]),
            "top_flow_entity_name": top_flow.get("entity_name"),
            "top_flow_entity_type": top_flow.get("entity_type"),
            "top_flow_in_usd": top_flow.get("in_usd"),
            "top_flow_out_usd": top_flow.get("out_usd"),
            "net_flow_usd": top_flow.get("net_usd"),
            "signal_quality": quality,
            "signal_score": score,
            "status": status,
            "raw_json": json.dumps(
                {
                    "token": token_payload,
                    "holders": holder_entities[:5],
                    "flow": top_flow,
                }
            ),
            "last_error": "; ".join(errors)[:300] if errors else None,
            "updated_ts_utc": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as exc:
        error_text = str(exc)
        payment_required = "402" in error_text or "payment required" in error_text.lower()
        if payment_required:
            if _LOG_PAYMENT_REQUIRED:
                log.info("[ARKHAM] entitlement missing for %s; enrichment skipped", token_mint)
        else:
            log.warning("[ARKHAM] enrich_token failed for %s: %s", token_mint, exc)
        data = {
            "token_mint": token_mint,
            "chain": chain,
            "status": "ENTITLEMENT_REQUIRED" if payment_required else "ERROR",
            "signal_quality": "NONE",
            "signal_score": 0.0,
            "updated_ts_utc": datetime.now(timezone.utc).isoformat(),
            "last_error": ("arkham_payment_required" if payment_required else error_text[:300]),
        }

    with get_conn() as conn:
        _upsert_cache(conn, token_mint, data)
    return data
