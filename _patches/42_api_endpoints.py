#!/usr/bin/env python3
"""Patch 42 — Add ML importance history + dynamic exit log API endpoints."""

TARGET = "/root/memecoin_engine/dashboard/backend/main.py"

with open(TARGET, "r") as f:
    code = f.read()

# Insert after ml-recent-predictions endpoint, before journal/learnings
MARKER = '''@app.get("/api/journal/learnings")'''

NEW_ENDPOINTS = '''
@app.get("/api/brain/ml-importance-history")
async def brain_ml_importance_history():
    """Get feature importance history across training runs."""
    import sys
    root = _engine_root()
    if root not in sys.path:
        sys.path.insert(0, root)
    try:
        from utils.ml_predictor import get_importance_history
        history = get_importance_history()
        return {"history": history, "n": len(history)}
    except Exception as e:
        return {"error": str(e), "history": []}


@app.get("/api/brain/dynamic-exit-log")
async def brain_dynamic_exit_log():
    """Get dynamic exit decisions log."""
    try:
        import sqlite3, os
        db = os.path.join(_engine_root(), "data_storage", "engine.db")
        with sqlite3.connect(db) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT del.*, pp.symbol, pp.side, pp.pnl_pct as final_pnl,
                       pp.exit_reason as final_exit_reason
                FROM dynamic_exit_log del
                LEFT JOIN perp_positions pp ON del.position_id = pp.id
                ORDER BY del.ts_utc DESC
                LIMIT 100
            """).fetchall()
        entries = [dict(r) for r in rows]
        return {"entries": entries, "n": len(entries)}
    except Exception as e:
        return {"error": str(e), "entries": []}


''' + MARKER

assert MARKER in code, f"Cannot find marker: {MARKER}"
code = code.replace(MARKER, NEW_ENDPOINTS, 1)

with open(TARGET, "w") as f:
    f.write(code)
print(f"[OK] Wrote {TARGET} ({len(code)} bytes)")

# Verify compilation
import subprocess
result = subprocess.run(
    ["python3", "-c", f"import py_compile; py_compile.compile('{TARGET}', doraise=True)"],
    capture_output=True, text=True
)
if result.returncode == 0:
    print("[OK] main.py compiles successfully")
else:
    print(f"[ERROR] Compilation failed:\n{result.stderr}")
    import sys
    sys.exit(1)

print("[OK] Added /api/brain/ml-importance-history endpoint")
print("[OK] Added /api/brain/dynamic-exit-log endpoint")
