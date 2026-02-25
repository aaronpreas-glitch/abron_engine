#!/usr/bin/env python3
"""
morning_brief.py — Daily Claude-generated trading brief via Telegram.

Pulls a snapshot of the engine DB, sends it to Claude Haiku, and pushes
the response to your Telegram chat. Run via cron at 08:00 UTC daily.

Cron entry:
    0 8 * * * /root/memecoin_engine/.venv/bin/python /root/memecoin_engine/morning_brief.py >> /root/memecoin_engine/logs/morning_brief.log 2>&1

Requirements: httpx, python-telegram-bot (already installed in engine venv)
"""
from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

# ── Config ────────────────────────────────────────────────────────────────────

BASE_DIR    = Path(__file__).resolve().parent
DB_PATH     = BASE_DIR / "data_storage" / "engine.db"
ENV_PATH    = BASE_DIR / ".env"

log = logging.getLogger("morning_brief")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)


def _load_env():
    """Load key=value pairs from .env into os.environ if not already set."""
    if not ENV_PATH.exists():
        return
    for line in ENV_PATH.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


# ── DB snapshot ───────────────────────────────────────────────────────────────

def build_context() -> str:
    """Pull compact engine state from SQLite for Claude's context."""
    now  = datetime.now(timezone.utc)
    d1   = (now - timedelta(days=1)).isoformat()
    d7   = (now - timedelta(days=7)).isoformat()

    lines: list[str] = []

    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    try:
        # ── Signal stats ───────────────────────────────────────────────────────
        row = conn.execute(
            """
            SELECT COUNT(*) as total,
                   SUM(CASE WHEN decision LIKE '%ALERT%' AND decision NOT LIKE '%DRY%' THEN 1 ELSE 0 END) as alerts,
                   AVG(score_total) as avg_score,
                   MAX(score_total) as max_score
            FROM signals WHERE ts_utc >= ?
            """, (d1,)
        ).fetchone()
        lines.append("=== LAST 24H SIGNALS ===")
        if row and row["total"]:
            lines.append(
                f"Scans: {row['total']} | Alerts: {row['alerts']} | "
                f"Avg score: {round(row['avg_score'] or 0, 1)} | "
                f"Best score: {round(row['max_score'] or 0, 1)}"
            )
        else:
            lines.append("No signals in last 24h.")

        # Top alert symbols last 24h
        top24 = conn.execute(
            """
            SELECT symbol, COUNT(*) as n, MAX(score_total) as best
            FROM signals
            WHERE ts_utc >= ? AND decision LIKE '%ALERT%' AND decision NOT LIKE '%DRY%'
            GROUP BY symbol ORDER BY best DESC LIMIT 8
            """, (d1,)
        ).fetchall()
        if top24:
            lines.append("Top alerts (24h): " + ", ".join(
                f"{r['symbol']}(score={round(r['best'] or 0,0)})" for r in top24
            ))

        # ── Outcomes from yesterday ────────────────────────────────────────────
        yesterday_alerts = conn.execute(
            """
            SELECT ao.symbol, ao.return_1h_pct, ao.return_4h_pct, ao.return_24h_pct,
                   ao.score, ao.regime_label
            FROM alert_outcomes ao
            WHERE ao.created_ts_utc >= ?
              AND ao.return_4h_pct IS NOT NULL
            ORDER BY ao.created_ts_utc DESC
            """, (d1,)
        ).fetchall()
        lines.append("")
        lines.append("=== YESTERDAY'S ALERT OUTCOMES (4h) ===")
        if yesterday_alerts:
            wins = [r for r in yesterday_alerts if (r["return_4h_pct"] or 0) > 0]
            avg  = sum(r["return_4h_pct"] for r in yesterday_alerts) / len(yesterday_alerts)
            lines.append(
                f"Evaluated: {len(yesterday_alerts)} | "
                f"Wins: {len(wins)} | "
                f"Win rate: {round(len(wins)/len(yesterday_alerts)*100,0)}% | "
                f"Avg 4h: {round(avg,2)}%"
            )
            for r in yesterday_alerts[:6]:
                marker = "✓" if (r["return_4h_pct"] or 0) > 0 else "✗"
                lines.append(
                    f"  {marker} {r['symbol']:<10} 4h={round(r['return_4h_pct'] or 0,2)}% "
                    f"score={round(r['score'] or 0,0)} regime={r['regime_label'] or '?'}"
                )
        else:
            lines.append("No evaluated outcomes from yesterday yet.")

        # ── All-time outcome performance ───────────────────────────────────────
        all_out = conn.execute(
            """
            SELECT symbol, COUNT(*) as n,
                   AVG(return_4h_pct) as avg4,
                   SUM(CASE WHEN return_4h_pct > 0 THEN 1 ELSE 0 END) as wins
            FROM alert_outcomes
            WHERE return_4h_pct IS NOT NULL
            GROUP BY symbol HAVING COUNT(*) >= 2
            ORDER BY avg4 DESC
            """
        ).fetchall()
        lines.append("")
        lines.append("=== ALL-TIME SYMBOL EDGE (≥2 outcomes, 4h) ===")
        if all_out:
            for o in all_out:
                wr = round(float(o["wins"] or 0) / int(o["n"]) * 100, 0)
                lines.append(f"  {o['symbol']:<10} n={o['n']} | avg={round(o['avg4'] or 0,2)}% | wr={wr}%")
        else:
            lines.append("Not enough outcome data yet (need ≥2 per symbol).")

        # ── Current regime ─────────────────────────────────────────────────────
        regime = conn.execute(
            """
            SELECT regime_label, regime_score, sol_change_24h
            FROM regime_snapshots ORDER BY id DESC LIMIT 1
            """
        ).fetchone()
        lines.append("")
        lines.append("=== CURRENT REGIME ===")
        if regime:
            lines.append(
                f"Label: {regime['regime_label']} | "
                f"Score: {round(regime['regime_score'] or 0, 0)} | "
                f"SOL 24h: {round(regime['sol_change_24h'] or 0, 2)}%"
            )
        else:
            lines.append("No regime data available.")

        # ── Pending outcomes ───────────────────────────────────────────────────
        pend = conn.execute(
            "SELECT COUNT(*) as n FROM alert_outcomes WHERE status='PENDING'"
        ).fetchone()
        comp = conn.execute(
            "SELECT COUNT(*) as n FROM alert_outcomes WHERE status='COMPLETE'"
        ).fetchone()
        lines.append("")
        lines.append(f"=== OUTCOME TRACKER === Complete: {comp['n']} | Pending: {pend['n']}")

        # ── 7d signal volume trend ─────────────────────────────────────────────
        daily = conn.execute(
            """
            SELECT DATE(ts_utc) as day,
                   COUNT(*) as scans,
                   SUM(CASE WHEN decision LIKE '%ALERT%' AND decision NOT LIKE '%DRY%' THEN 1 ELSE 0 END) as alerts
            FROM signals WHERE ts_utc >= ?
            GROUP BY day ORDER BY day ASC
            """, (d7,)
        ).fetchall()
        if daily:
            lines.append("")
            lines.append("=== DAILY ALERT VOLUME (7d) ===")
            for d in daily:
                lines.append(f"  {d['day']}: {d['alerts']} alerts / {d['scans']} scans")

    finally:
        conn.close()

    return "\n".join(lines)


