"""
Patch 182 — Score threshold decision support: GET /api/memecoins/score-analysis

Answers the question: Is MEMECOIN_BUY_SCORE_MIN=65 the right threshold?

Data (852 complete outcomes, Mar 8 2026):
  - Score 65+ (current gate): WR=18.9%, avg=-27.5%  WORST COHORT
  - Score 20-24: WR=65.9%, avg=+33.6%               BEST (tuner knows this)
  - Score 40-44: WR=67.3%, avg=+28.0%               SECOND PEAK (tuner misses it)
  - Score 60-79: WR=0.0%  across all bands           DEAD ZONE
  - All signals (no gate): WR=36.2%, avg=-2.2%       BETTER than current gate

The score distribution is bimodal. The tuner found peak #1 (20-25) but set
max_score=25, cutting off peak #2 (40-44).

New endpoint returns:
  - bands: 5-point score bands with WR, avg_24h, n
  - threshold_sim: WR/avg at thresholds [0, 20, 25, 30, 40, 50, 65]
  - bought_split: performance of bought vs not-bought signals
  - optimal_window: single best scoring window by WR * avg_24h
  - tuner: current kv_store recommendation
  - verdict: MISALIGNED / LOW / OPTIMAL with reasoning
"""
import os
import py_compile

MR_PATH = "/root/memecoin_engine/dashboard/backend/routers/memecoins.py"

