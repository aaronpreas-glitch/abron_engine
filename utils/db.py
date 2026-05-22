from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from statistics import median

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data_storage", "engine.db")
DB_TIMEOUT_SECONDS = max(5.0, float(os.getenv("SQLITE_TIMEOUT_SECONDS", "20")))
DB_BUSY_TIMEOUT_MS = max(5000, int(os.getenv("SQLITE_BUSY_TIMEOUT_MS", str(int(DB_TIMEOUT_SECONDS * 1000)))))
DB_PROCESS_SERIALIZE = os.getenv("SQLITE_PROCESS_SERIALIZE", "true").lower() in ("1", "true", "yes")
_DB_PROCESS_LOCK = threading.RLock()


def parse_utc_ts(value: object) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    for candidate in (raw, raw.replace("Z", "+00:00")):
        try:
            parsed = datetime.fromisoformat(candidate)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except Exception:
            pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
        except Exception:
            pass
    return None


def utc_iso(value: object | None = None) -> str:
    parsed = parse_utc_ts(value) if value is not None else None
    return (parsed or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()


def age_seconds_utc(value: object, *, now: datetime | None = None) -> float | None:
    parsed = parse_utc_ts(value)
    if parsed is None:
        return None
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return max(0.0, (current - parsed.astimezone(timezone.utc)).total_seconds())


@contextmanager
def db_process_lock():
    """Serialize SQLite access inside one Python process.

    SQLite WAL handles readers well, but this engine runs many async loops that
    hop into threads and write concurrently. A process-local gate removes most
    self-inflicted writer storms before SQLite's cross-process busy timeout is
    involved.
    """
    if not DB_PROCESS_SERIALIZE:
        yield
        return
    with _DB_PROCESS_LOCK:
        yield


@contextmanager
def get_conn():
    with db_process_lock():
        conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS)  # Patch 163: timeout for WAL readers
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")       # Patch 163: WAL for concurrent readers
        conn.execute("PRAGMA synchronous=NORMAL")     # Keep WAL durable without over-serializing writers
        conn.execute(f"PRAGMA busy_timeout={DB_BUSY_TIMEOUT_MS}")      # Patch 163: wait on lock
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()


def is_database_locked_error(exc: BaseException) -> bool:
    return "database is locked" in str(exc).lower() or "database table is locked" in str(exc).lower()


def with_db_retry(fn, *, retries: int = 3, base_sleep_s: float = 0.2):
    """
    Run a small DB operation with bounded retry on SQLite writer lock pressure.

    This is intentionally tiny and synchronous so high-frequency loops can fail
    soft instead of surfacing transient lock errors to the dashboard.
    """
    attempts = max(1, int(retries))
    for attempt in range(attempts):
        try:
            return fn()
        except sqlite3.OperationalError as exc:
            if not is_database_locked_error(exc) or attempt >= attempts - 1:
                raise
            time.sleep(max(0.05, base_sleep_s) * (2 ** attempt))


def persistent_rate_limit_check(alert_type: str, limit_s: int) -> bool:
    """
    Persistent rate limiting backed by kv_store. Survives service restarts. Patch 164.

    Returns True  (suppress) if the alert was fired within the last `limit_s` seconds.
    Returns False (allow)    and records the current timestamp otherwise.

    Fails open on any DB error — a DB failure never silently suppresses an alert.
    """
    import time as _time
    key = f"alert_ts:{alert_type}"
    now = _time.time()
    try:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT value FROM kv_store WHERE key=?", (key,)
            ).fetchone()
            last_ts = float(row[0]) if row else 0.0
            if now - last_ts < limit_s:
                return True   # within window — suppress
            conn.execute(
                "INSERT OR REPLACE INTO kv_store (key, value) VALUES (?, ?)",
                (key, str(now)),
            )
            return False      # outside window — allow and record
    except Exception:
        return False          # fail open: never suppress due to DB error


def get_provider_status(provider: str) -> dict | None:
    clean_provider = str(provider or "").strip().lower()
    if not clean_provider:
        return None
    try:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT value FROM kv_store WHERE key=?",
                (f"provider_status:{clean_provider}",),
            ).fetchone()
        if not row or not row[0]:
            return None
        payload = json.loads(row[0])
        return payload if isinstance(payload, dict) else None
    except Exception:
        return None


def provider_in_cooldown(provider: str) -> tuple[bool, dict]:
    payload = dict(get_provider_status(provider) or {})
    if not payload:
        return False, {}
    try:
        until_raw = str(payload.get("cooldown_until") or "").strip()
        if not until_raw:
            return False, payload
        until = datetime.fromisoformat(until_raw.replace("Z", "+00:00"))
        if until.tzinfo is None:
            until = until.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) < until, payload
    except Exception:
        return False, payload


def mark_provider_degraded(
    provider: str,
    *,
    cooldown_seconds: int,
    reason: str,
    detail: str | None = None,
    status: str = "DEGRADED",
) -> dict:
    clean_provider = str(provider or "").strip().lower()
    if not clean_provider:
        return {}
    now = datetime.now(timezone.utc)
    existing = get_provider_status(clean_provider) or {}
    consecutive_failures = int(existing.get("consecutive_failures") or 0) + 1
    base_cooldown = max(60, int(cooldown_seconds))
    # Escalate repeated 429/degraded reads, but cap so recovery probes still happen.
    effective_cooldown = min(base_cooldown * (2 ** min(max(consecutive_failures - 1, 0), 4)), 3600)
    payload = {
        "provider": clean_provider,
        "status": str(status or "DEGRADED").strip().upper(),
        "updated_at": now.isoformat(),
        "last_error_at": now.isoformat(),
        "last_ok_at": existing.get("last_ok_at"),
        "reason": str(reason or "unknown_error"),
        "detail": str(detail or reason or "").strip() or None,
        "cooldown_until": (now + timedelta(seconds=effective_cooldown)).isoformat(),
        "cooldown_seconds": effective_cooldown,
        "base_cooldown_seconds": base_cooldown,
        "consecutive_failures": consecutive_failures,
        "recovery_state": "COOLDOWN",
    }
    try:
        def _write() -> None:
            with get_conn() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO kv_store (key, value) VALUES (?, ?)",
                    (
                        f"provider_status:{clean_provider}",
                        json.dumps(payload, separators=(",", ":")),
                    ),
                )

        with_db_retry(_write, retries=3, base_sleep_s=0.1)
        return payload
    except Exception:
        return {}


def _provider_active_write_due(existing: dict, *, detail: str | None, now: datetime) -> bool:
    if str(existing.get("status") or "").upper() != "ACTIVE":
        return True
    if str(existing.get("detail") or "") != (str(detail or "").strip() or ""):
        return True
    try:
        min_seconds = max(10, int(os.getenv("PROVIDER_ACTIVE_WRITE_MIN_SECONDS", "45")))
    except Exception:
        min_seconds = 45
    last_ok = parse_utc_ts(existing.get("last_ok_at") or existing.get("updated_at"))
    if last_ok is None:
        return True
    return (now - last_ok.astimezone(timezone.utc)).total_seconds() >= min_seconds


def mark_provider_active(provider: str, *, detail: str | None = None) -> dict:
    clean_provider = str(provider or "").strip().lower()
    if not clean_provider:
        return {}
    now = datetime.now(timezone.utc)
    existing = get_provider_status(clean_provider) or {}
    clean_detail = str(detail or "").strip() or None
    if not _provider_active_write_due(existing, detail=clean_detail, now=now):
        payload = dict(existing)
        payload["write_skipped"] = True
        payload["skip_reason"] = "active_status_throttled"
        return payload
    payload = {
        "provider": clean_provider,
        "status": "ACTIVE",
        "updated_at": now.isoformat(),
        "last_ok_at": now.isoformat(),
        "last_error_at": existing.get("last_error_at"),
        "reason": None,
        "detail": clean_detail,
        "cooldown_until": None,
        "cooldown_seconds": 0,
        "consecutive_failures": 0,
        "recovery_state": "RECOVERED",
    }
    try:
        def _write() -> None:
            with get_conn() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO kv_store (key, value) VALUES (?, ?)",
                    (
                        f"provider_status:{clean_provider}",
                        json.dumps(payload, separators=(",", ":")),
                    ),
                )

        with_db_retry(_write, retries=3, base_sleep_s=0.1)
        return payload
    except Exception:
        return {}


def mark_provider_disabled(
    provider: str,
    *,
    reason: str = "disabled",
    detail: str | None = None,
) -> dict:
    clean_provider = str(provider or "").strip().lower()
    if not clean_provider:
        return {}
    now = datetime.now(timezone.utc)
    existing = get_provider_status(clean_provider) or {}
    payload = {
        "provider": clean_provider,
        "status": "DISABLED",
        "updated_at": now.isoformat(),
        "last_ok_at": existing.get("last_ok_at"),
        "last_error_at": existing.get("last_error_at"),
        "reason": str(reason or "disabled"),
        "detail": str(detail or reason or "").strip() or None,
        "cooldown_until": None,
        "cooldown_seconds": 0,
        "consecutive_failures": 0,
        "recovery_state": "DISABLED",
    }
    try:
        def _write() -> None:
            with get_conn() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO kv_store (key, value) VALUES (?, ?)",
                    (
                        f"provider_status:{clean_provider}",
                        json.dumps(payload, separators=(",", ":")),
                    ),
                )

        with_db_retry(_write, retries=3, base_sleep_s=0.1)
        return payload
    except Exception:
        return {}





def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    return bool(row)


def _ensure_watchdog_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS watchdog_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts_utc TEXT NOT NULL,
            watchdog TEXT NOT NULL,
            status TEXT NOT NULL,
            detail TEXT,
            metadata_json TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_watchdog_events_watchdog_ts
        ON watchdog_events(watchdog, ts_utc)
        """
    )


def _ensure_allocation_recommendation_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS allocation_recommendations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts_utc TEXT NOT NULL,
            posture TEXT NOT NULL,
            note TEXT,
            allocatable_base_usd REAL,
            signature TEXT,
            inputs_json TEXT,
            recommendations_json TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_allocation_recommendations_ts
        ON allocation_recommendations(ts_utc)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_allocation_recommendations_signature_ts
        ON allocation_recommendations(signature, ts_utc)
        """
    )


