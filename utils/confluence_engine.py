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
_MCAP_FLOOR = 1_500_000
_MIN_LIQUIDITY = 50_000
_MIN_SCAN_COUNT_30D = 3


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

_CREATE_CONFLUENCE_EVENTS = """
CREATE TABLE IF NOT EXISTS confluence_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_utc TEXT NOT NULL,
    confluence_type TEXT NOT NULL,
    overlap_scope TEXT NOT NULL DEFAULT 'EXACT_MINT',
    token_symbol TEXT,
    token_mint TEXT,
    source_count INTEGER NOT NULL DEFAULT 0,
    sources TEXT,
    whale_alert_id INTEGER,
    memecoin_scan_id INTEGER,
    whale_score REAL,
    memecoin_score REAL,
    arkham_score REAL,
    arkham_signal_quality TEXT,
    arkham_entity_name TEXT,
    arkham_entity_type TEXT,
    arkham_net_flow_usd REAL,
    confluence_score REAL,
    market_cap_usd REAL,
    price_at_event REAL,
    alert_sent INTEGER NOT NULL DEFAULT 0,
    price_1h REAL,
    return_1h_pct REAL,
    price_4h REAL,
    return_4h_pct REAL,
    price_24h REAL,
    return_24h_pct REAL,
    outcome_status TEXT NOT NULL DEFAULT 'PENDING'
)
"""


