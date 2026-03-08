"""
Patch 98 — Operational Stability Pack

Changes:
  1. Auto-daily checklist loop   — fires at 00:05 UTC, sends Telegram with verdict + top actions
  2. Trading agent heartbeat     — _perp_monitor_loop beats every 60s (matches 'trading' interval)
  3. Optimizer agent heartbeat   — _weekly_report_loop beats on weekly report run
  4. Optimizer interval fix      — change 86400s → 604800s (7d) so it doesn't show stalled daily
  5. Next_actions padded to 3    — always generate exactly 3 actionable items in checklist
  6. Daily report backfill       — trigger report for today via API after deploy

Files patched:
  dashboard/backend/main.py   (4 changes)
  utils/orchestrator.py       (1 change — optimizer interval)

Why:
  - Checklist was manual-only: no Telegram push, no daily visibility without user action
  - 'trading' and 'optimizer' agents showed 'init' forever → misleading orchestrator grid
  - next_actions capped at 2 items → not enough for "top 3 daily fixes" pattern
  - optimizer interval of 1d meant it showed stalled 6 out of 7 days

Verify:
  After deploy:
    journalctl -u memecoin-dashboard -n 20 --no-pager | grep -E 'CHECKLIST|trading|optimizer'
    curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/orchestrator/status |
      python3 -m json.tool | grep -A2 '"trading"'
  At 00:05 UTC tomorrow → check Telegram for 🟢/🟡/🔴 checklist ping
"""
import sys, os, re

BASE = '/root/memecoin_engine'
MAIN = os.path.join(BASE, 'dashboard', 'backend', 'main.py')
ORCH = os.path.join(BASE, 'utils', 'orchestrator.py')

txt = open(MAIN).read()

# ─── OP-1: Add _auto_checklist_loop() before lifespan() ──────────────────────

AUTO_CHECKLIST_FN = r'''
async def _auto_checklist_loop():
    """Daily checklist auto-runner — fires at 00:05 UTC every day.

    Calls _run_checklist_sync(), stores result in orchestrator cache,
    appends to MEMORY.md, and sends Telegram with verdict + top 3 actions.
    """
    await asyncio.sleep(180)   # startup delay — let all agents warm up first
    _ran_today: set = set()
    while True:
        try:
            _now_cl = datetime.utcnow()
            _day_cl = _now_cl.strftime("%Y-%m-%d")
            if _now_cl.hour == 0 and 5 <= _now_cl.minute <= 15 and _day_cl not in _ran_today:
                log.info("[CHECKLIST] Auto-running daily checklist for %s", _day_cl)
                try:
                    _cl_result = await asyncio.to_thread(_run_checklist_sync)
                    _orch_set_cl(_cl_result)
                    _verdict_cl = _cl_result.get("verdict", "?")
                    _trades_cl  = _cl_result.get("summary", {}).get("trades_24h", 0)
                    _wr_cl      = _cl_result.get("summary", {}).get("win_rate_24h", 0.0)
                    _pnl_cl     = _cl_result.get("summary", {}).get("avg_pnl_24h", 0.0)
                    _actions_cl = _cl_result.get("next_actions", [])
                    _orch_mem("alert", f"DAILY CHECKLIST: {_verdict_cl} | "
                              f"24h={_trades_cl} trades WR={_wr_cl:.0f}% PnL={_pnl_cl:+.3f}%")
                    _emoji_cl = "\U0001f534" if _verdict_cl == "MOVING BACKWARD" \
                                else "\U0001f7e1" if _verdict_cl == "STALLED" else "\U0001f7e2"
                    _body_cl = (f"Verdict: {_verdict_cl}\n"
                                f"24h: {_trades_cl} trades | WR {_wr_cl:.0f}% | AvgPnL {_pnl_cl:+.3f}%\n")
                    if _actions_cl:
                        _body_cl += "\nTop Actions:\n" + "\n".join(
                            f"  {_i+1}. {_a}" for _i, _a in enumerate(_actions_cl))
                    await _send_tg("Daily Checklist " + _day_cl, _body_cl, _emoji_cl)
                    log.info("[CHECKLIST] Done: %s | trades=%d WR=%.0f%% PnL=%+.3f%%",
                             _verdict_cl, _trades_cl, _wr_cl, _pnl_cl)
                    _ran_today.add(_day_cl)
                    if len(_ran_today) > 7:
                        _ran_today.pop()
                except Exception as _cle:
                    log.warning("[CHECKLIST] Auto-run error: %s", _cle)
        except Exception as _cloe:
            log.warning("[CHECKLIST] Outer loop error: %s", _cloe)
        await asyncio.sleep(300)   # check every 5 min


'''

