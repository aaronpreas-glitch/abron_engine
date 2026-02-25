"""
outcome_tracker.py — Standalone async service that evaluates pending alert_outcomes.

Runs as a background asyncio task inside the FastAPI dashboard process.
Every POLL_INTERVAL_SECONDS it:
  1. Fetches all PENDING alert_outcomes rows
  2. For each row, checks which horizons (1h / 4h / 24h) are now due
  3. Fetches the current price from CoinGecko (by symbol or mint)
  4. Computes return_pct = (current - entry) / entry * 100
  5. Writes the result back via the RW connection

Price sources (tried in order, most reliable first):
  - CoinGecko /simple/price by symbol  (BTC/ETH/SOL direct)
  - CoinGecko /coins/{id}/contract/{mint}  (by contract address)
  - Jupiter quote API (SOL-ecosystem fallback)

Designed to be API-key-free (CoinGecko free tier) with conservative rate limiting.
"""
from __future__ import annotations

import asyncio
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import httpx

log = logging.getLogger("outcome_tracker")

# ── Config ────────────────────────────────────────────────────────────────────

_DB_PATH = Path(__file__).resolve().parents[2] / "data_storage" / "engine.db"
POLL_INTERVAL   = 300        # seconds between evaluation passes
BATCH_SIZE      = 50         # max rows per pass
REQUEST_TIMEOUT = 12         # seconds per HTTP call
RATE_LIMIT_SLEEP = 2.5       # seconds between CoinGecko calls
MAX_OUTCOME_AGE  = timedelta(hours=36)  # abandon rows older than this

# Known CoinGecko IDs for major coins so we skip contract lookup
_SYMBOL_TO_CG_ID: dict[str, str] = {
    "BTC":      "bitcoin",
    "ETH":      "ethereum",
    "SOL":      "solana",
    "BNB":      "binancecoin",
    "USDC":     "usd-coin",
    "USDT":     "tether",
    "WIF":      "dogwifcoin",
    "BONK":     "bonk",
    "POPCAT":   "popcat",
    "FARTCOIN": "fartcoin",
    "MOODENG":  "moo-deng",
    "GOAT":     "goatseus-maximus",
    "AI16Z":    "ai16z",
    "ZEREBRO":  "zerebro",
    "TRUMP":    "official-trump",
    "MELANIA":  "melania-meme",
}

# ── DB helpers ────────────────────────────────────────────────────────────────

