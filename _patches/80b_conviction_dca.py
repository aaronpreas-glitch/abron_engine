"""
Patch 80b — perp_executor.py: conviction sizing + winner-run extension + paper DCA
1. Add CONVICTION_SIZE_MULT_LOW/MED/HIGH config lambdas
2. Conviction tier sizing after quality gate (scales position by ml_wp)
3. WINNER_RUN_EXTEND action in _evaluate_dynamic_exit (pnl>=1.2% + ml_wp>=0.70 + MID mode)
4. _try_mid_dca() function (paper-only DCA on MID losers)
5. mid_monitor_step: _winner_extended flag + WINNER_RUN_EXTEND handler + DCA call + TIME_LIMIT guard
"""
import subprocess, sys, re
from pathlib import Path

ROOT = Path("/root/memecoin_engine")
PE   = ROOT / "utils" / "perp_executor.py"

text = PE.read_text()

# ─────────────────────────────────────────────────────────────────────────────
# Replace 1 — Add conviction sizing config lambdas after MID_MAX_OPEN
# ─────────────────────────────────────────────────────────────────────────────
OLD_1 = 'MID_MAX_OPEN        = lambda: _int("MID_MAX_OPEN", 8)\n\nDB_PATH = os.path.join('
NEW_1 = ('MID_MAX_OPEN        = lambda: _int("MID_MAX_OPEN", 8)\n'
         '\n'
         '# ── Conviction tier sizing multipliers (Patch 80) ─────────────────────────────\n'
         'CONVICTION_SIZE_MULT_LOW  = lambda: _float("CONVICTION_SIZE_MULT_LOW",  0.3)\n'
         'CONVICTION_SIZE_MULT_MED  = lambda: _float("CONVICTION_SIZE_MULT_MED",  1.0)\n'
         'CONVICTION_SIZE_MULT_HIGH = lambda: _float("CONVICTION_SIZE_MULT_HIGH", 1.8)\n'
         '\n'
         'DB_PATH = os.path.join(')

assert OLD_1 in text, "OLD_1 anchor (MID_MAX_OPEN + DB_PATH) not found"
text = text.replace(OLD_1, NEW_1)
print("✓ conviction config lambdas added")

# ─────────────────────────────────────────────────────────────────────────────
# Replace 2 — Conviction tier sizing AFTER quality gate, BEFORE # Compute exit levels
# ─────────────────────────────────────────────────────────────────────────────
OLD_2 = ('        return False\n'
         '\n'
         '    # Compute exit levels — scalp uses tight TP/SL, swing uses wide swing targets\n'
         '    if is_scalp:\n'
         '        size_usd   = round(float(signal.get("size_usd", SCALP_SIZE_USD())) * combined_mult, 2)')
NEW_2 = ('        return False\n'
         '\n'
         '    # ── Conviction Tier Sizing (Patch 80) ────────────────────────────────────\n'
         '    # Scale position size by ML win-probability tier:\n'
         '    # 0.60–0.69 → LOW mult (cautious), 0.70–0.79 → MED (normal), 0.80+ → HIGH\n'
         '    if _ml_wp_val >= 0.80:\n'
         '        _conviction_mult = CONVICTION_SIZE_MULT_HIGH()\n'
         '    elif _ml_wp_val >= 0.70:\n'
         '        _conviction_mult = CONVICTION_SIZE_MULT_MED()\n'
         '    else:  # 0.60–0.69: passed gate minimum, use cautious size\n'
         '        _conviction_mult = CONVICTION_SIZE_MULT_LOW()\n'
         '    combined_mult = min(round(combined_mult * _conviction_mult, 2), 3.0)\n'
         '    logger.debug("[CONVICTION] %s ml_wp=%.2f tier_mult=%.1fx combined=%.2fx",\n'
         '                 symbol, _ml_wp_val, _conviction_mult, combined_mult)\n'
         '\n'
         '    # Compute exit levels — scalp uses tight TP/SL, swing uses wide swing targets\n'
         '    if is_scalp:\n'
         '        size_usd   = round(float(signal.get("size_usd", SCALP_SIZE_USD())) * combined_mult, 2)')

