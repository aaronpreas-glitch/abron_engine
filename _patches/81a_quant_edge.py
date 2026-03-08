"""
Patch 81a — perp_executor.py: Full Quant Edge Package
1. Config lambdas: Kelly, EV, Moonshot, Portfolio Heat
2. Helper functions: _refresh_kelly_params, _get_portfolio_heat
3. EV Filter + Kelly Sizing + Portfolio Heat Guard + Moonshot Mode (in execute_perp_signal)
4. Regime-aware TP/SL adjustments + notes metadata
5. Moonshot TIME_LIMIT skip in all 3 monitor steps
"""
import subprocess, sys
from pathlib import Path

ROOT = Path("/root/memecoin_engine")
PE   = ROOT / "utils" / "perp_executor.py"

text = PE.read_text()

# ─────────────────────────────────────────────────────────────────────────────
# R1 — Config lambdas after CONVICTION_SIZE_MULT_HIGH, before DB_PATH
# ─────────────────────────────────────────────────────────────────────────────
OLD_R1 = 'CONVICTION_SIZE_MULT_HIGH = lambda: _float("CONVICTION_SIZE_MULT_HIGH", 1.8)\n\nDB_PATH = os.path.join('
NEW_R1 = ('CONVICTION_SIZE_MULT_HIGH = lambda: _float("CONVICTION_SIZE_MULT_HIGH", 1.8)\n'
          '\n'
          '# ── Quant Edge config (Patch 81) ─────────────────────────────────────────────\n'
          'KELLY_FRACTION         = lambda: _float("KELLY_FRACTION", 0.5)\n'
          'MAX_KELLY_CAP          = lambda: _float("MAX_KELLY_CAP", 0.25)\n'
          'EV_MIN_THRESHOLD       = lambda: _float("EV_MIN_THRESHOLD", 0.8)\n'
          'EV_ATR_RATIO_MIN       = lambda: _float("EV_ATR_RATIO_MIN", 1.5)\n'
          'MOONSHOT_MIN_WP        = lambda: _float("MOONSHOT_MIN_WP", 0.85)\n'
          'MOONSHOT_MIN_SENT      = lambda: _float("MOONSHOT_MIN_SENT", 12.0)\n'
          'MOONSHOT_SIZE_MULT     = lambda: _float("MOONSHOT_SIZE_MULT", 4.0)\n'
          'PORTFOLIO_MAX_EXPOSURE = lambda: _float("PORTFOLIO_MAX_EXPOSURE", 800.0)\n'
          '\n'
          'DB_PATH = os.path.join(')

assert OLD_R1 in text, "R1 anchor (CONVICTION + DB_PATH) not found"
text = text.replace(OLD_R1, NEW_R1)
print("✓ R1: Quant config lambdas added")

# ─────────────────────────────────────────────────────────────────────────────
# R2 — Helper functions before _log_skipped_signal
# ─────────────────────────────────────────────────────────────────────────────
OLD_R2 = ('def _log_skipped_signal(symbol, side, mode, reason, ml_wp, pred_ret, sent_boost, regime, notes):\n'
          '    """Insert a row into skipped_signals_log. Swallows all exceptions."""')
