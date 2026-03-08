"""
Patch 67 — Bug Fixes
=====================
Bug 1: mid_monitor_step uses `await _fetch_price()` but _fetch_price is sync.
        Fires "object float can't be used in 'await' expression" every 60s,
        making mid-position monitoring a complete no-op.

Bug 2: scalp_scan + mid_scan both initialize _sent_score only inside the LONG
        branch, then reference it in the SHORT branch without initialization.
        Fires "local variable '_sent_score' referenced before assignment" every
        time a SHORT signal fires on any symbol.
"""

from pathlib import Path
import py_compile, tempfile, shutil

EXECUTOR = Path(__file__).resolve().parent.parent / "utils" / "perp_executor.py"
MAIN     = Path(__file__).resolve().parent.parent / "dashboard" / "backend" / "main.py"

exe  = EXECUTOR.read_text()
main = MAIN.read_text()

# ── Bug 1: remove `await` from _fetch_price call in mid_monitor_step ──────────

OLD_AWAIT_FETCH = '        price = await _fetch_price(symbol)\n'
NEW_SYNC_FETCH  = '        price = _fetch_price(symbol)\n'

count = exe.count(OLD_AWAIT_FETCH)
assert count == 1, f"Expected 1 occurrence of await _fetch_price in perp_executor, found {count}"
exe = exe.replace(OLD_AWAIT_FETCH, NEW_SYNC_FETCH)
print("✅ Bug 1 fixed: removed erroneous `await` from _fetch_price in mid_monitor_step")

EXECUTOR.write_text(exe)

tmp = Path(tempfile.mktemp(suffix=".py"))
shutil.copy(EXECUTOR, tmp)
py_compile.compile(str(tmp), doraise=True)
tmp.unlink()
print("✅ perp_executor.py compiles OK")

# ── Bug 2a: scalp_scan — initialize _sent_score before if/elif ────────────────
# The LONG branch sets _sent_score = 0.0, but the SHORT branch uses it without
# initialization. Fix: hoist the init above both branches.

OLD_SCALP_LONG = (
    '                    if chg_5m > threshold:\n'
    '                        # ── Fetch sentiment ──\n'
    '                        _sent_score = 0.0\n'
    '                        _sent_boost = 0\n'
)
NEW_SCALP_LONG = (
    '                    _sent_score = 0.0\n'
    '                    _sent_boost = 0\n'
    '                    if chg_5m > threshold:\n'
    '                        # ── Fetch sentiment ──\n'
)
assert OLD_SCALP_LONG in main, "scalp_scan LONG sent_score anchor not found"
main = main.replace(OLD_SCALP_LONG, NEW_SCALP_LONG)
print("✅ Bug 2a fixed: _sent_score hoisted before scalp if/elif")

# ── Bug 2b: mid_scan — same pattern, 15m ────────────────────────────────────

OLD_MID_LONG = (
    '                    if chg_15m > threshold:\n'
    '                        # ── Fetch sentiment ──\n'
    '                        _sent_score = 0.0\n'
    '                        _sent_boost = 0\n'
)
NEW_MID_LONG = (
    '                    _sent_score = 0.0\n'
    '                    _sent_boost = 0\n'
    '                    if chg_15m > threshold:\n'
    '                        # ── Fetch sentiment ──\n'
)
assert OLD_MID_LONG in main, "mid_scan LONG sent_score anchor not found"
main = main.replace(OLD_MID_LONG, NEW_MID_LONG)
print("✅ Bug 2b fixed: _sent_score hoisted before mid-scan if/elif")

MAIN.write_text(main)

tmp = Path(tempfile.mktemp(suffix=".py"))
shutil.copy(MAIN, tmp)
py_compile.compile(str(tmp), doraise=True)
tmp.unlink()
print("✅ main.py compiles OK")
print("\nPatch 67 complete")
