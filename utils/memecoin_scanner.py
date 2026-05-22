"""
Memecoin Scanner — Patch 131
Enhanced scanner: RugCheck safety, market cap, token age, volume acceleration.
Thresholds auto-tune from learned outcomes stored in kv_store.
Results cached in kv_store['memecoin_scan_cache'].
"""

from __future__ import annotations

import json
import logging
import math
import os
import time
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta

from utils.trade_quality import assess_trade_quality, assess_trade_quality_from_snapshot

log = logging.getLogger(__name__)


def _independent_source_mode() -> bool:
    raw = f"{os.getenv('NO_BIRDEYE_MODE', '')},{os.getenv('INDEPENDENT_SOURCE_MODE', '')}".lower()
    return any(value.strip() in {"1", "true", "yes", "on"} for value in raw.split(","))


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
RUGCHECK_EMPTY_TTL_S = 180    # 3 min — empty/unknown responses are often transient

# Patch 249: discovery ingress gate thresholds
# Calibrated for catch-early ingress — intentionally permissive vs scanner thresholds.
# vol_h24 used (not h1) — h1 is 0 for coins with no trade in the last 60 min.
# Patch 253: raised DISC_LIQ_MIN from 3_000 → 15_000 (cuts sub-10k micro/decay bucket).
#            added DISC_VOL_LIQ_RATIO_MIN: vol/liq floor filters residual-LP dying tokens.
DISC_LIQ_MIN            = 15_000       # real LP floor — eliminates micro/decay entries
DISC_LIQ_MAX            = 2_000_000    # exclude already-established tokens
DISC_VOL_H24_MIN        = 100          # minimal 24h activity — confirms pair is alive
DISC_VOL_LIQ_RATIO_MIN  = 1.5          # vol/liq floor — distinguishes active from fading
DISC_AGE_MAX_DAYS       = 30.0         # 30 days max — no minimum (liq floor does that work)
DISC_FDV_MIN            = 10_000       # not microscopic
DISC_FDV_MAX            = 100_000_000  # not mega-cap
DISC_FLOOD_H            = 8.0          # re-ingest cooldown (matches lifecycle GAP_THRESHOLD_H)

# Patch 254: DexScreener top-boosted endpoint — free, no API key, one call per cycle.
# /token-boosts/top/v1  = ranked by cumulative spend (active projects, consistent with
# narrative_momentum.py).  /active/v1 does not exist (404).
_DEXSCREENER_BOOSTS_URL = f"{DEXSCREENER_BASE}/token-boosts/top/v1"
DEXSCREENER_PROVIDER = "dexscreener"
DEXSCREENER_COOLDOWN_SECONDS = max(300, int(os.getenv("DEXSCREENER_COOLDOWN_SECONDS", "900")))

# Patch 252: GeckoTerminal discovery source — creation-time-ordered, name-agnostic
_GECKO_BASE    = "https://api.geckoterminal.com/api/v2"
_GECKO_HEADERS = {
    "Accept":     "application/json;version=20230302",
    "User-Agent": "memecoin-engine/1.0",
}


def _dex_provider_in_cooldown() -> tuple[bool, dict]:
    try:
        from utils.db import provider_in_cooldown
        return provider_in_cooldown(DEXSCREENER_PROVIDER)
    except Exception:
        return False, {}


def _mark_dex_provider_degraded(reason: str, detail: str | None = None) -> dict:
    try:
        from utils.db import mark_provider_degraded
        return mark_provider_degraded(
            DEXSCREENER_PROVIDER,
            cooldown_seconds=DEXSCREENER_COOLDOWN_SECONDS,
            reason=reason,
            detail=detail,
        )
    except Exception:
        return {}


def _mark_dex_provider_active(detail: str | None = None) -> dict:
    try:
        from utils.db import mark_provider_active
        return mark_provider_active(DEXSCREENER_PROVIDER, detail=detail)
    except Exception:
        return {}


def _current_dex_provider_status() -> dict:
    try:
        from utils.db import get_provider_status
        return dict(get_provider_status(DEXSCREENER_PROVIDER) or {})
    except Exception:
        return {}


def _dex_search_pairs(query: str, *, reason: str) -> list[dict]:
    try:
        from data.dexscreener import fetch_search_pairs  # type: ignore

        return fetch_search_pairs(query, reason=reason, provider=DEXSCREENER_PROVIDER)
    except Exception:
        return []


def _dex_token_pairs(mints, *, reason: str) -> list[dict]:
    try:
        from data.dexscreener import fetch_token_pairs  # type: ignore

        return fetch_token_pairs(mints, reason=reason, provider=DEXSCREENER_PROVIDER)
    except Exception:
        return []


def _dex_latest_profiles(*, reason: str) -> list[dict]:
    try:
        from data.dexscreener import fetch_latest_profiles  # type: ignore

        return fetch_latest_profiles(reason=reason, provider=DEXSCREENER_PROVIDER)
    except Exception:
        return []


def _dex_token_boosts(kind: str, *, reason: str) -> list[dict]:
    try:
        from data.dexscreener import fetch_token_boosts  # type: ignore

        return fetch_token_boosts(kind, reason=reason, provider=DEXSCREENER_PROVIDER)
    except Exception:
        return []


# ── Default thresholds (overridden by learned values in kv_store) ─────────────

DEFAULT_THRESHOLDS: dict = {
    "min_volume_24h":       15_000,   # modestly looser floor for current dry regime; keeps out dead pairs while restoring scanner flow
    "min_liquidity_usd":     7_500,   # modestly looser LP floor; still avoids true micro-liquidity noise
    "min_price_change_1h":   3.0,     # min 1h price change % (was 5.0 — catches earlier momentum)
    "min_mcap":          1_500_000,   # min market cap USD  (Patch 276: raised from 300k — sub-1.5M bucket avg -6.1% 4h, 27% win rate)
    "max_mcap":         50_000_000,   # max market cap USD  (skip already-mooned)
    "min_age_days":          1.0,     # skip brand new (bundle risk)
    "max_age_days":         30.0,     # skip stale (momentum window closed)
    "min_vol_acceleration":  3.0,     # h1_vol >= 3% of h24_vol (was 5.0 — still filters noise)
    "max_top_holder_pct":    5.0,     # skip if single wallet holds >5% (Patch 216 — attribution data: 5-8% bucket = 11.9% WR, -23.6% avg, 59% cat)
    "allow_warn":            True,    # show WARN signals; filter only DANGER/RUGGED
    "top_n":                10,
    # Patch 131 — overextension gates (data-driven from 342 4h outcomes)
    "max_1h_change_pct":    20.0,     # skip already-pumped tokens (>20% 1h → 0% win rate)
    "max_vol_liq_ratio":    15.0,     # skip thinly-traded / wash-traded (vol/liq > 15x → 27% win rate)
}


def _prefilter_metrics(pair: dict, now_ts: float) -> dict:
    vol = float(pair.get("volume", {}).get("h24", 0) or 0)
    liq = float(pair.get("liquidity", {}).get("usd", 0) or 0)
    chg1h = float(pair.get("priceChange", {}).get("h1", 0) or 0)
    price = float(pair.get("priceUsd", 0) or 0)
    vol_h1 = float(pair.get("volume", {}).get("h1", 0) or 0)
    fdv = float(pair.get("fdv", 0) or 0)
    mint = pair.get("baseToken", {}).get("address", "")
    symbol = str(pair.get("baseToken", {}).get("symbol") or "???").upper()
    pair_created = pair.get("pairCreatedAt")
    age_days = None
    if pair_created:
        age_days = (now_ts - pair_created / 1000) / 86400
    vol_accel = round((vol_h1 / vol * 100), 1) if vol > 0 else 0.0
    mcap = fdv or 0
    return {
        "symbol": symbol,
        "mint": mint,
        "price": round(price, 8) if price > 0 else 0.0,
        "volume_24h": round(vol, 0),
        "liquidity_usd": round(liq, 0),
        "change_1h": round(chg1h, 2),
        "mcap_usd": round(mcap, 0),
        "token_age_days": round(age_days, 2) if age_days is not None else None,
        "vol_acceleration": vol_accel,
        "_raw_volume_24h": vol,
        "_raw_liquidity_usd": liq,
        "_raw_change_1h": chg1h,
        "_raw_price": price,
        "_raw_mcap_usd": mcap,
        "_raw_age_days": age_days,
    }


def _prefilter_failures_from_metrics(metrics: dict, t: dict, vol_min: float, liq_min: float, chg_min: float) -> list[str]:
    failures: list[str] = []
    mint = str(metrics.get("mint") or "")
    price = float(metrics.get("_raw_price") or 0.0)
    vol = float(metrics.get("_raw_volume_24h") or 0.0)
    liq = float(metrics.get("_raw_liquidity_usd") or 0.0)
    chg1h = float(metrics.get("_raw_change_1h") or 0.0)
    mcap = float(metrics.get("_raw_mcap_usd") or 0.0)
    age_days = metrics.get("_raw_age_days")
    vol_accel = float(metrics.get("vol_acceleration") or 0.0)

    if not mint or price <= 0:
        failures.append("missing_mint_or_price")
    if vol < vol_min:
        failures.append("volume_below_min")
    if liq < liq_min:
        failures.append("liquidity_below_min")
    if chg1h < chg_min:
        failures.append("change_below_min")
    if chg1h > t["max_1h_change_pct"]:
        failures.append("overextended_1h")
    if liq > 0 and vol / liq > t["max_vol_liq_ratio"]:
        failures.append("vol_liq_ratio_high")
    if mcap > 0:
        if mcap < t["min_mcap"]:
            failures.append("mcap_below_min")
        if mcap > t["max_mcap"]:
            failures.append("mcap_above_max")
    if age_days is not None:
        if age_days < t["min_age_days"]:
            failures.append("age_below_min")
        if age_days > t["max_age_days"]:
            failures.append("age_above_max")
    if vol_accel < t["min_vol_acceleration"]:
        failures.append("vol_accel_below_min")
    return failures


def _classify_near_miss(metrics: dict, failures: list[str], t: dict) -> tuple[str, str | None]:
    meaningful = [f for f in failures if f != "missing_mint_or_price"]
    if not meaningful:
        return "STRUCTURAL_REJECT", None

    tolerated_one_gate = None
    if len(meaningful) == 1:
        gate = meaningful[0]
        mcap = float(metrics.get("_raw_mcap_usd") or 0.0)
        vol_accel = float(metrics.get("vol_acceleration") or 0.0)
        chg1h = float(metrics.get("_raw_change_1h") or 0.0)
        vol = float(metrics.get("_raw_volume_24h") or 0.0)
        liq = float(metrics.get("_raw_liquidity_usd") or 0.0)
        age_days = metrics.get("_raw_age_days")
        strong_base = vol >= max(50_000.0, t["min_volume_24h"] * 2) and liq >= max(15_000.0, t["min_liquidity_usd"] * 2)
        if gate == "overextended_1h" and strong_base and chg1h <= (float(t["max_1h_change_pct"]) + 12.0):
            tolerated_one_gate = "overextended_only"
        elif gate == "mcap_below_min" and mcap >= float(t["min_mcap"]) * 0.75 and strong_base:
            tolerated_one_gate = "mcap_close_only"
        elif gate == "vol_accel_below_min" and vol_accel >= float(t["min_vol_acceleration"]) * 0.7 and strong_base:
            tolerated_one_gate = "vol_accel_close_only"
        elif gate == "age_below_min" and age_days is not None and age_days >= float(t["min_age_days"]) * 0.6 and strong_base:
            tolerated_one_gate = "age_close_only"

    if tolerated_one_gate:
        return "ACCEPTABLE_NEAR_MISS", tolerated_one_gate

    structural_failures = {"missing_mint_or_price", "age_above_max", "mcap_above_max", "vol_liq_ratio_high"}
    if any(f in structural_failures for f in meaningful):
        return "STRUCTURAL_REJECT", None

    weak_failure_count = sum(
        1 for f in meaningful
        if f in {"volume_below_min", "liquidity_below_min", "change_below_min", "mcap_below_min", "vol_accel_below_min", "age_below_min"}
    )
    if weak_failure_count > 0:
        return "WEAK_NEAR_MISS", None
    return "STRUCTURAL_REJECT", None


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
                ttl_s = int(cached.get("_ttl_s") or 0) or RUGCHECK_CACHE_TTL_S
                if time.time() - cached.get("_ts", 0) < ttl_s:
                    return cached
    except Exception:
        pass
    return None


def _save_rugcheck_cache(mint: str, data: dict):
    try:
        from utils.db import get_conn
        data["_ts"] = time.time()
        if not data.get("_ttl_s"):
            _empty_unknown = (
                str(data.get("rug_label") or "UNKNOWN").upper() == "UNKNOWN"
                and float(data.get("top_holder_pct") or 0.0) <= 0.0
                and float(data.get("top5_holder_pct") or 0.0) <= 0.0
                and float(data.get("lp_locked_pct") or 0.0) <= 0.0
                and not bool(data.get("mint_revoked", False))
                and not bool(data.get("freeze_revoked", False))
                and not list(data.get("risk_names") or [])
            )
            data["_ttl_s"] = RUGCHECK_EMPTY_TTL_S if _empty_unknown else RUGCHECK_CACHE_TTL_S
        with get_conn() as conn:
            conn.execute("""
                INSERT INTO kv_store (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """, (f"rugcheck:{mint}", json.dumps(data)))
    except Exception:
        pass


def _helius_rug_fallback(mint: str) -> dict | None:
    try:
        from utils.helius import get_token_safety, is_available  # type: ignore
    except Exception:
        return None
    if not is_available():
        return None

    try:
        safety = get_token_safety(mint)
    except Exception:
        return None

    mint_error = str(safety.get("mint_error") or "")
    holder_error = str(safety.get("holder_error") or "")
    if mint_error and mint_error != "rpc_fail" and holder_error and holder_error != "rpc_fail":
        return None

    top1 = float(safety.get("top1_pct") or 0.0)
    top5 = float(safety.get("top5_pct") or 0.0)
    top10 = float(safety.get("top10_pct") or 0.0)
    mint_revoked = bool(safety.get("mint_authority_revoked", False))
    freeze_revoked = bool(safety.get("freeze_authority_revoked", False))
    grade = str(safety.get("grade") or "UNKNOWN").upper()
    flags = list(safety.get("flags") or [])

    if grade in ("DANGER", "RISKY") or not mint_revoked:
        rug_label = "DANGER"
    elif grade in ("SAFE", "CAUTION"):
        # Conservative: Helius gives concentration/authority, but not LP lock.
        rug_label = "WARN"
    else:
        rug_label = "UNKNOWN"

    if (
        top1 <= 0.0
        and top5 <= 0.0
        and top10 <= 0.0
        and not mint_revoked
        and not freeze_revoked
        and not flags
    ):
        return None

    return {
        "rug_label": rug_label,
        "top_holder_pct": round(top1, 1),
        "top5_holder_pct": round(top5, 1),
        "top10_holder_pct": round(top10, 1),
        "lp_locked_pct": 0.0,
        "mint_revoked": mint_revoked,
        "freeze_revoked": freeze_revoked,
        "insider_count": 0,
        "risk_names": flags,
        "safety_source": "HELIUS_FALLBACK",
        "_ttl_s": RUGCHECK_EMPTY_TTL_S,
    }