NEW_R2 = (
    '# ── Kelly + Portfolio Heat Helpers (Patch 81) ─────────────────────────────────\n'
    '\n'
    '_kelly_cache = {"ts": 0.0, "win_rate": 0.5, "avg_win": 1.0, "avg_loss": 1.0, "kelly_f": 0.0}\n'
    '\n'
    'def _refresh_kelly_params():\n'
    '    """Compute Kelly fraction from last 50 closed paper trades. 5-min cache."""\n'
    '    import time as _t, sqlite3 as _sq\n'
    '    global _kelly_cache\n'
    '    now = _t.time()\n'
    '    if now - _kelly_cache["ts"] < 300:\n'
    '        return _kelly_cache\n'
    '    try:\n'
    '        with _sq.connect(DB_PATH) as c:\n'
    '            rows = c.execute(\n'
    '                "SELECT pnl_pct FROM perp_positions "\n'
    '                "WHERE status=\'CLOSED\' AND dry_run=1 AND pnl_pct IS NOT NULL "\n'
    '                "ORDER BY closed_ts_utc DESC LIMIT 50"\n'
    '            ).fetchall()\n'
    '        if len(rows) < 10:\n'
    '            _kelly_cache["ts"] = now\n'
    '            return _kelly_cache\n'
    '        pnls = [float(r[0]) for r in rows]\n'
    '        wins = [p for p in pnls if p > 0]\n'
    '        losses = [abs(p) for p in pnls if p < 0]\n'
    '        wr = len(wins) / len(pnls) if pnls else 0.5\n'
    '        aw = sum(wins) / len(wins) if wins else 1.0\n'
    '        al = sum(losses) / len(losses) if losses else 1.0\n'
    '        b = aw / al if al > 0 else 1.0   # win/loss ratio\n'
    '        f = (wr * b - (1 - wr)) / b if b > 0 else 0\n'
    '        f = max(f, 0.0)  # never negative\n'
    '        _kelly_cache = {"ts": now, "win_rate": wr, "avg_win": aw, "avg_loss": al, "kelly_f": f}\n'
    '    except Exception as _e:\n'
    '        logger.debug("_refresh_kelly_params error: %s", _e)\n'
    '        _kelly_cache["ts"] = now\n'
    '    return _kelly_cache\n'
    '\n'
    '\n'
    'def _get_portfolio_heat():\n'
    '    """Total open exposure / max allowed. Returns heat 0-100+."""\n'
    '    try:\n'
    '        positions = _get_open_perp_positions()\n'
    '        total = sum(\n'
    '            float(p.get("size_usd") or 0) * float(p.get("leverage") or 1)\n'
    '            for p in positions\n'
    '        )\n'
    '        mx = PORTFOLIO_MAX_EXPOSURE()\n'
    '        return (total / mx * 100) if mx > 0 else 0\n'
    '    except Exception:\n'
    '        return 0\n'
    '\n'
    '\n'
    'def _log_skipped_signal(symbol, side, mode, reason, ml_wp, pred_ret, sent_boost, regime, notes):\n'
    '    """Insert a row into skipped_signals_log. Swallows all exceptions."""'
)

assert OLD_R2 in text, "R2 anchor (_log_skipped_signal) not found"
text = text.replace(OLD_R2, NEW_R2)
print("✓ R2: Kelly + Portfolio Heat helpers added")

# ─────────────────────────────────────────────────────────────────────────────
# R3 — EV Filter + Kelly Sizing + Portfolio Heat + Moonshot (after conviction, before exit levels)
# ─────────────────────────────────────────────────────────────────────────────
OLD_R3 = ('    logger.debug("[CONVICTION] %s ml_wp=%.2f tier_mult=%.1fx combined=%.2fx",\n'
          '                 symbol, _ml_wp_val, _conviction_mult, combined_mult)\n'
          '\n'
          '    # Compute exit levels — scalp uses tight TP/SL, swing uses wide swing targets\n'
          '    if is_scalp:')