def ensure_confluence_tables(conn) -> None:
    """Create confluence persistence tables if they do not exist yet."""
    conn.execute(_CREATE_CONFLUENCE_EVENTS)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_confluence_ts ON confluence_events(ts_utc DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_confluence_mint_ts ON confluence_events(token_mint, ts_utc DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_confluence_type_ts ON confluence_events(confluence_type, ts_utc DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_confluence_outcome ON confluence_events(outcome_status)"
    )
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(confluence_events)").fetchall()]
        for _col, _type in [
            ("arkham_score", "REAL"),
            ("arkham_signal_quality", "TEXT"),
            ("arkham_entity_name", "TEXT"),
            ("arkham_entity_type", "TEXT"),
            ("arkham_net_flow_usd", "REAL"),
            ("overlap_scope", "TEXT NOT NULL DEFAULT 'EXACT_MINT'"),
        ]:
            if _col not in cols:
                conn.execute(f"ALTER TABLE confluence_events ADD COLUMN {_col} {_type}")
    except Exception:
        pass

def _now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _get_token_price(mint: str) -> float | None:
    """Fetch current price from DexScreener individual token endpoint."""
    try:
        from data.dexscreener import fetch_token_pairs  # type: ignore

        pairs = fetch_token_pairs(mint, reason="confluence_token_price_429")
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


def _coin_quality_verdict(sig: dict) -> str:
    rug = str(sig.get("rug_label") or "").upper()
    top1 = float(sig.get("top_holder_pct") or 0)
    top5 = float(sig.get("top5_holder_pct") or 0)
    liq = float(sig.get("liquidity_usd") or 0)
    vol24 = float(sig.get("volume_24h") or 0)
    age = float(sig.get("token_age_days") or 0)
    hq = str(sig.get("holder_quality_level") or "").upper()
    vl = (vol24 / liq) if liq > 1000 else 0.0
    if rug == "DANGER" or top1 >= 80 or top5 >= 90 or (0 < liq < 30_000) or vl > 25:
        return "WEAK"
    if rug in ("WARN", "UNKNOWN") or (50 <= top1 < 80) or (70 <= top5 < 90) or (0 < liq < 75_000) or (0 < age < 7) or (10 < vl <= 25) or hq == "RISKY":
        return "QUESTIONABLE"
    return "CLEAR"


def _smart_wallet_context(conn, mint: str, cutoff: str) -> tuple[list[str], float]:
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
    except Exception as e:
        log.debug("[CONF] smart_wallet check error: %s", e)
    return swt_wallets, swt_sol


def _get_structural_memecoin_context(conn, mint: str, now: datetime) -> dict | None:
    """
    Weaker memecoin structural match used when there is no fresh scanner overlap:
    current whale signal + token still fits the memecoin focus universe / lifecycle.
    """
    cutoff_14d = (now - timedelta(days=14)).strftime("%Y-%m-%d %H:%M:%S")
    cutoff_30d = (now - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
    corr_cutoff = (now - timedelta(days=14)).strftime("%Y-%m-%d %H:%M:%S")

    latest = conn.execute("""
        SELECT id, symbol, mint, scanned_at, score, rug_label, liquidity_usd,
               top_holder_pct, top5_holder_pct, volume_24h, token_age_days,
               holder_quality_level, mcap_at_scan
        FROM memecoin_signal_outcomes
        WHERE source='SCANNER' AND mint=?
        ORDER BY scanned_at DESC
        LIMIT 1
    """, (mint,)).fetchone()
    if not latest:
        return None
    latest = dict(latest)
    if (latest.get("scanned_at") or "") < cutoff_14d:
        return None

    symbol = latest.get("symbol")
    if not symbol:
        return None

    scan_count = conn.execute("""
        SELECT COUNT(*) FROM memecoin_signal_outcomes
        WHERE source='SCANNER' AND symbol=? AND scanned_at >= ?
    """, (symbol, cutoff_30d)).fetchone()[0] or 0
    if scan_count < _MIN_SCAN_COUNT_30D:
        return None

    mcap = float(latest.get("mcap_at_scan") or 0)
    liq = float(latest.get("liquidity_usd") or 0)
    if mcap < _MCAP_FLOOR or liq < _MIN_LIQUIDITY:
        return None

    if _coin_quality_verdict(latest) == "WEAK":
        return None

    perf = conn.execute("""
        SELECT COUNT(*) AS n, ROUND(AVG(return_24h_pct), 1) AS avg_return
        FROM memecoin_signal_outcomes
        WHERE symbol=? AND status='COMPLETE' AND return_24h_pct IS NOT NULL
    """, (symbol,)).fetchone()
    perf_n = int(perf["n"] or 0)
    perf_avg = float(perf["avg_return"] or 0)
    if perf_n >= 15 and perf_avg < -10:
        return None

    corr = conn.execute("""
        SELECT COUNT(*) AS n, ROUND(AVG(return_24h_pct), 1) AS avg_return
        FROM memecoin_signal_outcomes
        WHERE symbol=? AND status='COMPLETE' AND return_24h_pct IS NOT NULL
          AND scanned_at >= ?
    """, (symbol, corr_cutoff)).fetchone()
    corr_n = int(corr["n"] or 0)
    corr_avg = float(corr["avg_return"] or 0)
    if corr_n >= 2 and corr_avg < -30.0:
        return None

    lc = conn.execute("""
        SELECT lifecycle_state, entry_window, fuel_quality, move_phase, vol_acc_current
        FROM symbol_lifecycle
        WHERE mint=?
        LIMIT 1
    """, (mint,)).fetchone()
    lc = dict(lc) if lc else {}

    lifecycle_ok = (
        lc.get("entry_window") in ("OPEN", "CLOSING")
        and lc.get("fuel_quality") in ("STRONG", "MODERATE")
        and lc.get("lifecycle_state") != "DEAD"
    )

    # Eligible-universe style structural fit OR strong live lifecycle context.
    if not lifecycle_ok and scan_count < _MIN_SCAN_COUNT_30D:
        return None

    structural_score = float(latest.get("score") or 0)
    if lifecycle_ok and lc.get("fuel_quality") == "STRONG":
        structural_score = min(100.0, structural_score + 5.0)

    return {
        "memecoin_scan_id": latest.get("id"),
        "symbol": symbol,
        "memecoin_score": round(structural_score, 1),
        "lifecycle_state": lc.get("lifecycle_state"),
        "entry_window": lc.get("entry_window"),
        "fuel_quality": lc.get("fuel_quality"),
        "move_phase": lc.get("move_phase"),
        "scan_count_30d": scan_count,
        "scanned_at": latest.get("scanned_at"),
    }


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
        SELECT id, token_symbol, token_mint, buy_amount_usd, market_cap_usd, price_at_alert,
               arkham_signal_score, arkham_signal_quality, arkham_top_entity_name,
               arkham_top_entity_type, arkham_net_flow_usd
        FROM whale_watch_alerts
        WHERE scanner_pass=1
          AND token_mint IS NOT NULL AND token_mint != ''
          AND ts_utc >= ?
    """, (cutoff,)).fetchall()

    if not whales:
        return

    mints_inserted_this_run: set[str] = set()

    for w in whales:
        w = dict(w)
        mint = w["token_mint"]

        if mint in mints_inserted_this_run:
            log.debug("[CONF] Dedup skip (in-run): %s", w.get("token_symbol"))
            continue

        existing = conn.execute(
            "SELECT id, confluence_type FROM confluence_events WHERE whale_alert_id=? ORDER BY id DESC LIMIT 1",
            (w["id"],)
        ).fetchone()
        existing = dict(existing) if existing else None

        dedup_cutoff_4h = (now - timedelta(hours=4)).strftime("%Y-%m-%d %H:%M:%S")
        recent_conf = conn.execute(
            "SELECT id, confluence_type FROM confluence_events WHERE token_mint=? AND ts_utc >= ? ORDER BY ts_utc DESC LIMIT 1",
            (mint, dedup_cutoff_4h),
        ).fetchone()
        recent_conf = dict(recent_conf) if recent_conf else None

        # Find matching GOOD memecoin scan in same 48h window
        # Note: memecoin_signal_outcomes uses 'mint' (not 'token_mint') and 'status'
        meme = conn.execute("""
            SELECT id, score FROM memecoin_signal_outcomes
            WHERE mint=?
              AND source='SCANNER'
              AND rug_label='GOOD'
              AND status IN ('PENDING', 'COMPLETE')
              AND scanned_at >= ?
            ORDER BY scanned_at DESC LIMIT 1
        """, (mint, cutoff)).fetchone()

        whale_score = float(w.get("buy_amount_usd") or 0)
        whale_norm = min(whale_score / 10_000 * 100, 100)
        arkham_score = float(w.get("arkham_signal_score") or 0)
        arkham_quality = str(w.get("arkham_signal_quality") or "NONE")
        arkham_boost = 0.0
        if arkham_quality == "HIGH":
            arkham_boost = 10.0
        elif arkham_quality == "MEDIUM":
            arkham_boost = 5.0
        elif arkham_quality == "LOW":
            arkham_boost = 2.5
        swt_wallets, swt_sol = _smart_wallet_context(conn, mint, cutoff)
        swt_present = len(swt_wallets) > 0
        swt_boost = min(swt_sol * 2, 20) if swt_present else 0.0

        if meme:
            meme = dict(meme)
            meme_score = float(meme.get("score") or 0)
            if arkham_score > 0:
                base_score = (whale_norm + meme_score + arkham_score) / 3
            else:
                base_score = (whale_norm + meme_score) / 2
            conf_score = round(base_score + swt_boost + arkham_boost, 1)
            sources = json.dumps(["whale_watch", "memecoin", "smart_wallet"] if swt_present
                                 else ["whale_watch", "memecoin"])
            source_count = 3 if swt_present else 2
            conf_type = "TRIPLE" if swt_present else "DUAL"

            if existing and existing.get("confluence_type") == "STRUCTURAL_DUAL":
                conn.execute("""
                    UPDATE confluence_events
                    SET ts_utc=?, sources=?, memecoin_scan_id=?, memecoin_score=?,
                        arkham_score=?, arkham_signal_quality=?, arkham_entity_name=?, arkham_entity_type=?, arkham_net_flow_usd=?,
                        confluence_score=?, confluence_type=?, source_count=?, overlap_scope=?
                    WHERE id=?
                """, (
                    now.strftime("%Y-%m-%d %H:%M:%S"),
                    sources,
                    meme["id"],
                    meme_score,
                    arkham_score,
                    arkham_quality,
                    w.get("arkham_top_entity_name"),
                    w.get("arkham_top_entity_type"),
                    w.get("arkham_net_flow_usd"),
                    conf_score,
                    conf_type,
                    source_count,
                    "EXACT_MINT",
                    existing["id"],
                ))
                conn.commit()
                log.info("[CONF] Upgraded STRUCTURAL_DUAL -> %s for %s mint=%s", conf_type, w.get("token_symbol"), mint)
            elif existing:
                continue
            else:
                if recent_conf:
                    log.debug("[CONF] Dedup skip (4h): %s existing=%s", w.get("token_symbol"), recent_conf.get("confluence_type"))
                    continue
                conn.execute("""
                    INSERT OR IGNORE INTO confluence_events
                      (ts_utc, token_symbol, token_mint, overlap_scope, sources,
                       whale_alert_id, memecoin_scan_id,
                       whale_score, memecoin_score, arkham_score, arkham_signal_quality,
                       arkham_entity_name, arkham_entity_type, arkham_net_flow_usd, confluence_score,
                       market_cap_usd, price_at_event,
                       confluence_type, source_count)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (
                    now.strftime("%Y-%m-%d %H:%M:%S"),
                    w.get("token_symbol") or "???",
                    mint,
                    "EXACT_MINT",
                    sources,
                    w["id"],
                    meme["id"],
                    whale_score,
                    meme_score,
                    arkham_score,
                    arkham_quality,
                    w.get("arkham_top_entity_name"),
                    w.get("arkham_top_entity_type"),
                    w.get("arkham_net_flow_usd"),
                    conf_score,
                    w.get("market_cap_usd"),
                    w.get("price_at_alert"),
                    conf_type,
                    source_count,
                ))
                conn.commit()
                mints_inserted_this_run.add(mint)
            log.info(
                "[CONF] New %s confluence: %s mint=%s conf_score=%.1f whale=$%.0f "
                "meme_score=%.0f arkham=%.1f swt=%d wallets",
                conf_type, w.get("token_symbol"), mint, conf_score,
                whale_score, meme_score, arkham_score, len(swt_wallets),
            )

            total = conn.execute("SELECT COUNT(*) FROM confluence_events").fetchone()[0]
            if total >= 50:
                _send_confluence_alert(
                    w.get("token_symbol") or "???",
                    conf_score, whale_score, meme_score,
                    w.get("market_cap_usd"),
                    swt_wallets if swt_present else None,
                )
            continue

        if existing:
            continue

        structural = _get_structural_memecoin_context(conn, mint, now)
        if not structural:
            continue

        if recent_conf:
            log.debug("[CONF] Structural dedup skip (4h): %s existing=%s", w.get("token_symbol"), recent_conf.get("confluence_type"))
            continue

        structural_score = float(structural.get("memecoin_score") or 0)
        if arkham_score > 0:
            base_score = (whale_norm + structural_score + arkham_score) / 3
        else:
            base_score = (whale_norm + structural_score) / 2
        conf_score = round(base_score * 0.85 + swt_boost + arkham_boost, 1)
        conf_type = "STRUCTURAL_TRIPLE" if swt_present else "STRUCTURAL_DUAL"
        sources = json.dumps(["whale_watch", "memecoin", "smart_wallet"] if swt_present
                             else ["whale_watch", "memecoin"])
        source_count = 3 if swt_present else 2

        conn.execute("""
            INSERT OR IGNORE INTO confluence_events
              (ts_utc, token_symbol, token_mint, overlap_scope, sources,
               whale_alert_id, memecoin_scan_id,
               whale_score, memecoin_score, arkham_score, arkham_signal_quality,
               arkham_entity_name, arkham_entity_type, arkham_net_flow_usd, confluence_score,
               market_cap_usd, price_at_event,
               confluence_type, source_count)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            now.strftime("%Y-%m-%d %H:%M:%S"),
            w.get("token_symbol") or structural.get("symbol") or "???",
            mint,
            "STRUCTURAL_CONTEXT",
            sources,
            w["id"],
            structural.get("memecoin_scan_id"),
            whale_score,
            structural_score,
            arkham_score,
            arkham_quality,
            w.get("arkham_top_entity_name"),
            w.get("arkham_top_entity_type"),
            w.get("arkham_net_flow_usd"),
            conf_score,
            w.get("market_cap_usd"),
            w.get("price_at_alert"),
            conf_type,
            source_count,
        ))
        conn.commit()
        mints_inserted_this_run.add(mint)

        log.info(
            "[CONF] New %s: %s mint=%s conf_score=%.1f whale=$%.0f structural_score=%.1f arkham=%.1f",
            conf_type, w.get("token_symbol"), mint, conf_score, whale_score, structural_score, arkham_score,
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
            ensure_confluence_tables(conn)
            _detect_confluences(conn)
            _update_outcomes(conn)
        orchestrator.heartbeat("confluence_engine")
    except Exception as e:
        log.warning("[CONF] confluence_step error: %s", e)
