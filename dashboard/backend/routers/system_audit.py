"""
System audit endpoint.

Purpose:
  - expose one compact operator-grade backend payload for posture, blockers,
    recent decisions, recent executions, and active anomalies
  - stay truthful without turning into a raw log dump
"""
from __future__ import annotations

import asyncio
import copy
import json
import logging
import os
import sqlite3
import time
from collections import Counter
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException

from auth import get_current_user
from routers._shared import _db_path, _ensure_engine_path
from snapshot_cache import load_snapshot, schedule_refresh, snapshot_or_build, store_snapshot

log = logging.getLogger("dashboard")
router = APIRouter(prefix="/api/system", tags=["system"])


def _dict(row: sqlite3.Row | None) -> dict:
    return dict(row) if row is not None else {}


def _json_loads(text: object) -> Any:
    if not text:
        return None
    try:
        return json.loads(str(text))
    except Exception:
        return None


def _independent_source_mode() -> bool:
    raw = f"{os.getenv('NO_BIRDEYE_MODE', '')},{os.getenv('INDEPENDENT_SOURCE_MODE', '')}".lower()
    return any(value.strip() in {"1", "true", "yes", "on"} for value in raw.split(","))


def _parse_ts(text: object) -> datetime | None:
    raw = str(text or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return None


def _format_ts(text: object) -> str | None:
    ts = _parse_ts(text)
    return ts.isoformat() if ts else (str(text) if text else None)


def _age_minutes(text: object) -> float | None:
    ts = _parse_ts(text)
    if ts is None:
        return None
    now = datetime.now(timezone.utc)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return max(0.0, (now - ts).total_seconds() / 60.0)


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return bool(row)


def _extract_blocker_labels(payload: Any) -> list[str]:
    labels: list[str] = []
    if isinstance(payload, dict):
        payload = [payload]
    if not isinstance(payload, list):
        return labels
    for item in payload:
        if isinstance(item, str):
            label = item.strip()
        elif isinstance(item, dict):
            label = str(
                item.get("name")
                or item.get("label")
                or item.get("blocker")
                or item.get("reason")
                or ""
            ).strip()
        else:
            label = ""
        if label:
            labels.append(label)
    return labels


def _normalize_blockers(
    posture_payload: dict,
    proof_stack: dict,
    whale_summary: dict,
    confluence_summary: dict,
    recent_decisions: list[dict],
) -> list[dict]:
    counter: Counter[str] = Counter()
    details: dict[str, str] = {}

    for row in recent_decisions:
        for label in row.get("blockers") or []:
            counter[label] += 1

    action_law = posture_payload.get("action_law_summary") or {}
    operating_mode = posture_payload.get("operating_mode_summary") or {}
    lane_unlock = posture_payload.get("lane_unlock_summary") or {}

    law_state = str(action_law.get("action_law_state") or "").strip().upper()
    highest = str(action_law.get("highest_permitted_action") or "").strip().upper()
    fresh_capital = str(action_law.get("fresh_capital_policy") or "").strip().upper()
    routing_state = str(lane_unlock.get("routing_state") or "").strip().upper()

    if law_state:
        counter[f"action_law:{law_state}"] += 1
        details[f"action_law:{law_state}"] = f"Action law is currently {law_state}."
    if fresh_capital == "BLOCKED":
        counter["fresh_capital_blocked"] += 1
        details["fresh_capital_blocked"] = "Fresh capital policy is currently BLOCKED."
    if routing_state == "LOCKED":
        counter["routing_locked"] += 1
        details["routing_locked"] = "No lane is currently route-ready."
    if highest:
        counter[f"highest_permitted:{highest}"] += 1
        details[f"highest_permitted:{highest}"] = f"Highest permitted action is {highest}."

    for item in list(proof_stack.get("current_blockers") or [])[:5]:
        if isinstance(item, dict):
            label = str(item.get("label") or item.get("blocker") or "").strip()
            count = int(item.get("count") or 1)
        else:
            label = str(item).strip()
            count = 1
        if not label:
            continue
        counter[f"proof:{label}"] += count
        details[f"proof:{label}"] = f"Proof stack blocker: {label}."

    whale_diag = whale_summary.get("pipeline_diagnostics") or {}
    whale_bottleneck = str(whale_diag.get("current_bottleneck") or "").strip().upper()
    whale_detail = str(whale_diag.get("current_bottleneck_detail") or "").strip()
    if whale_bottleneck and whale_bottleneck not in {"UNKNOWN", "HEALTHY"}:
        counter[f"whale:{whale_bottleneck}"] += 1
        details[f"whale:{whale_bottleneck}"] = whale_detail or f"Whale bottleneck is {whale_bottleneck}."
    for item in list(whale_diag.get("top_fail_reasons") or [])[:3]:
        label = str(item.get("reason") or "").strip()
        if not label:
            continue
        count = int(item.get("count") or item.get("n") or 1)
        counter[f"whale_fail:{label}"] += count
        details[f"whale_fail:{label}"] = f"Whale fail reason: {label}."

    pre = confluence_summary.get("pre_confluence") or {}
    pre_status = str(pre.get("status") or "").strip().upper()
    if pre_status and pre_status not in {"UNKNOWN", "EXACT_OVERLAP_READY", "NO_SIGNAL"}:
        counter[f"confluence:{pre_status}"] += 1
        if pre_status == "NO_SHARED_NAMES":
            details[f"confluence:{pre_status}"] = "Whale and memecoin are not surfacing the same names yet."
        elif pre_status == "WINDOW_MISS":
            details[f"confluence:{pre_status}"] = "The same mint exists outside the 48h confluence window."
        elif pre_status == "SYMBOL_REUSE_MISMATCH":
            details[f"confluence:{pre_status}"] = "Same symbol family is appearing on different mints."
        else:
            details[f"confluence:{pre_status}"] = f"Pre-confluence status is {pre_status}."

    top = counter.most_common(12)
    return [
        {"label": label, "count": count, "detail": details.get(label)}
        for label, count in top
    ]


def _clean_memecoin_blocker_reason(key: str, reason: str) -> str:
    clean_key = str(key or "").strip()
    clean_reason = str(reason or "").strip()
    if clean_key == "score_above_ceiling" and ("0.0" in clean_reason or not clean_reason):
        return "Scanner score is already too extended for the current proof ceiling."
    if clean_key == "score_below_floor" and ("0.0" in clean_reason or not clean_reason):
        return "Scanner score is still below the current proof floor."
    if clean_reason and "none" not in clean_reason.lower():
        return clean_reason
    return {
        "rug_warning": "Safety telemetry is not clean enough yet, so this setup still needs stricter rug review.",
        "rug_warn_review": "Safety telemetry is mixed, so this setup stays in review instead of promoting cleanly.",
        "rug_data_missing": "Safety telemetry is incomplete, so the system is refusing to trust this setup yet.",
        "cache_empty": "Live scan cache is empty, so this setup is being reviewed from fallback input instead of a fresh scan cycle.",
    }.get(clean_key, clean_reason or clean_key.replace("_", " ").strip())


def _allocator_truth_snapshot(allocator_watchdog: dict, allocator_history: list[dict]) -> dict:
    history = [dict(item or {}) for item in list(allocator_history or [])]
    latest_history = history[0] if history else {}
    watchdog_last_snapshot = _format_ts(allocator_watchdog.get("last_snapshot_utc"))
    history_last_snapshot = _format_ts(latest_history.get("ts_utc"))
    last_snapshot = history_last_snapshot or watchdog_last_snapshot
    age_minutes = _age_minutes(last_snapshot)

    status = str(allocator_watchdog.get("status") or "UNKNOWN").strip().upper() or "UNKNOWN"
    history_status = "UNKNOWN"
    if age_minutes is None:
        history_status = "NO_HISTORY"
    elif age_minutes <= 180:
        history_status = "FRESH"
    elif age_minutes <= 1440:
        history_status = "AGING"
    else:
        history_status = "STALE"

    detail = "Allocator history freshness is unavailable."
    if history_status == "FRESH":
        detail = "Allocator history is current enough to trust."
    elif history_status == "AGING":
        detail = f"Allocator history is aging ({int(age_minutes or 0)}m old), but still usable."
    elif history_status == "STALE":
        detail = f"Allocator history is stale ({int(age_minutes or 0)}m old), so treat allocator posture as a watchdog heartbeat more than a fresh snapshot."
    elif history_status == "NO_HISTORY":
        detail = "Allocator watchdog is alive, but there is no recent allocator history snapshot behind it."

    return {
        **dict(allocator_watchdog or {}),
        "status": status,
        "history_status": history_status,
        "history_last_snapshot_utc": last_snapshot,
        "history_age_minutes": round(age_minutes, 1) if age_minutes is not None else None,
        "detail": detail,
    }


def _token_stats_truth_snapshot(conn: sqlite3.Connection, stream_watchdog: dict) -> dict:
    raw = dict(stream_watchdog or {})
    snapshot_count = 0
    latest_snapshot = None
    latest_symbols: list[str] = []

    if _table_exists(conn, "memecoin_token_stats_snapshots"):
        row = conn.execute(
            "SELECT COUNT(*) AS c, MAX(ts_utc) AS max_ts FROM memecoin_token_stats_snapshots"
        ).fetchone()
        snapshot_count = int((row["c"] if row else 0) or 0)
        latest_snapshot = _format_ts(row["max_ts"] if row else None)
        rows = conn.execute(
            """
            SELECT symbol
            FROM memecoin_token_stats_snapshots
            WHERE ts_utc = ?
            ORDER BY id DESC
            LIMIT 8
            """,
            (row["max_ts"] if row else "",),
        ).fetchall() if latest_snapshot else []
        latest_symbols = [str(r["symbol"] or "").upper() for r in rows if str(r["symbol"] or "").strip()]

    snapshot_age = _age_minutes(latest_snapshot)
    watchdog_status = str(raw.get("status") or "UNKNOWN").strip().upper() or "UNKNOWN"
    ws_connected = bool(raw.get("ws_connected"))

    data_status = "NO_DATA"
    if snapshot_age is not None and snapshot_age <= 10:
        data_status = "FRESH"
    elif snapshot_age is not None and snapshot_age <= 60:
        data_status = "AGING"
    elif snapshot_count > 0:
        data_status = "STALE"

    status = watchdog_status
    if data_status == "FRESH":
        status = "ACTIVE"
    elif data_status == "AGING" and watchdog_status not in {"ERROR", "DISABLED"}:
        status = "DEGRADED"
    elif data_status in {"STALE", "NO_DATA"} and watchdog_status not in {"ERROR", "DISABLED"}:
        status = "DEGRADED"

    if data_status == "FRESH":
        detail = "Birdeye token stats snapshots are fresh; proof and exit layers can use live token telemetry."
    elif data_status == "AGING":
        detail = f"Birdeye token stats snapshots are aging ({int(snapshot_age or 0)}m old), so treat token telemetry as usable but cooling."
    elif data_status == "STALE":
        detail = f"Birdeye token stats snapshots are stale ({int(snapshot_age or 0)}m old); fallback proof input should stay visible."
    else:
        detail = str(raw.get("detail") or "No Birdeye token stats snapshots have been recorded yet.")

    return {
        **raw,
        "status": status,
        "watchdog_status": watchdog_status,
        "data_status": data_status,
        "latest_snapshot_utc": latest_snapshot,
        "snapshot_age_minutes": round(snapshot_age, 1) if snapshot_age is not None else None,
        "snapshot_count": snapshot_count,
        "latest_symbols": latest_symbols,
        "detail": detail,
    }


def _watch_to_entry_replay_report(conn: sqlite3.Connection, watch_status: dict) -> dict:
    tracked = [dict(item or {}) for item in list((watch_status or {}).get("tracked") or [])]
    recent_events = [dict(item or {}) for item in list((watch_status or {}).get("recent_events") or [])]
    true_last = [item for item in tracked if item.get("true_last_blocker")]
    multi_blocked = [item for item in tracked if not item.get("true_last_blocker")]
    established = [item for item in tracked if item.get("is_established_runner")]

    def _nearest_outcome(row: sqlite3.Row) -> dict:
        mint = str(row["mint"] or "").strip() if "mint" in row.keys() else ""
        symbol = str(row["symbol"] or "").strip().upper() if "symbol" in row.keys() else ""
        surfaced_at = str(row["surfaced_at"] or "").strip() if "surfaced_at" in row.keys() else ""
        if not surfaced_at or not _table_exists(conn, "memecoin_signal_outcomes"):
            return {}
        try:
            if mint:
                matched = conn.execute(
                    """
                    SELECT scanned_at, return_1h_pct, return_4h_pct, return_24h_pct
                    FROM memecoin_signal_outcomes
                    WHERE mint = ?
                    ORDER BY ABS(strftime('%s', scanned_at) - strftime('%s', ?)) ASC
                    LIMIT 1
                    """,
                    (mint, surfaced_at),
                ).fetchone()
            else:
                matched = None
            if matched is None and symbol:
                matched = conn.execute(
                    """
                    SELECT scanned_at, return_1h_pct, return_4h_pct, return_24h_pct
                    FROM memecoin_signal_outcomes
                    WHERE UPPER(symbol) = ?
                    ORDER BY ABS(strftime('%s', scanned_at) - strftime('%s', ?)) ASC
                    LIMIT 1
                    """,
                    (symbol, surfaced_at),
                ).fetchone()
            return dict(matched) if matched else {}
        except Exception:
            return {}

    def _float_or_none(value: object) -> float | None:
        try:
            if value is None or value == "":
                return None
            return float(value)
        except Exception:
            return None

    def _horizon_stats(rows: list[dict], key: str) -> dict:
        values = [_float_or_none(row.get(key)) for row in rows]
        complete = [value for value in values if value is not None]
        if not complete:
            return {"complete": 0, "win_rate_pct": None, "avg_return_pct": None}
        return {
            "complete": len(complete),
            "win_rate_pct": round(sum(1 for value in complete if value > 0) / len(complete) * 100.0, 1),
            "avg_return_pct": round(sum(complete) / len(complete), 2),
        }

    surface_summary = {
        "surfaced": 0,
        "outcomes_complete": 0,
        "win_rate_pct": None,
        "avg_return_24h_pct": None,
        "horizons": {
            "1h": {"complete": 0, "win_rate_pct": None, "avg_return_pct": None},
            "4h": {"complete": 0, "win_rate_pct": None, "avg_return_pct": None},
            "24h": {"complete": 0, "win_rate_pct": None, "avg_return_pct": None},
        },
        "quality_state": "NO_TRIGGER_SAMPLE",
        "recent": [],
    }
    if _table_exists(conn, "research_surface_log"):
        try:
            rows = conn.execute(
                """
                SELECT surfaced_at, symbol, mint, proof_reason, proof_score,
                       proof_snapshot_json, return_24h_pct
                FROM research_surface_log
                WHERE source='WATCH_TO_ENTRY'
                ORDER BY surfaced_at DESC
                LIMIT 200
                """
            ).fetchall()
            enriched: list[dict] = []
            for row in rows:
                item = dict(row)
                matched = _nearest_outcome(row)
                item["source_scanned_at"] = matched.get("scanned_at")
                item["return_1h_pct"] = matched.get("return_1h_pct")
                item["return_4h_pct"] = matched.get("return_4h_pct")
                item["return_24h_pct"] = (
                    item.get("return_24h_pct")
                    if item.get("return_24h_pct") is not None
                    else matched.get("return_24h_pct")
                )
                enriched.append(item)
            values = [float(item["return_24h_pct"]) for item in enriched if item.get("return_24h_pct") is not None]
            surface_summary["surfaced"] = len(rows)
            surface_summary["outcomes_complete"] = len(values)
            surface_summary["horizons"] = {
                "1h": _horizon_stats(enriched, "return_1h_pct"),
                "4h": _horizon_stats(enriched, "return_4h_pct"),
                "24h": _horizon_stats(enriched, "return_24h_pct"),
            }
            surface_summary["recent"] = [
                {
                    "surfaced_at": item.get("surfaced_at"),
                    "symbol": item.get("symbol"),
                    "mint": item.get("mint"),
                    "proof_score": item.get("proof_score"),
                    "return_1h_pct": item.get("return_1h_pct"),
                    "return_4h_pct": item.get("return_4h_pct"),
                    "return_24h_pct": item.get("return_24h_pct"),
                }
                for item in enriched[:8]
            ]
            if values:
                surface_summary["win_rate_pct"] = round(sum(1 for value in values if value > 0) / len(values) * 100.0, 1)
                surface_summary["avg_return_24h_pct"] = round(sum(values) / len(values), 2)
            complete_4h = int(surface_summary["horizons"]["4h"]["complete"] or 0)
            avg_4h = surface_summary["horizons"]["4h"]["avg_return_pct"]
            if complete_4h >= 8 and avg_4h is not None:
                surface_summary["quality_state"] = "POSITIVE" if float(avg_4h) > 0 else "NEGATIVE"
            elif len(rows) > 0:
                surface_summary["quality_state"] = "BUILDING_SAMPLE"
        except Exception:
            pass

    blocker_counts: Counter[str] = Counter()
    for item in tracked:
        for key in list(item.get("remaining_blocker_keys") or []):
            blocker_counts[str(key)] += 1

    state = "NO_SAMPLE"
    if true_last:
        state = "ARMED_TRUE_LAST"
    elif tracked:
        state = "WATCHING_MULTI_BLOCKED"
    if int(surface_summary["outcomes_complete"] or 0) >= 5:
        state = "REPLAY_READY"

    return {
        "state": state,
        "headline": (
            f"{len(true_last)} true last-blocker setup(s) armed."
            if true_last
            else (
                f"{len(tracked)} watched setup(s), but all still have multiple blockers."
                if tracked
                else "No watch-to-entry candidates are armed right now."
            )
        ),
        "tracking_count": len(tracked),
        "true_last_blocker_count": len(true_last),
        "multi_blocked_count": len(multi_blocked),
        "established_runner_count": len(established),
        "top_blockers": [
            {"key": key, "count": int(count)}
            for key, count in blocker_counts.most_common(6)
        ],
        "event_summary": {
            "logged_triggers": len(recent_events),
            "last_trigger_at": recent_events[0].get("ts_utc") if recent_events else None,
            "last_trigger_symbol": recent_events[0].get("symbol") if recent_events else None,
        },
        "surface_summary": surface_summary,
        "next_step": (
            "Wait for a true last-blocker clear before trusting an entry alert."
            if not true_last
            else "If one clears into PROOF_READY, verify CA and use manual execution discipline."
        ),
    }


def _established_runner_coverage(conn: sqlite3.Connection, watch_status: dict) -> dict:
    def _float_or_none(value: object) -> float | None:
        try:
            if value is None or value == "":
                return None
            return float(value)
        except Exception:
            return None

    def _mint_from_row(row: object) -> str:
        if not isinstance(row, dict):
            return ""
        for key in ("mint", "token_address", "tokenAddress", "address", "contract_address", "ca"):
            value = str(row.get(key) or "").strip()
            if value:
                return value
        for key in ("baseToken", "base_token", "token"):
            nested = row.get(key)
            if isinstance(nested, dict):
                value = str(nested.get("address") or nested.get("mint") or "").strip()
                if value:
                    return value
        return ""

    def _candidate_rows(payload: object) -> list[dict]:
        if isinstance(payload, list):
            return [dict(item) for item in payload if isinstance(item, dict)]
        if not isinstance(payload, dict):
            return []
        for key in ("candidates", "signals", "rows", "items", "tokens", "data"):
            rows = payload.get(key)
            if isinstance(rows, list):
                return [dict(item) for item in rows if isinstance(item, dict)]
        return []

    tracked = [dict(item or {}) for item in list((watch_status or {}).get("tracked") or [])]
    tracked_mints = {str(item.get("mint") or "").strip() for item in tracked if str(item.get("mint") or "").strip()}
    tracked_by_mint = {str(item.get("mint") or "").strip(): item for item in tracked if str(item.get("mint") or "").strip()}

    proof_rows: list[dict] = []
    try:
        from utils.memecoin_manager import get_proof_candidate_snapshot  # type: ignore

        proof_snapshot = get_proof_candidate_snapshot(limit=75)
        proof_rows = [dict(item or {}) for item in list((proof_snapshot or {}).get("candidates") or [])]
    except Exception:
        proof_rows = []
    proof_mints = {str(item.get("mint") or item.get("token_address") or "").strip() for item in proof_rows if str(item.get("mint") or item.get("token_address") or "").strip()}
    proof_by_mint = {
        str(item.get("mint") or item.get("token_address") or "").strip(): item
        for item in proof_rows
        if str(item.get("mint") or item.get("token_address") or "").strip()
    }

    scan_rows: list[dict] = []
    for key in ("memecoin_scan_cache", "memecoin_scan_cache_last_nonempty"):
        try:
            row = conn.execute("SELECT value FROM kv_store WHERE key=?", (key,)).fetchone()
            scan_rows.extend(_candidate_rows(_json_loads(row[0] if row else None)))
        except Exception:
            pass
    scan_mints = {_mint_from_row(item) for item in scan_rows if _mint_from_row(item)}

    runner_rows: list[dict] = []
    runner_status: dict = {}
    try:
        row = conn.execute("SELECT value FROM kv_store WHERE key='memecoin_runner_heartbeat_signals'").fetchone()
        payload = _json_loads(row[0] if row else None)
        runner_rows = _candidate_rows(payload)
        if isinstance(payload, dict):
            runner_status = dict(payload.get("status") or {})
    except Exception:
        runner_rows = []
        runner_status = {}
    runner_mints = {_mint_from_row(item) for item in runner_rows if _mint_from_row(item)}

    profiles: dict[str, dict] = {}
    try:
        from utils.conviction_recovery import established_runner_profile_map  # type: ignore

        profiles = established_runner_profile_map()
    except Exception:
        profiles = {}

    runners = [
        dict(profile)
        for profile in profiles.values()
        if profile.get("is_established_runner") and str(profile.get("mint") or "").strip()
    ]
    rows: list[dict] = []
    for profile in runners:
        mint = str(profile.get("mint") or "").strip()
        watch = tracked_by_mint.get(mint) or {}
        proof = proof_by_mint.get(mint) or {}
        status = "CONFIGURED_COLD"
        if mint in tracked_mints:
            status = "WATCHING"
        elif str(proof.get("proof_status") or "").upper() == "PROOF_READY":
            status = "PROOF_READY"
        elif mint in proof_mints:
            status = "PROOF_PIPELINE"
        elif mint in runner_mints:
            status = "RUNNER_HEARTBEAT"
        elif mint in scan_mints:
            status = "SCANNER_SEEN"
        rows.append(
            {
                "symbol": profile.get("symbol"),
                "mint": mint,
                "profile": profile.get("profile"),
                "status": status,
                "quality_score": profile.get("quality_score"),
                "reference_mcap": profile.get("reference_mcap"),
                "known_peak_mcap": profile.get("known_peak_mcap"),
                "thesis": profile.get("thesis"),
                "primary_blocker": watch.get("primary_blocker"),
                "remaining_blocker_count": watch.get("remaining_blocker_count"),
                "true_last_blocker": bool(watch.get("true_last_blocker")),
                "proof_status": proof.get("proof_status"),
                "proof_score": proof.get("proof_score"),
                "source": profile.get("source"),
            }
        )

    def _sort_key(item: dict) -> tuple:
        status_rank = {
            "WATCHING": 0,
            "PROOF_READY": 1,
            "PROOF_PIPELINE": 2,
            "RUNNER_HEARTBEAT": 3,
            "SCANNER_SEEN": 4,
            "CONFIGURED_COLD": 5,
        }.get(str(item.get("status") or ""), 9)
        score = _float_or_none(item.get("quality_score"))
        return (status_rank, -(score if score is not None else 0.0), str(item.get("symbol") or ""))

    rows = sorted(rows, key=_sort_key)
    live_seen = [item for item in rows if item.get("status") != "CONFIGURED_COLD"]
    cold = [item for item in rows if item.get("status") == "CONFIGURED_COLD"]
    state = "LIVE_COVERAGE" if live_seen else ("CONFIGURED_COLD" if rows else "NO_RUNNER_LIST")
    return {
        "state": state,
        "headline": (
            f"{len(live_seen)} established runner(s) are live in scanner/proof/watch coverage."
            if state == "LIVE_COVERAGE"
            else (
                f"{len(rows)} established runner(s) are configured, but none are live in scanner/proof/watch right now."
                if state == "CONFIGURED_COLD"
                else "No established-runner profile list is available."
            )
        ),
        "tracked_count": len(rows),
        "covered_count": len(rows),
        "live_seen_count": len(live_seen),
        "missing_count": 0,
        "cold_count": len(cold),
        "watching_count": len([item for item in rows if item.get("status") == "WATCHING"]),
        "proof_pipeline_count": len([item for item in rows if item.get("status") in {"PROOF_READY", "PROOF_PIPELINE"}]),
        "runner_heartbeat_count": len([item for item in rows if item.get("status") == "RUNNER_HEARTBEAT"]),
        "scanner_seen_count": len([item for item in rows if item.get("status") == "SCANNER_SEEN"]),
        "scan_cache_count": len(scan_mints),
        "runner_heartbeat_status": runner_status,
        "covered": rows[:12],
        "cold": cold[:12],
        "missing": [],
        "next_step": (
            "No configured runner is live right now; keep discovery warm and let watch-to-entry trigger only after the final blocker clears."
            if cold and not live_seen
            else "Keep coverage live and use watch-to-entry instead of blind entries."
        ),
    }


def _wallet_stream_truth_snapshot(stream_watchdog: dict) -> dict:
    raw = dict(stream_watchdog or {})
    watchdog_status = str(raw.get("status") or "UNKNOWN").strip().upper() or "UNKNOWN"
    source_mode = str(raw.get("data_status") or "").strip().upper()
    last_frame_ts = _format_ts(raw.get("last_frame_ts"))
    last_event_ts = _format_ts(raw.get("last_event_ts"))
    checked_at = _format_ts(raw.get("checked_at"))
    frame_age = _age_minutes(last_frame_ts)
    event_age = _age_minutes(last_event_ts)
    checked_age = _age_minutes(checked_at)
    last_message_type = str(raw.get("last_message_type") or "").strip().upper()

    if source_mode == "INDEPENDENT_MODE":
        inserted = int(raw.get("inserted_live_events") or raw.get("persisted_events") or 0)
        polled = int(raw.get("polled_wallets") or 0)
        if watchdog_status == "ERROR":
            status = "ERROR"
            detail = str(raw.get("detail") or "Independent wallet flow is erroring.")
        elif checked_age is not None and checked_age <= 15 and polled > 0:
            status = "ACTIVE" if inserted > 0 else "DEGRADED"
            detail = (
                f"Independent wallet flow is fresh; Helius poll inserted {inserted} live event(s)."
                if inserted > 0 else
                "Independent wallet flow is fresh, but the latest poll had no new wallet events."
            )
        elif checked_age is not None and checked_age <= 180:
            status = "DEGRADED"
            detail = f"Independent wallet flow is aging ({int(checked_age)}m since poll)."
        else:
            status = "DEGRADED"
            detail = "Independent wallet flow has no fresh poll yet."
        return {
            **raw,
            "status": status,
            "watchdog_status": watchdog_status,
            "data_status": {
                "mode": "INDEPENDENT_MODE",
                "poll": "FRESH" if checked_age is not None and checked_age <= 15 else "AGING" if checked_age is not None and checked_age <= 180 else "STALE",
                "events": "FRESH" if inserted > 0 else "NO_NEW_EVENTS",
            },
            "last_poll_age_minutes": round(checked_age, 1) if checked_age is not None else None,
            "detail": detail,
        }

    frame_status = "NO_DATA"
    if frame_age is not None and frame_age <= 30:
        frame_status = "FRESH"
    elif frame_age is not None and frame_age <= 180:
        frame_status = "AGING"
    elif last_frame_ts:
        frame_status = "STALE"

    event_status = "NO_DATA"
    if event_age is not None and event_age <= 180:
        event_status = "FRESH"
    elif event_age is not None and event_age <= 1440:
        event_status = "AGING"
    elif last_event_ts:
        event_status = "STALE"

    status = watchdog_status
    if watchdog_status not in {"ERROR", "DISABLED"}:
        if frame_status == "FRESH" and event_status in {"FRESH", "AGING", "NO_DATA"} and last_message_type != "ERROR":
            status = "ACTIVE"
        elif frame_status in {"AGING", "STALE", "NO_DATA"} or event_status == "STALE" or last_message_type == "ERROR":
            status = "DEGRADED"

    if status == "ACTIVE":
        detail = "Wallet stream frames are fresh enough to trust as reinforcement context."
    elif frame_status == "STALE":
        detail = f"Wallet stream frames are stale ({int(frame_age or 0)}m old); reinforcement should not overclaim live wallet flow."
    elif event_status == "STALE":
        detail = f"Wallet stream events are stale ({int(event_age or 0)}m old); treat wallet reinforcement as historical context."
    elif last_message_type == "ERROR":
        detail = "Wallet stream last message was an error; live wallet reinforcement is degraded."
    else:
        detail = "Wallet stream freshness is not strong enough to fully trust as live reinforcement."

    return {
        **raw,
        "status": status,
        "watchdog_status": watchdog_status,
        "data_status": {
            "frames": frame_status,
            "events": event_status,
        },
        "last_frame_age_minutes": round(frame_age, 1) if frame_age is not None else None,
        "last_event_age_minutes": round(event_age, 1) if event_age is not None else None,
        "detail": detail,
    }


def _runtime_data_confidence(runtime: dict) -> dict:
    provider_status = dict(runtime.get("provider_status") or {})
    fallbacks = dict(runtime.get("fallbacks") or {})
    wallet_stream = dict(runtime.get("wallet_stream") or {})
    memecoin_input = dict(runtime.get("memecoin_input") or {})
    independent_mode = bool((runtime.get("source_strategy") or {}).get("independent_mode") or _independent_source_mode())

    issues: list[str] = []
    if not independent_mode and str((provider_status.get("birdeye") or {}).get("status") or "").upper() in {"ERROR", "DEGRADED"}:
        reason = str((provider_status.get("birdeye") or {}).get("reason") or "").lower()
        issues.append("BirdEye auth/API is degraded." if "auth" in reason or "key" in reason else "BirdEye provider is degraded.")
    if str((provider_status.get("dexscreener") or {}).get("status") or "").upper() != "ACTIVE":
        issues.append("DexScreener scanner provider is degraded.")
    if str((provider_status.get("dexscreener_spot") or {}).get("status") or "").upper() != "ACTIVE":
        issues.append("Spot DexScreener provider is degraded.")
    token_stats_state = str((fallbacks.get("birdeye_token_stats") or {}).get("data_status") or "").upper()
    if token_stats_state in {"STALE", "NO_DATA"}:
        issues.append("Independent token-stat replacement is not live." if independent_mode else "Birdeye token stats are stale.")
    wallet_state = str(wallet_stream.get("status") or "").upper()
    if wallet_state != "ACTIVE":
        issues.append("Independent wallet-flow replacement is not live." if independent_mode and wallet_state == "DISABLED" else "Wallet transaction stream is degraded.")
    if str((memecoin_input.get("scan_cache") or {}).get("status") or "").upper() not in {"LIVE_NONEMPTY", "FRESH_EMPTY"}:
        issues.append("Memecoin scanner cache is not fresh.")

    if not issues:
        status = "HIGH"
        headline = "Data confidence is high."
    elif len(issues) <= 2:
        status = "MEDIUM"
        headline = "Data confidence is usable with caveats."
    else:
        status = "LOW"
        headline = "Data confidence is constrained by stale or degraded feeds."

    return {
        "status": status,
        "headline": headline,
        "issues": issues[:6],
    }


def _runtime_provider_recovery(runtime: dict) -> dict:
    provider_status = dict(runtime.get("provider_status") or {})
    fallbacks = dict(runtime.get("fallbacks") or {})
    wallet_stream = dict(runtime.get("wallet_stream") or {})
    independent_mode = bool((runtime.get("source_strategy") or {}).get("independent_mode") or _independent_source_mode())
    feeds: list[dict] = []

    def _provider_feed(label: str, key: str) -> None:
        state = dict(provider_status.get(key) or {})
        status = str(state.get("status") or "UNKNOWN").upper()
        cooldown_until = _format_ts(state.get("cooldown_until"))
        cooldown_dt = _parse_ts(cooldown_until)
        if cooldown_dt and cooldown_dt.tzinfo is None:
            cooldown_dt = cooldown_dt.replace(tzinfo=timezone.utc)
        recovering = bool(cooldown_dt and datetime.now(timezone.utc) < cooldown_dt.astimezone(timezone.utc))
        feeds.append({
            "label": label,
            "key": key,
            "status": "RECOVERING" if recovering else status,
            "raw_status": status,
            "recovery_state": state.get("recovery_state") or ("COOLDOWN" if recovering else None),
            "cooldown_until": cooldown_until,
            "consecutive_failures": int(state.get("consecutive_failures") or 0),
            "reason": state.get("reason"),
            "detail": state.get("detail"),
        })

    _provider_feed("DexScreener scanner", "dexscreener")
    _provider_feed("DexScreener spot", "dexscreener_spot")
    if independent_mode:
        feeds.append({
            "label": "BirdEye API",
            "key": "birdeye",
            "status": "DISABLED",
            "raw_status": "DISABLED",
            "recovery_state": "INDEPENDENT_MODE",
            "cooldown_until": None,
            "consecutive_failures": 0,
            "reason": "independent_mode",
            "detail": "BirdEye is intentionally disabled; no recovery action is required.",
        })
    else:
        _provider_feed("BirdEye API", "birdeye")

    token_stats = dict(fallbacks.get("birdeye_token_stats") or {})
    feeds.append({
        "label": "Token stats replacement" if independent_mode else "Birdeye token stats",
        "key": "birdeye_token_stats",
        "status": str(token_stats.get("status") or "UNKNOWN").upper(),
        "raw_status": str(token_stats.get("watchdog_status") or token_stats.get("status") or "UNKNOWN").upper(),
        "recovery_state": "REPLACEMENT_NEEDED" if independent_mode else ("STALE_INPUT" if str(token_stats.get("data_status") or "").upper() in {"STALE", "NO_DATA"} else None),
        "cooldown_until": None,
        "consecutive_failures": 0,
        "reason": str(token_stats.get("data_status") or "").upper() or None,
        "detail": token_stats.get("detail"),
    })
    feeds.append({
        "label": "Wallet flow replacement" if independent_mode else "Wallet transaction stream",
        "key": "wallet_stream",
        "status": str(wallet_stream.get("status") or "UNKNOWN").upper(),
        "raw_status": str(wallet_stream.get("watchdog_status") or wallet_stream.get("status") or "UNKNOWN").upper(),
        "recovery_state": "REPLACEMENT_NEEDED" if independent_mode else ("RECYCLE_WORKERS" if str(wallet_stream.get("status") or "").upper() == "DEGRADED" else None),
        "cooldown_until": None,
        "consecutive_failures": int(wallet_stream.get("recovery_count") or 0),
        "reason": str(wallet_stream.get("last_message_type") or "").upper() or None,
        "detail": wallet_stream.get("detail"),
    })

    limiting = [
        feed for feed in feeds
        if str(feed.get("status") or "").upper() not in {"ACTIVE", "RECOVERED"}
    ]
    if not limiting:
        headline = "All decision feeds are fresh."
    elif any(str(feed.get("status") or "").upper() == "RECOVERING" for feed in limiting):
        headline = "One or more feeds are cooling down and retrying."
    else:
        headline = "One or more feeds are limiting decision confidence."

    return {
        "headline": headline,
        "limiting_count": len(limiting),
        "feeds": feeds,
    }


def _runtime_recovery_checklist(runtime: dict) -> list[dict]:
    provider_status = dict(runtime.get("provider_status") or {})
    fallbacks = dict(runtime.get("fallbacks") or {})
    wallet_stream = dict(runtime.get("wallet_stream") or {})
    market_source = dict(runtime.get("market_data_source") or {})
    memecoin_input = dict(runtime.get("memecoin_input") or {})
    independent_mode = bool((runtime.get("source_strategy") or {}).get("independent_mode") or _independent_source_mode())
    items: list[dict] = []

    birdeye = dict(provider_status.get("birdeye") or {})
    birdeye_status = str(birdeye.get("status") or "UNKNOWN").upper()
    birdeye_reason = str(birdeye.get("reason") or "").lower()
    items.append({
        "key": "birdeye_api",
        "label": "BirdEye API",
        "state": "DISABLED_INTENTIONAL" if independent_mode else ("FAILING_AUTH" if birdeye_status == "ERROR" and ("auth" in birdeye_reason or "key" in birdeye_reason) else birdeye_status),
        "detail": "BirdEye is intentionally not required in independent mode." if independent_mode else (birdeye.get("detail") or birdeye.get("reason") or "BirdEye provider status has not reported yet."),
        "next_step": "No billing/key action required; keep building independent confirmation feeds." if independent_mode else ("Refresh or replace BIRDEYE_API_KEY on the engine service." if birdeye_status == "ERROR" else "Keep BirdEye as the preferred source once it returns ACTIVE."),
    })
    dex = dict(provider_status.get("dexscreener") or {})
    items.append({
        "key": "dexscreener",
        "label": "DexScreener",
        "state": str(dex.get("status") or "UNKNOWN").upper(),
        "detail": dex.get("detail") or dex.get("reason") or "DexScreener provider status has not reported yet.",
        "next_step": "Let cooldown/backoff clear; rely on GeckoTerminal fallback meanwhile.",
    })
    token_stats = dict(fallbacks.get("birdeye_token_stats") or {})
    items.append({
        "key": "token_stats",
        "label": "Token Stats Replacement" if independent_mode else "Token Stats Stream",
        "state": "REPLACEMENT_NEEDED" if independent_mode else str(token_stats.get("data_status") or token_stats.get("status") or "UNKNOWN").upper(),
        "detail": token_stats.get("detail") or "Token stat freshness has not reported yet.",
        "next_step": "Add independent token velocity/holder/tx telemetry before proof can regain full confidence." if independent_mode else "Token stats must produce fresh snapshots before proof can regain full confidence.",
    })
    items.append({
        "key": "wallet_stream",
        "label": "Wallet Flow Replacement" if independent_mode else "Wallet Stream",
        "state": "REPLACEMENT_NEEDED" if independent_mode else str(wallet_stream.get("status") or "UNKNOWN").upper(),
        "detail": wallet_stream.get("detail") or "Wallet stream freshness has not reported yet.",
        "next_step": "Use historical wallet scoring only until an independent wallet flow source is live." if independent_mode else "Worker recycle is enabled; keep wallet reinforcement tapered until frames recover.",
    })
    items.append({
        "key": "market_fallback",
        "label": "Market Fallback",
        "state": str(market_source.get("status") or "UNKNOWN").upper(),
        "detail": market_source.get("detail") or "Market fallback status will populate after the tactical feed runs.",
        "next_step": "Use GeckoTerminal + DexScreener as the primary independent market universe." if independent_mode else "Use GeckoTerminal fallback candidates when BirdEye/DexScreener are degraded.",
    })
    items.append({
        "key": "proof_engine",
        "label": "Proof Engine",
        "state": "STALE_PENALIZED" if str((runtime.get("data_confidence") or {}).get("status") or "").upper() == "LOW" else "NORMAL",
        "detail": memecoin_input.get("pipeline_detail") or (runtime.get("data_confidence") or {}).get("headline"),
        "next_step": "Only allow proof-ready once data confidence improves or fallback evidence is clearly tagged.",
    })
    return items


def _runtime_decision_mode(runtime: dict) -> dict:
    confidence = str((runtime.get("data_confidence") or {}).get("status") or "UNKNOWN").upper()
    provider_status = dict(runtime.get("provider_status") or {})
    market_source = dict(runtime.get("market_data_source") or {})
    wallet_stream = dict(runtime.get("wallet_stream") or {})
    token_stats = dict((runtime.get("fallbacks") or {}).get("birdeye_token_stats") or {})
    independent_mode = bool((runtime.get("source_strategy") or {}).get("independent_mode") or _independent_source_mode())

    fallback_active = bool(market_source.get("fallback_active"))
    birdeye_state = str((provider_status.get("birdeye") or {}).get("status") or "UNKNOWN").upper()
    dex_state = str((provider_status.get("dexscreener") or {}).get("status") or "UNKNOWN").upper()
    wallet_state = str(wallet_stream.get("status") or "UNKNOWN").upper()
    token_stats_state = str(token_stats.get("data_status") or token_stats.get("status") or "UNKNOWN").upper()

    hard_blocks: list[str] = []
    soft_blocks: list[str] = []
    if birdeye_state == "ERROR" and not independent_mode:
        hard_blocks.append("birdeye_auth")
    if wallet_state != "ACTIVE":
        soft_blocks.append("wallet_stream")
    if token_stats_state in {"STALE", "NO_DATA", "DEGRADED"}:
        soft_blocks.append("token_stats")
    if dex_state != "ACTIVE" and not fallback_active:
        hard_blocks.append("market_source")
    elif dex_state != "ACTIVE":
        soft_blocks.append("dexscreener")

    if independent_mode and fallback_active:
        mode = "INDEPENDENT_RESEARCH"
        headline = "Independent market sources are active; keep deployable proof conservative until replacement token/wallet telemetry is live."
        allowed = ["monitor", "research", "fallback_score", "manual_review"]
        blocked = ["auto_deploy", "proof_ready_without_fresh_confirmation", "wallet_reinforcement_overweight"]
    elif confidence == "HIGH":
        mode = "LIVE_DECISION"
        headline = "Live data is healthy enough for normal decisioning."
        allowed = ["monitor", "research", "score", "proof_review"]
        blocked = []
    elif fallback_active and not hard_blocks:
        mode = "FALLBACK_DECISION"
        headline = "Fallback data is usable, but deployable proof should stay conservative."
        allowed = ["monitor", "research", "fallback_score", "manual_review"]
        blocked = ["auto_deploy", "proof_ready_without_fresh_confirmation"]
    elif fallback_active:
        mode = "RECOVERY_RESEARCH"
        headline = "Fallback data can support research, but auth/feed blockers prevent deployable confidence."
        allowed = ["monitor", "research", "fallback_discovery"]
        blocked = ["auto_deploy", "proof_ready", "wallet_reinforcement_overweight"]
    else:
        mode = "RECOVERY_ONLY"
        headline = "Decision engine is in recovery-only mode until core feeds recover."
        allowed = ["monitor", "provider_recovery"]
        blocked = ["auto_deploy", "proof_ready", "new_entries"]

    return {
        "mode": mode,
        "headline": headline,
        "confidence": confidence,
        "fallback_active": fallback_active,
        "market_primary": market_source.get("primary"),
        "allowed_actions": allowed,
        "blocked_actions": blocked,
        "hard_blocks": hard_blocks,
        "soft_blocks": soft_blocks,
    }


def _memecoin_scan_cache_truth(conn: sqlite3.Connection) -> dict:
    meta_row = conn.execute(
        "SELECT value FROM kv_store WHERE key='memecoin_scan_cache_meta'"
    ).fetchone()
    last_row = conn.execute(
        "SELECT value FROM kv_store WHERE key='memecoin_scan_cache_last_nonempty'"
    ).fetchone()

    meta = _json_loads(meta_row[0] if meta_row else None) or {}
    last = _json_loads(last_row[0] if last_row else None) or {}
    latest_scan_utc = _format_ts(meta.get("saved_at"))
    latest_age = _age_minutes(latest_scan_utc)
    latest_count = int(meta.get("count") or 0)
    last_nonempty_utc = _format_ts(last.get("saved_at"))
    last_nonempty_age = _age_minutes(last_nonempty_utc)
    last_signals = list(last.get("signals") or [])
    last_nonempty_count = len(last_signals)
    top = dict(last_signals[0] or {}) if last_signals else {}

    status = "UNKNOWN"
    if latest_age is None:
        status = "NO_SCAN"
    elif latest_age <= 15 and latest_count > 0:
        status = "LIVE_NONEMPTY"
    elif latest_age <= 15:
        status = "FRESH_EMPTY"
    elif latest_age <= 60:
        status = "AGING"
    else:
        status = "STALE"

    if status == "LIVE_NONEMPTY":
        detail = f"Latest scan is fresh and has {latest_count} qualified candidate(s)."
    elif status == "FRESH_EMPTY":
        if last_nonempty_count > 0:
            detail = "Latest scan is fresh but empty, so proof is using the most recent qualified candidate snapshot."
        else:
            detail = "Latest scan is fresh but no candidate survived filters yet."
    elif status == "AGING":
        detail = f"Latest scan is aging ({int(latest_age or 0)}m old); proof may lean on fallback until the next non-empty cycle."
    elif status == "STALE":
        detail = f"Latest scan is stale ({int(latest_age or 0)}m old); scanner freshness needs attention."
    else:
        detail = "No scanner cache metadata is available yet."

    return {
        "status": status,
        "latest_scan_utc": latest_scan_utc,
        "latest_scan_age_minutes": round(latest_age, 1) if latest_age is not None else None,
        "latest_scan_count": latest_count,
        "last_nonempty_scan_utc": last_nonempty_utc,
        "last_nonempty_age_minutes": round(last_nonempty_age, 1) if last_nonempty_age is not None else None,
        "last_nonempty_count": last_nonempty_count,
        "last_nonempty_top_symbol": str(top.get("symbol") or "").upper() or None,
        "last_nonempty_top_mint": str(top.get("mint") or "") or None,
        "detail": detail,
        "provider_status": meta.get("provider_status") or {},
    }


def _memecoin_live_buy_gate(conn: sqlite3.Connection) -> dict:
    if not _table_exists(conn, "memecoin_signal_outcomes"):
        return {
            "status": "UNKNOWN",
            "sample_size": 0,
            "minimum_sample": 30,
            "win_rate_pct": None,
            "pause_threshold_pct": 40.0,
            "live_buy_paused": False,
            "detail": "Memecoin outcome table is unavailable, so the live-buy performance gate cannot be evaluated.",
        }

    rows = conn.execute(
        """
        SELECT return_4h_pct
        FROM memecoin_signal_outcomes
        WHERE rug_label='GOOD' AND return_4h_pct IS NOT NULL
        ORDER BY scanned_at DESC
        LIMIT 30
        """
    ).fetchall()
    sample_size = len(rows)
    threshold_pct = 40.0
    minimum_sample = 30
    win_rate = None
    live_buy_paused = False
    status = "BUILDING_SAMPLE"

    if sample_size >= minimum_sample:
        wins = sum(1 for row in rows if float(row["return_4h_pct"] or 0.0) > 0.0)
        win_rate = (wins / sample_size) * 100.0
        live_buy_paused = win_rate < threshold_pct
        status = "PAUSED" if live_buy_paused else "OPEN"

    if status == "PAUSED":
        detail = f"Live memecoin entries are paused because recent GOOD-token 4h win rate is {win_rate:.1f}% below the {threshold_pct:.0f}% safety floor."
    elif status == "OPEN":
        detail = f"Live memecoin entry gate is open; recent GOOD-token 4h win rate is {win_rate:.1f}%."
    else:
        detail = f"Live memecoin entry gate is building sample ({sample_size}/{minimum_sample} GOOD-token outcomes)."

    return {
        "status": status,
        "sample_size": sample_size,
        "minimum_sample": minimum_sample,
        "win_rate_pct": round(win_rate, 1) if win_rate is not None else None,
        "pause_threshold_pct": threshold_pct,
        "live_buy_paused": live_buy_paused,
        "detail": detail,
    }


def _entry_unlock_summary(
    posture_payload: dict,
    authority_snapshot: dict,
    live_buy_gate: dict,
) -> dict:
    action_law = dict(posture_payload.get("action_law_summary") or {})
    lane_unlock = dict(posture_payload.get("lane_unlock_summary") or {})
    operating_rec = dict(posture_payload.get("operating_recommendation") or {})

    rank_map = {
        "PAPER_EXECUTION_ONLY": 0,
        "MANAGE_AND_PLAN": 1,
        "CONDITIONAL_ROUTING": 2,
        "SELECTIVE_DEPLOYMENT": 3,
        "FULL_DEPLOYMENT": 4,
    }
    highest = str(
        authority_snapshot.get("highest_permitted_action")
        or action_law.get("highest_permitted_action")
        or "UNKNOWN"
    ).strip().upper()
    current_rank = rank_map.get(highest, -1)
    fresh_capital = str(
        authority_snapshot.get("fresh_capital_policy")
        or action_law.get("fresh_capital_policy")
        or "UNKNOWN"
    ).strip().upper()
    action_law_state = str(
        authority_snapshot.get("action_law_state")
        or action_law.get("action_law_state")
        or "UNKNOWN"
    ).strip().upper()
    memecoin_unlock = dict(lane_unlock.get("memecoins") or {})
    spot_unlock = dict(lane_unlock.get("spot") or {})
    live_buy_paused = bool(live_buy_gate.get("live_buy_paused"))

    checks = [
        {
            "key": "law_rank",
            "label": "Authority rank",
            "passed": current_rank >= 3,
            "detail": f"Highest permitted action is {highest}; new entries require SELECTIVE_DEPLOYMENT.",
        },
        {
            "key": "fresh_capital",
            "label": "Fresh capital",
            "passed": fresh_capital in {"SELECTIVE", "OPEN"},
            "detail": f"Fresh capital policy is {fresh_capital}.",
        },
        {
            "key": "memecoin_performance",
            "label": "Memecoin live-buy gate",
            "passed": not live_buy_paused,
            "detail": str(live_buy_gate.get("detail") or "Memecoin live-buy gate unavailable."),
        },
        {
            "key": "memecoin_unlock",
            "label": "Memecoin unlock",
            "passed": str(memecoin_unlock.get("current_state") or "").upper() in {"BACKED", "DEPLOYABLE", "LIVE_READY"},
            "detail": str(memecoin_unlock.get("next_unlock_note") or "Memecoin lane still needs cleaner proof alignment."),
        },
        {
            "key": "spot_unlock",
            "label": "Spot unlock",
            "passed": str(spot_unlock.get("current_state") or "").upper() in {"ADD_READY_SETUP", "BACKED", "DEPLOYABLE", "LIVE_READY"},
            "detail": str(spot_unlock.get("next_unlock_note") or "Spot lane still needs a cleaner add or rotation setup."),
        },
    ]
    unmet = [item for item in checks if not item.get("passed")]

    authority_open = current_rank >= 3 and fresh_capital in {"SELECTIVE", "OPEN"}
    new_entries_open = authority_open and not live_buy_paused
    if new_entries_open:
        status = "OPEN"
        headline = "New-entry authority is open; only setup quality should decide whether to act."
        primary_unlock = "Wait for a clean candidate instead of forcing action."
    elif current_rank < 3:
        status = "AUTHORITY_BLOCKED"
        headline = "New entries are intentionally blocked by authority rank."
        primary_unlock = "Move highest permitted action from conditional routing to selective deployment."
    elif fresh_capital not in {"SELECTIVE", "OPEN"}:
        status = "CAPITAL_CONDITIONAL"
        headline = "New entries are blocked by fresh-capital policy."
        primary_unlock = "Fresh capital policy needs to move to SELECTIVE or OPEN."
    elif live_buy_paused:
        status = "PERFORMANCE_PAUSED"
        headline = "New entries are paused by the recent memecoin performance gate."
        primary_unlock = "Recent GOOD-token 4h win rate needs to recover above the safety floor."
    else:
        status = "SETUP_LOCKED"
        headline = "Authority is close, but lane setup quality still needs improvement."
        primary_unlock = str((operating_rec.get("top_session_action") or {}).get("note") or "Advance the next lane unlock.")

    return {
        "status": status,
        "headline": headline,
        "primary_unlock": primary_unlock,
        "action_law_state": action_law_state,
        "highest_permitted_action": highest,
        "current_rank": current_rank,
        "required_rank": 3,
        "fresh_capital_policy": fresh_capital,
        "authority_open_for_new_entries": authority_open,
        "new_entries_open": new_entries_open,
        "checks": checks,
        "unmet_checks": unmet,
        "top_session_action": operating_rec.get("top_session_action"),
    }


def _recent_decisions(conn: sqlite3.Connection, limit: int = 20) -> list[dict]:
    if not _table_exists(conn, "decision_journal"):
        return []
    rows = conn.execute(
        """
        SELECT id, created_ts, COALESCE(last_seen_ts, created_ts) AS activity_ts,
               COALESCE(surface_count, 1) AS surface_count,
               source_surface, system, symbol, mint,
               recommended_action, priority, reason, blockers_json,
               operator_decision, resolution_status, verdict
        FROM decision_journal
        ORDER BY activity_ts DESC, id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    results: list[dict] = []
    for row in rows:
        blockers = _extract_blocker_labels(_json_loads(row["blockers_json"]))
        results.append(
            {
                "id": int(row["id"]),
                "ts": _format_ts(row["activity_ts"]),
                "source": str(row["source_surface"] or ""),
                "system": str(row["system"] or ""),
                "symbol": str(row["symbol"] or ""),
                "mint": str(row["mint"] or ""),
                "surface_count": int(row["surface_count"] or 1),
                "recommended_action": str(row["recommended_action"] or ""),
                "priority": str(row["priority"] or ""),
                "reason": str(row["reason"] or ""),
                "operator_decision": str(row["operator_decision"] or ""),
                "resolution_status": str(row["resolution_status"] or ""),
                "verdict": str(row["verdict"] or ""),
                "blockers": blockers,
            }
        )
    return results


def _recent_executions(conn: sqlite3.Connection, limit_each: int = 10) -> list[dict]:
    items: list[dict] = []

    if _table_exists(conn, "perp_positions"):
        rows = conn.execute(
            """
            SELECT id, opened_ts_utc, closed_ts_utc, symbol, side, status,
                   dry_run, entry_price, exit_price, pnl_usd, exit_reason
            FROM perp_positions
            ORDER BY COALESCE(closed_ts_utc, opened_ts_utc) DESC, id DESC
            LIMIT ?
            """,
            (limit_each,),
        ).fetchall()
        for row in rows:
            ts = row["closed_ts_utc"] or row["opened_ts_utc"]
            items.append(
                {
                    "kind": "PERP",
                    "id": int(row["id"]),
                    "ts": _format_ts(ts),
                    "symbol": str(row["symbol"] or ""),
                    "status": str(row["status"] or ""),
                    "side": str(row["side"] or ""),
                    "mode": "PAPER" if int(row["dry_run"] or 0) == 1 else "LIVE",
                    "entry_price": row["entry_price"],
                    "exit_price": row["exit_price"],
                    "pnl_usd": row["pnl_usd"],
                    "exit_reason": str(row["exit_reason"] or ""),
                }
            )

    if _table_exists(conn, "memecoin_trades"):
        rows = conn.execute(
            """
            SELECT id, opened_ts_utc, closed_ts_utc, symbol, status,
                   amount_usd, entry_price, exit_price, pnl_usd,
                   is_pilot, proof_status, proof_reason
            FROM memecoin_trades
            ORDER BY COALESCE(closed_ts_utc, opened_ts_utc) DESC, id DESC
            LIMIT ?
            """,
            (limit_each,),
        ).fetchall()
        for row in rows:
            ts = row["closed_ts_utc"] or row["opened_ts_utc"]
            items.append(
                {
                    "kind": "MEMECOIN",
                    "id": int(row["id"]),
                    "ts": _format_ts(ts),
                    "symbol": str(row["symbol"] or ""),
                    "status": str(row["status"] or ""),
                    "mode": "PILOT" if int(row["is_pilot"] or 0) == 1 else "PAPER",
                    "amount_usd": row["amount_usd"],
                    "entry_price": row["entry_price"],
                    "exit_price": row["exit_price"],
                    "pnl_usd": row["pnl_usd"],
                    "proof_status": str(row["proof_status"] or ""),
                    "proof_reason": str(row["proof_reason"] or ""),
                }
            )

    if _table_exists(conn, "spot_buys"):
        rows = conn.execute(
            """
            SELECT id, ts_utc, symbol, side, amount_usd, price_usd, dry_run, tx_sig
            FROM spot_buys
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit_each,),
        ).fetchall()
        for row in rows:
            items.append(
                {
                    "kind": "SPOT",
                    "id": int(row["id"]),
                    "ts": _format_ts(row["ts_utc"]),
                    "symbol": str(row["symbol"] or ""),
                    "side": str(row["side"] or ""),
                    "mode": "PAPER" if int(row["dry_run"] or 0) == 1 else "LIVE",
                    "amount_usd": row["amount_usd"],
                    "price_usd": row["price_usd"],
                    "tx_sig": str(row["tx_sig"] or ""),
                }
            )

    if _table_exists(conn, "whale_watch_alerts"):
        rows = conn.execute(
            """
            SELECT id, ts_utc, token_symbol, scanner_score, scanner_pass,
                   arkham_signal_quality
            FROM whale_watch_alerts
            WHERE scanner_pass=1
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit_each,),
        ).fetchall()
        for row in rows:
            items.append(
                {
                    "kind": "WHALE_SIGNAL",
                    "id": int(row["id"]),
                    "ts": _format_ts(row["ts_utc"]),
                    "symbol": str(row["token_symbol"] or ""),
                    "status": "SCANNER_PASS",
                    "score": row["scanner_score"],
                    "arkham_quality": str(row["arkham_signal_quality"] or ""),
                }
            )

    if _table_exists(conn, "confluence_events"):
        rows = conn.execute(
            """
            SELECT id, ts_utc, token_symbol, confluence_type, confluence_score, outcome_status
            FROM confluence_events
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit_each,),
        ).fetchall()
        for row in rows:
            items.append(
                {
                    "kind": "CONFLUENCE",
                    "id": int(row["id"]),
                    "ts": _format_ts(row["ts_utc"]),
                    "symbol": str(row["token_symbol"] or ""),
                    "status": str(row["outcome_status"] or ""),
                    "confluence_type": str(row["confluence_type"] or ""),
                    "score": row["confluence_score"],
                }
            )

    items.sort(key=lambda x: x.get("ts") or "", reverse=True)
    return items[:40]


