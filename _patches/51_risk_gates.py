#!/usr/bin/env python3
"""
Patch 51 — Risk Kill-Switch + Fix 100% Acceptance Bug + Mode Lock Improvements

Applies to: /root/memecoin_engine/utils/perp_executor.py

Changes:
  1. Fix _check_quality_gate: remove ml_wp==0 bypass — always enforce daily cap,
     min interval, mode lock; apply ML+sentiment gates only when ml_wp > 0.
  2. Update _check_mode_switching: extend lock from 12h→24h, add daily PnL < -1%
     as a second trigger.
  3. Add risk globals: _daily_max_dd_pct, _hard_trade_cap, _cooldown_min_neg,
     _survive_mode_*, _last_any_entry_ts.
  4. Add helper functions: _create_risk_block_log, _log_risk_block, _check_risk_limits.
  5. Add _check_risk_limits call in execute_perp_signal (hard gate before quality gate).
  6. Track _last_any_entry_ts after each successful position open.
"""

import pathlib
import sys

TARGET = pathlib.Path("/root/memecoin_engine/utils/perp_executor.py")
assert TARGET.exists(), f"Target not found: {TARGET}"
content = TARGET.read_text()

# ── Change 1: Fix _check_quality_gate ─────────────────────────────────────────
# Use slice-replace: find function boundaries by searching for its def and the
# next top-level def, to avoid brittle whitespace matching.

idx_gate_start = content.find('\ndef _check_quality_gate(')
assert idx_gate_start != -1, "FAIL [1/6]: _check_quality_gate not found"
idx_gate_end = content.find('\ndef _check_mode_switching(', idx_gate_start + 1)
assert idx_gate_end != -1, "FAIL [1/6]: end of _check_quality_gate not found"

OLD_GATE = content[idx_gate_start:idx_gate_end]
NEW_GATE = '''
def _check_quality_gate(symbol, side, mode_tag, ml_wp, pred_ret, sent_boost, regime, notes_str):
    """
    Returns (True, "OK") if signal passes all quality gates, or (False, reason) if skipped.
    Logs the skip to skipped_signals_log automatically.

    Gate logic:
    - Daily cap, min interval, mode lock: ALWAYS enforced (regardless of ML data).
    - ML thresholds + sentiment: only applied when ml_wp > 0 (predictor has data).
      This prevents the cold-start bypass where ml_wp=0 let everything through.
    """
    global _daily_trade_count

    _check_daily_reset()
    cap = 3 if _daily_cb_active else _daily_trade_cap

    def _skip(reason):
        _log_skipped_signal(symbol, side, mode_tag, reason, ml_wp, pred_ret, sent_boost, regime, notes_str)
        return (False, reason)

    # 1. Daily trade hard cap — ALWAYS enforced
    if _daily_trade_count >= cap:
        return _skip("DAILY_CAP")

    # 2. Min interval between same symbol+side+mode — ALWAYS enforced
    key = f"{symbol}+{side}+{mode_tag}"
    last_ts = _last_trade_ts.get(key)
    if last_ts is not None:
        elapsed_min = (datetime.now(timezone.utc) - last_ts).total_seconds() / 60.0
        if elapsed_min < _min_interval_min:
            return _skip("MIN_INTERVAL")

    # 3. Mode lock (poor win-rate or negative day) — ALWAYS enforced
    if _dynamic_mode_forced and mode_tag == "SCALP":
        return _skip("SWING_ONLY")

    # 4. ML quality + sentiment thresholds — only when ML predictor has data (ml_wp > 0)
    if ml_wp > 0 or pred_ret > 0:
        if ml_wp < _ml_min_win_prob:
            return _skip("LOW_WIN_PROB")
        if pred_ret < _ml_min_pred_ret:
            return _skip("LOW_PRED_RET")
        if sent_boost < 5:
            return _skip("LOW_SENTIMENT")

    return (True, "OK")'''

