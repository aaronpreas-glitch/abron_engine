"""
jupiter_perps_trade.py — Jupiter Perps on-chain open/close execution.

Pattern mirrors jupiter_swap.py:
  - Sync HTTP via requests
  - Keypair loaded from WALLET_PRIVATE_KEY (base58, shared with spot executor)
  - dry_run=True gate skips all HTTP calls and returns a no-op result

Env vars:
  WALLET_PRIVATE_KEY        — base58 Solana keypair private key
  PERP_SLIPPAGE_BPS         — max slippage in basis points, default "300" (3%)

Patch 110: v1→v2 API upgrade + SOL collateral (no USDC required)
"""
import base64
import logging
import os

import requests

log = logging.getLogger(__name__)

PERPS_API    = "https://perps-api.jup.ag/v2"
SOL_DECIMALS = 9   # lamports per SOL

# ── Keypair (lazy-cached, shared with jupiter_swap.py) ────────────────────────

_keypair_cache = None


def _load_keypair():
    """Load wallet keypair from WALLET_PRIVATE_KEY env (base58-encoded)."""
    global _keypair_cache
    if _keypair_cache is not None:
        return _keypair_cache
    raw = os.getenv("WALLET_PRIVATE_KEY", "").strip()
    if not raw:
        log.warning("WALLET_PRIVATE_KEY not set — perps live execution disabled")
        return None
    try:
        from solders.keypair import Keypair  # type: ignore
        import base58                        # type: ignore
        kp = Keypair.from_bytes(base58.b58decode(raw))
        _keypair_cache = kp
        log.info("Perps keypair loaded: pubkey=%s", str(kp.pubkey()))
        return kp
    except ImportError:
        log.error("solders/base58 not installed — run: pip install solders base58")
        return None
    except Exception as exc:
        log.error("Perps keypair load failed: %s", exc)
        return None


def get_wallet_address() -> str:
    """Return the wallet's base58 public key, or empty string if not configured."""
    kp = _load_keypair()
    return str(kp.pubkey()) if kp else ""


# ── SOL price helper ──────────────────────────────────────────────────────────

def _get_sol_price() -> float:
    """Fetch current SOL/USD price from Kraken. Returns 0.0 on error."""
    try:
        r = requests.get(
            "https://api.kraken.com/0/public/Ticker?pair=SOLUSD",
            timeout=8,
        )
        result = r.json().get("result", {})
        ticker = result.get("SOLUSD", result.get("SOLUSDT", {}))
        return float(ticker["c"][0])   # "c" = last trade closed price
    except Exception as exc:
        log.warning("Could not fetch SOL price from Kraken: %s", exc)
        return 0.0


def _usd_to_lamports(usd: float, sol_price: float) -> str:
    """Convert a USD amount to SOL lamports string. Returns '0' if price unavailable."""
    if sol_price <= 0:
        return "0"
    sol_amount = usd / sol_price
    return str(int(sol_amount * 10 ** SOL_DECIMALS))


# ── Transaction signing ───────────────────────────────────────────────────────

def _sign_tx(serialized_b64: str) -> tuple[str | None, str | None]:
    """Decode a base64 Jupiter versioned transaction, sign it.

    Returns (signed_b64, presigned_tx_sig):
        signed_b64:       re-encoded base64 transaction ready to submit
        presigned_tx_sig: base58 wallet signature extracted before submission
                          (Patch 161 — used as idempotency key for reconciliation)
    """
    kp = _load_keypair()
    if not kp:
        return None, None
    try:
        from solders.transaction import VersionedTransaction  # type: ignore
        tx = VersionedTransaction.from_bytes(base64.b64decode(serialized_b64))
        # V0 VersionedTransaction signing: prepend 0x80 version prefix to message bytes.
        # Jupiter's tx has multiple required signers; preserve existing sigs (Jupiter
        # pre-signs slot 2) and only replace slot 0 (our wallet).
        sign_bytes = bytes([0x80]) + bytes(tx.message)
        sig = kp.sign_message(sign_bytes)
        presigned_tx_sig = str(sig)   # Patch 161: base58 sig available before submit
        existing_sigs = list(tx.signatures)
        existing_sigs[0] = sig
        signed_tx = VersionedTransaction.populate(tx.message, existing_sigs)
        return base64.b64encode(bytes(signed_tx)).decode(), presigned_tx_sig
    except Exception as exc:
        log.error("Tx sign failed: %s", exc)
        return None, None


# ── Open position ─────────────────────────────────────────────────────────────

