#!/usr/bin/env python3
"""
Patch 54 — PNL Optimizer + Missed Opportunities

Changes:
  A. utils/perp_executor.py
     1. Add _optimize_pnl_targets() — analyzes skipped signals every 15 closes
     2. Add _apply_selectivity_tuning() — live in-memory param update from dashboard
     3. Call _optimize_pnl_targets() on every 15th close

  B. dashboard/backend/main.py
     4. Extend ALLOWED_KEYS in apply-tuner (ML_MIN_PRED_RET, DAILY_TRADE_CAP, etc.)
     5. Add in-memory live-tune call after apply-tuner writes .env
     6. Add GET /api/brain/pnl-optimizer endpoint
     7. Add GET /api/brain/missed-opportunities endpoint
"""

import pathlib

# ─────────────────────────────────────────────────────────────────────────────
# A. perp_executor.py
# ─────────────────────────────────────────────────────────────────────────────

PE = pathlib.Path("/root/memecoin_engine/utils/perp_executor.py")
assert PE.exists()
pe = PE.read_text()

# ── A1: Add _optimize_pnl_targets + _apply_selectivity_tuning before circuit breaker

OLD_CB = "\ndef _check_dynamic_exit_circuit_breaker():"
assert OLD_CB in pe, "FAIL [A1]: circuit breaker anchor not found"

