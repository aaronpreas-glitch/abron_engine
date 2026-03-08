"""
Patch 183 — Multi-Modal Tuner: exhaustive window search for bimodal score bands

Root cause:
  _tune_thresholds_step() uses a cumulative-above search (scores >= threshold).
  This means:
    - It picks threshold=20 because everything-above-20 has the best cumulative WR
    - Then max_score ceiling loop starts at 25 and finds score>25 cohort has WR<40%
      (dead zone 50-80 drags down the whole above-25 set)
    - Result: tuner outputs min_score=20, max_score=25 — locks out the 40-44 band

  Reality (852 samples):
    Band A: score 20-24  n=68   WR=71%  avg=+38%  ← tuner finds this
    Dead zone: 25-39   WR mixed, 40-49 has second peak
    Band B: score 40-44  n=104  WR=67%  avg=+28%  ← tuner completely misses this

Fix:
  Replace cumulative search with exhaustive 5-point window search.
  Try all (lo, hi) pairs with width 5/10/15, min n=10 per window.
  Rank by expectancy = WR% × avg_4h_return (positive edge only).
  Select top 3 non-overlapping windows via greedy pass.

  Output:
    thresholds.min_score / max_score = best single band (backward compat)
    bands[]             = list of top non-overlapping windows
    multi_band_mode     = True if ≥2 viable bands with WR>50% and positive expectancy

  _auto_buy_step() gate updated:
    multi_band_mode=True → accept signal if score in ANY of the bands
    multi_band_mode=False → original min/max logic (unchanged)
"""
import os
import py_compile

MM_PATH = "/root/memecoin_engine/utils/memecoin_manager.py"

with open(MM_PATH) as f:
    src = f.read()


# ── Replacement A: Replace cumulative floor search with exhaustive window ──────
OLD_A = (
    "    # Optimal score threshold — Patch 132\n"
    "    # Search the FULL score range (not just 55-90) and find the floor that\n"
    "    # maximises win rate. Win = 4h return > 0 (any profit — >30% too strict\n"
    "    # at small sample sizes). Data shows low-score tokens outperform high-score\n"
    "    # tokens, so we must search downward too; don't assume high score = better.\n"
    "    best_score_min = 30   # conservative default — cast wide until data settles\n"
    "    best_wr = 0.0\n"
    "    for threshold in range(20, 76, 5):\n"
    "        above = [r for r in rows if (r[\"score\"] or 0) >= threshold]\n"
    "        if len(above) >= 5:\n"
    "            wr = sum(1 for r in above if (r[\"return_4h_pct\"] or 0) > 0) / len(above)\n"
    "            if wr > best_wr:\n"
    "                best_wr = wr\n"
    "                best_score_min = threshold\n"
)

NEW_A = (
    "    # Patch 183: Exhaustive 5-point window search — replaces cumulative floor\n"
    "    # search (Patch 132). Cumulative search missed the bimodal 40-44 peak by\n"
    "    # setting max_score=25 too eagerly. Exhaustive windows find disconnected\n"
    "    # high-quality score bands independently.\n"
    "    #\n"
    "    # Try all (lo, hi) windows with widths 5/10/15 pt, min n=10 per window.\n"
    "    # Rank by expectancy = WR% × avg_4h_return (real edge, both must be +ve).\n"
    "    # Greedy non-overlap pass selects top 3 independent viable bands.\n"
    "    _all_windows: list = []\n"
    "    for _lo in range(0, 80, 5):\n"
    "        for _width in (5, 10, 15):\n"
    "            _hi = _lo + _width\n"
    "            _win = [r for r in rows if _lo <= (r[\"score\"] or 0) < _hi]\n"
    "            if len(_win) < 10:\n"
    "                continue\n"
    "            _wr = sum(1 for r in _win if (r[\"return_4h_pct\"] or 0) > 0) / len(_win)\n"
    "            _avg = sum((r[\"return_4h_pct\"] or 0) for r in _win) / len(_win)\n"
    "            # Expectancy: only count windows with positive avg (true edge)\n"
    "            _exp = (_wr * 100) * _avg if _avg > 0 else -999.0\n"
    "            _all_windows.append({\n"
    "                \"lo\": _lo, \"hi\": _hi, \"n\": len(_win),\n"
    "                \"wr\": round(_wr * 100, 1),\n"
    "                \"avg_4h\": round(_avg, 2),\n"
    "                \"expectancy\": round(_exp, 2),\n"
    "            })\n"
    "\n"
    "    # Greedy non-overlapping band selection\n"
    "    _all_windows.sort(key=lambda w: w[\"expectancy\"], reverse=True)\n"
    "    _top_bands: list = []\n"
    "    for _w in _all_windows:\n"
    "        if not any(_w[\"lo\"] < _b[\"hi\"] and _w[\"hi\"] > _b[\"lo\"] for _b in _top_bands):\n"
    "            _top_bands.append(_w)\n"
    "        if len(_top_bands) >= 3:\n"
    "            break\n"
    "\n"
    "    # Best single band → backward compat min_score/max_score\n"
    "    _best = _top_bands[0] if _top_bands else None\n"
    "    best_score_min = _best[\"lo\"] if _best else 30\n"
    "    best_score_max = (_best[\"hi\"] - 1) if _best else 999\n"
    "\n"
    "    # Multi-band mode: ≥2 viable bands with WR>50% and positive expectancy\n"
    "    _viable_bands = [b for b in _top_bands if b[\"wr\"] > 50.0 and b[\"expectancy\"] > 0]\n"
    "    multi_band_mode = len(_viable_bands) >= 2\n"
)

