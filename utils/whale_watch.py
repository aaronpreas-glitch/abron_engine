"""
Whale Watch Agent — Patch 139

Reads Moby Whale Watch Telegram channel via Telethon userbot (user's own account).
Observation mode only — no auto-buy. Logs all alerts, sends Telegram notice
when BOTH whale alert + scanner gates pass.

Setup:
  1. Run `python3 utils/whale_watch_setup.py` on VPS once (interactive auth)
  2. Set WHALE_WATCH_CHANNEL, TELEGRAM_API_ID, TELEGRAM_API_HASH in .env
  3. Restart dashboard — this agent starts automatically

DB table: whale_watch_alerts (see init_whale_watch_table below)
Outcomes tracked at 1h / 4h / 24h (same pattern as memecoin scanner)
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import Optional

log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────

def _session_file() -> str:
    root = os.path.dirname(os.path.dirname(__file__))
    return os.path.join(root, "data_storage", "whale_watch.session")


MC_MIN_USD     = 5_000_000   # $5M
MC_MAX_USD     = 50_000_000  # $50M
ACCUM_WINDOW_S = 600         # 10 min — same token 2+ buys = accumulation signal
REQUEST_TIMEOUT = 8

# ── MC tier thresholds ─────────────────────────────────────────────────────────

def _mc_tier(mc: float) -> str:
    """Classify market cap into one of 4 tiers."""
    if mc <= 0:
        return "unknown"
    if mc < 5_000_000:
        return "micro"
    if mc < 50_000_000:
        return "sweet_spot"
    if mc < 200_000_000:
        return "mid"
    return "large"

# Cross-agent targets by tier
_TIER_TARGETS = {
    "micro":      "observation",       # log only — too risky for current phase
    "sweet_spot": "memecoin_scanner",  # Phase 3: cross-confirm with memecoin scanner
    "mid":        "spot_accumulator",  # Phase 4: mid-cap basket integration
    "large":      "spot_accumulator",  # Phase 4: macro flow signal for basket
    "unknown":    "observation",
}


# ── DB ────────────────────────────────────────────────────────────────────────

def init_whale_watch_table() -> None:
    """Create whale_watch_alerts and cross_agent_signals tables if they don't exist."""
    from utils.db import get_conn
    from utils.arkham_client import ensure_arkham_tables
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS whale_watch_alerts (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                ts_utc           TEXT NOT NULL,
                raw_text         TEXT NOT NULL,
                alert_type       TEXT,           -- WHALE | KOL
                kol_name         TEXT,
                token_symbol     TEXT,
                token_mint       TEXT,           -- resolved from DexScreener
                token_pair_address TEXT,
                mint_resolution_source TEXT,
                mint_resolution_reason TEXT,
                buy_amount_usd   REAL,
                market_cap_usd   REAL,
                mc_tier          TEXT,           -- micro | sweet_spot | mid | large
                mc_in_range      INTEGER DEFAULT 0,   -- 1 if $5M–$50M
                scanner_pass     INTEGER,             -- 1 = gates pass, 0 = fail, NULL = not checked
                scanner_score    REAL,
                scanner_rug_label TEXT,
                scanner_reason   TEXT,
                alert_sent       INTEGER DEFAULT 0,
                price_at_alert   REAL,
                price_source     TEXT,
                price_resolution_reason TEXT,
                price_1h         REAL,
                price_4h         REAL,
                price_24h        REAL,
                return_1h_pct    REAL,
                return_4h_pct    REAL,
                return_24h_pct   REAL,
                outcome_status   TEXT DEFAULT 'PENDING',
                arkham_status    TEXT,
                arkham_signal_quality TEXT,
                arkham_signal_score REAL,
                arkham_entity_holder_count INTEGER,
                arkham_top_entity_name TEXT,
                arkham_top_entity_type TEXT,
                arkham_top_entity_pct_of_cap REAL,
                arkham_top_flow_entity_name TEXT,
                arkham_top_flow_entity_type TEXT,
                arkham_net_flow_usd REAL,
                arkham_enriched_at TEXT
            )
        """)
        # Upgrade path: add mc_tier column if missing (Patch 139 → 141)
        try:
            cols = [r[1] for r in conn.execute("PRAGMA table_info(whale_watch_alerts)").fetchall()]
            if "mc_tier" not in cols:
                conn.execute("ALTER TABLE whale_watch_alerts ADD COLUMN mc_tier TEXT")
            if "scanner_reason" not in cols:
                conn.execute("ALTER TABLE whale_watch_alerts ADD COLUMN scanner_reason TEXT")
            for _col, _type in [
                ("token_pair_address", "TEXT"),
                ("mint_resolution_source", "TEXT"),
                ("mint_resolution_reason", "TEXT"),
                ("price_source", "TEXT"),
                ("price_resolution_reason", "TEXT"),
            ]:
                if _col not in cols:
                    conn.execute(f"ALTER TABLE whale_watch_alerts ADD COLUMN {_col} {_type}")
            for _col, _type in [
                ("arkham_status", "TEXT"),
                ("arkham_signal_quality", "TEXT"),
                ("arkham_signal_score", "REAL"),
                ("arkham_entity_holder_count", "INTEGER"),
                ("arkham_top_entity_name", "TEXT"),
                ("arkham_top_entity_type", "TEXT"),
                ("arkham_top_entity_pct_of_cap", "REAL"),
                ("arkham_top_flow_entity_name", "TEXT"),
                ("arkham_top_flow_entity_type", "TEXT"),
                ("arkham_net_flow_usd", "REAL"),
                ("arkham_enriched_at", "TEXT"),
            ]:
                if _col not in cols:
                    conn.execute(f"ALTER TABLE whale_watch_alerts ADD COLUMN {_col} {_type}")
        except Exception:
            pass
        ensure_arkham_tables(conn)

        # Cross-agent signal bus
        conn.execute("""
            CREATE TABLE IF NOT EXISTS cross_agent_signals (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                ts_utc            TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                source            TEXT NOT NULL,
                target            TEXT NOT NULL,
                signal_type       TEXT NOT NULL,
                token_symbol      TEXT,
                token_mint        TEXT,
                mc_tier           TEXT,
                buy_amount_usd    REAL,
                market_cap_usd    REAL,
                scanner_score     REAL,
                scanner_rug_label TEXT,
                expires_ts        TEXT,
                consumed          INTEGER DEFAULT 0,
                consumed_ts       TEXT,
                ref_alert_id      INTEGER
            )
        """)


# ── Message parser ────────────────────────────────────────────────────────────

def _extract_dollar_amounts(text: str) -> list[tuple[int, float]]:
    """Return list of (position, usd_value) for all $X / $XK / $XM in text."""
    results = []
    for m in re.finditer(r'\$([0-9,]+\.?[0-9]*)([KMBkmb]?)', text):
        try:
            raw = float(m.group(1).replace(",", ""))
            suffix = m.group(2).upper()
            if suffix == "K":
                raw *= 1_000
            elif suffix == "M":
                raw *= 1_000_000
            elif suffix == "B":
                raw *= 1_000_000_000
            results.append((m.start(), raw))
        except Exception:
            pass
    return results


def parse_whale_alert(text: str) -> dict | None:
    """
    Parse a Moby Whale Watch alert.
    Returns dict with keys: alert_type, kol_name, token_symbol, buy_amount_usd, market_cap_usd
    Returns None if the message doesn't look like a whale/KOL alert.

    Handles formats like:
      "A 🐋 whale just bought $30K of $BONK at $11.7M MC"
      "KOL @CryptoGuru just bought $5K of $WIF at $23M MC"
      "Smart money bought $10,000 of PEPE | MC: $45M"
    """
    text_lower = text.lower()

    has_whale = any(kw in text_lower for kw in ["whale", "smart money", "big buy", "big wallet"])
    has_kol   = bool(re.search(r'\bkol\b', text_lower))

    if not has_whale and not has_kol:
        return None

    result: dict = {
        "alert_type":     "KOL" if has_kol else "WHALE",
        "kol_name":       None,
        "token_symbol":   None,
        "buy_amount_usd": None,
        "market_cap_usd": None,
    }

    # KOL name — "KOL @handle" or "KOL Name"
    if has_kol:
        kol_m = re.search(r'\bkol\b\s+[@]?([A-Za-z0-9_]+)', text, re.IGNORECASE)
        if kol_m:
            result["kol_name"] = kol_m.group(1)

    _non_tokens = {"MC", "USD", "USDC", "USDT", "SOL", "ETH", "BTC", "KOL"}

    # PRIMARY: Moby format — "of $TOKEN at" or "of TOKEN at"
    # This is the actual token being bought, not the whale-type label ($PUNCH, $BABY etc.)
    # Handles mixed-case like $BioLLM, $arc, $WIF
    of_at_m = re.search(r'\bof\s+\$?([A-Za-z][A-Za-z0-9]{1,15})\s+at\b', text, re.IGNORECASE)
    if of_at_m:
        sym = of_at_m.group(1).upper()
        if sym not in _non_tokens:
            result["token_symbol"] = sym

    if not result["token_symbol"]:
        # FALLBACK: "of $TOKEN" anywhere (no "at" required)
        of_m = re.search(r'\bof\s+\$([A-Za-z][A-Za-z0-9]{1,15})\b', text)
        if of_m:
            sym = of_m.group(1).upper()
            if sym not in _non_tokens:
                result["token_symbol"] = sym

    if not result["token_symbol"]:
        # LAST RESORT: first $SYMBOL in text (old behaviour — may pick up whale-type label)
        sym_candidates = re.findall(r'\$([A-Z]{2,10})\b', text)
        for sym in sym_candidates:
            if sym not in _non_tokens:
                result["token_symbol"] = sym
                break

    # Dollar amounts
    amounts = _extract_dollar_amounts(text)
    if amounts:
        text_lower = text.lower()
        mc_pos  = -1
        buy_pos = -1

        # Locate keywords in original text
        for kw in ["mc", "market cap", "mcap", " at $", "cap:"]:
            idx = text_lower.find(kw)
            if idx >= 0:
                mc_pos = idx
                break

        for kw in ["bought", "buy", "purchased"]:
            idx = text_lower.find(kw)
            if idx >= 0:
                buy_pos = idx
                break

        if len(amounts) >= 2:
            if mc_pos >= 0 and buy_pos >= 0:
                # Assign by proximity to keywords
                mc_amt  = min(amounts, key=lambda a: abs(a[0] - mc_pos))
                buy_amt = min(amounts, key=lambda a: abs(a[0] - buy_pos))
                result["market_cap_usd"]   = mc_amt[1]
                result["buy_amount_usd"]   = buy_amt[1]
            else:
                # Heuristic: larger = MC, smaller = buy
                by_val = sorted(amounts, key=lambda a: a[1])
                result["buy_amount_usd"]   = by_val[0][1]
                result["market_cap_usd"]   = by_val[-1][1]
        elif len(amounts) == 1:
            if mc_pos >= 0:
                result["market_cap_usd"] = amounts[0][1]
            else:
                result["buy_amount_usd"] = amounts[0][1]

    # Reject if we couldn't find a token symbol — nothing actionable
    if not result["token_symbol"]:
        return None

    return result


# ── Token resolver ────────────────────────────────────────────────────────────

def _resolve_token(symbol: str, market_cap_hint: float | None = None) -> dict:
    """
    Resolve a Solana token by symbol using the shared cache-first resolver.

    Returns:
      {
        "pair": dict | None,
        "mint": str,
        "source": str | None,
        "reason": str | None,
        "candidate_count": int,
      }
    """
    try:
        from utils.token_resolver import resolve_solana_token  # type: ignore

        return resolve_solana_token(symbol, market_cap_hint=market_cap_hint)
    except Exception as e:
        log.debug("[WHALE] token resolver failed for %s: %s", symbol, e)
        return {
            "pair": None,
            "mint": "",
            "source": None,
            "reason": "resolver_error",
            "candidate_count": 0,
        }


def _pair_price_usd(pair: dict) -> Optional[float]:
    """Best-effort USD price from the resolved DexScreener pair payload."""
    try:
        price_raw = pair.get("priceUsd")
        if price_raw is None:
            return None
        price = float(price_raw)
        return price if price > 0 else None
    except Exception:
        return None


def _register_price_feed_mint(mint: str) -> None:
    """Register newly seen whale mints with the shared price feed when available."""
    try:
        from utils import ws_price_feed  # type: ignore
        ws_price_feed.register_mint(mint)
    except Exception:
        pass


def _get_token_price(mint: str) -> Optional[float]:
    """Fetch current USD price using the active price paths in this codebase."""
    try:
        from utils import ws_price_feed  # type: ignore
        cached = ws_price_feed.get_price(mint)
        if cached and cached > 0:
            return float(cached)
    except Exception:
        pass
    try:
        import asyncio as _asyncio
        from utils.jupiter_swap import get_token_price_usd  # type: ignore
        price = _asyncio.run(get_token_price_usd(mint))
        if price and price > 0:
            return float(price)
    except Exception:
        pass
    return None


def _get_token_price_with_source(mint: str) -> tuple[Optional[float], Optional[str]]:
    try:
        from utils import ws_price_feed  # type: ignore
        cached = ws_price_feed.get_price(mint)
        if cached and cached > 0:
            return float(cached), "ws_price_feed"
    except Exception:
        pass
    try:
        import asyncio as _asyncio
        from utils.jupiter_swap import get_token_price_usd  # type: ignore
        price = _asyncio.run(get_token_price_usd(mint))
        if price and price > 0:
            return float(price), "jupiter"
    except Exception:
        pass
    try:
        from data.birdeye import fetch_birdeye_price  # type: ignore
        price = fetch_birdeye_price(mint)
        if price and price > 0:
            return float(price), "birdeye"
    except Exception:
        pass
    try:
        from data.dexscreener import fetch_token_snapshot  # type: ignore

        snap = fetch_token_snapshot(mint) or {}
        price = float(snap.get("price") or 0)
        if price > 0:
            return price, "dex_token_budgeted"
    except Exception:
        pass
    return None, None


def _resolve_alert_price(mint: str, pair: dict | None = None) -> dict:
    if not mint:
        return {"price": None, "source": None, "reason": "missing_mint"}
    pair_price = _pair_price_usd(pair or {})
    if pair_price and pair_price > 0:
        return {"price": pair_price, "source": "dex_pair", "reason": None}
    price, source = _get_token_price_with_source(mint)
    if price and price > 0:
        return {"price": price, "source": source, "reason": None}
    return {"price": None, "source": None, "reason": "price_unavailable"}


# ── Scanner gate check ────────────────────────────────────────────────────────

def _run_scanner_check(pair: dict, mint: str) -> dict:
    """
    Run key scanner gates on a pair dict from DexScreener.
    Returns: {pass: bool, score: float, rug_label: str, reason: str}
    """
    from utils.memecoin_scanner import _rug_check, _score_token

    liq    = float((pair.get("liquidity") or {}).get("usd", 0) or 0)
    vol24  = float((pair.get("volume")    or {}).get("h24", 0) or 0)
    vol1h  = float((pair.get("volume")    or {}).get("h1",  0) or 0)
    chg1h  = float((pair.get("priceChange") or {}).get("h1", 0) or 0)
    mc     = float((pair.get("fdv") or pair.get("marketCap") or 0))

    # Vol acceleration: 1h vol as % of 24h vol (expect ≥3%)
    vol_acc = (vol1h / vol24 * 100) if vol24 > 0 else 0.0

    rug = _rug_check(mint)
    rug_label = rug.get("rug_label", "UNKNOWN")
    top_holder = rug.get("top_holder_pct", 0.0)

    score, _ = _score_token(pair, vol_acc, rug_label)

    # Gate checks
    reasons = []
    passed  = True

    if rug_label in ("DANGER", "RUGGED"):
        reasons.append(f"rug={rug_label}")
        passed = False
    if top_holder > 35.0:
        reasons.append(f"top_holder={top_holder:.0f}%")
        passed = False
    if liq < 10_000:
        reasons.append(f"liq=${liq:,.0f}")
        passed = False
    if vol24 < 25_000:
        reasons.append(f"vol24=${vol24:,.0f}")
        passed = False

    return {
        "pass":      passed,
        "score":     score,
        "rug_label": rug_label,
        "reason":    ", ".join(reasons) if reasons else "ok",
    }


# ── Accumulation detection ─────────────────────────────────────────────────────

def _detect_accumulation(symbol: str, current_id: int) -> bool:
    """Return True if this token was alerted 2+ times in the last 10 minutes."""
    try:
        from utils.db import get_conn
        cutoff = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        with get_conn() as conn:
            row = conn.execute("""
                SELECT COUNT(*) FROM whale_watch_alerts
                WHERE token_symbol = ?
                  AND ts_utc >= datetime('now', '-10 minutes')
                  AND id != ?
            """, (symbol, current_id)).fetchone()
            return bool(row and row[0] >= 1)
    except Exception:
        return False


# ── Log + alert ───────────────────────────────────────────────────────────────

def _log_alert(parsed: dict, raw_text: str) -> int:
    """Insert a new alert row. Returns the inserted row id."""
    from utils.db import get_conn, with_db_retry
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    mc = parsed.get("market_cap_usd") or 0
    in_range = 1 if MC_MIN_USD <= mc <= MC_MAX_USD else 0
    tier = _mc_tier(mc)

    def _write() -> int:
        with get_conn() as conn:
            cur = conn.execute("""
                INSERT INTO whale_watch_alerts
                    (ts_utc, raw_text, alert_type, kol_name, token_symbol,
                     buy_amount_usd, market_cap_usd, mc_tier, mc_in_range)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                ts,
                raw_text,
                parsed.get("alert_type"),
                parsed.get("kol_name"),
                parsed.get("token_symbol"),
                parsed.get("buy_amount_usd"),
                parsed.get("market_cap_usd"),
                tier,
                in_range,
            ))
            return int(cur.lastrowid)

    return int(with_db_retry(_write, retries=5, base_sleep_s=0.35))


