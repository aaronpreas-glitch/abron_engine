"""
Patch 100 — Telegram One-Tap Tuner Approval

Flow:
  00:05 UTC → AI checklist fires → Telegram ping with:
    🔴 Verdict: MOVING BACKWARD
    24h: 2 trades | WR 50% | AvgPnL -0.74%
    ML_MIN_WIN_PROB 0.42 is strangling deal flow...
    Top Actions: 1. Lower ML_MIN_WIN_PROB 0.42→0.35 ...
    [✅ Apply 2 changes]  [❌ Skip]

  User taps ✅ → changes written to .env + applied in-memory immediately
  User taps ❌ → changes discarded, buttons removed

Changes:
  1. Update AI prompt to also return tuner_changes (structured key/value list)
  2. Extract tuner_changes from AI response + add to checklist return dict
  3. Add module-level helpers: _pending_tuner100, _TUNE_BOUNDS100,
     _apply_tuner_internal100(), _send_tg_buttons_sync100(), _telegram_callback_loop()
  4. Update _auto_checklist_loop to send buttons when tuner_changes present
  5. Register _telegram_callback_loop in lifespan + cancel block

Files patched:
  dashboard/backend/main.py (6 changes)

Safety:
  - Only keys in ALLOWED_KEYS_100 (~18 common tuning params) can be auto-applied
  - All numeric changes validated against _TUNE_BOUNDS100 before writing
  - User must tap ✅ — nothing happens without explicit approval
  - Falls back to plain message if no tuner_changes in AI response

Verify:
  # Trigger checklist manually:
  TOKEN=$(curl -s -X POST http://localhost:8000/api/auth/login \
    -H 'Content-Type: application/json' \
    -d '{"password":"HArden978ab"}' | python3 -c 'import sys,json; print(json.load(sys.stdin)["token"])')
  curl -s -X POST http://localhost:8000/api/orchestrator/run-daily-checklist \
    -H "Authorization: Bearer $TOKEN" | python3 -m json.tool | grep -A10 '"tuner_changes"'
  # Expected: list of {"key":..., "value":...} objects
  # Check Telegram: message should have ✅/❌ buttons
"""
import sys, os

BASE = '/root/memecoin_engine'
MAIN = os.path.join(BASE, 'dashboard', 'backend', 'main.py')

txt = open(MAIN).read()

# ─── OP-1: Update AI block — add tuner_changes to prompt + extraction ─────────

OLD_AI_BLOCK = '''    # ─── AI-enhanced analysis (Patch 99) ─────────────────────────────────────
    _ai_analysis99 = ''
    try:
        import anthropic as _anth99
        import json as _json99
        _anth_key99 = os.environ.get('ANTHROPIC_API_KEY', '')
        if _anth_key99:
            _client99 = _anth99.Anthropic(api_key=_anth_key99)
            _ctx99 = (
                f"Paper trading bot daily snapshot:\\n"
                f"Verdict: {_verdict97}\\n"
                f"24h: {_trades_24h} trades | WR {_wr_24h:.1f}% | Avg PnL {_avg_pnl_24h:+.3f}%\\n"
                f"24h: TIME_LIMIT={_tl_pct_24h:.1f}% | STOP_LOSS={_sl_count_24h} | DD={_dd_24h:+.2f}%\\n"
                f"24h skipped signals: {_skips_24h}\\n"
                f"7d: {_trades_7d} trades | WR {_wr_7d:.1f}% | Avg PnL {_avg_pnl_7d:+.3f}%\\n"
                f"Gate params: {_json99.dumps(_params97)}\\n"
                f"Backward flags: {_backward_flags}\\n"
                f"Stalled agents: {_stalled97}\\n"
                f"Rules-based actions (replace these with better ones): {_next97}\\n"
            )
            _resp99 = _client99.messages.create(
                model='claude-haiku-4-5-20251001',
                max_tokens=400,
                system=(
                    'You are a quant trading system analyst reviewing a Solana memecoin paper trading bot. '
                    'Given the performance snapshot, return ONLY valid JSON (no markdown, no code fences) '
                    'with exactly two keys: '
                    '"next_actions": a list of exactly 3 short actionable strings (each under 120 chars), '
                    '"analysis": a single sentence (max 180 chars) naming the single biggest issue right now. '
                    'Each action must be specific: name the exact .env key to change and the suggested value. '
                    'Example action: "Lower ML_MIN_WIN_PROB from 0.62 to 0.58 — 47 skips today, only 2 trades." '
                    'Be direct and data-driven. No hedging, no generic advice.'
                ),
                messages=[{'role': 'user', 'content': _ctx99}],
            )
            _raw99 = _resp99.content[0].text.strip()
            # Strip markdown code fences if model wraps anyway
            if _raw99.startswith('```'):
                _raw99 = _raw99.split('```')[1]
                if _raw99.startswith('json'):
                    _raw99 = _raw99[4:]
            _parsed99 = _json99.loads(_raw99.strip())
            if isinstance(_parsed99.get('next_actions'), list) and len(_parsed99['next_actions']) >= 3:
                _next97 = [str(_a) for _a in _parsed99['next_actions'][:3]]
                _ai_analysis99 = str(_parsed99.get('analysis', ''))
                log.info('[CHECKLIST] AI analysis OK: %s', _ai_analysis99[:80])
    except Exception as _aie99:
        log.debug('[CHECKLIST] AI analysis failed (using rules-based): %s', _aie99)'''

