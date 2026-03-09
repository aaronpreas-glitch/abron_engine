"""
Patch 187 — Fix confluence duplicate-row creation (P187)

Root cause:
  _detect_confluences() iterates over whale_watch_alerts in a single loop.
  When the whale scanner detects 2 buys for the same token in the same pass,
  it inserts consecutive whale_alert_ids (e.g. 237/238). Both pass the
  per-whale-alert dedup (different ids) and the P181 4h-mint DB dedup
  SELECT — because Python sqlite3's WAL read snapshot in the same connection
  does not reliably reflect a just-committed INSERT from the previous loop
  iteration.  Result: every hot token generates an exact duplicate confluence
  row with the same ts_utc and identical outcome data.

Evidence (from DB):
  13 groups with (token_mint, ts_utc) duplicates — all pairs have consecutive
  whale_alert_ids confirming same-loop-call origin.
  Tokens affected: PENGUIN (×3 at one ts), PUNCH (×2 at 9 ts), LOBSTAR (×2 at 2 ts)

Fixes:
  A. utils/confluence_engine.py:
       — Add mints_inserted_this_run: set before the whale loop
       — After per-whale-alert dedup, skip if mint already inserted this call
       — Change INSERT → INSERT OR IGNORE (safety net against future schema
         collisions once UNIQUE index is in place)
       — After conn.commit(), add mint to the in-run set
  B. DB: CREATE UNIQUE INDEX on (token_mint, ts_utc) after cleanup
  C. DB: DELETE 13 duplicate rows (keep lowest id per exact duplicate group)

No frontend changes: confluence event table reads DB directly; row counts
will correctly reflect deduplicated data.  No operator-facing wording or
reason codes change.  No strategy/trading behavior changes.

Files changed:
  /root/memecoin_engine/utils/confluence_engine.py
  /root/memecoin_engine/data_storage/engine.db  (direct SQL, no migration file)
"""
import py_compile
import sqlite3

CE_PATH = "/root/memecoin_engine/utils/confluence_engine.py"
DB_PATH = "/root/memecoin_engine/data_storage/engine.db"

ce = open(CE_PATH).read()


# ── A1: Add mints_inserted_this_run set + in-run dedup check ─────────────────
# Anchor: the block from `if not whales: return` through the per-whale dedup
# `if exists: continue` and into `# ── Arm 1:`.  Unique within the file.

OLD_A = (
    "    if not whales:\n"
    "        return\n"
    "\n"
    "    for w in whales:\n"
    "        w = dict(w)\n"
    "        mint = w[\"token_mint\"]\n"
    "\n"
    "        # Skip if already logged for this whale alert\n"
    "        exists = conn.execute(\n"
    "            \"SELECT 1 FROM confluence_events WHERE whale_alert_id=?\", (w[\"id\"],)\n"
    "        ).fetchone()\n"
    "        if exists:\n"
    "            continue\n"
    "\n"
    "        # ── Arm 1: Memecoin Scanner ────────────────────────────────────────────\n"
)

NEW_A = (
    "    if not whales:\n"
    "        return\n"
    "\n"
    "    mints_inserted_this_run: set = set()  # P187: in-run dedup\n"
    "\n"
    "    for w in whales:\n"
    "        w = dict(w)\n"
    "        mint = w[\"token_mint\"]\n"
    "\n"
    "        # Skip if already logged for this whale alert\n"
    "        exists = conn.execute(\n"
    "            \"SELECT 1 FROM confluence_events WHERE whale_alert_id=?\", (w[\"id\"],)\n"
    "        ).fetchone()\n"
    "        if exists:\n"
    "            continue\n"
    "\n"
    "        # P187: in-run dedup — prevents same-second duplicates when multiple\n"
    "        # consecutive whale alerts fire for the same token in one step() call.\n"
    "        # The DB 4h mint dedup below handles cross-run; this handles intra-run\n"
    "        # where SQLite WAL snapshots may not see the same-call committed INSERT.\n"
    "        if mint in mints_inserted_this_run:\n"
    "            log.debug(\"[CONF] Dedup skip (in-run): %s — mint already inserted this call\",\n"
    "                      w.get(\"token_symbol\"))\n"
    "            continue\n"
    "\n"
    "        # ── Arm 1: Memecoin Scanner ────────────────────────────────────────────\n"
)

assert OLD_A in ce, "Anchor A not found — check _detect_confluences whale loop header"
ce = ce.replace(OLD_A, NEW_A, 1)
print("Step A1: mints_inserted_this_run set + in-run dedup check added")


# ── A2: INSERT → INSERT OR IGNORE, add mints_inserted_this_run.add() ─────────
# Anchor: the INSERT block + conn.commit() in _detect_confluences.
# Using the unique INSERT column list as anchor; only one INSERT in this function.

