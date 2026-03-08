"""
Patch 163 — Platform reliability: WAL mode, upstream failure alerting, operator workflow.

DB migration: enable WAL journal mode on the live engine.db.
No schema changes — WAL is a connection-level pragma applied at runtime.
This script verifies WAL is active after applying it to the live DB.

Run: python3 _patches/163.py
"""
import os
import sqlite3
import py_compile
import sys

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "data_storage", "engine.db")

ENGINE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FILES_TO_CHECK = [
    "utils/db.py",
    "utils/tier_manager.py",
    "utils/smart_wallet_tracker.py",
    "utils/memecoin_scanner.py",
    "dashboard/backend/routers/tiers.py",
]


def verify_wal(db_path: str) -> None:
    """Enable WAL on the live DB and verify it is active."""
    print(f"\n[163] Enabling WAL on {db_path}")
    conn = sqlite3.connect(db_path)
    result = conn.execute("PRAGMA journal_mode=WAL").fetchone()
    mode = result[0] if result else "unknown"
    conn.execute("PRAGMA busy_timeout=5000")
    conn.commit()
    conn.close()
    if mode == "wal":
        print(f"  ✅ journal_mode=WAL confirmed")
    else:
        print(f"  ⚠️  journal_mode={mode} (expected 'wal' — check DB not on NFS/tmpfs)")


def syntax_check(engine_root: str, files: list) -> None:
    """py_compile all changed files."""
    print("\n[163] Syntax check:")
    ok = True
    for rel in files:
        path = os.path.join(engine_root, rel)
        try:
            py_compile.compile(path, doraise=True)
            print(f"  ✅ Syntax OK — {rel}")
        except py_compile.PyCompileError as e:
            print(f"  ❌ Syntax FAIL — {rel}: {e}")
            ok = False
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    syntax_check(ENGINE_ROOT, FILES_TO_CHECK)
    verify_wal(DB_PATH)
    print("\n🚀 Patch 163 complete.")
    print("   Changes active on next service restart:")
    print("   • WAL + busy_timeout on all DB connections")
    print("   • Jupiter API down → Telegram alert (30 min rate limit)")
    print("   • Helius non-200 → Telegram alert (1h rate limit)")
    print("   • DexScreener 429 → Telegram alert (30 min rate limit, scanner + swt)")
    print("   • Helius fetch errors upgraded debug→warning in logs")
    print("   • GET  /api/tiers/intents     — operator intent inspection")
    print("   • POST /api/tiers/intents/{id}/resolve — manual RECONCILE_MANUAL_REQUIRED resolution")
