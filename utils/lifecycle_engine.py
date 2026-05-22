"""
Memecoin Lifecycle Engine — Patch 240

Computes per-symbol lifecycle state from memecoin_signal_outcomes history.
Writes/updates the symbol_lifecycle table every time the scanner runs.

State machine:
  ACTIVE    — currently in scanner, vol_acc in setup range (3–8×)
  EXTENDED  — currently in scanner, vol_acc > 8× (running hot)
  COOLING   — currently in scanner, vol_acc < 3× (fading), or no first-leg proof
  REVIVAL   — currently active after gap > 7 days (was dark, just came back)
  RELOAD    — first-leg confirmed, in gap 8h–7d, liq held
  DORMANT   — first-leg confirmed, in gap 7–30d, liq holding some floor
  DEAD      — gap > 30d, or pullback > 80% + liq < $15k

Window definition:
  A new window begins when the gap between consecutive scans for the
  same symbol exceeds GAP_THRESHOLD_H (8 hours).
"""

import logging
import math
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone

log = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

GAP_THRESHOLD_H   = 8.0     # gap > 8h between scans → new window
REVIVAL_GAP_H     = 168.0   # gap before current window > 7d → REVIVAL
DORMANT_GAP_H     = 168.0   # hours in gap → DORMANT (7 days)
DEAD_GAP_H        = 720.0   # hours in gap → DEAD (30 days)

FIRST_LEG_THRESH  = 30.0    # return_24h_pct >= 30% = first-leg confirmed
VA_EXTENDED       = 8.0     # vol_acc > this while in-window → EXTENDED
VA_ACTIVE_MIN     = 3.0     # vol_acc < this while in-window → COOLING

DEAD_PULLBACK_PCT = 80.0    # pullback depth > 80% AND liq < MIN_LIQ_ALIVE → DEAD
MIN_LIQ_ALIVE     = 15_000  # liq below this is dead territory
SURVIVOR_RATIO    = 0.25    # liq_floor >= 25% of last-window peak → survivor_confirmed

# Patch 248: fuel quality
FUEL_TRAP_LIQ     = 20_000  # absolute liq below this → TRAP regardless of expansion ratios

PROVISIONAL_MIN_SCORE = 85.0
PROVISIONAL_MIN_VA    = 5.0
PROVISIONAL_MAX_TOPH  = 12.0


# ── Public ranking model (Patch 242 / shared 243 / freshness 244) ────────────

def compute_rank(rec: dict, now: "datetime | None" = None) -> tuple:
    """
    Deterministic rank for one symbol_lifecycle row.
    Returns (score: int, priority: str, factors: list[str]).

    Priority bands: HIGH >= 60, MEDIUM >= 40, LOW < 40.

    rec keys used: lifecycle_state, liq_trend, survivor_confirmed,
                   multi_leg_confirmed, n_windows, best_return_pct,
                   pullback_depth_pct, state_entered_at (Patch 244)
    """
    if now is None:
        now = datetime.now(timezone.utc)

    score   = 0
    factors = []

    state = rec.get("lifecycle_state", "")
    base  = {"RELOAD": 40, "REVIVAL": 35, "ACTIVE": 25, "DORMANT": 10}.get(state, 0)
    score += base
    if base:
        factors.append(state)

    trend = rec.get("liq_trend", "flat")
    if trend == "growing":
        score += 15
        factors.append("liq↑")
    elif trend == "shrinking":
        score -= 5
        factors.append("liq↓")

    if rec.get("survivor_confirmed"):
        score += 15
        factors.append("survivor")

    if rec.get("multi_leg_confirmed"):
        score += 10
        factors.append("multi-leg")

    n_win = int(rec.get("n_windows") or 0)
    if n_win >= 5:
        score += 10
        factors.append(f"{n_win}w")
    elif n_win >= 3:
        score += 7
        factors.append(f"{n_win}w")
    elif n_win >= 2:
        score += 3
        factors.append(f"{n_win}w")

    br = rec.get("best_return_pct")
    if br is not None:
        if br >= 100:
            score += 12
            factors.append(f"+{int(br)}%")
        elif br >= 50:
            score += 6
            factors.append(f"+{int(br)}%")
        elif br >= 30:
            score += 3
            factors.append(f"+{int(br)}%")

    pb = float(rec.get("pullback_depth_pct") or 0)
    if pb >= 80:
        score -= 20
        factors.append("pb↓↓")
    elif pb >= 60:
        score -= 10
        factors.append("pb↓")
    elif pb >= 40:
        score -= 3

    # ── Patch 244: freshness — how long has coin been in this state? ──────────
    # Rewards recent state entry; penalises coins sitting unchanged for days.
    # Uses _ts_to_dt / _hours defined later in this module (resolved at call time).
    sea = rec.get("state_entered_at")
    if sea:
        try:
            hours_in_state = (now - _ts_to_dt(sea)).total_seconds() / 3600.0
            if hours_in_state < 24:
                score += 15
                factors.append("new<24h")
            elif hours_in_state < 72:
                score += 8
                factors.append("fresh<3d")
            elif hours_in_state >= 240:   # 10 days
                score -= 20
                factors.append("stale10d")
            elif hours_in_state >= 120:   # 5 days
                score -= 10
                factors.append("stale5d")
        except (ValueError, TypeError):
            pass

    priority = "HIGH" if score >= 60 else ("MEDIUM" if score >= 40 else "LOW")
    return score, priority, factors


