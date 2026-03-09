"""
Patch 186 — Align horizon-comparison output with live tuner state (P185)

After P185 switched the tuner to 24h optimization, the horizon_comparison
diagnostic was still reporting SWITCH_RECOMMENDED as if the switch had not
happened — an operator-truth mismatch.

Fix: read active_horizon from the already-computed `tuner` object and branch
verdict logic accordingly. No strategy or auto-buy behavior changes.

New verdict states:
  ALREADY_SWITCHED      — tuner is on 24h and missed band(s) confirm why
  CURRENTLY_OPTIMAL     — tuner is on 24h and both horizons agree
  SWITCH_RECOMMENDED    — tuner is on 4h and 24h would add viable bands (unchanged)
  ALIGNED               — tuner is on 4h and both horizons agree (unchanged)
  INSUFFICIENT_DATA     — fewer than 100 dual-outcome samples (unchanged)

Also adds 'active_horizon' to the horizon_comparison dict for frontend display.

Files changed:
  /root/memecoin_engine/dashboard/backend/routers/memecoins.py
(MemecoinsPage.tsx frontend changes handled separately)
"""
import py_compile

MR_PATH = "/root/memecoin_engine/dashboard/backend/routers/memecoins.py"

mr = open(MR_PATH).read()


# ── Single replacement: entire verdict block + horizon_comparison dict ─────────
# Replace from `_n_both = len(_h_rows)` through the end of the dict assignment.
# `tuner` is already in scope (defined ~20 lines above in the same _run() call).

OLD = (
    "                _n_both = len(_h_rows)\n"
    "                if _n_both < 100:\n"
    "                    _hv = {'label': 'INSUFFICIENT_DATA',\n"
    "                           'message': f'Only {_n_both} dual-outcome samples -- need >=100.'}\n"
    "                elif not _missed:\n"
    "                    _hv = {'label': 'ALIGNED',\n"
    "                           'message': (\n"
    "                               f'Both horizons select the same {len(_b4h)} band(s). '\n"
    "                               '4h tuner is well-calibrated for this dataset.'\n"
    "                           )}\n"
    "                else:\n"
    "                    _miss_strs = ', '.join(\n"
    "                        f\"score {b['lo']}-{b['hi']} (WR={b['wr']:.0f}%, 24h avg={b['avg_ret']:+.1f}%)\"\n"
    "                        for b in _missed\n"
    "                    )\n"
    "                    _hv = {'label': 'SWITCH_RECOMMENDED',\n"
    "                           'message': (\n"
    "                               f'24h finds {len(_b24h)} viable band(s); 4h finds {len(_b4h)}. '\n"
    "                               f'Missed by 4h: {_miss_strs}. '\n"
    "                               'These signals look like noise at 4h but resolve strongly at 24h. '\n"
    "                               'Switching tuner to 24h would include them in auto-buy.'\n"
    "                           )}\n"
    "\n"
    "                horizon_comparison = {\n"
    "                    'n_both':             _n_both,\n"
    "                    'bands_4h':           _b4h,\n"
    "                    'bands_24h':          _b24h,\n"
    "                    'bands_missed_by_4h': _missed,\n"
    "                    'verdict':            _hv,\n"
    "                }\n"
)

NEW = (
    "                _n_both    = len(_h_rows)\n"
    "                _active_hz = (tuner or {}).get('optimization_horizon', '4h')  # P186\n"
    "\n"
    "                if _n_both < 100:\n"
    "                    _hv = {'label': 'INSUFFICIENT_DATA',\n"
    "                           'message': f'Only {_n_both} dual-outcome samples -- need >=100.'}\n"
    "\n"
    "                elif _active_hz == '24h' and _missed:\n"
    "                    # Tuner is already on 24h; the missed bands were the reason for the switch.\n"
    "                    _miss_strs = ', '.join(\n"
    "                        f\"score {b['lo']}-{b['hi']} (WR={b['wr']:.0f}%, 24h avg={b['avg_ret']:+.1f}%)\"\n"
    "                        for b in _missed\n"
    "                    )\n"
    "                    _hv = {'label': 'ALREADY_SWITCHED',\n"
    "                           'message': (\n"
    "                               f'Tuner is already on 24h optimization (switched in P185). '\n"
    "                               f'The previously-missed band(s) are now included in auto-buy: '\n"
    "                               f'{_miss_strs}. '\n"
    "                               f'4h comparison is counterfactual — shown for reference only.'\n"
    "                           )}\n"
    "\n"
    "                elif _active_hz == '24h' and not _missed:\n"
    "                    # Tuner is on 24h and both horizons agree — fully optimal.\n"
    "                    _hv = {'label': 'CURRENTLY_OPTIMAL',\n"
    "                           'message': (\n"
    "                               f'Tuner is on 24h optimization. '\n"
    "                               f'Both 4h and 24h exhaustive searches select the same '\n"
    "                               f'{len(_b4h)} top band(s) — no additional signals would '\n"
    "                               f'be gained by the alternative horizon.'\n"
    "                           )}\n"
    "\n"
    "                elif not _missed:\n"
    "                    # Tuner is on 4h and both horizons agree.\n"
    "                    _hv = {'label': 'ALIGNED',\n"
    "                           'message': (\n"
    "                               f'Both horizons select the same {len(_b4h)} band(s). '\n"
    "                               '4h tuner is well-calibrated for this dataset.'\n"
    "                           )}\n"
    "\n"
    "                else:\n"
    "                    # Tuner is on 4h but 24h finds additional viable bands.\n"
    "                    _miss_strs = ', '.join(\n"
    "                        f\"score {b['lo']}-{b['hi']} (WR={b['wr']:.0f}%, 24h avg={b['avg_ret']:+.1f}%)\"\n"
    "                        for b in _missed\n"
    "                    )\n"
    "                    _hv = {'label': 'SWITCH_RECOMMENDED',\n"
    "                           'message': (\n"
    "                               f'24h finds {len(_b24h)} viable band(s); 4h finds {len(_b4h)}. '\n"
    "                               f'Missed by 4h: {_miss_strs}. '\n"
    "                               'These signals look like noise at 4h but resolve strongly at 24h. '\n"
    "                               'Switching tuner to 24h would include them in auto-buy.'\n"
    "                           )}\n"
    "\n"
    "                horizon_comparison = {\n"
    "                    'n_both':             _n_both,\n"
    "                    'bands_4h':           _b4h,\n"
    "                    'bands_24h':          _b24h,\n"
    "                    'bands_missed_by_4h': _missed,\n"
    "                    'active_horizon':     _active_hz,   # P186\n"
    "                    'verdict':            _hv,\n"
    "                }\n"
)

assert OLD in mr, "Anchor not found — check verdict block in horizon_comparison try block"
mr = mr.replace(OLD, NEW, 1)
print("Step A: horizon_comparison verdict block updated with active_horizon awareness")

with open(MR_PATH, "w") as f:
    f.write(mr)

py_compile.compile(MR_PATH, doraise=True)
print(f"memecoins.py — py_compile: OK")
print("\nPatch 186 applied successfully.")
print("  Active horizon now read from tuner.optimization_horizon (defaults to '4h' for compat).")
print("  ALREADY_SWITCHED fires when tuner is on 24h and missed bands confirm the reason.")
print("  CURRENTLY_OPTIMAL fires when tuner is on 24h and horizons agree.")
print("  SWITCH_RECOMMENDED / ALIGNED unchanged — fire only when tuner is on 4h.")
print("  active_horizon added to horizon_comparison dict for frontend display.")
