#!/usr/bin/env bash
# status.sh — session start snapshot for memecoin_engine
# Usage: bash status.sh
# Read-only. No service restarts, no writes.

set -euo pipefail

VPS="root@68.183.148.183"
KEY="$HOME/.ssh/memecoin_deploy"

ssh -i "$KEY" -T "$VPS" python3 << 'PYEOF'
import urllib.request, json, sqlite3, time
from collections import Counter
from datetime import datetime, timezone

DB  = "/root/memecoin_engine/data_storage/engine.db"
URL = "http://localhost:8000"
PWD = "HArden978ab"

def hago(s):
    if s is None: return "?"
    s = abs(s)
    if s < 60:   return f"{s:.0f}s"
    if s < 3600: return f"{s/60:.0f}m"
    return f"{s/3600:.1f}h"

def ago_ts(ts):
    return hago(time.time() - float(ts)) if ts else "?"

# ── Auth ─────────────────────────────────────────────────────────────────────
token = ""
try:
    req = urllib.request.Request(
        f"{URL}/api/auth/login",
        data=json.dumps({"password": PWD}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    token = json.loads(urllib.request.urlopen(req, timeout=5).read())["token"]
except Exception:
    pass

def api(path):
    try:
        req = urllib.request.Request(
            f"{URL}{path}", headers={"Authorization": f"Bearer {token}"}
        )
        return json.loads(urllib.request.urlopen(req, timeout=5).read())
    except Exception:
        return {}

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row

print(f"=== memecoin_engine · {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC ===")

# ── MARKET ───────────────────────────────────────────────────────────────────
print("\nMARKET")
row = conn.execute("SELECT value FROM kv_store WHERE key='shared_fear_greed'").fetchone()
if row:
    fg  = json.loads(row[0])
    fav = "favorable" if fg.get("favorable") else "EXTREME FEAR — deploy paused"
    print(f"  F&G  {fg.get('value','?'):>3}  {fg.get('label','?'):<20} [{fav}]  cached {ago_ts(fg.get('_ts'))} ago")
else:
    print("  F&G  (not cached)")

# ── AGENTS ───────────────────────────────────────────────────────────────────
print("\nAGENTS")
# Stale thresholds match each agent's expected interval (from INVARIANTS.md)
WATCH = {
    "memecoin_scan":   600,    # runs every 5 min  — stale at >10 min
    "data_integrity":  600,    # runs every 5 min  — stale at >10 min
    "research":        18000,  # runs every 4h     — stale at >5h
    "spot_monitor":    600,    # runs every ~1 min — stale at >10 min
}
orch = api("/api/orchestrator/status")
if orch.get("agents"):
    for a in orch["agents"]:
        if a["name"] in WATCH:
            secs      = a.get("last_beat_ago_s")
            health    = a.get("health", "?")
            threshold = WATCH[a["name"]]
            stale     = secs is not None and secs > threshold
            flag      = "  ! STALE" if stale else ""
            print(f"  {a['name']:<22} {hago(secs):<8} [{health}]{flag}")
else:
    print("  (orchestrator unavailable — check: systemctl status memecoin-dashboard)")

# ── POSITIONS ────────────────────────────────────────────────────────────────
print("\nPOSITIONS")
op = conn.execute("SELECT COUNT(*) FROM perp_positions   WHERE status='OPEN'").fetchone()[0]
om = conn.execute("SELECT COUNT(*) FROM memecoin_trades  WHERE status='OPEN'").fetchone()[0]
print(f"  {op} perp OPEN  |  {om} memecoin OPEN")

# ── OUTCOMES ─────────────────────────────────────────────────────────────────
print("\nOUTCOMES")
total   = conn.execute("SELECT COUNT(*) FROM memecoin_signal_outcomes WHERE status='COMPLETE'").fetchone()[0]
pending = conn.execute("SELECT COUNT(*) FROM memecoin_signal_outcomes WHERE status='PENDING'").fetchone()[0]
print(f"  {total} COMPLETE  |  {pending} PENDING")

sym_rows = conn.execute("""
    SELECT COUNT(*) AS n,
           ROUND(AVG(return_24h_pct), 1)                                                       AS avg,
           ROUND(SUM(CASE WHEN return_24h_pct >= 10 THEN 1.0 ELSE 0.0 END)*100.0/COUNT(*), 1) AS wr
    FROM memecoin_signal_outcomes
    WHERE status = 'COMPLETE' AND return_24h_pct IS NOT NULL
    GROUP BY symbol
""").fetchall()
if sym_rows:
    tc = Counter()
    for r in sym_rows:
        n, avg, wr = r["n"], r["avg"] or 0, r["wr"] or 0
        if   n >= 15 and wr >= 50 and avg >= 5: tc["PROVEN_POSITIVE"] += 1
        elif n >= 15 and avg < -10:             tc["PROVEN_NEGATIVE"] += 1
        elif n >= 8:                            tc["TESTED_NEUTRAL"]  += 1
        else:                                   tc["UNPROVEN"]        += 1
    print("  Perf tiers:  " + "  ".join(f"{k}={v}" for k, v in sorted(tc.items())))

for col, lbl in [("trust_label", "Trust"), ("triage_state", "Triage")]:
    lrows = conn.execute(f"""
        SELECT {col} AS val, COUNT(*) AS n
        FROM memecoin_signal_outcomes
        WHERE {col} IS NOT NULL
        GROUP BY {col} ORDER BY n DESC
    """).fetchall()
    if lrows:
        print(f"  {lbl:<8}     " + "  ".join(f"{r['val']}={r['n']}" for r in lrows))

# ── VALIDATION GATE ───────────────────────────────────────────────────────────
print("\nVALIDATION GATE")
need = max(0, 20 - total)
if need > 0:
    print(f"  {total}/20 COMPLETE outcomes — need {need} more before any verdict can be earned")
else:
    print(f"  {total} COMPLETE — threshold met.  Check /api/memecoins/intel-validation for field coverage.")

conn.close()
PYEOF
