"""
Shared helpers for all router modules.

NOTE: router files live at dashboard/backend/routers/*.py, so the engine root
is parents[3] (one level deeper than main.py's parents[2]).
"""
from __future__ import annotations

from pathlib import Path


def _engine_root() -> str:
    """Absolute path to the engine root (/root/memecoin_engine or local equivalent)."""
    return str(Path(__file__).resolve().parents[3])


def _ensure_engine_path() -> None:
    """Add engine root to sys.path so utils.* modules are importable."""
    import sys
    root = _engine_root()
    if root not in sys.path:
        sys.path.insert(0, root)


def _db_path() -> Path:
    """Canonical path to the SQLite database."""
    return Path(_engine_root()) / "data_storage" / "engine.db"
