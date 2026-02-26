"""
perp_executor.py — Paper + Live Perpetuals Executor (Jupiter Perps on Solana)

Mirrors executor.py for spot tokens, but for leveraged perpetual positions.

Signals come from:
  - Auto-scanner: SOL/BTC/ETH directional based on regime (BULL→LONG, BEAR→SHORT)
  - Manual: Dashboard Quick Open form

Env vars:
  PERP_EXECUTOR_ENABLED  = true | false   (default false)
  PERP_DRY_RUN           = true | false   (default true — paper trading)
  MAX_OPEN_PERPS         = int            (default 2)
  PERP_SIZE_USD          = float          (default 100.0 per position)
  PERP_DEFAULT_LEVERAGE  = float          (default 2.0)
  PERP_COOLDOWN_HOURS    = float          (default 3.0 — min gap between same-symbol signals)
  PERP_MAX_HOLD_HOURS    = float          (default 48.0)
  PERP_STOP_PCT          = float          (default 8.0 — % from entry)
  PERP_TP1_PCT           = float          (default 15.0)
  PERP_TP2_PCT           = float          (default 30.0)
  PERP_TP1_CLOSE_PCT     = float          (default 0.50 — close 50% at TP1)
"""

import logging
import os
import sqlite3
from datetime import datetime, timezone, timedelta

import requests

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────

def _bool(key: str, default: bool) -> bool:
    return os.getenv(key, "true" if default else "false").lower() in ("1", "true", "yes")

def _float(key: str, default: float) -> float:
    try:
        return float(os.getenv(key, str(default)))
    except Exception:
        return default

def _int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, str(default)))
    except Exception:
        return default


PERP_ENABLED        = lambda: _bool("PERP_EXECUTOR_ENABLED", False)
PERP_DRY_RUN        = lambda: _bool("PERP_DRY_RUN", True)
MAX_OPEN_PERPS      = lambda: _int("MAX_OPEN_PERPS", 2)
PERP_SIZE_USD       = lambda: _float("PERP_SIZE_USD", 100.0)
PERP_LEVERAGE       = lambda: _float("PERP_DEFAULT_LEVERAGE", 2.0)
PERP_COOLDOWN_H     = lambda: _float("PERP_COOLDOWN_HOURS", 3.0)
PERP_MAX_HOLD_H     = lambda: _float("PERP_MAX_HOLD_HOURS", 48.0)
PERP_STOP_PCT       = lambda: _float("PERP_STOP_PCT", 8.0)
PERP_TP1_PCT        = lambda: _float("PERP_TP1_PCT", 15.0)
PERP_TP2_PCT        = lambda: _float("PERP_TP2_PCT", 30.0)
PERP_TP1_CLOSE_PCT  = lambda: _float("PERP_TP1_CLOSE_PCT", 0.50)

DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "data_storage", "engine.db"
)

PRICE_APIS = {
    "SOL": "https://price.jup.ag/v4/price?ids=SOL",
    "BTC": "https://price.jup.ag/v4/price?ids=BTC",
    "ETH": "https://price.jup.ag/v4/price?ids=ETH",
}

# Symbol → CoinGecko fallback IDs
CG_IDS = {"SOL": "solana", "BTC": "bitcoin", "ETH": "ethereum"}

# ── DB helpers ────────────────────────────────────────────────────────────────

def _conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fetch_price(symbol: str) -> float | None:
    """Fetch live price for SOL/BTC/ETH from Jupiter price API."""
    try:
        url = PRICE_APIS.get(symbol)
        if not url:
            return None
        r = requests.get(url, timeout=5)
        data = r.json()
        return float(data["data"][symbol]["price"])
    except Exception as e:
        logger.debug("Price fetch failed for %s: %s", symbol, e)
    # Fallback: CoinGecko
    try:
        cg_id = CG_IDS.get(symbol, symbol.lower())
        r = requests.get(
            f"https://api.coingecko.com/api/v3/simple/price?ids={cg_id}&vs_currencies=usd",
            timeout=5,
        )
        data = r.json()
        return float(data[cg_id]["usd"])
    except Exception as e:
        logger.debug("CoinGecko fallback failed for %s: %s", symbol, e)
    return None


def _get_open_perp_positions(dry_run_filter: int | None = None) -> list[dict]:
    """Return open perp positions. dry_run_filter: 1=paper, 0=live, None=all."""
    with _conn() as c:
        cur = c.cursor()
        if dry_run_filter is None:
            cur.execute("SELECT * FROM perp_positions WHERE status='OPEN' ORDER BY opened_ts_utc DESC")
        else:
            cur.execute(
                "SELECT * FROM perp_positions WHERE status='OPEN' AND dry_run=? ORDER BY opened_ts_utc DESC",
                (dry_run_filter,),
            )
        return [dict(r) for r in cur.fetchall()]


