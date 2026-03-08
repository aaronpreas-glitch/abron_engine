"""
Spot DCA Signal Engine — Patch 134.

Computes per-token buy timing scores for the spot basket (WIF/BONK/POPCAT/JUP/RAY/ORCA/PYTH).
Logs DCA_NOW/WATCH signals to spot_signals table for outcome tracking.
Tuner adjusts the effective score threshold once 20+ 7-day outcomes are complete.

Score formula (higher = better DCA entry):
  Fear & Greed:  <15 → +3, <25 → +2, <40 → +1, >70 → -1
  Price (h24):   <-15% → +3, <-8% → +2, <-3% → +1, >10% → -2, >5% → -1
  Momentum (h6): <-5% → +2, <-2% → +1
  Weight gap:    >10% underweight → +2, >5% → +1, overweight >5% → -1

Signal types:
  DCA_NOW (≥5): Strong entry — accumulate now
  WATCH   (3–4): Conditions becoming favorable — prepare
  HOLD    (1–2): Neutral — no action needed
  AVOID   (≤0):  Unfavourable conditions — wait

Note: DOWNTREND badge (Patch 130) handles the sell side separately.
Trend is intentionally excluded from the score — a DOWNTREND during extreme
fear is often the ideal DCA window; trend already has its own visual column.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone, timedelta

log = logging.getLogger("spot_signal_engine")

# kv_store keys
KV_KEY_ENRICHED   = "spot_enriched"          # written every 60s by spot_accumulator
KV_KEY_SIGNALS    = "spot_current_signals"   # all 7 scores cached here
KV_KEY_LAST_SCAN  = "spot_signal_last_scan"  # ISO timestamp of last full scan
KV_KEY_THRESHOLDS = "spot_signal_thresholds" # tuner output

SCAN_INTERVAL_S       = 55 * 60    # 55 min between scans (guards against 60s heartbeat)
DEDUP_INTERVAL_H      = 6          # don't log same token more than once per 6h
OUTCOME_7D_DAYS       = 7
OUTCOME_30D_DAYS      = 30

# One-time table init guard
_table_ensured = False


# ── DB setup ──────────────────────────────────────────────────────────────────

def _ensure_table() -> None:
    global _table_ensured
    if _table_ensured:
        return
    try:
        from utils.db import get_conn  # type: ignore
        with get_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS spot_signals (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts_utc          TEXT NOT NULL,
                    symbol          TEXT NOT NULL,
                    score           REAL NOT NULL,
                    signal_type     TEXT NOT NULL,
                    price_at_signal REAL,
                    h24_at_signal   REAL,
                    h6_at_signal    REAL,
                    fg_at_signal    INTEGER,
                    trend_at_signal TEXT,
                    portfolio_gap   REAL,
                    price_7d        REAL,
                    return_7d_pct   REAL,
                    outcome_7d_ts   TEXT,
                    price_30d       REAL,
                    return_30d_pct  REAL,
                    outcome_30d_ts  TEXT,
                    status          TEXT DEFAULT 'PENDING'
                )
            """)
        _table_ensured = True
    except Exception as exc:
        log.warning("_ensure_table error: %s", exc)


# ── Score computation (pure function — no I/O) ────────────────────────────────

def compute_spot_score(
    h24: float | None,
    h6: float | None,
    fg_value: int | None,
    target_pct: float,
    current_pct: float,
) -> tuple[float, str]:
    """
    Compute DCA entry score for a single basket token.
    Returns (score, signal_type).
    """
    score = 0.0

    # 1. Fear & Greed — macro timing
    if fg_value is not None:
        if   fg_value < 15:  score += 3.0   # extreme fear = accumulate hard
        elif fg_value < 25:  score += 2.0   # fear = good DCA window
        elif fg_value < 40:  score += 1.0   # neutral-fear = mild signal
        elif fg_value > 70:  score -= 1.0   # greed = wait

    # 2. 24h momentum — dip = entry opportunity
    if h24 is not None:
        if   h24 < -15:  score += 3.0
        elif h24 < -8:   score += 2.0
        elif h24 < -3:   score += 1.0
        elif h24 > 10:   score -= 2.0   # already pumped
        elif h24 > 5:    score -= 1.0

    # 3. 6h momentum — confirms dip direction
    if h6 is not None:
        if   h6 < -5:  score += 2.0
        elif h6 < -2:  score += 1.0

    # 4. Portfolio weight gap — underweight tokens get priority
    gap = target_pct - current_pct
    if   gap > 10:  score += 2.0
    elif gap > 5:   score += 1.0
    elif gap < -5:  score -= 1.0   # overweight, skip

    # Signal type
    if   score >= 5:  signal_type = "DCA_NOW"
    elif score >= 3:  signal_type = "WATCH"
    elif score >= 1:  signal_type = "HOLD"
    else:             signal_type = "AVOID"

    return score, signal_type


