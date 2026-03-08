#!/usr/bin/env python3
"""Patch 45: Aggressive winner-run logic + auto-tune + circuit breaker enhancement.

Changes to perp_executor.py:
1. Rewrite _evaluate_dynamic_exit() — PROFIT_LOCK, lowered DYNAMIC_TRAIL, SENTIMENT_TRAIL
2. Add _get_trail_tighten_factor(), _maybe_auto_learn_trail(), _auto_tune_exit_params()
3. Add PROFIT_LOCK handler in all 3 monitor steps
4. Enhanced circuit breaker (3 consecutive bad TRAIL exits)
5. Update _dyn_reasons and auto-labeling for PROFIT_LOCK, SENTIMENT_TRAIL
6. Auto-tune counter + call after each trade close
"""
import pathlib

FILE = pathlib.Path("/root/memecoin_engine/utils/perp_executor.py")
content = FILE.read_text()
original = content

changes = 0

# ═══════════════════════════════════════════════════════════════════════════════
# 1. Update _dyn_reasons tuple to include PROFIT_LOCK, SENTIMENT_TRAIL
# ═══════════════════════════════════════════════════════════════════════════════
old = '''        _dyn_reasons = ("ML_EARLY_EXIT", "ML_PROB_DROP", "DYNAMIC_TRAIL",
                        "TRAILING_ATR_EXTEND", "TRAILING_ATR_WINNER", "PARTIAL_PROFIT")'''
new = '''        _dyn_reasons = ("ML_EARLY_EXIT", "ML_PROB_DROP", "DYNAMIC_TRAIL",
                        "TRAILING_ATR_EXTEND", "TRAILING_ATR_WINNER", "PARTIAL_PROFIT",
                        "PROFIT_LOCK", "SENTIMENT_TRAIL")'''
assert old in content, "FAIL [1/9]: _dyn_reasons not found"
content = content.replace(old, new, 1)
changes += 1
print("[1/9] Updated _dyn_reasons tuple")

# ═══════════════════════════════════════════════════════════════════════════════
# 2. Update auto-labeling to include PROFIT_LOCK, SENTIMENT_TRAIL in trail groups
# ═══════════════════════════════════════════════════════════════════════════════
old = '''            elif exit_reason in ("DYNAMIC_TRAIL", "TRAILING_ATR_EXTEND", "TRAILING_ATR_WINNER") and leveraged_pct > 0:
                _outcome = "good_call"  # trailed and caught profit
            elif exit_reason in ("DYNAMIC_TRAIL", "TRAILING_ATR_EXTEND", "TRAILING_ATR_WINNER") and leveraged_pct < -0.5:
                _outcome = "bad_call"  # trail didn't help'''
new = '''            elif exit_reason in ("DYNAMIC_TRAIL", "TRAILING_ATR_EXTEND", "TRAILING_ATR_WINNER", "PROFIT_LOCK", "SENTIMENT_TRAIL") and leveraged_pct > 0:
                _outcome = "good_call"  # trailed and caught profit
            elif exit_reason in ("DYNAMIC_TRAIL", "TRAILING_ATR_EXTEND", "TRAILING_ATR_WINNER", "PROFIT_LOCK", "SENTIMENT_TRAIL") and leveraged_pct < -0.5:
                _outcome = "bad_call"  # trail didn't help'''
assert old in content, "FAIL [2/9]: trail auto-label not found"
content = content.replace(old, new, 1)
changes += 1
print("[2/9] Updated auto-labeling for PROFIT_LOCK/SENTIMENT_TRAIL")

# ═══════════════════════════════════════════════════════════════════════════════
# 3. Add module-level state vars (trail tighten factor, auto-tune counters)
# ═══════════════════════════════════════════════════════════════════════════════
old = '''_dynamic_exit_disabled_until = None'''
new = '''_dynamic_exit_disabled_until = None
_trail_tighten_factor = 1.0
_auto_tune_trade_counter = 0
_auto_tune_last_count = 0'''
assert old in content, "FAIL [3/9]: _dynamic_exit_disabled_until not found"
content = content.replace(old, new, 1)
changes += 1
print("[3/9] Added module-level state vars")