assert OLD_2 in text, "OLD_2 anchor (return False + Compute exit levels) not found"
text = text.replace(OLD_2, NEW_2)
print("✓ conviction tier sizing inserted")

# ─────────────────────────────────────────────────────────────────────────────
# Replace 3 — WINNER_RUN_EXTEND in _evaluate_dynamic_exit before PROFIT_LOCK
# ─────────────────────────────────────────────────────────────────────────────
OLD_3 = ('    # ── 1. PROFIT_LOCK: High conf + big winner -> close 30% + trail rest ──\n'
         '    if pnl_pct >= 1.2 and ml_conf == "HIGH" and not _has_partial_close(pos["id"]):')
NEW_3 = ('    # ── 0c. WINNER_RUN_EXTEND: MID mode big winner + high ML → disable TIME_LIMIT ──\n'
         '    # Only fires once per position (WINNER_RUN_EXTENDED marker in notes prevents re-fire)\n'
         '    if (mode == "MID" and pnl_pct >= 1.2 and ml_wp >= 0.70\n'
         '            and "WINNER_RUN_EXTENDED" not in notes_str):\n'
         '        _log_dynamic_exit(pos["id"], "WINNER_RUN_EXTEND", ml_wp, ml_conf, pnl_pct, age_h,\n'
         '                          f"winner_run(pnl={pnl_pct:.2f}%_ml_wp={ml_wp:.2f})")\n'
         '        # Mark in notes so this only fires once\n'
         '        try:\n'
         '            import sqlite3 as _sq\n'
         '            _new_notes = (pos.get("notes") or "") + "|WINNER_RUN_EXTENDED=1"\n'
         '            with _sq.connect(DB_PATH) as _c:\n'
         '                _c.execute("UPDATE perp_positions SET notes=? WHERE id=?",\n'
         '                           (_new_notes, pos["id"]))\n'
         '        except Exception as _exc:\n'
         '            logger.warning("[WINNER_RUN] notes update failed: %s", _exc)\n'
         '        return {"action": "WINNER_RUN_EXTEND", "ml_wp": ml_wp, "reason": "WINNER_RUN_EXTEND"}\n'
         '\n'
         '    # ── 1. PROFIT_LOCK: High conf + big winner -> close 30% + trail rest ──\n'
         '    if pnl_pct >= 1.2 and ml_conf == "HIGH" and not _has_partial_close(pos["id"]):')

assert OLD_3 in text, "OLD_3 anchor (PROFIT_LOCK header) not found"
text = text.replace(OLD_3, NEW_3)
print("✓ WINNER_RUN_EXTEND added to _evaluate_dynamic_exit")

# ─────────────────────────────────────────────────────────────────────────────
# Replace 4 — Insert _try_mid_dca() before mid_monitor_step
# ─────────────────────────────────────────────────────────────────────────────
OLD_4 = ('async def mid_monitor_step() -> None:\n'
         '    """Check open MID positions — exits at TP (+8%), SL (-4%), or time limit (6h)."""')
