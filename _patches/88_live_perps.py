#!/usr/bin/env python3
"""Patch 88 — Jupiter Perps Live Execution Layer

perp_executor.py:
  PE-R1: DB migration — add jupiter_position_key, tx_sig_open, tx_sig_close columns
  PE-R2: _open_perp_position() — add new columns to INSERT
  PE-R3: Wire open_perp_sync() at dry_run=False open branch
  PE-R4: Wire close_perp_sync() inside _close_perp_position()

PERP_DRY_RUN stays true — no live trades fire until user explicitly flips it.
"""
from pathlib import Path
import subprocess

PE   = Path("/root/memecoin_engine/utils/perp_executor.py")
text = PE.read_text()

# ─────────────────────────────────────────────────────────────────────────────
# PE-R1: DB migration — add 3 new columns to _ensure_dynamic_exit_table()
# Anchor: last ADD COLUMN block before c.commit() in that function
# ─────────────────────────────────────────────────────────────────────────────
PE_R1_ANCHOR = (
    '        try:\n'
    '            c.execute("ALTER TABLE perp_positions ADD COLUMN rs_triggered TEXT")\n'
    '        except Exception:\n'
    '            pass  # already exists\n'
    '        c.commit()'
)
assert text.count(PE_R1_ANCHOR) == 1, f"PE-R1: expected 1 anchor, found {text.count(PE_R1_ANCHOR)}"

PE_R1_NEW = (
    '        try:\n'
    '            c.execute("ALTER TABLE perp_positions ADD COLUMN rs_triggered TEXT")\n'
    '        except Exception:\n'
    '            pass  # already exists\n'
    '        # Patch 88: Jupiter Perps live execution columns\n'
    '        for _col88 in [\n'
    '            ("jupiter_position_key", "TEXT"),\n'
    '            ("tx_sig_open",          "TEXT"),\n'
    '            ("tx_sig_close",         "TEXT"),\n'
    '        ]:\n'
    '            try:\n'
    '                c.execute(f"ALTER TABLE perp_positions ADD COLUMN {_col88[0]} {_col88[1]}")\n'
    '            except Exception:\n'
    '                pass  # already exists\n'
    '        c.commit()'
)

text = text.replace(PE_R1_ANCHOR, PE_R1_NEW)
assert text.count(PE_R1_NEW) == 1, "PE-R1 replacement produced multiple matches"
print("88 PE-R1: DB migration columns added ✓")

# ─────────────────────────────────────────────────────────────────────────────
# PE-R2: _open_perp_position() — add new keyword params + INSERT columns
# ─────────────────────────────────────────────────────────────────────────────
PE_R2_SIG_ANCHOR = (
    'def _open_perp_position(\n'
    '    symbol: str, side: str, entry_price: float,\n'
    '    stop_price: float, tp1_price: float, tp2_price: float,\n'
    '    size_usd: float, leverage: float,\n'
    '    regime_label: str, dry_run: bool, notes: str = "",\n'
    ') -> dict | None:'
)
assert text.count(PE_R2_SIG_ANCHOR) == 1, f"PE-R2 sig: expected 1, found {text.count(PE_R2_SIG_ANCHOR)}"

PE_R2_SIG_NEW = (
    'def _open_perp_position(\n'
    '    symbol: str, side: str, entry_price: float,\n'
    '    stop_price: float, tp1_price: float, tp2_price: float,\n'
    '    size_usd: float, leverage: float,\n'
    '    regime_label: str, dry_run: bool, notes: str = "",\n'
    '    jupiter_position_key: str | None = None,\n'
    '    tx_sig_open: str | None = None,\n'
    ') -> dict | None:'
)
text = text.replace(PE_R2_SIG_ANCHOR, PE_R2_SIG_NEW)
assert text.count(PE_R2_SIG_NEW) == 1, "PE-R2 sig replacement error"
print("88 PE-R2a: function signature updated ✓")

