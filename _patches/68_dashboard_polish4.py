"""
Patch 68 — Dashboard Polish 4
==============================
Backend changes only (frontend was built + rsynced separately):

1. news_feed.py — add `coins` parameter to fetch_news() for keyword filtering:
   - 'memecoins': filters to SOL ecosystem + meme tokens
   - 'majors' / 'btc-eth': filters to Bitcoin / Ethereum news

2. main.py /api/news — add `coins: str | None = None` query param,
   pass it through to fetch_news().
"""

from pathlib import Path
import py_compile, tempfile, shutil

ENGINE = Path(__file__).resolve().parent.parent
NEWS   = ENGINE / "dashboard" / "backend" / "news_feed.py"
MAIN   = ENGINE / "dashboard" / "backend" / "main.py"

news = NEWS.read_text()
main = MAIN.read_text()

# ── 1. news_feed.py ─────────────────────────────────────────────────────────

OLD_SIG = (
    'def fetch_news(limit: int = 40, tag: Optional[str] = None) -> list[dict]:\n'
    '    """\n'
    '    Return up to `limit` news items, newest first.\n'
    '    Optionally filter by tag: \'SOL\', \'MARKET\', or \'CRYPTO\'.\n'
    '    Uses a 5-minute in-process cache.\n'
    '    """\n'
)
NEW_SIG = (
    'def fetch_news(limit: int = 40, tag: Optional[str] = None, coins: Optional[str] = None) -> list[dict]:\n'
    '    """\n'
    '    Return up to `limit` news items, newest first.\n'
    '    Optionally filter by tag: \'SOL\', \'MARKET\', or \'CRYPTO\'.\n'
    '    Optionally filter by coins: \'memecoins\' or \'majors\'.\n'
    '    Uses a 5-minute in-process cache.\n'
    '    """\n'
)
assert OLD_SIG in news, "fetch_news signature anchor not found"
news = news.replace(OLD_SIG, NEW_SIG)
print("✅ fetch_news signature updated with coins param")

OLD_TAIL = (
    '    if tag:\n'
    '        items = [i for i in items if i["tag"] == tag.upper()]\n'
    '\n'
    '    return items[:limit]\n'
)
NEW_TAIL = (
    '    if tag:\n'
    '        items = [i for i in items if i["tag"] == tag.upper()]\n'
    '\n'
    '    if coins:\n'
    '        _ck = coins.lower()\n'
    '        if _ck == "memecoins":\n'
    '            _kw = re.compile(\n'
    '                r"\\b(bonk|wif|fartcoin|pepe|doge|shib|floki|brett|mog|ponke|popcat|neiro|goat|"\n'
    '                r"meme\\b|memecoin|pump\\.fun|raydi|raydium|orca|jup\\b|jupiter|solana|sol\\b)\\b",\n'
    '                re.I,\n'
    '            )\n'
    '            items = [i for i in items if _kw.search(i["title"] + " " + (i.get("summary") or ""))]\n'
    '        elif _ck in ("majors", "btc-eth"):\n'
    '            _kw = re.compile(\n'
    '                r"\\b(bitcoin|btc|ethereum|eth|base\\b|layer.?2|l2\\b)\\b",\n'
    '                re.I,\n'
    '            )\n'
    '            items = [i for i in items if _kw.search(i["title"] + " " + (i.get("summary") or ""))]\n'
    '\n'
    '    return items[:limit]\n'
)
assert OLD_TAIL in news, "fetch_news tail anchor not found"
news = news.replace(OLD_TAIL, NEW_TAIL)
print("✅ fetch_news coins filter logic added")

NEWS.write_text(news)

tmp = Path(tempfile.mktemp(suffix=".py"))
shutil.copy(NEWS, tmp)
py_compile.compile(str(tmp), doraise=True)
tmp.unlink()
print("✅ news_feed.py compiles OK")

# ── 2. main.py /api/news endpoint ───────────────────────────────────────────

OLD_NEWS_EP = (
    '@app.get("/api/news")\n'
    'async def news(\n'
    '    limit: int = 40,\n'
    '    tag: str | None = None,\n'
    '    _: str = Depends(get_current_user),\n'
    '):\n'
    '    items = await asyncio.to_thread(fetch_news, min(80, limit), tag)\n'
    '    return items\n'
)
NEW_NEWS_EP = (
    '@app.get("/api/news")\n'
    'async def news(\n'
    '    limit: int = 40,\n'
    '    tag: str | None = None,\n'
    '    coins: str | None = None,\n'
    '    _: str = Depends(get_current_user),\n'
    '):\n'
    '    items = await asyncio.to_thread(fetch_news, min(80, limit), tag, coins)\n'
    '    return items\n'
)
assert OLD_NEWS_EP in main, "/api/news endpoint anchor not found"
main = main.replace(OLD_NEWS_EP, NEW_NEWS_EP)
print("✅ /api/news endpoint updated with coins param")

MAIN.write_text(main)

tmp = Path(tempfile.mktemp(suffix=".py"))
shutil.copy(MAIN, tmp)
py_compile.compile(str(tmp), doraise=True)
tmp.unlink()
print("✅ main.py compiles OK")
print("\nPatch 68 complete")
