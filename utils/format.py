from datetime import datetime
import html
import logging

try:
    from elite_features import build_intel_block
    _ELITE_ENABLED = True
except ImportError:
    _ELITE_ENABLED = False
    def build_intel_block(token_data):
        return ""

try:
    from utils.position_sizing import calculate_position_size
    from utils.db import get_risk_mode
    _SIZING_ENABLED = True
except ImportError:
    _SIZING_ENABLED = False
    def calculate_position_size(*a, **kw):
        return {}
    def get_risk_mode():
        return {"mode": "NORMAL", "emoji": "🟢", "streak": 0, "threshold_delta": 0, "size_multiplier": 1.0, "min_confidence": None, "paused": False}

try:
    from config import PORTFOLIO_USD, POSITION_SIZE_MIN_PCT, POSITION_SIZE_MAX_PCT
except ImportError:
    PORTFOLIO_USD = 5000.0
    POSITION_SIZE_MIN_PCT = 1.0
    POSITION_SIZE_MAX_PCT = 8.0


def _esc(value):
    return html.escape(str(value))


def _render_pre(rows):
    lines = [str(row or "") for row in rows]
    if not lines:
        return ""
    panel_width = _PANEL_WIDTH
    wrapped = []
    for line in lines:
        wrapped.extend(_wrap_text(line, panel_width))
    return "\n".join(f"<code>{_esc(line)}</code>" for line in wrapped)


_PANEL_WIDTH = 42


def _trim_text(value, max_len):
    text = str(value or "")
    if max_len <= 0:
        return text
    return text


def _wrap_text(value, max_len):
    text = str(value or "")
    if max_len <= 0 or len(text) <= max_len:
        return [text]
    out = []
    remaining = text
    while len(remaining) > max_len:
        cut = remaining.rfind(" ", 0, max_len + 1)
        if cut <= 0:
            cut = max_len
        out.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    out.append(remaining)
    return out


def _kv(label, value, width: int = _PANEL_WIDTH):
    key = str(label or "").upper()[:9]
    line = f"{key:<9} | {value}"
    return _trim_text(line, width)


def _header_block(tag: str, header_rows=None, width: int = _PANEL_WIDTH):
    rows = [
        f"<b>{_esc(tag)}</b>",
        f"<code>{'-' * min(30, width)}</code>",
    ]
    for row in (header_rows or []):
        rows.append(f"<code>{_esc(_trim_text(str(row or ''), width))}</code>")
    return "\n".join(rows)


def _score_bar(score):
    score = max(0, min(100, float(score or 0)))
    blocks = int(round(score / 10.0))
    return ("#" * blocks) + ("-" * (10 - blocks))


def _fmt_pct(value):
    if value is None:
        return "N/A"
    try:
        number = float(value)
        sign = "+" if number > 0 else ""
        return f"{sign}{number:.2f}%"
    except (TypeError, ValueError):
        return "N/A"


def _fmt_num(value, digits=0):
    try:
        return f"{float(value):,.{digits}f}"
    except (TypeError, ValueError):
        return "N/A"


def _fmt_usd_compact(value):
    try:
        n = float(value)
    except (TypeError, ValueError):
        return "N/A"
    abs_n = abs(n)
    if abs_n >= 1_000_000_000:
        return f"${n/1_000_000_000:.2f}B"
    if abs_n >= 1_000_000:
        return f"${n/1_000_000:.2f}M"
    if abs_n >= 1_000:
        return f"${n/1_000:.1f}K"
    return f"${n:.0f}"


def _fmt_price_precise(value):
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


def _fmt_holders(value):
    if value in (None, "", "N/A"):
        return "N/A"
    try:
        return f"{int(float(value)):,}"
    except (TypeError, ValueError):
        return "N/A"


def _confidence_label(confidence):
    conf = str(confidence or "C").upper()
    if conf == "A":
        return "A"
    if conf == "B":
        return "B"
    return "C"


def _compact_entry(entry_type):
    text = str(entry_type or "").strip().lower()
    if text == "momentum continuation":
        return "Momentum continuation"
    if text == "dip recovery setup":
        return "Dip recovery"
    return str(entry_type or "N/A")


