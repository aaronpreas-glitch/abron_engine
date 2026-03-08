"""
Spot Accumulation — Patch 128.

Semi-manual DCA accumulation of established Solana ecosystem tokens.
Basket: WIF, BONK, POPCAT, JUP, RAY, ORCA, PYTH.

Env vars:
  SPOT_DRY_RUN=true   — paper mode (default). Set false to execute real swaps.

Key functions:
  fetch_basket_prices()        — batch price fetch via DexScreener
  get_portfolio_state()        — holdings + live prices + PnL
  get_allocation_advice(n)     — allocate $n by underweight gap
  buy_spot(symbol, mint, usd)  — buy (paper or live via jupiter_swap)
  sell_spot(symbol, mint, pct) — sell partial or full position
  spot_monitor_step()          — cache prices, heartbeat agent
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime, timezone

import requests

log = logging.getLogger("spot_accumulator")

REQUEST_TIMEOUT  = 8
DEXSCREENER_BASE = "https://api.dexscreener.com"
KV_KEY_PRICES    = "spot_prices"
KV_KEY_ENRICHED  = "spot_enriched"   # Patch 130 — cached enriched price+trend data
CACHE_TTL_S      = 300               # 5 min — refresh interval matching spot_monitor_step

# ── Curated basket ────────────────────────────────────────────────────────────

BASKET: list[dict] = [
    # ── Original 7 (Patch 128) ────────────────────────────────────────────────
    {"symbol": "JUP",    "name": "Jupiter",          "mint": "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN",  "target_pct": 15.0},
    {"symbol": "WIF",    "name": "dogwifhat",         "mint": "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm", "target_pct": 13.0},
    {"symbol": "RAY",    "name": "Raydium",           "mint": "4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R", "target_pct": 11.0},
    {"symbol": "BONK",   "name": "Bonk",              "mint": "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263", "target_pct": 10.0},
    {"symbol": "ORCA",   "name": "Orca",              "mint": "orcaEKTdK7LKz57vaAYr9QeNsVEPfiu6QeMU1kektZE",  "target_pct":  8.0},
    {"symbol": "POPCAT", "name": "Popcat",            "mint": "7GCihgDB8fe6KNjn2MYtkzZcRjQy3t9GHdC8uHYmW2hr", "target_pct":  5.0},
    {"symbol": "PYTH",   "name": "Pyth Network",      "mint": "HZ1JovNiVvGrGNiiYvEozEVgZ58xaU3RKwX8eACQBCt3", "target_pct":  4.0},
    # ── Expanded basket (Patch 135) ───────────────────────────────────────────
    {"symbol": "JTO",    "name": "Jito",              "mint": "jtojtomepa8beP8AuQc6eXt5FriJwfFMwQx2v2f9mCL",  "target_pct": 12.0},
    {"symbol": "PENGU",  "name": "Pudgy Penguins",    "mint": "2zMMhcVQEXDtdE6vsFS7S7D5oUodfJHE8vd1gnBouauv", "target_pct": 10.0},
    {"symbol": "MEW",    "name": "cat in a dogs world","mint": "MEW1gQWJ3nEXg2qgERiKu7FAFj79PHvQVREQUzScPP5",  "target_pct":  7.0},
    {"symbol": "W",      "name": "Wormhole",          "mint": "85VBFQZC9TZkfaptBWjvUw7YbZjy52A6mjtPGjstQAmQ", "target_pct":  5.0},
]

_BASKET_BY_SYMBOL: dict[str, dict] = {b["symbol"]: b for b in BASKET}
_BASKET_BY_MINT:   dict[str, dict] = {b["mint"]:   b for b in BASKET}

# SPL token decimal places — used to convert raw on-chain units → human amounts (Patch 150)
# Default 6; overrides listed for non-standard tokens.
_TOKEN_DECIMALS: dict[str, int] = {
    "JUP":    6,
    "WIF":    6,
    "RAY":    6,
    "ORCA":   6,
    "PYTH":   6,
    "PENGU":  6,
    "MEW":    6,
    "W":      6,
    "BONK":   5,   # BONK uses 5 decimals
    "POPCAT": 9,   # POPCAT uses 9 decimals
    "JTO":    9,   # JTO uses 9 decimals
}

# CoinGecko IDs — used as fallback when DexScreener is rate-limited (Patch 130)
_COINGECKO_IDS: dict[str, str] = {
    # Original 7
    "WIF":    "dogwifcoin",
    "BONK":   "bonk",
    "POPCAT": "popcat",
    "JUP":    "jupiter-exchange-solana",
    "RAY":    "raydium",
    "ORCA":   "orca",
    "PYTH":   "pyth-network",
    # Patch 135 additions
    "JTO":    "jito-governance-token",
    "PENGU":  "pudgy-penguins",
    "MEW":    "cat-in-a-dogs-world",
    "W":      "wormhole",
}


# ── DB helpers ────────────────────────────────────────────────────────────────

def _get_conn():
    from utils.db import get_conn  # type: ignore
    return get_conn()


# ── Price fetching ────────────────────────────────────────────────────────────

def _fetch_basket_enriched() -> dict[str, dict]:
    """
    Fetch current USD prices + 24h/6h price changes for all basket tokens.
    Returns {symbol: {price, h24, h6}} — h24/h6 are pct changes (float or None).
    Uses DexScreener batch API (same call as price-only fetch — no extra requests).
    Patch 130: internal function; fetch_basket_prices() wraps this for backward compat.
    """
    result: dict[str, dict] = {
        b["symbol"]: {"price": 0.0, "h24": None, "h6": None} for b in BASKET
    }
    mints = [b["mint"] for b in BASKET]
    batch_str = ",".join(mints)
    try:
        r = requests.get(
            f"{DEXSCREENER_BASE}/latest/dex/tokens/{batch_str}",
            timeout=REQUEST_TIMEOUT,
            headers={"User-Agent": "memecoin-engine/1.0"},
        )
        if r.status_code != 200:
            log.warning("DexScreener batch price HTTP %s", r.status_code)
            pairs = []  # Don't try to parse non-200 body; CoinGecko fallback below
        else:
            pairs = r.json().get("pairs") or []
        # Build mint → best data map (highest liquidity pair wins)
        mint_data: dict[str, dict] = {}  # mint → {price, liq, h24, h6}
        for pair in pairs:
            if pair.get("chainId") != "solana":
                continue
            mint  = pair.get("baseToken", {}).get("address", "")
            price = float(pair.get("priceUsd", 0) or 0)
            liq   = float(pair.get("liquidity", {}).get("usd", 0) or 0)
            if mint and price > 0:
                existing = mint_data.get(mint)
                if existing is None or liq > existing["liq"]:
                    pc = pair.get("priceChange") or {}
                    mint_data[mint] = {
                        "price": price,
                        "liq":   liq,
                        "h24":   float(pc["h24"]) if pc.get("h24") is not None else None,
                        "h6":    float(pc["h6"])  if pc.get("h6")  is not None else None,
                    }
        # Map back to symbols
        for token in BASKET:
            d = mint_data.get(token["mint"])
            if d:
                result[token["symbol"]] = {
                    "price": d["price"],
                    "h24":   d["h24"],
                    "h6":    d["h6"],
                }
    except Exception as exc:
        log.warning("_fetch_basket_enriched error: %s", exc)

    # Patch 130: fallback to CoinGecko for any token missing a price (full or partial gap).
    # Originally triggered only on all-zeros; now also fills individual tokens DexScreener
    # missed — important for newer/lower-liquidity tokens like JTO and W. Patch 135.
    missing = [sym for sym, d in result.items() if d["price"] == 0.0]
    if missing:
        cg = _fetch_basket_from_coingecko()
        for sym in missing:
            if cg.get(sym, {}).get("price", 0.0) > 0:
                result[sym] = cg[sym]

    # Patch 136: individual DexScreener token endpoint for any token with a price
    # but missing h6 (e.g. JTO/W when batch returns no h6 for them). Tries each
    # missing-h6 token individually via /dex/tokens/{mint}. Silent on failure.
    h6_missing = [
        b for b in BASKET
        if result.get(b["symbol"], {}).get("price", 0.0) > 0
        and result.get(b["symbol"], {}).get("h6") is None
    ]
    for tok in h6_missing:
        try:
            import requests as _req
            r = _req.get(
                f"https://api.dexscreener.com/latest/dex/tokens/{tok['mint']}",
                timeout=5,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            pairs = (r.json().get("pairs") or []) if r.status_code == 200 else []
            if pairs and pairs[0].get("priceChange", {}).get("h6") is not None:
                result[tok["symbol"]]["h6"] = float(pairs[0]["priceChange"]["h6"])
                log.debug("spot h6 fill: %s h6=%.2f%%", tok["symbol"], result[tok["symbol"]]["h6"])
        except Exception:
            pass

    return result


def _fetch_basket_from_coingecko() -> dict[str, dict]:
    """
    Fallback price source when DexScreener is rate-limited.
    Uses CoinGecko simple/price endpoint — provides price + h24 (no h6).
    Trend will show NEUTRAL for all tokens (h6 unavailable), but prices display correctly.
    Patch 130.
    """
    result: dict[str, dict] = {
        b["symbol"]: {"price": 0.0, "h24": None, "h6": None} for b in BASKET
    }
    cg_ids = ",".join(_COINGECKO_IDS.values())
    try:
        r = requests.get(
            f"https://api.coingecko.com/api/v3/simple/price"
            f"?ids={cg_ids}&vs_currencies=usd&include_24hr_change=true",
            timeout=REQUEST_TIMEOUT,
            headers={"User-Agent": "memecoin-engine/1.0"},
        )
        if r.status_code != 200:
            log.warning("CoinGecko fallback HTTP %s", r.status_code)
            return result
        data = r.json()
        # Invert the id→symbol mapping for lookup
        id_to_sym = {v: k for k, v in _COINGECKO_IDS.items()}
        for cg_id, entry in data.items():
            sym = id_to_sym.get(cg_id)
            if sym:
                result[sym] = {
                    "price": float(entry.get("usd", 0) or 0),
                    "h24":   round(float(entry["usd_24h_change"]), 2) if entry.get("usd_24h_change") is not None else None,
                    "h6":    None,  # CoinGecko simple/price doesn't provide 6h
                }
        log.info("CoinGecko fallback returned prices for %d tokens",
                 sum(1 for d in result.values() if d["price"] > 0))
    except Exception as exc:
        log.warning("_fetch_basket_from_coingecko error: %s", exc)
    return result


def _compute_trend(h24: float | None, h6: float | None) -> str:
    """
    Derive trend signal from 24h and 6h price change percentages.
    Patch 130 — used in get_portfolio_state() to populate trend field.

    UPTREND:   h24 > +3% AND h6 > 0%       (medium + short term both positive)
    DOWNTREND: h24 < -5% OR (h24 < 0 AND h6 < -2%)  (clear negative momentum)
    NEUTRAL:   everything else
    """
    if h24 is None:
        return "NEUTRAL"
    # h6=None means fallback price source (CoinGecko) — use h24-only thresholds
    if h6 is None:
        if h24 > 5:
            return "UPTREND"
        if h24 < -5:
            return "DOWNTREND"
        return "NEUTRAL"
    # Both timeframes available (DexScreener) — use combined signal
    if h24 > 3 and h6 > 0:
        return "UPTREND"
    if h24 < -5 or (h24 < 0 and h6 < -2):
        return "DOWNTREND"
    return "NEUTRAL"


def _read_enriched_cache(allow_stale: bool = False) -> dict[str, dict] | None:
    """
    Read enriched price+change data from kv_store cache.
    Returns cached data if < CACHE_TTL_S old (or allow_stale=True), else None.
    Patch 130 — prevents DexScreener rate limits on every /spot/status call.
    allow_stale=True is used as last-resort fallback when live fetch fails (429).
    """
    try:
        with _get_conn() as conn:
            row = conn.execute(
                "SELECT value FROM kv_store WHERE key=?", (KV_KEY_ENRICHED,)
            ).fetchone()
        if not row:
            return None
        cached = json.loads(row[0])
        data = cached.get("data") or {}
        # Reject cache if all prices are 0 (written during a 429 rate-limit window)
        if data and not any(d.get("price", 0) > 0 for d in data.values()):
            return None
        if not allow_stale:
            updated_at = cached.get("updated_at", "")
            if updated_at:
                from datetime import datetime, timezone
                age = (datetime.now(timezone.utc) - datetime.fromisoformat(updated_at)).total_seconds()
                if age > CACHE_TTL_S:
                    return None
        return data or None
    except Exception:
        return None


def fetch_basket_prices() -> dict[str, float]:
    """
    Fetch current USD prices for all basket tokens via DexScreener batch API.
    Returns {symbol: price_usd}. Missing tokens get 0.0.
    Thin wrapper around _fetch_basket_enriched() for backward compatibility.
    """
    enriched = _fetch_basket_enriched()
    return {sym: d["price"] for sym, d in enriched.items()}


# ── Portfolio state ───────────────────────────────────────────────────────────

def get_portfolio_state() -> dict:
    """
    Read spot_holdings from DB, enrich with live prices.
    Returns full portfolio snapshot.
    """
    # Patch 135: read-only cache strategy — spot_monitor_step owns all fetching.
    # Tier 1: fresh cache (< 5min old)
    # Tier 2: stale cache (monitor hasn't cycled yet, avoid 429 storm from dashboard polls)
    # Tier 3: live fetch only on cold start (no cache written at all)
    enriched = _read_enriched_cache()
    if not enriched:
        enriched = _read_enriched_cache(allow_stale=True)
    if not enriched:
        # Cold start only — no cache ever written yet
        enriched = _fetch_basket_enriched()

    # Load DB holdings
    holdings_by_symbol: dict[str, dict] = {}
    try:
        with _get_conn() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT * FROM spot_holdings").fetchall()
            for r in rows:
                holdings_by_symbol[r["symbol"]] = dict(r)
    except Exception as exc:
        log.warning("get_portfolio_state DB read error: %s", exc)

    # Build enriched basket list (all 7 tokens always present)
    total_invested = 0.0
    total_value    = 0.0
    holdings: list[dict] = []

    for token in BASKET:
        sym       = token["symbol"]
        mint      = token["mint"]
        h         = holdings_by_symbol.get(sym)
        price_data = enriched.get(sym, {"price": 0.0, "h24": None, "h6": None})
        price      = price_data["price"]

        if h and h.get("token_amount", 0) > 0:
            tok_amt     = float(h["token_amount"])
            invested    = float(h["total_invested"])
            avg_cost    = float(h["avg_cost_usd"])
            value       = round(tok_amt * price, 4) if price > 0 else 0.0
            pnl_usd     = round(value - invested, 4)
            pnl_pct     = round((value - invested) / invested * 100, 2) if invested > 0 else 0.0
            total_invested += invested
            total_value    += value
        else:
            tok_amt  = 0.0
            invested = 0.0
            avg_cost = 0.0
            value    = 0.0
            pnl_usd  = 0.0
            pnl_pct  = 0.0

        holdings.append({
            "symbol":           sym,
            "name":             token["name"],
            "mint":             mint,
            "target_pct":       token["target_pct"],
            "token_amount":     tok_amt,
            "total_invested":   invested,
            "avg_cost_usd":     avg_cost,
            "current_price":    price,
            "current_value":    value,
            "pnl_usd":          pnl_usd,
            "pnl_pct":          pnl_pct,
            "last_buy_ts":      h["last_buy_ts"] if h else None,
            # Patch 130 — trend signal fields
            "trend":            _compute_trend(price_data["h24"], price_data["h6"]),
            "price_change_24h": round(price_data["h24"], 2) if price_data["h24"] is not None else None,
            "price_change_6h":  round(price_data["h6"],  2) if price_data["h6"]  is not None else None,
        })

    # Compute portfolio-level weights
    for h in holdings:
        h["current_pct"] = round(
            h["current_value"] / total_value * 100 if total_value > 0 else 0.0, 1
        )

    total_pnl_usd = round(total_value - total_invested, 2)
    total_pnl_pct = round(
        (total_value - total_invested) / total_invested * 100 if total_invested > 0 else 0.0, 2
    )

    return {
        "holdings":        holdings,
        "holdings_count":  sum(1 for h in holdings if h["token_amount"] > 0),
        "total_invested":  round(total_invested, 2),
        "total_value":     round(total_value, 2),
        "total_pnl_usd":   total_pnl_usd,
        "total_pnl_pct":   total_pnl_pct,
        "dry_run":         os.getenv("SPOT_DRY_RUN", "true").lower() == "true",
        "prices_ts":       datetime.now(timezone.utc).isoformat(),
    }


# ── Allocation advice ─────────────────────────────────────────────────────────

def get_allocation_advice(amount_usd: float) -> list[dict]:
    """
    Given a budget, compute optimal allocation by underweight gap.

    Algorithm:
    1. Compute each coin's underweight gap (target_pct - current_pct)
    2. Normalize positive gaps → proportional budget split
    3. Minimum $5 per coin; skip below threshold
    4. Return sorted by suggested_usd desc
    """
    if amount_usd <= 0:
        return []

    state    = get_portfolio_state()
    holdings = {h["symbol"]: h for h in state["holdings"]}
    total_v  = state["total_value"]

    # Compute gaps
    gaps: list[dict] = []
    for token in BASKET:
        sym        = token["symbol"]
        h          = holdings.get(sym, {})
        target     = token["target_pct"]
        current    = h.get("current_pct", 0.0)
        gap        = max(0.0, target - current)
        gaps.append({
            "symbol":      sym,
            "name":        token["name"],
            "mint":        token["mint"],
            "target_pct":  target,
            "current_pct": current,
            "gap_pct":     round(gap, 2),
            "current_price": h.get("current_price", 0.0),
        })

    total_gap = sum(g["gap_pct"] for g in gaps)
    if total_gap == 0:
        # Portfolio is perfectly balanced — spread evenly
        total_gap = len(gaps)
        for g in gaps:
            g["gap_pct"] = 1.0

    MIN_ALLOCATION = 5.0
    advice: list[dict] = []

    for g in gaps:
        if g["gap_pct"] <= 0:
            continue
        raw = amount_usd * (g["gap_pct"] / total_gap)
        if raw < MIN_ALLOCATION:
            continue
        advice.append({
            "symbol":        g["symbol"],
            "name":          g["name"],
            "mint":          g["mint"],
            "target_pct":    g["target_pct"],
            "current_pct":   g["current_pct"],
            "gap_pct":       g["gap_pct"],
            "suggested_usd": round(raw, 2),
            "current_price": g["current_price"],
        })

    # Re-scale to exactly match budget (floating point drift)
    total_suggested = sum(a["suggested_usd"] for a in advice)
    if total_suggested > 0 and advice:
        scale = amount_usd / total_suggested
        for a in advice:
            a["suggested_usd"] = round(a["suggested_usd"] * scale, 2)

    advice.sort(key=lambda x: x["suggested_usd"], reverse=True)
    return advice


# ── Buy / Sell ────────────────────────────────────────────────────────────────

def buy_spot(symbol: str, mint: str, amount_usd: float) -> dict:
    """
    Execute a spot buy.

    SPOT_DRY_RUN=true (default): simulate at current price, record PAPER tx.
    SPOT_DRY_RUN=false: execute real Jupiter swap, update holdings.
    """
    symbol    = symbol.upper()
    dry_run   = os.getenv("SPOT_DRY_RUN", "true").lower() == "true"
    ts_now    = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    if amount_usd < 1.0:
        return {"success": False, "error": "amount_usd must be >= $1"}

    # Fetch price first (needed for both paper and live)
    prices    = fetch_basket_prices()
    price     = prices.get(symbol, 0.0)

    if price <= 0:
        return {"success": False, "error": f"Could not fetch price for {symbol}"}

    token_amount = round(amount_usd / price, 6)
    tx_sig       = "PAPER"

    if not dry_run:
        # Live path — delegate to jupiter_swap
        try:
            import asyncio as _asyncio
            from utils.jupiter_swap import execute_buy, get_sol_price_usd  # type: ignore
            sol_price = _asyncio.get_event_loop().run_until_complete(get_sol_price_usd())
            result    = _asyncio.get_event_loop().run_until_complete(
                execute_buy(mint, amount_usd, sol_price)
            )
            if not result.get("tx_sig"):
                return {"success": False, "error": "Jupiter swap returned no tx_sig"}
            tx_sig           = result["tx_sig"]
            _raw             = result.get("amount_out_raw")
            _decimals        = _TOKEN_DECIMALS.get(symbol, 6)
            if _raw and _raw > 0:
                token_amount = _raw / (10 ** _decimals)          # Patch 150 — apply decimals
                price        = amount_usd / token_amount if token_amount > 0 else price
            else:
                price        = result.get("filled_price", price)
        except Exception as exc:
            log.error("buy_spot live swap error: %s", exc)
            return {"success": False, "error": str(exc)}

    # Update spot_holdings (upsert) and log to spot_buys
    try:
        with _get_conn() as conn:
            # Get existing holding
            existing = conn.execute(
                "SELECT * FROM spot_holdings WHERE symbol=?", (symbol,)
            ).fetchone()

            if existing:
                old_tokens   = float(existing["token_amount"])
                old_invested = float(existing["total_invested"])
                new_tokens   = old_tokens + token_amount
                new_invested = old_invested + amount_usd
                new_avg_cost = new_invested / new_tokens if new_tokens > 0 else price
                conn.execute("""
                    UPDATE spot_holdings SET
                        token_amount=?, total_invested=?, avg_cost_usd=?, last_buy_ts=?
                    WHERE symbol=?
                """, (round(new_tokens, 6), round(new_invested, 4),
                      round(new_avg_cost, 8), ts_now, symbol))
            else:
                conn.execute("""
                    INSERT INTO spot_holdings
                        (symbol, mint, token_amount, total_invested, avg_cost_usd, last_buy_ts, created_ts)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (symbol, mint,
                      round(token_amount, 6),
                      round(amount_usd, 4),
                      round(price, 8),
                      ts_now, ts_now))

            # Audit log
            conn.execute("""
                INSERT INTO spot_buys
                    (ts_utc, symbol, mint, side, amount_usd, token_amount, price_usd, tx_sig, dry_run)
                VALUES (?, ?, ?, 'BUY', ?, ?, ?, ?, ?)
            """, (ts_now, symbol, mint,
                  round(amount_usd, 4),
                  round(token_amount, 6),
                  round(price, 8),
                  tx_sig,
                  1 if dry_run else 0))
    except Exception as exc:
        log.error("buy_spot DB update error: %s", exc)
        return {"success": False, "error": f"DB update failed: {exc}"}

    # Memory log
    try:
        from utils import orchestrator  # type: ignore
        mode = "PAPER" if dry_run else "LIVE"
        orchestrator.append_memory(
            "spot_monitor",
            f"BUY [{mode}] {symbol}  ${amount_usd:.0f}  tokens={token_amount:.4f}  "
            f"price=${price:.4f}  tx={tx_sig[:12]}",
        )
    except Exception:
        pass

    # Telegram alert
    try:
        from utils.telegram_alerts import send_telegram_sync, should_rate_limit  # type: ignore
        if not should_rate_limit("spot_buy", 30):
            mode = "PAPER" if dry_run else "LIVE"
            send_telegram_sync(
                f"Spot Buy [{mode}] 🟢",
                f"{symbol}  ${amount_usd:.0f} → {token_amount:.4f} tokens @ ${price:.4f}",
                "🟢",
            )
    except Exception:
        pass

    return {
        "success":      True,
        "symbol":       symbol,
        "mint":         mint,
        "amount_usd":   amount_usd,
        "token_amount": round(token_amount, 6),
        "price_usd":    round(price, 8),
        "tx_sig":       tx_sig,
        "dry_run":      dry_run,
    }