# ── Patch 246: Leg-intelligence phase computation ────────────────────────────

def _compute_phase(
    lifecycle_state:    str,
    liq_current:        float,
    liq_floor:          float,
    vol_acc:            float,
    liq_trend:          str,
    pullback_depth_pct: float,
    hours_since_last_scan: float,
) -> tuple:
    """
    Compute (move_phase, phase_score, entry_window) for one lifecycle record.

    move_phase  — categorical position in the current leg:
                  IGNITION | EARLY | MID | EXTENDED | CHURN | RELOAD

    phase_score — 0-100 integer, low = early in leg, high = late / spent.
                  Four additive components (max sum 100):
                    exp_score   0-40  log2(liq_expansion_ratio) * 12, capped
                    trend_score 0-25  growing=0 / flat=12 / shrinking=25
                    vacc_score  0-20  va>=8→0, 5-8→5, 3-5→10, 1.5-3→15, <1.5→20
                    pb_score    0-15  <30%→0, 30-50%→5, 50-70%→10, >=70%→15

    entry_window — OPEN | CLOSING | CLOSED
                   OPEN    = phase is IGNITION/EARLY (score<40) or clean RELOAD (score<50)
                   CLOSED  = CHURN, or EXTENDED with score>=55
                   CLOSING = everything else
    """
    # ── liq expansion ratio (primary phase signal) ────────────────────────
    # Floor must be positive; if floor is missing/zero use liq_current so ratio=1.
    floor     = max(liq_floor if (liq_floor and liq_floor > 0) else liq_current, 1_000.0)
    expansion = (liq_current / floor) if (liq_current and liq_current > 0) else 1.0

    # ── move_phase ────────────────────────────────────────────────────────
    # Between-window states are classified first (lifecycle_state drives it).
    # In-window states are classified by expansion ratio + liq_trend.
    if lifecycle_state == "DEAD":
        move_phase = "CHURN"
    elif lifecycle_state in ("RELOAD", "DORMANT") or (
        lifecycle_state == "COOLING" and hours_since_last_scan > GAP_THRESHOLD_H
    ):
        # Confirmed gap — quality depends on pullback depth
        move_phase = "CHURN" if pullback_depth_pct >= 70.0 else "RELOAD"
    else:
        # In-window: ACTIVE / EXTENDED / REVIVAL / COOLING-in-window
        if liq_trend == "shrinking" or (
            vol_acc < 2.0 and lifecycle_state not in ("REVIVAL",)
        ):
            move_phase = "CHURN"
        elif expansion < 2.0:
            move_phase = "IGNITION"
        elif expansion < 4.0:
            move_phase = "EARLY"
        elif expansion < 8.0:
            move_phase = "MID"
        else:
            move_phase = "EXTENDED"

    # ── phase_score (0-100) ───────────────────────────────────────────────
    # Component 1: liq expansion (0-40) — log2 scale so early gains dominate
    exp_score = min(40, int(math.log2(max(expansion, 1.0)) * 12))

    # Component 2: liq trend direction (0-25)
    trend_score = {"growing": 0, "flat": 12, "shrinking": 25}.get(liq_trend or "flat", 12)

    # Component 3: vol_acc health — higher va means earlier/stronger move (0-20)
    if   vol_acc >= 8.0: vacc_score = 0
    elif vol_acc >= 5.0: vacc_score = 5
    elif vol_acc >= 3.0: vacc_score = 10
    elif vol_acc >= 1.5: vacc_score = 15
    else:                vacc_score = 20

    # Component 4: pullback depth — deeper pullback = more spent (0-15)
    if   pullback_depth_pct < 30:  pb_score = 0
    elif pullback_depth_pct < 50:  pb_score = 5
    elif pullback_depth_pct < 70:  pb_score = 10
    else:                          pb_score = 15

    phase_score = min(100, exp_score + trend_score + vacc_score + pb_score)

    # ── entry_window ──────────────────────────────────────────────────────
    if move_phase == "CHURN":
        entry_window = "CLOSED"
    elif move_phase == "EXTENDED" and phase_score >= 55:
        entry_window = "CLOSED"
    elif move_phase in ("IGNITION", "EARLY") and phase_score < 40:
        entry_window = "OPEN"
    elif move_phase == "RELOAD" and phase_score < 50:
        entry_window = "OPEN"
    else:
        entry_window = "CLOSING"

    return move_phase, phase_score, entry_window


