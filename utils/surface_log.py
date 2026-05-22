"""
Surface log utility — Patch 267 (extends Patch 266 / 264 / 263)

Provides:
  surface_log_write(items, source)  — snapshot surfaced candidates to research_surface_log
  surface_log_fill_outcomes()       — fill return_24h_pct for rows >= 24h old
  dj_outcome_fill()                 — fill outcome_4h_pct/24h_pct + verdict on resolved DJ rows

Design:
  - research_surface_log stores the classification labels that existed at surfacing
    time (action, move_type, continuation_archetype, fuel_quality,
    entry_window, score, etc.)
  - 24h later, fill_outcomes() looks up memecoin_signal_outcomes for the nearest
    scan to the surfacing time and copies its return_24h_pct
  - This creates the labeled outcome dataset needed for future calibration

Mint-aware identity (Patch 266):
  - research_surface_log now stores mint (nullable) for new rows
  - surface_log_write() reads mint from item dicts when present
  - surface_log_fill_outcomes() prefers mint-keyed outcome lookup when mint is set,
    falling back to symbol-keyed lookup for legacy rows (mint IS NULL)
  - This prevents recycled-ticker contamination of calibration data on Solana

Accountability bridge (Patch 264):
  - After writing ACT rows, _create_pending_decisions() auto-inserts a
    PENDING decision_journal row for each ACT item (if not already present
    as an unresolved thread for that symbol/mint)
  - Uses idempotent CREATE IF NOT EXISTS for decision_journal so this module
    never depends on home.py having already created the table

Decision-journal outcome closure (Patch 267):
  - _create_pending_decisions() now stores mint on the DJ row (from tuple[14])
    so dj_outcome_fill() can use mint-first MSO lookup
  - dj_outcome_fill() finds operator-resolved DJ entries (not PENDING) with
    resolution_status still PENDING, looks up MSO return_4h_pct / return_24h_pct
    within ±4h of created_ts, computes verdict, marks resolution_status = RESOLVED
  - Entries with no MSO match after 48h are marked NO_OUTCOME (token unlisted)
  - Called automatically from memecoin_outcome_step() every 60 s

Deduplication:
  - surface_log_write() skips any (symbol, source) pair already written
    within the last _DEDUPE_MINUTES minutes
  - Prevents spam from frequent operator page refreshes

Safety:
  - All I/O is wrapped in try/except; errors are logged at DEBUG level only
  - No function raises — all are fire-and-forget
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime, timezone

log = logging.getLogger(__name__)

# Dedupe window: don't re-log (symbol, source) if written within this many minutes
_DEDUPE_MINUTES = 30

# Minimal decision_journal DDL — idempotent, safe to run even if home.py already
# created the table (CREATE TABLE IF NOT EXISTS is a no-op if it exists)
_DJ_SCHEMA_MIN = """
CREATE TABLE IF NOT EXISTS decision_journal (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    created_ts         TEXT NOT NULL,
    source_surface     TEXT NOT NULL,
    system             TEXT,
    symbol             TEXT,
    mint               TEXT DEFAULT NULL,
    recommended_action TEXT NOT NULL,
    priority           TEXT,
    reason             TEXT,
    blockers_json      TEXT,
    snapshot_json      TEXT,
    operator_decision  TEXT NOT NULL DEFAULT 'PENDING',
    operator_note      TEXT,
    last_seen_ts       TEXT,
    surface_count      INTEGER NOT NULL DEFAULT 1,
    resolved_ts        TEXT,
    outcome_4h_pct     REAL,
    outcome_24h_pct    REAL,
    resolution_status  TEXT NOT NULL DEFAULT 'PENDING',
    verdict            TEXT
)
"""

# Patch 267: add mint to decision_journal for mint-first outcome attribution.
# Fails silently on fresh installs (column already defined in _DJ_SCHEMA_MIN above).
_MIGRATE_267_DJ = "ALTER TABLE decision_journal ADD COLUMN mint TEXT DEFAULT NULL"
_MIGRATE_314_LAST_SEEN = "ALTER TABLE decision_journal ADD COLUMN last_seen_ts TEXT"
_MIGRATE_314_SURFACE_COUNT = "ALTER TABLE decision_journal ADD COLUMN surface_count INTEGER NOT NULL DEFAULT 1"


_SCHEMA = """
CREATE TABLE IF NOT EXISTS research_surface_log (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    surfaced_at         TEXT    NOT NULL,
    symbol              TEXT    NOT NULL,
    mint                TEXT    DEFAULT NULL,
    source              TEXT    NOT NULL,
    action              TEXT    NOT NULL,
    move_type           TEXT,
    continuation_archetype TEXT,
    research_priority   TEXT,
    fuel_quality        TEXT,
    entry_window        TEXT,
    move_phase          TEXT,
    score               REAL,
    support_overlap_score REAL,
    support_overlap_tags TEXT,
    capital_posture     TEXT,
    capital_band        TEXT,
    capital_ready_state TEXT,
    capital_suggested_entry_usd REAL,
    capital_pressure_bucket TEXT,
    capital_regime_bucket TEXT,
    capital_mix_bucket  TEXT,
    capital_allocator_stance TEXT,
    capital_headroom_bucket TEXT,
    capital_route_bucket TEXT,
    marginal_route      TEXT,
    allocator_posture   TEXT,
    dominant_book       TEXT,
    queue_system        TEXT,
    queue_priority_bucket TEXT,
    marginal_route_authority TEXT,
    proof_stack_authority TEXT,
    reinforcement_authority TEXT,
    promotion_authority TEXT,
    deployment_authority TEXT,
    decision_authority TEXT,
    continuation_memory_authority TEXT,
    fresh_catalyst_bucket TEXT,
    fresh_discovery_authority TEXT,
    freshness_bucket    TEXT,
    proof_candidate     INTEGER NOT NULL DEFAULT 0,
    proof_reason        TEXT,
    proof_score         REAL,
    proof_snapshot_json TEXT,
    first_leg_confirmed INTEGER NOT NULL DEFAULT 0,
    n_windows           INTEGER NOT NULL DEFAULT 0,
    is_second_leg       INTEGER NOT NULL DEFAULT 0,
    return_24h_pct      REAL,
    outcome_status      TEXT    NOT NULL DEFAULT 'PENDING',
    outcome_filled_at   TEXT
)
"""

# Patch 266: non-destructive migration — adds mint to existing tables.
# Fails silently on fresh installs (column already defined in _SCHEMA above).
_MIGRATE_266 = "ALTER TABLE research_surface_log ADD COLUMN mint TEXT DEFAULT NULL"
_MIGRATE_326 = "ALTER TABLE research_surface_log ADD COLUMN continuation_archetype TEXT"
_MIGRATE_327_SCORE = "ALTER TABLE research_surface_log ADD COLUMN support_overlap_score REAL"
_MIGRATE_327_TAGS = "ALTER TABLE research_surface_log ADD COLUMN support_overlap_tags TEXT"
_MIGRATE_334_POSTURE = "ALTER TABLE research_surface_log ADD COLUMN capital_posture TEXT"
_MIGRATE_334_BAND = "ALTER TABLE research_surface_log ADD COLUMN capital_band TEXT"
_MIGRATE_338_READY = "ALTER TABLE research_surface_log ADD COLUMN capital_ready_state TEXT"
_MIGRATE_338_SUGGESTED = "ALTER TABLE research_surface_log ADD COLUMN capital_suggested_entry_usd REAL"
_MIGRATE_343_PRESSURE = "ALTER TABLE research_surface_log ADD COLUMN capital_pressure_bucket TEXT"
_MIGRATE_349_REGIME = "ALTER TABLE research_surface_log ADD COLUMN capital_regime_bucket TEXT"
_MIGRATE_350_MIX = "ALTER TABLE research_surface_log ADD COLUMN capital_mix_bucket TEXT"
_MIGRATE_355_ALLOCATOR = "ALTER TABLE research_surface_log ADD COLUMN capital_allocator_stance TEXT"
_MIGRATE_353_HEADROOM = "ALTER TABLE research_surface_log ADD COLUMN capital_headroom_bucket TEXT"
_MIGRATE_354_ROUTE = "ALTER TABLE research_surface_log ADD COLUMN capital_route_bucket TEXT"
_MIGRATE_356_MARGINAL = "ALTER TABLE research_surface_log ADD COLUMN marginal_route TEXT"
_MIGRATE_357_ALLOC_POSTURE = "ALTER TABLE research_surface_log ADD COLUMN allocator_posture TEXT"
_MIGRATE_358_DOMINANT_BOOK = "ALTER TABLE research_surface_log ADD COLUMN dominant_book TEXT"
_MIGRATE_359_QUEUE_SYSTEM = "ALTER TABLE research_surface_log ADD COLUMN queue_system TEXT"
_MIGRATE_360_QUEUE_PRIORITY = "ALTER TABLE research_surface_log ADD COLUMN queue_priority_bucket TEXT"
_MIGRATE_361_ROUTE_AUTHORITY = "ALTER TABLE research_surface_log ADD COLUMN marginal_route_authority TEXT"
_MIGRATE_365_PROOF_AUTHORITY = "ALTER TABLE research_surface_log ADD COLUMN proof_stack_authority TEXT"
_MIGRATE_367_REINFORCEMENT_AUTHORITY = "ALTER TABLE research_surface_log ADD COLUMN reinforcement_authority TEXT"
_MIGRATE_366_PROMOTION_AUTHORITY = "ALTER TABLE research_surface_log ADD COLUMN promotion_authority TEXT"
_MIGRATE_368_DEPLOYMENT_AUTHORITY = "ALTER TABLE research_surface_log ADD COLUMN deployment_authority TEXT"
_MIGRATE_369_DECISION_AUTHORITY = "ALTER TABLE research_surface_log ADD COLUMN decision_authority TEXT"
_MIGRATE_362_MEMORY_AUTHORITY = "ALTER TABLE research_surface_log ADD COLUMN continuation_memory_authority TEXT"
_MIGRATE_363_FRESH_CATALYST = "ALTER TABLE research_surface_log ADD COLUMN fresh_catalyst_bucket TEXT"
_MIGRATE_364_FRESH_DISCOVERY_AUTHORITY = "ALTER TABLE research_surface_log ADD COLUMN fresh_discovery_authority TEXT"
_MIGRATE_346_FRESHNESS = "ALTER TABLE research_surface_log ADD COLUMN freshness_bucket TEXT"
_MIGRATE_370_PROOF_CANDIDATE = "ALTER TABLE research_surface_log ADD COLUMN proof_candidate INTEGER NOT NULL DEFAULT 0"
_MIGRATE_371_PROOF_REASON = "ALTER TABLE research_surface_log ADD COLUMN proof_reason TEXT"
_MIGRATE_372_PROOF_SCORE = "ALTER TABLE research_surface_log ADD COLUMN proof_score REAL"
_MIGRATE_373_PROOF_SNAPSHOT = "ALTER TABLE research_surface_log ADD COLUMN proof_snapshot_json TEXT"


def _db_path() -> str:
    """Resolve engine.db path from this module's location (utils/ → parent)."""
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    return os.path.join(root, "data_storage", "engine.db")


