"""
executor.py — Trade execution orchestrator.

Manages the full lifecycle: buy → position monitoring → adaptive exit → sell.

Controlled by env vars:
  EXECUTOR_ENABLED=false    — master kill switch
  EXECUTOR_DRY_RUN=true     — log actions without sending txns
  MIN_SCORE_TO_EXECUTE=75   — higher bar than alert threshold
  MAX_OPEN_POSITIONS=3      — concurrent position cap
  STOP_LOSS_PCT=0.18        — default stop (overridden by exit_strategy)

Entry points:
  execute_signal(signal)    — called from main.py after alert fires
  position_monitor_loop()   — background task; now WebSocket-driven (falls back to 60s poll)
  force_sell(symbol)        — called from dashboard API

Price feed (Phase 3):
  Uses ws_price_feed.py for push-based price updates.
  When a new price arrives for a monitored mint, exit conditions are evaluated
  immediately — reducing worst-case reaction time from 60s → ~1-2s.
  Falls back to HTTP polling every 60s if no WS update within FALLBACK_POLL_SEC.
"""

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# ── Config ─────────────────────────────────────────────────────────────────────

EXECUTOR_ENABLED      = os.getenv("EXECUTOR_ENABLED", "false").lower() == "true"
DRY_RUN               = os.getenv("EXECUTOR_DRY_RUN", "true").lower() == "true"
MIN_SCORE             = float(os.getenv("MIN_SCORE_TO_EXECUTE", "75"))
MAX_OPEN_POSITIONS    = int(os.getenv("MAX_OPEN_POSITIONS", "3"))
TELEGRAM_TOKEN        = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID      = os.getenv("TELEGRAM_CHAT_ID", "")
MONITOR_INTERVAL_SEC  = 60   # legacy fallback poll interval

# Phase 3: WebSocket-driven exit
# If no WS price update arrives within this many seconds, fall back to HTTP poll.
FALLBACK_POLL_SEC     = int(os.getenv("EXECUTOR_FALLBACK_POLL_SEC", "30"))

# ── In-memory position state ──────────────────────────────────────────────────
# Keyed by trade_id:
# { exit_plan, tp1_hit, peak_price, amount_out_raw, position_usd }
_position_state: dict[int, dict] = {}
_monitor_task: Optional[asyncio.Task] = None


# ── Telegram helper ───────────────────────────────────────────────────────────

