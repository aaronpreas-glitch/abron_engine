"""
Patch 180 — Live Jupiter truth for perp alerts & dashboard.

Changes to utils/tier_manager.py:

1. New helper _fetch_jupiter_live(wallet, pubkey_to_sym):
   - Fetches FULL Jupiter v1 positions (same endpoint as wallet router)
   - Returns {symbol: {liq_price, leverage, collateral_usd, mark_price,
     entry_price, pnl_usd, pubkeys}} keyed by DB symbol
   - Uses pubkey cross-reference for reliable symbol identification
   - Returns {} on any failure (callers fall back to DB data)

2. Section 2.5 (Patch 179 liq buffer alert) updated:
   - Prefers Jupiter live liq_price over DB stop_price
   - Prefers Jupiter live leverage, collateral, mark_price, entry_price
   - Alert message now shows "eff. X.xxx" leverage + "[Jupiter]" or "[DB est.]" source
   - Full DB fallback preserved when Jupiter unavailable

3. tier_status() enriched:
   - Calls _fetch_jupiter_live() once per request
   - Adds liq_price, jup_leverage, jup_collateral, jup_pnl to each position group
   - Safe: missing fields return None (frontend can handle gracefully)

Quantified gap fixed (Mar 8, 2026):
  ETH: DB stop_price ~$1,838 → 5.5% CRITICAL | Jupiter liq $1,714 → 11.9% WARN
  BTC: DB stop_price ~$58,119 → 13.4% WARN   | Jupiter liq $57,033 → 15.1% OK
"""
import pathlib, py_compile, sys

TARGET = pathlib.Path("/root/memecoin_engine/utils/tier_manager.py")
src = TARGET.read_text()

# ── 1. Add _fetch_jupiter_live() after _get_jupiter_position_keys() ───────────

ANCHOR_AFTER_FUNC = "        return None\n\n\n# ── Open a tier position ──────────────────────────────────────────────────────"

if ANCHOR_AFTER_FUNC not in src:
    print("ERROR: anchor for _get_jupiter_position_keys end not found")
    print("Nearby chars around 'return None':")
    idx = src.find("        return None\n\n\n")
    if idx >= 0:
        print(repr(src[idx:idx+120]))
    sys.exit(1)

NEW_FUNC = '''        return None


def _fetch_jupiter_live(wallet: str, pubkey_to_sym: dict) -> dict:
    """Patch 180: Fetch full Jupiter v1 positions and return live data keyed by symbol.

    Uses v1 API (same as wallet router) which reliably returns liquidationPrice,
    leverage, collateralUsd, markPrice, entryPrice, pnlAfterFeesUsd.

    pubkey_to_sym: {jupiter_position_key: symbol} — built from DB tier positions.
    Used to identify which Jupiter position corresponds to which symbol (SOL/BTC/ETH)
    since the Jupiter marketMint → symbol mapping is incomplete for some tokens.

    Returns {symbol: {liq_price, leverage, collateral_usd, mark_price,
                       entry_price, pnl_usd, pubkeys}} or {} on any failure.
    Callers must fall back to DB data when result is {}.
    """
    if not wallet:
        return {}
    try:
        import requests as _req_jl  # avoid name collision
        r = _req_jl.get(
            "https://perps-api.jup.ag/v1/positions",
            params={"walletAddress": wallet},
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        if isinstance(data, list):
            raw_list = data
        else:
            raw_list = data.get("dataList") or data.get("positions") or data.get("data") or []

        def _jf(val, divisor: float = 1.0) -> float:
            try:
                return round(float(val) / divisor, 6)
            except Exception:
                return 0.0

        result: dict = {}
        for p in raw_list:
            # Identify symbol via pubkey cross-reference (100% accurate)
            pk = (
                p.get("positionPubkey")
                or p.get("address")
                or p.get("position_pubkey")
                or p.get("pubkey")
            )
            sym = pubkey_to_sym.get(pk)
            if not sym:
                # Fallback: parse market field (e.g. "SOL-PERP" → "SOL")
                market_raw = str(p.get("marketSymbol") or p.get("market") or "")
                if "-" in market_raw:
                    sym = market_raw.split("-")[0].upper()
            if not sym:
                continue  # can't identify symbol — skip

            liq_price      = _jf(p.get("liquidationPrice"))
            mark_price     = _jf(p.get("markPrice"))
            entry_price    = _jf(p.get("entryPrice"))
            leverage       = _jf(p.get("leverage"))
            collateral_usd = _jf(p.get("collateralUsd"), 1_000_000)  # micro-USDC → USD
            pnl_usd        = _jf(p.get("pnlAfterFeesUsd"))

            if sym in result:
                # Shouldn't happen (1 position per market), but accumulate pubkeys
                result[sym]["pubkeys"].add(pk)
            else:
                result[sym] = {
                    "liq_price":      liq_price,
                    "mark_price":     mark_price,
                    "entry_price":    entry_price,
                    "leverage":       leverage,
                    "collateral_usd": collateral_usd,
                    "pnl_usd":        pnl_usd,
                    "pubkeys":        {pk} if pk else set(),
                }

        log.info("[TIER] Jupiter live (P180): %d symbols — %s", len(result), list(result.keys()))
        return result
    except Exception as _e:
        log.warning("[TIER] _fetch_jupiter_live error: %s", _e)
        return {}


# ── Open a tier position ──────────────────────────────────────────────────────'''

