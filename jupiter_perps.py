"""
Jupiter Perps SOL Leverage Assistant
Read-only position tracker for Jupiter perpetuals on Solana.
Fetches position data via Jupiter Perps API and on-chain RPC.
"""

import json
import logging
import math
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

# ── Constants ────────────────────────────────────────────────────────────────

WALLET_ADDRESS = "6YeATB75AyJKM8ujv3qQXtzCKrACmQgzpgf4EmjihhF4"
SOLANA_RPC = "https://api.mainnet-beta.solana.com"

# Jupiter Perps API
JUPITER_PERPS_API = "https://perps-api.jup.ag/v1"

# DCA tracker storage
_DCA_FILE = Path(__file__).resolve().parent / "data_storage" / "sol_dca.json"

# Alert thresholds
LIQ_WARN_PCT = 20.0          # warn if < 20% away from liquidation (upgraded from 15%)
PRICE_TARGETS = [100.0, 120.0, 160.0]
FUNDING_WARN_PCT = 0.05      # warn if funding rate > 0.05%
FUNDING_HIGH_PCT = 0.06      # show cost impact above this
MONTHLY_ADD_USD = 250.0
MONTHLY_LEVERAGE = 3.0
CHECK_INTERVAL_SECONDS = 60
MONTHLY_REMINDER_DAYS = 30

# Risk zone thresholds (liq distance %)
SAFE_LIQ_DISTANCE = 40.0       # > 40% = safe
NEUTRAL_LIQ_DISTANCE = 25.0    # 25-40% = neutral
WARN_LIQ_DISTANCE = 15.0       # 15-25% = aggressive
DANGER_LIQ_DISTANCE = 15.0     # < 15% = blocked

# Leverage options
SAFE_LEVERAGE = 2.0
NEUTRAL_LEVERAGE = 3.0
AGGRESSIVE_LEVERAGE = 4.5
MAX_LEVERAGE = 5.0

# Volatility thresholds
VOL_WARNING_THRESHOLD = 12.0   # % — warn if above
VOL_HIGH_THRESHOLD = 10.0      # % — suggest lower leverage

# Price zones to analyse in /pricezones
PRICE_ZONE_LEVELS = [100.0, 75.0, 60.0]

# ── Separator ─────────────────────────────────────────────────────────────────

SEP = "━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# ── State ─────────────────────────────────────────────────────────────────────

_state = {
    "last_monthly_reminder_ts": 0.0,
    "targets_hit": set(),          # price targets already alerted
    "last_liq_alert_ts": 0.0,
    "last_funding_alert_ts": 0.0,
    "last_dca_zone_alert_ts": 0.0, # DCA zone drop alert cooldown
    "dca_zone_alerted": set(),     # which DCA zones already pinged
}


# ── DCA Tracker ───────────────────────────────────────────────────────────────

def _load_dca_entries() -> list:
    """Load DCA entries from JSON file."""
    try:
        _DCA_FILE.parent.mkdir(parents=True, exist_ok=True)
        if _DCA_FILE.exists():
            with open(_DCA_FILE) as f:
                data = json.load(f)
                return data if isinstance(data, list) else []
    except Exception as e:
        logging.warning("dca load error: %s", e)
    return []


def _save_dca_entries(entries: list):
    """Save DCA entries to JSON file."""
    try:
        _DCA_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(_DCA_FILE, "w") as f:
            json.dump(entries, f, indent=2)
    except Exception as e:
        logging.warning("dca save error: %s", e)


def add_dca_entry(amount_usd: float, sol_price: float, leverage: float = 1.0, note: str = "") -> dict:
    """
    Log a new DCA entry.
    amount_usd: USD collateral added
    sol_price: SOL price at time of add
    leverage: leverage used (default 1x = spot)
    """
    entries = _load_dca_entries()
    entry = {
        "ts": time.time(),
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "amount_usd": round(float(amount_usd), 2),
        "sol_price": round(float(sol_price), 4),
        "leverage": round(float(leverage), 2),
        "size_usd": round(float(amount_usd) * float(leverage), 2),
        "sol_amount": round(float(amount_usd) * float(leverage) / float(sol_price), 4),
        "note": str(note or ""),
    }
    entries.append(entry)
    _save_dca_entries(entries)
    return entry


def get_dca_entries() -> list:
    """Return all DCA entries sorted oldest-first."""
    return sorted(_load_dca_entries(), key=lambda e: e.get("ts", 0))


def calc_dca_summary(sol_price: float) -> dict:
    """
    Calculate DCA summary stats from all logged entries.
    Returns avg cost, total invested, total SOL, PnL, breakeven.
    """
    entries = get_dca_entries()
    if not entries:
        return {"entries": [], "count": 0}

    total_usd_invested = sum(e.get("amount_usd", 0) for e in entries)
    total_size_usd = sum(e.get("size_usd", 0) for e in entries)
    total_sol = sum(e.get("sol_amount", 0) for e in entries)

    # Weighted average cost = total size USD / total SOL
    avg_cost = total_size_usd / total_sol if total_sol > 0 else 0

    # Current value of SOL holdings
    current_value = total_sol * sol_price

    # PnL relative to size (leveraged exposure)
    pnl = current_value - total_size_usd

    # Breakeven = avg cost (price where PnL = 0)
    breakeven = avg_cost

    # Next DCA zones: -10%, -20%, -30% from avg cost
    dca_zones = [round(avg_cost * (1 - pct / 100), 2) for pct in [10, 20, 30]]

    return {
        "entries": entries,
        "count": len(entries),
        "total_usd_invested": total_usd_invested,
        "total_size_usd": total_size_usd,
        "total_sol": total_sol,
        "avg_cost": avg_cost,
        "current_value": current_value,
        "pnl": pnl,
        "pnl_pct": (pnl / total_size_usd * 100) if total_size_usd > 0 else 0,
        "breakeven": breakeven,
        "sol_price": sol_price,
        "dca_zones": dca_zones,
    }