def _ensure_wallet_reinforcement_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tracked_wallets (
            wallet_address TEXT PRIMARY KEY,
            label TEXT,
            cohort TEXT,
            enabled INTEGER NOT NULL DEFAULT 1,
            quality_score REAL,
            confidence_score REAL,
            source TEXT,
            notes TEXT,
            last_seen_utc TEXT,
            created_at_utc TEXT NOT NULL,
            updated_at_utc TEXT NOT NULL,
            metadata_json TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_tracked_wallets_enabled_quality
        ON tracked_wallets(enabled, quality_score DESC, updated_at_utc DESC)
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS wallet_token_observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            observed_at_utc TEXT NOT NULL,
            wallet_address TEXT NOT NULL,
            mint TEXT NOT NULL,
            symbol TEXT,
            tx_hash TEXT NOT NULL,
            side TEXT,
            source TEXT,
            volume_usd REAL,
            block_unix_time INTEGER,
            metadata_json TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_wallet_token_obs_unique
        ON wallet_token_observations(wallet_address, mint, tx_hash)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_wallet_token_obs_mint_ts
        ON wallet_token_observations(mint, observed_at_utc)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_wallet_token_obs_wallet_ts
        ON wallet_token_observations(wallet_address, observed_at_utc)
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS wallet_live_txs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            observed_at_utc TEXT NOT NULL,
            wallet_address TEXT NOT NULL,
            mint TEXT,
            symbol TEXT,
            tx_hash TEXT NOT NULL,
            side TEXT,
            amount_usd REAL,
            token_amount REAL,
            token_price REAL,
            source TEXT,
            pool_address TEXT,
            counterparty_symbol TEXT,
            counterparty_address TEXT,
            metadata_json TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_wallet_live_txs_unique
        ON wallet_live_txs(wallet_address, tx_hash)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_wallet_live_txs_wallet_ts
        ON wallet_live_txs(wallet_address, observed_at_utc)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_wallet_live_txs_mint_ts
        ON wallet_live_txs(mint, observed_at_utc)
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS wallet_quality_scores (
            wallet_address TEXT PRIMARY KEY,
            quality_score REAL NOT NULL,
            overlap_count INTEGER DEFAULT 0,
            positive_outcomes INTEGER DEFAULT 0,
            negative_outcomes INTEGER DEFAULT 0,
            neutral_outcomes INTEGER DEFAULT 0,
            last_seen_utc TEXT,
            updated_at_utc TEXT NOT NULL,
            metadata_json TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_wallet_quality_updated
        ON wallet_quality_scores(updated_at_utc)
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS wallet_signal_trade_outcomes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_id INTEGER NOT NULL,
            wallet_address TEXT NOT NULL,
            mint TEXT NOT NULL,
            symbol TEXT,
            opened_ts_utc TEXT,
            closed_ts_utc TEXT NOT NULL,
            pnl_pct REAL,
            outcome_label TEXT NOT NULL,
            regime_label TEXT,
            signal_source TEXT,
            quality_score_at_entry REAL,
            notes_json TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_wallet_signal_trade_outcomes_trade_wallet
        ON wallet_signal_trade_outcomes(trade_id, wallet_address)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_wallet_signal_trade_outcomes_wallet_ts
        ON wallet_signal_trade_outcomes(wallet_address, closed_ts_utc)
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS wallet_signal_accuracy (
            wallet_address TEXT PRIMARY KEY,
            trades_tagged INTEGER NOT NULL DEFAULT 0,
            wins INTEGER NOT NULL DEFAULT 0,
            losses INTEGER NOT NULL DEFAULT 0,
            neutral INTEGER NOT NULL DEFAULT 0,
            avg_return_pct REAL,
            last_return_pct REAL,
            best_return_pct REAL,
            worst_return_pct REAL,
            updated_at_utc TEXT NOT NULL,
            metadata_json TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_wallet_signal_accuracy_updated
        ON wallet_signal_accuracy(updated_at_utc)
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS mint_wallet_reinforcement_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mint TEXT NOT NULL,
            symbol TEXT,
            ts_utc TEXT NOT NULL,
            wallet_overlap_score REAL NOT NULL,
            wallet_confidence REAL NOT NULL,
            wallet_support_level TEXT NOT NULL,
            unique_wallets INTEGER DEFAULT 0,
            repeat_wallets INTEGER DEFAULT 0,
            high_quality_wallets INTEGER DEFAULT 0,
            recent_wallet_activity INTEGER DEFAULT 0,
            reasons_json TEXT,
            inputs_json TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_mint_wallet_reinforcement_mint_ts
        ON mint_wallet_reinforcement_snapshots(mint, ts_utc)
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS mint_wallet_behavior_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mint TEXT NOT NULL,
            symbol TEXT,
            ts_utc TEXT NOT NULL,
            wallet_conviction_score REAL NOT NULL,
            wallet_cluster_score REAL NOT NULL,
            wallet_behavior_state TEXT NOT NULL,
            smart_money_quality TEXT NOT NULL,
            tracked_wallet_count INTEGER DEFAULT 0,
            buy_wallet_count INTEGER DEFAULT 0,
            sell_wallet_count INTEGER DEFAULT 0,
            buy_volume_usd REAL DEFAULT 0,
            sell_volume_usd REAL DEFAULT 0,
            repeat_wallet_count INTEGER DEFAULT 0,
            net_flow_usd REAL DEFAULT 0,
            reasons_json TEXT,
            inputs_json TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_mint_wallet_behavior_mint_ts
        ON mint_wallet_behavior_snapshots(mint, ts_utc)
        """
    )


def _ensure_manual_perp_journal_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS manual_perp_journal (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts_utc TEXT NOT NULL,
            symbol TEXT NOT NULL,
            action TEXT NOT NULL,
            side TEXT,
            price REAL,
            size_usd REAL,
            deposit_withdraw_usd REAL,
            fee_usd REAL,
            realized_pnl_usd REAL,
            leverage REAL,
            source TEXT NOT NULL DEFAULT 'MANUAL',
            source_ref TEXT,
            notes TEXT,
            created_at_utc TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_manual_perp_journal_symbol_ts
        ON manual_perp_journal(symbol, ts_utc DESC)
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_manual_perp_journal_source_ref
        ON manual_perp_journal(source_ref)
        WHERE source_ref IS NOT NULL
        """
    )


def _ensure_trade_quality_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS memecoin_trade_quality_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mint TEXT NOT NULL,
            symbol TEXT,
            ts_utc TEXT NOT NULL,
            liquidity_quality_score REAL NOT NULL,
            trade_quality_score REAL NOT NULL,
            market_integrity_score REAL NOT NULL,
            execution_quality_score REAL NOT NULL,
            quality_verdict TEXT NOT NULL,
            reasons_json TEXT,
            inputs_json TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_memecoin_trade_quality_mint_ts
        ON memecoin_trade_quality_snapshots(mint, ts_utc)
        """
    )


def _ensure_token_stats_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS memecoin_token_stats_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mint TEXT NOT NULL,
            symbol TEXT,
            ts_utc TEXT NOT NULL,
            price REAL,
            liquidity REAL,
            marketcap REAL,
            fdv REAL,
            last_trade_unix_time INTEGER,
            volume_30m_usd REAL,
            volume_1h_usd REAL,
            volume_24h_usd REAL,
            volume_buy_1h_usd REAL,
            volume_sell_1h_usd REAL,
            trade_1h INTEGER,
            buy_1h INTEGER,
            sell_1h INTEGER,
            unique_wallet_1h INTEGER,
            price_change_30m_percent REAL,
            price_change_1h_percent REAL,
            price_change_24h_percent REAL,
            volume_1h_change_percent REAL,
            trade_1h_change_percent REAL,
            reasons_json TEXT,
            inputs_json TEXT,
            raw_json TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_memecoin_token_stats_mint_ts
        ON memecoin_token_stats_snapshots(mint, ts_utc)
        """
    )


def _ensure_large_trade_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS memecoin_large_trade_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mint TEXT NOT NULL,
            symbol TEXT,
            ts_utc TEXT NOT NULL,
            trade_side TEXT,
            volume_usd REAL NOT NULL,
            owner TEXT,
            source TEXT,
            tx_hash TEXT NOT NULL,
            pool_address TEXT,
            counterparty_symbol TEXT,
            counterparty_address TEXT,
            token_amount REAL,
            token_price REAL,
            sponsorship_label TEXT,
            reasons_json TEXT,
            raw_json TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_memecoin_large_trade_unique
        ON memecoin_large_trade_snapshots(mint, tx_hash)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_memecoin_large_trade_mint_ts
        ON memecoin_large_trade_snapshots(mint, ts_utc)
        """
    )


def _ensure_speculation_heat_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS speculation_heat_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts_utc TEXT NOT NULL,
            heat_state TEXT NOT NULL,
            heat_score REAL NOT NULL,
            momentum TEXT,
            speculation_heat_score REAL NOT NULL,
            froth_score REAL NOT NULL,
            sponsorship_score REAL NOT NULL,
            quality_score REAL NOT NULL,
            note TEXT,
            signature TEXT,
            inputs_json TEXT,
            reasons_json TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_speculation_heat_ts
        ON speculation_heat_snapshots(ts_utc)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_speculation_heat_signature_ts
        ON speculation_heat_snapshots(signature, ts_utc)
        """
    )


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

    with get_conn() as conn:
        cur = conn.cursor()
        _ensure_speculation_heat_tables(conn)
        _ensure_large_trade_tables(conn)
        _ensure_trade_quality_tables(conn)
        _ensure_token_stats_tables(conn)
        _ensure_wallet_reinforcement_tables(conn)
        _ensure_manual_perp_journal_tables(conn)

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

        # ── Patch 203: sample-build tagging ──────────────────────────────────
        try:
            cur.execute("ALTER TABLE alert_outcomes ADD COLUMN is_sample_build INTEGER NOT NULL DEFAULT 0")
        except Exception:
            pass  # column already exists — fine

        # ── Patch 248: entry-context attribution ─────────────────────────────
        try:
            cur.execute("ALTER TABLE alert_outcomes ADD COLUMN entry_context TEXT")
        except Exception:
            pass  # column already exists — fine

        # ── Patch 252: setup_label — watchlist status persistence ────────────
        try:
            cur.execute("ALTER TABLE alert_outcomes ADD COLUMN setup_label TEXT")
        except Exception:
            pass  # column already exists — fine

        # ── Phase-5 perp trading tables ─────────────────────────────────────
        cur.execute("""
        CREATE TABLE IF NOT EXISTS perp_positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            opened_ts_utc TEXT NOT NULL,
            closed_ts_utc TEXT,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            entry_price REAL NOT NULL,
            exit_price REAL,
            stop_price REAL NOT NULL,
            tp1_price REAL,
            tp2_price REAL,
            size_usd REAL NOT NULL,
            leverage REAL NOT NULL DEFAULT 2.0,
            collateral_usd REAL,
            pnl_pct REAL,
            pnl_usd REAL,
            regime_label TEXT,
            exit_reason TEXT,
            status TEXT NOT NULL DEFAULT 'OPEN',
            dry_run INTEGER NOT NULL DEFAULT 1,
            notes TEXT,
            jupiter_position_key TEXT,
            tx_sig_open TEXT,
            tx_sig_close TEXT
        );
        """)
        # Patch 108/162: idempotent — add columns to existing DBs that predate Patch 108
        for _col_def in (
            "jupiter_position_key TEXT",
            "tx_sig_open TEXT",
            "tx_sig_close TEXT",
        ):
            try:
                cur.execute(f"ALTER TABLE perp_positions ADD COLUMN {_col_def}")
            except Exception:
                pass  # column already exists — ignore
        cur.execute("""
        CREATE TABLE IF NOT EXISTS perp_outcomes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_ts_utc TEXT NOT NULL,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            entry_price REAL NOT NULL,
            regime_label TEXT,
            evaluated_1h_ts_utc TEXT,
            return_1h_pct REAL,
            evaluated_4h_ts_utc TEXT,
            return_4h_pct REAL,
            evaluated_24h_ts_utc TEXT,
            return_24h_pct REAL,
            status TEXT NOT NULL DEFAULT 'PENDING'
        );
        """)
        cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_perp_positions_symbol_status
        ON perp_positions(symbol, status);
        """)
        cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_perp_outcomes_symbol_ts
        ON perp_outcomes(symbol, created_ts_utc);
        """)

        # ── Patch 161: tier execution intent tracking ────────────────────────
        cur.execute("""
        CREATE TABLE IF NOT EXISTS tier_execution_intents (
            id                      INTEGER PRIMARY KEY AUTOINCREMENT,
            created_ts              TEXT    NOT NULL,
            resolved_ts             TEXT,
            tier_label              TEXT    NOT NULL,
            symbol                  TEXT    NOT NULL,
            side                    TEXT    NOT NULL,
            collateral_usd          REAL    NOT NULL,
            leverage                REAL    NOT NULL,
            status                  TEXT    NOT NULL DEFAULT 'PENDING',
            presigned_tx_sig        TEXT,
            position_pubkey         TEXT,
            tx_sig_confirmed        TEXT,
            perp_position_id        INTEGER,
            error_detail            TEXT,
            build_response_excerpt  TEXT
        );
        """)
        cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_tei_status
        ON tier_execution_intents(status);
        """)
        cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_tei_tier_created
        ON tier_execution_intents(tier_label, created_ts);
        """)

        # ── Patch 115: memecoin spot trades ─────────────────────────────────
        cur.execute("""
        CREATE TABLE IF NOT EXISTS memecoin_trades (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            opened_ts_utc   TEXT NOT NULL,
            symbol          TEXT NOT NULL,
            mint            TEXT NOT NULL,
            entry_price     REAL,
            amount_usd      REAL,
            initial_amount_usd REAL,
            token_amount    REAL,
            status          TEXT DEFAULT 'OPEN',
            exit_price      REAL,
            exit_reason     TEXT,
            pnl_pct         REAL,
            pnl_usd         REAL,
            realized_release_usd REAL DEFAULT 0,
            realized_pnl_usd REAL DEFAULT 0,
            closed_ts_utc   TEXT,
            tx_sig_open     TEXT,
            tx_sig_close    TEXT,
            is_proof_build  INTEGER DEFAULT 0,
            proof_status    TEXT,
            proof_reason    TEXT,
            proof_score     REAL,
            proof_snapshot_json TEXT,
            entry_timing_score REAL,
            entry_safety_score REAL,
            entry_market_quality_score REAL,
            entry_support_score REAL,
            entry_readiness_score REAL,
            entry_profit_room_score REAL,
            entry_profit_room_label TEXT,
            entry_market_quality_verdict TEXT,
            entry_wallet_behavior_state TEXT,
            entry_smart_money_quality TEXT,
            entry_regime_label TEXT,
            entry_route TEXT,
            entry_lane_authority TEXT,
            scan_detected_at TEXT,
            proof_ready_at TEXT,
            entry_fired_at TEXT,
            minutes_scan_to_proof_ready REAL,
            minutes_proof_ready_to_entry REAL,
            minutes_scan_to_entry REAL,
            scan_price REAL,
            proof_ready_price REAL,
            scan_mcap REAL,
            proof_ready_mcap REAL,
            pct_move_scan_to_entry REAL,
            pct_move_proof_ready_to_entry REAL,
            entry_timing_bucket TEXT,
            entry_window_position_pct REAL,
            signal_window_phase TEXT,
            confirmation_wait_used INTEGER DEFAULT 0,
            confirmation_wait_minutes REAL,
            max_favorable_excursion_pct REAL,
            max_adverse_excursion_pct REAL,
            mfe_ts_utc TEXT,
            mae_ts_utc TEXT,
            entry_attribution_json TEXT
        );
        """)
        cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_memecoin_trades_status
        ON memecoin_trades(status);
        """)

        # ── Spot accumulation tables — centralized in init_db (Patch 321) ─────
        cur.execute("""
        CREATE TABLE IF NOT EXISTS spot_holdings (
            symbol          TEXT PRIMARY KEY,
            mint            TEXT NOT NULL,
            token_amount    REAL NOT NULL DEFAULT 0,
            total_invested  REAL NOT NULL DEFAULT 0,
            avg_cost_usd    REAL,
            last_buy_ts     TEXT,
            created_ts      TEXT NOT NULL
        );
        """)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS spot_buys (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            ts_utc          TEXT NOT NULL,
            symbol          TEXT NOT NULL,
            mint            TEXT NOT NULL,
            side            TEXT NOT NULL,
            amount_usd      REAL,
            token_amount    REAL,
            price_usd       REAL,
            tx_sig          TEXT,
            dry_run         INTEGER NOT NULL DEFAULT 1
        );
        """)
        cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_spot_buys_symbol_ts
        ON spot_buys(symbol, ts_utc);
        """)

        # ── Cross-arm capital ledger (Patch 321) ──────────────────────────────
        cur.execute("""
        CREATE TABLE IF NOT EXISTS capital_events (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            ts_utc          TEXT NOT NULL,
            arm             TEXT NOT NULL,
            event_type      TEXT NOT NULL,
            amount_usd      REAL NOT NULL,
            notes           TEXT,
            symbol          TEXT,
            ref_table       TEXT,
            ref_id          INTEGER,
            dry_run         INTEGER NOT NULL DEFAULT 1
        );
        """)
        cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_capital_events_arm_ts
        ON capital_events(arm, ts_utc);
        """)
        cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_capital_events_type_ts
        ON capital_events(event_type, ts_utc);
        """)

        # ── Memecoin proof exit reviews (Patch 322) ───────────────────────────
        cur.execute("""
        CREATE TABLE IF NOT EXISTS memecoin_exit_reviews (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            ts_utc              TEXT NOT NULL,
            trade_id            INTEGER NOT NULL,
            mint                TEXT NOT NULL,
            symbol              TEXT NOT NULL,
            scanner_regime      TEXT,
            current_price       REAL,
            current_return_pct  REAL,
            age_hours           REAL,
            entry_proof_score   REAL,
            current_proof_score REAL,
            proof_score_delta   REAL,
            current_proof_reason TEXT,
            review_state        TEXT NOT NULL,
            recommended_action  TEXT NOT NULL,
            exit_reason         TEXT,
            should_exit         INTEGER NOT NULL DEFAULT 0,
            auto_exit_enabled   INTEGER NOT NULL DEFAULT 0,
            executed            INTEGER NOT NULL DEFAULT 0,
            intent_id           INTEGER,
            notes               TEXT
        );
        """)
        cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_memecoin_exit_reviews_trade_ts
        ON memecoin_exit_reviews(trade_id, ts_utc);
        """)
        cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_memecoin_exit_reviews_mint_ts
        ON memecoin_exit_reviews(mint, ts_utc);
        """)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS memecoin_exit_signal_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts_utc TEXT NOT NULL,
            trade_id INTEGER NOT NULL,
            mint TEXT NOT NULL,
            symbol TEXT NOT NULL,
            age_minutes REAL,
            current_price REAL,
            current_return_pct REAL,
            current_scanner_score REAL,
            current_timing_score REAL,
            current_market_quality_score REAL,
            current_support_score REAL,
            current_readiness_score REAL,
            current_profit_room_score REAL,
            current_profit_room_label TEXT,
            current_market_quality_verdict TEXT,
            current_wallet_behavior_state TEXT,
            current_smart_money_quality TEXT,
            current_proof_score REAL,
            current_proof_reason TEXT,
            volume_trend_label TEXT,
            scanner_still_actionable INTEGER NOT NULL DEFAULT 0,
            large_trade_buy_volume REAL,
            large_trade_sell_volume REAL,
            large_trade_buy_count INTEGER,
            large_trade_sell_count INTEGER,
            token_stats_ts_utc TEXT,
            notes_json TEXT
        );
        """)
        cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_memecoin_exit_signal_snapshots_trade_ts
        ON memecoin_exit_signal_snapshots(trade_id, ts_utc);
        """)
        cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_memecoin_exit_signal_snapshots_mint_ts
        ON memecoin_exit_signal_snapshots(mint, ts_utc);
        """)
        try:
            cur.execute("ALTER TABLE memecoin_exit_reviews ADD COLUMN recommended_pct REAL")
        except Exception:
            pass
        for _col in (
            ("entry_readiness_score", "REAL"),
            ("current_readiness_score", "REAL"),
            ("readiness_delta", "REAL"),
            ("current_readiness_level", "TEXT"),
            ("entry_support_score", "REAL"),
            ("current_support_score", "REAL"),
            ("support_score_delta", "REAL"),
            ("entry_market_quality_score", "REAL"),
            ("current_market_quality_score", "REAL"),
            ("market_quality_score_delta", "REAL"),
            ("entry_market_quality_verdict", "TEXT"),
            ("current_market_quality_verdict", "TEXT"),
            ("entry_profit_room_score", "REAL"),
            ("current_profit_room_score", "REAL"),
            ("profit_room_score_delta", "REAL"),
            ("entry_profit_room_label", "TEXT"),
            ("current_profit_room_label", "TEXT"),
            ("entry_wallet_behavior_state", "TEXT"),
            ("current_wallet_behavior_state", "TEXT"),
            ("entry_smart_money_quality", "TEXT"),
            ("current_smart_money_quality", "TEXT"),
            ("comparison_snapshot_json", "TEXT"),
        ):
            try:
                cur.execute(f"ALTER TABLE memecoin_exit_reviews ADD COLUMN {_col[0]} {_col[1]}")
            except Exception:
                pass

        # ── Patch 116+117: memecoin signal outcome tracking ──────────────────
        # Records every DexScreener signal shown to user.
        # Patch 117 adds safety/mcap/age enrichment columns.
        cur.execute("""
        CREATE TABLE IF NOT EXISTS memecoin_signal_outcomes (
            id                   INTEGER PRIMARY KEY AUTOINCREMENT,
            scanned_at           TEXT NOT NULL,
            symbol               TEXT NOT NULL,
            mint                 TEXT NOT NULL,
            score                REAL,
            price_at_scan        REAL,
            change_1h_at_scan    REAL,
            volume_24h           REAL,
            liquidity_usd        REAL,
            -- Patch 117 enrichment columns
            rug_label            TEXT DEFAULT 'UNKNOWN',
            top_holder_pct       REAL,
            lp_locked_pct        REAL,
            mcap_at_scan         REAL,
            token_age_days       REAL,
            vol_acceleration     REAL,
            mint_revoked         INTEGER DEFAULT 0,
            freeze_revoked       INTEGER DEFAULT 0,
            -- Outcome columns (filled by outcome step)
            return_1h_pct        REAL,
            evaluated_1h_ts_utc  TEXT,
            return_4h_pct        REAL,
            evaluated_4h_ts_utc  TEXT,
            return_24h_pct       REAL,
            evaluated_24h_ts_utc TEXT,
            status               TEXT DEFAULT 'PENDING',
            bought               INTEGER DEFAULT 0,
            -- Patch 249: discovery source marker
            source               TEXT DEFAULT 'SCANNER',
            -- Patch 314: scanner regime attribution for learning-loop cohorting
            scanner_regime       TEXT DEFAULT 'NORMAL',
            scanner_relaxation_reason TEXT,
            -- Patch 254: attention layer — off-chain / narrative signals
            attention_infrastructure  TEXT DEFAULT NULL,  -- PRESENT | PARTIAL | ABSENT
            boost_active              INTEGER DEFAULT 0,  -- 1 if DexScreener active boost
            attention_quality         TEXT DEFAULT NULL,  -- STRONG | MODERATE | WEAK | NONE
            regime_at_scan            TEXT,
            fear_greed_at_scan        REAL,
            heat_state_at_scan        TEXT,
            heat_score_at_scan        REAL
        );
        """)
        cur.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_memecoin_outcomes_mint_scan
        ON memecoin_signal_outcomes(mint, scanned_at);
        """)
        cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_memecoin_outcomes_status
        ON memecoin_signal_outcomes(status);
        """)

        # ── Patch 117 migration: add enrichment columns to existing DBs ───────
        _p117_cols = [
            ("rug_label",        "TEXT DEFAULT 'UNKNOWN'"),
            ("top_holder_pct",   "REAL"),
            ("lp_locked_pct",    "REAL"),
            ("mcap_at_scan",     "REAL"),
            ("token_age_days",   "REAL"),
            ("vol_acceleration", "REAL"),
            ("mint_revoked",     "INTEGER DEFAULT 0"),
            ("freeze_revoked",   "INTEGER DEFAULT 0"),
        ]
        for col, col_def in _p117_cols:
            try:
                cur.execute(
                    f"ALTER TABLE memecoin_signal_outcomes ADD COLUMN {col} {col_def}"
                )
            except Exception:
                pass  # column already exists

        # ── Patch 249: discovery source marker migration ──────────────────────
        try:
            cur.execute(
                "ALTER TABLE memecoin_signal_outcomes ADD COLUMN source TEXT DEFAULT 'SCANNER'"
            )
        except Exception:
            pass  # column already exists

        # ── Patch 254: attention layer columns migration ───────────────────────
        for _col, _defn in (
            ("scanner_regime",          "TEXT DEFAULT 'NORMAL'"),
            ("scanner_relaxation_reason","TEXT"),
            ("attention_infrastructure", "TEXT DEFAULT NULL"),
            ("boost_active",             "INTEGER DEFAULT 0"),
            ("attention_quality",        "TEXT DEFAULT NULL"),
            ("regime_at_scan",           "TEXT"),
            ("fear_greed_at_scan",       "REAL"),
            ("heat_state_at_scan",       "TEXT"),
            ("heat_score_at_scan",       "REAL"),
        ):
            try:
                cur.execute(
                    f"ALTER TABLE memecoin_signal_outcomes ADD COLUMN {_col} {_defn}"
                )
            except Exception:
                pass  # column already exists

        # Patch 310+: label-validation columns for trust / triage proof tracks.
        # labeled_at marks labels written near scan time so later calibration can
        # distinguish clean forward labels from older bootstrap backfills.
        for _col, _defn in (
            ("trust_label", "TEXT"),
            ("triage_state", "TEXT"),
            ("labeled_at", "TEXT"),
            ("is_proof_build", "INTEGER DEFAULT 0"),
            ("proof_reason", "TEXT"),
            ("proof_score", "REAL"),
            ("proof_snapshot_json", "TEXT"),
        ):
            try:
                cur.execute(
                    f"ALTER TABLE memecoin_signal_outcomes ADD COLUMN {_col} {_defn}"
                )
            except Exception:
                pass  # column already exists

        # ── Pilot mode migration: tag memecoin_trades with is_pilot flag ─────────
        # is_pilot=1 → trade was executed in PILOT mode (live, guardrailed).
        # is_pilot=0 → paper trade or unrestricted live (pre-pilot rows default to 0).
        try:
            cur.execute("ALTER TABLE memecoin_trades ADD COLUMN is_pilot INTEGER DEFAULT 0")
        except Exception:
            pass  # column already exists

        # ── Patch 272: lifecycle context at auto-buy entry ────────────────────────
        # Snapshot the lifecycle gate conditions at the moment the trade is opened.
        # Stored immutably on the trade row so attribution is always accurate
        # regardless of how symbol_lifecycle changes after entry.
        for _col, _defn in [
            ("entry_fuel_quality", "TEXT"),
            ("entry_window",       "TEXT"),
            ("entry_move_phase",   "TEXT"),
            ("entry_score",        "REAL"),
            ("initial_amount_usd", "REAL"),
            ("is_proof_build",     "INTEGER DEFAULT 0"),
            ("proof_status",       "TEXT"),
            ("proof_reason",       "TEXT"),
            ("proof_score",        "REAL"),
            ("proof_snapshot_json","TEXT"),
            ("entry_timing_score", "REAL"),
            ("entry_safety_score", "REAL"),
            ("entry_market_quality_score", "REAL"),
            ("entry_support_score", "REAL"),
            ("entry_readiness_score", "REAL"),
            ("entry_profit_room_score", "REAL"),
            ("entry_profit_room_label", "TEXT"),
            ("entry_market_quality_verdict", "TEXT"),
            ("entry_wallet_behavior_state", "TEXT"),
            ("entry_smart_money_quality", "TEXT"),
            ("entry_regime_label", "TEXT"),
            ("entry_route", "TEXT"),
            ("entry_lane_authority", "TEXT"),
            ("scan_detected_at", "TEXT"),
            ("proof_ready_at", "TEXT"),
            ("entry_fired_at", "TEXT"),
            ("minutes_scan_to_proof_ready", "REAL"),
            ("minutes_proof_ready_to_entry", "REAL"),
            ("minutes_scan_to_entry", "REAL"),
            ("scan_price", "REAL"),
            ("proof_ready_price", "REAL"),
            ("scan_mcap", "REAL"),
            ("proof_ready_mcap", "REAL"),
            ("pct_move_scan_to_entry", "REAL"),
            ("pct_move_proof_ready_to_entry", "REAL"),
            ("entry_timing_bucket", "TEXT"),
            ("entry_window_position_pct", "REAL"),
            ("signal_window_phase", "TEXT"),
            ("confirmation_wait_used", "INTEGER DEFAULT 0"),
            ("confirmation_wait_minutes", "REAL"),
            ("max_favorable_excursion_pct", "REAL"),
            ("max_adverse_excursion_pct", "REAL"),
            ("mfe_ts_utc", "TEXT"),
            ("mae_ts_utc", "TEXT"),
            ("entry_attribution_json", "TEXT"),
            ("realized_release_usd","REAL DEFAULT 0"),
            ("realized_pnl_usd",   "REAL DEFAULT 0"),
        ]:
            try:
                cur.execute(f"ALTER TABLE memecoin_trades ADD COLUMN {_col} {_defn}")
            except Exception:
                pass  # column already exists

        # Patch 164: ensure kv_store exists for persistent_rate_limit_check.
        # tier_manager._get_db() also creates it, but init_db() is used by tests
        # and fresh installs before tier_manager ever runs.
        cur.execute("""
        CREATE TABLE IF NOT EXISTS kv_store (
            key   TEXT PRIMARY KEY,
            value TEXT
        )
        """)
        _ensure_watchdog_tables(conn)
        _ensure_allocation_recommendation_tables(conn)
        _ensure_wallet_reinforcement_tables(conn)
        _ensure_manual_perp_journal_tables(conn)

        # Roadmap 3: authority decision audit log (observe-before-enforce phase).
        cur.execute("""
        CREATE TABLE IF NOT EXISTS authority_decisions (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            ts_utc          TEXT    NOT NULL,
            executor        TEXT    NOT NULL,
            lane            TEXT    NOT NULL,
            proposed_action TEXT    NOT NULL,
            verdict         TEXT    NOT NULL,
            reasons         TEXT    NOT NULL,
            enforce         INTEGER NOT NULL DEFAULT 0,
            snapshot_age_s  INTEGER,
            stale           INTEGER NOT NULL DEFAULT 0
        )
        """)

        # Patch 240: per-symbol lifecycle state computed from MSO history.
        cur.execute("""
        CREATE TABLE IF NOT EXISTS symbol_lifecycle (
            symbol                   TEXT PRIMARY KEY,
            lifecycle_state          TEXT    NOT NULL,
            n_windows                INTEGER DEFAULT 0,
            best_return_pct          REAL,
            liq_floor                REAL,
            liq_current              REAL,
            liq_trend                TEXT,
            pullback_depth_pct       REAL,
            hours_since_last_window  REAL,
            hours_since_last_scan    REAL,
            peak_liq_last_window     REAL,
            vol_acc_current          REAL,
            first_leg_confirmed      INTEGER DEFAULT 0,
            multi_leg_confirmed      INTEGER DEFAULT 0,
            survivor_confirmed       INTEGER DEFAULT 0,
            last_computed_at         TEXT    NOT NULL,
            state_entered_at         TEXT,
            first_seen_at            TEXT,
            -- Patch 246: leg-intelligence phase fields
            move_phase               TEXT,
            phase_score              INTEGER,
            entry_window             TEXT,
            -- Patch 248: fuel quality
            fuel_quality             TEXT,
            provisional_first_leg    INTEGER DEFAULT 0,
            provisional_reason       TEXT,
            provisional_score        REAL,
            -- Patch 251: mint identity (staged, symbol PK unchanged)
            mint                     TEXT DEFAULT NULL
        )
        """)

        # Patch 244: add freshness columns to existing symbol_lifecycle tables (migration).
        _sl_cols = {row[1] for row in cur.execute("PRAGMA table_info(symbol_lifecycle)").fetchall()}
        if "state_entered_at" not in _sl_cols:
            cur.execute("ALTER TABLE symbol_lifecycle ADD COLUMN state_entered_at TEXT")
        if "first_seen_at" not in _sl_cols:
            cur.execute("ALTER TABLE symbol_lifecycle ADD COLUMN first_seen_at TEXT")
        # Patch 246: leg-intelligence phase fields migration.
        for _col, _defn in [
            ("move_phase",   "TEXT"),
            ("phase_score",  "INTEGER"),
            ("entry_window", "TEXT"),
        ]:
            if _col not in _sl_cols:
                cur.execute(f"ALTER TABLE symbol_lifecycle ADD COLUMN {_col} {_defn}")
        # Patch 248: fuel quality migration.
        if "fuel_quality" not in _sl_cols:
            cur.execute("ALTER TABLE symbol_lifecycle ADD COLUMN fuel_quality TEXT")
        for _col, _defn in [
            ("provisional_first_leg", "INTEGER DEFAULT 0"),
            ("provisional_reason", "TEXT"),
            ("provisional_score", "REAL"),
        ]:
            if _col not in _sl_cols:
                cur.execute(f"ALTER TABLE symbol_lifecycle ADD COLUMN {_col} {_defn}")
        # Patch 251: mint identity — add mint column (first staged mint-aware upgrade).
        if "mint" not in _sl_cols:
            cur.execute("ALTER TABLE symbol_lifecycle ADD COLUMN mint TEXT DEFAULT NULL")
        # Backfill: most recent mint per symbol from MSO for existing rows.
        cur.execute("""
            UPDATE symbol_lifecycle
            SET mint = (
                SELECT mint FROM memecoin_signal_outcomes
                WHERE memecoin_signal_outcomes.symbol = symbol_lifecycle.symbol
                  AND mint IS NOT NULL
                ORDER BY scanned_at DESC
                LIMIT 1
            )
            WHERE mint IS NULL
        """)
        # Backfill: state_entered_at = last_computed_at where null (best available approximation)
        cur.execute("""
            UPDATE symbol_lifecycle
            SET state_entered_at = last_computed_at
            WHERE state_entered_at IS NULL
        """)
        # Backfill: first_seen_at = earliest MSO scanned_at for each symbol
        cur.execute("""
            UPDATE symbol_lifecycle
            SET first_seen_at = (
                SELECT MIN(scanned_at)
                FROM memecoin_signal_outcomes
                WHERE memecoin_signal_outcomes.symbol = symbol_lifecycle.symbol
            )
            WHERE first_seen_at IS NULL
        """)

        # Patch 243: forward-validation snapshots for lifecycle ranking lane.
        cur.execute("""
        CREATE TABLE IF NOT EXISTS lifecycle_rank_snapshots (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol              TEXT    NOT NULL,
            snapped_at          TEXT    NOT NULL,
            lifecycle_state     TEXT    NOT NULL,
            priority            TEXT    NOT NULL,
            rank_score          INTEGER NOT NULL,
            liq_current         REAL,
            liq_trend           TEXT,
            pullback_depth_pct  REAL,
            n_windows           INTEGER,
            best_return_pct     REAL,
            survivor_confirmed  INTEGER DEFAULT 0,
            multi_leg_confirmed INTEGER DEFAULT 0,
            -- Patch 245: attribution dimensions recorded at snapshot time
            is_early_watch      INTEGER DEFAULT 0,
            freshness_bucket    TEXT,
            -- outcome columns (filled ~24h later by link_lifecycle_outcomes)
            outcome_return_pct  REAL,
            outcome_is_win      INTEGER,
            outcome_at          TEXT,
            outcome_hours       REAL,
            liq_at_outcome      REAL
        )
        """)
        cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_lrs_symbol_snapped
        ON lifecycle_rank_snapshots (symbol, snapped_at)
        """)

        # Patch 245: migrate existing lifecycle_rank_snapshots table.
        _lrs_cols = {row[1] for row in cur.execute("PRAGMA table_info(lifecycle_rank_snapshots)").fetchall()}
        for _col, _defn in [
            ("is_early_watch",   "INTEGER DEFAULT 0"),
            ("freshness_bucket", "TEXT"),
            ("liq_at_outcome",   "REAL"),
        ]:
            if _col not in _lrs_cols:
                cur.execute(f"ALTER TABLE lifecycle_rank_snapshots ADD COLUMN {_col} {_defn}")
        # Backfill existing rows: treat all as confirmed lane, freshness unknown
        cur.execute("""
            UPDATE lifecycle_rank_snapshots
            SET is_early_watch = 0
            WHERE is_early_watch IS NULL
        """)
        cur.execute("""
            UPDATE lifecycle_rank_snapshots
            SET freshness_bucket = 'new<24h'
            WHERE freshness_bucket IS NULL
        """)

        # ── Phase 5 Step 1: auto-execution audit log ────────────────────────
        cur.execute("""
        CREATE TABLE IF NOT EXISTS execution_intents (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            ts_utc          TEXT    NOT NULL,
            source          TEXT    NOT NULL,
            lane            TEXT    NOT NULL,
            action_type     TEXT    NOT NULL,
            symbol          TEXT,
            amount_usd      REAL,
            mode            TEXT    NOT NULL DEFAULT 'OBSERVE',
            authority_verdict TEXT,
            authority_reasons TEXT,
            executed        INTEGER NOT NULL DEFAULT 0,
            execution_result TEXT,
            notes           TEXT
        )
        """)
        cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_execution_intents_ts
        ON execution_intents (ts_utc)
        """)


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


