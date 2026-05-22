from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone
from typing import Any


def norm_symbol(value: str | None) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def _f(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def _market_cap(item: dict[str, Any]) -> float:
    return _f(
        item.get("mcap_usd")
        or item.get("market_cap")
        or item.get("market_cap_usd")
        or item.get("fdv")
        or item.get("latest_mcap_usd")
        or item.get("initial_mcap_usd")
    )


def _liquidity(item: dict[str, Any]) -> float:
    return _f(item.get("liquidity_usd") or item.get("liquidity") or item.get("latest_liquidity_usd"))


def _volume_24h(item: dict[str, Any]) -> float:
    return _f(item.get("volume_24h") or item.get("latest_volume_24h"))


def _pair_from_item(item: dict[str, Any]) -> dict[str, Any]:
    symbol = str(item.get("symbol") or item.get("base_symbol") or "").upper()
    mint = str(item.get("mint") or item.get("address") or item.get("token_mint") or "").strip()
    price = _f(item.get("price") or item.get("latest_price"), 0.0)
    mcap = _market_cap(item)
    liq = _liquidity(item)
    vol24 = _volume_24h(item)
    vol6 = _f(item.get("volume_6h"), 0.0)
    vol1 = _f(item.get("volume_1h"), 0.0)
    vol5m = _f(item.get("volume_5m"), 0.0)
    if not vol1 and vol24:
        vol1 = vol24 / 24.0
    if not vol6 and vol24:
        vol6 = vol24 / 4.0
    pair_address = str(item.get("pair_address") or item.get("token_pair_address") or "").strip()
    return {
        "chainId": "solana",
        "pairAddress": pair_address or None,
        "baseToken": {
            "address": mint,
            "symbol": symbol,
            "name": item.get("name") or symbol,
        },
        "quoteToken": {"symbol": str(item.get("quote_symbol") or "SOL").upper()},
        "priceUsd": str(price) if price > 0 else None,
        "marketCap": mcap or None,
        "fdv": _f(item.get("fdv"), mcap) or None,
        "liquidity": {"usd": liq},
        "volume": {"h24": vol24, "h6": vol6, "h1": vol1, "m5": vol5m},
        "priceChange": {
            "h24": _f(item.get("change_24h") or item.get("latest_change_24h")),
            "h6": _f(item.get("change_6h")),
            "h1": _f(item.get("change_1h") or item.get("latest_change_1h")),
        },
        "txns": {
            "h1": {"buys": int(_f(item.get("txns_h1"))), "sells": 0},
            "h24": {"buys": int(_f(item.get("txns_h24"))), "sells": 0},
        },
        "_resolver_source": item.get("_resolver_source") or item.get("source"),
    }


def _candidate_from_item(
    item: dict[str, Any],
    *,
    query_norm: str,
    source: str,
    reason: str,
    market_cap_hint: float | None,
) -> dict[str, Any] | None:
    symbol_norm = norm_symbol(item.get("symbol"))
    name_norm = norm_symbol(item.get("name"))
    mint = str(item.get("mint") or item.get("address") or item.get("token_mint") or "").strip()
    if not mint:
        return None
    if symbol_norm == query_norm:
        match_rank = 4
        match_source = "exact_symbol"
    elif name_norm == query_norm:
        match_rank = 3
        match_source = "exact_name"
    elif symbol_norm and (symbol_norm.startswith(query_norm) or query_norm.startswith(symbol_norm)):
        match_rank = 2
        match_source = "symbol_prefix"
    elif name_norm and (query_norm in name_norm or name_norm in query_norm):
        match_rank = 1
        match_source = "name_partial"
    else:
        return None

    liq = _liquidity(item)
    vol24 = _volume_24h(item)
    mcap = _market_cap(item)
    source_bonus = {
        "conviction_market_cache": 18,
        "runner_radar": 14,
        "memecoin_scan_cache": 12,
        "geckoterminal_trending": 8,
        "dexscreener_search": 4,
    }.get(source, 0)
    hint_bonus = 0.0
    if market_cap_hint and market_cap_hint > 0 and mcap > 0:
        ratio = max(mcap, market_cap_hint) / max(1.0, min(mcap, market_cap_hint))
        hint_bonus = max(-25.0, 16.0 - (ratio - 1.0) * 18.0)
    depth_bonus = min(12.0, math.log10(max(liq, 1.0)) * 1.5) + min(10.0, math.log10(max(vol24, 1.0)) * 1.2)
    score = match_rank * 30.0 + source_bonus + hint_bonus + depth_bonus
    normalized = dict(item)
    normalized["_resolver_source"] = source
    return {
        "pair": _pair_from_item(normalized),
        "mint": mint,
        "source": f"{source}:{match_source}",
        "reason": reason,
        "candidate_count": 1,
        "score": round(score, 3),
        "match_rank": match_rank,
        "liquidity_usd": liq,
        "volume_24h": vol24,
        "market_cap_usd": mcap,
        "pair_address": str(item.get("pair_address") or item.get("token_pair_address") or "").strip() or None,
        "confidence": "HIGH" if score >= 145 else "MEDIUM" if score >= 112 else "LOW",
    }


def _dedupe_best(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_mint: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        mint = str(candidate.get("mint") or "")
        if not mint:
            continue
        current = by_mint.get(mint)
        if not current or float(candidate.get("score") or 0) > float(current.get("score") or 0):
            by_mint[mint] = candidate
    return sorted(by_mint.values(), key=lambda c: float(c.get("score") or 0), reverse=True)


def _candidate_public(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "mint": candidate.get("mint"),
        "source": candidate.get("source"),
        "score": candidate.get("score"),
        "confidence": candidate.get("confidence"),
        "market_cap_usd": candidate.get("market_cap_usd"),
        "liquidity_usd": candidate.get("liquidity_usd"),
        "volume_24h": candidate.get("volume_24h"),
        "pair_address": candidate.get("pair_address"),
    }


def _write_identity_event(
    symbol: str,
    *,
    status: str,
    reason: str | None,
    best: dict[str, Any] | None,
    candidates: list[dict[str, Any]],
    market_cap_hint: float | None,
) -> None:
    try:
        from utils.db import get_conn  # type: ignore

        now = datetime.now(timezone.utc).isoformat()
        public_candidates = [_candidate_public(c) for c in candidates[:8]]
        with get_conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS token_identity_resolutions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts_utc TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    status TEXT NOT NULL,
                    reason TEXT,
                    resolved_mint TEXT,
                    source TEXT,
                    confidence TEXT,
                    candidate_count INTEGER,
                    market_cap_hint REAL,
                    candidates_json TEXT
                )
                """
            )
            conn.execute(
                """
                INSERT INTO token_identity_resolutions (
                    ts_utc, symbol, status, reason, resolved_mint, source, confidence,
                    candidate_count, market_cap_hint, candidates_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    now,
                    norm_symbol(symbol) or str(symbol or "").upper(),
                    status,
                    reason,
                    (best or {}).get("mint"),
                    (best or {}).get("source"),
                    (best or {}).get("confidence"),
                    len(candidates),
                    market_cap_hint,
                    json.dumps(public_candidates, separators=(",", ":")),
                ),
            )
            conn.execute(
                "INSERT OR REPLACE INTO kv_store (key, value) VALUES (?, ?)",
                (
                    f"token_identity:last:{norm_symbol(symbol)}",
                    json.dumps(
                        {
                            "ts_utc": now,
                            "symbol": norm_symbol(symbol),
                            "status": status,
                            "reason": reason,
                            "resolved_mint": (best or {}).get("mint"),
                            "source": (best or {}).get("source"),
                            "confidence": (best or {}).get("confidence"),
                            "candidate_count": len(candidates),
                            "market_cap_hint": market_cap_hint,
                            "candidates": public_candidates,
                        },
                        separators=(",", ":"),
                    ),
                ),
            )
    except Exception:
        pass


