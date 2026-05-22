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
  PERP_DEFAULT_LEVERAGE  = float          (default 5.0)
  PERP_HIGH_CONF_LEVERAGE = float         (default 7.0)
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
from utils.db import record_capital_event

logger = logging.getLogger(__name__)


def _parse_notes(notes: str) -> dict:
    """Parse pipe-delimited notes string into a dict. e.g. 'ml_wp=0.38|ev=48.1' → {'ml_wp':'0.38','ev':'48.1'}"""
    result: dict = {}
    for pair in (notes or "").split("|"):
        if "=" in pair:
            k, v = pair.split("=", 1)
            result[k.strip()] = v.strip()
    return result


def _tg_perp(title: str, body: str, emoji: str = "🤖") -> None:
    """Send a Telegram notification for a perp trade event. Silent on failure."""
    try:
        from utils.telegram_alerts import send_telegram_sync  # type: ignore
        send_telegram_sync(title=title, body=body, emoji=emoji)
    except Exception as _e:
        logger.debug("Telegram perp notify failed: %s", _e)

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
PERP_LEVERAGE       = lambda: _float("PERP_DEFAULT_LEVERAGE", 5.0)
PERP_HIGH_CONF_LEVERAGE = lambda: max(_float("PERP_HIGH_CONF_LEVERAGE", 7.0), PERP_LEVERAGE())
PERP_COOLDOWN_H     = lambda: _float("PERP_COOLDOWN_HOURS", 3.0)
PERP_MAX_HOLD_H     = lambda: _float("PERP_MAX_HOLD_HOURS", 48.0)
PERP_STOP_PCT       = lambda: _float("PERP_STOP_PCT", 8.0)
PERP_TP1_PCT        = lambda: _float("PERP_TP1_PCT", 15.0)
PERP_TP2_PCT        = lambda: _float("PERP_TP2_PCT", 30.0)
PERP_TP1_CLOSE_PCT  = lambda: _float("PERP_TP1_CLOSE_PCT", 0.50)

# ── Scalp config (parallel paper track) ───────────────────────────────────────
SCALP_ENABLED       = lambda: _bool("SCALP_ENABLED", False)
SCALP_TP_PCT        = lambda: _float("SCALP_TP_PCT", 2.0)
SCALP_STOP_PCT      = lambda: _float("SCALP_STOP_PCT", 0.8)
SCALP_SIZE_USD      = lambda: _float("SCALP_SIZE_USD", 25.0)
SCALP_LEVERAGE      = lambda: _float("SCALP_LEVERAGE", 3.0)
SCALP_MAX_HOLD_MIN  = lambda: _float("SCALP_MAX_HOLD_MINUTES", 30.0)
SCALP_COOLDOWN_MIN  = lambda: _float("SCALP_COOLDOWN_MINUTES", 2.0)
SCALP_MAX_OPEN      = lambda: _int("SCALP_MAX_OPEN", 15)
SCALP_5M_THRESHOLD  = lambda: _float("SCALP_5M_THRESHOLD", 0.15)
PERP_MIN_COLLATERAL_USD = lambda: max(_float("PERP_MIN_COLLATERAL_USD", 10.0), 0.0)

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


def _normalize_live_order_size(size_usd: float, leverage: float) -> tuple[float, float, bool]:
    """
    Ensure live orders meet the exchange collateral floor.

    Returns (size_usd, collateral_usd, resized).
    """
    if leverage <= 0:
        return size_usd, 0.0, False

    collateral_usd = size_usd / leverage
    min_collateral_usd = PERP_MIN_COLLATERAL_USD()
    if collateral_usd + 1e-9 >= min_collateral_usd:
        return size_usd, collateral_usd, False

    collateral_usd = min_collateral_usd
    size_usd = round(collateral_usd * leverage, 2)
    return size_usd, collateral_usd, True