# ── Patch 248: Fuel-quality computation ──────────────────────────────────────

def _compute_fuel_quality(
    liq_current:          float,
    liq_floor:            float,
    peak_liq_last_window: float,
    liq_trend:            str,
    vol_acc:              float,
    top_holder_pct:       float,
) -> str:
    """
    Compute fuel_quality for one lifecycle record.

    Answers: is the energy behind this candidate real and broad, or thin/fading?

    Returns: STRONG | MODERATE | WEAK | TRAP

    ── TRAP override ───────────────────────────────────────────────────────────
    liq_current < FUEL_TRAP_LIQ ($20k) — absolute liq too thin for a real move
    regardless of expansion ratios or trend. Large % moves on tiny liq are noise.

    ── Additive score (max 90 before adjustment) ───────────────────────────────
    Component 1 — Liq retention (0–35):
        How well is current liq holding vs the reference peak?
        ref = peak_liq_last_window if set, else liq_current (neutral).
        retention = min(1.0, liq_current / ref)
          >= 0.90 → 35   (holding strong)
          >= 0.70 → 25   (modest bleed, structure intact)
          >= 0.50 → 15   (meaningful bleed)
          >= 0.30 → 5    (significant bleed, fading)
          <  0.30 → 0    (mostly gone)

    Component 2 — Liq trend direction (0–30):
        growing   → 30  (liq actively expanding)
        flat      → 15  (holding, not growing)
        shrinking →  0  (fuel draining)

    Component 3 — Volume acceleration level (0–25):
        vol_acc >= 6.0 → 25
        vol_acc >= 4.0 → 20
        vol_acc >= 2.5 → 15
        vol_acc >= 1.5 → 8
        vol_acc <  1.5 → 0

    ── Concentration adjustment ─────────────────────────────────────────────────
    top_holder_pct > 30% → −10
    top_holder_pct > 20% → −5
    otherwise            →  0

    ── Classification thresholds ────────────────────────────────────────────────
    STRONG:   score >= 60
    MODERATE: score >= 35
    WEAK:     score <  35
    """

    # TRAP: absolute liq too thin
    if (liq_current or 0) < FUEL_TRAP_LIQ:
        return "TRAP"

    # ── Component 1: Liq retention (0–35) ───────────────────────────────────
    ref = (peak_liq_last_window if peak_liq_last_window and peak_liq_last_window > 0
           else liq_current)
    retention = min(1.0, (liq_current or 0) / max(ref, 1.0))
    if   retention >= 0.90: retention_score = 35
    elif retention >= 0.70: retention_score = 25
    elif retention >= 0.50: retention_score = 15
    elif retention >= 0.30: retention_score = 5
    else:                   retention_score = 0

    # ── Component 2: Liq trend direction (0–30) ─────────────────────────────
    trend_score = {"growing": 30, "flat": 15, "shrinking": 0}.get(liq_trend or "flat", 15)

    # ── Component 3: Volume acceleration level (0–25) ────────────────────────
    va = vol_acc or 0.0
    if   va >= 6.0: vacc_score = 25
    elif va >= 4.0: vacc_score = 20
    elif va >= 2.5: vacc_score = 15
    elif va >= 1.5: vacc_score = 8
    else:           vacc_score = 0

    # ── Concentration adjustment ─────────────────────────────────────────────
    th = top_holder_pct or 0.0
    if   th > 30: holder_adj = -10
    elif th > 20: holder_adj = -5
    else:         holder_adj = 0

    score = retention_score + trend_score + vacc_score + holder_adj

    # ── Classification ───────────────────────────────────────────────────────
    if   score >= 60: return "STRONG"
    elif score >= 35: return "MODERATE"
    else:             return "WEAK"


