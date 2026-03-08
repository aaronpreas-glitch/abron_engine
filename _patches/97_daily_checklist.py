#!/usr/bin/env python3
"""Patch 97 — Forward-Progress Auditor: Daily Checklist + System Status Card

Changes:
  Step 0 : VPS orchestrator.py — add _checklist_result state + get/set fns
  Step 1a: main.py import block — add _orch_set_cl, _orch_get_cl
  Step 1b: main.py — insert _run_checklist_sync() before _watchdog_agent_loop
  Step 1c: main.py — add 2 endpoints: checklist-result + run-daily-checklist
"""
import ast
from pathlib import Path

VPS_ROOT = Path('/root/memecoin_engine')
ORC_PATH = VPS_ROOT / 'utils' / 'orchestrator.py'
MX       = VPS_ROOT / 'dashboard' / 'backend' / 'main.py'

# ── Step 0: Update VPS orchestrator.py ───────────────────────────────────────
print('Step 0: updating orchestrator.py ...')
orc_text = ORC_PATH.read_text()

# 0a — add _checklist_result state vars (idempotent)
OLD_ORC_STATE = "_di_lock = threading.Lock()"
NEW_ORC_STATE = """_di_lock = threading.Lock()
_checklist_result: dict = {}
_cl_lock = threading.Lock()"""
if '_cl_lock' not in orc_text:
    assert orc_text.count(OLD_ORC_STATE) == 1, 'Step 0a anchor not found'
    orc_text = orc_text.replace(OLD_ORC_STATE, NEW_ORC_STATE, 1)
    print('  0a: state vars added')
else:
    print('  0a: already present, skipping')

# 0b — add get/set functions (idempotent)
OLD_ORC_LOAD = "def load_config() -> dict:"
NEW_ORC_FUNCS = '''def set_checklist_result(result: dict) -> None:
    """Store the latest daily checklist result (Patch 97)."""
    global _checklist_result
    with _cl_lock:
        _checklist_result = result


def get_checklist_result() -> dict:
    """Return the latest daily checklist result (Patch 97)."""
    with _cl_lock:
        return _checklist_result.copy()


def load_config() -> dict:'''
if 'get_checklist_result' not in orc_text:
    assert orc_text.count(OLD_ORC_LOAD) == 1, 'Step 0b anchor not found'
    orc_text = orc_text.replace(OLD_ORC_LOAD, NEW_ORC_FUNCS, 1)
    print('  0b: get/set functions added')
else:
    print('  0b: already present, skipping')

ORC_PATH.write_text(orc_text)
print('Step 0: orchestrator.py updated ✓')

# ── Step 1a: Extend main.py orchestrator import ───────────────────────────────
print('Step 1a: extending orchestrator import ...')
text = MX.read_text()

OLD_IMPORT = "    set_data_integrity_status as _orch_set_di, get_data_integrity_status as _orch_get_di)"
NEW_IMPORT = """    set_data_integrity_status as _orch_set_di, get_data_integrity_status as _orch_get_di,
    set_checklist_result as _orch_set_cl, get_checklist_result as _orch_get_cl)"""

if '_orch_set_cl' not in text:
    assert text.count(OLD_IMPORT) == 1, f'Step 1a anchor: {text.count(OLD_IMPORT)} matches'
    text = text.replace(OLD_IMPORT, NEW_IMPORT, 1)
    print('  import block extended')
else:
    print('  already present, skipping')

# Also patch the stubs fallback block if present
OLD_STUBS = "    _orch_get_di   = lambda: {}"
NEW_STUBS = """    _orch_get_di   = lambda: {}
    _orch_set_cl   = lambda r: None
    _orch_get_cl   = lambda: {}"""
if OLD_STUBS in text and '_orch_get_cl' not in text:
    text = text.replace(OLD_STUBS, NEW_STUBS, 1)
    print('  stubs block updated')

print('Step 1a ✓')

# ── Step 1b: Insert _run_checklist_sync() function ───────────────────────────
print('Step 1b: inserting _run_checklist_sync ...')

FUNC_ANCHOR = "async def _watchdog_agent_loop():"

