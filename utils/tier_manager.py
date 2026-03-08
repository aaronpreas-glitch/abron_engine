"""
tier_manager.py — Tiered Leverage System (Patch 114)

Three tiers, all SOL LONG (configurable via env):
  3x:  $150 notional ($50 collateral) — diamond hands, NO auto-close, holds forever
  5x:  $100 notional ($20 collateral) — TP at +20% raw, auto re-enter after TP
  10x:  $70 notional ($7 collateral)  — TP at +10% raw, auto re-enter after TP or liquidation

Liquidation detection:
  Compare DB OPEN tier positions vs on-chain Jupiter positions.
  If OPEN in DB but gone from Jupiter → mark LIQUIDATED → re-enter (10x only).

Profit buffer:
  When 5x or 10x hits TP → PnL goes into kv_store['tier_profit_buffer'].
  When buffer >= PROFIT_BUFFER_3X_THRESHOLD ($50) → open new 3x.
  When buffer >= PROFIT_BUFFER_5X_THRESHOLD ($20) → open new 5x.
"""

import logging
import os
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import uuid as _uuid_mod

import requests

log = logging.getLogger(__name__)

DB_PATH               = "/root/memecoin_engine/data_storage/engine.db"
PERPS_API             = "https://perps-api.jup.ag/v2"
_LOCK_EXPIRY_S        = 90   # Patch 161: tier open lock TTL in seconds
_AMBIGUOUS_GUARD_HOURS = 24  # Patch 162: window for unresolved-intent guard

# Patch 162A: statuses that represent a completed (terminal) intent lifecycle step.
# _update_execution_intent() auto-sets resolved_ts only when transitioning to one of these.
_TERMINAL_STATUSES = frozenset({
    "SUBMIT_CONFIRMED",
    "RECONCILED_CONFIRMED",
    "RECONCILE_MANUAL_REQUIRED",
    "RECONCILED_FAILED",
    "BUILD_FAILED",
    "SIGN_FAILED",
    "BLOCKED_LOCK_HELD",
    "BLOCKED_PENDING_RECONCILIATION",
    "STALE_PENDING",
})

# Patch 166: statuses that block a new open for the same tier+symbol within the guard window.
# Single source of truth — used by _get_blocking_intents().  Mirrors _OPERATOR_VISIBLE_STATUSES
# in the tiers router (kept separate intentionally: router is for display, this is for safety).
_BLOCKING_STATUSES: frozenset = frozenset({
    "PENDING",
    "SUBMIT_AMBIGUOUS",
    "RECONCILE_MANUAL_REQUIRED",
})

# ── Env helpers ───────────────────────────────────────────────────────────────

def _f(key: str, default: float) -> float:
    return float(os.getenv(key, str(default)))

def _s(key: str, default: str) -> str:
    return os.getenv(key, default)


def _fetch_fear_greed() -> dict:
    """Delegate to agent_coordinator for shared 15-min cached F&G (Patch 120).

    Returns {value: int|None, label: str, favorable: bool}.
    favorable=False only on extreme fear (<= 25) — signals market panic,
    don't auto-deploy profit buffer then. Unknown → don't block (favorable=True).
    """
    try:
        from utils.agent_coordinator import get_fear_greed as _gfg  # type: ignore
        return _gfg()
    except Exception:
        return {"value": None, "label": "UNKNOWN", "favorable": True}


# ── Tier config ───────────────────────────────────────────────────────────────

def _tiers() -> dict:
    """
    Each tier uses a DIFFERENT market symbol so Jupiter creates separate positions.
    Jupiter only allows 1 position per market per wallet — using SOL/BTC/ETH keeps them independent.
    """
    return {
        "3x": {
            "symbol":   _s("TIER_3X_SYMBOL", "SOL"),   # anchor position — diamond hands
            "side":     "LONG",
            "leverage": _f("TIER_3X_LEVERAGE", 3),
            "notional": _f("TIER_3X_NOTIONAL", 150),
            "collateral": _f("TIER_3X_NOTIONAL", 150) / _f("TIER_3X_LEVERAGE", 3),
            "tp_pct":   None,   # diamond hands — never auto-close
            "reenter":  False,
        },
        "5x": {
            "symbol":   _s("TIER_5X_SYMBOL", "BTC"),   # BTC position
            "side":     "LONG",
            "leverage": _f("TIER_5X_LEVERAGE", 5),
            "notional": _f("TIER_5X_NOTIONAL", 100),
            "collateral": _f("TIER_5X_NOTIONAL", 100) / _f("TIER_5X_LEVERAGE", 5),
            "tp_pct":   _f("TIER_5X_TP_PCT", 20),
            "reenter":  True,
        },
        "10x": {
            "symbol":   _s("TIER_10X_SYMBOL", "ETH"),  # ETH position — most aggressive
            "side":     "LONG",
            "leverage": _f("TIER_10X_LEVERAGE", 10),
            "notional": _f("TIER_10X_NOTIONAL", 70),
            "collateral": _f("TIER_10X_NOTIONAL", 70) / _f("TIER_10X_LEVERAGE", 10),
            "tp_pct":   _f("TIER_10X_TP_PCT", 10),
            "reenter":  True,
        },
    }


# ── DB helpers ────────────────────────────────────────────────────────────────

def _get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10)  # Patch 163
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")       # Patch 163
    conn.execute("PRAGMA busy_timeout=5000")      # Patch 163
    # Ensure kv_store table exists
    conn.execute(
        "CREATE TABLE IF NOT EXISTS kv_store "
        "(key TEXT PRIMARY KEY, value TEXT)"
    )
    conn.commit()
    return conn


def _get_tier_positions(conn: sqlite3.Connection, tier_label: Optional[str] = None) -> list:
    """Return OPEN positions tagged with a tier label (or all tier positions if None)."""
    if tier_label:
        rows = conn.execute(
            "SELECT * FROM perp_positions WHERE status='OPEN' AND notes LIKE ?",
            (f"%TIER:{tier_label}%",),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM perp_positions WHERE status='OPEN' AND notes LIKE '%TIER:%'"
        ).fetchall()
    return [dict(r) for r in rows]


