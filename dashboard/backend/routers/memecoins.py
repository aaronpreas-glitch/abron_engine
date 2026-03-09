"""
Memecoin scanner endpoints — Patches 115, 116, 117, 125.

Routes:
  GET  /api/memecoins/status     — scanner signals + open positions + stats
  POST /api/memecoins/buy        — buy a memecoin by mint address
  POST /api/memecoins/sell/{mint} — sell an open position
  GET  /api/memecoins/analytics  — score buckets, rug breakdown, tuner progress
"""
from __future__ import annotations

import asyncio
import logging
import os
import sqlite3

from fastapi import APIRouter, Depends, HTTPException

from auth import get_current_user
from routers._shared import _ensure_engine_path, _db_path

log = logging.getLogger("dashboard")
router = APIRouter(prefix="/api/memecoins", tags=["memecoins"])


@router.get("/status")
async def memecoins_status_ep(_: str = Depends(get_current_user)):
    """Scanner signals + open positions + stats."""
    _ensure_engine_path()
    try:
        from utils.memecoin_manager import memecoin_status as _ms  # type: ignore
        return await asyncio.to_thread(_ms)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/buy")
async def memecoins_buy_ep(body: dict, _: str = Depends(get_current_user)):
    """Buy a memecoin by mint address."""
    _ensure_engine_path()
    mint       = str(body.get("mint",       "")).strip()
    symbol     = str(body.get("symbol",     "")).strip().upper()
    amount_usd = float(body.get("amount_usd", 10))
    if not mint or not symbol:
        raise HTTPException(status_code=400, detail="mint and symbol required")
    try:
        from utils.memecoin_manager import buy_memecoin as _bm  # type: ignore
        return await asyncio.to_thread(_bm, mint, symbol, amount_usd)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/sell/{mint}")
async def memecoins_sell_ep(mint: str, _: str = Depends(get_current_user)):
    """Sell an open memecoin position by mint address."""
    _ensure_engine_path()
    try:
        from utils.memecoin_manager import sell_memecoin as _sm  # type: ignore
        return await asyncio.to_thread(_sm, mint, "MANUAL")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/trending")
async def memecoins_trending_ep(_: str = Depends(get_current_user)):
    """
    Narrative momentum trending data — CoinGecko + DexScreener Solana boosted.
    Returns cached payload refreshed every 4h by the research loop. (Patch 127)
    """
    _ensure_engine_path()
    try:
        from utils.narrative_momentum import get_narrative_data  # type: ignore
        data = get_narrative_data()
        return data or {"coingecko": [], "dexscreener": [], "updated_at": None}
    except Exception as exc:
        return {"coingecko": [], "dexscreener": [], "updated_at": None, "error": str(exc)}


