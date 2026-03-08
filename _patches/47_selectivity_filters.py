#!/usr/bin/env python3
"""Patch 47: High-Conviction Selectivity Filters + Quality-Over-Quantity Auto-Tuning.

Changes to perp_executor.py:
1. Add selectivity globals (daily cap, ML thresholds, mode-switching state)
2. Add helper functions: _check_daily_reset, _log_skipped_signal, _check_quality_gate,
   _check_mode_switching, _auto_tune_selectivity
3. Integrate quality gate in execute_perp_signal (after sentiment block, before exit levels)
4. Increment daily trade count after successful position open
5. Call _auto_tune_selectivity from _close_perp_position
"""
import pathlib

FILE = pathlib.Path("/root/memecoin_engine/utils/perp_executor.py")
content = FILE.read_text()
original = content

changes = 0

# ═══════════════════════════════════════════════════════════════════════════════
# 1. Add selectivity globals after _auto_tune_last_count = 0
# ═══════════════════════════════════════════════════════════════════════════════
old = '''_trail_tighten_factor = 1.0
_auto_tune_trade_counter = 0
_auto_tune_last_count = 0'''
new = '''_trail_tighten_factor = 1.0
_auto_tune_trade_counter = 0
_auto_tune_last_count = 0

# ── Selectivity / quality-gate globals ────────────────────────────────────────
_daily_trade_count: int = 0
_daily_trade_reset_date: object = None   # str "YYYY-MM-DD" UTC
_last_trade_ts: dict = {}               # key "SYMBOL+SIDE+MODE" → datetime
_dynamic_mode_forced: bool = False      # True = SWING_ONLY after poor WR
_dynamic_mode_until: object = None      # datetime expiry of SWING_ONLY
_ml_min_win_prob: float = 0.70          # quality gate — auto-tunable
_ml_min_pred_ret: float = 0.8           # quality gate — auto-tunable
_daily_trade_cap: int = 5               # max trades/24h UTC — auto-tunable
_daily_cb_active: bool = False          # True = circuit breaker lowered cap to 3
_min_interval_min: float = 5.0         # min minutes between any trades
_selectivity_tune_counter: int = 0     # incremented per close'''
assert old in content, "FAIL [1/5]: _auto_tune_last_count anchor not found"
content = content.replace(old, new, 1)
changes += 1
print("[1/5] Added selectivity globals")