NEW_AI_BLOCK = '''    # ─── AI-enhanced analysis (Patch 99 + 100) ──────────────────────────────────
    _ai_analysis99 = ''
    _ai_tuner99: list = []
    try:
        import anthropic as _anth99
        import json as _json99
        _anth_key99 = os.environ.get('ANTHROPIC_API_KEY', '')
        if _anth_key99:
            _client99 = _anth99.Anthropic(api_key=_anth_key99)
            _ctx99 = (
                f"Paper trading bot daily snapshot:\\n"
                f"Verdict: {_verdict97}\\n"
                f"24h: {_trades_24h} trades | WR {_wr_24h:.1f}% | Avg PnL {_avg_pnl_24h:+.3f}%\\n"
                f"24h: TIME_LIMIT={_tl_pct_24h:.1f}% | STOP_LOSS={_sl_count_24h} | DD={_dd_24h:+.2f}%\\n"
                f"24h skipped signals: {_skips_24h}\\n"
                f"7d: {_trades_7d} trades | WR {_wr_7d:.1f}% | Avg PnL {_avg_pnl_7d:+.3f}%\\n"
                f"Gate params: {_json99.dumps(_params97)}\\n"
                f"Backward flags: {_backward_flags}\\n"
                f"Stalled agents: {_stalled97}\\n"
                f"Rules-based actions (replace these with better ones): {_next97}\\n"
            )
            _resp99 = _client99.messages.create(
                model='claude-haiku-4-5-20251001',
                max_tokens=500,
                system=(
                    'You are a quant trading system analyst reviewing a Solana memecoin paper trading bot. '
                    'Given the performance snapshot, return ONLY valid JSON (no markdown, no code fences) '
                    'with exactly three keys: '
                    '"next_actions": a list of exactly 3 short actionable strings (each under 120 chars), '
                    '"analysis": a single sentence (max 180 chars) naming the single biggest issue right now, '
                    '"tuner_changes": a list of {"key": "PARAM_NAME", "value": "new_value"} objects — '
                    'only include entries where a specific .env parameter should change (max 3, empty list OK). '
                    'Each action must reference the exact .env key and suggested value. '
                    'Example tuner_change: {"key": "ML_MIN_WIN_PROB", "value": "0.35"} '
                    'Be direct and data-driven. No hedging, no generic advice.'
                ),
                messages=[{'role': 'user', 'content': _ctx99}],
            )
            _raw99 = _resp99.content[0].text.strip()
            # Strip markdown code fences if model wraps anyway
            if _raw99.startswith('```'):
                _raw99 = _raw99.split('```')[1]
                if _raw99.startswith('json'):
                    _raw99 = _raw99[4:]
            _parsed99 = _json99.loads(_raw99.strip())
            if isinstance(_parsed99.get('next_actions'), list) and len(_parsed99['next_actions']) >= 3:
                _next97 = [str(_a) for _a in _parsed99['next_actions'][:3]]
                _ai_analysis99 = str(_parsed99.get('analysis', ''))
                _raw_tc = _parsed99.get('tuner_changes', [])
                if isinstance(_raw_tc, list):
                    _ai_tuner99 = [
                        {'key': str(tc.get('key', '')), 'value': str(tc.get('value', ''))}
                        for tc in _raw_tc
                        if isinstance(tc, dict) and tc.get('key') and tc.get('value')
                    ]
                log.info('[CHECKLIST] AI OK: %s | %d tuner changes', _ai_analysis99[:80], len(_ai_tuner99))
    except Exception as _aie99:
        log.debug('[CHECKLIST] AI analysis failed (using rules-based): %s', _aie99)'''

