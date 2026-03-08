"""
Memecoin Manager — Patch 117
Buy / sell / monitor / status / tune for memecoin spot trades.
Reuses jupiter_swap.py for execution, db.py for persistence.
"""

import asyncio
import json
import os
import requests
from datetime import datetime, timezone, timedelta

from utils.db import get_conn
from utils.memecoin_scanner import get_cached_signals
from utils import orchestrator


# ── Price fetch ──────────────────────────────────────────────────────────────

def _fetch_sol_price() -> float:
    """Fetch current SOL price (Jupiter → Kraken fallback)."""
    try:
        r = requests.get(
            "https://price.jup.ag/v4/price?ids=SOL",
            timeout=6,
        )
        return float(r.json()["data"]["SOL"]["price"])
    except Exception:
        pass
    try:
        r = requests.get(
            "https://api.kraken.com/0/public/Ticker?pair=SOLUSD",
            timeout=6,
        )
        return float(r.json()["result"]["SOLUSD"]["c"][0])
    except Exception:
        return 0.0


def _fetch_token_price(mint: str) -> float:
    """Fetch current price for a token mint.

    Primary:  Jupiter v6 price API (better micro-cap coverage than v4)
    Fallback: DexScreener pair data (always has prices for active Solana pairs)
    """
    # 1. Jupiter v6
    try:
        r = requests.get(
            f"https://price.jup.ag/v6/price?ids={mint}",
            timeout=6,
        )
        data = r.json().get("data", {})
        td = data.get(mint) or next(iter(data.values()), None)
        if td:
            return float(td["price"])
    except Exception:
        pass
    # 2. DexScreener fallback — reliable for any active Solana pair
    try:
        r = requests.get(
            f"https://api.dexscreener.com/latest/dex/tokens/{mint}",
            timeout=8,
            headers={"User-Agent": "memecoin-engine/1.0"},
        )
        pairs = r.json().get("pairs") or []
        sol_pairs = [p for p in pairs if p.get("chainId") == "solana"]
        if sol_pairs:
            best = max(
                sol_pairs,
                key=lambda p: float((p.get("liquidity") or {}).get("usd", 0) or 0),
            )
            return float(best.get("priceUsd", 0) or 0)
    except Exception:
        pass
    return 0.0


# ── Buy / Sell ────────────────────────────────────────────────────────────────