OLD_B = (
    "        conn.execute(\"\"\"\n"
    "            INSERT INTO confluence_events\n"
    "              (ts_utc, token_symbol, token_mint, sources,\n"
    "               whale_alert_id, memecoin_scan_id,\n"
    "               whale_score, memecoin_score, confluence_score,\n"
    "               market_cap_usd, price_at_event,\n"
    "               confluence_type, source_count)\n"
    "            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)\n"
    "        \"\"\", (\n"
    "            now.strftime(\"%Y-%m-%d %H:%M:%S\"),\n"
    "            w.get(\"token_symbol\") or \"???\",\n"
    "            mint,\n"
    "            sources,\n"
    "            w[\"id\"],\n"
    "            memecoin_scan_id,\n"
    "            whale_score,\n"
    "            meme_score,\n"
    "            conf_score,\n"
    "            w.get(\"market_cap_usd\"),\n"
    "            w.get(\"price_at_alert\"),\n"
    "            conf_type,\n"
    "            source_count,\n"
    "        ))\n"
    "        conn.commit()\n"
)

NEW_B = (
    "        conn.execute(\"\"\"\n"
    "            INSERT OR IGNORE INTO confluence_events\n"
    "              (ts_utc, token_symbol, token_mint, sources,\n"
    "               whale_alert_id, memecoin_scan_id,\n"
    "               whale_score, memecoin_score, confluence_score,\n"
    "               market_cap_usd, price_at_event,\n"
    "               confluence_type, source_count)\n"
    "            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)\n"
    "        \"\"\", (\n"
    "            now.strftime(\"%Y-%m-%d %H:%M:%S\"),\n"
    "            w.get(\"token_symbol\") or \"???\",\n"
    "            mint,\n"
    "            sources,\n"
    "            w[\"id\"],\n"
    "            memecoin_scan_id,\n"
    "            whale_score,\n"
    "            meme_score,\n"
    "            conf_score,\n"
    "            w.get(\"market_cap_usd\"),\n"
    "            w.get(\"price_at_alert\"),\n"
    "            conf_type,\n"
    "            source_count,\n"
    "        ))\n"
    "        conn.commit()\n"
    "        mints_inserted_this_run.add(mint)  # P187: mark mint as inserted this run\n"
)

assert OLD_B in ce, "Anchor B not found — check INSERT block in _detect_confluences"
ce = ce.replace(OLD_B, NEW_B, 1)
print("Step A2: INSERT → INSERT OR IGNORE, mints_inserted_this_run.add() added after commit")


# Write and verify
with open(CE_PATH, "w") as f:
    f.write(ce)

py_compile.compile(CE_PATH, doraise=True)
print(f"confluence_engine.py — py_compile: OK\n")


# ── B: DB cleanup — delete duplicate rows, add UNIQUE INDEX ──────────────────

conn = sqlite3.connect(DB_PATH, timeout=10)
conn.execute("PRAGMA journal_mode=WAL")

# B1: Count duplicates before cleanup
dup_count_before = conn.execute("""
    SELECT COUNT(*) FROM (
        SELECT ts_utc, token_mint FROM confluence_events
        GROUP BY ts_utc, token_mint HAVING COUNT(id) > 1
    )
""").fetchone()[0]
total_before = conn.execute("SELECT COUNT(*) FROM confluence_events").fetchone()[0]
print(f"Step B1: Before cleanup — total={total_before}, duplicate groups={dup_count_before}")

# B2: Delete duplicate rows — keep the lowest id per (token_mint, ts_utc) group
deleted = conn.execute("""
    DELETE FROM confluence_events
    WHERE id NOT IN (
        SELECT MIN(id) FROM confluence_events
        GROUP BY token_mint, ts_utc
    )
""").rowcount
conn.commit()
print(f"Step B2: Deleted {deleted} duplicate rows (kept lowest id per group)")

# Verify
total_after  = conn.execute("SELECT COUNT(*) FROM confluence_events").fetchone()[0]
dup_after    = conn.execute("""
    SELECT COUNT(*) FROM (
        SELECT ts_utc, token_mint FROM confluence_events
        GROUP BY ts_utc, token_mint HAVING COUNT(id) > 1
    )
""").fetchone()[0]
print(f"  After cleanup — total={total_after}, duplicate groups={dup_after}")
assert dup_after == 0, "Duplicate groups remain after cleanup!"

# B3: Add UNIQUE INDEX on (token_mint, ts_utc) — DB-level safety net
conn.execute("""
    CREATE UNIQUE INDEX IF NOT EXISTS uq_conf_mint_ts
    ON confluence_events (token_mint, ts_utc)
""")
conn.commit()
print("Step B3: UNIQUE INDEX uq_conf_mint_ts created on (token_mint, ts_utc)")

conn.close()

print("\nPatch 187 applied successfully.")
print(f"  Deleted {deleted} duplicate rows from confluence_events.")
print("  mints_inserted_this_run set prevents intra-loop duplicates in _detect_confluences.")
print("  INSERT OR IGNORE + UNIQUE INDEX (token_mint, ts_utc) as DB-level safety net.")
print("  4h mint dedup (P181) retained for cross-run dedup.")
print("  No frontend changes: confluence table counts will reflect clean data automatically.")