def _active_anomalies(conn: sqlite3.Connection, whale_summary: dict, confluence_summary: dict) -> list[dict]:
    anomalies: list[dict] = []

    if _table_exists(conn, "perp_positions"):
        open_bad = conn.execute(
            """
            SELECT COUNT(*) FROM perp_positions
            WHERE status='OPEN'
              AND (closed_ts_utc IS NOT NULL OR exit_price IS NOT NULL OR tx_sig_close IS NOT NULL)
            """
        ).fetchone()[0] or 0
        closed_bad = conn.execute(
            """
            SELECT COUNT(*) FROM perp_positions
            WHERE status='CLOSED'
              AND COALESCE(exit_reason, '') != 'PILOT_CLEANUP'
              AND (closed_ts_utc IS NULL OR exit_price IS NULL)
            """
        ).fetchone()[0] or 0
        if open_bad:
            anomalies.append({
                "label": "PERP_STATE_MISMATCH",
                "severity": "HIGH",
                "detail": f"{open_bad} open perp row(s) carry close-state fields.",
            })
        if closed_bad:
            anomalies.append({
                "label": "PERP_CLOSE_INCOMPLETE",
                "severity": "HIGH",
                "detail": f"{closed_bad} closed perp row(s) are missing exit fields.",
            })

    if _table_exists(conn, "memecoin_trades"):
        trade_bad = conn.execute(
            """
            SELECT COUNT(*) FROM memecoin_trades
            WHERE status='OPEN'
              AND (closed_ts_utc IS NOT NULL OR exit_price IS NOT NULL)
            """
        ).fetchone()[0] or 0
        if trade_bad:
            anomalies.append({
                "label": "MEMECOIN_STATE_MISMATCH",
                "severity": "MEDIUM",
                "detail": f"{trade_bad} open memecoin trade(s) carry close-state fields.",
            })

    whale_total = int(whale_summary.get("total") or 0)
    whale_ingest = str(whale_summary.get("ingest_status") or "").strip().upper()
    if whale_total > 0 and whale_ingest in {"NOT_CONFIGURED", "UNKNOWN"}:
        anomalies.append({
            "label": "WHALE_STATUS_DRIFT",
            "severity": "MEDIUM",
            "detail": "Whale has live rows but its ingest status looks unconfigured.",
        })

    if int(confluence_summary.get("exact_mint_overlap_ready") or 0) > 0 and int(confluence_summary.get("total") or 0) <= 0:
        anomalies.append({
            "label": "CONFLUENCE_WRITE_GAP",
            "severity": "MEDIUM",
            "detail": "Exact mint overlap exists but confluence_events is still empty.",
        })

    try:
        row = conn.execute(
            "SELECT value FROM kv_store WHERE key='pipeline_watchdog_status'"
        ).fetchone()
        wd = _json_loads(row[0] if row else None) or {}
        wd_status = str(wd.get("status") or "").upper()
        age_hours = wd.get("age_hours")
        if wd_status in {"STALE", "NO_DATA"}:
            anomalies.append({
                "label": "PIPELINE_WATCHDOG_ALERT",
                "severity": "HIGH" if wd_status == "STALE" else "MEDIUM",
                "detail": str(wd.get("detail") or f"Pipeline watchdog status is {wd_status}."),
            })
        elif wd_status == "AGING":
            anomalies.append({
                "label": "PIPELINE_WATCHDOG_AGING",
                "severity": "LOW",
                "detail": str(wd.get("detail") or f"Pipeline evaluator age is {age_hours}h."),
            })
    except Exception:
        pass

    return anomalies