def _is_past_grace(pos: dict, now_utc: datetime, grace_min: int) -> bool:
    """Return True if position is older than the grace period (Patch 123)."""
    opened_str = pos.get("opened_ts_utc") or ""
    if not opened_str:
        return True   # no timestamp → assume past grace
    try:
        opened_ts = datetime.fromisoformat(opened_str.replace("Z", "+00:00"))
        return (now_utc - opened_ts).total_seconds() / 60 >= grace_min
    except Exception:
        return True


def _record_open(
    conn: sqlite3.Connection,
    symbol: str,
    side: str,
    tier_label: str,
    tier_cfg: dict,
    result: dict,
) -> int:
    """Insert a new OPEN position record for a tier position. Returns row id."""
    entry  = result.get("entry_price_usd", 0.0)
    liq    = result.get("liq_price_usd", 0.0)
    size   = result.get("size_usd", tier_cfg["notional"])
    jup_key = result.get("position_pubkey", "")
    tx_sig  = result.get("tx_sig", "")
    ts      = datetime.now(timezone.utc).isoformat()

    # TP prices (raw %, applied to entry)
    tp_pct  = tier_cfg["tp_pct"]
    tp1_price = None
    tp2_price = None
    if tp_pct and entry > 0:
        if side == "LONG":
            tp1_price = round(entry * (1 + tp_pct / 100), 6)
        else:
            tp1_price = round(entry * (1 - tp_pct / 100), 6)

    notes = f"TIER:{tier_label}"

    cur = conn.execute("""
        INSERT INTO perp_positions
            (opened_ts_utc, symbol, side, entry_price, stop_price, tp1_price,
             size_usd, leverage, collateral_usd, status, dry_run, notes,
             jupiter_position_key, tx_sig_open, regime_label)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', 0, ?, ?, ?, 'TIER')
    """, (
        ts, symbol, side, entry,
        liq if liq > 0 else 0.0,   # stop_price = liq price (Jupiter enforces)
        tp1_price,
        size,
        tier_cfg["leverage"],
        tier_cfg["collateral"],
        notes,
        jup_key,
        tx_sig,
    ))
    conn.commit()
    pos_id = cur.lastrowid
    log.info(
        "[TIER] Recorded OPEN %s %s %s entry=$%.4f collateral=$%.0f id=#%s",
        tier_label, symbol, side, entry, tier_cfg["collateral"], pos_id,
    )
    return pos_id


def _record_close(
    conn: sqlite3.Connection,
    pos: dict,
    exit_price: float,
    raw_pnl_pct: float,
    exit_reason: str,
    tx_sig: str = "",
    *,
    commit: bool = True,   # Patch 136: set False to batch with other writes before committing
) -> float:
    """Update a position as CLOSED. Returns pnl_usd."""
    leverage     = pos.get("leverage") or 1.0
    collateral   = pos.get("collateral_usd") or 0.0
    lev_pnl_pct  = round(raw_pnl_pct * leverage, 4)
    pnl_usd      = round((lev_pnl_pct / 100) * collateral, 4)
    ts           = datetime.now(timezone.utc).isoformat()

    conn.execute("""
        UPDATE perp_positions SET
            status='CLOSED', exit_price=?, pnl_pct=?, pnl_usd=?,
            exit_reason=?, closed_ts_utc=?, tx_sig_close=?
        WHERE id=?
    """, (exit_price, lev_pnl_pct, pnl_usd, exit_reason, ts, tx_sig, pos["id"]))
    if commit:
        conn.commit()
    log.info(
        "[TIER] Closed #%s %s pnl=%.2f%% ($%.4f) reason=%s",
        pos["id"], pos["symbol"], lev_pnl_pct, pnl_usd, exit_reason,
    )
    return pnl_usd


# ── Profit buffer ─────────────────────────────────────────────────────────────

def get_profit_buffer(conn: sqlite3.Connection) -> float:
    row = conn.execute(
        "SELECT value FROM kv_store WHERE key='tier_profit_buffer'"
    ).fetchone()
    return float(row[0]) if row else 0.0


def _add_to_buffer(conn: sqlite3.Connection, amount: float, *, commit: bool = True):  # Patch 136
    conn.execute("""
        INSERT INTO kv_store (key, value) VALUES ('tier_profit_buffer', ?)
        ON CONFLICT(key) DO UPDATE
            SET value = CAST(CAST(value AS REAL) + ? AS TEXT)
    """, (str(amount), amount))
    if commit:
        conn.commit()
    buf = get_profit_buffer(conn)
    log.info("[TIER] Profit buffer +$%.2f → total $%.2f", amount, buf)


def _spend_buffer(conn: sqlite3.Connection, amount: float):
    conn.execute("""
        INSERT INTO kv_store (key, value) VALUES ('tier_profit_buffer', ?)
        ON CONFLICT(key) DO UPDATE
            SET value = CAST(CAST(value AS REAL) - ? AS TEXT)
    """, (str(-amount), amount))
    conn.commit()


# ── Patch 161: Execution intent tracking + idempotent lock ────────────────────

def _acquire_tier_lock(conn: sqlite3.Connection, tier_label: str, symbol: str) -> "str | None":
    """Acquire exclusive lock for opening a tier position. Returns token on success, None if locked."""
    key = f"tier_open_lock_{tier_label}_{symbol.upper()}"
    now_dt      = datetime.now(timezone.utc)
    expires_dt  = now_dt + timedelta(seconds=_LOCK_EXPIRY_S)
    now_str     = now_dt.strftime("%Y-%m-%dT%H:%M:%S.%f+00:00")
    expires_str = expires_dt.strftime("%Y-%m-%dT%H:%M:%S.%f+00:00")
    token = _uuid_mod.uuid4().hex
    val   = f"{expires_str}|{token}"
    # Remove any expired lock first (SUBSTR comparison on fixed-width 32-char timestamps)
    conn.execute(
        "DELETE FROM kv_store WHERE key=? AND SUBSTR(value,1,32) < SUBSTR(?,1,32)",
        (key, now_str),
    )
    conn.commit()
    # Atomic acquire — INSERT OR IGNORE leaves existing row untouched
    conn.execute(
        "INSERT OR IGNORE INTO kv_store (key, value) VALUES (?, ?)",
        (key, val),
    )
    conn.commit()
    row = conn.execute("SELECT value FROM kv_store WHERE key=?", (key,)).fetchone()
    return token if (row and row[0] == val) else None


