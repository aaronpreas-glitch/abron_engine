#!/usr/bin/env python3
"""
auto_tune.py — Weekly autonomous self-tuner for the Memecoin Engine.

Runs every Monday at 09:00 UTC via cron. Analyzes the last 14 days of
outcome data, generates threshold recommendations via the existing optimizer,
applies changes if the safety gates pass, and sends a Telegram report
regardless of outcome.

Cron entry (add to VPS crontab):
    0 9 * * 1 /root/memecoin_engine/.venv/bin/python /root/memecoin_engine/auto_tune.py >> /root/memecoin_engine/logs/auto_tune.log 2>&1

Safety rules:
  - Requires ≥10 scan_runs AND ≥5 evaluated 4h outcomes to apply anything
  - Only applies if threshold changes ≥3pts, regime ≥3pts, or confidence changes
  - Hard bounds: threshold [55–95], regime [35–70], confidence {A,B,C}
  - Backs up .env before every write
  - Writes audit log on every run — even skips
  - Never touches PORTFOLIO_USD or position sizing params

Phase 2 additions:
  - Per-lane win rate analysis (new_runner / legacy / watchlist / launch)
  - LAUNCH_MIN_SCORE auto-tuning when launch lane outperforms new_runner by 10+ pts wr_4h
  - Score component correlation analysis via utils/score_analyzer.py
  - SCORE_WEIGHTS + DYNAMIC_HOT_KEYWORDS applied when 3+ consistent weeks confirm direction
  - Exit learnings integration: best exit rule per (regime × exit_reason) in exit_profiles.json
  - Staleness decay: alert_outcomes > 30 days old weighted 20% less in get_pattern_win_rate
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx

# ── Paths ─────────────────────────────────────────────────────────────────────

BASE_DIR    = Path(__file__).resolve().parent
ENV_PATH    = BASE_DIR / ".env"
LOG_PATH    = BASE_DIR / "data_storage" / "tuning_log.json"
LOGS_DIR    = BASE_DIR / "logs"

sys.path.insert(0, str(BASE_DIR))

log = logging.getLogger("auto_tune")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)

# ── Safety bounds ──────────────────────────────────────────────────────────────

MIN_SCAN_RUNS   = 10    # min SCAN_RUN rows in lookback to trust the data
MIN_OUTCOMES_4H = 5     # min evaluated 4h outcomes to trust the optimizer
MIN_DELTA_INT   = 3     # min integer change in threshold/regime to bother applying
LOOKBACK_DAYS   = 14    # how far back to look when analyzing performance

THRESHOLD_MIN   = 55
THRESHOLD_MAX   = 95
REGIME_MIN      = 35
REGIME_MAX      = 70
VALID_CONF      = {"A", "B", "C"}

# ── Phase 2 tuning bounds ──────────────────────────────────────────────────────

LAUNCH_SCORE_MIN  = 50    # floor for LAUNCH_MIN_SCORE auto-tuning
LAUNCH_SCORE_MAX  = 85    # ceiling for LAUNCH_MIN_SCORE auto-tuning
LAUNCH_SCORE_STEP = 3     # lower by this many points when launch outperforms
MIN_LANE_N        = 10    # min outcomes per lane before tuning LAUNCH_MIN_SCORE
LANE_OUTPERFORM_PTS = 10  # launch wr_4h must beat new_runner by this many points
EXIT_PROFILES_PATH = BASE_DIR / "data_storage" / "exit_profiles.json"


# ── .env helpers ──────────────────────────────────────────────────────────────

def _load_env() -> None:
    """Load key=value pairs from .env into os.environ if not already set."""
    if not ENV_PATH.exists():
        return
    for line in ENV_PATH.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


def _parse_env() -> dict[str, str]:
    values: dict[str, str] = {}
    if not ENV_PATH.exists():
        return values
    for line in ENV_PATH.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        values[k.strip()] = v.strip().strip('"').strip("'")
    return values


def _rewrite_env(updates: dict[str, str]) -> Path:
    """Apply updates dict to .env, return path of .env.bak file created."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    bak = BASE_DIR / f".env.bak.{ts}"
    shutil.copy2(ENV_PATH, bak)

    lines = ENV_PATH.read_text().splitlines()
    out, seen = [], set()
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            out.append(line)
            continue
        k, _, _ = line.partition("=")
        k = k.strip()
        if k in updates:
            out.append(f"{k}={updates[k]}")
            seen.add(k)
        else:
            out.append(line)
    for k, v in updates.items():
        if k not in seen:
            out.append(f"{k}={v}")
    ENV_PATH.write_text("\n".join(out) + "\n")
    return bak


