#!/usr/bin/env python3
"""
Create utils/backtester.py — replay historical trades with current tuner params.
And add /api/backtest/* endpoints to main.py.
"""

# ── Part 1: Create backtester module ──

BACKTESTER_CODE = r'''"""
Backtester — Replay historical trades with current or custom parameters.

Simulates what would have happened if we used different TP/SL settings,
different entry filters, or ML predictions.
"""
import os, json, logging, sqlite3
from datetime import datetime, timezone, timedelta

log = logging.getLogger("backtester")

_ENGINE_ROOT = os.environ.get("ENGINE_ROOT", "/root/memecoin_engine")
_DB_PATH     = os.path.join(_ENGINE_ROOT, "data_storage", "engine.db")


def _get_trades(lookback_days: int = 30) -> list[dict]:
    """Fetch closed perp trades within lookback window."""
    if not os.path.exists(_DB_PATH):
        return []
    cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).isoformat()
    with sqlite3.connect(_DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT pp.*, pet.missed_pct_5m, pet.missed_pct_15m, pet.missed_pct_30m,
                   pet.would_have_continued
            FROM perp_positions pp
            LEFT JOIN post_exit_tracking pet ON pet.position_id = pp.id
            WHERE pp.status = 'CLOSED' AND pp.pnl_pct IS NOT NULL
              AND pp.closed_ts_utc >= ?
            ORDER BY pp.closed_ts_utc ASC
        """, (cutoff,)).fetchall()
    return [dict(r) for r in rows]


def _parse_notes(notes: str) -> dict:
    result = {}
    if not notes:
        return result
    for part in notes.split("|"):
        if "=" in part:
            k, v = part.split("=", 1)
            result[k.strip()] = v.strip()
    return result


def run_backtest(
    lookback_days: int = 30,
    tp_mult: float = 1.0,
    sl_mult: float = 1.0,
    min_win_prob: float = 0.0,
    mode_filter: str = "",
) -> dict:
    """Run a backtest simulation.

    Args:
        lookback_days: How many days back to simulate
        tp_mult: Multiply TP levels by this factor (1.0 = current, 1.5 = wider)
        sl_mult: Multiply SL levels by this factor
        min_win_prob: Only take trades with ML win_prob >= this (0 = all)
        mode_filter: "SCALP", "MID", "SWING", or "" for all

    Returns comprehensive backtest results.
    """
    trades = _get_trades(lookback_days)
    if not trades:
        return {"status": "no_data", "lookback_days": lookback_days}

    # Try to get ML predictions for filtering
    ml_predictions = {}
    try:
        from utils.ml_predictor import predict_signal, _parse_notes as ml_parse
        for trade in trades:
            notes_p = _parse_notes(trade.get("notes", "") or "")
            signal = {
                "side": trade["side"],
                "source": notes_p.get("source", "auto"),
                "rsi_14": float(notes_p.get("rsi_14", 50)),
                "macd_hist": float(notes_p.get("macd_hist", 0)) if notes_p.get("macd_hist") and notes_p.get("macd_hist") != "None" else None,
                "macd_cross": notes_p.get("macd_cross") if notes_p.get("macd_cross") != "None" else None,
                "atr_pct": float(notes_p.get("atr_pct", 0.5)) if notes_p.get("atr_pct") and notes_p.get("atr_pct") != "None" else None,
                "momentum_5m": float(notes_p.get("momentum_5m", 0)),
                "momentum_15m": float(notes_p.get("momentum_15m", 0)),
                "regime_label": notes_p.get("regime", trade.get("regime_label", "TRANSITION")),
            }
            pred = predict_signal(signal)
            if pred:
                ml_predictions[trade["id"]] = pred
    except Exception as e:
        log.debug("ML predictions unavailable for backtest: %s", e)

    # Simulate
    actual_results = []
    simulated_results = []
    filtered_out = 0

    for trade in trades:
        notes_p = _parse_notes(trade.get("notes", "") or "")
        mode = notes_p.get("mode", "SWING")

        # Mode filter
        if mode_filter and mode.upper() != mode_filter.upper():
            continue

        # ML filter
        ml_pred = ml_predictions.get(trade["id"])
        if min_win_prob > 0 and ml_pred:
            if ml_pred["win_prob"] < min_win_prob:
                filtered_out += 1
                continue

        pnl = float(trade["pnl_pct"] or 0)
        entry = float(trade["entry_price"] or 0)
        exit_p = float(trade["exit_price"] or 0)
        stop = float(trade["stop_price"] or 0)
        tp1 = float(trade["tp1_price"] or 0)
        tp2 = float(trade["tp2_price"] or 0) if trade.get("tp2_price") else None
        side = trade["side"]
        mae = float(trade["mae"] or 0)
        mfe = float(trade["mfe"] or 0)
        missed_30 = float(trade.get("missed_pct_30m") or 0)

        actual_results.append({
            "id": trade["id"],
            "symbol": trade["symbol"],
            "side": side,
            "mode": mode,
            "pnl_pct": pnl,
            "exit_reason": trade["exit_reason"],
            "entry_price": entry,
            "ml_win_prob": ml_pred["win_prob"] if ml_pred else None,
            "ml_pred_return": ml_pred["pred_return"] if ml_pred else None,
        })

        # Simulate with adjusted TP/SL
        if entry > 0 and stop > 0:
            if side == "LONG":
                sim_stop = entry - (entry - stop) * sl_mult
                sim_tp1 = entry + (tp1 - entry) * tp_mult if tp1 > entry else tp1

                # Would the adjusted SL have saved us?
                # If MFE shows price went up enough to hit wider TP
                if tp_mult != 1.0 and mfe > 0:
                    max_price = entry * (1 + mfe)
                    if max_price >= sim_tp1 > 0:
                        sim_pnl = (sim_tp1 - entry) / entry * 100
                    elif exit_p > 0:
                        sim_pnl = pnl  # same exit
                    else:
                        sim_pnl = pnl
                else:
                    sim_pnl = pnl

                # Would tighter SL have cut losses sooner?
                if sl_mult < 1.0 and mae < 0:
                    min_price = entry * (1 + mae)
                    if min_price <= sim_stop:
                        sim_pnl = (sim_stop - entry) / entry * 100  # stopped out earlier
                    elif pnl > 0:
                        sim_pnl = pnl  # still won
            else:
                sim_stop = entry + (stop - entry) * sl_mult
                sim_tp1 = entry - (entry - tp1) * tp_mult if tp1 < entry else tp1

                if tp_mult != 1.0 and mfe > 0:
                    min_price = entry * (1 - mfe)
                    if tp1 > 0 and min_price <= sim_tp1:
                        sim_pnl = (entry - sim_tp1) / entry * 100
                    else:
                        sim_pnl = pnl
                else:
                    sim_pnl = pnl

                if sl_mult < 1.0 and mae < 0:
                    max_price = entry * (1 - mae)
                    if max_price >= sim_stop:
                        sim_pnl = (entry - sim_stop) / entry * 100
                    elif pnl > 0:
                        sim_pnl = pnl
        else:
            sim_pnl = pnl

        simulated_results.append({
            "id": trade["id"],
            "symbol": trade["symbol"],
            "side": side,
            "mode": mode,
            "actual_pnl": pnl,
            "simulated_pnl": round(sim_pnl, 4),
            "exit_reason": trade["exit_reason"],
            "missed_30m": missed_30,
        })

    if not actual_results:
        return {"status": "no_matching_trades", "lookback_days": lookback_days}

    # Compute summary stats
    n = len(actual_results)
    actual_wins = sum(1 for r in actual_results if r["pnl_pct"] > 0)
    actual_total_pnl = sum(r["pnl_pct"] for r in actual_results)
    actual_avg_pnl = actual_total_pnl / n

    sim_wins = sum(1 for r in simulated_results if r["simulated_pnl"] > 0)
    sim_total_pnl = sum(r["simulated_pnl"] for r in simulated_results)
    sim_avg_pnl = sim_total_pnl / n

    # Equity curves
    actual_equity = []
    sim_equity = []
    cum_actual = 0
    cum_sim = 0
    for i, (a, s) in enumerate(zip(actual_results, simulated_results)):
        cum_actual += a["pnl_pct"]
        cum_sim += s["simulated_pnl"]
        actual_equity.append({"trade": i + 1, "actual": round(cum_actual, 3), "simulated": round(cum_sim, 3)})

    return {
        "status": "complete",
        "params": {
            "lookback_days": lookback_days,
            "tp_mult": tp_mult,
            "sl_mult": sl_mult,
            "min_win_prob": min_win_prob,
            "mode_filter": mode_filter,
        },
        "summary": {
            "n_trades": n,
            "filtered_out": filtered_out,
            "actual": {
                "wins": actual_wins,
                "losses": n - actual_wins,
                "win_rate": round(actual_wins / n * 100, 1),
                "avg_pnl": round(actual_avg_pnl, 3),
                "total_pnl": round(actual_total_pnl, 3),
            },
            "simulated": {
                "wins": sim_wins,
                "losses": n - sim_wins,
                "win_rate": round(sim_wins / n * 100, 1),
                "avg_pnl": round(sim_avg_pnl, 3),
                "total_pnl": round(sim_total_pnl, 3),
            },
            "delta": {
                "win_rate": round((sim_wins / n - actual_wins / n) * 100, 1),
                "avg_pnl": round(sim_avg_pnl - actual_avg_pnl, 3),
                "total_pnl": round(sim_total_pnl - actual_total_pnl, 3),
            },
        },
        "equity_curve": actual_equity,
        "trades": simulated_results,
        "by_mode": _group_by_mode(simulated_results, actual_results),
    }


def _group_by_mode(simulated: list, actual: list) -> dict:
    """Group results by trading mode."""
    modes = {}
    for a, s in zip(actual, simulated):
        mode = s["mode"]
        if mode not in modes:
            modes[mode] = {"actual_pnls": [], "sim_pnls": [], "n": 0}
        modes[mode]["actual_pnls"].append(a["pnl_pct"])
        modes[mode]["sim_pnls"].append(s["simulated_pnl"])
        modes[mode]["n"] += 1

    result = {}
    for mode, data in modes.items():
        n = data["n"]
        a_wins = sum(1 for p in data["actual_pnls"] if p > 0)
        s_wins = sum(1 for p in data["sim_pnls"] if p > 0)
        result[mode] = {
            "n": n,
            "actual_wr": round(a_wins / n * 100, 1) if n > 0 else 0,
            "sim_wr": round(s_wins / n * 100, 1) if n > 0 else 0,
            "actual_avg": round(sum(data["actual_pnls"]) / n, 3) if n > 0 else 0,
            "sim_avg": round(sum(data["sim_pnls"]) / n, 3) if n > 0 else 0,
        }
    return result
'''