def _release_tier_lock(
    conn: sqlite3.Connection, tier_label: str, symbol: str, token: "str | None"
) -> None:
    """Release the tier open lock identified by token."""
    if not token:
        return
    key = f"tier_open_lock_{tier_label}_{symbol.upper()}"
    conn.execute("DELETE FROM kv_store WHERE key=? AND value LIKE ?", (key, f"%|{token}"))
    conn.commit()


def _insert_execution_intent(
    conn: sqlite3.Connection,
    tier_label: str,
    symbol: str,
    side: str,
    collateral_usd: float,
    leverage: float,
    *,
    status: str = "PENDING",
) -> int:
    """Insert an execution intent with the given status. Returns the new row id."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f+00:00")
    cur = conn.execute(
        """
        INSERT INTO tier_execution_intents
            (created_ts, tier_label, symbol, side, collateral_usd, leverage, status)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (ts, tier_label, symbol.upper(), side.upper(), collateral_usd, leverage, status),
    )
    conn.commit()
    return cur.lastrowid


def _get_blocking_intents(
    conn: sqlite3.Connection,
    tier_label: str,
    symbol: str,
) -> list:
    """
    Patch 162: Return unresolved intent rows that block a new open for tier_label+symbol.

    Blocking statuses (within _AMBIGUOUS_GUARD_HOURS window):
      PENDING                     — open call in progress or crashed mid-flight
      SUBMIT_AMBIGUOUS            — tx submitted but confirmation unknown
      RECONCILE_MANUAL_REQUIRED   — on-chain state unknown, operator must resolve

    Returns list of dicts (most recent first). Empty list = no block.
    """
    cutoff = (
        datetime.now(timezone.utc) - timedelta(hours=_AMBIGUOUS_GUARD_HOURS)
    ).strftime("%Y-%m-%dT%H:%M:%S.%f+00:00")
    _placeholders = ",".join(f"'{s}'" for s in sorted(_BLOCKING_STATUSES))
    rows = conn.execute(
        f"""
        SELECT * FROM tier_execution_intents
        WHERE tier_label = ?
          AND symbol     = ?
          AND status IN ({_placeholders})
          AND created_ts >= ?
        ORDER BY created_ts DESC
        """,
        (tier_label, symbol.upper(), cutoff),
    ).fetchall()
    return [dict(r) for r in rows]


def _expire_stale_pending_intents(
    conn: sqlite3.Connection,
    tier_label: str,
    symbol: str,
) -> None:
    """
    Patch 162A: Mark PENDING intents older than (_LOCK_EXPIRY_S + 30s) as STALE_PENDING.

    A PENDING row is inserted at the start of open_tier_position() and is normally
    resolved (to SUBMIT_CONFIRMED / SUBMIT_AMBIGUOUS / BUILD_FAILED / etc.) before the
    lock is released (~90s). If the process died between intent insert and the update
    call, the row stays PENDING indefinitely and would block future opens for 24h under
    the _get_blocking_intents() guard.

    Marking it STALE_PENDING removes it from the blocking set while preserving the
    audit trail. The threshold (120s) is intentionally > _LOCK_EXPIRY_S so a normally
    completing open — even a slow one — is never mis-expired.
    """
    stale_cutoff = (
        datetime.now(timezone.utc) - timedelta(seconds=_LOCK_EXPIRY_S + 30)
    ).strftime("%Y-%m-%dT%H:%M:%S.%f+00:00")
    resolved_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f+00:00")
    conn.execute(
        """
        UPDATE tier_execution_intents
           SET status = 'STALE_PENDING', resolved_ts = ?
         WHERE tier_label = ?
           AND symbol     = ?
           AND status     = 'PENDING'
           AND created_ts < ?
        """,
        (resolved_ts, tier_label, symbol.upper(), stale_cutoff),
    )
    conn.commit()