# ── Audit log ─────────────────────────────────────────────────────────────────

def _append_log(entry: dict) -> None:
    """Append one JSON entry to the audit log file."""
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    existing: list[dict] = []
    if LOG_PATH.exists():
        try:
            existing = json.loads(LOG_PATH.read_text())
        except Exception:
            existing = []
    existing.append(entry)
    LOG_PATH.write_text(json.dumps(existing, indent=2))


# ── Telegram ──────────────────────────────────────────────────────────────────

async def _send_telegram(text: str) -> None:
    token   = os.environ.get("TELEGRAM_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        log.warning("TELEGRAM_TOKEN or TELEGRAM_CHAT_ID not set — skipping Telegram.")
        return
    if len(text) > 4000:
        text = text[:3997] + "…"
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
        )
        if r.status_code == 200:
            log.info("Telegram message sent.")
        else:
            log.error("Telegram send failed: %s %s", r.status_code, r.text[:200])


# ── Telegram message builders ─────────────────────────────────────────────────

def _msg_applied(before: dict, after: dict, metrics: dict, reasons: list[str]) -> str:
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    changes = []
    if before["ALERT_THRESHOLD"] != after["ALERT_THRESHOLD"]:
        changes.append(f"Threshold: <b>{before['ALERT_THRESHOLD']} → {after['ALERT_THRESHOLD']}</b>")
    if before["REGIME_MIN_SCORE"] != after["REGIME_MIN_SCORE"]:
        changes.append(f"Regime gate: <b>{before['REGIME_MIN_SCORE']} → {after['REGIME_MIN_SCORE']}</b>")
    if before["MIN_CONFIDENCE_TO_ALERT"] != after["MIN_CONFIDENCE_TO_ALERT"]:
        changes.append(f"Min confidence: <b>{before['MIN_CONFIDENCE_TO_ALERT']} → {after['MIN_CONFIDENCE_TO_ALERT']}</b>")

    changes_str = "\n".join(f"  • {c}" for c in changes)
    reasons_str = "\n".join(f"  — {r}" for r in reasons[:4])
    wr  = round(metrics.get("win_rate_4h", 0), 1)
    avg = round(metrics.get("avg_return_4h", 0), 2)
    n   = metrics.get("outcomes_4h", 0)

    return (
        f"🔧 <b>Auto-tune applied</b> — {date}\n\n"
        f"{changes_str}\n\n"
        f"<b>Why:</b>\n{reasons_str}\n\n"
        f"<b>Data:</b> {n} outcomes · win rate {wr}% · avg 4h {avg:+.2f}%\n"
        f"New config is live. Engine restarted."
    )


def _msg_no_change(metrics: dict, reasons: list[str]) -> str:
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    wr  = round(metrics.get("win_rate_4h", 0), 1)
    avg = round(metrics.get("avg_return_4h", 0), 2)
    n   = metrics.get("outcomes_4h", 0)
    thresh = metrics.get("current_threshold", "?")
    return (
        f"📊 <b>Auto-tune ran, no changes</b> — {date}\n\n"
        f"Current config is performing within expected range.\n"
        f"Threshold holding at <b>{thresh}</b>.\n\n"
        f"<b>Data:</b> {n} outcomes · win rate {wr}% · avg 4h {avg:+.2f}%"
    )