def _open_perp_position(
    symbol: str, side: str, entry_price: float,
    stop_price: float, tp1_price: float, tp2_price: float,
    size_usd: float, leverage: float,
    regime_label: str, dry_run: bool, notes: str = "",
) -> dict | None:
    """Insert a new perp position row and return it."""
    collateral = size_usd / leverage
    ts = _now_iso()
    with _conn() as c:
        cur = c.cursor()
        cur.execute("""
            INSERT INTO perp_positions
            (opened_ts_utc, symbol, side, entry_price, stop_price, tp1_price, tp2_price,
             size_usd, leverage, collateral_usd, regime_label, status, dry_run, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', ?, ?)
        """, (
            ts, symbol.upper(), side.upper(), entry_price,
            stop_price, tp1_price, tp2_price,
            size_usd, leverage, collateral, regime_label,
            1 if dry_run else 0, notes,
        ))
        position_id = cur.lastrowid
        c.commit()
    with _conn() as c:
        cur = c.cursor()
        cur.execute("SELECT * FROM perp_positions WHERE id=?", (position_id,))
        row = cur.fetchone()
        return dict(row) if row else None


def _close_perp_position(
    position_id: int, exit_price: float, exit_reason: str
) -> dict | None:
    """Close a perp position and calculate PnL."""
    with _conn() as c:
        cur = c.cursor()
        cur.execute("SELECT * FROM perp_positions WHERE id=?", (position_id,))
        row = cur.fetchone()
        if not row:
            return None
        pos = dict(row)

    entry = pos["entry_price"]
    side  = pos["side"].upper()
    size  = pos["size_usd"]
    lev   = pos["leverage"]

    # PnL calculation (leveraged)
    if side == "LONG":
        raw_pct = (exit_price - entry) / entry * 100
    else:
        raw_pct = (entry - exit_price) / entry * 100

    leveraged_pct = raw_pct * lev
    pnl_usd = size * (leveraged_pct / 100)

    ts = _now_iso()
    with _conn() as c:
        cur = c.cursor()
        cur.execute("""
            UPDATE perp_positions
            SET status='CLOSED', closed_ts_utc=?, exit_price=?,
                pnl_pct=?, pnl_usd=?, exit_reason=?
            WHERE id=?
        """, (ts, exit_price, round(leveraged_pct, 4), round(pnl_usd, 4), exit_reason, position_id))
        c.commit()

    # Update perp_outcomes if exists
    try:
        with _conn() as c:
            cur = c.cursor()
            cur.execute(
                "UPDATE perp_outcomes SET status='COMPLETE' WHERE symbol=? AND side=? AND status='PENDING'",
                (pos["symbol"], side),
            )
            c.commit()
    except Exception:
        pass

    pos.update({"exit_price": exit_price, "pnl_pct": leveraged_pct, "pnl_usd": pnl_usd, "exit_reason": exit_reason})
    return pos


def _queue_perp_outcome(symbol: str, side: str, entry_price: float, regime_label: str):
    """Insert a row into perp_outcomes for the learning loop."""
    ts = _now_iso()
    with _conn() as c:
        cur = c.cursor()
        cur.execute("""
            INSERT INTO perp_outcomes (created_ts_utc, symbol, side, entry_price, regime_label, status)
            VALUES (?, ?, ?, ?, ?, 'PENDING')
        """, (ts, symbol.upper(), side.upper(), entry_price, regime_label))
        c.commit()


