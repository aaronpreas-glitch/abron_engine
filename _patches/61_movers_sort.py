"""
Patch 61 — Movers Sorting + Favorites Sync
============================================
1. /api/market/movers: add ?sort=change|volume param
   - sort=change (default): gainers by +change_24h, losers by -change_24h
   - sort=volume: both tabs sorted by volume_usd desc (top by liquidity)
   - sort param is orthogonal to filter param — they compose freely
2. /api/user/favorites (GET + POST):
   - SQLite user_favorites table (user_id TEXT, symbol TEXT, added_at TEXT)
   - USER_ID from .env (default "default")
   - GET  /api/user/favorites              → {symbols: [...], count: N}
   - POST /api/user/favorites {symbol, action: "add"|"remove"}
                                           → {ok: true, symbols: [...]}
   - Table auto-created on first request; zero migration required
"""

from pathlib import Path
import py_compile, tempfile, shutil

MAIN = Path(__file__).resolve().parent.parent / "dashboard" / "backend" / "main.py"
main = MAIN.read_text()

# ── 1. Add `sort` param to market_movers signature ───────────────────────────

OLD_SIG = (
    'async def market_movers(request: Request, filter: str = "all", '
    '_: str = Depends(get_current_user)):'
)
NEW_SIG = (
    'async def market_movers(request: Request, filter: str = "all", '
    'sort: str = "change", _: str = Depends(get_current_user)):'
)
assert OLD_SIG in main, f"movers sig anchor not found"
main = main.replace(OLD_SIG, NEW_SIG)
print("✅ sort param added to movers signature")

# ── 2. Replace sort + return block ───────────────────────────────────────────
# The existing block has a stray line-break inside the sorted() call for gainers
# (artifact from an earlier patch). Match it exactly.

OLD_SORT = (
    '    if filter == "memecoins":\n'
    '        pool    = [r for r in parsed if _is_memecoin(r)]\n'
    '        gainers = sorted(pool, key=lambda x: x["change_24h"], reverse=True)[:15]\n'
    '        losers  = sorted(pool, key=lambda x: x["change_24h"])[:15]\n'
    '    else:\n'
    '        gainers = sorted(parsed, key=lambda x: x["change_24h"], reverse=True\n'
    ')[:40]\n'
    '        losers  = sorted(parsed, key=lambda x: x["change_24h"])[:20]\n'
    '\n'
    '    return JSONResponse({\n'
    '        "gainers": gainers,\n'
    '        "losers":  losers,\n'
    '        "source":  "OKX",\n'
    '        "filter":  filter,\n'
    '        "ts":      datetime.utcnow().isoformat() + "Z",\n'
    '    })\n'
)
assert OLD_SORT in main, "movers sort+return anchor not found"

NEW_SORT = (
    '    pool  = [r for r in parsed if _is_memecoin(r)] if filter == "memecoins" else parsed\n'
    '    limit = 15 if filter == "memecoins" else 20\n'
    '    if sort == "volume":\n'
    '        by_vol  = sorted(pool, key=lambda x: x["volume_usd"], reverse=True)[:limit * 2]\n'
    '        gainers = by_vol\n'
    '        losers  = by_vol\n'
    '    else:\n'
    '        gainers = sorted(pool, key=lambda x: x["change_24h"], reverse=True)[:limit * 2]\n'
    '        losers  = sorted(pool, key=lambda x: x["change_24h"])[:limit]\n'
    '\n'
    '    return JSONResponse({\n'
    '        "gainers": gainers,\n'
    '        "losers":  losers,\n'
    '        "source":  "OKX",\n'
    '        "filter":  filter,\n'
    '        "sort":    sort,\n'
    '        "ts":      datetime.utcnow().isoformat() + "Z",\n'
    '    })\n'
)
main = main.replace(OLD_SORT, NEW_SORT)
print("✅ sort logic replaced in movers endpoint")

# ── 3. User favorites endpoints ──────────────────────────────────────────────

INSERT_ANCHOR = '@app.get("/api/journal/learnings")'
assert INSERT_ANCHOR in main, "learnings anchor not found"

FAVORITES_EP = r'''@app.get("/api/user/favorites")
async def user_favorites_get(_: str = Depends(get_current_user)):
    """Return the current user's pinned favorite symbols."""
    import sqlite3 as _sq, os as _os
    user_id = _os.environ.get("USER_ID", "default")
    db_path = os.path.join(_engine_root(), "data_storage", "engine.db")
    try:
        with _sq.connect(db_path) as c:
            c.execute(
                "CREATE TABLE IF NOT EXISTS user_favorites "
                "(user_id TEXT NOT NULL, symbol TEXT NOT NULL, added_at TEXT NOT NULL, "
                "PRIMARY KEY (user_id, symbol))"
            )
            rows = c.execute(
                "SELECT symbol FROM user_favorites WHERE user_id = ? ORDER BY added_at",
                (user_id,)
            ).fetchall()
            symbols = [r[0] for r in rows]
        return JSONResponse({"symbols": symbols, "count": len(symbols)})
    except Exception as exc:
        log.warning("user_favorites_get error: %s", exc)
        return JSONResponse({"symbols": [], "count": 0, "error": str(exc)})


@app.post("/api/user/favorites")
async def user_favorites_post(request: Request, _: str = Depends(get_current_user)):
    """Add or remove a symbol from the user's favorites."""
    import sqlite3 as _sq, os as _os
    body = await request.json()
    symbol = str(body.get("symbol", "")).upper().strip()
    action = str(body.get("action", "add")).lower()
    if not symbol:
        return JSONResponse({"error": "symbol required"}, status_code=400)
    user_id = _os.environ.get("USER_ID", "default")
    db_path = os.path.join(_engine_root(), "data_storage", "engine.db")
    try:
        with _sq.connect(db_path) as c:
            c.execute(
                "CREATE TABLE IF NOT EXISTS user_favorites "
                "(user_id TEXT NOT NULL, symbol TEXT NOT NULL, added_at TEXT NOT NULL, "
                "PRIMARY KEY (user_id, symbol))"
            )
            if action == "remove":
                c.execute(
                    "DELETE FROM user_favorites WHERE user_id = ? AND symbol = ?",
                    (user_id, symbol)
                )
            else:
                c.execute(
                    "INSERT OR IGNORE INTO user_favorites (user_id, symbol, added_at) VALUES (?, ?, ?)",
                    (user_id, symbol, datetime.utcnow().isoformat() + "Z")
                )
            rows = c.execute(
                "SELECT symbol FROM user_favorites WHERE user_id = ? ORDER BY added_at",
                (user_id,)
            ).fetchall()
            symbols = [r[0] for r in rows]
        return JSONResponse({"ok": True, "symbols": symbols, "count": len(symbols)})
    except Exception as exc:
        log.warning("user_favorites_post error: %s", exc)
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


'''

main = main.replace(INSERT_ANCHOR, FAVORITES_EP + INSERT_ANCHOR)
print("✅ /api/user/favorites (GET + POST) inserted")

MAIN.write_text(main)

tmp = Path(tempfile.mktemp(suffix=".py"))
shutil.copy(MAIN, tmp)
py_compile.compile(str(tmp), doraise=True)
tmp.unlink()
print("✅ main.py compiles OK")
print("\nPatch 61 complete")
