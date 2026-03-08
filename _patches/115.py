"""
Patch 115 — Memecoin Scanner
Deploys:
  - utils/memecoin_scanner.py   (new)
  - utils/memecoin_manager.py   (new)
  - utils/db.py                 (memecoin_trades table added)
  - dashboard/backend/main.py   (3 endpoints + scan loop + monitor hook)

Run from engine root:
  python3 _patches/115.py
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

print("Patch 115 — Memecoin Scanner")
print("=" * 40)

# 1. Run DB migration (creates memecoin_trades table)
print("\n[1] Running DB migration...")
from utils.db import init_db
init_db()
print("    ✓ memecoin_trades table created (or already exists)")

# 2. Verify scanner imports
print("\n[2] Verifying memecoin_scanner...")
from utils.memecoin_scanner import scan_trending_solana, get_cached_signals, cache_signals
print("    ✓ memecoin_scanner imports OK")

# 3. Verify manager imports
print("\n[3] Verifying memecoin_manager...")
from utils.memecoin_manager import memecoin_status, buy_memecoin, sell_memecoin, memecoin_monitor_step
print("    ✓ memecoin_manager imports OK")

# 4. Quick smoke test: status endpoint
print("\n[4] Smoke test: memecoin_status()...")
try:
    result = memecoin_status()
    print(f"    ✓ signals={len(result['signals'])}  positions={len(result['positions'])}  stats={result['stats']}")
except Exception as e:
    print(f"    ✗ ERROR: {e}")

# 5. Quick scan test (live HTTP call)
print("\n[5] Smoke test: scan_trending_solana() (live DexScreener call)...")
try:
    signals = scan_trending_solana(top_n=3)
    if signals:
        for s in signals:
            print(f"    → {s['symbol']:12s}  score={s['score']:5.1f}  1h={s['change_1h']:+.1f}%  vol=${s['volume_24h']:,.0f}")
        cache_signals(signals)
        print(f"    ✓ {len(signals)} signals cached")
    else:
        print("    ⚠ No signals returned (might be off-hours or filters too strict)")
except Exception as e:
    print(f"    ✗ ERROR: {e}")

print("\n" + "=" * 40)
print("Patch 115 complete.")
print("Restart the dashboard service to activate the scan loop:")
print("  systemctl restart memecoin-dashboard")