def _in_cooldown(symbol: str, side: str) -> bool:
    """Return True if a same-symbol same-side position was opened within cooldown window."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=PERP_COOLDOWN_H())).isoformat()
    with _conn() as c:
        cur = c.cursor()
        cur.execute("""
            SELECT COUNT(*) FROM perp_positions
            WHERE symbol=? AND side=? AND opened_ts_utc > ? AND status IN ('OPEN','CLOSED')
        """, (symbol.upper(), side.upper(), cutoff))
        count = cur.fetchone()[0]
    return count > 0


# ── Public API ────────────────────────────────────────────────────────────────

async def execute_perp_signal(signal: dict) -> bool:
    """
    Open a paper or live perp position.

    signal keys:
      symbol        str   SOL | BTC | ETH
      side          str   LONG | SHORT
      size_usd      float (optional — defaults to PERP_SIZE_USD env)
      leverage      float (optional — defaults to PERP_LEVERAGE env)
      regime_label  str   (optional)
      source        str   (optional — 'auto' | 'dashboard')
    """
    if not PERP_ENABLED():
        logger.debug("Perp executor disabled — skipping")
        return False

    symbol = str(signal.get("symbol", "SOL")).upper()
    side   = str(signal.get("side", "LONG")).upper()

    if symbol not in ("SOL", "BTC", "ETH"):
        logger.warning("Unsupported perp symbol: %s", symbol)
        return False

    # Guard: cooldown
    if _in_cooldown(symbol, side):
        logger.info("Perp cooldown active for %s %s — skipping", symbol, side)
        return False

    # Guard: max open positions
    open_positions = _get_open_perp_positions()
    if len(open_positions) >= MAX_OPEN_PERPS():
        logger.info("Max open perps (%d) reached — skipping", MAX_OPEN_PERPS())
        return False

    # Guard: no duplicate symbol+side already open
    for p in open_positions:
        if p["symbol"] == symbol and p["side"] == side:
            logger.info("Already have open %s %s — skipping", symbol, side)
            return False

    # Fetch live price
    entry_price = _fetch_price(symbol)
    if not entry_price or entry_price <= 0:
        logger.warning("Could not fetch price for %s — skipping perp signal", symbol)
        return False

    size_usd    = float(signal.get("size_usd", PERP_SIZE_USD()))
    leverage    = float(signal.get("leverage", PERP_LEVERAGE()))
    regime      = str(signal.get("regime_label", "NEUTRAL"))
    dry_run     = PERP_DRY_RUN()

    # Compute exit levels
    stop_pct = PERP_STOP_PCT() / 100
    tp1_pct  = PERP_TP1_PCT() / 100
    tp2_pct  = PERP_TP2_PCT() / 100

    if side == "LONG":
        stop_price = entry_price * (1 - stop_pct)
        tp1_price  = entry_price * (1 + tp1_pct)
        tp2_price  = entry_price * (1 + tp2_pct)
    else:  # SHORT
        stop_price = entry_price * (1 + stop_pct)
        tp1_price  = entry_price * (1 - tp1_pct)
        tp2_price  = entry_price * (1 - tp2_pct)

    mode_tag = "PAPER" if dry_run else "LIVE"
    notes    = (
        f"auto={signal.get('source','auto')}|regime={regime}"
        f"|leverage={leverage}|tp1={round(tp1_price,4)}|tp2={round(tp2_price,4)}"
    )

    logger.info(
        "[PERP %s] %s %s @ $%.4f  stop=$%.4f  TP1=$%.4f  TP2=$%.4f  size=$%.0f x%.1f",
        mode_tag, side, symbol, entry_price, stop_price, tp1_price, tp2_price, size_usd, leverage,
    )

    if dry_run:
        pos = _open_perp_position(
            symbol, side, entry_price, stop_price, tp1_price, tp2_price,
            size_usd, leverage, regime, dry_run=True, notes=notes,
        )
    else:
        # Live: would call Jupiter Perps open API here
        # For now: open in DB as live (dry_run=0) and log warning
        logger.warning("LIVE PERP: Jupiter Perps open API not yet integrated — recording in DB only")
        pos = _open_perp_position(
            symbol, side, entry_price, stop_price, tp1_price, tp2_price,
            size_usd, leverage, regime, dry_run=False, notes=notes,
        )

    if pos:
        _queue_perp_outcome(symbol, side, entry_price, regime)
        logger.info("[PERP %s] Position opened id=%s", mode_tag, pos.get("id"))
        return True

    return False


def get_perp_status() -> dict:
    """Return full perp executor status for dashboard API."""
    enabled  = PERP_ENABLED()
    dry_run  = PERP_DRY_RUN()
    dry_int  = 1 if dry_run else 0

    open_positions = _get_open_perp_positions(dry_run_filter=dry_int)

    # Stats from closed positions
    with _conn() as c:
        cur = c.cursor()
        cur.execute("""
            SELECT COUNT(*) as total,
                   SUM(CASE WHEN pnl_pct > 0 THEN 1 ELSE 0 END) as wins,
                   AVG(pnl_pct) as avg_pnl
            FROM perp_positions
            WHERE status='CLOSED' AND dry_run=?
        """, (dry_int,))
        row = cur.fetchone()
        total_closed = row["total"] or 0
        wins = row["wins"] or 0
        avg_pnl = row["avg_pnl"]
        win_rate = round(wins / total_closed * 100, 1) if total_closed > 0 else None

    return {
        "enabled":        enabled,
        "dry_run":        dry_run,
        "max_positions":  MAX_OPEN_PERPS(),
        "size_usd":       PERP_SIZE_USD(),
        "default_leverage": PERP_LEVERAGE(),
        "open_positions": len(open_positions),
        "positions":      open_positions,
        "total_closed":   total_closed,
        "win_rate":       win_rate,
        "avg_pnl_pct":    round(avg_pnl, 2) if avg_pnl is not None else None,
    }


async def force_close_perp(position_id: int) -> dict:
    """Force-close a perp position at current market price."""
    with _conn() as c:
        cur = c.cursor()
        cur.execute("SELECT * FROM perp_positions WHERE id=?", (position_id,))
        row = cur.fetchone()
    if not row:
        return {"success": False, "error": "Position not found"}

    pos = dict(row)
    symbol = pos["symbol"]

    # Fetch current price
    price = _fetch_price(symbol)
    if not price:
        return {"success": False, "error": f"Could not fetch {symbol} price"}

    result = _close_perp_position(position_id, price, "FORCE_CLOSE")
    if result:
        return {"success": True, "pnl_pct": result.get("pnl_pct"), "exit_price": price}
    return {"success": False, "error": "Close failed"}


def get_perp_equity_curve(lookback_days: int = 30, fee_pct: float = 0.05) -> list:
    """
    Compute cumulative leveraged PnL curve from closed perp_positions.
    Returns list of {trade_n, ts, symbol, side, gross_ret, net_ret, equity_pct, drawdown_pct}.
    fee_pct: 0.05% per side (Jupiter Perps taker fee).
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).isoformat()
    with _conn() as c:
        cur = c.cursor()
        cur.execute("""
            SELECT id, closed_ts_utc as ts, symbol, side, pnl_pct
            FROM perp_positions
            WHERE status='CLOSED' AND closed_ts_utc > ?
            ORDER BY closed_ts_utc ASC
        """, (cutoff,))
        rows = [dict(r) for r in cur.fetchall()]

    if not rows:
        return []

    equity = 1.0
    peak   = 1.0
    result = []

    for i, row in enumerate(rows, 1):
        gross_ret = row["pnl_pct"] or 0.0
        net_ret   = gross_ret - fee_pct * 2  # round-trip fee
        equity   *= 1.0 + (net_ret / 100.0)
        peak      = max(peak, equity)
        dd        = (equity - peak) / peak * 100 if peak > 0 else 0.0

        result.append({
            "trade_n":     i,
            "ts":          row["ts"] or "",
            "symbol":      row["symbol"],
            "side":        row["side"],
            "gross_ret":   round(gross_ret, 4),
            "net_ret":     round(net_ret, 4),
            "equity":      round(equity, 6),
            "equity_pct":  round((equity - 1.0) * 100, 4),
            "drawdown_pct": round(dd, 4),
        })

    return result


