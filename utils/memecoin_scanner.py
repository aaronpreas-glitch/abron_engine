"""
Memecoin Scanner — Patch 131
Enhanced scanner: RugCheck safety, market cap, token age, volume acceleration.
Thresholds auto-tune from learned outcomes stored in kv_store.
Results cached in kv_store['memecoin_scan_cache'].
"""

import json
import logging
import math
import time
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

log = logging.getLogger(__name__)


def _alert_dex_429_scanner() -> None:
    """Rate-limited Telegram when DexScreener 429s affect the memecoin scan. Patch 163/164.

    Patch 164: upgraded from in-memory to persistent kv_store rate limit.
    """
    from utils.db import persistent_rate_limit_check  # Patch 164
    from utils.telegram_alerts import send_telegram_sync  # noqa
    if persistent_rate_limit_check("dex_429_scanner", 1800):  # 30 min
        return
    send_telegram_sync(
        "DexScreener Rate Limit",
        "Memecoin scanner: DexScreener returning 429 (rate limited).\n"
        "Signals may be <b>missed</b> this scan cycle.",
        "⚠️",
    )

DEXSCREENER_BASE     = "https://api.dexscreener.com"
RUGCHECK_BASE        = "https://api.rugcheck.xyz/v1"
REQUEST_TIMEOUT      = 8
RUGCHECK_TIMEOUT     = 7
RUGCHECK_CACHE_TTL_S = 1800   # 30 min — token safety rarely changes


# ── Default thresholds (overridden by learned values in kv_store) ─────────────

DEFAULT_THRESHOLDS: dict = {
    "min_volume_24h":       25_000,   # min 24h volume USD (was 50k — catches outliers in fear markets)
    "min_liquidity_usd":    10_000,   # min LP liquidity USD
    "min_price_change_1h":   3.0,     # min 1h price change % (was 5.0 — catches earlier momentum)
    "min_mcap":            300_000,   # min market cap USD  (skip micro-dust)
    "max_mcap":         50_000_000,   # max market cap USD  (skip already-mooned)
    "min_age_days":          1.0,     # skip brand new (bundle risk)
    "max_age_days":         30.0,     # skip stale (momentum window closed)
    "min_vol_acceleration":  3.0,     # h1_vol >= 3% of h24_vol (was 5.0 — still filters noise)
    "max_top_holder_pct":   35.0,     # skip if single wallet holds >35%
    "allow_warn":            True,    # show WARN signals; filter only DANGER/RUGGED
    "top_n":                10,
    # Patch 131 — overextension gates (data-driven from 342 4h outcomes)
    "max_1h_change_pct":    20.0,     # skip already-pumped tokens (>20% 1h → 0% win rate)
    "max_vol_liq_ratio":    15.0,     # skip thinly-traded / wash-traded (vol/liq > 15x → 27% win rate)
}


def _load_thresholds() -> dict:
    """Merge learned thresholds from kv_store with defaults.

    Patch 132: confidence gate — scanner filtering thresholds (vol_acceleration,
    top_holder_pct) only apply at medium/high confidence (50+ samples).
    At low confidence the tuner is dominated by a single token (WAR) and
    overfits. min_score is auto_buy-only so always safe to pass through.
    """
    t = DEFAULT_THRESHOLDS.copy()
    try:
        from utils.db import get_conn
        with get_conn() as conn:
            row = conn.execute(
                "SELECT value FROM kv_store WHERE key='memecoin_learned_thresholds'"
            ).fetchone()
            if row:
                learned     = json.loads(row["value"])
                all_t       = learned.get("thresholds", {})
                confidence  = learned.get("confidence", "low")
                # min_score: used only by auto_buy — safe at any confidence
                if "min_score" in all_t:
                    t["min_score"] = all_t["min_score"]
                # Scanner filters: only apply when we have enough samples to trust them
                if confidence in ("medium", "high"):
                    for k in ("min_vol_acceleration", "max_top_holder_pct"):
                        if k in all_t:
                            t[k] = all_t[k]
    except Exception:
        pass
    return t