def _compute_provisional_first_leg(
    *,
    first_leg_confirmed: bool,
    entry_window: str,
    fuel_quality: str,
    move_phase: str,
    vol_acc_current: float,
    top_holder_pct: float,
    scanner_score: float,
    scanner_regime: str,
) -> tuple[int, str | None, float]:
    """
    Early-but-credible bridge state for names that are structurally strong but
    have not yet earned a confirmed first leg.
    """
    if first_leg_confirmed:
        return 0, None, 0.0

    score = 0.0
    reasons: list[str] = []

    if str(entry_window or "").upper() == "OPEN":
        score += 20.0
        reasons.append("entry_window_open")
    if str(fuel_quality or "").upper() == "STRONG":
        score += 20.0
        reasons.append("fuel_strong")
    if str(move_phase or "").upper() in ("RELOAD", "EARLY", "IGNITION"):
        score += 20.0
        reasons.append(f"move_phase_{str(move_phase or '').lower()}")
    if float(vol_acc_current or 0.0) >= PROVISIONAL_MIN_VA:
        score += 15.0
        reasons.append("vol_acc_healthy")
    if float(top_holder_pct or 0.0) <= PROVISIONAL_MAX_TOPH:
        score += 10.0
        reasons.append("holder_ok")
    if float(scanner_score or 0.0) >= PROVISIONAL_MIN_SCORE:
        score += 15.0
        reasons.append("scanner_score_high")
    if str(scanner_regime or "").upper() == "NORMAL":
        score += 10.0
        reasons.append("normal_scanner_regime")

    provisional = int(
        str(entry_window or "").upper() == "OPEN"
        and str(fuel_quality or "").upper() == "STRONG"
        and str(move_phase or "").upper() in ("RELOAD", "EARLY", "IGNITION")
        and float(vol_acc_current or 0.0) >= PROVISIONAL_MIN_VA
        and float(top_holder_pct or 0.0) <= PROVISIONAL_MAX_TOPH
        and float(scanner_score or 0.0) >= PROVISIONAL_MIN_SCORE
        and str(scanner_regime or "").upper() == "NORMAL"
    )
    return provisional, (" + ".join(reasons) if provisional and reasons else None), round(score, 1)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _ts_to_dt(ts: str) -> datetime:
    raw = str(ts or "").strip()
    if not raw:
        raise ValueError(f"lifecycle: cannot parse timestamp {ts!r}")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    raise ValueError(f"lifecycle: cannot parse timestamp {ts!r}")


def _hours(earlier: datetime, later: datetime) -> float:
    return (later - earlier).total_seconds() / 3600.0


def _liq_trend(liqs: list) -> str:
    """Given a list of liq values oldest→newest, return growing/flat/shrinking."""
    if len(liqs) < 2:
        return "flat"
    oldest, newest = float(liqs[0] or 0), float(liqs[-1] or 0)
    if oldest == 0:
        return "flat"
    ratio = newest / oldest
    if ratio >= 1.10:
        return "growing"
    if ratio <= 0.90:
        return "shrinking"
    return "flat"


# ── Per-window stats ─────────────────────────────────────────────────────────

def _win_stats(rows: list) -> dict:
    liqs = [float(r["liquidity_usd"] or 0) for r in rows]
    vas  = [float(r["vol_acceleration"] or 0) for r in rows]
    rets = [
        float(r["return_24h_pct"])
        for r in rows
        if r.get("return_24h_pct") is not None
        and r.get("status") == "COMPLETE"
    ]
    return {
        "peak_liq":    max(liqs) if liqs else 0.0,
        "close_liq":   float(liqs[-1]) if liqs else 0.0,   # liq at window close
        "open_liq":    float(liqs[0])  if liqs else 0.0,   # liq at window open
        "peak_va":     max(vas)  if vas  else 0.0,
        "scan_count":  len(rows),
        "best_return": max(rets) if rets else None,
        "start_dt":    _ts_to_dt(rows[0]["scanned_at"]),
        "end_dt":      _ts_to_dt(rows[-1]["scanned_at"]),
    }


# ── Core computation ─────────────────────────────────────────────────────────

