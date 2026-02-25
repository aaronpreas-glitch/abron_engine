import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from statistics import median

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data_storage", "engine.db")


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

    with get_conn() as conn:
        cur = conn.cursor()

        cur.execute("""
        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts_utc TEXT NOT NULL,
            chain TEXT NOT NULL,
            symbol TEXT NOT NULL,
            mint TEXT,
            pair_address TEXT,
            category TEXT,
            setup_type TEXT,
            conviction INTEGER,
            regime_score REAL,
            regime_label TEXT,
            liquidity_usd REAL,
            liquidity_change_24h REAL,
            volume_24h REAL,
            price_usd REAL,
            change_24h REAL,
            rel_strength_vs_sol REAL,
            score_total REAL,
            decision TEXT,
            notes TEXT
        );
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            opened_ts_utc TEXT NOT NULL,
            closed_ts_utc TEXT,
            chain TEXT NOT NULL,
            symbol TEXT NOT NULL,
            mint TEXT,
            pair_address TEXT,
            category TEXT,
            setup_type TEXT,
            regime_score REAL,
            regime_label TEXT,
            entry_price REAL NOT NULL,
            stop_price REAL NOT NULL,
            exit_price REAL,
            risk_pct REAL,
            position_pct REAL,
            partial1_price REAL,
            partial1_pct_closed REAL,
            partial2_price REAL,
            partial2_pct_closed REAL,
            mae REAL,
            mfe REAL,
            r_multiple REAL,
            pnl_pct REAL,
            status TEXT NOT NULL DEFAULT 'OPEN',
            notes TEXT,
            tx_sig TEXT,
            exit_reason TEXT,
            position_usd REAL
        );
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS regime_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts_utc TEXT NOT NULL,
            sol_change_24h REAL,
            breadth_pct REAL,
            liquidity_score REAL,
            volume_score REAL,
            regime_score REAL,
            regime_label TEXT,
            notes TEXT
        );
        """)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS alert_outcomes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_ts_utc TEXT NOT NULL,
            symbol TEXT NOT NULL,
            mint TEXT,
            entry_price REAL NOT NULL,
            score REAL,
            regime_score REAL,
            regime_label TEXT,
            confidence TEXT,
            evaluated_1h_ts_utc TEXT,
            return_1h_pct REAL,
            evaluated_4h_ts_utc TEXT,
            return_4h_pct REAL,
            evaluated_24h_ts_utc TEXT,
            return_24h_pct REAL,
            last_error TEXT,
            status TEXT NOT NULL DEFAULT 'PENDING'
        );
        """)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS risk_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            pause_until_utc TEXT,
            reason TEXT,
            updated_ts_utc TEXT NOT NULL
        );
        """)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS symbol_controls (
            symbol TEXT PRIMARY KEY,
            cooldown_until_utc TEXT,
            blacklist_until_utc TEXT,
            reason TEXT,
            updated_ts_utc TEXT NOT NULL
        );
        """)
        cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_signals_symbol_ts
        ON signals(symbol, ts_utc);
        """)
        cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_alert_outcomes_symbol_ts
        ON alert_outcomes(symbol, created_ts_utc);
        """)
        cur.execute(
            """
            INSERT OR IGNORE INTO risk_state (id, pause_until_utc, reason, updated_ts_utc)
            VALUES (1, NULL, NULL, ?)
            """,
            (datetime.utcnow().isoformat(),),
        )

        # ── Executor migrations — idempotent ALTER TABLE ────────────────────
        for _col, _type in [("tx_sig", "TEXT"), ("exit_reason", "TEXT"), ("position_usd", "REAL")]:
            try:
                cur.execute(f"ALTER TABLE trades ADD COLUMN {_col} {_type}")
            except Exception:
                pass  # column already exists — fine

        # ── Phase-2 brain migrations — lane/source tagging + score breakdown ──
        # alert_outcomes: add lane (which scanner fired) + source (data provider)
        for _col, _type in [("lane", "TEXT"), ("source", "TEXT")]:
            try:
                cur.execute(f"ALTER TABLE alert_outcomes ADD COLUMN {_col} {_type}")
            except Exception:
                pass
        # signals: score_breakdown stores JSON of 7-component scores at ALERT time
        try:
            cur.execute("ALTER TABLE signals ADD COLUMN score_breakdown TEXT")
        except Exception:
            pass

        # ── Phase-3 market cycle migrations — tag every signal + outcome ───────
        # cycle_phase = 'BEAR' | 'TRANSITION' | 'BULL' based on rolling regime median
        for _tbl in ("regime_snapshots", "alert_outcomes", "signals"):
            try:
                cur.execute(f"ALTER TABLE {_tbl} ADD COLUMN cycle_phase TEXT")
            except Exception:
                pass  # column already exists — fine

        # Add helius_grade to signals table
        try:
            cur.execute("ALTER TABLE signals ADD COLUMN helius_grade TEXT")
        except Exception:
            pass


def log_signal(signal_data: dict):
    """
    Insert one signal row into the signals table.
    """
    with get_conn() as conn:
        cur = conn.cursor()

        helius_grade = signal_data.get("helius_grade")

        cur.execute("""
        INSERT INTO signals (
            ts_utc,
            chain,
            symbol,
            mint,
            pair_address,
            category,
            setup_type,
            conviction,
            regime_score,
            regime_label,
            liquidity_usd,
            liquidity_change_24h,
            volume_24h,
            price_usd,
            change_24h,
            rel_strength_vs_sol,
            score_total,
            decision,
            notes,
            score_breakdown,
            helius_grade
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            datetime.utcnow().isoformat(),
            signal_data.get("chain", "solana"),
            signal_data.get("symbol", ""),
            signal_data.get("mint"),
            signal_data.get("pair_address"),
            signal_data.get("category"),
            signal_data.get("setup_type"),
            signal_data.get("conviction"),
            signal_data.get("regime_score"),
            signal_data.get("regime_label"),
            signal_data.get("liquidity"),
            signal_data.get("liquidity_change"),
            signal_data.get("volume_24h"),
            signal_data.get("price"),
            signal_data.get("change_24h"),
            signal_data.get("rel_strength"),
            signal_data.get("score"),
            signal_data.get("decision"),
            signal_data.get("notes"),
            signal_data.get("score_breakdown"),
            helius_grade,
        ))


