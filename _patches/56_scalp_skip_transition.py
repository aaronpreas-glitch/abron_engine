"""
Patch 56 — SCALP_SKIP_TRANSITION guard
=======================================
Adds a regime-aware filter that skips SCALP signals when the market is
in a sideways TRANSITION regime. Includes:
  - Configurable flag (SCALP_SKIP_TRANSITION=true in .env)
  - Auto-learn in _auto_tune_selectivity (>35% stop rate → auto-enable)
  - In-memory hot-apply via _apply_scalp_regime_tuning()
  - gate-stats: skipped_by_regime breakdown + transition_guard_active flag
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PE   = ROOT / "utils" / "perp_executor.py"
MAIN = ROOT / "dashboard" / "backend" / "main.py"

# ── A: perp_executor.py ─────────────────────────────────────────────────────

pe = PE.read_text()

# A1: Config lambda — after SCALP_5M_THRESHOLD
A1_ANCHOR = 'SCALP_5M_THRESHOLD  = lambda: _float("SCALP_5M_THRESHOLD", 0.15)'
assert A1_ANCHOR in pe, "A1 anchor not found"
pe = pe.replace(
    A1_ANCHOR,
    A1_ANCHOR + "\n"
    'SCALP_SKIP_TRANSITION = lambda: _bool("SCALP_SKIP_TRANSITION", True)   # skip SCALP in sideways regime',
)

# A2: Cached globals — after _account_balance_usd
A2_ANCHOR = "_account_balance_usd: float  = 0.0    # total account balance for 0.5% risk cap"
assert A2_ANCHOR in pe, "A2 anchor not found"
pe = pe.replace(
    A2_ANCHOR,
    A2_ANCHOR + "\n"
    "_scalp_skip_transition: bool   = True    # mirror of SCALP_SKIP_TRANSITION env var\n"
    "_scalp_skip_until:      object = None    # datetime — auto-suppression expiry",
)

# A3: Regime guard in _check_quality_gate — after SWING_ONLY check
A3_ANCHOR = (
    "    # 3. Mode lock (poor win-rate or negative day) — ALWAYS enforced\n"
    "    if _dynamic_mode_forced and mode_tag == \"SCALP\":\n"
    "        return _skip(\"SWING_ONLY\")"
)
assert A3_ANCHOR in pe, "A3 anchor not found"
pe = pe.replace(
    A3_ANCHOR,
    A3_ANCHOR + "\n\n"
    "    # 3b. Regime guard: skip SCALP in sideways TRANSITION market\n"
    "    if mode_tag == \"SCALP\" and regime == \"TRANSITION\" and _scalp_skip_transition:\n"
    "        return _skip(\"REGIME_TRANSITION\")",
)

# A4a: Extend global declaration in _auto_tune_selectivity
A4A_ANCHOR = "    global _ml_min_win_prob, _daily_trade_cap"
assert A4A_ANCHOR in pe, "A4a anchor not found"
pe = pe.replace(
    A4A_ANCHOR,
    "    global _ml_min_win_prob, _daily_trade_cap, _scalp_skip_transition, _scalp_skip_until",
    1,  # only first occurrence (inside _auto_tune_selectivity)
)

# A4b: Auto-learn TRANSITION stop rate — before "if not adjustments:"
A4B_ANCHOR = "        if not adjustments:\n            adjustments.append(\n                f\"No changes needed"
assert A4B_ANCHOR in pe, "A4b anchor not found"
AUTO_LEARN = (
    "        # Auto-learn: check TRANSITION stop rate across last 20 SCALP trades\n"
    "        try:\n"
    "            # Check expiry of auto-set suppression\n"
    "            if _scalp_skip_until is not None and datetime.now(timezone.utc) >= _scalp_skip_until:\n"
    "                _scalp_skip_transition = bool(SCALP_SKIP_TRANSITION())\n"
    "                _scalp_skip_until = None\n"
    "                logger.info('[SELECTIVITY-TUNE] SCALP_SKIP_TRANSITION auto-expiry → reset to env default=%s', _scalp_skip_transition)\n"
    "\n"
    "            trans_rows = c.execute(\"\"\"\n"
    "                SELECT exit_reason FROM perp_positions\n"
    "                WHERE status='CLOSED'\n"
    "                  AND notes LIKE '%mode=SCALP%'\n"
    "                  AND notes LIKE '%regime=TRANSITION%'\n"
    "                  AND closed_ts_utc > ?\n"
    "                ORDER BY closed_ts_utc DESC\n"
    "                LIMIT 20\n"
    "            \"\"\", (cutoff_7d,)).fetchall()\n"
    "            if len(trans_rows) >= 20:\n"
    "                stop_rate = sum(1 for r in trans_rows if r[0] == 'STOP_LOSS') / len(trans_rows)\n"
    "                if stop_rate > 0.35 and not _scalp_skip_transition:\n"
    "                    _scalp_skip_transition = True\n"
    "                    _scalp_skip_until = datetime.now(timezone.utc) + timedelta(hours=24)\n"
    "                    adjustments.append(\n"
    "                        f'TRANSITION stop_rate={stop_rate:.1%} >35% → SCALP_SKIP_TRANSITION=True for 24h'\n"
    "                    )\n"
    "                    logger.info('[SELECTIVITY-TUNE] TRANSITION stop_rate=%.1f%% → auto SCALP_SKIP_TRANSITION=True', stop_rate * 100)\n"
    "                elif stop_rate < 0.20 and _scalp_skip_transition and _scalp_skip_until is not None:\n"
    "                    _scalp_skip_transition = False\n"
    "                    _scalp_skip_until = None\n"
    "                    adjustments.append(\n"
    "                        f'TRANSITION stop_rate={stop_rate:.1%} <20% → SCALP_SKIP_TRANSITION=False (re-enabled)'\n"
    "                    )\n"
    "                    logger.info('[SELECTIVITY-TUNE] TRANSITION stop_rate=%.1f%% → auto SCALP_SKIP_TRANSITION=False', stop_rate * 100)\n"
    "        except Exception as _te:\n"
    "            logger.debug('auto-learn SCALP_SKIP_TRANSITION error: %s', _te)\n"
    "\n"
)
pe = pe.replace(A4B_ANCHOR, AUTO_LEARN + A4B_ANCHOR)

# A5: Add _apply_scalp_regime_tuning — after _apply_live_mode_tuning
A5_ANCHOR = "\ndef _check_dynamic_exit_circuit_breaker():"
assert A5_ANCHOR in pe, "A5 anchor not found"
pe = pe.replace(
    A5_ANCHOR,
    "\n\ndef _apply_scalp_regime_tuning(key: str, value: str) -> bool:\n"
    "    \"\"\"Update SCALP_SKIP_TRANSITION in-memory without restart.\"\"\"\n"
    "    global _scalp_skip_transition, _scalp_skip_until\n"
    "    try:\n"
    "        if key == \"SCALP_SKIP_TRANSITION\":\n"
    "            _scalp_skip_transition = value.lower() in (\"1\", \"true\", \"yes\")\n"
    "            _scalp_skip_until = None  # manual override clears auto-expiry\n"
    "            logger.info(\"[LIVE-TUNE] SCALP_SKIP_TRANSITION → %s\", _scalp_skip_transition)\n"
    "            return True\n"
    "        return False\n"
    "    except Exception as _e:\n"
    "        logger.debug(\"_apply_scalp_regime_tuning(%s, %s) error: %s\", key, value, _e)\n"
    "        return False\n"
    + A5_ANCHOR,
)

PE.write_text(pe)
print("✅ perp_executor.py patched (A1–A5)")

# Verify
import py_compile, tempfile, shutil
tmp = Path(tempfile.mktemp(suffix=".py"))
shutil.copy(PE, tmp)
py_compile.compile(str(tmp), doraise=True)
tmp.unlink()
print("✅ perp_executor.py compiles OK")


# ── B: main.py ──────────────────────────────────────────────────────────────

main = MAIN.read_text()

# B1: ALLOWED_KEYS — add SCALP_SKIP_TRANSITION
B1_ANCHOR = '"VOL_FILTER_THRESHOLD", "VOL_SIZE_MULT",'
assert B1_ANCHOR in main, "B1 anchor not found"
main = main.replace(
    B1_ANCHOR,
    '"SCALP_SKIP_TRANSITION",\n'
    '        "VOL_FILTER_THRESHOLD", "VOL_SIZE_MULT",',
)

# B2: apply-tuner dispatch — after _apply_live_mode_tuning dispatch block
B2_ANCHOR = (
    "        if _pe_mod and hasattr(_pe_mod, '_apply_live_mode_tuning'):\n"
    "            for key, val in applied.items():\n"
    "                _pe_mod._apply_live_mode_tuning(key, val)"
)
assert B2_ANCHOR in main, "B2 anchor not found"
main = main.replace(
    B2_ANCHOR,
    B2_ANCHOR + "\n"
    "        if _pe_mod and hasattr(_pe_mod, '_apply_scalp_regime_tuning'):\n"
    "            for key, val in applied.items():\n"
    "                _pe_mod._apply_scalp_regime_tuning(key, val)",
)

# B3: gate-stats — add skipped_by_regime + transition_guard_active before return
B3_ANCHOR = (
    "    except Exception as _e:\n"
    "        log.warning(\"brain_gate_stats error: %s\", _e)\n"
    "\n"
    "    return JSONResponse(result)"
)
assert B3_ANCHOR in main, "B3 anchor not found"
REGIME_BLOCK = (
    "        # Regime-based skip breakdown\n"
    "        try:\n"
    "            rows = c.execute(\"\"\"\n"
    "                SELECT regime, COUNT(*) AS cnt\n"
    "                FROM skipped_signals_log\n"
    "                WHERE ts_utc >= ?\n"
    "                GROUP BY regime\n"
    "                ORDER BY cnt DESC\n"
    "            \"\"\", (since_24h,)).fetchall()\n"
    "            result[\"skipped_by_regime\"] = [\n"
    "                {\"regime\": r[\"regime\"], \"count\": int(r[\"cnt\"])} for r in rows\n"
    "            ]\n"
    "        except Exception:\n"
    "            result[\"skipped_by_regime\"] = []\n"
    "\n"
    "        # SCALP_SKIP_TRANSITION live state\n"
    "        try:\n"
    "            import sys as _sys_gs\n"
    "            _pe_gs = (_sys_gs.modules.get('utils.perp_executor')\n"
    "                      or _sys_gs.modules.get('memecoin_engine.utils.perp_executor'))\n"
    "            result[\"transition_guard_active\"] = bool(getattr(_pe_gs, '_scalp_skip_transition', False)) if _pe_gs else False\n"
    "            result[\"scalp_skip_until\"] = (\n"
    "                getattr(_pe_gs, '_scalp_skip_until', None).isoformat()\n"
    "                if _pe_gs and getattr(_pe_gs, '_scalp_skip_until', None) else None\n"
    "            )\n"
    "        except Exception:\n"
    "            result[\"transition_guard_active\"] = False\n"
    "            result[\"scalp_skip_until\"] = None\n"
    "\n"
)
# Insert the regime block inside the try: block before the except
# The gate-stats try block ends just before the except
B3_INSERT_ANCHOR = (
    "            # Compute derived metrics\n"
    "            total = result[\"skipped\"] + result[\"accepted\"] + result[\"blocked_by_risk\"]\n"
    "            result[\"signals_seen\"] = total\n"
    "            if total > 0:\n"
    "                result[\"accepted_rate\"] = round(result[\"accepted\"] / total * 100, 2)\n"
)
assert B3_INSERT_ANCHOR in main, "B3 insert anchor not found"
main = main.replace(
    B3_INSERT_ANCHOR,
    B3_INSERT_ANCHOR + "\n" + REGIME_BLOCK,
)

MAIN.write_text(main)
print("✅ main.py patched (B1–B3)")

tmp2 = Path(tempfile.mktemp(suffix=".py"))
shutil.copy(MAIN, tmp2)
py_compile.compile(str(tmp2), doraise=True)
tmp2.unlink()
print("✅ main.py compiles OK")
print("\nPatch 56 complete — deploy with scp + python3 + rsync")
