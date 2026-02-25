"""
jupiter_swap.py — Jupiter V6 quote + swap execution module.

Uses:
  - Jupiter Quote API: GET https://quote-api.jup.ag/v6/quote
  - Jupiter Swap API: POST https://quote-api.jup.ag/v6/swap
  - solders for transaction signing (pure Python, fast)
  - httpx for async HTTP

Env vars required:
  WALLET_PRIVATE_KEY   — base58-encoded Solana keypair private key
  SOLANA_RPC_URL       — RPC endpoint (default: mainnet-beta)
  EXECUTOR_DRY_RUN     — if "true", skip actual RPC submission
"""

import asyncio
import base64
import json
import logging
import os
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

SOL_MINT = "So11111111111111111111111111111111111111112"
LAMPORTS_PER_SOL = 1_000_000_000
QUOTE_API = "https://quote-api.jup.ag/v6/quote"
SWAP_API = "https://quote-api.jup.ag/v6/swap"
PRICE_API = "https://api.jup.ag/price/v2"
MAX_SLIPPAGE_BPS = 150          # 1.5% — memecoins need headroom
MAX_PRICE_IMPACT_PCT = 3.0      # reject if price impact > 3%
DRY_RUN = os.getenv("EXECUTOR_DRY_RUN", "true").lower() == "true"
RPC_URL = os.getenv("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")

# ── Keypair loading (lazy, cached) ─────────────────────────────────────────────

_keypair_cache = None


def load_keypair():
    """Load keypair from WALLET_PRIVATE_KEY env var (base58 encoded).
    Returns a solders.keypair.Keypair or None if not configured."""
    global _keypair_cache
    if _keypair_cache is not None:
        return _keypair_cache
    raw = os.getenv("WALLET_PRIVATE_KEY", "").strip()
    if not raw:
        logger.warning("WALLET_PRIVATE_KEY not set — running in quote-only mode")
        return None
    try:
        from solders.keypair import Keypair  # type: ignore
        import base58  # type: ignore
        secret_bytes = base58.b58decode(raw)
        kp = Keypair.from_bytes(secret_bytes)
        _keypair_cache = kp
        logger.info("Keypair loaded: pubkey=%s", str(kp.pubkey()))
        return kp
    except ImportError:
        logger.error("solders/base58 not installed — run: pip install solders base58")
        return None
    except Exception as exc:
        logger.error("Failed to load keypair: %s", exc)
        return None


def get_public_key() -> Optional[str]:
    """Return the wallet's public key as a string."""
    kp = load_keypair()
    if kp is None:
        return None
    return str(kp.pubkey())


# ── Quote ──────────────────────────────────────────────────────────────────────

async def get_quote(
    input_mint: str,
    output_mint: str,
    amount_lamports: int,
    slippage_bps: int = MAX_SLIPPAGE_BPS,
) -> dict:
    """
    GET /v6/quote — fetch best route.

    Returns Jupiter quote dict including:
      outAmount, priceImpactPct, routePlan, inputMint, outputMint, inAmount
    Raises ValueError if price impact is too high.
    """
    params = {
        "inputMint": input_mint,
        "outputMint": output_mint,
        "amount": str(amount_lamports),
        "slippageBps": str(slippage_bps),
        "onlyDirectRoutes": "false",
        "asLegacyTransaction": "false",
    }
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(QUOTE_API, params=params)
        r.raise_for_status()
        quote = r.json()

    impact = float(quote.get("priceImpactPct", 0) or 0)
    if impact > MAX_PRICE_IMPACT_PCT:
        raise ValueError(
            f"Price impact {impact:.2f}% exceeds max {MAX_PRICE_IMPACT_PCT}% — aborting"
        )
    logger.debug(
        "Quote: %s→%s  in=%s  out=%s  impact=%.3f%%",
        input_mint[:8], output_mint[:8],
        amount_lamports, quote.get("outAmount"), impact,
    )
    return quote