def _rsi_meaning(rsi_value):
    if rsi_value is None:
        return None
    try:
        r = float(rsi_value)
    except (TypeError, ValueError):
        return None
    if r < 35:
        return "RSI oversold"
    if r < 50:
        return "RSI weak"
    if r < 65:
        return "RSI healthy"
    if r < 75:
        return "RSI strong"
    return "RSI overbought"


def _macd_meaning(macd_hist_value):
    if macd_hist_value is None:
        return None
    try:
        h = float(macd_hist_value)
    except (TypeError, ValueError):
        return None
    if h > 0.01:
        return "MACD bullish strong"
    if h > 0:
        return "MACD bullish"
    if h > -0.01:
        return "MACD neutral"
    return "MACD bearish"


def _priority_from_score(score):
    try:
        s = float(score or 0)
    except (TypeError, ValueError):
        return "P3"
    if s >= 90:
        return "P1"
    if s >= 80:
        return "P2"
    return "P3"


def _data_age_text(last_trade_unix):
    try:
        ts = int(float(last_trade_unix))
    except (TypeError, ValueError):
        return "N/A"
    if ts <= 0:
        return "N/A"
    now_ts = int(datetime.utcnow().timestamp())
    age_sec = max(0, now_ts - ts)
    if age_sec < 60:
        return f"{age_sec}s"
    if age_sec < 3600:
        return f"{age_sec // 60}m"
    return f"{age_sec // 3600}h"


def _invalidation_from_risk_plan(risk_plan):
    raw = str(risk_plan or "").strip()
    if not raw:
        return "Thesis break"
    return raw.split("|", 1)[0].strip()


