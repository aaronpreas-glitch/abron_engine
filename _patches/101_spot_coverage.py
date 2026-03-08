"""
Patch 101 — Spot Coverage + Duplicate Trade Fix

Two problems fixed:

A. Spot duplicate trades (restart replay bug)
   Root cause: _spot_signal_scan_loop initialises _spot_last_signal_id to
   MIN(id)-1 from the last 24h on EVERY restart. With 7 restarts today,
   GRASS and ARC were re-traded each time their previous position closed.
   Fix: use MAX(id) instead — only process genuinely new signals after restart.
   Also add per-symbol cooldown: skip if a CLOSED trade exists for that symbol
   in the last 2 hours (defense-in-depth).

B. Checklist / AI blind to spot trades
   The checklist, verdict logic, AI context, and return dict all query only
   perp_positions. The trades table (spot) is never touched.
   Fix: add parallel spot queries for 24h + 7d, update verdict rules,
   update report sections, update AI context with PERP/SPOT breakdown.

Files patched:
  dashboard/backend/main.py (8 changes)

Verify:
  # Check no more same-symbol duplicates after restart:
  journalctl -u memecoin-dashboard -n 5 --no-pager | grep 'SPOT SCAN'
  # Expected: "Watching for signals after id=NNN" where NNN = current MAX id

  # Check checklist now shows spot + perp:
  TOKEN=$(curl -s -X POST http://localhost:8000/api/auth/login \
    -H 'Content-Type: application/json' \
    -d '{"password":"HArden978ab"}' | python3 -c 'import sys,json; print(json.load(sys.stdin)["token"])')
  curl -s -X POST http://localhost:8000/api/orchestrator/run-daily-checklist \
    -H "Authorization: Bearer $TOKEN" | python3 -m json.tool | grep -E 'spot|perp|verdict'
"""
import sys, os, ast

BASE = '/root/memecoin_engine'
MAIN = os.path.join(BASE, 'dashboard', 'backend', 'main.py')
txt  = open(MAIN).read()

# ─── OP-1: Fix spot scan init pointer ────────────────────────────────────────
# Change MIN(id)-1 from 24h window → MAX(id) so restarts never replay history

OLD_INIT = (
    '            # Start from 24h ago so we catch recent signals on restart\n'
    '            row = _c.execute(\n'
    '                "SELECT COALESCE(MIN(id)-1,0) FROM signals WHERE ts_utc > datetime(\'now\',\'-24 hours\')"\n'
    '            ).fetchone()\n'
    '            _spot_last_signal_id = int(row[0]) if row[0] else 0\n'
    '        log.info("[SPOT SCAN] Initialised. Watching for signals after id=%d", _spot_last_signal_id)'
)
NEW_INIT = (
    '            # Use MAX(id) — never replay history on restart (prevents duplicate trades)\n'
    '            row = _c.execute("SELECT COALESCE(MAX(id), 0) FROM signals").fetchone()\n'
    '            _spot_last_signal_id = int(row[0]) if row[0] else 0\n'
    '        log.info("[SPOT SCAN] Initialised. Watching for signals after id=%d", _spot_last_signal_id)'
)
assert OLD_INIT in txt, 'Anchor not found: spot scan init pointer'
txt = txt.replace(OLD_INIT, NEW_INIT)
print('✓ OP-1: spot scan init pointer fixed (MAX id instead of MIN-1 from 24h)')

# ─── OP-2: Add per-symbol cooldown guard ─────────────────────────────────────
# Skip symbol if it had a CLOSED trade within last 2h (defense-in-depth vs restart replay)