def _identity_conflict(best: dict[str, Any], second: dict[str, Any], market_cap_hint: float | None) -> bool:
    if str(best.get("mint") or "") == str(second.get("mint") or ""):
        return False
    same_rank = int(best.get("match_rank") or 0) == int(second.get("match_rank") or 0)
    if not same_rank:
        return False
    best_score = float(best.get("score") or 0)
    second_score = float(second.get("score") or 0)
    if second_score < best_score * 0.92:
        return False
    if not market_cap_hint or market_cap_hint <= 0:
        return True
    best_mc = float(best.get("market_cap_usd") or 0)
    second_mc = float(second.get("market_cap_usd") or 0)
    if best_mc <= 0 or second_mc <= 0:
        return True
    best_gap = abs(best_mc - market_cap_hint) / max(market_cap_hint, 1.0)
    second_gap = abs(second_mc - market_cap_hint) / max(market_cap_hint, 1.0)
    return second_gap <= best_gap * 1.25


def _rows_from_market_cache(query_norm: str) -> list[dict[str, Any]]:
    try:
        from utils.db import get_conn  # type: ignore

        with get_conn() as conn:
            rows = conn.execute(
                """
                SELECT mint, symbol, snapshot_json, source, quality_score, updated_at
                FROM conviction_recovery_market_cache
                WHERE UPPER(symbol)=?
                ORDER BY updated_at DESC
                LIMIT 16
                """,
                (query_norm,),
            ).fetchall()
        out = []
        for row in rows:
            try:
                item = json.loads(row["snapshot_json"] or "{}")
            except Exception:
                item = {}
            item.setdefault("mint", row["mint"])
            item.setdefault("symbol", row["symbol"])
            item.setdefault("source", row["source"])
            item.setdefault("quality_score", row["quality_score"])
            out.append(item)
        return out
    except Exception:
        return []


