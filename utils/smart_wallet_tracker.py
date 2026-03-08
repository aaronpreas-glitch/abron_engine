"""
Smart Wallet Tracker — Patch 145

Polls Helius Enhanced Transactions API every 5 min for each tracked wallet.
Logs new swap buys, detects multi-wallet accumulation, tracks 1h/4h/24h outcomes.

Wallets are managed via the dashboard (POST /api/wallets/add).
Called from monitor loop: every 5 min.
"""
from __future__ import annotations

import json
import logging
import os
import time
from contextlib import suppress
from datetime import datetime, timedelta, timezone

import requests

log = logging.getLogger(__name__)

# ── Config ─────────────────────────────────────────────────────────────────────

HELIUS_KEY     = os.environ.get("HELIUS_API_KEY", "")
HELIUS_BASE    = "https://api.helius.xyz/v0"

# ── Upstream failure alert helpers (Patch 163) ─────────────────────────────────

def _alert_helius_failure(status_code: int) -> None:
    """Rate-limited Telegram when Helius API returns non-200. At most once per hour.

    Patch 164: upgraded from in-memory to persistent kv_store rate limit.
    """
    from utils.db import persistent_rate_limit_check  # Patch 164
    from utils.telegram_alerts import send_telegram_sync  # noqa
    if persistent_rate_limit_check("helius_failure", 3600):
        return
    send_telegram_sync(
        "Helius API Failure",
        f"Smart wallet tracker: Helius returned HTTP {status_code}.\n"
        f"New wallet buys may be <b>missed</b> until resolved.",
        "⚠️",
    )


def _alert_dex_429_swt() -> None:
    """Rate-limited Telegram when DexScreener returns 429 in token resolution. 30 min.

    Patch 164: upgraded from in-memory to persistent kv_store rate limit.
    """
    from utils.db import persistent_rate_limit_check  # Patch 164
    from utils.telegram_alerts import send_telegram_sync  # noqa
    if persistent_rate_limit_check("dex_429_swt", 1800):
        return
    send_telegram_sync(
        "DexScreener Rate Limit",
        "Smart wallet tracker: DexScreener returning 429.\n"
        "Token price resolution failing — buys may be <b>skipped</b> (NULL price guard).",
        "⚠️",
    )


STABLE_MINTS: set[str] = {
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",  # USDT
    "So11111111111111111111111111111111111111112",      # WSOL
}

MIN_SOL_BUY    = 0.05      # ignore dust buys below this SOL amount
PHASE_SIGNAL   = 50        # Telegram alerts unlock at this many total logged buys
ACCUM_WINDOW_H = 2         # hours to look back for multi-wallet accumulation
ACCUM_MIN      = 2         # wallets needed to trigger accumulation alert
SOL_PRICE_USD  = 140.0     # fallback if we can't fetch live price (rough estimate)


# ── DB helpers ─────────────────────────────────────────────────────────────────

def _get_conn():
    from utils.db import get_conn  # type: ignore
    return get_conn()


def _get_active_wallets() -> list[dict]:
    try:
        with _get_conn() as conn:
            rows = conn.execute(
                "SELECT id, address, label, last_checked_ts FROM smart_wallets WHERE active=1"
            ).fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        log.warning("[SWT] get_active_wallets error: %s", e)
        return []


def _update_last_checked(conn, wallet_id: int, ts: float) -> None:
    conn.execute(
        "UPDATE smart_wallets SET last_checked_ts=? WHERE id=?", (ts, wallet_id)
    )


def _total_buys(conn) -> int:
    row = conn.execute("SELECT COUNT(*) FROM smart_wallet_buys").fetchone()
    return row[0] if row else 0


# ── Helius fetch ───────────────────────────────────────────────────────────────

