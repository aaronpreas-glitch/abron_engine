"""
Patch 82a — perp_executor.py: Edge Acceleration (Exit Overhaul)
1. New config lambdas: SCALP_BREAKEVEN_TRIGGER, EARLY_CUT_*, WINNER_EXTEND_TRAIL_PCT
2. Tighten SCALP_STOP_PCT default from 0.8 to 0.5
3. Schema: add trail_stop_price column
4. Rewrite scalp_monitor_step with persistent trail, breakeven, early cut, winner extend
5. Register new exit reasons + outcome classification
6. Apply persistent trail + winner extend to perp_monitor_step and mid_monitor_step
7. Update .env SCALP_STOP_PCT=0.5
"""
import subprocess, sys, re
from pathlib import Path

ROOT = Path("/root/memecoin_engine")
PE   = ROOT / "utils" / "perp_executor.py"

text = PE.read_text()

# ─────────────────────────────────────────────────────────────────────────────
# R1 — Config lambdas after PORTFOLIO_MAX_EXPOSURE, before DB_PATH
# ─────────────────────────────────────────────────────────────────────────────
OLD_R1 = 'PORTFOLIO_MAX_EXPOSURE = lambda: _float("PORTFOLIO_MAX_EXPOSURE", 800.0)\n\nDB_PATH = os.path.join('
NEW_R1 = ('PORTFOLIO_MAX_EXPOSURE = lambda: _float("PORTFOLIO_MAX_EXPOSURE", 800.0)\n'
          '\n'
          '# ── Edge Acceleration config (Patch 82) ────────────────────────────────────────\n'
          'SCALP_BREAKEVEN_TRIGGER = lambda: _float("SCALP_BREAKEVEN_TRIGGER", 0.3)\n'
          'EARLY_CUT_MINUTES       = lambda: _float("EARLY_CUT_MINUTES", 5.0)\n'
          'EARLY_CUT_LOSS_PCT      = lambda: _float("EARLY_CUT_LOSS_PCT", 0.3)\n'
          'WINNER_EXTEND_TRAIL_PCT = lambda: _float("WINNER_EXTEND_TRAIL_PCT", 0.3)\n'
          '\n'
          'DB_PATH = os.path.join(')

assert OLD_R1 in text, "R1 anchor (PORTFOLIO_MAX_EXPOSURE + DB_PATH) not found"
text = text.replace(OLD_R1, NEW_R1)
print("✓ R1: Edge Acceleration config lambdas added")

# ─────────────────────────────────────────────────────────────────────────────
# R2 — Tighten SCALP_STOP_PCT default from 0.8 to 0.5
# ─────────────────────────────────────────────────────────────────────────────
OLD_R2 = 'SCALP_STOP_PCT      = lambda: _float("SCALP_STOP_PCT", 0.8)'
NEW_R2 = 'SCALP_STOP_PCT      = lambda: _float("SCALP_STOP_PCT", 0.5)'

assert OLD_R2 in text, "R2 anchor (SCALP_STOP_PCT 0.8) not found"
text = text.replace(OLD_R2, NEW_R2)
print("✓ R2: SCALP_STOP_PCT default → 0.5")

# ─────────────────────────────────────────────────────────────────────────────
# R3 — Schema migration: trail_stop_price column
# ─────────────────────────────────────────────────────────────────────────────
OLD_R3 = ('        # Add partial_closes column to perp_positions\n'
          '        try:\n'
          '            c.execute("ALTER TABLE perp_positions ADD COLUMN partial_closes TEXT")\n'
          '        except Exception:\n'
          '            pass  # already exists\n'
          '        c.commit()')
NEW_R3 = ('        # Add partial_closes column to perp_positions\n'
          '        try:\n'
          '            c.execute("ALTER TABLE perp_positions ADD COLUMN partial_closes TEXT")\n'
          '        except Exception:\n'
          '            pass  # already exists\n'
          '        # Add trail_stop_price column (Patch 82)\n'
          '        try:\n'
          '            c.execute("ALTER TABLE perp_positions ADD COLUMN trail_stop_price REAL")\n'
          '        except Exception:\n'
          '            pass  # already exists\n'
          '        c.commit()')

assert OLD_R3 in text, "R3 anchor (partial_closes migration) not found"
text = text.replace(OLD_R3, NEW_R3)
print("✓ R3: trail_stop_price column migration added")