# ═══════════════════════════════════════════════════════════════════════════════
# 4. Enhanced circuit breaker — add 3 consecutive bad_call TRAIL check
# ═══════════════════════════════════════════════════════════════════════════════
old = '''def _check_dynamic_exit_circuit_breaker():
    """Disable dynamic exits for 24h if 4 consecutive EARLY_EXIT losses."""
    global _dynamic_exit_disabled_until
    if _dynamic_exit_disabled_until and datetime.now(timezone.utc) < _dynamic_exit_disabled_until:
        return True  # still disabled
    if _dynamic_exit_disabled_until and datetime.now(timezone.utc) >= _dynamic_exit_disabled_until:
        _dynamic_exit_disabled_until = None
        logger.info("[CIRCUIT BREAKER] Dynamic exit re-enabled after 24h cooldown")
        return False
    try:
        with _conn() as c:
            cur = c.cursor()
            cur.execute("""
                SELECT del.action, pp.pnl_pct
                FROM dynamic_exit_log del
                JOIN perp_positions pp ON del.position_id = pp.id
                WHERE del.action IN ('EARLY_EXIT', 'ML_PROB_DROP') AND pp.status = 'CLOSED'
                ORDER BY del.ts_utc DESC LIMIT 4
            """)
            rows = cur.fetchall()
        if len(rows) >= 4 and all(r[1] is not None and float(r[1]) < 0 for r in rows):
            _dynamic_exit_disabled_until = datetime.now(timezone.utc) + timedelta(hours=24)
            logger.warning("[CIRCUIT BREAKER] 4 consecutive early-exit losses — disabling dynamic exits for 24h")
            return True
    except Exception:
        pass
    return False'''
new = '''def _check_dynamic_exit_circuit_breaker():
    """Disable dynamic exits for 24h if 4 consecutive EARLY_EXIT losses OR 3 consecutive bad_call TRAIL exits."""
    global _dynamic_exit_disabled_until
    if _dynamic_exit_disabled_until and datetime.now(timezone.utc) < _dynamic_exit_disabled_until:
        return True  # still disabled
    if _dynamic_exit_disabled_until and datetime.now(timezone.utc) >= _dynamic_exit_disabled_until:
        _dynamic_exit_disabled_until = None
        logger.info("[CIRCUIT BREAKER] Dynamic exit re-enabled after 24h cooldown")
        return False
    try:
        with _conn() as c:
            cur = c.cursor()
            # Check 1: 4 consecutive early-exit losses
            cur.execute("""
                SELECT del.action, pp.pnl_pct
                FROM dynamic_exit_log del
                JOIN perp_positions pp ON del.position_id = pp.id
                WHERE del.action IN ('EARLY_EXIT', 'ML_PROB_DROP') AND pp.status = 'CLOSED'
                ORDER BY del.ts_utc DESC LIMIT 4
            """)
            rows = cur.fetchall()
            if len(rows) >= 4 and all(r[1] is not None and float(r[1]) < 0 for r in rows):
                _dynamic_exit_disabled_until = datetime.now(timezone.utc) + timedelta(hours=24)
                logger.warning("[CIRCUIT BREAKER] 4 consecutive early-exit losses — disabling dynamic exits for 24h")
                return True
            # Check 2: 3 consecutive bad_call TRAIL/PROFIT_LOCK/SENTIMENT_TRAIL exits
            cur.execute("""
                SELECT outcome
                FROM dynamic_exit_log
                WHERE action IN ('DYNAMIC_TRAIL', 'TRAILING_ATR_EXTEND', 'TRAILING_ATR_WINNER',
                                 'PROFIT_LOCK', 'SENTIMENT_TRAIL')
                  AND outcome IS NOT NULL
                ORDER BY ts_utc DESC LIMIT 3
            """)
            trail_rows = cur.fetchall()
            if len(trail_rows) >= 3 and all(r[0] == 'bad_call' for r in trail_rows):
                _dynamic_exit_disabled_until = datetime.now(timezone.utc) + timedelta(hours=24)
                logger.warning("[CIRCUIT BREAKER] 3 consecutive bad_call TRAIL exits — disabling dynamic exits for 24h")
                return True
    except Exception:
        pass
    return False'''
assert old in content, "FAIL [4/9]: circuit breaker not found"
content = content.replace(old, new, 1)
changes += 1
print("[4/9] Enhanced circuit breaker")