def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(_SCHEMA)
    try:
        conn.execute(_MIGRATE_266)
    except sqlite3.OperationalError:
        pass  # column already exists (fresh install or already migrated)
    try:
        conn.execute(_MIGRATE_326)
    except sqlite3.OperationalError:
        pass  # column already exists (fresh install or already migrated)
    try:
        conn.execute(_MIGRATE_327_SCORE)
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute(_MIGRATE_327_TAGS)
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute(_MIGRATE_334_POSTURE)
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute(_MIGRATE_334_BAND)
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute(_MIGRATE_338_READY)
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute(_MIGRATE_338_SUGGESTED)
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute(_MIGRATE_343_PRESSURE)
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute(_MIGRATE_349_REGIME)
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute(_MIGRATE_350_MIX)
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute(_MIGRATE_355_ALLOCATOR)
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute(_MIGRATE_353_HEADROOM)
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute(_MIGRATE_354_ROUTE)
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute(_MIGRATE_356_MARGINAL)
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute(_MIGRATE_357_ALLOC_POSTURE)
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute(_MIGRATE_358_DOMINANT_BOOK)
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute(_MIGRATE_359_QUEUE_SYSTEM)
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute(_MIGRATE_360_QUEUE_PRIORITY)
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute(_MIGRATE_361_ROUTE_AUTHORITY)
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute(_MIGRATE_365_PROOF_AUTHORITY)
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute(_MIGRATE_367_REINFORCEMENT_AUTHORITY)
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute(_MIGRATE_366_PROMOTION_AUTHORITY)
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute(_MIGRATE_368_DEPLOYMENT_AUTHORITY)
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute(_MIGRATE_369_DECISION_AUTHORITY)
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute(_MIGRATE_362_MEMORY_AUTHORITY)
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute(_MIGRATE_363_FRESH_CATALYST)
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute(_MIGRATE_364_FRESH_DISCOVERY_AUTHORITY)
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute(_MIGRATE_346_FRESHNESS)
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute(_MIGRATE_370_PROOF_CANDIDATE)
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute(_MIGRATE_371_PROOF_REASON)
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute(_MIGRATE_372_PROOF_SCORE)
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute(_MIGRATE_373_PROOF_SNAPSHOT)
    except sqlite3.OperationalError:
        pass


