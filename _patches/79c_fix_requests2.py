"""
Patch 79c — fix remaining bare requests.get calls inside endpoint bodies
(chart_data and recent_trades both make direct requests.get calls)
"""
import subprocess, sys
from pathlib import Path

ROOT = Path("/root/memecoin_engine")
MAIN = ROOT / "dashboard/backend/main.py"

main_text = MAIN.read_text()

# ── Fix chart-data endpoint body ─────────────────────────────────────────────
OLD_CHART_FETCH = '''    try:
        r = requests.get(
            f"https://api.geckoterminal.com/api/v2/networks/solana/pools/{pool}/ohlcv/{timeframe}",
            params={"aggregate": agg, "limit": 300, "currency": "usd"},
            headers={"Accept": "application/json"},
            timeout=12,
        )'''
NEW_CHART_FETCH = '''    try:
        import requests as _req
        r = _req.get(
            f"https://api.geckoterminal.com/api/v2/networks/solana/pools/{pool}/ohlcv/{timeframe}",
            params={"aggregate": agg, "limit": 300, "currency": "usd"},
            headers={"Accept": "application/json"},
            timeout=12,
        )'''

assert OLD_CHART_FETCH in main_text, "OLD_CHART_FETCH not found"
main_text = main_text.replace(OLD_CHART_FETCH, NEW_CHART_FETCH)
print("✓ token_chart_data requests.get fixed")

# ── Fix recent-trades endpoint body ──────────────────────────────────────────
OLD_TRADES_FETCH = '''    try:
        r = requests.get(
            f"https://api.geckoterminal.com/api/v2/networks/solana/pools/{pool}/trades",
            params={"trade_volume_in_usd_greater_than": 0},
            headers={"Accept": "application/json"},
            timeout=12,
        )'''
NEW_TRADES_FETCH = '''    try:
        import requests as _req
        r = _req.get(
            f"https://api.geckoterminal.com/api/v2/networks/solana/pools/{pool}/trades",
            params={"trade_volume_in_usd_greater_than": 0},
            headers={"Accept": "application/json"},
            timeout=12,
        )'''

assert OLD_TRADES_FETCH in main_text, "OLD_TRADES_FETCH not found"
main_text = main_text.replace(OLD_TRADES_FETCH, NEW_TRADES_FETCH)
print("✓ token_recent_trades requests.get fixed")

MAIN.write_text(main_text)

r = subprocess.run([sys.executable, "-m", "py_compile", str(MAIN)], capture_output=True, text=True)
if r.returncode != 0:
    print("✗ compile error:", r.stderr); sys.exit(1)
print("✓ main.py compiles OK")