OLD_PRICE_CHECK = (
    '                if price <= 0:\n'
    '                    log.debug("[SPOT SCAN] Skipping %s — no price in signal", symbol)\n'
    '                    continue\n'
    '\n'
    '                # ── Portfolio sizing ───────────────────────────────────────────'
)
NEW_PRICE_CHECK = (
    '                if price <= 0:\n'
    '                    log.debug("[SPOT SCAN] Skipping %s — no price in signal", symbol)\n'
    '                    continue\n'
    '\n'
    '                # Cooldown guard: skip if symbol had a closed trade in last 2h (Patch 101)\n'
    '                try:\n'
    '                    with _sq.connect(f"file:{_db_path}?mode=ro", uri=True) as _cg101:\n'
    '                        _rc101 = _cg101.execute(\n'
    '                            "SELECT COUNT(*) FROM trades WHERE symbol=? AND status=\'CLOSED\' "\n'
    '                            "AND closed_ts_utc >= datetime(\'now\',\'-2 hours\')",\n'
    '                            (symbol,)).fetchone()\n'
    '                    if _rc101 and _rc101[0] > 0:\n'
    '                        log.debug("[SPOT SCAN] Cooldown: %s closed trade in last 2h — skipping", symbol)\n'
    '                        continue\n'
    '                except Exception:\n'
    '                    pass\n'
    '\n'
    '                # ── Portfolio sizing ───────────────────────────────────────────'
)
assert OLD_PRICE_CHECK in txt, 'Anchor not found: price <= 0 check before portfolio sizing'
txt = txt.replace(OLD_PRICE_CHECK, NEW_PRICE_CHECK)
print('✓ OP-2: per-symbol cooldown guard added (skips symbol closed in last 2h)')

# ─── OP-3: Init spot vars + add spot DB queries in checklist ─────────────────

OLD_VARS = (
    '    _avg_pnl_7d = 0.0; _skips_24h = 0; _crit_count = 0; _crit_msgs = []\n'
    '    try:\n'
    '        with _sql97.connect(_db97) as _c97:'
)
NEW_VARS = (
    '    _avg_pnl_7d = 0.0; _skips_24h = 0; _crit_count = 0; _crit_msgs = []\n'
    '    # Spot metrics — trades table (Patch 101)\n'
    '    _sp_trades_24h = 0; _sp_wins_24h = 0; _sp_avg_pnl_24h = 0.0\n'
    '    _sp_sl_count_24h = 0; _sp_dd_24h = 0.0; _sp_trades_7d = 0\n'
    '    _sp_wins_7d = 0; _sp_avg_pnl_7d = 0.0; _sp_worst_sym = \'\'; _sp_worst_pnl = 0.0\n'
    '    try:\n'
    '        with _sql97.connect(_db97) as _c97:'
)
assert OLD_VARS in txt, 'Anchor not found: _avg_pnl_7d ... try: with connect'
txt = txt.replace(OLD_VARS, NEW_VARS)

OLD_CRIT_END = (
    "            _crit_count = _al[0] if _al else 0\n"
    "            if _al and _al[1]:\n"
    "                _crit_msgs = _al[1].split('|||')[:5]\n"
    "    except Exception as _dbe97:\n"
    "        log.debug('_run_checklist_sync db error: %s', _dbe97)"
)
NEW_CRIT_END = (
    "            _crit_count = _al[0] if _al else 0\n"
    "            if _al and _al[1]:\n"
    "                _crit_msgs = _al[1].split('|||')[:5]\n"
    "            # ── Spot trades (Patch 101) ────────────────────────────────────\n"
    "            _sp = _c97.execute(\n"
    "                'SELECT COUNT(*), SUM(CASE WHEN pnl_pct > 0 THEN 1 ELSE 0 END), AVG(pnl_pct), '\n"
    "                \"SUM(CASE WHEN exit_reason='STOP_LOSS' THEN 1 ELSE 0 END), \"\n"
    "                'COALESCE(SUM(pnl_pct), 0) '\n"
    "                \"FROM trades WHERE status='CLOSED' AND opened_ts_utc >= ?\",\n"
    "                (_since24,)).fetchone()\n"
    "            if _sp and _sp[0]:\n"
    "                _sp_trades_24h = _sp[0] or 0; _sp_wins_24h = _sp[1] or 0\n"
    "                _sp_avg_pnl_24h = round(_sp[2] or 0.0, 3)\n"
    "                _sp_sl_count_24h = _sp[3] or 0; _sp_dd_24h = round(_sp[4] or 0.0, 2)\n"
    "            _sp7 = _c97.execute(\n"
    "                'SELECT COUNT(*), SUM(CASE WHEN pnl_pct > 0 THEN 1 ELSE 0 END), AVG(pnl_pct) '\n"
    "                \"FROM trades WHERE status='CLOSED' AND opened_ts_utc >= ?\",\n"
    "                (_since7d,)).fetchone()\n"
    "            if _sp7 and _sp7[0]:\n"
    "                _sp_trades_7d = _sp7[0] or 0; _sp_wins_7d = _sp7[1] or 0\n"
    "                _sp_avg_pnl_7d = round(_sp7[2] or 0.0, 3)\n"
    "            _spw = _c97.execute(\n"
    "                \"SELECT symbol, pnl_pct FROM trades WHERE status='CLOSED' AND opened_ts_utc >= ? \"\n"
    "                'ORDER BY pnl_pct ASC LIMIT 1', (_since24,)).fetchone()\n"
    "            if _spw:\n"
    "                _sp_worst_sym = _spw[0] or ''; _sp_worst_pnl = round(_spw[1] or 0.0, 2)\n"
    "    except Exception as _dbe97:\n"
    "        log.debug('_run_checklist_sync db error: %s', _dbe97)"
)
assert OLD_CRIT_END in txt, 'Anchor not found: crit_msgs end + except dbe97'
txt = txt.replace(OLD_CRIT_END, NEW_CRIT_END)
print('✓ OP-3: spot vars + DB queries added to checklist')