def _rug_check(mint: str) -> dict:
    """
    Call RugCheck API for safety enrichment. Results cached 30 min.
    Returns: rug_label, top_holder_pct, top5_holder_pct, lp_locked_pct,
             mint_revoked, freeze_revoked
    """
    cached = _load_rugcheck_cache(mint)
    if cached:
        return cached

    result = {
        "rug_label":        "UNKNOWN",
        "top_holder_pct":   0.0,
        "top5_holder_pct":  0.0,
        "top10_holder_pct": 0.0,   # Patch 214
        "lp_locked_pct":    0.0,
        "mint_revoked":     False,
        "freeze_revoked":   False,
        "insider_count":    0,     # Patch 214 — count of topHolders with insider=True
        "risk_names":       [],    # Patch 214 — RugCheck named risk strings
        "safety_source":    "RUGCHECK",
    }
    try:
        r = requests.get(
            f"{RUGCHECK_BASE}/tokens/{mint}/report",
            timeout=RUGCHECK_TIMEOUT,
            headers={"User-Agent": "memecoin-engine/1.0"},
        )
        if r.status_code != 200:
            fallback = _helius_rug_fallback(mint)
            if fallback:
                result.update(fallback)
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
            result["top_holder_pct"]   = round(float(holders[0].get("pct", 0)), 1)
            result["top5_holder_pct"]  = round(
                sum(float(h.get("pct", 0)) for h in holders[:5]), 1
            )
            # Patch 214 — top10 concentration + insider wallet count
            result["top10_holder_pct"] = round(
                sum(float(h.get("pct", 0)) for h in holders[:10]), 1
            )
            result["insider_count"]    = sum(
                1 for h in holders if h.get("insider", False)
            )

        # Patch 214 — RugCheck named risks (extracted alongside rug_label detection)
        result["risk_names"] = [r.get("name", "") for r in risks if r.get("name")]

        markets = d.get("markets", [])
        if markets:
            lp = markets[0].get("lp", {})
            result["lp_locked_pct"] = round(
                float(lp.get("lpLockedPct") or lp.get("lpBurnedPct") or 0), 1
            )

        result["mint_revoked"]   = d.get("mintAuthority")   is None
        result["freeze_revoked"] = d.get("freezeAuthority") is None

    except Exception:
        fallback = _helius_rug_fallback(mint)
        if fallback:
            result.update(fallback)

    if (
        result["rug_label"] == "UNKNOWN"
        and float(result.get("top_holder_pct") or 0.0) <= 0.0
        and float(result.get("top5_holder_pct") or 0.0) <= 0.0
        and float(result.get("lp_locked_pct") or 0.0) <= 0.0
        and not bool(result.get("mint_revoked", False))
        and not bool(result.get("freeze_revoked", False))
    ):
        fallback = _helius_rug_fallback(mint)
        if fallback:
            result.update(fallback)

    _save_rugcheck_cache(mint, result)
    return result


# ── Bundle-risk filter ────────────────────────────────────────────────────────

def _bundle_risk(rug: dict) -> tuple:
    """
    Compute bundle/manipulation risk from RugCheck holder and authority data.
    Returns (score_penalty: float, level: str, reasons: list[str]).

    Level:   BLOCK | HIGH | MEDIUM | LOW | CLEAN
    Penalty: subtracted from signal score (capped at 50 to avoid over-penalising)

    Empirically derived thresholds (from 1142 completed signal outcomes):
      top_holder_pct 5-10%  → 12% WR / -23.4% avg  (vs 55% WR / +16.9% at <5%)
      top_holder_pct 10-35% → 0% WR in 1 sample (direction clear)
      mint_revoked=False    → 25% WR / -42.5% avg   (vs 45% WR / +7.5% when revoked)

    top5_holder_pct thresholds are heuristic (not yet historically calibrated).
    """
    reasons: list = []
    penalty = 0.0

    top1  = float(rug.get("top_holder_pct")  or 0)
    top5  = float(rug.get("top5_holder_pct") or 0)
    mint_rev   = bool(rug.get("mint_revoked",   False))
    freeze_rev = bool(rug.get("freeze_revoked", False))

    # ── Hard block (return immediately, do not score) ─────────────────────────
    # top5 > 60%: top 5 wallets hold majority of supply — classic bundled launch
    if top5 > 60:
        return (100.0, "BLOCK", [f"top5 holders {top5:.0f}% (bundled)"])

    # ── Graduated penalties ───────────────────────────────────────────────────
    # top_holder_pct: empirically 5-10% → 12% WR (vs 55% at <5%)
    if top1 >= 10:
        penalty += 20
        reasons.append(f"top holder {top1:.0f}%")
    elif top1 >= 5:
        penalty += 12
        reasons.append(f"top holder {top1:.0f}%")

    # top5_holder_pct: heuristic (no historical calibration yet for this field)
    if top5 >= 40:
        penalty += 10
        reasons.append(f"top5 holders {top5:.0f}%")
    elif top5 >= 25:
        penalty += 5
        reasons.append(f"top5 holders {top5:.0f}%")

    # mint_revoked: empirically 25% WR when not revoked vs 45% when revoked
    if not mint_rev:
        penalty += 8
        reasons.append("mint not revoked")

    # freeze_revoked: accounts can be frozen — lower signal than mint
    if not freeze_rev:
        penalty += 4
        reasons.append("freeze not revoked")

    # ── Derive level ──────────────────────────────────────────────────────────
    if penalty >= 25:
        level = "HIGH"
    elif penalty >= 12:
        level = "MEDIUM"
    elif penalty > 0:
        level = "LOW"
    else:
        level = "CLEAN"

    return (min(penalty, 50.0), level, reasons)


# ── Holder Quality Engine — Patch 214 ─────────────────────────────────────────

def _holder_quality(rug: dict) -> dict:
    """
    Compute holder quality score and level from RugCheck enrichment data.
    Replaces _bundle_risk() as the active quality gate in the scan pipeline.
    _bundle_risk() is retained for reference but no longer called.

    Returns dict with:
      holder_quality_score   (0-100, higher = better supply distribution)
      holder_quality_level   (CLEAN | CAUTION | RISKY | BLOCKED)
      holder_quality_reasons (list of triggered reasons, up to 5)
      hq_penalty             (signal score deduction, 0-50, calibrated from empirical data)
      bundle_risk_level      (backward compat: CLEAN|LOW|MEDIUM|HIGH|BLOCK)
      bundle_risk_reasons    (backward compat: first 3 reasons)

    Score inputs from RugCheck:
      top_holder_pct   — % supply in single largest holder
      top5_holder_pct  — % supply in top 5 holders
      top10_holder_pct — % supply in top 10 holders (Patch 214 new)
      insider_count    — count of flagged insider wallets (Patch 214 new)
      risk_names       — RugCheck named risk strings (Patch 214 new)
      mint_revoked     — mint authority revoked
      freeze_revoked   — freeze authority revoked
      lp_locked_pct    — % LP locked/burned

    Quality score thresholds:
      75-100: CLEAN
      50-74:  CAUTION
      25-49:  RISKY
      0-24:   BLOCKED
    """
    top1         = float(rug.get("top_holder_pct")   or 0)
    top5         = float(rug.get("top5_holder_pct")  or 0)
    top10        = float(rug.get("top10_holder_pct") or 0)
    insider_n    = int(  rug.get("insider_count")    or 0)
    mint_rev     = bool( rug.get("mint_revoked",   False))
    freeze_rev   = bool( rug.get("freeze_revoked", False))
    lp_locked    = float(rug.get("lp_locked_pct")    or 0)
    risk_names   = list( rug.get("risk_names")        or [])

    # ── Hard block: top5 > 60% — classic bundled launch ──────────────────────
    if top5 > 60:
        return {
            "holder_quality_score":   0.0,
            "holder_quality_level":   "BLOCKED",
            "holder_quality_reasons": [f"top5 holders {top5:.0f}% (bundled launch)"],
            "hq_penalty":             100.0,
            "bundle_risk_level":      "BLOCK",
            "bundle_risk_reasons":    [f"top5 holders {top5:.0f}% (bundled)"],
        }

    # ── Quality score: start at 100, deduct for each risk ────────────────────
    score   = 100.0
    reasons: list = []

    # Concentration: top1 — empirically calibrated (top1 >= 10% → 0% WR)
    if top1 >= 20:
        score -= 30
        reasons.append(f"top holder {top1:.0f}% (major whale)")
    elif top1 >= 10:
        score -= 20
        reasons.append(f"top holder {top1:.0f}%")
    elif top1 >= 5:
        score -= 12
        reasons.append(f"top holder {top1:.0f}%")

    # Concentration: top5
    if top5 >= 40:
        score -= 15
        reasons.append(f"top5 holders {top5:.0f}%")
    elif top5 >= 25:
        score -= 8
        reasons.append(f"top5 holders {top5:.0f}%")
    elif top5 >= 15:
        score -= 4
        reasons.append(f"top5 {top5:.0f}%")

    # Concentration: top10 — new signal, shows depth of distribution
    if top10 >= 50:
        score -= 8
        reasons.append(f"top10 holders {top10:.0f}%")
    elif top10 >= 35:
        score -= 4
        reasons.append(f"top10 {top10:.0f}%")

    # Insider wallets — flagged by RugCheck in topHolders[n].insider
    if insider_n >= 3:
        score -= 15
        reasons.append(f"{insider_n} insider wallets")
    elif insider_n >= 1:
        score -= 8
        reasons.append(f"{insider_n} insider wallet{'s' if insider_n > 1 else ''}")

    # Authority risks — empirically calibrated
    if not mint_rev:
        score -= 8
        reasons.append("mint not revoked")
    if not freeze_rev:
        score -= 4
        reasons.append("freeze not revoked")

    # Named RugCheck risks — pick up bundled/insider signals not captured above
    _reasons_str = " ".join(reasons).lower()
    for rn in risk_names:
        rn_lower = rn.lower()
        if "bundle" in rn_lower and "bundled" not in _reasons_str:
            score -= 10
            reasons.append(f"RugCheck: {rn}")
            _reasons_str = " ".join(reasons).lower()
            break
        if "insider" in rn_lower and "insider" not in _reasons_str:
            score -= 6
            reasons.append(f"RugCheck: {rn}")
            break

    # LP lock bonus — small reward for locked/burned liquidity
    if lp_locked >= 80:
        score += 5
    elif lp_locked >= 50:
        score += 2

    # Stacking multiplier: 3+ independent risk signals compound each other
    if len(reasons) >= 3:
        score -= 10
        reasons.append("stacked risks")

    score = max(0.0, min(100.0, round(score, 1)))
    reasons = reasons[:5]  # cap display list

    # ── Level ────────────────────────────────────────────────────────────────
    if score >= 75:
        level = "CLEAN"
    elif score >= 50:
        level = "CAUTION"
    elif score >= 25:
        level = "RISKY"
    else:
        level = "BLOCKED"

    # ── hq_penalty: signal score deduction (empirically calibrated, max 50) ──
    # Kept separate from quality_score to preserve empirical signal calibration.
    # Mirrors old _bundle_risk() penalty logic plus new inputs.
    hq_pen = 0.0
    if top1 >= 10:
        hq_pen += 20
    elif top1 >= 5:
        hq_pen += 12
    if top5 >= 40:
        hq_pen += 10
    elif top5 >= 25:
        hq_pen += 5
    if not mint_rev:
        hq_pen += 8
    if not freeze_rev:
        hq_pen += 4
    # Extended: insider and top10 signals add additional penalty
    if insider_n >= 3:
        hq_pen += 10
    elif insider_n >= 1:
        hq_pen += 5
    if top10 >= 50:
        hq_pen += 4
    hq_penalty = min(50.0, round(hq_pen, 1))

    # ── Backward-compat bundle_risk fields ───────────────────────────────────
    if level == "BLOCKED":
        b_level = "HIGH"   # non-BLOCK because hard-block already returned above
    elif level == "RISKY":
        b_level = "HIGH"
    elif level == "CAUTION":
        b_level = "MEDIUM" if hq_penalty >= 12 else "LOW"
    else:
        b_level = "CLEAN"

    return {
        "holder_quality_score":   score,
        "holder_quality_level":   level,
        "holder_quality_reasons": reasons,
        "hq_penalty":             hq_penalty,
        "bundle_risk_level":      b_level,
        "bundle_risk_reasons":    reasons[:3],
    }


# ── Scoring ───────────────────────────────────────────────────────────────────

def _clamp_score(value: float) -> float:
    return round(max(0.0, min(100.0, value)), 1)


def _profit_room_label(score: float) -> str:
    if score >= 75.0:
        return "EARLY"
    if score >= 55.0:
        return "WORKABLE"
    if score >= 35.0:
        return "STRETCHED"
    return "TOO_LATE"


def _compute_profit_room_score(
    *,
    change_1h: float,
    change_24h: float,
    mcap_usd: float,
    buy_pressure: float,
    vol_acceleration: float,
    entry_window: str = "",
    move_phase: str = "",
    fuel_quality: str = "",
    first_leg_confirmed: int = 0,
    provisional_first_leg: int = 0,
    wallet_behavior_state: str = "",
    wallet_conviction_score: float = 0.0,
    large_trade_buy_count: int = 0,
    large_trade_sell_count: int = 0,
    market_quality_score: float = 0.0,
) -> tuple[float, str, list[str], dict]:
    score = 50.0
    reasons: list[str] = []
    components: dict[str, float] = {}

    if change_1h <= 6.0:
        extension_1h = 14.0
        reasons.append("short-term move still early")
    elif change_1h <= 12.0:
        extension_1h = 9.0
        reasons.append("1h extension still workable")
    elif change_1h <= 20.0:
        extension_1h = 0.0
    elif change_1h <= 35.0:
        extension_1h = -10.0
        reasons.append("1h move already stretched")
    else:
        extension_1h = -20.0
        reasons.append("1h move already very extended")
    score += extension_1h
    components["change_1h_component"] = round(extension_1h, 1)

    if change_24h <= 35.0:
        extension_24h = 10.0
    elif change_24h <= 80.0:
        extension_24h = 3.0
    elif change_24h <= 150.0:
        extension_24h = -8.0
        reasons.append("24h move is already well underway")
    elif change_24h <= 250.0:
        extension_24h = -16.0
        reasons.append("24h move is already crowded")
    else:
        extension_24h = -22.0
        reasons.append("24h move already looks late")
    score += extension_24h
    components["change_24h_component"] = round(extension_24h, 1)

    if mcap_usd < 3_000_000:
        mcap_component = 8.0
    elif mcap_usd < 10_000_000:
        mcap_component = 5.0
    elif mcap_usd < 20_000_000:
        mcap_component = 0.0
    elif mcap_usd < 40_000_000:
        mcap_component = -4.0
    else:
        mcap_component = -8.0
    if mcap_usd >= 12_000_000 and (change_1h >= 20.0 or change_24h >= 120.0):
        mcap_component -= 8.0
        reasons.append("market cap is high for the current extension")
    score += mcap_component
    components["mcap_component"] = round(mcap_component, 1)

    if 2.0 <= vol_acceleration < 8.0:
        accel_component = 4.0
    elif 8.0 <= vol_acceleration < 15.0:
        accel_component = 2.0
    elif 15.0 <= vol_acceleration < 25.0:
        accel_component = -5.0
        reasons.append("volume acceleration is getting crowded")
    elif vol_acceleration >= 25.0:
        accel_component = -10.0
        reasons.append("volume acceleration already looks euphoric")
    else:
        accel_component = 1.0
    score += accel_component
    components["vol_acceleration_component"] = round(accel_component, 1)

    if buy_pressure >= 60.0:
        pressure_component = 4.0
    elif buy_pressure >= 55.0:
        pressure_component = 2.0
    elif buy_pressure >= 48.0:
        pressure_component = 0.0
    else:
        pressure_component = -6.0
        reasons.append("buy pressure is fading")
    score += pressure_component
    components["buy_pressure_component"] = round(pressure_component, 1)

    window = str(entry_window or "").upper()
    window_component = {
        "OPEN": 6.0,
        "CLOSING": -10.0,
        "CLOSED": -18.0,
    }.get(window, 0.0)
    if window == "OPEN":
        reasons.append("entry window still open")
    elif window == "CLOSING":
        reasons.append("entry window is closing")
    elif window == "CLOSED":
        reasons.append("entry window already closed")
    score += window_component
    components["entry_window_component"] = round(window_component, 1)

    phase = str(move_phase or "").upper()
    phase_component = {
        "EARLY": 6.0,
        "IGNITION": 6.0,
        "RELOAD": 6.0,
        "MID": 1.0,
        "EXTENDED": -18.0,
        "CHURN": -12.0,
    }.get(phase, 0.0)
    if phase in ("EARLY", "IGNITION", "RELOAD"):
        reasons.append("move phase still has room")
    elif phase == "EXTENDED":
        reasons.append("move phase already extended")
    elif phase == "CHURN":
        reasons.append("move phase is losing asymmetry")
    score += phase_component
    components["move_phase_component"] = round(phase_component, 1)

    fuel = str(fuel_quality or "").upper()
    fuel_component = {
        "STRONG": 2.0,
        "MODERATE": 1.0,
        "TRAP": -12.0,
        "WEAK": -8.0,
    }.get(fuel, 0.0)
    if fuel == "STRONG":
        reasons.append("fuel still supports another leg")
    elif fuel == "TRAP":
        reasons.append("fuel quality suggests late chase risk")
    score += fuel_component
    components["fuel_quality_component"] = round(fuel_component, 1)

    leg_component = 0.0
    if int(first_leg_confirmed or 0) == 1:
        leg_component += 2.0
    elif int(provisional_first_leg or 0) == 1:
        leg_component -= 4.0
    score += leg_component
    components["leg_confirmation_component"] = round(leg_component, 1)

    wallet_state = str(wallet_behavior_state or "").upper()
    conviction = float(wallet_conviction_score or 0.0)
    wallet_component = 0.0
    if wallet_state == "ENTERING":
        wallet_component = 8.0 if conviction >= 65.0 else 5.0
        reasons.append("tracked wallets are still entering")
    elif wallet_state == "ADDING":
        wallet_component = 6.0 if conviction >= 60.0 else 3.0
        reasons.append("tracked wallets are still adding")
    elif wallet_state == "HOLDING":
        wallet_component = 1.0
    elif wallet_state == "EXITING":
        wallet_component = -12.0
        reasons.append("tracked wallets are already exiting")
    score += wallet_component
    components["wallet_behavior_component"] = round(wallet_component, 1)

    large_trade_component = 0.0
    if large_trade_buy_count > large_trade_sell_count:
        large_trade_component = 4.0 if (large_trade_buy_count - large_trade_sell_count) >= 2 else 2.0
        reasons.append("large-trade flow still leans buy-side")
    elif large_trade_sell_count > large_trade_buy_count:
        large_trade_component = -6.0 if (large_trade_sell_count - large_trade_buy_count) >= 2 else -3.0
        reasons.append("large-trade flow already leans sell-side")
    score += large_trade_component
    components["large_trade_component"] = round(large_trade_component, 1)

    mq_component = 0.0
    if market_quality_score >= 75.0:
        mq_component = 4.0
    elif 55.0 <= market_quality_score < 75.0:
        mq_component = 1.0
    elif 0.0 < market_quality_score < 45.0:
        mq_component = -8.0
        reasons.append("market quality is too thin for late chasing")
    score += mq_component
    components["market_quality_component"] = round(mq_component, 1)

    score = _clamp_score(score)
    label = _profit_room_label(score)
    return score, label, reasons[:5], components