def sell_spot(symbol: str, mint: str, pct: float = 100.0) -> dict:
    """
    Sell pct% of holding (default 100 = full sell).
    Paper mode simulates; live mode executes via jupiter_swap.
    """
    symbol  = symbol.upper()
    dry_run = os.getenv("SPOT_DRY_RUN", "true").lower() == "true"
    ts_now  = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    if pct <= 0 or pct > 100:
        return {"success": False, "error": "pct must be 1–100"}

    # Load holding
    holding = None
    try:
        with _get_conn() as conn:
            holding = conn.execute(
                "SELECT * FROM spot_holdings WHERE symbol=?", (symbol,)
            ).fetchone()
    except Exception as exc:
        return {"success": False, "error": str(exc)}

    if not holding or float(holding["token_amount"]) <= 0:
        return {"success": False, "error": f"No open holding for {symbol}"}

    prices       = fetch_basket_prices()
    price        = prices.get(symbol, 0.0)
    tok_to_sell  = round(float(holding["token_amount"]) * pct / 100, 6)
    usd_received = round(tok_to_sell * price, 4) if price > 0 else 0.0
    tx_sig       = "PAPER"

    if not dry_run and price > 0:
        try:
            import asyncio as _asyncio
            from utils.jupiter_swap import execute_sell, get_sol_price_usd  # type: ignore
            # token_amount_raw needs decimals — approximate with tok_to_sell * 10^6
            result = _asyncio.get_event_loop().run_until_complete(
                execute_sell(mint, int(tok_to_sell * 1_000_000))
            )
            tx_sig       = result.get("tx_sig", "PAPER")
            usd_received = result.get("usd_received", usd_received)
        except Exception as exc:
            log.error("sell_spot live swap error: %s", exc)
            return {"success": False, "error": str(exc)}

    # Update DB
    try:
        with _get_conn() as conn:
            remaining = float(holding["token_amount"]) - tok_to_sell
            if remaining <= 1e-8:
                # Full exit — clear the holding
                conn.execute("DELETE FROM spot_holdings WHERE symbol=?", (symbol,))
            else:
                # Partial — keep avg_cost, reduce token_amount and total_invested
                remain_invested = float(holding["total_invested"]) * (remaining / float(holding["token_amount"]))
                conn.execute("""
                    UPDATE spot_holdings SET token_amount=?, total_invested=?, last_buy_ts=?
                    WHERE symbol=?
                """, (round(remaining, 6), round(remain_invested, 4), ts_now, symbol))

            conn.execute("""
                INSERT INTO spot_buys
                    (ts_utc, symbol, mint, side, amount_usd, token_amount, price_usd, tx_sig, dry_run)
                VALUES (?, ?, ?, 'SELL', ?, ?, ?, ?, ?)
            """, (ts_now, symbol, mint,
                  round(usd_received, 4),
                  round(tok_to_sell, 6),
                  round(price, 8),
                  tx_sig,
                  1 if dry_run else 0))
    except Exception as exc:
        return {"success": False, "error": f"DB update failed: {exc}"}

    try:
        from utils import orchestrator  # type: ignore
        orchestrator.append_memory(
            "spot_monitor",
            f"SELL {'PAPER' if dry_run else 'LIVE'} {symbol}  {pct:.0f}%  "
            f"${usd_received:.2f}  tx={tx_sig[:12]}",
        )
    except Exception:
        pass

    return {
        "success":      True,
        "symbol":       symbol,
        "pct_sold":     pct,
        "tokens_sold":  tok_to_sell,
        "usd_received": usd_received,
        "price_usd":    price,
        "tx_sig":       tx_sig,
        "dry_run":      dry_run,
    }


