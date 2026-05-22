from __future__ import annotations

"""
utils/authority.py — Roadmap 3 Authority Bridge

Reads the authority snapshot written by the dashboard Home endpoint and exposes
a single resolve_action() call that each engine executor can use to check whether
a proposed action is permitted under the current operating model.

Design principles:
  - Read-only from the engine side (no writes to authority state from executors)
  - Fails OPEN: a missing or stale snapshot returns ALLOW with a warning, never blocks
  - Observe-before-enforce: set AUTHORITY_ENFORCE=true to activate hard blocking
  - No duplicate truth: this module never computes authority rules itself — it only
    reads the snapshot produced by _build_home_operating_stack() in home.py

Snapshot schema (written by home.py):
  {
    'computed_at':              ISO timestamp,
    'action_law_state':         RESTRICTED | CONDITIONAL | PERMISSIVE | EXECUTION_ENABLED,
    'highest_permitted_action': PAPER_EXECUTION_ONLY | MANAGE_AND_PLAN | CONDITIONAL_ROUTING
                                | SELECTIVE_DEPLOYMENT | FULL_DEPLOYMENT,
    'law_confidence':           str,
    'fresh_capital_policy':     BLOCKED | CONDITIONAL | SELECTIVE | OPEN,
    'rotation_policy':          str,
    'policy_state':             str,
    'memecoins_suspension_state': ACTIVE | WATCHING | SUSPENDED | REACTIVATING | HARD_BLOCKED,
    'spot_suspension_state':    same,
    'perps_suspension_state':   same,
    'whale_suspension_state':   same,
  }
"""

import json
import logging
import os
import time
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

_KV_KEY = "authority_snapshot"

# Snapshot older than this is considered stale — fail open
_STALE_THRESHOLD_S = 600  # 10 minutes

# Law rank: higher = more permissive.  FULL_DEPLOYMENT = 4.
_LAW_RANK = {
    "PAPER_EXECUTION_ONLY": 0,
    "MANAGE_AND_PLAN":       1,
    "CONDITIONAL_ROUTING":   2,
    "SELECTIVE_DEPLOYMENT":  3,
    "FULL_DEPLOYMENT":       4,
}

# Minimum law rank required for each proposed action
_ACTION_REQUIRED_RANK = {
    "new_entry":       3,   # SELECTIVE_DEPLOYMENT or higher
    "scale_existing":  2,   # CONDITIONAL_ROUTING or higher
    "manage_existing": 1,   # MANAGE_AND_PLAN or higher
    "close_position":  0,   # always permitted — protective/risk-reduction
    "reduce_risk":     0,   # always permitted — protective/risk-reduction
    "paper_only":      0,   # always OK
}

# Suspension states that block new entries / scaling
_BLOCKING_STATES = {"SUSPENDED", "HARD_BLOCKED"}
_LOG_THROTTLE_SECONDS = int(os.getenv("AUTHORITY_LOG_THROTTLE_SECONDS", "300"))
_last_log_by_key: dict[str, float] = {}
_SNAPSHOT_MIN_WRITE_INTERVAL_SECONDS = int(os.getenv("AUTHORITY_SNAPSHOT_MIN_WRITE_INTERVAL_SECONDS", "30"))
_SNAPSHOT_LOCK_LOG_THROTTLE_SECONDS = int(os.getenv("AUTHORITY_SNAPSHOT_LOCK_LOG_THROTTLE_SECONDS", "120"))
_SNAPSHOT_FORCE_WRITE_INTERVAL_SECONDS = int(os.getenv("AUTHORITY_SNAPSHOT_FORCE_WRITE_INTERVAL_SECONDS", "300"))
_SNAPSHOT_LOCK_LOG_LEVEL = os.getenv("AUTHORITY_SNAPSHOT_LOCK_LOG_LEVEL", "debug").strip().lower()
_last_snapshot_write_ts = 0.0
_last_snapshot_lock_log_ts = 0.0

# Whether to enforce (default: observe only)
def _enforce_mode() -> bool:
    return os.getenv("AUTHORITY_ENFORCE", "false").lower() == "true"


# ── DB access ─────────────────────────────────────────────────────────────────

def _db_conn(timeout: float = 5.0, busy_timeout_ms: int = 3000):
    """Return a sqlite3 connection to the shared engine DB (same as utils/db.py)."""
    import sqlite3
    db_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "data_storage", "engine.db"
    )
    conn = sqlite3.connect(db_path, timeout=timeout)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)}")
    return conn


# ── Public API ─────────────────────────────────────────────────────────────────

def read_snapshot() -> dict | None:
    """Read the latest authority snapshot from kv_store.  Returns None if missing."""
    try:
        conn = _db_conn()
        try:
            row = conn.execute(
                "SELECT value FROM kv_store WHERE key=?", (_KV_KEY,)
            ).fetchone()
            if row:
                return json.loads(row["value"])
            return None
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("authority.read_snapshot error: %s", exc)
        return None


def _snapshot_signature(snapshot: dict | None) -> tuple:
    snap = snapshot or {}
    return (
        snap.get("action_law_state"),
        snap.get("highest_permitted_action"),
        snap.get("fresh_capital_policy"),
        snap.get("rotation_policy"),
        snap.get("policy_state"),
        snap.get("memecoins_suspension_state"),
        snap.get("spot_suspension_state"),
        snap.get("perps_suspension_state"),
        snap.get("whale_suspension_state"),
    )


def _snapshot_age_seconds(snapshot: dict | None) -> int | None:
    raw = str((snapshot or {}).get("computed_at") or "").strip()
    if not raw:
        return None
    try:
        ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return int((datetime.now(timezone.utc) - ts).total_seconds())
    except Exception:
        return None


def write_snapshot(snapshot: dict) -> None:
    """Write an authority snapshot to kv_store.  Called by home.py endpoints."""
    global _last_snapshot_write_ts, _last_snapshot_lock_log_ts
    now = time.time()
    if now - _last_snapshot_write_ts < max(1, _SNAPSHOT_MIN_WRITE_INTERVAL_SECONDS):
        return
    try:
        current = read_snapshot()
        current_age = _snapshot_age_seconds(current)
        if (
            current
            and current_age is not None
            and current_age < max(_SNAPSHOT_MIN_WRITE_INTERVAL_SECONDS, _SNAPSHOT_FORCE_WRITE_INTERVAL_SECONDS)
            and _snapshot_signature(current) == _snapshot_signature(snapshot)
        ):
            _last_snapshot_write_ts = now
            return
        payload = json.dumps(snapshot)
        conn = _db_conn(timeout=0.5, busy_timeout_ms=250)
        try:
            conn.execute(
                "INSERT OR REPLACE INTO kv_store (key, value) VALUES (?, ?)",
                (_KV_KEY, payload),
            )
            conn.commit()
            _last_snapshot_write_ts = now
        finally:
            conn.close()
    except Exception as exc:
        message = str(exc).lower()
        if "database is locked" in message:
            if now - _last_snapshot_lock_log_ts >= max(1, _SNAPSHOT_LOCK_LOG_THROTTLE_SECONDS):
                log_fn = logger.warning if _SNAPSHOT_LOCK_LOG_LEVEL == "warning" else logger.debug
                log_fn("authority.write_snapshot skipped: database is locked")
                _last_snapshot_lock_log_ts = now
            return
        logger.warning("authority.write_snapshot error: %s", exc)


