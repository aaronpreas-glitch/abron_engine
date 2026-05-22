#!/usr/bin/env python3
"""
cli_agent.py — Autonomous execution shell for Watchlist Breakdown+TRANSITION lane.
Patch 257.

Commands:
    python cli_agent.py run              # Start loop in simulate mode (safe default)
    python cli_agent.py run --live       # Start loop in live mode (real trades)
    python cli_agent.py status           # Show current state, open positions, day stats
    python cli_agent.py pause [--hours N] # Halt new entries for N hours (default 24)

Signal source:
    alert_outcomes WHERE lane='watchlist' AND setup_label='Breakdown'
                       AND cycle_phase='TRANSITION'
    Freshness guard: signals older than CLI_SIGNAL_FRESHNESS_SECS (default 600s) ignored.
    Duplicate guard: one open CLI position per symbol; symbol cooldown post-close.

Execution:
    Simulate: price fetch via DexScreener, no real swap, dry_run=1.
    Live:     Jupiter spot buy/sell via utils/jupiter_swap.py, dry_run=0.

State:
    cli_positions   — execution positions (separate from memecoin_trades)
    cli_event_log   — full audit trail of every action and decision

Risk (all env-overridable, hard-coded safe defaults):
    CLI_MAX_SLOTS=3, CLI_POSITION_USD=25, CLI_MAX_DAILY_DEPLOY=75,
    CLI_DAILY_STOP_USD=50, CLI_STOP_LOSS_PCT=15, CLI_MAX_HOLD_HOURS=24,
    CLI_CONSEC_LOSS_PAUSE=3, CLI_SYMBOL_COOLDOWN_H=48,
    CLI_BANKROLL_FLOOR_USD=500, CLI_MAX_BANKROLL_PCT=10,
    CLI_TOTAL_BANKROLL_USD=2300

IMPORTANT:
    - Default mode is simulate. --live flag required for real trades.
    - Observational alert_outcomes rows are NEVER written to by this agent.
    - Executed-trade state lives exclusively in cli_positions.
"""

import os
import sys
import json
import time
import asyncio
import argparse
import requests
from datetime import datetime, timedelta

# ── Path + env setup ────────────────────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # triggers load_dotenv(override=True) — must come before os.getenv calls
from utils.db import get_conn


# ── Risk parameters (env-overridable, safe defaults) ────────────────────────────
def _cfg_int(key, default):   return int(os.getenv(key, str(default)))
def _cfg_float(key, default): return float(os.getenv(key, str(default)))

CLI_MAX_SLOTS          = _cfg_int  ("CLI_MAX_SLOTS",              3)
CLI_POSITION_USD       = _cfg_float("CLI_POSITION_USD",          25.0)
CLI_MAX_DAILY_DEPLOY   = _cfg_float("CLI_MAX_DAILY_DEPLOY",      75.0)
CLI_DAILY_STOP_USD     = _cfg_float("CLI_DAILY_STOP_USD",        50.0)
CLI_STOP_LOSS_PCT      = _cfg_float("CLI_STOP_LOSS_PCT",         15.0)  # positive number → -15%
CLI_MAX_HOLD_HOURS     = _cfg_float("CLI_MAX_HOLD_HOURS",        24.0)
CLI_CONSEC_LOSS_PAUSE  = _cfg_int  ("CLI_CONSEC_LOSS_PAUSE",      3)
CLI_SYMBOL_COOLDOWN_H  = _cfg_float("CLI_SYMBOL_COOLDOWN_H",     48.0)
CLI_BANKROLL_FLOOR_USD = _cfg_float("CLI_BANKROLL_FLOOR_USD",   500.0)
CLI_MAX_BANKROLL_PCT   = _cfg_float("CLI_MAX_BANKROLL_PCT",      10.0)  # % of total bankroll
CLI_TOTAL_BANKROLL_USD = _cfg_float("CLI_TOTAL_BANKROLL_USD",  2300.0)
CLI_POLL_SECONDS       = _cfg_int  ("CLI_POLL_SECONDS",          60)
CLI_SIGNAL_FRESHNESS_S = _cfg_int  ("CLI_SIGNAL_FRESHNESS_SECS", 600)

# Derived: absolute USD cap — never more than CLI_MAX_BANKROLL_PCT% deployed at once
CLI_MAX_TOTAL_DEPLOYED = CLI_TOTAL_BANKROLL_USD * CLI_MAX_BANKROLL_PCT / 100
# Default: 2300 * 10% = $230 max across all open CLI positions