content = content[:idx_gate_start] + NEW_GATE + content[idx_gate_end:]
print("OK [1/6] _check_quality_gate fixed (removed cold-start bypass)")

# ── Change 2: Update _check_mode_switching ────────────────────────────────────
idx_mode_start = content.find('\ndef _check_mode_switching(')
assert idx_mode_start != -1, "FAIL [2/6]: _check_mode_switching not found"
idx_mode_end = content.find('\ndef _auto_tune_selectivity(', idx_mode_start + 1)
assert idx_mode_end != -1, "FAIL [2/6]: end of _check_mode_switching not found"

NEW_MODE = '''
def _check_mode_switching():
    """
    Force SWING_ONLY for 24h when any of:
    - Last 5 closed trades: fewer than 3 wins  (win-rate check)
    - Daily PnL sum (last 24h) < -1.0%          (negative-day check)
    Resets automatically when expiry is reached.
    """
    global _dynamic_mode_forced, _dynamic_mode_until
    # Check expiry first
    if _dynamic_mode_forced and _dynamic_mode_until is not None:
        if datetime.now(timezone.utc) >= _dynamic_mode_until:
            _dynamic_mode_forced = False
            _dynamic_mode_until  = None
            logger.info("[SELECTIVITY] SWING_ONLY cooldown expired — all modes re-enabled")
        return  # Don't re-evaluate while forced
    try:
        with _conn() as c:
            # Check 1: win rate over last 5 closed trades
            rows = c.execute("""
                SELECT pnl_pct FROM perp_positions
                WHERE status='CLOSED' ORDER BY closed_ts_utc DESC LIMIT 5
            """).fetchall()
            if len(rows) >= 5:
                wins = sum(1 for r in rows if (r[0] or 0) > 0)
                if wins < 3:
                    _dynamic_mode_forced = True
                    _dynamic_mode_until  = datetime.now(timezone.utc) + timedelta(hours=24)
                    logger.warning(
                        "[SELECTIVITY] Last 5 trades: %d/5 wins — SWING_ONLY for 24h", wins
                    )
                    return
            # Check 2: daily PnL sum < -1%
            since_24h = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
            row = c.execute("""
                SELECT COALESCE(SUM(pnl_pct), 0.0) FROM perp_positions
                WHERE status='CLOSED' AND closed_ts_utc >= ?
            """, (since_24h,)).fetchone()
            daily_pnl = float(row[0] or 0)
            if daily_pnl < -1.0:
                _dynamic_mode_forced = True
                _dynamic_mode_until  = datetime.now(timezone.utc) + timedelta(hours=24)
                logger.warning(
                    "[SELECTIVITY] Daily PnL sum %.2f%% < -1%% — SWING_ONLY for 24h", daily_pnl
                )
    except Exception as _e:
        logger.debug("_check_mode_switching error: %s", _e)'''

content = content[:idx_mode_start] + NEW_MODE + content[idx_mode_end:]
print("OK [2/6] _check_mode_switching updated (24h lock, daily DD trigger)")

# ── Change 3: Add risk globals after selectivity globals block ─────────────────
OLD_GLOBALS_END = "_selectivity_tune_counter: int = 0     # incremented per close\n"
assert OLD_GLOBALS_END in content, "FAIL [3/6]: selectivity globals end anchor not found"

NEW_GLOBALS_END = (
    "_selectivity_tune_counter: int = 0     # incremented per close\n"
    "\n"
    "# ── Risk kill-switch globals ───────────────────────────────────────────────────\n"
    "_daily_max_dd_pct:   float  = 2.0    # kill-switch: sum(pnl_pct last 24h) < -N pct-points\n"
    "_hard_trade_cap:     int    = 3      # max entries/day while daily PnL is negative\n"
    "_cooldown_min_neg:   float  = 10.0   # min gap (minutes) between entries on a negative day\n"
    "_survive_mode_active: bool   = False  # True = DD threshold hit, new entries paused\n"
    "_survive_mode_until:  object = None   # datetime when survive mode expires\n"
    "_last_any_entry_ts:   object = None   # datetime of most recent entry (any mode/symbol)\n"
)
content = content.replace(OLD_GLOBALS_END, NEW_GLOBALS_END, 1)
print("OK [3/6] risk globals added")

