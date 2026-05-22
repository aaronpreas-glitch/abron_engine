from __future__ import annotations

from typing import Any


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def _pick_first(item: dict[str, Any], keys: list[str]) -> Any:
    for key in keys:
        value = item.get(key)
        if value is not None and value != "":
            return value
    return None


def inspect_birdeye_wallet_tx(
    data: dict[str, Any],
    *,
    wallet_address: str,
    ts_utc: str,
) -> dict[str, Any]:
    payload = dict(data or {})
    tx_hash = str(_pick_first(payload, ["txHash", "signature", "tx_hash"]) or "").strip()
    if not tx_hash:
        return {
            "normalized": None,
            "drop_reason": "missing_tx_hash",
            "sample": {
                "wallet_address": wallet_address,
                "raw_type": str(payload.get("type") or "") or None,
                "keys": sorted(payload.keys())[:20],
            },
        }

    from_leg = dict(payload.get("from") or {})
    to_leg = dict(payload.get("to") or {})
    base_leg = dict(payload.get("base") or {})
    quote_leg = dict(payload.get("quote") or {})
    token_leg = None
    counterparty_leg = None

    if from_leg or to_leg:
        from_symbol = str(_pick_first(from_leg, ["symbol", "tokenSymbol"]) or "").upper()
        to_symbol = str(_pick_first(to_leg, ["symbol", "tokenSymbol"]) or "").upper()
        stable_like = {"USDC", "USDT", "USDS", "DAI", "SOL", "WSOL", "ETH", "WETH", "BTC", "WBTC"}
        if to_symbol and to_symbol not in stable_like:
            token_leg = to_leg
            counterparty_leg = from_leg
        elif from_symbol and from_symbol not in stable_like:
            token_leg = from_leg
            counterparty_leg = to_leg
    elif base_leg or quote_leg:
        base_symbol = str(_pick_first(base_leg, ["symbol", "tokenSymbol"]) or "").upper()
        quote_symbol = str(_pick_first(quote_leg, ["symbol", "tokenSymbol"]) or "").upper()
        stable_like = {"USDC", "USDT", "USDS", "DAI", "SOL", "WSOL", "ETH", "WETH", "BTC", "WBTC"}
        if quote_symbol and quote_symbol not in stable_like:
            token_leg = quote_leg
            counterparty_leg = base_leg
        elif base_symbol and base_symbol not in stable_like:
            token_leg = base_leg
            counterparty_leg = quote_leg
        elif quote_leg:
            token_leg = quote_leg
            counterparty_leg = base_leg
        elif base_leg:
            token_leg = base_leg
            counterparty_leg = quote_leg

    mint = str(
        _pick_first(
            token_leg or payload,
            ["address", "mint", "tokenAddress", "token_address"],
        )
        or ""
    ).strip()
    symbol = str(_pick_first(token_leg or payload, ["symbol", "tokenSymbol", "token_symbol"]) or "").upper() or None
    side = str(_pick_first(payload, ["side", "direction", "txType", "tx_type", "type"]) or "").upper() or None
    if side in ("SWAP_BUY", "BUY_IN", "IN"):
        side = "BUY"
    elif side in ("SWAP_SELL", "SELL_OUT", "OUT"):
        side = "SELL"
    elif not side and token_leg is not None:
        side = "BUY" if token_leg is to_leg else "SELL"
    elif side and ("SELL" in side or "REMOVE" in side):
        side = "SELL"
    elif side:
        side = "BUY"

    amount_usd = _to_float(_pick_first(payload, ["amountUsd", "amount_usd", "usdValue", "valueUsd", "volumeUSD"]))
    token_amount = abs(
        _to_float(_pick_first(token_leg or payload, ["uiAmount", "uiChangeAmount", "amount", "tokenAmount"]))
    )
    token_price = _to_float(_pick_first(token_leg or payload, ["nearestPrice", "price", "tokenPrice"]))
    if token_price <= 0 and token_amount > 0 and amount_usd > 0:
        token_price = amount_usd / token_amount

    counterparty_symbol = str(
        _pick_first(counterparty_leg or payload, ["symbol", "tokenSymbol", "counterpartySymbol"]) or ""
    ).upper() or None
    counterparty_address = str(
        _pick_first(counterparty_leg or payload, ["address", "tokenAddress", "counterpartyAddress"]) or ""
    ).strip() or None

    normalized = {
        "observed_at_utc": ts_utc,
        "wallet_address": wallet_address,
        "mint": mint or None,
        "symbol": symbol,
        "tx_hash": tx_hash,
        "side": side,
        "amount_usd": round(amount_usd, 2),
        "token_amount": round(token_amount, 8),
        "token_price": round(token_price, 10) if token_price > 0 else 0.0,
        "source": str(_pick_first(payload, ["source", "dex", "venue"]) or "") or None,
        "pool_address": str(_pick_first(payload, ["poolAddress", "pairAddress", "pool_address"]) or "") or None,
        "counterparty_symbol": counterparty_symbol,
        "counterparty_address": counterparty_address,
        "metadata": {
            "raw_type": str(payload.get("type") or ""),
            "owner": str(_pick_first(payload, ["owner", "wallet", "address"]) or "") or None,
            "block_unix_time": _pick_first(payload, ["blockUnixTime", "block_unix_time"]),
        },
    }
    sample = {
        "wallet_address": wallet_address,
        "tx_hash": tx_hash,
        "mint": normalized["mint"],
        "symbol": normalized["symbol"],
        "side": normalized["side"],
        "amount_usd": normalized["amount_usd"],
        "source": normalized["source"],
        "raw_type": str(payload.get("type") or "") or None,
        "base_symbol": str(_pick_first(base_leg, ["symbol", "tokenSymbol"]) or "").upper() or None,
        "quote_symbol": str(_pick_first(quote_leg, ["symbol", "tokenSymbol"]) or "").upper() or None,
        "from_symbol": str(_pick_first(from_leg, ["symbol", "tokenSymbol"]) or "").upper() or None,
        "to_symbol": str(_pick_first(to_leg, ["symbol", "tokenSymbol"]) or "").upper() or None,
    }

    if not normalized["mint"]:
        return {
            "normalized": None,
            "drop_reason": "missing_mint",
            "sample": sample,
        }

    return {
        "normalized": normalized,
        "drop_reason": None,
        "sample": sample,
    }


def normalize_birdeye_wallet_tx(
    data: dict[str, Any],
    *,
    wallet_address: str,
    ts_utc: str,
) -> dict[str, Any] | None:
    inspection = inspect_birdeye_wallet_tx(
        data,
        wallet_address=wallet_address,
        ts_utc=ts_utc,
    )
    return inspection.get("normalized")
