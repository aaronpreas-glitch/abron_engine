"""
Patch 181 — Fix TRIPLE confluence outcome pipeline.

Root causes:
  1. No per-mint dedup window: each new whale alert for a hot token (e.g. PUNCH)
     creates a fresh TRIPLE event. 17 TRIPLEs generated in 2 days for one token.
  2. EXPIRED rows skipped by _update_outcomes (only queries PENDING): rows manually
     set to EXPIRED during a previous cleanup session will never get 4h/24h prices.
  3. No TTL enforcer for confluence_events: PENDING rows with no price have no exit.
  4. No expire_reason field: operator cannot see WHY a row expired.

Fixes:
  A. DB: ADD COLUMN expire_reason TEXT to confluence_events
  B. DB recovery: tag existing EXPIRED rows with expire_reason='manual_cleanup_P181'
  C. confluence_engine._detect_confluences(): 4h per-mint dedup window
  D. confluence_engine._update_outcomes(): extend query to include EXPIRED rows;
     promote EXPIRED -> COMPLETE when price_24h filled; tag recovered rows
  E. confluence_engine: add _expire_stale_confluences() TTL enforcer (48h)
  F. confluence_engine.confluence_step(): call _expire_stale_confluences()
  G. wallets.py /api/wallets/triples: return expire_reason field
"""
import os
import sys
import sqlite3
import py_compile

DB_PATH = "/root/memecoin_engine/data_storage/engine.db"
CE_PATH = "/root/memecoin_engine/utils/confluence_engine.py"
WR_PATH = "/root/memecoin_engine/dashboard/backend/routers/wallets.py"

TQ = '"""'  # triple-quote helper to avoid embedding issues in anchor strings


# ── Steps A + B: DB migration ─────────────────────────────────────────────────
conn = sqlite3.connect(DB_PATH)

# A: Add expire_reason column
try:
    conn.execute("ALTER TABLE confluence_events ADD COLUMN expire_reason TEXT")
    conn.commit()
    print("Step A: Added expire_reason column to confluence_events")
except sqlite3.OperationalError as e:
    if "duplicate column" in str(e).lower():
        print("Step A: expire_reason column already exists — skipping")
    else:
        raise

# B: Tag existing EXPIRED rows for operator visibility
n = conn.execute("""
    UPDATE confluence_events
    SET expire_reason='manual_cleanup_P181'
    WHERE outcome_status='EXPIRED' AND expire_reason IS NULL
""").rowcount
conn.commit()
print(f"Step B: Tagged {n} EXPIRED row(s) with expire_reason='manual_cleanup_P181'")

conn.close()


# ── Steps C-F: Patch confluence_engine.py ────────────────────────────────────
with open(CE_PATH) as f:
    src = f.read()

# ── C: 4h per-mint dedup before INSERT ───────────────────────────────────────
OLD_C = (
    "            memecoin_scan_id = None\n"
    "\n"
    "        conn.execute(" + TQ
)
NEW_C = (
    "            memecoin_scan_id = None\n"
    "\n"
    "        # Patch 181: per-mint dedup — skip if any confluence event exists for\n"
    "        # this mint within the last 4h. Prevents hot tokens from generating\n"
    "        # dozens of TRIPLE events across successive whale alert IDs.\n"
    '        _dedup_cutoff_4h = (now - timedelta(hours=4)).strftime("%Y-%m-%d %H:%M:%S")\n'
    "        _recent_conf = conn.execute(\n"
    '            "SELECT 1 FROM confluence_events WHERE token_mint=? AND ts_utc >= ?",\n'
    "            (mint, _dedup_cutoff_4h),\n"
    "        ).fetchone()\n"
    "        if _recent_conf:\n"
    '            log.debug("[CONF] Dedup skip: %s %s — confluence already within 4h",\n'
    "                      conf_type, w.get(\"token_symbol\"))\n"
    "            continue\n"
    "\n"
    "        conn.execute(" + TQ
)

