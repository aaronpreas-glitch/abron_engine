"""
Patch 74 — Solana Memecoins + Perps Altcoin Refinement

Backend changes:
  A. utils/perp_executor.py
     A1. Extend CG_IDS to include 9 new perp altcoins
     A2. Extend hard symbol guard to include new alts

  B. dashboard/backend/main.py
     B1. _perp_signal_scan_loop: extend _CG_IDS for new alts
     B2. _scalp_signal_scan_loop: extend _KRAKEN_PAIRS for new alts
     B3. _mid_signal_scan_loop: extend _KRAKEN_PAIRS for new alts
     B4. perps/manual-open force-fire guard: extend allowed set
     B5. market_movers _MEMECOINS: replace with Solana-only set
     B6. market_movers _MEME_SP: replace with Solana-only set
     B7. CryptoPanic default currencies: update
     B8. CryptoPanic memecoins override: update

Frontend + .env updates done separately (watchlist entries script).
"""

import subprocess, sys
from pathlib import Path

ROOT   = Path("/root/memecoin_engine")
EXEC   = ROOT / "utils/perp_executor.py"
MAIN   = ROOT / "dashboard/backend/main.py"

# ─────────────────────────────────────────────────────────────────────────────
# A. utils/perp_executor.py
# ─────────────────────────────────────────────────────────────────────────────

exe_text = EXEC.read_text()

# A1 — Extend CG_IDS ──────────────────────────────────────────────────────────
OLD_CG = 'CG_IDS = {"SOL": "solana", "BTC": "bitcoin", "ETH": "ethereum"}'
NEW_CG = (
    'CG_IDS = {\n'
    '    "SOL": "solana", "BTC": "bitcoin", "ETH": "ethereum",\n'
    '    # Perp altcoin expansion (Patch 74)\n'
    '    "SUI":  "sui",\n'
    '    "TON":  "the-open-network",\n'
    '    "AVAX": "avalanche-2",\n'
    '    "ARB":  "arbitrum",\n'
    '    "OP":   "optimism",\n'
    '    "NEAR": "near",\n'
    '    "INJ":  "injective-protocol",\n'
    '    "SEI":  "sei-network",\n'
    '    "APT":  "aptos",\n'
    '}'
)
assert OLD_CG in exe_text, f"A1 anchor not found: {OLD_CG!r}"
exe_text = exe_text.replace(OLD_CG, NEW_CG)
print("A1 OK — CG_IDS extended")

# A2 — Extend hard symbol guard ───────────────────────────────────────────────
OLD_GUARD = '    if symbol not in ("SOL", "BTC", "ETH"):\n        logger.warning("Unsupported perp symbol: %s", symbol)\n        return False'
NEW_GUARD = (
    '    _PERP_ALLOWED = {\n'
    '        "SOL", "BTC", "ETH",\n'
    '        # Altcoin expansion (Patch 74)\n'
    '        "SUI", "TON", "AVAX", "ARB", "OP", "NEAR", "INJ", "SEI", "APT",\n'
    '    }\n'
    '    if symbol not in _PERP_ALLOWED:\n'
    '        logger.warning("Unsupported perp symbol: %s", symbol)\n'
    '        return False'
)
assert OLD_GUARD in exe_text, "A2 anchor not found"
exe_text = exe_text.replace(OLD_GUARD, NEW_GUARD)
print("A2 OK — symbol guard extended")

EXEC.write_text(exe_text)

# Compile check
r = subprocess.run([sys.executable, "-m", "py_compile", str(EXEC)], capture_output=True, text=True)
if r.returncode != 0:
    print("COMPILE ERROR perp_executor.py:", r.stderr); sys.exit(1)
print("A OK — perp_executor.py compiles clean")

# ─────────────────────────────────────────────────────────────────────────────
# B. dashboard/backend/main.py
# ─────────────────────────────────────────────────────────────────────────────

main_text = MAIN.read_text()