def _compute_timing_score(
    pair: dict,
    vol_acceleration: float,
    mcap_usd: float,
    buy_pressure: float,
) -> tuple[float, dict]:
    """Score entry timing only. Safety and market structure live elsewhere."""
    chg1h = float(pair.get("priceChange", {}).get("h1", 0) or 0)
    chg24h = float(pair.get("priceChange", {}).get("h24", 0) or 0)

    if chg1h < 5:
        h1_score = 35.0
    elif chg1h < 10:
        h1_score = 30.0
    elif chg1h < 15:
        h1_score = 22.0
    elif chg1h < 20:
        h1_score = 10.0
    else:
        h1_score = 0.0

    va = vol_acceleration
    if 2 <= va < 5:
        accel_score = 25.0
    elif 5 <= va < 10:
        accel_score = 18.0
    elif 10 <= va < 20:
        accel_score = 10.0
    elif va >= 20:
        accel_score = 2.0
    else:
        accel_score = 8.0

    m = mcap_usd
    if 3_000_000 <= m < 10_000_000:
        mcap_score = 15.0
    elif 1_500_000 <= m < 3_000_000:
        mcap_score = 10.0
    elif 10_000_000 <= m < 25_000_000:
        mcap_score = 7.0
    elif 25_000_000 <= m < 50_000_000:
        mcap_score = 4.0
    else:
        mcap_score = 0.0

    if 5.0 <= chg24h <= 60.0:
        daily_shape_score = 15.0
    elif 60.0 < chg24h <= 120.0:
        daily_shape_score = 8.0
    elif 0.0 <= chg24h < 5.0:
        daily_shape_score = 7.0
    elif chg24h > 120.0:
        daily_shape_score = 0.0
    else:
        daily_shape_score = 2.0

    if buy_pressure >= 60.0:
        pressure_score = 10.0
    elif buy_pressure >= 55.0:
        pressure_score = 8.0
    elif buy_pressure >= 50.0:
        pressure_score = 5.0
    elif buy_pressure >= 45.0:
        pressure_score = 3.0
    else:
        pressure_score = 0.0

    base_timing_score = _clamp_score(h1_score + accel_score + mcap_score + daily_shape_score + pressure_score)
    profit_room_score, profit_room_label, profit_room_reasons, profit_room_components = _compute_profit_room_score(
        change_1h=chg1h,
        change_24h=chg24h,
        mcap_usd=mcap_usd,
        buy_pressure=buy_pressure,
        vol_acceleration=vol_acceleration,
    )
    timing_score = _clamp_score((base_timing_score * 0.75) + (profit_room_score * 0.25))
    return timing_score, {
        "base_timing_score": round(base_timing_score, 1),
        "h1_change_score": round(h1_score, 1),
        "vol_acceleration_score": round(accel_score, 1),
        "mcap_score": round(mcap_score, 1),
        "daily_shape_score": round(daily_shape_score, 1),
        "buy_pressure_score": round(pressure_score, 1),
        "profit_room_score": round(profit_room_score, 1),
        "profit_room_label": profit_room_label,
        "profit_room_reasons": profit_room_reasons,
        "profit_room_components": profit_room_components,
    }


def _compute_safety_score(rug: dict, holder_quality: dict) -> tuple[float, dict]:
    """Score token integrity and structural safety only."""
    rug_label = str(rug.get("rug_label") or "UNKNOWN").upper()
    holder_quality_score = float(holder_quality.get("holder_quality_score") or 0.0)
    mint_revoked = bool(rug.get("mint_revoked", False))
    freeze_revoked = bool(rug.get("freeze_revoked", False))
    lp_locked_pct = float(rug.get("lp_locked_pct") or 0.0)

    base = holder_quality_score * 0.70
    rug_adjust = {
        "GOOD": 20.0,
        "WARN": 8.0,
        "UNKNOWN": 0.0,
        "DANGER": -40.0,
        "RUGGED": -60.0,
    }.get(rug_label, -5.0)
    authority_score = (8.0 if mint_revoked else -8.0) + (4.0 if freeze_revoked else -4.0)
    if lp_locked_pct >= 90.0:
        liquidity_lock_score = 8.0
    elif lp_locked_pct >= 75.0:
        liquidity_lock_score = 5.0
    elif lp_locked_pct >= 50.0:
        liquidity_lock_score = 2.0
    else:
        liquidity_lock_score = -4.0

    safety_score = base + rug_adjust + authority_score + liquidity_lock_score
    if rug_label == "WARN":
        safety_score = min(safety_score, 78.0)
    elif rug_label == "UNKNOWN":
        safety_score = min(safety_score, 60.0)
    elif rug_label in ("DANGER", "RUGGED"):
        safety_score = min(safety_score, 20.0)

    safety_score = _clamp_score(safety_score)
    return safety_score, {
        "holder_quality_component": round(base, 1),
        "rug_label_component": round(rug_adjust, 1),
        "authority_component": round(authority_score, 1),
        "lp_lock_component": round(liquidity_lock_score, 1),
    }


def _compose_scanner_rank_score(
    timing_score: float,
    safety_score: float,
    market_quality_score: float,
    narrative_bonus: float = 0.0,
) -> float:
    score = (timing_score * 0.50) + (safety_score * 0.30) + (market_quality_score * 0.20) + narrative_bonus
    return _clamp_score(score)


def _build_score_profile(
    pair: dict,
    rug: dict,
    holder_quality: dict,
    vol_acceleration: float,
    mcap_usd: float,
) -> dict:
    buy_pressure = _pair_buy_pressure(pair)
    timing_score, timing_components = _compute_timing_score(pair, vol_acceleration, mcap_usd, buy_pressure)
    safety_score, safety_components = _compute_safety_score(rug, holder_quality)
    trade_quality = assess_trade_quality(pair, vol_acceleration, buy_pressure)
    market_quality_score = float(trade_quality["execution_quality_score"])
    profit_room_components = dict(timing_components.get("profit_room_components") or {})
    scanner_rank_score = _compose_scanner_rank_score(timing_score, safety_score, market_quality_score)
    return {
        "timing_score": timing_score,
        "safety_score": safety_score,
        "market_quality_score": market_quality_score,
        "profit_room_score": float(timing_components.get("profit_room_score") or 0.0),
        "profit_room_label": str(timing_components.get("profit_room_label") or "WORKABLE"),
        "profit_room_reasons": list(timing_components.get("profit_room_reasons") or []),
        "market_quality_verdict": trade_quality["quality_verdict"],
        "market_quality_reasons": list(trade_quality["reasons"]),
        "scanner_rank_score": scanner_rank_score,
        "buy_pressure": buy_pressure,
        "score_contract_version": 2,
        "score_components": {
            "timing": timing_components,
            "profit_room": {
                "score": float(timing_components.get("profit_room_score") or 0.0),
                "label": str(timing_components.get("profit_room_label") or "WORKABLE"),
                "reasons": list(timing_components.get("profit_room_reasons") or []),
                **profit_room_components,
            },
            "safety": safety_components,
            "market_quality": {
                **dict(trade_quality.get("components") or {}),
                "quality_verdict": trade_quality.get("quality_verdict"),
                "reasons": list(trade_quality.get("reasons") or []),
            },
        },
        "trade_quality_snapshot": trade_quality,
    }


def _score_token(
    pair: dict,
    vol_acceleration: float,
    rug_label: str,
    top_holder_pct: float = 0.0,
    mcap_usd: float = 0.0,
) -> tuple:
    """
    Backward-compatible wrapper for legacy callers.
    Returns the derived scanner rank score and buy pressure.
    """
    rug = {
        "rug_label": rug_label,
        "top_holder_pct": top_holder_pct,
        "mint_revoked": True,
        "freeze_revoked": True,
        "lp_locked_pct": 100.0,
    }
    holder_quality = {
        "holder_quality_score": 100.0 if top_holder_pct < 2.0 else 75.0 if top_holder_pct < 5.0 else 50.0 if top_holder_pct < 10.0 else 20.0
    }
    profile = _build_score_profile(pair, rug, holder_quality, vol_acceleration, mcap_usd)
    return profile["scanner_rank_score"], profile["buy_pressure"]


# ── Entry Context Classifier — Patch 222 ──────────────────────────────────────

_CONTEXT_ADJUSTMENTS: dict = {
    "EXTENDED":          -8.0,
    "DISTRIBUTION_RISK": -10.0,
    "DEAD_OR_CHOP":      -5.0,
    "PULLBACK":          +3.0,
    "TREND_CONTINUATION": 0.0,
    "LAUNCH":             0.0,
    "UNKNOWN":            0.0,
}


def _classify_entry_context(
    chg1h:        float,
    chg24h:       float,
    vol_accel:    float,
    buy_pressure: float,
    age_days:     float,
) -> dict:
    """
    Classify where a token is in its price move — context for timing, not quality.
    Uses only fields already available at scan time (no new external calls).

    Returns:
      entry_context         str   one of 7 buckets
      entry_context_score   float 0-100 rule confidence
      entry_context_reasons list  short operator-readable strings

    Buckets (evaluated in priority order):
      DISTRIBUTION_RISK — 24h run with sellers taking over this hour
      EXTENDED          — overextended short-term, chasing risk
      LAUNCH            — token fresh (1–3 days old, early discovery)
      DEAD_OR_CHOP      — low momentum, move probably already done
      PULLBACK          — strong 24h run, hourly slowing (consolidation)
      TREND_CONTINUATION— measured uptrend still running
      UNKNOWN           — true fallback only

    Score adjustments (applied by caller, capped at 0 floor):
      EXTENDED:           -8
      DISTRIBUTION_RISK: -10
      DEAD_OR_CHOP:       -5
      PULLBACK:           +3
      All others:          0

    V2 fix (Patch 223): recalibrated rules against actual scanner input ranges.
      - DEAD_OR_CHOP: widened to vol_accel < 5, chg1h < 7, chg24h < 20
        (previous < 4 / < 5 / < 15 thresholds were too tight at scanner minimums)
      - LAUNCH: age_days <= 2.0 primary gate (was 1.0 — same as min_age_days floor)
        secondary: age <= 3.5d with modest chg24h and vol_accel signal
      - PULLBACK: replaces FIRST_PULLBACK; removed chg1h < 0 requirement
        (structurally impossible: scanner min_price_change_1h = 3.0%)
        new definition: large 24h run with weak hourly = post-run consolidation
      - TREND_CONTINUATION: removed buy_pressure >= 52 hard gate
        (defaults to exactly 50.0 when DexScreener returns no txn data,
        which blocked valid classifications systematically)
        buy_pressure now used only as confidence modifier
    """

    # ── 1. DISTRIBUTION_RISK: 24h run but buying has faded this hour ─────────
    if chg24h > 40 and buy_pressure < 45 and vol_accel < 8:
        conf    = 75.0
        reasons = [f"+{chg24h:.0f}% 24h but buy pressure {buy_pressure:.0f}% — sellers active"]
        if vol_accel < 4:
            reasons.append(f"vol accel {vol_accel:.0f}% — volume collapsing")
            conf = min(92.0, conf + 10)
        return {"entry_context": "DISTRIBUTION_RISK", "entry_context_score": conf,
                "entry_context_reasons": reasons}

    # ── 2. EXTENDED: already overextended this hour — late entry risk ─────────
    if (chg24h > 80 and chg1h > 12) or (chg24h > 50 and chg1h > 15):
        conf    = 72.0
        reasons = [f"+{chg24h:.0f}% 24h / +{chg1h:.1f}% 1h — overextended"]
        if chg24h > 120 or chg1h > 18:
            reasons.append("extreme extension — late entry risk")
            conf = min(92.0, conf + 12)
        return {"entry_context": "EXTENDED", "entry_context_score": conf,
                "entry_context_reasons": reasons}

    # ── 3. LAUNCH: fresh token, early price-discovery phase ──────────────────
    # Evaluated before DEAD_OR_CHOP — fresh tokens are LAUNCH regardless of momentum.
    # Primary: <= 2.0d (scanner min_age_days=1.0, so this covers 1–2 day old tokens)
    # Secondary: <= 3.5d with modest 24h move and active volume
    if age_days <= 2.0 or (age_days <= 3.5 and chg24h < 40 and vol_accel > 8):
        conf    = 70.0
        reasons = [f"{age_days:.1f}d old — early launch phase"]
        if vol_accel > 20:
            reasons.append(f"vol accel {vol_accel:.0f}% — active market makers")
            conf = min(86.0, conf + 8)
        return {"entry_context": "LAUNCH", "entry_context_score": conf,
                "entry_context_reasons": reasons}

    # ── 4. DEAD_OR_CHOP: low momentum — move may already be done ─────────────
    # Widened: vol_accel < 5 (was < 4), chg1h < 7 (was < 5), chg24h < 20 (was < 15)
    # Evaluated after LAUNCH so fresh tokens are never misclassified as chop.
    if vol_accel < 5 and chg1h < 7 and chg24h < 20:
        conf    = 65.0
        reasons = [f"vol accel {vol_accel:.1f}% — low conviction"]
        if chg1h < 5 and chg24h < 10:
            reasons.append(f"+{chg24h:.0f}% 24h / +{chg1h:.1f}% 1h — marginal move")
            conf = min(82.0, conf + 10)
        return {"entry_context": "DEAD_OR_CHOP", "entry_context_score": conf,
                "entry_context_reasons": reasons}

    # ── 5. PULLBACK: strong 24h run, hourly momentum slowing ─────────────────
    # Replaces FIRST_PULLBACK. chg1h < 0 removed — scanner filters guarantee
    # chg1h >= 3%, so a "pullback" here means weak hourly vs large daily move.
    if chg24h > 20 and chg1h < 7 and buy_pressure >= 45:
        conf    = 68.0
        reasons = [f"+{chg24h:.0f}% 24h / +{chg1h:.1f}% 1h — post-run consolidation"]
        if buy_pressure >= 52:
            reasons.append(f"buy pressure {buy_pressure:.0f}% — buyers still present")
            conf = min(84.0, conf + 8)
        return {"entry_context": "PULLBACK", "entry_context_score": conf,
                "entry_context_reasons": reasons}

    # ── 6. TREND_CONTINUATION: measured uptrend still running ────────────────
    # buy_pressure >= 52 hard gate removed — defaults to 50.0 when no txn data,
    # which blocked valid classifications. Now used as confidence boost only.
    # chg24h > 5 (was > 10 original, lowered to avoid stranding early-breakout tokens)
    if chg24h > 5 and chg1h >= 3:
        conf    = 62.0
        reasons = [f"+{chg24h:.0f}% 24h / +{chg1h:.1f}% 1h — trend intact"]
        if vol_accel >= 10 and buy_pressure >= 52:
            reasons.append(f"vol accel {vol_accel:.0f}% + buy pressure {buy_pressure:.0f}% — strong")
            conf = min(82.0, conf + 12)
        elif vol_accel >= 15:
            reasons.append(f"vol accel {vol_accel:.0f}% — accelerating")
            conf = min(78.0, conf + 8)
        elif buy_pressure >= 55:
            reasons.append(f"buy pressure {buy_pressure:.0f}% — buyer-dominated")
            conf = min(76.0, conf + 8)
        return {"entry_context": "TREND_CONTINUATION", "entry_context_score": conf,
                "entry_context_reasons": reasons}

    # ── 7. UNKNOWN: true fallback — mixed signals ─────────────────────────────
    return {"entry_context": "UNKNOWN", "entry_context_score": 40.0,
            "entry_context_reasons": ["mixed signals — no clear phase"]}