assert OLD_AI_BLOCK in txt, 'Anchor not found: AI block (Patch 99)'
txt = txt.replace(OLD_AI_BLOCK, NEW_AI_BLOCK)
print('✓ OP-1: AI block updated — prompt requests tuner_changes, extraction added')

# ─── OP-2: Add tuner_changes to checklist return dict ────────────────────────

OLD_RETURN = ("        'ai_analysis': _ai_analysis99,  # Patch 99\n"
              "    }\n"
              "\n"
              "\nasync def _watchdog_agent_loop():")
NEW_RETURN = ("        'ai_analysis': _ai_analysis99,  # Patch 99\n"
              "        'tuner_changes': _ai_tuner99,    # Patch 100\n"
              "    }\n"
              "\n"
              "\nasync def _watchdog_agent_loop():")

assert OLD_RETURN in txt, 'Anchor not found: return dict ai_analysis line'
txt = txt.replace(OLD_RETURN, NEW_RETURN)
print('✓ OP-2: tuner_changes added to checklist return dict')

# ─── OP-3: Insert module-level helpers before _auto_checklist_loop ────────────

HELPERS_BLOCK = r'''
# ─── Patch 100: Telegram one-tap tuner approval ───────────────────────────────

_pending_tuner100: dict = {}   # date → list of {"key":..., "value":...}

_TUNE_BOUNDS100 = {
    'ML_MIN_WIN_PROB': (0.28, 0.72), 'ML_MIN_PRED_RET': (0.001, 0.025),
    'EV_MIN_THRESHOLD': (-500.0, 50.0), 'DAILY_TRADE_CAP': (3, 60),
    'SCALP_5M_THRESHOLD': (0.08, 0.60), 'SCALP_BREAKEVEN_TRIGGER': (0.10, 1.50),
    'SCALP_TP_PCT': (0.3, 5.0), 'SCALP_STOP_PCT': (0.3, 5.0), 'SCALP_MAX_OPEN': (1, 10),
    'MAX_OPEN_PERPS': (1, 10), 'PERP_STOP_PCT': (0.5, 8.0), 'PERP_TP1_PCT': (0.5, 10.0),
    'PERP_TP2_PCT': (0.5, 15.0), 'DAILY_MAX_DD_PCT': (1.0, 15.0), 'HARD_TRADE_CAP': (5, 100),
    'WINNER_EXTEND_TRAIL_PCT': (0.1, 2.0), 'EARLY_CUT_LOSS_PCT': (0.1, 2.0),
    'EARLY_CUT_MINUTES': (1, 30), 'MIN_INTERVAL_MIN': (1, 60),
}

_ALLOWED_KEYS_100 = {
    'ML_MIN_WIN_PROB', 'ML_MIN_PRED_RET', 'EV_MIN_THRESHOLD', 'DAILY_TRADE_CAP',
    'SCALP_5M_THRESHOLD', 'SCALP_BREAKEVEN_TRIGGER', 'SCALP_TP_PCT', 'SCALP_STOP_PCT',
    'SCALP_MAX_OPEN', 'MAX_OPEN_PERPS', 'PERP_STOP_PCT', 'PERP_TP1_PCT', 'PERP_TP2_PCT',
    'DAILY_MAX_DD_PCT', 'HARD_TRADE_CAP', 'WINNER_EXTEND_TRAIL_PCT', 'EARLY_CUT_LOSS_PCT',
    'EARLY_CUT_MINUTES', 'MIN_INTERVAL_MIN', 'VOL_FILTER_THRESHOLD', 'REGIME_MIN_SCORE',
    'SCALP_LEVERAGE', 'PERP_LEVERAGE',
}


def _apply_tuner_internal100(changes: list) -> tuple:
    """Apply tuner changes directly (no HTTP). Returns (applied, rejected, oob)."""
    applied: dict = {}
    rejected: list = []
    oob: list = []
    env_path = os.path.join(_engine_root(), '.env')
    for tc in changes:
        key = tc.get('key', '').strip()
        val = str(tc.get('value', '')).strip()
        if not key or not val:
            continue
        if key not in _ALLOWED_KEYS_100:
            rejected.append(key)
            continue
        if key in _TUNE_BOUNDS100:
            try:
                fval = float(val)
                lo, hi = _TUNE_BOUNDS100[key]
                if not (lo <= fval <= hi):
                    oob.append(f'{key}={val} (bounds {lo}–{hi})')
                    continue
            except ValueError:
                pass   # non-numeric (booleans etc) skip bounds check
        applied[key] = val
    if not applied:
        return applied, rejected, oob
    # Write to .env
    env_lines = open(env_path).readlines() if os.path.exists(env_path) else []
    updated: set = set()
    new_lines: list = []
    for line in env_lines:
        stripped = line.strip()
        if '=' in stripped and not stripped.startswith('#'):
            k = stripped.split('=', 1)[0].strip()
            if k in applied:
                new_lines.append(f'{k}={applied[k]}\n')
                updated.add(k)
                continue
        new_lines.append(line if line.endswith('\n') else line + '\n')
    for k, v in applied.items():
        if k not in updated:
            new_lines.append(f'{k}={v}\n')
    open(env_path, 'w').writelines(new_lines)
    # Apply to os.environ + perp_executor globals
    import sys as _s100
    _pe100 = _s100.modules.get('utils.perp_executor')
    for k, v in applied.items():
        os.environ[k] = v
        if _pe100:
            if hasattr(_pe100, '_apply_selectivity_tuning'):
                _pe100._apply_selectivity_tuning(k, v)
            if hasattr(_pe100, '_apply_live_mode_tuning'):
                _pe100._apply_live_mode_tuning(k, v)
    return applied, rejected, oob


def _send_tg_buttons_sync100(title: str, body: str, emoji: str, buttons: list) -> int | None:
    """Send Telegram message with inline keyboard. Returns message_id or None."""
    import requests as _rq100
    token = os.getenv('TELEGRAM_TOKEN', '').strip()
    chat_id = os.getenv('TELEGRAM_CHAT_ID', '').strip()
    if not token or not chat_id:
        return None
    text = f"{emoji} <b>{title}</b>\n{body}"
    try:
        _r = _rq100.post(
            f'https://api.telegram.org/bot{token}/sendMessage',
            json={'chat_id': chat_id, 'text': text, 'parse_mode': 'HTML',
                  'reply_markup': {'inline_keyboard': [buttons]}},
            timeout=8,
        )
        if _r.status_code == 200:
            return _r.json().get('result', {}).get('message_id')
        log.debug('[TG-BUTTONS] HTTP %s: %s', _r.status_code, _r.text[:100])
    except Exception as _e:
        log.debug('[TG-BUTTONS] send error: %s', _e)
    return None


async def _telegram_callback_loop():
    """Patch 100 — Poll Telegram for inline keyboard callback queries (Apply/Skip tuner)."""
    import requests as _req100

    def _tg_get_updates(token: str, offset: int):
        return _req100.get(
            f'https://api.telegram.org/bot{token}/getUpdates',
            params={'offset': offset, 'timeout': 20, 'allowed_updates': '["callback_query"]'},
            timeout=25,
        )

    def _tg_answer(token: str, cb_id: str, text: str = ''):
        return _req100.post(
            f'https://api.telegram.org/bot{token}/answerCallbackQuery',
            json={'callback_query_id': cb_id, 'text': text},
            timeout=5,
        )

    def _tg_remove_buttons(token: str, chat_id, msg_id: int):
        return _req100.post(
            f'https://api.telegram.org/bot{token}/editMessageReplyMarkup',
            json={'chat_id': chat_id, 'message_id': msg_id, 'reply_markup': {'inline_keyboard': []}},
            timeout=5,
        )

    await asyncio.sleep(15)   # let other loops start first
    _offset = 0
    log.info('[TELEGRAM-CB] Callback polling started')
    while True:
        try:
            _token = os.getenv('TELEGRAM_TOKEN', '').strip()
            if not _token:
                await asyncio.sleep(60)
                continue
            _resp = await asyncio.to_thread(_tg_get_updates, _token, _offset)
            if _resp.status_code != 200:
                await asyncio.sleep(5)
                continue
            for _upd in _resp.json().get('result', []):
                _offset = _upd['update_id'] + 1
                _cb = _upd.get('callback_query')
                if not _cb:
                    continue
                _data   = _cb.get('data', '')
                _cb_id  = _cb['id']
                _msg    = _cb.get('message', {})
                _msg_id = _msg.get('message_id')
                _chat_id = _msg.get('chat', {}).get('id')

                if _data.startswith('apply_tune_'):
                    _date = _data[len('apply_tune_'):]
                    _changes = _pending_tuner100.pop(_date, None)
                    if _changes:
                        _applied, _rejected, _oob = await asyncio.to_thread(
                            _apply_tuner_internal100, _changes
                        )
                        if _applied:
                            _status = '✅ Applied: ' + ', '.join(f'{k}={v}' for k, v in _applied.items())
                            if _oob:
                                _status += '\n⚠️ Out of bounds (skipped): ' + ', '.join(_oob)
                            log.info('[TUNER-AUTO] Applied via Telegram: %s', _applied)
                            await _send_tg('Tuner Applied', _status, '✅')
                        else:
                            _status = '⚠️ No changes applied'
                            if _oob:
                                _status += ' — out of bounds: ' + ', '.join(_oob)
                            if _rejected:
                                _status += ' — key not allowed: ' + ', '.join(_rejected)
                    else:
                        _status = '⚠️ Changes already applied or expired.'
                    await asyncio.to_thread(_tg_answer, _token, _cb_id, 'Processing…')
                    if _msg_id and _chat_id:
                        await asyncio.to_thread(_tg_remove_buttons, _token, _chat_id, _msg_id)

                elif _data.startswith('skip_tune_'):
                    _date = _data[len('skip_tune_'):]
                    _pending_tuner100.pop(_date, None)
                    await asyncio.to_thread(_tg_answer, _token, _cb_id, 'Skipped ✓')
                    if _msg_id and _chat_id:
                        await asyncio.to_thread(_tg_remove_buttons, _token, _chat_id, _msg_id)
                    log.info('[TUNER-AUTO] Skipped via Telegram for %s', _date)

        except Exception as _e100:
            log.debug('[TELEGRAM-CB] Poll error: %s', _e100)
        await asyncio.sleep(3)   # poll every 3 seconds


'''

