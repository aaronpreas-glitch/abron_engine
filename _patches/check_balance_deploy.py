"""Check wallet balance + Jupiter positions, then deploy remaining to tier positions."""
import sys, os, requests, json, time
sys.path.insert(0, '/root/memecoin_engine')
os.chdir('/root/memecoin_engine')

from utils.tier_manager import open_tier_position
from utils.jupiter_perps_trade import get_wallet_address

WALLET = get_wallet_address()
RPC    = os.environ.get('SOLANA_RPC_URL', 'https://api.mainnet-beta.solana.com')
print(f"Wallet: {WALLET[:8]}...{WALLET[-4:]}")

# ── 1. SOL balance via raw RPC ─────────────────────────────────────────────────
rpc_resp = requests.post(
    RPC,
    json={"jsonrpc": "2.0", "id": 1, "method": "getBalance", "params": [WALLET]},
    timeout=10
).json()
lamports = rpc_resp["result"]["value"]
sol_bal = lamports / 1e9
SOL_PRICE = 84.0  # approximate; actual price fetched below
print(f"SOL balance: {sol_bal:.4f} SOL")

# Try to get live SOL price from CoinGecko
try:
    cg = requests.get(
        "https://api.coingecko.com/api/v3/simple/price",
        params={"ids": "solana", "vs_currencies": "usd"},
        timeout=5
    ).json()
    SOL_PRICE = cg["solana"]["usd"]
    print(f"SOL price: ${SOL_PRICE:.2f}")
except Exception:
    print(f"SOL price: ${SOL_PRICE:.2f} (fallback)")

usd_bal = sol_bal * SOL_PRICE
print(f"Wallet USD value: ${usd_bal:.2f}")

# ── 2. Jupiter on-chain positions ──────────────────────────────────────────────
jup_resp = requests.get(
    "https://perps-api.jup.ag/v2/positions",
    params={"walletAddress": WALLET},
    timeout=10
)
positions = jup_resp.json().get("dataList", [])
print(f"\nJupiter on-chain positions: {len(positions)}")
total_col = 0
for p in positions:
    sym = p.get("marketSymbol", "?")
    col = float(p.get("collateralUsd", 0))
    sz  = float(p.get("sizeUsd", 0))
    pnl = float(p.get("unrealizedPnlUsd", 0))
    total_col += col
    print(f"  {sym}: col=${col:.2f}, size=${sz:.0f}, PnL=${pnl:+.2f}")
print(f"Total on-chain collateral: ${total_col:.2f}")

# ── 3. Deployment plan ─────────────────────────────────────────────────────────
KEEP_BUFFER = 8.0   # keep $8 for fees / emergencies
deployable  = max(0, usd_bal - KEEP_BUFFER)
print(f"\nDeployable (after ${KEEP_BUFFER} buffer): ${deployable:.2f}")

# Tier collateral sizes from .env
col_3x  = float(os.environ.get('TIER_3X_NOTIONAL', 150)) / float(os.environ.get('TIER_3X_LEVERAGE', 3))   # $50
col_5x  = float(os.environ.get('TIER_5X_NOTIONAL', 100)) / float(os.environ.get('TIER_5X_LEVERAGE', 5))   # $20
col_10x = float(os.environ.get('TIER_10X_NOTIONAL', 100)) / float(os.environ.get('TIER_10X_LEVERAGE', 10)) # $10

print(f"Tier collateral: 3x=${col_3x:.0f}, 5x={col_5x:.0f}, 10x={col_10x:.0f}")

# Greedy fill: prioritise 3x (anchor), then 5x (mid), then 10x (profit)
plan = []
remaining = deployable

# How many 3x can we open?
n3x = min(2, int(remaining // col_3x))   # cap at 2 (don't over-concentrate on one market)
remaining -= n3x * col_3x
plan.extend(['3x'] * n3x)

# How many 5x?
n5x = min(4, int(remaining // col_5x))
remaining -= n5x * col_5x
plan.extend(['5x'] * n5x)

# How many 10x?
n10x = int(remaining // col_10x)
remaining -= n10x * col_10x
plan.extend(['10x'] * n10x)

print(f"\nDeployment plan:")
print(f"  3x (SOL, diamond): {n3x}× ${col_3x:.0f} = ${n3x*col_3x:.0f}")
print(f"  5x (BTC, TP 20%): {n5x}× ${col_5x:.0f} = ${n5x*col_5x:.0f}")
print(f"  10x (ETH, TP 10%): {n10x}× ${col_10x:.0f} = ${n10x*col_10x:.0f}")
total_plan = n3x*col_3x + n5x*col_5x + n10x*col_10x
print(f"  Total deploy: ${total_plan:.0f} | Residual: ${remaining:.2f}")
print(f"  Positions to open: {len(plan)}")

if not plan:
    print("Nothing to open — balance too low.")
    sys.exit(0)

# ── 4. Execute ─────────────────────────────────────────────────────────────────
print("\n--- OPENING POSITIONS ---")
results = []
for tier in plan:
    print(f"Opening {tier}...", end=" ", flush=True)
    try:
        r = open_tier_position(tier)
        ok = r.get('success', False)
        tx = r.get('tx_sig', r.get('error', 'N/A'))
        print("OK  tx=" + str(tx)[:20] if ok else "FAIL  " + str(tx)[:60])
        results.append((tier, ok, str(tx)[:20]))
    except Exception as ex:
        print(f"EXCEPTION: {ex}")
        results.append((tier, False, str(ex)[:60]))
    time.sleep(2)  # small delay between opens

print("\n--- SUMMARY ---")
ok_count = sum(1 for _, ok, _ in results if ok)
print(f"{ok_count}/{len(results)} positions opened successfully")
for tier, ok, info in results:
    status = "✅" if ok else "❌"
    print(f"  {status} {tier}: {info}")