# ═══════════════════════════════════════════════════════════════════════════════
# 2. Add helper functions before async def execute_perp_signal
# ═══════════════════════════════════════════════════════════════════════════════
HELPER_FUNCS = '''
# ── Selectivity helpers ────────────────────────────────────────────────────────

def _check_daily_reset():
    """Reset daily trade counter at UTC midnight."""
    global _daily_trade_count, _daily_trade_reset_date
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if _daily_trade_reset_date != today:
        _daily_trade_count = 0
        _daily_trade_reset_date = today


def _log_skipped_signal(symbol, side, mode, reason, ml_wp, pred_ret, sent_boost, regime, notes):
    """Insert a row into skipped_signals_log. Swallows all exceptions."""
    try:
        with _conn() as c:
            c.execute("""
                CREATE TABLE IF NOT EXISTS skipped_signals_log (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts_utc      TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
                    symbol      TEXT,
                    side        TEXT,
                    mode        TEXT,
                    skip_reason TEXT,
                    ml_wp       REAL,
                    pred_ret    REAL,
                    sent_boost  REAL,
                    regime      TEXT,
                    notes       TEXT
                )
            """)
            c.execute("""
                INSERT INTO skipped_signals_log
                    (symbol, side, mode, skip_reason, ml_wp, pred_ret, sent_boost, regime, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (symbol, side, mode, reason,
                  round(float(ml_wp), 4), round(float(pred_ret), 4),
                  round(float(sent_boost), 2), regime, notes))
            c.commit()
    except Exception as _e:
        logger.debug("_log_skipped_signal error: %s", _e)


def _check_quality_gate(symbol, side, mode_tag, ml_wp, pred_ret, sent_boost, regime, notes_str):
    """
    Returns (True, "OK") if signal passes all quality gates, or (False, reason) if skipped.
    Logs the skip to skipped_signals_log automatically.

    Bypass: if ml_wp == 0 and pred_ret == 0 (manual/dashboard signals with no ML data).
    """
    global _daily_trade_count

    # Bypass for manual signals (no ML data attached)
    if ml_wp <= 0 and pred_ret <= 0:
        return (True, "OK")

    _check_daily_reset()
    cap = 3 if _daily_cb_active else _daily_trade_cap

    def _skip(reason):
        _log_skipped_signal(symbol, side, mode_tag, reason, ml_wp, pred_ret, sent_boost, regime, notes_str)
        return (False, reason)

    if ml_wp < _ml_min_win_prob:
        return _skip("LOW_WIN_PROB")

    if pred_ret < _ml_min_pred_ret:
        return _skip("LOW_PRED_RET")

    if sent_boost < 5:
        return _skip("LOW_SENTIMENT")

    if _daily_trade_count >= cap:
        return _skip("DAILY_CAP")

    key = f"{symbol}+{side}+{mode_tag}"
    last_ts = _last_trade_ts.get(key)
    if last_ts is not None:
        elapsed_min = (datetime.now(timezone.utc) - last_ts).total_seconds() / 60.0
        if elapsed_min < _min_interval_min:
            return _skip("MIN_INTERVAL")

    if _dynamic_mode_forced and mode_tag == "SCALP":
        return _skip("DYNAMIC_MODE")

    return (True, "OK")


def _check_mode_switching():
    """
    After 5 closed positions with < 3 wins → force SWING_ONLY for 12h.
    Resets automatically when expiry is reached.
    """
    global _dynamic_mode_forced, _dynamic_mode_until
    # Check expiry first
    if _dynamic_mode_forced and _dynamic_mode_until is not None:
        if datetime.now(timezone.utc) >= _dynamic_mode_until:
            _dynamic_mode_forced = False
            _dynamic_mode_until = None
            logger.info("[SELECTIVITY] SWING_ONLY cooldown expired — all modes re-enabled")
        return  # Don't re-evaluate while forced
    try:
        with _conn() as c:
            rows = c.execute("""
                SELECT pnl_pct FROM perp_positions
                WHERE status='CLOSED' ORDER BY closed_ts_utc DESC LIMIT 5
            """).fetchall()
        if len(rows) < 5:
            return
        wins = sum(1 for r in rows if (r[0] or 0) > 0)
        if wins < 3:
            _dynamic_mode_forced = True
            _dynamic_mode_until = datetime.now(timezone.utc) + timedelta(hours=12)
            logger.warning(
                "[SELECTIVITY] Last 5 trades: %d/5 wins — switching to SWING_ONLY for 12h", wins
            )
    except Exception as _e:
        logger.debug("_check_mode_switching error: %s", _e)


def _auto_tune_selectivity():
    """
    Runs every 10 closed trades. Adjusts ML min win_prob and daily cap
    based on trades/day rate and avg PNL per trade.
    Writes suggestions to dynamic_exit_tuner_log.
    """
    global _ml_min_win_prob, _daily_trade_cap
    if _selectivity_tune_counter % 10 != 0:
        return
    try:
        with _conn() as c:
            # Trades per day over last 7 days
            cutoff_7d = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
            cnt_row = c.execute("""
                SELECT COUNT(*) FROM perp_positions
                WHERE status='CLOSED' AND closed_ts_utc > ?
            """, (cutoff_7d,)).fetchone()
            trades_per_day = round((cnt_row[0] or 0) / 7.0, 2)

            # Avg PNL last 20 trades
            avg_row = c.execute("""
                SELECT AVG(pnl_pct) FROM (
                    SELECT pnl_pct FROM perp_positions
                    WHERE status='CLOSED' AND pnl_pct IS NOT NULL
                    ORDER BY closed_ts_utc DESC LIMIT 20
                )
            """).fetchone()
            avg_pnl = round(float(avg_row[0] or 0), 3)

            # Skipped signals today
            today_start = datetime.now(timezone.utc).strftime("%Y-%m-%d") + "T00:00:00"
            skipped_today = 0
            try:
                sk_row = c.execute("""
                    SELECT COUNT(*) FROM skipped_signals_log WHERE ts_utc >= ?
                """, (today_start,)).fetchone()
                skipped_today = sk_row[0] or 0
            except Exception:
                pass

        adjustments = []
        import json as _json

        if trades_per_day > 4:
            old_wp = _ml_min_win_prob
            _ml_min_win_prob = round(min(_ml_min_win_prob + 0.05, 0.85), 2)
            adjustments.append(
                f"Trades/day={trades_per_day:.1f} > 4 → raised ML gate {old_wp:.2f} → {_ml_min_win_prob:.2f}"
            )
            logger.info("[SELECTIVITY-TUNE] trades/day=%.1f → _ml_min_win_prob=%.2f",
                        trades_per_day, _ml_min_win_prob)

        if avg_pnl < 0.5 and avg_pnl != 0:
            old_wp = _ml_min_win_prob
            _ml_min_win_prob = round(min(_ml_min_win_prob + 0.05, 0.85), 2)
            adjustments.append(
                f"Avg PNL={avg_pnl:.2f}% < 0.5% → tightened selectivity gate to {_ml_min_win_prob:.2f}"
            )
            logger.info("[SELECTIVITY-TUNE] avg_pnl=%.2f%% → _ml_min_win_prob=%.2f",
                        avg_pnl, _ml_min_win_prob)

        # Safety: high skip rate → raise daily cap to avoid missing big moves
        if skipped_today > 10 and _daily_trade_cap < 8:
            _daily_trade_cap += 1
            adjustments.append(
                f"High skip rate ({skipped_today}/day) → raised daily cap to {_daily_trade_cap}"
            )
            logger.info("[SELECTIVITY-TUNE] skip_rate=%d → daily_cap=%d", skipped_today, _daily_trade_cap)

        if not adjustments:
            adjustments.append(
                f"No changes needed (trades/day={trades_per_day:.1f}, avg_pnl={avg_pnl:.2f}%)"
            )

        analysis = _json.dumps({
            "context": "selectivity",
            "ml_min_win_prob": _ml_min_win_prob,
            "ml_min_pred_ret": _ml_min_pred_ret,
            "daily_trade_cap": _daily_trade_cap,
            "trades_per_day": trades_per_day,
            "avg_pnl_last_20": avg_pnl,
            "skipped_today": skipped_today,
        })
        adj_json = _json.dumps(adjustments)

        with _conn() as c:
            c.execute("""
                CREATE TABLE IF NOT EXISTS dynamic_exit_tuner_log (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts_utc      TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
                    total_closed INTEGER,
                    analysis    TEXT,
                    adjustments TEXT
                )
            """)
            c.execute("""
                INSERT INTO dynamic_exit_tuner_log (total_closed, analysis, adjustments)
                VALUES (?, ?, ?)
            """, (_selectivity_tune_counter, analysis, adj_json))
            c.commit()

        logger.info("[SELECTIVITY-TUNE] Ran at counter=%d: %s",
                    _selectivity_tune_counter, "; ".join(adjustments))
    except Exception as _e:
        logger.warning("_auto_tune_selectivity error: %s", _e)

'''

