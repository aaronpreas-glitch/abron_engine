"""
Tier system endpoints — Patch 114.

Routes:
  GET  /api/tiers/status                         — tier positions, profit buffer, and config
  POST /api/tiers/open-all                       — open 3x, 5x, and 10x simultaneously
  POST /api/tiers/open/{tier_label}              — open a single tier (3x | 5x | 10x)
  GET  /api/tiers/positions-live                 — operator view: live on-chain positions with
                                                   risk classification and action guidance
  GET  /api/tiers/intents                        — list unresolved execution intents (Patch 163)
  POST /api/tiers/intents/{id}/resolve           — manually resolve RECONCILE_MANUAL_REQUIRED (Patch 163)
  GET  /api/tiers/intents/{id}/recovery-context  — full operator recovery context, read-only (Patch 165)
                                                   Patch 166: enriched with next_action + repair_sql
"""
from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException

from auth import get_current_user
from routers._shared import _ensure_engine_path

log = logging.getLogger("dashboard")
router = APIRouter(prefix="/api/tiers", tags=["tiers"])


# ── Positions-live helpers ────────────────────────────────────────────────────

_JUP_V1_POSITIONS = "https://perps-api.jup.ag/v1/positions"
_MINT_SYMBOL: Dict[str, str] = {
    "So11111111111111111111111111111111111111112":   "SOL",
    "3NZ9JMVBmGAqocybic2c7LQCJScmgsAZ6vQqTDzcqmJh": "BTC",   # Jupiter Perps market mint
    "9n4nbM75f5Ui33ZbPYXn59EwSgE8CGsHtAeTH5YFeJ9E": "BTC",   # legacy/token mint fallback
    "7vfCXTUXx5WJV5JADk17DUJ4ksgau7utNKj4b963voxs": "ETH",
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v": "USDC",
}


def _fl(val: Any, divisor: float = 1.0) -> float:
    """Safe float coercion with optional divisor (e.g. micro-units → USD)."""
    try:
        return round(float(val) / divisor, 6)
    except Exception:
        return 0.0


def _resolve_perps_wallet() -> str:
    """Use the same wallet source as the live Jupiter perps client."""
    try:
        from utils.jupiter_perps_trade import get_wallet_address  # type: ignore
        return str(get_wallet_address() or "").strip()
    except Exception:
        return ""


def _compute_risk_tier(liq_dist_pct: float | None) -> str:
    """
    Classify a position by liquidation distance.
    Thresholds match jupiter_perps.py constants (SAFE/NEUTRAL/WARN/DANGER).
    """
    if liq_dist_pct is None: return "UNKNOWN"
    if liq_dist_pct < 15:    return "DANGER"
    if liq_dist_pct < 25:    return "WARN"
    if liq_dist_pct < 40:    return "NEUTRAL"
    return "SAFE"


def _compute_action_guidance(
    liq_dist_pct: float | None,
    pnl_usd: float,
    collateral_usd: float,
) -> str:
    """
    Decision-support label for the operator. Never auto-executed.

    CLOSE  — liq within 5%: emergency, position at imminent risk
    REDUCE — liq within 10% AND position in loss: stressed and losing
    WATCH  — liq within 20% OR position losing >20% of collateral
    HOLD   — all else: position healthy, no action needed
    """
    if liq_dist_pct is not None and liq_dist_pct < 5:
        return "CLOSE"
    if liq_dist_pct is not None and liq_dist_pct < 10 and pnl_usd < 0:
        return "REDUCE"
    loss_pct = abs(pnl_usd) / collateral_usd * 100 if collateral_usd > 0 else 0.0
    if (liq_dist_pct is not None and liq_dist_pct < 20) or (pnl_usd < 0 and loss_pct > 20):
        return "WATCH"
    return "HOLD"


# ── Perp aging / escalation ───────────────────────────────────────────────────
#
# Tracks how long each position has been in a warning state across polling cycles.
# State persisted in kv_store as JSON under key "perp_aging_{symbol}".
#
# Schema (kv_store value):
#   {
#     "action":              str,       — last recorded action_guidance
#     "consecutive_checks":  int,       — checks in this exact action (resets on change)
#     "warn_checks":         int,       — consecutive checks in WATCH / REDUCE / CLOSE
#     "first_warn_ts":       str|null,  — ISO-8601 UTC when current warn streak started
#   }

_WARN_ACTIONS = frozenset({"WATCH", "REDUCE", "CLOSE"})


def _compute_aging(prev: dict, action: str, now_ts: str) -> dict:
    """
    Pure function. Advance the aging record for one position check.

    Rules:
      - consecutive_checks increments when action is unchanged; resets to 1 on change.
      - warn_checks increments for any WATCH/REDUCE/CLOSE check (regardless of which).
        Resets to 0 the moment action returns to HOLD.
      - first_warn_ts is set on the first check that enters a warning state;
        preserved while warn_checks keeps incrementing; cleared on HOLD.
    """
    is_warn      = action in _WARN_ACTIONS
    prev_action  = prev.get("action", "")
    prev_is_warn = prev_action in _WARN_ACTIONS

    # Consecutive checks in this exact action
    consec = (prev.get("consecutive_checks", 0) + 1) if prev_action == action else 1

    # Consecutive checks in any warning state
    if is_warn:
        if prev_is_warn:
            warn_checks   = prev.get("warn_checks", 0) + 1
            first_warn_ts = prev.get("first_warn_ts") or now_ts
        else:
            warn_checks   = 1
            first_warn_ts = now_ts
    else:
        warn_checks   = 0
        first_warn_ts = None

    return {
        "action":             action,
        "consecutive_checks": consec,
        "warn_checks":        warn_checks,
        "first_warn_ts":      first_warn_ts,
    }