# ── RugCheck (with kv_store cache) ───────────────────────────────────────────

def _load_rugcheck_cache(mint: str) -> dict | None:
    try:
        from utils.db import get_conn
        with get_conn() as conn:
            row = conn.execute(
                "SELECT value FROM kv_store WHERE key=?",
                (f"rugcheck:{mint}",)
            ).fetchone()
            if row:
                cached = json.loads(row["value"])
                if time.time() - cached.get("_ts", 0) < RUGCHECK_CACHE_TTL_S:
                    return cached
    except Exception:
        pass
    return None


def _save_rugcheck_cache(mint: str, data: dict):
    try:
        from utils.db import get_conn
        data["_ts"] = time.time()
        with get_conn() as conn:
            conn.execute("""
                INSERT INTO kv_store (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """, (f"rugcheck:{mint}", json.dumps(data)))
    except Exception:
        pass


def _rug_check(mint: str) -> dict:
    """
    Call RugCheck API for safety enrichment. Results cached 30 min.
    Returns: rug_label, top_holder_pct, lp_locked_pct, mint_revoked, freeze_revoked
    """
    cached = _load_rugcheck_cache(mint)
    if cached:
        return cached

    result = {
        "rug_label":      "UNKNOWN",
        "top_holder_pct": 0.0,
        "lp_locked_pct":  0.0,
        "mint_revoked":   False,
        "freeze_revoked": False,
    }
    try:
        r = requests.get(
            f"{RUGCHECK_BASE}/tokens/{mint}/report",
            timeout=RUGCHECK_TIMEOUT,
            headers={"User-Agent": "memecoin-engine/1.0"},
        )
        if r.status_code != 200:
            _save_rugcheck_cache(mint, result)
            return result

        d = r.json()

        if d.get("rugged"):
            result["rug_label"] = "RUGGED"
            _save_rugcheck_cache(mint, result)
            return result

        risks  = d.get("risks", [])
        levels = {str(risk.get("level", "")).lower() for risk in risks}
        if "danger" in levels:
            result["rug_label"] = "DANGER"
        elif "warn" in levels:
            result["rug_label"] = "WARN"
        else:
            result["rug_label"] = "GOOD"

        holders = d.get("topHolders", [])
        if holders:
            result["top_holder_pct"] = round(float(holders[0].get("pct", 0)), 1)

        markets = d.get("markets", [])
        if markets:
            lp = markets[0].get("lp", {})
            result["lp_locked_pct"] = round(
                float(lp.get("lpLockedPct") or lp.get("lpBurnedPct") or 0), 1
            )

        result["mint_revoked"]   = d.get("mintAuthority")   is None
        result["freeze_revoked"] = d.get("freezeAuthority") is None

    except Exception:
        pass

    _save_rugcheck_cache(mint, result)
    return result


# ── Scoring ───────────────────────────────────────────────────────────────────