def _fetch_wallet_swaps(address: str, since_ts: float | None) -> list[dict]:
    """
    Call Helius Enhanced Transactions API for SWAP-type txns.
    Filter to only transactions newer than since_ts (Unix epoch).
    Returns list ordered newest-first (Helius default).
    """
    if not HELIUS_KEY:
        log.warning("[SWT] HELIUS_API_KEY not set")
        return []
    try:
        r = requests.get(
            f"{HELIUS_BASE}/addresses/{address}/transactions",
            params={"api-key": HELIUS_KEY, "type": "SWAP", "limit": 10},
            timeout=12,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        if r.status_code != 200:
            log.warning("[SWT] Helius %s → HTTP %d", address[:12], r.status_code)  # Patch 163
            _alert_helius_failure(r.status_code)  # Patch 163
            return []
        txns = r.json()
        if not isinstance(txns, list):
            log.debug("[SWT] Helius unexpected response for %s: %s", address[:12], type(txns))
            return []
        if since_ts:
            txns = [t for t in txns if t.get("timestamp", 0) > since_ts]
        return txns
    except Exception as e:
        log.warning("[SWT] fetch_wallet_swaps error for %s: %s", address[:12], e)  # Patch 163
        return []


# ── Buy parsing ────────────────────────────────────────────────────────────────

def _parse_buy(tx: dict, wallet_address: str) -> dict | None:
    """
    Determine if this SWAP represents the wallet buying a non-stable SPL token.
    Returns {mint, sol_spent, source, sig, timestamp} or None.
    """
    # Find token transfers where wallet RECEIVES a non-stable token
    received = [
        t for t in tx.get("tokenTransfers", [])
        if t.get("toUserAccount") == wallet_address
        and t.get("mint") not in STABLE_MINTS
        and t.get("mint")  # non-empty
    ]
    if not received:
        return None

    # Pick the largest received token (some swaps have fee splits)
    # tokenAmount may be string or float
    def _amt(t):
        with suppress(Exception):
            return float(t.get("tokenAmount", 0))
        return 0.0

    received.sort(key=_amt, reverse=True)
    bought_mint = received[0]["mint"]

    # Calculate SOL spent (native transfers from wallet)
    sol_spent = 0.0
    for nt in tx.get("nativeTransfers", []):
        if nt.get("fromUserAccount") == wallet_address:
            with suppress(Exception):
                sol_spent += int(nt.get("amount", 0)) / 1e9

    if sol_spent < MIN_SOL_BUY:
        return None

    return {
        "mint":      bought_mint,
        "sol_spent": sol_spent,
        "source":    tx.get("source", "unknown"),
        "sig":       tx.get("signature", ""),
        "timestamp": tx.get("timestamp", int(time.time())),
    }


# ── Token resolution ───────────────────────────────────────────────────────────

def _resolve_token(mint: str) -> dict:
    """
    DexScreener lookup for symbol, price, market cap.
    Same pattern as memecoin_scanner.py.
    Returns dict (may have None fields on failure).
    """
    try:
        r = requests.get(
            f"https://api.dexscreener.com/latest/dex/tokens/{mint}",
            timeout=8,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        if r.status_code == 429:  # Patch 163: 429 = rate limited; alert and skip
            log.warning("[SWT] DexScreener 429 rate limit resolving %s", mint[:12])
            _alert_dex_429_swt()
            return {"symbol": None, "price_usd": None, "market_cap_usd": None}
        if r.status_code != 200:
            return {"symbol": None, "price_usd": None, "market_cap_usd": None}
        pairs = r.json().get("pairs") or []
        # Pick SOL-base pair with most liquidity
        sol_pairs = [p for p in pairs if p.get("chainId") == "solana"]
        if not sol_pairs:
            return {"symbol": None, "price_usd": None, "market_cap_usd": None}
        best = max(sol_pairs, key=lambda p: float(p.get("liquidity", {}).get("usd", 0) or 0))
        return {
            "symbol":        best.get("baseToken", {}).get("symbol"),
            "price_usd":     float(best.get("priceUsd") or 0) or None,
            "market_cap_usd": float(best.get("marketCap") or 0) or None,
        }
    except Exception as e:
        log.debug("[SWT] resolve_token error for %s: %s", mint[:12], e)
        return {"symbol": None, "price_usd": None, "market_cap_usd": None}


# ── DB writes ──────────────────────────────────────────────────────────────────

def _log_buy(conn, wallet: dict, buy: dict, token: dict) -> bool:
    """Insert buy into smart_wallet_buys. Returns True if new row inserted."""
    ts = datetime.fromtimestamp(buy["timestamp"], tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    sol_price = token.get("price_usd") and (buy["sol_spent"] * SOL_PRICE_USD) or None
    # Use SOL * rough price if DexScreener can't give us USD
    buy_usd = buy["sol_spent"] * SOL_PRICE_USD if sol_price is None else sol_price

    try:
        conn.execute("""
            INSERT OR IGNORE INTO smart_wallet_buys
              (ts_utc, wallet_address, wallet_label, tx_signature,
               token_symbol, token_mint, buy_amount_sol, buy_amount_usd,
               market_cap_usd, price_at_buy, dex_source)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """, (
            ts,
            wallet["address"],
            wallet["label"],
            buy["sig"],
            token.get("symbol"),
            buy["mint"],
            round(buy["sol_spent"], 6),
            round(buy_usd, 2),
            token.get("market_cap_usd"),
            token.get("price_usd"),
            buy["source"],
        ))
        if conn.execute("SELECT changes()").fetchone()[0] > 0:
            # Increment wallet buy counter
            conn.execute(
                "UPDATE smart_wallets SET total_buys = total_buys + 1 WHERE id=?",
                (wallet["id"],)
            )
            log.info("[SWT] New buy: %s → %s %.4f SOL src=%s",
                     wallet["label"], token.get("symbol") or buy["mint"][:12],
                     buy["sol_spent"], buy["source"])
            # Wire to cross_agent_signals for confluence engine (Patch 146)
            try:
                expires = (
                    datetime.now(timezone.utc) + timedelta(hours=48)
                ).strftime("%Y-%m-%dT%H:%M:%SZ")
                conn.execute("""
                    INSERT INTO cross_agent_signals
                      (source, target, signal_type, token_symbol, token_mint,
                       buy_amount_usd, market_cap_usd, scanner_score, scanner_rug_label,
                       expires_ts)
                    VALUES (?,?,?,?,?,?,?,?,?,?)
                """, (
                    "smart_wallet",
                    "confluence_engine",
                    "SMART_WALLET_BUY",
                    token.get("symbol"),
                    buy["mint"],
                    round(buy_usd, 2),
                    token.get("market_cap_usd"),
                    round(buy["sol_spent"], 4),  # sol_spent stored in scanner_score
                    wallet["label"],             # wallet label stored in scanner_rug_label
                    expires,
                ))
            except Exception as _e:
                log.debug("[SWT] cross_agent_signals write error: %s", _e)
            return True
    except Exception as e:
        log.debug("[SWT] log_buy error: %s", e)
    return False


# ── Accumulation detection ─────────────────────────────────────────────────────

def _check_accumulation(conn, token_mint: str, total_buys_now: int) -> None:
    """
    If ACCUM_MIN+ wallets bought the same mint in the last ACCUM_WINDOW_H hours,
    log an accumulation event and fire Telegram if in Phase 3+.
    """
    cutoff = (
        datetime.now(timezone.utc) - timedelta(hours=ACCUM_WINDOW_H)
    ).strftime("%Y-%m-%d %H:%M:%S")

    rows = conn.execute("""
        SELECT DISTINCT wallet_address, wallet_label, SUM(buy_amount_sol) as sol
        FROM smart_wallet_buys
        WHERE token_mint=? AND ts_utc >= ?
        GROUP BY wallet_address
    """, (token_mint, cutoff)).fetchall()

    if len(rows) < ACCUM_MIN:
        return

    # Dedupe: check no accumulation logged for this mint in the window
    existing = conn.execute("""
        SELECT 1 FROM smart_wallet_accumulations
        WHERE token_mint=? AND ts_utc >= ?
    """, (token_mint, cutoff)).fetchone()
    if existing:
        return  # already logged this accumulation window

    # Resolve token info
    token = _resolve_token(token_mint)
    wallet_labels = [r["wallet_label"] or r["wallet_address"][:12] for r in rows]
    total_sol = sum(float(r["sol"] or 0) for r in rows)

    ts_now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    cur = conn.execute("""
        INSERT INTO smart_wallet_accumulations
          (ts_utc, token_mint, token_symbol, wallet_count, wallet_labels,
           total_sol, market_cap_usd)
        VALUES (?,?,?,?,?,?,?)
    """, (
        ts_now,
        token_mint,
        token.get("symbol"),
        len(rows),
        json.dumps(wallet_labels),
        round(total_sol, 4),
        token.get("market_cap_usd"),
    ))
    accum_id = cur.lastrowid
    log.info("[SWT] Accumulation detected: %s — %d wallets, %.2f SOL",
             token.get("symbol") or token_mint[:12], len(rows), total_sol)

    # Telegram alert in Phase 3+
    if total_buys_now >= PHASE_SIGNAL:
        sent = _send_accum_alert(token, wallet_labels, total_sol)
        if sent and accum_id:
            conn.execute("UPDATE smart_wallet_accumulations SET alert_sent=1 WHERE id=?", (accum_id,))  # Patch 150


def _send_accum_alert(token: dict, wallet_labels: list[str], total_sol: float) -> bool:
    """Send Telegram accumulation alert. Returns True if alert was sent. Patch 150."""
    try:
        from utils.telegram_alerts import send_telegram_sync, should_rate_limit  # type: ignore
        sym    = token.get("symbol") or "Unknown"
        mc_str = _fmt_mc(token.get("market_cap_usd"))
        key    = f"swt_accum_{token.get('symbol','?')}"
        if should_rate_limit(key, 14400):  # 4h rate limit
            return False
        send_telegram_sync(
            f"🎯 Smart Wallet Signal — {sym}",
            f"{len(wallet_labels)} wallets accumulating in {ACCUM_WINDOW_H}h\n"
            f"Wallets: {', '.join(wallet_labels)}\n"
            f"Total: {total_sol:.2f} SOL | MC: {mc_str}\n"
            "→ Check WALLETS tab",
            "🎯",
        )
        return True
    except Exception as e:
        log.debug("[SWT] send_accum_alert error: %s", e)
        return False


def _fmt_mc(mc: float | None) -> str:
    if mc is None:
        return "unknown"
    if mc >= 1_000_000:
        return f"${mc/1_000_000:.1f}M"
    if mc >= 1_000:
        return f"${mc/1_000:.0f}K"
    return f"${mc:.0f}"


# ── Outcome tracking ───────────────────────────────────────────────────────────

def _update_outcomes(conn) -> None:
    """
    Fill 1h/4h/24h price returns for PENDING buys.
    Same pattern as whale_watch_outcome_step.
    """
    pending = conn.execute("""
        SELECT id, token_mint, price_at_buy, ts_utc,
               price_1h, price_4h, price_24h
        FROM smart_wallet_buys
        WHERE outcome_status='PENDING' AND price_at_buy IS NOT NULL AND price_at_buy > 0
        ORDER BY ts_utc ASC
        LIMIT 30
    """).fetchall()

    now = datetime.now(timezone.utc)

    for row in pending:
        buy_ts = datetime.fromisoformat(row["ts_utc"].replace(" ", "T")).replace(tzinfo=timezone.utc)
        age_s  = (now - buy_ts).total_seconds()

        need_1h  = row["price_1h"]  is None and age_s >= 3600
        need_4h  = row["price_4h"]  is None and age_s >= 14400
        need_24h = row["price_24h"] is None and age_s >= 86400

        if not (need_1h or need_4h or need_24h):
            continue

        # Fetch current price
        token = _resolve_token(row["token_mint"])
        cur_price = token.get("price_usd")
        if cur_price is None or cur_price <= 0:
            # Patch 150: dead/delisted token — close out at 24h with -100% return
            if need_24h:
                conn.execute("""
                    UPDATE smart_wallet_buys SET
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

        ts_eval = now.strftime("%Y-%m-%d %H:%M:%S")
        base    = row["price_at_buy"]

        updates: dict = {}
        if need_1h:
            updates["price_1h"]       = cur_price
            updates["return_1h_pct"]  = round((cur_price - base) / base * 100, 2)
            updates["evaluated_1h_ts"] = ts_eval
        if need_4h:
            updates["price_4h"]       = cur_price
            updates["return_4h_pct"]  = round((cur_price - base) / base * 100, 2)
            updates["evaluated_4h_ts"] = ts_eval
        if need_24h:
            updates["price_24h"]       = cur_price
            updates["return_24h_pct"]  = round((cur_price - base) / base * 100, 2)
            updates["evaluated_24h_ts"] = ts_eval
            updates["outcome_status"]  = "COMPLETE"

        if updates:
            set_clause = ", ".join(f"{k}=?" for k in updates)
            conn.execute(
                f"UPDATE smart_wallet_buys SET {set_clause} WHERE id=?",
                (*updates.values(), row["id"]),
            )

    # Also update accumulation outcomes
    _update_accum_outcomes(conn, now)


def _update_accum_outcomes(conn, now: datetime) -> None:
    """Fill return fields on smart_wallet_accumulations using the constituent buy returns."""
    pending = conn.execute("""
        SELECT a.id, a.token_mint, a.ts_utc
        FROM smart_wallet_accumulations a
        WHERE a.outcome_status='PENDING'
    """).fetchall()

    for row in pending:
        ts = datetime.fromisoformat(row["ts_utc"].replace(" ", "T")).replace(tzinfo=timezone.utc)
        age_s = (now - ts).total_seconds()

        # Aggregate buy returns from constituent buys in the accumulation window
        cutoff = (ts - timedelta(hours=ACCUM_WINDOW_H)).strftime("%Y-%m-%d %H:%M:%S")

        updates: dict = {}
        if age_s >= 3600:
            r1 = conn.execute("""
                SELECT AVG(return_1h_pct) FROM smart_wallet_buys
                WHERE token_mint=? AND ts_utc >= ? AND return_1h_pct IS NOT NULL
            """, (row["token_mint"], cutoff)).fetchone()
            if r1 and r1[0] is not None:
                updates["return_1h_pct"] = round(r1[0], 2)

        if age_s >= 14400:
            r4 = conn.execute("""
                SELECT AVG(return_4h_pct) FROM smart_wallet_buys
                WHERE token_mint=? AND ts_utc >= ? AND return_4h_pct IS NOT NULL
            """, (row["token_mint"], cutoff)).fetchone()
            if r4 and r4[0] is not None:
                updates["return_4h_pct"] = round(r4[0], 2)

        if age_s >= 86400:
            r24 = conn.execute("""
                SELECT AVG(return_24h_pct) FROM smart_wallet_buys
                WHERE token_mint=? AND ts_utc >= ? AND return_24h_pct IS NOT NULL
            """, (row["token_mint"], cutoff)).fetchone()
            if r24 and r24[0] is not None:
                updates["return_24h_pct"] = round(r24[0], 2)
                updates["outcome_status"] = "COMPLETE"

        if updates:
            set_clause = ", ".join(f"{k}=?" for k in updates)
            conn.execute(
                f"UPDATE smart_wallet_accumulations SET {set_clause} WHERE id=?",
                (*updates.values(), row["id"]),
            )


# ── Public interface ───────────────────────────────────────────────────────────

def get_wallet_list() -> list[dict]:
    """Return all wallets (active + inactive) with stats. Used by API router."""
    try:
        with _get_conn() as conn:
            rows = conn.execute("""
                SELECT w.id, w.address, w.label, w.active, w.total_buys,
                       w.last_checked_ts, w.added_ts,
                       ROUND(AVG(CASE WHEN b.return_24h_pct > 0 THEN 1.0 ELSE 0.0 END)*100,1) as wr_24h
                FROM smart_wallets w
                LEFT JOIN smart_wallet_buys b ON b.wallet_address=w.address AND b.outcome_status='COMPLETE'
                GROUP BY w.id
                ORDER BY w.active DESC, w.total_buys DESC
            """).fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        log.warning("[SWT] get_wallet_list error: %s", e)
        return []


def add_wallet(address: str, label: str) -> dict:
    """Add a new tracked wallet. Returns {ok, id, error}."""
    import re
    if not re.match(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$", address):
        return {"ok": False, "error": "Invalid Solana address format"}
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    try:
        with _get_conn() as conn:
            conn.execute(
                "INSERT INTO smart_wallets (address, label, added_ts) VALUES (?,?,?)",
                (address, label or "Unknown", ts),
            )
            row = conn.execute(
                "SELECT id FROM smart_wallets WHERE address=?", (address,)
            ).fetchone()
            return {"ok": True, "id": row[0]}
    except Exception as e:
        if "UNIQUE" in str(e):
            return {"ok": False, "error": "Wallet already tracked"}
        return {"ok": False, "error": str(e)}


def remove_wallet(wallet_id: int) -> bool:
    """Soft delete (set active=0). Returns True on success."""
    try:
        with _get_conn() as conn:
            conn.execute("UPDATE smart_wallets SET active=0 WHERE id=?", (wallet_id,))
        return True
    except Exception:
        return False


def smart_wallet_step() -> None:
    """
    Main entry point — called every 5 min from monitor loop.
    Polls all active wallets, logs new buys, detects accumulation, updates outcomes.
    """
    from utils import orchestrator  # type: ignore

    wallets = _get_active_wallets()
    if not wallets:
        log.debug("[SWT] No active wallets to track")
        orchestrator.heartbeat("smart_wallet_tracker")
        return

    now_ts = time.time()

    try:
        with _get_conn() as conn:
            conn.row_factory = __import__("sqlite3").Row

            total = _total_buys(conn)
            new_mints: set[str] = set()

            for w in wallets:
                txns = _fetch_wallet_swaps(w["address"], w["last_checked_ts"])
                for tx in txns:
                    buy = _parse_buy(tx, w["address"])
                    if not buy:
                        continue
                    token = _resolve_token(buy["mint"])
                    inserted = _log_buy(conn, w, buy, token)
                    if inserted:
                        total += 1
                        new_mints.add(buy["mint"])
                _update_last_checked(conn, w["id"], now_ts)

            # Check accumulation for any mint that got a new buy
            for mint in new_mints:
                _check_accumulation(conn, mint, total)

            _update_outcomes(conn)

        log.info("[SWT] smart_wallet_step: %d wallets polled, %d new buys",
                 len(wallets), len(new_mints))
    except Exception as e:
        log.warning("[SWT] smart_wallet_step error: %s", e)

    orchestrator.heartbeat("smart_wallet_tracker")
