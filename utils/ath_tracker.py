"""
utils/ath_tracker.py — All-Time High price tracker

Tracks the highest price ever seen per token across all scans.
Used to implement the "second leg" strategy:
  - Ape AFTER 80-90% drawdown from ATH
  - CT thinks it's dead = your entry point
  - Community still alive + dev shipping = conviction

Data stored in engine.db `token_ath` table.
Updated on every scan automatically.
"""

from __future__ import annotations

import logging
from typing import Optional

log = logging.getLogger(__name__)

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS token_ath (
    mint            TEXT PRIMARY KEY,
    symbol          TEXT,
    ath_price       REAL NOT NULL,
    ath_ts_utc      TEXT NOT NULL,
    last_price      REAL,
    last_seen_utc   TEXT,
    pct_from_ath    REAL,
    leg             TEXT   -- 'FIRST_LEG' | 'DRAWDOWN' | 'SECOND_LEG' | 'THIRD_LEG'
);
"""

# Drawdown thresholds for leg classification
_SECOND_LEG_MIN_DRAWDOWN  = 0.75   # >= 75% down from ATH = potential second leg entry
_SECOND_LEG_IDEAL_DRAWDOWN = 0.85  # >= 85% = ideal zone (the "80-90%" in the framework)
_THIRD_LEG_THRESHOLD      = 0.50   # < 50% from ATH = third leg territory


def _ensure_table(conn) -> None:
    conn.execute(_CREATE_TABLE)


def update_ath(mint: str, symbol: str, current_price: float, ts_utc: str) -> dict:
    """
    Update ATH record for a token. Called every scan cycle.

    Returns dict with:
        ath_price       float
        pct_from_ath    float   0.0 = at ATH, 0.85 = 85% below ATH
        drawdown_pct    float   human-readable: 85.0 means "85% down"
        leg             str     FIRST_LEG | DRAWDOWN | SECOND_LEG | THIRD_LEG
        is_second_leg   bool    True if in the ideal entry zone
    """
    if not mint or not current_price or current_price <= 0:
        return _empty_ath()

    try:
        from utils.db import get_conn
        with get_conn() as conn:
            _ensure_table(conn)

            row = conn.execute(
                "SELECT ath_price, ath_ts_utc FROM token_ath WHERE mint = ?", (mint,)
            ).fetchone()

            if row:
                ath_price, ath_ts = row[0], row[1]
                if current_price > ath_price:
                    # New ATH
                    ath_price = current_price
                    ath_ts    = ts_utc
                    conn.execute(
                        """UPDATE token_ath SET ath_price=?, ath_ts_utc=?, symbol=?,
                           last_price=?, last_seen_utc=?, pct_from_ath=?, leg=?
                           WHERE mint=?""",
                        (ath_price, ath_ts, symbol, current_price, ts_utc,
                         0.0, "FIRST_LEG", mint)
                    )
                else:
                    pct_from_ath = (ath_price - current_price) / ath_price
                    leg = _classify_leg(pct_from_ath)
                    conn.execute(
                        """UPDATE token_ath SET symbol=?, last_price=?, last_seen_utc=?,
                           pct_from_ath=?, leg=? WHERE mint=?""",
                        (symbol, current_price, ts_utc, pct_from_ath, leg, mint)
                    )
            else:
                # First time seeing this token — assume current price is ATH
                conn.execute(
                    """INSERT INTO token_ath
                       (mint, symbol, ath_price, ath_ts_utc, last_price, last_seen_utc,
                        pct_from_ath, leg)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (mint, symbol, current_price, ts_utc, current_price, ts_utc,
                     0.0, "FIRST_LEG")
                )
                ath_price    = current_price
                pct_from_ath = 0.0

            pct_from_ath = (ath_price - current_price) / ath_price if ath_price > 0 else 0.0
            leg = _classify_leg(pct_from_ath)

            return {
                "ath_price":     ath_price,
                "pct_from_ath":  pct_from_ath,
                "drawdown_pct":  round(pct_from_ath * 100, 1),
                "leg":           leg,
                "is_second_leg": leg == "SECOND_LEG",
            }

    except Exception as exc:
        log.debug("ATH tracker error for %s: %s", mint, exc)
        return _empty_ath()


def get_ath(mint: str) -> dict:
    """Read current ATH record for a token without updating."""
    if not mint:
        return _empty_ath()
    try:
        from utils.db import get_conn
        with get_conn() as conn:
            _ensure_table(conn)
            row = conn.execute(
                """SELECT ath_price, last_price, pct_from_ath, leg, ath_ts_utc
                   FROM token_ath WHERE mint=?""", (mint,)
            ).fetchone()
            if not row:
                return _empty_ath()
            ath_price, last_price, pct_from_ath, leg, ath_ts = row
            return {
                "ath_price":     ath_price,
                "pct_from_ath":  pct_from_ath or 0.0,
                "drawdown_pct":  round((pct_from_ath or 0.0) * 100, 1),
                "leg":           leg or "UNKNOWN",
                "is_second_leg": (leg or "") == "SECOND_LEG",
                "ath_ts_utc":    ath_ts,
            }
    except Exception as exc:
        log.debug("ATH get error for %s: %s", mint, exc)
        return _empty_ath()


def get_second_leg_candidates(min_drawdown_pct: float = 75.0, limit: int = 50) -> list[dict]:
    """
    Return tokens that are currently in second-leg territory.
    min_drawdown_pct: minimum % below ATH (default 75%)
    """
    try:
        from utils.db import get_conn
        with get_conn() as conn:
            _ensure_table(conn)
            min_pct = min_drawdown_pct / 100.0
            rows = conn.execute(
                """SELECT mint, symbol, ath_price, last_price, pct_from_ath, leg,
                          ath_ts_utc, last_seen_utc
                   FROM token_ath
                   WHERE pct_from_ath >= ? AND leg = 'SECOND_LEG'
                   ORDER BY pct_from_ath DESC
                   LIMIT ?""",
                (min_pct, limit)
            ).fetchall()
            return [
                {
                    "mint":         r[0],
                    "symbol":       r[1],
                    "ath_price":    r[2],
                    "last_price":   r[3],
                    "drawdown_pct": round(r[4] * 100, 1),
                    "leg":          r[5],
                    "ath_ts_utc":   r[6],
                    "last_seen_utc": r[7],
                }
                for r in rows
            ]
    except Exception as exc:
        log.debug("Second leg candidates error: %s", exc)
        return []


def _classify_leg(pct_from_ath: float) -> str:
    """
    Classify which leg a token is in based on drawdown from ATH.

    FIRST_LEG  — at or near ATH (< 30% down), first pump
    DRAWDOWN   — 30-74% down, still distributing, too early
    SECOND_LEG — 75-95% down, CT thinks it's dead, ideal entry zone
    THIRD_LEG  — < 10% from ATH again, already recovered, whale territory
    """
    if pct_from_ath < 0.10:
        return "FIRST_LEG"    # At or near ATH — first leg or recovered
    elif pct_from_ath < 0.30:
        return "FIRST_LEG"    # Still high — first leg consolidation
    elif pct_from_ath < 0.75:
        return "DRAWDOWN"     # Distributing, early whales still exiting
    elif pct_from_ath <= 0.95:
        return "SECOND_LEG"   # The zone — CT gave up, early whales gone
    else:
        return "DRAWDOWN"     # >95% down = probably dead, not a recovery


def _empty_ath() -> dict:
    return {
        "ath_price":     0.0,
        "pct_from_ath":  0.0,
        "drawdown_pct":  0.0,
        "leg":           "UNKNOWN",
        "is_second_leg": False,
    }
