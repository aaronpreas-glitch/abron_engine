"""
Patch 184 — Tuner horizon comparison: 4h vs 24h optimization

Question answered:
  The P183 multi-modal tuner runs exhaustive window search on return_4h_pct.
  The memecoin strategy was observed to behave more like a 24h hold than a 4h
  one. Does optimizing on 4h outcomes select the right score bands?

Implementation:
  - Adds _exhaustive_bands() helper inside _run() of /api/memecoins/score-analysis
    (same algorithm as P183 tuner, but parameterised on the return column)
  - Runs the search on BOTH return_4h_pct and return_24h_pct against the
    same set of rows (those with both outcomes filled — n≈717+ at patch time)
  - Returns horizon_comparison object:
      n_both             — rows with both 4h and 24h outcomes
      bands_4h           — top 3 non-overlapping windows by 4h expectancy
      bands_24h          — top 3 non-overlapping windows by 24h expectancy
      bands_missed_by_4h — 24h viable bands (WR>50%, exp>0) not in 4h top bands
      verdict            — SWITCH_RECOMMENDED | ALIGNED | INSUFFICIENT_DATA
  - Exposes P183 score_bands + multi_band_mode in the tuner object
    (were stored in kv_store but not surfaced by the score-analysis endpoint)

No runtime behavior changes.
No tuner config flip.
Frontend (MemecoinsPage.tsx) was pre-built for this output — renders
horizon_comparison section automatically when present.

Files changed:
  dashboard/backend/routers/memecoins.py
"""
import os
import py_compile

MR_PATH = "/root/memecoin_engine/dashboard/backend/routers/memecoins.py"

with open(MR_PATH) as f:
    src = f.read()


# ── Replacement A: Add _exhaustive_bands helper inside score-analysis _run() ──
# Inserts before the `from utils.db import get_conn` line that is unique to
# the score-analysis endpoint's _run() (analytics endpoint uses sqlite3 directly).

OLD_A = (
    "    def _run():\n"
    "        from utils.db import get_conn  # type: ignore\n"
    "        with get_conn() as conn:\n"
    "\n"
    "            # ── 5-point score bands ───────────────────────────────────────────\n"
)

NEW_A = (
    "    def _run():\n"
    "        # P184: Exhaustive window search helper — same algorithm as P183 tuner\n"
    "        # but parameterised on ret_key so it works for 4h and 24h returns.\n"
    "        def _exhaustive_bands(rows_data, ret_key, min_n=10):\n"
    "            \"\"\"Try all (lo,hi) windows widths 5/10/15, min n per window.\n"
    "            Rank by expectancy = WR% * avg_return (positive edge only).\n"
    "            Greedy non-overlapping pass returns top 3 independent bands.\"\"\"\n"
    "            all_wins = []\n"
    "            for lo in range(0, 80, 5):\n"
    "                for width in (5, 10, 15):\n"
    "                    hi = lo + width\n"
    "                    win = [\n"
    "                        r for r in rows_data\n"
    "                        if r[\"score\"] is not None and lo <= r[\"score\"] < hi\n"
    "                        and r[ret_key] is not None\n"
    "                    ]\n"
    "                    if len(win) < min_n:\n"
    "                        continue\n"
    "                    rets = [float(r[ret_key]) for r in win]\n"
    "                    wr   = sum(1 for x in rets if x > 0) / len(rets)\n"
    "                    avg  = sum(rets) / len(rets)\n"
    "                    exp  = (wr * 100) * avg if avg > 0 else -999.0\n"
    "                    all_wins.append({\n"
    "                        \"lo\": lo, \"hi\": hi, \"n\": len(win),\n"
    "                        \"wr\": round(wr * 100, 1),\n"
    "                        \"avg_ret\": round(avg, 2),\n"
    "                        \"expectancy\": round(exp, 2),\n"
    "                    })\n"
    "            all_wins.sort(key=lambda w: w[\"expectancy\"], reverse=True)\n"
    "            top = []\n"
    "            for w in all_wins:\n"
    "                if not any(w[\"lo\"] < b[\"hi\"] and w[\"hi\"] > b[\"lo\"] for b in top):\n"
    "                    top.append(w)\n"
    "                if len(top) >= 3:\n"
    "                    break\n"
    "            return top\n"
    "\n"
    "        from utils.db import get_conn  # type: ignore\n"
    "        with get_conn() as conn:\n"
    "\n"
    "            # ── 5-point score bands ───────────────────────────────────────────\n"
)