def format_signal(token_data, compact: bool = True):
    sep = "━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    thin = "┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄"
    symbol = str(token_data.get("symbol", "UNKNOWN")).upper()
    score = float(token_data.get("score", 0) or 0)
    liquidity = float(token_data.get("liquidity", 0) or 0)
    volume_24h = float(token_data.get("volume_24h", 0) or 0)
    holders = _fmt_holders(token_data.get("holders", "N/A"))
    confidence = token_data.get("confidence", "C")
    regime_label = str(token_data.get("regime_label", "UNKNOWN"))
    profile = str(token_data.get("profile", "STRATEGIC")).upper()
    change_24h = token_data.get("change_24h")
    change_1h = token_data.get("change_1h")
    rsi = token_data.get("rsi")
    macd_hist = token_data.get("macd_hist")
    market_cap = token_data.get("market_cap")
    fdv = token_data.get("fdv")
    wallet_fit = str(token_data.get("wallet_fit") or "W2")
    risk_plan = str(token_data.get("risk_plan") or "SL -10% | TP +12/+25 | Trail 10%")
    txns_h1 = token_data.get("txns_h1")

    confidence_line = _confidence_label(confidence)
    change_line = _fmt_pct(change_24h)
    change_1h_line = _fmt_pct(change_1h)
    cap_value = market_cap if isinstance(market_cap, (int, float)) and market_cap > 0 else fdv
    invalidation = _invalidation_from_risk_plan(risk_plan)

    # Score visuals
    score_int = int(round(score))
    filled = int(round(score / 10))
    score_bar = "█" * filled + "░" * (10 - filled)
    if score >= 85:
        score_tier = "ELITE"
        action = "STRONG BUY"
        action_emoji = "🔥"
    elif score >= 75:
        score_tier = "HIGH"
        action = "BUY"
        action_emoji = "🟢"
    elif score >= 65:
        score_tier = "MED"
        action = "WATCH"
        action_emoji = "🟡"
    else:
        score_tier = "LOW"
        action = "SKIP"
        action_emoji = "⚪"

    regime_emoji = "🟢" if "ON" in regime_label else ("🔴" if "OFF" in regime_label else "🟡")

    # Entry price and targets
    price = float(token_data.get("price") or 0)
    entry_display = _fmt_price_precise(price) if price > 0 else "N/A"
    if price > 0:
        target1 = _fmt_price_precise(price * 1.12)
        target2 = _fmt_price_precise(price * 1.25)
        target3 = _fmt_price_precise(price * 1.50)
        stop = _fmt_price_precise(price * 0.90)
    else:
        target1 = target2 = target3 = stop = "N/A"

    # Momentum line
    tech_parts = [x for x in [_rsi_meaning(rsi), _macd_meaning(macd_hist)] if x]
    momentum_line = " · ".join(tech_parts) if tech_parts else "—"

    # Position sizing
    size_usd_str = ""
    size_pct_str = ""
    size_note = ""
    risk_mode_line = ""
    try:
        if _SIZING_ENABLED:
            risk_mode = get_risk_mode()
            rm_emoji = risk_mode.get("emoji", "🟢")
            rm_label = risk_mode.get("mode", "NORMAL")
            rm_streak = risk_mode.get("streak", 0)
            size_mult = float(risk_mode.get("size_multiplier", 1.0))
            rm_streak_str = f"  L{rm_streak}" if rm_streak > 0 else ""
            risk_mode_line = f"<code>  Risk    {rm_emoji} {_esc(rm_label)}{_esc(rm_streak_str)}</code>"

            # Pull outcome stats for this confidence tier
            outcome_stats = None
            try:
                from elite_features import get_pattern_win_rate
                stats = get_pattern_win_rate(confidence, regime_label, score_min=score)
                if stats:
                    outcome_stats = {
                        "win_rate": stats["win_rate"],
                        "sample_size": stats["sample_size"],
                        "avg_win_pct": max(1.0, stats["avg_return_4h"]),
                        "avg_loss_pct": -5.0,
                    }
            except Exception:
                pass

            sizing = calculate_position_size(
                token=token_data,
                portfolio_usd=PORTFOLIO_USD,
                outcome_stats=outcome_stats,
                min_pct=POSITION_SIZE_MIN_PCT,
                max_pct=POSITION_SIZE_MAX_PCT * size_mult,
            )
            raw_usd = sizing.get("position_usd", 0)
            adj_usd = raw_usd * size_mult
            adj_pct = (adj_usd / PORTFOLIO_USD * 100) if PORTFOLIO_USD > 0 else 0
            adj_usd = max(PORTFOLIO_USD * POSITION_SIZE_MIN_PCT / 100, adj_usd * size_mult)
            adj_pct = adj_usd / PORTFOLIO_USD * 100
            slip_bps = sizing.get("slippage_bps", 0)
            limited_by = sizing.get("limited_by", "kelly")
            size_usd_str = f"${adj_usd:,.0f}"
            size_pct_str = f"{adj_pct:.1f}%"
            if limited_by == "slippage":
                size_note = f"  slip {slip_bps:.0f}bps"
            elif size_mult < 1.0:
                size_note = f"  {rm_label.lower()}-reduced"
    except Exception:
        pass

    lines = [
        f"<b>🎯 MEMECOIN SETUP — ${_esc(symbol)}</b>",
        f"<code>{sep}</code>",
        f"<code>  Score  {score_bar} {score_int}/100 [{_esc(score_tier)}]</code>",
        f"<code>  Grade  {_esc(confidence_line)}   Regime  {_esc(regime_label)}</code>",
    ]
    if risk_mode_line:
        lines.append(risk_mode_line)
    lines += [
        f"<code>{thin}</code>",
        f"<code>  Cap    {_esc(_fmt_usd_compact(cap_value)):<10}  Liq    {_esc(_fmt_usd_compact(liquidity))}</code>",
        f"<code>  Vol24h {_esc(_fmt_usd_compact(volume_24h)):<10}  1h     {_esc(change_1h_line)}</code>",
        f"<code>  24h   {_esc(change_line):<11}  Holders {_esc(holders)}</code>",
    ]

    if tech_parts or txns_h1:
        txn_part = f"  Txns/h {_esc(str(int(txns_h1)))}" if txns_h1 else ""
        lines.append(f"<code>  Momo  {_esc(momentum_line)}{txn_part}</code>")

    lines += [
        f"<code>{sep}</code>",
        f"<code>  {action_emoji} {_esc(action)}</code>",
        f"<code>{thin}</code>",
        f"<code>  Entry   {_esc(entry_display)}</code>",
        f"<code>  TP1     {_esc(target1)}  (+12%)</code>",
        f"<code>  TP2     {_esc(target2)}  (+25%)</code>",
        f"<code>  TP3     {_esc(target3)}  (+50%)</code>",
        f"<code>  Stop    {_esc(stop)}  (-10%)</code>",
        f"<code>{thin}</code>",
        f"<code>  Invalidation: {_esc(invalidation)}</code>",
    ]

    if size_usd_str:
        lines += [
            f"<code>{thin}</code>",
            f"<code>  Size    {_esc(size_usd_str)}  ({_esc(size_pct_str)} of ${PORTFOLIO_USD:,.0f}){_esc(size_note)}</code>",
        ]

    lines.append(f"<code>{sep}</code>")

    # Elite intelligence block — narrative, sentiment, win rate
    try:
        if _ELITE_ENABLED:
            intel = build_intel_block(token_data)
            if intel:
                lines.append(intel)
    except Exception:
        pass

    return "\n".join(lines)