@router.get("/status")
async def tiers_status_ep(_: str = Depends(get_current_user)):
    """Return tier positions, profit buffer, and config."""
    _ensure_engine_path()
    try:
        from utils.tier_manager import tier_status as _ts  # type: ignore
        return _ts()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/open-all")
async def tiers_open_all_ep(_: str = Depends(get_current_user)):
    """Open 3x, 5x, and 10x tier positions simultaneously."""
    _ensure_engine_path()
    try:
        from utils.tier_manager import open_all_tiers as _oat  # type: ignore
        results = await asyncio.to_thread(_oat)
        return {"ok": True, "results": results}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/open/{tier_label}")
async def tiers_open_one_ep(tier_label: str, _: str = Depends(get_current_user)):
    """Open a single tier position (3x, 5x, or 10x)."""
    _ensure_engine_path()
    if tier_label not in ("3x", "5x", "10x"):
        raise HTTPException(status_code=400, detail="tier_label must be 3x, 5x, or 10x")
    try:
        from utils.tier_manager import open_tier_position as _otp  # type: ignore
        result = await asyncio.to_thread(_otp, tier_label)
        return result
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ── Positions Live: enriched operator view ────────────────────────────────────

@router.get("/positions-live")
async def tiers_positions_live_ep(_: str = Depends(get_current_user)):
    """
    Operator view: live on-chain perp positions enriched with risk classification
    and action guidance. Joins Jupiter real-time data with internal tier ledger.

    Risk tiers (based on liquidation distance):
      SAFE    — liq >= 40% away
      NEUTRAL — liq 25-40% away
      WARN    — liq 15-25% away
      DANGER  — liq < 15% away

    Action guidance (decision support only — never auto-executed):
      HOLD    — position healthy, no action needed
      WATCH   — liq < 20% away OR losing >20% of collateral
      REDUCE  — liq < 10% away AND position in loss
      CLOSE   — liq < 5% away (emergency)

    Returns positions sorted by urgency (CLOSE first, HOLD last).
    """
    _ensure_engine_path()

    def _run() -> dict:
        import os
        import requests as _req
        from datetime import datetime, timezone
        from utils.tier_manager import DB_PATH as _db_path  # type: ignore

        mode       = "LIVE" if os.getenv("PERP_DRY_RUN", "true").lower() == "false" else "SIM"
        wallet     = _resolve_perps_wallet()
        fetched_at = datetime.now(timezone.utc).isoformat()

        if not wallet:
            return {
                "mode": mode,
                "wallet": None,
                "fetched_at": fetched_at,
                "positions": [],
                "error": "Wallet address not configured",
            }

        # ── Fetch live Jupiter positions ──────────────────────────────────────
        jup_error: str | None = None
        raw_positions: list[Dict[str, Any]] = []
        try:
            r = _req.get(_JUP_V1_POSITIONS, params={"walletAddress": wallet}, timeout=10)
            if r.status_code == 200:
                raw_list = r.json().get("dataList") or r.json().get("positions") or []
                for p in raw_list:
                    mint       = p.get("marketMint", "")
                    symbol     = _MINT_SYMBOL.get(mint, mint[:6] + "...")
                    mark_price = _fl(p.get("markPrice"))
                    liq_price  = _fl(p.get("liquidationPrice"))
                    pnl_usd    = _fl(p.get("pnlAfterFeesUsd"))
                    col_usd    = _fl(p.get("collateralUsd"), 1_000_000)
                    liq_dist   = (
                        round(abs(mark_price - liq_price) / mark_price * 100, 1)
                        if mark_price > 0 and liq_price > 0 else None
                    )
                    raw_positions.append({
                        "symbol":           symbol,
                        "side":             str(p.get("side") or "LONG").upper(),
                        "entry_price":      _fl(p.get("entryPrice")),
                        "mark_price":       mark_price,
                        "size_usd":         _fl(p.get("sizeUsdDelta"), 1_000_000),
                        "collateral_usd":   col_usd,
                        "pnl_usd":          pnl_usd,
                        "pnl_pct":          _fl(p.get("pnlChangePctAfterFees")),
                        "leverage":         _fl(p.get("leverage")),
                        "liq_price":        liq_price,
                        "liq_distance_pct": liq_dist,
                        "total_fees_usd":   _fl(p.get("totalFeesUsd")),
                        "position_pubkey":  p.get("positionPubkey", ""),
                    })
            else:
                jup_error = f"Jupiter API returned HTTP {r.status_code}"
        except Exception as exc:
            jup_error = str(exc)[:200]

        # ── DB context: tier labels + stacking counts + aging states ──────────
        db_context:     Dict[str, Dict[str, Any]] = {}
        aging_context:  Dict[str, Dict[str, Any]] = {}   # perp_aging_{sym} from kv_store
        try:
            conn = sqlite3.connect(_db_path, timeout=10)
            conn.row_factory = sqlite3.Row
            db_rows = conn.execute("""
                SELECT symbol,
                       notes,
                       COUNT(*)            AS stacked_count,
                       MIN(entry_price)    AS first_entry_price,
                       SUM(collateral_usd) AS total_collateral_usd
                FROM   perp_positions
                WHERE  status = 'OPEN' AND notes LIKE '%TIER%'
                GROUP  BY symbol
            """).fetchall()
            for row in db_rows:
                notes = row["notes"] or ""
                tier_label = None
                for t in ("10x", "5x", "3x"):
                    if t in notes:
                        tier_label = t
                        break
                db_context[row["symbol"]] = {
                    "tier_label":          tier_label,
                    "stacked_count":       int(row["stacked_count"]),
                    "first_entry_price":   float(row["first_entry_price"]  or 0),
                    "db_collateral_usd":   float(row["total_collateral_usd"] or 0),
                }
            # Read existing aging records for all symbols (best-effort)
            aging_rows = conn.execute(
                "SELECT key, value FROM kv_store WHERE key LIKE 'perp_aging_%'"
            ).fetchall()
            for ar in aging_rows:
                sym_key = ar["key"].replace("perp_aging_", "", 1)
                try:
                    aging_context[sym_key] = json.loads(ar["value"])
                except Exception:
                    pass
            conn.close()
        except Exception as db_exc:
            log.debug("[positions-live] DB context fetch failed: %s", db_exc)

        # ── Enrich with risk tier + action guidance + aging ───────────────────
        enriched:      list[Dict[str, Any]] = []
        updated_aging: Dict[str, Dict[str, Any]] = {}   # write back after enrichment

        for pos in raw_positions:
            liq_dist  = pos["liq_distance_pct"]
            pnl_usd   = pos["pnl_usd"]
            col_usd   = pos["collateral_usd"]
            sym       = pos["symbol"]
            db_ctx    = db_context.get(sym, {})
            risk_tier = _compute_risk_tier(liq_dist)
            action    = _compute_action_guidance(liq_dist, pnl_usd, col_usd)

            # Advance aging state for this symbol
            new_aging = _compute_aging(aging_context.get(sym, {}), action, fetched_at)
            updated_aging[sym] = new_aging

            # Expose persistence fields only while in a warning state
            if action in _WARN_ACTIONS:
                watch_checks  = new_aging["warn_checks"]
                first_warn_ts = new_aging["first_warn_ts"]
            else:
                watch_checks  = 0
                first_warn_ts = None

            enriched.append({
                **pos,
                "risk_tier":       risk_tier,
                "action_guidance": action,
                "tier_label":      db_ctx.get("tier_label"),
                "stacked_count":   db_ctx.get("stacked_count", 1),
                "watch_checks":    watch_checks,    # consecutive checks in WATCH/REDUCE/CLOSE
                "first_warn_ts":   first_warn_ts,   # ISO-8601 UTC when current warn streak started
            })

        # ── Persist updated aging states ──────────────────────────────────────
        if updated_aging:
            try:
                conn2 = sqlite3.connect(_db_path, timeout=10)
                for sym, state in updated_aging.items():
                    conn2.execute(
                        "INSERT OR REPLACE INTO kv_store (key, value) VALUES (?, ?)",
                        (f"perp_aging_{sym}", json.dumps(state)),
                    )
                conn2.commit()
                conn2.close()
            except Exception as age_exc:
                log.debug("[positions-live] aging state write failed: %s", age_exc)

        # Sort by urgency: CLOSE first, HOLD last
        _urgency = {"CLOSE": 0, "REDUCE": 1, "WATCH": 2, "HOLD": 3}
        enriched.sort(key=lambda p: _urgency.get(p["action_guidance"], 9))

        # ── Summary ───────────────────────────────────────────────────────────
        net_pnl        = round(sum(p["pnl_usd"]       for p in enriched), 2)
        total_col      = round(sum(p["collateral_usd"] for p in enriched), 2)
        worst_liq_dist = min(
            (p["liq_distance_pct"] for p in enriched if p["liq_distance_pct"] is not None),
            default=None,
        )
        action_counts: Dict[str, int] = {"HOLD": 0, "WATCH": 0, "REDUCE": 0, "CLOSE": 0}
        for p in enriched:
            a = p["action_guidance"]
            action_counts[a] = action_counts.get(a, 0) + 1

        return {
            "positions": enriched,
            "summary": {
                "position_count":         len(enriched),
                "total_collateral_usd":   total_col,
                "net_pnl_usd":            net_pnl,
                "worst_liq_distance_pct": worst_liq_dist,
                "action_counts":          action_counts,
            },
            "system_mode":  mode,
            "fetched_at":   fetched_at,
            "jupiter_error": jup_error,
        }

    try:
        return await asyncio.to_thread(_run)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ── Patch 204: Perp ledger reconciliation ─────────────────────────────────────
