"""Abrons Orchestrator — Agent Registry & Shared Memory (Patch 91).

Provides:
  - AgentRegistry: heartbeat tracking and health status for the 7 named agents
  - MEMORY.md: rolling append-only event log shared across all agents
  - RESEARCH.md: overwritten each 4h research cycle

Designed to be imported by dashboard/backend/main.py with zero side-effects
at import time (no network calls, no file I/O until functions are called).
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────────────
ENGINE_ROOT: Path = Path(__file__).resolve().parent.parent
MEMORY_FILE: Path = ENGINE_ROOT / "MEMORY.md"
RESEARCH_FILE: Path = ENGINE_ROOT / "RESEARCH.md"
CONFIG_FILE: Path = ENGINE_ROOT / "orchestrator_config.json"

MAX_MEMORY_LINES: int = 5000   # trim tail when file exceeds this

# ── Agent Registry ─────────────────────────────────────────────────────────────
# Each entry: interval_s = expected heartbeat interval (used to compute staleness)
_agents: dict[str, dict] = {
    # Patch 120 — clean registry: only agents with real step functions and heartbeats
    # Legacy swing-trading agents (monitoring, trading, watchdog, optimizer, scalp_scan,
    # alert) removed — they were never wired to step functions in the current architecture.
    "health_watchdog":  {"interval_s": 60,    "last_beat": 0.0, "status": "init"},  # Patch 118
    "data_integrity":   {"interval_s": 300,   "last_beat": 0.0, "status": "init"},  # Patch 120
    "memecoin_scan":    {"interval_s": 300,   "last_beat": 0.0, "status": "init"},  # Patch 116
    "memecoin_monitor": {"interval_s": 60,    "last_beat": 0.0, "status": "init"},  # Patch 116
    "tier_monitor":     {"interval_s": 60,    "last_beat": 0.0, "status": "init"},  # Patch 120
    "research":         {"interval_s": 14400, "last_beat": 0.0, "status": "init"},  # Patch 120
    "spot_monitor":     {"interval_s": 300,   "last_beat": 0.0, "status": "init"},  # Patch 128
    "whale_watch":      {"interval_s": 120,   "last_beat": 0.0, "status": "init"},  # Patch 139
    "confluence_engine":{"interval_s": 300,   "last_beat": 0.0, "status": "init"},  # Patch 143
    "funding_monitor":  {"interval_s": 1800,  "last_beat": 0.0, "status": "init"},  # Patch 144
    "smart_wallet_tracker": {"interval_s": 300, "last_beat": 0.0, "status": "init"},  # Patch 145
}

_mem_lock = threading.Lock()   # protect concurrent writes to MEMORY.md

# ── Health check & data integrity state (Patch 96) ────────────────────────────
_health_check: dict = {}
_data_integrity: dict = {}
_hc_lock = threading.Lock()
_di_lock = threading.Lock()
_checklist_result: dict = {}
_cl_lock = threading.Lock()


def heartbeat(agent: str) -> None:
    """Mark an agent as alive with the current timestamp."""
    if agent in _agents:
        _agents[agent]["last_beat"] = time.time()
        _agents[agent]["status"] = "alive"


def get_status() -> list[dict]:
    """Return a list of agent health dicts.

    health values: "init" | "alive" | "slow" | "stalled"
    """
    now = time.time()
    result: list[dict] = []
    for name, d in _agents.items():
        age_s = now - d["last_beat"] if d["last_beat"] > 0 else 999_999
        interval = d["interval_s"]
        if d["last_beat"] == 0.0:
            health = "init"
        elif age_s < interval * 1.5:
            health = "alive"
        elif age_s < interval * 3.0:
            health = "slow"
        else:
            health = "stalled"
        result.append({
            "name": name,
            "health": health,
            "interval_s": interval,
            "last_beat_ago_s": round(age_s) if d["last_beat"] > 0 else None,
            "status": d["status"],
        })
    return result


# ── Memory helpers ─────────────────────────────────────────────────────────────

def append_memory(agent: str, msg: str) -> None:
    """Append a timestamped entry to MEMORY.md.

    Format:
        ## [2026-02-28 03:30:00 UTC] WATCHDOG
        All 8 loops alive | Open=0 | DD=0.00%
    """
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    entry = f"\n## [{ts}] {agent.upper()}\n{msg}\n"
    with _mem_lock:
        with open(MEMORY_FILE, "a", encoding="utf-8") as fh:
            fh.write(entry)
        # Trim if file has grown too large
        try:
            lines = MEMORY_FILE.read_text(encoding="utf-8").splitlines()
            if len(lines) > MAX_MEMORY_LINES:
                MEMORY_FILE.write_text(
                    "\n".join(lines[-MAX_MEMORY_LINES:]), encoding="utf-8"
                )
        except Exception:
            pass  # best-effort trim


def read_memory(lines: int = 50) -> str:
    """Return the last N lines of MEMORY.md as a single string."""
    if not MEMORY_FILE.exists():
        return "(no memory yet — MEMORY.md will be created on first event)"
    try:
        all_lines = MEMORY_FILE.read_text(encoding="utf-8").splitlines()
        return "\n".join(all_lines[-max(1, lines):])
    except Exception as exc:
        return f"(error reading MEMORY.md: {exc})"


def write_research(content: str) -> None:
    """Overwrite RESEARCH.md with new research digest content."""
    try:
        RESEARCH_FILE.write_text(content, encoding="utf-8")
    except Exception:
        pass


def read_research() -> str:
    """Return the full contents of RESEARCH.md."""
    if not RESEARCH_FILE.exists():
        return "(no research digest yet — trigger one via POST /api/orchestrator/trigger-research)"
    try:
        return RESEARCH_FILE.read_text(encoding="utf-8")
    except Exception as exc:
        return f"(error reading RESEARCH.md: {exc})"


def set_health_status(result: dict) -> None:
    """Store the latest health check result (Patch 96)."""
    global _health_check
    with _hc_lock:
        _health_check = result


def get_health_status() -> dict:
    """Return the latest health check result (Patch 96)."""
    with _hc_lock:
        return _health_check.copy()


def set_data_integrity_status(result: dict) -> None:
    """Store the latest data integrity check result (Patch 96)."""
    global _data_integrity
    with _di_lock:
        _data_integrity = result


def get_data_integrity_status() -> dict:
    """Return the latest data integrity check result (Patch 96)."""
    with _di_lock:
        return _data_integrity.copy()


def set_checklist_result(result: dict) -> None:
    """Store the latest daily checklist result (Patch 97)."""
    global _checklist_result
    with _cl_lock:
        _checklist_result = result


def get_checklist_result() -> dict:
    """Return the latest daily checklist result (Patch 97)."""
    with _cl_lock:
        return _checklist_result.copy()


def load_config() -> dict:
    """Load orchestrator_config.json, returning defaults on error."""
    defaults: dict = {
        "agents": {
            "monitoring": {"enabled": True,  "telegram_on_trade": True,  "telegram_on_signal": False},
            "research":   {"enabled": True,  "interval_hours": 4, "telegram_on_complete": True},
            "watchdog":   {"enabled": True,  "stall_multiplier": 2.5, "dd_alert_pct": 3.0},
            "optimizer":  {"enabled": True},
            "alert":      {"enabled": True,  "telegram": True, "slack": True},
        },
        "memory": {"max_lines": 5000},
        "version": "91",
    }
    try:
        import json
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return defaults