# Using raw triple-single-quoted string so \n inside stays as \n in output file
NEW_FUNC = r'''def _run_checklist_sync() -> dict:
    """Forward-Progress Auditor — sync, called via asyncio.to_thread from endpoint."""
    import sqlite3 as _sql97
    import subprocess as _sp97
    from datetime import datetime as _dt97, timedelta as _td97
    _now = _dt97.utcnow()
    _date_str = _now.strftime('%Y-%m-%d %H:%M UTC')
    _db97 = '/root/memecoin_engine/data_storage/engine.db'
    _env_path = '/root/memecoin_engine/.env'
    _backward_flags = []; _stalled_flags = []; _forward_flags = []

    # ── System Health ──────────────────────────────────────────────────────────
    _agents97 = _orch_status()
    _stalled97 = [a['name'] for a in _agents97 if a['health'] == 'stalled']
    _slow97    = [a['name'] for a in _agents97 if a['health'] == 'slow']
    _alive97   = [a['name'] for a in _agents97 if a['health'] == 'alive']
    _health97  = _orch_get_health()
    _exc_count = 0; _exc_samples = []
    try:
        _since_log = (_now - _td97(hours=1)).strftime('%Y-%m-%d %H:%M:%S')
        _lp97 = _sp97.run(
            ['journalctl', '-u', 'memecoin-dashboard', '--since', _since_log, '--no-pager', '-n', '200'],
            capture_output=True, text=True, timeout=5)
        for _ln97 in _lp97.stdout.splitlines():
            if any(k in _ln97 for k in ('Exception', 'Traceback', 'ERROR ', 'error:')):
                _exc_count += 1
                if len(_exc_samples) < 3:
                    _exc_samples.append(_ln97.strip()[-100:])
    except Exception:
        pass
    if _stalled97:
        _backward_flags.append('Agent(s) stalled: ' + ', '.join(_stalled97))
    if _exc_count > 10:
        _backward_flags.append('High exception rate: ' + str(_exc_count) + ' errors in last 60 min')

    # ── Performance DB queries ─────────────────────────────────────────────────
    _since24 = (_now - _td97(hours=24)).isoformat()
    _since7d  = (_now - _td97(days=7)).isoformat()
    _trades_24h = 0; _wins_24h = 0; _avg_pnl_24h = 0.0; _tl_pct_24h = 0.0
    _sl_count_24h = 0; _dd_24h = 0.0; _trades_7d = 0; _wins_7d = 0
    _avg_pnl_7d = 0.0; _skips_24h = 0; _crit_count = 0; _crit_msgs = []
    try:
        with _sql97.connect(_db97) as _c97:
            _r = _c97.execute(
                'SELECT COUNT(*), SUM(CASE WHEN pnl_pct > 0 THEN 1 ELSE 0 END), AVG(pnl_pct), '
                "SUM(CASE WHEN exit_reason='TIME_LIMIT' THEN 1 ELSE 0 END), "
                "SUM(CASE WHEN exit_reason='STOP_LOSS' THEN 1 ELSE 0 END), "
                'COALESCE(SUM(pnl_pct), 0) '
                "FROM perp_positions WHERE status='CLOSED' AND opened_ts_utc >= ? AND dry_run=1",
                (_since24,)).fetchone()
            if _r and _r[0]:
                _trades_24h = _r[0] or 0; _wins_24h = _r[1] or 0
                _avg_pnl_24h = round(_r[2] or 0.0, 3)
                _tl_c = _r[3] or 0; _sl_count_24h = _r[4] or 0
                _dd_24h = round(_r[5] or 0.0, 2)
                _tl_pct_24h = round(_tl_c / _trades_24h * 100, 1) if _trades_24h > 0 else 0.0
            _r7 = _c97.execute(
                'SELECT COUNT(*), SUM(CASE WHEN pnl_pct > 0 THEN 1 ELSE 0 END), AVG(pnl_pct) '
                "FROM perp_positions WHERE status='CLOSED' AND opened_ts_utc >= ? AND dry_run=1",
                (_since7d,)).fetchone()
            if _r7 and _r7[0]:
                _trades_7d = _r7[0] or 0; _wins_7d = _r7[1] or 0
                _avg_pnl_7d = round(_r7[2] or 0.0, 3)
            _sk = _c97.execute(
                'SELECT COUNT(*) FROM skipped_signals_log WHERE ts_utc >= ?', (_since24,)).fetchone()
            _skips_24h = _sk[0] if _sk else 0
            _al = _c97.execute(
                "SELECT COUNT(*), GROUP_CONCAT(alert_type || ': ' || message, '|||') "
                "FROM performance_alerts_log WHERE severity='CRITICAL' AND ts_utc >= ?",
                (_since24,)).fetchone()
            _crit_count = _al[0] if _al else 0
            if _al and _al[1]:
                _crit_msgs = _al[1].split('|||')[:5]
    except Exception as _dbe97:
        log.debug('_run_checklist_sync db error: %s', _dbe97)

    _wr_24h = round(_wins_24h / _trades_24h * 100, 1) if _trades_24h > 0 else 0.0
    _wr_7d  = round(_wins_7d  / _trades_7d  * 100, 1) if _trades_7d  > 0 else 0.0

    # ── Gate params from .env ──────────────────────────────────────────────────
    _params97 = {}
    _GATE_KEYS97 = ('ML_MIN_WIN_PROB', 'ML_MIN_PRED_RET', 'EV_MIN_THRESHOLD',
                    'DAILY_TRADE_CAP', 'DAILY_MAX_DD_PCT', 'VOL_FILTER_THRESHOLD')
    try:
        with open(_env_path, 'r') as _ef97:
            for _el97 in _ef97:
                _el97 = _el97.strip()
                if '=' in _el97 and not _el97.startswith('#'):
                    _ek97, _, _ev97 = _el97.partition('=')
                    _ek97 = _ek97.strip(); _ev97 = _ev97.strip().strip('"').strip("'")
                    if _ek97 in _GATE_KEYS97:
                        _params97[_ek97] = _ev97
    except Exception:
        pass

    # ── Data integrity ─────────────────────────────────────────────────────────
    _di97 = _orch_get_di()
    _feeds97 = _di97.get('feeds', {})
    _feed_alerts97 = _di97.get('alerts', [])
    _feed_ok97 = all(
        v.get('status') in ('ok', 'not_configured') for v in _feeds97.values()
    ) if _feeds97 else True

    # ── Verdict rules ──────────────────────────────────────────────────────────
    if _crit_count > 0:
        _backward_flags.append(str(_crit_count) + ' CRITICAL alert(s) fired in 24h')
    if _tl_pct_24h >= 60.0 and _trades_24h >= 5:
        _backward_flags.append('TIME_LIMIT at ' + str(_tl_pct_24h) + '% (>=60% threshold)')
    if _dd_24h <= -6.0 and _trades_24h > 0:
        _backward_flags.append('24h cumulative PnL ' + ('%.2f' % _dd_24h) + '% (<=-6% threshold)')
    if not _feed_ok97 and _feeds97:
        _bad97 = [k for k, v in _feeds97.items() if v.get('status') not in ('ok', 'not_configured')]
        if _bad97:
            _backward_flags.append('Feed(s) failing: ' + ', '.join(_bad97))
    if _avg_pnl_7d < -0.5 and _trades_7d >= 10:
        _backward_flags.append('7d avg PnL ' + ('%.3f' % _avg_pnl_7d) + '% (regression, n=' + str(_trades_7d) + ')')
    if not _backward_flags:
        if _trades_24h == 0 and _skips_24h > 50:
            _stalled_flags.append('0 trades but ' + str(_skips_24h) + ' skipped signals (over-gating)')
        elif _trades_24h >= 5 and _wr_24h < 40.0 and _avg_pnl_24h < 0.0:
            _stalled_flags.append('No edge: WR=' + str(_wr_24h) + '% avg=' + ('%.3f' % _avg_pnl_24h) + '% n=' + str(_trades_24h))
    if not _backward_flags and not _stalled_flags:
        if _avg_pnl_24h > 0.0 and _trades_24h > 0:
            _forward_flags.append('24h avg PnL +' + ('%.3f' % _avg_pnl_24h) + '% positive')
        elif _trades_24h > 0:
            _forward_flags.append('System executing: ' + str(_trades_24h) + ' trades in 24h')
        else:
            _forward_flags.append('System healthy (no trades in 24h window)')

    # Verdict
    if _backward_flags:
        _verdict97 = 'MOVING BACKWARD'; _vemoji97 = '\U0001f534'
    elif _stalled_flags:
        _verdict97 = 'STALLED'; _vemoji97 = '\U0001f7e1'
    else:
        _verdict97 = 'MOVING FORWARD'; _vemoji97 = '\U0001f7e2'

    # ── Next actions ───────────────────────────────────────────────────────────
    _next97 = []
    if _tl_pct_24h >= 60.0 and _trades_24h >= 5:
        _next97.append('Patch: Reduce EXIT_TIME_LIMIT_MIN (TIME_LIMIT exits at ' + str(_tl_pct_24h) + '%)')
    if _avg_pnl_24h < -0.3 and _sl_count_24h > 3:
        _next97.append('Patch: Tighten stop-loss (' + str(_sl_count_24h) + ' SL exits, avg=' + ('%.3f' % _avg_pnl_24h) + '%)')
    if _trades_24h == 0 and _skips_24h > 50:
        _next97.append('Action: Lower ML_MIN_WIN_PROB (' + str(_skips_24h) + ' skips, 0 trades in 24h)')
    if _crit_count > 0:
        _next97.append('Action: Review CRITICAL alerts (' + str(_crit_count) + ' today)')
    if not _next97:
        if _avg_pnl_7d >= 0.0 and _trades_7d >= 10:
            _next97.append('Optimize: Expand symbol universe or refine entry selectivity')
        else:
            _next97.append('Monitor: Track next 24h cycle for trend direction')
    _next97 = _next97[:2]

    # Safe opts proposal
    _safe97 = []
    if _tl_pct_24h >= 60.0 and _trades_24h >= 5:
        _safe97.append('  - Reduce EXIT_TIME_LIMIT_MIN by 20%')
    if _trades_24h == 0 and _skips_24h > 50:
        _safe97.append('  - Lower ML_MIN_WIN_PROB by 0.03')
    _apply97 = ('Apply All Safe Optimizations: YES \u2014 proposed:\n' + '\n'.join(_safe97)
                if _safe97 else 'Apply All Safe Optimizations: NO \u2014 within parameters')

    # ── Build report_markdown ──────────────────────────────────────────────────
    _R97 = []
    _R97.append('# Abrons Daily Checklist \u2014 ' + _date_str)
    _R97.append('')
    _R97.append('## 1. System Health')
    _R97.append('Alive (' + str(len(_alive97)) + '): ' + (', '.join(_alive97) or 'none'))
    _R97.append('Slow  (' + str(len(_slow97))  + '): ' + (', '.join(_slow97)  or 'none'))
    _R97.append('Stalled (' + str(len(_stalled97)) + '): ' + (', '.join(_stalled97) or 'none'))
    _R97.append('Health watchdog: ' + ((_health97.get('status', 'init')) if _health97 else 'init'))
    _R97.append('Log errors (60m): ' + str(_exc_count) + ((' \u2014 ' + ' | '.join(_exc_samples[:2])) if _exc_samples else ''))
    _R97.append('')
    _R97.append('## 2. Data Feeds')
    if _feeds97:
        for _fn97, _fv97 in _feeds97.items():
            _fs97 = _fv97.get('status', '?')
            _fa97 = _fv97.get('age_s')
            _R97.append('  ' + _fn97 + ': ' + _fs97 + ((' (' + str(_fa97) + 's)') if _fa97 is not None else ''))
    else:
        _R97.append('  (data integrity agent initializing)')
    if _feed_alerts97:
        _R97.append('  Feed alerts: ' + ', '.join(_feed_alerts97))
    _R97.append('')
    _R97.append('## 3. Performance (24h)')
    _R97.append('Trades: ' + str(_trades_24h) + ' | WR: ' + str(_wr_24h) + '% | Avg PnL: ' + ('%.3f' % _avg_pnl_24h) + '%')
    _R97.append('TIME_LIMIT exits: ' + str(_tl_pct_24h) + '% | Stop-loss exits: ' + str(_sl_count_24h))
    _R97.append('Cumulative PnL: ' + ('%.2f' % _dd_24h) + '%')
    _R97.append('Skipped signals: ' + str(_skips_24h))
    _R97.append('')
    _R97.append('## 4. Performance (7d)')
    _R97.append('Trades: ' + str(_trades_7d) + ' | WR: ' + str(_wr_7d) + '% | Avg PnL: ' + ('%.3f' % _avg_pnl_7d) + '%')
    _R97.append('')
    _R97.append('## 5. Gate & Parameter Health')
    if _params97:
        for _pk97, _pv97 in _params97.items():
            _R97.append('  ' + _pk97 + '=' + _pv97)
    else:
        _R97.append('  (not found in .env \u2014 using runtime defaults)')
    _R97.append('')
    _R97.append('## 6. Alert Review')
    if _crit_msgs:
        for _cm97 in _crit_msgs:
            _R97.append('  \U0001f534 ' + _cm97)
    elif _health97 and _health97.get('alerts'):
        for _ha97 in (_health97.get('alerts') or [])[:5]:
            _R97.append('  \U0001f7e1 ' + _ha97)
    else:
        _R97.append('  No critical/warning alerts in 24h \u2713')
    _R97.append('')
    _R97.append('## 7. Data Integrity')
    if _feeds97:
        for _fn97, _fv97 in _feeds97.items():
            _R97.append('  ' + _fn97 + ': ' + _fv97.get('status', '?'))
    else:
        _R97.append('  (initializing\u2026)')
    _R97.append('')
    _R97.append('## 8. Verdict & Next Actions')
    _R97.append(_vemoji97 + ' **Verdict: ' + _verdict97 + '**')
    _R97.append('Reasoning:')
    _active97 = _backward_flags or _stalled_flags or _forward_flags
    _sym97 = '  \u26a0 ' if (_backward_flags or _stalled_flags) else '  \u2713 '
    for _af97 in _active97:
        _R97.append(_sym97 + _af97)
    _R97.append('')
    _R97.append('Next Actions:')
    for _ni97, _na97 in enumerate(_next97):
        _R97.append('  ' + str(_ni97 + 1) + '. ' + _na97)
    _R97.append('')
    _R97.append(_apply97)
    _R97.append('')
    _R97.append('---')
    _R97.append('Checklist complete. Ready for next actions. What would you like to focus on today?')
    _report97 = '\n'.join(_R97)

    return {
        'ts': _now.isoformat() + 'Z',
        'verdict': _verdict97,
        'report_markdown': _report97,
        'summary': {
            'trades_24h': _trades_24h,
            'win_rate_24h': _wr_24h,
            'avg_pnl_24h': _avg_pnl_24h,
            'time_limit_pct_24h': _tl_pct_24h,
            'dd_24h': _dd_24h,
            'skips_24h': _skips_24h,
            'critical_alerts_24h': _crit_count,
            'agents_stalled': _stalled97,
            'feed_ok': _feed_ok97,
        },
        'backward_flags': _backward_flags,
        'stalled_flags': _stalled_flags,
        'forward_flags': _forward_flags,
        'next_actions': _next97,
    }


'''

