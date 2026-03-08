#!/usr/bin/env python3
"""Patch 91d — Replace aiohttp with requests in _research_agent_loop.

aiohttp is not installed on VPS. The existing scan loops use `requests`
(sync, called in async context via asyncio.to_thread or directly).
"""
from pathlib import Path
import subprocess

MAIN = Path("/root/memecoin_engine/dashboard/backend/main.py")
text = MAIN.read_text()

OLD = (
    "    import sqlite3 as _sql3r\n"
    "    import aiohttp as _aior\n"
    "    from datetime import timedelta as _tdr\n"
    "    _dbr = str(Path(__file__).resolve().parent.parent.parent / \"data_storage\" / \"engine.db\")\n"
    "    # local Kraken pair map (same as scan loops)\n"
    "    _RES_PAIRS = {"
)
assert text.count(OLD) == 1, f"anchor count: {text.count(OLD)}"

NEW = (
    "    import sqlite3 as _sql3r\n"
    "    import requests as _req_r\n"
    "    from datetime import timedelta as _tdr\n"
    "    _dbr = str(Path(__file__).resolve().parent.parent.parent / \"data_storage\" / \"engine.db\")\n"
    "    # local Kraken pair map (same as scan loops)\n"
    "    _RES_PAIRS = {"
)
text = text.replace(OLD, NEW)
assert text.count(NEW) == 1, "replacement error"
print("Step 1: replaced aiohttp import with requests ✓")

# Replace the async aiohttp session block with sync requests calls
OLD2 = (
    "            async with _aior.ClientSession(timeout=_aior.ClientTimeout(total=15)) as sess:\n"
    "                for sym, (pair, rkey) in _RES_PAIRS.items():\n"
    "                    try:\n"
    "                        url = f\"https://api.kraken.com/0/public/OHLC?pair={pair}&interval=240\"\n"
    "                        async with sess.get(url) as r:\n"
    "                            jd = await r.json()\n"
    "                        candles = list(jd.get(\"result\", {}).values())[0] if jd.get(\"result\") else []"
)
assert text.count(OLD2) == 1, f"step2 anchor count: {text.count(OLD2)}"
NEW2 = (
    "            for sym, (pair, rkey) in _RES_PAIRS.items():\n"
    "                try:\n"
    "                    url = f\"https://api.kraken.com/0/public/OHLC?pair={pair}&interval=240\"\n"
    "                    _rr = _req_r.get(url, timeout=8)\n"
    "                    jd = _rr.json()\n"
    "                    candles = list(jd.get(\"result\", {}).values())[0] if jd.get(\"result\") else []"
)
text = text.replace(OLD2, NEW2)
assert text.count(NEW2) == 1, "step2 replacement error"
print("Step 2: replaced async aiohttp session with sync requests ✓")

# Fix indentation of the inner try body (was indented for the `async with sess` block)
# The `continue` and rest of symbol loop body need one less level of indentation
OLD3 = (
    "                    if len(candles) < 20:\n"
    "                            continue\n"
    "                        rsi_val = _compute_rsi(candles)\n"
    "                        macd_val = _compute_macd(candles)\n"
    "                        macd_h = round(macd_val[\"histogram\"], 4) if macd_val else None\n"
    "                        closes = [float(c[4]) for c in candles]\n"
    "                        chg_4h = round((closes[-2] - closes[-3]) / closes[-3] * 100, 2) if len(closes) >= 3 else 0.0\n"
    "                        rsi_r = round(rsi_val, 1) if rsi_val is not None else \"?\"\n"
    "                        regime = \"BEAR\" if (rsi_val or 50) < 40 else \"BULL\" if (rsi_val or 50) > 60 else \"NEUTRAL\"\n"
    "                        lines.append(f\"| {sym:<5} | {chg_4h:+.2f}% | {rsi_r} | {macd_h} | {regime} |\")\n"
    "                    except Exception:\n"
    "                        continue"
)
if text.count(OLD3) == 1:
    NEW3 = (
        "                    if len(candles) < 20:\n"
        "                        continue\n"
        "                    rsi_val = _compute_rsi(candles)\n"
        "                    macd_val = _compute_macd(candles)\n"
        "                    macd_h = round(macd_val[\"histogram\"], 4) if macd_val else None\n"
        "                    closes = [float(c[4]) for c in candles]\n"
        "                    chg_4h = round((closes[-2] - closes[-3]) / closes[-3] * 100, 2) if len(closes) >= 3 else 0.0\n"
        "                    rsi_r = round(rsi_val, 1) if rsi_val is not None else \"?\"\n"
        "                    regime = \"BEAR\" if (rsi_val or 50) < 40 else \"BULL\" if (rsi_val or 50) > 60 else \"NEUTRAL\"\n"
        "                    lines.append(f\"| {sym:<5} | {chg_4h:+.2f}% | {rsi_r} | {macd_h} | {regime} |\")\n"
        "                except Exception:\n"
        "                    continue"
    )
    text = text.replace(OLD3, NEW3)
    print("Step 3: fixed inner loop indentation ✓")
else:
    print(f"Step 3: inner loop anchor count={text.count(OLD3)} (may already be correct, skipping)")

MAIN.write_text(text)
r = subprocess.run(["python3", "-m", "py_compile", str(MAIN)], capture_output=True, text=True)
if r.returncode != 0:
    print("COMPILE ERROR:", r.stderr)
    raise SystemExit(1)
print("Patch 91d: compile OK ✓")