new_src = src.replace(ANCHOR_AFTER_FUNC, NEW_FUNC, 1)
if new_src == src:
    print("ERROR: _fetch_jupiter_live insertion anchor replacement failed")
    sys.exit(1)
print("Step 1: added _fetch_jupiter_live()")

# ── 2. Replace section 2.5 (Patch 179 liq buffer alert) ──────────────────────

OLD_SECTION_25 = """        # ── 2.5. Liq buffer alert (Patch 179) ──────────────────────────────────
        # Two thresholds, separate rate-limit keys per symbol per level:
        #   CRITICAL (<10%)   — 🔴 alert every 4h — act now
        #   WARN     (10-15%) — ⚠️  alert every 6h — heads up
        # Stacked rows: take WORST liq price per symbol (highest stop_price for
        # LONG = closest to current price) so we never under-report risk.
        _liq_price_cache: dict = {}
        _buf_positions: dict = {}  # sym → {liq, price, side, col, lev, tier, entry}
        for _lp in _get_tier_positions(conn):
            _sym  = _lp["symbol"]
            _liq  = _lp.get("stop_price") or 0.0
            if not _liq:
                continue
            if _sym not in _liq_price_cache:
                _liq_price_cache[_sym] = _fetch_price(_sym)
            _price = _liq_price_cache[_sym]
            if not _price:
                continue
            _side  = _lp.get("side", "LONG")
            _col   = _lp.get("collateral_usd") or 0.0
            _lev   = _lp.get("leverage") or 0.0
            _notes = _lp.get("notes") or ""
            _tier  = _notes.replace("TIER:", "").strip().split()[0] if "TIER:" in _notes else "?"
            _entry = _lp.get("entry_price") or 0.0
            # Keep worst-case liq per symbol (highest stop_price for LONG)
            _prev = _buf_positions.get(_sym)
            if _prev is None or (
                _side == "LONG"  and _liq > _prev["liq"]
            ) or (
                _side == "SHORT" and _liq < _prev["liq"]
            ):
                _buf_positions[_sym] = {
                    "liq": _liq, "price": _price, "side": _side,
                    "col": _col, "lev": _lev, "tier": _tier, "entry": _entry,
                }
        try:
            from utils.telegram_alerts import send_telegram_sync, should_rate_limit  # type: ignore
            for _sym, _d in _buf_positions.items():
                _price = _d["price"]
                _liq   = _d["liq"]
                _side  = _d["side"]
                _buf   = (
                    (_price - _liq) / _price * 100 if _side == "LONG"
                    else (_liq - _price) / _price * 100
                )
                if _buf <= 0:
                    continue  # already liquidated — liq detector handles it
                _tier      = _d["tier"]
                _col       = _d["col"]
                _lev       = _d["lev"]
                _entry     = _d["entry"]
                _entry_str = f" | Entry: ${_entry:,.2f}" if _entry else ""
                if _buf < 10.0:
                    # CRITICAL — act now, rate-limited 4h per symbol
                    if not should_rate_limit(f"liq_crit_{_sym}", 14400):
                        send_telegram_sync(
                            f"🔴 {_sym} Liq Buffer CRITICAL",
                            (
                                f"Tier {_tier} ({_lev:.1f}x) {_side} | Col: ${_col:,.0f}\\n"
                                f"Price: ${_price:,.2f}{_entry_str}\\n"
                                f"Liq: ${_liq:,.2f} | Buffer: {_buf:.1f}%\\n"
                                f"→ Add margin or close to avoid liquidation"
                            ),
                            "🔴",
                        )
                        log.warning(
                            "[TIER] Liq CRITICAL: %s tier=%s lev=%.1fx price=$%.2f"
                            " liq=$%.2f buf=%.1f%%",
                            _sym, _tier, _lev, _price, _liq, _buf,
                        )
                elif _buf < 15.0:
                    # WARN — heads up, rate-limited 6h per symbol
                    if not should_rate_limit(f"liq_warn_{_sym}", 21600):
                        send_telegram_sync(
                            f"⚠️ {_sym} Liq Buffer Warning",
                            (
                                f"Tier {_tier} ({_lev:.1f}x) {_side} | Col: ${_col:,.0f}\\n"
                                f"Price: ${_price:,.2f}{_entry_str}\\n"
                                f"Liq: ${_liq:,.2f} | Buffer: {_buf:.1f}%\\n"
                                f"→ Monitor closely — buffer below 15%"
                            ),
                            "⚠️",
                        )
                        log.warning(
                            "[TIER] Liq WARN: %s tier=%s lev=%.1fx price=$%.2f"
                            " liq=$%.2f buf=%.1f%%",
                            _sym, _tier, _lev, _price, _liq, _buf,
                        )
        except Exception:
            pass"""

