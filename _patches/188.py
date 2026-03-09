"""
Patch 188 — Align recent-performance gate in _auto_buy_step() to 24h horizon

Why:
  The learning-state review (P187 session) found that the gate at lines 573-574
  of _auto_buy_step() still uses return_4h_pct while:
    - the tuner now optimizes on 24h (P185)
    - readiness/pilot logic is 24h-aware
    - score 20-25 band is only visible at 24h (precisely the band previously
      "invisible" to the 4h tuner)

  4h WR structurally inflates due to early momentum (last-30 GOOD at 4h: 63.3%)
  vs what the signal is actually worth at 24h (last-30 GOOD at 24h: 40.0%).
  Using 4h for the gate means the gate could allow buys in a 24h-bear regime or
  block them in a 24h-bull regime depending on early-momentum noise.

Change:
  return_4h_pct → return_24h_pct (query WHERE clause and SELECT column)
  Threshold kept at 40% — same semantic: "pause buying if recent 24h WR < 40%"
  Minimum sample check (len >= 30) kept — if fewer than 30 GOOD tokens have 24h
  outcomes, gate is bypassed (allows buys); conservative for sparse environments
  LIMIT 30 kept — equivalent lookback window

No frontend changes: gate is live-mode only (if not dry_run) with no operator-
facing API surface. System is currently in PAPER mode (MEMECOIN_DRY_RUN=true)
so this has zero current operational impact.

Files changed:
  /root/memecoin_engine/utils/memecoin_manager.py
"""
import py_compile

MM_PATH = "/root/memecoin_engine/utils/memecoin_manager.py"

mm = open(MM_PATH).read()


# ── A: Update gate query + comment + log message + docstring ─────────────────
# Anchor: the full recent-performance gate block including comment, query, and
# log line. Unique in the file (only one "recent WR" gate exists).

OLD_A = (
    "    # Recent performance gate — live mode only. If last 30 GOOD outcomes < 40% WR,\n"
    "    # market conditions are poor → pause buying to protect capital (Patch 135)\n"
    "    if not dry_run:\n"
    "        try:\n"
    "            with get_conn() as conn:\n"
    "                recent = conn.execute(\"\"\"\n"
    "                    SELECT return_4h_pct FROM memecoin_signal_outcomes\n"
    "                    WHERE rug_label='GOOD' AND return_4h_pct IS NOT NULL\n"
    "                    ORDER BY scanned_at DESC LIMIT 30\n"
    "                \"\"\").fetchall()\n"
    "            if len(recent) >= 30:\n"
    "                recent_wr = sum(1 for r in recent if r[0] > 0) / len(recent)\n"
    "                if recent_wr < 0.40:\n"
    "                    log.info(\"[MEME] Recent WR %.1f%% < 40%% — pausing live buys\", recent_wr * 100)\n"
    "                    return\n"
    "        except Exception:\n"
    "            pass\n"
)

NEW_A = (
    "    # Recent performance gate — live mode only. If last 30 GOOD 24h outcomes < 40% WR,\n"
    "    # market conditions are poor → pause buying to protect capital (Patch 135 / P188: 24h)\n"
    "    # Sparse-data guard: gate is bypassed if fewer than 30 GOOD tokens have 24h outcomes.\n"
    "    if not dry_run:\n"
    "        try:\n"
    "            with get_conn() as conn:\n"
    "                recent = conn.execute(\"\"\"\n"
    "                    SELECT return_24h_pct FROM memecoin_signal_outcomes\n"
    "                    WHERE rug_label='GOOD' AND return_24h_pct IS NOT NULL\n"
    "                    ORDER BY scanned_at DESC LIMIT 30\n"
    "                \"\"\").fetchall()\n"
    "            if len(recent) >= 30:\n"
    "                recent_wr = sum(1 for r in recent if r[0] > 0) / len(recent)\n"
    "                if recent_wr < 0.40:\n"
    "                    log.info(\"[MEME] Recent 24h WR %.1f%% < 40%% — pausing live buys (P188)\", recent_wr * 100)\n"
    "                    return\n"
    "        except Exception:\n"
    "            pass\n"
)

assert OLD_A in mm, "Anchor A not found — check recent-performance gate in _auto_buy_step"
mm = mm.replace(OLD_A, NEW_A, 1)
print("Step A: recent-performance gate updated: return_4h_pct → return_24h_pct")


# ── B: Update docstring line that describes the gate ──────────────────────────
# Anchor: the gate description in _auto_buy_step's docstring.

OLD_B = (
    "      recent WR >= 40% (last 30 GOOD outcomes) — live mode only; pauses buys in bad market regimes\n"
)

NEW_B = (
    "      recent 24h WR >= 40% (last 30 GOOD 24h outcomes) — live mode only; pauses buys in bad market regimes (P188)\n"
)

assert OLD_B in mm, "Anchor B not found — check _auto_buy_step docstring gate line"
mm = mm.replace(OLD_B, NEW_B, 1)
print("Step B: _auto_buy_step docstring updated to mention 24h gate")


with open(MM_PATH, "w") as f:
    f.write(mm)

py_compile.compile(MM_PATH, doraise=True)
print(f"memecoin_manager.py — py_compile: OK")

print("\nPatch 188 applied successfully.")
print("  Gate column: return_4h_pct → return_24h_pct")
print("  Threshold: 40% (unchanged)")
print("  Minimum sample: 30 (unchanged — bypasses gate if <30 GOOD 24h outcomes)")
print("  LIMIT: 30 (unchanged)")
print("  Log message: updated to say '24h WR'")
print("  No frontend changes (gate is live-mode only, no operator-facing surface)")