# ═══════════════════════════════════════════════════════════════════════════════
# 5. Replace _evaluate_dynamic_exit + prepend helper functions
# ═══════════════════════════════════════════════════════════════════════════════
old = '''def _evaluate_dynamic_exit(pos, current_price, age_h, mode):
    """Evaluate whether to modify exit behavior based on ML + trend.

    Returns: None (no action), or dict with:
      {"action": "EARLY_EXIT"|"TRAIL_ATR"|"PARTIAL_CLOSE", "reason": "...", ...}

    Priority order:
      1. DYNAMIC_TRAIL — high ML conviction + profitable → immediate trailing
      2. PARTIAL_PROFIT — medium conf + winning ≥0.8% → close 50%
      3. ML_PROB_DROP — medium conf + losing → cut early
      4. Near-expiry smart exits
      5. Mid-hold adjustments
    """
    notes_str = pos.get("notes") or ""
    np = _parse_notes_dict(notes_str)
    ml_wp_str = np.get("ml_wp", "0.5")
    ml_conf = np.get("ml_conf", "MEDIUM")

    try:
        ml_wp = float(ml_wp_str) if ml_wp_str != "N/A" else 0.5
    except (ValueError, TypeError):
        ml_wp = 0.5

    entry = float(pos.get("entry_price") or 0)
    side = (pos.get("side") or "").upper()
    if entry <= 0:
        return None

    # Calculate current unrealized PnL %
    if side == "LONG":
        pnl_pct = (current_price - entry) / entry * 100
    else:
        pnl_pct = (entry - current_price) / entry * 100

    # Get ATR for trailing calculation
    atr_pct_str = np.get("atr_pct", "0.5")
    try:
        atr_pct = float(atr_pct_str) if atr_pct_str != "None" else 0.5
    except (ValueError, TypeError):
        atr_pct = 0.5

    # Max hold for this mode
    if mode == "SCALP":
        max_hold_h = SCALP_MAX_HOLD_MIN() / 60.0
    elif mode == "MID":
        max_hold_h = MID_MAX_HOLD_H()
    else:
        max_hold_h = PERP_MAX_HOLD_H()

    near_expiry = age_h >= max_hold_h * 0.8

    # ── 1. DYNAMIC_TRAIL: High ML conviction + profitable → immediate trailing ──
    if ml_wp >= 0.72 and pnl_pct > 0:
        trail = max(0.003, atr_pct / 100 * 1.2)
        _log_dynamic_exit(pos["id"], "DYNAMIC_TRAIL", ml_wp, ml_conf, pnl_pct, age_h,
                          f"high_ml_wp({ml_wp:.2f})+profitable({pnl_pct:.2f}%)")
        return {"action": "TRAIL_ATR", "trail_pct": trail, "reason": "DYNAMIC_TRAIL"}

    # ── 2. PARTIAL_PROFIT: Medium conf + winning ≥0.8% → close 50% ──
    if pnl_pct >= 0.8 and ml_conf == "MEDIUM" and not _has_partial_close(pos["id"]):
        _log_dynamic_exit(pos["id"], "PARTIAL_PROFIT", ml_wp, ml_conf, pnl_pct, age_h,
                          f"partial_profit({pnl_pct:.2f}%)+medium_conf({ml_wp:.2f})")
        return {"action": "PARTIAL_CLOSE", "close_pct": 0.50, "reason": "PARTIAL_PROFIT"}

    # ── 3. ML_PROB_DROP: Medium conf + losing → entry prediction wrong ──
    if ml_conf == "MEDIUM" and pnl_pct < -0.3:
        _log_dynamic_exit(pos["id"], "ML_PROB_DROP", ml_wp, ml_conf, pnl_pct, age_h,
                          f"medium_conf({ml_wp:.2f})+losing({pnl_pct:.2f}%)")
        return {"action": "EARLY_EXIT", "reason": "ML_PROB_DROP"}

    # ── 4. Near-expiry smart exits ──
    if near_expiry:
        if pnl_pct > 0.3 and ml_wp >= 0.55:
            trail = max(0.003, atr_pct / 100 * 1.5)
            _log_dynamic_exit(pos["id"], "TRAIL_ATR", ml_wp, ml_conf, pnl_pct, age_h,
                              f"near_expiry+profitable({pnl_pct:.2f}%)+ml_favorable({ml_wp:.2f})")
            return {"action": "TRAIL_ATR", "trail_pct": trail, "reason": "TRAILING_ATR_EXTEND"}

        if pnl_pct < -0.5 and ml_wp < 0.45:
            _log_dynamic_exit(pos["id"], "EARLY_EXIT", ml_wp, ml_conf, pnl_pct, age_h,
                              f"near_expiry+losing({pnl_pct:.2f}%)+ml_bearish({ml_wp:.2f})")
            return {"action": "EARLY_EXIT", "reason": "ML_EARLY_EXIT"}

    # ── 5. Mid-hold dynamic adjustments ──
    if pnl_pct > 0.5 and ml_conf == "HIGH":
        trail = max(0.003, atr_pct / 100 * 0.8)
        _log_dynamic_exit(pos["id"], "TRAIL_ATR", ml_wp, ml_conf, pnl_pct, age_h,
                          f"mid_hold+winning({pnl_pct:.2f}%)+HIGH_conf")
        return {"action": "TRAIL_ATR", "trail_pct": trail, "reason": "TRAILING_ATR_WINNER"}

    if pnl_pct < -1.0 and ml_wp < 0.40:
        _log_dynamic_exit(pos["id"], "EARLY_EXIT", ml_wp, ml_conf, pnl_pct, age_h,
                          f"mid_hold+deep_loss({pnl_pct:.2f}%)+ml_very_bearish({ml_wp:.2f})")
        return {"action": "EARLY_EXIT", "reason": "ML_EARLY_EXIT"}

    return None  # no dynamic action, continue normal monitoring'''

