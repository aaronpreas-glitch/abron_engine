#!/usr/bin/env python3
"""89c: Fix sent_score UnboundLocalError in execute_perp_signal ML block.

sent_score is assigned at line ~1126 but the ML block at line ~1052 reads it first.
Fix: read from signal dict early before the ML block.
Also reverts the temp logger.error back to logger.debug.
"""
from pathlib import Path
import subprocess

PE = Path("/root/memecoin_engine/utils/perp_executor.py")
text = PE.read_text()

# Fix 1: init sent_score_early before ML block, use it in the ML sig dict
OLD = (
    "    # \u2500\u2500 ML Prediction \u2500\u2500\n"
    "    ml_prediction = None\n"
    "    try:\n"
    "        from utils.ml_predictor import predict_signal as _ml_predict\n"
    "        _ml_sig = dict(signal)\n"
    "        _ml_sig[\"sentiment_score\"] = sent_score\n"
)
assert text.count(OLD) == 1, f"89c-1: expected 1 anchor, found {text.count(OLD)}"

NEW = (
    "    # \u2500\u2500 ML Prediction \u2500\u2500\n"
    "    # sent_score is assigned later (~line 1126); read from signal dict here to avoid UnboundLocalError\n"
    "    sent_score_early = float(signal.get(\"sentiment_score\", 0) or 0)\n"
    "    ml_prediction = None\n"
    "    try:\n"
    "        from utils.ml_predictor import predict_signal as _ml_predict\n"
    "        _ml_sig = dict(signal)\n"
    "        _ml_sig[\"sentiment_score\"] = sent_score_early\n"
)
text = text.replace(OLD, NEW)
assert text.count(NEW) == 1, "89c-1 replacement error"
print("89c-1: sent_score_early fix applied \u2713")

# Fix 2: revert temp logger.error back to logger.debug
OLD2 = (
    "    except Exception as _mle:\n"
    "        logger.error(\"ML predict error (TEMP DEBUG): %s\", _mle, exc_info=True)\n"
)
if text.count(OLD2) == 1:
    NEW2 = (
        "    except Exception as _mle:\n"
        "        logger.debug(\"ML predict error: %s\", _mle)\n"
    )
    text = text.replace(OLD2, NEW2)
    print("89c-2: logger.error reverted to logger.debug \u2713")
else:
    print("89c-2: logger.error already reverted (skipping)")

PE.write_text(text)
r = subprocess.run(["python3", "-m", "py_compile", str(PE)], capture_output=True, text=True)
if r.returncode != 0:
    print("COMPILE ERROR:", r.stderr)
    raise SystemExit(1)
print("89c: compile OK \u2713")