def _fetch_price(symbol: str) -> float | None:
    """Fetch live price for SOL/BTC/ETH — tries Jupiter, CoinGecko, then Kraken."""
    try:
        url = PRICE_APIS.get(symbol)
        if url:
            r = requests.get(url, timeout=5)
            data = r.json()
            return float(data["data"][symbol]["price"])
    except Exception as e:
        logger.debug("Price fetch failed for %s: %s", symbol, e)
    # Fallback 1: CoinGecko
    try:
        cg_id = CG_IDS.get(symbol, symbol.lower())
        r = requests.get(
            f"https://api.coingecko.com/api/v3/simple/price?ids={cg_id}&vs_currencies=usd",
            timeout=5,
        )
        data = r.json()
        price = data.get(cg_id, {}).get("usd")
        if price:
            return float(price)
    except Exception as e:
        logger.debug("CoinGecko fallback failed for %s: %s", symbol, e)
    # Fallback 2: Kraken public ticker (no API key, no geo-block)
    _KRAKEN_TICKER = {"SOL": "SOLUSD", "BTC": "XBTUSD", "ETH": "ETHUSD"}
    _KRAKEN_KEY    = {"SOL": "SOLUSD", "BTC": "XXBTZUSD", "ETH": "XETHZUSD"}
    try:
        pair = _KRAKEN_TICKER.get(symbol)
        if pair:
            r = requests.get(
                f"https://api.kraken.com/0/public/Ticker?pair={pair}",
                timeout=5,
            )
            result = r.json().get("result", {})
            key    = _KRAKEN_KEY.get(symbol, pair)
            price  = result.get(key, {}).get("c", [None])[0]
            if price:
                logger.debug("Kraken price for %s: %s", symbol, price)
                return float(price)
    except Exception as e:
        logger.debug("Kraken fallback failed for %s: %s", symbol, e)
    logger.warning("Could not fetch price for %s — skipping perp signal", symbol)
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


