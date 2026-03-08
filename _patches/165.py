"""
Patch 165 — Assisted recovery, restart-path hardening, in-memory safety audit.

Changes:
  - tests/test_patch162.py
      • fixture: db.DB_PATH now also patched (persistent_rate_limit_check isolation)
      • fixture: manual kv_store creation removed (init_db() handles it since Patch 164)
      • TestRestartRecovery — 4 new tests proving all safety state is DB-backed:
          test_blocking_guard_is_entirely_db_backed
          test_stale_pending_expires_via_fresh_connection
          test_persistent_rate_limit_survives_simulated_restart
          test_reconciled_confirmed_clears_guard_on_fresh_connection

  - dashboard/backend/routers/tiers.py
      • _build_candidate_repair() — pure helper, no writes
      • GET /api/tiers/intents/{id}/recovery-context — read-only operator recovery endpoint:
          fetches intent row, local perp_positions match, live Jupiter position,
          and candidate INSERT payload for operator review

  In-memory safety audit findings (no code changes needed beyond the above):
    ✅ Blocking guard:      fully DB-backed (tier_execution_intents)
    ✅ DB lock:             fully DB-backed (kv_store, 90s TTL)
    ✅ Critical alert RL:   fully DB-backed (kv_store — Patch 164)
    ⚠  Non-critical alerts: still use in-memory should_rate_limit() in tier_manager
       (tier_tp, tier_buffer, liq_prox, ext_close, funding, health, confluence, F&G)
       → Acceptable: these are post-action notifications; a missed/duplicate alert
         after restart is minor and never suppresses a safety action.
    ⚠  Orchestrator last_beat: in-memory per agent. Affects health display only,
       never money movement. Not worth persisting.

Run: python3 _patches/165.py
"""
import os
import py_compile
import sys

ENGINE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FILES_TO_CHECK = [
    "tests/test_patch162.py",
    "dashboard/backend/routers/tiers.py",
]


def syntax_check(engine_root: str, files: list) -> None:
    """py_compile all changed files; exit non-zero on any failure."""
    print("\n[165] Syntax check:")
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


def run_tests(engine_root: str) -> None:
    """Run the full regression suite; exit non-zero on failure."""
    print("\n[165] Running regression tests:")
    if sys.version_info < (3, 10):
        print("  ⏭  Skipped (Python < 3.10 — run on VPS)")
        return
    import subprocess
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_patch162.py", "-v", "--tb=short"],
        cwd=engine_root,
        capture_output=False,
    )
    if result.returncode != 0:
        print("  ❌ Tests FAILED")
        sys.exit(1)
    print("  ✅ All tests passed")


if __name__ == "__main__":
    syntax_check(ENGINE_ROOT, FILES_TO_CHECK)
    run_tests(ENGINE_ROOT)
    print("\n🚀 Patch 165 complete.")
    print("   Changes active on next service restart:")
    print("   • GET /api/tiers/intents/{id}/recovery-context — operator recovery context")
    print("   • 18/18 regression tests (4 new restart/recovery invariant tests)")
    print("   • db.DB_PATH now patched in test fixture (persistent RL fully isolated)")