NEW_FUNCS = '''

def _optimize_pnl_targets():
    """
    Analyze skipped_signals_log vs accepted trades to find tuning opportunities.
    Runs every 15 closed trades. Writes structured suggestions to pnl_optimizer_log.

    Looks at three gate failure modes:
      LOW_PRED_RET  — signals just below the pred_ret threshold
      LOW_WIN_PROB  — signals just below the win_prob threshold
      DAILY_CAP     — signals blocked because we already hit the daily limit
    """
    import json as _json
    try:
        with _conn() as c:
            since_7d = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()

            # Ensure log table exists
            c.execute("""
                CREATE TABLE IF NOT EXISTS pnl_optimizer_log (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts_utc      TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
                    trade_count INTEGER,
                    suggestions TEXT,
                    skipped_total INTEGER
                )
            """)

            # Aggregate skip stats by reason
            skip_rows = c.execute("""
                SELECT skip_reason, COUNT(*) as n,
                       AVG(pred_ret) as avg_pr, AVG(ml_wp) as avg_wp
                FROM skipped_signals_log
                WHERE ts_utc >= ?
                GROUP BY skip_reason
            """, (since_7d,)).fetchall()
            skips = {r[0]: {"n": r[1], "avg_pr": float(r[2] or 0), "avg_wp": float(r[3] or 0)}
                     for r in skip_rows}

            skipped_total = sum(v["n"] for v in skips.values())

            suggestions = []

            # ── 1. LOW_PRED_RET: signals close to pred_ret threshold
            if "LOW_PRED_RET" in skips:
                d = skips["LOW_PRED_RET"]
                n, avg_pr = d["n"], d["avg_pr"]
                near_threshold = _ml_min_pred_ret * 0.7 <= avg_pr < _ml_min_pred_ret
                extra_tpd = n / 7.0
                if n >= 8 and near_threshold and extra_tpd < 3:
                    new_thr = round(max(0.30, _ml_min_pred_ret - 0.15), 2)
                    est_lift = round(avg_pr * 0.5 * extra_tpd, 2)
                    suggestions.append({
                        "key":           "ML_MIN_PRED_RET",
                        "label":         "Lower pred_ret gate",
                        "current":       round(_ml_min_pred_ret, 2),
                        "suggested":     new_thr,
                        "rationale":     (
                            f"{n} signals blocked by LOW_PRED_RET in 7d "
                            f"(avg pred_ret={avg_pr:.2f}%). "
                            f"Lowering to {new_thr} adds ~{extra_tpd:.1f} trades/day "
                            f"with estimated +{est_lift:.2f}% daily PnL lift."
                        ),
                        "est_pnl_lift":  est_lift,
                        "extra_tpd":     round(extra_tpd, 1),
                    })

            # ── 2. LOW_WIN_PROB: signals close to win_prob threshold
            if "LOW_WIN_PROB" in skips:
                d = skips["LOW_WIN_PROB"]
                n, avg_wp = d["n"], d["avg_wp"]
                near_threshold = _ml_min_win_prob * 0.85 <= avg_wp < _ml_min_win_prob
                extra_tpd = n / 7.0
                if n >= 8 and near_threshold and extra_tpd < 2:
                    new_thr = round(max(0.55, _ml_min_win_prob - 0.05), 2)
                    suggestions.append({
                        "key":           "ML_MIN_WIN_PROB",
                        "label":         "Lower win_prob gate",
                        "current":       round(_ml_min_win_prob, 2),
                        "suggested":     new_thr,
                        "rationale":     (
                            f"{n} signals blocked by LOW_WIN_PROB in 7d "
                            f"(avg win_prob={avg_wp:.2f}). Near-threshold signals "
                            f"may have positive expectancy (+{extra_tpd:.1f} trades/day)."
                        ),
                        "est_pnl_lift":  None,
                        "extra_tpd":     round(extra_tpd, 1),
                    })

            # ── 3. DAILY_CAP: good signals blocked by the daily limit
            if "DAILY_CAP" in skips:
                d = skips["DAILY_CAP"]
                n, avg_pr = d["n"], d["avg_pr"]
                extra_tpd = n / 7.0
                if n >= 7 and avg_pr > 0.4 and _daily_trade_cap < 8:
                    new_cap = min(_daily_trade_cap + 1, 7)
                    est_lift = round(avg_pr * 0.5 * (n / 7), 2)
                    suggestions.append({
                        "key":           "DAILY_TRADE_CAP",
                        "label":         "Raise daily trade cap",
                        "current":       _daily_trade_cap,
                        "suggested":     new_cap,
                        "rationale":     (
                            f"{n} signals blocked by DAILY_CAP in 7d "
                            f"(avg pred_ret={avg_pr:.2f}%). Raising cap to {new_cap} "
                            f"adds ~{extra_tpd:.1f} trades/day, est +{est_lift:.2f}% daily lift."
                        ),
                        "est_pnl_lift":  est_lift,
                        "extra_tpd":     round(extra_tpd, 1),
                    })

            if not suggestions:
                return

            c.execute(
                "INSERT INTO pnl_optimizer_log (trade_count, suggestions, skipped_total) "
                "VALUES (?, ?, ?)",
                (_auto_tune_trade_counter, _json.dumps(suggestions), skipped_total),
            )
            c.commit()
            logger.info("[PNL-OPT] %d suggestion(s) at trade #%d (skipped_total=%d)",
                        len(suggestions), _auto_tune_trade_counter, skipped_total)
    except Exception as _e:
        logger.debug("_optimize_pnl_targets error: %s", _e)


def _apply_selectivity_tuning(key: str, value: str) -> bool:
    """
    Update in-memory selectivity / risk globals immediately when apply-tuner fires.
    Called from main.py's apply-tuner endpoint after writing .env.
    Returns True if key was handled, False if unknown.
    """
    global _ml_min_win_prob, _ml_min_pred_ret, _daily_trade_cap
    global _min_interval_min, _daily_max_dd_pct, _hard_trade_cap
    try:
        if key == "ML_MIN_WIN_PROB":
            _ml_min_win_prob = float(value)
        elif key == "ML_MIN_PRED_RET":
            _ml_min_pred_ret = float(value)
        elif key == "DAILY_TRADE_CAP":
            _daily_trade_cap = max(1, int(float(value)))
        elif key == "MIN_INTERVAL_MIN":
            _min_interval_min = float(value)
        elif key == "DAILY_MAX_DD_PCT":
            _daily_max_dd_pct = float(value)
        elif key == "HARD_TRADE_CAP":
            _hard_trade_cap = max(1, int(float(value)))
        else:
            return False
        logger.info("[LIVE-TUNE] %s → %s (applied in-memory)", key, value)
        return True
    except Exception as _e:
        logger.debug("_apply_selectivity_tuning(%s, %s) error: %s", key, value, _e)
        return False

'''

