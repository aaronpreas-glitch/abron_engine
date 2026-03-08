#!/usr/bin/env python3
"""
Patch perp_executor.py to:
1. Add rsi_14 to trade notes for all 3 modes
2. Add post-exit tracking record creation on close
"""
import sys

PERP_PY = "/root/memecoin_engine/utils/perp_executor.py"

with open(PERP_PY, "r") as f:
    code = f.read()

# ── 1. Add rsi_14 to notes for all 3 modes ──
# For each mode's momentum section, add rsi_14

# Scalp mode notes - find the mom_str loop in the scalp block
# We need to add rsi_14 after momentum entries
# The pattern: "for mk in ("momentum_5m", "momentum_15m", "momentum_1h"):" appears 3 times

# Add rsi_14 to the momentum keys for all modes
old_mom_keys = '''for mk in ("momentum_5m", "momentum_15m", "momentum_1h"):
            if mk in signal:
                mom_str += f"|{mk}={signal[mk]}"'''

new_mom_keys = '''for mk in ("momentum_5m", "momentum_15m", "momentum_1h", "rsi_14"):
            if mk in signal:
                mom_str += f"|{mk}={signal[mk]}"'''

count = code.count(old_mom_keys)
if count > 0:
    code = code.replace(old_mom_keys, new_mom_keys)
    print(f"✓ Added rsi_14 to momentum keys in {count} mode blocks")
else:
    # Try alternate formatting
    print("⚠ Could not find momentum keys pattern — checking alternate format")
    if 'rsi_14' not in code:
        print("✗ rsi_14 not in code and pattern not found")
    else:
        print("⚠ rsi_14 already in code")

# ── 2. Add post-exit tracking record creation on close ──
# After the broadcast_trade_event block in _close_perp_position, insert post-exit tracking

POST_EXIT_INSERT = '''
    # Create post-exit tracking record for price monitoring after close
    try:
        import sqlite3 as _sq3
        _db = str(pathlib.Path(__file__).resolve().parent.parent / "data_storage" / "engine.db")
        notes_str_pe = pos.get("notes") or ""
        _pe_mode = "SCALP" if "mode=SCALP" in notes_str_pe else ("MID" if "mode=MID" in notes_str_pe else "SWING")
        with _sq3.connect(_db) as _pc:
            _pc.execute("""
                INSERT OR IGNORE INTO post_exit_tracking
                  (position_id, symbol, side, mode, exit_price, exit_reason, exit_ts)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (position_id, pos["symbol"], side, _pe_mode, exit_price, exit_reason, ts))
            _pc.commit()
        logger.debug("[POST-EXIT] Queued tracking for %s %s pos=%d", pos["symbol"], side, position_id)
    except Exception as _pe_e:
        logger.debug("post_exit_tracking insert failed: %s", _pe_e)
'''

if 'post_exit_tracking' not in code:
    # Insert after the broadcast block, right before "return pos"
    # Find "return pos" in _close_perp_position
    # The pattern is: "    return pos\n" at the end of _close_perp_position
    # After the broadcast try/except block
    old_return = '''        pass

    return pos


def _update_price_extremes'''
    new_return = '''        pass
''' + POST_EXIT_INSERT + '''
    return pos


def _update_price_extremes'''

    if old_return in code:
        code = code.replace(old_return, new_return, 1)
        print("✓ Added post-exit tracking record creation in _close_perp_position")
    else:
        print("✗ Could not find insertion point for post-exit tracking")
        # Try simpler pattern
        old_return2 = '    return pos\n\n\ndef _update_price_extremes'
        new_return2 = POST_EXIT_INSERT + '\n    return pos\n\n\ndef _update_price_extremes'
        if old_return2 in code:
            code = code.replace(old_return2, new_return2, 1)
            print("✓ Added post-exit tracking (alt pattern)")
        else:
            print("✗ Could not find any insertion point")
else:
    print("⚠ post_exit_tracking already in code, skipping")

with open(PERP_PY, "w") as f:
    f.write(code)

print("\n✅ perp_executor.py patched successfully")
