#!/usr/bin/env python3
"""
Wallet Discovery — Aligned Smart Wallets (v2)

Strategy: find RECENT BUYERS of our spot tokens (active traders, not whales)
who ALSO buy small-cap memecoins in 0.05–15 SOL range.

Long-term top holders of JUP/WIF are typically institutions/VCs — they don't
touch memecoins. RECENT BUYERS are active traders who might do both.

Pipeline:
  1. DexScreener → Raydium pool address for each spot token
  2. Helius Enhanced API on pool address → parsed recent swaps + buyer wallets
  3. For each unique buyer: check their swap history for meme activity
  4. Score: spot token diversity × meme frequency × buy-size alignment

Usage:
  set -a && source /root/memecoin_engine/.env && set +a
  python3 /root/memecoin_engine/_patches/discover_wallets.py
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from collections import defaultdict

import requests

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger(__name__)

# ── Config ───────────────────────────────────────────────────────────────────

HELIUS_RPC  = os.environ.get("HELIUS_RPC_URL", "")
HELIUS_KEY  = os.environ.get("HELIUS_API_KEY", "")
HELIUS_BASE = "https://api.helius.xyz/v0"

SPOT_TOKENS = [
    {"symbol": "JUP",    "mint": "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN",  "weight": 1.5},
    {"symbol": "WIF",    "mint": "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm", "weight": 1.3},
    {"symbol": "RAY",    "mint": "4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R", "weight": 1.2},
    {"symbol": "BONK",   "mint": "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263", "weight": 1.1},
    {"symbol": "ORCA",   "mint": "orcaEKTdK7LKz57vaAYr9QeNsVEPfiu6QeMU1kektZE",  "weight": 1.0},
    {"symbol": "POPCAT", "mint": "7GCihgDB8fe6KNjn2MYtkzZcRjQy3t9GHdC8uHYmW2hr", "weight": 0.7},
    {"symbol": "PYTH",   "mint": "HZ1JovNiVvGrGNiiYvEozEVgZ58xaU3RKwX8eACQBCt3", "weight": 0.5},
]

SPOT_MINTS = {t["mint"] for t in SPOT_TOKENS}

STABLE_MINTS: set[str] = {
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",  # USDT
    "So11111111111111111111111111111111111111112",      # WSOL
}

EXCLUDE_MINTS = SPOT_MINTS | STABLE_MINTS

# Pool swap sample: how many recent transactions to pull per spot pool
POOL_TXN_LIMIT  = 50

# Memecoin buy size range (SOL) — same as smart_wallet_tracker.py
MIN_MEME_SOL = 0.05
MAX_MEME_SOL = 15.0

# Minimum meme buys to qualify
MIN_MEME_BUYS = 1

# ── DexScreener: get pool address for a token ─────────────────────────────────

def get_pool_address(mint: str, symbol: str) -> str | None:
    """Return the Raydium/Orca pool address for this token via DexScreener."""
    try:
        r = requests.get(
            f"https://api.dexscreener.com/latest/dex/tokens/{mint}",
            timeout=8,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        pairs = r.json().get("pairs") or []
        # Pick the highest-liquidity SOL-chain pair
        sol_pairs = [p for p in pairs if p.get("chainId") == "solana"]
        if not sol_pairs:
            return None
        best = max(sol_pairs, key=lambda p: float(p.get("liquidity", {}).get("usd", 0) or 0))
        pool = best.get("pairAddress")
        dex  = best.get("dexId", "?")
        liq  = best.get("liquidity", {}).get("usd", 0)
        log.info("    pool=%s dex=%s liq=$%.0f", pool, dex, liq or 0)
        return pool
    except Exception as e:
        log.debug("  DexScreener error for %s: %s", symbol, e)
        return None


# ── Helius: get recent swaps from a pool address ─────────────────────────────

def get_pool_swaps(pool_address: str, limit: int = POOL_TXN_LIMIT) -> list[dict]:
    """
    Call Helius Enhanced Transactions on the pool address.
    Returns parsed swap transactions involving this pool.
    """
    try:
        r = requests.get(
            f"{HELIUS_BASE}/addresses/{pool_address}/transactions",
            params={"api-key": HELIUS_KEY, "type": "SWAP", "limit": limit},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=15,
        )
        if r.status_code != 200:
            log.debug("  Helius pool swaps HTTP %d for %s", r.status_code, pool_address[:12])
            return []
        result = r.json()
        return result if isinstance(result, list) else []
    except Exception as e:
        log.debug("  Helius pool swaps error: %s", e)
        return []


def extract_buyers(txns: list[dict], spot_mint: str) -> list[str]:
    """
    From pool swap transactions, extract wallet addresses that BOUGHT the spot token.
    A buyer is a wallet whose tokenTransfers show them receiving the spot token.
    """
    buyers: set[str] = set()
    for tx in txns:
        for transfer in tx.get("tokenTransfers", []):
            if transfer.get("mint") == spot_mint:
                recipient = transfer.get("toUserAccount", "")
                if recipient and len(recipient) >= 32:
                    buyers.add(recipient)
    return list(buyers)


# ── Helius: check a wallet's memecoin swap history ───────────────────────────

def get_swap_history(wallet: str, limit: int = 20) -> list[dict]:
    try:
        r = requests.get(
            f"{HELIUS_BASE}/addresses/{wallet}/transactions",
            params={"api-key": HELIUS_KEY, "type": "SWAP", "limit": limit},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=12,
        )
        if r.status_code != 200:
            return []
        result = r.json()
        return result if isinstance(result, list) else []
    except Exception:
        return []


def parse_meme_buys(txns: list[dict], wallet: str) -> list[dict]:
    """
    Find memecoin buys: wallet receives non-spot, non-stable token AND spends SOL in range.
    """
    buys = []
    for tx in txns:
        received = [
            t for t in tx.get("tokenTransfers", [])
            if t.get("toUserAccount") == wallet
            and t.get("mint") not in EXCLUDE_MINTS
            and t.get("mint")
        ]
        if not received:
            continue

        sol_spent = 0.0
        for nt in tx.get("nativeTransfers", []):
            if nt.get("fromUserAccount") == wallet:
                try:
                    sol_spent += int(nt.get("amount", 0)) / 1e9
                except Exception:
                    pass

        if MIN_MEME_SOL <= sol_spent <= MAX_MEME_SOL:
            buys.append({
                "mint":      received[0]["mint"],
                "sol_spent": sol_spent,
                "source":    tx.get("source", ""),
            })
    return buys


def parse_spot_buys(txns: list[dict], wallet: str) -> set[str]:
    """Return set of spot token symbols this wallet recently bought."""
    bought: set[str] = set()
    for tx in txns:
        for transfer in tx.get("tokenTransfers", []):
            if (transfer.get("toUserAccount") == wallet
                    and transfer.get("mint") in SPOT_MINTS):
                # Find which symbol this is
                for st in SPOT_TOKENS:
                    if st["mint"] == transfer.get("mint"):
                        bought.add(st["symbol"])
    return bought


# ── Known program addresses to skip ─────────────────────────────────────────

_KNOWN_PROGRAMS = {
    "11111111111111111111111111111111",
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
    "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJe8bv",
    "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4",
    "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8",
    "9xQeWvG816bUx9EPjHmaT23yvVM2ZWbrrpZb9PusVFin",
}

def _is_program(address: str) -> bool:
    return address in _KNOWN_PROGRAMS or len(address) < 32


# ── Main discovery ────────────────────────────────────────────────────────────

def discover() -> list[dict]:
    if not HELIUS_RPC or not HELIUS_KEY:
        log.error("HELIUS_RPC_URL and HELIUS_API_KEY must be set")
        sys.exit(1)

    log.info("=" * 60)
    log.info("WALLET DISCOVERY — Recent spot buyers who also do memes")
    log.info("=" * 60)

    # Step 1: Get recent buyers of each spot token via their DEX pool
    log.info("\n[1] Getting recent pool swap buyers for each spot token...")

    # wallet_address → {symbol: weight} (which spot tokens they bought recently)
    wallet_spots: dict[str, dict[str, float]] = defaultdict(dict)

    for token in SPOT_TOKENS:
        log.info("\n  %s (%s...)", token["symbol"], token["mint"][:12])

        pool = get_pool_address(token["mint"], token["symbol"])
        if not pool:
            log.info("    → No pool found, skipping")
            continue

        txns = get_pool_swaps(pool, limit=POOL_TXN_LIMIT)
        log.info("    → %d swap txns fetched from pool", len(txns))

        buyers = extract_buyers(txns, token["mint"])
        filtered = [b for b in buyers if not _is_program(b)]
        log.info("    → %d buyer wallets extracted", len(filtered))

        for wallet in filtered:
            wallet_spots[wallet][token["symbol"]] = token["weight"]

        time.sleep(0.5)

    total_unique = len(wallet_spots)
    log.info("\n  Total unique recent buyers across all spot tokens: %d", total_unique)

    multi_spot = {w: s for w, s in wallet_spots.items() if len(s) >= 2}
    log.info("  Bought 2+ different spot tokens recently: %d", len(multi_spot))

    # Use multi-spot if we have enough, else fall back to all
    candidates = multi_spot if len(multi_spot) >= 5 else wallet_spots
    log.info("  Checking %d candidates for meme activity...", len(candidates))

    # Step 2: Check each candidate's full swap history
    log.info("\n[2] Checking memecoin activity (last 20 swaps per wallet)...")

    results = []
    checked = 0

    for wallet, spots in candidates.items():
        checked += 1
        if checked % 20 == 0:
            log.info("  ... %d / %d", checked, len(candidates))

        txns      = get_swap_history(wallet, limit=20)
        meme_buys = parse_meme_buys(txns, wallet)
        # Also capture which spot tokens they've bought (may include more than pool sample)
        spot_bought = parse_spot_buys(txns, wallet)
        # Merge spot signals
        for sym in spot_bought:
            for st in SPOT_TOKENS:
                if st["symbol"] == sym:
                    spots[sym] = st["weight"]

        time.sleep(0.12)

        if len(meme_buys) < MIN_MEME_BUYS:
            continue

        avg_sol = sum(b["sol_spent"] for b in meme_buys) / len(meme_buys)

        # Hard filter: avg meme buy too large → whale/degen, skip
        if avg_sol > 10.0:
            continue

        spot_score  = sum(spots.values())
        meme_count  = len(meme_buys)
        # Size alignment: 0.05–2 SOL is ideal, penalise larger buys
        size_fit    = max(0.3, 1.0 - max(0, avg_sol - 2.0) / 10.0)
        total_score = spot_score * (1 + meme_count * 0.4) * size_fit

        label = "+".join(sorted(spots.keys())) + " buyer"

        results.append({
            "wallet":       wallet,
            "label":        label,
            "spot_tokens":  sorted(spots.keys()),
            "spot_count":   len(spots),
            "spot_score":   round(spot_score, 2),
            "meme_buys":    meme_count,
            "avg_meme_sol": round(avg_sol, 4),
            "total_score":  round(total_score, 3),
        })

    results.sort(key=lambda x: x["total_score"], reverse=True)

    # ── Print results ────────────────────────────────────────────────────────
    log.info("\n" + "=" * 60)
    log.info("TOP ALIGNED WALLET CANDIDATES")
    log.info("=" * 60)

    if not results:
        log.info("\nNo qualifying wallets found.")
        log.info("Top wallet_spots sample (no meme filter):")
        for w, s in list(wallet_spots.items())[:10]:
            log.info("  %s  spots=%s", w, list(s.keys()))
        return []

    for i, r in enumerate(results[:20]):
        log.info("\n%2d. %s", i + 1, r["wallet"])
        log.info("    Spot : %s  (%d tokens, score %.1f)",
                 " + ".join(r["spot_tokens"]), r["spot_count"], r["spot_score"])
        log.info("    Memes: %d buys | avg %.3f SOL | total_score %.2f",
                 r["meme_buys"], r["avg_meme_sol"], r["total_score"])
        log.info("    Label: \"%s\"", r["label"])

    out_path = "/root/memecoin_engine/_patches/wallet_candidates.json"
    with open(out_path, "w") as f:
        json.dump(results[:30], f, indent=2)
    log.info("\nSaved top 30 to %s", out_path)

    return results


if __name__ == "__main__":
    discover()
