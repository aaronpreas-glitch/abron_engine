"""
Patch 79b — fix _gt_get_pool: add inline `import requests as _req`
The backend uses inline imports (not top-level), so replace the bare
`requests.get(...)` calls with `_req.get(...)` after importing.
"""
import subprocess, sys
from pathlib import Path

ROOT = Path("/root/memecoin_engine")
MAIN = ROOT / "dashboard/backend/main.py"

main_text = MAIN.read_text()

# ── Fix _gt_get_pool ──────────────────────────────────────────────────────────
OLD_POOL = '''def _gt_get_pool(token_address: str):
    """Return highest-liquidity Solana pool address for a token (GeckoTerminal)."""
    cached = _token_pool_cache.get(token_address)
    if cached and cached["exp"] > time.time():
        return cached["pool"]
    try:
        r = requests.get(
            f"https://api.geckoterminal.com/api/v2/networks/solana/tokens/{token_address}/pools",
            params={"page": 1},
            headers={"Accept": "application/json"},
            timeout=10,
        )
        pools = r.json().get("data", [])
        if not pools:
            return None
        pool_addr = pools[0]["attributes"]["address"]
        _token_pool_cache[token_address] = {"pool": pool_addr, "exp": time.time() + 3600}
        return pool_addr
    except Exception as exc:
        log.warning("_gt_get_pool error: %s", exc)
        return None'''

NEW_POOL = '''def _gt_get_pool(token_address: str):
    """Return highest-liquidity Solana pool address for a token (GeckoTerminal)."""
    import requests as _req
    import time as _time
    cached = _token_pool_cache.get(token_address)
    if cached and cached["exp"] > _time.time():
        return cached["pool"]
    try:
        r = _req.get(
            f"https://api.geckoterminal.com/api/v2/networks/solana/tokens/{token_address}/pools",
            params={"page": 1},
            headers={"Accept": "application/json"},
            timeout=10,
        )
        pools = r.json().get("data", [])
        if not pools:
            return None
        pool_addr = pools[0]["attributes"]["address"]
        _token_pool_cache[token_address] = {"pool": pool_addr, "exp": _time.time() + 3600}
        return pool_addr
    except Exception as exc:
        log.warning("_gt_get_pool error: %s", exc)
        return None'''

assert OLD_POOL in main_text, "OLD_POOL anchor not found"
main_text = main_text.replace(OLD_POOL, NEW_POOL)
print("✓ _gt_get_pool fixed")

# ── Fix token_chart_data — replace time.time() calls ─────────────────────────
OLD_CHART_CACHE = '''    cached = _token_ohlcv_cache.get(cache_key)
    if cached and cached["exp"] > time.time():
        return JSONResponse(cached["data"])'''

NEW_CHART_CACHE = '''    import time as _time
    cached = _token_ohlcv_cache.get(cache_key)
    if cached and cached["exp"] > _time.time():
        return JSONResponse(cached["data"])'''

assert OLD_CHART_CACHE in main_text, "OLD_CHART_CACHE anchor not found"
main_text = main_text.replace(OLD_CHART_CACHE, NEW_CHART_CACHE)

OLD_CHART_STORE = '''        ttl = 60 if resolution in ("1m", "5m") else 300
        _token_ohlcv_cache[cache_key] = {"data": result, "exp": time.time() + ttl}'''
NEW_CHART_STORE = '''        ttl = 60 if resolution in ("1m", "5m") else 300
        _token_ohlcv_cache[cache_key] = {"data": result, "exp": _time.time() + ttl}'''

assert OLD_CHART_STORE in main_text, "OLD_CHART_STORE anchor not found"
main_text = main_text.replace(OLD_CHART_STORE, NEW_CHART_STORE)
print("✓ token_chart_data time refs fixed")

# ── Fix token_recent_trades ───────────────────────────────────────────────────
OLD_TRADES_CACHE = '''    cached = _token_trades_cache.get(address)
    if cached and cached["exp"] > time.time():
        return JSONResponse(cached["data"])'''
NEW_TRADES_CACHE = '''    import time as _time
    cached = _token_trades_cache.get(address)
    if cached and cached["exp"] > _time.time():
        return JSONResponse(cached["data"])'''

assert OLD_TRADES_CACHE in main_text, "OLD_TRADES_CACHE anchor not found"
main_text = main_text.replace(OLD_TRADES_CACHE, NEW_TRADES_CACHE)

OLD_TRADES_STORE = '''        _token_trades_cache[address] = {"data": result, "exp": time.time() + 15}'''
NEW_TRADES_STORE = '''        _token_trades_cache[address] = {"data": result, "exp": _time.time() + 15}'''

assert OLD_TRADES_STORE in main_text, "OLD_TRADES_STORE anchor not found"
main_text = main_text.replace(OLD_TRADES_STORE, NEW_TRADES_STORE)
print("✓ token_recent_trades time refs fixed")

MAIN.write_text(main_text)

r = subprocess.run([sys.executable, "-m", "py_compile", str(MAIN)], capture_output=True, text=True)
if r.returncode != 0:
    print("✗ compile error:", r.stderr); sys.exit(1)
print("✓ main.py compiles OK")