def format_runner_watch(token_data, compact: bool = True):
    sep = "━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    thin = "┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄"
    symbol = str(token_data.get("symbol") or "UNKNOWN").upper()
    name = str(token_data.get("name") or symbol)
    age_hours = token_data.get("age_hours")
    market_cap = token_data.get("market_cap")
    fdv = token_data.get("fdv")
    liquidity = float(token_data.get("liquidity") or 0)
    volume_24h = float(token_data.get("volume_24h") or 0)
    change_24h = token_data.get("change_24h")
    change_1h = token_data.get("change_1h")
    txns_h1 = token_data.get("txns_h1")
    watch_score = token_data.get("watch_score")
    x_proxy = str(token_data.get("x_proxy_label") or "")
    narrative = str(token_data.get("narrative_label") or "")
    wallet_fit = str(token_data.get("wallet_fit") or "W3")
    risk_plan = str(token_data.get("risk_plan") or "Small size | hard stop | quick scale-outs")
    cap_value = market_cap if isinstance(market_cap, (int, float)) and market_cap > 0 else fdv
    age_text = f"{float(age_hours):.1f}h old" if isinstance(age_hours, (int, float)) else "New"
    score_val = float(watch_score) if isinstance(watch_score, (int, float)) else 0.0
    score_int = int(round(score_val))
    filled = int(round(score_val / 10))
    score_bar = "█" * filled + "░" * (10 - filled)
    change_line = _fmt_pct(change_24h)
    change_1h_line = _fmt_pct(change_1h)
    price = float(token_data.get("price") or 0)
    entry_display = _fmt_price_precise(price) if price > 0 else "N/A"
    txns_text = str(int(txns_h1)) if isinstance(txns_h1, (int, float)) else "—"

    # Volume-to-liquidity ratio for heat indicator
    vol_liq_ratio = volume_24h / liquidity if liquidity > 0 else 0
    if vol_liq_ratio > 3:
        heat = "🔥🔥🔥 HOT"
    elif vol_liq_ratio > 1.5:
        heat = "🔥🔥 ACTIVE"
    elif vol_liq_ratio > 0.5:
        heat = "🔥 MOVING"
    else:
        heat = "❄️ COLD"

    narrative_line = f" · {_esc(narrative)}" if narrative and narrative != "N/A" else ""
    x_line = f" · {_esc(x_proxy)}" if x_proxy and x_proxy != "N/A" else ""

    lines = [
        f"<b>🚀 RUNNER WATCH — ${_esc(symbol)}</b>",
        f"<code>{sep}</code>",
        f"<code>  Score  {score_bar} {score_int}/100</code>",
        f"<code>  Age    {_esc(age_text)}</code>",
        f"<code>  Heat   {_esc(heat)}</code>",
        f"<code>{thin}</code>",
        f"<code>  Cap    {_esc(_fmt_usd_compact(cap_value)):<10}  Liq    {_esc(_fmt_usd_compact(liquidity))}</code>",
        f"<code>  Vol24h {_esc(_fmt_usd_compact(volume_24h)):<10}  Txns/h {_esc(txns_text)}</code>",
        f"<code>  24h   {_esc(change_line):<11}  1h     {_esc(change_1h_line)}</code>",
        f"<code>{sep}</code>",
        f"<code>  🟡 MONITOR — DO NOT CHASE</code>",
        f"<code>  Entry   {_esc(entry_display)}</code>",
        f"<code>  Plan    {_esc(risk_plan)}</code>",
        f"<code>  Exit    Flow collapse / liquidity fade</code>",
    ]

    if narrative_line or x_line:
        lines.append(f"<code>{thin}</code>")
        lines.append(f"<code>  Signal{narrative_line}{x_line}</code>")

    lines.append(f"<code>{sep}</code>")
    lines.append(f"<i>⚠️ Watch only — not a buy signal. Confirm before sizing.</i>")

    return "\n".join(lines)


