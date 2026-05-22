"""
utils/entry_monitor.py — Phase 5 Step 5: Auto-Entry Proposal Monitor

Reads cached memecoin and spot signals and proposes entry actions through
the shared execution intent pipeline.  OBSERVE mode by default — logs what
would happen without executing.

This module does NOT invent new signal logic.  It reuses the same qualifying
gates as _auto_buy_step() (memecoin_manager.py) and DCA signals (spot_signal_engine).

Discipline:
  - OBSERVE mode (default): log intent, do not execute
  - EXECUTE mode: log intent + execute if authority approves
  - Controlled by env vars: MEMECOIN_AUTO_ENTRY, SPOT_AUTO_ENTRY
  - Cooldown: one proposal per mint/symbol per 30 min
  - Authority always central: new_entry requires rank 3 + FCP open

Design principles:
  - Never blocks on errors
  - All intents via propose_and_execute()
  - Does NOT bypass or duplicate _auto_buy_step() — both can coexist safely
    (cooldown prevents double-execution, authority gates prevent unintended buys)
"""

import json
import logging
import os
import sqlite3
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def _memecoin_auto_entry() -> bool:
    return os.getenv("MEMECOIN_AUTO_ENTRY", "false").lower() == "true"


def _spot_auto_entry() -> bool:
    return os.getenv("SPOT_AUTO_ENTRY", "false").lower() == "true"


# ── Cooldown (30 min per mint/symbol) ─────────────────────────────────────────

_last_proposal: dict[str, float] = {}
_COOLDOWN_S = 1800  # 30 minutes


def _on_cooldown(key: str) -> bool:
    now = datetime.now(tz=timezone.utc).timestamp()
    last = _last_proposal.get(key, 0)
    if now - last < _COOLDOWN_S:
        return True
    _last_proposal[key] = now
    return False


# ── DB helpers ────────────────────────────────────────────────────────────────