assert OLD_A in src, "Anchor A not found — check score-analysis _run() opening"
src = src.replace(OLD_A, NEW_A, 1)
print("Step A: Added _exhaustive_bands helper inside score-analysis _run()")


# ── Replacement B: Expose P183 score_bands + multi_band_mode in tuner object ──

OLD_B = (
    "                    tuner = {\n"
    "                        \"min_score\":   t_data[\"thresholds\"].get(\"min_score\"),\n"
    "                        \"max_score\":   t_data[\"thresholds\"].get(\"max_score\"),\n"
    "                        \"confidence\":  t_data.get(\"confidence\"),\n"
    "                        \"sample_size\": t_data.get(\"sample_size\"),\n"
    "                        \"win_rate\":    t_data.get(\"win_rate\"),\n"
    "                        \"updated_at\":  t_data.get(\"updated_at\"),\n"
    "                    }\n"
)

NEW_B = (
    "                    tuner = {\n"
    "                        \"min_score\":       t_data[\"thresholds\"].get(\"min_score\"),\n"
    "                        \"max_score\":       t_data[\"thresholds\"].get(\"max_score\"),\n"
    "                        \"confidence\":      t_data.get(\"confidence\"),\n"
    "                        \"sample_size\":     t_data.get(\"sample_size\"),\n"
    "                        \"win_rate\":        t_data.get(\"win_rate\"),\n"
    "                        \"updated_at\":      t_data.get(\"updated_at\"),\n"
    "                        \"score_bands\":     t_data.get(\"bands\", []),                    # P183\n"
    "                        \"multi_band_mode\": bool(t_data.get(\"multi_band_mode\", False)), # P183\n"
    "                    }\n"
)

assert OLD_B in src, "Anchor B not found — check tuner dict in score-analysis _run()"
src = src.replace(OLD_B, NEW_B, 1)
print("Step B: score_bands + multi_band_mode now surfaced in tuner object")


# ── Replacement C: Insert horizon_comparison computation + update return dict ──
# Replaces the verdict section + return dict as one block so the horizon
# computation is inserted right before the verdict and the return dict is
# extended with horizon_comparison.

