"""
Patch 171 — Live-pilot readiness framework for the memecoin scanner arm.

Not a live-execution patch. No trading behaviour changes.
Backend analytical endpoint only.

Changes:
  - dashboard/backend/main.py
      A. GET /api/brain/memecoin-pilot-readiness
           Conservative pilot readiness with stricter gates than paper readiness.

           Hard gates (NOT_PILOT_READY if any fail):
             market   — F&G >35 (paper is >25), risk_mode == NORMAL
             edge     — 24h expectancy ≥+2%, win rate ≥55%, payoff ≥1.0x
             sample   — ≥20 post-cooldown (post-2026-03-07) clean 24h trades

           Soft gates (PILOT_WATCH if hard gates pass but soft fail):
             rolling  — both last-10 and prior-10 24h windows positive
             fg_opt   — F&G >50 (optimal entry timing)

           Verdict scale: NOT_PILOT_READY | PILOT_WATCH | PILOT_READY

      B. pilot_constraints block (decision support — not enforced):
             max_concurrent_live_positions: 1
             max_capital_per_trade_sol: 0.1
             max_daily_loss_usd: 25
             exit_policy: hold_24h_with_stop (-10%)
             excluded_regimes: UNKNOWN
             trial_duration_days: 14 / max_pilot_trades_total: 20

      C. promotion_tiers block — explicit three-tier framework:
             tier_1_paper_readiness  → /api/brain/memecoin-readiness
             tier_2_live_pilot       → /api/brain/memecoin-pilot-readiness
             tier_3_full_live        → (not implemented — described only)

      D. gate_differences_vs_pilot — shows exactly how each gate escalates
         from paper to pilot level.

Run: python3 _patches/171.py
"""
import os
import py_compile
import sys

ENGINE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FILES_TO_CHECK = [
    "dashboard/backend/main.py",
]


def syntax_check(engine_root: str, files: list) -> None:
    print("\n[171] Syntax check:")
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
    print("\n[171] Endpoint smoke test:")
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

    required_top  = ["verdict", "active_blockers", "blocker_categories",
                     "gate_detail", "promotion_tiers", "pilot_constraints",
                     "path_to_pilot_ready", "horizon_context", "metrics"]
    required_tiers = ["tier_1_paper_readiness", "tier_2_live_pilot", "tier_3_full_live"]
    required_cats  = ["market", "edge", "sample"]
    required_pc    = ["max_concurrent_live_positions", "max_capital_per_trade_sol",
                      "max_daily_loss_usd", "enforcement"]

    path = "/api/brain/memecoin-pilot-readiness"
    r = subprocess.run(
        ["curl", "-s"] + hdrs + [f"{base}{path}"],
        capture_output=True, text=True,
    )
    try:
        data = json.loads(r.stdout)
    except Exception as e:
        print(f"  ❌ {path} — JSON parse error: {e}")
        print(f"     Raw: {r.stdout[:400]}")
        sys.exit(1)

    all_ok = True

    missing = [k for k in required_top if k not in data]
    if missing:
        print(f"  ❌ missing top-level keys: {missing}")
        all_ok = False

    tiers = data.get("promotion_tiers", {})
    missing_t = [k for k in required_tiers if k not in tiers]
    if missing_t:
        print(f"  ❌ promotion_tiers missing: {missing_t}")
        all_ok = False

    bc = data.get("blocker_categories", {})
    missing_c = [k for k in required_cats if k not in bc]
    if missing_c:
        print(f"  ❌ blocker_categories missing: {missing_c}")
        all_ok = False

    pc = data.get("pilot_constraints", {})
    missing_pc = [k for k in required_pc if k not in pc]
    if missing_pc:
        print(f"  ❌ pilot_constraints missing: {missing_pc}")
        all_ok = False

    if all_ok:
        print(f"  ✅ {path} — OK")
        print(f"     verdict:              {data.get('verdict')}")
        bc_m = bc.get("market", [])
        bc_e = bc.get("edge",   [])
        bc_s = bc.get("sample", [])
        print(f"     market blockers:      {len(bc_m)}")
        print(f"     edge blockers:        {len(bc_e)}")
        print(f"     sample blockers:      {len(bc_s)}")
        hc = data.get("horizon_context", {})
        print(f"     24h expectancy:       {hc.get('expectancy_24h')}%")
        print(f"     post_cooldown_n:      {data.get('metrics', {}).get('post_cooldown_n')}")
        # Print path to pilot
        ptp = data.get("path_to_pilot_ready", [])
        if ptp:
            print(f"     path to PILOT_READY ({len(ptp)} items):")
            for item in ptp[:3]:
                print(f"       • {item[:90]}")
        t2  = tiers.get("tier_2_live_pilot", {})
        print(f"     tier_2 current_verdict: {t2.get('current_verdict')}")
    else:
        print(f"     Response snippet: {r.stdout[:600]}")
        sys.exit(1)


if __name__ == "__main__":
    syntax_check(ENGINE_ROOT, FILES_TO_CHECK)
    smoke_endpoints(ENGINE_ROOT)
    print("\n🚀 Patch 171 complete.")
    print("   New endpoint:")
    print("   • GET /api/brain/memecoin-pilot-readiness")
    print("     verdict: NOT_PILOT_READY | PILOT_WATCH | PILOT_READY")
    print("     gates: market (F&G>35, NORMAL) + edge (exp≥+2%, WR≥55%, PR≥1.0x)")
    print("            + sample (≥20 post-cooldown 24h trades)")
    print("     includes: promotion_tiers (3-level) + pilot_constraints (decision support)")