# ── Schema ───────────────────────────────────────────────────────────────────────
_SCHEMA_STMTS = [
    """
    CREATE TABLE IF NOT EXISTS cli_positions (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        signal_id     INTEGER,          -- alert_outcomes.id that triggered this entry
        symbol        TEXT    NOT NULL,
        mint          TEXT    NOT NULL DEFAULT '',
        entry_price   REAL    NOT NULL,
        exit_price    REAL,
        amount_usd    REAL    NOT NULL,
        token_amount  REAL,             -- raw units (live) or human units (simulate)
        pnl_pct       REAL,
        pnl_usd       REAL,
        status        TEXT    NOT NULL DEFAULT 'OPEN',  -- OPEN | CLOSED
        exit_reason   TEXT,             -- STOP_LOSS | MAX_HOLD | MANUAL | KILLED
        dry_run       INTEGER NOT NULL DEFAULT 1,       -- 1=simulate  0=live
        tx_sig_open   TEXT,
        tx_sig_close  TEXT,
        opened_ts_utc TEXT    NOT NULL,
        closed_ts_utc TEXT,
        notes         TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS cli_event_log (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        ts_utc     TEXT    NOT NULL,
        event_type TEXT    NOT NULL,
        symbol     TEXT,
        detail     TEXT,
        dry_run    INTEGER DEFAULT 1
    )
    """,
]


def _ensure_schema():
    with get_conn() as conn:
        for stmt in _SCHEMA_STMTS:
            conn.execute(stmt)


# ── kv_store helpers ─────────────────────────────────────────────────────────────
def _kv_get(key: str, default=None):
    try:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT value FROM kv_store WHERE key=?", (key,)
            ).fetchone()
            return row[0] if row else default
    except Exception:
        return default


def _kv_set(key: str, value):
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO kv_store (key, value) VALUES (?,?)",
            (key, str(value)),
        )


# ── Event logging ────────────────────────────────────────────────────────────────
def _log(event_type: str, symbol: str = None, detail=None, dry_run: int = 1):
    """Write to cli_event_log and print to terminal."""
    ts = datetime.utcnow().isoformat()
    if isinstance(detail, dict):
        detail_str = json.dumps(detail, default=str)
    else:
        detail_str = str(detail or "")

    try:
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO cli_event_log (ts_utc, event_type, symbol, detail, dry_run)"
                " VALUES (?,?,?,?,?)",
                (ts, event_type, symbol, detail_str[:2000], dry_run),
            )
    except Exception as e:
        # Never let logging failures crash the loop
        print(f"[LOG_ERR] {e}")

    prefix = "[SIM]" if dry_run else "[LIVE]"
    sym    = f" {symbol}" if symbol else ""
    trunc  = detail_str[:100] + ("…" if len(detail_str) > 100 else "")
    print(f"{ts[:19]} {prefix} {event_type}{sym}: {trunc}")


def _print(msg: str):
    print(f"{datetime.utcnow().strftime('%H:%M:%S')} {msg}")


# ── Price fetch ──────────────────────────────────────────────────────────────────
def _fetch_token_price(mint: str) -> float | None:
    """
    Fetch current token price (USD) via DexScreener.
    Uses highest-liquidity Solana pair for the given mint.
    Returns None on failure — caller must handle gracefully.
    """
    if not mint or mint in ("PAPER", "SIM_NO_TX", ""):
        return None
    try:
        url = f"https://api.dexscreener.com/latest/dex/tokens/{mint}"
        r = requests.get(url, timeout=8)
        r.raise_for_status()
        pairs = (r.json().get("pairs") or [])
        # Filter to Solana pairs only
        sol_pairs = [p for p in pairs if (p.get("chainId") or "").lower() == "solana"]
        if not sol_pairs:
            sol_pairs = pairs  # fallback: use any chain
        if not sol_pairs:
            return None
        # Take highest-liquidity pair
        best = max(sol_pairs, key=lambda p: float((p.get("liquidity") or {}).get("usd", 0) or 0))
        price = float(best.get("priceUsd") or 0)
        return price if price > 0 else None
    except Exception:
        return None


