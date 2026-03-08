"""
Patch 80c — main.py: DCA history + DCA stats API endpoints
- GET /api/perps/dca-history?position_id=X  → full DCA log for a position
- GET /api/brain/dca-stats                  → aggregate DCA statistics
"""
import subprocess, sys
from pathlib import Path

ROOT = Path("/root/memecoin_engine")
MAIN = ROOT / "dashboard" / "backend" / "main.py"

text = MAIN.read_text()

# ─────────────────────────────────────────────────────────────────────────────
# Insert two new endpoints before the journal/learnings anchor
# ─────────────────────────────────────────────────────────────────────────────
ANCHOR = '@app.get("/api/journal/learnings")'
assert ANCHOR in text, f"Anchor '{ANCHOR}' not found in main.py"

NEW_ENDPOINTS = '''
@app.get("/api/perps/dca-history")
async def perps_dca_history(
    position_id: int,
    _: str = Depends(get_current_user),
):
    """Return full DCA log for a specific position."""
    import sqlite3 as _sq
    db_path = os.path.join(_engine_root(), "data_storage", "engine.db")
    try:
        with _sq.connect(db_path) as c:
            # Ensure dca_log exists (created by Patch 80a on VPS)
            rows = c.execute(
                "SELECT id, ts, dca_number, dca_size_usd, price_at_dca,"
                " pnl_at_dca_pct, new_avg_entry, reason"
                " FROM dca_log WHERE position_id=? ORDER BY dca_number ASC",
                (position_id,)
            ).fetchall()
        entries = [
            {
                "id":            r[0],
                "ts":            r[1],
                "dca_number":    r[2],
                "dca_size_usd":  r[3],
                "price_at_dca":  r[4],
                "pnl_at_dca_pct":r[5],
                "new_avg_entry": r[6],
                "reason":        r[7],
            }
            for r in rows
        ]
        return JSONResponse({"position_id": position_id, "entries": entries, "count": len(entries)})
    except Exception as exc:
        return JSONResponse({"position_id": position_id, "entries": [], "count": 0,
                             "error": str(exc)})


@app.get("/api/brain/dca-stats")
async def brain_dca_stats(_: str = Depends(get_current_user)):
    """Aggregate DCA statistics across all paper positions."""
    import sqlite3 as _sq
    from datetime import datetime, timezone, timedelta
    db_path = os.path.join(_engine_root(), "data_storage", "engine.db")
    try:
        with _sq.connect(db_path) as c:
            # Check if dca_log table exists
            tbl = c.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='dca_log'"
            ).fetchone()
            if not tbl:
                return JSONResponse({
                    "total_dcas": 0, "total_positions_dcad": 0,
                    "avg_pnl_at_dca": None, "pnl_improvement_avg": None,
                    "dca_win_rate": None, "recent_dcas": [], "ts": datetime.utcnow().isoformat() + "Z"
                })

            # Total DCA events
            total_dcas = c.execute("SELECT COUNT(*) FROM dca_log").fetchone()[0]
            total_positions = c.execute(
                "SELECT COUNT(DISTINCT position_id) FROM dca_log"
            ).fetchone()[0]

            # Avg PnL at DCA trigger (should be negative — averaging down)
            avg_pnl_at_dca = c.execute(
                "SELECT AVG(pnl_at_dca_pct) FROM dca_log"
            ).fetchone()[0]

            # How did positions close after DCA?
            # Compare DCA'd positions vs non-DCA'd positions final pnl_pct
            dcad_pnl_row = c.execute("""
                SELECT AVG(p.pnl_pct)
                FROM perp_positions p
                JOIN dca_log d ON d.position_id = p.id
                WHERE p.status = 'CLOSED' AND p.dry_run = 1
            """).fetchone()
            non_dcad_pnl_row = c.execute("""
                SELECT AVG(p.pnl_pct)
                FROM perp_positions p
                WHERE p.status = 'CLOSED' AND p.dry_run = 1
                  AND p.id NOT IN (SELECT DISTINCT position_id FROM dca_log)
                  AND p.notes LIKE '%mode=MID%'
            """).fetchone()
            dcad_avg    = dcad_pnl_row[0]
            non_dcad_avg = non_dcad_pnl_row[0]

            # Win rate on DCA'd positions
            dcad_wins = c.execute("""
                SELECT COUNT(*)
                FROM perp_positions p
                JOIN dca_log d ON d.position_id = p.id
                WHERE p.status = 'CLOSED' AND p.dry_run = 1 AND p.pnl_pct > 0
            """).fetchone()[0]
            dcad_total = c.execute("""
                SELECT COUNT(DISTINCT p.id)
                FROM perp_positions p
                JOIN dca_log d ON d.position_id = p.id
                WHERE p.status = 'CLOSED' AND p.dry_run = 1
            """).fetchone()[0]

            # Recent DCA events (last 10)
            recent = c.execute(
                "SELECT d.ts, d.symbol, d.side, d.dca_number, d.dca_size_usd,"
                " d.price_at_dca, d.pnl_at_dca_pct, d.new_avg_entry"
                " FROM dca_log d ORDER BY d.id DESC LIMIT 10"
            ).fetchall()
            recent_list = [
                {
                    "ts":            r[0], "symbol":      r[1], "side":        r[2],
                    "dca_number":    r[3], "dca_size_usd":r[4], "price_at_dca":r[5],
                    "pnl_at_dca_pct":r[6], "new_avg_entry":r[7],
                }
                for r in recent
            ]

        return JSONResponse({
            "total_dcas":            total_dcas,
            "total_positions_dcad":  total_positions,
            "avg_pnl_at_dca":        round(avg_pnl_at_dca, 2) if avg_pnl_at_dca is not None else None,
            "dcad_avg_final_pnl":    round(dcad_avg, 2) if dcad_avg is not None else None,
            "non_dcad_avg_final_pnl":round(non_dcad_avg, 2) if non_dcad_avg is not None else None,
            "dcad_win_rate":         round(dcad_wins / dcad_total * 100, 1) if dcad_total > 0 else None,
            "recent_dcas":           recent_list,
            "ts":                    datetime.utcnow().isoformat() + "Z",
        })
    except Exception as exc:
        return JSONResponse({"error": str(exc), "total_dcas": 0, "recent_dcas": []})


'''

text = text.replace(ANCHOR, NEW_ENDPOINTS + ANCHOR)
print("✓ /api/perps/dca-history endpoint inserted")
print("✓ /api/brain/dca-stats endpoint inserted")

MAIN.write_text(text)

r = subprocess.run(
    [sys.executable, "-m", "py_compile", str(MAIN)],
    capture_output=True, text=True
)
if r.returncode != 0:
    print("✗ compile error:", r.stderr)
    sys.exit(1)
print("✓ main.py compiles OK")
print("✓ Patch 80c complete")