#
# Alignment thresholds (used to classify each symbol):
#   ALIGNED         — |drift_collateral_pct| <  5 %  AND  |drift_entry_pct| < 2 %
#   DRIFTED         — |drift_collateral_pct| >= 5 %  OR   |drift_entry_pct| >= 2 %
#   MISSING_IN_DB   — Jupiter has a live position; no OPEN DB rows for that symbol
#   MISSING_ON_CHAIN— DB has OPEN rows for symbol; Jupiter has no matching position
#
# Read-only. No writes, no execution changes.

_RECON_ALIGNED_COL_PCT  = 5.0   # %  — collateral drift threshold
_RECON_ALIGNED_ENTRY_PCT = 2.0  # %  — entry-price drift threshold


@router.get("/reconciliation")
async def tiers_reconciliation_ep(_: str = Depends(get_current_user)):
    """
    Patch 204 — Read-only perp ledger reconciliation.

    For each symbol with open DB rows or a live Jupiter position, compare:
      - aggregate DB collateral / size / entry price
      - live Jupiter collateral / size / entry price
    and classify the match quality.
    """
    _ensure_engine_path()

    def _run() -> dict:
        import os
        import requests as _req
        from datetime import datetime, timezone
        from utils.tier_manager import DB_PATH as _db_path  # type: ignore

        fetched_at = datetime.now(timezone.utc).isoformat()
        wallet     = _resolve_perps_wallet()

        if not wallet:
            return {
                "wallet": None,
                "fetched_at": fetched_at,
                "total_live_positions": 0,
                "matched_symbols": [],
                "unmatched_jupiter_symbols": [],
                "rows": [],
                "error": "Wallet address not configured",
            }

        # ── 1. Fetch live Jupiter positions (reuse existing logic) ─────────────
        jup_error: str | None = None
        jup_by_symbol: Dict[str, Dict[str, Any]] = {}
        try:
            r = _req.get(_JUP_V1_POSITIONS, params={"walletAddress": wallet}, timeout=10)
            if r.status_code == 200:
                raw_list = r.json().get("dataList") or r.json().get("positions") or []
                for p in raw_list:
                    mint   = p.get("marketMint", "")
                    symbol = _MINT_SYMBOL.get(mint, mint[:6] + "...")
                    # Aggregate multiple entries for same symbol (shouldn't happen on-chain
                    # but handle defensively)
                    existing = jup_by_symbol.get(symbol)
                    col_usd  = _fl(p.get("collateralUsd"),  1_000_000)
                    size_usd = _fl(p.get("sizeUsdDelta"),   1_000_000)
                    ep       = _fl(p.get("entryPrice"))
                    pubkey   = p.get("positionPubkey", "")
                    if existing:
                        existing["live_collateral_usd"] += col_usd
                        existing["live_size_usd"]       += size_usd
                        # weighted avg entry price
                        prev_sz = existing.get("_size_weight", existing["live_size_usd"])
                        existing["live_entry_price"] = (
                            (existing["live_entry_price"] * prev_sz + ep * size_usd)
                            / (prev_sz + size_usd)
                        ) if prev_sz + size_usd > 0 else ep
                    else:
                        jup_by_symbol[symbol] = {
                            "live_collateral_usd": col_usd,
                            "live_size_usd":       size_usd,
                            "live_entry_price":    ep,
                            "jupiter_position_key": pubkey,
                        }
            else:
                jup_error = f"Jupiter HTTP {r.status_code}"
        except Exception as exc:
            jup_error = str(exc)[:200]

        if jup_error:
            return {
                "positions": [],
                "summary": None,
                "thresholds": {
                    "aligned_collateral_pct": _RECON_ALIGNED_COL_PCT,
                    "aligned_entry_pct":      _RECON_ALIGNED_ENTRY_PCT,
                },
                "fetched_at":   fetched_at,
                "jupiter_error": jup_error,
            }

        # ── 2. Fetch DB open positions aggregated by symbol ────────────────────
        db_by_symbol: Dict[str, Dict[str, Any]] = {}
        try:
            conn = sqlite3.connect(_db_path, timeout=10)
            conn.row_factory = sqlite3.Row
            db_rows = conn.execute("""
                SELECT symbol,
                       notes,
                       COUNT(*)                                       AS stacked_count,
                       SUM(collateral_usd)                           AS db_collateral_usd,
                       SUM(size_usd)                                 AS db_size_usd,
                       SUM(collateral_usd * entry_price)
                           / NULLIF(SUM(collateral_usd), 0)          AS db_entry_price_wavg,
                       MAX(opened_ts_utc)                            AS last_sync_ts
                FROM   perp_positions
                WHERE  status = 'OPEN'
                GROUP  BY symbol
            """).fetchall()
            conn.close()
            for row in db_rows:
                notes      = row["notes"] or ""
                tier_label = None
                for t in ("10x", "5x", "3x"):
                    if t in notes:
                        tier_label = t
                        break
                db_by_symbol[row["symbol"]] = {
                    "tier_label":        tier_label,
                    "stacked_count":     int(row["stacked_count"]),
                    "db_collateral_usd": float(row["db_collateral_usd"] or 0),
                    "db_size_usd":       float(row["db_size_usd"]       or 0),
                    "db_entry_price":    float(row["db_entry_price_wavg"] or 0),
                    "last_sync_ts":      row["last_sync_ts"],
                }
        except Exception as db_exc:
            log.warning("[reconciliation] DB fetch failed: %s", db_exc)

        # ── 3. Build per-symbol reconciliation rows ───────────────────────────
        all_symbols = set(jup_by_symbol) | set(db_by_symbol)
        positions: list[Dict[str, Any]] = []

        for sym in sorted(all_symbols):
            jup = jup_by_symbol.get(sym)
            db  = db_by_symbol.get(sym)

            live_col  = jup["live_collateral_usd"] if jup else 0.0
            live_size = jup["live_size_usd"]       if jup else 0.0
            live_ep   = jup["live_entry_price"]    if jup else 0.0
            db_col    = db["db_collateral_usd"]    if db  else 0.0
            db_size   = db["db_size_usd"]          if db  else 0.0
            db_ep     = db["db_entry_price"]       if db  else 0.0

            drift_col_usd  = round(live_col  - db_col,  2)
            drift_size_usd = round(live_size - db_size, 2)

            # percentage drifts (use live as reference; handle zero-division)
            drift_col_pct  = round(abs(drift_col_usd)  / live_col  * 100, 1) if live_col  > 0 else None
            drift_entry_pct = round(
                abs(live_ep - db_ep) / live_ep * 100, 2
            ) if live_ep > 0 else None

            if jup and not db:
                status = "MISSING_IN_DB"
            elif db and not jup:
                status = "MISSING_ON_CHAIN"
            else:
                col_ok   = (drift_col_pct  is not None and drift_col_pct  < _RECON_ALIGNED_COL_PCT)
                entry_ok = (drift_entry_pct is not None and drift_entry_pct < _RECON_ALIGNED_ENTRY_PCT)
                status = "ALIGNED" if (col_ok and entry_ok) else "DRIFTED"

            positions.append({
                "symbol":              sym,
                "tier_label":          db["tier_label"]         if db  else None,
                "jupiter_position_key": jup["jupiter_position_key"] if jup else None,
                "db_collateral_usd":   db_col,
                "live_collateral_usd": live_col,
                "db_size_usd":         db_size,
                "live_size_usd":       live_size,
                "db_entry_price":      db_ep,
                "live_entry_price":    live_ep,
                "drift_collateral_usd": drift_col_usd,
                "drift_collateral_pct": drift_col_pct,
                "drift_size_usd":      drift_size_usd,
                "drift_entry_pct":     drift_entry_pct,
                "status":              status,
                "last_sync_ts":        db["last_sync_ts"] if db else None,
                "notes":               f"stacked_count={db['stacked_count']}" if db else "no DB rows",
            })

        # Sort: worst first (MISSING > DRIFTED > ALIGNED, then by abs drift)
        _status_order = {"MISSING_IN_DB": 0, "MISSING_ON_CHAIN": 1, "DRIFTED": 2, "ALIGNED": 3}
        positions.sort(key=lambda p: (
            _status_order.get(p["status"], 9),
            -(abs(p["drift_collateral_usd"])),
        ))

        # ── 4. Summary ────────────────────────────────────────────────────────
        statuses = [p["status"] for p in positions]
        total_db_col   = round(sum(p["db_collateral_usd"]   for p in positions), 2)
        total_live_col = round(sum(p["live_collateral_usd"] for p in positions), 2)

        return {
            "positions": positions,
            "summary": {
                "total_db_collateral_usd":   total_db_col,
                "total_live_collateral_usd": total_live_col,
                "total_drift_usd":           round(total_live_col - total_db_col, 2),
                "count_aligned":             statuses.count("ALIGNED"),
                "count_drifted":             statuses.count("DRIFTED"),
                "count_missing_on_chain":    statuses.count("MISSING_ON_CHAIN"),
                "count_missing_in_db":       statuses.count("MISSING_IN_DB"),
            },
            "thresholds": {
                "aligned_collateral_pct": _RECON_ALIGNED_COL_PCT,
                "aligned_entry_pct":      _RECON_ALIGNED_ENTRY_PCT,
            },
            "fetched_at":    fetched_at,
            "jupiter_error": jup_error,
        }

    try:
        return await asyncio.to_thread(_run)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ── Patch 163: Operator workflow for unresolved execution intents ──────────────

