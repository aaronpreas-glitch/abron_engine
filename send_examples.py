"""Send all 10 signal card examples to Telegram for visual review."""

import asyncio
from telegram import Bot
from utils.format import (
    format_buy, format_sell, format_runner, format_watchlist,
    format_watchlist_summary, format_digest, format_analysis,
    format_daily_summary, format_weekly_tuning, format_watchdog,
)

TOKEN = "8434670970:AAGQNka3ED5oR990qc2qKeobdWlOkvnJEVg"
CHAT_ID = 1887678023

SAMPLE_MINT = "7GCihgDB8fe6KNjn2MYtkzZcRjQy3t9GHdC8uHYmW2hr"

async def main():
    bot = Bot(token=TOKEN)

    examples = [
        ("BUY", format_buy({
            "symbol": "BONK",
            "score": 91,
            "price": 0.00002847,
            "change_24h": 18.4,
            "liquidity": 5_200_000,
            "volume_24h": 1_800_000,
            "holders": 14_320,
            "trend": "Uptrend confirmed",
            "entry_type": "28% pullback from 7D high",
            "pullback_depth": 28.0,
            "rs_7d": 12.5,
            "mint": SAMPLE_MINT,
        })),
        ("SELL", format_sell({
            "symbol": "WIF",
            "entry_price": 0.00031200,
            "exit_price": 0.00027456,
            "pnl_pct": -12.0,
            "r_multiple": -1.0,
            "sell_action": "CLOSE FULL POSITION",
            "sell_reason": "Trailing stop triggered",
            "hold_time": "4h 22m",
            "mfe": 8.3,
            "mae": -12.0,
            "mint": SAMPLE_MINT,
        })),
        ("RUNNER", format_runner({
            "symbol": "POPCAT",
            "score": 88,
            "price": 0.00045120,
            "change_24h": 42.6,
            "change_1h": 15.2,
            "liquidity": 3_400_000,
            "volume_24h": 2_900_000,
            "volume_spike": 4.2,
            "breakout_type": "Range breakout with volume",
            "momentum": "Very strong",
            "rs_7d": 31.0,
            "mint": SAMPLE_MINT,
        })),
        ("WATCHLIST", format_watchlist({
            "symbol": "MEW",
            "score": 78,
            "price": 0.00001234,
            "change_24h": 5.2,
            "liquidity": 2_100_000,
            "volume_24h": 890_000,
            "trigger": "Pullback into 25-35% zone",
            "conviction": "High",
            "notes": "Strong RS, waiting for dip",
            "mint": SAMPLE_MINT,
        })),
        ("WATCHLIST SUMMARY", format_watchlist_summary([
            {"symbol": "BONK", "score": 91, "change_24h": 18.4, "liquidity": 5_200_000},
            {"symbol": "MEW", "score": 78, "change_24h": 5.2, "liquidity": 2_100_000},
            {"symbol": "POPCAT", "score": 88, "change_24h": 42.6, "liquidity": 3_400_000},
            {"symbol": "WEN", "score": 72, "change_24h": -3.1, "liquidity": 1_500_000},
        ])),
        ("DIGEST", format_digest({
            "scanned": 247,
            "passed": 12,
            "regime_label": "Bullish",
            "regime_score": 78,
            "top_tokens": [
                {"symbol": "BONK", "score": 91, "change_24h": 18.4},
                {"symbol": "POPCAT", "score": 88, "change_24h": 42.6},
                {"symbol": "MEW", "score": 78, "change_24h": 5.2},
            ],
        })),
        ("ANALYSIS", format_analysis({
            "symbol": "BONK",
            "score": 91,
            "price": 0.00002847,
            "liquidity": 5_200_000,
            "volume_24h": 1_800_000,
            "change_24h": 18.4,
            "breakdown": {
                "trend": 20,
                "pullback": 20,
                "relative_strength": 15,
                "liquidity": 15,
                "volume": 12,
                "regime": 15,
                "volatility": 7,
                "sentiment": 8,
            },
            "mint": SAMPLE_MINT,
        })),
        ("DAILY SUMMARY", format_daily_summary({
            "signals_today": 7,
            "buys": 3,
            "sells": 2,
            "runners": 2,
            "win_rate": 66.7,
            "total_pnl": 4.2,
            "best_trade": {"symbol": "BONK", "pnl_pct": 28.5},
            "worst_trade": {"symbol": "WIF", "pnl_pct": -12.0},
            "regime_label": "Bullish",
            "regime_score": 78,
        })),
        ("WEEKLY TUNING", format_weekly_tuning({
            "period": "Feb 8 - Feb 15, 2026",
            "total_trades": 18,
            "win_rate": 61.1,
            "avg_r": 1.4,
            "tier_a": 6,
            "tier_a_wr": 83.3,
            "tier_b": 8,
            "tier_b_wr": 62.5,
            "tier_c": 4,
            "tier_c_wr": 25.0,
            "recommendations": "  Raise threshold to 80\n  Below-75 trades losing money",
        })),
        ("WATCHDOG", format_watchdog({
            "symbol": "SCAM",
            "severity": "HIGH",
            "alert_type": "Liquidity drain detected",
            "details": "Liquidity dropped 45% in 2 hours\nLP tokens moving to new wallet",
            "liquidity": 320_000,
            "liquidity_change": -45.0,
            "holders": 892,
            "mint": SAMPLE_MINT,
        })),
    ]

    for name, msg in examples:
        print(f"Sending {name}...")
        result = await bot.send_message(
            chat_id=CHAT_ID,
            text=msg,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        print(f"  -> message_id: {result.message_id}")
        await asyncio.sleep(1.5)  # avoid rate limits

    print("\nAll 10 examples sent!")

asyncio.run(main())
