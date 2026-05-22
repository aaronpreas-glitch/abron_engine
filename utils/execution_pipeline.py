"""
utils/execution_pipeline.py — Phase 5 Step 4: Shared Execution Intent Pipeline

One compact flow for all Phase 5 automation:
  1. Propose an action (source, lane, action_type, symbol)
  2. Resolve authority (calls resolve_action)
  3. Check Phase 6 safety guardrails (kill switch, daily caps, freshness)
  4. Execute if mode=EXECUTE and all gates pass
  5. Record intent with full audit trail
  6. Return structured result

Used by exit_monitor, dca_monitor, entry_monitor, and future automation slices.

Design:
  - Authority BLOCK always prevents execution (no enforce-flag dependency)
  - Exit actions (close_position, reduce_risk) are never blocked in practice (rank 0)
  - The caller provides execute_fn — this module never imports executor code
  - Never raises — fails open for monitoring, logs errors in execution_result

Phase 6 safety guardrails (additive to authority, never replace it):
  - AUTO_EXECUTION_ENABLED (global kill switch, default false)
  - Daily caps per lane+action_type (configurable via env vars)
  - Signal freshness gate (optional, caller passes signal_ts)
"""

import logging
import os
import sys
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


# ── Phase 6 Step 1: safety guardrail helpers ─────────────────────────────────

def _auto_execution_enabled() -> bool:
    """Global kill switch — if false, all automation runs in OBSERVE regardless."""
    return os.getenv("AUTO_EXECUTION_ENABLED", "false").lower() == "true"


def _get_daily_cap(lane: str, action_type: str) -> int:
    """
    Read per-lane-action daily cap from env.

    Env var pattern: {LANE}_{ACTION}_DAILY_CAP
    Examples:
      SPOT_CLOSE_POSITION_DAILY_CAP=10
      SPOT_NEW_ENTRY_DAILY_CAP=3
      MEMECOINS_NEW_ENTRY_DAILY_CAP=5

    Default: 10 for exits, 5 for entries/DCA, 0 means unlimited.
    """
    key = f"{lane.upper()}_{action_type.upper()}_DAILY_CAP"
    raw = os.getenv(key, "")
    if raw.strip():
        try:
            return int(raw.strip())
        except ValueError:
            pass
    # Sensible defaults
    if action_type in ("close_position", "reduce_risk"):
        return 10
    return 5


def _count_today_executed(lane: str, action_type: str) -> int:
    """Count execution_intents rows with executed=1 in the last 24h for lane+action_type."""
    try:
        from db import get_conn  # type: ignore[import]
        cutoff = datetime.now(tz=timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        ).isoformat()
        with get_conn() as conn:
            row = conn.execute(
                """SELECT COUNT(*) FROM execution_intents
                   WHERE lane = ? AND action_type = ? AND executed = 1
                     AND ts_utc >= ?""",
                (lane, action_type, cutoff),
            ).fetchone()
            return row[0] if row else 0
    except Exception:
        return 0


_MAX_SIGNAL_AGE_S_DEFAULT = 7200  # 2 hours


def _max_signal_age_s() -> int:
    """Signal freshness threshold in seconds."""
    raw = os.getenv("MAX_SIGNAL_AGE_S", "")
    if raw.strip():
        try:
            return int(raw.strip())
        except ValueError:
            pass
    return _MAX_SIGNAL_AGE_S_DEFAULT


def _signal_age_s(signal_ts: str | None) -> float | None:
    """Return age in seconds of a signal timestamp, or None if unparseable."""
    if not signal_ts:
        return None
    try:
        if isinstance(signal_ts, (int, float)):
            return (datetime.now(tz=timezone.utc).timestamp() - float(signal_ts))
        ts = datetime.fromisoformat(str(signal_ts))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return (datetime.now(tz=timezone.utc) - ts).total_seconds()
    except Exception:
        return None


# ── Automation status (read-only summary for operator controls) ───────────────

