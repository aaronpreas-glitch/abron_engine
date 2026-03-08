#!/usr/bin/env python3
"""Fix MID scan sentiment indentation and any remaining issues."""

MAIN_PY = "/root/memecoin_engine/dashboard/backend/main.py"

with open(MAIN_PY, "r") as f:
    code = f.read()

# Fix MID scan: same pattern as SCALP
broken_mid = '''                    if chg_15m > threshold:

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

fixed_mid = '''                    if chg_15m > threshold:
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

if broken_mid in code:
    code = code.replace(broken_mid, fixed_mid)
    print("✓ Fixed MID sentiment indentation")
else:
    print("⚠ MID broken pattern not found exactly, trying flexible fix")
    # Try to fix by finding and replacing
    old = 'if chg_15m > threshold:\n\n                # ── Fetch sentiment'
    if old in code:
        # Replace and re-indent the block
        idx = code.find(old)
        # Find end of sentiment block (the "pass" line followed by empty line)
        block_end = code.find('\n\n                        await execute_perp_signal', idx)
        if block_end == -1:
            block_end = code.find('\n\n                        await', idx)
        if block_end > 0:
            old_block = code[idx:block_end]
            # Re-indent: add 8 spaces to each line in the sentiment block
            lines = old_block.split('\n')
            new_lines = [lines[0].replace('\n\n', '\n')]  # The if line, remove extra newline
            for line in lines[1:]:
                if line.strip():
                    new_lines.append('        ' + line)
                else:
                    new_lines.append(line)
            code = code[:idx] + '\n'.join(new_lines) + code[block_end:]
            print("✓ Fixed MID with flexible approach")

# Also need to initialize _sent_score before the "if" blocks for safety
# In SCALP section, ensure _sent_score is initialized BEFORE the if chg_5m check
# Let's add a default initialization at the top of the for loop
scalp_start = code.find('_scalp_signal_scan_loop')
mid_start = code.find('_mid_signal_scan_loop')

if scalp_start > 0 and mid_start > 0:
    # In the scalp for loop, add initialization before the if threshold check
    # Find "for symbol in symbols:" in scalp section
    scalp_for = code.find('for symbol in symbols', scalp_start)
    if scalp_for > 0 and scalp_for < mid_start:
        # Find the line after "for symbol in symbols:" and add init
        for_line_end = code.find('\n', scalp_for)
        next_line_start = for_line_end + 1
        next_line = code[next_line_start:code.find('\n', next_line_start)]
        indent = next_line[:len(next_line) - len(next_line.lstrip())]

        # Only add if not already there
        if '_sent_score = 0' not in code[scalp_for:scalp_for+300]:
            init_block = f'\n{indent}_sent_score = 0.0\n{indent}_sent_boost = 0\n'
            code = code[:next_line_start] + init_block + code[next_line_start:]
            print("✓ Added sentiment init in scalp for-loop")

    # Same for MID
    mid_for = code.find('for symbol in symbols', mid_start)
    if mid_for > 0:
        for_line_end = code.find('\n', mid_for)
        next_line_start = for_line_end + 1
        next_line = code[next_line_start:code.find('\n', next_line_start)]
        indent = next_line[:len(next_line) - len(next_line.lstrip())]

        if '_sent_score = 0' not in code[mid_for:mid_for+300]:
            init_block = f'\n{indent}_sent_score = 0.0\n{indent}_sent_boost = 0\n'
            code = code[:next_line_start] + init_block + code[next_line_start:]
            print("✓ Added sentiment init in mid for-loop")


with open(MAIN_PY, "w") as f:
    f.write(code)

# Verify
try:
    compile(code, "main.py", "exec")
    print("\n✅ main.py compiles successfully")
except SyntaxError as e:
    print(f"\n✗ Still has error at line {e.lineno}: {e.msg}")
    # Show the problematic area
    lines = code.split('\n')
    start = max(0, e.lineno - 3)
    end = min(len(lines), e.lineno + 3)
    for i in range(start, end):
        marker = ">>>" if i == e.lineno - 1 else "   "
        print(f"  {marker} {i+1}: {lines[i][:100]}")