def _synthetic_buy_pressure(
    chg1h: float,
    unique_wallet_1h_change: float | None,
    txns_h1: int,
) -> float:
    """Estimate buy pressure from BirdEye intrahour price/wallet activity."""
    bp = 50.0
    bp += max(-18.0, min(18.0, float(chg1h or 0.0) * 2.0))
    uw = float(unique_wallet_1h_change or 0.0)
    bp += max(-12.0, min(12.0, uw * 0.12))
    if int(txns_h1 or 0) <= 2:
        bp = (bp + 50.0) / 2.0
    return round(max(5.0, min(95.0, bp)), 1)


def _build_birdeye_pair_from_seed(seed: dict, overview: dict) -> tuple[dict, float]:
    """
    Build a Dex-like pair shape from BirdEye seed + overview so the existing
    scoring and entry-context code can still run when Dex detail fetches fail.
    Returns (pair_like_dict, vol_acceleration).
    """
    mint = str(seed.get("address") or "")
    symbol = str(seed.get("symbol") or "???").upper()
    source = str(seed.get("source") or "birdeye_fallback")
    price = float(seed.get("price") or 0.0)
    chg1h = float(
        overview.get("priceChange1hPercent")
        if overview.get("priceChange1hPercent") is not None
        else seed.get("change_1h") or 0.0
    )
    chg24h = float(
        overview.get("priceChange24hPercent")
        if overview.get("priceChange24hPercent") is not None
        else seed.get("change_24h") or 0.0
    )
    volume_24h = float(seed.get("volume_24h") or 0.0)
    liquidity = float(seed.get("liquidity") or 0.0)
    mcap = float(seed.get("market_cap") or seed.get("fdv") or 0.0)
    txns_h1 = int(overview.get("txns_h1") or seed.get("txns_h1") or 0)
    txns_h24 = int(overview.get("txns_h24") or seed.get("txns_h24") or 0)
    vol_accel = round((txns_h1 / txns_h24) * 100.0, 1) if txns_h24 > 0 else 0.0
    bp = _synthetic_buy_pressure(
        chg1h,
        overview.get("uniqueWallet1hChangePercent"),
        txns_h1,
    )
    buys = max(0, int(round(txns_h1 * (bp / 100.0))))
    sells = max(0, txns_h1 - buys)
    volume_h1 = volume_24h * max(0.0, min(vol_accel / 100.0, 0.5))
    pair = {
        "baseToken": {"symbol": symbol, "address": mint},
        "priceUsd": price,
        "priceChange": {"h1": chg1h, "h24": chg24h},
        "volume": {"h24": volume_24h, "h1": volume_h1},
        "liquidity": {"usd": liquidity},
        "fdv": mcap,
        "txns": {"h1": {"buys": buys, "sells": sells}},
        "url": (
            f"https://www.geckoterminal.com/solana/pools/{seed.get('pair_address')}"
            if source == "geckoterminal" and seed.get("pair_address")
            else f"https://birdeye.so/token/{mint}?chain=solana"
        ),
        # BirdEye seed payload does not expose a reliable pair-created timestamp
        # here. Seed these as mature-enough candidates so the existing prefilter
        # does not discard them as "stale unknown age" before safety/scoring can
        # judge them.
        "pairCreatedAt": int((time.time() - (2.0 * 86400.0)) * 1000),
        "syntheticSource": "GECKOTERMINAL_FALLBACK" if source == "geckoterminal" else "BIRDEYE_FALLBACK",
        "_candidate_data_source": source,
        "_candidate_freshness_state": "FALLBACK" if source == "geckoterminal" else "LIVE_OR_FALLBACK",
    }
    return pair, vol_accel