NEW_R3 = ('    logger.debug("[CONVICTION] %s ml_wp=%.2f tier_mult=%.1fx combined=%.2fx",\n'
          '                 symbol, _ml_wp_val, _conviction_mult, combined_mult)\n'
          '\n'
          '    # ── EV Filter (Patch 81) ─────────────────────────────────────────────────\n'
          '    _pred_ret_raw = _pred_ret_v\n'
          '    _expected_loss_pct = (PERP_STOP_PCT() if not is_scalp else SCALP_STOP_PCT()) / 100\n'
          '    if is_mid:\n'
          '        _expected_loss_pct = MID_STOP_PCT() / 100\n'
          '    _ev_raw = (_ml_wp_val * _pred_ret_raw) - ((1 - _ml_wp_val) * _expected_loss_pct)\n'
          '    _ev_pct = _ev_raw * 100\n'
          '    _atr_v = float(signal.get("atr_pct") or 0.5)\n'
          '    _ev_atr_ratio = _ev_pct / _atr_v if _atr_v > 0 else 0\n'
          '    if _ev_pct < EV_MIN_THRESHOLD() or _ev_atr_ratio < EV_ATR_RATIO_MIN():\n'
          '        _log_skipped_signal(symbol, side, mode_tag,\n'
          '                            f"EV_FILTER: ev={_ev_pct:.3f}% ev_atr={_ev_atr_ratio:.2f}",\n'
          '                            _ml_wp_val, _pred_ret_v, sent_boost, regime, "")\n'
          '        logger.info("[EV-FILTER] Skipped %s %s %s — ev=%.3f%% ev_atr=%.2f",\n'
          '                    mode_tag, symbol, side, _ev_pct, _ev_atr_ratio)\n'
          '        return False\n'
          '\n'
          '    # ── Kelly Criterion Sizing (Patch 81) ─────────────────────────────────────\n'
          '    _kp = _refresh_kelly_params()\n'
          '    _kelly_raw = _kp["kelly_f"]\n'
          '    _half_kelly = _kelly_raw * KELLY_FRACTION()\n'
          '    if _half_kelly > 0.01 and _kp["win_rate"] > 0:\n'
          '        _kelly_mult = min(_half_kelly * 10, 3.0)  # 5% half-kelly → 0.5x, 15% → 1.5x\n'
          '        combined_mult = min(round(combined_mult * _kelly_mult, 2), 3.0)\n'
          '        logger.debug("[KELLY] %s f=%.3f half=%.3f mult=%.2fx combined=%.2fx",\n'
          '                     symbol, _kelly_raw, _half_kelly, _kelly_mult, combined_mult)\n'
          '\n'
          '    # ── Portfolio Heat Guard (Patch 81) ───────────────────────────────────────\n'
          '    _heat = _get_portfolio_heat()\n'
          '    _heat_max = 60.0\n'
          '    if "TRANSITION" in regime.upper():\n'
          '        _heat_max = 30.0\n'
          '    elif "BULL" in regime.upper():\n'
          '        _heat_max = 80.0\n'
          '    if _heat > _heat_max:\n'
          '        _log_skipped_signal(symbol, side, mode_tag,\n'
          '                            f"PORTFOLIO_HEAT: heat={_heat:.0f}% max={_heat_max:.0f}%",\n'
          '                            _ml_wp_val, _pred_ret_v, sent_boost, regime, "")\n'
          '        logger.info("[HEAT-GUARD] Skipped %s %s %s — heat=%.0f%% max=%.0f%%",\n'
          '                    mode_tag, symbol, side, _heat, _heat_max)\n'
          '        return False\n'
          '    elif _heat > _heat_max * 0.75:\n'
          '        combined_mult = round(combined_mult * 0.5, 2)\n'
          '        logger.info("[HEAT-REDUCE] %s heat=%.0f%% → size reduced 50%%", symbol, _heat)\n'
          '\n'
          '    # ── Moonshot Mode (Patch 81) ──────────────────────────────────────────────\n'
          '    _is_moonshot = False\n'
          '    if (_ml_wp_val >= MOONSHOT_MIN_WP()\n'
          '            and sent_boost >= MOONSHOT_MIN_SENT()\n'
          '            and "BULL" in regime.upper()):\n'
          '        _is_moonshot = True\n'
          '        combined_mult = round(combined_mult * MOONSHOT_SIZE_MULT(), 2)\n'
          '        logger.info("[MOONSHOT] %s %s — ml_wp=%.2f sent=%d regime=%s → %.1fx size",\n'
          '                    symbol, side, _ml_wp_val, int(sent_boost), regime, combined_mult)\n'
          '\n'
          '    # Compute exit levels — scalp uses tight TP/SL, swing uses wide swing targets\n'
          '    if is_scalp:')

