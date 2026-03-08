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

import requests

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
                buy_amount_usd   REAL,
                market_cap_usd   REAL,
                mc_tier          TEXT,           -- micro | sweet_spot | mid | large
                mc_in_range      INTEGER DEFAULT 0,   -- 1 if $5M–$50M
                scanner_pass     INTEGER,             -- 1 = gates pass, 0 = fail, NULL = not checked
                scanner_score    REAL,
                scanner_rug_label TEXT,
                alert_sent       INTEGER DEFAULT 0,
                price_at_alert   REAL,
                price_1h         REAL,
                price_4h         REAL,
                price_24h        REAL,
                return_1h_pct    REAL,
                return_4h_pct    REAL,
                return_24h_pct   REAL,
                outcome_status   TEXT DEFAULT 'PENDING'
            )
        """)
        # Upgrade path: add mc_tier column if missing (Patch 139 → 141)
        try:
            cols = [r[1] for r in conn.execute("PRAGMA table_info(whale_watch_alerts)").fetchall()]
            if "mc_tier" not in cols:
                conn.execute("ALTER TABLE whale_watch_alerts ADD COLUMN mc_tier TEXT")
        except Exception:
            pass

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


# ── DexScreener lookup ────────────────────────────────────────────────────────

def _resolve_token(symbol: str) -> dict | None:
    """
    Search DexScreener for a Solana token by symbol.
    Returns the highest-liquidity Solana pair data, or None.
    """
    try:
        r = requests.get(
            f"https://api.dexscreener.com/latest/dex/search?q={symbol}",
            timeout=REQUEST_TIMEOUT,
            headers={"User-Agent": "memecoin-engine/1.0"},
        )
        if r.status_code != 200:
            return None
        pairs = r.json().get("pairs") or []
        sol_pairs = [
            p for p in pairs
            if isinstance(p, dict) and p.get("chainId") == "solana"
            and (p.get("baseToken") or {}).get("symbol", "").upper() == symbol.upper()
        ]
        if not sol_pairs:
            # Relax: any Solana pair containing the symbol
            sol_pairs = [
                p for p in pairs
                if isinstance(p, dict) and p.get("chainId") == "solana"
            ]
        if not sol_pairs:
            return None
        # Pick highest liquidity
        best = max(
            sol_pairs,
            key=lambda p: float((p.get("liquidity") or {}).get("usd", 0) or 0),
        )
        return best
    except Exception as e:
        log.debug("[WHALE] DexScreener lookup failed for %s: %s", symbol, e)
        return None


def _get_token_price(mint: str) -> float:
    """Fetch current price for a mint. Primary: DexScreener. Fallback: Jupiter price API.
    price.jup.ag is dead (DNS removed) — do not use. Patch 150.
    """
    # Primary: DexScreener (no auth required)
    try:
        r = requests.get(
            f"https://api.dexscreener.com/latest/dex/tokens/{mint}",
            timeout=REQUEST_TIMEOUT,
            headers={"User-Agent": "memecoin-engine/1.0"},
        )
        if r.status_code == 200:
            pairs = r.json().get("pairs") or []
            sol_pairs = [p for p in pairs if p.get("chainId") == "solana"]
            if sol_pairs:
                best = max(sol_pairs, key=lambda p: float((p.get("liquidity") or {}).get("usd", 0) or 0))
                price = float(best.get("priceUsd", 0) or 0)
                if price > 0:
                    return price
    except Exception:
        pass
    # Fallback: Jupiter price API v2 (requires API key)
    try:
        jup_key = os.environ.get("JUPITER_API_KEY", "")
        headers = {"x-api-key": jup_key} if jup_key else {}
        r = requests.get(
            f"https://api.jup.ag/price/v2?ids={mint}",
            headers=headers,
            timeout=6,
        )
        if r.status_code == 200:
            data = r.json().get("data", {})
            td = data.get(mint)
            if td and td.get("price"):
                return float(td["price"])
    except Exception:
        pass
    return 0.0


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
    from utils.db import get_conn
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    mc = parsed.get("market_cap_usd") or 0
    in_range = 1 if MC_MIN_USD <= mc <= MC_MAX_USD else 0
    tier = _mc_tier(mc)

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
        return cur.lastrowid


def _update_alert_scanner(alert_id: int, mint: str, price: float, scanner: dict) -> None:
    """Update alert row with resolved mint, price, and scanner result."""
    from utils.db import get_conn
    with get_conn() as conn:
        conn.execute("""
            UPDATE whale_watch_alerts
            SET token_mint=?, price_at_alert=?, scanner_pass=?, scanner_score=?, scanner_rug_label=?
            WHERE id=?
        """, (
            mint,
            price,
            1 if scanner["pass"] else 0,
            scanner["score"],
            scanner["rug_label"],
            alert_id,
        ))


def _mark_alert_sent(alert_id: int) -> None:
    from utils.db import get_conn
    with get_conn() as conn:
        conn.execute("UPDATE whale_watch_alerts SET alert_sent=1 WHERE id=?", (alert_id,))


def _write_cross_signal(alert_id: int, parsed: dict, mc: float, scanner: dict, mint: str) -> None:
    """
    Write a cross-agent signal to the signal bus when whale + scanner both pass.
    Consuming agents query this table to find whale-confirmed tokens.

    Phase 3+ (100 outcomes): memecoin_scanner reads WHALE_CONFIRM for sweet_spot tokens
    Phase 4+ (250 outcomes): spot_accumulator reads WHALE_CONFIRM for mid/large tokens
    """
    from utils.db import get_conn
    from datetime import timedelta
    tier   = _mc_tier(mc)
    target = _TIER_TARGETS.get(tier, "observation")
    expires = (datetime.now(timezone.utc) + timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")

    try:
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

    # Patch 150: close out rows where price_at_alert was never captured (stuck forever)
    with get_conn() as conn:
        no_price_rows = conn.execute("""
            SELECT id, ts_utc FROM whale_watch_alerts
            WHERE outcome_status='PENDING'
              AND (price_at_alert IS NULL OR price_at_alert = 0)
        """).fetchall()
        for r in no_price_rows:
            try:
                ts = datetime.strptime(r["ts_utc"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                if (now - ts).total_seconds() >= 86400:
                    conn.execute("""
                        UPDATE whale_watch_alerts
                        SET outcome_status='COMPLETE', price_24h=0, return_24h_pct=NULL
                        WHERE id=?
                    """, (r["id"],))
            except Exception:
                pass

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
            if current_price <= 0:
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

    # Resolve token via DexScreener
    pair = await asyncio.to_thread(_resolve_token, symbol)
    if not pair:
        log.info("[WHALE] Could not resolve %s on DexScreener", symbol)
        return

    mint = (pair.get("baseToken") or {}).get("address", "")
    if not mint:
        log.info("[WHALE] No mint address found for %s", symbol)
        return

    # If MC was missing from alert, use DexScreener value and check range
    if mc == 0:
        mc = float(pair.get("fdv") or pair.get("marketCap") or 0)
        if mc > 0 and not (MC_MIN_USD <= mc <= MC_MAX_USD):
            log.info("[WHALE] DexScreener MC $%.1fM outside range — skipping scanner", mc / 1e6)
            from utils.db import get_conn
            with get_conn() as conn:
                conn.execute(
                    "UPDATE whale_watch_alerts SET token_mint=?, market_cap_usd=? WHERE id=?",
                    (mint, mc, alert_id),
                )
            return

    # Get price
    price = await asyncio.to_thread(_get_token_price, mint)

    # Run scanner gates
    scanner = await asyncio.to_thread(_run_scanner_check, pair, mint)
    await asyncio.to_thread(_update_alert_scanner, alert_id, mint, price, scanner)

    log.info("[WHALE] Scanner check: pass=%s score=%.1f rug=%s reason=%s",
             scanner["pass"], scanner["score"], scanner["rug_label"], scanner["reason"])

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
            f"Price: ${price:.6g}"
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