# ─────────────────────────────────────────────────────────────────────────────
# R4 — Rewrite scalp_monitor_step exit logic
# ─────────────────────────────────────────────────────────────────────────────
OLD_R4 = '''        exit_reason = None

        if side == "LONG":
            if price <= stop:
                exit_reason = "STOP_LOSS"
            elif tp1 and price >= tp1:
                exit_reason = "TP1"
        else:  # SHORT
            if price >= stop:
                exit_reason = "STOP_LOSS"
            elif tp1 and price <= tp1:
                exit_reason = "TP1"

        # Hard safety rail: never exceed 2.5x max hold time
        hard_max_h = max_hold_h * 2.5
        if age_h >= hard_max_h:
            exit_reason = "HARD_TIME_LIMIT"

        # Dynamic exit evaluation (before TIME_LIMIT fallback)
        if exit_reason is None and not _check_dynamic_exit_circuit_breaker():
            dyn = _evaluate_dynamic_exit(pos, price, age_h, "SCALP")
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
                            exit_reason = dyn["reason"]

        # TIME_LIMIT fallback — skip for Moonshot positions (Patch 81)
        if exit_reason is None and age_h >= max_hold_h:
            if "MOONSHOT" not in (pos.get("notes") or ""):
                exit_reason = "TIME_LIMIT"

        if exit_reason:
            result = _close_perp_position(pos_id, price, exit_reason)
            paper_label = "PAPER" if pos["dry_run"] else "LIVE"
            if result:
                logger.info(
                    "[SCALP %s] Closed %s %s @ $%.4f  reason=%s  pnl=%.2f%%",
                    paper_label, side, symbol, price, exit_reason, result.get("pnl_pct", 0),
                )'''