pe = pe.replace(OLD_CB, NEW_FUNCS + "\ndef _check_dynamic_exit_circuit_breaker():", 1)
print("OK [A1] _optimize_pnl_targets + _apply_selectivity_tuning added")

# ── A2: Call _optimize_pnl_targets every 15 closes in _close_perp_position

OLD_AUTOTUNE = (
    "    _auto_tune_trade_counter += 1\n"
    "    _auto_tune_exit_params()\n"
    "    _selectivity_tune_counter += 1\n"
    "    _auto_tune_selectivity()"
)
NEW_AUTOTUNE = (
    "    _auto_tune_trade_counter += 1\n"
    "    _auto_tune_exit_params()\n"
    "    _selectivity_tune_counter += 1\n"
    "    _auto_tune_selectivity()\n"
    "    if _auto_tune_trade_counter % 15 == 0:\n"
    "        _optimize_pnl_targets()"
)
assert OLD_AUTOTUNE in pe, "FAIL [A2]: auto-tune counter block not found"
pe = pe.replace(OLD_AUTOTUNE, NEW_AUTOTUNE, 1)
print("OK [A2] _optimize_pnl_targets called every 15 closes")

PE.write_text(pe)
print("perp_executor.py updated.\n")

# ─────────────────────────────────────────────────────────────────────────────
# B. main.py
# ─────────────────────────────────────────────────────────────────────────────

MP = pathlib.Path("/root/memecoin_engine/dashboard/backend/main.py")
assert MP.exists()
mp = MP.read_text()

# ── B1: Extend ALLOWED_KEYS

OLD_KEYS = (
    '        "ML_MIN_WIN_PROB", "VOL_FILTER_THRESHOLD", "VOL_SIZE_MULT",\n'
    '        "PUMP_DUMP_THRESHOLD", "ALERT_THRESHOLD", "REGIME_MIN_SCORE",\n'
    '    }'
)
NEW_KEYS = (
    '        "ML_MIN_WIN_PROB", "ML_MIN_PRED_RET", "DAILY_TRADE_CAP",\n'
    '        "MIN_INTERVAL_MIN", "DAILY_MAX_DD_PCT", "HARD_TRADE_CAP",\n'
    '        "VOL_FILTER_THRESHOLD", "VOL_SIZE_MULT",\n'
    '        "PUMP_DUMP_THRESHOLD", "ALERT_THRESHOLD", "REGIME_MIN_SCORE",\n'
    '    }'
)
assert OLD_KEYS in mp, "FAIL [B1]: ALLOWED_KEYS anchor not found"
mp = mp.replace(OLD_KEYS, NEW_KEYS, 1)
print("OK [B1] ALLOWED_KEYS extended with selectivity/risk params")

# ── B2: Add live in-memory tuning call after os.environ update

OLD_ENVIRON_RELOAD = (
    "    # Reload into os.environ\n"
    "    for key, val in applied.items():\n"
    "        os.environ[key] = val\n"
    "\n"
    "    # Log the tuning event"
)
NEW_ENVIRON_RELOAD = (
    "    # Reload into os.environ + apply in-memory to selectivity globals\n"
    "    for key, val in applied.items():\n"
    "        os.environ[key] = val\n"
    "    try:\n"
    "        import sys as _sys\n"
    "        _pe_mod = (_sys.modules.get('utils.perp_executor')\n"
    "                   or _sys.modules.get('memecoin_engine.utils.perp_executor'))\n"
    "        if _pe_mod and hasattr(_pe_mod, '_apply_selectivity_tuning'):\n"
    "            for key, val in applied.items():\n"
    "                _pe_mod._apply_selectivity_tuning(key, val)\n"
    "    except Exception:\n"
    "        pass\n"
    "\n"
    "    # Log the tuning event"
)
assert OLD_ENVIRON_RELOAD in mp, "FAIL [B2]: os.environ reload anchor not found"
mp = mp.replace(OLD_ENVIRON_RELOAD, NEW_ENVIRON_RELOAD, 1)
print("OK [B2] Live in-memory tuning call added to apply-tuner")