def _resolve_swing_leverage(signal: dict) -> float:
    """Default swing perps to 5x; step up to 7x only for high-confidence entries."""
    if signal.get("leverage") is not None:
        try:
            return float(signal.get("leverage"))
        except Exception:
            pass

    confidence = str(signal.get("confidence") or "").strip().upper()
    if confidence in {"A", "HIGH"}:
        return float(PERP_HIGH_CONF_LEVERAGE())
    return float(PERP_LEVERAGE())


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
    record_capital_event(
        "perps",
        "DEPLOY",
        collateral,
        f"{'PAPER' if dry_run else 'LIVE'} {side.upper()} {symbol.upper()} {leverage:.1f}x",
        symbol=symbol,
        ref_table="perp_positions",
        ref_id=int(position_id),
        dry_run=bool(dry_run),
        ts_utc=ts,
    )
    with _conn() as c:
        cur = c.cursor()
        cur.execute("SELECT * FROM perp_positions WHERE id=?", (position_id,))
        row = cur.fetchone()
        if row:
            # ── Telegram: Trade Opened ──────────────────────────────────────
            try:
                _nd = _parse_notes(notes)
                _tag  = "[SIMULATE]" if dry_run else "[LIVE]"
                _emj  = "🔵" if dry_run else "🟠"
                _sp   = abs((stop_price  - entry_price) / entry_price * 100) if entry_price else 0
                _tp1p = abs((tp1_price   - entry_price) / entry_price * 100) if entry_price else 0
                _tp2p = abs((tp2_price   - entry_price) / entry_price * 100) if entry_price else 0
                _body = (
                    f"Entry: ${entry_price:.4g}  Size: ${size_usd:.0f}  {leverage:.0f}× lev\n"
                    f"Stop: ${stop_price:.4g} (-{_sp:.1f}%)  TP1: +{_tp1p:.0f}%  TP2: +{_tp2p:.0f}%\n"
                    f"Regime: {regime_label}  src: {_nd.get('source', '?')}\n"
                    f"ML: wp={_nd.get('ml_wp','?')}  ret={_nd.get('ml_ret','?')}%  "
                    f"conf={_nd.get('ml_conf','?')}  EV={_nd.get('ev','?')}"
                )
                _tg_perp(f"{_tag} {symbol.upper()} {side.upper()} OPENED", _body, _emj)
            except Exception as _te:
                logger.debug("Telegram open format failed: %s", _te)
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
    is_live = not pos.get("dry_run")
    jup_key = pos.get("jupiter_position_key") or ""

    # ── Live: close on Jupiter first ──────────────────────────────────────
    _tx_sig_close = ""
    if is_live and jup_key:
        try:
            from utils.jupiter_perps_trade import close_perp_sync  # type: ignore[import]
            jup_close = close_perp_sync(
                position_pubkey=jup_key,
                dry_run=False,
                symbol=pos["symbol"],
            )
        except Exception as _jup_exc:
            logger.error("LIVE PERP close_perp_sync failed for id=%s: %s", position_id, _jup_exc)
            jup_close = {"success": False, "error": str(_jup_exc)}

        if not jup_close.get("success"):
            _close_err = str(jup_close.get("error", "unknown"))
            _missing_on_chain = "invalid_position" in _close_err.lower() or "position not found" in _close_err.lower()
            if _missing_on_chain:
                logger.warning(
                    "LIVE PERP: Jupiter reports id=%s key=%s already missing on-chain; "
                    "reconciling DB position as closed.",
                    position_id, jup_key[:16],
                )
                exit_reason = f"{exit_reason}|ONCHAIN_MISSING"
            else:
                logger.error(
                    "LIVE PERP: Jupiter close FAILED for id=%s key=%s — %s  "
                    "Position may still be open on-chain! Manual close required.",
                    position_id, jup_key[:16], _close_err,
                )
                # Mark position as needing manual intervention, do NOT mark CLOSED
                try:
                    with _conn() as c:
                        c.cursor().execute(
                            "UPDATE perp_positions SET notes = notes || ? WHERE id = ?",
                            ("\n[CLOSE_FAILED] " + _close_err[:100], position_id),
                        )
                        c.commit()
                except Exception:
                    pass
                return None  # signal failure — position stays OPEN in DB

        _tx_sig_close = jup_close.get("tx_sig") or ""
        # Use Jupiter's reported PnL if available (more accurate than local calc)
        _jup_pnl = jup_close.get("pnl_usd")
        logger.info(
            "LIVE PERP: closed id=%s on Jupiter — tx=%s pnl=$%.4f",
            position_id, _tx_sig_close[:16] if _tx_sig_close else "?",
            _jup_pnl if _jup_pnl else 0,
        )
    elif is_live and not jup_key:
        logger.warning(
            "LIVE PERP: closing id=%s in DB only — no jupiter_position_key stored. "
            "Position may be orphaned on-chain.",
            position_id,
        )

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
                pnl_pct=?, pnl_usd=?, exit_reason=?, tx_sig_close=?
            WHERE id=?
        """, (ts, exit_price, round(leveraged_pct, 4), round(pnl_usd, 4),
              exit_reason, _tx_sig_close, position_id))
        c.commit()
    record_capital_event(
        "perps",
        "RELEASE",
        float(pos.get("collateral_usd") or 0.0),
        f"{'PAPER' if pos.get('dry_run') else 'LIVE'} close {pos['symbol']} [{exit_reason}]",
        symbol=pos["symbol"],
        ref_table="perp_positions",
        ref_id=int(position_id),
        dry_run=bool(pos.get("dry_run")),
        ts_utc=ts,
    )
    record_capital_event(
        "perps",
        "REALIZED_PNL",
        pnl_usd,
        f"{'PAPER' if pos.get('dry_run') else 'LIVE'} close {pos['symbol']} [{exit_reason}]",
        symbol=pos["symbol"],
        ref_table="perp_positions",
        ref_id=int(position_id),
        dry_run=bool(pos.get("dry_run")),
        ts_utc=ts,
    )

    # Write completed outcome to perp_outcomes for the learning loop
    try:
        # Determine mode from notes (SCALP or SWING)
        notes_str = pos.get("notes") or ""
        mode = "SCALP" if "mode=SCALP" in notes_str else "SWING"
        opened_ts = pos.get("opened_ts_utc", ts)
        regime    = pos.get("regime_label", "UNKNOWN")
        # Ensure columns exist (migrations may have added them)
        with _conn() as c:
            cur = c.cursor()
            cur.execute("""
                INSERT INTO perp_outcomes
                  (created_ts_utc, symbol, side, entry_price, regime_label,
                   return_1h_pct, return_4h_pct, return_24h_pct,
                   evaluated_1h_ts_utc, evaluated_4h_ts_utc, evaluated_24h_ts_utc,
                   status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'COMPLETE')
            """, (
                opened_ts,
                pos["symbol"].upper(), side, pos["entry_price"], regime,
                round(leveraged_pct, 4),   # use actual PnL for all horizons
                round(leveraged_pct, 4),   # perp outcome known at close
                round(leveraged_pct, 4),
                ts, ts, ts,
            ))
            c.commit()
        logger.info(
            "[PERP OUTCOME] Recorded %s %s %s  pnl=%.2f%%  reason=%s  mode=%s",
            side, pos["symbol"], "WIN" if leveraged_pct > 0 else "LOSS",
            leveraged_pct, exit_reason, mode,
        )
    except Exception as _oe:
        logger.warning("perp_outcomes insert failed: %s", _oe)

    pos.update({"exit_price": exit_price, "pnl_pct": leveraged_pct, "pnl_usd": pnl_usd, "exit_reason": exit_reason})

    # ── Telegram: Trade Closed ──────────────────────────────────────────────
    try:
        _emj  = "✅" if leveraged_pct > 0 else "❌"
        _tag  = "[SIMULATE]" if pos.get("dry_run") else "[LIVE]"
        _pnl  = f"+{leveraged_pct:.2f}%" if leveraged_pct > 0 else f"{leveraged_pct:.2f}%"
        _usd  = f"(${pnl_usd:+.2f})"
        try:
            _odt  = datetime.fromisoformat(pos["opened_ts_utc"].replace("Z","").replace("+00:00","").split(".")[0])
            _hold = f"{(datetime.utcnow() - _odt).total_seconds() / 3600:.1f}h"
        except Exception:
            _hold = "?"
        _body = (
            f"Reason: {exit_reason}\n"
            f"${pos['entry_price']:.4g} → ${exit_price:.4g}  hold: {_hold}\n"
            f"Regime: {pos.get('regime_label', '?')}"
        )
        _tg_perp(f"{_tag} {pos['symbol']} {side} CLOSED  {_pnl} {_usd}", _body, _emj)
    except Exception as _te:
        logger.debug("Telegram close format failed: %s", _te)

    # Broadcast trade close event to dashboard WebSocket clients
    try:
        import asyncio as _asyncio
        notes_str = pos.get("notes") or ""
        _mode = "SCALP" if "mode=SCALP" in notes_str else "SWING"
        from dashboard.backend.ws_manager import broadcast_trade_event  # type: ignore
        _asyncio.get_event_loop().create_task(broadcast_trade_event(
            event="trade_close", mode=_mode, symbol=pos["symbol"], side=side,
            entry_price=pos["entry_price"], exit_price=exit_price,
            pnl_pct=leveraged_pct, exit_reason=exit_reason,
            size_usd=pos.get("size_usd"), leverage=pos.get("leverage"),
        ))
    except Exception:
        pass

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


def _get_open_scalp_positions() -> list[dict]:
    """Return open perp positions tagged mode=SCALP."""
    with _conn() as c:
        cur = c.cursor()
        cur.execute(
            "SELECT * FROM perp_positions WHERE status='OPEN' AND notes LIKE '%mode=SCALP%' ORDER BY opened_ts_utc DESC"
        )
        return [dict(r) for r in cur.fetchall()]


def _get_open_swing_positions() -> list[dict]:
    """Return open perp positions NOT tagged mode=SCALP (swing + legacy positions)."""
    with _conn() as c:
        cur = c.cursor()
        cur.execute(
            "SELECT * FROM perp_positions WHERE status='OPEN' AND (notes IS NULL OR notes NOT LIKE '%mode=SCALP%') ORDER BY opened_ts_utc DESC"
        )
        return [dict(r) for r in cur.fetchall()]


def _in_cooldown(symbol: str, side: str) -> bool:
    """Return True if a same-symbol same-side SWING position was opened within cooldown window."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=PERP_COOLDOWN_H())).isoformat()
    with _conn() as c:
        cur = c.cursor()
        cur.execute("""
            SELECT COUNT(*) FROM perp_positions
            WHERE symbol=? AND side=? AND opened_ts_utc > ? AND status IN ('OPEN','CLOSED')
              AND (notes IS NULL OR notes NOT LIKE '%mode=SCALP%')
        """, (symbol.upper(), side.upper(), cutoff))
        count = cur.fetchone()[0]
    return count > 0