NEW_SECTION_25 = """        # ── 2.5. Liq buffer alert (Patch 180) ──────────────────────────────────
        # Two thresholds, separate rate-limit keys per symbol per level:
        #   CRITICAL (<10%)   — 🔴 alert every 4h — act now
        #   WARN     (10-15%) — ⚠️  alert every 6h — heads up
        #
        # Patch 180: Prefer Jupiter live data for liq_price, leverage, collateral,
        # and mark_price over stale DB snapshot values. DB fallback preserved when
        # Jupiter API is unavailable. Source annotated in alert message.
        #
        # Stacked rows: when Jupiter live data is available, all DB rows for a symbol
        # will use the same Jupiter liq_price (the actual blended liq). When only DB
        # data is available, take WORST stop_price per symbol as before (Patch 179).
        _p180_db_positions = _get_tier_positions(conn)
        _p180_pubkey_sym   = {
            p["jupiter_position_key"]: p["symbol"]
            for p in _p180_db_positions
            if p.get("jupiter_position_key")
        }
        # One Jupiter call per cycle — feeds liq buffer alerts with live data
        _live_jup: dict = _fetch_jupiter_live(wallet, _p180_pubkey_sym)

        _liq_price_cache: dict = {}
        _buf_positions: dict = {}  # sym → {liq, price, side, col, lev, tier, entry, src}
        for _lp in _p180_db_positions:
            _sym  = _lp["symbol"]
            _live = _live_jup.get(_sym, {})
            # Prefer Jupiter live liq_price; fall back to DB stop_price
            _liq  = _live.get("liq_price") or _lp.get("stop_price") or 0.0
            if not _liq:
                continue
            # Price: prefer Jupiter mark_price (same source, avoids redundant Kraken call)
            if _sym not in _liq_price_cache:
                _liq_price_cache[_sym] = _live.get("mark_price") or _fetch_price(_sym)
            _price = _liq_price_cache[_sym]
            if not _price:
                continue
            _side  = _lp.get("side", "LONG")
            # Prefer Jupiter live leverage and collateral over DB snapshot
            _col   = _live.get("collateral_usd") or _lp.get("collateral_usd") or 0.0
            _lev   = _live.get("leverage")       or _lp.get("leverage")       or 0.0
            _notes = _lp.get("notes") or ""
            _tier  = _notes.replace("TIER:", "").strip().split()[0] if "TIER:" in _notes else "?"
            _entry = _live.get("entry_price")    or _lp.get("entry_price")    or 0.0
            _src   = "Jupiter" if _live else "DB est."
            # Keep worst-case liq per symbol:
            # - With Jupiter: all rows for symbol share the same live liq_price → idempotent
            # - Without Jupiter: take highest stop_price for LONG (Patch 179 behaviour preserved)
            _prev = _buf_positions.get(_sym)
            if _prev is None or (
                _side == "LONG"  and _liq > _prev["liq"]
            ) or (
                _side == "SHORT" and _liq < _prev["liq"]
            ):
                _buf_positions[_sym] = {
                    "liq": _liq, "price": _price, "side": _side,
                    "col": _col, "lev": _lev, "tier": _tier, "entry": _entry, "src": _src,
                }
        try:
            from utils.telegram_alerts import send_telegram_sync, should_rate_limit  # type: ignore
            for _sym, _d in _buf_positions.items():
                _price = _d["price"]
                _liq   = _d["liq"]
                _side  = _d["side"]
                _buf   = (
                    (_price - _liq) / _price * 100 if _side == "LONG"
                    else (_liq - _price) / _price * 100
                )
                if _buf <= 0:
                    continue  # already liquidated — liq detector handles it
                _tier      = _d["tier"]
                _col       = _d["col"]
                _lev       = _d["lev"]
                _entry     = _d["entry"]
                _src       = _d["src"]
                _entry_str = f" | Entry: ${_entry:,.2f}" if _entry else ""
                if _buf < 10.0:
                    # CRITICAL — act now, rate-limited 4h per symbol
                    if not should_rate_limit(f"liq_crit_{_sym}", 14400):
                        send_telegram_sync(
                            f"🔴 {_sym} Liq Buffer CRITICAL",
                            (
                                f"Tier {_tier} (eff. {_lev:.2f}x) {_side} | Col: ${_col:,.0f}\\n"
                                f"Price: ${_price:,.2f}{_entry_str}\\n"
                                f"Liq: ${_liq:,.2f} [{_src}] | Buffer: {_buf:.1f}%\\n"
                                f"→ Add margin or close to avoid liquidation"
                            ),
                            "🔴",
                        )
                        log.warning(
                            "[TIER] Liq CRITICAL: %s tier=%s lev=%.2fx price=$%.2f"
                            " liq=$%.2f buf=%.1f%% src=%s",
                            _sym, _tier, _lev, _price, _liq, _buf, _src,
                        )
                elif _buf < 15.0:
                    # WARN — heads up, rate-limited 6h per symbol
                    if not should_rate_limit(f"liq_warn_{_sym}", 21600):
                        send_telegram_sync(
                            f"⚠️ {_sym} Liq Buffer Warning",
                            (
                                f"Tier {_tier} (eff. {_lev:.2f}x) {_side} | Col: ${_col:,.0f}\\n"
                                f"Price: ${_price:,.2f}{_entry_str}\\n"
                                f"Liq: ${_liq:,.2f} [{_src}] | Buffer: {_buf:.1f}%\\n"
                                f"→ Monitor closely — buffer below 15%"
                            ),
                            "⚠️",
                        )
                        log.warning(
                            "[TIER] Liq WARN: %s tier=%s lev=%.2fx price=$%.2f"
                            " liq=$%.2f buf=%.1f%% src=%s",
                            _sym, _tier, _lev, _price, _liq, _buf, _src,
                        )
        except Exception:
            pass"""

