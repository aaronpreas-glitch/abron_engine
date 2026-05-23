from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import requests


TOKEN_INTEL_MAX_TOKENS = max(5, int(os.getenv("TOKEN_INTEL_MAX_TOKENS", "60")))
TOKEN_INTEL_LIVE_MARKET_LIMIT = max(0, int(os.getenv("TOKEN_INTEL_LIVE_MARKET_LIMIT", "28")))
TOKEN_INTEL_CONFIRMATION_LIMIT = max(0, int(os.getenv("TOKEN_INTEL_CONFIRMATION_LIMIT", "12")))
TOKEN_INTEL_PROVIDER_REPAIR_LIMIT = max(0, int(os.getenv("TOKEN_INTEL_PROVIDER_REPAIR_LIMIT", "10")))
TOKEN_INTEL_RPC_HOLDER_LIMIT = max(0, int(os.getenv("TOKEN_INTEL_RPC_HOLDER_LIMIT", "12")))
TOKEN_INTEL_MIN_INTERVAL_SECONDS = max(60, int(os.getenv("TOKEN_INTEL_MIN_INTERVAL_SECONDS", "300")))
SOLANA_RPC_URL = os.getenv("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com").strip()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _f(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def _i(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except Exception:
        return default


def _json_loads(raw: Any, default: Any = None) -> Any:
    if not raw:
        return default
    try:
        return json.loads(str(raw))
    except Exception:
        return default


def _get_conn():
    from utils.db import get_conn  # type: ignore

    return get_conn()


def _ensure_tables(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS token_intelligence_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts_utc TEXT NOT NULL,
            mint TEXT NOT NULL,
            symbol TEXT,
            lane_sources TEXT,
            identity_status TEXT,
            identity_source TEXT,
            identity_confidence TEXT,
            identity_reason TEXT,
            resolved_mint TEXT,
            pair_address TEXT,
            market_source TEXT,
            data_freshness TEXT,
            data_confidence TEXT,
            price REAL,
            liquidity REAL,
            marketcap REAL,
            fdv REAL,
            volume_5m_usd REAL,
            volume_1h_usd REAL,
            volume_6h_usd REAL,
            volume_24h_usd REAL,
            price_change_1h_percent REAL,
            price_change_6h_percent REAL,
            price_change_24h_percent REAL,
            trade_1h INTEGER,
            trade_24h INTEGER,
            buy_pressure_1h REAL,
            unique_wallet_1h INTEGER,
            holder_top1_pct REAL,
            holder_top10_pct REAL,
            token_supply REAL,
            risk_score REAL,
            pressure_score REAL,
            quality_score REAL,
            reasons_json TEXT,
            raw_json TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_token_intel_snapshots_mint_ts
        ON token_intelligence_snapshots(mint, ts_utc)
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS token_intelligence_current (
            mint TEXT PRIMARY KEY,
            symbol TEXT,
            updated_at TEXT NOT NULL,
            lane_sources TEXT,
            identity_status TEXT,
            identity_source TEXT,
            identity_confidence TEXT,
            identity_reason TEXT,
            resolved_mint TEXT,
            pair_address TEXT,
            market_source TEXT,
            data_freshness TEXT,
            data_confidence TEXT,
            price REAL,
            liquidity REAL,
            marketcap REAL,
            fdv REAL,
            volume_5m_usd REAL,
            volume_1h_usd REAL,
            volume_6h_usd REAL,
            volume_24h_usd REAL,
            price_change_1h_percent REAL,
            price_change_6h_percent REAL,
            price_change_24h_percent REAL,
            trade_1h INTEGER,
            trade_24h INTEGER,
            buy_pressure_1h REAL,
            unique_wallet_1h INTEGER,
            holder_top1_pct REAL,
            holder_top10_pct REAL,
            token_supply REAL,
            risk_score REAL,
            pressure_score REAL,
            quality_score REAL,
            reasons_json TEXT,
            raw_json TEXT
        )
        """
    )
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(token_intelligence_current)").fetchall()}
        for col, col_type in [
            ("provider_repair_status", "TEXT"),
            ("provider_repair_failure_class", "TEXT"),
            ("provider_repair_updated_at", "TEXT"),
            ("provider_repair_retired_until_unix", "REAL"),
        ]:
            if col not in cols:
                conn.execute(f"ALTER TABLE token_intelligence_current ADD COLUMN {col} {col_type}")
    except Exception:
        pass
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS token_provider_repair_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts_utc TEXT NOT NULL,
            mint TEXT NOT NULL,
            symbol TEXT,
            previous_source TEXT,
            previous_freshness TEXT,
            previous_confidence TEXT,
            previous_quality REAL,
            previous_pressure REAL,
            repair_status TEXT NOT NULL,
            new_source TEXT,
            new_freshness TEXT,
            new_confidence TEXT,
            new_quality REAL,
            new_pressure REAL,
            latency_ms INTEGER,
            unresolved_count INTEGER,
            reason TEXT
        )
        """
    )
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(token_provider_repair_events)").fetchall()}
        for col, col_type in [
            ("failure_class", "TEXT"),
            ("provider_path", "TEXT"),
            ("repair_score", "REAL"),
            ("queue_flags_json", "TEXT"),
        ]:
            if col not in cols:
                conn.execute(f"ALTER TABLE token_provider_repair_events ADD COLUMN {col} {col_type}")
    except Exception:
        pass
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_token_provider_repair_events_ts
        ON token_provider_repair_events(ts_utc)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_token_provider_repair_events_mint_ts
        ON token_provider_repair_events(mint, ts_utc)
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS provider_repair_escalations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_ts TEXT NOT NULL,
            updated_ts TEXT NOT NULL,
            mint TEXT NOT NULL,
            symbol TEXT,
            failure_class TEXT,
            repair_status TEXT,
            escalation_lane TEXT NOT NULL,
            escalation_status TEXT NOT NULL,
            priority_score REAL,
            queue_flags_json TEXT,
            provider_path TEXT,
            reason TEXT,
            source_event_id INTEGER,
            operator_decision TEXT,
            operator_note TEXT,
            outcome_label TEXT,
            last_action_ts TEXT,
            sla_due_ts TEXT,
            raw_json TEXT
        )
        """
    )
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(provider_repair_escalations)").fetchall()}
        for col, col_type in [
            ("failure_class", "TEXT"),
            ("repair_status", "TEXT"),
            ("operator_decision", "TEXT"),
            ("operator_note", "TEXT"),
            ("outcome_label", "TEXT"),
            ("last_action_ts", "TEXT"),
            ("sla_due_ts", "TEXT"),
            ("raw_json", "TEXT"),
            ("outcome_check_ts", "TEXT"),
            ("outcome_status", "TEXT"),
            ("outcome_source", "TEXT"),
            ("outcome_return_1h_pct", "REAL"),
            ("outcome_return_4h_pct", "REAL"),
            ("outcome_return_24h_pct", "REAL"),
            ("outcome_max_return_pct", "REAL"),
            ("outcome_max_drawdown_pct", "REAL"),
            ("blocker_correctness", "TEXT"),
            ("confidence_impact", "REAL"),
            ("outcome_reason", "TEXT"),
        ]:
            if col not in cols:
                conn.execute(f"ALTER TABLE provider_repair_escalations ADD COLUMN {col} {col_type}")
    except Exception:
        pass
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_provider_repair_escalations_mint_lane
        ON provider_repair_escalations(mint, escalation_lane)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_provider_repair_escalations_status_due
        ON provider_repair_escalations(escalation_status, sla_due_ts)
        """
    )