def _in_cooldown_for_mode(symbol: str, side: str, mode: str) -> bool:
    """Mode-scoped cooldown — scalp and swing cooldowns don't interfere with each other."""
    if mode == "SCALP":
        hours = SCALP_COOLDOWN_MIN() / 60.0
    else:
        hours = PERP_COOLDOWN_H()
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    with _conn() as c:
        cur = c.cursor()
        if mode == "SCALP":
            cur.execute("""
                SELECT COUNT(*) FROM perp_positions
                WHERE symbol=? AND side=? AND opened_ts_utc > ?
                  AND status IN ('OPEN','CLOSED') AND notes LIKE '%mode=SCALP%'
            """, (symbol.upper(), side.upper(), cutoff))
        else:
            cur.execute("""
                SELECT COUNT(*) FROM perp_positions
                WHERE symbol=? AND side=? AND opened_ts_utc > ?
                  AND status IN ('OPEN','CLOSED')
                  AND (notes IS NULL OR notes NOT LIKE '%mode=SCALP%')
            """, (symbol.upper(), side.upper(), cutoff))
        return cur.fetchone()[0] > 0


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

    # ── Roadmap 3: authority bridge observe-mode check ────────────────────────
    try:
        from authority import resolve_action  # type: ignore[import]
        _auth = resolve_action("new_entry", "perps", executor="perps")
        if _auth["verdict"] == "BLOCK":
            # ── Proving-window exception: perps only, env-gated, temporary ──
            _proving = os.getenv("PERP_PROVING_WINDOW", "false").lower() == "true"
            if _proving:
                logger.warning(
                    "PROVING_WINDOW: perp authority BLOCK overridden — %s",
                    "; ".join(_auth["reasons"]),
                )
            else:
                logger.debug(
                    "AUTHORITY_BLOCK: perp_executor skipping signal — %s",
                    "; ".join(_auth["reasons"]),
                )
                return False
    except Exception as _auth_exc:
        logger.debug("authority check skipped: %s", _auth_exc)
    # ── End authority bridge ──────────────────────────────────────────────────

    symbol = str(signal.get("symbol", "SOL")).upper()
    side   = str(signal.get("side", "LONG")).upper()

    if symbol not in ("SOL", "BTC", "ETH"):
        logger.warning("Unsupported perp symbol: %s", symbol)
        return False

    # Detect scalp vs swing mode
    is_scalp = str(signal.get("source", "")).lower() == "scalp"
    mode_tag = "SCALP" if is_scalp else "SWING"

    # Guard: mode-scoped cooldown (scalp and swing use separate counters)
    if _in_cooldown_for_mode(symbol, side, mode_tag):
        logger.info("%s cooldown active for %s %s — skipping", mode_tag, symbol, side)
        return False

    # Guard: mode-scoped max open positions
    if is_scalp:
        open_mine = _get_open_scalp_positions()
        cap = SCALP_MAX_OPEN()
    else:
        open_mine = _get_open_swing_positions()
        cap = MAX_OPEN_PERPS()

    if len(open_mine) >= cap:
        logger.info("Max open %s positions (%d) reached — skipping", mode_tag, cap)
        return False

    # Guard: no duplicate symbol+side already open in same mode
    for p in open_mine:
        if p["symbol"] == symbol and p["side"] == side:
            logger.info("Already have open %s %s %s — skipping", mode_tag, symbol, side)
            return False

    # Patch 273: regime-direction filter — SCALP only
    # Fear (F&G ≤ 40): suppress LONG entries (counter-trend in bear/fear regime)
    # Greed (F&G ≥ 60): suppress SHORT entries (counter-trend in bull/greed regime)
    # Neutral (41–59): allow both directions
    if is_scalp:
        try:
            import json as _json
            with _conn() as _c:
                _fg_row = _c.execute(
                    "SELECT value FROM kv_store WHERE key='shared_fear_greed'"
                ).fetchone()
            _fg_val = None
            if _fg_row:
                _fg_data = _json.loads(_fg_row[0])
                _fg_val = _fg_data.get("value")
            if _fg_val is not None:
                if _fg_val <= 40 and side == "LONG":
                    logger.info(
                        "[SCALP REGIME FILTER] blocked LONG %s — F&G=%d (fear≤40, bear regime)",
                        symbol, _fg_val,
                    )
                    return False
                if _fg_val >= 60 and side == "SHORT":
                    logger.info(
                        "[SCALP REGIME FILTER] blocked SHORT %s — F&G=%d (greed≥60, bull regime)",
                        symbol, _fg_val,
                    )
                    return False
        except Exception as _e:
            logger.warning("[SCALP REGIME FILTER] F&G read failed, allowing both sides: %s", _e)

    # Fetch live price
    entry_price = _fetch_price(symbol)
    if not entry_price or entry_price <= 0:
        logger.warning("Could not fetch price for %s — skipping perp signal", symbol)
        return False

    regime  = str(signal.get("regime_label", "NEUTRAL"))
    dry_run = PERP_DRY_RUN()
    paper_tag = "PAPER" if dry_run else "LIVE"

    requested_size_usd = 0.0
    collateral = 0.0
    collateral_floor_applied = False

    # Compute exit levels — scalp uses tight TP/SL, swing uses wide swing targets
    if is_scalp:
        size_usd   = float(signal.get("size_usd", SCALP_SIZE_USD()))
        leverage   = float(signal.get("leverage", SCALP_LEVERAGE()))
        stop_pct   = SCALP_STOP_PCT() / 100
        tp1_pct    = SCALP_TP_PCT() / 100
        if side == "LONG":
            stop_price = entry_price * (1 - stop_pct)
            tp1_price  = entry_price * (1 + tp1_pct)
            tp2_price  = tp1_price   # sentinel: full exit at TP1, no TP2
        else:  # SHORT
            stop_price = entry_price * (1 + stop_pct)
            tp1_price  = entry_price * (1 - tp1_pct)
            tp2_price  = tp1_price
        notes = (
            f"mode=SCALP|source={signal.get('source','scalp')}|regime={regime}"
            f"|leverage={leverage}|tp1={round(tp1_price, 4)}"
        )
        logger.info(
            "[SCALP %s] %s %s @ $%.4f  stop=$%.4f  TP1=$%.4f  size=$%.0f x%.1f",
            paper_tag, side, symbol, entry_price, stop_price, tp1_price, size_usd, leverage,
        )
    else:
        size_usd   = float(signal.get("size_usd", PERP_SIZE_USD()))
        leverage   = _resolve_swing_leverage(signal)
        stop_pct   = PERP_STOP_PCT() / 100
        tp1_pct    = PERP_TP1_PCT() / 100
        tp2_pct    = PERP_TP2_PCT() / 100
        if side == "LONG":
            stop_price = entry_price * (1 - stop_pct)
            tp1_price  = entry_price * (1 + tp1_pct)
            tp2_price  = entry_price * (1 + tp2_pct)
        else:  # SHORT
            stop_price = entry_price * (1 + stop_pct)
            tp1_price  = entry_price * (1 - tp1_pct)
            tp2_price  = entry_price * (1 - tp2_pct)
        notes = (
            f"mode=SWING|source={signal.get('source','auto')}|regime={regime}"
            f"|confidence={str(signal.get('confidence') or '').upper() or 'UNSET'}"
            f"|leverage={leverage}|tp1={round(tp1_price, 4)}|tp2={round(tp2_price, 4)}"
        )
        logger.info(
            "[PERP %s] %s %s @ $%.4f  stop=$%.4f  TP1=$%.4f  TP2=$%.4f  size=$%.0f x%.1f",
            paper_tag, side, symbol, entry_price, stop_price, tp1_price, tp2_price, size_usd, leverage,
        )

    requested_size_usd = size_usd
    collateral = (size_usd / leverage) if leverage > 0 else 0.0
    if not dry_run:
        size_usd, collateral, collateral_floor_applied = _normalize_live_order_size(size_usd, leverage)
        if collateral_floor_applied:
            logger.warning(
                "[%s LIVE] Raised size from $%.2f to $%.2f to satisfy minimum collateral $%.2f at %.1fx",
                mode_tag, requested_size_usd, size_usd, collateral, leverage,
            )
            notes += (
                f"|requested_size_usd={round(requested_size_usd, 2)}"
                f"|collateral_floor_applied=1|min_collateral_usd={round(collateral, 2)}"
            )

    if dry_run:
        pos = _open_perp_position(
            symbol, side, entry_price, stop_price, tp1_price, tp2_price,
            size_usd, leverage, regime, dry_run=True, notes=notes,
        )
    else:
        # ── Live: call Jupiter Perps open API, then record in DB ──────────
        try:
            from utils.jupiter_perps_trade import open_perp_sync
            jup_result = open_perp_sync(
                symbol=symbol, side=side,
                collateral_usd=collateral, leverage=leverage,
                dry_run=False,
            )
        except Exception as _jup_exc:
            logger.error("LIVE PERP open_perp_sync import/call failed: %s", _jup_exc)
            jup_result = {"success": False, "error": str(_jup_exc)}

        if not jup_result.get("success"):
            logger.error(
                "LIVE PERP: Jupiter open FAILED for %s %s — %s",
                side, symbol, jup_result.get("error", "unknown"),
            )
            return False  # do NOT record a phantom position in the DB

        # Jupiter succeeded — record position with on-chain keys
        _jup_entry = jup_result.get("entry_price_usd")
        if _jup_entry and _jup_entry > 0:
            entry_price = _jup_entry  # use the actual fill price

        pos = _open_perp_position(
            symbol, side, entry_price, stop_price, tp1_price, tp2_price,
            size_usd, leverage, regime, dry_run=False, notes=notes,
        )
        # Store Jupiter on-chain keys for later close
        if pos:
            _jpk = jup_result.get("position_pubkey") or ""
            _txs = jup_result.get("tx_sig") or ""
            try:
                with _conn() as c:
                    c.cursor().execute(
                        "UPDATE perp_positions SET jupiter_position_key=?, tx_sig_open=? WHERE id=?",
                        (_jpk, _txs, pos["id"]),
                    )
                    c.commit()
                pos["jupiter_position_key"] = _jpk
                pos["tx_sig_open"] = _txs
            except Exception as _db_exc:
                logger.warning("LIVE PERP: failed to store jupiter keys: %s", _db_exc)
            logger.info(
                "LIVE PERP: %s %s opened on Jupiter — pos_key=%s tx=%s",
                side, symbol, _jpk[:16] if _jpk else "?", _txs[:16] if _txs else "?",
            )

    if pos:
        _queue_perp_outcome(symbol, side, entry_price, regime)
        logger.info("[%s %s] Position opened id=%s", mode_tag, paper_tag, pos.get("id"))
        # Broadcast trade open event to dashboard WebSocket clients
        try:
            import asyncio as _asyncio
            from dashboard.backend.ws_manager import broadcast_trade_event  # type: ignore
            _asyncio.get_event_loop().create_task(broadcast_trade_event(
                event="trade_open", mode=mode_tag, symbol=symbol, side=side,
                entry_price=entry_price, size_usd=size_usd, leverage=leverage,
            ))
        except Exception:
            pass
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
    # ── Roadmap 3: authority observation (close_position always permitted) ────
    try:
        from authority import resolve_action  # type: ignore[import]
        resolve_action("close_position", "perps", executor="perps_force")
    except Exception:
        pass
    # ── End authority observation ─────────────────────────────────────────────
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
    open_positions = _get_open_swing_positions()  # scalp positions handled by scalp_monitor_step()
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
            # ── Roadmap 3: authority observation (close always permitted) ─────
            try:
                from authority import resolve_action  # type: ignore[import]
                resolve_action("close_position", "perps", executor="perps_monitor")
            except Exception:
                pass
            # ── End authority observation ─────────────────────────────────────
            result = _close_perp_position(pos_id, price, exit_reason)
            mode   = "PAPER" if pos["dry_run"] else "LIVE"
            if result:
                logger.info(
                    "[PERP %s] Closed %s %s @ $%.4f  reason=%s  pnl=%.2f%%",
                    mode, side, symbol, price, exit_reason, result.get("pnl_pct", 0),
                )