def format_dca_dashboard(sol_price: float, added_entry: dict = None) -> str:
    """Format /dca summary card."""
    summary = calc_dca_summary(sol_price)

    if summary["count"] == 0:
        return "\n".join([
            f"<b>💰 [DCA]: SOL DCA TRACKER</b>",
            f"<code>{SEP}</code>",
            f"<code>No DCA entries yet.</code>",
            f"<code>{SEP}</code>",
            f"<code>Use: /dca &lt;amount&gt; [leverage]</code>",
            f"<code>Example: /dca 250 3</code>",
            f"<code>         /dca 500 1  (spot)</code>",
        ])

    pnl = summary["pnl"]
    pnl_pct = summary["pnl_pct"]
    pnl_emoji = "🟢" if pnl >= 0 else "🔴"
    avg = summary["avg_cost"]
    price_vs_avg_pct = (sol_price - avg) / avg * 100 if avg > 0 else 0
    above_below = "above" if price_vs_avg_pct >= 0 else "below"

    lines = [
        f"<b>💰 [DCA]: SOL DCA TRACKER</b>",
        f"<code>{SEP}</code>",
        f"<code>📊 Entries       : {summary['count']}</code>",
        f"<code>💵 Total invested : {_fv(summary['total_usd_invested'])}</code>",
        f"<code>📐 Avg cost       : {_fp(avg)}</code>",
        f"<code>💰 SOL price      : {_fp(sol_price)}</code>",
        f"<code>🪙 SOL held       : {summary['total_sol']:.4f} SOL</code>",
        f"<code>{SEP}</code>",
        f"<code>📈 Current value  : {_fv(summary['current_value'])}</code>",
        f"<code>{pnl_emoji} PnL            : {_fv(pnl)} ({pnl_pct:+.1f}%)</code>",
        f"<code>🎯 Breakeven      : {_fp(avg)} ({price_vs_avg_pct:+.1f}% {above_below})</code>",
        f"<code>{SEP}</code>",
        f"<b>📍 NEXT DCA ZONES</b>",
        f"<code>{SEP}</code>",
    ]

    for i, zone in enumerate(summary["dca_zones"]):
        pct_down = [10, 20, 30][i]
        gap = sol_price - zone
        lines.append(f"<code>-{pct_down}%  → {_fp(zone)}  (${gap:.2f} away)</code>")

    # Recent entries (last 5)
    if summary["entries"]:
        lines += [f"<code>{SEP}</code>", f"<b>📋 RECENT ENTRIES</b>", f"<code>{SEP}</code>"]
        for e in reversed(summary["entries"][-5:]):
            lev_str = f"{e.get('leverage', 1):.1f}x" if e.get("leverage", 1) != 1 else "spot"
            lines.append(
                f"<code>{e.get('date','?')}  {_fp(e.get('sol_price'))}  "
                f"{_fv(e.get('amount_usd'))} @ {lev_str}</code>"
            )

    if added_entry:
        lines += [
            f"<code>{SEP}</code>",
            f"<code>✅ Entry logged: {_fv(added_entry.get('amount_usd'))} @ {_fp(added_entry.get('sol_price'))}</code>",
        ]

    return "\n".join(lines)


# ── Fetch position from Jupiter Perps API ────────────────────────────────────

def fetch_jupiter_position():
    """
    Fetch open SOL-LONG position for wallet from Jupiter Perps API.
    Returns normalized dict or None.
    """
    try:
        url = f"{JUPITER_PERPS_API}/positions"
        params = {"walletAddress": WALLET_ADDRESS}
        r = requests.get(url, params=params, timeout=10)
        if r.status_code != 200:
            return None
        data = r.json() or {}
        positions = data.get("dataList") or data.get("positions") or []
        # Find open SOL long
        for pos in positions:
            market = str(pos.get("market") or pos.get("marketSymbol") or "").upper()
            side = str(pos.get("side") or "").upper()
            if "SOL" in market and side in ("LONG", "BUY"):
                return _normalize_position(pos)
        # If no match, return first open position
        if positions:
            return _normalize_position(positions[0])
        return None
    except Exception as e:
        logging.warning("jupiter_perps: fetch error: %s", e)
        return None


def _normalize_position(pos):
    """
    Normalize raw Jupiter Perps position dict to standard fields.
    Jupiter API returns all numeric values as strings.
    Actual fields: entryPrice, markPrice, liquidationPrice, leverage,
    size (SOL), collateralUsd, pnlAfterFeesUsd, borrowFeesUsd, side, createdTime
    """
    def _f(key, fallback=0.0):
        v = pos.get(key)
        try:
            return float(v) if v is not None else fallback
        except (TypeError, ValueError):
            return fallback

    entry_price  = _f("entryPrice")
    mark_price   = _f("markPrice")
    liq_price    = _f("liquidationPrice")
    leverage     = _f("leverage")

    # sizeUsdDelta is the USD position size (in micro-units, divide by 1e6)
    size_usd_raw = _f("sizeUsdDelta")
    size_usd = size_usd_raw / 1_000_000 if size_usd_raw > 1000 else _f("size")

    # collateralUsd is in micro-units too
    collateral_raw = _f("collateralUsd")
    collateral = collateral_raw / 1_000_000 if collateral_raw > 1000 else _f("collateral")

    # PnL: use pnlAfterFeesUsd (micro-units) or pnlAfterFees
    pnl_raw = _f("pnlAfterFeesUsd")
    pnl = pnl_raw if abs(pnl_raw) < 1_000_000 else pnl_raw / 1_000_000

    # Funding rate: derive from borrow fees / position age in days
    borrow_fees_usd = _f("borrowFeesUsd")
    created_time = _f("createdTime")
    funding_rate = 0.0
    if borrow_fees_usd and created_time and size_usd > 0:
        import time as _time
        days_open = max(1, (_time.time() - created_time) / 86400)
        # Daily rate as % of position size
        funding_rate = (borrow_fees_usd / days_open) / size_usd * 100

    # Fallback leverage calc
    if leverage == 0 and collateral > 0 and size_usd > 0:
        leverage = size_usd / collateral

    side = str(pos.get("side") or "long").upper()

    return {
        "entry_price":  entry_price,
        "mark_price":   mark_price,
        "size_usd":     size_usd,
        "collateral":   collateral,
        "liq_price":    liq_price,
        "pnl":          pnl,
        "funding_rate": funding_rate,
        "leverage":     leverage,
        "market":       "SOL-PERP",
        "side":         side,
        "borrow_fees_usd": borrow_fees_usd,
        "raw":          pos,
    }