def _compute_symbol(symbol: str, rows: list, now: datetime) -> dict:
    """
    Given all MSO rows for one symbol (sorted by scanned_at ASC),
    return a dict ready to upsert into symbol_lifecycle.
    """
    # ── 1. Segment into windows ───────────────────────────────────────
    windows: list = []
    cur_win = [rows[0]]
    for r in rows[1:]:
        gap_h = _hours(_ts_to_dt(cur_win[-1]["scanned_at"]), _ts_to_dt(r["scanned_at"]))
        if gap_h > GAP_THRESHOLD_H:
            windows.append(cur_win)
            cur_win = [r]
        else:
            cur_win.append(r)
    windows.append(cur_win)

    n_windows   = len(windows)
    last_win    = windows[-1]
    last_row    = last_win[-1]
    last_scan_dt = _ts_to_dt(last_row["scanned_at"])
    hours_since_last_scan = _hours(last_scan_dt, now)

    # Is the last window currently open (last scan < 8h ago)?
    last_win_open = hours_since_last_scan < GAP_THRESHOLD_H

    # ── 2. Per-window stats ───────────────────────────────────────────
    wstats = [_win_stats(w) for w in windows]

    # ── 3. Aggregate across all windows ──────────────────────────────
    all_returns = [ws["best_return"] for ws in wstats if ws["best_return"] is not None]
    best_return_pct = max(all_returns) if all_returns else None

    first_leg_confirmed = any(
        ws["best_return"] is not None and ws["best_return"] >= FIRST_LEG_THRESH
        for ws in wstats
    )
    legs_with_positive_return = sum(
        1 for ws in wstats
        if ws["best_return"] is not None and ws["best_return"] > 0
    )
    multi_leg_confirmed = legs_with_positive_return >= 2

    # ── 4. Current state snapshot ────────────────────────────────────
    liq_current     = float(last_row["liquidity_usd"] or 0)
    vol_acc_current = float(last_row["vol_acceleration"] or 0)

    # Liq trend: last 3 scans oldest→newest
    recent_rows = rows[-3:]
    liq_trend = _liq_trend([float(r["liquidity_usd"] or 0) for r in recent_rows])

    # ── 5. Between-window state ───────────────────────────────────────
    #
    # If last window is OPEN (currently active):
    #   - "previous window" = wstats[-2] (if exists)
    #   - gap_before_current = hours between prev window close and cur window open
    #
    # If last window is CLOSED (in gap now):
    #   - "last completed window" = wstats[-1]
    #   - no "current window"

    if last_win_open and n_windows >= 2:
        prev_ws = wstats[-2]
        cur_ws  = wstats[-1]
        peak_liq_last_window = prev_ws["peak_liq"]
        gap_before_current_h = _hours(prev_ws["end_dt"], cur_ws["start_dt"])
    elif last_win_open and n_windows == 1:
        # Only one window and it's open — no previous window
        peak_liq_last_window = wstats[-1]["peak_liq"]
        gap_before_current_h = 0.0
    else:
        # In gap — last closed window is wstats[-1]
        peak_liq_last_window = wstats[-1]["peak_liq"]
        gap_before_current_h = 0.0

    # Pullback depth: how far current liq is below last window peak
    if peak_liq_last_window > 0:
        pullback_depth_pct = max(0.0, (peak_liq_last_window - liq_current) / peak_liq_last_window * 100.0)
    else:
        pullback_depth_pct = 0.0

    # Liq floor: minimum close_liq at the end of each completed window
    # (best proxy for between-window liq floor from available on-chain data)
    completed_wstats = wstats[:-1] if last_win_open else wstats
    if completed_wstats:
        liq_floor = min(ws["close_liq"] for ws in completed_wstats)
    else:
        liq_floor = liq_current

    # Survivor: liq floor held >= 25% of peak after legs
    if peak_liq_last_window > 0 and completed_wstats:
        survivor_confirmed = int(liq_floor >= peak_liq_last_window * SURVIVOR_RATIO)
    else:
        survivor_confirmed = 0

    # Hours since last window (0 if currently in window)
    hours_since_last_window = 0.0 if last_win_open else hours_since_last_scan

    # ── 6. Lifecycle state ────────────────────────────────────────────
    if last_win_open:
        # Currently in scanner
        if gap_before_current_h >= REVIVAL_GAP_H:
            state = "REVIVAL"
        elif vol_acc_current > VA_EXTENDED:
            state = "EXTENDED"
        elif vol_acc_current >= VA_ACTIVE_MIN:
            state = "ACTIVE"
        else:
            state = "COOLING"
    else:
        # In gap (not seen in last 8h+)
        if not first_leg_confirmed:
            state = "COOLING"       # never proved demand
        elif hours_since_last_scan >= DEAD_GAP_H:
            state = "DEAD"          # 30+ days dark
        elif pullback_depth_pct >= DEAD_PULLBACK_PCT and liq_current < MIN_LIQ_ALIVE:
            state = "DEAD"          # liq mostly gone
        elif hours_since_last_scan >= DORMANT_GAP_H:
            state = "DORMANT"       # 7–30d quiet, still alive
        else:
            state = "RELOAD"        # sweet spot: proved + gap + liq held

    # ── Patch 246: leg-intelligence phase ────────────────────────────────────
    move_phase, phase_score, entry_window = _compute_phase(
        lifecycle_state    = state,
        liq_current        = liq_current,
        liq_floor          = liq_floor,
        vol_acc            = vol_acc_current,
        liq_trend          = liq_trend,
        pullback_depth_pct = pullback_depth_pct,
        hours_since_last_scan = hours_since_last_scan,
    )

    # ── Patch 248: fuel quality ───────────────────────────────────────────────
    top_holder_pct = float(last_row.get("top_holder_pct") or 0)
    scanner_score = float(last_row.get("score") or 0.0)
    scanner_regime = str(last_row.get("scanner_regime") or "NORMAL")
    fuel_quality = _compute_fuel_quality(
        liq_current          = liq_current,
        liq_floor            = liq_floor,
        peak_liq_last_window = peak_liq_last_window,
        liq_trend            = liq_trend,
        vol_acc              = vol_acc_current,
        top_holder_pct       = top_holder_pct,
    )
    provisional_first_leg, provisional_reason, provisional_score = _compute_provisional_first_leg(
        first_leg_confirmed = bool(first_leg_confirmed),
        entry_window = entry_window,
        fuel_quality = fuel_quality,
        move_phase = move_phase,
        vol_acc_current = vol_acc_current,
        top_holder_pct = top_holder_pct,
        scanner_score = scanner_score,
        scanner_regime = scanner_regime,
    )

    now_s = now.strftime("%Y-%m-%d %H:%M:%S")
    return {
        "symbol":                  symbol,
        "lifecycle_state":         state,
        "n_windows":               n_windows,
        "best_return_pct":         best_return_pct,
        "liq_floor":               round(liq_floor, 2),
        "liq_current":             round(liq_current, 2),
        "liq_trend":               liq_trend,
        "pullback_depth_pct":      round(pullback_depth_pct, 1),
        "hours_since_last_window": round(hours_since_last_window, 1),
        "hours_since_last_scan":   round(hours_since_last_scan, 1),
        "peak_liq_last_window":    round(peak_liq_last_window, 2),
        "vol_acc_current":         round(vol_acc_current, 2),
        "first_leg_confirmed":     int(first_leg_confirmed),
        "multi_leg_confirmed":     int(multi_leg_confirmed),
        "survivor_confirmed":      survivor_confirmed,
        "last_computed_at":        now_s,
        # Patch 244: freshness tracking
        "state_entered_at":        now_s,          # used by _upsert CASE WHEN on state change
        "first_seen_at":           rows[0]["scanned_at"],  # oldest MSO row for this symbol
        # Patch 246: leg-intelligence phase
        "move_phase":              move_phase,
        "phase_score":             phase_score,
        "entry_window":            entry_window,
        # Patch 248: fuel quality
        "fuel_quality":            fuel_quality,
        "provisional_first_leg":   int(provisional_first_leg),
        "provisional_reason":      provisional_reason,
        "provisional_score":       float(provisional_score or 0.0),
    }