async def scalp_monitor_step():
    """
    Check open SCALP positions every 5s and close on stop/TP/time.
    Called from _scalp_monitor_loop() in main.py.

    Scalp exits differ from swing:
    - TP1 triggers full exit (tp2=tp1 sentinel, so both conditions fire on same price)
    - max_hold uses SCALP_MAX_HOLD_MINUTES converted to hours
    - 5-second polling catches tiny 0.8% SL and 2% TP moves fast enough
    """
    open_positions = _get_open_scalp_positions()
    if not open_positions:
        return

    max_hold_h = SCALP_MAX_HOLD_MIN() / 60.0

    for pos in open_positions:
        pos_id = pos["id"]
        symbol = pos["symbol"]
        side   = pos["side"].upper()
        stop   = pos["stop_price"]
        tp1    = pos["tp1_price"]
        opened = pos["opened_ts_utc"]

        price = _fetch_price(symbol)
        if not price:
            continue

        try:
            opened_dt = datetime.fromisoformat(opened.replace("Z", "+00:00"))
            age_h = (datetime.now(timezone.utc) - opened_dt).total_seconds() / 3600
        except Exception:
            age_h = 0

        exit_reason = None

        if side == "LONG":
            if price <= stop:
                exit_reason = "STOP_LOSS"
            elif tp1 and price >= tp1:
                exit_reason = "TP1"
            elif age_h >= max_hold_h:
                exit_reason = "TIME_LIMIT"
        else:  # SHORT
            if price >= stop:
                exit_reason = "STOP_LOSS"
            elif tp1 and price <= tp1:
                exit_reason = "TP1"
            elif age_h >= max_hold_h:
                exit_reason = "TIME_LIMIT"

        if exit_reason:
            result = _close_perp_position(pos_id, price, exit_reason)
            paper_label = "PAPER" if pos["dry_run"] else "LIVE"
            if result:
                logger.info(
                    "[SCALP %s] Closed %s %s @ $%.4f  reason=%s  pnl=%.2f%%",
                    paper_label, side, symbol, price, exit_reason, result.get("pnl_pct", 0),
                )