def _perp_pattern_audit(conn: sqlite3.Connection) -> dict:
    if not _table_exists(conn, "perp_positions"):
        return {
            "mode": {"executor_enabled": False, "dry_run": True, "scalp_enabled": False},
            "summary": {"closed_trades": 0, "avg_pnl_usd": None, "avg_return_pct": None},
            "by_side": [],
            "by_weekday": [],
            "by_phase": [],
            "by_regime": [],
            "headline": "No perp history yet.",
        }

    executor_enabled = str(os.getenv("PERP_EXECUTOR_ENABLED", "false")).lower() == "true"
    dry_run = str(os.getenv("PERP_DRY_RUN", "true")).lower() == "true"
    scalp_enabled = str(os.getenv("SCALP_ENABLED", "false")).lower() == "true"

    summary_row = conn.execute(
        """
        SELECT
            COUNT(*) AS closed_trades,
            AVG(COALESCE(pnl_usd, 0)) AS avg_pnl_usd,
            AVG(COALESCE(pnl_pct, 0)) AS avg_return_pct
        FROM perp_positions
        WHERE status='CLOSED'
        """
    ).fetchone()

    side_rows = conn.execute(
        """
        SELECT
            UPPER(COALESCE(side, 'UNKNOWN')) AS side,
            COUNT(*) AS n,
            AVG(COALESCE(pnl_usd, 0)) AS avg_pnl_usd,
            AVG(COALESCE(pnl_pct, 0)) AS avg_return_pct,
            SUM(COALESCE(pnl_usd, 0)) AS total_pnl_usd
        FROM perp_positions
        WHERE status='CLOSED'
        GROUP BY UPPER(COALESCE(side, 'UNKNOWN'))
        ORDER BY n DESC
        """
    ).fetchall()

    weekday_rows = conn.execute(
        """
        SELECT
            strftime('%w', COALESCE(closed_ts_utc, opened_ts_utc)) AS weekday_num,
            COUNT(*) AS n,
            AVG(COALESCE(pnl_usd, 0)) AS avg_pnl_usd,
            AVG(COALESCE(pnl_pct, 0)) AS avg_return_pct,
            SUM(COALESCE(pnl_usd, 0)) AS total_pnl_usd
        FROM perp_positions
        WHERE status='CLOSED'
        GROUP BY weekday_num
        ORDER BY weekday_num
        """
    ).fetchall()

    regime_rows = conn.execute(
        """
        SELECT
            COALESCE(regime_label, 'UNKNOWN') AS regime_label,
            COUNT(*) AS n,
            AVG(COALESCE(pnl_usd, 0)) AS avg_pnl_usd,
            AVG(COALESCE(pnl_pct, 0)) AS avg_return_pct,
            SUM(COALESCE(pnl_usd, 0)) AS total_pnl_usd
        FROM perp_positions
        WHERE status='CLOSED'
        GROUP BY COALESCE(regime_label, 'UNKNOWN')
        ORDER BY n DESC
        """
    ).fetchall()

    weekday_name = {
        "0": "Sun",
        "1": "Mon",
        "2": "Tue",
        "3": "Wed",
        "4": "Thu",
        "5": "Fri",
        "6": "Sat",
    }
    phase_map = {
        "Sun": "WEEKEND",
        "Sat": "WEEKEND",
        "Mon": "EARLY_WEEK",
        "Tue": "EARLY_WEEK",
        "Wed": "MIDWEEK",
        "Thu": "MIDWEEK",
        "Fri": "LATE_WEEK",
    }

    by_weekday: list[dict] = []
    phase_accum: dict[str, dict] = {}
    for row in weekday_rows:
        wd = weekday_name.get(str(row["weekday_num"] or ""), "Unknown")
        item = {
            "weekday": wd,
            "count": int(row["n"] or 0),
            "avg_pnl_usd": round(float(row["avg_pnl_usd"] or 0.0), 4),
            "avg_return_pct": round(float(row["avg_return_pct"] or 0.0), 4),
            "total_pnl_usd": round(float(row["total_pnl_usd"] or 0.0), 4),
        }
        by_weekday.append(item)

        phase = phase_map.get(wd, "OTHER")
        bucket = phase_accum.setdefault(
            phase,
            {"phase": phase, "count": 0, "total_pnl_usd": 0.0, "sum_avg_return_weighted": 0.0},
        )
        bucket["count"] += item["count"]
        bucket["total_pnl_usd"] += item["total_pnl_usd"]
        bucket["sum_avg_return_weighted"] += item["avg_return_pct"] * item["count"]

    by_phase: list[dict] = []
    for phase in ("WEEKEND", "EARLY_WEEK", "MIDWEEK", "LATE_WEEK", "OTHER"):
        bucket = phase_accum.get(phase)
        if not bucket or bucket["count"] <= 0:
            continue
        by_phase.append(
            {
                "phase": phase,
                "count": int(bucket["count"]),
                "total_pnl_usd": round(float(bucket["total_pnl_usd"]), 4),
                "avg_return_pct": round(float(bucket["sum_avg_return_weighted"]) / max(int(bucket["count"]), 1), 4),
            }
        )

    by_side = [
        {
            "side": str(row["side"] or "UNKNOWN"),
            "count": int(row["n"] or 0),
            "avg_pnl_usd": round(float(row["avg_pnl_usd"] or 0.0), 4),
            "avg_return_pct": round(float(row["avg_return_pct"] or 0.0), 4),
            "total_pnl_usd": round(float(row["total_pnl_usd"] or 0.0), 4),
        }
        for row in side_rows
    ]
    by_regime = [
        {
            "regime_label": str(row["regime_label"] or "UNKNOWN"),
            "count": int(row["n"] or 0),
            "avg_pnl_usd": round(float(row["avg_pnl_usd"] or 0.0), 4),
            "avg_return_pct": round(float(row["avg_return_pct"] or 0.0), 4),
            "total_pnl_usd": round(float(row["total_pnl_usd"] or 0.0), 4),
        }
        for row in regime_rows
    ]

    weekend = next((row for row in by_phase if row["phase"] == "WEEKEND"), None)
    midweek = next((row for row in by_phase if row["phase"] == "MIDWEEK"), None)
    shorts = next((row for row in by_side if row["side"] == "SHORT"), None)
    longs = next((row for row in by_side if row["side"] == "LONG"), None)

    headline_parts: list[str] = []
    if weekend and weekend["total_pnl_usd"] > 0:
        headline_parts.append("Weekend perps have been net positive.")
    if midweek and midweek["total_pnl_usd"] < 0:
        headline_parts.append("Midweek has been the weakest perp window.")
    if shorts and longs and shorts["count"] > longs["count"] * 3:
        headline_parts.append("History is still heavily short-skewed.")
    headline = " ".join(headline_parts) or "Perp history exists, but no strong pattern stands out yet."

    return {
        "mode": {
            "executor_enabled": executor_enabled,
            "dry_run": dry_run,
            "scalp_enabled": scalp_enabled,
        },
        "summary": {
            "closed_trades": int(summary_row["closed_trades"] or 0) if summary_row else 0,
            "avg_pnl_usd": round(float(summary_row["avg_pnl_usd"] or 0.0), 4) if summary_row else None,
            "avg_return_pct": round(float(summary_row["avg_return_pct"] or 0.0), 4) if summary_row else None,
        },
        "by_side": by_side,
        "by_weekday": by_weekday,
        "by_phase": by_phase,
        "by_regime": by_regime,
        "headline": headline,
    }