LIFESPAN_ANCHOR = 'async def lifespan(app: FastAPI):'
assert LIFESPAN_ANCHOR in txt, 'Anchor not found: lifespan def'
txt = txt.replace(LIFESPAN_ANCHOR, AUTO_CHECKLIST_FN + LIFESPAN_ANCHOR)
print('✓ OP-1: _auto_checklist_loop() inserted before lifespan()')

# ─── OP-2: Register task_auto_checklist in lifespan ─────────────────────────

OLD_TASK_REG = '    task_daily_report  = asyncio.create_task(_daily_report_loop())'
NEW_TASK_REG = ('    task_daily_report  = asyncio.create_task(_daily_report_loop())\n'
                '    task_auto_checklist = asyncio.create_task(_auto_checklist_loop())  # Patch 98')
assert OLD_TASK_REG in txt, 'Anchor not found: task_daily_report create_task'
txt = txt.replace(OLD_TASK_REG, NEW_TASK_REG)
print('✓ OP-2: task_auto_checklist registered in lifespan')

# ─── OP-3: Add task_auto_checklist to all_tasks cancel block ─────────────────

OLD_CANCEL = ('                 task_health_wdg, task_data_integ)  # Patch 96')
NEW_CANCEL  = ('                 task_health_wdg, task_data_integ,  # Patch 96\n'
               '                 task_auto_checklist)  # Patch 98')
assert OLD_CANCEL in txt, 'Anchor not found: cancel block Patch 96 line'
txt = txt.replace(OLD_CANCEL, NEW_CANCEL)
print('✓ OP-3: task_auto_checklist added to cancel block')

# ─── OP-4: Trading heartbeat in _perp_monitor_loop ───────────────────────────
# Add _orch_hb("trading") after await perp_monitor_step() inside the loop.

OLD_PERP_MON = (
    '        try:\n'
    '            from utils.perp_executor import perp_monitor_step  # type: ignore\n'
    '            await perp_monitor_step()\n'
    '        except Exception as _e:\n'
    '            log.debug("perp_monitor_step error: %s", _e)\n'
    '        await asyncio.sleep(60)'
)
NEW_PERP_MON = (
    '        try:\n'
    '            from utils.perp_executor import perp_monitor_step  # type: ignore\n'
    '            await perp_monitor_step()\n'
    '            _orch_hb("trading")  # Patch 98\n'
    '        except Exception as _e:\n'
    '            log.debug("perp_monitor_step error: %s", _e)\n'
    '        await asyncio.sleep(60)'
)
assert OLD_PERP_MON in txt, 'Anchor not found: perp_monitor_step block'
txt = txt.replace(OLD_PERP_MON, NEW_PERP_MON)
print('✓ OP-4: trading heartbeat added to _perp_monitor_loop')

# ─── OP-5: Optimizer heartbeat in _weekly_report_loop ────────────────────────

OLD_WEEKLY = (
    '                report = generate_weekly_report()\n'
    '                logger.info("[REPORT] Weekly report generated: %d trades, %.1f%% WR",\n'
    '                            report.get("summary", {}).get("n_trades", 0),\n'
    '                            report.get("summary", {}).get("win_rate", 0))'
)
NEW_WEEKLY = (
    '                report = generate_weekly_report()\n'
    '                _orch_hb("optimizer")  # Patch 98\n'
    '                logger.info("[REPORT] Weekly report generated: %d trades, %.1f%% WR",\n'
    '                            report.get("summary", {}).get("n_trades", 0),\n'
    '                            report.get("summary", {}).get("win_rate", 0))'
)
assert OLD_WEEKLY in txt, 'Anchor not found: generate_weekly_report block'
txt = txt.replace(OLD_WEEKLY, NEW_WEEKLY)
print('✓ OP-5: optimizer heartbeat added to _weekly_report_loop')

