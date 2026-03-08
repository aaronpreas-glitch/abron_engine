"""agent_coordinator.py — Shared brain layer for all agents (Patch 120).

Three responsibilities shared across every agent in the system:

1. get_fear_greed()
   Single source of truth for market sentiment.
   Cached in kv_store['shared_fear_greed'] for 15 min so every agent
   reads the same value without triggering duplicate API calls every 60s.

2. data_integrity_step()
   Called every 5 min from _perp_monitor_loop.
   Real checks: outcome fill rate, scan freshness, DB table sanity.
   Heartbeats 'data_integrity'. Writes to kv_store['data_integrity_status'].

3. research_step()
   Called every 4h from _research_loop.
   Synthesizes memecoin signal outcomes into a human-readable digest:
     - Score bucket win rates
     - Rug label performance
     - Tuner threshold status
     - Market context (F&G, open positions)
   Writes to MEMORY.md via orchestrator.append_memory('RESEARCH', ...).
   Heartbeats 'research'.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone

import requests

FEAR_GREED_KEY      = "shared_fear_greed"
DATA_INTEGRITY_KEY  = "data_integrity_status"
FEAR_GREED_URL      = "https://api.alternative.me/fng/?limit=1"


# ── Internal helpers ──────────────────────────────────────────────────────────

def _get_conn():
    from utils.db import get_conn  # type: ignore
    return get_conn()


def _orch():
    from utils import orchestrator  # type: ignore
    return orchestrator


def _kv_get(key: str):
    try:
        with _get_conn() as conn:
            row = conn.execute(
                "SELECT value FROM kv_store WHERE key=?", (key,)
            ).fetchone()
            return json.loads(row[0]) if row else None
    except Exception:
        return None


def _kv_set(key: str, value: dict) -> None:
    try:
        with _get_conn() as conn:
            conn.execute(
                "INSERT INTO kv_store (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(value)),
            )
    except Exception:
        pass


# ── 1. Shared Fear & Greed cache ──────────────────────────────────────────────

def get_fear_greed(cache_ttl_min: int = 15) -> dict:
    """Return the current Crypto Fear & Greed index.

    Caches in kv_store['shared_fear_greed'] for cache_ttl_min minutes so all
    agents (tier_manager, scanner, health_watchdog) read the same value without
    making independent API calls every cycle.

    Returns {value: int|None, label: str, favorable: bool, _ts: float}.
    Never fails — returns favorable=True on error so no agent is blocked.
    """
    # Read from cache
    cached = _kv_get(FEAR_GREED_KEY)
    if cached:
        age_min = (time.time() - cached.get("_ts", 0)) / 60
        if age_min < cache_ttl_min:
            return cached

    # Fetch fresh
    try:
        r = requests.get(FEAR_GREED_URL, timeout=6)
        d = r.json()["data"][0]
        value = int(d["value"])
        label = d["value_classification"]
        result: dict = {
            "value":     value,
            "label":     label,
            "favorable": value > 25,  # extreme fear <= 25 → don't auto-deploy capital
            "_ts":       time.time(),
        }
        _kv_set(FEAR_GREED_KEY, result)
        _check_fg_crossing(cached, result)   # Patch 142 — threshold crossing alert
        return result
    except Exception:
        return {"value": None, "label": "UNKNOWN", "favorable": True, "_ts": time.time()}


def _check_fg_crossing(prev: dict, curr: dict) -> None:
    """Fire Telegram alert when F&G crosses the 25 threshold (Patch 142).

    Fires once per crossing event:
      ≤25 → >25 : market recovering, live gates may be opening
      >25 → ≤25 : dropped into extreme fear, buffer auto-deploy paused
    """
    try:
        if not isinstance(prev, dict):
            return
        prev_val = prev.get("value")
        curr_val = curr.get("value")
        if prev_val is None or curr_val is None:
            return
        from utils.telegram_alerts import send_telegram_sync, should_rate_limit  # type: ignore
        if prev_val <= 25 and curr_val > 25:
            if not should_rate_limit("fg_cross_up", 3600):
                send_telegram_sync(
                    "F&G Crossed Above 25 🟢",
                    (
                        f"Fear & Greed: {prev_val} → {curr_val} ({curr.get('label', '')})\n"
                        "Market recovering — check memecoin live criteria."
                    ),
                    "🟢",
                )
        elif prev_val > 25 and curr_val <= 25:
            if not should_rate_limit("fg_cross_down", 3600):
                send_telegram_sync(
                    "F&G Dropped to Extreme Fear 🔴",
                    (
                        f"Fear & Greed: {prev_val} → {curr_val} ({curr.get('label', '')})\n"
                        "Profit buffer auto-deploy paused."
                    ),
                    "🔴",
                )
    except Exception:
        pass


# ── 2. Data integrity check ───────────────────────────────────────────────────

def data_integrity_step() -> dict:
    """Real data integrity checks — runs every 5 min.

    Checks:
    - Memecoin outcome fill rate (are 1h returns being populated?)
    - Scan agent freshness (is the scanner still running?)
    - DB table counts (total vs complete outcomes)

    Heartbeats 'data_integrity'. Writes to kv_store['data_integrity_status'].
    """
    orch = _orch()
    orch.heartbeat("data_integrity")

    ts_utc = datetime.now(timezone.utc)
    issues: list[str] = []

    # ── Check 1: outcome fill rate ────────────────────────────────────────────
    unfilled_1h = 0
    total_outcomes = 0
    complete_outcomes = 0
    try:
        with _get_conn() as conn:
            unfilled_1h = conn.execute("""
                SELECT COUNT(*) FROM memecoin_signal_outcomes
                WHERE status = 'PENDING'
                  AND return_1h_pct IS NULL
                  AND scanned_at < datetime('now', '-1 hour')
            """).fetchone()[0]

            row = conn.execute(
                "SELECT COUNT(*) FROM memecoin_signal_outcomes"
            ).fetchone()
            total_outcomes = row[0] if row else 0

            row = conn.execute(
                "SELECT COUNT(*) FROM memecoin_signal_outcomes WHERE status='COMPLETE'"
            ).fetchone()
            complete_outcomes = row[0] if row else 0
    except Exception:
        pass

    if unfilled_1h > 0:
        issues.append(f"{unfilled_1h} outcomes > 1h old with missing 1h return (price fetch may be failing)")

    # ── Check 2: scan freshness ───────────────────────────────────────────────
    scan_age_min: float | None = None
    try:
        agents = orch.get_status()
        scan_agent = next((a for a in agents if a["name"] == "memecoin_scan"), None)
        if scan_agent and scan_agent.get("last_beat_ago_s") is not None:
            scan_age_min = scan_agent["last_beat_ago_s"] / 60
    except Exception:
        pass

    if scan_age_min is not None and scan_age_min > 10:
        issues.append(f"memecoin_scan last ran {scan_age_min:.0f}m ago (expected ≤ 5m)")

    # ── Check 3: open position sanity ─────────────────────────────────────────
    try:
        with _get_conn() as conn:
            open_perp = conn.execute(
                "SELECT COUNT(*) FROM perp_positions WHERE status='OPEN'"
            ).fetchone()[0]
            open_meme = conn.execute(
                "SELECT COUNT(*) FROM memecoin_trades WHERE status='OPEN'"
            ).fetchone()[0]
    except Exception:
        open_perp = open_meme = 0

    # ── Write result ──────────────────────────────────────────────────────────
    result = {
        "status":            "OK" if not issues else "DEGRADED",
        "ts":                ts_utc.isoformat() + "Z",
        "unfilled_1h":       unfilled_1h,
        "scan_age_min":      round(scan_age_min, 1) if scan_age_min is not None else None,
        "total_outcomes":    total_outcomes,
        "complete_outcomes": complete_outcomes,
        "open_perp":         open_perp,
        "open_meme":         open_meme,
        "issues":            issues,
    }
    _kv_set(DATA_INTEGRITY_KEY, result)
    return result


# ── 3. Research synthesis ─────────────────────────────────────────────────────

def research_step() -> None:
    """4h synthesis of memecoin learning data — writes to MEMORY.md.

    Aggregates:
    - Score bucket win rates (4h return > +30%)
    - Rug label performance
    - Current tuner threshold status + confidence
    - Market context (F&G, open positions)

    Heartbeats 'research'. Visible in the Memory Feed section of the dashboard.
    """
    orch = _orch()
    orch.heartbeat("research")

    ts_utc = datetime.now(timezone.utc)
    import os as _os
    _paper   = _os.getenv("MEMECOIN_DRY_RUN", "true").lower() == "true"
    _auto    = _os.getenv("MEMECOIN_AUTO_BUY", "false").lower() == "true"
    _mode    = "PAPER MODE — studying mechanics, no real money" if _paper else "LIVE MODE"
    _buy_str = "AUTO-BUY ON" if _auto else "AUTO-BUY OFF (observing)"
    lines: list[str] = [
        f"=== RESEARCH SYNTHESIS [{ts_utc.strftime('%Y-%m-%d %H:%M UTC')}] ===",
        f"Mode: {_mode} | {_buy_str}",
    ]

    # ── Outcome sample summary ────────────────────────────────────────────────
    rows = []
    try:
        with _get_conn() as conn:
            rows = conn.execute("""
                SELECT score, return_4h_pct, rug_label, bought
                FROM memecoin_signal_outcomes
                WHERE status = 'COMPLETE' AND return_4h_pct IS NOT NULL
                ORDER BY scanned_at DESC
                LIMIT 100
            """).fetchall()
    except Exception:
        pass

    n = len(rows)
    if n == 0:
        lines.append("Samples: 0 complete — learning loop needs more time (need 20+)")
    else:
        bought_ct = sum(1 for r in rows if r["bought"])
        lines.append(f"Samples: {n} complete | {bought_ct} bought | {n - bought_ct} watched")

        # Score bucket win rates
        lines.append("Score performance (4h >+30% win rate):")
        for lo, hi in [(70, 999), (60, 70), (50, 60), (0, 50)]:
            bucket = [r for r in rows if lo <= (r["score"] or 0) < hi]
            if bucket:
                wins = sum(1 for r in bucket if (r["return_4h_pct"] or 0) > 30)
                wr   = round(wins / len(bucket) * 100)
                lbl  = f"score {lo}+" if hi == 999 else f"score {lo}-{hi-1}"
                lines.append(f"  {lbl:13}: {len(bucket):3} samples  {wr:3}% win rate")

        # Rug label breakdown
        lines.append("Rug label breakdown:")
        for label in ("GOOD", "WARN", "UNKNOWN"):
            subset = [r for r in rows if r["rug_label"] == label]
            if subset:
                wins = sum(1 for r in subset if (r["return_4h_pct"] or 0) > 30)
                wr   = round(wins / len(subset) * 100)
                lines.append(f"  {label:8}: {len(subset):3} samples  {wr:3}% win rate")

    # ── Tuner status ──────────────────────────────────────────────────────────
    try:
        lt = _kv_get("memecoin_learned_thresholds")
        if lt:
            thr  = lt.get("thresholds", {})
            conf = lt.get("confidence", "none")
            ns   = lt.get("sample_size", 0)
            lines.append(
                f"Tuner: score_min={thr.get('score_min', 'default')} "
                f"vacc_min={thr.get('vol_accel_min', 'default')} "
                f"confidence={conf} ({ns} samples)"
            )
        else:
            lines.append("Tuner: no learned thresholds yet — need 20 complete samples")
    except Exception:
        pass

    # ── Spot portfolio summary (Patch 128) ───────────────────────────────────
    try:
        from utils.spot_accumulator import get_portfolio_state  # type: ignore
        _sp = get_portfolio_state()
        if _sp.get("total_invested", 0) > 0:
            lines.append(
                f"Spot: invested=${_sp['total_invested']:.0f} "
                f"value=${_sp['total_value']:.0f} "
                f"PnL={_sp['total_pnl_pct']:+.1f}% ({_sp['holdings_count']} coins)"
            )
    except Exception:
        pass

    # ── Narrative momentum refresh (Patch 127) ────────────────────────────────
    try:
        from utils.narrative_momentum import update_narrative_momentum  # type: ignore
        _nm = update_narrative_momentum()
        lines.append(
            f"Narrative: {len(_nm.get('coingecko', []))} CoinGecko trending "
            f"+ {len(_nm.get('dexscreener', []))} DexScreener Solana boosted"
        )
    except Exception:
        pass

    # ── Market context ────────────────────────────────────────────────────────
    fg = get_fear_greed()
    lines.append(f"Market: F&G={fg['value']} ({fg['label']}) — favorable={fg['favorable']}")

    # ── Open position summary ─────────────────────────────────────────────────
    try:
        with _get_conn() as conn:
            open_perp = conn.execute(
                "SELECT COUNT(*) FROM perp_positions WHERE status='OPEN'"
            ).fetchone()[0]
            open_meme = conn.execute(
                "SELECT COUNT(*) FROM memecoin_trades WHERE status='OPEN'"
            ).fetchone()[0]
        lines.append(f"Positions: {open_perp} perp OPEN | {open_meme} memecoin OPEN")
    except Exception:
        pass

    # ── Write to MEMORY.md ────────────────────────────────────────────────────
    orch.append_memory("RESEARCH", "\n".join(lines))

    # Telegram research digest (Patch 121 — max once per 4h)
    try:
        from utils.telegram_alerts import send_telegram_sync, should_rate_limit  # type: ignore
        if not should_rate_limit("research", 14400):
            send_telegram_sync("Research Synthesis 🧠", "\n".join(lines[:8]), "🧠")
    except Exception:
        pass