def _update_alert_resolution(
    alert_id: int,
    *,
    mint: str | None = None,
    pair_address: str | None = None,
    market_cap_usd: float | None = None,
    mint_resolution_source: str | None = None,
    mint_resolution_reason: str | None = None,
    price: Optional[float] = None,
    price_source: str | None = None,
    price_resolution_reason: str | None = None,
) -> None:
    from utils.db import get_conn, with_db_retry

    def _write() -> None:
        with get_conn() as conn:
            conn.execute("""
                UPDATE whale_watch_alerts
                SET token_mint=COALESCE(?, token_mint),
                    token_pair_address=COALESCE(?, token_pair_address),
                    market_cap_usd=COALESCE(?, market_cap_usd),
                    mint_resolution_source=?,
                    mint_resolution_reason=?,
                    price_at_alert=COALESCE(?, price_at_alert),
                    price_source=?,
                    price_resolution_reason=?
                WHERE id=?
            """, (
                mint,
                pair_address,
                market_cap_usd,
                mint_resolution_source,
                mint_resolution_reason,
                price,
                price_source,
                price_resolution_reason,
                alert_id,
            ))

    with_db_retry(_write, retries=5, base_sleep_s=0.25)


def _update_alert_scanner(alert_id: int, scanner: dict) -> None:
    """Update alert row with scanner result."""
    from utils.db import get_conn, with_db_retry

    def _write() -> None:
        with get_conn() as conn:
            conn.execute("""
                UPDATE whale_watch_alerts
                SET scanner_pass=?, scanner_score=?, scanner_rug_label=?, scanner_reason=?
                WHERE id=?
            """, (
                1 if scanner["pass"] else 0,
                scanner["score"],
                scanner["rug_label"],
                scanner.get("reason"),
                alert_id,
            ))

    with_db_retry(_write, retries=5, base_sleep_s=0.25)