def _manual_perp_journal_summary(conn: sqlite3.Connection, limit: int = 8) -> dict:
    if not _table_exists(conn, "manual_perp_journal"):
        return {
            "summary": {"total_events": 0, "realized_events": 0},
            "by_action": [],
            "recent": [],
            "headline": "No manual perp journal entries yet.",
        }

    summary_row = conn.execute(
        """
        SELECT
            COUNT(*) AS total_events,
            SUM(CASE WHEN realized_pnl_usd IS NOT NULL THEN 1 ELSE 0 END) AS realized_events,
            SUM(COALESCE(realized_pnl_usd, 0.0)) AS total_realized_pnl_usd,
            AVG(realized_pnl_usd) AS avg_realized_pnl_usd,
            MIN(ts_utc) AS first_event_ts,
            MAX(ts_utc) AS last_event_ts
        FROM manual_perp_journal
        WHERE symbol = 'SOL'
        """
    ).fetchone()
    action_rows = conn.execute(
        """
        SELECT
            action,
            side,
            COUNT(*) AS n,
            SUM(COALESCE(realized_pnl_usd, 0.0)) AS total_realized_pnl_usd,
            AVG(realized_pnl_usd) AS avg_realized_pnl_usd
        FROM manual_perp_journal
        WHERE symbol = 'SOL'
        GROUP BY action, side
        ORDER BY n DESC, action ASC
        """
    ).fetchall()
    recent_rows = conn.execute(
        """
        SELECT
            id,
            ts_utc,
            symbol,
            action,
            side,
            price,
            size_usd,
            deposit_withdraw_usd,
            fee_usd,
            realized_pnl_usd,
            source,
            notes
        FROM manual_perp_journal
        WHERE symbol = 'SOL'
        ORDER BY ts_utc DESC, id DESC
        LIMIT ?
        """,
        (max(1, int(limit)),),
    ).fetchall()

    total_events = int(summary_row["total_events"] or 0) if summary_row else 0
    realized_events = int(summary_row["realized_events"] or 0) if summary_row else 0
    total_realized = round(float(summary_row["total_realized_pnl_usd"] or 0.0), 4) if summary_row else 0.0
    avg_realized = round(float(summary_row["avg_realized_pnl_usd"] or 0.0), 4) if summary_row and summary_row["avg_realized_pnl_usd"] is not None else None

    by_action = [
        {
            "action": str(row["action"] or ""),
            "side": str(row["side"] or ""),
            "count": int(row["n"] or 0),
            "total_realized_pnl_usd": round(float(row["total_realized_pnl_usd"] or 0.0), 4),
            "avg_realized_pnl_usd": round(float(row["avg_realized_pnl_usd"] or 0.0), 4) if row["avg_realized_pnl_usd"] is not None else None,
        }
        for row in action_rows
    ]
    recent = [
        {
            "id": int(row["id"]),
            "ts": _format_ts(row["ts_utc"]),
            "symbol": str(row["symbol"] or ""),
            "action": str(row["action"] or ""),
            "side": str(row["side"] or ""),
            "price": row["price"],
            "size_usd": row["size_usd"],
            "deposit_withdraw_usd": row["deposit_withdraw_usd"],
            "fee_usd": row["fee_usd"],
            "realized_pnl_usd": row["realized_pnl_usd"],
            "source": str(row["source"] or ""),
            "notes": str(row["notes"] or ""),
        }
        for row in recent_rows
    ]

    headline_parts: list[str] = []
    if realized_events:
        if total_realized > 0:
            headline_parts.append(f"Manual SOL scalps are net positive across {realized_events} realized exits.")
        elif total_realized < 0:
            headline_parts.append(f"Manual SOL scalps are net negative across {realized_events} realized exits.")
    increase_shorts = next((row for row in by_action if row["action"] == "INCREASE_SHORT"), None)
    increase_longs = next((row for row in by_action if row["action"] == "INCREASE_LONG"), None)
    if increase_shorts and increase_longs:
        if increase_shorts["count"] > increase_longs["count"]:
            headline_parts.append("Recent manual flow has leaned more short than long.")
        elif increase_longs["count"] > increase_shorts["count"]:
            headline_parts.append("Recent manual flow has leaned more long than short.")
    if total_events and total_events < 10:
        headline_parts.append("Sample is still partial and screenshot-derived.")
    headline = " ".join(headline_parts) or "Manual SOL scalp journal is ready for entries."

    return {
        "summary": {
            "total_events": total_events,
            "realized_events": realized_events,
            "total_realized_pnl_usd": total_realized,
            "avg_realized_pnl_usd": avg_realized,
            "first_event_ts": _format_ts(summary_row["first_event_ts"]) if summary_row else None,
            "last_event_ts": _format_ts(summary_row["last_event_ts"]) if summary_row else None,
        },
        "by_action": by_action,
        "recent": recent,
        "headline": headline,
    }


