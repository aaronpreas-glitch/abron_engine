#!/usr/bin/env python3
"""
Patch 161 — Idempotent Jupiter Perps Tier Execution

DB migration: creates tier_execution_intents table + two indexes.
Run on VPS before restarting the service.

Modified files (already committed locally):
  utils/db.py              — init_db() gains tier_execution_intents
  utils/jupiter_perps_trade.py — _sign_tx returns tuple, open_perp_sync state model
  utils/tier_manager.py    — lock + intent tracking, atomic TP close + buffer
"""
import py_compile
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
DB   = ROOT / "data_storage" / "engine.db"

# ── DB migration ──────────────────────────────────────────────────────────────

with sqlite3.connect(str(DB)) as conn:
    conn.execute("""
    CREATE TABLE IF NOT EXISTS tier_execution_intents (
        id                      INTEGER PRIMARY KEY AUTOINCREMENT,
        created_ts              TEXT    NOT NULL,
        resolved_ts             TEXT,
        tier_label              TEXT    NOT NULL,
        symbol                  TEXT    NOT NULL,
        side                    TEXT    NOT NULL,
        collateral_usd          REAL    NOT NULL,
        leverage                REAL    NOT NULL,
        status                  TEXT    NOT NULL DEFAULT 'PENDING',
        presigned_tx_sig        TEXT,
        position_pubkey         TEXT,
        tx_sig_confirmed        TEXT,
        perp_position_id        INTEGER,
        error_detail            TEXT,
        build_response_excerpt  TEXT
    )
    """)
    conn.execute("""
    CREATE INDEX IF NOT EXISTS idx_tei_status
    ON tier_execution_intents(status)
    """)
    conn.execute("""
    CREATE INDEX IF NOT EXISTS idx_tei_tier_created
    ON tier_execution_intents(tier_label, created_ts)
    """)
    conn.commit()
    print("✅ tier_execution_intents created (or already existed)")

# ── Syntax check ──────────────────────────────────────────────────────────────

files = [
    ROOT / "utils" / "db.py",
    ROOT / "utils" / "jupiter_perps_trade.py",
    ROOT / "utils" / "tier_manager.py",
]

ok = True
for f in files:
    try:
        py_compile.compile(str(f), doraise=True)
        print(f"✅ Syntax OK — {f.name}")
    except py_compile.PyCompileError as e:
        print(f"❌ Syntax error in {f.name}: {e}")
        ok = False

if not ok:
    sys.exit(1)

print()
print("🚀 Patch 161 complete — restart service to activate.")
print("   Verify: sqlite3 data_storage/engine.db '.tables' | grep tier_execution")