async def build_swap_tx(quote: dict, user_public_key: str) -> str:
    """
    POST /v6/swap — get a base64-encoded transaction ready to sign.
    Returns base64 transaction string.
    """
    payload = {
        "quoteResponse": quote,
        "userPublicKey": user_public_key,
        "wrapAndUnwrapSol": True,
        "dynamicComputeUnitLimit": True,
        "prioritizationFeeLamports": "auto",
    }
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(SWAP_API, json=payload)
        r.raise_for_status()
        data = r.json()
    return data["swapTransaction"]


async def sign_and_send(tx_b64: str) -> str:
    """
    Deserialize base64 transaction, sign with loaded keypair, send to RPC.
    Returns transaction signature string.
    Raises RuntimeError in dry-run mode (won't send).
    """
    if DRY_RUN:
        raise RuntimeError("DRY_RUN mode — not sending transaction")

    kp = load_keypair()
    if kp is None:
        raise RuntimeError("Keypair not loaded — cannot sign")

    try:
        from solders.transaction import VersionedTransaction  # type: ignore
        from solders.keypair import Keypair  # type: ignore
    except ImportError:
        raise RuntimeError("solders not installed — run: pip install solders base58")

    tx_bytes = base64.b64decode(tx_b64)
    tx = VersionedTransaction.from_bytes(tx_bytes)

    # Sign the transaction
    tx.sign([kp])

    # Serialize and send via RPC
    signed_bytes = bytes(tx)
    encoded = base64.b64encode(signed_bytes).decode()

    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "sendTransaction",
        "params": [
            encoded,
            {
                "encoding": "base64",
                "skipPreflight": False,
                "preflightCommitment": "confirmed",
                "maxRetries": 3,
            },
        ],
    }
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(RPC_URL, json=payload)
        r.raise_for_status()
        result = r.json()

    if "error" in result:
        raise RuntimeError(f"RPC error: {result['error']}")

    sig = result.get("result")
    if not sig:
        raise RuntimeError(f"No tx signature in RPC response: {result}")

    logger.info("Transaction sent: %s", sig)
    return sig


# ── Price fetch ────────────────────────────────────────────────────────────────

async def get_sol_price_usd() -> float:
    """Fetch current SOL price in USD from Jupiter Price API."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(PRICE_API, params={"ids": SOL_MINT})
            r.raise_for_status()
            data = r.json()
        price = float(data.get("data", {}).get(SOL_MINT, {}).get("price", 0) or 0)
        if price > 0:
            return price
    except Exception as exc:
        logger.warning("Jupiter SOL price fetch failed: %s", exc)

    # Fallback: try DexScreener
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get("https://api.dexscreener.com/latest/dex/tokens/" + SOL_MINT)
            r.raise_for_status()
            pairs = r.json().get("pairs", [])
            if pairs:
                return float(pairs[0].get("priceUsd", 0) or 0)
    except Exception:
        pass

    raise RuntimeError("Could not fetch SOL price from any source")


async def get_token_price_usd(mint: str) -> Optional[float]:
    """Fetch current token price in USD from Jupiter Price API."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(PRICE_API, params={"ids": mint})
            r.raise_for_status()
            data = r.json()
        price_data = data.get("data", {}).get(mint, {})
        price = float(price_data.get("price", 0) or 0)
        return price if price > 0 else None
    except Exception as exc:
        logger.warning("Token price fetch failed for %s: %s", mint[:8], exc)
        return None


# ── Full buy flow ──────────────────────────────────────────────────────────────