def _sol_perp_bias_snapshot(perp_patterns: dict, manual_perp_journal: dict, best_action: dict) -> dict:
    eastern_now = datetime.now(ZoneInfo("America/New_York"))
    weekday = eastern_now.strftime("%a")
    phase_map = {
        "Sat": "WEEKEND",
        "Sun": "WEEKEND",
        "Mon": "EARLY_WEEK",
        "Tue": "EARLY_WEEK",
        "Wed": "MIDWEEK",
        "Thu": "MIDWEEK",
        "Fri": "LATE_WEEK",
    }
    current_phase = phase_map.get(weekday, "OTHER")

    phase_row = next(
        (row for row in (perp_patterns.get("by_phase") or []) if str(row.get("phase") or "") == current_phase),
        None,
    )
    short_realized = next(
        (row for row in (manual_perp_journal.get("by_action") or []) if row.get("action") == "DECREASE_SHORT"),
        None,
    )
    long_realized = next(
        (row for row in (manual_perp_journal.get("by_action") or []) if row.get("action") == "DECREASE_LONG"),
        None,
    )

    historical_bias = "NEUTRAL"
    historical_note = "Historical perp sample is too thin for this phase."
    phase_avg = None
    phase_count = 0
    if phase_row:
        phase_avg = float(phase_row.get("avg_return_pct") or 0.0)
        phase_count = int(phase_row.get("count") or 0)
        if phase_count >= 10:
            historical_bias = "LONG" if phase_avg > 0 else ("SHORT" if phase_avg < 0 else "NEUTRAL")
            historical_note = (
                f"{current_phase.replace('_', ' ').title()} has averaged "
                f"{phase_avg:+.2f}% across {phase_count} closed system perp trades."
            )
        else:
            historical_note = f"{current_phase.replace('_', ' ').title()} only has {phase_count} closed system perp trades."

    manual_bias = "NEUTRAL"
    manual_note = "Manual SOL sample is still too small."
    short_total = float(short_realized.get("total_realized_pnl_usd") or 0.0) if short_realized else 0.0
    long_total = float(long_realized.get("total_realized_pnl_usd") or 0.0) if long_realized else 0.0
    realized_count = int(manual_perp_journal.get("summary", {}).get("realized_events") or 0)
    if realized_count >= 2:
        if short_total > long_total:
            manual_bias = "SHORT"
        elif long_total > short_total:
            manual_bias = "LONG"
        manual_note = (
            f"Manual SOL journal is {short_total:+.2f} on short exits vs {long_total:+.2f} on long exits "
            f"across {realized_count} realized entries."
        )

    system_bias = str(best_action.get("action") or "NEUTRAL").upper() if str(best_action.get("arm") or "") == "PERPS" else "NEUTRAL"

    if historical_bias != "NEUTRAL" and manual_bias != "NEUTRAL" and historical_bias == manual_bias:
        combined_bias = historical_bias
        alignment = "ALIGNED"
    elif historical_bias == "NEUTRAL" and manual_bias != "NEUTRAL":
        combined_bias = manual_bias
        alignment = "MANUAL_LED"
    elif manual_bias == "NEUTRAL" and historical_bias != "NEUTRAL":
        combined_bias = historical_bias
        alignment = "SYSTEM_LED"
    elif historical_bias != "NEUTRAL" and manual_bias != "NEUTRAL" and historical_bias != manual_bias:
        combined_bias = system_bias if system_bias != "NEUTRAL" else historical_bias
        alignment = "MIXED"
    else:
        combined_bias = system_bias
        alignment = "THIN"

    if alignment == "ALIGNED":
        headline = f"{current_phase.replace('_', ' ').title()} currently leans {combined_bias.lower()} across both system history and your manual sample."
    elif alignment == "MIXED":
        headline = f"{current_phase.replace('_', ' ').title()} has mixed signals between system history and your manual SOL sample."
    elif alignment == "MANUAL_LED":
        headline = f"Manual SOL sample currently leans {combined_bias.lower()}, but system phase history is still thin or neutral."
    elif alignment == "SYSTEM_LED":
        headline = f"System perp history currently leans {combined_bias.lower()} for {current_phase.replace('_', ' ').lower()}, but manual sample is still thin."
    else:
        headline = "SOL perp bias is still building; the system does not have enough aligned evidence yet."

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "current_weekday": weekday,
        "current_phase": current_phase,
        "historical_bias": historical_bias,
        "manual_bias": manual_bias,
        "system_bias": system_bias,
        "combined_bias": combined_bias,
        "alignment": alignment,
        "headline": headline,
        "historical_note": historical_note,
        "manual_note": manual_note,
        "phase_stats": phase_row,
        "manual_stats": {
            "realized_events": realized_count,
            "short_realized_pnl_usd": round(short_total, 4),
            "long_realized_pnl_usd": round(long_total, 4),
        },
    }


def _focus_build_summary(posture: dict) -> dict:
    operating_mode = dict(posture.get("operating_mode_summary") or {})
    lane_policy = dict(posture.get("lane_policy_summary") or {})
    lane_unlock = dict(posture.get("lane_unlock_summary") or {})
    memecoins = dict(posture.get("memecoins") or {})
    spot = dict(posture.get("spot") or {})
    proof_stack = dict(posture.get("proof_stack") or {})

    check_labels = {
        "proof_stack_backed": "proof stack",
        "route_window_open": "route window",
        "headroom_available": "headroom",
        "lane_authority_constructive": "lane authority",
        "signal_confidence_ready": "signal confidence",
        "add_ready_setup_present": "add-ready setup",
    }

    def _unmet_checks(unlock: dict) -> list[dict]:
        items: list[dict] = []
        for raw in list(unlock.get("checklist") or []):
            if raw.get("passed"):
                continue
            key = str(raw.get("name") or "").strip()
            items.append(
                {
                    "key": key,
                    "label": check_labels.get(key, key.replace("_", " ").strip() or "check"),
                    "note": str(raw.get("note") or "").strip(),
                }
            )
        return items

    def _dedupe_keep_order(values: list[str]) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for value in values:
            text = str(value or "").strip()
            if not text or text in seen:
                continue
            seen.add(text)
            out.append(text)
        return out

    route_bucket = str(operating_mode.get("route_bucket") or "UNKNOWN").strip().upper() or "UNKNOWN"
    window_bucket = str(operating_mode.get("window_bucket") or "UNKNOWN").strip().upper() or "UNKNOWN"
    headroom_bucket = str(operating_mode.get("headroom_bucket") or "UNKNOWN").strip().upper() or "UNKNOWN"

    meme_unlock = dict(lane_unlock.get("memecoins") or {})
    meme_next = str(meme_unlock.get("next_unlock_state") or "MAINTAIN").strip().upper() or "MAINTAIN"
    meme_checks_passed = int(meme_unlock.get("passed_checks") or 0)
    meme_checks_total = int(meme_unlock.get("total_checks") or 0)
    meme_unmet = _unmet_checks(meme_unlock)
    proof_ready_now = int(proof_stack.get("proof_ready_now") or 0)
    proof_input_source = str(proof_stack.get("proof_input_source") or "UNKNOWN").strip().upper() or "UNKNOWN"
    proof_blockers = list(proof_stack.get("current_blockers") or [])
    proof_blocker_labels = [
        str(item.get("key") or item.get("reason") or item or "").strip()
        for item in proof_blockers[:3]
        if str(item.get("key") or item.get("reason") or item or "").strip()
    ]
    proof_blocker_details = []
    for raw in proof_blockers[:3]:
        if not isinstance(raw, dict):
            continue
        key = str(raw.get("key") or "").strip()
        reason = str(raw.get("reason") or "").strip()
        if not key and not reason:
            continue
        proof_blocker_details.append(
            {
                "key": key or reason,
                "label": key.replace("_", " ").strip().title() if key else "blocker",
                "reason": _clean_memecoin_blocker_reason(key, reason),
            }
        )

    if proof_ready_now > 0 and route_bucket in ("DISCIPLINED_PROBE", "SCALE_READY"):
        meme_state = "NEAR_DEPLOYABLE"
        meme_headline = "Memecoin lane has at least one proof-ready candidate and an open route bucket."
    elif route_bucket in ("DISCIPLINED_PROBE", "SCALE_READY") and meme_checks_passed >= max(meme_checks_total - 1, 1):
        meme_state = "PROMOTING"
        meme_headline = "Memecoin lane is close to usable deployment but still needs one cleaner authority step."
    else:
        meme_state = "BUILDING"
        meme_headline = "Memecoin lane still needs stronger route + proof alignment before it becomes truly usable."

    meme_next_step = str(meme_unlock.get("next_unlock_note") or "Memecoin lane needs stronger continuation/proof alignment.")
    if proof_blocker_labels:
        meme_next_step = f"{meme_next_step} Current blockers: {', '.join(proof_blocker_labels)}."

    meme_move_steps: list[str] = []
    for item in proof_blocker_details:
        if item["key"] == "relaxed_path_tightened":
            meme_move_steps.append("Exact reinforcement has to reappear on a current candidate.")
            meme_move_steps.append("Proof structure has to tighten enough for the relaxed path to stop being a near-miss.")
        else:
            meme_move_steps.append(item["reason"])
    for item in meme_unmet:
        if item["key"] == "proof_stack_backed":
            meme_move_steps.append("Proof stack authority needs to move from tentative to backed.")
        elif item["key"] == "lane_authority_constructive":
            meme_move_steps.append("Lane authority needs to improve from tentative to backed.")
        elif item["key"] == "route_window_open":
            meme_move_steps.append("The route window has to stay open when a cleaner candidate appears.")
        elif item["key"] == "headroom_available":
            meme_move_steps.append("Capital headroom needs to stay workable for memecoin deployment.")
        elif item["note"]:
            meme_move_steps.append(item["note"])
    if proof_input_source != "LIVE_CACHE":
        meme_move_steps.append(
            f"Proof review is currently leaning on {proof_input_source.lower().replace('_', ' ')} instead of a clean live-cache cycle."
        )
    meme_move_steps = _dedupe_keep_order(meme_move_steps)[:4]
    if proof_input_source != "LIVE_CACHE":
        meme_headline = "Memecoin lane is still building, and current candidate freshness is being softened by fallback proof input."

    spot_unlock = dict(lane_unlock.get("spot") or {})
    spot_next = str(spot_unlock.get("next_unlock_state") or "MAINTAIN").strip().upper() or "MAINTAIN"
    spot_checks_passed = int(spot_unlock.get("passed_checks") or 0)
    spot_checks_total = int(spot_unlock.get("total_checks") or 0)
    spot_unmet = _unmet_checks(spot_unlock)
    spot_policy = dict(lane_policy.get("spot") or {})
    spot_source_posture = str(spot_policy.get("source_posture") or "").strip().upper() or "UNKNOWN"
    signal_conf = str(spot.get("signal_confidence") or "pending").strip().lower()
    spot_wr = spot.get("win_rate_7d")
    holdings_count = int(spot.get("holdings_count") or 0)
    basket_size = int(spot.get("basket_size") or 0)

    if signal_conf == "high" and spot_next in ("ADD_READY_SETUP", "BACKED_MANUAL"):
        spot_state = "CLOSE"
        spot_headline = "Spot lane quality is respectable, but it still needs a cleaner add-ready setup."
    elif signal_conf in ("high", "medium"):
        spot_state = "WATCHING"
        spot_headline = "Spot lane is constructive, but not yet giving us a clear add."
    else:
        spot_state = "THIN"
        spot_headline = "Spot lane still needs stronger signal confidence before we should push it harder."

    spot_next_step = str(spot_unlock.get("next_unlock_note") or "Spot lane needs at least one add-ready setup to strengthen its authority.")
    spot_context = ""
    if spot_source_posture == "AT_CAPACITY":
        spot_context = "Basket is already full, so the next spot improvement probably needs to be rotation-worthy, not just decent."
    elif holdings_count > 0:
        spot_context = "We already have spot exposure, so the next step is quality of adds, not forcing more names."

    spot_move_steps: list[str] = []
    for item in spot_unmet:
        if item["key"] == "add_ready_setup_present":
            spot_move_steps.append("At least one spot setup has to become cleanly add-ready.")
        elif item["key"] == "lane_authority_constructive":
            spot_move_steps.append("Spot lane authority needs to improve from tentative to backed.")
        elif item["key"] == "signal_confidence_ready":
            spot_move_steps.append("Signal confidence needs to hold at medium or high.")
        elif item["note"]:
            spot_move_steps.append(item["note"])
    if spot_context:
        spot_move_steps.append(spot_context)
    spot_move_steps = _dedupe_keep_order(spot_move_steps)[:4]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "memecoin_route": {
            "state": meme_state,
            "headline": meme_headline,
            "next_unlock_state": meme_next,
            "next_step": meme_next_step,
            "route_bucket": route_bucket,
            "window_bucket": window_bucket,
            "headroom_bucket": headroom_bucket,
            "proof_ready_now": proof_ready_now,
            "proof_input_source": proof_input_source,
            "unlock_progress": f"{meme_checks_passed}/{meme_checks_total}" if meme_checks_total > 0 else "—",
            "blockers": proof_blocker_labels,
            "blocker_details": proof_blocker_details,
            "unmet_checks": meme_unmet,
            "moves_to_backed": meme_move_steps,
            "outcomes": memecoins.get("outcomes"),
            "win_rate_pct": memecoins.get("wr_pct"),
        },
        "spot_adds": {
            "state": spot_state,
            "headline": spot_headline,
            "next_unlock_state": spot_next,
            "next_step": spot_next_step,
            "signal_confidence": signal_conf,
            "unlock_progress": f"{spot_checks_passed}/{spot_checks_total}" if spot_checks_total > 0 else "—",
            "holdings_count": holdings_count,
            "basket_size": basket_size,
            "win_rate_7d": spot_wr,
            "source_posture": spot_source_posture,
            "unmet_checks": spot_unmet,
            "moves_to_add_ready": spot_move_steps,
            "context_note": spot_context,
        },
    }


