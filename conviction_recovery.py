from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from typing import Any

STALE_OK_SECONDS = max(30, int(os.getenv("CONVICTION_RECOVERY_STALE_OK_SECONDS", "180")))
DIRECT_BATCH_URL = "https://api.dexscreener.com/latest/dex/tokens/{mints}"
MAX_DISCOVERY_TOKENS = max(5, int(os.getenv("CONVICTION_RECOVERY_MAX_DISCOVERY_TOKENS", "35")))
MIN_QUALITY_SCORE = max(0, int(os.getenv("CONVICTION_RECOVERY_MIN_QUALITY_SCORE", "62")))
LEGACY_QUERIES = [
    item.strip()
    for item in os.getenv(
        "CONVICTION_RECOVERY_SEARCH_QUERIES",
        "TROLL,WOJAK,JUP,USDUC,FARTCOIN,WIF,BONK,POPCAT,MEW,PENGU,PNUT,GOAT,MOODENG,BOME,MYRO,PONKE,SLERF,SAMO,RAY,ORCA,JTO,PYTH",
    ).split(",")
    if item.strip()
]
SEARCH_URL = "https://api.dexscreener.com/latest/dex/search?q={query}"


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
        by_mint[mint] = {
            "symbol": str(token.get("symbol") or "UNKNOWN").upper(),
            "mint": mint,
            "profile": "discovered_established",
            "reference_mcap": mcap,
            "known_peak_mcap": mcap,
            "thesis": "Discovered established token with liquidity, volume, and attention signs.",
        }
    return list(by_mint.values())


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
    return {
        "symbol": base.get("symbol") or "UNKNOWN",
        "address": address,
        "pair_address": pair.get("pairAddress"),
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
    url = DIRECT_BATCH_URL.format(mints=",".join(clean))
    payload: dict[str, Any] | None = None
    try:
        import requests  # type: ignore

        response = requests.get(
            url,
            timeout=14,
            headers={"User-Agent": "memecoin-engine/conviction-recovery"},
        )
        if response.status_code == 200:
            payload = response.json() or {}
    except Exception:
        payload = None
    if payload is None:
        try:
            completed = subprocess.run(
                ["curl", "-fsS", "--max-time", "14", "-A", "memecoin-engine/conviction-recovery", url],
                check=True,
                capture_output=True,
                text=True,
            )
            payload = json.loads(completed.stdout or "{}")
        except Exception:
            payload = None
    if not payload:
        return {}
    pairs = payload.get("pairs") or []
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


def _fetch_search_candidates(queries: list[str], per_query: int = 6, limit: int = 35) -> list[dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    for query in queries[:12]:
        url = SEARCH_URL.format(query=query)
        payload: dict[str, Any] | None = None
        try:
            completed = subprocess.run(
                ["curl", "-fsS", "--max-time", "5", "-A", "memecoin-engine/conviction-search", url],
                check=True,
                capture_output=True,
                text=True,
            )
            payload = json.loads(completed.stdout or "{}")
        except Exception:
            payload = None
        for item in (_normalize_dex_pair(pair) for pair in ((payload or {}).get("pairs") or [])[:per_query]):
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

    quality_floor_pass = quality_score >= MIN_QUALITY_SCORE and liq >= 100_000 and vol >= 100_000 and mcap >= 1_000_000

    if not quality_floor_pass:
        verdict = "QUALITY_REJECT"
        entry_state = "NO_TRADE"
        exit_state = "IGNORE"
        trigger = "Rejected: not enough quality, liquidity, volume, history, or community signal for a good-buy surface."
        invalidation = "Needs stronger quality score and renewed activity before it can appear as a buy candidate."
        take_profit = "No TP plan because this is not a good-buy candidate."
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
        "reasons": reasons[:7],
        "from_reference_multiple": round(from_ref, 2) if from_ref else None,
        "drawdown_from_known_peak_pct": drawdown_from_known_peak_pct,
        "age_days": round(age_days, 1) if age_days is not None else None,
    }


def build_conviction_recovery(limit: int = 8, *, record: bool = True) -> dict[str, Any]:
    from utils.db import get_conn  # type: ignore

    try:
        from data.dexscreener import fetch_token_snapshot  # type: ignore
    except Exception:
        fetch_token_snapshot = None  # type: ignore

    discovered = _fetch_search_candidates(LEGACY_QUERIES, per_query=6, limit=MAX_DISCOVERY_TOKENS)
    now_iso = _now_iso()
    tokens = _merge_token_meta(_seed_tokens(), discovered)
    direct_snapshots = _fetch_direct_snapshots([str(token.get("mint") or "") for token in tokens])
    rows: list[dict[str, Any]] = []
    with get_conn() as conn:
        _ensure_tables(conn)
        for token in tokens:
            mint = str(token.get("mint") or "").strip()
            if not mint:
                continue
            snap = direct_snapshots.get(mint) or (fetch_token_snapshot(mint) if fetch_token_snapshot else None) or {}
            if not snap:
                cached_item = _stale_fallback_item(conn, token, mint, now_iso)
                if cached_item:
                    rows.append(cached_item)
                    continue
                rows.append(
                    {
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
                )
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
                "data_freshness": "FRESH",
                **decision,
            }
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
            rows.append(item)

    rows = [
        row for row in rows
        if row.get("quality_floor_pass") or str(row.get("profile") or "").startswith("established") or str(row.get("profile") or "") in {"dead_to_reload", "violent_recovery_spike", "meme_leader_beta"}
    ]
    rows.sort(
        key=lambda r: (
            {"SCOUT_READY": 6, "WATCH_CLOSE": 5, "ACCUMULATION_ZONE": 4, "EXIT_WATCH": 3, "SELL_INTO_STRENGTH": 2, "WATCH_RELOAD": 1}.get(str(r.get("verdict")), 0),
            _f(r.get("quality_score")),
            _f(r.get("survivability_score")),
            _f(r.get("volume_24h")),
        ),
        reverse=True,
    )
    limited = rows[: max(1, min(int(limit), 20))]
    return {
        "generated_at": now_iso,
        "headline": "Tracking your proven recovery/leader coins for reload entries and fast exit plans.",
        "summary": {
            "total": len(limited),
            "fresh": sum(1 for r in limited if r.get("data_freshness") == "FRESH"),
            "recent_fallback": sum(1 for r in limited if r.get("data_freshness") == "RECENT_PROVIDER_FALLBACK"),
            "stale": sum(1 for r in limited if r.get("data_freshness") in {"STALE_REVIEW_ONLY", "NO_DATA"}),
            "quality_pass": sum(1 for r in limited if r.get("quality_floor_pass")),
            "quality_reject": sum(1 for r in limited if r.get("verdict") == "QUALITY_REJECT"),
            "scout_ready": sum(1 for r in limited if r.get("verdict") == "SCOUT_READY" and r.get("data_freshness") in {"FRESH", "RECENT_PROVIDER_FALLBACK"}),
            "watch_close": sum(1 for r in limited if r.get("verdict") == "WATCH_CLOSE" and r.get("data_freshness") in {"FRESH", "RECENT_PROVIDER_FALLBACK"}),
            "accumulation_zone": sum(1 for r in limited if r.get("verdict") == "ACCUMULATION_ZONE" and r.get("data_freshness") in {"FRESH", "RECENT_PROVIDER_FALLBACK"}),
            "exit_watch": sum(1 for r in limited if r.get("verdict") in {"EXIT_WATCH", "SELL_INTO_STRENGTH"} and r.get("data_freshness") in {"FRESH", "RECENT_PROVIDER_FALLBACK"}),
        },
        "tokens": limited,
    }
