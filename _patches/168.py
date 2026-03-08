"""
Patch 168 — Duplicate-entry hardening and horizon/dedup counterfactual analysis.

Not a live-execution patch. Perp trading is not touched.

Changes:
  - utils/executor.py
      A. Symbol cooldown for paper (DRY_RUN) mode.
         Before opening a paper entry, checks whether an alert_outcome row
         already exists for this symbol within EXECUTOR_SYMBOL_COOLDOWN_H hours
         (default 4.0). If yes, skips the entry and logs a COOLDOWN message.
         Never fires when EXECUTOR_DRY_RUN=false (live mode).
         Set EXECUTOR_SYMBOL_COOLDOWN_H=0 to disable.

  - dashboard/backend/main.py
      B. GET /api/brain/horizon-comparison
           Shows how the same paper trades perform at 1h / 4h / 24h.
           Counts turnaround trades (4h<0 but 24h>0) and deterioration trades.
           Computes early_exit_delta: avg(24h_return - 4h_return).
           Answers: is the 4h evaluation horizon cutting winners short?

      C. GET /api/brain/dedup-counterfactual
           Classifies alert_outcome rows as originals vs duplicates
           (same symbol re-entered within dedup_window_h, default 4h).
           Computes four expectancy scenarios:
             all_4h / deduped_4h / all_24h / deduped_24h
           Attribution: how much of the gap comes from duplicate removal
           vs switching to the 24h evaluation horizon.

Run: python3 _patches/168.py
"""
import os
import py_compile
import sys

ENGINE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FILES_TO_CHECK = [
    "utils/executor.py",
    "dashboard/backend/main.py",
]


def syntax_check(engine_root: str, files: list) -> None:
    print("\n[168] Syntax check:")
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
    print("\n[168] Endpoint smoke test:")
    if sys.version_info < (3, 10):
        print("  ⏭  Skipped (Python < 3.10 — run on VPS)")
        return

    import subprocess, json
    base = "http://localhost:8000"

    tok = subprocess.run(
        ["curl", "-s", "-X", "POST", f"{base}/api/auth/login",
         "-H", "Content-Type: application/json",
         "-d", '{"password":"HArden978ab"}'],
        capture_output=True, text=True,
    )
    try:
        token = json.loads(tok.stdout)["token"]
    except Exception as e:
        print(f"  ❌ Auth failed: {e}")
        sys.exit(1)

    hdrs = ["-H", f"Authorization: Bearer {token}"]
    tests = [
        ("/api/brain/horizon-comparison",      ["by_horizon", "turnaround_count", "early_exit_delta_avg_pct"]),
        ("/api/brain/dedup-counterfactual",    ["scenarios", "attribution", "duplicate_count"]),
    ]

    all_ok = True
    for path, expected_keys in tests:
        r = subprocess.run(
            ["curl", "-s"] + hdrs + [f"{base}{path}"],
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
    print("\n🚀 Patch 168 complete.")
    print("   Changes active on next service restart:")
    print("   • executor.py: paper-mode symbol cooldown (EXECUTOR_SYMBOL_COOLDOWN_H=4.0)")
    print("   • GET /api/brain/horizon-comparison    — 1h/4h/24h outcome comparison")
    print("   • GET /api/brain/dedup-counterfactual  — duplicate-removal + horizon counterfactual")