def _compute_trend(h24: float | None, h6: float | None) -> str:
    """Mirror of spot_accumulator._compute_trend — avoids circular import."""
    if h24 is None:
        return "NEUTRAL"
    if h6 is None:
        return "UPTREND" if h24 > 5 else "DOWNTREND" if h24 < -5 else "NEUTRAL"
    if h24 > 3 and h6 > 0:
        return "UPTREND"
    if h24 < -5 or (h24 < 0 and h6 < -2):
        return "DOWNTREND"
    return "NEUTRAL"


# ── Main hourly scan ──────────────────────────────────────────────────────────

def run_spot_signal_scan() -> None:
    """
    Compute DCA entry signals for all 7 basket tokens. Throttled to ~1/hr.

    Reads: enriched price cache (kv_store), portfolio holdings (DB), F&G (agent_coordinator)
    Writes: spot_signals rows (DCA_NOW/WATCH only, de-duped per 6h), kv_store signal cache
    """
    from utils.db import get_conn  # type: ignore

    _ensure_table()

    ts_now = datetime.now(timezone.utc)
    ts_str = ts_now.strftime("%Y-%m-%d %H:%M:%S")

    # Throttle: skip if < 55 min since last scan
    try:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT value FROM kv_store WHERE key=?", (KV_KEY_LAST_SCAN,)
            ).fetchone()
        if row:
            raw = row[0]
            # value is stored as JSON string (quoted ISO) or bare ISO
            last_iso = json.loads(raw) if raw.startswith('"') else raw
            last_dt  = datetime.fromisoformat(last_iso)
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=timezone.utc)
            if (ts_now - last_dt).total_seconds() < SCAN_INTERVAL_S:
                return
    except Exception:
        pass   # can't read timestamp → run the scan

    # ── 1. Enriched price cache ────────────────────────────────────────────────
    enriched: dict[str, dict] = {}
    try:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT value FROM kv_store WHERE key=?", (KV_KEY_ENRICHED,)
            ).fetchone()
        if row:
            enriched = json.loads(row[0]).get("data") or {}
    except Exception as exc:
        log.warning("run_spot_signal_scan: enriched cache read failed: %s", exc)

    if not enriched or not any(d.get("price", 0) > 0 for d in enriched.values()):
        log.warning("run_spot_signal_scan: no valid prices in cache, skipping")
        return

    # ── 2. Portfolio weights (current_pct per token) ──────────────────────────
    portfolio_weights: dict[str, float] = {}
    try:
        with get_conn() as conn:
            conn.row_factory = sqlite3.Row
            holding_rows = conn.execute(
                "SELECT symbol, token_amount FROM spot_holdings"
            ).fetchall()
        holdings_map: dict[str, float] = {
            r["symbol"]: float(r["token_amount"] or 0) for r in holding_rows
        }
        # Compute current values using enriched prices
        from utils.spot_accumulator import BASKET  # type: ignore   (lazy — no circular issue)
        values: dict[str, float] = {}
        for token in BASKET:
            sym = token["symbol"]
            values[sym] = holdings_map.get(sym, 0.0) * enriched.get(sym, {}).get("price", 0.0)
        total_v = sum(values.values())
        for token in BASKET:
            sym = token["symbol"]
            portfolio_weights[sym] = round(values[sym] / total_v * 100 if total_v > 0 else 0.0, 1)
    except Exception as exc:
        log.warning("run_spot_signal_scan: portfolio weight read failed: %s", exc)
        # Fall through with empty weights — DCA signals will be less precise but still useful

    # ── 3. Fear & Greed ───────────────────────────────────────────────────────
    fg_value: int | None = None
    try:
        from utils.agent_coordinator import get_fear_greed  # type: ignore
        fg_raw = get_fear_greed()
        if fg_raw.get("value") is not None:
            fg_value = int(fg_raw["value"])
    except Exception:
        pass

    # ── 4. Compute scores ─────────────────────────────────────────────────────
    from utils.spot_accumulator import BASKET  # type: ignore

    # De-duplication: symbols already logged in the last DEDUP_INTERVAL_H hours
    recent_logged: set[str] = set()
    try:
        cutoff_dedup = (ts_now - timedelta(hours=DEDUP_INTERVAL_H)).strftime("%Y-%m-%d %H:%M:%S")
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT DISTINCT symbol FROM spot_signals WHERE ts_utc >= ? "
                "AND signal_type IN ('DCA_NOW','WATCH')",
                (cutoff_dedup,),
            ).fetchall()
        recent_logged = {r[0] for r in rows}
    except Exception:
        pass

    scores_cache: dict[str, dict] = {}
    rows_to_insert: list[tuple] = []

    for token in BASKET:
        sym        = token["symbol"]
        pd         = enriched.get(sym, {})
        h24        = pd.get("h24")
        h6         = pd.get("h6")
        price      = pd.get("price", 0.0)
        target_pct = token["target_pct"]
        curr_pct   = portfolio_weights.get(sym, 0.0)
        gap        = round(target_pct - curr_pct, 1)
        trend      = _compute_trend(h24, h6)

        score, signal_type = compute_spot_score(h24, h6, fg_value, target_pct, curr_pct)

        scores_cache[sym] = {
            "score":       round(score, 1),
            "signal_type": signal_type,
            "h24":         round(h24, 2) if h24 is not None else None,
            "h6":          round(h6, 2)  if h6  is not None else None,
            "fg":          fg_value,
            "trend":       trend,
            "gap":         gap,
            "price":       price,
        }

        # Log to DB only if actionable (DCA_NOW or WATCH) and not recently logged
        if signal_type in ("DCA_NOW", "WATCH") and sym not in recent_logged:
            rows_to_insert.append((
                ts_str, sym, round(score, 1), signal_type,
                round(price, 8) if price > 0 else None,
                round(h24, 2) if h24 is not None else None,
                round(h6, 2)  if h6  is not None else None,
                fg_value, trend, gap,
            ))

    # ── 5. Batch insert signal rows ───────────────────────────────────────────
    if rows_to_insert:
        try:
            with get_conn() as conn:
                conn.executemany("""
                    INSERT INTO spot_signals
                        (ts_utc, symbol, score, signal_type, price_at_signal,
                         h24_at_signal, h6_at_signal, fg_at_signal,
                         trend_at_signal, portfolio_gap)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, rows_to_insert)
            log.info(
                "spot_signal_scan: logged %d signals — %s",
                len(rows_to_insert),
                ", ".join(f"{r[1]}/{r[3]}" for r in rows_to_insert),
            )
        except Exception as exc:
            log.warning("spot_signal_scan: DB insert error: %s", exc)

    # ── 6. Write score cache + update last-scan timestamp ────────────────────
    try:
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO kv_store (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (KV_KEY_SIGNALS, json.dumps({"data": scores_cache, "updated_at": ts_now.isoformat()})),
            )
            conn.execute(
                "INSERT INTO kv_store (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (KV_KEY_LAST_SCAN, json.dumps(ts_now.isoformat())),
            )
    except Exception as exc:
        log.warning("spot_signal_scan: kv_store write error: %s", exc)

    log.debug("spot_signal_scan: done — %d tokens, F&G=%s, %d signals logged",
              len(BASKET), fg_value, len(rows_to_insert))


# ── Outcome fill (called every monitor step) ──────────────────────────────────

def fill_spot_signal_outcomes() -> None:
    """
    Fill 7d and 30d price outcomes for pending spot_signals rows.
    Uses enriched price cache (no extra API calls). Lightweight — safe to call every 60s.
    Triggers tuner automatically once 20+ COMPLETE rows exist.
    """
    from utils.db import get_conn  # type: ignore

    _ensure_table()

    ts_now = datetime.now(timezone.utc)
    ts_str = ts_now.strftime("%Y-%m-%d %H:%M:%S")

    # Current prices from cache
    current_prices: dict[str, float] = {}
    try:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT value FROM kv_store WHERE key=?", (KV_KEY_ENRICHED,)
            ).fetchone()
        if row:
            enriched = json.loads(row[0]).get("data") or {}
            current_prices = {sym: float(d.get("price", 0)) for sym, d in enriched.items()}
    except Exception:
        return   # can't fill without prices

    if not any(p > 0 for p in current_prices.values()):
        return

    cutoff_7d  = (ts_now - timedelta(days=OUTCOME_7D_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
    cutoff_30d = (ts_now - timedelta(days=OUTCOME_30D_DAYS)).strftime("%Y-%m-%d %H:%M:%S")

    try:
        with get_conn() as conn:
            conn.row_factory = sqlite3.Row

            pending_7d = conn.execute("""
                SELECT id, symbol, price_at_signal
                FROM spot_signals
                WHERE ts_utc <= ? AND price_7d IS NULL AND price_at_signal IS NOT NULL
            """, (cutoff_7d,)).fetchall()

            pending_30d = conn.execute("""
                SELECT id, symbol, price_at_signal
                FROM spot_signals
                WHERE ts_utc <= ? AND price_30d IS NULL AND price_at_signal IS NOT NULL
            """, (cutoff_30d,)).fetchall()

            updates_7d: list[tuple]  = []
            updates_30d: list[tuple] = []

            for r in pending_7d:
                cur   = current_prices.get(r["symbol"], 0.0)
                orig  = float(r["price_at_signal"])
                if cur > 0 and orig > 0:
                    ret = round((cur - orig) / orig * 100, 2)
                    updates_7d.append((round(cur, 8), ret, ts_str, r["id"]))

            for r in pending_30d:
                cur  = current_prices.get(r["symbol"], 0.0)
                orig = float(r["price_at_signal"])
                if cur > 0 and orig > 0:
                    ret = round((cur - orig) / orig * 100, 2)
                    updates_30d.append((round(cur, 8), ret, ts_str, r["id"]))

            if updates_7d:
                conn.executemany("""
                    UPDATE spot_signals
                    SET price_7d=?, return_7d_pct=?, outcome_7d_ts=?
                    WHERE id=?
                """, updates_7d)
                log.info("fill_spot_outcomes: 7d filled for %d rows", len(updates_7d))

            if updates_30d:
                conn.executemany("""
                    UPDATE spot_signals
                    SET price_30d=?, return_30d_pct=?, outcome_30d_ts=?, status='COMPLETE'
                    WHERE id=?
                """, updates_30d)
                log.info("fill_spot_outcomes: 30d + COMPLETE for %d rows", len(updates_30d))

    except Exception as exc:
        log.warning("fill_spot_signal_outcomes: DB error: %s", exc)
        return

    # Trigger tuner if we now have enough complete samples
    try:
        with get_conn() as conn:
            complete_ct = conn.execute(
                "SELECT COUNT(*) FROM spot_signals WHERE status='COMPLETE'"
            ).fetchone()[0]
        if complete_ct >= 20:
            tune_spot_thresholds()
    except Exception:
        pass


# ── Tuner ─────────────────────────────────────────────────────────────────────

def tune_spot_thresholds() -> None:
    """
    Find the score threshold (2–7) that maximises 7d win rate.
    Writes result to kv_store["spot_signal_thresholds"].
    Requires >= 20 COMPLETE rows.
    """
    from utils.db import get_conn  # type: ignore

    try:
        with get_conn() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT score, return_7d_pct
                FROM spot_signals
                WHERE status='COMPLETE' AND return_7d_pct IS NOT NULL
            """).fetchall()
    except Exception as exc:
        log.warning("tune_spot_thresholds: DB read error: %s", exc)
        return

    if len(rows) < 20:
        return

    total    = len(rows)
    winners  = sum(1 for r in rows if (r["return_7d_pct"] or 0) > 0)
    win_rate = round(winners / total * 100, 1)

    best_threshold = 3.0
    best_wr        = 0.0
    for thr in [2, 3, 4, 5, 6, 7]:
        above = [r for r in rows if (r["score"] or 0) >= thr]
        if len(above) >= 5:
            wr = sum(1 for r in above if (r["return_7d_pct"] or 0) > 0) / len(above)
            if wr > best_wr:
                best_wr        = wr
                best_threshold = float(thr)

    confidence = "low" if total < 20 else "medium" if total < 50 else "high"

    payload = {
        "min_score":   best_threshold,
        "sample_size": total,
        "win_rate":    win_rate,
        "confidence":  confidence,
        "updated_at":  datetime.now(timezone.utc).isoformat(),
    }

    try:
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO kv_store (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (KV_KEY_THRESHOLDS, json.dumps(payload)),
            )
        log.info(
            "tune_spot_thresholds: min_score=%.0f  win_rate=%.1f%%  confidence=%s  n=%d",
            best_threshold, win_rate, confidence, total,
        )
    except Exception as exc:
        log.warning("tune_spot_thresholds: kv_store write error: %s", exc)
