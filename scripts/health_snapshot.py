#!/usr/bin/env python3
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from utils.db import (  # noqa: E402
    get_engine_health_snapshot,
    get_outcome_queue_stats,
    get_performance_summary,
    get_portfolio_simulation_metrics,
    get_risk_pause_state,
    get_symbol_controls_summary,
    init_db,
)


def _get_launchd_status(label: str):
    launchd_target = f"gui/{os.getuid()}/{label}"
    try:
        proc = subprocess.run(
            ["launchctl", "print", launchd_target],
            check=False,
            capture_output=True,
            text=True,
        )
    except Exception as exc:
        return {"running": False, "error": str(exc)}

    out = proc.stdout
    if proc.returncode != 0:
        return {"running": False, "pid": None, "last_exit_code": None, "label": label}

    state_match = re.search(r"^\s*state = (\S+)\s*$", out, flags=re.MULTILINE)
    pid_match = re.search(r"^\s*pid = (\d+)\s*$", out, flags=re.MULTILINE)
    last_exit_match = re.search(r"^\s*last exit code = (.+)\s*$", out, flags=re.MULTILINE)

    state = state_match.group(1) if state_match else ""
    pid = int(pid_match.group(1)) if pid_match else None
    last_exit_raw = (last_exit_match.group(1).strip() if last_exit_match else "")
    if last_exit_raw and last_exit_raw != "(never exited)":
        digits = re.findall(r"-?\d+", last_exit_raw)
        last_exit_code = int(digits[0]) if digits else None
    else:
        last_exit_code = None

    return {
        "running": state == "running" and pid is not None,
        "pid": pid,
        "last_exit_code": last_exit_code,
        "label": label,
    }


def _iso(dt):
    if not dt:
        return None
    return dt.replace(tzinfo=timezone.utc).isoformat()


def main():
    init_db()
    health = get_engine_health_snapshot()
    perf_24h = get_performance_summary(lookback_hours=24)
    portfolio_7d_4h = get_portfolio_simulation_metrics(lookback_days=7, horizon_hours=4)
    queue = get_outcome_queue_stats()
    risk_pause = get_risk_pause_state()
    symbol_controls = get_symbol_controls_summary()
    launchd = _get_launchd_status("com.memecoin.engine")

    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "service": launchd,
        "last_scan_run_utc": _iso(health.get("last_scan_run")),
        "last_alert_utc": _iso(health.get("last_alert")),
        "performance_24h": perf_24h,
        "portfolio_sim_7d_4h": portfolio_7d_4h,
        "outcome_queue": queue,
        "risk_pause_until_utc": _iso(risk_pause.get("pause_until")),
        "risk_pause_reason": risk_pause.get("reason"),
        "symbol_controls": symbol_controls,
    }
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