PE_R2_INSERT_ANCHOR = (
    '        cur.execute("""\n'
    '            INSERT INTO perp_positions\n'
    '            (opened_ts_utc, symbol, side, entry_price, stop_price, tp1_price, tp2_price,\n'
    '             size_usd, leverage, collateral_usd, regime_label, status, dry_run, notes)\n'
    '            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, \'OPEN\', ?, ?)\n'
    '        """, (\n'
    '            ts, symbol.upper(), side.upper(), entry_price,\n'
    '            stop_price, tp1_price, tp2_price,\n'
    '            size_usd, leverage, collateral, regime_label,\n'
    '            1 if dry_run else 0, notes,\n'
    '        ))'
)
assert text.count(PE_R2_INSERT_ANCHOR) == 1, f"PE-R2 insert: expected 1, found {text.count(PE_R2_INSERT_ANCHOR)}"

PE_R2_INSERT_NEW = (
    '        cur.execute("""\n'
    '            INSERT INTO perp_positions\n'
    '            (opened_ts_utc, symbol, side, entry_price, stop_price, tp1_price, tp2_price,\n'
    '             size_usd, leverage, collateral_usd, regime_label, status, dry_run, notes,\n'
    '             jupiter_position_key, tx_sig_open)\n'
    '            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, \'OPEN\', ?, ?, ?, ?)\n'
    '        """, (\n'
    '            ts, symbol.upper(), side.upper(), entry_price,\n'
    '            stop_price, tp1_price, tp2_price,\n'
    '            size_usd, leverage, collateral, regime_label,\n'
    '            1 if dry_run else 0, notes,\n'
    '            jupiter_position_key, tx_sig_open,\n'
    '        ))'
)
text = text.replace(PE_R2_INSERT_ANCHOR, PE_R2_INSERT_NEW)
assert text.count(PE_R2_INSERT_NEW) == 1, "PE-R2 insert replacement error"
print("88 PE-R2b: INSERT updated with new columns ✓")

# ─────────────────────────────────────────────────────────────────────────────
# PE-R3: Wire open_perp_sync() at dry_run=False open branch
# ─────────────────────────────────────────────────────────────────────────────
PE_R3_ANCHOR = (
    '    else:\n'
    '        # Live: would call Jupiter Perps open API here\n'
    '        # For now: open in DB as live (dry_run=0) and log warning\n'
    '        logger.warning("LIVE PERP: Jupiter Perps open API not yet integrated — recording in DB only")\n'
    '        pos = _open_perp_position(\n'
    '            symbol, side, entry_price, stop_price, tp1_price, tp2_price,\n'
    '            size_usd, leverage, regime, dry_run=False, notes=notes,\n'
    '        )'
)
assert text.count(PE_R3_ANCHOR) == 1, f"PE-R3: expected 1, found {text.count(PE_R3_ANCHOR)}"

PE_R3_NEW = (
    '    else:\n'
    '        # Patch 88: Jupiter Perps live execution\n'
    '        try:\n'
    '            from utils.jupiter_perps_trade import open_perp_sync as _jpt_open\n'
    '        except ImportError:\n'
    '            _jpt_open = None\n'
    '        if _jpt_open is None:\n'
    '            logger.error("LIVE PERP: jupiter_perps_trade not available — aborting open")\n'
    '            pos = None\n'
    '        else:\n'
    '            _jlive = _jpt_open(\n'
    '                symbol=symbol, side=side,\n'
    '                collateral_usd=size_usd / leverage,\n'
    '                leverage=leverage,\n'
    '                dry_run=False,\n'
    '            )\n'
    '            if not _jlive.get("success"):\n'
    '                logger.error("LIVE PERP OPEN FAILED: %s", _jlive.get("error"))\n'
    '                pos = None\n'
    '            else:\n'
    '                pos = _open_perp_position(\n'
    '                    symbol, side, entry_price, stop_price, tp1_price, tp2_price,\n'
    '                    size_usd, leverage, regime, dry_run=False, notes=notes,\n'
    '                    jupiter_position_key=_jlive.get("position_pubkey"),\n'
    '                    tx_sig_open=_jlive.get("tx_sig"),\n'
    '                )'
)
text = text.replace(PE_R3_ANCHOR, PE_R3_NEW)
assert text.count(PE_R3_NEW) == 1, "PE-R3 replacement error"
print("88 PE-R3: open_perp_sync wired at dry_run=False branch ✓")

