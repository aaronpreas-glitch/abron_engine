"""
Patch 108 — Live Executor Wiring
Wires open_perp_sync() and close_perp_sync() into perp_executor.py.
Adds 3 DB columns. Flips PERP_DRY_RUN=false in .env.

Safety: checklist gates still run before every trade.
simulate_24h gate will naturally block first live trade until timer reaches 24h.
"""
import sys, py_compile, sqlite3
from pathlib import Path

# ── DB Migration ──────────────────────────────────────────────────────────────

DB = Path("/root/memecoin_engine/data_storage/engine.db")
with sqlite3.connect(str(DB)) as conn:
    for col, typ in [
        ("jupiter_position_key", "TEXT"),
        ("tx_sig_open",          "TEXT"),
        ("tx_sig_close",         "TEXT"),
    ]:
        try:
            conn.execute(f"ALTER TABLE perp_positions ADD COLUMN {col} {typ}")
            print(f"  ✅ Added column: {col}")
        except Exception:
            print(f"     (already exists: {col})")

# ── Code Modifications ────────────────────────────────────────────────────────

EXECUTOR = Path("/root/memecoin_engine/utils/perp_executor.py")
text = EXECUTOR.read_text()

# ── Change 1: _open_perp_position() signature ─────────────────────────────────

OLD1 = 'def _open_perp_position(\n    symbol: str, side: str, entry_price: float,\n    stop_price: float, tp1_price: float, tp2_price: float,\n    size_usd: float, leverage: float,\n    regime_label: str, dry_run: bool, notes: str = "",\n) -> dict | None:'

NEW1 = 'def _open_perp_position(\n    symbol: str, side: str, entry_price: float,\n    stop_price: float, tp1_price: float, tp2_price: float,\n    size_usd: float, leverage: float,\n    regime_label: str, dry_run: bool, notes: str = "",\n    jupiter_position_key: str = "", tx_sig_open: str = "",\n) -> dict | None:'

if OLD1 not in text:
    print("❌ Change 1 marker not found (_open_perp_position signature)")
    sys.exit(1)
text = text.replace(OLD1, NEW1, 1)
print("✅ Change 1: _open_perp_position signature updated")

# ── Change 2: INSERT statement ────────────────────────────────────────────────

OLD2 = (
    "            INSERT INTO perp_positions\n"
    "            (opened_ts_utc, symbol, side, entry_price, stop_price, tp1_price, tp2_price,\n"
    "             size_usd, leverage, collateral_usd, regime_label, status, dry_run, notes)\n"
    "            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', ?, ?)\n"
    '        """, (\n'
    "            ts, symbol.upper(), side.upper(), entry_price,\n"
    "            stop_price, tp1_price, tp2_price,\n"
    "            size_usd, leverage, collateral, regime_label,\n"
    "            1 if dry_run else 0, notes,\n"
    "        ))"
)

NEW2 = (
    "            INSERT INTO perp_positions\n"
    "            (opened_ts_utc, symbol, side, entry_price, stop_price, tp1_price, tp2_price,\n"
    "             size_usd, leverage, collateral_usd, regime_label, status, dry_run, notes,\n"
    "             jupiter_position_key, tx_sig_open)\n"
    "            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', ?, ?, ?, ?)\n"
    '        """, (\n'
    "            ts, symbol.upper(), side.upper(), entry_price,\n"
    "            stop_price, tp1_price, tp2_price,\n"
    "            size_usd, leverage, collateral, regime_label,\n"
    "            1 if dry_run else 0, notes,\n"
    "            jupiter_position_key, tx_sig_open,\n"
    "        ))"
)

if OLD2 not in text:
    print("❌ Change 2 marker not found (INSERT statement)")
    sys.exit(1)
text = text.replace(OLD2, NEW2, 1)
print("✅ Change 2: INSERT statement updated with live fields")

# ── Change 3: Open stub → live Jupiter call ───────────────────────────────────

OLD3 = (
    "    else:\n"
    "        # Live: would call Jupiter Perps open API here\n"
    "        # For now: open in DB as live (dry_run=0) and log warning\n"
    "        logger.warning(\"LIVE PERP: Jupiter Perps open API not yet integrated \u2014 recording in DB only\")\n"
    "        pos = _open_perp_position(\n"
    "            symbol, side, entry_price, stop_price, tp1_price, tp2_price,\n"
    "            size_usd, leverage, regime, dry_run=False, notes=notes,\n"
    "        )"
)