OLD_C = (
    "            # ── Verdict ───────────────────────────────────────────────────────\n"
    "            # Compare config_min performance vs all-signals baseline\n"
    "            config_sim = next((x for x in threshold_sim if x[\"threshold\"] == config_min), None)\n"
    "            baseline   = next((x for x in threshold_sim if x[\"threshold\"] == 0), None)\n"
    "            config_wr  = config_sim[\"wr\"]  if config_sim else 0.0\n"
    "            baseline_wr = baseline[\"wr\"]   if baseline  else 0.0\n"
    "\n"
    "            if config_wr < baseline_wr - 10:\n"
    "                verdict_label = \"MISALIGNED\"\n"
    "                verdict_msg = (\n"
    "                    f\"ENV gate (score\\u2265{config_min}) WR={config_wr:.0f}% is \"\n"
    "                    f\"{baseline_wr - config_wr:.0f}pp BELOW the no-gate baseline \"\n"
    "                    f\"({baseline_wr:.0f}%). Raising the threshold is actively \"\n"
    "                    f\"selecting worse signals. Tuner recommends score \"\n"
    "                    f\"{tuner['min_score']}-{tuner['max_score']} \"\n"
    "                    f\"({tuner['confidence']} confidence, {tuner['sample_size']} samples).\"\n"
    "                    if tuner else\n"
    "                    f\"ENV gate (score\\u2265{config_min}) performs worse than no gate. Lower the threshold.\"\n"
    "                )\n"
    "            elif config_wr < 30:\n"
    "                verdict_label = \"SUBOPTIMAL\"\n"
    "                verdict_msg = (\n"
    "                    f\"ENV gate (score\\u2265{config_min}) WR={config_wr:.0f}% \\u2014 \"\n"
    "                    \"low but not inverting. Consider lowering toward 25-40 range.\"\n"
    "                )\n"
    "            else:\n"
    "                verdict_label = \"CALIBRATED\"\n"
    "                verdict_msg = f\"ENV gate (score\\u2265{config_min}) WR={config_wr:.0f}% \\u2014 acceptable.\"\n"
    "\n"
    "            return {\n"
    "                \"config_score_min\": config_min,\n"
    "                \"bands\":            bands,\n"
    "                \"threshold_sim\":    threshold_sim,\n"
    "                \"bought_split\":     bought_split,\n"
    "                \"optimal_window\":   best_window,\n"
    "                \"window_results\":   window_results,\n"
    "                \"tuner\":            tuner,\n"
    "                \"verdict\":          {\"label\": verdict_label, \"message\": verdict_msg},\n"
    "            }\n"
)