NEW_R4 = '''        exit_reason = None
        _lev = float(pos.get("leverage") or SCALP_LEVERAGE())

        # Compute unrealized PnL %
        if side == "LONG":
            _raw_pnl = (price - entry) / entry * 100
        else:
            _raw_pnl = (entry - price) / entry * 100

        # ── 0. Persistent Trail Stop (Patch 82) ─────────────────────────
        _tsp = pos.get("trail_stop_price")
        if _tsp is not None:
            _tsp = float(_tsp)
            if side == "LONG" and price <= _tsp:
                exit_reason = "TRAIL_STOP"
            elif side == "SHORT" and price >= _tsp:
                exit_reason = "TRAIL_STOP"

        # ── 1. Early Cut on Fast Losers (Patch 82) ──────────────────────
        if exit_reason is None:
            _age_min = age_h * 60
            if _age_min <= EARLY_CUT_MINUTES() and _raw_pnl < -EARLY_CUT_LOSS_PCT():
                exit_reason = "EARLY_CUT"
                logger.info("[EARLY_CUT] %s %s pnl=%.2f%% age=%.1fmin", symbol, side, _raw_pnl, _age_min)

        # ── 2. Breakeven Activation (Patch 82) ──────────────────────────
        if exit_reason is None and _tsp is None:
            _mfe_val = float(pos.get("mfe") or 0)
            _mfe_pct = _mfe_val * 100 if abs(_mfe_val) < 1.0 else _mfe_val
            if _mfe_pct >= SCALP_BREAKEVEN_TRIGGER():
                try:
                    with _conn() as _c82:
                        _c82.execute("UPDATE perp_positions SET trail_stop_price=? WHERE id=?",
                                     (entry, pos_id))
                        _c82.commit()
                    _tsp = entry
                    logger.info("[BREAKEVEN] %s %s trail→$%.4f (mfe=%.2f%%)", symbol, side, entry, _mfe_pct)
                except Exception:
                    pass

        # ── 3. Standard SL/TP checks ────────────────────────────────────
        if exit_reason is None:
            if side == "LONG":
                if price <= stop:
                    exit_reason = "STOP_LOSS"
                elif tp1 and price >= tp1:
                    exit_reason = "TP1"
            else:
                if price >= stop:
                    exit_reason = "STOP_LOSS"
                elif tp1 and price <= tp1:
                    exit_reason = "TP1"

        # Hard safety rail: never exceed 2.5x max hold time
        hard_max_h = max_hold_h * 2.5
        if exit_reason is None and age_h >= hard_max_h:
            exit_reason = "HARD_TIME_LIMIT"

        # ── 5. Dynamic exit + persistent trail (Patch 82) ───────────────
        if exit_reason is None and not _check_dynamic_exit_circuit_breaker():
            dyn = _evaluate_dynamic_exit(pos, price, age_h, "SCALP")
            if dyn:
                if dyn["action"] == "EARLY_EXIT":
                    exit_reason = dyn["reason"]
                elif dyn["action"] == "PARTIAL_CLOSE":
                    _execute_partial_close(pos_id, dyn["close_pct"], price, dyn["reason"])
                elif dyn["action"] in ("TRAIL_ATR", "PROFIT_LOCK"):
                    if dyn["action"] == "PROFIT_LOCK":
                        _execute_partial_close(pos_id, dyn["close_pct"], price, dyn["reason"])
                    # Compute + persist trail (Patch 82)
                    if side == "LONG":
                        _pk = float(pos.get("peak_price") or price)
                        _new_t = _pk * (1 - dyn["trail_pct"])
                    else:
                        _pk = float(pos.get("trough_price") or price)
                        _new_t = _pk * (1 + dyn["trail_pct"])
                    # Ratchet: only update if tighter
                    if _tsp is None or (side == "LONG" and _new_t > _tsp) or (side == "SHORT" and _new_t < _tsp):
                        try:
                            with _conn() as _c82:
                                _c82.execute("UPDATE perp_positions SET trail_stop_price=? WHERE id=?",
                                             (_new_t, pos_id))
                                _c82.commit()
                            _tsp = _new_t
                        except Exception:
                            pass
                    if _tsp is not None:
                        if side == "LONG" and price <= _tsp:
                            exit_reason = dyn["reason"]
                        elif side == "SHORT" and price >= _tsp:
                            exit_reason = dyn["reason"]

        # ── 6. Winner Extension at TIME_LIMIT (Patch 82) ────────────────
        if exit_reason is None and age_h >= max_hold_h:
            if "MOONSHOT" not in (pos.get("notes") or ""):
                if _raw_pnl > 0:
                    if side == "LONG":
                        _pk = float(pos.get("peak_price") or price)
                        _ext = _pk * (1 - WINNER_EXTEND_TRAIL_PCT() / 100)
                    else:
                        _pk = float(pos.get("trough_price") or price)
                        _ext = _pk * (1 + WINNER_EXTEND_TRAIL_PCT() / 100)
                    if _tsp is None or (side == "LONG" and _ext > _tsp) or (side == "SHORT" and _ext < _tsp):
                        try:
                            with _conn() as _c82:
                                _c82.execute("UPDATE perp_positions SET trail_stop_price=? WHERE id=?",
                                             (_ext, pos_id))
                                _c82.commit()
                            _tsp = _ext
                        except Exception:
                            pass
                    logger.info("[WINNER_EXTEND] %s %s pnl=+%.2f%% trail→$%.4f", symbol, side, _raw_pnl, _tsp or 0)
                    if _tsp is not None:
                        if side == "LONG" and price <= _tsp:
                            exit_reason = "WINNER_TRAIL"
                        elif side == "SHORT" and price >= _tsp:
                            exit_reason = "WINNER_TRAIL"
                else:
                    exit_reason = "TIME_LIMIT"

        if exit_reason:
            result = _close_perp_position(pos_id, price, exit_reason)
            paper_label = "PAPER" if pos["dry_run"] else "LIVE"
            if result:
                logger.info(
                    "[SCALP %s] Closed %s %s @ $%.4f  reason=%s  pnl=%.2f%%",
                    paper_label, side, symbol, price, exit_reason, result.get("pnl_pct", 0),
                )'''

assert OLD_R4 in text, "R4 anchor (scalp_monitor exit_reason=None through close) not found"
text = text.replace(OLD_R4, NEW_R4)
print("✓ R4: scalp_monitor_step rewritten with persistent trail + breakeven + early cut + winner extend")

# ─────────────────────────────────────────────────────────────────────────────
# R5 — Register new exit reasons
# ─────────────────────────────────────────────────────────────────────────────
OLD_R5 = ('        _dyn_reasons = ("ML_EARLY_EXIT", "ML_PROB_DROP", "DYNAMIC_TRAIL",\n'
          '                        "TRAILING_ATR_EXTEND", "TRAILING_ATR_WINNER", "PARTIAL_PROFIT",\n'
          '                        "PROFIT_LOCK", "SENTIMENT_TRAIL",\n'
          '                        "PROTECT_PROFIT", "TRAIL_EARLY")')
