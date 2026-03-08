#!/usr/bin/env python3
"""
Patch 52 — Gate Stats API Endpoint

Applies to: /root/memecoin_engine/dashboard/backend/main.py

Adds GET /api/brain/gate-stats:
  Returns 24h acceptance rate, top skip reasons, risk blocks.
  Inserted between /api/brain/bull-readiness and /api/journal/learnings.
"""

import pathlib

TARGET = pathlib.Path("/root/memecoin_engine/dashboard/backend/main.py")
assert TARGET.exists(), f"Target not found: {TARGET}"
content = TARGET.read_text()

ANCHOR = '@app.get("/api/journal/learnings")'
assert ANCHOR in content, f"FAIL: anchor '{ANCHOR}' not found"

NEW_ENDPOINT = '''@app.get("/api/brain/gate-stats")
async def brain_gate_stats(request: Request):
    """
    Returns quality-gate + risk-gate stats for the last 24h:
      - signals_seen  = skipped + accepted + risk_blocked
      - accepted_rate = accepted / signals_seen  (%)
      - top_fail_reasons from skipped_signals_log
      - risk_blocks from risk_block_log (if table exists)
    """
    _auth(request)
    since_24h = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    db_path = BASE_DIR / "data_storage" / "engine.db"
    result: dict = {
        "window_h": 24,
        "skipped": 0,
        "accepted": 0,
        "blocked_by_risk": 0,
        "signals_seen": 0,
        "accepted_rate": 0.0,
        "top_fail_reasons": [],
        "risk_blocks": [],
        "ts_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    try:
        with sqlite3.connect(str(db_path)) as c:
            c.row_factory = sqlite3.Row

            # Skipped count + top reasons (last 24h)
            try:
                row = c.execute(
                    "SELECT COUNT(*) FROM skipped_signals_log WHERE ts_utc >= ?", (since_24h,)
                ).fetchone()
                result["skipped"] = int(row[0] or 0)
            except Exception:
                pass

            try:
                rows = c.execute("""
                    SELECT skip_reason, COUNT(*) AS cnt
                    FROM skipped_signals_log
                    WHERE ts_utc >= ?
                    GROUP BY skip_reason
                    ORDER BY cnt DESC
                    LIMIT 8
                """, (since_24h,)).fetchall()
                result["top_fail_reasons"] = [
                    {"reason": r["skip_reason"], "count": int(r["cnt"])} for r in rows
                ]
            except Exception:
                pass

            # Accepted count = positions opened in last 24h
            try:
                row = c.execute(
                    "SELECT COUNT(*) FROM perp_positions WHERE opened_ts_utc >= ?", (since_24h,)
                ).fetchone()
                result["accepted"] = int(row[0] or 0)
            except Exception:
                pass

            # Risk blocks from risk_block_log (optional table — created by Patch 51)
            try:
                row = c.execute(
                    "SELECT COUNT(*) FROM risk_block_log WHERE ts_utc >= ?", (since_24h,)
                ).fetchone()
                result["blocked_by_risk"] = int(row[0] or 0)
            except Exception:
                pass  # table doesn't exist yet

            try:
                rows = c.execute("""
                    SELECT reason, COUNT(*) AS cnt
                    FROM risk_block_log
                    WHERE ts_utc >= ?
                    GROUP BY reason
                    ORDER BY cnt DESC
                    LIMIT 5
                """, (since_24h,)).fetchall()
                result["risk_blocks"] = [
                    {"reason": r["reason"], "count": int(r["cnt"])} for r in rows
                ]
            except Exception:
                pass

            # Compute derived metrics
            total = result["skipped"] + result["accepted"] + result["blocked_by_risk"]
            result["signals_seen"] = total
            if total > 0:
                result["accepted_rate"] = round(result["accepted"] / total * 100, 2)

    except Exception as _e:
        log.warning("brain_gate_stats error: %s", _e)

    return JSONResponse(result)


@app.get("/api/journal/learnings")'''

content = content.replace(ANCHOR, NEW_ENDPOINT, 1)

TARGET.write_text(content)
print("Patch 52 applied — /api/brain/gate-stats endpoint added.")