def _build_birdeye_fallback_candidates(
    seed_map: dict[str, dict],
    mints: list[str],
    *,
    t: dict,
    vol_min: float,
    liq_min: float,
    chg_min: float,
    now_ts: float,
) -> list[dict]:
    """
    Build a minimal candidate set directly from BirdEye when DexScreener pair-detail
    fetches are constrained. This keeps the existing scoring/safety pipeline intact
    while removing Dex as a single point of failure for candidate creation.
    """
    if not seed_map or not mints:
        return []

    try:
        from data.birdeye import fetch_birdeye_token_overview  # type: ignore
    except Exception as exc:
        log.warning("[SCAN] BirdEye overview helper unavailable: %s", exc)
        return []

    def _seed_rank(seed: dict) -> float:
        source = str(seed.get("source") or "")
        volume = float(seed.get("volume_24h") or 0.0)
        liquidity = float(seed.get("liquidity") or 0.0)
        change_1h = abs(float(seed.get("change_1h") or 0.0))
        change_24h = abs(float(seed.get("change_24h") or 0.0))
        mcap = float(seed.get("market_cap") or seed.get("fdv") or 0.0)
        vol_liq_ratio = (volume / liquidity) if liquidity > 0 else 0.0

        score = 0.0
        score += min(volume / 250_000.0, 18.0)
        score += min(liquidity / 125_000.0, 16.0)
        score += min(change_1h * 2.0, 18.0)
        score += min(change_24h / 8.0, 10.0)

        if source == "birdeye_market_data":
            score += 14.0
        elif source == "birdeye_smart_money":
            score += 12.0
        elif source == "birdeye_trending":
            score += 8.0
        elif source == "birdeye_scanner_seed":
            score += 6.0
        elif source == "geckoterminal":
            score += 7.0

        if mcap > 25_000_000.0:
            score -= 18.0
        elif mcap > 10_000_000.0:
            score -= 10.0

        if vol_liq_ratio > 120.0:
            score -= 22.0
        elif vol_liq_ratio > 80.0:
            score -= 14.0
        elif vol_liq_ratio > 40.0:
            score -= 6.0

        return score

    fallback_window = min(
        max(len(seed_map), 0),
        max(24, min(32, int(max(12, len(mints)) * 0.8))),
    )
    ranked_mints = sorted(
        (mint for mint in mints if mint in seed_map),
        key=lambda mint: _seed_rank(seed_map[mint]),
        reverse=True,
    )[:fallback_window]
    priority_market_mints = sorted(
        (
            mint for mint in mints
            if mint in seed_map and str(seed_map[mint].get("source") or "") == "birdeye_market_data"
        ),
        key=lambda mint: (
            abs(float(seed_map[mint].get("change_1h") or 0.0)),
            abs(float(seed_map[mint].get("change_24h") or 0.0)),
            float(seed_map[mint].get("volume_24h") or 0.0),
        ),
        reverse=True,
    )[:4]
    if priority_market_mints:
        seen_ranked: set[str] = set()
        merged_ranked: list[str] = []
        for mint in [*priority_market_mints, *ranked_mints]:
            if mint and mint not in seen_ranked:
                merged_ranked.append(mint)
                seen_ranked.add(mint)
        ranked_mints = merged_ranked[:fallback_window]
    if not ranked_mints:
        return []

    fallback_candidates: list[dict] = []
    with ThreadPoolExecutor(max_workers=6) as ex:
        fut_to_mint = {
            ex.submit(fetch_birdeye_token_overview, mint): mint for mint in ranked_mints
        }
        for fut in as_completed(fut_to_mint):
            mint = fut_to_mint[fut]
            seed = seed_map.get(mint)
            if not seed:
                continue
            try:
                overview = fut.result() or {}
            except Exception:
                overview = {}

            pair, vol_accel = _build_birdeye_pair_from_seed(seed, overview)
            metrics = _prefilter_metrics(pair, now_ts)
            failures = _prefilter_failures_from_metrics(metrics, t, vol_min, liq_min, chg_min)
            meaningful_failures = set(f for f in failures if f != "missing_mint_or_price")
            chg1h = float(pair.get("priceChange", {}).get("h1", 0.0) or 0.0)
            liq = float(pair.get("liquidity", {}).get("usd", 0.0) or 0.0)
            mcap = float(pair.get("fdv", 0.0) or 0.0)
            txns_h1 = int(overview.get("txns_h1") or 0)
            vol_liq_ratio = (
                float(pair.get("volume", {}).get("h24", 0.0) or 0.0) / liq
                if liq > 0
                else 0.0
            )

            # Premium-data carveout: BirdEye can show real live-flow names that trip
            # the legacy "vol/liq too high + still small-cap" rejects before Dex
            # detail is available. Keep this narrow and explicit so we do not loosen
            # the whole scanner globally.
            birdeye_flow_relax = (
                meaningful_failures
                and meaningful_failures.issubset({"vol_liq_ratio_high", "mcap_below_min"})
                and liq >= max(50_000.0, float(liq_min) * 4.0)
                and 0.0 < vol_liq_ratio <= 180.0
                and txns_h1 >= 1000
                and chg1h >= 3.0
                and mcap >= max(250_000.0, float(t["min_mcap"]) * 0.15)
            )
            if failures:
                if birdeye_flow_relax:
                    pair["_scanner_regime"] = "RELAXED_BIRDEYE_FLOW"
                    pair["_scanner_relaxation_reason"] = "birdeye_high_turnover_flow"
                else:
                    near_miss_class, relax_reason = _classify_near_miss(metrics, failures, t)
                    if near_miss_class != "ACCEPTABLE_NEAR_MISS":
                        continue
                    pair["_scanner_regime"] = "RELAXED_NEAR_MISS"
                    pair["_scanner_relaxation_reason"] = relax_reason
            else:
                pair["_scanner_regime"] = "NORMAL"
                pair["_scanner_relaxation_reason"] = None
            pair["_synthetic_vol_acceleration"] = vol_accel
            fallback_candidates.append(pair)

    fallback_candidates.sort(
        key=lambda pair: (
            float(pair.get("volume", {}).get("h24", 0.0) or 0.0),
            abs(float(pair.get("priceChange", {}).get("h24", 0.0) or 0.0)),
        ),
        reverse=True,
    )
    return fallback_candidates


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

    # Step 1: collect Solana mints with BirdEye as the primary seed universe.
    # DexScreener becomes a supplement only when BirdEye seed coverage is thin.
    sol_mints, seen = [], set()
    bird_tokens: list[dict] = []
    bird_seed_map: dict[str, dict] = {}
    seed_counts = {"birdeye": 0, "dexscreener": 0, "geckoterminal": 0}
    target_seed_count = min(40, max(18, int(top) * 4))
    dex_cooldown_active, dex_provider_state = _dex_provider_in_cooldown()
    had_dex_success = False

    def _add_mint(mint: str, source: str):
        if mint and mint not in seen:
            sol_mints.append(mint)
            seen.add(mint)
            seed_counts[source] = seed_counts.get(source, 0) + 1

    # Premium BirdEye seeds are optional. In independent mode we do not call them
    # at all; GeckoTerminal + DexScreener become the discovery universe.
    if _independent_source_mode():
        try:
            from utils.db import mark_provider_disabled  # type: ignore
            mark_provider_disabled(
                "birdeye",
                reason="independent_mode",
                detail="BirdEye scanner seeds skipped intentionally; using independent sources.",
            )
        except Exception:
            pass
        log.info("[SCAN] BirdEye seed fetch skipped — independent source mode active")
    else:
        try:
            from data.birdeye import (
                fetch_birdeye_market_data,
                fetch_birdeye_scanner_candidates,
                fetch_birdeye_smart_money_candidates,
                fetch_birdeye_trending_candidates,
            )  # type: ignore

            seed_limit = min(50, max(30, target_seed_count))
            bird_tokens = fetch_birdeye_scanner_candidates(
                limit=seed_limit,
                min_liquidity_usd=max(5_000.0, float(liq_min) * 0.5),
                min_volume_24h=max(10_000.0, float(vol_min) * 0.5),
                max_mcap=max(float(t["max_mcap"]), 5_000_000.0),
            )
            bird_seed_map = {
                str(token.get("address") or ""): token
                for token in bird_tokens
                if str(token.get("address") or "")
            }
            for token in bird_tokens:
                _add_mint(str(token.get("address") or ""), "birdeye")

            for token in fetch_birdeye_trending_candidates(limit=min(12, top + 6)) or []:
                mint = str(token.get("address") or "")
                if not mint:
                    continue
                bird_seed_map.setdefault(mint, token)
                _add_mint(mint, "birdeye")

            for token in fetch_birdeye_smart_money_candidates(limit=min(12, top + 6)) or []:
                mint = str(token.get("address") or "")
                if not mint:
                    continue
                bird_seed_map.setdefault(mint, token)
                _add_mint(mint, "birdeye")

            for token in fetch_birdeye_market_data() or []:
                mint = str(token.get("address") or "")
                if not mint:
                    continue
                existing = bird_seed_map.get(mint)
                if existing is None:
                    bird_seed_map[mint] = {
                        "symbol": str(token.get("symbol") or "???").upper(),
                        "address": mint,
                        "liquidity": float(token.get("liquidity") or 0.0),
                        "volume_24h": float(token.get("volume_24h") or 0.0),
                        "price": float(token.get("price") or 0.0),
                        "change_24h": float(token.get("change_24h") or 0.0),
                        "change_1h": float(token.get("change_1h") or 0.0),
                        "market_cap": float(token.get("market_cap") or token.get("fdv") or 0.0),
                        "fdv": float(token.get("fdv") or 0.0),
                        "source": "birdeye_market_data",
                    }
                else:
                    existing["symbol"] = str(token.get("symbol") or existing.get("symbol") or "???").upper()
                    existing["price"] = float(token.get("price") or existing.get("price") or 0.0)
                    existing["liquidity"] = float(token.get("liquidity") or existing.get("liquidity") or 0.0)
                    existing["volume_24h"] = float(token.get("volume_24h") or existing.get("volume_24h") or 0.0)
                    existing["change_24h"] = float(token.get("change_24h") or existing.get("change_24h") or 0.0)
                    existing["change_1h"] = float(token.get("change_1h") or existing.get("change_1h") or 0.0)
                    existing["market_cap"] = float(token.get("market_cap") or token.get("fdv") or existing.get("market_cap") or existing.get("fdv") or 0.0)
                    existing["fdv"] = float(token.get("fdv") or existing.get("fdv") or 0.0)
                    existing["source"] = "birdeye_market_data"
                _add_mint(mint, "birdeye")
        except Exception as exc:
            log.warning("[SCAN] BirdEye seed fetch failed: %s", exc)

    if len(sol_mints) < target_seed_count:
        try:
            from data.geckoterminal import fetch_geckoterminal_trending  # type: ignore

            for token in fetch_geckoterminal_trending(limit=min(30, target_seed_count)) or []:
                mint = str(token.get("address") or "")
                if not mint:
                    continue
                token["source"] = "geckoterminal"
                bird_seed_map.setdefault(mint, token)
                _add_mint(mint, "geckoterminal")
        except Exception as exc:
            log.warning("[SCAN] GeckoTerminal fallback seed fetch failed: %s", exc)

    dex_seed_cap = max(8, int(top) * 2)

    def _collect_mints(url: str):
        nonlocal dex_cooldown_active, had_dex_success
        if seed_counts.get("dexscreener", 0) >= dex_seed_cap:
            return
        if dex_cooldown_active:
            return
        try:
            if "token-profiles" in url:
                items = _dex_latest_profiles(reason="scanner_seed_profiles_429")
            elif "token-boosts/top" in url:
                items = _dex_token_boosts("top", reason="scanner_seed_boosts_top_429")
            elif "token-boosts/latest" in url:
                items = _dex_token_boosts("latest", reason="scanner_seed_boosts_latest_429")
            else:
                items = []
            if items:
                had_dex_success = True
            for p in items:
                if isinstance(p, dict) and p.get("chainId") == "solana":
                    mint = p.get("tokenAddress", "")
                    _add_mint(mint, "dexscreener")
                    if seed_counts.get("dexscreener", 0) >= dex_seed_cap:
                        break
        except Exception:
            pass

    if len(sol_mints) < target_seed_count and dex_cooldown_active:
        log.info("[SCAN] DexScreener seed supplement skipped — provider cooldown active")

    if len(sol_mints) < target_seed_count and not dex_cooldown_active:
        # Lightweight DexScreener supplement only when BirdEye did not provide enough
        # coverage. This keeps DexScreener useful without making it the primary choke
        # point for the scanner.
        try:
            pairs = _dex_search_pairs("solana", reason="scanner_search_429")
            if pairs:
                had_dex_success = True
                for pair in pairs:
                    if pair.get("chainId") == "solana":
                        mint = pair.get("baseToken", {}).get("address", "")
                        _add_mint(mint, "dexscreener")
                        if len(sol_mints) >= target_seed_count or seed_counts.get("dexscreener", 0) >= dex_seed_cap:
                            break
        except Exception:
            pass

    if len(sol_mints) < target_seed_count and seed_counts.get("dexscreener", 0) < dex_seed_cap and not dex_cooldown_active:
        # Heavier DexScreener sources only as a final supplement.
        _collect_mints(f"{DEXSCREENER_BASE}/token-profiles/latest/v1")
        _collect_mints(f"{DEXSCREENER_BASE}/token-boosts/top/v1")
        _collect_mints(f"{DEXSCREENER_BASE}/token-boosts/latest/v1")

    # Cap at 60 unique mints — enough breadth for the scanner while keeping
    # downstream DexScreener pair-detail calls within a safer request budget.
    sol_mints = sol_mints[:60]

    log.info(
        "[SCAN] seed mints birdeye=%s gecko=%s dexscreener=%s total=%s",
        int(seed_counts.get("birdeye") or 0),
        int(seed_counts.get("geckoterminal") or 0),
        int(seed_counts.get("dexscreener") or 0),
        len(sol_mints),
    )

    if not sol_mints:
        return []

    # Step 2: fetch pair details in larger batches to reduce rate-limit pressure.
    candidates = []
    dex_detail_429 = False
    if dex_cooldown_active:
        log.info("[SCAN] DexScreener pair-detail fetch skipped — provider cooldown active")
    else:
        for i in range(0, len(sol_mints), 10):
            batch_str = ",".join(sol_mints[i:i+10])
            try:
                pairs = _dex_token_pairs(batch_str, reason="scanner_pair_detail_429")
                if not pairs:
                    continue
                had_dex_success = True
                candidates.extend(p for p in pairs if p.get("chainId") == "solana")
            except Exception:
                continue

    if dex_detail_429 and not candidates:
        log.info(
            "[SCAN] pair-detail fetch constrained by DexScreener 429 — seeds=%s (birdeye=%s dexscreener=%s)",
            len(sol_mints),
            int(seed_counts.get("birdeye") or 0),
            int(seed_counts.get("dexscreener") or 0),
        )

    if (dex_detail_429 or not candidates) and bird_seed_map:
        fallback_candidates = _build_birdeye_fallback_candidates(
            bird_seed_map,
            sol_mints,
            t=t,
            vol_min=vol_min,
            liq_min=liq_min,
            chg_min=chg_min,
            now_ts=now_ts,
        )
        if fallback_candidates:
            existing_mints = {
                str(pair.get("baseToken", {}).get("address") or "")
                for pair in candidates
            }
            appended = 0
            for pair in fallback_candidates:
                mint = str(pair.get("baseToken", {}).get("address") or "")
                if mint and mint not in existing_mints:
                    candidates.append(pair)
                    existing_mints.add(mint)
                    appended += 1
            if appended:
                log.info(
                    "[SCAN] appended %s BirdEye-native fallback candidates after Dex detail constraint",
                    appended,
                )

    if had_dex_success and not dex_cooldown_active:
        dex_provider_state = _mark_dex_provider_active("scanner_requests_ok") or dex_provider_state

    # Step 3: apply quality + range filters
    pre_filtered: dict = {}   # mint → (pair, mcap, age_days, vol_accel, scanner_regime, scanner_relaxation_reason)
    for pair in candidates:
        metrics = _prefilter_metrics(pair, now_ts)
        mint = str(metrics.get("mint") or "")
        vol = float(metrics.get("_raw_volume_24h") or 0.0)
        mcap = float(metrics.get("_raw_mcap_usd") or 0.0)
        age_days = metrics.get("_raw_age_days")
        if age_days is None:
            age_days = 9999.0
        vol_accel = float(
            pair.get("_synthetic_vol_acceleration")
            if pair.get("_synthetic_vol_acceleration") is not None
            else metrics.get("vol_acceleration") or 0.0
        )
        failures = _prefilter_failures_from_metrics(metrics, t, vol_min, liq_min, chg_min)
        scanner_regime = str(pair.get("_scanner_regime") or "NORMAL")
        scanner_relaxation_reason = pair.get("_scanner_relaxation_reason")
        if failures:
            near_miss_class, relax_reason = _classify_near_miss(metrics, failures, t)
            if near_miss_class == "ACCEPTABLE_NEAR_MISS":
                scanner_regime = "RELAXED_NEAR_MISS"
                scanner_relaxation_reason = relax_reason
            else:
                continue

        # Deduplicate — keep highest-vol pair per mint
        existing = pre_filtered.get(mint)
        if existing is None or vol > float(
            existing[0].get("volume", {}).get("h24", 0) or 0
        ):
            pre_filtered[mint] = (pair, mcap, age_days, vol_accel, scanner_regime, scanner_relaxation_reason)

    if not pre_filtered and bird_seed_map:
        fallback_candidates = _build_birdeye_fallback_candidates(
            bird_seed_map,
            sol_mints,
            t=t,
            vol_min=vol_min,
            liq_min=liq_min,
            chg_min=chg_min,
            now_ts=now_ts,
        )
        if fallback_candidates:
            log.info(
                "[SCAN] using %s BirdEye-native fallback candidates after prefilter collapse",
                len(fallback_candidates),
            )
            for pair in fallback_candidates:
                metrics = _prefilter_metrics(pair, now_ts)
                mint = str(metrics.get("mint") or "")
                vol = float(metrics.get("_raw_volume_24h") or 0.0)
                mcap = float(metrics.get("_raw_mcap_usd") or 0.0)
                age_days = metrics.get("_raw_age_days")
                if age_days is None:
                    age_days = 9999.0
                vol_accel = float(
                    pair.get("_synthetic_vol_acceleration")
                    if pair.get("_synthetic_vol_acceleration") is not None
                    else metrics.get("vol_acceleration") or 0.0
                )
                existing = pre_filtered.get(mint)
                if existing is None or vol > float(existing[0].get("volume", {}).get("h24", 0) or 0):
                    pre_filtered[mint] = (
                        pair,
                        mcap,
                        age_days,
                        vol_accel,
                        str(pair.get("_scanner_regime") or "NORMAL"),
                        pair.get("_scanner_relaxation_reason"),
                    )

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
    post_prefilter_blocks: list[dict] = []
    bundle_blocked = 0   # count hard-blocked by bundle risk (for operator stats)
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    for mint, (pair, mcap, age_days, vol_accel, scanner_regime, scanner_relaxation_reason) in pre_filtered.items():
        rug   = rug_results.get(mint, {"rug_label": "UNKNOWN"})
        label = rug.get("rug_label", "UNKNOWN")

        if label in ("DANGER", "RUGGED"):
            post_prefilter_blocks.append({
                "symbol": pair.get("baseToken", {}).get("symbol", "???").upper(),
                "reason": f"rug_label={label}",
            })
            continue
        if not t["allow_warn"] and label == "WARN":
            post_prefilter_blocks.append({
                "symbol": pair.get("baseToken", {}).get("symbol", "???").upper(),
                "reason": "warn_filtered",
            })
            continue

        top_h = float(rug.get("top_holder_pct") or 0)
        if top_h > t["max_top_holder_pct"] and top_h > 0:
            post_prefilter_blocks.append({
                "symbol": pair.get("baseToken", {}).get("symbol", "???").upper(),
                "reason": f"top_holder_pct={top_h:.1f}>max={float(t['max_top_holder_pct']):.1f}",
            })
            continue

        # Holder quality filter: hard block or graduated score penalty (Patch 214)
        hq = _holder_quality(rug)
        b_level   = hq["bundle_risk_level"]
        b_reasons = hq["holder_quality_reasons"]
        b_penalty = hq["hq_penalty"]
        if hq["holder_quality_level"] == "BLOCKED":
            log.debug("[SCAN] %s blocked: holder quality %s", mint, b_reasons)
            bundle_blocked += 1
            post_prefilter_blocks.append({
                "symbol": pair.get("baseToken", {}).get("symbol", "???").upper(),
                "reason": "; ".join(hq["holder_quality_reasons"][:2]) or "holder_quality_blocked",
            })
            continue

        symbol  = pair.get("baseToken", {}).get("symbol", "???").upper()
        price   = float(pair.get("priceUsd", 0) or 0)
        chg1h   = float(pair.get("priceChange", {}).get("h1",  0) or 0)
        chg24h  = float(pair.get("priceChange", {}).get("h24", 0) or 0)
        vol24h  = float(pair.get("volume",      {}).get("h24", 0) or 0)
        liq     = float(pair.get("liquidity",   {}).get("usd", 0) or 0)
        dex_url = pair.get("url", f"https://dexscreener.com/solana/{mint}")
        profile = _build_score_profile(pair, rug, hq, vol_accel, mcap)
        buy_pressure = float(profile["buy_pressure"])
        timing_score = float(profile["timing_score"])
        safety_score = float(profile["safety_score"])
        market_quality_score = float(profile["market_quality_score"])
        profit_room_score = float(profile.get("profit_room_score") or 0.0)
        profit_room_label = str(profile.get("profit_room_label") or "WORKABLE")
        profit_room_reasons = list(profile.get("profit_room_reasons") or [])
        score_components = dict(profile.get("score_components") or {})

        # Narrative momentum bonus — Patch 127
        narrative_trending  = False
        narrative_sources: list = []
        narrative_bonus = 0.0
        try:
            from utils.narrative_momentum import is_trending as _is_trending  # type: ignore
            _nt = _is_trending(symbol, mint)
            if _nt["trending"]:
                narrative_trending = True
                narrative_sources  = _nt["sources"]
                narrative_bonus = float(_nt["bonus"] or 0.0)
        except Exception:
            pass

        # Entry context classification — Patch 222
        # Classifies timing phase, not token quality. Applies a modest
        # score penalty (EXTENDED/DISTRIBUTION_RISK/DEAD_OR_CHOP) or
        # bonus (FIRST_PULLBACK) so context feeds into signal ranking.
        ec = _classify_entry_context(chg1h, chg24h, vol_accel, buy_pressure, age_days)
        ctx_adj = _CONTEXT_ADJUSTMENTS.get(ec["entry_context"], 0.0)
        timing_detail = dict(score_components.get("timing") or {})
        timing_detail["context_adjustment"] = round(ctx_adj, 1)
        score_components["timing"] = timing_detail
        if ctx_adj != 0.0:
            timing_score = _clamp_score(timing_score + ctx_adj)
        profit_room_ctx_adj = {
            "PULLBACK": 6.0,
            "TREND_CONTINUATION": 1.0,
            "LAUNCH": 5.0,
            "EXTENDED": -12.0,
            "DISTRIBUTION_RISK": -14.0,
            "DEAD_OR_CHOP": -10.0,
            "UNKNOWN": 0.0,
        }.get(ec["entry_context"], 0.0)
        if profit_room_ctx_adj != 0.0:
            profit_room_score = _clamp_score(profit_room_score + profit_room_ctx_adj)
            profit_room_label = _profit_room_label(profit_room_score)
            profit_room_detail = dict(score_components.get("profit_room") or {})
            profit_room_detail["entry_context_component"] = round(profit_room_ctx_adj, 1)
            profit_room_detail["score"] = round(profit_room_score, 1)
            profit_room_detail["label"] = profit_room_label
            score_components["profit_room"] = profit_room_detail

        score = _compose_scanner_rank_score(
            timing_score=timing_score,
            safety_score=safety_score,
            market_quality_score=market_quality_score,
            narrative_bonus=narrative_bonus,
        )

        scored.append({
            "mint":                   mint,
            "symbol":                 symbol,
            "price":                  price,
            "change_1h":              round(chg1h,  2),
            "change_24h":             round(chg24h, 2),
            "volume_24h":             round(vol24h, 0),
            "liquidity_usd":          round(liq, 0),
            "mcap_usd":               round(mcap, 0),
            "token_age_days":         round(age_days, 1),
            "vol_acceleration":       vol_accel,
            "buy_pressure":           buy_pressure,
            "score":                  score,
            "scanner_rank_score":     score,
            "timing_score":           timing_score,
            "safety_score":           safety_score,
            "market_quality_score":   market_quality_score,
            "profit_room_score":      profit_room_score,
            "profit_room_label":      profit_room_label,
            "profit_room_reasons":    profit_room_reasons,
            "score_contract_version": int(profile.get("score_contract_version") or 1),
            "score_components":       score_components,
            "rug_label":              label,
            "top_holder_pct":         top_h,
            "top5_holder_pct":        float(rug.get("top5_holder_pct")  or 0),
            "lp_locked_pct":          float(rug.get("lp_locked_pct")    or 0),
            "mint_revoked":           bool(rug.get("mint_revoked",   False)),
            "freeze_revoked":         bool(rug.get("freeze_revoked", False)),
            "bundle_risk_level":      b_level,
            "bundle_risk_reasons":    b_reasons,
            # Patch 214 — Holder Quality Engine
            "holder_quality_score":   hq["holder_quality_score"],
            "holder_quality_level":   hq["holder_quality_level"],
            "holder_quality_reasons": hq["holder_quality_reasons"],
            "dex_url":                dex_url,
            "scanned_at":             now_str,
            "narrative":              narrative_trending,
            "narrative_sources":      narrative_sources,
            "narrative_bonus":        round(narrative_bonus, 1),
            # Patch 222 — Entry Context Engine
            "entry_context":          ec["entry_context"],
            "entry_context_score":    ec["entry_context_score"],
            "entry_context_reasons":  ec["entry_context_reasons"],
            "market_quality_verdict": profile.get("market_quality_verdict"),
            "market_quality_reasons": list(profile.get("market_quality_reasons") or []),
            "trade_quality_snapshot": dict(profile.get("trade_quality_snapshot") or {}),
            "scanner_regime":         scanner_regime,
            "scanner_relaxation_reason": scanner_relaxation_reason,
            "candidate_data_source":  pair.get("_candidate_data_source") or pair.get("syntheticSource") or "dexscreener",
            "candidate_freshness_state": pair.get("_candidate_freshness_state") or ("FALLBACK" if pair.get("syntheticSource") else "LIVE"),
        })

    scored.sort(key=lambda x: x["score"], reverse=True)

    # Store bundle filter stats for operator visibility (backward compat)
    bundle_penalized = sum(
        1 for s in scored if s.get("bundle_risk_level") not in ("CLEAN", None)
    )
    # Patch 214 — holder quality distribution stats
    hq_counts = {"blocked": bundle_blocked, "risky": 0, "caution": 0, "clean": 0}
    for s in scored:
        lvl = s.get("holder_quality_level", "CLEAN")
        if lvl == "RISKY":
            hq_counts["risky"]  += 1
        elif lvl == "CAUTION":
            hq_counts["caution"] += 1
        else:
            hq_counts["clean"]   += 1
    # Patch 222 — context bucket distribution (operator visibility)
    ctx_counts: dict = {}
    for s in scored:
        c = s.get("entry_context", "UNKNOWN")
        ctx_counts[c] = ctx_counts.get(c, 0) + 1

    try:
        from utils.db import get_conn as _gc
        with _gc() as _c:
            _c.execute(
                "INSERT INTO kv_store (key, value) VALUES ('memecoin_bundle_stats', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (json.dumps({
                    "blocked":   bundle_blocked,
                    "penalized": bundle_penalized,
                    "_ts":       time.time(),
                }),)
            )
            # Patch 214 — hq_stats (BLOCKED count includes pre-score hard blocks)
            _c.execute(
                "INSERT INTO kv_store (key, value) VALUES ('memecoin_hq_stats', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (json.dumps({**hq_counts, "_ts": time.time()}),)
            )
            # Patch 222 — context stats
            _c.execute(
                "INSERT INTO kv_store (key, value) VALUES ('memecoin_context_stats', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (json.dumps({**ctx_counts, "_ts": time.time()}),)
            )
    except Exception:
        pass

    if not scored and post_prefilter_blocks:
        summary = ", ".join(
            f"{item['symbol']} ({item['reason']})"
            for item in post_prefilter_blocks[:3]
        )
        log.info("[SCAN] post-prefilter blocks prevented output: %s", summary)

    return scored[:top]


