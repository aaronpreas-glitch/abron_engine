"""
Patch 99 — AI-Enhanced Daily Checklist

Changes:
  1. After rules-based next_actions are built in _run_checklist_sync(),
     call Claude Haiku to replace them with data-driven, specific actions
     (exact .env keys + suggested values). Falls back to rules-based on error.
  2. Add 'ai_analysis' field to checklist return dict (1-sentence summary).
  3. Prepend AI analysis sentence to Telegram body in _auto_checklist_loop.

Files patched:
  dashboard/backend/main.py (3 changes)

Why:
  - Rules-based next_actions are generic templates ("Monitor: Track next 24h...")
  - Claude Haiku reads actual metrics and returns specific tunable actions
  - Cost: ~$0.0002/day (single Haiku call, ~300 tokens in/out)
  - Latency: ~1-2s — acceptable for a daily background loop

Verify:
  # Trigger checklist manually and check AI actions:
  TOKEN=$(curl -s -X POST http://localhost:8000/api/auth/login \
    -H 'Content-Type: application/json' \
    -d '{"password":"HArden978ab"}' | python3 -c 'import sys,json; print(json.load(sys.stdin)["token"])')
  curl -s -X POST http://localhost:8000/api/orchestrator/run-daily-checklist \
    -H "Authorization: Bearer $TOKEN" | python3 -m json.tool | grep -A6 '"next_actions"'
  # Expected: 3 specific actions referencing real .env keys + values
  # Also check: "ai_analysis" field is populated
"""
import sys, os

BASE = '/root/memecoin_engine'
MAIN = os.path.join(BASE, 'dashboard', 'backend', 'main.py')

txt = open(MAIN).read()

# ─── OP-1: AI call block after _next97 = _next97[:3] ─────────────────────────

OLD_NEXT_CAP = '    _next97 = _next97[:3]\n\n    # Safe opts proposal'

NEW_NEXT_CAP = r'''    _next97 = _next97[:3]

    # ─── AI-enhanced analysis (Patch 99) ─────────────────────────────────────
    _ai_analysis99 = ''
    try:
        import anthropic as _anth99
        import json as _json99
        _anth_key99 = os.environ.get('ANTHROPIC_API_KEY', '')
        if _anth_key99:
            _client99 = _anth99.Anthropic(api_key=_anth_key99)
            _ctx99 = (
                f"Paper trading bot daily snapshot:\n"
                f"Verdict: {_verdict97}\n"
                f"24h: {_trades_24h} trades | WR {_wr_24h:.1f}% | Avg PnL {_avg_pnl_24h:+.3f}%\n"
                f"24h: TIME_LIMIT={_tl_pct_24h:.1f}% | STOP_LOSS={_sl_count_24h} | DD={_dd_24h:+.2f}%\n"
                f"24h skipped signals: {_skips_24h}\n"
                f"7d: {_trades_7d} trades | WR {_wr_7d:.1f}% | Avg PnL {_avg_pnl_7d:+.3f}%\n"
                f"Gate params: {_json99.dumps(_params97)}\n"
                f"Backward flags: {_backward_flags}\n"
                f"Stalled agents: {_stalled97}\n"
                f"Rules-based actions (replace these with better ones): {_next97}\n"
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
        log.debug('[CHECKLIST] AI analysis failed (using rules-based): %s', _aie99)

    # Safe opts proposal'''

assert OLD_NEXT_CAP in txt, 'Anchor not found: _next97[:3] + Safe opts proposal'
txt = txt.replace(OLD_NEXT_CAP, NEW_NEXT_CAP)
print('✓ OP-1: AI call block inserted after _next97[:3]')

# ─── OP-2: Add ai_analysis to return dict ────────────────────────────────────

OLD_RETURN = "        'next_actions': _next97,\n    }\n\n\nasync def _watchdog_agent_loop():"
NEW_RETURN = ("        'next_actions': _next97,\n"
              "        'ai_analysis': _ai_analysis99,  # Patch 99\n"
              "    }\n\n\nasync def _watchdog_agent_loop():")

assert OLD_RETURN in txt, 'Anchor not found: return dict next_actions line'
txt = txt.replace(OLD_RETURN, NEW_RETURN)
print('✓ OP-2: ai_analysis added to return dict')

# ─── OP-3: Add AI analysis line to Telegram body in _auto_checklist_loop ─────

OLD_TG_BODY = ('                    _body_cl = (f"Verdict: {_verdict_cl}\\n"\n'
               '                                f"24h: {_trades_cl} trades | WR {_wr_cl:.0f}% | AvgPnL {_pnl_cl:+.3f}%\\n")\n'
               '                    if _actions_cl:\n'
               '                        _body_cl += "\\nTop Actions:\\n" + "\\n".join(\n'
               '                            f"  {_i+1}. {_a}" for _i, _a in enumerate(_actions_cl))')

NEW_TG_BODY = ('                    _ai_line_cl = _cl_result.get("ai_analysis", "")  # Patch 99\n'
               '                    _body_cl = (f"Verdict: {_verdict_cl}\\n"\n'
               '                                f"24h: {_trades_cl} trades | WR {_wr_cl:.0f}% | AvgPnL {_pnl_cl:+.3f}%\\n")\n'
               '                    if _ai_line_cl:\n'
               '                        _body_cl += f"\\n{_ai_line_cl}\\n"\n'
               '                    if _actions_cl:\n'
               '                        _body_cl += "\\nTop Actions:\\n" + "\\n".join(\n'
               '                            f"  {_i+1}. {_a}" for _i, _a in enumerate(_actions_cl))')

assert OLD_TG_BODY in txt, 'Anchor not found: Telegram _body_cl block'
txt = txt.replace(OLD_TG_BODY, NEW_TG_BODY)
print('✓ OP-3: AI analysis line added to Telegram body')

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
print('  TOKEN=$(curl -s -X POST http://localhost:8000/api/auth/login \\')
print('    -H \'Content-Type: application/json\' \\')
print('    -d \'{"password":"HArden978ab"}\' | python3 -c \'import sys,json; print(json.load(sys.stdin)["token"])\')')
print('  curl -s -X POST http://localhost:8000/api/orchestrator/run-daily-checklist \\')
print('    -H "Authorization: Bearer $TOKEN" | python3 -m json.tool | grep -A8 \'"next_actions"\'')