NEW_R5 = ('        _dyn_reasons = ("ML_EARLY_EXIT", "ML_PROB_DROP", "DYNAMIC_TRAIL",\n'
          '                        "TRAILING_ATR_EXTEND", "TRAILING_ATR_WINNER", "PARTIAL_PROFIT",\n'
          '                        "PROFIT_LOCK", "SENTIMENT_TRAIL",\n'
          '                        "PROTECT_PROFIT", "TRAIL_EARLY",\n'
          '                        "TRAIL_STOP", "EARLY_CUT", "WINNER_TRAIL")')

assert OLD_R5 in text, "R5 anchor (_dyn_reasons tuple) not found"
text = text.replace(OLD_R5, NEW_R5)
print("✓ R5: New exit reasons registered")

# ─────────────────────────────────────────────────────────────────────────────
# R6 — Outcome classification for new exit reasons
# ─────────────────────────────────────────────────────────────────────────────
OLD_R6 = ('            elif exit_reason == "PROTECT_PROFIT" and leveraged_pct > 0:\n'
          '                _outcome = "good_call"  # locked in profit before giveback')
NEW_R6 = ('            elif exit_reason == "PROTECT_PROFIT" and leveraged_pct > 0:\n'
          '                _outcome = "good_call"  # locked in profit before giveback\n'
          '            elif exit_reason == "EARLY_CUT" and leveraged_pct < 0:\n'
          '                _outcome = "good_call"  # correctly cut a fast loser\n'
          '            elif exit_reason == "EARLY_CUT" and leveraged_pct > 0.5:\n'
          '                _outcome = "bad_call"  # cut a winner too early\n'
          '            elif exit_reason in ("TRAIL_STOP", "WINNER_TRAIL") and leveraged_pct > 0:\n'
          '                _outcome = "good_call"  # persistent trail locked profit\n'
          '            elif exit_reason in ("TRAIL_STOP", "WINNER_TRAIL") and leveraged_pct < -0.5:\n'
          '                _outcome = "bad_call"  # trail did not help')

assert OLD_R6 in text, "R6 anchor (PROTECT_PROFIT outcome) not found"
text = text.replace(OLD_R6, NEW_R6)
print("✓ R6: Outcome classification for new exit reasons")

# ─────────────────────────────────────────────────────────────────────────────
# R7 — perp_monitor_step (SWING): persistent trail + winner extension
# ─────────────────────────────────────────────────────────────────────────────
OLD_R7 = '''        exit_reason = None

        if side == "LONG":
            if price <= stop:
                exit_reason = "STOP_LOSS"
            elif tp2 and price >= tp2:
                exit_reason = "TP2"
            elif tp1 and price >= tp1:
                exit_reason = "TP1"
        else:  # SHORT
            if price >= stop:
                exit_reason = "STOP_LOSS"
            elif tp2 and price <= tp2:
                exit_reason = "TP2"
            elif tp1 and price <= tp1:
                exit_reason = "TP1"

        # Hard safety rail: never exceed 2.5x max hold time
        hard_max_h = max_hold * 2.5
        if age_h >= hard_max_h:
            exit_reason = "HARD_TIME_LIMIT"

        # Dynamic exit evaluation (before TIME_LIMIT fallback)
        if exit_reason is None and not _check_dynamic_exit_circuit_breaker():
            dyn = _evaluate_dynamic_exit(pos, price, age_h, "SWING")
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
                            exit_reason = dyn["reason"]

        # TIME_LIMIT fallback — skip for Moonshot positions (Patch 81)
        if exit_reason is None and age_h >= max_hold:
            if "MOONSHOT" not in (pos.get("notes") or ""):
                exit_reason = "TIME_LIMIT"

        if exit_reason:
            result = _close_perp_position(pos_id, price, exit_reason)
            mode   = "PAPER" if pos["dry_run"] else "LIVE"
            if result:
                logger.info(
                    "[PERP %s] Closed %s %s @ $%.4f  reason=%s  pnl=%.2f%%",
                    mode, side, symbol, price, exit_reason, result.get("pnl_pct", 0),
                )'''