assert OLD_R3 in text, "R3 anchor (conviction debug + Compute exit levels) not found"
text = text.replace(OLD_R3, NEW_R3)
print("✓ R3: EV + Kelly + Heat + Moonshot blocks added")

# ─────────────────────────────────────────────────────────────────────────────
# R4 — Regime TP/SL adjustments + notes metadata (after SWING logger, before Real-money)
# ─────────────────────────────────────────────────────────────────────────────
OLD_R4 = ('        logger.info(\n'
          '            "[PERP %s] %s %s @ $%.4f  stop=$%.4f  TP1=$%.4f  TP2=$%.4f  size=$%.0f x%.1f",\n'
          '            paper_tag, side, symbol, entry_price, stop_price, tp1_price, tp2_price, size_usd, leverage,\n'
          '        )\n'
          '\n'
          '    # Real-money / simulate mode: scale size + cap at balance % + log decision\n'
          '    _rm_mode  = _real_money_mode')
NEW_R4 = ('        logger.info(\n'
          '            "[PERP %s] %s %s @ $%.4f  stop=$%.4f  TP1=$%.4f  TP2=$%.4f  size=$%.0f x%.1f",\n'
          '            paper_tag, side, symbol, entry_price, stop_price, tp1_price, tp2_price, size_usd, leverage,\n'
          '        )\n'
          '\n'
          '    # ── Regime-Aware TP/SL + Moonshot Adjustments (Patch 81) ───────────────\n'
          '    if _ml_wp_val >= 0.75:\n'
          '        # Widen TP 30% from entry on high conviction\n'
          '        if side == "LONG":\n'
          '            tp1_price = entry_price + (tp1_price - entry_price) * 1.3\n'
          '            if tp2_price and tp2_price != tp1_price:\n'
          '                tp2_price = entry_price + (tp2_price - entry_price) * 1.3\n'
          '        else:\n'
          '            tp1_price = entry_price - (entry_price - tp1_price) * 1.3\n'
          '            if tp2_price and tp2_price != tp1_price:\n'
          '                tp2_price = entry_price - (entry_price - tp2_price) * 1.3\n'
          '    if "TRANSITION" in regime.upper():\n'
          '        # Tighten SL 10% in transition to avoid whipsaws\n'
          '        if side == "LONG":\n'
          '            stop_price = entry_price - (entry_price - stop_price) * 0.9\n'
          '        else:\n'
          '            stop_price = entry_price + (stop_price - entry_price) * 0.9\n'
          '    if _is_moonshot:\n'
          '        # Moonshot: extra-wide TP (50% wider)\n'
          '        if side == "LONG":\n'
          '            tp1_price = entry_price + (tp1_price - entry_price) * 1.5\n'
          '            if tp2_price and tp2_price != tp1_price:\n'
          '                tp2_price = entry_price + (tp2_price - entry_price) * 1.5\n'
          '        else:\n'
          '            tp1_price = entry_price - (entry_price - tp1_price) * 1.5\n'
          '            if tp2_price and tp2_price != tp1_price:\n'
          '                tp2_price = entry_price - (entry_price - tp2_price) * 1.5\n'
          '\n'
          '    # Append quant metadata to notes (Patch 81)\n'
          '    notes += f"|ev={_ev_pct:.3f}|kelly={_kelly_raw:.3f}|heat={_heat:.0f}"\n'
          '    if _is_moonshot:\n'
          '        notes += "|MOONSHOT=1"\n'
          '\n'
          '    # Real-money / simulate mode: scale size + cap at balance % + log decision\n'
          '    _rm_mode  = _real_money_mode')

assert OLD_R4 in text, "R4 anchor (PERP logger + Real-money) not found"
text = text.replace(OLD_R4, NEW_R4)
print("✓ R4: Regime TP/SL adjustments + notes metadata added")

# ─────────────────────────────────────────────────────────────────────────────
# R5 — perp_monitor_step: Moonshot TIME_LIMIT skip
# ─────────────────────────────────────────────────────────────────────────────
OLD_R5 = ('        # TIME_LIMIT fallback\n'
          '        if exit_reason is None and age_h >= max_hold:\n'
          '            exit_reason = "TIME_LIMIT"\n'
          '\n'
          '        if exit_reason:\n'
          '            result = _close_perp_position(pos_id, price, exit_reason)\n'
          '            mode   = "PAPER" if pos["dry_run"] else "LIVE"')