def _watchlist_status_label(status):
    raw = str(status or "").strip()
    if not raw:
        return "Unknown"
    upper = raw.upper()
    if upper == "NODATA":
        return "NoData"
    return raw.title()


def _watchlist_status_code(status):
    status_norm = _watchlist_status_label(status)
    mapping = {
        "Momentum": "MOM",
        "Reclaim": "REC",
        "Breakdown": "BRK",
        "Range": "RNG",
        "Volatile": "VOL",
        "Illiquid": "ILL",
        "NoData": "NOD",
        "Unknown": "UNK",
    }
    return mapping.get(status_norm, "UNK"), status_norm


def format_watchlist_signal(token_data, compact: bool = True):
    sep = "━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    thin = "┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄"

    symbol = str(token_data.get("symbol") or "UNKNOWN").upper()
    status_code, status_label = _watchlist_status_code(token_data.get("status"))
    reason = str(token_data.get("reason") or "Status condition triggered.")

    # Market data
    market_cap = token_data.get("market_cap")
    fdv = token_data.get("fdv")
    cap_value = market_cap if isinstance(market_cap, (int, float)) and market_cap > 0 else fdv
    liquidity = float(token_data.get("liquidity") or 0)
    volume_24h = float(token_data.get("volume_24h") or 0)
    price = float(token_data.get("price") or 0)
    change_24h = token_data.get("change_24h")
    change_1h = token_data.get("change_1h")
    txns_h1 = token_data.get("txns_h1")
    upside = str(token_data.get("upside_potential") or "Medium").title()
    failure = str(token_data.get("failure_risk") or "Medium").title()

    entry_display = _fmt_price_precise(price) if price > 0 else "N/A"
    change_24h_str = _fmt_pct(change_24h)
    change_1h_str = _fmt_pct(change_1h)
    vol_to_liq = (volume_24h / liquidity) if liquidity > 0 else 0.0

    # Status → signal tier
    if status_label == "Momentum":
        action = "STRONG WATCH"
        action_emoji = "🟢"
        header_tag = "🚀 MOMENTUM"
    elif status_label == "Reclaim":
        action = "WATCH FOR ENTRY"
        action_emoji = "🟡"
        header_tag = "♻️ RECLAIM"
    elif status_label == "Breakdown":
        action = "AVOID / EXIT"
        action_emoji = "🔴"
        header_tag = "⚠️ BREAKDOWN"
    elif status_label == "Volatile":
        action = "MONITOR ONLY"
        action_emoji = "🟠"
        header_tag = "⚡ VOLATILE"
    else:
        action = "MONITOR"
        action_emoji = "⚪"
        header_tag = "👁 RANGING"

    # Upside / risk label → compact tag
    upside_tag = {"High": "🔥 High", "Medium": "🟡 Med", "Low": "⚪ Low"}.get(upside, upside)
    risk_tag = {"High": "🔴 High", "Medium": "🟡 Med", "Low": "🟢 Low"}.get(failure, failure)

    # Heat indicator based on vol/liq ratio
    if vol_to_liq >= 3.0:
        heat = "🔥🔥🔥 HOT"
    elif vol_to_liq >= 1.5:
        heat = "🔥🔥 ACTIVE"
    elif vol_to_liq >= 0.5:
        heat = "🔥 MOVING"
    else:
        heat = "❄️ COLD"

    # Txn activity
    txn_str = f"{int(txns_h1)} txns/1h" if txns_h1 else "—"

    lines = [
        f"<b>👁 [SIGNAL]: WATCHLIST  ·  {header_tag}</b>",
        f"<code>{sep}</code>",
        f"<code>  ${_esc(symbol):<10}  {action_emoji} {_esc(action)}</code>",
        f"<code>{thin}</code>",
        f"<code>  Cap    {_esc(_fmt_usd_compact(cap_value)):<10}  Liq  {_esc(_fmt_usd_compact(liquidity))}</code>",
        f"<code>  Vol24h {_esc(_fmt_usd_compact(volume_24h)):<10}  24h  {_esc(change_24h_str)}</code>",
        f"<code>  1h     {_esc(change_1h_str):<10}  {heat}</code>",
        f"<code>{thin}</code>",
        f"<code>  📍 Entry   {_esc(entry_display)}</code>",
        f"<code>  📝 {_esc(reason[:80])}</code>",
        f"<code>{thin}</code>",
        f"<code>  Upside {upside_tag:<12}  Risk  {risk_tag}</code>",
        f"<code>{sep}</code>",
    ]

    return "\n".join(lines)