def record_manual_perp_journal_event(
    *,
    event_ts_utc: str,
    symbol: str,
    action: str,
    side: str | None = None,
    price: float | None = None,
    size_usd: float | None = None,
    deposit_withdraw_usd: float | None = None,
    fee_usd: float | None = None,
    realized_pnl_usd: float | None = None,
    leverage: float | None = None,
    source: str = "MANUAL",
    source_ref: str | None = None,
    notes: str | None = None,
) -> dict:
    symbol_norm = str(symbol or "").strip().upper()
    action_norm = str(action or "").strip().upper()
    side_norm = str(side or "").strip().upper() or None
    source_norm = str(source or "MANUAL").strip().upper()
    source_ref_norm = str(source_ref or "").strip() or None
    event_ts_norm = str(event_ts_utc or "").strip()
    if not symbol_norm or not action_norm or not event_ts_norm:
        return {"created": False, "entry": None}

    with get_conn() as conn:
        _ensure_manual_perp_journal_tables(conn)
        cur = conn.cursor()
        if source_ref_norm:
            existing = cur.execute(
                """
                SELECT *
                FROM manual_perp_journal
                WHERE source_ref = ?
                LIMIT 1
                """,
                (source_ref_norm,),
            ).fetchone()
            if existing:
                return {"created": False, "entry": dict(existing)}

        cur.execute(
            """
            INSERT INTO manual_perp_journal (
                ts_utc,
                symbol,
                action,
                side,
                price,
                size_usd,
                deposit_withdraw_usd,
                fee_usd,
                realized_pnl_usd,
                leverage,
                source,
                source_ref,
                notes,
                created_at_utc
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_ts_norm,
                symbol_norm,
                action_norm,
                side_norm,
                float(price) if price is not None else None,
                float(size_usd) if size_usd is not None else None,
                float(deposit_withdraw_usd) if deposit_withdraw_usd is not None else None,
                float(fee_usd) if fee_usd is not None else None,
                float(realized_pnl_usd) if realized_pnl_usd is not None else None,
                float(leverage) if leverage is not None else None,
                source_norm,
                source_ref_norm,
                (notes or "")[:500] or None,
                datetime.utcnow().isoformat(),
            ),
        )
        row_id = int(cur.lastrowid)
        row = cur.execute(
            "SELECT * FROM manual_perp_journal WHERE id = ?",
            (row_id,),
        ).fetchone()
        return {"created": True, "entry": dict(row) if row else None}


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
                is_sample_build,
                entry_context,
                setup_label,
                status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                int(bool(outcome_data.get("is_sample_build", 0))),
                outcome_data.get("entry_context", "UNKNOWN"),  # Patch 248
                outcome_data.get("setup_label"),               # Patch 252
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
            WHERE status = 'PENDING'
            ORDER BY created_ts_utc ASC
            LIMIT ?
            """,
            (limit,),
        )
        return [dict(r) for r in cur.fetchall()]


def expire_stale_pending_outcomes(stale_hours: int = 72) -> int:
    """
    Mark very old PENDING alert outcomes as EXPIRED so they do not poison later
    horizon calculations with stale entry prices.

    This is idempotent: once a row leaves PENDING it will not be touched again.
    Returns the number of rows updated.
    """
    cutoff_iso = (datetime.utcnow() - timedelta(hours=stale_hours)).isoformat()
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE alert_outcomes
            SET status = 'EXPIRED',
                last_error = COALESCE(last_error, 'expired_stale_pending')
            WHERE status = 'PENDING'
              AND created_ts_utc < ?
              AND evaluated_1h_ts_utc IS NULL
            """,
            (cutoff_iso,),
        )
        return int(cur.rowcount or 0)


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
        parsed = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            return parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed
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

        outcome_source = "memecoin_signal_outcomes"
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
            FROM memecoin_signal_outcomes
            WHERE datetime(scanned_at) >= datetime(?)
            """,
            (cutoff_iso,),
        )
        out_row = cur.fetchone()
        if not out_row or not int(out_row["n4"] or 0):
            outcome_source = "alert_outcomes"
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
    if outcome_source == "alert_outcomes" and outcomes_4h_count >= min_outcomes_4h:
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
        "outcome_source": outcome_source,
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


# ── Roadmap 3: authority decision audit log ────────────────────────────────────

def record_authority_decision(
    executor: str,
    lane: str,
    proposed_action: str,
    verdict: str,
    reasons: list,
    enforce: bool,
    snapshot_age_s: int | None,
    stale: bool,
) -> None:
    """Persist one authority resolution event. Auto-trims to last 500 rows."""
    ts = datetime.utcnow().isoformat()
    reasons_str = "; ".join(reasons) if reasons else ""
    try:
        with get_conn() as conn:
            conn.execute(
                """
                INSERT INTO authority_decisions
                    (ts_utc, executor, lane, proposed_action, verdict, reasons,
                     enforce, snapshot_age_s, stale)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (ts, executor, lane, proposed_action, verdict, reasons_str,
                 1 if enforce else 0, snapshot_age_s, 1 if stale else 0),
            )
            # Keep table compact — trim oldest rows beyond 500
            conn.execute(
                """
                DELETE FROM authority_decisions
                WHERE id NOT IN (
                    SELECT id FROM authority_decisions
                    ORDER BY id DESC LIMIT 500
                )
                """
            )
    except Exception:
        pass  # observability must never break execution paths


def get_recent_authority_decisions(limit: int = 50) -> list[dict]:
    """Return the most recent authority decisions, newest first."""
    try:
        with get_conn() as conn:
            rows = conn.execute(
                """
                SELECT id, ts_utc, executor, lane, proposed_action, verdict,
                       reasons, enforce, snapshot_age_s, stale
                FROM authority_decisions
                ORDER BY id DESC
                LIMIT ?
                """,
                (max(1, min(limit, 500)),),
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


# ── Phase 5 Step 1: execution intent audit log ────────────────────────────────

def record_execution_intent(
    source: str,
    lane: str,
    action_type: str,
    symbol: str | None = None,
    amount_usd: float | None = None,
    mode: str = "OBSERVE",
    authority_verdict: str | None = None,
    authority_reasons: list | None = None,
    executed: bool = False,
    execution_result: str | None = None,
    notes: str | None = None,
) -> int | None:
    """
    Record one execution intent to the audit log.

    mode values:
      OBSERVE  — intent logged but not executed (observe-before-enforce)
      EXECUTE  — intent was evaluated and (if approved) executed
      MANUAL   — operator-initiated action, logged for completeness

    Returns the row id on success, None on failure.
    Never raises — observability must not break execution paths.
    """
    ts = datetime.utcnow().isoformat()
    reasons_str = "; ".join(authority_reasons) if authority_reasons else None
    try:
        with get_conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO execution_intents
                    (ts_utc, source, lane, action_type, symbol, amount_usd,
                     mode, authority_verdict, authority_reasons, executed,
                     execution_result, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (ts, source, lane, action_type, symbol, amount_usd,
                 mode, authority_verdict, reasons_str,
                 1 if executed else 0, execution_result, notes),
            )
            row_id = cur.lastrowid
            # Keep table compact — trim oldest rows beyond 1000
            conn.execute(
                """
                DELETE FROM execution_intents
                WHERE id NOT IN (
                    SELECT id FROM execution_intents
                    ORDER BY id DESC LIMIT 1000
                )
                """
            )
            return row_id
    except Exception:
        return None


def get_recent_execution_intents(limit: int = 50) -> list[dict]:
    """Return the most recent execution intents, newest first."""
    try:
        with get_conn() as conn:
            rows = conn.execute(
                """
                SELECT id, ts_utc, source, lane, action_type, symbol,
                       amount_usd, mode, authority_verdict, authority_reasons,
                       executed, execution_result, notes
                FROM execution_intents
                ORDER BY id DESC
                LIMIT ?
                """,
                (max(1, min(limit, 500)),),
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def record_capital_event(
    arm: str,
    event_type: str,
    amount_usd: float,
    notes: str | None = None,
    *,
    symbol: str | None = None,
    ref_table: str | None = None,
    ref_id: int | None = None,
    dry_run: bool = True,
    ts_utc: str | None = None,
) -> int | None:
    """
    Append one capital ledger event.

    Event types are intentionally simple:
      DEPLOY         — principal capital committed into a lane
      RELEASE        — principal capital released from a lane
      REALIZED_PNL   — realized profit/loss for a lane
      DEPOSIT        — external capital added
      WITHDRAWAL     — external capital removed
    """
    try:
        amount = round(float(amount_usd or 0.0), 4)
    except Exception:
        return None
    if amount == 0:
        return None
    try:
        with get_conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO capital_events
                    (ts_utc, arm, event_type, amount_usd, notes, symbol, ref_table, ref_id, dry_run)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ts_utc or datetime.utcnow().isoformat(),
                    str(arm or "").strip().lower(),
                    str(event_type or "").strip().upper(),
                    amount,
                    notes,
                    str(symbol or "").strip().upper() or None,
                    ref_table,
                    ref_id,
                    1 if dry_run else 0,
                ),
            )
            return cur.lastrowid
    except Exception:
        return None


def get_recent_capital_events(limit: int = 50) -> list[dict]:
    try:
        with get_conn() as conn:
            if not _table_exists(conn, "capital_events"):
                return []
            rows = conn.execute(
                """
                SELECT id, ts_utc, arm, event_type, amount_usd, notes, symbol, ref_table, ref_id, dry_run
                FROM capital_events
                ORDER BY id DESC
                LIMIT ?
                """,
                (max(1, min(limit, 200)),),
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def record_watchdog_event(
    watchdog: str,
    status: str,
    detail: str | None = None,
    *,
    metadata: dict | None = None,
    ts_utc: str | None = None,
) -> int | None:
    try:
        with get_conn() as conn:
            _ensure_watchdog_tables(conn)
            cur = conn.execute(
                """
                INSERT INTO watchdog_events
                    (ts_utc, watchdog, status, detail, metadata_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    ts_utc or datetime.utcnow().isoformat(),
                    str(watchdog or "").strip().upper(),
                    str(status or "").strip().upper(),
                    (detail or "").strip() or None,
                    json.dumps(metadata or {}, separators=(",", ":")),
                ),
            )
            return cur.lastrowid
    except Exception:
        return None