# ── DB write ─────────────────────────────────────────────────────────────────

def _upsert(conn, lc: dict) -> None:
    conn.execute("""
        INSERT INTO symbol_lifecycle (
            symbol, lifecycle_state, n_windows, best_return_pct,
            liq_floor, liq_current, liq_trend, pullback_depth_pct,
            hours_since_last_window, hours_since_last_scan, peak_liq_last_window,
            vol_acc_current, first_leg_confirmed, multi_leg_confirmed,
            survivor_confirmed, last_computed_at,
            state_entered_at, first_seen_at,
            move_phase, phase_score, entry_window,
            fuel_quality,
            provisional_first_leg, provisional_reason, provisional_score,
            mint
        ) VALUES (
            :symbol, :lifecycle_state, :n_windows, :best_return_pct,
            :liq_floor, :liq_current, :liq_trend, :pullback_depth_pct,
            :hours_since_last_window, :hours_since_last_scan, :peak_liq_last_window,
            :vol_acc_current, :first_leg_confirmed, :multi_leg_confirmed,
            :survivor_confirmed, :last_computed_at,
            :state_entered_at, :first_seen_at,
            :move_phase, :phase_score, :entry_window,
            :fuel_quality,
            :provisional_first_leg, :provisional_reason, :provisional_score,
            :mint
        )
        ON CONFLICT(symbol) DO UPDATE SET
            lifecycle_state          = excluded.lifecycle_state,
            n_windows                = excluded.n_windows,
            best_return_pct          = excluded.best_return_pct,
            liq_floor                = excluded.liq_floor,
            liq_current              = excluded.liq_current,
            liq_trend                = excluded.liq_trend,
            pullback_depth_pct       = excluded.pullback_depth_pct,
            hours_since_last_window  = excluded.hours_since_last_window,
            hours_since_last_scan    = excluded.hours_since_last_scan,
            peak_liq_last_window     = excluded.peak_liq_last_window,
            vol_acc_current          = excluded.vol_acc_current,
            first_leg_confirmed      = excluded.first_leg_confirmed,
            multi_leg_confirmed      = excluded.multi_leg_confirmed,
            survivor_confirmed       = excluded.survivor_confirmed,
            last_computed_at         = excluded.last_computed_at,
            -- Patch 244: reset state_entered_at only when lifecycle_state changes
            state_entered_at         = CASE
                WHEN excluded.lifecycle_state != symbol_lifecycle.lifecycle_state
                THEN excluded.last_computed_at
                ELSE symbol_lifecycle.state_entered_at
            END,
            -- first_seen_at is set once and never overwritten
            first_seen_at            = COALESCE(symbol_lifecycle.first_seen_at, excluded.first_seen_at),
            -- Patch 246: phase fields always updated (recomputed every cycle)
            move_phase               = excluded.move_phase,
            phase_score              = excluded.phase_score,
            entry_window             = excluded.entry_window,
            -- Patch 248: fuel quality always updated (recomputed every cycle)
            fuel_quality             = excluded.fuel_quality,
            provisional_first_leg    = excluded.provisional_first_leg,
            provisional_reason       = excluded.provisional_reason,
            provisional_score        = excluded.provisional_score,
            -- Patch 251: mint updated each cycle (tracks most recent mint for this symbol)
            mint                     = excluded.mint
    """, lc)