def open_perp_sync(
    symbol:         str,
    side:           str,
    collateral_usd: float,
    leverage:       float,
    dry_run:        bool = True,
) -> dict:
    """
    Open a leveraged perpetual position on Jupiter Perps (v2 API, SOL collateral).

    Args:
        symbol:         "SOL", "BTC", or "ETH"
        side:           "LONG" or "SHORT"
        collateral_usd: USD amount to deposit as collateral (paid in SOL)
        leverage:       position leverage (e.g. 5.0)
        dry_run:        if True, log intent only — no HTTP calls made

    Returns dict with keys:
        success (bool), position_pubkey (str), tx_sig (str),
        entry_price_usd (float), size_usd (float), liq_price_usd (float)
    """
    if dry_run:
        log.info(
            "[PERPS DRY_RUN] Would open %s %s collateral=$%.2f lev=%.1fx",
            side, symbol, collateral_usd, leverage,
        )
        return {
            "ok": True, "success": True, "state": "DRY_RUN",
            "dry_run": True, "error": None, "presigned_tx_sig": None,
            "position_pubkey": None, "tx_sig": None,
            "entry_price_usd": 0.0, "size_usd": 0.0, "liq_price_usd": 0.0,
            "response_body_excerpt": None,
        }

    wallet = get_wallet_address()
    if not wallet:
        return {
            "ok": False, "success": False, "state": "BUILD_FAILED",
            "error": "WALLET_PRIVATE_KEY not configured", "presigned_tx_sig": None,
            "position_pubkey": None, "tx_sig": None,
            "entry_price_usd": 0.0, "size_usd": 0.0, "liq_price_usd": 0.0,
            "response_body_excerpt": None,
        }

    # Convert USD collateral → SOL lamports
    sol_price = _get_sol_price()
    if sol_price <= 0:
        return {
            "ok": False, "success": False, "state": "BUILD_FAILED",
            "error": "Could not fetch SOL price for collateral conversion",
            "presigned_tx_sig": None, "position_pubkey": None, "tx_sig": None,
            "entry_price_usd": 0.0, "size_usd": 0.0, "liq_price_usd": 0.0,
            "response_body_excerpt": None,
        }
    input_amount = _usd_to_lamports(collateral_usd, sol_price)
    log.info(
        "[PERPS] Collateral $%.2f @ SOL $%.2f = %s lamports",
        collateral_usd, sol_price, input_amount,
    )

    slippage_bps = os.getenv("PERP_SLIPPAGE_BPS", "300")
    payload = {
        "asset":            symbol.upper(),
        "inputToken":       "SOL",
        "inputTokenAmount": input_amount,
        "side":             side.lower(),   # "long" / "short"
        "maxSlippageBps":   slippage_bps,
        "leverage":         str(float(leverage)),
        "walletAddress":    wallet,
    }

    # ── Phase 1: build (quote + unsigned tx) ─────────────────────────────────
    data = None
    build_excerpt = None
    try:
        r = requests.post(f"{PERPS_API}/positions/increase", json=payload, timeout=15)
        r.raise_for_status()
        data = r.json()
    except requests.HTTPError as exc:
        body = ""
        try:
            body = exc.response.text[:300]
        except Exception:
            pass
        build_excerpt = body
        log.error("open_perp_sync build HTTP error %s: %s", exc.response.status_code, body)
        return {
            "ok": False, "success": False, "state": "BUILD_FAILED",
            "error": f"HTTP {exc.response.status_code}: {body}",
            "presigned_tx_sig": None, "position_pubkey": None, "tx_sig": None,
            "entry_price_usd": 0.0, "size_usd": 0.0, "liq_price_usd": 0.0,
            "response_body_excerpt": build_excerpt,
        }
    except Exception as exc:
        log.error("open_perp_sync build error: %s", exc)
        return {
            "ok": False, "success": False, "state": "BUILD_FAILED",
            "error": str(exc), "presigned_tx_sig": None, "position_pubkey": None,
            "tx_sig": None, "entry_price_usd": 0.0, "size_usd": 0.0, "liq_price_usd": 0.0,
            "response_body_excerpt": None,
        }

    build_excerpt   = str(data)[:300]
    position_pubkey = data.get("positionPubkey", "")

    # ── Phase 2: sign ─────────────────────────────────────────────────────────
    signed, presigned_tx_sig = _sign_tx(data["serializedTxBase64"])
    if not signed:
        return {
            "ok": False, "success": False, "state": "SIGN_FAILED",
            "error": "Transaction signing failed — check WALLET_PRIVATE_KEY",
            "presigned_tx_sig": None, "position_pubkey": position_pubkey,
            "tx_sig": None, "entry_price_usd": 0.0, "size_usd": 0.0, "liq_price_usd": 0.0,
            "response_body_excerpt": build_excerpt,
        }

    # ── Phase 3: execute — all errors are SUBMIT_AMBIGUOUS ───────────────────
    quote = data.get("quote", {})
    # Jupiter v2 returns USD values scaled by 1e6
    def _usd(key: str) -> float:
        raw = quote.get(key, "0")
        try:
            return float(raw) / 1e6
        except (ValueError, TypeError):
            return 0.0

    try:
        exec_r = requests.post(
            f"{PERPS_API}/transaction/execute",
            json={"action": "increase-position", "serializedTxBase64": signed},
            timeout=20,
        )
        exec_r.raise_for_status()
        tx_sig = exec_r.json().get("txid") or exec_r.json().get("signature", "")
        log.info(
            "[PERPS LIVE] Opened %s %s size=$%.2f lev=%.1fx entry=$%.4f tx=%s",
            side, symbol, _usd("positionSizeUsd"), leverage, _usd("averagePriceUsd"), tx_sig,
        )
        return {
            "ok": True, "success": True, "state": "SUBMIT_CONFIRMED",
            "error": None, "presigned_tx_sig": presigned_tx_sig,
            "position_pubkey": position_pubkey,
            "tx_sig": tx_sig,
            "entry_price_usd": _usd("averagePriceUsd"),
            "size_usd": _usd("positionSizeUsd"),
            "liq_price_usd": _usd("liquidationPriceUsd"),
            "response_body_excerpt": None,
        }
    except requests.HTTPError as exc:
        body = ""
        try:
            body = exc.response.text[:300]
        except Exception:
            pass
        log.error("open_perp_sync execute HTTP error %s: %s", exc.response.status_code, body)
        return {
            "ok": False, "success": False, "state": "SUBMIT_AMBIGUOUS",
            "error": f"HTTP {exc.response.status_code}: {body}",
            "presigned_tx_sig": presigned_tx_sig, "position_pubkey": position_pubkey,
            "tx_sig": None, "entry_price_usd": 0.0, "size_usd": 0.0, "liq_price_usd": 0.0,
            "response_body_excerpt": body,
        }
    except Exception as exc:
        log.error("open_perp_sync execute error: %s", exc)
        return {
            "ok": False, "success": False, "state": "SUBMIT_AMBIGUOUS",
            "error": str(exc), "presigned_tx_sig": presigned_tx_sig,
            "position_pubkey": position_pubkey,
            "tx_sig": None, "entry_price_usd": 0.0, "size_usd": 0.0, "liq_price_usd": 0.0,
            "response_body_excerpt": None,
        }


