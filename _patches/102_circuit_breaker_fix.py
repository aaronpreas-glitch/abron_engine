"""
Patch 102 — Circuit Breaker Fix

Two problems:

1. Re-fires on every restart (stale entries)
   The CB queries the last 4 EARLY_EXIT/ML_PROB_DROP rows with no time filter.
   4 old Feb 26 losses sit in the DB permanently, so CB fires on every boot.
   Fix: add AND del.ts_utc >= datetime('now', '-48 hours') — only recent losses count.

2. Doesn't survive restarts when legitimately fired
   _dynamic_exit_disabled_until is in-memory only. If CB fires at 10pm and the
   service restarts at 11pm, the cooldown is forgotten and fresh losses could
   re-enable exits too early.
   Fix: persist disabled_until to data_storage/cb_state.txt on fire, restore on boot.

Files patched:
  utils/perp_executor.py (2 changes)

Verify:
  journalctl -u memecoin-dashboard -n 10 --no-pager | grep 'CIRCUIT BREAKER'
  # Expected: NO "disabling dynamic exits" line after restart
  # (old Feb 26 entries are now outside the 48h window)
"""
import sys, os, ast

BASE = '/root/memecoin_engine'
ORCH = os.path.join(BASE, 'utils', 'perp_executor.py')
txt  = open(ORCH).read()

# ─── OP-1: Add _cb_persist helper before _check_dynamic_exit_circuit_breaker ──

OLD_CB_DEF = 'def _check_dynamic_exit_circuit_breaker():'

NEW_CB_HELPER = '''def _cb_persist(until):
    """Persist CB disabled_until to disk so it survives restarts. (Patch 102)"""
    try:
        _cbf = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            '..', 'data_storage', 'cb_state.txt')
        with open(_cbf, 'w') as _f:
            _f.write(until.isoformat())
    except Exception:
        pass


def _cb_clear():
    """Remove persisted CB state file."""
    try:
        _cbf = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            '..', 'data_storage', 'cb_state.txt')
        if os.path.exists(_cbf):
            os.remove(_cbf)
    except Exception:
        pass


def _check_dynamic_exit_circuit_breaker():'''

assert OLD_CB_DEF in txt, 'Anchor not found: _check_dynamic_exit_circuit_breaker def'
txt = txt.replace(OLD_CB_DEF, NEW_CB_HELPER, 1)
print('✓ OP-1: _cb_persist + _cb_clear helpers added')

# ─── OP-2: Replace CB check body with staleness filter + persistence ──────────

OLD_CB_BODY = '''def _check_dynamic_exit_circuit_breaker():
    """Disable dynamic exits for 24h if 4 consecutive EARLY_EXIT losses OR 3 consecutive bad_call TRAIL exits."""
    global _dynamic_exit_disabled_until
    if _dynamic_exit_disabled_until and datetime.now(timezone.utc) < _dynamic_exit_disabled_until:
        return True  # still disabled
    if _dynamic_exit_disabled_until and datetime.now(timezone.utc) >= _dynamic_exit_disabled_until:
        _dynamic_exit_disabled_until = None
        logger.info("[CIRCUIT BREAKER] Dynamic exit re-enabled after 24h cooldown")
        return False
    try:
        with _conn() as c:
            cur = c.cursor()
            # Check 1: 4 consecutive early-exit losses
            cur.execute("""
                SELECT del.action, pp.pnl_pct
                FROM dynamic_exit_log del
                JOIN perp_positions pp ON del.position_id = pp.id
                WHERE del.action IN ('EARLY_EXIT', 'ML_PROB_DROP') AND pp.status = 'CLOSED'
                ORDER BY del.ts_utc DESC LIMIT 4
            """)
            rows = cur.fetchall()
            if len(rows) >= 4 and all(r[1] is not None and float(r[1]) < 0 for r in rows):
                _dynamic_exit_disabled_until = datetime.now(timezone.utc) + timedelta(hours=24)
                logger.warning("[CIRCUIT BREAKER] 4 consecutive early-exit losses — disabling dynamic exits for 24h")
                return True
            # Check 2: 3 consecutive bad_call TRAIL/PROFIT_LOCK/SENTIMENT_TRAIL exits
            cur.execute("""
                SELECT outcome
                FROM dynamic_exit_log
                WHERE action IN ('DYNAMIC_TRAIL', 'TRAILING_ATR_EXTEND', 'TRAILING_ATR_WINNER',
                                 'PROFIT_LOCK', 'SENTIMENT_TRAIL')
                  AND outcome IS NOT NULL
                ORDER BY ts_utc DESC LIMIT 3
            """)
            trail_rows = cur.fetchall()
            if len(trail_rows) >= 3 and all(r[0] == 'bad_call' for r in trail_rows):
                _dynamic_exit_disabled_until = datetime.now(timezone.utc) + timedelta(hours=24)
                logger.warning("[CIRCUIT BREAKER] 3 consecutive bad_call TRAIL exits — disabling dynamic exits for 24h")
                return True
    except Exception:
        pass
    return False'''