# ─── OP-4: Add spot WR calcs + spot backward flags ───────────────────────────

OLD_WR_CALC = (
    '    _wr_24h = round(_wins_24h / _trades_24h * 100, 1) if _trades_24h > 0 else 0.0\n'
    '    _wr_7d  = round(_wins_7d  / _trades_7d  * 100, 1) if _trades_7d  > 0 else 0.0'
)
NEW_WR_CALC = (
    '    _wr_24h = round(_wins_24h / _trades_24h * 100, 1) if _trades_24h > 0 else 0.0\n'
    '    _wr_7d  = round(_wins_7d  / _trades_7d  * 100, 1) if _trades_7d  > 0 else 0.0\n'
    '    _sp_wr_24h = round(_sp_wins_24h / _sp_trades_24h * 100, 1) if _sp_trades_24h > 0 else 0.0\n'
    '    _sp_wr_7d  = round(_sp_wins_7d  / _sp_trades_7d  * 100, 1) if _sp_trades_7d  > 0 else 0.0'
)
assert OLD_WR_CALC in txt, 'Anchor not found: _wr_24h / _wr_7d calc block'
txt = txt.replace(OLD_WR_CALC, NEW_WR_CALC)

# Add spot backward flags after the perp feed check
OLD_FEED_FLAG = (
    "    if _avg_pnl_7d < -0.5 and _trades_7d >= 10:\n"
    "        _backward_flags.append('7d avg PnL ' + ('%.3f' % _avg_pnl_7d) + '% (regression, n=' + str(_trades_7d) + ')')\n"
    "    if not _backward_flags:"
)
NEW_FEED_FLAG = (
    "    if _avg_pnl_7d < -0.5 and _trades_7d >= 10:\n"
    "        _backward_flags.append('7d avg PnL ' + ('%.3f' % _avg_pnl_7d) + '% (regression, n=' + str(_trades_7d) + ')')\n"
    "    # Spot backward flags (Patch 101)\n"
    "    if _sp_avg_pnl_24h < -3.0 and _sp_trades_24h >= 5:\n"
    "        _backward_flags.append('Spot 24h avg PnL ' + ('%.2f' % _sp_avg_pnl_24h) + '% — stop-losses dominating (n=' + str(_sp_trades_24h) + ')')\n"
    "    if _sp_worst_pnl < -8.0 and _sp_worst_sym:\n"
    "        _backward_flags.append('Spot worst: ' + _sp_worst_sym + ' ' + ('%.1f' % _sp_worst_pnl) + '% — oversized loss')\n"
    "    if not _backward_flags:"
)
assert OLD_FEED_FLAG in txt, 'Anchor not found: 7d avg PnL backward flag + if not _backward_flags'
txt = txt.replace(OLD_FEED_FLAG, NEW_FEED_FLAG)
print('✓ OP-4: spot WR calcs + spot backward flags added')

# ─── OP-5: Update report markdown — sections 3 and 4 ────────────────────────

