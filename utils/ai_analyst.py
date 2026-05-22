from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from utils.db import get_conn, get_memecoin_outcome_insights
from utils.orchestrator import append_memory
from utils.speculation_heat import build_speculation_heat_snapshot

_KV_KEY = "ai_analyst_snapshot"


def _ensure_dashboard_backend_path() -> None:
    backend_root = Path(__file__).resolve().parent.parent / "dashboard" / "backend"
    backend_root_str = str(backend_root)
    if backend_root_str not in sys.path:
        sys.path.insert(0, backend_root_str)


def _safe_float(value) -> float | None:
    try:
        return float(value) if value is not None else None
    except Exception:
        return None


def _safe_int(value) -> int | None:
    try:
        return int(value) if value is not None else None
    except Exception:
        return None


def _load_kv_json(key: str) -> dict:
    try:
        with get_conn() as conn:
            row = conn.execute("SELECT value FROM kv_store WHERE key=?", (key,)).fetchone()
        if row and row[0]:
            return json.loads(row[0])
    except Exception:
        pass
    return {}


def get_latest_ai_analyst_snapshot() -> dict:
    return _load_kv_json(_KV_KEY)


def _store_ai_analyst_snapshot(snapshot: dict, *, append_memory_entry: bool = True) -> dict:
    previous = get_latest_ai_analyst_snapshot()
    previous_sig = str(previous.get("signature") or "")
    current_sig = str(snapshot.get("signature") or "")
    try:
        with get_conn() as conn:
            conn.execute(
                """
                INSERT INTO kv_store (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
                """,
                (_KV_KEY, json.dumps(snapshot, separators=(",", ":"))),
            )
    except Exception:
        pass

    if append_memory_entry and current_sig and current_sig != previous_sig:
        lines = [
            f"Headline: {snapshot.get('headline') or 'No headline.'}",
            f"Top lesson: {snapshot.get('top_lesson') or 'No lesson yet.'}",
        ]
        for key, label in (
            ("what_improved", "Improved"),
            ("what_degraded", "Degraded"),
            ("review_now", "Review now"),
            ("do_not_change_yet", "Do not change yet"),
        ):
            values = [str(v).strip() for v in (snapshot.get(key) or []) if str(v).strip()]
            if values:
                lines.append(f"{label}: " + " | ".join(values[:2]))
        append_memory("AI_ANALYST", "\n".join(lines))
    return snapshot


def _summarize_health_state() -> dict:
    keys = (
        "system_health",
        "data_integrity_status",
        "pipeline_watchdog_status",
        "allocator_stream_status",
        "token_stats_stream_status",
        "large_trade_stream_status",
        "wallet_tx_stream_status",
    )
    health: dict[str, dict] = {}
    for key in keys:
        payload = _load_kv_json(key)
        status = (
            str(payload.get("status") or payload.get("health") or payload.get("state") or "UNKNOWN")
            .strip()
            .upper()
        )
        health[key] = {
            "status": status or "UNKNOWN",
            "summary": str(
                payload.get("summary")
                or payload.get("detail")
                or payload.get("reason")
                or payload.get("note")
                or ""
            ).strip(),
        }
    return health


def _build_best_action_snapshot() -> dict:
    try:
        _ensure_dashboard_backend_path()
        from routers.home import _build_best_action  # type: ignore

        payload = _build_best_action() or {}
        return {
            "verdict": str(payload.get("verdict") or "UNKNOWN"),
            "arm": str(payload.get("arm") or ""),
            "asset": str(payload.get("asset") or ""),
            "conviction": _safe_float(payload.get("conviction")) or 0.0,
            "reason": str(payload.get("reason") or ""),
            "execution_blocked": bool(payload.get("execution_blocked")),
            "candidates": list(payload.get("candidates") or [])[:3],
        }
    except Exception as exc:
        return {
            "verdict": "UNKNOWN",
            "arm": "",
            "asset": "",
            "conviction": 0.0,
            "reason": f"Best action unavailable: {exc}",
            "execution_blocked": False,
            "candidates": [],
        }


def _build_spot_snapshot() -> dict:
    try:
        _ensure_dashboard_backend_path()
        from routers.home import _build_home_summary_payload  # type: ignore

        payload = _build_home_summary_payload() or {}
        spot = dict(payload.get("spot") or {})
        return {
            "mode": str(spot.get("mode") or "UNKNOWN"),
            "signal_confidence": str(spot.get("signal_confidence") or "UNKNOWN"),
            "holdings_count": _safe_int(spot.get("holdings_count")) or 0,
            "basket_size": _safe_int(spot.get("basket_size")) or 0,
            "win_rate_7d": _safe_float(spot.get("win_rate_7d")),
            "outcomes_complete": _safe_int(spot.get("outcomes_complete")) or 0,
        }
    except Exception as exc:
        return {
            "mode": "UNKNOWN",
            "signal_confidence": "UNKNOWN",
            "holdings_count": 0,
            "basket_size": 0,
            "win_rate_7d": None,
            "outcomes_complete": 0,
            "detail": f"Spot summary unavailable: {exc}",
        }


