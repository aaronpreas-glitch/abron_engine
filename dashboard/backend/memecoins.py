"""
Memecoin scanner endpoints — Patches 115, 116, 117, 125.

Routes:
  GET  /api/memecoins/status     — scanner signals + open positions + stats
  POST /api/memecoins/buy        — buy a memecoin by mint address
  POST /api/memecoins/sell/{mint} — sell an open position
  GET  /api/memecoins/analytics  — score buckets, rug breakdown, tuner progress
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import time

from fastapi import APIRouter, Depends, HTTPException

import config as _config  # noqa: F401  # ensures .env is loaded for ad-hoc router imports
from auth import get_current_user
from routers._shared import _ensure_engine_path, _db_path

log = logging.getLogger("dashboard")
router = APIRouter(prefix="/api/memecoins", tags=["memecoins"])
router_v3 = APIRouter(prefix="/api/v3/memecoins", tags=["memecoins-v3"])


def _choose_continuation_memory(
    exact_snapshot: dict | None,
    candidate_snapshots: list[tuple[str, dict | None]],
) -> tuple[dict, str]:
    """Pick the best continuation-memory source, not just the largest bucket."""

    def _default_snapshot() -> dict:
        return {
            "state": "THIN",
            "confidence": "LOW",
            "trailing_n": 0,
            "trailing_weighted_n": 0.0,
            "trailing_wr": None,
            "trailing_avg": None,
            "cat_rate": None,
            "lifetime_n": 0,
            "lifetime_weighted_n": 0.0,
            "lifetime_wr": None,
            "lifetime_avg": None,
            "wr_delta": None,
            "avg_delta": None,
        }

    def _score(source: str, snap: dict) -> float:
        source_bonus = {
            "EXACT_SETUP": 8.0,
            "PROFILE_FRESH_REINFORCED_ARCHETYPE": 7.6,
            "PROFILE_REINFORCED_ARCHETYPE": 7.2,
            "PROFILE_ARCHETYPE": 6.5,
            "PROFILE_FUEL_PHASE": 6.0,
            "PROFILE_WINDOW_PHASE": 4.0,
            "PROFILE_PHASE": 2.0,
        }.get(source, 0.0)
        state_bonus = {
            "COMPOUNDING": 8.0,
            "MIXED": 2.0,
            "THIN": 0.0,
            "FRAGILE": -8.0,
        }.get(str(snap.get("state") or "THIN"), 0.0)
        conf_bonus = {
            "HIGH": 4.0,
            "MEDIUM": 2.0,
            "LOW": 0.0,
        }.get(str(snap.get("confidence") or "LOW"), 0.0)
        trailing_weight = float(
            snap.get("trailing_weighted_n")
            or snap.get("trailing_n")
            or 0.0
        )
        n_bonus = min(trailing_weight, 10.0) * 0.35
        wr_delta = snap.get("wr_delta")
        avg_delta = snap.get("avg_delta")
        trend_bonus = 0.0
        if wr_delta is not None:
            trend_bonus += max(min(float(wr_delta) * 0.15, 3.0), -3.0)
        if avg_delta is not None:
            trend_bonus += max(min(float(avg_delta) * 0.10, 2.0), -2.0)
        return source_bonus + state_bonus + conf_bonus + n_bonus + trend_bonus

    best_snap = dict(exact_snapshot or _default_snapshot())
    best_src = "EXACT_SETUP"
    best_score = _score(best_src, best_snap)

    for src, snap in candidate_snapshots:
        if not snap:
            continue
        cur = dict(snap)
        cur_score = _score(src, cur)
        if cur_score > best_score:
            best_snap = cur
            best_src = src
            best_score = cur_score

    return best_snap, best_src


def _continuation_memory_authority(
    mem: dict | None,
    mem_source: str | None = None,
    reinforcement_bucket: str | None = None,
    freshness_bucket: str | None = None,
) -> str:
    """Classify how forcefully continuation memory should influence judgment."""
    snap = dict(mem or {})
    state = str(snap.get("state") or "THIN")
    confidence = str(snap.get("confidence") or "LOW")
    weighted_n = float(snap.get("trailing_weighted_n") or snap.get("trailing_n") or 0.0)
    source = str(mem_source or "EXACT_SETUP")
    reinforcement = str(reinforcement_bucket or "PLAIN").strip() or "PLAIN"
    freshness = str(freshness_bucket or "UNKNOWN").strip() or "UNKNOWN"
    precise_source = source in (
        "EXACT_SETUP",
        "PROFILE_FRESH_REINFORCED_ARCHETYPE",
        "PROFILE_REINFORCED_ARCHETYPE",
        "PROFILE_ARCHETYPE",
        "PROFILE_FUEL_PHASE",
    )

    if state == "FRAGILE" and weighted_n >= 3.0:
        return "ADVERSE"
    if state == "FRAGILE":
        return "TENTATIVE"

    if (
        state == "COMPOUNDING"
        and precise_source
        and confidence in ("HIGH", "MEDIUM")
        and weighted_n >= 5.0
    ):
        return "FORCEFUL"
    if (
        state == "COMPOUNDING"
        and reinforcement == "STRONG_REINFORCED"
        and weighted_n >= 4.0
        and confidence in ("HIGH", "MEDIUM")
    ):
        return "FORCEFUL"
    if (
        state == "COMPOUNDING"
        and freshness == "RECENT_REACTIVATION"
        and weighted_n >= 5.0
        and confidence == "HIGH"
    ):
        return "FORCEFUL"

    if (
        state == "COMPOUNDING"
        and weighted_n >= 3.0
        and (precise_source or confidence in ("HIGH", "MEDIUM"))
    ):
        return "BACKED"
    if (
        state == "MIXED"
        and precise_source
        and confidence in ("HIGH", "MEDIUM")
        and weighted_n >= 5.0
    ):
        return "BACKED"

    return "TENTATIVE"


def _fresh_catalyst_bucket(
    lifecycle_state: str | None,
    scan_best: bool,
    appearances_72h: int,
    hours_since_state_change: float | None,
    vol_acceleration: float | None,
) -> str:
    """Classify why a fresh-qualified continuation is interesting right now."""
    state = str(lifecycle_state or "").strip().upper()
    if (
        state in ("RELOAD", "REVIVAL")
        and hours_since_state_change is not None
        and hours_since_state_change <= 24.0 * 7.0
    ):
        return "REACTIVATION_DRIVEN"
    if scan_best and (vol_acceleration or 0.0) >= 3.0:
        return "SCAN_MOMO"
    if appearances_72h >= 3:
        return "PERSISTENCE_DRIVEN"
    return "THIN_CATALYST"


def _fresh_discovery_authority(
    freshness_memory: dict | None,
    catalyst_memory: dict | None,
    freshness_bucket: str | None = None,
    catalyst_bucket: str | None = None,
) -> str:
    """Authority for safe-fresh discovery paths, not just continuation memory."""
    fresh = dict(freshness_memory or {})
    catalyst = dict(catalyst_memory or {})
    fresh_state = str(fresh.get("state") or "THIN")
    fresh_conf = str(fresh.get("confidence") or "LOW")
    fresh_n = float(fresh.get("trailing_weighted_n") or fresh.get("trailing_n") or 0.0)
    catalyst_state = str(catalyst.get("state") or "THIN")
    catalyst_conf = str(catalyst.get("confidence") or "LOW")
    catalyst_n = float(catalyst.get("trailing_weighted_n") or catalyst.get("trailing_n") or 0.0)
    fresh_bucket = str(freshness_bucket or "UNKNOWN").strip().upper() or "UNKNOWN"
    catalyst_bucket = str(catalyst_bucket or "UNKNOWN").strip().upper() or "UNKNOWN"

    if (
        (fresh_state == "FRAGILE" and fresh_n >= 3.0)
        or (catalyst_state == "FRAGILE" and catalyst_n >= 3.0)
    ):
        return "ADVERSE"

    if (
        fresh_state == "COMPOUNDING"
        and catalyst_state == "COMPOUNDING"
        and fresh_conf in ("HIGH", "MEDIUM")
        and catalyst_conf in ("HIGH", "MEDIUM")
        and fresh_n >= 4.0
        and catalyst_n >= 4.0
    ):
        return "FORCEFUL"

    if (
        fresh_state == "COMPOUNDING"
        and fresh_conf in ("HIGH", "MEDIUM")
        and fresh_n >= 3.0
        and catalyst_bucket in ("REACTIVATION_DRIVEN", "SCAN_MOMO", "PERSISTENCE_DRIVEN")
    ):
        return "BACKED"
    if (
        catalyst_state == "COMPOUNDING"
        and catalyst_conf in ("HIGH", "MEDIUM")
        and catalyst_n >= 3.0
        and fresh_bucket in ("RECENT_REACTIVATION", "NEWLY_QUALIFIED")
    ):
        return "BACKED"

    return "TENTATIVE"


def _proof_stack_authority(validation_support: dict | None) -> str:
    """Classify how much authority the validation/proof stack has earned."""
    support = dict(validation_support or {})
    trust_signal = str(((support.get("trust_labels") or {}).get("signal")) or "ACCUMULATING")
    triage_signal = str(((support.get("triage_states") or {}).get("signal")) or "ACCUMULATING")
    transition_signal = str(((support.get("transition_detector") or {}).get("signal")) or "ACCUMULATING")
    trust_clean_n = int(((support.get("trust_labels") or {}).get("clean_n")) or 0)
    triage_clean_n = int(((support.get("triage_states") or {}).get("clean_n")) or 0)

    promotive_layers = sum(1 for s in (trust_signal, triage_signal, transition_signal) if s == "PROMOTIVE")
    early_layers = sum(1 for s in (trust_signal, triage_signal, transition_signal) if s == "EARLY")
    adverse_layers = sum(1 for s in (trust_signal, triage_signal, transition_signal) if s == "ADVERSE")

    if adverse_layers > 0:
        return "ADVERSE"
    if (
        trust_signal == "PROMOTIVE"
        and triage_signal == "PROMOTIVE"
        and transition_signal in ("PROMOTIVE", "EARLY")
        and min(trust_clean_n, triage_clean_n) >= 20
    ):
        return "FORCEFUL"
    if promotive_layers >= 2:
        return "FORCEFUL"
    if promotive_layers >= 1 and early_layers >= 1:
        return "BACKED"
    if promotive_layers >= 1 or early_layers >= 2:
        return "BACKED"
    return "TENTATIVE"


def _continuation_promotion_authority(
    proof_stack_authority: str | None,
    continuation_memory_authority: str | None,
    reinforcement_bucket: str | None = None,
    fresh_discovery_authority: str | None = None,
) -> str:
    """Combined authority for whether a continuation deserves promotion pressure."""
    proof_auth = str(proof_stack_authority or "TENTATIVE").strip().upper() or "TENTATIVE"
    mem_auth = str(continuation_memory_authority or "TENTATIVE").strip().upper() or "TENTATIVE"
    reinforcement = str(reinforcement_bucket or "PLAIN").strip().upper() or "PLAIN"
    fresh_auth = str(fresh_discovery_authority or "TENTATIVE").strip().upper() or "TENTATIVE"

    if "ADVERSE" in (proof_auth, mem_auth, fresh_auth):
        return "BLOCKED"
    if (
        proof_auth == "FORCEFUL"
        and mem_auth == "FORCEFUL"
        and reinforcement in ("REINFORCED", "STRONG_REINFORCED")
    ):
        return "STACKED"
    if (
        proof_auth in ("FORCEFUL", "BACKED")
        and mem_auth in ("FORCEFUL", "BACKED")
        and (
            reinforcement in ("REINFORCED", "STRONG_REINFORCED")
            or fresh_auth in ("FORCEFUL", "BACKED")
        )
    ):
        return "SUPPORTED"
    if mem_auth in ("FORCEFUL", "BACKED") and proof_auth in ("FORCEFUL", "BACKED"):
        return "SUPPORTED"
    return "TENTATIVE"


def _reinforcement_authority(
    support_overlap_score: float | int | None,
    reinforcement_bucket: str | None,
    proof_stack_authority: str | None = None,
) -> str:
    """Authority for whether cross-lane reinforcement deserves real decision weight."""
    score = float(support_overlap_score or 0.0)
    bucket = str(reinforcement_bucket or "PLAIN").strip().upper() or "PLAIN"
    proof_auth = str(proof_stack_authority or "TENTATIVE").strip().upper() or "TENTATIVE"

    if bucket == "PLAIN" or score <= 0.0:
        return "ABSENT"
    if bucket == "STRONG_REINFORCED" and score >= 8.0 and proof_auth in ("FORCEFUL", "BACKED"):
        return "FORCEFUL"
    if bucket in ("REINFORCED", "STRONG_REINFORCED") and score >= 3.0 and proof_auth in ("FORCEFUL", "BACKED", "TENTATIVE"):
        return "BACKED"
    return "TENTATIVE"


def _deployment_authority(
    promotion_authority: str | None,
    reinforcement_authority: str | None,
    capital_ready_state: str | None,
    capital_route_bucket: str | None,
    capital_headroom_bucket: str | None,
) -> str:
    """Combined authority for whether a candidate is actually deployable now."""
    promotion = str(promotion_authority or "TENTATIVE").strip().upper() or "TENTATIVE"
    reinforcement = str(reinforcement_authority or "ABSENT").strip().upper() or "ABSENT"
    ready = str(capital_ready_state or "UNKNOWN").strip().upper() or "UNKNOWN"
    route = str(capital_route_bucket or "LOCKED").strip().upper() or "LOCKED"
    headroom = str(capital_headroom_bucket or "UNKNOWN").strip().upper() or "UNKNOWN"

    if promotion == "BLOCKED" or route == "LOCKED" or headroom == "EXHAUSTED":
        return "BLOCKED"
    if ready != "READY":
        return "PLANNING_ONLY"
    if (
        promotion == "STACKED"
        and reinforcement == "FORCEFUL"
        and route == "SCALE_READY"
        and headroom == "AMPLE"
    ):
        return "EXECUTABLE"
    if (
        promotion in ("STACKED", "SUPPORTED")
        and reinforcement in ("FORCEFUL", "BACKED")
        and route in ("SCALE_READY", "DISCIPLINED_PROBE", "MICRO_PROBE_ONLY")
        and headroom in ("AMPLE", "WORKABLE", "THIN")
    ):
        return "CONDITIONALLY_READY"
    return "PLANNING_ONLY"


def _decision_authority(
    proof_stack_authority: str | None,
    continuation_memory_authority: str | None,
    fresh_discovery_authority: str | None,
    reinforcement_authority: str | None,
    promotion_authority: str | None,
    deployment_authority: str | None,
) -> str:
    """Top-level authority for whether the continuation stack deserves action now."""
    proof_auth = str(proof_stack_authority or "TENTATIVE").strip().upper() or "TENTATIVE"
    mem_auth = str(continuation_memory_authority or "TENTATIVE").strip().upper() or "TENTATIVE"
    fresh_auth = str(fresh_discovery_authority or "TENTATIVE").strip().upper() or "TENTATIVE"
    reinf_auth = str(reinforcement_authority or "ABSENT").strip().upper() or "ABSENT"
    promo_auth = str(promotion_authority or "TENTATIVE").strip().upper() or "TENTATIVE"
    deploy_auth = str(deployment_authority or "PLANNING_ONLY").strip().upper() or "PLANNING_ONLY"

    if (
        proof_auth == "ADVERSE"
        or mem_auth == "ADVERSE"
        or fresh_auth == "ADVERSE"
        or promo_auth == "BLOCKED"
        or deploy_auth == "BLOCKED"
    ):
        return "BLOCKED"
    if (
        proof_auth == "FORCEFUL"
        and mem_auth == "FORCEFUL"
        and reinf_auth == "FORCEFUL"
        and promo_auth == "STACKED"
        and deploy_auth == "EXECUTABLE"
    ):
        return "STACKED"
    if (
        proof_auth in ("FORCEFUL", "BACKED")
        and mem_auth in ("FORCEFUL", "BACKED")
        and promo_auth in ("STACKED", "SUPPORTED")
        and deploy_auth in ("EXECUTABLE", "CONDITIONALLY_READY")
        and reinf_auth in ("FORCEFUL", "BACKED", "TENTATIVE")
    ):
        return "SUPPORTED"
    return "TENTATIVE"


def _meme_lane_operating_summary(
    recommendation: str | None,
    confidence: str | None,
    proof_stack_authority: str | None,
    capital_context: dict | None,
    evidence_maturity: str | None = None,
) -> dict:
    """
    Compact Roadmap 2 lane-operating layer for the memecoin NBA surface.

    Synthesizes recommendation state, proof authority, and capital context
    into decision-grade lane vocabulary without duplicating the Home framework.
    All inputs come directly from already-computed NBA fields — no new queries.

    lane_operating_state values:
      PROOF_BUILDING   — paper-eligible, proof stack is still accumulating
      PLANNING_ONLY    — paper mode or deployment blocked by policy/capital
      EXECUTION_READY  — all gates pass for live execution
      MONITOR_RESEARCH — lifecycle structure forming, no scored signal yet
      BLOCKED          — hard blocker or adverse proof stack
      QUIET            — no data / empty scanner cache
    """
    rec        = str(recommendation    or "NO_DATA").strip().upper()   or "NO_DATA"
    conf       = str(confidence        or "LOW").strip().upper()        or "LOW"
    proof_auth = str(proof_stack_authority or "TENTATIVE").strip().upper() or "TENTATIVE"
    cap        = dict(capital_context  or {})
    ev_mat     = str(evidence_maturity or "EXPERIMENTAL").strip().upper() or "EXPERIMENTAL"

    mode      = str(cap.get("mode")                   or "PAPER").strip().upper()       or "PAPER"
    auto_buy  = bool(cap.get("auto_buy"))
    route     = str(cap.get("capital_route_bucket")   or "LOCKED").strip().upper()      or "LOCKED"
    headroom  = str(cap.get("capital_headroom_bucket") or "UNKNOWN").strip().upper()    or "UNKNOWN"
    window    = str(cap.get("capital_window_bucket")  or "CLOSED").strip().upper()      or "CLOSED"

    def _check(name: str, passed: bool, note: str) -> dict:
        return {"name": name, "passed": bool(passed), "note": note}

    checklist = [
        _check(
            "proof_stack_backed",
            proof_auth in ("BACKED", "FORCEFUL"),
            f"Proof stack authority is {proof_auth}.",
        ),
        _check(
            "recommendation_actionable",
            rec in ("EXECUTE_PAPER", "WATCH"),
            f"Current recommendation is {rec}.",
        ),
        _check(
            "route_non_locked",
            route not in ("LOCKED",),
            f"Capital route bucket is {route}.",
        ),
        _check(
            "headroom_available",
            headroom not in ("EXHAUSTED",),
            f"Capital headroom bucket is {headroom}.",
        ),
    ]
    passed_checks = sum(1 for c in checklist if c["passed"])
    total_checks  = len(checklist)

    # Lane operating state (priority order)
    if rec == "DO_NOT_TOUCH" or proof_auth == "ADVERSE":
        lane_operating_state = "BLOCKED"
        lane_operating_note  = (
            "Hard blocker detected — candidate pool or proof stack is adverse."
        )
    elif rec == "NO_DATA":
        lane_operating_state = "QUIET"
        lane_operating_note  = (
            "Scanner cache is empty or no lifecycle candidates qualify."
        )
    elif mode == "PAPER" or not auto_buy:
        if rec == "EXECUTE_PAPER" and proof_auth in ("BACKED", "FORCEFUL"):
            lane_operating_state = "PROOF_BUILDING"
            lane_operating_note  = (
                f"Paper execution is policy-eligible. "
                f"Proof stack authority is {proof_auth} — building toward deployment unlock."
            )
        else:
            lane_operating_state = "PLANNING_ONLY"
            lane_operating_note  = (
                "Memecoin lane is in paper/planning mode. "
                + (
                    "Recommendation available for paper execution."
                    if rec in ("EXECUTE_PAPER", "WATCH")
                    else "No currently-eligible candidate."
                )
            )
    elif route == "LOCKED" or headroom == "EXHAUSTED":
        lane_operating_state = "PLANNING_ONLY"
        lane_operating_note  = (
            f"Route is {route} and headroom is {headroom} — deployment blocked by capital policy."
        )
    elif rec == "EXECUTE_PAPER" and route not in ("LOCKED",) and headroom not in ("EXHAUSTED",):
        lane_operating_state = "EXECUTION_READY"
        lane_operating_note  = (
            f"Candidate is eligible for live execution. "
            f"Route={route}, headroom={headroom}, confidence={conf}."
        )
    elif rec == "WATCH":
        lane_operating_state = "MONITOR_RESEARCH"
        lane_operating_note  = (
            "Lifecycle structure is forming, but no scored signal yet. Stay in research mode."
        )
    else:
        lane_operating_state = "PLANNING_ONLY"
        lane_operating_note  = (
            f"Current recommendation is {rec}. Lane is in disciplined wait."
        )

    # Next unlock
    if lane_operating_state == "BLOCKED":
        next_unlock_state = "UNBLOCK_REQUIRED"
        next_unlock_note  = (
            "Resolve adverse proof stack or hard blocker before any other unlock."
        )
    elif lane_operating_state == "QUIET":
        next_unlock_state = "FRESH_SIGNAL"
        next_unlock_note  = (
            "Lane needs a fresh scanner signal or lifecycle candidate to activate."
        )
    elif lane_operating_state == "PROOF_BUILDING":
        if proof_auth == "TENTATIVE":
            next_unlock_state = "PROOF_STACK_BACKED"
            next_unlock_note  = (
                "Build proof stack to BACKED before deployment policy can unlock."
            )
        else:
            next_unlock_state = "ROUTE_OPEN"
            next_unlock_note  = (
                "Proof is building. Route and mode upgrade needed for full deployment unlock."
            )
    elif lane_operating_state == "PLANNING_ONLY":
        if proof_auth not in ("BACKED", "FORCEFUL"):
            next_unlock_state = "PROOF_STACK_BACKED"
            next_unlock_note  = (
                "Memecoin lane needs stronger proof-stack authority before policy can loosen."
            )
        elif route == "LOCKED":
            next_unlock_state = "ROUTE_OPEN"
            next_unlock_note  = "Capital route must open before deployment is permitted."
        elif headroom in ("THIN", "EXHAUSTED"):
            next_unlock_state = "HEADROOM_RECOVERY"
            next_unlock_note  = "Wait for headroom recovery before adding new exposure."
        elif mode == "PAPER":
            next_unlock_state = "MODE_UPGRADE"
            next_unlock_note  = (
                "Mode is PAPER — mode upgrade to PILOT or LIVE is the next deployment gate."
            )
        else:
            next_unlock_state = "MAINTAIN"
            next_unlock_note  = "Lane is in good shape. Maintain current setup quality."
    elif lane_operating_state == "MONITOR_RESEARCH":
        next_unlock_state = "SCORED_SIGNAL"
        next_unlock_note  = (
            "Lane needs a candidate with a scored scan signal to move from watch to execute."
        )
    elif lane_operating_state == "EXECUTION_READY":
        next_unlock_state = "MAINTAIN"
        next_unlock_note  = (
            "Lane has execution authority. Maintain candidate quality and capital discipline."
        )
    else:
        next_unlock_state = "MAINTAIN"
        next_unlock_note  = "No immediate unlock pressure."

    # Primary constraint (most blocking)
    if proof_auth == "ADVERSE":
        primary_constraint_key  = "PROOF_ADVERSE"
        primary_constraint_note = (
            "Proof stack is adverse — pause execution and review validation layers."
        )
    elif rec == "DO_NOT_TOUCH":
        primary_constraint_key  = "HARD_BLOCKER"
        primary_constraint_note = (
            "Candidate pool has hard blockers (rug fuel / danger labels). Do not execute."
        )
    elif rec == "NO_DATA":
        primary_constraint_key  = "NO_ACTIVE_SIGNAL"
        primary_constraint_note = (
            "Scanner cache is empty or no lifecycle candidates qualify right now."
        )
    elif mode == "PAPER" or not auto_buy:
        primary_constraint_key  = "MODE_PAPER"
        primary_constraint_note = (
            "Runtime mode is PAPER or auto-buy is off. Execution is planning-only."
        )
    elif route == "LOCKED":
        primary_constraint_key  = "ROUTE_LOCKED"
        primary_constraint_note = str(
            cap.get("capital_route_note") or "Capital route is locked."
        )
    elif headroom == "EXHAUSTED":
        primary_constraint_key  = "HEADROOM_EXHAUSTED"
        primary_constraint_note = str(
            cap.get("capital_headroom_note") or "No effective headroom remains."
        )
    elif proof_auth == "TENTATIVE":
        primary_constraint_key  = "PROOF_TENTATIVE"
        primary_constraint_note = (
            "Proof stack authority is TENTATIVE — build more paper execution evidence."
        )
    elif rec not in ("EXECUTE_PAPER", "WATCH"):
        primary_constraint_key  = "NO_ACTIONABLE_CANDIDATE"
        primary_constraint_note = (
            f"No candidate currently earning paper-execution eligibility ({rec})."
        )
    elif headroom == "THIN":
        primary_constraint_key  = "HEADROOM_THIN"
        primary_constraint_note = str(
            cap.get("capital_headroom_note") or "Headroom is thin."
        )
    else:
        primary_constraint_key  = "NONE"
        primary_constraint_note = "No significant constraint detected."

    # Operating alignment: are proof, signal, and capital pointing the same direction?
    if proof_auth in ("BACKED", "FORCEFUL") and rec in ("EXECUTE_PAPER", "WATCH") and route not in ("LOCKED",):
        operating_alignment = "ALIGNED"
    elif proof_auth == "ADVERSE" or rec == "DO_NOT_TOUCH":
        operating_alignment = "MISALIGNED"
    else:
        operating_alignment = "PARTIAL"

    # Lane momentum — directional signal for cross-lane consumers
    _check_ratio = passed_checks / max(total_checks, 1)
    if lane_operating_state == "BLOCKED":
        lane_momentum = "BLOCKED"
    elif lane_operating_state == "QUIET":
        lane_momentum = "STALLING"
    elif lane_operating_state == "EXECUTION_READY" or (
        lane_operating_state == "PROOF_BUILDING" and _check_ratio >= 0.5
    ):
        lane_momentum = "ADVANCING"
    elif passed_checks == 0 and operating_alignment == "MISALIGNED":
        lane_momentum = "STALLING"
    else:
        lane_momentum = "BUILDING"

    # Unlock horizon — how many checks remain
    _outstanding = total_checks - passed_checks
    if next_unlock_state == "MAINTAIN" or _outstanding <= 0:
        unlock_horizon = "NONE"
    elif _outstanding == 1:
        unlock_horizon = "NEAR"
    elif _outstanding <= 3:
        unlock_horizon = "MEDIUM"
    else:
        unlock_horizon = "FAR"

    return {
        "lane_operating_state":   lane_operating_state,
        "lane_operating_note":    lane_operating_note,
        "next_unlock_state":      next_unlock_state,
        "next_unlock_note":       next_unlock_note,
        "primary_constraint_key":  primary_constraint_key,
        "primary_constraint_note": primary_constraint_note,
        "passed_checks":          passed_checks,
        "total_checks":           total_checks,
        "checklist":              checklist,
        "operating_alignment":    operating_alignment,
        "proof_stack_authority":  proof_auth,
        "capital_route_bucket":   route,
        "capital_headroom_bucket": headroom,
        "capital_window_bucket":  window,
        "lane_momentum":          lane_momentum,
        "unlock_horizon":         unlock_horizon,
    }


def _meme_candidate_operating_summary(candidate: dict | None) -> dict | None:
    """
    Compact Roadmap 2 operating layer for a single formatted memecoin candidate.

    Reads from the already-computed authority and capital fields on the candidate
    dict produced by _fmt_cand() — no new queries, no separate framework.
    Returns None when no candidate is present (preserves quiet-state honesty).

    candidate_operating_state values:
      STACKED       — all authority layers stacked, executable
      SUPPORTED     — strong decision + conditional deployment
      TENTATIVE     — decision authority is still tentative
      PLANNING_ONLY — not blocked, but deployment not open
      BLOCKED       — triage DO_NOT_TOUCH or hard decision/deploy block
    """
    if not candidate:
        return None
    c = dict(candidate)

    proof_auth  = str(c.get("proof_stack_authority")         or "TENTATIVE").strip().upper() or "TENTATIVE"
    mem_auth    = str(c.get("continuation_memory_authority") or "TENTATIVE").strip().upper() or "TENTATIVE"
    reinf_auth  = str(c.get("reinforcement_authority")       or "ABSENT").strip().upper()    or "ABSENT"
    promo_auth  = str(c.get("promotion_authority")           or "TENTATIVE").strip().upper() or "TENTATIVE"
    deploy_auth = str(c.get("deployment_authority")          or "PLANNING_ONLY").strip().upper() or "PLANNING_ONLY"
    dec_auth    = str(c.get("decision_authority")            or "TENTATIVE").strip().upper() or "TENTATIVE"
    route       = str(c.get("capital_route_bucket")          or "LOCKED").strip().upper()    or "LOCKED"
    headroom    = str(c.get("capital_headroom_bucket")       or "UNKNOWN").strip().upper()   or "UNKNOWN"
    ready       = str(c.get("capital_ready_state")           or "UNKNOWN").strip().upper()   or "UNKNOWN"
    triage      = str(c.get("triage_state")                  or "MONITOR").strip().upper()   or "MONITOR"

    def _check(name: str, passed: bool, note: str) -> dict:
        return {"name": name, "passed": bool(passed), "note": note}

    checklist = [
        _check(
            "proof_stack_backed",
            proof_auth in ("BACKED", "FORCEFUL"),
            f"Proof stack authority is {proof_auth}.",
        ),
        _check(
            "memory_authority_constructive",
            mem_auth in ("BACKED", "FORCEFUL"),
            f"Continuation memory authority is {mem_auth}.",
        ),
        _check(
            "promotion_earned",
            promo_auth in ("STACKED", "SUPPORTED"),
            f"Promotion authority is {promo_auth}.",
        ),
        _check(
            "deployment_open",
            deploy_auth in ("EXECUTABLE", "CONDITIONALLY_READY"),
            f"Deployment authority is {deploy_auth}.",
        ),
    ]
    passed_checks = sum(1 for ck in checklist if ck["passed"])
    total_checks  = len(checklist)

    # Candidate operating state (priority order)
    if triage == "DO_NOT_TOUCH" or dec_auth == "BLOCKED" or deploy_auth == "BLOCKED":
        candidate_operating_state = "BLOCKED"
        candidate_operating_note  = (
            f"Candidate is blocked — triage={triage}, decision={dec_auth}, deployment={deploy_auth}."
        )
    elif dec_auth == "STACKED" and deploy_auth == "EXECUTABLE":
        candidate_operating_state = "STACKED"
        candidate_operating_note  = (
            "All authority layers are stacked. Candidate is ready for disciplined execution."
        )
    elif dec_auth in ("STACKED", "SUPPORTED") and deploy_auth in ("EXECUTABLE", "CONDITIONALLY_READY"):
        candidate_operating_state = "SUPPORTED"
        candidate_operating_note  = (
            f"Candidate has stacked decision authority ({dec_auth}) with deployment={deploy_auth}."
        )
    elif ready != "READY" or route == "LOCKED":
        candidate_operating_state = "PLANNING_ONLY"
        candidate_operating_note  = (
            f"Candidate is structurally tracked but deployment is not open. "
            f"ready={ready}, route={route}."
        )
    elif dec_auth == "TENTATIVE":
        candidate_operating_state = "TENTATIVE"
        candidate_operating_note  = (
            "Candidate authority is tentative. Not enough continuation or proof weight yet."
        )
    else:
        candidate_operating_state = "PLANNING_ONLY"
        candidate_operating_note  = (
            f"Candidate is in planning mode. decision={dec_auth}, deployment={deploy_auth}."
        )

    # Next unlock
    if candidate_operating_state == "BLOCKED":
        candidate_next_unlock_state = "UNBLOCK_REQUIRED"
        candidate_next_unlock_note  = (
            "Resolve triage or decision block before evaluating further."
        )
    elif candidate_operating_state == "STACKED":
        candidate_next_unlock_state = "MAINTAIN"
        candidate_next_unlock_note  = "Candidate authority is stacked. Maintain discipline."
    elif candidate_operating_state == "SUPPORTED" and deploy_auth == "CONDITIONALLY_READY":
        candidate_next_unlock_state = "ROUTE_SCALE"
        candidate_next_unlock_note  = (
            "Deployment is conditionally ready. A scale-ready route completes the unlock."
        )
    elif promo_auth == "TENTATIVE" and proof_auth not in ("BACKED", "FORCEFUL"):
        candidate_next_unlock_state = "PROOF_STACK_BACKED"
        candidate_next_unlock_note  = (
            "Candidate needs stronger proof-stack authority before promotion can advance."
        )
    elif promo_auth == "TENTATIVE" and proof_auth in ("BACKED", "FORCEFUL"):
        candidate_next_unlock_state = "PROMOTION_SUPPORTED"
        candidate_next_unlock_note  = (
            "Proof is backed. Continuation memory or reinforcement upgrade needed for promotion."
        )
    elif promo_auth in ("STACKED", "SUPPORTED") and deploy_auth == "PLANNING_ONLY":
        candidate_next_unlock_state = "DEPLOYMENT_OPEN"
        candidate_next_unlock_note  = (
            "Promotion authority is earned. Capital route or ready-state unlock needed."
        )
    elif route == "LOCKED":
        candidate_next_unlock_state = "ROUTE_OPEN"
        candidate_next_unlock_note  = (
            "Capital route must open before this candidate can be deployed."
        )
    else:
        candidate_next_unlock_state = "MAINTAIN"
        candidate_next_unlock_note  = "No immediate unlock pressure for this candidate."

    # Primary constraint (priority order)
    if triage == "DO_NOT_TOUCH":
        candidate_primary_constraint_key  = "TRIAGE_BLOCKED"
        candidate_primary_constraint_note = "Candidate triage state is DO_NOT_TOUCH."
    elif dec_auth == "BLOCKED":
        candidate_primary_constraint_key  = "DECISION_BLOCKED"
        candidate_primary_constraint_note = (
            "Decision authority is blocked — adverse proof or promotion block."
        )
    elif deploy_auth == "BLOCKED":
        candidate_primary_constraint_key  = "DEPLOYMENT_BLOCKED"
        candidate_primary_constraint_note = (
            f"Deployment is blocked — route={route}, headroom={headroom}."
        )
    elif proof_auth == "TENTATIVE":
        candidate_primary_constraint_key  = "PROOF_TENTATIVE"
        candidate_primary_constraint_note = (
            "Proof stack authority is tentative. System-level evidence is still thin."
        )
    elif mem_auth == "TENTATIVE":
        candidate_primary_constraint_key  = "MEMORY_TENTATIVE"
        candidate_primary_constraint_note = (
            "Continuation memory authority is tentative. Need more continuation evidence."
        )
    elif promo_auth == "TENTATIVE":
        candidate_primary_constraint_key  = "PROMOTION_TENTATIVE"
        candidate_primary_constraint_note = (
            "Promotion authority is tentative. Need stronger proof + memory stacking."
        )
    elif route == "LOCKED":
        candidate_primary_constraint_key  = "ROUTE_LOCKED"
        candidate_primary_constraint_note = (
            "Capital route is locked. Deployment not possible under current policy."
        )
    elif deploy_auth == "PLANNING_ONLY":
        candidate_primary_constraint_key  = "DEPLOYMENT_PLANNING_ONLY"
        candidate_primary_constraint_note = (
            "Deployment is planning-only. Route or ready-state upgrade needed."
        )
    else:
        candidate_primary_constraint_key  = "NONE"
        candidate_primary_constraint_note = "No significant constraint for this candidate."

    # Alignment: proof, memory, and capital pointing the same direction?
    if (
        proof_auth in ("BACKED", "FORCEFUL")
        and mem_auth in ("BACKED", "FORCEFUL")
        and route not in ("LOCKED",)
    ):
        candidate_alignment = "ALIGNED"
    elif triage == "DO_NOT_TOUCH" or dec_auth == "BLOCKED" or proof_auth == "ADVERSE":
        candidate_alignment = "MISALIGNED"
    else:
        candidate_alignment = "PARTIAL"

    # Lane momentum — directional signal for cross-lane consumers
    _cand_check_ratio = passed_checks / max(total_checks, 1)
    if candidate_operating_state == "BLOCKED":
        lane_momentum = "BLOCKED"
    elif candidate_operating_state in ("STACKED", "SUPPORTED"):
        lane_momentum = "ADVANCING"
    elif passed_checks == 0 and candidate_alignment == "MISALIGNED":
        lane_momentum = "STALLING"
    elif _cand_check_ratio <= 0.0:
        lane_momentum = "STALLING"
    else:
        lane_momentum = "BUILDING"

    # Unlock horizon — how many checks remain
    _cand_outstanding = total_checks - passed_checks
    if candidate_next_unlock_state == "MAINTAIN" or _cand_outstanding <= 0:
        unlock_horizon = "NONE"
    elif _cand_outstanding == 1:
        unlock_horizon = "NEAR"
    elif _cand_outstanding <= 3:
        unlock_horizon = "MEDIUM"
    else:
        unlock_horizon = "FAR"

    return {
        "candidate_operating_state":         candidate_operating_state,
        "candidate_operating_note":          candidate_operating_note,
        "candidate_next_unlock_state":       candidate_next_unlock_state,
        "candidate_next_unlock_note":        candidate_next_unlock_note,
        "candidate_primary_constraint_key":  candidate_primary_constraint_key,
        "candidate_primary_constraint_note": candidate_primary_constraint_note,
        "candidate_alignment":               candidate_alignment,
        "candidate_passed_checks":           passed_checks,
        "candidate_total_checks":            total_checks,
        "checklist":                         checklist,
        "lane_momentum":                     lane_momentum,
        "unlock_horizon":                    unlock_horizon,
    }


def _continuation_recency_weight(ts_value: str | None) -> float:
    """Light recency weighting for continuation memory evidence."""
    if not ts_value:
        return 0.8
    try:
        from datetime import datetime, timezone

        raw = str(ts_value).strip()
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        age_hours = max(
            0.0,
            (datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds() / 3600.0,
        )
    except Exception:
        return 0.8

    if age_hours <= 72:
        return 1.15
    if age_hours <= 24 * 14:
        return 1.0
    if age_hours <= 24 * 45:
        return 0.9
    return 0.8


def _support_signal_recency_multiplier(
    ts_value: str | None,
    *,
    fresh_hours: float,
    active_hours: float,
    fade_hours: float,
) -> float:
    """Time-decay support-lane reinforcement so stale overlap doesn't overclaim."""
    if not ts_value:
        return 0.45
    try:
        from datetime import datetime, timezone

        raw = str(ts_value).strip()
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        age_hours = max(
            0.0,
            (datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds() / 3600.0,
        )
    except Exception:
        return 0.45

    if age_hours <= fresh_hours:
        return 1.0
    if age_hours <= active_hours:
        return 0.8
    if age_hours <= fade_hours:
        return 0.55
    return 0.25


def _continuation_archetype(
    entry_window: str | None,
    fuel_quality: str | None,
    move_phase: str | None,
) -> str:
    """Compact continuation family used for pattern memory and ranking."""
    ew = str(entry_window or "").upper()
    fq = str(fuel_quality or "").upper()
    mp = str(move_phase or "").upper()

    if mp == "REVIVAL":
        return "REVIVAL_CONTINUATION"
    if mp == "RELOAD" and ew == "OPEN" and fq in ("STRONG", "MODERATE"):
        return "DORMANT_SECOND_LEG"
    if mp == "RELOAD":
        return "RELOAD_CONTINUATION"
    if ew == "CLOSING" and mp in ("EARLY", "MID", "RELOAD"):
        return "DEEP_PULLBACK"
    if ew == "OPEN" and fq in ("STRONG", "MODERATE") and mp in ("EARLY", "MID"):
        return "STRUCTURAL_PASS"
    return "PHASE_CONTINUATION"


def _support_reinforcement_bucket(
    support_overlap_score: float | int | None,
    support_overlap_tags: list[str] | tuple[str, ...] | None,
) -> str:
    """Compact reinforcement family for pattern memory and calibration."""
    score = float(support_overlap_score or 0.0)
    tags = {str(t or "").strip() for t in (support_overlap_tags or []) if str(t or "").strip()}
    if score >= 8.0 or len(tags) >= 2:
        return "STRONG_REINFORCED"
    if score >= 3.0 or bool(tags):
        return "REINFORCED"
    return "PLAIN"


def _capital_deployment_family(
    capital_posture: str | None,
    capital_ready_state: str | None,
    capital_pressure_bucket: str | None,
    capital_regime_bucket: str | None,
) -> str:
    posture = str(capital_posture or "UNKNOWN").strip().upper() or "UNKNOWN"
    ready = str(capital_ready_state or "UNKNOWN").strip().upper() or "UNKNOWN"
    pressure = str(capital_pressure_bucket or "UNKNOWN").strip().upper() or "UNKNOWN"
    regime = str(capital_regime_bucket or "UNKNOWN").strip().upper() or "UNKNOWN"
    return f"{posture}|{ready}|{pressure}|{regime}"


def _capital_intensity_bucket(amount_usd: float | int | None) -> str:
    val = float(amount_usd or 0.0)
    if val <= 0:
        return "ZERO"
    if val < 6:
        return "MICRO"
    if val < 12:
        return "SMALL"
    if val < 20:
        return "MEDIUM"
    return "LARGE"


def _capital_window_bucket(
    mode: str | None,
    auto_buy: bool | None,
    remaining_slots: int | None,
    effective_per_trade_usd: float | int | None,
    effective_remaining_cap_usd: float | int | None,
    effective_cap_multiplier: float | int | None,
    capital_ready_state: str | None = None,
    suggested_entry_usd: float | int | None = None,
) -> str:
    _mode = str(mode or "PAPER").strip().upper() or "PAPER"
    _ready = str(capital_ready_state or "").strip().upper()
    _slots = int(remaining_slots or 0)
    _per_trade = float(effective_per_trade_usd or 0.0)
    _remaining = float(effective_remaining_cap_usd or 0.0)
    _mult = float(effective_cap_multiplier or 0.0)
    _suggested = float(suggested_entry_usd or 0.0)

    if (
        _mode == "PAPER"
        or not bool(auto_buy)
        or _slots <= 0
        or _remaining <= 0
        or (_ready and _ready != "READY")
    ):
        return "CLOSED"
    if _suggested > 0 and _suggested <= 5.0:
        return "MICRO_WINDOW"
    if _remaining <= max(_per_trade, 5.0) or _per_trade <= 5.0:
        return "MICRO_WINDOW"
    if _mult < 0.5 or _slots == 1 or _remaining <= 15.0:
        return "LIMITED_WINDOW"
    return "OPEN_WINDOW"


def _capital_headroom_bucket(
    effective_remaining_cap_usd: float | int | None,
    effective_per_trade_usd: float | int | None,
    remaining_slots: int | None,
) -> str:
    _remaining = float(effective_remaining_cap_usd or 0.0)
    _per_trade = float(effective_per_trade_usd or 0.0)
    _slots = int(remaining_slots or 0)
    if _remaining <= 0.0 or _slots <= 0:
        return "EXHAUSTED"
    if _remaining <= max(_per_trade, 5.0):
        return "THIN"
    if _remaining <= max(_per_trade * 2.0, 15.0) or _slots == 1:
        return "WORKABLE"
    return "AMPLE"


def _capital_route_bucket(
    mode: str | None,
    auto_buy: bool | None,
    capital_posture: str | None,
    capital_ready_state: str | None,
    capital_window_bucket: str | None,
    capital_headroom_bucket: str | None,
    capital_regime_bucket: str | None,
) -> str:
    _mode = str(mode or "PAPER").strip().upper() or "PAPER"
    _posture = str(capital_posture or "UNKNOWN").strip().upper() or "UNKNOWN"
    _ready = str(capital_ready_state or "UNKNOWN").strip().upper() or "UNKNOWN"
    _window = str(capital_window_bucket or "CLOSED").strip().upper() or "CLOSED"
    _headroom = str(capital_headroom_bucket or "UNKNOWN").strip().upper() or "UNKNOWN"
    _regime = str(capital_regime_bucket or "UNKNOWN").strip().upper() or "UNKNOWN"
    if _mode == "PAPER" or not bool(auto_buy) or _ready != "READY" or _window == "CLOSED":
        return "LOCKED"
    if _headroom == "EXHAUSTED":
        return "LOCKED"
    if _window == "MICRO_WINDOW" or _headroom == "THIN":
        return "MICRO_PROBE_ONLY"
    if _posture == "SCALE_CANDIDATE" and _window == "OPEN_WINDOW" and _headroom == "AMPLE" and _regime != "RISK_OFF":
        return "SCALE_READY"
    return "DISCIPLINED_PROBE"


def _capital_allocator_stance(
    mode: str | None,
    auto_buy: bool | None,
    remaining_slots: int | None,
    effective_remaining_cap_usd: float | int | None,
    capital_pressure_bucket: str | None,
    capital_regime_bucket: str | None,
    capital_mix_bucket: str | None,
) -> tuple[str, str]:
    _mode = str(mode or "PAPER").strip().upper() or "PAPER"
    _pressure = str(capital_pressure_bucket or "UNKNOWN").strip().upper() or "UNKNOWN"
    _regime = str(capital_regime_bucket or "UNKNOWN").strip().upper() or "UNKNOWN"
    _mix = str(capital_mix_bucket or "UNKNOWN").strip().upper() or "UNKNOWN"
    _slots = int(remaining_slots or 0)
    _remaining_cap = float(effective_remaining_cap_usd or 0.0)
    if _mode == "PAPER":
        return "PAPER_ONLY", "Runtime mode is PAPER. Memecoin capital stays planning-only."
    if not bool(auto_buy):
        return "DISABLED", "Auto-buy is disabled. Memecoin deployment stays manual/planning-only."
    if _slots <= 0 or _remaining_cap <= 0:
        return "SATURATED", "Adaptive memecoin budget or slot capacity is fully used."
    if _pressure == "HIGH" or _regime == "RISK_OFF":
        return "TIGHT", "Memecoin allocation is heavily throttled by portfolio pressure or regime."
    if _pressure == "MEDIUM" or _regime == "MIXED" or _mix in ("BALANCED", "PERP_HEAVY"):
        return "DISCIPLINED", "Memecoin allocation is open, but sizing stays disciplined."
    return "OPEN", "Memecoin allocator conditions are supportive."


def _capital_marginal_route(capital_route_bucket: str | None) -> str:
    route = str(capital_route_bucket or "LOCKED").strip().upper() or "LOCKED"
    if route == "SCALE_READY":
        return "MEMECOIN_SCALE"
    if route in ("DISCIPLINED_PROBE", "MICRO_PROBE_ONLY"):
        return "MEMECOIN_PROBE"
    return "HOLD_CASH"


def _capital_routing_family(
    capital_allocator_stance: str | None,
    capital_route_bucket: str | None,
    marginal_route: str | None,
) -> str:
    stance = str(capital_allocator_stance or "UNKNOWN").strip().upper() or "UNKNOWN"
    route = str(capital_route_bucket or "UNKNOWN").strip().upper() or "UNKNOWN"
    marginal = str(marginal_route or "UNKNOWN").strip().upper() or "UNKNOWN"
    return f"{stance}|{route}|{marginal}"


def _ensure_memecoin_outcome_label_schema(conn) -> None:
    """Self-heal label columns expected by trust/triage writes and validators."""
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(memecoin_signal_outcomes)").fetchall()}
    except Exception:
        return
    for col, defn in (
        ("trust_label", "TEXT"),
        ("triage_state", "TEXT"),
        ("labeled_at", "TEXT"),
        ("scanner_regime", "TEXT DEFAULT 'NORMAL'"),
        ("scanner_relaxation_reason", "TEXT"),
    ):
        if col not in cols:
            try:
                conn.execute(f"ALTER TABLE memecoin_signal_outcomes ADD COLUMN {col} {defn}")
            except Exception:
                pass


@router.get("/status")
async def memecoins_status_ep(_: str = Depends(get_current_user)):
    """Scanner signals + open positions + stats."""
    _ensure_engine_path()
    try:
        import json as _json
        from utils.memecoin_manager import memecoin_status as _ms  # type: ignore
        status = await asyncio.to_thread(_ms)
        # Append bundle-filter stats, holder-quality stats, and context stats
        try:
            db = _db_path()
            conn = sqlite3.connect(db)
            row_b = conn.execute(
                "SELECT value FROM kv_store WHERE key='memecoin_bundle_stats'"
            ).fetchone()
            row_h = conn.execute(
                "SELECT value FROM kv_store WHERE key='memecoin_hq_stats'"
            ).fetchone()
            # Patch 222 — Entry Context Engine summary
            row_c = conn.execute(
                "SELECT value FROM kv_store WHERE key='memecoin_context_stats'"
            ).fetchone()
            conn.close()
            status["bundle_stats"]    = _json.loads(row_b[0]) if row_b else None
            status["hq_summary"]      = _json.loads(row_h[0]) if row_h else None
            status["context_summary"] = _json.loads(row_c[0]) if row_c else None
        except Exception:
            status["bundle_stats"]    = None
            status["hq_summary"]      = None
            status["context_summary"] = None
        return status
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/stats")
async def memecoins_stats_ep(_: str = Depends(get_current_user)):
    """Backward-compatible alias for older callers expecting memecoin status at /stats."""
    return await memecoins_status_ep(_)


@router.post("/buy")
async def memecoins_buy_ep(body: dict, _: str = Depends(get_current_user)):
    """Buy a memecoin by mint address."""
    _ensure_engine_path()
    mint       = str(body.get("mint",       "")).strip()
    symbol     = str(body.get("symbol",     "")).strip().upper()
    amount_usd = float(body.get("amount_usd", 10))
    if not mint or not symbol:
        raise HTTPException(status_code=400, detail="mint and symbol required")
    try:
        from utils.memecoin_manager import buy_memecoin as _bm  # type: ignore
        return await asyncio.to_thread(_bm, mint, symbol, amount_usd)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/sell/{mint}")
async def memecoins_sell_ep(mint: str, body: dict | None = None, _: str = Depends(get_current_user)):
    """Sell an open memecoin position by mint address."""
    _ensure_engine_path()
    try:
        from utils.memecoin_manager import sell_memecoin as _sm  # type: ignore
        pct = float((body or {}).get("pct", 100))
        reason = str((body or {}).get("reason", "MANUAL")).strip() or "MANUAL"
        return await asyncio.to_thread(_sm, mint, reason, pct)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/pilot-status")
async def memecoins_pilot_status_ep(_: str = Depends(get_current_user)):
    _memecoins_v2_retired("/api/memecoins/pilot-status")

    """
    Pilot mode status: current mode, activation blockers, exposure, PnL, readiness gates.

    Returns the same structure regardless of current mode so the frontend can always
    render an accurate picture. Safe to poll at high frequency — read-only.

    mode:
      PAPER  — DRY_RUN=true (default). No real trades.
      PILOT  — DRY_RUN=false + PILOT_MODE=true. Live with hard guardrails.
      LIVE   — DRY_RUN=false + PILOT_MODE=false. Unrestricted (explicit opt-in).
    """
    _ensure_engine_path()

    def _run() -> dict:
        from utils.memecoin_manager import get_memecoin_mode, get_pilot_stats  # type: ignore

        mode       = get_memecoin_mode()
        dry_run    = os.getenv("MEMECOIN_DRY_RUN",           "true").lower()  == "true"
        pilot_mode = os.getenv("MEMECOIN_PILOT_MODE",        "false").lower() == "true"
        auto_buy   = os.getenv("MEMECOIN_AUTO_BUY",          "false").lower() == "true"

        # Pilot guardrail config (shown regardless of mode so operator knows thresholds)
        pilot_max_open  = int(  os.getenv("MEMECOIN_PILOT_MAX_OPEN",       "2"))
        pilot_max_usd   = float(os.getenv("MEMECOIN_PILOT_MAX_USD",         "10"))
        pilot_total_cap = float(os.getenv("MEMECOIN_PILOT_TOTAL_CAP_USD",   "50"))

        stats = get_pilot_stats()

        # ── Readiness gates ────────────────────────────────────────────────────
        fg_value = None
        fg_ok    = False
        try:
            from utils.agent_coordinator import get_fear_greed  # type: ignore
            fg_data  = get_fear_greed()
            fg_value = fg_data.get("value")
            fg_ok    = fg_value is not None and fg_value > 35
        except Exception:
            pass

        # WR gate source: memecoin_signal_outcomes (Patch 217).
        # Switched from alert_outcomes — that table reflected pre-crash SOS data
        # (95% WR) while the scanner's actual last-20 completions were 0% WR.
        # MSO is the live scanner pipeline; its last-20 is the correct readiness signal.
        # COOLDOWN_DATE matches brain_memecoin_pilot_readiness hard gate.
        COOLDOWN_DATE    = "2026-03-07T00:00:00"
        wr_24h           = None
        wr_ok            = False
        n_sample         = 0
        post_cooldown_n  = 0
        post_cooldown_ok = False
        try:
            from utils.db import get_conn as _gc  # type: ignore
            with _gc() as conn:
                # Last-20 scan WR — post-cooldown cohort only (Patch 235+)
                # scanned_at filter prevents pre-cooldown re-evaluations from
                # contaminating the gate if old rows ever get reprocessed.
                rows = conn.execute("""
                    SELECT return_24h_pct
                    FROM   memecoin_signal_outcomes
                    WHERE  status = 'COMPLETE' AND return_24h_pct IS NOT NULL
                      AND  scanned_at >= ?
                    ORDER  BY evaluated_24h_ts_utc DESC
                    LIMIT  20
                """, (COOLDOWN_DATE,)).fetchall()
                if rows:
                    n_sample = len(rows)
                    wins     = sum(1 for r in rows if float(r["return_24h_pct"]) > 0)
                    wr_24h   = round(wins / n_sample * 100, 1)
                    wr_ok    = wr_24h >= 55.0

                # Post-cooldown sample gate — Patch 220: switched from alert_outcomes
                # to memecoin_signal_outcomes (same fix as Patch 217 WR gate).
                # alert_outcomes is a legacy watchlist pipeline (73 rows, score=0)
                # and was producing n=2 post-cooldown against the scanner's real 600+.
                pc_row = conn.execute("""
                    SELECT COUNT(*) AS n
                    FROM   memecoin_signal_outcomes
                    WHERE  scanned_at >= ?
                      AND  status = 'COMPLETE'
                      AND  return_24h_pct IS NOT NULL
                """, (COOLDOWN_DATE,)).fetchone()
                post_cooldown_n  = int(pc_row["n"] or 0) if pc_row else 0
                post_cooldown_ok = post_cooldown_n >= 20
        except Exception:
            pass

        # ── Patch 225: repeat-token guard suppression count ───────────────────
        repeat_guard_suppressed = None
        try:
            from utils.db import get_conn as _gc  # type: ignore
            import json as _json225
            with _gc() as conn:
                row = conn.execute(
                    "SELECT value FROM kv_store WHERE key='memecoin_repeat_guard_24h'"
                ).fetchone()
                if row and row["value"]:
                    _rg = _json225.loads(row["value"])
                    repeat_guard_suppressed = _rg.get("suppressed")
        except Exception:
            pass

        # ── Activation blockers ────────────────────────────────────────────────
        # Listed in priority order: env config → hard gates → advisory gates.
        # [HARD] = must pass before pilot can start; advisory = warning only.
        blockers = []
        if dry_run:
            blockers.append("MEMECOIN_DRY_RUN=true — must be false for live execution")
        if not pilot_mode:
            blockers.append("MEMECOIN_PILOT_MODE=false — must be true to enter pilot mode")
        if not auto_buy:
            blockers.append("MEMECOIN_AUTO_BUY=false — must be true for auto-execution")
        if not fg_ok:
            fg_str = str(fg_value) if fg_value is not None else "unknown"
            blockers.append(f"F&G={fg_str} — pilot gate requires F&G > 35 [HARD]")
        if not post_cooldown_ok:
            blockers.append(
                f"post-cooldown sample: {post_cooldown_n}/20 evaluated trades "
                f"after {COOLDOWN_DATE} — need 20 clean trades [HARD]"
            )
        if not wr_ok:
            wr_str = f"{wr_24h}%" if wr_24h is not None else "unknown"
            blockers.append(f"last-20 WR={wr_str} — pilot gate requires ≥55% (advisory)")

        remaining_cap = max(0.0, pilot_total_cap - stats["total_exposure_usd"])

        return {
            "mode":          mode,
            "pilot_active":  mode == "PILOT" and auto_buy,
            "auto_buy":      auto_buy,
            "blockers":      blockers,
            "config": {
                "max_open":          pilot_max_open,
                "max_usd_per_trade": pilot_max_usd,
                "total_cap_usd":     pilot_total_cap,
            },
            "exposure": {
                "open_count":         stats["open_count"],
                "total_exposure_usd": stats["total_exposure_usd"],
                "remaining_cap_usd":  round(remaining_cap, 2),
                "open_trades":        stats["open_trades"],
            },
            "pnl": {
                "closed_count":     stats["closed_count"],
                "realized_pnl_usd": stats["realized_pnl_usd"],
                "recent_closed":    stats["recent_closed"],
            },
            "readiness": {
                "fg_ok":                     fg_ok,
                "fg_value":                  fg_value,
                "wr_ok":                     wr_ok,
                "wr_24h":                    wr_24h,
                "sample_size":               n_sample,
                "post_cooldown_n":           post_cooldown_n,
                "post_cooldown_ok":          post_cooldown_ok,
                "cooldown_date":             COOLDOWN_DATE,
                "repeat_guard_suppressed":   repeat_guard_suppressed,  # Patch 225
            },
            "generated_at": __import__("datetime").datetime.now(
                __import__("datetime").timezone.utc
            ).isoformat(),
        }

    try:
        return await asyncio.to_thread(_run)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/trending")
async def memecoins_trending_ep(_: str = Depends(get_current_user)):
    """
    Narrative momentum trending data — CoinGecko + DexScreener Solana boosted.
    Returns cached payload refreshed every 4h by the research loop. (Patch 127)
    """
    _ensure_engine_path()
    try:
        from utils.narrative_momentum import get_narrative_data  # type: ignore
        data = get_narrative_data()
        return data or {"coingecko": [], "dexscreener": [], "updated_at": None}
    except Exception as exc:
        return {"coingecko": [], "dexscreener": [], "updated_at": None, "error": str(exc)}


@router.get("/analytics")
async def memecoins_analytics_ep(_: str = Depends(get_current_user)):
    """
    Signal outcome analytics — score buckets, rug label breakdown, win rates,
    avg returns, and auto-buy config with tuner progress. (Patches 116+117+125)
    """
    _db = _db_path()

    COOLDOWN = "2026-03-07T00:00:00"

    def _run():
        c = sqlite3.connect(str(_db))
        c.row_factory = sqlite3.Row

        # Post-cooldown cohort only — same era as trust/readiness gates
        total    = c.execute(
            "SELECT COUNT(*) FROM memecoin_signal_outcomes WHERE scanned_at >= ?",
            (COOLDOWN,)
        ).fetchone()[0]
        complete = c.execute(
            "SELECT COUNT(*) FROM memecoin_signal_outcomes WHERE status='COMPLETE' AND scanned_at >= ?",
            (COOLDOWN,)
        ).fetchone()[0]
        bought   = c.execute(
            "SELECT COUNT(*) FROM memecoin_signal_outcomes WHERE bought=1 AND scanned_at >= ?",
            (COOLDOWN,)
        ).fetchone()[0]

        # Score buckets
        buckets = []
        for label, lo, hi in [("70+", 70, 101), ("50\u201369", 50, 70), ("<50", 0, 50)]:
            rows = c.execute("""
                SELECT return_1h_pct, return_4h_pct, return_24h_pct, bought
                FROM memecoin_signal_outcomes
                WHERE score >= ? AND score < ? AND status = 'COMPLETE'
            """, (lo, hi)).fetchall()

            if not rows:
                buckets.append({
                    "label": label, "count": 0, "win_rate_4h": None,
                    "avg_return_1h": None, "avg_return_4h": None,
                    "avg_return_24h": None, "buy_rate": None,
                })
                continue

            r1   = [float(r["return_1h_pct"])  for r in rows if r["return_1h_pct"]  is not None]
            r4   = [float(r["return_4h_pct"])  for r in rows if r["return_4h_pct"]  is not None]
            r24  = [float(r["return_24h_pct"]) for r in rows if r["return_24h_pct"] is not None]
            win4 = sum(1 for x in r4 if x > 0)
            buckets.append({
                "label":          label,
                "count":          len(rows),
                "win_rate_4h":    round(win4 / len(r4) * 100, 1) if r4 else None,
                "avg_return_1h":  round(sum(r1)  / len(r1),  2)  if r1  else None,
                "avg_return_4h":  round(sum(r4)  / len(r4),  2)  if r4  else None,
                "avg_return_24h": round(sum(r24) / len(r24), 2)  if r24 else None,
                "buy_rate":       round(sum(1 for r in rows if r["bought"]) / len(rows) * 100, 1),
            })

        # Rug label breakdown (Patch 117)
        rug_breakdown = []
        for rug_label in ("GOOD", "WARN", "UNKNOWN"):
            rows = c.execute("""
                SELECT return_4h_pct, return_24h_pct, bought
                FROM memecoin_signal_outcomes
                WHERE rug_label = ? AND status = 'COMPLETE'
            """, (rug_label,)).fetchall()
            if rows:
                r4  = [float(r["return_4h_pct"]) for r in rows if r["return_4h_pct"] is not None]
                win = sum(1 for x in r4 if x > 0)
                rug_breakdown.append({
                    "label":         rug_label,
                    "count":         len(rows),
                    "win_rate_4h":   round(win / len(r4) * 100, 1) if r4 else None,
                    "avg_return_4h": round(sum(r4) / len(r4), 2)   if r4 else None,
                })

        # Best 10 by 4h return
        top = c.execute("""
            SELECT symbol, mint, score, rug_label, mcap_at_scan,
                   token_age_days, vol_acceleration, top_holder_pct,
                   return_1h_pct, return_4h_pct, return_24h_pct, bought, scanned_at
            FROM memecoin_signal_outcomes
            WHERE return_4h_pct IS NOT NULL
            ORDER BY return_4h_pct DESC
            LIMIT 10
        """).fetchall()

        # Learned thresholds
        lt_row = c.execute(
            "SELECT value FROM kv_store WHERE key='memecoin_learned_thresholds'"
        ).fetchone()
        learned = None
        if lt_row:
            try:
                import json as _j
                learned = _j.loads(lt_row["value"])
            except Exception:
                pass

        # Auto-buy config + tuner progress (Patch 125, updated Patch 138)
        # Milestone ladder: 20→50→200→500→1000, then perpetual +500 increments forever.
        # Progress bar always advances — learning never stops.
        if complete >= 1000:
            _tuner_needed = ((complete // 500) + 1) * 500  # perpetual: next 500 boundary
        elif complete >= 500:
            _tuner_needed = 1000
        elif complete >= 200:
            _tuner_needed = 500
        elif complete >= 50:
            _tuner_needed = 200
        elif complete >= 20:
            _tuner_needed = 50
        else:
            _tuner_needed = 20
        auto_buy = {
            "enabled":         os.getenv("MEMECOIN_AUTO_BUY",    "false").lower() == "true",
            "dry_run":         os.getenv("MEMECOIN_DRY_RUN",     "true").lower()  == "true",
            "score_min":       float(os.getenv("MEMECOIN_BUY_SCORE_MIN", "65")),
            "max_open":        int(os.getenv("MEMECOIN_MAX_OPEN", "3")),
            "buy_usd":         float(os.getenv("MEMECOIN_BUY_USD", "15")),
            "tuner_threshold": _tuner_needed,
            "complete_pct":    round(min(complete / _tuner_needed * 100, 100.0), 1),
        }

        # Patch 214 — Holder Quality breakdown by level (historical calibration)
        # Uses COALESCE: new rows have holder_quality_level; legacy rows derive
        # level from bundle_risk_level mapping so the chart isn't empty on day 1.
        hq_breakdown = []
        try:
            hq_rows = c.execute("""
                SELECT
                    COALESCE(holder_quality_level,
                        CASE bundle_risk_level
                            WHEN 'BLOCK'  THEN 'BLOCKED'
                            WHEN 'HIGH'   THEN 'RISKY'
                            WHEN 'MEDIUM' THEN 'CAUTION'
                            WHEN 'LOW'    THEN 'CAUTION'
                            ELSE 'CLEAN'
                        END
                    ) AS hq_level,
                    return_4h_pct,
                    return_24h_pct
                FROM memecoin_signal_outcomes
                WHERE status = 'COMPLETE'
            """).fetchall()
            from collections import defaultdict as _dd
            buckets_hq: dict = _dd(list)
            for r in hq_rows:
                lvl = r["hq_level"] or "CLEAN"
                buckets_hq[lvl].append(r)
            for lvl in ("CLEAN", "CAUTION", "RISKY", "BLOCKED"):
                lvl_rows = buckets_hq.get(lvl, [])
                r4  = [float(r["return_4h_pct"])  for r in lvl_rows if r["return_4h_pct"]  is not None]
                r24 = [float(r["return_24h_pct"]) for r in lvl_rows if r["return_24h_pct"] is not None]
                win4 = sum(1 for x in r4 if x > 0)
                hq_breakdown.append({
                    "level":         lvl,
                    "count":         len(lvl_rows),
                    "win_rate_4h":   round(win4 / len(r4) * 100, 1)    if r4  else None,
                    "avg_return_4h": round(sum(r4)  / len(r4),  2)     if r4  else None,
                    "avg_return_24h": round(sum(r24) / len(r24), 2)    if r24 else None,
                })
        except Exception:
            pass  # degrade gracefully if schema doesn't have columns yet

        c.close()
        return {
            "total_tracked":          total,
            "complete":               complete,
            "pending":                total - complete,
            "bought_count":           bought,
            "score_buckets":          buckets,
            "rug_breakdown":          rug_breakdown,
            "top_performers":         [dict(r) for r in top],
            "learned_thresholds":     learned,
            "auto_buy":               auto_buy,
            "holder_quality_breakdown": hq_breakdown,  # Patch 214
        }

    try:
        return await asyncio.to_thread(_run)
    except Exception as exc:
        log.warning("memecoins_analytics_ep error: %s", exc)
        return {
            "total_tracked": 0, "complete": 0, "pending": 0, "bought_count": 0,
            "score_buckets": [], "rug_breakdown": [], "top_performers": [],
            "learned_thresholds": None,
        }


# ── Patch 182 + 184: Score threshold decision support ─────────────────────────

@router.get("/score-analysis")
async def memecoins_score_analysis_ep(_: str = Depends(get_current_user)):
    """
    Compares outcome quality across score bands and threshold levels so the
    operator can decide whether MEMECOIN_BUY_SCORE_MIN needs adjustment.

    P182: bands, threshold_sim, bought_split, optimal_window, tuner, verdict
    P184: horizon_comparison — runs the P183 exhaustive window search on both
          4h and 24h outcomes and answers: is the tuner optimizing on the
          wrong horizon?

    Returns:
      bands              — 5-pt score bands: WR, avg_24h, n, avg_win, avg_loss
      threshold_sim      — cumulative stats at key thresholds (0,20,25,30,40,50,65)
      bought_split       — performance of actually-bought vs skipped signals
      optimal_window     — single best score range (WR × avg_24h product)
      tuner              — current auto-tuner recommendation from kv_store
                           (includes P183 score_bands + multi_band_mode)
      config_score_min   — current MEMECOIN_BUY_SCORE_MIN env value
      verdict            — operator verdict: MISALIGNED / SUBOPTIMAL / CALIBRATED
      horizon_comparison — P184: side-by-side 4h vs 24h band search results
    """
    import json as _json
    import os as _os

    config_min = int(float(_os.getenv("MEMECOIN_BUY_SCORE_MIN", "65")))

    def _run():
        # P184: Exhaustive window search helper — same algorithm as P183 tuner
        # but parameterised on ret_key so it works for both 4h and 24h returns.
        def _exhaustive_bands(rows_data, ret_key, min_n=10):
            """Try all (lo, hi) windows with widths 5/10/15, min n per window.
            Rank by expectancy = WR% × avg_return (positive edge only).
            Greedy non-overlapping pass returns top 3 independent bands."""
            all_wins = []
            for lo in range(0, 80, 5):
                for width in (5, 10, 15):
                    hi = lo + width
                    win = [
                        r for r in rows_data
                        if r["score"] is not None and lo <= r["score"] < hi
                        and r[ret_key] is not None
                    ]
                    if len(win) < min_n:
                        continue
                    rets = [float(r[ret_key]) for r in win]
                    wr   = sum(1 for x in rets if x > 0) / len(rets)
                    avg  = sum(rets) / len(rets)
                    exp  = (wr * 100) * avg if avg > 0 else -999.0
                    all_wins.append({
                        "lo": lo, "hi": hi, "n": len(win),
                        "wr": round(wr * 100, 1),
                        "avg_ret": round(avg, 2),
                        "expectancy": round(exp, 2),
                    })
            all_wins.sort(key=lambda w: w["expectancy"], reverse=True)
            top = []
            for w in all_wins:
                if not any(w["lo"] < b["hi"] and w["hi"] > b["lo"] for b in top):
                    top.append(w)
                if len(top) >= 3:
                    break
            return top

        COOLDOWN = "2026-03-07T00:00:00"
        from utils.db import get_conn  # type: ignore
        with get_conn() as conn:

            # ── 5-point score bands ───────────────────────────────────────────
            band_rows = conn.execute("""
                SELECT
                  (CAST(score AS INTEGER) / 5) * 5 AS lo,
                  COUNT(*) AS n,
                  ROUND(AVG(CASE WHEN return_24h_pct > 0 THEN 1.0 ELSE 0.0 END)*100, 1) AS wr,
                  ROUND(AVG(return_24h_pct), 2) AS avg_24h,
                  ROUND(AVG(CASE WHEN return_24h_pct > 0 THEN return_24h_pct END), 2) AS avg_win,
                  ROUND(AVG(CASE WHEN return_24h_pct <= 0 THEN return_24h_pct END), 2) AS avg_loss
                FROM memecoin_signal_outcomes
                WHERE score IS NOT NULL AND return_24h_pct IS NOT NULL
                  AND scanned_at >= ?
                GROUP BY lo ORDER BY lo
            """, (COOLDOWN,)).fetchall()
            bands = [dict(r) for r in band_rows]

            # ── Threshold simulation ──────────────────────────────────────────
            base_thresholds = [0, 20, 25, 30, 40, 50, 65]
            if config_min not in base_thresholds:
                base_thresholds.append(config_min)
            base_thresholds = sorted(set(base_thresholds), reverse=True)

            threshold_sim = []
            for t in base_thresholds:
                r = conn.execute("""
                    SELECT COUNT(*) AS n,
                           ROUND(AVG(CASE WHEN return_24h_pct > 0 THEN 1.0 ELSE 0.0 END)*100, 1) AS wr,
                           ROUND(AVG(return_24h_pct), 2) AS avg_ret,
                           ROUND(AVG(CASE WHEN return_24h_pct > 0 THEN return_24h_pct END), 2) AS avg_win,
                           ROUND(AVG(CASE WHEN return_24h_pct <= 0 THEN return_24h_pct END), 2) AS avg_loss
                    FROM memecoin_signal_outcomes
                    WHERE score >= ? AND return_24h_pct IS NOT NULL
                      AND scanned_at >= ?
                """, (t, COOLDOWN)).fetchone()
                n = int(r["n"] or 0)
                threshold_sim.append({
                    "threshold":  t,
                    "n":          n,
                    "wr":         float(r["wr"] or 0),
                    "avg_24h":    float(r["avg_ret"] or 0),
                    "avg_win":    float(r["avg_win"] or 0) if r["avg_win"] else None,
                    "avg_loss":   float(r["avg_loss"] or 0) if r["avg_loss"] else None,
                    "is_current": t == config_min,
                })

            # ── Bought vs not-bought split ────────────────────────────────────
            bought_split = {}
            for bought_val in (1, 0):
                r = conn.execute("""
                    SELECT COUNT(*) AS n,
                           ROUND(AVG(score), 1) AS avg_score,
                           ROUND(AVG(CASE WHEN return_24h_pct > 0 THEN 1.0 ELSE 0.0 END)*100, 1) AS wr,
                           ROUND(AVG(return_24h_pct), 2) AS avg_24h
                    FROM memecoin_signal_outcomes
                    WHERE bought=? AND return_24h_pct IS NOT NULL
                      AND scanned_at >= ?
                """, (bought_val, COOLDOWN)).fetchone()
                key = "bought" if bought_val == 1 else "not_bought"
                bought_split[key] = {
                    "n":         int(r["n"] or 0),
                    "avg_score": float(r["avg_score"] or 0),
                    "wr":        float(r["wr"] or 0),
                    "avg_24h":   float(r["avg_24h"] or 0),
                }

            # ── Optimal window: test candidate ranges ─────────────────────────
            windows = [
                (10, 49), (20, 24), (20, 49), (30, 49), (35, 49),
                (40, 44), (40, 49), (40, 59), (20, 59),
            ]
            best_window = None
            best_score_product = -9999.0
            window_results = []
            for lo, hi in windows:
                r = conn.execute("""
                    SELECT COUNT(*) AS n,
                           ROUND(AVG(CASE WHEN return_24h_pct > 0 THEN 1.0 ELSE 0.0 END)*100, 1) AS wr,
                           ROUND(AVG(return_24h_pct), 2) AS avg_24h
                    FROM memecoin_signal_outcomes
                    WHERE score BETWEEN ? AND ? AND return_24h_pct IS NOT NULL
                      AND scanned_at >= ?
                """, (lo, hi, COOLDOWN)).fetchone()
                n = int(r["n"] or 0)
                wr = float(r["wr"] or 0)
                avg = float(r["avg_24h"] or 0)
                product = (wr / 100) * avg if n >= 20 else -9999.0
                entry = {"lo": lo, "hi": hi, "n": n, "wr": wr, "avg_24h": avg}
                window_results.append(entry)
                if product > best_score_product:
                    best_score_product = product
                    best_window = entry

            # ── Tuner stored thresholds ───────────────────────────────────────
            tuner = None
            try:
                row = conn.execute(
                    "SELECT value FROM kv_store WHERE key='memecoin_learned_thresholds'"
                ).fetchone()
                if row:
                    t_data = _json.loads(row["value"])
                    tuner = {
                        "min_score":       t_data["thresholds"].get("min_score"),
                        "max_score":       t_data["thresholds"].get("max_score"),
                        "confidence":      t_data.get("confidence"),
                        "sample_size":     t_data.get("sample_size"),
                        "win_rate":        t_data.get("win_rate"),
                        "updated_at":      t_data.get("updated_at"),
                        "score_bands":     t_data.get("bands", []),                    # P183 multi-modal
                        "multi_band_mode": bool(t_data.get("multi_band_mode", False)), # P183
                    }
            except Exception:
                pass

            # ── P184: Horizon comparison — 4h vs 24h exhaustive band search ──
            # Fetches rows with BOTH returns filled. Runs _exhaustive_bands on
            # each horizon independently and compares top bands.
            # No rug filter — matches what _tune_thresholds_step() trains on.
            horizon_comparison = None
            try:
                dual_rows = conn.execute("""
                    SELECT score, return_4h_pct, return_24h_pct
                    FROM memecoin_signal_outcomes
                    WHERE return_4h_pct  IS NOT NULL
                      AND return_24h_pct IS NOT NULL
                      AND score          IS NOT NULL
                      AND scanned_at     >= ?
                """, (COOLDOWN,)).fetchall()
                n_both = len(dual_rows)

                if n_both < 50:
                    horizon_comparison = {
                        "n_both":             n_both,
                        "bands_4h":           [],
                        "bands_24h":          [],
                        "bands_missed_by_4h": [],
                        "verdict": {
                            "label":   "INSUFFICIENT_DATA",
                            "message": (
                                f"Only {n_both} rows with both 4h and 24h outcomes. "
                                "Need 50+ for reliable comparison."
                            ),
                        },
                    }
                else:
                    dual = [
                        {
                            "score":          float(r["score"] or 0),
                            "return_4h_pct":  float(r["return_4h_pct"] or 0),
                            "return_24h_pct": float(r["return_24h_pct"] or 0),
                        }
                        for r in dual_rows
                    ]
                    bands_4h  = _exhaustive_bands(dual, "return_4h_pct")
                    bands_24h = _exhaustive_bands(dual, "return_24h_pct")

                    # 24h viable bands not covered by any 4h top band
                    viable_24h   = [b for b in bands_24h if b["wr"] > 50.0 and b["expectancy"] > 0]
                    bands_missed = [
                        b for b in viable_24h
                        if not any(b["lo"] < b4["hi"] and b["hi"] > b4["lo"] for b4 in bands_4h)
                    ]

                    if not bands_4h or not bands_24h:
                        hv_label = "INSUFFICIENT_DATA"
                        hv_msg   = "Exhaustive search found no viable bands in one or both horizons."
                    elif bands_missed:
                        top4h  = bands_4h[0]
                        top24h = bands_24h[0]
                        hv_label = "SWITCH_RECOMMENDED"
                        hv_msg = (
                            f"24h top band {top24h['lo']}–{top24h['hi']} "
                            f"(WR={top24h['wr']:.0f}%, avg={top24h['avg_ret']:+.1f}%) "
                            f"is not covered by 4h tuner "
                            f"(4h top: {top4h['lo']}–{top4h['hi']}, "
                            f"WR={top4h['wr']:.0f}%). "
                            f"{len(bands_missed)} viable 24h band(s) missed by 4h gates. "
                            "Switching to 24h optimization would expose these signals."
                        )
                    else:
                        top4h     = bands_4h[0]
                        top24h    = bands_24h[0]
                        overlap   = (top4h["lo"] < top24h["hi"] and top24h["lo"] < top4h["hi"])
                        exp_delta = top24h["expectancy"] - top4h["expectancy"]
                        threshold = max(top4h["expectancy"] * 0.2, 5.0) if top4h["expectancy"] else 5.0
                        if overlap and abs(exp_delta) < threshold:
                            hv_label = "ALIGNED"
                            hv_msg = (
                                f"4h and 24h top bands agree: "
                                f"4h={top4h['lo']}–{top4h['hi']} (WR={top4h['wr']:.0f}%), "
                                f"24h={top24h['lo']}–{top24h['hi']} (WR={top24h['wr']:.0f}%). "
                                "No evidence the 4h horizon creates selection bias."
                            )
                        else:
                            hv_label = "SWITCH_RECOMMENDED"
                            overlap_note = (
                                "Bands do not overlap — materially different score regions."
                                if not overlap else
                                f"Bands overlap but 24h expectancy differs by {exp_delta:+.1f}."
                            )
                            hv_msg = (
                                f"4h top band {top4h['lo']}–{top4h['hi']} "
                                f"(WR={top4h['wr']:.0f}%, exp={top4h['expectancy']:.1f}) vs "
                                f"24h top band {top24h['lo']}–{top24h['hi']} "
                                f"(WR={top24h['wr']:.0f}%, exp={top24h['expectancy']:.1f}). "
                                + overlap_note
                            )

                    horizon_comparison = {
                        "n_both":             n_both,
                        "bands_4h":           bands_4h,
                        "bands_24h":          bands_24h,
                        "bands_missed_by_4h": bands_missed,
                        "verdict":            {"label": hv_label, "message": hv_msg},
                    }

            except Exception as _hc_exc:
                horizon_comparison = {
                    "n_both": 0, "bands_4h": [], "bands_24h": [], "bands_missed_by_4h": [],
                    "verdict": {"label": "INSUFFICIENT_DATA", "message": str(_hc_exc)},
                    "error":   str(_hc_exc),
                }

            # ── Verdict ───────────────────────────────────────────────────────
            # Compare config_min performance vs all-signals baseline
            config_sim  = next((x for x in threshold_sim if x["threshold"] == config_min), None)
            baseline    = next((x for x in threshold_sim if x["threshold"] == 0), None)
            config_wr   = config_sim["wr"]  if config_sim else 0.0
            baseline_wr = baseline["wr"]    if baseline  else 0.0

            if config_wr < baseline_wr - 10:
                verdict_label = "MISALIGNED"
                verdict_msg = (
                    f"ENV gate (score≥{config_min}) WR={config_wr:.0f}% is "
                    f"{baseline_wr - config_wr:.0f}pp BELOW the no-gate baseline "
                    f"({baseline_wr:.0f}%). Raising the threshold is actively "
                    f"selecting worse signals. Tuner recommends score "
                    f"{tuner['min_score']}-{tuner['max_score']} "
                    f"({tuner['confidence']} confidence, {tuner['sample_size']} samples)."
                    if tuner else
                    f"ENV gate (score≥{config_min}) performs worse than no gate. Lower the threshold."
                )
            elif config_wr < 30:
                verdict_label = "SUBOPTIMAL"
                verdict_msg = (
                    f"ENV gate (score≥{config_min}) WR={config_wr:.0f}% — "
                    "low but not inverting. Consider lowering toward 25-40 range."
                )
            else:
                verdict_label = "CALIBRATED"
                verdict_msg = f"ENV gate (score≥{config_min}) WR={config_wr:.0f}% — acceptable."

            return {
                "config_score_min":   config_min,
                "bands":              bands,
                "threshold_sim":      threshold_sim,
                "bought_split":       bought_split,
                "optimal_window":     best_window,
                "window_results":     window_results,
                "tuner":              tuner,
                "verdict":            {"label": verdict_label, "message": verdict_msg},
                "horizon_comparison": horizon_comparison,  # P184
            }

    try:
        return await asyncio.to_thread(_run)
    except Exception as exc:
        log.warning("memecoins_score_analysis_ep error: %s", exc)
        return {"error": str(exc)}


# ── Patch 189: Top buy candidates — gate replication ──────────────────────────

@router.get("/top-candidates")
async def memecoins_top_candidates_ep(_: str = Depends(get_current_user)):
    _memecoins_v2_retired("/api/memecoins/top-candidates")

    """
    Read-only replication of _auto_buy_step() gate logic — decision support.
    Evaluates each cached scanner signal against all gates without executing
    any buy. Returns BUY_NOW / WATCH / BLOCKED status with per-gate blocker
    reasons. Powers the Top Buys panel in HomePage. P189.
    """
    import json as _j

    def _run():
        # ── Cached scanner signals ────────────────────────────────────────────
        try:
            from utils.memecoin_scanner import get_cached_signals  # type: ignore
            signals = sorted(
                get_cached_signals(),
                key=lambda s: s.get("score", 0), reverse=True
            )
        except Exception:
            signals = []

        # Patch 313/314: eligible-universe pre-filter — restrict scan-cache
        # signals to the same working-set standard the NBA uses. This keeps
        # Top Candidates from spending attention on names the main memecoin lane
        # would already reject (WEAK coin quality / PROVEN_NEGATIVE history),
        # while still falling back safely on DB error.
        try:
            from utils.db import get_conn as _tc_gceu  # type: ignore
            from datetime import datetime as _tc_dt, timezone as _tc_tz, timedelta as _tc_td
            _tc_now    = _tc_dt.now(_tc_tz.utc)
            _tc_c30    = (_tc_now - _tc_td(days=30)).strftime("%Y-%m-%d %H:%M:%S")
            _tc_c14    = (_tc_now - _tc_td(days=14)).strftime("%Y-%m-%d %H:%M:%S")
            with _tc_gceu() as _tc_conn:
                _tc_conn.row_factory = sqlite3.Row
                _tc_eu_rows = _tc_conn.execute("""
                    SELECT symbol, COUNT(*) AS cnt, MAX(scanned_at) AS last_scan
                    FROM memecoin_signal_outcomes
                    WHERE scanned_at >= ?
                    GROUP BY symbol
                """, (_tc_c30,)).fetchall()
                _tc_perf_rows = _tc_conn.execute("""
                    SELECT symbol,
                           COUNT(*) AS n,
                           ROUND(AVG(return_24h_pct), 1) AS avg_return,
                           ROUND(SUM(CASE WHEN return_24h_pct >= 10 THEN 1.0 ELSE 0.0 END)
                                 * 100.0 / COUNT(*), 1) AS win_rate
                    FROM memecoin_signal_outcomes
                    WHERE status = 'COMPLETE' AND return_24h_pct IS NOT NULL
                    GROUP BY symbol
                """).fetchall()
            _tc_eu_syms: set = {
                r["symbol"]
                for r in _tc_eu_rows
                if int(r["cnt"]) >= 3 and (r["last_scan"] or "") >= _tc_c14
            }
            _tc_perf_tier: dict = {}
            for _pr in _tc_perf_rows:
                _pn   = int(_pr["n"] or 0)
                _pavg = float(_pr["avg_return"] or 0)
                _pwr  = float(_pr["win_rate"] or 0)
                if   _pn >= 15 and _pwr >= 50 and _pavg >= 5: _ptier = "PROVEN_POSITIVE"
                elif _pn >= 15 and _pavg < -10:               _ptier = "PROVEN_NEGATIVE"
                elif _pn >= 8:                                _ptier = "TESTED_NEUTRAL"
                else:                                         _ptier = "UNPROVEN"
                _tc_perf_tier[_pr["symbol"]] = _ptier

            def _tc_coin_quality(sig: dict) -> str:
                _rug   = str(sig.get("rug_label") or "").upper()
                _top1  = float(sig.get("top_holder_pct") or 0)
                _top5  = float(sig.get("top5_holder_pct") or 0)
                _liq   = float(sig.get("liquidity_usd") or 0)
                _vol24 = float(sig.get("volume_24h") or 0)
                _age   = float(sig.get("token_age_days") or 0)
                _hq    = str(sig.get("holder_quality_level") or "").upper()
                _vl    = (_vol24 / _liq) if _liq > 1000 else 0.0
                if (_rug == "DANGER" or _top1 >= 80 or _top5 >= 90
                        or (0 < _liq < 30_000) or _vl > 25):
                    return "WEAK"
                if (_rug in ("WARN", "UNKNOWN") or (50 <= _top1 < 80)
                        or (70 <= _top5 < 90) or (0 < _liq < 75_000)
                        or (0 < _age < 7) or (10 < _vl <= 25) or _hq == "RISKY"):
                    return "QUESTIONABLE"
                return "CLEAR"

            # Require the same live-cache gates NBA uses.
            _tc_eu_syms = {
                s.get("symbol")
                for s in signals
                if s.get("symbol") in _tc_eu_syms
                and (s.get("mcap_usd") is not None and float(s.get("mcap_usd") or 0) >= 1_500_000)
                and float(s.get("liquidity_usd") or 0) >= 50_000
                and _tc_coin_quality(s) != "WEAK"
                and _tc_perf_tier.get(s.get("symbol"), "UNPROVEN") != "PROVEN_NEGATIVE"
            }
            signals = [s for s in signals if s.get("symbol") in _tc_eu_syms]
        except Exception:
            pass  # fallback: no pre-filter; Patch 312 mcap veto still applies

        # ── Env config ────────────────────────────────────────────────────────
        auto_buy  = os.getenv("MEMECOIN_AUTO_BUY", "false").lower() == "true"
        dry_run   = os.getenv("MEMECOIN_DRY_RUN",  "true").lower()  == "true"
        max_open  = int(os.getenv("MEMECOIN_MAX_OPEN", "3"))
        env_score = float(os.getenv("MEMECOIN_BUY_SCORE_MIN", "65"))

        # ── Tuner thresholds (mirrors _auto_buy_step() loading logic) ─────────
        threshold       = env_score
        max_score       = 999
        vacc_min        = 5.0
        holder_max      = 35.0
        bands: list     = []
        multi_band_mode = False
        try:
            from utils.db import get_conn  # type: ignore
            with get_conn() as conn:
                row = conn.execute(
                    "SELECT value FROM kv_store WHERE key='memecoin_learned_thresholds'"
                ).fetchone()
            if row:
                lt = _j.loads(row[0])
                if lt.get("confidence") in ("medium", "high"):
                    t               = lt.get("thresholds", {})
                    threshold       = float(t.get("min_score",            threshold))
                    max_score       = float(t.get("max_score",            999))
                    vacc_min        = float(t.get("min_vol_acceleration", vacc_min))
                    holder_max      = float(t.get("max_top_holder_pct",   holder_max))
                    bands           = lt.get("bands", [])
                    multi_band_mode = bool(lt.get("multi_band_mode", False))
        except Exception:
            pass

        # ── Open positions ────────────────────────────────────────────────────
        open_mints: set = set()
        open_count = 0
        try:
            from utils.db import get_conn  # type: ignore
            with get_conn() as conn:
                rows = conn.execute(
                    "SELECT mint FROM memecoin_trades WHERE status='OPEN'"
                ).fetchall()
            open_mints = {r[0] for r in rows}
            open_count = len(open_mints)
        except Exception:
            pass

        # ── F&G ───────────────────────────────────────────────────────────────
        fg_value: int | None = None
        fg_favorable         = False
        try:
            from utils.agent_coordinator import get_fear_greed  # type: ignore
            fg           = get_fear_greed()
            fg_value     = fg.get("value")
            fg_favorable = bool(fg.get("favorable", False))
        except Exception:
            pass

        # ── System-level blockers (apply to every signal) ─────────────────────
        sys_blockers: list = []
        if not auto_buy:
            sys_blockers.append("AUTO_BUY=false")
        if open_count >= max_open:
            sys_blockers.append(f"CAPACITY ({open_count}/{max_open})")
        if not dry_run and not fg_favorable:
            sys_blockers.append(f"F&G={fg_value} <25")

        # ── Evaluate each signal (mirrors P183 multi-band gate logic) ─────────
        candidates: list = []
        for sig in signals:
            mint       = sig.get("mint", "")
            score      = float(sig.get("score") or 0)
            rug        = sig.get("rug_label", "UNKNOWN")
            bp         = float(sig.get("buy_pressure") or 50.0)
            revoked    = bool(sig.get("mint_revoked", False))
            vacc       = float(sig.get("vol_acceleration") or 0.0)
            holder_pct = float(sig.get("top_holder_pct") or 0.0)

            sig_blockers: list = []
            if mint in open_mints:
                sig_blockers.append("ALREADY_OPEN")

            # Patch 312: hard mcap floor — explicit $1.5M veto.
            # Prevents sub-floor tokens from reaching BUY_NOW or WATCH status
            # regardless of score/rug/bp gates below.
            _st_mcap = sig.get("mcap_usd")
            if _st_mcap is None or float(_st_mcap) < 1_500_000:
                _st_mcap_str = f"${float(_st_mcap):,.0f}" if _st_mcap is not None else "unknown"
                sig_blockers.append(f"MCAP_FLOOR ({_st_mcap_str} < $1.5M)")

            # Score / band gate — exact mirror of P183 multi-band logic
            if multi_band_mode and bands:
                _min_lo = min(b["lo"] for b in bands)
                if score < _min_lo:
                    sig_blockers.append(f"SCORE_BELOW_BANDS ({score:.0f})")
                elif not any(b["lo"] <= score < b["hi"] for b in bands):
                    sig_blockers.append(f"DEAD_ZONE ({score:.0f})")
            else:
                if score < threshold:
                    sig_blockers.append(f"SCORE_LOW ({score:.0f}<{threshold:.0f})")
                elif score > max_score:
                    sig_blockers.append(f"SCORE_HIGH ({score:.0f}>{max_score:.0f})")

            if rug != "GOOD":        sig_blockers.append(f"RUG={rug}")
            if bp < 55:              sig_blockers.append(f"BP={bp:.0f}%<55%")
            if not revoked:          sig_blockers.append("MINT_LIVE")
            if vacc < vacc_min:      sig_blockers.append(f"VACC={vacc:.1f}<{vacc_min:.1f}")
            if holder_pct > holder_max:
                sig_blockers.append(f"HOLDER={holder_pct:.0f}%>{holder_max:.0f}%")

            all_blockers = sys_blockers + sig_blockers
            if not all_blockers:
                status = "BUY_NOW"
            elif not sig_blockers:
                status = "WATCH"    # signal is clean; only system-level gate blocking
            else:
                status = "BLOCKED"

            candidates.append({
                "mint":                mint,
                "symbol":              sig.get("symbol", "?"),
                "score":               round(score, 1),
                "rug_label":           rug,
                "buy_pressure":        round(bp, 1),
                "mint_revoked":        revoked,
                "vol_acceleration":    round(vacc, 2),
                "top_holder_pct":      round(holder_pct, 1),
                "top5_holder_pct":     round(float(sig.get("top5_holder_pct") or 0), 1),
                "bundle_risk_level":   sig.get("bundle_risk_level", "CLEAN"),
                "bundle_risk_reasons": sig.get("bundle_risk_reasons", []),
                "mcap_usd":            sig.get("mcap_usd"),
                "narrative":           sig.get("narrative"),
                "scanned_at":          sig.get("scanned_at"),
                "status":              status,
                "blockers":            all_blockers,
                "signal_blockers":     sig_blockers,
            })

        return {
            "candidates":      candidates,
            "signal_count":    len(signals),
            "open_count":      open_count,
            "max_open":        max_open,
            "dry_run":         dry_run,
            "auto_buy":        auto_buy,
            "fg_value":        fg_value,
            "fg_favorable":    fg_favorable,
            "multi_band_mode": multi_band_mode,
            "active_bands":    [{"lo": b["lo"], "hi": b["hi"], "wr": b.get("wr")}
                                 for b in bands],
            "sys_blockers":    sys_blockers,
        }

    try:
        return await asyncio.to_thread(_run)
    except Exception as exc:
        log.warning("memecoins_top_candidates_ep error: %s", exc)
        return {"error": str(exc), "candidates": [], "signal_count": 0}


# ── Patch 313: Eligible universe diagnostic ───────────────────────────────────

@router.get("/eligible-universe")
async def eligible_universe_ep(_: str = Depends(get_current_user)):
    _memecoins_v2_retired("/api/memecoins/eligible-universe")

    """
    Patch 313 — Eligible-universe diagnostic.

    Returns the curated working set of symbols the NBA and top-candidates
    action surfaces draw from, along with per-symbol exclusion reasons for
    every symbol in the 30-day MSO history that did NOT qualify.

    Six-gate eligibility rules:
      1. scan_count_30d >= 3      — persistent scanner presence
      2. last_scan within 14d    — recency
      3. mcap_usd >= $1.5M       — confirmed above floor in current scan cache
      4. liquidity_usd >= $50K   — minimum tradeable depth
      5. coin_quality != WEAK    — Patch 311 structural veto
      6. perf_tier != PROVEN_NEGATIVE — confirmed poor track record excluded

    Safe to poll at any frequency — read-only.
    """
    _ensure_engine_path()

    def _run() -> dict:
        import json as _json
        import sqlite3 as _sq3
        from datetime import datetime, timezone, timedelta

        now    = datetime.now(timezone.utc)
        c30    = (now - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
        c14    = (now - timedelta(days=14)).strftime("%Y-%m-%d %H:%M:%S")
        EU_MIN_SCANS = 3
        EU_MIN_LIQ   = 50_000
        MCAP_FLOOR   = 1_500_000

        with _sq3.connect(_db_path()) as conn:
            conn.row_factory = _sq3.Row
            _ensure_memecoin_outcome_label_schema(conn)

            # Scan appearance counts from MSO
            mso_rows = conn.execute("""
                SELECT symbol,
                       COUNT(*)        AS cnt_30d,
                       MAX(scanned_at) AS last_scan
                FROM memecoin_signal_outcomes
                WHERE scanned_at >= ?
                GROUP BY symbol
            """, (c30,)).fetchall()
            scan_stats = {
                r["symbol"]: {"cnt": int(r["cnt_30d"]), "last": r["last_scan"]}
                for r in mso_rows
            }

            # Per-symbol historical performance (for PROVEN_NEGATIVE gate)
            perf_rows = conn.execute("""
                SELECT symbol,
                       COUNT(*) AS n,
                       ROUND(AVG(return_24h_pct), 1)                                   AS avg_return,
                       ROUND(SUM(CASE WHEN return_24h_pct >= 10 THEN 1.0 ELSE 0.0 END)
                             * 100.0 / COUNT(*), 1)                                    AS win_rate
                FROM memecoin_signal_outcomes
                WHERE status = 'COMPLETE' AND return_24h_pct IS NOT NULL
                GROUP BY symbol
            """).fetchall()

            corr_start = (now - timedelta(days=14)).strftime("%Y-%m-%d")
            corr_rows = conn.execute("""
                SELECT symbol, return_24h_pct
                FROM memecoin_signal_outcomes
                WHERE status = 'COMPLETE'
                  AND return_24h_pct IS NOT NULL
                  AND scanned_at >= ?
            """, (corr_start,)).fetchall()

            last_scan_rows = conn.execute("""
                SELECT symbol, mcap_at_scan, liquidity_usd, rug_label,
                       top_holder_pct, top5_holder_pct,
                       volume_24h, token_age_days, holder_quality_level, scanned_at
                FROM memecoin_signal_outcomes
                WHERE source = 'SCANNER'
                  AND scanned_at = (
                      SELECT MAX(scanned_at) FROM memecoin_signal_outcomes m2
                      WHERE m2.symbol = memecoin_signal_outcomes.symbol
                        AND m2.source = 'SCANNER'
                  )
            """).fetchall()

        # Perf tier map
        sym_perf: dict = {}
        for pr in perf_rows:
            pn   = int(pr["n"])
            pavg = float(pr["avg_return"] or 0)
            pwr  = float(pr["win_rate"]   or 0)
            if   pn >= 15 and pwr >= 50 and pavg >= 5:  ptier = "PROVEN_POSITIVE"
            elif pn >= 15 and pavg < -10:                ptier = "PROVEN_NEGATIVE"
            elif pn >= 8:                                ptier = "TESTED_NEUTRAL"
            else:                                        ptier = "UNPROVEN"
            sym_perf[pr["symbol"]] = {"n": pn, "avg": round(pavg, 1),
                                      "wr": round(pwr, 1), "tier": ptier}

        corr_perf: dict = {}
        corr_by_sym: dict = {}
        for cr in corr_rows:
            corr_by_sym.setdefault(cr["symbol"], []).append(float(cr["return_24h_pct"]))
        for sym, rets in corr_by_sym.items():
            cn = len(rets)
            cavg = round(sum(rets) / cn, 1)
            if cn >= 2 and cavg < -30.0:
                ctier = "CASUALTY"
            elif cn >= 2 and cavg > -5.0 and (sum(1 for r in rets if r > -20.0) / cn) >= 0.70:
                ctier = "SURVIVOR"
            elif cn >= 2:
                ctier = "TRACKER"
            else:
                ctier = "UNRATED"
            corr_perf[sym] = {"n": cn, "avg": cavg, "tier": ctier}

        # Current scan cache
        try:
            from utils.memecoin_scanner import get_cached_signals  # type: ignore
            signals = get_cached_signals()
        except Exception:
            signals = []
        sig_by_sym = {s.get("symbol"): s for s in signals if s.get("symbol")}
        last_scan_sig = {
            r["symbol"]: {
                "mcap_usd":             float(r["mcap_at_scan"] or 0),
                "liquidity_usd":        float(r["liquidity_usd"] or 0),
                "rug_label":            r["rug_label"],
                "top_holder_pct":       float(r["top_holder_pct"] or 0),
                "top5_holder_pct":      float(r["top5_holder_pct"] or 0),
                "volume_24h":           float(r["volume_24h"] or 0),
                "token_age_days":       float(r["token_age_days"] or 0),
                "holder_quality_level": r["holder_quality_level"],
                "scanned_at":           r["scanned_at"],
            }
            for r in last_scan_rows
        }

        # Coin-quality verdict (pure function, no external lookups)
        def _cqv(sig: dict) -> str:
            rug   = str(sig.get("rug_label")        or "").upper()
            top1  = float(sig.get("top_holder_pct") or 0)
            top5  = float(sig.get("top5_holder_pct") or 0)
            liq   = float(sig.get("liquidity_usd")  or 0)
            vol24 = float(sig.get("volume_24h")     or 0)
            age   = float(sig.get("token_age_days") or 0)
            hq    = str(sig.get("holder_quality_level") or "").upper()
            vl    = (vol24 / liq) if liq > 1000 else 0.0
            if (rug == "DANGER" or top1 >= 80 or top5 >= 90
                    or (0 < liq < 30_000) or vl > 25):
                return "WEAK"
            if (rug in ("WARN", "UNKNOWN") or (50 <= top1 < 80)
                    or (70 <= top5 < 90) or (0 < liq < 75_000)
                    or (0 < age < 7) or (10 < vl <= 25) or hq == "RISKY"):
                return "QUESTIONABLE"
            return "CLEAR"

        # Evaluate each symbol
        all_syms = set(scan_stats.keys()) | set(sig_by_sym.keys()) | set(last_scan_sig.keys())
        eligible: list = []
        excluded: dict = {}

        for sym in sorted(all_syms):
            reasons: list = []
            estat = scan_stats.get(sym, {})
            ecnt  = estat.get("cnt",  0)
            elast = estat.get("last", "") or ""

            if ecnt < EU_MIN_SCANS:
                reasons.append(f"scan_count_30d={ecnt} (<{EU_MIN_SCANS})")
            if not elast or elast < c14:
                reasons.append(f"last_scan={'stale' if elast else 'never'} (>14d)")

            esig = sig_by_sym.get(sym) or last_scan_sig.get(sym)
            if esig:
                emcap = esig.get("mcap_usd")
                eliq  = float(esig.get("liquidity_usd") or 0)
                if emcap is None or float(emcap) < MCAP_FLOOR:
                    reasons.append(
                        "mcap={}(<$1.5M)".format(
                            "${:,.0f}".format(float(emcap)) if emcap is not None else "unknown"
                        )
                    )
                if eliq < EU_MIN_LIQ:
                    reasons.append(f"liq=${eliq:,.0f} (<${EU_MIN_LIQ:,.0f})")
                if _cqv(esig) == "WEAK":
                    reasons.append("coin_quality=WEAK")
            else:
                reasons.append("no_recent_scan_snapshot")

            if sym_perf.get(sym, {}).get("tier") == "PROVEN_NEGATIVE":
                reasons.append("perf_tier=PROVEN_NEGATIVE")
            if corr_perf.get(sym, {}).get("tier") == "CASUALTY":
                reasons.append("correction_tier=CASUALTY")

            sp = sym_perf.get(sym, {})
            entry = {
                "symbol":        sym,
                "scan_count_30d": ecnt,
                "last_scan":      elast or None,
                "mcap_usd":       esig.get("mcap_usd") if esig else None,
                "liquidity_usd":  (float(esig.get("liquidity_usd") or 0) or None) if esig else None,
                "coin_quality":   _cqv(esig) if esig else None,
                "perf_tier":      sp.get("tier", "UNPROVEN"),
                "correction_tier": corr_perf.get(sym, {}).get("tier", "UNRATED"),
                "perf_n":         sp.get("n", 0),
                "perf_avg":       sp.get("avg"),
            }
            if reasons:
                excluded[sym] = {"reasons": reasons, **entry}
            else:
                eligible.append(entry)

        # Exclusion breakdown by gate
        gate_counts: dict = {}
        for sym, ex in excluded.items():
            for r in ex["reasons"]:
                key = r.split("=")[0].split("(")[0].strip()
                gate_counts[key] = gate_counts.get(key, 0) + 1

        return {
            "eligible": {
                "count":   len(eligible),
                "symbols": eligible,
            },
            "excluded": {
                "count":       len(excluded),
                "gate_counts": gate_counts,
                "symbols":     dict(list(excluded.items())),
            },
            "universe_size_total": len(all_syms),
            "gates": {
                "min_scan_count_30d":   EU_MIN_SCANS,
                "min_liquidity_usd":    EU_MIN_LIQ,
                "mcap_floor":           MCAP_FLOOR,
                "max_last_scan_age_d":  14,
                "coin_quality_veto":    "WEAK",
                "perf_tier_exclusion":  "PROVEN_NEGATIVE",
            },
            "generated_at": now.isoformat(),
        }

    try:
        return await asyncio.to_thread(_run)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ── Patch 205: Sample-build tracker ───────────────────────────────────────────

_SB_TARGET         = 20
_SB_COOLDOWN_DATE  = "2026-03-07T00:00:00"  # matches pilot-readiness gate


@router.get("/sample-build-tracker")
async def sample_build_tracker_ep(_: str = Depends(get_current_user)):
    _memecoins_v2_retired("/api/memecoins/sample-build-tracker")

    """
    Patch 205 — Read-only sample-build experiment progress tracker.

    Returns metrics from alert_outcomes for is_sample_build=1 rows so the
    operator can see whether evidence is accumulating fast enough and what
    quality the 24h cohort is showing.  No writes, no execution changes.
    """
    _ensure_engine_path()

    def _run() -> dict:
        from datetime import datetime, timezone, timedelta
        from utils.db import get_conn as _gc  # type: ignore

        enabled   = os.getenv("SAMPLE_BUILD_MODE",      "false").lower() == "true"
        score_min = int(os.getenv("SAMPLE_BUILD_SCORE_MIN", "65"))

        now_utc   = datetime.now(timezone.utc)
        ago_24h   = (now_utc - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%S")
        ago_72h   = (now_utc - timedelta(hours=72)).strftime("%Y-%m-%dT%H:%M:%S")

        with _gc() as conn:
            # ── Post-cooldown evaluated counts ─────────────────────────────────
            pc_all = conn.execute("""
                SELECT COUNT(*) FROM alert_outcomes
                WHERE  created_ts_utc >= ? AND return_24h_pct IS NOT NULL
                  AND  is_sample_build = 0
            """, (_SB_COOLDOWN_DATE,)).fetchone()[0] or 0

            pc_sb = conn.execute("""
                SELECT COUNT(*) FROM alert_outcomes
                WHERE  created_ts_utc >= ? AND return_24h_pct IS NOT NULL
                  AND  is_sample_build = 1
            """, (_SB_COOLDOWN_DATE,)).fetchone()[0] or 0

            # ── 24h creation pace (new rows in last 24h) ───────────────────────
            new_24h_sb = conn.execute("""
                SELECT COUNT(*) FROM alert_outcomes
                WHERE  created_ts_utc >= ? AND is_sample_build = 1
            """, (ago_24h,)).fetchone()[0] or 0

            # ── 72h creation pace (for projection) ────────────────────────────
            new_72h_sb = conn.execute("""
                SELECT COUNT(*) FROM alert_outcomes
                WHERE  created_ts_utc >= ? AND is_sample_build = 1
            """, (ago_72h,)).fetchone()[0] or 0

            # ── 24h evaluated cohort quality ───────────────────────────────────
            eval_rows = conn.execute("""
                SELECT return_24h_pct FROM alert_outcomes
                WHERE  evaluated_24h_ts_utc >= ?
                  AND  return_24h_pct IS NOT NULL
                  AND  is_sample_build = 1
            """, (ago_24h,)).fetchall()
            completed_24h_sb = len(eval_rows)
            if completed_24h_sb > 0:
                returns = [float(r[0]) for r in eval_rows]
                wr_24h_sb  = round(sum(1 for r in returns if r > 0) / len(returns) * 100, 1)
                avg_24h_sb = round(sum(returns) / len(returns), 2)
            else:
                wr_24h_sb  = None
                avg_24h_sb = None

            # ── Latest timestamps ──────────────────────────────────────────────
            latest_signal = conn.execute("""
                SELECT MAX(created_ts_utc) FROM alert_outcomes
                WHERE  is_sample_build = 1
            """).fetchone()[0]

            latest_completed = conn.execute("""
                SELECT MAX(evaluated_24h_ts_utc) FROM alert_outcomes
                WHERE  is_sample_build = 1 AND return_24h_pct IS NOT NULL
            """).fetchone()[0]

        # ── Projection ────────────────────────────────────────────────────────
        # Rate = rows created in last 72h ÷ 3 (rows/day)
        # Remaining = target - already-evaluated sample-build rows
        # projected_days = remaining / rate  (null if rate == 0)
        remaining = max(0, _SB_TARGET - int(pc_sb))
        rate_per_day = round(int(new_72h_sb) / 3.0, 2) if new_72h_sb else 0.0
        if remaining == 0:
            projected_days = 0.0
        elif rate_per_day > 0:
            projected_days = round(remaining / rate_per_day, 1)
        else:
            projected_days = None

        # ── Status ────────────────────────────────────────────────────────────
        if not enabled:
            status = "OFF"
        elif int(pc_sb) >= _SB_TARGET:
            status = "READY"
        else:
            status = "COLLECTING"

        return {
            "enabled":                     enabled,
            "score_min":                   score_min,
            "post_cooldown_target":        _SB_TARGET,
            "post_cooldown_n":             int(pc_all),
            "post_cooldown_n_sample_build": int(pc_sb),
            "new_rows_24h":                int(new_24h_sb),
            "new_rows_24h_sample_build":   int(new_24h_sb),
            "completed_24h_n_sample_build": completed_24h_sb,
            "wr_24h_sample_build":         wr_24h_sb,
            "avg_24h_sample_build":        avg_24h_sb,
            "projected_days_to_target":    projected_days,
            "rate_per_day":                rate_per_day,
            "latest_signal_ts":            latest_signal,
            "latest_completed_24h_ts":     latest_completed,
            "status":                      status,
            "fetched_at":                  now_utc.isoformat(),
        }

    try:
        return await asyncio.to_thread(_run)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ── Patch 210: Alert threshold simulator ──────────────────────────────────────

_SIM_COOLDOWN = "2026-03-07T00:00:00"
_SIM_TARGET   = 20

@router.get("/threshold-simulator")
async def threshold_simulator_ep(_: str = Depends(get_current_user)):
    _memecoins_v2_retired("/api/memecoins/threshold-simulator")

    """
    Patch 210 — Read-only alert-threshold decision support.

    Compares candidate ALERT_THRESHOLD values using historical
    memecoin_signal_outcomes data.  For each threshold reports:
      eligible_n, post_cooldown_eligible_n, completed_24h_n, wr_24h, avg_24h,
      catastrophic_loss_rate, rate_per_day, estimated_days_to_20, verdict.

    verdict: CURRENT | BALANCED | TOO_LOOSE | null (insufficient data)
    is_best: true on the highest-threshold BALANCED row (best tradeoff candidate)

    No writes.  No env changes.  No execution impact.
    """
    _ensure_engine_path()

    def _run() -> dict:
        from datetime import datetime, timezone
        from utils.db import get_conn as _gc  # type: ignore

        # Read live from .env so the display stays accurate after config changes
        # without requiring a dashboard restart (mirrors config_editor pattern).
        try:
            from config_editor import _read_env as _ce_read  # type: ignore
            _env_live = _ce_read()
            current_t = int(float(_env_live.get("ALERT_THRESHOLD", "95")))
        except Exception:
            current_t = int(float(os.getenv("ALERT_THRESHOLD", "95")))
        now_utc      = datetime.now(timezone.utc)
        cooldown_dt  = datetime.fromisoformat(_SIM_COOLDOWN)
        elapsed_days = (now_utc.replace(tzinfo=None) - cooldown_dt).total_seconds() / 86400

        candidates = sorted({current_t, 95, 90, 85, 80, 75, 65}, reverse=True)

        with _gc() as conn:
            # Current gate count from alert_outcomes (actual, for remaining calc)
            n_gate_current = int(conn.execute(
                "SELECT COUNT(*) FROM alert_outcomes "
                "WHERE created_ts_utc >= ? AND return_24h_pct IS NOT NULL",
                (_SIM_COOLDOWN,)
            ).fetchone()[0] or 0)

            rows_out = []
            for t in candidates:
                # All-time quality from MSO (larger sample — more reliable WR/avg)
                r_all = conn.execute("""
                    SELECT COUNT(*) AS n,
                           SUM(CASE WHEN return_24h_pct IS NOT NULL THEN 1 ELSE 0 END) AS completed,
                           SUM(CASE WHEN return_24h_pct > 0 THEN 1 ELSE 0 END) AS wins,
                           AVG(CASE WHEN return_24h_pct IS NOT NULL THEN return_24h_pct END) AS avg,
                           SUM(CASE WHEN return_24h_pct <= -20 THEN 1 ELSE 0 END) AS cat
                    FROM memecoin_signal_outcomes WHERE score >= ?
                """, (t,)).fetchone()

                # Post-cooldown MSO count (for velocity / rate)
                n_pc = int(conn.execute(
                    "SELECT COUNT(*) FROM memecoin_signal_outcomes "
                    "WHERE score >= ? AND scanned_at >= ?",
                    (t, _SIM_COOLDOWN)
                ).fetchone()[0] or 0)

                eligible_n  = int(r_all["n"] or 0)
                completed_n = int(r_all["completed"] or 0)
                wins        = int(r_all["wins"] or 0)
                avg_24h_raw = r_all["avg"]
                cat_n       = int(r_all["cat"] or 0)

                wr_24h   = round(wins / completed_n * 100, 1) if completed_n > 0 else None
                avg_24h  = round(float(avg_24h_raw), 2) if avg_24h_raw is not None else None
                cat_rate = round(cat_n / completed_n * 100, 1) if completed_n >= 5 else None

                # Rate: post-cooldown MSO entries / elapsed days (min 1 entry)
                rate_pd  = round(n_pc / elapsed_days, 2) if elapsed_days >= 1 and n_pc >= 1 else None
                remaining = max(0, _SIM_TARGET - n_gate_current)
                est_days  = round(remaining / rate_pd, 1) if rate_pd and rate_pd > 0 else None

                # Verdict
                if t == current_t:
                    verdict = "CURRENT"
                elif completed_n < 10:
                    verdict = None
                elif (wr_24h is not None and wr_24h < 35) or \
                     (avg_24h is not None and avg_24h < -13) or \
                     (cat_rate is not None and cat_rate >= 47):
                    verdict = "TOO_LOOSE"
                else:
                    verdict = "BALANCED"

                rows_out.append({
                    "threshold":               t,
                    "is_current":              t == current_t,
                    "is_best":                 False,
                    "eligible_n":              eligible_n,
                    "post_cooldown_eligible_n": n_pc,
                    "completed_24h_n":         completed_n,
                    "wr_24h":                  wr_24h,
                    "avg_24h":                 avg_24h,
                    "catastrophic_loss_rate":  cat_rate,
                    "rate_per_day":            rate_pd,
                    "estimated_days_to_20":    est_days,
                    "verdict":                 verdict,
                })

        # Mark is_best: first BALANCED row (highest threshold with good quality)
        for row in rows_out:
            if row["verdict"] == "BALANCED":
                row["is_best"] = True
                break

        return {
            "current_threshold": current_t,
            "n_gate_current":    n_gate_current,
            "cooldown_date":     _SIM_COOLDOWN,
            "rows":              rows_out,
            "generated_at":      now_utc.isoformat(),
        }

    try:
        return await asyncio.to_thread(_run)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ── Patch 212: Threshold change impact tracker ────────────────────────────────

@router.get("/threshold-change-impact")
async def threshold_change_impact_ep(_: str = Depends(get_current_user)):
    _memecoins_v2_retired("/api/memecoins/threshold-change-impact")

    """
    Patch 212 — Read-only post-change impact tracker.

    Compares signal quality and entry rate before/after the ALERT_THRESHOLD
    change from 95 → 90.  Source of truth for change_ts: .env file mtime.

    pre:  score >= 95  (PRE_THRESHOLD), all MSO history before change_ts
    post: score >= current_threshold,  all MSO data since change_ts

    status: TOO_EARLY (< 1.5d elapsed or < 5 completed) |
            IMPROVING | WORSE | MIXED

    No writes.  No env changes.  No execution impact.
    """
    _ensure_engine_path()

    def _run() -> dict:
        import datetime as _dt
        from utils.db import get_conn as _gc  # type: ignore

        # ── Change timestamp: .env file mtime is the most reliable proxy ───────
        env_path           = "/root/memecoin_engine/.env"
        FALLBACK_CHANGE_TS = "2026-03-11T21:48:59"
        PRE_THRESHOLD      = 95

        try:
            from config_editor import _read_env as _ce_read  # type: ignore
            _env_live      = _ce_read()
            post_threshold = int(float(_env_live.get("ALERT_THRESHOLD", "90")))
        except Exception:
            post_threshold = int(float(os.getenv("ALERT_THRESHOLD", "90")))

        try:
            mtime_unix       = os.path.getmtime(env_path)
            change_dt        = _dt.datetime.utcfromtimestamp(mtime_unix)
            change_ts        = change_dt.strftime("%Y-%m-%dT%H:%M:%S")
            change_ts_source = "env_mtime"
        except Exception:
            change_ts        = FALLBACK_CHANGE_TS
            change_dt        = _dt.datetime.fromisoformat(FALLBACK_CHANGE_TS)
            change_ts_source = "hardcoded_fallback"

        now_utc           = _dt.datetime.utcnow()
        elapsed_post_days = (now_utc - change_dt).total_seconds() / 86400

        with _gc() as conn:
            # Anchor elapsed_pre_days to the first MSO entry (not a fixed 90d cap
            # — the whole dataset is only ~10d, so use all available history).
            first_mso_ts = conn.execute(
                "SELECT MIN(scanned_at) FROM memecoin_signal_outcomes"
            ).fetchone()[0]
            if first_mso_ts:
                first_mso_dt     = _dt.datetime.fromisoformat(first_mso_ts[:19])
                elapsed_pre_days = max(1.0, (change_dt - first_mso_dt).total_seconds() / 86400)
            else:
                elapsed_pre_days = 10.0

            # ── Stats helper ───────────────────────────────────────────────────
            def _stats(thr, ts_from, ts_to):
                """Return (n, completed, wr_24h, avg_24h, cat_rate) for a window.
                ts_to=None means open-ended (up to present)."""
                if ts_to is not None:
                    r = conn.execute("""
                        SELECT COUNT(*) AS n,
                               SUM(CASE WHEN return_24h_pct IS NOT NULL THEN 1 ELSE 0 END) AS completed,
                               SUM(CASE WHEN return_24h_pct > 0  THEN 1.0 ELSE 0 END) AS wins,
                               AVG(CASE WHEN return_24h_pct IS NOT NULL THEN return_24h_pct END) AS avg,
                               SUM(CASE WHEN return_24h_pct <= -20 THEN 1 ELSE 0 END) AS cat
                        FROM memecoin_signal_outcomes
                        WHERE score >= ? AND scanned_at >= ? AND scanned_at < ?
                    """, (thr, ts_from, ts_to)).fetchone()
                else:
                    r = conn.execute("""
                        SELECT COUNT(*) AS n,
                               SUM(CASE WHEN return_24h_pct IS NOT NULL THEN 1 ELSE 0 END) AS completed,
                               SUM(CASE WHEN return_24h_pct > 0  THEN 1.0 ELSE 0 END) AS wins,
                               AVG(CASE WHEN return_24h_pct IS NOT NULL THEN return_24h_pct END) AS avg,
                               SUM(CASE WHEN return_24h_pct <= -20 THEN 1 ELSE 0 END) AS cat
                        FROM memecoin_signal_outcomes
                        WHERE score >= ? AND scanned_at >= ?
                    """, (thr, ts_from)).fetchone()

                n_rows    = int(r["n"] or 0)
                completed = int(r["completed"] or 0)
                wins      = float(r["wins"] or 0)
                avg_raw   = r["avg"]
                cat_n     = int(r["cat"] or 0)

                wr_24h   = round(wins / completed * 100, 1)  if completed > 0    else None
                avg_24h  = round(float(avg_raw), 2)          if avg_raw is not None else None
                cat_rate = round(cat_n / completed * 100, 1) if completed >= 5    else None

                return n_rows, completed, wr_24h, avg_24h, cat_rate

            # Pre: all MSO before change_ts at t=PRE_THRESHOLD
            pre_n, pre_comp, pre_wr, pre_avg, pre_cat = _stats(
                PRE_THRESHOLD, "1970-01-01T00:00:00", change_ts
            )
            pre_rate = round(pre_n / elapsed_pre_days, 2) if pre_n > 0 and elapsed_pre_days > 0 else None

            # Post: all MSO since change_ts at t=post_threshold
            post_n, post_comp, post_wr, post_avg, post_cat = _stats(
                post_threshold, change_ts, None
            )
            post_rate = round(post_n / elapsed_post_days, 2) if post_n > 0 and elapsed_post_days > 0 else None

        # ── Status ──────────────────────────────────────────────────────────────
        # TOO_EARLY: post window too short or not enough completed 24h evals.
        # Completed evals take ≥24h to fill after signal creation, so effectively
        # nothing will be ready for the first ~48h.
        if elapsed_post_days < 1.5 or post_comp < 5:
            status = "TOO_EARLY"
        elif post_wr is not None and pre_wr is not None:
            wr_better  = post_wr  > pre_wr
            avg_better = (post_avg is not None and pre_avg is not None and post_avg > pre_avg)
            if wr_better and avg_better:
                status = "IMPROVING"
            elif not wr_better and not avg_better:
                status = "WORSE"
            else:
                status = "MIXED"
        else:
            status = "TOO_EARLY"

        # ── Deltas (null when either side is missing) ────────────────────────────
        def _d(a, b):
            return round(a - b, 2) if a is not None and b is not None else None

        return {
            "change_ts":          change_ts,
            "change_ts_source":   change_ts_source,
            "pre_threshold":      PRE_THRESHOLD,
            "post_threshold":     post_threshold,
            "pre_window":         (
                f"all MSO before {change_ts[:10]} "
                f"({pre_n} signals, {round(elapsed_pre_days, 1)}d)"
            ),
            "post_window":        (
                f"MSO since {change_ts[:10]} "
                f"({post_n} signals, {round(elapsed_post_days, 1)}d)"
            ),
            "elapsed_post_days":  round(elapsed_post_days, 2),
            "pre": {
                "threshold":              PRE_THRESHOLD,
                "entries_per_day":        pre_rate,
                "completed_24h_n":        pre_comp,
                "wr_24h":                 pre_wr,
                "avg_24h":               pre_avg,
                "catastrophic_loss_rate": pre_cat,
            },
            "post": {
                "threshold":              post_threshold,
                "entries_per_day":        post_rate,
                "completed_24h_n":        post_comp,
                "wr_24h":                 post_wr,
                "avg_24h":               post_avg,
                "catastrophic_loss_rate": post_cat,
            },
            "delta": {
                "entry_rate_delta":   _d(post_rate, pre_rate),
                "wr_delta":           _d(post_wr,   pre_wr),
                "avg_24h_delta":      _d(post_avg,  pre_avg),
                "catastrophic_delta": _d(post_cat,  pre_cat),
            },
            "status":       status,
            "generated_at": now_utc.isoformat(),
        }

    try:
        return await asyncio.to_thread(_run)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ── Patch 215: Post-trade factor attribution ──────────────────────────────────

@router.get("/attribution")
async def get_attribution(_: str = Depends(get_current_user)):
    """
    Read-only factor attribution across all completed memecoin signal outcomes.
    Buckets each available factor and computes WR/avg/cat_rate relative to the
    all-rows baseline.  Factors without sufficient coverage are returned in
    sparse_factors with an explanation.
    Patch 215.
    """
    COOLDOWN = "2026-03-07T00:00:00"

    def _run():
        db   = _db_path()
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row

        BASE = f"status = 'COMPLETE' AND return_24h_pct IS NOT NULL AND scanned_at >= '{COOLDOWN}'"

        # ── Baseline (all complete rows) ──────────────────────────────────────
        bl = conn.execute(f"""
            SELECT
              COUNT(*)                                                             AS n,
              ROUND(AVG(CASE WHEN return_24h_pct > 0 THEN 1.0 ELSE 0.0 END)*100,1) AS wr,
              ROUND(AVG(return_24h_pct), 2)                                        AS avg_ret,
              ROUND(AVG(CASE WHEN return_24h_pct <= -20 THEN 1.0 ELSE 0.0 END)*100,1) AS cat,
              MIN(scanned_at)                                                      AS oldest,
              MAX(scanned_at)                                                      AS newest
            FROM memecoin_signal_outcomes WHERE {BASE}
        """).fetchone()

        n_total = bl["n"]       or 0
        b_wr    = bl["wr"]      or 0.0
        b_avg   = bl["avg_ret"] or 0.0

        def _verdict(n, wr, avg):
            if not n or n < 20:
                return "TOO_SMALL"
            wr_d  = (wr  or 0) - b_wr
            avg_d = (avg or 0) - b_avg
            if wr_d >= 5 and avg_d >= 5:
                return "HELPFUL"
            if wr_d <= -8 or avg_d <= -10:
                return "HURTFUL"
            return "NEUTRAL"

        def _q(sql):
            return [dict(r) for r in conn.execute(sql).fetchall()]

        def _factor(name, label, rows):
            buckets = []
            for r in rows:
                n   = r.get("n", 0)
                wr  = r.get("wr_24h")
                avg = r.get("avg_24h")
                buckets.append({
                    "bucket":   r["bucket"],
                    "n":        n,
                    "wr_24h":   wr,
                    "avg_24h":  avg,
                    "cat_rate": r.get("cat_rate"),
                    "verdict":  _verdict(n, wr, avg),
                })
            return {"name": name, "label": label, "buckets": buckets}

        # ── 1. Score bands (10-pt) ────────────────────────────────────────────
        score_rows = _q(f"""
            SELECT
              CAST((CAST(score AS INTEGER) / 10) * 10 AS TEXT)
                || '-'
                || CAST((CAST(score AS INTEGER) / 10) * 10 + 10 AS TEXT) AS bucket,
              (CAST(score AS INTEGER) / 10) * 10                          AS lo,
              COUNT(*)                                                     AS n,
              ROUND(AVG(CASE WHEN return_24h_pct > 0 THEN 1.0 ELSE 0.0 END)*100,1) AS wr_24h,
              ROUND(AVG(return_24h_pct), 2)                                AS avg_24h,
              ROUND(AVG(CASE WHEN return_24h_pct <= -20 THEN 1.0 ELSE 0.0 END)*100,1) AS cat_rate
            FROM memecoin_signal_outcomes
            WHERE score IS NOT NULL AND {BASE}
            GROUP BY lo ORDER BY lo
        """)

        # ── 2. Top-holder concentration ───────────────────────────────────────
        holder_rows = _q(f"""
            SELECT
              CASE
                WHEN top_holder_pct < 3 THEN '<3%'
                WHEN top_holder_pct < 5 THEN '3-5%'
                WHEN top_holder_pct < 8 THEN '5-8%'
                ELSE '>=8%'
              END                                                          AS bucket,
              MIN(top_holder_pct)                                          AS sort_v,
              COUNT(*)                                                     AS n,
              ROUND(AVG(CASE WHEN return_24h_pct > 0 THEN 1.0 ELSE 0.0 END)*100,1) AS wr_24h,
              ROUND(AVG(return_24h_pct), 2)                                AS avg_24h,
              ROUND(AVG(CASE WHEN return_24h_pct <= -20 THEN 1.0 ELSE 0.0 END)*100,1) AS cat_rate
            FROM memecoin_signal_outcomes
            WHERE top_holder_pct IS NOT NULL AND {BASE}
            GROUP BY bucket ORDER BY sort_v
        """)

        # ── 3. Rug label ──────────────────────────────────────────────────────
        rug_rows = _q(f"""
            SELECT
              rug_label                                                    AS bucket,
              COUNT(*)                                                     AS n,
              ROUND(AVG(CASE WHEN return_24h_pct > 0 THEN 1.0 ELSE 0.0 END)*100,1) AS wr_24h,
              ROUND(AVG(return_24h_pct), 2)                                AS avg_24h,
              ROUND(AVG(CASE WHEN return_24h_pct <= -20 THEN 1.0 ELSE 0.0 END)*100,1) AS cat_rate
            FROM memecoin_signal_outcomes WHERE {BASE}
            GROUP BY rug_label
        """)

        # ── 4. Authority profile (mint + freeze revoked) ──────────────────────
        auth_rows = _q(f"""
            SELECT
              CASE
                WHEN mint_revoked = 1 AND freeze_revoked = 1 THEN 'both revoked'
                WHEN mint_revoked = 0 AND freeze_revoked = 0 THEN 'neither revoked'
                WHEN mint_revoked = 1                        THEN 'mint only'
                ELSE                                              'freeze only'
              END                                                          AS bucket,
              COUNT(*)                                                     AS n,
              ROUND(AVG(CASE WHEN return_24h_pct > 0 THEN 1.0 ELSE 0.0 END)*100,1) AS wr_24h,
              ROUND(AVG(return_24h_pct), 2)                                AS avg_24h,
              ROUND(AVG(CASE WHEN return_24h_pct <= -20 THEN 1.0 ELSE 0.0 END)*100,1) AS cat_rate
            FROM memecoin_signal_outcomes WHERE {BASE}
            GROUP BY bucket
        """)

        # ── 5. LP lock ────────────────────────────────────────────────────────
        lp_rows = _q(f"""
            SELECT
              CASE
                WHEN lp_locked_pct >= 100 THEN '100%'
                WHEN lp_locked_pct >= 80  THEN '80-99%'
                WHEN lp_locked_pct >= 50  THEN '50-79%'
                ELSE '<50%'
              END                                                          AS bucket,
              MIN(lp_locked_pct)                                           AS sort_v,
              COUNT(*)                                                     AS n,
              ROUND(AVG(CASE WHEN return_24h_pct > 0 THEN 1.0 ELSE 0.0 END)*100,1) AS wr_24h,
              ROUND(AVG(return_24h_pct), 2)                                AS avg_24h,
              ROUND(AVG(CASE WHEN return_24h_pct <= -20 THEN 1.0 ELSE 0.0 END)*100,1) AS cat_rate
            FROM memecoin_signal_outcomes
            WHERE lp_locked_pct IS NOT NULL AND {BASE}
            GROUP BY bucket ORDER BY sort_v DESC
        """)

        # ── 6. Token age ──────────────────────────────────────────────────────
        age_rows = _q(f"""
            SELECT
              CASE
                WHEN token_age_days < 1  THEN '<1 day'
                WHEN token_age_days < 7  THEN '1-7 days'
                WHEN token_age_days < 30 THEN '7-30 days'
                ELSE '>30 days'
              END                                                          AS bucket,
              MIN(token_age_days)                                          AS sort_v,
              COUNT(*)                                                     AS n,
              ROUND(AVG(CASE WHEN return_24h_pct > 0 THEN 1.0 ELSE 0.0 END)*100,1) AS wr_24h,
              ROUND(AVG(return_24h_pct), 2)                                AS avg_24h,
              ROUND(AVG(CASE WHEN return_24h_pct <= -20 THEN 1.0 ELSE 0.0 END)*100,1) AS cat_rate
            FROM memecoin_signal_outcomes
            WHERE token_age_days IS NOT NULL AND {BASE}
            GROUP BY bucket ORDER BY sort_v
        """)

        # ── 7. Vol acceleration ───────────────────────────────────────────────
        vol_rows = _q(f"""
            SELECT
              CASE
                WHEN vol_acceleration < 3  THEN '<3'
                WHEN vol_acceleration < 8  THEN '3-8'
                WHEN vol_acceleration < 15 THEN '8-15'
                ELSE '>15'
              END                                                          AS bucket,
              MIN(vol_acceleration)                                        AS sort_v,
              COUNT(*)                                                     AS n,
              ROUND(AVG(CASE WHEN return_24h_pct > 0 THEN 1.0 ELSE 0.0 END)*100,1) AS wr_24h,
              ROUND(AVG(return_24h_pct), 2)                                AS avg_24h,
              ROUND(AVG(CASE WHEN return_24h_pct <= -20 THEN 1.0 ELSE 0.0 END)*100,1) AS cat_rate
            FROM memecoin_signal_outcomes
            WHERE vol_acceleration IS NOT NULL AND {BASE}
            GROUP BY bucket ORDER BY sort_v
        """)

        conn.close()

        factors = [
            _factor("score_band",  "Score Band",       score_rows),
            _factor("top_holder",  "Top Holder %",     holder_rows),
            _factor("rug_label",   "Rug Label",        rug_rows),
            _factor("authority",   "Authority",        auth_rows),
            _factor("lp_lock",     "LP Lock",          lp_rows),
            _factor("token_age",   "Token Age",        age_rows),
            _factor("vol_accel",   "Vol Acceleration", vol_rows),
        ]

        # Factors without sufficient historical coverage — returned for transparency
        sparse_factors = [
            {
                "name":   "holder_quality_level",
                "label":  "Holder Quality",
                "reason": "Patch 214 — 0 COMPLETE rows yet; will populate as new scans cycle",
            },
            {
                "name":   "bundle_risk_level",
                "label":  "Bundle Risk",
                "reason": "92 of 1433 rows labeled; 1341 legacy NULL — insufficient for attribution",
            },
            {
                "name":   "regime",
                "label":  "Regime",
                "reason": "Stored for new scanner rows; awaiting enough COMPLETE coverage for attribution",
            },
            {
                "name":   "fear_greed",
                "label":  "Fear & Greed",
                "reason": "Not stored in memecoin_signal_outcomes",
            },
        ]

        return {
            "n_complete":     n_total,
            "baseline_wr":    b_wr,
            "baseline_avg":   b_avg,
            "baseline_cat":   bl["cat"],
            "data_start":     bl["oldest"],
            "data_end":       bl["newest"],
            "factors":        factors,
            "sparse_factors": sparse_factors,
        }

    try:
        return await asyncio.to_thread(_run)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ── Patch 223 — Entry Context Coverage Tracker ─────────────────────────────
@router.get("/context-coverage")
async def context_coverage_ep(_: str = Depends(get_current_user)):
    _memecoins_v2_retired("/api/memecoins/context-coverage")

    """Read-only. Returns coverage stats for post-fix entry_context data."""
    _ensure_engine_path()

    def _run() -> dict:
        from utils.db import get_conn  # type: ignore
        REVIEW_TARGET = 50
        with get_conn() as conn:
            n = conn.execute("""
                SELECT COUNT(*) FROM memecoin_signal_outcomes
                WHERE status = 'COMPLETE'
                  AND entry_context IS NOT NULL
                  AND entry_context NOT IN ('UNKNOWN', '')
            """).fetchone()[0] or 0

            buckets = conn.execute("""
                SELECT entry_context, COUNT(*) AS cnt
                FROM memecoin_signal_outcomes
                WHERE status = 'COMPLETE'
                  AND entry_context IS NOT NULL
                  AND entry_context NOT IN ('UNKNOWN', '')
                GROUP BY entry_context
                ORDER BY cnt DESC
            """).fetchall()

            top = conn.execute("""
                SELECT symbol, COUNT(*) AS cnt
                FROM memecoin_signal_outcomes
                WHERE status = 'COMPLETE'
                  AND entry_context IS NOT NULL
                  AND entry_context NOT IN ('UNKNOWN', '')
                GROUP BY symbol
                ORDER BY cnt DESC
                LIMIT 1
            """).fetchone()

            latest = conn.execute("""
                SELECT MAX(evaluated_24h_ts_utc)
                FROM memecoin_signal_outcomes
                WHERE status = 'COMPLETE'
                  AND entry_context IS NOT NULL
                  AND entry_context NOT IN ('UNKNOWN', '')
            """).fetchone()[0]

        top_sym = top[0] if top else None
        top_n   = top[1] if top else 0
        top_pct = round(top_n / n * 100, 1) if n > 0 else None

        if n >= REVIEW_TARGET:
            status = "GROWING" if (top_pct and top_pct > 60) else "REVIEW_READY"
        elif n >= 20:
            status = "GROWING"
        else:
            status = "TOO_EARLY"

        return {
            "complete_non_unknown_n":   n,
            "review_target_n":          REVIEW_TARGET,
            "counts_by_context":        {r[0]: r[1] for r in buckets},
            "top_token_symbol":         top_sym,
            "top_token_share_pct":      top_pct,
            "status":                   status,
            "latest_completed_context_ts": latest,
        }

    try:
        return await asyncio.to_thread(_run)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ── Patch 231 — Setup Ladder ──────────────────────────────────────────────────

@router.get("/setups")
async def memecoins_setups_ep(_: str = Depends(get_current_user)):
    _memecoins_v2_retired("/api/memecoins/setups")

    """
    Setup Ladder — synthesizes current scan cache into graded setups.

    Grades each signal A / B / C or BLOCKED using explicit, auditable rules:
      A  — score >= ALERT_THRESHOLD, no hard warnings, clean entry context
      B  — score >= 50, at most 1 soft warning
      C  — score >= 30, doesn't qualify for A or B
      BLOCKED — hard flag present (holder BLOCKED, DANGER rug, bad entry context,
                 or HIGH bundle risk) regardless of score

    Returns:
      best_setups[]     — top 3 graded A/B/C, sorted A->B->C then score desc
      blocked_setups[]  — top 3 BLOCKED, sorted score desc
      market_posture    — single human-readable sentence for operator glance
      generated_at      — UTC ISO timestamp
    """
    _ensure_engine_path()

    def _run() -> dict:
        import json as _json
        import datetime as _dt

        alert_th = int(os.getenv("ALERT_THRESHOLD", "70"))

        # ── Load kv_store data ──────────────────────────────────────────────
        signals: list = []
        learned: dict = {}
        fg_value      = None
        fg_label      = "Unknown"
        fg_favorable  = False

        try:
            from utils.db import get_conn as _gc  # type: ignore
            with _gc() as conn:
                row = conn.execute(
                    "SELECT value FROM kv_store WHERE key='memecoin_scan_cache'"
                ).fetchone()
                if row and row["value"]:
                    signals = _json.loads(row["value"])

                row = conn.execute(
                    "SELECT value FROM kv_store WHERE key='memecoin_learned_thresholds'"
                ).fetchone()
                if row and row["value"]:
                    learned = _json.loads(row["value"]).get("thresholds", {})

                row = conn.execute(
                    "SELECT value FROM kv_store WHERE key='shared_fear_greed'"
                ).fetchone()
                if row and row["value"]:
                    fg = _json.loads(row["value"])
                    fg_value     = fg.get("value")
                    fg_label     = fg.get("label", "Unknown")
                    fg_favorable = bool(fg.get("favorable", False))
        except Exception:
            pass

        max_top_holder = float(learned.get("max_top_holder_pct", 4.7))

        # ── Grade each signal ───────────────────────────────────────────────
        best_setups:    list = []
        blocked_setups: list = []

        for sig in signals:
            score = sig.get("score") or 0
            if score < 30:
                continue  # below radar

            holder_q     = sig.get("holder_quality_level") or "UNKNOWN"
            rug          = sig.get("rug_label")             or "UNKNOWN"
            entry        = sig.get("entry_context")         or "UNKNOWN"
            top_h        = float(sig.get("top_holder_pct") or 0)
            bundle       = sig.get("bundle_risk_level")     or "CLEAN"
            buy_pressure = sig.get("buy_pressure")          or 0
            narrative    = bool(sig.get("narrative", False))

            score_band = (
                "70+" if score >= 70 else
                "50-69" if score >= 50 else
                "<50"
            )

            # Positive reasons
            reasons: list = [f"score {score}"]
            if buy_pressure and buy_pressure > 60:
                reasons.append(f"buy pressure {buy_pressure:.0f}%")
            if narrative:
                reasons.append("narrative match")
            if holder_q == "CLEAN":
                reasons.append("clean holder dist")
            if entry in ("PULLBACK", "FIRST_PULLBACK", "TREND_CONTINUATION"):
                reasons.append(f"entry: {entry.lower().replace('_', ' ')}")

            # Blockers
            blockers: list = []
            if holder_q in ("BLOCKED", "RISKY"):
                blockers.append(f"holder: {holder_q.lower()}")
            if rug in ("WARN", "DANGER", "RUGGED"):
                blockers.append(f"rug: {rug.lower()}")
            if entry in ("DISTRIBUTION_RISK", "DEAD_OR_CHOP"):
                blockers.append(f"context: {entry.lower().replace('_', ' ')}")
            if top_h >= max_top_holder:
                blockers.append(f"top holder {top_h:.1f}% (max {max_top_holder:.1f}%)")
            if bundle == "HIGH":
                blockers.append("bundle risk: HIGH")

            # Hard block — any single disqualifying flag
            hard_blocked = (
                holder_q == "BLOCKED"
                or rug in ("DANGER", "RUGGED")
                or entry in ("DISTRIBUTION_RISK", "DEAD_OR_CHOP")
                or bundle == "HIGH"
            )

            row_base = dict(
                symbol               = sig.get("symbol"),
                score                = score,
                score_band           = score_band,
                holder_quality_level = holder_q,
                entry_context        = entry,
                top_holder_pct       = top_h,
                rug_label            = rug,
                is_actionable        = score >= alert_th,
                reasons              = reasons,
                blockers             = blockers,
            )

            if hard_blocked:
                # Patch 234 — identify the specific hard-stop flag(s) for operator clarity
                _hb: list = []
                if holder_q == "BLOCKED":
                    _hb.append("holder distribution: BLOCKED")
                if rug in ("DANGER", "RUGGED"):
                    _hb.append(f"rug risk: {rug.lower()}")
                if entry in ("DISTRIBUTION_RISK", "DEAD_OR_CHOP"):
                    _hb.append(f"entry context: {entry.lower().replace('_', ' ')}")
                if bundle == "HIGH":
                    _hb.append("bundle risk: HIGH")
                blocked_setups.append({
                    **row_base,
                    "primary_constraint":     _hb[0] if _hb else "hard block",
                    "downgrade_reasons":      _hb,
                    "missing_for_next_grade": [],
                })
            else:
                # Soft warning count for grade — track which warnings fired (Patch 234)
                warnings = 0
                _warn_reasons: list = []
                if holder_q == "RISKY":
                    warnings += 1
                    _warn_reasons.append("holder quality: risky")
                if rug == "WARN":
                    warnings += 1
                    _warn_reasons.append("rug: warn")
                if top_h >= max_top_holder:
                    warnings += 1
                    _warn_reasons.append(f"top holder {top_h:.1f}% above preferred")
                if entry in ("EXTENDED", "UNKNOWN"):
                    warnings += 1
                    _warn_reasons.append(f"entry context: {entry.lower()}")

                if score >= alert_th and warnings == 0:
                    grade = "A"
                elif score >= 50 and warnings <= 1:
                    grade = "B"
                else:
                    grade = "C"

                # Patch 234 — derive gap analysis from already-computed variables
                _dg: list = []   # downgrade_reasons
                _mf: list = []   # missing_for_next_grade
                if grade == "B":
                    if score < alert_th:
                        _dg.append(f"score {score} below A threshold ({alert_th})")
                        _mf.append(f"raise score by {alert_th - score}")
                    if warnings > 0:
                        _dg.extend(_warn_reasons)
                        _mf.append("clear soft warning")
                elif grade == "C":
                    if score < 50:
                        _dg.append(f"score {score} below B threshold (50)")
                        _mf.append(f"raise score by {50 - score}")
                    if warnings > 1:
                        _dg.extend(_warn_reasons)
                        _mf.append("reduce to ≤1 soft warning")

                best_setups.append({
                    **row_base,
                    "setup_grade":           grade,
                    "primary_constraint":    _dg[0] if _dg else "",
                    "downgrade_reasons":     _dg,
                    "missing_for_next_grade": _mf,
                })

        # Sort: A->B->C then score desc; blocked by score desc
        _g = {"A": 0, "B": 1, "C": 2}
        best_setups.sort(key=lambda x: (_g.get(x["setup_grade"], 3), -x["score"]))
        blocked_setups.sort(key=lambda x: -x["score"])

        # ── Market posture sentence ─────────────────────────────────────────
        n_signals = len(signals)
        n_a = sum(1 for s in best_setups if s["setup_grade"] == "A")
        n_b = sum(1 for s in best_setups if s["setup_grade"] == "B")

        fg_part = (f"F&G {fg_value} ({fg_label})" if fg_value is not None
                   else "F&G unknown")

        if n_signals == 0:
            posture = f"{fg_part} · scanner quiet — no signals in cache"
        else:
            if n_a > 0:
                setup_part = f"{n_a} grade-A setup{'s' if n_a != 1 else ''}"
            elif n_b > 0:
                setup_part = f"{n_b} grade-B setup{'s' if n_b != 1 else ''}"
            elif best_setups:
                setup_part = f"{len(best_setups)} grade-C only"
            else:
                setup_part = "all setups blocked"
            posture = (
                f"{fg_part} · "
                f"{n_signals} signal{'s' if n_signals != 1 else ''} · "
                f"{setup_part}"
            )

        return {
            "best_setups":     best_setups[:3],
            "blocked_setups":  blocked_setups[:3],
            "market_posture":  posture,
            "fg_value":        fg_value,
            "fg_label":        fg_label,
            "fg_favorable":    fg_favorable,
            "n_signals":       n_signals,
            "n_grade_a":       n_a,
            "n_grade_b":       n_b,
            "alert_threshold": alert_th,
            "generated_at":    _dt.datetime.now(_dt.timezone.utc).isoformat(),
        }

    try:
        return await asyncio.to_thread(_run)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ── Patch 236: Setup Ladder Scorecard ─────────────────────────────────────────

@router.get("/setup-scorecard")
async def setup_scorecard_ep(_: str = Depends(get_current_user)):
    _memecoins_v2_retired("/api/memecoins/setup-scorecard")

    """
    Setup Ladder Scorecard — read-only feedback loop.

    Groups Decision Journal entries (source_surface='SETUP_LADDER') by setup_grade,
    and computes resolved counts, WR, and avg 24h return per grade.

    setup_grade is stored inside snapshot_json — extracted in Python.

    Status rules (based on total resolved entries):
      TOO_EARLY  — resolved < 5
      GROWING    — 5 ≤ resolved < 20
      REVIEWABLE — resolved ≥ 20
    """
    _ensure_engine_path()

    def _run() -> dict:
        import json as _json
        import datetime as _dt

        try:
            from utils.db import get_conn as _gc  # type: ignore
            with _gc() as conn:
                rows = conn.execute(
                    "SELECT snapshot_json, operator_decision, "
                    "       outcome_24h_pct, outcome_4h_pct, "
                    "       resolution_status, created_ts, symbol "
                    "FROM decision_journal "
                    "WHERE source_surface = 'SETUP_LADDER' "
                    "ORDER BY id"
                ).fetchall()
        except Exception as exc:
            return {"status": "TOO_EARLY", "total_entries": 0, "total_resolved": 0,
                    "grades": {}, "decisions": {}, "error": str(exc),
                    "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat()}

        # Parse grade from snapshot_json and bucket entries by grade
        grade_buckets: dict = {"A": [], "B": [], "C": []}
        decision_counts: dict = {"FOLLOWED": 0, "SKIPPED": 0, "OVERRIDDEN": 0}

        for row in rows:
            snap: dict = {}
            if row["snapshot_json"]:
                try:
                    snap = _json.loads(row["snapshot_json"])
                except Exception:
                    pass

            grade = snap.get("setup_grade") or "UNKNOWN"
            if grade in grade_buckets:
                grade_buckets[grade].append({
                    "decision":    row["operator_decision"],
                    "outcome_24h": row["outcome_24h_pct"],
                    "outcome_4h":  row["outcome_4h_pct"],
                    "resolved":    row["resolution_status"] == "RESOLVED",
                })

            dec = row["operator_decision"] or ""
            if dec in decision_counts:
                decision_counts[dec] += 1

        # Totals
        total_entries  = sum(len(v) for v in grade_buckets.values())
        total_resolved = sum(1 for v in grade_buckets.values() for e in v if e["resolved"])

        # Status badge
        if total_resolved < 5:
            status = "TOO_EARLY"
        elif total_resolved < 20:
            status = "GROWING"
        else:
            status = "REVIEWABLE"

        # Per-grade stats
        grade_summary: dict = {}
        for grade in ("A", "B", "C"):
            entries  = grade_buckets.get(grade, [])
            resolved = [e for e in entries if e["resolved"]]
            outcomes = [e["outcome_24h"] for e in resolved if e["outcome_24h"] is not None]
            wr   = round(sum(1 for x in outcomes if x > 0) / len(outcomes) * 100, 1) if outcomes else None
            avg  = round(sum(outcomes) / len(outcomes), 1) if outcomes else None
            grade_summary[grade] = {
                "total":    len(entries),
                "resolved": len(resolved),
                "wr_24h":   wr,
                "avg_24h":  avg,
            }

        return {
            "status":         status,
            "total_entries":  total_entries,
            "total_resolved": total_resolved,
            "grades":         grade_summary,
            "decisions":      decision_counts,
            "generated_at":   _dt.datetime.now(_dt.timezone.utc).isoformat(),
        }

    try:
        return await asyncio.to_thread(_run)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ── Patch 240 — Failure Taxonomy ─────────────────────────────────────────────

@router.get("/failure-taxonomy")
async def failure_taxonomy_ep(_: str = Depends(get_current_user)):
    _memecoins_v2_retired("/api/memecoins/failure-taxonomy")

    """Patch 240 — Concentration drivers from last-20 COMPLETE sample (read-only)."""
    _ensure_engine_path()

    def _run() -> dict:
        import datetime as _dt
        from collections import Counter as _Counter

        db = _db_path()
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT symbol, scanned_at, score, return_24h_pct, "
                "entry_context, holder_quality_level, rug_label "
                "FROM memecoin_signal_outcomes "
                "WHERE status='COMPLETE' AND return_24h_pct IS NOT NULL "
                "ORDER BY evaluated_24h_ts_utc DESC LIMIT 20"
            ).fetchall()
        finally:
            conn.close()

        now = _dt.datetime.now(_dt.timezone.utc).isoformat()

        if not rows:
            return {
                "status": "NO_DATA",
                "n": 0,
                "drivers": [],
                "summary": "No evaluated trades in sample.",
                "generated_at": now,
            }

        n = len(rows)
        alert_threshold = int(os.getenv("ALERT_THRESHOLD", "90"))
        drivers: list = []

        # TOKEN_CONCENTRATION
        token_counts = _Counter(r["symbol"] for r in rows if r["symbol"])
        if token_counts:
            top_token, top_n = token_counts.most_common(1)[0]
            pct = round(top_n / n * 100)
            sev = "HIGH" if pct >= 80 else "WARN" if pct >= 60 else None
            if sev:
                drivers.append({
                    "type": "TOKEN_CONCENTRATION",
                    "label": "Token concentration",
                    "value": f"{pct}%",
                    "severity": sev,
                    "reason": f"{top_token} is {pct}% of sample ({top_n}/{n})",
                })

        # DAY_CONCENTRATION
        day_counts = _Counter(str(r["scanned_at"])[:10] for r in rows)
        if day_counts:
            top_day, top_day_n = day_counts.most_common(1)[0]
            pct = round(top_day_n / n * 100)
            sev = "HIGH" if pct >= 80 else "WARN" if pct >= 60 else None
            if sev:
                drivers.append({
                    "type": "DAY_CONCENTRATION",
                    "label": "Day concentration",
                    "value": f"{pct}%",
                    "severity": sev,
                    "reason": f"{top_day} is {pct}% of sample ({top_day_n}/{n})",
                })

        # NON_ACTIONABLE_SAMPLE
        actionable = sum(1 for r in rows if float(r["score"] or 0) >= alert_threshold)
        non_act = n - actionable
        pct = round(non_act / n * 100)
        sev = "HIGH" if pct >= 80 else "WARN" if pct >= 50 else None
        if sev:
            drivers.append({
                "type": "NON_ACTIONABLE_SAMPLE",
                "label": "Non-actionable rows",
                "value": f"{non_act}/{n}",
                "severity": sev,
                "reason": f"{non_act}/{n} rows below alert threshold ({alert_threshold})",
            })

        # CONTEXT_CONCENTRATION
        ctx_counts = _Counter(r["entry_context"] for r in rows if r["entry_context"])
        if ctx_counts:
            top_ctx, top_ctx_n = ctx_counts.most_common(1)[0]
            pct = round(top_ctx_n / n * 100)
            sev = "HIGH" if pct >= 70 else "WARN" if pct >= 50 else None
            if sev:
                drivers.append({
                    "type": "CONTEXT_CONCENTRATION",
                    "label": "Context concentration",
                    "value": f"{pct}%",
                    "severity": sev,
                    "reason": f"{top_ctx} is {pct}% of context ({top_ctx_n}/{n})",
                })

        # HOLDER_QUALITY — flag if significant non-CLEAN
        hq_counts = _Counter(r["holder_quality_level"] for r in rows if r["holder_quality_level"])
        non_clean = sum(v for k, v in hq_counts.items() if k != "CLEAN")
        pct = round(non_clean / n * 100)
        sev = "HIGH" if pct >= 50 else "WARN" if pct >= 30 else None
        if sev:
            drivers.append({
                "type": "HOLDER_QUALITY",
                "label": "Non-clean holders",
                "value": f"{pct}%",
                "severity": sev,
                "reason": f"{non_clean}/{n} rows have non-CLEAN holder quality",
            })

        # RUG_LABEL — flag if significant non-GOOD
        rug_counts = _Counter(r["rug_label"] for r in rows if r["rug_label"])
        non_good = sum(v for k, v in rug_counts.items() if k != "GOOD")
        pct = round(non_good / n * 100)
        sev = "HIGH" if pct >= 50 else "WARN" if pct >= 30 else None
        if sev:
            drivers.append({
                "type": "RUG_LABEL",
                "label": "Non-good rug labels",
                "value": f"{pct}%",
                "severity": sev,
                "reason": f"{non_good}/{n} rows have non-GOOD rug label",
            })

        # Sort: HIGH first, WARN second
        drivers.sort(key=lambda d: 0 if d["severity"] == "HIGH" else 1)

        # Summary line
        high = [d for d in drivers if d["severity"] == "HIGH"]
        warn = [d for d in drivers if d["severity"] == "WARN"]
        if not drivers:
            summary = "No concentration issues detected — WR weak for other reasons."
        elif high:
            summary = "WR skewed by " + ", ".join(d["label"].lower() for d in high) + "."
        else:
            summary = "Moderate concentration in " + ", ".join(d["label"].lower() for d in warn) + "."

        return {
            "status": "OK",
            "n": n,
            "drivers": drivers,
            "summary": summary,
            "generated_at": now,
        }

    try:
        return await asyncio.to_thread(_run)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ── Memecoin Readiness Monitor ────────────────────────────────────────────────

@router.get("/readiness")
async def memecoins_readiness_ep(_: str = Depends(get_current_user)):
    _memecoins_v2_retired("/api/memecoins/readiness")

    """
    Focused operator readiness monitor for the memecoin lane. Read-only.

    Returns a structured snapshot of exactly why the lane is blocked (or ready),
    how far each gate is from clearing, and whether conditions are improving.

    state:
      BLOCKED — one or more hard gates fail
      CLOSE   — all hard gates pass OR WR within 15pp of threshold
      READY   — F&G passes AND WR gate passes (lane can trade)

    trend (based on first half vs second half of capped WR sample):
      IMPROVING — recent half WR > older half WR by >5pp
      WORSENING — recent half WR < older half WR by >5pp
      FLAT      — within 5pp either direction
      UNKNOWN   — sample too small to split meaningfully

    No gate thresholds are changed here. This is purely observational.
    """
    _ensure_engine_path()

    def _run() -> dict:
        import json as _json
        from collections import Counter as _Counter
        from datetime import datetime, timezone
        from utils.db import get_conn  # type: ignore

        now = datetime.now(timezone.utc).isoformat()

        # ── F&G gate ──────────────────────────────────────────────────────────
        FG_THRESHOLD = 25
        fg_value: int | None = None
        fg_pass  = False
        try:
            with get_conn() as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    "SELECT value FROM kv_store WHERE key='shared_fear_greed'"
                ).fetchone()
            if row:
                fg_data  = _json.loads(row["value"])
                fg_value = fg_data.get("value")
                fg_pass  = fg_value is not None and int(fg_value) > FG_THRESHOLD
        except Exception:
            pass

        # ── WR gate — Patch 237 capped sample (mirrors home.py) ──────────────
        CAP_N        = 20
        SYM_CAP      = 3
        WR_THRESHOLD = 55.0

        all_raw = []
        try:
            with get_conn() as conn:
                conn.row_factory = sqlite3.Row
                all_raw = conn.execute(
                    "SELECT return_24h_pct, symbol, score, scanned_at, evaluated_24h_ts_utc "
                    "FROM memecoin_signal_outcomes "
                    "WHERE status = 'COMPLETE' AND return_24h_pct IS NOT NULL "
                    "ORDER BY evaluated_24h_ts_utc DESC LIMIT 200"
                ).fetchall()
            all_raw = [dict(r) for r in all_raw]
        except Exception:
            pass

        # Raw first-20 concentration (audit trail — old gate style)
        raw20       = all_raw[:20]
        raw20_syms  = [r["symbol"] or "" for r in raw20]
        raw20_ctr   = _Counter(raw20_syms)
        raw_top     = raw20_ctr.most_common(1)
        raw_top_sym = raw_top[0][0] if raw_top else None
        raw_top_pct = round(raw_top[0][1] / len(raw20) * 100, 1) if raw20 else None

        # Build capped sample
        sym_counts: dict = {}
        capped: list = []
        for r in all_raw:
            sym = r["symbol"] or ""
            if sym_counts.get(sym, 0) < SYM_CAP:
                capped.append(r)
                sym_counts[sym] = sym_counts.get(sym, 0) + 1
            if len(capped) >= CAP_N:
                break

        n_wr   = len(capped)
        wr_pct: float | None = None
        wr_pass = False
        if n_wr >= 1:
            wins   = sum(1 for r in capped if float(r["return_24h_pct"]) > 0)
            wr_pct = round(wins / n_wr * 100, 1)
            wr_pass = n_wr >= 5 and wr_pct >= WR_THRESHOLD

        # Capped sample concentration
        cap_syms    = [r["symbol"] or "" for r in capped]
        cap_ctr     = _Counter(cap_syms)
        cap_top     = cap_ctr.most_common(1)
        cap_top_sym = cap_top[0][0] if cap_top else None
        cap_top_pct = round(cap_top[0][1] / n_wr * 100, 1) if n_wr > 0 else None
        cap_tok_v   = cap_top_pct or 0
        concentration = (
            "HIGHLY_CONCENTRATED" if cap_tok_v > 80 else
            "NARROW"              if cap_tok_v > 60 else
            "BROAD"
        )

        # Trend: split capped sample into recent half vs older half
        trend      = "UNKNOWN"
        trend_detail: str | None = None
        if n_wr >= 6:
            mid        = n_wr // 2
            recent_h   = capped[:mid]    # rows 0..mid-1 — most recently evaluated
            older_h    = capped[mid:]    # rows mid..n-1 — older evaluated
            r_wins     = sum(1 for r in recent_h if float(r["return_24h_pct"]) > 0)
            o_wins     = sum(1 for r in older_h  if float(r["return_24h_pct"]) > 0)
            r_wr       = round(r_wins / len(recent_h) * 100, 1)
            o_wr       = round(o_wins / len(older_h)  * 100, 1)
            diff       = r_wr - o_wr
            trend      = "IMPROVING" if diff > 5 else "WORSENING" if diff < -5 else "FLAT"
            trend_detail = f"recent {len(recent_h)} evals {r_wr:.0f}% vs older {len(older_h)} evals {o_wr:.0f}%"

        # Gap to threshold
        wr_gap: float | None = None
        if wr_pct is not None:
            wr_gap = round(WR_THRESHOLD - wr_pct, 1)  # positive = need this many more pp

        fg_gap: int | None = None
        if fg_value is not None:
            fg_gap = FG_THRESHOLD - int(fg_value)  # positive = still this far below threshold

        # Overall state
        if fg_pass and wr_pass:
            state = "READY"
        elif fg_pass and wr_pct is not None and wr_pct >= (WR_THRESHOLD - 15):
            state = "CLOSE"
        else:
            state = "BLOCKED"

        # Blocker summary
        blockers: list[str] = []
        if not fg_pass:
            fgv = str(int(fg_value)) if fg_value is not None else "?"
            blockers.append(
                f"F&G {fgv} must rise above {FG_THRESHOLD} — "
                f"currently {fg_gap} points below threshold"
            )
        if not wr_pass:
            wrv = f"{wr_pct:.1f}%" if wr_pct is not None else "?"
            n_info = f"n={n_wr}" if n_wr > 0 else "no data"
            blockers.append(
                f"WR gate {wrv} must reach {WR_THRESHOLD:.0f}% ({n_info}) — "
                f"{wr_gap:.1f}pp gap; {trend.lower()} ({trend_detail or '—'})"
            )

        return {
            "state":      state,
            "fg": {
                "value":      int(fg_value) if fg_value is not None else None,
                "threshold":  FG_THRESHOLD,
                "gap":        fg_gap,
                "pass":       fg_pass,
            },
            "wr": {
                "pct":        wr_pct,
                "threshold":  WR_THRESHOLD,
                "gap":        wr_gap,
                "n":          n_wr,
                "pass":       wr_pass,
                "cap_n":      CAP_N,
                "sym_cap":    SYM_CAP,
            },
            "concentration": {
                "raw_top_pct":  raw_top_pct,
                "raw_top_sym":  raw_top_sym,
                "cap_top_pct":  cap_top_pct,
                "cap_top_sym":  cap_top_sym,
                "status":       concentration,
            },
            "trend": {
                "direction": trend,
                "detail":    trend_detail,
            },
            "blockers":     blockers,
            "generated_at": now,
        }

    try:
        return await asyncio.to_thread(_run)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


def get_proof_stack_summary_data(limit: int = 25) -> dict:
    """Compact backend summary of proof-ready memecoin build progress."""
    _ensure_engine_path()
    from utils.memecoin_manager import get_memecoin_mode, get_proof_candidate_snapshot  # type: ignore

    snapshot = get_proof_candidate_snapshot(limit=max(int(limit), 25))
    proof_ready = [
        c for c in list(snapshot.get("candidates") or [])
        if str(c.get("proof_status") or "") == "PROOF_READY"
    ]

    def _stats(rows: list[dict], field: str) -> dict:
        vals = [float(r.get(field) or 0.0) for r in rows if r.get(field) is not None]
        if not vals:
            return {"complete": 0, "wr": None, "avg_return": None}
        complete = len(vals)
        wins = sum(1 for v in vals if v > 0)
        return {
            "complete": complete,
            "wr": round(wins / complete * 100.0, 1),
            "avg_return": round(sum(vals) / complete, 2),
        }

    def _parse_json(text: object) -> dict:
        if not text:
            return {}
        try:
            parsed = json.loads(str(text))
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}

    with sqlite3.connect(_db_path()) as conn:
        conn.row_factory = sqlite3.Row

        trade_rows = conn.execute(
            """
            SELECT id, opened_ts_utc, closed_ts_utc, symbol, mint, status,
                   is_pilot, amount_usd, initial_amount_usd, realized_release_usd, realized_pnl_usd,
                   entry_score, proof_status, proof_reason,
                   proof_score, proof_snapshot_json,
                   (
                     SELECT COALESCE(m.scanner_regime, 'NORMAL')
                     FROM memecoin_signal_outcomes m
                     WHERE m.mint = memecoin_trades.mint
                       AND m.source = 'SCANNER'
                       AND m.scanned_at <= memecoin_trades.opened_ts_utc
                     ORDER BY m.scanned_at DESC
                     LIMIT 1
                   ) AS scanner_regime,
                   (
                     SELECT m.scanner_relaxation_reason
                     FROM memecoin_signal_outcomes m
                     WHERE m.mint = memecoin_trades.mint
                       AND m.source = 'SCANNER'
                       AND m.scanned_at <= memecoin_trades.opened_ts_utc
                     ORDER BY m.scanned_at DESC
                     LIMIT 1
                   ) AS scanner_relaxation_reason,
                   (
                     SELECT m.trust_label
                     FROM memecoin_signal_outcomes m
                     WHERE m.mint = memecoin_trades.mint
                       AND m.source = 'SCANNER'
                       AND m.scanned_at <= memecoin_trades.opened_ts_utc
                     ORDER BY m.scanned_at DESC
                     LIMIT 1
                   ) AS trust_label,
                   (
                     SELECT m.triage_state
                     FROM memecoin_signal_outcomes m
                     WHERE m.mint = memecoin_trades.mint
                       AND m.source = 'SCANNER'
                       AND m.scanned_at <= memecoin_trades.opened_ts_utc
                     ORDER BY m.scanned_at DESC
                     LIMIT 1
                   ) AS triage_state,
                   (
                     SELECT m.scanned_at
                     FROM memecoin_signal_outcomes m
                     WHERE m.mint = memecoin_trades.mint
                       AND m.source = 'SCANNER'
                       AND m.scanned_at <= memecoin_trades.opened_ts_utc
                     ORDER BY m.scanned_at DESC
                     LIMIT 1
                   ) AS source_scanned_at,
                   (
                     SELECT m.return_4h_pct
                     FROM memecoin_signal_outcomes m
                     WHERE m.mint = memecoin_trades.mint
                       AND m.source = 'SCANNER'
                       AND m.scanned_at <= memecoin_trades.opened_ts_utc
                     ORDER BY m.scanned_at DESC
                     LIMIT 1
                   ) AS source_return_4h_pct,
                   (
                     SELECT m.return_24h_pct
                     FROM memecoin_signal_outcomes m
                     WHERE m.mint = memecoin_trades.mint
                       AND m.source = 'SCANNER'
                       AND m.scanned_at <= memecoin_trades.opened_ts_utc
                     ORDER BY m.scanned_at DESC
                     LIMIT 1
                   ) AS source_return_24h_pct
            FROM memecoin_trades
            WHERE is_proof_build = 1
            ORDER BY opened_ts_utc DESC
            """
        ).fetchall()

        outcome_rows = conn.execute(
            """
            SELECT id, scanned_at, symbol, mint, source, status,
                   COALESCE(scanner_regime, 'NORMAL') AS scanner_regime,
                   scanner_relaxation_reason,
                   return_4h_pct, return_24h_pct, proof_reason,
                   proof_score, proof_snapshot_json
            FROM memecoin_signal_outcomes
            WHERE is_proof_build = 1
            ORDER BY scanned_at DESC
            """
        ).fetchall()

        try:
            surfaced_total = conn.execute(
                """
                SELECT COUNT(*) FROM research_surface_log
                WHERE source='PROOF_STACK' AND proof_candidate=1
                """
            ).fetchone()[0] or 0
            surfaced_24h = conn.execute(
                """
                SELECT COUNT(*) FROM research_surface_log
                WHERE source='PROOF_STACK' AND proof_candidate=1
                  AND surfaced_at >= datetime('now','-24 hours')
                """
            ).fetchone()[0] or 0
        except Exception:
            surfaced_total = 0
            surfaced_24h = 0

    outcome_payload = []
    trade_payload = []
    source_breakdown: dict[str, dict] = {}
    regime_breakdown: dict[str, dict] = {}
    trade_regime_breakdown: dict[str, dict] = {}
    with_support: list[dict] = []
    without_support: list[dict] = []
    open_trade_rows: list[dict] = []
    closed_trade_rows: list[dict] = []
    for row in trade_rows:
        parsed = _parse_json(row["proof_snapshot_json"])
        trade_item = {
            "id": int(row["id"]),
            "opened_ts_utc": str(row["opened_ts_utc"] or ""),
            "closed_ts_utc": str(row["closed_ts_utc"] or ""),
            "symbol": str(row["symbol"] or ""),
            "mint": str(row["mint"] or ""),
            "status": str(row["status"] or ""),
            "amount_usd": float(row["amount_usd"] or 0.0),
            "initial_amount_usd": float(row["initial_amount_usd"] or row["amount_usd"] or 0.0),
            "realized_release_usd": float(row["realized_release_usd"] or 0.0),
            "realized_pnl_usd": float(row["realized_pnl_usd"] or 0.0),
            "entry_score": float(row["entry_score"] or 0.0),
            "proof_status": str(row["proof_status"] or ""),
            "proof_reason": str(row["proof_reason"] or ""),
            "proof_score": float(row["proof_score"] or 0.0),
            "scanner_regime": str(row["scanner_regime"] or "NORMAL"),
            "scanner_relaxation_reason": str(row["scanner_relaxation_reason"] or ""),
            "trust_label": str(row["trust_label"] or ""),
            "triage_state": str(row["triage_state"] or ""),
            "source_scanned_at": str(row["source_scanned_at"] or ""),
            "source_return_4h_pct": row["source_return_4h_pct"],
            "source_return_24h_pct": row["source_return_24h_pct"],
            "support_tags": list(((parsed.get("reinforcement") or {}).get("support_tags") or [])),
            "support_details": list(((parsed.get("reinforcement") or {}).get("support_details") or [])),
        }
        trade_payload.append(trade_item)
        regime = trade_item["scanner_regime"]
        trade_regime_breakdown.setdefault(
            regime,
            {"scanner_regime": regime, "count": 0, "open": 0, "closed": 0, "relaxation_reasons": {}},
        )
        trade_regime_breakdown[regime]["count"] += 1
        if trade_item["status"] == "OPEN":
            trade_regime_breakdown[regime]["open"] += 1
            open_trade_rows.append(trade_item)
        elif trade_item["status"] == "CLOSED":
            trade_regime_breakdown[regime]["closed"] += 1
            closed_trade_rows.append(trade_item)
        if trade_item["scanner_relaxation_reason"]:
            rr = trade_item["scanner_relaxation_reason"]
            trade_regime_breakdown[regime]["relaxation_reasons"][rr] = int(
                trade_regime_breakdown[regime]["relaxation_reasons"].get(rr) or 0
            ) + 1

    for row in outcome_rows:
        parsed = _parse_json(row["proof_snapshot_json"])
        reinforcement = parsed.get("reinforcement") or {}
        support_tags = list(reinforcement.get("support_tags") or [])
        has_support = len(support_tags) > 0 or float(reinforcement.get("support_score") or 0.0) > 0
        item = {
            "id": int(row["id"]),
            "symbol": str(row["symbol"] or ""),
            "mint": str(row["mint"] or ""),
            "source": str(row["source"] or "UNKNOWN"),
            "status": str(row["status"] or ""),
            "scanner_regime": str(row["scanner_regime"] or "NORMAL"),
            "scanner_relaxation_reason": str(row["scanner_relaxation_reason"] or ""),
            "return_4h_pct": row["return_4h_pct"],
            "return_24h_pct": row["return_24h_pct"],
            "proof_reason": str(row["proof_reason"] or ""),
            "proof_score": float(row["proof_score"] or 0.0),
            "support_tags": support_tags,
            "support_details": list(reinforcement.get("support_details") or []),
            "has_support": has_support,
        }
        outcome_payload.append(item)
        bucket = with_support if has_support else without_support
        bucket.append(item)
        src = item["source"]
        source_breakdown.setdefault(src, {"source": src, "count": 0, "complete_24h": 0})
        source_breakdown[src]["count"] += 1
        if item["return_24h_pct"] is not None:
            source_breakdown[src]["complete_24h"] += 1
        regime = item["scanner_regime"]
        regime_breakdown.setdefault(
            regime,
            {"scanner_regime": regime, "count": 0, "complete_24h": 0, "relaxation_reasons": {}},
        )
        regime_breakdown[regime]["count"] += 1
        if item["return_24h_pct"] is not None:
            regime_breakdown[regime]["complete_24h"] += 1
        if item["scanner_relaxation_reason"]:
            rr = item["scanner_relaxation_reason"]
            regime_breakdown[regime]["relaxation_reasons"][rr] = int(
                regime_breakdown[regime]["relaxation_reasons"].get(rr) or 0
            ) + 1

    mode = str(get_memecoin_mode() or "PAPER")
    blockers = list(snapshot.get("blocker_counts") or [])
    return {
        "mode": mode,
        "proof_ready_now": int(snapshot.get("proof_ready_count") or 0),
        "proof_ready_candidates": proof_ready[: min(max(int(limit), 1), 10)],
        "current_blockers": blockers[:10],
        "proof_candidates_surfaced": {
            "total": int(surfaced_total),
            "recent_24h": int(surfaced_24h),
        },
        "proof_build_trades": {
            "total": len(trade_rows),
            "open": sum(1 for r in trade_rows if str(r["status"] or "") == "OPEN"),
            "closed": sum(1 for r in trade_rows if str(r["status"] or "") == "CLOSED"),
            "pilot": sum(1 for r in trade_rows if int(r["is_pilot"] or 0) == 1),
            "by_scanner_regime": list(trade_regime_breakdown.values()),
        },
        "proof_build_outcomes": {
            "count": len(outcome_payload),
            "by_source": list(source_breakdown.values()),
            "by_scanner_regime": list(regime_breakdown.values()),
            "return_4h": _stats(outcome_payload, "return_4h_pct"),
            "return_24h": _stats(outcome_payload, "return_24h_pct"),
            "reinforced": {
                "count": len(with_support),
                "return_4h": _stats(with_support, "return_4h_pct"),
                "return_24h": _stats(with_support, "return_24h_pct"),
            },
            "unreinforced": {
                "count": len(without_support),
                "return_4h": _stats(without_support, "return_4h_pct"),
                "return_24h": _stats(without_support, "return_24h_pct"),
            },
        },
        "proof_review": {
            "open_trades": open_trade_rows[: min(max(int(limit), 1), 10)],
            "recent_trades": trade_payload[: min(max(int(limit), 1), 10)],
            "recent_outcomes": outcome_payload[: min(max(int(limit), 1), 10)],
            "scanner_regime_review": {
                "trade_counts": list(trade_regime_breakdown.values()),
                "outcome_counts": list(regime_breakdown.values()),
            },
        },
        "thresholds": snapshot.get("thresholds") or {},
        "generated_at": snapshot.get("generated_at"),
    }


def get_memecoin_funnel_diagnostics_data(limit: int = 10) -> dict:
    """Compact diagnostics for discovery -> scanner -> proof funnel health."""
    _ensure_engine_path()
    from utils.memecoin_scanner import _load_thresholds  # type: ignore

    proof = get_proof_stack_summary_data(limit=max(int(limit), 10))
    scanner_thresholds = {}
    try:
        scanner_thresholds = _load_thresholds()
    except Exception:
        scanner_thresholds = {}

    def _status_count(conn: sqlite3.Connection, hours: int) -> list[dict]:
        rows = conn.execute(
            """
            SELECT source, status, COUNT(*) AS n
            FROM memecoin_signal_outcomes
            WHERE scanned_at >= datetime('now', ?)
            GROUP BY source, status
            ORDER BY source, status
            """,
            (f"-{int(hours)} hours",),
        ).fetchall()
        return [
            {
                "source": str(r["source"] or "UNKNOWN"),
                "status": str(r["status"] or "UNKNOWN"),
                "count": int(r["n"] or 0),
            }
            for r in rows
        ]

    with sqlite3.connect(_db_path()) as conn:
        conn.row_factory = sqlite3.Row

        cache_row = conn.execute(
            "SELECT value FROM kv_store WHERE key='memecoin_scan_cache'"
        ).fetchone()
        warm_cache_row = conn.execute(
            "SELECT value FROM kv_store WHERE key='memecoin_scan_cache_last_nonempty'"
        ).fetchone()
        try:
            cache_signals = json.loads(cache_row["value"]) if cache_row and cache_row["value"] else []
        except Exception:
            cache_signals = []
        try:
            warm_cache_payload = json.loads(warm_cache_row["value"]) if warm_cache_row and warm_cache_row["value"] else {}
        except Exception:
            warm_cache_payload = {}
        warm_cache_signals = list(warm_cache_payload.get("signals") or [])
        warm_cache_saved_at = str(warm_cache_payload.get("saved_at") or "")

        recent_6h = _status_count(conn, 6)
        recent_24h = _status_count(conn, 24)
        recent_72h = _status_count(conn, 72)

        def _count(window_rows: list[dict], source: str, status: str | None = None) -> int:
            total = 0
            for row in window_rows:
                if row["source"] != source:
                    continue
                if status is not None and row["status"] != status:
                    continue
                total += int(row["count"])
            return total

        latest_discovery = conn.execute(
            """
            SELECT scanned_at, symbol
            FROM memecoin_signal_outcomes
            WHERE source='DISCOVERY'
            ORDER BY scanned_at DESC
            LIMIT 1
            """
        ).fetchone()
        latest_scanner = conn.execute(
            """
            SELECT scanned_at, symbol
            FROM memecoin_signal_outcomes
            WHERE source='SCANNER'
            ORDER BY scanned_at DESC
            LIMIT 1
            """
        ).fetchone()

    discovery_24h = _count(recent_24h, "DISCOVERY")
    scanner_24h = _count(recent_24h, "SCANNER")
    scanner_72h = _count(recent_72h, "SCANNER")
    scanner_pending_24h = _count(recent_24h, "SCANNER", "PENDING")
    proof_ready_now = int(proof.get("proof_ready_now") or 0)
    proof_blockers = list(proof.get("current_blockers") or [])
    cache_normal = sum(1 for s in cache_signals if str(s.get("scanner_regime") or "NORMAL") == "NORMAL")
    cache_relaxed = sum(1 for s in cache_signals if str(s.get("scanner_regime") or "") == "RELAXED_NEAR_MISS")
    warm_cache_is_fresh = False
    if warm_cache_saved_at:
        try:
            _ts = datetime.fromisoformat(warm_cache_saved_at.replace("Z", "+00:00"))
            if _ts.tzinfo is None:
                _ts = _ts.replace(tzinfo=timezone.utc)
            warm_cache_age_h = (datetime.now(timezone.utc) - _ts.astimezone(timezone.utc)).total_seconds() / 3600.0
            warm_cache_is_fresh = warm_cache_age_h <= 6.0
        except Exception:
            warm_cache_is_fresh = False

    if len(cache_signals) == 0 and discovery_24h > 0 and scanner_24h == 0:
        funnel_status = "DISCOVERY_ONLY"
        funnel_reason = "Discovery ingress is live, but scanner-confirmed candidates have not reached the live cache in the last 24h."
    elif len(cache_signals) == 0 and warm_cache_is_fresh and len(warm_cache_signals) > 0:
        funnel_status = "SCANNER_DROUGHT"
        funnel_reason = (
            f"Live scan-cache is empty, but proof can still lean on the last non-empty cache snapshot "
            f"({len(warm_cache_signals)} row(s), saved {warm_cache_saved_at})."
        )
    elif len(cache_signals) == 0 and scanner_pending_24h > 0:
        funnel_status = "SCANNER_DROUGHT"
        funnel_reason = (
            f"Live scan-cache is empty, but {scanner_pending_24h} recent pending scanner row(s) still exist in the last 24h."
        )
    elif len(cache_signals) == 0 and scanner_72h > 0:
        funnel_status = "SCANNER_DROUGHT"
        funnel_reason = "The lane has some recent scanner history, but no current live scan-cache candidates."
    elif len(cache_signals) == 0:
        funnel_status = "NO_CURRENT_CANDIDATES"
        funnel_reason = "Neither the live cache nor recent scanner rows are producing actionable candidates right now."
    elif proof_ready_now == 0:
        funnel_status = "PROOF_FILTERED"
        top = proof_blockers[0]["key"] if proof_blockers else "no dominant blocker"
        funnel_reason = f"Scanner cache has candidates, but proof gating is still filtering them out ({top})."
    else:
        funnel_status = "FLOWING"
        funnel_reason = "Scanner cache and proof stack are both producing current candidates."

    return {
        "generated_at": proof.get("generated_at"),
        "status": funnel_status,
        "reason": funnel_reason,
        "scanner_thresholds": scanner_thresholds,
        "proof_thresholds": proof.get("thresholds") or {},
        "current_cache": {
            "count": len(cache_signals),
            "normal_count": cache_normal,
            "relaxed_count": cache_relaxed,
            "symbols": [str(s.get("symbol") or "") for s in cache_signals[: min(max(int(limit), 1), 10)]],
        },
        "warm_cache": {
            "count": len(warm_cache_signals) if warm_cache_is_fresh else 0,
            "saved_at": warm_cache_saved_at if warm_cache_is_fresh else None,
            "symbols": [str(s.get("symbol") or "") for s in warm_cache_signals[: min(max(int(limit), 1), 10)]] if warm_cache_is_fresh else [],
        },
        "recent_activity": {
            "last_6h": recent_6h,
            "last_24h": recent_24h,
            "last_72h": recent_72h,
            "latest_discovery": dict(latest_discovery) if latest_discovery else None,
            "latest_scanner": dict(latest_scanner) if latest_scanner else None,
        },
        "stage_counts": {
            "discovery_24h": discovery_24h,
            "scanner_24h": scanner_24h,
            "scanner_72h": scanner_72h,
            "cache_now": len(cache_signals),
            "proof_ready_now": proof_ready_now,
            "proof_build_trades_total": int((proof.get("proof_build_trades") or {}).get("total") or 0),
            "proof_build_outcomes_total": int((proof.get("proof_build_outcomes") or {}).get("count") or 0),
        },
        "proof_blockers": proof_blockers[:10],
    }


def get_proof_pipeline_summary_data(limit: int = 10) -> dict:
    """Explain how recent scanner candidates are progressing toward proof trades."""
    _ensure_engine_path()
    from utils.scanner_labels import resolve_scanner_labels  # type: ignore

    try:
        from dashboard.backend.routers.confluence import _get_reinforcement_summary  # type: ignore
    except Exception:
        try:
            from routers.confluence import _get_reinforcement_summary  # type: ignore
        except Exception:
            _get_reinforcement_summary = None  # type: ignore
    try:
        from utils.memecoin_manager import get_proof_candidate_snapshot  # type: ignore
    except Exception:
        get_proof_candidate_snapshot = None  # type: ignore

    proof = get_proof_stack_summary_data(limit=max(int(limit), 10))
    funnel = get_memecoin_funnel_diagnostics_data(limit=max(int(limit), 10))
    proof_snapshot = (
        get_proof_candidate_snapshot(limit=max(int(limit), 10), include_recent_complete=True)
        if get_proof_candidate_snapshot is not None
        else {"candidates": [], "proof_input_source": "UNAVAILABLE"}
    )

    with sqlite3.connect(_db_path()) as conn:
        conn.row_factory = sqlite3.Row

        reinforcement = (
            _get_reinforcement_summary(conn, limit=max(int(limit), 10))
            if _get_reinforcement_summary is not None
            else {"top_candidates": [], "level_counts": {}, "status": "UNAVAILABLE", "detail": "reinforcement unavailable"}
        )

        scanner_rows = conn.execute(
            """
            WITH recent_scanner AS (
                SELECT
                    id, scanned_at, symbol, mint, status, score,
                    trust_label, triage_state, proof_reason, proof_score,
                    COALESCE(scanner_regime, 'NORMAL') AS scanner_regime,
                    scanner_relaxation_reason,
                    return_4h_pct, return_24h_pct,
                    ROW_NUMBER() OVER (
                        PARTITION BY COALESCE(NULLIF(mint, ''), symbol)
                        ORDER BY scanned_at DESC, id DESC
                    ) AS rn
                FROM memecoin_signal_outcomes
                WHERE source='SCANNER'
                  AND status IN ('PENDING', 'COMPLETE')
                  AND scanned_at >= datetime('now', '-72 hours')
            )
            SELECT
                id, scanned_at, symbol, mint, status, score,
                trust_label, triage_state, proof_reason, proof_score,
                scanner_regime, scanner_relaxation_reason,
                return_4h_pct, return_24h_pct
            FROM recent_scanner
            WHERE rn = 1
            ORDER BY scanned_at DESC
            LIMIT ?
            """,
            (max(int(limit), 1),),
        ).fetchall()

        open_trade_rows = conn.execute(
            """
            SELECT
                id, symbol, mint, status, proof_status, proof_reason,
                proof_score, opened_ts_utc
            FROM memecoin_trades
            WHERE status='OPEN'
              AND is_proof_build=1
            ORDER BY opened_ts_utc DESC
            """
        ).fetchall()

        proof_candidates_by_mint = {
            str(c.get("mint") or ""): dict(c)
            for c in list(proof_snapshot.get("candidates") or [])
            if str(c.get("mint") or "")
        }
        proof_ready_candidates = {
            str(c.get("mint") or ""): dict(c)
            for c in list(proof_snapshot.get("candidates") or [])
            if str(c.get("mint") or "") and str(c.get("proof_status") or "") == "PROOF_READY"
        }
        if not proof_ready_candidates:
            proof_ready_candidates = {
            str(c.get("mint") or ""): dict(c)
            for c in list(proof.get("proof_ready_candidates") or [])
            if str(c.get("mint") or "")
        }
        reinforcement_by_mint = {
            str(c.get("mint") or ""): dict(c)
            for c in list(reinforcement.get("top_candidates") or [])
            if str(c.get("mint") or "")
        }
        open_trades_by_mint = {
            str(r["mint"] or ""): dict(r)
            for r in open_trade_rows
            if str(r["mint"] or "")
        }

        candidate_rows: list[dict] = []
        stage_counts = {
            "scanner_pending": 0,
            "reinforced_pending": 0,
            "proof_ready": 0,
            "in_proof_trade": 0,
        }
        blocker_counts: dict[str, int] = {}

        for row in scanner_rows:
            row_data = dict(row)
            row_status = str(row_data.get("status") or "PENDING").upper()
            row_data.update(resolve_scanner_labels(conn, row_data, persist=True))

            mint = str(row_data["mint"] or "")
            symbol = str(row_data["symbol"] or "")
            reinforcement_row = reinforcement_by_mint.get(mint) or {}
            proof_ready_row = proof_ready_candidates.get(mint) or {}
            proof_candidate_row = proof_candidates_by_mint.get(mint) or {}
            open_trade_row = open_trades_by_mint.get(mint) or {}

            blockers: list[str] = []
            stage = "SCANNER_PENDING"
            stage_note = "Scanner-confirmed candidate is waiting for stronger proof promotion."

            reinf_level = str(reinforcement_row.get("reinforcement_level") or "NONE").upper()
            reinf_reasons = list(reinforcement_row.get("reinforcement_reasons") or [])
            reinf_whale_kind = str((((reinforcement_row.get("support_breakdown") or {}).get("whale") or {}).get("kind") or "NONE")).upper()
            relaxed_exact_reinforcement = reinf_whale_kind == "EXACT_MINT"
            is_relaxed = str(row_data["scanner_regime"] or "NORMAL").upper() == "RELAXED_NEAR_MISS"

            if open_trade_row:
                stage = "IN_PROOF_TRADE"
                stage_note = "Candidate already has an open proof-build trade."
            elif proof_ready_row:
                stage = "PROOF_READY"
                stage_note = "Candidate has reached proof-ready status and is waiting for proof-build execution."
            elif reinf_level in ("LIGHT", "MODERATE", "STRONG") and (not is_relaxed or relaxed_exact_reinforcement):
                stage = "REINFORCED_PENDING"
                if row_status == "COMPLETE":
                    stage_note = "Recent scanner-complete candidate still carries reinforcement support, but has not promoted into proof yet."
                else:
                    stage_note = "Candidate has reinforcement support but has not promoted into proof yet."
            elif row_status == "COMPLETE":
                stage_note = "Recent scanner-complete candidate is being kept in observe-mode for proof review."
            else:
                blockers.append("no_reinforcement")

            if is_relaxed:
                blockers.append("relaxed_scanner_path")
                if reinf_level in ("LIGHT", "MODERATE", "STRONG") and not relaxed_exact_reinforcement:
                    blockers.append("relaxed_needs_exact_reinforcement")
            if not str(row_data["trust_label"] or "").strip():
                blockers.append("trust_unlabeled")
            if not str(row_data["triage_state"] or "").strip():
                blockers.append("triage_unlabeled")
            _proof_blocker = str(proof_candidate_row.get("blocker_key") or "").strip()
            if _proof_blocker:
                blockers.append(_proof_blocker)
            elif (
                str(funnel.get("status") or "").upper() == "SCANNER_DROUGHT"
                and str(proof_snapshot.get("proof_input_source") or "").upper() == "LIVE_CACHE"
            ):
                blockers.append("cache_empty")

            if stage == "SCANNER_PENDING":
                stage_counts["scanner_pending"] += 1
            elif stage == "REINFORCED_PENDING":
                stage_counts["reinforced_pending"] += 1
            elif stage == "PROOF_READY":
                stage_counts["proof_ready"] += 1
            elif stage == "IN_PROOF_TRADE":
                stage_counts["in_proof_trade"] += 1

            for blocker in blockers:
                blocker_counts[blocker] = int(blocker_counts.get(blocker) or 0) + 1

            candidate_rows.append({
                "id": int(row_data["id"]),
                "symbol": symbol,
                "mint": mint,
                "scanned_at": str(row_data["scanned_at"] or ""),
                "row_status": row_status,
                "score": float(row_data["score"] or 0.0),
                "scanner_regime": str(row_data["scanner_regime"] or "NORMAL"),
                "scanner_relaxation_reason": str(row_data["scanner_relaxation_reason"] or ""),
                "trust_label": str(row_data["trust_label"] or ""),
                "triage_state": str(row_data["triage_state"] or ""),
                "label_source": str(row_data.get("label_source") or "EXISTING"),
                "symbol_unambiguous": bool(row_data.get("symbol_unambiguous", False)),
                "proof_reason": str(proof_candidate_row.get("proof_reason") or row_data["proof_reason"] or ""),
                "proof_score": proof_candidate_row.get("proof_score") or row_data["proof_score"],
                "provisional_first_leg": int(proof_candidate_row.get("provisional_first_leg") or 0),
                "provisional_reason": str(proof_candidate_row.get("provisional_reason") or ""),
                "provisional_score": proof_candidate_row.get("provisional_score"),
                "return_4h_pct": row_data["return_4h_pct"],
                "return_24h_pct": row_data["return_24h_pct"],
                "stage": stage,
                "stage_note": stage_note,
                "promotion_blockers": blockers,
                "reinforcement_level": reinf_level,
                "reinforcement_score": float(reinforcement_row.get("reinforcement_score") or 0.0),
                "reinforcement_reasons": reinf_reasons[:4],
                "reinforcement_support_kind": reinf_whale_kind or "NONE",
                "reinforcement_support_breakdown": dict(reinforcement_row.get("support_breakdown") or {}),
                "reinforcement_next_level": reinforcement_row.get("reinforcement_next_level"),
                "reinforcement_points_to_next": reinforcement_row.get("reinforcement_points_to_next"),
                "reinforcement_thresholds": dict(reinforcement_row.get("reinforcement_thresholds") or {}),
                "reinforcement_missing_reasons": list(reinforcement_row.get("reinforcement_missing_reasons") or [])[:5],
                "open_proof_trade_id": open_trade_row.get("id"),
                "open_proof_status": open_trade_row.get("proof_status"),
            })

        try:
            conn.commit()
        except Exception:
            pass

    blocker_summary = [
        {"key": key, "count": blocker_counts[key]}
        for key in sorted(blocker_counts, key=lambda k: (-blocker_counts[k], k))
    ]

    return {
        "generated_at": proof.get("generated_at"),
        "status": "ACTIVE" if candidate_rows else "QUIET",
        "detail": (
            "Recent scanner candidates are being tracked through the proof pipeline."
            if candidate_rows else
            "No current scanner-pending memecoin candidates need proof-pipeline review."
        ),
        "stage_counts": {
            "scanner_pending": int(stage_counts["scanner_pending"]),
            "reinforced_pending": int(stage_counts["reinforced_pending"]),
            "proof_ready": int(stage_counts["proof_ready"]),
            "in_proof_trade": int(stage_counts["in_proof_trade"]),
            "open_proof_trades_total": int((proof.get("proof_build_trades") or {}).get("open") or 0),
            "proof_outcomes_total": int((proof.get("proof_build_outcomes") or {}).get("count") or 0),
        },
        "pipeline_health": {
            "funnel_status": funnel.get("status"),
            "funnel_reason": funnel.get("reason"),
            "proof_input_source": proof_snapshot.get("proof_input_source"),
            "reinforcement_status": reinforcement.get("status"),
            "reinforcement_detail": reinforcement.get("detail"),
            "reinforcement_thresholds": dict(reinforcement.get("thresholds") or {}),
            "reinforcement_summary": {
                "candidate_count": int(reinforcement.get("candidate_count") or 0),
                "level_counts": dict(reinforcement.get("level_counts") or {}),
                "exact_overlap_candidates": int(reinforcement.get("exact_overlap_candidates") or 0),
                "symbol_family_candidates": int(reinforcement.get("symbol_family_candidates") or 0),
                "relaxed_candidates": int(reinforcement.get("relaxed_candidates") or 0),
            },
        },
        "top_blockers": blocker_summary[:8],
        "candidates": candidate_rows[: max(int(limit), 1)],
    }


def _proof_slot_expansion_policy(
    *,
    proof_authority: str | None,
    normal_cohort: dict | None,
    reinforced_cohort: dict | None,
    relaxed_cohort: dict | None,
    identity_conflict_count: int = 0,
) -> dict:
    """Explicit gate model for when slot 2 should be recommended."""
    proof_auth = str(proof_authority or "TENTATIVE").upper()
    normal = dict(normal_cohort or {})
    reinforced = dict(reinforced_cohort or {})
    relaxed = dict(relaxed_cohort or {})

    normal_n = int(normal.get("sample_n") or 0)
    reinforced_n = int(reinforced.get("sample_n") or 0)
    relaxed_verdict = str(relaxed.get("verdict") or "TOO_THIN")

    gates = [
        {
            "key": "normal_cohort_promotive",
            "passed": str(normal.get("verdict") or "") == "PROMOTE" and normal_n >= 10,
            "note": "Normal cohort needs promotive evidence with at least 10 resolved outcomes.",
        },
        {
            "key": "proof_stack_backed",
            "passed": proof_auth in ("BACKED", "FORCEFUL"),
            "note": "Proof stack authority needs to move above tentative before widening slots.",
        },
        {
            "key": "reinforced_cohort_maturing",
            "passed": str(reinforced.get("verdict") or "") in ("HOLD", "PROMOTE") and reinforced_n >= 3,
            "note": "Reinforced cohort needs at least 3 resolved outcomes without degrading.",
        },
        {
            "key": "identity_clear",
            "passed": int(identity_conflict_count or 0) == 0,
            "note": "No current recycled-ticker / multi-mint identity conflicts in the active scanner cohort.",
        },
        {
            "key": "relaxed_not_driving_expansion",
            "passed": relaxed_verdict != "PROMOTE",
            "note": "Relaxed-path names should not be the reason slot 2 opens.",
        },
    ]

    unmet = [g for g in gates if not g["passed"]]
    if all(g["passed"] for g in gates):
        slot_authority = "EXPANDABLE"
        recommendation = "Recommend a controlled move to 2 proof slots."
    elif gates[0]["passed"] and (not gates[1]["passed"] or not gates[2]["passed"]):
        slot_authority = "ACCUMULATING"
        recommendation = "Normal cohort is earning trust, but proof/reinforced evidence is still too thin for slot 2."
    elif not gates[0]["passed"]:
        slot_authority = "HOLD"
        recommendation = "Do not widen slots until the normal cohort keeps earning trust."
    else:
        slot_authority = "HOLD"
        recommendation = "Hold at 1 slot while the remaining expansion gates clear."

    return {
        "slot_authority": slot_authority,
        "recommended_slots": 2 if slot_authority == "EXPANDABLE" else 1,
        "recommendation": recommendation,
        "gates": gates,
        "unmet_gate_keys": [g["key"] for g in unmet],
    }


def get_memecoin_graduation_data(limit: int = 8, lookback_days: int = 30) -> dict:
    """Cohort review and graduation recommendation for the memecoin lane."""
    _ensure_engine_path()

    from utils.memecoin_manager import get_memecoin_mode  # type: ignore

    try:
        from dashboard.backend.routers.confluence import _get_reinforcement_summary  # type: ignore
    except Exception:
        try:
            from routers.confluence import _get_reinforcement_summary  # type: ignore
        except Exception:
            _get_reinforcement_summary = None  # type: ignore

    proof = get_proof_stack_summary_data(limit=max(int(limit), 10))
    pipeline = get_proof_pipeline_summary_data(limit=max(int(limit), 10))

    def _stats(rows: list[dict]) -> dict:
        r4 = [float(r.get("return_4h_pct")) for r in rows if r.get("return_4h_pct") is not None]
        r24 = [float(r.get("return_24h_pct")) for r in rows if r.get("return_24h_pct") is not None]
        return {
            "sample_n": len(rows),
            "avg_return_4h": round(sum(r4) / len(r4), 2) if r4 else None,
            "avg_return_24h": round(sum(r24) / len(r24), 2) if r24 else None,
            "win_rate_4h": round(sum(1 for v in r4 if v > 0) / len(r4) * 100.0, 1) if r4 else None,
            "win_rate_24h": round(sum(1 for v in r24 if v > 0) / len(r24) * 100.0, 1) if r24 else None,
            "open_proof_trade_n": sum(1 for r in rows if str(r.get("status") or "").upper() == "OPEN"),
        }

    def _trend(rows: list[dict]) -> str:
        if len(rows) < 3:
            return "THIN"
        recent = [float(r.get("return_24h_pct")) for r in rows[:5] if r.get("return_24h_pct") is not None]
        older = [float(r.get("return_24h_pct")) for r in rows[5:] if r.get("return_24h_pct") is not None]
        if not recent or not older:
            return "THIN"
        delta = (sum(recent) / len(recent)) - (sum(older) / len(older))
        if delta >= 5.0:
            return "IMPROVING"
        if delta <= -5.0:
            return "WORSENING"
        return "FLAT"

    def _verdict(stats: dict, label: str) -> tuple[str, str]:
        n = int(stats.get("sample_n") or 0)
        avg24 = stats.get("avg_return_24h")
        wr24 = stats.get("win_rate_24h")
        if n < 3:
            return "TOO_THIN", f"{label} has only {n} resolved outcome(s)."
        if avg24 is not None and avg24 <= -10.0:
            return "TIGHTEN", f"{label} is averaging {avg24:+.1f}% at 24h."
        if wr24 is not None and wr24 < 35.0:
            return "TIGHTEN", f"{label} 24h win rate is only {wr24:.1f}%."
        if n >= 5 and avg24 is not None and avg24 >= 8.0 and wr24 is not None and wr24 >= 60.0:
            return "PROMOTE", f"{label} is earning trust with {wr24:.1f}% WR and {avg24:+.1f}% avg 24h return."
        return "HOLD", f"{label} is constructive but still building a larger sample."

    def _cohort_row(cohort_key: str, label: str, rows: list[dict], source_note: str) -> dict:
        stats = _stats(rows)
        verdict, reason = _verdict(stats, label)
        return {
            "cohort_key": cohort_key,
            "label": label,
            "sample_n": int(stats["sample_n"]),
            "proof_trade_n": sum(1 for r in rows if int(r.get("is_proof_build") or 0) == 1),
            "open_proof_trade_n": int(stats["open_proof_trade_n"]),
            "avg_return_4h": stats["avg_return_4h"],
            "avg_return_24h": stats["avg_return_24h"],
            "win_rate_4h": stats["win_rate_4h"],
            "win_rate_24h": stats["win_rate_24h"],
            "recent_trend": _trend(rows),
            "verdict": verdict,
            "reason": f"{reason} Source: {source_note}.",
        }

    identity_conflict_count = 0
    with sqlite3.connect(_db_path()) as conn:
        conn.row_factory = sqlite3.Row
        _ensure_memecoin_outcome_label_schema(conn)

        def _track_signal(payload: dict | None) -> str:
            payload = payload or {}
            verdict = str(payload.get("verdict") or "")
            if verdict == "EARNING_ITS_PLACE":
                return "PROMOTIVE"
            if verdict == "FALSIFIED":
                return "ADVERSE"
            bootstrap = (payload.get("validation_tracks") or {}).get("bootstrap_observed") or {}
            if bootstrap.get("gradient_present"):
                return "EARLY"
            return "ACCUMULATING"

        try:
            _tl = _validate_trust_labels(conn)
            _ts = _validate_triage_states(conn)
            _td = _validate_transition_detector(conn)
            validation_support = {
                "trust_labels": {
                    "signal": _track_signal(_tl),
                    "clean_n": int(_tl.get("clean_n") or 0),
                },
                "triage_states": {
                    "signal": _track_signal(_ts),
                    "clean_n": int(_ts.get("clean_n") or 0),
                },
                "transition_detector": {
                    "signal": (
                        "PROMOTIVE" if str(_td.get("verdict") or "") == "EARNING_ITS_PLACE"
                        else "ADVERSE" if str(_td.get("verdict") or "") == "FALSIFIED"
                        else "EARLY" if str(_td.get("directional_state") or "") == "THIN_POSITIVE_SIGNAL"
                        else "ACCUMULATING"
                    ),
                },
            }
        except Exception:
            validation_support = {
                "trust_labels": {"signal": "ACCUMULATING", "clean_n": 0},
                "triage_states": {"signal": "ACCUMULATING", "clean_n": 0},
                "transition_detector": {"signal": "ACCUMULATING"},
            }

        validation_support["proof_stack_authority"] = _proof_stack_authority(validation_support)

        scanner_complete_rows = [
            dict(r) for r in conn.execute(
                """
                SELECT scanned_at, symbol, mint, status, return_4h_pct, return_24h_pct,
                       is_proof_build, COALESCE(scanner_regime, 'NORMAL') AS scanner_regime
                FROM memecoin_signal_outcomes
                WHERE source='SCANNER'
                  AND status='COMPLETE'
                  AND scanned_at >= datetime('now', ?)
                ORDER BY scanned_at DESC
                """,
                (f"-{int(lookback_days)} days",),
            ).fetchall()
        ]
        proof_outcome_rows = [
            dict(r) for r in conn.execute(
                """
                SELECT scanned_at, symbol, mint, status, return_4h_pct, return_24h_pct,
                       is_proof_build, proof_snapshot_json,
                       COALESCE(scanner_regime, 'NORMAL') AS scanner_regime
                FROM memecoin_signal_outcomes
                WHERE is_proof_build=1
                  AND scanned_at >= datetime('now', ?)
                ORDER BY scanned_at DESC
                """,
                (f"-{int(lookback_days)} days",),
            ).fetchall()
        ]
        reinforcement = (
            _get_reinforcement_summary(conn, limit=max(int(limit), 12))
            if _get_reinforcement_summary is not None
            else {"status": "UNAVAILABLE", "detail": "reinforcement unavailable", "top_candidates": [], "level_counts": {}}
        )
        identity_conflict_count = int((
            conn.execute(
                """
                SELECT COUNT(*)
                FROM (
                    SELECT symbol
                    FROM memecoin_signal_outcomes
                    WHERE source='SCANNER'
                      AND mint IS NOT NULL AND mint != ''
                      AND scanned_at >= datetime('now', ?)
                    GROUP BY symbol
                    HAVING COUNT(DISTINCT mint) > 1
                )
                """,
                (f"-{int(lookback_days)} days",),
            ).fetchone()
            or [0]
        )[0])

    def _has_reinforcement(row: dict) -> bool:
        try:
            payload = json.loads(str(row.get("proof_snapshot_json") or "{}"))
            reinforcement_block = payload.get("reinforcement") or {}
            tags = list(reinforcement_block.get("support_tags") or [])
            score = float(reinforcement_block.get("support_score") or 0.0)
            return bool(tags) or score > 0.0
        except Exception:
            return False

    for row in proof_outcome_rows:
        row["has_reinforcement"] = _has_reinforcement(row)

    normal_rows = [r for r in scanner_complete_rows if str(r.get("scanner_regime") or "NORMAL").upper() == "NORMAL"]
    relaxed_rows = [r for r in scanner_complete_rows if str(r.get("scanner_regime") or "").upper() == "RELAXED_NEAR_MISS"]
    reinforced_rows = [r for r in proof_outcome_rows if bool(r.get("has_reinforcement"))]
    unreinforced_rows = [r for r in proof_outcome_rows if not bool(r.get("has_reinforcement"))]
    normal_reinforced_rows = [
        r for r in proof_outcome_rows
        if bool(r.get("has_reinforcement")) and str(r.get("scanner_regime") or "NORMAL").upper() == "NORMAL"
    ]
    relaxed_reinforced_rows = [
        r for r in proof_outcome_rows
        if bool(r.get("has_reinforcement")) and str(r.get("scanner_regime") or "").upper() == "RELAXED_NEAR_MISS"
    ]

    cohorts = [
        _cohort_row("NORMAL", "Normal Scanner", normal_rows, "scanner complete outcomes"),
        _cohort_row("RELAXED_NEAR_MISS", "Relaxed Near Miss", relaxed_rows, "scanner complete outcomes"),
        _cohort_row("REINFORCED", "Reinforced Proof", reinforced_rows, "proof-build outcomes with reinforcement"),
        _cohort_row("UNREINFORCED", "Standalone Proof", unreinforced_rows, "proof-build outcomes without reinforcement"),
        _cohort_row("NORMAL_REINFORCED", "Normal + Reinforced", normal_reinforced_rows, "proof-build reinforced normal cohort"),
        _cohort_row("RELAXED_REINFORCED", "Relaxed + Reinforced", relaxed_reinforced_rows, "proof-build reinforced relaxed cohort"),
    ]
    cohort_map = {row["cohort_key"]: row for row in cohorts}

    proof_auth = str(validation_support.get("proof_stack_authority") or "TENTATIVE")
    reinforcement_status = str(reinforcement.get("status") or "INACTIVE")
    if reinforcement_status == "STRONG_SIGNALING":
        reinforcement_authority = "FORCEFUL"
    elif reinforcement_status == "ACTIVE":
        reinforcement_authority = "BACKED"
    elif reinforcement_status == "LIGHT_ONLY":
        reinforcement_authority = "TENTATIVE"
    else:
        reinforcement_authority = "ABSENT"

    promote_keys = {row["cohort_key"] for row in cohorts if row["verdict"] == "PROMOTE"}
    tighten_keys = {row["cohort_key"] for row in cohorts if row["verdict"] == "TIGHTEN"}
    if "NORMAL_REINFORCED" in promote_keys or "REINFORCED" in promote_keys:
        promotion_authority = "PROMOTIVE"
    elif "NORMAL" in promote_keys or "RELAXED_NEAR_MISS" in promote_keys:
        promotion_authority = "SUPPORTED"
    elif len(tighten_keys) >= 2:
        promotion_authority = "TIGHTENED"
    else:
        promotion_authority = "ACCUMULATING"

    slot_policy = _proof_slot_expansion_policy(
        proof_authority=proof_auth,
        normal_cohort=cohort_map.get("NORMAL"),
        reinforced_cohort=cohort_map.get("NORMAL_REINFORCED") or cohort_map.get("REINFORCED"),
        relaxed_cohort=cohort_map.get("RELAXED_NEAR_MISS"),
        identity_conflict_count=identity_conflict_count,
    )
    total_slots = max(1, int(os.getenv("MEMECOIN_PROOF_SLOTS", "1")))
    used_slots = int((proof.get("proof_build_trades") or {}).get("open") or 0)
    recommended_slots = int(slot_policy.get("recommended_slots") or 1)

    mode = str(get_memecoin_mode() or "PAPER").upper()
    auto_buy = os.getenv("MEMECOIN_AUTO_BUY", "false").lower() == "true"
    if not auto_buy:
        deployment_authority = "BLOCKED"
        active_policy_posture = "AUTO_BUY_DISABLED"
    elif mode == "PAPER":
        deployment_authority = "BLOCKED"
        active_policy_posture = "PAPER_PROOF_ONLY"
    elif proof_auth in ("FORCEFUL", "BACKED") and promotion_authority in ("PROMOTIVE", "SUPPORTED"):
        deployment_authority = "LIMITED" if recommended_slots <= 1 else "ENABLED"
        active_policy_posture = "LIMITED_PROOF_DEPLOYMENT" if deployment_authority == "LIMITED" else "PROMOTIVE_DEPLOYMENT"
    else:
        deployment_authority = "BLOCKED"
        active_policy_posture = "PROOF_STILL_ACCUMULATING"

    if deployment_authority == "ENABLED":
        graduation_state = "PROMOTIVE"
    elif promotion_authority in ("PROMOTIVE", "SUPPORTED") or proof_auth in ("BACKED", "FORCEFUL"):
        graduation_state = "EARNING_TRUST"
    elif int((proof.get("proof_build_trades") or {}).get("total") or 0) > 0:
        graduation_state = "ACCUMULATING"
    else:
        graduation_state = "EXPERIMENTAL"

    policy_recommendation = {
        "proof_band_recommendation": (
            "Keep the 76–95 extension for NORMAL scanner names. That cohort is earning trust."
            if "NORMAL" in promote_keys else
            "Hold the current proof band. The normal cohort is still too thin or mixed."
        ),
        "relaxed_path_recommendation": (
            "Keep relaxed-path restrictions in place. Relaxed names are still mixed and should stay secondary."
            if cohort_map["RELAXED_NEAR_MISS"]["verdict"] != "PROMOTE" else
            "Relaxed-path names are improving enough to review a limited promotion experiment."
        ),
        "reinforced_path_recommendation": (
            "Prioritize reinforced normal names ahead of standalone candidates."
            if ("NORMAL_REINFORCED" in promote_keys or "REINFORCED" in promote_keys) else
            "Reinforcement is informative, but still too thin to unlock more policy on its own."
        ),
        "proof_slot_recommendation": str(slot_policy.get("recommendation") or "Keep proof slots unchanged."),
        "deployment_recommendation": (
            "Keep real deployment blocked. Continue earning evidence in paper/proof mode."
            if deployment_authority == "BLOCKED" else
            "Lane has earned limited deployment authority. Keep sizing disciplined."
        ),
    }

    recent_promotions = [
        {
            "symbol": str(c.get("symbol") or ""),
            "stage": str(c.get("stage") or ""),
            "note": str(c.get("stage_note") or ""),
        }
        for c in list(pipeline.get("candidates") or [])
        if str(c.get("stage") or "") in ("IN_PROOF_TRADE", "PROOF_READY")
    ][:5]
    recent_degradations = [
        {
            "symbol": str(c.get("symbol") or ""),
            "stage": str(c.get("stage") or ""),
            "blockers": list(c.get("promotion_blockers") or [])[:3],
        }
        for c in list(pipeline.get("candidates") or [])
        if list(c.get("promotion_blockers") or [])
    ][:5]

    top_constraints = [
        {"key": str(item.get("key") or ""), "count": int(item.get("count") or 0)}
        for item in list(pipeline.get("top_blockers") or [])[:8]
    ]
    if mode == "PAPER":
        top_constraints.append({"key": "paper_mode", "count": 1})
    if used_slots >= total_slots:
        top_constraints.append({"key": "proof_slots_full", "count": used_slots})

    status = "ACTIVE"
    detail = "Memecoin graduation review is active and tracking which cohorts are earning more freedom."
    if graduation_state == "PROMOTIVE":
        status = "PROMOTIVE"
        detail = "At least one memecoin cohort is earning stronger policy support."
    elif graduation_state == "ACCUMULATING":
        status = "ACCUMULATING"
        detail = "The proof lane is alive, but the cohorts are still too thin to promote broadly."
    elif graduation_state == "EXPERIMENTAL":
        status = "EXPERIMENTAL"
        detail = "The memecoin lane is still early. Most cohorts are accumulating evidence rather than earning promotion."

    return {
        "generated_at": proof.get("generated_at") or pipeline.get("generated_at"),
        "status": status,
        "detail": detail,
        "lane_state": {
            "proof_authority": proof_auth,
            "reinforcement_authority": reinforcement_authority,
            "promotion_authority": promotion_authority,
            "deployment_authority": deployment_authority,
            "graduation_state": graduation_state,
            "proof_input_source": ((pipeline.get("pipeline_health") or {}).get("proof_input_source")),
            "active_policy_posture": active_policy_posture,
        },
        "cohorts": cohorts,
        "policy_recommendation": policy_recommendation,
        "proof_slots": {
            "total_slots": total_slots,
            "used_slots": used_slots,
            "recommended_slots": recommended_slots,
            "slot_state": "FULL" if used_slots >= total_slots else "AVAILABLE",
            "slot_authority": slot_policy.get("slot_authority"),
            "slot_gates": list(slot_policy.get("gates") or []),
        },
        "recent_promotions": recent_promotions,
        "recent_degradations": recent_degradations,
        "top_constraints": top_constraints,
    }


def get_memecoin_proof_expansion_data(limit: int = 8, lookback_days: int = 30) -> dict:
    """Observe-mode proof slot planner: next-slot eligibility without changing execution."""
    _ensure_engine_path()

    proof = get_proof_stack_summary_data(limit=max(int(limit), 10))
    pipeline = get_proof_pipeline_summary_data(limit=max(int(limit), 10))
    graduation = get_memecoin_graduation_data(limit=max(int(limit), 8), lookback_days=max(int(lookback_days), 7))

    lane_state = dict(graduation.get("lane_state") or {})
    proof_slots = dict(graduation.get("proof_slots") or {})
    cohorts = {str(c.get("cohort_key") or ""): dict(c) for c in list(graduation.get("cohorts") or [])}
    current_trades = list(((proof.get("proof_review") or {}).get("open_trades") or []))
    pipeline_candidates = list(pipeline.get("candidates") or [])

    total_slots = int(proof_slots.get("total_slots") or 1)
    used_slots = int(proof_slots.get("used_slots") or 0)
    recommended_slots = int(proof_slots.get("recommended_slots") or 1)
    available_slots_now = max(0, total_slots - used_slots)
    recommended_openings = max(0, recommended_slots - used_slots)

    normal_verdict = str((cohorts.get("NORMAL") or {}).get("verdict") or "TOO_THIN")
    relaxed_verdict = str((cohorts.get("RELAXED_NEAR_MISS") or {}).get("verdict") or "TOO_THIN")
    reinforced_verdict = str((cohorts.get("NORMAL_REINFORCED") or cohorts.get("REINFORCED") or {}).get("verdict") or "TOO_THIN")

    def _cohort_bucket(candidate: dict) -> str:
        regime = str(candidate.get("scanner_regime") or "NORMAL").upper()
        reinforcement = str(candidate.get("reinforcement_level") or "NONE").upper()
        if regime == "NORMAL" and reinforcement in ("LIGHT", "MODERATE", "STRONG"):
            return "NORMAL_REINFORCED"
        if regime == "RELAXED_NEAR_MISS" and reinforcement in ("LIGHT", "MODERATE", "STRONG"):
            return "RELAXED_REINFORCED"
        if regime == "RELAXED_NEAR_MISS":
            return "RELAXED_NEAR_MISS"
        return "NORMAL"

    def _observe_slot_blockers(candidate: dict) -> list[str]:
        blockers = list(candidate.get("promotion_blockers") or [])
        bucket = _cohort_bucket(candidate)
        if bucket == "RELAXED_NEAR_MISS" and relaxed_verdict == "TIGHTEN":
            blockers.append("relaxed_cohort_tightened")
        if bucket == "RELAXED_REINFORCED" and relaxed_verdict == "TIGHTEN":
            blockers.append("relaxed_base_cohort_tightened")
        if bucket == "NORMAL" and normal_verdict not in ("PROMOTE", "HOLD"):
            blockers.append("normal_cohort_not_constructive")
        if bucket in ("NORMAL_REINFORCED", "RELAXED_REINFORCED") and reinforced_verdict == "TOO_THIN":
            blockers.append("reinforced_cohort_too_thin")
        if used_slots >= recommended_slots:
            blockers.append("recommended_slots_full")
        if str(lane_state.get("deployment_authority") or "BLOCKED").upper() == "BLOCKED":
            blockers.append("deployment_blocked")
        return list(dict.fromkeys(blockers))

    def _sort_key(candidate: dict) -> tuple:
        bucket = _cohort_bucket(candidate)
        stage = str(candidate.get("stage") or "SCANNER_PENDING")
        bucket_rank = {
            "NORMAL_REINFORCED": 0,
            "NORMAL": 1,
            "RELAXED_REINFORCED": 2,
            "RELAXED_NEAR_MISS": 3,
        }.get(bucket, 9)
        stage_rank = {
            "PROOF_READY": 0,
            "REINFORCED_PENDING": 1,
            "SCANNER_PENDING": 2,
            "IN_PROOF_TRADE": 3,
        }.get(stage, 9)
        return (
            bucket_rank,
            stage_rank,
            -float(candidate.get("proof_score") or 0.0),
            -float(candidate.get("score") or 0.0),
            str(candidate.get("symbol") or ""),
        )

    candidate_plan_rows: list[dict] = []
    for candidate in sorted(pipeline_candidates, key=_sort_key):
        if str(candidate.get("stage") or "") == "IN_PROOF_TRADE":
            continue
        bucket = _cohort_bucket(candidate)
        blockers = _observe_slot_blockers(candidate)
        slot_eligible = (
            str(candidate.get("stage") or "") == "PROOF_READY"
            and not blockers
            and available_slots_now > 0
            and recommended_openings > 0
        )
        candidate_plan_rows.append({
            "symbol": str(candidate.get("symbol") or ""),
            "mint": str(candidate.get("mint") or ""),
            "stage": str(candidate.get("stage") or ""),
            "stage_note": str(candidate.get("stage_note") or ""),
            "cohort_bucket": bucket,
            "scanner_regime": str(candidate.get("scanner_regime") or "NORMAL"),
            "reinforcement_level": str(candidate.get("reinforcement_level") or "NONE"),
            "proof_score": candidate.get("proof_score"),
            "score": candidate.get("score"),
            "slot_eligible_now": bool(slot_eligible),
            "slot_blockers": blockers,
        })

    next_up = candidate_plan_rows[: max(int(limit), 1)]
    next_promotable = next((row for row in next_up if row.get("slot_eligible_now")), None)

    identity_watch = {"collision_prone_symbols": [], "count": 0}
    with sqlite3.connect(_db_path()) as conn:
        conn.row_factory = sqlite3.Row
        collision_rows = conn.execute(
            """
            SELECT symbol,
                   COUNT(DISTINCT mint) AS mint_count,
                   MAX(scanned_at) AS last_seen_at
            FROM memecoin_signal_outcomes
            WHERE source='SCANNER'
              AND mint IS NOT NULL AND mint != ''
              AND scanned_at >= datetime('now', ?)
            GROUP BY symbol
            HAVING COUNT(DISTINCT mint) > 1
            ORDER BY mint_count DESC, last_seen_at DESC
            LIMIT 8
            """,
            (f"-{int(lookback_days)} days",),
        ).fetchall()
        identity_watch = {
            "count": len(collision_rows),
            "collision_prone_symbols": [
                {
                    "symbol": str(r["symbol"] or ""),
                    "mint_count": int(r["mint_count"] or 0),
                    "last_seen_at": str(r["last_seen_at"] or ""),
                }
                for r in collision_rows
            ],
        }

    if recommended_openings <= 0:
        planner_state = "HOLD"
        planner_note = "No additional proof slots are earned right now."
    elif available_slots_now <= 0:
        planner_state = "AT_CAPACITY"
        planner_note = "The current proof slot budget is full."
    elif next_promotable:
        planner_state = "EXPANDABLE"
        planner_note = f"{next_promotable['symbol']} would be the next candidate if another proof slot were allowed."
    else:
        planner_state = "WAITING_ON_QUALITY"
        planner_note = "No current candidate meets the observe-mode next-slot bar."

    return {
        "generated_at": graduation.get("generated_at") or pipeline.get("generated_at"),
        "status": "ACTIVE",
        "detail": "Observe-mode proof slot planner for controlled expansion.",
        "slot_policy": {
            "planner_state": planner_state,
            "planner_note": planner_note,
            "total_slots": total_slots,
            "used_slots": used_slots,
            "available_slots_now": available_slots_now,
            "recommended_slots": recommended_slots,
            "recommended_openings": recommended_openings,
            "deployment_authority": lane_state.get("deployment_authority"),
            "graduation_state": lane_state.get("graduation_state"),
            "slot_authority": ((graduation.get("proof_slots") or {}).get("slot_authority")),
            "slot_gates": list(((graduation.get("proof_slots") or {}).get("slot_gates") or [])),
        },
        "current_proof_trades": current_trades[: max(int(limit), 1)],
        "next_slot_candidates": next_up,
        "identity_watch": identity_watch,
        "top_constraints": list(graduation.get("top_constraints") or [])[:8],
    }


def _v3_parse_ts(value: str | None):
    from datetime import datetime, timezone

    if not value:
        return None
    raw = str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            dt = datetime.strptime(raw, fmt)
            return dt.replace(tzinfo=timezone.utc)
        except Exception:
            pass
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _v3_reason_label(key: str) -> str:
    labels = {
        "already_open": "Already Open",
        "buy_pressure_low": "Buy Pressure Low",
        "cache_empty": "Cache Empty",
        "discovery_needs_scanner_confirmation": "Discovery Needs Scanner Confirmation",
        "discovery_promoted": "Discovery Promoted",
        "entry_window_closed": "Entry Window Closed",
        "entry_window_not_open": "Entry Window Not Open",
        "first_leg_unconfirmed": "First Leg Unconfirmed",
        "fuel_not_actionable": "Fuel Not Actionable",
        "fuel_trap": "Fuel Trap",
        "holder_concentration": "Holder Concentration",
        "missing_mint": "Missing Mint",
        "mint_not_revoked": "Mint Not Revoked",
        "move_phase_extended": "Move Phase Extended",
        "no_reinforcement": "No Reinforcement",
        "provisional_first_leg_only": "Provisional First Leg Only",
        "recent_loss_lock": "Recent Loss Lock",
        "recommended_slots_full": "Recommended Slots Full",
        "deployment_blocked": "Deployment Blocked",
        "reinforced_cohort_too_thin": "Reinforced Cohort Thin",
        "relaxed_base_cohort_tightened": "Relaxed Base Tightened",
        "relaxed_cohort_tightened": "Relaxed Cohort Tightened",
        "relaxed_needs_exact_reinforcement": "Relaxed Needs Exact Reinforcement",
        "relaxed_path_tightened": "Relaxed Path Tightened",
        "relaxed_scanner_path": "Relaxed Scanner Path",
        "rug_danger": "Rug Danger",
        "rug_warning": "Rug Warning",
        "rug_warn_review": "Warn Review",
        "rug_data_missing": "Safety Data Missing",
        "score_above_ceiling": "Score Above Ceiling",
        "score_below_floor": "Score Below Floor",
        "paper_mode": "Paper Mode",
        "proof_slots_full": "Proof Slots Full",
        "triage_unlabeled": "Triage Unlabeled",
        "trust_unlabeled": "Trust Unlabeled",
        "vol_acceleration_low": "Vol Acceleration Low",
    }
    return labels.get(key, key.replace("_", " ").title())


def _v3_reason_severity(key: str) -> str:
    if key in {
        "already_open",
        "buy_pressure_low",
        "deployment_blocked",
        "discovery_needs_scanner_confirmation",
        "entry_window_closed",
        "fuel_trap",
        "holder_concentration",
        "mint_not_revoked",
        "missing_mint",
        "move_phase_extended",
        "recent_loss_lock",
        "rug_danger",
        "rug_not_good",
        "rug_warning",
        "score_below_floor",
        "vol_acceleration_low",
    }:
        return "BLOCK"
    if key in {
        "cache_empty",
        "discovery_promoted",
        "first_leg_unconfirmed",
        "no_reinforcement",
        "provisional_first_leg_only",
        "proof_slots_full",
        "recommended_slots_full",
        "reinforced_cohort_too_thin",
        "relaxed_base_cohort_tightened",
        "relaxed_cohort_tightened",
        "relaxed_needs_exact_reinforcement",
        "relaxed_path_tightened",
        "relaxed_scanner_path",
        "rug_data_missing",
        "rug_warn_review",
        "score_above_ceiling",
        "paper_mode",
        "triage_unlabeled",
        "trust_unlabeled",
    }:
        return "WARN"
    return "INFO"


def _v3_reason(key: str, detail: str = "") -> dict:
    return {
        "key": key,
        "label": _v3_reason_label(key),
        "detail": detail or _v3_reason_label(key),
        "severity": _v3_reason_severity(key),
    }


def _v3_constraint_detail(key: str, count: int) -> str:
    if key == "paper_mode":
        return "Lane is still operating in paper mode."
    if key == "proof_slots_full":
        return f"{count} proof slot{'s' if count != 1 else ''} currently occupied."
    if count <= 0:
        return _v3_reason_label(key)
    return f"{count} active occurrence{'s' if count != 1 else ''}."


def _v3_top_constraints(constraints: list | None) -> list[dict]:
    normalized: list[dict] = []
    for item in list(constraints or []):
        key = str((item or {}).get("key") or "").strip()
        if not key:
            continue
        count = int((item or {}).get("count") or 0)
        normalized.append({
            "key": key,
            "count": count,
            "label": _v3_reason_label(key),
            "detail": _v3_constraint_detail(key, count),
            "severity": _v3_reason_severity(key),
        })
    return normalized


def _v3_support_signal(tag: str, detail: str = "") -> dict:
    key = str(tag or "").strip().lower()
    labels = {
        "whale_overlap": "Whale Overlap",
        "whale_family_overlap": "Whale Family Overlap",
        "confluence_overlap": "Confluence Overlap",
        "confluence_family_overlap": "Confluence Family Overlap",
        "arkham_meaningful": "Arkham Meaningful",
    }
    severities = {
        "whale_overlap": "GOOD",
        "confluence_overlap": "GOOD",
        "whale_family_overlap": "INFO",
        "confluence_family_overlap": "INFO",
        "arkham_meaningful": "INFO",
    }
    fallback_label = key.replace("_", " ").title() if key else "Support"
    return {
        "key": key or "support",
        "label": labels.get(key, fallback_label),
        "detail": detail or labels.get(key, fallback_label),
        "severity": severities.get(key, "INFO"),
    }


def _v3_support_signals(tags: list | None, details: list | None = None) -> list[dict]:
    raw_tags = [str(t or "").strip() for t in list(tags or []) if str(t or "").strip()]
    raw_details = [str(d or "").strip() for d in list(details or [])]
    signals: list[dict] = []
    for idx, tag in enumerate(raw_tags):
        detail = raw_details[idx] if idx < len(raw_details) and raw_details[idx] else ""
        signal = _v3_support_signal(tag, detail)
        if any(str(existing.get("key") or "") == signal["key"] for existing in signals):
            continue
        signals.append(signal)
    return signals


def _v3_label_confidence(label_source: str, symbol_unambiguous: bool) -> float:
    source_scores = {
        "EXISTING": 0.98,
        "MINT_HISTORY": 0.9,
        "SYMBOL_HISTORY": 0.8 if symbol_unambiguous else 0.65,
        "DERIVED_RELAXED": 0.52,
        "DERIVED_ELEVATED": 0.58,
        "DERIVED_WATCH": 0.46,
        "DERIVED_DEFAULT": 0.34,
    }
    parts = [p for p in str(label_source or "EXISTING").upper().split("+") if p]
    if not parts:
        parts = ["EXISTING"]
    scores = [source_scores.get(p, 0.4) for p in parts]
    return round(sum(scores) / len(scores), 2)


def _v3_candidate_scanner_block(candidate: dict) -> dict:
    return {
        "rug_label": str(candidate.get("rug_label") or "UNKNOWN"),
        "rug_telemetry_missing": bool(candidate.get("rug_label") in ("UNKNOWN", "")),
        "holder_quality_level": str(candidate.get("holder_quality_level") or "UNKNOWN"),
        "holder_quality_score": candidate.get("holder_quality_score"),
        "top_holder_pct": candidate.get("top_holder_pct"),
        "top5_holder_pct": candidate.get("top5_holder_pct"),
        "lp_locked_pct": candidate.get("lp_locked_pct"),
        "mint_revoked": bool(candidate.get("mint_revoked")),
        "freeze_revoked": bool(candidate.get("freeze_revoked")),
    }


def _v3_support_kind(candidate: dict, proof_row: dict | None) -> str:
    support_kind = str(candidate.get("reinforcement_support_kind") or "NONE").upper()
    proof_support = (((proof_row or {}).get("proof_components") or {}).get("reinforcement") or {})
    tags = [str(t or "").lower() for t in list(proof_support.get("support_tags") or [])]
    if support_kind == "EXACT_MINT" or any(t in ("whale_overlap", "confluence_overlap") for t in tags):
        return "EXACT_MINT"
    if any("family" in t for t in tags) or int(proof_support.get("symbol_family_support") or 0) > 0:
        return "SYMBOL_FAMILY"
    return "NONE"


def _v3_route(candidate: dict, proof_row: dict | None) -> str:
    proof_status = str((proof_row or {}).get("proof_status") or "").upper()
    if proof_status == "RESEARCH_ONLY":
        return "RESEARCH_ONLY"
    if str(candidate.get("scanner_regime") or "NORMAL").upper() == "RELAXED_NEAR_MISS":
        return "RELAXED"
    return "NORMAL"


def _v3_safety_confidence(scanner_block: dict) -> int:
    telemetry_missing = bool(scanner_block.get("rug_telemetry_missing"))
    if telemetry_missing:
        return 25
    confidence = 65
    if float(scanner_block.get("top5_holder_pct") or 0.0) > 0.0:
        confidence += 15
    if float(scanner_block.get("lp_locked_pct") or 0.0) > 0.0:
        confidence += 10
    if bool(scanner_block.get("mint_revoked")):
        confidence += 5
    if bool(scanner_block.get("freeze_revoked")):
        confidence += 5
    return max(0, min(100, confidence))


def _v3_freshness(scanned_at: str, input_source: str, drought_reason: str | None) -> dict:
    from datetime import datetime, timezone

    source_base = {
        "DISCOVERY_PROMOTED": 66,
        "LIVE_CACHE": 90,
        "LIVE_CACHE_AUGMENTED": 94,
        "WARM_CACHE_FALLBACK": 74,
        "WARM_CACHE_FALLBACK_AUGMENTED": 78,
        "RECENT_SCANNER_FALLBACK": 58,
        "RECENT_PENDING_FALLBACK": 55,
    }
    now = datetime.now(timezone.utc)
    scanned_dt = _v3_parse_ts(scanned_at)
    age_minutes = 9999
    if scanned_dt is not None:
        age_minutes = max(0, int((now - scanned_dt).total_seconds() // 60))
    freshness_score = max(
        0,
        min(
            100,
            int(source_base.get(str(input_source or "").upper(), 45) - min(age_minutes, 240) * 0.18),
        ),
    )
    return {
        "input_source": str(input_source or "UNKNOWN"),
        "freshness_score": freshness_score,
        "cache_lineage": str(input_source or "UNKNOWN").lower(),
        "drought_reason": str(drought_reason or "") or None,
        "scanned_at": str(scanned_at or ""),
        "age_minutes": age_minutes,
    }


def _v3_split_reasons(candidate: dict, proof_row: dict | None, global_unlock: list[dict]) -> tuple[list[dict], list[dict]]:
    proof_reason = str((proof_row or {}).get("proof_reason") or candidate.get("proof_reason") or "")
    primary_blocker = str((proof_row or {}).get("blocker_key") or "").strip()
    blocker_keys = list(dict.fromkeys(list(candidate.get("promotion_blockers") or []) + ([primary_blocker] if primary_blocker else [])))
    hard: list[dict] = []
    soft: list[dict] = []
    for key in blocker_keys:
        detail = proof_reason if key == primary_blocker and proof_reason else _v3_reason_label(key)
        reason = _v3_reason(key, detail)
        if reason["severity"] == "BLOCK":
            hard.append(reason)
        else:
            soft.append(reason)
    for unlock in global_unlock:
        if all(str(existing.get("key") or "") != str(unlock.get("key") or "") for existing in hard + soft):
            if str(unlock.get("severity") or "") == "BLOCK":
                hard.append(unlock)
            else:
                soft.append(unlock)
    return hard, soft


def _v3_confidence(
    *,
    candidate: dict,
    proof_row: dict | None,
    hard_blockers: list[dict],
    soft_penalties: list[dict],
    freshness_score: int,
    support_kind: str,
    route: str,
) -> int:
    stage = str(candidate.get("stage") or "SCANNER_PENDING").upper()
    base = float(
        (proof_row or {}).get("proof_score")
        or candidate.get("proof_score")
        or candidate.get("score")
        or 0.0
    )
    confidence = base
    confidence += {
        "SCANNER_PENDING": 0.0,
        "REINFORCED_PENDING": 5.0,
        "PROOF_READY": 12.0,
        "IN_PROOF_TRADE": 16.0,
        "COMPLETE": -2.0,
    }.get(stage, 0.0)
    confidence += (freshness_score - 70.0) * 0.12
    confidence += {"EXACT_MINT": 8.0, "SYMBOL_FAMILY": 3.0, "NONE": 0.0}.get(support_kind, 0.0)
    confidence -= len(hard_blockers) * 18.0
    confidence -= len(soft_penalties) * 6.0
    if route == "RELAXED":
        confidence -= 8.0
    elif route == "RESEARCH_ONLY":
        confidence -= 15.0
    return max(0, min(100, int(round(confidence))))


def _v3_reinforcement_debug(candidate: dict, support_kind: str, route: str) -> dict:
    thresholds = dict(candidate.get("reinforcement_thresholds") or {})
    if not thresholds:
        thresholds = {"light_min": 2.0, "moderate_min": 5.0, "strong_min": 9.0}

    score = float(candidate.get("reinforcement_score") or 0.0)
    if score >= float(thresholds["strong_min"]):
        next_level = None
        points_to_next = 0.0
    elif score >= float(thresholds["moderate_min"]):
        next_level = "STRONG"
        points_to_next = round(float(thresholds["strong_min"]) - score, 2)
    elif score >= float(thresholds["light_min"]):
        next_level = "MODERATE"
        points_to_next = round(float(thresholds["moderate_min"]) - score, 2)
    else:
        next_level = "LIGHT"
        points_to_next = round(float(thresholds["light_min"]) - score, 2)

    breakdown = dict(candidate.get("reinforcement_support_breakdown") or {})
    whale = dict(breakdown.get("whale") or {})
    missing_reasons = list(candidate.get("reinforcement_missing_reasons") or [])
    if not missing_reasons:
        if support_kind == "NONE":
            missing_reasons.append("no cross-lane reinforcement detected yet")
        elif support_kind == "SYMBOL_FAMILY":
            missing_reasons.append("only symbol-family support is present")
        if route == "RELAXED" and support_kind != "EXACT_MINT":
            missing_reasons.append("relaxed path needs exact mint reinforcement")
        if int(whale.get("meaningful_arkham_count") or 0) <= 0:
            missing_reasons.append("no live Arkham context is attached")
    missing_reasons = list(dict.fromkeys([m for m in missing_reasons if str(m or "").strip()]))[:5]

    return {
        "next_level": next_level,
        "points_to_next": points_to_next,
        "thresholds": thresholds,
        "missing_reasons": missing_reasons,
        "breakdown": breakdown,
    }


def _v3_global_unlock_reasons(graduation: dict) -> list[dict]:
    lane_state = dict(graduation.get("lane_state") or {})
    proof_slots = dict(graduation.get("proof_slots") or {})
    reasons: list[dict] = []
    if str(lane_state.get("deployment_authority") or "").upper() == "BLOCKED":
        reasons.append(_v3_reason("deployment_blocked", "Lane policy still blocks deployment."))
    if str(proof_slots.get("slot_state") or "").upper() == "FULL":
        reasons.append(_v3_reason("recommended_slots_full", "Current proof slot budget is full."))
    for gate in list(proof_slots.get("slot_gates") or []):
        if bool(gate.get("passed")):
            continue
        gate_key = str(gate.get("key") or "")
        reason_key = {
            "reinforced_cohort_maturing": "reinforced_cohort_too_thin",
        }.get(gate_key)
        if reason_key:
            reasons.append(_v3_reason(reason_key, str(gate.get("note") or "")))
    return reasons


def _v3_candidate_unlock_next(
    candidate: dict,
    proof_row: dict | None,
    graduation: dict,
    *,
    support_kind: str,
    route: str,
) -> list[dict]:
    proof_row = dict(proof_row or {})
    proof_components = dict(proof_row.get("proof_components") or {})
    lifecycle = dict(proof_components.get("lifecycle") or {})
    scanner = dict(proof_components.get("scanner") or {})
    proof_slots = dict(graduation.get("proof_slots") or {})
    lane_state = dict(graduation.get("lane_state") or {})

    stage = str(candidate.get("stage") or "SCANNER_PENDING").upper()
    primary_blocker = str(proof_row.get("blocker_key") or "").strip()
    candidate_blockers = list(candidate.get("promotion_blockers") or [])
    unlock: list[dict] = []

    def _push(key: str, detail: str = "") -> None:
        if not key:
            return
        if any(str(item.get("key") or "") == key for item in unlock):
            return
        unlock.append(_v3_reason(key, detail))

    source = str(candidate.get("source") or "SCANNER").upper()
    if source == "DISCOVERY":
        _push(
            "discovery_needs_scanner_confirmation",
            "Discovery-promoted names need scanner-quality confirmation before they should be treated like survivor-lane names.",
        )

    if route == "RELAXED":
        if support_kind != "EXACT_MINT":
            _push(
                "relaxed_needs_exact_reinforcement",
                "Relaxed-path promotion needs exact mint reinforcement before it can advance.",
            )
        else:
            _push(
                "relaxed_path_tightened",
                "Relaxed-path names still need stronger proof structure than normal-path names.",
            )

    reinf_level = str(candidate.get("reinforcement_level") or "NONE").upper()
    if reinf_level == "NONE" and "no_reinforcement" in candidate_blockers:
        _push("no_reinforcement", "This candidate needs reinforcement before it can promote further.")

    if int(lifecycle.get("first_leg_confirmed") or 0) == 0:
        if int(lifecycle.get("provisional_first_leg") or 0) == 1:
            _push(
                "provisional_first_leg_only",
                "First leg is only provisional right now; it still needs confirmation.",
            )
        elif primary_blocker == "first_leg_unconfirmed" or "first_leg_unconfirmed" in candidate_blockers:
            _push("first_leg_unconfirmed", "This candidate still needs a confirmed first leg.")

    if primary_blocker in {
        "vol_acceleration_low",
        "buy_pressure_low",
        "score_below_floor",
        "score_above_ceiling",
        "rug_warning",
        "rug_warn_review",
        "rug_data_missing",
        "rug_danger",
        "holder_concentration",
        "mint_not_revoked",
    }:
        detail = str(proof_row.get("proof_reason") or "") or _v3_reason_label(primary_blocker)
        _push(primary_blocker, detail)

    if primary_blocker == "entry_window_closed":
        _push("entry_window_closed", "Entry window is closed; this needs a fresher lifecycle setup.")
    if primary_blocker == "move_phase_extended":
        _push("move_phase_extended", "Move phase is already extended; it needs a better reset/reload state.")

    for blocker in candidate_blockers:
        if blocker in {
            "trust_unlabeled",
            "triage_unlabeled",
            "cache_empty",
        }:
            detail = {
                "trust_unlabeled": "Trust label is still missing on the latest scanner row.",
                "triage_unlabeled": "Triage state is still missing on the latest scanner row.",
                "cache_empty": "Live scan cache is empty; this candidate is being reviewed from fallback input.",
            }.get(blocker, "")
            _push(blocker, detail)

    if stage == "PROOF_READY":
        if int(proof_slots.get("total_slots") or 0) <= int(proof_slots.get("used_slots") or 0):
            _push("recommended_slots_full", "Proof slot capacity is full, so this ready name cannot open yet.")
        if str(lane_state.get("deployment_authority") or "").upper() == "BLOCKED":
            _push("deployment_blocked", "Lane policy still blocks deployment even though this name is proof-ready.")
    elif stage == "REINFORCED_PENDING":
        _push("first_leg_unconfirmed", "Reinforcement is present, but proof promotion still needs stronger confirmation.")
    elif stage == "SCANNER_PENDING" and route == "NORMAL" and reinf_level != "NONE":
        _push("first_leg_unconfirmed", "This name is reinforced, but still needs proof confirmation to advance.")

    for global_reason in _v3_global_unlock_reasons(graduation):
        key = str(global_reason.get("key") or "")
        if key == "deployment_blocked" and stage not in ("PROOF_READY", "IN_PROOF_TRADE"):
            continue
        if key == "recommended_slots_full" and stage not in ("PROOF_READY",):
            continue
        if key == "reinforced_cohort_too_thin" and reinf_level not in ("LIGHT", "MODERATE", "STRONG"):
            continue
        _push(key, str(global_reason.get("detail") or ""))

    if not unlock:
        if stage == "IN_PROOF_TRADE":
            _push("already_open", "This candidate is already in an open proof trade.")
        else:
            _push("no_reinforcement", "This candidate is still accumulating evidence.")

    return unlock[:4]


def _build_v3_candidate(candidate: dict, proof_row: dict | None, graduation: dict, pipeline: dict) -> dict:
    proof_row = dict(proof_row or {})
    proof_components = dict(proof_row.get("proof_components") or {})
    scanner_block = dict(proof_components.get("scanner") or _v3_candidate_scanner_block(candidate) or {})
    reinforcement_block = dict(proof_components.get("reinforcement") or {})

    lane_state = dict(graduation.get("lane_state") or {})
    proof_slots = dict(graduation.get("proof_slots") or {})
    source = str(candidate.get("source") or "SCANNER").upper()
    input_source = (
        "DISCOVERY_PROMOTED"
        if source == "DISCOVERY"
        else str(((pipeline.get("pipeline_health") or {}).get("proof_input_source")) or "UNKNOWN")
    )
    drought_reason = (
        "promoted from discovery ingress"
        if source == "DISCOVERY"
        else str(((pipeline.get("pipeline_health") or {}).get("funnel_reason")) or "") or None
    )
    freshness = _v3_freshness(str(candidate.get("scanned_at") or ""), input_source, drought_reason)

    support_kind = _v3_support_kind(candidate, proof_row)
    route = _v3_route(candidate, proof_row)
    hard_blockers, soft_penalties = _v3_split_reasons(candidate, proof_row, _v3_global_unlock_reasons(graduation))
    unlock_next = _v3_candidate_unlock_next(
        candidate,
        proof_row,
        graduation,
        support_kind=support_kind,
        route=route,
    )
    confidence = _v3_confidence(
        candidate=candidate,
        proof_row=proof_row,
        hard_blockers=hard_blockers,
        soft_penalties=soft_penalties,
        freshness_score=int(freshness["freshness_score"]),
        support_kind=support_kind,
        route=route,
    )

    label_source = str(candidate.get("label_source") or "EXISTING")
    symbol_unambiguous = bool(candidate.get("symbol_unambiguous", False))
    available_slots = max(
        0,
        int(proof_slots.get("total_slots") or 0) - int(proof_slots.get("used_slots") or 0),
    )
    reinforcement_debug = _v3_reinforcement_debug(candidate, support_kind, route)
    whale_breakdown = dict((reinforcement_debug.get("breakdown") or {}).get("whale") or {})

    return {
        "id": int(candidate.get("id") or 0),
        "symbol": str(candidate.get("symbol") or ""),
        "mint": str(candidate.get("mint") or ""),
        "source": source,
        "promotion_state": str(candidate.get("promotion_state") or "") or None,
        "discovery_stage": str(candidate.get("discovery_stage") or "") or None,
        "discovery_rank_score": candidate.get("discovery_rank_score"),
        "discovery_flags": list(candidate.get("discovery_flags") or []),
        "row_status": str(candidate.get("row_status") or "PENDING"),
        "scanner_score": float(candidate.get("score") or 0.0),
        "scanner_regime": str(candidate.get("scanner_regime") or "NORMAL"),
        "scanner_relaxation_reason": str(candidate.get("scanner_relaxation_reason") or "") or None,
        "return_4h_pct": candidate.get("return_4h_pct"),
        "return_24h_pct": candidate.get("return_24h_pct"),
        "freshness": freshness,
        "safety": {
            "rug_label": str(scanner_block.get("rug_label") or "UNKNOWN"),
            "safety_confidence": _v3_safety_confidence(scanner_block),
            "telemetry_missing": bool(scanner_block.get("rug_telemetry_missing")),
            "holder_quality_level": str(scanner_block.get("holder_quality_level") or "UNKNOWN"),
            "holder_quality_score": scanner_block.get("holder_quality_score"),
            "top_holder_pct": scanner_block.get("top_holder_pct"),
            "top5_holder_pct": scanner_block.get("top5_holder_pct"),
            "lp_locked_pct": scanner_block.get("lp_locked_pct"),
            "mint_revoked": bool(scanner_block.get("mint_revoked")),
            "freeze_revoked": bool(scanner_block.get("freeze_revoked")),
        },
        "reinforcement": {
            "level": str(candidate.get("reinforcement_level") or "NONE"),
            "score": float(candidate.get("reinforcement_score") or 0.0),
            "support_kind": support_kind,
            "confidence": {
                "EXACT_MINT": 0.95,
                "SYMBOL_FAMILY": 0.6,
                "NONE": 0.1,
            }.get(support_kind, 0.1),
            "recency_minutes": whale_breakdown.get("age_minutes"),
            "reasons": [
                _v3_reason(str(reason or ""), _v3_reason_label(str(reason or "")))
                for reason in list(candidate.get("reinforcement_reasons") or [])[:4]
                if str(reason or "").strip()
            ],
            "support_signals": _v3_support_signals(
                list(reinforcement_block.get("support_tags") or []),
                list(reinforcement_block.get("support_details") or []),
            ),
            "debug": reinforcement_debug,
        },
        "proof": {
            "route": route,
            "stage": str(candidate.get("stage") or "SCANNER_PENDING"),
            "confidence": confidence,
            "score": proof_row.get("proof_score") or candidate.get("proof_score"),
            "hard_blockers": hard_blockers,
            "soft_penalties": soft_penalties,
            "promotion_summary": str(candidate.get("stage_note") or ""),
            "proof_reason": str(proof_row.get("proof_reason") or candidate.get("proof_reason") or "") or None,
            "first_leg_confirmed": bool(((proof_components.get("lifecycle") or {}).get("first_leg_confirmed"))),
            "provisional_first_leg": bool(candidate.get("provisional_first_leg") or proof_row.get("provisional_first_leg")),
            "provisional_score": candidate.get("provisional_score") or proof_row.get("provisional_score"),
            "provisional_reason": str(candidate.get("provisional_reason") or proof_row.get("provisional_reason") or "") or None,
        },
        "policy": {
            "deploy_authority": str(lane_state.get("deployment_authority") or "UNKNOWN"),
            "slot_state": str(proof_slots.get("slot_state") or "UNKNOWN"),
            "recommended_slots": int(proof_slots.get("recommended_slots") or 0),
            "used_slots": int(proof_slots.get("used_slots") or 0),
            "available_slots": available_slots,
            "unlock_next": unlock_next,
        },
        "labels": {
            "trust_label": str(candidate.get("trust_label") or "") or None,
            "triage_state": str(candidate.get("triage_state") or "") or None,
            "label_confidence": _v3_label_confidence(label_source, symbol_unambiguous),
            "label_source": label_source,
            "labeled_at": str(candidate.get("labeled_at") or "") or None,
        },
    }


def _get_v3_discovery_promotion_feed(limit: int = 12) -> dict:
    from datetime import datetime, timedelta, timezone
    from utils.db import get_conn, get_recent_scan_bests  # type: ignore
    from utils.scanner_labels import resolve_scanner_labels  # type: ignore

    now = datetime.now(timezone.utc)
    cutoff_48h = (now - timedelta(hours=48)).strftime("%Y-%m-%d %H:%M:%S")
    cutoff_72h = (now - timedelta(hours=72)).strftime("%Y-%m-%d %H:%M:%S")
    now_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    MAX_TOP_H_QL = 3.0
    MAX_LIQ_QL = 50_000.0
    MAX_APPEAR_72H = 3
    MIN_PROMOTION_SCORE = 55.0

    recent_scan_bests = get_recent_scan_bests(lookback_hours=12, limit=50)
    scan_best_by_symbol = {
        str(r.get("symbol") or "").upper(): r
        for r in recent_scan_bests
        if r.get("symbol")
    }

    with get_conn() as conn:
        conn.row_factory = sqlite3.Row

        sym_72h_rows = conn.execute(
            "SELECT symbol, COUNT(*) as cnt FROM memecoin_signal_outcomes "
            "WHERE scanned_at >= ? GROUP BY symbol",
            (cutoff_72h,),
        ).fetchall()
        appearances_72h = {
            str(r["symbol"]).upper(): int(r["cnt"] or 0)
            for r in sym_72h_rows
            if r["symbol"]
        }

        rows = conn.execute(
            """
            SELECT
                d.id,
                d.symbol,
                d.mint,
                d.scanned_at,
                d.status,
                d.score,
                d.return_4h_pct,
                d.return_24h_pct,
                d.liquidity_usd,
                d.rug_label,
                d.top_holder_pct,
                d.top5_holder_pct,
                d.lp_locked_pct,
                d.mcap_at_scan,
                d.token_age_days,
                d.vol_acceleration,
                d.mint_revoked,
                d.freeze_revoked,
                d.buy_pressure_at_scan,
                d.holder_quality_score,
                d.holder_quality_level,
                d.attention_infrastructure,
                d.boost_active,
                d.attention_quality,
                d.trust_label,
                d.triage_state,
                d.labeled_at,
                COALESCE(d.scanner_regime, 'NORMAL') AS scanner_regime,
                d.scanner_relaxation_reason,
                lc.lifecycle_state,
                lc.first_leg_confirmed,
                lc.n_windows,
                lc.liq_trend,
                lc.liq_current,
                lc.hours_since_last_scan,
                lc.entry_window,
                lc.fuel_quality,
                lc.move_phase
            FROM (
                SELECT m1.*
                FROM memecoin_signal_outcomes m1
                INNER JOIN (
                    SELECT mint, MAX(scanned_at) AS max_scanned_at
                    FROM memecoin_signal_outcomes
                    WHERE source = 'DISCOVERY'
                      AND scanned_at >= ?
                    GROUP BY mint
                ) latest
                  ON latest.mint = m1.mint
                 AND latest.max_scanned_at = m1.scanned_at
                WHERE m1.source = 'DISCOVERY'
            ) d
            LEFT JOIN symbol_lifecycle lc
              ON lc.mint = d.mint
            ORDER BY d.scanned_at DESC
            LIMIT 120
            """,
            (cutoff_48h,),
        ).fetchall()

        candidates: list[dict] = []
        seen_symbols: set[str] = set()
        qualified_count = 0
        promoted_count = 0
        scan_best_overlap = 0

        for row in rows:
            rec = dict(row)
            rec.update(resolve_scanner_labels(conn, rec, persist=True))
            symbol = str(rec.get("symbol") or "").upper()
            if not symbol or symbol in seen_symbols:
                continue

            liq = float(rec.get("liquidity_usd") or rec.get("liq_current") or 0.0)
            top_h = rec.get("top_holder_pct")
            top_h_val = float(top_h) if top_h is not None else None
            vol_acc = rec.get("vol_acceleration")
            vol_acc_val = float(vol_acc) if vol_acc is not None else None
            attention_quality = str(rec.get("attention_quality") or "")
            attention_infra = str(rec.get("attention_infrastructure") or "")
            boost_active = int(rec.get("boost_active") or 0)
            lifecycle_state = rec.get("lifecycle_state")
            first_leg_confirmed = int(rec.get("first_leg_confirmed") or 0)
            n_windows = int(rec.get("n_windows") or 0)
            liq_trend = str(rec.get("liq_trend") or "flat")
            raw_hours_since_last_scan = rec.get("hours_since_last_scan")
            hsls = float(raw_hours_since_last_scan) if raw_hours_since_last_scan is not None else None
            appear_72h = appearances_72h.get(symbol, 0)
            scan_best = scan_best_by_symbol.get(symbol)

            quality_lane = (
                top_h_val is not None and 0 < top_h_val < MAX_TOP_H_QL
                and 0 < liq < MAX_LIQ_QL
                and appear_72h <= MAX_APPEAR_72H
            )
            lifecycle_early = (
                lifecycle_state is not None
                and first_leg_confirmed == 0
                and (hsls is not None and hsls < 8.0)
                and (vol_acc_val or 0.0) >= 3.0
                and liq_trend in ("flat", "growing")
                and n_windows <= 2
            )
            ingress_qualified = (
                liq >= 50_000.0
                and (
                    attention_quality in ("STRONG", "MODERATE")
                    or boost_active == 1
                    or attention_infra not in ("", "ABSENT", "NONE", "0")
                    or (vol_acc_val or 0.0) >= 3.0
                )
            )

            if not (quality_lane or lifecycle_early or ingress_qualified or scan_best):
                continue
            qualified_count += 1
            if scan_best:
                scan_best_overlap += 1

            rank_score = 0.0
            badges: list[str] = []
            if lifecycle_early:
                rank_score += 35
                badges.append("LIFECYCLE_EARLY")
            if quality_lane:
                rank_score += 18
                badges.append("QUALITY_LANE")
            if scan_best:
                rank_score += 14
                badges.append("SCAN_BEST")
            if boost_active == 1:
                rank_score += 12
                badges.append("BOOSTED")
            if attention_quality == "STRONG":
                rank_score += 10
                badges.append("ATTN_STRONG")
            elif attention_quality == "MODERATE":
                rank_score += 5
                badges.append("ATTN_MODERATE")
            if vol_acc_val is not None:
                rank_score += min(vol_acc_val * 1.8, 18)
            rank_score += min(liq / 40_000.0, 12)
            rank_score = round(rank_score, 2)

            discovery_stage = (
                "LIFECYCLE_EARLY" if lifecycle_early else
                "DISCOVERY_PLUS" if ingress_qualified and quality_lane else
                "DISCOVERY_INGRESS"
            )
            promotable = (
                (lifecycle_early or ingress_qualified)
                and rank_score >= MIN_PROMOTION_SCORE
                and float(rec.get("score") or 0.0) >= 50.0
                and str(rec.get("rug_label") or "UNKNOWN").upper() == "GOOD"
                and (vol_acc_val or 0.0) >= 3.0
                and liq >= 50_000.0
            )
            if promotable:
                promoted_count += 1

            promotion_blockers: list[str] = ["discovery_promoted", "discovery_needs_scanner_confirmation"]
            if not str(rec.get("trust_label") or "").strip():
                promotion_blockers.append("trust_unlabeled")
            if not str(rec.get("triage_state") or "").strip():
                promotion_blockers.append("triage_unlabeled")
            promotion_blockers.append("no_reinforcement")

            candidates.append({
                "id": int(rec.get("id") or 0),
                "symbol": symbol,
                "mint": rec.get("mint"),
                "scanned_at": str(rec.get("scanned_at") or ""),
                "row_status": str(rec.get("status") or "WATCH").upper(),
                "source": "DISCOVERY",
                "promotion_state": "DISCOVERY_PROMOTED" if promotable else "DISCOVERY_QUALIFIED",
                "discovery_stage": discovery_stage,
                "discovery_rank_score": rank_score,
                "discovery_flags": badges,
                "score": float(rec.get("score") or 0.0),
                "scanner_regime": str(rec.get("scanner_regime") or "NORMAL"),
                "scanner_relaxation_reason": str(rec.get("scanner_relaxation_reason") or ""),
                "trust_label": str(rec.get("trust_label") or ""),
                "triage_state": str(rec.get("triage_state") or ""),
                "labeled_at": str(rec.get("labeled_at") or ""),
                "label_source": str(rec.get("label_source") or "DERIVED_WATCH"),
                "symbol_unambiguous": bool(rec.get("symbol_unambiguous", False)),
                "proof_reason": "discovery-promoted candidate is waiting for scanner-quality confirmation",
                "proof_score": None,
                "provisional_first_leg": 0,
                "provisional_reason": "",
                "provisional_score": None,
                "return_4h_pct": rec.get("return_4h_pct"),
                "return_24h_pct": rec.get("return_24h_pct"),
                "stage": "SCANNER_PENDING",
                "stage_note": (
                    "Discovery-promoted candidate passed fresh-ingress quality checks and is waiting for scanner/proof confirmation."
                    if promotable else
                    "Discovery-qualified candidate is being tracked, but has not earned queue promotion yet."
                ),
                "promotion_blockers": promotion_blockers[:5],
                "reinforcement_level": "NONE",
                "reinforcement_score": 0.0,
                "reinforcement_reasons": [],
                "reinforcement_support_kind": "NONE",
                "reinforcement_support_breakdown": {},
                "reinforcement_next_level": "LIGHT",
                "reinforcement_points_to_next": 2.0,
                "reinforcement_thresholds": {"light_min": 2.0, "moderate_min": 5.0, "strong_min": 9.0},
                "reinforcement_missing_reasons": [
                    "discovery path has not earned cross-lane reinforcement yet",
                    "scanner-quality confirmation is still pending",
                ],
                "open_proof_trade_id": None,
                "open_proof_status": None,
                "rug_label": str(rec.get("rug_label") or "UNKNOWN"),
                "top_holder_pct": rec.get("top_holder_pct"),
                "top5_holder_pct": rec.get("top5_holder_pct"),
                "lp_locked_pct": rec.get("lp_locked_pct"),
                "mcap_at_scan": rec.get("mcap_at_scan"),
                "token_age_days": rec.get("token_age_days"),
                "vol_acceleration": rec.get("vol_acceleration"),
                "mint_revoked": bool(rec.get("mint_revoked")),
                "freeze_revoked": bool(rec.get("freeze_revoked")),
                "buy_pressure_at_scan": rec.get("buy_pressure_at_scan"),
                "holder_quality_score": rec.get("holder_quality_score"),
                "holder_quality_level": str(rec.get("holder_quality_level") or "UNKNOWN"),
                "_promotable": promotable,
            })
            seen_symbols.add(symbol)

    candidates.sort(
        key=lambda item: (
            int(bool(item.get("_promotable"))),
            float(item.get("discovery_rank_score") or 0.0),
            float(item.get("score") or 0.0),
            str(item.get("scanned_at") or ""),
        ),
        reverse=True,
    )
    candidates = candidates[: max(1, min(int(limit), 20))]
    promoted = [c for c in candidates if bool(c.get("_promotable"))]
    for item in candidates:
        item.pop("_promotable", None)

    return {
        "generated_at": now_iso,
        "candidates": candidates,
        "promoted_candidates": promoted,
        "summary": {
            "qualified_count": qualified_count,
            "promoted_count": promoted_count,
            "scan_best_overlap": scan_best_overlap,
            "lifecycle_early": sum(1 for c in candidates if c.get("discovery_stage") == "LIFECYCLE_EARLY"),
            "discovery_plus": sum(1 for c in candidates if c.get("discovery_stage") == "DISCOVERY_PLUS"),
            "discovery_ingress": sum(1 for c in candidates if c.get("discovery_stage") == "DISCOVERY_INGRESS"),
        },
    }


def get_memecoin_v3_lane_state(limit: int = 10, lookback_days: int = 30) -> dict:
    graduation = get_memecoin_graduation_data(limit=max(int(limit), 8), lookback_days=max(int(lookback_days), 7))
    pipeline = get_proof_pipeline_summary_data(limit=max(int(limit), 10))
    discovery = _get_v3_discovery_promotion_feed(limit=max(int(limit), 10))

    with sqlite3.connect(_db_path()) as conn:
        conn.row_factory = sqlite3.Row
        coverage_row = conn.execute(
            """
            SELECT
                COUNT(*) AS total_rows,
                SUM(CASE WHEN NULLIF(trust_label, '') IS NOT NULL AND NULLIF(triage_state, '') IS NOT NULL THEN 1 ELSE 0 END) AS fully_labeled,
                SUM(CASE WHEN rug_label='UNKNOWN' THEN 1 ELSE 0 END) AS rug_unknown,
                SUM(CASE WHEN rug_label='WARN' THEN 1 ELSE 0 END) AS rug_warn,
                SUM(CASE WHEN rug_label='GOOD' THEN 1 ELSE 0 END) AS rug_good
            FROM memecoin_signal_outcomes
            WHERE source='SCANNER'
              AND status IN ('PENDING', 'COMPLETE')
              AND scanned_at >= datetime('now', '-72 hours')
            """
        ).fetchone()

    total_rows = int((coverage_row["total_rows"] or 0) if coverage_row else 0)
    fully_labeled = int((coverage_row["fully_labeled"] or 0) if coverage_row else 0)
    label_coverage = round((fully_labeled / total_rows) * 100.0, 1) if total_rows > 0 else 100.0

    return {
        "generated_at": graduation.get("generated_at") or pipeline.get("generated_at"),
        "status": str(graduation.get("status") or "ACTIVE"),
        "detail": str(graduation.get("detail") or ""),
        "lane_state": dict(graduation.get("lane_state") or {}),
        "proof_slots": dict(graduation.get("proof_slots") or {}),
        "cohorts": list(graduation.get("cohorts") or []),
        "system_health": {
            "pipeline_status": str(pipeline.get("status") or "QUIET"),
            "pipeline_detail": str(pipeline.get("detail") or ""),
            "funnel_status": str(((pipeline.get("pipeline_health") or {}).get("funnel_status")) or "UNKNOWN"),
            "funnel_reason": str(((pipeline.get("pipeline_health") or {}).get("funnel_reason")) or ""),
            "proof_input_source": str(((pipeline.get("pipeline_health") or {}).get("proof_input_source")) or "UNKNOWN"),
            "reinforcement_status": str(((pipeline.get("pipeline_health") or {}).get("reinforcement_status")) or "UNKNOWN"),
            "reinforcement_detail": str(((pipeline.get("pipeline_health") or {}).get("reinforcement_detail")) or ""),
            "reinforcement_thresholds": dict(((pipeline.get("pipeline_health") or {}).get("reinforcement_thresholds")) or {}),
            "reinforcement_summary": dict(((pipeline.get("pipeline_health") or {}).get("reinforcement_summary")) or {}),
            "discovery_summary": dict(discovery.get("summary") or {}),
            "label_coverage_pct": label_coverage,
            "recent_scanner_rows": total_rows,
            "rug_mix": {
                "good": int((coverage_row["rug_good"] or 0) if coverage_row else 0),
                "warn": int((coverage_row["rug_warn"] or 0) if coverage_row else 0),
                "unknown": int((coverage_row["rug_unknown"] or 0) if coverage_row else 0),
            },
        },
        "top_constraints": _v3_top_constraints(graduation.get("top_constraints") or []),
    }


def get_memecoin_v3_queue(limit: int = 25, lookback_days: int = 30) -> dict:
    graduation = get_memecoin_graduation_data(limit=max(int(limit), 8), lookback_days=max(int(lookback_days), 7))
    pipeline = get_proof_pipeline_summary_data(limit=max(int(limit), 10))
    discovery = _get_v3_discovery_promotion_feed(limit=max(6, min(int(limit), 12)))
    proof_snapshot = {}
    try:
        from utils.memecoin_manager import get_proof_candidate_snapshot  # type: ignore

        proof_snapshot = get_proof_candidate_snapshot(limit=max(int(limit) * 2, 20), include_recent_complete=True)
    except Exception:
        proof_snapshot = {"candidates": []}

    proof_by_mint = {
        str(item.get("mint") or ""): dict(item)
        for item in list(proof_snapshot.get("candidates") or [])
        if str(item.get("mint") or "")
    }

    grouped = {
        "scanner_pending": [],
        "reinforced_pending": [],
        "proof_ready": [],
        "in_proof_trade": [],
        "complete": [],
    }
    counts = {key: 0 for key in grouped}
    stage_to_group = {
        "SCANNER_PENDING": "scanner_pending",
        "REINFORCED_PENDING": "reinforced_pending",
        "PROOF_READY": "proof_ready",
        "IN_PROOF_TRADE": "in_proof_trade",
        "COMPLETE": "complete",
    }

    for candidate in list(pipeline.get("candidates") or []):
        group_key = stage_to_group.get(str(candidate.get("stage") or "").upper(), "scanner_pending")
        normalized = _build_v3_candidate(candidate, proof_by_mint.get(str(candidate.get("mint") or "")), graduation, pipeline)
        grouped[group_key].append(normalized)
        counts[group_key] += 1

    seen_mints = {
        str(item.get("mint") or "")
        for items in grouped.values()
        for item in items
        if str(item.get("mint") or "")
    }
    for candidate in list(discovery.get("promoted_candidates") or []):
        mint = str(candidate.get("mint") or "")
        if not mint or mint in seen_mints:
            continue
        normalized = _build_v3_candidate(candidate, None, graduation, pipeline)
        grouped["scanner_pending"].append(normalized)
        counts["scanner_pending"] += 1
        seen_mints.add(mint)

    for items in grouped.values():
        items.sort(
            key=lambda item: (
                -int(((item.get("proof") or {}).get("confidence") or 0)),
                -float(item.get("scanner_score") or 0.0),
                str(item.get("symbol") or ""),
            )
        )

    return {
        "generated_at": pipeline.get("generated_at") or graduation.get("generated_at"),
        "status": str(pipeline.get("status") or "QUIET"),
        "detail": str(pipeline.get("detail") or ""),
        "counts": counts,
        "lane_state": dict(graduation.get("lane_state") or {}),
        "discovery_summary": dict(discovery.get("summary") or {}),
        "groups": grouped,
    }


def _v3_queue_candidate_map(queue_payload: dict) -> dict[str, dict]:
    by_mint: dict[str, dict] = {}
    for items in dict(queue_payload.get("groups") or {}).values():
        for item in list(items or []):
            mint = str(item.get("mint") or "")
            if mint:
                by_mint[mint] = dict(item)
    return by_mint


def _v3_trade_age_hours(opened_ts_utc: str | None) -> float | None:
    from datetime import datetime, timezone

    dt = _v3_parse_ts(opened_ts_utc)
    if dt is None:
        return None
    return round((datetime.now(timezone.utc) - dt).total_seconds() / 3600.0, 1)


def _v3_strip_legacy_support_fields(payload: dict | None) -> dict:
    clean = dict(payload or {})
    clean.pop("support_tags", None)
    clean.pop("support_details", None)
    return clean


def get_memecoin_v3_candidate_detail(mint: str, lookback_days: int = 30) -> dict:
    _ensure_engine_path()
    from utils.scanner_labels import resolve_scanner_labels  # type: ignore

    mint = str(mint or "").strip()
    if not mint:
        raise HTTPException(status_code=400, detail="mint is required")

    queue_payload = get_memecoin_v3_queue(limit=150, lookback_days=max(int(lookback_days), 7))
    lane_payload = get_memecoin_v3_lane_state(limit=12, lookback_days=max(int(lookback_days), 7))
    proof_payload = get_proof_stack_summary_data(limit=25)
    candidate = _v3_queue_candidate_map(queue_payload).get(mint)

    with sqlite3.connect(_db_path()) as conn:
        conn.row_factory = sqlite3.Row
        _ensure_memecoin_outcome_label_schema(conn)
        history_rows = conn.execute(
            """
            SELECT
                id, scanned_at, source, status, symbol, mint, score,
                proof_score, proof_reason, return_4h_pct, return_24h_pct,
                trust_label, triage_state, labeled_at,
                COALESCE(scanner_regime, 'NORMAL') AS scanner_regime,
                scanner_relaxation_reason
            FROM memecoin_signal_outcomes
            WHERE mint = ?
              AND scanned_at >= datetime('now', ?)
            ORDER BY scanned_at DESC, id DESC
            LIMIT 16
            """,
            (mint, f"-{int(lookback_days)} days"),
        ).fetchall()

        history: list[dict] = []
        for row in history_rows:
            row_data = dict(row)
            if str(row_data.get("source") or "").upper() == "SCANNER":
                row_data.update(resolve_scanner_labels(conn, row_data, persist=True))
            history.append({
                "id": int(row_data.get("id") or 0),
                "scanned_at": str(row_data.get("scanned_at") or ""),
                "source": str(row_data.get("source") or "UNKNOWN"),
                "status": str(row_data.get("status") or "UNKNOWN"),
                "symbol": str(row_data.get("symbol") or ""),
                "mint": str(row_data.get("mint") or ""),
                "score": row_data.get("score"),
                "proof_score": row_data.get("proof_score"),
                "proof_reason": str(row_data.get("proof_reason") or "") or None,
                "return_4h_pct": row_data.get("return_4h_pct"),
                "return_24h_pct": row_data.get("return_24h_pct"),
                "scanner_regime": str(row_data.get("scanner_regime") or "NORMAL"),
                "scanner_relaxation_reason": str(row_data.get("scanner_relaxation_reason") or "") or None,
                "trust_label": str(row_data.get("trust_label") or "") or None,
                "triage_state": str(row_data.get("triage_state") or "") or None,
                "label_source": str(row_data.get("label_source") or "EXISTING") if row_data.get("source") == "SCANNER" else None,
                "labeled_at": str(row_data.get("labeled_at") or "") or None,
            })
        try:
            conn.commit()
        except Exception:
            pass

    if candidate is None and not history:
        raise HTTPException(status_code=404, detail=f"no recent candidate found for mint {mint}")

    proof_review = dict(proof_payload.get("proof_review") or {})
    open_trade = next(
        (dict(item) for item in list(proof_review.get("open_trades") or []) if str(item.get("mint") or "") == mint),
        None,
    )
    if open_trade is not None:
        support_tags = list(open_trade.get("support_tags") or [])
        support_details = list(open_trade.get("support_details") or [])
        open_trade = _v3_strip_legacy_support_fields(open_trade)
        open_trade["age_hours"] = _v3_trade_age_hours(open_trade.get("opened_ts_utc"))
        open_trade["support_signals"] = _v3_support_signals(
            support_tags,
            support_details,
        )
        try:
            from utils.db import get_latest_memecoin_exit_reviews, get_recent_memecoin_exit_review_history  # type: ignore

            exit_reviews = get_latest_memecoin_exit_reviews([int(open_trade.get("id") or 0)])
            open_trade["exit_review"] = exit_reviews.get(int(open_trade.get("id") or 0))
            exit_history = get_recent_memecoin_exit_review_history([int(open_trade.get("id") or 0)], limit_per_trade=4)
            open_trade["exit_history"] = exit_history.get(int(open_trade.get("id") or 0), [])
        except Exception:
            open_trade["exit_review"] = None
            open_trade["exit_history"] = []
    recent_outcomes = []
    for item in list(proof_review.get("recent_outcomes") or []):
        if str(item.get("mint") or "") != mint:
            continue
        payload = dict(item)
        payload["route"] = (
            "RELAXED"
            if str(payload.get("scanner_regime") or "").upper() == "RELAXED_NEAR_MISS"
            else "NORMAL"
        )
        support_tags = list(payload.get("support_tags") or [])
        support_details = list(payload.get("support_details") or [])
        payload["support_signals"] = _v3_support_signals(
            support_tags,
            support_details,
        )
        payload = _v3_strip_legacy_support_fields(payload)
        recent_outcomes.append(payload)

    return {
        "generated_at": queue_payload.get("generated_at") or lane_payload.get("generated_at"),
        "mint": mint,
        "lane_state": dict(lane_payload.get("lane_state") or {}),
        "candidate": candidate,
        "history": history,
        "proof_review": {
            "open_trade": open_trade,
            "recent_outcomes": recent_outcomes,
        },
    }


def get_memecoin_v3_proof_workspace(limit: int = 12, lookback_days: int = 30) -> dict:
    queue_payload = get_memecoin_v3_queue(limit=max(int(limit) * 3, 30), lookback_days=max(int(lookback_days), 7))
    lane_payload = get_memecoin_v3_lane_state(limit=max(int(limit), 8), lookback_days=max(int(lookback_days), 7))
    proof_payload = get_proof_stack_summary_data(limit=max(int(limit) * 2, 20))

    groups = dict(queue_payload.get("groups") or {})
    proof_review = dict(proof_payload.get("proof_review") or {})
    queue_by_mint = _v3_queue_candidate_map(queue_payload)

    open_trade_rows = []
    trade_ids = [int(dict(item).get("id") or 0) for item in list(proof_review.get("open_trades") or []) if int(dict(item).get("id") or 0) > 0]
    try:
        from utils.db import get_latest_memecoin_exit_reviews, get_recent_memecoin_exit_review_history  # type: ignore

        exit_reviews = get_latest_memecoin_exit_reviews(trade_ids)
        exit_history = get_recent_memecoin_exit_review_history(trade_ids, limit_per_trade=4)
    except Exception:
        exit_reviews = {}
        exit_history = {}
    for item in list(proof_review.get("open_trades") or []):
        trade = dict(item)
        mint = str(trade.get("mint") or "")
        support_tags = list(trade.get("support_tags") or [])
        support_details = list(trade.get("support_details") or [])
        trade = _v3_strip_legacy_support_fields(trade)
        trade["age_hours"] = _v3_trade_age_hours(trade.get("opened_ts_utc"))
        trade["support_signals"] = _v3_support_signals(
            support_tags,
            support_details,
        )
        trade["exit_review"] = exit_reviews.get(int(trade.get("id") or 0))
        trade["exit_history"] = exit_history.get(int(trade.get("id") or 0), [])
        open_trade_rows.append({
            "candidate": queue_by_mint.get(mint),
            "trade": trade,
        })

    recent_outcomes = []
    for item in list(proof_review.get("recent_outcomes") or [])[: max(int(limit), 1)]:
        outcome = dict(item)
        route = (
            "RELAXED"
            if str(outcome.get("scanner_regime") or "").upper() == "RELAXED_NEAR_MISS"
            else "NORMAL"
        )
        support_tags = list(outcome.get("support_tags") or [])
        support_details = list(outcome.get("support_details") or [])
        recent_outcomes.append({
            "symbol": str(outcome.get("symbol") or ""),
            "mint": str(outcome.get("mint") or ""),
            "route": route,
            "scanner_regime": str(outcome.get("scanner_regime") or "NORMAL"),
            "scanner_relaxation_reason": str(outcome.get("scanner_relaxation_reason") or "") or None,
            "return_4h_pct": outcome.get("return_4h_pct"),
            "return_24h_pct": outcome.get("return_24h_pct"),
            "proof_score": outcome.get("proof_score"),
            "proof_reason": str(outcome.get("proof_reason") or "") or None,
            "has_support": bool(outcome.get("has_support")),
            "support_signals": _v3_support_signals(
                support_tags,
                support_details,
            ),
        })

    return {
        "generated_at": queue_payload.get("generated_at") or proof_payload.get("generated_at"),
        "status": "ACTIVE",
        "detail": "Proof workspace groups live proof candidates, open proof trades, and recent proof outcomes.",
        "lane_state": dict(lane_payload.get("lane_state") or {}),
        "system_health": dict(lane_payload.get("system_health") or {}),
        "counts": {
            "proof_ready": len(list(groups.get("proof_ready") or [])),
            "reinforced_pending": len(list(groups.get("reinforced_pending") or [])),
            "in_proof_trade": len(open_trade_rows),
            "recent_outcomes": len(recent_outcomes),
        },
        "proof_ready": list(groups.get("proof_ready") or [])[: max(int(limit), 1)],
        "reinforced_pending": list(groups.get("reinforced_pending") or [])[: max(int(limit), 1)],
        "in_proof_trade": open_trade_rows[: max(int(limit), 1)],
        "recent_outcomes": recent_outcomes,
        "current_blockers": list(proof_payload.get("current_blockers") or [])[:8],
    }


def _memecoins_v2_retired(route_name: str) -> None:
    raise HTTPException(
        status_code=410,
        detail={
            "error": "endpoint_retired",
            "route": route_name,
            "message": "This V2 memecoin endpoint has been retired. Use the V3 memecoin operator endpoints instead.",
            "replacement_family": "/api/v3/memecoins/*",
        },
    )


@router.get("/proof-stack")
async def memecoins_proof_stack_retired(_: str = Depends(get_current_user)):
    _memecoins_v2_retired("/api/memecoins/proof-stack")


@router.get("/funnel-diagnostics")
async def memecoins_funnel_diagnostics_retired(_: str = Depends(get_current_user)):
    _memecoins_v2_retired("/api/memecoins/funnel-diagnostics")


@router.get("/proof-pipeline")
async def memecoins_proof_pipeline_retired(_: str = Depends(get_current_user)):
    _memecoins_v2_retired("/api/memecoins/proof-pipeline")


@router.get("/graduation")
async def memecoins_graduation_retired(_: str = Depends(get_current_user)):
    _memecoins_v2_retired("/api/memecoins/graduation")


@router.get("/proof-expansion")
async def memecoins_proof_expansion_retired(_: str = Depends(get_current_user)):
    _memecoins_v2_retired("/api/memecoins/proof-expansion")


@router_v3.get("/lane-state")
async def memecoins_v3_lane_state_ep(_: str = Depends(get_current_user), limit: int = 10, lookback_days: int = 30):
    """V3 lane authority + system health contract for the memecoin operator UI."""
    try:
        return await asyncio.to_thread(get_memecoin_v3_lane_state, limit, lookback_days)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router_v3.get("/queue")
async def memecoins_v3_queue_ep(_: str = Depends(get_current_user), limit: int = 25, lookback_days: int = 30):
    """V3 grouped candidate queue contract for the memecoin operator UI."""
    try:
        return await asyncio.to_thread(get_memecoin_v3_queue, limit, lookback_days)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router_v3.get("/candidate/{mint}")
async def memecoins_v3_candidate_ep(mint: str, _: str = Depends(get_current_user), lookback_days: int = 30):
    """V3 candidate detail contract for one mint, including recent history and proof review."""
    try:
        return await asyncio.to_thread(get_memecoin_v3_candidate_detail, mint, lookback_days)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router_v3.get("/proof-workspace")
async def memecoins_v3_proof_workspace_ep(_: str = Depends(get_current_user), limit: int = 12, lookback_days: int = 30):
    """V3 proof workspace contract for proof-ready, reinforced, open-trade, and recent outcome review."""
    try:
        return await asyncio.to_thread(get_memecoin_v3_proof_workspace, limit, lookback_days)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/scanner-diagnostics")
async def memecoins_scanner_diagnostics_ep(_: str = Depends(get_current_user), limit: int = 10):
    """Raw memecoin scanner gate breakdown before cache/proof gating."""
    _ensure_engine_path()
    try:
        from utils.memecoin_scanner import scan_trending_solana_diagnostics  # type: ignore
        return await asyncio.to_thread(scan_trending_solana_diagnostics, None, None, None, limit)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ── Patch 238 — Quality Lane endpoint ─────────────────────────────────────────

@router.get("/quality-lane")
async def memecoins_quality_lane_ep(_: str = Depends(get_current_user)):
    """
    Patch 238 — Quality Lane.
    Classifies current scan-cache signals against structural alpha criteria:
      top_holder_pct < 3.0%  +  liquidity_usd < $50k  +  ≤3 appearances in 72h.
    Also returns recent 48h MSO rows that met those criteria (for outcome tracking).
    """
    _ensure_engine_path()

    def _run() -> dict:
        import json as _json
        from datetime import datetime, timezone, timedelta
        from utils.db import get_conn  # type: ignore

        # ── Gate constants (must match Patch 238 in memecoin_scanner.py) ──────
        MAX_TOP_H    = 3.0
        MAX_LIQ      = 50_000.0
        MAX_APPEAR   = 3
        now          = datetime.now(timezone.utc)
        cutoff_72h   = (now - timedelta(hours=72)).strftime("%Y-%m-%d %H:%M:%S")
        cutoff_48h   = (now - timedelta(hours=48)).strftime("%Y-%m-%d %H:%M:%S")
        now_iso      = now.isoformat()

        with get_conn() as conn:
            conn.row_factory = sqlite3.Row

            # Live scan cache
            row = conn.execute(
                "SELECT value FROM kv_store WHERE key='memecoin_scan_cache'"
            ).fetchone()
            signals = _json.loads(row["value"]) if row else []

            # 72h symbol appearance counts from MSO
            sym_72h_rows = conn.execute(
                "SELECT symbol, COUNT(*) as cnt FROM memecoin_signal_outcomes "
                "WHERE scanned_at >= ? GROUP BY symbol", (cutoff_72h,)
            ).fetchall()
            sym_72h: dict = {r["symbol"]: r["cnt"] for r in sym_72h_rows}

            # Recent 48h MSO rows that met quality criteria — for outcome tracking
            recent_rows = conn.execute(
                "SELECT symbol, mint, score, top_holder_pct, liquidity_usd, "
                "scanned_at, return_24h_pct, status, quality_lane "
                "FROM memecoin_signal_outcomes "
                "WHERE scanned_at >= ? "
                "  AND top_holder_pct > 0 AND top_holder_pct < ? "
                "  AND liquidity_usd > 0  AND liquidity_usd < ? "
                "ORDER BY scanned_at DESC LIMIT 30",
                (cutoff_48h, MAX_TOP_H, MAX_LIQ)
            ).fetchall()

        # ── Classify live candidates ──────────────────────────────────────────
        all_candidates = []
        for s in signals:
            top_h  = float(s.get("top_holder_pct") or 0)
            liq    = float(s.get("liquidity_usd")   or 0)
            sym    = s.get("symbol", "")
            appear = sym_72h.get(sym, 0)

            fails = []
            if not (0 < top_h < MAX_TOP_H):
                fails.append(f"top_h={top_h:.1f}% (need <{MAX_TOP_H}%)")
            if not (0 < liq < MAX_LIQ):
                fails.append(f"liq=${liq:,.0f} (need <${MAX_LIQ:,.0f})")
            if appear > MAX_APPEAR:
                fails.append(f"72h_appearances={appear} (need ≤{MAX_APPEAR})")

            qualifies = (len(fails) == 0) and top_h > 0 and liq > 0
            all_candidates.append({
                "symbol":           sym,
                "mint":             s.get("mint", ""),
                "score":            s.get("score"),
                "top_holder_pct":   round(top_h, 2),
                "liquidity_usd":    round(liq, 0),
                "appearances_72h":  appear,
                "qualifies":        qualifies,
                "fails":            fails,
                "scanned_at":       s.get("scanned_at", ""),
            })

        qualified = [c for c in all_candidates if c["qualifies"]]

        # ── Build recent outcome history ──────────────────────────────────────
        recent = []
        for r in recent_rows:
            sym    = r["symbol"]
            appear = sym_72h.get(sym, 0)
            ret    = r["return_24h_pct"]
            recent.append({
                "symbol":           sym,
                "mint":             r["mint"],
                "score":            r["score"],
                "top_holder_pct":   round(float(r["top_holder_pct"]), 2) if r["top_holder_pct"] else None,
                "liquidity_usd":    round(float(r["liquidity_usd"]), 0)  if r["liquidity_usd"]  else None,
                "scanned_at":       r["scanned_at"],
                "return_24h_pct":   round(float(ret), 2) if ret is not None else None,
                "status":           r["status"],
                "appearances_72h":  appear,
                "quality_lane_tag": bool(r["quality_lane"]) if r["quality_lane"] is not None else None,
            })

        # WR on recent_rows that have outcomes
        evaluated  = [r for r in recent if r["return_24h_pct"] is not None]
        wins       = sum(1 for r in evaluated if r["return_24h_pct"] > 0)
        recent_wr  = round(wins / len(evaluated) * 100, 1) if evaluated else None

        return {
            "live": {
                "count":      len(qualified),
                "empty":      len(qualified) == 0,
                "candidates": qualified,
            },
            "all_live_count": len(all_candidates),
            "recent_48h": {
                "rows":      recent,
                "n":         len(recent),
                "evaluated": len(evaluated),
                "wr":        recent_wr,
            },
            "criteria": {
                "max_top_holder_pct":  MAX_TOP_H,
                "max_liquidity_usd":   MAX_LIQ,
                "max_appearances_72h": MAX_APPEAR,
                "description": (
                    f"top_holder_pct < {MAX_TOP_H}% "
                    f"+ liquidity_usd < ${MAX_LIQ:,.0f} "
                    f"+ ≤{MAX_APPEAR} appearances in last 72h"
                ),
            },
            "generated_at": now_iso,
        }

    try:
        return await asyncio.to_thread(_run)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ── Patch 239 — Second-Leg Candidate Detector ─────────────────────────────────


# ── Second-Leg / Lifecycle Lane — Patch 241 — lifecycle-driven refactor ───────
# Replaces Patch 239 inline-computation endpoint.
# Now reads from symbol_lifecycle (computed every 5 min by lifecycle_engine.py).
# No window counting. No MSO recomputation. Single SQL query.

@router.get("/second-leg")
async def memecoins_second_leg_ep(_: str = Depends(get_current_user)):
    _memecoins_v2_retired("/api/memecoins/second-leg")
    """
    Patch 241 — Lifecycle-driven persistent-demand operator lane.

    Primary candidates:  RELOAD + REVIVAL + first_leg_confirmed
                         ACTIVE + first_leg_confirmed (if top_h passes)
    Watching:            DORMANT + first_leg_confirmed
    Excluded:            COOLING, DEAD, no first_leg_confirmed
    """
    _ensure_engine_path()

    def _run() -> dict:
        import sqlite3
        from datetime import datetime, timezone
        from utils.db import get_conn  # type: ignore

        TOP_H_MAX = 6.0   # top_holder survivor filter (from most recent MSO scan)

        with get_conn() as conn:
            conn.row_factory = sqlite3.Row

            rows = conn.execute("""
                SELECT
                    lc.symbol,
                    lc.lifecycle_state,
                    lc.n_windows,
                    lc.best_return_pct,
                    lc.liq_current,
                    lc.liq_trend,
                    lc.pullback_depth_pct,
                    lc.hours_since_last_scan,
                    lc.hours_since_last_window,
                    lc.vol_acc_current,
                    lc.liq_floor,
                    lc.peak_liq_last_window,
                    lc.first_leg_confirmed,
                    lc.multi_leg_confirmed,
                    lc.survivor_confirmed,
                    lc.last_computed_at,
                    lc.state_entered_at,
                    lc.first_seen_at,
                    COALESCE(mso.top_holder_pct, 0.0) AS top_holder_pct,
                    lc.move_phase,
                    lc.phase_score,
                    lc.entry_window,
                    lc.fuel_quality,
                    perf.perf_n,
                    perf.perf_avg,
                    lmcap.last_mcap,
                    corr.corr_n,
                    corr.corr_avg
                FROM symbol_lifecycle lc
                LEFT JOIN (
                    SELECT symbol, top_holder_pct,
                           ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY scanned_at DESC) AS rn
                    FROM   memecoin_signal_outcomes
                ) mso ON mso.symbol = lc.symbol AND mso.rn = 1
                LEFT JOIN (
                    SELECT symbol,
                           COUNT(*) AS perf_n,
                           AVG(return_24h_pct) AS perf_avg
                    FROM memecoin_signal_outcomes
                    WHERE status = 'COMPLETE' AND return_24h_pct IS NOT NULL
                    GROUP BY symbol
                ) perf ON perf.symbol = lc.symbol
                LEFT JOIN (
                    SELECT symbol, mcap_at_scan AS last_mcap
                    FROM memecoin_signal_outcomes
                    WHERE source = 'SCANNER'
                    GROUP BY symbol
                    HAVING MAX(scanned_at)
                ) lmcap ON lmcap.symbol = lc.symbol
                LEFT JOIN (
                    SELECT symbol,
                           COUNT(*) AS corr_n,
                           AVG(return_24h_pct) AS corr_avg
                    FROM memecoin_signal_outcomes
                    WHERE status = 'COMPLETE' AND return_24h_pct IS NOT NULL
                      AND scanned_at >= date('now', '-14 days')
                    GROUP BY symbol
                ) corr ON corr.symbol = lc.symbol
                WHERE lc.first_leg_confirmed = 1
                  AND lc.lifecycle_state IN ('RELOAD', 'REVIVAL', 'ACTIVE', 'DORMANT')
                ORDER BY
                    CASE lc.lifecycle_state
                        WHEN 'RELOAD'   THEN 1
                        WHEN 'REVIVAL'  THEN 2
                        WHEN 'ACTIVE'   THEN 3
                        WHEN 'DORMANT'  THEN 4
                    END,
                    lc.multi_leg_confirmed DESC,
                    lc.n_windows            DESC,
                    lc.best_return_pct      DESC
            """).fetchall()

        # ── Ranking model — Patch 242 (shared via lifecycle_engine, Patch 243) ────
        from utils.lifecycle_engine import compute_rank as _compute_rank  # type: ignore

        # ── Classify and rank ────────────────────────────────────────────────

        primary  = []
        watching = []

        _MCAP_FLOOR = 1_500_000

        for r in rows:
            rec   = dict(r)
            state = rec["lifecycle_state"]
            top_h = float(rec.get("top_holder_pct") or 0)
            top_h_ok = top_h == 0 or top_h < TOP_H_MAX

            score, priority, rank_factors = _compute_rank(rec)
            rec["rank_score"]   = score
            rec["priority"]     = priority
            rec["rank_factors"] = rank_factors

            # Patch 303b: policy gates — PROVEN_NEGATIVE, CASUALTY, and sub-floor
            # tokens must not appear in PRIMARY (action surface).  They stay in
            # watching so the operator can still see them as informational context.
            # Patch 306: CASUALTY check (correction-period avg < -30% with n≥2)
            # closes the latent DISTRUST gap; DISTRUST = PROVEN_NEGATIVE OR CASUALTY.
            _perf_n   = int(rec.get("perf_n")  or 0)
            _perf_avg = float(rec.get("perf_avg") or 0) if rec.get("perf_avg") is not None else 0.0
            _last_mcap = rec.get("last_mcap")
            _corr_n   = int(rec.get("corr_n")  or 0)
            _corr_avg = float(rec.get("corr_avg") or 0) if rec.get("corr_avg") is not None else 0.0
            _proven_neg  = _perf_n >= 15 and _perf_avg < -10
            _casualty    = _corr_n >= 2  and _corr_avg < -30.0   # Patch 306
            _sub_floor   = _last_mcap is not None and float(_last_mcap) < _MCAP_FLOOR
            _gate_failed = _proven_neg or _casualty or _sub_floor

            if _gate_failed:
                if _proven_neg:
                    rec["note"] = f"PROVEN_NEGATIVE (avg {_perf_avg:.1f}%, n={_perf_n}) — watching only"
                elif _casualty:
                    rec["note"] = f"DISTRUST/CASUALTY (corr avg {_corr_avg:.1f}%, n={_corr_n}) — watching only"  # Patch 306
                else:
                    rec["note"] = f"sub-floor mcap ${_last_mcap:,.0f} — watching only"
                watching.append(rec)
                continue

            if state in ("RELOAD", "REVIVAL") or (state == "ACTIVE" and top_h_ok):
                if top_h_ok:
                    primary.append(rec)
                else:
                    rec["note"] = f"top_h {top_h:.1f}% (>{TOP_H_MAX}%)"
                    watching.append(rec)
            else:
                if not top_h_ok:
                    rec["note"] = f"top_h {top_h:.1f}% (>{TOP_H_MAX}%)"
                watching.append(rec)

        # Sort by rank_score DESC within each section
        primary.sort( key=lambda x: -x["rank_score"])
        watching.sort(key=lambda x: -x["rank_score"])

        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return {
            "primary":      primary,
            "watching":     watching,
            "count":        len(primary),
            "empty":        len(primary) == 0,
            "generated_at": now_iso,
        }

    try:
        return await asyncio.to_thread(_run)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/lifecycle")
async def memecoins_lifecycle_ep(_: str = Depends(get_current_user)):
    _memecoins_v2_retired("/api/memecoins/lifecycle")
    """
    Return all rows from symbol_lifecycle.
    Used for verification and as the data source for future operator lanes.
    """
    _ensure_engine_path()

    def _run() -> dict:
        import sqlite3
        from utils.db import get_conn  # type: ignore

        with get_conn() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT symbol, lifecycle_state, n_windows, best_return_pct,
                       liq_floor, liq_current, liq_trend, pullback_depth_pct,
                       hours_since_last_window, hours_since_last_scan,
                       peak_liq_last_window, vol_acc_current,
                       first_leg_confirmed, multi_leg_confirmed,
                       survivor_confirmed, last_computed_at
                FROM   symbol_lifecycle
                ORDER  BY
                    CASE lifecycle_state
                        WHEN "RELOAD"   THEN 1
                        WHEN "REVIVAL"  THEN 2
                        WHEN "ACTIVE"   THEN 3
                        WHEN "EXTENDED" THEN 4
                        WHEN "DORMANT"  THEN 5
                        WHEN "COOLING"  THEN 6
                        WHEN "DEAD"     THEN 7
                        ELSE 8
                    END,
                    first_leg_confirmed DESC,
                    n_windows DESC
            """).fetchall()

        symbols = [dict(r) for r in rows]

        # Counts by state
        from collections import Counter
        state_counts = dict(Counter(s["lifecycle_state"] for s in symbols))

        return {
            "symbols":      symbols,
            "total":        len(symbols),
            "state_counts": state_counts,
            "generated_at": __import__("datetime").datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        }

    try:
        return await asyncio.to_thread(_run)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/lifecycle-validation")
async def memecoins_lifecycle_validation_ep(_: str = Depends(get_current_user)):
    """
    Patch 243 — Forward-validation stats for the lifecycle ranking lane.
    Returns win-rate / avg return by priority tier, plus recent resolved snapshots.
    """
    _ensure_engine_path()

    def _run() -> dict:
        import sqlite3
        from utils.db import get_conn  # type: ignore

        with get_conn() as conn:
            conn.row_factory = sqlite3.Row

            # ── Tier summary ─────────────────────────────────────────────────
            tier_rows = conn.execute("""
                SELECT
                    priority,
                    COUNT(*)                                             AS total,
                    SUM(CASE WHEN outcome_is_win IS NOT NULL THEN 1 ELSE 0 END)  AS resolved,
                    SUM(CASE WHEN outcome_is_win = 1        THEN 1 ELSE 0 END)  AS wins,
                    AVG(CASE WHEN outcome_return_pct IS NOT NULL
                             THEN outcome_return_pct END)               AS avg_return
                FROM lifecycle_rank_snapshots
                GROUP BY priority
                ORDER BY CASE priority WHEN 'HIGH' THEN 1 WHEN 'MEDIUM' THEN 2 ELSE 3 END
            """).fetchall()

            tiers = []
            for r in tier_rows:
                resolved = int(r["resolved"] or 0)
                wins     = int(r["wins"] or 0)
                tiers.append({
                    "priority":   r["priority"],
                    "total":      int(r["total"]),
                    "resolved":   resolved,
                    "wins":       wins,
                    "win_rate":   round(wins / resolved * 100, 1) if resolved > 0 else None,
                    "avg_return": round(float(r["avg_return"]), 1) if r["avg_return"] is not None else None,
                })

            # ── Recent resolved snapshots (last 20) ──────────────────────────
            recent_rows = conn.execute("""
                SELECT symbol, snapped_at, lifecycle_state, priority, rank_score,
                       outcome_return_pct, outcome_is_win, outcome_hours
                FROM lifecycle_rank_snapshots
                WHERE outcome_is_win IS NOT NULL
                ORDER BY snapped_at DESC
                LIMIT 20
            """).fetchall()
            recent = [dict(r) for r in recent_rows]

            # ── Totals ───────────────────────────────────────────────────────
            totals = conn.execute("""
                SELECT COUNT(*) AS total,
                       SUM(CASE WHEN outcome_is_win IS NOT NULL THEN 1 ELSE 0 END) AS resolved,
                       SUM(CASE WHEN outcome_is_win = 1 THEN 1 ELSE 0 END) AS wins
                FROM lifecycle_rank_snapshots
            """).fetchone()

            total_snap   = int(totals["total"]    or 0)
            total_res    = int(totals["resolved"] or 0)
            total_wins   = int(totals["wins"]     or 0)
            overall_wr   = round(total_wins / total_res * 100, 1) if total_res > 0 else None

        import datetime as _dt
        return {
            "tiers":        tiers,
            "recent":       recent,
            "total_snapshots": total_snap,
            "total_resolved":  total_res,
            "overall_win_rate": overall_wr,
            "generated_at": _dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        }

    try:
        return await asyncio.to_thread(_run)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/early-watch")
async def memecoins_early_watch_ep(_: str = Depends(get_current_user)):
    _memecoins_v2_retired("/api/memecoins/early-watch")
    """
    Patch 244 + v3 discovery expansion — Early Watch blends:
      1. lifecycle-backed pre-confirmation names already in symbol_lifecycle
      2. fresh discovery-ingress names that are liquid enough to merit attention
         even before lifecycle confirmation exists

    This keeps the confirmed survivor lane clean while giving genuinely new names
    a visible operator surface earlier.
    """
    _ensure_engine_path()

    def _run() -> dict:
        import sqlite3
        from datetime import datetime, timezone
        from utils.db import get_conn  # type: ignore

        EARLY_WATCH_HOURS   = 96.0     # first_seen_at within last 4 days
        VA_MIN              = 3.0      # vol_acc floor to be worth watching
        MAX_CANDIDATES      = 12
        DISCOVERY_HOURS     = 48.0     # recent ingress-only names
        DISCOVERY_LIQ_MIN   = 50_000.0
        DISCOVERY_TOP_H_MAX = 12.0

        with get_conn() as conn:
            conn.row_factory = sqlite3.Row

            # ── Source A: lifecycle-backed early watch ───────────────────────
            lifecycle_rows = conn.execute("""
                SELECT
                    symbol,
                    lifecycle_state,
                    vol_acc_current,
                    liq_current,
                    liq_trend,
                    hours_since_last_scan,
                    first_seen_at,
                    state_entered_at,
                    n_windows,
                    pullback_depth_pct,
                    liq_floor,
                    peak_liq_last_window,
                    last_computed_at
                FROM symbol_lifecycle
                WHERE first_seen_at IS NOT NULL
                  AND first_seen_at >= datetime('now', :window)
                  AND hours_since_last_scan < 8
                  AND vol_acc_current >= :va_min
                  AND liq_trend IN ('flat', 'growing')
                  AND first_leg_confirmed = 0
                  AND n_windows <= 2
                ORDER BY vol_acc_current DESC, liq_current DESC
                LIMIT :limit
            """, {
                "window": f"-{int(EARLY_WATCH_HOURS)} hours",
                "va_min": VA_MIN,
                "limit":  MAX_CANDIDATES,
            }).fetchall()

            # ── Source B: discovery-ingress names not yet lifecycle-confirmed ─
            # These are newer than the survivor lane and can still be worth
            # operator attention when liquidity and attention quality are present.
            discovery_rows = conn.execute("""
                SELECT
                    d.symbol,
                    d.mint,
                    d.scanned_at               AS first_seen_at,
                    d.liquidity_usd            AS liq_current,
                    d.vol_acceleration         AS vol_acc_current,
                    d.top_holder_pct,
                    d.attention_infrastructure,
                    d.boost_active,
                    d.attention_quality,
                    d.token_age_days,
                    d.score,
                    ROUND((julianday('now') - julianday(d.scanned_at)) * 24.0, 1) AS hours_since_last_scan,
                    lc.lifecycle_state,
                    lc.first_leg_confirmed,
                    lc.n_windows,
                    lc.liq_trend,
                    lc.pullback_depth_pct,
                    lc.state_entered_at,
                    lc.last_computed_at
                FROM (
                    SELECT m1.*
                    FROM memecoin_signal_outcomes m1
                    INNER JOIN (
                        SELECT mint, MAX(scanned_at) AS max_scanned_at
                        FROM memecoin_signal_outcomes
                        WHERE source = 'DISCOVERY'
                          AND scanned_at >= datetime('now', :discovery_window)
                        GROUP BY mint
                    ) latest
                      ON latest.mint = m1.mint
                     AND latest.max_scanned_at = m1.scanned_at
                    WHERE m1.source = 'DISCOVERY'
                ) d
                LEFT JOIN symbol_lifecycle lc
                  ON lc.mint = d.mint
                WHERE (lc.first_leg_confirmed IS NULL OR lc.first_leg_confirmed = 0)
                  AND d.liquidity_usd >= :liq_min
                  AND COALESCE(d.top_holder_pct, 0) <= :top_h_max
                  AND (
                        COALESCE(d.attention_quality, '') IN ('STRONG', 'MODERATE')
                        OR COALESCE(d.boost_active, 0) = 1
                        OR COALESCE(d.attention_infrastructure, '') NOT IN ('', 'ABSENT', 'NONE', '0')
                        OR COALESCE(d.vol_acceleration, 0) >= :va_min
                  )
                ORDER BY
                    COALESCE(d.boost_active, 0) DESC,
                    CASE COALESCE(d.attention_quality, '')
                        WHEN 'STRONG' THEN 2
                        WHEN 'MODERATE' THEN 1
                        ELSE 0
                    END DESC,
                    COALESCE(d.vol_acceleration, 0) DESC,
                    COALESCE(d.liquidity_usd, 0) DESC
                LIMIT :limit
            """, {
                "discovery_window": f"-{int(DISCOVERY_HOURS)} hours",
                "liq_min": DISCOVERY_LIQ_MIN,
                "top_h_max": DISCOVERY_TOP_H_MAX,
                "va_min": VA_MIN,
                "limit": MAX_CANDIDATES,
            }).fetchall()

        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        candidates = []
        seen_symbols: set[str] = set()

        def _hours_since(ts_val) -> float | None:
            try:
                from datetime import datetime as _dt
                raw = str(ts_val or "")
                for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f"):
                    try:
                        dt = _dt.strptime(raw, fmt).replace(tzinfo=timezone.utc)
                        return round((datetime.now(timezone.utc) - dt).total_seconds() / 3600.0, 1)
                    except ValueError:
                        continue
                if raw.endswith("Z"):
                    dt = _dt.fromisoformat(raw.replace("Z", "+00:00"))
                    return round((datetime.now(timezone.utc) - dt).total_seconds() / 3600.0, 1)
            except Exception:
                return None
            return None

        for r in lifecycle_rows:
            rec = dict(r)
            rec["surface"] = "LIFECYCLE"
            rec["discovery_stage"] = "LIFECYCLE_EARLY"
            rec["hours_since_first_seen"] = _hours_since(rec.get("first_seen_at"))
            rec["rank_score"] = (
                float(rec.get("vol_acc_current") or 0) * 10.0
                + float(rec.get("liq_current") or 0) / 10_000.0
            )
            sym = str(rec.get("symbol") or "").upper()
            if sym:
                seen_symbols.add(sym)
            candidates.append(rec)

        for r in discovery_rows:
            rec = dict(r)
            sym = str(rec.get("symbol") or "").upper()
            if not sym or sym in seen_symbols:
                continue
            rec["lifecycle_state"] = rec.get("lifecycle_state") or "DISCOVERY"
            rec["liq_trend"] = rec.get("liq_trend") or "flat"
            rec["n_windows"] = int(rec.get("n_windows") or 0)
            rec["pullback_depth_pct"] = float(rec.get("pullback_depth_pct") or 0.0)
            rec["liq_floor"] = float(rec.get("liq_current") or 0.0)
            rec["peak_liq_last_window"] = float(rec.get("liq_current") or 0.0)
            rec["surface"] = "DISCOVERY"
            rec["discovery_stage"] = "INGRESS"
            rec["hours_since_first_seen"] = _hours_since(rec.get("first_seen_at"))
            rec["rank_score"] = (
                (20.0 if int(rec.get("boost_active") or 0) == 1 else 0.0)
                + (12.0 if str(rec.get("attention_quality") or "") == "STRONG" else 6.0 if str(rec.get("attention_quality") or "") == "MODERATE" else 0.0)
                + float(rec.get("vol_acc_current") or 0) * 8.0
                + float(rec.get("liq_current") or 0) / 15_000.0
            )
            seen_symbols.add(sym)
            candidates.append(rec)

        candidates.sort(key=lambda rec: float(rec.get("rank_score") or 0), reverse=True)
        candidates = candidates[:MAX_CANDIDATES]

        return {
            "candidates":        candidates,
            "count":             len(candidates),
            "lifecycle_count":   sum(1 for c in candidates if c.get("surface") == "LIFECYCLE"),
            "discovery_count":   sum(1 for c in candidates if c.get("surface") == "DISCOVERY"),
            "generated_at":      now_iso,
        }

    try:
        return await asyncio.to_thread(_run)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/lifecycle-attribution")
async def memecoins_lifecycle_attribution_ep(_: str = Depends(get_current_user)):
    """
    Patch 245 — Lifecycle attribution: breakdown of resolved snapshots by
    lifecycle_state, priority, freshness_bucket, and surface (confirmed vs
    early_watch).  Used to validate whether the candidate-finder's
    classifications are actually predictive.
    """
    _ensure_engine_path()

    def _run() -> dict:
        from utils.lifecycle_validation import compute_lifecycle_attribution  # type: ignore
        return compute_lifecycle_attribution()

    try:
        return await asyncio.to_thread(_run)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ── Patch 272: Paper Trade Lifecycle Attribution ──────────────────────────────

@router.get("/paper-trade-attribution")
async def paper_trade_attribution_ep(_: str = Depends(get_current_user)):
    """
    Patch 272 — Attribution of closed paper trades by lifecycle gate conditions
    captured at entry time (entry_fuel_quality, entry_window, entry_move_phase,
    entry_score).

    Returns:
      - buckets: grouped by fuel_quality × entry_window with n / win_rate / avg_pnl
      - trades: individual closed trade records with entry context
      - summary: total closed, win_rate, avg_pnl across all context-tagged trades
    """
    _ensure_engine_path()

    def _run() -> dict:
        import sqlite3
        with sqlite3.connect(_db_path()) as conn:
            conn.row_factory = sqlite3.Row

            # ── Fuel × Window buckets ────────────────────────────────────────
            bucket_rows = conn.execute("""
                SELECT
                    COALESCE(entry_fuel_quality, 'UNKNOWN') AS fuel,
                    COALESCE(entry_window,       'UNKNOWN') AS window,
                    COUNT(*)                                AS n,
                    SUM(CASE WHEN pnl_pct > 0 THEN 1 ELSE 0 END) AS wins,
                    ROUND(AVG(pnl_pct), 2)                  AS avg_pnl,
                    ROUND(MIN(pnl_pct), 2)                  AS min_pnl,
                    ROUND(MAX(pnl_pct), 2)                  AS max_pnl
                FROM memecoin_trades
                WHERE closed_ts_utc IS NOT NULL
                GROUP BY fuel, window
                ORDER BY n DESC
            """).fetchall()

            buckets = []
            for r in bucket_rows:
                n = r["n"] or 0
                wins = r["wins"] or 0
                buckets.append({
                    "fuel":     r["fuel"],
                    "window":   r["window"],
                    "n":        n,
                    "wins":     wins,
                    "win_rate": round(wins / n * 100, 1) if n > 0 else 0,
                    "avg_pnl":  r["avg_pnl"],
                    "min_pnl":  r["min_pnl"],
                    "max_pnl":  r["max_pnl"],
                })

            # ── Individual closed trades with context ────────────────────────
            trade_rows = conn.execute("""
                SELECT
                    id, symbol, pnl_pct, exit_reason,
                    opened_ts_utc, closed_ts_utc,
                    entry_fuel_quality, entry_window,
                    entry_move_phase, entry_score,
                    is_pilot
                FROM memecoin_trades
                WHERE closed_ts_utc IS NOT NULL
                ORDER BY closed_ts_utc DESC
                LIMIT 50
            """).fetchall()

            trades = [dict(r) for r in trade_rows]

            # ── Summary across context-tagged rows only ──────────────────────
            tagged = [t for t in trades if t["entry_fuel_quality"] is not None]
            all_closed = len(trades)
            n_tagged  = len(tagged)
            n_wins    = sum(1 for t in tagged if (t["pnl_pct"] or 0) > 0)
            avg_pnl   = (
                round(sum((t["pnl_pct"] or 0) for t in tagged) / n_tagged, 2)
                if n_tagged > 0 else None
            )

            return {
                "buckets":         buckets,
                "trades":          trades,
                "summary": {
                    "total_closed":    all_closed,
                    "context_tagged":  n_tagged,
                    "untagged":        all_closed - n_tagged,
                    "tagged_win_rate": round(n_wins / n_tagged * 100, 1) if n_tagged > 0 else None,
                    "tagged_avg_pnl":  avg_pnl,
                },
                "generated_at": __import__("datetime").datetime.utcnow().isoformat(),
            }

    try:
        return await asyncio.to_thread(_run)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ── Patch 249: Discovery Monitor ──────────────────────────────────────────────

@router.get("/discovery-monitor")
async def memecoins_discovery_monitor_ep(_: str = Depends(get_current_user)):
    """
    Patch 249 — Discovery layer monitor.

    Returns all DISCOVERY-sourced rows ingested in the last 48h, joined with
    current symbol_lifecycle state.  Classifies each coin as:
      CANDIDATE   — in lifecycle + state IN (RELOAD, REVIVAL, ACTIVE) + first_leg_confirmed
      EARLY_WATCH — in lifecycle but not a confirmed candidate
      TRACKING    — has any lifecycle row
      NONE        — not yet in lifecycle

    Used to evaluate whether the discovery layer is adding useful new names
    or just ingesting noise.
    """
    _ensure_engine_path()

    def _run() -> dict:
        import sqlite3
        from datetime import datetime, timezone, timedelta

        with sqlite3.connect(_db_path()) as conn:
            conn.row_factory = sqlite3.Row

            cutoff = (datetime.now(timezone.utc) - timedelta(hours=48)).strftime("%Y-%m-%d %H:%M:%S")

            # Latest discovery row per mint (most recent ingestion within 48h).
            # Patch 254: attention fields (attention_infrastructure, boost_active,
            # attention_quality) included from MSO row written at ingress time.
            rows = conn.execute("""
                SELECT
                    m.symbol,
                    m.mint,
                    m.scanned_at                 AS ingested_at,
                    m.liquidity_usd              AS liq_at_ingress,
                    m.volume_24h                 AS vol24_at_ingress,
                    m.token_age_days             AS age_at_ingress,
                    m.attention_infrastructure,
                    m.boost_active,
                    m.attention_quality,
                    lc.lifecycle_state,
                    lc.liq_current,
                    lc.vol_acc_current,
                    lc.first_leg_confirmed,
                    lc.fuel_quality,
                    lc.entry_window,
                    lc.move_phase
                FROM (
                    SELECT symbol, mint,
                           MAX(scanned_at) AS scanned_at,
                           liquidity_usd, volume_24h, token_age_days,
                           attention_infrastructure, boost_active, attention_quality
                    FROM   memecoin_signal_outcomes
                    WHERE  source = 'DISCOVERY' AND scanned_at >= ?
                    GROUP  BY mint
                ) m
                LEFT JOIN symbol_lifecycle lc ON lc.symbol = m.symbol
                ORDER BY m.scanned_at DESC
            """, (cutoff,)).fetchall()

            CANDIDATE_STATES = ("RELOAD", "REVIVAL", "ACTIVE")

            entries = []
            counts  = {"CANDIDATE": 0, "EARLY_WATCH": 0, "TRACKING": 0, "NONE": 0}

            for r in rows:
                rec = dict(r)

                # Classify
                lc_state = rec.get("lifecycle_state")
                flc      = int(rec.get("first_leg_confirmed") or 0)

                if lc_state and lc_state in CANDIDATE_STATES and flc:
                    status = "CANDIDATE"
                elif lc_state:
                    status = "EARLY_WATCH" if lc_state not in ("DEAD",) else "TRACKING"
                else:
                    status = "NONE"

                counts[status] += 1
                rec["status"] = status
                entries.append(rec)

            now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            return {
                "entries":      entries,
                "counts":       counts,
                "total":        len(entries),
                "generated_at": now_iso,
            }

    try:
        return await asyncio.to_thread(_run)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/discovery-candidates")
async def memecoins_discovery_candidates_ep(_: str = Depends(get_current_user)):
    _memecoins_v2_retired("/api/memecoins/discovery-candidates")
    """
    v3 discovery lane — unified fresh-candidate surface.

    Blends the best current discovery inputs into one ranked list:
      - fresh DISCOVERY ingress rows
      - lifecycle-backed early-watch qualification
      - quality-lane qualification
      - recent scan-best presence

    This does not replace the survivor lane. It gives the operator one compact
    answer to: "what genuinely new names are worth attention right now?"
    """
    _ensure_engine_path()

    def _run() -> dict:
        import sqlite3
        from datetime import datetime, timezone, timedelta
        from utils.db import get_conn, get_recent_scan_bests  # type: ignore

        now = datetime.now(timezone.utc)
        cutoff_48h = (now - timedelta(hours=48)).strftime("%Y-%m-%d %H:%M:%S")
        cutoff_72h = (now - timedelta(hours=72)).strftime("%Y-%m-%d %H:%M:%S")
        now_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")

        MAX_TOP_H_QL = 3.0
        MAX_LIQ_QL = 50_000.0
        MAX_APPEAR_72H = 3
        DISCOVERY_LIMIT = 16

        recent_scan_bests = get_recent_scan_bests(lookback_hours=12, limit=50)
        scan_best_by_symbol = {str(r.get("symbol") or "").upper(): r for r in recent_scan_bests if r.get("symbol")}

        with get_conn() as conn:
            conn.row_factory = sqlite3.Row

            sym_72h_rows = conn.execute(
                "SELECT symbol, COUNT(*) as cnt FROM memecoin_signal_outcomes "
                "WHERE scanned_at >= ? GROUP BY symbol",
                (cutoff_72h,),
            ).fetchall()
            appearances_72h = {str(r["symbol"]).upper(): int(r["cnt"] or 0) for r in sym_72h_rows if r["symbol"]}

            rows = conn.execute("""
                SELECT
                    d.symbol,
                    d.mint,
                    d.scanned_at,
                    d.score,
                    d.liquidity_usd,
                    d.volume_24h,
                    d.token_age_days,
                    d.vol_acceleration,
                    d.top_holder_pct,
                    d.attention_infrastructure,
                    d.boost_active,
                    d.attention_quality,
                    lc.lifecycle_state,
                    lc.first_leg_confirmed,
                    lc.n_windows,
                    lc.liq_trend,
                    lc.liq_current,
                    lc.vol_acc_current,
                    lc.first_seen_at,
                    lc.state_entered_at,
                    lc.hours_since_last_scan,
                    lc.entry_window,
                    lc.fuel_quality,
                    lc.move_phase
                FROM (
                    SELECT m1.*
                    FROM memecoin_signal_outcomes m1
                    INNER JOIN (
                        SELECT mint, MAX(scanned_at) AS max_scanned_at
                        FROM memecoin_signal_outcomes
                        WHERE source = 'DISCOVERY'
                          AND scanned_at >= ?
                        GROUP BY mint
                    ) latest
                      ON latest.mint = m1.mint
                     AND latest.max_scanned_at = m1.scanned_at
                    WHERE m1.source = 'DISCOVERY'
                ) d
                LEFT JOIN symbol_lifecycle lc
                  ON lc.mint = d.mint
                ORDER BY d.scanned_at DESC
                LIMIT 120
            """, (cutoff_48h,)).fetchall()

        def _hours_since(ts_val) -> float | None:
            try:
                raw = str(ts_val or "")
                if not raw:
                    return None
                normalized = raw.replace(" ", "T")
                if normalized.endswith("Z"):
                    dt = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
                else:
                    dt = datetime.fromisoformat(normalized)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                return round((now - dt.astimezone(timezone.utc)).total_seconds() / 3600.0, 1)
            except Exception:
                return None

        candidates = []
        seen_symbols: set[str] = set()
        for row in rows:
            rec = dict(row)
            symbol = str(rec.get("symbol") or "").upper()
            if not symbol or symbol in seen_symbols:
                continue

            liq = float(rec.get("liquidity_usd") or 0.0)
            top_h = rec.get("top_holder_pct")
            top_h_val = float(top_h) if top_h is not None else None
            vol_acc = rec.get("vol_acceleration")
            vol_acc_val = float(vol_acc) if vol_acc is not None else None
            attention_quality = str(rec.get("attention_quality") or "")
            attention_infra = str(rec.get("attention_infrastructure") or "")
            boost_active = int(rec.get("boost_active") or 0)
            lifecycle_state = rec.get("lifecycle_state")
            first_leg_confirmed = int(rec.get("first_leg_confirmed") or 0)
            n_windows = int(rec.get("n_windows") or 0)
            liq_trend = str(rec.get("liq_trend") or "flat")
            raw_hours_since_last_scan = rec.get("hours_since_last_scan")
            hsls = float(raw_hours_since_last_scan) if raw_hours_since_last_scan is not None else None
            appear_72h = appearances_72h.get(symbol, 0)
            scan_best = scan_best_by_symbol.get(symbol)

            quality_lane = (
                top_h_val is not None and 0 < top_h_val < MAX_TOP_H_QL
                and 0 < liq < MAX_LIQ_QL
                and appear_72h <= MAX_APPEAR_72H
            )
            lifecycle_early = (
                lifecycle_state is not None
                and first_leg_confirmed == 0
                and (hsls is not None and hsls < 8.0)
                and (vol_acc_val or 0.0) >= 3.0
                and liq_trend in ("flat", "growing")
                and n_windows <= 2
            )
            ingress_qualified = (
                liq >= 50_000.0
                and (
                    attention_quality in ("STRONG", "MODERATE")
                    or boost_active == 1
                    or attention_infra not in ("", "ABSENT", "NONE", "0")
                    or (vol_acc_val or 0.0) >= 3.0
                )
            )

            if not (quality_lane or lifecycle_early or ingress_qualified or scan_best):
                continue

            score = 0.0
            badges = []
            if lifecycle_early:
                score += 35
                badges.append("LIFECYCLE_EARLY")
            if quality_lane:
                score += 18
                badges.append("QUALITY_LANE")
            if scan_best:
                score += 14
                badges.append("SCAN_BEST")
            if boost_active == 1:
                score += 12
                badges.append("BOOSTED")
            if attention_quality == "STRONG":
                score += 10
                badges.append("ATTN_STRONG")
            elif attention_quality == "MODERATE":
                score += 5
                badges.append("ATTN_MODERATE")
            if vol_acc_val is not None:
                score += min(vol_acc_val * 1.8, 18)
            score += min(liq / 40_000.0, 12)

            stage = (
                "LIFECYCLE_EARLY" if lifecycle_early else
                "DISCOVERY_PLUS" if ingress_qualified and quality_lane else
                "DISCOVERY_INGRESS"
            )
            display_first_seen = (
                rec.get("first_seen_at") if lifecycle_early else rec.get("scanned_at")
            ) or rec.get("scanned_at")
            display_hours_since_first_seen = _hours_since(display_first_seen)
            display_hours_since_last_scan = (
                round(hsls, 1) if lifecycle_early and hsls is not None
                else _hours_since(rec.get("scanned_at"))
            )

            candidates.append({
                "symbol": symbol,
                "mint": rec.get("mint"),
                "stage": stage,
                "rank_score": round(score, 2),
                "source_flags": badges,
                "liquidity_usd": round(liq, 0) if liq else None,
                "vol_acceleration": round(vol_acc_val, 2) if vol_acc_val is not None else None,
                "top_holder_pct": round(top_h_val, 2) if top_h_val is not None else None,
                "attention_quality": attention_quality or None,
                "boost_active": boost_active,
                "attention_infrastructure": attention_infra or None,
                "appearances_72h": appear_72h,
                "hours_since_first_seen": display_hours_since_first_seen,
                "hours_since_last_scan": display_hours_since_last_scan,
                "lifecycle_state": lifecycle_state or "DISCOVERY",
                "entry_window": rec.get("entry_window"),
                "fuel_quality": rec.get("fuel_quality"),
                "move_phase": rec.get("move_phase"),
                "first_leg_confirmed": first_leg_confirmed,
                "n_windows": n_windows,
                "scan_best": bool(scan_best),
                "scan_best_regime": scan_best.get("regime_label") if scan_best else None,
                "scan_best_change_24h": scan_best.get("change_24h") if scan_best else None,
                "first_seen_at": display_first_seen,
                "last_seen_at": rec.get("scanned_at"),
            })
            seen_symbols.add(symbol)

        candidates.sort(key=lambda r: (float(r.get("rank_score") or 0), -float(r.get("hours_since_first_seen") or 9999) * -1), reverse=True)
        candidates = candidates[:DISCOVERY_LIMIT]

        return {
            "candidates": candidates,
            "count": len(candidates),
            "summary": {
                "lifecycle_early": sum(1 for c in candidates if c["stage"] == "LIFECYCLE_EARLY"),
                "discovery_plus": sum(1 for c in candidates if c["stage"] == "DISCOVERY_PLUS"),
                "discovery_ingress": sum(1 for c in candidates if c["stage"] == "DISCOVERY_INGRESS"),
                "scan_best_overlap": sum(1 for c in candidates if c.get("scan_best")),
            },
            "generated_at": now_iso,
        }

    try:
        return await asyncio.to_thread(_run)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/fresh-qualified")
async def memecoins_fresh_qualified_ep(_: str = Depends(get_current_user)):
    _memecoins_v2_retired("/api/memecoins/fresh-qualified")
    """
    Safer fresh-candidate lane for the actual operating style.

    This is not raw early discovery. It looks for newer names that have already
    crossed the system's safety floor and shown enough quality to deserve
    attention:
      - recent discovery / scanner presence
      - mcap >= $1.5M
      - liquidity >= $50K
      - at least one quality signal (attention / boost / vol accel / scan-best)

    Goal: fresher than the survivor lane, safer than the early-watch lane.
    """
    _ensure_engine_path()

    def _run() -> dict:
        import sqlite3
        from datetime import datetime, timezone, timedelta
        from utils.db import get_conn, get_recent_scan_bests  # type: ignore

        now = datetime.now(timezone.utc)
        cutoff_7d = (now - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
        cutoff_72h = (now - timedelta(hours=72)).strftime("%Y-%m-%d %H:%M:%S")
        cutoff_30d = (now - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
        cutoff_14d = (now - timedelta(days=14)).strftime("%Y-%m-%d %H:%M:%S")
        now_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")

        MCAP_FLOOR = 1_500_000.0
        LIQ_FLOOR = 50_000.0
        MAX_TOP_HOLDER = 20.0
        MAX_FIRST_SEEN_HOURS = 24.0 * 45.0
        MAX_IDLE_STATE_HOURS = 24.0 * 10.0
        LIMIT = 12

        recent_scan_bests = get_recent_scan_bests(lookback_hours=24, limit=80)
        scan_best_by_symbol = {
            str(r.get("symbol") or "").upper(): r
            for r in recent_scan_bests
            if r.get("symbol")
        }

        def _hours_since(ts_val) -> float | None:
            try:
                raw = str(ts_val or "")
                if not raw:
                    return None
                normalized = raw.replace(" ", "T")
                if normalized.endswith("Z"):
                    dt = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
                else:
                    dt = datetime.fromisoformat(normalized)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                return round((now - dt.astimezone(timezone.utc)).total_seconds() / 3600.0, 1)
            except Exception:
                return None

        with get_conn() as conn:
            conn.row_factory = sqlite3.Row

            appearances_rows = conn.execute(
                """
                SELECT symbol, COUNT(*) AS cnt
                FROM memecoin_signal_outcomes
                WHERE scanned_at >= ?
                GROUP BY symbol
                """,
                (cutoff_72h,),
            ).fetchall()
            appearances_72h = {
                str(r["symbol"]).upper(): int(r["cnt"] or 0)
                for r in appearances_rows
                if r["symbol"]
            }

            scan_stats_rows = conn.execute(
                """
                SELECT symbol, COUNT(*) AS cnt_30d, MAX(scanned_at) AS last_scan
                FROM memecoin_signal_outcomes
                WHERE scanned_at >= ?
                GROUP BY symbol
                """,
                (cutoff_30d,),
            ).fetchall()
            scan_stats_30d = {
                str(r["symbol"]).upper(): {
                    "cnt_30d": int(r["cnt_30d"] or 0),
                    "last_scan": r["last_scan"],
                }
                for r in scan_stats_rows
                if r["symbol"]
            }

            perf_rows = conn.execute(
                """
                SELECT
                    symbol,
                    COUNT(*) AS n,
                    AVG(return_24h_pct) AS avg_return
                FROM memecoin_signal_outcomes
                WHERE status = 'COMPLETE' AND return_24h_pct IS NOT NULL
                GROUP BY symbol
                """
            ).fetchall()
            perf_by_symbol = {
                str(r["symbol"]).upper(): {
                    "n": int(r["n"] or 0),
                    "avg_return": float(r["avg_return"] or 0.0),
                }
                for r in perf_rows
                if r["symbol"]
            }

            corr_rows = conn.execute(
                """
                SELECT
                    symbol,
                    COUNT(*) AS n,
                    AVG(return_24h_pct) AS avg_return
                FROM memecoin_signal_outcomes
                WHERE status = 'COMPLETE'
                  AND return_24h_pct IS NOT NULL
                  AND scanned_at >= datetime('now', '-14 days')
                GROUP BY symbol
                """
            ).fetchall()
            corr_by_symbol = {
                str(r["symbol"]).upper(): {
                    "n": int(r["n"] or 0),
                    "avg_return": float(r["avg_return"] or 0.0),
                }
                for r in corr_rows
                if r["symbol"]
            }

            def _memory_snapshot(pnls: list) -> dict:
                def _norm(obs_list: list) -> list[tuple[float, float]]:
                    out = []
                    for obs in obs_list:
                        if isinstance(obs, (tuple, list)):
                            pnl = float(obs[0])
                            wt = float(obs[1]) if len(obs) > 1 else 1.0
                        else:
                            pnl = float(obs)
                            wt = 1.0
                        out.append((pnl, max(wt, 0.0)))
                    return out

                t = _norm(pnls[-10:])
                tn = len(t)
                tw = sum(w for _, w in t)
                twr = (sum(w for p, w in t if p > 0) / tw * 100.0) if tw else 0.0
                tavg = round(sum(p * w for p, w in t) / tw, 1) if tw else None
                tcat = round(sum(w for p, w in t if p <= -20.0) / tw * 100.0, 1) if tw else None
                l = _norm(pnls)
                ln = len(l)
                lw = sum(w for _, w in l)
                lwr = round(sum(w for p, w in l if p > 0) / lw * 100.0, 1) if lw else None
                lavg = round(sum(p * w for p, w in l) / lw, 1) if lw else None
                wr_delta = round(twr - float(lwr or 0.0), 1) if tn and lwr is not None else None
                avg_delta = round(float(tavg or 0.0) - float(lavg or 0.0), 1) if tn and lavg is not None and tavg is not None else None
                if tw < 4.5:
                    mem_state = "THIN"
                elif (
                    twr >= 60.0
                    and (tavg or 0.0) > 5.0
                    and (tcat or 0.0) < 20.0
                    and (wr_delta is None or wr_delta >= -5.0)
                ):
                    mem_state = "COMPOUNDING"
                elif (
                    twr < 40.0
                    or (tavg or 0.0) < 0.0
                    or (tcat or 0.0) >= 30.0
                    or (wr_delta is not None and wr_delta <= -20.0)
                ):
                    mem_state = "FRAGILE"
                else:
                    mem_state = "MIXED"
                if tw >= 8.0 and lw >= 12.0:
                    confidence = "HIGH"
                elif tw >= 5.0:
                    confidence = "MEDIUM"
                else:
                    confidence = "LOW"
                return {
                    "state": mem_state,
                    "confidence": confidence,
                    "trailing_n": tn,
                    "trailing_weighted_n": round(tw, 1),
                    "trailing_wr": round(twr, 1) if tn else None,
                    "trailing_avg": tavg,
                    "cat_rate": tcat,
                    "lifetime_n": ln,
                    "lifetime_weighted_n": round(lw, 1),
                    "lifetime_wr": lwr,
                    "lifetime_avg": lavg,
                    "wr_delta": wr_delta,
                    "avg_delta": avg_delta,
                }

            trade_rows = conn.execute("""
                SELECT entry_window, entry_fuel_quality, entry_move_phase, pnl_pct, closed_ts_utc
                FROM memecoin_trades
                WHERE status = 'CLOSED'
                  AND closed_ts_utc IS NOT NULL
                  AND pnl_pct IS NOT NULL
                  AND entry_window IS NOT NULL
                  AND entry_fuel_quality IS NOT NULL
                  AND entry_move_phase IS NOT NULL
                ORDER BY closed_ts_utc ASC
            """).fetchall()
            setup_memory = {}
            fresh_reinforced_archetype_memory = {}
            reinforced_archetype_memory = {}
            archetype_memory = {}
            profile_memory = {}
            window_phase_memory = {}
            phase_memory = {}
            fresh_catalyst_bucket_memory = {}
            capital_posture_memory = {}
            capital_ready_state_memory = {}
            capital_pressure_bucket_memory = {}
            capital_regime_bucket_memory = {}
            capital_mix_bucket_memory = {}
            capital_allocator_stance_memory = {}
            capital_routing_family_memory = {}
            capital_headroom_bucket_memory = {}
            capital_route_bucket_memory = {}
            deployment_authority_memory = {}
            decision_authority_memory = {}
            capital_deployment_family_memory = {}
            capital_intensity_bucket_memory = {}
            capital_window_bucket_memory = {}
            freshness_bucket_memory = {}
            _setup_buckets: dict = {}
            _fresh_reinforced_archetype_buckets: dict = {}
            _reinforced_archetype_buckets: dict = {}
            _archetype_buckets: dict = {}
            _profile_buckets: dict = {}
            _window_phase_buckets: dict = {}
            _phase_buckets: dict = {}
            _fresh_catalyst_buckets: dict = {}
            _capital_posture_buckets: dict = {}
            _capital_ready_buckets: dict = {}
            _capital_pressure_buckets: dict = {}
            _capital_regime_buckets: dict = {}
            _capital_mix_buckets: dict = {}
            _capital_allocator_stance_buckets: dict = {}
            _capital_routing_family_buckets: dict = {}
            _capital_headroom_buckets: dict = {}
            _capital_route_buckets: dict = {}
            _deployment_authority_buckets: dict = {}
            _decision_authority_buckets: dict = {}
            _capital_deployment_family_buckets: dict = {}
            _capital_intensity_buckets: dict = {}
            _capital_window_buckets: dict = {}
            _fresh_catalyst_buckets: dict = {}
            _freshness_buckets: dict = {}
            for tr in trade_rows:
                k = (tr["entry_window"], tr["entry_fuel_quality"], tr["entry_move_phase"])
                ak = (_continuation_archetype(tr["entry_window"], tr["entry_fuel_quality"], tr["entry_move_phase"]),)
                rak = (ak[0], "PLAIN")
                pk = (tr["entry_fuel_quality"], tr["entry_move_phase"])
                wk = (tr["entry_window"], tr["entry_move_phase"])
                mk = (tr["entry_move_phase"],)
                pnl = (
                    float(tr["pnl_pct"]),
                    _continuation_recency_weight(tr["closed_ts_utc"]),
                )
                _setup_buckets.setdefault(k, []).append(pnl)
                _reinforced_archetype_buckets.setdefault(rak, []).append(pnl)
                _archetype_buckets.setdefault(ak, []).append(pnl)
                _profile_buckets.setdefault(pk, []).append(pnl)
                _window_phase_buckets.setdefault(wk, []).append(pnl)
                _phase_buckets.setdefault(mk, []).append(pnl)
            # Enrich continuation memory with resolved surfaced outcomes, not just
            # closed trades. HOME_QUEUE rows are the closest non-trade record of
            # what the system actually chose to pay attention to.
            _surface_rows = conn.execute("""
                SELECT entry_window, fuel_quality, move_phase,
                       continuation_archetype, support_overlap_score,
                       support_overlap_tags, capital_posture, capital_ready_state,
                       capital_suggested_entry_usd, capital_pressure_bucket, capital_regime_bucket, capital_mix_bucket, capital_allocator_stance, capital_headroom_bucket, capital_route_bucket, marginal_route, proof_stack_authority, reinforcement_authority, promotion_authority, deployment_authority, continuation_memory_authority, fresh_discovery_authority, fresh_catalyst_bucket, freshness_bucket,
                       return_24h_pct, surfaced_at
                FROM research_surface_log
                WHERE source = 'HOME_QUEUE'
                  AND outcome_status = 'RESOLVED'
                  AND return_24h_pct IS NOT NULL
                  AND entry_window IS NOT NULL
                  AND fuel_quality IS NOT NULL
                  AND move_phase IS NOT NULL
                ORDER BY surfaced_at ASC
            """).fetchall()
            for sr in _surface_rows:
                k = (sr["entry_window"], sr["fuel_quality"], sr["move_phase"])
                _arch = str(sr["continuation_archetype"] or "").strip() or _continuation_archetype(
                    sr["entry_window"], sr["fuel_quality"], sr["move_phase"]
                )
                ak = (_arch,)
                _support_bucket = _support_reinforcement_bucket(
                    sr["support_overlap_score"] if "support_overlap_score" in sr.keys() else None,
                    json.loads(sr["support_overlap_tags"] or "[]") if ("support_overlap_tags" in sr.keys() and sr["support_overlap_tags"]) else [],
                )
                _fresh_bucket = str(sr["freshness_bucket"] or "UNKNOWN").strip().upper() or "UNKNOWN"
                _fresh_catalyst_bucket_value = str(sr["fresh_catalyst_bucket"] or "UNKNOWN").strip().upper() or "UNKNOWN"
                frak = (_arch, _support_bucket, _fresh_bucket)
                rak = (_arch, _support_bucket)
                pk = (sr["fuel_quality"], sr["move_phase"])
                wk = (sr["entry_window"], sr["move_phase"])
                mk = (sr["move_phase"],)
                cpk = str(sr["capital_posture"] or "UNKNOWN").strip() or "UNKNOWN"
                crk = str(sr["capital_ready_state"] or "UNKNOWN").strip() or "UNKNOWN"
                cxb = str(sr["capital_pressure_bucket"] or "UNKNOWN").strip().upper() or "UNKNOWN"
                rgb = str(sr["capital_regime_bucket"] or "UNKNOWN").strip().upper() or "UNKNOWN"
                cmb = str(sr["capital_mix_bucket"] or "UNKNOWN").strip().upper() or "UNKNOWN"
                cas = str(sr["capital_allocator_stance"] or "UNKNOWN").strip().upper() or "UNKNOWN"
                chb = str(sr["capital_headroom_bucket"] or "UNKNOWN").strip().upper() or "UNKNOWN"
                crb = str(sr["capital_route_bucket"] or "UNKNOWN").strip().upper() or "UNKNOWN"
                cmr = str(sr["marginal_route"] or _capital_marginal_route(crb)).strip().upper() or "UNKNOWN"
                crf = _capital_routing_family(cas, crb, cmr)
                cda = _deployment_authority(
                    sr["promotion_authority"] if "promotion_authority" in sr.keys() else None,
                    sr["reinforcement_authority"] if "reinforcement_authority" in sr.keys() else None,
                    crk,
                    crb,
                    chb,
                )
                dsa = _decision_authority(
                    sr["proof_stack_authority"] if "proof_stack_authority" in sr.keys() else None,
                    sr["continuation_memory_authority"] if "continuation_memory_authority" in sr.keys() else None,
                    sr["fresh_discovery_authority"] if "fresh_discovery_authority" in sr.keys() else None,
                    sr["reinforcement_authority"] if "reinforcement_authority" in sr.keys() else None,
                    sr["promotion_authority"] if "promotion_authority" in sr.keys() else None,
                    sr["deployment_authority"] if "deployment_authority" in sr.keys() else cda,
                )
                cdf = _capital_deployment_family(cpk, crk, cxb, rgb)
                cib = _capital_intensity_bucket(sr["capital_suggested_entry_usd"] or 0.0)
                cwb = _capital_window_bucket(
                    "LIVE",
                    True,
                    1,
                    sr["capital_suggested_entry_usd"] or 0.0,
                    sr["capital_suggested_entry_usd"] or 0.0,
                    1.0,
                    crk,
                    sr["capital_suggested_entry_usd"] or 0.0,
                )
                cwb = _capital_window_bucket(
                    "LIVE",
                    True,
                    1,
                    sr["capital_suggested_entry_usd"] or 0.0,
                    sr["capital_suggested_entry_usd"] or 0.0,
                    1.0,
                    crk,
                    sr["capital_suggested_entry_usd"] or 0.0,
                )
                fxb = str(sr["freshness_bucket"] or "UNKNOWN").strip().upper() or "UNKNOWN"
                pnl = (
                    float(sr["return_24h_pct"]),
                    round(0.6 * _continuation_recency_weight(sr["surfaced_at"]), 3),
                )
                _setup_buckets.setdefault(k, []).append(pnl)
                _fresh_reinforced_archetype_buckets.setdefault(frak, []).append(pnl)
                _reinforced_archetype_buckets.setdefault(rak, []).append(pnl)
                _archetype_buckets.setdefault(ak, []).append(pnl)
                _profile_buckets.setdefault(pk, []).append(pnl)
                _window_phase_buckets.setdefault(wk, []).append(pnl)
                _phase_buckets.setdefault(mk, []).append(pnl)
                _capital_posture_buckets.setdefault(cpk, []).append(pnl)
                _capital_ready_buckets.setdefault(crk, []).append(pnl)
                _capital_pressure_buckets.setdefault(cxb, []).append(pnl)
                _capital_regime_buckets.setdefault(rgb, []).append(pnl)
                _capital_mix_buckets.setdefault(cmb, []).append(pnl)
                _capital_allocator_stance_buckets.setdefault(cas, []).append(pnl)
                _capital_routing_family_buckets.setdefault(crf, []).append(pnl)
                _capital_headroom_buckets.setdefault(chb, []).append(pnl)
                _capital_route_buckets.setdefault(crb, []).append(pnl)
                _deployment_authority_buckets.setdefault(cda, []).append(pnl)
                _decision_authority_buckets.setdefault(dsa, []).append(pnl)
                _capital_deployment_family_buckets.setdefault(cdf, []).append(pnl)
                _capital_intensity_buckets.setdefault(cib, []).append(pnl)
                _capital_window_buckets.setdefault(cwb, []).append(pnl)
                _capital_window_buckets.setdefault(cwb, []).append(pnl)
                _fresh_catalyst_buckets.setdefault(_fresh_catalyst_bucket_value, []).append(pnl)
                _freshness_buckets.setdefault(fxb, []).append(pnl)
            for k, pnls in _setup_buckets.items():
                setup_memory[k] = _memory_snapshot(pnls)
            for k, pnls in _fresh_reinforced_archetype_buckets.items():
                fresh_reinforced_archetype_memory[k] = _memory_snapshot(pnls)
            for k, pnls in _reinforced_archetype_buckets.items():
                reinforced_archetype_memory[k] = _memory_snapshot(pnls)
            for k, pnls in _archetype_buckets.items():
                archetype_memory[k] = _memory_snapshot(pnls)
            for k, pnls in _profile_buckets.items():
                profile_memory[k] = _memory_snapshot(pnls)
            for k, pnls in _window_phase_buckets.items():
                window_phase_memory[k] = _memory_snapshot(pnls)
            for k, pnls in _phase_buckets.items():
                phase_memory[k] = _memory_snapshot(pnls)
            for k, pnls in _capital_posture_buckets.items():
                capital_posture_memory[k] = _memory_snapshot(pnls)
            for k, pnls in _capital_ready_buckets.items():
                capital_ready_state_memory[k] = _memory_snapshot(pnls)
            for k, pnls in _capital_pressure_buckets.items():
                capital_pressure_bucket_memory[k] = _memory_snapshot(pnls)
            for k, pnls in _capital_regime_buckets.items():
                capital_regime_bucket_memory[k] = _memory_snapshot(pnls)
            for k, pnls in _capital_mix_buckets.items():
                capital_mix_bucket_memory[k] = _memory_snapshot(pnls)
            for k, pnls in _capital_allocator_stance_buckets.items():
                capital_allocator_stance_memory[k] = _memory_snapshot(pnls)
            for k, pnls in _capital_routing_family_buckets.items():
                capital_routing_family_memory[k] = _memory_snapshot(pnls)
            for k, pnls in _capital_headroom_buckets.items():
                capital_headroom_bucket_memory[k] = _memory_snapshot(pnls)
            for k, pnls in _capital_route_buckets.items():
                capital_route_bucket_memory[k] = _memory_snapshot(pnls)
            for k, pnls in _deployment_authority_buckets.items():
                deployment_authority_memory[k] = _memory_snapshot(pnls)
            for k, pnls in _decision_authority_buckets.items():
                decision_authority_memory[k] = _memory_snapshot(pnls)
            for k, pnls in _capital_deployment_family_buckets.items():
                capital_deployment_family_memory[k] = _memory_snapshot(pnls)
            for k, pnls in _capital_intensity_buckets.items():
                capital_intensity_bucket_memory[k] = _memory_snapshot(pnls)
            for k, pnls in _capital_window_buckets.items():
                capital_window_bucket_memory[k] = _memory_snapshot(pnls)
            for k, pnls in _fresh_catalyst_buckets.items():
                fresh_catalyst_bucket_memory[k] = _memory_snapshot(pnls)
            for k, pnls in _freshness_buckets.items():
                freshness_bucket_memory[k] = _memory_snapshot(pnls)

            rows = conn.execute(
                """
                SELECT
                    c.symbol,
                    c.mint,
                    c.scanned_at,
                    c.source,
                    c.score,
                    c.mcap_at_scan,
                    c.liquidity_usd,
                    c.volume_24h,
                    c.token_age_days,
                    c.vol_acceleration,
                    c.top_holder_pct,
                    c.top5_holder_pct,
                    c.rug_label,
                    c.holder_quality_level,
                    c.attention_infrastructure,
                    c.boost_active,
                    c.attention_quality,
                    lc.lifecycle_state,
                    lc.first_leg_confirmed,
                    lc.n_windows,
                    lc.liq_trend,
                    lc.first_seen_at,
                    lc.state_entered_at,
                    lc.hours_since_last_scan,
                    lc.entry_window,
                    lc.fuel_quality,
                    lc.move_phase
                FROM (
                    SELECT m1.*
                    FROM memecoin_signal_outcomes m1
                    INNER JOIN (
                        SELECT symbol, MAX(scanned_at) AS max_scanned_at
                        FROM memecoin_signal_outcomes
                        WHERE source IN ('DISCOVERY', 'SCANNER')
                          AND scanned_at >= ?
                          AND mcap_at_scan >= ?
                          AND liquidity_usd >= ?
                        GROUP BY symbol
                    ) latest
                      ON latest.symbol = m1.symbol
                     AND latest.max_scanned_at = m1.scanned_at
                    WHERE m1.source IN ('DISCOVERY', 'SCANNER')
                ) c
                LEFT JOIN symbol_lifecycle lc
                  ON lc.symbol = c.symbol
                ORDER BY c.scanned_at DESC
                LIMIT 220
                """,
                (cutoff_7d, MCAP_FLOOR, LIQ_FLOOR),
            ).fetchall()

        def _coin_quality(rec: dict) -> str:
            rug = str(rec.get("rug_label") or "").upper()
            top1 = float(rec.get("top_holder_pct") or 0.0)
            top5 = float(rec.get("top5_holder_pct") or 0.0)
            liq = float(rec.get("liquidity_usd") or 0.0)
            vol24 = float(rec.get("volume_24h") or 0.0)
            age = float(rec.get("token_age_days") or 0.0)
            hq = str(rec.get("holder_quality_level") or "").upper()
            vl = (vol24 / liq) if liq > 1000 else 0.0
            if rug == "DANGER" or top1 >= 80 or top5 >= 90 or (0 < liq < 30_000) or vl > 25:
                return "WEAK"
            if rug in ("WARN", "UNKNOWN") or (50 <= top1 < 80) or (70 <= top5 < 90) or (0 < liq < 75_000) or (0 < age < 7) or (10 < vl <= 25) or hq == "RISKY":
                return "QUESTIONABLE"
            return "CLEAR"

        candidates = []
        seen_symbols: set[str] = set()
        for row in rows:
            rec = dict(row)
            symbol = str(rec.get("symbol") or "").upper()
            if not symbol or symbol in seen_symbols:
                continue

            liq = float(rec.get("liquidity_usd") or 0.0)
            mcap = float(rec.get("mcap_at_scan") or 0.0)
            top_h = rec.get("top_holder_pct")
            top_h_val = float(top_h) if top_h is not None else None
            vol_acc = rec.get("vol_acceleration")
            vol_acc_val = float(vol_acc) if vol_acc is not None else None
            appearances = appearances_72h.get(symbol, 0)
            attention_quality = str(rec.get("attention_quality") or "")
            attention_infra = str(rec.get("attention_infrastructure") or "")
            boost_active = int(rec.get("boost_active") or 0)
            lifecycle_state = str(rec.get("lifecycle_state") or "")
            scan_best = scan_best_by_symbol.get(symbol)
            scanned_at = rec.get("scanned_at")
            lifecycle_first_seen = rec.get("first_seen_at")
            state_entered_at = rec.get("state_entered_at")
            display_first_seen = lifecycle_first_seen or scanned_at
            hours_since_first_seen = _hours_since(display_first_seen)
            hours_since_last_scan = _hours_since(scanned_at)
            hours_since_state_change = _hours_since(state_entered_at)
            coin_quality = _coin_quality(rec)
            perf = perf_by_symbol.get(symbol, {})
            corr = corr_by_symbol.get(symbol, {})
            proven_negative = int(perf.get("n") or 0) >= 15 and float(perf.get("avg_return") or 0.0) < -10.0
            casualty = int(corr.get("n") or 0) >= 2 and float(corr.get("avg_return") or 0.0) < -30.0
            scan_stats = scan_stats_30d.get(symbol, {})
            scan_count_30d = int(scan_stats.get("cnt_30d") or 0)
            last_scan_30d = scan_stats.get("last_scan")
            incumbent_working_set = bool(scan_count_30d >= 3 and last_scan_30d and str(last_scan_30d) >= cutoff_14d)

            if mcap < MCAP_FLOOR or liq < LIQ_FLOOR:
                continue
            if top_h_val is not None and top_h_val > MAX_TOP_HOLDER:
                continue
            if lifecycle_state == "DEAD":
                continue
            if coin_quality == "WEAK" or proven_negative or casualty:
                continue

            quality_signals = []
            if appearances >= 2:
                quality_signals.append("PERSISTENCE")
            if attention_quality in ("STRONG", "MODERATE"):
                quality_signals.append(f"ATTN_{attention_quality}")
            if boost_active == 1:
                quality_signals.append("BOOSTED")
            if attention_infra not in ("", "ABSENT", "NONE", "0"):
                quality_signals.append("INFRA")
            if vol_acc_val is not None and vol_acc_val >= 3.0:
                quality_signals.append("VOL_ACCEL")
            if scan_best:
                quality_signals.append("SCAN_BEST")
            if str(rec.get("entry_window") or "") == "OPEN":
                quality_signals.append("OPEN_WINDOW")
            if lifecycle_state in ("RELOAD", "REVIVAL", "ACTIVE"):
                quality_signals.append(f"STATE_{lifecycle_state}")
            if hours_since_state_change is not None and hours_since_state_change <= 24.0 * 7.0:
                quality_signals.append("RECENT_STATE_CHANGE")
            if str(rec.get("source") or "").upper() == "SCANNER":
                quality_signals.append("SCANNER_CONFIRMED")

            if not quality_signals:
                continue

            continuation_ready = (
                int(rec.get("first_leg_confirmed") or 0) == 1
                or int(rec.get("n_windows") or 0) >= 2
                or lifecycle_state in ("RELOAD", "REVIVAL")
                or str(rec.get("move_phase") or "") == "RELOAD"
            )
            if not continuation_ready:
                continue

            too_early_for_style = (
                hours_since_first_seen is not None
                and hours_since_first_seen < 48.0
                and int(rec.get("first_leg_confirmed") or 0) == 0
                and int(rec.get("n_windows") or 0) < 2
            )
            if too_early_for_style:
                continue

            fresh_enough = (
                (hours_since_first_seen is not None and hours_since_first_seen <= MAX_FIRST_SEEN_HOURS)
                or (hours_since_state_change is not None and hours_since_state_change <= MAX_IDLE_STATE_HOURS)
                or bool(scan_best)
                or lifecycle_state in ("RELOAD", "REVIVAL", "ACTIVE")
            )
            if not fresh_enough:
                continue

            setup_key = (
                str(rec.get("entry_window") or ""),
                str(rec.get("fuel_quality") or ""),
                str(rec.get("move_phase") or ""),
            )
            archetype_key = (
                _continuation_archetype(
                    rec.get("entry_window"),
                    rec.get("fuel_quality"),
                    rec.get("move_phase"),
                ),
            )
            profile_key = (
                str(rec.get("fuel_quality") or ""),
                str(rec.get("move_phase") or ""),
            )
            continuation_memory = setup_memory.get(setup_key, {
                "state": "THIN",
                "trailing_n": 0,
                "trailing_wr": None,
                "trailing_avg": None,
                "cat_rate": None,
                "lifetime_n": 0,
                "lifetime_wr": None,
                "lifetime_avg": None,
            })
            continuation_memory_source = "EXACT_SETUP"
            reinforcement_bucket = _support_reinforcement_bucket(
                rec.get("support_overlap_score"),
                rec.get("support_overlap_tags"),
            )
            freshness_bucket = (
                "RECENT_REACTIVATION"
                if (
                    hours_since_state_change is not None
                    and hours_since_state_change <= 168.0
                    and lifecycle_state in ("RELOAD", "REVIVAL", "ACTIVE")
                    and (hours_since_first_seen or 0) > 168.0
                )
                else "NEWLY_QUALIFIED"
            )
            _mem_candidates = [
                ("PROFILE_FRESH_REINFORCED_ARCHETYPE", fresh_reinforced_archetype_memory.get((archetype_key[0], reinforcement_bucket, freshness_bucket))),
                ("PROFILE_REINFORCED_ARCHETYPE", reinforced_archetype_memory.get((archetype_key[0], reinforcement_bucket))),
                ("PROFILE_ARCHETYPE", archetype_memory.get(archetype_key)),
                ("PROFILE_FUEL_PHASE", profile_memory.get(profile_key)),
                ("PROFILE_WINDOW_PHASE", window_phase_memory.get((str(rec.get("entry_window") or ""), str(rec.get("move_phase") or "")))),
                ("PROFILE_PHASE", phase_memory.get((str(rec.get("move_phase") or ""),))),
            ]
            continuation_memory, continuation_memory_source = _choose_continuation_memory(
                continuation_memory,
                _mem_candidates,
            )
            _mem_weight = {
                "EXACT_SETUP": 1.0,
                "PROFILE_FRESH_REINFORCED_ARCHETYPE": 0.95,
                "PROFILE_REINFORCED_ARCHETYPE": 0.9,
                "PROFILE_ARCHETYPE": 0.85,
                "PROFILE_FUEL_PHASE": 0.75,
                "PROFILE_WINDOW_PHASE": 0.6,
                "PROFILE_PHASE": 0.4,
            }.get(str(continuation_memory_source or "EXACT_SETUP"), 0.4)
            _mem_conf = str(continuation_memory.get("confidence") or "LOW")
            _mem_wr_delta = continuation_memory.get("wr_delta")
            _mem_avg_delta = continuation_memory.get("avg_delta")
            freshness_memory = freshness_bucket_memory.get(freshness_bucket)
            _fresh_mem_state = str((freshness_memory or {}).get("state") or "THIN")
            _fresh_mem_conf = str((freshness_memory or {}).get("confidence") or "LOW")
            _fresh_mem_wr_delta = (freshness_memory or {}).get("wr_delta")
            _fresh_mem_avg_delta = (freshness_memory or {}).get("avg_delta")

            score = 0.0
            score += 22.0  # fresh-qualified base
            score += min(max((mcap - MCAP_FLOOR) / 500_000.0, 0.0), 16.0)
            score += min(liq / 80_000.0, 12.0)
            if appearances >= 2:
                score += min(appearances * 2.5, 10.0)
            if attention_quality == "STRONG":
                score += 8.0
            elif attention_quality == "MODERATE":
                score += 4.0
            if boost_active == 1:
                score += 6.0
            if vol_acc_val is not None:
                score += min(vol_acc_val * 1.5, 12.0)
            if scan_best:
                score += 10.0
            if lifecycle_state in ("RELOAD", "REVIVAL", "ACTIVE"):
                score += 6.0
            score += 4.0 if freshness_bucket == "RECENT_REACTIVATION" else 1.5
            if str(rec.get("entry_window") or "") == "OPEN":
                score += 4.0
            if hours_since_state_change is not None:
                if hours_since_state_change <= 24.0 * 3.0:
                    score += 8.0
                elif hours_since_state_change <= 24.0 * 7.0:
                    score += 4.0
            if hours_since_first_seen is not None:
                if hours_since_first_seen <= 72:
                    score += 8.0
                elif hours_since_first_seen <= 24 * 7:
                    score += 5.0
                elif hours_since_first_seen <= 24 * 21:
                    score += 2.0
            score += {
                "COMPOUNDING": 18.0,
                "MIXED": 6.0,
                "THIN": 0.0,
                "FRAGILE": -18.0,
            }.get(str(continuation_memory.get("state") or "THIN"), 0.0) * _mem_weight
            score += {
                "HIGH": 4.0,
                "MEDIUM": 2.0,
                "LOW": 0.0,
            }.get(_mem_conf, 0.0) * _mem_weight
            if continuation_memory.get("trailing_wr") is not None:
                score += max(min((float(continuation_memory["trailing_wr"]) - 50.0) * 0.25, 8.0), -8.0) * _mem_weight
            if continuation_memory.get("trailing_avg") is not None:
                score += max(min(float(continuation_memory["trailing_avg"]) * 0.20, 6.0), -6.0) * _mem_weight
            if _mem_wr_delta is not None:
                score += max(min(float(_mem_wr_delta) * 0.20, 4.0), -4.0) * _mem_weight
            if _mem_avg_delta is not None:
                score += max(min(float(_mem_avg_delta) * 0.20, 3.0), -3.0) * _mem_weight
            if continuation_memory.get("cat_rate") is not None and float(continuation_memory["cat_rate"]) >= 30.0:
                score -= min((float(continuation_memory["cat_rate"]) - 30.0) * 0.20, 8.0) * _mem_weight
            score += {
                "COMPOUNDING": 6.0,
                "MIXED": 2.0,
                "THIN": 0.0,
                "FRAGILE": -7.0,
            }.get(_fresh_mem_state, 0.0)
            score += {
                "HIGH": 1.5,
                "MEDIUM": 0.75,
                "LOW": 0.0,
            }.get(_fresh_mem_conf, 0.0)
            if _fresh_mem_wr_delta is not None:
                score += max(min(float(_fresh_mem_wr_delta) * 0.12, 2.5), -2.5)
            if _fresh_mem_avg_delta is not None:
                score += max(min(float(_fresh_mem_avg_delta) * 0.12, 2.0), -2.0)

            fresh_catalyst_score = 0.0
            if scan_best:
                fresh_catalyst_score += 5.0
            if lifecycle_state in ("RELOAD", "REVIVAL"):
                fresh_catalyst_score += 5.0
            elif lifecycle_state == "ACTIVE":
                fresh_catalyst_score += 2.0
            if hours_since_state_change is not None:
                if hours_since_state_change <= 24.0 * 3.0:
                    fresh_catalyst_score += 6.0
                elif hours_since_state_change <= 24.0 * 7.0:
                    fresh_catalyst_score += 3.0
            if hours_since_first_seen is not None:
                if hours_since_first_seen <= 24.0 * 7.0:
                    fresh_catalyst_score += 4.0
                elif hours_since_first_seen <= 24.0 * 21.0:
                    fresh_catalyst_score += 2.0
            if appearances >= 3:
                fresh_catalyst_score += 3.0
            elif appearances >= 2:
                fresh_catalyst_score += 1.5
            if vol_acc_val is not None:
                if vol_acc_val >= 6.0:
                    fresh_catalyst_score += 3.0
                elif vol_acc_val >= 3.0:
                    fresh_catalyst_score += 1.5

            fresh_catalyst = fresh_catalyst_score >= 7.0
            fresh_catalyst_kind = _fresh_catalyst_bucket(
                lifecycle_state,
                bool(scan_best),
                appearances,
                hours_since_state_change,
                vol_acc_val,
            )
            if fresh_catalyst_score >= 12.0:
                score += 6.0
            elif fresh_catalyst_score >= 7.0:
                score += 3.0
            score += {
                "REACTIVATION_DRIVEN": 4.0,
                "SCAN_MOMO": 3.0,
                "PERSISTENCE_DRIVEN": 2.0,
                "THIN_CATALYST": -1.0,
            }.get(fresh_catalyst_kind, 0.0)
            _fresh_catalyst_mem = fresh_catalyst_bucket_memory.get(fresh_catalyst_kind)
            _fresh_catalyst_mem_state = str((_fresh_catalyst_mem or {}).get("state") or "THIN")
            _fresh_catalyst_mem_conf = str((_fresh_catalyst_mem or {}).get("confidence") or "LOW")
            score += {
                "COMPOUNDING": 4.0,
                "MIXED": 1.5,
                "THIN": 0.0,
                "FRAGILE": -5.0,
            }.get(_fresh_catalyst_mem_state, 0.0)
            score += {
                "HIGH": 1.0,
                "MEDIUM": 0.5,
                "LOW": 0.0,
            }.get(_fresh_catalyst_mem_conf, 0.0)

            rotation_penalty = 0.0
            if incumbent_working_set:
                if fresh_catalyst_score < 4.0:
                    rotation_penalty = 14.0
                elif fresh_catalyst_score < 7.0:
                    rotation_penalty = 8.0
                score -= rotation_penalty

            candidates.append({
                "symbol": symbol,
                "mint": rec.get("mint"),
                "rank_score": round(score, 2),
                "source": rec.get("source"),
                "mcap_usd": round(mcap, 0),
                "liquidity_usd": round(liq, 0),
                "volume_24h": round(float(rec.get("volume_24h") or 0.0), 0) if rec.get("volume_24h") is not None else None,
                "vol_acceleration": round(vol_acc_val, 2) if vol_acc_val is not None else None,
                "top_holder_pct": round(top_h_val, 2) if top_h_val is not None else None,
                "appearances_72h": appearances,
                "coin_quality": coin_quality,
                "attention_quality": attention_quality or None,
                "boost_active": boost_active,
                "attention_infrastructure": attention_infra or None,
                "quality_signals": quality_signals,
                "scan_count_30d": scan_count_30d,
                "incumbent_working_set": incumbent_working_set,
                "fresh_catalyst": fresh_catalyst,
                "fresh_catalyst_bucket": fresh_catalyst_kind,
                "fresh_catalyst_bucket_memory": _fresh_catalyst_mem,
                "fresh_catalyst_score": round(fresh_catalyst_score, 1),
                "rotation_penalty": rotation_penalty,
                "hours_since_first_seen": hours_since_first_seen,
                "hours_since_last_scan": hours_since_last_scan,
                "hours_since_state_change": hours_since_state_change,
                "first_seen_at": display_first_seen,
                "last_seen_at": scanned_at,
                "lifecycle_state": lifecycle_state or "DISCOVERY",
                "entry_window": rec.get("entry_window"),
                "fuel_quality": rec.get("fuel_quality"),
                "move_phase": rec.get("move_phase"),
                "continuation_memory": continuation_memory,
                "continuation_memory_source": continuation_memory_source,
                "continuation_archetype": archetype_key[0],
                "reinforcement_bucket": reinforcement_bucket,
                "freshness_bucket": freshness_bucket,
                "freshness_bucket_memory": freshness_memory,
                "first_leg_confirmed": int(rec.get("first_leg_confirmed") or 0),
                "n_windows": int(rec.get("n_windows") or 0),
                "scan_best": bool(scan_best),
                "scan_best_regime": scan_best.get("regime_label") if scan_best else None,
                "scan_best_change_24h": scan_best.get("change_24h") if scan_best else None,
            })
            seen_symbols.add(symbol)

        candidates.sort(
            key=lambda r: (
                float(r.get("rank_score") or 0.0),
                -(float(r.get("hours_since_first_seen") or 9999.0)),
            ),
            reverse=True,
        )
        candidates = candidates[:LIMIT]

        return {
            "candidates": candidates,
            "count": len(candidates),
            "summary": {
                "mcap_floor": MCAP_FLOOR,
                "liquidity_floor": LIQ_FLOOR,
                "scan_best_overlap": sum(1 for c in candidates if c.get("scan_best")),
                "open_window": sum(1 for c in candidates if c.get("entry_window") == "OPEN"),
                "repeat_presence": sum(1 for c in candidates if int(c.get("appearances_72h") or 0) >= 2),
                "scanner_confirmed": sum(1 for c in candidates if c.get("source") == "SCANNER"),
                "incumbent_penalized": sum(1 for c in candidates if float(c.get("rotation_penalty") or 0.0) > 0),
            },
            "generated_at": now_iso,
        }

    try:
        return await asyncio.to_thread(_run)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ── Patch 275: Token Intelligence Dossier ─────────────────────────────────────

@router.get("/token-dossier/{symbol}")
async def memecoins_token_dossier_ep(symbol: str, _: str = Depends(get_current_user)):
    _memecoins_v2_retired("/api/memecoins/token-dossier/{symbol}")
    """One-token deep-dive: conviction snapshot, scan history, trade history."""
    _ensure_engine_path()

    def _run() -> dict:
        import sqlite3
        from datetime import datetime, timezone

        def _conviction(ew, fq, mp) -> str:
            """Mirror Patch 274 execution-grade conviction gate logic."""
            if ew is None and fq is None:
                return "research-only — lifecycle missing"
            if ew == "CLOSED":
                return "rejected — entry_window=CLOSED"
            if fq == "TRAP":
                return "rejected — fuel_quality=TRAP"
            if mp in ("EXTENDED", "CHURN"):
                return f"rejected — move_phase={mp}"
            if ew != "OPEN":
                return f"research-only — entry_window={ew}"
            if fq not in ("STRONG", "MODERATE"):
                return f"research-only — fuel_quality={fq}"
            return "EXECUTION_READY"

        with sqlite3.connect(_db_path()) as conn:
            conn.row_factory = sqlite3.Row

            # ── Identity / lifecycle snapshot ─────────────────────────────────
            lc_row = conn.execute(
                "SELECT symbol, mint, lifecycle_state, entry_window, fuel_quality, "
                "move_phase, phase_score, first_leg_confirmed, multi_leg_confirmed, "
                "vol_acc_current, first_seen_at, state_entered_at, last_computed_at "
                "FROM symbol_lifecycle WHERE symbol = ?",
                (symbol,),
            ).fetchone()

            if lc_row:
                lc = dict(lc_row)
                mint = lc.get("mint")
                ew   = lc.get("entry_window")
                fq   = lc.get("fuel_quality")
                mp   = lc.get("move_phase")
            else:
                lc   = {"symbol": symbol}
                mint = ew = fq = mp = None

            lc["conviction"] = _conviction(ew, fq, mp)

            # ── Scan history — last 15 from memecoin_signal_outcomes ──────────
            scan_rows = conn.execute(
                "SELECT scanned_at, score, buy_pressure_at_scan AS buy_pressure, "
                "vol_acceleration, rug_label, return_1h_pct, return_4h_pct, "
                "return_24h_pct, bought "
                "FROM memecoin_signal_outcomes WHERE symbol = ? "
                "ORDER BY scanned_at DESC LIMIT 15",
                (symbol,),
            ).fetchall()
            scan_history = [dict(r) for r in scan_rows]

            # ── Trade history — all trades for this mint (symbol fallback) ────
            if mint:
                trade_rows = conn.execute(
                    "SELECT opened_ts_utc, status, entry_score, entry_fuel_quality, "
                    "entry_window, entry_move_phase, amount_usd, pnl_pct, pnl_usd, "
                    "exit_reason, closed_ts_utc "
                    "FROM memecoin_trades WHERE mint = ? "
                    "ORDER BY opened_ts_utc DESC",
                    (mint,),
                ).fetchall()
            else:
                trade_rows = conn.execute(
                    "SELECT opened_ts_utc, status, entry_score, entry_fuel_quality, "
                    "entry_window, entry_move_phase, amount_usd, pnl_pct, pnl_usd, "
                    "exit_reason, closed_ts_utc "
                    "FROM memecoin_trades WHERE symbol = ? "
                    "ORDER BY opened_ts_utc DESC",
                    (symbol,),
                ).fetchall()

            trades = []
            for r in trade_rows:
                t = dict(r)
                if t.get("opened_ts_utc") and t.get("closed_ts_utc"):
                    try:
                        def _p(ts: str):
                            return datetime.fromisoformat(
                                ts.replace("Z", "+00:00") if ts else ts
                            )
                        t["hold_hours"] = round(
                            (_p(t["closed_ts_utc"]) - _p(t["opened_ts_utc"])).total_seconds() / 3600, 1
                        )
                    except Exception:
                        t["hold_hours"] = None
                else:
                    t["hold_hours"] = None
                trades.append(t)

            # Patch 293: symbol-level historical performance summary
            # Replicates Patch 289 tier thresholds from NBA _run() exactly
            perf_row = conn.execute("""
                SELECT
                    COUNT(*) AS n,
                    ROUND(AVG(return_24h_pct), 1) AS avg_return,
                    ROUND(SUM(CASE WHEN return_24h_pct >= 10 THEN 1.0 ELSE 0.0 END)
                          * 100.0 / COUNT(*), 1) AS win_rate
                FROM memecoin_signal_outcomes
                WHERE symbol = ? AND status = 'COMPLETE' AND return_24h_pct IS NOT NULL
            """, (symbol,)).fetchone()
            _pn   = int(perf_row["n"]          or 0)
            _pwr  = float(perf_row["win_rate"]  or 0)
            _pavg = float(perf_row["avg_return"] or 0) if perf_row["avg_return"] is not None else None
            if   _pn >= 15 and _pwr >= 50 and (_pavg or 0) >= 5:  _ptier = "PROVEN_POSITIVE"
            elif _pn >= 15 and (_pavg or 0) < -10:                _ptier = "PROVEN_NEGATIVE"
            elif _pn >= 8:                                         _ptier = "TESTED_NEUTRAL"
            else:                                                   _ptier = "UNPROVEN"

            # ── Patch 297: setup-aware trust verdict ──────────────────────────
            # Consistent with Patch 296 decision tree; computed inline since this
            # is a separate _run() with its own conn scope.

            # 1. Correction-period performance (14-day window, same as NBA)
            from datetime import timedelta as _td297
            _corr_start_297 = (datetime.now(timezone.utc) - _td297(days=14)).strftime("%Y-%m-%d")
            _cr297 = conn.execute("""
                SELECT COUNT(*) AS n,
                       ROUND(AVG(return_24h_pct), 1) AS avg_return,
                       ROUND(SUM(CASE WHEN return_24h_pct > -20.0 THEN 1.0 ELSE 0.0 END)
                             / COUNT(*), 2) AS survival_rate
                FROM memecoin_signal_outcomes
                WHERE symbol = ? AND status = 'COMPLETE'
                  AND return_24h_pct IS NOT NULL AND scanned_at >= ?
            """, (symbol, _corr_start_297)).fetchone()
            _cn297  = int(_cr297["n"] or 0)
            _cavg297 = float(_cr297["avg_return"] or 0) if _cr297["avg_return"] is not None else None
            _csr297  = float(_cr297["survival_rate"] or 0)
            if   _cn297 >= 2 and (_cavg297 or 0) > -5.0 and _csr297 >= 0.70: _ctier297 = "SURVIVOR"
            elif _cn297 >= 2 and (_cavg297 or 0) < -30.0:                    _ctier297 = "CASUALTY"
            elif _cn297 >= 2:                                                 _ctier297 = "TRACKER"
            else:                                                              _ctier297 = "UNRATED"

            # 2. Avg loss depth (failure profile)
            _lr297 = conn.execute("""
                SELECT AVG(return_24h_pct) AS avg_loss, COUNT(*) AS n_loss
                FROM memecoin_signal_outcomes
                WHERE symbol = ? AND status = 'COMPLETE' AND return_24h_pct < 0
            """, (symbol,)).fetchone()
            _avg_loss297 = round(float(_lr297["avg_loss"]), 1) if _lr297 and _lr297["avg_loss"] is not None else None
            _n_loss297   = int(_lr297["n_loss"] or 0) if _lr297 else 0

            # 3. Setup ledger status (current lifecycle ew/fq/mp combination)
            _setup_status297 = "INSUFFICIENT_DATA"
            if ew and fq and mp:
                _sl297 = conn.execute("""
                    SELECT pnl_pct FROM memecoin_trades
                    WHERE status = 'CLOSED' AND closed_ts_utc IS NOT NULL
                      AND pnl_pct IS NOT NULL
                      AND entry_window = ? AND entry_fuel_quality = ? AND entry_move_phase = ?
                    ORDER BY closed_ts_utc DESC LIMIT 20
                """, (ew, fq, mp)).fetchall()
                _lpnls297 = [float(r["pnl_pct"]) for r in _sl297]
                if len(_lpnls297) >= 5:
                    _t297 = _lpnls297[:10]; _tn297 = len(_t297)
                    _twr297 = sum(1 for p in _t297 if p > 0) / _tn297 * 100
                    _lwr297 = sum(1 for p in _lpnls297 if p > 0) / len(_lpnls297) * 100
                    if   _twr297 >= 55.0:                              _setup_status297 = "WORKING"
                    elif _lwr297 >= 50.0 and _twr297 < (_lwr297 - 20): _setup_status297 = "DEGRADING"
                    elif _twr297 < 35.0:                               _setup_status297 = "FAILING"

            # 4. Build evidence reasons (same sourcing pattern as Patch 296)
            _tp297: list = []  # trust_positive
            _tc297: list = []  # trust_caution
            _tu297: list = []  # trust_upgrade

            _psign297 = "+" if (_pavg or 0) >= 0 else ""
            if _ptier == "PROVEN_POSITIVE":
                _tp297.append(f"PROVEN_POSITIVE all-time \u2014 avg {_psign297}{_pavg}% WR {_pwr:.0f}% (n={_pn})")
            elif _ptier == "TESTED_NEUTRAL":
                _tp297.append(f"TESTED_NEUTRAL all-time \u2014 avg {_psign297}{_pavg}% (n={_pn})")
            elif _ptier == "PROVEN_NEGATIVE":
                _tc297.append(f"PROVEN_NEGATIVE all-time \u2014 avg {_pavg}% (n={_pn})")
            else:
                _tc297.append(f"symbol unproven \u2014 {_pn} outcome(s) so far")

            _csign297 = "+" if (_cavg297 or 0) >= 0 else ""
            if _ctier297 == "SURVIVOR":
                _tp297.append(f"correction SURVIVOR \u2014 avg {_csign297}{_cavg297}% (n={_cn297})")
            elif _ctier297 == "CASUALTY":
                _tc297.append(f"correction CASUALTY \u2014 avg {_cavg297}% (n={_cn297})")
            elif _ctier297 == "TRACKER":
                _tc297.append(f"correction TRACKER \u2014 avg {_cavg297}% (n={_cn297})")
            else:
                _tc297.append("no correction-period outcomes (< 2 in 14d window)")

            if _setup_status297 == "WORKING":
                _tp297.append("setup ledger WORKING \u2014 recent WR \u2265 55%")
            elif _setup_status297 == "DEGRADING":
                _tc297.append("setup ledger DEGRADING \u2014 WR declining vs historical")
            elif _setup_status297 == "FAILING":
                _tc297.append("setup ledger FAILING \u2014 recent WR < 35%")
            else:
                _tc297.append("setup ledger thin \u2014 insufficient data")

            if _avg_loss297 is not None and _avg_loss297 < -20.0 and _n_loss297 >= 3:
                _tc297.append(f"avg loss depth {_avg_loss297}% when setup fails (n={_n_loss297})")
            elif _avg_loss297 is not None and _avg_loss297 >= -10.0 and _n_loss297 >= 3:
                _tp297.append(f"loss profile shallow \u2014 avg {_avg_loss297}% on losses (n={_n_loss297})")

            # 5. Decision tree (identical to Patch 296 _compute_trust)
            if _ptier == "PROVEN_NEGATIVE" or _ctier297 == "CASUALTY":
                _tlabel297 = "DISTRUST"
                if _ctier297 == "CASUALTY":
                    _tu297.append("DISTRUST lifts when correction CASUALTY resolves (F\u0026G recovery \u2265 35)")
                if _ptier == "PROVEN_NEGATIVE":
                    _gap297 = max(0, 15 - _pn)
                    _tu297.append(f"sustained positive outcomes required to requalify tier ({_gap297} more at min)")
            elif (_ptier == "PROVEN_POSITIVE"
                  and _ctier297 == "SURVIVOR"
                  and _setup_status297 in ("WORKING", "INSUFFICIENT_DATA")):
                _tlabel297 = "HIGH_TRUST"
                if _setup_status297 == "INSUFFICIENT_DATA":
                    _tc297.append("setup ledger unconfirmed \u2014 HIGH_TRUST pending ledger data")
                    _tu297.append("5+ outcomes confirm setup ledger WORKING")
            elif (_ptier == "UNPROVEN"
                  and _ctier297 == "UNRATED"
                  and _setup_status297 == "INSUFFICIENT_DATA"):
                _tlabel297 = "LOW_TRUST"
                _gap297 = max(0, 8 - _pn)
                if _gap297 > 0:
                    _tu297.append(f"{_gap297} more outcomes \u2192 TESTED_NEUTRAL threshold")
                _tu297.append("2 correction-period outcomes \u2192 correction tier classification")
            else:
                _tlabel297 = "CONDITIONAL_TRUST"
                if _ptier == "TESTED_NEUTRAL":
                    _gap297 = max(0, 15 - _pn)
                    if _gap297 > 0:
                        _tu297.append(f"{_gap297} more outcomes + WR \u2265 50% + avg \u2265 +5% \u2192 PROVEN_POSITIVE")
                if _ctier297 in ("TRACKER", "UNRATED"):
                    _tu297.append("correction avg improvement above -5% threshold \u2192 SURVIVOR")
                if _setup_status297 in ("DEGRADING", "FAILING", "INSUFFICIENT_DATA"):
                    _tu297.append("setup ledger recovery to WORKING")

            # 6. Trust summary (one sentence)
            _pp297 = _tp297[0] if _tp297 else None
            _pc297 = _tc297[0] if _tc297 else None
            _sparts297 = [_tlabel297.replace("_", " ")]
            if _pp297:
                _sparts297.append(_pp297)
            if _pc297 and _tlabel297 != "HIGH_TRUST":
                _sparts297.append(f"caution: {_pc297}")
            _tsummary297 = " \u2014 ".join(_sparts297)

            # Patch 306: read triage_state from most recent persisted MSO row.
            # Persisted by _fmt_cand() in the NBA pipeline at scan time.
            _triage_row306 = conn.execute("""
                SELECT triage_state FROM memecoin_signal_outcomes
                WHERE symbol = ? AND triage_state IS NOT NULL
                ORDER BY scanned_at DESC LIMIT 1
            """, (symbol,)).fetchone()
            _triage_state306 = _triage_row306["triage_state"] if _triage_row306 else None

            now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            return {
                "symbol":           symbol,
                "identity":         lc,
                "scan_history":     scan_history,
                "trade_history":    trades,
                "perf_tier":        _ptier,
                "perf_n":           _pn,
                "avg_return":       round(_pavg, 1) if _pavg is not None else None,
                "correction_tier":  _ctier297,                     # Patch 297
                "correction_avg":   round(_cavg297, 1) if _cavg297 is not None else None,  # Patch 297
                "correction_n":     _cn297,                        # Patch 297
                "trust_label":      _tlabel297,                    # Patch 297
                "trust_positive":   _tp297,                        # Patch 297
                "trust_caution":    _tc297,                        # Patch 297
                "trust_upgrade":    _tu297,                        # Patch 297
                "trust_summary":    _tsummary297,                  # Patch 297
                "triage_state":     _triage_state306,              # Patch 306
                "generated_at":     now_iso,
            }

    try:
        return await asyncio.to_thread(_run)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ── Patch 286: Setup Performance Ledger + Exit-Shape Attribution ──────────────

@router.get("/setup-performance-ledger")
async def setup_performance_ledger_ep(_: str = Depends(get_current_user)):
    """
    Patch 286 — Setup Performance Ledger with Exit-Shape Attribution.

    Extends Patch 282 with outcome-shape learning: each setup key now tracks
    not just win/loss rate but HOW it wins or fails.

    Exit shape buckets (derived from exit_reason x pnl_pct):
      TP_CLEAN      — exit_reason='TP_2X' (full take-profit)
      SL_STANDARD   — exit_reason='SL_50' AND pnl >= -55% (normal SL)
      SL_SLIPPAGE   — exit_reason='SL_50' AND pnl < -55%  (crash/pre-rug signal)
      RUG           — exit_reason='RUG_NO_LIQ' or pnl <= -95% (wipeout)
      MANUAL        — exit_reason contains 'MANUAL' (operator close; no learning)

    Shape quality label per setup:
      CLEAN_EXITS     — TP_CLEAN >= 30% AND (SL_SLIPPAGE + RUG) < 20%
      SLIPPAGE_HEAVY  — SL_SLIPPAGE / (SL_STANDARD + SL_SLIPPAGE) >= 40%
      RUG_PRONE       — RUG / non-MANUAL exits >= 25%
      INSUFFICIENT_DATA — fewer than 5 non-MANUAL exits for this setup

    Global section covers ALL closed trades regardless of lifecycle tag —
    immediately useful from existing pre-Patch 272 data.
    Per-setup breakdown auto-populates as lifecycle-tagged trades close.

    Status rules (trailing_n = min(10, total trades for this setup key)):
      WORKING          -- trailing win rate >= 55% with >= 5 trailing trades
      DEGRADING        -- trailing wr < (lifetime wr - 20pp) with >= 5 trailing trades
      FAILING          -- trailing win rate < 35% with >= 5 trailing trades
      INSUFFICIENT_DATA -- fewer than 5 trailing trades for this setup
    """
    _ensure_engine_path()

    def _run() -> dict:
        import sqlite3
        from datetime import datetime, timezone
        from collections import defaultdict

        _SHAPE_KEYS  = ("TP_CLEAN", "SL_STANDARD", "SL_SLIPPAGE", "RUG", "MANUAL")
        _EMPTY_SHAPE = {k: 0 for k in _SHAPE_KEYS}

        def _exit_shape(exit_reason, pnl_pct) -> str:
            er  = str(exit_reason or "").upper()
            pnl = float(pnl_pct or 0)
            if er == "TP_2X":
                return "TP_CLEAN"
            if er.startswith("PROOF_SL_"):
                return "SL_SLIPPAGE" if pnl < -55 else "SL_STANDARD"
            if er in ("PROOF_WINDOW_LOST", "PROOF_TIME_REVIEW"):
                return "SL_STANDARD" if pnl < 0 else "MANUAL"
            if er == "RUG_NO_LIQ" or pnl <= -95:
                return "RUG"
            if er == "SL_50":
                return "SL_SLIPPAGE" if pnl < -55 else "SL_STANDARD"
            if "MANUAL" in er:
                return "MANUAL"
            # Fallback for any unrecognised future reason strings
            if pnl > 20:   return "TP_CLEAN"
            if pnl <= -95: return "RUG"
            if pnl < -55:  return "SL_SLIPPAGE"
            if pnl < 0:    return "SL_STANDARD"
            return "MANUAL"

        def _shape_quality(shapes: dict) -> str:
            total      = sum(shapes.values())
            non_manual = total - shapes.get("MANUAL", 0)
            if non_manual < 5:
                return "INSUFFICIENT_DATA"
            sl_exits  = shapes["SL_STANDARD"] + shapes["SL_SLIPPAGE"]
            slip_rate = shapes["SL_SLIPPAGE"] / sl_exits if sl_exits else 0
            rug_rate  = shapes["RUG"] / non_manual
            tp_rate   = shapes["TP_CLEAN"] / total
            bad_rate  = (shapes["SL_SLIPPAGE"] + shapes["RUG"]) / total
            if rug_rate >= 0.25:
                return "RUG_PRONE"
            if slip_rate >= 0.40:
                return "SLIPPAGE_HEAVY"
            if tp_rate >= 0.30 and bad_rate < 0.20:
                return "CLEAN_EXITS"
            return "INSUFFICIENT_DATA"

        def _rates(shapes: dict) -> tuple:
            sl_exits   = shapes["SL_STANDARD"] + shapes["SL_SLIPPAGE"]
            non_manual = sum(shapes.values()) - shapes.get("MANUAL", 0)
            slip_rate  = round(shapes["SL_SLIPPAGE"] / sl_exits, 3) if sl_exits >= 2 else None
            rug_rate   = round(shapes["RUG"] / non_manual, 3) if non_manual >= 3 else None
            return slip_rate, rug_rate

        def _status(lifetime_wr: float, trailing_n: int, trailing_wr: float) -> str:
            if trailing_n < 5:
                return "INSUFFICIENT_DATA"
            if trailing_wr >= 55.0:
                return "WORKING"
            if lifetime_wr >= 50.0 and trailing_wr < (lifetime_wr - 20.0):
                return "DEGRADING"
            if trailing_wr < 35.0:
                return "FAILING"
            return "INSUFFICIENT_DATA"

        with sqlite3.connect(_db_path()) as conn:
            conn.row_factory = sqlite3.Row

            # Lifecycle-tagged trades — per-setup ledger
            tagged_rows = conn.execute("""
                SELECT entry_window, entry_fuel_quality, entry_move_phase,
                       pnl_pct, exit_reason, closed_ts_utc
                FROM memecoin_trades
                WHERE closed_ts_utc      IS NOT NULL
                  AND entry_window       IS NOT NULL
                  AND entry_fuel_quality IS NOT NULL
                  AND entry_move_phase   IS NOT NULL
                ORDER BY closed_ts_utc ASC
            """).fetchall()

            # All closed trades — for global exit-shape view (pre-Patch 272 included)
            all_closed = conn.execute("""
                SELECT exit_reason, pnl_pct
                FROM memecoin_trades
                WHERE closed_ts_utc IS NOT NULL AND pnl_pct IS NOT NULL
            """).fetchall()

            total_closed = conn.execute(
                "SELECT COUNT(*) FROM memecoin_trades WHERE closed_ts_utc IS NOT NULL"
            ).fetchone()[0]

        tagged_count = len(tagged_rows)

        # ── Global exit-shape breakdown (all trades, no lifecycle filter) ─────
        global_shapes = dict(_EMPTY_SHAPE)
        for r in all_closed:
            s = _exit_shape(r["exit_reason"], r["pnl_pct"])
            global_shapes[s] = global_shapes[s] + 1

        global_slip_rate, global_rug_rate = _rates(global_shapes)
        global_shape_quality              = _shape_quality(global_shapes)

        # ── Per-setup ledger ──────────────────────────────────────────────────
        pnl_buckets:   dict = defaultdict(list)
        shape_buckets: dict = defaultdict(lambda: dict(_EMPTY_SHAPE))

        for r in tagged_rows:
            key = (r["entry_window"], r["entry_fuel_quality"], r["entry_move_phase"])
            pnl_buckets[key].append(float(r["pnl_pct"] or 0))
            s = _exit_shape(r["exit_reason"], r["pnl_pct"])
            shape_buckets[key][s] = shape_buckets[key][s] + 1

        TRAILING_N = 10
        setups     = []

        for (ew, fq, mp), pnl_list in pnl_buckets.items():
            lifetime_n    = len(pnl_list)
            lifetime_wins = sum(1 for p in pnl_list if p > 0)
            lifetime_wr   = round(lifetime_wins / lifetime_n * 100, 1) if lifetime_n else 0.0
            lifetime_avg  = round(sum(pnl_list)  / lifetime_n, 2)      if lifetime_n else 0.0

            trailing      = pnl_list[-TRAILING_N:]
            trailing_n    = len(trailing)
            trailing_wins = sum(1 for p in trailing if p > 0)
            trailing_wr   = round(trailing_wins / trailing_n * 100, 1) if trailing_n else 0.0
            trailing_avg  = round(sum(trailing)  / trailing_n, 2)      if trailing_n else 0.0

            shapes              = dict(shape_buckets[(ew, fq, mp)])
            slip_rate, rug_rate = _rates(shapes)

            setups.append({
                "setup_key":        f"{ew} / {fq} / {mp}",
                "entry_window":     ew,
                "fuel_quality":     fq,
                "move_phase":       mp,
                "lifetime_n":       lifetime_n,
                "lifetime_wins":    lifetime_wins,
                "lifetime_wr":      lifetime_wr,
                "lifetime_avg_pnl": lifetime_avg,
                "trailing_n":       trailing_n,
                "trailing_wins":    trailing_wins,
                "trailing_wr":      trailing_wr,
                "trailing_avg_pnl": trailing_avg,
                "status":           _status(lifetime_wr, trailing_n, trailing_wr),
                # ── Exit-shape attribution ────────────────────────────────────
                "exit_shapes":      shapes,
                "slippage_rate":    slip_rate,
                "rug_rate":         rug_rate,
                "shape_quality":    _shape_quality(shapes),
            })

        _order = {"WORKING": 0, "DEGRADING": 1, "FAILING": 2, "INSUFFICIENT_DATA": 3}
        setups.sort(key=lambda x: (_order.get(x["status"], 4), -x["trailing_wr"]))

        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return {
            "setups":               setups,
            "total_closed":         total_closed,
            "tagged":               tagged_count,
            "untagged":             total_closed - tagged_count,
            # ── Global exit-shape view (all closed trades) ────────────────────
            "global_shapes":        global_shapes,
            "global_slippage_rate": global_slip_rate,
            "global_rug_rate":      global_rug_rate,
            "global_shape_quality": global_shape_quality,
            "generated_at":         now_iso,
        }

    try:
        return await asyncio.to_thread(_run)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))




# ── Patch 285: Adaptive Next-Best-Action Recommendation Engine ────────────────

@router.get("/next-best-action")
async def next_best_action_ep(_: str = Depends(get_current_user)):
    _memecoins_v2_retired("/api/memecoins/next-best-action")
    """
    Patch 285 — Adaptive Next-Best-Action Recommendation Engine.

    Upgraded from Patch 284 synthesis to a full recommendation engine that:
      - Compares candidates and explains why one wins over others
      - Returns explicit upgrade / downgrade conditions for the primary action
      - Distinguishes DO_NOT_TOUCH (hard blockers) from HOLD (soft / quiet)
      - Synthesises dominant blocker reason across the evaluated pool
      - Uses setup ledger status in explicit named confidence rules

    Recommendation states:
      EXECUTE_PAPER   — a candidate passes all paper gates
      WATCH           — structural lifecycle gates pass, no scored signal yet
      HOLD            — no candidates pass; pool is soft / quiet
      DO_NOT_TOUCH    — signals exist but hard blockers dominate (TRAP fuel /
                         DANGER rug / recent loss on every candidate)
      NO_DATA         — scan cache empty and no lifecycle candidates qualify

    Confidence (named rules):
      HIGH   — score clearly above threshold + flc=1 + STRONG fuel + setup=WORKING
      MEDIUM — all gates pass + (INSUFFICIENT_DATA ledger or score barely clears)
      LOW    — passes but (DEGRADING ledger or score ≤ min+5 or lifecycle-only)
    """
    _ensure_engine_path()

    def _run() -> dict:
        import json as _json
        import sqlite3 as _sq3
        from datetime import datetime, timezone, timedelta

        # ── Patch 287: exit-shape helpers for global confidence feedback ──────
        _SHAPE_KEYS  = ("TP_CLEAN", "SL_STANDARD", "SL_SLIPPAGE", "RUG", "MANUAL")
        _EMPTY_SHAPE = {k: 0 for k in _SHAPE_KEYS}

        def _exit_shape_g(exit_reason, pnl_pct) -> str:
            er  = str(exit_reason or "").upper()
            pnl = float(pnl_pct or 0)
            if er == "TP_2X":                    return "TP_CLEAN"
            if er.startswith("PROOF_SL_"):       return "SL_SLIPPAGE" if pnl < -55 else "SL_STANDARD"
            if er in ("PROOF_WINDOW_LOST", "PROOF_TIME_REVIEW"):
                return "SL_STANDARD" if pnl < 0 else "MANUAL"
            if er == "RUG_NO_LIQ" or pnl <= -95: return "RUG"
            if er == "SL_50":   return "SL_SLIPPAGE" if pnl < -55 else "SL_STANDARD"
            if "MANUAL" in er:                   return "MANUAL"
            if pnl > 20:   return "TP_CLEAN"
            if pnl <= -95: return "RUG"
            if pnl < -55:  return "SL_SLIPPAGE"
            if pnl < 0:    return "SL_STANDARD"
            return "MANUAL"

        def _shape_quality_g(shapes: dict) -> str:
            total      = sum(shapes.values())
            non_manual = total - shapes.get("MANUAL", 0)
            if non_manual < 5:                             return "INSUFFICIENT_DATA"
            sl_exits  = shapes["SL_STANDARD"] + shapes["SL_SLIPPAGE"]
            slip_rate = shapes["SL_SLIPPAGE"] / sl_exits if sl_exits else 0
            rug_rate  = shapes["RUG"] / non_manual
            tp_rate   = shapes["TP_CLEAN"] / total
            bad_rate  = (shapes["SL_SLIPPAGE"] + shapes["RUG"]) / total
            if rug_rate >= 0.25:                            return "RUG_PRONE"
            if slip_rate >= 0.40:                           return "SLIPPAGE_HEAVY"
            if tp_rate >= 0.30 and bad_rate < 0.20:         return "CLEAN_EXITS"
            return "INSUFFICIENT_DATA"

        _FUEL_RANK  = {"STRONG": 3, "MODERATE": 2, "WEAK": 1, "TRAP": 0}
        _PHASE_RANK = {"IGNITION": 5, "EARLY": 4, "MID": 3, "RELOAD": 2,
                       "REVIVAL": 1, "EXTENDED": -1, "CHURN": -2}
        _LEDGER_RANK = {"WORKING": 3, "INSUFFICIENT_DATA": 2,
                        "DEGRADING": 1, "FAILING": 0}
        # Patch 289: historical performance rank contribution
        # PROVEN_NEGATIVE overrides structural quality — a token that reliably
        # loses money should not surface as top recommendation regardless of
        # current lifecycle state.  PROVEN_POSITIVE earns a rank bonus.
        _PERF_RANK  = {"PROVEN_POSITIVE": 2, "TESTED_NEUTRAL": 0,
                       "UNPROVEN": 0, "PROVEN_NEGATIVE": -3}

        now     = datetime.now(timezone.utc)
        now_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")

        with _sq3.connect(_db_path()) as conn:
            conn.row_factory = _sq3.Row

            # ── Learned thresholds ────────────────────────────────────────────
            thresh_row = conn.execute(
                "SELECT value FROM kv_store WHERE key = 'memecoin_learned_thresholds'"
            ).fetchone()
            thresholds = {}
            if thresh_row and thresh_row["value"]:
                try:
                    outer      = _json.loads(thresh_row["value"])
                    thresholds = outer.get("thresholds", outer)
                except Exception:
                    pass
            score_min  = float(thresholds.get("min_score",             20))
            score_max  = float(thresholds.get("max_score",             45))
            vacc_min   = float(thresholds.get("min_vol_acceleration", 5.6))
            holder_max = float(thresholds.get("max_top_holder_pct",   4.7))

            # ── F&G ───────────────────────────────────────────────────────────
            # Patch 291: use get_fear_greed() instead of raw kv_store read so
            # the 15-min TTL refresh fires from this path and source is explicit.
            fg_val = None; fg_label = "unknown"; fg_favorable = False
            fg_source = "alternative.me"
            try:
                from utils.agent_coordinator import get_fear_greed  # type: ignore
                _fg_data  = get_fear_greed()
                fg_val    = _fg_data.get("value")
                fg_label  = str(_fg_data.get("label", "unknown"))
                fg_favorable = bool(_fg_data.get("favorable", False))
                fg_source = str(_fg_data.get("source", "alternative.me"))
            except Exception:
                pass

            # ── Scan cache ────────────────────────────────────────────────────
            cache_row = conn.execute(
                "SELECT value FROM kv_store WHERE key = 'memecoin_scan_cache'"
            ).fetchone()
            signals     = []
            data_source = "scan_cache"
            if cache_row and cache_row["value"]:
                try:
                    raw = _json.loads(cache_row["value"])
                    signals = raw if isinstance(raw, list) else []
                except Exception:
                    pass

            # ── Lifecycle map ─────────────────────────────────────────────────
            lc_rows = conn.execute(
                "SELECT symbol, mint, entry_window, fuel_quality, move_phase, "
                "first_leg_confirmed FROM symbol_lifecycle"
            ).fetchall()
            lc_by_mint   = {}
            lc_by_symbol = {}
            for r in lc_rows:
                rec = dict(r)
                if rec.get("mint"):
                    lc_by_mint[rec["mint"]] = rec
                lc_by_symbol[rec["symbol"]] = rec

            def _memory_snapshot(pnls: list) -> dict:
                def _norm(obs_list: list) -> list[tuple[float, float]]:
                    out = []
                    for obs in obs_list:
                        if isinstance(obs, (tuple, list)):
                            pnl = float(obs[0])
                            wt = float(obs[1]) if len(obs) > 1 else 1.0
                        else:
                            pnl = float(obs)
                            wt = 1.0
                        out.append((pnl, max(wt, 0.0)))
                    return out

                t = _norm(pnls[-10:])
                tn = len(t)
                tw = sum(w for _, w in t)
                twr = (sum(w for p, w in t if p > 0) / tw * 100.0) if tw else 0.0
                l = _norm(pnls)
                ln = len(l)
                lw = sum(w for _, w in l)
                lwr = (sum(w for p, w in l if p > 0) / lw * 100.0) if lw else 0.0
                tavg = round(sum(p * w for p, w in t) / tw, 1) if tw else None
                lavg = round(sum(p * w for p, w in l) / lw, 1) if lw else None
                tcat = round(sum(w for p, w in t if p <= -20.0) / tw * 100, 1) if tw else None
                wr_delta = round(twr - float(lwr or 0.0), 1) if tn and ln else None
                avg_delta = round(float(tavg or 0.0) - float(lavg or 0.0), 1) if tn and ln and tavg is not None and lavg is not None else None
                if tw < 4.5:
                    mem_state = "THIN"
                elif (
                    twr >= 60.0
                    and (tavg or 0.0) > 5.0
                    and (tcat or 0.0) < 20.0
                    and (wr_delta is None or wr_delta >= -5.0)
                ):
                    mem_state = "COMPOUNDING"
                elif (
                    twr < 40.0
                    or (tavg or 0.0) < 0.0
                    or (tcat or 0.0) >= 30.0
                    or (wr_delta is not None and wr_delta <= -20.0)
                ):
                    mem_state = "FRAGILE"
                else:
                    mem_state = "MIXED"
                if tw >= 8.0 and lw >= 12.0:
                    confidence = "HIGH"
                elif tw >= 5.0:
                    confidence = "MEDIUM"
                else:
                    confidence = "LOW"
                return {
                    "state": mem_state,
                    "confidence": confidence,
                    "trailing_n": tn,
                    "trailing_weighted_n": round(tw, 1),
                    "trailing_wr": round(twr, 1) if tn else None,
                    "trailing_avg": tavg,
                    "cat_rate": tcat,
                    "lifetime_n": ln,
                    "lifetime_weighted_n": round(lw, 1),
                    "lifetime_wr": round(lwr, 1) if ln else None,
                    "lifetime_avg": lavg,
                    "wr_delta": wr_delta,
                    "avg_delta": avg_delta,
                }

            # ── Setup ledger (compact trailing-10) ────────────────────────────
            trade_rows = conn.execute("""
                SELECT entry_window, entry_fuel_quality, entry_move_phase, pnl_pct, closed_ts_utc
                FROM memecoin_trades
                WHERE status = 'CLOSED'
                  AND closed_ts_utc IS NOT NULL
                  AND pnl_pct IS NOT NULL
                  AND entry_window IS NOT NULL
                  AND entry_fuel_quality IS NOT NULL
                  AND entry_move_phase IS NOT NULL
                ORDER BY closed_ts_utc ASC
            """).fetchall()
            _buckets: dict = {}
            _fresh_reinforced_archetype_buckets: dict = {}
            _reinforced_archetype_buckets: dict = {}
            _archetype_buckets: dict = {}
            _profile_buckets: dict = {}
            _window_phase_buckets: dict = {}
            _phase_buckets: dict = {}
            _capital_posture_buckets: dict = {}
            _capital_ready_buckets: dict = {}
            _capital_pressure_buckets: dict = {}
            _capital_regime_buckets: dict = {}
            _capital_mix_buckets: dict = {}
            _capital_allocator_stance_buckets: dict = {}
            _capital_routing_family_buckets: dict = {}
            _capital_headroom_buckets: dict = {}
            _capital_route_buckets: dict = {}
            _deployment_authority_buckets: dict = {}
            _decision_authority_buckets: dict = {}
            _capital_deployment_family_buckets: dict = {}
            _capital_intensity_buckets: dict = {}
            _capital_window_buckets: dict = {}
            _fresh_catalyst_buckets: dict = {}
            _freshness_buckets: dict = {}
            for tr in trade_rows:
                k = (tr["entry_window"], tr["entry_fuel_quality"], tr["entry_move_phase"])
                ak = (_continuation_archetype(tr["entry_window"], tr["entry_fuel_quality"], tr["entry_move_phase"]),)
                rak = (ak[0], "PLAIN")
                pk = (tr["entry_fuel_quality"], tr["entry_move_phase"])
                wk = (tr["entry_window"], tr["entry_move_phase"])
                mk = (tr["entry_move_phase"],)
                pnl = (
                    float(tr["pnl_pct"]),
                    _continuation_recency_weight(tr["closed_ts_utc"]),
                )
                _buckets.setdefault(k, []).append(pnl)
                _reinforced_archetype_buckets.setdefault(rak, []).append(pnl)
                _archetype_buckets.setdefault(ak, []).append(pnl)
                _profile_buckets.setdefault(pk, []).append(pnl)
                _window_phase_buckets.setdefault(wk, []).append(pnl)
                _phase_buckets.setdefault(mk, []).append(pnl)
            _surface_rows = conn.execute("""
                SELECT entry_window, fuel_quality, move_phase,
                       continuation_archetype, support_overlap_score,
                       support_overlap_tags, capital_posture, capital_ready_state,
                       capital_suggested_entry_usd, capital_pressure_bucket, capital_regime_bucket, capital_mix_bucket, capital_allocator_stance, capital_headroom_bucket, capital_route_bucket, marginal_route, proof_stack_authority, reinforcement_authority, promotion_authority, deployment_authority, continuation_memory_authority, fresh_discovery_authority, fresh_catalyst_bucket, freshness_bucket,
                       return_24h_pct, surfaced_at
                FROM research_surface_log
                WHERE source = 'HOME_QUEUE'
                  AND outcome_status = 'RESOLVED'
                  AND return_24h_pct IS NOT NULL
                  AND entry_window IS NOT NULL
                  AND fuel_quality IS NOT NULL
                  AND move_phase IS NOT NULL
                ORDER BY surfaced_at ASC
            """).fetchall()
            for sr in _surface_rows:
                k = (sr["entry_window"], sr["fuel_quality"], sr["move_phase"])
                _arch = str(sr["continuation_archetype"] or "").strip() or _continuation_archetype(
                    sr["entry_window"], sr["fuel_quality"], sr["move_phase"]
                )
                ak = (_arch,)
                _support_bucket = _support_reinforcement_bucket(
                    sr["support_overlap_score"] if "support_overlap_score" in sr.keys() else None,
                    json.loads(sr["support_overlap_tags"] or "[]") if ("support_overlap_tags" in sr.keys() and sr["support_overlap_tags"]) else [],
                )
                _fresh_bucket = str(sr["freshness_bucket"] or "UNKNOWN").strip().upper() or "UNKNOWN"
                _fresh_catalyst_bucket_value = str(sr["fresh_catalyst_bucket"] or "UNKNOWN").strip().upper() or "UNKNOWN"
                frak = (_arch, _support_bucket, _fresh_bucket)
                rak = (_arch, _support_bucket)
                pk = (sr["fuel_quality"], sr["move_phase"])
                wk = (sr["entry_window"], sr["move_phase"])
                mk = (sr["move_phase"],)
                cpk = str(sr["capital_posture"] or "UNKNOWN").strip() or "UNKNOWN"
                crk = str(sr["capital_ready_state"] or "UNKNOWN").strip() or "UNKNOWN"
                cxb = str(sr["capital_pressure_bucket"] or "UNKNOWN").strip().upper() or "UNKNOWN"
                rgb = str(sr["capital_regime_bucket"] or "UNKNOWN").strip().upper() or "UNKNOWN"
                cmb = str(sr["capital_mix_bucket"] or "UNKNOWN").strip().upper() or "UNKNOWN"
                cas = str(sr["capital_allocator_stance"] or "UNKNOWN").strip().upper() or "UNKNOWN"
                chb = str(sr["capital_headroom_bucket"] or "UNKNOWN").strip().upper() or "UNKNOWN"
                crb = str(sr["capital_route_bucket"] or "UNKNOWN").strip().upper() or "UNKNOWN"
                cmr = str(sr["marginal_route"] or _capital_marginal_route(crb)).strip().upper() or "UNKNOWN"
                crf = _capital_routing_family(cas, crb, cmr)
                cda = _deployment_authority(
                    sr["promotion_authority"] if "promotion_authority" in sr.keys() else None,
                    sr["reinforcement_authority"] if "reinforcement_authority" in sr.keys() else None,
                    crk,
                    crb,
                    chb,
                )
                dsa = _decision_authority(
                    sr["proof_stack_authority"] if "proof_stack_authority" in sr.keys() else None,
                    sr["continuation_memory_authority"] if "continuation_memory_authority" in sr.keys() else None,
                    sr["fresh_discovery_authority"] if "fresh_discovery_authority" in sr.keys() else None,
                    sr["reinforcement_authority"] if "reinforcement_authority" in sr.keys() else None,
                    sr["promotion_authority"] if "promotion_authority" in sr.keys() else None,
                    sr["deployment_authority"] if "deployment_authority" in sr.keys() else cda,
                )
                cdf = _capital_deployment_family(cpk, crk, cxb, rgb)
                cib = _capital_intensity_bucket(sr["capital_suggested_entry_usd"] or 0.0)
                fxb = str(sr["freshness_bucket"] or "UNKNOWN").strip().upper() or "UNKNOWN"
                pnl = (
                    float(sr["return_24h_pct"]),
                    round(0.6 * _continuation_recency_weight(sr["surfaced_at"]), 3),
                )
                _buckets.setdefault(k, []).append(pnl)
                _fresh_reinforced_archetype_buckets.setdefault(frak, []).append(pnl)
                _reinforced_archetype_buckets.setdefault(rak, []).append(pnl)
                _archetype_buckets.setdefault(ak, []).append(pnl)
                _profile_buckets.setdefault(pk, []).append(pnl)
                _window_phase_buckets.setdefault(wk, []).append(pnl)
                _phase_buckets.setdefault(mk, []).append(pnl)
                _capital_posture_buckets.setdefault(cpk, []).append(pnl)
                _capital_ready_buckets.setdefault(crk, []).append(pnl)
                _capital_pressure_buckets.setdefault(cxb, []).append(pnl)
                _capital_regime_buckets.setdefault(rgb, []).append(pnl)
                _capital_mix_buckets.setdefault(cmb, []).append(pnl)
                _capital_allocator_stance_buckets.setdefault(cas, []).append(pnl)
                _capital_routing_family_buckets.setdefault(crf, []).append(pnl)
                _capital_headroom_buckets.setdefault(chb, []).append(pnl)
                _capital_route_buckets.setdefault(crb, []).append(pnl)
                _deployment_authority_buckets.setdefault(cda, []).append(pnl)
                _decision_authority_buckets.setdefault(dsa, []).append(pnl)
                _capital_deployment_family_buckets.setdefault(cdf, []).append(pnl)
                _capital_intensity_buckets.setdefault(cib, []).append(pnl)
                _fresh_catalyst_buckets.setdefault(_fresh_catalyst_bucket_value, []).append(pnl)
                _freshness_buckets.setdefault(fxb, []).append(pnl)
            ledger_status: dict = {}
            setup_memory: dict = {}
            fresh_reinforced_archetype_memory: dict = {}
            reinforced_archetype_memory: dict = {}
            archetype_memory: dict = {}
            profile_memory: dict = {}
            window_phase_memory: dict = {}
            phase_memory: dict = {}
            capital_posture_memory: dict = {}
            capital_ready_state_memory: dict = {}
            capital_pressure_bucket_memory: dict = {}
            capital_regime_bucket_memory: dict = {}
            capital_mix_bucket_memory: dict = {}
            capital_allocator_stance_memory: dict = {}
            capital_routing_family_memory: dict = {}
            capital_headroom_bucket_memory: dict = {}
            capital_route_bucket_memory: dict = {}
            deployment_authority_memory: dict = {}
            decision_authority_memory: dict = {}
            capital_deployment_family_memory: dict = {}
            capital_intensity_bucket_memory: dict = {}
            capital_window_bucket_memory: dict = {}
            fresh_catalyst_bucket_memory: dict = {}
            freshness_bucket_memory: dict = {}
            for k, pnls in _fresh_reinforced_archetype_buckets.items():
                fresh_reinforced_archetype_memory[k] = _memory_snapshot(pnls)
            for k, pnls in _reinforced_archetype_buckets.items():
                reinforced_archetype_memory[k] = _memory_snapshot(pnls)
            for k, pnls in _buckets.items():
                def _norm_pnls(obs_list: list) -> list[float]:
                    out: list[float] = []
                    for obs in obs_list:
                        if isinstance(obs, (tuple, list)):
                            out.append(float(obs[0]))
                        else:
                            out.append(float(obs))
                    return out

                t = _norm_pnls(pnls[-10:])
                tn  = len(t);  tw  = sum(1 for p in t if p > 0)
                twr = tw / tn * 100 if tn else 0.0
                l = _norm_pnls(pnls)
                ln  = len(l); lw = sum(1 for p in l if p > 0)
                lwr = lw / ln * 100 if ln else 0.0
                if tn < 5:             st = "INSUFFICIENT_DATA"
                elif twr >= 55.0:      st = "WORKING"
                elif lwr >= 50.0 and twr < (lwr - 20.0): st = "DEGRADING"
                elif twr < 35.0:       st = "FAILING"
                else:                  st = "INSUFFICIENT_DATA"
                ledger_status[k] = st
                setup_memory[k] = _memory_snapshot(pnls)
            for k, pnls in _archetype_buckets.items():
                archetype_memory[k] = _memory_snapshot(pnls)
            for k, pnls in _profile_buckets.items():
                profile_memory[k] = _memory_snapshot(pnls)
            for k, pnls in _window_phase_buckets.items():
                window_phase_memory[k] = _memory_snapshot(pnls)
            for k, pnls in _phase_buckets.items():
                phase_memory[k] = _memory_snapshot(pnls)
            for k, pnls in _capital_posture_buckets.items():
                capital_posture_memory[k] = _memory_snapshot(pnls)
            for k, pnls in _capital_ready_buckets.items():
                capital_ready_state_memory[k] = _memory_snapshot(pnls)
            for k, pnls in _capital_pressure_buckets.items():
                capital_pressure_bucket_memory[k] = _memory_snapshot(pnls)
            for k, pnls in _capital_regime_buckets.items():
                capital_regime_bucket_memory[k] = _memory_snapshot(pnls)
            for k, pnls in _capital_mix_buckets.items():
                capital_mix_bucket_memory[k] = _memory_snapshot(pnls)
            for k, pnls in _capital_allocator_stance_buckets.items():
                capital_allocator_stance_memory[k] = _memory_snapshot(pnls)
            for k, pnls in _capital_routing_family_buckets.items():
                capital_routing_family_memory[k] = _memory_snapshot(pnls)
            for k, pnls in _capital_headroom_buckets.items():
                capital_headroom_bucket_memory[k] = _memory_snapshot(pnls)
            for k, pnls in _capital_route_buckets.items():
                capital_route_bucket_memory[k] = _memory_snapshot(pnls)
            for k, pnls in _deployment_authority_buckets.items():
                deployment_authority_memory[k] = _memory_snapshot(pnls)
            for k, pnls in _decision_authority_buckets.items():
                decision_authority_memory[k] = _memory_snapshot(pnls)
            for k, pnls in _capital_deployment_family_buckets.items():
                capital_deployment_family_memory[k] = _memory_snapshot(pnls)
            for k, pnls in _capital_intensity_buckets.items():
                capital_intensity_bucket_memory[k] = _memory_snapshot(pnls)
            for k, pnls in _capital_window_buckets.items():
                capital_window_bucket_memory[k] = _memory_snapshot(pnls)
            for k, pnls in _fresh_catalyst_buckets.items():
                fresh_catalyst_bucket_memory[k] = _memory_snapshot(pnls)
            for k, pnls in _freshness_buckets.items():
                freshness_bucket_memory[k] = _memory_snapshot(pnls)

            # ── Recent 7-day loss mints (anti-repeat gate) ────────────────────
            cutoff_7d = (now - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
            loss_rows = conn.execute(
                "SELECT mint, closed_ts_utc FROM memecoin_trades "
                "WHERE pnl_pct < 0 AND closed_ts_utc >= ?",
                (cutoff_7d,)
            ).fetchall()
            loss_mints     = {r["mint"] for r in loss_rows if r["mint"]}
            loss_expiry    = {}    # mint → ISO expiry string
            for r in loss_rows:
                if r["mint"] and r["closed_ts_utc"]:
                    try:
                        from datetime import timedelta as _td
                        exp = (datetime.strptime(r["closed_ts_utc"][:19],
                               "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                               + timedelta(days=7))
                        loss_expiry[r["mint"]] = exp.strftime("%Y-%m-%d")
                    except Exception:
                        pass

            # Patch 288: last-known mcap per symbol for fallback floor enforcement
            _mcap_rows = conn.execute("""
                SELECT symbol, mcap_at_scan
                FROM memecoin_signal_outcomes
                WHERE mcap_at_scan IS NOT NULL
                  AND scanned_at = (
                      SELECT MAX(scanned_at) FROM memecoin_signal_outcomes m2
                      WHERE m2.symbol = memecoin_signal_outcomes.symbol
                        AND m2.mcap_at_scan IS NOT NULL
                  )
            """).fetchall()
            _last_mcap: dict = {r["symbol"]: float(r["mcap_at_scan"]) for r in _mcap_rows}
            _last_scan_rows = conn.execute("""
                SELECT symbol, mcap_at_scan, liquidity_usd, rug_label,
                       top_holder_pct, top5_holder_pct,
                       volume_24h, token_age_days, holder_quality_level, scanned_at
                FROM memecoin_signal_outcomes
                WHERE source = 'SCANNER'
                  AND scanned_at = (
                      SELECT MAX(scanned_at) FROM memecoin_signal_outcomes m2
                      WHERE m2.symbol = memecoin_signal_outcomes.symbol
                        AND m2.source = 'SCANNER'
                  )
            """).fetchall()
            _last_scan_sig: dict = {
                r["symbol"]: {
                    "mcap_usd":             float(r["mcap_at_scan"] or 0),
                    "liquidity_usd":        float(r["liquidity_usd"] or 0),
                    "rug_label":            r["rug_label"],
                    "top_holder_pct":       float(r["top_holder_pct"] or 0),
                    "top5_holder_pct":      float(r["top5_holder_pct"] or 0),
                    "volume_24h":           float(r["volume_24h"] or 0),
                    "token_age_days":       float(r["token_age_days"] or 0),
                    "holder_quality_level": r["holder_quality_level"],
                    "scanned_at":           r["scanned_at"],
                }
                for r in _last_scan_rows
            }

            # ── Broader lifecycle pool — blocker categorisation ───────────────
            # Count all lifecycle rows regardless of pass/fail for honest pool stats
            all_lc = conn.execute(
                "SELECT symbol, mint, entry_window, fuel_quality, move_phase, "
                "first_leg_confirmed FROM symbol_lifecycle WHERE mint IS NOT NULL"
            ).fetchall()
            # Patch 287: global closed trades for exit-shape confidence feedback
            _shape_rows = conn.execute("""
                SELECT exit_reason, pnl_pct FROM memecoin_trades
                WHERE closed_ts_utc IS NOT NULL AND pnl_pct IS NOT NULL
            """).fetchall()
            # Patch 289: per-symbol historical outcome performance from MSO
            # Returns n, win_rate (return_24h_pct >= 10%), avg_return per symbol
            # across all COMPLETE outcomes — the most direct signal of symbol reliability.
            _perf_rows = conn.execute("""
                SELECT
                    symbol,
                    COUNT(*) AS n,
                    ROUND(AVG(return_24h_pct), 1) AS avg_return,
                    ROUND(SUM(CASE WHEN return_24h_pct >= 10 THEN 1.0 ELSE 0.0 END)
                          * 100.0 / COUNT(*), 1) AS win_rate
                FROM memecoin_signal_outcomes
                WHERE status = 'COMPLETE' AND return_24h_pct IS NOT NULL
                GROUP BY symbol
            """).fetchall()
            pool_blockers: dict = {}
            for r in all_lc:
                ew  = r["entry_window"]  or ""
                fq  = r["fuel_quality"]  or ""
                mp  = r["move_phase"]    or ""
                flc = int(r["first_leg_confirmed"] or 0)
                if ew != "OPEN":
                    k = "entry_window_not_open"
                elif flc != 1:
                    k = "first_leg_not_confirmed"
                elif fq not in ("STRONG", "MODERATE"):
                    k = "weak_or_trap_fuel"
                elif mp in ("EXTENDED", "CHURN"):
                    k = "move_phase_extended_churn"
                else:
                    continue  # this one passes structural gates
                pool_blockers[k] = pool_blockers.get(k, 0) + 1

            # Patch 313: scan-appearance counts for eligible-universe computation.
            # Queries MSO directly — tells us how many times each symbol appeared
            # in scanner output over the past 30 days and when it was last seen.
            # Both values are required for the persistence + recency gates below.
            _eu_cutoff_30d = (now - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
            _eu_cutoff_14d = (now - timedelta(days=14)).strftime("%Y-%m-%d %H:%M:%S")
            _eu_mso_rows = conn.execute("""
                SELECT symbol,
                       COUNT(*)        AS cnt_30d,
                       MAX(scanned_at) AS last_scan
                FROM memecoin_signal_outcomes
                WHERE scanned_at >= ?
                GROUP BY symbol
            """, (_eu_cutoff_30d,)).fetchall()
            _eu_scan_stats: dict = {
                r["symbol"]: {"cnt": int(r["cnt_30d"]), "last": r["last_scan"]}
                for r in _eu_mso_rows
            }

        # ── Patch 289: symbol historical performance map ──────────────────────
        # Tier thresholds: PROVEN_POSITIVE  n≥15, wr≥50%, avg≥+5%
        #                  PROVEN_NEGATIVE  n≥15, avg<−10%
        #                  TESTED_NEUTRAL   n≥8  (not qualifying above)
        #                  UNPROVEN         n<8
        _sym_perf: dict = {}
        for _pr in _perf_rows:
            _pn   = int(_pr["n"])
            _pavg = float(_pr["avg_return"] or 0)
            _pwr  = float(_pr["win_rate"]   or 0)
            if _pn >= 15 and _pwr >= 50 and _pavg >= 5:
                _ptier = "PROVEN_POSITIVE"
            elif _pn >= 15 and _pavg < -10:
                _ptier = "PROVEN_NEGATIVE"
            elif _pn >= 8:
                _ptier = "TESTED_NEUTRAL"
            else:
                _ptier = "UNPROVEN"
            _sym_perf[_pr["symbol"]] = {
                "n": _pn, "wr": round(_pwr, 1),
                "avg": round(_pavg, 1), "tier": _ptier,
            }

        # ── Patch 296: correction survival classifier — moved here from the
        # quiet_market_intel branch so _corr_perf is available in both the NBA
        # scored-signal path AND the lifecycle-direct path.  14-day rolling
        # window; regime-adaptive; no stored F&G history required.
        _CORR_WINDOW_DAYS  = 14
        _CORR_SURVIVOR_AVG  = -5.0
        _CORR_CASUALTY_AVG  = -30.0
        _CORR_SURVIVAL_RATE = 0.70
        _CORR_MIN_N         = 2
        _corr_start = (now - timedelta(days=_CORR_WINDOW_DAYS)).strftime("%Y-%m-%d")
        _corr_perf: dict = {}
        try:
            _corr_rows = conn.execute("""
                SELECT symbol, return_24h_pct
                FROM memecoin_signal_outcomes
                WHERE status = 'COMPLETE'
                  AND return_24h_pct IS NOT NULL
                  AND scanned_at >= ?
            """, (_corr_start,)).fetchall()
            _corr_by_sym: dict = {}
            for _cr in _corr_rows:
                _s = _cr["symbol"]
                if _s not in _corr_by_sym:
                    _corr_by_sym[_s] = []
                _corr_by_sym[_s].append(float(_cr["return_24h_pct"]))
            for _s, _rets in _corr_by_sym.items():
                _cn   = len(_rets)
                _cavg = round(sum(_rets) / _cn, 1)
                _csr  = round(sum(1 for r in _rets if r > -20.0) / _cn, 2)
                if _cn >= _CORR_MIN_N and _cavg > _CORR_SURVIVOR_AVG and _csr >= _CORR_SURVIVAL_RATE:
                    _ctier_val = "SURVIVOR"
                elif _cn >= _CORR_MIN_N and _cavg < _CORR_CASUALTY_AVG:
                    _ctier_val = "CASUALTY"
                elif _cn >= _CORR_MIN_N:
                    _ctier_val = "TRACKER"
                else:
                    _ctier_val = "UNRATED"
                _corr_perf[_s] = {
                    "n": _cn, "avg": _cavg,
                    "survival_rate": _csr, "tier": _ctier_val,
                }
        except Exception:
            pass

        # ── Patch 287: global exit-shape quality ──────────────────────────────
        _g_shapes = dict(_EMPTY_SHAPE)
        for _r in _shape_rows:
            _g_shapes[_exit_shape_g(_r["exit_reason"], _r["pnl_pct"])] += 1
        _global_sq  = _shape_quality_g(_g_shapes)
        _sl_exits   = _g_shapes["SL_STANDARD"] + _g_shapes["SL_SLIPPAGE"]
        _non_manual = sum(_g_shapes.values()) - _g_shapes.get("MANUAL", 0)
        _slip_pct   = round(_g_shapes["SL_SLIPPAGE"] / _sl_exits   * 100, 1) if _sl_exits   else None
        _rug_pct    = round(_g_shapes["RUG"]          / _non_manual * 100, 1) if _non_manual else None

        # ── Fallback: scan cache empty → lifecycle-only candidates ─────────────
        _MCAP_FLOOR = 1_500_000  # Patch 288: $1.5M minimum market cap
        if not signals:
            data_source = "lifecycle_direct"
            for lc in lc_by_mint.values():
                ew  = lc.get("entry_window", "")
                fq  = lc.get("fuel_quality", "")
                mp  = lc.get("move_phase",   "")
                flc = int(lc.get("first_leg_confirmed", 0) or 0)
                if (ew == "OPEN" and fq in ("STRONG", "MODERATE")
                        and mp not in ("EXTENDED", "CHURN") and flc == 1):
                    # Patch 288: enforce mcap floor — no record = fail (conservative)
                    _sym  = lc.get("symbol", "")
                    _mcap = _last_mcap.get(_sym)
                    if _mcap is None or _mcap < _MCAP_FLOOR:
                        log.info(
                            "[NBA] %s skipped — mcap floor: last_known=%s < $%.1fM",
                            _sym,
                            f"${_mcap:,.0f}" if _mcap is not None else "N/A",
                            _MCAP_FLOOR / 1_000_000,
                        )
                        continue
                    signals.append({
                        "_lifecycle_only": True,
                        "symbol":           lc.get("symbol", ""),
                        "mint":             lc.get("mint",   ""),
                        "score":            None,
                        "rug_label":        None,
                        "buy_pressure":     None,
                        "vol_acceleration": None,
                        "top_holder_pct":   None,
                        "mint_revoked":     False,
                    })

        # Patch 311: coin-quality veto — structural quality verdict from scan cache.
        # Defined here (before the signal eval loop) so it is in scope when first
        # called inside the loop.  No external lookups; all inputs from scan cache.
        # Rules are deterministic and auditable.
        def _coin_quality_verdict(sig: dict) -> tuple:
            """
            Returns (coin_quality, quality_caution).
            coin_quality: 'CLEAR' | 'QUESTIONABLE' | 'WEAK'

            WEAK    — hard structural flag present; coin structurally compromised
                      regardless of technical signal quality.
            QUESTIONABLE — one meaningful concern; operator should note before acting.
            CLEAR   — no structural flags from available scan-cache data.
            """
            rug   = str(sig.get("rug_label")            or "").upper()
            top1  = float(sig.get("top_holder_pct")     or 0)
            top5  = float(sig.get("top5_holder_pct")    or 0)
            liq   = float(sig.get("liquidity_usd")      or 0)
            vol24 = float(sig.get("volume_24h")         or 0)
            age   = float(sig.get("token_age_days")     or 0)
            hq    = str(sig.get("holder_quality_level") or "").upper()

            # vol/liq ratio — only meaningful when liq is substantial (avoid /tiny-float)
            vol_liq = (vol24 / liq) if liq > 1000 else 0.0

            # ── WEAK: hard structural red flags ─────────────────────────────────
            weak = []
            if rug == "DANGER":
                weak.append("rug=DANGER")
            if top1 >= 80:
                weak.append(f"top_holder {top1:.0f}%")
            if top5 >= 90:
                weak.append(f"top5_holder {top5:.0f}%")
            if 0 < liq < 30_000:
                weak.append(f"liq ${liq:,.0f}")
            if vol_liq > 25:
                weak.append(f"vol/liq {vol_liq:.0f}x")
            if weak:
                return ("WEAK", " + ".join(weak))

            # ── QUESTIONABLE: one meaningful concern ─────────────────────────────
            q = []
            if rug in ("WARN", "UNKNOWN"):
                q.append(f"rug={rug.lower()}")
            if 50 <= top1 < 80:
                q.append(f"top_holder {top1:.0f}%")
            if 70 <= top5 < 90:
                q.append(f"top5_holder {top5:.0f}%")
            if 0 < liq < 75_000:
                q.append(f"liq ${liq:,.0f}")
            if 0 < age < 7:
                q.append(f"age {age:.0f}d")
            if 10 < vol_liq <= 25:
                q.append(f"vol/liq {vol_liq:.0f}x")
            if hq == "RISKY":
                q.append("holder_quality=RISKY")
            if q:
                return ("QUESTIONABLE", " + ".join(q))

            return ("CLEAR", "")

        # Patch 313: eligible-universe computation ────────────────────────────
        # Determines the clean working set of symbols the NBA draws candidates
        # from.  Only scan-cache signals (lc_only=False) are subject to universe
        # membership.  Lifecycle-only fallback signals are already pre-screened
        # by Patch 288 and bypass this gate unchanged.
        #
        # Inclusion gates (all six must pass):
        #   1. scan_count_30d >= 3        — persistent scanner presence, not a fluke
        #   2. last_scan within 14d       — recency; ghost tokens excluded
        #   3. mcap_usd >= $1.5M          — confirmed above floor in current cache
        #   4. liquidity_usd >= $50K      — minimum tradeable depth
        #   5. coin_quality != 'WEAK'     — Patch 311 structural veto
        #   6. perf_tier != PROVEN_NEGATIVE — confirmed poor track record excluded
        #   7. correction_tier != CASUALTY  — active correction casualty excluded
        _EU_MIN_SCANS = 3
        _EU_MIN_LIQ   = 50_000
        _eu_sig_by_sym: dict = {
            s.get("symbol"): s
            for s in signals
            if s.get("symbol") and not s.get("_lifecycle_only")
        }
        _eligible_syms: set = set()
        _eu_excluded:   dict = {}
        _all_eu_syms = set(_eu_scan_stats.keys()) | set(_eu_sig_by_sym.keys())
        for _esym in _all_eu_syms:
            _ereasons: list = []
            _estat = _eu_scan_stats.get(_esym, {})
            _ecnt  = _estat.get("cnt",  0)
            _elast = _estat.get("last", "") or ""
            # Gate 1: scan persistence
            if _ecnt < _EU_MIN_SCANS:
                _ereasons.append(f"scan_count_30d={_ecnt} (<{_EU_MIN_SCANS})")
            # Gate 2: recency
            if not _elast or _elast < _eu_cutoff_14d:
                _ereasons.append(f"last_scan={'stale' if _elast else 'never'} (>14d)")
            # Gates 3–5: from current scan cache entry
            _esig = _eu_sig_by_sym.get(_esym) or _last_scan_sig.get(_esym)
            if _esig:
                _emcap = _esig.get("mcap_usd")
                _eliq  = float(_esig.get("liquidity_usd") or 0)
                if _emcap is None or float(_emcap) < _MCAP_FLOOR:
                    _ereasons.append(
                        "mcap={}(<$1.5M)".format(
                            "${:,.0f}".format(float(_emcap)) if _emcap is not None else "unknown"
                        )
                    )
                if _eliq < _EU_MIN_LIQ:
                    _ereasons.append(f"liq=${_eliq:,.0f} (<${_EU_MIN_LIQ:,.0f})")
                _ecq, _ = _coin_quality_verdict(_esig)
                if _ecq == "WEAK":
                    _ereasons.append("coin_quality=WEAK")
            else:
                # No current or recent scanner-confirmed snapshot available.
                _ereasons.append("no_recent_scan_snapshot")
            # Gate 6: performance exclusion
            if _sym_perf.get(_esym, {}).get("tier") == "PROVEN_NEGATIVE":
                _ereasons.append("perf_tier=PROVEN_NEGATIVE")
            # Gate 7: correction casualty exclusion
            if _corr_perf.get(_esym, {}).get("tier") == "CASUALTY":
                _ereasons.append("correction_tier=CASUALTY")
            if _ereasons:
                _eu_excluded[_esym] = _ereasons
            else:
                _eligible_syms.add(_esym)

        # Pre-filter: drop non-eligible scan-cache signals before evaluation.
        # Lifecycle-only signals are left untouched (they bypass the gate above).
        # This is the primary debris reduction step — the evaluation loop now
        # only runs on symbols that earned their place in the working set.
        signals = [
            s for s in signals
            if s.get("_lifecycle_only") or s.get("symbol") in _eligible_syms
        ]

        # ── Evaluate each signal through paper gate stack ─────────────────────
        candidates = []

        confluence_bonus_by_symbol: dict[str, float] = {}
        whale_bonus_by_symbol: dict[str, float] = {}
        confluence_detail_by_symbol: dict[str, str] = {}
        whale_detail_by_symbol: dict[str, str] = {}
        try:
            _conf_rows = conn.execute("""
                SELECT token_symbol,
                       confluence_type,
                       MAX(COALESCE(confluence_score, 0)),
                       MAX(ts_utc)
                FROM confluence_events
                WHERE ts_utc >= datetime('now','-48 hours')
                  AND token_symbol IS NOT NULL
                GROUP BY token_symbol, confluence_type
            """).fetchall()
            for _sym, _ctype, _score, _last_ts in _conf_rows:
                _symbol = str(_sym or "").upper().strip()
                if not _symbol:
                    continue
                _base = (
                    8.0 if _ctype in ("TRIPLE", "DUAL")
                    else 6.0 if _ctype in ("STRUCTURAL_TRIPLE", "STRUCTURAL_DUAL")
                    else 0.0
                )
                _score_bonus = (
                    2.0 if float(_score or 0.0) >= 70.0
                    else 1.0 if float(_score or 0.0) >= 40.0
                    else 0.0
                )
                _bonus = (_base + _score_bonus) * _support_signal_recency_multiplier(
                    _last_ts,
                    fresh_hours=6.0,
                    active_hours=24.0,
                    fade_hours=48.0,
                )
                if _bonus > confluence_bonus_by_symbol.get(_symbol, 0.0):
                    confluence_bonus_by_symbol[_symbol] = _bonus
                    _detail = f"{_ctype or 'confluence'} score={float(_score or 0.0):.0f}"
                    confluence_detail_by_symbol[_symbol] = _detail
        except Exception:
            pass

        try:
            _whale_rows = conn.execute("""
                SELECT token_symbol,
                       MAX(COALESCE(scanner_pass, 0)),
                       MAX(COALESCE(scanner_score, 0)),
                       MAX(ts_utc)
                FROM whale_watch_alerts
                WHERE ts_utc >= datetime('now','-6 hours')
                  AND token_symbol IS NOT NULL
                GROUP BY token_symbol
            """).fetchall()
            for _sym, _scanner_pass, _scanner_score, _last_ts in _whale_rows:
                _symbol = str(_sym or "").upper().strip()
                if not _symbol:
                    continue
                _bonus = 5.0 if int(_scanner_pass or 0) == 1 else 3.0
                if float(_scanner_score or 0) >= 70.0:
                    _bonus += 1.0
                elif float(_scanner_score or 0) >= 50.0:
                    _bonus += 0.5
                _bonus *= _support_signal_recency_multiplier(
                    _last_ts,
                    fresh_hours=1.0,
                    active_hours=3.0,
                    fade_hours=6.0,
                )
                if _bonus > whale_bonus_by_symbol.get(_symbol, 0.0):
                    whale_bonus_by_symbol[_symbol] = _bonus
                    _detail = (
                        "scanner-pass whale"
                        if int(_scanner_pass or 0) == 1
                        else "tracking whale"
                    )
                    whale_detail_by_symbol[_symbol] = (
                        f"{_detail} score={float(_scanner_score or 0.0):.0f}"
                    )
        except Exception:
            pass

        for sig in signals:
            symbol  = str(sig.get("symbol", ""))
            mint    = str(sig.get("mint",   ""))
            score   = sig.get("score")
            rug     = sig.get("rug_label")
            bp      = sig.get("buy_pressure")
            vacc    = sig.get("vol_acceleration")
            holder  = sig.get("top_holder_pct")
            revoked = bool(sig.get("mint_revoked", False))
            lc_only = bool(sig.get("_lifecycle_only", False))

            lc  = lc_by_mint.get(mint) or lc_by_symbol.get(symbol)
            ew  = lc.get("entry_window",        "") if lc else None
            fq  = lc.get("fuel_quality",        "") if lc else None
            mp  = lc.get("move_phase",          "") if lc else None
            flc = int(lc.get("first_leg_confirmed", 0) or 0) if lc else 0

            chain:  list = []
            failed: list = []
            hard_block = False  # TRAP fuel / DANGER rug / recent-loss

            def _chk(name: str, cond: bool, pass_d: str, fail_d: str,
                     hard: bool = False) -> None:
                if cond:
                    chain.append({"name": name, "status": "PASS", "detail": pass_d})
                else:
                    chain.append({"name": name, "status": "FAIL", "detail": fail_d})
                    failed.append(fail_d)
                    if hard:
                        nonlocal hard_block
                        hard_block = True

            if lc_only or score is None:
                chain.append({"name": "Score", "status": "N/A",
                              "detail": "no score data (lifecycle-only)"})
            else:
                sc = float(score)
                if sc < score_min:
                    chain.append({"name": "Score", "status": "FAIL",
                                  "detail": f"score={sc:.1f} < min={score_min:.0f}"})
                    failed.append(f"score {sc:.1f} below min {score_min:.0f}")
                elif sc > score_max:
                    chain.append({"name": "Score", "status": "FAIL",
                                  "detail": f"score={sc:.1f} > max={score_max:.0f}"})
                    failed.append(f"score {sc:.1f} above max {score_max:.0f}")
                else:
                    chain.append({"name": "Score", "status": "PASS",
                                  "detail": f"score={sc:.1f} [{score_min:.0f}–{score_max:.0f}]"})

            if not lc_only:
                # Patch 312: hard mcap floor — explicit $1.5M veto at this action surface.
                # Independent of the scanner's min_mcap threshold (which can drift via
                # the tuner).  Lifecycle-only signals are pre-filtered by Patch 288 and
                # skip this check (lc_only=True guard above).
                _sig_mcap = sig.get("mcap_usd")
                _mcap_ok  = _sig_mcap is not None and float(_sig_mcap) >= _MCAP_FLOOR
                _chk("McapFloor",
                     _mcap_ok,
                     f"mcap=${float(_sig_mcap):,.0f} [≥$1.5M]" if _sig_mcap is not None else "",
                     f"mcap=${float(_sig_mcap):,.0f} < $1.5M floor" if _sig_mcap is not None
                         else "mcap unknown — sub-floor treatment",
                     hard=True)

                is_danger = str(rug).upper() == "DANGER"
                _chk("Rug",
                     str(rug).upper() == "GOOD",
                     "rug=GOOD",
                     f"rug={rug}",
                     hard=is_danger)

                bp_v = float(bp) if bp is not None else None
                _chk("BuyPressure",
                     bp_v is not None and bp_v >= 55,
                     f"bp={bp_v:.0f}%" if bp_v is not None else "bp=?",
                     f"bp={bp_v} < 55%")

                _chk("MintRevoked",
                     not revoked,
                     "mint_revoked=false",
                     "mint_revoked=true",
                     hard=revoked)

                vacc_v = float(vacc) if vacc is not None else None
                _chk("VolAccel",
                     vacc_v is not None and vacc_v >= vacc_min,
                     f"vacc={vacc_v:.1f}× [≥{vacc_min}×]" if vacc_v is not None else "vacc=?",
                     f"vacc={vacc_v} < {vacc_min}×")

                h_v = float(holder) if holder is not None else None
                _chk("TopHolder",
                     h_v is not None and h_v <= holder_max,
                     f"holder={h_v:.1f}% [≤{holder_max}%]" if h_v is not None else "holder=?",
                     f"holder={h_v}% > {holder_max}%")

            if lc is None:
                for nm in ("EntryWindow", "FuelQuality", "MovePhase", "FirstLeg"):
                    chain.append({"name": nm, "status": "MISSING",
                                  "detail": "no lifecycle row"})
                failed.append("lifecycle missing")
            else:
                _chk("EntryWindow",
                     ew == "OPEN",
                     "entry_window=OPEN",
                     f"entry_window={ew}")
                is_trap = (fq == "TRAP")
                _chk("FuelQuality",
                     fq in ("STRONG", "MODERATE"),
                     f"fuel_quality={fq}",
                     f"fuel_quality={fq} (need STRONG/MODERATE)",
                     hard=is_trap)
                _chk("MovePhase",
                     mp not in ("EXTENDED", "CHURN"),
                     f"move_phase={mp}",
                     f"move_phase={mp} (EXTENDED/CHURN blocked)")
                _chk("FirstLeg",
                     flc == 1,
                     "first_leg_confirmed=1",
                     "first_leg_confirmed=0")

            repeat_loss = bool(mint and mint in loss_mints)
            _chk("AntiRepeat",
                 not repeat_loss,
                 "no recent loss",
                 f"loss in last 7d (lock until {loss_expiry.get(mint, '?')})",
                 hard=repeat_loss)

            lk       = (ew, fq, mp) if (ew and fq and mp) else None
            setup_st = ledger_status.get(lk, "INSUFFICIENT_DATA") if lk else "INSUFFICIENT_DATA"
            chain.append({"name": "SetupLedger", "status": "INFO",
                          "detail": f"setup={setup_st}"})
            if fg_val is not None:
                chain.append({"name": "F&G", "status": "INFO",
                              "detail": f"F&G={fg_val} ({fg_label}) — paper gate not applied"})

            # Patch 289: symbol historical performance — inject into chain
            _sp      = _sym_perf.get(symbol, {})
            _sp_tier = _sp.get("tier", "UNPROVEN")
            _sp_n    = _sp.get("n",    0)
            _sp_wr   = _sp.get("wr",   0.0)
            _sp_avg  = _sp.get("avg",  0.0)
            if _sp_n > 0:
                _sp_sign = "+" if _sp_avg >= 0 else ""
                _sp_detail = (f"n={_sp_n} wr={_sp_wr:.0f}% "
                              f"avg={_sp_sign}{_sp_avg:.1f}% → {_sp_tier}")
            else:
                _sp_detail = "UNPROVEN (no outcome data)"
            _sp_status = ("PASS" if _sp_tier == "PROVEN_POSITIVE"
                          else "FAIL" if _sp_tier == "PROVEN_NEGATIVE"
                          else "INFO")
            chain.append({"name": "SymbolHistory", "status": _sp_status,
                          "detail": _sp_detail})

            all_pass = (not failed) and (not lc_only)
            lc_pass  = lc_only and (not failed)

            # Patch 311: coin-quality veto — skip for lifecycle-only (no scan data)
            _cq_pair = _coin_quality_verdict(sig) if not lc_only else (None, "")

            _mem_exact = setup_memory.get(lk, {
                    "state": "THIN",
                    "trailing_n": 0,
                    "trailing_wr": None,
                    "trailing_avg": None,
                    "cat_rate": None,
                    "lifetime_n": 0,
                    "lifetime_wr": None,
                    "lifetime_avg": None,
                })
            _mem_source = "EXACT_SETUP"
            _arch = _continuation_archetype(ew, fq, mp)
            _support_overlap_score = float(confluence_bonus_by_symbol.get(symbol, 0.0)) + float(whale_bonus_by_symbol.get(symbol, 0.0))
            _support_overlap_tags = []
            if confluence_bonus_by_symbol.get(symbol, 0.0) > 0:
                _support_overlap_tags.append("confluence_overlap")
            if whale_bonus_by_symbol.get(symbol, 0.0) > 0:
                _support_overlap_tags.append("whale_overlap")
            _reinforcement_bucket = _support_reinforcement_bucket(
                _support_overlap_score,
                _support_overlap_tags,
            )
            _freshness_bucket = "UNKNOWN"
            _mem_candidates = [
                ("PROFILE_FRESH_REINFORCED_ARCHETYPE", fresh_reinforced_archetype_memory.get((_arch, _reinforcement_bucket, _freshness_bucket))),
                ("PROFILE_REINFORCED_ARCHETYPE", reinforced_archetype_memory.get((_arch, _reinforcement_bucket))),
                ("PROFILE_ARCHETYPE", archetype_memory.get((_arch,))),
                ("PROFILE_FUEL_PHASE", profile_memory.get((fq, mp))),
                ("PROFILE_WINDOW_PHASE", window_phase_memory.get((ew, mp))),
                ("PROFILE_PHASE", phase_memory.get((mp,))),
            ]
            _mem_exact, _mem_source = _choose_continuation_memory(_mem_exact, _mem_candidates)
            _support_overlap_detail = []
            if confluence_detail_by_symbol.get(symbol):
                _support_overlap_detail.append(confluence_detail_by_symbol[symbol])
            if whale_detail_by_symbol.get(symbol):
                _support_overlap_detail.append(whale_detail_by_symbol[symbol])

            candidates.append({
                "symbol":  symbol, "mint": mint,
                "scanned_at": sig.get("scanned_at"),
                "score":   round(float(score), 1) if score is not None else None,
                "entry_window": ew, "fuel_quality": fq, "move_phase": mp,
                "continuation_archetype": _arch,
                "first_leg_confirmed": flc,
                "setup_status": setup_st,
                "continuation_memory": _mem_exact,
                "continuation_memory_source": _mem_source,
                "lc_only":    lc_only,
                "all_pass":   all_pass,
                "lc_pass":    lc_pass,
                "hard_block": hard_block,
                "chain":      chain,
                "failed":     failed,
                "repeat_loss": repeat_loss,
                "perf_tier":  _sp_tier,
                "perf_n":     _sp_n,
                "coin_quality":    _cq_pair[0],   # Patch 311
                "quality_caution": _cq_pair[1],   # Patch 311
                "support_overlap_score": _support_overlap_score,
                "support_overlap_tags": _support_overlap_tags,
                "support_overlap_detail": _support_overlap_detail,
                "reinforcement_bucket": _reinforcement_bucket,
            })

        def _continuation_quality_score(c: dict) -> float:
            """Rank continuation quality for the style we actually want.

            The system is not trying to reward raw freshness or first-launch
            behavior. This score explicitly prefers already-real continuation
            structure: reload/revival shape, open window, strong fuel, proven
            resilience, and penalty for stale or degraded survivors.
            """
            score = 0.0

            move_phase = str(c.get("move_phase") or "")
            fuel_quality = str(c.get("fuel_quality") or "")
            entry_window = str(c.get("entry_window") or "")
            setup_status = str(c.get("setup_status") or "")
            perf_tier = str(c.get("perf_tier") or "UNPROVEN")
            corr_tier = str(_corr_perf.get(c["symbol"], {}).get("tier") or "UNRATED")
            continuation_archetype = str(c.get("continuation_archetype") or "")
            mem = c.get("continuation_memory") or {}
            mem_state = str(mem.get("state") or "THIN")
            mem_source = str(c.get("continuation_memory_source") or "EXACT_SETUP")
            mem_weight = {
                "EXACT_SETUP": 1.0,
                "PROFILE_FRESH_REINFORCED_ARCHETYPE": 0.95,
                "PROFILE_REINFORCED_ARCHETYPE": 0.9,
                "PROFILE_ARCHETYPE": 0.85,
                "PROFILE_FUEL_PHASE": 0.75,
                "PROFILE_WINDOW_PHASE": 0.6,
                "PROFILE_PHASE": 0.4,
            }.get(mem_source, 0.4)
            mem_wr = mem.get("trailing_wr")
            mem_avg = mem.get("trailing_avg")
            mem_cat = mem.get("cat_rate")
            mem_conf = str(mem.get("confidence") or "LOW")
            mem_wr_delta = mem.get("wr_delta")
            mem_avg_delta = mem.get("avg_delta")
            mem_weighted_n = float(mem.get("trailing_weighted_n") or mem.get("trailing_n") or 0.0)
            precise_memory = mem_source in ("EXACT_SETUP", "PROFILE_FRESH_REINFORCED_ARCHETYPE", "PROFILE_REINFORCED_ARCHETYPE", "PROFILE_ARCHETYPE", "PROFILE_FUEL_PHASE")
            mem_authority = _continuation_memory_authority(
                mem,
                mem_source,
                str(c.get("reinforcement_bucket") or "PLAIN"),
                str(c.get("freshness_bucket") or "UNKNOWN"),
            )

            phase_bonus = {
                "RELOAD": 24.0,
                "MID": 14.0,
                "EARLY": 12.0,
                "IGNITION": 6.0,
                "EXTENDED": -10.0,
                "CHURN": -12.0,
            }
            archetype_bonus = {
                "DORMANT_SECOND_LEG": 12.0,
                "REVIVAL_CONTINUATION": 10.0,
                "RELOAD_CONTINUATION": 8.0,
                "DEEP_PULLBACK": 5.0,
                "STRUCTURAL_PASS": 4.0,
                "PHASE_CONTINUATION": 0.0,
            }
            fuel_bonus = {
                "STRONG": 16.0,
                "MODERATE": 8.0,
                "WEAK": -10.0,
                "TRAP": -40.0,
            }
            window_bonus = {
                "OPEN": 12.0,
                "CLOSING": 2.0,
                "CLOSED": -14.0,
            }
            setup_bonus = {
                "WORKING": 16.0,
                "INSUFFICIENT_DATA": 2.0,
                "DEGRADING": -8.0,
                "FAILING": -18.0,
            }
            perf_bonus = {
                "PROVEN_POSITIVE": 18.0,
                "TESTED_NEUTRAL": 8.0,
                "UNPROVEN": 0.0,
                "PROVEN_NEGATIVE": -28.0,
            }
            corr_bonus = {
                "SURVIVOR": 12.0,
                "TRACKER": 4.0,
                "UNRATED": 0.0,
                "CASUALTY": -20.0,
            }

            score += phase_bonus.get(move_phase, 0.0)
            score += archetype_bonus.get(continuation_archetype, 0.0)
            score += fuel_bonus.get(fuel_quality, 0.0)
            score += window_bonus.get(entry_window, 0.0)
            score += setup_bonus.get(setup_status, 0.0)
            score += perf_bonus.get(perf_tier, 0.0)
            score += corr_bonus.get(corr_tier, 0.0)
            score += {
                "COMPOUNDING": 18.0,
                "MIXED": 6.0,
                "THIN": 0.0,
                "FRAGILE": -18.0,
            }.get(mem_state, 0.0) * mem_weight
            score += {
                "FORCEFUL": 10.0,
                "BACKED": 4.0,
                "TENTATIVE": 0.0,
                "ADVERSE": -10.0,
            }.get(mem_authority, 0.0)
            score += {
                "HIGH": 4.0,
                "MEDIUM": 2.0,
                "LOW": 0.0,
            }.get(mem_conf, 0.0) * mem_weight

            if int(c.get("first_leg_confirmed") or 0) == 1:
                score += 14.0

            if c.get("all_pass"):
                score += 8.0
            if c.get("lc_pass"):
                score += 4.0
            if c.get("lc_only"):
                score -= 4.0
            if c.get("repeat_loss"):
                score -= 30.0
            score += float(c.get("support_overlap_score") or 0.0)

            raw_score = c.get("score")
            if raw_score is not None:
                score += min(float(raw_score), 100.0) * 0.12
            if mem_wr is not None:
                score += max(min((float(mem_wr) - 50.0) * 0.25, 8.0), -8.0) * mem_weight
            if mem_avg is not None:
                score += max(min(float(mem_avg) * 0.20, 6.0), -6.0) * mem_weight
            if mem_wr_delta is not None:
                _wr_delta_weight = 0.24 if precise_memory else 0.16
                score += max(min(float(mem_wr_delta) * _wr_delta_weight, 5.0), -5.0) * mem_weight
            if mem_avg_delta is not None:
                _avg_delta_weight = 0.22 if precise_memory else 0.14
                score += max(min(float(mem_avg_delta) * _avg_delta_weight, 3.5), -3.5) * mem_weight
            if mem_cat is not None and float(mem_cat) >= 30.0:
                score -= min((float(mem_cat) - 30.0) * 0.20, 8.0) * mem_weight

            score += (
                5.0 if mem_weighted_n >= 8.0
                else 3.0 if mem_weighted_n >= 5.0
                else 1.5 if mem_weighted_n >= 3.0
                else 0.0
            ) * mem_weight

            if mem_state == "COMPOUNDING" and mem_weighted_n < 3.0:
                score -= 5.0
            elif mem_state == "COMPOUNDING" and mem_weighted_n < 5.0:
                score -= 2.0

            if mem_conf == "LOW" and mem_state in ("COMPOUNDING", "MIXED") and mem_weighted_n < 4.0:
                score -= 3.0

            if precise_memory and mem_conf == "HIGH" and mem_wr_delta is not None and float(mem_wr_delta) > 0:
                score += min(float(mem_wr_delta) * 0.10, 2.0)
            elif (not precise_memory) and mem_wr_delta is not None and float(mem_wr_delta) > 0:
                score += min(float(mem_wr_delta) * 0.04, 0.8)

            return round(score, 1)

        for _c in candidates:
            _c["continuation_score"] = _continuation_quality_score(_c)

        # ── Rank candidates ───────────────────────────────────────────────────
        full_pass = sorted(
            [c for c in candidates if c["all_pass"]],
            key=lambda x: (
                x.get("continuation_score", 0.0),
                x["score"] or 0,
                _LEDGER_RANK.get(x["setup_status"], 2),
            ), reverse=True
        )
        # Lifecycle-only candidates still lean on performance history, but their
        # continuation_score now already carries setup-memory quality. That lets
        # compounding continuation shapes rise without giving stale lc-only names
        # a free pass.
        lc_pass_list = sorted(
            [c for c in candidates if c["lc_pass"]],
            key=lambda x: (
                x.get("continuation_score", 0.0),
                _PERF_RANK.get(x.get("perf_tier", "UNPROVEN"), 0),
                _FUEL_RANK.get(x["fuel_quality"] or "", 0),
                _PHASE_RANK.get(x["move_phase"] or "", 0),
            ), reverse=True
        )
        best      = full_pass[0] if full_pass else (lc_pass_list[0] if lc_pass_list else None)
        runner_up = (full_pass[1] if len(full_pass) > 1
                     else lc_pass_list[1] if (not full_pass and len(lc_pass_list) > 1)
                     else lc_pass_list[0] if (full_pass and lc_pass_list)
                     else None)

        # ── Candidate comparison (why best wins over runner_up) ───────────────
        def _compare(b: dict, r: dict) -> list:
            reasons = []

            bcs = float(b.get("continuation_score") or 0.0)
            rcs = float(r.get("continuation_score") or 0.0)
            if (bcs - rcs) >= 5.0:
                reasons.append(
                    f"continuation quality {bcs:.1f} vs {rcs:.1f}"
                )

            bmem = (b.get("continuation_memory") or {}).get("state")
            rmem = (r.get("continuation_memory") or {}).get("state")
            if bmem and rmem and bmem != rmem:
                reasons.append(
                    f"setup memory {bmem.lower()} vs {rmem.lower()}"
                )
            bauth = str(b.get("continuation_memory_authority") or "")
            rauth = str(r.get("continuation_memory_authority") or "")
            if bauth and rauth and bauth != rauth:
                reasons.append(
                    f"memory authority {bauth.lower().replace('_', ' ')} vs {rauth.lower().replace('_', ' ')}"
                )
            barch = str(b.get("continuation_archetype") or "")
            rarch = str(r.get("continuation_archetype") or "")
            if barch and rarch and barch != rarch:
                reasons.append(
                    f"continuation archetype {barch.lower()} vs {rarch.lower()}"
                )
            bsup = float(b.get("support_overlap_score") or 0.0)
            rsup = float(r.get("support_overlap_score") or 0.0)
            if (bsup - rsup) >= 2.0:
                reasons.append(
                    f"cross-lane reinforcement {bsup:.1f} vs {rsup:.1f}"
                )

            # Patch 289: performance tier checked first — it dominates ranking
            bpt = _PERF_RANK.get(b.get("perf_tier", "UNPROVEN"), 0)
            rpt = _PERF_RANK.get(r.get("perf_tier", "UNPROVEN"), 0)
            if bpt > rpt:
                _bsp = _sym_perf.get(b["symbol"], {})
                _rsp = _sym_perf.get(r["symbol"], {})
                _bs_str = (f"{b.get('perf_tier','UNPROVEN')} "
                           f"(n={_bsp.get('n',0)}, wr={_bsp.get('wr',0):.0f}%, "
                           f"avg={_bsp.get('avg',0):+.1f}%)")
                _rs_str = (f"{r.get('perf_tier','UNPROVEN')} "
                           f"(n={_rsp.get('n',0)}, avg={_rsp.get('avg',0):+.1f}%)")
                reasons.append(
                    f"{b['symbol']} {_bs_str} vs {r['symbol']} {_rs_str}")

            bf = _FUEL_RANK.get(b["fuel_quality"] or "", 0)
            rf = _FUEL_RANK.get(r["fuel_quality"] or "", 0)
            if bf > rf:
                reasons.append(
                    f"{b['symbol']} {b['fuel_quality']} fuel vs "
                    f"{r['symbol']} {r['fuel_quality'] or 'unknown'}")

            if b["first_leg_confirmed"] == 1 and r["first_leg_confirmed"] == 0:
                reasons.append(
                    f"{b['symbol']} first_leg_confirmed=1 vs "
                    f"{r['symbol']} unconfirmed")

            bs = _SCORE_S(b["score"]); rs = _SCORE_S(r["score"])
            if bs is not None and rs is not None and (bs - rs) >= 2.0:
                reasons.append(f"score {bs:.1f} vs {rs:.1f}")

            bl = _LEDGER_RANK.get(b["setup_status"], 2)
            rl = _LEDGER_RANK.get(r["setup_status"], 2)
            if bl > rl:
                reasons.append(
                    f"setup ledger {b['setup_status']} vs {r['setup_status']}")

            bm = _PHASE_RANK.get(b["move_phase"] or "", 0)
            rm = _PHASE_RANK.get(r["move_phase"] or "", 0)
            if bm > rm:
                reasons.append(
                    f"move_phase {b['move_phase']} vs {r['move_phase']}")

            bfresh = str(b.get("freshness_bucket") or "")
            rfresh = str(r.get("freshness_bucket") or "")
            if bfresh == "RECENT_REACTIVATION" and rfresh != "RECENT_REACTIVATION":
                reasons.append(
                    f"{b['symbol']} recent reactivation freshness vs {r['symbol']} {rfresh.lower().replace('_', ' ') or 'generic freshness'}"
                )

            if not b["lc_only"] and r["lc_only"]:
                reasons.append(
                    f"{r['symbol']} has no scored signal (lifecycle-only)")

            if r["repeat_loss"] and not b["repeat_loss"]:
                reasons.append(
                    f"{r['symbol']} blocked by recent loss memory")

            if not reasons:
                # Truly tied on all comparable dimensions
                if b["score"] is None:
                    reasons.append(
                        f"{b['symbol']} ranked first (tied on fuel/phase/history — "
                        f"no score data to differentiate)")
                else:
                    reasons.append(
                        f"{b['symbol']} ranked first by score "
                        f"({b['score']:.1f} vs {r['score']:.1f if r['score'] is not None else '?'})")
            return reasons

        def _SCORE_S(v):
            return float(v) if v is not None else None

        why_wins = _compare(best, runner_up) if (best and runner_up) else []

        # ── Runner-up loss reason ─────────────────────────────────────────────
        def _runner_loss(r: dict) -> list:
            reasons = []
            if r["lc_only"] and best and not best["lc_only"]:
                reasons.append("no scored signal — lifecycle structural only")
            if r["failed"]:
                reasons.extend(r["failed"][:3])
            # Patch 289: surface PROVEN_NEGATIVE explicitly so operator knows why
            if r.get("perf_tier") == "PROVEN_NEGATIVE":
                _rsp = _sym_perf.get(r["symbol"], {})
                reasons.append(
                    f"PROVEN_NEGATIVE history "
                    f"(n={_rsp.get('n',0)}, avg={_rsp.get('avg',0):+.1f}%, "
                    f"wr={_rsp.get('wr',0):.0f}%)")
            if not reasons:
                reasons.append("ranked lower on performance/fuel/score/ledger")
            return reasons

        runner_up_loss = _runner_loss(runner_up) if runner_up else []

        # ── Recommendation ────────────────────────────────────────────────────
        hard_block_all = (
            signals
            and not full_pass
            and not lc_pass_list
            and all(c["hard_block"] for c in candidates)
        )

        if not signals:
            recommendation = "NO_DATA"
            confidence     = None
        elif hard_block_all:
            recommendation = "DO_NOT_TOUCH"
            confidence     = None
        elif best is None:
            recommendation = "HOLD"
            confidence     = None
        elif best["lc_only"]:
            recommendation = "WATCH"
            confidence     = "LOW"
        else:
            recommendation = "EXECUTE_PAPER"
            sc            = best["score"] or 0
            clearly_above = sc >= (score_min + 5)
            st            = best["setup_status"]
            flc1          = best["first_leg_confirmed"] == 1
            fq_strong     = best["fuel_quality"] == "STRONG"
            if clearly_above and flc1 and fq_strong and st == "WORKING":
                confidence = "HIGH"
            elif st == "DEGRADING" or not clearly_above:
                confidence = "LOW"
            else:
                confidence = "MEDIUM"

        # ── Patch 287: shape-aware confidence adjustment ───────────────────────
        # Rules (applied only when confidence is already set):
        #   RUG_PRONE      → drop 2 tiers (HIGH→LOW, MEDIUM→LOW, LOW→LOW)
        #   SLIPPAGE_HEAVY → drop 1 tier  (HIGH→MEDIUM, MEDIUM→LOW, LOW→LOW)
        #   CLEAN_EXITS    → raise 1 tier (LOW→MEDIUM, MEDIUM→HIGH, HIGH→HIGH)
        #   INSUFFICIENT_DATA → no change
        shape_caution: list = []
        if confidence is not None:
            _clist = ["LOW", "MEDIUM", "HIGH"]
            _cmap  = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}
            _ci    = _cmap.get(confidence, 1)
            if _global_sq == "RUG_PRONE":
                _ci = max(0, _ci - 2)
                if _rug_pct is not None:
                    shape_caution.append(
                        f"Global rug profile elevated "
                        f"({_rug_pct:.1f}% of non-manual exits)")
            elif _global_sq == "SLIPPAGE_HEAVY":
                _ci = max(0, _ci - 1)
                if _slip_pct is not None:
                    shape_caution.append(
                        f"SL exits show excessive slippage "
                        f"({_slip_pct:.1f}% of stops exceeded loss floor)")
            elif _global_sq == "CLEAN_EXITS":
                _ci = min(2, _ci + 1)
            confidence = _clist[_ci]

        # ── Upgrade / downgrade conditions ────────────────────────────────────
        upgrade_conditions:   list = []
        downgrade_conditions: list = []

        if recommendation == "NO_DATA":
            upgrade_conditions.append({
                "trigger": "→ WATCH",
                "when": "scanner cycle completes (next ~5 min)"})

        elif recommendation == "HOLD":
            upgrade_conditions.append({
                "trigger": "→ WATCH",
                "when": "scan cache repopulates with lifecycle-confirmed tokens"})
            upgrade_conditions.append({
                "trigger": "→ EXECUTE_PAPER",
                "when": (f"a token passes all gates: "
                         f"score [{score_min:.0f}–{score_max:.0f}], rug=GOOD, "
                         f"bp≥55, lifecycle OPEN + STRONG/MODERATE + flc=1")})

        elif recommendation == "DO_NOT_TOUCH":
            hard_reasons = sorted(
                set(f for c in candidates for f in c["failed"] if c["hard_block"]),
                key=lambda x: len(x))
            upgrade_conditions.append({
                "trigger": "→ HOLD",
                "when": ("hard blockers clear: "
                         + "; ".join(hard_reasons[:3]) if hard_reasons
                         else "hard blockers (TRAP fuel / DANGER rug / recent loss) clear")})

        elif recommendation == "WATCH":
            sym = best["symbol"]
            upgrade_conditions.append({
                "trigger": "→ EXECUTE_PAPER",
                "when": (f"scanner returns scored signal for {sym}: "
                         f"score ≥ {score_min:.0f}, rug=GOOD, bp≥55, "
                         f"vacc≥{vacc_min:.1f}×, holder≤{holder_max:.1f}%")})
            downgrade_conditions.append({
                "trigger": "→ HOLD",
                "when": f"entry_window changes from OPEN to CLOSING or CLOSED"})
            if best.get("fuel_quality") in ("STRONG", "MODERATE"):
                downgrade_conditions.append({
                    "trigger": "→ DO_NOT_TOUCH",
                    "when": "fuel_quality degrades to TRAP"})
            if best.get("repeat_loss") is False and best.get("mint"):
                downgrade_conditions.append({
                    "trigger": "→ DO_NOT_TOUCH",
                    "when": (f"a trade on {sym} closes at a loss "
                             f"(7-day anti-repeat lock activates)")})

        elif recommendation == "EXECUTE_PAPER":
            sym = best["symbol"]
            downgrade_conditions.append({
                "trigger": "→ WATCH",
                "when": "entry_window slips to CLOSING"})
            downgrade_conditions.append({
                "trigger": "→ WATCH",
                "when": (f"score drops below {score_min:.0f} "
                         f"or exceeds {score_max:.0f}")})
            downgrade_conditions.append({
                "trigger": "→ DO_NOT_TOUCH",
                "when": (f"trade on {sym} closes at a loss "
                         f"(7-day anti-repeat lock activates)")})
            if confidence != "HIGH":
                upgrade_conditions.append({
                    "trigger": "→ HIGH confidence",
                    "when": (f"setup ledger accumulates ≥5 WORKING trades "
                             f"for {best.get('entry_window','?')} / "
                             f"{best.get('fuel_quality','?')} / "
                             f"{best.get('move_phase','?')}")})
            if confidence == "LOW" and best.get("setup_status") == "DEGRADING":
                downgrade_conditions.append({
                    "trigger": "→ HOLD (setup FAILING)",
                    "when": "setup ledger trailing win-rate falls below 35%"})

        # ── Blocker summary ───────────────────────────────────────────────────
        signal_blockers: dict = {}
        total_blocked_sig = 0
        for c in candidates:
            if not c["all_pass"] and not c["lc_pass"]:
                total_blocked_sig += 1
                for f in c["failed"]:
                    fl = f.lower()
                    if "lifecycle missing" in fl:
                        k = "no_lifecycle_row"
                    elif "first_leg" in fl:
                        k = "first_leg_not_confirmed"
                    elif "entry_window" in fl:
                        k = "entry_window_not_open"
                    elif "score" in fl and "below" in fl:
                        k = "score_below_threshold"
                    elif "score" in fl and "above" in fl:
                        k = "score_above_max"
                    elif "rug=" in fl:
                        k = "rug_label_not_good"
                    elif "bp=" in fl or "buy_pressure" in fl:
                        k = "buy_pressure_low"
                    elif "vacc" in fl:
                        k = "vol_accel_low"
                    elif "holder" in fl:
                        k = "top_holder_high"
                    elif "trap" in fl or "weak" in fl:
                        k = "weak_or_trap_fuel"
                    elif "extended" in fl or "churn" in fl:
                        k = "move_phase_extended_churn"
                    elif "loss" in fl:
                        k = "recent_loss_memory"
                    else:
                        k = "other"
                    signal_blockers[k] = signal_blockers.get(k, 0) + 1

        # Merge pool_blockers (lifecycle pool) with signal_blockers
        combined_blockers = dict(pool_blockers)
        for k, v in signal_blockers.items():
            combined_blockers[k] = combined_blockers.get(k, 0) + v

        dominant_cat  = (max(combined_blockers, key=lambda x: combined_blockers[x])
                         if combined_blockers else None)
        dominant_count = combined_blockers.get(dominant_cat, 0) if dominant_cat else 0

        blocker_summary = {
            "total_lifecycle_pool": len(all_lc),
            "total_blocked":        total_blocked_sig + sum(pool_blockers.values()),
            "dominant_reason":      dominant_cat,
            "dominant_count":       dominant_count,
            "categories":           combined_blockers,
        }

        # Patch 292: quiet-market intelligence block (only when scanner is silent)
        quiet_market_intel: dict | None = None
        if data_source == "lifecycle_direct":
            # ── Market diagnosis ──────────────────────────────────────────────
            # memecoin_context_stats is an entry_context distribution of SCORED tokens.
            # Empty (no non-_ts keys) means zero tokens reached scoring stage —
            # which filter stopped them is unknown. Do NOT name chg1h specifically.
            _ctx: dict = {}
            try:
                _ctx_row = conn.execute(
                    "SELECT value FROM kv_store WHERE key = 'memecoin_context_stats'"
                ).fetchone()
                if _ctx_row and _ctx_row["value"]:
                    _ctx = _json.loads(_ctx_row["value"])
            except Exception:
                pass
            _ctx_token_count = sum(v for k, v in _ctx.items() if k != "_ts" and isinstance(v, int))
            _scan_quiet_min: float | None = None
            try:
                _ts_row = conn.execute(
                    "SELECT value FROM kv_store WHERE key = 'memecoin_scan_last_ts'"
                ).fetchone()
                if _ts_row and _ts_row["value"]:
                    _scan_quiet_min = round((time.time() - float(_json.loads(_ts_row["value"]))) / 60, 1)
            except Exception:
                pass
            if _ctx_token_count == 0:
                _q_blocker = "no tokens reached scored signal stage — all pre-score filters dry"
                _q_evidence = "context_stats empty — zero tokens passed pre-score filter chain"
            else:
                _q_blocker = "scanner ran but no signals met final cache criteria"
                _q_evidence = f"context_stats has {_ctx_token_count} scored token(s) but none cached"
            _fg_gap_exec  = max(0, 26 - (fg_val or 0))
            _fg_gap_pilot = max(0, 36 - (fg_val or 0))
            _diag_status  = "CORRECTION_UNDERWAY" if (fg_val or 0) <= 25 else "SCANNER_QUIET"

            # ── liq_trend for monitor ranking ─────────────────────────────────
            _liq_trends: dict = {}
            try:
                for _lr in conn.execute(
                    "SELECT symbol, liq_trend FROM symbol_lifecycle"
                ).fetchall():
                    _liq_trends[_lr[0]] = (_lr[1] or "")
            except Exception:
                pass

            # ── _corr_perf: pre-computed at _sym_perf block (Patch 296 refactor) ─
            # _corr_perf, _CORR_WINDOW_DAYS and tier constants are now computed
            # at 8-space level (after _sym_perf) so they're available in the NBA
            # scored-signal path as well.  No recomputation needed here.

            # ── Monitor ranking ───────────────────────────────────────────────
            _PERF_ORDER = {
                "PROVEN_POSITIVE": 0, "TESTED_NEUTRAL": 1,
                "UNPROVEN": 2, "PROVEN_NEGATIVE": 3,
            }
            _CORR_ORDER = {
                "SURVIVOR": 0, "TRACKER": 1, "UNRATED": 2, "CASUALTY": 3,
            }

            # ── Patch 298: pre-compute transition slopes per monitor symbol ──────
            # Query last 4 scored rows per symbol; compute vol_acceleration and
            # score slopes (newest-first, so slope = (row[0]-row[-1])/(n-1)).
            _monitor_syms_298 = [_lc.get("symbol", "") for _lc in lc_pass_list if _lc.get("symbol")]
            _raw_trans: dict = {}  # symbol -> {vacc_slope, score_slope, vacc_gap, cur_vacc, cur_score}
            if _monitor_syms_298:
                _ph_298 = ",".join("?" * len(_monitor_syms_298))
                try:
                    _trans_rows_298 = conn.execute(f"""
                        SELECT symbol, vol_acceleration, score
                        FROM (
                            SELECT symbol, vol_acceleration, score,
                                   ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY scanned_at DESC) AS rn
                            FROM memecoin_signal_outcomes
                            WHERE symbol IN ({_ph_298})
                              AND source = 'SCANNER'
                              AND vol_acceleration IS NOT NULL
                              AND score IS NOT NULL
                        ) WHERE rn <= 4
                    """, _monitor_syms_298).fetchall()
                    _by_sym_298: dict = {}
                    for _tr in _trans_rows_298:
                        _s = _tr["symbol"]
                        if _s not in _by_sym_298:
                            _by_sym_298[_s] = []
                        _by_sym_298[_s].append(_tr)
                    for _sym_298, _rows_298 in _by_sym_298.items():
                        if len(_rows_298) < 2:
                            continue
                        _vs = [float(_r["vol_acceleration"] or 0) for _r in _rows_298]
                        _ss = [float(_r["score"]            or 0) for _r in _rows_298]
                        _n_pts = len(_rows_298)
                        _raw_trans[_sym_298] = {
                            "vacc_slope":  (_vs[0] - _vs[-1]) / max(_n_pts - 1, 1),
                            "score_slope": (_ss[0] - _ss[-1]) / max(_n_pts - 1, 1),
                            "vacc_gap":    vacc_min - _vs[0],
                            "cur_vacc":    _vs[0],
                            "cur_score":   _ss[0],
                        }
                except Exception:
                    pass

            _monitors: list = []
            for _lc in lc_pass_list:
                _sym  = _lc.get("symbol", "")
                _sp   = _sym_perf.get(_sym, {})
                _tier = _sp.get("tier", "UNPROVEN")
                _liq  = _liq_trends.get(_sym, "")
                _avg  = _sp.get("avg")
                _n    = _sp.get("n", 0)
                _estat_qm = _eu_scan_stats.get(_sym, {})
                _last_sig = _last_scan_sig.get(_sym, {})
                _scan_cnt = int(_estat_qm.get("cnt", 0))
                _scan_last = _estat_qm.get("last", "") or ""
                _last_liq = float(_last_sig.get("liquidity_usd") or 0)
                _last_mcap_qm = _last_mcap.get(_sym)
                _cq_qm, _ = _coin_quality_verdict(_last_sig) if _last_sig else (None, "")
                _focus_fail = []
                if _scan_cnt < _EU_MIN_SCANS:
                    _focus_fail.append("scan persistence")
                if not _scan_last or _scan_last < _eu_cutoff_14d:
                    _focus_fail.append("stale scan")
                if _last_mcap_qm is None or _last_mcap_qm < _MCAP_FLOOR:
                    _focus_fail.append("mcap floor")
                if _last_liq < _EU_MIN_LIQ:
                    _focus_fail.append("liq floor")
                _cp      = _corr_perf.get(_sym, {})
                _ctier   = _cp.get("tier", "UNRATED")
                _cavg    = _cp.get("avg")
                _cn_corr = _cp.get("n", 0)
                if _cq_qm == "WEAK":
                    _focus_fail.append("coin quality")
                if _ctier == "CASUALTY":
                    _focus_fail.append("correction casualty")
                if _tier == "PROVEN_NEGATIVE":
                    _focus_fail.append("proven negative")

                if _focus_fail:
                    _rdns = "LOW_PRIORITY"
                elif _liq == "shrinking":
                    _rdns = "WATCH_WITH_CAUTION"
                elif _tier in ("PROVEN_POSITIVE", "TESTED_NEUTRAL") and _liq == "growing":
                    _rdns = "BEST_MONITOR"
                else:
                    _rdns = "WATCH"
                _wake_up = (
                    _ctier == "SURVIVOR"
                    and _liq == "growing"
                    and _lc.get("move_phase") in ("RELOAD", "CHURN")
                    and _lc.get("entry_window") in ("OPEN", "CLOSING")
                )
                # Patch 298: transition status from slope data
                _rt = _raw_trans.get(_sym, {})
                _vacc_slope_298  = _rt.get("vacc_slope", 0.0)
                _score_slope_298 = _rt.get("score_slope", 0.0)
                _vacc_gap_298    = _rt.get("vacc_gap")
                _cur_vacc_298    = _rt.get("cur_vacc", 0.0)
                _cur_score_298   = _rt.get("cur_score", 0.0)
                if _vacc_gap_298 is not None:
                    if _vacc_slope_298 > 0.3 and _score_slope_298 > 2.0 and _vacc_gap_298 < 2.0:
                        _tstatus = "APPROACHING"
                    elif _vacc_slope_298 < -0.3 or _score_slope_298 < -2.0:
                        _tstatus = "FADING"
                    elif _wake_up and _vacc_gap_298 < 3.0:
                        _tstatus = "STALLING"
                    else:
                        _tstatus = "DORMANT"
                else:
                    _tstatus = "DORMANT"
                _gap_to_scanner_298 = {
                    "vacc_gap":    round(_vacc_gap_298, 2),
                    "score_gap":   round(score_min - _cur_score_298, 1),
                    "vacc_slope":  round(_vacc_slope_298, 2),
                    "score_slope": round(_score_slope_298, 1),
                } if _vacc_gap_298 is not None else None
                if _tstatus == "APPROACHING":
                    if _vacc_gap_298 <= 0:
                        # Already above vacc threshold — something else is blocking
                        _tsummary = (f"APPROACHING \u2014 vacc {_cur_vacc_298:.1f}\u00d7 already above "
                                     f"threshold, other conditions pending "
                                     f"(score_slope={_score_slope_298:+.1f}/scan)")
                    else:
                        # Still below threshold, closing the gap
                        _tsummary = (f"APPROACHING \u2014 {_vacc_gap_298:.1f}\u00d7 below vacc threshold, "
                                     f"closing (vacc_slope={_vacc_slope_298:+.1f}\u00d7/scan, "
                                     f"score_slope={_score_slope_298:+.1f}/scan)")
                elif _tstatus == "FADING":
                    _tsummary = (f"FADING "
                                 f"(vacc_slope={_vacc_slope_298:+.1f}\u00d7/scan, "
                                 f"score_slope={_score_slope_298:+.1f}/scan)")
                elif _tstatus == "STALLING":
                    if _vacc_gap_298 <= 0:
                        _tsummary = (f"STALLING \u2014 vacc {_cur_vacc_298:.1f}\u00d7 above threshold, "
                                     f"momentum plateauing")
                    else:
                        _tsummary = (f"STALLING \u2014 {_vacc_gap_298:.1f}\u00d7 below vacc threshold, "
                                     f"not gaining (vacc={_cur_vacc_298:.1f}\u00d7)")
                elif _vacc_gap_298 is not None:
                    _tsummary = (f"DORMANT (vacc={_cur_vacc_298:.1f}\u00d7, "
                                 f"{_vacc_gap_298:.1f}\u00d7 gap to scanner)")
                else:
                    _tsummary = "DORMANT \u2014 no scored history"
                _monitors.append({
                    "symbol":            _sym,
                    "readiness":         _rdns,
                    "focus_context":     "EXCLUDED_CONTEXT" if _focus_fail else "FOCUS_UNIVERSE",
                    "perf_tier":         _tier,
                    "avg_return":        round(_avg, 1) if _avg is not None else None,
                    "n_outcomes":        _n,
                    "lifecycle_state":   _lc.get("entry_window", ""),
                    "fuel_quality":      _lc.get("fuel_quality", ""),
                    "move_phase":        _lc.get("move_phase", ""),
                    "liq_trend":         _liq,
                    "correction_tier":   _ctier,
                    "correction_avg":    round(_cavg, 1) if _cavg is not None else None,
                    "correction_n":      _cn_corr,
                    "wake_up_candidate":  _wake_up,
                    "transition_status":  _tstatus,
                    "gap_to_scanner":     _gap_to_scanner_298,
                    "transition_summary": _tsummary,
                    "focus_reason":       ", ".join(_focus_fail) if _focus_fail else "",
                    "_sort_key":          (
                        0 if _tstatus == "APPROACHING" else (1 if _wake_up else 2),
                        _CORR_ORDER.get(_ctier, 2),
                        _PERF_ORDER.get(_tier, 2),
                        0 if _liq == "growing" else 1,
                    ),
                })
            _monitors.sort(key=lambda x: x["_sort_key"])
            for _m in _monitors:
                del _m["_sort_key"]

            # ── What it takes ─────────────────────────────────────────────────
            _conditions = [
                "chg1h 3\u201320%",
                "vol_24h \u2265 $25,000",
                "liq_usd \u2265 $10,000",
                "vol/liq \u2264 15\u00d7",
                "mcap $1.5M\u2013$50M",
                "age 1\u201330 days",
                f"vol_accel \u2265 {vacc_min:.1f}%",
                f"top_holder \u2264 {holder_max:.1f}%",
                "rug_label \u2260 BAD",
                f"score {score_min:.0f}\u2013{score_max:.0f}",
            ]

            # ── Do-nothing statement ──────────────────────────────────────────
            _best_syms        = [m["symbol"] for m in _monitors if m["readiness"] == "BEST_MONITOR"]
            _skip_syms        = [m["symbol"] for m in _monitors if m["readiness"] == "LOW_PRIORITY"]
            _survivor_syms    = [m["symbol"] for m in _monitors if m["correction_tier"] == "SURVIVOR"]
            _casualty_syms    = [m["symbol"] for m in _monitors if m["correction_tier"] == "CASUALTY"]
            _wake_syms        = [m["symbol"] for m in _monitors if m["wake_up_candidate"]]
            _approaching_syms = [m["symbol"] for m in _monitors if m["transition_status"] == "APPROACHING"]
            _do_nothing = (
                f"No action. Scanner idle \u2014 {_q_blocker}. "
                + (f"APPROACHING threshold: {', '.join(_approaching_syms)} \u2014 elevated watch. " if _approaching_syms else "")
                + (f"{', '.join(_wake_syms)} SURVIVOR + growing liq \u2014 prime watch. " if _wake_syms else "")
                + (f"{', '.join(_best_syms)} accumulating (growing liq) \u2014 monitor. " if _best_syms else "")
                + (f"Correction survivors: {', '.join(_survivor_syms)}. " if _survivor_syms and not _wake_syms else "")
                + (f"Correction casualties: {', '.join(_casualty_syms)} \u2014 avoid. " if _casualty_syms else "")
                + (f"Skip {', '.join(_skip_syms)} (excluded from focus universe). " if _skip_syms else "")
                + (f"Re-eval when F&G > 25 (+{_fg_gap_exec} pts needed)." if _fg_gap_exec > 0 else "F&G is favorable.")
            )

            _suppressed_monitors = [m for m in _monitors if m["readiness"] == "LOW_PRIORITY"]
            _visible_monitors    = [m for m in _monitors if m["readiness"] != "LOW_PRIORITY"]

            quiet_market_intel = {
                "market_diagnosis": {
                    "status":           _diag_status,
                    "dominant_blocker": _q_blocker,
                    "evidence":         _q_evidence,
                    "fg_value":         fg_val,
                    "fg_label":         fg_label,
                    "fg_gap_exec":      _fg_gap_exec,
                    "fg_gap_pilot":     _fg_gap_pilot,
                    "scan_quiet_min":   _scan_quiet_min,
                },
                "monitors":      _visible_monitors,
                "suppressed":    {
                    "count":   len(_suppressed_monitors),
                    "symbols": [m["symbol"] for m in _suppressed_monitors[:12]],
                    "sample":  [
                        {"symbol": m["symbol"], "reason": m.get("focus_reason", "")}
                        for m in _suppressed_monitors[:8]
                    ],
                    "reason":  "low priority / excluded from default watchlist",
                },
                "what_it_takes": {
                    "description":        "First executable signal requires ALL of:",
                    "conditions":         _conditions,
                    "currently_blocking": _q_blocker,
                },
                "do_nothing":    _do_nothing,
            }

        # top-5 flat blockers (backward compat)
        blocker_counts: dict = {}
        for c in candidates:
            if not c["all_pass"] and not c["lc_pass"]:
                for f in c["failed"]:
                    blocker_counts[f] = blocker_counts.get(f, 0) + 1
        blockers = sorted(blocker_counts, key=lambda x: blocker_counts[x], reverse=True)[:5]

        _validation_snapshot_cache: dict | None = None

        def _validation_support_snapshot() -> dict:
            nonlocal _validation_snapshot_cache
            if _validation_snapshot_cache is not None:
                return _validation_snapshot_cache

            def _track_signal(payload: dict | None) -> str:
                payload = payload or {}
                verdict = str(payload.get("verdict") or "")
                if verdict == "EARNING_ITS_PLACE":
                    return "PROMOTIVE"
                if verdict == "FALSIFIED":
                    return "ADVERSE"
                bootstrap = (payload.get("validation_tracks") or {}).get("bootstrap_observed") or {}
                if bootstrap.get("gradient_present"):
                    return "EARLY"
                return "ACCUMULATING"

            try:
                _tl = _validate_trust_labels(conn)
                _ts = _validate_triage_states(conn)
                _td = _validate_transition_detector(conn)
                _validation_snapshot_cache = {
                    "trust_labels": {
                        "signal": _track_signal(_tl),
                        "clean_n": int(_tl.get("clean_n") or 0),
                    },
                    "triage_states": {
                        "signal": _track_signal(_ts),
                        "clean_n": int(_ts.get("clean_n") or 0),
                    },
                    "transition_detector": {
                        "signal": (
                            "PROMOTIVE" if str(_td.get("verdict") or "") == "EARNING_ITS_PLACE"
                            else "ADVERSE" if str(_td.get("verdict") or "") == "FALSIFIED"
                            else "EARLY" if str(_td.get("directional_state") or "") == "THIN_POSITIVE_SIGNAL"
                            else "ACCUMULATING"
                        ),
                    },
                }
                _validation_snapshot_cache["proof_stack_authority"] = _proof_stack_authority(_validation_snapshot_cache)
            except Exception:
                _validation_snapshot_cache = {
                    "trust_labels": {"signal": "ACCUMULATING", "clean_n": 0},
                    "triage_states": {"signal": "ACCUMULATING", "clean_n": 0},
                    "transition_detector": {"signal": "ACCUMULATING"},
                }
                _validation_snapshot_cache["proof_stack_authority"] = _proof_stack_authority(_validation_snapshot_cache)
            return _validation_snapshot_cache

        def _compute_trust(
            symbol: str,
            setup_status: str,
            continuation_memory: dict | None = None,
            continuation_memory_source: str | None = None,
            continuation_archetype: str | None = None,
            reinforcement_bucket: str | None = None,
            support_overlap_score: float | int | None = None,
        ) -> dict:
            """Patch 296 — setup-aware trust judgment.
            Combines four explicit inputs: all-time perf tier, correction tier,
            setup-ledger status, avg loss depth.  Decision tree is deterministic
            and fully auditable — no hidden weights.
            """
            _sp = _sym_perf.get(symbol, {})
            _cp = _corr_perf.get(symbol, {})

            perf_tier = _sp.get("tier", "UNPROVEN")
            perf_n    = int(_sp.get("n", 0))
            perf_avg  = _sp.get("avg")
            perf_wr   = _sp.get("wr")

            corr_tier = _cp.get("tier", "UNRATED")
            corr_avg  = _cp.get("avg")
            corr_n    = int(_cp.get("n", 0))
            mem       = continuation_memory or {}
            mem_state = str(mem.get("state") or "THIN")
            mem_n     = float(mem.get("trailing_weighted_n") or mem.get("trailing_n") or 0.0)
            mem_avg   = mem.get("trailing_avg")
            mem_wr    = mem.get("trailing_wr")
            mem_conf  = str(mem.get("confidence") or "LOW")
            mem_wr_delta = mem.get("wr_delta")
            mem_avg_delta = mem.get("avg_delta")
            mem_source = str(continuation_memory_source or "EXACT_SETUP")
            mem_archetype = str(continuation_archetype or "").strip()
            reinforcement_bucket = str(reinforcement_bucket or "PLAIN").strip() or "PLAIN"
            mem_authority = _continuation_memory_authority(
                mem,
                mem_source,
                reinforcement_bucket,
                None,
            )
            mem_precise = mem_source in ("EXACT_SETUP", "PROFILE_FRESH_REINFORCED_ARCHETYPE", "PROFILE_REINFORCED_ARCHETYPE", "PROFILE_ARCHETYPE", "PROFILE_FUEL_PHASE")
            mem_family = mem_archetype.lower().replace("_", " ") if mem_archetype else "continuation family"
            if reinforcement_bucket == "STRONG_REINFORCED":
                mem_family = f"reinforced {mem_family}"
            elif reinforcement_bucket == "REINFORCED":
                mem_family = f"cross-confirmed {mem_family}"

            # Avg loss depth — one query, only used if n_loss >= 3
            avg_loss: float | None = None
            n_loss: int = 0
            try:
                _lr = conn.execute("""
                    SELECT AVG(return_24h_pct) AS avg_loss,
                           COUNT(*)            AS n_loss
                    FROM memecoin_signal_outcomes
                    WHERE symbol = ? AND status = 'COMPLETE' AND return_24h_pct < 0
                """, (symbol,)).fetchone()
                if _lr and _lr["avg_loss"] is not None:
                    avg_loss = round(float(_lr["avg_loss"]), 1)
                    n_loss   = int(_lr["n_loss"])
            except Exception:
                pass

            trust_positive: list = []
            trust_caution:  list = []
            trust_upgrade:  list = []

            # ── Evidence reasons — sourced, explicit ──────────────────────────
            # All-time performance
            _psign = "+" if (perf_avg or 0) >= 0 else ""
            if perf_tier == "PROVEN_POSITIVE":
                trust_positive.append(
                    f"PROVEN_POSITIVE all-time — avg {_psign}{perf_avg}%"
                    f" WR {perf_wr:.0f}% (n={perf_n})"
                )
            elif perf_tier == "TESTED_NEUTRAL":
                trust_positive.append(
                    f"TESTED_NEUTRAL all-time — avg {_psign}{perf_avg}% (n={perf_n})"
                )
            elif perf_tier == "PROVEN_NEGATIVE":
                trust_caution.append(
                    f"PROVEN_NEGATIVE all-time — avg {perf_avg}% (n={perf_n})"
                )
            else:
                trust_caution.append(f"symbol unproven — {perf_n} outcome(s) so far")

            # Correction behavior (14-day window)
            _csign = "+" if (corr_avg or 0) >= 0 else ""
            if corr_tier == "SURVIVOR":
                trust_positive.append(
                    f"correction SURVIVOR — avg {_csign}{corr_avg}% (n={corr_n})"
                )
            elif corr_tier == "CASUALTY":
                trust_caution.append(
                    f"correction CASUALTY — avg {corr_avg}% (n={corr_n})"
                )
            elif corr_tier == "TRACKER":
                trust_caution.append(
                    f"correction TRACKER — avg {corr_avg}% (n={corr_n})"
                )
            else:
                trust_caution.append("no correction-period outcomes (< 2 in 14d window)")

            # Setup ledger
            if setup_status == "WORKING":
                trust_positive.append("setup ledger WORKING — recent WR \u2265 55%")
            elif setup_status == "DEGRADING":
                trust_caution.append("setup ledger DEGRADING — WR declining vs historical")
            elif setup_status == "FAILING":
                trust_caution.append("setup ledger FAILING — recent WR < 35%")
            else:
                trust_caution.append("setup ledger thin — insufficient data")

            if mem_state == "COMPOUNDING":
                trust_positive.append(
                    f"{mem_family} memory COMPOUNDING — recent similar setups"
                    f" WR {mem_wr:.0f}% avg {mem_avg:+.1f}% (n={mem_n:.1f})"
                    if mem_wr is not None and mem_avg is not None and mem_n
                    else f"{mem_family} memory COMPOUNDING — similar setups improving"
                )
            elif mem_state == "FRAGILE":
                trust_caution.append(
                    f"{mem_family} memory FRAGILE — recent similar setups unstable (n={mem_n:.1f})"
                    if mem_n else f"{mem_family} memory FRAGILE — recent similar setups unstable"
                )
            elif mem_state == "MIXED":
                trust_caution.append(f"{mem_family} memory mixed — setup family not cleanly one-sided yet")

            if mem_conf == "HIGH":
                trust_positive.append(f"{mem_family} memory confidence high — broad enough recent sample")
            elif mem_conf == "LOW" and mem_n > 0:
                trust_caution.append(f"{mem_family} memory low-confidence — sample still thin")

            if mem_authority == "FORCEFUL":
                trust_positive.append("continuation memory authority is forceful — memory has earned real decision weight")
            elif mem_authority == "BACKED":
                trust_positive.append("continuation memory authority is backed — enough context exists to lean on it")
            elif mem_authority == "ADVERSE":
                trust_caution.append("continuation memory authority is adverse — recent pattern memory is not earning trust")

            if reinforcement_bucket == "STRONG_REINFORCED":
                trust_positive.append("support lanes aligned — whale/confluence reinforcement present")
            elif reinforcement_bucket == "REINFORCED":
                trust_positive.append("support-lane overlap present — not standalone, but confirming")

            if mem_wr_delta is not None and float(mem_wr_delta) >= 10.0:
                trust_positive.append(f"continuation trend improving — WR delta {float(mem_wr_delta):+.0f}pp vs lifetime")
            elif mem_wr_delta is not None and float(mem_wr_delta) <= -10.0:
                trust_caution.append(f"continuation trend weakening — WR delta {float(mem_wr_delta):+.0f}pp vs lifetime")

            if mem_avg_delta is not None and float(mem_avg_delta) >= 5.0:
                trust_positive.append(f"continuation avg improving — {float(mem_avg_delta):+.1f}pp vs lifetime")
            elif mem_avg_delta is not None and float(mem_avg_delta) <= -5.0:
                trust_caution.append(f"continuation avg weakening — {float(mem_avg_delta):+.1f}pp vs lifetime")

            # Failure profile (avg loss depth, only if meaningful sample)
            if avg_loss is not None and avg_loss < -20.0 and n_loss >= 3:
                trust_caution.append(
                    f"avg loss depth {avg_loss}% when setup fails (n={n_loss})"
                )
            elif avg_loss is not None and avg_loss >= -10.0 and n_loss >= 3:
                trust_positive.append(
                    f"loss profile shallow — avg {avg_loss}% on losses (n={n_loss})"
                )

            validation_support = _validation_support_snapshot()
            trust_validation = validation_support.get("trust_labels") or {}
            triage_validation = validation_support.get("triage_states") or {}
            transition_validation = validation_support.get("transition_detector") or {}
            trust_signal = str(trust_validation.get("signal") or "ACCUMULATING")
            triage_signal = str(triage_validation.get("signal") or "ACCUMULATING")
            transition_signal = str(transition_validation.get("signal") or "ACCUMULATING")
            proof_stack_authority = str(
                validation_support.get("proof_stack_authority")
                or _proof_stack_authority(validation_support)
            )
            reinforcement_authority = _reinforcement_authority(
                support_overlap_score,
                reinforcement_bucket,
                proof_stack_authority,
            )

            if trust_signal == "PROMOTIVE":
                trust_positive.append("trust-label layer promotive — clean label validation has earned promotion")
            elif trust_signal == "EARLY":
                trust_positive.append("trust-label layer early-positive — bootstrap gradient present, not yet clean-promoted")
            elif trust_signal == "ADVERSE":
                trust_caution.append("trust-label layer adverse — label gradient has not held up in resolved outcomes")

            if triage_signal == "PROMOTIVE":
                trust_positive.append("triage layer promotive — investigate/do-not-touch split is validating cleanly")
            elif triage_signal == "EARLY":
                trust_positive.append("triage layer early-positive — bootstrap triage gradient is forming")
            elif triage_signal == "ADVERSE":
                trust_caution.append("triage layer adverse — triage separation is not validating cleanly yet")

            if transition_signal == "PROMOTIVE":
                trust_positive.append("transition detector promotive — approach-vs-baseline gradient is validated")
            elif transition_signal == "EARLY":
                trust_positive.append("transition detector early-positive — thin positive slope signal exists")
            elif transition_signal == "ADVERSE":
                trust_caution.append("transition detector adverse — prior momentum slope is not helping outcomes")

            if proof_stack_authority == "FORCEFUL":
                trust_positive.append("proof stack authority forceful — validation layers are aligned strongly enough to drive action")
            elif proof_stack_authority == "BACKED":
                trust_positive.append("proof stack authority backed — validation stack is directionally supportive")
            elif proof_stack_authority == "ADVERSE":
                trust_caution.append("proof stack authority adverse — validation layers are not aligned enough to trust aggressive promotion")

            # ── Decision tree — deterministic, no hidden weighting ────────────
            # DISTRUST: any active hard negative from top-2 tier-1 inputs
            if perf_tier == "PROVEN_NEGATIVE" or corr_tier == "CASUALTY":
                trust_label = "DISTRUST"
                if corr_tier == "CASUALTY":
                    trust_upgrade.append(
                        "DISTRUST lifts when correction CASUALTY resolves (F&G recovery \u2265 35)"
                    )
                if perf_tier == "PROVEN_NEGATIVE":
                    _gap = max(0, 15 - perf_n)
                    trust_upgrade.append(
                        f"sustained positive outcomes required to requalify tier"
                        f" ({_gap} more at min to re-evaluate)"
                    )

            # HIGH_TRUST: positive convergence across all three major inputs
            elif (perf_tier == "PROVEN_POSITIVE"
                  and corr_tier == "SURVIVOR"
                  and setup_status in ("WORKING", "INSUFFICIENT_DATA")):
                trust_label = "HIGH_TRUST"
                if setup_status == "INSUFFICIENT_DATA":
                    trust_caution.append(
                        "setup ledger unconfirmed — HIGH_TRUST pending ledger data"
                    )
                    trust_upgrade.append("5+ outcomes confirm setup ledger WORKING")

            # LOW_TRUST: all major dimensions thin / unrated
            elif (perf_tier == "UNPROVEN"
                  and corr_tier == "UNRATED"
                  and setup_status == "INSUFFICIENT_DATA"):
                if mem_state == "COMPOUNDING" and mem_n >= 5.0 and mem_precise:
                    trust_label = "CONDITIONAL_TRUST"
                    trust_upgrade.append(
                        "keep compounding continuation memory stable while symbol history matures"
                    )
                else:
                    trust_label = "LOW_TRUST"
                    _gap_perf = max(0, 8 - perf_n)
                    if _gap_perf > 0:
                        trust_upgrade.append(
                            f"{_gap_perf} more outcomes \u2192 TESTED_NEUTRAL threshold"
                        )
                    trust_upgrade.append(
                        "2 correction-period outcomes \u2192 correction tier classification"
                    )

            # CONDITIONAL_TRUST: everything else — partial signals, mixed evidence
            else:
                trust_label = "CONDITIONAL_TRUST"
                if perf_tier == "TESTED_NEUTRAL":
                    _gap = max(0, 15 - perf_n)
                    if _gap > 0:
                        trust_upgrade.append(
                            f"{_gap} more outcomes + WR \u2265 50% + avg \u2265 +5%"
                            f" \u2192 PROVEN_POSITIVE"
                        )
                if corr_tier in ("TRACKER", "UNRATED"):
                    trust_upgrade.append(
                        "correction avg improvement above -5% threshold \u2192 SURVIVOR"
                    )
                if setup_status in ("DEGRADING", "FAILING", "INSUFFICIENT_DATA"):
                    trust_upgrade.append("setup ledger recovery to WORKING")
                if mem_state == "COMPOUNDING":
                    trust_upgrade.append("continuation memory persists as COMPOUNDING")

            if trust_label == "HIGH_TRUST" and (trust_signal == "ADVERSE" or triage_signal == "ADVERSE"):
                trust_label = "CONDITIONAL_TRUST"
                trust_caution.append("global label validation adverse — high trust capped until label engine recovers")
                trust_upgrade.append("clean trust/triage validation must recover to promotive state")
            elif trust_label == "HIGH_TRUST" and proof_stack_authority == "ADVERSE":
                trust_label = "CONDITIONAL_TRUST"
                trust_caution.append("proof stack authority adverse — high trust capped until the validation stack recovers")
                trust_upgrade.append("proof stack authority must recover above adverse")
            elif trust_label == "HIGH_TRUST" and mem_authority == "ADVERSE":
                trust_label = "CONDITIONAL_TRUST"
                trust_caution.append("continuation memory authority adverse — high trust capped until family memory recovers")
                trust_upgrade.append("continuation memory authority must recover above adverse")
            elif (
                trust_label == "CONDITIONAL_TRUST"
                and proof_stack_authority in ("FORCEFUL", "BACKED")
                and trust_signal == "PROMOTIVE"
                and triage_signal == "PROMOTIVE"
                and transition_signal in ("PROMOTIVE", "EARLY")
                and perf_tier == "PROVEN_POSITIVE"
                and corr_tier == "SURVIVOR"
                and mem_authority in ("FORCEFUL", "BACKED")
            ):
                trust_label = "HIGH_TRUST"
                trust_positive.append("proof stack aligned — validation registry supports the continuation profile")

            # ── Trust summary — one sentence ──────────────────────────────────
            _pp = trust_positive[0] if trust_positive else None
            _pc = trust_caution[0]  if trust_caution  else None
            _parts = [trust_label.replace("_", " ")]
            if _pp:
                _parts.append(_pp)
            if _pc and trust_label != "HIGH_TRUST":
                _parts.append(f"caution: {_pc}")
            trust_summary = " \u2014 ".join(_parts)

            return {
                "trust_label":    trust_label,
                "trust_positive": trust_positive,
                "trust_caution":  trust_caution,
                "trust_upgrade":  trust_upgrade,
                "trust_summary":  trust_summary,
            }

        # ── Trust cache — avoids re-running the avg_loss_depth query for the ────
        # same symbol twice when building research_pool (Patch 299).
        _trust_cache: dict = {}

        def _compute_trust_cached(
            symbol: str,
            setup_status: str,
            continuation_memory: dict | None = None,
            continuation_memory_source: str | None = None,
            continuation_archetype: str | None = None,
            reinforcement_bucket: str | None = None,
            support_overlap_score: float | int | None = None,
        ) -> dict:
            _mem = continuation_memory or {}
            _key = (
                symbol,
                setup_status,
                str(_mem.get("state") or "THIN"),
                round(float(_mem.get("trailing_weighted_n") or _mem.get("trailing_n") or 0.0), 1),
                str(_mem.get("confidence") or "LOW"),
                str(continuation_memory_source or "EXACT_SETUP"),
                str(continuation_archetype or ""),
                str(reinforcement_bucket or "PLAIN"),
                round(float(support_overlap_score or 0.0), 2),
                _mem.get("wr_delta"),
                _mem.get("avg_delta"),
            )
            if _key not in _trust_cache:
                _trust_cache[_key] = _compute_trust(
                    symbol,
                    setup_status,
                    continuation_memory,
                    continuation_memory_source,
                    continuation_archetype,
                    reinforcement_bucket,
                    support_overlap_score,
                )
            return _trust_cache[_key]

        def _capital_posture_for_candidate(c: dict, trust: dict, triage: tuple[str, str]) -> dict:
            """
            Early capital-discipline policy for continuation candidates.
            This is not final sizing logic; it determines whether a setup is
            research-only, probe-only, or eventually eligible for fuller sizing.
            """
            validation_support = _validation_support_snapshot()
            trust_signal = str(((validation_support.get("trust_labels") or {}).get("signal")) or "ACCUMULATING")
            triage_signal = str(((validation_support.get("triage_states") or {}).get("signal")) or "ACCUMULATING")
            transition_signal = str(((validation_support.get("transition_detector") or {}).get("signal")) or "ACCUMULATING")
            promotive_layers = sum(1 for s in (trust_signal, triage_signal, transition_signal) if s == "PROMOTIVE")
            early_layers = sum(1 for s in (trust_signal, triage_signal, transition_signal) if s == "EARLY")
            adverse_layers = sum(1 for s in (trust_signal, triage_signal, transition_signal) if s == "ADVERSE")
            proof_stack_authority = str(
                validation_support.get("proof_stack_authority")
                or _proof_stack_authority(validation_support)
            )
            trust_label = str(trust.get("trust_label") or "LOW_TRUST")
            triage_state = str(triage[0] or "BLOCKED")
            setup_status = str(c.get("setup_status") or "INSUFFICIENT_DATA")
            entry_window = str(c.get("entry_window") or "")
            move_phase = str(c.get("move_phase") or "")
            reinforcement_bucket = str(c.get("reinforcement_bucket") or "PLAIN")
            reinforcement_authority = globals()["_reinforcement_authority"](
                c.get("support_overlap_score"),
                reinforcement_bucket,
                proof_stack_authority,
            )
            freshness_bucket = str(c.get("freshness_bucket") or "UNKNOWN")
            mem = c.get("continuation_memory") or {}
            mem_state = str(mem.get("state") or "THIN")
            mem_n = float(mem.get("trailing_weighted_n") or mem.get("trailing_n") or 0.0)
            mem_authority = _continuation_memory_authority(
                mem,
                c.get("continuation_memory_source"),
                reinforcement_bucket,
                freshness_bucket,
            )
            fresh_mem = c.get("freshness_bucket_memory") or freshness_bucket_memory.get(freshness_bucket)
            catalyst_bucket = str(c.get("fresh_catalyst_bucket") or "UNKNOWN")
            catalyst_mem = c.get("fresh_catalyst_bucket_memory") or fresh_catalyst_bucket_memory.get(catalyst_bucket)
            fresh_discovery_authority = _fresh_discovery_authority(
                fresh_mem,
                catalyst_mem,
                freshness_bucket,
                catalyst_bucket,
            )
            promotion_authority = _continuation_promotion_authority(
                proof_stack_authority,
                mem_authority,
                reinforcement_bucket,
                fresh_discovery_authority,
            )
            fresh_mem_state = str(((fresh_mem or {}).get("state")) or "THIN")
            fresh_mem_n = float(((fresh_mem or {}).get("trailing_weighted_n")) or ((fresh_mem or {}).get("trailing_n")) or 0.0)
            fresh_mem_conf = str(((fresh_mem or {}).get("confidence")) or "LOW")

            if triage_state == "DO_NOT_TOUCH" or trust_label == "DISTRUST":
                _capital = {
                    "capital_posture": "NO_DEPLOY",
                    "capital_band": "ZERO",
                    "guidance": "Do not deploy capital. Keep this out of the active book.",
                    "why": "trust/triage path is explicitly adverse",
                }
            elif adverse_layers > 0:
                _capital = {
                    "capital_posture": "RESEARCH_ONLY",
                    "capital_band": "ZERO",
                    "guidance": "Research only. Validation stack is adverse, so capital stays sidelined.",
                    "why": "proof stack has adverse layer(s)",
                }
            elif trust_label == "LOW_TRUST" or triage_state == "BLOCKED":
                _capital = {
                    "capital_posture": "RESEARCH_ONLY",
                    "capital_band": "ZERO",
                    "guidance": "Keep this in research/watch only. Setup is not ready for capital.",
                    "why": "trust or triage is still too thin",
                }
            elif (
                trust_label == "HIGH_TRUST"
                and triage_state == "INVESTIGATE_NOW"
                and setup_status == "WORKING"
                and entry_window == "OPEN"
                and move_phase in ("RELOAD", "MID", "EARLY")
                and mem_authority == "FORCEFUL"
                and mem_n >= 5.0
                and proof_stack_authority == "FORCEFUL"
                and reinforcement_authority == "FORCEFUL"
                and promotion_authority == "STACKED"
                and promotive_layers >= 2
                and reinforcement_bucket in ("REINFORCED", "STRONG_REINFORCED")
            ):
                _capital = {
                    "capital_posture": "SCALE_CANDIDATE",
                    "capital_band": "FULL",
                    "guidance": "Eligible for fuller sizing once live capital rules are enabled.",
                    "why": "strong trust, promotive proof stack, and reinforced compounding continuation",
                }
            elif (
                trust_label in ("HIGH_TRUST", "CONDITIONAL_TRUST")
                and triage_state in ("INVESTIGATE_NOW", "MONITOR")
                and entry_window == "OPEN"
                and move_phase in ("RELOAD", "MID", "EARLY")
                and mem_authority in ("FORCEFUL", "BACKED")
                and proof_stack_authority in ("FORCEFUL", "BACKED")
                and reinforcement_authority in ("FORCEFUL", "BACKED", "TENTATIVE")
                and promotion_authority in ("STACKED", "SUPPORTED")
                and (promotive_layers >= 1 or early_layers >= 1 or reinforcement_bucket in ("REINFORCED", "STRONG_REINFORCED"))
            ):
                _capital = {
                    "capital_posture": "PROBE_ONLY",
                    "capital_band": "SMALL",
                    "guidance": "At most probe-sized capital when live rules exist. Good continuation shape, but proof stack is not fully promotive yet.",
                    "why": "continuation quality is constructive, but proof stack is still maturing",
                }
            elif (
                bool(c.get("fresh_qualified"))
                and freshness_bucket == "RECENT_REACTIVATION"
                and trust_label in ("HIGH_TRUST", "CONDITIONAL_TRUST")
                and triage_state in ("INVESTIGATE_NOW", "MONITOR")
                and entry_window == "OPEN"
                and move_phase in ("RELOAD", "MID", "EARLY")
                and mem_authority in ("FORCEFUL", "BACKED")
                and mem_n >= 4.0
                and fresh_mem is not None
                and fresh_mem_state == "COMPOUNDING"
                and fresh_mem_conf in ("HIGH", "MEDIUM")
                and fresh_discovery_authority in ("FORCEFUL", "BACKED")
                and proof_stack_authority in ("FORCEFUL", "BACKED")
                and reinforcement_authority in ("FORCEFUL", "BACKED", "TENTATIVE")
                and promotion_authority in ("STACKED", "SUPPORTED")
                and adverse_layers == 0
            ):
                _capital = {
                    "capital_posture": "PROBE_ONLY",
                    "capital_band": "SMALL",
                    "guidance": "Small-probe only. Recent reactivation is behaving constructively, but keep sizing tight until broader proof matures.",
                    "why": "recent-reactivation freshness memory is compounding and continuation shape is constructive",
                }
            else:
                _capital = {
                    "capital_posture": "RESEARCH_ONLY",
                    "capital_band": "ZERO",
                    "guidance": "Keep under watch and research. Let proof, trust, and setup quality improve first.",
                    "why": "setup is interesting but not capital-ready yet",
                }

            _posture_mem = capital_posture_memory.get(str(_capital["capital_posture"] or "UNKNOWN"))
            _posture_mem_state = str(((_posture_mem or {}).get("state")) or "THIN")
            _posture_mem_n = float(((_posture_mem or {}).get("trailing_weighted_n")) or ((_posture_mem or {}).get("trailing_n")) or 0.0)
            _posture_mem_conf = float(((_posture_mem or {}).get("confidence")) or 0.0)

            if (
                _capital["capital_posture"] == "SCALE_CANDIDATE"
                and _posture_mem is not None
                and (
                    _posture_mem_state in ("FRAGILE", "THIN")
                    or (_posture_mem_n >= 4.0 and _posture_mem_conf < 0.5)
                )
            ):
                _capital = {
                    "capital_posture": "PROBE_ONLY",
                    "capital_band": "SMALL",
                    "guidance": "Scale-sized deployment stays capped to probe-sized while scale-candidate memory is still thin.",
                    "why": f"scale-candidate outcome memory is {_posture_mem_state.lower()} (n={round(_posture_mem_n, 1)})",
                    "capital_posture_memory": _posture_mem,
                    "freshness_bucket_memory": fresh_mem,
                }
            elif (
                _capital["capital_posture"] == "PROBE_ONLY"
                and _posture_mem is not None
                and _posture_mem_state == "FRAGILE"
                and _posture_mem_n >= 4.0
            ):
                _capital = {
                    "capital_posture": "RESEARCH_ONLY",
                    "capital_band": "ZERO",
                    "guidance": "Recent probe-sized continuation outcomes are degrading. Keep this in research-only until memory improves.",
                    "why": f"probe-only outcome memory is fragile (n={round(_posture_mem_n, 1)})",
                    "capital_posture_memory": _posture_mem,
                    "freshness_bucket_memory": fresh_mem,
                }
            elif (
                _capital["capital_posture"] in ("SCALE_CANDIDATE", "PROBE_ONLY")
                and fresh_mem is not None
                and fresh_mem_state == "FRAGILE"
                and fresh_mem_n >= 4.0
            ):
                _capital = {
                    "capital_posture": "RESEARCH_ONLY" if _capital["capital_posture"] == "PROBE_ONLY" else "PROBE_ONLY",
                    "capital_band": "ZERO" if _capital["capital_posture"] == "PROBE_ONLY" else "SMALL",
                    "guidance": (
                        "Recent freshness-conditioned continuation outcomes are degrading. Keep this in research-only until freshness memory improves."
                        if _capital["capital_posture"] == "PROBE_ONLY"
                        else "Scale-sized deployment stays capped while freshness-conditioned continuation outcomes are degrading."
                    ),
                    "why": f"{freshness_bucket.lower().replace('_', ' ')} memory is fragile (n={round(fresh_mem_n, 1)})",
                    "capital_posture_memory": _posture_mem,
                    "freshness_bucket_memory": fresh_mem,
                }
            elif (
                _capital["capital_posture"] in ("SCALE_CANDIDATE", "PROBE_ONLY")
                and fresh_discovery_authority == "ADVERSE"
            ):
                _capital = {
                    "capital_posture": "RESEARCH_ONLY" if _capital["capital_posture"] == "PROBE_ONLY" else "PROBE_ONLY",
                    "capital_band": "ZERO" if _capital["capital_posture"] == "PROBE_ONLY" else "SMALL",
                    "guidance": (
                        "Fresh discovery authority is adverse. Keep this in research-only until safe-fresh evidence improves."
                        if _capital["capital_posture"] == "PROBE_ONLY"
                        else "Scale-sized deployment stays capped while safe-fresh discovery authority is adverse."
                    ),
                    "why": "fresh discovery authority is adverse",
                    "capital_posture_memory": _posture_mem,
                    "freshness_bucket_memory": fresh_mem,
                }
            elif (
                _capital["capital_posture"] in ("SCALE_CANDIDATE", "PROBE_ONLY")
                and mem_authority == "ADVERSE"
            ):
                _capital = {
                    "capital_posture": "RESEARCH_ONLY" if _capital["capital_posture"] == "PROBE_ONLY" else "PROBE_ONLY",
                    "capital_band": "ZERO" if _capital["capital_posture"] == "PROBE_ONLY" else "SMALL",
                    "guidance": (
                        "Continuation memory authority is adverse. Keep this in research-only until the memory stack recovers."
                        if _capital["capital_posture"] == "PROBE_ONLY"
                        else "Scale-sized deployment stays capped while continuation memory authority is adverse."
                    ),
                    "why": "continuation memory authority is adverse",
                    "capital_posture_memory": _posture_mem,
                    "freshness_bucket_memory": fresh_mem,
                }
            else:
                _capital["capital_posture_memory"] = _posture_mem
                _capital["freshness_bucket_memory"] = fresh_mem

            _capital["continuation_memory_authority"] = mem_authority
            _capital["fresh_discovery_authority"] = fresh_discovery_authority
            _capital["proof_stack_authority"] = proof_stack_authority
            _capital["reinforcement_authority"] = reinforcement_authority
            _capital["promotion_authority"] = promotion_authority

            return _capital

        def _memecoin_capital_context() -> dict:
            """
            Shared memecoin capital context for planning guidance.
            This is intentionally conservative: it reflects the current runtime
            mode and remaining guardrail capacity without triggering execution.
            """
            try:
                from utils.memecoin_manager import get_memecoin_mode  # type: ignore
                _mode = str(get_memecoin_mode() or "PAPER")
            except Exception:
                _dry = os.getenv("MEMECOIN_DRY_RUN", "true").lower() == "true"
                _pilot = os.getenv("MEMECOIN_PILOT_MODE", "false").lower() == "true"
                _mode = "PAPER" if _dry else ("PILOT" if _pilot else "LIVE")

            _auto_buy = os.getenv("MEMECOIN_AUTO_BUY", "false").lower() == "true"
            _max_open = int(os.getenv("MEMECOIN_MAX_OPEN", "3"))
            _buy_usd = float(os.getenv("MEMECOIN_BUY_USD", "15"))
            _pilot_max_usd = float(os.getenv("MEMECOIN_PILOT_MAX_USD", "10"))
            _pilot_total_cap = float(os.getenv("MEMECOIN_PILOT_TOTAL_CAP_USD", "50"))

            _where = "status='OPEN'"
            if _mode == "PILOT":
                _where += " AND is_pilot=1"

            _row = conn.execute(
                f"""
                SELECT
                    COUNT(*) AS open_count,
                    COALESCE(SUM(amount_usd), 0) AS exposure_usd
                FROM memecoin_trades
                WHERE {_where}
                """
            ).fetchone()
            _open_count = int((_row["open_count"] if _row else 0) or 0)
            _open_exposure = float((_row["exposure_usd"] if _row else 0.0) or 0.0)

            _reference_per_trade = _pilot_max_usd if _mode == "PILOT" else _buy_usd
            _reference_total_cap = _pilot_total_cap if _mode == "PILOT" else (_buy_usd * max(_max_open, 1))
            _remaining_slots = max(0, _max_open - _open_count)
            _remaining_cap = max(0.0, _reference_total_cap - _open_exposure)

            _spot_invested = 0.0
            try:
                from utils.spot_accumulator import get_portfolio_state  # type: ignore
                _spot_state = get_portfolio_state() or {}
                _spot_invested = float(_spot_state.get("total_invested") or 0.0)
            except Exception:
                _spot_invested = 0.0

            _perps_collateral = 0.0
            _perps_positions = 0
            try:
                _prow = conn.execute(
                    """
                    SELECT
                        COUNT(DISTINCT symbol) AS open_positions,
                        COALESCE(SUM(collateral_usd), 0) AS collateral_usd
                    FROM perp_positions
                    WHERE status='OPEN'
                    """
                ).fetchone()
                if _prow:
                    _perps_positions = int((_prow["open_positions"] or 0))
                    _perps_collateral = float((_prow["collateral_usd"] or 0.0))
            except Exception:
                _perps_collateral = 0.0
                _perps_positions = 0

            _fg_value = None
            _fg_bucket = "UNKNOWN"
            try:
                from utils.agent_coordinator import get_fear_greed  # type: ignore
                _fg = get_fear_greed() or {}
                _fg_value = _fg.get("value")
                if _fg_value is not None:
                    _fg_i = int(_fg_value)
                    if _fg_i < 15:
                        _fg_bucket = "XFEAR"
                    elif _fg_i < 25:
                        _fg_bucket = "FEAR"
                    elif _fg_i < 40:
                        _fg_bucket = "CAUTIOUS"
                    else:
                        _fg_bucket = "NEUTRAL_PLUS"
            except Exception:
                pass

            _cycle_phase = "TRANSITION"
            try:
                from utils.market_cycle import get_current_cycle_phase  # type: ignore
                _cycle_phase = str(get_current_cycle_phase() or "TRANSITION").upper()
            except Exception:
                _cycle_phase = "TRANSITION"

            _total_deployed = max(0.0, _spot_invested + _perps_collateral)
            if _perps_positions > 0 or _total_deployed >= 3000:
                _pressure_bucket = "HIGH"
                _pressure_note = "Broader portfolio exposure is already elevated."
            elif _total_deployed >= 1000:
                _pressure_bucket = "MEDIUM"
                _pressure_note = "Broader portfolio already has meaningful deployed capital."
            else:
                _pressure_bucket = "LOW"
                _pressure_note = "Broader portfolio deployment is still light."

            if _cycle_phase == "BEAR" or _fg_bucket in ("XFEAR", "FEAR"):
                _regime_bucket = "RISK_OFF"
                _regime_note = "Market regime is still risk-off for memecoin deployment."
            elif _cycle_phase == "BULL" and _fg_bucket == "NEUTRAL_PLUS":
                _regime_bucket = "RISK_ON"
                _regime_note = "Market regime is supportive for continuation deployment."
            else:
                _regime_bucket = "MIXED"
                _regime_note = "Market regime is mixed; keep memecoin deployment disciplined."

            if _total_deployed < 250:
                _mix_bucket = "LIGHT"
                _mix_note = "Broader portfolio is still lightly deployed."
            elif _spot_invested >= max(250.0, _perps_collateral * 2.0):
                _mix_bucket = "SPOT_HEAVY"
                _mix_note = "Spot inventory dominates current portfolio deployment."
            elif _perps_collateral >= max(150.0, _spot_invested * 1.25):
                _mix_bucket = "PERP_HEAVY"
                _mix_note = "Perp collateral is dominating current portfolio deployment."
            else:
                _mix_bucket = "BALANCED"
                _mix_note = "Spot and perp deployment are relatively balanced."

            _cap_pressure_mult = 0.55 if _pressure_bucket == "HIGH" else 0.8 if _pressure_bucket == "MEDIUM" else 1.0
            _cap_regime_mult = 0.55 if _regime_bucket == "RISK_OFF" else 0.8 if _regime_bucket == "MIXED" else 1.0
            _cap_mix_mult = 0.75 if _mix_bucket == "PERP_HEAVY" else 0.9 if _mix_bucket == "BALANCED" else 1.0
            _effective_mult = max(0.2, round(_cap_pressure_mult * _cap_regime_mult * _cap_mix_mult, 3))
            _effective_total_cap = round(max(_reference_total_cap * 0.2, _reference_total_cap * _effective_mult), 2)
            _effective_per_trade = round(max(_reference_per_trade * 0.35, _reference_per_trade * _effective_mult), 2)
            _effective_remaining_cap = round(max(0.0, _effective_total_cap - _open_exposure), 2)
            _headroom_bucket = _capital_headroom_bucket(
                _effective_remaining_cap,
                _effective_per_trade,
                _remaining_slots,
            )
            if _headroom_bucket == "EXHAUSTED":
                _headroom_note = "Effective memecoin headroom is exhausted."
            elif _headroom_bucket == "THIN":
                _headroom_note = "Only thin memecoin headroom remains."
            elif _headroom_bucket == "WORKABLE":
                _headroom_note = "Memecoin headroom is workable but not abundant."
            else:
                _headroom_note = "Memecoin headroom is ample within current guardrails."
            _window_bucket = _capital_window_bucket(
                _mode,
                _auto_buy,
                _remaining_slots,
                _effective_per_trade,
                _effective_remaining_cap,
                _effective_mult,
            )
            if _window_bucket == "CLOSED":
                _window_note = "Memecoin deployment window is effectively closed."
            elif _window_bucket == "MICRO_WINDOW":
                _window_note = "Memecoin deployment window is open only for very small probes."
            elif _window_bucket == "LIMITED_WINDOW":
                _window_note = "Memecoin deployment window is limited and should stay selective."
            else:
                _window_note = "Memecoin deployment window is open within current guardrails."
            _route_bucket = _capital_route_bucket(
                _mode,
                _auto_buy,
                "RESEARCH_ONLY",
                "READY" if _mode in ("PILOT", "LIVE") and _auto_buy and _remaining_slots > 0 and _effective_remaining_cap > 0 else "PLANNING_ONLY",
                _window_bucket,
                _headroom_bucket,
                _regime_bucket,
            )
            if _route_bucket == "LOCKED":
                _route_note = "Memecoin allocator route is locked."
            elif _route_bucket == "MICRO_PROBE_ONLY":
                _route_note = "Memecoin allocator route allows only micro probes."
            elif _route_bucket == "DISCIPLINED_PROBE":
                _route_note = "Memecoin allocator route is open only for disciplined probes."
            else:
                _route_note = "Memecoin allocator route is open for scale-ready setups."
            _marginal_route = _capital_marginal_route(_route_bucket)
            _allocator_stance, _allocator_note = _capital_allocator_stance(
                _mode,
                _auto_buy,
                _remaining_slots,
                _effective_remaining_cap,
                _pressure_bucket,
                _regime_bucket,
                _mix_bucket,
            )

            return {
                "mode": _mode,
                "auto_buy": _auto_buy,
                "max_open": _max_open,
                "open_count": _open_count,
                "remaining_slots": _remaining_slots,
                "reference_per_trade_usd": round(_reference_per_trade, 2),
                "reference_total_cap_usd": round(_reference_total_cap, 2),
                "effective_per_trade_usd": _effective_per_trade,
                "effective_total_cap_usd": _effective_total_cap,
                "effective_cap_multiplier": _effective_mult,
                "open_exposure_usd": round(_open_exposure, 2),
                "remaining_cap_usd": round(_remaining_cap, 2),
                "effective_remaining_cap_usd": _effective_remaining_cap,
                "spot_invested_usd": round(_spot_invested, 2),
                "perps_collateral_usd": round(_perps_collateral, 2),
                "perps_positions": _perps_positions,
                "total_deployed_usd": round(_total_deployed, 2),
                "capital_pressure_bucket": _pressure_bucket,
                "capital_pressure_note": _pressure_note,
                "fg_value": _fg_value,
                "fg_bucket": _fg_bucket,
                "market_cycle_phase": _cycle_phase,
                "capital_regime_bucket": _regime_bucket,
                "capital_regime_note": _regime_note,
                "capital_mix_bucket": _mix_bucket,
                "capital_mix_note": _mix_note,
                "capital_allocator_stance": _allocator_stance,
                "capital_allocator_note": _allocator_note,
                "capital_marginal_route": _marginal_route,
                "capital_headroom_bucket": _headroom_bucket,
                "capital_headroom_note": _headroom_note,
                "capital_route_bucket": _route_bucket,
                "capital_route_note": _route_note,
                "capital_window_bucket": _window_bucket,
                "capital_window_note": _window_note,
            }

        def _capital_plan_for_candidate(c: dict, capital_posture: dict) -> dict:
            """
            Translate capital posture into a concrete planning envelope.
            Budgets are guidance only and remain mode-aware:
            PAPER => planning-only
            PILOT/LIVE => constrained by current open exposure and slot capacity
            """
            _ctx = _memecoin_capital_context()
            _posture = str(capital_posture.get("capital_posture") or "RESEARCH_ONLY")
            _band = str(capital_posture.get("capital_band") or "ZERO")
            _per_trade = float(_ctx.get("effective_per_trade_usd") or _ctx.get("reference_per_trade_usd") or 0.0)
            _remaining_cap = float(_ctx.get("effective_remaining_cap_usd") or _ctx.get("remaining_cap_usd") or 0.0)
            _remaining_slots = int(_ctx.get("remaining_slots") or 0)
            _mode = str(_ctx.get("mode") or "PAPER")
            _auto_buy = bool(_ctx.get("auto_buy"))
            _reinforcement = str(c.get("reinforcement_bucket") or "PLAIN")
            _trust = str(c.get("trust_label") or "")
            _pressure_bucket = str(_ctx.get("capital_pressure_bucket") or "LOW")
            _pressure_note = str(_ctx.get("capital_pressure_note") or "")
            _regime_bucket = str(_ctx.get("capital_regime_bucket") or "MIXED")
            _regime_note = str(_ctx.get("capital_regime_note") or "")
            _mix_bucket = str(_ctx.get("capital_mix_bucket") or "UNKNOWN")
            _mix_note = str(_ctx.get("capital_mix_note") or "")
            _allocator_stance = str(_ctx.get("capital_allocator_stance") or "UNKNOWN")
            _allocator_note = str(_ctx.get("capital_allocator_note") or "")
            _marginal_route = str(_ctx.get("capital_marginal_route") or _capital_marginal_route(_ctx.get("capital_route_bucket")))
            _headroom_bucket = str(_ctx.get("capital_headroom_bucket") or "UNKNOWN")
            _headroom_note = str(_ctx.get("capital_headroom_note") or "")
            _window_bucket = str(_ctx.get("capital_window_bucket") or "CLOSED")
            _window_note = str(_ctx.get("capital_window_note") or "")
            _effective_total_cap = float(_ctx.get("effective_total_cap_usd") or 0.0)
            _effective_mult = float(_ctx.get("effective_cap_multiplier") or 1.0)
            _pressure_mem = capital_pressure_bucket_memory.get(_pressure_bucket)
            _regime_mem = capital_regime_bucket_memory.get(_regime_bucket)
            _mix_mem = capital_mix_bucket_memory.get(_mix_bucket)
            _allocator_stance_mem = capital_allocator_stance_memory.get(_allocator_stance)
            _headroom_mem = capital_headroom_bucket_memory.get(_headroom_bucket)
            _window_mem = capital_window_bucket_memory.get(_window_bucket)
            _pressure_mem_state = str(((_pressure_mem or {}).get("state")) or "THIN")
            _pressure_mem_n = float(((_pressure_mem or {}).get("trailing_weighted_n")) or ((_pressure_mem or {}).get("trailing_n")) or 0.0)
            _pressure_mem_conf = float(((_pressure_mem or {}).get("confidence")) or 0.0)
            _regime_mem_state = str(((_regime_mem or {}).get("state")) or "THIN")
            _regime_mem_n = float(((_regime_mem or {}).get("trailing_weighted_n")) or ((_regime_mem or {}).get("trailing_n")) or 0.0)
            _regime_mem_conf = float(((_regime_mem or {}).get("confidence")) or 0.0)
            _mix_mem_state = str(((_mix_mem or {}).get("state")) or "THIN")
            _mix_mem_n = float(((_mix_mem or {}).get("trailing_weighted_n")) or ((_mix_mem or {}).get("trailing_n")) or 0.0)
            _mix_mem_conf = float(((_mix_mem or {}).get("confidence")) or 0.0)
            _allocator_stance_mem_state = str(((_allocator_stance_mem or {}).get("state")) or "THIN")
            _allocator_stance_mem_n = float(((_allocator_stance_mem or {}).get("trailing_weighted_n")) or ((_allocator_stance_mem or {}).get("trailing_n")) or 0.0)
            _allocator_stance_mem_conf = float(((_allocator_stance_mem or {}).get("confidence")) or 0.0)
            _headroom_mem_state = str(((_headroom_mem or {}).get("state")) or "THIN")
            _headroom_mem_n = float(((_headroom_mem or {}).get("trailing_weighted_n")) or ((_headroom_mem or {}).get("trailing_n")) or 0.0)
            _headroom_mem_conf = float(((_headroom_mem or {}).get("confidence")) or 0.0)
            _window_mem_state = str(((_window_mem or {}).get("state")) or "THIN")
            _window_mem_n = float(((_window_mem or {}).get("trailing_weighted_n")) or ((_window_mem or {}).get("trailing_n")) or 0.0)
            _window_mem_conf = float(((_window_mem or {}).get("confidence")) or 0.0)

            _blockers: list[str] = []
            if _mode == "PAPER":
                _blockers.append("runtime mode is PAPER")
            if not _auto_buy:
                _blockers.append("auto-buy is disabled")
            if _remaining_slots <= 0:
                _blockers.append("position capacity is full")
            if _remaining_cap <= 0:
                _blockers.append("adaptive memecoin cap is exhausted")
            if _pressure_bucket == "HIGH":
                _blockers.append("broader portfolio exposure is already elevated")
            elif _pressure_bucket == "MEDIUM":
                _blockers.append("broader portfolio already has meaningful deployment")
            if _regime_bucket == "RISK_OFF":
                _blockers.append("market regime is risk-off for memecoin deployment")
            elif _regime_bucket == "MIXED":
                _blockers.append("market regime is mixed")
            if _mix_bucket == "PERP_HEAVY":
                _blockers.append("portfolio is already perp-heavy")
            if _allocator_stance in ("PAPER_ONLY", "DISABLED", "SATURATED"):
                _blockers.append(_allocator_note.lower())
            if _headroom_bucket == "EXHAUSTED":
                _blockers.append("effective memecoin headroom is exhausted")
            elif _headroom_bucket == "THIN":
                _blockers.append("effective memecoin headroom is thin")
            if _window_bucket == "CLOSED":
                _blockers.append("memecoin deployment window is effectively closed")
            elif _window_bucket == "MICRO_WINDOW":
                _blockers.append("memecoin deployment window is micro-sized")
            elif _window_bucket == "LIMITED_WINDOW":
                _blockers.append("memecoin deployment window is limited")

            _pressure_mult = 0.5 if _pressure_bucket == "HIGH" else 0.75 if _pressure_bucket == "MEDIUM" else 1.0
            _regime_mult = 0.55 if _regime_bucket == "RISK_OFF" else 0.8 if _regime_bucket == "MIXED" else 1.0
            _mix_mult = 0.75 if _mix_bucket == "PERP_HEAVY" else 0.9 if _mix_bucket == "BALANCED" else 1.0
            if (
                _pressure_bucket in ("HIGH", "MEDIUM")
                and _pressure_mem is not None
                and (
                    _pressure_mem_state == "FRAGILE"
                    or (_pressure_mem_n >= 4.0 and _pressure_mem_conf < 0.5)
                )
            ):
                _pressure_mult *= 0.75
                _blockers.append(f"{_pressure_bucket.lower()}-pressure outcome memory is {_pressure_mem_state.lower()}")
            if (
                _regime_bucket in ("RISK_OFF", "MIXED")
                and _regime_mem is not None
                and (
                    _regime_mem_state == "FRAGILE"
                    or (_regime_mem_n >= 4.0 and _regime_mem_conf < 0.5)
                )
            ):
                _regime_mult *= 0.75
                _blockers.append(f"{_regime_bucket.lower().replace('_', '-')}-regime outcome memory is {_regime_mem_state.lower()}")
            if (
                _mix_bucket in ("PERP_HEAVY", "BALANCED")
                and _mix_mem is not None
                and (
                    _mix_mem_state == "FRAGILE"
                    or (_mix_mem_n >= 4.0 and _mix_mem_conf < 0.5)
                )
            ):
                _mix_mult *= 0.8
                _blockers.append(f"{_mix_bucket.lower().replace('_', '-')}-mix outcome memory is {_mix_mem_state.lower()}")
            _deploy_mult = _pressure_mult * _regime_mult * _mix_mult
            if (
                _allocator_stance in ("TIGHT", "DISCIPLINED")
                and _allocator_stance_mem is not None
                and (
                    _allocator_stance_mem_state == "FRAGILE"
                    or (_allocator_stance_mem_n >= 4.0 and _allocator_stance_mem_conf < 0.5)
                )
            ):
                _deploy_mult *= 0.85
                _blockers.append(f"{_allocator_stance.lower()}-allocator outcome memory is {_allocator_stance_mem_state.lower()}")
            if (
                _headroom_bucket in ("THIN", "WORKABLE")
                and _headroom_mem is not None
                and (
                    _headroom_mem_state == "FRAGILE"
                    or (_headroom_mem_n >= 4.0 and _headroom_mem_conf < 0.5)
                )
            ):
                _deploy_mult *= 0.85
                _blockers.append(f"{_headroom_bucket.lower()}-headroom outcome memory is {_headroom_mem_state.lower()}")
            if (
                _window_bucket in ("LIMITED_WINDOW", "OPEN_WINDOW")
                and _window_mem is not None
                and (
                    _window_mem_state == "FRAGILE"
                    or (_window_mem_n >= 4.0 and _window_mem_conf < 0.5)
                )
            ):
                _deploy_mult *= 0.8
                _blockers.append(f"{_window_bucket.lower().replace('_', '-')}-outcome memory is {_window_mem_state.lower()}")
            _route_bucket = _capital_route_bucket(
                _mode,
                _auto_buy,
                _posture,
                "READY" if _mode in ("PILOT", "LIVE") and _auto_buy and _remaining_slots > 0 and _remaining_cap > 0 else "PLANNING_ONLY",
                _window_bucket,
                _headroom_bucket,
                _regime_bucket,
            )
            _route_mem = capital_route_bucket_memory.get(_route_bucket)
            _routing_family = _capital_routing_family(_allocator_stance, _route_bucket, _marginal_route)
            _routing_family_mem = capital_routing_family_memory.get(_routing_family)
            _route_mem_state = str(((_route_mem or {}).get("state")) or "THIN")
            _route_mem_n = float(((_route_mem or {}).get("trailing_weighted_n")) or ((_route_mem or {}).get("trailing_n")) or 0.0)
            _route_mem_conf = float(((_route_mem or {}).get("confidence")) or 0.0)
            _routing_family_mem_state = str(((_routing_family_mem or {}).get("state")) or "THIN")
            _routing_family_mem_n = float(((_routing_family_mem or {}).get("trailing_weighted_n")) or ((_routing_family_mem or {}).get("trailing_n")) or 0.0)
            _routing_family_mem_conf = float(((_routing_family_mem or {}).get("confidence")) or 0.0)
            if (
                _route_bucket in ("DISCIPLINED_PROBE", "SCALE_READY")
                and _routing_family_mem is not None
                and (
                    _routing_family_mem_state == "FRAGILE"
                    or (_routing_family_mem_n >= 4.0 and _routing_family_mem_conf < 0.5)
                )
            ):
                _deploy_mult *= 0.8
                _blockers.append("combined routing-family outcome memory is fragile")
            if (
                _route_bucket in ("DISCIPLINED_PROBE", "SCALE_READY")
                and _route_mem is not None
                and (
                    _route_mem_state == "FRAGILE"
                    or (_route_mem_n >= 4.0 and _route_mem_conf < 0.5)
                )
            ):
                _deploy_mult *= 0.8
                _blockers.append(f"{_route_bucket.lower().replace('_', '-')}-route outcome memory is {_route_mem_state.lower()}")

            if _posture in ("NO_DEPLOY", "RESEARCH_ONLY"):
                _ready_state = "PLANNING_ONLY" if _mode == "PAPER" else "RESEARCH_ONLY"
                _deploy_authority = _deployment_authority(
                    capital_posture.get("promotion_authority"),
                    capital_posture.get("reinforcement_authority"),
                    _ready_state,
                    "LOCKED",
                    _headroom_bucket,
                )
                _deploy_authority_mem = deployment_authority_memory.get(_deploy_authority)
                _decision_authority_val = _decision_authority(
                    capital_posture.get("proof_stack_authority"),
                    capital_posture.get("continuation_memory_authority"),
                    capital_posture.get("fresh_discovery_authority"),
                    capital_posture.get("reinforcement_authority"),
                    capital_posture.get("promotion_authority"),
                    _deploy_authority,
                )
                _decision_authority_mem = decision_authority_memory.get(_decision_authority_val)
                _family = _capital_deployment_family(
                    _posture,
                    _ready_state,
                    _pressure_bucket,
                    _regime_bucket,
                )
                return {
                    "capital_ready_state": _ready_state,
                    "suggested_entry_usd": 0.0,
                    "max_entry_usd": 0.0,
                    "staged_scale_usd": [],
                    "remaining_cap_usd": round(_remaining_cap, 2),
                    "remaining_slots": _remaining_slots,
                    "effective_per_trade_usd": round(_per_trade, 2),
                    "effective_total_cap_usd": round(_effective_total_cap, 2),
                    "effective_cap_multiplier": round(_effective_mult, 3),
                    "deployment_blockers": _blockers,
                    "capital_pressure_bucket": _pressure_bucket,
                    "capital_pressure_note": _pressure_note,
                    "capital_pressure_memory": _pressure_mem,
                    "capital_regime_bucket": _regime_bucket,
                    "capital_regime_note": _regime_note,
                    "capital_mix_bucket": _mix_bucket,
                    "capital_mix_note": _mix_note,
                    "capital_mix_memory": _mix_mem,
                    "capital_allocator_stance": _allocator_stance,
                    "capital_allocator_note": _allocator_note,
                    "capital_allocator_stance_memory": _allocator_stance_mem,
                    "capital_marginal_route": _marginal_route,
                    "capital_routing_family": _routing_family,
                    "capital_routing_family_memory": _routing_family_mem,
                    "capital_headroom_bucket": _headroom_bucket,
                    "capital_headroom_note": _headroom_note,
                    "capital_headroom_memory": _headroom_mem,
                    "deployment_authority": _deploy_authority,
                    "deployment_authority_memory": _deploy_authority_mem,
                    "decision_authority": _decision_authority_val,
                    "decision_authority_memory": _decision_authority_mem,
                    "capital_route_bucket": "LOCKED",
                    "capital_route_memory": capital_route_bucket_memory.get("LOCKED"),
                    "capital_window_bucket": _window_bucket,
                    "capital_window_note": _window_note,
                    "capital_window_memory": _window_mem,
                    "capital_intensity_bucket": "ZERO",
                    "capital_intensity_bucket_memory": capital_intensity_bucket_memory.get("ZERO"),
                    "capital_deployment_family": _family,
                    "capital_deployment_family_memory": capital_deployment_family_memory.get(_family),
                }

            if _posture == "PROBE_ONLY":
                _probe_mult = 0.75 if _reinforcement == "STRONG_REINFORCED" or _trust == "HIGH_TRUST" else 0.5
                _max_entry = min(round(_per_trade * _deploy_mult, 2), _remaining_cap)
                _suggested = min(_max_entry, round(_per_trade * _probe_mult, 2))
                _ready = "READY" if _mode in ("PILOT", "LIVE") and _auto_buy and _remaining_slots > 0 and _remaining_cap > 0 else "PLANNING_ONLY"
                _ready_mem = capital_ready_state_memory.get(_ready)
                _ready_mem_state = str(((_ready_mem or {}).get("state")) or "THIN")
                _ready_mem_n = float(((_ready_mem or {}).get("trailing_weighted_n")) or ((_ready_mem or {}).get("trailing_n")) or 0.0)
                if _ready == "READY" and _ready_mem is not None and _ready_mem_state == "FRAGILE" and _ready_mem_n >= 4.0:
                    _ready = "PLANNING_ONLY"
                    _blockers.append("ready-state outcome memory is fragile")
                _family = _capital_deployment_family(_posture, _ready, _pressure_bucket, _regime_bucket)
                _family_mem = capital_deployment_family_memory.get(_family)
                _family_mem_state = str(((_family_mem or {}).get("state")) or "THIN")
                _family_mem_n = float(((_family_mem or {}).get("trailing_weighted_n")) or ((_family_mem or {}).get("trailing_n")) or 0.0)
                _family_mem_conf = float(((_family_mem or {}).get("confidence")) or 0.0)
                if (
                    _ready == "READY"
                    and _family_mem is not None
                    and (
                        _family_mem_state == "FRAGILE"
                        or (_family_mem_n >= 4.0 and _family_mem_conf < 0.5)
                    )
                ):
                    _ready = "PLANNING_ONLY"
                    _blockers.append("combined deployment-family memory is fragile")
                    _family = _capital_deployment_family(_posture, _ready, _pressure_bucket, _regime_bucket)
                    _family_mem = capital_deployment_family_memory.get(_family)
                _intensity_bucket = _capital_intensity_bucket(_suggested)
                _intensity_mem = capital_intensity_bucket_memory.get(_intensity_bucket)
                _intensity_mem_state = str(((_intensity_mem or {}).get("state")) or "THIN")
                _intensity_mem_n = float(((_intensity_mem or {}).get("trailing_weighted_n")) or ((_intensity_mem or {}).get("trailing_n")) or 0.0)
                _intensity_mem_conf = float(((_intensity_mem or {}).get("confidence")) or 0.0)
                if (
                    _suggested > 0
                    and _intensity_bucket in ("MEDIUM", "LARGE")
                    and _intensity_mem is not None
                    and (
                        _intensity_mem_state == "FRAGILE"
                        or (_intensity_mem_n >= 4.0 and _intensity_mem_conf < 0.5)
                    )
                ):
                    _suggested = min(_suggested, round(max(_per_trade * 0.75, 0.0), 2))
                    _max_entry = max(_suggested, min(_max_entry, round(max(_per_trade, _suggested), 2)))
                    _intensity_bucket = _capital_intensity_bucket(_suggested)
                    _intensity_mem = capital_intensity_bucket_memory.get(_intensity_bucket)
                    _blockers.append("higher-intensity outcome memory is fragile")
                _candidate_window_bucket = _capital_window_bucket(
                    _mode,
                    _auto_buy,
                    _remaining_slots,
                    _per_trade,
                    _remaining_cap,
                    _effective_mult,
                    _ready,
                    _suggested,
                )
                _candidate_window_mem = capital_window_bucket_memory.get(_candidate_window_bucket)
                _candidate_route_bucket = _capital_route_bucket(
                    _mode,
                    _auto_buy,
                    _posture,
                    _ready,
                    _candidate_window_bucket,
                    _headroom_bucket,
                    _regime_bucket,
                )
                _candidate_route_mem = capital_route_bucket_memory.get(_candidate_route_bucket)
                _candidate_marginal_route = _capital_marginal_route(_candidate_route_bucket)
                _candidate_routing_family = _capital_routing_family(_allocator_stance, _candidate_route_bucket, _candidate_marginal_route)
                _candidate_routing_family_mem = capital_routing_family_memory.get(_candidate_routing_family)
                _deploy_authority = _deployment_authority(
                    capital_posture.get("promotion_authority"),
                    capital_posture.get("reinforcement_authority"),
                    _ready,
                    _candidate_route_bucket,
                    _headroom_bucket,
                )
                _deploy_authority_mem = deployment_authority_memory.get(_deploy_authority)
                _deploy_authority_mem_state = str(((_deploy_authority_mem or {}).get("state")) or "THIN")
                _deploy_authority_mem_n = float(((_deploy_authority_mem or {}).get("trailing_weighted_n")) or ((_deploy_authority_mem or {}).get("trailing_n")) or 0.0)
                _deploy_authority_mem_conf = float(((_deploy_authority_mem or {}).get("confidence")) or 0.0)
                if (
                    _ready == "READY"
                    and _deploy_authority in ("EXECUTABLE", "CONDITIONALLY_READY")
                    and _deploy_authority_mem is not None
                    and (
                        _deploy_authority_mem_state == "FRAGILE"
                        or (_deploy_authority_mem_n >= 4.0 and _deploy_authority_mem_conf < 0.5)
                    )
                ):
                    _ready = "PLANNING_ONLY"
                    _blockers.append("deployment-authority outcome memory is fragile")
                    _candidate_route_bucket = _capital_route_bucket(
                        _mode,
                        _auto_buy,
                        _posture,
                        _ready,
                        _candidate_window_bucket,
                        _headroom_bucket,
                        _regime_bucket,
                    )
                    _candidate_route_mem = capital_route_bucket_memory.get(_candidate_route_bucket)
                    _candidate_marginal_route = _capital_marginal_route(_candidate_route_bucket)
                    _candidate_routing_family = _capital_routing_family(_allocator_stance, _candidate_route_bucket, _candidate_marginal_route)
                    _candidate_routing_family_mem = capital_routing_family_memory.get(_candidate_routing_family)
                    _deploy_authority = _deployment_authority(
                        capital_posture.get("promotion_authority"),
                        capital_posture.get("reinforcement_authority"),
                        _ready,
                        _candidate_route_bucket,
                        _headroom_bucket,
                    )
                    _deploy_authority_mem = deployment_authority_memory.get(_deploy_authority)
                _decision_authority_val = _decision_authority(
                    capital_posture.get("proof_stack_authority"),
                    capital_posture.get("continuation_memory_authority"),
                    capital_posture.get("fresh_discovery_authority"),
                    capital_posture.get("reinforcement_authority"),
                    capital_posture.get("promotion_authority"),
                    _deploy_authority,
                )
                _decision_authority_mem = decision_authority_memory.get(_decision_authority_val)
                return {
                    "capital_ready_state": _ready,
                    "suggested_entry_usd": round(max(_suggested, 0.0), 2),
                    "max_entry_usd": round(max(_max_entry, 0.0), 2),
                    "staged_scale_usd": [round(max(_suggested, 0.0), 2)] if _suggested > 0 else [],
                    "remaining_cap_usd": round(_remaining_cap, 2),
                    "remaining_slots": _remaining_slots,
                    "effective_per_trade_usd": round(_per_trade, 2),
                    "effective_total_cap_usd": round(_effective_total_cap, 2),
                    "effective_cap_multiplier": round(_effective_mult, 3),
                    "deployment_blockers": [] if _ready == "READY" else _blockers,
                    "capital_pressure_bucket": _pressure_bucket,
                    "capital_pressure_note": _pressure_note,
                    "capital_pressure_memory": _pressure_mem,
                    "capital_regime_bucket": _regime_bucket,
                    "capital_regime_note": _regime_note,
                    "capital_mix_bucket": _mix_bucket,
                    "capital_mix_note": _mix_note,
                    "capital_mix_memory": _mix_mem,
                    "capital_allocator_stance": _allocator_stance,
                    "capital_allocator_note": _allocator_note,
                    "capital_allocator_stance_memory": _allocator_stance_mem,
                    "capital_marginal_route": _candidate_marginal_route,
                    "capital_routing_family": _candidate_routing_family,
                    "capital_routing_family_memory": _candidate_routing_family_mem,
                    "capital_headroom_bucket": _headroom_bucket,
                    "capital_headroom_note": _headroom_note,
                    "capital_headroom_memory": _headroom_mem,
                    "deployment_authority": _deploy_authority,
                    "deployment_authority_memory": _deploy_authority_mem,
                    "decision_authority": _decision_authority_val,
                    "decision_authority_memory": _decision_authority_mem,
                    "capital_route_bucket": _candidate_route_bucket,
                    "capital_route_memory": _candidate_route_mem,
                    "capital_window_bucket": _candidate_window_bucket,
                    "capital_window_note": _window_note,
                    "capital_window_memory": _candidate_window_mem,
                    "capital_intensity_bucket": _intensity_bucket,
                    "capital_intensity_bucket_memory": _intensity_mem,
                    "capital_deployment_family": _family,
                    "capital_deployment_family_memory": _family_mem,
                }

            if _posture == "SCALE_CANDIDATE":
                _first_leg = min(round(_per_trade * _deploy_mult, 2), _remaining_cap)
                _second_leg = min(round(_per_trade * _deploy_mult, 2), max(0.0, _remaining_cap - _first_leg))
                _stages = [round(_first_leg, 2)] if _first_leg > 0 else []
                if _second_leg > 0 and _remaining_slots > 1:
                    _stages.append(round(_second_leg, 2))
                _ready = "READY" if _mode in ("PILOT", "LIVE") and _auto_buy and _remaining_slots > 0 and _remaining_cap > 0 else "PLANNING_ONLY"
                _ready_mem = capital_ready_state_memory.get(_ready)
                _ready_mem_state = str(((_ready_mem or {}).get("state")) or "THIN")
                _ready_mem_n = float(((_ready_mem or {}).get("trailing_weighted_n")) or ((_ready_mem or {}).get("trailing_n")) or 0.0)
                if _ready == "READY" and _ready_mem is not None and _ready_mem_state == "FRAGILE" and _ready_mem_n >= 4.0:
                    _ready = "PLANNING_ONLY"
                    _blockers.append("ready-state outcome memory is fragile")
                _family = _capital_deployment_family(_posture, _ready, _pressure_bucket, _regime_bucket)
                _family_mem = capital_deployment_family_memory.get(_family)
                _family_mem_state = str(((_family_mem or {}).get("state")) or "THIN")
                _family_mem_n = float(((_family_mem or {}).get("trailing_weighted_n")) or ((_family_mem or {}).get("trailing_n")) or 0.0)
                _family_mem_conf = float(((_family_mem or {}).get("confidence")) or 0.0)
                if (
                    _ready == "READY"
                    and _family_mem is not None
                    and (
                        _family_mem_state == "FRAGILE"
                        or (_family_mem_n >= 4.0 and _family_mem_conf < 0.5)
                    )
                ):
                    _ready = "PLANNING_ONLY"
                    _blockers.append("combined deployment-family memory is fragile")
                    _family = _capital_deployment_family(_posture, _ready, _pressure_bucket, _regime_bucket)
                    _family_mem = capital_deployment_family_memory.get(_family)
                _intensity_bucket = _capital_intensity_bucket(_first_leg)
                _intensity_mem = capital_intensity_bucket_memory.get(_intensity_bucket)
                _intensity_mem_state = str(((_intensity_mem or {}).get("state")) or "THIN")
                _intensity_mem_n = float(((_intensity_mem or {}).get("trailing_weighted_n")) or ((_intensity_mem or {}).get("trailing_n")) or 0.0)
                _intensity_mem_conf = float(((_intensity_mem or {}).get("confidence")) or 0.0)
                if (
                    _first_leg > 0
                    and _intensity_bucket in ("MEDIUM", "LARGE")
                    and _intensity_mem is not None
                    and (
                        _intensity_mem_state == "FRAGILE"
                        or (_intensity_mem_n >= 4.0 and _intensity_mem_conf < 0.5)
                    )
                ):
                    _first_leg = min(_first_leg, round(max(_per_trade * 0.75, 0.0), 2))
                    _second_leg = min(_second_leg, round(max(_per_trade * 0.5, 0.0), 2))
                    _stages = [round(_first_leg, 2)] if _first_leg > 0 else []
                    if _second_leg > 0 and _remaining_slots > 1:
                        _stages.append(round(_second_leg, 2))
                    _intensity_bucket = _capital_intensity_bucket(_first_leg)
                    _intensity_mem = capital_intensity_bucket_memory.get(_intensity_bucket)
                    _blockers.append("higher-intensity outcome memory is fragile")
                _candidate_window_bucket = _capital_window_bucket(
                    _mode,
                    _auto_buy,
                    _remaining_slots,
                    _per_trade,
                    _remaining_cap,
                    _effective_mult,
                    _ready,
                    _first_leg,
                )
                _candidate_window_mem = capital_window_bucket_memory.get(_candidate_window_bucket)
                _candidate_route_bucket = _capital_route_bucket(
                    _mode,
                    _auto_buy,
                    _posture,
                    _ready,
                    _candidate_window_bucket,
                    _headroom_bucket,
                    _regime_bucket,
                )
                _candidate_route_mem = capital_route_bucket_memory.get(_candidate_route_bucket)
                _candidate_marginal_route = _capital_marginal_route(_candidate_route_bucket)
                _candidate_routing_family = _capital_routing_family(_allocator_stance, _candidate_route_bucket, _candidate_marginal_route)
                _candidate_routing_family_mem = capital_routing_family_memory.get(_candidate_routing_family)
                _deploy_authority = _deployment_authority(
                    capital_posture.get("promotion_authority"),
                    capital_posture.get("reinforcement_authority"),
                    _ready,
                    _candidate_route_bucket,
                    _headroom_bucket,
                )
                _deploy_authority_mem = deployment_authority_memory.get(_deploy_authority)
                _deploy_authority_mem_state = str(((_deploy_authority_mem or {}).get("state")) or "THIN")
                _deploy_authority_mem_n = float(((_deploy_authority_mem or {}).get("trailing_weighted_n")) or ((_deploy_authority_mem or {}).get("trailing_n")) or 0.0)
                _deploy_authority_mem_conf = float(((_deploy_authority_mem or {}).get("confidence")) or 0.0)
                if (
                    _ready == "READY"
                    and _deploy_authority in ("EXECUTABLE", "CONDITIONALLY_READY")
                    and _deploy_authority_mem is not None
                    and (
                        _deploy_authority_mem_state == "FRAGILE"
                        or (_deploy_authority_mem_n >= 4.0 and _deploy_authority_mem_conf < 0.5)
                    )
                ):
                    _ready = "PLANNING_ONLY"
                    _blockers.append("deployment-authority outcome memory is fragile")
                    _candidate_route_bucket = _capital_route_bucket(
                        _mode,
                        _auto_buy,
                        _posture,
                        _ready,
                        _candidate_window_bucket,
                        _headroom_bucket,
                        _regime_bucket,
                    )
                    _candidate_route_mem = capital_route_bucket_memory.get(_candidate_route_bucket)
                    _candidate_marginal_route = _capital_marginal_route(_candidate_route_bucket)
                    _candidate_routing_family = _capital_routing_family(_allocator_stance, _candidate_route_bucket, _candidate_marginal_route)
                    _candidate_routing_family_mem = capital_routing_family_memory.get(_candidate_routing_family)
                    _deploy_authority = _deployment_authority(
                        capital_posture.get("promotion_authority"),
                        capital_posture.get("reinforcement_authority"),
                        _ready,
                        _candidate_route_bucket,
                        _headroom_bucket,
                    )
                    _deploy_authority_mem = deployment_authority_memory.get(_deploy_authority)
                _decision_authority_val = _decision_authority(
                    capital_posture.get("proof_stack_authority"),
                    capital_posture.get("continuation_memory_authority"),
                    capital_posture.get("fresh_discovery_authority"),
                    capital_posture.get("reinforcement_authority"),
                    capital_posture.get("promotion_authority"),
                    _deploy_authority,
                )
                _decision_authority_mem = decision_authority_memory.get(_decision_authority_val)
                return {
                    "capital_ready_state": _ready,
                    "suggested_entry_usd": round(max(_first_leg, 0.0), 2),
                    "max_entry_usd": round(max(_first_leg + _second_leg, 0.0), 2),
                    "staged_scale_usd": _stages,
                    "remaining_cap_usd": round(_remaining_cap, 2),
                    "remaining_slots": _remaining_slots,
                    "effective_per_trade_usd": round(_per_trade, 2),
                    "effective_total_cap_usd": round(_effective_total_cap, 2),
                    "effective_cap_multiplier": round(_effective_mult, 3),
                    "deployment_blockers": [] if _ready == "READY" else _blockers,
                    "capital_pressure_bucket": _pressure_bucket,
                    "capital_pressure_note": _pressure_note,
                    "capital_pressure_memory": _pressure_mem,
                    "capital_regime_bucket": _regime_bucket,
                    "capital_regime_note": _regime_note,
                    "capital_mix_bucket": _mix_bucket,
                    "capital_mix_note": _mix_note,
                    "capital_mix_memory": _mix_mem,
                    "capital_allocator_stance": _allocator_stance,
                    "capital_allocator_note": _allocator_note,
                    "capital_allocator_stance_memory": _allocator_stance_mem,
                    "capital_marginal_route": _candidate_marginal_route,
                    "capital_routing_family": _candidate_routing_family,
                    "capital_routing_family_memory": _candidate_routing_family_mem,
                    "capital_headroom_bucket": _headroom_bucket,
                    "capital_headroom_note": _headroom_note,
                    "capital_headroom_memory": _headroom_mem,
                    "deployment_authority": _deploy_authority,
                    "deployment_authority_memory": _deploy_authority_mem,
                    "decision_authority": _decision_authority_val,
                    "decision_authority_memory": _decision_authority_mem,
                    "capital_route_bucket": _candidate_route_bucket,
                    "capital_route_memory": _candidate_route_mem,
                    "capital_window_bucket": _candidate_window_bucket,
                    "capital_window_note": _window_note,
                    "capital_window_memory": _candidate_window_mem,
                    "capital_intensity_bucket": _intensity_bucket,
                    "capital_intensity_bucket_memory": _intensity_mem,
                    "capital_deployment_family": _family,
                    "capital_deployment_family_memory": _family_mem,
                }

            _family = _capital_deployment_family(_posture, "PLANNING_ONLY", _pressure_bucket, _regime_bucket)
            _deploy_authority = _deployment_authority(
                capital_posture.get("promotion_authority"),
                capital_posture.get("reinforcement_authority"),
                "PLANNING_ONLY",
                "LOCKED",
                _headroom_bucket,
            )
            _deploy_authority_mem = deployment_authority_memory.get(_deploy_authority)
            _decision_authority_val = _decision_authority(
                capital_posture.get("proof_stack_authority"),
                capital_posture.get("continuation_memory_authority"),
                capital_posture.get("fresh_discovery_authority"),
                capital_posture.get("reinforcement_authority"),
                capital_posture.get("promotion_authority"),
                _deploy_authority,
            )
            _decision_authority_mem = decision_authority_memory.get(_decision_authority_val)
            return {
                "capital_ready_state": "PLANNING_ONLY",
                "suggested_entry_usd": 0.0,
                "max_entry_usd": 0.0,
                "staged_scale_usd": [],
                "remaining_cap_usd": round(_remaining_cap, 2),
                "remaining_slots": _remaining_slots,
                "effective_per_trade_usd": round(_per_trade, 2),
                "effective_total_cap_usd": round(_effective_total_cap, 2),
                "effective_cap_multiplier": round(_effective_mult, 3),
                "deployment_blockers": _blockers,
                "capital_pressure_bucket": _pressure_bucket,
                "capital_pressure_note": _pressure_note,
                "capital_pressure_memory": _pressure_mem,
                "capital_regime_bucket": _regime_bucket,
                "capital_regime_note": _regime_note,
                "capital_mix_bucket": _mix_bucket,
                "capital_mix_note": _mix_note,
                "capital_mix_memory": _mix_mem,
                "capital_allocator_stance": _allocator_stance,
                "capital_allocator_note": _allocator_note,
                "capital_allocator_stance_memory": _allocator_stance_mem,
                "capital_marginal_route": _marginal_route,
                "capital_routing_family": _routing_family,
                "capital_routing_family_memory": _routing_family_mem,
                "capital_headroom_bucket": _headroom_bucket,
                "capital_headroom_note": _headroom_note,
                "capital_headroom_memory": _headroom_mem,
                "deployment_authority": _deploy_authority,
                "deployment_authority_memory": _deploy_authority_mem,
                "decision_authority": _decision_authority_val,
                "decision_authority_memory": _decision_authority_mem,
                "capital_route_bucket": "LOCKED",
                "capital_route_memory": capital_route_bucket_memory.get("LOCKED"),
                "capital_window_bucket": _window_bucket,
                "capital_window_note": _window_note,
                "capital_window_memory": _window_mem,
                "capital_intensity_bucket": "ZERO",
                "capital_intensity_bucket_memory": capital_intensity_bucket_memory.get("ZERO"),
                "capital_deployment_family": _family,
                "capital_deployment_family_memory": capital_deployment_family_memory.get(_family),
            }

        # Patch 306: prevent DISTRUST from being the primary NBA candidate when a
        # non-DISTRUST lc_pass candidate is available.  `best` was chosen by the
        # perf_rank sort before trust could be evaluated; this post-sort step checks
        # the selected token's trust and substitutes the first non-DISTRUST alternative
        # when the market is lc-only (full_pass empty) so the operator never sees a
        # primary card that reads WATCH / DISTRUST / DO_NOT_TOUCH.
        if not full_pass and best and lc_pass_list:
            _bt306 = _compute_trust_cached(
                best["symbol"],
                best["setup_status"],
                best.get("continuation_memory"),
                best.get("continuation_memory_source"),
                best.get("continuation_archetype"),
                best.get("reinforcement_bucket"),
            )
            if _bt306["trust_label"] == "DISTRUST":
                _alt306 = next(
                    (c for c in lc_pass_list
                     if c["symbol"] != best["symbol"] and
                     _compute_trust_cached(
                         c["symbol"],
                         c["setup_status"],
                         c.get("continuation_memory"),
                         c.get("continuation_memory_source"),
                         c.get("continuation_archetype"),
                         c.get("reinforcement_bucket"),
                     )["trust_label"] != "DISTRUST"),
                    None
                )
                if _alt306:
                    best = _alt306

        # Keep runner_up consistent with the final winner. Patch 314:
        # post-sort best substitution (for example replacing a DISTRUST winner)
        # can otherwise leave runner_up pointing to the same symbol as best.
        if best:
            _runner_pool = [
                c for c in (full_pass + lc_pass_list)
                if c.get("symbol") != best.get("symbol")
            ]
            runner_up = _runner_pool[0] if _runner_pool else None
        else:
            runner_up = None

        def _triage_cand(c_raw: dict, trust: dict, cp: dict) -> tuple[str, str]:
            """Patch 299 — deterministic triage classification.
            Returns (triage_state, triage_reason) using only existing candidate
            fields + pre-computed trust + correction performance dicts.
            Rule priority: DO_NOT_TOUCH hard exclusions first, then positive
            classification, then soft BLOCKED states.
            """
            trust_label = trust["trust_label"]
            trust_caut  = trust["trust_caution"]
            perf_tier   = c_raw.get("perf_tier", "UNPROVEN")
            perf_n      = c_raw.get("perf_n", 0)
            corr_tier   = cp.get("tier", "UNRATED")
            corr_avg    = cp.get("avg")
            mem         = c_raw.get("continuation_memory") or {}
            mem_state   = str(mem.get("state") or "THIN")
            mem_source  = str(c_raw.get("continuation_memory_source") or "EXACT_SETUP")
            mem_precise = mem_source in ("EXACT_SETUP", "PROFILE_FRESH_REINFORCED_ARCHETYPE", "PROFILE_REINFORCED_ARCHETYPE", "PROFILE_ARCHETYPE", "PROFILE_FUEL_PHASE")
            reinforcement_bucket = str(c_raw.get("reinforcement_bucket") or "PLAIN")
            entry_window = str(c_raw.get("entry_window") or "")
            move_phase   = str(c_raw.get("move_phase") or "")
            fresh_qualified = bool(c_raw.get("fresh_qualified"))
            freshness_bucket = str(c_raw.get("freshness_bucket") or "UNKNOWN")
            mem_authority = _continuation_memory_authority(
                mem,
                mem_source,
                reinforcement_bucket,
                freshness_bucket,
            )
            fresh_mem = c_raw.get("freshness_bucket_memory") or freshness_bucket_memory.get(freshness_bucket)
            catalyst_bucket = str(c_raw.get("fresh_catalyst_bucket") or "UNKNOWN")
            catalyst_mem = c_raw.get("fresh_catalyst_bucket_memory") or fresh_catalyst_bucket_memory.get(catalyst_bucket)
            fresh_discovery_authority = _fresh_discovery_authority(
                fresh_mem,
                catalyst_mem,
                freshness_bucket,
                catalyst_bucket,
            )
            fresh_mem_state = str(((fresh_mem or {}).get("state")) or "THIN")
            fresh_mem_conf = str(((fresh_mem or {}).get("confidence")) or "LOW")
            validation_support = _validation_support_snapshot()
            proof_stack_authority = str(
                validation_support.get("proof_stack_authority")
                or _proof_stack_authority(validation_support)
            )
            reinforcement_authority = _reinforcement_authority(
                c_raw.get("support_overlap_score"),
                reinforcement_bucket,
                proof_stack_authority,
            )
            promotion_authority = _continuation_promotion_authority(
                proof_stack_authority,
                mem_authority,
                reinforcement_bucket,
                fresh_discovery_authority,
            )

            # ── Priority 1–4: hard exclusions ─────────────────────────────────
            if trust_label == "DISTRUST":
                _reason = trust_caut[0] if trust_caut else "multiple negative signals"
                return "DO_NOT_TOUCH", _reason

            if perf_tier == "PROVEN_NEGATIVE":
                _sp   = _sym_perf.get(c_raw["symbol"], {})
                _avg  = _sp.get("avg")
                _sign = "+" if (_avg or 0) >= 0 else ""
                return "DO_NOT_TOUCH", f"proven negative — avg {_sign}{_avg or '?'}% all-time (n={perf_n})"

            if corr_tier == "CASUALTY":
                _sign = "+" if (corr_avg or 0) >= 0 else ""
                return "DO_NOT_TOUCH", f"correction casualty — avg {_sign}{corr_avg or '?'}% in current phase"

            if c_raw.get("hard_block"):
                _hard = next((ch for ch in c_raw.get("chain", []) if ch.get("status") == "FAIL"), None)
                return "DO_NOT_TOUCH", _hard["detail"] if _hard else "hard blocker"

            if mem_state == "FRAGILE":
                if mem_source == "PROFILE_PHASE":
                    return "BLOCKED", "broad phase memory fragile — similar continuation shapes have degraded recently"
                return "BLOCKED", "setup memory fragile — similar continuation shapes have degraded recently"
            if mem_authority == "ADVERSE":
                return "BLOCKED", "memory authority adverse — similar continuation setups are not earning trust"
            if fresh_qualified and fresh_discovery_authority == "ADVERSE":
                return "BLOCKED", "fresh discovery authority adverse — safe-fresh catalyst quality is not earning trust"
            if promotion_authority == "BLOCKED":
                return "BLOCKED", "decision authority blocked — proof and continuation stack are not aligned enough yet"
            if reinforcement_authority == "ABSENT" and reinforcement_bucket in ("REINFORCED", "STRONG_REINFORCED"):
                return "BLOCKED", "reinforcement authority absent — cross-lane overlap has not earned trust yet"

            # ── Priority 5: all conviction gates pass + decent trust ───────────
            if c_raw.get("all_pass") and trust_label in ("HIGH_TRUST", "CONDITIONAL_TRUST"):
                if promotion_authority == "STACKED":
                    return "INVESTIGATE_NOW", f"{trust['trust_summary']} — promotion authority stacked"
                if promotion_authority == "SUPPORTED" and mem_authority in ("FORCEFUL", "BACKED"):
                    return "INVESTIGATE_NOW", f"{trust['trust_summary']} — promotion authority supported"
                if reinforcement_authority == "FORCEFUL":
                    return "INVESTIGATE_NOW", f"{trust['trust_summary']} — reinforcement authority forceful"
                if mem_authority == "FORCEFUL":
                    return "INVESTIGATE_NOW", f"{trust['trust_summary']} — memory authority forceful"
                if mem_state == "COMPOUNDING":
                    return "INVESTIGATE_NOW", f"{trust['trust_summary']} — setup memory compounding"
                return "INVESTIGATE_NOW", trust["trust_summary"]

            # ── Priority 6: lifecycle pass, no scored signal, decent trust ─────
            if c_raw.get("lc_pass") and trust_label in ("HIGH_TRUST", "CONDITIONAL_TRUST"):
                if (
                    fresh_qualified
                    and freshness_bucket == "RECENT_REACTIVATION"
                    and fresh_mem is not None
                    and fresh_mem_state == "COMPOUNDING"
                    and fresh_mem_conf in ("HIGH", "MEDIUM")
                    and fresh_discovery_authority in ("FORCEFUL", "BACKED")
                    and promotion_authority in ("STACKED", "SUPPORTED")
                    and entry_window == "OPEN"
                    and move_phase in ("RELOAD", "MID", "EARLY")
                ):
                    if reinforcement_bucket == "STRONG_REINFORCED":
                        return "INVESTIGATE_NOW", "recent-reactivation freshness is compounding with strong cross-lane reinforcement"
                    if reinforcement_bucket == "REINFORCED":
                        return "INVESTIGATE_NOW", "recent-reactivation freshness is compounding with cross-lane support"
                    return "INVESTIGATE_NOW", "recent-reactivation freshness is compounding"
                if (
                    mem_authority in ("FORCEFUL", "BACKED")
                    and promotion_authority in ("STACKED", "SUPPORTED")
                    and mem_precise
                    and entry_window == "OPEN"
                    and move_phase in ("RELOAD", "MID", "EARLY")
                ):
                    _prefix = "fresh-qualified continuation" if fresh_qualified else "lifecycle continuation"
                    if mem_authority == "FORCEFUL":
                        return "INVESTIGATE_NOW", f"{_prefix} backed by forceful continuation memory"
                    if reinforcement_bucket == "STRONG_REINFORCED":
                        return "INVESTIGATE_NOW", f"{_prefix} backed by reinforced compounding setup memory"
                    if reinforcement_bucket == "REINFORCED":
                        return "INVESTIGATE_NOW", f"{_prefix} backed by cross-confirmed setup memory"
                    return "INVESTIGATE_NOW", f"{_prefix} backed by compounding setup memory"
                if (
                    mem_state == "COMPOUNDING"
                    and not mem_precise
                    and entry_window == "OPEN"
                    and move_phase in ("RELOAD", "MID", "EARLY")
                ):
                    return "MONITOR", "broad continuation memory positive — await tighter setup confirmation"
                return "MONITOR", "lifecycle gates pass \u2014 awaiting scored signal"

            # ── Priority 7: all_pass but trust too thin ────────────────────────
            if c_raw.get("all_pass") and trust_label == "LOW_TRUST":
                _caut = trust_caut[0] if trust_caut else "insufficient history"
                return "BLOCKED", f"trust thin \u2014 {_caut}"

            # ── Priority 8: lifecycle only, any remaining trust state ──────────
            if c_raw.get("lc_pass"):
                if (
                    fresh_qualified
                    and freshness_bucket == "RECENT_REACTIVATION"
                    and fresh_mem is not None
                    and fresh_mem_state == "FRAGILE"
                ):
                    return "BLOCKED", "recent-reactivation freshness memory is fragile — wait for cleaner continuation confirmation"
                if mem_state == "MIXED" and fresh_qualified and entry_window == "OPEN":
                    return "MONITOR", "fresh-qualified continuation — setup memory mixed, keep under watch"
                if fresh_qualified and fresh_discovery_authority == "TENTATIVE" and entry_window == "OPEN":
                    return "MONITOR", "fresh-qualified continuation — safe-fresh catalyst still tentative"
                if reinforcement_authority == "TENTATIVE" and reinforcement_bucket in ("REINFORCED", "STRONG_REINFORCED") and entry_window == "OPEN":
                    return "MONITOR", "cross-lane overlap visible — reinforcement authority still tentative"
                if promotion_authority == "TENTATIVE" and entry_window == "OPEN":
                    return "MONITOR", "continuation shape visible — promotion authority still tentative"
                _caut = trust_caut[0] if trust_caut else "trust thin"
                return "BLOCKED", f"lifecycle only \u2014 {_caut}"

            # ── Priority 9: scored signal present but gate(s) failed ───────────
            if c_raw.get("score") is not None and c_raw.get("failed"):
                _f = c_raw["failed"][0]
                return "BLOCKED", _f.replace("_", " ").lower()

            return "BLOCKED", "insufficient signals"

        def _fmt_cand(c: dict | None) -> dict | None:
            if c is None:
                return None
            _trust  = _compute_trust_cached(
                c["symbol"],
                c["setup_status"],
                c.get("continuation_memory"),
                c.get("continuation_memory_source"),
                c.get("continuation_archetype"),
                c.get("reinforcement_bucket"),
                c.get("support_overlap_score"),
            )
            _cp_c   = _corr_perf.get(c["symbol"], {})
            _triage = _triage_cand(c, _trust, _cp_c)   # compute once (Patch 301 fix)
            _capital = _capital_posture_for_candidate(c, _trust, _triage)
            _mem_authority = _continuation_memory_authority(
                c.get("continuation_memory"),
                c.get("continuation_memory_source"),
                c.get("reinforcement_bucket"),
                c.get("freshness_bucket"),
            )
            _fresh_discovery_authority_value = _fresh_discovery_authority(
                c.get("freshness_bucket_memory"),
                c.get("fresh_catalyst_bucket_memory"),
                c.get("freshness_bucket"),
                c.get("fresh_catalyst_bucket"),
            )
            _proof_auth = str(
                (_validation_support_snapshot().get("proof_stack_authority"))
                or _proof_stack_authority(_validation_support_snapshot())
            )
            _reinforcement_authority_value = globals()["_reinforcement_authority"](
                c.get("support_overlap_score"),
                c.get("reinforcement_bucket"),
                _proof_auth,
            )
            _promotion_authority = _continuation_promotion_authority(
                _proof_auth,
                _mem_authority,
                c.get("reinforcement_bucket"),
                _fresh_discovery_authority_value,
            )
            c["trust_label"] = _trust["trust_label"]
            _capital_plan = _capital_plan_for_candidate(c, _capital)
            _decision_authority_val = _capital_plan.get("decision_authority")
            if c.get("fresh_qualified"):
                _focus_context = "FRESH_QUALIFIED"
            else:
                _focus_context = (
                    "FALLBACK_CONTEXT" if c.get("lc_only")
                    else "FOCUS_UNIVERSE" if c.get("symbol") in _eligible_syms
                    else "EXCLUDED_CONTEXT"
                )
            _in_eu = c.get("symbol") in _eligible_syms
            _focus_note = (
                "fresh-qualified continuation candidate above safety floor"
                if _focus_context == "FRESH_QUALIFIED"
                else "selected from curated eligible universe"
                if _focus_context == "FOCUS_UNIVERSE"
                else (
                    "lifecycle recommendation — symbol is in the eligible universe; scanner signal awaited"
                    if _in_eu
                    else "lifecycle fallback context — not from live eligible universe"
                )
                if _focus_context == "FALLBACK_CONTEXT"
                else "outside focus universe"
            )

            _persist_scanner_labels(c, _trust["trust_label"], _triage[0])

            return {
                "symbol":              c["symbol"],
                "mint":                c["mint"],
                "score":               c["score"],
                "entry_window":        c["entry_window"],
                "fuel_quality":        c["fuel_quality"],
                "move_phase":          c["move_phase"],
                "continuation_archetype": c.get("continuation_archetype"),
                "first_leg_confirmed": c["first_leg_confirmed"],
                "setup_status":        c["setup_status"],
                "continuation_memory": c.get("continuation_memory"),
                "continuation_memory_source": c.get("continuation_memory_source", "EXACT_SETUP"),
                "continuation_memory_authority": _mem_authority,
                "fresh_discovery_authority": _fresh_discovery_authority_value,
                "proof_stack_authority": _proof_auth,
                "reinforcement_authority": _reinforcement_authority_value,
                "promotion_authority": _promotion_authority,
                "decision_authority": _decision_authority_val,
                "reinforcement_bucket": c.get("reinforcement_bucket", "PLAIN"),
                "lc_only":             c["lc_only"],
                "conviction_chain":    c["chain"],
                "perf_tier":           c.get("perf_tier", "UNPROVEN"),
                "perf_n":              c.get("perf_n", 0),
                "avg_return":          _sym_perf.get(c["symbol"], {}).get("avg"),  # Patch 293
                "correction_tier":     _cp_c.get("tier", "UNRATED"),               # Patch 296
                "correction_avg":      _cp_c.get("avg"),                            # Patch 296
                "correction_n":        int(_cp_c.get("n", 0)),                      # Patch 296
                "trust_label":         _trust["trust_label"],                       # Patch 296
                "trust_positive":      _trust["trust_positive"],                    # Patch 296
                "trust_caution":       _trust["trust_caution"],                     # Patch 296
                "trust_upgrade":       _trust["trust_upgrade"],                     # Patch 296
                "trust_summary":       _trust["trust_summary"],                     # Patch 296
                "triage_state":        _triage[0],                                  # Patch 299
                "triage_reason":       _triage[1],                                  # Patch 299
                "capital_posture":     _capital["capital_posture"],
                "capital_band":        _capital["capital_band"],
                "capital_guidance":    _capital["guidance"],
                "capital_rationale":   _capital["why"],
                "capital_posture_memory": _capital.get("capital_posture_memory"),
                "capital_ready_state": _capital_plan["capital_ready_state"],
                "capital_suggested_entry_usd": _capital_plan["suggested_entry_usd"],
                "capital_max_entry_usd": _capital_plan["max_entry_usd"],
                "capital_staged_scale_usd": _capital_plan["staged_scale_usd"],
                "capital_remaining_cap_usd": _capital_plan["remaining_cap_usd"],
                "capital_remaining_slots": _capital_plan["remaining_slots"],
                "capital_effective_per_trade_usd": _capital_plan.get("effective_per_trade_usd"),
                "capital_effective_total_cap_usd": _capital_plan.get("effective_total_cap_usd"),
                "capital_effective_cap_multiplier": _capital_plan.get("effective_cap_multiplier"),
                "capital_deployment_blockers": _capital_plan["deployment_blockers"],
                "capital_pressure_bucket": _capital_plan["capital_pressure_bucket"],
                "capital_pressure_note": _capital_plan["capital_pressure_note"],
                "capital_pressure_memory": _capital_plan.get("capital_pressure_memory"),
                "capital_regime_bucket": _capital_plan.get("capital_regime_bucket"),
                "capital_regime_note": _capital_plan.get("capital_regime_note"),
                "capital_mix_bucket": _capital_plan.get("capital_mix_bucket"),
                "capital_mix_note": _capital_plan.get("capital_mix_note"),
                "capital_mix_memory": _capital_plan.get("capital_mix_memory"),
                "capital_allocator_stance": _capital_plan.get("capital_allocator_stance"),
                "capital_allocator_note": _capital_plan.get("capital_allocator_note"),
                "capital_allocator_stance_memory": _capital_plan.get("capital_allocator_stance_memory"),
                "deployment_authority": _capital_plan.get("deployment_authority"),
                "deployment_authority_memory": _capital_plan.get("deployment_authority_memory"),
                "capital_marginal_route": _capital_plan.get("capital_marginal_route"),
                "capital_routing_family": _capital_plan.get("capital_routing_family"),
                "capital_routing_family_memory": _capital_plan.get("capital_routing_family_memory"),
                "capital_headroom_bucket": _capital_plan.get("capital_headroom_bucket"),
                "capital_headroom_note": _capital_plan.get("capital_headroom_note"),
                "capital_headroom_memory": _capital_plan.get("capital_headroom_memory"),
                "capital_route_bucket": _capital_plan.get("capital_route_bucket"),
                "capital_route_memory": _capital_plan.get("capital_route_memory"),
                "capital_window_bucket": _capital_plan.get("capital_window_bucket"),
                "capital_window_note": _capital_plan.get("capital_window_note"),
                "capital_window_memory": _capital_plan.get("capital_window_memory"),
                "capital_intensity_bucket": _capital_plan.get("capital_intensity_bucket"),
                "capital_intensity_bucket_memory": _capital_plan.get("capital_intensity_bucket_memory"),
                "capital_deployment_family": _capital_plan.get("capital_deployment_family"),
                "capital_deployment_family_memory": _capital_plan.get("capital_deployment_family_memory"),
                "coin_quality":        c.get("coin_quality"),                       # Patch 311
                "quality_caution":     c.get("quality_caution", ""),                # Patch 311
                "focus_context":       _focus_context,
                "focus_note":          _focus_note,
                "fresh_qualified":     bool(c.get("fresh_qualified")),
                "fresh_catalyst_bucket": c.get("fresh_catalyst_bucket"),
                "fresh_catalyst_bucket_memory": c.get("fresh_catalyst_bucket_memory"),
                "freshness_bucket":    c.get("freshness_bucket"),
                "freshness_bucket_memory": c.get("freshness_bucket_memory"),
                "candidate_origin":    c.get("candidate_origin", "PRIMARY"),
                "support_overlap_score": round(float(c.get("support_overlap_score") or 0.0), 2),
                "support_overlap_tags": list(c.get("support_overlap_tags") or []),
                "support_overlap_detail": list(c.get("support_overlap_detail") or []),
            }

        def _persist_scanner_labels(candidate: dict, trust_label: str, triage_state: str) -> None:
            """Persist labels to the most specific scanner row available."""
            _symbol = str(candidate.get("symbol") or "").strip().upper()
            if not _symbol:
                return

            _mint = str(candidate.get("mint") or "").strip()
            _scanned_at = str(candidate.get("scanned_at") or candidate.get("last_seen_at") or "").strip()

            try:
                if _mint and _scanned_at:
                    conn.execute("""
                        UPDATE memecoin_signal_outcomes
                        SET    trust_label = ?,
                               triage_state = ?,
                               labeled_at  = COALESCE(labeled_at, datetime('now'))
                        WHERE  id = (
                            SELECT id FROM memecoin_signal_outcomes
                            WHERE  symbol = ? AND source = 'SCANNER'
                              AND mint = ? AND scanned_at = ?
                            ORDER BY id DESC LIMIT 1
                        )
                    """, (trust_label, triage_state, _symbol, _mint, _scanned_at))
                    return

                if _mint:
                    conn.execute("""
                        UPDATE memecoin_signal_outcomes
                        SET    trust_label = ?,
                               triage_state = ?,
                               labeled_at  = COALESCE(labeled_at, datetime('now'))
                        WHERE  id = (
                            SELECT id FROM memecoin_signal_outcomes
                            WHERE  symbol = ? AND source = 'SCANNER'
                              AND mint = ?
                            ORDER BY scanned_at DESC, id DESC LIMIT 1
                        )
                    """, (trust_label, triage_state, _symbol, _mint))
                    return

                conn.execute("""
                    UPDATE memecoin_signal_outcomes
                    SET    trust_label = ?,
                           triage_state = ?,
                           labeled_at  = COALESCE(labeled_at, datetime('now'))
                    WHERE  id = (
                        SELECT id FROM memecoin_signal_outcomes
                        WHERE  symbol = ? AND source = 'SCANNER'
                        ORDER BY scanned_at DESC, id DESC LIMIT 1
                    )
                """, (trust_label, triage_state, _symbol))
            except Exception:
                pass

        def _fresh_qualified_pool_rows() -> list[dict]:
            from datetime import timedelta
            _cut_14d = (now - timedelta(days=14)).strftime("%Y-%m-%d %H:%M:%S")
            _cut_72h = (now - timedelta(hours=72)).strftime("%Y-%m-%d %H:%M:%S")
            _fq_rows = conn.execute("""
                SELECT symbol, COUNT(*) AS cnt
                FROM memecoin_signal_outcomes
                WHERE scanned_at >= ?
                GROUP BY symbol
            """, (_cut_72h,)).fetchall()
            _fq_appear = {str(r["symbol"]).upper(): int(r["cnt"] or 0) for r in _fq_rows if r["symbol"]}
            _rows = conn.execute("""
                SELECT
                    c.symbol, c.mint, c.scanned_at, c.source, c.score,
                    c.mcap_at_scan, c.liquidity_usd, c.volume_24h,
                    c.vol_acceleration, c.top_holder_pct, c.rug_label,
                    c.attention_quality, c.attention_infrastructure, c.boost_active,
                    lc.lifecycle_state, lc.first_leg_confirmed, lc.n_windows,
                    lc.first_seen_at, lc.state_entered_at,
                    lc.entry_window, lc.fuel_quality, lc.move_phase
                FROM (
                    SELECT m1.*
                    FROM memecoin_signal_outcomes m1
                    INNER JOIN (
                        SELECT symbol, MAX(scanned_at) AS max_scanned_at
                        FROM memecoin_signal_outcomes
                        WHERE source IN ('DISCOVERY', 'SCANNER')
                          AND scanned_at >= ?
                          AND mcap_at_scan >= ?
                          AND liquidity_usd >= ?
                        GROUP BY symbol
                    ) latest
                      ON latest.symbol = m1.symbol
                     AND latest.max_scanned_at = m1.scanned_at
                    WHERE m1.source IN ('DISCOVERY', 'SCANNER')
                ) c
                LEFT JOIN symbol_lifecycle lc ON lc.symbol = c.symbol
                ORDER BY c.scanned_at DESC
                LIMIT 120
            """, (_cut_14d, _MCAP_FLOOR, 50_000)).fetchall()

            out: list[dict] = []
            for _r in _rows:
                _rec = dict(_r)
                _sym = str(_rec.get("symbol") or "").upper()
                if not _sym:
                    continue
                _mcap = float(_rec.get("mcap_at_scan") or 0.0)
                _liq = float(_rec.get("liquidity_usd") or 0.0)
                _top1 = float(_rec.get("top_holder_pct") or 0.0)
                _lc_state = str(_rec.get("lifecycle_state") or "")
                _flc = int(_rec.get("first_leg_confirmed") or 0)
                _nwin = int(_rec.get("n_windows") or 0)
                _appear = _fq_appear.get(_sym, 0)
                _first_seen = _rec.get("first_seen_at") or _rec.get("scanned_at")
                _state_entered = _rec.get("state_entered_at") or _rec.get("scanned_at")
                _hours_first_seen = None
                _hours_state_entered = None
                try:
                    if _first_seen:
                        _hours_first_seen = max(
                            0.0,
                            (now - datetime.strptime(str(_first_seen)[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)).total_seconds() / 3600.0,
                        )
                except Exception:
                    _hours_first_seen = None
                try:
                    if _state_entered:
                        _hours_state_entered = max(
                            0.0,
                            (now - datetime.strptime(str(_state_entered)[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)).total_seconds() / 3600.0,
                        )
                except Exception:
                    _hours_state_entered = None
                _cq, _cq_note = _coin_quality_verdict({
                    "rug_label": _rec.get("rug_label"),
                    "top_holder_pct": _top1,
                    "top5_holder_pct": 0.0,
                    "liquidity_usd": _liq,
                    "volume_24h": float(_rec.get("volume_24h") or 0.0),
                    "token_age_days": 0.0,
                    "holder_quality_level": "",
                })
                if _mcap < _MCAP_FLOOR or _liq < 50_000 or _top1 > 20.0:
                    continue
                if _cq == "WEAK" or _lc_state == "DEAD":
                    continue
                if _sym_perf.get(_sym, {}).get("tier") == "PROVEN_NEGATIVE":
                    continue
                if _corr_perf.get(_sym, {}).get("tier") == "CASUALTY":
                    continue
                _ew = _rec.get("entry_window") or ""
                _fq = _rec.get("fuel_quality") or ""
                _mp = _rec.get("move_phase") or ""
                _arch = _continuation_archetype(_ew, _fq, _mp)
                _continuation = (
                    _flc == 1
                    or _nwin >= 2
                    or _lc_state in ("RELOAD", "REVIVAL")
                    or _mp == "RELOAD"
                )
                if not _continuation:
                    continue
                if (
                    _hours_first_seen is not None
                    and _hours_first_seen < 48.0
                    and _flc == 0
                    and _nwin < 2
                ):
                    continue
                _recent_reactivation = (
                    _hours_state_entered is not None
                    and _hours_state_entered <= 168.0
                    and _lc_state in ("RELOAD", "REVIVAL", "ACTIVE")
                )
                _freshness_bucket = (
                    "RECENT_REACTIVATION"
                    if _recent_reactivation and (_hours_first_seen or 0) > 168.0
                    else "NEWLY_QUALIFIED"
                )
                _fresh_catalyst_kind = _fresh_catalyst_bucket(
                    _lc_state,
                    False,
                    _appear,
                    _hours_state_entered,
                    None,
                )
                _fresh_enough = (
                    _hours_first_seen is None
                    or _hours_first_seen <= 168.0
                    or _recent_reactivation
                )
                if not _fresh_enough:
                    continue
                _setup = ledger_status.get((_ew, _fq, _mp), "INSUFFICIENT_DATA") if (_ew and _fq and _mp) else "INSUFFICIENT_DATA"
                _lcp = (_ew == "OPEN" and _fq in ("STRONG", "MODERATE") and _mp not in ("EXTENDED", "CHURN") and (_flc == 1 or _nwin >= 2))
                _repeat_loss = bool(_rec.get("mint") and _rec.get("mint") in loss_mints)
                if _repeat_loss:
                    continue
                _mem_key = (_ew, _fq, _mp)
                _mem_exact = setup_memory.get(_mem_key, {
                        "state": "THIN",
                        "trailing_n": 0,
                        "trailing_wr": None,
                        "trailing_avg": None,
                        "cat_rate": None,
                        "lifetime_n": 0,
                        "lifetime_wr": None,
                        "lifetime_avg": None,
                    })
                _mem_source = "EXACT_SETUP"
                _reinforcement_bucket = _support_reinforcement_bucket(
                    _rec.get("support_overlap_score"),
                    _rec.get("support_overlap_tags"),
                )
                _mem_candidates = [
                    ("PROFILE_FRESH_REINFORCED_ARCHETYPE", fresh_reinforced_archetype_memory.get((_arch, _reinforcement_bucket, _freshness_bucket))),
                    ("PROFILE_REINFORCED_ARCHETYPE", reinforced_archetype_memory.get((_arch, _reinforcement_bucket))),
                    ("PROFILE_ARCHETYPE", archetype_memory.get((_arch,))),
                    ("PROFILE_FUEL_PHASE", profile_memory.get((_fq, _mp))),
                    ("PROFILE_WINDOW_PHASE", window_phase_memory.get((_ew, _mp))),
                    ("PROFILE_PHASE", phase_memory.get((_mp,))),
                ]
                _mem_exact, _mem_source = _choose_continuation_memory(_mem_exact, _mem_candidates)
                _fresh_catalyst_memory = fresh_catalyst_bucket_memory.get(_fresh_catalyst_kind)
                out.append({
                    "symbol": _sym,
                    "mint": _rec.get("mint"),
                    "scanned_at": _rec.get("scanned_at"),
                    "score": round(float(_rec.get("score") or 0.0), 1) if _rec.get("score") is not None else None,
                    "entry_window": _ew,
                    "fuel_quality": _fq,
                    "move_phase": _mp,
                    "continuation_archetype": _arch,
                    "first_leg_confirmed": _flc,
                    "setup_status": _setup,
                    "continuation_memory": _mem_exact,
                    "continuation_memory_source": _mem_source,
                    "reinforcement_bucket": _reinforcement_bucket,
                    "lc_only": True,
                    "all_pass": False,
                    "lc_pass": _lcp,
                    "hard_block": False,
                    "chain": [{
                        "name": "FreshQualified",
                        "status": "INFO",
                        "detail": (
                            f"fresh-qualified above $1.5M floor · recent reactivation · {_appear} appearance(s) / 72h"
                            if _freshness_bucket == "RECENT_REACTIVATION"
                            else f"fresh-qualified above $1.5M floor · {_lc_state or 'DISCOVERY'} · {_appear} appearance(s) / 72h"
                        )
                    }],
                    "failed": [],
                    "repeat_loss": False,
                    "perf_tier": _sym_perf.get(_sym, {}).get("tier", "UNPROVEN"),
                    "perf_n": _sym_perf.get(_sym, {}).get("n", 0),
                    "coin_quality": _cq,
                    "quality_caution": _cq_note,
                    "fresh_qualified": True,
                    "fresh_catalyst_bucket": _fresh_catalyst_kind,
                    "fresh_catalyst_bucket_memory": _fresh_catalyst_memory,
                    "freshness_bucket": _freshness_bucket,
                    "freshness_bucket_memory": freshness_bucket_memory.get(_freshness_bucket),
                    "candidate_origin": "FRESH_QUALIFIED",
                })
            return out

        # ── Patch 299: research_pool — full triage-classified candidate set ────
        # Includes: all full_pass + all lc_pass + up to 5 scored-but-blocked.
        # Scored-but-blocked tokens are included so the operator can see what
        # has a signal but is being held back by a specific gate.
        _scored_blocked = sorted(
            [c for c in candidates
             if not c["all_pass"] and not c["lc_pass"]
             and c.get("score") is not None],
            key=lambda x: x["score"] or 0, reverse=True
        )[:5]
        _pool_raw = full_pass + lc_pass_list + _scored_blocked
        _pool_syms = {c["symbol"] for c in _pool_raw}
        _fresh_qualified_raw = [
            c for c in _fresh_qualified_pool_rows()
            if c["symbol"] not in _pool_syms
        ][:4]
        _pool_raw = _pool_raw + _fresh_qualified_raw
        _pool_fmt_all   = [_fmt_cand(c) for c in _pool_raw]
        _research_excluded = [c for c in _pool_fmt_all if c.get("triage_state") == "DO_NOT_TOUCH"]

        def _research_sort_key(c: dict) -> tuple:
            triage_rank = {
                "INVESTIGATE_NOW": 0,
                "MONITOR": 1,
                "BLOCKED": 2,
                "DO_NOT_TOUCH": 3,
            }.get(str(c.get("triage_state") or ""), 9)
            focus_rank = {
                "FRESH_QUALIFIED": 0,
                "FOCUS_UNIVERSE": 1,
                "FALLBACK_CONTEXT": 2,
                "EXCLUDED_CONTEXT": 3,
            }.get(str(c.get("focus_context") or ""), 9)
            freshness_rank = {
                "RECENT_REACTIVATION": 0,
                "NEWLY_QUALIFIED": 1,
            }.get(str(c.get("freshness_bucket") or ""), 9)
            trust_rank = {
                "HIGH_TRUST": 0,
                "CONDITIONAL_TRUST": 1,
                "LOW_TRUST": 2,
                "DISTRUST": 3,
            }.get(str(c.get("trust_label") or ""), 9)
            mem_rank = {
                "COMPOUNDING": 0,
                "MIXED": 1,
                "THIN": 2,
                "FRAGILE": 3,
            }.get(str((c.get("continuation_memory") or {}).get("state") or ""), 9)
            move_rank = {
                "RELOAD": 0,
                "MID": 1,
                "EARLY": 2,
                "IGNITION": 3,
                "EXTENDED": 4,
                "CHURN": 5,
            }.get(str(c.get("move_phase") or ""), 9)
            score = -float(c.get("score") or 0.0)
            return (triage_rank, focus_rank, freshness_rank, trust_rank, mem_rank, move_rank, score)

        research_pool = sorted(
            [c for c in _pool_fmt_all if c.get("triage_state") != "DO_NOT_TOUCH"],
            key=_research_sort_key,
        )
        _best_fmt       = _fmt_cand(best)
        _runner_up_fmt  = _fmt_cand(runner_up)

        # If the best lifecycle candidate is distrusted / do-not-touch, but we
        # already have a safer fresh-qualified continuation candidate above the
        # floor in research_pool, promote that safer fresh-qualified name to the
        # visible primary watch slot. This keeps the system aligned with the
        # continuation-first roadmap instead of anchoring the main card to a
        # known-bad legacy fallback.
        if _best_fmt and _best_fmt.get("triage_state") == "DO_NOT_TOUCH":
            _fq_primary = next(
                (
                    c for c in research_pool
                    if c.get("fresh_qualified")
                    and c.get("triage_state") in ("INVESTIGATE_NOW", "MONITOR", "BLOCKED")
                ),
                None,
            )
            if _fq_primary:
                _best_fmt = _fq_primary
                recommendation = "WATCH"
                confidence = "LOW"
                why_wins = [
                    (
                        "recent-reactivation fresh-qualified continuation candidate preferred over distrusted lifecycle fallback"
                        if _fq_primary.get("freshness_bucket") == "RECENT_REACTIVATION"
                        else "safer fresh-qualified continuation candidate preferred over distrusted lifecycle fallback"
                    )
                ]
                runner_up_loss = []
                if _runner_up_fmt and _runner_up_fmt.get("symbol") == _best_fmt.get("symbol"):
                    _runner_up_fmt = None

        # If the visible winner is only a lifecycle fallback context, but we
        # already have a safer fresh-qualified continuation name above the floor
        # with comparable triage, prefer that fresher qualified candidate as the
        # primary watch slot. This keeps the main card biased toward safer
        # continuation freshness instead of familiar fallback inertia.
        if (
            _best_fmt
            and _best_fmt.get("focus_context") == "FALLBACK_CONTEXT"
            and _best_fmt.get("triage_state") in ("MONITOR", "BLOCKED")
        ):
            _fq_primary = next(
                (
                    c for c in research_pool
                    if c.get("fresh_qualified")
                    and c.get("triage_state") in ("INVESTIGATE_NOW", "MONITOR")
                    and c.get("trust_label") in ("HIGH_TRUST", "CONDITIONAL_TRUST")
                ),
                None,
            )
            if _fq_primary and _fq_primary.get("symbol") != _best_fmt.get("symbol"):
                _best_fmt = _fq_primary
                recommendation = "WATCH"
                confidence = "LOW"
                why_wins = [
                    (
                        "recent-reactivation fresh-qualified continuation candidate preferred over lifecycle fallback context"
                        if _fq_primary.get("freshness_bucket") == "RECENT_REACTIVATION"
                        else "safer fresh-qualified continuation candidate preferred over lifecycle fallback context"
                    )
                ]
                runner_up_loss = []
                if _runner_up_fmt and _runner_up_fmt.get("symbol") == _best_fmt.get("symbol"):
                    _runner_up_fmt = None

        # Patch 314: if the only alternate candidate is already excluded by the
        # system, do not surface it in the runner-up slot. Keep excluded names
        # available in research_excluded/suppressed context instead of giving
        # them a prominent comparison slot beside the winner.
        if _runner_up_fmt and _runner_up_fmt.get("triage_state") == "DO_NOT_TOUCH":
            _runner_up_fmt = None
            runner_up_loss = []
            if not why_wins:
                why_wins = []

        # Patch 302: commit trust_label + triage_state UPDATEs written by _fmt_cand()
        try:
            conn.commit()
        except Exception:
            pass

        # Patch 309: label the most recent SCANNER row for every symbol in
        # symbol_lifecycle that was NOT already covered by _fmt_cand().
        #
        # Root cause of the coverage gap:
        #   _fmt_cand() only labels symbols that appear in _pool_raw
        #   (full_pass + lc_pass_list + _scored_blocked[:5]).  Symbols that are
        #   tracked in symbol_lifecycle but absent from the current scan cache
        #   (or ranked below top-5 in scored_blocked) never receive a label —
        #   their SCANNER rows sit COMPLETE and unlabeled indefinitely.
        #
        # Fix: after the main labeling pass, iterate over ALL lifecycle-tracked
        # symbols and label their most recent SCANNER row using the same trust/
        # triage computation already available in this _run() scope.
        #
        # No look-ahead: labels are computed from current NBA-pipeline data
        # (perf history, correction window, setup ledger) — none of which are
        # future outcomes of the specific row being labeled.  This is identical
        # in principle to what _fmt_cand() already does for visible candidates.
        _pool_syms309 = {c["symbol"] for c in _pool_raw}
        _cand_syms309 = {c["symbol"] for c in candidates}
        _wrote309 = 0

        # Pass A: scored candidates evaluated but not in _pool_raw (scan cache present)
        for _ec in candidates:
            if (_ec["symbol"] in _pool_syms309
                    or _ec.get("score") is None
                    or _ec.get("_lifecycle_only")):
                continue
            try:
                _ec_trust  = _compute_trust_cached(
                    _ec["symbol"],
                    _ec["setup_status"],
                    _ec.get("continuation_memory"),
                    _ec.get("continuation_memory_source"),
                    _ec.get("continuation_archetype"),
                    _ec.get("reinforcement_bucket"),
                )
                _ec_cp_c   = _corr_perf.get(_ec["symbol"], {})
                _ec_triage = _triage_cand(_ec, _ec_trust, _ec_cp_c)
                _persist_scanner_labels(_ec, _ec_trust["trust_label"], _ec_triage[0])
                _wrote309 += 1
            except Exception:
                pass

        # Pass B: lifecycle symbols NOT in candidates at all (scan cache empty or
        # symbol not currently in rotation).  Derive setup_status from ledger_status
        # using the symbol's current lifecycle entry — the same lookup the candidate
        # evaluation loop uses.
        for _sym309, _lc309 in lc_by_symbol.items():
            if _sym309 in _pool_syms309 or _sym309 in _cand_syms309:
                continue  # already handled by _fmt_cand() or Pass A
            try:
                _ew309  = _lc309.get("entry_window", "")
                _fq309  = _lc309.get("fuel_quality",  "")
                _mp309  = _lc309.get("move_phase",     "")
                _flc309 = int(_lc309.get("first_leg_confirmed", 0) or 0)
                _st309  = ledger_status.get((_ew309, _fq309, _mp309), "INSUFFICIENT_DATA")
                # Minimal candidate dict for _triage_cand — lifecycle state only,
                # no scan score available.
                _lcp309 = (
                    _ew309 == "OPEN"
                    and _fq309 in ("STRONG", "MODERATE")
                    and _mp309 not in ("EXTENDED", "CHURN")
                    and _flc309 == 1
                )
                _ec309 = {
                    "symbol":              _sym309,
                    "all_pass":            False,   # no scanner score to confirm full pass
                    "lc_pass":             _lcp309,
                    "hard_block":          False,
                    "entry_window":        _ew309,
                    "fuel_quality":        _fq309,
                    "move_phase":          _mp309,
                    "continuation_archetype": _continuation_archetype(_ew309, _fq309, _mp309),
                    "continuation_memory_source": "PROFILE_ARCHETYPE",
                    "continuation_memory": archetype_memory.get((_continuation_archetype(_ew309, _fq309, _mp309),), {
                        "state": "THIN",
                        "trailing_n": 0,
                        "trailing_weighted_n": 0.0,
                        "trailing_wr": None,
                        "trailing_avg": None,
                        "cat_rate": None,
                        "lifetime_n": 0,
                        "lifetime_weighted_n": 0.0,
                        "lifetime_wr": None,
                        "lifetime_avg": None,
                        "wr_delta": None,
                        "avg_delta": None,
                    }),
                    "first_leg_confirmed": _flc309,
                    "score":               None,
                }
                _t309   = _compute_trust_cached(
                    _sym309,
                    _st309,
                    _ec309.get("continuation_memory"),
                    _ec309.get("continuation_memory_source"),
                    _ec309.get("continuation_archetype"),
                    _ec309.get("reinforcement_bucket"),
                )
                _cp309  = _corr_perf.get(_sym309, {})
                _tr309  = _triage_cand(_ec309, _t309, _cp309)
                _persist_scanner_labels(_ec309, _t309["trust_label"], _tr309[0])
                _wrote309 += 1
            except Exception:
                pass

        if _wrote309:
            try:
                conn.commit()
            except Exception:
                pass

        # Patch 308: evidence_maturity — classify how much of this recommendation
        # rests on validated vs still-accumulating intelligence layers.
        # Layer validation thresholds mirror /home/intel-stack (Patch 307):
        #   Performance Memory  → EARNING_ITS_PLACE when n >= 20 (currently 1574)
        #   Trust Labels        → validated when labeled outcomes >= 20 (currently 4)
        #   Triage States       → same table as trust_labels; same count
        try:
            _ev_n  = int((conn.execute(
                "SELECT COUNT(*) FROM memecoin_signal_outcomes "
                "WHERE status='COMPLETE' AND return_24h_pct IS NOT NULL"
            ).fetchone() or [0])[0])
            # Patch 310: use clean-forward count only.
            # labeled_at IS NOT NULL  → stamped after schema migration (post-Patch 310).
            # 172800s = 48h threshold — label must have been applied within 48h of scan.
            _ev_tl_total = int((conn.execute(
                "SELECT COUNT(*) FROM memecoin_signal_outcomes "
                "WHERE trust_label IS NOT NULL AND status='COMPLETE'"
            ).fetchone() or [0])[0])
            _ev_tl = int((conn.execute(
                "SELECT COUNT(*) FROM memecoin_signal_outcomes "
                "WHERE trust_label IS NOT NULL AND status='COMPLETE' "
                "AND labeled_at IS NOT NULL "
                "AND (julianday(labeled_at) - julianday(scanned_at)) * 86400.0 <= 172800"
            ).fetchone() or [0])[0])
            _perf_proven  = _ev_n  >= 20   # Performance Memory threshold
            _trust_proven = _ev_tl >= 20   # Trust/Triage Labels (clean-forward) threshold
            _val_snapshot = _validation_support_snapshot()
            _tl_sig = str(((_val_snapshot.get("trust_labels") or {}).get("signal")) or "ACCUMULATING")
            _ts_sig = str(((_val_snapshot.get("triage_states") or {}).get("signal")) or "ACCUMULATING")
            _td_sig = str(((_val_snapshot.get("transition_detector") or {}).get("signal")) or "ACCUMULATING")
            _promotive_layers = sum(1 for s in (_tl_sig, _ts_sig, _td_sig) if s == "PROMOTIVE")
            _early_layers = sum(1 for s in (_tl_sig, _ts_sig, _td_sig) if s == "EARLY")
            _adverse_layers = sum(1 for s in (_tl_sig, _ts_sig, _td_sig) if s == "ADVERSE")
            _proof_auth = str(_val_snapshot.get("proof_stack_authority") or _proof_stack_authority(_val_snapshot))

            if _perf_proven and _trust_proven and _proof_auth == "FORCEFUL" and _adverse_layers == 0 and _promotive_layers >= 2:
                _ev_mat  = "PROVEN"
                _ev_note = (
                    f"Performance memory validated ({_ev_n} outcomes) and proof stack aligned "
                    f"({_promotive_layers} promotive validation layer(s), authority={_proof_auth})."
                )
            elif _adverse_layers > 0 or _proof_auth == "ADVERSE":
                _ev_mat = "MIXED"
                _ev_note = (
                    f"Core performance layer {'validated' if _perf_proven else 'still thin'} "
                    f"but { _adverse_layers } validation layer(s) are adverse. "
                    f"Proof authority={_proof_auth}. Treat setup selection with caution."
                )
            elif _perf_proven:
                _cand_trust = (_best_fmt or {}).get("trust_label", "CONDITIONAL_TRUST")
                if _cand_trust in ("DISTRUST", "HIGH_TRUST"):
                    _ev_note = (
                        f"Performance memory validated ({_ev_n} outcomes). "
                        f"Trust signal active ({_cand_trust}) but not yet validated "
                        f"({_ev_tl}/20 clean-labeled, {_ev_tl_total} total)."
                    )
                else:
                    _ev_note = (
                        f"Built mainly on proven performance memory ({_ev_n} outcomes). "
                        f"Trust/triage accumulating clean labels "
                        f"({_ev_tl}/20 clean-labeled, {_ev_tl_total} total bootstrap)."
                    )
                if _promotive_layers or _early_layers:
                    _ev_note += (
                        f" Validation stack: {_promotive_layers} promotive, "
                        f"{_early_layers} early, {_adverse_layers} adverse."
                    )
                _ev_note += f" Proof authority={_proof_auth}."
                _ev_mat = "MIXED"
            elif _early_layers > 0:
                _ev_mat = "EARLY_STACK"
                _ev_note = (
                    f"Core performance layer not yet validated ({_ev_n}/20 outcomes), "
                    f"but {_early_layers} validation layer(s) already show early-positive directional signal "
                    f"(authority={_proof_auth})."
                )
            else:
                _ev_mat  = "EXPERIMENTAL"
                _ev_note = (
                    f"Core performance layer not yet validated ({_ev_n}/20 outcomes). "
                    f"Treat recommendation as early signal only (authority={_proof_auth})."
                )
        except Exception:
            _ev_mat  = "EXPERIMENTAL"
            _ev_note = "Evidence maturity unavailable."
            _proof_auth = "TENTATIVE"

        _capital_context = _memecoin_capital_context()
        _lane_operating = _meme_lane_operating_summary(
            recommendation,
            confidence,
            _proof_auth,
            _capital_context,
            _ev_mat,
        )
        _cand_ops = _meme_candidate_operating_summary(_best_fmt)
        if _best_fmt and _cand_ops:
            _best_fmt = {**_best_fmt, "operating_summary": _cand_ops}
        _ru_ops = _meme_candidate_operating_summary(_runner_up_fmt)
        if _runner_up_fmt and _ru_ops:
            _runner_up_fmt = {**_runner_up_fmt, "operating_summary": _ru_ops}
        return {
            "recommendation":       recommendation,
            "confidence":           confidence,
            "candidate":            _best_fmt,
            "why_wins":             why_wins,
            "upgrade_conditions":   upgrade_conditions,
            "downgrade_conditions": downgrade_conditions,
            "runner_up":            _runner_up_fmt,
            "runner_up_loss_reason": runner_up_loss,
            "blocker_summary":      blocker_summary,
            "blockers":             blockers,
            "signals_evaluated":    len(signals),
            "full_pass_count":      len(full_pass),
            "data_source":          data_source,
            "shape_caution":        shape_caution,
            "global_shape_quality": _global_sq,
            "fg_value":             fg_val,
            "fg_favorable":         fg_favorable,
            "fg_source":            fg_source,
            "quiet_market_intel":   quiet_market_intel,
            "research_pool":        research_pool,           # Patch 299
            "fresh_qualified":      {
                "count": len(_fresh_qualified_raw),
                "symbols": [c["symbol"] for c in _fresh_qualified_raw],
            },
            "research_excluded":    {
                "count":   len(_research_excluded),
                "symbols": [
                    {
                        "symbol":        c["symbol"],
                        "trust_label":   c.get("trust_label"),
                        "triage_reason": c.get("triage_reason"),
                    }
                    for c in _research_excluded[:12]
                ],
                "reason": "excluded from default research focus",
            },
            "capital_context":      _capital_context,
            "evidence_maturity":    _ev_mat,                 # Patch 308
            "evidence_note":        _ev_note,                # Patch 308
            "proof_stack_authority": _proof_auth,
            "lane_operating_summary": _lane_operating,
            "eligible_universe":    {                        # Patch 313
                "count":          len(_eligible_syms),
                "symbols":        sorted(_eligible_syms),
                "excluded_count": len(_eu_excluded),
                "excluded_sample": {
                    k: v for k, v in list(_eu_excluded.items())[:15]
                },
                "gates": {
                    "min_scan_count_30d": _EU_MIN_SCANS,
                    "min_liquidity_usd":  _EU_MIN_LIQ,
                    "mcap_floor":         _MCAP_FLOOR,
                    "max_last_scan_age_days": 14,
                },
            },
            "generated_at":         now_iso,
        }

    try:
        return await asyncio.to_thread(_run)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ── Patch 301: Predictive Validation Layer ────────────────────────────────────

def _validate_perf_tier_forward(conn) -> dict:
    """
    Walk-forward perf-tier validity test.

    For each COMPLETE outcome row, compute the perf_tier that symbol would have
    held using only outcomes PRIOR to that scan date (self-join on scanned_at <
    current).  Group by that prior tier and compute outcome statistics.

    True walk-forward — no look-ahead bias.  Answers: does PROVEN_POSITIVE
    (as classifiable at scan time) predict better future returns than UNPROVEN?
    """
    try:
        rows = conn.execute("""
            WITH completed AS (
                SELECT id, symbol, scanned_at, return_24h_pct
                FROM   memecoin_signal_outcomes
                WHERE  status         = 'COMPLETE'
                  AND  return_24h_pct IS NOT NULL
                  AND  (rug_label IS NULL OR rug_label != 'RUG')
            ),
            prior AS (
                SELECT
                    c1.id,
                    c1.return_24h_pct,
                    COUNT(c2.id)                                                   AS prior_n,
                    AVG(c2.return_24h_pct)                                         AS prior_avg,
                    SUM(CASE WHEN c2.return_24h_pct >= 10 THEN 1.0 ELSE 0.0 END)
                        * 100.0 / NULLIF(COUNT(c2.id), 0)                         AS prior_wr
                FROM completed c1
                LEFT JOIN completed c2
                    ON  c2.symbol     = c1.symbol
                    AND c2.scanned_at < c1.scanned_at
                GROUP BY c1.id, c1.return_24h_pct
            )
            SELECT
                CASE
                    WHEN prior_n >= 15 AND prior_wr >= 50 AND prior_avg >= 5  THEN 'PROVEN_POSITIVE'
                    WHEN prior_n >= 15 AND prior_avg <  -10                   THEN 'PROVEN_NEGATIVE'
                    WHEN prior_n >= 8                                         THEN 'TESTED_NEUTRAL'
                    ELSE                                                           'UNPROVEN'
                END                                                           AS tier,
                COUNT(*)                                                      AS n,
                ROUND(AVG(return_24h_pct),   1)                              AS avg_return,
                ROUND(SUM(CASE WHEN return_24h_pct >= 10 THEN 1.0 ELSE 0.0 END)
                      * 100.0 / COUNT(*), 1)                                 AS wr_10,
                ROUND(SUM(CASE WHEN return_24h_pct >  0  THEN 1.0 ELSE 0.0 END)
                      * 100.0 / COUNT(*), 1)                                 AS wr_0
            FROM prior
            GROUP BY tier
            ORDER BY CASE tier
                WHEN 'PROVEN_POSITIVE' THEN 0
                WHEN 'TESTED_NEUTRAL'  THEN 1
                WHEN 'PROVEN_NEGATIVE' THEN 2
                ELSE                        3
            END
        """).fetchall()

        tier_rows = [
            {
                "tier":       r["tier"],
                "n":          r["n"],
                "avg_return": r["avg_return"],
                "wr_10":      r["wr_10"],
                "wr_0":       r["wr_0"],
            }
            for r in rows
        ]
        total_n = sum(r["n"] for r in tier_rows)

        proven_pos = next((r for r in tier_rows if r["tier"] == "PROVEN_POSITIVE"), None)
        unproven   = next((r for r in tier_rows if r["tier"] == "UNPROVEN"),        None)

        verdict        = "NOT_YET_PROVEN"
        verdict_reason = "no PROVEN_POSITIVE samples — need ≥15 prior outcomes per symbol"

        if proven_pos and unproven:
            if proven_pos["n"] < 10:
                direction      = "correct" if proven_pos["wr_10"] >= unproven["wr_10"] else "wrong"
                verdict        = "NOT_YET_PROVEN"
                verdict_reason = (f"PROVEN_POSITIVE n={proven_pos['n']} — too thin (need ≥10); "
                                  f"direction {direction}")
            elif proven_pos["wr_10"] > unproven["wr_10"] and proven_pos["avg_return"] > unproven["avg_return"]:
                lift           = round(proven_pos["wr_10"] - unproven["wr_10"], 1)
                verdict        = "EARNING_ITS_PLACE"
                verdict_reason = (f"PROVEN_POSITIVE WR {proven_pos['wr_10']}% vs "
                                  f"UNPROVEN {unproven['wr_10']}% (+{lift}pp lift, "
                                  f"n={proven_pos['n']})")
            else:
                verdict        = "FALSIFIED"
                verdict_reason = (f"no WR gradient: PROVEN_POSITIVE {proven_pos['wr_10']}% "
                                  f"vs UNPROVEN {unproven['wr_10']}% (n={proven_pos['n']})")

        return {
            "rows":           tier_rows,
            "verdict":        verdict,
            "verdict_reason": verdict_reason,
            "total_n":        total_n,
        }
    except Exception as e:
        return {"rows": [], "verdict": "ERROR", "verdict_reason": str(e), "total_n": 0}


def _validate_transition_detector(conn) -> dict:
    """
    Transition detector reconstruction validation.

    For each COMPLETE SCANNER row, look at the prior 2–3 SCANNER rows for the
    same symbol, compute vacc_slope and score_slope, classify into
    APPROACHING / FADING / DORMANT, then use the current row's return_24h_pct.

    Pure prediction: prior-only momentum → current scan outcome.  No look-ahead.

    Scope note: tests on already-triggered scanner tokens, not on quiet-monitor
    candidates.  Answers: does rising momentum in prior scans predict better
    outcomes when a token appears in the scanner?
    """
    try:
        all_rows = conn.execute("""
            SELECT symbol, scanned_at, vol_acceleration, score, return_24h_pct
            FROM   memecoin_signal_outcomes
            WHERE  source          = 'SCANNER'
              AND  vol_acceleration IS NOT NULL
              AND  score            IS NOT NULL
              AND  status           = 'COMPLETE'
              AND  return_24h_pct   IS NOT NULL
            ORDER BY symbol, scanned_at
        """).fetchall()

        by_sym: dict = {}
        for r in all_rows:
            s = r["symbol"]
            if s not in by_sym:
                by_sym[s] = []
            by_sym[s].append({
                "vacc":           float(r["vol_acceleration"]),
                "score":          float(r["score"]),
                "return_24h_pct": float(r["return_24h_pct"]),
            })

        _VACC_SLOPE_MIN  = 0.3
        _SCORE_SLOPE_MIN = 2.0

        labeled: list = []
        for rows_s in by_sym.values():
            for i in range(1, len(rows_s)):
                prior  = rows_s[max(0, i - 3): i]   # up to 3 prior rows, no current
                if len(prior) < 2:
                    continue
                vs      = [w["vacc"]  for w in prior]
                ss      = [w["score"] for w in prior]
                n_pts   = len(prior)
                vacc_slope  = (vs[-1] - vs[0]) / (n_pts - 1)   # positive = rising
                score_slope = (ss[-1] - ss[0]) / (n_pts - 1)

                if vacc_slope > _VACC_SLOPE_MIN and score_slope > _SCORE_SLOPE_MIN:
                    status = "APPROACHING"
                elif vacc_slope < -_VACC_SLOPE_MIN or score_slope < -_SCORE_SLOPE_MIN:
                    status = "FADING"
                else:
                    status = "DORMANT"

                labeled.append({"status": status, "return_24h_pct": rows_s[i]["return_24h_pct"]})

        from collections import defaultdict as _dd
        groups: dict = _dd(list)
        for item in labeled:
            groups[item["status"]].append(item["return_24h_pct"])

        _ORDER  = {"APPROACHING": 0, "DORMANT": 1, "FADING": 2}
        st_rows = []
        for st in sorted(groups.keys(), key=lambda x: _ORDER.get(x, 9)):
            returns = groups[st]
            n       = len(returns)
            avg_r   = round(sum(returns) / n, 1)                                   if n else None
            wr_10   = round(sum(1 for r in returns if r >= 10) / n * 100, 1)       if n else None
            wr_0    = round(sum(1 for r in returns if r >  0)  / n * 100, 1)       if n else None
            st_rows.append({
                "status":     st,
                "n":          n,
                "avg_return": avg_r,
                "wr_10":      wr_10,
                "wr_0":       wr_0,
            })

        total_n     = sum(r["n"] for r in st_rows)
        approaching = next((r for r in st_rows if r["status"] == "APPROACHING"), None)
        fading      = next((r for r in st_rows if r["status"] == "FADING"),      None)
        dormant     = next((r for r in st_rows if r["status"] == "DORMANT"),     None)
        baseline    = fading or dormant

        verdict        = "NOT_YET_PROVEN"
        verdict_reason = "insufficient data"
        directional_state = "NO_SIGNAL"
        validation_tracks = {
            "promotive_track": {
                "scope": "APPROACHING vs baseline (FADING or DORMANT)",
                "approaching": approaching,
                "baseline": baseline,
                "lift_pp": None,
                "directional_state": "NO_SIGNAL",
            }
        }

        if (approaching and baseline
                and approaching["wr_10"] is not None
                and baseline["wr_10"]    is not None):
            lift_pp    = round(approaching["wr_10"] - baseline["wr_10"], 1)
            ctrl_label = baseline["status"]
            if lift_pp >= 5:
                directional_state = "PROMOTIVE_SIGNAL"
            elif lift_pp > 0:
                directional_state = "THIN_POSITIVE_SIGNAL"
            elif lift_pp < 0:
                directional_state = "NEGATIVE_SIGNAL"
            else:
                directional_state = "FLAT_SIGNAL"
            validation_tracks["promotive_track"] = {
                "scope": "APPROACHING vs baseline (FADING or DORMANT)",
                "approaching": approaching,
                "baseline": {
                    "status": ctrl_label,
                    "n": baseline["n"],
                    "avg_return": baseline["avg_return"],
                    "wr_10": baseline["wr_10"],
                    "wr_0": baseline["wr_0"],
                },
                "lift_pp": lift_pp,
                "directional_state": directional_state,
            }
            if approaching["n"] < 10:
                direction      = "positive" if lift_pp >= 0 else "negative"
                verdict        = "NOT_YET_PROVEN"
                verdict_reason = (f"APPROACHING n={approaching['n']} — too thin (need ≥10); "
                                  f"{direction} direction")
            elif lift_pp >= 5:
                verdict        = "EARNING_ITS_PLACE"
                verdict_reason = (f"APPROACHING WR {approaching['wr_10']}% vs "
                                  f"{ctrl_label} {baseline['wr_10']}% "
                                  f"(+{lift_pp}pp lift, n={approaching['n']})")
            elif lift_pp < 0:
                verdict        = "FALSIFIED"
                verdict_reason = (f"APPROACHING underperforms {ctrl_label} "
                                  f"({lift_pp:+.1f}pp, n={approaching['n']})")
            else:
                verdict        = "NOT_YET_PROVEN"
                verdict_reason = (f"minimal lift +{lift_pp}pp vs {ctrl_label} "
                                  f"(need ≥5pp — n={approaching['n']})")
        elif approaching:
            validation_tracks["promotive_track"] = {
                "scope": "APPROACHING vs baseline (FADING or DORMANT)",
                "approaching": approaching,
                "baseline": None,
                "lift_pp": None,
                "directional_state": "BASELINE_ABSENT",
            }

        scope_note = "active scanner tokens only · quiet-monitor population not testable yet"
        if directional_state == "THIN_POSITIVE_SIGNAL":
            scope_note += "; early positive slope signal exists but has not earned promotion"
        elif directional_state == "NEGATIVE_SIGNAL":
            scope_note += "; current direction is adverse vs baseline"

        return {
            "rows":           st_rows,
            "verdict":        verdict,
            "verdict_reason": verdict_reason,
            "total_n":        total_n,
            "scope_note":     scope_note,
            "validation_tracks": validation_tracks,
            "directional_state": directional_state,
        }
    except Exception as e:
        return {"rows": [], "verdict": "ERROR", "verdict_reason": str(e), "total_n": 0}


def _label_track_summary(raw_rows: list, key_field: str, key_status: str, contrast_status: str | None) -> dict:
    """Split label evidence into clean-promotive vs bootstrap-directional tracks."""
    tracks: dict[str, list] = {"clean_near_scan": [], "bootstrap_observed": []}
    for row in raw_rows:
        track_key = "clean_near_scan" if int(row["is_clean"] or 0) == 1 else "bootstrap_observed"
        tracks[track_key].append(row)

    def _summarize(track_rows: list, scope: str) -> dict:
        grouped: dict[str, list] = {}
        for row in track_rows:
            grouped.setdefault(row[key_field], []).append(float(row["return_24h_pct"]))

        def _bucket(status: str | None) -> dict | None:
            if not status:
                return None
            vals = grouped.get(status) or []
            if not vals:
                return None
            n = len(vals)
            return {
                "status": status,
                "n": n,
                "avg_return": round(sum(vals) / n, 1),
                "wr_10": round(sum(1 for v in vals if v >= 10) / n * 100, 1),
                "wr_0": round(sum(1 for v in vals if v > 0) / n * 100, 1),
            }

        key_bucket      = _bucket(key_status)
        contrast_bucket = _bucket(contrast_status)
        total_n         = len(track_rows)

        gradient_ok = False
        if key_bucket and key_bucket["avg_return"] is not None and key_bucket["avg_return"] < 0:
            if contrast_bucket is None:
                gradient_ok = True
            elif (
                contrast_bucket["wr_10"] is not None
                and key_bucket["wr_10"] is not None
                and contrast_bucket["wr_10"] >= key_bucket["wr_10"]
            ):
                gradient_ok = True

        return {
            "scope": scope,
            "total_n": total_n,
            "key_bucket": key_bucket,
            "contrast_bucket": contrast_bucket,
            "gradient_present": gradient_ok,
        }

    clean_track = _summarize(
        tracks["clean_near_scan"],
        "promotion-eligible labels written within 48h of scan",
    )
    bootstrap_track = _summarize(
        tracks["bootstrap_observed"],
        "retroactive/bootstrap labels — directional only, not promotive",
    )
    return {
        "clean_near_scan": clean_track,
        "bootstrap_observed": bootstrap_track,
    }


def _validate_trust_labels(conn) -> dict:
    """
    Patch 305 — Direct validation of scan-time-persisted trust_label vs 24h outcomes.

    trust_label is written by _fmt_cand() at NBA query time (Patch 301), using only
    data available at that moment (perf_tier, correction_tier, setup_status).
    No look-ahead — NBA is polled every 30–60s, so label reflects pre-outcome state.

    Verdict logic:
      clean_n < 20    → NOT_YET_PROVEN
      clean_n ≥ 20 + DISTRUST avg < 0 + gradient vs HIGH_TRUST (or HIGH_TRUST absent)
                      → EARNING_ITS_PLACE
      n ≥ 20 + gradient inverted → FALSIFIED
    """
    _ensure_memecoin_outcome_label_schema(conn)
    try:
        rows = conn.execute("""
            SELECT
                trust_label                                                   AS label,
                COUNT(*)                                                      AS n,
                ROUND(AVG(return_24h_pct), 1)                                AS avg_return,
                ROUND(SUM(CASE WHEN return_24h_pct >= 10 THEN 1.0 ELSE 0.0 END)
                      * 100.0 / COUNT(*), 1)                                 AS wr_10,
                ROUND(SUM(CASE WHEN return_24h_pct >  0  THEN 1.0 ELSE 0.0 END)
                      * 100.0 / COUNT(*), 1)                                 AS wr_0
            FROM memecoin_signal_outcomes
            WHERE status         = 'COMPLETE'
              AND return_24h_pct IS NOT NULL
              AND trust_label    IS NOT NULL
            GROUP BY trust_label
            ORDER BY CASE trust_label
                WHEN 'HIGH_TRUST'        THEN 0
                WHEN 'CONDITIONAL_TRUST' THEN 1
                WHEN 'LOW_TRUST'         THEN 2
                WHEN 'DISTRUST'          THEN 3
                ELSE                          4
            END
        """).fetchall()

        raw_rows = conn.execute("""
            SELECT
                trust_label AS status,
                return_24h_pct,
                CASE
                    WHEN labeled_at IS NOT NULL
                     AND (julianday(labeled_at) - julianday(scanned_at)) * 86400.0 <= 172800
                    THEN 1 ELSE 0
                END AS is_clean
            FROM memecoin_signal_outcomes
            WHERE status='COMPLETE'
              AND return_24h_pct IS NOT NULL
              AND trust_label IS NOT NULL
        """).fetchall()

        clean_total = int((conn.execute("""
            SELECT COUNT(*)
            FROM memecoin_signal_outcomes
            WHERE status='COMPLETE'
              AND return_24h_pct IS NOT NULL
              AND trust_label IS NOT NULL
              AND labeled_at IS NOT NULL
              AND (julianday(labeled_at) - julianday(scanned_at)) * 86400.0 <= 172800
        """).fetchone() or [0])[0])

        label_rows = [
            {"status": r["label"], "n": r["n"],
             "avg_return": r["avg_return"], "wr_10": r["wr_10"], "wr_0": r["wr_0"]}
            for r in rows
        ]
        total_n  = sum(r["n"] for r in label_rows)
        distrust = next((r for r in label_rows if r["status"] == "DISTRUST"), None)
        high     = next((r for r in label_rows if r["status"] == "HIGH_TRUST"), None)
        tracks   = _label_track_summary(raw_rows, "status", "DISTRUST", "HIGH_TRUST")
        bootstrap_total = max(0, total_n - clean_total)
        bootstrap_track = tracks["bootstrap_observed"]

        verdict        = "NOT_YET_PROVEN"
        verdict_reason = (f"{clean_total}/20 clean-labeled "
                          f"({total_n} total, {bootstrap_total} bootstrap)")

        if clean_total >= 20:
            if distrust and distrust["avg_return"] is not None and distrust["avg_return"] < 0:
                # HIGH_TRUST absent OR HIGH_TRUST wr_10 > DISTRUST wr_10 → gradient correct
                if high is None or (
                    high["avg_return"] is not None
                    and high["wr_10"] is not None
                    and distrust["wr_10"] is not None
                    and high["wr_10"] >= distrust["wr_10"]
                ):
                    ht_str = (f" vs HIGH_TRUST avg {high['avg_return']}% WR {high['wr_10']}%"
                              if high else " (HIGH_TRUST not yet in sample)")
                    verdict        = "EARNING_ITS_PLACE"
                    verdict_reason = (
                        f"DISTRUST avg {distrust['avg_return']}% WR {distrust['wr_10']}%"
                        f"{ht_str} (clean n={clean_total}, total={total_n})"
                    )
                else:
                    verdict        = "FALSIFIED"
                    verdict_reason = (
                        f"gradient inverted — DISTRUST avg {distrust['avg_return']}% "
                        f"but HIGH_TRUST avg {high['avg_return']}% is not superior "
                        f"(clean n={clean_total}, total={total_n})"
                    )
            else:
                verdict_reason = (f"{clean_total}/20 clean-labeled "
                                  f"({total_n} total) — insufficient DISTRUST outcomes for gradient check")

        scope_note = (
            f"promotion uses clean near-scan labels only ({clean_total} clean, {bootstrap_total} bootstrap)"
        )
        if clean_total < 20 and bootstrap_total > 0 and bootstrap_track["gradient_present"]:
            key_bucket = bootstrap_track["key_bucket"]
            contrast   = bootstrap_track["contrast_bucket"]
            if key_bucket:
                contrast_str = (
                    f" vs {contrast['status']} WR {contrast['wr_10']}%"
                    if contrast else " (contrast bucket absent)"
                )
                scope_note += (
                    f"; bootstrap sample already shows directional signal: "
                    f"{key_bucket['status']} avg {key_bucket['avg_return']}% WR {key_bucket['wr_10']}%"
                    f"{contrast_str}"
                )

        return {
            "rows":           label_rows,
            "verdict":        verdict,
            "verdict_reason": verdict_reason,
            "total_n":        total_n,
            "scope_note":     scope_note,
            "validation_tracks": tracks,
            "clean_n":        clean_total,
            "bootstrap_n":    bootstrap_total,
        }
    except Exception as e:
        return {"rows": [], "verdict": "ERROR", "verdict_reason": str(e), "total_n": 0}


def _validate_triage_states(conn) -> dict:
    """
    Patch 305 — Direct validation of scan-time-persisted triage_state vs 24h outcomes.

    triage_state written alongside trust_label by _fmt_cand() (Patch 301).

    Verdict logic:
      clean_n < 20    → NOT_YET_PROVEN
      clean_n ≥ 20 + DO_NOT_TOUCH avg < 0 + avg(DO_NOT_TOUCH) < avg(INVESTIGATE_NOW or absent)
                      → EARNING_ITS_PLACE
      inverted        → FALSIFIED
    """
    _ensure_memecoin_outcome_label_schema(conn)
    try:
        rows = conn.execute("""
            SELECT
                triage_state                                                  AS state,
                COUNT(*)                                                      AS n,
                ROUND(AVG(return_24h_pct), 1)                                AS avg_return,
                ROUND(SUM(CASE WHEN return_24h_pct >= 10 THEN 1.0 ELSE 0.0 END)
                      * 100.0 / COUNT(*), 1)                                 AS wr_10,
                ROUND(SUM(CASE WHEN return_24h_pct >  0  THEN 1.0 ELSE 0.0 END)
                      * 100.0 / COUNT(*), 1)                                 AS wr_0
            FROM memecoin_signal_outcomes
            WHERE status         = 'COMPLETE'
              AND return_24h_pct IS NOT NULL
              AND triage_state   IS NOT NULL
            GROUP BY triage_state
            ORDER BY CASE triage_state
                WHEN 'INVESTIGATE_NOW' THEN 0
                WHEN 'MONITOR'         THEN 1
                WHEN 'BLOCKED'         THEN 2
                WHEN 'DO_NOT_TOUCH'    THEN 3
                ELSE                        4
            END
        """).fetchall()

        raw_rows = conn.execute("""
            SELECT
                triage_state AS status,
                return_24h_pct,
                CASE
                    WHEN labeled_at IS NOT NULL
                     AND (julianday(labeled_at) - julianday(scanned_at)) * 86400.0 <= 172800
                    THEN 1 ELSE 0
                END AS is_clean
            FROM memecoin_signal_outcomes
            WHERE status='COMPLETE'
              AND return_24h_pct IS NOT NULL
              AND triage_state IS NOT NULL
        """).fetchall()

        clean_total = int((conn.execute("""
            SELECT COUNT(*)
            FROM memecoin_signal_outcomes
            WHERE status='COMPLETE'
              AND return_24h_pct IS NOT NULL
              AND triage_state IS NOT NULL
              AND labeled_at IS NOT NULL
              AND (julianday(labeled_at) - julianday(scanned_at)) * 86400.0 <= 172800
        """).fetchone() or [0])[0])

        state_rows = [
            {"status": r["state"], "n": r["n"],
             "avg_return": r["avg_return"], "wr_10": r["wr_10"], "wr_0": r["wr_0"]}
            for r in rows
        ]
        total_n     = sum(r["n"] for r in state_rows)
        dnt         = next((r for r in state_rows if r["status"] == "DO_NOT_TOUCH"),   None)
        investigate = next((r for r in state_rows if r["status"] == "INVESTIGATE_NOW"), None)
        tracks      = _label_track_summary(raw_rows, "status", "DO_NOT_TOUCH", "INVESTIGATE_NOW")
        bootstrap_total = max(0, total_n - clean_total)
        bootstrap_track = tracks["bootstrap_observed"]

        verdict        = "NOT_YET_PROVEN"
        verdict_reason = (f"{clean_total}/20 clean-labeled "
                          f"({total_n} total, {bootstrap_total} bootstrap)")

        if clean_total >= 20:
            if dnt and dnt["avg_return"] is not None and dnt["avg_return"] < 0:
                if investigate is None or (
                    investigate["avg_return"] is not None
                    and investigate["avg_return"] > dnt["avg_return"]
                ):
                    inv_str = (f" vs INVESTIGATE_NOW avg {investigate['avg_return']}%"
                               if investigate else " (INVESTIGATE_NOW not yet in sample)")
                    verdict        = "EARNING_ITS_PLACE"
                    verdict_reason = (
                        f"DO_NOT_TOUCH avg {dnt['avg_return']}% WR {dnt['wr_10']}%"
                        f"{inv_str} (clean n={clean_total}, total={total_n})"
                    )
                else:
                    verdict        = "FALSIFIED"
                    verdict_reason = (
                        f"triage gradient inverted — DO_NOT_TOUCH avg {dnt['avg_return']}% "
                        f"not below INVESTIGATE_NOW avg {investigate['avg_return']}% "
                        f"(clean n={clean_total}, total={total_n})"
                    )
            else:
                verdict_reason = (f"{clean_total}/20 clean-labeled "
                                  f"({total_n} total) — insufficient DO_NOT_TOUCH outcomes for gradient check")

        scope_note = (
            f"promotion uses clean near-scan labels only ({clean_total} clean, {bootstrap_total} bootstrap)"
        )
        if clean_total < 20 and bootstrap_total > 0 and bootstrap_track["gradient_present"]:
            key_bucket = bootstrap_track["key_bucket"]
            contrast   = bootstrap_track["contrast_bucket"]
            if key_bucket:
                contrast_str = (
                    f" vs {contrast['status']} avg {contrast['avg_return']}%"
                    if contrast else " (contrast bucket absent)"
                )
                scope_note += (
                    f"; bootstrap sample already shows directional signal: "
                    f"{key_bucket['status']} avg {key_bucket['avg_return']}% WR {key_bucket['wr_10']}%"
                    f"{contrast_str}"
                )

        return {
            "rows":           state_rows,
            "verdict":        verdict,
            "verdict_reason": verdict_reason,
            "total_n":        total_n,
            "scope_note":     scope_note,
            "validation_tracks": tracks,
            "clean_n":        clean_total,
            "bootstrap_n":    bootstrap_total,
        }
    except Exception as e:
        return {"rows": [], "verdict": "ERROR", "verdict_reason": str(e), "total_n": 0}


@router.get("/intel-validation")
async def get_intel_validation(_: str = Depends(get_current_user)):
    """
    Patch 305 — Predictive validation layer.

    Validates four intelligence layers against COMPLETE outcome data.

    Validated here:
      1. Perf-tier forward validity  — walk-forward self-join, zero look-ahead
      2. Transition detector         — prior momentum slopes → current outcome
      3. Trust label validation      — Patch 301 scan-time-persisted trust_label
      4. Triage state validation     — Patch 301 scan-time-persisted triage_state

    Deferred (require additional scan-time logging):
      - correction_tier — phase boundaries not persisted at scan time
    """
    _ensure_engine_path()

    def _run() -> dict:
        import sqlite3 as _sq3
        from datetime import datetime as _dt, timezone as _tz
        import os as _os
        root    = _os.path.abspath(_os.path.join(_os.path.dirname(__file__), "..", "..", ".."))
        db_path = _os.path.join(root, "data_storage", "engine.db")
        conn    = _sq3.connect(db_path)
        conn.row_factory = _sq3.Row
        try:
            return {
                "perf_tier_forward":      _validate_perf_tier_forward(conn),
                "transition_detector":    _validate_transition_detector(conn),
                "trust_label_validation": _validate_trust_labels(conn),    # Patch 305
                "triage_state_validation": _validate_triage_states(conn),  # Patch 305
                "not_yet_validated": [
                    "correction_tier — phase boundaries not persisted at scan time",
                ],
                "generated_at": _dt.now(_tz.utc).isoformat(),
            }
        finally:
            conn.close()

    import asyncio as _aio
    return await _aio.to_thread(_run)