# ── Close position ────────────────────────────────────────────────────────────

def close_perp_sync(
    position_pubkey: str,
    dry_run:         bool = True,
    symbol:          str = "SOL",
) -> dict:
    """
    Close an entire Jupiter Perps position by its on-chain pubkey.
    receiveToken must be the market token (SOL→SOL, BTC→BTC, ETH→ETH).
    Jupiter v2 rejects 'SOL' as receiveToken for non-SOL positions.

    Args:
        position_pubkey: from DB column `jupiter_position_key` (set at open time)
        dry_run:         if True, log intent only — no HTTP calls made
        symbol:          asset symbol — used to pick correct receiveToken

    Returns dict with keys:
        success (bool), tx_sig (str), pnl_usd (float)
    """
    if dry_run:
        log.info("[PERPS DRY_RUN] Would close position %s", position_pubkey)
        return {"success": True, "dry_run": True, "tx_sig": None, "pnl_usd": 0.0}

    if not position_pubkey:
        return {"success": False, "error": "position_pubkey is empty — cannot close"}

    # Jupiter v2: receiveToken must be the market token for the asset
    # "Positions can only be closed in USDC and the market token"
    _RECEIVE = {"SOL": "SOL", "BTC": "BTC", "ETH": "ETH"}
    receive_token = _RECEIVE.get(symbol.upper(), "SOL")

    slippage_bps = os.getenv("PERP_SLIPPAGE_BPS", "300")
    payload = {
        "positionPubkey": position_pubkey,
        "receiveToken":   receive_token,
        "entirePosition": True,
        "maxSlippageBps": slippage_bps,
    }

    try:
        r = requests.post(f"{PERPS_API}/positions/decrease", json=payload, timeout=15)
        r.raise_for_status()
        data = r.json()

        signed, _ = _sign_tx(data["serializedTxBase64"])
        if not signed:
            return {"success": False, "error": "Transaction signing failed — check WALLET_PRIVATE_KEY"}

        exec_r = requests.post(
            f"{PERPS_API}/transaction/execute",
            json={"action": "decrease-position", "serializedTxBase64": signed},
            timeout=20,
        )
        exec_r.raise_for_status()

        quote = data.get("quote", {})
        def _usd(key: str) -> float:
            raw = quote.get(key, "0")
            try:
                return float(raw) / 1e6
            except (ValueError, TypeError):
                return 0.0

        tx_sig  = exec_r.json().get("txid") or exec_r.json().get("signature", "")
        pnl_usd = _usd("pnlAfterFeesUsd")
        log.info("[PERPS LIVE] Closed position %s pnl=$%.4f tx=%s", position_pubkey, pnl_usd, tx_sig)
        return {"success": True, "tx_sig": tx_sig, "pnl_usd": pnl_usd}

    except requests.HTTPError as exc:
        body = ""
        try:
            body = exc.response.text[:300]
        except Exception:
            pass
        log.error("close_perp_sync HTTP error %s: %s", exc.response.status_code, body)
        return {"success": False, "error": f"HTTP {exc.response.status_code}: {body}"}
    except Exception as exc:
        log.error("close_perp_sync error: %s", exc)
        return {"success": False, "error": str(exc)}
