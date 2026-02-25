#!/usr/bin/env python3
import argparse
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"

sys.path.insert(0, str(ROOT))
from utils.db import get_weekly_tuning_report, init_db  # noqa: E402


def _parse_env(path: Path) -> dict:
    values = {}
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(value, high))


def _to_int(values: dict, key: str, default: int) -> int:
    try:
        return int(values.get(key, default))
    except (TypeError, ValueError):
        return default


def _rewrite_env(path: Path, updates: dict):
    lines = path.read_text().splitlines()
    updated_lines = []
    seen = set()

    for line in lines:
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in line:
            updated_lines.append(line)
            continue

        key, _ = line.split("=", 1)
        key = key.strip()
        if key in updates:
            updated_lines.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            updated_lines.append(line)

    for key, value in updates.items():
        if key not in seen:
            updated_lines.append(f"{key}={value}")

    path.write_text("\n".join(updated_lines) + "\n")


def main():
    parser = argparse.ArgumentParser(
        description="Apply weekly tuning recommendations to .env with safety bounds."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview recommendations without writing .env.",
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Reload launchd agent after applying updates.",
    )
    parser.add_argument(
        "--min-scan-runs",
        type=int,
        default=10,
        help="Minimum SCAN_RUN samples required to auto-apply recommendations.",
    )
    args = parser.parse_args()

    if not ENV_PATH.exists():
        print(f"Missing .env file at {ENV_PATH}")
        return 1

    env_values = _parse_env(ENV_PATH)
    init_db()
    current_alert_threshold = _to_int(env_values, "ALERT_THRESHOLD", 70)
    current_regime_min_score = _to_int(env_values, "REGIME_MIN_SCORE", 50)
    current_confidence = env_values.get("MIN_CONFIDENCE_TO_ALERT", "B").strip().upper()
    lookback_days = _to_int(env_values, "WEEKLY_TUNING_LOOKBACK_DAYS", 7)
    min_outcomes_4h = _to_int(env_values, "WEEKLY_TUNING_MIN_OUTCOMES_4H", 8)

    report = get_weekly_tuning_report(
        lookback_days=lookback_days,
        current_alert_threshold=current_alert_threshold,
        current_regime_min_score=current_regime_min_score,
        current_min_confidence_to_alert=current_confidence,
        min_outcomes_4h=min_outcomes_4h,
    )

    print(
        f"Weekly tuning input: scan_runs={report['scan_runs']} alerts={report['alerts']} "
        f"alert_rate={report['alert_rate']:.1f}% block_rate={report['block_rate']:.1f}%"
    )

    if report["scan_runs"] < args.min_scan_runs:
        print(
            f"Not enough data to auto-apply ({report['scan_runs']} < {args.min_scan_runs}). "
            "Use --min-scan-runs 0 to override."
        )
        return 2

    rec = report["recommended"]
    next_alert_threshold = _clamp(int(rec["alert_threshold"]), 55, 95)
    next_regime_min_score = _clamp(int(rec["regime_min_score"]), 35, 70)
    next_confidence = str(rec["min_confidence_to_alert"]).strip().upper()
    if next_confidence not in {"A", "B", "C"}:
        next_confidence = current_confidence if current_confidence in {"A", "B", "C"} else "B"

    updates = {
        "ALERT_THRESHOLD": str(next_alert_threshold),
        "REGIME_MIN_SCORE": str(next_regime_min_score),
        "MIN_CONFIDENCE_TO_ALERT": next_confidence,
    }

    print("Proposed updates:")
    print(f"  ALERT_THRESHOLD: {current_alert_threshold} -> {updates['ALERT_THRESHOLD']}")
    print(f"  REGIME_MIN_SCORE: {current_regime_min_score} -> {updates['REGIME_MIN_SCORE']}")
    print(f"  MIN_CONFIDENCE_TO_ALERT: {current_confidence} -> {updates['MIN_CONFIDENCE_TO_ALERT']}")
    print("Rationale:")
    for reason in report["reasons"]:
        print(f"  - {reason}")

    if args.dry_run:
        print("Dry run complete. No file changes applied.")
        return 0

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup_path = ROOT / f".env.bak.{ts}"
    shutil.copy2(ENV_PATH, backup_path)
    _rewrite_env(ENV_PATH, updates)

    print(f"Backup written: {backup_path}")
    print(f"Updated: {ENV_PATH}")

    if args.reload:
        install_script = ROOT / "scripts" / "install_launchd.sh"
        subprocess.run([str(install_script)], cwd=str(ROOT), check=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