# ── Fallback: fetch SOL price from DexScreener ───────────────────────────────

def fetch_sol_price():
    """Fetch current SOL price from DexScreener as fallback mark price."""
    try:
        r = requests.get(
            "https://api.dexscreener.com/latest/dex/search",
            params={"q": "SOL"},
            timeout=10,
        )
        data = r.json() or {}
        pairs = data.get("pairs") or []
        sol_stable = [
            p for p in pairs
            if p.get("chainId") == "solana"
            and str(p.get("baseToken", {}).get("symbol") or "").upper() == "SOL"
            and str(p.get("quoteToken", {}).get("symbol") or "").upper() in ("USDC", "USDT")
        ]
        if sol_stable:
            best = sorted(sol_stable, key=lambda p: float((p.get("liquidity") or {}).get("usd") or 0), reverse=True)[0]
            return float(best.get("priceUsd") or 0)
    except Exception:
        pass
    return None


# ── Calculations ──────────────────────────────────────────────────────────────

def calc_liq_distance_pct(mark_price, liq_price):
    """Returns % distance from mark to liquidation (positive = safe)."""
    if not mark_price or not liq_price or liq_price <= 0:
        return None
    return abs(mark_price - liq_price) / mark_price * 100


def calc_what_if(position, target_price):
    """Calculate PnL and new position state at a target price."""
    if not position:
        return None
    entry = position["entry_price"]
    size = position["size_usd"]
    collateral = position["collateral"]
    liq = position["liq_price"]
    if not entry or entry <= 0 or not size or not collateral:
        return None
    price_change_pct = (target_price - entry) / entry * 100
    pnl = size * (price_change_pct / 100)
    new_collateral = collateral + pnl
    liq_distance = calc_liq_distance_pct(target_price, liq)
    return {
        "target_price": target_price,
        "price_change_pct": price_change_pct,
        "pnl": pnl,
        "new_collateral": new_collateral,
        "liq_distance_pct": liq_distance,
    }


def calc_monthly_add_impact(position, add_usd=MONTHLY_ADD_USD, leverage=MONTHLY_LEVERAGE):
    """Calculate what adding $250 at 3x leverage does to position."""
    if not position:
        return None
    new_size = add_usd * leverage
    current_size = position["size_usd"]
    current_collateral = position["collateral"]
    new_total_size = current_size + new_size
    new_total_collateral = current_collateral + add_usd
    new_leverage = new_total_size / new_total_collateral if new_total_collateral > 0 else 0
    return {
        "add_usd": add_usd,
        "add_leverage": leverage,
        "new_size_added": new_size,
        "new_total_size": new_total_size,
        "new_total_collateral": new_total_collateral,
        "new_leverage": new_leverage,
    }


# ── SOL Volatility (30d) ──────────────────────────────────────────────────────

_vol_cache = {"value": None, "ts": 0.0}
_VOL_CACHE_TTL = 3600  # refresh hourly


def fetch_sol_volatility_30d():
    """
    Estimate 30-day SOL volatility using CoinGecko daily OHLC.
    Returns annualised daily vol % (e.g. 8.3 means 8.3%).
    Caches result for 1 hour.
    """
    now = time.time()
    if _vol_cache["value"] is not None and now - _vol_cache["ts"] < _VOL_CACHE_TTL:
        return _vol_cache["value"]
    try:
        # CoinGecko free endpoint — no key needed
        url = "https://api.coingecko.com/api/v3/coins/solana/market_chart"
        params = {"vs_currency": "usd", "days": "30", "interval": "daily"}
        r = requests.get(url, params=params, timeout=12)
        if r.status_code != 200:
            return None
        data = r.json() or {}
        prices = [p[1] for p in (data.get("prices") or []) if p and len(p) > 1]
        if len(prices) < 5:
            return None
        # Daily log returns
        returns = [math.log(prices[i] / prices[i - 1]) for i in range(1, len(prices))]
        mean = sum(returns) / len(returns)
        variance = sum((r - mean) ** 2 for r in returns) / len(returns)
        daily_vol = math.sqrt(variance) * 100  # as %
        _vol_cache["value"] = daily_vol
        _vol_cache["ts"] = now
        return daily_vol
    except Exception as e:
        logging.warning("sol_volatility fetch error: %s", e)
        return None


# ── Liquidation price estimation ──────────────────────────────────────────────

def estimate_liq_price(entry_price, collateral, size_usd, side="LONG", maintenance_margin=0.01):
    """
    Estimate liquidation price for a leveraged position.
    Uses simplified formula: liq = entry * (1 - (collateral/size) + maintenance_margin)
    For LONG: liq = entry * (1 - (collateral - maintenance*size) / size)
    """
    if not entry_price or not collateral or not size_usd or size_usd <= 0:
        return None
    try:
        # collateral ratio
        col_ratio = collateral / size_usd
        if side == "LONG":
            liq = entry_price * (1 - col_ratio + maintenance_margin)
        else:
            liq = entry_price * (1 + col_ratio - maintenance_margin)
        return max(0.0, liq)
    except Exception:
        return None


# ── Leverage Recommendation Engine ───────────────────────────────────────────