def _score_token(pair: dict, vol_acceleration: float, rug_label: str) -> tuple:
    """
    Score 0-100 incorporating safety, momentum, and buy pressure.
    Returns (score: float, buy_pressure: float).

      vol_score          (0-40): log scale $50k → $10M 24h volume
      mom_score          (0-40): 1h change, capped at +200%
      safety_bonus       (0-10): GOOD=10, WARN=5, else 0
      accel_bonus        (0-10): vol acceleration, 30% → full 10pts
      buy_pressure_bonus (0-10): % of 1h txns that are buys; above 50% adds pts
    """
    vol   = float(pair.get("volume", {}).get("h24", 0) or 0)
    chg1h = float(pair.get("priceChange", {}).get("h1", 0) or 0)

    vol_score = 0.0
    if vol >= 10_000_000:
        vol_score = 40.0
    elif vol > 50_000:
        vol_score = 40.0 * (math.log10(vol) - math.log10(50_000)) / \
                          (math.log10(10_000_000) - math.log10(50_000))

    mom_score = 0.0
    if chg1h >= 200:
        mom_score = 40.0
    elif chg1h > 3:
        mom_score = 40.0 * (chg1h - 3) / (200 - 3)

    safety_bonus = {"GOOD": 10.0, "WARN": 5.0}.get(rug_label, 0.0)
    accel_bonus  = min(10.0, vol_acceleration / 3.0)

    # Buy pressure: % of 1h transactions that are buys
    # >50% = buying pressure, each 5% above neutral adds 1pt (capped at 10)
    txns_h1   = pair.get("txns", {}).get("h1", {})
    buys      = int(txns_h1.get("buys",  0) or 0)
    sells     = int(txns_h1.get("sells", 0) or 0)
    total_txn = buys + sells
    buy_pressure      = round(buys / total_txn * 100, 1) if total_txn > 0 else 50.0
    buy_pressure_bonus = min(10.0, max(0.0, (buy_pressure - 50.0) / 5.0))

    score = round(vol_score + mom_score + safety_bonus + accel_bonus + buy_pressure_bonus, 1)
    return score, buy_pressure


# ── Main scanner ──────────────────────────────────────────────────────────────

