"""
Patch 178 — Smart wallet operator UX: backend changes.

1. get_wallet_list() in smart_wallet_tracker.py:
   - Add complete_buys (COUNT of COMPLETE rows per wallet)
   - Add avg_24h (AVG return_24h_pct across COMPLETE rows)
   - Sort by wr_24h DESC NULLS LAST (best wallets first, cull candidates at bottom)

2. Add GET /api/wallets/triples endpoint to wallets.py:
   - Queries confluence_events WHERE confluence_type='TRIPLE'
   - Returns: id, ts_utc, token_mint, token_symbol, source_details,
             market_cap_usd, return_1h_pct, return_4h_pct, return_24h_pct, outcome_status
"""
import pathlib, py_compile, sys

# ── 1. smart_wallet_tracker.py: get_wallet_list() SQL upgrade ─────────────────

TRACKER = pathlib.Path("/root/memecoin_engine/utils/smart_wallet_tracker.py")
src = TRACKER.read_text()

# Anchor: the SELECT inside get_wallet_list — first line is unique enough
OLD_ANCHOR = '                SELECT w.id, w.address, w.label, w.active, w.total_buys,\n                       w.last_checked_ts, w.added_ts,\n                       ROUND(AVG(CASE WHEN b.return_24h_pct > 0 THEN 1.0 ELSE 0.0 END)*100,1) as wr_24h\n                FROM smart_wallets w\n                LEFT JOIN smart_wallet_buys b ON b.wallet_address=w.address AND b.outcome_status=\'COMPLETE\'\n                GROUP BY w.id\n                ORDER BY w.active DESC, w.total_buys DESC'

NEW_SQL = '                SELECT w.id, w.address, w.label, w.active, w.total_buys,\n                       w.last_checked_ts, w.added_ts,\n                       COUNT(b.id)                                                              AS complete_buys,\n                       ROUND(AVG(CASE WHEN b.return_24h_pct > 0 THEN 1.0 ELSE 0.0 END)*100, 1) AS wr_24h,\n                       ROUND(AVG(b.return_24h_pct), 1)                                         AS avg_24h\n                FROM smart_wallets w\n                LEFT JOIN smart_wallet_buys b ON b.wallet_address=w.address AND b.outcome_status=\'COMPLETE\'\n                GROUP BY w.id\n                ORDER BY w.active DESC, wr_24h DESC NULLS LAST, w.total_buys DESC'

if OLD_ANCHOR not in src:
    print(f"ERROR: get_wallet_list SQL anchor not found in smart_wallet_tracker.py")
    print("First 200 chars of file:")
    print(repr(src[:200]))
    sys.exit(1)

new_src = src.replace(OLD_ANCHOR, NEW_SQL, 1)
TRACKER.write_text(new_src)
print("Wrote smart_wallet_tracker.py")

try:
    py_compile.compile(str(TRACKER), doraise=True)
    print("py_compile smart_wallet_tracker: OK")
except py_compile.PyCompileError as e:
    print(f"SYNTAX ERROR in smart_wallet_tracker: {e}")
    TRACKER.write_text(src)
    print("Restored original — change NOT applied")
    sys.exit(1)

# ── 2. wallets.py: add /api/wallets/triples endpoint ─────────────────────────

WALLETS = pathlib.Path("/root/memecoin_engine/dashboard/backend/routers/wallets.py")
wsrc = WALLETS.read_text()

if '"/triples"' in wsrc or "'/triples'" in wsrc:
    print("SKIP: /triples endpoint already exists in wallets.py")
else:
    TRIPLES_ENDPOINT = '''

@router.get("/triples")
def get_triples(limit: int = 30, _user=Depends(get_current_user)):
    """Return TRIPLE confluence events (whale + scanner + smart_wallet all agree). Patch 178."""
    try:
        with _get_db() as conn:
            rows = conn.execute("""
                SELECT id, ts_utc, token_mint, token_symbol,
                       source_details, market_cap_usd,
                       return_1h_pct, return_4h_pct, return_24h_pct,
                       outcome_status
                FROM confluence_events
                WHERE confluence_type='TRIPLE'
                ORDER BY ts_utc DESC LIMIT ?
            """, (min(limit, 200),)).fetchall()
            return {"triples": [dict(r) for r in rows]}
    except Exception as e:
        log.warning("[WALLETS] /triples error: %s", e)
        return {"triples": [], "error": str(e)}
'''
    WALLETS.write_text(wsrc + TRIPLES_ENDPOINT)
    print("Wrote wallets.py (added /triples endpoint)")

    try:
        py_compile.compile(str(WALLETS), doraise=True)
        print("py_compile wallets: OK")
    except py_compile.PyCompileError as e:
        print(f"SYNTAX ERROR in wallets: {e}")
        WALLETS.write_text(wsrc)
        print("Restored original wallets.py")
        sys.exit(1)

print("Patch 178 backend applied successfully.")