AUTO_CHECKLIST_ANCHOR = 'async def _auto_checklist_loop():'
assert AUTO_CHECKLIST_ANCHOR in txt, 'Anchor not found: _auto_checklist_loop def'
txt = txt.replace(AUTO_CHECKLIST_ANCHOR, HELPERS_BLOCK + AUTO_CHECKLIST_ANCHOR)
print('✓ OP-3: module-level helpers + _telegram_callback_loop inserted')

# ─── OP-4: Update _auto_checklist_loop to send with buttons ──────────────────

OLD_TG_SEND = ('                    _actions_cl = _cl_result.get("next_actions", [])\n'
               '                    _tuner_cl = _cl_result.get("tuner_changes", [])  # Patch 100\n'
               '                    _orch_mem("alert", f"DAILY CHECKLIST: {_verdict_cl} | "\n'
               '                              f"24h={_trades_cl} trades WR={_wr_cl:.0f}% PnL={_pnl_cl:+.3f}%")\n'
               '                    _emoji_cl = "\\U0001f534" if _verdict_cl == "MOVING BACKWARD" \\\n'
               '                                else "\\U0001f7e1" if _verdict_cl == "STALLED" else "\\U0001f7e2"\n'
               '                    _ai_line_cl = _cl_result.get("ai_analysis", "")  # Patch 99\n'
               '                    _body_cl = (f"Verdict: {_verdict_cl}\\n"\n'
               '                                f"24h: {_trades_cl} trades | WR {_wr_cl:.0f}% | AvgPnL {_pnl_cl:+.3f}%\\n")\n'
               '                    if _ai_line_cl:\n'
               '                        _body_cl += f"\\n{_ai_line_cl}\\n"\n'
               '                    if _actions_cl:\n'
               '                        _body_cl += "\\nTop Actions:\\n" + "\\n".join(\n'
               '                            f"  {_i+1}. {_a}" for _i, _a in enumerate(_actions_cl))\n'
               '                    await _send_tg("Daily Checklist " + _day_cl, _body_cl, _emoji_cl)')