def get_recent_watchdog_events(*, watchdog: str | None = None, limit: int = 50) -> list[dict]:
    try:
        with get_conn() as conn:
            _ensure_watchdog_tables(conn)
            if watchdog:
                rows = conn.execute(
                    """
                    SELECT id, ts_utc, watchdog, status, detail, metadata_json
                    FROM watchdog_events
                    WHERE watchdog = ?
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (str(watchdog).strip().upper(), max(1, min(limit, 200))),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT id, ts_utc, watchdog, status, detail, metadata_json
                    FROM watchdog_events
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (max(1, min(limit, 200)),),
                ).fetchall()
        out: list[dict] = []
        for row in rows:
            item = dict(row)
            try:
                item["metadata"] = json.loads(item.pop("metadata_json") or "{}")
            except Exception:
                item["metadata"] = {}
                item.pop("metadata_json", None)
            out.append(item)
        return out
    except Exception:
        return []


def record_allocation_recommendation(
    payload: dict,
    *,
    min_interval_minutes: int = 360,
    force: bool = False,
) -> int | None:
    try:
        now = datetime.utcnow()
        ts_utc = str(payload.get("generated_at") or now.isoformat())
        signature_payload = {
            "posture": payload.get("posture"),
            "inputs": payload.get("inputs") or {},
            "recommendations": payload.get("recommendations") or [],
        }
        signature = json.dumps(signature_payload, sort_keys=True, separators=(",", ":"))
        with get_conn() as conn:
            _ensure_allocation_recommendation_tables(conn)
            if not force:
                row = conn.execute(
                    """
                    SELECT id, ts_utc, signature
                    FROM allocation_recommendations
                    ORDER BY id DESC
                    LIMIT 1
                    """
                ).fetchone()
                if row:
                    last_sig = str(row["signature"] or "")
                    last_ts = _parse_iso(str(row["ts_utc"] or ""))
                    age_minutes = None
                    if last_ts is not None:
                        age_minutes = max(0.0, (now - last_ts).total_seconds() / 60.0)
                    if last_sig == signature and age_minutes is not None and age_minutes < max(5, int(min_interval_minutes)):
                        return None
            cur = conn.execute(
                """
                INSERT INTO allocation_recommendations
                    (ts_utc, posture, note, allocatable_base_usd, signature, inputs_json, recommendations_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ts_utc,
                    str(payload.get("posture") or "UNKNOWN"),
                    str(payload.get("note") or "") or None,
                    float(payload.get("allocatable_base_usd") or 0.0),
                    signature,
                    json.dumps(payload.get("inputs") or {}, separators=(",", ":")),
                    json.dumps(payload.get("recommendations") or [], separators=(",", ":")),
                ),
            )
            conn.execute(
                """
                DELETE FROM allocation_recommendations
                WHERE id NOT IN (
                    SELECT id FROM allocation_recommendations
                    ORDER BY id DESC LIMIT 250
                )
                """
            )
            return cur.lastrowid
    except Exception:
        return None


def get_recent_allocation_recommendations(limit: int = 20) -> list[dict]:
    try:
        with get_conn() as conn:
            _ensure_allocation_recommendation_tables(conn)
            rows = conn.execute(
                """
                SELECT id, ts_utc, posture, note, allocatable_base_usd, inputs_json, recommendations_json
                FROM allocation_recommendations
                ORDER BY id DESC
                LIMIT ?
                """,
                (max(1, min(limit, 200)),),
            ).fetchall()
        out: list[dict] = []
        for row in rows:
            item = dict(row)
            try:
                item["inputs"] = json.loads(item.pop("inputs_json") or "{}")
            except Exception:
                item["inputs"] = {}
                item.pop("inputs_json", None)
            try:
                item["recommendations"] = json.loads(item.pop("recommendations_json") or "[]")
            except Exception:
                item["recommendations"] = []
                item.pop("recommendations_json", None)
            out.append(item)
        return out
    except Exception:
        return []


def record_speculation_heat_snapshot(
    payload: dict,
    *,
    min_interval_minutes: int = 60,
    force: bool = False,
) -> int | None:
    try:
        now = datetime.utcnow()
        ts_utc = str(payload.get("generated_at") or now.isoformat())
        signature_payload = {
            "heat_state": payload.get("heat_state"),
            "heat_score": round(float(payload.get("heat_score") or 0.0), 1),
            "momentum": payload.get("momentum"),
            "speculation_heat_score": round(float(payload.get("speculation_heat_score") or 0.0), 1),
            "froth_score": round(float(payload.get("froth_score") or 0.0), 1),
            "sponsorship_score": round(float(payload.get("sponsorship_score") or 0.0), 1),
            "quality_score": round(float(payload.get("quality_score") or 0.0), 1),
        }
        signature = json.dumps(signature_payload, sort_keys=True, separators=(",", ":"))
        with get_conn() as conn:
            _ensure_speculation_heat_tables(conn)
            if not force:
                row = conn.execute(
                    """
                    SELECT id, ts_utc, signature
                    FROM speculation_heat_snapshots
                    ORDER BY id DESC
                    LIMIT 1
                    """
                ).fetchone()
                if row:
                    last_sig = str(row["signature"] or "")
                    last_ts = _parse_iso(str(row["ts_utc"] or ""))
                    age_minutes = None
                    if last_ts is not None:
                        age_minutes = max(0.0, (now - last_ts).total_seconds() / 60.0)
                    if last_sig == signature and age_minutes is not None and age_minutes < max(10, int(min_interval_minutes)):
                        return None
            cur = conn.execute(
                """
                INSERT INTO speculation_heat_snapshots
                    (ts_utc, heat_state, heat_score, momentum, speculation_heat_score,
                     froth_score, sponsorship_score, quality_score, note, signature,
                     inputs_json, reasons_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ts_utc,
                    str(payload.get("heat_state") or "COOL"),
                    float(payload.get("heat_score") or 0.0),
                    str(payload.get("momentum") or "") or None,
                    float(payload.get("speculation_heat_score") or 0.0),
                    float(payload.get("froth_score") or 0.0),
                    float(payload.get("sponsorship_score") or 0.0),
                    float(payload.get("quality_score") or 0.0),
                    str(payload.get("note") or "") or None,
                    signature,
                    json.dumps(payload.get("inputs") or {}, separators=(",", ":")),
                    json.dumps(payload.get("reasons") or [], separators=(",", ":")),
                ),
            )
            conn.execute(
                """
                DELETE FROM speculation_heat_snapshots
                WHERE id NOT IN (
                    SELECT id FROM speculation_heat_snapshots
                    ORDER BY id DESC LIMIT 500
                )
                """
            )
            return cur.lastrowid
    except Exception:
        return None


def get_recent_speculation_heat_snapshots(limit: int = 24) -> list[dict]:
    try:
        with get_conn() as conn:
            _ensure_speculation_heat_tables(conn)
            rows = conn.execute(
                """
                SELECT id, ts_utc, heat_state, heat_score, momentum, speculation_heat_score,
                       froth_score, sponsorship_score, quality_score, note, inputs_json, reasons_json
                FROM speculation_heat_snapshots
                ORDER BY id DESC
                LIMIT ?
                """,
                (max(1, min(limit, 500)),),
            ).fetchall()
        out: list[dict] = []
        for row in rows:
            item = dict(row)
            try:
                item["inputs"] = json.loads(item.pop("inputs_json") or "{}")
            except Exception:
                item["inputs"] = {}
                item.pop("inputs_json", None)
            try:
                item["reasons"] = json.loads(item.pop("reasons_json") or "[]")
            except Exception:
                item["reasons"] = []
                item.pop("reasons_json", None)
            out.append(item)
        return out
    except Exception:
        return []


def get_latest_speculation_heat_snapshot() -> dict | None:
    rows = get_recent_speculation_heat_snapshots(limit=1)
    return rows[0] if rows else None


def record_wallet_observations(observations: list[dict]) -> int:
    if not observations:
        return 0
    inserted = 0
    try:
        with get_conn() as conn:
            _ensure_wallet_reinforcement_tables(conn)
            for item in observations:
                cur = conn.execute(
                    """
                    INSERT OR IGNORE INTO wallet_token_observations
                        (observed_at_utc, wallet_address, mint, symbol, tx_hash, side, source, volume_usd, block_unix_time, metadata_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(item.get("observed_at_utc") or datetime.utcnow().isoformat()),
                        str(item.get("wallet_address") or ""),
                        str(item.get("mint") or ""),
                        str(item.get("symbol") or "") or None,
                        str(item.get("tx_hash") or ""),
                        str(item.get("side") or "") or None,
                        str(item.get("source") or "") or None,
                        float(item.get("volume_usd") or 0.0),
                        int(item.get("block_unix_time") or 0) or None,
                        json.dumps(item.get("metadata") or {}, separators=(",", ":")),
                    ),
                )
                if int(cur.rowcount or 0) > 0:
                    inserted += 1
        return inserted
    except Exception:
        return inserted