OLD_PERF_24H = (
    "    _R97.append('## 3. Performance (24h)')\n"
    "    _R97.append('Trades: ' + str(_trades_24h) + ' | WR: ' + str(_wr_24h) + '% | Avg PnL: ' + ('%.3f' % _avg_pnl_24h) + '%')\n"
    "    _R97.append('TIME_LIMIT exits: ' + str(_tl_pct_24h) + '% | Stop-loss exits: ' + str(_sl_count_24h))\n"
    "    _R97.append('Cumulative PnL: ' + ('%.2f' % _dd_24h) + '%')\n"
    "    _R97.append('Skipped signals: ' + str(_skips_24h))"
)
NEW_PERF_24H = (
    "    _R97.append('## 3. Performance (24h)')\n"
    "    _R97.append('Perp — Trades: ' + str(_trades_24h) + ' | WR: ' + str(_wr_24h) + '% | Avg PnL: ' + ('%.3f' % _avg_pnl_24h) + '%')\n"
    "    _R97.append('Perp — TIME_LIMIT: ' + str(_tl_pct_24h) + '% | SL exits: ' + str(_sl_count_24h) + ' | Cum PnL: ' + ('%.2f' % _dd_24h) + '%')\n"
    "    _R97.append('Spot — Trades: ' + str(_sp_trades_24h) + ' | WR: ' + str(_sp_wr_24h) + '% | Avg PnL: ' + ('%.3f' % _sp_avg_pnl_24h) + '%')\n"
    "    _R97.append('Spot — SL exits: ' + str(_sp_sl_count_24h) + ' | Cum PnL: ' + ('%.2f' % _sp_dd_24h) + '%'"
    " + ((' | Worst: ' + _sp_worst_sym + ' ' + ('%.1f' % _sp_worst_pnl) + '%') if _sp_worst_sym else ''))\n"
    "    _R97.append('Skipped signals: ' + str(_skips_24h))"
)
assert OLD_PERF_24H in txt, 'Anchor not found: performance 24h section'
txt = txt.replace(OLD_PERF_24H, NEW_PERF_24H)

OLD_PERF_7D = (
    "    _R97.append('## 4. Performance (7d)')\n"
    "    _R97.append('Trades: ' + str(_trades_7d) + ' | WR: ' + str(_wr_7d) + '% | Avg PnL: ' + ('%.3f' % _avg_pnl_7d) + '%')"
)
NEW_PERF_7D = (
    "    _R97.append('## 4. Performance (7d)')\n"
    "    _R97.append('Perp — Trades: ' + str(_trades_7d) + ' | WR: ' + str(_wr_7d) + '% | Avg PnL: ' + ('%.3f' % _avg_pnl_7d) + '%')\n"
    "    _R97.append('Spot — Trades: ' + str(_sp_trades_7d) + ' | WR: ' + str(_sp_wr_7d) + '% | Avg PnL: ' + ('%.3f' % _sp_avg_pnl_7d) + '%')"
)
assert OLD_PERF_7D in txt, 'Anchor not found: performance 7d section'
txt = txt.replace(OLD_PERF_7D, NEW_PERF_7D)
print('✓ OP-5: report markdown sections 3+4 updated with Perp/Spot split')

# ─── OP-6: Update AI context — add SPOT lines ────────────────────────────────