# ── Decision journal auto-create ──────────────────────────────────────────────

def _create_pending_decisions(
    act_rows: list,
    conn: sqlite3.Connection,
    now_str: str,
    source: str,
) -> None:
    """Auto-insert PENDING decision_journal entries for ACT-tier surfacings.

    Args:
        act_rows: subset of rows_to_insert tuples where action == 'ACT'.
                  Tuple layout (mirrors surface_log INSERT order):
                  [0] surfaced_at, [1] symbol, [2] source, [3] action,
                  [4] move_type, [5] continuation_archetype, [6] research_priority,
                  [7] fuel_quality, [8] entry_window, [9] move_phase, [10] score,
                  [11] first_leg_confirmed, [12] n_windows, [13] is_second_leg,
                  [14] mint (Patch 267 — stored on DJ row for mint-first outcome lookup)
        conn:     open sqlite3 connection (decision_journal in same db)
        now_str:  UTC timestamp string
        source:   'HOME_QUEUE' or 'RESEARCH_PAGE' (stored in reason field)

    Dedupe/update: unresolved ACT_SURFACE rows are treated as a single operator
    thread per symbol/mint. Re-surfacings refresh the existing row instead of
    creating another pending item.
    """
    if not act_rows:
        return

    def _reason_quality(reason: str | None) -> tuple[int, int]:
        text = str(reason or "")
        score = 0
        if "RESEARCH_PAGE" in text:
            score += 3
        if "HOME_QUEUE" in text:
            score += 1
        if "score=None" not in text and "score=" in text:
            score += 1
        if "fuel=" in text and "fuel= ·" not in text and "fuel=" + "" not in text:
            score += 1
        if "window=" in text and "window= ·" not in text and "window=" + "" not in text:
            score += 1
        if "move=" in text and "move= ·" not in text:
            score += 1
        if "arch=" in text and "arch= ·" not in text:
            score += 1
        if "2nd-leg" in text or "history" in text:
            score += 1
        return score, len(text)

    def _snapshot_quality(snapshot_text: str | None) -> tuple[int, int]:
        text = str(snapshot_text or "")
        score = 0
        if '"score":null' not in text and '"score":' in text:
            score += 1
        if '"fuel_quality":"' in text and '"fuel_quality":""' not in text:
            score += 1
        if '"entry_window":"' in text and '"entry_window":""' not in text:
            score += 1
        if '"move_type":"' in text and '"move_type":""' not in text:
            score += 1
        if '"continuation_archetype":"' in text and '"continuation_archetype":""' not in text:
            score += 1
        if '"first_leg_confirmed":1' in text:
            score += 1
        return score, len(text)

    # Ensure decision_journal exists and has mint column (idempotent)
    conn.execute(_DJ_SCHEMA_MIN)
    for ddl in (_MIGRATE_267_DJ, _MIGRATE_314_LAST_SEEN, _MIGRATE_314_SURFACE_COUNT):
        try:
            conn.execute(ddl)
        except sqlite3.OperationalError:
            pass  # column already exists

    # Build unresolved thread map — one row per active symbol/mint decision thread.
    unresolved_rows = conn.execute(
            "SELECT id, symbol, mint, COALESCE(surface_count, 1), "
            "       reason, snapshot_json, "
            "       COALESCE(last_seen_ts, created_ts) AS activity_ts "
            "FROM decision_journal "
            "WHERE source_surface = 'ACT_SURFACE' "
            "  AND resolution_status = 'PENDING' "
            "  AND operator_decision = 'PENDING'"
            "ORDER BY COALESCE(last_seen_ts, created_ts) DESC, id DESC"
    ).fetchall()
    unresolved_by_key: dict[tuple[str, str], tuple[int, int, str, str]] = {}
    unresolved_by_symbol: dict[str, tuple[int, int, str, str, str]] = {}
    for row in unresolved_rows:
        symbol = str(row[1] or "").strip().upper()
        mint = str(row[2] or "").strip()
        existing_reason = str(row[4] or "")
        existing_snapshot = str(row[5] or "")
        key = (mint.upper(), symbol) if mint else ("", symbol)
        unresolved_by_key[key] = (int(row[0]), int(row[3] or 1), existing_reason, existing_snapshot)
        if symbol and symbol not in unresolved_by_symbol:
            unresolved_by_symbol[symbol] = (int(row[0]), int(row[3] or 1), mint.upper(), existing_reason, existing_snapshot)

    to_insert = []
    for r in act_rows:
        sym = r[1]
        mint = r[14] or None
        symbol_key = str(sym or "").strip().upper()
        mint_key = str(mint or "").strip().upper()
        key = (mint_key, symbol_key)
        move_type      = r[4] or ""
        continuation_archetype = r[5] or ""
        rp             = r[6] or ""
        fuel_quality   = r[7] or ""
        entry_window   = r[8] or ""
        score          = r[10]
        flc            = bool(r[11])
        nw             = r[12]
        is_sl          = bool(r[13])

        reason_parts = [f"score={score}", f"fuel={fuel_quality}",
                        f"window={entry_window}", f"move={move_type}"]
        if continuation_archetype:
            reason_parts.append(f"arch={continuation_archetype}")
        if is_sl:
            reason_parts.append("2nd-leg")
        if flc:
            reason_parts.append(f"{nw}w history")
        reason = f"ACT surfaced from {source}: " + " · ".join(reason_parts)

        snapshot = (
            f'{{"move_type":"{move_type}","research_priority":"{rp}",'
            f'"continuation_archetype":"{continuation_archetype}",'
            f'"fuel_quality":"{fuel_quality}","entry_window":"{entry_window}",'
            f'"score":{score},"first_leg_confirmed":{int(flc)},'
            f'"n_windows":{nw},"is_second_leg":{int(is_sl)}}}'
        )

        existing = unresolved_by_key.get(key)
        if not existing:
            if mint_key:
                existing = unresolved_by_key.get(("", symbol_key))
            if not existing:
                sym_existing = unresolved_by_symbol.get(symbol_key)
                if sym_existing:
                    existing = (sym_existing[0], sym_existing[1], sym_existing[3], sym_existing[4])
        if existing:
            existing_id, seen_count, existing_reason, existing_snapshot = existing
            chosen_reason = reason
            if _reason_quality(existing_reason) > _reason_quality(reason):
                chosen_reason = existing_reason
            chosen_snapshot = snapshot
            if _snapshot_quality(existing_snapshot) > _snapshot_quality(snapshot):
                chosen_snapshot = existing_snapshot
            conn.execute(
                """
                UPDATE decision_journal
                   SET priority = ?,
                       reason = ?,
                       snapshot_json = ?,
                       mint = COALESCE(mint, ?),
                       last_seen_ts = ?,
                       surface_count = ?
                 WHERE id = ?
                """,
                (
                    rp,
                    chosen_reason,
                    chosen_snapshot,
                    mint,
                    now_str,
                    seen_count + 1,
                    existing_id,
                ),
            )
            unresolved_by_key[key] = (existing_id, seen_count + 1, chosen_reason, chosen_snapshot)
            if symbol_key:
                unresolved_by_symbol[symbol_key] = (existing_id, seen_count + 1, mint_key, chosen_reason, chosen_snapshot)
            continue

        to_insert.append((
            now_str,          # created_ts
            "ACT_SURFACE",    # source_surface
            "MEMECOINS",      # system
            sym,              # symbol
            mint,             # mint  (Patch 267 — enables mint-first outcome lookup)
            "ACT",            # recommended_action
            rp,               # priority
            reason,           # reason
            snapshot,         # snapshot_json
            now_str,          # last_seen_ts
        ))
        unresolved_by_key[key] = (-1, 1, reason, snapshot)
        if symbol_key and symbol_key not in unresolved_by_symbol:
            unresolved_by_symbol[symbol_key] = (-1, 1, mint_key, reason, snapshot)

    if to_insert:
        conn.executemany("""
            INSERT INTO decision_journal (
                created_ts, source_surface, system, symbol, mint,
                recommended_action, priority, reason, snapshot_json,
                last_seen_ts, surface_count, operator_decision, resolution_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 'PENDING', 'PENDING')
        """, to_insert)
        log.debug("[SURFACE_LOG] created %d pending DJ entries (source=%s)", len(to_insert), source)