def scan_trending_solana_diagnostics(
    min_volume_24h: float = None,
    min_liquidity_usd: float = None,
    min_price_change_1h: float = None,
    top_n: int = None,
) -> dict:
    """Run the scanner in diagnostics mode and return stage/filter breakdowns."""
    t       = _load_thresholds()
    vol_min = min_volume_24h      or t["min_volume_24h"]
    liq_min = min_liquidity_usd   or t["min_liquidity_usd"]
    chg_min = min_price_change_1h or t["min_price_change_1h"]
    top     = top_n               or t["top_n"]
    now_ts  = time.time()

    source_counts = {
        "profiles_latest": 0,
        "boosts_top": 0,
        "boosts_latest": 0,
        "search_trending": 0,
    }
    sol_mints, seen = [], set()
    dex_cooldown_active, dex_provider_state = _dex_provider_in_cooldown()
    had_dex_success = False

    def _collect_mints(url: str, bucket: str):
        nonlocal dex_cooldown_active, had_dex_success
        if dex_cooldown_active:
            return
        try:
            if "token-profiles" in url:
                items = _dex_latest_profiles(reason="scanner_diagnostics_profiles_429")
            elif "token-boosts/top" in url:
                items = _dex_token_boosts("top", reason="scanner_diagnostics_boosts_top_429")
            elif "token-boosts/latest" in url:
                items = _dex_token_boosts("latest", reason="scanner_diagnostics_boosts_latest_429")
            else:
                items = []
            if items:
                had_dex_success = True
            for p in items:
                if isinstance(p, dict) and p.get("chainId") == "solana":
                    mint = p.get("tokenAddress", "")
                    if mint and mint not in seen:
                        sol_mints.append(mint)
                        seen.add(mint)
                        source_counts[bucket] += 1
        except Exception:
            pass

    if not dex_cooldown_active:
        _collect_mints(f"{DEXSCREENER_BASE}/token-profiles/latest/v1", "profiles_latest")
        _collect_mints(f"{DEXSCREENER_BASE}/token-boosts/top/v1", "boosts_top")
        _collect_mints(f"{DEXSCREENER_BASE}/token-boosts/latest/v1", "boosts_latest")
        try:
            pairs = _dex_search_pairs("solana", reason="scanner_diagnostics_search_429")
            if pairs:
                had_dex_success = True
                for pair in pairs:
                    if pair.get("chainId") == "solana":
                        mint = pair.get("baseToken", {}).get("address", "")
                        if mint and mint not in seen:
                            sol_mints.append(mint)
                            seen.add(mint)
                            source_counts["search_trending"] += 1
        except Exception:
            pass

    raw_mints = len(sol_mints)
    sol_mints = sol_mints[:90]
    if not sol_mints:
        return {
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "status": "NO_SOURCE_MINTS",
            "thresholds": t,
            "source_counts": source_counts,
            "provider_status": _current_dex_provider_status(),
            "stage_counts": {
                "raw_source_mints": raw_mints,
                "batched_mints": 0,
                "pair_candidates": 0,
                "prefilter_survivors": 0,
                "rug_filtered_survivors": 0,
                "scored_survivors": 0,
                "returned": 0,
            },
            "prefilter_rejections": [],
            "safety_rejections": [],
            "top_scored": [],
        }

    candidates = []
    if not dex_cooldown_active:
        for i in range(0, len(sol_mints), 5):
            batch_str = ",".join(sol_mints[i:i+5])
            try:
                pairs = _dex_token_pairs(batch_str, reason="scanner_diagnostics_detail_429")
                if not pairs:
                    continue
                had_dex_success = True
                candidates.extend(p for p in pairs if p.get("chainId") == "solana")
            except Exception:
                continue

    if had_dex_success and not dex_cooldown_active:
        dex_provider_state = _mark_dex_provider_active("scanner_diagnostics_ok") or dex_provider_state

    prefilter_counts: dict[str, int] = {
        "missing_mint_or_price": 0,
        "volume_below_min": 0,
        "liquidity_below_min": 0,
        "change_below_min": 0,
        "overextended_1h": 0,
        "vol_liq_ratio_high": 0,
        "mcap_below_min": 0,
        "mcap_above_max": 0,
        "age_below_min": 0,
        "age_above_max": 0,
        "vol_accel_below_min": 0,
    }
    pre_filtered: dict = {}
    normal_prefilter_survivors = 0
    relaxed_prefilter_survivors = 0
    near_misses: list[dict] = []
    for pair in candidates:
        metrics = _prefilter_metrics(pair, now_ts)
        failures = _prefilter_failures_from_metrics(metrics, t, vol_min, liq_min, chg_min)
        if failures:
            near_miss_class, relax_reason = _classify_near_miss(metrics, failures, t)
            fail_set = set(failures)
            meaningful = fail_set - {"missing_mint_or_price"}
            if 0 < len(meaningful) <= 2:
                near_misses.append(
                    {
                        **metrics,
                        "near_miss_class": near_miss_class,
                        "relaxation_reason": relax_reason,
                        "failed_gates": failures,
                        "fail_count": len(failures),
                    }
                )
        mint = str(metrics.get("mint") or "")
        vol = float(metrics.get("_raw_volume_24h") or 0.0)
        mcap = float(metrics.get("_raw_mcap_usd") or 0.0)
        age_days = metrics.get("_raw_age_days")
        if age_days is None:
            age_days = 9999.0
        vol_accel = float(metrics.get("vol_acceleration") or 0.0)
        if failures:
            for gate in failures:
                if gate in prefilter_counts:
                    prefilter_counts[gate] += 1
            if near_miss_class != "ACCEPTABLE_NEAR_MISS":
                continue
            scanner_regime = "RELAXED_NEAR_MISS"
            scanner_relaxation_reason = relax_reason
            relaxed_prefilter_survivors += 1
        else:
            scanner_regime = "NORMAL"
            scanner_relaxation_reason = None
            normal_prefilter_survivors += 1

        existing = pre_filtered.get(mint)
        if existing is None or vol > float(existing[0].get("volume", {}).get("h24", 0) or 0):
            pre_filtered[mint] = (pair, mcap, age_days, vol_accel, scanner_regime, scanner_relaxation_reason)

    safety_counts: dict[str, int] = {
        "rug_danger_or_rugged": 0,
        "warn_filtered": 0,
        "top_holder_above_max": 0,
        "holder_quality_blocked": 0,
    }
    rug_results: dict = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        fut_to_mint = {ex.submit(_rug_check, mint): mint for mint in pre_filtered}
        for fut in as_completed(fut_to_mint):
            mint = fut_to_mint[fut]
            try:
                rug_results[mint] = fut.result()
            except Exception:
                rug_results[mint] = {"rug_label": "UNKNOWN"}

    scored = []
    for mint, (pair, mcap, age_days, vol_accel, scanner_regime, scanner_relaxation_reason) in pre_filtered.items():
        rug   = rug_results.get(mint, {"rug_label": "UNKNOWN"})
        label = rug.get("rug_label", "UNKNOWN")
        if label in ("DANGER", "RUGGED"):
            safety_counts["rug_danger_or_rugged"] += 1
            continue
        if not t["allow_warn"] and label == "WARN":
            safety_counts["warn_filtered"] += 1
            continue
        top_h = float(rug.get("top_holder_pct") or 0)
        if top_h > t["max_top_holder_pct"] and top_h > 0:
            safety_counts["top_holder_above_max"] += 1
            continue

        hq = _holder_quality(rug)
        if hq["holder_quality_level"] == "BLOCKED":
            safety_counts["holder_quality_blocked"] += 1
            continue

        symbol = pair.get("baseToken", {}).get("symbol", "???").upper()
        profile = _build_score_profile(pair, rug, hq, vol_accel, mcap)
        score = float(profile["scanner_rank_score"])
        buy_pressure = float(profile["buy_pressure"])
        scored.append({
            "symbol": symbol,
            "mint": mint,
            "score": score,
            "scanner_rank_score": score,
            "timing_score": float(profile["timing_score"]),
            "safety_score": float(profile["safety_score"]),
            "market_quality_score": float(profile["market_quality_score"]),
            "rug_label": label,
            "top_holder_pct": top_h,
            "vol_acceleration": vol_accel,
            "buy_pressure": buy_pressure,
            "mcap_usd": round(mcap, 0),
            "liquidity_usd": round(float(pair.get("liquidity", {}).get("usd", 0) or 0), 0),
            "scanner_regime": scanner_regime,
            "scanner_relaxation_reason": scanner_relaxation_reason,
        })

    scored.sort(key=lambda x: float(x.get("score") or 0.0), reverse=True)
    normal_returned = sum(1 for s in scored[:top] if str(s.get("scanner_regime") or "NORMAL") == "NORMAL")
    relaxed_returned = sum(1 for s in scored[:top] if str(s.get("scanner_regime") or "") == "RELAXED_NEAR_MISS")
    near_misses.sort(
        key=lambda x: (
            0 if x.get("near_miss_class") == "ACCEPTABLE_NEAR_MISS" else 1,
            int(x.get("fail_count") or 99),
            -float(x.get("volume_24h") or 0.0),
            -float(x.get("liquidity_usd") or 0.0),
            str(x.get("symbol") or ""),
        )
    )
    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "status": "OK",
        "thresholds": t,
        "source_counts": source_counts,
        "provider_status": _current_dex_provider_status(),
        "stage_counts": {
            "raw_source_mints": raw_mints,
            "batched_mints": len(sol_mints),
            "pair_candidates": len(candidates),
            "prefilter_survivors": len(pre_filtered),
            "prefilter_survivors_normal": normal_prefilter_survivors,
            "prefilter_survivors_relaxed": relaxed_prefilter_survivors,
            "rug_filtered_survivors": len(scored),
            "scored_survivors": len(scored),
            "returned": len(scored[:top]),
            "returned_normal": normal_returned,
            "returned_relaxed": relaxed_returned,
        },
        "prefilter_rejections": [
            {"key": key, "count": int(count)}
            for key, count in sorted(prefilter_counts.items(), key=lambda kv: (-kv[1], kv[0]))
            if count > 0
        ],
        "safety_rejections": [
            {"key": key, "count": int(count)}
            for key, count in sorted(safety_counts.items(), key=lambda kv: (-kv[1], kv[0]))
            if count > 0
        ],
        "near_misses": near_misses[: max(int(top), 10)],
        "top_scored": scored[:top],
    }


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


def get_last_nonempty_cached_signals(max_age_hours: float = 6.0) -> list:
    """Read the most recent non-empty scan cache snapshot within a short TTL."""
    try:
        from utils.db import get_conn
        with get_conn() as conn:
            row = conn.execute(
                "SELECT value FROM kv_store WHERE key='memecoin_scan_cache_last_nonempty'"
            ).fetchone()
            if not row or not row["value"]:
                return []
            payload = json.loads(row["value"])
            signals = payload.get("signals") or []
            saved_at = str(payload.get("saved_at") or "")
            if not signals or not saved_at:
                return []
            try:
                ts = datetime.fromisoformat(saved_at.replace("Z", "+00:00"))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                age_h = (datetime.now(timezone.utc) - ts.astimezone(timezone.utc)).total_seconds() / 3600.0
                if age_h > float(max_age_hours):
                    return []
            except Exception:
                return []
            return list(signals)
    except Exception:
        pass
    return []