async def _tg(msg: str) -> None:
    """Send a Telegram message (fire-and-forget)."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logger.info("TELEGRAM not configured. Message: %s", msg)
        return
    try:
        import httpx
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": msg,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(url, json=payload)
    except Exception as exc:
        logger.warning("Telegram send failed: %s", exc)


# ── DB helpers ─────────────────────────────────────────────────────────────────

def _open_position(symbol: str, mint: str, entry_price: float,
                   stop_price: float, position_usd: float, tx_sig: str, notes: str) -> Optional[dict]:
    """Open a position in DB and return the trade row."""
    try:
        from utils.db import open_manual_position  # type: ignore
        result = open_manual_position(
            symbol=symbol,
            mint=mint,
            entry_price=entry_price,
            stop_price=stop_price,
            notes=notes,
        )
        if result.get("created"):
            # Attach tx_sig and position_usd via update if columns exist
            trade = result.get("position") or {}
            _attach_trade_meta(trade.get("id"), tx_sig, position_usd)
            return trade
        else:
            logger.warning("Position already exists for %s — skipping open", symbol)
            return None
    except Exception as exc:
        logger.error("Failed to open position for %s: %s", symbol, exc)
        return None


def _attach_trade_meta(trade_id: Optional[int], tx_sig: str, position_usd: float) -> None:
    """Update trade with tx_sig and position_usd (graceful if columns missing)."""
    if not trade_id:
        return
    try:
        from utils.db import get_conn  # type: ignore
        with get_conn() as conn:
            cur = conn.cursor()
            # Try setting tx_sig and position_usd — columns added by db.py migration
            try:
                cur.execute(
                    "UPDATE trades SET tx_sig=?, position_usd=? WHERE id=?",
                    (tx_sig, position_usd, trade_id),
                )
            except Exception:
                pass  # columns not yet migrated — fine, non-critical
    except Exception as exc:
        logger.warning("_attach_trade_meta failed: %s", exc)


def _close_position_with_meta(
    symbol: str,
    mint: str,
    trade_id: int,
    exit_price: float,
    exit_reason: str,
    pnl_pct: float,
) -> None:
    """Close position in DB with exit metadata."""
    try:
        from utils.db import close_manual_position, get_conn  # type: ignore
        note = f"exit={exit_reason} pnl={pnl_pct:.2f}%"
        close_manual_position(symbol=symbol, mint=mint, exit_price=exit_price, notes=note)
        # Try setting exit_reason column (graceful if missing)
        try:
            with get_conn() as conn:
                conn.execute(
                    "UPDATE trades SET exit_reason=?, pnl_pct=? WHERE id=?",
                    (exit_reason, pnl_pct, trade_id),
                )
        except Exception:
            pass
    except Exception as exc:
        logger.error("Failed to close position %d: %s", trade_id, exc)


def _get_open_positions() -> list[dict]:
    """Return all currently open trades."""
    try:
        from utils.db import get_open_positions  # type: ignore
        return get_open_positions(limit=50)
    except Exception as exc:
        logger.error("get_open_positions failed: %s", exc)
        return []


def _count_open_positions() -> int:
    return len(_get_open_positions())


# ── Signal execution ──────────────────────────────────────────────────────────

async def execute_signal(signal: dict) -> bool:
    """
    Called from main.py right after an alert fires.

    signal = {
        symbol, mint, entry_price, score, confidence,
        regime_label, position_usd
    }

    Returns True if a trade was opened (or simulated), False if skipped.
    """
    symbol       = signal.get("symbol", "?")
    mint         = signal.get("mint", "")
    entry_price  = float(signal.get("entry_price") or 0)
    score        = float(signal.get("score") or 0)
    confidence   = signal.get("confidence", "C")
    regime       = signal.get("regime_label", "UNKNOWN")
    position_usd = float(signal.get("position_usd") or 0)

    prefix = "[DRY_RUN] " if DRY_RUN else ""
    logger.info("%sexecute_signal: %s score=%.1f conf=%s regime=%s pos=$%.0f",
                prefix, symbol, score, confidence, regime, position_usd)

    # ── Guard rails ────────────────────────────────────────────────────────────

    if not EXECUTOR_ENABLED:
        logger.debug("EXECUTOR_ENABLED=false — skipping %s", symbol)
        return False

    if not mint:
        logger.info("No mint for %s — cannot execute", symbol)
        return False

    if score < MIN_SCORE:
        logger.info("Score %.1f < MIN_SCORE %.1f — skipping %s", score, MIN_SCORE, symbol)
        return False

    if position_usd <= 0:
        logger.info("position_usd <= 0 — skipping %s", symbol)
        return False

    if entry_price <= 0:
        logger.info("entry_price <= 0 — skipping %s", symbol)
        return False

    # Risk mode check
    try:
        from utils.db import get_risk_mode  # type: ignore
        risk_mode = get_risk_mode()
        if risk_mode == "DEFENSIVE" and score < 85:
            logger.info("DEFENSIVE risk mode + score<85 — skipping %s", symbol)
            return False
    except Exception:
        pass

    # Already have an open position?
    try:
        from utils.db import has_open_position  # type: ignore
        if has_open_position(symbol=symbol, mint=mint):
            logger.info("Already have open position for %s — skipping", symbol)
            return False
    except Exception:
        pass

    # Too many concurrent positions?
    open_count = _count_open_positions()
    if open_count >= MAX_OPEN_POSITIONS:
        logger.info("MAX_OPEN_POSITIONS=%d reached — skipping %s", MAX_OPEN_POSITIONS, symbol)
        return False

    # ── Build exit plan ────────────────────────────────────────────────────────

    from utils.exit_strategy import build_exit_plan  # type: ignore
    exit_plan = build_exit_plan(signal)

    stop_price = entry_price * (1 + exit_plan["stop_loss_pct"])

    logger.info(
        "%sBUYING %s  $%.2f  stop=%.6f  tp1=+%.1f%%  tp2=+%.1f%%  hold=%.1fh (learned from %d)",
        prefix, symbol, position_usd,
        stop_price,
        exit_plan["tp1_pct"] * 100,
        exit_plan["tp2_pct"] * 100,
        exit_plan["max_hold_hours"],
        exit_plan["learned_from"],
    )

    # ── Execute buy ────────────────────────────────────────────────────────────

    tx_sig = "DRY_RUN"
    amount_out_raw = 0

    if not DRY_RUN:
        try:
            from utils.jupiter_swap import execute_buy, get_sol_price_usd  # type: ignore
            sol_price = await get_sol_price_usd()
            result = await execute_buy(mint, position_usd, sol_price)
            tx_sig = result.get("tx_sig", "NO_SIG")
            amount_out_raw = result.get("amount_out_raw", 0)
        except Exception as exc:
            logger.error("Buy execution failed for %s: %s", symbol, exc)
            await _tg(f"⚠️ AUTO-BUY FAILED: <b>${symbol}</b>\n{exc}")
            return False

    # ── Open position in DB ────────────────────────────────────────────────────

    # Compute actual TP/stop price levels so PositionCard can display them
    tp1_price  = round(entry_price * (1 + exit_plan["tp1_pct"]), 12)
    tp2_price  = round(entry_price * (1 + exit_plan["tp2_pct"]), 12)

    notes = (
        f"auto=1|score={score:.0f}|conf={confidence}|regime={regime}"
        f"|tp1={tp1_price}|tp2={tp2_price}"
        f"|stop={exit_plan['stop_loss_pct']*100:.0f}%|tx={tx_sig[:12]}"
    )

    trade = _open_position(symbol, mint, entry_price, stop_price, position_usd, tx_sig, notes)
    if trade is None:
        return False

    trade_id = trade.get("id")

    # ── Queue outcome tracking so auto_tune sees executor trades ───────────────
    try:
        from utils.db import queue_alert_outcome  # type: ignore
        queue_alert_outcome({
            "symbol":       symbol,
            "mint":         mint,
            "entry_price":  entry_price,
            "score":        score,
            "regime_label": regime,
            "confidence":   confidence,
            "lane":         signal.get("lane", "executor"),
            "source":       signal.get("source", "executor"),
            "cycle_phase":  signal.get("cycle_phase", "TRANSITION"),
        })
    except Exception as _qao_err:
        logger.debug("queue_alert_outcome error: %s", _qao_err)

    # Store state for monitor loop
    if trade_id:
        _position_state[trade_id] = {
            "exit_plan":     exit_plan,
            "tp1_hit":       False,
            "peak_price":    entry_price,
            "amount_out_raw": amount_out_raw,
            "position_usd":  position_usd,
            "symbol":        symbol,
            "mint":          mint,
        }

    # Phase 3: Register mint with WebSocket price feed so we get push updates
    try:
        from utils.ws_price_feed import register_mint  # type: ignore
        register_mint(mint)
        logger.info("Registered %s with WS price feed", symbol)
    except Exception as exc:
        logger.warning("ws_price_feed.register_mint failed: %s", exc)

    # ── Telegram notification ──────────────────────────────────────────────────

    mode_tag = "🔵 DRY RUN" if DRY_RUN else "✅ AUTO-BOUGHT"
    sig_display = tx_sig[:12] + "…" if len(tx_sig) > 12 else tx_sig
    msg = (
        f"{mode_tag} <b>${symbol}</b> ${position_usd:.0f}\n"
        f"Entry: ${entry_price:.8g}  Score: {score:.0f}  Conf: {confidence}\n"
        f"Stop: ${stop_price:.8g} ({exit_plan['stop_loss_pct']*100:.0f}%)\n"
        f"TP1: +{exit_plan['tp1_pct']*100:.0f}%  TP2: +{exit_plan['tp2_pct']*100:.0f}%\n"
        f"Hold: ≤{exit_plan['max_hold_hours']:.0f}h  Learned: {exit_plan['learned_from']} signals\n"
        f"TX: <code>{sig_display}</code>"
    )
    await _tg(msg)
    return True


# ── Position monitor ──────────────────────────────────────────────────────────

async def monitor_positions() -> None:
    """
    One monitoring pass — check all open positions and exit if conditions met.
    Called every MONITOR_INTERVAL_SEC seconds.
    """
    positions = _get_open_positions()
    if not positions:
        return

    from utils.exit_strategy import should_exit, update_exit_learnings  # type: ignore

    for trade in positions:
        trade_id = trade.get("id")
        symbol   = trade.get("symbol", "?")
        mint     = trade.get("mint", "")
        entry    = float(trade.get("entry_price", 0))

        if not mint or entry <= 0:
            continue

        # Fetch current price — WS cache first, then HTTP fallback
        current_price: Optional[float] = None
        try:
            from utils import ws_price_feed as _wf  # type: ignore
            cached = _wf.get_price(mint)
            age    = _wf.get_price_age(mint)
            if cached and age is not None and age <= 30:
                current_price = cached
        except Exception:
            pass

        if not current_price:
            try:
                from utils.jupiter_swap import get_token_price_usd  # type: ignore
                current_price = await get_token_price_usd(mint)
            except Exception as exc:
                logger.warning("Price fetch failed for %s: %s", symbol, exc)
                continue

        if not current_price or current_price <= 0:
            continue

        # Get or init state for this trade
        state = _position_state.get(trade_id, {})
        if not state:
            # Reconstructed from DB after restart — use defaults
            from utils.exit_strategy import _default_plan  # type: ignore
            state = {
                "exit_plan":    _default_plan(f"restored|{symbol}"),
                "tp1_hit":      False,
                "peak_price":   entry,
                "amount_out_raw": 0,
                "position_usd": 0.0,
                "symbol":       symbol,
                "mint":         mint,
            }
            _position_state[trade_id] = state

        # Update peak
        if current_price > state["peak_price"]:
            state["peak_price"] = current_price

        exit_plan = state["exit_plan"]
        result = should_exit(
            trade=trade,
            current_price=current_price,
            peak_price=state["peak_price"],
            exit_plan=exit_plan,
            tp1_hit=state["tp1_hit"],
        )

        if result["exit"]:
            reason = result["reason"]
            pct_to_sell = result["pct_to_sell"]
            logger.info("EXIT TRIGGERED: %s  reason=%s  sell=%.0f%%  price=%.8g",
                        symbol, reason, pct_to_sell * 100, current_price)

            # Mark TP1 so we don't re-trigger it
            if reason.startswith("TP1"):
                state["tp1_hit"] = True

            await execute_exit(
                trade=trade,
                state=state,
                current_price=current_price,
                reason=reason,
                pct_to_sell=pct_to_sell,
            )


async def execute_exit(
    trade: dict,
    state: dict,
    current_price: float,
    reason: str,
    pct_to_sell: float,
) -> None:
    """Execute a sell and update all records."""
    trade_id    = trade.get("id")
    symbol      = trade.get("symbol", "?")
    mint        = trade.get("mint", "")
    entry_price = float(trade.get("entry_price", 0))
    position_usd = state.get("position_usd", 0.0)
    exit_plan   = state.get("exit_plan", {})

    pnl_pct = ((current_price - entry_price) / entry_price * 100) if entry_price > 0 else 0.0

    is_full_exit = pct_to_sell >= 0.95

    if not DRY_RUN:
        amount_raw = state.get("amount_out_raw", 0)
        sell_amount = int(amount_raw * pct_to_sell) if amount_raw > 0 else 0

        if sell_amount > 0:
            try:
                from utils.jupiter_swap import execute_sell  # type: ignore
                sell_result = await execute_sell(mint, sell_amount)
                logger.info("Sell executed: %s  tx=%s  usd=%.2f",
                            symbol, sell_result.get("tx_sig", "?"), sell_result.get("usd_received", 0))
            except Exception as exc:
                logger.error("Sell failed for %s: %s", symbol, exc)
                await _tg(f"⚠️ AUTO-SELL FAILED: <b>${symbol}</b>\n{reason}\n{exc}")
                return
        else:
            logger.warning("No token amount recorded for %s — can't sell", symbol)
    else:
        logger.info("DRY_RUN SELL: %s  %.0f%%  reason=%s  pnl=%.2f%%",
                    symbol, pct_to_sell * 100, reason, pnl_pct)

    if is_full_exit:
        _close_position_with_meta(
            symbol=symbol,
            mint=mint,
            trade_id=trade_id,
            exit_price=current_price,
            exit_reason=reason.split(" ")[0],
            pnl_pct=pnl_pct,
        )
        # Save to learnings
        from utils.exit_strategy import update_exit_learnings  # type: ignore
        if trade_id:
            update_exit_learnings(
                trade_id=trade_id,
                symbol=symbol,
                exit_reason=reason.split(" ")[0],
                entry_price=entry_price,
                exit_price=current_price,
                position_usd=position_usd,
                exit_plan=exit_plan,
            )
        _position_state.pop(trade_id, None)

        # Phase 3: Unregister from WS feed (no more positions holding this mint)
        # Only unregister if no other open positions have the same mint
        still_needed = any(
            s.get("mint") == mint
            for tid, s in _position_state.items()
            if tid != trade_id
        )
        if not still_needed:
            try:
                from utils.ws_price_feed import unregister_mint  # type: ignore
                unregister_mint(mint)
            except Exception:
                pass

    # Notify
    emoji = "🟢" if pnl_pct > 0 else ("🔴" if pnl_pct < 0 else "⚪")
    mode_tag = "[DRY_RUN] " if DRY_RUN else ""
    partial_tag = f" (partial {pct_to_sell*100:.0f}%)" if not is_full_exit else ""
    msg = (
        f"{mode_tag}{emoji} <b>${symbol}</b> EXIT{partial_tag}\n"
        f"Reason: {reason}\n"
        f"Entry: ${entry_price:.8g} → Exit: ${current_price:.8g}\n"
        f"PnL: {'+' if pnl_pct > 0 else ''}{pnl_pct:.2f}%"
    )
    if position_usd > 0:
        pnl_usd = position_usd * pnl_pct / 100
        msg += f" (${pnl_usd:+.2f})"
    await _tg(msg)


# ── Force sell (from dashboard) ───────────────────────────────────────────────

async def force_sell(symbol: str) -> dict:
    """
    Immediately sell all tokens for a given symbol.
    Called from dashboard API endpoint.
    Returns { success, message }
    """
    positions = _get_open_positions()
    target = next((p for p in positions if p.get("symbol", "").upper() == symbol.upper()), None)

    if not target:
        return {"success": False, "message": f"No open position found for {symbol}"}

    trade_id = target.get("id")
    mint     = target.get("mint", "")
    entry    = float(target.get("entry_price", 0))

    current_price: Optional[float] = None
    if mint:
        try:
            from utils.jupiter_swap import get_token_price_usd  # type: ignore
            current_price = await get_token_price_usd(mint)
        except Exception:
            pass

    if not current_price:
        # Use entry as fallback
        current_price = entry

    state = _position_state.get(trade_id, {
        "exit_plan":    {},
        "tp1_hit":      False,
        "peak_price":   entry,
        "amount_out_raw": 0,
        "position_usd": 0.0,
        "symbol":       symbol,
        "mint":         mint,
    })

    await execute_exit(
        trade=target,
        state=state,
        current_price=current_price,
        reason="FORCE_SELL",
        pct_to_sell=1.0,
    )
    return {"success": True, "message": f"Force-sold {symbol} at ${current_price:.8g}"}


# ── Background loop ───────────────────────────────────────────────────────────

async def position_monitor_loop() -> None:
    """
    Phase 3: WebSocket-driven position monitor.

    Strategy:
      1. Start ws_price_feed (if not already started) so Birdeye WS + HTTP fallback run.
      2. For each open position, maintain a per-mint price subscription queue.
      3. Wait on ANY queue for a price push (timeout = FALLBACK_POLL_SEC).
         → On push:  evaluate exit conditions for all positions sharing that mint.
         → On timeout (fallback): run a full HTTP price sweep for all open positions.

    This replaces the old fixed 60s sleep — worst-case reaction time is now
    FALLBACK_POLL_SEC (default 30s) and best-case is ~1-2s via WebSocket.
    """
    logger.info(
        "Position monitor loop started (WS-driven, fallback=%ds)",
        FALLBACK_POLL_SEC,
    )

    # Start the WebSocket price feed
    try:
        from utils import ws_price_feed  # type: ignore
        ws_price_feed.start()
        logger.info("ws_price_feed started from position_monitor_loop")
    except Exception as exc:
        logger.warning("ws_price_feed start failed: %s — falling back to poll only", exc)
        ws_price_feed = None  # type: ignore

    # Track per-mint subscriber queues so we can clean them up
    _mint_queues: dict[str, asyncio.Queue] = {}

    while True:
        try:
            positions = _get_open_positions()

            # ── Sync WS subscriptions ──────────────────────────────────────────
            active_mints = {p.get("mint", "") for p in positions if p.get("mint")}

            if ws_price_feed:
                # Subscribe new mints
                for mint in active_mints:
                    if mint and mint not in _mint_queues:
                        ws_price_feed.register_mint(mint)
                        _mint_queues[mint] = ws_price_feed.subscribe(mint)

                # Unsubscribe gone mints
                gone_mints = set(_mint_queues) - active_mints
                for mint in gone_mints:
                    q = _mint_queues.pop(mint)
                    ws_price_feed.unsubscribe(mint, q)
                    ws_price_feed.unregister_mint(mint)

            # ── Nothing to watch ──────────────────────────────────────────────
            if not positions:
                await asyncio.sleep(FALLBACK_POLL_SEC)
                continue

            # ── Wait for a price update (any mint) or timeout ─────────────────
            if _mint_queues:
                # Build a set of gather tasks — first one to fire wins
                wait_tasks = {
                    mint: asyncio.create_task(q.get())
                    for mint, q in _mint_queues.items()
                }
                done, pending = await asyncio.wait(
                    wait_tasks.values(),
                    timeout=FALLBACK_POLL_SEC,
                    return_when=asyncio.FIRST_COMPLETED,
                )

                # Cancel remaining wait tasks
                for t in pending:
                    t.cancel()

                if done:
                    # A price update arrived — evaluate exit for the updated mint(s)
                    updated_mints: set[str] = set()
                    for task in done:
                        try:
                            mint_recv, price_recv = task.result()
                            updated_mints.add(mint_recv)
                            # Drain queue in case multiple updates queued up
                            q = _mint_queues.get(mint_recv)
                            if q:
                                while not q.empty():
                                    q.get_nowait()
                        except Exception:
                            pass

                    # Check exit for positions on the updated mints
                    relevant = [p for p in positions if p.get("mint") in updated_mints]
                    if relevant:
                        await _check_exits_for(relevant)
                    continue   # back to top of loop — re-sync queues

                else:
                    # Timeout — run a full fallback sweep
                    logger.debug("WS timeout — running fallback sweep for %d positions", len(positions))
                    await monitor_positions()

            else:
                # No queues (WS feed unavailable) — pure poll mode
                await monitor_positions()
                await asyncio.sleep(FALLBACK_POLL_SEC)

        except Exception as exc:
            logger.error("position_monitor_loop error: %s", exc)
            await asyncio.sleep(5)


async def _check_exits_for(positions: list[dict]) -> None:
    """
    Run exit condition checks for a specific subset of positions using
    cached WS prices. Falls back to HTTP fetch if cache is empty.
    """
    from utils.exit_strategy import should_exit, update_exit_learnings  # type: ignore

    try:
        from utils import ws_price_feed  # type: ignore
        use_cache = True
    except Exception:
        use_cache = False

    for trade in positions:
        trade_id = trade.get("id")
        symbol   = trade.get("symbol", "?")
        mint     = trade.get("mint", "")
        entry    = float(trade.get("entry_price", 0))

        if not mint or entry <= 0:
            continue

        # Get price: WS cache first, then HTTP fallback
        current_price: Optional[float] = None
        if use_cache:
            current_price = ws_price_feed.get_price(mint)
            age = ws_price_feed.get_price_age(mint)
            if age is not None and age > 30:
                current_price = None   # stale — re-fetch

        if not current_price:
            try:
                from utils.jupiter_swap import get_token_price_usd  # type: ignore
                current_price = await get_token_price_usd(mint)
                if current_price and use_cache:
                    # Push back into cache so WS feed knows this price
                    ws_price_feed._push_price(mint, current_price)
            except Exception as exc:
                logger.warning("Price fetch failed for %s: %s", symbol, exc)
                continue

        if not current_price or current_price <= 0:
            continue

        # Get or init state
        state = _position_state.get(trade_id, {})
        if not state:
            from utils.exit_strategy import _default_plan  # type: ignore
            state = {
                "exit_plan":      _default_plan(f"restored|{symbol}"),
                "tp1_hit":        False,
                "peak_price":     entry,
                "amount_out_raw": 0,
                "position_usd":   0.0,
                "symbol":         symbol,
                "mint":           mint,
            }
            _position_state[trade_id] = state

        # Update peak
        if current_price > state["peak_price"]:
            state["peak_price"] = current_price

        result = should_exit(
            trade=trade,
            current_price=current_price,
            peak_price=state["peak_price"],
            exit_plan=state["exit_plan"],
            tp1_hit=state["tp1_hit"],
        )

        if result["exit"]:
            reason      = result["reason"]
            pct_to_sell = result["pct_to_sell"]
            logger.info(
                "EXIT TRIGGERED (WS): %s  reason=%s  sell=%.0f%%  price=%.8g",
                symbol, reason, pct_to_sell * 100, current_price,
            )
            if reason.startswith("TP1"):
                state["tp1_hit"] = True

            await execute_exit(
                trade=trade,
                state=state,
                current_price=current_price,
                reason=reason,
                pct_to_sell=pct_to_sell,
            )


# ── Status summary ────────────────────────────────────────────────────────────

def get_executor_status() -> dict:
    """Return current executor status for the dashboard API."""
    from utils.exit_strategy import get_exit_summary  # type: ignore
    open_positions = _get_open_positions()
    learnings = get_exit_summary()

    portfolio_usd = float(os.getenv("PORTFOLIO_USD", "1000"))

    # Phase 3: Include WS price feed status
    ws_status: dict = {}
    try:
        from utils import ws_price_feed  # type: ignore
        ws_status = {
            "ws_connected":     ws_price_feed.is_ws_connected(),
            "registered_mints": len(ws_price_feed._registered),
            "fallback_poll_sec": FALLBACK_POLL_SEC,
        }
    except Exception:
        ws_status = {"ws_connected": False}

    return {
        "enabled": os.getenv("EXECUTOR_ENABLED", "false").lower() == "true",
        "dry_run": os.getenv("EXECUTOR_DRY_RUN", "true").lower() == "true",
        "portfolio_usd": portfolio_usd,
        "min_score": MIN_SCORE,
        "max_open_positions": MAX_OPEN_POSITIONS,
        "open_positions": len(open_positions),
        "price_feed": ws_status,
        "positions": [
            {
                "id":          p.get("id"),
                "symbol":      p.get("symbol"),
                "mint":        p.get("mint"),
                "entry_price": p.get("entry_price"),
                "stop_price":  p.get("stop_price"),
                "opened_ts":   p.get("opened_ts_utc"),
                "notes":       p.get("notes"),
            }
            for p in open_positions
        ],
        "total_closed": learnings.get("total", 0),
        "win_rate":     learnings.get("win_rate"),
        "avg_pnl_pct":  learnings.get("avg_pnl_pct"),
        "exit_summary": learnings.get("by_reason", {}),
    }