if '_run_checklist_sync' not in text:
    assert text.count(FUNC_ANCHOR) == 1, f'Step 1b FUNC_ANCHOR: {text.count(FUNC_ANCHOR)} matches'
    text = text.replace(FUNC_ANCHOR, NEW_FUNC + FUNC_ANCHOR, 1)
    print('Step 1b: _run_checklist_sync inserted ✓')
else:
    print('Step 1b: already present, skipping')

# ── Step 1c: Add 2 new endpoints ─────────────────────────────────────────────
print('Step 1c: adding checklist endpoints ...')

ENDPOINT_ANCHOR = '@app.get("/api/journal/learnings")'
NEW_ENDPOINTS = '''@app.get("/api/orchestrator/checklist-result")
async def orchestrator_checklist_result(_: str = Depends(get_current_user)):
    result = _orch_get_cl()
    if not result:
        return {"ts": None, "verdict": None, "report_markdown": None, "summary": {}}
    return result


@app.post("/api/orchestrator/run-daily-checklist")
async def orchestrator_run_checklist(_: str = Depends(get_current_user)):
    result = await asyncio.to_thread(_run_checklist_sync)
    _orch_set_cl(result)
    _orch_mem("checklist", (
        "DAILY_CHECKLIST_RUN: " + result["verdict"] +
        " | 24h trades=" + str(result["summary"].get("trades_24h", 0)) +
        " | WR=" + str(result["summary"].get("win_rate_24h", 0)) +
        "% | AvgPnL=" + str(result["summary"].get("avg_pnl_24h", 0)) + "%"
    ))
    return result


@app.get("/api/journal/learnings")'''

if 'run-daily-checklist' not in text:
    assert text.count(ENDPOINT_ANCHOR) == 1, f'Step 1c anchor: {text.count(ENDPOINT_ANCHOR)} matches'
    text = text.replace(ENDPOINT_ANCHOR, NEW_ENDPOINTS, 1)
    print('Step 1c: endpoints added ✓')
else:
    print('Step 1c: already present, skipping')

# ── Write + compile check ─────────────────────────────────────────────────────
MX.write_text(text)
try:
    ast.parse(text)
    print('Compile check: OK ✓')
except SyntaxError as e:
    print(f'COMPILE ERROR: {e}')
    raise

print('\nPatch 97 applied successfully.')
print('Next: scp + apply + systemctl restart + frontend build + rsync')