NEW_R7 = '''        exit_reason = None

        # ── Persistent Trail Stop check (Patch 82) ──────────────────────
        _tsp = pos.get("trail_stop_price")
        if _tsp is not None:
            _tsp = float(_tsp)
            if side == "LONG" and price <= _tsp:
                exit_reason = "TRAIL_STOP"
            elif side == "SHORT" and price >= _tsp:
                exit_reason = "TRAIL_STOP"

        if exit_reason is None:
            if side == "LONG":
                if price <= stop:
                    exit_reason = "STOP_LOSS"
                elif tp2 and price >= tp2:
                    exit_reason = "TP2"
                elif tp1 and price >= tp1:
                    exit_reason = "TP1"
            else:
                if price >= stop:
                    exit_reason = "STOP_LOSS"
                elif tp2 and price <= tp2:
                    exit_reason = "TP2"
                elif tp1 and price <= tp1:
                    exit_reason = "TP1"

        # Hard safety rail: never exceed 2.5x max hold time
        hard_max_h = max_hold * 2.5
        if exit_reason is None and age_h >= hard_max_h:
            exit_reason = "HARD_TIME_LIMIT"

        # Dynamic exit + persistent trail (Patch 82)
        if exit_reason is None and not _check_dynamic_exit_circuit_breaker():
            dyn = _evaluate_dynamic_exit(pos, price, age_h, "SWING")
            if dyn:
                if dyn["action"] == "EARLY_EXIT":
                    exit_reason = dyn["reason"]
                elif dyn["action"] == "PARTIAL_CLOSE":
                    _execute_partial_close(pos_id, dyn["close_pct"], price, dyn["reason"])
                elif dyn["action"] in ("TRAIL_ATR", "PROFIT_LOCK"):
                    if dyn["action"] == "PROFIT_LOCK":
                        _execute_partial_close(pos_id, dyn["close_pct"], price, dyn["reason"])
                    if side == "LONG":
                        _pk = float(pos.get("peak_price") or price)
                        _new_t = _pk * (1 - dyn["trail_pct"])
                    else:
                        _pk = float(pos.get("trough_price") or price)
                        _new_t = _pk * (1 + dyn["trail_pct"])
                    if _tsp is None or (side == "LONG" and _new_t > _tsp) or (side == "SHORT" and _new_t < _tsp):
                        try:
                            with _conn() as _c82:
                                _c82.execute("UPDATE perp_positions SET trail_stop_price=? WHERE id=?",
                                             (_new_t, pos_id))
                                _c82.commit()
                            _tsp = _new_t
                        except Exception:
                            pass
                    if _tsp is not None:
                        if side == "LONG" and price <= _tsp:
                            exit_reason = dyn["reason"]
                        elif side == "SHORT" and price >= _tsp:
                            exit_reason = dyn["reason"]

        # Winner Extension at TIME_LIMIT (Patch 82)
        if exit_reason is None and age_h >= max_hold:
            if "MOONSHOT" not in (pos.get("notes") or ""):
                _rpnl = ((price - entry) / entry * 100) if side == "LONG" else ((entry - price) / entry * 100)
                if _rpnl > 0:
                    if side == "LONG":
                        _pk = float(pos.get("peak_price") or price)
                        _ext = _pk * (1 - WINNER_EXTEND_TRAIL_PCT() / 100)
                    else:
                        _pk = float(pos.get("trough_price") or price)
                        _ext = _pk * (1 + WINNER_EXTEND_TRAIL_PCT() / 100)
                    if _tsp is None or (side == "LONG" and _ext > _tsp) or (side == "SHORT" and _ext < _tsp):
                        try:
                            with _conn() as _c82:
                                _c82.execute("UPDATE perp_positions SET trail_stop_price=? WHERE id=?",
                                             (_ext, pos_id))
                                _c82.commit()
                            _tsp = _ext
                        except Exception:
                            pass
                    if _tsp is not None:
                        if side == "LONG" and price <= _tsp:
                            exit_reason = "WINNER_TRAIL"
                        elif side == "SHORT" and price >= _tsp:
                            exit_reason = "WINNER_TRAIL"
                else:
                    exit_reason = "TIME_LIMIT"

        if exit_reason:
            result = _close_perp_position(pos_id, price, exit_reason)
            mode   = "PAPER" if pos["dry_run"] else "LIVE"
            if result:
                logger.info(
                    "[PERP %s] Closed %s %s @ $%.4f  reason=%s  pnl=%.2f%%",
                    mode, side, symbol, price, exit_reason, result.get("pnl_pct", 0),
                )'''

