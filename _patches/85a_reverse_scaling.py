"""
Patch 85a — perp_executor.py + main.py: Reverse Scaling + MOONSHOT_NO_TIME_LIMIT

Changes:
1. RS config lambdas: RS_TRIGGER_PCT1/2, RS_SELL_PCT1/2, RS_ML_WP_MIN, MOONSHOT_NO_TIME_LIMIT
2. Schema: rs_triggered TEXT column on perp_positions
3. scalp_monitor_step: RS1 (+5% → sell 40%, trail→entry) + RS2 (+20% → sell 30% more)
4. mid_monitor_step: same RS logic
5. moonshot TIME_LIMIT guard: MOONSHOT_NO_TIME_LIMIT() flag (default true = current behaviour)
6. ALLOWED_KEYS: add RS_* + MOONSHOT_NO_TIME_LIMIT for apply-tuner
7. .env: RS defaults
8. _generate_daily_report: return recommendation_changes dict for Apply All button
"""
import subprocess, sys
from pathlib import Path

ROOT = Path("/root/memecoin_engine")
PE   = ROOT / "utils" / "perp_executor.py"
MAIN = ROOT / "dashboard" / "backend" / "main.py"

pe_text   = PE.read_text()
main_text = MAIN.read_text()

# ─────────────────────────────────────────────────────────────────────────────
# PE R1 — Config lambdas: RS_* + MOONSHOT_NO_TIME_LIMIT
# ─────────────────────────────────────────────────────────────────────────────
ANCHOR_PE_R1 = 'WINNER_EXTEND_TRAIL_PCT = lambda: _float("WINNER_EXTEND_TRAIL_PCT", 0.3)'
assert ANCHOR_PE_R1 in pe_text, f"PE R1 anchor not found: {ANCHOR_PE_R1!r}"

pe_text = pe_text.replace(ANCHOR_PE_R1, ANCHOR_PE_R1 + """

# ── Reverse Scaling config (Patch 85) ─────────────────────────────────────────
RS_TRIGGER_PCT1        = lambda: _float("RS_TRIGGER_PCT1", 5.0)     # PnL% that triggers first partial sell
RS_TRIGGER_PCT2        = lambda: _float("RS_TRIGGER_PCT2", 20.0)    # PnL% that triggers second partial sell
RS_SELL_PCT1           = lambda: _float("RS_SELL_PCT1", 40.0)       # % of position to sell at trigger 1
RS_SELL_PCT2           = lambda: _float("RS_SELL_PCT2", 30.0)       # % of position to sell at trigger 2
RS_ML_WP_MIN           = lambda: _float("RS_ML_WP_MIN", 0.70)       # ML gate (skipped if ml_wp unavailable)
MOONSHOT_NO_TIME_LIMIT = lambda: os.getenv("MOONSHOT_NO_TIME_LIMIT", "true").lower() == "true"
""")
print("✓ PE R1: RS config + MOONSHOT_NO_TIME_LIMIT lambdas added")

# ─────────────────────────────────────────────────────────────────────────────
# PE R2 — Schema: rs_triggered column
# ─────────────────────────────────────────────────────────────────────────────
ANCHOR_PE_R2 = '''        # Add trail_stop_price column (Patch 82)
        try:
            c.execute("ALTER TABLE perp_positions ADD COLUMN trail_stop_price REAL")
        except Exception:
            pass  # already exists'''
assert ANCHOR_PE_R2 in pe_text, "PE R2 anchor not found"

pe_text = pe_text.replace(ANCHOR_PE_R2, ANCHOR_PE_R2 + """
        # Add rs_triggered column (Patch 85)
        try:
            c.execute("ALTER TABLE perp_positions ADD COLUMN rs_triggered TEXT")
        except Exception:
            pass  # already exists""")
print("✓ PE R2: rs_triggered schema migration added")

# ─────────────────────────────────────────────────────────────────────────────
# PE R3 — scalp_monitor_step: Reverse Scaling block
#         Insert BEFORE the standard SL/TP check (anchor is unique in file)
# ─────────────────────────────────────────────────────────────────────────────
ANCHOR_PE_R3 = "        # ── 3. Standard SL/TP checks ────────────────────────────────────"
assert ANCHOR_PE_R3 in pe_text, "PE R3 anchor not found"

