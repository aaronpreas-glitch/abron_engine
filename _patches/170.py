"""
Patch 170 — 24h-aware readiness framework and promotion discipline.

Not a live-execution patch. No trading behaviour changes.
Read-only endpoint enrichment only.

Changes:
  - dashboard/backend/main.py
      GET /api/brain/memecoin-readiness (full rewrite — same URL)
        A. strategy_horizon: classifies the strategy as "4h" | "24h" | "unclear"
             based on the observed performance gap between horizons.
             24h classified when: gap >5pp AND exp_24h > 0.

        B. blocker_categories: {market, data, edge}
             market — F&G, risk mode (external — cannot be scanner-fixed)
             data   — sample concentration, insufficient trades
             edge   — expectancy below gate at recommended horizon

        C. promotion_category: "MARKET_GATED" | "EDGE_GATED" | "DATA_GATED"
                              | "MULTI_GATED" | "CLEAR"
             Single-word classification of what is holding back promotion.

        D. promotion_scenario: hypothetical — if market gate cleared today,
             would the arm be promotable at the recommended horizon?
             Includes missing_confirmation list.

        E. Primary edge gate now tracks the recommended horizon (not blindly 4h).
             If strategy_horizon="24h", the expectancy_24h gate is hard.
             4h expectancy is shown as informational only.

        F. gate_detail enriched with category field per gate.

        G. horizon_context enriched with is_primary_gate flag + payoff_ratio.

        H. Verdict structure unchanged (READY / WATCH / NOT_READY).
             Market gates are still hard regardless of horizon.

Run: python3 _patches/170.py
"""
import os
import py_compile
import sys

ENGINE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FILES_TO_CHECK = [
    "dashboard/backend/main.py",
]


def syntax_check(engine_root: str, files: list) -> None:
    print("\n[170] Syntax check:")
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
    print("\n[170] Endpoint smoke test:")
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

    # Required top-level keys (Patch 170 additions)
    required_keys = [
        "verdict",
        "strategy_horizon",
        "horizon_rationale",
        "promotion_category",
        "blocker_categories",
        "active_blockers",
        "promotion_scenario",
        "horizon_context",
        "gate_detail",
        "metrics",
    ]
    # Required nested keys
    required_scenario_keys = [
        "would_promote_at_recommended_horizon",
        "would_promote_at_24h",
        "would_promote_at_4h",
        "recommended_horizon",
        "missing_confirmation",
        "summary",
    ]
    required_category_keys = ["market", "data", "edge"]

    path = "/api/brain/memecoin-readiness"
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
    missing = [k for k in required_keys if k not in data]
    if missing:
        print(f"  ❌ {path} — missing top-level keys: {missing}")
        all_ok = False

    ps = data.get("promotion_scenario", {})
    missing_ps = [k for k in required_scenario_keys if k not in ps]
    if missing_ps:
        print(f"  ❌ promotion_scenario — missing keys: {missing_ps}")
        all_ok = False

    bc = data.get("blocker_categories", {})
    missing_bc = [k for k in required_category_keys if k not in bc]
    if missing_bc:
        print(f"  ❌ blocker_categories — missing keys: {missing_bc}")
        all_ok = False

    if all_ok:
        print(f"  ✅ {path} — OK")
        print(f"     verdict:            {data.get('verdict')}")
        print(f"     strategy_horizon:   {data.get('strategy_horizon')}")
        print(f"     promotion_category: {data.get('promotion_category')}")
        horizon_ctx = data.get("horizon_context", {})
        e4  = horizon_ctx.get("4h",  {}).get("expectancy_pct")
        e24 = horizon_ctx.get("24h", {}).get("expectancy_pct")
        print(f"     4h exp:  {e4:+.2f}%  |  24h exp: {e24:+.2f}%" if e4 is not None and e24 is not None else "")
        mkt = bc.get("market", [])
        edg = bc.get("edge", [])
        dat = bc.get("data", [])
        print(f"     market blockers: {len(mkt)} | edge blockers: {len(edg)} | data warnings: {len(dat)}")
        print(f"     scenario: {ps.get('summary', '')[:100]}")
    else:
        print(f"     Response snippet: {r.stdout[:500]}")
        sys.exit(1)


if __name__ == "__main__":
    syntax_check(ENGINE_ROOT, FILES_TO_CHECK)
    smoke_endpoints(ENGINE_ROOT)
    print("\n🚀 Patch 170 complete.")
    print("   Changes active on next service restart:")
    print("   • GET /api/brain/memecoin-readiness — 24h-aware verdict, categorised blockers")
    print("     strategy_horizon / promotion_category / blocker_categories / promotion_scenario")