# ── Write ─────────────────────────────────────────────────────────────────────

def surface_log_write(items: list, source: str) -> None:
    """Snapshot surfaced classification labels into research_surface_log.

    Args:
        items:  List of candidate dicts.  Each must have 'symbol'.
                Optional fields: mint, action, move_type, continuation_archetype,
                research_priority, fuel_quality, entry_window, move_phase, score,
                first_leg_confirmed, n_windows, is_second_leg.
                mint is stored when present (Patch 266 — enables mint-first
                outcome attribution in fill_outcomes).
        source: 'HOME_QUEUE' or 'RESEARCH_PAGE'

    Deduplication: skips any symbol already logged from this source within
    the last _DEDUPE_MINUTES minutes.  Safe to call on every refresh.

    Tuple layout (positions referenced by _create_pending_decisions):
      [0] surfaced_at  [1] symbol  [2] source    [3] action
      [4] move_type    [5] archetype [6] rp         [7] fuel
      [8] entry_window [9] move_phase [10] score    [11] flc
      [12] n_windows   [13] is_second_leg [14] mint [15] support_score [16] support_tags
      [17] capital_posture [18] capital_band [19] capital_ready_state [20] capital_suggested_entry_usd [21] capital_pressure_bucket [22] capital_regime_bucket [23] capital_mix_bucket [24] capital_allocator_stance [25] capital_headroom_bucket [26] capital_route_bucket [27] marginal_route [28] allocator_posture [29] dominant_book [30] queue_system [31] queue_priority_bucket [32] marginal_route_authority [33] proof_stack_authority [34] reinforcement_authority [35] promotion_authority [36] deployment_authority [37] decision_authority [38] continuation_memory_authority [39] fresh_catalyst_bucket [40] fresh_discovery_authority [41] freshness_bucket [42] proof_candidate [43] proof_reason [44] proof_score [45] proof_snapshot_json
    """
    if not items:
        return
    try:
        conn = sqlite3.connect(_db_path())
        _ensure_table(conn)
        conn.commit()

        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        # Build dedupe set — symbols already logged from this source recently
        recent: set = {
            row[0] for row in conn.execute(
                "SELECT symbol FROM research_surface_log "
                "WHERE source = ? AND surfaced_at >= datetime('now', ?)",
                (source, f"-{_DEDUPE_MINUTES} minutes"),
            ).fetchall()
        }

        rows_to_insert = []
        for item in items:
            sym = item.get("symbol")
            if not sym or sym in recent:
                continue
            rows_to_insert.append((
                now_str,                                      # [0]
                sym,                                          # [1]
                source,                                       # [2]
                item.get("action") or "",                     # [3]
                item.get("move_type"),                        # [4]
                item.get("continuation_archetype"),           # [5]
                item.get("research_priority"),                # [6]
                item.get("fuel_quality"),                     # [7]
                item.get("entry_window"),                     # [8]
                item.get("move_phase"),                       # [9]
                item.get("score"),                            # [10]
                1 if item.get("first_leg_confirmed") else 0,  # [11]
                int(item.get("n_windows") or 0),              # [12]
                1 if item.get("is_second_leg") else 0,        # [13]
                item.get("mint") or None,                     # [14] Patch 266
                float(item.get("support_overlap_score") or 0.0),  # [15]
                json.dumps(item.get("support_overlap_tags") or []),  # [16]
                item.get("capital_posture"),                  # [17]
                item.get("capital_band"),                     # [18]
                item.get("capital_ready_state"),              # [19]
                item.get("capital_suggested_entry_usd"),      # [20]
                item.get("capital_pressure_bucket"),          # [21]
                item.get("capital_regime_bucket"),            # [22]
                item.get("capital_mix_bucket"),               # [23]
                item.get("capital_allocator_stance"),         # [24]
                item.get("capital_headroom_bucket"),          # [25]
                item.get("capital_route_bucket"),             # [26]
                item.get("marginal_route"),                   # [27]
                item.get("allocator_posture"),                # [28]
                item.get("dominant_book"),                    # [29]
                item.get("queue_system") or item.get("system"),  # [30]
                item.get("queue_priority_bucket") or item.get("priority"),  # [31]
                item.get("marginal_route_authority"),         # [32]
                item.get("proof_stack_authority"),            # [33]
                item.get("reinforcement_authority"),          # [34]
                item.get("promotion_authority"),              # [35]
                item.get("deployment_authority"),             # [36]
                item.get("decision_authority"),               # [37]
                item.get("continuation_memory_authority"),    # [38]
                item.get("fresh_catalyst_bucket"),            # [39]
                item.get("fresh_discovery_authority"),        # [40]
                item.get("freshness_bucket"),                 # [41]
                1 if item.get("proof_candidate") else 0,      # [42]
                item.get("proof_reason"),                     # [43]
                item.get("proof_score"),                      # [44]
                item.get("proof_snapshot_json"),              # [45]
            ))

        if rows_to_insert:
            conn.executemany("""
                INSERT INTO research_surface_log (
                    surfaced_at, symbol, source, action, move_type,
                    continuation_archetype, research_priority,
                    fuel_quality, entry_window, move_phase,
                    score, first_leg_confirmed, n_windows, is_second_leg,
                    mint, support_overlap_score, support_overlap_tags,
                    capital_posture, capital_band, capital_ready_state,
                    capital_suggested_entry_usd, capital_pressure_bucket, capital_regime_bucket, capital_mix_bucket, capital_allocator_stance, capital_headroom_bucket, capital_route_bucket, marginal_route, allocator_posture, dominant_book, queue_system, queue_priority_bucket, marginal_route_authority, proof_stack_authority, reinforcement_authority, promotion_authority, deployment_authority, decision_authority, continuation_memory_authority, fresh_catalyst_bucket, fresh_discovery_authority, freshness_bucket, proof_candidate, proof_reason, proof_score, proof_snapshot_json, outcome_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING')
            """, rows_to_insert)
            log.debug("[SURFACE_LOG] wrote %d rows (source=%s)", len(rows_to_insert), source)

            # Patch 264: auto-create PENDING decision_journal entries for ACT items
            act_rows = [r for r in rows_to_insert if r[3] == "ACT"]
            _create_pending_decisions(act_rows, conn, now_str, source)

            conn.commit()

        conn.close()
    except Exception as _e:
        log.debug("[SURFACE_LOG] write failed (%s): %s", source, _e)