def _fetch_sol_price() -> float:
    """Fetch current SOL/USD price via DexScreener. Returns 150.0 as fallback."""
    SOL_USDC_PAIR = "58oQChx4yWmvKdwLLZzBi4ChoCc2fqCUWaS3PAZkCTSb"
    try:
        url = f"https://api.dexscreener.com/latest/dex/pairs/solana/{SOL_USDC_PAIR}"
        r = requests.get(url, timeout=8)
        r.raise_for_status()
        pair = (r.json().get("pair") or r.json().get("pairs", [None])[0]) or {}
        price = float(pair.get("priceUsd") or 0)
        return price if price > 0 else 150.0
    except Exception:
        return 150.0


# ── Signal eligibility ───────────────────────────────────────────────────────────
def _fetch_eligible_signals(last_seen_id: int) -> list[dict]:
    """
    Pull new qualifying alert_outcomes rows:
        lane        = 'watchlist'
        setup_label = 'Breakdown'
        cycle_phase = 'TRANSITION'
        id          > last_seen_id          (not yet processed)
        created_ts  >= freshness cutoff     (within last CLI_SIGNAL_FRESHNESS_S seconds)

    Returns list of dicts ordered by id ASC.
    Does NOT modify alert_outcomes — read-only access.
    """
    freshness_cutoff = (
        datetime.utcnow() - timedelta(seconds=CLI_SIGNAL_FRESHNESS_S)
    ).isoformat()

    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT id, symbol, mint, entry_price, score, regime_label,
                   cycle_phase, setup_label, lane, created_ts_utc
            FROM   alert_outcomes
            WHERE  lane        = 'watchlist'
              AND  setup_label = 'Breakdown'
              AND  cycle_phase = 'TRANSITION'
              AND  id          > ?
              AND  created_ts_utc >= ?
            ORDER  BY id ASC
            """,
            (last_seen_id, freshness_cutoff),
        ).fetchall()
    return [dict(r) for r in rows]


# ── Position state helpers ───────────────────────────────────────────────────────
def _open_positions(dry_run: int) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM cli_positions WHERE status='OPEN' AND dry_run=?",
            (dry_run,),
        ).fetchall()
    return [dict(r) for r in rows]


def _open_slot_count(dry_run: int) -> int:
    return len(_open_positions(dry_run))


def _symbol_has_open(symbol: str, dry_run: int) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM cli_positions WHERE symbol=? AND status='OPEN' AND dry_run=?",
            (symbol, dry_run),
        ).fetchone()
    return row is not None


def _symbol_in_cooldown(symbol: str) -> bool:
    """True if this symbol had a CLI close within CLI_SYMBOL_COOLDOWN_H hours."""
    cutoff = (datetime.utcnow() - timedelta(hours=CLI_SYMBOL_COOLDOWN_H)).isoformat()
    with get_conn() as conn:
        row = conn.execute(
            """SELECT id FROM cli_positions
               WHERE symbol=? AND status='CLOSED' AND closed_ts_utc >= ?
               ORDER BY closed_ts_utc DESC LIMIT 1""",
            (symbol, cutoff),
        ).fetchone()
    return row is not None


# ── Daily counters ───────────────────────────────────────────────────────────────
def _today() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d")


def _daily_deployed(dry_run: int) -> float:
    """Total USD opened in new CLI positions today (UTC)."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(amount_usd),0) FROM cli_positions"
            " WHERE dry_run=? AND DATE(opened_ts_utc)=?",
            (dry_run, _today()),
        ).fetchone()
    return float(row[0])


def _daily_realized_pnl(dry_run: int) -> float:
    """Sum of realized pnl_usd for positions closed today (UTC)."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(pnl_usd),0) FROM cli_positions"
            " WHERE dry_run=? AND status='CLOSED' AND DATE(closed_ts_utc)=?",
            (dry_run, _today()),
        ).fetchone()
    return float(row[0])


def _total_deployed_usd(dry_run: int) -> float:
    """Total USD currently in open CLI positions (all time, not just today)."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(amount_usd),0) FROM cli_positions"
            " WHERE status='OPEN' AND dry_run=?",
            (dry_run,),
        ).fetchone()
    return float(row[0])


