"""
Funding Rate Monitor API — Patch 144

Routes:
  GET /api/funding/current
"""
from __future__ import annotations

import logging
import os
import sys

from fastapi import APIRouter, Depends

from auth import get_current_user  # type: ignore

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/funding", tags=["funding"])


def _ensure_path() -> None:
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if root not in sys.path:
        sys.path.insert(0, root)


@router.get("/current")
def get_funding_current(_user=Depends(get_current_user)):
    """Return cached funding rates for SOL, BTC, ETH from kv_store."""
    try:
        _ensure_path()
        from utils.funding_monitor import get_funding_rates  # type: ignore
        rates = get_funding_rates()
        data = {k: v for k, v in rates.items() if k != "_ts"}
        return {"rates": data, "ts": rates.get("_ts")}
    except Exception as e:
        log.warning("[FUND] /current error: %s", e)
        return {"rates": {}, "ts": None, "error": str(e)}
