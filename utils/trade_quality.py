from __future__ import annotations

from typing import Any


def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return round(max(lo, min(hi, float(value))), 1)


def _verdict(score: float) -> str:
    if score >= 75.0:
        return "CLEAN"
    if score >= 55.0:
        return "WATCH"
    if score >= 35.0:
        return "UNSTABLE"
    return "AVOID"


def _reason_text(*, key: str, value: float | int | None = None) -> str:
    if key == "liquidity_healthy":
        return "liquidity base is healthy"
    if key == "liquidity_thin":
        return f"liquidity still thin (${float(value or 0):,.0f})"
    if key == "turnover_healthy":
        return "turnover is healthy for the liquidity base"
    if key == "turnover_hot":
        return f"turnover too hot for liquidity ({float(value or 0):.1f}x)"
    if key == "turnover_cold":
        return f"turnover still thin ({float(value or 0):.1f}x)"
    if key == "breadth_strong":
        return f"trade breadth is strong ({int(value or 0)} txns/h1)"
    if key == "breadth_thin":
        return f"trade breadth is thin ({int(value or 0)} txns/h1)"
    if key == "flow_balanced":
        return "flow is balanced enough to trust"
    if key == "flow_extreme":
        return f"flow is too one-sided ({float(value or 0):.1f}% buy pressure)"
    if key == "stable_move":
        return "move quality is stable"
    if key == "unstable_move":
        return "move quality still looks unstable"
    return key.replace("_", " ")


def assess_trade_quality_from_snapshot(
    *,
    liquidity_usd: float,
    volume_24h: float,
    change_1h: float,
    vol_acceleration: float,
    buy_pressure: float = 50.0,
    txns_h1: int = 0,
) -> dict[str, Any]:
    activity_ratio = (volume_24h / liquidity_usd) if liquidity_usd > 0 else 0.0

    if liquidity_usd >= 100_000:
        liquidity_score = 35.0
    elif liquidity_usd >= 50_000:
        liquidity_score = 30.0
    elif liquidity_usd >= 25_000:
        liquidity_score = 22.0
    elif liquidity_usd >= 12_500:
        liquidity_score = 12.0
    else:
        liquidity_score = 4.0

    if 2.0 <= activity_ratio <= 8.0:
        turnover_score = 25.0
    elif 1.0 <= activity_ratio < 2.0 or 8.0 < activity_ratio <= 12.0:
        turnover_score = 18.0
    elif 0.5 <= activity_ratio < 1.0 or 12.0 < activity_ratio <= 20.0:
        turnover_score = 10.0
    else:
        turnover_score = 2.0

    if txns_h1 >= 250:
        breadth_score = 20.0
    elif txns_h1 >= 100:
        breadth_score = 15.0
    elif txns_h1 >= 40:
        breadth_score = 8.0
    elif txns_h1 >= 10:
        breadth_score = 4.0
    else:
        breadth_score = 0.0

    if 45.0 <= buy_pressure <= 75.0:
        balance_score = 10.0
    elif 35.0 <= buy_pressure < 45.0 or 75.0 < buy_pressure <= 85.0:
        balance_score = 6.0
    elif 25.0 <= buy_pressure < 35.0 or 85.0 < buy_pressure <= 92.0:
        balance_score = 2.0
    else:
        balance_score = 0.0

    if change_1h < 12.0 and vol_acceleration < 15.0:
        stability_score = 10.0
    elif change_1h < 20.0 and vol_acceleration < 20.0:
        stability_score = 5.0
    else:
        stability_score = 0.0

    liquidity_quality_score = _clamp(liquidity_score + (turnover_score * 0.35))
    trade_quality_score = _clamp((turnover_score * 0.45) + (breadth_score * 0.35) + (balance_score * 0.20))
    market_integrity_score = _clamp((balance_score * 0.30) + (stability_score * 0.40) + (breadth_score * 0.30))
    execution_quality_score = _clamp(
        liquidity_score + turnover_score + breadth_score + balance_score + stability_score
    )

    reason_keys: list[tuple[str, float | int | None]] = []
    reason_keys.append(("liquidity_healthy" if liquidity_score >= 22.0 else "liquidity_thin", liquidity_usd))
    if turnover_score >= 18.0:
        reason_keys.append(("turnover_healthy", activity_ratio))
    elif activity_ratio > 12.0:
        reason_keys.append(("turnover_hot", activity_ratio))
    else:
        reason_keys.append(("turnover_cold", activity_ratio))
    reason_keys.append(("breadth_strong" if breadth_score >= 15.0 else "breadth_thin", txns_h1))
    reason_keys.append(("flow_balanced" if balance_score >= 6.0 else "flow_extreme", buy_pressure))
    reason_keys.append(("stable_move" if stability_score >= 5.0 else "unstable_move", None))

    return {
        "liquidity_quality_score": liquidity_quality_score,
        "trade_quality_score": trade_quality_score,
        "market_integrity_score": market_integrity_score,
        "execution_quality_score": execution_quality_score,
        "quality_verdict": _verdict(execution_quality_score),
        "reasons": [_reason_text(key=k, value=v) for k, v in reason_keys[:5]],
        "inputs": {
            "liquidity_usd": round(float(liquidity_usd or 0.0), 2),
            "volume_24h": round(float(volume_24h or 0.0), 2),
            "change_1h": round(float(change_1h or 0.0), 2),
            "vol_acceleration": round(float(vol_acceleration or 0.0), 2),
            "buy_pressure": round(float(buy_pressure or 0.0), 1),
            "txns_h1": int(txns_h1 or 0),
            "activity_ratio": round(activity_ratio, 2),
        },
        "components": {
            "liquidity_score": round(liquidity_score, 1),
            "turnover_score": round(turnover_score, 1),
            "breadth_score": round(breadth_score, 1),
            "balance_score": round(balance_score, 1),
            "stability_score": round(stability_score, 1),
            "activity_ratio": round(activity_ratio, 2),
            "txns_h1": int(txns_h1 or 0),
        },
    }


def assess_trade_quality(pair: dict[str, Any], vol_acceleration: float, buy_pressure: float) -> dict[str, Any]:
    liquidity_usd = float(pair.get("liquidity", {}).get("usd", 0) or 0.0)
    volume_24h = float(pair.get("volume", {}).get("h24", 0) or 0.0)
    change_1h = float(pair.get("priceChange", {}).get("h1", 0) or 0.0)
    txns_h1_data = pair.get("txns", {}).get("h1", {}) or {}
    txns_h1 = int(txns_h1_data.get("buys", 0) or 0) + int(txns_h1_data.get("sells", 0) or 0)
    return assess_trade_quality_from_snapshot(
        liquidity_usd=liquidity_usd,
        volume_24h=volume_24h,
        change_1h=change_1h,
        vol_acceleration=vol_acceleration,
        buy_pressure=buy_pressure,
        txns_h1=txns_h1,
    )