def _evaluate_action_against_snapshot(
    proposed_action: str, lane: str, snap: dict
) -> tuple[str, list[str]]:
    """
    Pure evaluation of proposed_action against a snapshot dict.
    Returns (verdict, reasons).  No DB I/O, no logging, no recording.
    Used by both resolve_action() (live path) and validate_rank_transitions() (test path).
    """
    verdict: str = "ALLOW"
    reasons: list[str] = []

    # Lane suspension check
    suspension_key   = f"{lane}_suspension_state"
    suspension_state = snap.get(suspension_key, "ACTIVE")
    if suspension_state in _BLOCKING_STATES:
        if proposed_action in ("new_entry", "scale_existing"):
            verdict = "BLOCK"
            reasons.append(
                f"lane_suspended — {lane} suspension_state={suspension_state}"
            )

    # Action law rank check
    highest      = snap.get("highest_permitted_action", "FULL_DEPLOYMENT")
    current_rank = _LAW_RANK.get(highest, 4)
    required_rank = _ACTION_REQUIRED_RANK.get(proposed_action, 0)
    if current_rank < required_rank:
        verdict = "BLOCK"
        reasons.append(
            f"law_rank_insufficient — highest_permitted={highest}(rank={current_rank}) "
            f"< required(rank={required_rank}) for action={proposed_action}"
        )

    # Fresh capital policy — only applies to new_entry (not exits/reduces)
    if proposed_action == "new_entry":
        fcp = snap.get("fresh_capital_policy", "OPEN")
        if fcp == "BLOCKED":
            verdict = "BLOCK"
            reasons.append(f"fresh_capital_blocked — fresh_capital_policy={fcp}")
        elif fcp == "CONDITIONAL":
            if verdict == "ALLOW":
                verdict = "CONDITIONAL"
            reasons.append(f"fresh_capital_conditional — fresh_capital_policy={fcp}")

    # Informative fallback reason
    if not reasons:
        reasons.append(
            f"permitted — highest_permitted={highest}, {lane}_state={suspension_state}, "
            f"action_law={snap.get('action_law_state', 'UNKNOWN')}"
        )

    return verdict, reasons


def resolve_action(proposed_action: str, lane: str, executor: str = "") -> dict:
    """
    Evaluate whether proposed_action is permitted for the given lane.

    Args:
        proposed_action: 'new_entry' | 'scale_existing' | 'manage_existing' | 'paper_only'
        lane:            'memecoins' | 'spot' | 'perps' | 'whale'
        executor:        caller identifier e.g. 'spot' | 'perps' | 'memecoins'

    Returns dict:
        {
            'verdict':          'ALLOW' | 'CONDITIONAL' | 'BLOCK',
            'reasons':          [str, ...],
            'snapshot_age_s':   int | None,
            'stale':            bool,
            'enforce':          bool,
            'executor':         str,
        }
    """
    enforce = _enforce_mode()
    result = {
        "verdict":        "ALLOW",
        "reasons":        [],
        "snapshot_age_s": None,
        "stale":          False,
        "enforce":        enforce,
        "executor":       executor,
    }

    # ── Load snapshot ──────────────────────────────────────────────────────────
    snap = read_snapshot()

    if snap is None:
        result["reasons"].append("no_snapshot — authority bridge not yet populated; failing open")
        result["stale"] = True
        _log_decision(proposed_action, lane, result)
        return result

    # ── Staleness check ────────────────────────────────────────────────────────
    computed_at_str = snap.get("computed_at")
    age_s = None
    if computed_at_str:
        try:
            computed_at = datetime.fromisoformat(computed_at_str)
            if computed_at.tzinfo is None:
                computed_at = computed_at.replace(tzinfo=timezone.utc)
            now = datetime.now(tz=timezone.utc)
            age_s = int((now - computed_at).total_seconds())
            result["snapshot_age_s"] = age_s
        except Exception:
            pass

    if age_s is not None and age_s > _STALE_THRESHOLD_S:
        result["reasons"].append(
            f"stale_snapshot — age={age_s}s > {_STALE_THRESHOLD_S}s; failing open"
        )
        result["stale"] = True
        _log_decision(proposed_action, lane, result)
        return result

    # ── Core evaluation (shared with validate_rank_transitions) ───────────────
    verdict, reasons = _evaluate_action_against_snapshot(proposed_action, lane, snap)
    result["verdict"] = verdict
    result["reasons"] = reasons

    _log_decision(proposed_action, lane, result)
    _record_decision(proposed_action, lane, result)
    return result


# ── Internal helpers ───────────────────────────────────────────────────────────

def _log_decision(proposed_action: str, lane: str, result: dict) -> None:
    """Emit AUTHORITY_OBSERVE or AUTHORITY_BLOCK log line."""
    verdict   = result["verdict"]
    enforce   = result["enforce"]
    executor  = result.get("executor", "")
    reasons   = "; ".join(result["reasons"])
    age       = result["snapshot_age_s"]
    age_str   = f" age={age}s" if age is not None else ""
    exec_str  = f" executor={executor}" if executor else ""
    result["log_suppressed"] = False

    key = "|".join([str(verdict), str(proposed_action), str(lane), str(executor), reasons])
    now = time.monotonic()
    last = _last_log_by_key.get(key)
    if last is not None and now - last < max(30, _LOG_THROTTLE_SECONDS):
        result["log_suppressed"] = True
        logger.debug(
            "AUTHORITY_%s_SUPPRESSED action=%s lane=%s%s reasons=[%s]%s",
            "BLOCK" if verdict == "BLOCK" else "OBSERVE",
            proposed_action, lane, exec_str, reasons, age_str,
        )
        return
    _last_log_by_key[key] = now

    if verdict == "BLOCK" and enforce:
        logger.warning(
            "AUTHORITY_BLOCK action=%s lane=%s%s reasons=[%s]%s",
            proposed_action, lane, exec_str, reasons, age_str,
        )
    elif verdict == "BLOCK":
        logger.info(
            "AUTHORITY_OBSERVE action=%s lane=%s%s verdict=BLOCK (not enforced) reasons=[%s]%s",
            proposed_action, lane, exec_str, reasons, age_str,
        )
    elif verdict == "CONDITIONAL":
        logger.info(
            "AUTHORITY_OBSERVE action=%s lane=%s%s verdict=CONDITIONAL reasons=[%s]%s",
            proposed_action, lane, exec_str, reasons, age_str,
        )
    else:
        logger.debug(
            "AUTHORITY_OBSERVE action=%s lane=%s%s verdict=ALLOW%s reasons=[%s]",
            proposed_action, lane, exec_str, age_str, reasons,
        )


