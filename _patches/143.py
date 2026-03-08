"""
Patch 143 — Confluence Engine: DB migration

Creates the confluence_events table for cross-system signal detection.
The Confluence Engine detects when Whale Watch (scanner_pass=1) and
Memecoin Scanner (rug_label='GOOD') agree on the same token_mint within 48h.

Run: python3 _patches/143.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.db import get_conn  # type: ignore

DDL = """
CREATE TABLE IF NOT EXISTS confluence_events (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_utc           TEXT    NOT NULL,
    confluence_type  TEXT    NOT NULL DEFAULT 'WHALE+MEME',
    token_symbol     TEXT    NOT NULL,
    token_mint       TEXT    NOT NULL,
    source_count     INTEGER NOT NULL DEFAULT 2,
    sources          TEXT    NOT NULL,
    whale_alert_id   INTEGER,
    memecoin_scan_id INTEGER,
    whale_score      REAL,
    memecoin_score   REAL,
    confluence_score REAL,
    market_cap_usd   REAL,
    price_at_event   REAL,
    alert_sent       INTEGER NOT NULL DEFAULT 0,
    price_1h         REAL,
    return_1h_pct    REAL,
    price_4h         REAL,
    return_4h_pct    REAL,
    price_24h        REAL,
    return_24h_pct   REAL,
    outcome_status   TEXT    NOT NULL DEFAULT 'PENDING'
);
CREATE INDEX IF NOT EXISTS idx_conf_mint ON confluence_events(token_mint);
CREATE INDEX IF NOT EXISTS idx_conf_ts   ON confluence_events(ts_utc);
"""


def run():
    with get_conn() as conn:
        conn.executescript(DDL)
    print("[143] confluence_events table created (or already exists).")

    # Verify
    with get_conn() as conn:
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='confluence_events'"
        ).fetchall()]
        cols = [r[1] for r in conn.execute("PRAGMA table_info(confluence_events)").fetchall()]

    print(f"[143] Tables found: {tables}")
    print(f"[143] Columns ({len(cols)}): {', '.join(cols)}")
    print("[143] Patch 143 complete.")


if __name__ == "__main__":
    run()
