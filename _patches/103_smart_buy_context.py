"""
Patch 103 — Smart Buy Context for AI Chat

Problems:
1. _build_db_context() is missing critical data for "what to buy" questions:
   - No SOL 24h change / hard block reason
   - No circuit breaker status
   - No per-symbol perp performance (side: LONG/SHORT)
   - No per-symbol spot performance
   - No recent watchlist momentum (2h)
   - WATCHLIST_ALERT rows with empty score_total confused the AI ("scores at 0")

2. _SYSTEM_PROMPT is too soft ("if data is sparse, say so honestly") — causes
   hedging and "I can't recommend" replies even when the engine has real data.

3. max_tokens=512 is too tight for a structured ranked-picks answer.

Files patched:
  dashboard/backend/main.py (3 changes)

Verify:
  # Ask the chat:
  # "what's the best thing to buy right now?"
  # Expected: ranked picks with market status, LONG/SHORT candidates, trigger condition
"""
import sys, os, ast

BASE = '/root/memecoin_engine'
MAIN = os.path.join(BASE, 'dashboard', 'backend', 'main.py')
txt  = open(MAIN).read()

# ─── OP-1: Add rich sections to _build_db_context() before conn.close() ──────

OLD_CONN_CLOSE = '''        lines.append("")

        conn.close()
    except Exception as exc:
        lines.append(f"[DB context error: {exc}]")'''

NEW_SECTIONS = '''        lines.append("")

        # ── Market hard-block state (from latest ENGINE signal) ────────────
        try:
            _eng = conn.execute(
                "SELECT regime_score, regime_label, notes, ts_utc FROM signals "
                "WHERE symbol='ENGINE' ORDER BY id DESC LIMIT 1"
            ).fetchone()
            lines.append("=== CURRENT MARKET / BLOCK STATUS ===")
            if _eng:
                lines.append(f"Regime: {_eng['regime_label'] or 'UNKNOWN'} (score {_eng['regime_score'] or '?'})")
                lines.append(f"Block reason: {_eng['notes'] or 'none'}")
                lines.append(f"As of: {_eng['ts_utc'][:16]} UTC")
            else:
                lines.append("No ENGINE signal yet — scanner may not have run")
            _cbf = os.path.join(os.path.dirname(__file__), '..', '..', 'data_storage', 'cb_state.txt')
            if os.path.exists(_cbf):
                lines.append(f"Circuit breaker: ACTIVE — dynamic exits paused until {open(_cbf).read().strip()[:16]} UTC")
            else:
                lines.append("Circuit breaker: inactive")
            lines.append("")
        except Exception:
            pass

        # ── Per-symbol perp performance (last 7d closed) ──────────────────
        try:
            _perp_sym = conn.execute("""
                SELECT symbol, side,
                       COUNT(*) as n,
                       ROUND(AVG(pnl_pct), 2) as avg_pnl,
                       SUM(CASE WHEN pnl_pct > 0 THEN 1 ELSE 0 END) as wins,
                       ROUND(MAX(pnl_pct), 2) as best,
                       ROUND(MIN(pnl_pct), 2) as worst
                FROM perp_positions
                WHERE status='CLOSED' AND opened_ts_utc >= ?
                GROUP BY symbol, side ORDER BY avg_pnl DESC LIMIT 12
            """, (d7,)).fetchall()
            lines.append("=== PERP PERFORMANCE BY SYMBOL + SIDE (7d closed) ===")
            if _perp_sym:
                for _p in _perp_sym:
                    _wr = round(float(_p['wins'] or 0) / int(_p['n']) * 100) if _p['n'] else 0
                    lines.append(
                        f"{_p['symbol']:<10} {_p['side']:<5} n={_p['n']} | "
                        f"avg={_p['avg_pnl']:+.2f}% | wr={_wr}% | "
                        f"best={_p['best']:+.2f}% worst={_p['worst']:+.2f}%"
                    )
            else:
                lines.append("No closed perp trades in 7d")
            lines.append("")
        except Exception:
            pass

        # ── Per-symbol spot performance (last 7d closed) ──────────────────
        try:
            _spot_sym = conn.execute("""
                SELECT symbol,
                       COUNT(*) as n,
                       ROUND(AVG(pnl_pct), 2) as avg_pnl,
                       SUM(CASE WHEN pnl_pct > 0 THEN 1 ELSE 0 END) as wins,
                       ROUND(MAX(pnl_pct), 2) as best,
                       ROUND(MIN(pnl_pct), 2) as worst
                FROM trades
                WHERE status='CLOSED' AND opened_ts_utc >= ?
                GROUP BY symbol ORDER BY avg_pnl DESC LIMIT 10
            """, (d7,)).fetchall()
            lines.append("=== SPOT PERFORMANCE BY SYMBOL (7d closed) ===")
            if _spot_sym:
                for _s in _spot_sym:
                    _wr = round(float(_s['wins'] or 0) / int(_s['n']) * 100) if _s['n'] else 0
                    lines.append(
                        f"{_s['symbol']:<10} n={_s['n']} | "
                        f"avg={_s['avg_pnl']:+.2f}% | wr={_wr}% | "
                        f"best={_s['best']:+.2f}% worst={_s['worst']:+.2f}%"
                    )
            else:
                lines.append("No closed spot trades in 7d")
            lines.append("")
        except Exception:
            pass

        # ── Recent watchlist momentum (last 2h) ───────────────────────────
        try:
            _recent = conn.execute("""
                SELECT symbol, ts_utc, setup_type, conviction
                FROM signals
                WHERE decision='WATCHLIST_ALERT'
                  AND ts_utc >= datetime('now', '-2 hours')
                ORDER BY ts_utc DESC LIMIT 15
            """).fetchall()
            lines.append("=== RECENT WATCHLIST ALERTS (last 2h — price momentum) ===")
            _conv_map = {3: "A", 2: "B", 1: "C"}
            if _recent:
                for _r in _recent:
                    _c = _conv_map.get(_r['conviction'], "?")
                    lines.append(
                        f"{_r['ts_utc'][11:16]} UTC | {_r['symbol']:<10} | "
                        f"setup={_r['setup_type'] or '?'} | conv={_c}"
                    )
            else:
                lines.append("No watchlist alerts in the last 2h")
            lines.append("")
        except Exception:
            pass

        conn.close()
    except Exception as exc:
        lines.append(f"[DB context error: {exc}]")'''