# Statuses that require operator attention or are still in-flight
_OPERATOR_VISIBLE_STATUSES = (
    "PENDING",
    "SUBMIT_AMBIGUOUS",
    "RECONCILE_MANUAL_REQUIRED",
)

# Statuses that an operator is allowed to manually resolve to
_RESOLVABLE_TO = {"RECONCILED_CONFIRMED", "RECONCILED_FAILED"}


@router.get("/intents")
async def tiers_list_intents(_: str = Depends(get_current_user)):
    """
    List unresolved or operator-attention tier execution intents.

    Returns: PENDING, SUBMIT_AMBIGUOUS, and RECONCILE_MANUAL_REQUIRED rows
    from the last 48 hours, ordered newest-first. Includes age and recommended action.
    """
    _ensure_engine_path()
    try:
        from utils.tier_manager import DB_PATH as _TM_DB  # type: ignore

        conn = sqlite3.connect(_TM_DB, timeout=10)
        conn.row_factory = sqlite3.Row
        now_dt = datetime.now(timezone.utc)

        placeholders = ",".join(f"'{s}'" for s in _OPERATOR_VISIBLE_STATUSES)
        rows = conn.execute(f"""
            SELECT * FROM tier_execution_intents
            WHERE status IN ({placeholders})
              AND created_ts >= datetime('now', '-48 hours')
            ORDER BY id DESC
            LIMIT 100
        """).fetchall()
        conn.close()

        result = []
        for row in rows:
            r = dict(row)
            # Compute human-readable age
            try:
                created_dt = datetime.fromisoformat(
                    r["created_ts"].replace("+00:00", "").rstrip("Z")
                ).replace(tzinfo=timezone.utc)
                age_s = (now_dt - created_dt).total_seconds()
                r["age"] = f"{int(age_s // 60)}m {int(age_s % 60)}s"
            except Exception:
                r["age"] = "unknown"

            # Patch 164: attach matching perp_positions row when pubkey is known.
            # Gives operator entry price, collateral, and liquidation price in one view.
            r["perp_position"] = None
            pubkey = r.get("position_pubkey") or ""
            if pubkey:
                try:
                    pp = conn.execute(
                        """
                        SELECT id, status, entry_price, collateral_usd,
                               liquidation_price, opened_ts_utc, closed_ts_utc,
                               leverage, side, symbol
                          FROM perp_positions
                         WHERE jupiter_position_key = ?
                         ORDER BY id DESC LIMIT 1
                        """,
                        (pubkey,),
                    ).fetchone()
                    if pp:
                        r["perp_position"] = dict(pp)
                except Exception:
                    pass  # never block the intent list due to a secondary lookup failure

            # Recommended action per status
            if r["status"] == "RECONCILE_MANUAL_REQUIRED":
                pp_hint = ""
                if r["perp_position"]:
                    pp_hint = (
                        f" perp_positions row #{r['perp_position']['id']} "
                        f"shows status={r['perp_position']['status']}."
                    )
                r["recommended_action"] = (
                    f"Check Jupiter dashboard for pubkey={pubkey or 'unknown'}.{pp_hint} "
                    "If position exists and is tracked in perp_positions: "
                    "POST /resolve with RECONCILED_CONFIRMED. "
                    "If position does not exist: POST /resolve with RECONCILED_FAILED."
                )
            elif r["status"] == "SUBMIT_AMBIGUOUS":
                r["recommended_action"] = (
                    "Reconciler handles this automatically each monitor cycle. "
                    "If stuck >10 min, check Jupiter dashboard for the pubkey."
                )
            else:  # PENDING
                r["recommended_action"] = (
                    "Should self-resolve or expire to STALE_PENDING within 120s. "
                    "If stuck, check for crashed monitor loop."
                )
            result.append(r)

        return {"intents": result, "count": len(result)}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/intents/{intent_id}/resolve")
