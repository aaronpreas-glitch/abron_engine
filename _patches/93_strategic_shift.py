#!/usr/bin/env python3
"""Patch 93 — Strategic Shift: MID/SWING Priority + Scalp Throttle.

Changes:
  1. utils/perp_executor.py
     a. Add SCALP_ML_MIN_WIN_PROB lambda (default 0.78)
     b. Per-mode ML gate: scalp uses SCALP_ML_MIN_WIN_PROB, others use ML_MIN_WIN_PROB
  2. dashboard/backend/main.py
     a. Add SCALP_ML_MIN_WIN_PROB to ALLOWED_KEYS
     b. Add /api/brain/strategy-distribution endpoint
  3. .env: SCALP_SIZE_USD=6, PERP_SIZE_USD=75, SCALP_ML_MIN_WIN_PROB=0.78
"""
from pathlib import Path
import subprocess, re

# ─────────────────────────────────────────────────────────────────────────────
# 1. utils/perp_executor.py
# ─────────────────────────────────────────────────────────────────────────────
PX = Path("/root/memecoin_engine/utils/perp_executor.py")
text = PX.read_text()

# 1a. Add SCALP_ML_MIN_WIN_PROB lambda after SCALP_SKIP_TRANSITION
OLD1 = 'SCALP_SKIP_TRANSITION = lambda: _bool("SCALP_SKIP_TRANSITION", True)'
assert text.count(OLD1) == 1, f"Step1a: found {text.count(OLD1)} matches"
NEW1 = (
    'SCALP_SKIP_TRANSITION = lambda: _bool("SCALP_SKIP_TRANSITION", True)\n'
    'SCALP_ML_MIN_WIN_PROB = lambda: _float("SCALP_ML_MIN_WIN_PROB", 0.78)'
    '  # Per-scalp ML quality gate (default: require 78% win prob)'
)
text = text.replace(OLD1, NEW1)
print("Step 1a: added SCALP_ML_MIN_WIN_PROB lambda ✓")

# 1b. Per-mode ML gate: scalp uses stricter threshold
OLD2 = '            min_wp = ML_MIN_WIN_PROB()'
assert text.count(OLD2) == 1, f"Step1b: found {text.count(OLD2)} matches"
NEW2 = '            min_wp = SCALP_ML_MIN_WIN_PROB() if is_scalp else ML_MIN_WIN_PROB()'
text = text.replace(OLD2, NEW2)
print("Step 1b: ML gate → per-mode threshold ✓")

PX.write_text(text)
r = subprocess.run(["python3", "-m", "py_compile", str(PX)], capture_output=True, text=True)
if r.returncode != 0:
    print("COMPILE ERROR:", r.stderr); raise SystemExit(1)
print("perp_executor.py compile OK ✓")

# ─────────────────────────────────────────────────────────────────────────────
# 2. dashboard/backend/main.py
# ─────────────────────────────────────────────────────────────────────────────
MAIN = Path("/root/memecoin_engine/dashboard/backend/main.py")
text = MAIN.read_text()

# 2a. Add SCALP_ML_MIN_WIN_PROB to ALLOWED_KEYS set
OLD3 = '    "TRANSITION_SL_TIGHTEN_PCT",\n    }'
assert text.count(OLD3) == 1, f"Step2a: found {text.count(OLD3)} matches"
NEW3 = '    "TRANSITION_SL_TIGHTEN_PCT",\n    "SCALP_ML_MIN_WIN_PROB",\n    }'
text = text.replace(OLD3, NEW3)
print("Step 2a: SCALP_ML_MIN_WIN_PROB added to ALLOWED_KEYS ✓")

# 2b. Add /api/brain/strategy-distribution endpoint before /api/journal/learnings
ANCHOR = '@app.get("/api/journal/learnings")'
assert text.count(ANCHOR) == 1, f"Step2b: found {text.count(ANCHOR)} matches"

NEW_ENDPOINT = '''\
@app.get("/api/brain/strategy-distribution")
async def brain_strategy_distribution(days: int = 7, _: str = Depends(get_current_user)):
    """Return closed-trade counts split by strategy mode (SCALP / MID / SWING)."""
    import sqlite3 as _sq3, re as _re3, pathlib as _pl93
    from datetime import timedelta as _td93, datetime as _dt93
    _db3 = str(_pl93.Path(__file__).resolve().parent.parent.parent / "data_storage" / "engine.db")
    cutoff = (_dt93.utcnow() - _td93(days=days)).isoformat()
    out: dict = {"SCALP": 0, "MID": 0, "SWING": 0, "total": 0, "days": days}
    try:
        with _sq3.connect(_db3) as _c3:
            rows = _c3.execute(
                "SELECT notes FROM perp_positions WHERE opened_ts_utc >= ? AND status='CLOSED'",
                (cutoff,)
            ).fetchall()
        for (notes,) in rows:
            m3 = _re3.search(r'mode=(SCALP|MID|SWING)', notes or '', _re3.IGNORECASE)
            mode = m3.group(1).upper() if m3 else "SWING"
            out[mode] = out.get(mode, 0) + 1
        out["total"] = out["SCALP"] + out["MID"] + out["SWING"]
    except Exception as _e3:
        pass
    return out


'''
text = text.replace(ANCHOR, NEW_ENDPOINT + ANCHOR)
print("Step 2b: /api/brain/strategy-distribution endpoint added ✓")

MAIN.write_text(text)
r = subprocess.run(["python3", "-m", "py_compile", str(MAIN)], capture_output=True, text=True)
if r.returncode != 0:
    print("COMPILE ERROR:", r.stderr); raise SystemExit(1)
print("main.py compile OK ✓")

# ─────────────────────────────────────────────────────────────────────────────
# 3. .env updates
# ─────────────────────────────────────────────────────────────────────────────
ENV = Path("/root/memecoin_engine/.env")
env_text = ENV.read_text()

# SCALP_SIZE_USD → 6  (was 25; now 0.25× to throttle scalp sizing)
env_new = re.sub(r'^SCALP_SIZE_USD=.*$', 'SCALP_SIZE_USD=6', env_text, flags=re.MULTILINE)
assert env_new != env_text, "Step3a: SCALP_SIZE_USD not found in .env"
print("Step 3a: SCALP_SIZE_USD=6 ✓")

# PERP_SIZE_USD → 75  (was 50; 1.5× boost for SWING)
env_new2 = re.sub(r'^PERP_SIZE_USD=.*$', 'PERP_SIZE_USD=75', env_new, flags=re.MULTILINE)
assert env_new2 != env_new, "Step3b: PERP_SIZE_USD not found in .env"
print("Step 3b: PERP_SIZE_USD=75 ✓")

# SCALP_ML_MIN_WIN_PROB
if 'SCALP_ML_MIN_WIN_PROB' not in env_new2:
    env_new2 += '\nSCALP_ML_MIN_WIN_PROB=0.78\n'
    print("Step 3c: SCALP_ML_MIN_WIN_PROB=0.78 added ✓")
else:
    env_new2 = re.sub(r'^SCALP_ML_MIN_WIN_PROB=.*$', 'SCALP_ML_MIN_WIN_PROB=0.78',
                      env_new2, flags=re.MULTILINE)
    print("Step 3c: SCALP_ML_MIN_WIN_PROB=0.78 updated ✓")

ENV.write_text(env_new2)
print("Patch 93: Strategic Shift applied successfully ✓")