def open_manual_position(
    symbol: str,
    mint: str | None = None,
    pair_address: str | None = None,
    entry_price: float | None = None,
    stop_price: float | None = None,
    notes: str | None = None,
):
    """
    Open one tracked position for a symbol/mint if no active one exists.
    Returns: {"created": bool, "position": dict|None}
    """
    symbol_norm = str(symbol or "").strip().upper()
    mint_norm = str(mint or "").strip() or None
    if not symbol_norm:
        return {"created": False, "position": None}

    entry = float(entry_price or 0)
    stop = float(stop_price) if stop_price is not None else (entry * 0.9 if entry > 0 else 0.0)
    now_iso = datetime.utcnow().isoformat()

    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT *
            FROM trades
            WHERE status = 'OPEN'
              AND (
                    symbol = ?
                 OR (? IS NOT NULL AND mint = ?)
              )
            ORDER BY opened_ts_utc DESC
            LIMIT 1
            """,
            (symbol_norm, mint_norm, mint_norm),
        )
        existing = cur.fetchone()
        if existing:
            return {"created": False, "position": dict(existing)}

        cur.execute(
            """
            INSERT INTO trades (
                opened_ts_utc,
                chain,
                symbol,
                mint,
                pair_address,
                entry_price,
                stop_price,
                status,
                notes
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 'OPEN', ?)
            """,
            (
                now_iso,
                "solana",
                symbol_norm,
                mint_norm,
                pair_address,
                entry,
                stop,
                (notes or "")[:400],
            ),
        )
        trade_id = int(cur.lastrowid)
        cur.execute("SELECT * FROM trades WHERE id = ?", (trade_id,))
        row = cur.fetchone()
        return {"created": True, "position": dict(row) if row else None}


def close_manual_position(
    symbol: str | None = None,
    mint: str | None = None,
    exit_price: float | None = None,
    notes: str | None = None,
) -> int:
    """
    Close active tracked positions for a symbol or mint.
    Returns number of closed positions.
    """
    symbol_norm = str(symbol or "").strip().upper() or None
    mint_norm = str(mint or "").strip() or None
    if not symbol_norm and not mint_norm:
        return 0

    with get_conn() as conn:
        cur = conn.cursor()
        if symbol_norm and mint_norm:
            cur.execute(
                """
                SELECT id, notes
                FROM trades
                WHERE status = 'OPEN'
                  AND (symbol = ? OR mint = ?)
                """,
                (symbol_norm, mint_norm),
            )
        elif symbol_norm:
            cur.execute(
                """
                SELECT id, notes
                FROM trades
                WHERE status = 'OPEN' AND symbol = ?
                """,
                (symbol_norm,),
            )
        else:
            cur.execute(
                """
                SELECT id, notes
                FROM trades
                WHERE status = 'OPEN' AND mint = ?
                """,
                (mint_norm,),
            )
        rows = cur.fetchall()
        if not rows:
            return 0

        now_iso = datetime.utcnow().isoformat()
        count = 0
        for row in rows:
            existing_notes = str(row["notes"] or "").strip()
            add_notes = str(notes or "").strip()
            merged_notes = existing_notes
            if add_notes:
                merged_notes = f"{existing_notes} | {add_notes}".strip(" |")
            cur.execute(
                """
                UPDATE trades
                SET closed_ts_utc = ?,
                    status = 'CLOSED',
                    exit_price = COALESCE(?, exit_price),
                    notes = ?
                WHERE id = ?
                """,
                (now_iso, exit_price, merged_notes[:400], int(row["id"])),
            )
            count += 1
        return count


def get_open_positions(limit: int = 100) -> list[dict]:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, opened_ts_utc, symbol, mint, pair_address, entry_price, stop_price, notes
            FROM trades
            WHERE status = 'OPEN'
            ORDER BY opened_ts_utc DESC
            LIMIT ?
            """,
            (max(1, int(limit)),),
        )
        return [dict(r) for r in cur.fetchall()]


def has_open_position(symbol: str | None = None, mint: str | None = None) -> bool:
    symbol_norm = str(symbol or "").strip().upper() or None
    mint_norm = str(mint or "").strip() or None
    if not symbol_norm and not mint_norm:
        return False

    with get_conn() as conn:
        cur = conn.cursor()
        if symbol_norm and mint_norm:
            cur.execute(
                """
                SELECT 1
                FROM trades
                WHERE status = 'OPEN'
                  AND (symbol = ? OR mint = ?)
                LIMIT 1
                """,
                (symbol_norm, mint_norm),
            )
        elif symbol_norm:
            cur.execute(
                """
                SELECT 1
                FROM trades
                WHERE status = 'OPEN' AND symbol = ?
                LIMIT 1
                """,
                (symbol_norm,),
            )
        else:
            cur.execute(
                """
                SELECT 1
                FROM trades
                WHERE status = 'OPEN' AND mint = ?
                LIMIT 1
                """,
                (mint_norm,),
            )
        return cur.fetchone() is not None


def queue_alert_outcome(outcome_data: dict):
    """
    Persist alert entry for delayed return attribution.

    Accepts optional 'lane' (which scanner fired: 'new_runner', 'legacy',
    'watchlist', 'launch') and 'source' (data provider: 'birdeye',
    'pump_fun_ws', 'dexscreener_profile', etc.) for per-lane win-rate learning.
    Accepts optional 'cycle_phase' ('BEAR' | 'TRANSITION' | 'BULL') for
    market-cycle-aware learning (Phase 3).
    """
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO alert_outcomes (
                created_ts_utc,
                symbol,
                mint,
                entry_price,
                score,
                regime_score,
                regime_label,
                confidence,
                lane,
                source,
                cycle_phase,
                status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.utcnow().isoformat(),
                outcome_data.get("symbol", ""),
                outcome_data.get("mint"),
                outcome_data.get("entry_price"),
                outcome_data.get("score"),
                outcome_data.get("regime_score"),
                outcome_data.get("regime_label"),
                outcome_data.get("confidence"),
                outcome_data.get("lane"),
                outcome_data.get("source"),
                outcome_data.get("cycle_phase"),
                "PENDING",
            ),
        )