assert OLD_C in src, f"Anchor C not found in {CE_PATH}"
src = src.replace(OLD_C, NEW_C, 1)
print("Step C: Added 4h per-mint dedup to _detect_confluences()")


# ── D1: Extend _update_outcomes query to include EXPIRED rows with partial data ──
OLD_D1 = (
    "    rows = conn.execute(" + TQ + "\n"
    "        SELECT id, ts_utc, token_mint, price_at_event,\n"
    "               price_1h, price_4h, price_24h\n"
    "        FROM confluence_events\n"
    "        WHERE outcome_status='PENDING' AND price_at_event IS NOT NULL AND price_at_event > 0\n"
    "    " + TQ + ").fetchall()"
)
NEW_D1 = (
    "    # Patch 181: also process EXPIRED rows with partial price data so\n"
    "    # manually-expired events can still have their 4h/24h returns resolved.\n"
    "    rows = conn.execute(" + TQ + "\n"
    "        SELECT id, ts_utc, token_mint, price_at_event,\n"
    "               price_1h, price_4h, price_24h, outcome_status\n"
    "        FROM confluence_events\n"
    "        WHERE outcome_status IN ('PENDING', 'EXPIRED')\n"
    "          AND price_at_event IS NOT NULL AND price_at_event > 0\n"
    "          AND (price_1h IS NULL OR price_4h IS NULL OR price_24h IS NULL)\n"
    "    " + TQ + ").fetchall()"
)

assert OLD_D1 in src, f"Anchor D1 not found in {CE_PATH}"
src = src.replace(OLD_D1, NEW_D1, 1)
print("Step D1: Extended _update_outcomes query to include EXPIRED rows")


# ── D2: Promote EXPIRED -> COMPLETE when price_24h filled ────────────────────
OLD_D2 = (
    '        if age_s >= 86400 and row["price_24h"] is None:\n'
    "            updates[\"price_24h\"] = current\n"
    "            updates[\"return_24h_pct\"] = round((current - entry) / entry * 100, 2)\n"
    '            updates["outcome_status"] = "COMPLETE"'
)
NEW_D2 = (
    '        if age_s >= 86400 and row["price_24h"] is None:\n'
    "            updates[\"price_24h\"] = current\n"
    "            updates[\"return_24h_pct\"] = round((current - entry) / entry * 100, 2)\n"
    '            updates["outcome_status"] = "COMPLETE"\n'
    "            # Patch 181: tag recovered EXPIRED rows for operator visibility\n"
    '            if row.get("outcome_status") == "EXPIRED":\n'
    '                updates["expire_reason"] = "recovered_P181"'
)

assert OLD_D2 in src, f"Anchor D2 not found in {CE_PATH}"
src = src.replace(OLD_D2, NEW_D2, 1)
print("Step D2: EXPIRED rows promoted to COMPLETE when price_24h filled")


# ── E: _expire_stale_confluences() TTL enforcer ──────────────────────────────
EXPIRE_FUNC = (
    "\n\n"
    "def _expire_stale_confluences(conn) -> None:\n"
    '    """Patch 181: TTL enforcer for confluence_events.\n'
    "\n"
    "    PENDING events older than 48h with price_24h still NULL are marked EXPIRED\n"
    "    with a reason code. Reason codes written to expire_reason:\n"
    "      ttl_48h         — event aged out naturally with no 24h price\n"
    "      no_entry_price  — created without price_at_event (can never resolve)\n"
    '    """\n'
    "    now = datetime.now(timezone.utc)\n"
    '    cutoff_48h = (now - timedelta(hours=48)).strftime("%Y-%m-%d %H:%M:%S")\n'
    "\n"
    "    stale = conn.execute(\"\"\"\n"
    "        SELECT id, price_at_event\n"
    "        FROM confluence_events\n"
    "        WHERE outcome_status='PENDING'\n"
    "          AND ts_utc < ?\n"
    "          AND price_24h IS NULL\n"
    "    \"\"\", (cutoff_48h,)).fetchall()\n"
    "\n"
    "    expired_count = 0\n"
    "    for row in stale:\n"
    "        row = dict(row)\n"
    "        if not row.get(\"price_at_event\") or float(row.get(\"price_at_event\") or 0) <= 0:\n"
    '            reason = "no_entry_price"\n'
    "        else:\n"
    '            reason = "ttl_48h"\n'
    "        conn.execute(\n"
    "            \"UPDATE confluence_events SET outcome_status='EXPIRED', expire_reason=? WHERE id=?\",\n"
    "            (reason, row[\"id\"]),\n"
    "        )\n"
    "        expired_count += 1\n"
    '        log.info("[CONF] TTL-expired confluence id=%d reason=%s", row["id"], reason)\n'
    "\n"
    "    if expired_count:\n"
    "        conn.commit()\n"
    '        log.info("[CONF] _expire_stale_confluences: expired %d stale PENDING rows", expired_count)\n'
)