OLD_CTX = (
    '            _ctx99 = (\n'
    '                f"Paper trading bot daily snapshot:\\n"\n'
    '                f"Verdict: {_verdict97}\\n"\n'
    '                f"24h: {_trades_24h} trades | WR {_wr_24h:.1f}% | Avg PnL {_avg_pnl_24h:+.3f}%\\n"\n'
    '                f"24h: TIME_LIMIT={_tl_pct_24h:.1f}% | STOP_LOSS={_sl_count_24h} | DD={_dd_24h:+.2f}%\\n"\n'
    '                f"24h skipped signals: {_skips_24h}\\n"\n'
    '                f"7d: {_trades_7d} trades | WR {_wr_7d:.1f}% | Avg PnL {_avg_pnl_7d:+.3f}%\\n"\n'
    '                f"Gate params: {_json99.dumps(_params97)}\\n"\n'
    '                f"Backward flags: {_backward_flags}\\n"\n'
    '                f"Stalled agents: {_stalled97}\\n"\n'
    '                f"Rules-based actions (replace these with better ones): {_next97}\\n"\n'
    '            )'
)
NEW_CTX = (
    '            _ctx99 = (\n'
    '                f"Paper trading bot daily snapshot:\\n"\n'
    '                f"Verdict: {_verdict97}\\n"\n'
    '                f"PERP 24h: {_trades_24h} trades | WR {_wr_24h:.1f}% | Avg PnL {_avg_pnl_24h:+.3f}%\\n"\n'
    '                f"PERP 24h: TIME_LIMIT={_tl_pct_24h:.1f}% | STOP_LOSS={_sl_count_24h} | DD={_dd_24h:+.2f}%\\n"\n'
    '                f"PERP 7d: {_trades_7d} trades | WR {_wr_7d:.1f}% | Avg PnL {_avg_pnl_7d:+.3f}%\\n"\n'
    '                f"SPOT 24h: {_sp_trades_24h} trades | WR {_sp_wr_24h:.1f}% | Avg PnL {_sp_avg_pnl_24h:+.3f}%\\n"\n'
    '                f"SPOT 24h: STOP_LOSS={_sp_sl_count_24h} | DD={_sp_dd_24h:+.2f}% | Worst: {_sp_worst_sym} {_sp_worst_pnl:+.1f}%\\n"\n'
    '                f"SPOT 7d: {_sp_trades_7d} trades | WR {_sp_wr_7d:.1f}% | Avg PnL {_sp_avg_pnl_7d:+.3f}%\\n"\n'
    '                f"24h skipped signals: {_skips_24h}\\n"\n'
    '                f"Gate params: {_json99.dumps(_params97)}\\n"\n'
    '                f"Backward flags: {_backward_flags}\\n"\n'
    '                f"Stalled agents: {_stalled97}\\n"\n'
    '                f"Rules-based actions (replace these with better ones): {_next97}\\n"\n'
    '            )'
)
assert OLD_CTX in txt, 'Anchor not found: AI context _ctx99 block'
txt = txt.replace(OLD_CTX, NEW_CTX)
print('✓ OP-6: AI context updated with PERP/SPOT split')

# ─── OP-7: Add spot metrics to return dict summary ───────────────────────────

OLD_SUMMARY = (
    "        'summary': {\n"
    "            'trades_24h': _trades_24h,\n"
    "            'win_rate_24h': _wr_24h,\n"
    "            'avg_pnl_24h': _avg_pnl_24h,\n"
    "            'time_limit_pct_24h': _tl_pct_24h,\n"
    "            'dd_24h': _dd_24h,\n"
    "            'skips_24h': _skips_24h,\n"
    "            'critical_alerts_24h': _crit_count,\n"
    "            'agents_stalled': _stalled97,\n"
    "            'feed_ok': _feed_ok97,\n"
    "        },"
)
NEW_SUMMARY = (
    "        'summary': {\n"
    "            'trades_24h': _trades_24h,\n"
    "            'win_rate_24h': _wr_24h,\n"
    "            'avg_pnl_24h': _avg_pnl_24h,\n"
    "            'time_limit_pct_24h': _tl_pct_24h,\n"
    "            'dd_24h': _dd_24h,\n"
    "            'skips_24h': _skips_24h,\n"
    "            'critical_alerts_24h': _crit_count,\n"
    "            'agents_stalled': _stalled97,\n"
    "            'feed_ok': _feed_ok97,\n"
    "            # Spot metrics (Patch 101)\n"
    "            'spot_trades_24h': _sp_trades_24h,\n"
    "            'spot_win_rate_24h': _sp_wr_24h,\n"
    "            'spot_avg_pnl_24h': _sp_avg_pnl_24h,\n"
    "            'spot_dd_24h': _sp_dd_24h,\n"
    "            'spot_worst_sym': _sp_worst_sym,\n"
    "            'spot_worst_pnl': _sp_worst_pnl,\n"
    "        },"
)
assert OLD_SUMMARY in txt, 'Anchor not found: summary dict in return'
txt = txt.replace(OLD_SUMMARY, NEW_SUMMARY)
print('✓ OP-7: spot metrics added to return dict summary')

# Write
open(MAIN, 'w').write(txt)
print(f'\n✓ main.py written ({len(txt):,} bytes)')

# Syntax check
try:
    ast.parse(open(MAIN).read())
    print('✓ Syntax check: main.py OK')
except SyntaxError as e:
    print(f'✗ SYNTAX ERROR: {e}')
    sys.exit(1)

print('\nAll done. Run: systemctl restart memecoin-dashboard')
print('\nVerify:')
print('  1. journalctl -u memecoin-dashboard -n 5 --no-pager | grep "SPOT SCAN"')
print('     Expected: id = current MAX (not old 221)')
print('  2. Trigger checklist and check spot_ fields in response')
