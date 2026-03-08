"""
Smart Wallet Tracker API — Patch 145

Routes:
  GET    /api/wallets/list
  POST   /api/wallets/add
  DELETE /api/wallets/{wallet_id}
  GET    /api/wallets/buys
  GET    /api/wallets/accumulations
  GET    /api/wallets/stats
"""
from __future__ import annotations

import logging
import os
import sys

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from auth import get_current_user  # type: ignore

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/wallets", tags=["wallets"])


def _ensure_path() -> None:
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if root not in sys.path:
        sys.path.insert(0, root)


def _get_db():
    _ensure_path()
    from utils.db import get_conn  # type: ignore
    return get_conn()


# ── Schemas ────────────────────────────────────────────────────────────────────

class AddWalletBody(BaseModel):
    address: str
    label: str = "Unknown"


# ── Helpers ────────────────────────────────────────────────────────────────────

def _phase_info(total_buys: int) -> dict:
    if total_buys < 10:
        return {"phase": "OBSERVE",  "label": "Building dataset",       "next": 10}
    if total_buys < 50:
        return {"phase": "ANALYZE",  "label": "Win rates emerging",      "next": 50}
    return     {"phase": "SIGNAL",   "label": "Telegram alerts active",  "next": None}


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/list")
def get_wallet_list(_user=Depends(get_current_user)):
    """Return all tracked wallets with stats."""
    try:
        _ensure_path()
        from utils.smart_wallet_tracker import get_wallet_list  # type: ignore
        return {"wallets": get_wallet_list()}
    except Exception as e:
        log.warning("[WALLETS] /list error: %s", e)
        return {"wallets": [], "error": str(e)}


@router.post("/add")
def add_wallet(body: AddWalletBody, _user=Depends(get_current_user)):
    """Add a new tracked wallet."""
    try:
        _ensure_path()
        from utils.smart_wallet_tracker import add_wallet as _add  # type: ignore
        result = _add(body.address.strip(), body.label.strip())
        if not result["ok"]:
            raise HTTPException(status_code=409, detail=result["error"])
        return result
    except HTTPException:
        raise
    except Exception as e:
        log.warning("[WALLETS] /add error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{wallet_id}")
def remove_wallet(wallet_id: int, _user=Depends(get_current_user)):
    """Soft-delete a tracked wallet (sets active=0, retains history)."""
    try:
        _ensure_path()
        from utils.smart_wallet_tracker import remove_wallet as _remove  # type: ignore
        ok = _remove(wallet_id)
        if not ok:
            raise HTTPException(status_code=404, detail="Wallet not found")
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        log.warning("[WALLETS] /delete error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/buys")
def get_buys(limit: int = 50, wallet_id: int | None = None,
             _user=Depends(get_current_user)):
    """Return recent buy feed, optionally filtered by wallet."""
    try:
        with _get_db() as conn:
            if wallet_id is not None:
                # resolve address from id
                w = conn.execute(
                    "SELECT address FROM smart_wallets WHERE id=?", (wallet_id,)
                ).fetchone()
                if not w:
                    return {"buys": []}
                rows = conn.execute("""
                    SELECT * FROM smart_wallet_buys
                    WHERE wallet_address=?
                    ORDER BY ts_utc DESC LIMIT ?
                """, (w[0], min(limit, 200))).fetchall()
            else:
                rows = conn.execute("""
                    SELECT * FROM smart_wallet_buys
                    WHERE wallet_address IN (SELECT address FROM smart_wallets WHERE active=1)
                    ORDER BY ts_utc DESC LIMIT ?
                """, (min(limit, 200),)).fetchall()
            return {"buys": [dict(r) for r in rows]}
    except Exception as e:
        log.warning("[WALLETS] /buys error: %s", e)
        return {"buys": [], "error": str(e)}


@router.get("/accumulations")
def get_accumulations(limit: int = 20, _user=Depends(get_current_user)):
    """Return recent accumulation events."""
    try:
        with _get_db() as conn:
            rows = conn.execute("""
                SELECT * FROM smart_wallet_accumulations
                ORDER BY ts_utc DESC LIMIT ?
            """, (min(limit, 100),)).fetchall()
            return {"accumulations": [dict(r) for r in rows]}
    except Exception as e:
        log.warning("[WALLETS] /accumulations error: %s", e)
        return {"accumulations": [], "error": str(e)}


@router.get("/stats")
def get_stats(_user=Depends(get_current_user)):
    """Return summary stats and phase info."""
    try:
        with _get_db() as conn:
            total_wallets = conn.execute(
                "SELECT COUNT(*) FROM smart_wallets WHERE active=1"
            ).fetchone()[0]
            # Scope all stats to ACTIVE wallets only — inactive/removed wallets
            # shouldn't inflate the phase or win rate (Patch 147)
            _active_filter = """
                wallet_address IN (SELECT address FROM smart_wallets WHERE active=1)
            """
            total_buys = conn.execute(
                f"SELECT COUNT(*) FROM smart_wallet_buys WHERE {_active_filter}"
            ).fetchone()[0]
            complete_buys = conn.execute(
                f"SELECT COUNT(*) FROM smart_wallet_buys WHERE outcome_status='COMPLETE' AND {_active_filter}"
            ).fetchone()[0]
            accumulations = conn.execute(
                "SELECT COUNT(*) FROM smart_wallet_accumulations"
            ).fetchone()[0]
            wr_24h = conn.execute(f"""
                SELECT ROUND(AVG(CASE WHEN return_24h_pct > 0 THEN 1.0 ELSE 0.0 END)*100, 1)
                FROM smart_wallet_buys WHERE outcome_status='COMPLETE' AND {_active_filter}
            """).fetchone()[0]

        phase = _phase_info(total_buys)
        return {
            "total_wallets":   total_wallets,
            "total_buys":      total_buys,
            "complete_buys":   complete_buys,
            "accumulations":   accumulations,
            "wr_24h":          wr_24h,
            "phase":           phase["phase"],
            "phase_label":     phase["label"],
            "next_milestone":  phase["next"],
        }
    except Exception as e:
        log.warning("[WALLETS] /stats error: %s", e)
        return {
            "total_wallets": 0, "total_buys": 0, "complete_buys": 0,
            "accumulations": 0, "wr_24h": None,
            "phase": "OBSERVE", "phase_label": "Building dataset", "next_milestone": 10,
            "error": str(e),
        }
