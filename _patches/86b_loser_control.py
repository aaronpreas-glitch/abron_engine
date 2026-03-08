#!/usr/bin/env python3
"""Patch 86b — ML Early Exit + TRANSITION SL Tighten

perp_executor.py:
  PE-R1: Config lambdas (ML_EARLY_EXIT_*, TRANSITION_SL_TIGHTEN_PCT)
  PE-R2: Loser control block inserted in mid_monitor_step before standard SL/TP

main.py:
  MAIN-R1: ALLOWED_KEYS extended

.env:
  ML_EARLY_EXIT_ENABLE=true, ML_EARLY_EXIT_PNL=-1.0, ML_EARLY_EXIT_WP=0.50
  TRANSITION_SL_TIGHTEN_PCT=15.0
"""
from pathlib import Path
import subprocess

PE   = Path("/root/memecoin_engine/utils/perp_executor.py")
MAIN = Path("/root/memecoin_engine/dashboard/backend/main.py")
ENV  = Path("/root/memecoin_engine/.env")

pe_text   = PE.read_text()
main_text = MAIN.read_text()

# ─────────────────────────────────────────────────────────────────────────────
# PE-R1: Config lambdas — insert after WINNER_EXTEND_TRAIL_PCT, before Reverse Scaling block
# ─────────────────────────────────────────────────────────────────────────────
PE_R1_ANCHOR = ('WINNER_EXTEND_TRAIL_PCT = lambda: _float("WINNER_EXTEND_TRAIL_PCT", 0.3)\n'
                '\n'
                '# ── Reverse Scaling config (Patch 85)')
assert pe_text.count(PE_R1_ANCHOR) == 1, f"PE-R1: expected 1 anchor, found {pe_text.count(PE_R1_ANCHOR)}"

PE_R1_NEW = (
    'WINNER_EXTEND_TRAIL_PCT = lambda: _float("WINNER_EXTEND_TRAIL_PCT", 0.3)\n'
    '\n'
    '# ── Loser Control config (Patch 86b) ─────────────────────────────────────────\n'
    'ML_EARLY_EXIT_ENABLE      = lambda: os.getenv("ML_EARLY_EXIT_ENABLE", "true").lower() == "true"\n'
    'ML_EARLY_EXIT_PNL         = lambda: _float("ML_EARLY_EXIT_PNL", -1.0)   # leveraged PnL% must be below this\n'
    'ML_EARLY_EXIT_WP          = lambda: _float("ML_EARLY_EXIT_WP", 0.50)    # ml_wp must be below this\n'
    'TRANSITION_SL_TIGHTEN_PCT = lambda: _float("TRANSITION_SL_TIGHTEN_PCT", 15.0)  # % to tighten SL in TRANSITION\n'
    '\n'
    '# ── Reverse Scaling config (Patch 85)'
)

pe_text = pe_text.replace(PE_R1_ANCHOR, PE_R1_NEW)
assert pe_text.count(PE_R1_NEW) == 1, "PE-R1 replacement produced multiple matches"

# ─────────────────────────────────────────────────────────────────────────────
# PE-R2: Loser control block in mid_monitor_step
#        Anchor: the standard SL/TP block with tp_price (unique to mid_monitor_step)
#        Insert BEFORE this block so ML exit + TRANSITION SL fire first.
# ─────────────────────────────────────────────────────────────────────────────
PE_R2_ANCHOR = (
    "        if reason is None:\n"
    "            if side == 'LONG':\n"
    "                if price >= tp_price:   reason = 'TP1'\n"
    "                elif price <= sl_price: reason = 'STOP_LOSS'\n"
    "            else:\n"
    "                if price <= tp_price:   reason = 'TP1'\n"
    "                elif price >= sl_price: reason = 'STOP_LOSS'"
)
assert pe_text.count(PE_R2_ANCHOR) == 1, f"PE-R2: expected 1 anchor, found {pe_text.count(PE_R2_ANCHOR)}"

