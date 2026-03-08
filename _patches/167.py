"""
Patch 167 — Memecoin expectancy diagnostics and promotion discipline.

Not a live-execution patch. No trading behaviour changes.
All changes are read-only analytical endpoints that query existing data.

Changes:
  - dashboard/backend/main.py
      A. GET /api/brain/expectancy-decomposition
           Win rate, avg win/loss, payoff ratio, expectancy for last N trades.
           Broken down by confidence and horizon (1h/4h/24h).
           Rolling 10-trade expectancy series shows trend direction.

      B. GET /api/brain/loss-clustering
           Clusters completed trades by: score band, regime_label, lane,
           cycle_phase. Surfaces worst 20 single-trade losses.
           Identifies where negative expectancy is concentrated.

      C. GET /api/brain/regime-diagnosis
           Performance by regime_label + cycle_phase + weekly trend (8 weeks).
           Auto-generates diagnosis_notes bullets (best/worst regime, trend
           direction, payoff ratio warning).

      D. GET /api/brain/memecoin-readiness (enriched, same URL)
           Added gate_detail: per-gate value vs threshold + passing flag + delta.
           Added path_to_watch + path_to_ready: specific improvement steps.

Run: python3 _patches/167.py
"""
import os
import py_compile
import sys

ENGINE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FILES_TO_CHECK = [
    "dashboard/backend/main.py",
]


def syntax_check(engine_root: str, files: list) -> None:
    """py_compile all changed files; exit non-zero on any failure."""
    print("\n[167] Syntax check:")
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


def smoke_endpoints(engine_root: str) -> None:
    """
    Hit each new endpoint once against the live DB and confirm 200 + expected keys.
    Skipped on Python < 3.10 (PEP 604 syntax in main codebase).
    """
    print("\n[167] Endpoint smoke test:")
    if sys.version_info < (3, 10):
        print("  ⏭  Skipped (Python < 3.10 — run on VPS)")
        return

    import subprocess, json
    base = "http://localhost:8000"

    # Get auth token
    tok_result = subprocess.run(
        [
            "curl", "-s", "-X", "POST",
            f"{base}/api/auth/login",
            "-H", "Content-Type: application/json",
            "-d", '{"password":"HArden978ab"}',
        ],
        capture_output=True, text=True,
    )
    try:
        token = json.loads(tok_result.stdout)["token"]
    except Exception as e:
        print(f"  ❌ Auth failed: {e}")
        sys.exit(1)

    headers = ["-H", f"Authorization: Bearer {token}"]
    tests = [
        ("/api/brain/memecoin-readiness",            ["verdict", "gate_detail", "path_to_watch"]),
        ("/api/brain/expectancy-decomposition",      ["overall", "by_horizon", "rolling"]),
        ("/api/brain/loss-clustering",               ["by_score_band", "by_regime", "worst_20"]),
        ("/api/brain/regime-diagnosis",              ["by_regime", "weekly_trend", "diagnosis_notes"]),
    ]

    all_ok = True
    for path, expected_keys in tests:
        r = subprocess.run(
            ["curl", "-s"] + headers + [f"{base}{path}"],
            capture_output=True, text=True,
        )
        try:
            data = json.loads(r.stdout)
            missing = [k for k in expected_keys if k not in data]
            if missing:
                print(f"  ❌ {path} — missing keys: {missing}")
                all_ok = False
            else:
                print(f"  ✅ {path} — OK")
        except Exception as e:
            print(f"  ❌ {path} — JSON parse error: {e}")
            all_ok = False

    if not all_ok:
        sys.exit(1)


if __name__ == "__main__":
    syntax_check(ENGINE_ROOT, FILES_TO_CHECK)
    smoke_endpoints(ENGINE_ROOT)
    print("\n🚀 Patch 167 complete.")
    print("   New endpoints:")
    print("   • GET /api/brain/expectancy-decomposition  — payoff ratio + rolling EV")
    print("   • GET /api/brain/loss-clustering           — where losses concentrate")
    print("   • GET /api/brain/regime-diagnosis          — regime/phase/weekly breakdown")
    print("   • GET /api/brain/memecoin-readiness        — enriched with gate_detail + path_to_*")