def record_wallet_live_txs(rows: list[dict]) -> int:
    if not rows:
        return 0
    inserted = 0
    try:
        with get_conn() as conn:
            _ensure_wallet_reinforcement_tables(conn)
            for item in rows:
                cur = conn.execute(
                    """
                    INSERT OR IGNORE INTO wallet_live_txs
                        (observed_at_utc, wallet_address, mint, symbol, tx_hash, side,
                         amount_usd, token_amount, token_price, source, pool_address,
                         counterparty_symbol, counterparty_address, metadata_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(item.get("observed_at_utc") or datetime.utcnow().isoformat()),
                        str(item.get("wallet_address") or ""),
                        str(item.get("mint") or "") or None,
                        str(item.get("symbol") or "") or None,
                        str(item.get("tx_hash") or ""),
                        str(item.get("side") or "") or None,
                        float(item.get("amount_usd") or 0.0),
                        float(item.get("token_amount") or 0.0),
                        float(item.get("token_price") or 0.0),
                        str(item.get("source") or "") or None,
                        str(item.get("pool_address") or "") or None,
                        str(item.get("counterparty_symbol") or "") or None,
                        str(item.get("counterparty_address") or "") or None,
                        json.dumps(item.get("metadata") or {}, separators=(",", ":")),
                    ),
                )
                if int(cur.rowcount or 0) > 0:
                    inserted += 1
        return inserted
    except Exception:
        return inserted


def upsert_tracked_wallets(rows: list[dict]) -> int:
    if not rows:
        return 0
    written = 0
    now_iso = datetime.utcnow().isoformat()
    try:
        with get_conn() as conn:
            _ensure_wallet_reinforcement_tables(conn)
            for item in rows:
                wallet_address = str(item.get("wallet_address") or "").strip()
                if not wallet_address:
                    continue
                conn.execute(
                    """
                    INSERT INTO tracked_wallets
                        (wallet_address, label, cohort, enabled, quality_score, confidence_score,
                         source, notes, last_seen_utc, created_at_utc, updated_at_utc, metadata_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(wallet_address) DO UPDATE SET
                        label=COALESCE(excluded.label, tracked_wallets.label),
                        cohort=COALESCE(excluded.cohort, tracked_wallets.cohort),
                        enabled=excluded.enabled,
                        quality_score=COALESCE(excluded.quality_score, tracked_wallets.quality_score),
                        confidence_score=COALESCE(excluded.confidence_score, tracked_wallets.confidence_score),
                        source=COALESCE(excluded.source, tracked_wallets.source),
                        notes=COALESCE(excluded.notes, tracked_wallets.notes),
                        last_seen_utc=COALESCE(excluded.last_seen_utc, tracked_wallets.last_seen_utc),
                        updated_at_utc=excluded.updated_at_utc,
                        metadata_json=COALESCE(excluded.metadata_json, tracked_wallets.metadata_json)
                    """,
                    (
                        wallet_address,
                        str(item.get("label") or "") or None,
                        str(item.get("cohort") or "core") or None,
                        0 if item.get("enabled") is False else 1,
                        float(item.get("quality_score") or 0.0) if item.get("quality_score") is not None else None,
                        float(item.get("confidence_score") or 0.0) if item.get("confidence_score") is not None else None,
                        str(item.get("source") or "") or None,
                        str(item.get("notes") or "") or None,
                        str(item.get("last_seen_utc") or "") or None,
                        str(item.get("created_at_utc") or now_iso),
                        str(item.get("updated_at_utc") or now_iso),
                        json.dumps(item.get("metadata") or {}, separators=(",", ":")),
                    ),
                )
                written += 1
        return written
    except Exception:
        return written


def get_tracked_wallets(*, enabled_only: bool = True, limit: int | None = None) -> list[dict]:
    try:
        with get_conn() as conn:
            _ensure_wallet_reinforcement_tables(conn)
            query = "SELECT * FROM tracked_wallets"
            params: list = []
            if enabled_only:
                query += " WHERE enabled = 1"
            query += " ORDER BY COALESCE(quality_score, 0) DESC, updated_at_utc DESC"
            if limit is not None:
                query += " LIMIT ?"
                params.append(max(1, int(limit)))
            rows = conn.execute(query, params).fetchall()
        out: list[dict] = []
        for row in rows:
            item = dict(row)
            try:
                item["metadata"] = json.loads(item.pop("metadata_json") or "{}")
            except Exception:
                item["metadata"] = {}
                item.pop("metadata_json", None)
            item["enabled"] = bool(item.get("enabled"))
            out.append(item)
        return out
    except Exception:
        return []


def get_recent_wallet_signalers_for_mint(
    mint: str,
    *,
    max_age_hours: int = 24,
    limit: int = 8,
    min_quality_score: float = 55.0,
) -> list[dict]:
    clean_mint = str(mint or "").strip()
    if not clean_mint:
        return []

    cutoff = f"-{max(1, int(max_age_hours))} hours"
    wallet_rows: dict[str, dict] = {}
    try:
        with get_conn() as conn:
            _ensure_wallet_reinforcement_tables(conn)

            live_rows = conn.execute(
                """
                SELECT
                    l.wallet_address,
                    MAX(l.observed_at_utc) AS last_seen_utc,
                    SUM(COALESCE(l.amount_usd, 0)) AS total_volume_usd,
                    COUNT(*) AS event_count,
                    MAX(COALESCE(t.quality_score, q.quality_score, 45.0)) AS quality_score
                FROM wallet_live_txs l
                LEFT JOIN tracked_wallets t
                  ON t.wallet_address = l.wallet_address
                LEFT JOIN wallet_quality_scores q
                  ON q.wallet_address = l.wallet_address
                WHERE l.mint = ?
                  AND l.observed_at_utc >= datetime('now', ?)
                GROUP BY l.wallet_address
                ORDER BY quality_score DESC, total_volume_usd DESC, last_seen_utc DESC
                LIMIT ?
                """,
                (clean_mint, cutoff, max(limit * 2, 8)),
            ).fetchall()

            for row in live_rows:
                wallet = str(row["wallet_address"] or "").strip()
                if not wallet:
                    continue
                wallet_rows[wallet] = {
                    "wallet_address": wallet,
                    "source": "tracked_live",
                    "quality_score": round(float(row["quality_score"] or 45.0), 1),
                    "event_count": int(row["event_count"] or 0),
                    "volume_usd": round(float(row["total_volume_usd"] or 0.0), 2),
                    "last_seen_utc": str(row["last_seen_utc"] or "") or None,
                }

            obs_rows = conn.execute(
                """
                SELECT
                    o.wallet_address,
                    MAX(o.observed_at_utc) AS last_seen_utc,
                    SUM(COALESCE(o.volume_usd, 0)) AS total_volume_usd,
                    COUNT(*) AS event_count,
                    MAX(COALESCE(q.quality_score, 45.0)) AS quality_score
                FROM wallet_token_observations o
                LEFT JOIN wallet_quality_scores q
                  ON q.wallet_address = o.wallet_address
                WHERE o.mint = ?
                  AND o.observed_at_utc >= datetime('now', ?)
                GROUP BY o.wallet_address
                ORDER BY quality_score DESC, total_volume_usd DESC, last_seen_utc DESC
                LIMIT ?
                """,
                (clean_mint, cutoff, max(limit * 3, 12)),
            ).fetchall()
    except Exception:
        return []

    for row in obs_rows:
        wallet = str(row["wallet_address"] or "").strip()
        if not wallet or wallet in wallet_rows:
            continue
        wallet_rows[wallet] = {
            "wallet_address": wallet,
            "source": "recent_observation",
            "quality_score": round(float(row["quality_score"] or 45.0), 1),
            "event_count": int(row["event_count"] or 0),
            "volume_usd": round(float(row["total_volume_usd"] or 0.0), 2),
            "last_seen_utc": str(row["last_seen_utc"] or "") or None,
        }

    ranked = sorted(
        wallet_rows.values(),
        key=lambda item: (
            1 if str(item.get("source") or "") == "tracked_live" else 0,
            float(item.get("quality_score") or 0.0),
            float(item.get("volume_usd") or 0.0),
            int(item.get("event_count") or 0),
            str(item.get("last_seen_utc") or ""),
        ),
        reverse=True,
    )
    filtered = [item for item in ranked if float(item.get("quality_score") or 0.0) >= float(min_quality_score)]
    chosen = filtered[: max(1, int(limit))]
    if not chosen:
        chosen = ranked[: max(1, int(limit))]
    return chosen


def upsert_wallet_quality_scores(rows: list[dict]) -> int:
    if not rows:
        return 0
    written = 0
    try:
        with get_conn() as conn:
            _ensure_wallet_reinforcement_tables(conn)
            for item in rows:
                conn.execute(
                    """
                    INSERT INTO wallet_quality_scores
                        (wallet_address, quality_score, overlap_count, positive_outcomes, negative_outcomes, neutral_outcomes, last_seen_utc, updated_at_utc, metadata_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(wallet_address) DO UPDATE SET
                        quality_score=excluded.quality_score,
                        overlap_count=excluded.overlap_count,
                        positive_outcomes=excluded.positive_outcomes,
                        negative_outcomes=excluded.negative_outcomes,
                        neutral_outcomes=excluded.neutral_outcomes,
                        last_seen_utc=excluded.last_seen_utc,
                        updated_at_utc=excluded.updated_at_utc,
                        metadata_json=excluded.metadata_json
                    """,
                    (
                        str(item.get("wallet_address") or ""),
                        float(item.get("quality_score") or 0.0),
                        int(item.get("overlap_count") or 0),
                        int(item.get("positive_outcomes") or 0),
                        int(item.get("negative_outcomes") or 0),
                        int(item.get("neutral_outcomes") or 0),
                        str(item.get("last_seen_utc") or "") or None,
                        str(item.get("updated_at_utc") or datetime.utcnow().isoformat()),
                        json.dumps(item.get("metadata") or {}, separators=(",", ":")),
                    ),
                )
                written += 1
        return written
    except Exception:
        return written


def record_wallet_signal_trade_outcomes(
    *,
    trade_id: int,
    mint: str,
    symbol: str,
    wallets: list[dict],
    pnl_pct: float | None,
    closed_ts_utc: str,
    opened_ts_utc: str | None = None,
    regime_label: str | None = None,
) -> int:
    if not wallets:
        return 0

    inserted = 0
    now_iso = datetime.utcnow().isoformat()
    pnl_value = float(pnl_pct or 0.0)
    if pnl_value > 0:
        outcome_label = "WIN"
    elif pnl_value < 0:
        outcome_label = "LOSS"
    else:
        outcome_label = "NEUTRAL"

    clean_wallets: list[dict] = []
    seen: set[str] = set()
    for item in wallets:
        wallet = str((item or {}).get("wallet_address") or "").strip()
        if not wallet or wallet in seen:
            continue
        seen.add(wallet)
        clean_wallets.append(dict(item))

    if not clean_wallets:
        return 0

    try:
        with get_conn() as conn:
            _ensure_wallet_reinforcement_tables(conn)
            for item in clean_wallets:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO wallet_signal_trade_outcomes
                        (trade_id, wallet_address, mint, symbol, opened_ts_utc, closed_ts_utc,
                         pnl_pct, outcome_label, regime_label, signal_source,
                         quality_score_at_entry, notes_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        int(trade_id),
                        str(item.get("wallet_address") or ""),
                        mint,
                        symbol,
                        opened_ts_utc,
                        closed_ts_utc,
                        pnl_value,
                        outcome_label,
                        regime_label,
                        str(item.get("source") or "") or None,
                        float(item.get("quality_score") or 0.0) if item.get("quality_score") is not None else None,
                        json.dumps(
                            {
                                "event_count": int(item.get("event_count") or 0),
                                "volume_usd": float(item.get("volume_usd") or 0.0),
                                "last_seen_utc": item.get("last_seen_utc"),
                            },
                            separators=(",", ":"),
                        ),
                    ),
                )
                inserted += int(conn.execute("SELECT changes()").fetchone()[0] or 0)

            if inserted <= 0:
                return 0

            for wallet in seen:
                row = conn.execute(
                    """
                    SELECT
                        COUNT(*) AS trades_tagged,
                        SUM(CASE WHEN outcome_label='WIN' THEN 1 ELSE 0 END) AS wins,
                        SUM(CASE WHEN outcome_label='LOSS' THEN 1 ELSE 0 END) AS losses,
                        SUM(CASE WHEN outcome_label='NEUTRAL' THEN 1 ELSE 0 END) AS neutral,
                        AVG(COALESCE(pnl_pct, 0)) AS avg_return_pct,
                        MAX(COALESCE(pnl_pct, 0)) AS best_return_pct,
                        MIN(COALESCE(pnl_pct, 0)) AS worst_return_pct
                    FROM wallet_signal_trade_outcomes
                    WHERE wallet_address = ?
                    """,
                    (wallet,),
                ).fetchone()
                conn.execute(
                    """
                    INSERT INTO wallet_signal_accuracy
                        (wallet_address, trades_tagged, wins, losses, neutral, avg_return_pct,
                         last_return_pct, best_return_pct, worst_return_pct, updated_at_utc, metadata_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(wallet_address) DO UPDATE SET
                        trades_tagged=excluded.trades_tagged,
                        wins=excluded.wins,
                        losses=excluded.losses,
                        neutral=excluded.neutral,
                        avg_return_pct=excluded.avg_return_pct,
                        last_return_pct=excluded.last_return_pct,
                        best_return_pct=excluded.best_return_pct,
                        worst_return_pct=excluded.worst_return_pct,
                        updated_at_utc=excluded.updated_at_utc,
                        metadata_json=excluded.metadata_json
                    """,
                    (
                        wallet,
                        int(row["trades_tagged"] or 0),
                        int(row["wins"] or 0),
                        int(row["losses"] or 0),
                        int(row["neutral"] or 0),
                        round(float(row["avg_return_pct"] or 0.0), 4) if row["avg_return_pct"] is not None else None,
                        pnl_value,
                        round(float(row["best_return_pct"] or 0.0), 4) if row["best_return_pct"] is not None else None,
                        round(float(row["worst_return_pct"] or 0.0), 4) if row["worst_return_pct"] is not None else None,
                        now_iso,
                        json.dumps(
                            {
                                "last_trade_id": int(trade_id),
                                "last_symbol": symbol,
                                "last_outcome_label": outcome_label,
                                "regime_label": regime_label,
                            },
                            separators=(",", ":"),
                        ),
                    ),
                )
        return inserted
    except Exception:
        return inserted


def get_wallet_signal_accuracy(wallet_addresses: list[str]) -> dict[str, dict]:
    clean_wallets = [str(w or "").strip() for w in wallet_addresses if str(w or "").strip()]
    if not clean_wallets:
        return {}
    try:
        with get_conn() as conn:
            _ensure_wallet_reinforcement_tables(conn)
            placeholders = ",".join("?" for _ in clean_wallets)
            rows = conn.execute(
                f"SELECT * FROM wallet_signal_accuracy WHERE wallet_address IN ({placeholders})",
                clean_wallets,
            ).fetchall()
        out: dict[str, dict] = {}
        for row in rows:
            item = dict(row)
            try:
                item["metadata"] = json.loads(item.pop("metadata_json") or "{}")
            except Exception:
                item["metadata"] = {}
                item.pop("metadata_json", None)
            out[str(item.get("wallet_address") or "")] = item
        return out
    except Exception:
        return {}


def record_mint_wallet_reinforcement_snapshots(rows: list[dict]) -> int:
    if not rows:
        return 0
    written = 0
    try:
        with get_conn() as conn:
            _ensure_wallet_reinforcement_tables(conn)
            for item in rows:
                conn.execute(
                    """
                    INSERT INTO mint_wallet_reinforcement_snapshots
                        (mint, symbol, ts_utc, wallet_overlap_score, wallet_confidence, wallet_support_level, unique_wallets, repeat_wallets, high_quality_wallets, recent_wallet_activity, reasons_json, inputs_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(item.get("mint") or ""),
                        str(item.get("symbol") or "") or None,
                        str(item.get("ts_utc") or datetime.utcnow().isoformat()),
                        float(item.get("wallet_overlap_score") or 0.0),
                        float(item.get("wallet_confidence") or 0.0),
                        str(item.get("wallet_support_level") or "NONE"),
                        int(item.get("unique_wallets") or 0),
                        int(item.get("repeat_wallets") or 0),
                        int(item.get("high_quality_wallets") or 0),
                        1 if bool(item.get("recent_wallet_activity")) else 0,
                        json.dumps(item.get("reasons") or [], separators=(",", ":")),
                        json.dumps(item.get("inputs") or {}, separators=(",", ":")),
                    ),
                )
                written += 1
        return written
    except Exception:
        return written


def record_mint_wallet_behavior_snapshots(rows: list[dict]) -> int:
    if not rows:
        return 0
    written = 0
    try:
        with get_conn() as conn:
            _ensure_wallet_reinforcement_tables(conn)
            for item in rows:
                conn.execute(
                    """
                    INSERT INTO mint_wallet_behavior_snapshots
                        (mint, symbol, ts_utc, wallet_conviction_score, wallet_cluster_score,
                         wallet_behavior_state, smart_money_quality, tracked_wallet_count,
                         buy_wallet_count, sell_wallet_count, buy_volume_usd, sell_volume_usd,
                         repeat_wallet_count, net_flow_usd, reasons_json, inputs_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(item.get("mint") or ""),
                        str(item.get("symbol") or "") or None,
                        str(item.get("ts_utc") or datetime.utcnow().isoformat()),
                        float(item.get("wallet_conviction_score") or 0.0),
                        float(item.get("wallet_cluster_score") or 0.0),
                        str(item.get("wallet_behavior_state") or "MIXED"),
                        str(item.get("smart_money_quality") or "NONE"),
                        int(item.get("tracked_wallet_count") or 0),
                        int(item.get("buy_wallet_count") or 0),
                        int(item.get("sell_wallet_count") or 0),
                        float(item.get("buy_volume_usd") or 0.0),
                        float(item.get("sell_volume_usd") or 0.0),
                        int(item.get("repeat_wallet_count") or 0),
                        float(item.get("net_flow_usd") or 0.0),
                        json.dumps(item.get("reasons") or [], separators=(",", ":")),
                        json.dumps(item.get("inputs") or {}, separators=(",", ":")),
                    ),
                )
                written += 1
        return written
    except Exception:
        return written


def get_latest_wallet_reinforcement_for_mints(
    mints: list[str],
    *,
    max_age_minutes: int | None = None,
) -> dict[str, dict]:
    clean_mints = [str(m or "").strip() for m in mints if str(m or "").strip()]
    if not clean_mints:
        return {}
    try:
        with get_conn() as conn:
            _ensure_wallet_reinforcement_tables(conn)
            placeholders = ",".join("?" for _ in clean_mints)
            params: list = list(clean_mints)
            age_clause = ""
            if max_age_minutes is not None:
                age_clause = "AND ts_utc >= datetime('now', ?)"
                params.append(f"-{max(1, int(max_age_minutes))} minutes")
            rows = conn.execute(
                f"""
                WITH latest AS (
                    SELECT
                        mint,
                        MAX(id) AS max_id
                    FROM mint_wallet_reinforcement_snapshots
                    WHERE mint IN ({placeholders})
                    {age_clause}
                    GROUP BY mint
                )
                SELECT s.*
                FROM mint_wallet_reinforcement_snapshots s
                INNER JOIN latest l ON l.max_id = s.id
                """,
                params,
            ).fetchall()
        out: dict[str, dict] = {}
        for row in rows:
            item = dict(row)
            try:
                item["reasons"] = json.loads(item.pop("reasons_json") or "[]")
            except Exception:
                item["reasons"] = []
                item.pop("reasons_json", None)
            try:
                item["inputs"] = json.loads(item.pop("inputs_json") or "{}")
            except Exception:
                item["inputs"] = {}
                item.pop("inputs_json", None)
            out[str(item.get("mint") or "")] = item
        return out
    except Exception:
        return {}


def get_latest_wallet_behavior_for_mints(
    mints: list[str],
    *,
    max_age_minutes: int | None = None,
) -> dict[str, dict]:
    clean_mints = [str(m or "").strip() for m in mints if str(m or "").strip()]
    if not clean_mints:
        return {}
    try:
        with get_conn() as conn:
            _ensure_wallet_reinforcement_tables(conn)
            placeholders = ",".join("?" for _ in clean_mints)
            params: list = list(clean_mints)
            age_clause = ""
            if max_age_minutes is not None:
                age_clause = "AND ts_utc >= datetime('now', ?)"
                params.append(f"-{max(1, int(max_age_minutes))} minutes")
            rows = conn.execute(
                f"""
                WITH latest AS (
                    SELECT mint, MAX(id) AS max_id
                    FROM mint_wallet_behavior_snapshots
                    WHERE mint IN ({placeholders})
                    {age_clause}
                    GROUP BY mint
                )
                SELECT s.*
                FROM mint_wallet_behavior_snapshots s
                INNER JOIN latest l ON l.max_id = s.id
                """,
                params,
            ).fetchall()
        out: dict[str, dict] = {}
        for row in rows:
            item = dict(row)
            try:
                item["reasons"] = json.loads(item.pop("reasons_json") or "[]")
            except Exception:
                item["reasons"] = []
                item.pop("reasons_json", None)
            try:
                item["inputs"] = json.loads(item.pop("inputs_json") or "{}")
            except Exception:
                item["inputs"] = {}
                item.pop("inputs_json", None)
            out[str(item.get("mint") or "")] = item
        return out
    except Exception:
        return {}


def _capital_event_exists(
    conn: sqlite3.Connection,
    *,
    event_type: str,
    ref_table: str,
    ref_id: int,
) -> bool:
    row = conn.execute(
        """
        SELECT 1
        FROM capital_events
        WHERE event_type = ?
          AND ref_table = ?
          AND ref_id = ?
        LIMIT 1
        """,
        (event_type, ref_table, ref_id),
    ).fetchone()
    return bool(row)


def _insert_capital_event_row(
    conn: sqlite3.Connection,
    *,
    arm: str,
    event_type: str,
    amount_usd: float,
    notes: str,
    symbol: str | None,
    ref_table: str,
    ref_id: int,
    dry_run: bool,
    ts_utc: str | None,
) -> int:
    if abs(float(amount_usd or 0.0)) < 1e-9:
        return 0
    conn.execute(
        """
        INSERT INTO capital_events
            (ts_utc, arm, event_type, amount_usd, notes, symbol, ref_table, ref_id, dry_run)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            ts_utc or datetime.utcnow().isoformat(),
            arm,
            event_type,
            round(float(amount_usd or 0.0), 4),
            notes,
            str(symbol or "").strip().upper() or None,
            ref_table,
            int(ref_id),
            1 if dry_run else 0,
        ),
    )
    return 1


def _backfill_perp_capital_events(conn: sqlite3.Connection) -> dict[str, int]:
    inserted = {"deploy": 0, "release": 0, "realized_pnl": 0}
    if not _table_exists(conn, "perp_positions"):
        return inserted

    rows = conn.execute(
        """
        SELECT id, opened_ts_utc, closed_ts_utc, symbol, side, leverage,
               collateral_usd, pnl_usd, exit_reason, status, dry_run
        FROM perp_positions
        ORDER BY id ASC
        """
    ).fetchall()

    for row in rows:
        position_id = int(row["id"])
        symbol = str(row["symbol"] or "").upper() or None
        dry_run = bool(row["dry_run"])
        collateral = float(row["collateral_usd"] or 0.0)
        close_reason = str(row["exit_reason"] or "CLOSE")
        notes_base = f"BACKFILL {'PAPER' if dry_run else 'LIVE'} {str(row['side'] or '').upper()} {symbol or '?'} {float(row['leverage'] or 0.0):.1f}x"

        if collateral > 0 and not _capital_event_exists(
            conn, event_type="DEPLOY", ref_table="perp_positions", ref_id=position_id
        ):
            inserted["deploy"] += _insert_capital_event_row(
                conn,
                arm="perps",
                event_type="DEPLOY",
                amount_usd=collateral,
                notes=f"{notes_base} [backfill open]",
                symbol=symbol,
                ref_table="perp_positions",
                ref_id=position_id,
                dry_run=dry_run,
                ts_utc=row["opened_ts_utc"],
            )

        if str(row["status"] or "").upper() != "CLOSED":
            continue

        if collateral > 0 and not _capital_event_exists(
            conn, event_type="RELEASE", ref_table="perp_positions", ref_id=position_id
        ):
            inserted["release"] += _insert_capital_event_row(
                conn,
                arm="perps",
                event_type="RELEASE",
                amount_usd=collateral,
                notes=f"{notes_base} [backfill close {close_reason}]",
                symbol=symbol,
                ref_table="perp_positions",
                ref_id=position_id,
                dry_run=dry_run,
                ts_utc=row["closed_ts_utc"] or row["opened_ts_utc"],
            )

        pnl_usd = float(row["pnl_usd"] or 0.0)
        if abs(pnl_usd) < 1e-9:
            continue
        if _capital_event_exists(
            conn, event_type="REALIZED_PNL", ref_table="perp_positions", ref_id=position_id
        ):
            continue
        inserted["realized_pnl"] += _insert_capital_event_row(
            conn,
            arm="perps",
            event_type="REALIZED_PNL",
            amount_usd=pnl_usd,
            notes=f"{notes_base} [backfill close {close_reason}]",
            symbol=symbol,
            ref_table="perp_positions",
            ref_id=position_id,
            dry_run=dry_run,
            ts_utc=row["closed_ts_utc"] or row["opened_ts_utc"],
        )

    return inserted


def _backfill_memecoin_capital_events(conn: sqlite3.Connection) -> dict[str, int]:
    inserted = {"deploy": 0, "release": 0, "realized_pnl": 0}
    if not _table_exists(conn, "memecoin_trades"):
        return inserted

    rows = conn.execute(
        """
        SELECT id, opened_ts_utc, closed_ts_utc, symbol, amount_usd,
               pnl_usd, exit_reason, status, tx_sig_open
        FROM memecoin_trades
        ORDER BY id ASC
        """
    ).fetchall()

    for row in rows:
        trade_id = int(row["id"])
        symbol = str(row["symbol"] or "").upper() or None
        dry_run = str(row["tx_sig_open"] or "").upper() == "PAPER"
        principal = float(row["amount_usd"] or 0.0)
        notes_base = f"BACKFILL {'PAPER' if dry_run else 'LIVE'} {symbol or '?'}"

        if principal > 0 and not _capital_event_exists(
            conn, event_type="DEPLOY", ref_table="memecoin_trades", ref_id=trade_id
        ):
            inserted["deploy"] += _insert_capital_event_row(
                conn,
                arm="memecoins",
                event_type="DEPLOY",
                amount_usd=principal,
                notes=f"{notes_base} [backfill buy]",
                symbol=symbol,
                ref_table="memecoin_trades",
                ref_id=trade_id,
                dry_run=dry_run,
                ts_utc=row["opened_ts_utc"],
            )

        if str(row["status"] or "").upper() != "CLOSED":
            continue

        if principal > 0 and not _capital_event_exists(
            conn, event_type="RELEASE", ref_table="memecoin_trades", ref_id=trade_id
        ):
            inserted["release"] += _insert_capital_event_row(
                conn,
                arm="memecoins",
                event_type="RELEASE",
                amount_usd=principal,
                notes=f"{notes_base} [backfill sell {str(row['exit_reason'] or 'CLOSE')}]",
                symbol=symbol,
                ref_table="memecoin_trades",
                ref_id=trade_id,
                dry_run=dry_run,
                ts_utc=row["closed_ts_utc"] or row["opened_ts_utc"],
            )

        pnl_usd = float(row["pnl_usd"] or 0.0)
        if abs(pnl_usd) < 1e-9:
            continue
        if _capital_event_exists(
            conn, event_type="REALIZED_PNL", ref_table="memecoin_trades", ref_id=trade_id
        ):
            continue
        inserted["realized_pnl"] += _insert_capital_event_row(
            conn,
            arm="memecoins",
            event_type="REALIZED_PNL",
            amount_usd=pnl_usd,
            notes=f"{notes_base} [backfill sell {str(row['exit_reason'] or 'CLOSE')}]",
            symbol=symbol,
            ref_table="memecoin_trades",
            ref_id=trade_id,
            dry_run=dry_run,
            ts_utc=row["closed_ts_utc"] or row["opened_ts_utc"],
        )

    return inserted


def _backfill_spot_capital_events(conn: sqlite3.Connection) -> dict[str, int]:
    inserted = {"deploy": 0, "release": 0, "realized_pnl": 0}
    if not _table_exists(conn, "spot_buys"):
        return inserted

    holdings: dict[str, dict[str, float]] = {}
    rows = conn.execute(
        """
        SELECT id, ts_utc, symbol, side, amount_usd, token_amount, dry_run
        FROM spot_buys
        ORDER BY ts_utc ASC, id ASC
        """
    ).fetchall()

    for row in rows:
        row_id = int(row["id"])
        symbol = str(row["symbol"] or "").upper()
        side = str(row["side"] or "").upper()
        amount_usd = float(row["amount_usd"] or 0.0)
        token_amount = float(row["token_amount"] or 0.0)
        dry_run = bool(row["dry_run"])
        state = holdings.setdefault(symbol, {"token_amount": 0.0, "invested": 0.0})

        if side == "BUY":
            if amount_usd > 0 and not _capital_event_exists(
                conn, event_type="DEPLOY", ref_table="spot_buys", ref_id=row_id
            ):
                inserted["deploy"] += _insert_capital_event_row(
                    conn,
                    arm="spot",
                    event_type="DEPLOY",
                    amount_usd=amount_usd,
                    notes=f"BACKFILL {'PAPER' if dry_run else 'LIVE'} buy {symbol}",
                    symbol=symbol,
                    ref_table="spot_buys",
                    ref_id=row_id,
                    dry_run=dry_run,
                    ts_utc=row["ts_utc"],
                )
            state["token_amount"] += token_amount
            state["invested"] += amount_usd
            continue

        if side != "SELL":
            continue

        existing_tokens = float(state["token_amount"] or 0.0)
        existing_invested = float(state["invested"] or 0.0)
        sold_tokens = min(max(token_amount, 0.0), existing_tokens) if existing_tokens > 0 else 0.0
        if existing_tokens > 0 and sold_tokens > 0:
            if sold_tokens >= existing_tokens - 1e-9:
                principal_released = existing_invested
                remaining_tokens = 0.0
                remaining_invested = 0.0
            else:
                remaining_tokens = max(0.0, existing_tokens - sold_tokens)
                remaining_invested = existing_invested * (remaining_tokens / existing_tokens)
                principal_released = max(0.0, existing_invested - remaining_invested)
        else:
            principal_released = 0.0
            remaining_tokens = existing_tokens
            remaining_invested = existing_invested

        if principal_released > 0 and not _capital_event_exists(
            conn, event_type="RELEASE", ref_table="spot_buys", ref_id=row_id
        ):
            inserted["release"] += _insert_capital_event_row(
                conn,
                arm="spot",
                event_type="RELEASE",
                amount_usd=principal_released,
                notes=f"BACKFILL {'PAPER' if dry_run else 'LIVE'} sell {symbol}",
                symbol=symbol,
                ref_table="spot_buys",
                ref_id=row_id,
                dry_run=dry_run,
                ts_utc=row["ts_utc"],
            )

        realized_pnl = amount_usd - principal_released
        if abs(realized_pnl) > 1e-9 and not _capital_event_exists(
            conn, event_type="REALIZED_PNL", ref_table="spot_buys", ref_id=row_id
        ):
            inserted["realized_pnl"] += _insert_capital_event_row(
                conn,
                arm="spot",
                event_type="REALIZED_PNL",
                amount_usd=realized_pnl,
                notes=f"BACKFILL {'PAPER' if dry_run else 'LIVE'} sell {symbol}",
                symbol=symbol,
                ref_table="spot_buys",
                ref_id=row_id,
                dry_run=dry_run,
                ts_utc=row["ts_utc"],
            )

        state["token_amount"] = remaining_tokens
        state["invested"] = remaining_invested

    return inserted


def backfill_capital_events() -> dict:
    summary = {
        "inserted": {
            "perps": {"deploy": 0, "release": 0, "realized_pnl": 0},
            "memecoins": {"deploy": 0, "release": 0, "realized_pnl": 0},
            "spot": {"deploy": 0, "release": 0, "realized_pnl": 0},
        },
        "inserted_total": 0,
    }

    with get_conn() as conn:
        if not _table_exists(conn, "capital_events"):
            return summary

        summary["inserted"]["perps"] = _backfill_perp_capital_events(conn)
        summary["inserted"]["memecoins"] = _backfill_memecoin_capital_events(conn)
        summary["inserted"]["spot"] = _backfill_spot_capital_events(conn)

        summary["inserted_total"] = int(
            sum(
                int(count)
                for arm_counts in summary["inserted"].values()
                for count in arm_counts.values()
            )
        )
    return summary


def get_capital_allocation_snapshot() -> dict:
    """
    Cross-arm capital snapshot built from live state plus the capital ledger.

    Current deployed capital is derived from the live arm tables.
    Realized PnL is aggregated from capital_events so the ledger becomes the
    forward-looking source of truth without requiring a historical backfill.
    """
    generated_at = datetime.utcnow().isoformat()
    arms: dict[str, dict] = {
        "perps": {
            "arm": "perps",
            "deployed_usd": 0.0,
            "realized_pnl_usd": 0.0,
            "deploy_events_usd": 0.0,
            "release_events_usd": 0.0,
            "open_positions": 0,
            "open_notional_usd": 0.0,
            "notes": "Uses open collateral as deployed capital and notional as context.",
        },
        "memecoins": {
            "arm": "memecoins",
            "deployed_usd": 0.0,
            "realized_pnl_usd": 0.0,
            "deploy_events_usd": 0.0,
            "release_events_usd": 0.0,
            "open_positions": 0,
            "notes": "Uses open proof/memecoin trade principal from memecoin_trades.",
        },
        "spot": {
            "arm": "spot",
            "deployed_usd": 0.0,
            "realized_pnl_usd": 0.0,
            "deploy_events_usd": 0.0,
            "release_events_usd": 0.0,
            "open_positions": 0,
            "notes": "Uses spot_holdings total_invested as deployed principal.",
        },
    }
    external = {
        "deposits_usd": 0.0,
        "withdrawals_usd": 0.0,
        "net_external_flow_usd": 0.0,
    }

    backfill_summary = backfill_capital_events()

    with get_conn() as conn:
        if _table_exists(conn, "perp_positions"):
            row = conn.execute(
                """
                SELECT
                    COUNT(*) AS open_positions,
                    COALESCE(SUM(collateral_usd), 0) AS deployed_usd,
                    COALESCE(SUM(size_usd), 0) AS open_notional_usd
                FROM perp_positions
                WHERE status='OPEN'
                """
            ).fetchone()
            if row:
                arms["perps"]["open_positions"] = int(row["open_positions"] or 0)
                arms["perps"]["deployed_usd"] = round(float(row["deployed_usd"] or 0.0), 4)
                arms["perps"]["open_notional_usd"] = round(float(row["open_notional_usd"] or 0.0), 4)

        if _table_exists(conn, "memecoin_trades"):
            row = conn.execute(
                """
                SELECT
                    COUNT(*) AS open_positions,
                    COALESCE(SUM(amount_usd), 0) AS deployed_usd
                FROM memecoin_trades
                WHERE status='OPEN'
                """
            ).fetchone()
            if row:
                arms["memecoins"]["open_positions"] = int(row["open_positions"] or 0)
                arms["memecoins"]["deployed_usd"] = round(float(row["deployed_usd"] or 0.0), 4)

        if _table_exists(conn, "spot_holdings"):
            row = conn.execute(
                """
                SELECT
                    COUNT(*) AS open_positions,
                    COALESCE(SUM(total_invested), 0) AS deployed_usd
                FROM spot_holdings
                WHERE token_amount > 0
                """
            ).fetchone()
            if row:
                arms["spot"]["open_positions"] = int(row["open_positions"] or 0)
                arms["spot"]["deployed_usd"] = round(float(row["deployed_usd"] or 0.0), 4)

        if _table_exists(conn, "capital_events"):
            rows = conn.execute(
                """
                SELECT arm, event_type, COALESCE(SUM(amount_usd), 0) AS total_amount
                FROM capital_events
                GROUP BY arm, event_type
                """
            ).fetchall()
            for row in rows:
                arm = str(row["arm"] or "").strip().lower()
                event_type = str(row["event_type"] or "").strip().upper()
                amount = round(float(row["total_amount"] or 0.0), 4)
                if event_type == "DEPOSIT":
                    external["deposits_usd"] += amount
                    continue
                if event_type == "WITHDRAWAL":
                    external["withdrawals_usd"] += amount
                    continue
                if arm not in arms:
                    continue
                if event_type == "DEPLOY":
                    arms[arm]["deploy_events_usd"] = amount
                elif event_type == "RELEASE":
                    arms[arm]["release_events_usd"] = amount
                elif event_type == "REALIZED_PNL":
                    arms[arm]["realized_pnl_usd"] = amount

    external["deposits_usd"] = round(external["deposits_usd"], 4)
    external["withdrawals_usd"] = round(external["withdrawals_usd"], 4)
    external["net_external_flow_usd"] = round(
        external["deposits_usd"] - external["withdrawals_usd"], 4
    )

    arm_rows = list(arms.values())
    totals = {
        "deployed_usd": round(sum(float(a["deployed_usd"] or 0.0) for a in arm_rows), 4),
        "realized_pnl_usd": round(sum(float(a["realized_pnl_usd"] or 0.0) for a in arm_rows), 4),
        "deploy_events_usd": round(sum(float(a["deploy_events_usd"] or 0.0) for a in arm_rows), 4),
        "release_events_usd": round(sum(float(a["release_events_usd"] or 0.0) for a in arm_rows), 4),
        "open_positions": int(sum(int(a["open_positions"] or 0) for a in arm_rows)),
    }
    ledger = {
        "total_events": 0,
        "first_event_ts": None,
        "last_event_ts": None,
        "backfill_inserted_total": int(backfill_summary.get("inserted_total", 0) or 0),
        "backfill_inserted_by_arm": backfill_summary.get("inserted", {}),
    }
    with get_conn() as conn:
        if _table_exists(conn, "capital_events"):
            row = conn.execute(
                """
                SELECT COUNT(*) AS total_events,
                       MIN(ts_utc) AS first_event_ts,
                       MAX(ts_utc) AS last_event_ts
                FROM capital_events
                """
            ).fetchone()
            if row:
                ledger["total_events"] = int(row["total_events"] or 0)
                ledger["first_event_ts"] = row["first_event_ts"]
                ledger["last_event_ts"] = row["last_event_ts"]

    return {
        "generated_at": generated_at,
        "totals": totals,
        "external_flows": external,
        "arms": arm_rows,
        "ledger": ledger,
        "recent_events": get_recent_capital_events(limit=25),
    }


def record_memecoin_exit_review(
    *,
    trade_id: int,
    mint: str,
    symbol: str,
    review_state: str,
    recommended_action: str,
    recommended_pct: float | None = None,
    should_exit: bool,
    auto_exit_enabled: bool,
    current_price: float | None = None,
    current_return_pct: float | None = None,
    age_hours: float | None = None,
    entry_proof_score: float | None = None,
    current_proof_score: float | None = None,
    proof_score_delta: float | None = None,
    current_proof_reason: str | None = None,
    entry_readiness_score: float | None = None,
    current_readiness_score: float | None = None,
    readiness_delta: float | None = None,
    current_readiness_level: str | None = None,
    entry_support_score: float | None = None,
    current_support_score: float | None = None,
    support_score_delta: float | None = None,
    entry_market_quality_score: float | None = None,
    current_market_quality_score: float | None = None,
    market_quality_score_delta: float | None = None,
    entry_market_quality_verdict: str | None = None,
    current_market_quality_verdict: str | None = None,
    entry_profit_room_score: float | None = None,
    current_profit_room_score: float | None = None,
    profit_room_score_delta: float | None = None,
    entry_profit_room_label: str | None = None,
    current_profit_room_label: str | None = None,
    entry_wallet_behavior_state: str | None = None,
    current_wallet_behavior_state: str | None = None,
    entry_smart_money_quality: str | None = None,
    current_smart_money_quality: str | None = None,
    comparison_snapshot_json: str | None = None,
    scanner_regime: str | None = None,
    exit_reason: str | None = None,
    executed: bool = False,
    intent_id: int | None = None,
    notes: str | None = None,
    ts_utc: str | None = None,
) -> int | None:
    try:
        with get_conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO memecoin_exit_reviews
                    (ts_utc, trade_id, mint, symbol, scanner_regime, current_price,
                     current_return_pct, age_hours, entry_proof_score, current_proof_score,
                     proof_score_delta, current_proof_reason,
                     entry_readiness_score, current_readiness_score, readiness_delta, current_readiness_level,
                     entry_support_score, current_support_score, support_score_delta,
                     entry_market_quality_score, current_market_quality_score, market_quality_score_delta,
                     entry_market_quality_verdict, current_market_quality_verdict,
                     entry_profit_room_score, current_profit_room_score, profit_room_score_delta,
                     entry_profit_room_label, current_profit_room_label,
                     entry_wallet_behavior_state, current_wallet_behavior_state,
                     entry_smart_money_quality, current_smart_money_quality,
                     comparison_snapshot_json,
                     review_state,
                     recommended_action, recommended_pct, exit_reason, should_exit, auto_exit_enabled,
                     executed, intent_id, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ts_utc or datetime.utcnow().isoformat(),
                    int(trade_id),
                    mint,
                    symbol,
                    scanner_regime,
                    current_price,
                    current_return_pct,
                    age_hours,
                    entry_proof_score,
                    current_proof_score,
                    proof_score_delta,
                    current_proof_reason,
                    entry_readiness_score,
                    current_readiness_score,
                    readiness_delta,
                    current_readiness_level,
                    entry_support_score,
                    current_support_score,
                    support_score_delta,
                    entry_market_quality_score,
                    current_market_quality_score,
                    market_quality_score_delta,
                    entry_market_quality_verdict,
                    current_market_quality_verdict,
                    entry_profit_room_score,
                    current_profit_room_score,
                    profit_room_score_delta,
                    entry_profit_room_label,
                    current_profit_room_label,
                    entry_wallet_behavior_state,
                    current_wallet_behavior_state,
                    entry_smart_money_quality,
                    current_smart_money_quality,
                    comparison_snapshot_json,
                    review_state,
                    recommended_action,
                    recommended_pct,
                    exit_reason,
                    1 if should_exit else 0,
                    1 if auto_exit_enabled else 0,
                    1 if executed else 0,
                    intent_id,
                    notes,
                ),
            )
            return cur.lastrowid
    except Exception:
        return None


def record_memecoin_exit_signal_snapshot(
    *,
    trade_id: int,
    mint: str,
    symbol: str,
    age_minutes: float | None = None,
    current_price: float | None = None,
    current_return_pct: float | None = None,
    current_scanner_score: float | None = None,
    current_timing_score: float | None = None,
    current_market_quality_score: float | None = None,
    current_support_score: float | None = None,
    current_readiness_score: float | None = None,
    current_profit_room_score: float | None = None,
    current_profit_room_label: str | None = None,
    current_market_quality_verdict: str | None = None,
    current_wallet_behavior_state: str | None = None,
    current_smart_money_quality: str | None = None,
    current_proof_score: float | None = None,
    current_proof_reason: str | None = None,
    volume_trend_label: str | None = None,
    scanner_still_actionable: bool = False,
    large_trade_buy_volume: float | None = None,
    large_trade_sell_volume: float | None = None,
    large_trade_buy_count: int | None = None,
    large_trade_sell_count: int | None = None,
    token_stats_ts_utc: str | None = None,
    notes_json: str | dict | list | None = None,
    ts_utc: str | None = None,
) -> int | None:
    try:
        encoded_notes = notes_json
        if notes_json is not None and not isinstance(notes_json, str):
            encoded_notes = json.dumps(notes_json, separators=(",", ":"))
        with get_conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO memecoin_exit_signal_snapshots
                    (ts_utc, trade_id, mint, symbol, age_minutes, current_price,
                     current_return_pct, current_scanner_score, current_timing_score,
                     current_market_quality_score, current_support_score, current_readiness_score,
                     current_profit_room_score, current_profit_room_label,
                     current_market_quality_verdict, current_wallet_behavior_state,
                     current_smart_money_quality, current_proof_score, current_proof_reason,
                     volume_trend_label, scanner_still_actionable,
                     large_trade_buy_volume, large_trade_sell_volume,
                     large_trade_buy_count, large_trade_sell_count,
                     token_stats_ts_utc, notes_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ts_utc or datetime.utcnow().isoformat(),
                    int(trade_id),
                    mint,
                    symbol,
                    age_minutes,
                    current_price,
                    current_return_pct,
                    current_scanner_score,
                    current_timing_score,
                    current_market_quality_score,
                    current_support_score,
                    current_readiness_score,
                    current_profit_room_score,
                    current_profit_room_label,
                    current_market_quality_verdict,
                    current_wallet_behavior_state,
                    current_smart_money_quality,
                    current_proof_score,
                    current_proof_reason,
                    volume_trend_label,
                    1 if scanner_still_actionable else 0,
                    large_trade_buy_volume,
                    large_trade_sell_volume,
                    int(large_trade_buy_count or 0),
                    int(large_trade_sell_count or 0),
                    token_stats_ts_utc,
                    encoded_notes,
                ),
            )
            return cur.lastrowid
    except Exception:
        return None


