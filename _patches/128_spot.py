"""
Patch 128 — Spot Accumulation DB migration.

Creates two tables:
  spot_holdings — one row per basket token, updated in-place on each buy
  spot_buys     — immutable audit log of every buy/sell transaction
"""
import sqlite3
import sys
from pathlib import Path

ENGINE_ROOT = Path(__file__).resolve().parents[1]
DB_PATH     = ENGINE_ROOT / "data_storage" / "engine.db"

def run():
    print(f"DB: {DB_PATH}")
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    conn.executescript("""
        CREATE TABLE IF NOT EXISTS spot_holdings (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol          TEXT NOT NULL UNIQUE,
            mint            TEXT NOT NULL,
            token_amount    REAL NOT NULL DEFAULT 0.0,
            total_invested  REAL NOT NULL DEFAULT 0.0,
            avg_cost_usd    REAL NOT NULL DEFAULT 0.0,
            last_buy_ts     TEXT,
            created_ts      TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS spot_buys (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            ts_utc          TEXT NOT NULL,
            symbol          TEXT NOT NULL,
            mint            TEXT NOT NULL,
            side            TEXT NOT NULL DEFAULT 'BUY',
            amount_usd      REAL NOT NULL,
            token_amount    REAL NOT NULL,
            price_usd       REAL NOT NULL,
            tx_sig          TEXT,
            dry_run         INTEGER NOT NULL DEFAULT 1
        );
    """)
    conn.commit()

    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()]
    print("Tables now in DB:", tables)

    for tbl in ("spot_holdings", "spot_buys"):
        if tbl in tables:
            print(f"  ✓ {tbl}")
        else:
            print(f"  ✗ {tbl} MISSING — something went wrong")
            sys.exit(1)

    conn.close()
    print("Patch 128 migration complete.")

if __name__ == "__main__":
    run()