_ALT_CG = (
    '        "SUI":  "sui",\n'
    '        "TON":  "the-open-network",\n'
    '        "AVAX": "avalanche-2",\n'
    '        "ARB":  "arbitrum",\n'
    '        "OP":   "optimism",\n'
    '        "NEAR": "near",\n'
    '        "INJ":  "injective-protocol",\n'
    '        "SEI":  "sei-network",\n'
    '        "APT":  "aptos",\n'
)

_ALT_KRAKEN = (
    '        # Altcoin expansion (Patch 74) — all confirmed on Kraken\n'
    '        "SUI":  ("SUIUSD",  "SUIUSD"),\n'
    '        "TON":  ("TONUSD",  "TONUSD"),\n'
    '        "AVAX": ("AVAXUSD", "AVAXUSD"),\n'
    '        "ARB":  ("ARBUSD",  "ARBUSD"),\n'
    '        "OP":   ("OPUSD",   "OPUSD"),\n'
    '        "NEAR": ("NEARUSD", "NEARUSD"),\n'
    '        "INJ":  ("INJUSD",  "INJUSD"),\n'
    '        "SEI":  ("SEIUSD",  "SEIUSD"),\n'
    '        "APT":  ("APTUSD",  "APTUSD"),\n'
)

# B1 — _perp_signal_scan_loop _CG_IDS ────────────────────────────────────────
OLD_B1 = '    _CG_IDS = {"SOL": "solana", "BTC": "bitcoin", "ETH": "ethereum"}'
NEW_B1 = (
    '    _CG_IDS = {\n'
    '        "SOL": "solana", "BTC": "bitcoin", "ETH": "ethereum",\n'
    + _ALT_CG +
    '    }'
)
assert OLD_B1 in main_text, "B1 anchor not found"
main_text = main_text.replace(OLD_B1, NEW_B1)
print("B1 OK — perp_scan _CG_IDS extended")

# B2 — _scalp_signal_scan_loop _KRAKEN_PAIRS (unique: has "Kraken normalises" comment) ─
OLD_B2 = (
    '    # Kraken pair names: (query_pair, result_key)\n'
    '    # Kraken normalises result keys differently from query params (e.g. XBTUSD \u2192 XXBTZUSD)\n'
    '    _KRAKEN_PAIRS = {\n'
    '        "SOL": ("SOLUSD",  "SOLUSD"),\n'
    '        "BTC": ("XBTUSD",  "XXBTZUSD"),\n'
    '        "ETH": ("ETHUSD",  "XETHZUSD"),\n'
    '    }'
)
NEW_B2 = (
    '    # Kraken pair names: (query_pair, result_key)\n'
    '    # Kraken normalises result keys differently from query params (e.g. XBTUSD \u2192 XXBTZUSD)\n'
    '    _KRAKEN_PAIRS = {\n'
    '        "SOL": ("SOLUSD",  "SOLUSD"),\n'
    '        "BTC": ("XBTUSD",  "XXBTZUSD"),\n'
    '        "ETH": ("ETHUSD",  "XETHZUSD"),\n'
    + _ALT_KRAKEN +
    '    }'
)
assert OLD_B2 in main_text, "B2 anchor not found"
main_text = main_text.replace(OLD_B2, NEW_B2)
print("B2 OK — scalp_scan _KRAKEN_PAIRS extended")

