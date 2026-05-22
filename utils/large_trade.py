from __future__ import annotations

from typing import Any

MAJOR_QUOTES = {
    "USDC",
    "USDT",
    "USDS",
    "DAI",
    "USD1",
    "USDE",
    "SOL",
    "WSOL",
    "BTC",
    "WBTC",
    "ETH",
    "WETH",
}


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def normalize_birdeye_large_trade(
    data: dict[str, Any],
    *,
    tracked_mints: dict[str, str | None],
    ts_utc: str,
) -> dict[str, Any] | None:
    from_leg = dict(data.get("from") or {})
    to_leg = dict(data.get("to") or {})
    from_addr = str(from_leg.get("address") or "").strip()
    to_addr = str(to_leg.get("address") or "").strip()
    if not from_addr and not to_addr:
        return None

    matched = [mint for mint in (from_addr, to_addr) if mint and mint in tracked_mints]
    if len(set(matched)) != 1:
        return None

    mint = matched[0]
    symbol = str(tracked_mints.get(mint) or "").upper() or None
    is_buy = mint == to_addr
    token_leg = to_leg if is_buy else from_leg
    counterparty = from_leg if is_buy else to_leg
    counterparty_symbol = str(counterparty.get("symbol") or "").upper() or None
    counterparty_address = str(counterparty.get("address") or "").strip() or None
    volume_usd = _to_float(data.get("volumeUSD"))
    token_amount = abs(_to_float(token_leg.get("uiChangeAmount") or token_leg.get("uiAmount")))
    token_price = _to_float(token_leg.get("nearestPrice") or token_leg.get("price"))
    if token_price <= 0 and token_amount > 0 and volume_usd > 0:
        token_price = volume_usd / token_amount

    reasons: list[str] = []
    if is_buy:
        reasons.append("recent large buy printed")
    else:
        reasons.append("recent large sell printed")
    if counterparty_symbol in MAJOR_QUOTES:
        reasons.append(f"flow came against {counterparty_symbol}")

    sponsorship_label = "WATCH"
    if is_buy and volume_usd >= 50000 and counterparty_symbol in MAJOR_QUOTES:
        sponsorship_label = "STRONG"
        reasons.append("size and quote quality suggest real sponsorship")
    elif is_buy and volume_usd >= 15000:
        sponsorship_label = "ACTIVE"
        reasons.append("size suggests meaningful interest")
    elif not is_buy and volume_usd >= 25000:
        sponsorship_label = "EXIT"
        reasons.append("exit-sized sell flow hit the tape")

    return {
        "mint": mint,
        "symbol": symbol or str(token_leg.get("symbol") or "").upper() or None,
        "ts_utc": ts_utc,
        "trade_side": "BUY" if is_buy else "SELL",
        "volume_usd": round(volume_usd, 2),
        "owner": str(data.get("owner") or "").strip() or None,
        "source": str(data.get("source") or "").strip() or None,
        "tx_hash": str(data.get("txHash") or "").strip(),
        "pool_address": str(data.get("poolAddress") or "").strip() or None,
        "counterparty_symbol": counterparty_symbol,
        "counterparty_address": counterparty_address,
        "token_amount": round(token_amount, 6),
        "token_price": round(token_price, 8) if token_price > 0 else 0.0,
        "sponsorship_label": sponsorship_label,
        "reasons": reasons[:4],
        "raw": dict(data),
    }