NEW_4 = (
         'def _try_mid_dca(pos, price, entry, side, age_h):\n'
         '    """Attempt a paper DCA on a losing MID position.\n'
         '\n'
         '    Conditions: dry_run=True, pnl < -0.5%, ml_wp >= 0.60, regime != TRANSITION,\n'
         '                last DCA > 4h ago, dca_count < 3, total added <= 200% original size.\n'
         '    On trigger: updates weighted avg entry, widens TP 25%, logs to dca_log.\n'
         '    """\n'
         '    import sqlite3 as _sq\n'
         '    from datetime import datetime, timezone\n'
         '\n'
         '    # Safety: never DCA in real/simulate live or survive mode\n'
         '    if _real_money_mode or _simulate_live_mode or _survive_mode_active:\n'
         '        return\n'
         '    if not pos.get("dry_run"):\n'
         '        return\n'
         '\n'
         '    pos_id = pos["id"]\n'
         '    notes_str = pos.get("notes") or ""\n'
         '    np_dict = _parse_notes_dict(notes_str)\n'
         '\n'
         '    # Parse ML win prob from notes\n'
         '    try:\n'
         '        ml_wp = float(np_dict.get("ml_wp", "0") or "0")\n'
         '    except (ValueError, TypeError):\n'
         '        ml_wp = 0.0\n'
         '\n'
         '    regime = np_dict.get("regime", "")\n'
         '\n'
         '    # Current PnL %\n'
         '    if entry <= 0 or price <= 0:\n'
         '        return\n'
         '    if side == "LONG":\n'
         '        pnl_pct = (price - entry) / entry * 100\n'
         '    else:\n'
         '        pnl_pct = (entry - price) / entry * 100\n'
         '\n'
         '    # Conditions\n'
         '    if pnl_pct >= -0.5:          return  # not down enough\n'
         '    if ml_wp < 0.60:             return  # insufficient conviction\n'
         '    if "TRANSITION" in regime.upper(): return  # skip regime transitions\n'
         '\n'
         '    dca_count = int(pos.get("dca_count") or 0)\n'
         '    if dca_count >= 3:           return  # max 3 DCAs\n'
         '\n'
         '    # 4h cooldown between DCAs\n'
         '    last_dca_ts = pos.get("last_dca_ts")\n'
         '    if last_dca_ts:\n'
         '        try:\n'
         '            last_dt = datetime.fromisoformat(last_dca_ts.replace("Z", "+00:00"))\n'
         '            hrs_since = (datetime.now(timezone.utc) - last_dt).total_seconds() / 3600\n'
         '            if hrs_since < 4.0:\n'
         '                return  # too soon\n'
         '        except Exception:\n'
         '            pass\n'
         '\n'
         '    # 200% cap: total DCA added ≤ 2× original position size\n'
         '    original_size_usd = float(pos.get("size_usd") or 40.0)\n'
         '    total_dca_usd = dca_count * original_size_usd\n'
         '    remaining_room = 2.0 * original_size_usd - total_dca_usd\n'
         '    if remaining_room <= 0:\n'
         '        return  # at cap\n'
         '\n'
         '    dca_size_usd = min(original_size_usd, remaining_room)\n'
         '\n'
         '    # Weighted average new entry\n'
         '    current_size_usd = original_size_usd * (1 + dca_count)\n'
         '    new_avg_entry = ((current_size_usd * entry + dca_size_usd * price)\n'
         '                     / (current_size_usd + dca_size_usd))\n'
         '\n'
         '    # Widen TP by 25% from new avg entry\n'
         '    tp1_price = float(pos.get("tp1_price") or 0)\n'
         '    new_tp1 = tp1_price\n'
         '    if tp1_price > 0 and entry > 0:\n'
         '        orig_tp_dist = abs(tp1_price - entry)\n'
         '        new_tp_dist  = orig_tp_dist * 1.25\n'
         '        new_tp1 = (new_avg_entry + new_tp_dist) if side == "LONG" else (new_avg_entry - new_tp_dist)\n'
         '\n'
         '    ts_now = datetime.now(timezone.utc).isoformat()\n'
         '    reason_str = f"ml_wp={ml_wp:.2f}|regime={regime}|pnl={pnl_pct:.2f}%"\n'
         '\n'
         '    try:\n'
         '        with _sq.connect(DB_PATH) as conn:\n'
         '            # Log the DCA event\n'
         '            conn.execute(\n'
         '                "INSERT INTO dca_log (position_id, ts, symbol, side, dca_number,"\n'
         '                " dca_size_usd, price_at_dca, pnl_at_dca_pct, new_avg_entry, reason)"\n'
         '                " VALUES (?,?,?,?,?,?,?,?,?,?)",\n'
         '                (pos_id, ts_now, pos["symbol"], side, dca_count + 1,\n'
         '                 dca_size_usd, price, pnl_pct, new_avg_entry, reason_str)\n'
         '            )\n'
         '            # Update position\n'
         '            new_notes = notes_str + f"|dca{dca_count+1}@{price:.6g}"\n'
         '            if new_tp1 > 0:\n'
         '                conn.execute(\n'
         '                    "UPDATE perp_positions SET entry_price=?, tp1_price=?, tp2_price=?,"\n'
         '                    " dca_count=?, last_dca_ts=?, notes=? WHERE id=?",\n'
         '                    (new_avg_entry, new_tp1, new_tp1, dca_count+1, ts_now, new_notes, pos_id)\n'
         '                )\n'
         '            else:\n'
         '                conn.execute(\n'
         '                    "UPDATE perp_positions SET entry_price=?, dca_count=?,"\n'
         '                    " last_dca_ts=?, notes=? WHERE id=?",\n'
         '                    (new_avg_entry, dca_count+1, ts_now, new_notes, pos_id)\n'
         '                )\n'
         '        logger.info(\n'
         '            "[MID DCA #%d] %s %s price=%.6g pnl=%.2f%% new_entry=%.6g"\n'
         '            " new_tp=%.6g ml_wp=%.2f size_added=$%.0f",\n'
         '            dca_count+1, side, pos["symbol"], price, pnl_pct,\n'
         '            new_avg_entry, new_tp1, ml_wp, dca_size_usd\n'
         '        )\n'
         '    except Exception as exc:\n'
         '        logger.warning("[MID DCA] DB update failed pos %d: %s", pos_id, exc)\n'
         '\n'
         '\n'
         'async def mid_monitor_step() -> None:\n'
         '    """Check open MID positions — exits at TP (+8%), SL (-4%), or time limit (6h)."""')

