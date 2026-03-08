"""
Confluence Engine — Patch 143

Detects when two independent signal sources agree on the same token within 48h:
  - Whale Watch (scanner_pass=1, token_mint resolved)
  - Memecoin Scanner (rug_label='GOOD', status='COMPLETE' or PENDING with score)

Runs as a poll-based agent (no Telethon). Called every 5 min from monitor loop.

Outcome tracking: price_1h / price_4h / price_24h via DexScreener individual endpoint.

Phase milestones:
  0-19   → OBSERVE  (log only)
  20-49  → ANALYZE  (stats shown in dashboard)
  50-99  → VALIDATE (Telegram alert on new confluence)
  100+   → INTEGRATE (paper signal, future: auto-watch)
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

import requests

log = logging.getLogger(__name__)

_DEXSCREENER_TOKEN_URL = "https://api.dexscreener.com/latest/dex/tokens/{mint}"
_PRICE_TIMEOUT = 6  # seconds


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _get_token_price(mint: str) -> float | None:
    """Fetch current price from DexScreener individual token endpoint."""
    try:
        r = requests.get(
            _DEXSCREENER_TOKEN_URL.format(mint=mint),
            timeout=_PRICE_TIMEOUT,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        pairs = r.json().get("pairs") or []
        if not pairs:
            return None
        price_str = (pairs[0].get("priceUsd") or "").strip()
        if price_str:
            return float(price_str)
    except Exception as e:
        log.debug("[CONF] price fetch failed for %s: %s", mint, e)
    return None


def _phase_label(total: int) -> str:
    if total < 20:
        return "OBSERVE"
    if total < 50:
        return "ANALYZE"
    if total < 100:
        return "VALIDATE"
    return "INTEGRATE"


def _send_confluence_alert(symbol: str, conf_score: float, whale_score: float,
                            meme_score: float, market_cap: float | None,
                            swt_wallets: list[str] | None = None) -> None:
    """Send Telegram alert for a new confluence event (dual or triple signal)."""
    try:
        from utils.telegram_alerts import send_telegram_sync, should_rate_limit  # type: ignore
        key = f"confluence_{symbol.upper()}"
        if should_rate_limit(key, 3600):
            return
        mc_str = f"${market_cap / 1_000_000:.1f}M" if market_cap else "unknown"
        if swt_wallets:
            title = "Triple Signal 🚨🔀"
            body = (
                f"{symbol} — WHALE + MEME + SMART WALLETS\n"
                f"Whale buy: ${whale_score:,.0f} | Meme score: {meme_score:.0f}\n"
                f"Smart wallets: {', '.join(swt_wallets)}\n"
                f"Confluence score: {conf_score:.1f} | MC: {mc_str}\n"
                "→ Check WALLETS + CONFLUENCE tabs"
            )
        else:
            title = "Confluence Signal 🔀"
            body = (
                f"{symbol} matched BOTH systems\n"
                f"Whale buy: ${whale_score:,.0f} | Meme score: {meme_score:.0f}\n"
                f"Confluence score: {conf_score:.1f} | MC: {mc_str}\n"
                "Observation mode — tracking outcome."
            )
        send_telegram_sync(title, body, "🔀")
    except Exception as e:
        log.debug("[CONF] telegram alert failed: %s", e)


# ──────────────────────────────────────────────────────────────────────────────
# Detection
# ──────────────────────────────────────────────────────────────────────────────

def _detect_confluences(conn) -> None:
    """
    Find new WHALE+MEME confluences and log them.
    Scans whale_watch_alerts (scanner_pass=1) against memecoin_signal_outcomes
    (rug_label='GOOD') within a 48-hour rolling window.
    """
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(hours=48)).strftime("%Y-%m-%d %H:%M:%S")

    # Whale alerts that passed scanner and have a resolved mint
    whales = conn.execute("""
        SELECT id, token_symbol, token_mint, buy_amount_usd, market_cap_usd, price_at_alert
        FROM whale_watch_alerts
        WHERE scanner_pass=1
          AND token_mint IS NOT NULL AND token_mint != ''
          AND ts_utc >= ?
    """, (cutoff,)).fetchall()

    if not whales:
        return

    for w in whales:
        w = dict(w)
        mint = w["token_mint"]

        # Skip if already logged for this whale alert
        exists = conn.execute(
            "SELECT 1 FROM confluence_events WHERE whale_alert_id=?", (w["id"],)
        ).fetchone()
        if exists:
            continue

        # Find matching GOOD memecoin scan in same 48h window
        # Note: memecoin_signal_outcomes uses 'mint' (not 'token_mint') and 'status'
        meme = conn.execute("""
            SELECT id, score FROM memecoin_signal_outcomes
            WHERE mint=? AND rug_label='GOOD' AND scanned_at >= ?
            ORDER BY scanned_at DESC LIMIT 1
        """, (mint, cutoff)).fetchone()

        if not meme:
            continue

        meme = dict(meme)
        whale_score = float(w.get("buy_amount_usd") or 0)
        meme_score  = float(meme.get("score") or 0)
        # Normalise whale_score: $10k buy → score 100, cap at 100
        base_score  = (min(whale_score / 10_000 * 100, 100) + meme_score) / 2

        # ── 3rd arm: Smart Wallet check (Patch 146) ───────────────────────────
        swt_wallets: list[str] = []
        swt_sol = 0.0
        try:
            swt_rows = conn.execute("""
                SELECT wallet_label, wallet_address, SUM(buy_amount_sol) as total_sol
                FROM smart_wallet_buys
                WHERE token_mint=? AND ts_utc >= ?
                GROUP BY wallet_address
            """, (mint, cutoff)).fetchall()
            swt_rows = [dict(r) for r in swt_rows]
            swt_wallets = [r.get("wallet_label") or r.get("wallet_address", "?")[:12]
                           for r in swt_rows]
            swt_sol = sum(float(r.get("total_sol") or 0) for r in swt_rows)
        except Exception as _e:
            log.debug("[CONF] smart_wallet check error: %s", _e)

        swt_present = len(swt_wallets) > 0
        # Smart wallet boost: each SOL spent adds up to 2 pts (cap at +20)
        swt_boost   = min(swt_sol * 2, 20) if swt_present else 0.0
        conf_score  = round(base_score + swt_boost, 1)

        sources      = json.dumps(["whale_watch", "memecoin", "smart_wallet"] if swt_present
                                   else ["whale_watch", "memecoin"])
        source_count = 3 if swt_present else 2
        conf_type    = "TRIPLE" if swt_present else "DUAL"

        conn.execute("""
            INSERT INTO confluence_events
              (ts_utc, token_symbol, token_mint, sources,
               whale_alert_id, memecoin_scan_id,
               whale_score, memecoin_score, confluence_score,
               market_cap_usd, price_at_event,
               confluence_type, source_count)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            now.strftime("%Y-%m-%d %H:%M:%S"),
            w.get("token_symbol") or "???",
            mint,
            sources,
            w["id"],
            meme["id"],
            whale_score,
            meme_score,
            conf_score,
            w.get("market_cap_usd"),
            w.get("price_at_alert"),
            conf_type,
            source_count,
        ))
        conn.commit()

        log.info(
            "[CONF] New %s confluence: %s mint=%s conf_score=%.1f whale=$%.0f "
            "meme_score=%.0f swt=%d wallets",
            conf_type, w.get("token_symbol"), mint, conf_score,
            whale_score, meme_score, len(swt_wallets),
        )

        # Telegram alert once we have enough data (VALIDATE phase = ≥50 events)
        total = conn.execute("SELECT COUNT(*) FROM confluence_events").fetchone()[0]
        if total >= 50:
            _send_confluence_alert(
                w.get("token_symbol") or "???",
                conf_score, whale_score, meme_score,
                w.get("market_cap_usd"),
                swt_wallets if swt_present else None,
            )