assert OLD_A in src, "Anchor A not found — check memecoin_manager.py tuner section"
src = src.replace(OLD_A, NEW_A, 1)
print("Step A: Replaced cumulative floor search with exhaustive window search")


# ── Replacement B: Replace ceiling search + thresholds + payload ───────────────
OLD_B = (
    "    # Max score ceiling — tokens scoring too high are over-excited/FOMO and dump\n"
    "    # Find the lowest ceiling above which WR drops below 40%\n"
    "    best_score_max = 999  # no ceiling by default\n"
    "    for ceiling in range(best_score_min + 5, 80, 5):\n"
    "        above = [r for r in rows if (r[\"score\"] or 0) > ceiling]\n"
    "        if len(above) < 5:\n"
    "            break\n"
    "        wr = sum(1 for r in above if (r[\"return_4h_pct\"] or 0) > 0) / len(above)\n"
    "        if wr < 0.40:\n"
    "            best_score_max = ceiling\n"
    "            break\n"
    "\n"
    "    thresholds = {\n"
    "        \"min_score\":            best_score_min,\n"
    "        \"max_score\":            best_score_max,\n"
    "        \"min_vol_acceleration\": best_vacc,\n"
    "        \"max_top_holder_pct\":   best_holder_max,\n"
    "    }\n"
    "\n"
    "    confidence = \"low\" if total < 50 else \"medium\" if total < 200 else \"high\"\n"
    "\n"
    "    payload = {\n"
    "        \"thresholds\":  thresholds,\n"
    "        \"sample_size\": total,\n"
    "        \"win_rate\":    win_rate,\n"
    "        \"rug_stats\":   rug_stats,\n"
    "        \"updated_at\":  ts_now,\n"
    "        \"confidence\":  confidence,\n"
    "    }\n"
)

NEW_B = (
    "    # Patch 183: ceiling search replaced by exhaustive window search above.\n"
    "    # best_score_min and best_score_max already set from _top_bands.\n"
    "\n"
    "    thresholds = {\n"
    "        \"min_score\":            best_score_min,\n"
    "        \"max_score\":            best_score_max,\n"
    "        \"min_vol_acceleration\": best_vacc,\n"
    "        \"max_top_holder_pct\":   best_holder_max,\n"
    "    }\n"
    "\n"
    "    confidence = \"low\" if total < 50 else \"medium\" if total < 200 else \"high\"\n"
    "\n"
    "    payload = {\n"
    "        \"thresholds\":      thresholds,\n"
    "        \"bands\":           _top_bands,           # Patch 183: multi-modal bands\n"
    "        \"multi_band_mode\": multi_band_mode,       # Patch 183: True if ≥2 viable bands\n"
    "        \"sample_size\":     total,\n"
    "        \"win_rate\":        win_rate,\n"
    "        \"rug_stats\":       rug_stats,\n"
    "        \"updated_at\":      ts_now,\n"
    "        \"confidence\":      confidence,\n"
    "    }\n"
)

assert OLD_B in src, "Anchor B not found — check tuner ceiling/thresholds/payload section"
src = src.replace(OLD_B, NEW_B, 1)
print("Step B: Updated thresholds + payload to include bands and multi_band_mode")


# ── Replacement C: Update append_memory to include multi-band info ─────────────
OLD_C = (
    "        orchestrator.append_memory(\n"
    "            \"memecoin_scan\",\n"
    "            f\"TUNE {total} samples | win={win_rate:.0f}% | \"\n"
    "            f\"score_min={best_score_min} vacc_min={best_vacc:.0f}% \"\n"
    "            f\"confidence={confidence}\",\n"
    "        )\n"
)