def _memecoin_graduation_tracker(graduation: dict, proof_stack: dict) -> dict:
    lane_state = dict(graduation.get("lane_state") or {})
    proof_slots = dict(graduation.get("proof_slots") or {})
    policy = dict(graduation.get("policy_recommendation") or {})
    cohorts = list(graduation.get("cohorts") or [])
    top_constraints = list(graduation.get("top_constraints") or [])
    recent_promotions = list(graduation.get("recent_promotions") or [])
    recent_degradations = list(graduation.get("recent_degradations") or [])

    graduation_state = str(lane_state.get("graduation_state") or "EXPERIMENTAL").strip().upper() or "EXPERIMENTAL"
    proof_auth = str(lane_state.get("proof_authority") or "TENTATIVE").strip().upper() or "TENTATIVE"
    reinforcement_auth = str(lane_state.get("reinforcement_authority") or "ABSENT").strip().upper() or "ABSENT"
    deployment_auth = str(lane_state.get("deployment_authority") or "BLOCKED").strip().upper() or "BLOCKED"
    proof_ready_now = int(proof_stack.get("proof_ready_now") or 0)
    slot_state = str(proof_slots.get("slot_state") or "UNKNOWN").strip().upper() or "UNKNOWN"

    ladder = [
        {
            "key": "RELAXED",
            "label": "relaxed",
            "status": "DONE" if graduation_state in ("ACCUMULATING", "EARNING_TRUST", "PROMOTIVE") else "ACTIVE",
            "note": "Scanner names are reaching the relaxed path, but they still need cleaner proof support.",
        },
        {
            "key": "REINFORCED",
            "label": "reinforced",
            "status": "DONE" if reinforcement_auth in ("BACKED", "FORCEFUL") else "ACTIVE",
            "note": "Cross-lane reinforcement is the next quality jump for memecoin candidates.",
        },
        {
            "key": "BACKED",
            "label": "backed",
            "status": "DONE" if proof_auth in ("BACKED", "FORCEFUL", "STACKED") else "ACTIVE",
            "note": "Proof stack and lane authority both need to move above tentative.",
        },
        {
            "key": "DEPLOYABLE",
            "label": "deployable",
            "status": "DONE" if deployment_auth in ("LIMITED", "ENABLED") else "WAITING",
            "note": "Deployment authority only opens after cleaner proof and reinforcement alignment.",
        },
    ]

    if deployment_auth in ("LIMITED", "ENABLED"):
        headline = "Memecoin route has moved beyond pure buildup and is earning controlled deployment authority."
    elif proof_ready_now > 0:
        headline = "Memecoin route is close enough that the next proof-quality improvement could matter immediately."
    else:
        headline = "Memecoin route is still graduating through reinforcement and proof quality rather than lacking names."

    promoted = [c for c in cohorts if str(c.get("verdict") or "").upper() == "PROMOTE"]
    tightening = [c for c in cohorts if str(c.get("verdict") or "").upper() == "TIGHTEN"]
    strongest = promoted[:2] or cohorts[:2]
    weakest = tightening[:2]

    return {
        "state": graduation_state,
        "headline": headline,
        "proof_authority": proof_auth,
        "reinforcement_authority": reinforcement_auth,
        "deployment_authority": deployment_auth,
        "proof_ready_now": proof_ready_now,
        "slot_state": slot_state,
        "used_slots": int(proof_slots.get("used_slots") or 0),
        "recommended_slots": int(proof_slots.get("recommended_slots") or 0),
        "ladder": ladder,
        "strongest_cohorts": [
            {
                "label": str(item.get("cohort_label") or item.get("cohort_key") or "cohort"),
                "verdict": str(item.get("verdict") or "UNKNOWN"),
                "sample_n": int(item.get("sample_n") or 0),
                "avg_24h": item.get("avg_24h"),
            }
            for item in strongest
        ],
        "weakest_cohorts": [
            {
                "label": str(item.get("cohort_label") or item.get("cohort_key") or "cohort"),
                "verdict": str(item.get("verdict") or "UNKNOWN"),
                "sample_n": int(item.get("sample_n") or 0),
                "avg_24h": item.get("avg_24h"),
            }
            for item in weakest
        ],
        "top_constraints": [
            {
                "key": str(item.get("key") or ""),
                "count": int(item.get("count") or 0),
            }
            for item in top_constraints[:4]
        ],
        "recent_promotions": recent_promotions[:3],
        "recent_degradations": recent_degradations[:3],
        "next_policy_move": str(policy.get("deployment_recommendation") or ""),
    }


def _spot_decision_layer(spot_summary: dict, spot_posture: dict, lane_unlock: dict) -> dict:
    conf = str(spot_summary.get("signal_confidence") or "pending").strip().lower() or "pending"
    holdings = int(spot_summary.get("holdings_count") or 0)
    basket = int(spot_summary.get("basket_size") or 0)
    win_rate_7d = spot_summary.get("win_rate_7d")
    actionable = int(spot_posture.get("actionable_setups") or 0)
    poor_conditions = int(spot_posture.get("poor_conditions") or 0)
    posture_name = str(spot_posture.get("posture") or "WAITING").strip().upper() or "WAITING"
    posture_detail = str(spot_posture.get("detail") or "").strip()

    spot_unlock = dict((lane_unlock or {}).get("spot") or {})
    unmet = [dict(item) for item in list(spot_unlock.get("checklist") or []) if not item.get("passed")]

    if actionable > 0 and holdings < basket:
        action = "ADD"
        headline = "Spot has room and at least one actionable setup, so adds are the live decision."
    elif actionable > 0 and holdings >= basket:
        action = "ROTATE"
        headline = "Spot has actionable quality, but the basket is already full, so the next decision is rotation."
    elif holdings > 0 and conf in ("high", "medium"):
        action = "HOLD"
        headline = "Spot quality is decent, but there is no clean add or rotation candidate yet."
    else:
        action = "WAIT"
        headline = "Spot is still a watch lane right now; no clean add or rotation decision is ready."

    why_not = []
    if actionable <= 0:
        why_not.append("No add-ready spot setup is live.")
    if holdings >= basket and basket > 0:
        why_not.append("The basket is already full, so a new buy has to be rotation-worthy.")
    if poor_conditions > 0:
        why_not.append(f"Poor add conditions are active on {poor_conditions} spot name(s).")
    for item in unmet[:2]:
        note = str(item.get("note") or "").strip()
        if note:
            why_not.append(note)

    next_moves = []
    if actionable <= 0:
        next_moves.append("Wait for at least one spot setup to become clearly add-ready.")
    if holdings >= basket and basket > 0:
        next_moves.append("Prefer a rotate decision over a blind add while the basket stays full.")
    if conf not in ("high", "medium"):
        next_moves.append("Signal confidence needs to recover before forcing spot action.")
    else:
        next_moves.append("Keep spot on watch until quality improves enough to justify a new add or swap.")

    deduped_next_moves = []
    for move in next_moves:
        if move not in deduped_next_moves:
            deduped_next_moves.append(move)

    return {
        "action": action,
        "headline": headline,
        "posture": posture_name,
        "detail": posture_detail,
        "signal_confidence": conf,
        "holdings_count": holdings,
        "basket_size": basket,
        "actionable_setups": actionable,
        "poor_conditions": poor_conditions,
        "win_rate_7d": win_rate_7d,
        "why_not_now": why_not[:3],
        "next_moves": deduped_next_moves[:3],
    }


def _memecoin_reinforcement_memory(graduation: dict) -> dict:
    lane_state = dict(graduation.get("lane_state") or {})
    cohorts = {str(item.get("cohort_key") or ""): dict(item) for item in list(graduation.get("cohorts") or [])}
    proof_auth = str(lane_state.get("proof_authority") or "TENTATIVE").strip().upper() or "TENTATIVE"
    reinforcement_auth = str(lane_state.get("reinforcement_authority") or "ABSENT").strip().upper() or "ABSENT"

    reinforced = cohorts.get("REINFORCED") or {}
    normal_reinforced = cohorts.get("NORMAL_REINFORCED") or {}
    relaxed_reinforced = cohorts.get("RELAXED_REINFORCED") or {}
    unreinforced = cohorts.get("UNREINFORCED") or {}

    exact_or_normal_support = int(normal_reinforced.get("sample_n") or 0)
    relaxed_support = int(relaxed_reinforced.get("sample_n") or 0)
    standalone = int(unreinforced.get("sample_n") or 0)
    reinforced_total = int(reinforced.get("sample_n") or 0)

    if reinforcement_auth in ("BACKED", "FORCEFUL") and exact_or_normal_support >= 3:
        headline = "Reinforcement memory is becoming decision-worthy, especially on normal reinforced names."
    elif reinforced_total > 0:
        headline = "Reinforcement is showing up, but it is still too thin or too mixed to unlock much on its own."
    else:
        headline = "Reinforcement memory is still mostly absent, so memecoin trust is leaning too hard on standalone proof."

    return {
        "headline": headline,
        "reinforcement_authority": reinforcement_auth,
        "proof_authority": proof_auth,
        "normal_reinforced": {
            "sample_n": exact_or_normal_support,
            "verdict": str(normal_reinforced.get("verdict") or "TOO_THIN"),
            "avg_24h": normal_reinforced.get("avg_24h"),
        },
        "relaxed_reinforced": {
            "sample_n": relaxed_support,
            "verdict": str(relaxed_reinforced.get("verdict") or "TOO_THIN"),
            "avg_24h": relaxed_reinforced.get("avg_24h"),
        },
        "standalone_proof": {
            "sample_n": standalone,
            "verdict": str(unreinforced.get("verdict") or "TOO_THIN"),
            "avg_24h": unreinforced.get("avg_24h"),
        },
        "next_unlock_hint": (
            "Exact or normal reinforced cohorts need more clean outcomes before relaxed names can graduate."
            if relaxed_support <= exact_or_normal_support
            else "Relaxed reinforced names are finally building enough history to justify a stricter review."
        ),
    }


def _memecoin_rep_depth(
    proof_stack: dict,
    memecoin_trade_count: int,
    memecoin_exit_review_count: int,
    memecoin_exit_snapshot_count: int,
) -> dict:
    build_trades = dict(proof_stack.get("proof_build_trades") or {})
    build_outcomes = dict(proof_stack.get("proof_build_outcomes") or {})
    return_4h = dict(build_outcomes.get("return_4h") or {})
    return_24h = dict(build_outcomes.get("return_24h") or {})

    total_proof_trades = int(build_trades.get("total") or 0)
    closed_proof_trades = int(build_trades.get("closed") or 0)
    proof_outcome_count = int(build_outcomes.get("count") or 0)
    complete_4h = int(return_4h.get("complete") or 0)
    complete_24h = int(return_24h.get("complete") or 0)

    target_proof_outcomes = 12
    target_24h = 8
    target_exit_reviews = 10

    if proof_outcome_count >= target_proof_outcomes and complete_24h >= target_24h and memecoin_exit_review_count >= target_exit_reviews:
        state = "MATURE"
        headline = "Memecoin learning sample is finally deep enough to start trusting route-level lessons more."
    elif proof_outcome_count >= 4 or memecoin_trade_count >= 4:
        state = "BUILDING"
        headline = "Memecoin reps are coming through, but the system still needs more full lifecycle closes before tuning gets real."
    else:
        state = "THIN"
        headline = "Memecoin learning is still very sample-thin; most of the lane is explaining more than it is proving."

    missing = []
    if proof_outcome_count < target_proof_outcomes:
        missing.append(f"{target_proof_outcomes - proof_outcome_count} more proof outcomes")
    if complete_24h < target_24h:
        missing.append(f"{target_24h - complete_24h} more 24h completions")
    if memecoin_exit_review_count < target_exit_reviews:
        missing.append(f"{target_exit_reviews - memecoin_exit_review_count} more exit reviews")

    next_rep_goal = (
        "Need " + ", ".join(missing[:3]) + " before the memecoin lane has enough completed learning depth."
        if missing else
        "Sample depth is finally strong enough to support more confident memecoin tuning."
    )

    return {
        "state": state,
        "headline": headline,
        "proof_trades_total": total_proof_trades,
        "proof_trades_closed": closed_proof_trades,
        "proof_outcomes": proof_outcome_count,
        "complete_4h": complete_4h,
        "complete_24h": complete_24h,
        "memecoin_trades": memecoin_trade_count,
        "exit_reviews": memecoin_exit_review_count,
        "exit_snapshots": memecoin_exit_snapshot_count,
        "next_rep_goal": next_rep_goal,
    }


