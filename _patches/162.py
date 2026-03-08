#!/usr/bin/env python3
"""
Patch 162 — Corrective patch for Patch 161: duplicate-open guard, real Jupiter
reconciliation, and fresh-DB schema safety.

DB migration: idempotent ALTER TABLE to ensure perp_positions has
jupiter_position_key, tx_sig_open, tx_sig_close on existing deployments.
(Fresh DBs already get these columns via the updated init_db().)

Modified files:
  utils/db.py          — perp_positions base schema gains 3 columns + ALTER fallback
  utils/tier_manager.py — _AMBIGUOUS_GUARD_HOURS, _insert_execution_intent status param,
                          _get_blocking_intents, open_tier_position guard + lock record,
                          _reconcile_ambiguous_intents rewritten to use Jupiter API
"""
import py_compile
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
DB   = ROOT / "data_storage" / "engine.db"

# ── DB migration ──────────────────────────────────────────────────────────────

with sqlite3.connect(str(DB)) as conn:
    # Idempotent — silently skips if column already exists (Patch 108 may have added them)
    for col_def in (
        "jupiter_position_key TEXT",
        "tx_sig_open TEXT",
        "tx_sig_close TEXT",
    ):
        try:
            conn.execute(f"ALTER TABLE perp_positions ADD COLUMN {col_def}")
            print(f"✅ Added column: perp_positions.{col_def.split()[0]}")
        except Exception:
            print(f"   (skip) perp_positions.{col_def.split()[0]} already exists")
    conn.commit()

# ── Syntax check ──────────────────────────────────────────────────────────────

files = [
    ROOT / "utils" / "db.py",
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
print("🚀 Patch 162 complete — restart service to activate.")
print("   Verify guard: INSERT a SUBMIT_AMBIGUOUS row for a tier+symbol,")
print("   then attempt open_tier_position() for the same tier+symbol —")
print("   should return state='BLOCKED_PENDING_RECONCILIATION'.")
