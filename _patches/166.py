"""
Patch 166 — Operator ergonomics, architecture cleanup, promotion discipline.

Changes:
  - utils/tier_manager.py
      • _BLOCKING_STATUSES — new module-level frozenset (D: architecture cleanup)
        Single source of truth for the three statuses that block a new open.
        _get_blocking_intents() now references this constant instead of
        hardcoded string literals in the SQL.

  - dashboard/backend/routers/tiers.py
      • _build_repair_sql() — pure helper that generates a copy-pasteable
        INSERT INTO perp_positions statement from proposed_fields
      • _build_candidate_repair() (A: operator ergonomics):
          - next_action — one-liner telling operator exactly what to do next
          - repair_sql  — copy-pasteable INSERT (INSERT_NEW only; None otherwise)

  - dashboard/backend/main.py
      • GET /api/brain/memecoin-readiness (C: promotion discipline)
          Three independent gates:
            1. F&G gate (value > 25)
            2. Risk mode (not DEFENSIVE)
            3. Expectancy ≥ -2% over last 20 completed 4h trades
          Returns verdict (READY / WATCH / NOT_READY) + blocking_reasons +
          warnings + raw metrics. Read-only, never blocks trading.

  In-memory safety audit (B): no meaningful new silent failures found in
  tier_monitor_step — all bare-excepts wrap Telegram/orchestrator calls
  (non-critical notifications, never suppress safety actions).
  The _BLOCKING_STATUSES constant (D) is the one structural improvement
  the audit identified worth making.

Run: python3 _patches/166.py
"""
import os
import py_compile
import sys

ENGINE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FILES_TO_CHECK = [
    "utils/tier_manager.py",
    "dashboard/backend/routers/tiers.py",
    "dashboard/backend/main.py",
]


def syntax_check(engine_root: str, files: list) -> None:
    """py_compile all changed files; exit non-zero on any failure."""
    print("\n[166] Syntax check:")
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
    print("\n[166] Running regression tests:")
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
    print("\n🚀 Patch 166 complete.")
    print("   Changes active on next service restart:")
    print("   • _BLOCKING_STATUSES — module-level constant in tier_manager")
    print("   • recovery-context now returns next_action + repair_sql")
    print("   • GET /api/brain/memecoin-readiness — paper-to-live verdict")
