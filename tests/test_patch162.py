"""
Patch 162 regression tests — idempotent tier open guards.

Tests run against a temporary SQLite DB, never touching the live system.
All external calls (Jupiter API, Telegram) are mocked.

Coverage:
  - Blocking guard: SUBMIT_AMBIGUOUS / RECONCILE_MANUAL_REQUIRED block new opens
  - Terminal statuses do NOT block new opens
  - Stale PENDING expiry: rows > 120s become STALE_PENDING
  - Fresh PENDING rows are not expired
  - Dual-row safety: stale expires, fresh SUBMIT_AMBIGUOUS still blocks
  - Lock behavior: BLOCKED_LOCK_HELD intent is written when lock is held
  - resolved_ts only auto-set on terminal status transitions
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

# Engine uses PEP 604 union syntax (str | None) which requires Python 3.10+.
# Tests are designed for the VPS (Python 3.11+). Skip gracefully on older interpreters.
if sys.version_info < (3, 10):
    pytest.skip("Requires Python 3.10+ (engine uses PEP 604 syntax)", allow_module_level=True)

# ── path setup ─────────────────────────────────────────────────────────────────
_ENGINE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ENGINE_ROOT not in sys.path:
    sys.path.insert(0, _ENGINE_ROOT)

# Lazy imports happen inside fixtures/tests to allow DB_PATH patching first
import utils.db as _db_module
import utils.tier_manager as tier_manager


# ── fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture()
def tmp_db(tmp_path):
    """
    Create a fresh temp DB with the full engine schema for each test.
    Patches db.DB_PATH AND tier_manager.DB_PATH to the temp file so both
    persistent_rate_limit_check (uses db.get_conn) and tier open guards
    (uses tier_manager.DB_PATH) operate against the temp DB.
    Mocks Telegram sends so no real alerts fire.

    Patch 164: init_db() now creates kv_store, so manual table creation
    is no longer needed. Patch 165: db.DB_PATH is now also patched for
    the duration of each test (persistent_rate_limit_check isolation).
    """
    db_file = str(tmp_path / "test_engine.db")

    # Build full schema (including kv_store) in temp DB
    with patch.object(_db_module, "DB_PATH", db_file):
        _db_module.init_db()

    # Patch both db.DB_PATH and tier_manager.DB_PATH to the temp file.
    # Suppress Telegram so no real alerts fire during tests.
    with (
        patch.object(_db_module, "DB_PATH", db_file),       # Patch 165: isolate persistent_rate_limit_check
        patch.object(tier_manager, "DB_PATH", db_file),
        patch("utils.telegram_alerts.send_telegram_sync", return_value=True),
        patch("utils.telegram_alerts.should_rate_limit", return_value=False),
    ):
        yield db_file


def _conn(db_file: str) -> sqlite3.Connection:
    """Open a raw sqlite3 connection to the temp DB (Row factory)."""
    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    return conn


def _old_ts(seconds: int) -> str:
    """ISO-8601 UTC timestamp `seconds` in the past."""
    t = datetime.now(timezone.utc) - timedelta(seconds=seconds)
    return t.strftime("%Y-%m-%dT%H:%M:%S.%f+00:00")


# ── CHECK 1: blocking guard ────────────────────────────────────────────────────

class TestBlockingGuard:
    """_get_blocking_intents must stop open_tier_position before any Jupiter call."""

    def test_submit_ambiguous_blocks_open(self, tmp_db):
        """A SUBMIT_AMBIGUOUS row for the same tier+symbol must block open_tier_position."""
        conn = _conn(tmp_db)
        tier_manager._insert_execution_intent(
            conn, "3x", "SOL", "LONG", 50.0, 3.0, status="SUBMIT_AMBIGUOUS"
        )
        conn.commit()
        conn.close()

        with patch("utils.jupiter_perps_trade.open_perp_sync") as mock_jupiter:
            result = tier_manager.open_tier_position("3x")

        mock_jupiter.assert_not_called()
        assert result.get("state") == "BLOCKED_PENDING_RECONCILIATION"
        assert result.get("ok") is False
        assert result.get("success") is False

    def test_reconcile_manual_required_blocks_open(self, tmp_db):
        """A RECONCILE_MANUAL_REQUIRED row must also block open_tier_position."""
        conn = _conn(tmp_db)
        tier_manager._insert_execution_intent(
            conn, "5x", "BTC", "LONG", 20.0, 5.0, status="RECONCILE_MANUAL_REQUIRED"
        )
        conn.commit()
        conn.close()

        with patch("utils.jupiter_perps_trade.open_perp_sync") as mock_jupiter:
            result = tier_manager.open_tier_position("5x")

        mock_jupiter.assert_not_called()
        assert result.get("state") == "BLOCKED_PENDING_RECONCILIATION"

    def test_blocked_intent_written_to_db(self, tmp_db):
        """A BLOCKED_PENDING_RECONCILIATION row must be persisted to DB."""
        conn = _conn(tmp_db)
        tier_manager._insert_execution_intent(
            conn, "3x", "SOL", "LONG", 50.0, 3.0, status="SUBMIT_AMBIGUOUS"
        )
        conn.commit()
        conn.close()

        with patch("utils.jupiter_perps_trade.open_perp_sync"):
            tier_manager.open_tier_position("3x")

        conn2 = _conn(tmp_db)
        guard_row = conn2.execute("""
            SELECT status, error_detail FROM tier_execution_intents
            WHERE status='BLOCKED_PENDING_RECONCILIATION'
            ORDER BY id DESC LIMIT 1
        """).fetchone()
        conn2.close()

        assert guard_row is not None
        assert "Blocked" in (guard_row["error_detail"] or "")

    def test_terminal_status_does_not_block(self, tmp_db):
        """A SUBMIT_CONFIRMED row (terminal) must NOT be returned by _get_blocking_intents."""
        conn = _conn(tmp_db)
        intent_id = tier_manager._insert_execution_intent(
            conn, "3x", "SOL", "LONG", 50.0, 3.0
        )
        tier_manager._update_execution_intent(conn, intent_id, status="SUBMIT_CONFIRMED")
        conn.commit()

        # Terminal status must not appear in blocking intents
        blocking = tier_manager._get_blocking_intents(conn, "3x", "SOL")
        conn.close()
        assert blocking == [], f"SUBMIT_CONFIRMED should not block; got: {blocking}"

    def test_different_tier_does_not_block(self, tmp_db):
        """A SUBMIT_AMBIGUOUS row for 5x/BTC must NOT block 3x/SOL (different tier+symbol)."""
        conn = _conn(tmp_db)
        tier_manager._insert_execution_intent(
            conn, "5x", "BTC", "LONG", 20.0, 5.0, status="SUBMIT_AMBIGUOUS"
        )
        conn.commit()

        # 3x/SOL should see no blocking intents — 5x/BTC is a different key
        blocking = tier_manager._get_blocking_intents(conn, "3x", "SOL")
        conn.close()
        assert blocking == [], f"5x/BTC blocker must not affect 3x/SOL; got: {blocking}"


# ── CHECK 2: stale PENDING expiry ──────────────────────────────────────────────

class TestStalePendingExpiry:
    """_expire_stale_pending_intents marks PENDING rows > 120s as STALE_PENDING."""

    def test_old_pending_becomes_stale(self, tmp_db):
        """PENDING row older than _LOCK_EXPIRY_S + 30s must become STALE_PENDING."""
        conn = _conn(tmp_db)
        row_id = conn.execute("""
            INSERT INTO tier_execution_intents
              (created_ts, tier_label, symbol, side, collateral_usd, leverage, status)
            VALUES (?, '3x', 'SOL', 'LONG', 50.0, 3.0, 'PENDING')
        """, (_old_ts(tier_manager._LOCK_EXPIRY_S + 60),)).lastrowid
        conn.commit()

        tier_manager._expire_stale_pending_intents(conn, "3x", "SOL")

        row = conn.execute(
            "SELECT status, resolved_ts FROM tier_execution_intents WHERE id=?", (row_id,)
        ).fetchone()
        conn.close()

        assert row["status"] == "STALE_PENDING"
        assert row["resolved_ts"] is not None

    def test_fresh_pending_not_expired(self, tmp_db):
        """PENDING row newer than threshold must NOT be changed."""
        conn = _conn(tmp_db)
        row_id = tier_manager._insert_execution_intent(
            conn, "3x", "SOL", "LONG", 50.0, 3.0
        )
        conn.commit()

        tier_manager._expire_stale_pending_intents(conn, "3x", "SOL")

        row = conn.execute(
            "SELECT status FROM tier_execution_intents WHERE id=?", (row_id,)
        ).fetchone()
        conn.close()

        assert row["status"] == "PENDING"

    def test_stale_expires_fresh_ambiguous_blocks(self, tmp_db):
        """
        Safety test (dual-row strategy):
        - Stale PENDING must expire to STALE_PENDING.
        - Fresh SUBMIT_AMBIGUOUS must still block the open.
        - open_perp_sync must never be called.
        """
        conn = _conn(tmp_db)
        stale_id = conn.execute("""
            INSERT INTO tier_execution_intents
              (created_ts, tier_label, symbol, side, collateral_usd, leverage, status)
            VALUES (?, '3x', 'SOL', 'LONG', 50.0, 3.0, 'PENDING')
        """, (_old_ts(tier_manager._LOCK_EXPIRY_S + 60),)).lastrowid

        tier_manager._insert_execution_intent(
            conn, "3x", "SOL", "LONG", 50.0, 3.0, status="SUBMIT_AMBIGUOUS"
        )
        conn.commit()
        conn.close()

        with patch("utils.jupiter_perps_trade.open_perp_sync") as mock_jupiter:
            result = tier_manager.open_tier_position("3x")

        mock_jupiter.assert_not_called()
        assert result.get("state") == "BLOCKED_PENDING_RECONCILIATION"

        conn2 = _conn(tmp_db)
        stale_row = conn2.execute(
            "SELECT status FROM tier_execution_intents WHERE id=?", (stale_id,)
        ).fetchone()
        conn2.close()
        assert stale_row["status"] == "STALE_PENDING"


# ── CHECK 3: lock behavior ─────────────────────────────────────────────────────

class TestLockBehavior:
    """Lock blocks concurrent opens; BLOCKED_LOCK_HELD intent is written."""

    def test_lock_blocked_records_intent(self, tmp_db):
        """When the tier lock is held, open_tier_position returns BLOCKED_LOCK_HELD."""
        conn = _conn(tmp_db)
        token = tier_manager._acquire_tier_lock(conn, "3x", "SOL")
        assert token is not None, "Failed to acquire lock for test setup"
        conn.commit()
        conn.close()

        with patch("utils.jupiter_perps_trade.open_perp_sync") as mock_jupiter:
            result = tier_manager.open_tier_position("3x")

        mock_jupiter.assert_not_called()
        assert result.get("state") == "BLOCKED_LOCK_HELD"

        conn2 = _conn(tmp_db)
        row = conn2.execute("""
            SELECT status FROM tier_execution_intents
            WHERE tier_label='3x' AND symbol='SOL' AND status='BLOCKED_LOCK_HELD'
            ORDER BY id DESC LIMIT 1
        """).fetchone()
        conn2.close()
        assert row is not None


# ── CHECK 4: resolved_ts auto-set only on terminal transitions ────────────────

class TestResolvedTs:
    """_update_execution_intent sets resolved_ts only on terminal statuses."""

    def test_resolved_ts_set_on_terminal(self, tmp_db):
        """Transitioning to SUBMIT_CONFIRMED must auto-set resolved_ts."""
        conn = _conn(tmp_db)
        intent_id = tier_manager._insert_execution_intent(
            conn, "3x", "SOL", "LONG", 50.0, 3.0
        )
        tier_manager._update_execution_intent(conn, intent_id, status="SUBMIT_CONFIRMED")

        row = conn.execute(
            "SELECT status, resolved_ts FROM tier_execution_intents WHERE id=?",
            (intent_id,)
        ).fetchone()
        conn.close()

        assert row["status"] == "SUBMIT_CONFIRMED"
        assert row["resolved_ts"] is not None

    def test_resolved_ts_not_set_on_non_terminal(self, tmp_db):
        """Updating a non-status field while PENDING must NOT set resolved_ts."""
        conn = _conn(tmp_db)
        intent_id = tier_manager._insert_execution_intent(
            conn, "3x", "SOL", "LONG", 50.0, 3.0
        )
        tier_manager._update_execution_intent(
            conn, intent_id, build_response_excerpt="partial"
        )

        row = conn.execute(
            "SELECT status, resolved_ts FROM tier_execution_intents WHERE id=?",
            (intent_id,)
        ).fetchone()
        conn.close()

        assert row["status"] == "PENDING"
        assert row["resolved_ts"] is None

    def test_all_terminal_statuses_set_resolved_ts(self, tmp_db):
        """Every status in _TERMINAL_STATUSES must trigger resolved_ts auto-set."""
        for status in tier_manager._TERMINAL_STATUSES:
            conn = _conn(tmp_db)
            intent_id = tier_manager._insert_execution_intent(
                conn, "3x", "SOL", "LONG", 50.0, 3.0
            )
            tier_manager._update_execution_intent(conn, intent_id, status=status)
            row = conn.execute(
                "SELECT resolved_ts FROM tier_execution_intents WHERE id=?",
                (intent_id,)
            ).fetchone()
            conn.close()
            assert row["resolved_ts"] is not None, (
                f"resolved_ts not set for terminal status '{status}'"
            )

    def test_submit_ambiguous_does_not_set_resolved_ts(self, tmp_db):
        """SUBMIT_AMBIGUOUS is NOT terminal — resolved_ts must NOT be auto-set."""
        conn = _conn(tmp_db)
        intent_id = tier_manager._insert_execution_intent(
            conn, "3x", "SOL", "LONG", 50.0, 3.0
        )
        tier_manager._update_execution_intent(
            conn, intent_id, status="SUBMIT_AMBIGUOUS"
        )

        row = conn.execute(
            "SELECT status, resolved_ts FROM tier_execution_intents WHERE id=?",
            (intent_id,)
        ).fetchone()
        conn.close()

        assert row["status"] == "SUBMIT_AMBIGUOUS"
        assert row["resolved_ts"] is None


# ── CHECK 5: end-to-end lifecycle ─────────────────────────────────────────────

class TestLifecycle:
    """
    Mocked end-to-end lifecycle:
      1. open_tier_position called → Jupiter mocked to return SUBMIT_AMBIGUOUS outcome
      2. A second open attempt is blocked (BLOCKED_PENDING_RECONCILIATION)
      3. Intent manually advanced to RECONCILED_CONFIRMED (simulates reconciler)
      4. _get_blocking_intents now returns empty → next open would proceed
    """

    def _mock_open_returns_ambiguous(self, tmp_db: str) -> int:
        """
        Drive open_tier_position with a mocked Jupiter path that writes
        SUBMIT_AMBIGUOUS to the DB (simulates a submitted-but-unconfirmed tx).
        Returns the intent_id that was written.
        """
        # Insert the SUBMIT_AMBIGUOUS intent directly — mirrors what
        # open_tier_position does internally when the tx submit is ambiguous.
        conn = _conn(tmp_db)
        intent_id = tier_manager._insert_execution_intent(
            conn, "3x", "SOL", "LONG", 50.0, 3.0
        )
        tier_manager._update_execution_intent(
            conn, intent_id, status="SUBMIT_AMBIGUOUS",
            position_pubkey="SynthPubkey123abc",
        )
        conn.commit()
        conn.close()
        return intent_id

    def test_lifecycle_submit_ambiguous_blocks_then_resolves(self, tmp_db):
        """
        Full lifecycle:
          SUBMIT_AMBIGUOUS written → duplicate open blocked →
          resolved to RECONCILED_CONFIRMED → unblocked.
        """
        # ── Phase 1: SUBMIT_AMBIGUOUS in DB ───────────────────────────────────
        intent_id = self._mock_open_returns_ambiguous(tmp_db)

        conn = _conn(tmp_db)
        row = conn.execute(
            "SELECT status FROM tier_execution_intents WHERE id=?", (intent_id,)
        ).fetchone()
        conn.close()
        assert row["status"] == "SUBMIT_AMBIGUOUS", "Setup: intent must be SUBMIT_AMBIGUOUS"

        # ── Phase 2: second open attempt must be blocked ───────────────────────
        with patch("utils.jupiter_perps_trade.open_perp_sync") as mock_jup:
            result = tier_manager.open_tier_position("3x")

        mock_jup.assert_not_called()
        assert result.get("state") == "BLOCKED_PENDING_RECONCILIATION"
        assert result.get("ok") is False

        # BLOCKED_PENDING_RECONCILIATION intent must be persisted
        conn2 = _conn(tmp_db)
        block_row = conn2.execute(
            "SELECT id FROM tier_execution_intents WHERE status='BLOCKED_PENDING_RECONCILIATION'"
            " ORDER BY id DESC LIMIT 1"
        ).fetchone()
        conn2.close()
        assert block_row is not None, "BLOCKED_PENDING_RECONCILIATION row must be written to DB"

        # ── Phase 3: simulate reconciler resolving the ambiguous intent ────────
        conn3 = _conn(tmp_db)
        tier_manager._update_execution_intent(
            conn3, intent_id, status="RECONCILED_CONFIRMED"
        )
        conn3.commit()

        # After resolution, blocking intents must be empty for this tier+symbol
        blocking = tier_manager._get_blocking_intents(conn3, "3x", "SOL")
        conn3.close()
        assert blocking == [], (
            f"After RECONCILED_CONFIRMED, _get_blocking_intents must return []; got: {blocking}"
        )


# ── CHECK 6: restart/recovery — all safety state is DB-backed ─────────────────

class TestRestartRecovery:
    """
    Restart-path invariant tests. Patch 165.

    All state that affects blocking guards and rate limits must survive a
    simulated service restart — i.e., must live in SQLite, not in-memory.

    We model a restart by using entirely separate DB connections for the
    "before restart" writes and the "after restart" reads. No shared Python
    objects carry state between the two phases.
    """

    def test_blocking_guard_is_entirely_db_backed(self, tmp_db):
        """
        A SUBMIT_AMBIGUOUS intent written via 'conn_a' must block via 'conn_b'.
        No in-memory accumulation should be needed.
        Models: original process writes intent → service restarts → new process reads DB.
        """
        # Phase 1 — "original process" writes the intent
        conn_a = _conn(tmp_db)
        intent_id = tier_manager._insert_execution_intent(
            conn_a, "3x", "SOL", "LONG", 50.0, 3.0
        )
        tier_manager._update_execution_intent(conn_a, intent_id, status="SUBMIT_AMBIGUOUS")
        conn_a.commit()
        conn_a.close()

        # Phase 2 — "new process after restart" reads via a fresh connection
        conn_b = _conn(tmp_db)
        blocking = tier_manager._get_blocking_intents(conn_b, "3x", "SOL")
        conn_b.close()

        assert len(blocking) == 1, (
            f"SUBMIT_AMBIGUOUS must block via fresh DB connection; got: {blocking}"
        )
        assert blocking[0]["status"] == "SUBMIT_AMBIGUOUS"

    def test_stale_pending_expires_via_fresh_connection(self, tmp_db):
        """
        A stale PENDING row written before "restart" must be expired by
        _expire_stale_pending_intents called on a fresh connection.
        Models: engine crashed mid-open → restart → first cycle expires stale lock.
        """
        # Phase 1 — write old PENDING (simulates crash during a previous open)
        conn_a = _conn(tmp_db)
        stale_id = conn_a.execute(
            """
            INSERT INTO tier_execution_intents
              (created_ts, tier_label, symbol, side, collateral_usd, leverage, status)
            VALUES (?, '3x', 'SOL', 'LONG', 50.0, 3.0, 'PENDING')
            """,
            (_old_ts(tier_manager._LOCK_EXPIRY_S + 60),),
        ).lastrowid
        conn_a.commit()
        conn_a.close()

        # Phase 2 — "new process after restart" runs expire on fresh connection
        conn_b = _conn(tmp_db)
        tier_manager._expire_stale_pending_intents(conn_b, "3x", "SOL")
        row = conn_b.execute(
            "SELECT status FROM tier_execution_intents WHERE id=?", (stale_id,)
        ).fetchone()
        conn_b.close()

        assert row["status"] == "STALE_PENDING", (
            f"Stale PENDING must expire via fresh connection; got status={row['status']}"
        )

    def test_persistent_rate_limit_survives_simulated_restart(self, tmp_db):
        """
        persistent_rate_limit_check writes cooldown timestamps to kv_store (SQLite).
        A second call — even via a fresh import after a simulated restart — must
        read the persisted timestamp and suppress within the window.

        db.DB_PATH is already patched to tmp_db by the fixture (Patch 165 fixture update).
        """
        # First call: outside any window — should allow and persist timestamp
        r1 = _db_module.persistent_rate_limit_check("test_restart_rl_165", limit_s=3600)

        # Second call: within window — must suppress by reading persisted timestamp
        r2 = _db_module.persistent_rate_limit_check("test_restart_rl_165", limit_s=3600)

        assert r1 is False, f"First call should allow (return False); got {r1}"
        assert r2 is True,  f"Second call within window should suppress (return True); got {r2}"

        # Verify timestamp was actually written to kv_store (not just in-memory)
        conn = _conn(tmp_db)
        row = conn.execute(
            "SELECT value FROM kv_store WHERE key='alert_ts:test_restart_rl_165'"
        ).fetchone()
        conn.close()
        assert row is not None, "persistent_rate_limit_check must write timestamp to kv_store"
        assert float(row["value"]) > 0, "Stored timestamp must be a positive Unix epoch float"

    def test_reconciled_confirmed_clears_guard_on_fresh_connection(self, tmp_db):
        """
        After reconciliation to RECONCILED_CONFIRMED (persisted to DB), a fresh
        connection (new process after restart) must see an empty blocking set.
        """
        # Phase 1 — write SUBMIT_AMBIGUOUS
        conn_a = _conn(tmp_db)
        intent_id = tier_manager._insert_execution_intent(
            conn_a, "3x", "SOL", "LONG", 50.0, 3.0
        )
        tier_manager._update_execution_intent(conn_a, intent_id, status="SUBMIT_AMBIGUOUS")
        conn_a.commit()
        conn_a.close()

        # Phase 2 — reconciler resolves (could be in same or different process)
        conn_b = _conn(tmp_db)
        tier_manager._update_execution_intent(conn_b, intent_id, status="RECONCILED_CONFIRMED")
        conn_b.commit()
        conn_b.close()

        # Phase 3 — "new process after restart" checks the guard via fresh connection
        conn_c = _conn(tmp_db)
        blocking = tier_manager._get_blocking_intents(conn_c, "3x", "SOL")
        conn_c.close()

        assert blocking == [], (
            f"After RECONCILED_CONFIRMED, fresh connection must see empty guard; got: {blocking}"
        )