def _record_decision(proposed_action: str, lane: str, result: dict) -> None:
    """Persist authority decision to DB audit log.  Never raises."""
    try:
        from db import record_authority_decision  # type: ignore[import]
        record_authority_decision(
            executor        = result.get("executor", ""),
            lane            = lane,
            proposed_action = proposed_action,
            verdict         = result["verdict"],
            reasons         = result["reasons"],
            enforce         = result["enforce"],
            snapshot_age_s  = result["snapshot_age_s"],
            stale           = result["stale"],
        )
    except Exception as exc:
        logger.debug("authority._record_decision skipped: %s", exc)


# ── Enforcement readiness summary ─────────────────────────────────────────────

# Rollout phases — ordered from safest to most permissive
_ROLLOUT_PHASES = [
    ("protective",  ["close_position", "reduce_risk"],  0),
    ("management",  ["manage_existing"],                1),
    ("conditional", ["scale_existing"],                 2),
    ("deployment",  ["new_entry"],                      3),
]

# What advancing to the next law rank requires (human-readable)
_RANK_ADVANCE_CONDITION = {
    1: "One or more lanes must be active or armed (any_live/any_armed)",
    2: "routing_watch active or a lane approaching reactivation (NEAR_REACTIVATION)",
    3: "routing_ready + fresh_capital_policy must open to SELECTIVE or OPEN",
    4: "routing_ready + fresh_capital_policy=OPEN",
}


def build_enforcement_readiness(
    snap: dict | None,
    decisions: list[dict] | None = None,
) -> dict:
    """
    Synthesize enforcement readiness from the authority snapshot and optional
    recent decision stream.

    enforcement_state vocabulary:
      OBSERVE_ONLY     — no snapshot or stale; cannot determine readiness
      PROTECTIVE_ONLY  — only exits/reduces are safely enforceable
      PARTIAL_READY    — protective + management are enforceable; deployment not yet
      DEPLOYMENT_READY — all action classes including new_entry are enforceable
      FULLY_READY      — same as DEPLOYMENT_READY (highest possible state)
    """
    enforce_active = _enforce_mode()
    decisions = decisions or []

    # ── No snapshot ───────────────────────────────────────────────────────────
    if snap is None:
        return {
            "enforcement_state":           "OBSERVE_ONLY",
            "enforcement_note":            "No authority snapshot available — cannot assess readiness.",
            "deployment_enforcement_ready":  False,
            "management_enforcement_ready":  False,
            "protective_enforcement_ready":  True,   # exits always safe
            "primary_enforcement_blocker":  "no_snapshot",
            "recommended_rollout":          "Await first snapshot (call any /api/home/* endpoint).",
            "authority_enforce_active":     enforce_active,
            "per_action_class":            _per_action_table(0, "OPEN"),
            "decision_stream":             _decision_stream_summary(decisions),
            "advance_condition":           _RANK_ADVANCE_CONDITION.get(1, ""),
        }

    # ── Stale snapshot ────────────────────────────────────────────────────────
    age_s = None
    computed_at_str = snap.get("computed_at")
    if computed_at_str:
        try:
            computed_at = datetime.fromisoformat(computed_at_str)
            if computed_at.tzinfo is None:
                computed_at = computed_at.replace(tzinfo=timezone.utc)
            age_s = int((datetime.now(tz=timezone.utc) - computed_at).total_seconds())
        except Exception:
            pass

    stale = age_s is not None and age_s > _STALE_THRESHOLD_S

    if stale:
        return {
            "enforcement_state":           "OBSERVE_ONLY",
            "enforcement_note":            f"Snapshot stale (age={age_s}s > {_STALE_THRESHOLD_S}s) — readiness based on stale data.",
            "deployment_enforcement_ready":  False,
            "management_enforcement_ready":  False,
            "protective_enforcement_ready":  True,
            "primary_enforcement_blocker":  f"stale_snapshot age={age_s}s",
            "recommended_rollout":          "Refresh snapshot via /api/home/modes then re-check.",
            "authority_enforce_active":     enforce_active,
            "per_action_class":            _per_action_table(0, snap.get("fresh_capital_policy", "OPEN")),
            "decision_stream":             _decision_stream_summary(decisions),
            "advance_condition":           "",
        }

    # ── Derive readiness from snapshot ────────────────────────────────────────
    highest          = snap.get("highest_permitted_action", "FULL_DEPLOYMENT")
    current_rank     = _LAW_RANK.get(highest, 4)
    action_law_state = snap.get("action_law_state", "UNKNOWN")
    fcp              = snap.get("fresh_capital_policy", "OPEN")
    policy_state     = snap.get("policy_state", "UNKNOWN")
    law_confidence   = snap.get("law_confidence", "UNKNOWN")

    # Per-phase readiness
    protective_ready   = True                                    # rank 0 — always
    management_ready   = current_rank >= 1                       # MANAGE_AND_PLAN+
    conditional_ready  = current_rank >= 2                       # CONDITIONAL_ROUTING+
    deployment_ready   = current_rank >= 3 and fcp != "BLOCKED"  # SELECTIVE_DEPLOYMENT+ + fcp open

    # Overall enforcement_state
    if deployment_ready:
        state = "DEPLOYMENT_READY"
    elif management_ready:
        state = "PARTIAL_READY"
    elif protective_ready:
        state = "PROTECTIVE_ONLY"
    else:
        state = "OBSERVE_ONLY"

    # Primary blocker
    blockers = []
    if not deployment_ready:
        if fcp == "BLOCKED":
            blockers.append(f"fresh_capital_policy=BLOCKED")
        if current_rank < 3:
            blockers.append(
                f"highest_permitted={highest}(rank={current_rank}) — "
                f"need SELECTIVE_DEPLOYMENT(rank=3)"
            )
    primary_blocker = "; ".join(blockers) if blockers else "none"

    # Note line
    note = (
        f"action_law={action_law_state}, highest_permitted={highest}(rank={current_rank}), "
        f"fcp={fcp}, policy={policy_state}, confidence={law_confidence}"
    )
    if age_s is not None:
        note += f", snapshot_age={age_s}s"

    # Recommended rollout
    rollout_lines = []
    if state == "DEPLOYMENT_READY":
        rollout_lines.append(
            "All action classes are enforceable. "
            "Set AUTHORITY_ENFORCE=true to activate."
        )
    else:
        # Identify the first ready phase not yet enforced
        rollout_lines.append("Phased rollout recommended:")
        rollout_lines.append(
            "  Phase 1 (NOW SAFE): set AUTHORITY_ENFORCE=true — "
            "close_position + reduce_risk will enforce as ALLOW (never blocks exits)."
        )
        if management_ready:
            rollout_lines.append(
                "  Phase 1 also covers: manage_existing enforces as ALLOW "
                f"(MANAGE_AND_PLAN(rank={current_rank}) permits this)."
            )
        if not deployment_ready:
            next_rank = 3
            advance_cond = _RANK_ADVANCE_CONDITION.get(next_rank, "")
            fcp_cond = "" if fcp != "BLOCKED" else " + fresh_capital_policy must open"
            rollout_lines.append(
                f"  Phase 2 (BLOCKED): new_entry enforcement requires "
                f"SELECTIVE_DEPLOYMENT(rank=3){fcp_cond}. "
                f"Condition: {advance_cond}"
            )

    # Advance condition for next rank
    next_rank_needed = current_rank + 1
    advance_cond = _RANK_ADVANCE_CONDITION.get(next_rank_needed, "Already at maximum rank.")

    return {
        "enforcement_state":           state,
        "enforcement_note":            note,
        "deployment_enforcement_ready":  deployment_ready,
        "management_enforcement_ready":  management_ready,
        "protective_enforcement_ready":  protective_ready,
        "primary_enforcement_blocker":  primary_blocker,
        "recommended_rollout":          " ".join(rollout_lines),
        "authority_enforce_active":     enforce_active,
        "per_action_class":            _per_action_table(current_rank, fcp),
        "decision_stream":             _decision_stream_summary(decisions),
        "advance_condition":           advance_cond,
        "snapshot_age_s":              age_s,
        "rank_progression":            build_rank_progression(snap),
    }