NEW_C = (
    "            # ── P184: Horizon comparison — 4h vs 24h exhaustive band search ──\n"
    "            # Fetches rows with BOTH returns filled. Runs _exhaustive_bands on\n"
    "            # each horizon independently and compares top bands.\n"
    "            # No rug filter — matches what _tune_thresholds_step() trains on.\n"
    "            horizon_comparison = None\n"
    "            try:\n"
    "                dual_rows = conn.execute(\"\"\"\n"
    "                    SELECT score, return_4h_pct, return_24h_pct\n"
    "                    FROM memecoin_signal_outcomes\n"
    "                    WHERE return_4h_pct  IS NOT NULL\n"
    "                      AND return_24h_pct IS NOT NULL\n"
    "                      AND score          IS NOT NULL\n"
    "                \"\"\").fetchall()\n"
    "                n_both = len(dual_rows)\n"
    "\n"
    "                if n_both < 50:\n"
    "                    horizon_comparison = {\n"
    "                        \"n_both\":             n_both,\n"
    "                        \"bands_4h\":           [],\n"
    "                        \"bands_24h\":          [],\n"
    "                        \"bands_missed_by_4h\": [],\n"
    "                        \"verdict\": {\n"
    "                            \"label\":   \"INSUFFICIENT_DATA\",\n"
    "                            \"message\": (\n"
    "                                f\"Only {n_both} rows with both 4h and 24h outcomes. \"\n"
    "                                \"Need 50+ for reliable comparison.\"\n"
    "                            ),\n"
    "                        },\n"
    "                    }\n"
    "                else:\n"
    "                    dual = [\n"
    "                        {\n"
    "                            \"score\":          float(r[\"score\"] or 0),\n"
    "                            \"return_4h_pct\":  float(r[\"return_4h_pct\"] or 0),\n"
    "                            \"return_24h_pct\": float(r[\"return_24h_pct\"] or 0),\n"
    "                        }\n"
    "                        for r in dual_rows\n"
    "                    ]\n"
    "                    bands_4h  = _exhaustive_bands(dual, \"return_4h_pct\")\n"
    "                    bands_24h = _exhaustive_bands(dual, \"return_24h_pct\")\n"
    "\n"
    "                    # 24h viable bands not covered by any 4h top band\n"
    "                    viable_24h   = [b for b in bands_24h if b[\"wr\"] > 50.0 and b[\"expectancy\"] > 0]\n"
    "                    bands_missed = [\n"
    "                        b for b in viable_24h\n"
    "                        if not any(b[\"lo\"] < b4[\"hi\"] and b[\"hi\"] > b4[\"lo\"] for b4 in bands_4h)\n"
    "                    ]\n"
    "\n"
    "                    if not bands_4h or not bands_24h:\n"
    "                        hv_label = \"INSUFFICIENT_DATA\"\n"
    "                        hv_msg   = \"Exhaustive search found no viable bands in one or both horizons.\"\n"
    "                    elif bands_missed:\n"
    "                        top4h  = bands_4h[0]\n"
    "                        top24h = bands_24h[0]\n"
    "                        hv_label = \"SWITCH_RECOMMENDED\"\n"
    "                        hv_msg = (\n"
    "                            f\"24h top band {top24h['lo']}\\u2013{top24h['hi']} \"\n"
    "                            f\"(WR={top24h['wr']:.0f}%, avg={top24h['avg_ret']:+.1f}%) \"\n"
    "                            f\"is not covered by 4h tuner \"\n"
    "                            f\"(4h top: {top4h['lo']}\\u2013{top4h['hi']}, \"\n"
    "                            f\"WR={top4h['wr']:.0f}%). \"\n"
    "                            f\"{len(bands_missed)} viable 24h band(s) missed by 4h gates. \"\n"
    "                            \"Switching to 24h optimization would expose these signals.\"\n"
    "                        )\n"
    "                    else:\n"
    "                        top4h     = bands_4h[0]\n"
    "                        top24h    = bands_24h[0]\n"
    "                        overlap   = (top4h[\"lo\"] < top24h[\"hi\"] and top24h[\"lo\"] < top4h[\"hi\"])\n"
    "                        exp_delta = top24h[\"expectancy\"] - top4h[\"expectancy\"]\n"
    "                        threshold = max(top4h[\"expectancy\"] * 0.2, 5.0) if top4h[\"expectancy\"] else 5.0\n"
    "                        if overlap and abs(exp_delta) < threshold:\n"
    "                            hv_label = \"ALIGNED\"\n"
    "                            hv_msg = (\n"
    "                                f\"4h and 24h top bands agree: \"\n"
    "                                f\"4h={top4h['lo']}\\u2013{top4h['hi']} (WR={top4h['wr']:.0f}%), \"\n"
    "                                f\"24h={top24h['lo']}\\u2013{top24h['hi']} (WR={top24h['wr']:.0f}%). \"\n"
    "                                \"No evidence the 4h horizon creates selection bias.\"\n"
    "                            )\n"
    "                        else:\n"
    "                            hv_label = \"SWITCH_RECOMMENDED\"\n"
    "                            overlap_note = (\n"
    "                                \"Bands do not overlap \\u2014 materially different score regions.\"\n"
    "                                if not overlap else\n"
    "                                f\"Bands overlap but 24h expectancy differs by {exp_delta:+.1f}.\"\n"
    "                            )\n"
    "                            hv_msg = (\n"
    "                                f\"4h top band {top4h['lo']}\\u2013{top4h['hi']} \"\n"
    "                                f\"(WR={top4h['wr']:.0f}%, exp={top4h['expectancy']:.1f}) vs \"\n"
    "                                f\"24h top band {top24h['lo']}\\u2013{top24h['hi']} \"\n"
    "                                f\"(WR={top24h['wr']:.0f}%, exp={top24h['expectancy']:.1f}). \"\n"
    "                                + overlap_note\n"
    "                            )\n"
    "\n"
    "                    horizon_comparison = {\n"
    "                        \"n_both\":             n_both,\n"
    "                        \"bands_4h\":           bands_4h,\n"
    "                        \"bands_24h\":          bands_24h,\n"
    "                        \"bands_missed_by_4h\": bands_missed,\n"
    "                        \"verdict\":            {\"label\": hv_label, \"message\": hv_msg},\n"
    "                    }\n"
    "\n"
    "            except Exception as _hc_exc:\n"
    "                horizon_comparison = {\n"
    "                    \"n_both\": 0, \"bands_4h\": [], \"bands_24h\": [], \"bands_missed_by_4h\": [],\n"
    "                    \"verdict\": {\"label\": \"INSUFFICIENT_DATA\", \"message\": str(_hc_exc)},\n"
    "                    \"error\":   str(_hc_exc),\n"
    "                }\n"
    "\n"
    "            # ── Verdict ───────────────────────────────────────────────────────\n"
    "            # Compare config_min performance vs all-signals baseline\n"
    "            config_sim = next((x for x in threshold_sim if x[\"threshold\"] == config_min), None)\n"
    "            baseline   = next((x for x in threshold_sim if x[\"threshold\"] == 0), None)\n"
    "            config_wr  = config_sim[\"wr\"]  if config_sim else 0.0\n"
    "            baseline_wr = baseline[\"wr\"]   if baseline  else 0.0\n"
    "\n"
    "            if config_wr < baseline_wr - 10:\n"
    "                verdict_label = \"MISALIGNED\"\n"
    "                verdict_msg = (\n"
    "                    f\"ENV gate (score\\u2265{config_min}) WR={config_wr:.0f}% is \"\n"
    "                    f\"{baseline_wr - config_wr:.0f}pp BELOW the no-gate baseline \"\n"
    "                    f\"({baseline_wr:.0f}%). Raising the threshold is actively \"\n"
    "                    f\"selecting worse signals. Tuner recommends score \"\n"
    "                    f\"{tuner['min_score']}-{tuner['max_score']} \"\n"
    "                    f\"({tuner['confidence']} confidence, {tuner['sample_size']} samples).\"\n"
    "                    if tuner else\n"
    "                    f\"ENV gate (score\\u2265{config_min}) performs worse than no gate. Lower the threshold.\"\n"
    "                )\n"
    "            elif config_wr < 30:\n"
    "                verdict_label = \"SUBOPTIMAL\"\n"
    "                verdict_msg = (\n"
    "                    f\"ENV gate (score\\u2265{config_min}) WR={config_wr:.0f}% \\u2014 \"\n"
    "                    \"low but not inverting. Consider lowering toward 25-40 range.\"\n"
    "                )\n"
    "            else:\n"
    "                verdict_label = \"CALIBRATED\"\n"
    "                verdict_msg = f\"ENV gate (score\\u2265{config_min}) WR={config_wr:.0f}% \\u2014 acceptable.\"\n"
    "\n"
    "            return {\n"
    "                \"config_score_min\":   config_min,\n"
    "                \"bands\":              bands,\n"
    "                \"threshold_sim\":      threshold_sim,\n"
    "                \"bought_split\":       bought_split,\n"
    "                \"optimal_window\":     best_window,\n"
    "                \"window_results\":     window_results,\n"
    "                \"tuner\":              tuner,\n"
    "                \"verdict\":            {\"label\": verdict_label, \"message\": verdict_msg},\n"
    "                \"horizon_comparison\": horizon_comparison,  # P184\n"
    "            }\n"
)

assert OLD_C in src, "Anchor C not found — check verdict+return block in score-analysis"
src = src.replace(OLD_C, NEW_C, 1)
print("Step C: horizon_comparison computed and added to return dict")


# Write and verify
with open(MR_PATH, "w") as f:
    f.write(src)

py_compile.compile(MR_PATH, doraise=True)
print(f"memecoins.py — py_compile: OK")
print("\nPatch 184 applied successfully.")
print("  A. _exhaustive_bands() helper added inside score-analysis _run()")
print("  B. tuner object now exposes score_bands + multi_band_mode (from P183 kv_store)")
print("  C. horizon_comparison computed: 4h vs 24h band search, verdict, bands_missed_by_4h")
print("  No runtime behavior changes. Frontend pre-built — renders automatically.")
