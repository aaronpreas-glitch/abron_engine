from __future__ import annotations

import json
import os
import subprocess
import time
from datetime import datetime, timezone, timedelta
from html import escape
from typing import Any

STALE_OK_SECONDS = max(30, int(os.getenv("CONVICTION_RECOVERY_STALE_OK_SECONDS", "180")))
CACHE_MAX_AGE_SECONDS = max(60, int(os.getenv("CONVICTION_RECOVERY_CACHE_MAX_AGE_SECONDS", "600")))
GOOD_BUY_MIN_SCORE = max(0, int(os.getenv("CONVICTION_RECOVERY_GOOD_BUY_MIN_SCORE", "78")))
CONFIRMATION_REQUIRED = max(1, int(os.getenv("CONVICTION_RECOVERY_CONFIRMATION_REQUIRED", "2")))
CONFIRMATION_WINDOW_SECONDS = max(60, int(os.getenv("CONVICTION_RECOVERY_CONFIRMATION_WINDOW_SECONDS", "900")))
CONFIRMATION_MIN_GAP_SECONDS = max(15, int(os.getenv("CONVICTION_RECOVERY_CONFIRMATION_MIN_GAP_SECONDS", "45")))
ALERT_COOLDOWN_SECONDS = max(60, int(os.getenv("CONVICTION_RECOVERY_ALERT_COOLDOWN_SECONDS", "1800")))
TELEGRAM_ALERTS_ENABLED = str(os.getenv("CONVICTION_RECOVERY_TELEGRAM_ALERTS", "false")).strip().lower() in {"1", "true", "yes", "on"}
AUTO_EXPANSION_ENABLED = str(os.getenv("CONVICTION_RECOVERY_AUTO_EXPANSION_ENABLED", "true")).strip().lower() in {"1", "true", "yes", "on"}
AUTO_EXPANSION_MIN_SCORE = max(0, int(os.getenv("CONVICTION_RECOVERY_AUTO_EXPANSION_MIN_SCORE", "72")))
RUNNER_HEARTBEAT_MIN_SCORE = max(0, float(os.getenv("RUNNER_HEARTBEAT_MIN_SCORE", "52")))
RUNNER_HEARTBEAT_MAX_SIGNALS = max(3, int(os.getenv("RUNNER_HEARTBEAT_MAX_SIGNALS", "30")))
RUNNER_HEARTBEAT_OUTCOME_MINUTES = max(5, int(os.getenv("RUNNER_HEARTBEAT_OUTCOME_MINUTES", "30")))
DIRECT_BATCH_URL = "https://api.dexscreener.com/latest/dex/tokens/{mints}"
MAX_DISCOVERY_TOKENS = max(5, int(os.getenv("CONVICTION_RECOVERY_MAX_DISCOVERY_TOKENS", "35")))
MIN_QUALITY_SCORE = max(0, int(os.getenv("CONVICTION_RECOVERY_MIN_QUALITY_SCORE", "62")))
LEGACY_QUERIES = [
    item.strip()
    for item in os.getenv(
        "CONVICTION_RECOVERY_SEARCH_QUERIES",
        "TROLL,WOJAK,JUP,USDUC,FARTCOIN,AURA,MAGA,WIF,BONK,POPCAT,MEW,PENGU,PNUT,GOAT,MOODENG,BOME,MYRO,PONKE,SLERF,SAMO,RAY,ORCA,JTO,PYTH",
    ).split(",")
    if item.strip()
]
SEARCH_URL = "https://api.dexscreener.com/latest/dex/search?q={query}"
EXCLUDED_EXPANSION_SYMBOLS = {"USDC", "USDT", "USDS", "USD1", "DAI", "FDUSD", "PYUSD", "SOL", "WSOL", "BTC", "ETH", "WETH", "WBTC"}