def _consecutive_losses(dry_run: int) -> int:
    """Count of consecutive losses in the N most recent closed positions."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT pnl_usd FROM cli_positions"
            " WHERE status='CLOSED' AND dry_run=?"
            " ORDER BY closed_ts_utc DESC LIMIT ?",
            (dry_run, CLI_CONSEC_LOSS_PAUSE),
        ).fetchall()
    losses = 0
    for r in rows:
        if (r[0] or 0) < 0:
            losses += 1
        else:
            break  # streak broken
    return losses


# ── Risk gate ────────────────────────────────────────────────────────────────────
def _check_gates(signal: dict, dry_run: int) -> tuple[bool, str]:
    """
    Evaluate all risk gates in priority order.
    Returns (allowed: bool, reason: str).
    'OK' reason means allowed.
    """
    symbol = signal["symbol"]

    # Gate 0 — Kill switch (re-read env each call, not module-level constant)
    if os.getenv("CLI_ENABLED", "true").lower() != "true":
        return False, "KILL_SWITCH: CLI_ENABLED=false"

    # Gate 1 — Manual pause
    paused_until = _kv_get("cli_paused_until")
    if paused_until:
        try:
            until_dt = datetime.fromisoformat(paused_until)
            if datetime.utcnow() < until_dt:
                return False, f"PAUSED until {paused_until}"
        except Exception:
            pass  # corrupt value — ignore

    # Gate 2 — Consecutive loss pause
    consec = _consecutive_losses(dry_run)
    if consec >= CLI_CONSEC_LOSS_PAUSE:
        return False, (
            f"CONSEC_LOSS_PAUSE: {consec} consecutive losses "
            f"(threshold={CLI_CONSEC_LOSS_PAUSE}). Run 'pause' to reset manually."
        )

    # Gate 3 — Daily realized stop
    day_pnl = _daily_realized_pnl(dry_run)
    if day_pnl <= -CLI_DAILY_STOP_USD:
        return False, (
            f"DAILY_STOP: realized P&L today = ${day_pnl:.2f} "
            f"(stop at -${CLI_DAILY_STOP_USD:.0f})"
        )

    # Gate 4 — Daily deploy cap
    deployed_today = _daily_deployed(dry_run)
    if deployed_today + CLI_POSITION_USD > CLI_MAX_DAILY_DEPLOY:
        return False, (
            f"DAILY_DEPLOY_CAP: ${deployed_today:.0f} deployed today "
            f"(cap=${CLI_MAX_DAILY_DEPLOY:.0f})"
        )

    # Gate 5 — Bankroll hard cap (max X% of total bankroll at risk at once)
    total_deployed = _total_deployed_usd(dry_run)
    if total_deployed + CLI_POSITION_USD > CLI_MAX_TOTAL_DEPLOYED:
        return False, (
            f"BANKROLL_CAP: ${total_deployed:.0f} deployed "
            f"(cap=${CLI_MAX_TOTAL_DEPLOYED:.0f} = "
            f"{CLI_MAX_BANKROLL_PCT:.0f}% of ${CLI_TOTAL_BANKROLL_USD:.0f})"
        )

    # Gate 6 — Slot limit
    open_count = _open_slot_count(dry_run)
    if open_count >= CLI_MAX_SLOTS:
        return False, f"SLOT_CAP: {open_count}/{CLI_MAX_SLOTS} slots full"

    # Gate 7 — Duplicate symbol (already have open position)
    if _symbol_has_open(symbol, dry_run):
        return False, f"DUPLICATE: {symbol} already has an open CLI position"

    # Gate 8 — Symbol cooldown
    if _symbol_in_cooldown(symbol):
        return False, f"COOLDOWN: {symbol} closed within {CLI_SYMBOL_COOLDOWN_H:.0f}h"

    return True, "OK"


# ── Execution: buy ───────────────────────────────────────────────────────────────
def _execute_buy(signal: dict, dry_run: int) -> dict | None:
    """
    Open a new CLI position for this signal.
    Simulate: fetch current price via DexScreener, write cli_positions, no swap.
    Live:     call jupiter_swap.execute_buy, write cli_positions with real tx_sig.

    Returns the new cli_positions row dict on success, None on failure.
    Does NOT touch memecoin_trades or alert_outcomes.
    """
    symbol = signal["symbol"]
    mint   = signal.get("mint") or ""

    # ── Roadmap 3: authority check (new_entry for watchlist lane) ─────────
    try:
        from utils.authority import resolve_action  # type: ignore[import]
        _auth = resolve_action("new_entry", "memecoins", executor="cli_agent_buy")
        if _auth["verdict"] == "BLOCK" and _auth["enforce"]:
            _log("AUTHORITY_BLOCK", symbol,
                 {"msg": "new_entry blocked by authority bridge", "reasons": _auth["reasons"]},
                 dry_run)
            return None
    except Exception:
        pass  # fail open

    if dry_run:
        # ── Simulate path ──────────────────────────────────────────────────
        price = _fetch_token_price(mint) if mint else None
        if not price or price <= 0:
            # Fallback to the price recorded at alert time
            price = float(signal.get("entry_price") or 0)
        if not price or price <= 0:
            _log("ERROR", symbol,
                 {"msg": "Cannot determine entry price — skipping signal"},
                 dry_run)
            return None

        token_amount = CLI_POSITION_USD / price
        tx_sig       = "SIM_NO_TX"

    else:
        # ── Live path ──────────────────────────────────────────────────────
        if not mint:
            _log("ERROR", symbol, {"msg": "No mint address — cannot execute live buy"}, dry_run)
            return None
        try:
            from utils.jupiter_swap import execute_buy as _jup_buy
            sol_price = _fetch_sol_price()
            result    = asyncio.run(_jup_buy(mint, CLI_POSITION_USD, sol_price))
            if not result or not result.get("tx_sig"):
                _log("ERROR", symbol,
                     {"msg": "Jupiter execute_buy returned empty result"},
                     dry_run)
                return None
            price        = float(result.get("filled_price") or signal.get("entry_price") or 0)
            token_amount = result.get("amount_out_raw", 0)   # raw token units (with decimals)
            tx_sig       = result["tx_sig"]
        except Exception as e:
            _log("ERROR", symbol, {"msg": f"Jupiter buy failed: {e}"}, dry_run)
            return None

    ts = datetime.utcnow().isoformat()
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO cli_positions
               (signal_id, symbol, mint, entry_price, amount_usd, token_amount,
                status, dry_run, tx_sig_open, opened_ts_utc)
               VALUES (?,?,?,?,?,?,'OPEN',?,?,?)""",
            (
                signal.get("id"), symbol, mint,
                price, CLI_POSITION_USD, token_amount,
                dry_run, tx_sig, ts,
            ),
        )
        pos_id = cur.lastrowid

    pos = {
        "id":            pos_id,
        "symbol":        symbol,
        "mint":          mint,
        "entry_price":   price,
        "amount_usd":    CLI_POSITION_USD,
        "token_amount":  token_amount,
        "dry_run":       dry_run,
        "tx_sig_open":   tx_sig,
        "opened_ts_utc": ts,
    }
    _log("ENTRY", symbol, {
        "pos_id":     pos_id,
        "entry_price": round(price, 8),
        "amount_usd":  CLI_POSITION_USD,
        "tx_sig":      tx_sig,
        "signal_id":   signal.get("id"),
    }, dry_run)
    return pos


