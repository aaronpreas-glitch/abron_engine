"""Patch 114 — Tier Manager integration into main.py

Changes:
1. Hook tier_monitor_step into _perp_monitor_loop (runs every 60s alongside perp_monitor_step)
2. Add GET  /api/tiers/status   — tier positions + profit buffer
3. Add POST /api/tiers/open-all — open 3x/5x/10x simultaneously
4. Add POST /api/tiers/open/{tier_label} — open a single tier
"""
import sys, os, re
sys.path.insert(0, '/root/memecoin_engine')
os.chdir('/root/memecoin_engine')

TARGET = 'dashboard/backend/main.py'

with open(TARGET, 'r') as f:
    src = f.read()

# ── 1. Hook tier_monitor_step into _perp_monitor_loop ────────────────────────

OLD_LOOP = '''            await perp_monitor_step()
            _orch_hb("trading")  # Patch 98'''

NEW_LOOP = '''            await perp_monitor_step()
            try:  # Patch 114 — tier monitor
                from utils.tier_manager import tier_monitor_step as _tier_step
                await asyncio.to_thread(_tier_step)
            except Exception as _te:
                log.error("tier_monitor_step error: %s", _te)
            _orch_hb("trading")  # Patch 98'''

assert OLD_LOOP in src, "ANCHOR 1 NOT FOUND: perp_monitor_step + _orch_hb"
src = src.replace(OLD_LOOP, NEW_LOOP, 1)
print("✅ Anchor 1: tier_monitor_step hook inserted")

# ── 2–4. New tier endpoints ───────────────────────────────────────────────────

ENDPOINT_ANCHOR = '@app.get("/api/journal/learnings")'
assert ENDPOINT_ANCHOR in src, "ANCHOR 2 NOT FOUND: journal/learnings"

NEW_ENDPOINTS = '''@app.get("/api/tiers/status")  # Patch 114
async def tiers_status_ep(_: str = Depends(get_current_user)):
    """Return tier positions, profit buffer, and config."""
    try:
        from utils.tier_manager import tier_status as _ts  # type: ignore
        return _ts()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/tiers/open-all")  # Patch 114
async def tiers_open_all_ep(_: str = Depends(get_current_user)):
    """Open 3x, 5x, and 10x tier positions simultaneously."""
    try:
        from utils.tier_manager import open_all_tiers as _oat  # type: ignore
        results = await asyncio.to_thread(_oat)
        return {"ok": True, "results": results}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/tiers/open/{tier_label}")  # Patch 114
async def tiers_open_one_ep(tier_label: str, _: str = Depends(get_current_user)):
    """Open a single tier position (3x, 5x, or 10x)."""
    if tier_label not in ("3x", "5x", "10x"):
        raise HTTPException(status_code=400, detail="tier_label must be 3x, 5x, or 10x")
    try:
        from utils.tier_manager import open_tier_position as _otp  # type: ignore
        result = await asyncio.to_thread(_otp, tier_label)
        return result
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


'''

src = src.replace(ENDPOINT_ANCHOR, NEW_ENDPOINTS + ENDPOINT_ANCHOR, 1)
print("✅ Anchor 2: tier endpoints inserted before journal/learnings")

# ── Write + verify ────────────────────────────────────────────────────────────

with open(TARGET, 'w') as f:
    f.write(src)

import py_compile
try:
    py_compile.compile(TARGET, doraise=True)
    print("✅ Syntax check passed")
except py_compile.PyCompileError as e:
    print(f"❌ SYNTAX ERROR: {e}")
    sys.exit(1)

print("\n✅ Patch 114 complete — restart service to activate")