# ── Outcome fill ──────────────────────────────────────────────────────────────

def surface_log_fill_outcomes() -> int:
    """Fill return_24h_pct for PENDING rows that are now >= 24h old.

    Match strategy (Patch 266 — mint-first):
      For each PENDING row where surfaced_at <= now - 24h:

        If mint IS NOT NULL (new rows, Patch 266+):
          Query memecoin_signal_outcomes WHERE mint = ? within ±4h.
          This is ticker-collision-safe — matches only the exact mint that
          was surfaced, even if another token later recycled the same symbol.

        If mint IS NULL (legacy rows, pre-Patch-266):
          Fall back to WHERE symbol = ? within ±4h.
          Same behavior as before; legacy rows are unaffected.

      In both cases: closest scan by time, non-null return_24h_pct required.

      If matched   → outcome_status = 'RESOLVED', return_24h_pct = matched value
      If unmatched AND surfaced_at <= now - 48h → outcome_status = 'EXPIRED'

    Returns count of rows marked RESOLVED in this call.
    """
    resolved = 0
    try:
        conn = sqlite3.connect(_db_path())
        _ensure_table(conn)
        conn.commit()

        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        # Rows ready for outcome resolution (>= 24h old, still PENDING)
        # Fetch mint alongside symbol — determines which lookup path to use
        pending = conn.execute("""
            SELECT id, symbol, mint, surfaced_at
            FROM research_surface_log
            WHERE outcome_status = 'PENDING'
              AND surfaced_at <= datetime('now', '-24 hours')
        """).fetchall()

        for row_id, symbol, mint, surfaced_at in pending:
            if mint:
                # Patch 266: mint-keyed lookup — exact token, no ticker collision risk
                match = conn.execute("""
                    SELECT return_24h_pct
                    FROM memecoin_signal_outcomes
                    WHERE mint = ?
                      AND return_24h_pct IS NOT NULL
                      AND scanned_at >= datetime(?, '-4 hours')
                      AND scanned_at <= datetime(?, '+4 hours')
                    ORDER BY ABS(
                        strftime('%s', scanned_at) - strftime('%s', ?)
                    )
                    LIMIT 1
                """, (mint, surfaced_at, surfaced_at, surfaced_at)).fetchone()
            else:
                # Legacy fallback: symbol-keyed lookup for pre-Patch-266 rows
                match = conn.execute("""
                    SELECT return_24h_pct
                    FROM memecoin_signal_outcomes
                    WHERE symbol = ?
                      AND return_24h_pct IS NOT NULL
                      AND scanned_at >= datetime(?, '-4 hours')
                      AND scanned_at <= datetime(?, '+4 hours')
                    ORDER BY ABS(
                        strftime('%s', scanned_at) - strftime('%s', ?)
                    )
                    LIMIT 1
                """, (symbol, surfaced_at, surfaced_at, surfaced_at)).fetchone()

            if match:
                conn.execute("""
                    UPDATE research_surface_log
                    SET return_24h_pct   = ?,
                        outcome_status   = 'RESOLVED',
                        outcome_filled_at = ?
                    WHERE id = ?
                """, (match[0], now_str, row_id))
                resolved += 1

        # Expire rows that have waited >= 48h with no match
        conn.execute("""
            UPDATE research_surface_log
            SET outcome_status   = 'EXPIRED',
                outcome_filled_at = ?
            WHERE outcome_status = 'PENDING'
              AND surfaced_at <= datetime('now', '-48 hours')
        """, (now_str,))

        conn.commit()
        conn.close()

        if resolved:
            log.debug("[SURFACE_LOG] filled %d outcomes", resolved)
    except Exception as _e:
        log.debug("[SURFACE_LOG] fill_outcomes failed: %s", _e)

    return resolved


