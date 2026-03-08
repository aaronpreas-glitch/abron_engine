"""
Patch 107 — SOL Spot Balance
Adds native SOL balance to /api/wallet/positions response.
One Solana mainnet RPC call (getBalance) — no key, no rate limits.
"""
import sys, py_compile
from pathlib import Path

BACKEND = Path("/root/memecoin_engine/dashboard/backend/main.py")
text = BACKEND.read_text()

# Find the existing return statement in wallet_positions_ep and replace it
OLD_RETURN = 'return {"wallet": WALLET, "positions": positions, "error": None}'

NEW_RETURN = '''    # ── SOL spot balance (Patch 107) ─────────────────────────────────────────
    def _get_sol_balance():
        r = _req.post(
            "https://api.mainnet-beta.solana.com",
            json={"jsonrpc": "2.0", "id": 1, "method": "getBalance", "params": [WALLET]},
            timeout=5,
        )
        return r.json().get("result", {}).get("value", 0) / 1_000_000_000

    try:
        sol_balance = await asyncio.to_thread(_get_sol_balance)
    except Exception:
        sol_balance = None

    return {"wallet": WALLET, "positions": positions, "sol_balance": sol_balance, "error": None}'''

if OLD_RETURN not in text:
    print("❌ Marker not found — old return statement missing")
    sys.exit(1)

text = text.replace(OLD_RETURN, NEW_RETURN, 1)
BACKEND.write_text(text)

try:
    py_compile.compile(str(BACKEND), doraise=True)
    print("✅ Syntax OK — sol_balance added to /api/wallet/positions")
except py_compile.PyCompileError as e:
    print(f"❌ {e}")
    sys.exit(1)