# ── Claude call ───────────────────────────────────────────────────────────────

_BRIEF_PROMPT = """\
You are the Abrons Engine AI. Generate a concise morning trading brief based on the data below.

Format:
1. Yesterday's performance (2-3 sentences, exact numbers)
2. Best/worst symbols if any outcomes available
3. What to watch today (based on top scoring recent alerts)
4. One honest recommendation (config, sizing, or watchlist)
5. One-line market regime summary

Rules:
- Be direct, use numbers, no fluff
- If data is sparse, say so — don't fabricate
- Max 250 words total
- No markdown headers, use plain text with line breaks
- End with: "— Abrons Engine Brief {date}"

DATA:
{context}
"""

async def generate_brief(context: str) -> str:
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return "⚠️ ANTHROPIC_API_KEY not set — skipping AI brief."

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    prompt  = _BRIEF_PROMPT.format(context=context, date=now_str)

    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key":         api_key,
                "anthropic-version": "2023-06-01",
                "content-type":      "application/json",
            },
            json={
                "model":      "claude-haiku-4-5",
                "max_tokens": 400,
                "messages":   [{"role": "user", "content": prompt}],
            },
        )
        r.raise_for_status()
        data = r.json()
        return data["content"][0]["text"].strip()


# ── Telegram send ─────────────────────────────────────────────────────────────

async def send_telegram(text: str) -> None:
    token   = os.environ.get("TELEGRAM_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")

    if not token or not chat_id:
        log.error("TELEGRAM_TOKEN or TELEGRAM_CHAT_ID not set — cannot send brief.")
        return

    # Telegram message limit is 4096 chars; truncate gracefully
    if len(text) > 4000:
        text = text[:3997] + "…"

    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                "chat_id":    chat_id,
                "text":       text,
                "parse_mode": "HTML",
            },
        )
        if r.status_code == 200:
            log.info("Morning brief sent to Telegram.")
        else:
            log.error("Telegram send failed: %s %s", r.status_code, r.text[:200])


# ── Main ──────────────────────────────────────────────────────────────────────

async def main():
    _load_env()

    log.info("Building DB context…")
    try:
        context = build_context()
    except Exception as e:
        log.error("DB context failed: %s", e)
        context = f"[DB read error: {e}]"

    log.info("Generating brief via Claude…")
    try:
        brief = await generate_brief(context)
    except Exception as e:
        log.error("Claude API failed: %s", e)
        brief = f"⚠️ Brief generation failed: {e}"

    now_str  = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    header   = f"🧠 <b>Abrons Engine — Morning Brief</b>\n{now_str}\n\n"
    full_msg = header + brief

    log.info("Brief:\n%s", brief)
    await send_telegram(full_msg)


if __name__ == "__main__":
    asyncio.run(main())