MODULE_PATH = "/root/memecoin_engine/utils/backtester.py"
with open(MODULE_PATH, "w") as f:
    f.write(BACKTESTER_CODE)
print(f"✅ Written {MODULE_PATH}")


# ── Part 2: Add backtest API endpoints to main.py ──

MAIN_PY = "/root/memecoin_engine/dashboard/backend/main.py"
with open(MAIN_PY, "r") as f:
    mcode = f.read()

BACKTEST_ENDPOINTS = '''

# ── Backtest Endpoints ─────────────────────────────────────────────────────────

@app.get("/api/backtest/run")
async def backtest_run(
    lookback_days: int = 30,
    tp_mult: float = 1.0,
    sl_mult: float = 1.0,
    min_win_prob: float = 0.0,
    mode: str = "",
):
    """Run a backtest simulation with given parameters."""
    import sys
    root = _engine_root()
    if root not in sys.path:
        sys.path.insert(0, root)
    try:
        from utils.backtester import run_backtest
        return run_backtest(
            lookback_days=lookback_days,
            tp_mult=tp_mult,
            sl_mult=sl_mult,
            min_win_prob=min_win_prob,
            mode_filter=mode,
        )
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/backtest/compare")
async def backtest_compare(lookback_days: int = 30):
    """Run multiple backtest scenarios and compare results."""
    import sys
    root = _engine_root()
    if root not in sys.path:
        sys.path.insert(0, root)
    try:
        from utils.backtester import run_backtest

        scenarios = [
            {"label": "Current", "tp_mult": 1.0, "sl_mult": 1.0, "min_win_prob": 0.0},
            {"label": "Wider TP (+50%)", "tp_mult": 1.5, "sl_mult": 1.0, "min_win_prob": 0.0},
            {"label": "Tighter SL (-30%)", "tp_mult": 1.0, "sl_mult": 0.7, "min_win_prob": 0.0},
            {"label": "ML Filter (>50%)", "tp_mult": 1.0, "sl_mult": 1.0, "min_win_prob": 0.5},
            {"label": "ML + Wider TP", "tp_mult": 1.5, "sl_mult": 1.0, "min_win_prob": 0.5},
            {"label": "Conservative", "tp_mult": 0.8, "sl_mult": 0.8, "min_win_prob": 0.6},
        ]

        results = []
        for sc in scenarios:
            bt = run_backtest(
                lookback_days=lookback_days,
                tp_mult=sc["tp_mult"],
                sl_mult=sc["sl_mult"],
                min_win_prob=sc["min_win_prob"],
            )
            if bt.get("status") == "complete":
                results.append({
                    "label": sc["label"],
                    "params": sc,
                    "n_trades": bt["summary"]["n_trades"],
                    "filtered_out": bt["summary"]["filtered_out"],
                    "actual_wr": bt["summary"]["actual"]["win_rate"],
                    "sim_wr": bt["summary"]["simulated"]["win_rate"],
                    "actual_avg_pnl": bt["summary"]["actual"]["avg_pnl"],
                    "sim_avg_pnl": bt["summary"]["simulated"]["avg_pnl"],
                    "actual_total_pnl": bt["summary"]["actual"]["total_pnl"],
                    "sim_total_pnl": bt["summary"]["simulated"]["total_pnl"],
                })
        return {"scenarios": results, "lookback_days": lookback_days}
    except Exception as e:
        return {"error": str(e)}

'''

if '/api/backtest/run' not in mcode:
    # Insert before ML brain endpoints or at end
    if '/api/brain/ml-status' in mcode:
        idx = mcode.find('/api/brain/ml-status')
        # Find the @app.get before it
        at_idx = mcode.rfind('\n@app.', 0, idx)
        if at_idx > 0:
            mcode = mcode[:at_idx] + BACKTEST_ENDPOINTS + mcode[at_idx:]
        else:
            mcode += BACKTEST_ENDPOINTS
    else:
        mcode += BACKTEST_ENDPOINTS
    print("✓ Added backtest API endpoints")
else:
    print("⚠ Backtest endpoints already exist")

with open(MAIN_PY, "w") as f:
    f.write(mcode)

print("\n✅ Backtester module + API endpoints deployed")