new = '''def _get_trail_tighten_factor():
    """Return current trail tighten multiplier (1.0 = normal, 0.7 = tightest)."""
    return _trail_tighten_factor


def _maybe_auto_learn_trail():
    """After 10+ trail exits with >=60% good_call → tighten trail by 10% (min 0.7)."""
    global _trail_tighten_factor
    try:
        with _conn() as c:
            cur = c.cursor()
            cur.execute("""
                SELECT COUNT(*) FROM dynamic_exit_log
                WHERE action IN ('DYNAMIC_TRAIL', 'TRAILING_ATR_EXTEND', 'TRAILING_ATR_WINNER',
                                 'PROFIT_LOCK', 'SENTIMENT_TRAIL')
                  AND outcome = 'good_call'
            """)
            good_count = cur.fetchone()[0] or 0
            cur.execute("""
                SELECT COUNT(*) FROM dynamic_exit_log
                WHERE action IN ('DYNAMIC_TRAIL', 'TRAILING_ATR_EXTEND', 'TRAILING_ATR_WINNER',
                                 'PROFIT_LOCK', 'SENTIMENT_TRAIL')
                  AND outcome IS NOT NULL
            """)
            total_count = cur.fetchone()[0] or 0
        if total_count >= 10 and good_count / total_count >= 0.6:
            old_factor = _trail_tighten_factor
            _trail_tighten_factor = max(0.7, _trail_tighten_factor * 0.9)
            if _trail_tighten_factor < old_factor:
                logger.info("[AUTO-LEARN TRAIL] Tightened trail factor: %.2f -> %.2f (good=%d/%d)",
                            old_factor, _trail_tighten_factor, good_count, total_count)
    except Exception as e:
        logger.debug("[AUTO-LEARN TRAIL] Error: %s", e)


def _auto_tune_exit_params():
    """Run every 8 new closed trades: analyze outcome rates, generate suggestions, log to DB."""
    global _auto_tune_last_count
    if _auto_tune_trade_counter - _auto_tune_last_count < 8:
        return
    _auto_tune_last_count = _auto_tune_trade_counter
    try:
        import json as _json
        with _conn() as c:
            cur = c.cursor()
            # Ensure tuner log table exists
            cur.execute("""
                CREATE TABLE IF NOT EXISTS dynamic_exit_tuner_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts_utc TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
                    total_closed INTEGER,
                    analysis TEXT,
                    adjustments TEXT
                )
            """)
            c.commit()
            # Get outcome rates per action
            cur.execute("""
                SELECT action,
                       COUNT(*) as n,
                       SUM(CASE WHEN outcome='good_call' THEN 1 ELSE 0 END) as good,
                       SUM(CASE WHEN outcome='bad_call' THEN 1 ELSE 0 END) as bad
                FROM dynamic_exit_log
                WHERE outcome IS NOT NULL
                GROUP BY action
            """)
            rows = cur.fetchall()
            cur.execute("SELECT COUNT(*) FROM perp_positions WHERE status='CLOSED'")
            total_closed = cur.fetchone()[0] or 0
        analysis = {}
        adjustments = []
        for action, n, good, bad in rows:
            good_rate = good / n if n > 0 else 0
            bad_rate = bad / n if n > 0 else 0
            analysis[action] = {"n": n, "good": good, "bad": bad,
                                "good_rate": round(good_rate, 2), "bad_rate": round(bad_rate, 2)}
            if action in ("DYNAMIC_TRAIL", "TRAILING_ATR_EXTEND", "TRAILING_ATR_WINNER",
                          "PROFIT_LOCK", "SENTIMENT_TRAIL"):
                if bad_rate > 0.4 and n >= 5:
                    adjustments.append(f"{action}: bad_rate={bad_rate:.0%} (n={n}) -> consider widening trail_pct")
            elif action in ("ML_PROB_DROP", "ML_EARLY_EXIT"):
                if bad_rate > 0.5 and n >= 5:
                    adjustments.append(f"{action}: bad_rate={bad_rate:.0%} (n={n}) -> consider tightening pnl threshold")
            elif action == "PARTIAL_PROFIT":
                if good_rate > 0.7 and n >= 5:
                    adjustments.append(f"{action}: good_rate={good_rate:.0%} (n={n}) -> consider triggering earlier")
        # Auto-learn trail tightening
        _maybe_auto_learn_trail()
        with _conn() as c:
            c.execute("""
                INSERT INTO dynamic_exit_tuner_log (total_closed, analysis, adjustments)
                VALUES (?, ?, ?)
            """, (total_closed, _json.dumps(analysis), _json.dumps(adjustments)))
            c.commit()
        logger.info("[AUTO-TUNE] Analyzed %d actions, %d suggestions, trail_factor=%.2f",
                    len(analysis), len(adjustments), _trail_tighten_factor)
    except Exception as e:
        logger.debug("[AUTO-TUNE] Error: %s", e)


def _evaluate_dynamic_exit(pos, current_price, age_h, mode):
    """Evaluate whether to modify exit behavior based on ML + trend + sentiment.

    Returns: None (no action), or dict with:
      {"action": "EARLY_EXIT"|"TRAIL_ATR"|"PARTIAL_CLOSE"|"PROFIT_LOCK", "reason": "...", ...}

    Priority order:
      1. PROFIT_LOCK — high conf + big winner -> lock 30% + trail rest
      2. DYNAMIC_TRAIL — ML conviction >=0.68 + profitable >=0.4% -> trail
      2b. SENTIMENT_TRAIL — ML >=0.68 + profitable >=0.25% + high sentiment -> trail
      3. PARTIAL_PROFIT — medium/high conf + winning >=0.7% -> close 50%
      4. ML_PROB_DROP — medium conf + losing -> cut early
      5. Near-expiry smart exits
      6. Mid-hold adjustments
    """
    notes_str = pos.get("notes") or ""
    np = _parse_notes_dict(notes_str)
    ml_wp_str = np.get("ml_wp", "0.5")
    ml_conf = np.get("ml_conf", "MEDIUM")

    try:
        ml_wp = float(ml_wp_str) if ml_wp_str != "N/A" else 0.5
    except (ValueError, TypeError):
        ml_wp = 0.5

    entry = float(pos.get("entry_price") or 0)
    side = (pos.get("side") or "").upper()
    if entry <= 0:
        return None

    # Calculate current unrealized PnL %
    if side == "LONG":
        pnl_pct = (current_price - entry) / entry * 100
    else:
        pnl_pct = (entry - current_price) / entry * 100

    # Get ATR for trailing calculation
    atr_pct_str = np.get("atr_pct", "0.5")
    try:
        atr_pct = float(atr_pct_str) if atr_pct_str != "None" else 0.5
    except (ValueError, TypeError):
        atr_pct = 0.5

    # Parse sent_boost for sentiment trail
    try:
        sent_boost = int(np.get("sent_boost", "0"))
    except (ValueError, TypeError):
        sent_boost = 0

    # Trail tighten factor (auto-learned)
    tighten = _get_trail_tighten_factor()

    # Max hold for this mode
    if mode == "SCALP":
        max_hold_h = SCALP_MAX_HOLD_MIN() / 60.0
    elif mode == "MID":
        max_hold_h = MID_MAX_HOLD_H()
    else:
        max_hold_h = PERP_MAX_HOLD_H()

    near_expiry = age_h >= max_hold_h * 0.8

    # ── 1. PROFIT_LOCK: High conf + big winner -> close 30% + trail rest ──
    if pnl_pct >= 1.2 and ml_conf == "HIGH" and not _has_partial_close(pos["id"]):
        trail = max(0.003, atr_pct / 100 * 1.0 * tighten)
        _log_dynamic_exit(pos["id"], "PROFIT_LOCK", ml_wp, ml_conf, pnl_pct, age_h,
                          f"profit_lock({pnl_pct:.2f}%)+HIGH_conf+trail({trail:.4f})")
        return {"action": "PROFIT_LOCK", "close_pct": 0.30, "trail_pct": trail, "reason": "PROFIT_LOCK"}

    # ── 2. DYNAMIC_TRAIL: ML conviction >=0.68 + profitable >=0.4% -> trail ──
    if ml_wp >= 0.68 and pnl_pct > 0.4:
        trail = max(0.003, atr_pct / 100 * 1.2 * tighten)
        _log_dynamic_exit(pos["id"], "DYNAMIC_TRAIL", ml_wp, ml_conf, pnl_pct, age_h,
                          f"ml_wp({ml_wp:.2f})+profitable({pnl_pct:.2f}%)+tighten({tighten:.2f})")
        return {"action": "TRAIL_ATR", "trail_pct": trail, "reason": "DYNAMIC_TRAIL"}

    # ── 2b. SENTIMENT_TRAIL: ML >=0.68 + profitable >=0.25% + high sentiment ──
    if ml_wp >= 0.68 and pnl_pct > 0.25 and sent_boost >= 8:
        trail = max(0.003, atr_pct / 100 * 1.2 * tighten)
        _log_dynamic_exit(pos["id"], "SENTIMENT_TRAIL", ml_wp, ml_conf, pnl_pct, age_h,
                          f"sentiment_trail({pnl_pct:.2f}%)+sent_boost({sent_boost})+tighten({tighten:.2f})")
        return {"action": "TRAIL_ATR", "trail_pct": trail, "reason": "SENTIMENT_TRAIL"}

    # ── 3. PARTIAL_PROFIT: Medium/High conf + winning >=0.7% -> close 50% ──
    if pnl_pct >= 0.7 and ml_conf in ("MEDIUM", "HIGH") and not _has_partial_close(pos["id"]):
        _log_dynamic_exit(pos["id"], "PARTIAL_PROFIT", ml_wp, ml_conf, pnl_pct, age_h,
                          f"partial_profit({pnl_pct:.2f}%)+{ml_conf}_conf({ml_wp:.2f})")
        return {"action": "PARTIAL_CLOSE", "close_pct": 0.50, "reason": "PARTIAL_PROFIT"}

    # ── 4. ML_PROB_DROP: Medium conf + losing -> cut early ──
    if ml_conf == "MEDIUM" and pnl_pct < -0.3:
        _log_dynamic_exit(pos["id"], "ML_PROB_DROP", ml_wp, ml_conf, pnl_pct, age_h,
                          f"medium_conf({ml_wp:.2f})+losing({pnl_pct:.2f}%)")
        return {"action": "EARLY_EXIT", "reason": "ML_PROB_DROP"}

    # ── 5. Near-expiry smart exits ──
    if near_expiry:
        if pnl_pct > 0.3 and ml_wp >= 0.55:
            trail = max(0.003, atr_pct / 100 * 1.5 * tighten)
            _log_dynamic_exit(pos["id"], "TRAIL_ATR", ml_wp, ml_conf, pnl_pct, age_h,
                              f"near_expiry+profitable({pnl_pct:.2f}%)+ml_favorable({ml_wp:.2f})")
            return {"action": "TRAIL_ATR", "trail_pct": trail, "reason": "TRAILING_ATR_EXTEND"}

        if pnl_pct < -0.5 and ml_wp < 0.45:
            _log_dynamic_exit(pos["id"], "EARLY_EXIT", ml_wp, ml_conf, pnl_pct, age_h,
                              f"near_expiry+losing({pnl_pct:.2f}%)+ml_bearish({ml_wp:.2f})")
            return {"action": "EARLY_EXIT", "reason": "ML_EARLY_EXIT"}

    # ── 6. Mid-hold dynamic adjustments ──
    if pnl_pct > 0.5 and ml_conf == "HIGH":
        trail = max(0.003, atr_pct / 100 * 0.8 * tighten)
        _log_dynamic_exit(pos["id"], "TRAIL_ATR", ml_wp, ml_conf, pnl_pct, age_h,
                          f"mid_hold+winning({pnl_pct:.2f}%)+HIGH_conf")
        return {"action": "TRAIL_ATR", "trail_pct": trail, "reason": "TRAILING_ATR_WINNER"}

    if pnl_pct < -1.0 and ml_wp < 0.40:
        _log_dynamic_exit(pos["id"], "EARLY_EXIT", ml_wp, ml_conf, pnl_pct, age_h,
                          f"mid_hold+deep_loss({pnl_pct:.2f}%)+ml_very_bearish({ml_wp:.2f})")
        return {"action": "EARLY_EXIT", "reason": "ML_EARLY_EXIT"}

    return None  # no dynamic action, continue normal monitoring'''