RS_SCALP_BLOCK = """        # ── RS.1/RS.2 Reverse Scaling (Patch 85) ────────────────────────────
        if exit_reason is None:
            import json as _rsjson
            _lev_s = float(pos.get("leverage") or 3.0)
            _scalp_pnl = (((price - entry) / entry * 100) if side == "LONG"
                          else ((entry - price) / entry * 100)) * _lev_s
            _rs_raw = pos.get("rs_triggered") or "{}"
            try:
                _rs_data = _rsjson.loads(_rs_raw)
            except Exception:
                _rs_data = {}
            # ML gate: pass if ml_wp unavailable (N/A) or above RS_ML_WP_MIN
            _rs_ml_ok = True
            for _rp in (pos.get("notes") or "").split("|"):
                if _rp.startswith("ml_wp="):
                    try:
                        _rs_ml_ok = float(_rp.split("=", 1)[1]) >= RS_ML_WP_MIN()
                    except Exception:
                        pass  # N/A or non-numeric → gate passes
            if _scalp_pnl >= RS_TRIGGER_PCT2() and _rs_data.get("rs1") and not _rs_data.get("rs2") and _rs_ml_ok:
                # Second partial: +20%+ → sell RS_SELL_PCT2 more, trail stays
                _execute_partial_close(pos_id, RS_SELL_PCT2() / 100.0, price, "REVERSE_SCALING_2")
                _rs_data["rs2"] = {"ts": _now_iso(), "pnl_pct": round(_scalp_pnl, 2), "price": price}
                try:
                    with _conn() as _rsc:
                        _rsc.execute("UPDATE perp_positions SET rs_triggered=? WHERE id=?",
                                     (_rsjson.dumps(_rs_data), pos_id))
                        _rsc.commit()
                except Exception:
                    pass
                _log_dynamic_exit(pos_id, "REVERSE_SCALING_2", 0, "", _scalp_pnl, age_h,
                                  f"+{RS_TRIGGER_PCT2():.0f}%: sold {RS_SELL_PCT2():.0f}% more")
                logger.info("[RS2] %s %s +%.2f%% — sold %.0f%%, 42%% of position remains",
                            symbol, side, _scalp_pnl, RS_SELL_PCT2())
            elif _scalp_pnl >= RS_TRIGGER_PCT1() and not _rs_data.get("rs1") and _rs_ml_ok:
                # First partial: +5%+ → sell RS_SELL_PCT1, trail remainder to entry (breakeven floor)
                _execute_partial_close(pos_id, RS_SELL_PCT1() / 100.0, price, "REVERSE_SCALING_1")
                _rs_data["rs1"] = {"ts": _now_iso(), "pnl_pct": round(_scalp_pnl, 2), "price": price}
                try:
                    with _conn() as _rsc:
                        _rsc.execute(
                            "UPDATE perp_positions SET rs_triggered=?, trail_stop_price=? WHERE id=?",
                            (_rsjson.dumps(_rs_data), entry, pos_id)
                        )
                        _rsc.commit()
                    _tsp = entry
                except Exception:
                    pass
                _log_dynamic_exit(pos_id, "REVERSE_SCALING_1", 0, "", _scalp_pnl, age_h,
                                  f"+{RS_TRIGGER_PCT1():.0f}%: sold {RS_SELL_PCT1():.0f}%, trail→entry")
                logger.info("[RS1] %s %s +%.2f%% — sold %.0f%%, trail→entry $%.4f",
                            symbol, side, _scalp_pnl, RS_SELL_PCT1(), entry)

"""
pe_text = pe_text.replace(ANCHOR_PE_R3, RS_SCALP_BLOCK + ANCHOR_PE_R3)
print("✓ PE R3: scalp_monitor_step reverse scaling added")

# ─────────────────────────────────────────────────────────────────────────────
# PE R4 — mid_monitor_step: Reverse Scaling block
#         Insert BEFORE the mid SL/TP check (tp_price/sl_price is unique to mid step)
# ─────────────────────────────────────────────────────────────────────────────
ANCHOR_PE_R4 = """        if reason is None:
            if side == 'LONG':
                if price >= tp_price:   reason = 'TP1'
                elif price <= sl_price: reason = 'STOP_LOSS'
            else:
                if price <= tp_price:   reason = 'TP1'
                elif price >= sl_price: reason = 'STOP_LOSS'

        # Hard safety rail: never exceed 2.5x max hold time"""
assert ANCHOR_PE_R4 in pe_text, "PE R4 anchor not found"