def _msg_insufficient(scan_runs: int, outcomes_4h: int) -> str:
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return (
        f"⏳ <b>Auto-tune skipped</b> — {date}\n\n"
        f"Not enough data to make confident recommendations.\n\n"
        f"  • Scan runs: {scan_runs} (need ≥{MIN_SCAN_RUNS})\n"
        f"  • Evaluated 4h outcomes: {outcomes_4h} (need ≥{MIN_OUTCOMES_4H})\n\n"
        f"Check back next Monday — data accumulates with each alert."
    )


def _msg_error(exc: Exception) -> str:
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return (
        f"⚠️ <b>Auto-tune error</b> — {date}\n\n"
        f"The tuner ran but hit an unexpected error:\n"
        f"<code>{str(exc)[:300]}</code>\n\n"
        f"Config unchanged. Check auto_tune.log for details."
    )


def _fmt_lane_section(lane_data: dict) -> str:
    """Format lane win rates for the Telegram report."""
    if not lane_data:
        return ""
    lanes = lane_data.get("lanes", [])
    if not lanes:
        return ""
    lines = ["\n<b>📊 Lane Win Rates (4h):</b>"]
    for row in lanes:
        bar = "🟢" if row["win_rate_4h"] >= 55 else ("🟡" if row["win_rate_4h"] >= 45 else "🔴")
        lines.append(
            f"  {bar} <b>{row['lane']}</b>: {row['win_rate_4h']:.1f}% wr · "
            f"{row['avg_return_4h']:+.1f}% avg · n={row['count']}"
        )
    by_src = lane_data.get("by_source", [])
    if by_src:
        lines.append("<b>📡 By Source:</b>")
        for row in by_src[:4]:  # top 4 sources
            lines.append(
                f"  • {row['source']}: {row['win_rate_4h']:.1f}% wr · n={row['count']}"
            )
    return "\n".join(lines)


def _fmt_score_section(analysis: dict) -> str:
    """Format score component analysis for the Telegram report."""
    if not analysis:
        return ""
    components = analysis.get("components", [])
    if not components:
        return ""
    # Sort by absolute correlation
    sorted_comps = sorted(components, key=lambda x: abs(x.get("corr_4h", 0)), reverse=True)
    lines = ["\n<b>🧠 Score Component Correlations (4h):</b>"]
    for c in sorted_comps[:5]:
        corr = c.get("corr_4h", 0)
        arrow = "📈" if corr > 0.1 else ("📉" if corr < -0.1 else "➡️")
        lines.append(f"  {arrow} {c['component']}: r={corr:.3f}")
    weeks_ready = analysis.get("consistent_weeks", 0)
    min_weeks = analysis.get("min_consistency_weeks", 3)
    if weeks_ready < min_weeks:
        lines.append(f"  ⏳ Weight overrides in {min_weeks - weeks_ready} more consistent week(s)")
    else:
        lines.append("  ✅ Weight overrides applied")
    kw_hot = analysis.get("hot_keywords", [])[:3]
    kw_cold = analysis.get("cold_keywords", [])[:2]
    if kw_hot:
        lines.append(f"  🔥 Hot keywords: {', '.join(kw_hot)}")
    if kw_cold:
        lines.append(f"  ❄️ Cold keywords: {', '.join(kw_cold)}")
    return "\n".join(lines)


def _fmt_exit_section(exit_profiles: dict) -> str:
    """Format exit profile findings for Telegram report."""
    if not exit_profiles:
        return ""
    by_regime = exit_profiles.get("by_regime", {})
    if not by_regime:
        return ""
    lines = ["\n<b>🚪 Exit Profiles (by regime):</b>"]
    for regime, profile in list(by_regime.items())[:4]:
        best = profile.get("best_exit_reason", "?")
        wr = profile.get("win_rate", 0)
        avg = profile.get("avg_pnl_pct", 0)
        n = profile.get("count", 0)
        lines.append(f"  • {regime}: best={best} wr={wr:.0f}% avg={avg:+.1f}% n={n}")
    return "\n".join(lines)


