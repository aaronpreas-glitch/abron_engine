"""
Patch 185 — Switch tuner optimization horizon from 4h to 24h

P183 exhaustive window search ranked score bands by 4h expectancy.
P184 (SWITCH_RECOMMENDED) proved this misses score 20–25:
  WR=66%, avg 24h=+33.6% — invisible to 4h tuner (n=82, n_both=901 samples)

Changes:
  utils/memecoin_manager.py (_tune_thresholds_step):
    A. DB query: filter on return_24h_pct IS NOT NULL (was 4h)
       winners/losers/win_rate: now 24h-based
    B. rug_stats win calculation: return_24h_pct (was 4h)
    C. Exhaustive window loop: _wr + _avg use return_24h_pct;
       band field renamed avg_4h → avg_24h
    D. payload: adds optimization_horizon = "24h" for transparency

  dashboard/backend/routers/memecoins.py:
    E. MISALIGNED verdict band string: avg_4h → avg_24h (with fallback
       for backward compat on stale kv_store data)
    F. tuner object: exposes optimization_horizon field

No config flips. No auto-buy behavior changes beyond tuner output itself.
Tuner will re-run within 60s after restart and update kv_store.
Expected new bands: 5–20 (WR~72%), 20–25 (WR~66%), 40–45 (WR~67%)

Files changed:
  /root/memecoin_engine/utils/memecoin_manager.py
  /root/memecoin_engine/dashboard/backend/routers/memecoins.py

(MemecoinsPage.tsx frontend changes handled separately)
"""
import os
import py_compile

MM_PATH = "/root/memecoin_engine/utils/memecoin_manager.py"
MR_PATH = "/root/memecoin_engine/dashboard/backend/routers/memecoins.py"

mm = open(MM_PATH).read()
mr = open(MR_PATH).read()


# ── A: Query WHERE + winners/losers/win_rate ──────────────────────────────────
# Covers the complete fetch-through-baseline section of _tune_thresholds_step.
# Unique: no other function has this SELECT + WHERE + winners/losers block.

OLD_A = (
    "            rows = conn.execute(\"\"\"\n"
    "                SELECT score, rug_label, top_holder_pct, mcap_at_scan,\n"
    "                       token_age_days, vol_acceleration,\n"
    "                       return_1h_pct, return_4h_pct, return_24h_pct, bought\n"
    "                FROM memecoin_signal_outcomes\n"
    "                WHERE status = 'COMPLETE' AND return_4h_pct IS NOT NULL\n"
    "            \"\"\").fetchall()\n"
    "    except Exception:\n"
    "        return\n"
    "\n"
    "    if len(rows) < 20:\n"
    "        return  # not enough data yet\n"
    "\n"
    "    total      = len(rows)\n"
    "    winners    = [r for r in rows if (r[\"return_4h_pct\"] or 0) > 0]   # Patch 132: any profit counts\n"
    "    losers     = [r for r in rows if (r[\"return_4h_pct\"] or 0) <= 0]\n"
    "    win_rate   = round(len(winners) / total * 100, 1)\n"
)

NEW_A = (
    "            rows = conn.execute(\"\"\"\n"
    "                SELECT score, rug_label, top_holder_pct, mcap_at_scan,\n"
    "                       token_age_days, vol_acceleration,\n"
    "                       return_1h_pct, return_4h_pct, return_24h_pct, bought\n"
    "                FROM memecoin_signal_outcomes\n"
    "                WHERE status = 'COMPLETE' AND return_24h_pct IS NOT NULL\n"
    "            \"\"\").fetchall()\n"
    "    except Exception:\n"
    "        return\n"
    "\n"
    "    if len(rows) < 20:\n"
    "        return  # not enough data yet\n"
    "\n"
    "    total      = len(rows)\n"
    "    winners    = [r for r in rows if (r[\"return_24h_pct\"] or 0) > 0]   # P185: 24h horizon\n"
    "    losers     = [r for r in rows if (r[\"return_24h_pct\"] or 0) <= 0]  # P185: 24h horizon\n"
    "    win_rate   = round(len(winners) / total * 100, 1)\n"
)

assert OLD_A in mm, "Anchor A not found — check _tune_thresholds_step query + winners block"
mm = mm.replace(OLD_A, NEW_A, 1)
print("Step A: Query + winners/losers/win_rate switched to return_24h_pct")


# ── B: rug_stats win calculation ──────────────────────────────────────────────
# Single unique line — only one rug_stats winner count in the whole file.

OLD_B = (
    "            w = sum(1 for r in subset if (r[\"return_4h_pct\"] or 0) > 0)  # Patch 132\n"
)

NEW_B = (
    "            w = sum(1 for r in subset if (r[\"return_24h_pct\"] or 0) > 0)  # P185: 24h horizon\n"
)

assert OLD_B in mm, "Anchor B not found — check rug_stats w= line in _tune_thresholds_step"
mm = mm.replace(OLD_B, NEW_B, 1)
print("Step B: rug_stats win calculation switched to return_24h_pct")


# ── C: Exhaustive window loop — metric + band field name ─────────────────────
# The full inner body of the for _lo / for _width loop.

OLD_C = (
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
)

NEW_C = (
    "            _wr = sum(1 for r in _win if (r[\"return_24h_pct\"] or 0) > 0) / len(_win)\n"
    "            _avg = sum((r[\"return_24h_pct\"] or 0) for r in _win) / len(_win)\n"
    "            # Expectancy: only count windows with positive avg (true edge)\n"
    "            _exp = (_wr * 100) * _avg if _avg > 0 else -999.0\n"
    "            _all_windows.append({\n"
    "                \"lo\": _lo, \"hi\": _hi, \"n\": len(_win),\n"
    "                \"wr\": round(_wr * 100, 1),\n"
    "                \"avg_24h\": round(_avg, 2),  # P185: renamed from avg_4h\n"
    "                \"expectancy\": round(_exp, 2),\n"
    "            })\n"
)

