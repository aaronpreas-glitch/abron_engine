from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone

from utils.db import DB_PATH, get_conn, is_database_locked_error, with_db_retry

MAX_TRUSTED_RETURN_PCT = float(os.getenv("PAPER_SIGNAL_MAX_TRUSTED_RETURN_PCT", "5000"))


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None = None) -> str:
    return (dt or _now()).isoformat()


def _f(value, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def _parse_ts(value) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _ensure_tables(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS paper_signal_positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            opened_ts_utc TEXT NOT NULL,
            closed_ts_utc TEXT,
            status TEXT NOT NULL DEFAULT 'OPEN',
            lane TEXT NOT NULL,
            symbol TEXT NOT NULL,
            mint TEXT NOT NULL,
            entry_price REAL NOT NULL,
            exit_price REAL,
            entry_marketcap REAL,
            exit_marketcap REAL,
            amount_usd REAL NOT NULL DEFAULT 0,
            token_amount REAL NOT NULL DEFAULT 0,
            entry_score REAL,
            entry_quality_score REAL,
            entry_risk_score REAL,
            entry_pressure_score REAL,
            entry_snapshot_json TEXT,
            exit_reason TEXT,
            pnl_pct REAL,
            pnl_usd REAL,
            max_favorable_excursion_pct REAL DEFAULT 0,
            max_adverse_excursion_pct REAL DEFAULT 0,
            last_review_ts_utc TEXT,
            last_snapshot_json TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_paper_signal_positions_status_mint
        ON paper_signal_positions(status, mint)
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS paper_signal_exit_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts_utc TEXT NOT NULL,
            paper_position_id INTEGER NOT NULL,
            mint TEXT NOT NULL,
            symbol TEXT NOT NULL,
            current_price REAL,
            current_marketcap REAL,
            current_return_pct REAL,
            age_hours REAL,
            pressure_score REAL,
            quality_score REAL,
            risk_score REAL,
            buy_pressure_1h REAL,
            change_1h_pct REAL,
            change_24h_pct REAL,
            volume_24h_usd REAL,
            liquidity_usd REAL,
            review_state TEXT NOT NULL,
            recommended_action TEXT NOT NULL,
            exit_reason TEXT,
            snapshot_json TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_paper_signal_exit_snapshots_position_ts
        ON paper_signal_exit_snapshots(paper_position_id, ts_utc)
        """
    )


def _load_previous_status_readonly() -> dict | None:
    try:
        with sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=0.75) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout=750")
            row = conn.execute("SELECT value FROM kv_store WHERE key='paper_signal_learning_status'").fetchone()
            if not row or not row["value"]:
                return None
            payload = json.loads(row["value"])
            return payload if isinstance(payload, dict) else None
    except Exception:
        return None


def _locked_status(exc: BaseException) -> dict:
    previous = _load_previous_status_readonly() or {}
    payload = {
        **previous,
        "watchdog": "PAPER_SIGNAL_LEARNING",
        "status": "LOCKED_RETRY_PENDING",
        "checked_at": _iso(),
        "detail": f"Paper signal learning hit SQLite writer pressure and will retry next cycle: {exc}",
    }
    payload["db_lock_retry_pending"] = True
    return payload


def _score_row(row: dict) -> float:
    liquidity = _f(row.get("liquidity"))
    volume_24h = _f(row.get("volume_24h_usd"))
    quality = _f(row.get("quality_score"))
    risk = _f(row.get("risk_score"))
    pressure = _f(row.get("pressure_score"))
    vol_liq = volume_24h / liquidity if liquidity > 0 else 0.0
    return round(
        quality * 0.42
        + risk * 0.18
        + pressure * 0.22
        + min(100.0, liquidity / 10_000.0) * 0.08
        + min(100.0, vol_liq * 25.0) * 0.10,
        1,
    )


def _candidate_blockers(row: dict) -> list[str]:
    blockers: list[str] = []
    if _price_unit_suspect(row):
        blockers.append("price_marketcap_unit_mismatch")
    if str(row.get("identity_status") or "").upper() != "RESOLVED":
        blockers.append("identity_not_resolved")
    if str(row.get("data_freshness") or "").upper() != "LIVE":
        blockers.append("market_data_not_live")
    if _f(row.get("price")) <= 0:
        blockers.append("missing_price")
    if _f(row.get("liquidity")) < 150_000:
        blockers.append("liquidity_below_paper_gate")
    if _f(row.get("volume_24h_usd")) < 175_000:
        blockers.append("volume_below_paper_gate")
    if _f(row.get("quality_score")) < 78:
        blockers.append("quality_below_paper_gate")
    if _f(row.get("risk_score")) < 75:
        blockers.append("risk_below_paper_gate")
    if _f(row.get("pressure_score")) < 62:
        blockers.append("pressure_below_paper_gate")
    if _f(row.get("buy_pressure_1h"), 50.0) < 45:
        blockers.append("sell_pressure_watch")
    if _f(row.get("price_change_1h_percent")) < -6:
        blockers.append("sharp_1h_drop")
    if _f(row.get("price_change_24h_percent")) < -15:
        blockers.append("weak_24h_trend")
    return blockers


def _price_unit_suspect(row: dict) -> bool:
    price = _f(row.get("price"))
    marketcap = _f(row.get("marketcap"))
    change_1h = abs(_f(row.get("price_change_1h_percent")))
    change_24h = abs(_f(row.get("price_change_24h_percent")))
    if price <= 0:
        return False
    if price > 25 and 0 < marketcap < 5_000_000:
        return True
    if marketcap > 0 and price / marketcap > 0.005:
        return True
    if price > 10 and (change_1h > 1000 or change_24h > 5000):
        return True
    return False


def _open_candidates(conn, limit: int) -> list[dict]:
    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='token_intelligence_current'"
    ).fetchone():
        return []
    rows = conn.execute(
        """
        SELECT *
        FROM token_intelligence_current
        WHERE mint IS NOT NULL AND mint != ''
          AND symbol IS NOT NULL AND symbol != ''
          AND price IS NOT NULL AND price > 0
        ORDER BY quality_score DESC, pressure_score DESC, updated_at DESC
        LIMIT 80
        """
    ).fetchall()
    out: list[dict] = []
    for raw in rows:
        row = dict(raw)
        blockers = _candidate_blockers(row)
        score = _score_row(row)
        if blockers or score < float(os.getenv("PAPER_SIGNAL_ENTRY_MIN_SCORE", "78")):
            continue
        row["_paper_score"] = score
        out.append(row)
        if len(out) >= limit:
            break
    return out


def _candidate_diagnostics(conn, limit: int = 80) -> dict:
    """Explain why paper learning did or did not open new positions."""
    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='token_intelligence_current'"
    ).fetchone():
        return {"available": False, "detail": "token_intelligence_current table missing"}
    min_score = float(os.getenv("PAPER_SIGNAL_ENTRY_MIN_SCORE", "78"))
    rows = conn.execute(
        """
        SELECT *
        FROM token_intelligence_current
        WHERE mint IS NOT NULL AND mint != ''
          AND symbol IS NOT NULL AND symbol != ''
          AND price IS NOT NULL AND price > 0
        ORDER BY quality_score DESC, pressure_score DESC, updated_at DESC
        LIMIT ?
        """,
        (max(1, int(limit)),),
    ).fetchall()
    blocker_counts: dict[str, int] = {}
    best_blocked: list[dict] = []
    eligible = 0
    for raw in rows:
        row = dict(raw)
        blockers = _candidate_blockers(row)
        score = _score_row(row)
        if score < min_score:
            blockers = [*blockers, "paper_score_below_entry_min"]
        if not blockers:
            eligible += 1
            continue
        for blocker in blockers:
            blocker_counts[blocker] = int(blocker_counts.get(blocker) or 0) + 1
        if len(best_blocked) < 8:
            best_blocked.append({
                "symbol": str(row.get("symbol") or "").upper(),
                "mint": row.get("mint"),
                "paper_score": score,
                "quality_score": _f(row.get("quality_score")),
                "risk_score": _f(row.get("risk_score")),
                "pressure_score": _f(row.get("pressure_score")),
                "liquidity": _f(row.get("liquidity")),
                "volume_24h_usd": _f(row.get("volume_24h_usd")),
                "data_freshness": row.get("data_freshness"),
                "identity_status": row.get("identity_status"),
                "blockers": blockers[:6],
            })
    top_blockers = [
        {"blocker": key, "count": count}
        for key, count in sorted(blocker_counts.items(), key=lambda item: item[1], reverse=True)[:10]
    ]
    return {
        "available": True,
        "scanned": len(rows),
        "eligible": eligible,
        "entry_min_score": min_score,
        "top_blockers": top_blockers,
        "best_blocked": best_blocked,
    }


def _snapshot_from_row(row: dict) -> dict:
    return {
        "symbol": str(row.get("symbol") or "").upper(),
        "mint": str(row.get("mint") or ""),
        "price": _f(row.get("price")),
        "marketcap": _f(row.get("marketcap")),
        "liquidity": _f(row.get("liquidity")),
        "volume_24h_usd": _f(row.get("volume_24h_usd")),
        "quality_score": _f(row.get("quality_score")),
        "risk_score": _f(row.get("risk_score")),
        "pressure_score": _f(row.get("pressure_score")),
        "buy_pressure_1h": _f(row.get("buy_pressure_1h"), 50.0),
        "price_change_1h_percent": _f(row.get("price_change_1h_percent")),
        "price_change_24h_percent": _f(row.get("price_change_24h_percent")),
        "data_freshness": row.get("data_freshness"),
        "identity_status": row.get("identity_status"),
        "market_source": row.get("market_source"),
        "updated_at": row.get("updated_at"),
    }


def _open_paper_position(conn, row: dict, amount_usd: float) -> bool:
    mint = str(row.get("mint") or "").strip()
    if not mint:
        return False
    if conn.execute("SELECT 1 FROM paper_signal_positions WHERE status='OPEN' AND mint=?", (mint,)).fetchone():
        return False
    cooldown_h = float(os.getenv("PAPER_SIGNAL_REENTRY_COOLDOWN_HOURS", "12"))
    if conn.execute(
        """
        SELECT 1 FROM paper_signal_positions
        WHERE mint=? AND closed_ts_utc >= datetime('now', ?)
        LIMIT 1
        """,
        (mint, f"-{max(1.0, cooldown_h)} hours"),
    ).fetchone():
        return False
    price = _f(row.get("price"))
    if price <= 0:
        return False
    snapshot = _snapshot_from_row(row)
    conn.execute(
        """
        INSERT INTO paper_signal_positions
            (opened_ts_utc, status, lane, symbol, mint, entry_price, entry_marketcap,
             amount_usd, token_amount, entry_score, entry_quality_score, entry_risk_score,
             entry_pressure_score, entry_snapshot_json, last_review_ts_utc, last_snapshot_json)
        VALUES (?, 'OPEN', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            _iso(),
            "MEMECOINS",
            str(row.get("symbol") or "").upper(),
            mint,
            price,
            _f(row.get("marketcap")),
            amount_usd,
            amount_usd / price if price > 0 else 0.0,
            _f(row.get("_paper_score")),
            _f(row.get("quality_score")),
            _f(row.get("risk_score")),
            _f(row.get("pressure_score")),
            json.dumps(snapshot, separators=(",", ":")),
            _iso(),
            json.dumps(snapshot, separators=(",", ":")),
        ),
    )
    return True


def _current_rows_by_mint(conn, mints: list[str]) -> dict[str, dict]:
    clean = [m for m in mints if m]
    if not clean:
        return {}
    placeholders = ",".join("?" for _ in clean)
    rows = conn.execute(
        f"SELECT * FROM token_intelligence_current WHERE mint IN ({placeholders})",
        clean,
    ).fetchall()
    return {str(row["mint"] or ""): dict(row) for row in rows}


def _review_open_positions(conn) -> dict:
    rows = conn.execute("SELECT * FROM paper_signal_positions WHERE status='OPEN' ORDER BY opened_ts_utc ASC").fetchall()
    if not rows:
        return {"reviewed": 0, "closed": 0}
    current_by_mint = _current_rows_by_mint(conn, [str(row["mint"] or "") for row in rows])
    closed = 0
    reviewed = 0
    stop_pct = float(os.getenv("PAPER_SIGNAL_STOP_PCT", "15"))
    tp_pct = float(os.getenv("PAPER_SIGNAL_TP_PCT", "25"))
    runner_pullback_pct = float(os.getenv("PAPER_SIGNAL_RUNNER_PULLBACK_PCT", "10"))
    max_hold_h = float(os.getenv("PAPER_SIGNAL_MAX_HOLD_HOURS", "72"))

    for raw in rows:
        pos = dict(raw)
        current = current_by_mint.get(str(pos.get("mint") or ""))
        if not current:
            continue
        price = _f(current.get("price"))
        entry = _f(pos.get("entry_price"))
        if price <= 0 or entry <= 0:
            continue
        if _price_unit_suspect(current):
            continue
        ret = round((price - entry) / entry * 100.0, 2)
        if abs(ret) > MAX_TRUSTED_RETURN_PCT:
            continue
        opened = _parse_ts(pos.get("opened_ts_utc"))
        age_h = max(0.0, (_now() - opened).total_seconds() / 3600.0) if opened else 0.0
        prev_mfe = _f(pos.get("max_favorable_excursion_pct"))
        prev_mae = _f(pos.get("max_adverse_excursion_pct"))
        mfe = max(prev_mfe, ret)
        mae = min(prev_mae, ret)
        pressure = _f(current.get("pressure_score"))
        buy_pressure = _f(current.get("buy_pressure_1h"), 50.0)
        change_1h = _f(current.get("price_change_1h_percent"))
        review_state = "HOLD"
        action = "hold_position"
        exit_reason = None
        if ret <= -stop_pct:
            review_state = "EXIT_STOP"
            action = "close_position"
            exit_reason = f"paper_stop_loss — return {ret:+.1f}% breached -{stop_pct:.1f}%"
        elif mfe >= tp_pct and (mfe - ret) >= runner_pullback_pct:
            review_state = "EXIT_RUNNER_ROLLOVER"
            action = "close_position"
            exit_reason = f"paper_runner_rollover — MFE {mfe:+.1f}% gave back {(mfe - ret):.1f}%"
        elif ret >= tp_pct and (pressure < 62 or buy_pressure < 48 or change_1h < 0):
            review_state = "EXIT_TAKE_PROFIT"
            action = "close_position"
            exit_reason = f"paper_take_profit — return {ret:+.1f}% with weakening pressure"
        elif age_h >= max_hold_h:
            review_state = "EXIT_MAX_HOLD"
            action = "close_position"
            exit_reason = f"paper_max_hold — open {age_h:.1f}h"
        snapshot = _snapshot_from_row(current)
        snapshot.update({"mfe_pct": round(mfe, 2), "mae_pct": round(mae, 2)})
        conn.execute(
            """
            INSERT INTO paper_signal_exit_snapshots
                (ts_utc, paper_position_id, mint, symbol, current_price, current_marketcap,
                 current_return_pct, age_hours, pressure_score, quality_score, risk_score,
                 buy_pressure_1h, change_1h_pct, change_24h_pct, volume_24h_usd,
                 liquidity_usd, review_state, recommended_action, exit_reason, snapshot_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _iso(),
                int(pos["id"]),
                str(pos.get("mint") or ""),
                str(pos.get("symbol") or ""),
                price,
                _f(current.get("marketcap")),
                ret,
                round(age_h, 3),
                pressure,
                _f(current.get("quality_score")),
                _f(current.get("risk_score")),
                buy_pressure,
                change_1h,
                _f(current.get("price_change_24h_percent")),
                _f(current.get("volume_24h_usd")),
                _f(current.get("liquidity")),
                review_state,
                action,
                exit_reason,
                json.dumps(snapshot, separators=(",", ":")),
            ),
        )
        reviewed += 1
        updates = [
            "max_favorable_excursion_pct=?",
            "max_adverse_excursion_pct=?",
            "last_review_ts_utc=?",
            "last_snapshot_json=?",
        ]
        params: list = [round(mfe, 2), round(mae, 2), _iso(), json.dumps(snapshot, separators=(",", ":"))]
        if exit_reason:
            pnl_usd = _f(pos.get("amount_usd")) * ret / 100.0
            updates.extend(["status='CLOSED'", "closed_ts_utc=?", "exit_price=?", "exit_marketcap=?", "exit_reason=?", "pnl_pct=?", "pnl_usd=?"])
            params.extend([_iso(), price, _f(current.get("marketcap")), exit_reason, ret, round(pnl_usd, 4)])
            closed += 1
        params.append(int(pos["id"]))
        conn.execute(f"UPDATE paper_signal_positions SET {', '.join(updates)} WHERE id=?", params)
    return {"reviewed": reviewed, "closed": closed}


def _paper_signal_learning_step_once(*, force: bool = False) -> dict:
    enabled = os.getenv("PAPER_SIGNAL_LEARNING_ENABLED", "true").lower() in ("1", "true", "yes")
    if not enabled:
        return {"watchdog": "PAPER_SIGNAL_LEARNING", "status": "PAUSED", "detail": "Disabled by env."}
    min_interval_s = max(60, int(os.getenv("PAPER_SIGNAL_LEARNING_MIN_INTERVAL_SECONDS", "300")))
    with get_conn() as conn:
        _ensure_tables(conn)
        if not force:
            row = conn.execute("SELECT value FROM kv_store WHERE key='paper_signal_learning_status'").fetchone()
            if row:
                try:
                    previous = json.loads(row["value"])
                    checked = _parse_ts(previous.get("checked_at"))
                    if checked and (_now() - checked).total_seconds() < min_interval_s:
                        previous["skipped_fresh"] = True
                        return previous
                except Exception:
                    pass
        review = _review_open_positions(conn)
        candidate_diagnostics = _candidate_diagnostics(conn)
        max_open = max(1, int(os.getenv("PAPER_SIGNAL_MAX_OPEN", "8")))
        open_count = int((conn.execute("SELECT COUNT(*) FROM paper_signal_positions WHERE status='OPEN'").fetchone() or [0])[0])
        opened = 0
        if open_count < max_open:
            amount_usd = float(os.getenv("PAPER_SIGNAL_SIZE_USD", "10"))
            for row in _open_candidates(conn, max_open - open_count):
                if _open_paper_position(conn, row, amount_usd):
                    opened += 1
        summary = conn.execute(
            """
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN status='OPEN' THEN 1 ELSE 0 END) AS open_n,
                SUM(CASE WHEN status='CLOSED' THEN 1 ELSE 0 END) AS closed_n,
                SUM(CASE WHEN status='CLOSED' AND ABS(COALESCE(pnl_pct, 0)) <= ? THEN 1 ELSE 0 END) AS trusted_closed_n,
                SUM(CASE WHEN status='CLOSED' AND ABS(COALESCE(pnl_pct, 0)) > ? THEN 1 ELSE 0 END) AS untrusted_closed_n,
                AVG(CASE WHEN status='CLOSED' AND ABS(COALESCE(pnl_pct, 0)) <= ? THEN pnl_pct END) AS avg_closed_return_pct,
                SUM(CASE WHEN status='CLOSED' AND ABS(COALESCE(pnl_pct, 0)) <= ? AND pnl_pct > 0 THEN 1 ELSE 0 END) AS wins
            FROM paper_signal_positions
            """,
            (MAX_TRUSTED_RETURN_PCT, MAX_TRUSTED_RETURN_PCT, MAX_TRUSTED_RETURN_PCT, MAX_TRUSTED_RETURN_PCT),
        ).fetchone()
        closed_n = int(summary["closed_n"] or 0) if summary else 0
        trusted_closed_n = int(summary["trusted_closed_n"] or 0) if summary else 0
        wins = int(summary["wins"] or 0) if summary else 0
        payload = {
            "watchdog": "PAPER_SIGNAL_LEARNING",
            "status": "ACTIVE",
            "checked_at": _iso(),
            "opened": opened,
            "reviewed": int(review.get("reviewed") or 0),
            "closed": int(review.get("closed") or 0),
            "summary": {
                "total": int(summary["total"] or 0) if summary else 0,
                "open": int(summary["open_n"] or 0) if summary else 0,
                "closed": closed_n,
                "trusted_closed": trusted_closed_n,
                "untrusted_closed": int(summary["untrusted_closed_n"] or 0) if summary else 0,
                "win_rate_pct": round(wins / trusted_closed_n * 100.0, 1) if trusted_closed_n else None,
                "avg_closed_return_pct": round(float(summary["avg_closed_return_pct"] or 0.0), 2) if trusted_closed_n else None,
                "max_trusted_return_pct": MAX_TRUSTED_RETURN_PCT,
            },
            "candidate_diagnostics": candidate_diagnostics,
            "detail": f"Paper signal loop opened {opened}, reviewed {int(review.get('reviewed') or 0)}, closed {int(review.get('closed') or 0)}.",
        }
        conn.execute(
            "INSERT OR REPLACE INTO kv_store (key, value) VALUES (?, ?)",
            ("paper_signal_learning_status", json.dumps(payload, separators=(",", ":"))),
        )
        return payload


def paper_signal_learning_step(*, force: bool = False) -> dict:
    try:
        return with_db_retry(
            lambda: _paper_signal_learning_step_once(force=force),
            retries=max(1, int(os.getenv("PAPER_SIGNAL_DB_RETRIES", "5"))),
            base_sleep_s=max(0.05, float(os.getenv("PAPER_SIGNAL_DB_RETRY_SLEEP_SECONDS", "0.35"))),
        )
    except Exception as exc:
        if is_database_locked_error(exc):
            return _locked_status(exc)
        raise