# ── Exit learnings processor ───────────────────────────────────────────────────

def _process_exit_learnings() -> dict:
    """
    Read exit_outcomes.json, compute best exit rule per regime,
    write exit_profiles.json for exit_strategy.py to consume.
    Returns summary dict for Telegram report.
    """
    try:
        from utils.exit_strategy import load_exit_learnings
        records = load_exit_learnings()
    except Exception as exc:
        log.warning("Could not load exit learnings: %s", exc)
        return {}

    if len(records) < 5:
        log.info("exit_learnings: only %d records — skipping profile generation", len(records))
        return {}

    # Group by (regime, exit_reason_prefix)
    by_regime: dict[str, dict] = {}
    for r in records:
        profile_key = r.get("profile_key", "")
        regime = profile_key.split("|")[0] if profile_key else "UNKNOWN"
        reason_prefix = r.get("exit_reason", "UNKNOWN").split(" ")[0].split("(")[0]
        if regime not in by_regime:
            by_regime[regime] = {}
        if reason_prefix not in by_regime[regime]:
            by_regime[regime][reason_prefix] = {"pnls": [], "wins": 0, "count": 0}
        pnl = float(r.get("pnl_pct", 0))
        by_regime[regime][reason_prefix]["pnls"].append(pnl)
        by_regime[regime][reason_prefix]["count"] += 1
        if pnl > 0:
            by_regime[regime][reason_prefix]["wins"] += 1

    profiles_by_regime: dict = {}
    for regime, reason_map in by_regime.items():
        best_reason = None
        best_avg_pnl = float("-inf")
        all_pnls = []
        total_n = 0
        for reason, stats in reason_map.items():
            pnls = stats["pnls"]
            avg = sum(pnls) / len(pnls) if pnls else 0.0
            all_pnls.extend(pnls)
            total_n += stats["count"]
            if avg > best_avg_pnl:
                best_avg_pnl = avg
                best_reason = reason

        if total_n < 3:
            continue

        wins_total = sum(1 for p in all_pnls if p > 0)
        profiles_by_regime[regime] = {
            "best_exit_reason": best_reason,
            "best_avg_pnl_pct": round(best_avg_pnl, 2),
            "win_rate": round(wins_total / total_n * 100, 1) if total_n else 0,
            "avg_pnl_pct": round(sum(all_pnls) / len(all_pnls), 2) if all_pnls else 0,
            "count": total_n,
            "reasons": {
                k: {
                    "count": v["count"],
                    "avg_pnl": round(sum(v["pnls"]) / len(v["pnls"]), 2) if v["pnls"] else 0.0,
                    "win_rate": round(v["wins"] / v["count"] * 100, 1) if v["count"] else 0.0,
                }
                for k, v in reason_map.items()
            },
        }

    exit_profiles = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "total_exits_analyzed": len(records),
        "by_regime": profiles_by_regime,
    }

    try:
        EXIT_PROFILES_PATH.parent.mkdir(parents=True, exist_ok=True)
        EXIT_PROFILES_PATH.write_text(json.dumps(exit_profiles, indent=2))
        log.info("exit_profiles.json updated with %d regime profiles", len(profiles_by_regime))
    except Exception as exc:
        log.warning("Could not write exit_profiles.json: %s", exc)

    return exit_profiles


# ── Core tuning logic ─────────────────────────────────────────────────────────

def _clamp(v: int, lo: int, hi: int) -> int:
    return max(lo, min(v, hi))


