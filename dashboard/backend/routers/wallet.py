"""
Wallet positions endpoint — Patches 105, 107.

Routes:
  GET /api/wallet/positions — Jupiter Perp positions + SOL balance for the configured wallet
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException

from auth import get_current_user
from snapshot_cache import snapshot_or_build

log = logging.getLogger("dashboard")
router = APIRouter(prefix="/api/wallet", tags=["wallet"])

_MINT = {
    "So11111111111111111111111111111111111111112":   "SOL",
    "3NZ9JMVBmGAqocybic2c7LQCJScmgsAZ6vQqTDzcqmJh": "BTC",   # Jupiter Perps market mint
    "9n4nbM75f5Ui33ZbPYXn59EwSgE8CGsHtAeTH5YFeJ9E": "BTC",   # legacy/token mint fallback
    "7vfCXTUXx5WJV5JADk17DUJ4ksgau7utNKj4b963voxs": "ETH",
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v": "USDC",
    "mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So":  "mSOL",
}


def _f(val, divisor: float = 1.0) -> float:
    try:
        return round(float(val) / divisor, 6)
    except Exception:
        return 0.0


def _get_wallet_address() -> str:
    """Resolve the configured wallet from the shared Jupiter perps keypair."""
    try:
        from utils.jupiter_perps_trade import get_wallet_address  # type: ignore
        return str(get_wallet_address() or "").strip()
    except Exception as exc:
        log.warning("wallet address resolution failed: %s", exc)
        return ""


def _read_wallet_cache(wallet: str) -> float | None:
    """
    Read the last-known-good SOL balance from the shared wallet cache so the UI
    does not flicker to zero on transient RPC failures.
    """
    if not wallet:
        return None
    try:
        import json
        import os
        import sqlite3
        root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
        db_path = os.path.join(root, "data_storage", "engine.db")
        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                "SELECT value FROM kv_store WHERE key=?", ("spot_wallet_fallback",)
            ).fetchone()
        if not row:
            return None
        cached = json.loads(row[0])
        if str(cached.get("wallet") or "") != wallet:
            return None
        balances = cached.get("balances") or {}
        sol = float(balances.get("SOL") or 0.0)
        return sol if sol > 0 else None
    except Exception:
        return None


def _get_positions(wallet: str):
    import requests as _req
    r = _req.get(
        "https://perps-api.jup.ag/v1/positions",
        params={"walletAddress": wallet},
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


def _get_sol_balance(wallet: str) -> float | None:
    import requests as _req
    r = _req.post(
        "https://api.mainnet-beta.solana.com",
        json={"jsonrpc": "2.0", "id": 1, "method": "getBalance", "params": [wallet]},
        timeout=5,
    )
    lamports = r.json().get("result", {}).get("value")
    if lamports is None:
        return None
    return lamports / 1_000_000_000


def _build_wallet_positions_payload() -> dict:
    try:
        wallet = _get_wallet_address()
        if not wallet:
            return {"wallet": None, "positions": [], "sol_balance": None, "error": "wallet not configured"}

        positions, err = _get_positions(wallet)
        if positions is None:
            return {"wallet": wallet, "positions": [], "sol_balance": None, "error": err}
        try:
            sol_balance = _get_sol_balance(wallet)
        except Exception:
            sol_balance = None
        if not sol_balance:
            cached_balance = _read_wallet_cache(wallet)
            if cached_balance:
                sol_balance = cached_balance
        return {"wallet": wallet, "positions": positions, "sol_balance": sol_balance, "error": None}
    except Exception as exc:
        log.warning("wallet_positions_ep error: %s", exc)
        return {"wallet": None, "positions": [], "sol_balance": None, "error": str(exc)}


@router.get("/positions")
async def wallet_positions_ep(_: str = Depends(get_current_user)):
    """Read-only: fetch all open Jupiter Perp positions for the configured wallet."""
    try:
        return await snapshot_or_build(
            "wallet:positions",
            _build_wallet_positions_payload,
            fresh_s=20,
            stale_s=180,
            wait_timeout_s=4,
        )
    except Exception as exc:
        return {
            "wallet": None,
            "positions": [],
            "sol_balance": None,
            "error": f"wallet_positions_warming:{exc}",
            "_snapshot": {
                "name": "wallet:positions",
                "updated_at": None,
                "age_seconds": None,
                "status": "WARMING",
                "source": "fallback",
            },
        }