NEW_R5 = ('        # TIME_LIMIT fallback — skip for Moonshot positions (Patch 81)\n'
          '        if exit_reason is None and age_h >= max_hold:\n'
          '            if "MOONSHOT" not in (pos.get("notes") or ""):\n'
          '                exit_reason = "TIME_LIMIT"\n'
          '\n'
          '        if exit_reason:\n'
          '            result = _close_perp_position(pos_id, price, exit_reason)\n'
          '            mode   = "PAPER" if pos["dry_run"] else "LIVE"')

assert OLD_R5 in text, "R5 anchor (perp_monitor TIME_LIMIT) not found"
text = text.replace(OLD_R5, NEW_R5)
print("✓ R5: perp_monitor_step Moonshot TIME_LIMIT skip")

# ─────────────────────────────────────────────────────────────────────────────
# R6 — scalp_monitor_step: Moonshot TIME_LIMIT skip
# ─────────────────────────────────────────────────────────────────────────────
OLD_R6 = ('        # TIME_LIMIT fallback\n'
          '        if exit_reason is None and age_h >= max_hold_h:\n'
          '            exit_reason = "TIME_LIMIT"\n'
          '\n'
          '        if exit_reason:\n'
          '            result = _close_perp_position(pos_id, price, exit_reason)\n'
          '            paper_label = "PAPER" if pos["dry_run"] else "LIVE"')
NEW_R6 = ('        # TIME_LIMIT fallback — skip for Moonshot positions (Patch 81)\n'
          '        if exit_reason is None and age_h >= max_hold_h:\n'
          '            if "MOONSHOT" not in (pos.get("notes") or ""):\n'
          '                exit_reason = "TIME_LIMIT"\n'
          '\n'
          '        if exit_reason:\n'
          '            result = _close_perp_position(pos_id, price, exit_reason)\n'
          '            paper_label = "PAPER" if pos["dry_run"] else "LIVE"')

assert OLD_R6 in text, "R6 anchor (scalp_monitor TIME_LIMIT) not found"
text = text.replace(OLD_R6, NEW_R6)
print("✓ R6: scalp_monitor_step Moonshot TIME_LIMIT skip")

# ─────────────────────────────────────────────────────────────────────────────
# R7 — mid_monitor_step: Moonshot TIME_LIMIT skip (combined with winner_extended)
# ─────────────────────────────────────────────────────────────────────────────
OLD_R7 = ("        # TIME_LIMIT: skip if winner-run extension is active (trade is running hot)\n"
          "        if reason is None and age_h >= max_hold_h and not _winner_extended:\n"
          "            reason = 'TIME_LIMIT'")
NEW_R7 = ("        # TIME_LIMIT: skip if winner-run extension OR Moonshot is active (Patch 81)\n"
          "        if reason is None and age_h >= max_hold_h and not _winner_extended:\n"
          "            if \"MOONSHOT\" not in (pos.get(\"notes\") or \"\"):\n"
          "                reason = 'TIME_LIMIT'")

assert OLD_R7 in text, "R7 anchor (mid_monitor TIME_LIMIT + winner_extended) not found"
text = text.replace(OLD_R7, NEW_R7)
print("✓ R7: mid_monitor_step Moonshot TIME_LIMIT skip")

# ─────────────────────────────────────────────────────────────────────────────
# Write + compile
# ─────────────────────────────────────────────────────────────────────────────
PE.write_text(text)

r = subprocess.run(
    [sys.executable, "-m", "py_compile", str(PE)],
    capture_output=True, text=True
)
if r.returncode != 0:
    print("✗ compile error:", r.stderr)
    sys.exit(1)
print("✓ perp_executor.py compiles OK")
print("✓ Patch 81a complete")
