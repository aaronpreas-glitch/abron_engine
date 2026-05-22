from __future__ import annotations

from typing import Any

from utils.trade_quality import assess_trade_quality_from_snapshot


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def _to_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except Exception:
        return default


def normalize_birdeye_token_stats(data: dict[str, Any], *, symbol: str | None = None) -> dict[str, Any]:
    mint = str(data.get("address") or data.get("mint") or "").strip()
    trade_1h = _to_int(data.get("trade_1h"))
    buy_1h = _to_int(data.get("buy_1h"))
    sell_1h = _to_int(data.get("sell_1h"))
    volume_1h_usd = _to_float(data.get("volume_1h_usd"))
    volume_buy_1h_usd = _to_float(data.get("volume_buy_1h_usd"))
    volume_sell_1h_usd = _to_float(data.get("volume_sell_1h_usd"))
    liquidity = _to_float(data.get("liquidity"))
    marketcap = _to_float(data.get("marketcap"))
    buy_pressure = (volume_buy_1h_usd / volume_1h_usd * 100.0) if volume_1h_usd > 0 else 50.0
    vol_acceleration = (_to_float(data.get("volume_1h_change_percent")) / 10.0) + 5.0
    vol_acceleration = max(0.0, min(40.0, vol_acceleration))

    row = {
        "mint": mint,
        "symbol": str(symbol or data.get("symbol") or "").upper() or None,
        "ts_utc": str(data.get("ts_utc") or ""),
        "price": _to_float(data.get("price")),
        "liquidity": liquidity,
        "marketcap": marketcap,
        "fdv": _to_float(data.get("fdv")),
        "last_trade_unix_time": _to_int(data.get("last_trade_unix_time")),
        "volume_30m_usd": _to_float(data.get("volume_30m_usd")),
        "volume_1h_usd": volume_1h_usd,
        "volume_24h_usd": _to_float(data.get("volume_24h_usd")),
        "volume_buy_1h_usd": volume_buy_1h_usd,
        "volume_sell_1h_usd": volume_sell_1h_usd,
        "trade_1h": trade_1h,
        "buy_1h": buy_1h,
        "sell_1h": sell_1h,
        "unique_wallet_1h": _to_int(data.get("unique_wallet_1h")),
        "price_change_30m_percent": _to_float(data.get("price_change_30m_percent")),
        "price_change_1h_percent": _to_float(data.get("price_change_1h_percent")),
        "price_change_24h_percent": _to_float(data.get("price_change_24h_percent")),
        "volume_1h_change_percent": _to_float(data.get("volume_1h_change_percent")),
        "trade_1h_change_percent": _to_float(data.get("trade_1h_change_percent")),
        "inputs": {
            "buy_pressure_1h": round(buy_pressure, 1),
            "vol_acceleration_est": round(vol_acceleration, 2),
            "trade_1h": trade_1h,
            "buy_1h": buy_1h,
            "sell_1h": sell_1h,
        },
        "raw": dict(data),
    }

    quality = assess_trade_quality_from_snapshot(
        liquidity_usd=liquidity,
        volume_24h=_to_float(data.get("volume_24h_usd")),
        change_1h=_to_float(data.get("price_change_1h_percent")),
        vol_acceleration=vol_acceleration,
        buy_pressure=buy_pressure,
        txns_h1=trade_1h,
    )
    row["reasons"] = list(quality.get("reasons") or [])
    row["trade_quality"] = quality
    return row


def build_pair_like_from_token_stats(stats: dict[str, Any]) -> dict[str, Any]:
    return {
        "priceChange": {
            "h1": _to_float(stats.get("price_change_1h_percent")),
            "h24": _to_float(stats.get("price_change_24h_percent")),
        },
        "volume": {
            "h24": _to_float(stats.get("volume_24h_usd")),
        },
        "txns": {
            "h1": {
                "buys": _to_int(stats.get("buy_1h")),
                "sells": _to_int(stats.get("sell_1h")),
            }
        },
        "liquidity": {
            "usd": _to_float(stats.get("liquidity")),
        },
    }


def derive_live_candidate_metrics(stats: dict[str, Any]) -> dict[str, float]:
    volume_1h_usd = _to_float(stats.get("volume_1h_usd"))
    volume_buy_1h_usd = _to_float(stats.get("volume_buy_1h_usd"))
    buy_pressure = (volume_buy_1h_usd / volume_1h_usd * 100.0) if volume_1h_usd > 0 else 50.0
    vol_acceleration = (_to_float(stats.get("volume_1h_change_percent")) / 10.0) + 5.0
    vol_acceleration = max(0.0, min(40.0, vol_acceleration))
    return {
        "buy_pressure": round(buy_pressure, 1),
        "vol_acceleration": round(vol_acceleration, 2),
        "marketcap": round(_to_float(stats.get("marketcap")), 2),
    }