NEW_CB_BODY = '''def _check_dynamic_exit_circuit_breaker():
    """Disable dynamic exits for 24h if 4 consecutive EARLY_EXIT losses (last 48h only)
    OR 3 consecutive bad_call TRAIL exits (last 48h only). Persists across restarts.
    Patch 102: added 48h staleness guard + disk persistence."""
    global _dynamic_exit_disabled_until

    # Restore persisted state on first call after restart
    if _dynamic_exit_disabled_until is None:
        try:
            _cbf = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                '..', 'data_storage', 'cb_state.txt')
            if os.path.exists(_cbf):
                _cb_ts = datetime.fromisoformat(open(_cbf).read().strip())
                if _cb_ts.tzinfo is None:
                    _cb_ts = _cb_ts.replace(tzinfo=timezone.utc)
                if datetime.now(timezone.utc) < _cb_ts:
                    _dynamic_exit_disabled_until = _cb_ts
                    logger.info("[CIRCUIT BREAKER] Restored from disk — disabled until %s",
                                _cb_ts.strftime('%Y-%m-%d %H:%M UTC'))
                else:
                    _cb_clear()   # expired, remove stale file
        except Exception:
            pass

    if _dynamic_exit_disabled_until and datetime.now(timezone.utc) < _dynamic_exit_disabled_until:
        return True  # still disabled
    if _dynamic_exit_disabled_until and datetime.now(timezone.utc) >= _dynamic_exit_disabled_until:
        _dynamic_exit_disabled_until = None
        _cb_clear()
        logger.info("[CIRCUIT BREAKER] Dynamic exit re-enabled after 24h cooldown")
        return False
    try:
        with _conn() as c:
            cur = c.cursor()
            # Check 1: 4 consecutive early-exit losses — last 48h only (staleness guard)
            cur.execute("""
                SELECT del.action, pp.pnl_pct
                FROM dynamic_exit_log del
                JOIN perp_positions pp ON del.position_id = pp.id
                WHERE del.action IN ('EARLY_EXIT', 'ML_PROB_DROP') AND pp.status = 'CLOSED'
                  AND del.ts_utc >= datetime('now', '-48 hours')
                ORDER BY del.ts_utc DESC LIMIT 4
            """)
            rows = cur.fetchall()
            if len(rows) >= 4 and all(r[1] is not None and float(r[1]) < 0 for r in rows):
                _dynamic_exit_disabled_until = datetime.now(timezone.utc) + timedelta(hours=24)
                _cb_persist(_dynamic_exit_disabled_until)
                logger.warning("[CIRCUIT BREAKER] 4 consecutive early-exit losses in 48h — disabling dynamic exits for 24h")
                return True
            # Check 2: 3 consecutive bad_call TRAIL exits — last 48h only
            cur.execute("""
                SELECT outcome
                FROM dynamic_exit_log
                WHERE action IN ('DYNAMIC_TRAIL', 'TRAILING_ATR_EXTEND', 'TRAILING_ATR_WINNER',
                                 'PROFIT_LOCK', 'SENTIMENT_TRAIL')
                  AND outcome IS NOT NULL
                  AND ts_utc >= datetime('now', '-48 hours')
                ORDER BY ts_utc DESC LIMIT 3
            """)
            trail_rows = cur.fetchall()
            if len(trail_rows) >= 3 and all(r[0] == 'bad_call' for r in trail_rows):
                _dynamic_exit_disabled_until = datetime.now(timezone.utc) + timedelta(hours=24)
                _cb_persist(_dynamic_exit_disabled_until)
                logger.warning("[CIRCUIT BREAKER] 3 consecutive bad_call TRAIL exits in 48h — disabling dynamic exits for 24h")
                return True
    except Exception:
        pass
    return False'''

assert OLD_CB_BODY in txt, 'Anchor not found: full CB body'
txt = txt.replace(OLD_CB_BODY, NEW_CB_BODY)
print('✓ OP-2: CB function replaced — 48h staleness filter + disk persistence')

# Write
open(ORCH, 'w').write(txt)
print(f'\n✓ perp_executor.py written ({len(txt):,} bytes)')

# Syntax check
try:
    ast.parse(open(ORCH).read())
    print('✓ Syntax check: perp_executor.py OK')
except SyntaxError as e:
    print(f'✗ SYNTAX ERROR: {e}')
    sys.exit(1)

print('\nAll done. Run: systemctl restart memecoin-dashboard')
print('\nVerify (after restart, should NOT see "disabling dynamic exits"):')
print('  journalctl -u memecoin-dashboard -n 15 --no-pager | grep "CIRCUIT BREAKER"')
