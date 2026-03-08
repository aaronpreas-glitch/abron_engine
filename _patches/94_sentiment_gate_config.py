#!/usr/bin/env python3
"""Patch 94 — Make sentiment quality gate configurable.

Problem: sent_boost < 5 gate blocks ALL signals when ML predictor runs (win_prob > 0)
because x_sentiment returns boost=0 for most tokens (no API key / no trending data).

Fix:
  1. utils/perp_executor.py: add SENT_GATE_MIN_BOOST lambda (default=-100, i.e. disabled)
     and replace hardcoded `< 5` with `< SENT_GATE_MIN_BOOST()`
  2. dashboard/backend/main.py: add SENT_GATE_MIN_BOOST to ALLOWED_KEYS
  3. .env: set SENT_GATE_MIN_BOOST=-100 (persist)
"""
from pathlib import Path
import subprocess, sys

# ─────────────────────────────────────────────────────────────────────────────
# 1. utils/perp_executor.py
# ─────────────────────────────────────────────────────────────────────────────
PX = Path("/root/memecoin_engine/utils/perp_executor.py")
text = PX.read_text()

# 1a. Add SENT_GATE_MIN_BOOST lambda after SCALP_ML_MIN_WIN_PROB
OLD1 = 'SCALP_ML_MIN_WIN_PROB = lambda: _float("SCALP_ML_MIN_WIN_PROB", 0.78)  # Per-scalp ML quality gate (default: require 78% win prob)   # skip SCALP in sideways regime'
assert text.count(OLD1) == 1, f"Step 1a: found {text.count(OLD1)} matches"
NEW1 = (
    'SCALP_ML_MIN_WIN_PROB = lambda: _float("SCALP_ML_MIN_WIN_PROB", 0.78)  # Per-scalp ML quality gate (default: require 78% win prob)   # skip SCALP in sideways regime\n'
    'SENT_GATE_MIN_BOOST   = lambda: _float("SENT_GATE_MIN_BOOST", -100.0)  # Sentiment gate threshold; -100 = disabled (sentiment service optional)'
)
text = text.replace(OLD1, NEW1)
print("Step 1a: SENT_GATE_MIN_BOOST lambda added ✓")

# 1b. Replace hardcoded sent_boost < 5 with configurable
OLD2 = '        if sent_boost < 5:\n            return _skip("LOW_SENTIMENT")'
assert text.count(OLD2) == 1, f"Step 1b: found {text.count(OLD2)} matches"
NEW2 = '        if sent_boost < SENT_GATE_MIN_BOOST():\n            return _skip("LOW_SENTIMENT")'
text = text.replace(OLD2, NEW2)
print("Step 1b: sentiment gate now uses SENT_GATE_MIN_BOOST() ✓")

PX.write_text(text)
r = subprocess.run(["python3", "-m", "py_compile", str(PX)], capture_output=True, text=True)
if r.returncode != 0:
    print("COMPILE ERROR:", r.stderr); sys.exit(1)
print("perp_executor.py compile OK ✓")

# ─────────────────────────────────────────────────────────────────────────────
# 2. dashboard/backend/main.py — add SENT_GATE_MIN_BOOST to ALLOWED_KEYS
# ─────────────────────────────────────────────────────────────────────────────
MAIN = Path("/root/memecoin_engine/dashboard/backend/main.py")
text = MAIN.read_text()

OLD3 = '    "SCALP_ML_MIN_WIN_PROB",\n    }'
assert text.count(OLD3) == 1, f"Step 2: found {text.count(OLD3)} matches"
NEW3 = '    "SCALP_ML_MIN_WIN_PROB",\n    "SENT_GATE_MIN_BOOST",\n    }'
text = text.replace(OLD3, NEW3)
print("Step 2: SENT_GATE_MIN_BOOST added to ALLOWED_KEYS ✓")

MAIN.write_text(text)
r = subprocess.run(["python3", "-m", "py_compile", str(MAIN)], capture_output=True, text=True)
if r.returncode != 0:
    print("COMPILE ERROR:", r.stderr); sys.exit(1)
print("main.py compile OK ✓")

# ─────────────────────────────────────────────────────────────────────────────
# 3. .env
# ─────────────────────────────────────────────────────────────────────────────
import re
ENV = Path("/root/memecoin_engine/.env")
env_text = ENV.read_text()

if "SENT_GATE_MIN_BOOST" not in env_text:
    env_text += "\nSENT_GATE_MIN_BOOST=-100.0\n"
    print("Step 3: SENT_GATE_MIN_BOOST=-100.0 added to .env ✓")
else:
    env_text = re.sub(r'^SENT_GATE_MIN_BOOST=.*$', 'SENT_GATE_MIN_BOOST=-100.0',
                      env_text, flags=re.MULTILINE)
    print("Step 3: SENT_GATE_MIN_BOOST=-100.0 updated in .env ✓")

ENV.write_text(env_text)
print("Patch 94: Sentiment gate now configurable — default DISABLED (-100). Trades should flow. ✓")