assert OLD_R7 in text, "R7 anchor (perp_monitor_step exit logic) not found"
text = text.replace(OLD_R7, NEW_R7)
print("✓ R7: perp_monitor_step (SWING) — persistent trail + winner extension")

# ─────────────────────────────────────────────────────────────────────────────
# R8 — mid_monitor_step: persistent trail + early cut + winner extension
# ─────────────────────────────────────────────────────────────────────────────
OLD_R8 = '''        reason = None
        _winner_extended = False  # set True by WINNER_RUN_EXTEND to skip TIME_LIMIT
        if side == 'LONG':
            if price >= tp_price:   reason = 'TP1'
            elif price <= sl_price: reason = 'STOP_LOSS'
        else:
            if price <= tp_price:   reason = 'TP1'
            elif price >= sl_price: reason = 'STOP_LOSS'

        # Hard safety rail: never exceed 2.5x max hold time
        hard_max_h = max_hold_h * 2.5
        if age_h >= hard_max_h:
            reason = "HARD_TIME_LIMIT"'''

NEW_R8 = '''        reason = None
        _winner_extended = False  # set True by WINNER_RUN_EXTEND to skip TIME_LIMIT

        # ── Persistent Trail Stop check (Patch 82) ──────────────────────
        _tsp = pos.get("trail_stop_price")
        if _tsp is not None:
            _tsp = float(_tsp)
            if side == "LONG" and price <= _tsp:
                reason = "TRAIL_STOP"
            elif side == "SHORT" and price >= _tsp:
                reason = "TRAIL_STOP"

        # ── Early Cut for MID (Patch 82) ────────────────────────────────
        if reason is None:
            _age_min = age_h * 60
            _rpnl_mid = ((price - entry) / entry * 100) if side == "LONG" else ((entry - price) / entry * 100)
            if _age_min <= EARLY_CUT_MINUTES() and _rpnl_mid < -EARLY_CUT_LOSS_PCT():
                reason = "EARLY_CUT"
                logger.info("[EARLY_CUT MID] %s %s pnl=%.2f%% age=%.1fmin", pos.get("symbol",""), side, _rpnl_mid, _age_min)

        if reason is None:
            if side == 'LONG':
                if price >= tp_price:   reason = 'TP1'
                elif price <= sl_price: reason = 'STOP_LOSS'
            else:
                if price <= tp_price:   reason = 'TP1'
                elif price >= sl_price: reason = 'STOP_LOSS'

        # Hard safety rail: never exceed 2.5x max hold time
        hard_max_h = max_hold_h * 2.5
        if reason is None and age_h >= hard_max_h:
            reason = "HARD_TIME_LIMIT"'''

assert OLD_R8 in text, "R8 anchor (mid_monitor reason=None) not found"
text = text.replace(OLD_R8, NEW_R8)
print("✓ R8: mid_monitor_step — persistent trail + early cut")

# Also persist trail in MID TRAIL_ATR/PROFIT_LOCK handlers
OLD_R8B = '''                elif dyn["action"] == "TRAIL_ATR":
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
                            reason = dyn["reason"]
                elif dyn["action"] == "WINNER_RUN_EXTEND":'''

NEW_R8B = '''                elif dyn["action"] in ("TRAIL_ATR", "PROFIT_LOCK"):
                    if dyn["action"] == "PROFIT_LOCK":
                        _execute_partial_close(pos_id, dyn["close_pct"], price, dyn["reason"])
                    if side == "LONG":
                        _pk = float(pos.get("peak_price") or price)
                        _new_t = _pk * (1 - dyn["trail_pct"])
                    else:
                        _pk = float(pos.get("trough_price") or price)
                        _new_t = _pk * (1 + dyn["trail_pct"])
                    if _tsp is None or (side == "LONG" and _new_t > _tsp) or (side == "SHORT" and _new_t < _tsp):
                        try:
                            with _conn() as _c82:
                                _c82.execute("UPDATE perp_positions SET trail_stop_price=? WHERE id=?",
                                             (_new_t, pos_id))
                                _c82.commit()
                            _tsp = _new_t
                        except Exception:
                            pass
                    if _tsp is not None:
                        if side == "LONG" and price <= _tsp:
                            reason = dyn["reason"]
                        elif side == "SHORT" and price >= _tsp:
                            reason = dyn["reason"]
                elif dyn["action"] == "WINNER_RUN_EXTEND":'''