def _update_execution_intent(conn: sqlite3.Connection, intent_id: int, **kwargs) -> None:
    """Update arbitrary columns on a tier_execution_intents row.

    Patch 162A: resolved_ts is only set when transitioning to a terminal status
    (see _TERMINAL_STATUSES). It is NOT auto-set for PENDING or SUBMIT_AMBIGUOUS
    updates, since those represent in-progress states that may be updated again.
    """
    if not kwargs:
        return
    if "resolved_ts" not in kwargs and kwargs.get("status") in _TERMINAL_STATUSES:
        kwargs["resolved_ts"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f+00:00")
    cols = ", ".join(f"{k}=?" for k in kwargs)
    vals = list(kwargs.values()) + [intent_id]
    conn.execute(f"UPDATE tier_execution_intents SET {cols} WHERE id=?", vals)
    conn.commit()


def _alert_jupiter_api_down(reason: str) -> None:
    """Rate-limited Telegram when Jupiter positions API is unreachable. Patch 163/164.

    Fires at most once per 30 minutes. Uses persistent kv_store so cooldown
    survives service restarts (Patch 164 upgrade from in-memory should_rate_limit).
    Covers both liquidation detection and reconcile path.
    """
    from utils.db import persistent_rate_limit_check  # Patch 164
    from utils.telegram_alerts import send_telegram_sync  # noqa
    if persistent_rate_limit_check("jup_api_down", 1800):
        return
    send_telegram_sync(
        "Jupiter API Down",
        f"Could not fetch open positions from Jupiter.\n"
        f"Liquidation detection and reconcile <b>skipped this cycle</b>.\n"
        f"<code>{reason[:300]}</code>",
        "🔴",
    )


def _alert_manual_required(intent_id: int, detail: str) -> None:
    """Fire a Telegram alert for RECONCILE_MANUAL_REQUIRED (persistent rate limit per intent).

    Patch 164: upgraded from in-memory should_rate_limit to persistent kv_store check
    so the alert re-fires after a restart if the intent is still unresolved.
    """
    try:
        from utils.db import persistent_rate_limit_check  # Patch 164
        from utils.telegram_alerts import send_telegram_sync  # type: ignore
        if not persistent_rate_limit_check(f"tier_reconcile_manual_{intent_id}", 3600):
            send_telegram_sync(
                "Tier Open — Manual Review Required ⚠️",
                f"Intent #{intent_id}: {detail}\n"
                f"Check tier_execution_intents table and Jupiter dashboard.",
                "⚠️",
            )
    except Exception:
        pass


def _reconcile_ambiguous_intents(conn: sqlite3.Connection) -> None:
    """
    Patch 162: Reconcile SUBMIT_AMBIGUOUS intents against live Jupiter positions API.

    Rules:
      1. Fetch current Jupiter open positions for the wallet (one API call per cycle).
         If the API call fails, skip all reconciliation this cycle — do not escalate.
      2. If intent.position_pubkey IS in the live Jupiter set:
         a. If a matching OPEN perp_positions row exists → RECONCILED_CONFIRMED.
         b. If no matching row → RECONCILE_MANUAL_REQUIRED + Telegram alert.
            (Position confirmed on-chain but not tracked locally. Do NOT auto-insert.)
      3. If intent.position_pubkey is NOT in the live Jupiter set (or pubkey is empty):
         - age >= 5 min → RECONCILED_FAILED (tx did not land within confirmation window).
         - age < 5 min  → leave as SUBMIT_AMBIGUOUS (still within confirmation window).
      No writes to perp_positions during reconciliation.
    """
    rows = conn.execute(
        "SELECT * FROM tier_execution_intents WHERE status='SUBMIT_AMBIGUOUS'",
    ).fetchall()
    if not rows:
        return

    # Fetch live Jupiter positions once — shared across all intents this cycle
    from utils.jupiter_perps_trade import get_wallet_address  # local import
    wallet   = get_wallet_address()
    jup_keys = _get_jupiter_position_keys(wallet)
    if jup_keys is None:
        # API error or wallet not configured — defer; never escalate on uncertainty
        log.warning("[TIER] Reconcile: Jupiter API unavailable — deferring reconciliation")
        return

    now_dt = datetime.now(timezone.utc)
    for row in rows:
        row       = dict(row)
        intent_id = row["id"]
        pubkey    = row.get("position_pubkey") or ""
        created   = row.get("created_ts") or ""
        try:
            created_dt = datetime.fromisoformat(created)
            age_s = (now_dt - created_dt).total_seconds()
        except Exception as _ts_err:
            log.warning(  # Patch 164: was silent bare-except; now surfaces bad timestamps
                "[TIER] Reconcile: could not parse created_ts %r for intent #%s: %s — treating as old",
                created, intent_id, _ts_err,
            )
            age_s = 9999  # unknown age → treat as old

        if pubkey and pubkey in jup_keys:
            # ── Position confirmed live on Jupiter ────────────────────────────
            match = conn.execute(
                "SELECT id FROM perp_positions WHERE jupiter_position_key=? AND status='OPEN'",
                (pubkey,),
            ).fetchone()
            if match:
                # DB is consistent — local row exists and position is open
                _update_execution_intent(
                    conn, intent_id,
                    status="RECONCILED_CONFIRMED",
                    perp_position_id=match[0],
                )
                log.info(
                    "[TIER] Intent #%s → RECONCILED_CONFIRMED "
                    "(Jupiter confirmed, perp_pos=#%s)",
                    intent_id, match[0],
                )
            else:
                # On Jupiter but no local DB row — operator must inspect and add manually
                _update_execution_intent(conn, intent_id, status="RECONCILE_MANUAL_REQUIRED")
                detail = (
                    f"pubkey={pubkey} confirmed on Jupiter but no matching OPEN "
                    f"perp_positions row. tier={row.get('tier_label')}, "
                    f"symbol={row.get('symbol')}, age={age_s:.0f}s. "
                    f"Inspect Jupiter dashboard and insert DB row manually if needed."
                )
                log.warning(
                    "[TIER] Intent #%s → RECONCILE_MANUAL_REQUIRED: %s",
                    intent_id, detail,
                )
                _alert_manual_required(intent_id, detail)
        else:
            # ── pubkey not found on Jupiter (or pubkey was empty) ─────────────
            if age_s >= 300:  # 5 minutes — past on-chain confirmation window
                _update_execution_intent(conn, intent_id, status="RECONCILED_FAILED")
                log.info(
                    "[TIER] Intent #%s → RECONCILED_FAILED "
                    "(pubkey=%s not on Jupiter, age=%.0fs)",
                    intent_id, pubkey or "empty", age_s,
                )
            # age < 5 min: leave as SUBMIT_AMBIGUOUS — still in confirmation window


# ── Price fetch ───────────────────────────────────────────────────────────────

_KRAKEN_PAIRS  = {"SOL": "SOLUSD", "BTC": "XBTUSD", "ETH": "ETHUSD"}
_CG_FALLBACK   = {"SOL": "solana", "BTC": "bitcoin", "ETH": "ethereum"}   # Patch 136

def _fetch_price(symbol: str) -> float:
    """Fetch current USD price for symbol. Primary: Kraken. Fallback: CoinGecko."""  # Patch 136
    pair = _KRAKEN_PAIRS.get(symbol.upper(), "SOLUSD")
    try:
        r = requests.get(
            f"https://api.kraken.com/0/public/Ticker?pair={pair}", timeout=8
        )
        data = r.json()
        return float(list(data["result"].values())[0]["c"][0])
    except Exception as e:
        log.warning("[TIER] Kraken price failed for %s: %s — trying CoinGecko", symbol, e)

    # Patch 136 — CoinGecko fallback so TP checks survive Kraken outages
    cg_id = _CG_FALLBACK.get(symbol.upper(), "")
    if not cg_id:
        return 0.0
    try:
        r2 = requests.get(
            f"https://api.coingecko.com/api/v3/simple/price?ids={cg_id}&vs_currencies=usd",
            timeout=10,
        )
        price = float(r2.json()[cg_id]["usd"])
        log.info("[TIER] CoinGecko fallback price for %s: $%.4f", symbol, price)
        return price
    except Exception as e2:
        log.warning("[TIER] CoinGecko fallback also failed for %s: %s", symbol, e2)
        return 0.0


# ── Jupiter position lookup (liquidation detection) ───────────────────────────

def _get_jupiter_position_keys(wallet: str) -> Optional[set]:
    """
    Fetch all open position pubkeys from Jupiter for `wallet`.
    Returns set of pubkeys, or None if the API call failed (don't liquidate on error).

    Jupiter v2 API returns: {"dataList": [...positions...], "count": N}
    Each position may have positionPubkey, address, pubkey, etc.
    """
    if not wallet:
        return None
    try:
        r = requests.get(
            f"{PERPS_API}/positions?walletAddress={wallet}",
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        # Handle all known Jupiter v2 response shapes
        if isinstance(data, list):
            positions = data
        else:
            positions = (
                data.get("dataList")          # Jupiter v2 actual key
                or data.get("positions")
                or data.get("data")
                or []
            )
        keys = set()
        for p in positions:
            pk = (
                p.get("positionPubkey")
                or p.get("address")
                or p.get("position_pubkey")
                or p.get("pubkey")
            )
            if pk:
                keys.add(pk)
        log.info("[TIER] Jupiter shows %d open positions for wallet (count=%s)", len(keys), data.get("count", "?") if isinstance(data, dict) else "?")
        return keys
    except Exception as e:
        log.warning("[TIER] Could not fetch Jupiter positions: %s", e)
        _alert_jupiter_api_down(str(e))  # Patch 163: rate-limited Telegram
        return None


# ── Open a tier position ──────────────────────────────────────────────────────

def open_tier_position(
    tier_label: str,
    symbol: str = None,
    side: str = None,
) -> dict:
    """Open a live Jupiter perp position for the given tier. Writes DB record.

    Patch 161: idempotent — DB-backed lock (90s TTL) + execution intent table.
    Callers check result.get('ok') or result.get('success') (both kept for compat).
    """
    from utils.jupiter_perps_trade import open_perp_sync  # local import

    cfg    = _tiers()[tier_label]
    # Each tier has its own symbol (SOL/BTC/ETH) — overrides are allowed but discouraged
    # Jupiter only allows 1 position per market, so different symbols = truly separate positions
    symbol = symbol or cfg["symbol"]
    side   = side   or cfg["side"]

    conn  = _get_db()
    token = None
    try:
        # ── Acquire lock (90s TTL, per tier+symbol) ───────────────────────────
        token = _acquire_tier_lock(conn, tier_label, symbol)
        if not token:
            log.warning("[TIER] %s %s lock held — aborting duplicate open", tier_label, symbol)
            # Patch 162: record lock-blocked attempts
            _insert_execution_intent(
                conn, tier_label, symbol, side, cfg["collateral"], cfg["leverage"],
                status="BLOCKED_LOCK_HELD",
            )
            return {
                "ok": False, "success": False, "state": "BLOCKED_LOCK_HELD",
                "error": "Lock held — another open in progress for this tier+symbol",
                "presigned_tx_sig": None, "position_pubkey": None, "tx_sig": None,
                "entry_price_usd": 0.0, "size_usd": 0.0, "liq_price_usd": 0.0,
                "response_body_excerpt": None,
            }

        # ── Patch 162A: expire stale PENDING intents before guard check ─────
        _expire_stale_pending_intents(conn, tier_label, symbol)

        # ── Patch 162: guard — block if unresolved ambiguous intent exists ────
        blocking = _get_blocking_intents(conn, tier_label, symbol)
        if blocking:
            blocking_ids = [r["id"] for r in blocking]
            log.warning(
                "[TIER] %s %s blocked — unresolved intents exist: %s",
                tier_label, symbol, blocking_ids,
            )
            guard_id = _insert_execution_intent(
                conn, tier_label, symbol, side, cfg["collateral"], cfg["leverage"],
                status="BLOCKED_PENDING_RECONCILIATION",
            )
            _update_execution_intent(
                conn, guard_id,
                error_detail=f"Blocked by unresolved intents: {blocking_ids}",
            )
            return {
                "ok": False, "success": False, "state": "BLOCKED_PENDING_RECONCILIATION",
                "error": (
                    f"Blocked — unresolved intent(s) {blocking_ids} exist for "
                    f"{tier_label} {symbol}. Resolve before opening."
                ),
                "presigned_tx_sig": None, "position_pubkey": None, "tx_sig": None,
                "entry_price_usd": 0.0, "size_usd": 0.0, "liq_price_usd": 0.0,
                "response_body_excerpt": None,
            }

        # ── Regime advisory ───────────────────────────────────────────────────
        fg = _fetch_fear_greed()
        if not fg["favorable"]:
            try:
                from utils import orchestrator as _orch  # type: ignore
                _orch.append_memory(
                    "TIER_MANAGER",
                    f"[REGIME ADVISORY] Opening {tier_label} {symbol} {side} in extreme fear — "
                    f"F&G={fg['value']} ({fg['label']}). Proceeding as manually authorized.",
                )
            except Exception:
                pass

        log.info(
            "[TIER] Opening %s %s %s | notional=$%.0f collateral=$%.0f lev=%.0fx | F&G=%s",
            tier_label, symbol, side, cfg["notional"], cfg["collateral"], cfg["leverage"],
            fg["value"] if fg["value"] is not None else "?",
        )

        # ── Insert intent (PENDING) ───────────────────────────────────────────
        intent_id = _insert_execution_intent(
            conn, tier_label, symbol, side, cfg["collateral"], cfg["leverage"],
        )

        # ── Call Jupiter Perps ────────────────────────────────────────────────
        result = open_perp_sync(
            symbol=symbol,
            side=side,
            collateral_usd=cfg["collateral"],
            leverage=cfg["leverage"],
            dry_run=False,
        )

        state = result.get("state", "")
        if state == "SUBMIT_CONFIRMED":
            pos_id = _record_open(conn, symbol, side, tier_label, cfg, result)
            _update_execution_intent(
                conn, intent_id,
                status="SUBMIT_CONFIRMED",
                presigned_tx_sig=result.get("presigned_tx_sig"),
                position_pubkey=result.get("position_pubkey"),
                tx_sig_confirmed=result.get("tx_sig"),
                perp_position_id=pos_id,
            )
            log.info("[TIER] ✅ %s opened — tx=%s", tier_label, result.get("tx_sig", ""))
        elif state == "SUBMIT_AMBIGUOUS":
            _update_execution_intent(
                conn, intent_id,
                status="SUBMIT_AMBIGUOUS",
                presigned_tx_sig=result.get("presigned_tx_sig"),
                position_pubkey=result.get("position_pubkey"),
                error_detail=result.get("error"),
                build_response_excerpt=result.get("response_body_excerpt"),
            )
            log.error(
                "[TIER] ⚠️ %s open AMBIGUOUS (may have landed on-chain) — intent #%s: %s",
                tier_label, intent_id, result.get("error"),
            )
        else:  # BUILD_FAILED, SIGN_FAILED, DRY_RUN, or unknown
            _update_execution_intent(
                conn, intent_id,
                status=state or "SUBMIT_FAILED",
                error_detail=result.get("error"),
                build_response_excerpt=result.get("response_body_excerpt"),
            )
            log.error("[TIER] ❌ %s open failed (%s): %s", tier_label, state, result.get("error"))

        return result
    finally:
        _release_tier_lock(conn, tier_label, symbol, token)
        conn.close()


def open_all_tiers(symbol: str = None, side: str = None) -> dict:
    """Open 3x (SOL), 5x (BTC), and 10x (ETH) positions — each on a different market."""
    results = {}
    for tier_label in ("3x", "5x", "10x"):
        # symbol/side can be overridden but defaults come from tier config
        results[tier_label] = open_tier_position(tier_label, symbol=symbol, side=side)
        time.sleep(2)   # small delay between opens to avoid rate limits
    return results


# ── Monitor step ──────────────────────────────────────────────────────────────

def tier_monitor_step():
    """
    Main tier monitoring step. Call from main loop every ~30-60s.

    1. Detect liquidations → mark LIQUIDATED in DB → re-enter 10x
    2. Check TP conditions for 5x and 10x → close → re-enter
    3. Check profit buffer → open new 3x/5x if threshold reached
    """
    # Patch 120 — heartbeat so orchestrator shows tier_monitor as alive
    try:
        from utils import orchestrator as _orch  # type: ignore
        _orch.heartbeat("tier_monitor")
    except Exception:
        pass

    from utils.jupiter_perps_trade import close_perp_sync, get_wallet_address  # local import

    conn = _get_db()
    try:
        # Patch 161: reconcile any SUBMIT_AMBIGUOUS intents first
        _reconcile_ambiguous_intents(conn)

        tiers    = _tiers()
        wallet   = get_wallet_address()

        # ── 1. Liquidation detection ─────────────────────────────────────────
        # Grace period: only liquidate if position opened > 10 minutes ago
        # Prevents false positives during on-chain confirmation delay
        LIQUIDATION_GRACE_MINUTES = 10

        jup_keys = _get_jupiter_position_keys(wallet)
        if jup_keys is not None:   # None = API error, skip this cycle
            all_tier_positions = _get_tier_positions(conn)
            now_utc = datetime.now(timezone.utc)

            # Symbol-level liquidation detection (Patch 123):
            # Group DB rows by symbol. A symbol is liquidated only if ALL its
            # Jupiter keys are gone AND all rows are past the grace period.
            # This prevents false positives from stacked rows with stale keys.
            symbol_map: dict = {}
            for pos in all_tier_positions:
                symbol_map.setdefault(pos["symbol"], []).append(pos)

            for sym, sym_positions in symbol_map.items():
                row_keys = {p["jupiter_position_key"] for p in sym_positions
                            if p.get("jupiter_position_key")}
                if row_keys & jup_keys:
                    continue   # at least one key still on-chain — not liquidated
                if not all(_is_past_grace(p, now_utc, LIQUIDATION_GRACE_MINUTES) for p in sym_positions):
                    continue   # some rows still in grace period — skip

                # Patch 148: Price-based liquidation verification.
                # When a position closes via TP, manual exit, or external close, its
                # pubkeys go stale but the price is far from the liquidation level.
                # Fetch current price and compare to stored liq price (stop_price).
                # If price is >5% above liq → NOT a real liquidation → EXTERNAL_CLOSE.
                # If price fetch fails or liq price not stored → proceed with LIQUIDATED (safe default).
                ts = datetime.now(timezone.utc).isoformat()
                first = sym_positions[0]
                liq_price  = first.get("stop_price") or 0.0
                is_liquidation = True   # default: assume liquidated unless proven otherwise

                if liq_price > 0:
                    try:
                        current_price = _fetch_price(sym)
                        if current_price > 0:
                            side = first.get("side", "LONG")
                            if side == "LONG":
                                liq_buffer_pct = (current_price - liq_price) / current_price * 100
                            else:
                                liq_buffer_pct = (liq_price - current_price) / current_price * 100
                            if liq_buffer_pct > 5.0:
                                # Price is significantly above liq → almost certainly a TP/manual exit
                                log.warning(
                                    "[TIER] %s: position gone from Jupiter but price $%.2f is %.1f%% "
                                    "above liq $%.2f — marking EXTERNAL_CLOSE, NOT LIQUIDATED (Patch 148)",
                                    sym, current_price, liq_buffer_pct, liq_price,
                                )
                                is_liquidation = False
                    except Exception as _pe:
                        log.warning("[TIER] %s: price fetch failed during liq check: %s — assuming LIQUIDATED", sym, _pe)

                if not is_liquidation:
                    # External close (TP fired, manual exit, etc.) — clean up stale DB rows, NO re-entry
                    for pos in sym_positions:
                        conn.execute("""
                            UPDATE perp_positions SET
                                status='CLOSED', exit_reason='EXTERNAL_CLOSE',
                                closed_ts_utc=?, pnl_pct=0.0, pnl_usd=0.0
                            WHERE id=?
                        """, (ts, pos["id"]))
                        log.info("[TIER] %s #%s → EXTERNAL_CLOSE (gone from Jupiter, price ok)", sym, pos["id"])
                    conn.commit()
                    # Telegram alert so user knows the engine detected and cleaned up
                    try:
                        from utils.telegram_alerts import send_telegram_sync, should_rate_limit  # type: ignore
                        if not should_rate_limit(f"ext_close_{sym}", 3600):
                            send_telegram_sync(
                                f"⚠️ {sym} External Close Detected",
                                (
                                    f"Position gone from Jupiter — NOT a liquidation.\n"
                                    f"Price ${current_price:,.2f} | Liq was ${liq_price:,.2f}\n"
                                    f"Marked EXTERNAL_CLOSE. No auto re-entry. Check manually."
                                ),
                                "⚠️",
                            )
                    except Exception:
                        pass
                else:
                    # Genuine liquidation — mark and re-enter 10x if applicable
                    for pos in sym_positions:
                        log.warning("[TIER] %s #%s → LIQUIDATED (symbol-level, Patch 148)", sym, pos["id"])
                        collateral = pos.get("collateral_usd") or 0.0
                        conn.execute("""
                            UPDATE perp_positions SET
                                status='LIQUIDATED', exit_reason='LIQUIDATED',
                                closed_ts_utc=?, pnl_pct=-100.0, pnl_usd=?
                            WHERE id=?
                        """, (ts, -collateral, pos["id"]))
                    conn.commit()
                    # Re-enter 10x if the first row was a 10x tier
                    if any("TIER:10x" in (p.get("notes") or "") for p in sym_positions):
                        log.info("[TIER] Re-entering 10x after confirmed liquidation of %s", sym)
                        open_tier_position("10x", symbol=first["symbol"], side=first["side"])

        # ── 2. TP checks for 5x and 10x ─────────────────────────────────────
        for tier_label in ("5x", "10x"):
            cfg = tiers[tier_label]
            tp_pct = cfg["tp_pct"]
            if not tp_pct:
                continue  # TP disabled for this tier (TIER_Nx_TP_PCT=0) — Patch 148
            positions = _get_tier_positions(conn, tier_label)
            closed_symbols: set = set()  # Patch 126 — prevent redundant closes on stacked rows

            for pos in positions:
                symbol = pos["symbol"]
                if symbol in closed_symbols:
                    continue  # already TP'd this symbol this cycle — skip stacked duplicates
                side   = pos["side"]
                entry  = pos.get("entry_price", 0.0)
                if not entry:
                    continue

                price = _fetch_price(symbol)
                if not price:
                    continue

                # Raw PnL (no leverage)
                if side == "LONG":
                    raw_pnl = (price - entry) / entry * 100
                else:
                    raw_pnl = (entry - price) / entry * 100

                if raw_pnl >= tp_pct:
                    log.info(
                        "[TIER] %s TP hit on #%s %s %s: raw_pnl=%.2f%% >= %.0f%%",
                        tier_label, pos["id"], symbol, side, raw_pnl, tp_pct,
                    )
                    jup_key = pos.get("jupiter_position_key", "")
                    close_result = close_perp_sync(
                        jup_key, dry_run=False, symbol=symbol
                    )
                    if close_result.get("success"):
                        closed_symbols.add(symbol)  # Patch 126 — guard rest of list

                        # Patch 136: atomic transaction — close primary + all stacked extras
                        # before touching the buffer. If any write fails, nothing commits.
                        pnl_usd = _record_close(
                            conn, pos, price, raw_pnl, "TIER_TP",
                            tx_sig=close_result.get("tx_sig", ""),
                            commit=False,   # deferred
                        )
                        # Collect and close all stacked extras in the same transaction
                        extras = conn.execute(
                            "SELECT * FROM perp_positions "
                            "WHERE status='OPEN' AND notes LIKE ? AND symbol=? AND id != ?",
                            (f"%TIER:{tier_label}%", symbol, pos["id"]),
                        ).fetchall()
                        ep_pnls: list[float] = []
                        for ep in extras:
                            ep_pnl = _record_close(
                                conn, dict(ep), price, raw_pnl, "TIER_TP_STACKED", "",
                                commit=False,  # deferred
                            )
                            ep_pnls.append(ep_pnl or 0.0)
                        # Patch 161: atomic — close records + buffer credits in one commit
                        if pnl_usd and pnl_usd > 0:
                            _add_to_buffer(conn, pnl_usd, commit=False)
                        for ep_pnl in ep_pnls:
                            if ep_pnl > 0:
                                _add_to_buffer(conn, ep_pnl, commit=False)
                        conn.commit()  # single commit: closes + buffer credits atomic

                        # Telegram TP alert — Patch 136: richer message with entry/exit/buffer
                        try:
                            from utils.telegram_alerts import send_telegram_sync, should_rate_limit  # type: ignore
                            if not should_rate_limit("tier_tp", 60):
                                buf_after = get_profit_buffer(conn)
                                reenter_note = f"\nRe-entering {tier_label} now…" if cfg["reenter"] else ""
                                send_telegram_sync(
                                    f"Tier {tier_label} TP 🎯",
                                    (
                                        f"{symbol} {side}\n"
                                        f"Entry: ${entry:,.4f} → Exit: ${price:,.4f}\n"
                                        f"Raw: +{raw_pnl:.2f}% | Profit: +${pnl_usd:.2f}\n"
                                        f"Buffer now: ${buf_after:.2f}"
                                        f"{reenter_note}"
                                    ),
                                    "🎯",
                                )
                        except Exception:
                            pass

                        # Re-enter same tier (5x and 10x auto re-enter)
                        if cfg["reenter"]:
                            log.info("[TIER] Re-entering %s after TP", tier_label)
                            time.sleep(2)
                            open_tier_position(tier_label, symbol=symbol, side=side)
                    else:
                        log.error(
                            "[TIER] Failed to close #%s: %s",
                            pos["id"], close_result.get("error"),
                        )

        # ── 2.5. Liq proximity alert (Patch 142) ──────────────────────────────
        # Warn via Telegram when any tier position is within 15% of liquidation.
        # Rate-limited per symbol to once per 2 hours — avoids flooding every cycle.
        _liq_price_cache: dict = {}
        for _lp in _get_tier_positions(conn):
            _sym  = _lp["symbol"]
            _liq  = _lp.get("stop_price") or 0.0
            if not _liq:
                continue
            if _sym not in _liq_price_cache:
                _liq_price_cache[_sym] = _fetch_price(_sym)
            _price = _liq_price_cache[_sym]
            if not _price:
                continue
            _side = _lp.get("side", "LONG")
            _buf  = (
                (_price - _liq) / _price * 100 if _side == "LONG"
                else (_liq - _price) / _price * 100
            )
            if 0 < _buf < 15:
                _tier_lbl = (_lp.get("notes") or "").replace("TIER:", "").strip()
                try:
                    from utils.telegram_alerts import send_telegram_sync, should_rate_limit  # type: ignore
                    if not should_rate_limit(f"liq_prox_{_sym}", 7200):
                        send_telegram_sync(
                            f"⚠️ {_sym} Near Liquidation",
                            (
                                f"Tier {_tier_lbl} {_sym} {_side}\n"
                                f"Price: ${_price:,.2f} | Liq: ${_liq:,.2f}\n"
                                f"Buffer: {_buf:.1f}% to liquidation"
                            ),
                            "⚠️",
                        )
                        log.warning(
                            "[TIER] Liq proximity: %s tier=%s price=$%.2f liq=$%.2f buf=%.1f%%",
                            _sym, _tier_lbl, _price, _liq, _buf,
                        )
                except Exception:
                    pass

        # ── 3. Profit buffer → new positions ─────────────────────────────────
        buf       = get_profit_buffer(conn)
        thresh_3x = _f("PROFIT_BUFFER_3X_THRESHOLD", 33)
        thresh_5x = _f("PROFIT_BUFFER_5X_THRESHOLD", 20)
        # Patch 129: do NOT override symbol/side — let open_tier_position use
        # each tier's own configured symbol (SOL for 3x, BTC for 5x, ETH for 10x).
        # The old TIER_SYMBOL override incorrectly opened all buffer deploys on SOL.

        if buf >= thresh_3x or buf >= thresh_5x:
            # Check market sentiment — hold buffer in extreme fear (F&G <= 25)
            fg = _fetch_fear_greed()
            if not fg["favorable"]:
                log.info(
                    "[TIER] Buffer $%.2f ready but F&G=%s (%s) — holding (extreme fear)",
                    buf, fg["value"], fg["label"],
                )
                try:
                    from utils import orchestrator as _orch  # type: ignore
                    _orch.append_memory(
                        "TIER_MANAGER",
                        f"Profit buffer ${buf:.2f} available. Holding auto-open — "
                        f"F&G={fg['value']} ({fg['label']}) extreme fear detected.",
                    )
                except Exception:
                    pass
            elif buf >= thresh_3x:
                log.info(
                    "[TIER] Profit buffer $%.2f ≥ $%.0f → opening new 3x (F&G=%s)",
                    buf, thresh_3x, fg["value"],
                )
                result = open_tier_position("3x")
                if result.get("success"):
                    _spend_buffer(conn, thresh_3x)
                    # Telegram buffer deploy alert (Patch 121)
                    try:
                        from utils.telegram_alerts import send_telegram_sync, should_rate_limit  # type: ignore
                        if not should_rate_limit("tier_buffer", 60):
                            send_telegram_sync(
                                "Tier Buffer Deployed 🚀",
                                f"Opened new 3x from profit buffer (was ${buf:.2f})",
                                "🚀",
                            )
                    except Exception:
                        pass
            elif buf >= thresh_5x:
                log.info(
                    "[TIER] Profit buffer $%.2f ≥ $%.0f → opening new 5x (F&G=%s)",
                    buf, thresh_5x, fg["value"],
                )
                result = open_tier_position("5x")
                if result.get("success"):
                    _spend_buffer(conn, thresh_5x)
                    # Telegram buffer deploy alert (Patch 121)
                    try:
                        from utils.telegram_alerts import send_telegram_sync, should_rate_limit  # type: ignore
                        if not should_rate_limit("tier_buffer", 60):
                            send_telegram_sync(
                                "Tier Buffer Deployed 🚀",
                                f"Opened new 5x from profit buffer (was ${buf:.2f})",
                                "🚀",
                            )
                    except Exception:
                        pass

    except Exception as e:
        log.error("[TIER] tier_monitor_step error: %s", e, exc_info=True)
    finally:
        conn.close()


# ── Status summary (for API endpoint) ────────────────────────────────────────

def tier_status() -> dict:
    """
    Return a JSON-serializable summary for the /api/tiers/status endpoint.
    """
    conn = _get_db()
    try:
        tiers    = _tiers()
        buffer   = get_profit_buffer(conn)
        positions = _get_tier_positions(conn)

        # Prices
        symbols = {p["symbol"] for p in positions}
        prices  = {sym: _fetch_price(sym) for sym in symbols}

        tier_summary = {}
        for tier_label, cfg in tiers.items():
            tier_positions = [
                p for p in positions
                if f"TIER:{tier_label}" in (p.get("notes") or "")
            ]
            # Group stacked rows by symbol — show as one card with summed collateral (Patch 123)
            symbol_groups: dict = {}
            for p in tier_positions:
                symbol_groups.setdefault(p["symbol"], []).append(p)

            enriched = []
            for sym, rows in symbol_groups.items():
                rows_sorted = sorted(rows, key=lambda r: r.get("opened_ts_utc") or "")
                first  = rows_sorted[0]
                latest = rows_sorted[-1]
                entry  = first.get("entry_price", 0.0) or 0.0
                total_col = sum(r.get("collateral_usd") or cfg["collateral"] for r in rows_sorted)
                price  = prices.get(sym, 0.0)
                side   = first["side"]

                raw_pnl = lev_pnl = pnl_usd = 0.0
                if entry and price:
                    raw_pnl = (price - entry) / entry * 100 if side == "LONG" else (entry - price) / entry * 100
                    lev_pnl = raw_pnl * cfg["leverage"]
                    pnl_usd = (lev_pnl / 100) * total_col

                enriched.append({
                    "id":            first["id"],
                    "symbol":        sym,
                    "side":          side,
                    "entry":         entry,
                    "price":         price,
                    "raw_pnl":       round(raw_pnl, 4),
                    "lev_pnl":       round(lev_pnl, 4),
                    "pnl_usd":       round(pnl_usd, 2),
                    "collateral":    total_col,
                    "jup_key":       latest.get("jupiter_position_key", ""),
                    "opened":        first.get("opened_ts_utc", ""),
                    "stacked_count": len(rows_sorted),
                })

            tier_summary[tier_label] = {
                "leverage":   cfg["leverage"],
                "notional":   cfg["notional"],
                "collateral": cfg["collateral"],
                "tp_pct":     cfg["tp_pct"],
                "reenter":    cfg["reenter"],
                "positions":  enriched,
                "count":      len(enriched),
            }

        return {
            "tiers":         tier_summary,
            "profit_buffer": round(buffer, 4),
            "thresholds":    {
                "3x": _f("PROFIT_BUFFER_3X_THRESHOLD", 33),
                "5x": _f("PROFIT_BUFFER_5X_THRESHOLD", 20),
            },
            "total_collateral": sum(
                p.get("collateral_usd") or 0
                for p in positions
            ),
            "total_pnl_usd": sum(
                e["pnl_usd"]
                for ts in tier_summary.values()
                for e in ts["positions"]
            ),
        }
    finally:
        conn.close()