# ── Patch 269: recycled-ticker collision detection ────────────────────────────

def _detect_collision(conn, symbol: str, incoming_mint: "str | None") -> "str | None":
    """
    Returns the *existing* mint if this upsert would silently overwrite a
    different token under the same symbol (recycled-ticker collision).

    Returns None when:
      - incoming_mint is None (no mint available to compare)
      - no existing row for this symbol
      - existing row has no mint stored yet
      - existing mint == incoming_mint (same token, safe update)

    Returns existing_mint (non-None str) when:
      - existing row has a non-null mint that differs from incoming_mint
        → caller should quarantine / skip the write
    """
    if not incoming_mint:
        return None
    row = conn.execute(
        "SELECT mint FROM symbol_lifecycle WHERE symbol = ?", (symbol,)
    ).fetchone()
    if row is None or not row[0] or row[0] == incoming_mint:
        return None
    existing_mint = row[0]

    # Allow takeover when the incoming mint is clearly the active symbol owner
    # and the stored mint is stale. This preserves recycled-ticker protection
    # while letting active names like BULL replace old symbol owners.
    try:
        recent = conn.execute(
            """
            SELECT mint, MAX(scanned_at) AS latest_scanned_at
            FROM memecoin_signal_outcomes
            WHERE symbol = ?
              AND mint IN (?, ?)
            GROUP BY mint
            """,
            (symbol, existing_mint, incoming_mint),
        ).fetchall()
        latest_by_mint = {
            str(r[0] or ""): str(r[1] or "")
            for r in recent
            if r[0] and r[1]
        }
        existing_latest = latest_by_mint.get(str(existing_mint))
        incoming_latest = latest_by_mint.get(str(incoming_mint))
        if incoming_latest and (
            not existing_latest or incoming_latest > existing_latest
        ):
            log.info(
                "lifecycle: symbol %s mint takeover allowed (old=%.16s… latest=%s, new=%.16s… latest=%s)",
                symbol,
                existing_mint,
                existing_latest or "none",
                incoming_mint,
                incoming_latest,
            )
            return None
    except Exception:
        pass

    return existing_mint   # conflict: existing mint differs