def _parse_iso(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def _pct_change(now_value: Any, base_value: Any) -> float | None:
    base = _f(base_value)
    current = _f(now_value)
    if base <= 0:
        return None
    return round(((current - base) / base) * 100.0, 2)


def _add_token(tokens: dict[str, dict], mint: str, symbol: str | None, source: str, raw: dict | None = None) -> None:
    clean_mint = str(mint or "").strip()
    if not clean_mint:
        return
    item = tokens.setdefault(
        clean_mint,
        {
            "mint": clean_mint,
            "symbol": str(symbol or "").strip().upper() or None,
            "sources": [],
            "raw_inputs": [],
        },
    )
    if symbol and not item.get("symbol"):
        item["symbol"] = str(symbol).strip().upper()
    if source and source not in item["sources"]:
        item["sources"].append(source)
    if raw:
        item["raw_inputs"].append(raw)


def _collect_scan_cache(conn, tokens: dict[str, dict]) -> None:
    rows = conn.execute(
        "SELECT value FROM kv_store WHERE key IN ('memecoin_scan_cache','memecoin_scan_cache_last_nonempty')"
    ).fetchall()
    for row in rows:
        payload = _json_loads(row["value"], [])
        candidates = payload if isinstance(payload, list) else []
        if isinstance(payload, dict):
            candidates = payload.get("signals") or payload.get("items") or payload.get("data") or []
        for item in list(candidates or [])[:20]:
            if not isinstance(item, dict):
                continue
            _add_token(tokens, item.get("mint") or item.get("address"), item.get("symbol"), "scan_cache", item)


def _collect_tokens() -> list[dict]:
    tokens: dict[str, dict] = {}
    with _get_conn() as conn:
        try:
            from utils.spot_accumulator import BASKET  # type: ignore

            for row in BASKET:
                _add_token(tokens, row.get("mint"), row.get("symbol"), "spot_basket", row)
        except Exception:
            pass

        try:
            rows = conn.execute(
                """
                SELECT mint, symbol, snapshot_json
                FROM conviction_recovery_market_cache
                ORDER BY updated_at DESC
                LIMIT 40
                """
            ).fetchall()
            for row in rows:
                raw = _json_loads(row["snapshot_json"], {}) or {}
                _add_token(tokens, row["mint"], row["symbol"], "conviction_recovery", raw)
        except Exception:
            pass

        try:
            _collect_scan_cache(conn, tokens)
        except Exception:
            pass

        try:
            rows = conn.execute(
                """
                SELECT token_mint, token_symbol, market_cap_usd, price_at_alert, token_pair_address
                FROM whale_watch_alerts
                WHERE token_mint IS NOT NULL AND token_mint != ''
                ORDER BY id DESC
                LIMIT 25
                """
            ).fetchall()
            for row in rows:
                _add_token(tokens, row["token_mint"], row["token_symbol"], "whale_watch", dict(row))
        except Exception:
            pass

        try:
            rows = conn.execute(
                """
                SELECT mint, symbol
                FROM memecoin_trades
                WHERE status='OPEN'
                ORDER BY opened_ts_utc DESC
                LIMIT 20
                """
            ).fetchall()
            for row in rows:
                _add_token(tokens, row["mint"], row["symbol"], "open_trade", dict(row))
        except Exception:
            pass

        try:
            rows = conn.execute(
                """
                SELECT mint, symbol, latest_mcap_usd, latest_price, latest_liquidity_usd, latest_volume_24h
                FROM memecoin_runner_radar_observations
                ORDER BY last_seen_utc DESC
                LIMIT 25
                """
            ).fetchall()
            for row in rows:
                _add_token(tokens, row["mint"], row["symbol"], "runner_radar", dict(row))
        except Exception:
            pass

    priority = {
        "open_trade": 100,
        "whale_watch": 90,
        "conviction_recovery": 80,
        "scan_cache": 70,
        "runner_radar": 60,
        "spot_basket": 50,
    }
    values = list(tokens.values())
    values.sort(key=lambda x: max(priority.get(s, 0) for s in x.get("sources") or [""]), reverse=True)
    return values[:TOKEN_INTEL_MAX_TOKENS]


def _best_raw(item: dict) -> dict:
    for raw in item.get("raw_inputs") or []:
        if isinstance(raw, dict) and any(raw.get(k) for k in ("price", "mcap_usd", "market_cap", "latest_price")):
            return raw
    return {}


def _pair_metrics(pair: dict) -> dict:
    base = pair.get("baseToken") or {}
    quote = pair.get("quoteToken") or {}
    volume = pair.get("volume") or {}
    change = pair.get("priceChange") or {}
    txns = pair.get("txns") or {}
    h1 = txns.get("h1") or {}
    h24 = txns.get("h24") or {}
    buys_h1 = _i(h1.get("buys"))
    sells_h1 = _i(h1.get("sells"))
    trade_h1 = buys_h1 + sells_h1
    buy_pressure = (buys_h1 / trade_h1 * 100.0) if trade_h1 > 0 else None
    return {
        "symbol": (base.get("symbol") or "").upper() or None,
        "base_mint": base.get("address"),
        "quote_mint": quote.get("address"),
        "pair_address": pair.get("pairAddress"),
        "price": _f(pair.get("priceUsd")),
        "liquidity": _f((pair.get("liquidity") or {}).get("usd")),
        "marketcap": _f(pair.get("marketCap") or pair.get("fdv")),
        "fdv": _f(pair.get("fdv")),
        "volume_5m_usd": _f(volume.get("m5")),
        "volume_1h_usd": _f(volume.get("h1")),
        "volume_6h_usd": _f(volume.get("h6")),
        "volume_24h_usd": _f(volume.get("h24")),
        "price_change_1h_percent": _f(change.get("h1")),
        "price_change_6h_percent": _f(change.get("h6")),
        "price_change_24h_percent": _f(change.get("h24")),
        "trade_1h": trade_h1,
        "trade_24h": _i(h24.get("buys")) + _i(h24.get("sells")),
        "buy_pressure_1h": round(buy_pressure, 1) if buy_pressure is not None else None,
    }


def _raw_metrics(raw: dict) -> dict:
    vol24 = _f(raw.get("volume_24h") or raw.get("latest_volume_24h") or raw.get("volume_24h_usd"))
    vol1 = _f(raw.get("volume_1h") or raw.get("volume_1h_usd"))
    return {
        "symbol": str(raw.get("symbol") or "").strip().upper() or None,
        "base_mint": None,
        "quote_mint": None,
        "pair_address": raw.get("pair_address") or raw.get("token_pair_address"),
        "price": _f(raw.get("price") or raw.get("latest_price")),
        "liquidity": _f(raw.get("liquidity") or raw.get("liquidity_usd") or raw.get("latest_liquidity_usd")),
        "marketcap": _f(raw.get("mcap_usd") or raw.get("market_cap") or raw.get("market_cap_usd") or raw.get("latest_mcap_usd")),
        "fdv": _f(raw.get("fdv")),
        "volume_5m_usd": _f(raw.get("volume_5m")),
        "volume_1h_usd": vol1,
        "volume_6h_usd": _f(raw.get("volume_6h")),
        "volume_24h_usd": vol24,
        "price_change_1h_percent": _f(raw.get("change_1h") or raw.get("latest_change_1h")),
        "price_change_6h_percent": _f(raw.get("change_6h")),
        "price_change_24h_percent": _f(raw.get("change_24h") or raw.get("latest_change_24h")),
        "trade_1h": _i(raw.get("txns_h1") or raw.get("trade_1h")),
        "trade_24h": _i(raw.get("txns_h24") or raw.get("trade_24h")),
        "buy_pressure_1h": _f(raw.get("buy_pressure") or raw.get("buy_pressure_1h"), 0.0) or None,
    }


def _fetch_market_metrics(mint: str, raw: dict, *, live_enabled: bool, reserve_market: bool = False) -> tuple[dict, str]:
    if not live_enabled:
        return _raw_metrics(raw), str(raw.get("provider_source") or raw.get("source") or "cache")
    try:
        from data.dexscreener import fetch_token_pairs  # type: ignore

        pairs = fetch_token_pairs(
            mint,
            reason="token_intel_confirm_429" if reserve_market else "token_intel_market_429",
            reserve=reserve_market,
        ) or []
        sol_pairs = [p for p in pairs if str(p.get("chainId") or "").lower() == "solana"]
        if sol_pairs:
            exact_base = [
                p for p in sol_pairs
                if str(((p.get("baseToken") or {}).get("address")) or "").strip() == mint
            ]
            best = max(exact_base or sol_pairs, key=lambda p: _f((p.get("liquidity") or {}).get("usd")))
            return _pair_metrics(best), "dexscreener"
    except Exception:
        pass
    return _raw_metrics(raw), str(raw.get("provider_source") or raw.get("source") or "cache")


def _rpc(method: str, params: list) -> Any:
    if not SOLANA_RPC_URL:
        return None
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    resp = requests.post(SOLANA_RPC_URL, json=payload, timeout=8, headers={"Content-Type": "application/json"})
    if resp.status_code != 200:
        return None
    return (resp.json() or {}).get("result")


def _holder_snapshot(mint: str) -> dict:
    try:
        supply_result = _rpc("getTokenSupply", [mint])
        supply = _f(((supply_result or {}).get("value") or {}).get("uiAmount"))
        largest_result = _rpc("getTokenLargestAccounts", [mint])
        accounts = ((largest_result or {}).get("value") or [])[:10]
        amounts = [_f(a.get("uiAmount")) for a in accounts]
        if supply <= 0 or not amounts:
            return {}
        return {
            "token_supply": supply,
            "holder_top1_pct": round((amounts[0] / supply) * 100.0, 2) if amounts else None,
            "holder_top10_pct": round((sum(amounts) / supply) * 100.0, 2),
        }
    except Exception:
        return {}


def _score(row: dict) -> tuple[float, float, float, list[str], str, str]:
    reasons: list[str] = []
    liq = _f(row.get("liquidity"))
    vol24 = _f(row.get("volume_24h_usd"))
    vol1 = _f(row.get("volume_1h_usd"))
    ch1 = _f(row.get("price_change_1h_percent"))
    top1 = _f(row.get("holder_top1_pct"))
    top10 = _f(row.get("holder_top10_pct"))
    trades = _i(row.get("trade_1h"))
    buy_pressure = _f(row.get("buy_pressure_1h"), 50.0)

    risk = 100.0
    if top1 > 20:
        risk -= 25
        reasons.append("top_holder_high")
    elif top1 > 10:
        risk -= 12
        reasons.append("top_holder_watch")
    if top10 > 55:
        risk -= 20
        reasons.append("top10_concentrated")
    if liq < 50_000:
        risk -= 20
        reasons.append("liquidity_thin")
    elif liq < 150_000:
        risk -= 8
        reasons.append("liquidity_watch")

    pressure = 50.0
    if vol24 > 0 and liq > 0:
        pressure += min(20.0, (vol24 / max(liq, 1.0)) * 5.0)
    if vol1 > 0 and vol24 > 0:
        pressure += min(15.0, (vol1 / max(vol24, 1.0)) * 100.0)
    pressure += max(-12.0, min(12.0, ch1 * 0.7))
    pressure += max(-10.0, min(10.0, (buy_pressure - 50.0) * 0.35))
    if trades >= 100:
        pressure += 8
    elif trades >= 35:
        pressure += 4
    pressure = max(0.0, min(100.0, pressure))

    quality = max(0.0, min(100.0, risk * 0.45 + pressure * 0.35 + min(100.0, liq / 10_000.0) * 0.20))
    data_confidence = "HIGH" if row.get("price") and row.get("liquidity") and row.get("volume_24h_usd") else "MEDIUM" if row.get("price") else "LOW"
    freshness = "LIVE" if row.get("market_source") == "dexscreener" else "RECENT" if row.get("price") else "STALE"
    return round(risk, 1), round(pressure, 1), round(quality, 1), reasons, data_confidence, freshness


def _cached_identity(symbol: str) -> dict:
    try:
        from utils.token_resolver import norm_symbol  # type: ignore

        key = f"token_identity:last:{norm_symbol(symbol)}"
    except Exception:
        key = f"token_identity:last:{str(symbol or '').upper()}"
    try:
        with _get_conn() as conn:
            row = conn.execute("SELECT value FROM kv_store WHERE key=?", (key,)).fetchone()
        return _json_loads(row[0] if row else None, {}) or {}
    except Exception:
        return {}


def _identity(item: dict) -> dict:
    symbol = str(item.get("symbol") or "").upper()
    mint = str(item.get("mint") or "")
    base_mint = str(item.get("base_mint") or "").strip()
    if not symbol:
        return {"identity_status": "UNKNOWN", "identity_reason": "missing_symbol"}

    if base_mint:
        if base_mint == mint:
            return {
                "identity_status": "RESOLVED",
                "identity_source": "dexscreener_pair_base",
                "identity_confidence": "HIGH",
                "identity_reason": None,
                "resolved_mint": mint,
            }
        return {
            "identity_status": "MISMATCH",
            "identity_source": "dexscreener_pair_base",
            "identity_confidence": "BLOCKED",
            "identity_reason": "live_pair_base_mint_mismatch",
            "resolved_mint": base_mint,
        }

    cached = _cached_identity(symbol)
    cached_mint = str(cached.get("resolved_mint") or "").strip()
    if cached_mint:
        status = "RESOLVED" if cached_mint == mint else "MISMATCH"
        return {
            "identity_status": status,
            "identity_source": cached.get("source") or "cached_identity_guard",
            "identity_confidence": cached.get("confidence") or ("HIGH" if status == "RESOLVED" else "BLOCKED"),
            "identity_reason": None if status == "RESOLVED" else "cached_identity_mismatch",
            "resolved_mint": cached_mint,
        }

    # Do not spend external provider calls here. Token Intelligence is mint-first:
    # if a trusted system lane supplied a mint but live-pair confirmation is absent,
    # keep the token usable while making the confidence explicit.
    return {
        "identity_status": "ASSERTED",
        "identity_source": str(item.get("lane_sources") or "system_lane"),
        "identity_confidence": "MEDIUM",
        "identity_reason": "mint_from_system_source_unverified_by_live_pair",
        "resolved_mint": mint,
    }


def _build_snapshot(item: dict, *, live_enabled: bool, holder_enabled: bool, reserve_market: bool = False) -> dict:
    raw = _best_raw(item)
    mint = str(item.get("mint") or "").strip()
    market, market_source = _fetch_market_metrics(mint, raw, live_enabled=live_enabled, reserve_market=reserve_market)
    symbol = str(market.get("symbol") or item.get("symbol") or raw.get("symbol") or "").strip().upper() or None
    if not symbol:
        raise ValueError("missing_symbol")
    row = {
        **market,
        "ts_utc": _now_iso(),
        "mint": mint,
        "symbol": symbol,
        "lane_sources": ",".join(item.get("sources") or []),
        "market_source": market_source,
    }
    if holder_enabled:
        row.update(_holder_snapshot(mint))
    row.update(_identity(row))
    risk, pressure, quality, reasons, data_confidence, freshness = _score(row)
    row.update(
        {
            "risk_score": risk,
            "pressure_score": pressure,
            "quality_score": quality,
            "reasons": reasons,
            "data_confidence": data_confidence,
            "data_freshness": freshness,
            "raw": {"inputs": item.get("raw_inputs") or [], "market": market},
        }
    )
    return row


def _snapshot_from_market(item: dict, market: dict, market_source: str) -> dict:
    mint = str(item.get("mint") or "").strip()
    raw = _best_raw(item)
    symbol = str(market.get("symbol") or item.get("symbol") or raw.get("symbol") or "").strip().upper() or None
    if not symbol:
        raise ValueError("missing_symbol")
    row = {
        **market,
        "ts_utc": _now_iso(),
        "mint": mint,
        "symbol": symbol,
        "lane_sources": ",".join(item.get("sources") or []),
        "market_source": market_source,
    }
    row.update(_identity(row))
    risk, pressure, quality, reasons, data_confidence, freshness = _score(row)
    row.update(
        {
            "risk_score": risk,
            "pressure_score": pressure,
            "quality_score": quality,
            "reasons": reasons,
            "data_confidence": data_confidence,
            "data_freshness": freshness,
            "raw": {"inputs": item.get("raw_inputs") or [], "market": market, "fallback_source": market_source},
        }
    )
    return row


def _snapshot_from_token_stats(item: dict) -> dict | None:
    mint = str(item.get("mint") or "").strip()
    if not mint:
        return None
    try:
        from utils.db import get_latest_memecoin_token_stats_for_mints  # type: ignore

        stats = (get_latest_memecoin_token_stats_for_mints([mint], max_age_minutes=240) or {}).get(mint) or {}
    except Exception:
        stats = {}
    if not stats:
        return None
    market = {
        "symbol": str(stats.get("symbol") or item.get("symbol") or "").upper() or None,
        "base_mint": mint,
        "quote_mint": None,
        "pair_address": None,
        "price": _f(stats.get("price")),
        "liquidity": _f(stats.get("liquidity")),
        "marketcap": _f(stats.get("marketcap")),
        "fdv": _f(stats.get("fdv")),
        "volume_5m_usd": 0.0,
        "volume_1h_usd": _f(stats.get("volume_1h_usd")),
        "volume_6h_usd": 0.0,
        "volume_24h_usd": _f(stats.get("volume_24h_usd")),
        "price_change_1h_percent": _f(stats.get("price_change_1h_percent")),
        "price_change_6h_percent": 0.0,
        "price_change_24h_percent": _f(stats.get("price_change_24h_percent")),
        "trade_1h": _i(stats.get("trade_1h")),
        "trade_24h": 0,
        "buy_pressure_1h": (
            round((_f(stats.get("volume_buy_1h_usd")) / max(1.0, _f(stats.get("volume_1h_usd"))) * 100.0), 1)
            if _f(stats.get("volume_1h_usd")) > 0
            else None
        ),
    }
    if _f(market.get("price")) <= 0:
        return None
    return _snapshot_from_market(item, market, "token_stats_cache")


def _jupiter_price(mint: str) -> float:
    try:
        resp = requests.get("https://api.jup.ag/price/v2", params={"ids": mint}, timeout=8)
        if resp.status_code != 200:
            return 0.0
        data = resp.json() or {}
        return _f(((data.get("data") or {}).get(mint) or {}).get("price"))
    except Exception:
        return 0.0


def _snapshot_from_birdeye_jupiter(item: dict) -> dict | None:
    mint = str(item.get("mint") or "").strip()
    if not mint:
        return None
    try:
        from data.birdeye import fetch_birdeye_token_overview  # type: ignore

        overview = fetch_birdeye_token_overview(mint) or {}
    except Exception:
        overview = {}
    price = _jupiter_price(mint)
    if price <= 0 and not overview:
        return None
    market = {
        "symbol": str(item.get("symbol") or "").upper() or None,
        "base_mint": mint,
        "quote_mint": None,
        "pair_address": None,
        "price": price,
        "liquidity": 0.0,
        "marketcap": 0.0,
        "fdv": 0.0,
        "volume_5m_usd": 0.0,
        "volume_1h_usd": 0.0,
        "volume_6h_usd": 0.0,
        "volume_24h_usd": 0.0,
        "price_change_1h_percent": _f(overview.get("priceChange1hPercent")),
        "price_change_6h_percent": 0.0,
        "price_change_24h_percent": _f(overview.get("priceChange24hPercent")),
        "trade_1h": _i(overview.get("txns_h1")),
        "trade_24h": _i(overview.get("txns_h24")),
        "buy_pressure_1h": None,
        "unique_wallet_1h": _i(overview.get("uniqueWallet1h")),
    }
    if _f(market.get("price")) <= 0:
        return None
    return _snapshot_from_market(item, market, "birdeye_jupiter_fallback")


def _classify_repair_failure(previous: dict, snapshot: dict | None, provider_path: list[str], exc: Exception | None = None) -> tuple[str, str]:
    if exc is not None:
        text = str(exc).lower()
        if "429" in text or "rate" in text or "budget" in text:
            return "provider_budget_or_rate_limit", "provider_budget_or_rate_limit"
        return "temporary_error", "provider_exception"
    if snapshot and str(snapshot.get("identity_status") or "").upper() == "MISMATCH":
        return "bad_ca_or_pair_mismatch", "live_pair_base_mint_mismatch"
    if snapshot and _f(snapshot.get("price")) > 0 and _f(snapshot.get("liquidity")) < 5_000:
        return "low_liquidity", "provider_found_price_but_liquidity_is_too_low"
    if "dexscreener" in provider_path and not snapshot:
        return "dexscreener_no_pair", "dexscreener_returned_no_usable_solana_pair"
    if _f(previous.get("price")) <= 0 and _f(previous.get("liquidity")) <= 0:
        return "no_market_data", "no_provider_returned_usable_market_data"
    if _i(previous.get("unresolved_count")) >= 2:
        return "dead_or_inactive_token", "repeated_provider_miss"
    return "provider_miss", "live_provider_missing_or_incomplete"


def _build_provider_repair_snapshot(item: dict) -> tuple[dict | None, str, str, str, list[str]]:
    provider_path: list[str] = []
    first_snapshot: dict | None = None
    first_exc: Exception | None = None
    try:
        provider_path.append("dexscreener")
        first_snapshot = _build_snapshot(
            item,
            live_enabled=True,
            holder_enabled=False,
            reserve_market=True,
        )
        if (
            str(first_snapshot.get("data_freshness") or "").upper() == "LIVE"
            and str(first_snapshot.get("data_confidence") or "").upper() == "HIGH"
        ):
            return first_snapshot, "LIVE_REPAIRED", "live_provider_returned_high_confidence_market_data", "live_repaired", provider_path
    except Exception as exc:
        first_exc = exc

    try:
        provider_path.append("token_stats_cache")
        stats_snapshot = _snapshot_from_token_stats(item)
        if stats_snapshot and str(stats_snapshot.get("data_confidence") or "").upper() != "LOW":
            return stats_snapshot, "FALLBACK_REPAIRED", "recent_token_stats_cache_repaired_provider_gap", "fallback_repaired", provider_path
    except Exception:
        pass

    try:
        provider_path.append("birdeye_jupiter")
        fallback_snapshot = _snapshot_from_birdeye_jupiter(item)
        if fallback_snapshot and _f(fallback_snapshot.get("price")) > 0:
            return fallback_snapshot, "FALLBACK_REPAIRED", "birdeye_or_jupiter_repaired_price_context", "fallback_repaired", provider_path
    except Exception:
        pass

    failure_class, reason = _classify_repair_failure(dict(item.get("_previous") or {}), first_snapshot, provider_path, first_exc)
    return first_snapshot, "UNRESOLVED", reason, failure_class, provider_path


def _record_snapshots(rows: list[dict]) -> int:
    if not rows:
        return 0
    inserted = 0
    with _get_conn() as conn:
        _ensure_tables(conn)
        for row in rows:
            values = (
                row.get("ts_utc"),
                row.get("mint"),
                row.get("symbol"),
                row.get("lane_sources"),
                row.get("identity_status"),
                row.get("identity_source"),
                row.get("identity_confidence"),
                row.get("identity_reason"),
                row.get("resolved_mint"),
                row.get("pair_address"),
                row.get("market_source"),
                row.get("data_freshness"),
                row.get("data_confidence"),
                _f(row.get("price")),
                _f(row.get("liquidity")),
                _f(row.get("marketcap")),
                _f(row.get("fdv")),
                _f(row.get("volume_5m_usd")),
                _f(row.get("volume_1h_usd")),
                _f(row.get("volume_6h_usd")),
                _f(row.get("volume_24h_usd")),
                _f(row.get("price_change_1h_percent")),
                _f(row.get("price_change_6h_percent")),
                _f(row.get("price_change_24h_percent")),
                _i(row.get("trade_1h")),
                _i(row.get("trade_24h")),
                _f(row.get("buy_pressure_1h"), 0.0),
                _i(row.get("unique_wallet_1h")),
                _f(row.get("holder_top1_pct")),
                _f(row.get("holder_top10_pct")),
                _f(row.get("token_supply")),
                _f(row.get("risk_score")),
                _f(row.get("pressure_score")),
                _f(row.get("quality_score")),
                json.dumps(list(row.get("reasons") or []), separators=(",", ":")),
                json.dumps(dict(row.get("raw") or {}), separators=(",", ":")),
            )
            conn.execute(
                """
                INSERT INTO token_intelligence_snapshots (
                    ts_utc, mint, symbol, lane_sources, identity_status, identity_source,
                    identity_confidence, identity_reason, resolved_mint, pair_address,
                    market_source, data_freshness, data_confidence, price, liquidity,
                    marketcap, fdv, volume_5m_usd, volume_1h_usd, volume_6h_usd,
                    volume_24h_usd, price_change_1h_percent, price_change_6h_percent,
                    price_change_24h_percent, trade_1h, trade_24h, buy_pressure_1h,
                    unique_wallet_1h, holder_top1_pct, holder_top10_pct, token_supply,
                    risk_score, pressure_score, quality_score, reasons_json, raw_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                values,
            )
            conn.execute(
                """
                INSERT INTO token_intelligence_current (
                    mint, symbol, updated_at, lane_sources, identity_status, identity_source,
                    identity_confidence, identity_reason, resolved_mint, pair_address,
                    market_source, data_freshness, data_confidence, price, liquidity,
                    marketcap, fdv, volume_5m_usd, volume_1h_usd, volume_6h_usd,
                    volume_24h_usd, price_change_1h_percent, price_change_6h_percent,
                    price_change_24h_percent, trade_1h, trade_24h, buy_pressure_1h,
                    unique_wallet_1h, holder_top1_pct, holder_top10_pct, token_supply,
                    risk_score, pressure_score, quality_score, reasons_json, raw_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(mint) DO UPDATE SET
                    symbol=excluded.symbol,
                    updated_at=excluded.updated_at,
                    lane_sources=excluded.lane_sources,
                    identity_status=excluded.identity_status,
                    identity_source=excluded.identity_source,
                    identity_confidence=excluded.identity_confidence,
                    identity_reason=excluded.identity_reason,
                    resolved_mint=excluded.resolved_mint,
                    pair_address=excluded.pair_address,
                    market_source=excluded.market_source,
                    data_freshness=excluded.data_freshness,
                    data_confidence=excluded.data_confidence,
                    price=excluded.price,
                    liquidity=excluded.liquidity,
                    marketcap=excluded.marketcap,
                    fdv=excluded.fdv,
                    volume_5m_usd=excluded.volume_5m_usd,
                    volume_1h_usd=excluded.volume_1h_usd,
                    volume_6h_usd=excluded.volume_6h_usd,
                    volume_24h_usd=excluded.volume_24h_usd,
                    price_change_1h_percent=excluded.price_change_1h_percent,
                    price_change_6h_percent=excluded.price_change_6h_percent,
                    price_change_24h_percent=excluded.price_change_24h_percent,
                    trade_1h=excluded.trade_1h,
                    trade_24h=excluded.trade_24h,
                    buy_pressure_1h=excluded.buy_pressure_1h,
                    unique_wallet_1h=excluded.unique_wallet_1h,
                    holder_top1_pct=excluded.holder_top1_pct,
                    holder_top10_pct=excluded.holder_top10_pct,
                    token_supply=excluded.token_supply,
                    risk_score=excluded.risk_score,
                    pressure_score=excluded.pressure_score,
                    quality_score=excluded.quality_score,
                    reasons_json=excluded.reasons_json,
                    raw_json=excluded.raw_json
                """,
                (values[1], values[2], values[0], *values[3:]),
            )
            inserted += 1
    return inserted


def _backfill_token_stats(rows: list[dict]) -> int:
    try:
        from utils.db import record_memecoin_token_stats_snapshots  # type: ignore
        from utils.token_stats import normalize_birdeye_token_stats  # type: ignore

        out = []
        for row in rows:
            out.append(
                normalize_birdeye_token_stats(
                    {
                        "address": row.get("mint"),
                        "symbol": row.get("symbol"),
                        "ts_utc": row.get("ts_utc"),
                        "price": row.get("price"),
                        "liquidity": row.get("liquidity"),
                        "marketcap": row.get("marketcap"),
                        "fdv": row.get("fdv"),
                        "volume_30m_usd": _f(row.get("volume_1h_usd")) / 2.0,
                        "volume_1h_usd": row.get("volume_1h_usd"),
                        "volume_24h_usd": row.get("volume_24h_usd"),
                        "volume_buy_1h_usd": _f(row.get("volume_1h_usd")) * (_f(row.get("buy_pressure_1h"), 50.0) / 100.0),
                        "volume_sell_1h_usd": _f(row.get("volume_1h_usd")) * (1.0 - (_f(row.get("buy_pressure_1h"), 50.0) / 100.0)),
                        "trade_1h": row.get("trade_1h"),
                        "buy_1h": int(_i(row.get("trade_1h")) * (_f(row.get("buy_pressure_1h"), 50.0) / 100.0)),
                        "sell_1h": max(0, _i(row.get("trade_1h")) - int(_i(row.get("trade_1h")) * (_f(row.get("buy_pressure_1h"), 50.0) / 100.0))),
                        "unique_wallet_1h": row.get("unique_wallet_1h"),
                        "price_change_1h_percent": row.get("price_change_1h_percent"),
                        "price_change_24h_percent": row.get("price_change_24h_percent"),
                        "volume_1h_change_percent": 0.0,
                        "trade_1h_change_percent": 0.0,
                    },
                    symbol=row.get("symbol"),
                )
            )
        return record_memecoin_token_stats_snapshots(out, min_interval_seconds=TOKEN_INTEL_MIN_INTERVAL_SECONDS)
    except Exception:
        return 0


def _live_confirmation_candidates(limit: int = TOKEN_INTEL_CONFIRMATION_LIMIT) -> list[dict]:
    if limit <= 0:
        return []
    try:
        with _get_conn() as conn:
            _ensure_tables(conn)
            rows = conn.execute(
                """
                SELECT *
                FROM token_intelligence_current
                WHERE mint IS NOT NULL AND TRIM(mint) != ''
                  AND symbol IS NOT NULL AND TRIM(symbol) != ''
                  AND data_freshness != 'LIVE'
                  AND identity_status IN ('RESOLVED','ASSERTED')
                  AND quality_score >= 72
                  AND risk_score >= 65
                  AND pressure_score >= 53
                  AND liquidity >= 75000
                  AND volume_24h_usd >= 75000
                ORDER BY
                  CASE WHEN data_freshness='STALE' OR data_confidence='LOW' THEN 1 ELSE 0 END DESC,
                  CASE WHEN data_freshness!='LIVE' THEN 1 ELSE 0 END DESC,
                  CASE WHEN identity_status='RESOLVED' THEN 1 ELSE 0 END DESC,
                  CASE WHEN market_source LIKE 'cache%' THEN 1 ELSE 0 END DESC,
                  quality_score DESC,
                  pressure_score DESC,
                  updated_at ASC
                LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
    except Exception:
        return []

    items: list[dict] = []
    for row in rows:
        items.append(_current_row_to_item(dict(row), source="live_confirmation"))
    return items


def _current_row_to_item(raw: dict, *, source: str) -> dict:
    queue_flags = []
    for key, label in [
        ("missed_context", "missed_runner"),
        ("research_context", "research"),
        ("paper_context", "paper"),
        ("catalyst_context", "catalyst"),
        ("watch_context", "watch_to_entry"),
    ]:
        if _i(raw.get(key)):
            queue_flags.append(label)
    return {
        "mint": raw.get("mint"),
        "symbol": raw.get("symbol"),
        "sources": [source],
        "raw_inputs": [
            {
                "symbol": raw.get("symbol"),
                "mint": raw.get("mint"),
                "price": raw.get("price"),
                "liquidity": raw.get("liquidity"),
                "market_cap": raw.get("marketcap"),
                "fdv": raw.get("fdv"),
                "volume_1h_usd": raw.get("volume_1h_usd"),
                "volume_24h_usd": raw.get("volume_24h_usd"),
                "change_1h": raw.get("price_change_1h_percent"),
                "change_24h": raw.get("price_change_24h_percent"),
                "buy_pressure_1h": raw.get("buy_pressure_1h"),
                "trade_1h": raw.get("trade_1h"),
                "provider_source": raw.get("market_source") or "token_intelligence_current",
            }
        ],
        "_previous": raw,
        "_repair_score": _f(raw.get("repair_score")),
        "_queue_flags": queue_flags,
    }


def _provider_repair_candidates(limit: int = TOKEN_INTEL_PROVIDER_REPAIR_LIMIT) -> list[dict]:
    if limit <= 0:
        return []
    try:
        with _get_conn() as conn:
            _ensure_tables(conn)
            rows = conn.execute(
                """
                SELECT c.*,
                       COALESCE((
                         SELECT COUNT(*)
                         FROM token_provider_repair_events e
                         WHERE e.mint=c.mint
                           AND e.repair_status IN ('UNRESOLVED','RETIRED_UNRESOLVED')
                       ), 0) AS unresolved_count,
                       EXISTS (
                         SELECT 1 FROM decision_journal dj
                         WHERE (dj.mint=c.mint OR UPPER(COALESCE(dj.symbol,''))=UPPER(COALESCE(c.symbol,'')))
                           AND dj.created_ts >= datetime('now','-36 hours')
                           AND UPPER(COALESCE(dj.outcome_label,'')) IN ('BIG_RUNNER','GOOD_RUNNER','GOOD_BUY')
                           AND UPPER(COALESCE(dj.recommended_action,'')) != 'BUY'
                       ) AS missed_context,
                       EXISTS (
                         SELECT 1 FROM memecoin_research_dossiers rd
                         WHERE rd.mint=c.mint
                           AND UPPER(COALESCE(rd.action,'')) NOT IN ('IGNORE','SKIP')
                       ) AS research_context,
                       EXISTS (
                         SELECT 1 FROM memecoin_entry_paper_trades pt
                         WHERE UPPER(COALESCE(pt.symbol,''))=UPPER(COALESCE(c.symbol,''))
                           AND pt.entry_ts >= datetime('now','-36 hours')
                       ) AS paper_context,
                       EXISTS (
                         SELECT 1 FROM memecoin_research_dossiers rd2
                         WHERE rd2.mint=c.mint
                           AND COALESCE(rd2.last_catalyst_confidence,0) >= 50
                       ) AS catalyst_context,
                       EXISTS (
                         SELECT 1 FROM decision_journal dj2
                         WHERE (dj2.mint=c.mint OR UPPER(COALESCE(dj2.symbol,''))=UPPER(COALESCE(c.symbol,'')))
                           AND dj2.created_ts >= datetime('now','-36 hours')
                           AND UPPER(COALESCE(dj2.priority,'')) IN ('WATCH','ENTRY_WATCH','SCOUT_ONLY')
                       ) AS watch_context,
                       (
                         CASE WHEN EXISTS (
                           SELECT 1 FROM decision_journal dj
                           WHERE (dj.mint=c.mint OR UPPER(COALESCE(dj.symbol,''))=UPPER(COALESCE(c.symbol,'')))
                             AND dj.created_ts >= datetime('now','-36 hours')
                             AND UPPER(COALESCE(dj.outcome_label,'')) IN ('BIG_RUNNER','GOOD_RUNNER','GOOD_BUY')
                             AND UPPER(COALESCE(dj.recommended_action,'')) != 'BUY'
                         ) THEN 100 ELSE 0 END
                         + CASE WHEN EXISTS (
                           SELECT 1 FROM memecoin_research_dossiers rd
                           WHERE rd.mint=c.mint
                             AND UPPER(COALESCE(rd.action,'')) NOT IN ('IGNORE','SKIP')
                         ) THEN 70 ELSE 0 END
                         + CASE WHEN EXISTS (
                           SELECT 1 FROM memecoin_entry_paper_trades pt
                           WHERE UPPER(COALESCE(pt.symbol,''))=UPPER(COALESCE(c.symbol,''))
                             AND pt.entry_ts >= datetime('now','-36 hours')
                         ) THEN 60 ELSE 0 END
                         + CASE WHEN EXISTS (
                           SELECT 1 FROM memecoin_research_dossiers rd2
                           WHERE rd2.mint=c.mint
                             AND COALESCE(rd2.last_catalyst_confidence,0) >= 50
                         ) THEN 45 ELSE 0 END
                         + CASE WHEN EXISTS (
                           SELECT 1 FROM decision_journal dj2
                           WHERE (dj2.mint=c.mint OR UPPER(COALESCE(dj2.symbol,''))=UPPER(COALESCE(c.symbol,'')))
                             AND dj2.created_ts >= datetime('now','-36 hours')
                             AND UPPER(COALESCE(dj2.priority,'')) IN ('WATCH','ENTRY_WATCH','SCOUT_ONLY')
                         ) THEN 35 ELSE 0 END
                         + COALESCE(c.quality_score,0) * 0.35
                         + COALESCE(c.pressure_score,0) * 0.25
                         - COALESCE((
                           SELECT COUNT(*)
                           FROM token_provider_repair_events e
                           WHERE e.mint=c.mint
                             AND e.repair_status IN ('UNRESOLVED','RETIRED_UNRESOLVED')
                         ), 0) * 12
                       ) AS repair_score
                FROM token_intelligence_current c
                WHERE c.mint IS NOT NULL AND TRIM(c.mint) != ''
                  AND c.symbol IS NOT NULL AND TRIM(c.symbol) != ''
                  AND (
                    UPPER(COALESCE(c.data_freshness, ''))='STALE'
                    OR UPPER(COALESCE(c.data_confidence, ''))='LOW'
                    OR LOWER(COALESCE(c.market_source, '')) LIKE 'cache%'
                  )
                  AND COALESCE(c.identity_status, '') IN ('RESOLVED','ASSERTED')
                  AND (
                    COALESCE(c.provider_repair_retired_until_unix, 0) <= ?
                    OR COALESCE(c.quality_score,0) >= 72
                    OR COALESCE(c.pressure_score,0) >= 60
                  )
                ORDER BY
                  repair_score DESC,
                  COALESCE(unresolved_count, 0) ASC,
                  COALESCE(c.quality_score,0) DESC,
                  COALESCE(c.pressure_score,0) DESC,
                  c.updated_at ASC
                LIMIT ?
                """,
                (time.time(), int(limit),),
            ).fetchall()
    except Exception:
        try:
            with _get_conn() as conn:
                _ensure_tables(conn)
                rows = conn.execute(
                    """
                    SELECT c.*,
                           0 AS unresolved_count,
                           0 AS missed_context,
                           0 AS research_context,
                           0 AS paper_context,
                           0 AS catalyst_context,
                           0 AS watch_context,
                           (COALESCE(c.quality_score,0) * 0.35 + COALESCE(c.pressure_score,0) * 0.25) AS repair_score
                    FROM token_intelligence_current c
                    WHERE c.mint IS NOT NULL AND TRIM(c.mint) != ''
                      AND c.symbol IS NOT NULL AND TRIM(c.symbol) != ''
                      AND (
                        UPPER(COALESCE(c.data_freshness, ''))='STALE'
                        OR UPPER(COALESCE(c.data_confidence, ''))='LOW'
                        OR LOWER(COALESCE(c.market_source, '')) LIKE 'cache%'
                      )
                      AND COALESCE(c.identity_status, '') IN ('RESOLVED','ASSERTED')
                    ORDER BY repair_score DESC, c.updated_at ASC
                    LIMIT ?
                    """,
                    (int(limit),),
                ).fetchall()
        except Exception:
            return []
    return [_current_row_to_item(dict(row), source="provider_repair") for row in rows]


def _repair_escalation_lane(event: dict) -> tuple[str, str, int] | None:
    status = str(event.get("repair_status") or "").upper()
    failure = str(event.get("failure_class") or "").strip().lower()
    flags = {str(x).strip().lower() for x in list(event.get("queue_flags") or []) if str(x).strip()}
    score = _f(event.get("repair_score"))
    high_priority = score >= 90 or bool(flags & {"missed_runner", "research", "paper", "catalyst", "watch_to_entry"})

    if status in {"LIVE_REPAIRED", "FALLBACK_REPAIRED"}:
        return None
    if status == "RETIRED_UNRESOLVED" and not high_priority:
        return ("RETIRE_OR_SUPPRESS", "SUPPRESSED", 24)
    if failure in {"bad_ca_or_pair_mismatch", "identity_mismatch"}:
        return ("IDENTITY_RESEARCH", "QUEUED", 4)
    if failure in {"provider_budget_or_rate_limit", "temporary_error", "provider_error"}:
        return ("RETRY_PROVIDER", "QUEUED", 2)
    if failure in {"provider_miss", "no_market_data", "dexscreener_no_pair"}:
        if high_priority:
            return ("RESEARCH_CATALYST_REFRESH", "QUEUED", 4)
        return ("RETIRE_OR_SUPPRESS", "SUPPRESSED", 24)
    if failure in {"low_liquidity"}:
        return ("MANUAL_REVIEW", "QUEUED" if high_priority else "WATCHING", 12)
    if high_priority:
        return ("MANUAL_REVIEW", "QUEUED", 8)
    return ("RETIRE_OR_SUPPRESS", "SUPPRESSED", 24)


def _route_repair_escalation(conn, event: dict, event_id: int | None) -> None:
    mint = str(event.get("mint") or "").strip()
    if not mint:
        return
    now = str(event.get("ts_utc") or _now_iso())
    status = str(event.get("repair_status") or "").upper()
    if status in {"LIVE_REPAIRED", "FALLBACK_REPAIRED"}:
        conn.execute(
            """
            UPDATE provider_repair_escalations
               SET updated_ts=?,
                   repair_status=?,
                   escalation_status='RESOLVED_REPAIRED',
                   outcome_label='HELPED_REPAIR',
                   last_action_ts=?,
                   source_event_id=COALESCE(?, source_event_id),
                   raw_json=?
             WHERE mint=?
               AND escalation_status IN ('QUEUED','WATCHING','FORCE_REFRESH_REQUESTED','STALE_REVIEW','REVIEW_COMPLETE_STILL_BLOCKED')
            """,
            (now, status, now, event_id, json.dumps(event, separators=(",", ":")), mint),
        )
        return

    route = _repair_escalation_lane(event)
    if not route:
        return
    lane, escalation_status, sla_hours = route
    flags_json = json.dumps(list(event.get("queue_flags") or []), separators=(",", ":"))
    raw_json = json.dumps(event, separators=(",", ":"))
    sla_due = (datetime.now(timezone.utc) + timedelta(hours=sla_hours)).isoformat()
    operator_decision = "AUTO_SUPPRESS" if escalation_status == "SUPPRESSED" else "PENDING"
    outcome_label = "LOW_VALUE_SUPPRESSED" if escalation_status == "SUPPRESSED" else None
    conn.execute(
        """
        INSERT INTO provider_repair_escalations (
            created_ts, updated_ts, mint, symbol, failure_class, repair_status,
            escalation_lane, escalation_status, priority_score, queue_flags_json,
            provider_path, reason, source_event_id, operator_decision, operator_note,
            outcome_label, last_action_ts, sla_due_ts, raw_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?)
        ON CONFLICT(mint, escalation_lane) DO UPDATE SET
            updated_ts=excluded.updated_ts,
            symbol=excluded.symbol,
            failure_class=excluded.failure_class,
            repair_status=excluded.repair_status,
            escalation_status=CASE
                WHEN provider_repair_escalations.escalation_status IN ('DISMISSED','RESOLVED_REPAIRED','REVIEW_COMPLETE_STILL_BLOCKED') THEN provider_repair_escalations.escalation_status
                ELSE excluded.escalation_status
            END,
            priority_score=MAX(COALESCE(provider_repair_escalations.priority_score, 0), COALESCE(excluded.priority_score, 0)),
            queue_flags_json=excluded.queue_flags_json,
            provider_path=excluded.provider_path,
            reason=excluded.reason,
            source_event_id=excluded.source_event_id,
            operator_decision=CASE
                WHEN provider_repair_escalations.escalation_status IN ('DISMISSED','RESOLVED_REPAIRED','REVIEW_COMPLETE_STILL_BLOCKED') THEN provider_repair_escalations.operator_decision
                ELSE excluded.operator_decision
            END,
            outcome_label=CASE
                WHEN provider_repair_escalations.escalation_status IN ('DISMISSED','RESOLVED_REPAIRED','REVIEW_COMPLETE_STILL_BLOCKED') THEN provider_repair_escalations.outcome_label
                ELSE excluded.outcome_label
            END,
            last_action_ts=excluded.last_action_ts,
            sla_due_ts=excluded.sla_due_ts,
            raw_json=excluded.raw_json
        """,
        (
            now,
            now,
            mint,
            event.get("symbol"),
            event.get("failure_class"),
            status,
            lane,
            escalation_status,
            _f(event.get("repair_score")),
            flags_json,
            event.get("provider_path"),
            event.get("reason"),
            event_id,
            operator_decision,
            outcome_label,
            now,
            sla_due,
            raw_json,
        ),
    )


def _record_provider_repair_events(events: list[dict]) -> int:
    if not events:
        return 0
    inserted = 0
    try:
        with _get_conn() as conn:
            _ensure_tables(conn)
            for event in events:
                cur = conn.execute(
                    """
                    INSERT INTO token_provider_repair_events (
                        ts_utc, mint, symbol, previous_source, previous_freshness,
                        previous_confidence, previous_quality, previous_pressure,
                        repair_status, new_source, new_freshness, new_confidence,
                        new_quality, new_pressure, latency_ms, unresolved_count, reason,
                        failure_class, provider_path, repair_score, queue_flags_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event.get("ts_utc"),
                        event.get("mint"),
                        event.get("symbol"),
                        event.get("previous_source"),
                        event.get("previous_freshness"),
                        event.get("previous_confidence"),
                        _f(event.get("previous_quality")),
                        _f(event.get("previous_pressure")),
                        event.get("repair_status"),
                        event.get("new_source"),
                        event.get("new_freshness"),
                        event.get("new_confidence"),
                        _f(event.get("new_quality")),
                        _f(event.get("new_pressure")),
                        _i(event.get("latency_ms")),
                        _i(event.get("unresolved_count")),
                        event.get("reason"),
                        event.get("failure_class"),
                        event.get("provider_path"),
                        _f(event.get("repair_score")),
                        json.dumps(list(event.get("queue_flags") or []), separators=(",", ":")),
                    ),
                )
                try:
                    _route_repair_escalation(conn, event, int(cur.lastrowid or 0) or None)
                except Exception:
                    pass
                if event.get("mint"):
                    retired_until = None
                    if str(event.get("repair_status") or "").upper() == "RETIRED_UNRESOLVED":
                        retired_until = time.time() + 24 * 3600
                    conn.execute(
                        """
                        UPDATE token_intelligence_current
                        SET provider_repair_status=?,
                            provider_repair_failure_class=?,
                            provider_repair_updated_at=?,
                            provider_repair_retired_until_unix=?
                        WHERE mint=?
                        """,
                        (
                            event.get("repair_status"),
                            event.get("failure_class"),
                            event.get("ts_utc"),
                            retired_until,
                            event.get("mint"),
                        ),
                    )
                inserted += 1
    except Exception:
        return inserted
    return inserted


def _latest_escalation_outcome(conn, row: dict) -> dict:
    mint = str(row.get("mint") or "").strip()
    symbol = str(row.get("symbol") or "").strip().upper()
    created = _parse_iso(row.get("created_ts") or row.get("updated_ts")) or datetime.now(timezone.utc)
    since = (created - timedelta(hours=6)).isoformat()
    params: list[Any] = []
    clauses = []
    if mint:
        clauses.append("mint=?")
        params.append(mint)
    if symbol:
        clauses.append("UPPER(symbol)=?")
        params.append(symbol)
    if not clauses:
        return {"source": "none", "status": "NO_IDENTITY"}
    where = " OR ".join(clauses)
    try:
        outcome = conn.execute(
            f"""
            SELECT scanned_at, symbol, mint, status, outcome_label,
                   return_1h_pct, return_4h_pct, return_24h_pct,
                   max_return_pct, max_drawdown_pct
            FROM memecoin_signal_outcomes
            WHERE ({where})
              AND COALESCE(scanned_at, '') >= ?
            ORDER BY
                CASE WHEN COALESCE(outcome_label, '') NOT IN ('', 'PENDING', 'TRACKING') THEN 1 ELSE 0 END DESC,
                COALESCE(max_return_pct, -999999) DESC,
                scanned_at DESC
            LIMIT 1
            """,
            tuple(params + [since]),
        ).fetchone()
        if outcome:
            return {"source": "memecoin_signal_outcomes", "row": dict(outcome)}
    except Exception:
        pass

    try:
        current = conn.execute(
            """
            SELECT mint, symbol, updated_at, marketcap, liquidity, data_freshness,
                   data_confidence, provider_repair_status, provider_repair_failure_class
            FROM token_intelligence_current
            WHERE mint=? OR UPPER(symbol)=?
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (mint, symbol),
        ).fetchone()
        if current:
            snapshot = conn.execute(
                """
                SELECT marketcap, liquidity, ts_utc
                FROM token_intelligence_snapshots
                WHERE mint=?
                  AND COALESCE(ts_utc, '') >= ?
                  AND COALESCE(marketcap, 0) > 0
                ORDER BY ts_utc ASC
                LIMIT 1
                """,
                (mint, since),
            ).fetchone()
            cur = dict(current)
            base = dict(snapshot) if snapshot else {}
            return {
                "source": "token_intelligence_current",
                "row": {
                    "scanned_at": cur.get("updated_at"),
                    "symbol": cur.get("symbol"),
                    "mint": cur.get("mint"),
                    "status": cur.get("data_freshness"),
                    "outcome_label": None,
                    "return_1h_pct": None,
                    "return_4h_pct": None,
                    "return_24h_pct": _pct_change(cur.get("marketcap"), base.get("marketcap")),
                    "max_return_pct": _pct_change(cur.get("marketcap"), base.get("marketcap")),
                    "max_drawdown_pct": None,
                    "marketcap": cur.get("marketcap"),
                    "data_confidence": cur.get("data_confidence"),
                    "provider_repair_status": cur.get("provider_repair_status"),
                },
            }
    except Exception:
        pass
    return {"source": "none", "status": "NO_OUTCOME_ROW"}


def _classify_escalation_correctness(escalation: dict, outcome: dict, now: datetime) -> dict:
    status = str(escalation.get("escalation_status") or "").upper()
    lane = str(escalation.get("escalation_lane") or "").upper()
    failure = str(escalation.get("failure_class") or "").lower()
    created = _parse_iso(escalation.get("created_ts") or escalation.get("updated_ts")) or now
    age_h = max(0.0, (now - created).total_seconds() / 3600.0)
    row = dict(outcome.get("row") or {})
    label = str(row.get("outcome_label") or "").upper()
    max_return = _f(row.get("max_return_pct"))
    ret_4h = _f(row.get("return_4h_pct"))
    ret_24h = _f(row.get("return_24h_pct"))
    marketcap = _f(row.get("marketcap"))
    bullish_labels = {"BIG_RUNNER", "GOOD_RUNNER", "GOOD_BUY", "WIN", "PROFIT"}
    weak_labels = {"BAD_BUY", "WEAK_BUY", "FLAT", "LOSS"}
    is_suppression = status == "SUPPRESSED" or lane == "RETIRE_OR_SUPPRESS"
    is_repaired = status == "RESOLVED_REPAIRED"

    if is_repaired:
        return {
            "outcome_status": "COMPLETE",
            "blocker_correctness": "REPAIR_RESOLVED",
            "confidence_impact": 0.0,
            "reason": "Provider repair later recovered usable market data.",
        }
    if label in bullish_labels or max_return >= 35.0 or ret_24h >= 35.0:
        return {
            "outcome_status": "COMPLETE",
            "blocker_correctness": "SUPPRESSION_TOO_AGGRESSIVE" if is_suppression else "BLOCK_MISSED_RUNNER",
            "confidence_impact": -12.0 if is_suppression else -15.0,
            "reason": f"Blocked escalation later showed bullish outcome ({label or round(max_return, 1)}).",
        }
    if label in weak_labels or max(max_return, ret_4h, ret_24h) <= 10.0 and age_h >= 4.0:
        return {
            "outcome_status": "COMPLETE",
            "blocker_correctness": "SUPPRESSION_CORRECT" if is_suppression else "BLOCK_CORRECT",
            "confidence_impact": 5.0 if failure in {"no_market_data", "bad_ca_or_pair_mismatch", "provider_miss"} else 3.0,
            "reason": f"Blocked escalation did not produce a meaningful runner within the review window ({label or 'low_return'}).",
        }
    if marketcap <= 0 and age_h >= 4.0 and failure in {"no_market_data", "bad_ca_or_pair_mismatch", "provider_miss"}:
        return {
            "outcome_status": "COMPLETE",
            "blocker_correctness": "SUPPRESSION_CORRECT" if is_suppression else "BLOCK_CORRECT",
            "confidence_impact": 4.0,
            "reason": "No usable market data or positive market cap appeared after review.",
        }
    return {
        "outcome_status": "PENDING",
        "blocker_correctness": "PENDING_OUTCOME",
        "confidence_impact": 0.0,
        "reason": "Waiting for enough post-review outcome evidence.",
    }


def evaluate_provider_escalation_outcomes(limit: int = 120) -> dict:
    """Backtest reviewed provider escalations against later market outcomes."""
    now = datetime.now(timezone.utc)
    stale_cutoff = (now - timedelta(minutes=55)).isoformat()
    checked = 0
    updated = 0
    counts: dict[str, int] = {}
    try:
        with _get_conn() as conn:
            _ensure_tables(conn)
            rows = [
                dict(r)
                for r in conn.execute(
                    """
                    SELECT *
                    FROM provider_repair_escalations
                    WHERE escalation_status IN (
                        'REVIEW_COMPLETE_STILL_BLOCKED',
                        'SUPPRESSED',
                        'RESOLVED_REPAIRED'
                    )
                      AND (
                        outcome_check_ts IS NULL
                        OR outcome_status='PENDING'
                        OR outcome_check_ts < ?
                      )
                    ORDER BY COALESCE(last_action_ts, updated_ts, created_ts) DESC
                    LIMIT ?
                    """,
                    (stale_cutoff, max(1, int(limit))),
                ).fetchall()
            ]
            for row in rows:
                checked += 1
                outcome = _latest_escalation_outcome(conn, row)
                classification = _classify_escalation_correctness(row, outcome, now)
                out_row = dict(outcome.get("row") or {})
                correctness = classification.get("blocker_correctness") or "PENDING_OUTCOME"
                counts[str(correctness)] = counts.get(str(correctness), 0) + 1
                conn.execute(
                    """
                    UPDATE provider_repair_escalations
                       SET outcome_check_ts=?,
                           outcome_status=?,
                           outcome_source=?,
                           outcome_return_1h_pct=?,
                           outcome_return_4h_pct=?,
                           outcome_return_24h_pct=?,
                           outcome_max_return_pct=?,
                           outcome_max_drawdown_pct=?,
                           blocker_correctness=?,
                           confidence_impact=?,
                           outcome_reason=?
                     WHERE id=?
                    """,
                    (
                        now.isoformat(),
                        classification.get("outcome_status"),
                        outcome.get("source"),
                        _f(out_row.get("return_1h_pct")) if out_row.get("return_1h_pct") is not None else None,
                        _f(out_row.get("return_4h_pct")) if out_row.get("return_4h_pct") is not None else None,
                        _f(out_row.get("return_24h_pct")) if out_row.get("return_24h_pct") is not None else None,
                        _f(out_row.get("max_return_pct")) if out_row.get("max_return_pct") is not None else None,
                        _f(out_row.get("max_drawdown_pct")) if out_row.get("max_drawdown_pct") is not None else None,
                        correctness,
                        _f(classification.get("confidence_impact")),
                        str(classification.get("reason") or "")[:500],
                        int(row.get("id") or 0),
                    ),
                )
                updated += 1
    except Exception as exc:
        return {"checked": checked, "updated": updated, "counts": counts, "error": str(exc)}
    return {"checked": checked, "updated": updated, "counts": counts}


def _record_live_confirmation_status(payload: dict) -> None:
    try:
        with _get_conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO kv_store (key, value) VALUES (?, ?)",
                ("token_live_confirmation_status", json.dumps(payload, separators=(",", ":"))),
            )
    except Exception:
        pass


def token_intelligence_step(*, force: bool = False) -> dict:
    now = time.time()
    try:
        with _get_conn() as conn:
            row = conn.execute("SELECT value FROM kv_store WHERE key='token_intelligence_status'").fetchone()
            status = _json_loads(row[0] if row else None, {}) or {}
        last_ts = _f(status.get("last_run_unix"))
        if not force and last_ts and now - last_ts < TOKEN_INTEL_MIN_INTERVAL_SECONDS:
            return {**status, "skipped": True, "reason": "min_interval"}
    except Exception:
        pass

    tokens = _collect_tokens()
    rows: list[dict] = []
    for idx, item in enumerate(tokens):
        try:
            rows.append(
                _build_snapshot(
                    item,
                    live_enabled=idx < TOKEN_INTEL_LIVE_MARKET_LIMIT,
                    holder_enabled=idx < TOKEN_INTEL_RPC_HOLDER_LIMIT,
                )
            )
        except Exception:
            continue

    confirmation_rows: list[dict] = []
    confirmation_items = _live_confirmation_candidates(TOKEN_INTEL_CONFIRMATION_LIMIT)
    for item in confirmation_items:
        try:
            confirmation_rows.append(
                _build_snapshot(
                    item,
                    live_enabled=True,
                    holder_enabled=False,
                    reserve_market=True,
                )
            )
        except Exception:
            continue
    if confirmation_rows:
        rows.extend(confirmation_rows)

    repair_rows: list[dict] = []
    repair_events: list[dict] = []
    repair_items = _provider_repair_candidates(TOKEN_INTEL_PROVIDER_REPAIR_LIMIT)
    for item in repair_items:
        previous = dict(item.get("_previous") or {})
        started = time.time()
        snapshot: dict | None = None
        repair_status = "UNRESOLVED"
        reason = "live_provider_missing_or_incomplete"
        failure_class = "provider_miss"
        provider_path: list[str] = []
        snapshot, repair_status, reason, failure_class, provider_path = _build_provider_repair_snapshot(item)
        latency_ms = int(max(0.0, (time.time() - started) * 1000.0))
        previous_unresolved = _i(previous.get("unresolved_count"))
        repaired = bool(
            snapshot
            and repair_status in {"LIVE_REPAIRED", "FALLBACK_REPAIRED"}
            and str(snapshot.get("data_confidence") or "").upper() != "LOW"
        )
        high_value_flags = set(item.get("_queue_flags") or [])
        high_value = _f(item.get("_repair_score")) >= 80 or bool(high_value_flags & {"missed_runner", "research", "paper", "catalyst", "watch_to_entry"})
        if repaired and snapshot:
            repair_rows.append(snapshot)
        elif (
            previous_unresolved >= 2
            and not high_value
            and failure_class in {"no_market_data", "dexscreener_no_pair", "dead_or_inactive_token", "low_liquidity", "provider_miss"}
            and _f(previous.get("quality_score")) < 68
            and _f(previous.get("pressure_score")) < 62
        ):
            repair_status = "RETIRED_UNRESOLVED"
            reason = f"retired_after_repeated_{failure_class}"
        repair_events.append(
            {
                "ts_utc": _now_iso(),
                "mint": previous.get("mint") or item.get("mint"),
                "symbol": previous.get("symbol") or item.get("symbol"),
                "previous_source": previous.get("market_source"),
                "previous_freshness": previous.get("data_freshness"),
                "previous_confidence": previous.get("data_confidence"),
                "previous_quality": previous.get("quality_score"),
                "previous_pressure": previous.get("pressure_score"),
                "repair_status": repair_status,
                "new_source": (snapshot or {}).get("market_source"),
                "new_freshness": (snapshot or {}).get("data_freshness"),
                "new_confidence": (snapshot or {}).get("data_confidence"),
                "new_quality": (snapshot or {}).get("quality_score"),
                "new_pressure": (snapshot or {}).get("pressure_score"),
                "latency_ms": latency_ms,
                "unresolved_count": previous_unresolved + (0 if repaired else 1),
                "reason": reason,
                "failure_class": failure_class,
                "provider_path": "->".join(provider_path),
                "repair_score": _f(item.get("_repair_score")),
                "queue_flags": list(item.get("_queue_flags") or []),
            }
        )
    if repair_rows:
        rows.extend(repair_rows)

    inserted = _record_snapshots(rows)
    token_stats_inserted = _backfill_token_stats(rows)
    repair_events_inserted = _record_provider_repair_events(repair_events)
    escalation_outcomes = evaluate_provider_escalation_outcomes(limit=120)
    conflicts = len([r for r in rows if str(r.get("identity_status") or "").upper() in {"QUARANTINED", "MISMATCH"}])
    live = len([r for r in rows if str(r.get("data_freshness") or "").upper() == "LIVE"])
    high_conf = len([r for r in rows if str(r.get("data_confidence") or "").upper() == "HIGH"])
    payload = {
        "watchdog": "TOKEN_INTELLIGENCE",
        "status": "ACTIVE" if rows else "NO_TOKENS",
        "checked_at": _now_iso(),
        "last_run_unix": now,
        "tracked_count": len(tokens),
        "snapshot_count": inserted,
        "token_stats_inserted": token_stats_inserted,
        "provider_repair_events_inserted": repair_events_inserted,
        "live_count": live,
        "high_confidence_count": high_conf,
        "identity_conflicts": conflicts,
        "live_confirmation": {
            "candidate_count": len(confirmation_items),
            "snapshot_count": len(confirmation_rows),
            "live_count": len([r for r in confirmation_rows if str(r.get("data_freshness") or "").upper() == "LIVE"]),
            "symbols": [r.get("symbol") for r in confirmation_rows[:10] if r.get("symbol")],
        },
        "provider_repair": {
            "candidate_count": len(repair_items),
            "event_count": repair_events_inserted,
            "live_repaired_count": len([e for e in repair_events if e.get("repair_status") == "LIVE_REPAIRED"]),
            "fallback_repaired_count": len([e for e in repair_events if e.get("repair_status") == "FALLBACK_REPAIRED"]),
            "unresolved_count": len([e for e in repair_events if e.get("repair_status") == "UNRESOLVED"]),
            "retired_count": len([e for e in repair_events if e.get("repair_status") == "RETIRED_UNRESOLVED"]),
            "failure_classes": sorted({str(e.get("failure_class") or "unknown") for e in repair_events}),
            "symbols": [e.get("symbol") for e in repair_events[:10] if e.get("symbol")],
        },
        "provider_escalation_outcomes": escalation_outcomes,
        "top_symbols": [r.get("symbol") for r in rows[:10] if r.get("symbol")],
        "detail": f"Independent token intelligence refreshed {inserted}/{len(tokens)} focused token(s).",
    }
    _record_live_confirmation_status(
        {
            "watchdog": "TOKEN_LIVE_CONFIRMATION",
            "status": "ACTIVE" if confirmation_items else "NO_CANDIDATES",
            "checked_at": payload["checked_at"],
            "candidate_count": len(confirmation_items),
            "snapshot_count": len(confirmation_rows),
            "live_count": payload["live_confirmation"]["live_count"],
            "symbols": payload["live_confirmation"]["symbols"],
            "detail": (
                f"Confirmed {payload['live_confirmation']['live_count']}/{len(confirmation_items)} priority token(s) with reserved live market budget."
                if confirmation_items
                else "No non-live high-quality token needed live confirmation."
            ),
        }
    )
    try:
        from utils import orchestrator  # type: ignore

        orchestrator.heartbeat("token_intelligence")
    except Exception:
        pass
    try:
        with _get_conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO kv_store (key, value) VALUES (?, ?)",
                ("token_intelligence_status", json.dumps(payload, separators=(",", ":"))),
            )
    except Exception:
        pass
    return payload


def get_token_intelligence_summary(limit: int = 12) -> dict:
    try:
        with _get_conn() as conn:
            _ensure_tables(conn)
            status_row = conn.execute("SELECT value FROM kv_store WHERE key='token_intelligence_status'").fetchone()
            rows = conn.execute(
                """
                SELECT *
                FROM token_intelligence_current
                ORDER BY quality_score DESC, updated_at DESC
                LIMIT ?
                """,
                (max(1, int(limit)),),
            ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            item["reasons"] = _json_loads(item.pop("reasons_json", None), []) or []
            item.pop("raw_json", None)
            items.append(item)
        return {"status": _json_loads(status_row[0] if status_row else None, {}) or {}, "items": items}
    except Exception as exc:
        return {"status": {"status": "ERROR", "detail": str(exc)}, "items": []}
