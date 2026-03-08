"""
Patch 92 — Fix Jupiter Price v2 401 errors

Jupiter's https://api.jup.ag/price/v2 now requires auth, causing 401 errors
every 8-30s across 3 files. Switch all price lookups to:
  Primary:  Birdeye REST (we have BIRDEYE_API_KEY in .env)
  Fallback: DexScreener (free, no auth)

Files patched:
  utils/jupiter_swap.py  — get_token_price_usd()
  utils/ws_price_feed.py — JUPITER_PRICE_URL + _fetch_jupiter_prices()
  dashboard/backend/main.py — 2 inline Jupiter price calls (lines ~2702, ~2978)

Also cleans up duplicate ENABLE_REGIME_GATE=false in .env
"""
import sys, os
sys.path.insert(0, '/root/memecoin_engine')

BASE = '/root/memecoin_engine'

# ─── 1. utils/jupiter_swap.py ─────────────────────────────────────────────────

p = os.path.join(BASE, 'utils', 'jupiter_swap.py')
txt = open(p).read()

OLD_GET_PRICE = '''async def get_token_price_usd(mint: str) -> Optional[float]:
    """Fetch current token price in USD from Jupiter Price API."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(PRICE_API, params={"ids": mint})
            r.raise_for_status()
            data = r.json()
        price_data = data.get("data", {}).get(mint, {})
        price = float(price_data.get("price", 0) or 0)
        return price if price > 0 else None
    except Exception as exc:
        logger.warning("Token price fetch failed for %s: %s", mint[:8], exc)
        return None'''

NEW_GET_PRICE = '''async def get_token_price_usd(mint: str) -> Optional[float]:
    """Fetch current token price in USD. Tries Birdeye first, then DexScreener."""
    birdeye_key = os.getenv("BIRDEYE_API_KEY", "")
    # 1. Birdeye
    if birdeye_key:
        try:
            async with httpx.AsyncClient(timeout=8) as client:
                r = await client.get(
                    "https://public-api.birdeye.so/defi/price",
                    params={"address": mint},
                    headers={"X-API-KEY": birdeye_key, "x-chain": "solana"},
                )
                if r.status_code == 200:
                    val = r.json().get("data", {}).get("value")
                    if val and float(val) > 0:
                        return float(val)
        except Exception:
            pass
    # 2. DexScreener fallback
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get(
                f"https://api.dexscreener.com/latest/dex/tokens/{mint}",
                headers={"User-Agent": "MemeEngine/1.0"},
            )
            if r.status_code == 200:
                pairs = r.json().get("pairs") or []
                sol_pairs = [p for p in pairs if p.get("quoteToken", {}).get("symbol", "") in ("SOL", "WSOL")]
                best = sorted(sol_pairs or pairs, key=lambda p: float(p.get("volume", {}).get("h24", 0) or 0), reverse=True)
                if best:
                    price = float(best[0].get("priceUsd") or 0)
                    if price > 0:
                        return price
    except Exception as exc:
        logger.warning("Token price fetch failed for %s: %s", mint[:8], exc)
    return None'''

assert OLD_GET_PRICE in txt, "Anchor not found in jupiter_swap.py"
txt = txt.replace(OLD_GET_PRICE, NEW_GET_PRICE)
open(p, 'w').write(txt)
print("✓ utils/jupiter_swap.py patched")

# ─── 2. utils/ws_price_feed.py ────────────────────────────────────────────────

p = os.path.join(BASE, 'utils', 'ws_price_feed.py')
txt = open(p).read()

OLD_FETCH_JUPITER = '''async def _fetch_jupiter_prices(mints: list[str]) -> None:
    """Fetch prices for a list of mints from Jupiter Price API v2."""
    if not mints:
        return
    # Jupiter supports comma-separated mints in ids param
    ids = ",".join(mints)
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(JUPITER_PRICE_URL, params={"ids": ids})
        r.raise_for_status()
        data = r.json().get("data", {})
        for mint, info in data.items():
            price_raw = info.get("price")
            if price_raw is not None:
                price = float(price_raw)
                # Only push if WS hasn't updated recently (< 5s ago)
                age = get_price_age(mint)
                if age is None or age > 5.0:
                    _push_price(mint, price)'''