def _paper_signal_learning_results(conn: sqlite3.Connection, limit: int = 10) -> dict:
    if not _table_exists(conn, "paper_signal_positions"):
        return {
            "status": "NO_DATA",
            "headline": "Paper learning has not opened any simulated entries yet.",
            "summary": {
                "total": 0,
                "open": 0,
                "closed": 0,
                "win_rate_pct": None,
                "avg_closed_return_pct": None,
                "avg_open_return_pct": None,
                "avg_open_mfe_pct": None,
                "avg_open_mae_pct": None,
            },
            "best_open": None,
            "worst_open": None,
            "positions": [],
        }

    has_token_intel = _table_exists(conn, "token_intelligence_current")
    if has_token_intel:
        rows = conn.execute(
            """
            SELECT p.*,
                   ti.price AS current_price,
                   ti.marketcap AS current_marketcap,
                   ti.quality_score AS current_quality_score,
                   ti.risk_score AS current_risk_score,
                   ti.pressure_score AS current_pressure_score,
                   ti.updated_at AS current_updated_at,
                   ti.data_freshness AS current_data_freshness
            FROM paper_signal_positions p
            LEFT JOIN token_intelligence_current ti ON ti.mint = p.mint
            ORDER BY
              CASE WHEN UPPER(p.status) = 'OPEN' THEN 0 ELSE 1 END,
              COALESCE(p.last_review_ts_utc, p.closed_ts_utc, p.opened_ts_utc) DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT p.*,
                   NULL AS current_price,
                   NULL AS current_marketcap,
                   NULL AS current_quality_score,
                   NULL AS current_risk_score,
                   NULL AS current_pressure_score,
                   NULL AS current_updated_at,
                   NULL AS current_data_freshness
            FROM paper_signal_positions p
            ORDER BY
              CASE WHEN UPPER(p.status) = 'OPEN' THEN 0 ELSE 1 END,
              COALESCE(p.last_review_ts_utc, p.closed_ts_utc, p.opened_ts_utc) DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    summary_row = conn.execute(
        """
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN UPPER(status) = 'OPEN' THEN 1 ELSE 0 END) AS open_count,
               SUM(CASE WHEN UPPER(status) != 'OPEN' THEN 1 ELSE 0 END) AS closed_count,
               SUM(CASE WHEN UPPER(status) != 'OPEN' AND COALESCE(pnl_pct, 0) > 0 THEN 1 ELSE 0 END) AS wins,
               AVG(CASE WHEN UPPER(status) != 'OPEN' THEN pnl_pct END) AS avg_closed_return_pct
        FROM paper_signal_positions
        """
    ).fetchone()

    def _float(value: object) -> float | None:
        try:
            if value is None:
                return None
            out = float(value)
            return out if out == out else None
        except Exception:
            return None

    def _return_pct(entry: float | None, current: float | None, fallback: float | None) -> float | None:
        if entry and current and entry > 0:
            return ((current - entry) / entry) * 100.0
        return fallback

    def _age_hours(ts_text: object) -> float | None:
        minutes = _age_minutes(ts_text)
        return round(minutes / 60.0, 1) if minutes is not None else None

    def _lesson(status: str, ret: float | None, mfe: float | None, mae: float | None, age_hours: float | None) -> str:
        status_u = status.upper()
        ret_v = float(ret or 0.0)
        mfe_v = float(mfe or 0.0)
        mae_v = float(mae or 0.0)
        age_v = float(age_hours or 0.0)
        if status_u != "OPEN":
            if ret is None:
                return "Closed without enough pricing to score the entry yet."
            if ret_v > 0:
                return "Closed winner; this setup should increase trust once the sample is large enough."
            return "Closed loser; compare the entry snapshot with winners before copying this setup."
        if ret is None:
            return "Tracking entry, but current pricing is not fresh enough to judge yet."
        if mfe_v >= 20 and ret_v < mfe_v * 0.45:
            return "Had a strong paper move but gave much of it back; exit timing needs attention."
        if ret_v >= 15:
            return "Entry is working; this is the kind of setup to study for earlier real entries."
        if ret_v <= -10 or mae_v <= -10:
            return "Entry is warning early; similar setups may need stricter confirmation or smaller sizing."
        if age_v >= 24 and abs(ret_v) < 5:
            return "Stale open paper idea; momentum may not be strong enough to deserve capital."
        return "Still forming; keep watching volume, pressure, and follow-through before learning from it."

    positions: list[dict] = []
    open_returns: list[float] = []
    open_mfes: list[float] = []
    open_maes: list[float] = []

    for row in rows:
        item = dict(row)
        status = str(item.get("status") or "UNKNOWN").upper()
        entry_price = _float(item.get("entry_price"))
        fallback_price = _float(item.get("exit_price")) if status != "OPEN" else None
        current_price = _float(item.get("current_price")) or fallback_price
        if status == "OPEN" and current_price is None:
            current_price = entry_price
        current_mcap = _float(item.get("current_marketcap"))
        if current_mcap is None:
            current_mcap = _float(item.get("exit_marketcap") if status != "OPEN" else item.get("entry_marketcap"))
        pnl_pct = _return_pct(entry_price, current_price, _float(item.get("pnl_pct")))
        mfe = _float(item.get("max_favorable_excursion_pct")) or 0.0
        mae = _float(item.get("max_adverse_excursion_pct")) or 0.0
        age_hours = _age_hours(item.get("opened_ts_utc"))

        if status == "OPEN" and pnl_pct is not None:
            open_returns.append(pnl_pct)
            open_mfes.append(mfe)
            open_maes.append(mae)

        positions.append(
            {
                "id": item.get("id"),
                "symbol": str(item.get("symbol") or ""),
                "mint": str(item.get("mint") or ""),
                "status": status,
                "opened_ts_utc": _format_ts(item.get("opened_ts_utc")),
                "closed_ts_utc": _format_ts(item.get("closed_ts_utc")),
                "last_review_ts_utc": _format_ts(item.get("last_review_ts_utc")),
                "age_hours": age_hours,
                "entry_price": entry_price,
                "current_price": current_price,
                "entry_marketcap": _float(item.get("entry_marketcap")),
                "current_marketcap": current_mcap,
                "entry_score": _float(item.get("entry_score")),
                "current_return_pct": round(pnl_pct, 2) if pnl_pct is not None else None,
                "max_favorable_excursion_pct": round(mfe, 2),
                "max_adverse_excursion_pct": round(mae, 2),
                "exit_reason": item.get("exit_reason"),
                "lesson": _lesson(status, pnl_pct, mfe, mae, age_hours),
                "pressure_score": _float(item.get("current_pressure_score")) or _float(item.get("entry_pressure_score")),
                "quality_score": _float(item.get("current_quality_score")) or _float(item.get("entry_quality_score")),
                "risk_score": _float(item.get("current_risk_score")) or _float(item.get("entry_risk_score")),
                "current_updated_at": _format_ts(item.get("current_updated_at")),
                "current_data_freshness": item.get("current_data_freshness"),
            }
        )

    total = int((summary_row["total"] if summary_row else 0) or 0)
    open_count = int((summary_row["open_count"] if summary_row else 0) or 0)
    closed_count = int((summary_row["closed_count"] if summary_row else 0) or 0)
    wins = int((summary_row["wins"] if summary_row else 0) or 0)
    win_rate = (wins / closed_count) * 100.0 if closed_count else None
    avg_closed = _float(summary_row["avg_closed_return_pct"] if summary_row else None)
    avg_open = sum(open_returns) / len(open_returns) if open_returns else None
    avg_mfe = sum(open_mfes) / len(open_mfes) if open_mfes else None
    avg_mae = sum(open_maes) / len(open_maes) if open_maes else None

    open_positions = [p for p in positions if p.get("status") == "OPEN" and p.get("current_return_pct") is not None]
    best_open = max(open_positions, key=lambda p: float(p.get("current_return_pct") or 0.0), default=None)
    worst_open = min(open_positions, key=lambda p: float(p.get("current_return_pct") or 0.0), default=None)

    if closed_count >= 10:
        status = "ACTIONABLE_SAMPLE"
        headline = "Paper learning has enough closes to start comparing setup families."
    elif open_count > 0:
        status = "TRACKING_OPEN"
        headline = f"Paper learning is tracking {open_count} simulated entries; wait for closes before tuning rules."
    elif total > 0:
        status = "LEARNING"
        headline = "Paper learning has positions, but needs fresh open entries or more closed examples."
    else:
        status = "NO_DATA"
        headline = "Paper learning has not opened any simulated entries yet."

    return {
        "status": status,
        "headline": headline,
        "summary": {
            "total": total,
            "open": open_count,
            "closed": closed_count,
            "win_rate_pct": round(win_rate, 1) if win_rate is not None else None,
            "avg_closed_return_pct": round(avg_closed, 2) if avg_closed is not None else None,
            "avg_open_return_pct": round(avg_open, 2) if avg_open is not None else None,
            "avg_open_mfe_pct": round(avg_mfe, 2) if avg_mfe is not None else None,
            "avg_open_mae_pct": round(avg_mae, 2) if avg_mae is not None else None,
        },
        "best_open": best_open,
        "worst_open": worst_open,
        "positions": positions,
    }


def _memecoin_proof_expansion_layer(expansion: dict) -> dict:
    slot_policy = dict(expansion.get("slot_policy") or {})
    next_slot_candidates = list(expansion.get("next_slot_candidates") or [])
    identity_watch = dict(expansion.get("identity_watch") or {})
    pipeline_health = dict(expansion.get("pipeline_health") or {})
    proof_input_source = str(pipeline_health.get("proof_input_source") or "UNKNOWN").strip().upper() or "UNKNOWN"

    top_candidates = []
    for item in next_slot_candidates[:3]:
        top_candidates.append(
            {
                "symbol": str(item.get("symbol") or ""),
                "stage": str(item.get("stage") or ""),
                "cohort_bucket": str(item.get("cohort_bucket") or ""),
                "slot_eligible_now": bool(item.get("slot_eligible_now")),
                "slot_blockers": list(item.get("slot_blockers") or [])[:3],
                "proof_score": item.get("proof_score"),
            }
        )

    return {
        "planner_state": str(slot_policy.get("planner_state") or "UNKNOWN"),
        "planner_note": str(slot_policy.get("planner_note") or ""),
        "available_slots_now": int(slot_policy.get("available_slots_now") or 0),
        "recommended_openings": int(slot_policy.get("recommended_openings") or 0),
        "deployment_authority": str(slot_policy.get("deployment_authority") or ""),
        "graduation_state": str(slot_policy.get("graduation_state") or ""),
        "identity_conflicts": int(identity_watch.get("count") or 0),
        "proof_input_source": proof_input_source,
        "fallback_mode_active": proof_input_source != "LIVE_CACHE",
        "fallback_note": (
            f"Proof expansion is currently reading from {proof_input_source.lower().replace('_', ' ')}."
            if proof_input_source != "LIVE_CACHE"
            else "Proof expansion is reading from the live cache."
        ),
        "top_candidates": top_candidates,
    }


def _spot_rotation_layer(spot_state: dict, spot_posture: dict) -> dict:
    holdings = [dict(item) for item in list(spot_state.get("holdings") or []) if float((item or {}).get("token_amount") or 0.0) > 0]
    posture_rows = {str(item.get("symbol") or ""): dict(item) for item in list(spot_posture.get("rows") or [])}
    actionable_symbols = list(spot_posture.get("actionable") or [])

    incoming = []
    for sym in actionable_symbols:
        row = posture_rows.get(sym) or {}
        holding = next((h for h in holdings if str(h.get("symbol") or "") == sym), None)
        incoming.append(
            {
                "symbol": sym,
                "posture": str(row.get("posture") or ""),
                "signal_type": str(row.get("signal_type") or ""),
                "score": row.get("score"),
                "gap": row.get("gap"),
                "already_held": holding is not None,
                "trend": holding.get("trend") if holding else None,
            }
        )

    def _out_rank(item: dict) -> tuple:
        posture = str((posture_rows.get(str(item.get("symbol") or "")) or {}).get("posture") or "HOLD")
        posture_rank = {
            "POOR_CONDITIONS": 0,
            "HOLD": 1,
            "INSUFFICIENT_DATA": 2,
            "ACCUMULATE": 3,
            "PRIME_ENTRY": 4,
        }.get(posture, 5)
        trend = str(item.get("trend") or "")
        trend_rank = {"FALLING": 0, "WEAK": 1, "RISING": 3, "STRONG": 4}.get(trend, 2)
        pnl = float(item.get("pnl_pct") or 0.0)
        return (posture_rank, trend_rank, pnl)

    weakest = sorted(holdings, key=_out_rank)[:3]
    weakest_out = [
        {
            "symbol": str(item.get("symbol") or ""),
            "posture": str((posture_rows.get(str(item.get("symbol") or "")) or {}).get("posture") or "HOLD"),
            "pnl_pct": item.get("pnl_pct"),
            "trend": str(item.get("trend") or ""),
            "current_pct": item.get("current_pct"),
        }
        for item in weakest
    ]

    if incoming and any(not row.get("already_held") for row in incoming):
        state = "ROTATION_CANDIDATE"
        headline = "Spot has at least one incoming candidate that could justify a rotation if the weakest holding stays soft."
    elif incoming:
        state = "ADD_INSIDE_BASKET"
        headline = "Spot quality is improving on names we already hold, but there is no clean rotate-in candidate yet."
    else:
        state = "NO_ROTATION"
        headline = "Spot does not have a clear rotate-in candidate right now."

    return {
        "state": state,
        "headline": headline,
        "incoming_candidates": incoming[:3],
        "weakest_holdings": weakest_out,
    }


def get_system_audit_data() -> dict:
    _ensure_engine_path()
    from routers.confluence import get_confluence_summary_data  # type: ignore
    from routers.home import _build_best_action, _build_home_summary_payload, _build_priority_summary_payload, _posture_spot  # type: ignore
    from routers.memecoins import get_memecoin_graduation_data, get_memecoin_proof_expansion_data, get_proof_stack_summary_data, get_memecoin_v3_lane_state  # type: ignore
    from routers.whale_watch import get_whale_summary_data  # type: ignore
    from utils.spot_accumulator import get_portfolio_state  # type: ignore
    from utils.db import get_recent_allocation_recommendations, get_recent_watchdog_events  # type: ignore

    posture_payload = _build_priority_summary_payload()
    home_summary = _build_home_summary_payload()
    best_action = _build_best_action()
    whale_summary = get_whale_summary_data()
    confluence_summary = get_confluence_summary_data()
    proof_stack = get_proof_stack_summary_data(limit=10)
    memecoin_lane_state = get_memecoin_v3_lane_state(limit=10, lookback_days=30)
    graduation = get_memecoin_graduation_data(limit=10, lookback_days=30)
    proof_expansion = get_memecoin_proof_expansion_data(limit=8, lookback_days=30)
    if not dict(proof_expansion or {}).get("pipeline_health"):
        proof_expansion = {
            **dict(proof_expansion or {}),
            "pipeline_health": {
                "proof_input_source": str(((memecoin_lane_state.get("system_health") or {}).get("proof_input_source")) or "UNKNOWN"),
            },
        }
    spot_posture = _posture_spot()
    spot_state = get_portfolio_state() or {}
    live_provider_budget_allocator = {}
    try:
        from utils.provider_budget import refresh_provider_budget_allocator_status  # type: ignore

        live_provider_budget_allocator = refresh_provider_budget_allocator_status()
    except Exception:
        live_provider_budget_allocator = {}

    with sqlite3.connect(_db_path(), timeout=20) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=20000")
        recent_decisions = _recent_decisions(conn, limit=20)
        recent_executions = _recent_executions(conn, limit_each=10)
        anomalies = _active_anomalies(conn, whale_summary, confluence_summary)
        perp_patterns = _perp_pattern_audit(conn)
        manual_perp_journal = _manual_perp_journal_summary(conn)
        sol_perp_bias = _sol_perp_bias_snapshot(perp_patterns, manual_perp_journal, best_action)
        wd_row = conn.execute(
            "SELECT value FROM kv_store WHERE key='pipeline_watchdog_status'"
        ).fetchone()
        pipeline_watchdog = _json_loads(wd_row[0] if wd_row else None) or {}
        alloc_row = conn.execute(
            "SELECT value FROM kv_store WHERE key='allocator_stream_status'"
        ).fetchone()
        allocator_watchdog = _json_loads(alloc_row[0] if alloc_row else None) or {}
        authority_row = conn.execute(
            "SELECT value FROM kv_store WHERE key='authority_snapshot'"
        ).fetchone()
        authority_snapshot = _json_loads(authority_row[0] if authority_row else None) or {}
        allocator_history = get_recent_allocation_recommendations(5)
        allocator_truth = _allocator_truth_snapshot(allocator_watchdog, allocator_history)
        wallet_row = conn.execute(
            "SELECT value FROM kv_store WHERE key='wallet_tx_stream_status'"
        ).fetchone()
        wallet_stream_status = _wallet_stream_truth_snapshot(
            _json_loads(wallet_row[0] if wallet_row else None) or {}
        )
        paper_learning_row = conn.execute(
            "SELECT value FROM kv_store WHERE key='paper_signal_learning_status'"
        ).fetchone()
        paper_signal_learning_status = _json_loads(paper_learning_row[0] if paper_learning_row else None) or {}
        dex_row = conn.execute(
            "SELECT value FROM kv_store WHERE key='provider_status:dexscreener'"
        ).fetchone()
        dex_provider_status = _json_loads(dex_row[0] if dex_row else None) or {}
        birdeye_provider_row = conn.execute(
            "SELECT value FROM kv_store WHERE key='provider_status:birdeye'"
        ).fetchone()
        birdeye_provider_status = _json_loads(birdeye_provider_row[0] if birdeye_provider_row else None) or {}
        spot_dex_row = conn.execute(
            "SELECT value FROM kv_store WHERE key='provider_status:dexscreener_spot'"
        ).fetchone()
        spot_dex_provider_status = _json_loads(spot_dex_row[0] if spot_dex_row else None) or {}
        gecko_row = conn.execute(
            "SELECT value FROM kv_store WHERE key='provider_status:geckoterminal'"
        ).fetchone()
        gecko_provider_status = _json_loads(gecko_row[0] if gecko_row else None) or {}
        dex_budget_row = conn.execute(
            "SELECT value FROM kv_store WHERE key='provider_budget:dexscreener'"
        ).fetchone()
        dex_provider_budget = _json_loads(dex_budget_row[0] if dex_budget_row else None) or {}
        spot_dex_budget_row = conn.execute(
            "SELECT value FROM kv_store WHERE key='provider_budget:dexscreener_spot'"
        ).fetchone()
        spot_dex_provider_budget = _json_loads(spot_dex_budget_row[0] if spot_dex_budget_row else None) or {}
        gecko_budget_row = conn.execute(
            "SELECT value FROM kv_store WHERE key='provider_budget:geckoterminal'"
        ).fetchone()
        gecko_provider_budget = _json_loads(gecko_budget_row[0] if gecko_budget_row else None) or {}
        provider_budget_allocator = {}
        try:
            from utils.provider_budget import provider_budget_snapshot  # type: ignore

            dex_provider_budget = provider_budget_snapshot("dexscreener")
            spot_dex_provider_budget = provider_budget_snapshot("dexscreener_spot")
            gecko_provider_budget = provider_budget_snapshot("geckoterminal")
            provider_budget_allocator = live_provider_budget_allocator
        except Exception:
            dex_provider_budget = dex_provider_budget or {}
            spot_dex_provider_budget = spot_dex_provider_budget or {}
            gecko_provider_budget = gecko_provider_budget or {}
            provider_budget_allocator = provider_budget_allocator or {}
        identity_guard = {"recent_conflicts": 0, "recent_resolutions": 0, "latest_conflict": None}
        if _table_exists(conn, "token_identity_resolutions"):
            id_rows = conn.execute(
                """
                SELECT ts_utc, symbol, status, reason, resolved_mint, source, confidence, candidate_count
                FROM token_identity_resolutions
                ORDER BY id DESC
                LIMIT 100
                """
            ).fetchall()
            recent = [dict(r) for r in id_rows]
            conflicts = [r for r in recent if str(r.get("status") or "").upper() == "QUARANTINED"]
            identity_guard = {
                "recent_conflicts": len(conflicts),
                "recent_resolutions": len([r for r in recent if str(r.get("status") or "").upper() == "RESOLVED"]),
                "latest_conflict": conflicts[0] if conflicts else None,
            }
        market_source_row = conn.execute(
            "SELECT value FROM kv_store WHERE key='market_data_source_status'"
        ).fetchone()
        market_data_source = _json_loads(market_source_row[0] if market_source_row else None) or {}
        birdeye_row = conn.execute(
            "SELECT value FROM kv_store WHERE key='token_stats_stream_status'"
        ).fetchone()
        birdeye_stream_status = _token_stats_truth_snapshot(
            conn,
            _json_loads(birdeye_row[0] if birdeye_row else None) or {},
        )
        token_intel_row = conn.execute(
            "SELECT value FROM kv_store WHERE key='token_intelligence_status'"
        ).fetchone()
        token_intelligence_status = _json_loads(token_intel_row[0] if token_intel_row else None) or {}
        token_live_confirmation_row = conn.execute(
            "SELECT value FROM kv_store WHERE key='token_live_confirmation_status'"
        ).fetchone()
        token_live_confirmation_status = _json_loads(token_live_confirmation_row[0] if token_live_confirmation_row else None) or {}
        watch_to_entry_row = conn.execute(
            "SELECT value FROM kv_store WHERE key='memecoin_watch_to_entry_status'"
        ).fetchone()
        watch_to_entry_status = _json_loads(watch_to_entry_row[0] if watch_to_entry_row else None) or {}
        watch_to_entry_replay = _watch_to_entry_replay_report(conn, watch_to_entry_status)
        established_runner_coverage = _established_runner_coverage(conn, watch_to_entry_status)
        token_intelligence_summary = {
            "status": str(token_intelligence_status.get("status") or "UNKNOWN").upper(),
            "checked_at": token_intelligence_status.get("checked_at"),
            "tracked_count": int(token_intelligence_status.get("tracked_count") or 0),
            "snapshot_count": int(token_intelligence_status.get("snapshot_count") or 0),
            "live_count": int(token_intelligence_status.get("live_count") or 0),
            "high_confidence_count": int(token_intelligence_status.get("high_confidence_count") or 0),
            "identity_conflicts": int(token_intelligence_status.get("identity_conflicts") or 0),
            "detail": token_intelligence_status.get("detail"),
        }
        if _table_exists(conn, "token_intelligence_current"):
            row = conn.execute(
                """
                SELECT COUNT(*) AS total,
                       SUM(CASE WHEN identity_status IN ('QUARANTINED','MISMATCH') THEN 1 ELSE 0 END) AS conflicts,
                       SUM(CASE WHEN data_freshness='LIVE' THEN 1 ELSE 0 END) AS live,
                       MAX(updated_at) AS latest
                FROM token_intelligence_current
                """
            ).fetchone()
            agg = dict(row) if row else {}
            token_intelligence_summary.update(
                {
                    "current_count": int(agg.get("total") or 0),
                    "current_conflicts": int(agg.get("conflicts") or 0),
                    "current_live": int(agg.get("live") or 0),
                    "latest_snapshot_at": agg.get("latest"),
                }
            )
        memecoin_scan_cache = _memecoin_scan_cache_truth(conn)
        memecoin_live_buy_gate = _memecoin_live_buy_gate(conn)
        authority_unlock = _entry_unlock_summary(
            posture_payload,
            authority_snapshot,
            memecoin_live_buy_gate,
        )
        memecoin_trade_count = int((conn.execute("SELECT COUNT(*) FROM memecoin_trades").fetchone() or [0])[0]) if _table_exists(conn, "memecoin_trades") else 0
        memecoin_exit_review_count = int((conn.execute("SELECT COUNT(*) FROM memecoin_exit_reviews").fetchone() or [0])[0]) if _table_exists(conn, "memecoin_exit_reviews") else 0
        memecoin_exit_snapshot_count = int((conn.execute("SELECT COUNT(*) FROM memecoin_exit_signal_snapshots").fetchone() or [0])[0]) if _table_exists(conn, "memecoin_exit_signal_snapshots") else 0
        paper_signal_learning_results = _paper_signal_learning_results(conn)

    posture = {
        "operating_mode_summary": posture_payload.get("operating_mode_summary"),
        "lane_policy_summary": posture_payload.get("lane_policy_summary"),
        "lane_authority_summary": posture_payload.get("lane_authority_summary"),
        "lane_unlock_summary": posture_payload.get("lane_unlock_summary"),
        "action_law_summary": posture_payload.get("action_law_summary"),
        "operating_recommendation": posture_payload.get("operating_recommendation"),
        "tiers": (home_summary or {}).get("tiers"),
        "memecoins": (home_summary or {}).get("memecoins"),
        "spot": (home_summary or {}).get("spot"),
        "whale_watch": {
            "ingest_status": whale_summary.get("ingest_status"),
            "ingest_detail": whale_summary.get("ingest_detail"),
            "total": whale_summary.get("total"),
            "in_range": whale_summary.get("in_range"),
            "scanner_pass": whale_summary.get("scanner_pass"),
            "pipeline_diagnostics": whale_summary.get("pipeline_diagnostics"),
        },
        "confluence": {
            "ingest_status": confluence_summary.get("ingest_status"),
            "ingest_detail": confluence_summary.get("ingest_detail"),
            "total": confluence_summary.get("total"),
            "whale_input_ready": confluence_summary.get("whale_input_ready"),
            "memecoin_input_ready": confluence_summary.get("memecoin_input_ready"),
            "exact_mint_overlap_ready": confluence_summary.get("exact_mint_overlap_ready"),
            "pre_confluence": confluence_summary.get("pre_confluence"),
        },
        "proof_stack": {
            "mode": proof_stack.get("mode"),
            "proof_ready_now": proof_stack.get("proof_ready_now"),
            "proof_candidates_surfaced": proof_stack.get("proof_candidates_surfaced"),
            "proof_build_trades": proof_stack.get("proof_build_trades"),
            "proof_build_outcomes": proof_stack.get("proof_build_outcomes"),
            "current_blockers": proof_stack.get("current_blockers"),
            "proof_input_source": ((memecoin_lane_state.get("system_health") or {}).get("proof_input_source")),
            "pipeline_status": ((memecoin_lane_state.get("system_health") or {}).get("pipeline_status")),
            "pipeline_detail": ((memecoin_lane_state.get("system_health") or {}).get("pipeline_detail")),
        },
    }
    focus_build = _focus_build_summary(posture)
    memecoin_graduation = _memecoin_graduation_tracker(graduation, proof_stack)
    spot_decision = _spot_decision_layer(dict((home_summary or {}).get("spot") or {}), spot_posture, dict(posture_payload.get("lane_unlock_summary") or {}))
    memecoin_reinforcement = _memecoin_reinforcement_memory(graduation)
    memecoin_rep_depth = _memecoin_rep_depth(
        proof_stack=proof_stack,
        memecoin_trade_count=memecoin_trade_count,
        memecoin_exit_review_count=memecoin_exit_review_count,
        memecoin_exit_snapshot_count=memecoin_exit_snapshot_count,
    )
    memecoin_proof_expansion = _memecoin_proof_expansion_layer(proof_expansion)
    spot_rotation = _spot_rotation_layer(spot_state, spot_posture)

    runtime = {
        "wallet_stream": wallet_stream_status,
        "provider_status": {
            "birdeye": birdeye_provider_status,
            "dexscreener": dex_provider_status,
            "dexscreener_spot": spot_dex_provider_status,
            "geckoterminal": gecko_provider_status,
        },
        "provider_budget": {
            "dexscreener": dex_provider_budget,
            "dexscreener_spot": spot_dex_provider_budget,
            "geckoterminal": gecko_provider_budget,
            "allocator": provider_budget_allocator,
        },
        "identity_guard": identity_guard,
        "market_data_source": market_data_source,
        "fallbacks": {
            "birdeye_token_stats": birdeye_stream_status,
            "token_intelligence": token_intelligence_summary,
            "token_live_confirmation": token_live_confirmation_status,
            "paper_signal_learning": paper_signal_learning_status,
        },
        "memecoin_input": {
            "proof_input_source": str(((memecoin_lane_state.get("system_health") or {}).get("proof_input_source")) or "UNKNOWN"),
            "pipeline_status": str(((memecoin_lane_state.get("system_health") or {}).get("pipeline_status")) or "UNKNOWN"),
            "pipeline_detail": str(((memecoin_lane_state.get("system_health") or {}).get("pipeline_detail")) or ""),
            "using_fallback": str(((memecoin_lane_state.get("system_health") or {}).get("proof_input_source")) or "UNKNOWN").strip().upper() != "LIVE_CACHE",
            "scan_cache": memecoin_scan_cache,
            "live_buy_gate": memecoin_live_buy_gate,
            "watch_to_entry": watch_to_entry_status,
            "watch_to_entry_replay": watch_to_entry_replay,
            "established_runner_coverage": established_runner_coverage,
        },
    }
    independent_mode = _independent_source_mode()
    runtime["source_strategy"] = {
        "independent_mode": independent_mode,
        "birdeye_required": False,
        "birdseye_required": False,
        "premium_sources_disabled": ["birdeye"] if independent_mode else [],
        "primary_market_source": market_data_source.get("primary"),
        "market_status": market_data_source.get("status"),
        "detail": (
            "BirdEye is disabled intentionally; GeckoTerminal/DexScreener drive discovery and proof remains conservative."
            if independent_mode
            else "BirdEye remains optional with GeckoTerminal/DexScreener fallback."
        ),
    }
    runtime["data_confidence"] = _runtime_data_confidence(runtime)
    runtime["provider_recovery"] = _runtime_provider_recovery(runtime)
    runtime["recovery_checklist"] = _runtime_recovery_checklist(runtime)
    runtime["decision_mode"] = _runtime_decision_mode(runtime)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "runtime": runtime,
        "authority_unlock": authority_unlock,
        "perp_patterns": perp_patterns,
        "manual_perp_journal": manual_perp_journal,
        "sol_perp_bias": sol_perp_bias,
        "posture": posture,
        "focus_build": focus_build,
        "memecoin_graduation": memecoin_graduation,
        "spot_decision": spot_decision,
        "paper_signal_learning": paper_signal_learning_results,
        "memecoin_reinforcement": memecoin_reinforcement,
        "memecoin_rep_depth": memecoin_rep_depth,
        "memecoin_proof_expansion": memecoin_proof_expansion,
        "watch_to_entry": watch_to_entry_status,
        "watch_to_entry_replay": watch_to_entry_replay,
        "established_runner_coverage": established_runner_coverage,
        "spot_rotation": spot_rotation,
        "watchdogs": {
            "pipeline": pipeline_watchdog,
            "events": get_recent_watchdog_events(watchdog="PIPELINE_EVAL", limit=10),
            "allocator": allocator_truth,
            "allocator_history": allocator_history,
        },
        "blockers": _normalize_blockers(
            posture_payload=posture_payload,
            proof_stack=proof_stack,
            whale_summary=whale_summary,
            confluence_summary=confluence_summary,
            recent_decisions=recent_decisions,
        ),
        "recent_decisions": recent_decisions,
        "recent_executions": recent_executions,
        "anomalies": anomalies,
    }


def _build_system_audit_fast_payload(base_payload: dict | None = None) -> dict:
    """Cheap audit overlay that keeps runtime truth fresh without rebuilding every deep section."""
    base = copy.deepcopy(base_payload) if isinstance(base_payload, dict) else {}
    now_iso = datetime.now(timezone.utc).isoformat()
    runtime = dict(base.get("runtime") or {})

    try:
        with sqlite3.connect(_db_path(), timeout=5) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout=5000")

            def _kv(key: str) -> dict:
                row = conn.execute("SELECT value FROM kv_store WHERE key=?", (key,)).fetchone()
                return _json_loads(row[0] if row else None) or {}

            wallet_stream_status = _wallet_stream_truth_snapshot(_kv("wallet_tx_stream_status"))
            dex_provider_status = _kv("provider_status:dexscreener")
            birdeye_provider_status = _kv("provider_status:birdeye")
            spot_dex_provider_status = _kv("provider_status:dexscreener_spot")
            gecko_provider_status = _kv("provider_status:geckoterminal")
            market_data_source = _kv("market_data_source_status")
            paper_signal_learning_status = _kv("paper_signal_learning_status")
            token_live_confirmation_status = _kv("token_live_confirmation_status")
            token_intelligence_status = _kv("token_intelligence_status")
            birdeye_stream_status = _token_stats_truth_snapshot(conn, _kv("token_stats_stream_status"))

            provider_budget = dict(runtime.get("provider_budget") or {})
            for provider in ("dexscreener", "dexscreener_spot", "geckoterminal"):
                try:
                    from utils.provider_budget import provider_budget_snapshot  # type: ignore
                    provider_budget[provider] = provider_budget_snapshot(provider)
                except Exception:
                    provider_budget[provider] = _kv(f"provider_budget:{provider}") or provider_budget.get(provider) or {}

            token_intelligence_summary = {
                "status": str(token_intelligence_status.get("status") or "UNKNOWN").upper(),
                "checked_at": token_intelligence_status.get("checked_at"),
                "tracked_count": int(token_intelligence_status.get("tracked_count") or 0),
                "snapshot_count": int(token_intelligence_status.get("snapshot_count") or 0),
                "live_count": int(token_intelligence_status.get("live_count") or 0),
                "high_confidence_count": int(token_intelligence_status.get("high_confidence_count") or 0),
                "identity_conflicts": int(token_intelligence_status.get("identity_conflicts") or 0),
                "detail": token_intelligence_status.get("detail"),
            }
            if _table_exists(conn, "token_intelligence_current"):
                row = conn.execute(
                    """
                    SELECT COUNT(*) AS total,
                           SUM(CASE WHEN identity_status IN ('QUARANTINED','MISMATCH') THEN 1 ELSE 0 END) AS conflicts,
                           SUM(CASE WHEN data_freshness='LIVE' THEN 1 ELSE 0 END) AS live,
                           MAX(updated_at) AS latest
                    FROM token_intelligence_current
                    """
                ).fetchone()
                agg = dict(row) if row else {}
                token_intelligence_summary.update({
                    "current_count": int(agg.get("total") or 0),
                    "current_conflicts": int(agg.get("conflicts") or 0),
                    "current_live": int(agg.get("live") or 0),
                    "latest_snapshot_at": agg.get("latest"),
                })

            memecoin_scan_cache = _memecoin_scan_cache_truth(conn)
            watch_to_entry_status = _kv("memecoin_watch_to_entry_status")
            runtime.update({
                "wallet_stream": wallet_stream_status,
                "provider_status": {
                    "birdeye": birdeye_provider_status,
                    "dexscreener": dex_provider_status,
                    "dexscreener_spot": spot_dex_provider_status,
                    "geckoterminal": gecko_provider_status,
                },
                "provider_budget": provider_budget,
                "market_data_source": market_data_source,
                "fallbacks": {
                    **dict(runtime.get("fallbacks") or {}),
                    "birdeye_token_stats": birdeye_stream_status,
                    "token_intelligence": token_intelligence_summary,
                    "token_live_confirmation": token_live_confirmation_status,
                    "paper_signal_learning": paper_signal_learning_status,
                },
                "memecoin_input": {
                    **dict(runtime.get("memecoin_input") or {}),
                    "scan_cache": memecoin_scan_cache,
                    "watch_to_entry": watch_to_entry_status,
                },
            })
            independent_mode = _independent_source_mode()
            runtime["source_strategy"] = {
                **dict(runtime.get("source_strategy") or {}),
                "independent_mode": independent_mode,
                "birdeye_required": False,
                "birdseye_required": False,
                "premium_sources_disabled": ["birdeye"] if independent_mode else [],
                "primary_market_source": market_data_source.get("primary"),
                "market_status": market_data_source.get("status"),
            }
            runtime["data_confidence"] = _runtime_data_confidence(runtime)
            runtime["provider_recovery"] = _runtime_provider_recovery(runtime)
            runtime["recovery_checklist"] = _runtime_recovery_checklist(runtime)
            runtime["decision_mode"] = _runtime_decision_mode(runtime)
    except Exception as exc:
        runtime = {
            **runtime,
            "fast_overlay_error": str(exc),
        }

    return {
        **base,
        "generated_at": now_iso,
        "refresh_mode": "SYSTEM_AUDIT_FAST_OVERLAY",
        "runtime": runtime,
        "audit_health": {
            "mode": "FAST_OVERLAY",
            "detail": "Runtime/provider freshness was refreshed without rebuilding every deep audit section.",
            "base_generated_at": base.get("generated_at"),
            "generated_at": now_iso,
        },
    }


def _build_system_audit_warming_payload(*, detail: str | None = None) -> dict:
    now_iso = datetime.now(timezone.utc).isoformat()
    return {
        "generated_at": now_iso,
        "refresh_mode": "SYSTEM_AUDIT_WARMING",
        "runtime": {},
        "audit_health": {
            "mode": "WARMING",
            "detail": detail or "System audit snapshot is warming. Keep using Home for current action state.",
            "generated_at": now_iso,
        },
        "top_findings": [],
        "recommendations": [],
    }


def _system_audit_stale_payload(base_payload: dict | None, *, detail: str | None = None) -> dict:
    payload = copy.deepcopy(base_payload) if isinstance(base_payload, dict) else _build_system_audit_warming_payload()
    payload["refresh_mode"] = "SYSTEM_AUDIT_STALE_SNAPSHOT"
    payload["audit_health"] = {
        **dict(payload.get("audit_health") or {}),
        "mode": "STALE_SNAPSHOT",
        "detail": detail or "Fresh audit overlay could not be rebuilt immediately; serving the last known system audit snapshot.",
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    return payload


def _system_audit_fast_or_stale(base_payload: dict | None, *, detail: str | None = None) -> dict:
    try:
        return _build_system_audit_fast_payload(base_payload if isinstance(base_payload, dict) else None)
    except Exception:
        return _system_audit_stale_payload(base_payload, detail=detail)


@router.get("/audit")
async def system_audit_ep(_: str = Depends(get_current_user)):
    try:
        snap = load_snapshot("system:audit")
        age = float(snap.get("_age_seconds") if snap else 10**9)
        if snap and age <= 900:
            return await snapshot_or_build(
                "system:audit",
                get_system_audit_data,
                fresh_s=900,
                stale_s=1800,
                wait_timeout_s=12,
            )
        if snap and snap.get("data") not in (None, {}, []):
            schedule_refresh("system:audit", get_system_audit_data, min_interval_s=1)
            return _system_audit_fast_or_stale(snap.get("data"))
        schedule_refresh("system:audit", get_system_audit_data, min_interval_s=1)
        return _system_audit_fast_or_stale(None)
    except Exception as exc:
        try:
            snap = load_snapshot("system:audit")
            if not snap:
                return _system_audit_fast_or_stale(None, detail=str(exc))
            schedule_refresh("system:audit", get_system_audit_data, min_interval_s=1)
            return _system_audit_fast_or_stale(snap.get("data"), detail=str(exc))
        except Exception:
            log.warning("system audit error: %s", exc)
            raise HTTPException(status_code=504, detail=str(exc))