def update_memecoin_trade_excursions(
    *,
    trade_id: int,
    current_return_pct: float | None,
    ts_utc: str | None = None,
) -> bool:
    if current_return_pct is None:
        return False
    try:
        ret = float(current_return_pct)
    except Exception:
        return False

    ts_value = str(ts_utc or datetime.utcnow().isoformat())
    try:
        with get_conn() as conn:
            row = conn.execute(
                """
                SELECT max_favorable_excursion_pct, max_adverse_excursion_pct
                FROM memecoin_trades
                WHERE id = ?
                """,
                (int(trade_id),),
            ).fetchone()
            if not row:
                return False

            current_mfe = row["max_favorable_excursion_pct"]
            current_mae = row["max_adverse_excursion_pct"]
            updates: list[str] = []
            params: list = []

            if ret >= 0:
                if current_mfe is None or ret > float(current_mfe):
                    updates.extend(["max_favorable_excursion_pct = ?", "mfe_ts_utc = ?"])
                    params.extend([round(ret, 4), ts_value])
            else:
                if current_mae is None or ret < float(current_mae):
                    updates.extend(["max_adverse_excursion_pct = ?", "mae_ts_utc = ?"])
                    params.extend([round(ret, 4), ts_value])

            if not updates:
                return False

            params.append(int(trade_id))
            conn.execute(
                f"UPDATE memecoin_trades SET {', '.join(updates)} WHERE id = ?",
                params,
            )
        return True
    except Exception:
        return False