def get_pending_alert_outcomes(limit: int = 50):
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT *
            FROM alert_outcomes
            WHERE status != 'COMPLETE'
            ORDER BY created_ts_utc ASC
            LIMIT ?
            """,
            (limit,),
        )
        return [dict(r) for r in cur.fetchall()]


def update_alert_outcome_horizon(outcome_id: int, horizon_hours: int, return_pct: float):
    now_iso = datetime.utcnow().isoformat()
    if horizon_hours == 1:
        ts_col, ret_col = "evaluated_1h_ts_utc", "return_1h_pct"
    elif horizon_hours == 4:
        ts_col, ret_col = "evaluated_4h_ts_utc", "return_4h_pct"
    elif horizon_hours == 24:
        ts_col, ret_col = "evaluated_24h_ts_utc", "return_24h_pct"
    else:
        return

    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            f"""
            UPDATE alert_outcomes
            SET {ts_col} = ?, {ret_col} = ?, last_error = NULL
            WHERE id = ?
            """,
            (now_iso, return_pct, outcome_id),
        )
        cur.execute(
            """
            UPDATE alert_outcomes
            SET status = 'COMPLETE'
            WHERE id = ?
            AND return_1h_pct IS NOT NULL
            AND return_4h_pct IS NOT NULL
            AND return_24h_pct IS NOT NULL
            """,
            (outcome_id,),
        )


def mark_alert_outcome_error(outcome_id: int, error: str):
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE alert_outcomes
            SET last_error = ?
            WHERE id = ?
            """,
            (error[:400], outcome_id),
        )


def mark_alert_outcome_complete(outcome_id: int):
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE alert_outcomes
            SET status = 'COMPLETE'
            WHERE id = ?
            """,
            (outcome_id,),
        )
def liquidity_non_decreasing(symbol: str, lookback_hours: int = 72) -> bool:
    """
    Returns True if liquidity has not meaningfully decreased
    over the lookback window based on historical signal logs.
    """

    cutoff = datetime.utcnow() - timedelta(hours=lookback_hours)

    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT liquidity_usd, ts_utc
            FROM signals
            WHERE symbol = ?
            AND ts_utc >= ?
            ORDER BY ts_utc ASC
        """, (symbol, cutoff.isoformat()))

        rows = cur.fetchall()

    if len(rows) < 2:
        # Not enough history → assume stable
        return True

    first = rows[0]["liquidity_usd"]
    last = rows[-1]["liquidity_usd"]

    if not first or not last:
        return True

    # If liquidity dropped more than 15% → unstable
    if last < first * 0.85:
        return False

    return True


def get_performance_summary(lookback_hours: int = 24) -> dict:
    """
    Aggregate engine performance metrics from the signals table.
    """
    cutoff = datetime.utcnow() - timedelta(hours=lookback_hours)
    cutoff_iso = cutoff.isoformat()

    with get_conn() as conn:
        cur = conn.cursor()

        cur.execute(
            """
            SELECT COUNT(*) AS c
            FROM signals
            WHERE decision = 'SCAN_BEST' AND ts_utc >= ?
            """,
            (cutoff_iso,),
        )
        scans = cur.fetchone()["c"] or 0

        cur.execute(
            """
            SELECT COUNT(*) AS c
            FROM signals
            WHERE decision IN ('ALERT', 'ALERT_DRY_RUN') AND ts_utc >= ?
            """,
            (cutoff_iso,),
        )
        alerts = cur.fetchone()["c"] or 0

        cur.execute(
            """
            SELECT
                COALESCE(AVG(score_total), 0) AS avg_score,
                COALESCE(MAX(score_total), 0) AS max_score
            FROM signals
            WHERE decision = 'SCAN_BEST' AND ts_utc >= ?
            """,
            (cutoff_iso,),
        )
        score_row = cur.fetchone()
        avg_score = float(score_row["avg_score"] or 0)
        max_score = float(score_row["max_score"] or 0)

        cur.execute(
            """
            SELECT symbol, COUNT(*) AS alerts
            FROM signals
            WHERE decision IN ('ALERT', 'ALERT_DRY_RUN') AND ts_utc >= ?
            GROUP BY symbol
            ORDER BY alerts DESC
            LIMIT 3
            """,
            (cutoff_iso,),
        )
        top_alert_symbols = [dict(row) for row in cur.fetchall()]

    alert_rate = (alerts / scans * 100.0) if scans else 0.0
    return {
        "lookback_hours": lookback_hours,
        "scans": scans,
        "alerts": alerts,
        "alert_rate": alert_rate,
        "avg_score": avg_score,
        "max_score": max_score,
        "top_alert_symbols": top_alert_symbols,
    }


def get_latest_decision_timestamp(decisions: list[str]):
    """
    Return latest ts_utc for any of the given decisions, else None.
    """
    if not decisions:
        return None

    placeholders = ",".join("?" for _ in decisions)
    query = f"""
        SELECT ts_utc
        FROM signals
        WHERE decision IN ({placeholders})
        ORDER BY ts_utc DESC
        LIMIT 1
    """
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(query, tuple(decisions))
        row = cur.fetchone()
    if not row:
        return None
    ts = row["ts_utc"]
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        return None


def get_last_alert_timestamp(symbol: str):
    """
    Return latest alert ts for a symbol, else None.
    """
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT ts_utc
            FROM signals
            WHERE symbol = ?
              AND decision IN ('ALERT', 'ALERT_DRY_RUN')
            ORDER BY ts_utc DESC
            LIMIT 1
            """,
            (symbol,),
        )
        row = cur.fetchone()
    if not row or not row["ts_utc"]:
        return None
    try:
        return datetime.fromisoformat(row["ts_utc"])
    except ValueError:
        return None


def get_last_decision_timestamp_for_symbol(symbol: str, decisions: list[str]):
    """
    Return latest timestamp for given symbol and decision set, else None.
    """
    if not symbol or not decisions:
        return None

    placeholders = ",".join("?" for _ in decisions)
    query = f"""
        SELECT ts_utc
        FROM signals
        WHERE symbol = ?
          AND decision IN ({placeholders})
        ORDER BY ts_utc DESC
        LIMIT 1
    """
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(query, tuple([symbol] + decisions))
        row = cur.fetchone()

    if not row or not row["ts_utc"]:
        return None
    try:
        return datetime.fromisoformat(row["ts_utc"])
    except ValueError:
        return None


def get_engine_health_snapshot() -> dict:
    """
    Fetch latest scan-run and alert timestamps for watchdog checks.
    """
    last_scan_run = get_latest_decision_timestamp(["SCAN_RUN"])
    last_alert = get_latest_decision_timestamp(["ALERT", "ALERT_DRY_RUN"])
    return {
        "last_scan_run": last_scan_run,
        "last_alert": last_alert,
    }


def get_latest_engine_event() -> dict | None:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT ts_utc, decision, notes, regime_score, regime_label
            FROM signals
            WHERE symbol = 'ENGINE'
            ORDER BY ts_utc DESC
            LIMIT 1
            """
        )
        row = cur.fetchone()
    if not row:
        return None
    return dict(row)


