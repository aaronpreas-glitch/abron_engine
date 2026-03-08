"""
Patch 80a — DB schema for DCA + Winner-Run system
- ALTER TABLE perp_positions: add dca_count, last_dca_ts columns
- CREATE TABLE dca_log
"""
import subprocess, sys, sqlite3
from pathlib import Path

ROOT    = Path("/root/memecoin_engine")
DB_PATH = ROOT / "data_storage" / "engine.db"

print(f"Opening DB: {DB_PATH}")
with sqlite3.connect(str(DB_PATH)) as conn:
    c = conn.cursor()

    # Add dca_count column
    try:
        c.execute("ALTER TABLE perp_positions ADD COLUMN dca_count INTEGER DEFAULT 0")
        print("✓ dca_count column added")
    except sqlite3.OperationalError as e:
        if "duplicate column" in str(e).lower():
            print("  dca_count already exists — skipping")
        else:
            raise

    # Add last_dca_ts column
    try:
        c.execute("ALTER TABLE perp_positions ADD COLUMN last_dca_ts TEXT")
        print("✓ last_dca_ts column added")
    except sqlite3.OperationalError as e:
        if "duplicate column" in str(e).lower():
            print("  last_dca_ts already exists — skipping")
        else:
            raise

    # Create dca_log table
    c.execute("""
        CREATE TABLE IF NOT EXISTS dca_log (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            position_id     INTEGER NOT NULL,
            ts              TEXT NOT NULL,
            symbol          TEXT NOT NULL,
            side            TEXT NOT NULL,
            dca_number      INTEGER NOT NULL,
            dca_size_usd    REAL NOT NULL,
            price_at_dca    REAL NOT NULL,
            pnl_at_dca_pct  REAL NOT NULL,
            new_avg_entry   REAL NOT NULL,
            reason          TEXT
        )
    """)
    print("✓ dca_log table ready")
    conn.commit()

# Verify schema
with sqlite3.connect(str(DB_PATH)) as conn:
    cols = [r[1] for r in conn.execute("PRAGMA table_info(perp_positions)").fetchall()]
    assert "dca_count" in cols, "dca_count column missing!"
    assert "last_dca_ts" in cols, "last_dca_ts column missing!"
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    assert "dca_log" in tables, "dca_log table missing!"

print("✓ All schema migrations verified")
print("✓ Patch 80a complete")