assert OLD_C in mm, "Anchor C not found — check exhaustive window loop body in _tune_thresholds_step"
mm = mm.replace(OLD_C, NEW_C, 1)
print("Step C: Window loop switched to return_24h_pct, avg_4h → avg_24h")


# ── D: Add optimization_horizon to kv_store payload ──────────────────────────

OLD_D = (
    "    payload = {\n"
    "        \"thresholds\":      thresholds,\n"
    "        \"bands\":           _top_bands,           # Patch 183: multi-modal bands\n"
    "        \"multi_band_mode\": multi_band_mode,       # Patch 183: True if ≥2 viable bands\n"
    "        \"sample_size\":     total,\n"
)

NEW_D = (
    "    payload = {\n"
    "        \"thresholds\":            thresholds,\n"
    "        \"bands\":                 _top_bands,         # Patch 183: multi-modal bands\n"
    "        \"multi_band_mode\":       multi_band_mode,    # Patch 183: True if ≥2 viable bands\n"
    "        \"optimization_horizon\":  \"24h\",              # P185: tuner ranks by 24h expectancy\n"
    "        \"sample_size\":           total,\n"
)

assert OLD_D in mm, "Anchor D not found — check payload dict in _tune_thresholds_step"
mm = mm.replace(OLD_D, NEW_D, 1)
print("Step D: optimization_horizon='24h' added to payload")


# Write memecoin_manager.py and verify
with open(MM_PATH, "w") as f:
    f.write(mm)

py_compile.compile(MM_PATH, doraise=True)
print(f"memecoin_manager.py — py_compile: OK\n")


# ── E: Router MISALIGNED verdict — avg_4h → avg_24h ──────────────────────────
# Use .get() with fallback for backward compat on stale kv_store data
# (old tuner runs that still have avg_4h will still render correctly).

OLD_E = (
    "                    _band_strs = \" + \".join(\n"
    "                        f\"score {b['lo']}-{b['hi']} (WR={b['wr']:.0f}%, 4h={b['avg_4h']:+.1f}%)\"\n"
    "                        for b in tuner[\"score_bands\"]\n"
    "                    )\n"
)

NEW_E = (
    "                    _band_strs = \" + \".join(\n"
    "                        f\"score {b['lo']}-{b['hi']} (WR={b['wr']:.0f}%, 24h={b.get('avg_24h', b.get('avg_4h', 0)):+.1f}%)\"\n"
    "                        for b in tuner[\"score_bands\"]\n"
    "                    )\n"
)

assert OLD_E in mr, "Anchor E not found — check MISALIGNED verdict _band_strs in memecoins.py"
mr = mr.replace(OLD_E, NEW_E, 1)
print("Step E: MISALIGNED verdict band_strs updated avg_4h → avg_24h (with fallback)")


# ── F: Router tuner object — add optimization_horizon field ──────────────────
# Defaults to "4h" for backward compat with stale kv_store data.

OLD_F = (
    "                    tuner = {\n"
    "                        \"min_score\":       t_data[\"thresholds\"].get(\"min_score\"),\n"
    "                        \"max_score\":       t_data[\"thresholds\"].get(\"max_score\"),\n"
    "                        \"confidence\":      t_data.get(\"confidence\"),\n"
    "                        \"sample_size\":     t_data.get(\"sample_size\"),\n"
    "                        \"win_rate\":        t_data.get(\"win_rate\"),\n"
    "                        \"updated_at\":      t_data.get(\"updated_at\"),\n"
    "                        \"score_bands\":     _t_bands,   # Patch 183: multi-modal\n"
    "                        \"multi_band_mode\": _multi,     # Patch 183: True if 2+ viable\n"
    "                    }\n"
)

NEW_F = (
    "                    tuner = {\n"
    "                        \"min_score\":            t_data[\"thresholds\"].get(\"min_score\"),\n"
    "                        \"max_score\":            t_data[\"thresholds\"].get(\"max_score\"),\n"
    "                        \"confidence\":           t_data.get(\"confidence\"),\n"
    "                        \"sample_size\":          t_data.get(\"sample_size\"),\n"
    "                        \"win_rate\":             t_data.get(\"win_rate\"),\n"
    "                        \"updated_at\":           t_data.get(\"updated_at\"),\n"
    "                        \"score_bands\":          _t_bands,   # Patch 183: multi-modal\n"
    "                        \"multi_band_mode\":      _multi,     # Patch 183: True if 2+ viable\n"
    "                        \"optimization_horizon\": t_data.get(\"optimization_horizon\", \"4h\"),  # P185\n"
    "                    }\n"
)

assert OLD_F in mr, "Anchor F not found — check tuner dict in score-analysis _run()"
mr = mr.replace(OLD_F, NEW_F, 1)
print("Step F: tuner object gains optimization_horizon field")


# Write memecoins.py and verify
with open(MR_PATH, "w") as f:
    f.write(mr)

py_compile.compile(MR_PATH, doraise=True)
print(f"memecoins.py — py_compile: OK\n")

print("Patch 185 applied successfully.")
print("  A. Tuner query: return_24h_pct IS NOT NULL (was 4h)")
print("  B. rug_stats: win count uses return_24h_pct")
print("  C. Window loop: _wr/_avg on return_24h_pct; band field avg_4h → avg_24h")
print("  D. payload: optimization_horizon='24h' stored in kv_store")
print("  E. Router verdict: avg_4h → avg_24h with fallback")
print("  F. Router tuner object: exposes optimization_horizon field")
print("  Restart service, then tuner fires within 60s with new 24h bands.")