DEFAULT_CONVICTION_TOKENS: list[dict[str, Any]] = [
    {
        "symbol": "TROLL",
        "mint": "5UUH9RTDiSpq6HKS6bp4NdU9PNJpXRXuiw6ShBTBhgH2",
        "profile": "established_meme_recovery",
        "reference_mcap": 11_000_000,
        "known_peak_mcap": 113_000_000,
        "thesis": "Known meme that proved it can reprice violently after attention returns.",
    },
    {
        "symbol": "WOJAK",
        "mint": "8J69rbLTzWWgUJziFY8jeu5tDwEPBwUz4pKBMr5rpump",
        "profile": "dead_to_reload",
        "reference_mcap": 3_000_000,
        "known_peak_mcap": 11_000_000,
        "thesis": "Recovery candidate that can wake up from a dead-looking base.",
    },
    {
        "symbol": "JUP",
        "mint": "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN",
        "profile": "established_spot_beta",
        "reference_mcap": 500_000_000,
        "known_peak_mcap": 833_000_000,
        "thesis": "Core Solana infrastructure beta with safer swing potential than pure memes.",
    },
    {
        "symbol": "USDUC",
        "mint": "CB9dDufT3ZuQXqqSfa1c5kY935TEreyBw9XJXxHKpump",
        "profile": "violent_recovery_spike",
        "reference_mcap": 1_700_000,
        "known_peak_mcap": 27_000_000,
        "thesis": "Shows the exact spike-and-roundtrip behavior that needs fast exits.",
    },
    {
        "symbol": "FARTCOIN",
        "mint": "9BB6NFEcjBCtnNLFko2FqVQBq8HHM13kCyYcdQbgpump",
        "profile": "meme_leader_beta",
        "reference_mcap": 145_000_000,
        "known_peak_mcap": 256_000_000,
        "thesis": "Large meme leader with history and room to move when Solana risk-on expands.",
    },
    {
        "symbol": "AURA",
        "mint": "DtR4D9FtVoTX2569gaL837ZgrB6wNjj6tkmnX9Rdk9B2",
        "profile": "established_meme_recovery",
        "reference_mcap": None,
        "known_peak_mcap": None,
        "thesis": "Established runner watch name; buy only when the last live blocker clears.",
    },
    {
        "symbol": "MAGA",
        "mint": "Hon2rHAiqkcDtUzL5gA2vjXPr7T1MPCK2UT2AHKCpump",
        "profile": "established_meme_recovery",
        "reference_mcap": None,
        "known_peak_mcap": None,
        "thesis": "Established runner watch name; avoid blind entries and wait for renewed proof.",
    },
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_ts(ts_raw: object) -> datetime | None:
    raw = str(ts_raw or "").strip()
    if not raw:
        return None
    try:
        ts = datetime.fromisoformat(raw.replace("Z", "+00:00").replace(" ", "T"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts.astimezone(timezone.utc)
    except Exception:
        return None


def _age_seconds(ts_raw: object) -> float | None:
    ts = _parse_ts(ts_raw)
    if ts is None:
        return None
    return max(0.0, (datetime.now(timezone.utc) - ts).total_seconds())


def _f(value: object, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def _load_tokens() -> list[dict[str, Any]]:
    raw = os.getenv("CONVICTION_RECOVERY_TOKENS_JSON", "").strip()
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return [dict(item) for item in parsed if isinstance(item, dict) and item.get("mint")]
        except Exception:
            pass
    return [dict(item) for item in DEFAULT_CONVICTION_TOKENS]


def _seed_tokens() -> list[dict[str, Any]]:
    tokens = _load_tokens()
    try:
        from utils.spot_accumulator import BASKET  # type: ignore

        for row in BASKET:
            mint = str(row.get("mint") or "").strip()
            if not mint:
                continue
            tokens.append(
                {
                    "symbol": str(row.get("symbol") or "").upper(),
                    "mint": mint,
                    "profile": "established_spot_basket",
                    "reference_mcap": None,
                    "known_peak_mcap": None,
                    "thesis": f"Established Solana basket token: {row.get('name') or row.get('symbol')}.",
                }
            )
    except Exception:
        pass
    return tokens


def established_runner_profile_map() -> dict[str, dict[str, Any]]:
    """Return mint-keyed proven-runner metadata without making provider calls."""
    profiles: dict[str, dict[str, Any]] = {}
    for token in _seed_tokens():
        mint = str(token.get("mint") or "").strip()
        if not mint:
            continue
        profile = str(token.get("profile") or "").strip()
        profiles[mint] = {
            "symbol": str(token.get("symbol") or "").upper(),
            "mint": mint,
            "profile": profile,
            "is_established_runner": profile.startswith("established")
            or profile in {"dead_to_reload", "violent_recovery_spike", "meme_leader_beta"},
            "reference_mcap": _f(token.get("reference_mcap"), None),
            "known_peak_mcap": _f(token.get("known_peak_mcap"), None),
            "thesis": token.get("thesis"),
            "source": "seed_watchlist",
        }
    try:
        from utils.db import get_conn  # type: ignore

        with get_conn() as conn:
            _ensure_tables(conn)
            rows = conn.execute(
                """
                SELECT mint, symbol, profile, quality_score, verdict, entry_state,
                       exit_state, snapshot_ts_utc, snapshot_json
                FROM conviction_recovery_market_cache
                WHERE COALESCE(mint, '') != ''
                """
            ).fetchall()
        for row in rows:
            mint = str(row["mint"] or "").strip()
            if not mint:
                continue
            cached = dict(profiles.get(mint) or {})
            raw = {}
            try:
                raw = json.loads(row["snapshot_json"] or "{}")
                raw = raw if isinstance(raw, dict) else {}
            except Exception:
                raw = {}
            profile = str(row["profile"] or cached.get("profile") or "").strip()
            cached.update(
                {
                    "symbol": str(row["symbol"] or cached.get("symbol") or raw.get("symbol") or "").upper(),
                    "mint": mint,
                    "profile": profile,
                    "is_established_runner": profile.startswith("established")
                    or profile
                    in {"dead_to_reload", "violent_recovery_spike", "meme_leader_beta", "auto_watchlist_expansion"},
                    "quality_score": _f(row["quality_score"], None),
                    "verdict": row["verdict"],
                    "entry_state": row["entry_state"],
                    "exit_state": row["exit_state"],
                    "snapshot_ts_utc": row["snapshot_ts_utc"],
                    "reference_mcap": cached.get("reference_mcap") if cached.get("reference_mcap") is not None else _f(raw.get("reference_mcap"), None),
                    "known_peak_mcap": cached.get("known_peak_mcap") if cached.get("known_peak_mcap") is not None else _f(raw.get("known_peak_mcap"), None),
                    "thesis": cached.get("thesis") or raw.get("thesis"),
                    "source": "conviction_recovery_cache",
                }
            )
            profiles[mint] = cached
    except Exception:
        pass
    return profiles


def _merge_token_meta(seed_tokens: list[dict[str, Any]], discovered: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_mint: dict[str, dict[str, Any]] = {}
    for token in seed_tokens:
        mint = str(token.get("mint") or "").strip()
        if mint:
            by_mint[mint] = dict(token)
    for token in discovered:
        mint = str(token.get("address") or token.get("mint") or "").strip()
        if not mint or mint in by_mint:
            continue
        mcap = _f(token.get("market_cap") or token.get("fdv"), None)
        expansion_score = _f(token.get("expansion_score"))
        by_mint[mint] = {
            "symbol": str(token.get("symbol") or "UNKNOWN").upper(),
            "mint": mint,
            "profile": "auto_watchlist_expansion" if expansion_score else "discovered_established",
            "reference_mcap": mcap,
            "known_peak_mcap": mcap,
            "expansion_score": round(expansion_score, 1) if expansion_score else None,
            "expansion_source": token.get("source") or "discovery",
            "expansion_reasons": token.get("expansion_reasons") or [],
            "thesis": (
                f"Auto-expanded watchlist candidate: liquidity, volume, flow, and market-cap history fit the system."
                if expansion_score
                else "Discovered established token with liquidity, volume, and attention signs."
            ),
        }
    return list(by_mint.values())


def _spot_basket_fallback_snapshots() -> dict[str, dict[str, Any]]:
    try:
        from utils.spot_accumulator import BASKET, _fetch_basket_enriched  # type: ignore
    except Exception:
        return {}
    try:
        enriched = _fetch_basket_enriched() or {}
    except Exception:
        return {}
    result: dict[str, dict[str, Any]] = {}
    for token in BASKET:
        sym = str(token.get("symbol") or "").upper()
        mint = str(token.get("mint") or "").strip()
        data = enriched.get(sym) or {}
        price = _f(data.get("price"))
        if not mint or price <= 0:
            continue
        result[mint] = {
            "symbol": sym,
            "address": mint,
            "pair_address": None,
            "liquidity": 0.0,
            "volume_24h": 0.0,
            "price": price,
            "change_24h": _f(data.get("h24"), 0.0),
            "change_6h": _f(data.get("h6"), 0.0),
            "change_1h": 0.0,
            "market_cap": None,
            "fdv": None,
            "txns_h1": 0,
            "txns_h24": 0,
            "spot_price_fallback": True,
        }
    return result


def _ensure_tables(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS conviction_recovery_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT,
            mint TEXT NOT NULL,
            profile TEXT,
            ts_utc TEXT NOT NULL,
            mcap_usd REAL,
            liquidity_usd REAL,
            volume_24h REAL,
            change_1h REAL,
            change_6h REAL,
            change_24h REAL,
            vol_liq_ratio REAL,
            txns_h1 INTEGER,
            txns_h24 INTEGER,
            verdict TEXT,
            entry_state TEXT,
            exit_state TEXT,
            raw_json TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_conviction_recovery_mint_ts
        ON conviction_recovery_snapshots(mint, ts_utc DESC)
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS conviction_recovery_market_cache (
            mint TEXT PRIMARY KEY,
            symbol TEXT,
            profile TEXT,
            snapshot_json TEXT NOT NULL,
            snapshot_ts_utc TEXT NOT NULL,
            source TEXT,
            quality_score REAL,
            verdict TEXT,
            entry_state TEXT,
            exit_state TEXT,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS conviction_recovery_confirmations (
            mint TEXT PRIMARY KEY,
            symbol TEXT,
            first_triggered_at TEXT,
            last_triggered_at TEXT,
            last_counted_at TEXT,
            first_snapshot_as_of TEXT,
            last_snapshot_as_of TEXT,
            confirmation_count INTEGER NOT NULL DEFAULT 0,
            raw_money_state TEXT,
            confirmed_state TEXT,
            buy_trigger_score REAL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS conviction_recovery_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts_utc TEXT NOT NULL,
            symbol TEXT,
            mint TEXT NOT NULL,
            alert_type TEXT NOT NULL,
            money_state TEXT,
            buy_trigger_score REAL,
            exit_trigger_score REAL,
            data_freshness TEXT,
            message TEXT NOT NULL,
            sent_telegram INTEGER NOT NULL DEFAULT 0,
            raw_json TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_conviction_recovery_alerts_mint_ts
        ON conviction_recovery_alerts(mint, alert_type, ts_utc DESC)
        """
    )


def _cache_market_item(conn, item: dict[str, Any], *, source: str) -> None:
    mint = str(item.get("mint") or "").strip()
    if not mint:
        return
    conn.execute(
        """
        INSERT INTO conviction_recovery_market_cache (
            mint, symbol, profile, snapshot_json, snapshot_ts_utc, source,
            quality_score, verdict, entry_state, exit_state, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(mint) DO UPDATE SET
            symbol=excluded.symbol,
            profile=excluded.profile,
            snapshot_json=excluded.snapshot_json,
            snapshot_ts_utc=excluded.snapshot_ts_utc,
            source=excluded.source,
            quality_score=excluded.quality_score,
            verdict=excluded.verdict,
            entry_state=excluded.entry_state,
            exit_state=excluded.exit_state,
            updated_at=excluded.updated_at
        """,
        (
            mint,
            item.get("symbol"),
            item.get("profile"),
            json.dumps(item, separators=(",", ":")),
            item.get("snapshot_as_of") or item.get("updated_at") or _now_iso(),
            source,
            _f(item.get("quality_score"), None),
            item.get("verdict"),
            item.get("entry_state"),
            item.get("exit_state"),
            _now_iso(),
        ),
    )


def _cache_fallback_item(conn, mint: str, now_iso: str) -> dict[str, Any] | None:
    previous = conn.execute(
        """
        SELECT snapshot_json, snapshot_ts_utc, source
        FROM conviction_recovery_market_cache
        WHERE mint=? AND COALESCE(snapshot_json, '') != ''
        LIMIT 1
        """,
        (mint,),
    ).fetchone()
    if not previous or not previous["snapshot_json"]:
        return None
    age = _age_seconds(previous["snapshot_ts_utc"])
    if age is None or age > CACHE_MAX_AGE_SECONDS:
        return None
    try:
        item = json.loads(previous["snapshot_json"])
    except Exception:
        return None
    item["data_freshness"] = "CACHE_FALLBACK"
    item["snapshot_as_of"] = previous["snapshot_ts_utc"]
    item["updated_at"] = now_iso
    item["stale_age_seconds"] = round(age, 1)
    item["cache_source"] = previous["source"]
    item["provider_source"] = item.get("provider_source") or previous["source"]
    item["trigger"] = f"Live provider slow; using recent market cache. {item.get('trigger') or 'Wait for full refresh before entry.'}"
    return item


def _to_float(value: object, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def _normalize_dex_pair(pair: dict[str, Any]) -> dict[str, Any] | None:
    pair = pair or {}
    if str(pair.get("chainId") or "").lower() != "solana":
        return None
    base = pair.get("baseToken") or {}
    address = base.get("address")
    if not address:
        return None
    txns = pair.get("txns") or {}
    h1 = txns.get("h1") or {}
    h24 = txns.get("h24") or {}
    info = pair.get("info") or {}
    websites = info.get("websites") or []
    socials = info.get("socials") or []
    return {
        "symbol": base.get("symbol") or "UNKNOWN",
        "address": address,
        "pair_address": pair.get("pairAddress"),
        "pair_created_at": pair.get("pairCreatedAt"),
        "website_links": len(websites) if isinstance(websites, list) else 0,
        "social_links": len(socials) if isinstance(socials, list) else 0,
        "liquidity": _to_float((pair.get("liquidity") or {}).get("usd")),
        "volume_24h": _to_float((pair.get("volume") or {}).get("h24")),
        "price": _to_float(pair.get("priceUsd"), default=None),
        "change_24h": _to_float((pair.get("priceChange") or {}).get("h24")),
        "change_6h": _to_float((pair.get("priceChange") or {}).get("h6")),
        "change_1h": _to_float((pair.get("priceChange") or {}).get("h1")),
        "market_cap": _to_float(pair.get("marketCap"), default=None),
        "fdv": _to_float(pair.get("fdv"), default=None),
        "txns_h1": int(_to_float(h1.get("buys")) + _to_float(h1.get("sells"))),
        "txns_h24": int(_to_float(h24.get("buys")) + _to_float(h24.get("sells"))),
    }


def _fetch_direct_snapshots(mints: list[str]) -> dict[str, dict[str, Any]]:
    clean = [mint for mint in dict.fromkeys(str(m or "").strip() for m in mints) if mint]
    if not clean:
        return {}
    try:
        from utils.db import provider_in_cooldown  # type: ignore

        in_cooldown, _state = provider_in_cooldown("dexscreener")
        if in_cooldown:
            return {}
    except Exception:
        pass
    try:
        from data.dexscreener import fetch_token_pairs  # type: ignore

        pairs = fetch_token_pairs(clean, reason="conviction_batch_429")
    except Exception:
        pairs = []
    if not pairs:
        return {}
    by_mint: dict[str, list[dict[str, Any]]] = {}
    for item in (_normalize_dex_pair(pair) for pair in pairs):
        if not item:
            continue
        address = str(item.get("address") or "")
        by_mint.setdefault(address, []).append(item)
    result: dict[str, dict[str, Any]] = {}
    for mint, rows in by_mint.items():
        rows.sort(key=lambda row: (_f(row.get("liquidity")), _f(row.get("volume_24h"))), reverse=True)
        if rows:
            result[mint] = rows[0]
    return result


def _fetch_gecko_snapshots(wanted_mints: set[str]) -> dict[str, dict[str, Any]]:
    if not wanted_mints:
        return {}
    try:
        from data.geckoterminal import fetch_geckoterminal_trending  # type: ignore

        rows = fetch_geckoterminal_trending(limit=max(50, len(wanted_mints) * 4)) or []
    except Exception:
        return {}
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        mint = str(row.get("address") or "").strip()
        if mint not in wanted_mints:
            continue
        result[mint] = {
            **row,
            "source": "geckoterminal_trending",
        }
    return result


def _expansion_score(row: dict[str, Any]) -> tuple[float, list[str]]:
    symbol = str(row.get("symbol") or "").upper()
    if symbol in EXCLUDED_EXPANSION_SYMBOLS:
        return 0.0, ["excluded symbol"]
    mcap = _f(row.get("market_cap") or row.get("fdv"))
    liq = _f(row.get("liquidity"))
    vol = _f(row.get("volume_24h"))
    txns_h1 = int(_f(row.get("txns_h1")))
    txns_h24 = int(_f(row.get("txns_h24")))
    chg1h = _f(row.get("change_1h"))
    chg6h = _f(row.get("change_6h"))
    chg24h = _f(row.get("change_24h"))
    social_links = int(_f(row.get("social_links")))
    website_links = int(_f(row.get("website_links")))
    age_ms = _f(row.get("pair_created_at"), None)
    age_days = None
    if age_ms:
        age_days = max(0.0, (datetime.now(timezone.utc).timestamp() - (age_ms / 1000.0)) / 86400.0)
    vol_liq = vol / liq if liq > 0 else 0.0
    if mcap < 1_000_000 or liq < 150_000 or vol < 150_000:
        return 0.0, ["below expansion floor"]
    has_established_evidence = (
        mcap >= 10_000_000
        or (age_days is not None and age_days >= 14)
        or social_links > 0
        or website_links > 0
    )
    if not has_established_evidence:
        return 0.0, ["not enough established-token evidence"]
    if chg24h > 200 and (age_days is None or age_days < 14):
        return 0.0, ["fresh vertical runner, not expansion"]
    score = 0.0
    reasons: list[str] = []
    if 1_000_000 <= mcap <= 2_000_000_000:
        score += 16
        reasons.append("market cap in tradable range")
    if liq >= 150_000:
        score += 18
        reasons.append("liquidity floor")
    if liq >= 500_000:
        score += 6
    if vol >= 150_000:
        score += 14
        reasons.append("volume floor")
    if vol_liq >= 0.4:
        score += 14
        reasons.append("active volume/liquidity")
    if txns_h1 >= 80:
        score += 12
        reasons.append("1h flow")
    elif txns_h24 >= 1000:
        score += 8
        reasons.append("24h flow")
    if chg1h > -5 and chg6h > -12:
        score += 8
        reasons.append("not actively breaking down")
    if -20 <= chg24h <= 120:
        score += 8
        reasons.append("not absurdly extended")
    if social_links or website_links:
        score += 8
        reasons.append("community/infrastructure")
    if age_days is not None and age_days >= 14:
        score += 12
        reasons.append("survived beyond launch")
    source = str(row.get("source") or "")
    if "geckoterminal" in source:
        score += 4
        reasons.append("trending source")
    return max(0.0, min(100.0, score)), reasons[:6]


def _fetch_gecko_expansion_candidates(limit: int = 50) -> list[dict[str, Any]]:
    try:
        from data.geckoterminal import fetch_geckoterminal_trending  # type: ignore

        rows = fetch_geckoterminal_trending(limit=limit) or []
    except Exception:
        return []
    candidates: list[dict[str, Any]] = []
    for row in rows:
        score, reasons = _expansion_score(row)
        if score < AUTO_EXPANSION_MIN_SCORE:
            continue
        candidates.append(
            {
                **row,
                "mint": row.get("address"),
                "expansion_score": score,
                "expansion_reasons": reasons,
                "source": row.get("source") or "geckoterminal_trending",
            }
        )
    candidates.sort(key=lambda r: (_f(r.get("expansion_score")), _f(r.get("volume_24h")), _f(r.get("liquidity"))), reverse=True)
    return candidates[: max(1, limit)]


def _watchlist_expansion_candidates(search_candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not AUTO_EXPANSION_ENABLED:
        return []
    by_mint: dict[str, dict[str, Any]] = {}
    for row in [*search_candidates, *_fetch_gecko_expansion_candidates(limit=MAX_DISCOVERY_TOKENS)]:
        mint = str(row.get("address") or row.get("mint") or "").strip()
        if not mint:
            continue
        score, reasons = _expansion_score(row)
        score = max(score, _f(row.get("expansion_score")))
        if score < AUTO_EXPANSION_MIN_SCORE:
            continue
        candidate = {
            **row,
            "mint": mint,
            "expansion_score": score,
            "expansion_reasons": row.get("expansion_reasons") or reasons,
            "source": row.get("source") or "search_expansion",
        }
        existing = by_mint.get(mint)
        if not existing or (_f(candidate.get("expansion_score")), _f(candidate.get("volume_24h"))) > (_f(existing.get("expansion_score")), _f(existing.get("volume_24h"))):
            by_mint[mint] = candidate
    rows = list(by_mint.values())
    rows.sort(key=lambda r: (_f(r.get("expansion_score")), _f(r.get("volume_24h")), _f(r.get("liquidity"))), reverse=True)
    return rows[:MAX_DISCOVERY_TOKENS]


def _fetch_search_candidates(queries: list[str], per_query: int = 6, limit: int = 35) -> list[dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    for query in queries[:12]:
        try:
            from data.dexscreener import fetch_search_pairs  # type: ignore

            pairs = fetch_search_pairs(query, reason="conviction_search_429")
        except Exception:
            pairs = []
        for item in (_normalize_dex_pair(pair) for pair in pairs[:per_query]):
            if not item:
                continue
            mcap = _f(item.get("market_cap") or item.get("fdv"))
            if mcap < 1_000_000 or _f(item.get("liquidity")) < 75_000 or _f(item.get("volume_24h")) < 75_000:
                continue
            mint = str(item.get("address") or "")
            existing = found.get(mint)
            if not existing or (_f(item.get("liquidity")), _f(item.get("volume_24h"))) > (_f(existing.get("liquidity")), _f(existing.get("volume_24h"))):
                found[mint] = item
        if len(found) >= limit:
            break
    rows = list(found.values())
    rows.sort(key=lambda row: (_f(row.get("liquidity")), _f(row.get("volume_24h"))), reverse=True)
    return rows[:limit]


def _stale_fallback_item(conn, token: dict[str, Any], mint: str, now_iso: str) -> dict[str, Any] | None:
    previous = conn.execute(
        """
        SELECT raw_json, ts_utc
        FROM conviction_recovery_snapshots
        WHERE mint=? AND COALESCE(raw_json, '') != ''
        ORDER BY ts_utc DESC
        LIMIT 1
        """,
        (mint,),
    ).fetchone()
    if not previous or not previous["raw_json"]:
        return None
    try:
        cached_item = json.loads(previous["raw_json"])
    except Exception:
        return None
    stale_age = _age_seconds(previous["ts_utc"])
    cached_item["snapshot_as_of"] = previous["ts_utc"]
    cached_item["updated_at"] = now_iso
    cached_item["stale_age_seconds"] = round(stale_age, 1) if stale_age is not None else None
    if stale_age is not None and stale_age <= STALE_OK_SECONDS:
        cached_item["data_freshness"] = "RECENT_PROVIDER_FALLBACK"
        cached_item["trigger"] = f"Provider fallback; recent plan: {cached_item.get('trigger') or 'wait for fresh data'}"
        if cached_item.get("quality_score") is None:
            cached_item["quality_score"] = cached_item.get("survivability_score")
            cached_item["quality_floor_pass"] = bool(_f(cached_item.get("survivability_score")) >= MIN_QUALITY_SCORE)
        return cached_item
    cached_item["data_freshness"] = "STALE_REVIEW_ONLY"
    cached_item["last_verdict"] = cached_item.get("verdict")
    cached_item["last_entry_state"] = cached_item.get("entry_state")
    cached_item["verdict"] = "DATA_STALE"
    cached_item["entry_state"] = "WAIT"
    cached_item["exit_state"] = "UNKNOWN"
    cached_item["trigger"] = "Market snapshot is stale; do not use this as an entry signal until data refreshes."
    cached_item["invalidation"] = "Fresh provider data required."
    cached_item["take_profit"] = "No current TP guidance from stale data."
    cached_item["reasons"] = ["provider stale", *(cached_item.get("reasons") or [])][:7]
    return cached_item


def _coin_memory(conn, mint: str, item: dict[str, Any]) -> dict[str, Any]:
    rows = conn.execute(
        """
        SELECT mcap_usd, change_24h, ts_utc
        FROM conviction_recovery_snapshots
        WHERE mint=? AND COALESCE(mcap_usd, 0) > 0
        ORDER BY ts_utc ASC
        LIMIT 750
        """,
        (mint,),
    ).fetchall()
    points: list[tuple[float, float, str]] = [
        (_f(row["mcap_usd"]), _f(row["change_24h"]), str(row["ts_utc"] or ""))
        for row in rows
        if _f(row["mcap_usd"]) > 0
    ]
    current_mcap = _f(item.get("mcap_usd"))
    if current_mcap > 0:
        points.append((current_mcap, _f(item.get("change_24h")), str(item.get("snapshot_as_of") or item.get("updated_at") or "")))

    known_peak = _f(item.get("known_peak_mcap"))
    ref = _f(item.get("reference_mcap"))
    mcaps = [mcap for mcap, _, _ in points if mcap > 0]
    if ref > 0:
        mcaps.append(ref)
    if known_peak > 0:
        mcaps.append(known_peak)
    if not mcaps:
        return {
            "memory_snapshot_count": len(points),
            "memory_note": "No market-cap memory yet; wait for the lane to build history.",
        }

    low = min(mcaps)
    high = max(mcaps)
    multiple_from_low = current_mcap / low if current_mcap > 0 and low > 0 else None
    drawdown = ((current_mcap - high) / high * 100.0) if current_mcap > 0 and high > 0 else None
    spike_rows = [
        (ts, mcap)
        for mcap, change_24h, ts in points
        if change_24h >= 50.0 or (low > 0 and mcap >= low * 2.0)
    ]
    last_spike_ts = spike_rows[-1][0] if spike_rows else None

    if multiple_from_low is not None and multiple_from_low >= 5 and (drawdown is None or drawdown > -30):
        note = "Far above the observed base; this is protect-profit territory unless a fresh consolidation forms."
    elif drawdown is not None and drawdown <= -55 and multiple_from_low is not None and multiple_from_low >= 1.2:
        note = "Deep reset from prior high; good reload candidate only when volume turns back on."
    elif len(points) < 4:
        note = "Memory is forming; treat the range as useful but not statistically strong yet."
    else:
        note = "Tracked range is established; use low/high memory to avoid chasing late spikes."

    return {
        "memory_snapshot_count": len(points),
        "observed_low_mcap": round(low, 2),
        "observed_high_mcap": round(high, 2),
        "multiple_from_observed_low": round(multiple_from_low, 2) if multiple_from_low is not None else None,
        "drawdown_from_observed_high_pct": round(drawdown, 1) if drawdown is not None else None,
        "spike_count": len(spike_rows),
        "last_spike_ts": last_spike_ts,
        "memory_note": note,
    }


def _runner_replay_for(conn, mint: str, item: dict[str, Any]) -> dict[str, Any]:
    try:
        row = conn.execute(
            """
            SELECT first_seen_utc, last_seen_utc, radar_score, entry_verdict,
                   entry_setup, replay_state, max_return_pct, missed_reason,
                   risk_flags_json, why_json
            FROM memecoin_runner_radar_observations
            WHERE mint=?
            ORDER BY first_seen_utc DESC
            LIMIT 1
            """,
            (mint,),
        ).fetchone()
    except Exception:
        row = None

    from_low = _f(item.get("multiple_from_observed_low"))
    spike_count = int(_f(item.get("spike_count")))
    state = "TRACKING"
    note = "No runner replay issue detected yet."
    blocker = None
    payload: dict[str, Any] = {
        "runner_replay_state": state,
        "runner_replay_note": note,
    }
    if row:
        rec = dict(row)
        risk_flags: list[str] = []
        try:
            parsed = json.loads(rec.get("risk_flags_json") or "[]")
            risk_flags = [str(x) for x in parsed if x]
        except Exception:
            risk_flags = []
        max_return = _f(rec.get("max_return_pct"), None)
        replay_state = str(rec.get("replay_state") or "TRACKING").upper()
        missed_reason = str(rec.get("missed_reason") or "").strip()
        entry_verdict = str(rec.get("entry_verdict") or "").strip()
        if replay_state == "MISSED_RUNNER":
            state = "MISSED_RUNNER"
            blocker = missed_reason or ", ".join(risk_flags) or "blocked"
            note = f"Runner replay says this moved up to {round(max_return or 0, 1)}% after being blocked by {blocker}."
        elif max_return is not None and max_return >= 50:
            state = "TRACKED_WINNER"
            note = f"Replay tracked a winner: max observed return {round(max_return, 1)}% from first radar sighting."
        elif entry_verdict:
            state = "TRACKED"
            note = f"Replay has this on record as {entry_verdict.replace('_', ' ').lower()}."
        payload.update(
            {
                "runner_replay_state": state,
                "runner_replay_note": note,
                "runner_replay_blocker": blocker,
                "runner_first_seen_at": rec.get("first_seen_utc"),
                "runner_last_seen_at": rec.get("last_seen_utc"),
                "runner_max_return_pct": max_return,
                "runner_entry_verdict": entry_verdict or None,
                "runner_entry_setup": rec.get("entry_setup"),
                "runner_radar_score": _f(rec.get("radar_score"), None),
                "runner_risk_flags": risk_flags[:5],
            }
        )
        return payload

    if spike_count > 0 and from_low >= 2.0:
        return {
            "runner_replay_state": "UNATTRIBUTED_SPIKE",
            "runner_replay_note": "Coin memory shows a spike, but runner radar does not have an early sighting yet. Replay should learn why coverage missed it.",
            "runner_replay_blocker": "no_radar_sighting",
        }
    return payload


def _missed_runner_replay_summary(conn, limit: int = 5) -> dict[str, Any]:
    try:
        from utils.early_runner_radar import update_runner_replay  # type: ignore

        update_runner_replay()
    except Exception:
        pass
    try:
        rows = conn.execute(
            """
            SELECT symbol, mint, first_seen_utc, replay_state, max_return_pct,
                   missed_reason, risk_flags_json, entry_verdict
            FROM memecoin_runner_radar_observations
            WHERE first_seen_utc >= datetime('now', '-14 days')
              AND (replay_state='MISSED_RUNNER' OR COALESCE(max_return_pct, 0) >= 50)
            ORDER BY COALESCE(max_return_pct, 0) DESC
            LIMIT ?
            """,
            (max(1, min(int(limit), 20)),),
        ).fetchall()
    except Exception:
        return {"total": 0, "missed": 0, "tracked_winners": 0, "top": [], "blockers": {}}
    top: list[dict[str, Any]] = []
    blockers: dict[str, int] = {}
    for row in rows:
        rec = dict(row)
        reason = str(rec.get("missed_reason") or "").strip()
        if reason:
            for part in [p.strip() for p in reason.split(",") if p.strip()]:
                blockers[part] = blockers.get(part, 0) + 1
        top.append(
            {
                "symbol": rec.get("symbol"),
                "mint": rec.get("mint"),
                "first_seen_utc": rec.get("first_seen_utc"),
                "replay_state": rec.get("replay_state"),
                "max_return_pct": rec.get("max_return_pct"),
                "missed_reason": reason or None,
                "entry_verdict": rec.get("entry_verdict"),
            }
        )
    return {
        "total": len(top),
        "missed": sum(1 for row in top if row.get("replay_state") == "MISSED_RUNNER"),
        "tracked_winners": sum(1 for row in top if row.get("replay_state") != "MISSED_RUNNER"),
        "top": top,
        "blockers": blockers,
    }


def _money_mode(item: dict[str, Any]) -> dict[str, Any]:
    freshness = str(item.get("data_freshness") or "").upper()
    verdict = str(item.get("verdict") or "").upper()
    profile = str(item.get("profile") or "")
    quality = _f(item.get("quality_score"))
    liq = _f(item.get("liquidity_usd"))
    vol = _f(item.get("volume_24h"))
    vol_liq = _f(item.get("vol_liq_ratio"))
    chg1h = _f(item.get("change_1h"))
    chg6h = _f(item.get("change_6h"))
    chg24h = _f(item.get("change_24h"))
    txns_h1 = int(_f(item.get("txns_h1")))
    from_ref = _f(item.get("from_reference_multiple"))
    from_low = _f(item.get("multiple_from_observed_low"))
    drawdown_high = _f(item.get("drawdown_from_observed_high_pct"), None)
    quality_pass = bool(item.get("quality_floor_pass"))
    fresh_enough = freshness in {"FRESH", "CACHE_FALLBACK", "RECENT_PROVIDER_FALLBACK"}
    full_depth = freshness != "SPOT_PRICE_FALLBACK"

    buy_score = 0
    reasons: list[str] = []
    if quality_pass:
        buy_score += min(28, int(quality * 0.28))
        reasons.append("quality floor passed")
    if liq >= 250_000:
        buy_score += 12
        reasons.append("liquidity deep enough")
    if vol >= 250_000 and vol_liq >= 0.25:
        buy_score += 14
        reasons.append("volume is alive")
    if txns_h1 >= 80:
        buy_score += 12
        reasons.append("1h flow active")
    if chg1h > 0 and chg6h >= 0:
        buy_score += 10
        reasons.append("short-term momentum positive")
    if verdict == "SCOUT_READY":
        buy_score += 18
        reasons.append("reload breakout classified")
    elif verdict == "WATCH_CLOSE":
        buy_score += 10
        reasons.append("wakeup forming")
    elif verdict == "ACCUMULATION_ZONE" and chg6h > 0:
        buy_score += 8
        reasons.append("base is starting to turn")
    if profile in {"established_spot_beta", "meme_leader_beta", "established_spot_basket"}:
        buy_score += 6
        reasons.append("established leader profile")
    if drawdown_high is not None and -65 <= drawdown_high <= -20:
        buy_score += 8
        reasons.append("reset from prior high gives room")
    if chg24h >= 80 or from_ref >= 5 or from_low >= 5:
        buy_score -= 22
        reasons.append("extension risk")
    if chg1h <= -3:
        buy_score -= 14
        reasons.append("1h selling pressure")
    if not full_depth:
        buy_score = min(buy_score, 35)
        reasons.append("market-depth confirmation missing")
    if not fresh_enough:
        buy_score = min(buy_score, 20)
        reasons.append("fresh data required")

    buy_score = max(0, min(100, buy_score))
    good_buy = (
        fresh_enough
        and full_depth
        and quality_pass
        and buy_score >= GOOD_BUY_MIN_SCORE
        and verdict in {"SCOUT_READY", "WATCH_CLOSE", "ACCUMULATION_ZONE"}
        and chg24h < 80
        and from_ref < 5
        and from_low < 5
    )

    exit_score = 0
    exit_reasons: list[str] = []
    if verdict in {"SELL_INTO_STRENGTH", "EXIT_WATCH"}:
        exit_score += 35
        exit_reasons.append("classifier sees exit risk")
    if chg24h >= 80:
        exit_score += 25
        exit_reasons.append("24h spike is extended")
    elif chg24h >= 35:
        exit_score += 12
        exit_reasons.append("large 24h move")
    if chg1h <= -3 and chg24h >= 20:
        exit_score += 18
        exit_reasons.append("momentum fading after move")
    if from_ref >= 5 or from_low >= 5:
        exit_score += 18
        exit_reasons.append("far above remembered base")
    if drawdown_high is not None and drawdown_high <= -35 and chg24h < 0:
        exit_score += 10
        exit_reasons.append("round-trip risk active")
    exit_score = max(0, min(100, exit_score))

    if freshness in {"NO_DATA", "STALE_REVIEW_ONLY"}:
        money_state = "DATA_WAIT"
        entry_plan = "Do not act until fresh market data returns."
    elif not full_depth:
        money_state = "DATA_WAIT"
        entry_plan = "Price-only fallback; wait for liquidity, volume, and transaction confirmation."
    elif exit_score >= 45:
        money_state = "PROTECT_PROFIT"
        entry_plan = "No new entry; reduce or trail if already holding."
    elif good_buy:
        money_state = "BUYABLE_NOW"
        entry_plan = "Manual route/liquidity check, then scale in with predefined invalidation."
    elif quality_pass and verdict in {"WATCH_CLOSE", "ACCUMULATION_ZONE", "SCOUT_READY", "WATCH_RELOAD"}:
        money_state = "WAITING_TRIGGER"
        entry_plan = "Keep on screen; buy only after the trigger conditions confirm."
    else:
        money_state = "NO_TRADE"
        entry_plan = "Skip until quality and momentum improve."

    if exit_score >= 65:
        exit_lock = "TAKE_PARTIALS"
    elif exit_score >= 45:
        exit_lock = "PROTECT_PROFIT"
    elif exit_score >= 25:
        exit_lock = "TRAIL"
    else:
        exit_lock = "NONE"

    return {
        "money_state": money_state,
        "raw_money_state": money_state,
        "good_buy": good_buy,
        "buy_trigger_score": buy_score,
        "buy_trigger_reasons": reasons[:7],
        "entry_plan": entry_plan,
        "exit_lock": exit_lock,
        "exit_trigger_score": exit_score,
        "exit_trigger_reasons": exit_reasons[:5],
    }


def _update_confirmation(conn, item: dict[str, Any], now_iso: str) -> dict[str, Any]:
    mint = str(item.get("mint") or "").strip()
    if not mint:
        return item
    raw_state = str(item.get("money_state") or "").upper()
    snapshot_as_of = str(item.get("snapshot_as_of") or now_iso)
    item["raw_money_state"] = raw_state
    item["confirmation_required_count"] = CONFIRMATION_REQUIRED

    if raw_state != "BUYABLE_NOW":
        item["confirmation_state"] = "NOT_REQUIRED" if raw_state == "PROTECT_PROFIT" else "IDLE"
        item["confirmation_count"] = 0
        conn.execute(
            """
            INSERT INTO conviction_recovery_confirmations (
                mint, symbol, first_triggered_at, last_triggered_at, last_counted_at,
                first_snapshot_as_of, last_snapshot_as_of, confirmation_count,
                raw_money_state, confirmed_state, buy_trigger_score, updated_at
            )
            VALUES (?, ?, NULL, NULL, NULL, NULL, NULL, 0, ?, ?, ?, ?)
            ON CONFLICT(mint) DO UPDATE SET
                symbol=excluded.symbol,
                raw_money_state=excluded.raw_money_state,
                confirmed_state=excluded.confirmed_state,
                buy_trigger_score=excluded.buy_trigger_score,
                updated_at=excluded.updated_at
            """,
            (
                mint,
                item.get("symbol"),
                raw_state,
                raw_state,
                _f(item.get("buy_trigger_score")),
                now_iso,
            ),
        )
        return item

    previous = conn.execute(
        """
        SELECT *
        FROM conviction_recovery_confirmations
        WHERE mint=?
        LIMIT 1
        """,
        (mint,),
    ).fetchone()
    count = 1
    first_triggered_at = now_iso
    first_snapshot_as_of = snapshot_as_of
    last_counted_at = now_iso
    if previous and str(previous["raw_money_state"] or "").upper() == "BUYABLE_NOW":
        first_age = _age_seconds(previous["first_triggered_at"])
        previous_snapshot = str(previous["last_snapshot_as_of"] or "")
        counted_age = _age_seconds(previous["last_counted_at"])
        still_in_window = first_age is not None and first_age <= CONFIRMATION_WINDOW_SECONDS
        distinct_snapshot = bool(snapshot_as_of and snapshot_as_of != previous_snapshot)
        enough_gap = counted_age is None or counted_age >= CONFIRMATION_MIN_GAP_SECONDS
        count = int(previous["confirmation_count"] or 0)
        first_triggered_at = previous["first_triggered_at"] or now_iso
        first_snapshot_as_of = previous["first_snapshot_as_of"] or snapshot_as_of
        last_counted_at = previous["last_counted_at"] or now_iso
        if not still_in_window:
            count = 1
            first_triggered_at = now_iso
            first_snapshot_as_of = snapshot_as_of
            last_counted_at = now_iso
        elif distinct_snapshot and enough_gap:
            count = max(1, count) + 1
            last_counted_at = now_iso

    confirmed = count >= CONFIRMATION_REQUIRED
    item["confirmation_count"] = count
    item["confirmation_state"] = "CONFIRMED" if confirmed else "PENDING_CONFIRMATION"
    item["confirmation_first_seen_at"] = first_triggered_at
    item["confirmation_last_seen_at"] = now_iso
    if not confirmed:
        needed = max(0, CONFIRMATION_REQUIRED - count)
        item["money_state"] = "WAITING_CONFIRMATION"
        item["good_buy"] = False
        item["entry_plan"] = (
            f"First buy trigger is present; waiting for {needed} more distinct fresh confirmation"
            f"{'' if needed == 1 else 's'} before calling this buyable."
        )
        item["buy_trigger_reasons"] = ["confirmation pending", *(item.get("buy_trigger_reasons") or [])][:7]

    conn.execute(
        """
        INSERT INTO conviction_recovery_confirmations (
            mint, symbol, first_triggered_at, last_triggered_at, last_counted_at,
            first_snapshot_as_of, last_snapshot_as_of, confirmation_count,
            raw_money_state, confirmed_state, buy_trigger_score, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(mint) DO UPDATE SET
            symbol=excluded.symbol,
            first_triggered_at=excluded.first_triggered_at,
            last_triggered_at=excluded.last_triggered_at,
            last_counted_at=excluded.last_counted_at,
            first_snapshot_as_of=excluded.first_snapshot_as_of,
            last_snapshot_as_of=excluded.last_snapshot_as_of,
            confirmation_count=excluded.confirmation_count,
            raw_money_state=excluded.raw_money_state,
            confirmed_state=excluded.confirmed_state,
            buy_trigger_score=excluded.buy_trigger_score,
            updated_at=excluded.updated_at
        """,
        (
            mint,
            item.get("symbol"),
            first_triggered_at,
            now_iso,
            last_counted_at,
            first_snapshot_as_of,
            snapshot_as_of,
            count,
            raw_state,
            item.get("money_state"),
            _f(item.get("buy_trigger_score")),
            now_iso,
        ),
    )
    return item


def _classify(token: dict[str, Any], snap: dict[str, Any]) -> dict[str, Any]:
    mcap = _f(snap.get("market_cap") or snap.get("fdv"))
    liq = _f(snap.get("liquidity"))
    vol = _f(snap.get("volume_24h"))
    chg1h = _f(snap.get("change_1h"))
    chg6h = _f(snap.get("change_6h"))
    chg24h = _f(snap.get("change_24h"))
    txns_h1 = int(_f(snap.get("txns_h1")))
    txns_h24 = int(_f(snap.get("txns_h24")))
    ref = max(1.0, _f(token.get("reference_mcap"), mcap or 1.0))
    known_peak = max(ref, _f(token.get("known_peak_mcap"), ref))
    vol_liq = vol / liq if liq > 0 else 0.0
    from_ref = mcap / ref if ref > 0 and mcap > 0 else 0.0
    drawdown_from_known_peak_pct = round((mcap - known_peak) / known_peak * 100.0, 1) if known_peak > 0 and mcap > 0 else None
    profile = str(token.get("profile") or "")
    spot_fallback = bool(snap.get("spot_price_fallback"))
    social_links = int(_f(snap.get("social_links")))
    website_links = int(_f(snap.get("website_links")))
    age_ms = _f(snap.get("pair_created_at"), None)
    age_days = None
    if age_ms:
        age_days = max(0.0, (datetime.now(timezone.utc).timestamp() - (age_ms / 1000.0)) / 86400.0)

    survivability = 0
    quality_score = 0
    reasons: list[str] = []
    if liq >= 150_000:
        survivability += 20
        quality_score += 16
        reasons.append("surviving liquidity")
    if liq >= 500_000:
        quality_score += 8
    if vol_liq >= 0.75:
        survivability += 20
        quality_score += 18
        reasons.append("active volume/liquidity")
    elif vol_liq >= 0.25:
        survivability += 12
        quality_score += 10
        reasons.append("volume is present")
    if txns_h1 >= 100:
        survivability += 18
        quality_score += 14
        reasons.append("active 1h flow")
    elif txns_h24 >= 1000:
        survivability += 10
        quality_score += 8
        reasons.append("active 24h flow")
    if mcap >= ref * 0.8:
        survivability += 14
        quality_score += 10
        reasons.append("above your interest zone")
    if profile in {"established_spot_beta", "meme_leader_beta", "established_spot_basket"}:
        survivability += 12
        quality_score += 14
        reasons.append("leader/spot profile")
    if profile in {"established_meme_recovery", "dead_to_reload", "violent_recovery_spike"}:
        quality_score += 10
        reasons.append("known recovery profile")
    if social_links >= 1 or website_links >= 1:
        quality_score += 8
        reasons.append("community/infrastructure present")
    if age_days is not None and age_days >= 14:
        quality_score += 8
        reasons.append("survived beyond launch")
    if mcap >= 2_000_000:
        quality_score += 8
    if chg1h > 0 or chg6h > 0 or chg24h > 0:
        quality_score += 8
    if chg1h <= -8:
        quality_score -= 8
        reasons.append("short-term selling pressure")
    if liq < 100_000:
        quality_score -= 12
        reasons.append("liquidity below quality floor")
    survivability = min(100, survivability)
    quality_score = max(0, min(100, quality_score))

    exit_state = "HOLD_MONITOR"
    entry_state = "WAIT"
    verdict = "WATCH_RELOAD"
    trigger = "Needs renewed 1h/6h acceleration before a fresh entry."
    invalidation = "Ignore if liquidity fades or 24h volume dries up."
    take_profit = "No active TP until entry state improves."

    extreme_spike = chg24h >= 120 or from_ref >= 8.0
    active_breakout = chg1h >= 2.0 and chg6h >= 4.0 and vol_liq >= 0.4 and txns_h1 >= 80
    early_wake = chg1h >= 1.0 and chg24h >= 8.0 and vol_liq >= 0.25
    base_reload = abs(chg24h) <= 8.0 and vol_liq >= 0.2 and liq >= 150_000
    fading_after_move = chg1h <= -3.0 and (chg24h >= 25.0 or from_ref >= 2.5)

    spot_fallback_pass = spot_fallback and profile == "established_spot_basket" and _f(snap.get("price")) > 0
    quality_floor_pass = quality_score >= MIN_QUALITY_SCORE and liq >= 100_000 and vol >= 100_000 and mcap >= 1_000_000
    display_allowed = quality_floor_pass or spot_fallback_pass

    if not display_allowed:
        verdict = "QUALITY_REJECT"
        entry_state = "NO_TRADE"
        exit_state = "IGNORE"
        trigger = "Rejected: not enough quality, liquidity, volume, history, or community signal for a good-buy surface."
        invalidation = "Needs stronger quality score and renewed activity before it can appear as a buy candidate."
        take_profit = "No TP plan because this is not a good-buy candidate."
    elif spot_fallback_pass:
        verdict = "SPOT_PRICE_ONLY"
        entry_state = "WAIT_CONFIRMATION"
        exit_state = "HOLD_MONITOR"
        trigger = "Spot fallback has price/trend only; wait for full liquidity/volume confirmation before treating as a buy."
        invalidation = "Needs fresh market-depth data from primary DEX source."
        take_profit = "Use existing spot risk rules until full market-depth data returns."
        reasons.append("spot price fallback")
    elif extreme_spike:
        verdict = "SELL_INTO_STRENGTH"
        entry_state = "NO_CHASE"
        exit_state = "PROTECT_PROFIT"
        trigger = "Do not chase; wait for a deep reset or clean consolidation."
        invalidation = "If already in, do not let a vertical spike round-trip below the breakout base."
        take_profit = "Scale hard into strength; protect principal and trail only a small runner."
        reasons.append("spike extension")
    elif fading_after_move:
        verdict = "EXIT_WATCH"
        entry_state = "WAIT_PULLBACK"
        exit_state = "DISTRIBUTION_RISK"
        trigger = "Wait for sellers to finish and for 1h momentum to reclaim positive."
        invalidation = "Avoid if lower highs continue or liquidity rotates out."
        take_profit = "If already holding, reduce exposure before the move round-trips."
        reasons.append("fading after move")
    elif active_breakout:
        verdict = "SCOUT_READY"
        entry_state = "MOMENTUM_RELOAD"
        exit_state = "TRAIL_FAST"
        trigger = "Manual scout after route/liquidity check; this is the early acceleration window."
        invalidation = "Cut or skip if 1h flips negative or volume/liquidity falls under 0.25x."
        take_profit = "Take partials into +20-40%; trail only if 6h strength expands."
        reasons.append("active reload breakout")
    elif early_wake:
        verdict = "WATCH_CLOSE"
        entry_state = "WAKEUP_FORMING"
        exit_state = "PLAN_EXIT_BEFORE_SPIKE"
        trigger = "Needs one more 1h acceleration or volume expansion before scout."
        invalidation = "Drop if the wakeup candle fades without follow-through."
        take_profit = "Pre-plan partials; these examples can round-trip fast after the spike."
        reasons.append("wakeup forming")
    elif base_reload:
        verdict = "ACCUMULATION_ZONE"
        entry_state = "BASE_MONITOR"
        exit_state = "NO_EXIT_SIGNAL"
        trigger = "Good candidate for patient watch; buy only when momentum reappears from the base."
        invalidation = "Invalid if volume stays dead or market cap loses the base area."
        take_profit = "Use staged exits once repricing starts; do not wait for perfect tops."
        reasons.append("base reload candidate")

    return {
        "verdict": verdict,
        "entry_state": entry_state,
        "exit_state": exit_state,
        "trigger": trigger,
        "invalidation": invalidation,
        "take_profit": take_profit,
        "survivability_score": survivability,
        "quality_score": quality_score,
        "quality_floor_pass": quality_floor_pass,
        "display_allowed": display_allowed,
        "reasons": reasons[:7],
        "from_reference_multiple": round(from_ref, 2) if from_ref else None,
        "drawdown_from_known_peak_pct": drawdown_from_known_peak_pct,
        "age_days": round(age_days, 1) if age_days is not None else None,
    }


def _send_telegram_alert(message: str) -> bool:
    if not TELEGRAM_ALERTS_ENABLED:
        return False
    if str(os.getenv("DRY_RUN", "false")).strip().lower() in {"1", "true", "yes", "on"}:
        return False
    token = os.getenv("TELEGRAM_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        return False
    try:
        import requests  # type: ignore

        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": message,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=10,
        )
        return 200 <= response.status_code < 300
    except Exception:
        return False


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, float(value)))


def _runner_buy_pressure(item: dict[str, Any]) -> float:
    chg1h = _f(item.get("change_1h"))
    txns_h1 = int(_f(item.get("txns_h1")))
    txns_h24 = int(_f(item.get("txns_h24")))
    pressure = 50.0 + max(-18.0, min(18.0, chg1h * 2.0))
    if txns_h24 > 0:
        activity_share = max(0.0, min(1.0, txns_h1 / max(txns_h24, 1)))
        pressure += max(-8.0, min(8.0, (activity_share - 0.04) * 100.0))
    if txns_h1 <= 2:
        pressure = (pressure + 50.0) / 2.0
    return round(_clamp(pressure, 5.0, 95.0), 1)


def _runner_vol_acceleration(item: dict[str, Any]) -> float:
    txns_h1 = int(_f(item.get("txns_h1")))
    txns_h24 = int(_f(item.get("txns_h24")))
    if txns_h24 > 0:
        return round(max(0.0, min(100.0, (txns_h1 / txns_h24) * 100.0)), 2)
    chg1h = _f(item.get("change_1h"))
    chg24h = abs(_f(item.get("change_24h")))
    if chg1h > 0 and chg24h > 0:
        return round(max(0.0, min(100.0, (chg1h / chg24h) * 100.0)), 2)
    return 0.0


def _runner_signal_from_item(item: dict[str, Any], now_iso: str) -> dict[str, Any] | None:
    mint = str(item.get("mint") or "").strip()
    if not mint:
        return None
    freshness = str(item.get("data_freshness") or "").upper()
    if freshness not in {"FRESH", "CACHE_FALLBACK", "RECENT_PROVIDER_FALLBACK", "SPOT_PRICE_FALLBACK"}:
        return None

    quality = _f(item.get("quality_score"))
    survivability = _f(item.get("survivability_score"))
    buy_trigger = _f(item.get("buy_trigger_score"))
    liq = _f(item.get("liquidity_usd"))
    vol = _f(item.get("volume_24h"))
    mcap = _f(item.get("mcap_usd"))
    chg1h = _f(item.get("change_1h"))
    chg24h = _f(item.get("change_24h"))
    if freshness == "SPOT_PRICE_FALLBACK":
        quality = min(quality, 55.0)
        buy_trigger = min(buy_trigger, 35.0)
    score = _clamp((quality * 0.45) + (buy_trigger * 0.35) + (survivability * 0.20))
    if score < RUNNER_HEARTBEAT_MIN_SCORE:
        return None

    rug: dict[str, Any] = {
        "rug_label": "UNKNOWN",
        "top_holder_pct": 0.0,
        "top5_holder_pct": 0.0,
        "lp_locked_pct": 0.0,
        "mint_revoked": False,
        "freeze_revoked": False,
        "risk_names": [],
    }
    try:
        from utils.memecoin_scanner import _rug_check  # type: ignore

        rug = dict(_rug_check(mint) or rug)
    except Exception:
        pass

    buy_pressure = _runner_buy_pressure(item)
    vol_accel = _runner_vol_acceleration(item)
    market_quality = _clamp(
        quality
        + (8.0 if liq >= 250_000 else 0.0)
        + (8.0 if vol >= 250_000 else 0.0)
        + (5.0 if buy_pressure >= 52 else 0.0)
        - (10.0 if freshness == "SPOT_PRICE_FALLBACK" else 0.0)
    )
    timing = _clamp((buy_trigger * 0.55) + (max(0.0, chg1h) * 3.0) + min(18.0, vol_accel))
    rug_label = str(rug.get("rug_label") or "UNKNOWN").upper()
    safety = _clamp(78.0 if rug_label == "GOOD" else 58.0 if rug_label == "WARN" else 38.0)
    if bool(rug.get("mint_revoked")):
        safety = _clamp(safety + 8.0)
    if float(rug.get("top_holder_pct") or 0.0) >= 20.0:
        safety = _clamp(safety - 15.0)

    market_verdict = (
        "CLEAN"
        if market_quality >= 75 and liq >= 150_000 and vol >= 100_000
        else "WATCH"
        if market_quality >= 58 and liq >= 75_000
        else "UNSTABLE"
    )
    return {
        "mint": mint,
        "symbol": str(item.get("symbol") or "UNKNOWN").upper(),
        "price": _f(item.get("price"), None),
        "change_1h": round(chg1h, 2),
        "change_24h": round(chg24h, 2),
        "volume_24h": round(vol, 0),
        "liquidity_usd": round(liq, 0),
        "mcap_usd": round(mcap, 0),
        "token_age_days": item.get("age_days"),
        "vol_acceleration": vol_accel,
        "buy_pressure": buy_pressure,
        "score": round(score, 1),
        "scanner_rank_score": round(score, 1),
        "timing_score": round(timing, 1),
        "safety_score": round(safety, 1),
        "market_quality_score": round(market_quality, 1),
        "profit_room_score": round(max(0.0, 100.0 - max(0.0, _f(item.get("from_reference_multiple")) * 12.0)), 1),
        "profit_room_label": "WORKABLE",
        "profit_room_reasons": ["runner heartbeat profile"],
        "score_contract_version": 3,
        "score_components": {
            "runner_heartbeat": {
                "quality_score": quality,
                "buy_trigger_score": buy_trigger,
                "survivability_score": survivability,
                "money_state": item.get("money_state"),
                "verdict": item.get("verdict"),
            }
        },
        "rug_label": rug_label,
        "top_holder_pct": float(rug.get("top_holder_pct") or 0.0),
        "top5_holder_pct": float(rug.get("top5_holder_pct") or 0.0),
        "lp_locked_pct": float(rug.get("lp_locked_pct") or 0.0),
        "mint_revoked": bool(rug.get("mint_revoked")),
        "freeze_revoked": bool(rug.get("freeze_revoked")),
        "bundle_risk_level": "LOW" if rug_label in {"GOOD", "WARN"} else "UNKNOWN",
        "bundle_risk_reasons": list(rug.get("risk_names") or [])[:5],
        "holder_quality_score": round(_clamp(100.0 - float(rug.get("top_holder_pct") or 0.0) * 3.0), 1) if rug.get("top_holder_pct") else None,
        "holder_quality_level": "GOOD" if float(rug.get("top_holder_pct") or 0.0) and float(rug.get("top_holder_pct") or 0.0) < 10 else "UNKNOWN",
        "holder_quality_reasons": ["runner heartbeat safety refresh"],
        "entry_context": str(item.get("entry_state") or "RUNNER_WATCH"),
        "entry_context_score": round(buy_trigger, 1),
        "entry_context_reasons": list(item.get("buy_trigger_reasons") or item.get("reasons") or [])[:5],
        "market_quality_verdict": market_verdict,
        "market_quality_reasons": [
            f"runner heartbeat freshness={freshness}",
            f"verdict={item.get('verdict')}",
            f"money_state={item.get('money_state')}",
        ],
        "trade_quality_snapshot": {
            "quality_verdict": market_verdict,
            "execution_quality_score": round(market_quality, 1),
            "inputs": {"liquidity_usd": liq, "volume_24h": vol, "buy_pressure": buy_pressure},
        },
        "scanner_regime": "RUNNER_HEARTBEAT",
        "scanner_relaxation_reason": "established_runner_active_coverage",
        "scanned_at": now_iso,
        "source": "RUNNER_HEARTBEAT",
        "runner_profile": item.get("profile"),
        "runner_money_state": item.get("money_state"),
        "runner_verdict": item.get("verdict"),
        "runner_data_freshness": freshness,
    }


def _record_runner_heartbeat_outcomes(conn, signals: list[dict[str, Any]], now_iso: str) -> int:
    if not signals:
        return 0
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=RUNNER_HEARTBEAT_OUTCOME_MINUTES)).strftime("%Y-%m-%d %H:%M:%S")
    inserted = 0
    for sig in signals:
        mint = str(sig.get("mint") or "").strip()
        if not mint:
            continue
        try:
            exists = conn.execute(
                """
                SELECT 1 FROM memecoin_signal_outcomes
                WHERE mint=? AND source='RUNNER_HEARTBEAT' AND scanned_at >= ?
                LIMIT 1
                """,
                (mint, cutoff),
            ).fetchone()
            if exists:
                continue
            conn.execute(
                """
                INSERT OR IGNORE INTO memecoin_signal_outcomes (
                    scanned_at, symbol, mint, score, price_at_scan,
                    change_1h_at_scan, change_24h_at_scan, volume_24h, liquidity_usd,
                    source, scanner_regime, scanner_relaxation_reason,
                    rug_label, top_holder_pct, top5_holder_pct, lp_locked_pct,
                    mcap_at_scan, token_age_days, vol_acceleration,
                    mint_revoked, freeze_revoked, bundle_risk_level, bundle_risk_reasons,
                    holder_quality_score, holder_quality_level, holder_quality_reasons,
                    entry_context, entry_context_score, entry_context_reasons,
                    timing_score, safety_score, market_quality_score, scanner_rank_score,
                    score_contract_version, score_components_json,
                    buy_pressure_at_scan, is_actionable_at_scan
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    now_iso,
                    sig.get("symbol"),
                    mint,
                    sig.get("score"),
                    sig.get("price"),
                    sig.get("change_1h"),
                    sig.get("change_24h"),
                    sig.get("volume_24h"),
                    sig.get("liquidity_usd"),
                    "RUNNER_HEARTBEAT",
                    sig.get("scanner_regime"),
                    sig.get("scanner_relaxation_reason"),
                    sig.get("rug_label"),
                    sig.get("top_holder_pct"),
                    sig.get("top5_holder_pct"),
                    sig.get("lp_locked_pct"),
                    sig.get("mcap_usd"),
                    sig.get("token_age_days"),
                    sig.get("vol_acceleration"),
                    int(bool(sig.get("mint_revoked"))),
                    int(bool(sig.get("freeze_revoked"))),
                    sig.get("bundle_risk_level"),
                    json.dumps(sig.get("bundle_risk_reasons") or []),
                    sig.get("holder_quality_score"),
                    sig.get("holder_quality_level"),
                    json.dumps(sig.get("holder_quality_reasons") or []),
                    sig.get("entry_context"),
                    sig.get("entry_context_score"),
                    json.dumps(sig.get("entry_context_reasons") or []),
                    sig.get("timing_score"),
                    sig.get("safety_score"),
                    sig.get("market_quality_score"),
                    sig.get("scanner_rank_score"),
                    int(sig.get("score_contract_version") or 0),
                    json.dumps(sig.get("score_components") or {}),
                    sig.get("buy_pressure"),
                    int(float(sig.get("score") or 0.0) >= 70.0),
                ),
            )
            inserted += 1
        except Exception:
            continue
    return inserted


def runner_heartbeat_step(*, limit: int | None = None, record: bool = True) -> dict[str, Any]:
    """Actively refresh configured runners and expose scanner-shaped proof inputs."""
    from utils.db import get_conn  # type: ignore

    now_iso = _now_iso()
    payload: dict[str, Any] | None = None
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            payload = build_conviction_recovery(limit=limit or RUNNER_HEARTBEAT_MAX_SIGNALS, record=record)
            last_error = None
            break
        except Exception as exc:
            last_error = exc
            if "database is locked" not in str(exc).lower():
                raise
            time.sleep(1.5 * (attempt + 1))
    if payload is None:
        status = {
            "enabled": True,
            "generated_at": now_iso,
            "configured_count": 0,
            "tokens_refreshed": 0,
            "signals_count": 0,
            "outcomes_inserted": 0,
            "min_score": RUNNER_HEARTBEAT_MIN_SCORE,
            "error": str(last_error or "runner heartbeat refresh failed"),
            "top": [],
        }
        try:
            with get_conn() as conn:
                conn.execute(
                    """
                    INSERT INTO kv_store (key, value) VALUES ('memecoin_runner_heartbeat_status', ?)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value
                    """,
                    (json.dumps(status),),
                )
        except Exception:
            pass
        return status
    tokens = [dict(item or {}) for item in list(payload.get("tokens") or [])]
    signals = [
        signal for signal in (_runner_signal_from_item(item, now_iso) for item in tokens)
        if signal is not None
    ]
    signals.sort(
        key=lambda row: (
            str(row.get("runner_data_freshness") or "") != "FRESH",
            -float(row.get("score") or 0.0),
            -float(row.get("market_quality_score") or 0.0),
        )
    )
    signals = signals[:RUNNER_HEARTBEAT_MAX_SIGNALS]
    with get_conn() as conn:
        _ensure_tables(conn)
        inserted = _record_runner_heartbeat_outcomes(conn, signals, now_iso) if record else 0
        status = {
            "enabled": True,
            "generated_at": now_iso,
            "configured_count": len(established_runner_profile_map()),
            "tokens_refreshed": len(tokens),
            "signals_count": len(signals),
            "outcomes_inserted": inserted,
            "min_score": RUNNER_HEARTBEAT_MIN_SCORE,
            "top": [
                {
                    "symbol": sig.get("symbol"),
                    "mint": sig.get("mint"),
                    "score": sig.get("score"),
                    "market_quality_score": sig.get("market_quality_score"),
                    "runner_verdict": sig.get("runner_verdict"),
                    "money_state": sig.get("runner_money_state"),
                    "rug_label": sig.get("rug_label"),
                    "freshness": sig.get("runner_data_freshness"),
                }
                for sig in signals[:10]
            ],
        }
        conn.execute(
            """
            INSERT INTO kv_store (key, value) VALUES ('memecoin_runner_heartbeat_signals', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (json.dumps({"generated_at": now_iso, "signals": signals, "status": status}),),
        )
        conn.execute(
            """
            INSERT INTO kv_store (key, value) VALUES ('memecoin_runner_heartbeat_status', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (json.dumps(status),),
        )
    return status


def _alert_message(item: dict[str, Any], alert_type: str) -> str:
    symbol = escape(str(item.get("symbol") or "UNKNOWN"))
    mint = escape(str(item.get("mint") or ""))
    state = escape(str(item.get("money_state") or ""))
    freshness = escape(str(item.get("data_freshness") or "UNKNOWN"))
    plan = escape(str(item.get("entry_plan") or item.get("trigger") or "Review manually."))
    if alert_type == "PROTECT_PROFIT":
        title = "PROTECT PROFIT"
        score = f"exit {int(_f(item.get('exit_trigger_score')))}"
    elif alert_type == "BUYABLE_NOW":
        title = "BUYABLE NOW"
        score = f"buy {int(_f(item.get('buy_trigger_score')))}"
    else:
        title = "TRIGGER FORMING"
        score = f"buy {int(_f(item.get('buy_trigger_score')))}"
    return (
        f"<b>{title}: ${symbol}</b>\n"
        f"<code>{state} · {score} · {freshness}</code>\n"
        f"<code>CA: {mint}</code>\n"
        f"{plan}"
    )


def _record_money_alerts(conn, rows: list[dict[str, Any]], now_iso: str) -> list[dict[str, Any]]:
    emitted: list[dict[str, Any]] = []
    for item in rows:
        mint = str(item.get("mint") or "").strip()
        state = str(item.get("money_state") or "").upper()
        if not mint or state not in {"BUYABLE_NOW", "PROTECT_PROFIT", "WAITING_CONFIRMATION"}:
            continue
        alert_type = state
        previous = conn.execute(
            """
            SELECT ts_utc
            FROM conviction_recovery_alerts
            WHERE mint=? AND alert_type=?
            ORDER BY ts_utc DESC
            LIMIT 1
            """,
            (mint, alert_type),
        ).fetchone()
        age = _age_seconds(previous["ts_utc"]) if previous else None
        if age is not None and age < ALERT_COOLDOWN_SECONDS:
            continue
        message = _alert_message(item, alert_type)
        sent = _send_telegram_alert(message)
        conn.execute(
            """
            INSERT INTO conviction_recovery_alerts (
                ts_utc, symbol, mint, alert_type, money_state, buy_trigger_score,
                exit_trigger_score, data_freshness, message, sent_telegram, raw_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                now_iso,
                item.get("symbol"),
                mint,
                alert_type,
                state,
                _f(item.get("buy_trigger_score"), None),
                _f(item.get("exit_trigger_score"), None),
                item.get("data_freshness"),
                message,
                1 if sent else 0,
                json.dumps(item, separators=(",", ":")),
            ),
        )
        emitted.append(
            {
                "ts_utc": now_iso,
                "symbol": item.get("symbol"),
                "mint": mint,
                "alert_type": alert_type,
                "money_state": state,
                "buy_trigger_score": item.get("buy_trigger_score"),
                "exit_trigger_score": item.get("exit_trigger_score"),
                "data_freshness": item.get("data_freshness"),
                "sent_telegram": sent,
                "message": message,
            }
        )
    return emitted


def _recent_money_alerts(conn, limit: int = 5) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT ts_utc, symbol, mint, alert_type, money_state, buy_trigger_score,
               exit_trigger_score, data_freshness, message, sent_telegram
        FROM conviction_recovery_alerts
        ORDER BY ts_utc DESC
        LIMIT ?
        """,
        (max(1, min(int(limit), 20)),),
    ).fetchall()
    return [dict(row) for row in rows]


def build_conviction_recovery_cached(limit: int = 8) -> dict[str, Any]:
    """Fast dashboard-safe recovery board from the latest cached market rows.

    The full builder can make provider calls and write snapshot history. Home only
    needs a current operator read, so this path avoids network IO and heavy writes.
    """
    from utils.db import get_conn  # type: ignore

    now_iso = _now_iso()
    max_rows = max(1, min(int(limit), 20))
    rows: list[dict[str, Any]] = []
    emitted_alerts: list[dict[str, Any]] = []
    replay_summary: dict[str, Any] = {"total": 0, "missed": 0, "tracked_winners": 0, "top": [], "blockers": {}}
    recent_alerts: list[dict[str, Any]] = []
    with get_conn() as conn:
        _ensure_tables(conn)
        cached_rows = conn.execute(
            """
            SELECT mint, symbol, profile, snapshot_json, snapshot_ts_utc, source,
                   quality_score, verdict, entry_state, exit_state, updated_at
            FROM conviction_recovery_market_cache
            WHERE COALESCE(snapshot_json, '') != ''
            ORDER BY
                CASE
                    WHEN verdict='SCOUT_READY' THEN 6
                    WHEN verdict='WATCH_CLOSE' THEN 5
                    WHEN verdict='ACCUMULATION_ZONE' THEN 4
                    WHEN verdict='EXIT_WATCH' THEN 3
                    WHEN verdict='SELL_INTO_STRENGTH' THEN 2
                    ELSE 1
                END DESC,
                COALESCE(quality_score, 0) DESC,
                snapshot_ts_utc DESC
            LIMIT ?
            """,
            (max_rows * 3,),
        ).fetchall()
        for row in cached_rows:
            try:
                item = json.loads(row["snapshot_json"] or "{}")
                item = item if isinstance(item, dict) else {}
            except Exception:
                item = {}
            if not item:
                continue
            mint = str(row["mint"] or item.get("mint") or "").strip()
            if not mint:
                continue
            age = _age_seconds(row["snapshot_ts_utc"])
            item.update({
                "mint": mint,
                "symbol": str(row["symbol"] or item.get("symbol") or "UNKNOWN").upper(),
                "profile": row["profile"] or item.get("profile"),
                "snapshot_as_of": row["snapshot_ts_utc"] or item.get("snapshot_as_of"),
                "updated_at": now_iso,
                "stale_age_seconds": age,
                "provider_source": row["source"] or item.get("provider_source") or "conviction_recovery_cache",
            })
            if age is None:
                item["data_freshness"] = "NO_DATA"
            elif age <= CACHE_MAX_AGE_SECONDS:
                item["data_freshness"] = "CACHE_FALLBACK"
            elif age <= max(CACHE_MAX_AGE_SECONDS * 6, 3600):
                item["data_freshness"] = "RECENT_PROVIDER_FALLBACK"
            else:
                item["data_freshness"] = "STALE_REVIEW_ONLY"
            item.update(_coin_memory(conn, mint, item))
            item.update(_runner_replay_for(conn, mint, item))
            item.update(_money_mode(item))
            rows.append(item)
            if len(rows) >= max_rows:
                break
        recent_alerts = _recent_money_alerts(conn, limit=5)
        replay_summary = _missed_runner_replay_summary(conn, limit=5)

    return {
        "generated_at": now_iso,
        "headline": "Tracking cached recovery/leader coins without provider blocking.",
        "summary": {
            "total": len(rows),
            "auto_expanded": sum(1 for r in rows if r.get("profile") == "auto_watchlist_expansion"),
            "replay_missed": int(replay_summary.get("missed") or 0),
            "replay_tracked_winners": int(replay_summary.get("tracked_winners") or 0),
            "fresh": sum(1 for r in rows if r.get("data_freshness") == "FRESH"),
            "spot_price_fallback": sum(1 for r in rows if r.get("data_freshness") == "SPOT_PRICE_FALLBACK"),
            "cache_fallback": sum(1 for r in rows if r.get("data_freshness") == "CACHE_FALLBACK"),
            "recent_fallback": sum(1 for r in rows if r.get("data_freshness") == "RECENT_PROVIDER_FALLBACK"),
            "stale": sum(1 for r in rows if r.get("data_freshness") in {"STALE_REVIEW_ONLY", "NO_DATA"}),
            "quality_pass": sum(1 for r in rows if r.get("quality_floor_pass") and r.get("data_freshness") in {"FRESH", "CACHE_FALLBACK", "RECENT_PROVIDER_FALLBACK"}),
            "quality_reject": sum(1 for r in rows if r.get("verdict") == "QUALITY_REJECT"),
            "buyable_now": sum(1 for r in rows if r.get("money_state") == "BUYABLE_NOW"),
            "waiting_confirmation": sum(1 for r in rows if r.get("money_state") == "WAITING_CONFIRMATION"),
            "waiting_trigger": sum(1 for r in rows if r.get("money_state") == "WAITING_TRIGGER"),
            "protect_profit": sum(1 for r in rows if r.get("money_state") == "PROTECT_PROFIT"),
            "data_wait": sum(1 for r in rows if r.get("money_state") == "DATA_WAIT"),
            "no_trade": sum(1 for r in rows if r.get("money_state") == "NO_TRADE"),
            "scout_ready": sum(1 for r in rows if r.get("verdict") == "SCOUT_READY" and r.get("data_freshness") in {"FRESH", "CACHE_FALLBACK", "RECENT_PROVIDER_FALLBACK"}),
            "watch_close": sum(1 for r in rows if r.get("verdict") == "WATCH_CLOSE" and r.get("data_freshness") in {"FRESH", "CACHE_FALLBACK", "RECENT_PROVIDER_FALLBACK"}),
            "accumulation_zone": sum(1 for r in rows if r.get("verdict") == "ACCUMULATION_ZONE" and r.get("data_freshness") in {"FRESH", "CACHE_FALLBACK", "RECENT_PROVIDER_FALLBACK"}),
            "exit_watch": sum(1 for r in rows if r.get("verdict") in {"EXIT_WATCH", "SELL_INTO_STRENGTH"} and r.get("data_freshness") in {"FRESH", "CACHE_FALLBACK", "RECENT_PROVIDER_FALLBACK"}),
        },
        "tokens": rows,
        "alerts": {
            "emitted": emitted_alerts,
            "recent": recent_alerts,
            "telegram_enabled": TELEGRAM_ALERTS_ENABLED,
            "cooldown_seconds": ALERT_COOLDOWN_SECONDS,
        },
        "expansion": {
            "enabled": AUTO_EXPANSION_ENABLED,
            "min_score": AUTO_EXPANSION_MIN_SCORE,
            "candidates_loaded": 0,
            "top": [],
        },
        "replay": replay_summary,
        "cache_only": True,
    }


def build_conviction_recovery(limit: int = 8, *, record: bool = True) -> dict[str, Any]:
    from utils.db import get_conn  # type: ignore

    try:
        from data.dexscreener import fetch_token_snapshot  # type: ignore
    except Exception:
        fetch_token_snapshot = None  # type: ignore

    search_discovered = _fetch_search_candidates(LEGACY_QUERIES, per_query=6, limit=MAX_DISCOVERY_TOKENS)
    expansion_candidates = _watchlist_expansion_candidates(search_discovered)
    discovered = [*expansion_candidates, *search_discovered]
    now_iso = _now_iso()
    seed_tokens = _seed_tokens()
    seed_mints = {str(token.get("mint") or "").strip() for token in seed_tokens if str(token.get("mint") or "").strip()}
    tokens = _merge_token_meta(seed_tokens, discovered)
    mints = {str(token.get("mint") or "").strip() for token in tokens if str(token.get("mint") or "").strip()}
    direct_snapshots = _fetch_direct_snapshots(list(mints))
    gecko_snapshots = _fetch_gecko_snapshots(mints)
    spot_fallbacks = _spot_basket_fallback_snapshots()
    rows: list[dict[str, Any]] = []
    emitted_alerts: list[dict[str, Any]] = []
    recent_alerts: list[dict[str, Any]] = []
    replay_summary: dict[str, Any] = {"total": 0, "missed": 0, "tracked_winners": 0, "top": [], "blockers": {}}
    with get_conn() as conn:
        _ensure_tables(conn)
        for token in tokens:
            mint = str(token.get("mint") or "").strip()
            if not mint:
                continue
            snap_source = None
            snap = direct_snapshots.get(mint)
            if snap:
                snap_source = "dexscreener_batch"
            if not snap and fetch_token_snapshot:
                snap = fetch_token_snapshot(mint) or {}
                if snap:
                    snap_source = str(snap.get("source") or "dexscreener_token")
            if not snap:
                snap = gecko_snapshots.get(mint) or {}
                if snap:
                    snap_source = "geckoterminal_trending"
            if not snap:
                cached_item = _cache_fallback_item(conn, mint, now_iso)
                if cached_item:
                    cached_item.update(_coin_memory(conn, mint, cached_item))
                    cached_item.update(_runner_replay_for(conn, mint, cached_item))
                    cached_item.update(_money_mode(cached_item))
                    cached_item = _update_confirmation(conn, cached_item, now_iso)
                    rows.append(cached_item)
                    continue
                snap = spot_fallbacks.get(mint) or {}
                if snap:
                    snap_source = "spot_price_fallback"
            if not snap:
                cached_item = _stale_fallback_item(conn, token, mint, now_iso)
                if cached_item:
                    cached_item.update(_coin_memory(conn, mint, cached_item))
                    cached_item.update(_runner_replay_for(conn, mint, cached_item))
                    cached_item.update(_money_mode(cached_item))
                    cached_item = _update_confirmation(conn, cached_item, now_iso)
                    rows.append(cached_item)
                    continue
                no_data_item = {
                    **token,
                    "verdict": "NO_DATA",
                    "entry_state": "WAIT",
                    "exit_state": "UNKNOWN",
                    "trigger": "No fresh market snapshot available.",
                    "invalidation": "Wait for fresh data.",
                    "take_profit": "No plan without data.",
                    "reasons": ["snapshot unavailable"],
                    "data_freshness": "NO_DATA",
                    "snapshot_as_of": None,
                    "updated_at": now_iso,
                }
                no_data_item.update(_coin_memory(conn, mint, no_data_item))
                no_data_item.update(_runner_replay_for(conn, mint, no_data_item))
                no_data_item.update(_money_mode(no_data_item))
                no_data_item = _update_confirmation(conn, no_data_item, now_iso)
                rows.append(no_data_item)
                continue
            decision = _classify(token, snap)
            mcap = _f(snap.get("market_cap") or snap.get("fdv"))
            liq = _f(snap.get("liquidity"))
            vol = _f(snap.get("volume_24h"))
            vol_liq = vol / liq if liq > 0 else 0.0
            item = {
                "symbol": str(token.get("symbol") or snap.get("symbol") or "UNKNOWN").upper(),
                "mint": mint,
                "profile": token.get("profile"),
                "thesis": token.get("thesis"),
                "expansion_score": token.get("expansion_score"),
                "expansion_source": token.get("expansion_source"),
                "expansion_reasons": token.get("expansion_reasons"),
                "reference_mcap": _f(token.get("reference_mcap"), None),
                "known_peak_mcap": _f(token.get("known_peak_mcap"), None),
                "mcap_usd": mcap,
                "liquidity_usd": liq,
                "volume_24h": vol,
                "vol_liq_ratio": round(vol_liq, 2),
                "change_1h": _f(snap.get("change_1h")),
                "change_6h": _f(snap.get("change_6h")),
                "change_24h": _f(snap.get("change_24h")),
                "txns_h1": int(_f(snap.get("txns_h1"))),
                "txns_h24": int(_f(snap.get("txns_h24"))),
                "age_days": decision.get("age_days"),
                "quality_score": decision.get("quality_score"),
                "quality_floor_pass": decision.get("quality_floor_pass"),
                "price": _f(snap.get("price"), None),
                "pair_address": snap.get("pair_address"),
                "updated_at": now_iso,
                "snapshot_as_of": now_iso,
                "stale_age_seconds": 0,
                "data_freshness": "SPOT_PRICE_FALLBACK" if snap.get("spot_price_fallback") else "FRESH",
                "provider_source": snap_source or snap.get("source") or "unknown",
                **decision,
            }
            item.update(_coin_memory(conn, mint, item))
            item.update(_runner_replay_for(conn, mint, item))
            item.update(_money_mode(item))
            item = _update_confirmation(conn, item, now_iso)
            if record:
                conn.execute(
                    """
                    INSERT INTO conviction_recovery_snapshots (
                        symbol, mint, profile, ts_utc, mcap_usd, liquidity_usd,
                        volume_24h, change_1h, change_6h, change_24h, vol_liq_ratio,
                        txns_h1, txns_h24, verdict, entry_state, exit_state, raw_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item["symbol"],
                        mint,
                        item.get("profile"),
                        now_iso,
                        mcap,
                        liq,
                        vol,
                        item["change_1h"],
                        item["change_6h"],
                        item["change_24h"],
                        item["vol_liq_ratio"],
                        item["txns_h1"],
                        item["txns_h24"],
                        item["verdict"],
                        item["entry_state"],
                        item["exit_state"],
                        json.dumps(item, separators=(",", ":")),
                    ),
                )
                _cache_market_item(item=item, conn=conn, source=str(item.get("provider_source") or ("spot_price_fallback" if snap.get("spot_price_fallback") else "fresh_market_depth")))
            rows.append(item)

    rows = [
        row for row in rows
        if row.get("display_allowed") or str(row.get("profile") or "").startswith("established") or str(row.get("profile") or "") in {"dead_to_reload", "violent_recovery_spike", "meme_leader_beta"}
    ]
    rows.sort(
        key=lambda r: (
            {"BUYABLE_NOW": 9, "PROTECT_PROFIT": 8, "WAITING_CONFIRMATION": 7, "WAITING_TRIGGER": 6, "DATA_WAIT": 2, "NO_TRADE": 1}.get(str(r.get("money_state")), 0),
            {"SCOUT_READY": 6, "WATCH_CLOSE": 5, "ACCUMULATION_ZONE": 4, "EXIT_WATCH": 3, "SELL_INTO_STRENGTH": 2, "WATCH_RELOAD": 1}.get(str(r.get("verdict")), 0),
            _f(r.get("buy_trigger_score")),
            _f(r.get("quality_score")),
            _f(r.get("survivability_score")),
            _f(r.get("volume_24h")),
        ),
        reverse=True,
    )
    limited = rows[: max(1, min(int(limit), 20))]
    with get_conn() as conn:
        _ensure_tables(conn)
        if record:
            emitted_alerts = _record_money_alerts(conn, limited, now_iso)
        recent_alerts = _recent_money_alerts(conn, limit=5)
        replay_summary = _missed_runner_replay_summary(conn, limit=5)
    return {
        "generated_at": now_iso,
        "headline": "Tracking proven and auto-expanded recovery/leader coins for reload entries, replay lessons, and fast exit plans.",
        "summary": {
            "total": len(limited),
            "auto_expanded": sum(1 for r in limited if r.get("profile") == "auto_watchlist_expansion"),
            "replay_missed": int(replay_summary.get("missed") or 0),
            "replay_tracked_winners": int(replay_summary.get("tracked_winners") or 0),
            "fresh": sum(1 for r in limited if r.get("data_freshness") == "FRESH"),
            "spot_price_fallback": sum(1 for r in limited if r.get("data_freshness") == "SPOT_PRICE_FALLBACK"),
            "cache_fallback": sum(1 for r in limited if r.get("data_freshness") == "CACHE_FALLBACK"),
            "recent_fallback": sum(1 for r in limited if r.get("data_freshness") == "RECENT_PROVIDER_FALLBACK"),
            "stale": sum(1 for r in limited if r.get("data_freshness") in {"STALE_REVIEW_ONLY", "NO_DATA"}),
            "quality_pass": sum(1 for r in limited if r.get("quality_floor_pass") and r.get("data_freshness") in {"FRESH", "CACHE_FALLBACK", "RECENT_PROVIDER_FALLBACK"}),
            "quality_reject": sum(1 for r in limited if r.get("verdict") == "QUALITY_REJECT"),
            "buyable_now": sum(1 for r in limited if r.get("money_state") == "BUYABLE_NOW"),
            "waiting_confirmation": sum(1 for r in limited if r.get("money_state") == "WAITING_CONFIRMATION"),
            "waiting_trigger": sum(1 for r in limited if r.get("money_state") == "WAITING_TRIGGER"),
            "protect_profit": sum(1 for r in limited if r.get("money_state") == "PROTECT_PROFIT"),
            "data_wait": sum(1 for r in limited if r.get("money_state") == "DATA_WAIT"),
            "no_trade": sum(1 for r in limited if r.get("money_state") == "NO_TRADE"),
            "scout_ready": sum(1 for r in limited if r.get("verdict") == "SCOUT_READY" and r.get("data_freshness") in {"FRESH", "CACHE_FALLBACK", "RECENT_PROVIDER_FALLBACK"}),
            "watch_close": sum(1 for r in limited if r.get("verdict") == "WATCH_CLOSE" and r.get("data_freshness") in {"FRESH", "CACHE_FALLBACK", "RECENT_PROVIDER_FALLBACK"}),
            "accumulation_zone": sum(1 for r in limited if r.get("verdict") == "ACCUMULATION_ZONE" and r.get("data_freshness") in {"FRESH", "CACHE_FALLBACK", "RECENT_PROVIDER_FALLBACK"}),
            "exit_watch": sum(1 for r in limited if r.get("verdict") in {"EXIT_WATCH", "SELL_INTO_STRENGTH"} and r.get("data_freshness") in {"FRESH", "CACHE_FALLBACK", "RECENT_PROVIDER_FALLBACK"}),
        },
        "tokens": limited,
        "alerts": {
            "emitted": emitted_alerts,
            "recent": recent_alerts,
            "telegram_enabled": TELEGRAM_ALERTS_ENABLED,
            "cooldown_seconds": ALERT_COOLDOWN_SECONDS,
        },
        "expansion": {
            "enabled": AUTO_EXPANSION_ENABLED,
            "min_score": AUTO_EXPANSION_MIN_SCORE,
            "candidates_loaded": len({str(row.get("mint") or row.get("address") or "").strip() for row in expansion_candidates if str(row.get("mint") or row.get("address") or "").strip() not in seed_mints}),
            "top": [
                {
                    "symbol": row.get("symbol"),
                    "mint": row.get("mint") or row.get("address"),
                    "score": round(_f(row.get("expansion_score")), 1),
                    "source": row.get("source"),
                    "reasons": row.get("expansion_reasons") or [],
                }
                for row in [r for r in expansion_candidates if str(r.get("mint") or r.get("address") or "").strip() not in seed_mints][:5]
            ],
        },
        "replay": replay_summary,
    }