NEW_FETCH_JUPITER = '''async def _fetch_jupiter_prices(mints: list[str]) -> None:
    """Fetch prices for a list of mints. Uses Birdeye multi-price, fallback DexScreener."""
    if not mints:
        return
    birdeye_key = os.getenv("BIRDEYE_API_KEY", "")
    fetched: dict[str, float] = {}

    # 1. Birdeye multi_price
    if birdeye_key:
        try:
            list_addr = ",".join(mints)
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(
                    "https://public-api.birdeye.so/defi/multi_price",
                    params={"list_address": list_addr},
                    headers={"X-API-KEY": birdeye_key, "x-chain": "solana"},
                )
                if r.status_code == 200:
                    data = r.json().get("data", {})
                    for mint, info in data.items():
                        val = (info or {}).get("value")
                        if val and float(val) > 0:
                            fetched[mint] = float(val)
        except Exception:
            pass

    # 2. DexScreener fallback for any mints not resolved by Birdeye
    remaining = [m for m in mints if m not in fetched]
    for mint in remaining:
        try:
            async with httpx.AsyncClient(timeout=8) as client:
                r = await client.get(
                    f"https://api.dexscreener.com/latest/dex/tokens/{mint}",
                    headers={"User-Agent": "MemeEngine/1.0"},
                )
                if r.status_code == 200:
                    pairs = r.json().get("pairs") or []
                    sol_pairs = [p for p in pairs if p.get("quoteToken", {}).get("symbol", "") in ("SOL", "WSOL")]
                    best = sorted(sol_pairs or pairs, key=lambda p: float(p.get("volume", {}).get("h24", 0) or 0), reverse=True)
                    if best:
                        price = float(best[0].get("priceUsd") or 0)
                        if price > 0:
                            fetched[mint] = price
        except Exception:
            pass

    for mint, price in fetched.items():
        age = get_price_age(mint)
        if age is None or age > 5.0:
            _push_price(mint, price)'''

assert OLD_FETCH_JUPITER in txt, "Anchor not found in ws_price_feed.py"
txt = txt.replace(OLD_FETCH_JUPITER, NEW_FETCH_JUPITER)
open(p, 'w').write(txt)
print("✓ utils/ws_price_feed.py patched")

# ─── 3. main.py — inline Jupiter call #1 (symbol signal endpoint) ─────────────

p = os.path.join(BASE, 'dashboard', 'backend', 'main.py')
txt = open(p).read()

OLD_INLINE1 = '''    if mint:
        try:
            async with _httpx.AsyncClient(timeout=8) as client:
                r = await client.get("https://api.jup.ag/price/v2", params={"ids": mint})
                price_str = r.json().get("data", {}).get(mint, {}).get("price")
                if price_str:
                    mark_price = float(price_str)
        except Exception:
            pass'''

NEW_INLINE1 = '''    if mint:
        try:
            _be_key = _os.environ.get("BIRDEYE_API_KEY", "")
            if _be_key:
                import httpx as _hx2
                async with _hx2.AsyncClient(timeout=8) as _c2:
                    _r2 = await _c2.get("https://public-api.birdeye.so/defi/price",
                        params={"address": mint},
                        headers={"X-API-KEY": _be_key, "x-chain": "solana"})
                    if _r2.status_code == 200:
                        _val = _r2.json().get("data", {}).get("value")
                        if _val and float(_val) > 0:
                            mark_price = float(_val)
        except Exception:
            pass'''

assert OLD_INLINE1 in txt, "Anchor #1 not found in main.py"
txt = txt.replace(OLD_INLINE1, NEW_INLINE1)
print("✓ main.py inline call #1 patched")

# ─── 4. main.py — inline Jupiter call #2 (spot live-pnl endpoint) ─────────────