# B3 — _mid_signal_scan_loop _KRAKEN_PAIRS (no preceding comments, unique via sleep(120)) ─
OLD_B3 = (
    '        await asyncio.sleep(120)\n'
    '        try:\n'
    '            mid_enabled = os.getenv("MID_ENABLED", "false").lower() == "true"'
)
# We insert the new _KRAKEN_PAIRS replacement by using a different anchor approach:
# The mid loop has its own _KRAKEN_PAIRS with no preceding comments.
# We replace the full _KRAKEN_PAIRS block that immediately follows the MID_ENABLED check.
OLD_B3_PAIRS = (
    '    _KRAKEN_PAIRS = {\n'
    '        "SOL": ("SOLUSD",  "SOLUSD"),\n'
    '        "BTC": ("XBTUSD",  "XXBTZUSD"),\n'
    '        "ETH": ("ETHUSD",  "XETHZUSD"),\n'
    '    }\n'
    '\n'
    '    while True:\n'
    '        await asyncio.sleep(120)\n'
    '        try:\n'
    '            mid_enabled = os.getenv("MID_ENABLED", "false").lower() == "true"'
)
NEW_B3_PAIRS = (
    '    _KRAKEN_PAIRS = {\n'
    '        "SOL": ("SOLUSD",  "SOLUSD"),\n'
    '        "BTC": ("XBTUSD",  "XXBTZUSD"),\n'
    '        "ETH": ("ETHUSD",  "XETHZUSD"),\n'
    + _ALT_KRAKEN +
    '    }\n'
    '\n'
    '    while True:\n'
    '        await asyncio.sleep(120)\n'
    '        try:\n'
    '            mid_enabled = os.getenv("MID_ENABLED", "false").lower() == "true"'
)
assert OLD_B3_PAIRS in main_text, "B3 anchor not found"
main_text = main_text.replace(OLD_B3_PAIRS, NEW_B3_PAIRS)
print("B3 OK — mid_scan _KRAKEN_PAIRS extended")

# B4 — force-fire guard ────────────────────────────────────────────────────────
OLD_B4 = (
    '        if symbol not in ("SOL", "BTC", "ETH"):\n'
    '            return JSONResponse({"success": False, "error": "symbol must be SOL, BTC, or ETH"}, status_code=400)'
)
NEW_B4 = (
    '        _MANUAL_ALLOWED = {\n'
    '            "SOL", "BTC", "ETH",\n'
    '            "SUI", "TON", "AVAX", "ARB", "OP", "NEAR", "INJ", "SEI", "APT",\n'
    '        }\n'
    '        if symbol not in _MANUAL_ALLOWED:\n'
    '            return JSONResponse({"success": False, "error": "symbol must be SOL, BTC, ETH or supported alt"}, status_code=400)'
)
assert OLD_B4 in main_text, "B4 anchor not found"
main_text = main_text.replace(OLD_B4, NEW_B4)
print("B4 OK — force-fire guard extended")

# B5 — _MEMECOINS: replace with Solana-only set ───────────────────────────────
OLD_B5 = '''    _MEMECOINS = {
        "DOGE","SHIB","PEPE","FLOKI","BONK","WIF","POPCAT","MEW","BRETT","MOG",
        "NEIRO","TURBO","WOJAK","LADYS","MEME","COQ","APU","GIGA","PNUT","SLERF",
        "PONKE","BOME","SILLY","MYRO","SMOL","DEGEN","BABYDOGE","ELON","SAMO",
        "MAGA","REDO","NOOT","CAT","DOG","RATS","SNEK","MANEKI","MICHI",
        "MOODENG","CHILLGUY","ACT","GRASS","ZEREBRO","GRIFFAIN","FARTCOIN",
        "VINE","HARAMBE","GUMMY","SPX","BITCOIN","MOTHER","SIGMA","QUACK",
        "HAMSTER","PORK","ELMO","BODEN","GOAT","FWOG","RETARDIO","GORK",
        "BORK","CHEEMS","BABYSATS","TRUMP","MELANIA","JAILSTOOL","POPO",
        "BIRD","SUNDOG","CATE","KEKIUS","VIRTUAL",
    }'''
NEW_B5 = '''    # Patch 74 — Solana-only memecoin set
    _MEMECOINS = {
        "WIF","BONK","POPCAT","FARTCOIN","GRIFFAIN","GOAT","MOODENG","PNUT",
        "MOTHER","ACT","NEIRO","MEW","BILLY","MOG","BRETT","GIGA","TURBO",
        "PENGU","AURA","CHILLGUY","BOME","WEN","SELFIE",
        # Legacy Solana memes (keep for watchability)
        "PONKE","SLERF","MYRO","ZEREBRO","GRASS",
    }'''
