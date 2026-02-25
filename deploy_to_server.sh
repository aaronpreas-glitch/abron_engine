#!/bin/bash
# =============================================================================
# MEMECOIN ENGINE — FULL DEPLOY SCRIPT
# Run this ON the DigitalOcean server AFTER ssh -i ~/.ssh/memecoin_deploy root@69.62.71.67
# =============================================================================
set -e
PROJECT="/root/memecoin_engine"

echo "=== STEP 1: Patching utils/format.py (MARKETCAP + _fmt_price_precise) ==="
python3 - << 'PYEOF'
import ast, sys

path = "/root/memecoin_engine/utils/format.py"
with open(path, "r") as f:
    src = f.read()

orig = src  # keep a backup reference

# ── 1. Add _fmt_price_precise() after _fmt_usd_compact() if not already there ──
if "_fmt_price_precise" not in src:
    old_fn = '''def _fmt_holders(value):'''
    new_fn = '''def _fmt_price_precise(value):
    try:
        p = float(value)
    except (TypeError, ValueError):
        return "N/A"
    if p <= 0:
        return "N/A"
    if p < 0.01:
        return f"${p:.8f}".rstrip("0").rstrip(".")
    if p < 1:
        return f"${p:.6f}".rstrip("0").rstrip(".")
    if p < 1000:
        return f"${p:.4f}".rstrip("0").rstrip(".")
    return _fmt_usd_compact(p)


def _fmt_holders(value):'''
    if old_fn in src:
        src = src.replace(old_fn, new_fn, 1)
        print("  ✓ Added _fmt_price_precise()")
    else:
        print("  ✗ Could not find _fmt_holders() anchor — check file manually")
else:
    print("  ✓ _fmt_price_precise() already present — skipping")

# ── 2. Replace PRICE row with MARKETCAP + ENTRY rows in format_signal() ──
old_price_row = '        _kv("PRICE", f"{_fmt_usd_compact(token_data.get(\'price\'))} | 24H {change_line}"),'
new_rows = '        _kv("MARKETCAP", f"{_fmt_usd_compact(cap_value)} | 24H {change_line}"),\n        _kv("ENTRY", _fmt_price_precise(token_data.get(\'price\'))),'

if old_price_row in src:
    src = src.replace(old_price_row, new_rows, 1)
    print("  ✓ Replaced PRICE with MARKETCAP+ENTRY in format_signal()")
elif '_kv("MARKETCAP"' in src and '_kv("ENTRY"' in src:
    print("  ✓ MARKETCAP+ENTRY rows already present in format_signal() — skipping")
else:
    print("  ! PRICE row not found in format_signal() — may already be patched or different format")

# ── 3. Update format_legacy_recovery() to show MARKETCAP ──
old_price_line = '    f"<code>💰 PRICE: ${_esc(display_price)}</code>",'
new_cap_lines = '''    # Marketcap display (prefer market_cap, fall back to fdv)
    cap_value = market_cap if isinstance(market_cap, (int, float)) and market_cap > 0 else fdv
    cap_display = _fmt_usd_compact(cap_value) if cap_value else "N/A"
'''

# Check if already updated
if '💰 MARKETCAP:' in src:
    print("  ✓ format_legacy_recovery() already shows MARKETCAP — skipping")
else:
    # Try to replace the PRICE line with MARKETCAP
    if old_price_line in src:
        new_price_line = '    f"<code>💰 MARKETCAP: {_esc(cap_display)}</code>",'
        src = src.replace(old_price_line, new_price_line, 1)
        print("  ✓ Replaced 💰 PRICE with 💰 MARKETCAP in format_legacy_recovery()")

        # Also add cap_value/cap_display extraction before the lines block
        old_anchor = '    lines = ['
        if old_anchor in src and 'cap_display' not in src:
            cap_extract = '''    # Marketcap display (prefer market_cap, fall back to fdv)
    cap_value = market_cap if isinstance(market_cap, (int, float)) and market_cap > 0 else fdv
    cap_display = _fmt_usd_compact(cap_value) if cap_value else "N/A"

    lines = ['''
            src = src.replace('    lines = [', cap_extract, 1)
            print("  ✓ Added cap_value/cap_display extraction in format_legacy_recovery()")
    else:
        print("  ! Could not find PRICE line in format_legacy_recovery() — may be different format")