# ── Execution: sell ──────────────────────────────────────────────────────────────
def _execute_sell(position: dict, reason: str, dry_run: int) -> bool:
    """
    Close an open CLI position.
    Simulate: fetch current price, compute PnL, update cli_positions.
    Live:     call jupiter_swap.execute_sell, compute PnL from USD received.

    Returns True on success, False on failure.
    Does NOT touch memecoin_trades or alert_outcomes.
    """
    symbol      = position["symbol"]
    mint        = position.get("mint") or ""
    entry_price = float(position.get("entry_price") or 0)
    pos_id      = position["id"]

    # ── Roadmap 3: authority observation (exits never blocked, just logged) ──
    try:
        from utils.authority import resolve_action  # type: ignore[import]
        resolve_action("close_position", "memecoins", executor="cli_agent_sell")
    except Exception:
        pass

    if dry_run:
        # ── Simulate path ──────────────────────────────────────────────────
        exit_price = _fetch_token_price(mint) if mint else None
        if not exit_price or exit_price <= 0:
            # Cannot fetch price — use entry_price (0% PnL), log warning
            exit_price = entry_price
            _log("ERROR", symbol,
                 {"msg": "Exit price fetch failed — recording 0% PnL", "pos_id": pos_id},
                 dry_run)
        tx_sig = "SIM_NO_TX"

    else:
        # ── Live path ──────────────────────────────────────────────────────
        token_amount_raw = int(position.get("token_amount") or 0)
        if token_amount_raw <= 0:
            _log("ERROR", symbol,
                 {"msg": "No token_amount_raw for live sell — aborting close", "pos_id": pos_id},
                 dry_run)
            return False
        try:
            from utils.jupiter_swap import execute_sell as _jup_sell
            result = asyncio.run(_jup_sell(mint, token_amount_raw))
            if not result:
                _log("ERROR", symbol,
                     {"msg": "Jupiter execute_sell returned empty result", "pos_id": pos_id},
                     dry_run)
                return False
            usd_received = float(result.get("usd_received") or 0)
            # Back-compute exit price from USD received and token amount
            original_tokens = float(position.get("token_amount") or 0)
            exit_price = (usd_received / original_tokens) if original_tokens > 0 else entry_price
            tx_sig     = result.get("tx_sig", "")
        except Exception as e:
            _log("ERROR", symbol, {"msg": f"Jupiter sell failed: {e}", "pos_id": pos_id}, dry_run)
            return False

    pnl_pct = ((exit_price - entry_price) / entry_price * 100) if entry_price > 0 else 0.0
    pnl_usd = float(position["amount_usd"]) * pnl_pct / 100
    ts      = datetime.utcnow().isoformat()

    with get_conn() as conn:
        conn.execute(
            """UPDATE cli_positions
               SET status='CLOSED', exit_price=?, pnl_pct=?, pnl_usd=?,
                   exit_reason=?, tx_sig_close=?, closed_ts_utc=?
               WHERE id=?""",
            (exit_price, pnl_pct, pnl_usd, reason, tx_sig, ts, pos_id),
        )

    _log("EXIT", symbol, {
        "pos_id":      pos_id,
        "entry_price": round(entry_price, 8),
        "exit_price":  round(exit_price, 8),
        "pnl_pct":     round(pnl_pct, 2),
        "pnl_usd":     round(pnl_usd, 2),
        "reason":      reason,
        "tx_sig":      tx_sig,
    }, dry_run)
    return True


