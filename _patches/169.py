"""
Patch 169 — Policy comparison and readiness horizon context.

Not a live-execution patch. No trading behaviour changes.
All changes are read-only analytical endpoints / enrichments.

Changes:
  - dashboard/backend/main.py
      A. GET /api/brain/policy-comparison
           Compares three evaluation policies on the same sample of paper trades
           (must have BOTH return_4h_pct AND return_24h_pct populated):
             current_4h  — use return_4h_pct as-is (the current default)
             hold_24h    — use return_24h_pct (hold every position to 24h)
             hybrid      — stop at 4h if return_4h < stop_threshold (-5% default),
                           otherwise hold to 24h
           Returns expectancy / win-rate / payoff ratio for each policy,
           stops_triggered count for hybrid, per-trade breakdown.

      B. GET /api/brain/memecoin-readiness (enriched)
           Added 24h context block (informational, does NOT change the verdict):
             evaluation_policy  — "4h" (current gate criterion)
             horizon_note       — one-sentence explanation of the horizon gap
             horizon_context    — 4h vs 24h metrics + what verdict would be at 24h

      C. decision_support block inside policy-comparison
           best_policy, verdict_changes_at_24h, horizon_gain_pct, summary.

Run: python3 _patches/169.py
"""
import os
import py_compile
import sys

ENGINE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FILES_TO_CHECK = [
    "dashboard/backend/main.py",
]


def syntax_check(engine_root: str, files: list) -> None:
    print("\n[169] Syntax check:")
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
    print("\n[169] Endpoint smoke test:")
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
        (
            "/api/brain/memecoin-readiness",
            ["verdict", "evaluation_policy", "horizon_context", "horizon_note"],
        ),
        (
            "/api/brain/policy-comparison",
            ["policies", "decision_support", "trade_categories"],
        ),
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
                print(f"     Response: {r.stdout[:300]}")
                all_ok = False
            else:
                print(f"  ✅ {path} — OK")
                # Print a quick digest of policy-comparison
                if "policy-comparison" in path:
                    pc = data.get("policies", {})
                    ds = data.get("decision_support", {})
                    for pol, metrics in pc.items():
                        exp = metrics.get("expectancy_pct", "?")
                        wr  = metrics.get("win_rate_pct", "?")
                        print(f"     {pol:12s}  exp={exp:+.2f}%  wr={wr:.1f}%")
                    print(f"     → best_policy: {ds.get('best_policy')}  "
                          f"horizon_gain: {ds.get('horizon_gain_pct')}%")
        except Exception as e:
            print(f"  ❌ {path} — JSON parse error: {e}")
            print(f"     Raw: {r.stdout[:300]}")
            all_ok = False

    if not all_ok:
        sys.exit(1)


if __name__ == "__main__":
    syntax_check(ENGINE_ROOT, FILES_TO_CHECK)
    smoke_endpoints(ENGINE_ROOT)
    print("\n🚀 Patch 169 complete.")
    print("   Changes active on next service restart:")
    print("   • GET /api/brain/policy-comparison      — 4h vs 24h vs hybrid expectancy comparison")
    print("   • GET /api/brain/memecoin-readiness      — enriched with evaluation_policy + horizon_context")
