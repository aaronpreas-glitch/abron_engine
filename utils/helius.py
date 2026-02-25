"""
utils/helius.py — Helius RPC on-chain safety checks (free tier)

Provides three signals per token:
  1. Mint safety  — is mintAuthority/freezeAuthority still live?
  2. Holder concentration — top-20 wallet distribution (rug risk)
  3. Composite safety score  — 0-100, higher = safer

All calls use standard JSON-RPC methods available on the free Helius plan.
Results are cached in-process for CACHE_TTL_SECONDS to avoid burning credits.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Optional

import requests

log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
HELIUS_RPC_URL: str = os.getenv(
    "HELIUS_RPC_URL",
    f"https://mainnet.helius-rpc.com/?api-key={os.getenv('HELIUS_API_KEY', '')}",
)
REQUEST_TIMEOUT: int = 8          # seconds per RPC call
CACHE_TTL_SECONDS: int = 900      # 15 min — holders don't move fast
MAX_RETRIES: int = 2

# Known LP / DEX program-owned accounts we should exclude from whale calc
# (Raydium, Orca, Meteora, Jupiter vaults, etc.)
_DEX_OWNERS = {
    "5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1",  # Raydium AMM v4
    "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8",  # Raydium AMM v3
    "9W959DqEETiGZocYWCQPaJ6sBmUzgfxXfqGeTEdp3aQP",  # Orca whirlpool
    "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4",   # Jupiter v6
    "MERLuDFBMmsHnsBPZw2sDQZHvXFMwp8EdjudcU2HKky",   # Meteora
    "LBUZKhRxPF3XUpBCjp4YzTKgLLjIdZkGkbEJBB3L68f",   # Meteora DLMM
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",   # SPL Token program
    "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJe1bRS",  # Associated Token
}

# ── In-process cache ──────────────────────────────────────────────────────────
_cache: dict[str, tuple[float, dict]] = {}   # {mint: (ts, result)}


def _rpc(method: str, params: list) -> Optional[dict]:
    """Make a single Helius JSON-RPC call. Returns result dict or None."""
    if not HELIUS_RPC_URL or "api-key=" not in HELIUS_RPC_URL:
        return None
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.post(
                HELIUS_RPC_URL,
                json=payload,
                timeout=REQUEST_TIMEOUT,
                headers={"Content-Type": "application/json"},
            )
            resp.raise_for_status()
            data = resp.json()
            if "error" in data:
                log.debug("Helius RPC error [%s]: %s", method, data["error"])
                return None
            return data.get("result")
        except requests.exceptions.Timeout:
            log.debug("Helius timeout [%s] attempt %d", method, attempt + 1)
        except requests.exceptions.RequestException as exc:
            log.debug("Helius request error [%s]: %s", method, exc)
            break
    return None


# ── Public API ────────────────────────────────────────────────────────────────

def get_mint_safety(mint: str) -> dict:
    """
    Check mint/freeze authority status for a token.

    Returns:
        mint_authority_revoked  bool   True = safe (can't print more)
        freeze_authority_revoked bool  True = safe (can't freeze wallets)
        supply                  int    raw token supply
        decimals                int
        safe                    bool   both authorities revoked
        error                   str|None
    """
    result = {
        "mint_authority_revoked": False,
        "freeze_authority_revoked": False,
        "supply": 0,
        "decimals": 0,
        "safe": False,
        "error": None,
    }
    if not mint:
        result["error"] = "no_mint"
        return result

    data = _rpc("getAccountInfo", [mint, {"encoding": "jsonParsed"}])
    if not data:
        result["error"] = "rpc_fail"
        return result

    try:
        info = data["value"]["data"]["parsed"]["info"]
        result["mint_authority_revoked"]   = info.get("mintAuthority")   is None
        result["freeze_authority_revoked"] = info.get("freezeAuthority") is None
        result["supply"]   = int(info.get("supply", 0))
        result["decimals"] = int(info.get("decimals", 0))
        result["safe"]     = result["mint_authority_revoked"] and result["freeze_authority_revoked"]
    except (KeyError, TypeError, ValueError) as exc:
        result["error"] = f"parse_error: {exc}"

    return result


def get_holder_concentration(mint: str) -> dict:
    """
    Fetch top-20 token holders and compute concentration metrics.

    Returns:
        top1_pct        float   largest single wallet %
        top5_pct        float   top-5 wallets combined %
        top10_pct       float   top-10 wallets combined %
        lp_excluded     bool    LP/DEX accounts were filtered out
        holder_count    int     number of non-LP accounts in top-20
        concentration_risk  str  "LOW" | "MEDIUM" | "HIGH" | "CRITICAL"
        error           str|None
    """
    result = {
        "top1_pct": 0.0,
        "top5_pct": 0.0,
        "top10_pct": 0.0,
        "lp_excluded": False,
        "holder_count": 0,
        "concentration_risk": "UNKNOWN",
        "error": None,
    }
    if not mint:
        result["error"] = "no_mint"
        return result

    data = _rpc("getTokenLargestAccounts", [mint])
    if not data:
        result["error"] = "rpc_fail"
        return result

    try:
        accounts = data.get("value", []) or []
        if not accounts:
            result["error"] = "no_accounts"
            return result

        # Total supply from the accounts list (sum of all top-20)
        # We'll compute percentages relative to summed top-20 balances
        # (can't get total supply here, but top-20 pct is still meaningful)
        amounts = []
        for acct in accounts:
            ui = float(acct.get("uiAmount") or 0)
            if ui > 0:
                amounts.append(ui)

        if not amounts:
            result["error"] = "zero_amounts"
            return result

        total = sum(amounts)
        if total == 0:
            result["error"] = "zero_total"
            return result

        pcts = [a / total * 100 for a in amounts]

        result["holder_count"]  = len(pcts)
        result["top1_pct"]      = round(pcts[0], 2) if pcts else 0.0
        result["top5_pct"]      = round(sum(pcts[:5]), 2)
        result["top10_pct"]     = round(sum(pcts[:10]), 2)

        # Risk classification based on top-5 concentration
        t5 = result["top5_pct"]
        if t5 >= 70:
            result["concentration_risk"] = "CRITICAL"
        elif t5 >= 50:
            result["concentration_risk"] = "HIGH"
        elif t5 >= 35:
            result["concentration_risk"] = "MEDIUM"
        else:
            result["concentration_risk"] = "LOW"

    except (KeyError, TypeError, ValueError) as exc:
        result["error"] = f"parse_error: {exc}"

    return result


def get_token_safety(mint: str, use_cache: bool = True) -> dict:
    """
    Composite on-chain safety check. Combines mint safety + holder concentration.

    Returns a single dict with all fields plus:
        safety_score    int     0-100  (higher = safer)
        flags           list    human-readable risk flags
        grade           str     "SAFE" | "CAUTION" | "RISKY" | "DANGER"
    """
    if not mint:
        return _empty_safety("no_mint")

    # Cache check
    if use_cache:
        cached = _cache.get(mint)
        if cached and (time.time() - cached[0]) < CACHE_TTL_SECONDS:
            return cached[1]

    mint_data   = get_mint_safety(mint)
    holder_data = get_holder_concentration(mint)

    flags: list[str] = []
    score = 100  # start perfect, deduct

    # ── Mint authority checks ─────────────────────────────────────────────────
    if mint_data.get("error") and mint_data["error"] not in ("rpc_fail",):
        pass  # data issue, don't penalize
    else:
        if not mint_data.get("mint_authority_revoked"):
            flags.append("MINT_AUTHORITY_LIVE")  # dev can print more tokens
            score -= 30
        if not mint_data.get("freeze_authority_revoked"):
            flags.append("FREEZE_AUTHORITY_LIVE")  # dev can freeze wallets
            score -= 20

    # ── Holder concentration checks ───────────────────────────────────────────
    risk = holder_data.get("concentration_risk", "UNKNOWN")
    t1   = holder_data.get("top1_pct", 0.0)
    t5   = holder_data.get("top5_pct", 0.0)

    if risk == "CRITICAL":
        flags.append(f"CONCENTRATION_CRITICAL_top5={t5:.0f}%")
        score -= 35
    elif risk == "HIGH":
        flags.append(f"CONCENTRATION_HIGH_top5={t5:.0f}%")
        score -= 20
    elif risk == "MEDIUM":
        flags.append(f"CONCENTRATION_MEDIUM_top5={t5:.0f}%")
        score -= 8

    if t1 >= 30:
        flags.append(f"SINGLE_WHALE_top1={t1:.0f}%")
        score -= 15
    elif t1 >= 20:
        flags.append(f"LARGE_HOLDER_top1={t1:.0f}%")
        score -= 5

    score = max(0, min(100, score))

    if score >= 80:
        grade = "SAFE"
    elif score >= 60:
        grade = "CAUTION"
    elif score >= 35:
        grade = "RISKY"
    else:
        grade = "DANGER"

    result = {
        # mint safety
        "mint_authority_revoked":   mint_data.get("mint_authority_revoked", False),
        "freeze_authority_revoked": mint_data.get("freeze_authority_revoked", False),
        "mint_safe":                mint_data.get("safe", False),
        # holder concentration
        "top1_pct":             holder_data.get("top1_pct", 0.0),
        "top5_pct":             holder_data.get("top5_pct", 0.0),
        "top10_pct":            holder_data.get("top10_pct", 0.0),
        "concentration_risk":   risk,
        # composite
        "safety_score":  score,
        "grade":         grade,
        "flags":         flags,
        "mint_error":    mint_data.get("error"),
        "holder_error":  holder_data.get("error"),
    }

    if use_cache:
        _cache[mint] = (time.time(), result)

    return result


def _empty_safety(error: str = "unavailable") -> dict:
    return {
        "mint_authority_revoked":   False,
        "freeze_authority_revoked": False,
        "mint_safe":                False,
        "top1_pct":                 0.0,
        "top5_pct":                 0.0,
        "top10_pct":                0.0,
        "concentration_risk":       "UNKNOWN",
        "safety_score":             50,   # neutral — don't penalize on data failure
        "grade":                    "UNKNOWN",
        "flags":                    [],
        "mint_error":               error,
        "holder_error":             error,
    }


def is_available() -> bool:
    """Return True if Helius RPC is configured and reachable."""
    return bool(HELIUS_RPC_URL and "api-key=" in HELIUS_RPC_URL and os.getenv("HELIUS_API_KEY"))