def _rows_from_runner_radar(query_norm: str) -> list[dict[str, Any]]:
    try:
        from utils.db import get_conn  # type: ignore

        with get_conn() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM memecoin_runner_radar_observations
                WHERE UPPER(symbol)=?
                ORDER BY last_seen_utc DESC
                LIMIT 16
                """,
                (query_norm,),
            ).fetchall()
        return [dict(row) for row in rows]
    except Exception:
        return []


def _rows_from_scan_cache(query_norm: str) -> list[dict[str, Any]]:
    try:
        from utils.db import get_conn  # type: ignore

        payloads: list[Any] = []
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT value FROM kv_store WHERE key IN ('memecoin_scan_cache','memecoin_scan_cache_last_nonempty')"
            ).fetchall()
        for row in rows:
            try:
                value = json.loads(row["value"] or "null")
            except Exception:
                continue
            if isinstance(value, list):
                payloads.extend(value)
            elif isinstance(value, dict):
                signals = value.get("signals") or value.get("items") or value.get("data")
                if isinstance(signals, list):
                    payloads.extend(signals)
        return [x for x in payloads if isinstance(x, dict) and norm_symbol(x.get("symbol")) == query_norm]
    except Exception:
        return []


def _rows_from_geckoterminal(query_norm: str) -> list[dict[str, Any]]:
    try:
        from data.geckoterminal import fetch_geckoterminal_trending  # type: ignore

        rows = fetch_geckoterminal_trending(limit=80) or []
        return [x for x in rows if isinstance(x, dict) and norm_symbol(x.get("symbol")) == query_norm]
    except Exception:
        return []


def _candidates_from_dex(query: str, query_norm: str, market_cap_hint: float | None) -> list[dict[str, Any]]:
    try:
        from data.dexscreener import fetch_search_pairs  # type: ignore

        pairs = fetch_search_pairs(query, reason="resolver_search_429")
    except Exception:
        pairs = []
    out: list[dict[str, Any]] = []
    for pair in pairs:
        if str(pair.get("chainId") or "").strip().lower() != "solana":
            continue
        base = pair.get("baseToken") or {}
        item = {
            "symbol": base.get("symbol"),
            "name": base.get("name"),
            "mint": base.get("address"),
            "pair_address": pair.get("pairAddress"),
            "liquidity": (pair.get("liquidity") or {}).get("usd"),
            "volume_24h": (pair.get("volume") or {}).get("h24"),
            "volume_6h": (pair.get("volume") or {}).get("h6"),
            "volume_1h": (pair.get("volume") or {}).get("h1"),
            "change_24h": (pair.get("priceChange") or {}).get("h24"),
            "change_6h": (pair.get("priceChange") or {}).get("h6"),
            "change_1h": (pair.get("priceChange") or {}).get("h1"),
            "market_cap": pair.get("marketCap") or pair.get("fdv"),
            "fdv": pair.get("fdv"),
            "price": pair.get("priceUsd"),
            "txns_h1": _f(((pair.get("txns") or {}).get("h1") or {}).get("buys")) + _f(((pair.get("txns") or {}).get("h1") or {}).get("sells")),
            "txns_h24": _f(((pair.get("txns") or {}).get("h24") or {}).get("buys")) + _f(((pair.get("txns") or {}).get("h24") or {}).get("sells")),
        }
        candidate = _candidate_from_item(
            item,
            query_norm=query_norm,
            source="dexscreener_search",
            reason="dex_search",
            market_cap_hint=market_cap_hint,
        )
        if candidate:
            candidate["pair"] = pair
            out.append(candidate)
    return out


def resolve_solana_token(symbol: str, *, market_cap_hint: float | None = None) -> dict[str, Any]:
    query = str(symbol or "").strip()
    query_norm = norm_symbol(query)
    if not query_norm:
        return {"pair": None, "mint": "", "source": None, "reason": "empty_symbol", "candidate_count": 0}

    candidates: list[dict[str, Any]] = []
    local_sources = [
        ("conviction_market_cache", "market_cache", _rows_from_market_cache),
        ("runner_radar", "runner_radar", _rows_from_runner_radar),
        ("memecoin_scan_cache", "scan_cache", _rows_from_scan_cache),
    ]
    for source, reason, loader in local_sources:
        rows = loader(query_norm)
        for row in rows:
            candidate = _candidate_from_item(
                row,
                query_norm=query_norm,
                source=source,
                reason=reason,
                market_cap_hint=market_cap_hint,
            )
            if candidate:
                candidates.append(candidate)

    strong_local = any(float(c.get("score") or 0) >= 145 for c in candidates)
    if not strong_local:
        for row in _rows_from_geckoterminal(query_norm):
            candidate = _candidate_from_item(
                row,
                query_norm=query_norm,
                source="geckoterminal_trending",
                reason="geckoterminal",
                market_cap_hint=market_cap_hint,
            )
            if candidate:
                candidates.append(candidate)
        strong_local = any(float(c.get("score") or 0) >= 145 for c in candidates)

    # Only spend Dex after local/fallback evidence is exhausted or weak.
    if not strong_local:
        candidates.extend(_candidates_from_dex(query, query_norm, market_cap_hint))

    ranked = _dedupe_best(candidates)
    if not ranked:
        result = {"pair": None, "mint": "", "source": None, "reason": "no_matching_solana_pair", "candidate_count": 0}
        _write_identity_event(query_norm, status="UNRESOLVED", reason="no_matching_solana_pair", best=None, candidates=[], market_cap_hint=market_cap_hint)
        return result

    best = ranked[0]
    if len(ranked) > 1:
        second = ranked[1]
        if _identity_conflict(best, second, market_cap_hint):
            _write_identity_event(
                query_norm,
                status="QUARANTINED",
                reason="identity_conflict",
                best=best,
                candidates=ranked,
                market_cap_hint=market_cap_hint,
            )
            return {
                "pair": None,
                "mint": "",
                "source": "identity_guard",
                "reason": "identity_conflict_quarantined",
                "candidate_count": len(ranked),
                "confidence": "BLOCKED",
                "candidates": [_candidate_public(c) for c in ranked[:5]],
            }

    best["candidate_count"] = len(ranked)
    best["reason"] = None
    _write_identity_event(
        query_norm,
        status="RESOLVED",
        reason=None,
        best=best,
        candidates=ranked,
        market_cap_hint=market_cap_hint,
    )
    return best