_MONITOR_GATES = [
    ("spot",      "exit",  "SPOT_AUTO_EXIT"),
    ("memecoins", "exit",  "MEMECOIN_AUTO_EXIT"),
    ("spot",      "dca",   "SPOT_AUTO_DCA"),
    ("spot",      "entry", "SPOT_AUTO_ENTRY"),
    ("memecoins", "entry", "MEMECOIN_AUTO_ENTRY"),
]

_CAP_ACTIONS = [
    ("spot",      "close_position"),
    ("spot",      "new_entry"),
    ("spot",      "scale_existing"),
    ("memecoins", "close_position"),
    ("memecoins", "new_entry"),
]


def build_automation_status() -> dict:
    """
    Build a compact operator-facing summary of automation guardrail state.
    Called from the dashboard backend — no writes, pure read.
    """
    enabled = _auto_execution_enabled()
    max_age = _max_signal_age_s()

    # Per-monitor gate states
    monitors = []
    for lane, kind, env_key in _MONITOR_GATES:
        val = os.getenv(env_key, "false").lower() == "true"
        effective = "EXECUTE" if (enabled and val) else ("ARMED" if val else "OBSERVE")
        monitors.append({
            "lane": lane, "kind": kind, "env_key": env_key,
            "enabled": val, "effective_mode": effective,
        })

    # Daily cap usage
    caps = []
    for lane, action in _CAP_ACTIONS:
        cap = _get_daily_cap(lane, action)
        used = _count_today_executed(lane, action)
        caps.append({
            "lane": lane, "action_type": action,
            "cap": cap, "used_today": used,
            "remaining": max(0, cap - used) if cap > 0 else None,
            "exhausted": (used >= cap) if cap > 0 else False,
        })

    # Recent guardrail blocks from execution_intents
    guardrail_blocks = []
    try:
        from db import get_conn  # type: ignore[import]
        with get_conn() as conn:
            rows = conn.execute(
                """SELECT ts_utc, source, lane, action_type, symbol, execution_result
                   FROM execution_intents
                   WHERE execution_result LIKE 'guardrail_blocked%'
                   ORDER BY id DESC LIMIT 10"""
            ).fetchall()
            guardrail_blocks = [dict(r) for r in rows]
    except Exception:
        pass

    return {
        "auto_execution_enabled": enabled,
        "max_signal_age_s":       max_age,
        "monitors":               monitors,
        "daily_caps":             caps,
        "recent_guardrail_blocks": guardrail_blocks,
    }


# ── Main pipeline ────────────────────────────────────────────────────────────