def validate_rank_transitions() -> dict:
    """
    Dry-run validation harness for authority rank transitions.

    Runs synthetic snapshots (rank 0–4) through _evaluate_action_against_snapshot()
    and asserts expected verdicts.  Does NOT read or modify the live DB snapshot.
    Does NOT change enforcement state.

    Returns a structured report with per-scenario results and an overall pass/fail.
    """
    actions = list(_ACTION_REQUIRED_RANK.keys())

    # ── Synthetic scenarios (ordered rank 0 → 4, plus edge cases) ───────────
    _scenarios: list[tuple[str, dict]] = [
        ("rank0_paper_only", {
            "highest_permitted_action": "PAPER_EXECUTION_ONLY",
            "action_law_state":         "RESTRICTED",
            "fresh_capital_policy":     "BLOCKED",
            "spot_suspension_state":       "ACTIVE",
            "perps_suspension_state":      "ACTIVE",
            "memecoins_suspension_state":  "ACTIVE",
            "whale_suspension_state":      "ACTIVE",
        }),
        ("rank1_manage_and_plan", {           # ← current live state
            "highest_permitted_action": "MANAGE_AND_PLAN",
            "action_law_state":         "RESTRICTED",
            "fresh_capital_policy":     "BLOCKED",
            "spot_suspension_state":       "ACTIVE",
            "perps_suspension_state":      "ACTIVE",
            "memecoins_suspension_state":  "ACTIVE",
            "whale_suspension_state":      "ACTIVE",
        }),
        ("rank2_conditional_routing_fcp_blocked", {
            "highest_permitted_action": "CONDITIONAL_ROUTING",
            "action_law_state":         "CONDITIONAL",
            "fresh_capital_policy":     "BLOCKED",
            "spot_suspension_state":       "ACTIVE",
            "perps_suspension_state":      "ACTIVE",
            "memecoins_suspension_state":  "ACTIVE",
            "whale_suspension_state":      "ACTIVE",
        }),
        ("rank2_conditional_routing_fcp_conditional", {
            "highest_permitted_action": "CONDITIONAL_ROUTING",
            "action_law_state":         "CONDITIONAL",
            "fresh_capital_policy":     "CONDITIONAL",
            "spot_suspension_state":       "ACTIVE",
            "perps_suspension_state":      "ACTIVE",
            "memecoins_suspension_state":  "ACTIVE",
            "whale_suspension_state":      "ACTIVE",
        }),
        # rank 3 with fcp=CONDITIONAL — new_entry should be CONDITIONAL
        ("rank3_selective_fcp_conditional", {
            "highest_permitted_action": "SELECTIVE_DEPLOYMENT",
            "action_law_state":         "PERMISSIVE",
            "fresh_capital_policy":     "CONDITIONAL",
            "spot_suspension_state":       "ACTIVE",
            "perps_suspension_state":      "ACTIVE",
            "memecoins_suspension_state":  "ACTIVE",
            "whale_suspension_state":      "ACTIVE",
        }),
        ("rank3_selective_deployment", {
            "highest_permitted_action": "SELECTIVE_DEPLOYMENT",
            "action_law_state":         "PERMISSIVE",
            "fresh_capital_policy":     "SELECTIVE",
            "spot_suspension_state":       "ACTIVE",
            "perps_suspension_state":      "ACTIVE",
            "memecoins_suspension_state":  "ACTIVE",
            "whale_suspension_state":      "ACTIVE",
        }),
        ("rank4_full_deployment", {
            "highest_permitted_action": "FULL_DEPLOYMENT",
            "action_law_state":         "EXECUTION_ENABLED",
            "fresh_capital_policy":     "OPEN",
            "spot_suspension_state":       "ACTIVE",
            "perps_suspension_state":      "ACTIVE",
            "memecoins_suspension_state":  "ACTIVE",
            "whale_suspension_state":      "ACTIVE",
        }),
        # Lane suspension edge case: rank-3 but spot lane SUSPENDED
        ("rank3_spot_lane_suspended", {
            "highest_permitted_action": "SELECTIVE_DEPLOYMENT",
            "action_law_state":         "PERMISSIVE",
            "fresh_capital_policy":     "SELECTIVE",
            "spot_suspension_state":       "SUSPENDED",   # only spot blocked
            "perps_suspension_state":      "ACTIVE",
            "memecoins_suspension_state":  "ACTIVE",
            "whale_suspension_state":      "ACTIVE",
        }),
        # Rank-3 but memecoin lane HARD_BLOCKED
        ("rank3_memecoins_hard_blocked", {
            "highest_permitted_action": "SELECTIVE_DEPLOYMENT",
            "action_law_state":         "PERMISSIVE",
            "fresh_capital_policy":     "SELECTIVE",
            "spot_suspension_state":       "ACTIVE",
            "perps_suspension_state":      "ACTIVE",
            "memecoins_suspension_state":  "HARD_BLOCKED",
            "whale_suspension_state":      "ACTIVE",
        }),
    ]

    # ── Expected verdict assertions ─────────────────────────────────────────
    # (scenario_label, action, lane) → expected_verdict
    _expected: dict[tuple[str, str, str], str] = {
        # rank 0 — only protective actions permitted
        ("rank0_paper_only", "close_position",  "spot"):  "ALLOW",
        ("rank0_paper_only", "reduce_risk",      "spot"):  "ALLOW",
        ("rank0_paper_only", "manage_existing",  "spot"):  "BLOCK",
        ("rank0_paper_only", "scale_existing",   "spot"):  "BLOCK",
        ("rank0_paper_only", "new_entry",         "spot"):  "BLOCK",
        # rank 1 — manage_existing unlocked, scale/new_entry still blocked
        ("rank1_manage_and_plan", "manage_existing",  "spot"):  "ALLOW",
        ("rank1_manage_and_plan", "close_position",   "spot"):  "ALLOW",
        ("rank1_manage_and_plan", "reduce_risk",       "spot"):  "ALLOW",
        ("rank1_manage_and_plan", "scale_existing",   "spot"):  "BLOCK",
        ("rank1_manage_and_plan", "new_entry",         "spot"):  "BLOCK",
        # rank 2 fcp=BLOCKED — scale_existing unlocked, new_entry still blocked by fcp
        ("rank2_conditional_routing_fcp_blocked", "scale_existing",  "spot"):  "ALLOW",
        ("rank2_conditional_routing_fcp_blocked", "manage_existing", "spot"):  "ALLOW",
        ("rank2_conditional_routing_fcp_blocked", "new_entry",        "spot"):  "BLOCK",
        # rank 2 fcp=CONDITIONAL — rank check fires first (rank 2 < required 3), new_entry is BLOCK
        ("rank2_conditional_routing_fcp_conditional", "new_entry",    "spot"):  "BLOCK",
        ("rank2_conditional_routing_fcp_conditional", "scale_existing","spot"): "ALLOW",
        # rank 3 fcp=CONDITIONAL — rank sufficient, FCP makes new_entry CONDITIONAL
        ("rank3_selective_fcp_conditional", "new_entry",        "spot"):  "CONDITIONAL",
        ("rank3_selective_fcp_conditional", "scale_existing",   "spot"):  "ALLOW",
        ("rank3_selective_fcp_conditional", "close_position",   "spot"):  "ALLOW",
        # rank 3 — new_entry ALLOW (fcp=SELECTIVE passes)
        ("rank3_selective_deployment", "new_entry",        "spot"):  "ALLOW",
        ("rank3_selective_deployment", "scale_existing",   "spot"):  "ALLOW",
        ("rank3_selective_deployment", "manage_existing",  "spot"):  "ALLOW",
        ("rank3_selective_deployment", "close_position",   "spot"):  "ALLOW",
        ("rank3_selective_deployment", "reduce_risk",       "spot"):  "ALLOW",
        # rank 4 — everything ALLOW
        ("rank4_full_deployment", "new_entry",        "spot"):  "ALLOW",
        ("rank4_full_deployment", "scale_existing",   "spot"):  "ALLOW",
        ("rank4_full_deployment", "manage_existing",  "spot"):  "ALLOW",
        # lane suspension: spot SUSPENDED at rank 3
        ("rank3_spot_lane_suspended", "new_entry",      "spot"):  "BLOCK",   # suspended blocks
        ("rank3_spot_lane_suspended", "scale_existing", "spot"):  "BLOCK",   # suspended blocks
        ("rank3_spot_lane_suspended", "close_position", "spot"):  "ALLOW",   # exits never blocked
        ("rank3_spot_lane_suspended", "reduce_risk",     "spot"):  "ALLOW",   # exits never blocked
        ("rank3_spot_lane_suspended", "new_entry",      "perps"): "ALLOW",   # perps unaffected
        # memecoins HARD_BLOCKED at rank 3
        ("rank3_memecoins_hard_blocked", "new_entry",       "memecoins"): "BLOCK",
        ("rank3_memecoins_hard_blocked", "close_position",  "memecoins"): "ALLOW",
        ("rank3_memecoins_hard_blocked", "new_entry",       "spot"):       "ALLOW",  # spot unaffected
    }

    # ── Run scenarios ────────────────────────────────────────────────────────
    pass_count = 0
    fail_count = 0
    scenario_reports: list[dict] = []

    for label, snap in _scenarios:
        # Determine which lanes to test for this scenario
        if "lane_suspended" in label or "hard_blocked" in label:
            test_lanes = ["spot", "perps", "memecoins"]
        else:
            test_lanes = ["spot"]   # representative; non-suspension cases are lane-agnostic

        row_results: list[dict] = []
        for action in actions:
            for lane in test_lanes:
                verdict, reasons = _evaluate_action_against_snapshot(action, lane, snap)
                key = (label, action, lane)
                expected = _expected.get(key)
                passed: bool | None = None
                if expected is not None:
                    passed = (verdict == expected)
                    if passed:
                        pass_count += 1
                    else:
                        fail_count += 1
                row_results.append({
                    "action":   action,
                    "lane":     lane,
                    "verdict":  verdict,
                    "reasons":  reasons,
                    "expected": expected,
                    "passed":   passed,
                })

        scenario_reports.append({
            "scenario": label,
            "snap_summary": {
                "highest_permitted_action": snap.get("highest_permitted_action"),
                "action_law_state":         snap.get("action_law_state"),
                "fresh_capital_policy":     snap.get("fresh_capital_policy"),
            },
            "results": row_results,
        })

    total_assertions = pass_count + fail_count
    return {
        "validation":        "rank_transition_harness",
        "pass":              pass_count,
        "fail":              fail_count,
        "total_assertions":  total_assertions,
        "all_passed":        fail_count == 0,
        "scenarios":         scenario_reports,
    }