OLD_INLINE2 = '''    # Jupiter Price API v2 — batch up to 100 mints per call
    mints_csv = ",".join(mint_to_ids.keys())
    result: dict[str, Any] = {}
    try:
        async with _httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                "https://api.jup.ag/price/v2",
                params={"ids": mints_csv},
            )
            data = r.json().get("data", {})

        for mint, trades in mint_to_ids.items():
            price_str = data.get(mint, {}).get("price")
            if not price_str:
                continue
            mark = float(price_str)
            for trade_id, entry in trades:
                pnl_pct = ((mark - entry) / entry) * 100.0
                result[str(trade_id)] = {
                    "mark_price": mark,
                    "pnl_pct": round(pnl_pct, 3),
                }
    except Exception as exc:
        log.warning("live-pnl fetch error: %s", exc)'''

NEW_INLINE2 = '''    # Price fetch — Birdeye multi_price (primary), DexScreener per-mint (fallback)
    result: dict[str, Any] = {}
    fetched_prices: dict[str, float] = {}
    _be_key = _os.environ.get("BIRDEYE_API_KEY", "")
    try:
        if _be_key:
            import httpx as _hx3
            async with _hx3.AsyncClient(timeout=10) as _c3:
                _r3 = await _c3.get(
                    "https://public-api.birdeye.so/defi/multi_price",
                    params={"list_address": ",".join(mint_to_ids.keys())},
                    headers={"X-API-KEY": _be_key, "x-chain": "solana"},
                )
                if _r3.status_code == 200:
                    for _mint, _info in (_r3.json().get("data") or {}).items():
                        _v = (_info or {}).get("value")
                        if _v and float(_v) > 0:
                            fetched_prices[_mint] = float(_v)
        # DexScreener fallback for any mints not resolved
        import httpx as _hx3b
        for _mint in mint_to_ids:
            if _mint in fetched_prices:
                continue
            try:
                async with _hx3b.AsyncClient(timeout=8) as _c3b:
                    _r3b = await _c3b.get(
                        f"https://api.dexscreener.com/latest/dex/tokens/{_mint}",
                        headers={"User-Agent": "MemeEngine/1.0"},
                    )
                    if _r3b.status_code == 200:
                        _pairs = _r3b.json().get("pairs") or []
                        _sol = [_p for _p in _pairs if _p.get("quoteToken", {}).get("symbol", "") in ("SOL", "WSOL")]
                        _best = sorted(_sol or _pairs, key=lambda _p: float(_p.get("volume", {}).get("h24", 0) or 0), reverse=True)
                        if _best:
                            _prc = float(_best[0].get("priceUsd") or 0)
                            if _prc > 0:
                                fetched_prices[_mint] = _prc
            except Exception:
                pass
    except Exception as exc:
        log.warning("live-pnl fetch error: %s", exc)

    for mint, trades in mint_to_ids.items():
        mark = fetched_prices.get(mint)
        if not mark:
            continue
        for trade_id, entry in trades:
            pnl_pct = ((mark - entry) / entry) * 100.0
            result[str(trade_id)] = {
                "mark_price": mark,
                "pnl_pct": round(pnl_pct, 3),
            }'''

assert OLD_INLINE2 in txt, "Anchor #2 not found in main.py"
txt = txt.replace(OLD_INLINE2, NEW_INLINE2)
open(p, 'w').write(txt)
print("✓ main.py inline call #2 patched")

# ─── 5. Clean up duplicate ENABLE_REGIME_GATE in .env ────────────────────────

env_path = os.path.join(BASE, '.env')
env_lines = open(env_path).readlines()
seen_keys = set()
clean_lines = []
for line in env_lines:
    key = line.split('=')[0].strip()
    if key and not key.startswith('#') and key in seen_keys:
        print(f"  ↳ Removing duplicate .env key: {key}")
        continue
    if key and not key.startswith('#'):
        seen_keys.add(key)
    clean_lines.append(line)
open(env_path, 'w').writelines(clean_lines)
print("✓ .env duplicate keys cleaned")

print("\nAll done. Run: systemctl restart memecoin-dashboard")