assert OLD_4 in text, "OLD_4 anchor (mid_monitor_step def) not found"
text = text.replace(OLD_4, NEW_4)
print("✓ _try_mid_dca() function inserted before mid_monitor_step")

# ─────────────────────────────────────────────────────────────────────────────
# Replace 5 — Add _winner_extended flag init in mid_monitor_step loop
# ─────────────────────────────────────────────────────────────────────────────
OLD_5 = ('        reason = None\n'
         '        if side == \'LONG\':\n'
         '            if price >= tp_price:   reason = \'TP1\'\n'
         '            elif price <= sl_price: reason = \'STOP_LOSS\'')
NEW_5 = ('        reason = None\n'
         '        _winner_extended = False  # set True by WINNER_RUN_EXTEND to skip TIME_LIMIT\n'
         '        if side == \'LONG\':\n'
         '            if price >= tp_price:   reason = \'TP1\'\n'
         '            elif price <= sl_price: reason = \'STOP_LOSS\'')

assert OLD_5 in text, "OLD_5 anchor (reason = None + TP/SL check) not found"
text = text.replace(OLD_5, NEW_5)
print("✓ _winner_extended flag added to mid_monitor_step loop")

# ─────────────────────────────────────────────────────────────────────────────
# Replace 6 — Add WINNER_RUN_EXTEND handler + DCA call + TIME_LIMIT guard
# Replace PROFIT_LOCK handler → end of function
# ─────────────────────────────────────────────────────────────────────────────
OLD_6 = ('                elif dyn["action"] == "PROFIT_LOCK":\n'
         '                    _execute_partial_close(pos_id, dyn["close_pct"], price, dyn["reason"])\n'
         '                    if side == "LONG":\n'
         '                        peak = float(pos.get("peak_price") or price)\n'
         '                        trail_stop = peak * (1 - dyn["trail_pct"])\n'
         '                        if price <= trail_stop:\n'
         '                            reason = dyn["reason"]\n'
         '                    else:\n'
         '                        peak = float(pos.get("trough_price") or price)\n'
         '                        trail_stop = peak * (1 + dyn["trail_pct"])\n'
         '                        if price >= trail_stop:\n'
         '                            reason = dyn["reason"]\n'
         '\n'
         "        if reason is None and age_h >= max_hold_h:\n"
         "            reason = 'TIME_LIMIT'\n"
         '\n'
         "        if reason:\n"
         "            result = _close_perp_position(pos_id, price, reason)\n"
         "            pnl = result.get('pnl_pct', 0) if result else 0\n"
         "            logger.info(\n"
         "                '[MID PAPER] Closed %s %s @ %.4g  reason=%s  pnl=%.2f%%  held=%.1fh',\n"
         "                side, symbol, price, reason, pnl, age_h\n"
         "            )")