@router.get("/analytics")
async def memecoins_analytics_ep(_: str = Depends(get_current_user)):
    """
    Signal outcome analytics — score buckets, rug label breakdown, win rates,
    avg returns, and auto-buy config with tuner progress. (Patches 116+117+125)
    """
    _db = _db_path()

    def _run():
        c = sqlite3.connect(str(_db))
        c.row_factory = sqlite3.Row

        total    = c.execute("SELECT COUNT(*) FROM memecoin_signal_outcomes").fetchone()[0]
        complete = c.execute(
            "SELECT COUNT(*) FROM memecoin_signal_outcomes WHERE status='COMPLETE'"
        ).fetchone()[0]
        bought   = c.execute(
            "SELECT COUNT(*) FROM memecoin_signal_outcomes WHERE bought=1"
        ).fetchone()[0]

        # Score buckets
        buckets = []
        for label, lo, hi in [("70+", 70, 101), ("50\u201369", 50, 70), ("<50", 0, 50)]:
            rows = c.execute("""
                SELECT return_1h_pct, return_4h_pct, return_24h_pct, bought
                FROM memecoin_signal_outcomes
                WHERE score >= ? AND score < ? AND status = 'COMPLETE'
            """, (lo, hi)).fetchall()

            if not rows:
                buckets.append({
                    "label": label, "count": 0, "win_rate_4h": None,
                    "avg_return_1h": None, "avg_return_4h": None,
                    "avg_return_24h": None, "buy_rate": None,
                })
                continue

            r1   = [float(r["return_1h_pct"])  for r in rows if r["return_1h_pct"]  is not None]
            r4   = [float(r["return_4h_pct"])  for r in rows if r["return_4h_pct"]  is not None]
            r24  = [float(r["return_24h_pct"]) for r in rows if r["return_24h_pct"] is not None]
            win4 = sum(1 for x in r4 if x > 0)
            buckets.append({
                "label":          label,
                "count":          len(rows),
                "win_rate_4h":    round(win4 / len(r4) * 100, 1) if r4 else None,
                "avg_return_1h":  round(sum(r1)  / len(r1),  2)  if r1  else None,
                "avg_return_4h":  round(sum(r4)  / len(r4),  2)  if r4  else None,
                "avg_return_24h": round(sum(r24) / len(r24), 2)  if r24 else None,
                "buy_rate":       round(sum(1 for r in rows if r["bought"]) / len(rows) * 100, 1),
            })

        # Rug label breakdown (Patch 117)
        rug_breakdown = []
        for rug_label in ("GOOD", "WARN", "UNKNOWN"):
            rows = c.execute("""
                SELECT return_4h_pct, return_24h_pct, bought
                FROM memecoin_signal_outcomes
                WHERE rug_label = ? AND status = 'COMPLETE'
            """, (rug_label,)).fetchall()
            if rows:
                r4  = [float(r["return_4h_pct"]) for r in rows if r["return_4h_pct"] is not None]
                win = sum(1 for x in r4 if x > 0)
                rug_breakdown.append({
                    "label":         rug_label,
                    "count":         len(rows),
                    "win_rate_4h":   round(win / len(r4) * 100, 1) if r4 else None,
                    "avg_return_4h": round(sum(r4) / len(r4), 2)   if r4 else None,
                })

        # Best 10 by 4h return
        top = c.execute("""
            SELECT symbol, mint, score, rug_label, mcap_at_scan,
                   token_age_days, vol_acceleration, top_holder_pct,
                   return_1h_pct, return_4h_pct, return_24h_pct, bought, scanned_at
            FROM memecoin_signal_outcomes
            WHERE return_4h_pct IS NOT NULL
            ORDER BY return_4h_pct DESC
            LIMIT 10
        """).fetchall()

        # Learned thresholds
        lt_row = c.execute(
            "SELECT value FROM kv_store WHERE key='memecoin_learned_thresholds'"
        ).fetchone()
        learned = None
        if lt_row:
            try:
                import json as _j
                learned = _j.loads(lt_row["value"])
            except Exception:
                pass

        # Auto-buy config + tuner progress (Patch 125, updated Patch 138)
        # Milestone ladder: 20→50→200→500→1000, then perpetual +500 increments forever.
        # Progress bar always advances — learning never stops.
        if complete >= 1000:
            _tuner_needed = ((complete // 500) + 1) * 500  # perpetual: next 500 boundary
        elif complete >= 500:
            _tuner_needed = 1000
        elif complete >= 200:
            _tuner_needed = 500
        elif complete >= 50:
            _tuner_needed = 200
        elif complete >= 20:
            _tuner_needed = 50
        else:
            _tuner_needed = 20
        auto_buy = {
            "enabled":         os.getenv("MEMECOIN_AUTO_BUY",    "false").lower() == "true",
            "dry_run":         os.getenv("MEMECOIN_DRY_RUN",     "true").lower()  == "true",
            "score_min":       float(os.getenv("MEMECOIN_BUY_SCORE_MIN", "65")),
            "max_open":        int(os.getenv("MEMECOIN_MAX_OPEN", "3")),
            "buy_usd":         float(os.getenv("MEMECOIN_BUY_USD", "15")),
            "tuner_threshold": _tuner_needed,
            "complete_pct":    round(min(complete / _tuner_needed * 100, 100.0), 1),
        }

        c.close()
        return {
            "total_tracked":      total,
            "complete":           complete,
            "pending":            total - complete,
            "bought_count":       bought,
            "score_buckets":      buckets,
            "rug_breakdown":      rug_breakdown,
            "top_performers":     [dict(r) for r in top],
            "learned_thresholds": learned,
            "auto_buy":           auto_buy,
        }

    try:
        return await asyncio.to_thread(_run)
    except Exception as exc:
        log.warning("memecoins_analytics_ep error: %s", exc)
        return {
            "total_tracked": 0, "complete": 0, "pending": 0, "bought_count": 0,
            "score_buckets": [], "rug_breakdown": [], "top_performers": [],
            "learned_thresholds": None,
        }


# ── Patch 182 + 184: Score threshold decision support ─────────────────────────

@router.get("/score-analysis")
async def memecoins_score_analysis_ep(_: str = Depends(get_current_user)):
    """
    Compares outcome quality across score bands and threshold levels so the
    operator can decide whether MEMECOIN_BUY_SCORE_MIN needs adjustment.

    P182: bands, threshold_sim, bought_split, optimal_window, tuner, verdict
    P184: horizon_comparison — runs the P183 exhaustive window search on both
          4h and 24h outcomes and answers: is the tuner optimizing on the
          wrong horizon?

    Returns:
      bands              — 5-pt score bands: WR, avg_24h, n, avg_win, avg_loss
      threshold_sim      — cumulative stats at key thresholds (0,20,25,30,40,50,65)
      bought_split       — performance of actually-bought vs skipped signals
      optimal_window     — single best score range (WR × avg_24h product)
      tuner              — current auto-tuner recommendation from kv_store
                           (includes P183 score_bands + multi_band_mode)
      config_score_min   — current MEMECOIN_BUY_SCORE_MIN env value
      verdict            — operator verdict: MISALIGNED / SUBOPTIMAL / CALIBRATED
      horizon_comparison — P184: side-by-side 4h vs 24h band search results
    """
    import json as _json
    import os as _os

    config_min = int(float(_os.getenv("MEMECOIN_BUY_SCORE_MIN", "65")))

    def _run():
        # P184: Exhaustive window search helper — same algorithm as P183 tuner
        # but parameterised on ret_key so it works for both 4h and 24h returns.
        def _exhaustive_bands(rows_data, ret_key, min_n=10):
            """Try all (lo, hi) windows with widths 5/10/15, min n per window.
            Rank by expectancy = WR% × avg_return (positive edge only).
            Greedy non-overlapping pass returns top 3 independent bands."""
            all_wins = []
            for lo in range(0, 80, 5):
                for width in (5, 10, 15):
                    hi = lo + width
                    win = [
                        r for r in rows_data
                        if r["score"] is not None and lo <= r["score"] < hi
                        and r[ret_key] is not None
                    ]
                    if len(win) < min_n:
                        continue
                    rets = [float(r[ret_key]) for r in win]
                    wr   = sum(1 for x in rets if x > 0) / len(rets)
                    avg  = sum(rets) / len(rets)
                    exp  = (wr * 100) * avg if avg > 0 else -999.0
                    all_wins.append({
                        "lo": lo, "hi": hi, "n": len(win),
                        "wr": round(wr * 100, 1),
                        "avg_ret": round(avg, 2),
                        "expectancy": round(exp, 2),
                    })
            all_wins.sort(key=lambda w: w["expectancy"], reverse=True)
            top = []
            for w in all_wins:
                if not any(w["lo"] < b["hi"] and w["hi"] > b["lo"] for b in top):
                    top.append(w)
                if len(top) >= 3:
                    break
            return top

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
                    "threshold":  t,
                    "n":          n,
                    "wr":         float(r["wr"] or 0),
                    "avg_24h":    float(r["avg_ret"] or 0),
                    "avg_win":    float(r["avg_win"] or 0) if r["avg_win"] else None,
                    "avg_loss":   float(r["avg_loss"] or 0) if r["avg_loss"] else None,
                    "is_current": t == config_min,
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
                        "min_score":       t_data["thresholds"].get("min_score"),
                        "max_score":       t_data["thresholds"].get("max_score"),
                        "confidence":      t_data.get("confidence"),
                        "sample_size":     t_data.get("sample_size"),
                        "win_rate":        t_data.get("win_rate"),
                        "updated_at":      t_data.get("updated_at"),
                        "score_bands":     t_data.get("bands", []),                    # P183 multi-modal
                        "multi_band_mode": bool(t_data.get("multi_band_mode", False)), # P183
                    }
            except Exception:
                pass

            # ── P184: Horizon comparison — 4h vs 24h exhaustive band search ──
            # Fetches rows with BOTH returns filled. Runs _exhaustive_bands on
            # each horizon independently and compares top bands.
            # No rug filter — matches what _tune_thresholds_step() trains on.
            horizon_comparison = None
            try:
                dual_rows = conn.execute("""
                    SELECT score, return_4h_pct, return_24h_pct
                    FROM memecoin_signal_outcomes
                    WHERE return_4h_pct  IS NOT NULL
                      AND return_24h_pct IS NOT NULL
                      AND score          IS NOT NULL
                """).fetchall()
                n_both = len(dual_rows)

                if n_both < 50:
                    horizon_comparison = {
                        "n_both":             n_both,
                        "bands_4h":           [],
                        "bands_24h":          [],
                        "bands_missed_by_4h": [],
                        "verdict": {
                            "label":   "INSUFFICIENT_DATA",
                            "message": (
                                f"Only {n_both} rows with both 4h and 24h outcomes. "
                                "Need 50+ for reliable comparison."
                            ),
                        },
                    }
                else:
                    dual = [
                        {
                            "score":          float(r["score"] or 0),
                            "return_4h_pct":  float(r["return_4h_pct"] or 0),
                            "return_24h_pct": float(r["return_24h_pct"] or 0),
                        }
                        for r in dual_rows
                    ]
                    bands_4h  = _exhaustive_bands(dual, "return_4h_pct")
                    bands_24h = _exhaustive_bands(dual, "return_24h_pct")

                    # 24h viable bands not covered by any 4h top band
                    viable_24h   = [b for b in bands_24h if b["wr"] > 50.0 and b["expectancy"] > 0]
                    bands_missed = [
                        b for b in viable_24h
                        if not any(b["lo"] < b4["hi"] and b["hi"] > b4["lo"] for b4 in bands_4h)
                    ]

                    if not bands_4h or not bands_24h:
                        hv_label = "INSUFFICIENT_DATA"
                        hv_msg   = "Exhaustive search found no viable bands in one or both horizons."
                    elif bands_missed:
                        top4h  = bands_4h[0]
                        top24h = bands_24h[0]
                        hv_label = "SWITCH_RECOMMENDED"
                        hv_msg = (
                            f"24h top band {top24h['lo']}–{top24h['hi']} "
                            f"(WR={top24h['wr']:.0f}%, avg={top24h['avg_ret']:+.1f}%) "
                            f"is not covered by 4h tuner "
                            f"(4h top: {top4h['lo']}–{top4h['hi']}, "
                            f"WR={top4h['wr']:.0f}%). "
                            f"{len(bands_missed)} viable 24h band(s) missed by 4h gates. "
                            "Switching to 24h optimization would expose these signals."
                        )
                    else:
                        top4h     = bands_4h[0]
                        top24h    = bands_24h[0]
                        overlap   = (top4h["lo"] < top24h["hi"] and top24h["lo"] < top4h["hi"])
                        exp_delta = top24h["expectancy"] - top4h["expectancy"]
                        threshold = max(top4h["expectancy"] * 0.2, 5.0) if top4h["expectancy"] else 5.0
                        if overlap and abs(exp_delta) < threshold:
                            hv_label = "ALIGNED"
                            hv_msg = (
                                f"4h and 24h top bands agree: "
                                f"4h={top4h['lo']}–{top4h['hi']} (WR={top4h['wr']:.0f}%), "
                                f"24h={top24h['lo']}–{top24h['hi']} (WR={top24h['wr']:.0f}%). "
                                "No evidence the 4h horizon creates selection bias."
                            )
                        else:
                            hv_label = "SWITCH_RECOMMENDED"
                            overlap_note = (
                                "Bands do not overlap — materially different score regions."
                                if not overlap else
                                f"Bands overlap but 24h expectancy differs by {exp_delta:+.1f}."
                            )
                            hv_msg = (
                                f"4h top band {top4h['lo']}–{top4h['hi']} "
                                f"(WR={top4h['wr']:.0f}%, exp={top4h['expectancy']:.1f}) vs "
                                f"24h top band {top24h['lo']}–{top24h['hi']} "
                                f"(WR={top24h['wr']:.0f}%, exp={top24h['expectancy']:.1f}). "
                                + overlap_note
                            )

                    horizon_comparison = {
                        "n_both":             n_both,
                        "bands_4h":           bands_4h,
                        "bands_24h":          bands_24h,
                        "bands_missed_by_4h": bands_missed,
                        "verdict":            {"label": hv_label, "message": hv_msg},
                    }

            except Exception as _hc_exc:
                horizon_comparison = {
                    "n_both": 0, "bands_4h": [], "bands_24h": [], "bands_missed_by_4h": [],
                    "verdict": {"label": "INSUFFICIENT_DATA", "message": str(_hc_exc)},
                    "error":   str(_hc_exc),
                }

            # ── Verdict ───────────────────────────────────────────────────────
            # Compare config_min performance vs all-signals baseline
            config_sim  = next((x for x in threshold_sim if x["threshold"] == config_min), None)
            baseline    = next((x for x in threshold_sim if x["threshold"] == 0), None)
            config_wr   = config_sim["wr"]  if config_sim else 0.0
            baseline_wr = baseline["wr"]    if baseline  else 0.0

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
                "config_score_min":   config_min,
                "bands":              bands,
                "threshold_sim":      threshold_sim,
                "bought_split":       bought_split,
                "optimal_window":     best_window,
                "window_results":     window_results,
                "tuner":              tuner,
                "verdict":            {"label": verdict_label, "message": verdict_msg},
                "horizon_comparison": horizon_comparison,  # P184
            }

    try:
        return await asyncio.to_thread(_run)
    except Exception as exc:
        log.warning("memecoins_score_analysis_ep error: %s", exc)
        return {"error": str(exc)}