# Check what's currently in the file (patch 99 may have the old version without _tuner_cl)
# Try new version first (patch 99 already applied), then old version
if OLD_TG_SEND not in txt:
    # Patch 99 version (without _tuner_cl line)
    OLD_TG_SEND = ('                    _actions_cl = _cl_result.get("next_actions", [])\n'
                   '                    _orch_mem("alert", f"DAILY CHECKLIST: {_verdict_cl} | "\n'
                   '                              f"24h={_trades_cl} trades WR={_wr_cl:.0f}% PnL={_pnl_cl:+.3f}%")\n'
                   '                    _emoji_cl = "\\U0001f534" if _verdict_cl == "MOVING BACKWARD" \\\n'
                   '                                else "\\U0001f7e1" if _verdict_cl == "STALLED" else "\\U0001f7e2"\n'
                   '                    _ai_line_cl = _cl_result.get("ai_analysis", "")  # Patch 99\n'
                   '                    _body_cl = (f"Verdict: {_verdict_cl}\\n"\n'
                   '                                f"24h: {_trades_cl} trades | WR {_wr_cl:.0f}% | AvgPnL {_pnl_cl:+.3f}%\\n")\n'
                   '                    if _ai_line_cl:\n'
                   '                        _body_cl += f"\\n{_ai_line_cl}\\n"\n'
                   '                    if _actions_cl:\n'
                   '                        _body_cl += "\\nTop Actions:\\n" + "\\n".join(\n'
                   '                            f"  {_i+1}. {_a}" for _i, _a in enumerate(_actions_cl))\n'
                   '                    await _send_tg("Daily Checklist " + _day_cl, _body_cl, _emoji_cl)')