def buy_memecoin(mint: str, symbol: str, amount_usd: float) -> dict:
    """
    Execute a spot buy via Jupiter swap.
    Logs the trade to memecoin_trades with status='OPEN'.

    MEMECOIN_DRY_RUN=true (default) → paper trade only, no real swap.
    """
    import sys
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    if root not in sys.path:
        sys.path.insert(0, root)

    # ── PAPER MODE — intercept before any real swap (Patch 121) ──────────────
    if os.getenv("MEMECOIN_DRY_RUN", "true").lower() == "true":
        sim_price  = _fetch_token_price(mint)
        sim_tokens = round(amount_usd / sim_price, 4) if sim_price > 0 else 0.0
        ts_now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        with get_conn() as conn:
            conn.execute("""
                INSERT INTO memecoin_trades
                    (opened_ts_utc, symbol, mint, entry_price, amount_usd,
                     token_amount, status, tx_sig_open)
                VALUES (?, ?, ?, ?, ?, ?, 'OPEN', 'PAPER')
            """, (ts_now, symbol.upper(), mint, sim_price, amount_usd, sim_tokens))
            conn.execute("""
                UPDATE memecoin_signal_outcomes
                SET bought = 1
                WHERE mint = ? AND id = (
                    SELECT id FROM memecoin_signal_outcomes
                    WHERE mint = ? ORDER BY scanned_at DESC LIMIT 1
                )
            """, (mint, mint))
        orchestrator.append_memory(
            "memecoin_scan",
            f"BUY [PAPER] {symbol.upper()} mint={mint[:8]}… ${amount_usd:.0f} "
            f"entry={sim_price:.8g}",
        )
        return {
            "success":      True,
            "tx_sig":       "PAPER",
            "entry_price":  sim_price,
            "token_amount": sim_tokens,
            "dry_run":      True,
        }
    # ── LIVE path — only reached when MEMECOIN_DRY_RUN=false ─────────────────

    sol_price = _fetch_sol_price()
    if sol_price <= 0:
        return {"success": False, "error": "Could not fetch SOL price"}

    try:
        from utils.jupiter_swap import execute_buy  # type: ignore
        loop = asyncio.new_event_loop()
        result = loop.run_until_complete(execute_buy(mint, amount_usd, sol_price))
        loop.close()
    except Exception as exc:
        return {"success": False, "error": str(exc)}

    if not result:
        return {"success": False, "error": "execute_buy returned None"}

    entry_price   = float(result.get("filled_price") or result.get("entry_price") or 0)
    token_amount  = float(result.get("token_amount")  or result.get("out_amount_ui") or 0)
    tx_sig        = result.get("tx_sig", "")
    dry_run       = result.get("dry_run", False)

    ts_now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO memecoin_trades
                (opened_ts_utc, symbol, mint, entry_price, amount_usd,
                 token_amount, status, tx_sig_open)
            VALUES (?, ?, ?, ?, ?, ?, 'OPEN', ?)
        """, (ts_now, symbol.upper(), mint, entry_price, amount_usd, token_amount, tx_sig))

        # Flag the most-recent outcome row for this mint as bought
        conn.execute("""
            UPDATE memecoin_signal_outcomes
            SET bought = 1
            WHERE mint = ? AND id = (
                SELECT id FROM memecoin_signal_outcomes
                WHERE mint = ? ORDER BY scanned_at DESC LIMIT 1
            )
        """, (mint, mint))

    orchestrator.append_memory(
        "memecoin_scan",
        f"BUY {symbol.upper()} mint={mint[:8]}… ${amount_usd:.0f} "
        f"entry={entry_price:.8g} {'DRY' if dry_run else 'LIVE'}",
    )

    return {
        "success":      True,
        "tx_sig":       tx_sig,
        "entry_price":  entry_price,
        "token_amount": token_amount,
        "dry_run":      dry_run,
    }


def sell_memecoin(mint: str, reason: str = "MANUAL") -> dict:
    """
    Execute a spot sell via Jupiter swap.
    Closes the open trade in DB and records PnL.
    """
    import os, sys
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    if root not in sys.path:
        sys.path.insert(0, root)

    with get_conn() as conn:
        row = conn.execute("""
            SELECT id, symbol, entry_price, amount_usd, token_amount
            FROM memecoin_trades
            WHERE mint = ? AND status = 'OPEN'
            ORDER BY opened_ts_utc DESC LIMIT 1
        """, (mint,)).fetchone()

    if not row:
        return {"success": False, "error": "No open trade found for this mint"}

    trade_id     = row["id"]
    symbol       = row["symbol"]
    entry_price  = float(row["entry_price"] or 0)
    amount_usd   = float(row["amount_usd"]  or 0)
    token_amount = float(row["token_amount"] or 0)

    if token_amount <= 0:
        return {"success": False, "error": "Token amount is 0 — cannot sell"}

    try:
        from utils.jupiter_swap import execute_sell  # type: ignore
        loop = asyncio.new_event_loop()
        result = loop.run_until_complete(execute_sell(mint, token_amount))
        loop.close()
    except Exception as exc:
        return {"success": False, "error": str(exc)}

    exit_price = float(result.get("filled_price") or result.get("exit_price") or 0)
    if exit_price <= 0:
        exit_price = _fetch_token_price(mint)

    pnl_pct = 0.0
    pnl_usd = 0.0
    if entry_price > 0 and exit_price > 0:
        pnl_pct = round((exit_price - entry_price) / entry_price * 100, 2)
        pnl_usd = round(amount_usd * pnl_pct / 100, 4)

    tx_sig = result.get("tx_sig", "")
    ts_now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    with get_conn() as conn:
        conn.execute("""
            UPDATE memecoin_trades
            SET status='CLOSED', exit_price=?, exit_reason=?,
                pnl_pct=?, pnl_usd=?, closed_ts_utc=?, tx_sig_close=?
            WHERE id=?
        """, (exit_price, reason, pnl_pct, pnl_usd, ts_now, tx_sig, trade_id))

    sign = "+" if pnl_pct >= 0 else ""
    orchestrator.append_memory(
        "memecoin_scan",
        f"SELL {symbol} [{reason}] exit={exit_price:.8g} "
        f"pnl={sign}{pnl_pct:.1f}% (${pnl_usd:+.2f})",
    )

    # Telegram alert on auto-exit (Patch 121)
    try:
        from utils.telegram_alerts import send_telegram_sync, should_rate_limit  # type: ignore
        emoji = "💰" if reason == "TP_2X" else "🛑"
        if not should_rate_limit("meme_exit", 60):
            send_telegram_sync(
                f"Memecoin {reason} {emoji}",
                f"{symbol}  pnl={sign}{pnl_pct:.1f}% (${pnl_usd:+.2f})",
                emoji,
            )
    except Exception:
        pass

    return {
        "success":    True,
        "pnl_pct":    pnl_pct,
        "pnl_usd":    pnl_usd,
        "exit_price": exit_price,
        "reason":     reason,
    }


# ── Outcome tracker (Patch 116) ───────────────────────────────────────────────

def memecoin_outcome_step():
    """
    Called every 60s alongside monitor_step.
    Fills in 1h / 4h / 24h price returns for every PENDING signal in
    memecoin_signal_outcomes. This is the core learning loop.
    """
    now    = datetime.now(timezone.utc)
    ts_now = now.strftime("%Y-%m-%d %H:%M:%S")

    with get_conn() as conn:
        pending = conn.execute("""
            SELECT id, mint, price_at_scan, scanned_at,
                   return_1h_pct, return_4h_pct, return_24h_pct
            FROM memecoin_signal_outcomes
            WHERE status = 'PENDING'
        """).fetchall()

    for row in pending:
        mint          = row["mint"]
        price_at_scan = float(row["price_at_scan"] or 0)
        if price_at_scan <= 0:
            continue

        try:
            raw_ts  = row["scanned_at"]
            scanned = datetime.fromisoformat(raw_ts.replace(" ", "T"))
            if scanned.tzinfo is None:
                scanned = scanned.replace(tzinfo=timezone.utc)
        except Exception:
            continue

        age = now - scanned
        if age < timedelta(hours=1):
            continue

        current = _fetch_token_price(mint)
        if current <= 0:
            continue

        ret = round((current - price_at_scan) / price_at_scan * 100, 2)

        updates: dict = {}
        if age >= timedelta(hours=1)  and row["return_1h_pct"]  is None:
            updates["return_1h_pct"]       = ret
            updates["evaluated_1h_ts_utc"] = ts_now
        if age >= timedelta(hours=4)  and row["return_4h_pct"]  is None:
            updates["return_4h_pct"]       = ret
            updates["evaluated_4h_ts_utc"] = ts_now
        if age >= timedelta(hours=24) and row["return_24h_pct"] is None:
            updates["return_24h_pct"]       = ret
            updates["evaluated_24h_ts_utc"] = ts_now
            updates["status"]               = "COMPLETE"

        if not updates:
            continue

        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values     = list(updates.values()) + [row["id"]]
        try:
            with get_conn() as conn:
                conn.execute(
                    f"UPDATE memecoin_signal_outcomes SET {set_clause} WHERE id = ?",
                    values,
                )
        except Exception:
            pass

    # Mark rows with dead/unlisted tokens as STALE after 48h
    # Prevents orphaned PENDING rows from clogging the learning query forever
    try:
        with get_conn() as conn:
            conn.execute("""
                UPDATE memecoin_signal_outcomes SET status = 'STALE'
                WHERE status = 'PENDING'
                  AND scanned_at < datetime('now', '-48 hours')
            """)
    except Exception:
        pass


# ── Auto-tuner (Patch 117) ────────────────────────────────────────────────────

def _tune_thresholds_step():
    """
    Analyze completed signal outcomes to auto-tune scanner thresholds.
    Requires >= 20 complete samples. Writes to kv_store['memecoin_learned_thresholds'].
    This is how the agents get smarter over time.
    """
    ts_now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    try:
        with get_conn() as conn:
            rows = conn.execute("""
                SELECT score, rug_label, top_holder_pct, mcap_at_scan,
                       token_age_days, vol_acceleration,
                       return_1h_pct, return_4h_pct, return_24h_pct, bought
                FROM memecoin_signal_outcomes
                WHERE status = 'COMPLETE' AND return_4h_pct IS NOT NULL
            """).fetchall()
    except Exception:
        return

    if len(rows) < 20:
        return  # not enough data yet

    total      = len(rows)
    winners    = [r for r in rows if (r["return_4h_pct"] or 0) > 0]   # Patch 132: any profit counts
    losers     = [r for r in rows if (r["return_4h_pct"] or 0) <= 0]
    win_rate   = round(len(winners) / total * 100, 1)

    # Win rates by rug label
    rug_stats: dict = {}
    for label in ("GOOD", "WARN", "UNKNOWN"):
        subset = [r for r in rows if r["rug_label"] == label]
        if subset:
            w = sum(1 for r in subset if (r["return_4h_pct"] or 0) > 0)  # Patch 132
            rug_stats[label] = {
                "count":    len(subset),
                "win_rate": round(w / len(subset) * 100, 1),
            }

    # Optimal score threshold — Patch 132
    # Search the FULL score range (not just 55-90) and find the floor that
    # maximises win rate. Win = 4h return > 0 (any profit — >30% too strict
    # at small sample sizes). Data shows low-score tokens outperform high-score
    # tokens, so we must search downward too; don't assume high score = better.
    best_score_min = 30   # conservative default — cast wide until data settles
    best_wr = 0.0
    for threshold in range(20, 76, 5):
        above = [r for r in rows if (r["score"] or 0) >= threshold]
        if len(above) >= 5:
            wr = sum(1 for r in above if (r["return_4h_pct"] or 0) > 0) / len(above)
            if wr > best_wr:
                best_wr = wr
                best_score_min = threshold

    # Optimal vol_acceleration: use 25th percentile of winners
    best_vacc = 5.0
    if winners:
        vaccs = sorted(float(r["vol_acceleration"] or 0) for r in winners)
        p25   = vaccs[max(0, len(vaccs) // 4 - 1)]
        best_vacc = max(5.0, round(p25, 1))

    # Max top holder: stay below average of winning signals + 50% buffer
    best_holder_max = 35.0
    win_holders = [float(r["top_holder_pct"] or 0) for r in winners if r["top_holder_pct"]]
    if win_holders:
        avg_winner_holder = sum(win_holders) / len(win_holders)
        best_holder_max   = min(50.0, round(avg_winner_holder * 1.5, 1))

    # Max score ceiling — tokens scoring too high are over-excited/FOMO and dump
    # Find the lowest ceiling above which WR drops below 40%
    best_score_max = 999  # no ceiling by default
    for ceiling in range(best_score_min + 5, 80, 5):
        above = [r for r in rows if (r["score"] or 0) > ceiling]
        if len(above) < 5:
            break
        wr = sum(1 for r in above if (r["return_4h_pct"] or 0) > 0) / len(above)
        if wr < 0.40:
            best_score_max = ceiling
            break

    thresholds = {
        "min_score":            best_score_min,
        "max_score":            best_score_max,
        "min_vol_acceleration": best_vacc,
        "max_top_holder_pct":   best_holder_max,
    }

    confidence = "low" if total < 50 else "medium" if total < 200 else "high"

    payload = {
        "thresholds":  thresholds,
        "sample_size": total,
        "win_rate":    win_rate,
        "rug_stats":   rug_stats,
        "updated_at":  ts_now,
        "confidence":  confidence,
    }

    try:
        with get_conn() as conn:
            conn.execute("""
                INSERT INTO kv_store (key, value) VALUES ('memecoin_learned_thresholds', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """, (json.dumps(payload),))
        orchestrator.append_memory(
            "memecoin_scan",
            f"TUNE {total} samples | win={win_rate:.0f}% | "
            f"score_min={best_score_min} vacc_min={best_vacc:.0f}% "
            f"confidence={confidence}",
        )
    except Exception:
        pass


# ── Monitor ───────────────────────────────────────────────────────────────────

def _auto_buy_step() -> None:
    """Auto-buy top qualifying signal each cycle when gate is enabled (Patch 121/135).

    Gates (all must pass):
      MEMECOIN_AUTO_BUY=true — master switch
      open positions < MEMECOIN_MAX_OPEN — capacity check
      F&G favorable (>25) — live mode only; skipped in paper mode to collect data at all conditions
      recent WR >= 40% (last 30 GOOD outcomes) — live mode only; pauses buys in bad market regimes
      score >= min_score threshold — signal quality floor
      score <= max_score threshold — avoids over-excited/FOMO tokens (historically poor WR above ceiling)
      rug_label == GOOD — safety
      buy_pressure >= 55% — momentum
      vol_acceleration >= min_vol_acceleration — tuner-learned momentum gate
      top_holder_pct <= max_top_holder_pct — concentration/whale dump protection
      mint_revoked == True — no inflation risk
    """
    if os.getenv("MEMECOIN_AUTO_BUY", "false").lower() != "true":
        return

    # Capacity check
    with get_conn() as conn:
        open_count = conn.execute(
            "SELECT COUNT(*) FROM memecoin_trades WHERE status='OPEN'"
        ).fetchone()[0]
        open_mints = {
            r[0] for r in conn.execute(
                "SELECT mint FROM memecoin_trades WHERE status='OPEN'"
            ).fetchall()
        }
    max_open = int(os.getenv("MEMECOIN_MAX_OPEN", "3"))
    if open_count >= max_open:
        return

    # F&G check — live mode only. In paper mode we want data at all F&G levels
    # so the learning loop accumulates outcomes across market conditions.
    dry_run = os.getenv("MEMECOIN_DRY_RUN", "true").lower() == "true"
    if not dry_run:
        try:
            from utils.agent_coordinator import get_fear_greed  # type: ignore
            if not get_fear_greed().get("favorable", True):
                return
        except Exception:
            pass

    # Load all tuner thresholds when confidence is medium/high (Patch 135)
    threshold       = float(os.getenv("MEMECOIN_BUY_SCORE_MIN", "65"))
    max_score       = 999    # no ceiling unless tuner has data
    vacc_min        = 5.0
    holder_max      = 35.0
    try:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT value FROM kv_store WHERE key='memecoin_learned_thresholds'"
            ).fetchone()
        if row:
            lt = json.loads(row[0])
            if lt.get("confidence") in ("medium", "high"):
                t = lt.get("thresholds", {})
                threshold  = float(t.get("min_score",            threshold))
                max_score  = float(t.get("max_score",            999))
                vacc_min   = float(t.get("min_vol_acceleration", vacc_min))
                holder_max = float(t.get("max_top_holder_pct",   holder_max))
    except Exception:
        pass

    # Recent performance gate — live mode only. If last 30 GOOD outcomes < 40% WR,
    # market conditions are poor → pause buying to protect capital (Patch 135)
    if not dry_run:
        try:
            with get_conn() as conn:
                recent = conn.execute("""
                    SELECT return_4h_pct FROM memecoin_signal_outcomes
                    WHERE rug_label='GOOD' AND return_4h_pct IS NOT NULL
                    ORDER BY scanned_at DESC LIMIT 30
                """).fetchall()
            if len(recent) >= 30:
                recent_wr = sum(1 for r in recent if r[0] > 0) / len(recent)
                if recent_wr < 0.40:
                    log.info("[MEME] Recent WR %.1f%% < 40%% — pausing live buys", recent_wr * 100)
                    return
        except Exception:
            pass

    # Loop signals by score descending — buy first qualifying one
    signals    = sorted(get_cached_signals(), key=lambda s: s.get("score", 0), reverse=True)
    amount_usd = float(os.getenv("MEMECOIN_BUY_USD", "15"))

    for sig in signals:
        mint       = sig.get("mint", "")
        symbol     = sig.get("symbol", "UNKNOWN")
        score      = sig.get("score", 0)
        rug        = sig.get("rug_label", "UNKNOWN")
        bp         = sig.get("buy_pressure") or 50.0
        revoked    = sig.get("mint_revoked", False)
        vacc       = float(sig.get("vol_acceleration") or 0.0)
        holder_pct = float(sig.get("top_holder_pct") or 0.0)

        if not mint or mint in open_mints:
            continue
        if score < threshold:
            break          # sorted descending — nothing better below
        if score > max_score:
            continue       # over-excited token — historically poor WR above ceiling
        if rug != "GOOD":
            continue
        if bp < 55:
            continue
        if not revoked:    # mint authority still live = inflation risk
            continue
        if vacc < vacc_min:
            continue       # insufficient volume acceleration
        if holder_pct > holder_max:
            continue       # too concentrated — whale dump risk

        result = buy_memecoin(mint, symbol, amount_usd)
        if result.get("success"):
            try:
                from utils.telegram_alerts import send_telegram_sync, should_rate_limit  # type: ignore
                if not should_rate_limit("meme_buy", 60):
                    mode = "PAPER" if result.get("dry_run") else "LIVE"
                    send_telegram_sync(
                        f"Memecoin Buy [{mode}] 🟢",
                        f"{symbol}  score={score:.1f}  rug=GOOD  bp={bp:.0f}%\n"
                        f"${amount_usd:.0f}  entry={result.get('entry_price', 0):.8g}\n"
                        f"Studying mechanics — {mode} trade logged.",
                        "🟢",
                    )
            except Exception:
                pass
        break   # one buy per cycle max


def memecoin_monitor_step():
    """
    Called every 60s from background loop.
    1. Auto-buys qualifying signals when MEMECOIN_AUTO_BUY=true (Patch 121).
    2. Auto-exits open positions at +100% (TP 2x) or -50% (SL).
    3. Fills in 1h/4h/24h returns on tracked signals (learning loop).
    4. Runs threshold tuner every cycle (no-ops until 20+ complete samples).
    """
    orchestrator.heartbeat("memecoin_scan")
    orchestrator.heartbeat("memecoin_monitor")

    # Auto-buy qualifying signals (Patch 121 — PAPER MODE while MEMECOIN_DRY_RUN=true)
    try:
        _auto_buy_step()
    except Exception:
        pass

    with get_conn() as conn:
        rows = conn.execute("""
            SELECT id, mint, symbol, entry_price, amount_usd
            FROM memecoin_trades
            WHERE status = 'OPEN'
        """).fetchall()

    for row in rows:
        mint        = row["mint"]
        entry_price = float(row["entry_price"] or 0)
        if entry_price <= 0:
            continue

        current_price = _fetch_token_price(mint)
        if current_price <= 0:
            continue

        pnl_pct = (current_price - entry_price) / entry_price * 100

        if pnl_pct >= 100.0:
            sell_memecoin(mint, "TP_2X")
        elif pnl_pct <= -50.0:
            sell_memecoin(mint, "SL_50")

    # Learning loop — fill in actual returns for tracked signals
    try:
        memecoin_outcome_step()
    except Exception:
        pass

    # Auto-tuner — adjusts thresholds once 20+ complete samples exist
    try:
        _tune_thresholds_step()
    except Exception:
        pass


# ── Status ────────────────────────────────────────────────────────────────────

def memecoin_status() -> dict:
    """
    Returns current scanner signals + open positions + stats +
    learned thresholds + recent closed trades.
    Called by GET /api/memecoins/status.
    """
    signals = get_cached_signals()

    # Open positions with live P&L
    positions = []
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT id, opened_ts_utc, symbol, mint, entry_price, amount_usd, token_amount
            FROM memecoin_trades
            WHERE status = 'OPEN'
            ORDER BY opened_ts_utc DESC
        """).fetchall()

    for row in rows:
        mint        = row["mint"]
        entry_price = float(row["entry_price"] or 0)
        current     = _fetch_token_price(mint)
        pnl_pct     = 0.0
        pnl_usd     = 0.0
        if entry_price > 0 and current > 0:
            pnl_pct = round((current - entry_price) / entry_price * 100, 2)
            pnl_usd = round(float(row["amount_usd"] or 0) * pnl_pct / 100, 4)

        positions.append({
            "id":            row["id"],
            "mint":          mint,
            "symbol":        row["symbol"],
            "entry_price":   entry_price,
            "current_price": current,
            "pnl_pct":       pnl_pct,
            "pnl_usd":       pnl_usd,
            "amount_usd":    float(row["amount_usd"] or 0),
            "opened":        row["opened_ts_utc"],
        })

    # Stats from closed trades
    with get_conn() as conn:
        closed = conn.execute("""
            SELECT pnl_pct, pnl_usd FROM memecoin_trades
            WHERE status = 'CLOSED'
        """).fetchall()

    total_pnl    = sum(float(r["pnl_usd"] or 0) for r in closed)
    wins         = sum(1 for r in closed if (r["pnl_pct"] or 0) > 0)
    closed_count = len(closed)
    win_rate     = round(wins / closed_count * 100, 1) if closed_count > 0 else 0.0

    # Recent closed trades (last 8) with PnL
    recent_closed = []
    with get_conn() as conn:
        rc_rows = conn.execute("""
            SELECT symbol, mint, pnl_pct, pnl_usd, exit_reason, closed_ts_utc
            FROM memecoin_trades
            WHERE status = 'CLOSED'
            ORDER BY closed_ts_utc DESC LIMIT 8
        """).fetchall()
    for r in rc_rows:
        recent_closed.append({
            "symbol":      r["symbol"],
            "mint":        r["mint"],
            "pnl_pct":     float(r["pnl_pct"] or 0),
            "pnl_usd":     float(r["pnl_usd"] or 0),
            "exit_reason": r["exit_reason"] or "MANUAL",
            "closed_at":   r["closed_ts_utc"],
        })

    # Learned thresholds from kv_store
    learned_thresholds = None
    try:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT value FROM kv_store WHERE key='memecoin_learned_thresholds'"
            ).fetchone()
            if row:
                learned_thresholds = json.loads(row["value"])
    except Exception:
        pass

    return {
        "signals":             signals,
        "positions":           positions,
        "stats": {
            "win_rate":     win_rate,
            "total_pnl":    round(total_pnl, 2),
            "closed_count": closed_count,
        },
        "recent_closed":       recent_closed,
        "learned_thresholds":  learned_thresholds,
    }