def _build_proof_summary(limit: int = 8) -> dict:
    try:
        from utils.memecoin_manager import get_proof_candidate_snapshot

        snapshot = get_proof_candidate_snapshot(limit=max(5, int(limit))) or {}
        candidates = list(snapshot.get("candidates") or [])
    except Exception as exc:
        return {
            "candidate_count": 0,
            "proof_ready_count": 0,
            "ready_or_near_count": 0,
            "top_candidates": [],
            "error": str(exc),
        }

    proof_ready = 0
    ready_or_near = 0
    top_candidates: list[dict] = []
    for candidate in candidates:
        proof_status = str(candidate.get("proof_status") or "").upper()
        readiness_score = _safe_float(candidate.get("readiness_score")) or 0.0
        if proof_status == "PROOF_READY":
            proof_ready += 1
        if proof_status == "PROOF_READY" or readiness_score >= 60.0:
            ready_or_near += 1
        top_candidates.append(
            {
                "symbol": str(candidate.get("symbol") or ""),
                "proof_status": proof_status or "UNKNOWN",
                "readiness_score": readiness_score,
                "support_score": _safe_float(candidate.get("support_score")) or 0.0,
                "market_quality_verdict": str(candidate.get("market_quality_verdict") or "UNKNOWN"),
                "profit_room_label": str(candidate.get("profit_room_label") or ""),
                "wallet_behavior_state": str(candidate.get("wallet_behavior_state") or ""),
                "hard_blockers": [
                    str((blocker or {}).get("key") or "")
                    for blocker in (candidate.get("hard_blockers") or [])
                    if str((blocker or {}).get("key") or "").strip()
                ][:3],
            }
        )

    return {
        "candidate_count": len(candidates),
        "proof_input_source": str(snapshot.get("proof_input_source") or "UNKNOWN"),
        "proof_ready_count": proof_ready,
        "ready_or_near_count": ready_or_near,
        "top_candidates": top_candidates[:5],
    }