NEW_ENDPOINT = '''

# ── Patch 182: Score threshold decision support ────────────────────────────────

@router.get("/score-analysis")
async def memecoins_score_analysis_ep(_: str = Depends(get_current_user)):
    """
    Compares outcome quality across score bands and threshold levels so the
    operator can decide whether MEMECOIN_BUY_SCORE_MIN needs adjustment.

    Returns:
      bands            — 5-point score bands: WR, avg_24h, n, avg_win, avg_loss
      threshold_sim    — cumulative stats at key thresholds (0, 20, 25, 30, 40, 50, 65)
      bought_split     — performance of actually-bought vs skipped signals
      optimal_window   — single best score range (by WR × avg_24h product)
      tuner            — current auto-tuner recommendation from kv_store
      config_score_min — current MEMECOIN_BUY_SCORE_MIN env value
      verdict          — operator verdict: MISALIGNED / SUBOPTIMAL / CALIBRATED
    """
    import json as _json
    import os as _os

    config_min = int(float(_os.getenv("MEMECOIN_BUY_SCORE_MIN", "65")))

    def _run():
        from utils.db import get_conn  # type: ignore
        with get_conn() as conn:

            # ── 5-point score bands ───────────────────────────────────────────
            band_rows = conn.execute("""
                SELECT
                  (CAST(score AS INTEGER) / 5) * 5 AS lo,
                  COUNT(*) AS n,
                  ROUND(AVG(CASE WHEN return_24h_pct > 0 THEN 1.0 ELSE 0.0 END)*100, 1) AS wr,
                  ROUND(AVG(return_24h_pct), 2) AS avg_24h,
                  ROUND(AVG(CASE WHEN return_24h_pct > 0 THEN return_24h_pct END), 2) AS avg_win,
                  ROUND(AVG(CASE WHEN return_24h_pct <= 0 THEN return_24h_pct END), 2) AS avg_loss
                FROM memecoin_signal_outcomes
                WHERE score IS NOT NULL AND return_24h_pct IS NOT NULL
                GROUP BY lo ORDER BY lo
            """).fetchall()
            bands = [dict(r) for r in band_rows]

            # ── Threshold simulation ──────────────────────────────────────────
            base_thresholds = [0, 20, 25, 30, 40, 50, 65]
            if config_min not in base_thresholds:
                base_thresholds.append(config_min)
            base_thresholds = sorted(set(base_thresholds), reverse=True)

            threshold_sim = []
            for t in base_thresholds:
                r = conn.execute("""
                    SELECT COUNT(*) AS n,
                           ROUND(AVG(CASE WHEN return_24h_pct > 0 THEN 1.0 ELSE 0.0 END)*100, 1) AS wr,
                           ROUND(AVG(return_24h_pct), 2) AS avg_ret,
                           ROUND(AVG(CASE WHEN return_24h_pct > 0 THEN return_24h_pct END), 2) AS avg_win,
                           ROUND(AVG(CASE WHEN return_24h_pct <= 0 THEN return_24h_pct END), 2) AS avg_loss
                    FROM memecoin_signal_outcomes
                    WHERE score >= ? AND return_24h_pct IS NOT NULL
                """, (t,)).fetchone()
                n = int(r["n"] or 0)
                threshold_sim.append({
                    "threshold":   t,
                    "n":           n,
                    "wr":          float(r["wr"] or 0),
                    "avg_24h":     float(r["avg_ret"] or 0),
                    "avg_win":     float(r["avg_win"] or 0) if r["avg_win"] else None,
                    "avg_loss":    float(r["avg_loss"] or 0) if r["avg_loss"] else None,
                    "is_current":  t == config_min,
                })

            # ── Bought vs not-bought split ────────────────────────────────────
            bought_split = {}
            for bought_val in (1, 0):
                r = conn.execute("""
                    SELECT COUNT(*) AS n,
                           ROUND(AVG(score), 1) AS avg_score,
                           ROUND(AVG(CASE WHEN return_24h_pct > 0 THEN 1.0 ELSE 0.0 END)*100, 1) AS wr,
                           ROUND(AVG(return_24h_pct), 2) AS avg_24h
                    FROM memecoin_signal_outcomes
                    WHERE bought=? AND return_24h_pct IS NOT NULL
                """, (bought_val,)).fetchone()
                key = "bought" if bought_val == 1 else "not_bought"
                bought_split[key] = {
                    "n":         int(r["n"] or 0),
                    "avg_score": float(r["avg_score"] or 0),
                    "wr":        float(r["wr"] or 0),
                    "avg_24h":   float(r["avg_24h"] or 0),
                }

            # ── Optimal window: test candidate ranges ─────────────────────────
            windows = [
                (10, 49), (20, 24), (20, 49), (30, 49), (35, 49),
                (40, 44), (40, 49), (40, 59), (20, 59),
            ]
            best_window = None
            best_score_product = -9999.0
            window_results = []
            for lo, hi in windows:
                r = conn.execute("""
                    SELECT COUNT(*) AS n,
                           ROUND(AVG(CASE WHEN return_24h_pct > 0 THEN 1.0 ELSE 0.0 END)*100, 1) AS wr,
                           ROUND(AVG(return_24h_pct), 2) AS avg_24h
                    FROM memecoin_signal_outcomes
                    WHERE score BETWEEN ? AND ? AND return_24h_pct IS NOT NULL
                """, (lo, hi)).fetchone()
                n = int(r["n"] or 0)
                wr = float(r["wr"] or 0)
                avg = float(r["avg_24h"] or 0)
                # Rank by (wr - 50) * avg where both must be positive for real edge
                # Require n >= 20 to avoid noise
                product = (wr / 100) * avg if n >= 20 else -9999.0
                entry = {"lo": lo, "hi": hi, "n": n, "wr": wr, "avg_24h": avg}
                window_results.append(entry)
                if product > best_score_product:
                    best_score_product = product
                    best_window = entry

            # ── Tuner stored thresholds ───────────────────────────────────────
            tuner = None
            try:
                row = conn.execute(
                    "SELECT value FROM kv_store WHERE key='memecoin_learned_thresholds'"
                ).fetchone()
                if row:
                    t_data = _json.loads(row["value"])
                    tuner = {
                        "min_score":   t_data["thresholds"].get("min_score"),
                        "max_score":   t_data["thresholds"].get("max_score"),
                        "confidence":  t_data.get("confidence"),
                        "sample_size": t_data.get("sample_size"),
                        "win_rate":    t_data.get("win_rate"),
                        "updated_at":  t_data.get("updated_at"),
                    }
            except Exception:
                pass

            # ── Verdict ───────────────────────────────────────────────────────
            # Compare config_min performance vs all-signals baseline
            config_sim = next((x for x in threshold_sim if x["threshold"] == config_min), None)
            baseline   = next((x for x in threshold_sim if x["threshold"] == 0), None)
            config_wr  = config_sim["wr"]  if config_sim else 0.0
            baseline_wr = baseline["wr"]   if baseline  else 0.0

            if config_wr < baseline_wr - 10:
                verdict_label = "MISALIGNED"
                verdict_msg = (
                    f"ENV gate (score≥{config_min}) WR={config_wr:.0f}% is "
                    f"{baseline_wr - config_wr:.0f}pp BELOW the no-gate baseline "
                    f"({baseline_wr:.0f}%). Raising the threshold is actively "
                    f"selecting worse signals. Tuner recommends score "
                    f"{tuner['min_score']}-{tuner['max_score']} "
                    f"({tuner['confidence']} confidence, {tuner['sample_size']} samples)."
                    if tuner else
                    f"ENV gate (score≥{config_min}) performs worse than no gate. Lower the threshold."
                )
            elif config_wr < 30:
                verdict_label = "SUBOPTIMAL"
                verdict_msg = (
                    f"ENV gate (score≥{config_min}) WR={config_wr:.0f}% — "
                    "low but not inverting. Consider lowering toward 25-40 range."
                )
            else:
                verdict_label = "CALIBRATED"
                verdict_msg = f"ENV gate (score≥{config_min}) WR={config_wr:.0f}% — acceptable."

            return {
                "config_score_min": config_min,
                "bands":            bands,
                "threshold_sim":    threshold_sim,
                "bought_split":     bought_split,
                "optimal_window":   best_window,
                "window_results":   window_results,
                "tuner":            tuner,
                "verdict":          {"label": verdict_label, "message": verdict_msg},
            }

    try:
        return await asyncio.to_thread(_run)
    except Exception as exc:
        log.warning("memecoins_score_analysis_ep error: %s", exc)
        return {"error": str(exc)}
'''

# Read current router file
with open(MR_PATH) as f:
    src = f.read()

# Append before the final journal/learnings insert point (or just at end of file)
# The file ends after the analytics endpoint error handler — append there
ANCHOR = "            \"learned_thresholds\": None,\n        }"
assert ANCHOR in src, f"Anchor not found in {MR_PATH}"
src = src.replace(ANCHOR, ANCHOR + NEW_ENDPOINT, 1)

with open(MR_PATH, "w") as f:
    f.write(src)

py_compile.compile(MR_PATH, doraise=True)
print(f"memecoins.py — py_compile: OK")
print("Patch 182 applied — GET /api/memecoins/score-analysis added")