# ── 4. Also ensure market_cap/fdv are extracted in format_legacy_recovery ──
# Check if market_cap is already read in that function
if 'market_cap = token_data.get("market_cap")' not in src:
    old_liq = '    liquidity = float(token_data.get("liquidity", 0) or 0)\n    market_cap = token_data.get("market_cap")'
    if old_liq not in src:
        # Try adding after liquidity line
        old_liq2 = '    liquidity = float(token_data.get("liquidity", 0) or 0)'
        if old_liq2 in src and 'def format_legacy_recovery' in src:
            src = src.replace(
                '    liquidity = float(token_data.get("liquidity", 0) or 0)\n    pattern_label',
                '    liquidity = float(token_data.get("liquidity", 0) or 0)\n    market_cap = token_data.get("market_cap")\n    fdv = token_data.get("fdv")\n    pattern_label',
                1
            )
            print("  ✓ Added market_cap/fdv extraction in format_legacy_recovery()")

# ── 5. Ensure Entry: line uses display_price ──
if 'Entry: {_esc(display_price)}' not in src and '📍 Entry:' in src:
    print("  ! Entry line format may be different — review manually")
else:
    print("  ✓ Entry line looks correct")

# ── Validate syntax ──
try:
    ast.parse(src)
    print("  ✓ Syntax check passed")
except SyntaxError as e:
    print(f"  ✗ SYNTAX ERROR: {e}")
    print("  Aborting — not writing file")
    sys.exit(1)

if src != orig:
    with open(path, "w") as f:
        f.write(src)
    print(f"  ✓ Written: {path}")
else:
    print("  ℹ No changes needed to format.py")
PYEOF

echo ""
echo "=== STEP 2: Patching data/dexscreener.py (broad legacy recovery fetch) ==="
python3 - << 'PYEOF'
import ast, sys

path = "/root/memecoin_engine/data/dexscreener.py"
with open(path, "r") as f:
    src = f.read()

orig = src

if "_LEGACY_BROAD_QUERIES" in src and "fetch_legacy_recovery_candidates" in src:
    print("  ✓ _LEGACY_BROAD_QUERIES + fetch_legacy_recovery_candidates already present — skipping")
else:
    addition = '''

# Broad keyword set that covers the established Solana memecoin universe.
# DexScreener search returns up to ~30 pairs per query — these terms collectively
# surface hundreds of old/established tokens without a hardcoded whitelist.
_LEGACY_BROAD_QUERIES = [
    "SOL", "BONK", "WIF", "PEPE", "DOGE", "SHIB", "FLOKI",
    "POPCAT", "BOME", "MYRO", "NEIRO", "MOODENG", "PNUT", "GOAT",
    "MEW", "BRETT", "TURBO", "SLERF", "SAMO", "COPE", "JUP",
    "RENDER", "PYTH", "ORCA", "STEP", "MNGO", "CATS", "FETCH",
    "AI", "MEME", "CAT", "DOG", "FROG", "MONKEY", "TRUMP", "PONKE",
]


def fetch_legacy_recovery_candidates(
    queries=None,
    pairs_per_query: int = 10,
    limit: int = 300,
):
    """
    Fetch broad DexScreener candidates for the Legacy Recovery scanner.
    Uses a wide keyword sweep to find ALL established Solana tokens,
    not just a hardcoded list. Falls back to _LEGACY_BROAD_QUERIES when
    no custom queries are provided.
    """
    use_queries = queries if queries else _LEGACY_BROAD_QUERIES
    try:
        endpoint = f"{_base_url().rstrip(\'/\')}/latest/dex/search"
        unique_tokens = {}

        for query in use_queries:
            try:
                response = requests.get(
                    endpoint,
                    params={"q": query},
                    timeout=15,
                )
                response.raise_for_status()
                data = response.json() or {}
                pairs = data.get("pairs", []) or []
                for pair in pairs[:max(1, pairs_per_query)]:
                    token = _normalize_pair(pair)
                    if not token:
                        continue
                    addr = token["address"]
                    existing = unique_tokens.get(addr)
                    if not existing:
                        unique_tokens[addr] = token
                    elif (token.get("liquidity") or 0) > (existing.get("liquidity") or 0):
                        unique_tokens[addr] = token
            except requests.exceptions.RequestException:
                continue

        tokens = list(unique_tokens.values())
        tokens.sort(
            key=lambda t: (t.get("liquidity") or 0, t.get("volume_24h") or 0),
            reverse=True,
        )
        return tokens[:max(1, limit)]

    except Exception:
        return []
'''
    src = src.rstrip() + "\n" + addition + "\n"
    print("  ✓ Appended _LEGACY_BROAD_QUERIES + fetch_legacy_recovery_candidates()")

try:
    ast.parse(src)
    print("  ✓ Syntax check passed")
except SyntaxError as e:
    print(f"  ✗ SYNTAX ERROR: {e}")
    sys.exit(1)

if src != orig:
    with open(path, "w") as f:
        f.write(src)
    print(f"  ✓ Written: {path}")
else:
    print("  ℹ No changes needed to dexscreener.py")
PYEOF