def scan_trending_solana(
    min_volume_24h:      float = None,
    min_liquidity_usd:   float = None,
    min_price_change_1h: float = None,
    top_n:               int   = None,
) -> list:
    """
    Scan DexScreener for trending Solana tokens.
    Enriches candidates with RugCheck safety data in parallel.
    All filters (mcap, age, vol accel, holder %) are applied before surfacing.
    """
    t       = _load_thresholds()
    vol_min = min_volume_24h      or t["min_volume_24h"]
    liq_min = min_liquidity_usd   or t["min_liquidity_usd"]
    chg_min = min_price_change_1h or t["min_price_change_1h"]
    top     = top_n               or t["top_n"]
    now_ts  = time.time()

    # Step 1: collect Solana mints from multiple DexScreener endpoints
    sol_mints, seen = [], set()

    def _collect_mints(url: str):
        try:
            r = requests.get(url, timeout=REQUEST_TIMEOUT,
                             headers={"User-Agent": "memecoin-engine/1.0"})
            if r.status_code == 429:  # Patch 163
                log.warning("[SCAN] DexScreener 429 rate limit on %s", url)
                _alert_dex_429_scanner()
                return
            if r.status_code != 200:
                return
            items = r.json()
            if not isinstance(items, list):
                return
            for p in items:
                if isinstance(p, dict) and p.get("chainId") == "solana":
                    mint = p.get("tokenAddress", "")
                    if mint and mint not in seen:
                        sol_mints.append(mint)
                        seen.add(mint)
        except Exception:
            pass

    # Latest token profiles (new listings with DexScreener profiles)
    _collect_mints(f"{DEXSCREENER_BASE}/token-profiles/latest/v1")
    # Top boosted tokens (active projects, team spending = higher legitimacy signal)
    _collect_mints(f"{DEXSCREENER_BASE}/token-boosts/top/v1")
    # Latest boosted tokens
    _collect_mints(f"{DEXSCREENER_BASE}/token-boosts/latest/v1")
    # 4th source: DexScreener trending search — active Solana pairs sorted by activity
    try:
        r = requests.get(
            f"{DEXSCREENER_BASE}/latest/dex/search?q=solana",
            timeout=REQUEST_TIMEOUT,
            headers={"User-Agent": "memecoin-engine/1.0"},
        )
        if r.status_code == 200:
            for pair in (r.json().get("pairs") or []):
                if pair.get("chainId") == "solana":
                    mint = pair.get("baseToken", {}).get("address", "")
                    if mint and mint not in seen:
                        sol_mints.append(mint)
                        seen.add(mint)
    except Exception:
        pass

    # Cap at 90 unique mints (up from 60 — 4 sources)
    sol_mints = sol_mints[:90]

    if not sol_mints:
        return []

    # Step 2: fetch pair details in batches of 5
    candidates = []
    for i in range(0, len(sol_mints), 5):
        batch_str = ",".join(sol_mints[i:i+5])
        try:
            r = requests.get(
                f"{DEXSCREENER_BASE}/latest/dex/tokens/{batch_str}",
                timeout=REQUEST_TIMEOUT,
                headers={"User-Agent": "memecoin-engine/1.0"},
            )
            if r.status_code == 429:  # Patch 163
                log.warning("[SCAN] DexScreener 429 on batch token fetch")
                _alert_dex_429_scanner()
                continue
            if r.status_code != 200:
                continue
            pairs = r.json().get("pairs") or []
            candidates.extend(p for p in pairs if p.get("chainId") == "solana")
        except Exception:
            continue

    # Step 3: apply quality + range filters
    pre_filtered: dict = {}   # mint → (pair, mcap, age_days, vol_accel)
    for pair in candidates:
        vol    = float(pair.get("volume",      {}).get("h24", 0) or 0)
        liq    = float(pair.get("liquidity",   {}).get("usd", 0) or 0)
        chg1h  = float(pair.get("priceChange", {}).get("h1",  0) or 0)
        price  = float(pair.get("priceUsd", 0) or 0)
        vol_h1 = float(pair.get("volume",      {}).get("h1",  0) or 0)
        fdv    = float(pair.get("fdv", 0) or 0)
        mint   = pair.get("baseToken", {}).get("address", "")

        if not mint or price <= 0:               continue
        if vol   < vol_min:                      continue
        if liq   < liq_min:                      continue
        if chg1h < chg_min:                      continue
        if chg1h > t["max_1h_change_pct"]:       continue   # overextension gate
        if liq > 0 and vol / liq > t["max_vol_liq_ratio"]: continue  # manipulation gate

        mcap = fdv or 0
        if mcap > 0:
            if mcap < t["min_mcap"]:             continue
            if mcap > t["max_mcap"]:             continue

        pair_created = pair.get("pairCreatedAt")
        age_days = 9999.0
        if pair_created:
            age_days = (now_ts - pair_created / 1000) / 86400
            if age_days < t["min_age_days"]:     continue
            if age_days > t["max_age_days"]:     continue

        vol_accel = round((vol_h1 / vol * 100), 1) if vol > 0 else 0.0
        if vol_accel < t["min_vol_acceleration"]: continue

        # Deduplicate — keep highest-vol pair per mint
        existing = pre_filtered.get(mint)
        if existing is None or vol > float(
            existing[0].get("volume", {}).get("h24", 0) or 0
        ):
            pre_filtered[mint] = (pair, mcap, age_days, vol_accel)

    if not pre_filtered:
        return []

    # Step 4: RugCheck in parallel (cached calls are near-instant)
    rug_results: dict = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        fut_to_mint = {ex.submit(_rug_check, mint): mint for mint in pre_filtered}
        for fut in as_completed(fut_to_mint):
            mint = fut_to_mint[fut]
            try:
                rug_results[mint] = fut.result()
            except Exception:
                rug_results[mint] = {"rug_label": "UNKNOWN"}

    # Step 5: safety filter + score + build result list
    scored = []
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    for mint, (pair, mcap, age_days, vol_accel) in pre_filtered.items():
        rug   = rug_results.get(mint, {"rug_label": "UNKNOWN"})
        label = rug.get("rug_label", "UNKNOWN")

        if label in ("DANGER", "RUGGED"):
            continue
        if not t["allow_warn"] and label == "WARN":
            continue

        top_h = float(rug.get("top_holder_pct") or 0)
        if top_h > t["max_top_holder_pct"] and top_h > 0:
            continue

        symbol  = pair.get("baseToken", {}).get("symbol", "???").upper()
        price   = float(pair.get("priceUsd", 0) or 0)
        chg1h   = float(pair.get("priceChange", {}).get("h1",  0) or 0)
        chg24h  = float(pair.get("priceChange", {}).get("h24", 0) or 0)
        vol24h  = float(pair.get("volume",      {}).get("h24", 0) or 0)
        liq     = float(pair.get("liquidity",   {}).get("usd", 0) or 0)
        dex_url = pair.get("url", f"https://dexscreener.com/solana/{mint}")
        score, buy_pressure = _score_token(pair, vol_accel, label)

        # Narrative momentum bonus — Patch 127
        narrative_trending  = False
        narrative_sources: list = []
        try:
            from utils.narrative_momentum import is_trending as _is_trending  # type: ignore
            _nt = _is_trending(symbol, mint)
            if _nt["trending"]:
                narrative_trending = True
                narrative_sources  = _nt["sources"]
                score = min(100.0, round(score + _nt["bonus"], 1))
        except Exception:
            pass

        scored.append({
            "mint":               mint,
            "symbol":             symbol,
            "price":              price,
            "change_1h":          round(chg1h,  2),
            "change_24h":         round(chg24h, 2),
            "volume_24h":         round(vol24h, 0),
            "liquidity_usd":      round(liq, 0),
            "mcap_usd":           round(mcap, 0),
            "token_age_days":     round(age_days, 1),
            "vol_acceleration":   vol_accel,
            "buy_pressure":       buy_pressure,
            "score":              score,
            "rug_label":          label,
            "top_holder_pct":     top_h,
            "lp_locked_pct":      float(rug.get("lp_locked_pct") or 0),
            "mint_revoked":       bool(rug.get("mint_revoked", False)),
            "freeze_revoked":     bool(rug.get("freeze_revoked", False)),
            "dex_url":            dex_url,
            "scanned_at":         now_str,
            "narrative":          narrative_trending,
            "narrative_sources":  narrative_sources,
        })

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:top]