async def run_auto_tune(dry_run: bool = False) -> int:
    _load_env()

    # Pull current config
    env = _parse_env()
    cur_threshold = int(env.get("ALERT_THRESHOLD", "70"))
    cur_regime    = int(env.get("REGIME_MIN_SCORE", "50"))
    cur_conf      = env.get("MIN_CONFIDENCE_TO_ALERT", "B").upper()

    log.info(
        "Current config: threshold=%s regime=%s confidence=%s",
        cur_threshold, cur_regime, cur_conf,
    )

    # Run analysis
    try:
        from utils.db import get_weekly_tuning_report, init_db
        init_db()
        report = get_weekly_tuning_report(
            lookback_days=LOOKBACK_DAYS,
            current_alert_threshold=cur_threshold,
            current_regime_min_score=cur_regime,
            current_min_confidence_to_alert=cur_conf,
            min_outcomes_4h=MIN_OUTCOMES_4H,
        )
    except Exception as exc:
        log.error("get_weekly_tuning_report failed: %s", exc)
        await _send_telegram(_msg_error(exc))
        return 1

    scan_runs   = int(report.get("scan_runs", 0))
    outcomes_4h = int(report.get("outcomes_4h_count", 0))
    avg_4h      = float(report.get("avg_return_4h", 0) or 0)
    win_rate_4h = float(report.get("winrate_4h", 0) or 0)
    reasons     = report.get("reasons", [])
    rec         = report.get("recommended", {})

    log.info(
        "Report: scan_runs=%s outcomes_4h=%s avg_4h=%.2f%% win_rate=%.1f%%",
        scan_runs, outcomes_4h, avg_4h, win_rate_4h,
    )

    metrics = {
        "scan_runs":      scan_runs,
        "alerts":         int(report.get("alerts", 0)),
        "outcomes_4h":    outcomes_4h,
        "avg_return_4h":  avg_4h,
        "win_rate_4h":    win_rate_4h,
        "current_threshold": cur_threshold,
    }

    # ── Safety gate: insufficient data ────────────────────────────────────────
    if scan_runs < MIN_SCAN_RUNS or outcomes_4h < MIN_OUTCOMES_4H:
        log.info("Insufficient data — skipping. scan_runs=%s outcomes_4h=%s", scan_runs, outcomes_4h)
        entry = {
            "ts_utc":  datetime.now(timezone.utc).isoformat(),
            "action":  "SKIPPED_INSUFFICIENT_DATA",
            "before":  {"ALERT_THRESHOLD": cur_threshold, "REGIME_MIN_SCORE": cur_regime, "MIN_CONFIDENCE_TO_ALERT": cur_conf},
            "after":   None,
            "reasons": [f"Only {scan_runs} scan runs (need {MIN_SCAN_RUNS}) and {outcomes_4h} outcomes (need {MIN_OUTCOMES_4H})"],
            "metrics": metrics,
            "optimizer": report.get("optimizer"),
        }
        _append_log(entry)
        await _send_telegram(_msg_insufficient(scan_runs, outcomes_4h))
        return 0

    # ── Compute recommended config ─────────────────────────────────────────────
    new_threshold = _clamp(int(rec.get("alert_threshold", cur_threshold)), THRESHOLD_MIN, THRESHOLD_MAX)
    new_regime    = _clamp(int(rec.get("regime_min_score", cur_regime)), REGIME_MIN, REGIME_MAX)
    new_conf      = str(rec.get("min_confidence_to_alert", cur_conf)).upper()
    if new_conf not in VALID_CONF:
        new_conf = cur_conf

    before = {"ALERT_THRESHOLD": cur_threshold, "REGIME_MIN_SCORE": cur_regime, "MIN_CONFIDENCE_TO_ALERT": cur_conf}
    after  = {"ALERT_THRESHOLD": new_threshold,  "REGIME_MIN_SCORE": new_regime,  "MIN_CONFIDENCE_TO_ALERT": new_conf}

    log.info("Recommended: threshold=%s regime=%s confidence=%s", new_threshold, new_regime, new_conf)

    # ── Change guard: skip if delta is trivial ─────────────────────────────────
    threshold_changed = abs(new_threshold - cur_threshold) >= MIN_DELTA_INT
    regime_changed    = abs(new_regime - cur_regime) >= MIN_DELTA_INT
    conf_changed      = new_conf != cur_conf
    any_meaningful    = threshold_changed or regime_changed or conf_changed

    if not any_meaningful:
        log.info("No meaningful changes — skipping apply.")

        # Still run Phase 2 analysis so learning data stays fresh
        _lane_data: dict = {}
        _score_analysis: dict = {}
        _exit_profiles: dict = {}
        try:
            from utils.db import get_lane_win_rates
            _lane_data = get_lane_win_rates(lookback_days=LOOKBACK_DAYS, min_n=MIN_LANE_N)
        except Exception as _exc:
            log.warning("Lane analysis (no-change path) failed: %s", _exc)
        try:
            from utils.score_analyzer import analyze_score_components, analyze_keyword_win_rates
            _ca = analyze_score_components(lookback_days=60)
            _ka = analyze_keyword_win_rates(lookback_days=30)
            _score_analysis = {
                "components": _ca.get("components", []),
                "consistent_weeks": _ca.get("consistent_weeks", 0),
                "min_consistency_weeks": _ca.get("min_consistency_weeks", 3),
                "hot_keywords": [k["keyword"] for k in _ka.get("hot", [])],
                "cold_keywords": [k["keyword"] for k in _ka.get("cold", [])],
            }
        except Exception as _exc:
            log.warning("Score analysis (no-change path) failed: %s", _exc)
        try:
            _exit_profiles = _process_exit_learnings()
        except Exception as _exc:
            log.warning("Exit learnings (no-change path) failed: %s", _exc)

        entry = {
            "ts_utc":         datetime.now(timezone.utc).isoformat(),
            "action":         "SKIPPED_NO_CHANGE",
            "before":         before,
            "after":          after,
            "reasons":        reasons,
            "metrics":        metrics,
            "optimizer":      report.get("optimizer"),
            "lane_data":      _lane_data,
            "score_analysis": _score_analysis,
        }
        _append_log(entry)

        tg_msg = _msg_no_change(metrics, reasons)
        tg_msg += _fmt_lane_section(_lane_data)
        tg_msg += _fmt_score_section(_score_analysis)
        tg_msg += _fmt_exit_section(_exit_profiles)
        await _send_telegram(tg_msg)
        return 0

    # ── Apply changes ──────────────────────────────────────────────────────────
    log.info(
        "Applying: threshold %s→%s, regime %s→%s, conf %s→%s",
        cur_threshold, new_threshold, cur_regime, new_regime, cur_conf, new_conf,
    )

    env_updates: dict[str, str] = {
        "ALERT_THRESHOLD":         str(new_threshold),
        "REGIME_MIN_SCORE":        str(new_regime),
        "MIN_CONFIDENCE_TO_ALERT": new_conf,
    }

    # ── Phase 2 A2: Lane win rate analysis + LAUNCH_MIN_SCORE tuning ──────────
    lane_data: dict = {}
    try:
        from utils.db import get_lane_win_rates
        lane_data = get_lane_win_rates(lookback_days=LOOKBACK_DAYS, min_n=MIN_LANE_N)
        log.info(
            "Lane win rates: total_tagged=%s total_outcomes=%s",
            lane_data.get("total_tagged", 0), lane_data.get("total_outcomes", 0),
        )

        # Auto-tune LAUNCH_MIN_SCORE
        lanes_list = lane_data.get("lanes", [])
        launch_lane = next((l for l in lanes_list if l["lane"] == "launch"), None)
        runner_lane = next((l for l in lanes_list if l["lane"] == "new_runner"), None)

        if (
            launch_lane and runner_lane
            and launch_lane["count"] >= MIN_LANE_N
            and runner_lane["count"] >= MIN_LANE_N
        ):
            launch_wr = launch_lane["win_rate_4h"]
            runner_wr = runner_lane["win_rate_4h"]
            cur_launch_score = int(env.get("LAUNCH_MIN_SCORE", "65"))

            if launch_wr >= runner_wr + LANE_OUTPERFORM_PTS:
                # Launch lane significantly outperforms — lower the bar to capture more
                new_launch_score = max(LAUNCH_SCORE_MIN, cur_launch_score - LAUNCH_SCORE_STEP)
                if new_launch_score != cur_launch_score:
                    env_updates["LAUNCH_MIN_SCORE"] = str(new_launch_score)
                    reasons.append(
                        f"Launch lane wr_4h {launch_wr:.1f}% vs new_runner {runner_wr:.1f}% "
                        f"(+{launch_wr - runner_wr:.1f}pts) — lowering LAUNCH_MIN_SCORE "
                        f"{cur_launch_score}→{new_launch_score}"
                    )
                    log.info(
                        "LAUNCH_MIN_SCORE: %s → %s (launch wr=%.1f%% vs runner wr=%.1f%%)",
                        cur_launch_score, new_launch_score, launch_wr, runner_wr,
                    )
            elif runner_wr >= launch_wr + LANE_OUTPERFORM_PTS and cur_launch_score < LAUNCH_SCORE_MAX:
                # Runner significantly outperforms — tighten launch criteria
                new_launch_score = min(LAUNCH_SCORE_MAX, cur_launch_score + LAUNCH_SCORE_STEP)
                if new_launch_score != cur_launch_score:
                    env_updates["LAUNCH_MIN_SCORE"] = str(new_launch_score)
                    reasons.append(
                        f"new_runner lane wr_4h {runner_wr:.1f}% significantly beats launch "
                        f"{launch_wr:.1f}% — raising LAUNCH_MIN_SCORE "
                        f"{cur_launch_score}→{new_launch_score}"
                    )
                    log.info(
                        "LAUNCH_MIN_SCORE: %s → %s (runner wr=%.1f%% >> launch wr=%.1f%%)",
                        cur_launch_score, new_launch_score, runner_wr, launch_wr,
                    )
        else:
            log.info(
                "Lane tuning: launch n=%s runner n=%s — need ≥%d each",
                launch_lane["count"] if launch_lane else 0,
                runner_lane["count"] if runner_lane else 0,
                MIN_LANE_N,
            )
    except Exception as exc:
        log.warning("Lane win rate analysis failed: %s", exc)

    # ── Phase 2 A3: Score component analysis + dynamic weight/keyword updates ──
    score_analysis: dict = {}
    try:
        from utils.score_analyzer import (
            analyze_score_components,
            analyze_keyword_win_rates,
            build_env_updates,
        )
        comp_analysis = analyze_score_components(lookback_days=60)
        kw_analysis   = analyze_keyword_win_rates(lookback_days=30)
        score_analysis = {
            "components":          comp_analysis.get("components", []),
            "consistent_weeks":    comp_analysis.get("consistent_weeks", 0),
            "min_consistency_weeks": comp_analysis.get("min_consistency_weeks", 3),
            "hot_keywords":        [k["keyword"] for k in kw_analysis.get("hot", [])],
            "cold_keywords":       [k["keyword"] for k in kw_analysis.get("cold", [])],
        }

        score_env_updates = build_env_updates(comp_analysis, kw_analysis)
        if score_env_updates:
            env_updates.update(score_env_updates)
            applied_keys = list(score_env_updates.keys())
            reasons.append(f"Score analyzer applied: {', '.join(applied_keys)}")
            log.info("Score analyzer env updates applied: %s", applied_keys)
        else:
            log.info("Score analyzer: not yet ready to apply weight overrides")
    except Exception as exc:
        log.warning("Score analyzer failed: %s", exc)

    # ── Phase 2 A4: Exit learnings → exit_profiles.json ───────────────────────
    exit_profiles: dict = {}
    try:
        exit_profiles = _process_exit_learnings()
    except Exception as exc:
        log.warning("Exit learnings processing failed: %s", exc)

    if not dry_run:
        bak = _rewrite_env(env_updates)
        log.info("Backed up .env → %s", bak)

        # Restart engine service
        try:
            result = subprocess.run(
                ["systemctl", "restart", "memecoin-engine"],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode == 0:
                log.info("memecoin-engine restarted successfully.")
            else:
                log.warning("systemctl restart returned %s: %s", result.returncode, result.stderr[:200])
        except Exception as exc:
            log.error("Failed to restart memecoin-engine: %s", exc)
    else:
        log.info("DRY RUN — skipping .env write and service restart.")

    entry = {
        "ts_utc":       datetime.now(timezone.utc).isoformat(),
        "action":       "APPLIED" if not dry_run else "DRY_RUN",
        "before":       before,
        "after":        after,
        "env_updates":  {k: v for k, v in env_updates.items()
                         if k not in ("ALERT_THRESHOLD", "REGIME_MIN_SCORE", "MIN_CONFIDENCE_TO_ALERT")},
        "reasons":      reasons,
        "metrics":      metrics,
        "optimizer":    report.get("optimizer"),
        "lane_data":    lane_data,
        "score_analysis": score_analysis,
    }
    _append_log(entry)

    # Phase 3: Update market cycle playbooks from accumulated outcome data
    cycle_summary: dict = {}
    try:
        from utils.market_cycle import update_cycle_playbooks  # type: ignore
        cycle_summary = update_cycle_playbooks(lookback_days=90)
        log.info("Cycle playbooks updated: %s", cycle_summary)
    except Exception as _ce:
        log.warning("Cycle playbook update failed: %s", _ce)

    # Build Phase-2-enriched Telegram message (Phase 3 section appended)
    tg_msg = _msg_applied(before, after, metrics, reasons)
    tg_msg += _fmt_lane_section(lane_data)
    tg_msg += _fmt_score_section(score_analysis)
    tg_msg += _fmt_exit_section(exit_profiles)
    tg_msg += _fmt_cycle_section(cycle_summary)

    if not dry_run:
        await _send_telegram(tg_msg)
    else:
        log.info("DRY RUN complete. Would have applied: %s", env_updates)

    return 0


def _fmt_cycle_section(summary: dict) -> str:
    """Format market cycle playbook update for Telegram weekly report."""
    if not summary:
        return ""

    EMOJI = {"BEAR": "🐻", "TRANSITION": "↔", "BULL": "🐂"}
    lines = ["\n\n<b>📊 Market Cycle Playbooks</b>"]

    for phase in ("BEAR", "TRANSITION", "BULL"):
        data = summary.get(phase, {})
        n    = data.get("sample_size", 0)
        emoji = EMOJI.get(phase, "?")

        if n < 3:
            lines.append(f"  {emoji} <b>{phase}</b>: ⏳ collecting data ({n} samples)")
            continue

        wr    = data.get("win_rate_4h")
        avg   = data.get("avg_return_4h")
        stop  = data.get("stop_loss_pct")
        tp1   = data.get("tp1_pct")
        tp2   = data.get("tp2_pct")
        hold  = data.get("max_hold_hours")
        status = data.get("status", "")

        wr_str   = f"{wr:.0f}%" if wr is not None else "—"
        avg_str  = f"{avg:+.1f}%" if avg is not None else "—"
        stop_str = f"{stop*100:.0f}%" if stop is not None else "—"
        tp1_str  = f"{tp1*100:.0f}%" if tp1 is not None else "—"
        tp2_str  = f"{tp2*100:.0f}%" if tp2 is not None else "—"
        hold_str = f"{hold:.0f}h" if hold is not None else "—"
        tag  = "✓ learned" if status == "updated" else "defaults"

        lines.append(
            f"  {emoji} <b>{phase}</b> [{n} samples, {tag}]: "
            f"WR={wr_str} avg={avg_str} · "
            f"stop={stop_str} tp1={tp1_str} tp2={tp2_str} hold={hold_str}"
        )

    return "\n".join(lines)


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Memecoin Engine — weekly autonomous self-tuner")
    parser.add_argument("--dry-run", action="store_true", help="Analyse and log but don't write .env or restart")
    args = parser.parse_args()

    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    return asyncio.run(run_auto_tune(dry_run=args.dry_run))


if __name__ == "__main__":
    raise SystemExit(main())