def get_recent_scan_bests(lookback_hours: int = 6, limit: int = 25) -> list[dict]:
    cutoff = datetime.utcnow() - timedelta(hours=max(1, lookback_hours))
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT ts_utc, symbol, mint, score_total, regime_label, regime_score, change_24h, liquidity_usd, volume_24h
            FROM signals
            WHERE decision = 'SCAN_BEST'
              AND ts_utc >= ?
            ORDER BY ts_utc DESC
            LIMIT ?
            """,
            (cutoff.isoformat(), max(1, limit)),
        )
        return [dict(r) for r in cur.fetchall()]


def get_outcome_queue_stats() -> dict:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) AS c FROM alert_outcomes")
        total = int(cur.fetchone()["c"] or 0)
        cur.execute("SELECT COUNT(*) AS c FROM alert_outcomes WHERE status = 'PENDING'")
        pending = int(cur.fetchone()["c"] or 0)
        cur.execute("SELECT COUNT(*) AS c FROM alert_outcomes WHERE status = 'COMPLETE'")
        complete = int(cur.fetchone()["c"] or 0)
    return {"total": total, "pending": pending, "complete": complete}


def get_alert_outcome_recap(lookback_hours: int = 24, limit: int = 8) -> list[dict]:
    cutoff = (datetime.utcnow() - timedelta(hours=max(1, int(lookback_hours)))).isoformat()
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT
                symbol,
                COUNT(*) AS alerts,
                AVG(return_1h_pct) AS avg_1h,
                AVG(return_4h_pct) AS avg_4h,
                AVG(return_24h_pct) AS avg_24h,
                SUM(CASE WHEN return_4h_pct > 0 THEN 1 ELSE 0 END) AS wins_4h,
                SUM(CASE WHEN return_4h_pct IS NOT NULL THEN 1 ELSE 0 END) AS n_4h
            FROM alert_outcomes
            WHERE created_ts_utc >= ?
            GROUP BY symbol
            ORDER BY alerts DESC, COALESCE(avg_4h, -9999) DESC
            LIMIT ?
            """,
            (cutoff, max(1, int(limit))),
        )
        rows = [dict(r) for r in cur.fetchall()]
    return rows


def get_symbol_controls_summary() -> dict:
    now_iso = datetime.utcnow().isoformat()
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT COUNT(*) AS c
            FROM symbol_controls
            WHERE blacklist_until_utc IS NOT NULL
              AND blacklist_until_utc > ?
            """,
            (now_iso,),
        )
        blacklisted = int(cur.fetchone()["c"] or 0)
        cur.execute(
            """
            SELECT COUNT(*) AS c
            FROM symbol_controls
            WHERE cooldown_until_utc IS NOT NULL
              AND cooldown_until_utc > ?
            """,
            (now_iso,),
        )
        cooldown = int(cur.fetchone()["c"] or 0)
    return {"blacklisted": blacklisted, "cooldown": cooldown}


def _parse_iso(ts):
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        return None


def count_alerts_since(cutoff_ts_utc: datetime, symbol: str | None = None) -> int:
    with get_conn() as conn:
        cur = conn.cursor()
        if symbol:
            cur.execute(
                """
                SELECT COUNT(*) AS c
                FROM signals
                WHERE decision IN ('ALERT', 'ALERT_DRY_RUN')
                  AND symbol = ?
                  AND ts_utc >= ?
                """,
                (symbol, cutoff_ts_utc.isoformat()),
            )
        else:
            cur.execute(
                """
                SELECT COUNT(*) AS c
                FROM signals
                WHERE decision IN ('ALERT', 'ALERT_DRY_RUN')
                  AND ts_utc >= ?
                """,
                (cutoff_ts_utc.isoformat(),),
            )
        return int(cur.fetchone()["c"] or 0)


def get_consecutive_losing_outcomes_4h(limit: int = 50) -> int:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT return_4h_pct
            FROM alert_outcomes
            WHERE return_4h_pct IS NOT NULL
            ORDER BY evaluated_4h_ts_utc DESC
            LIMIT ?
            """,
            (limit,),
        )
        rows = [float(r["return_4h_pct"]) for r in cur.fetchall()]

    streak = 0
    for ret in rows:
        if ret < 0:
            streak += 1
        else:
            break
    return streak


def get_risk_mode() -> dict:
    """
    Returns the current risk mode based on consecutive losing 4h outcomes.
    Modes:
      NORMAL     — 0-1 losses   → full sizing, no threshold adjustment
      CAUTIOUS   — 2 losses     → -50% size, threshold +5
      DEFENSIVE  — 3+ losses    → -70% size, threshold +10, tighten to A-only
    Also returns threshold_delta and size_multiplier for use by engine/sizer.
    """
    streak = get_consecutive_losing_outcomes_4h(limit=50)
    pause_state = get_risk_pause_state()
    is_paused = bool(
        pause_state.get("pause_until") and
        pause_state["pause_until"] > datetime.utcnow()
    )

    if is_paused or streak >= 3:
        return {
            "mode": "DEFENSIVE",
            "emoji": "🔴",
            "streak": streak,
            "threshold_delta": 10,
            "size_multiplier": 0.30,
            "min_confidence": "A",
            "paused": is_paused,
        }
    if streak == 2:
        return {
            "mode": "CAUTIOUS",
            "emoji": "🟡",
            "streak": streak,
            "threshold_delta": 5,
            "size_multiplier": 0.50,
            "min_confidence": "B",
            "paused": False,
        }
    return {
        "mode": "NORMAL",
        "emoji": "🟢",
        "streak": streak,
        "threshold_delta": 0,
        "size_multiplier": 1.0,
        "min_confidence": None,
        "paused": False,
    }