def propose_and_execute(
    source: str,
    lane: str,
    action_type: str,
    symbol: str | None = None,
    amount_usd: float | None = None,
    mode: str = "OBSERVE",
    execute_fn: object = None,
    notes: str = "",
    signal_ts: str | None = None,
) -> dict:
    """
    Shared execution intent pipeline.

    Args:
        source:       caller identifier (e.g. "spot_exit_monitor", "spot_dca_monitor")
        lane:         "spot" | "perps" | "memecoins"
        action_type:  "new_entry" | "scale_existing" | "close_position" | "reduce_risk" | etc.
        symbol:       token symbol (optional)
        amount_usd:   trade size (optional)
        mode:         "OBSERVE" (log only) | "EXECUTE" (log + act)
        execute_fn:   callable() -> dict with "success" key. Called only if mode=EXECUTE
                      and all gates pass. None = observe-only regardless of mode.
        notes:        freeform context for the audit log
        signal_ts:    ISO timestamp or unix epoch of the originating signal (optional).
                      If provided and stale, execution is blocked with reason.

    Returns:
        {
            "authority_verdict":  str | None,
            "authority_blocked":  bool,
            "guardrail_blocked":  bool,
            "guardrail_reason":   str | None,
            "executed":           bool,
            "execution_result":   str | None,
            "intent_id":          int | None,
        }
    """
    # ── 1. Resolve authority ──────────────────────────────────────────────
    authority_verdict = None
    authority_reasons = None
    authority_blocked = False

    try:
        _utils = os.path.dirname(__file__)
        if _utils not in sys.path:
            sys.path.insert(0, _utils)
        from authority import resolve_action
        _auth = resolve_action(action_type, lane, executor=source)
        authority_verdict = _auth["verdict"]
        authority_reasons = _auth["reasons"]
        if _auth["verdict"] == "BLOCK":
            authority_blocked = True
    except Exception as exc:
        logger.debug("execution_pipeline authority error: %s", exc)

    # ── 2. Phase 6 safety guardrails (only matter in EXECUTE mode) ────────
    guardrail_blocked = False
    guardrail_reason = None

    if mode == "EXECUTE" and not authority_blocked:
        # 2a. Global kill switch
        if not _auto_execution_enabled():
            guardrail_blocked = True
            guardrail_reason = "kill_switch — AUTO_EXECUTION_ENABLED=false"

        # 2b. Daily cap
        if not guardrail_blocked:
            cap = _get_daily_cap(lane, action_type)
            if cap > 0:
                today_count = _count_today_executed(lane, action_type)
                if today_count >= cap:
                    guardrail_blocked = True
                    guardrail_reason = (
                        f"daily_cap — {lane}/{action_type} "
                        f"executed {today_count}/{cap} today"
                    )

        # 2c. Signal freshness
        if not guardrail_blocked and signal_ts is not None:
            age = _signal_age_s(signal_ts)
            max_age = _max_signal_age_s()
            if age is not None and age > max_age:
                guardrail_blocked = True
                guardrail_reason = (
                    f"stale_signal — age {int(age)}s > max {max_age}s"
                )

    # ── 3. Execute if all gates pass ──────────────────────────────────────
    executed = False
    execution_result = None

    if authority_blocked:
        execution_result = "authority_blocked"
    elif guardrail_blocked:
        execution_result = f"guardrail_blocked: {guardrail_reason}"
    elif mode == "EXECUTE" and execute_fn is not None:
        try:
            result = execute_fn()
            if isinstance(result, dict):
                executed = bool(result.get("success", False))
                if executed:
                    execution_result = "ok"
                else:
                    execution_result = result.get("error", "unknown_error")
            else:
                execution_result = "unexpected_return_type"
        except Exception as exc:
            execution_result = f"error: {exc}"

    # ── 4. Record intent ──────────────────────────────────────────────────
    intent_id = None
    try:
        from db import record_execution_intent
        intent_id = record_execution_intent(
            source=source,
            lane=lane,
            action_type=action_type,
            symbol=symbol,
            amount_usd=amount_usd,
            mode=mode,
            authority_verdict=authority_verdict,
            authority_reasons=authority_reasons,
            executed=executed,
            execution_result=execution_result,
            notes=notes,
        )
    except Exception as exc:
        logger.debug("execution_pipeline intent record error: %s", exc)

    # ── 5. Log ────────────────────────────────────────────────────────────
    if executed:
        logger.info(
            "PIPELINE_EXEC %s %s %s %s — %s",
            source, action_type, lane, symbol or "", notes[:60],
        )
    elif guardrail_blocked:
        logger.info(
            "PIPELINE_GUARDRAIL %s %s %s %s — %s",
            source, action_type, lane, symbol or "", guardrail_reason,
        )
    else:
        logger.debug(
            "PIPELINE_INTENT %s %s %s %s mode=%s verdict=%s — %s",
            source, action_type, lane, symbol or "", mode,
            authority_verdict or "?", notes[:60],
        )

    return {
        "authority_verdict":  authority_verdict,
        "authority_blocked":  authority_blocked,
        "guardrail_blocked":  guardrail_blocked,
        "guardrail_reason":   guardrail_reason,
        "executed":           executed,
        "execution_result":   execution_result,
        "intent_id":          intent_id,
    }