def _fallback_sections(context: dict) -> dict:
    outcome = dict(context.get("outcome_insights") or {})
    heat = dict(context.get("speculation_heat") or {})
    best_action = dict(context.get("best_action") or {})
    proof = dict(context.get("proof_summary") or {})
    spot = dict(context.get("spot_summary") or {})
    health = dict(context.get("health") or {})

    top_lesson = str(outcome.get("headline") or "Outcome history is still too thin for strong lessons.")
    highlights = [str((item or {}).get("title") or "").strip() for item in (outcome.get("highlights") or [])]
    highlights = [item for item in highlights if item]
    if highlights:
        top_lesson = highlights[0]

    improved: list[str] = []
    degraded: list[str] = []
    review_now: list[str] = []
    hold_steady: list[str] = []

    heat_state = str(heat.get("heat_state") or "UNKNOWN").upper()
    heat_score = _safe_float(heat.get("heat_score")) or 0.0
    quality_score = _safe_float(heat.get("quality_score")) or 0.0
    froth_score = _safe_float(heat.get("froth_score")) or 0.0

    if heat_state in ("WARM", "HOT") and quality_score >= 65.0:
        improved.append(f"Speculation heat is {heat_state.lower()} but quality is still holding ({quality_score:.1f}).")
    elif heat_state == "COOL":
        improved.append("Heat is cool, which lowers pressure to chase marginal memecoin setups.")

    verdict = str(best_action.get("verdict") or "DO_NOTHING").upper()
    arm = str(best_action.get("arm") or "").upper()
    asset = str(best_action.get("asset") or "").upper()
    conviction = _safe_float(best_action.get("conviction")) or 0.0
    if verdict != "DO_NOTHING" and conviction >= 55.0 and arm in ("SPOT", "MEMECOINS"):
        improved.append(f"Best Action currently prefers {arm or 'a live arm'} on {asset or 'the strongest asset'} with {conviction:.0f} conviction.")
    elif verdict != "DO_NOTHING" and conviction >= 55.0 and arm == "PERPS":
        hold_steady.append(f"Perps still have a live read on {asset or 'the strongest asset'}, but that remains background context for the current memecoin and spot focus.")

    if froth_score >= 55.0:
        degraded.append(f"Froth is elevated ({froth_score:.1f}), so late memecoin entries need extra skepticism.")
    if verdict == "DO_NOTHING":
        degraded.append("Best Action is not seeing a cross-arm setup strong enough to force deployment.")

    proof_ready_count = _safe_int(proof.get("proof_ready_count")) or 0
    ready_or_near_count = _safe_int(proof.get("ready_or_near_count")) or 0
    top_candidates = list(proof.get("top_candidates") or [])
    if proof_ready_count <= 0:
        review_now.append("Memecoin lane still lacks a proof-ready candidate, so blocked deployment may be a signal-quality issue rather than a policy issue.")
    if top_candidates:
        first = dict(top_candidates[0] or {})
        blockers = [b for b in (first.get("hard_blockers") or []) if b]
        if blockers:
            review_now.append(
                f"Top memecoin candidate {first.get('symbol') or 'UNKNOWN'} is being held back by {', '.join(blockers[:2])}."
            )
        if str(first.get("profit_room_label") or "").upper() in ("STRETCHED", "TOO_LATE"):
            review_now.append(
                f"Top memecoin candidate {first.get('symbol') or 'UNKNOWN'} looks economically late ({str(first.get('profit_room_label') or '').lower()})."
            )
    proof_input_source = str(proof.get("proof_input_source") or "").upper()
    if proof_input_source and proof_input_source != "LIVE_CACHE":
        degraded.append(
            f"Memecoin proof review is leaning on {proof_input_source.lower().replace('_', ' ')}, so candidate freshness is weaker than a clean live-cache cycle."
        )

    spot_conf = str(spot.get("signal_confidence") or "UNKNOWN").lower()
    spot_mode = str(spot.get("mode") or "UNKNOWN").upper()
    spot_holdings = _safe_int(spot.get("holdings_count")) or 0
    spot_basket = _safe_int(spot.get("basket_size")) or 0
    if spot_mode in ("PAPER", "LIVE") and spot_conf in ("high", "medium"):
        improved.append(
            f"Spot quality is at least {spot_conf}, so the lane is structurally usable even if the next move is still hold-or-rotate."
        )
    elif spot_mode in ("PAPER", "LIVE"):
        review_now.append("Spot confidence is still too soft to force a new add; keep waiting for a cleaner add-ready or rotate-worthy setup.")
    if spot_basket > 0 and spot_holdings >= spot_basket:
        hold_steady.append("Spot basket is already full, so the next spot improvement needs to be rotation-worthy rather than just decent.")

    wallet_status = str(((health.get("wallet_tx_stream_status") or {}).get("status") or "UNKNOWN")).upper()
    if wallet_status in ("ACTIVE", "HEALTHY"):
        hold_steady.append("Keep the new wallet-intelligence lane running; it still needs live history before we retune around it.")
    else:
        review_now.append("Wallet tracking is not showing healthy live flow yet, so support learning is still underpowered.")

    if outcome.get("status") == "THIN":
        hold_steady.append(str(outcome.get("headline") or "Outcome attribution is still thin; do not overfit early samples."))
    elif highlights:
        improved.append(f"Outcome learning has enough signal to surface a first edge: {highlights[0]}")

    system_health = str(((health.get("system_health") or {}).get("status") or "UNKNOWN")).upper()
    data_integrity = str(((health.get("data_integrity_status") or {}).get("status") or "UNKNOWN")).upper()
    if system_health not in ("OK", "HEALTHY", "FRESH", "PASS"):
        degraded.append(f"System health is reporting {system_health.lower()}, so trust fresh signals a little less until that clears.")
    if data_integrity not in ("OK", "HEALTHY", "PASS"):
        review_now.append(f"Data integrity is {data_integrity.lower()}, which is worth checking before tightening thresholds.")

    improved = improved[:2] or ["The runtime is cleaner now, which makes system state easier to trust."]
    degraded = degraded[:2] or ["No major degradation is standing out yet beyond normal thin-history limits."]
    review_now = review_now[:2] or ["Review the next live memecoin candidates to see whether profit-room and readiness still agree with human judgment."]
    hold_steady = hold_steady[:2] or ["Do not introduce more major systems yet; let the new live layers accumulate evidence first."]

    headline = "Memecoin and spot lanes are clearer now, but the newest learning layers still need more live reps."
    if proof_ready_count > 0:
        headline = "Memecoin lane has a live proof-quality setup to review, but it still needs enough room and clean reinforcement to earn trust."
    elif spot_conf in ("high", "medium"):
        headline = "Spot lane is structurally usable, but the next move still needs to be a clean add or rotation instead of a forced entry."
    if proof_input_source and proof_input_source != "LIVE_CACHE":
        headline = "Memecoin lane is running, but candidate freshness is weaker right now because proof review is leaning on fallback input."
    if heat_state in ("HOT", "OVERHEATED"):
        headline = "Speculation is heating up, so memecoin room discipline and spot selectivity matter more than raw signal excitement."

    return {
        "headline": headline,
        "top_lesson": top_lesson,
        "what_improved": improved,
        "what_degraded": degraded,
        "review_now": review_now,
        "do_not_change_yet": hold_steady,
    }