NEW3 = (
    "    else:\n"
    "        # Patch 108: Live Jupiter Perps execution\n"
    "        from utils.jupiter_perps_trade import open_perp_sync as _jpt_open  # noqa\n"
    "        _jlive = _jpt_open(\n"
    "            symbol=symbol, side=side,\n"
    "            collateral_usd=size_usd / leverage,\n"
    "            leverage=leverage,\n"
    "            dry_run=False,\n"
    "        )\n"
    "        if not _jlive.get(\"success\"):\n"
    "            logger.error(\"LIVE PERP OPEN FAILED: %s\", _jlive.get(\"error\"))\n"
    "            return False\n"
    "        actual_entry = _jlive.get(\"entry_price_usd\") or entry_price\n"
    "        pos = _open_perp_position(\n"
    "            symbol, side, actual_entry, stop_price, tp1_price, tp2_price,\n"
    "            size_usd, leverage, regime, dry_run=False, notes=notes,\n"
    "            jupiter_position_key=_jlive.get(\"position_pubkey\", \"\"),\n"
    "            tx_sig_open=_jlive.get(\"tx_sig\", \"\"),\n"
    "        )"
)

if OLD3 not in text:
    print("❌ Change 3 marker not found (open stub)")
    sys.exit(1)
text = text.replace(OLD3, NEW3, 1)
print("✅ Change 3: Open stub replaced with live Jupiter call")

# ── Change 4: Close function → live close_perp_sync + tx_sig_close column ────

OLD4 = (
    "    ts = _now_iso()\n"
    "    with _conn() as c:\n"
    "        cur = c.cursor()\n"
    "        cur.execute(\"\"\"\n"
    "            UPDATE perp_positions\n"
    "            SET status='CLOSED', closed_ts_utc=?, exit_price=?,\n"
    "                pnl_pct=?, pnl_usd=?, exit_reason=?\n"
    "            WHERE id=?\n"
    "        \"\"\", (ts, exit_price, round(leveraged_pct, 4), round(pnl_usd, 4), exit_reason, position_id))\n"
    "        c.commit()"
)

NEW4 = (
    "    # Patch 108: Execute on-chain close for live positions\n"
    "    _tx_sig_close = None\n"
    "    if not pos.get(\"dry_run\") and pos.get(\"jupiter_position_key\"):\n"
    "        try:\n"
    "            from utils.jupiter_perps_trade import close_perp_sync as _jpt_close  # noqa\n"
    "            _cr = _jpt_close(pos[\"jupiter_position_key\"], dry_run=False)\n"
    "            if _cr.get(\"success\"):\n"
    "                _tx_sig_close = _cr.get(\"tx_sig\")\n"
    "                logger.info(\"LIVE PERP CLOSE tx=%s\", _tx_sig_close)\n"
    "            else:\n"
    "                logger.error(\"LIVE PERP CLOSE FAILED id=%s: %s\", position_id, _cr.get(\"error\"))\n"
    "        except Exception as _jex:\n"
    "            logger.error(\"LIVE PERP CLOSE ERROR id=%s: %s\", position_id, _jex)\n"
    "\n"
    "    ts = _now_iso()\n"
    "    with _conn() as c:\n"
    "        cur = c.cursor()\n"
    "        cur.execute(\"\"\"\n"
    "            UPDATE perp_positions\n"
    "            SET status='CLOSED', closed_ts_utc=?, exit_price=?,\n"
    "                pnl_pct=?, pnl_usd=?, exit_reason=?, tx_sig_close=?\n"
    "            WHERE id=?\n"
    "        \"\"\", (ts, exit_price, round(leveraged_pct, 4), round(pnl_usd, 4), exit_reason, _tx_sig_close, position_id))\n"
    "        c.commit()"
)

if OLD4 not in text:
    print("❌ Change 4 marker not found (close UPDATE statement)")
    sys.exit(1)
text = text.replace(OLD4, NEW4, 1)
print("✅ Change 4: Close function wired with live Jupiter call")

# ── Write + compile ───────────────────────────────────────────────────────────

EXECUTOR.write_text(text)
try:
    py_compile.compile(str(EXECUTOR), doraise=True)
    print("✅ Syntax OK — perp_executor.py patched")
except py_compile.PyCompileError as e:
    print(f"❌ Syntax error: {e}")
    sys.exit(1)

# ── Flip PERP_DRY_RUN=false ───────────────────────────────────────────────────

ENV = Path("/root/memecoin_engine/.env")
env_text = ENV.read_text()
if "PERP_DRY_RUN=true" in env_text:
    env_text = env_text.replace("PERP_DRY_RUN=true", "PERP_DRY_RUN=false", 1)
    ENV.write_text(env_text)
    print("✅ PERP_DRY_RUN=false — engine is live on next restart")
elif "PERP_DRY_RUN=false" in env_text:
    print("   PERP_DRY_RUN already false")
else:
    ENV.write_text(env_text + "\nPERP_DRY_RUN=false\n")
    print("✅ PERP_DRY_RUN=false added to .env")

print("")
print("🚀 Patch 108 complete — engine wired for live trading")
print("   Restart the service to activate.")
print("   Note: checklist gates still apply — first live trade blocked until simulate_24h passes.")