def _get_conn():
    db_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "data_storage", "engine.db"
    )
    conn = sqlite3.connect(db_path, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=3000")
    return conn


# ── Memecoin entry monitor ────────────────────────────────────────────────────

def memecoin_entry_monitor_step() -> int:
    """
    Read cached memecoin scan signals, apply qualifying gates, and propose
    entries through the shared pipeline.  Returns number of intents logged.
    """
    try:
        from utils.memecoin_scanner import get_cached_signals  # type: ignore[import]
        signals = get_cached_signals()
    except Exception:
        return 0

    if not signals:
        return 0

    auto_exec = _memecoin_auto_entry()
    mode = "EXECUTE" if auto_exec else "OBSERVE"

    # Load thresholds (same as _auto_buy_step)
    threshold = float(os.getenv("MEMECOIN_BUY_SCORE_MIN", "65"))
    amount_usd = float(os.getenv("MEMECOIN_BUY_USD", "15"))

    # Open mints — don't propose for already-open positions
    open_mints: set[str] = set()
    try:
        conn = _get_conn()
        try:
            rows = conn.execute(
                "SELECT mint FROM memecoin_trades WHERE status='OPEN'"
            ).fetchall()
            open_mints = {r["mint"] for r in rows}
        finally:
            conn.close()
    except Exception:
        pass

    intents_logged = 0
    sorted_signals = sorted(signals, key=lambda s: s.get("score", 0), reverse=True)

    for sig in sorted_signals:
        mint = sig.get("mint", "")
        symbol = sig.get("symbol", "UNKNOWN")
        score = sig.get("score", 0)
        rug = sig.get("rug_label", "UNKNOWN")
        bp = sig.get("buy_pressure") or 50.0
        revoked = sig.get("mint_revoked", False)
        vacc = float(sig.get("vol_acceleration") or 0.0)
        holder_pct = float(sig.get("top_holder_pct") or 0.0)

        # ── Qualifying gates (same as _auto_buy_step) ────────────────────
        if not mint or mint in open_mints:
            continue
        if score < threshold:
            break  # sorted descending
        if rug != "GOOD":
            continue
        if bp < 55:
            continue
        if not revoked:
            continue
        if vacc < 5.0:
            continue
        if holder_pct > 35.0:
            continue

        if _on_cooldown(f"mc_entry_{mint}"):
            continue

        # ── Pipeline ─────────────────────────────────────────────────────
        _exec_fn = None
        if auto_exec:
            _mnt, _sym, _amt = mint, symbol, amount_usd
            def _exec_fn():
                from utils.memecoin_manager import buy_memecoin  # type: ignore[import]
                return buy_memecoin(_mnt, _sym, _amt)

        _sig_ts = sig.get("scanned_at") or sig.get("created_at")
        try:
            from execution_pipeline import propose_and_execute
            propose_and_execute(
                source="memecoin_entry_monitor",
                lane="memecoins",
                action_type="new_entry",
                symbol=symbol,
                amount_usd=amount_usd,
                mode=mode,
                execute_fn=_exec_fn,
                notes=f"score={score} rug={rug} bp={bp:.0f} vacc={vacc:.1f} holder={holder_pct:.1f}%",
                signal_ts=_sig_ts,
            )
            intents_logged += 1
        except Exception:
            pass

    return intents_logged


# ── Spot entry monitor ────────────────────────────────────────────────────────

def spot_entry_monitor_step() -> int:
    """
    Read cached spot DCA signals and propose new-token entries (tokens NOT
    currently held) through the shared pipeline.  Returns number of intents logged.

    Note: DCA adds for already-held tokens are handled by dca_monitor.py
    (scale_existing).  This monitor covers only truly new basket entries.
    """
    signal_updated_at = None
    try:
        conn = _get_conn()
        try:
            row = conn.execute(
                "SELECT value FROM kv_store WHERE key = 'spot_current_signals'"
            ).fetchone()
            if not row:
                return 0
            payload = json.loads(row["value"])
            signals = payload.get("data", {})
            signal_updated_at = payload.get("updated_at")
        finally:
            conn.close()
    except Exception:
        return 0

    if not signals:
        return 0

    # Held symbols — this monitor only proposes for unheld tokens
    held: set[str] = set()
    try:
        conn = _get_conn()
        try:
            rows = conn.execute(
                "SELECT symbol FROM spot_holdings WHERE token_amount > 0"
            ).fetchall()
            held = {r["symbol"] for r in rows}
        finally:
            conn.close()
    except Exception:
        pass

    auto_exec = _spot_auto_entry()
    mode = "EXECUTE" if auto_exec else "OBSERVE"
    amount_usd = float(os.getenv("SPOT_ENTRY_AMOUNT_USD", "10.0"))
    intents_logged = 0

    for symbol, sig in signals.items():
        if sig.get("signal_type") != "DCA_NOW":
            continue
        if symbol in held:
            continue  # held tokens go through dca_monitor as scale_existing

        if _on_cooldown(f"spot_entry_{symbol}"):
            continue

        score = sig.get("score", 0)
        price = sig.get("price", 0)

        _exec_fn = None
        if auto_exec:
            _sym = symbol
            def _exec_fn():
                from utils.spot_accumulator import BASKET, buy_spot  # type: ignore[import]
                _map = {b["symbol"]: b for b in BASKET}
                t = _map.get(_sym)
                if not t:
                    return {"success": False, "error": f"{_sym} not in basket"}
                return buy_spot(_sym, t["mint"], amount_usd)

        try:
            from execution_pipeline import propose_and_execute
            propose_and_execute(
                source="spot_entry_monitor",
                lane="spot",
                action_type="new_entry",
                symbol=symbol,
                amount_usd=amount_usd,
                mode=mode,
                execute_fn=_exec_fn,
                notes=f"dca_now score={score} price={price} new_token",
                signal_ts=signal_updated_at,
            )
            intents_logged += 1
        except Exception:
            pass

    return intents_logged
