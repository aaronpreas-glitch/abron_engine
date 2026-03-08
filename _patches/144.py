"""
Patch 144 — Funding Rate Monitor: DB migration

Creates the funding_snapshots table for storing periodic Binance funding rate data
for SOL, BTC, ETH perpetual futures.

Run: python3 _patches/144.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.db import get_conn  # type: ignore

DDL = """
CREATE TABLE IF NOT EXISTS funding_snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_utc          TEXT    NOT NULL,
    symbol          TEXT    NOT NULL,
    rate            REAL,
    mark_price      REAL,
    next_funding_ts INTEGER
);
CREATE INDEX IF NOT EXISTS idx_fs_symbol_ts ON funding_snapshots(symbol, ts_utc);
"""


def run():
    with get_conn() as conn:
        conn.executescript(DDL)
    print("[144] funding_snapshots table created (or already exists).")

    with get_conn() as conn:
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='funding_snapshots'"
        ).fetchall()]
        cols = [r[1] for r in conn.execute("PRAGMA table_info(funding_snapshots)").fetchall()]

    print(f"[144] Tables found: {tables}")
    print(f"[144] Columns ({len(cols)}): {', '.join(cols)}")
    print("[144] Patch 144 complete.")


if __name__ == "__main__":
    run()
