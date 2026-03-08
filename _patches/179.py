"""
Patch 179 — Perp liquidation buffer alert improvement.

Replaces the coarse Patch 142 liq proximity block in tier_manager.py with:
  - Two thresholds: CRITICAL (<10%) and WARN (10-15%)
  - Separate rate-limit keys per symbol per level (4h CRITICAL, 6h WARN)
  - Worst-case liq price per symbol (highest stop_price for LONG = closest to liq)
  - Operator-useful message: tier label, leverage, collateral, entry, recommended action
"""
import pathlib, py_compile, sys

TARGET = pathlib.Path("/root/memecoin_engine/utils/tier_manager.py")
src = TARGET.read_text()

# ── Anchor: the comment that starts the section ──────────────────────────────
ANCHOR = "        # ── 2.5. Liq proximity alert (Patch 142) ──────────────────────────────"
END    = "\n        # ── 3. Profit buffer → new positions ─────────────────────────────────"

start = src.find(ANCHOR)
end   = src.find(END)

if start == -1:
    print("ERROR: start anchor not found"); sys.exit(1)
if end == -1:
    print("ERROR: end anchor not found"); sys.exit(1)

OLD_BLOCK = src[start:end]
print(f"Found section at lines covering chars {start}–{end} ({len(OLD_BLOCK)} chars)")

NEW_BLOCK = '''        # ── 2.5. Liq buffer alert (Patch 179) ──────────────────────────────────
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
            pass'''

new_src = src[:start] + NEW_BLOCK + src[end:]
TARGET.write_text(new_src)
print("Wrote tier_manager.py")

# Syntax check
try:
    py_compile.compile(str(TARGET), doraise=True)
    print("py_compile: OK")
except py_compile.PyCompileError as e:
    print(f"SYNTAX ERROR: {e}")
    TARGET.write_text(src)
    print("Restored original — patch NOT applied")
    sys.exit(1)

print("Patch 179 applied successfully.")