# ── Cache helpers ─────────────────────────────────────────────────────────────

def get_cached_signals() -> list:
    """Read last scan results from kv_store['memecoin_scan_cache']."""
    try:
        from utils.db import get_conn
        with get_conn() as conn:
            row = conn.execute(
                "SELECT value FROM kv_store WHERE key='memecoin_scan_cache'"
            ).fetchone()
            if row:
                return json.loads(row["value"])
    except Exception:
        pass
    return []


def cache_signals(signals: list):
    """Write scan results to kv_store and log each signal to memecoin_signal_outcomes."""
    try:
        from utils.db import get_conn
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        with get_conn() as conn:
            conn.execute("""
                INSERT INTO kv_store (key, value) VALUES ('memecoin_scan_cache', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """, (json.dumps(signals),))

            for s in signals:
                conn.execute("""
                    INSERT OR IGNORE INTO memecoin_signal_outcomes
                        (scanned_at, symbol, mint, score, price_at_scan,
                         change_1h_at_scan, volume_24h, liquidity_usd,
                         rug_label, top_holder_pct, lp_locked_pct,
                         mcap_at_scan, token_age_days, vol_acceleration,
                         mint_revoked, freeze_revoked)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    s.get("scanned_at", now_str),
                    s.get("symbol", ""),
                    s.get("mint", ""),
                    s.get("score"),
                    s.get("price"),
                    s.get("change_1h"),
                    s.get("volume_24h"),
                    s.get("liquidity_usd"),
                    s.get("rug_label", "UNKNOWN"),
                    s.get("top_holder_pct"),
                    s.get("lp_locked_pct"),
                    s.get("mcap_usd"),
                    s.get("token_age_days"),
                    s.get("vol_acceleration"),
                    int(bool(s.get("mint_revoked", False))),
                    int(bool(s.get("freeze_revoked", False))),
                ))
    except Exception:
        pass
