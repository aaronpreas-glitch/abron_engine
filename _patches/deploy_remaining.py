"""Deploy remaining ~$50 as 5x BTC positions after 2nd 3x SOL failed."""
import sys, os, requests, time
sys.path.insert(0, '/root/memecoin_engine')
os.chdir('/root/memecoin_engine')

from utils.tier_manager import open_tier_position
from utils.jupiter_perps_trade import get_wallet_address

WALLET = get_wallet_address()
RPC = os.environ.get('SOLANA_RPC_URL', 'https://api.mainnet-beta.solana.com')

# Check remaining balance
rpc_resp = requests.post(
    RPC,
    json={"jsonrpc": "2.0", "id": 1, "method": "getBalance", "params": [WALLET]},
    timeout=10
).json()
sol_bal = rpc_resp["result"]["value"] / 1e9

try:
    cg = requests.get(
        "https://api.coingecko.com/api/v3/simple/price",
        params={"ids": "solana", "vs_currencies": "usd"},
        timeout=5
    ).json()
    SOL_PRICE = cg["solana"]["usd"]
except Exception:
    SOL_PRICE = 84.0

usd_bal = sol_bal * SOL_PRICE
print(f"Remaining SOL: {sol_bal:.4f} (~${usd_bal:.2f})")

# Deploy remaining as 5x BTC ($20 col each) + leftover as 10x ETH ($10 col)
KEEP = 8.0
deployable = max(0, usd_bal - KEEP)
print(f"Deployable: ${deployable:.2f}")

plan = []
remaining = deployable
n5x = int(remaining // 20)
remaining -= n5x * 20
plan.extend(['5x'] * n5x)
n10x = int(remaining // 10)
plan.extend(['10x'] * n10x)

print(f"Plan: {n5x}× 5x BTC + {n10x}× 10x ETH")

if not plan:
    print("Nothing to open.")
    sys.exit(0)

for tier in plan:
    print(f"Opening {tier}...", end=" ", flush=True)
    try:
        r = open_tier_position(tier)
        ok = r.get('success', False)
        tx = str(r.get('tx_sig', r.get('error', 'N/A')))
        print("OK  " + tx[:30] if ok else "FAIL  " + tx[:80])
    except Exception as ex:
        print(f"EXCEPTION: {ex}")
    time.sleep(2)

print("\nDone.")