# ── B3+B4: Add pnl-optimizer and missed-opportunities endpoints before journal/learnings

JOURNAL_ANCHOR = '@app.get("/api/journal/learnings")'
assert JOURNAL_ANCHOR in mp, "FAIL [B3]: journal/learnings anchor not found"

NEW_ENDPOINTS = '''@app.get("/api/brain/pnl-optimizer")
async def brain_pnl_optimizer(_: str = Depends(get_current_user)):
    """
    Latest PNL optimization suggestions from _optimize_pnl_targets().
    Returns the most recent batch plus a 7-day suggestion history.
    """
    try:
        import sqlite3 as _sq, pathlib as _pl, json as _js
        db = str(_pl.Path(__file__).resolve().parent.parent.parent / "data_storage" / "engine.db")
        with _sq.connect(db) as c:
            c.row_factory = _sq.Row
            try:
                rows = c.execute("""
                    SELECT id, ts_utc, trade_count, suggestions, skipped_total
                    FROM pnl_optimizer_log
                    ORDER BY ts_utc DESC LIMIT 10
                """).fetchall()
            except Exception:
                rows = []

            history = []
            latest_suggestions = []
            for i, r in enumerate(rows):
                try:
                    sugs = _js.loads(r["suggestions"] or "[]")
                except Exception:
                    sugs = []
                entry = {
                    "id":            r["id"],
                    "ts_utc":        r["ts_utc"],
                    "trade_count":   r["trade_count"],
                    "skipped_total": r["skipped_total"],
                    "suggestions":   sugs,
                }
                history.append(entry)
                if i == 0:
                    latest_suggestions = sugs

        return {
            "latest_suggestions": latest_suggestions,
            "history":            history,
            "has_suggestions":    len(latest_suggestions) > 0,
        }
    except Exception as _e:
        log.warning("brain_pnl_optimizer error: %s", _e)
        return {"latest_suggestions": [], "history": [], "has_suggestions": False}


@app.get("/api/brain/missed-opportunities")
async def brain_missed_opportunities(_: str = Depends(get_current_user)):
    """
    Top skipped signals from the last 24h ordered by predicted return.
    Shows the 'best' signals the gate filtered out — useful for tuning decisions.
    """
    try:
        import sqlite3 as _sq, pathlib as _pl
        from datetime import datetime as _dt, timezone as _tz, timedelta as _td
        db = str(_pl.Path(__file__).resolve().parent.parent.parent / "data_storage" / "engine.db")
        since_24h = (_dt.now(_tz.utc) - _td(hours=24)).isoformat()
        with _sq.connect(db) as c:
            c.row_factory = _sq.Row
            try:
                rows = c.execute("""
                    SELECT ts_utc, symbol, side, mode, skip_reason,
                           ml_wp, pred_ret, sent_boost, regime
                    FROM skipped_signals_log
                    WHERE ts_utc >= ? AND pred_ret > 0
                    ORDER BY pred_ret DESC
                    LIMIT 10
                """, (since_24h,)).fetchall()
            except Exception:
                rows = []

            opps = [dict(r) for r in rows]

        # Annotate with "value" tier
        for o in opps:
            pr = float(o.get("pred_ret") or 0)
            if pr >= 1.5:
                o["tier"] = "HIGH"
            elif pr >= 0.8:
                o["tier"] = "MEDIUM"
            else:
                o["tier"] = "LOW"

        return {
            "opportunities": opps,
            "count":         len(opps),
            "window_h":      24,
        }
    except Exception as _e:
        log.warning("brain_missed_opportunities error: %s", _e)
        return {"opportunities": [], "count": 0, "window_h": 24}


@app.get("/api/journal/learnings")'''

mp = mp.replace(JOURNAL_ANCHOR, NEW_ENDPOINTS, 1)
print("OK [B3+B4] pnl-optimizer + missed-opportunities endpoints added")

MP.write_text(mp)
print("\nPatch 54 applied successfully.")