old = '''async def execute_perp_signal(signal: dict) -> bool:'''
new = HELPER_FUNCS + '''async def execute_perp_signal(signal: dict) -> bool:'''
assert old in content, "FAIL [2/5]: async def execute_perp_signal not found"
content = content.replace(old, new, 1)
changes += 1
print("[2/5] Added selectivity helper functions")

# ═══════════════════════════════════════════════════════════════════════════════
# 3. Insert quality gate in execute_perp_signal (before "# Compute exit levels")
#    At this point: ml_prediction (local), sent_boost (local), regime (local) are all available
# ═══════════════════════════════════════════════════════════════════════════════
old = '''    combined_mult = round(combined_mult * sent_mult, 2)

    # Compute exit levels'''
new = '''    combined_mult = round(combined_mult * sent_mult, 2)

    # ── High-Conviction Quality Gate ──
    _check_mode_switching()
    _ml_wp_val  = float(ml_prediction["win_prob"])  if ml_prediction else 0.0
    _pred_ret_v = float(ml_prediction["pred_return"]) if ml_prediction else 0.0
    _gate_ok, _gate_reason = _check_quality_gate(
        symbol, side, mode_tag, _ml_wp_val, _pred_ret_v, sent_boost, regime, ""
    )
    if not _gate_ok:
        logger.info(
            "[QUALITY-GATE] Skipped %s %s %s — %s (ml_wp=%.2f pred_ret=%.2f sent=%d)",
            mode_tag, symbol, side, _gate_reason, _ml_wp_val, _pred_ret_v, int(sent_boost),
        )
        return False

    # Compute exit levels'''