# ── Decision-journal outcome closure (Patch 267) ──────────────────────────────

def _dj_verdict_local(operator_decision: str, outcome_24h_pct) -> str | None:
    """Compute verdict from operator decision + 24h outcome.

    Mirrors home.py's _dj_verdict() — kept local to avoid circular import.
      FOLLOWED + positive → GOOD_FOLLOW   FOLLOWED + negative → BAD_FOLLOW
      SKIPPED  + positive → BAD_SKIP      SKIPPED  + negative → GOOD_SKIP
      OVERRIDDEN + positive → GOOD_OVERRIDE            negative → BAD_OVERRIDE
    """
    if outcome_24h_pct is None:
        return None
    pos = float(outcome_24h_pct) > 0
    if operator_decision == "FOLLOWED":
        return "GOOD_FOLLOW" if pos else "BAD_FOLLOW"
    if operator_decision == "SKIPPED":
        return "BAD_SKIP" if pos else "GOOD_SKIP"
    if operator_decision == "OVERRIDDEN":
        return "GOOD_OVERRIDE" if pos else "BAD_OVERRIDE"
    return None


def dj_outcome_fill() -> int:
    """Fill outcome_4h_pct / outcome_24h_pct / verdict for operator-resolved DJ entries.

    Called automatically from memecoin_outcome_step() every 60 s (Patch 267).
    Completes the operator accountability loop: ACT surfaced → operator decides
    → outcome fills automatically → verdict computed.

    Criteria for processing:
      operator_decision NOT IN ('PENDING') — operator has made a decision
      resolution_status = 'PENDING'        — outcome not yet attributed

    Match strategy (mint-first):
      If mint IS NOT NULL: MSO WHERE mint = ? within ±4h of created_ts
      If mint IS NULL:     MSO WHERE symbol = ? within ±4h of created_ts (legacy)
      Both require return_24h_pct IS NOT NULL (24h data must be available).

    Fills:  outcome_4h_pct, outcome_24h_pct, verdict, resolved_ts
    Sets:   resolution_status = 'RESOLVED'
    Expire: entries >= 48h old with no MSO match → resolution_status = 'NO_OUTCOME'

    Returns count of entries closed in this call.
    """
    closed = 0
    try:
        conn = sqlite3.connect(_db_path())
        _ensure_table(conn)
        # Ensure DJ table and mint column both exist
        conn.execute(_DJ_SCHEMA_MIN)
        try:
            conn.execute(_MIGRATE_267_DJ)
        except sqlite3.OperationalError:
            pass  # column already exists
        conn.commit()

        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        # Only process rows where operator has made a decision
        rows = conn.execute("""
            SELECT id, symbol, mint, operator_decision, created_ts
            FROM decision_journal
            WHERE operator_decision NOT IN ('PENDING')
              AND resolution_status = 'PENDING'
        """).fetchall()

        for row_id, symbol, mint, op_decision, created_ts in rows:
            # Normalize timestamp — may be ISO offset, space-separated, or UTC Z
            ts_clean = (
                created_ts
                .replace("T", " ")
                .replace("+00:00", "")
                .replace("Z", "")
            )[:19]

            if mint:
                # Mint-first: exact token, no ticker-collision risk
                match = conn.execute("""
                    SELECT return_4h_pct, return_24h_pct
                    FROM memecoin_signal_outcomes
                    WHERE mint = ?
                      AND return_24h_pct IS NOT NULL
                      AND scanned_at >= datetime(?, '-4 hours')
                      AND scanned_at <= datetime(?, '+4 hours')
                    ORDER BY ABS(
                        strftime('%s', scanned_at) - strftime('%s', ?)
                    )
                    LIMIT 1
                """, (mint, ts_clean, ts_clean, ts_clean)).fetchone()
            else:
                # Symbol fallback for legacy DJ rows (pre-Patch-267, no mint stored)
                match = conn.execute("""
                    SELECT return_4h_pct, return_24h_pct
                    FROM memecoin_signal_outcomes
                    WHERE symbol = ?
                      AND return_24h_pct IS NOT NULL
                      AND scanned_at >= datetime(?, '-4 hours')
                      AND scanned_at <= datetime(?, '+4 hours')
                    ORDER BY ABS(
                        strftime('%s', scanned_at) - strftime('%s', ?)
                    )
                    LIMIT 1
                """, (symbol, ts_clean, ts_clean, ts_clean)).fetchone()

            if match:
                r4h, r24h = match[0], match[1]
                verdict = _dj_verdict_local(op_decision, r24h)
                conn.execute("""
                    UPDATE decision_journal
                    SET outcome_4h_pct    = ?,
                        outcome_24h_pct   = ?,
                        verdict           = ?,
                        resolved_ts       = ?,
                        resolution_status = 'RESOLVED'
                    WHERE id = ?
                """, (r4h, r24h, verdict, now_str, row_id))
                closed += 1

        # Expire operator-decided rows that have waited >= 48h without an MSO match.
        # Token may be delisted or never had a qualifying scan — mark as NO_OUTCOME.
        conn.execute("""
            UPDATE decision_journal
            SET resolution_status = 'NO_OUTCOME',
                resolved_ts       = ?
            WHERE operator_decision NOT IN ('PENDING')
              AND resolution_status = 'PENDING'
              AND created_ts <= datetime('now', '-48 hours')
        """, (now_str,))

        conn.commit()
        conn.close()

        if closed:
            log.debug("[DJ_OUTCOME] closed %d decision outcomes", closed)
    except Exception as _e:
        log.debug("[DJ_OUTCOME] fill failed: %s", _e)

    return closed