# ── Patch 189: Top buy candidates — gate replication ──────────────────────────

@router.get("/top-candidates")
async def memecoins_top_candidates_ep(_: str = Depends(get_current_user)):
    """
    Read-only replication of _auto_buy_step() gate logic — decision support.
    Evaluates each cached scanner signal against all gates without executing
    any buy. Returns BUY_NOW / WATCH / BLOCKED status with per-gate blocker
    reasons. Powers the Top Buys panel in HomePage. P189.
    """
    import json as _j

    def _run():
        # ── Cached scanner signals ────────────────────────────────────────────
        try:
            from utils.memecoin_scanner import get_cached_signals  # type: ignore
            signals = sorted(
                get_cached_signals(),
                key=lambda s: s.get("score", 0), reverse=True
            )
        except Exception:
            signals = []

        # ── Env config ────────────────────────────────────────────────────────
        auto_buy  = os.getenv("MEMECOIN_AUTO_BUY", "false").lower() == "true"
        dry_run   = os.getenv("MEMECOIN_DRY_RUN",  "true").lower()  == "true"
        max_open  = int(os.getenv("MEMECOIN_MAX_OPEN", "3"))
        env_score = float(os.getenv("MEMECOIN_BUY_SCORE_MIN", "65"))

        # ── Tuner thresholds (mirrors _auto_buy_step() loading logic) ─────────
        threshold       = env_score
        max_score       = 999
        vacc_min        = 5.0
        holder_max      = 35.0
        bands: list     = []
        multi_band_mode = False
        try:
            from utils.db import get_conn  # type: ignore
            with get_conn() as conn:
                row = conn.execute(
                    "SELECT value FROM kv_store WHERE key='memecoin_learned_thresholds'"
                ).fetchone()
            if row:
                lt = _j.loads(row[0])
                if lt.get("confidence") in ("medium", "high"):
                    t               = lt.get("thresholds", {})
                    threshold       = float(t.get("min_score",            threshold))
                    max_score       = float(t.get("max_score",            999))
                    vacc_min        = float(t.get("min_vol_acceleration", vacc_min))
                    holder_max      = float(t.get("max_top_holder_pct",   holder_max))
                    bands           = lt.get("bands", [])
                    multi_band_mode = bool(lt.get("multi_band_mode", False))
        except Exception:
            pass

        # ── Open positions ────────────────────────────────────────────────────
        open_mints: set = set()
        open_count = 0
        try:
            from utils.db import get_conn  # type: ignore
            with get_conn() as conn:
                rows = conn.execute(
                    "SELECT mint FROM memecoin_trades WHERE status='OPEN'"
                ).fetchall()
            open_mints = {r[0] for r in rows}
            open_count = len(open_mints)
        except Exception:
            pass

        # ── F&G ───────────────────────────────────────────────────────────────
        fg_value: int | None = None
        fg_favorable         = False
        try:
            from utils.agent_coordinator import get_fear_greed  # type: ignore
            fg           = get_fear_greed()
            fg_value     = fg.get("value")
            fg_favorable = bool(fg.get("favorable", False))
        except Exception:
            pass

        # ── System-level blockers (apply to every signal) ─────────────────────
        sys_blockers: list = []
        if not auto_buy:
            sys_blockers.append("AUTO_BUY=false")
        if open_count >= max_open:
            sys_blockers.append(f"CAPACITY ({open_count}/{max_open})")
        if not dry_run and not fg_favorable:
            sys_blockers.append(f"F&G={fg_value} <25")

        # ── Evaluate each signal (mirrors P183 multi-band gate logic) ─────────
        candidates: list = []
        for sig in signals:
            mint       = sig.get("mint", "")
            score      = float(sig.get("score") or 0)
            rug        = sig.get("rug_label", "UNKNOWN")
            bp         = float(sig.get("buy_pressure") or 50.0)
            revoked    = bool(sig.get("mint_revoked", False))
            vacc       = float(sig.get("vol_acceleration") or 0.0)
            holder_pct = float(sig.get("top_holder_pct") or 0.0)

            sig_blockers: list = []
            if mint in open_mints:
                sig_blockers.append("ALREADY_OPEN")

            # Score / band gate — exact mirror of P183 multi-band logic
            if multi_band_mode and bands:
                _min_lo = min(b["lo"] for b in bands)
                if score < _min_lo:
                    sig_blockers.append(f"SCORE_BELOW_BANDS ({score:.0f})")
                elif not any(b["lo"] <= score < b["hi"] for b in bands):
                    sig_blockers.append(f"DEAD_ZONE ({score:.0f})")
            else:
                if score < threshold:
                    sig_blockers.append(f"SCORE_LOW ({score:.0f}<{threshold:.0f})")
                elif score > max_score:
                    sig_blockers.append(f"SCORE_HIGH ({score:.0f}>{max_score:.0f})")

            if rug != "GOOD":        sig_blockers.append(f"RUG={rug}")
            if bp < 55:              sig_blockers.append(f"BP={bp:.0f}%<55%")
            if not revoked:          sig_blockers.append("MINT_LIVE")
            if vacc < vacc_min:      sig_blockers.append(f"VACC={vacc:.1f}<{vacc_min:.1f}")
            if holder_pct > holder_max:
                sig_blockers.append(f"HOLDER={holder_pct:.0f}%>{holder_max:.0f}%")

            all_blockers = sys_blockers + sig_blockers
            if not all_blockers:
                status = "BUY_NOW"
            elif not sig_blockers:
                status = "WATCH"    # signal is clean; only system-level gate blocking
            else:
                status = "BLOCKED"

            candidates.append({
                "mint":             mint,
                "symbol":           sig.get("symbol", "?"),
                "score":            round(score, 1),
                "rug_label":        rug,
                "buy_pressure":     round(bp, 1),
                "mint_revoked":     revoked,
                "vol_acceleration": round(vacc, 2),
                "top_holder_pct":   round(holder_pct, 1),
                "mcap_usd":         sig.get("mcap_usd"),
                "narrative":        sig.get("narrative"),
                "scanned_at":       sig.get("scanned_at"),
                "status":           status,
                "blockers":         all_blockers,
                "signal_blockers":  sig_blockers,
            })

        return {
            "candidates":      candidates,
            "signal_count":    len(signals),
            "open_count":      open_count,
            "max_open":        max_open,
            "dry_run":         dry_run,
            "auto_buy":        auto_buy,
            "fg_value":        fg_value,
            "fg_favorable":    fg_favorable,
            "multi_band_mode": multi_band_mode,
            "active_bands":    [{"lo": b["lo"], "hi": b["hi"], "wr": b.get("wr")}
                                 for b in bands],
            "sys_blockers":    sys_blockers,
        }

    try:
        return await asyncio.to_thread(_run)
    except Exception as exc:
        log.warning("memecoins_top_candidates_ep error: %s", exc)
        return {"error": str(exc), "candidates": [], "signal_count": 0}