@contextmanager
def _rw_conn():
    conn = sqlite3.connect(str(_DB_PATH), timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _get_pending(limit: int = BATCH_SIZE) -> list[dict]:
    with _rw_conn() as conn:
        rows = conn.execute(
            """
            SELECT id, created_ts_utc, symbol, mint, entry_price,
                   score, regime_score, regime_label, confidence,
                   return_1h_pct, return_4h_pct, return_24h_pct,
                   evaluated_1h_ts_utc, evaluated_4h_ts_utc, evaluated_24h_ts_utc,
                   last_error, status
            FROM alert_outcomes
            WHERE status != 'COMPLETE'
            ORDER BY created_ts_utc ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def _write_horizon(outcome_id: int, horizon_h: int, return_pct: float):
    now = datetime.now(timezone.utc).isoformat()
    col_map = {
        1:  ("evaluated_1h_ts_utc",  "return_1h_pct"),
        4:  ("evaluated_4h_ts_utc",  "return_4h_pct"),
        24: ("evaluated_24h_ts_utc", "return_24h_pct"),
    }
    ts_col, ret_col = col_map[horizon_h]
    with _rw_conn() as conn:
        conn.execute(
            f"UPDATE alert_outcomes SET {ts_col}=?, {ret_col}=?, last_error=NULL WHERE id=?",
            (now, round(return_pct, 4), outcome_id),
        )
        # Mark COMPLETE when all three horizons filled
        conn.execute(
            """
            UPDATE alert_outcomes SET status='COMPLETE'
            WHERE id=?
              AND return_1h_pct IS NOT NULL
              AND return_4h_pct IS NOT NULL
              AND return_24h_pct IS NOT NULL
            """,
            (outcome_id,),
        )


def _write_error(outcome_id: int, error: str):
    with _rw_conn() as conn:
        conn.execute(
            "UPDATE alert_outcomes SET last_error=? WHERE id=?",
            (error[:300], outcome_id),
        )


def _mark_abandoned(outcome_id: int):
    """Fill missing horizons with None and mark COMPLETE to stop retrying."""
    with _rw_conn() as conn:
        conn.execute(
            """
            UPDATE alert_outcomes
            SET status='COMPLETE', last_error='abandoned_too_old'
            WHERE id=?
            """,
            (outcome_id,),
        )


# ── Price fetching ─────────────────────────────────────────────────────────────

async def _cg_price_by_id(client: httpx.AsyncClient, cg_id: str) -> Optional[float]:
    """Fetch USD price from CoinGecko by known coin ID."""
    try:
        r = await client.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids": cg_id, "vs_currencies": "usd"},
            timeout=REQUEST_TIMEOUT,
        )
        data = r.json()
        price = data.get(cg_id, {}).get("usd")
        return float(price) if price else None
    except Exception as e:
        log.debug("CG price_by_id(%s) failed: %s", cg_id, e)
        return None


async def _cg_price_by_contract(client: httpx.AsyncClient, mint: str) -> Optional[float]:
    """Fetch USD price from CoinGecko by Solana contract address."""
    try:
        r = await client.get(
            f"https://api.coingecko.com/api/v3/coins/solana/contract/{mint}",
            timeout=REQUEST_TIMEOUT,
        )
        if r.status_code != 200:
            return None
        data = r.json()
        price = data.get("market_data", {}).get("current_price", {}).get("usd")
        return float(price) if price else None
    except Exception as e:
        log.debug("CG contract(%s) failed: %s", mint[:12], e)
        return None


async def _jupiter_price(client: httpx.AsyncClient, mint: str) -> Optional[float]:
    """Jupiter Price API v2 — reliable for Solana ecosystem tokens."""
    try:
        r = await client.get(
            "https://api.jup.ag/price/v2",
            params={"ids": mint},
            timeout=REQUEST_TIMEOUT,
        )
        data = r.json()
        price_str = data.get("data", {}).get(mint, {}).get("price")
        return float(price_str) if price_str else None
    except Exception as e:
        log.debug("Jupiter price(%s) failed: %s", mint[:12], e)
        return None


async def fetch_current_price(
    client: httpx.AsyncClient,
    symbol: str,
    mint: Optional[str],
) -> Optional[float]:
    """
    Try price sources in priority order:
      1. CoinGecko by known ID (fast, reliable for major coins)
      2. Jupiter by mint (best for Solana memecoins)
      3. CoinGecko by contract (slowest, most complete)
    """
    sym = symbol.upper().strip()

    # Source 1: known CoinGecko ID
    cg_id = _SYMBOL_TO_CG_ID.get(sym)
    if cg_id:
        price = await _cg_price_by_id(client, cg_id)
        if price and price > 0:
            return price

    # Source 2: Jupiter (mint address — best for long-tail Solana tokens)
    if mint:
        price = await _jupiter_price(client, mint)
        if price and price > 0:
            return price
        await asyncio.sleep(0.5)

        # Source 3: CoinGecko contract (rate-limited, use as last resort)
        price = await _cg_price_by_contract(client, mint)
        if price and price > 0:
            return price

    return None


# ── Main evaluation loop ──────────────────────────────────────────────────────

async def run_evaluation_pass():
    """Single evaluation pass — fetch pending rows and fill due horizons."""
    rows = _get_pending(BATCH_SIZE)
    if not rows:
        return

    now = datetime.now(timezone.utc)
    processed = 0

    async with httpx.AsyncClient(
        headers={"User-Agent": "AbronsDashboard/1.0"},
        follow_redirects=True,
    ) as client:
        for row in rows:
            outcome_id = int(row["id"])

            # Parse creation time
            try:
                created_str = row["created_ts_utc"]
                # Handle both naive and aware datetimes
                created = datetime.fromisoformat(created_str)
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
            except (TypeError, ValueError):
                _write_error(outcome_id, "bad_created_ts")
                continue

            age = now - created

            # Abandon very old rows (token price no longer meaningful)
            if age > MAX_OUTCOME_AGE:
                _mark_abandoned(outcome_id)
                log.debug("Abandoned old outcome id=%d (%s)", outcome_id, row["symbol"])
                continue

            entry_price = float(row.get("entry_price") or 0)
            if entry_price <= 0:
                _mark_abandoned(outcome_id)
                continue

            # Which horizons are now due and not yet filled?
            due = []
            if row["return_1h_pct"] is None and age >= timedelta(hours=1, minutes=5):
                due.append(1)
            if row["return_4h_pct"] is None and age >= timedelta(hours=4, minutes=5):
                due.append(4)
            if row["return_24h_pct"] is None and age >= timedelta(hours=24, minutes=5):
                due.append(24)

            if not due:
                continue

            # Fetch current price
            symbol = str(row.get("symbol") or "").upper()
            mint   = row.get("mint")

            current_price = await fetch_current_price(client, symbol, mint)

            if current_price is None or current_price <= 0:
                _write_error(outcome_id, "price_unavailable")
                log.debug("No price for %s (id=%d)", symbol, outcome_id)
                await asyncio.sleep(RATE_LIMIT_SLEEP)
                continue

            ret_pct = ((current_price - entry_price) / entry_price) * 100.0

            for h in due:
                _write_horizon(outcome_id, h, ret_pct)
                log.info(
                    "Outcome id=%d %s [%dh]: entry=%.6f current=%.6f ret=%.2f%%",
                    outcome_id, symbol, h, entry_price, current_price, ret_pct,
                )

            processed += 1
            # Respect CoinGecko rate limits (50 req/min on free tier)
            await asyncio.sleep(RATE_LIMIT_SLEEP)

    if processed:
        log.info("Outcome tracker: filled %d horizon(s) this pass", processed)


async def outcome_tracker_loop():
    """Long-running background task. Runs forever inside the FastAPI process."""
    log.info("Outcome tracker started (poll every %ds)", POLL_INTERVAL)
    # Small boot delay so the API is ready before first pass
    await asyncio.sleep(15)

    while True:
        try:
            await run_evaluation_pass()
        except Exception as exc:
            log.error("Outcome tracker pass failed: %s", exc, exc_info=True)

        await asyncio.sleep(POLL_INTERVAL)