# ── Entry point ───────────────────────────────────────────────────────────────

def compute_lifecycle() -> int:
    """
    Read all memecoin_signal_outcomes rows, compute per-symbol lifecycle state,
    write to symbol_lifecycle. Returns number of symbols processed.

    Called from _memecoin_scan_loop in main.py after each scanner run.
    """
    import sys
    import os
    # Ensure engine root is importable when called from main.py context
    _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _root not in sys.path:
        sys.path.insert(0, _root)

    from utils.db import get_conn, is_database_locked_error, with_db_retry  # type: ignore  # noqa: PLC0415

    def _ensure_schema() -> None:
        with get_conn() as conn:
            try:
                conn.execute(
                    "ALTER TABLE symbol_lifecycle ADD COLUMN mint TEXT DEFAULT NULL"
                )
                log.info("lifecycle: added mint column to symbol_lifecycle (Patch 269)")
            except sqlite3.OperationalError:
                pass   # column already present

    with_db_retry(_ensure_schema, retries=4, base_sleep_s=0.35)

    with get_conn() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT symbol, mint, scanned_at, liquidity_usd, vol_acceleration,
                   return_24h_pct, status, top_holder_pct, score,
                   COALESCE(scanner_regime, 'NORMAL') AS scanner_regime
            FROM   memecoin_signal_outcomes
            ORDER  BY symbol ASC, scanned_at ASC
        """).fetchall()

    if not rows:
        log.debug("lifecycle: no MSO rows, skipping")
        return 0

    # Group by symbol; track most-recent mint per symbol (Patch 251)
    by_symbol: dict    = defaultdict(list)
    latest_mint: dict  = {}   # symbol → most recent non-null mint
    for r in rows:
        by_symbol[r["symbol"]].append(dict(r))
        if r["mint"]:   # rows are scanned_at ASC — last write wins = most recent
            latest_mint[r["symbol"]] = r["mint"]

    now = datetime.now(timezone.utc)
    records: list[dict] = []
    errors = 0

    # Compute outside the write transaction. Holding SQLite's writer lock while
    # deriving ~2.5k lifecycle rows caused dashboard snapshot writes to pile up.
    for symbol, sym_rows in by_symbol.items():
        try:
            lc = _compute_symbol(symbol, sym_rows, now)
            lc["mint"] = latest_mint.get(symbol)
            records.append(lc)
        except Exception as exc:
            log.warning("lifecycle: failed to compute %s: %s", symbol, exc)
            errors += 1

    processed = 0
    quarantine = 0
    chunk_size = max(50, int(os.getenv("LIFECYCLE_WRITE_CHUNK_SIZE", "500")))

    def _write_lifecycle_chunk(chunk: list[dict]) -> tuple[int, int]:
        chunk_processed = 0
        chunk_quarantine = 0
        with get_conn() as conn:
            conn.row_factory = sqlite3.Row
            for lc in chunk:
                existing_mint = _detect_collision(conn, lc["symbol"], lc["mint"])
                if existing_mint is not None:
                    log.warning(
                        "lifecycle: QUARANTINE %s — recycled-ticker collision "
                        "(prev_mint=%.16s…  new_mint=%.16s…), skipping upsert",
                        lc["symbol"], existing_mint, lc["mint"],
                    )
                    chunk_quarantine += 1
                    continue
                _upsert(conn, lc)
                chunk_processed += 1
        return chunk_processed, chunk_quarantine

    for idx in range(0, len(records), chunk_size):
        chunk = records[idx : idx + chunk_size]
        try:
            written, skipped = with_db_retry(
                lambda chunk=chunk: _write_lifecycle_chunk(chunk),
                retries=5,
                base_sleep_s=0.4,
            )
            processed += int(written or 0)
            quarantine += int(skipped or 0)
        except Exception as exc:
            if is_database_locked_error(exc):
                log.warning(
                    "lifecycle: failed to write chunk %d-%d after lock retries: %s",
                    idx + 1,
                    idx + len(chunk),
                    exc,
                )
            else:
                log.warning("lifecycle: failed to write chunk %d-%d: %s", idx + 1, idx + len(chunk), exc)
            errors += len(chunk)

    log.info(
        "lifecycle: %d symbols computed, %d quarantined, %d errors",
        processed, quarantine, errors,
    )
    return processed