PE_R2_BLOCK = (
    "        # ── Patch 86b: ML Early Exit + TRANSITION SL tighten ──────────────────────\n"
    "        if reason is None:\n"
    "            # Extract ml_wp from notes (e.g. 'ml_wp=0.62|...')\n"
    "            _me_ml_wp = None\n"
    "            for _mep in (pos.get('notes') or '').split('|'):\n"
    "                if _mep.startswith('ml_wp='):\n"
    "                    try: _me_ml_wp = float(_mep.split('=', 1)[1])\n"
    "                    except Exception: pass\n"
    "\n"
    "            # 1. ML Early Exit: bad PnL + confirmed low win probability → cut now\n"
    "            if (ML_EARLY_EXIT_ENABLE()\n"
    "                    and _mid_pnl < ML_EARLY_EXIT_PNL()\n"
    "                    and _me_ml_wp is not None\n"
    "                    and _me_ml_wp < ML_EARLY_EXIT_WP()):\n"
    "                reason = 'ML_EARLY_EXIT'\n"
    "                _log_dynamic_exit(\n"
    "                    pos_id, 'ML_EARLY_EXIT', _me_ml_wp, '', _mid_pnl, age_h,\n"
    "                    f'PnL={_mid_pnl:.2f}% ml_wp={_me_ml_wp:.2f} < thresholds'\n"
    "                )\n"
    "\n"
    "            # 2. TRANSITION SL tighten: tighten effective SL by TRANSITION_SL_TIGHTEN_PCT\n"
    "            if reason is None and TRANSITION_SL_TIGHTEN_PCT() > 0:\n"
    "                _r_lbl = pos.get('regime_label') or ''\n"
    "                if 'TRANSITION' in _r_lbl:\n"
    "                    _sl_dist = abs(entry - sl_price)\n"
    "                    _eff_sl = (\n"
    "                        entry - _sl_dist * (1 - TRANSITION_SL_TIGHTEN_PCT() / 100)\n"
    "                        if side == 'LONG'\n"
    "                        else entry + _sl_dist * (1 - TRANSITION_SL_TIGHTEN_PCT() / 100)\n"
    "                    )\n"
    "                    _hit = (side == 'LONG' and price <= _eff_sl) or (side == 'SHORT' and price >= _eff_sl)\n"
    "                    if _hit:\n"
    "                        reason = 'STOP_LOSS'\n"
    "                        _log_dynamic_exit(\n"
    "                            pos_id, 'TRANSITION_SL', _me_ml_wp or 0, '', _mid_pnl, age_h,\n"
    "                            f'TRANSITION SL tightened {TRANSITION_SL_TIGHTEN_PCT():.0f}%: eff_sl={_eff_sl:.4g}'\n"
    "                        )\n"
    "\n"
)

pe_text = pe_text.replace(PE_R2_ANCHOR, PE_R2_BLOCK + PE_R2_ANCHOR)
PE.write_text(pe_text)
print("86b PE: lambdas + mid_monitor loser control block written ✓")

# ─────────────────────────────────────────────────────────────────────────────
# Compile PE
# ─────────────────────────────────────────────────────────────────────────────
r = subprocess.run(["python3", "-m", "py_compile", str(PE)], capture_output=True, text=True)
if r.returncode != 0:
    print("PE COMPILE ERROR:", r.stderr)
    raise SystemExit(1)
print("86b PE compile OK ✓")

# ─────────────────────────────────────────────────────────────────────────────
# MAIN-R1: Extend ALLOWED_KEYS with new tuner keys
# ─────────────────────────────────────────────────────────────────────────────
assert main_text.count('"MOONSHOT_NO_TIME_LIMIT"') == 1, \
    f"MAIN-R1: MOONSHOT_NO_TIME_LIMIT anchor count={main_text.count('MOONSHOT_NO_TIME_LIMIT')}"

main_text = main_text.replace(
    '"MOONSHOT_NO_TIME_LIMIT"',
    '"MOONSHOT_NO_TIME_LIMIT",\n'
    '    "ML_EARLY_EXIT_ENABLE", "ML_EARLY_EXIT_PNL", "ML_EARLY_EXIT_WP",\n'
    '    "TRANSITION_SL_TIGHTEN_PCT"'
)

MAIN.write_text(main_text)
print("86b MAIN: ALLOWED_KEYS extended ✓")

r = subprocess.run(["python3", "-m", "py_compile", str(MAIN)], capture_output=True, text=True)
if r.returncode != 0:
    print("MAIN COMPILE ERROR:", r.stderr)
    raise SystemExit(1)
print("86b MAIN compile OK ✓")

# ─────────────────────────────────────────────────────────────────────────────
# .env — append new defaults if missing
# ─────────────────────────────────────────────────────────────────────────────
env_text = ENV.read_text()
new_keys = [
    ("ML_EARLY_EXIT_ENABLE",      "true"),
    ("ML_EARLY_EXIT_PNL",         "-1.0"),
    ("ML_EARLY_EXIT_WP",          "0.50"),
    ("TRANSITION_SL_TIGHTEN_PCT", "15.0"),
]
additions = [f"{k}={v}" for k, v in new_keys if k not in env_text]
if additions:
    ENV.write_text(env_text.rstrip() + "\n" + "\n".join(additions) + "\n")
    print(f"86b .env: added {additions} ✓")
else:
    print("86b .env: all keys already present ✓")

print("\n86b patch complete — restart service to activate")
