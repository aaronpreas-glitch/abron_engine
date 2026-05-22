from __future__ import annotations

import json
import os
from collections import Counter
from datetime import datetime, timezone
from typing import Any


RADAR_REFRESH_SECONDS = max(45, int(os.getenv("EARLY_RUNNER_REFRESH_SECONDS", "75")))
RADAR_REFRESH_WINDOW_MINUTES = max(5, int(os.getenv("EARLY_RUNNER_REFRESH_WINDOW_MINUTES", "15")))
EARLY_RUNNER_ALERTS_ENABLED = str(os.getenv("EARLY_RUNNER_ALERTS_ENABLED", "true")).strip().lower() in {"1", "true", "yes", "on"}
EARLY_RUNNER_ALERT_COOLDOWN_SECONDS = max(300, int(os.getenv("EARLY_RUNNER_ALERT_COOLDOWN_SECONDS", "1800")))


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _f(value: object, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def _parse_ts(ts_raw: object) -> datetime | None:
    raw = str(ts_raw or "").strip()
    if not raw:
        return None
    try:
        ts = datetime.fromisoformat(raw.replace("Z", "+00:00").replace(" ", "T"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts.astimezone(timezone.utc)
    except Exception:
        return None


def _age_minutes(ts_raw: object) -> float | None:
    ts = _parse_ts(ts_raw)
    if ts is None:
        return None
    return max(0.0, (_now() - ts).total_seconds() / 60.0)


def _ensure_tables(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS memecoin_runner_radar_observations (
            mint TEXT PRIMARY KEY,
            symbol TEXT,
            first_seen_utc TEXT NOT NULL,
            last_seen_utc TEXT NOT NULL,
            last_refresh_utc TEXT,
            source TEXT,
            status TEXT,
            radar_score REAL,
            scanner_score REAL,
            escalation_state TEXT,
            verdict TEXT,
            action_guidance TEXT,
            verdict_reasons_json TEXT,
            alerted_at TEXT,
            execution_state TEXT,
            risk_flags_json TEXT,
            why_json TEXT,
            initial_mcap_usd REAL,
            latest_mcap_usd REAL,
            initial_price REAL,
            latest_price REAL,
            initial_liquidity_usd REAL,
            latest_liquidity_usd REAL,
            initial_volume_24h REAL,
            latest_volume_24h REAL,
            initial_change_1h REAL,
            latest_change_1h REAL,
            initial_change_24h REAL,
            latest_change_24h REAL,
            vol_liq_ratio REAL,
            replay_state TEXT DEFAULT 'TRACKING',
            max_return_pct REAL,
            return_1h_pct REAL,
            return_4h_pct REAL,
            return_24h_pct REAL,
            missed_reason TEXT,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_runner_radar_seen
        ON memecoin_runner_radar_observations(last_seen_utc DESC)
        """
    )
    existing = {
        str(row[1])
        for row in conn.execute("PRAGMA table_info(memecoin_runner_radar_observations)").fetchall()
    }
    migrations = {
        "verdict": "ALTER TABLE memecoin_runner_radar_observations ADD COLUMN verdict TEXT",
        "action_guidance": "ALTER TABLE memecoin_runner_radar_observations ADD COLUMN action_guidance TEXT",
        "verdict_reasons_json": "ALTER TABLE memecoin_runner_radar_observations ADD COLUMN verdict_reasons_json TEXT",
        "alerted_at": "ALTER TABLE memecoin_runner_radar_observations ADD COLUMN alerted_at TEXT",
    }
    for column, statement in migrations.items():
        if column not in existing:
            conn.execute(statement)


def _risk_flags(rec: dict[str, Any]) -> list[str]:
    trust = str(rec.get("trust_label") or "").upper()
    triage = str(rec.get("triage_state") or "").upper()
    context = str(rec.get("entry_context") or "UNKNOWN").upper()
    flags: list[str] = []
    if trust and trust not in {"", "TRUSTED", "OK"}:
        flags.append(trust)
    if triage and triage not in {"", "MONITOR"}:
        flags.append(triage)
    if context in {"EXTENDED", "DISTRIBUTION_RISK", "DEAD_OR_CHOP"}:
        flags.append(context)
    return flags[:5]


def _score(rec: dict[str, Any]) -> tuple[float, list[str]]:
    scanner_score = _f(rec.get("score"))
    chg1h = _f(rec.get("change_1h_at_scan"))
    chg24h = _f(rec.get("change_24h_at_scan"))
    liq = _f(rec.get("liquidity_usd"))
    vol = _f(rec.get("volume_24h"))
    mcap = _f(rec.get("mcap_at_scan"))
    vol_liq = vol / liq if liq > 0 else 0.0
    age_m = _age_minutes(rec.get("scanned_at"))
    freshness_bonus = 12.0 if age_m is not None and age_m <= 30 else 7.0 if age_m is not None and age_m <= 90 else 2.0
    mcap_bonus = 14.0 if mcap < 2_000_000 else 10.0 if mcap < 8_000_000 else 5.0
    velocity = min(max(chg1h, 0.0) * 1.1, 24.0) + min(max(chg24h, 0.0) * 0.18, 22.0) + min(vol_liq * 4.0, 22.0)
    radar_score = round(max(0.0, min(100.0, (scanner_score * 0.36) + velocity + freshness_bonus + mcap_bonus)), 1)

    why: list[str] = []
    if age_m is not None and age_m <= 90:
        why.append("fresh scan")
    if mcap and mcap < 3_000_000:
        why.append("early market cap")
    if chg1h >= 5:
        why.append("1h velocity")
    if chg24h >= 25:
        why.append("24h breakout")
    if vol_liq >= 3:
        why.append("volume/liquidity explosion")
    if scanner_score >= 75:
        why.append("high scanner score")
    return radar_score, why[:6]


def _decision(rec: dict[str, Any], radar_score: float, flags: list[str], why: list[str]) -> dict[str, Any]:
    scanner_score = _f(rec.get("score"))
    chg1h = _f(rec.get("change_1h_at_scan"))
    chg24h = _f(rec.get("change_24h_at_scan"))
    liq = _f(rec.get("liquidity_usd"))
    vol = _f(rec.get("volume_24h"))
    mcap = _f(rec.get("mcap_at_scan"))
    vol_liq = vol / liq if liq > 0 else 0.0
    context = str(rec.get("entry_context") or "UNKNOWN").upper()
    triage = str(rec.get("triage_state") or "").upper()
    flag_set = {str(flag).upper() for flag in flags}
    hard_red = flag_set.intersection({"DISTRUST", "DO_NOT_TOUCH", "DEAD_OR_CHOP", "DISTRIBUTION_RISK"})
    blocked = "BLOCKED" in flag_set or triage == "BLOCKED"
    low_trust_only = bool(flag_set) and flag_set.issubset({"LOW_TRUST", "UNKNOWN_TRUST"})
    velocity_strong = (chg1h >= 5 or chg24h >= 30) and vol_liq >= 2.5 and liq >= 50_000
    liquidity_ok = liq >= 100_000
    early_enough = 0 < mcap <= 20_000_000
    stretched = context == "EXTENDED" or chg1h >= 28 or chg24h >= 140
    forgivable_blocker = low_trust_only and velocity_strong and early_enough

    reasons = list(why[:4])
    if forgivable_blocker:
        reasons.append("low-trust blocker forgiven by velocity")
    if blocked and not forgivable_blocker:
        reasons.append("blocked by current execution policy")
    if hard_red:
        reasons.append("hard red flag active")
    if stretched:
        reasons.append("stretched move")

    if hard_red or (blocked and not forgivable_blocker and radar_score < 88):
        state = "MONITOR_NOW" if radar_score >= 72 else "FAST_WATCH" if radar_score >= 62 else "OBSERVE"
        return {
            "escalation_state": state,
            "verdict": "DO_NOT_TOUCH",
            "action_guidance": "Skip for now; only reconsider if red flags clear and the token reappears with clean confirmation.",
            "verdict_reasons": reasons[:6],
        }

    if stretched and radar_score < 90:
        state = "MANUAL_SNIPER" if radar_score >= 82 and liquidity_ok else "MONITOR_NOW" if radar_score >= 72 else "FAST_WATCH"
        return {
            "escalation_state": state,
            "verdict": "WAIT_FOR_PULLBACK",
            "action_guidance": "Do not chase the first candle; wait for pullback, re-acceleration, or proof confirmation.",
            "verdict_reasons": reasons[:6],
        }

    if radar_score >= 88 and scanner_score >= 80 and liquidity_ok and velocity_strong and early_enough:
        return {
            "escalation_state": "PROOF_CANDIDATE",
            "verdict": "PROOF_CANDIDATE",
            "action_guidance": "Review proof stack now; this can graduate if independent confirmation holds.",
            "verdict_reasons": reasons[:6],
        }

    if radar_score >= 80 and velocity_strong:
        return {
            "escalation_state": "MANUAL_SNIPER",
            "verdict": "LOOK_NOW",
            "action_guidance": "Manual review now; verify CA, liquidity, holders, and route before any entry.",
            "verdict_reasons": reasons[:6],
        }

    if radar_score >= 72:
        return {
            "escalation_state": "MONITOR_NOW",
            "verdict": "WATCH_CLOSE",
            "action_guidance": "Keep on screen; needs one more clean confirmation before it becomes urgent.",
            "verdict_reasons": reasons[:6],
        }

    if radar_score >= 62:
        return {
            "escalation_state": "FAST_WATCH",
            "verdict": "WATCH",
            "action_guidance": "Track only; early shape is forming but not actionable yet.",
            "verdict_reasons": reasons[:6],
        }

    return {
        "escalation_state": "OBSERVE",
        "verdict": "WATCH",
        "action_guidance": "Background observe; not enough urgency for manual action.",
        "verdict_reasons": reasons[:6],
    }


def _send_runner_alert(conn, row: dict[str, Any]) -> None:
    if not EARLY_RUNNER_ALERTS_ENABLED:
        return
    state = str(row.get("escalation_state") or "").upper()
    if state not in {"MANUAL_SNIPER", "PROOF_CANDIDATE"}:
        return
    mint = str(row.get("mint") or "").strip()
    if not mint:
        return
    try:
        from utils.telegram_alerts import send_telegram_sync  # type: ignore
    except Exception:
        return
    alert_key = f"early_runner:{state}:{mint}"
    key = f"alert_ts:{alert_key}"
    try:
        import time as _time

        now_epoch = _time.time()
        rate_row = conn.execute("SELECT value FROM kv_store WHERE key=?", (key,)).fetchone()
        last_epoch = float(rate_row[0]) if rate_row and rate_row[0] else 0.0
        if now_epoch - last_epoch < EARLY_RUNNER_ALERT_COOLDOWN_SECONDS:
            return
        conn.execute("INSERT OR REPLACE INTO kv_store (key, value) VALUES (?, ?)", (key, str(now_epoch)))
    except Exception:
        return
    symbol = str(row.get("symbol") or "UNKNOWN").upper()
    flags = ", ".join(row.get("risk_flags") or []) or "none"
    reasons = ", ".join(row.get("verdict_reasons") or []) or "score/velocity"
    body = "\n".join(
        [
            f"{symbol} | {row.get('verdict')} | radar {float(row.get('radar_score') or 0):.0f}",
            f"CA: <code>{mint}</code>",
            f"1h {float(row.get('change_1h_at_scan') or 0):+.1f}% | 24h {float(row.get('change_24h_at_scan') or 0):+.1f}% | vol/liq {float(row.get('vol_liq_ratio') or 0):.1f}x",
            f"Risk: {flags}",
            f"Why: {reasons}",
            "Manual review only. No auto-entry was placed.",
        ]
    )
    if send_telegram_sync("Early Runner Decision", body, "!"):
        conn.execute(
            "UPDATE memecoin_runner_radar_observations SET alerted_at=?, updated_at=? WHERE mint=?",
            (_now().isoformat(), _now().isoformat(), mint),
        )


def _row_to_item(row: dict[str, Any]) -> dict[str, Any]:
    flags = json.loads(row.get("risk_flags_json") or "[]")
    why = json.loads(row.get("why_json") or "[]")
    verdict_reasons = json.loads(row.get("verdict_reasons_json") or "[]")
    age_m = _age_minutes(row.get("last_seen_utc"))
    last_refresh_age = _age_minutes(row.get("last_refresh_utc"))
    return {
        "symbol": str(row.get("symbol") or "UNKNOWN").upper(),
        "mint": row.get("mint"),
        "source": str(row.get("source") or "").upper(),
        "status": row.get("status"),
        "radar_score": _f(row.get("radar_score")),
        "scanner_score": _f(row.get("scanner_score")),
        "state": row.get("escalation_state") or "OBSERVE",
        "escalation_state": row.get("escalation_state") or "OBSERVE",
        "verdict": row.get("verdict") or "WATCH",
        "action_guidance": row.get("action_guidance") or "Manual review only; no automatic execution is allowed from this radar.",
        "verdict_reasons": verdict_reasons,
        "alerted_at": row.get("alerted_at"),
        "execution_state": row.get("execution_state") or "MANUAL_REVIEW_ONLY",
        "reason": (
            f"{str(row.get('escalation_state') or 'observe').lower().replace('_', ' ')}: "
            f"{_f(row.get('latest_change_1h')):+.1f}% 1h, "
            f"{_f(row.get('latest_change_24h')):+.1f}% 24h, "
            f"vol/liq {_f(row.get('vol_liq_ratio')):.1f}x"
        ),
        "why_it_matters": why,
        "risk_flags": flags,
        "mcap_usd": _f(row.get("latest_mcap_usd") or row.get("initial_mcap_usd")),
        "liquidity_usd": _f(row.get("latest_liquidity_usd") or row.get("initial_liquidity_usd")),
        "volume_24h": _f(row.get("latest_volume_24h") or row.get("initial_volume_24h")),
        "vol_liq_ratio": _f(row.get("vol_liq_ratio")),
        "change_1h": _f(row.get("latest_change_1h") if row.get("latest_change_1h") is not None else row.get("initial_change_1h")),
        "change_24h": _f(row.get("latest_change_24h") if row.get("latest_change_24h") is not None else row.get("initial_change_24h")),
        "scanned_at": row.get("first_seen_utc"),
        "last_seen_at": row.get("last_seen_utc"),
        "last_refresh_at": row.get("last_refresh_utc"),
        "age_minutes": round(age_m, 1) if age_m is not None else None,
        "next_check_seconds": max(0, RADAR_REFRESH_SECONDS - int((last_refresh_age or 0) * 60)) if row.get("last_refresh_utc") else 0,
        "replay_state": row.get("replay_state") or "TRACKING",
        "max_return_pct": row.get("max_return_pct"),
        "missed_reason": row.get("missed_reason"),
    }


def build_early_runner_radar(limit: int = 8, lookback_hours: int = 24, *, record: bool = True) -> dict[str, Any]:
    from utils.db import get_conn  # type: ignore

    cutoff = f"-{max(1, int(lookback_hours))} hours"
    now_iso = _now().isoformat()
    with get_conn() as conn:
        _ensure_tables(conn)
        raw_rows = conn.execute(
            """
            SELECT *
            FROM memecoin_signal_outcomes
            WHERE scanned_at >= datetime('now', ?)
              AND source IN ('DISCOVERY', 'SCANNER')
              AND COALESCE(mint, '') != ''
              AND COALESCE(mcap_at_scan, 0) BETWEEN 25000 AND 30000000
              AND COALESCE(liquidity_usd, 0) >= 12000
              AND COALESCE(volume_24h, 0) >= 25000
              AND (
                    COALESCE(score, 0) >= 50
                 OR COALESCE(change_1h_at_scan, 0) >= 5
                 OR COALESCE(change_24h_at_scan, 0) >= 20
                 OR (COALESCE(volume_24h, 0) / NULLIF(COALESCE(liquidity_usd, 0), 0)) >= 3
              )
            ORDER BY scanned_at DESC
            LIMIT 300
            """,
            (cutoff,),
        ).fetchall()

        by_mint: dict[str, dict[str, Any]] = {}
        for row in raw_rows:
            rec = dict(row)
            mint = str(rec.get("mint") or "").strip()
            if not mint:
                continue
            radar_score, why = _score(rec)
            flags = _risk_flags(rec)
            decision = _decision(rec, radar_score, flags, why)
            liq = _f(rec.get("liquidity_usd"))
            vol = _f(rec.get("volume_24h"))
            item = {
                **rec,
                "radar_score": radar_score,
                "why": why,
                "risk_flags": flags,
                "escalation_state": decision["escalation_state"],
                "verdict": decision["verdict"],
                "action_guidance": decision["action_guidance"],
                "verdict_reasons": decision["verdict_reasons"],
                "vol_liq_ratio": vol / liq if liq > 0 else 0.0,
            }
            prev = by_mint.get(mint)
            if prev is None or radar_score > _f(prev.get("radar_score")):
                by_mint[mint] = item

        for mint, rec in by_mint.items():
            if not record:
                continue
            conn.execute(
                """
                INSERT INTO memecoin_runner_radar_observations (
                    mint, symbol, first_seen_utc, last_seen_utc, source, status,
                    radar_score, scanner_score, escalation_state, verdict, action_guidance,
                    verdict_reasons_json, execution_state, risk_flags_json, why_json, initial_mcap_usd, latest_mcap_usd,
                    initial_price, latest_price, initial_liquidity_usd, latest_liquidity_usd,
                    initial_volume_24h, latest_volume_24h, initial_change_1h, latest_change_1h,
                    initial_change_24h, latest_change_24h, vol_liq_ratio, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'MANUAL_REVIEW_ONLY', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(mint) DO UPDATE SET
                    symbol=excluded.symbol,
                    last_seen_utc=excluded.last_seen_utc,
                    source=excluded.source,
                    status=excluded.status,
                    radar_score=excluded.radar_score,
                    scanner_score=excluded.scanner_score,
                    escalation_state=excluded.escalation_state,
                    verdict=excluded.verdict,
                    action_guidance=excluded.action_guidance,
                    verdict_reasons_json=excluded.verdict_reasons_json,
                    execution_state='MANUAL_REVIEW_ONLY',
                    risk_flags_json=excluded.risk_flags_json,
                    why_json=excluded.why_json,
                    latest_mcap_usd=excluded.latest_mcap_usd,
                    latest_price=COALESCE(excluded.latest_price, latest_price),
                    latest_liquidity_usd=excluded.latest_liquidity_usd,
                    latest_volume_24h=excluded.latest_volume_24h,
                    latest_change_1h=excluded.latest_change_1h,
                    latest_change_24h=excluded.latest_change_24h,
                    vol_liq_ratio=excluded.vol_liq_ratio,
                    updated_at=excluded.updated_at
                """,
                (
                    mint,
                    str(rec.get("symbol") or "UNKNOWN").upper(),
                    rec.get("scanned_at") or now_iso,
                    rec.get("scanned_at") or now_iso,
                    str(rec.get("source") or "").upper(),
                    rec.get("status"),
                    rec["radar_score"],
                    _f(rec.get("score")),
                    rec["escalation_state"],
                    rec["verdict"],
                    rec["action_guidance"],
                    json.dumps(rec["verdict_reasons"]),
                    json.dumps(rec["risk_flags"]),
                    json.dumps(rec["why"]),
                    _f(rec.get("mcap_at_scan")),
                    _f(rec.get("mcap_at_scan")),
                    _f(rec.get("price_at_scan"), None),
                    _f(rec.get("price_at_scan"), None),
                    _f(rec.get("liquidity_usd")),
                    _f(rec.get("liquidity_usd")),
                    _f(rec.get("volume_24h")),
                    _f(rec.get("volume_24h")),
                    _f(rec.get("change_1h_at_scan")),
                    _f(rec.get("change_1h_at_scan")),
                    _f(rec.get("change_24h_at_scan")),
                    _f(rec.get("change_24h_at_scan")),
                    rec["vol_liq_ratio"],
                    now_iso,
                ),
            )
            _send_runner_alert(conn, rec)

        rows = conn.execute(
            """
            SELECT *
            FROM memecoin_runner_radar_observations
            WHERE last_seen_utc >= datetime('now', ?)
            ORDER BY radar_score DESC, last_seen_utc DESC
            LIMIT ?
            """,
            (cutoff, max(1, min(int(limit), 20))),
        ).fetchall()

    runners = [_row_to_item(dict(row)) for row in rows]
    return {
        "generated_at": now_iso,
        "lookback_hours": max(1, int(lookback_hours)),
        "headline": "Early runners are on screen; execution remains manual-review only." if runners else "No strong early runners are surfacing in the current lookback.",
        "summary": {
            "total": len(runners),
            "monitor_now": sum(1 for r in runners if r["escalation_state"] == "MONITOR_NOW"),
            "fast_watch": sum(1 for r in runners if r["escalation_state"] == "FAST_WATCH"),
            "manual_sniper": sum(1 for r in runners if r["escalation_state"] == "MANUAL_SNIPER"),
            "proof_candidate": sum(1 for r in runners if r["escalation_state"] == "PROOF_CANDIDATE"),
            "look_now": sum(1 for r in runners if r["verdict"] == "LOOK_NOW"),
            "wait_for_pullback": sum(1 for r in runners if r["verdict"] == "WAIT_FOR_PULLBACK"),
            "do_not_touch": sum(1 for r in runners if r["verdict"] == "DO_NOT_TOUCH"),
            "manual_review_only": len(runners),
        },
        "runners": runners,
    }


def refresh_active_runner_snapshots(limit: int = 12) -> dict[str, Any]:
    from data.dexscreener import fetch_token_snapshot  # type: ignore
    from utils.db import get_conn  # type: ignore

    now_iso = _now().isoformat()
    refreshed = 0
    with get_conn() as conn:
        _ensure_tables(conn)
        rows = conn.execute(
            """
            SELECT *
            FROM memecoin_runner_radar_observations
            WHERE first_seen_utc >= datetime('now', ?)
              AND escalation_state IN ('FAST_WATCH', 'MONITOR_NOW', 'MANUAL_SNIPER', 'PROOF_CANDIDATE')
            ORDER BY radar_score DESC, last_seen_utc DESC
            LIMIT ?
            """,
            (f"-{RADAR_REFRESH_WINDOW_MINUTES} minutes", max(1, int(limit))),
        ).fetchall()

    for row in rows:
        rec = dict(row)
        mint = str(rec.get("mint") or "")
        if not mint:
            continue
        snap = fetch_token_snapshot(mint)
        if not snap:
            continue
        price = _f(snap.get("price"))
        mcap = _f(snap.get("market_cap") or snap.get("fdv"))
        liq = _f(snap.get("liquidity"))
        vol = _f(snap.get("volume_24h"))
        chg1h = _f(snap.get("change_1h"))
        chg24h = _f(snap.get("change_24h"))
        vol_liq = vol / liq if liq > 0 else _f(rec.get("vol_liq_ratio"))
        decision_rec = {
            **rec,
            "score": rec.get("scanner_score"),
            "change_1h_at_scan": chg1h,
            "change_24h_at_scan": chg24h,
            "liquidity_usd": liq,
            "volume_24h": vol,
            "mcap_at_scan": mcap,
        }
        flags = json.loads(rec.get("risk_flags_json") or "[]")
        why = json.loads(rec.get("why_json") or "[]")
        refreshed_score = _f(rec.get("radar_score"))
        refreshed_why = list(why)
        if chg1h >= 5 and "1h velocity" not in refreshed_why:
            refreshed_why.append("1h velocity")
        if chg24h >= 25 and "24h breakout" not in refreshed_why:
            refreshed_why.append("24h breakout")
        if vol_liq >= 3 and "volume/liquidity explosion" not in refreshed_why:
            refreshed_why.append("volume/liquidity explosion")
        decision = _decision(decision_rec, refreshed_score, flags, refreshed_why)
        initial_price = _f(rec.get("initial_price"))
        max_return = _f(rec.get("max_return_pct"), None)
        if initial_price and price:
            live_return = round((price - initial_price) / initial_price * 100.0, 2)
            max_return = live_return if max_return is None else max(max_return, live_return)
        with get_conn() as conn:
            _ensure_tables(conn)
            conn.execute(
                """
                UPDATE memecoin_runner_radar_observations
                SET last_refresh_utc=?, latest_price=?, latest_mcap_usd=?,
                    latest_liquidity_usd=?, latest_volume_24h=?, latest_change_1h=?,
                    latest_change_24h=?, vol_liq_ratio=?, escalation_state=?, verdict=?,
                    action_guidance=?, verdict_reasons_json=?, max_return_pct=?, updated_at=?
                WHERE mint=?
                """,
                (
                    now_iso,
                    price,
                    mcap,
                    liq,
                    vol,
                    chg1h,
                    chg24h,
                    vol_liq,
                    decision["escalation_state"],
                    decision["verdict"],
                    decision["action_guidance"],
                    json.dumps(decision["verdict_reasons"]),
                    max_return,
                    now_iso,
                    mint,
                ),
            )
            _send_runner_alert(
                conn,
                {
                    **decision_rec,
                    "mint": mint,
                    "symbol": rec.get("symbol"),
                    "radar_score": refreshed_score,
                    "escalation_state": decision["escalation_state"],
                    "verdict": decision["verdict"],
                    "risk_flags": flags,
                    "verdict_reasons": decision["verdict_reasons"],
                    "vol_liq_ratio": vol_liq,
                },
            )
        refreshed += 1
    return {"refreshed": refreshed, "checked": len(rows), "updated_at": now_iso}


def update_runner_replay() -> dict[str, Any]:
    from utils.db import get_conn  # type: ignore

    now_iso = _now().isoformat()
    with get_conn() as conn:
        _ensure_tables(conn)
        rows = conn.execute(
            """
            SELECT r.*, m.return_1h_pct, m.return_4h_pct, m.return_24h_pct
            FROM memecoin_runner_radar_observations r
            LEFT JOIN memecoin_signal_outcomes m ON m.mint = r.mint
            WHERE r.first_seen_utc >= datetime('now', '-7 days')
            ORDER BY r.radar_score DESC
            """
        ).fetchall()
        updated = 0
        blockers = Counter()
        for row in rows:
            rec = dict(row)
            returns = [_f(rec.get("return_1h_pct"), None), _f(rec.get("return_4h_pct"), None), _f(rec.get("return_24h_pct"), None), _f(rec.get("max_return_pct"), None)]
            valid = [r for r in returns if r is not None]
            if not valid:
                continue
            max_ret = max(valid)
            flags = json.loads(rec.get("risk_flags_json") or "[]")
            replay_state = "TRACKED_WINNER" if max_ret >= 50 else "TRACKED"
            missed_reason = None
            if max_ret >= 100 and any(flag in {"BLOCKED", "LOW_TRUST", "DISTRUST", "DO_NOT_TOUCH"} for flag in flags):
                replay_state = "MISSED_RUNNER"
                missed_reason = ", ".join(flags) or "blocked"
                for flag in flags:
                    blockers[str(flag)] += 1
            conn.execute(
                """
                UPDATE memecoin_runner_radar_observations
                SET return_1h_pct=COALESCE(return_1h_pct, ?),
                    return_4h_pct=COALESCE(return_4h_pct, ?),
                    return_24h_pct=COALESCE(return_24h_pct, ?),
                    max_return_pct=?,
                    replay_state=?,
                    missed_reason=?,
                    updated_at=?
                WHERE mint=?
                """,
                (
                    rec.get("return_1h_pct"),
                    rec.get("return_4h_pct"),
                    rec.get("return_24h_pct"),
                    max_ret,
                    replay_state,
                    missed_reason,
                    now_iso,
                    rec.get("mint"),
                ),
            )
            updated += 1
        payload = {"updated_at": now_iso, "updated": updated, "missed_blockers": dict(blockers)}
        conn.execute(
            "INSERT OR REPLACE INTO kv_store (key, value) VALUES (?, ?)",
            ("early_runner_replay_summary", json.dumps(payload, separators=(",", ":"))),
        )
    return payload


def early_runner_maintenance_step() -> dict[str, Any]:
    radar = build_early_runner_radar(limit=12, lookback_hours=24, record=True)
    refresh = refresh_active_runner_snapshots()
    replay = update_runner_replay()
    return {"radar": radar.get("summary"), "refresh": refresh, "replay": replay}