NEW_6 = ('                elif dyn["action"] == "PROFIT_LOCK":\n'
         '                    _execute_partial_close(pos_id, dyn["close_pct"], price, dyn["reason"])\n'
         '                    if side == "LONG":\n'
         '                        peak = float(pos.get("peak_price") or price)\n'
         '                        trail_stop = peak * (1 - dyn["trail_pct"])\n'
         '                        if price <= trail_stop:\n'
         '                            reason = dyn["reason"]\n'
         '                    else:\n'
         '                        peak = float(pos.get("trough_price") or price)\n'
         '                        trail_stop = peak * (1 + dyn["trail_pct"])\n'
         '                        if price >= trail_stop:\n'
         '                            reason = dyn["reason"]\n'
         '                elif dyn["action"] == "WINNER_RUN_EXTEND":\n'
         '                    _winner_extended = True\n'
         '                    # Widen TP by 50% — position is running hot, give it more room\n'
         '                    try:\n'
         '                        import sqlite3 as _sq\n'
         '                        orig_tp = float(pos.get("tp1_price") or tp_price)\n'
         '                        if side == "LONG":\n'
         '                            new_tp = entry + abs(orig_tp - entry) * 1.5\n'
         '                        else:\n'
         '                            new_tp = entry - abs(entry - orig_tp) * 1.5\n'
         '                        with _sq.connect(DB_PATH) as _c:\n'
         '                            _c.execute(\n'
         '                                "UPDATE perp_positions SET tp1_price=?, tp2_price=? WHERE id=?",\n'
         '                                (new_tp, new_tp, pos_id)\n'
         '                            )\n'
         '                        tp_price = new_tp\n'
         '                        logger.info(\n'
         '                            "[MID WINNER_RUN] %s %s extending hold window"\n'
         '                            " tp→%.6g ml_wp=%.2f",\n'
         '                            side, symbol, new_tp, dyn.get("ml_wp", 0)\n'
         '                        )\n'
         '                    except Exception as _e:\n'
         '                        logger.warning("[MID WINNER_RUN] TP widen failed: %s", _e)\n'
         '\n'
         '        # Paper DCA: try averaging in on a losing position (before TIME_LIMIT)\n'
         '        if reason is None and pos.get("dry_run") and not _winner_extended:\n'
         '            _try_mid_dca(pos, price, entry, side, age_h)\n'
         '\n'
         "        # TIME_LIMIT: skip if winner-run extension is active (trade is running hot)\n"
         "        if reason is None and age_h >= max_hold_h and not _winner_extended:\n"
         "            reason = 'TIME_LIMIT'\n"
         '\n'
         "        if reason:\n"
         "            result = _close_perp_position(pos_id, price, reason)\n"
         "            pnl = result.get('pnl_pct', 0) if result else 0\n"
         "            logger.info(\n"
         "                '[MID PAPER] Closed %s %s @ %.4g  reason=%s  pnl=%.2f%%  held=%.1fh',\n"
         "                side, symbol, price, reason, pnl, age_h\n"
         "            )")

assert OLD_6 in text, "OLD_6 anchor (PROFIT_LOCK handler + TIME_LIMIT + close block) not found"
text = text.replace(OLD_6, NEW_6)
print("✓ WINNER_RUN_EXTEND handler + DCA call + TIME_LIMIT guard added to mid_monitor_step")

# ─────────────────────────────────────────────────────────────────────────────
# Write + compile check
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
print("✓ Patch 80b complete")
