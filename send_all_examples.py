#!/usr/bin/env python3
"""Send example messages for every signal type to Telegram."""

import asyncio
import html
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from config import TELEGRAM_TOKEN, TELEGRAM_CHAT_ID
from telegram import Bot

from utils.format import (
    format_signal,
    format_runner_watch,
    format_watchlist_signal,
    format_watchlist_summary,
)

# ── Helpers duplicated from main.py (inline formatters use these) ──

_PANEL_WIDTH = 42

def _esc(value):
    return html.escape(str(value))

def _trim_text(value, max_len):
    text = str(value or "")
    return text

def _wrap_text(value, max_len):
    text = str(value or "")
    if max_len <= 0 or len(text) <= max_len:
        return [text]
    out = []
    remaining = text
    while len(remaining) > max_len:
        cut = remaining.rfind(" ", 0, max_len + 1)
        if cut <= 0:
            cut = max_len
        out.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    out.append(remaining)
    return out

def _render_pre(rows):
    lines = [str(row or "") for row in rows]
    if not lines:
        return ""
    wrapped = []
    for line in lines:
        wrapped.extend(_wrap_text(line, _PANEL_WIDTH))
    return "\n".join(f"<code>{_esc(line)}</code>" for line in wrapped)

def _kv(label, value, width=_PANEL_WIDTH):
    key = str(label or "").upper()[:9]
    line = f"{key:<9} | {value}"
    return _trim_text(line, width)

def _header_block(tag, rows=None, width=_PANEL_WIDTH):
    parts = [
        f"<b>{_esc(tag)}</b>",
        f"<code>{'-' * min(30, width)}</code>",
    ]
    for row in (rows or []):
        parts.append(f"<code>{_esc(_trim_text(str(row or ''), width))}</code>")
    return "\n".join(parts)

def _fmt_pct(value):
    if value is None:
        return "N/A"
    try:
        n = float(value)
        sign = "+" if n > 0 else ""
        return f"{sign}{n:.2f}%"
    except (TypeError, ValueError):
        return "N/A"

def _fmt_usd_compact(value):
    try:
        n = float(value)
    except (TypeError, ValueError):
        return "N/A"
    abs_n = abs(n)
    if abs_n >= 1_000_000_000:
        return f"${n/1_000_000_000:.2f}B"
    if abs_n >= 1_000_000:
        return f"${n/1_000_000:.2f}M"
    if abs_n >= 1_000:
        return f"${n/1_000:.1f}K"
    return f"${n:.0f}"

# ── Build example messages ──

def example_buy_signal():
    """1. Buy Signal"""
    return format_signal({
        "symbol": "WIF",
        "score": 87.3,
        "liquidity": 4_200_000,
        "volume_24h": 12_500_000,
        "holders": 18420,
        "trend": "Uptrend",
        "entry_type": "Momentum continuation",
        "confidence": "A",
        "regime_label": "RISK_ON",
        "profile": "TACTICAL",
        "change_24h": 24.5,
        "rsi": 62.4,
        "macd_hist": 0.015,
        "market_cap": 890_000_000,
        "price": 1.24,
        "wallet_fit": "W2",
        "risk_plan": "SL -10% | TP +12/+25 | Trail 10%",
        "rotation_plan": "Rotate profits to W1/USDC per plan",
        "last_trade_unix": 1739600000,
    }, compact=False)


def example_runner_watch():
    """2. Runner Watch"""
    return format_runner_watch({
        "symbol": "BUTTCOIN",
        "name": "Buttcoin",
        "age_hours": 6.2,
        "market_cap": 42_000_000,
        "liquidity": 1_800_000,
        "volume_24h": 8_500_000,
        "change_24h": 185.0,
        "txns_h1": 420,
        "watch_score": 78.5,
        "x_proxy_label": "High",
        "narrative_label": "Meme/Culture",
        "note": "Viral meme coin gaining social traction",
        "price": 0.042,
        "wallet_fit": "W3",
        "risk_plan": "Small size only | hard stop | quick scale-outs",
        "rotation_plan": "Rotate wins into W1/W2 or USDC",
    }, compact=False)