async def execute_buy(mint: str, amount_usd: float, sol_price: float) -> dict:
    """
    Full buy flow: SOL → token.
    1. Convert USD to lamports
    2. Get quote
    3. Build swap tx
    4. Sign and send (or dry-run log)

    Returns: { tx_sig, filled_price, amount_out_raw, amount_usd, sol_used }
    """
    if sol_price <= 0:
        raise ValueError(f"Invalid SOL price: {sol_price}")
    if amount_usd <= 0:
        raise ValueError(f"Invalid amount_usd: {amount_usd}")

    sol_amount = amount_usd / sol_price
    lamports = int(sol_amount * LAMPORTS_PER_SOL)

    if lamports < 100_000:  # < 0.0001 SOL
        raise ValueError(f"Amount too small: {lamports} lamports")

    pubkey = get_public_key()
    if pubkey is None and not DRY_RUN:
        raise RuntimeError("Wallet not configured")

    logger.info(
        "BUY %s: $%.2f → %.4f SOL → %d lamports [dry=%s]",
        mint[:8], amount_usd, sol_amount, lamports, DRY_RUN,
    )

    quote = await get_quote(SOL_MINT, mint, lamports)
    amount_out = int(quote.get("outAmount", 0))

    if DRY_RUN:
        logger.info(
            "DRY_RUN BUY: %s  $%.2f  out=%d  impact=%.3f%%",
            mint[:8], amount_usd, amount_out,
            float(quote.get("priceImpactPct", 0) or 0),
        )
        return {
            "tx_sig": "DRY_RUN_NO_TX",
            "filled_price": (amount_usd / amount_out) if amount_out > 0 else 0,
            "amount_out_raw": amount_out,
            "amount_usd": amount_usd,
            "sol_used": sol_amount,
            "dry_run": True,
        }

    tx_b64 = await build_swap_tx(quote, pubkey)
    sig = await sign_and_send(tx_b64)

    filled_price = (amount_usd / amount_out) if amount_out > 0 else 0

    return {
        "tx_sig": sig,
        "filled_price": filled_price,
        "amount_out_raw": amount_out,
        "amount_usd": amount_usd,
        "sol_used": sol_amount,
        "dry_run": False,
    }


# ── Full sell flow ─────────────────────────────────────────────────────────────

async def execute_sell(mint: str, token_amount_raw: int) -> dict:
    """
    Full sell flow: token → SOL.
    1. Get quote (token → SOL)
    2. Build swap tx
    3. Sign and send (or dry-run log)

    Returns: { tx_sig, sol_received, usd_received, dry_run }
    """
    if token_amount_raw <= 0:
        raise ValueError(f"Invalid token amount: {token_amount_raw}")

    pubkey = get_public_key()
    if pubkey is None and not DRY_RUN:
        raise RuntimeError("Wallet not configured")

    logger.info(
        "SELL %s: %d raw tokens [dry=%s]",
        mint[:8], token_amount_raw, DRY_RUN,
    )

    quote = await get_quote(mint, SOL_MINT, token_amount_raw)
    sol_out_lamports = int(quote.get("outAmount", 0))
    sol_received = sol_out_lamports / LAMPORTS_PER_SOL

    if DRY_RUN:
        sol_price = 150.0  # approximate for logging
        try:
            sol_price = await get_sol_price_usd()
        except Exception:
            pass
        logger.info(
            "DRY_RUN SELL: %s  %d tokens → %.4f SOL (~$%.2f)  impact=%.3f%%",
            mint[:8], token_amount_raw, sol_received, sol_received * sol_price,
            float(quote.get("priceImpactPct", 0) or 0),
        )
        return {
            "tx_sig": "DRY_RUN_NO_TX",
            "sol_received": sol_received,
            "usd_received": sol_received * sol_price,
            "dry_run": True,
        }

    tx_b64 = await build_swap_tx(quote, pubkey)
    sig = await sign_and_send(tx_b64)

    sol_price = 0.0
    try:
        sol_price = await get_sol_price_usd()
    except Exception:
        pass

    return {
        "tx_sig": sig,
        "sol_received": sol_received,
        "usd_received": sol_received * sol_price,
        "dry_run": False,
    }