def calc_leverage_recommendation(position, sol_price, add_usd=MONTHLY_ADD_USD, vol=None, funding=None):
    """
    For a given $add_usd, calculate SAFE / NEUTRAL / AGGRESSIVE leverage options.
    Returns dict with recommendations and context.
    """
    if not sol_price or sol_price <= 0:
        return None

    current_size = float((position or {}).get("size_usd") or 0)
    current_collateral = float((position or {}).get("collateral") or 0)
    current_liq = float((position or {}).get("liq_price") or 0)
    entry_price = float((position or {}).get("entry_price") or sol_price)
    funding = funding or float((position or {}).get("funding_rate") or 0)

    vol = vol  # may be None

    # Volatility adjustment: if vol > 10%, nudge leverage down by 0.5x
    vol_adjustment = 0.0
    if vol and vol > VOL_HIGH_THRESHOLD:
        vol_adjustment = -0.5

    leverage_options = [
        ("safe",       "🟢 SAFE ZONE",       SAFE_LEVERAGE       + vol_adjustment, "Sleep well at night",         "Very low, slow compounding"),
        ("neutral",    "🟡 NEUTRAL ZONE",     NEUTRAL_LEVERAGE    + vol_adjustment, "Standard DCA strategy",       "Moderate, balanced returns"),
        ("aggressive", "🔴 AGGRESSIVE ZONE",  AGGRESSIVE_LEVERAGE + vol_adjustment, "Only if actively monitoring", "Higher, faster liquidation"),
    ]

    # Cap each at MAX_LEVERAGE and floor at 1.0
    leverage_options = [
        (k, label, max(1.0, min(MAX_LEVERAGE, lev)), desc, risk)
        for k, label, lev, desc, risk in leverage_options
    ]

    results = []
    for key, label, lev, best_for, risk in leverage_options:
        new_size_added = add_usd * lev
        new_total_size = current_size + new_size_added
        new_total_collateral = current_collateral + add_usd
        new_effective_leverage = new_total_size / new_total_collateral if new_total_collateral > 0 else lev

        # Estimate new liq price for the combined position
        new_liq = estimate_liq_price(entry_price, new_total_collateral, new_total_size)
        liq_dist = calc_liq_distance_pct(sol_price, new_liq)

        # Funding cost estimate (daily, for display)
        daily_funding_cost = new_total_size * abs(funding) / 100 if funding else 0

        # Blocked if would be < danger threshold
        blocked = liq_dist is not None and liq_dist < DANGER_LIQ_DISTANCE

        # Zone classification
        if liq_dist is None:
            zone = "unknown"
        elif liq_dist >= SAFE_LIQ_DISTANCE:
            zone = "safe"
        elif liq_dist >= NEUTRAL_LIQ_DISTANCE:
            zone = "neutral"
        elif liq_dist >= WARN_LIQ_DISTANCE:
            zone = "aggressive"
        else:
            zone = "danger"

        results.append({
            "key": key,
            "label": label,
            "leverage": lev,
            "effective_leverage": new_effective_leverage,
            "new_size_added": new_size_added,
            "new_total_size": new_total_size,
            "new_total_collateral": new_total_collateral,
            "new_liq_price": new_liq,
            "liq_distance_pct": liq_dist,
            "zone": zone,
            "blocked": blocked,
            "daily_funding_cost": daily_funding_cost,
            "best_for": best_for,
            "risk": risk,
        })

    # Worst-case: SOL drops 20%
    worst_case_price = sol_price * 0.80
    worst_liq_dist = calc_liq_distance_pct(worst_case_price, current_liq) if current_liq else None

    # Recommended = first non-blocked option matching risk tolerance
    recommended = next((r for r in results if not r["blocked"]), None)

    return {
        "results": results,
        "recommended": recommended,
        "add_usd": add_usd,
        "sol_price": sol_price,
        "vol": vol,
        "funding": funding,
        "vol_adjustment": vol_adjustment,
        "worst_case_price": worst_case_price,
        "worst_case_liq_dist": worst_liq_dist,
        "current_position": position,
    }


def calc_price_zones(position, sol_price, zone_prices=None):
    """
    For each price level, show PnL, liq distance, and action.
    Also shows liq price at safe/neutral/aggressive leverage.
    """
    if not position:
        return None
    zone_prices = zone_prices or PRICE_ZONE_LEVELS
    entry = float(position.get("entry_price") or sol_price)
    size = float(position.get("size_usd") or 0)
    collateral = float(position.get("collateral") or 0)
    liq = float(position.get("liq_price") or 0)

    zones = []
    for price in sorted(zone_prices, reverse=True):
        price_chg_pct = (price - entry) / entry * 100 if entry > 0 else 0
        pnl = size * (price_chg_pct / 100)
        liq_dist = calc_liq_distance_pct(price, liq)

        if liq_dist is None:
            status = "❓ UNKNOWN"
            action = "Check position data"
        elif price <= liq:
            status = "❌ LIQUIDATED"
            action = "Position closed"
        elif liq_dist < WARN_LIQ_DISTANCE:
            status = "🔴 WARNING"
            action = "Top-up collateral NOW or close"
        elif liq_dist < NEUTRAL_LIQ_DISTANCE:
            status = "🟡 CAUTION"
            action = "Consider adding collateral"
        else:
            status = "✅ SAFE"
            action = "Hold or trim"

        zones.append({
            "price": price,
            "pnl": pnl,
            "liq_dist": liq_dist,
            "status": status,
            "action": action,
        })

    # Safe price floors at different leverages (for current add amount)
    def floor_at_lev(lev):
        sz = size + MONTHLY_ADD_USD * lev
        col = collateral + MONTHLY_ADD_USD
        return estimate_liq_price(entry, col, sz)

    floors = {
        f"{SAFE_LEVERAGE:.1f}x": floor_at_lev(SAFE_LEVERAGE),
        f"{NEUTRAL_LEVERAGE:.1f}x": floor_at_lev(NEUTRAL_LEVERAGE),
        f"{AGGRESSIVE_LEVERAGE:.1f}x": floor_at_lev(AGGRESSIVE_LEVERAGE),
    }

    return {
        "zones": zones,
        "floors": floors,
        "current_liq": liq,
        "entry": entry,
        "leverage": float(position.get("leverage") or 0),
        "sol_price": sol_price,
    }