def example_watchlist_signal():
    """3. Watchlist Signal"""
    return format_watchlist_signal({
        "symbol": "PSYOPANIME",
        "status": "Momentum",
        "reason": "Price reclaiming key level with volume surge",
        "market_cap": 15_000_000,
        "liquidity": 620_000,
        "volume_24h": 2_100_000,
        "change_24h": 18.5,
        "price": 0.0082,
        "upside_potential": "High",
        "failure_risk": "Medium",
        "wallet_fit": "W3",
    }, compact=False)


def example_watchlist_summary():
    """4. Watchlist Summary"""
    return format_watchlist_summary([
        {"symbol": "BP", "status": "Momentum", "change_24h": 32.5, "liquidity": 1_200_000, "volume_24h": 4_500_000, "has_live_data": True},
        {"symbol": "PSYOPANIME", "status": "Reclaim", "change_24h": 12.3, "liquidity": 620_000, "volume_24h": 2_100_000, "has_live_data": True},
        {"symbol": "SKR", "status": "Range", "change_24h": -2.1, "liquidity": 800_000, "volume_24h": 900_000, "has_live_data": True},
        {"symbol": "BUTTCOIN", "status": "Momentum", "change_24h": 45.0, "liquidity": 1_800_000, "volume_24h": 8_500_000, "has_live_data": True},
        {"symbol": "TESTICLE", "status": "Breakdown", "change_24h": -18.5, "liquidity": 150_000, "volume_24h": 300_000, "has_live_data": True},
        {"symbol": "WOJAK", "status": "Range", "change_24h": 1.2, "liquidity": 400_000, "volume_24h": 500_000, "has_live_data": True},
        {"symbol": "TROLL", "status": "Volatile", "change_24h": 8.7, "liquidity": 550_000, "volume_24h": 1_200_000, "has_live_data": True},
        {"symbol": "CHILLHOUSE", "status": "NoData", "change_24h": None, "liquidity": None, "volume_24h": None, "has_live_data": False},
    ])


def example_sell_alert():
    """5. Sell/Exit Alert"""
    rows = [
        _kv("PRIORITY", "P1"),
        _kv("SIGNAL", "Hype Fade"),
        _kv("PRICE", "$1.24 | 24H -12.40%"),
        _kv("MKT_CAP", "$890.00M"),
        _kv("LIQUIDITY", "$4.20M"),
        _kv("ACTION", "Bad buy / de-risk now"),
        _kv("REASON", "Momentum rolled over (1h -3.2%, 6h -8.1%) and participation faded (vol/liq 0.18)."),
        _kv("INVALID", "Momentum + volume recovery"),
        _kv("DATA_AGE", "Live refresh on alert send"),
    ]
    header = _header_block("[SIGNAL]:SELL", rows=[
        _kv("SYMBOL", "$WIF"),
        _kv("PROFILE", "TACTICAL"),
    ])
    return header + "\n" + _render_pre(rows)


def example_daily_summary():
    """6. Daily Summary"""
    rows = [
        _kv("WINDOW", "24H"),
        _kv("SCANS", "48"),
        _kv("ALERTS", "7"),
        _kv("ALERT_RT", "14.6%"),
        _kv("AVG_SCORE", "78.42"),
        _kv("MAX_SCORE", "91.30"),
        _kv("TOP", "WIF(3), BONK(2), PEPE(1), TRUMP(1)"),
        "-" * 30,
        _kv("RECAP", "24H"),
        "1. $WIF | alerts 3 | 4h +8.42% | win 67% (2/3)",
        "2. $BONK | alerts 2 | 4h +3.10% | win 50% (1/2)",
        "3. $PEPE | alerts 1 | 4h -2.80% | win 0% (0/1)",
        "4. $TRUMP | alerts 1 | 4h +12.50% | win 100% (1/1)",
    ]
    return _header_block("[SIGNAL]:DAILY_SUMMARY") + "\n" + _render_pre(rows)