def record_memecoin_trade_quality_snapshots(rows: list[dict]) -> int:
    if not rows:
        return 0
    inserted = 0
    try:
        with get_conn() as conn:
            _ensure_trade_quality_tables(conn)
            for row in rows:
                conn.execute(
                    """
                    INSERT INTO memecoin_trade_quality_snapshots
                        (mint, symbol, ts_utc, liquidity_quality_score, trade_quality_score,
                         market_integrity_score, execution_quality_score, quality_verdict,
                         reasons_json, inputs_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(row.get("mint") or ""),
                        str(row.get("symbol") or ""),
                        str(row.get("ts_utc") or datetime.utcnow().isoformat()),
                        float(row.get("liquidity_quality_score") or 0.0),
                        float(row.get("trade_quality_score") or 0.0),
                        float(row.get("market_integrity_score") or 0.0),
                        float(row.get("execution_quality_score") or 0.0),
                        str(row.get("quality_verdict") or "WATCH"),
                        json.dumps(list(row.get("reasons") or [])),
                        json.dumps(dict(row.get("inputs") or {})),
                    ),
                )
                inserted += 1
    except Exception:
        return inserted
    return inserted


def record_memecoin_token_stats_snapshots(
    rows: list[dict],
    *,
    min_interval_seconds: int = 120,
) -> int:
    if not rows:
        return 0
    inserted = 0
    try:
        with get_conn() as conn:
            _ensure_token_stats_tables(conn)
            for row in rows:
                mint = str(row.get("mint") or "").strip()
                if not mint:
                    continue
                ts_utc = str(row.get("ts_utc") or datetime.utcnow().isoformat())
                latest = conn.execute(
                    """
                    SELECT ts_utc, price, volume_1h_usd, trade_1h, unique_wallet_1h
                    FROM memecoin_token_stats_snapshots
                    WHERE mint = ?
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    (mint,),
                ).fetchone()
                should_insert = True
                if latest:
                    latest_ts = str(latest["ts_utc"] or "")
                    try:
                        latest_dt = datetime.fromisoformat(latest_ts.replace("Z", "+00:00"))
                        if latest_dt.tzinfo is None:
                            latest_dt = latest_dt.replace(tzinfo=timezone.utc)
                    except Exception:
                        latest_dt = None
                    if latest_dt is not None:
                        age_seconds = (datetime.now(timezone.utc) - latest_dt.astimezone(timezone.utc)).total_seconds()
                        should_insert = age_seconds >= max(30, int(min_interval_seconds))
                    if not should_insert:
                        latest_price = float(latest["price"] or 0.0)
                        current_price = float(row.get("price") or 0.0)
                        latest_volume = float(latest["volume_1h_usd"] or 0.0)
                        current_volume = float(row.get("volume_1h_usd") or 0.0)
                        latest_trades = int(latest["trade_1h"] or 0)
                        current_trades = int(row.get("trade_1h") or 0)
                        latest_wallets = int(latest["unique_wallet_1h"] or 0)
                        current_wallets = int(row.get("unique_wallet_1h") or 0)
                        price_move_pct = abs(((current_price - latest_price) / latest_price) * 100.0) if latest_price > 0 and current_price > 0 else 0.0
                        volume_move_pct = abs(((current_volume - latest_volume) / latest_volume) * 100.0) if latest_volume > 0 and current_volume > 0 else 0.0
                        should_insert = (
                            price_move_pct >= 2.0
                            or volume_move_pct >= 20.0
                            or abs(current_trades - latest_trades) >= 50
                            or abs(current_wallets - latest_wallets) >= 25
                        )
                if not should_insert:
                    continue
                conn.execute(
                    """
                    INSERT INTO memecoin_token_stats_snapshots
                        (mint, symbol, ts_utc, price, liquidity, marketcap, fdv,
                         last_trade_unix_time, volume_30m_usd, volume_1h_usd, volume_24h_usd,
                         volume_buy_1h_usd, volume_sell_1h_usd, trade_1h, buy_1h, sell_1h,
                         unique_wallet_1h, price_change_30m_percent, price_change_1h_percent,
                         price_change_24h_percent, volume_1h_change_percent,
                         trade_1h_change_percent, reasons_json, inputs_json, raw_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        mint,
                        str(row.get("symbol") or "") or None,
                        ts_utc,
                        float(row.get("price") or 0.0),
                        float(row.get("liquidity") or 0.0),
                        float(row.get("marketcap") or 0.0),
                        float(row.get("fdv") or 0.0),
                        int(row.get("last_trade_unix_time") or 0) or None,
                        float(row.get("volume_30m_usd") or 0.0),
                        float(row.get("volume_1h_usd") or 0.0),
                        float(row.get("volume_24h_usd") or 0.0),
                        float(row.get("volume_buy_1h_usd") or 0.0),
                        float(row.get("volume_sell_1h_usd") or 0.0),
                        int(row.get("trade_1h") or 0),
                        int(row.get("buy_1h") or 0),
                        int(row.get("sell_1h") or 0),
                        int(row.get("unique_wallet_1h") or 0),
                        float(row.get("price_change_30m_percent") or 0.0),
                        float(row.get("price_change_1h_percent") or 0.0),
                        float(row.get("price_change_24h_percent") or 0.0),
                        float(row.get("volume_1h_change_percent") or 0.0),
                        float(row.get("trade_1h_change_percent") or 0.0),
                        json.dumps(list(row.get("reasons") or []), separators=(",", ":")),
                        json.dumps(dict(row.get("inputs") or {}), separators=(",", ":")),
                        json.dumps(dict(row.get("raw") or {}), separators=(",", ":")),
                    ),
                )
                inserted += 1
    except Exception:
        return inserted
    return inserted


def record_memecoin_large_trade_snapshots(rows: list[dict]) -> int:
    if not rows:
        return 0
    inserted = 0
    try:
        with get_conn() as conn:
            _ensure_large_trade_tables(conn)
            for row in rows:
                mint = str(row.get("mint") or "").strip()
                tx_hash = str(row.get("tx_hash") or "").strip()
                if not mint or not tx_hash:
                    continue
                conn.execute(
                    """
                    INSERT OR IGNORE INTO memecoin_large_trade_snapshots
                        (mint, symbol, ts_utc, trade_side, volume_usd, owner, source, tx_hash,
                         pool_address, counterparty_symbol, counterparty_address, token_amount,
                         token_price, sponsorship_label, reasons_json, raw_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        mint,
                        str(row.get("symbol") or "") or None,
                        str(row.get("ts_utc") or datetime.utcnow().isoformat()),
                        str(row.get("trade_side") or "") or None,
                        float(row.get("volume_usd") or 0.0),
                        str(row.get("owner") or "") or None,
                        str(row.get("source") or "") or None,
                        tx_hash,
                        str(row.get("pool_address") or "") or None,
                        str(row.get("counterparty_symbol") or "") or None,
                        str(row.get("counterparty_address") or "") or None,
                        float(row.get("token_amount") or 0.0),
                        float(row.get("token_price") or 0.0),
                        str(row.get("sponsorship_label") or "") or None,
                        json.dumps(list(row.get("reasons") or []), separators=(",", ":")),
                        json.dumps(dict(row.get("raw") or {}), separators=(",", ":")),
                    ),
                )
                inserted += int(conn.execute("SELECT changes()").fetchone()[0] or 0)
    except Exception:
        return inserted
    return inserted


def get_latest_memecoin_token_stats_for_mints(
    mints: list[str],
    *,
    max_age_minutes: int | None = None,
) -> dict[str, dict]:
    clean_mints = [str(m or "").strip() for m in mints if str(m or "").strip()]
    if not clean_mints:
        return {}
    try:
        with get_conn() as conn:
            _ensure_token_stats_tables(conn)
            placeholders = ",".join("?" for _ in clean_mints)
            params: list = list(clean_mints)
            age_clause = ""
            if max_age_minutes is not None:
                age_clause = "AND ts_utc >= datetime('now', ?)"
                params.append(f"-{max(1, int(max_age_minutes))} minutes")
            rows = conn.execute(
                f"""
                WITH latest AS (
                    SELECT mint, MAX(id) AS max_id
                    FROM memecoin_token_stats_snapshots
                    WHERE mint IN ({placeholders})
                    {age_clause}
                    GROUP BY mint
                )
                SELECT s.*
                FROM memecoin_token_stats_snapshots s
                INNER JOIN latest l ON l.max_id = s.id
                """,
                params,
            ).fetchall()
        out: dict[str, dict] = {}
        for row in rows:
            item = dict(row)
            try:
                item["reasons"] = json.loads(item.pop("reasons_json") or "[]")
            except Exception:
                item["reasons"] = []
                item.pop("reasons_json", None)
            try:
                item["inputs"] = json.loads(item.pop("inputs_json") or "{}")
            except Exception:
                item["inputs"] = {}
                item.pop("inputs_json", None)
            try:
                item["raw"] = json.loads(item.pop("raw_json") or "{}")
            except Exception:
                item["raw"] = {}
                item.pop("raw_json", None)
            out[str(item.get("mint") or "")] = item
        return out
    except Exception:
        return {}


def get_recent_large_trade_support_for_mints(
    mints: list[str],
    *,
    max_age_minutes: int = 180,
) -> dict[str, dict]:
    clean_mints = [str(m or "").strip() for m in mints if str(m or "").strip()]
    if not clean_mints:
        return {}
    try:
        with get_conn() as conn:
            _ensure_large_trade_tables(conn)
            placeholders = ",".join("?" for _ in clean_mints)
            params = list(clean_mints) + [f"-{max(5, int(max_age_minutes))} minutes"]
            rows = conn.execute(
                f"""
                SELECT
                    mint,
                    COUNT(*) AS trade_count,
                    COUNT(DISTINCT owner) AS unique_wallets,
                    SUM(CASE WHEN UPPER(COALESCE(trade_side, 'BUY'))='BUY' THEN 1 ELSE 0 END) AS buy_count,
                    SUM(CASE WHEN UPPER(COALESCE(trade_side, 'BUY'))='SELL' THEN 1 ELSE 0 END) AS sell_count,
                    SUM(CASE WHEN UPPER(COALESCE(trade_side, 'BUY'))='BUY' THEN COALESCE(volume_usd, 0) ELSE 0 END) AS buy_volume_usd,
                    SUM(CASE WHEN UPPER(COALESCE(trade_side, 'BUY'))='SELL' THEN COALESCE(volume_usd, 0) ELSE 0 END) AS sell_volume_usd,
                    MAX(COALESCE(volume_usd, 0)) AS max_trade_volume_usd,
                    MAX(ts_utc) AS last_seen_utc,
                    SUM(CASE WHEN UPPER(COALESCE(sponsorship_label, ''))='STRONG' THEN 1 ELSE 0 END) AS strong_labels
                FROM memecoin_large_trade_snapshots
                WHERE mint IN ({placeholders})
                  AND ts_utc >= datetime('now', ?)
                GROUP BY mint
                """,
                params,
            ).fetchall()
    except Exception:
        return {}

    out: dict[str, dict] = {}
    for row in rows:
        buy_volume = float(row["buy_volume_usd"] or 0.0)
        sell_volume = float(row["sell_volume_usd"] or 0.0)
        net_volume = buy_volume - sell_volume
        total_volume = buy_volume + sell_volume
        buy_share = (buy_volume / total_volume * 100.0) if total_volume > 0 else 50.0
        out[str(row["mint"] or "")] = {
            "trade_count": int(row["trade_count"] or 0),
            "unique_wallets": int(row["unique_wallets"] or 0),
            "buy_count": int(row["buy_count"] or 0),
            "sell_count": int(row["sell_count"] or 0),
            "buy_volume_usd": round(buy_volume, 2),
            "sell_volume_usd": round(sell_volume, 2),
            "net_volume_usd": round(net_volume, 2),
            "buy_share_pct": round(buy_share, 1),
            "max_trade_volume_usd": round(float(row["max_trade_volume_usd"] or 0.0), 2),
            "last_seen_utc": str(row["last_seen_utc"] or ""),
            "strong_labels": int(row["strong_labels"] or 0),
        }
    return out


