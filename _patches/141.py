"""
Patch 141 — Whale Watch: mc_tier column + cross_agent_signals table

Changes:
  1. Add `mc_tier` column to whale_watch_alerts (micro/sweet_spot/mid/large)
  2. Retroactively compute mc_tier for existing rows from market_cap_usd
  3. Create cross_agent_signals table — shared signal bus between agents
"""
import os
import sqlite3
import sys

root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
db_path = os.path.join(root, "data_storage", "engine.db")
print(f"DB: {db_path}")

conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row

# ── 1. Add mc_tier column if not exists ───────────────────────────────────────
existing_cols = [r[1] for r in conn.execute("PRAGMA table_info(whale_watch_alerts)").fetchall()]
if "mc_tier" not in existing_cols:
    conn.execute("ALTER TABLE whale_watch_alerts ADD COLUMN mc_tier TEXT")
    print("✓ Added mc_tier column to whale_watch_alerts")
else:
    print("  mc_tier column already exists — skipping")

# ── 2. Retroactively compute mc_tier from market_cap_usd ──────────────────────
conn.execute("""
    UPDATE whale_watch_alerts
    SET mc_tier = CASE
        WHEN market_cap_usd IS NULL     THEN 'unknown'
        WHEN market_cap_usd < 5000000   THEN 'micro'
        WHEN market_cap_usd < 50000000  THEN 'sweet_spot'
        WHEN market_cap_usd < 200000000 THEN 'mid'
        ELSE 'large'
    END
    WHERE mc_tier IS NULL
""")
updated = conn.total_changes
print(f"✓ Updated {updated} rows with mc_tier")

# ── 3. Create cross_agent_signals table ───────────────────────────────────────
conn.execute("""
    CREATE TABLE IF NOT EXISTS cross_agent_signals (
        id                INTEGER PRIMARY KEY AUTOINCREMENT,
        ts_utc            TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
        source            TEXT NOT NULL,          -- 'whale_watch'
        target            TEXT NOT NULL,          -- 'memecoin_scanner' | 'spot_accumulator' | 'observation'
        signal_type       TEXT NOT NULL,          -- 'WHALE_CONFIRM' | 'WHALE_FLOW'
        token_symbol      TEXT,
        token_mint        TEXT,
        mc_tier           TEXT,                   -- 'micro' | 'sweet_spot' | 'mid' | 'large'
        buy_amount_usd    REAL,
        market_cap_usd    REAL,
        scanner_score     REAL,
        scanner_rug_label TEXT,
        expires_ts        TEXT,                   -- 2h window — when signal expires
        consumed          INTEGER DEFAULT 0,      -- 1 when used by target agent
        consumed_ts       TEXT,
        ref_alert_id      INTEGER                 -- FK → whale_watch_alerts.id
    )
""")
print("✓ cross_agent_signals table ready")

conn.commit()
conn.close()
print("\nPatch 141 complete.")