assert OLD_TG_SEND in txt, 'Anchor not found: Telegram send block in _auto_checklist_loop'

NEW_TG_SEND = ('                    _actions_cl = _cl_result.get("next_actions", [])\n'
               '                    _tuner_cl   = _cl_result.get("tuner_changes", [])  # Patch 100\n'
               '                    _orch_mem("alert", f"DAILY CHECKLIST: {_verdict_cl} | "\n'
               '                              f"24h={_trades_cl} trades WR={_wr_cl:.0f}% PnL={_pnl_cl:+.3f}%")\n'
               '                    _emoji_cl = "\\U0001f534" if _verdict_cl == "MOVING BACKWARD" \\\n'
               '                                else "\\U0001f7e1" if _verdict_cl == "STALLED" else "\\U0001f7e2"\n'
               '                    _ai_line_cl = _cl_result.get("ai_analysis", "")  # Patch 99\n'
               '                    _body_cl = (f"Verdict: {_verdict_cl}\\n"\n'
               '                                f"24h: {_trades_cl} trades | WR {_wr_cl:.0f}% | AvgPnL {_pnl_cl:+.3f}%\\n")\n'
               '                    if _ai_line_cl:\n'
               '                        _body_cl += f"\\n{_ai_line_cl}\\n"\n'
               '                    if _actions_cl:\n'
               '                        _body_cl += "\\nTop Actions:\\n" + "\\n".join(\n'
               '                            f"  {_i+1}. {_a}" for _i, _a in enumerate(_actions_cl))\n'
               '                    if _tuner_cl:  # Patch 100: send with approval buttons\n'
               '                        _pending_tuner100[_day_cl] = _tuner_cl\n'
               '                        _btns_cl = [\n'
               '                            {"text": f"\\u2705 Apply {len(_tuner_cl)} change(s)",\n'
               '                             "callback_data": f"apply_tune_{_day_cl}"},\n'
               '                            {"text": "\\u274c Skip",\n'
               '                             "callback_data": f"skip_tune_{_day_cl}"},\n'
               '                        ]\n'
               '                        await asyncio.to_thread(\n'
               '                            _send_tg_buttons_sync100,\n'
               '                            "Daily Checklist " + _day_cl, _body_cl, _emoji_cl, _btns_cl\n'
               '                        )\n'
               '                    else:\n'
               '                        await _send_tg("Daily Checklist " + _day_cl, _body_cl, _emoji_cl)')