assert old in content, "FAIL [5/9]: _evaluate_dynamic_exit not found"
content = content.replace(old, new, 1)
changes += 1
print("[5/9] Replaced _evaluate_dynamic_exit + added helper functions")

# ═══════════════════════════════════════════════════════════════════════════════
# 6. Add PROFIT_LOCK handler in swing monitor
# ═══════════════════════════════════════════════════════════════════════════════
old = '''            dyn = _evaluate_dynamic_exit(pos, price, age_h, "SWING")
            if dyn:
                if dyn["action"] == "EARLY_EXIT":
                    exit_reason = dyn["reason"]
                elif dyn["action"] == "PARTIAL_CLOSE":
                    _execute_partial_close(pos_id, dyn["close_pct"], price, dyn["reason"])
                elif dyn["action"] == "TRAIL_ATR":
                    if side == "LONG":
                        peak = float(pos.get("peak_price") or price)
                    else:
                        peak = float(pos.get("trough_price") or price)
                    if side == "LONG":
                        trail_stop = peak * (1 - dyn["trail_pct"])
                        if price <= trail_stop:
                            exit_reason = dyn["reason"]
                    else:
                        trail_stop = peak * (1 + dyn["trail_pct"])
                        if price >= trail_stop:
                            exit_reason = dyn["reason"]'''