def format_legacy_recovery(token_data):
    sep = "━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    thin = "┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄"
    symbol = str(token_data.get("symbol", "UNKNOWN")).upper()
    price = float(token_data.get("price", 0) or 0)
    change_24h = token_data.get("change_24h")
    change_1h = token_data.get("change_1h")
    liquidity = float(token_data.get("liquidity", 0) or 0)
    volume_24h = float(token_data.get("volume_24h", 0) or 0)
    market_cap = token_data.get("market_cap")
    fdv = token_data.get("fdv")
    pattern_label = str(token_data.get("pattern_label") or "Reversal")
    pattern_status = str(token_data.get("pattern_status") or "Forming")
    sol_status = str(token_data.get("sol_status") or "NEUTRAL")
    age_days = token_data.get("age_days")
    holders = _fmt_holders(token_data.get("holders", "N/A"))
    score = float(token_data.get("score", 0) or 0)
    confidence = token_data.get("confidence", "B")

    cap_value = market_cap if isinstance(market_cap, (int, float)) and market_cap > 0 else fdv
    cap_display = _fmt_usd_compact(cap_value) if cap_value else "N/A"
    display_price = _fmt_price_precise(price)
    change_line = _fmt_pct(change_24h)
    change_1h_line = _fmt_pct(change_1h)
    confidence_line = _confidence_label(confidence)

    age_text = f"{int(age_days)}d established" if isinstance(age_days, (int, float)) and age_days > 0 else ">90d established"
    sol_emoji = "🟢" if sol_status == "RISK_ON" else ("🔴" if "OFF" in sol_status else "🟡")

    # Pattern status label
    if pattern_status.lower() in ("confirmed", "active"):
        pattern_emoji = "✅"
    elif pattern_status.lower() in ("forming", "developing"):
        pattern_emoji = "🔄"
    else:
        pattern_emoji = "⏳"

    # Score bar
    score_int = int(round(score))
    filled = int(round(score / 10))
    score_bar = "█" * filled + "░" * (10 - filled)

    # Targets — legacy recovery uses wider targets (bigger moves expected)
    if price > 0:
        target1 = _fmt_price_precise(price * 1.50)
        target2 = _fmt_price_precise(price * 2.00)
        target3 = _fmt_price_precise(price * 3.00)
        stop = _fmt_price_precise(price * 0.50)
    else:
        target1 = target2 = target3 = stop = "N/A"

    lines = [
        f"<b>♻️ LEGACY RECOVERY — ${_esc(symbol)}</b>",
        f"<code>{sep}</code>",
        f"<code>  Score  {score_bar} {score_int}/100  [{_esc(confidence_line)}]</code>",
        f"<code>  Age    {_esc(age_text)}   Grade {_esc(confidence_line)}</code>",
        f"<code>  {sol_emoji} Macro   SOL {_esc(sol_status)}</code>",
        f"<code>{thin}</code>",
        f"<code>  Cap    {_esc(cap_display):<10}  Liq    {_esc(_fmt_usd_compact(liquidity))}</code>",
        f"<code>  Vol24h {_esc(_fmt_usd_compact(volume_24h)):<10}  Holders {_esc(holders)}</code>",
        f"<code>  24h   {_esc(change_line):<11}  1h     {_esc(change_1h_line)}</code>",
        f"<code>{thin}</code>",
        f"<code>  {pattern_emoji} Pattern  {_esc(pattern_label)} — {_esc(pattern_status)}</code>",
        f"<code>  Thesis   Proven narrative + volume spike revival</code>",
        f"<code>{sep}</code>",
        f"<code>  ✅ BUY — LEGACY RECOVERY SIGNAL</code>",
        f"<code>{thin}</code>",
        f"<code>  Entry   {_esc(display_price)}</code>",
        f"<code>  TP1     {_esc(target1)}  (+50%)</code>",
        f"<code>  TP2     {_esc(target2)}  (+100%)</code>",
        f"<code>  TP3     {_esc(target3)}  (+200%)</code>",
        f"<code>  Stop    {_esc(stop)}  (-50%)</code>",
        f"<code>{thin}</code>",
        f"<code>  Invalidation: Pattern fails / volume collapses</code>",
        f"<code>{sep}</code>",
    ]

    # Elite intelligence block
    try:
        if _ELITE_ENABLED:
            intel = build_intel_block(token_data)
            if intel:
                lines.append(intel)
    except Exception:
        pass

    return "\n".join(lines)


