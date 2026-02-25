def closes(candles):
    out = []
    for x in candles:
        c = x.get("c")
        if c is None:
            continue
        try:
            out.append(float(c))
        except Exception:
            continue
    return out


def vols(candles):
    out = []
    for x in candles:
        v = x.get("v")
        if v is None:
            continue
        try:
            out.append(float(v))
        except Exception:
            continue
    return out


def sma(values):
    if not values:
        return 0.0
    return sum(values) / len(values)


def pct_change(a, b):
    if a == 0:
        return 0.0
    return (b - a) / a


def compute_structure(token_candles_7d, sol_candles_7d):
    """
    Computes features needed by scoring/model.py
    Returns dict:
      pullback_depth, trend_confirmed, rs_7d, rs_3d,
      volume_contracting, volume_expanding_on_bounce
    """

    t_close = closes(token_candles_7d)
    s_close = closes(sol_candles_7d)

    if len(t_close) < 30 or len(s_close) < 30:
        return {}

    # Pullback depth from 7D high
    high_7d = max(t_close)
    cur = t_close[-1]
    pullback_depth = (high_7d - cur) / high_7d if high_7d else 0.0

    # Trend proxy: price above 7D SMA AND SMA rising
    sma_7d = sma(t_close)
    sma_prev = sma(t_close[:-24]) if len(t_close) > 24 else sma_7d
    sma_rising = sma_7d >= sma_prev
    trend_confirmed = (cur > sma_7d) and sma_rising

    # Relative strength vs SOL
    t_7d = pct_change(t_close[0], t_close[-1])
    s_7d = pct_change(s_close[0], s_close[-1])
    rs_7d = t_7d - s_7d

    if len(t_close) >= 72 and len(s_close) >= 72:
        t_3d = pct_change(t_close[-72], t_close[-1])
        s_3d = pct_change(s_close[-72], s_close[-1])
    else:
        t_3d = pct_change(t_close[0], t_close[-1])
        s_3d = pct_change(s_close[0], s_close[-1])
    rs_3d = t_3d - s_3d

    # Volume structure
    t_vol = vols(token_candles_7d)
    if len(t_vol) < 48:
        volume_contracting = False
        volume_expanding_on_bounce = False
    else:
        last_24 = sma(t_vol[-24:])
        prev_24 = sma(t_vol[-48:-24])
        volume_contracting = last_24 < prev_24

        last_6 = sma(t_vol[-6:])
        volume_expanding_on_bounce = (last_6 > last_24) and (t_close[-1] > t_close[-2])

    return {
        "pullback_depth": pullback_depth,
        "trend_confirmed": trend_confirmed,
        "rs_7d": rs_7d,
        "rs_3d": rs_3d,
        "volume_contracting": volume_contracting,
        "volume_expanding_on_bounce": volume_expanding_on_bounce,
    }


def ema(values, period):
    if not values or period <= 0:
        return []
    alpha = 2.0 / (period + 1.0)
    out = []
    prev = float(values[0])
    out.append(prev)
    for v in values[1:]:
        cur = (float(v) * alpha) + (prev * (1.0 - alpha))
        out.append(cur)
        prev = cur
    return out


def rsi(values, period=14):
    if not values or len(values) < period + 1:
        return None

    gains = []
    losses = []
    for i in range(1, len(values)):
        delta = float(values[i]) - float(values[i - 1])
        gains.append(max(delta, 0.0))
        losses.append(max(-delta, 0.0))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(gains)):
        avg_gain = ((avg_gain * (period - 1)) + gains[i]) / period
        avg_loss = ((avg_loss * (period - 1)) + losses[i]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def macd(values, fast=12, slow=26, signal=9):
    if not values or len(values) < slow + signal:
        return None

    fast_ema = ema(values, fast)
    slow_ema = ema(values, slow)
    line = [f - s for f, s in zip(fast_ema, slow_ema)]
    signal_line = ema(line, signal)
    hist = [m - s for m, s in zip(line, signal_line)]

    return {
        "macd_line": line[-1],
        "macd_signal": signal_line[-1],
        "macd_hist": hist[-1],
    }