assert old in content, "FAIL [3/5]: sentiment combined_mult anchor not found"
content = content.replace(old, new, 1)
changes += 1
print("[3/5] Inserted quality gate in execute_perp_signal")

# ═══════════════════════════════════════════════════════════════════════════════
# 4. Increment daily trade count + record last trade timestamp after position open
# ═══════════════════════════════════════════════════════════════════════════════
old = '''    if pos:
        _queue_perp_outcome(symbol, side, entry_price, regime)'''
new = '''    if pos:
        # Selectivity: track daily count + per-symbol interval
        global _daily_trade_count, _last_trade_ts
        _daily_trade_count += 1
        _last_trade_ts[f"{symbol}+{side}+{mode_tag}"] = datetime.now(timezone.utc)
        _queue_perp_outcome(symbol, side, entry_price, regime)'''
assert old in content, "FAIL [4/5]: if pos: _queue_perp_outcome anchor not found"
content = content.replace(old, new, 1)
changes += 1
print("[4/5] Added trade count increment after position open")

# ═══════════════════════════════════════════════════════════════════════════════
# 5. Call _auto_tune_selectivity from _close_perp_position (after _auto_tune_exit_params)
# ═══════════════════════════════════════════════════════════════════════════════
old = '''    # Auto-tune: increment counter and maybe run analysis
    global _auto_tune_trade_counter
    _auto_tune_trade_counter += 1
    _auto_tune_exit_params()'''
new = '''    # Auto-tune: increment counters and maybe run analysis
    global _auto_tune_trade_counter, _selectivity_tune_counter
    _auto_tune_trade_counter += 1
    _auto_tune_exit_params()
    _selectivity_tune_counter += 1
    _auto_tune_selectivity()'''
assert old in content, "FAIL [5/5]: _auto_tune_trade_counter anchor not found"
content = content.replace(old, new, 1)
changes += 1
print("[5/5] Added _auto_tune_selectivity call in _close_perp_position")

# ═══════════════════════════════════════════════════════════════════════════════
# Write
# ═══════════════════════════════════════════════════════════════════════════════
assert changes == 5, f"Expected 5 changes, got {changes}"
FILE.write_text(content)
print(f"\n✅ Patch 47 applied ({changes}/5 changes) — Selectivity filters live")
print("   New globals: _daily_trade_count, _ml_min_win_prob, _daily_trade_cap, etc.")
print("   New functions: _check_quality_gate, _check_mode_switching, _auto_tune_selectivity")
print("   New DB table: skipped_signals_log")
print("   Gate thresholds: ml_wp>=0.70, pred_ret>=0.8%, sent_boost>=5, daily_cap=5")