def get_latest_4h_outcome_timestamp():
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT evaluated_4h_ts_utc
            FROM alert_outcomes
            WHERE return_4h_pct IS NOT NULL
            ORDER BY evaluated_4h_ts_utc DESC
            LIMIT 1
            """
        )
        row = cur.fetchone()
    if not row:
        return None
    return _parse_iso(row["evaluated_4h_ts_utc"])


def get_risk_pause_state() -> dict:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT pause_until_utc, reason, updated_ts_utc FROM risk_state WHERE id = 1")
        row = cur.fetchone()
    if not row:
        return {"pause_until": None, "reason": None, "updated_ts": None}
    return {
        "pause_until": _parse_iso(row["pause_until_utc"]),
        "reason": row["reason"],
        "updated_ts": _parse_iso(row["updated_ts_utc"]),
    }


def set_risk_pause(hours: int, reason: str):
    pause_until = datetime.utcnow() + timedelta(hours=max(0, hours))
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE risk_state
            SET pause_until_utc = ?, reason = ?, updated_ts_utc = ?
            WHERE id = 1
            """,
            (pause_until.isoformat(), reason[:400], datetime.utcnow().isoformat()),
        )


def clear_risk_pause():
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE risk_state
            SET pause_until_utc = NULL, reason = NULL, updated_ts_utc = ?
            WHERE id = 1
            """,
            (datetime.utcnow().isoformat(),),
        )


def get_active_symbol_control(symbol: str):
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT symbol, cooldown_until_utc, blacklist_until_utc, reason, updated_ts_utc
            FROM symbol_controls
            WHERE symbol = ?
            """,
            (symbol,),
        )
        row = cur.fetchone()
    if not row:
        return None

    now = datetime.utcnow()
    cooldown_until = _parse_iso(row["cooldown_until_utc"])
    blacklist_until = _parse_iso(row["blacklist_until_utc"])
    if blacklist_until and blacklist_until > now:
        return {
            "symbol": symbol,
            "type": "BLACKLIST",
            "until": blacklist_until,
            "reason": row["reason"],
        }
    if cooldown_until and cooldown_until > now:
        return {
            "symbol": symbol,
            "type": "COOLDOWN",
            "until": cooldown_until,
            "reason": row["reason"],
        }
    return None


def set_symbol_control(symbol: str, control_type: str, hours: int, reason: str):
    now = datetime.utcnow()
    until = now + timedelta(hours=max(0, hours))
    cooldown_until = until.isoformat() if control_type == "COOLDOWN" else None
    blacklist_until = until.isoformat() if control_type == "BLACKLIST" else None

    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO symbol_controls (symbol, cooldown_until_utc, blacklist_until_utc, reason, updated_ts_utc)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(symbol) DO UPDATE SET
              cooldown_until_utc = COALESCE(excluded.cooldown_until_utc, symbol_controls.cooldown_until_utc),
              blacklist_until_utc = COALESCE(excluded.blacklist_until_utc, symbol_controls.blacklist_until_utc),
              reason = excluded.reason,
              updated_ts_utc = excluded.updated_ts_utc
            """,
            (symbol, cooldown_until, blacklist_until, reason[:400], now.isoformat()),
        )


def get_symbol_outcome_stats(symbol: str, lookback_days: int = 30) -> dict:
    cutoff = (datetime.utcnow() - timedelta(days=lookback_days)).isoformat()
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT return_4h_pct
            FROM alert_outcomes
            WHERE symbol = ?
              AND created_ts_utc >= ?
              AND return_4h_pct IS NOT NULL
            ORDER BY evaluated_4h_ts_utc DESC
            LIMIT 20
            """,
            (symbol, cutoff),
        )
        r4 = [float(r["return_4h_pct"]) for r in cur.fetchall()]

        cur.execute(
            """
            SELECT return_24h_pct
            FROM alert_outcomes
            WHERE symbol = ?
              AND created_ts_utc >= ?
              AND return_24h_pct IS NOT NULL
            ORDER BY evaluated_24h_ts_utc DESC
            LIMIT 20
            """,
            (symbol, cutoff),
        )
        r24 = [float(r["return_24h_pct"]) for r in cur.fetchall()]

    return {
        "returns_4h": r4,
        "returns_24h": r24,
        "avg_24h": (sum(r24) / len(r24)) if r24 else 0.0,
    }