RS_MID_BLOCK = """        # ── RS.1/RS.2 Reverse Scaling — MID (Patch 85) ──────────────────────────
        if reason is None:
            import json as _rsmjson
            _lev_m = float(pos.get("leverage") or 2.0)
            _mid_pnl = (((price - entry) / entry * 100) if side == "LONG"
                        else ((entry - price) / entry * 100)) * _lev_m
            _rsm_raw = pos.get("rs_triggered") or "{}"
            try:
                _rsm_data = _rsmjson.loads(_rsm_raw)
            except Exception:
                _rsm_data = {}
            _rsm_ml_ok = True
            for _rmp in (pos.get("notes") or "").split("|"):
                if _rmp.startswith("ml_wp="):
                    try:
                        _rsm_ml_ok = float(_rmp.split("=", 1)[1]) >= RS_ML_WP_MIN()
                    except Exception:
                        pass
            if _mid_pnl >= RS_TRIGGER_PCT2() and _rsm_data.get("rs1") and not _rsm_data.get("rs2") and _rsm_ml_ok:
                _execute_partial_close(pos_id, RS_SELL_PCT2() / 100.0, price, "REVERSE_SCALING_2")
                _rsm_data["rs2"] = {"ts": _now_iso(), "pnl_pct": round(_mid_pnl, 2), "price": price}
                try:
                    with _conn() as _rsmc:
                        _rsmc.execute("UPDATE perp_positions SET rs_triggered=? WHERE id=?",
                                      (_rsmjson.dumps(_rsm_data), pos_id))
                        _rsmc.commit()
                except Exception:
                    pass
                _log_dynamic_exit(pos_id, "REVERSE_SCALING_2", 0, "", _mid_pnl, age_h,
                                  f"MID +{RS_TRIGGER_PCT2():.0f}%: sold {RS_SELL_PCT2():.0f}% more")
                logger.info("[RS2 MID] %s %s +%.2f%% — sold %.0f%%", symbol, side, _mid_pnl, RS_SELL_PCT2())
            elif _mid_pnl >= RS_TRIGGER_PCT1() and not _rsm_data.get("rs1") and _rsm_ml_ok:
                _execute_partial_close(pos_id, RS_SELL_PCT1() / 100.0, price, "REVERSE_SCALING_1")
                _rsm_data["rs1"] = {"ts": _now_iso(), "pnl_pct": round(_mid_pnl, 2), "price": price}
                try:
                    with _conn() as _rsmc:
                        _rsmc.execute(
                            "UPDATE perp_positions SET rs_triggered=?, trail_stop_price=? WHERE id=?",
                            (_rsmjson.dumps(_rsm_data), entry, pos_id)
                        )
                        _rsmc.commit()
                    _tsp = entry
                except Exception:
                    pass
                _log_dynamic_exit(pos_id, "REVERSE_SCALING_1", 0, "", _mid_pnl, age_h,
                                  f"MID +{RS_TRIGGER_PCT1():.0f}%: sold {RS_SELL_PCT1():.0f}%, trail→entry")
                logger.info("[RS1 MID] %s %s +%.2f%% — sold %.0f%%, trail→entry $%.4f",
                            symbol, side, _mid_pnl, RS_SELL_PCT1(), entry)

"""
pe_text = pe_text.replace(ANCHOR_PE_R4, RS_MID_BLOCK + ANCHOR_PE_R4)
print("✓ PE R4: mid_monitor_step reverse scaling added")

# ─────────────────────────────────────────────────────────────────────────────
# PE R5 — MOONSHOT_NO_TIME_LIMIT: replace hardcoded guard with configurable flag
#         Appears 3 times (perp/scalp/mid monitor steps) — replace_all via str.replace
# ─────────────────────────────────────────────────────────────────────────────
OLD_MOONSHOT_GUARD = '            if "MOONSHOT" not in (pos.get("notes") or ""):'
NEW_MOONSHOT_GUARD = '            if not ("MOONSHOT=1" in (pos.get("notes") or "") and MOONSHOT_NO_TIME_LIMIT()):'
count_r5 = pe_text.count(OLD_MOONSHOT_GUARD)
assert count_r5 >= 2, f"PE R5: expected ≥2 occurrences, found {count_r5}"
pe_text = pe_text.replace(OLD_MOONSHOT_GUARD, NEW_MOONSHOT_GUARD)
print(f"✓ PE R5: MOONSHOT_NO_TIME_LIMIT guard applied to {count_r5} monitor step(s)")

# ─────────────────────────────────────────────────────────────────────────────
# Write + compile perp_executor.py
# ─────────────────────────────────────────────────────────────────────────────
PE.write_text(pe_text)
r = subprocess.run([sys.executable, "-m", "py_compile", str(PE)],
                   capture_output=True, text=True)
if r.returncode != 0:
    print("✗ PE compile error:", r.stderr)
    sys.exit(1)
print("✓ perp_executor.py compiles OK")

# ─────────────────────────────────────────────────────────────────────────────
# MAIN R6 — ALLOWED_KEYS: add RS_* + MOONSHOT_NO_TIME_LIMIT
# ─────────────────────────────────────────────────────────────────────────────
ANCHOR_MAIN_R6 = '''        # Edge Acceleration keys (Patch 82)
        "SCALP_BREAKEVEN_TRIGGER", "EARLY_CUT_MINUTES",
        "EARLY_CUT_LOSS_PCT", "WINNER_EXTEND_TRAIL_PCT",
    }'''