# Insert before the public entry point section
STEP_ANCHOR = "\n# ──────────────────────────────────────────────────────────────────────────────\n# Public entry point"
assert STEP_ANCHOR in src, f"Anchor for public entry point not found in {CE_PATH}"
src = src.replace(STEP_ANCHOR, EXPIRE_FUNC + STEP_ANCHOR, 1)
print("Step E: Added _expire_stale_confluences() TTL enforcer")


# ── F: Call TTL enforcer from confluence_step ─────────────────────────────────
OLD_F = (
    "        with get_conn() as conn:\n"
    "            _detect_confluences(conn)\n"
    "            _update_outcomes(conn)\n"
)
NEW_F = (
    "        with get_conn() as conn:\n"
    "            _detect_confluences(conn)\n"
    "            _update_outcomes(conn)\n"
    "            _expire_stale_confluences(conn)  # Patch 181: TTL enforcer\n"
)

assert OLD_F in src, f"Anchor F not found in {CE_PATH}"
src = src.replace(OLD_F, NEW_F, 1)
print("Step F: confluence_step() now calls _expire_stale_confluences()")


# Write and verify
with open(CE_PATH, "w") as f:
    f.write(src)

py_compile.compile(CE_PATH, doraise=True)
print(f"confluence_engine.py — py_compile: OK")


# ── Step G: Add expire_reason to /api/wallets/triples ────────────────────────
with open(WR_PATH) as f:
    wr_src = f.read()

OLD_G = (
    "                SELECT id, ts_utc, token_mint, token_symbol,\n"
    "                       sources AS source_details, market_cap_usd,\n"
    "                       return_1h_pct, return_4h_pct, return_24h_pct,\n"
    "                       outcome_status\n"
    "                FROM confluence_events\n"
    "                WHERE confluence_type='TRIPLE'"
)
NEW_G = (
    "                SELECT id, ts_utc, token_mint, token_symbol,\n"
    "                       sources AS source_details, market_cap_usd,\n"
    "                       return_1h_pct, return_4h_pct, return_24h_pct,\n"
    "                       outcome_status, expire_reason\n"
    "                FROM confluence_events\n"
    "                WHERE confluence_type='TRIPLE'"
)

assert OLD_G in wr_src, f"Anchor G not found in {WR_PATH}"
wr_src = wr_src.replace(OLD_G, NEW_G, 1)

with open(WR_PATH, "w") as f:
    f.write(wr_src)

py_compile.compile(WR_PATH, doraise=True)
print(f"wallets.py — py_compile: OK")


print("\nPatch 181 applied successfully.")
print("  A. expire_reason column added to confluence_events")
print("  B. Existing EXPIRED rows tagged 'manual_cleanup_P181'")
print("  C. 4h per-mint dedup in _detect_confluences()")
print("  D. _update_outcomes() processes EXPIRED rows + promotes to COMPLETE")
print("  E. _expire_stale_confluences() TTL enforcer added (48h)")
print("  F. confluence_step() calls TTL enforcer each cycle")
print("  G. /api/wallets/triples returns expire_reason field")
