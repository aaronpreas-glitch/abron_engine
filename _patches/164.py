"""
Patch 164 — Persistent alert rate-limiting, intent enrichment, silent-failure hardening.

Changes:
  - utils/db.py
      • persistent_rate_limit_check() — kv_store-backed, survives restarts
      • kv_store added to init_db() (tests no longer need manual table creation)
  - utils/tier_manager.py
      • _alert_jupiter_api_down()     → persistent_rate_limit_check
      • _alert_manual_required()      → persistent_rate_limit_check
      • _reconcile_ambiguous_intents() timestamp bare-except → log.warning
  - utils/smart_wallet_tracker.py
      • _alert_helius_failure()       → persistent_rate_limit_check
      • _alert_dex_429_swt()          → persistent_rate_limit_check
  - utils/memecoin_scanner.py
      • _alert_dex_429_scanner()      → persistent_rate_limit_check
  - dashboard/backend/routers/tiers.py
      • GET /api/tiers/intents enriched with perp_positions context per intent
  - tests/test_patch162.py
      • TestLifecycle — mocked end-to-end SUBMIT_AMBIGUOUS → block → resolve flow

Run: python3 _patches/164.py
"""
import os
import py_compile
import sqlite3
import sys

ENGINE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(ENGINE_ROOT, "data_storage", "engine.db")

FILES_TO_CHECK = [
    "utils/db.py",
    "utils/tier_manager.py",
    "utils/smart_wallet_tracker.py",
    "utils/memecoin_scanner.py",
    "dashboard/backend/routers/tiers.py",
    "tests/test_patch162.py",
]


def syntax_check(engine_root: str, files: list) -> None:
    """py_compile all changed files; exit non-zero on any failure."""
    print("\n[164] Syntax check:")
    ok = True
    for rel in files:
        path = os.path.join(engine_root, rel)
        try:
            py_compile.compile(path, doraise=True)
            print(f"  ✅ Syntax OK  — {rel}")
        except py_compile.PyCompileError as e:
            print(f"  ❌ Syntax FAIL — {rel}: {e}")
            ok = False
    if not ok:
        sys.exit(1)


def verify_kv_store(db_path: str) -> None:
    """Ensure kv_store table exists in the live DB (idempotent)."""
    print(f"\n[164] Verifying kv_store in {db_path}")
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS kv_store (key TEXT PRIMARY KEY, value TEXT)"
    )
    conn.commit()
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='kv_store'"
    ).fetchone()
    conn.close()
    if row:
        print("  ✅ kv_store table confirmed")
    else:
        print("  ❌ kv_store table missing — check DB permissions")
        sys.exit(1)


def smoke_test_persistent_rl(engine_root: str) -> None:
    """
    Import persistent_rate_limit_check and verify it runs without error.
    Uses a test-only key so it never pollutes real alert cooldowns.
    Skipped gracefully on Python < 3.10 (engine uses PEP 604 syntax).
    """
    print("\n[164] Smoke-testing persistent_rate_limit_check:")
    if sys.version_info < (3, 10):
        print("  ⏭  Skipped (Python < 3.10 — run on VPS for full smoke test)")
        return

    sys.path.insert(0, engine_root)
    try:
        import importlib
        db_mod = importlib.import_module("utils.db")
        fn = db_mod.persistent_rate_limit_check

        # First call: should allow (return False)
        r1 = fn("_patch164_smoke_test", 3600)
        # Second call within window: should suppress (return True)
        r2 = fn("_patch164_smoke_test", 3600)
        # Reset test key so we don't leave a 1h cooldown in the live DB
        import sqlite3 as _sq
        conn = _sq.connect(db_mod.DB_PATH)
        conn.execute("DELETE FROM kv_store WHERE key='alert_ts:_patch164_smoke_test'")
        conn.commit()
        conn.close()

        if r1 is False and r2 is True:
            print("  ✅ persistent_rate_limit_check: allow→suppress cycle correct")
        else:
            print(f"  ❌ unexpected results: r1={r1}, r2={r2} (expected False, True)")
            sys.exit(1)
    except Exception as e:
        print(f"  ❌ smoke test failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    syntax_check(ENGINE_ROOT, FILES_TO_CHECK)
    verify_kv_store(DB_PATH)
    smoke_test_persistent_rl(ENGINE_ROOT)
    print("\n🚀 Patch 164 complete.")
    print("   Changes active on next service restart:")
    print("   • Alert cooldowns persisted to kv_store (survive restarts)")
    print("   • /api/tiers/intents enriched with perp_positions context")
    print("   • _reconcile_ambiguous_intents timestamp parse error now logged")
    print("   • kv_store added to init_db() — no manual fixture creation needed in tests")