def get_portfolio_simulation_metrics(lookback_days: int = 30, horizon_hours: int = 4) -> dict:
    ret_col = {1: "return_1h_pct", 4: "return_4h_pct", 24: "return_24h_pct"}.get(horizon_hours, "return_4h_pct")
    ts_col = {1: "evaluated_1h_ts_utc", 4: "evaluated_4h_ts_utc", 24: "evaluated_24h_ts_utc"}.get(
        horizon_hours,
        "evaluated_4h_ts_utc",
    )
    cutoff = (datetime.utcnow() - timedelta(days=lookback_days)).isoformat()
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            f"""
            SELECT symbol, {ret_col} AS ret, {ts_col} AS ts
            FROM alert_outcomes
            WHERE {ret_col} IS NOT NULL
              AND {ts_col} IS NOT NULL
              AND created_ts_utc >= ?
            ORDER BY {ts_col} ASC
            """,
            (cutoff,),
        )
        rows = [dict(r) for r in cur.fetchall()]

    returns = [float(r["ret"]) for r in rows]
    if not returns:
        return {
            "lookback_days": lookback_days,
            "horizon_hours": horizon_hours,
            "trades": 0,
            "avg_return_pct": 0.0,
            "median_return_pct": 0.0,
            "win_rate_pct": 0.0,
            "payoff_ratio": 0.0,
            "expectancy_pct": 0.0,
            "max_drawdown_pct": 0.0,
            "equity_end": 1.0,
        }

    wins = [r for r in returns if r > 0]
    losses = [r for r in returns if r < 0]
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    win_rate = len(wins) / len(returns)
    payoff_ratio = (avg_win / abs(avg_loss)) if avg_loss < 0 else 0.0
    expectancy = (win_rate * avg_win) + ((1 - win_rate) * avg_loss)

    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    for ret in returns:
        equity *= 1.0 + (ret / 100.0)
        if equity > peak:
            peak = equity
        dd = (peak - equity) / peak if peak else 0.0
        if dd > max_dd:
            max_dd = dd

    sorted_returns = sorted(returns)
    med = sorted_returns[len(sorted_returns) // 2] if len(sorted_returns) % 2 == 1 else (
        (sorted_returns[len(sorted_returns) // 2 - 1] + sorted_returns[len(sorted_returns) // 2]) / 2
    )
    return {
        "lookback_days": lookback_days,
        "horizon_hours": horizon_hours,
        "trades": len(returns),
        "avg_return_pct": sum(returns) / len(returns),
        "median_return_pct": med,
        "win_rate_pct": win_rate * 100.0,
        "payoff_ratio": payoff_ratio,
        "expectancy_pct": expectancy,
        "max_drawdown_pct": max_dd * 100.0,
        "equity_end": equity,
    }


def optimize_thresholds_from_outcomes(
    lookback_days: int,
    min_outcomes_4h: int = 8,
) -> dict | None:
    cutoff = (datetime.utcnow() - timedelta(days=lookback_days)).isoformat()
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT score, regime_score, confidence, return_4h_pct, created_ts_utc
            FROM alert_outcomes
            WHERE return_4h_pct IS NOT NULL
              AND score IS NOT NULL
              AND regime_score IS NOT NULL
              AND confidence IS NOT NULL
              AND created_ts_utc >= ?
            ORDER BY created_ts_utc ASC
            """,
            (cutoff,),
        )
        rows = [dict(r) for r in cur.fetchall()]

    if len(rows) < min_outcomes_4h:
        return None

    rank = {"C": 1, "B": 2, "A": 3}
    best = None
    conf_options = ["C", "B", "A"]
    for threshold in range(55, 100, 5):
        for regime_min in range(35, 75, 5):
            for conf in conf_options:
                conf_min = rank[conf]
                subset = [
                    float(r["return_4h_pct"])
                    for r in rows
                    if float(r["score"]) >= threshold
                    and float(r["regime_score"]) >= regime_min
                    and rank.get(str(r["confidence"]).upper(), 0) >= conf_min
                ]
                n = len(subset)
                if n < max(5, min_outcomes_4h // 2):
                    continue
                avg_ret = sum(subset) / n
                wins = [x for x in subset if x > 0]
                win_rate = (len(wins) / n) * 100.0
                equity = 1.0
                peak = 1.0
                max_dd = 0.0
                for ret in subset:
                    equity *= 1.0 + (ret / 100.0)
                    if equity > peak:
                        peak = equity
                    dd = (peak - equity) / peak if peak else 0.0
                    max_dd = max(max_dd, dd)
                score_obj = (avg_ret * (n ** 0.5)) + ((win_rate - 50.0) * 0.05) - (max_dd * 100.0 * 0.2)
                candidate = {
                    "alert_threshold": threshold,
                    "regime_min_score": regime_min,
                    "min_confidence_to_alert": conf,
                    "samples": n,
                    "avg_return_4h_pct": avg_ret,
                    "win_rate_4h_pct": win_rate,
                    "max_drawdown_pct": max_dd * 100.0,
                    "objective": score_obj,
                }
                if not best or candidate["objective"] > best["objective"]:
                    best = candidate
    return best


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    if pct <= 0:
        return values[0]
    if pct >= 100:
        return values[-1]
    idx = int(round((pct / 100.0) * (len(values) - 1)))
    return values[idx]


def get_weekly_tuning_report(
    lookback_days: int,
    current_alert_threshold: int,
    current_regime_min_score: int,
    current_min_confidence_to_alert: str,
    min_outcomes_4h: int = 8,
) -> dict:
    """
    Analyze recent outcomes and provide config tuning recommendations.
    """
    cutoff = datetime.utcnow() - timedelta(days=lookback_days)
    cutoff_iso = cutoff.isoformat()

    with get_conn() as conn:
        cur = conn.cursor()

        cur.execute(
            """
            SELECT COUNT(*) AS c
            FROM signals
            WHERE decision = 'SCAN_RUN' AND ts_utc >= ?
            """,
            (cutoff_iso,),
        )
        scan_runs = int(cur.fetchone()["c"] or 0)

        cur.execute(
            """
            SELECT COUNT(*) AS c
            FROM signals
            WHERE decision = 'SCAN_BEST' AND ts_utc >= ?
            """,
            (cutoff_iso,),
        )
        scan_best = int(cur.fetchone()["c"] or 0)

        cur.execute(
            """
            SELECT COUNT(*) AS c
            FROM signals
            WHERE decision IN ('ALERT', 'ALERT_DRY_RUN') AND ts_utc >= ?
            """,
            (cutoff_iso,),
        )
        alerts = int(cur.fetchone()["c"] or 0)

        cur.execute(
            """
            SELECT COUNT(*) AS c
            FROM signals
            WHERE decision = 'REGIME_BLOCK' AND ts_utc >= ?
            """,
            (cutoff_iso,),
        )
        regime_blocks = int(cur.fetchone()["c"] or 0)

        cur.execute(
            """
            SELECT score_total
            FROM signals
            WHERE decision = 'SCAN_BEST' AND score_total IS NOT NULL AND ts_utc >= ?
            ORDER BY score_total ASC
            """,
            (cutoff_iso,),
        )
        score_values = [float(row["score_total"]) for row in cur.fetchall()]

        cur.execute(
            """
            SELECT regime_score
            FROM signals
            WHERE decision = 'REGIME_BLOCK' AND regime_score IS NOT NULL AND ts_utc >= ?
            ORDER BY regime_score ASC
            """,
            (cutoff_iso,),
        )
        blocked_regime_scores = [float(row["regime_score"]) for row in cur.fetchall()]

        cur.execute(
            """
            SELECT
                COUNT(return_1h_pct) AS n1,
                COALESCE(AVG(return_1h_pct), 0) AS avg1,
                COALESCE(SUM(CASE WHEN return_1h_pct > 0 THEN 1 ELSE 0 END), 0) AS w1,
                COUNT(return_4h_pct) AS n4,
                COALESCE(AVG(return_4h_pct), 0) AS avg4,
                COALESCE(SUM(CASE WHEN return_4h_pct > 0 THEN 1 ELSE 0 END), 0) AS w4,
                COUNT(return_24h_pct) AS n24,
                COALESCE(AVG(return_24h_pct), 0) AS avg24,
                COALESCE(SUM(CASE WHEN return_24h_pct > 0 THEN 1 ELSE 0 END), 0) AS w24
            FROM alert_outcomes
            WHERE created_ts_utc >= ?
            """,
            (cutoff_iso,),
        )
        out_row = cur.fetchone()

    alert_rate = (alerts / scan_runs * 100.0) if scan_runs else 0.0
    block_rate = (regime_blocks / scan_runs * 100.0) if scan_runs else 0.0
    p50_score = median(score_values) if score_values else 0.0
    p75_score = _percentile(score_values, 75)
    p90_score = _percentile(score_values, 90)
    median_blocked_regime = median(blocked_regime_scores) if blocked_regime_scores else 0.0
    outcomes_1h_count = int(out_row["n1"] or 0)
    outcomes_4h_count = int(out_row["n4"] or 0)
    outcomes_24h_count = int(out_row["n24"] or 0)
    avg_return_1h = float(out_row["avg1"] or 0)
    avg_return_4h = float(out_row["avg4"] or 0)
    avg_return_24h = float(out_row["avg24"] or 0)
    winrate_1h = (float(out_row["w1"] or 0) / outcomes_1h_count * 100.0) if outcomes_1h_count else 0.0
    winrate_4h = (float(out_row["w4"] or 0) / outcomes_4h_count * 100.0) if outcomes_4h_count else 0.0
    winrate_24h = (float(out_row["w24"] or 0) / outcomes_24h_count * 100.0) if outcomes_24h_count else 0.0
    portfolio_4h = get_portfolio_simulation_metrics(lookback_days=lookback_days, horizon_hours=4)

    rec_alert_threshold = current_alert_threshold
    rec_regime_min_score = current_regime_min_score
    rec_confidence = current_min_confidence_to_alert
    reasons = []

    # Outcome-first tuning (4h is primary signal quality window).
    optimizer = None
    if outcomes_4h_count >= min_outcomes_4h:
        optimizer = optimize_thresholds_from_outcomes(
            lookback_days=lookback_days,
            min_outcomes_4h=min_outcomes_4h,
        )

    if optimizer:
        rec_alert_threshold = int(optimizer["alert_threshold"])
        rec_regime_min_score = int(optimizer["regime_min_score"])
        rec_confidence = str(optimizer["min_confidence_to_alert"])
        reasons.append(
            "Optimizer selected params from realized 4h outcomes "
            f"(n={optimizer['samples']}, avg={optimizer['avg_return_4h_pct']:.2f}%)."
        )
    elif outcomes_4h_count >= min_outcomes_4h:
        if avg_return_4h < -1.5 or winrate_4h < 42:
            rec_alert_threshold = min(95, current_alert_threshold + 5)
            rec_confidence = "A"
            rec_regime_min_score = min(70, current_regime_min_score + 3)
            reasons.append("4h outcomes are weak; tightened threshold/confidence/regime.")
        elif avg_return_4h > 3.0 and winrate_4h >= 58:
            rec_alert_threshold = max(55, current_alert_threshold - 3)
            if current_min_confidence_to_alert == "A":
                rec_confidence = "B"
            reasons.append("4h outcomes are strong; slightly loosened gate to scale opportunities.")
        else:
            reasons.append("4h outcome edge is neutral; kept risk posture mostly unchanged.")
    elif scan_runs >= 10:
        # Fallback when outcome history is still sparse.
        if alert_rate < 3:
            rec_alert_threshold = max(55, current_alert_threshold - 5)
            if current_min_confidence_to_alert == "A":
                rec_confidence = "B"
            reasons.append("Outcome sample sparse and alert rate low; loosened gate modestly.")
        elif alert_rate > 25:
            rec_alert_threshold = min(95, current_alert_threshold + 5)
            rec_confidence = "A"
            reasons.append("Outcome sample sparse and alert rate high; tightened gate.")

    if p75_score > 0 and current_alert_threshold < p75_score - 15:
        rec_alert_threshold = max(rec_alert_threshold, int(round(p75_score - 10)))
        reasons.append("Score distribution supports a higher threshold near upper quartile.")
    elif p75_score > 0 and current_alert_threshold > p90_score + 5:
        rec_alert_threshold = min(rec_alert_threshold, int(round(max(55, p90_score - 3))))
        reasons.append("Current threshold appears above high-score cluster; easing slightly.")

    if block_rate > 80 and median_blocked_regime > 0:
        rec_regime_min_score = max(35, int(round(median_blocked_regime + 2)))
        reasons.append("Regime gate blocks most scans; moved gate closer to observed regime.")
    elif block_rate < 20 and alert_rate > 18:
        rec_regime_min_score = min(70, current_regime_min_score + 5)
        reasons.append("Low regime blocking with high alert rate; tightened regime gate.")

    if not reasons:
        reasons.append("No strong drift detected; keep current parameters.")

    return {
        "lookback_days": lookback_days,
        "scan_runs": scan_runs,
        "scan_best": scan_best,
        "alerts": alerts,
        "alert_rate": alert_rate,
        "regime_blocks": regime_blocks,
        "block_rate": block_rate,
        "p50_score": p50_score,
        "p75_score": p75_score,
        "p90_score": p90_score,
        "median_blocked_regime": median_blocked_regime,
        "outcomes_1h_count": outcomes_1h_count,
        "outcomes_4h_count": outcomes_4h_count,
        "outcomes_24h_count": outcomes_24h_count,
        "avg_return_1h": avg_return_1h,
        "avg_return_4h": avg_return_4h,
        "avg_return_24h": avg_return_24h,
        "winrate_1h": winrate_1h,
        "winrate_4h": winrate_4h,
        "winrate_24h": winrate_24h,
        "portfolio_4h": portfolio_4h,
        "optimizer": optimizer,
        "current": {
            "alert_threshold": current_alert_threshold,
            "regime_min_score": current_regime_min_score,
            "min_confidence_to_alert": current_min_confidence_to_alert,
        },
        "recommended": {
            "alert_threshold": rec_alert_threshold,
            "regime_min_score": rec_regime_min_score,
            "min_confidence_to_alert": rec_confidence,
        },
        "reasons": reasons,
    }


def get_lane_win_rates(lookback_days: int = 30, min_n: int = 5) -> dict:
    """
    Compute win rates for each alert lane and data source.
    Applies staleness decay: outcomes > 30 days old are weighted 80% vs 100%.

    Returns:
    {
      "lanes": [
        {"lane": str, "count": int, "win_rate_4h": float, "avg_return_4h": float},
        ...
      ],
      "by_source": [
        {"source": str, "count": int, "win_rate_4h": float, "avg_return_4h": float},
        ...
      ],
      "total_tagged": int,    # outcomes with a non-NULL lane
      "total_outcomes": int,  # all completed outcomes in window
      "lookback_days": int,
    }
    """
    cutoff_iso = (datetime.utcnow() - timedelta(days=lookback_days)).isoformat()
    staleness_cutoff = (datetime.utcnow() - timedelta(days=30)).isoformat()

    with get_conn() as conn:
        cur = conn.cursor()

        # All completed outcomes in the window — include ts for staleness decay
        cur.execute(
            """
            SELECT lane, source, return_1h_pct, return_4h_pct, created_ts_utc
            FROM alert_outcomes
            WHERE status = 'COMPLETE'
              AND return_4h_pct IS NOT NULL
              AND created_ts_utc >= ?
            """,
            (cutoff_iso,),
        )
        rows = [dict(r) for r in cur.fetchall()]

    total_outcomes = len(rows)
    total_tagged   = sum(1 for r in rows if r.get("lane"))

    def _weight(row: dict) -> float:
        """80% weight for outcomes older than 30 days (staleness decay)."""
        ts = row.get("created_ts_utc", "")
        return 0.80 if ts < staleness_cutoff else 1.0

    def _agg(subset: list) -> dict | None:
        if len(subset) < min_n:
            return None
        weighted_wins = 0.0
        weighted_total = 0.0
        weighted_sum_4h = 0.0
        for r in subset:
            r4h = r.get("return_4h_pct")
            if r4h is None:
                continue
            w = _weight(r)
            weighted_total += w
            weighted_sum_4h += float(r4h) * w
            if float(r4h) > 0:
                weighted_wins += w
        if weighted_total < 1e-6:
            return None
        return {
            "count":       len(subset),
            "win_rate_4h": round(weighted_wins / weighted_total * 100, 1),
            "avg_return_4h": round(weighted_sum_4h / weighted_total, 2),
        }

    # Group by lane
    lanes_raw: dict[str, list] = {}
    for r in rows:
        key = r.get("lane") or "unknown"
        lanes_raw.setdefault(key, []).append(r)

    # Group by source
    sources_raw: dict[str, list] = {}
    for r in rows:
        key = r.get("source") or "unknown"
        sources_raw.setdefault(key, []).append(r)

    lanes_list = []
    for k, v in sorted(lanes_raw.items()):
        agg = _agg(v)
        if agg:
            lanes_list.append({"lane": k, **agg})
    lanes_list.sort(key=lambda x: x["win_rate_4h"], reverse=True)

    sources_list = []
    for k, v in sorted(sources_raw.items()):
        agg = _agg(v)
        if agg:
            sources_list.append({"source": k, **agg})
    sources_list.sort(key=lambda x: x["win_rate_4h"], reverse=True)

    return {
        "lanes":          lanes_list,
        "by_source":      sources_list,
        "total_tagged":   total_tagged,
        "total_outcomes": total_outcomes,
        "lookback_days":  lookback_days,
    }


def get_score_breakdown_stats(lookback_days: int = 60, min_n: int = 10) -> dict:
    """
    Analyze which score breakdown components correlate with winning 4h outcomes.
    Reads score_breakdown JSON from signals table (populated at ALERT time).

    Returns per-component correlation stats and winning keyword analysis.
    """
    import json as _json

    cutoff_iso = (datetime.utcnow() - timedelta(days=lookback_days)).isoformat()

    with get_conn() as conn:
        cur = conn.cursor()

        # Join ALERT signals with their outcome returns via symbol + time proximity
        cur.execute(
            """
            SELECT
                s.score_breakdown,
                s.notes,
                ao.return_4h_pct,
                ao.return_1h_pct
            FROM signals s
            JOIN alert_outcomes ao
              ON s.symbol = ao.symbol
             AND ao.created_ts_utc >= s.ts_utc
             AND ao.created_ts_utc <= datetime(s.ts_utc, '+300 seconds')
            WHERE s.decision IN ('ALERT', 'ALERT_DRY_RUN')
              AND s.score_breakdown IS NOT NULL
              AND ao.status = 'COMPLETE'
              AND ao.return_4h_pct IS NOT NULL
              AND s.ts_utc >= ?
            ORDER BY s.ts_utc DESC
            """,
            (cutoff_iso,),
        )
        rows = [dict(r) for r in cur.fetchall()]

    if len(rows) < min_n:
        return {"insufficient_data": True, "n": len(rows), "min_n": min_n}

    # Parse breakdowns
    component_data: dict[str, list[tuple[float, float]]] = {}  # component -> [(component_score, outcome)]
    for row in rows:
        try:
            breakdown = _json.loads(row["score_breakdown"] or "{}")
        except Exception:
            continue
        ret4h = row.get("return_4h_pct")
        if ret4h is None:
            continue
        for component, comp_score in breakdown.items():
            if isinstance(comp_score, (int, float)):
                component_data.setdefault(component, []).append((float(comp_score), float(ret4h)))

    # Compute correlation for each component (Pearson)
    def _pearson(pairs: list[tuple[float, float]]) -> float:
        if len(pairs) < 5:
            return 0.0
        xs = [p[0] for p in pairs]
        ys = [p[1] for p in pairs]
        n  = len(xs)
        mx = sum(xs) / n
        my = sum(ys) / n
        num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
        denom = (sum((x - mx) ** 2 for x in xs) * sum((y - my) ** 2 for y in ys)) ** 0.5
        return round(num / denom, 3) if denom > 1e-9 else 0.0

    correlations = {}
    for comp, pairs in component_data.items():
        corr = _pearson(pairs)
        wins = sum(1 for _, y in pairs if y > 0)
        correlations[comp] = {
            "correlation_4h": corr,
            "n": len(pairs),
            "avg_score": round(sum(p[0] for p in pairs) / len(pairs), 2),
            "win_rate_when_high": round(  # win rate when component score is above-median
                sum(1 for s, y in pairs if s > sum(p[0] for p in pairs) / len(pairs) and y > 0)
                / max(1, sum(1 for s, _ in pairs if s > sum(p[0] for p in pairs) / len(pairs))),
                3
            ),
        }

    # Sort by absolute correlation — most predictive first
    sorted_corr = dict(sorted(correlations.items(), key=lambda x: abs(x[1]["correlation_4h"]), reverse=True))

    return {
        "n": len(rows),
        "component_correlations": sorted_corr,
        "lookback_days": lookback_days,
    }