# ── Formatters ────────────────────────────────────────────────────────────────

def _fp(v, decimals=2):
    """Format price."""
    if v is None:
        return "N/A"
    try:
        return f"${float(v):,.{decimals}f}"
    except (TypeError, ValueError):
        return "N/A"


def _fv(v, decimals=2):
    """Format USD value compact."""
    if v is None:
        return "N/A"
    try:
        v = float(v)
        if abs(v) >= 1_000_000:
            return f"${v/1_000_000:.2f}M"
        if abs(v) >= 1_000:
            return f"${v/1_000:.1f}K"
        return f"${v:,.{decimals}f}"
    except (TypeError, ValueError):
        return "N/A"


def _fpct(v, decimals=2):
    """Format percentage."""
    if v is None:
        return "N/A"
    try:
        return f"{float(v):+.{decimals}f}%"
    except (TypeError, ValueError):
        return "N/A"


def format_lev_dashboard(position, sol_price=None):
    """Format /lev dashboard message."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    if not position:
        # No position found — show SOL price only
        price_line = _fp(sol_price) if sol_price else "N/A"
        return "\n".join([
            f"<b>📊 [LEV]: SOL POSITION</b>",
            f"<code>{SEP}</code>",
            f"<code>❌ NO OPEN POSITION FOUND</code>",
            f"<code>💰 SOL PRICE: {price_line}</code>",
            f"<code>🕐 CHECKED: {now}</code>",
            f"<code>{SEP}</code>",
            f"<code>💡 Open a position on Jupiter Perps</code>",
            f"<code>   to start tracking.</code>",
        ])

    mark = position["mark_price"] or sol_price
    entry = position["entry_price"]
    size = position["size_usd"]
    collateral = position["collateral"]
    liq = position["liq_price"]
    pnl = position["pnl"]
    funding = position["funding_rate"]
    leverage = position["leverage"]
    market = position["market"]
    side = position["side"]

    liq_dist = calc_liq_distance_pct(mark, liq)
    liq_dist_str = f"{liq_dist:.1f}%" if liq_dist is not None else "N/A"
    liq_emoji = "🔴" if (liq_dist is not None and liq_dist < LIQ_WARN_PCT) else "🟢"

    pnl_emoji = "🟢" if pnl >= 0 else "🔴"
    side_emoji = "📈" if side == "LONG" else "📉"

    funding_str = f"{funding:.4f}%" if funding else "N/A"
    funding_emoji = "⚠️" if (funding and abs(funding) > FUNDING_WARN_PCT) else "💤"

    lev_str = f"{leverage:.1f}x" if leverage else "N/A"

    lines = [
        f"<b>📊 [LEV]: SOL POSITION</b>",
        f"<code>{SEP}</code>",
        f"<code>{side_emoji} MARKET: {market} {side}</code>",
        f"<code>💰 MARK:   {_fp(mark)}</code>",
        f"<code>🎯 ENTRY:  {_fp(entry)}</code>",
        f"<code>📐 LEVERAGE: {lev_str}</code>",
        f"<code>{SEP}</code>",
        f"<code>💼 SIZE:      {_fv(size)}</code>",
        f"<code>🏦 COLLATERAL:{_fv(collateral)}</code>",
        f"<code>{pnl_emoji} PnL:       {_fv(pnl)}</code>",
        f"<code>{SEP}</code>",
        f"<code>{liq_emoji} LIQ PRICE: {_fp(liq)}</code>",
        f"<code>📏 LIQ DIST:  {liq_dist_str}</code>",
        f"<code>{funding_emoji} FUNDING:   {funding_str}</code>",
        f"<code>{SEP}</code>",
        f"<code>🕐 {now}</code>",
    ]

    # Feature 5: Liquidation Zone Predictor
    try:
        from elite_features import format_liq_zones_block
        liq_block = format_liq_zones_block(
            sol_price=float(mark or 0),
            funding_rate=funding,
            leverage=leverage,
            liq_price=float(liq or 0),
        )
        if liq_block:
            lines.append(liq_block)
    except Exception:
        pass

    return "\n".join(lines)


def format_lev_status(position, sol_price=None):
    """Format /lev-status quick one-liner."""
    mark = (position or {}).get("mark_price") or sol_price
    pnl = (position or {}).get("pnl", 0)
    liq = (position or {}).get("liq_price")
    liq_dist = calc_liq_distance_pct(mark, liq)

    if not position:
        return f"<b>📊 LEV STATUS</b>\n<code>❌ No open position | SOL {_fp(sol_price)}</code>"

    pnl_emoji = "🟢" if pnl >= 0 else "🔴"
    liq_emoji = "🔴" if (liq_dist is not None and liq_dist < LIQ_WARN_PCT) else "🟢"
    liq_str = f"{liq_dist:.1f}% to liq" if liq_dist is not None else "liq N/A"

    return "\n".join([
        f"<b>📊 [LEV]: QUICK STATUS</b>",
        f"<code>{SEP}</code>",
        f"<code>💰 SOL: {_fp(mark)}  |  {pnl_emoji} PnL: {_fv(pnl)}</code>",
        f"<code>{liq_emoji} {liq_str}  |  Entry: {_fp(position['entry_price'])}</code>",
    ])


def format_what_if(position, targets=None):
    """Format /what-if calculator for multiple price targets."""
    if not position:
        return "<b>📊 WHAT-IF</b>\n<code>❌ No open position to calculate.</code>"

    targets = targets or PRICE_TARGETS
    lines = [
        f"<b>🔮 [LEV]: WHAT-IF CALCULATOR</b>",
        f"<code>{SEP}</code>",
        f"<code>🎯 ENTRY: {_fp(position['entry_price'])}</code>",
        f"<code>💼 SIZE:  {_fv(position['size_usd'])}</code>",
        f"<code>{SEP}</code>",
    ]

    for t in sorted(targets):
        w = calc_what_if(position, t)
        if not w:
            continue
        pnl_emoji = "🟢" if w["pnl"] >= 0 else "🔴"
        liq_str = f"{w['liq_distance_pct']:.1f}% to liq" if w["liq_distance_pct"] is not None else "N/A"
        lines.append(
            f"<code>{pnl_emoji} ${t:.0f} → PnL {_fv(w['pnl'])} ({_fpct(w['price_change_pct'])}) | {liq_str}</code>"
        )

    monthly = calc_monthly_add_impact(position)
    if monthly:
        lines += [
            f"<code>{SEP}</code>",
            f"<b>💵 +${MONTHLY_ADD_USD:.0f} @ {MONTHLY_LEVERAGE:.0f}x ADD:</b>",
            f"<code>📐 New leverage: {monthly['new_leverage']:.2f}x</code>",
            f"<code>💼 New size:     {_fv(monthly['new_total_size'])}</code>",
            f"<code>🏦 New collat:   {_fv(monthly['new_total_collateral'])}</code>",
        ]

    return "\n".join(lines)


def format_liq_alert(position):
    """Format liquidation risk alert."""
    mark = position["mark_price"]
    liq = position["liq_price"]
    liq_dist = calc_liq_distance_pct(mark, liq)
    return "\n".join([
        f"<b>🚨 [LEV]: LIQUIDATION WARNING</b>",
        f"<code>{SEP}</code>",
        f"<code>⚠️  Only {liq_dist:.1f}% from liquidation!</code>",
        f"<code>💰 MARK:      {_fp(mark)}</code>",
        f"<code>💀 LIQ PRICE: {_fp(liq)}</code>",
        f"<code>📝 Consider adding collateral or reducing size.</code>",
    ])


def format_target_alert(position, target):
    """Format price target hit alert."""
    mark = position["mark_price"]
    pnl = position["pnl"]
    pnl_emoji = "🟢" if pnl >= 0 else "🔴"
    return "\n".join([
        f"<b>🎯 [LEV]: TARGET HIT — ${target:.0f}</b>",
        f"<code>{SEP}</code>",
        f"<code>💰 SOL PRICE: {_fp(mark)}</code>",
        f"<code>{pnl_emoji} CURRENT PnL: {_fv(pnl)}</code>",
        f"<code>🎯 TARGET: ${target:.0f} ✅</code>",
        f"<code>📝 Consider taking partial profits.</code>",
    ])


def format_funding_alert(position):
    """Format high funding rate alert."""
    rate = position["funding_rate"]
    return "\n".join([
        f"<b>⚠️ [LEV]: HIGH FUNDING RATE</b>",
        f"<code>{SEP}</code>",
        f"<code>📈 FUNDING: {rate:.4f}% (threshold: {FUNDING_WARN_PCT}%)</code>",
        f"<code>💰 MARK: {_fp(position['mark_price'])}</code>",
        f"<code>📝 High funding is costing you. Monitor closely.</code>",
    ])


def format_monthly_reminder(position):
    """Format monthly $250 add reminder."""
    monthly = calc_monthly_add_impact(position) if position else None
    lines = [
        f"<b>📅 [LEV]: MONTHLY ADD REMINDER</b>",
        f"<code>{SEP}</code>",
        f"<code>💵 Time to add ${MONTHLY_ADD_USD:.0f} @ {MONTHLY_LEVERAGE:.0f}x leverage</code>",
        f"<code>🔗 https://jup.ag/perps</code>",
    ]
    if monthly:
        lines += [
            f"<code>{SEP}</code>",
            f"<code>📐 After add — new leverage: {monthly['new_leverage']:.2f}x</code>",
            f"<code>💼 New size:     {_fv(monthly['new_total_size'])}</code>",
            f"<code>🏦 New collat:   {_fv(monthly['new_total_collateral'])}</code>",
        ]
    return "\n".join(lines)


def format_leverage_rec(rec):
    """Format /levrec leverage recommendation message."""
    if not rec:
        return "<b>📊 LEV REC</b>\n<code>❌ Could not fetch position or price data.</code>"

    sol_price = rec["sol_price"]
    add_usd = rec["add_usd"]
    vol = rec["vol"]
    funding = rec["funding"]
    vol_adj = rec["vol_adjustment"]
    worst_price = rec["worst_case_price"]
    worst_dist = rec["worst_case_liq_dist"]

    # Vol display
    if vol is None:
        vol_str = "N/A"
        vol_emoji = "❓"
    elif vol > VOL_WARNING_THRESHOLD:
        vol_str = f"{vol:.1f}% (HIGH ⚠️)"
        vol_emoji = "🔴"
    elif vol > VOL_HIGH_THRESHOLD:
        vol_str = f"{vol:.1f}% (moderate)"
        vol_emoji = "🟡"
    else:
        vol_str = f"{vol:.1f}% (low)"
        vol_emoji = "🟢"

    funding_str = f"{funding:+.4f}%" if funding else "N/A"
    funding_emoji = "✅" if funding and funding >= 0 else ("⚠️" if funding and abs(funding) > FUNDING_HIGH_PCT else "💤")

    lines = [
        f"<b>📊 [LEV]: LEVERAGE RECOMMENDATION</b>",
        f"<code>{SEP}</code>",
        f"<code>💰 SOL PRICE: {_fp(sol_price)}</code>",
        f"<code>💵 YOUR ADD:  ${add_usd:.0f}</code>",
    ]

    if vol_adj < 0:
        lines.append(f"<code>⚠️  High vol — leverage reduced by {abs(vol_adj):.1f}x</code>")

    lines.append(f"<code>{SEP}</code>")

    for r in rec["results"]:
        if r["blocked"]:
            lines += [
                f"<b>{r['label']}</b>",
                f"<code>🚫 BLOCKED — liq too close ({r['liq_distance_pct']:.1f}% < {DANGER_LIQ_DISTANCE:.0f}%)</code>",
                f"<code>{SEP}</code>",
            ]
            continue

        liq_str = f"{_fp(r['new_liq_price'])}" if r["new_liq_price"] else "N/A"
        dist_str = f"{r['liq_distance_pct']:.1f}%" if r["liq_distance_pct"] is not None else "N/A"
        funding_cost_str = f"~${r['daily_funding_cost']:.2f}/day" if r["daily_funding_cost"] else "N/A"
        eff_lev_str = f"{r['effective_leverage']:.2f}x" if r["effective_leverage"] else f"{r['leverage']:.1f}x"

        lines += [
            f"<b>{r['label']}</b>",
            f"<code>📐 Leverage:    {r['leverage']:.1f}x (eff. {eff_lev_str})</code>",
            f"<code>💼 New position: {_fv(r['new_total_size'])}</code>",
            f"<code>💀 Est. liq:     {liq_str}</code>",
            f"<code>📏 Liq distance: {dist_str}</code>",
            f"<code>💸 Funding cost: {funding_cost_str}</code>",
            f"<code>⚠️  Risk: {r['risk']}</code>",
            f"<code>✅ Best for: {r['best_for']}</code>",
            f"<code>{SEP}</code>",
        ]

    # Recommended
    rec_opt = rec["recommended"]
    if rec_opt:
        lines.append(f"<b>✅ RECOMMENDATION: {rec_opt['leverage']:.1f}x ({rec_opt['key'].upper()})</b>")
    else:
        lines.append(f"<b>🚫 ALL OPTIONS BLOCKED — market too risky to add now</b>")

    # Context
    lines += [
        f"<code>{SEP}</code>",
        f"<code>{vol_emoji} SOL VOL (30d): {vol_str}</code>",
        f"<code>{funding_emoji} FUNDING RATE: {funding_str} (good for longs)</code>",
    ]

    # Worst-case warning
    if worst_dist is not None and worst_dist < WARN_LIQ_DISTANCE:
        lines += [
            f"<code>{SEP}</code>",
            f"<code>🚨 WORST-CASE: SOL at {_fp(worst_price)} (-20%)</code>",
            f"<code>   → Only {worst_dist:.1f}% from liq — HIGH DANGER</code>",
        ]
    elif worst_dist is not None:
        lines += [
            f"<code>{SEP}</code>",
            f"<code>🧯 WORST-CASE: SOL at {_fp(worst_price)} (-20%)</code>",
            f"<code>   → {worst_dist:.1f}% from liq — manageable</code>",
        ]

    # Scaling suggestions — always shown
    position = rec.get("current_position")
    if position and sol_price:
        scaling = format_scaling_suggestions(position, sol_price)
        if scaling:
            lines.append(scaling)

    return "\n".join(lines)


def format_price_zones(pz):
    """Format /pricezones output."""
    if not pz:
        return "<b>📍 PRICE ZONES</b>\n<code>❌ No position data available.</code>"

    sol_price = pz["sol_price"]
    entry = pz["entry"]
    liq = pz["current_liq"]
    leverage = pz["leverage"]

    lines = [
        f"<b>📍 [LEV]: SOL PRICE ZONES</b>",
        f"<code>{SEP}</code>",
        f"<code>📐 Position: {leverage:.2f}x at {_fp(entry)}</code>",
        f"<code>💰 Current:  {_fp(sol_price)}</code>",
        f"<code>💀 Liq price: {_fp(liq)}</code>",
        f"<code>{SEP}</code>",
        f"<code>If SOL goes to:</code>",
        f"<code>{SEP}</code>",
    ]

    for z in pz["zones"]:
        pnl_str = _fv(z["pnl"])
        dist_str = f"{z['liq_dist']:.1f}% to liq" if z["liq_dist"] is not None else "N/A"
        lines += [
            f"<b>{z['status']} — {_fp(z['price'])}</b>",
            f"<code>  PnL: {pnl_str}  |  {dist_str}</code>",
            f"<code>  → {z['action']}</code>",
        ]

    lines.append(f"<code>{SEP}</code>")
    lines.append(f"<code>Safe price floors (after +${MONTHLY_ADD_USD:.0f} add):</code>")
    for lev_label, floor in pz["floors"].items():
        floor_str = _fp(floor) if floor else "N/A"
        lines.append(f"<code>  @ {lev_label} leverage → liq {floor_str}</code>")

    return "\n".join(lines)


# ── Alert checker (called by scheduler) ──────────────────────────────────────

def check_alerts(position):
    """
    Check all alert conditions. Returns list of (alert_type, message) tuples.
    Only fires each alert once per cooldown period.
    """
    alerts = []
    if not position:
        return alerts

    now = time.time()
    mark = position["mark_price"]
    liq = position["liq_price"]
    funding = position["funding_rate"]

    # Liquidation risk alert — now at 20% (upgraded from 15%), cooldown 1h
    liq_dist = calc_liq_distance_pct(mark, liq)
    if liq_dist is not None and liq_dist < LIQ_WARN_PCT:
        if now - _state["last_liq_alert_ts"] > 3600:
            _state["last_liq_alert_ts"] = now
            alerts.append(("liq", format_liq_alert(position)))

    # DCA zone alert — ping when SOL drops to -10%/-20%/-30% from avg cost
    dca_alert = check_dca_zone_alert(mark)
    if dca_alert:
        alerts.append(("dca_zone", dca_alert))

    # Price target alerts (one-time per target)
    if mark:
        for target in PRICE_TARGETS:
            key = f"target_{target}"
            if key not in _state["targets_hit"] and mark >= target:
                _state["targets_hit"].add(key)
                alerts.append(("target", format_target_alert(position, target)))
            elif key in _state["targets_hit"] and mark < target * 0.97:
                # Reset if price drops >3% below target
                _state["targets_hit"].discard(key)

    # Funding rate alert (cooldown 4h)
    if funding and abs(funding) > FUNDING_WARN_PCT:
        if now - _state["last_funding_alert_ts"] > 14400:
            _state["last_funding_alert_ts"] = now
            alerts.append(("funding", format_funding_alert(position)))

    return alerts


def check_monthly_reminder():
    """Returns monthly reminder message if 30 days have passed, else None."""
    now = time.time()
    days_elapsed = (now - _state["last_monthly_reminder_ts"]) / 86400
    if days_elapsed >= MONTHLY_REMINDER_DAYS:
        _state["last_monthly_reminder_ts"] = now
        return True
    return False


def check_dca_zone_alert(sol_price: float) -> str | None:
    """
    Check if SOL has dropped into a DCA zone (-10%, -20%, -30% from avg cost).
    Fires once per zone with 6h cooldown after reset.
    Returns alert message string or None.
    """
    summary = calc_dca_summary(sol_price)
    if summary["count"] == 0:
        return None

    now = time.time()
    zones = summary["dca_zones"]
    zone_labels = ["10%", "20%", "30%"]

    for i, (zone_price, label) in enumerate(zip(zones, zone_labels)):
        zone_key = f"dca_zone_{label}"
        if sol_price <= zone_price:
            if zone_key not in _state["dca_zone_alerted"]:
                _state["dca_zone_alerted"].add(zone_key)
                avg = summary["avg_cost"]
                pnl = summary["pnl"]
                pnl_emoji = "🟢" if pnl >= 0 else "🔴"
                return "\n".join([
                    f"<b>📍 [DCA]: ZONE ALERT — -{label} FROM AVG</b>",
                    f"<code>{SEP}</code>",
                    f"<code>💰 SOL PRICE  : {_fp(sol_price)}</code>",
                    f"<code>📐 AVG COST   : {_fp(avg)}</code>",
                    f"<code>🎯 ZONE PRICE : {_fp(zone_price)} (-{label})</code>",
                    f"<code>{pnl_emoji} TOTAL PnL   : {_fv(pnl)}</code>",
                    f"<code>{SEP}</code>",
                    f"<code>📋 Consider a DCA add at this level.</code>",
                    f"<code>   Use /dca &lt;amount&gt; to log your add.</code>",
                ])
        else:
            # Reset zone if price recovers above it
            _state["dca_zone_alerted"].discard(zone_key)

    return None


# ── Position Scaling Suggestions ──────────────────────────────────────────────

def calc_scaling_suggestions(position, sol_price: float) -> list:
    """
    Given current position, calculate collateral amounts needed to reach
    2x, 3x effective leverage and what each does to liq price.
    Returns list of dicts.
    """
    if not position:
        return []

    size_usd = float(position.get("size_usd") or 0)
    collateral = float(position.get("collateral") or 0)
    entry_price = float(position.get("entry_price") or sol_price)
    liq_price = float(position.get("liq_price") or 0)

    if size_usd <= 0 or collateral <= 0:
        return []

    current_lev = size_usd / collateral if collateral > 0 else 0
    suggestions = []

    for target_lev in [2.0, 3.0, 4.0]:
        # Collateral needed to hit target_lev with current size
        needed_collateral = size_usd / target_lev
        add_needed = needed_collateral - collateral

        if add_needed <= 0:
            # Already at or above this leverage — show how much to remove
            remove = collateral - needed_collateral
            new_liq = estimate_liq_price(entry_price, needed_collateral, size_usd)
            liq_dist = calc_liq_distance_pct(sol_price, new_liq)
            suggestions.append({
                "target_lev": target_lev,
                "direction": "reduce",
                "collateral_delta": -remove,
                "new_collateral": needed_collateral,
                "new_liq_price": new_liq,
                "liq_distance_pct": liq_dist,
                "current_lev": current_lev,
            })
        else:
            new_collateral = collateral + add_needed
            new_liq = estimate_liq_price(entry_price, new_collateral, size_usd)
            liq_dist = calc_liq_distance_pct(sol_price, new_liq)
            suggestions.append({
                "target_lev": target_lev,
                "direction": "add",
                "collateral_delta": add_needed,
                "new_collateral": new_collateral,
                "new_liq_price": new_liq,
                "liq_distance_pct": liq_dist,
                "current_lev": current_lev,
            })

    return suggestions


def format_scaling_suggestions(position, sol_price: float) -> str:
    """Format position scaling suggestions block (appended to /levrec)."""
    suggestions = calc_scaling_suggestions(position, sol_price)
    if not suggestions:
        return ""

    current_lev = float(position.get("leverage") or 0)
    size_usd = float(position.get("size_usd") or 0)
    liq_price = float(position.get("liq_price") or 0)
    current_liq_dist = calc_liq_distance_pct(sol_price, liq_price)

    lines = [
        f"<code>{SEP}</code>",
        f"<b>🔧 POSITION SCALING</b>",
        f"<code>{SEP}</code>",
        f"<code>Current: {current_lev:.2f}x  |  Size: {_fv(size_usd)}</code>",
        f"<code>Liq dist: {current_liq_dist:.1f}%  |  Liq: {_fp(liq_price)}</code>",
        f"<code>{SEP}</code>",
        f"<code>To reach target leverage:</code>",
    ]

    for s in suggestions:
        lev = s["target_lev"]
        delta = s["collateral_delta"]
        direction = s["direction"]
        new_liq = s["new_liq_price"]
        dist = s["liq_distance_pct"]

        if direction == "add":
            action = f"ADD {_fv(delta)} collateral"
            arrow = "⬆️"
        else:
            action = f"REMOVE {_fv(abs(delta))} collateral"
            arrow = "⬇️"

        dist_str = f"{dist:.1f}%" if dist is not None else "N/A"
        liq_str = _fp(new_liq) if new_liq else "N/A"
        zone_emoji = "🟢" if (dist and dist >= SAFE_LIQ_DISTANCE) else ("🟡" if (dist and dist >= NEUTRAL_LIQ_DISTANCE) else "🔴")

        lines += [
            f"<b>{arrow} {lev:.0f}x target</b>",
            f"<code>  {action}</code>",
            f"<code>  New liq: {liq_str}  {zone_emoji} {dist_str} away</code>",
        ]

    return "\n".join(lines)