new = '''            dyn = _evaluate_dynamic_exit(pos, price, age_h, "SWING")
            if dyn:
                if dyn["action"] == "EARLY_EXIT":
                    exit_reason = dyn["reason"]
                elif dyn["action"] == "PARTIAL_CLOSE":
                    _execute_partial_close(pos_id, dyn["close_pct"], price, dyn["reason"])
                elif dyn["action"] == "TRAIL_ATR":
                    if side == "LONG":
                        peak = float(pos.get("peak_price") or price)
                    else:
                        peak = float(pos.get("trough_price") or price)
                    if side == "LONG":
                        trail_stop = peak * (1 - dyn["trail_pct"])
                        if price <= trail_stop:
                            exit_reason = dyn["reason"]
                    else:
                        trail_stop = peak * (1 + dyn["trail_pct"])
                        if price >= trail_stop:
                            exit_reason = dyn["reason"]
                elif dyn["action"] == "PROFIT_LOCK":
                    _execute_partial_close(pos_id, dyn["close_pct"], price, dyn["reason"])
                    if side == "LONG":
                        peak = float(pos.get("peak_price") or price)
                        trail_stop = peak * (1 - dyn["trail_pct"])
                        if price <= trail_stop:
                            exit_reason = dyn["reason"]
                    else:
                        peak = float(pos.get("trough_price") or price)
                        trail_stop = peak * (1 + dyn["trail_pct"])
                        if price >= trail_stop:
                            exit_reason = dyn["reason"]'''