# ─────────────────────────────────────────────────────────────────────────────
# PE-R4: Wire close_perp_sync() inside _close_perp_position()
# Insert BEFORE the PnL calculation block (after pos = dict(row))
# ─────────────────────────────────────────────────────────────────────────────
PE_R4_ANCHOR = (
    '    entry = pos["entry_price"]\n'
    '    side  = pos["side"].upper()\n'
    '    size  = pos["size_usd"]\n'
    '    lev   = pos["leverage"]\n'
    '\n'
    '    # PnL calculation (leveraged)\n'
    '    if side == "LONG":\n'
    '        raw_pct = (exit_price - entry) / entry * 100\n'
    '    else:\n'
    '        raw_pct = (entry - exit_price) / entry * 100'
)
assert text.count(PE_R4_ANCHOR) == 1, f"PE-R4: expected 1, found {text.count(PE_R4_ANCHOR)}"

PE_R4_NEW = (
    '    entry = pos["entry_price"]\n'
    '    side  = pos["side"].upper()\n'
    '    size  = pos["size_usd"]\n'
    '    lev   = pos["leverage"]\n'
    '\n'
    '    # Patch 88: Execute on-chain close for live (non-paper) positions\n'
    '    _tx_sig_close = None\n'
    '    if not pos.get("dry_run") and pos.get("jupiter_position_key"):\n'
    '        try:\n'
    '            from utils.jupiter_perps_trade import close_perp_sync as _jpt_close\n'
    '            _cr = _jpt_close(pos["jupiter_position_key"], dry_run=False)\n'
    '            if _cr.get("success"):\n'
    '                _tx_sig_close = _cr.get("tx_sig")\n'
    '            else:\n'
    '                logger.error(\n'
    '                    "LIVE PERP CLOSE FAILED id=%s: %s",\n'
    '                    position_id, _cr.get("error"),\n'
    '                )\n'
    '                # Continue — still update DB for audit trail\n'
    '        except Exception as _jex:\n'
    '            logger.error("LIVE PERP CLOSE ERROR id=%s: %s", position_id, _jex)\n'
    '\n'
    '    # PnL calculation (leveraged)\n'
    '    if side == "LONG":\n'
    '        raw_pct = (exit_price - entry) / entry * 100\n'
    '    else:\n'
    '        raw_pct = (entry - exit_price) / entry * 100'
)
text = text.replace(PE_R4_ANCHOR, PE_R4_NEW)
assert text.count(PE_R4_NEW) == 1, "PE-R4 replacement error"
print("88 PE-R4: close_perp_sync wired in _close_perp_position ✓")

# ─────────────────────────────────────────────────────────────────────────────
# PE-R5: Store tx_sig_close in the UPDATE statement
# ─────────────────────────────────────────────────────────────────────────────
PE_R5_ANCHOR = (
    '        cur.execute("""\n'
    '            UPDATE perp_positions\n'
    '            SET status=\'CLOSED\', closed_ts_utc=?, exit_price=?,\n'
    '                pnl_pct=?, pnl_usd=?, exit_reason=?\n'
    '            WHERE id=?\n'
    '        """, (ts, exit_price, round(leveraged_pct, 4), round(pnl_usd, 4), exit_reason, position_id))'
)
assert text.count(PE_R5_ANCHOR) == 1, f"PE-R5: expected 1, found {text.count(PE_R5_ANCHOR)}"

PE_R5_NEW = (
    '        cur.execute("""\n'
    '            UPDATE perp_positions\n'
    '            SET status=\'CLOSED\', closed_ts_utc=?, exit_price=?,\n'
    '                pnl_pct=?, pnl_usd=?, exit_reason=?, tx_sig_close=?\n'
    '            WHERE id=?\n'
    '        """, (ts, exit_price, round(leveraged_pct, 4), round(pnl_usd, 4), exit_reason, _tx_sig_close, position_id))'
)
text = text.replace(PE_R5_ANCHOR, PE_R5_NEW)
assert text.count(PE_R5_NEW) == 1, "PE-R5 replacement error"
print("88 PE-R5: tx_sig_close stored in UPDATE ✓")

# ─────────────────────────────────────────────────────────────────────────────
# Write + compile
# ─────────────────────────────────────────────────────────────────────────────
PE.write_text(text)
r = subprocess.run(["python3", "-m", "py_compile", str(PE)], capture_output=True, text=True)
if r.returncode != 0:
    print("PE COMPILE ERROR:", r.stderr)
    raise SystemExit(1)
print("88 PE compile OK ✓")
print("\n88 patch complete — restart service to activate")
