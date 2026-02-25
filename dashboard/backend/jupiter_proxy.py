"""
jupiter_proxy.py — Thin wrapper that imports from the engine's jupiter_perps.py
and exposes simple functions for the dashboard API.

All heavy logic (API calls, DCA file I/O, calculations) stays in the engine module.
The dashboard backend just calls these helpers inside asyncio.to_thread() calls.
"""
from __future__ import annotations

import importlib.util
import os
import sys
import logging

log = logging.getLogger("dashboard.jupiter")

# ---------------------------------------------------------------------------
# Bootstrap: add engine root to sys.path so we can import jupiter_perps
# ---------------------------------------------------------------------------

_ENGINE_ROOT = os.path.realpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..")
)
if _ENGINE_ROOT not in sys.path:
    sys.path.insert(0, _ENGINE_ROOT)

try:
    import jupiter_perps as _jp
    _AVAILABLE = True
    log.info("jupiter_perps imported OK from %s", _ENGINE_ROOT)
except ImportError as exc:
    _AVAILABLE = False
    _jp = None  # type: ignore
    log.warning("jupiter_perps not importable: %s — perps/DCA endpoints will return empty data", exc)


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def get_perps_position() -> dict | None:
    """Fetch and normalize the current Jupiter Perps SOL-LONG position."""
    if not _AVAILABLE:
        return None
    try:
        return _jp.fetch_jupiter_position()
    except Exception as exc:
        log.warning("fetch_jupiter_position error: %s", exc)
        return None


def get_sol_price() -> float | None:
    """Return current SOL/USD price from DexScreener."""
    if not _AVAILABLE:
        return None
    try:
        return _jp.fetch_sol_price()
    except Exception as exc:
        log.warning("fetch_sol_price error: %s", exc)
        return None


def get_dca_summary(current_sol_price: float) -> dict:
    """
    Return full DCA tracker summary dict including entries and aggregate stats.
    Returns empty summary if no entries exist.
    """
    if not _AVAILABLE:
        return _empty_dca()
    try:
        entries = _jp.get_dca_entries()
        if not entries:
            return _empty_dca()
        summary = _jp._calc_dca_summary(current_sol_price)  # type: ignore[attr-defined]
        return {
            "entries": entries,
            "summary": summary,
        }
    except Exception as exc:
        log.warning("get_dca_summary error: %s", exc)
        return _empty_dca()


def add_dca_entry_proxy(amount_usd: float, sol_price: float, leverage: float = 1.0) -> dict:
    """Add a new DCA entry and return the entry dict."""
    if not _AVAILABLE:
        raise RuntimeError("jupiter_perps not available")
    return _jp.add_dca_entry(amount_usd, sol_price, leverage)


def clear_dca_entries() -> None:
    """Clear all DCA entries."""
    if not _AVAILABLE:
        return
    try:
        from pathlib import Path
        dca_file: Path = _jp._DCA_FILE  # type: ignore[attr-defined]
        if dca_file.exists():
            dca_file.unlink()
    except Exception as exc:
        log.warning("clear_dca_entries error: %s", exc)


def _empty_dca() -> dict:
    return {"entries": [], "summary": None}
