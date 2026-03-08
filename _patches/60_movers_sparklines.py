"""
Patch 60 — Movers sparklines
==============================
Adds 24h sparkline field to each /api/market/movers row.
  • Fetches OKX /market/candles (4H, 6 bars = 24h) in parallel for the
    top 20 movers by |change_24h| whenever the parsed cache is refreshed.
  • Sparkline is a list of 6 close-price floats (oldest→newest).
  • Adds no extra endpoints — pure enhancement to existing movers cache logic.
"""

from pathlib import Path
import py_compile, tempfile, shutil

MAIN = Path(__file__).resolve().parent.parent / "dashboard" / "backend" / "main.py"
main = MAIN.read_text()

# ── Insert sparkline fetch into the movers cache-refresh block ───────────────
# Anchor: the two lines that write to the cache after building parsed
OLD = (
    '            _market_movers_cache["parsed"] = parsed\n'
    '            _market_movers_cache["ts"]     = now\n'
)
assert OLD in main, "movers cache-write anchor not found"

SPARKLINE_BLOCK = (
    '            # Batch-fetch 24h sparklines (OKX 4H candles, 6 bars) for top 20 movers\n'
    '            try:\n'
    '                _sp_syms = [_r["symbol"] for _r in sorted(\n'
    '                    parsed, key=lambda x: abs(x["change_24h"]), reverse=True\n'
    ')[:20]]\n'
    '                async def _get_sp(_sym, _r2=_req):\n'
    '                    try:\n'
    '                        _url = (f"https://www.okx.com/api/v5/market/candles"\n'
    '                                f"?instId={_sym}-USDT&bar=4H&limit=6")\n'
    '                        _res = await asyncio.to_thread(lambda u=_url, r=_r2: r.get(u, timeout=5).json())\n'
    '                        _pts = _res.get("data", [])\n'
    '                        return _sym, [float(p[4]) for p in reversed(_pts) if len(p) > 4]\n'
    '                    except Exception:\n'
    '                        return _sym, []\n'
    '                _sp_results = await asyncio.gather(*(_get_sp(s) for s in _sp_syms), return_exceptions=True)\n'
    '                _spark_map = {}\n'
    '                for _sr in _sp_results:\n'
    '                    if not isinstance(_sr, Exception):\n'
    '                        _spark_map[_sr[0]] = _sr[1]\n'
    '                for _row in parsed:\n'
    '                    _row["sparkline"] = _spark_map.get(_row["symbol"], [])\n'
    '            except Exception as _spe:\n'
    '                log.debug("movers sparkline fetch error: %s", _spe)\n'
    '\n'
)

main = main.replace(OLD, SPARKLINE_BLOCK + OLD)
print("✅ sparkline fetch block inserted")

MAIN.write_text(main)

tmp = Path(tempfile.mktemp(suffix=".py"))
shutil.copy(MAIN, tmp)
py_compile.compile(str(tmp), doraise=True)
tmp.unlink()
print("✅ main.py compiles OK")
print("\nPatch 60 complete")