assert OLD_CONN_CLOSE in txt, 'Anchor not found: conn.close() block'
txt = txt.replace(OLD_CONN_CLOSE, NEW_SECTIONS, 1)
print('✓ OP-1: Rich market/perp/spot/watchlist sections added to _build_db_context()')

# ─── OP-2: Replace _SYSTEM_PROMPT with direct "own it" version ────────────────

OLD_PROMPT = '''_SYSTEM_PROMPT = """\
You are the Abrons Engine AI — a personal trading analyst embedded inside a \\
Solana memecoin signal dashboard. You have direct access to the engine's live \\
database snapshot provided below.

Your role:
- Answer questions about signal performance, outcome data, regime edge, and config
- Give concise, data-driven answers — use exact numbers from the context
- If the data is sparse (few outcomes), say so honestly and suggest waiting
- Never make up numbers not present in the context
- Keep answers short and direct — the user is an active trader, not a student
- You can give recommendations (e.g. "raise your threshold") when the data supports it
- Format with short paragraphs or bullet points, no markdown headers
- Today's date/time (UTC): {now}

DB SNAPSHOT:
{context}
"""'''

NEW_PROMPT = '''_SYSTEM_PROMPT = """\
You are the Abrons Engine AI — the brain of a live Solana memecoin + perps paper trading system.
You have real-time access to the engine DB: regime state, hard-block status, trade history,
open positions, and recent market alerts.

Rules:
- ALWAYS give a direct, actionable answer. Never say "I can't recommend" — this is our engine,
  our data. You ARE the brain. Own every answer.
- Use exact numbers from the DB snapshot. Never fabricate data.
- Short and punchy. Active trader context, not a lecture.
- If data is sparse, say so in one line then still give your best pick.

When asked "what to buy", "best trade", "best opportunity", or similar:
1. MARKET STATUS — one line: CLEAR / RISK_OFF / HARD_BLOCK + reason (e.g. "SOL -10.76% 24h")
2. CIRCUIT BREAKER — mention if active (dynamic exits paused until X)
3. TOP 3 LONG candidates — perp LONG or spot, ranked by avg PnL then win rate then recent momentum
4. TOP SHORT candidate — perp SHORT only, best negative-correlation symbol if sample exists
5. TRIGGER — one concrete condition to enter: e.g. "Enter when SOL 24h recovers above -4%"
   If market is CLEAR, say "Enter now — conditions are live"
6. Even during HARD_BLOCK: still give the ranked list with "wait for [X] before entering"

Format: bullet points. Include the numbers. No markdown headers.
Today: {now}

DB SNAPSHOT:
{context}
"""'''

assert OLD_PROMPT in txt, 'Anchor not found: _SYSTEM_PROMPT'
txt = txt.replace(OLD_PROMPT, NEW_PROMPT, 1)
print('✓ OP-2: _SYSTEM_PROMPT replaced — direct "own it" directive')

# ─── OP-3: Bump max_tokens 512 → 700 ─────────────────────────────────────────

OLD_TOKENS = '"max_tokens": 512,'
NEW_TOKENS = '"max_tokens": 700,'

assert OLD_TOKENS in txt, 'Anchor not found: max_tokens 512'
txt = txt.replace(OLD_TOKENS, NEW_TOKENS, 1)
print('✓ OP-3: max_tokens bumped 512 → 700')

# Write
open(MAIN, 'w').write(txt)
print(f'\n✓ main.py written ({len(txt):,} bytes)')

# Syntax check
try:
    ast.parse(open(MAIN).read())
    print('✓ Syntax check: main.py OK')
except SyntaxError as e:
    print(f'✗ SYNTAX ERROR: {e}')
    sys.exit(1)

print('\nAll done. Run: systemctl restart memecoin-dashboard')
print('\nVerify:')
print('  Ask the chat: "what\'s the best thing to buy right now?"')
print('  Expected: market status + ranked LONG/SHORT picks + trigger condition')