async def perp_monitor_step():
    """
    Check all open perp positions and close on stop/TP/time.
    Called every 60s from the background monitor loop in main.py.
    """
    open_positions = _get_open_perp_positions()
    if not open_positions:
        return

    for pos in open_positions:
        pos_id   = pos["id"]
        symbol   = pos["symbol"]
        side     = pos["side"].upper()
        entry    = pos["entry_price"]
        stop     = pos["stop_price"]
        tp1      = pos["tp1_price"]
        tp2      = pos["tp2_price"]
        size     = pos["size_usd"]
        opened   = pos["opened_ts_utc"]
        max_hold = PERP_MAX_HOLD_H()

        # Fetch current price
        price = _fetch_price(symbol)
        if not price:
            continue

        # Check time limit
        try:
            opened_dt = datetime.fromisoformat(opened.replace("Z", "+00:00"))
            age_h = (datetime.now(timezone.utc) - opened_dt).total_seconds() / 3600
        except Exception:
            age_h = 0

        exit_reason = None

        if side == "LONG":
            if price <= stop:
                exit_reason = "STOP_LOSS"
            elif tp2 and price >= tp2:
                exit_reason = "TP2"
            elif tp1 and price >= tp1:
                exit_reason = "TP1"
            elif age_h >= max_hold:
                exit_reason = "TIME_LIMIT"
        else:  # SHORT
            if price >= stop:
                exit_reason = "STOP_LOSS"
            elif tp2 and price <= tp2:
                exit_reason = "TP2"
            elif tp1 and price <= tp1:
                exit_reason = "TP1"
            elif age_h >= max_hold:
                exit_reason = "TIME_LIMIT"

        if exit_reason:
            result = _close_perp_position(pos_id, price, exit_reason)
            mode   = "PAPER" if pos["dry_run"] else "LIVE"
            if result:
                logger.info(
                    "[PERP %s] Closed %s %s @ $%.4f  reason=%s  pnl=%.2f%%",
                    mode, side, symbol, price, exit_reason, result.get("pnl_pct", 0),
                )