# ──────────────────────────────────────────────────────────────────────────────
# Outcome tracking
# ──────────────────────────────────────────────────────────────────────────────

def _update_outcomes(conn) -> None:
    """
    Fill in price_1h / price_4h / price_24h for PENDING confluence events.
    Marks outcome_status='COMPLETE' once price_24h is recorded.
    """
    now = datetime.now(timezone.utc)

    rows = conn.execute("""
        SELECT id, ts_utc, token_mint, price_at_event,
               price_1h, price_4h, price_24h
        FROM confluence_events
        WHERE outcome_status='PENDING' AND price_at_event IS NOT NULL AND price_at_event > 0
    """).fetchall()

    for row in rows:
        row = dict(row)
        try:
            alert_ts = datetime.strptime(row["ts_utc"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        except Exception:
            continue

        age_s = (now - alert_ts).total_seconds()
        entry = float(row["price_at_event"])

        needs_price = (
            (age_s >= 3600   and row["price_1h"]  is None) or
            (age_s >= 14400  and row["price_4h"]  is None) or
            (age_s >= 86400  and row["price_24h"] is None)
        )
        if not needs_price:
            continue

        current = _get_token_price(row["token_mint"])
        if current is None or current <= 0:
            continue

        updates: dict = {}

        if age_s >= 3600 and row["price_1h"] is None:
            updates["price_1h"] = current
            updates["return_1h_pct"] = round((current - entry) / entry * 100, 2)

        if age_s >= 14400 and row["price_4h"] is None:
            updates["price_4h"] = current
            updates["return_4h_pct"] = round((current - entry) / entry * 100, 2)

        if age_s >= 86400 and row["price_24h"] is None:
            updates["price_24h"] = current
            updates["return_24h_pct"] = round((current - entry) / entry * 100, 2)
            updates["outcome_status"] = "COMPLETE"

        if not updates:
            continue

        set_clause = ", ".join(f"{k}=?" for k in updates)
        conn.execute(
            f"UPDATE confluence_events SET {set_clause} WHERE id=?",
            list(updates.values()) + [row["id"]],
        )
        conn.commit()
        log.debug(
            "[CONF] outcome update id=%d: %s",
            row["id"], {k: v for k, v in updates.items() if "return" in k or "status" in k},
        )


# ──────────────────────────────────────────────────────────────────────────────
# Public entry point
# ──────────────────────────────────────────────────────────────────────────────

def confluence_step() -> None:
    """
    Called every 5 minutes from the monitor loop.
    Detects new confluences and updates pending outcomes.
    """
    from utils.db import get_conn  # type: ignore
    from utils import orchestrator  # type: ignore

    try:
        with get_conn() as conn:
            _detect_confluences(conn)
            _update_outcomes(conn)
        orchestrator.heartbeat("confluence_engine")
    except Exception as e:
        log.warning("[CONF] confluence_step error: %s", e)
