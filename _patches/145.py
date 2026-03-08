"""
Patch 145 — Smart Wallet Tracker: DB migration

Creates three tables:
  smart_wallets            — tracked wallet roster
  smart_wallet_buys        — individual buy events with outcome tracking
  smart_wallet_accumulations — multi-wallet accumulation signals

Run: python3 _patches/145.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.db import get_conn  # type: ignore

DDL = """
CREATE TABLE IF NOT EXISTS smart_wallets (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    address         TEXT NOT NULL UNIQUE,
    label           TEXT NOT NULL DEFAULT 'Unknown',
    added_ts        TEXT NOT NULL,
    active          INTEGER NOT NULL DEFAULT 1,
    total_buys      INTEGER NOT NULL DEFAULT 0,
    last_checked_ts REAL,
    notes           TEXT
);

CREATE TABLE IF NOT EXISTS smart_wallet_buys (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_utc           TEXT NOT NULL,
    wallet_address   TEXT NOT NULL,
    wallet_label     TEXT,
    tx_signature     TEXT UNIQUE NOT NULL,
    token_symbol     TEXT,
    token_mint       TEXT NOT NULL,
    buy_amount_sol   REAL,
    buy_amount_usd   REAL,
    market_cap_usd   REAL,
    price_at_buy     REAL,
    dex_source       TEXT,
    price_1h         REAL,
    return_1h_pct    REAL,
    evaluated_1h_ts  TEXT,
    price_4h         REAL,
    return_4h_pct    REAL,
    evaluated_4h_ts  TEXT,
    price_24h        REAL,
    return_24h_pct   REAL,
    evaluated_24h_ts TEXT,
    outcome_status   TEXT NOT NULL DEFAULT 'PENDING'
);
CREATE INDEX IF NOT EXISTS idx_swb_mint   ON smart_wallet_buys(token_mint);
CREATE INDEX IF NOT EXISTS idx_swb_ts     ON smart_wallet_buys(ts_utc);
CREATE INDEX IF NOT EXISTS idx_swb_wallet ON smart_wallet_buys(wallet_address);

CREATE TABLE IF NOT EXISTS smart_wallet_accumulations (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_utc         TEXT NOT NULL,
    token_mint     TEXT NOT NULL,
    token_symbol   TEXT,
    wallet_count   INTEGER NOT NULL,
    wallet_labels  TEXT NOT NULL,
    total_sol      REAL,
    market_cap_usd REAL,
    alert_sent     INTEGER NOT NULL DEFAULT 0,
    return_1h_pct  REAL,
    return_4h_pct  REAL,
    return_24h_pct REAL,
    outcome_status TEXT NOT NULL DEFAULT 'PENDING'
);
CREATE INDEX IF NOT EXISTS idx_swa_mint ON smart_wallet_accumulations(token_mint);
"""


def run():
    with get_conn() as conn:
        conn.executescript(DDL)
    print("[145] Tables created (or already exist).")

    with get_conn() as conn:
        tables = [
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'smart_%'"
            ).fetchall()
        ]
        for tbl in tables:
            cols = [r[1] for r in conn.execute(f"PRAGMA table_info({tbl})").fetchall()]
            print(f"[145] {tbl}: {len(cols)} columns — {', '.join(cols)}")

    print("[145] Patch 145 complete.")


if __name__ == "__main__":
    run()