# ── Change 4: Add risk helper functions before _check_dynamic_exit_circuit_breaker
OLD_CB_ANCHOR = "\ndef _check_dynamic_exit_circuit_breaker():"
assert OLD_CB_ANCHOR in content, "FAIL [4/6]: _check_dynamic_exit_circuit_breaker anchor not found"

RISK_FUNCS = '''

def _create_risk_block_log():
    """Create risk_block_log table idempotently."""
    try:
        with _conn() as c:
            c.execute("""
                CREATE TABLE IF NOT EXISTS risk_block_log (
                    id     INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts_utc TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
                    symbol TEXT,
                    side   TEXT,
                    mode   TEXT,
                    reason TEXT,
                    value  REAL,
                    notes  TEXT
                )
            """)
            c.commit()
    except Exception as _e:
        logger.debug("_create_risk_block_log error: %s", _e)


def _log_risk_block(symbol, side, mode, reason, value=0.0, notes=""):
    """Log a blocked entry to risk_block_log."""
    try:
        with _conn() as c:
            _create_risk_block_log()
            c.execute(
                "INSERT INTO risk_block_log (symbol, side, mode, reason, value, notes) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (symbol, side, mode, reason, round(float(value), 4), notes),
            )
            c.commit()
    except Exception as _e:
        logger.debug("_log_risk_block error: %s", _e)


def _check_risk_limits(symbol, side, mode_tag):
    """
    Hard risk gates evaluated before quality-gate and ML checks.
    Returns (True, "OK") to allow entry, or (False, reason) to block.

    Gates (in priority order):
    1. Survive mode active  → SURVIVE_MODE   (8-hour pause after DD kill-switch fires)
    2. Daily DD kill-switch → DAILY_DD_KILL  (sum pnl_pct last 24h < -_daily_max_dd_pct)
    3. Hard trade cap       → HARD_TRADE_CAP (≥ _hard_trade_cap entries when daily_pnl < 0)
    4. Global cooldown      → GLOBAL_COOLDOWN (< _cooldown_min_neg min since last entry on neg day)
    """
    global _survive_mode_active, _survive_mode_until, _last_any_entry_ts

    # ── 1. Survive mode still active? ─────────────────────────────────────
    if _survive_mode_active:
        if _survive_mode_until and datetime.now(timezone.utc) >= _survive_mode_until:
            _survive_mode_active = False
            _survive_mode_until  = None
            logger.info("[RISK] Survive mode expired — entries re-enabled")
        else:
            _log_risk_block(symbol, side, mode_tag, "SURVIVE_MODE")
            return (False, "SURVIVE_MODE")

    # ── 2. Read today's closed PnL from DB ────────────────────────────────
    try:
        since_24h = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        with _conn() as c:
            row = c.execute("""
                SELECT COALESCE(SUM(pnl_pct), 0.0), COUNT(*)
                FROM perp_positions
                WHERE status='CLOSED' AND closed_ts_utc >= ?
            """, (since_24h,)).fetchone()
        daily_pnl   = float(row[0] or 0)
        daily_count = int(row[1] or 0)
    except Exception as _e:
        logger.debug("_check_risk_limits db error: %s", _e)
        daily_pnl, daily_count = 0.0, 0

    # ── 3. Kill-switch: daily DD exceeded ─────────────────────────────────
    if daily_pnl < -_daily_max_dd_pct:
        _survive_mode_active = True
        _survive_mode_until  = datetime.now(timezone.utc) + timedelta(hours=8)
        logger.warning(
            "[RISK] Daily PnL %.2f%% < -%.1f%% — SURVIVE MODE activated for 8h",
            daily_pnl, _daily_max_dd_pct,
        )
        _log_risk_block(symbol, side, mode_tag, "DAILY_DD_KILL", daily_pnl,
                        f"threshold=-{_daily_max_dd_pct:.1f}%")
        return (False, "DAILY_DD_KILL")

    # ── 4. Hard trade cap while negative ──────────────────────────────────
    if daily_pnl < 0 and daily_count >= _hard_trade_cap:
        logger.info(
            "[RISK] Hard cap %d reached (daily_pnl=%.2f%%) — blocking %s %s %s",
            _hard_trade_cap, daily_pnl, mode_tag, symbol, side,
        )
        _log_risk_block(symbol, side, mode_tag, "HARD_TRADE_CAP", daily_count,
                        f"daily_pnl={daily_pnl:.2f}%")
        return (False, "HARD_TRADE_CAP")

    # ── 5. Global cooldown while negative ─────────────────────────────────
    if daily_pnl < 0 and _last_any_entry_ts is not None:
        elapsed_min = (datetime.now(timezone.utc) - _last_any_entry_ts).total_seconds() / 60.0
        if elapsed_min < _cooldown_min_neg:
            logger.info(
                "[RISK] Global cooldown %.1f/%.1f min (negative day) — blocking %s %s %s",
                elapsed_min, _cooldown_min_neg, mode_tag, symbol, side,
            )
            _log_risk_block(symbol, side, mode_tag, "GLOBAL_COOLDOWN", elapsed_min,
                            f"elapsed={elapsed_min:.1f}min")
            return (False, "GLOBAL_COOLDOWN")

    return (True, "OK")

'''