def format_watchlist_summary(rows):
    sep = "━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    thin = "┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄"

    if not rows:
        return "\n".join([
            f"<b>👁 WATCHLIST SUMMARY</b>",
            f"<code>{sep}</code>",
            f"<code>No watchlist tokens configured.</code>",
            f"<code>{sep}</code>",
        ])

    order = {"Momentum": 0, "Reclaim": 1, "Volatile": 2, "Range": 3, "Breakdown": 4, "Illiquid": 5, "NoData": 6}
    sorted_rows = sorted(
        rows,
        key=lambda r: (
            order.get(str(r.get("status") or ""), 9),
            -(float(r.get("volume_24h") or 0)),
        ),
    )

    total = len(sorted_rows)
    live = sum(1 for r in sorted_rows if bool(r.get("has_live_data", True)))
    no_data = total - live

    # Status count breakdown
    status_counts = {}
    for r in sorted_rows:
        _, sl = _watchlist_status_code(r.get("status"))
        status_counts[sl] = status_counts.get(sl, 0) + 1

    s_emoji_map = {
        "Momentum": "🟢", "Reclaim": "🟡", "Volatile": "🟠",
        "Range": "⚪", "Breakdown": "🔴", "Illiquid": "⛔", "NoData": "❓",
    }
    s_tag_map = {
        "Momentum": "MOM", "Reclaim": "RCL", "Volatile": "VOL",
        "Range": "RNG", "Breakdown": "BRK", "Illiquid": "ILL", "NoData": "N/A",
    }

    # Build compact status pill summary
    pill_parts = []
    for label in ["Momentum", "Reclaim", "Volatile", "Range", "Breakdown", "Illiquid"]:
        n = status_counts.get(label, 0)
        if n:
            pill_parts.append(f"{s_emoji_map[label]}{label[:3]} {n}")
    pills = "  ".join(pill_parts)

    lines = [
        f"<b>👁 WATCHLIST SUMMARY  ·  {live}/{total} live</b>",
        f"<code>{sep}</code>",
        f"<code>  {pills}</code>",
        f"<code>{thin}</code>",
        f"<code>  {'TOKEN':<8}  {'STATUS':<4}  {'24H':>7}  {'1H':>6}  {'LIQ':>7}</code>",
        f"<code>{thin}</code>",
    ]

    for row in sorted_rows:
        _, status_label = _watchlist_status_code(row.get("status"))
        symbol = str(row.get("symbol") or "?").upper()
        s_emoji = s_emoji_map.get(status_label, "⚪")
        s_tag = s_tag_map.get(status_label, "???")
        ch24 = _fmt_pct(row.get("change_24h"))
        ch1 = _fmt_pct(row.get("change_1h"))
        liq = _fmt_usd_compact(row.get("liquidity"))
        has_data = bool(row.get("has_live_data", True))
        if not has_data:
            lines.append(f"<code>  ❓ {symbol:<8}  {'—':<4}  {'—':>7}  {'—':>6}  {'—':>7}</code>")
        else:
            lines.append(
                f"<code>  {s_emoji} {symbol:<8}  {s_tag:<4}  {ch24:>7}  {ch1:>6}  {liq:>7}</code>"
            )

    lines.append(f"<code>{sep}</code>")
    return "\n".join(lines)