# ── Position management ──────────────────────────────────────────────────────────
def _manage_positions(dry_run: int):
    """
    Check all open positions every poll cycle for:
      1. Max hold time exceeded → exit with MAX_HOLD
      2. Stop loss breached    → exit with STOP_LOSS
    Checks are ordered by priority: time first, then price.
    """
    for pos in _open_positions(dry_run):
        symbol  = pos["symbol"]
        mint    = pos.get("mint") or ""
        entry   = float(pos.get("entry_price") or 0)

        # Time-based exit check
        opened_dt = datetime.fromisoformat(pos["opened_ts_utc"])
        age_hours = (datetime.utcnow() - opened_dt).total_seconds() / 3600
        if age_hours >= CLI_MAX_HOLD_HOURS:
            _execute_sell(pos, f"MAX_HOLD({age_hours:.1f}h)", dry_run)
            continue  # skip price check after close

        # Stop loss check
        if mint and entry > 0:
            current = _fetch_token_price(mint)
            if current and current > 0:
                pnl_pct = (current - entry) / entry * 100
                if pnl_pct <= -CLI_STOP_LOSS_PCT:
                    _execute_sell(pos, f"STOP_LOSS({pnl_pct:.1f}%)", dry_run)


# ── Main run loop ────────────────────────────────────────────────────────────────
def cmd_run(live: bool = False):
    dry_run   = 0 if live else 1
    mode_str  = "LIVE" if live else "SIMULATE"

    _ensure_schema()

    _log("STATUS", detail={
        "msg":              f"Agent started — {mode_str} mode",
        "poll_seconds":     CLI_POLL_SECONDS,
        "max_slots":        CLI_MAX_SLOTS,
        "position_usd":     CLI_POSITION_USD,
        "daily_cap":        CLI_MAX_DAILY_DEPLOY,
        "daily_stop":       CLI_DAILY_STOP_USD,
        "stop_loss_pct":    CLI_STOP_LOSS_PCT,
        "max_hold_hours":   CLI_MAX_HOLD_HOURS,
        "bankroll_cap_usd": CLI_MAX_TOTAL_DEPLOYED,
        "freshness_secs":   CLI_SIGNAL_FRESHNESS_S,
    }, dry_run=dry_run)

    _print(f"CLI agent started — mode={mode_str}")
    _print(
        f"Risk: slots={CLI_MAX_SLOTS}, pos=${CLI_POSITION_USD:.0f}, "
        f"daily_cap=${CLI_MAX_DAILY_DEPLOY:.0f}, daily_stop=-${CLI_DAILY_STOP_USD:.0f}, "
        f"stop_loss=-{CLI_STOP_LOSS_PCT:.0f}%, hold={CLI_MAX_HOLD_HOURS:.0f}h"
    )
    _print(
        f"Bankroll cap: {CLI_MAX_BANKROLL_PCT:.0f}% of ${CLI_TOTAL_BANKROLL_USD:.0f} "
        f"= ${CLI_MAX_TOTAL_DEPLOYED:.0f} max deployed at once"
    )
    _print(f"Signal: watchlist / Breakdown / TRANSITION only — freshness={CLI_SIGNAL_FRESHNESS_S}s")

    last_seen_id = int(_kv_get("cli_last_seen_signal_id", "0"))
    _print(f"Resuming from alert_outcomes.id > {last_seen_id}")

    while True:
        try:
            # ── Kill switch (re-read env each cycle) ──────────────────────
            if os.getenv("CLI_ENABLED", "true").lower() != "true":
                _print("CLI_ENABLED=false — kill switch active, sleeping")
                time.sleep(CLI_POLL_SECONDS)
                continue

            # ── Manage existing open positions ─────────────────────────────
            _manage_positions(dry_run)

            # ── Fetch new qualifying signals ───────────────────────────────
            signals = _fetch_eligible_signals(last_seen_id)
            if signals:
                _print(f"Found {len(signals)} new qualifying signal(s)")

            for signal in signals:
                sig_id = signal["id"]
                symbol = signal["symbol"]

                # Always advance cursor even if this signal is blocked
                if sig_id > last_seen_id:
                    last_seen_id = sig_id
                    _kv_set("cli_last_seen_signal_id", sig_id)

                _log("SIGNAL_SEEN", symbol, {
                    "signal_id":   sig_id,
                    "setup_label": signal.get("setup_label"),
                    "cycle_phase": signal.get("cycle_phase"),
                    "score":       signal.get("score"),
                    "ts":          signal.get("created_ts_utc"),
                }, dry_run)

                # ── Risk gates ─────────────────────────────────────────────
                allowed, reason = _check_gates(signal, dry_run)
                if not allowed:
                    _log("GATE_BLOCK", symbol,
                         {"reason": reason, "signal_id": sig_id}, dry_run)
                    continue

                # ── Execute entry ──────────────────────────────────────────
                _execute_buy(signal, dry_run)

        except KeyboardInterrupt:
            _print("Interrupted — shutting down cleanly.")
            _log("STATUS", detail={"msg": "Agent stopped by KeyboardInterrupt"}, dry_run=dry_run)
            break

        except Exception as e:
            _log("ERROR", detail={"msg": f"Main loop error: {e}"}, dry_run=dry_run)
            _print(f"Loop error (will retry): {e}")

        time.sleep(CLI_POLL_SECONDS)