assert OLD_B5 in main_text, "B5 anchor not found"
main_text = main_text.replace(OLD_B5, NEW_B5)
print("B5 OK — _MEMECOINS replaced with Solana-only set")

# B6 — _MEME_SP: replace with Solana-only set ─────────────────────────────────
OLD_B6 = '''                _MEME_SP = {
                    "DOGE","SHIB","PEPE","FLOKI","BONK","WIF","POPCAT","MEW","BRETT","MOG",
                    "NEIRO","TURBO","WOJAK","MEME","APU","GIGA","PNUT","SLERF",
                    "PONKE","BOME","SILLY","MYRO","DEGEN","BABYDOGE","ELON",
                    "MAGA","NOOT","CAT","DOG","RATS","SNEK","MICHI",
                    "MOODENG","CHILLGUY","ACT","ZEREBRO","GRIFFAIN","FARTCOIN",
                    "VINE","HARAMBE","MOTHER","GOAT","FWOG",
                    "TRUMP","MELANIA","POPO","BIRD","CATE","VIRTUAL","GORK",
                }'''
NEW_B6 = '''                # Patch 74 — Solana-only sparkline set
                _MEME_SP = {
                    "WIF","BONK","POPCAT","FARTCOIN","GRIFFAIN","GOAT","MOODENG","PNUT",
                    "MOTHER","ACT","NEIRO","MEW","BILLY","MOG","BRETT","GIGA","TURBO",
                    "PENGU","AURA","CHILLGUY","BOME","WEN","SELFIE",
                    "PONKE","SLERF","MYRO","ZEREBRO","GRASS",
                }'''
assert OLD_B6 in main_text, "B6 anchor not found"
main_text = main_text.replace(OLD_B6, NEW_B6)
print("B6 OK — _MEME_SP replaced with Solana-only set")

# B7 — CryptoPanic default currencies ─────────────────────────────────────────
OLD_B7 = '    currencies: str = "BTC,ETH,SOL,PEPE,DOGE,SHIB,TRUMP,WIF,BONK,FLOKI,MEW,BRETT,POPCAT,PNUT,FARTCOIN",'
NEW_B7 = '    currencies: str = "BTC,ETH,SOL,WIF,BONK,POPCAT,FARTCOIN,PNUT,MOODENG,PENGU,MEW,GOAT,GIGA,CHILLGUY,ACT",'
assert OLD_B7 in main_text, "B7 anchor not found"
main_text = main_text.replace(OLD_B7, NEW_B7)
print("B7 OK — CryptoPanic default currencies updated")

# B8 — CryptoPanic memecoins filter ──────────────────────────────────────────
OLD_B8 = '            currencies = "WIF,BONK,PEPE,DOGE,SHIB,FLOKI,MEW,BRETT,POPCAT,PNUT,FARTCOIN,SOL"'
NEW_B8 = '            currencies = "WIF,BONK,POPCAT,FARTCOIN,PNUT,MOODENG,MEW,GOAT,GIGA,ACT,CHILLGUY,AURA,PENGU,BOME,SOL"'
assert OLD_B8 in main_text, "B8 anchor not found"
main_text = main_text.replace(OLD_B8, NEW_B8)
print("B8 OK — CryptoPanic memecoins filter updated")

MAIN.write_text(main_text)

# Compile check
r = subprocess.run([sys.executable, "-m", "py_compile", str(MAIN)], capture_output=True, text=True)
if r.returncode != 0:
    print("COMPILE ERROR main.py:", r.stderr); sys.exit(1)
print("B OK — main.py compiles clean")

print("\nPatch 74 complete — all 10 steps applied.")
print("Next: update .env WATCHLIST_ENTRIES, rsync dist (no frontend change), restart service.")