content = content.replace(OLD_CB_ANCHOR, RISK_FUNCS + "\ndef _check_dynamic_exit_circuit_breaker():", 1)
print("OK [4/6] risk helper functions added")

# ── Change 5: Add _check_risk_limits call early in execute_perp_signal ────────
OLD_FETCH = "    # Fetch live price\n    entry_price = _fetch_price(symbol)"
assert OLD_FETCH in content, "FAIL [5/6]: fetch_price anchor not found in execute_perp_signal"

NEW_FETCH = (
    "    # Hard risk gates: daily DD kill-switch, hard trade cap, global cooldown\n"
    "    _risk_ok, _risk_reason = _check_risk_limits(symbol, side, mode_tag)\n"
    "    if not _risk_ok:\n"
    "        logger.info(\"[RISK-GATE] Blocked %s %s %s — %s\", mode_tag, symbol, side, _risk_reason)\n"
    "        return False\n"
    "\n"
    "    # Fetch live price\n"
    "    entry_price = _fetch_price(symbol)"
)
content = content.replace(OLD_FETCH, NEW_FETCH, 1)
print("OK [5/6] _check_risk_limits call inserted in execute_perp_signal")

# ── Change 6: Track _last_any_entry_ts after successful open ──────────────────
OLD_TRACK = (
    "        global _daily_trade_count, _last_trade_ts\n"
    "        _daily_trade_count += 1\n"
    "        _last_trade_ts[f\"{symbol}+{side}+{mode_tag}\"] = datetime.now(timezone.utc)"
)
assert OLD_TRACK in content, "FAIL [6/6]: _last_trade_ts tracking anchor not found"

NEW_TRACK = (
    "        global _daily_trade_count, _last_trade_ts, _last_any_entry_ts\n"
    "        _daily_trade_count += 1\n"
    "        _last_trade_ts[f\"{symbol}+{side}+{mode_tag}\"] = datetime.now(timezone.utc)\n"
    "        _last_any_entry_ts = datetime.now(timezone.utc)"
)
content = content.replace(OLD_TRACK, NEW_TRACK, 1)
print("OK [6/6] _last_any_entry_ts tracking added")

TARGET.write_text(content)
print("\nPatch 51 applied successfully.")