# ── Status command ───────────────────────────────────────────────────────────────
def cmd_status():
    _ensure_schema()
    now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    print(f"\n{'='*56}")
    print(f"  CLI AGENT STATUS  —  {now_str}")
    print(f"{'='*56}")

    # Kill switch / pause state
    enabled = os.getenv("CLI_ENABLED", "true")
    if enabled.lower() != "true":
        print(f"  KILL SWITCH ACTIVE  (CLI_ENABLED={enabled})")

    paused_until = _kv_get("cli_paused_until")
    if paused_until:
        try:
            until_dt = datetime.fromisoformat(paused_until)
            if datetime.utcnow() < until_dt:
                remaining = (until_dt - datetime.utcnow()).total_seconds() / 3600
                print(f"  PAUSED until {paused_until}  ({remaining:.1f}h remaining)")
        except Exception:
            pass

    for dr in [1, 0]:
        label    = "SIMULATE" if dr else "LIVE"
        pos_list = _open_positions(dr)
        deployed = _total_deployed_usd(dr)
        today_d  = _daily_deployed(dr)
        day_pnl  = _daily_realized_pnl(dr)
        consec   = _consecutive_losses(dr)

        print(f"\n  [{label}]")
        print(f"    Open positions:    {len(pos_list)} / {CLI_MAX_SLOTS}")
        print(f"    Total deployed:    ${deployed:.2f}")
        print(f"    Bankroll cap:      ${CLI_MAX_TOTAL_DEPLOYED:.0f}"
              f"  ({CLI_MAX_BANKROLL_PCT:.0f}% of ${CLI_TOTAL_BANKROLL_USD:.0f})")
        print(f"    Deployed today:    ${today_d:.2f} / ${CLI_MAX_DAILY_DEPLOY:.0f}")
        print(f"    Realized P&L today: ${day_pnl:+.2f}"
              f"  (stop at -${CLI_DAILY_STOP_USD:.0f})")
        print(f"    Consec losses:     {consec}"
              f"  (pause at {CLI_CONSEC_LOSS_PAUSE})")

        if pos_list:
            print(f"    Open positions:")
            for p in pos_list:
                age_h = (
                    datetime.utcnow() - datetime.fromisoformat(p["opened_ts_utc"])
                ).total_seconds() / 3600
                current = _fetch_token_price(p.get("mint") or "")
                if current and (p.get("entry_price") or 0) > 0:
                    live_pnl = (current - p["entry_price"]) / p["entry_price"] * 100
                    pnl_str  = f"  live={live_pnl:+.1f}%"
                else:
                    pnl_str  = ""
                print(
                    f"      {p['symbol']:12s}  entry=${p['entry_price']:.6f}"
                    f"  ${p['amount_usd']:.0f}  age={age_h:.1f}h"
                    f"  id={p['id']}{pnl_str}"
                )

        # Recent closed
        with get_conn() as conn:
            recent = conn.execute(
                """SELECT symbol, entry_price, exit_price, pnl_pct, pnl_usd,
                          exit_reason, closed_ts_utc
                   FROM cli_positions
                   WHERE status='CLOSED' AND dry_run=?
                   ORDER BY closed_ts_utc DESC LIMIT 8""",
                (dr,),
            ).fetchall()

        if recent:
            print(f"    Recent closed:")
            for r in recent:
                sign = "+" if (r[3] or 0) >= 0 else ""
                print(
                    f"      {r[0]:12s}  {sign}{r[3]:.1f}%  ${r[4]:.2f}"
                    f"  [{r[5]}]  {(r[6] or '')[:16]}"
                )

    # Last signal cursor
    last_id = _kv_get("cli_last_seen_signal_id", "0")
    print(f"\n  Last processed signal id: {last_id}")
    print(f"{'='*56}\n")