def validate_outcome_gates() -> dict:
    """
    Phase 4 Step 5: Validate outcome-gated rank advancement and FCP downgrade logic.

    Runs synthetic snapshots with varying outcome fields through build_rank_progression()
    and asserts expected structural_met / outcome_met / condition_met values.
    Does NOT read or modify the live DB.

    Returns a structured pass/fail report.
    """
    # Base snapshot fields shared across all scenarios
    _BASE = {
        "memecoins_suspension_state": "ACTIVE",
        "spot_suspension_state":      "ACTIVE",
        "perps_suspension_state":     "ACTIVE",
        "whale_suspension_state":     "ACTIVE",
    }

    def _snap(**overrides: object) -> dict:
        return {**_BASE, **overrides}

    # ── Scenarios ──────────────────────────────────────────────────────────
    _scenarios = [
        # 1. No outcome data → outcome gates pass by default (None)
        ("no_outcome_data", _snap(
            highest_permitted_action="MANAGE_AND_PLAN",
            action_law_state="RESTRICTED",
            fresh_capital_policy="BLOCKED",
            system_routing_state="LOCKED",
            routing_ready_lanes=[],
            routing_watch_lanes=[],
            # no outcome_* fields at all
        )),
        # 2. Insufficient outcome data → outcome gates pass by default (None)
        ("insufficient_outcome_data", _snap(
            highest_permitted_action="MANAGE_AND_PLAN",
            action_law_state="RESTRICTED",
            fresh_capital_policy="BLOCKED",
            system_routing_state="LOCKED",
            routing_ready_lanes=[],
            routing_watch_lanes=[],
            outcome_aggregate_wr_4h=30.0,
            outcome_aggregate_n=3,
            outcome_aggregate_sufficient=False,
            outcome_best_lane_wr_4h=30.0,
            outcome_best_lane_sufficient=False,
        )),
        # 3. Sufficient + good outcomes + structural open → all gates pass
        ("sufficient_good_structural_open", _snap(
            highest_permitted_action="FULL_DEPLOYMENT",
            action_law_state="EXECUTION_ENABLED",
            fresh_capital_policy="OPEN",
            system_routing_state="OPEN",
            routing_ready_lanes=["perps"],
            routing_watch_lanes=[],
            outcome_aggregate_wr_4h=55.0,
            outcome_aggregate_n=100,
            outcome_aggregate_sufficient=True,
            outcome_best_lane_wr_4h=55.0,
            outcome_best_lane_sufficient=True,
        )),
        # 4. Sufficient + poor outcomes + structural open → outcome blocks higher ranks
        ("sufficient_poor_structural_open", _snap(
            highest_permitted_action="FULL_DEPLOYMENT",
            action_law_state="EXECUTION_ENABLED",
            fresh_capital_policy="OPEN",
            system_routing_state="OPEN",
            routing_ready_lanes=["perps"],
            routing_watch_lanes=[],
            outcome_aggregate_wr_4h=35.0,
            outcome_aggregate_n=50,
            outcome_aggregate_sufficient=True,
            outcome_best_lane_wr_4h=35.0,
            outcome_best_lane_sufficient=True,
        )),
        # 5. Structural locked + good outcomes → structural still blocks
        ("structural_locked_good_outcomes", _snap(
            highest_permitted_action="MANAGE_AND_PLAN",
            action_law_state="RESTRICTED",
            fresh_capital_policy="BLOCKED",
            system_routing_state="LOCKED",
            routing_ready_lanes=[],
            routing_watch_lanes=[],
            outcome_aggregate_wr_4h=60.0,
            outcome_aggregate_n=200,
            outcome_aggregate_sufficient=True,
            outcome_best_lane_wr_4h=60.0,
            outcome_best_lane_sufficient=True,
        )),
        # 6. R2: structural MET + outcome MET (wr >= 40%)
        ("r2_both_met", _snap(
            highest_permitted_action="CONDITIONAL_ROUTING",
            action_law_state="CONDITIONAL",
            fresh_capital_policy="BLOCKED",
            system_routing_state="CAUTIOUS",
            routing_ready_lanes=[],
            routing_watch_lanes=["perps"],
            outcome_aggregate_wr_4h=45.0,
            outcome_aggregate_n=30,
            outcome_aggregate_sufficient=True,
            outcome_best_lane_wr_4h=45.0,
            outcome_best_lane_sufficient=True,
        )),
        # 7. R2: structural MET + outcome UNMET (wr < 40%)
        ("r2_structural_met_outcome_unmet", _snap(
            highest_permitted_action="CONDITIONAL_ROUTING",
            action_law_state="CONDITIONAL",
            fresh_capital_policy="BLOCKED",
            system_routing_state="CAUTIOUS",
            routing_ready_lanes=[],
            routing_watch_lanes=["perps"],
            outcome_aggregate_wr_4h=35.0,
            outcome_aggregate_n=30,
            outcome_aggregate_sufficient=True,
            outcome_best_lane_wr_4h=35.0,
            outcome_best_lane_sufficient=True,
        )),
        # 8. R3: best lane exactly 50% → MET
        ("r3_best_lane_50pct", _snap(
            highest_permitted_action="SELECTIVE_DEPLOYMENT",
            action_law_state="PERMISSIVE",
            fresh_capital_policy="SELECTIVE",
            system_routing_state="OPEN",
            routing_ready_lanes=["perps"],
            routing_watch_lanes=[],
            outcome_aggregate_wr_4h=50.0,
            outcome_aggregate_n=50,
            outcome_aggregate_sufficient=True,
            outcome_best_lane_wr_4h=50.0,
            outcome_best_lane_sufficient=True,
        )),
        # 9. R3: best lane 49% → UNMET
        ("r3_best_lane_49pct", _snap(
            highest_permitted_action="SELECTIVE_DEPLOYMENT",
            action_law_state="PERMISSIVE",
            fresh_capital_policy="SELECTIVE",
            system_routing_state="OPEN",
            routing_ready_lanes=["perps"],
            routing_watch_lanes=[],
            outcome_aggregate_wr_4h=49.0,
            outcome_aggregate_n=50,
            outcome_aggregate_sufficient=True,
            outcome_best_lane_wr_4h=49.0,
            outcome_best_lane_sufficient=True,
        )),
        # 10. Best-lane sufficient but from insufficient lane data → no best lane counted
        ("best_lane_insufficient_ignored", _snap(
            highest_permitted_action="SELECTIVE_DEPLOYMENT",
            action_law_state="PERMISSIVE",
            fresh_capital_policy="SELECTIVE",
            system_routing_state="OPEN",
            routing_ready_lanes=["perps"],
            routing_watch_lanes=[],
            outcome_aggregate_wr_4h=55.0,
            outcome_aggregate_n=50,
            outcome_aggregate_sufficient=True,
            outcome_best_lane_wr_4h=0,        # no sufficient lane has good wr
            outcome_best_lane_sufficient=False,  # no lane is sufficient
        )),
    ]

    # ── Expected: (scenario, rank) → { structural_met, outcome_met, condition_met }
    # Only assert on ranks that test something interesting
    _expected: dict[tuple[str, int], dict] = {
        # 1. No outcome data — outcome gates should be None (pass by default)
        ("no_outcome_data", 2): {"structural_met": False, "outcome_met": None, "condition_met": False},
        ("no_outcome_data", 3): {"structural_met": False, "outcome_met": None, "condition_met": False},

        # 2. Insufficient data — same: outcome=None, structural dominates
        ("insufficient_outcome_data", 2): {"structural_met": False, "outcome_met": None, "condition_met": False},

        # 3. All good — everything should be True
        ("sufficient_good_structural_open", 2): {"structural_met": True,  "outcome_met": True, "condition_met": True},
        ("sufficient_good_structural_open", 3): {"structural_met": True,  "outcome_met": True, "condition_met": True},
        ("sufficient_good_structural_open", 4): {"structural_met": True,  "outcome_met": True, "condition_met": True},

        # 4. Poor outcomes with structural open — outcome blocks R2, R3, R4
        ("sufficient_poor_structural_open", 2): {"structural_met": True,  "outcome_met": False, "condition_met": False},
        ("sufficient_poor_structural_open", 3): {"structural_met": True,  "outcome_met": False, "condition_met": False},
        ("sufficient_poor_structural_open", 4): {"structural_met": True,  "outcome_met": False, "condition_met": False},

        # 5. Structural locked + good outcomes — structural blocks, outcome irrelevant
        ("structural_locked_good_outcomes", 2): {"structural_met": False, "outcome_met": True,  "condition_met": False},
        ("structural_locked_good_outcomes", 3): {"structural_met": False, "outcome_met": True,  "condition_met": False},

        # 6. R2 both MET
        ("r2_both_met", 2): {"structural_met": True, "outcome_met": True, "condition_met": True},

        # 7. R2 structural MET but outcome UNMET → overall UNMET
        ("r2_structural_met_outcome_unmet", 2): {"structural_met": True, "outcome_met": False, "condition_met": False},

        # 8. R3 best lane exactly 50% → MET
        ("r3_best_lane_50pct", 3): {"structural_met": True, "outcome_met": True, "condition_met": True},

        # 9. R3 best lane 49% → UNMET
        ("r3_best_lane_49pct", 3): {"structural_met": True, "outcome_met": False, "condition_met": False},

        # 10. No sufficient lane for best-lane gate → outcome=None (passes by default)
        ("best_lane_insufficient_ignored", 3): {"structural_met": True, "outcome_met": None, "condition_met": True},
    }

    # ── Run ────────────────────────────────────────────────────────────────
    pass_count = 0
    fail_count = 0
    scenario_reports: list[dict] = []

    for label, snap in _scenarios:
        ladder = build_rank_progression(snap)
        row_results: list[dict] = []

        for entry in ladder:
            rank = entry["rank"]
            key = (label, rank)
            expected = _expected.get(key)
            if expected is None:
                continue  # no assertion for this rank in this scenario

            actual = {
                "structural_met": entry.get("structural_met"),
                "outcome_met":    entry.get("outcome_met"),
                "condition_met":  entry.get("condition_met"),
            }
            passed = (actual == expected)
            if passed:
                pass_count += 1
            else:
                fail_count += 1

            row_results.append({
                "rank":     rank,
                "expected": expected,
                "actual":   actual,
                "passed":   passed,
            })

        scenario_reports.append({
            "scenario": label,
            "results":  row_results,
        })

    total = pass_count + fail_count
    return {
        "validation":       "outcome_gate_harness",
        "pass":             pass_count,
        "fail":             fail_count,
        "total_assertions": total,
        "all_passed":       fail_count == 0,
        "scenarios":        scenario_reports,
    }


