"""health_monitor.py — System Health Watchdog (Patch 118).

Runs every 60 s inside _perp_monitor_loop.

Checks:
  1. DB connectivity
  2. Agent heartbeat health (stalled/init agents)
  3. Memecoin scan freshness (warn if > 15 min since last scan)
  4. Open position sanity (warn if > 20 open memecoins)

Writes result to kv_store['system_health'] and orchestrator in-memory.

Daily digest (once per UTC day at first run after midnight):
  Logs a summary of the day's PnL, agent health, and open positions
  to MEMORY.md so you can review it in the Feed section.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone

# ── Imports ────────────────────────────────────────────────────────────────────


def _get_conn():
    from utils.db import get_conn  # type: ignore
    return get_conn()


def _orch():
    from utils import orchestrator  # type: ignore
    return orchestrator


# ── Helpers ────────────────────────────────────────────────────────────────────

def _kv_get(key: str) -> str | None:
    try:
        with _get_conn() as conn:
            row = conn.execute(
                "SELECT value FROM kv_store WHERE key=?", (key,)
            ).fetchone()
            return row[0] if row else None
    except Exception:
        return None


def _kv_set(key: str, value: str) -> None:
    try:
        with _get_conn() as conn:
            conn.execute(
                "INSERT INTO kv_store (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
    except Exception:
        pass


# ── Core watchdog step ─────────────────────────────────────────────────────────

def health_watchdog_step() -> None:
    """Run one health check cycle. Called every 60 s from _perp_monitor_loop."""
    ts_utc = datetime.now(timezone.utc)
    issues: list[str] = []
    warnings: list[str] = []

    # ── 1. DB connectivity ────────────────────────────────────────────────────
    db_ok = False
    try:
        with _get_conn() as conn:
            conn.execute("SELECT 1").fetchone()
        db_ok = True
    except Exception as exc:
        issues.append(f"DB_CONN: {exc}")

    # ── 2. Agent health ───────────────────────────────────────────────────────
    orch = _orch()
    orch.heartbeat("health_watchdog")
    orch.heartbeat("data_integrity")

    agents = orch.get_status()
    stalled: list[str] = []
    slow: list[str] = []
    for ag in agents:
        if ag["health"] == "stalled":
            stalled.append(ag["name"])
        elif ag["health"] == "slow":
            slow.append(ag["name"])

    # Only flag as issue if core agents are stalled
    _core = {"trading", "monitoring", "memecoin_monitor"}
    stalled_core = [a for a in stalled if a in _core]
    if stalled_core:
        issues.append(f"STALLED: {', '.join(stalled_core)}")
    if slow:
        warnings.append(f"SLOW: {', '.join(slow)}")
    if stalled:
        warnings.append(f"stalled(non-core): {', '.join(a for a in stalled if a not in _core)}")

    # ── 3. Memecoin scan freshness ────────────────────────────────────────────
    scan_age_min: float | None = None
    try:
        cached = _kv_get("memecoin_scan_cache")
        if cached:
            data = json.loads(cached)
            scan_ts_str = data.get("_ts") if isinstance(data, dict) else None
            if not scan_ts_str and isinstance(data, list) and data:
                # Signals list — pick _ts from first element or separate key
                scan_ts_str = None  # no embedded timestamp in list format
            if scan_ts_str:
                scan_ts = datetime.fromisoformat(scan_ts_str.replace("Z", "+00:00"))
                scan_age_min = (ts_utc - scan_ts).total_seconds() / 60
        # Also check memecoin_scan agent heartbeat
        scan_agent = next((a for a in agents if a["name"] == "memecoin_scan"), None)
        if scan_agent and scan_agent["last_beat_ago_s"] is not None:
            agent_age_min = scan_agent["last_beat_ago_s"] / 60
            # Use the minimum (most recent evidence)
            if scan_age_min is None or agent_age_min < scan_age_min:
                scan_age_min = agent_age_min
    except Exception:
        pass

    if scan_age_min is not None and scan_age_min > 15:
        warnings.append(f"SCAN_STALE: {scan_age_min:.0f}m ago")

    # ── 4. Open memecoin position count ───────────────────────────────────────
    open_meme_count = 0
    try:
        with _get_conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM memecoin_trades WHERE status='OPEN'"
            ).fetchone()
            open_meme_count = row[0] if row else 0
    except Exception:
        pass

    if open_meme_count > 20:
        warnings.append(f"OPEN_MEME_HIGH: {open_meme_count} positions")

    # ── 5. Open perp position count vs max ───────────────────────────────────
    try:
        with _get_conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM perp_positions WHERE status='OPEN'"
            ).fetchone()
            open_perp = row[0] if row else 0
        if open_perp > 15:
            warnings.append(f"OPEN_PERP_HIGH: {open_perp} positions")
    except Exception:
        pass

    # ── Determine overall status ──────────────────────────────────────────────
    if not db_ok or len(issues) > 0:
        status = "CRITICAL"
    elif len(warnings) >= 3:
        status = "DEGRADED"
    elif warnings:
        status = "WARN"
    else:
        status = "HEALTHY"

    # ── Write to kv_store ─────────────────────────────────────────────────────
    result = {
        "status": status,
        "ts": ts_utc.isoformat() + "Z",
        "db": db_ok,
        "issues": issues,
        "warnings": warnings,
        "agents_total": len(agents),
        "agents_stalled": len(stalled),
        "agents_slow": len(slow),
        "scan_age_min": round(scan_age_min, 1) if scan_age_min is not None else None,
        "open_meme": open_meme_count,
    }
    _kv_set("system_health", json.dumps(result))

    # ── Also store in orchestrator in-memory ──────────────────────────────────
    orch.set_health_status(result)

    # ── Telegram alert on CRITICAL or DEGRADED (Patch 121) ───────────────────
    try:
        from utils.telegram_alerts import send_telegram_sync, should_rate_limit  # type: ignore
        if status in ("CRITICAL", "DEGRADED"):
            if not should_rate_limit("health_alert", 600):   # max once per 10 min
                emoji = "🔴" if status == "CRITICAL" else "🟡"
                body  = " | ".join(issues + warnings) or "No details"
                send_telegram_sync(f"System {status}", body, emoji)
    except Exception:
        pass

    # ── Daily digest ─────────────────────────────────────────────────────────
    _maybe_daily_digest(ts_utc, result, agents)


# ── Daily digest ───────────────────────────────────────────────────────────────

def _maybe_daily_digest(ts_utc: datetime, health: dict, agents: list[dict]) -> None:
    """Write a daily summary to MEMORY.md once per UTC day (after 00:05 UTC)."""
    today_str = ts_utc.strftime("%Y-%m-%d")
    # Only fire after 00:05 UTC to avoid midnight edge case
    if ts_utc.hour == 0 and ts_utc.minute < 5:
        return

    last_date = _kv_get("health_last_digest_date")
    if last_date == today_str:
        return

    _kv_set("health_last_digest_date", today_str)

    try:
        digest = _build_digest(ts_utc, health, agents)
        _orch().append_memory("HEALTH_WATCHDOG", digest)
    except Exception:
        pass


def _build_digest(ts_utc: datetime, health: dict, agents: list[dict]) -> str:
    """Build a human-readable daily digest string."""
    lines: list[str] = [f"=== DAILY HEALTH DIGEST — {ts_utc.strftime('%Y-%m-%d')} ==="]
    lines.append(f"System status: {health['status']}")

    if health["issues"]:
        lines.append(f"⚠ Issues: {' | '.join(health['issues'])}")
    if health["warnings"]:
        lines.append(f"→ Warnings: {' | '.join(health['warnings'])}")

    # Agent summary
    alive_agents = [a["name"] for a in agents if a["health"] == "alive"]
    stalled_agents = [a["name"] for a in agents if a["health"] == "stalled"]
    lines.append(f"Agents — alive: {len(alive_agents)}, stalled: {len(stalled_agents)}")
    if stalled_agents:
        lines.append(f"  stalled: {', '.join(stalled_agents)}")

    # Perp PnL summary (last 24h)
    try:
        with _get_conn() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) as cnt,
                       SUM(CASE WHEN pnl_pct > 0 THEN 1 ELSE 0 END) as wins,
                       ROUND(AVG(pnl_pct), 2) as avg_pnl,
                       ROUND(SUM(pnl_usd), 2) as total_usd
                FROM perp_positions
                WHERE status='CLOSED'
                  AND closed_ts_utc >= datetime('now', '-24 hours')
                """
            ).fetchone()
            if row and row[0]:
                wr = round(row[1] / row[0] * 100) if row[0] else 0
                lines.append(
                    f"Perp trades (24h): {row[0]} closed | "
                    f"win {wr}% | avg {row[2]}% | P&L ${row[3]}"
                )
    except Exception:
        pass

    # Memecoin PnL summary (last 24h)
    try:
        with _get_conn() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) as cnt,
                       SUM(CASE WHEN pnl_pct > 0 THEN 1 ELSE 0 END) as wins,
                       ROUND(AVG(pnl_pct), 2) as avg_pnl,
                       ROUND(SUM(pnl_usd), 2) as total_usd
                FROM memecoin_trades
                WHERE status='CLOSED'
                  AND closed_ts_utc >= datetime('now', '-24 hours')
                """
            ).fetchone()
            if row and row[0]:
                wr = round(row[1] / row[0] * 100) if row[0] else 0
                lines.append(
                    f"Memecoin trades (24h): {row[0]} closed | "
                    f"win {wr}% | avg {row[2]}% | P&L ${row[3]}"
                )
    except Exception:
        pass

    # Open positions
    try:
        with _get_conn() as conn:
            open_perp = conn.execute(
                "SELECT COUNT(*) FROM perp_positions WHERE status='OPEN'"
            ).fetchone()[0]
            open_meme = conn.execute(
                "SELECT COUNT(*) FROM memecoin_trades WHERE status='OPEN'"
            ).fetchone()[0]
        lines.append(f"Open positions — perp: {open_perp}, memecoin: {open_meme}")
    except Exception:
        pass

    return "\n".join(lines)