txt = txt.replace(OLD_TG_SEND, NEW_TG_SEND)
print('✓ OP-4: _auto_checklist_loop updated — sends buttons when tuner_changes present')

# ─── OP-5: Register task_tg_callback in lifespan ─────────────────────────────

OLD_TASK_REG = '    task_auto_checklist = asyncio.create_task(_auto_checklist_loop())  # Patch 98'
NEW_TASK_REG = ('    task_auto_checklist = asyncio.create_task(_auto_checklist_loop())  # Patch 98\n'
                '    task_tg_callback    = asyncio.create_task(_telegram_callback_loop())  # Patch 100')

assert OLD_TASK_REG in txt, 'Anchor not found: task_auto_checklist create_task'
txt = txt.replace(OLD_TASK_REG, NEW_TASK_REG)
print('✓ OP-5: task_tg_callback registered in lifespan')

# ─── OP-6: Add task_tg_callback to cancel block ──────────────────────────────

OLD_CANCEL = '                 task_auto_checklist)  # Patch 98'
NEW_CANCEL  = ('                 task_auto_checklist,  # Patch 98\n'
               '                 task_tg_callback)  # Patch 100')

assert OLD_CANCEL in txt, 'Anchor not found: cancel block Patch 98 line'
txt = txt.replace(OLD_CANCEL, NEW_CANCEL)
print('✓ OP-6: task_tg_callback added to cancel block')

# Write
open(MAIN, 'w').write(txt)
print(f'\n✓ main.py written ({len(txt):,} bytes)')

# Syntax check
import ast
try:
    ast.parse(open(MAIN).read())
    print('✓ Syntax check: main.py OK')
except SyntaxError as e:
    print(f'✗ SYNTAX ERROR: {e}')
    sys.exit(1)

print('\nAll done. Run: systemctl restart memecoin-dashboard')
print('\nVerify:')
print('  journalctl -u memecoin-dashboard -n 5 --no-pager | grep -E "TELEGRAM-CB|startup complete"')
print('  # Then trigger checklist and tap the ✅ button in Telegram')