if OLD_SECTION_25 not in new_src:
    print("ERROR: section 2.5 anchor not found in modified source")
    print("Searching for partial anchor...")
    if "# ── 2.5. Liq buffer alert (Patch 179)" in new_src:
        print("  Found P179 comment — the full block text may differ")
    sys.exit(1)

new_src = new_src.replace(OLD_SECTION_25, NEW_SECTION_25, 1)
print("Step 2: updated section 2.5 liq buffer alert")

# ── 3. Enrich tier_status() with Jupiter live fields ─────────────────────────

# Anchor: the start of the tier_status prices block
OLD_TIER_STATUS_PRICES = """        # Prices
        symbols = {p["symbol"] for p in positions}
        prices  = {sym: _fetch_price(sym) for sym in symbols}"""

NEW_TIER_STATUS_PRICES = """        # Prices
        symbols = {p["symbol"] for p in positions}
        prices  = {sym: _fetch_price(sym) for sym in symbols}

        # Patch 180: fetch Jupiter live data once for liq_price + live fields
        try:
            from utils.jupiter_perps_trade import get_wallet_address as _gwa  # local import
            _ts_wallet        = _gwa()
            _ts_pubkey_to_sym = {
                p["jupiter_position_key"]: p["symbol"]
                for p in positions
                if p.get("jupiter_position_key")
            }
            _ts_live_jup = _fetch_jupiter_live(_ts_wallet, _ts_pubkey_to_sym)
        except Exception:
            _ts_live_jup = {}"""