async def tiers_resolve_intent_ep(
    intent_id: int,
    body: Dict[str, Any],
    _: str = Depends(get_current_user),
):
    """
    Manually resolve a RECONCILE_MANUAL_REQUIRED execution intent.

    Body: {"resolution": "RECONCILED_CONFIRMED" | "RECONCILED_FAILED", "note": "..."}

    Safe: only updates the intent row status. Never opens positions or modifies
    perp_positions. Only operates on intents currently in RECONCILE_MANUAL_REQUIRED.
    """
    _ensure_engine_path()

    resolution = str(body.get("resolution", "")).strip()
    if resolution not in _RESOLVABLE_TO:
        raise HTTPException(
            status_code=400,
            detail=f"resolution must be one of: {sorted(_RESOLVABLE_TO)}",
        )
    note = str(body.get("note", ""))[:500]

    try:
        from utils.tier_manager import DB_PATH as _TM_DB  # type: ignore

        conn = sqlite3.connect(_TM_DB, timeout=10)
        conn.row_factory = sqlite3.Row

        row = conn.execute(
            "SELECT id, status, tier_label, symbol, position_pubkey FROM tier_execution_intents WHERE id=?",
            (intent_id,),
        ).fetchone()

        if not row:
            conn.close()
            raise HTTPException(status_code=404, detail=f"Intent {intent_id} not found")

        row = dict(row)
        if row["status"] != "RECONCILE_MANUAL_REQUIRED":
            conn.close()
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Intent {intent_id} has status '{row['status']}', "
                    f"not RECONCILE_MANUAL_REQUIRED. Only that status can be manually resolved."
                ),
            )

        resolved_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f+00:00")
        detail = (
            f"Manually resolved by operator to {resolution}."
            + (f" Note: {note}" if note else "")
        )

        conn.execute(
            """
            UPDATE tier_execution_intents
               SET status=?, resolved_ts=?, error_detail=?
             WHERE id=?
            """,
            (resolution, resolved_ts, detail, intent_id),
        )
        conn.commit()
        conn.close()

        log.info(
            "[TIER] Intent #%d manually resolved → %s (tier=%s sym=%s pubkey=%s). Note: %s",
            intent_id, resolution,
            row.get("tier_label"), row.get("symbol"), row.get("position_pubkey"), note,
        )

        return {
            "ok": True,
            "intent_id": intent_id,
            "resolved_to": resolution,
            "note": note,
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ── Patch 165: Recovery context — read-only operator helper ───────────────────

# Jupiter perps API base — mirrors tier_manager.PERPS_API; duplicated here to
# keep the live critical path (tier_manager) decoupled from operator tooling.
_PERPS_API = "https://perps-api.jup.ag/v2"


def _build_repair_sql(proposed: dict) -> str:
    """
    Patch 166: Build a copy-pasteable INSERT statement from proposed_fields.

    Pure function. Values of None become SQL NULL; strings are single-quote escaped.
    The operator must review and fill any NULL fields (entry_price, stop_price)
    before executing.
    """
    def _sql_val(v: Any) -> str:
        if v is None:
            return "NULL"
        if isinstance(v, bool):
            return "1" if v else "0"
        if isinstance(v, (int, float)):
            return str(v)
        return "'" + str(v).replace("'", "''") + "'"

    cols    = list(proposed.keys())
    col_str = ", ".join(cols)
    val_str = ", ".join(_sql_val(proposed[c]) for c in cols)
    return f"INSERT INTO perp_positions ({col_str})\nVALUES ({val_str});"


def _build_candidate_repair(
    intent: dict,
    local_pp: dict | None,
    jup_pos: dict | None,
) -> dict:
    """
    Build the candidate perp_positions repair payload for operator review.

    Pure function — no DB writes, no side effects. Returns a dict describing
    what the operator would need to INSERT (or whether to skip the INSERT).
    The operator MUST review proposed_fields before doing anything.

    Patch 166 additions:
      next_action — one-liner telling operator exactly what to do next
      repair_sql  — copy-pasteable INSERT statement (INSERT_NEW only; None otherwise)
    """
    pubkey = intent.get("position_pubkey") or ""

    if local_pp:
        return {
            "action": "RESOLVE_ONLY",
            "next_action": (
                f"perp_positions row #{local_pp['id']} already exists — "
                "verify it looks correct, then POST /resolve RECONCILED_CONFIRMED."
            ),
            "repair_sql": None,
            "proposed_fields": None,
            "notes": (
                f"perp_positions row #{local_pp['id']} already exists for this pubkey "
                f"(status={local_pp['status']}, entry_price={local_pp.get('entry_price')}). "
                "No INSERT needed — verify the row is correct, then "
                "POST /resolve with RECONCILED_CONFIRMED."
            ),
        }

    if not pubkey:
        return {
            "action": "NONE",
            "next_action": "No pubkey on this intent — provide position_pubkey before recovery is possible.",
            "repair_sql": None,
            "proposed_fields": None,
            "notes": "Intent has no position_pubkey — cannot verify or repair without it.",
        }

    # Derive fields from intent + Jupiter position data (all best-effort)
    collateral_usd = float(intent.get("collateral_usd") or 0.0)
    leverage       = float(intent.get("leverage") or 1.0)
    entry_price    = None

    if jup_pos:
        # Try every field name Jupiter has been observed to use
        raw_entry = (
            jup_pos.get("entryPrice")
            or jup_pos.get("entry_price")
            or jup_pos.get("averageEntryPrice")
            or jup_pos.get("avgEntryPrice")
        )
        if raw_entry is not None:
            try:
                entry_price = float(raw_entry)
            except (TypeError, ValueError):
                pass

        # Collateral from Jupiter if plausible (more accurate than stored intent value)
        raw_col = (
            jup_pos.get("collateralValue")
            or jup_pos.get("collateral")
            or jup_pos.get("sizeCollateral")
        )
        if raw_col is not None:
            try:
                col_val = float(raw_col)
                # Jupiter sometimes returns collateral in micro-USDC (1e6); normalise
                if collateral_usd > 0 and col_val > collateral_usd * 100:
                    col_val /= 1_000_000
                if col_val > 0:
                    collateral_usd = col_val
            except (TypeError, ValueError):
                pass

    size_usd = collateral_usd * leverage
    now_ts   = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f+00:00")

    proposed = {
        "opened_ts_utc":        intent.get("created_ts") or now_ts,
        "symbol":               intent.get("symbol"),
        "side":                 intent.get("side"),
        "entry_price":          entry_price,    # from Jupiter; None if unavailable — OPERATOR MUST VERIFY
        "stop_price":           None,           # OPERATOR MUST SET based on risk tolerance
        "size_usd":             size_usd,
        "leverage":             leverage,
        "collateral_usd":       collateral_usd,
        "status":               "OPEN",
        "dry_run":              0,
        "jupiter_position_key": pubkey,
        "tx_sig_open":          intent.get("presigned_tx_sig"),
        "notes":                f"Manually inserted by operator — intent #{intent['id']}",
    }

    repair_sql = _build_repair_sql(proposed)

    if jup_pos:
        action_notes = (
            "Jupiter confirmed this pubkey is live. Review proposed_fields carefully "
            "(especially entry_price and stop_price — stop_price is not set automatically). "
            "INSERT the row into perp_positions, then POST /resolve with RECONCILED_CONFIRMED."
        )
        next_action = (
            "Position live on Jupiter — fill stop_price, run repair_sql, "
            "then POST /resolve RECONCILED_CONFIRMED."
        )
    else:
        action_notes = (
            "Position NOT found in Jupiter API response (API error or position not live). "
            "If you have confirmed the position via the Jupiter UI: fill entry_price and "
            "stop_price manually, INSERT into perp_positions, then POST /resolve with "
            "RECONCILED_CONFIRMED. If the position does NOT exist: "
            "POST /resolve with RECONCILED_FAILED."
        )
        next_action = (
            "Position NOT found on Jupiter — confirm via Jupiter UI. "
            "If live: fill entry_price + stop_price in repair_sql and run it, then POST /resolve RECONCILED_CONFIRMED. "
            "If gone: POST /resolve RECONCILED_FAILED."
        )

    return {
        "action":          "INSERT_NEW",
        "next_action":     next_action,
        "repair_sql":      repair_sql,
        "proposed_fields": proposed,
        "notes":           action_notes,
    }


@router.get("/intents/{intent_id}/recovery-context")
async def tiers_intent_recovery_context(
    intent_id: int,
    _: str = Depends(get_current_user),
):
    """
    Full recovery context for a RECONCILE_MANUAL_REQUIRED execution intent.

    Read-only — no DB writes, no position mutations, no Jupiter actions.

    Returns:
      intent              — the full intent row
      local_perp_position — matching perp_positions row (by jupiter_position_key), if any
      jupiter_position    — raw Jupiter API data for the pubkey (best-effort; may be null)
      jupiter_fetch_error — error string if Jupiter was unreachable, else null
      candidate_repair    — action + proposed_fields for operator to review

    Operator workflow:
      1. GET /api/tiers/intents/{id}/recovery-context   ← gather facts (this endpoint)
      2. Review candidate_repair.proposed_fields
      3. If action=INSERT_NEW: operator INSERTs into perp_positions manually
      4. POST /api/tiers/intents/{id}/resolve           ← mark RECONCILED_CONFIRMED or FAILED
    """
    _ensure_engine_path()
    try:
        import requests as _req  # local import to avoid module-level dependency issues
        from utils.tier_manager import DB_PATH as _TM_DB  # type: ignore

        conn = sqlite3.connect(_TM_DB, timeout=10)
        conn.row_factory = sqlite3.Row

        intent_row = conn.execute(
            "SELECT * FROM tier_execution_intents WHERE id=?", (intent_id,)
        ).fetchone()
        if not intent_row:
            conn.close()
            raise HTTPException(status_code=404, detail=f"Intent {intent_id} not found")

        intent = dict(intent_row)
        pubkey = intent.get("position_pubkey") or ""

        # Fetch matching perp_positions row (read-only; by pubkey)
        local_pp = None
        if pubkey:
            pp_row = conn.execute(
                """
                SELECT id, status, symbol, side, entry_price, stop_price,
                       collateral_usd, leverage, size_usd,
                       opened_ts_utc, closed_ts_utc, jupiter_position_key, notes
                  FROM perp_positions
                 WHERE jupiter_position_key = ?
                 ORDER BY id DESC LIMIT 1
                """,
                (pubkey,),
            ).fetchone()
            if pp_row:
                local_pp = dict(pp_row)

        conn.close()

        # Fetch live Jupiter position data — best-effort, fully graceful on failure
        jupiter_position  = None
        jupiter_fetch_error = None
        if pubkey:
            try:
                from utils.jupiter_perps_trade import get_wallet_address  # type: ignore
                wallet = get_wallet_address()
                if not wallet:
                    jupiter_fetch_error = (
                        "Wallet address not configured (SOLANA_WALLET_ADDRESS env var missing)"
                    )
                else:
                    r = _req.get(
                        f"{_PERPS_API}/positions?walletAddress={wallet}",
                        timeout=10,
                    )
                    if r.status_code == 200:
                        data      = r.json()
                        positions = (
                            data if isinstance(data, list)
                            else (
                                data.get("dataList")
                                or data.get("positions")
                                or data.get("data")
                                or []
                            )
                        )
                        for p in positions:
                            pk = (
                                p.get("positionPubkey")
                                or p.get("address")
                                or p.get("position_pubkey")
                                or p.get("pubkey")
                            )
                            if pk == pubkey:
                                jupiter_position = p
                                break
                        if jupiter_position is None:
                            jupiter_fetch_error = (
                                f"Pubkey {pubkey[:20]}… not found in "
                                f"{len(positions)} Jupiter positions for this wallet"
                            )
                    else:
                        jupiter_fetch_error = f"Jupiter API returned HTTP {r.status_code}"
            except Exception as e:
                jupiter_fetch_error = str(e)[:300]

        candidate_repair = _build_candidate_repair(intent, local_pp, jupiter_position)

        log.info(
            "[TIER] Recovery context fetched for intent #%d (status=%s pubkey=%s jup_found=%s)",
            intent_id, intent.get("status"), pubkey[:16] if pubkey else "none",
            jupiter_position is not None,
        )

        return {
            "intent":              intent,
            "local_perp_position": local_pp,
            "jupiter_position":    jupiter_position,
            "jupiter_fetch_error": jupiter_fetch_error,
            "candidate_repair":    candidate_repair,
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
