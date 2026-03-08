#!/usr/bin/env python3
"""Patch 95 — Expand Kraken price fallback to cover all perp altcoins.

Problem: _fetch_price() Kraken fallback only has SOL/BTC/ETH.
CoinGecko is rate-limited (429) because the engine calls it too frequently.
Result: APT/OP/ARB/SUI/TON/AVAX/NEAR/INJ/SEI all fail price fetch → signals skipped.

Fix: Add all 9 altcoins to _KRAKEN_TICKER + use dynamic result key lookup
(avoids hardcoding the XXBT/XETH Kraken key quirks for new pairs).
"""
from pathlib import Path
import subprocess, sys

PX = Path("/root/memecoin_engine/utils/perp_executor.py")
text = PX.read_text()

OLD = '''    # Fallback 2: Kraken public ticker (no API key, no geo-block)
    _KRAKEN_TICKER = {"SOL": "SOLUSD", "BTC": "XBTUSD", "ETH": "ETHUSD"}
    _KRAKEN_KEY    = {"SOL": "SOLUSD", "BTC": "XXBTZUSD", "ETH": "XETHZUSD"}
    try:
        pair = _KRAKEN_TICKER.get(symbol)
        if pair:
            r = requests.get(
                f"https://api.kraken.com/0/public/Ticker?pair={pair}",
                timeout=5,
            )
            result = r.json().get("result", {})
            key    = _KRAKEN_KEY.get(symbol, pair)
            price  = result.get(key, {}).get("c", [None])[0]
            if price:
                logger.debug("Kraken price for %s: %s", symbol, price)
                return float(price)
    except Exception as e:
        logger.debug("Kraken fallback failed for %s: %s", symbol, e)'''

assert text.count(OLD) == 1, f"Patch 95: found {text.count(OLD)} matches for Kraken fallback block"

NEW = '''    # Fallback 2: Kraken public ticker (no API key, no geo-block)
    # Expanded to cover all perp altcoins (Patch 95 — CoinGecko rate-limit workaround)
    _KRAKEN_TICKER = {
        "SOL": "SOLUSD", "BTC": "XBTUSD", "ETH": "ETHUSD",
        "SUI": "SUIUSD", "TON": "TONUSD", "AVAX": "AVAXUSD",
        "ARB": "ARBUSD", "OP": "OPUSD",   "NEAR": "NEARUSD",
        "INJ": "INJUSD", "SEI": "SEIUSD", "APT": "APTUSD",
    }
    try:
        pair = _KRAKEN_TICKER.get(symbol)
        if pair:
            r = requests.get(
                f"https://api.kraken.com/0/public/Ticker?pair={pair}",
                timeout=5,
            )
            result = r.json().get("result", {})
            if result:
                key   = list(result.keys())[0]   # works for all symbols (incl. XXBT/XETH quirks)
                price = result[key].get("c", [None])[0]
                if price:
                    logger.debug("Kraken price for %s: %s", symbol, price)
                    return float(price)
    except Exception as e:
        logger.debug("Kraken fallback failed for %s: %s", symbol, e)'''

text = text.replace(OLD, NEW)
PX.write_text(text)

r = subprocess.run(["python3", "-m", "py_compile", str(PX)], capture_output=True, text=True)
if r.returncode != 0:
    print("COMPILE ERROR:", r.stderr); sys.exit(1)
print("Patch 95: Kraken price fallback expanded to all altcoins ✓")
print("CoinGecko 429 will now fall through to Kraken for APT/OP/ARB/SUI/TON/AVAX/NEAR/INJ/SEI")