if OLD_TIER_STATUS_PRICES not in new_src:
    print("ERROR: tier_status prices anchor not found")
    sys.exit(1)

new_src = new_src.replace(OLD_TIER_STATUS_PRICES, NEW_TIER_STATUS_PRICES, 1)
print("Step 3a: injected Jupiter live fetch into tier_status()")

# Add live fields to enriched.append — anchor the existing last field "stacked_count"
OLD_ENRICHED_APPEND = """                enriched.append({
                    "id":            first["id"],
                    "symbol":        sym,
                    "side":          side,
                    "entry":         entry,
                    "price":         price,
                    "raw_pnl":       round(raw_pnl, 4),
                    "lev_pnl":       round(lev_pnl, 4),
                    "pnl_usd":       round(pnl_usd, 2),
                    "collateral":    total_col,
                    "jup_key":       latest.get("jupiter_position_key", ""),
                    "opened":        first.get("opened_ts_utc", ""),
                    "stacked_count": len(rows_sorted),
                })"""

NEW_ENRICHED_APPEND = """                # Patch 180: inject Jupiter live fields (None-safe; frontend handles gracefully)
                _jlive = _ts_live_jup.get(sym, {})
                enriched.append({
                    "id":            first["id"],
                    "symbol":        sym,
                    "side":          side,
                    "entry":         entry,
                    "price":         price,
                    "raw_pnl":       round(raw_pnl, 4),
                    "lev_pnl":       round(lev_pnl, 4),
                    "pnl_usd":       round(pnl_usd, 2),
                    "collateral":    total_col,
                    "jup_key":       latest.get("jupiter_position_key", ""),
                    "opened":        first.get("opened_ts_utc", ""),
                    "stacked_count": len(rows_sorted),
                    # Live Jupiter truth — prefer over DB calc for accurate display
                    "liq_price":      _jlive.get("liq_price"),       # actual blended liq
                    "jup_leverage":   _jlive.get("leverage"),         # effective blended leverage
                    "jup_collateral": _jlive.get("collateral_usd"),   # live collateral (inc. PnL)
                    "jup_pnl":        _jlive.get("pnl_usd"),          # Jupiter PnL after fees
                    "jup_mark_price": _jlive.get("mark_price"),       # Jupiter mark price
                })"""

if OLD_ENRICHED_APPEND not in new_src:
    print("ERROR: enriched.append anchor not found")
    sys.exit(1)

new_src = new_src.replace(OLD_ENRICHED_APPEND, NEW_ENRICHED_APPEND, 1)
print("Step 3b: injected live fields into enriched.append()")

# ── Write + verify ────────────────────────────────────────────────────────────
TARGET.write_text(new_src)
print(f"Wrote {TARGET}")

try:
    py_compile.compile(str(TARGET), doraise=True)
    print("py_compile: OK")
except py_compile.PyCompileError as e:
    print(f"SYNTAX ERROR: {e}")
    TARGET.write_text(src)
    print("Restored original — patch NOT applied")
    sys.exit(1)

print("Patch 180 applied successfully.")