_ANALYST_PROMPT = """
You are an analyst for a crypto trading system. You are advisory only.

Read the structured system snapshot below and return strict JSON with this schema:
{
  "headline": "one sentence",
  "top_lesson": "one sentence",
  "what_improved": ["short item", "short item"],
  "what_degraded": ["short item", "short item"],
  "review_now": ["short item", "short item"],
  "do_not_change_yet": ["short item", "short item"]
}

Rules:
- Be concrete and concise.
- Use system language like readiness, profit room, support, market quality, heat, and best action.
- Treat memecoins and spot as the primary focus.
- Treat perps as background context only unless there is no meaningful memecoin or spot insight at all.
- Do not recommend automatic threshold changes.
- Do not mention missing keys or JSON formatting.
- Return JSON only, with no markdown.

SNAPSHOT:
{snapshot_json}
""".strip()


async def _generate_ai_sections(context: dict) -> dict | None:
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        return None

    prompt = _ANALYST_PROMPT.format(snapshot_json=json.dumps(context, separators=(",", ":"), default=str))
    async with httpx.AsyncClient(timeout=40) as client:
        response = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-haiku-4-5",
                "max_tokens": 500,
                "messages": [{"role": "user", "content": prompt}],
            },
        )
        response.raise_for_status()
        data = response.json()
        text = str(((data.get("content") or [{}])[0]).get("text") or "").strip()
        if not text:
            return None
        try:
            parsed = json.loads(text)
        except Exception:
            start = text.find("{")
            end = text.rfind("}")
            if start >= 0 and end > start:
                parsed = json.loads(text[start : end + 1])
            else:
                return None

    return {
        "headline": str(parsed.get("headline") or "").strip(),
        "top_lesson": str(parsed.get("top_lesson") or "").strip(),
        "what_improved": [str(v).strip() for v in (parsed.get("what_improved") or []) if str(v).strip()][:2],
        "what_degraded": [str(v).strip() for v in (parsed.get("what_degraded") or []) if str(v).strip()][:2],
        "review_now": [str(v).strip() for v in (parsed.get("review_now") or []) if str(v).strip()][:2],
        "do_not_change_yet": [str(v).strip() for v in (parsed.get("do_not_change_yet") or []) if str(v).strip()][:2],
    }


def _signature_for_snapshot(snapshot: dict) -> str:
    raw = json.dumps(
        {
            "headline": snapshot.get("headline"),
            "top_lesson": snapshot.get("top_lesson"),
            "what_improved": snapshot.get("what_improved"),
            "what_degraded": snapshot.get("what_degraded"),
            "review_now": snapshot.get("review_now"),
            "do_not_change_yet": snapshot.get("do_not_change_yet"),
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _build_context(window_days: int = 90) -> dict:
    try:
        speculation_heat = build_speculation_heat_snapshot()
    except Exception as exc:
        speculation_heat = {
            "heat_state": "UNKNOWN",
            "heat_score": 0.0,
            "momentum": "STEADY",
            "quality_score": 0.0,
            "froth_score": 0.0,
            "sponsorship_score": 0.0,
            "note": f"Speculation heat unavailable: {exc}",
        }

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "outcome_insights": get_memecoin_outcome_insights(window_days=max(7, int(window_days)), min_trades=5),
        "speculation_heat": speculation_heat,
        "best_action": _build_best_action_snapshot(),
        "spot_summary": _build_spot_snapshot(),
        "proof_summary": _build_proof_summary(limit=8),
        "health": _summarize_health_state(),
    }


async def refresh_ai_analyst_snapshot(
    *,
    window_days: int = 90,
    append_memory_entry: bool = True,
) -> dict:
    context = _build_context(window_days=max(7, int(window_days)))
    sections = None
    source = "rules"
    try:
        sections = await _generate_ai_sections(context)
        if sections:
            source = "ai"
    except Exception:
        sections = None

    if not sections:
        sections = _fallback_sections(context)

    snapshot = {
        "generated_at": context["generated_at"],
        "status": "READY",
        "source": source,
        **sections,
        "context": context,
    }
    snapshot["signature"] = _signature_for_snapshot(snapshot)
    return _store_ai_analyst_snapshot(snapshot, append_memory_entry=append_memory_entry)


async def get_or_refresh_ai_analyst_snapshot(
    *,
    max_age_minutes: int = 60,
    window_days: int = 90,
) -> dict:
    latest = get_latest_ai_analyst_snapshot()
    if latest:
        try:
            ts = datetime.fromisoformat(str(latest.get("generated_at") or "").replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) - ts <= timedelta(minutes=max(30, int(max_age_minutes))):
                return latest
        except Exception:
            pass
    return await refresh_ai_analyst_snapshot(window_days=max(7, int(window_days)), append_memory_entry=False)