def cache_signals(signals: list):
    """Write scan results to kv_store and log each signal to memecoin_signal_outcomes.

    Patch 225 — repeat-token guard:
    A given mint is only written to memecoin_signal_outcomes once per 24-hour window.
    Subsequent appearances of the same mint in the same window are suppressed from MSO
    (which feeds the WR gate, context coverage, and readiness samples) but the full
    signal list is still written to the kv_store scan cache for display.
    """
    try:
        from utils.db import get_conn
        from utils.db import get_latest_speculation_heat_snapshot
        from utils.db import record_memecoin_trade_quality_snapshots
        from utils.agent_coordinator import get_fear_greed
        from datetime import timedelta
        now_utc = datetime.now(timezone.utc)
        now_str    = now_utc.strftime("%Y-%m-%d %H:%M:%S")
        cutoff_24h = (now_utc - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")

        with get_conn() as conn:
            try:
                from utils.db import get_provider_status
                dex_provider_status = dict(get_provider_status(DEXSCREENER_PROVIDER) or {})
            except Exception:
                dex_provider_status = {}
            # Migrate: add columns if not yet present (idempotent)
            for col_def in (
                "top5_holder_pct      REAL",
                "bundle_risk_level    TEXT",
                "bundle_risk_reasons  TEXT",
                "scanner_regime       TEXT DEFAULT 'NORMAL'",
                "scanner_relaxation_reason TEXT",
                # Patch 214 — Holder Quality Engine
                "holder_quality_score   REAL",
                "holder_quality_level   TEXT",
                "holder_quality_reasons TEXT",
                # Patch 222 — Entry Context Engine
                "entry_context         TEXT DEFAULT 'UNKNOWN'",
                "entry_context_score   REAL",
                "entry_context_reasons TEXT",
                # Patch 310 — decomposed scanner score contract
                "timing_score          REAL",
                "safety_score          REAL",
                "market_quality_score  REAL",
                "scanner_rank_score    REAL",
                "score_contract_version INTEGER DEFAULT 0",
                "score_components_json TEXT",
                # Patch 223 — persist 24h change for attribution
                "change_24h_at_scan    REAL",
                # Patch 230 — richer scan-time metadata for attribution
                "buy_pressure_at_scan  REAL",
                "is_actionable_at_scan INTEGER",
                "narrative_at_scan     INTEGER",
                "mcap_bucket_at_scan   TEXT",
                # Patch 238 — Quality Lane classification
                "quality_lane          INTEGER DEFAULT 0",
                "quality_lane_reasons  TEXT",
                # Patch 301 — Predictive validation labels (written by NBA run path)
                "trust_label   TEXT",
                "triage_state  TEXT",
                # Patch D — regime / sentiment / heat context
                "regime_at_scan TEXT",
                "fear_greed_at_scan REAL",
                "heat_state_at_scan TEXT",
                "heat_score_at_scan REAL",
            ):
                try:
                    conn.execute(
                        f"ALTER TABLE memecoin_signal_outcomes ADD COLUMN {col_def}"
                    )
                except Exception:
                    pass  # column already exists

            try:
                _regime_row = conn.execute(
                    "SELECT value FROM kv_store WHERE key='last_regime_label'"
                ).fetchone()
                regime_at_scan = str((_regime_row[0] if _regime_row else "UNKNOWN") or "UNKNOWN").strip().upper()
            except Exception:
                regime_at_scan = "UNKNOWN"
            try:
                fg_snapshot = dict(get_fear_greed() or {})
                fear_greed_at_scan = float(fg_snapshot.get("value")) if fg_snapshot.get("value") is not None else None
            except Exception:
                fear_greed_at_scan = None
            try:
                heat_snapshot = dict(get_latest_speculation_heat_snapshot() or {})
                heat_state_at_scan = str(heat_snapshot.get("heat_state") or "") or None
                heat_score_at_scan = float(heat_snapshot.get("heat_score")) if heat_snapshot.get("heat_score") is not None else None
            except Exception:
                heat_state_at_scan = None
                heat_score_at_scan = None

            # ── kv_store scan cache (all signals — display is unaffected) ───────
            conn.execute("""
                INSERT INTO kv_store (key, value) VALUES ('memecoin_scan_cache', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """, (json.dumps(signals),))
            conn.execute("""
                INSERT INTO kv_store (key, value) VALUES ('memecoin_scan_cache_meta', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """, (json.dumps({
                "saved_at": now_utc.isoformat(),
                "count": len(signals),
                "provider_status": dex_provider_status,
            }),))
            if signals:
                conn.execute("""
                    INSERT INTO kv_store (key, value) VALUES ('memecoin_scan_cache_last_nonempty', ?)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """, (json.dumps({
                    "saved_at": now_utc.isoformat(),
                    "signals": signals,
                }),))

            # ── Patch 225: repeat-token guard ────────────────────────────────────
            # Fetch mints that already have an MSO row within the last 24h.
            # Mints in this set are skipped — only the FIRST appearance per 24h
            # window is written to MSO, preventing one token from dominating
            # the WR gate sample, context coverage, and readiness counts.
            # Patch 249: exclude DISCOVERY rows from suppression so scanner
            # can score discovery-ingested coins within the normal 24h window.
            existing = conn.execute(
                "SELECT DISTINCT mint FROM memecoin_signal_outcomes "
                "WHERE scanned_at >= ? AND (source IS NULL OR source != 'DISCOVERY')",
                (cutoff_24h,)
            ).fetchall()
            seen_mints = {r[0] for r in existing}

            # ── Patch 238: quality-lane 72h symbol appearance counts ─────────────
            # Used to flag first-discovery tokens (≤3 recent appearances = early stage)
            _cutoff_72h = (now_utc - timedelta(hours=72)).strftime("%Y-%m-%d %H:%M:%S")
            _sym_72h_rows = conn.execute(
                "SELECT symbol, COUNT(*) FROM memecoin_signal_outcomes "
                "WHERE scanned_at >= ? GROUP BY symbol", (_cutoff_72h,)
            ).fetchall()
            _sym_72h_counts: dict = {r[0]: r[1] for r in _sym_72h_rows}
            # Quality lane gate constants (Patch 238)
            _QL_MAX_TOP_H   = 3.0    # top_holder_pct < 3.0%
            _QL_MAX_LIQ     = 50_000 # liquidity_usd  < $50k
            _QL_MAX_APPEAR  = 3      # ≤3 appearances in last 72h

            _alert_th = int(os.getenv("ALERT_THRESHOLD", "70"))

            suppressed = 0
            trade_quality_rows: list[dict] = []
            for s in signals:
                mint = s.get("mint", "")
                _tq = dict(s.get("trade_quality_snapshot") or {})
                if not _tq:
                    _tq = assess_trade_quality_from_snapshot(
                        liquidity_usd=float(s.get("liquidity_usd") or 0.0),
                        volume_24h=float(s.get("volume_24h") or 0.0),
                        change_1h=float(s.get("change_1h") or 0.0),
                        vol_acceleration=float(s.get("vol_acceleration") or 0.0),
                        buy_pressure=float(s.get("buy_pressure") or 50.0),
                        txns_h1=int((s.get("score_components") or {}).get("market_quality", {}).get("txns_h1") or 0),
                    )
                trade_quality_rows.append({
                    "mint": mint,
                    "symbol": s.get("symbol", ""),
                    "ts_utc": s.get("scanned_at", now_utc.isoformat()),
                    "liquidity_quality_score": _tq.get("liquidity_quality_score"),
                    "trade_quality_score": _tq.get("trade_quality_score"),
                    "market_integrity_score": _tq.get("market_integrity_score"),
                    "execution_quality_score": _tq.get("execution_quality_score"),
                    "quality_verdict": _tq.get("quality_verdict"),
                    "reasons": _tq.get("reasons"),
                    "inputs": _tq.get("inputs"),
                })
                if mint and mint in seen_mints:
                    suppressed += 1
                    continue  # suppress: already logged within 24h
                _mcap    = s.get("mcap_usd") or 0
                _mcap_bkt = (
                    "micro"      if _mcap < 1_500_000  else   # Patch 278: aligned with whale_watch MC_MIN_USD
                    "sweet_spot" if _mcap < 50_000_000 else
                    "mid"        if _mcap < 200_000_000 else
                    "large"
                )
                # ── Patch 238: quality-lane classification ───────────────────────
                _ql_top_h  = float(s.get("top_holder_pct") or 0)
                _ql_liq    = float(s.get("liquidity_usd") or 0)
                _ql_sym    = s.get("symbol", "")
                _ql_appear = _sym_72h_counts.get(_ql_sym, 0)
                _ql_fails: list = []
                if not (0 < _ql_top_h < _QL_MAX_TOP_H):
                    _ql_fails.append(f"top_h={_ql_top_h:.1f}%")
                if not (0 < _ql_liq < _QL_MAX_LIQ):
                    _ql_fails.append(f"liq=${_ql_liq:,.0f}")
                if _ql_appear > _QL_MAX_APPEAR:
                    _ql_fails.append(f"72h_app={_ql_appear}")
                _ql_pass = len(_ql_fails) == 0 and _ql_top_h > 0 and _ql_liq > 0
                conn.execute("""
                    INSERT OR IGNORE INTO memecoin_signal_outcomes
                        (scanned_at, symbol, mint, score, price_at_scan,
                         change_1h_at_scan, change_24h_at_scan, volume_24h, liquidity_usd,
                         source,
                         scanner_regime, scanner_relaxation_reason,
                         rug_label, top_holder_pct, top5_holder_pct, lp_locked_pct,
                         mcap_at_scan, token_age_days, vol_acceleration,
                         mint_revoked, freeze_revoked,
                        bundle_risk_level, bundle_risk_reasons,
                        holder_quality_score, holder_quality_level, holder_quality_reasons,
                        entry_context, entry_context_score, entry_context_reasons,
                        timing_score, safety_score, market_quality_score, scanner_rank_score,
                        score_contract_version, score_components_json,
                        regime_at_scan, fear_greed_at_scan, heat_state_at_scan, heat_score_at_scan,
                        buy_pressure_at_scan, is_actionable_at_scan,
                        narrative_at_scan, mcap_bucket_at_scan,
                        quality_lane, quality_lane_reasons)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    s.get("scanned_at", now_str),
                    s.get("symbol", ""),
                    mint,
                    s.get("score"),
                    s.get("price"),
                    s.get("change_1h"),
                    s.get("change_24h"),
                    s.get("volume_24h"),
                    s.get("liquidity_usd"),
                    "SCANNER",
                    s.get("scanner_regime", "NORMAL"),
                    s.get("scanner_relaxation_reason"),
                    s.get("rug_label", "UNKNOWN"),
                    s.get("top_holder_pct"),
                    s.get("top5_holder_pct"),
                    s.get("lp_locked_pct"),
                    s.get("mcap_usd"),
                    s.get("token_age_days"),
                    s.get("vol_acceleration"),
                    int(bool(s.get("mint_revoked", False))),
                    int(bool(s.get("freeze_revoked", False))),
                    s.get("bundle_risk_level", "CLEAN"),
                    json.dumps(s.get("bundle_risk_reasons", [])),
                    s.get("holder_quality_score"),
                    s.get("holder_quality_level"),
                    json.dumps(s.get("holder_quality_reasons", [])),
                    # Patch 223 — Entry Context Engine (fixed)
                    s.get("entry_context", "UNKNOWN"),
                    s.get("entry_context_score"),
                    json.dumps(s.get("entry_context_reasons", [])),
                    s.get("timing_score"),
                    s.get("safety_score"),
                    s.get("market_quality_score"),
                    s.get("scanner_rank_score", s.get("score")),
                    int(s.get("score_contract_version") or 0),
                    json.dumps(s.get("score_components", {})),
                    regime_at_scan,
                    fear_greed_at_scan,
                    heat_state_at_scan,
                    heat_score_at_scan,
                    # Patch 230 — richer scan-time metadata
                    s.get("buy_pressure"),
                    int((s.get("score") or 0) >= _alert_th),
                    int(bool(s.get("narrative", False))),
                    _mcap_bkt,
                    # Patch 238 — quality lane
                    int(_ql_pass),
                    json.dumps(_ql_fails),
                ))

            # Write suppression count to kv_store for operator visibility
            conn.execute("""
                INSERT INTO kv_store (key, value) VALUES ('memecoin_repeat_guard_24h', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """, (json.dumps({"suppressed": suppressed, "ts": now_str}),))
        if trade_quality_rows:
            try:
                record_memecoin_trade_quality_snapshots(trade_quality_rows)
            except Exception:
                pass

    except Exception:
        pass


def refresh_signal_cache(
    *,
    min_volume_24h: float | None = None,
    min_liquidity_usd: float | None = None,
    min_price_change_1h: float | None = None,
    top_n: int | None = None,
) -> list:
    """Run the scanner and refresh kv_store cache in one call."""
    signals = scan_trending_solana(
        min_volume_24h=min_volume_24h,
        min_liquidity_usd=min_liquidity_usd,
        min_price_change_1h=min_price_change_1h,
        top_n=top_n,
    )
    cache_signals(signals)
    return signals


# ── Patch 254: Attention layer helpers ───────────────────────────────────────

def _fetch_boost_mints() -> dict:
    """
    Patch 254 — Fetch currently-active DexScreener token boosts.

    Returns dict: mint → {"has_twitter": bool, "has_telegram": bool, "has_website": bool}
    for Solana tokens only.  One HTTP call per discovery cycle, not per token.

    A boosted token = someone paid real money to promote it right now.
    The boost response also carries social links, making it a two-for-one:
    boost presence AND infrastructure evidence for boosted tokens.

    Free tier, no API key required.
    """
    result: dict = {}
    try:
        for item in _dex_token_boosts("top", reason="attention_boosts_top_429"):
            if (item.get("chainId") or "") != "solana":
                continue
            mint = item.get("tokenAddress", "")
            if not mint:
                continue
            links = item.get("links") or []
            result[mint] = {
                "has_twitter":  any((l.get("type") or "").lower() == "twitter"  for l in links),
                "has_telegram": any((l.get("type") or "").lower() == "telegram" for l in links),
                "has_website":  any(
                    (l.get("label") or "").lower() == "website"
                    or (l.get("type") or "").lower() == "website"
                    # bare links (url only, no type/label) that aren't a known social
                    or (
                        l.get("url")
                        and (l.get("type") or "").lower() not in ("twitter", "telegram", "discord")
                        and (l.get("label") or "").lower() not in ("twitter", "telegram", "discord")
                        and not (l.get("type") or l.get("label"))
                    )
                    for l in links
                ),
            }
    except Exception as exc:
        log.warning("[DISCOVERY] boost fetch failed: %s", exc)
    log.debug("[DISCOVERY] boosts: %d Solana boosted tokens", len(result))
    return result


def _extract_infra(pair: dict, boost_links: dict | None) -> str:
    """
    Patch 254 — Classify social link presence as PRESENT / PARTIAL / ABSENT.

    Priority order:
      1. pair's own `info` field (present in DexScreener search results)
      2. boost metadata (present when token is actively boosted)
      3. ABSENT (GeckoTerminal pairs have no social metadata in normalized dict)

    Scoring: twitter + telegram + website each count as 1 link.
      ≥2 links → PRESENT
       1 link  → PARTIAL
       0 links → ABSENT
    """
    # DexScreener pair's info field (richest source — directly from pair response)
    info = pair.get("info") or {}
    if info:
        socials  = info.get("socials")  or []
        websites = info.get("websites") or []
        has_twitter  = any((s.get("type") or "").lower() == "twitter"  for s in socials)
        has_telegram = any((s.get("type") or "").lower() == "telegram" for s in socials)
        has_website  = bool(websites)
    elif boost_links:
        # Boost metadata as fallback (only available for boosted tokens)
        has_twitter  = boost_links.get("has_twitter",  False)
        has_telegram = boost_links.get("has_telegram", False)
        has_website  = boost_links.get("has_website",  False)
    else:
        return "ABSENT"

    link_count = sum([has_twitter, has_telegram, has_website])
    if link_count >= 2:
        return "PRESENT"
    if link_count == 1:
        return "PARTIAL"
    return "ABSENT"


def _derive_attention_quality(infra: str, boost: bool) -> str:
    """
    Patch 254 — Deterministic attention_quality from infrastructure + boost.

    STRONG   = infrastructure PRESENT  + boosted
    MODERATE = infrastructure PRESENT  (not boosted)
               OR infrastructure PARTIAL + boosted
    WEAK     = infrastructure PARTIAL  (not boosted)
    NONE     = infrastructure ABSENT   (regardless of boost — no community = no attention)
    """
    if infra == "PRESENT" and boost:    return "STRONG"
    if infra == "PRESENT":              return "MODERATE"
    if infra == "PARTIAL" and boost:    return "MODERATE"
    if infra == "PARTIAL":              return "WEAK"
    return "NONE"


def _safe_pair_change(pair: dict, key: str) -> float | None:
    """Best-effort price-change extractor for mixed DexScreener/Gecko discovery rows."""
    try:
        price_change = pair.get("priceChange") or {}
        raw = price_change.get(key)
        if raw is None:
            return None
        return float(raw)
    except Exception:
        return None


def _pair_buy_pressure(pair: dict) -> float:
    """Use DexScreener txn splits when present; otherwise stay neutral."""
    try:
        txns_h1 = (pair.get("txns") or {}).get("h1") or {}
        buys = int(txns_h1.get("buys", 0) or 0)
        sells = int(txns_h1.get("sells", 0) or 0)
        total = buys + sells
        return round((buys / total) * 100.0, 1) if total > 0 else 50.0
    except Exception:
        return 50.0


def _score_discovery_ingress(
    *,
    liquidity_usd: float,
    volume_24h: float,
    fdv: float,
    age_days: float | None,
    attention_infrastructure: str,
    boost_active: int,
    attention_quality: str,
    change_1h: float | None = None,
    change_24h: float | None = None,
    buy_pressure: float = 50.0,
) -> tuple[float, float, dict]:
    """
    Conservative structural score for discovery ingress rows.

    This is intentionally lighter-weight than the full scanner score. The goal is
    to keep discovery names rankable and learnable instead of writing inert
    score=0 rows, while avoiding accidental treatment as full proof-quality hits.
    """
    activity_ratio = round(volume_24h / liquidity_usd, 2) if liquidity_usd > 0 else 0.0

    score = 14.0  # structural base once the ingress gate has already admitted the pair
    score += min(liquidity_usd / 40_000.0, 12.0)

    if activity_ratio >= 6.0:
        score += 18.0
    elif activity_ratio >= 4.0:
        score += 14.0
    elif activity_ratio >= 2.5:
        score += 10.0
    elif activity_ratio >= 1.5:
        score += 6.0

    if 1_500_000 <= fdv < 3_000_000:
        score += 8.0
    elif 3_000_000 <= fdv < 10_000_000:
        score += 12.0
    elif 10_000_000 <= fdv < 25_000_000:
        score += 8.0
    elif 25_000_000 <= fdv < 50_000_000:
        score += 4.0

    if age_days is not None:
        if age_days <= 2.0:
            score += 12.0
        elif age_days <= 7.0:
            score += 8.0
        elif age_days <= 21.0:
            score += 4.0

    if attention_quality == "STRONG":
        score += 10.0
    elif attention_quality == "MODERATE":
        score += 6.0
    elif attention_quality == "WEAK":
        score += 2.0

    if boost_active == 1:
        score += 6.0

    if attention_infrastructure == "PRESENT":
        score += 5.0
    elif attention_infrastructure == "PARTIAL":
        score += 2.0

    if change_24h is not None:
        if 5.0 <= change_24h <= 60.0:
            score += 4.0
        elif change_24h >= 120.0:
            score -= 4.0

    if change_1h is not None:
        if change_1h < 5.0:
            score += 4.0
        elif change_1h < 15.0:
            score += 2.0
        elif change_1h >= 25.0:
            score -= 4.0

    entry_context = {
        "entry_context": "UNKNOWN",
        "entry_context_score": None,
        "entry_context_reasons": [],
    }
    if age_days is not None and (change_1h is not None or change_24h is not None):
        entry_context = _classify_entry_context(
            float(change_1h or 0.0),
            float(change_24h or 0.0),
            activity_ratio,
            buy_pressure,
            float(age_days),
        )
        # Discovery context is rougher than scanner context, so only apply half-strength.
        ctx_adj = _CONTEXT_ADJUSTMENTS.get(entry_context["entry_context"], 0.0) * 0.5
        if ctx_adj != 0.0:
            score = max(0.0, min(100.0, round(score + ctx_adj, 1)))

    return round(max(0.0, min(score, 100.0)), 1), activity_ratio, entry_context


def backfill_recent_discovery_scores(lookback_hours: int = 168, limit: int = 300) -> int:
    """Repair recent discovery rows that were written without usable score/context fields."""
    from utils.db import get_conn  # type: ignore

    updated = 0
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=max(int(lookback_hours), 1))).strftime("%Y-%m-%d %H:%M:%S")

    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT
                id,
                liquidity_usd,
                volume_24h,
                COALESCE(mcap_at_scan, 0.0) AS mcap_at_scan,
                token_age_days,
                COALESCE(attention_infrastructure, 'ABSENT') AS attention_infrastructure,
                COALESCE(boost_active, 0) AS boost_active,
                COALESCE(attention_quality, 'NONE') AS attention_quality,
                change_1h_at_scan,
                change_24h_at_scan,
                COALESCE(buy_pressure_at_scan, 50.0) AS buy_pressure_at_scan,
                score,
                vol_acceleration,
                entry_context,
                entry_context_score,
                entry_context_reasons
            FROM memecoin_signal_outcomes
            WHERE source = 'DISCOVERY'
              AND scanned_at >= ?
              AND (
                    score IS NULL
                 OR score = 0
                 OR vol_acceleration IS NULL
                 OR entry_context IS NULL
                 OR entry_context = ''
                 OR entry_context = 'UNKNOWN'
              )
            ORDER BY scanned_at DESC
            LIMIT ?
            """,
            (cutoff, max(int(limit), 1)),
        ).fetchall()

        for row in rows:
            rec = dict(row)
            score, activity_ratio, entry_context = _score_discovery_ingress(
                liquidity_usd=float(rec.get("liquidity_usd") or 0.0),
                volume_24h=float(rec.get("volume_24h") or 0.0),
                fdv=float(rec.get("mcap_at_scan") or 0.0),
                age_days=float(rec["token_age_days"]) if rec.get("token_age_days") is not None else None,
                attention_infrastructure=str(rec.get("attention_infrastructure") or "ABSENT"),
                boost_active=int(rec.get("boost_active") or 0),
                attention_quality=str(rec.get("attention_quality") or "NONE"),
                change_1h=float(rec["change_1h_at_scan"]) if rec.get("change_1h_at_scan") is not None else None,
                change_24h=float(rec["change_24h_at_scan"]) if rec.get("change_24h_at_scan") is not None else None,
                buy_pressure=float(rec.get("buy_pressure_at_scan") or 50.0),
            )

            conn.execute(
                """
                UPDATE memecoin_signal_outcomes
                SET
                    score = CASE WHEN score IS NULL OR score = 0 THEN ? ELSE score END,
                    vol_acceleration = COALESCE(vol_acceleration, ?),
                    entry_context = CASE
                        WHEN entry_context IS NULL OR entry_context = '' OR entry_context = 'UNKNOWN'
                        THEN ?
                        ELSE entry_context
                    END,
                    entry_context_score = COALESCE(entry_context_score, ?),
                    entry_context_reasons = CASE
                        WHEN entry_context_reasons IS NULL OR entry_context_reasons = '' OR entry_context_reasons = '[]'
                        THEN ?
                        ELSE entry_context_reasons
                    END
                WHERE id = ?
                """,
                (
                    score,
                    activity_ratio,
                    entry_context.get("entry_context", "UNKNOWN"),
                    entry_context.get("entry_context_score"),
                    json.dumps(entry_context.get("entry_context_reasons") or []),
                    int(rec["id"]),
                ),
            )
            updated += 1

    if updated:
        log.info("[DISCOVERY] backfilled scores/context on %d recent rows", updated)
    return updated


# ── Patch 249: Broader discovery ingress ─────────────────────────────────────

def _fetch_geckoterminal_new_pools() -> list:
    """
    Patch 252: Fetch recently-created Solana pools from GeckoTerminal.

    Source: GET /api/v2/networks/solana/new_pools?page={1,2}
    Pages 1-2 = ~40 pools, creation-time ordered newest-first.
    Free tier, no API key required.

    Why this matters: creation-time ordering gives us pools in the 1-12h
    post-launch window independent of name/boost/trending visibility — the
    exact gap the keyword-query approach cannot cover.

    Returns pair-like dicts normalized to the same shape ingest_discovery_candidates()
    already reads from DexScreener, so the ingress gate runs unchanged:
        baseToken     : {address: mint, symbol: str}
        liquidity     : {usd: float}
        volume        : {h24: float}
        fdv           : float
        pairCreatedAt : int | None   (Unix milliseconds, matching DexScreener)
        chainId       : "solana"
    """
    try:
        from data.geckoterminal import fetch_geckoterminal_new_pools  # type: ignore

        results = fetch_geckoterminal_new_pools(pages=(1, 2), limit=40)
    except Exception as exc:
        log.warning("[DISCOVERY] GeckoTerminal new_pools fetch failed: %s", exc)
        results = []

    log.debug("[DISCOVERY] GeckoTerminal: %d pools fetched (pages 1-2)", len(results))
    return results


def _fetch_discovery_pairs() -> list:
    """
    Fetch broad Solana DEX pairs via multiple DexScreener search queries.
    Deduplicates by baseToken address (mint). Returns raw pair dicts.

    Patch 249: broader discovery universe via 4 search queries chosen for
    unique Solana pair coverage — independent of the scanner's own queries
    (solana, pump, profiles, boosts).

    Query selection rationale (empirical): meme, ai, dog, cat collectively
    surface ~50-60 unique Solana pairs per run vs scanner's ~5-8 from ?q=solana.
    These queries capture meme/ai narrative tokens and subcategories (cat/dog
    memecoins) that often don't appear in the scanner's trending feeds.

    This is a v1 improvement over the narrow legacy universe, not full coverage.
    Patch 252 adds GeckoTerminal new_pools for broader creation-time coverage.
    """
    # 5 queries chosen for maximum unique Solana pair coverage per run,
    # minimising overlap with the scanner's own sources (solana, pump, profiles, boosts).
    # Empirically: "new" alone adds ~16 unique pairs; "inu"/"coin"/"meme"/"ai" together
    # add another ~50+ unique pairs for ~70 total unique Solana candidates per cycle.
    _DISC_QUERIES = ("new", "inu", "meme", "ai", "coin")
    seen_mints: set = set()
    results:    list = []
    dex_cooldown_active, _provider_status = _dex_provider_in_cooldown()

    if dex_cooldown_active:
        log.info("[DISCOVERY] DexScreener queries skipped — provider cooldown active")

    if not dex_cooldown_active:
        for q in _DISC_QUERIES:
            try:
                for pair in _dex_search_pairs(q, reason="discovery_search_429"):
                    if (pair.get("chainId") or "") != "solana":
                        continue
                    mint = (pair.get("baseToken") or {}).get("address", "")
                    if not mint or mint in seen_mints:
                        continue
                    seen_mints.add(mint)
                    results.append(pair)
            except Exception as exc:
                log.warning("[DISCOVERY] fetch failed for ?q=%s: %s", q, exc)

    # Patch 252: merge GeckoTerminal new_pools — creation-time-ordered, name-agnostic.
    # Dedup against existing results; ingress gate runs unchanged on normalized dicts.
    for pair in _fetch_geckoterminal_new_pools():
        mint = (pair.get("baseToken") or {}).get("address", "")
        if not mint or mint in seen_mints:
            continue
        seen_mints.add(mint)
        results.append(pair)

    log.debug("[DISCOVERY] total unique pairs (DSR+GT): %d", len(results))
    return results


def ingest_discovery_candidates() -> int:
    """
    Patch 249 — Lightweight discovery ingress gate.

    Fetches broad Solana pairs via _fetch_discovery_pairs(), applies structural
    filters, and writes qualifying mints to memecoin_signal_outcomes with:
        status = 'WATCH'       — distinct from PENDING/COMPLETE; ignored by
                                 outcome accounting and all readiness/trust gates
        source = 'DISCOVERY'   — traceable marker; excluded from scanner
                                 repeat-token suppression (cache_signals fix)

    Anti-flood: mint-based, not symbol-based. Skips any mint already present
    in MSO within the last DISC_FLOOD_H hours.

    Returns count of rows written.
    """
    from utils.db import get_conn  # type: ignore

    pairs = _fetch_discovery_pairs()
    if not pairs:
        return 0

    # Patch 254: fetch active boosts once per cycle (one HTTP call, not per token).
    boost_data = _fetch_boost_mints()

    now = datetime.now(timezone.utc)

    with get_conn() as conn:
        # Anti-flood: mint-based — get all mints seen in last DISC_FLOOD_H hours.
        # Using mint (not symbol) as canonical identity to avoid ticker collisions
        # suppressing valid new candidates.
        flood_cutoff = (now - timedelta(hours=DISC_FLOOD_H)).strftime("%Y-%m-%d %H:%M:%S")
        recent_mints = {
            r[0] for r in conn.execute(
                "SELECT mint FROM memecoin_signal_outcomes "
                "WHERE scanned_at > ? AND mint IS NOT NULL",
                (flood_cutoff,)
            ).fetchall()
        }

        ingested   = 0
        scanned_at = now.strftime("%Y-%m-%d %H:%M:%S")

        for pair in pairs:
            try:
                mint   = (pair.get("baseToken") or {}).get("address", "")
                symbol = (pair.get("baseToken") or {}).get("symbol",  "")
                if not mint or not symbol:
                    continue

                # Mint-first anti-flood check
                if mint in recent_mints:
                    continue

                liq_usd = float((pair.get("liquidity") or {}).get("usd") or 0)
                vol_h24 = float((pair.get("volume")    or {}).get("h24") or 0)
                fdv     = float( pair.get("fdv")  or 0)

                # pairCreatedAt is a Unix millisecond timestamp
                created_ms = pair.get("pairCreatedAt")
                age_days = (
                    (now.timestamp() - created_ms / 1000.0) / 86400.0
                    if created_ms else None
                )

                # ── Ingress gate (structural only — no deep scoring) ──────────
                # vol_h24 used (not h1) — h1 is 0 for any coin with no trade
                # in the last 60 min, which is common for emerging tokens.
                if liq_usd < DISC_LIQ_MIN:                  continue  # no real LP yet
                if liq_usd > DISC_LIQ_MAX:                  continue  # already established
                if vol_h24 < DISC_VOL_H24_MIN:              continue  # dead / no activity
                if age_days is not None and age_days > DISC_AGE_MAX_DAYS: continue  # stale
                if fdv > 0 and fdv < DISC_FDV_MIN:          continue  # microscopic
                if fdv > 0 and fdv > DISC_FDV_MAX:          continue  # mega-cap
                # Patch 253: vol/liq ratio floor — rejects residual-LP dying tokens.
                # A decaying token retains liquidity while volume collapses; ratio < 1.5
                # distinguishes active trading from passive remnant LP.
                # Only applied when liq_usd > 0 (already guaranteed by DISC_LIQ_MIN check).
                if vol_h24 / liq_usd < DISC_VOL_LIQ_RATIO_MIN: continue  # fading/decay

                # Patch 254: attention signals — computed once per admitted pair.
                _boost_links = boost_data.get(mint)          # None if not boosted
                _boost_active = 1 if _boost_links is not None else 0
                _infra   = _extract_infra(pair, _boost_links)
                _aq      = _derive_attention_quality(_infra, bool(_boost_active))
                _chg_1h = _safe_pair_change(pair, "h1")
                _chg_24h = _safe_pair_change(pair, "h24")
                _buy_pressure = _pair_buy_pressure(pair)
                _disc_score, _activity_ratio, _entry_context = _score_discovery_ingress(
                    liquidity_usd=liq_usd,
                    volume_24h=vol_h24,
                    fdv=fdv,
                    age_days=age_days,
                    attention_infrastructure=_infra,
                    boost_active=_boost_active,
                    attention_quality=_aq,
                    change_1h=_chg_1h,
                    change_24h=_chg_24h,
                    buy_pressure=_buy_pressure,
                )

                # Patch 294: capture entry price so outcome_step can compute
                # 24h returns for DISCOVERY rows — previously these were always
                # NULL and rows expired to STALE without generating learning data.
                _disc_price: float | None = None
                try:
                    _raw_p = pair.get("priceUsd")
                    if _raw_p is not None:
                        _p = float(_raw_p)
                        _disc_price = _p if _p > 0 else None
                except Exception:
                    pass

                # Write row — WATCH status, DISCOVERY source.
                # Patch 254 adds attention fields alongside existing structural fields.
                # Patch 294 adds price_at_scan for outcome fill eligibility.
                # Patch 318 adds conservative structural scoring so discovery rows
                # are rankable and learnable instead of inert score=0 watch rows.
                conn.execute("""
                    INSERT OR IGNORE INTO memecoin_signal_outcomes
                        (scanned_at, symbol, mint, score,
                         liquidity_usd, volume_24h, token_age_days,
                         status, source,
                         attention_infrastructure, boost_active, attention_quality,
                         price_at_scan, mcap_at_scan, vol_acceleration,
                         change_1h_at_scan, change_24h_at_scan, buy_pressure_at_scan,
                         entry_context, entry_context_score, entry_context_reasons)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 'WATCH', 'DISCOVERY', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (scanned_at, symbol, mint,
                      _disc_score,
                      round(liq_usd, 2),
                      round(vol_h24, 2),
                      round(age_days, 2) if age_days is not None else None,
                      _infra, _boost_active, _aq,
                      _disc_price,
                      round(fdv, 2) if fdv > 0 else None,
                      round(_activity_ratio, 2) if _activity_ratio > 0 else None,
                      round(_chg_1h, 2) if _chg_1h is not None else None,
                      round(_chg_24h, 2) if _chg_24h is not None else None,
                      _buy_pressure,
                      _entry_context.get("entry_context", "UNKNOWN"),
                      _entry_context.get("entry_context_score"),
                      json.dumps(_entry_context.get("entry_context_reasons") or [])))

                recent_mints.add(mint)  # prevent duplicates within same batch
                ingested += 1

            except Exception:
                continue

        conn.commit()

    backfill_recent_discovery_scores()

    if ingested:
        log.info("[DISCOVERY] ingested %d new candidates", ingested)
    return ingested