echo ""
echo "=== STEP 3: Patching main.py (import + use fetch_legacy_recovery_candidates) ==="
python3 - << 'PYEOF'
import ast, sys

path = "/root/memecoin_engine/main.py"
with open(path, "r") as f:
    src = f.read()

orig = src

# ── Add fetch_legacy_recovery_candidates to dexscreener imports ──
if "fetch_legacy_recovery_candidates" in src:
    print("  ✓ fetch_legacy_recovery_candidates already imported — skipping import patch")
else:
    # Find the dexscreener import block and add to it
    old_import = "from data.dexscreener import ("
    if old_import in src:
        # Find the closing paren of the import block
        idx = src.index(old_import)
        end_idx = src.index(")", idx)
        old_block = src[idx:end_idx+1]
        # Add fetch_legacy_recovery_candidates before closing paren
        new_block = old_block[:-1].rstrip() + "\n    fetch_legacy_recovery_candidates,\n)"
        src = src.replace(old_block, new_block, 1)
        print("  ✓ Added fetch_legacy_recovery_candidates to dexscreener imports")
    else:
        # Try single-line import
        old_single = "from data.dexscreener import fetch_runner_watch_candidates"
        if old_single in src:
            src = src.replace(old_single, old_single + ", fetch_legacy_recovery_candidates", 1)
            print("  ✓ Added fetch_legacy_recovery_candidates to single-line import")
        else:
            print("  ! Could not find dexscreener import to patch — add manually")

# ── Also add LEGACY_RECOVERY_SEARCH_QUERIES + LEGACY_RECOVERY_PAIRS_PER_QUERY to config imports ──
if "LEGACY_RECOVERY_SEARCH_QUERIES" not in src:
    old_cfg = "from config import ("
    if old_cfg in src:
        idx = src.index(old_cfg)
        end_idx = src.index(")", idx)
        old_block = src[idx:end_idx+1]
        new_block = old_block[:-1].rstrip() + "\n    LEGACY_RECOVERY_SEARCH_QUERIES,\n    LEGACY_RECOVERY_PAIRS_PER_QUERY,\n)"
        src = src.replace(old_block, new_block, 1)
        print("  ✓ Added LEGACY_RECOVERY_SEARCH_QUERIES + LEGACY_RECOVERY_PAIRS_PER_QUERY to config imports")
    else:
        print("  ! Could not find config import to patch")
else:
    print("  ✓ LEGACY_RECOVERY_SEARCH_QUERIES already in imports — skipping")

# ── Patch run_legacy_recovery_scanner() to use broad fetch ──
if "fetch_legacy_recovery_candidates" in src and "run_legacy_recovery_scanner" in src:
    # Check if the function already uses fetch_legacy_recovery_candidates
    fn_start = src.find("async def run_legacy_recovery_scanner")
    fn_end = src.find("\nasync def ", fn_start + 1)
    if fn_end == -1:
        fn_end = len(src)
    fn_body = src[fn_start:fn_end]

    if "fetch_legacy_recovery_candidates" in fn_body:
        print("  ✓ run_legacy_recovery_scanner() already uses fetch_legacy_recovery_candidates — skipping")
    else:
        # Replace old hardcoded token list logic with broad fetch
        old_tokens_line = "        tokens = fetch_legacy_tokens()"
        new_tokens_block = """        custom_queries = LEGACY_RECOVERY_SEARCH_QUERIES if LEGACY_RECOVERY_SEARCH_QUERIES else None
        tokens = fetch_legacy_recovery_candidates(
            queries=custom_queries,
            pairs_per_query=LEGACY_RECOVERY_PAIRS_PER_QUERY,
            limit=300,
        )"""
        if old_tokens_line in src:
            src = src.replace(old_tokens_line, new_tokens_block, 1)
            print("  ✓ Replaced fetch_legacy_tokens() with fetch_legacy_recovery_candidates() call")
        else:
            print("  ! Could not find fetch_legacy_tokens() call — check run_legacy_recovery_scanner() manually")
else:
    print("  ! run_legacy_recovery_scanner or fetch_legacy_recovery_candidates not found in main.py")

try:
    ast.parse(src)
    print("  ✓ Syntax check passed")
except SyntaxError as e:
    print(f"  ✗ SYNTAX ERROR: {e}")
    sys.exit(1)

if src != orig:
    with open(path, "w") as f:
        f.write(src)
    print(f"  ✓ Written: {path}")
else:
    print("  ℹ No changes needed to main.py")
PYEOF

echo ""
echo "=== STEP 4: Restarting memecoin-engine service ==="
systemctl restart memecoin-engine
sleep 3
systemctl status memecoin-engine --no-pager -l | head -30
echo ""
echo "=== DEPLOY COMPLETE ==="
