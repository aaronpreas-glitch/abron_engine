"""
Wallet positions endpoint — Patches 105, 107.

Routes:
  GET /api/wallet/positions — Jupiter Perp positions + SOL balance for the configured wallet
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends

from auth import get_current_user

log = logging.getLogger("dashboard")
router = APIRouter(prefix="/api/wallet", tags=["wallet"])

_WALLET = "6YeATB75AyJKM8ujv3qQXtzCKrACmQgzpgf4EmjihhF4"

_MINT = {
    "So11111111111111111111111111111111111111112":  "SOL",
    "9n4nbM75f5Ui33ZbPYXn59EwSgE8CGsHtAeTH5YFeJ9E": "BTC",
    "7vfCXTUXx5WJV5JADk17DUJ4ksgau7utNKj4b963voxs": "ETH",
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v": "USDC",
    "mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So":  "mSOL",
}


def _f(val, divisor: float = 1.0) -> float:
    try:
        return round(float(val) / divisor, 6)
    except Exception:
        return 0.0


def _get_positions():
    import requests as _req
    r = _req.get(
        "https://perps-api.jup.ag/v1/positions",
        params={"walletAddress": _WALLET},
        timeout=10,
    )
    if r.status_code != 200:
        return None, f"Jupiter API {r.status_code}"
    raw_list = r.json().get("dataList") or r.json().get("positions") or []
    positions = []
    for p in raw_list:
        mint        = p.get("marketMint", "")
        symbol      = _MINT.get(mint, mint[:6] + "...")
        side        = str(p.get("side") or "long").upper()
        entry_price    = _f(p.get("entryPrice"))
        mark_price     = _f(p.get("markPrice"))
        leverage       = _f(p.get("leverage"))
        liq_price      = _f(p.get("liquidationPrice"))
        collateral_usd = _f(p.get("collateralUsd"), 1_000_000)
        size_usd       = _f(p.get("sizeUsdDelta"),  1_000_000)
        pnl_usd        = _f(p.get("pnlAfterFeesUsd"))
        pnl_pct        = _f(p.get("pnlChangePctAfterFees"))
        value_usd      = _f(p.get("value"))
        total_fees_usd = _f(p.get("totalFeesUsd"))
        liq_near = (
            liq_price > 0 and mark_price > 0 and
            abs(mark_price - liq_price) / mark_price < 0.15
        )
        positions.append({
            "market":          f"{symbol}-PERP",
            "symbol":          symbol,
            "side":            side,
            "entry_price":     entry_price,
            "mark_price":      mark_price,
            "size_usd":        size_usd,
            "collateral_usd":  collateral_usd,
            "value_usd":       value_usd,
            "pnl_usd":         pnl_usd,
            "pnl_pct":         pnl_pct,
            "leverage":        leverage,
            "liq_price":       liq_price,
            "liq_near":        liq_near,
            "total_fees_usd":  total_fees_usd,
            "position_pubkey": p.get("positionPubkey", ""),
        })
    return positions, None


def _get_sol_balance() -> float:
    import requests as _req
    r = _req.post(
        "https://api.mainnet-beta.solana.com",
        json={"jsonrpc": "2.0", "id": 1, "method": "getBalance", "params": [_WALLET]},
        timeout=5,
    )
    return r.json().get("result", {}).get("value", 0) / 1_000_000_000


@router.get("/positions")
async def wallet_positions_ep(_: str = Depends(get_current_user)):
    """Read-only: fetch all open Jupiter Perp positions for the configured wallet."""
    try:
        positions, err = await asyncio.to_thread(_get_positions)
        if positions is None:
            return {"wallet": _WALLET, "positions": [], "sol_balance": None, "error": err}
        try:
            sol_balance = await asyncio.to_thread(_get_sol_balance)
        except Exception:
            sol_balance = None
        return {"wallet": _WALLET, "positions": positions, "sol_balance": sol_balance, "error": None}
    except Exception as exc:
        log.warning("wallet_positions_ep error: %s", exc)
        return {"wallet": _WALLET, "positions": [], "sol_balance": None, "error": str(exc)}