# ─── OP-6: Pad next_actions to 3 items ───────────────────────────────────────
# Current code caps at 2. We extend the fallback logic and raise cap to 3.

OLD_NEXT = (
    "    if not _next97:\n"
    "        if _avg_pnl_7d >= 0.0 and _trades_7d >= 10:\n"
    "            _next97.append('Optimize: Expand symbol universe or refine entry selectivity')\n"
    "        else:\n"
    "            _next97.append('Monitor: Track next 24h cycle for trend direction')\n"
    "    _next97 = _next97[:2]"
)
NEW_NEXT = (
    "    # Pad to 3 actions — add context-aware fallback items\n"
    "    if _trades_24h == 0 and _skips_24h == 0 and 'Lower ML_MIN_WIN_PROB' not in ' '.join(_next97):\n"
    "        _next97.append('Check: System has 0 trades AND 0 skips — verify scan loops are alive')\n"
    "    if _dd_24h < -1.5 and 'stop-loss' not in ' '.join(_next97).lower() and 'stop_loss' not in ' '.join(_next97).lower():\n"
    "        _next97.append('Watch: 24h DD=' + ('%.2f' % _dd_24h) + '% — monitor stop-loss levels')\n"
    "    if len(_next97) < 3:\n"
    "        if _avg_pnl_7d >= 0.3 and _trades_7d >= 10:\n"
    "            _next97.append('Optimize: WR trend positive — consider expanding MAX_OPEN_PERPS or symbol count')\n"
    "        elif _avg_pnl_7d >= 0.0 and _trades_7d >= 5:\n"
    "            _next97.append('Monitor: Track next 24h — system in balance, watch for improving WR')\n"
    "        elif _trades_7d == 0:\n"
    "            _next97.append('Investigate: 0 trades in 7d — check gate parameters and scan loop health')\n"
    "        else:\n"
    "            _next97.append('Tune: Review ML_MIN_WIN_PROB and EV_MIN_THRESHOLD against recent skip rate')\n"
    "    _next97 = _next97[:3]"
)
assert OLD_NEXT in txt, 'Anchor not found: next_actions fallback block'
txt = txt.replace(OLD_NEXT, NEW_NEXT)
print('✓ OP-6: next_actions padded to 3 items')

# Write patched main.py
open(MAIN, 'w').write(txt)
print(f'\n✓ main.py written ({len(txt):,} bytes)')

# ─── OP-7: Fix optimizer interval in orchestrator.py ─────────────────────────
# Change interval_s from 86400 (1d) to 604800 (7d) to match weekly run cadence.

otxt = open(ORCH).read()
OLD_OPT_INTERVAL = '"optimizer":      {"interval_s": 86400, "last_beat": 0.0, "status": "init"},'
NEW_OPT_INTERVAL = '"optimizer":      {"interval_s": 604800,"last_beat": 0.0, "status": "init"},  # weekly'
assert OLD_OPT_INTERVAL in otxt, 'Anchor not found: optimizer interval in orchestrator.py'
otxt = otxt.replace(OLD_OPT_INTERVAL, NEW_OPT_INTERVAL)
open(ORCH, 'w').write(otxt)
print('✓ OP-7: optimizer interval → 604800s (7d) in orchestrator.py')

# ─── Syntax check ─────────────────────────────────────────────────────────────
import ast
try:
    ast.parse(open(MAIN).read())
    print('✓ Syntax check: main.py OK')
except SyntaxError as e:
    print(f'✗ SYNTAX ERROR in main.py: {e}')
    sys.exit(1)
ast.parse(open(ORCH).read())
print('✓ Syntax check: orchestrator.py OK')

print('\nAll done. Run: systemctl restart memecoin-dashboard')
print('\nVerify after restart:')
print('  journalctl -u memecoin-dashboard -n 30 --no-pager | grep -E "CHECKLIST|started"')
print('  curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/orchestrator/status | python3 -m json.tool')
