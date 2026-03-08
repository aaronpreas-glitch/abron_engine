"""
Patch 79d — fix trade price field selection + add wallet address
- BUY:  from=SOL, to=BONK  → token price = price_to_in_usd
- SELL: from=BONK, to=SOL  → token price = price_from_in_usd
- Also expose tx_from_address as wallet (shortened in frontend)
"""
import subprocess, sys
from pathlib import Path

ROOT = Path("/root/memecoin_engine")
MAIN = ROOT / "dashboard/backend/main.py"

main_text = MAIN.read_text()

OLD_TRADE_BUILD = '''        trades = []
        for item in items[:40]:
            attr = item.get("attributes", {})
            # price: use from_usd first, fallback to to_usd
            price = float(attr.get("price_from_in_usd") or attr.get("price_to_in_usd") or 0)
            trades.append({
                "time":       attr.get("block_timestamp"),
                "kind":       attr.get("kind", "unknown"),
                "price_usd":  price,
                "volume_usd": float(attr.get("volume_in_usd") or 0),
                "tx_hash":    attr.get("tx_hash", ""),
            })'''

NEW_TRADE_BUILD = '''        trades = []
        for item in items[:40]:
            attr = item.get("attributes", {})
            kind = attr.get("kind", "unknown")
            # BUY:  buyer gives SOL (from), gets token (to) → token price = price_to_in_usd
            # SELL: seller gives token (from), gets SOL (to) → token price = price_from_in_usd
            if kind == "buy":
                price = float(attr.get("price_to_in_usd") or 0)
            else:
                price = float(attr.get("price_from_in_usd") or 0)
            wallet = attr.get("tx_from_address", "")
            trades.append({
                "time":       attr.get("block_timestamp"),
                "kind":       kind,
                "price_usd":  price,
                "volume_usd": float(attr.get("volume_in_usd") or 0),
                "tx_hash":    attr.get("tx_hash", ""),
                "wallet":     wallet,
            })'''

assert OLD_TRADE_BUILD in main_text, "OLD_TRADE_BUILD not found"
main_text = main_text.replace(OLD_TRADE_BUILD, NEW_TRADE_BUILD)
print("✓ trade price logic fixed + wallet added")

MAIN.write_text(main_text)

r = subprocess.run([sys.executable, "-m", "py_compile", str(MAIN)], capture_output=True, text=True)
if r.returncode != 0:
    print("✗ compile error:", r.stderr); sys.exit(1)
print("✓ main.py compiles OK")