# ── Monitor step (called every 60s from main loop) ────────────────────────────

def spot_monitor_step() -> None:
    """Refresh price cache and heartbeat the spot_monitor agent."""
    try:
        from utils import orchestrator  # type: ignore
        orchestrator.heartbeat("spot_monitor")
    except Exception:
        pass

    # Cache enriched data (price + h24 + h6) so /status endpoint avoids DexScreener rate limits.
    # Patch 130: also writes plain price cache for backward compat.
    # Only write to cache if we got real prices — don't overwrite good data with 429 zeros.
    try:
        enriched = _fetch_basket_enriched()
        if not any(d["price"] > 0 for d in enriched.values()):
            log.warning("spot_monitor: DexScreener returned all-zero prices (rate-limited?), skipping cache update")
            return
        prices   = {sym: d["price"] for sym, d in enriched.items()}
        ts_now   = datetime.now(timezone.utc).isoformat()
        with _get_conn() as conn:
            conn.execute(
                "INSERT INTO kv_store (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (KV_KEY_PRICES, json.dumps({"prices": prices, "updated_at": ts_now})),
            )
            conn.execute(
                "INSERT INTO kv_store (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (KV_KEY_ENRICHED, json.dumps({"data": enriched, "updated_at": ts_now})),
            )
        log.debug("spot_monitor: enriched prices cached for %d tokens", len(enriched))
    except Exception as exc:
        log.warning("spot_monitor_step price cache error: %s", exc)

    # Patch 134: DCA signal engine — fill outcomes (always) + hourly scan (throttled internally)
    try:
        from utils.spot_signal_engine import fill_spot_signal_outcomes, run_spot_signal_scan  # type: ignore
        fill_spot_signal_outcomes()
        run_spot_signal_scan()
    except Exception as exc:
        log.warning("spot_monitor: signal engine error: %s", exc)