def get_latest_memecoin_exit_reviews(trade_ids: list[int] | None = None) -> dict[int, dict]:
    try:
        with get_conn() as conn:
            if not _table_exists(conn, "memecoin_exit_reviews"):
                return {}
            if trade_ids:
                placeholders = ",".join("?" for _ in trade_ids)
                rows = conn.execute(
                    f"""
                    SELECT r.*
                    FROM memecoin_exit_reviews r
                    INNER JOIN (
                        SELECT trade_id, MAX(id) AS max_id
                        FROM memecoin_exit_reviews
                        WHERE trade_id IN ({placeholders})
                        GROUP BY trade_id
                    ) latest ON latest.max_id = r.id
                    """,
                    tuple(int(tid) for tid in trade_ids),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT r.*
                    FROM memecoin_exit_reviews r
                    INNER JOIN (
                        SELECT trade_id, MAX(id) AS max_id
                        FROM memecoin_exit_reviews
                        GROUP BY trade_id
                    ) latest ON latest.max_id = r.id
                    """
                ).fetchall()
        return {int(row["trade_id"]): dict(row) for row in rows}
    except Exception:
        return {}


def get_memecoin_outcome_audit(window_days: int = 90) -> dict:
    def _safe_float(value):
        try:
            return float(value) if value is not None else None
        except Exception:
            return None

    def _profit_room_label(row: dict) -> str:
        label = str(row.get("entry_profit_room_label") or "").strip().upper()
        return label or "UNKNOWN"

    def _readiness_band(row: dict) -> str:
        score = _safe_float(row.get("entry_readiness_score"))
        if score is None:
            return "UNKNOWN"
        if score >= 80.0:
            return "READY"
        if score >= 60.0:
            return "NEARLY_READY"
        if score >= 40.0:
            return "DEVELOPING"
        return "EARLY"

    def _support_band(row: dict) -> str:
        score = _safe_float(row.get("entry_support_score"))
        if score is None:
            return "UNKNOWN"
        if score >= 75.0:
            return "STRONG"
        if score >= 45.0:
            return "MODERATE"
        if score >= 20.0:
            return "LIGHT"
        return "NONE"

    def _market_verdict(row: dict) -> str:
        verdict = str(row.get("entry_market_quality_verdict") or "").strip().upper()
        return verdict or "UNKNOWN"

    def _summarize(rows: list[dict], key_name: str, label_order: list[str] | None = None) -> list[dict]:
        buckets: dict[str, list[dict]] = {}
        for row in rows:
            label = str(row.get(key_name) or "UNKNOWN")
            buckets.setdefault(label, []).append(row)
        labels = label_order or sorted(buckets.keys())
        if label_order:
            labels += [label for label in sorted(buckets.keys()) if label not in label_order]
        summary: list[dict] = []
        for label in labels:
            group = buckets.get(label) or []
            if not group:
                continue
            pnl_pcts = [_safe_float(r.get("pnl_pct")) for r in group]
            pnl_pcts = [v for v in pnl_pcts if v is not None]
            pnl_usd = [_safe_float(r.get("pnl_usd")) for r in group]
            pnl_usd = [v for v in pnl_usd if v is not None]
            wins = sum(1 for v in pnl_pcts if v > 0)
            summary.append({
                "label": label,
                "count": len(group),
                "win_rate": round((wins / len(pnl_pcts) * 100.0), 1) if pnl_pcts else None,
                "avg_pnl_pct": round(sum(pnl_pcts) / len(pnl_pcts), 2) if pnl_pcts else None,
                "avg_pnl_usd": round(sum(pnl_usd) / len(pnl_usd), 4) if pnl_usd else None,
            })
        return summary

    try:
        with get_conn() as conn:
            if not _table_exists(conn, "memecoin_trades"):
                return {"summary": {"closed_trades": 0, "attributed_trades": 0}, "groups": {}, "review_drift": {}}

            trade_rows = conn.execute(
                """
                SELECT id, symbol, mint, opened_ts_utc, closed_ts_utc, pnl_pct, pnl_usd,
                       entry_readiness_score, entry_support_score, entry_market_quality_score,
                       entry_market_quality_verdict, entry_profit_room_score, entry_profit_room_label
                FROM memecoin_trades
                WHERE status='CLOSED'
                  AND closed_ts_utc >= datetime('now', ?)
                ORDER BY closed_ts_utc DESC, id DESC
                """,
                (f"-{max(7, int(window_days))} days",),
            ).fetchall()
            closed = [dict(r) for r in trade_rows]
            attributed = [
                row for row in closed
                if any(
                    row.get(field) is not None
                    for field in (
                        "entry_readiness_score",
                        "entry_support_score",
                        "entry_market_quality_score",
                        "entry_profit_room_score",
                    )
                )
            ]

            normalized: list[dict] = []
            for row in attributed:
                normalized.append({
                    **row,
                    "profit_room_group": _profit_room_label(row),
                    "readiness_group": _readiness_band(row),
                    "support_group": _support_band(row),
                    "market_group": _market_verdict(row),
                })

            review_drift = {
                "reviewed_trades": 0,
                "avg_readiness_delta": None,
                "avg_support_delta": None,
                "avg_market_quality_delta": None,
                "avg_profit_room_delta": None,
                "readiness_fades": 0,
                "support_fades": 0,
                "market_quality_fades": 0,
                "profit_room_fades": 0,
            }
            if _table_exists(conn, "memecoin_exit_reviews") and attributed:
                trade_ids = [int(r["id"]) for r in attributed]
                placeholders = ",".join("?" for _ in trade_ids)
                review_rows = conn.execute(
                    f"""
                    SELECT r.*
                    FROM memecoin_exit_reviews r
                    INNER JOIN (
                        SELECT trade_id, MAX(id) AS max_id
                        FROM memecoin_exit_reviews
                        WHERE trade_id IN ({placeholders})
                        GROUP BY trade_id
                    ) latest
                      ON latest.trade_id = r.trade_id
                     AND latest.max_id = r.id
                    """,
                    tuple(trade_ids),
                ).fetchall()
                latest_reviews = [dict(r) for r in review_rows]
                if latest_reviews:
                    def _avg(field: str):
                        vals = [_safe_float(r.get(field)) for r in latest_reviews]
                        vals = [v for v in vals if v is not None]
                        return round(sum(vals) / len(vals), 2) if vals else None
                    review_drift = {
                        "reviewed_trades": len(latest_reviews),
                        "avg_readiness_delta": _avg("readiness_delta"),
                        "avg_support_delta": _avg("support_score_delta"),
                        "avg_market_quality_delta": _avg("market_quality_score_delta"),
                        "avg_profit_room_delta": _avg("profit_room_score_delta"),
                        "readiness_fades": sum(1 for r in latest_reviews if (_safe_float(r.get("readiness_delta")) or 0.0) < 0),
                        "support_fades": sum(1 for r in latest_reviews if (_safe_float(r.get("support_score_delta")) or 0.0) < 0),
                        "market_quality_fades": sum(1 for r in latest_reviews if (_safe_float(r.get("market_quality_score_delta")) or 0.0) < 0),
                        "profit_room_fades": sum(1 for r in latest_reviews if (_safe_float(r.get("profit_room_score_delta")) or 0.0) < 0),
                    }

            return {
                "summary": {
                    "window_days": max(7, int(window_days)),
                    "closed_trades": len(closed),
                    "attributed_trades": len(attributed),
                    "attribution_coverage_pct": round((len(attributed) / len(closed) * 100.0), 1) if closed else 0.0,
                },
                "groups": {
                    "profit_room": _summarize(normalized, "profit_room_group", ["EARLY", "WORKABLE", "STRETCHED", "TOO_LATE", "UNKNOWN"]),
                    "readiness": _summarize(normalized, "readiness_group", ["READY", "NEARLY_READY", "DEVELOPING", "EARLY", "UNKNOWN"]),
                    "market_quality": _summarize(normalized, "market_group", ["CLEAN", "WATCH", "UNSTABLE", "AVOID", "UNKNOWN"]),
                    "support": _summarize(normalized, "support_group", ["STRONG", "MODERATE", "LIGHT", "NONE", "UNKNOWN"]),
                },
                "review_drift": review_drift,
                "recent_examples": [
                    {
                        "trade_id": int(row["id"]),
                        "symbol": str(row.get("symbol") or ""),
                        "pnl_pct": _safe_float(row.get("pnl_pct")),
                        "profit_room_label": row.get("profit_room_group"),
                        "readiness_band": row.get("readiness_group"),
                        "market_quality_verdict": row.get("market_group"),
                        "support_band": row.get("support_group"),
                    }
                    for row in normalized[:10]
                ],
            }
    except Exception:
        return {"summary": {"closed_trades": 0, "attributed_trades": 0}, "groups": {}, "review_drift": {}}


def get_memecoin_outcome_insights(window_days: int = 90, min_trades: int = 5) -> dict:
    def _safe_float(value):
        try:
            return float(value) if value is not None else None
        except Exception:
            return None

    audit = get_memecoin_outcome_audit(window_days=max(7, int(window_days)))
    summary = audit.get("summary") or {}
    groups = audit.get("groups") or {}
    review_drift = audit.get("review_drift") or {}
    attributed_trades = int(summary.get("attributed_trades") or 0)
    closed_trades = int(summary.get("closed_trades") or 0)
    min_required = max(3, int(min_trades or 5))

    payload = {
        "window_days": int(summary.get("window_days") or max(7, int(window_days))),
        "closed_trades": closed_trades,
        "attributed_trades": attributed_trades,
        "attribution_coverage_pct": _safe_float(summary.get("attribution_coverage_pct")) or 0.0,
        "status": "READY" if attributed_trades >= min_required else "THIN",
        "minimum_required": min_required,
        "headline": (
            "Outcome insights are ready."
            if attributed_trades >= min_required
            else f"Need {min_required} attributed closed trades before outcome lessons are trustworthy."
        ),
        "highlights": [],
    }

    if attributed_trades < min_required:
        return payload

    def _find_best(rows: list[dict], min_count: int = 2, prefer_high: bool = True) -> dict | None:
        eligible = []
        for row in rows or []:
            count = int(row.get("count") or 0)
            avg_pnl = _safe_float(row.get("avg_pnl_pct"))
            if count < min_count or avg_pnl is None:
                continue
            eligible.append(row)
        if not eligible:
            return None
        return sorted(
            eligible,
            key=lambda row: (
                _safe_float(row.get("avg_pnl_pct")) if prefer_high else -(_safe_float(row.get("avg_pnl_pct")) or 0.0),
                _safe_float(row.get("win_rate")) or 0.0,
                int(row.get("count") or 0),
            ),
            reverse=True,
        )[0]

    def _find_worst(rows: list[dict], min_count: int = 2) -> dict | None:
        eligible = []
        for row in rows or []:
            count = int(row.get("count") or 0)
            avg_pnl = _safe_float(row.get("avg_pnl_pct"))
            if count < min_count or avg_pnl is None:
                continue
            eligible.append(row)
        if not eligible:
            return None
        return sorted(
            eligible,
            key=lambda row: (
                _safe_float(row.get("avg_pnl_pct")) or 0.0,
                _safe_float(row.get("win_rate")) or 0.0,
                -int(row.get("count") or 0),
            ),
        )[0]

    def _format_group_detail(prefix: str, row: dict) -> str:
        count = int(row.get("count") or 0)
        win_rate = _safe_float(row.get("win_rate"))
        avg_pnl = _safe_float(row.get("avg_pnl_pct"))
        bits = [prefix]
        if win_rate is not None:
            bits.append(f"{win_rate:.1f}% win rate")
        if avg_pnl is not None:
            bits.append(f"{avg_pnl:+.2f}% avg pnl")
        bits.append(f"n={count}")
        return " | ".join(bits)

    profit_rows = groups.get("profit_room") or []
    best_profit = _find_best(profit_rows, min_count=2)
    if best_profit and str(best_profit.get("label") or "").upper() != "UNKNOWN":
        payload["highlights"].append({
            "kind": "profit_room",
            "title": f"{best_profit['label']} entries are leading",
            "detail": _format_group_detail("Profit room cohort", best_profit),
            "metric": round(_safe_float(best_profit.get("avg_pnl_pct")) or 0.0, 2),
            "confidence": "medium" if int(best_profit.get("count") or 0) < 5 else "high",
        })

    readiness_rows = groups.get("readiness") or []
    best_readiness = _find_best(readiness_rows, min_count=2)
    if best_readiness and str(best_readiness.get("label") or "").upper() != "UNKNOWN":
        payload["highlights"].append({
            "kind": "readiness",
            "title": f"{best_readiness['label']} setups are paying best",
            "detail": _format_group_detail("Readiness cohort", best_readiness),
            "metric": round(_safe_float(best_readiness.get("avg_pnl_pct")) or 0.0, 2),
            "confidence": "medium" if int(best_readiness.get("count") or 0) < 5 else "high",
        })

    worst_profit = _find_worst(profit_rows, min_count=2)
    if worst_profit and str(worst_profit.get("label") or "").upper() not in ("UNKNOWN", ""):
        worst_avg = _safe_float(worst_profit.get("avg_pnl_pct"))
        if worst_avg is not None and worst_avg < 0:
            payload["highlights"].append({
                "kind": "risk",
                "title": f"{worst_profit['label']} entries are dragging returns",
                "detail": _format_group_detail("Weakest profit-room cohort", worst_profit),
                "metric": round(worst_avg, 2),
                "confidence": "medium" if int(worst_profit.get("count") or 0) < 5 else "high",
            })

    fade_candidates = [
        ("readiness", _safe_float(review_drift.get("avg_readiness_delta"))),
        ("support", _safe_float(review_drift.get("avg_support_delta"))),
        ("market quality", _safe_float(review_drift.get("avg_market_quality_delta"))),
        ("profit room", _safe_float(review_drift.get("avg_profit_room_delta"))),
    ]
    reviewed = int(review_drift.get("reviewed_trades") or 0)
    strongest_fade = sorted(
        [item for item in fade_candidates if item[1] is not None],
        key=lambda item: item[1],
    )
    if reviewed >= 2 and strongest_fade:
        fade_name, fade_value = strongest_fade[0]
        if fade_value is not None and fade_value < 0:
            payload["highlights"].append({
                "kind": "drift",
                "title": f"{fade_name.title()} is fading before exits",
                "detail": f"Latest reviews show average {fade_name} drift of {fade_value:+.2f} across {reviewed} reviewed trades.",
                "metric": round(fade_value, 2),
                "confidence": "medium" if reviewed < 5 else "high",
            })

    payload["highlights"] = payload["highlights"][:3]
    if not payload["highlights"]:
        payload["headline"] = "Attribution is accumulating, but there is not yet a clear edge signal."
    return payload


def get_memecoin_calibration_view(window_days: int = 90, limit: int = 25) -> dict:
    def _safe_float(value):
        try:
            return float(value) if value is not None else None
        except Exception:
            return None

    def _avg(values: list[float | None], digits: int = 2) -> float | None:
        usable = [float(v) for v in values if v is not None]
        return round(sum(usable) / len(usable), digits) if usable else None

    def _entry_timing_label(row: dict) -> str:
        label = str(row.get("entry_timing_bucket") or "").strip().upper()
        return label or "UNKNOWN"

    def _window_phase_label(row: dict) -> str:
        label = str(row.get("signal_window_phase") or "").strip().upper()
        return label or "UNKNOWN"

    def _profit_room_label(row: dict) -> str:
        label = str(row.get("entry_profit_room_label") or "").strip().upper()
        return label or "UNKNOWN"

    def _regime_label(row: dict) -> str:
        label = str(row.get("entry_regime_label") or "").strip().upper()
        return label or "UNKNOWN"

    def _capture_ratio(row: dict) -> float | None:
        pnl_pct = _safe_float(row.get("pnl_pct"))
        mfe_pct = _safe_float(row.get("max_favorable_excursion_pct"))
        if pnl_pct is None or mfe_pct is None or mfe_pct <= 0:
            return None
        return round((pnl_pct / mfe_pct) * 100.0, 1)

    def _latency_bucket(row: dict) -> str:
        minutes = _safe_float(row.get("minutes_scan_to_entry"))
        if minutes is None:
            return "UNKNOWN"
        if minutes <= 15.0:
            return "IMMEDIATE"
        if minutes <= 60.0:
            return "FAST"
        if minutes <= 240.0:
            return "LATE"
        return "VERY_LATE"

    def _summarize(rows: list[dict], key_name: str, label_order: list[str] | None = None) -> list[dict]:
        buckets: dict[str, list[dict]] = {}
        for row in rows:
            label = str(row.get(key_name) or "UNKNOWN")
            buckets.setdefault(label, []).append(row)
        labels = label_order or sorted(buckets.keys())
        if label_order:
            labels += [label for label in sorted(buckets.keys()) if label not in label_order]
        summary: list[dict] = []
        for label in labels:
            group = buckets.get(label) or []
            if not group:
                continue
            pnl_pcts = [_safe_float(r.get("pnl_pct")) for r in group]
            pnl_pcts = [v for v in pnl_pcts if v is not None]
            wins = sum(1 for v in pnl_pcts if v > 0)
            summary.append({
                "label": label,
                "count": len(group),
                "win_rate": round((wins / len(pnl_pcts) * 100.0), 1) if pnl_pcts else None,
                "avg_pnl_pct": _avg([_safe_float(r.get("pnl_pct")) for r in group]),
                "avg_latency_minutes": _avg([_safe_float(r.get("minutes_scan_to_entry")) for r in group]),
                "avg_move_cost_pct": _avg([_safe_float(r.get("pct_move_scan_to_entry")) for r in group]),
                "avg_mfe_pct": _avg([_safe_float(r.get("max_favorable_excursion_pct")) for r in group]),
                "avg_mae_pct": _avg([_safe_float(r.get("max_adverse_excursion_pct")) for r in group]),
                "avg_capture_ratio_pct": _avg([_capture_ratio(r) for r in group], digits=1),
            })
        return summary

    def _find_group(rows: list[dict], label: str) -> dict | None:
        label = str(label or "").upper()
        for row in rows or []:
            if str(row.get("label") or "").upper() == label:
                return row
        return None

    try:
        with get_conn() as conn:
            if not _table_exists(conn, "memecoin_trades"):
                return {
                    "summary": {"window_days": max(7, int(window_days)), "closed_trades": 0, "calibrated_trades": 0},
                    "overview": {},
                    "groups": {},
                    "recent_trades": [],
                    "insights": [],
                }

            trade_rows = conn.execute(
                """
                SELECT id, symbol, mint, opened_ts_utc, closed_ts_utc, exit_reason,
                       pnl_pct, pnl_usd,
                       entry_profit_room_label, entry_regime_label,
                       minutes_scan_to_entry, pct_move_scan_to_entry,
                       entry_timing_bucket, signal_window_phase,
                       max_favorable_excursion_pct, max_adverse_excursion_pct
                FROM memecoin_trades
                WHERE status='CLOSED'
                  AND closed_ts_utc >= datetime('now', ?)
                ORDER BY closed_ts_utc DESC, id DESC
                """,
                (f"-{max(7, int(window_days))} days",),
            ).fetchall()
            closed = [dict(r) for r in trade_rows]
            calibrated = []
            for row in closed:
                if any(
                    row.get(field) is not None
                    for field in (
                        "minutes_scan_to_entry",
                        "pct_move_scan_to_entry",
                        "entry_timing_bucket",
                        "signal_window_phase",
                        "max_favorable_excursion_pct",
                        "max_adverse_excursion_pct",
                    )
                ):
                    calibrated.append({
                        **row,
                        "entry_timing_group": _entry_timing_label(row),
                        "window_phase_group": _window_phase_label(row),
                        "profit_room_group": _profit_room_label(row),
                        "regime_group": _regime_label(row),
                        "latency_group": _latency_bucket(row),
                        "capture_ratio_pct": _capture_ratio(row),
                    })

            timing_groups = _summarize(calibrated, "entry_timing_group", ["IMMEDIATE", "FAST", "LATE", "VERY_LATE", "UNKNOWN"])
            phase_groups = _summarize(calibrated, "window_phase_group", ["EARLY", "MID", "LATE", "EXHAUSTED", "UNKNOWN"])
            profit_groups = _summarize(calibrated, "profit_room_group", ["EARLY", "WORKABLE", "STRETCHED", "TOO_LATE", "UNKNOWN"])
            regime_groups = _summarize(calibrated, "regime_group", ["BULL", "RISK_ON", "TRANSITION", "RISK_OFF", "BEAR", "UNKNOWN"])

            immediate_group = _find_group(timing_groups, "IMMEDIATE")
            late_group = _find_group(timing_groups, "LATE")
            very_late_group = _find_group(timing_groups, "VERY_LATE")
            early_phase_group = _find_group(phase_groups, "EARLY")
            exhausted_phase_group = _find_group(phase_groups, "EXHAUSTED")

            insights: list[dict] = []
            if immediate_group and late_group:
                fast_avg = _safe_float(immediate_group.get("avg_pnl_pct"))
                late_avg = _safe_float(late_group.get("avg_pnl_pct"))
                if fast_avg is not None and late_avg is not None:
                    insights.append({
                        "kind": "timing_gap",
                        "title": "Entry timing gap",
                        "detail": f"IMMEDIATE trades average {fast_avg:+.2f}% vs LATE trades at {late_avg:+.2f}%.",
                        "metric": round(fast_avg - late_avg, 2),
                    })
            if early_phase_group and exhausted_phase_group:
                early_avg = _safe_float(early_phase_group.get("avg_pnl_pct"))
                exhausted_avg = _safe_float(exhausted_phase_group.get("avg_pnl_pct"))
                if early_avg is not None and exhausted_avg is not None:
                    insights.append({
                        "kind": "window_phase",
                        "title": "Window phase edge",
                        "detail": f"EARLY window entries average {early_avg:+.2f}% vs EXHAUSTED entries at {exhausted_avg:+.2f}%.",
                        "metric": round(early_avg - exhausted_avg, 2),
                    })

            capture_values = [_capture_ratio(row) for row in calibrated]
            avg_capture_ratio = _avg(capture_values, digits=1)
            avg_move_cost_pct = _avg([_safe_float(row.get("pct_move_scan_to_entry")) for row in calibrated])
            avg_latency_minutes = _avg([_safe_float(row.get("minutes_scan_to_entry")) for row in calibrated])
            avg_mfe_pct = _avg([_safe_float(row.get("max_favorable_excursion_pct")) for row in calibrated])
            avg_mae_pct = _avg([_safe_float(row.get("max_adverse_excursion_pct")) for row in calibrated])

            return {
                "summary": {
                    "window_days": max(7, int(window_days)),
                    "closed_trades": len(closed),
                    "calibrated_trades": len(calibrated),
                    "calibration_coverage_pct": round((len(calibrated) / len(closed) * 100.0), 1) if closed else 0.0,
                },
                "overview": {
                    "avg_latency_minutes": avg_latency_minutes,
                    "avg_move_cost_pct": avg_move_cost_pct,
                    "avg_mfe_pct": avg_mfe_pct,
                    "avg_mae_pct": avg_mae_pct,
                    "avg_capture_ratio_pct": avg_capture_ratio,
                },
                "groups": {
                    "entry_timing": timing_groups,
                    "signal_window_phase": phase_groups,
                    "profit_room": profit_groups,
                    "regime": regime_groups,
                },
                "recent_trades": [
                    {
                        "trade_id": int(row["id"]),
                        "symbol": str(row.get("symbol") or ""),
                        "pnl_pct": _safe_float(row.get("pnl_pct")),
                        "entry_timing_bucket": row.get("entry_timing_group"),
                        "signal_window_phase": row.get("window_phase_group"),
                        "profit_room_label": row.get("profit_room_group"),
                        "regime_label": row.get("regime_group"),
                        "minutes_scan_to_entry": _safe_float(row.get("minutes_scan_to_entry")),
                        "pct_move_scan_to_entry": _safe_float(row.get("pct_move_scan_to_entry")),
                        "max_favorable_excursion_pct": _safe_float(row.get("max_favorable_excursion_pct")),
                        "max_adverse_excursion_pct": _safe_float(row.get("max_adverse_excursion_pct")),
                        "capture_ratio_pct": row.get("capture_ratio_pct"),
                        "exit_reason": row.get("exit_reason"),
                    }
                    for row in calibrated[: max(5, min(int(limit or 25), 50))]
                ],
                "insights": insights[:3],
            }
    except Exception:
        return {
            "summary": {"window_days": max(7, int(window_days)), "closed_trades": 0, "calibrated_trades": 0},
            "overview": {},
            "groups": {},
            "recent_trades": [],
            "insights": [],
        }


def get_recent_memecoin_exit_review_history(
    trade_ids: list[int] | None = None,
    limit_per_trade: int = 5,
) -> dict[int, list[dict]]:
    try:
        with get_conn() as conn:
            if not _table_exists(conn, "memecoin_exit_reviews"):
                return {}
            rows = []
            if trade_ids:
                placeholders = ",".join("?" for _ in trade_ids)
                rows = conn.execute(
                    f"""
                    SELECT *
                    FROM memecoin_exit_reviews
                    WHERE trade_id IN ({placeholders})
                    ORDER BY trade_id ASC, id DESC
                    """,
                    tuple(int(tid) for tid in trade_ids),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT *
                    FROM memecoin_exit_reviews
                    ORDER BY trade_id ASC, id DESC
                    """
                ).fetchall()

        out: dict[int, list[dict]] = {}
        for row in rows:
            trade_id = int(row["trade_id"])
            bucket = out.setdefault(trade_id, [])
            if len(bucket) >= max(1, int(limit_per_trade)):
                continue
            bucket.append(dict(row))
        return out
    except Exception:
        return {}
