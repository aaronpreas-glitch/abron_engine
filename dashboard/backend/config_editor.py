"""
config_editor.py — Read and safely update .env config keys.

Only 5 keys are editable via the dashboard. Every write:
  1. Validates the new values (type + range).
  2. Creates a timestamped .env backup.
  3. Rewrites .env preserving all other keys.
  4. Restarts the memecoin-engine systemd service.
"""
from __future__ import annotations

import logging
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger("dashboard.config")

_ENV_PATH = Path(__file__).resolve().parents[2] / ".env"

# Editable keys: (type, min, max, allowed_values)
_EDITABLE_KEYS: dict[str, dict] = {
    "ALERT_THRESHOLD": {"type": int, "min": 55, "max": 95},
    "REGIME_MIN_SCORE": {"type": int, "min": 10, "max": 70},
    "MIN_CONFIDENCE_TO_ALERT": {"type": str, "allowed": ["A", "B", "C"]},
    "MAX_ALERTS_PER_CYCLE": {"type": int, "min": 1, "max": 10},
    "PORTFOLIO_USD": {"type": float, "min": 100.0, "max": 1_000_000.0},
}


def _read_env() -> dict[str, str]:
    """Read .env as ordered key→value dict, preserving comments."""
    result = {}
    if not _ENV_PATH.exists():
        return result
    for line in _ENV_PATH.read_text().splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, val = stripped.partition("=")
        result[key.strip()] = val.strip()
    return result


def _write_env(values: dict[str, str]) -> None:
    """Rewrite .env preserving existing lines, only updating known keys."""
    if not _ENV_PATH.exists():
        raise FileNotFoundError(f".env not found at {_ENV_PATH}")
    lines = _ENV_PATH.read_text().splitlines()
    updated_keys: set[str] = set()
    new_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#") or "=" not in stripped:
            new_lines.append(line)
            continue
        key = stripped.split("=", 1)[0].strip()
        if key in values:
            new_lines.append(f"{key}={values[key]}")
            updated_keys.add(key)
        else:
            new_lines.append(line)
    # Append any keys not already in file
    for key, val in values.items():
        if key not in updated_keys:
            new_lines.append(f"{key}={val}")
    _ENV_PATH.write_text("\n".join(new_lines) + "\n")


def get_config() -> dict[str, Any]:
    """Return current values of all editable keys from .env."""
    env = _read_env()
    result = {}
    for key, meta in _EDITABLE_KEYS.items():
        raw = env.get(key, "")
        try:
            if meta["type"] == int:
                result[key] = int(raw)
            elif meta["type"] == float:
                result[key] = float(raw)
            else:
                result[key] = raw
        except (ValueError, TypeError):
            result[key] = raw
    return result


def validate_and_update(new_values: dict[str, Any]) -> dict[str, Any]:
    """
    Validate, backup .env, write updated keys, restart bot service.
    Returns {"success": bool, "errors": list, "restarted": bool}.
    """
    errors = []
    coerced: dict[str, str] = {}

    for key, raw_val in new_values.items():
        if key not in _EDITABLE_KEYS:
            errors.append(f"Key '{key}' is not editable via the dashboard.")
            continue
        meta = _EDITABLE_KEYS[key]
        try:
            val = meta["type"](raw_val)
        except (ValueError, TypeError):
            errors.append(f"{key}: cannot convert '{raw_val}' to {meta['type'].__name__}.")
            continue
        if "allowed" in meta and val not in meta["allowed"]:
            errors.append(f"{key}: must be one of {meta['allowed']}, got '{val}'.")
            continue
        if "min" in meta and val < meta["min"]:
            errors.append(f"{key}: must be >= {meta['min']}, got {val}.")
            continue
        if "max" in meta and val > meta["max"]:
            errors.append(f"{key}: must be <= {meta['max']}, got {val}.")
            continue
        coerced[key] = str(val)

    if errors:
        return {"success": False, "errors": errors, "restarted": False}

    # Backup current .env
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup_path = _ENV_PATH.parent / f".env.bak.{ts}"
    try:
        backup_path.write_text(_ENV_PATH.read_text())
        log.info("Backed up .env → %s", backup_path)
    except OSError as exc:
        log.warning("Could not create .env backup: %s", exc)

    # Write updated values
    _write_env(coerced)
    log.info("Updated .env keys: %s", list(coerced.keys()))

    # Restart the engine service
    restarted = False
    try:
        subprocess.run(
            ["systemctl", "restart", "memecoin-engine"],
            check=True,
            timeout=15,
            capture_output=True,
        )
        restarted = True
        log.info("memecoin-engine service restarted.")
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as exc:
        log.warning("Could not restart memecoin-engine: %s", exc)

    return {"success": True, "errors": [], "restarted": restarted, "updated_keys": list(coerced.keys())}
