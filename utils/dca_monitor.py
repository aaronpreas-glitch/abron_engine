"""
utils/dca_monitor.py — Phase 5: DCA Auto-Accumulation Monitor

Reads cached spot DCA signals (DCA_NOW from spot_signal_engine) and proposes
accumulation actions through the authority + execution-intent pipeline.

Action classification:
  - scale_existing: operator already holds the token (rank 2 required)
  - new_entry:      operator does not hold the token (rank 3 required)

Discipline:
  - OBSERVE mode (default): log intent, do not execute
  - EXECUTE mode: log intent + call buy_spot if authority approves
  - Controlled by env var: SPOT_AUTO_DCA (default false)
  - Amount per DCA from env: SPOT_DCA_AMOUNT_USD (default 5.0)
  - Cooldown: one DCA per token per 6h (matches signal scan interval)

Design principles:
  - Never blocks on errors (fail-open for monitoring)
  - Logs every intent via record_execution_intent
  - Calls resolve_action with correct action type (scale_existing vs new_entry)
  - Does NOT duplicate or replace manual buy paths
  - Does NOT bypass authority law
"""

import json
import logging
import os
import sqlite3
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def _spot_auto_dca() -> bool:
    return os.getenv("SPOT_AUTO_DCA", "false").lower() == "true"


def _dca_amount_usd() -> float:
    return float(os.getenv("SPOT_DCA_AMOUNT_USD", "5.0"))


# ── Cooldown (6h per symbol, matches signal scan interval) ────────────────────

_last_dca: dict[str, float] = {}
_DCA_COOLDOWN_S = 6 * 3600  # 6 hours


def _on_dca_cooldown(symbol: str) -> bool:
    now = datetime.now(tz=timezone.utc).timestamp()
    last = _last_dca.get(symbol, 0)
    if now - last < _DCA_COOLDOWN_S:
        return True
    _last_dca[symbol] = now
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


def _read_dca_signals() -> tuple[dict, str | None]:
    """Read cached spot signals from kv_store. Returns ({symbol: {...}}, updated_at)."""
    try:
        conn = _get_conn()
        try:
            row = conn.execute(
                "SELECT value FROM kv_store WHERE key = 'spot_current_signals'"
            ).fetchone()
            if not row:
                return {}, None
            payload = json.loads(row["value"])
            return payload.get("data", {}), payload.get("updated_at")
        finally:
            conn.close()
    except Exception:
        return {}, None


def _get_held_symbols() -> set[str]:
    """Return set of spot basket symbols the operator currently holds (non-zero balance)."""
    try:
        conn = _get_conn()
        try:
            rows = conn.execute(
                "SELECT symbol FROM spot_holdings WHERE token_amount > 0"
            ).fetchall()
            return {r["symbol"] for r in rows}
        finally:
            conn.close()
    except Exception:
        return set()


# ── Core monitor step ─────────────────────────────────────────────────────────

def spot_dca_monitor_step() -> int:
    """
    Check cached DCA signals and propose accumulation intents.
    Returns number of intents logged.
    """
    signals, signal_updated_at = _read_dca_signals()
    if not signals:
        return 0

    held = _get_held_symbols()
    auto_exec = _spot_auto_dca()
    mode = "EXECUTE" if auto_exec else "OBSERVE"
    amount = _dca_amount_usd()
    intents_logged = 0

    for symbol, sig in signals.items():
        signal_type = sig.get("signal_type", "")
        if signal_type != "DCA_NOW":
            continue

        if _on_dca_cooldown(symbol):
            continue

        score = sig.get("score", 0)
        price = sig.get("price", 0)

        # Classify action type based on existing holdings
        already_held = symbol in held
        action_type = "scale_existing" if already_held else "new_entry"

        # ── Pipeline: authorize → execute → audit ────────────────────────
        _exec_fn = None
        if auto_exec:
            _sym, _amt = symbol, amount
            def _exec_fn():
                from utils.spot_accumulator import BASKET, buy_spot  # type: ignore[import]
                _basket_map = {b["symbol"]: b for b in BASKET}
                token = _basket_map.get(_sym)
                if not token:
                    return {"success": False, "error": f"symbol {_sym} not in basket"}
                return buy_spot(_sym, token["mint"], _amt)

        notes = (
            f"dca_signal score={score} price={price} "
            f"{'held (scale)' if already_held else 'new (entry)'}"
        )

        try:
            from execution_pipeline import propose_and_execute
            r = propose_and_execute(
                source="spot_dca_monitor",
                lane="spot",
                action_type=action_type,
                symbol=symbol,
                amount_usd=amount,
                mode=mode,
                execute_fn=_exec_fn,
                notes=notes,
                signal_ts=signal_updated_at,
            )
            intents_logged += 1
        except Exception:
            pass

    return intents_logged
