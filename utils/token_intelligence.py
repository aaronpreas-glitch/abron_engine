from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
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
                       ), 0) AS unresolved_count
                FROM token_intelligence_current c
                WHERE c.mint IS NOT NULL AND TRIM(c.mint) != ''
                  AND c.symbol IS NOT NULL AND TRIM(c.symbol) != ''
                  AND (
                    UPPER(COALESCE(c.data_freshness, ''))='STALE'
                    OR UPPER(COALESCE(c.data_confidence, ''))='LOW'
                    OR LOWER(COALESCE(c.market_source, '')) LIKE 'cache%'
                  )
                  AND COALESCE(c.identity_status, '') IN ('RESOLVED','ASSERTED')
                ORDER BY
                  CASE WHEN COALESCE(c.quality_score,0) >= 72 OR COALESCE(c.pressure_score,0) >= 60 THEN 1 ELSE 0 END DESC,
                  COALESCE(unresolved_count, 0) ASC,
                  COALESCE(c.quality_score,0) DESC,
                  COALESCE(c.pressure_score,0) DESC,
                  c.updated_at ASC
                LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
    except Exception:
        return []
    return [_current_row_to_item(dict(row), source="provider_repair") for row in rows]


def _record_provider_repair_events(events: list[dict]) -> int:
    if not events:
        return 0
    inserted = 0
    try:
        with _get_conn() as conn:
            _ensure_tables(conn)
            for event in events:
                conn.execute(
                    """
                    INSERT INTO token_provider_repair_events (
                        ts_utc, mint, symbol, previous_source, previous_freshness,
                        previous_confidence, previous_quality, previous_pressure,
                        repair_status, new_source, new_freshness, new_confidence,
                        new_quality, new_pressure, latency_ms, unresolved_count, reason
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    ),
                )
                inserted += 1
    except Exception:
        return inserted
    return inserted


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
        try:
            snapshot = _build_snapshot(
                item,
                live_enabled=True,
                holder_enabled=False,
                reserve_market=True,
            )
        except Exception:
            snapshot = None
        latency_ms = int(max(0.0, (time.time() - started) * 1000.0))
        previous_unresolved = _i(previous.get("unresolved_count"))
        live_repaired = bool(
            snapshot
            and str(snapshot.get("data_freshness") or "").upper() == "LIVE"
            and str(snapshot.get("data_confidence") or "").upper() == "HIGH"
        )
        if live_repaired and snapshot:
            repair_status = "LIVE_REPAIRED"
            reason = "live_provider_returned_high_confidence_market_data"
            repair_rows.append(snapshot)
        elif previous_unresolved >= 2 and _f(previous.get("quality_score")) < 65 and _f(previous.get("pressure_score")) < 58:
            repair_status = "RETIRED_UNRESOLVED"
            reason = "repeated_provider_miss_low_priority_cache_row"
        else:
            repair_status = "UNRESOLVED"
            reason = "live_provider_missing_or_incomplete"
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
                "unresolved_count": previous_unresolved + (0 if live_repaired else 1),
                "reason": reason,
            }
        )
    if repair_rows:
        rows.extend(repair_rows)

    inserted = _record_snapshots(rows)
    token_stats_inserted = _backfill_token_stats(rows)
    repair_events_inserted = _record_provider_repair_events(repair_events)
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
            "unresolved_count": len([e for e in repair_events if e.get("repair_status") == "UNRESOLVED"]),
            "retired_count": len([e for e in repair_events if e.get("repair_status") == "RETIRED_UNRESOLVED"]),
            "symbols": [e.get("symbol") for e in repair_events[:10] if e.get("symbol")],
        },
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