def example_weekly_tuning():
    """7. Weekly Tuning"""
    rows = [
        _kv("WINDOW", "7D"),
        _kv("SCANS", "336"),
        _kv("BESTSCAN", "87"),
        _kv("ALERTS", "42"),
        _kv("ALERT_RT", "12.5%"),
        _kv("BLOCK_RT", "18.3%"),
        _kv("P50/75/90", "72/81/89"),
        "-" * 30,
        _kv("EDGE_1H", "+2.14%/62%/38"),
        _kv("EDGE_4H", "+4.80%/58%/35"),
        _kv("EDGE_24H", "+8.20%/55%/28"),
        _kv("SIM_4H", "trd 35 exp +1.82%"),
        _kv("SIM_DD", "8.40% eq 1.064"),
        "-" * 30,
        _kv("CUR_THR", "72"),
        _kv("CUR_REG", "35"),
        _kv("CUR_CONF", "B"),
        _kv("NEW_THR", "74"),
        _kv("NEW_REG", "30"),
        _kv("NEW_CONF", "B"),
        _kv("OPT", "thr 74 reg 30 conf B n 420"),
        "-" * 30,
        _kv("RATIONALE", "Edge improving; tightening threshold to filter noise."),
        "- Win rate trending up at 4h horizon",
        "- Volume participation healthy across top picks",
        "- Regime stability supports tighter gates",
    ]
    return _header_block("[SIGNAL]:WEEKLY_TUNING") + "\n" + _render_pre(rows)


def example_analysis():
    """8. Analysis (unified market bulletin)"""
    def _card_row(label, value):
        key = str(label).upper().replace(" ", "_")[:9].ljust(9)
        return f"<code>{_esc(key)} | {_esc(value)}</code>"

    lines = [
        f"<b>[ANALYSIS]: $WIF</b>",
        "<code>------------------------------</code>",
        _card_row("STATUS", "BUY"),
        _card_row("PROFILE", "TACTICAL | Balanced"),
        _card_row("BEST_FIT", "Mid-Term"),
        _card_row("QUALITY", "score 91 (A) | 24h +24.5% | liq $4.20M"),
        _card_row("REASON", "Top-ranked qualified setup right now"),
        _card_row("RULE", "score >= 72 and confidence >= B"),
        "",
        "<i>Tip: Use /goodbuy all for full tier breakdown on demand.</i>",
    ]
    return "\n".join(lines).strip()


ALL_EXAMPLES = [
    ("1/8 BUY SIGNAL", example_buy_signal),
    ("2/8 RUNNER WATCH", example_runner_watch),
    ("3/8 WATCHLIST SIGNAL", example_watchlist_signal),
    ("4/8 WATCHLIST SUMMARY", example_watchlist_summary),
    ("5/8 SELL ALERT", example_sell_alert),
    ("6/8 DAILY SUMMARY", example_daily_summary),
    ("7/8 WEEKLY TUNING", example_weekly_tuning),
    ("8/8 ANALYSIS BULLETIN", example_analysis),
]


async def main():
    bot = Bot(token=TELEGRAM_TOKEN)
    chat_id = int(TELEGRAM_CHAT_ID)

    for label, fn in ALL_EXAMPLES:
        try:
            msg_text = fn()
            result = await bot.send_message(
                chat_id=chat_id,
                text=msg_text,
                parse_mode="HTML",
            )
            print(f"  SENT {label} -> message_id={result.message_id}")
        except Exception as e:
            print(f"  FAIL {label} -> {e}")
        await asyncio.sleep(0.5)

    print("\nDone — all 8 signal types sent.")


if __name__ == "__main__":
    asyncio.run(main())