assert OLD_R8B in text, "R8B anchor (MID TRAIL_ATR/PROFIT_LOCK) not found"
text = text.replace(OLD_R8B, NEW_R8B)
print("✓ R8B: mid_monitor_step — persistent trail in TRAIL_ATR/PROFIT_LOCK")

# MID TIME_LIMIT winner extension
OLD_R8C = ("        # TIME_LIMIT: skip if winner-run extension OR Moonshot is active (Patch 81)\n"
           "        if reason is None and age_h >= max_hold_h and not _winner_extended:\n"
           "            if \"MOONSHOT\" not in (pos.get(\"notes\") or \"\"):\n"
           "                reason = 'TIME_LIMIT'")
NEW_R8C = ("        # TIME_LIMIT: winner extension OR skip for Moonshot/winner-run (Patch 82)\n"
           "        if reason is None and age_h >= max_hold_h and not _winner_extended:\n"
           "            if \"MOONSHOT\" not in (pos.get(\"notes\") or \"\"):\n"
           "                _rpnl_tl = ((price - entry) / entry * 100) if side == 'LONG' else ((entry - price) / entry * 100)\n"
           "                if _rpnl_tl > 0:\n"
           "                    if side == 'LONG':\n"
           "                        _pk = float(pos.get('peak_price') or price)\n"
           "                        _ext = _pk * (1 - WINNER_EXTEND_TRAIL_PCT() / 100)\n"
           "                    else:\n"
           "                        _pk = float(pos.get('trough_price') or price)\n"
           "                        _ext = _pk * (1 + WINNER_EXTEND_TRAIL_PCT() / 100)\n"
           "                    if _tsp is None or (side == 'LONG' and _ext > _tsp) or (side == 'SHORT' and _ext < _tsp):\n"
           "                        try:\n"
           "                            with _conn() as _c82:\n"
           "                                _c82.execute('UPDATE perp_positions SET trail_stop_price=? WHERE id=?',\n"
           "                                             (_ext, pos_id))\n"
           "                                _c82.commit()\n"
           "                            _tsp = _ext\n"
           "                        except Exception:\n"
           "                            pass\n"
           "                    if _tsp is not None:\n"
           "                        if side == 'LONG' and price <= _tsp:\n"
           "                            reason = 'WINNER_TRAIL'\n"
           "                        elif side == 'SHORT' and price >= _tsp:\n"
           "                            reason = 'WINNER_TRAIL'\n"
           "                else:\n"
           "                    reason = 'TIME_LIMIT'")

assert OLD_R8C in text, "R8C anchor (MID TIME_LIMIT) not found"
text = text.replace(OLD_R8C, NEW_R8C)
print("✓ R8C: mid_monitor_step — winner extension at TIME_LIMIT")

# ─────────────────────────────────────────────────────────────────────────────
# Write + compile
# ─────────────────────────────────────────────────────────────────────────────
PE.write_text(text)

r = subprocess.run(
    [sys.executable, "-m", "py_compile", str(PE)],
    capture_output=True, text=True
)
if r.returncode != 0:
    print("✗ compile error:", r.stderr)
    sys.exit(1)
print("✓ perp_executor.py compiles OK")

# ─────────────────────────────────────────────────────────────────────────────
# R9 — Update .env: SCALP_STOP_PCT=0.5
# ─────────────────────────────────────────────────────────────────────────────
env_path = ROOT / ".env"
env_text = env_path.read_text()
if "SCALP_STOP_PCT=" in env_text:
    env_text = re.sub(r'SCALP_STOP_PCT=\S*', 'SCALP_STOP_PCT=0.5', env_text)
else:
    env_text += "\nSCALP_STOP_PCT=0.5\n"
env_path.write_text(env_text)
print("✓ R9: .env updated SCALP_STOP_PCT=0.5")

print("✓ Patch 82a complete")