assert old in content, "FAIL [6/9]: swing monitor handler not found"
content = content.replace(old, new, 1)
changes += 1
print("[6/9] Added PROFIT_LOCK handler in swing monitor")

# ═══════════════════════════════════════════════════════════════════════════════
# 7. Add PROFIT_LOCK handler in scalp monitor
# ═══════════════════════════════════════════════════════════════════════════════
old = '''            dyn = _evaluate_dynamic_exit(pos, price, age_h, "SCALP")
            if dyn:
                if dyn["action"] == "EARLY_EXIT":
                    exit_reason = dyn["reason"]
                elif dyn["action"] == "PARTIAL_CLOSE":
                    _execute_partial_close(pos_id, dyn["close_pct"], price, dyn["reason"])
                elif dyn["action"] == "TRAIL_ATR":
                    if side == "LONG":
                        peak = float(pos.get("peak_price") or price)
                    else:
                        peak = float(pos.get("trough_price") or price)
                    if side == "LONG":
                        trail_stop = peak * (1 - dyn["trail_pct"])
                        if price <= trail_stop:
                            exit_reason = dyn["reason"]
                    else:
                        trail_stop = peak * (1 + dyn["trail_pct"])
                        if price >= trail_stop:
                            exit_reason = dyn["reason"]'''
new = '''            dyn = _evaluate_dynamic_exit(pos, price, age_h, "SCALP")
            if dyn:
                if dyn["action"] == "EARLY_EXIT":
                    exit_reason = dyn["reason"]
                elif dyn["action"] == "PARTIAL_CLOSE":
                    _execute_partial_close(pos_id, dyn["close_pct"], price, dyn["reason"])
                elif dyn["action"] == "TRAIL_ATR":
                    if side == "LONG":
                        peak = float(pos.get("peak_price") or price)
                    else:
                        peak = float(pos.get("trough_price") or price)
                    if side == "LONG":
                        trail_stop = peak * (1 - dyn["trail_pct"])
                        if price <= trail_stop:
                            exit_reason = dyn["reason"]
                    else:
                        trail_stop = peak * (1 + dyn["trail_pct"])
                        if price >= trail_stop:
                            exit_reason = dyn["reason"]
                elif dyn["action"] == "PROFIT_LOCK":
                    _execute_partial_close(pos_id, dyn["close_pct"], price, dyn["reason"])
                    if side == "LONG":
                        peak = float(pos.get("peak_price") or price)
                        trail_stop = peak * (1 - dyn["trail_pct"])
                        if price <= trail_stop:
                            exit_reason = dyn["reason"]
                    else:
                        peak = float(pos.get("trough_price") or price)
                        trail_stop = peak * (1 + dyn["trail_pct"])
                        if price >= trail_stop:
                            exit_reason = dyn["reason"]'''