def _update_alert_arkham(alert_id: int, intel: dict) -> None:
    """Persist Arkham token enrichment onto the alert row for downstream lanes."""
    from utils.db import get_conn, with_db_retry

    def _write() -> None:
        with get_conn() as conn:
            conn.execute(
                """
                UPDATE whale_watch_alerts
                SET arkham_status=?,
                    arkham_signal_quality=?,
                    arkham_signal_score=?,
                    arkham_entity_holder_count=?,
                    arkham_top_entity_name=?,
                    arkham_top_entity_type=?,
                    arkham_top_entity_pct_of_cap=?,
                    arkham_top_flow_entity_name=?,
                    arkham_top_flow_entity_type=?,
                    arkham_net_flow_usd=?,
                    arkham_enriched_at=?
                WHERE id=?
                """,
                (
                    intel.get("status"),
                    intel.get("signal_quality"),
                    intel.get("signal_score"),
                    intel.get("entity_holder_count"),
                    intel.get("top_entity_name"),
                    intel.get("top_entity_type"),
                    intel.get("top_entity_pct_of_cap"),
                    intel.get("top_flow_entity_name"),
                    intel.get("top_flow_entity_type"),
                    intel.get("net_flow_usd"),
                    intel.get("updated_ts_utc"),
                    alert_id,
                ),
            )

    with_db_retry(_write, retries=5, base_sleep_s=0.25)


