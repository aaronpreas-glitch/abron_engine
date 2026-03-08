"""
Tier system endpoints — Patch 114.

Routes:
  GET  /api/tiers/status                         — tier positions, profit buffer, and config
  POST /api/tiers/open-all                       — open 3x, 5x, and 10x simultaneously
  POST /api/tiers/open/{tier_label}              — open a single tier (3x | 5x | 10x)
  GET  /api/tiers/intents                        — list unresolved execution intents (Patch 163)
  POST /api/tiers/intents/{id}/resolve           — manually resolve RECONCILE_MANUAL_REQUIRED (Patch 163)
  GET  /api/tiers/intents/{id}/recovery-context  — full operator recovery context, read-only (Patch 165)
                                                   Patch 166: enriched with next_action + repair_sql
"""
from __future__ import annotations

import asyncio
import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException

from auth import get_current_user
from routers._shared import _ensure_engine_path

log = logging.getLogger("dashboard")
router = APIRouter(prefix="/api/tiers", tags=["tiers"])


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