assert old in content, "FAIL [7/9]: scalp monitor handler not found"
content = content.replace(old, new, 1)
changes += 1
print("[7/9] Added PROFIT_LOCK handler in scalp monitor")

# ═══════════════════════════════════════════════════════════════════════════════
# 8. Add PROFIT_LOCK handler in mid monitor (uses 'reason' not 'exit_reason')
# ═══════════════════════════════════════════════════════════════════════════════
old = '''            dyn = _evaluate_dynamic_exit(pos, price, age_h, "MID")
            if dyn:
                if dyn["action"] == "EARLY_EXIT":
                    reason = dyn["reason"]
                elif dyn["action"] == "PARTIAL_CLOSE":
                    _execute_partial_close(pos_id, dyn["close_pct"], price, dyn["reason"])
                elif dyn["action"] == "TRAIL_ATR":
                    if side == "LONG":
                        peak = float(pos.get("peak_price") or price)
                    else:
                        peak = float(pos.get("trough_price") or price)
                    if side == "LONG":
                        trail_stop = peak * (1 - dyn["trail_pct"])
                        if price <= trail_stop:
                            reason = dyn["reason"]
                    else:
                        trail_stop = peak * (1 + dyn["trail_pct"])
                        if price >= trail_stop:
                            reason = dyn["reason"]'''
new = '''            dyn = _evaluate_dynamic_exit(pos, price, age_h, "MID")
            if dyn:
                if dyn["action"] == "EARLY_EXIT":
                    reason = dyn["reason"]
                elif dyn["action"] == "PARTIAL_CLOSE":
                    _execute_partial_close(pos_id, dyn["close_pct"], price, dyn["reason"])
                elif dyn["action"] == "TRAIL_ATR":
                    if side == "LONG":
                        peak = float(pos.get("peak_price") or price)
                    else:
                        peak = float(pos.get("trough_price") or price)
                    if side == "LONG":
                        trail_stop = peak * (1 - dyn["trail_pct"])
                        if price <= trail_stop:
                            reason = dyn["reason"]
                    else:
                        trail_stop = peak * (1 + dyn["trail_pct"])
                        if price >= trail_stop:
                            reason = dyn["reason"]
                elif dyn["action"] == "PROFIT_LOCK":
                    _execute_partial_close(pos_id, dyn["close_pct"], price, dyn["reason"])
                    if side == "LONG":
                        peak = float(pos.get("peak_price") or price)
                        trail_stop = peak * (1 - dyn["trail_pct"])
                        if price <= trail_stop:
                            reason = dyn["reason"]
                    else:
                        peak = float(pos.get("trough_price") or price)
                        trail_stop = peak * (1 + dyn["trail_pct"])
                        if price >= trail_stop:
                            reason = dyn["reason"]'''
assert old in content, "FAIL [8/9]: mid monitor handler not found"
content = content.replace(old, new, 1)
changes += 1
print("[8/9] Added PROFIT_LOCK handler in mid monitor")

# ═══════════════════════════════════════════════════════════════════════════════
# 9. Add auto-tune counter increment + call in _close_perp_position
# ═══════════════════════════════════════════════════════════════════════════════
old = '''            logger.info("[DYN-EXIT OUTCOME] pos=%d reason=%s pnl=%.2f%% → %s",
                        position_id, exit_reason, leveraged_pct, _outcome)
    except Exception:
        pass

    # Create post-exit tracking record for price monitoring after close'''
new = '''            logger.info("[DYN-EXIT OUTCOME] pos=%d reason=%s pnl=%.2f%% → %s",
                        position_id, exit_reason, leveraged_pct, _outcome)
    except Exception:
        pass

    # Auto-tune: increment counter and maybe run analysis
    global _auto_tune_trade_counter
    _auto_tune_trade_counter += 1
    _auto_tune_exit_params()

    # Create post-exit tracking record for price monitoring after close'''
assert old in content, "FAIL [9/9]: post-exit anchor not found"
content = content.replace(old, new, 1)
changes += 1
print("[9/9] Added auto-tune counter + call in _close_perp_position")

# ═══════════════════════════════════════════════════════════════════════════════
# Write the file
# ═══════════════════════════════════════════════════════════════════════════════
assert changes == 9, f"Expected 9 changes, got {changes}"
FILE.write_text(content)

# Verify it compiles
import py_compile
py_compile.compile(str(FILE), doraise=True)
print(f"\n✅ Patch 45 applied successfully — all {changes} changes verified, file compiles OK")