NEW_C = (
    "        _band_summary = \" + \".join(\n"
    "            f\"{b['lo']}-{b['hi']}(WR={b['wr']:.0f}%)\"\n"
    "            for b in _top_bands\n"
    "        )\n"
    "        orchestrator.append_memory(\n"
    "            \"memecoin_scan\",\n"
    "            f\"TUNE {total} samples | win={win_rate:.0f}% | \"\n"
    "            f\"bands={_band_summary} | multi={multi_band_mode} | \"\n"
    "            f\"confidence={confidence}\",\n"
    "        )\n"
)

assert OLD_C in src, "Anchor C not found — check append_memory call"
src = src.replace(OLD_C, NEW_C, 1)
print("Step C: Updated append_memory to log multi-band info")


# ── Replacement D: Add bands variables to _auto_buy_step() init ───────────────
OLD_D = (
    "    max_score       = 999    # no ceiling unless tuner has data\n"
    "    vacc_min        = 5.0\n"
    "    holder_max      = 35.0\n"
)

NEW_D = (
    "    max_score       = 999    # no ceiling unless tuner has data\n"
    "    vacc_min        = 5.0\n"
    "    holder_max      = 35.0\n"
    "    bands           = []     # Patch 183: multi-modal score bands from tuner\n"
    "    multi_band_mode = False  # Patch 183: True when ≥2 viable disconnected bands found\n"
)

assert OLD_D in src, "Anchor D not found — check _auto_buy_step() init block"
src = src.replace(OLD_D, NEW_D, 1)
print("Step D: Added bands + multi_band_mode init vars to _auto_buy_step()")


# ── Replacement E: Extract bands from kv_store in _auto_buy_step() ─────────────
OLD_E = (
    "            if lt.get(\"confidence\") in (\"medium\", \"high\"):\n"
    "                t = lt.get(\"thresholds\", {})\n"
    "                threshold  = float(t.get(\"min_score\",            threshold))\n"
    "                max_score  = float(t.get(\"max_score\",            999))\n"
    "                vacc_min   = float(t.get(\"min_vol_acceleration\", vacc_min))\n"
    "                holder_max = float(t.get(\"max_top_holder_pct\",   holder_max))\n"
)

NEW_E = (
    "            if lt.get(\"confidence\") in (\"medium\", \"high\"):\n"
    "                t = lt.get(\"thresholds\", {})\n"
    "                threshold       = float(t.get(\"min_score\",            threshold))\n"
    "                max_score       = float(t.get(\"max_score\",            999))\n"
    "                vacc_min        = float(t.get(\"min_vol_acceleration\", vacc_min))\n"
    "                holder_max      = float(t.get(\"max_top_holder_pct\",   holder_max))\n"
    "                bands           = lt.get(\"bands\", [])            # Patch 183\n"
    "                multi_band_mode = bool(lt.get(\"multi_band_mode\", False))  # Patch 183\n"
)

assert OLD_E in src, "Anchor E not found — check _auto_buy_step() kv_store loading"
src = src.replace(OLD_E, NEW_E, 1)
print("Step E: _auto_buy_step() now extracts bands + multi_band_mode from tuner")


# ── Replacement F: Update signal gate to support multi-band ───────────────────
OLD_F = (
    "        if score < threshold:\n"
    "            break          # sorted descending — nothing better below\n"
    "        if score > max_score:\n"
    "            continue       # over-excited token — historically poor WR above ceiling\n"
)

NEW_F = (
    "        # Patch 183: multi-band gate — prefer disconnected band membership check\n"
    "        if multi_band_mode and bands:\n"
    "            _min_lo = min(b[\"lo\"] for b in bands)\n"
    "            if score < _min_lo:\n"
    "                break      # below all bands — sorted descending, done\n"
    "            if not any(b[\"lo\"] <= score < b[\"hi\"] for b in bands):\n"
    "                continue   # dead zone between bands — skip\n"
    "        else:\n"
    "            if score < threshold:\n"
    "                break          # sorted descending — nothing better below\n"
    "            if score > max_score:\n"
    "                continue       # over-excited token — historically poor WR above ceiling\n"
)

assert OLD_F in src, "Anchor F not found — check _auto_buy_step() signal loop gate"
src = src.replace(OLD_F, NEW_F, 1)
print("Step F: Signal gate updated to use multi-band membership when available")


# Write and verify
with open(MM_PATH, "w") as f:
    f.write(src)

py_compile.compile(MM_PATH, doraise=True)
print(f"memecoin_manager.py — py_compile: OK")
print("\nPatch 183 applied successfully.")
print("  A. Exhaustive 5-point window search replaces cumulative floor search")
print("  B. thresholds + payload now include bands[] and multi_band_mode")
print("  C. append_memory logs multi-band band summary")
print("  D. _auto_buy_step() init: bands=[] multi_band_mode=False")
print("  E. _auto_buy_step() extracts bands + multi_band_mode from kv_store")
print("  F. Signal gate: uses band membership check when multi_band_mode=True")
