"""
Patch 77 — Extend Simulation Endpoint
Backend: POST /api/risk/extend-simulation
  - Inserts a new SIMULATE_AUTO_STARTED row into live_transition_log
  - The hours_active calculation in simulate-review queries for the
    most recent SIMULATE_AUTO_STARTED event — inserting a new row
    resets the 24h clock without any in-memory changes
  - Used by the "Extend +24h" button in BrainSimReviewModal
"""

import subprocess, sys
from pathlib import Path

ROOT = Path("/root/memecoin_engine")
MAIN = ROOT / "dashboard/backend/main.py"

main_text = MAIN.read_text()

ANCHOR = '@app.get("/api/journal/learnings")'
assert ANCHOR in main_text, f"anchor not found: {ANCHOR}"
assert "_extend_simulation" not in main_text, "endpoint already inserted"

NEW_ENDPOINT = '''
# ── Extend Simulation +24h ─────────────────────────────────────────────────────
@app.post("/api/risk/extend-simulation")
async def risk_extend_simulation(_: str = Depends(get_current_user)):
    """Reset simulation 24h clock by inserting a new SIMULATE_AUTO_STARTED event.
    hours_active is computed from the most recent such event, so this effectively
    extends the simulation window by 24h from now."""
    import sqlite3 as _sq
    ts_now = datetime.utcnow().isoformat() + "Z"
    db_path = os.path.join(_engine_root(), "data_storage", "engine.db")
    try:
        with _sq.connect(db_path) as c:
            c.execute(
                "INSERT INTO live_transition_log (event_type, ts_utc, note) VALUES (?, ?, ?)",
                ("SIMULATE_AUTO_STARTED", ts_now, "Extended +24h via dashboard button"),
            )
        log.info("extend_simulation: clock reset at %s", ts_now)
        return JSONResponse({
            "ok":      True,
            "message": "Simulation extended by 24h — clock reset",
            "ts":      ts_now,
        })
    except Exception as exc:
        log.warning("extend_simulation error: %s", exc)
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


'''

main_text = main_text.replace(ANCHOR, NEW_ENDPOINT + ANCHOR)
assert "_extend_simulation" in main_text, "endpoint not inserted"
MAIN.write_text(main_text)
print("✓ main.py — /api/risk/extend-simulation inserted")

r = subprocess.run([sys.executable, "-m", "py_compile", str(MAIN)], capture_output=True, text=True)
if r.returncode != 0:
    print("✗ compile error:", r.stderr); sys.exit(1)
print("✓ main.py compiles OK")