def build_rank_progression(snap: dict | None) -> list[dict]:
    """
    Build a rank-by-rank progression ladder from current snapshot data.

    Each entry shows:
      rank, name, unlocks (which actions become ALLOW at this rank),
      condition (human-readable), condition_met (bool or None if not evaluable),
      is_current (bool), is_reached (bool)

    Returns a list ordered from rank 0 to rank 4.
    """
    if snap is None:
        return []

    highest       = snap.get("highest_permitted_action", "PAPER_EXECUTION_ONLY")
    current_rank  = _LAW_RANK.get(highest, 0)
    fcp           = snap.get("fresh_capital_policy", "BLOCKED")

    # Evaluate conditions from snapshot where possible
    # Lane suspension states
    lane_states = {
        lane: snap.get(f"{lane}_suspension_state", "ACTIVE")
        for lane in ("memecoins", "spot", "perps", "whale")
    }
    any_active = any(s == "ACTIVE" for s in lane_states.values())
    any_watching_or_active = any(s in ("ACTIVE", "WATCHING") for s in lane_states.values())

    # Routing data (added in Phase B snapshot enrichment)
    sys_routing   = snap.get("system_routing_state")   # LOCKED / DEFENSIVE / CAUTIOUS / OPEN
    ready_lanes   = snap.get("routing_ready_lanes", [])
    watch_lanes   = snap.get("routing_watch_lanes", [])
    routing_watch_active = bool(watch_lanes) or bool(ready_lanes)
    routing_ready        = bool(ready_lanes) or sys_routing == "OPEN"
    fcp_open             = fcp in ("SELECTIVE", "OPEN")
    fcp_fully_open       = fcp == "OPEN"

    # ── Phase 4 Step 2: outcome attribution from snapshot ────────────────
    agg_wr_4h       = snap.get("outcome_aggregate_wr_4h")        # float | None
    agg_n           = snap.get("outcome_aggregate_n", 0)          # int
    agg_sufficient  = snap.get("outcome_aggregate_sufficient", False)
    best_lane_wr    = snap.get("outcome_best_lane_wr_4h")         # float | None
    best_lane_suf   = snap.get("outcome_best_lane_sufficient", False)

    # Outcome gate helpers — pass by default when data is insufficient
    def _outcome_gate(required_wr: float, min_outcomes: int) -> tuple[bool | None, str]:
        """
        Evaluate an outcome gate.  Returns (met, description).
        met=True  — sufficient data and wr meets threshold
        met=False — sufficient data but wr below threshold
        met=None  — insufficient data, gate passes by default
        """
        if not agg_sufficient or agg_n < min_outcomes:
            return None, f"aggregate wr_4h ≥ {required_wr}% over ≥{min_outcomes} outcomes (insufficient data — passes by default)"
        met = agg_wr_4h is not None and agg_wr_4h >= required_wr
        return met, f"aggregate wr_4h ≥ {required_wr}% over ≥{min_outcomes} outcomes (current: {agg_wr_4h}% over {agg_n})"

    def _best_lane_gate(required_wr: float) -> tuple[bool | None, str]:
        """Evaluate a best-lane outcome gate."""
        if not best_lane_suf:
            return None, f"best lane wr_4h ≥ {required_wr}% with sufficient data (no lane has enough data — passes by default)"
        met = best_lane_wr is not None and best_lane_wr >= required_wr
        return met, f"best lane wr_4h ≥ {required_wr}% (current best: {best_lane_wr}%)"

    # Rank 2 outcome gate: aggregate wr_4h >= 40% over >= 20 outcomes
    r2_outcome_met, r2_outcome_desc = _outcome_gate(40.0, 20)
    # Rank 3 outcome gate: at least one lane with wr_4h >= 50%
    r3_outcome_met, r3_outcome_desc = _best_lane_gate(50.0)
    # Rank 4 outcome gate: aggregate wr_4h >= 50% over >= 20 outcomes
    r4_outcome_met, r4_outcome_desc = _outcome_gate(50.0, 20)

    def _combined(structural: bool | None, outcome: bool | None) -> bool | None:
        """Combine structural + outcome conditions. None = not evaluable → pass."""
        if structural is None:
            return None  # can't evaluate structural → can't evaluate overall
        if structural is False:
            return False  # structural blocks regardless
        # structural is True — check outcome
        if outcome is None:
            return True  # insufficient data → outcome passes by default
        return outcome  # both evaluable — outcome decides

    _RANK_LADDER = [
        {
            "rank":       0,
            "name":       "PAPER_EXECUTION_ONLY",
            "unlocks":    ["close_position", "reduce_risk", "paper_only"],
            "structural_condition":  "Base state — no conditions required.",
            "structural_met": True,
            "outcome_condition": None,
            "outcome_met": None,
            "condition_met": True,
        },
        {
            "rank":       1,
            "name":       "MANAGE_AND_PLAN",
            "unlocks":    ["manage_existing"],
            "structural_condition":  "One or more lanes active or armed.",
            "structural_met": any_active,
            "outcome_condition": None,
            "outcome_met": None,
            "condition_met": any_active,
        },
        {
            "rank":       2,
            "name":       "CONDITIONAL_ROUTING",
            "unlocks":    ["scale_existing"],
            "structural_condition":  "Routing watch active or a lane approaching reactivation.",
            "structural_met": routing_watch_active if sys_routing is not None else None,
            "outcome_condition": r2_outcome_desc,
            "outcome_met": r2_outcome_met,
            "condition_met": _combined(
                routing_watch_active if sys_routing is not None else None,
                r2_outcome_met,
            ),
        },
        {
            "rank":       3,
            "name":       "SELECTIVE_DEPLOYMENT",
            "unlocks":    ["new_entry (with FCP gate)"],
            "structural_condition":  "Routing ready + fresh_capital_policy opens to SELECTIVE or OPEN.",
            "structural_met": (routing_ready and fcp_open) if sys_routing is not None else None,
            "outcome_condition": r3_outcome_desc,
            "outcome_met": r3_outcome_met,
            "condition_met": _combined(
                (routing_ready and fcp_open) if sys_routing is not None else None,
                r3_outcome_met,
            ),
        },
        {
            "rank":       4,
            "name":       "FULL_DEPLOYMENT",
            "unlocks":    ["new_entry (unrestricted)"],
            "structural_condition":  "Routing ready + fresh_capital_policy = OPEN.",
            "structural_met": (routing_ready and fcp_fully_open) if sys_routing is not None else None,
            "outcome_condition": r4_outcome_desc,
            "outcome_met": r4_outcome_met,
            "condition_met": _combined(
                (routing_ready and fcp_fully_open) if sys_routing is not None else None,
                r4_outcome_met,
            ),
        },
    ]

    # Backwards-compatible "condition" field for frontend display
    for entry in _RANK_LADDER:
        parts = [entry["structural_condition"]]
        if entry.get("outcome_condition"):
            parts.append(entry["outcome_condition"])
        entry["condition"] = " + ".join(parts)
        entry["is_current"] = entry["rank"] == current_rank
        entry["is_reached"] = entry["rank"] <= current_rank

    return _RANK_LADDER


