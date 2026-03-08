#!/usr/bin/env python3
"""Fix the indentation issue with sentiment block in main.py."""

MAIN_PY = "/root/memecoin_engine/dashboard/backend/main.py"

with open(MAIN_PY, "r") as f:
    code = f.read()

# The problem: sentiment block was inserted between "if chg_5m > threshold:" and its body
# Need to move it INSIDE the if block with proper indentation

# Fix 1: In scalp scan - find the broken pattern and fix it
broken = '''                    if chg_5m > threshold:

                # ── Fetch sentiment ──
                _sent_score = 0.0
                _sent_boost = 0
                try:
                    _ensure_engine_path()
                    from utils.x_sentiment import get_sentiment
                    _sent = await get_sentiment(symbol)
                    _sent_score = _sent.get("sentiment_score", 0)
                    _sent_boost = _sent.get("boost", 0)
                    if _sent_boost != 0:
                        logger.info("[SENTIMENT] %s score=%.2f boost=%+d vol_spike=%s",
                                    symbol, _sent_score, _sent_boost, _sent.get("volume_spike"))
                except Exception as _se:
                    pass

'''

fixed = '''                    if chg_5m > threshold:
                        # ── Fetch sentiment ──
                        _sent_score = 0.0
                        _sent_boost = 0
                        try:
                            _ensure_engine_path()
                            from utils.x_sentiment import get_sentiment
                            _sent = await get_sentiment(symbol)
                            _sent_score = _sent.get("sentiment_score", 0)
                            _sent_boost = _sent.get("boost", 0)
                            if _sent_boost != 0:
                                logger.info("[SENTIMENT] %s score=%.2f boost=%+d vol_spike=%s",
                                            symbol, _sent_score, _sent_boost, _sent.get("volume_spike"))
                        except Exception as _se:
                            pass

'''

count = code.count(broken)
if count > 0:
    code = code.replace(broken, fixed)
    print(f"✓ Fixed {count} broken sentiment indentation block(s)")
else:
    print("⚠ Broken pattern not found, trying alternative fix")
    # Try to find the issue more broadly
    # The core issue: sentiment block at wrong indent level after "if chg_5m > threshold:\n\n"
    old_pattern = 'if chg_5m > threshold:\n\n                # ── Fetch sentiment ──'
    new_pattern = 'if chg_5m > threshold:\n                        # ── Fetch sentiment ──'
    if old_pattern in code:
        code = code.replace(old_pattern, new_pattern)
        # Now fix all the lines in the block
        # The block lines are at 16-space indent but need to be at 24-space
        lines = code.split('\n')
        in_fix = False
        fixed_lines = []
        for line in lines:
            if '# ── Fetch sentiment ──' in line and line.strip().startswith('#'):
                in_fix = True
            if in_fix:
                if line.strip() == '' or (line.strip() and not line.startswith('                    ')):
                    in_fix = False
                    fixed_lines.append(line)
                    continue
                # Add 8 more spaces of indent
                if line.startswith('                ') and not line.startswith('                        '):
                    line = '        ' + line
            fixed_lines.append(line)
        code = '\n'.join(fixed_lines)
        print("✓ Fixed with alternative pattern")

# Also check MID scan has the same issue
# MID scan typically has different indentation
mid_broken = code.find('_mid_signal_scan_loop')
if mid_broken > 0:
    # Check for the same pattern in mid section
    mid_section_end = code.find('_post_exit_monitor', mid_broken)
    if mid_section_end == -1:
        mid_section_end = len(code)
    mid_section = code[mid_broken:mid_section_end]

    if '                    if chg_15m >' in mid_section or '                    if chg_5m >' in mid_section:
        # Check if sentiment block is misplaced here too
        broken_mid = mid_section.find('# ── Fetch sentiment ──')
        if broken_mid > 0:
            # Check indent of the fetch sentiment line
            line_start = mid_section.rfind('\n', 0, broken_mid) + 1
            indent = len(mid_section[line_start:broken_mid]) - len(mid_section[line_start:broken_mid].lstrip())
            if indent < 24:
                print(f"⚠ MID sentiment block may have indent issue (indent={indent})")


with open(MAIN_PY, "w") as f:
    f.write(code)

# Verify
try:
    compile(code, "main.py", "exec")
    print("\n✅ main.py compiles successfully after fix")
except SyntaxError as e:
    print(f"\n✗ Still has syntax error: {e}")
    print(f"  Line {e.lineno}: {e.text}")
