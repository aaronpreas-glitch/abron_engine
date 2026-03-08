#!/usr/bin/env python3
"""
Patch main.py to add:
1. /api/perps/risk endpoint — shows perp-specific risk state (streak, DD, indicator quality)
2. /api/perps/indicator-insights endpoint — exposes _analyze_indicator_patterns results
"""

MAIN_PY = "/root/memecoin_engine/dashboard/backend/main.py"

with open(MAIN_PY, "r") as f:
    code = f.read()

RISK_ENDPOINTS = '''

@app.get("/api/perps/risk")
async def perps_risk_status():
    """Return perp-specific risk metrics: losing streak, daily DD, indicator quality."""
    import sqlite3
    db_path = os.path.join(_engine_root(), "data_storage", "engine.db")
    try:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            # Recent trades for streak
            recent = conn.execute("""
                SELECT pnl_pct FROM perp_positions
                WHERE status='CLOSED' AND pnl_pct IS NOT NULL
                ORDER BY closed_ts_utc DESC LIMIT 10
            """).fetchall()
            # Daily PnL
            from datetime import datetime, timezone
            today = datetime.now(timezone.utc).strftime("%Y-%m-%dT00:00:00")
            daily = conn.execute("""
                SELECT COALESCE(SUM(pnl_usd), 0) as total_pnl,
                       COUNT(*) as trade_count,
                       SUM(CASE WHEN pnl_pct > 0 THEN 1 ELSE 0 END) as wins
                FROM perp_positions
                WHERE status='CLOSED' AND closed_ts_utc >= ?
            """, (today,)).fetchone()

        # Compute streak
        streak = 0
        for r in recent:
            if (r["pnl_pct"] or 0) < 0:
                streak += 1
            else:
                break

        daily_pnl = float(daily["total_pnl"] or 0)
        daily_trades = int(daily["trade_count"] or 0)
        daily_wins = int(daily["wins"] or 0)

        # Risk level
        if streak >= 5:
            level = "CRITICAL"
            size_mult = 0.3
        elif streak >= 3:
            level = "ELEVATED"
            size_mult = 0.5
        elif daily_pnl < -5:  # >$5 loss today
            level = "CAUTIOUS"
            size_mult = 0.7
        else:
            level = "NORMAL"
            size_mult = 1.0

        return {
            "losing_streak": streak,
            "daily_pnl_usd": round(daily_pnl, 2),
            "daily_trades": daily_trades,
            "daily_wins": daily_wins,
            "daily_wr": round(daily_wins / daily_trades * 100, 1) if daily_trades > 0 else 0,
            "risk_level": level,
            "size_multiplier": size_mult,
        }
    except Exception as e:
        return {"error": str(e), "risk_level": "UNKNOWN", "losing_streak": 0}


@app.get("/api/perps/indicator-insights")
async def perps_indicator_insights():
    """Return indicator pattern analysis from auto_tune."""
    import sys, json
    root = _engine_root()
    if root not in sys.path:
        sys.path.insert(0, root)
    try:
        # Read the perp_profiles.json which has indicator data
        profiles_path = os.path.join(root, "data_storage", "perp_profiles.json")
        if os.path.exists(profiles_path):
            with open(profiles_path) as f:
                profiles = json.load(f)
            return profiles
        return {"error": "No profiles yet — waiting for trades to close"}
    except Exception as e:
        return {"error": str(e)}

'''

if '/api/perps/risk' not in code:
    # Insert before the last WebSocket handler or at end
    if '/api/perps/post-exit' in code:
        # Find end of post-exit endpoint function
        idx = code.find('/api/perps/post-exit')
        next_at = code.find('\n@app.', idx + 20)
        if next_at > 0:
            code = code[:next_at] + RISK_ENDPOINTS + code[next_at:]
        else:
            code += RISK_ENDPOINTS
    else:
        code += RISK_ENDPOINTS
    print("✓ Added /api/perps/risk and /api/perps/indicator-insights endpoints")
else:
    print("⚠ Endpoints already exist")

with open(MAIN_PY, "w") as f:
    f.write(code)

print("\n✅ Risk API endpoints added")
