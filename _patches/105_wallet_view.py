"""
Patch 105 — Wallet View
Injects GET /api/wallet/positions endpoint into dashboard backend.
Reads live Jupiter Perp positions for the configured wallet (read-only, no key needed).
"""
import sys
from pathlib import Path

BACKEND = Path("/root/memecoin_engine/dashboard/backend/main.py")
text = BACKEND.read_text()

ENDPOINT = r'''

# ── Wallet View — Live Jupiter Perp Positions (Patch 105) ────────────────────

@app.get("/api/wallet/positions")
async def wallet_positions_ep(_: str = Depends(get_current_user)):
    """Read-only: fetch all open Jupiter Perp positions for the configured wallet."""
    import requests as _req

    WALLET = "6YeATB75AyJKM8ujv3qQXtzCKrACmQgzpgf4EmjihhF4"

    # Mint address → human-readable symbol
    _MINT = {
        "So11111111111111111111111111111111111111112": "SOL",
        "9n4nbM75f5Ui33ZbPYXn59EwSgE8CGsHtAeTH5YFeJ9E": "BTC",
        "7vfCXTUXx5WJV5JADk17DUJ4ksgau7utNKj4b963voxs": "ETH",
        "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v": "USDC",
        "mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So":  "mSOL",
    }

    def _f(val, divisor=1):
        """Safe float parse, divide by divisor."""
        try:
            return round(float(val) / divisor, 6)
        except Exception:
            return 0.0

    try:
        r = _req.get(
            "https://perps-api.jup.ag/v1/positions",
            params={"walletAddress": WALLET},
            timeout=10,
        )
        if r.status_code != 200:
            return {"wallet": WALLET, "positions": [], "error": f"Jupiter API {r.status_code}"}

        raw_list = r.json().get("dataList") or r.json().get("positions") or []

        positions = []
        for p in raw_list:
            mint = p.get("marketMint", "")
            symbol = _MINT.get(mint, mint[:6] + "...")
            market = f"{symbol}-PERP"
            side = str(p.get("side") or "long").upper()

            entry_price    = _f(p.get("entryPrice"))
            mark_price     = _f(p.get("markPrice"))
            leverage       = _f(p.get("leverage"))
            liq_price      = _f(p.get("liquidationPrice"))

            # collateralUsd and sizeUsdDelta are in micro-USD (divide by 1e6)
            collateral_usd = _f(p.get("collateralUsd"), 1_000_000)
            size_usd       = _f(p.get("sizeUsdDelta"),  1_000_000)

            # pnlAfterFeesUsd is already a human-readable USD string
            pnl_usd  = _f(p.get("pnlAfterFeesUsd"))
            pnl_pct  = _f(p.get("pnlChangePctAfterFees"))

            # Current position value (collateral + unrealised PnL)
            value_usd = _f(p.get("value"))

            # Fees info
            total_fees_usd = _f(p.get("totalFeesUsd"))

            # Liq proximity warning — within 15% of mark price
            liq_near = (
                liq_price > 0 and mark_price > 0 and
                abs(mark_price - liq_price) / mark_price < 0.15
            )

            positions.append({
                "market":         market,
                "symbol":         symbol,
                "side":           side,
                "entry_price":    entry_price,
                "mark_price":     mark_price,
                "size_usd":       size_usd,
                "collateral_usd": collateral_usd,
                "value_usd":      value_usd,
                "pnl_usd":        pnl_usd,
                "pnl_pct":        pnl_pct,
                "leverage":       leverage,
                "liq_price":      liq_price,
                "liq_near":       liq_near,
                "total_fees_usd": total_fees_usd,
                "position_pubkey": p.get("positionPubkey", ""),
            })

        return {"wallet": WALLET, "positions": positions, "error": None}

    except Exception as exc:
        log.warning("wallet_positions_ep error: %s", exc)
        return {"wallet": WALLET, "positions": [], "error": str(exc)}

'''

MARKER = '@app.get("/api/journal/learnings")'
if MARKER not in text:
    print("❌ Marker not found")
    sys.exit(1)

text = text.replace(MARKER, ENDPOINT + "\n" + MARKER, 1)
BACKEND.write_text(text)

import py_compile
try:
    py_compile.compile(str(BACKEND), doraise=True)
    print("✅ Syntax OK — /api/wallet/positions endpoint injected")
except py_compile.PyCompileError as e:
    print(f"❌ Syntax error: {e}")
    sys.exit(1)