assert ANCHOR_MAIN_R6 in main_text, "MAIN R6 anchor not found"

main_text = main_text.replace(ANCHOR_MAIN_R6, '''        # Edge Acceleration keys (Patch 82)
        "SCALP_BREAKEVEN_TRIGGER", "EARLY_CUT_MINUTES",
        "EARLY_CUT_LOSS_PCT", "WINNER_EXTEND_TRAIL_PCT",
        # Reverse Scaling keys (Patch 85)
        "RS_TRIGGER_PCT1", "RS_TRIGGER_PCT2",
        "RS_SELL_PCT1", "RS_SELL_PCT2",
        "RS_ML_WP_MIN", "MOONSHOT_NO_TIME_LIMIT",
    }''')
print("✓ MAIN R6: RS_* + MOONSHOT_NO_TIME_LIMIT added to ALLOWED_KEYS")

# ─────────────────────────────────────────────────────────────────────────────
# MAIN R7 — _generate_daily_report: add recommendation_changes for Apply All
# ─────────────────────────────────────────────────────────────────────────────
ANCHOR_MAIN_R7 = ('     return {"date": date_str, "metrics": metrics, "patterns": patterns,\n'
                  '             "recommendations": recs, "generated_at": now_str}')
assert ANCHOR_MAIN_R7 in main_text, "MAIN R7 anchor not found"

main_text = main_text.replace(ANCHOR_MAIN_R7, '''     # Build structured changes dict for Apply All button (Patch 85)
     import re as _re_ac
     _apply_changes = {}
     for _rec in recs:
         for _akey in ["SCALP_5M_THRESHOLD", "SCALP_STOP_PCT", "WINNER_EXTEND_TRAIL_PCT",
                       "SCALP_BREAKEVEN_TRIGGER", "EARLY_CUT_LOSS_PCT", "ML_MIN_WIN_PROB",
                       "RS_TRIGGER_PCT1", "RS_TRIGGER_PCT2", "RS_SELL_PCT1"]:
             if _akey in _rec:
                 _am = _re_ac.search(r'to (\d+\.?\d+)', _rec)
                 if _am:
                     _apply_changes[_akey] = _am.group(1)

     return {"date": date_str, "metrics": metrics, "patterns": patterns,
             "recommendations": recs,
             "recommendation_changes": _apply_changes,
             "generated_at": now_str}''')
print("✓ MAIN R7: recommendation_changes added to daily report return")

# ─────────────────────────────────────────────────────────────────────────────
# Write + compile main.py
# ─────────────────────────────────────────────────────────────────────────────
MAIN.write_text(main_text)
r = subprocess.run([sys.executable, "-m", "py_compile", str(MAIN)],
                   capture_output=True, text=True)
if r.returncode != 0:
    print("✗ MAIN compile error:", r.stderr)
    sys.exit(1)
print("✓ main.py compiles OK")

# ─────────────────────────────────────────────────────────────────────────────
# .env — add RS defaults + MOONSHOT_NO_TIME_LIMIT
# ─────────────────────────────────────────────────────────────────────────────
env_path = ROOT / ".env"
env_text = env_path.read_text()

new_env_lines = []
if "RS_TRIGGER_PCT1" not in env_text:
    new_env_lines.append("RS_TRIGGER_PCT1=5.0")
if "RS_TRIGGER_PCT2" not in env_text:
    new_env_lines.append("RS_TRIGGER_PCT2=20.0")
if "RS_SELL_PCT1" not in env_text:
    new_env_lines.append("RS_SELL_PCT1=40.0")
if "RS_SELL_PCT2" not in env_text:
    new_env_lines.append("RS_SELL_PCT2=30.0")
if "RS_ML_WP_MIN" not in env_text:
    new_env_lines.append("RS_ML_WP_MIN=0.70")
if "MOONSHOT_NO_TIME_LIMIT" not in env_text:
    new_env_lines.append("MOONSHOT_NO_TIME_LIMIT=true")

if new_env_lines:
    env_path.write_text(env_text.rstrip() + "\n" + "\n".join(new_env_lines) + "\n")
    print(f"✓ .env: added {len(new_env_lines)} RS keys")
else:
    print("✓ .env: RS keys already present")

print("\n✓ Patch 85a complete — Reverse Scaling + MOONSHOT_NO_TIME_LIMIT deployed")
print("  RS1: +5% PnL → sell 40%, trail→entry | RS2: +20% PnL → sell 30% more")
print("  MOONSHOT_NO_TIME_LIMIT=true (moonshoots run indefinitely on trail only)")
print("  Apply All button: recommendation_changes dict now in daily report API response")
