"""
Patch 63 — Live-Prep Safeguards v2
====================================
1. /api/brain/live-checklist: add simulate_live_mode field to response
2. /api/risk/simulate-live (POST):
   - Toggle SIMULATE_LIVE_MODE in .env + os.environ
   - Log SIMULATE_ENABLED / SIMULATE_DISABLED event to live_transition_log
   - Returns {ok, simulate_live_mode, ts}
"""

from pathlib import Path
import py_compile, tempfile, shutil

MAIN = Path(__file__).resolve().parent.parent / "dashboard" / "backend" / "main.py"
main = MAIN.read_text()

# ── 1. Patch live-checklist response — add simulate_live_mode ─────────────────

OLD_CL_RETURN = (
    '        "real_money_mode": rm_mode,\n'
    '    })\n'
)
NEW_CL_RETURN = (
    '        "real_money_mode": rm_mode,\n'
    '        "simulate_live_mode": os.environ.get("SIMULATE_LIVE_MODE", "false").lower() in ("1", "true", "yes"),\n'
    '    })\n'
)
assert OLD_CL_RETURN in main, "live-checklist return anchor not found"
main = main.replace(OLD_CL_RETURN, NEW_CL_RETURN)
print("✅ simulate_live_mode added to live-checklist response")

# ── 2. Add /api/risk/simulate-live endpoint ────────────────────────────────────

INSERT_ANCHOR = '@app.get("/api/journal/learnings")'
assert INSERT_ANCHOR in main, "learnings anchor not found"

SIMULATE_EP = r'''@app.post("/api/risk/simulate-live")
async def risk_simulate_live(request: Request, _: str = Depends(get_current_user)):
    """Toggle SIMULATE_LIVE_MODE — real-money sizing with paper routing."""
    import sqlite3 as _sq, re as _re
    body = await request.json()
    enable = bool(body.get("enable", False))
    new_val = "true" if enable else "false"
    try:
        env_path = os.path.join(_engine_root(), ".env")
        if os.path.exists(env_path):
            env_text = open(env_path).read()
            if "SIMULATE_LIVE_MODE" in env_text:
                env_text = _re.sub(r"SIMULATE_LIVE_MODE=\S*", f"SIMULATE_LIVE_MODE={new_val}", env_text)
            else:
                env_text += f"\nSIMULATE_LIVE_MODE={new_val}\n"
            open(env_path, "w").write(env_text)
        os.environ["SIMULATE_LIVE_MODE"] = new_val
        db_path = os.path.join(_engine_root(), "data_storage", "engine.db")
        event_type = "SIMULATE_ENABLED" if enable else "SIMULATE_DISABLED"
        with _sq.connect(db_path) as c:
            c.execute(
                "CREATE TABLE IF NOT EXISTS live_transition_log "
                "(id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, event_type TEXT, "
                "symbol TEXT, side TEXT, mode TEXT, reason TEXT, metadata TEXT)"
            )
            c.execute(
                "INSERT INTO live_transition_log (ts, event_type, symbol, side, mode, reason) VALUES (?,?,?,?,?,?)",
                (datetime.utcnow().isoformat() + "Z", event_type, "", "", "SIMULATE",
                 "SIMULATE_LIVE_MODE toggled via dashboard"),
            )
        return JSONResponse({
            "ok": True,
            "simulate_live_mode": enable,
            "ts": datetime.utcnow().isoformat() + "Z",
        })
    except Exception as exc:
        log.warning("risk_simulate_live error: %s", exc)
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


'''

main = main.replace(INSERT_ANCHOR, SIMULATE_EP + INSERT_ANCHOR)
print("✅ /api/risk/simulate-live endpoint inserted")

MAIN.write_text(main)

tmp = Path(tempfile.mktemp(suffix=".py"))
shutil.copy(MAIN, tmp)
py_compile.compile(str(tmp), doraise=True)
tmp.unlink()
print("✅ main.py compiles OK")
print("\nPatch 63 complete")