# ── Pause command ────────────────────────────────────────────────────────────────
def cmd_pause(hours: float = 24.0):
    _ensure_schema()
    until_dt = datetime.utcnow() + timedelta(hours=hours)
    until_str = until_dt.isoformat()
    _kv_set("cli_paused_until", until_str)
    _log("PAUSE", detail={"until": until_str, "hours": hours}, dry_run=1)
    print(f"\nCLI paused until {until_str}  ({hours:.0f}h from now)")
    print("New entries are blocked. Open positions continue to exit normally.")
    print("To resume: set CLI_ENABLED=true and restart, or wait for pause to expire.\n")


# ── Entry point ──────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        prog="cli_agent",
        description=(
            "Autonomous execution shell — Watchlist Breakdown+TRANSITION lane only.\n"
            "Default mode: simulate (no real trades). Use --live for real execution."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    # run
    run_p = sub.add_parser("run", help="Start the execution loop")
    run_p.add_argument(
        "--live",
        action="store_true",
        default=False,
        help="Enable real trade execution (default: simulate/paper mode)",
    )

    # status
    sub.add_parser("status", help="Show current agent state and open positions")

    # pause
    pause_p = sub.add_parser("pause", help="Halt new entries for N hours")
    pause_p.add_argument(
        "--hours",
        type=float,
        default=24.0,
        metavar="N",
        help="Hours to pause new entries (default: 24)",
    )

    args = parser.parse_args()

    if args.command == "run":
        live = getattr(args, "live", False)
        if live:
            print("\n" + "!" * 56)
            print("  WARNING: LIVE mode — real trades will be executed")
            print(f"  Bankroll cap: ${CLI_MAX_TOTAL_DEPLOYED:.0f}"
                  f"  ({CLI_MAX_BANKROLL_PCT:.0f}% of ${CLI_TOTAL_BANKROLL_USD:.0f})")
            print("  Verify CLI_TOTAL_BANKROLL_USD in .env before proceeding.")
            print("  Starting in 5 seconds. Ctrl+C to abort.")
            print("!" * 56 + "\n")
            try:
                time.sleep(5)
            except KeyboardInterrupt:
                print("Aborted.")
                return
        cmd_run(live=live)

    elif args.command == "status":
        cmd_status()

    elif args.command == "pause":
        cmd_pause(hours=args.hours)

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