def _per_action_table(current_rank: int, fcp: str) -> dict:
    """Per-action-class readiness table."""
    return {
        "new_entry": {
            "enforcement_ready": current_rank >= 3 and fcp != "BLOCKED",
            "current_verdict":   "ALLOW" if (current_rank >= 3 and fcp != "BLOCKED") else "BLOCK",
            "required_rank":     3,
            "current_rank":      current_rank,
            "fcp_gate":          fcp,
        },
        "scale_existing": {
            "enforcement_ready": current_rank >= 2,
            "current_verdict":   "ALLOW" if current_rank >= 2 else "BLOCK",
            "required_rank":     2,
            "current_rank":      current_rank,
            "fcp_gate":          "n/a",
        },
        "manage_existing": {
            "enforcement_ready": current_rank >= 1,
            "current_verdict":   "ALLOW" if current_rank >= 1 else "BLOCK",
            "required_rank":     1,
            "current_rank":      current_rank,
            "fcp_gate":          "n/a",
        },
        "close_position": {
            "enforcement_ready": True,
            "current_verdict":   "ALLOW",
            "required_rank":     0,
            "current_rank":      current_rank,
            "fcp_gate":          "n/a",
        },
        "reduce_risk": {
            "enforcement_ready": True,
            "current_verdict":   "ALLOW",
            "required_rank":     0,
            "current_rank":      current_rank,
            "fcp_gate":          "n/a",
        },
    }


def _decision_stream_summary(decisions: list[dict]) -> dict:
    """Compact summary of recent authority decision counts."""
    total      = len(decisions)
    by_verdict: dict[str, int] = {}
    by_action:  dict[str, int] = {}
    for d in decisions:
        v = d.get("verdict", "ALLOW")
        a = d.get("proposed_action", "unknown")
        by_verdict[v] = by_verdict.get(v, 0) + 1
        by_action[a]  = by_action.get(a, 0) + 1
    return {
        "total":      total,
        "by_verdict": by_verdict,
        "by_action":  by_action,
    }