def _mark_alert_sent(alert_id: int) -> None:
    from utils.db import get_conn, with_db_retry

    def _write() -> None:
        with get_conn() as conn:
            conn.execute("UPDATE whale_watch_alerts SET alert_sent=1 WHERE id=?", (alert_id,))

    with_db_retry(_write, retries=5, base_sleep_s=0.25)


def _write_cross_signal(alert_id: int, parsed: dict, mc: float, scanner: dict, mint: str) -> None:
    """
    Write a cross-agent signal to the signal bus when whale + scanner both pass.
    Consuming agents query this table to find whale-confirmed tokens.

    Phase 3+ (100 outcomes): memecoin_scanner reads WHALE_CONFIRM for sweet_spot tokens
    Phase 4+ (250 outcomes): spot_accumulator reads WHALE_CONFIRM for mid/large tokens
    """
    from utils.db import get_conn, with_db_retry
    from datetime import timedelta
    tier   = _mc_tier(mc)
    target = _TIER_TARGETS.get(tier, "observation")
    expires = (datetime.now(timezone.utc) + timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")

    def _write() -> None:
        with get_conn() as conn:
            conn.execute("""
                INSERT INTO cross_agent_signals
                    (source, target, signal_type, token_symbol, token_mint, mc_tier,
                     buy_amount_usd, market_cap_usd, scanner_score, scanner_rug_label,
                     expires_ts, ref_alert_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                "whale_watch",
                target,
                "WHALE_CONFIRM",
                parsed.get("token_symbol"),
                mint,
                tier,
                parsed.get("buy_amount_usd"),
                mc,
                scanner["score"],
                scanner["rug_label"],
                expires,
                alert_id,
            ))

    try:
        with_db_retry(_write, retries=5, base_sleep_s=0.25)
        log.info("[WHALE] Cross-signal written: %s → %s (tier=%s)",
                 parsed.get("token_symbol"), target, tier)
    except Exception as e:
        log.warning("[WHALE] cross_signal write failed: %s", e)


# ── Outcome tracking ──────────────────────────────────────────────────────────

def whale_watch_outcome_step() -> None:
    """
    Update price outcomes for pending whale_watch_alerts.
    Called every 5 minutes from the monitor loop.
    """
    from utils.db import get_conn
    now = datetime.now(timezone.utc)
    cutoff_24h = (now.timestamp() - 86400)

    # Patch 218: mark untrackable rows UNRESOLVED before TTL sees them.
    # Rows with no mint / no entry price are cross-chain tokens (ETH-native,
    # stablecoins) whose prices can't be resolved via the Solana/DexScreener
    # oracle.  Writing -100.0 for these is false data — use UNRESOLVED so the
    # stats query excludes them from WR and avg_return calculations.
    from utils.db import get_conn as _gc218  # local alias to avoid shadowing
    with _gc218() as conn:
        conn.execute("""
            UPDATE whale_watch_alerts
            SET outcome_status = 'UNRESOLVED'
            WHERE outcome_status = 'PENDING'
              AND (token_mint IS NULL OR token_mint = ''
                   OR price_at_alert IS NULL OR price_at_alert = 0)
        """)

    # Patch 151: universal TTL enforcer — closes any PENDING rows older than 48h
    from utils.outcome_ttl import close_stale_pending  # type: ignore
    with get_conn() as conn:
        close_stale_pending(conn, table="whale_watch_alerts")

    with get_conn() as conn:
        rows = conn.execute("""
            SELECT id, ts_utc, token_mint, price_at_alert,
                   price_1h, price_4h, price_24h
            FROM whale_watch_alerts
            WHERE outcome_status='PENDING' AND token_mint IS NOT NULL AND price_at_alert > 0
        """).fetchall()

    for row in rows:
        try:
            row = dict(row)
            alert_ts = datetime.strptime(row["ts_utc"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            age_s = (now - alert_ts).total_seconds()
            mint  = row["token_mint"]

            updates: dict = {}
            needs_price = (
                (age_s >= 3600   and row["price_1h"]  is None) or
                (age_s >= 14400  and row["price_4h"]  is None) or
                (age_s >= 86400  and row["price_24h"] is None)
            )
            if not needs_price:
                continue

            current_price = _get_token_price(mint)

            # Patch 150: if token is dead/delisted and 24h has elapsed, close out anyway
            if not current_price or current_price <= 0:
                if age_s >= 86400:
                    # Token dead — mark COMPLETE with worst-case returns
                    from utils.db import get_conn as _gc
                    with _gc() as conn:
                        conn.execute("""
                            UPDATE whale_watch_alerts SET
                                price_1h  = COALESCE(price_1h,  0),
                                price_4h  = COALESCE(price_4h,  0),
                                price_24h = 0,
                                return_1h_pct  = COALESCE(return_1h_pct,  -100.0),
                                return_4h_pct  = COALESCE(return_4h_pct,  -100.0),
                                return_24h_pct = -100.0,
                                outcome_status = 'COMPLETE'
                            WHERE id=?
                        """, (row["id"],))
                continue

            entry = row["price_at_alert"]

            if age_s >= 3600 and row["price_1h"] is None:
                updates["price_1h"]      = current_price
                updates["return_1h_pct"] = round((current_price - entry) / entry * 100, 2)
            if age_s >= 14400 and row["price_4h"] is None:
                updates["price_4h"]      = current_price
                updates["return_4h_pct"] = round((current_price - entry) / entry * 100, 2)
            if age_s >= 86400 and row["price_24h"] is None:
                updates["price_24h"]       = current_price
                updates["return_24h_pct"]  = round((current_price - entry) / entry * 100, 2)

            if not updates:
                continue

            # Mark complete if 24h done
            if "price_24h" in updates:
                updates["outcome_status"] = "COMPLETE"

            set_clause = ", ".join(f"{k}=?" for k in updates)
            from utils.db import get_conn as _gc
            with _gc() as conn:
                conn.execute(
                    f"UPDATE whale_watch_alerts SET {set_clause} WHERE id=?",
                    list(updates.values()) + [row["id"]],
                )
        except Exception as e:
            log.debug("[WHALE] outcome_step error for id=%s: %s", row.get("id"), e)


# ── Core message handler ──────────────────────────────────────────────────────

async def _handle_whale_message(text: str) -> None:
    """Process one message from the Whale Watch channel."""
    from utils import orchestrator
    from utils.telegram_alerts import send_telegram_sync
    from utils.arkham_client import enrich_token

    if not text or not text.strip():
        return

    log.info("[WHALE] Raw message: %s", text[:200])

    parsed = parse_whale_alert(text)
    if not parsed:
        log.debug("[WHALE] Message doesn't match whale alert pattern — skipping")
        return

    symbol = parsed.get("token_symbol", "???")
    mc     = parsed.get("market_cap_usd") or 0
    buy    = parsed.get("buy_amount_usd") or 0
    in_range = MC_MIN_USD <= mc <= MC_MAX_USD if mc > 0 else None

    # Always log — even out-of-range alerts
    alert_id = await asyncio.to_thread(_log_alert, parsed, text)
    log.info("[WHALE] Logged alert id=%d: %s type=%s mc=$%.1fM buy=$%.0f in_range=%s",
             alert_id, symbol, parsed["alert_type"], mc / 1e6 if mc else 0, buy, in_range)

    if mc > 0 and not in_range:
        log.info("[WHALE] MC $%.1fM outside $5M-$50M range — logged, not scanning", mc / 1e6)
        return

    # Resolve token cache-first, then use budgeted live providers only if needed.
    resolution = await asyncio.to_thread(_resolve_token, symbol, mc or None)
    pair = resolution.get("pair")
    mint = str(resolution.get("mint") or "").strip()
    mint_source = resolution.get("source")
    mint_reason = resolution.get("reason")
    candidate_count = int(resolution.get("candidate_count") or 0)

    if not pair or not mint:
        await asyncio.to_thread(
            _update_alert_resolution,
            alert_id,
            market_cap_usd=mc or None,
            mint_resolution_source=mint_source,
            mint_resolution_reason=mint_reason or "mint_unresolved",
        )
        log.info(
            "[WHALE] Mint unresolved for %s: reason=%s candidates=%d",
            symbol,
            mint_reason or "mint_unresolved",
            candidate_count,
        )
        return

    pair_address = str(pair.get("pairAddress") or "").strip() or None

    # If MC was missing from alert, use DexScreener value and check range
    if mc == 0:
        mc = float(pair.get("fdv") or pair.get("marketCap") or 0)
        if mc > 0 and not (MC_MIN_USD <= mc <= MC_MAX_USD):
            await asyncio.to_thread(
                _update_alert_resolution,
                alert_id,
                mint=mint,
                pair_address=pair_address,
                market_cap_usd=mc,
                mint_resolution_source=mint_source,
                mint_resolution_reason=mint_reason,
            )
            log.info(
                "[WHALE] DexScreener MC $%.1fM outside range — skipping scanner for %s",
                mc / 1e6,
                symbol,
            )
            return

    # Register the mint with the shared feed so follow-up outcome tracking can warm naturally.
    await asyncio.to_thread(_register_price_feed_mint, mint)

    price_info = await asyncio.to_thread(_resolve_alert_price, mint, pair)
    price = price_info.get("price")
    price_source = price_info.get("source")
    price_reason = price_info.get("reason")

    await asyncio.to_thread(
        _update_alert_resolution,
        alert_id,
        mint=mint,
        pair_address=pair_address,
        market_cap_usd=mc or None,
        mint_resolution_source=mint_source,
        mint_resolution_reason=mint_reason,
        price=price,
        price_source=price_source,
        price_resolution_reason=price_reason,
    )

    log.info(
        "[WHALE] Resolution %s: mint_source=%s price_source=%s price_reason=%s",
        symbol,
        mint_source or "unknown",
        price_source or "none",
        price_reason or "ok",
    )

    # Arkham token/entity enrichment — fail-open support context only.
    intel = await asyncio.to_thread(enrich_token, mint, "solana")
    await asyncio.to_thread(_update_alert_arkham, alert_id, intel)

    # Run scanner gates
    scanner = await asyncio.to_thread(_run_scanner_check, pair, mint)
    await asyncio.to_thread(_update_alert_scanner, alert_id, scanner)

    log.info("[WHALE] Scanner check: pass=%s score=%.1f rug=%s reason=%s",
             scanner["pass"], scanner["score"], scanner["rug_label"], scanner["reason"])
    if intel.get("status") == "LIVE":
        log.info(
            "[WHALE] Arkham enrich: quality=%s score=%.1f top_entity=%s net_flow=$%.0f",
            intel.get("signal_quality"),
            float(intel.get("signal_score") or 0),
            intel.get("top_entity_name") or "n/a",
            float(intel.get("net_flow_usd") or 0),
        )

    # Check accumulation pattern
    is_accum = await asyncio.to_thread(_detect_accumulation, symbol, alert_id)

    # Send Telegram alert only if scanner passes
    if scanner["pass"]:
        mc_str  = f"${mc/1e6:.1f}M" if mc >= 1e6 else f"${mc:,.0f}"
        buy_str = f"${buy/1e3:.0f}K" if buy >= 1000 else f"${buy:,.0f}"
        accum_tag = " 🔁 ACCUMULATION" if is_accum else ""
        type_tag  = "🐋 WHALE" if parsed["alert_type"] == "WHALE" else f"🎯 KOL {parsed.get('kol_name','')}"

        title = f"{type_tag} + ✅ SCANNER{accum_tag}"
        body  = (
            f"<b>${symbol}</b> | MC {mc_str} | bought {buy_str}\n"
            f"Score: {scanner['score']:.0f} | Safety: {scanner['rug_label']}\n"
            + (f"Price: ${price:.6g}" if price and price > 0 else "Price: unavailable")
        )
        await asyncio.to_thread(send_telegram_sync, title, body, "🚨")
        await asyncio.to_thread(_mark_alert_sent, alert_id)
        await asyncio.to_thread(_write_cross_signal, alert_id, parsed, mc, scanner, mint)
        log.info("[WHALE] Telegram alert sent for %s", symbol)

    orchestrator.heartbeat("whale_watch")


# ── Telethon client ───────────────────────────────────────────────────────────

async def start_whale_watch() -> None:
    """
    Start the Telethon userbot and listen for Whale Watch messages.
    Called from main.py lifespan — runs indefinitely.

    Prerequisites:
      - Session file must exist (run whale_watch_setup.py once on VPS)
      - WHALE_WATCH_CHANNEL env var must be set
      - TELEGRAM_API_ID and TELEGRAM_API_HASH env vars must be set
    """
    from utils import orchestrator

    session = _session_file()
    channel = os.getenv("WHALE_WATCH_CHANNEL", "").strip()
    api_id   = int(os.getenv("TELEGRAM_API_ID",   "0") or "0")
    api_hash = os.getenv("TELEGRAM_API_HASH", "").strip()

    # Init DB table on startup
    try:
        init_whale_watch_table()
    except Exception as e:
        log.warning("[WHALE] Table init error: %s", e)

    # Guard: session file required
    if not os.path.exists(session):
        log.warning("[WHALE] Session file not found at %s — run whale_watch_setup.py first", session)
        while not os.path.exists(session):
            await asyncio.sleep(300)
        log.info("[WHALE] Session file appeared — (re)starting Whale Watch")

    if not channel:
        log.warning("[WHALE] WHALE_WATCH_CHANNEL not set — Whale Watch disabled")
        while True:
            await asyncio.sleep(3600)

    if not api_id or not api_hash:
        log.warning("[WHALE] TELEGRAM_API_ID / TELEGRAM_API_HASH not set — Whale Watch disabled")
        while True:
            await asyncio.sleep(3600)

    try:
        from telethon import TelegramClient, events  # type: ignore
    except ImportError:
        log.error("[WHALE] telethon not installed — run: pip install telethon")
        while True:
            await asyncio.sleep(3600)
        return

    client = TelegramClient(session, api_id, api_hash)

    @client.on(events.NewMessage(chats=[channel]))
    async def _on_message(event) -> None:
        try:
            await _handle_whale_message(event.message.message or "")
        except Exception as e:
            log.warning("[WHALE] handler error: %s", e)

    try:
        await client.start()
        log.info("[WHALE] Telethon client connected. Listening to channel: %s", channel)
        orchestrator.heartbeat("whale_watch")

        # Keep running — heartbeat every 60s, reconnect if needed
        while True:
            await asyncio.sleep(60)
            orchestrator.heartbeat("whale_watch")
            if not client.is_connected():
                log.warning("[WHALE] Client disconnected — reconnecting")
                await client.connect()

    except Exception as e:
        log.error("[WHALE] Fatal error in start_whale_watch: %s", e)
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass
