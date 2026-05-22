"""
Spot Accumulation endpoints — Patch 128 + Patch 134.

Routes:
  GET  /api/spot/status        — holdings + live prices + PnL + basket config
  GET  /api/spot/advice        — allocation advice for ?amount=N budget
  GET  /api/spot/history       — last 50 transactions from spot_buys audit log
  GET  /api/spot/signals       — per-token DCA signal scores + learning progress (Patch 134)
  GET  /api/spot/analytics     — signal performance breakdowns + history (Patch 134)
  GET  /api/spot/posture       — per-token outcome-conditioned entry posture (Patch 304)
  POST /api/spot/buy           — buy {symbol, mint, amount_usd}
  POST /api/spot/sell/{symbol} — sell position (manual, optional ?pct=50)
"""
from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Query

from auth import get_current_user
from routers._shared import _ensure_engine_path

log    = logging.getLogger("dashboard")
router = APIRouter(prefix="/api/spot", tags=["spot"])


@router.get("/status")
async def spot_status_ep(_: str = Depends(get_current_user)):
    """Holdings + live prices + PnL + basket config."""
    _ensure_engine_path()
    try:
        from utils.spot_accumulator import get_portfolio_state  # type: ignore
        return await asyncio.to_thread(get_portfolio_state)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/advice")
async def spot_advice_ep(
    amount: float = Query(..., gt=0, description="Budget in USD"),
    _: str = Depends(get_current_user),
):
    """Allocation advice — how to split $amount across underweight basket positions."""
    _ensure_engine_path()
    try:
        from utils.spot_accumulator import get_allocation_advice  # type: ignore
        advice = await asyncio.to_thread(get_allocation_advice, amount)
        return {"amount_usd": amount, "advice": advice}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/history")
async def spot_history_ep(
    limit: int = Query(default=50, ge=1, le=200, description="Max rows to return"),
    _: str = Depends(get_current_user),
):
    """Last N transactions from the spot_buys audit log."""
    _ensure_engine_path()
    try:
        from utils.db import get_conn  # type: ignore
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT id, ts_utc, symbol, side, amount_usd, token_amount, "
                "price_usd, tx_sig, dry_run FROM spot_buys "
                "ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return {"transactions": [dict(r) for r in rows]}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/buy")
async def spot_buy_ep(body: dict, _: str = Depends(get_current_user)):
    """Buy a basket token. Body: {symbol, mint, amount_usd}."""
    _ensure_engine_path()
    symbol     = str(body.get("symbol", "")).strip().upper()
    mint       = str(body.get("mint",   "")).strip()
    amount_usd = float(body.get("amount_usd", 0))

    if not symbol or not mint:
        raise HTTPException(status_code=400, detail="symbol and mint are required")
    if amount_usd < 1:
        raise HTTPException(status_code=400, detail="amount_usd must be >= 1")

    try:
        from utils.spot_accumulator import buy_spot  # type: ignore
        result = await asyncio.to_thread(buy_spot, symbol, mint, amount_usd)
        if not result.get("success"):
            raise HTTPException(status_code=422, detail=result.get("error", "buy failed"))
        return result
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/signals")
async def spot_signals_ep(_: str = Depends(get_current_user)):
    """
    Per-token DCA signal scores + learning loop analytics. Patch 134.
    Reads cached scores from kv_store (written hourly by signal engine).
    """
    _ensure_engine_path()
    try:
        from utils.db import get_conn  # type: ignore

        # ── Score cache ───────────────────────────────────────────────────────
        signals: dict = {}
        signals_updated_at: str | None = None
        try:
            with get_conn() as conn:
                row = conn.execute(
                    "SELECT value FROM kv_store WHERE key='spot_current_signals'"
                ).fetchone()
            if row:
                cached = json.loads(row[0])
                signals = cached.get("data") or {}
                signals_updated_at = cached.get("updated_at")
        except Exception:
            pass

        # ── Tuner thresholds ──────────────────────────────────────────────────
        tuner_data: dict = {}
        try:
            with get_conn() as conn:
                row = conn.execute(
                    "SELECT value FROM kv_store WHERE key='spot_signal_thresholds'"
                ).fetchone()
            if row:
                tuner_data = json.loads(row[0])
        except Exception:
            pass

        # ── Learning analytics ────────────────────────────────────────────────
        total    = 0
        complete = 0
        try:
            with get_conn() as conn:
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS spot_signals ("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT,"
                    "ts_utc TEXT, symbol TEXT, score REAL, signal_type TEXT,"
                    "price_at_signal REAL, h24_at_signal REAL, h6_at_signal REAL,"
                    "fg_at_signal INTEGER, trend_at_signal TEXT, portfolio_gap REAL,"
                    "price_7d REAL, return_7d_pct REAL, outcome_7d_ts TEXT,"
                    "price_30d REAL, return_30d_pct REAL, outcome_30d_ts TEXT,"
                    "status TEXT DEFAULT 'PENDING')"
                )
                total    = conn.execute("SELECT COUNT(*) FROM spot_signals").fetchone()[0]
                complete = conn.execute(
                    "SELECT COUNT(*) FROM spot_signals WHERE status='COMPLETE'"
                ).fetchone()[0]
        except Exception:
            pass

        # Milestone ladder: 10→20→50→100, then perpetual +50 increments forever.
        # Spot outcomes take 7 days each — ladder is scaled accordingly (vs memecoin's +500).
        if complete >= 100:
            tuner_threshold = ((complete // 50) + 1) * 50  # perpetual: next 50 boundary
        elif complete >= 50:
            tuner_threshold = 100
        elif complete >= 20:
            tuner_threshold = 50
        elif complete >= 10:
            tuner_threshold = 20
        else:
            tuner_threshold = 10

        complete_pct = round(min(complete / tuner_threshold * 100, 100.0), 1)

        confidence   = tuner_data.get("confidence", "pending")
        min_score    = tuner_data.get("min_score", 3)

        return {
            "signals": signals,
            "signals_updated_at": signals_updated_at,
            "learning": {
                "total":            total,
                "complete":         complete,
                "tuner_threshold":  tuner_threshold,
                "complete_pct":     complete_pct,
                "confidence":       confidence,
                "min_score":        min_score,
                "win_rate":         tuner_data.get("win_rate"),
                "sample_size":      tuner_data.get("sample_size"),
            },
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/analytics")
async def spot_analytics_ep(_: str = Depends(get_current_user)):
    """
    Signal outcome analytics — breakdowns by signal type, F&G bucket, and token.
    Plus tuner output and recent signal history. Patch 134.
    """
    _ensure_engine_path()

    def _run():
        import sqlite3 as _sqlite3
        from utils.db import get_conn  # type: ignore

        # Ensure table exists (idempotent)
        _CREATE = (
            "CREATE TABLE IF NOT EXISTS spot_signals ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, ts_utc TEXT, symbol TEXT, "
            "score REAL, signal_type TEXT, price_at_signal REAL, h24_at_signal REAL, "
            "h6_at_signal REAL, fg_at_signal INTEGER, trend_at_signal TEXT, portfolio_gap REAL, "
            "price_7d REAL, return_7d_pct REAL, outcome_7d_ts TEXT, "
            "price_30d REAL, return_30d_pct REAL, outcome_30d_ts TEXT, "
            "status TEXT DEFAULT 'PENDING')"
        )
        with get_conn() as conn:
            conn.execute(_CREATE)
            total    = conn.execute("SELECT COUNT(*) FROM spot_signals").fetchone()[0]
            complete = conn.execute(
                "SELECT COUNT(*) FROM spot_signals WHERE status='COMPLETE'"
            ).fetchone()[0]
            pending  = total - complete

        # Load completed rows for analytics
        with get_conn() as conn:
            conn.row_factory = _sqlite3.Row
            rows = conn.execute("""
                SELECT symbol, score, signal_type, fg_at_signal, h24_at_signal,
                       trend_at_signal, return_7d_pct, return_30d_pct
                FROM spot_signals
                WHERE status='COMPLETE' AND return_7d_pct IS NOT NULL
            """).fetchall()
        rows = [dict(r) for r in rows]

        def _stats(subset: list) -> dict:
            if not subset:
                return {"count": 0, "win_rate_7d": None, "avg_return_7d": None}
            wins = sum(1 for r in subset if (r.get("return_7d_pct") or 0) > 0)
            avg  = sum((r.get("return_7d_pct") or 0) for r in subset) / len(subset)
            return {
                "count":        len(subset),
                "win_rate_7d":  round(wins / len(subset) * 100, 1),
                "avg_return_7d": round(avg, 2),
            }

        # ── Signal type breakdown ─────────────────────────────────────────────
        signal_breakdown = []
        for stype in ("DCA_NOW", "WATCH"):
            subset = [r for r in rows if r["signal_type"] == stype]
            signal_breakdown.append({"label": stype, **_stats(subset)})

        # ── F&G bucket breakdown ──────────────────────────────────────────────
        fg_buckets = [
            ("<15  XFEAR",    None, 15),
            ("15–25  FEAR",   15,   25),
            ("25–40  CAUTIOUS", 25, 40),
            (">40  NEUTRAL+", 40, None),
        ]
        fg_breakdown = []
        for label, lo, hi in fg_buckets:
            subset = [
                r for r in rows
                if r.get("fg_at_signal") is not None
                and (lo is None or int(r["fg_at_signal"]) >= lo)
                and (hi is None or int(r["fg_at_signal"]) <  hi)
            ]
            fg_breakdown.append({"label": label, **_stats(subset)})

        # ── Token breakdown ───────────────────────────────────────────────────
        from utils.spot_accumulator import BASKET  # type: ignore
        token_breakdown = []
        for token in BASKET:
            sym    = token["symbol"]
            subset = [r for r in rows if r["symbol"] == sym]
            token_breakdown.append({"symbol": sym, **_stats(subset)})

        # ── Tuner thresholds ──────────────────────────────────────────────────
        tuner: dict | None = None
        try:
            with get_conn() as conn:
                row = conn.execute(
                    "SELECT value FROM kv_store WHERE key='spot_signal_thresholds'"
                ).fetchone()
            if row:
                tuner = json.loads(row[0])
        except Exception:
            pass

        # ── Recent signal history (last 30, newest first) ────────────────────
        recent: list[dict] = []
        try:
            with get_conn() as conn:
                conn.row_factory = _sqlite3.Row
                rrows = conn.execute("""
                    SELECT id, ts_utc, symbol, score, signal_type,
                           price_at_signal, h24_at_signal, h6_at_signal,
                           fg_at_signal, trend_at_signal, portfolio_gap,
                           return_7d_pct, return_30d_pct, status
                    FROM spot_signals
                    ORDER BY id DESC LIMIT 30
                """).fetchall()
            recent = [dict(r) for r in rrows]
        except Exception:
            pass

        return {
            "total":            total,
            "complete":         complete,
            "pending":          pending,
            "signal_breakdown": signal_breakdown,
            "fg_breakdown":     fg_breakdown,
            "token_breakdown":  token_breakdown,
            "tuner":            tuner,
            "recent_signals":   recent,
        }

    try:
        return await asyncio.to_thread(_run)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ── Spot Portfolio OS — Step 1: Position Thesis ───────────────────────────────

_CREATE_THESIS = (
    "CREATE TABLE IF NOT EXISTS spot_thesis ("
    "symbol TEXT PRIMARY KEY, "
    "thesis_text TEXT, "
    "target_pct REAL, "
    "invalidation_condition TEXT, "
    "status TEXT DEFAULT 'HOLD', "
    "updated_at TEXT"
    ")"
)


def _ensure_thesis_table(conn) -> None:
    conn.execute(_CREATE_THESIS)


@router.get("/thesis")
async def spot_thesis_get(_: str = Depends(get_current_user)):
    """Return all thesis rows keyed by symbol. Spot Portfolio OS Step 1."""
    _ensure_engine_path()
    try:
        import sqlite3 as _sqlite3
        from utils.db import get_conn  # type: ignore
        with get_conn() as conn:
            _ensure_thesis_table(conn)
            conn.row_factory = _sqlite3.Row
            rows = conn.execute(
                "SELECT symbol, thesis_text, target_pct, "
                "invalidation_condition, status, updated_at "
                "FROM spot_thesis"
            ).fetchall()
        return {"thesis": {r["symbol"]: dict(r) for r in rows}}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/thesis")
async def spot_thesis_post(body: dict, _: str = Depends(get_current_user)):
    """
    Upsert one thesis row. Spot Portfolio OS Step 1.
    Body: {symbol, thesis_text, target_pct, invalidation_condition, status}
    """
    _ensure_engine_path()
    symbol = str(body.get("symbol", "")).strip().upper()
    if not symbol:
        raise HTTPException(status_code=400, detail="symbol is required")

    thesis_text            = body.get("thesis_text") or None
    target_pct             = body.get("target_pct")
    invalidation_condition = body.get("invalidation_condition") or None
    status                 = str(body.get("status", "HOLD")).strip().upper()
    if status not in ("HOLD", "WATCH", "EXIT"):
        status = "HOLD"
    if target_pct is not None:
        try:
            target_pct = float(target_pct)
        except (TypeError, ValueError):
            target_pct = None

    try:
        from datetime import datetime, timezone
        from utils.db import get_conn  # type: ignore
        updated_at = datetime.now(timezone.utc).isoformat()
        with get_conn() as conn:
            _ensure_thesis_table(conn)
            conn.execute(
                "INSERT INTO spot_thesis "
                "(symbol, thesis_text, target_pct, invalidation_condition, status, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(symbol) DO UPDATE SET "
                "thesis_text=excluded.thesis_text, "
                "target_pct=excluded.target_pct, "
                "invalidation_condition=excluded.invalidation_condition, "
                "status=excluded.status, "
                "updated_at=excluded.updated_at",
                (symbol, thesis_text, target_pct, invalidation_condition, status, updated_at),
            )
        return {"ok": True, "symbol": symbol}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ── Spot Portfolio OS — Step 3: Add-Candidate Engine ─────────────────────────

@router.get("/add-plan")
async def spot_add_plan_ep(
    budget: float = Query(..., gt=0, description="Budget in USD"),
    _: str = Depends(get_current_user),
):
    """
    Rank basket tokens as accumulation candidates given a budget.

    Scoring (advisory only, no execution):
      rank_score = gap_weight * 0.45 + (signal_score / 10) * 0.35 + fear_weight * 0.20

    Eligibility rules:
      - Must have thesis target_pct set
      - Must not be >5% above target (already overweight)
      - Must have a current signal in kv_store

    Returns:
      candidates: ranked list with suggested_usd allocations
      excluded:   list with per-token exclusion reasons
      fg:         F&G used for scoring
      min_score:  tuner threshold applied
      budget:     echo of input
    """
    _ensure_engine_path()

    def _run() -> dict:
        import sqlite3 as _sqlite3
        from utils.db import get_conn        # type: ignore
        from utils.spot_accumulator import BASKET  # type: ignore

        # ── Load signal cache ─────────────────────────────────────────────────
        signals: dict = {}
        try:
            with get_conn() as conn:
                row = conn.execute(
                    "SELECT value FROM kv_store WHERE key='spot_current_signals'"
                ).fetchone()
            if row:
                cached  = json.loads(row[0])
                signals = cached.get("data") or {}
        except Exception:
            pass

        # ── Load tuner min_score ──────────────────────────────────────────────
        min_score: float = 3.0
        try:
            with get_conn() as conn:
                row = conn.execute(
                    "SELECT value FROM kv_store WHERE key='spot_signal_thresholds'"
                ).fetchone()
            if row:
                td        = json.loads(row[0])
                min_score = float(td.get("min_score", 3))
        except Exception:
            pass

        # ── Load thesis targets ───────────────────────────────────────────────
        thesis: dict = {}
        try:
            with get_conn() as conn:
                _ensure_thesis_table(conn)
                conn.row_factory = _sqlite3.Row
                for r in conn.execute(
                    "SELECT symbol, target_pct FROM spot_thesis"
                ).fetchall():
                    if r["target_pct"] is not None:
                        thesis[r["symbol"]] = float(r["target_pct"])
        except Exception:
            pass

        # ── Load holdings (sum invested for exposure%) ────────────────────────
        holdings: dict = {}   # symbol → total_invested
        sum_invested: float = 0.0
        try:
            with get_conn() as conn:
                conn.row_factory = _sqlite3.Row
                for r in conn.execute(
                    "SELECT symbol, total_invested, token_amount FROM spot_holdings"
                ).fetchall():
                    if (r["token_amount"] or 0) > 0:
                        inv = float(r["total_invested"] or 0)
                        holdings[r["symbol"]] = inv
                        sum_invested += inv
        except Exception:
            pass

        # ── Load last buy timestamp per symbol (cooldown awareness) ───────────
        # Counts all BUY entries regardless of dry_run — spacing discipline
        # applies equally in paper mode so the operator builds good habits.
        last_buys: dict = {}   # symbol → ISO timestamp string
        try:
            with get_conn() as conn:
                conn.row_factory = _sqlite3.Row
                for r in conn.execute(
                    "SELECT symbol, MAX(ts_utc) AS last_add_ts "
                    "FROM spot_buys WHERE side='BUY' GROUP BY symbol"
                ).fetchall():
                    if r["last_add_ts"]:
                        last_buys[r["symbol"]] = r["last_add_ts"]
        except Exception:
            pass

        # ── Derive F&G from any signal row ────────────────────────────────────
        fg: int | None = None
        for s in signals.values():
            if s.get("fg") is not None:
                try:
                    fg = int(s["fg"])
                    break
                except (TypeError, ValueError):
                    pass

        # ── Fear weight ───────────────────────────────────────────────────────
        if fg is not None and fg < 25:
            fear_weight = 1.0
        elif fg is not None and fg <= 40:
            fear_weight = 0.7
        else:
            fear_weight = 0.35

        # ── Build candidate / excluded lists ─────────────────────────────────
        basket_symbols = [b["symbol"] for b in BASKET]
        candidates: list[dict] = []
        excluded:   list[dict] = []

        for sym in basket_symbols:
            sig = signals.get(sym)
            target_pct = thesis.get(sym)

            # Compute current exposure %
            inv         = holdings.get(sym, 0.0)
            current_pct = (inv / sum_invested * 100) if sum_invested > 0 else 0.0

            # ── Eligibility checks ────────────────────────────────────────────
            if target_pct is None:
                excluded.append({"symbol": sym, "reason": "no target set in thesis"})
                continue

            gap = target_pct - current_pct  # positive → underweight

            if gap < -5:
                excluded.append({
                    "symbol": sym,
                    "reason": f"already {abs(gap):.1f}% above target ({current_pct:.1f}% vs {target_pct}% target)",
                })
                continue

            if sig is None:
                excluded.append({"symbol": sym, "reason": "no signal data available"})
                continue

            signal_score: float = float(sig.get("score") or 0)
            signal_type:  str   = str(sig.get("signal_type") or "HOLD")

            # gap_weight: normalised 0–1 from gap vs target (clamp to [0, 1])
            gap_weight = max(0.0, min(1.0, gap / max(target_pct, 1))) if target_pct else 0.0

            rank_score = (
                gap_weight       * 0.45
                + (signal_score / 10) * 0.35
                + fear_weight    * 0.20
            )

            # ── Cooldown awareness ────────────────────────────────────────────
            from datetime import datetime, timezone
            last_add_ts    = last_buys.get(sym)
            days_since_add: float | None = None
            cooldown_state: str = "ELIGIBLE"
            if last_add_ts:
                try:
                    ts = datetime.fromisoformat(
                        last_add_ts.replace("Z", "+00:00")
                    )
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    days_since_add = (
                        datetime.now(timezone.utc) - ts
                    ).total_seconds() / 86400.0
                    if days_since_add < 30:
                        cooldown_state = "COOLING_DOWN"
                except Exception:
                    pass

            candidates.append({
                "symbol":         sym,
                "current_pct":    round(current_pct, 2),
                "target_pct":     target_pct,
                "gap_pct":        round(gap, 2),
                "signal_type":    signal_type,
                "signal_score":   round(signal_score, 2),
                "rank_score":     round(rank_score, 4),
                "fg":             fg,
                "min_score":      min_score,
                "last_add_ts":    last_add_ts,
                "days_since_add": round(days_since_add, 1) if days_since_add is not None else None,
                "cooldown_state": cooldown_state,
                # suggested_usd computed after ranking (proportional to rank_score)
            })

        # ── Sort descending by rank_score ─────────────────────────────────────
        candidates.sort(key=lambda c: c["rank_score"], reverse=True)

        # ── Allocate budget proportionally to rank_score ──────────────────────
        total_rank = sum(c["rank_score"] for c in candidates)
        for c in candidates:
            if total_rank > 0:
                c["suggested_usd"] = round(budget * c["rank_score"] / total_rank, 2)
            else:
                c["suggested_usd"] = round(budget / len(candidates), 2) if candidates else 0.0

        # ── Minimum allocation floor ──────────────────────────────────────────
        # Prevents small budgets from spraying tiny amounts across many names.
        # Iteratively removes below-floor candidates and redistributes their
        # budget proportionally among remaining active candidates.
        # If all candidates fall below the floor, keeps the top N names where
        # N = max(1, floor(budget / FLOOR_USD)) and redistributes among them.
        FLOOR_USD: float = 75.0
        floor_skipped: list[dict] = []

        if candidates:
            active = list(candidates)  # sorted desc by rank_score

            while True:
                below = [c for c in active if c["suggested_usd"] < FLOOR_USD]
                above = [c for c in active if c["suggested_usd"] >= FLOOR_USD]

                if not below:
                    break  # all at or above floor — stable

                if not above:
                    # Every candidate is below the floor.
                    # Keep top N where each share >= FLOOR_USD.
                    remaining = sum(c["suggested_usd"] for c in active)
                    keep_n    = max(1, int(remaining // FLOOR_USD))
                    keep      = active[:keep_n]
                    skip      = active[keep_n:]
                    for c in skip:
                        floor_skipped.append({
                            "symbol": c["symbol"],
                            "reason": (
                                f"suggested ${c['suggested_usd']:.0f} below "
                                f"minimum add floor (${FLOOR_USD:.0f}); "
                                "budget concentrated into top-ranked names"
                            ),
                        })
                    # Redistribute remaining budget proportionally among keep
                    tr = sum(c["rank_score"] for c in keep) or 1.0
                    for c in keep:
                        c["suggested_usd"] = round(
                            remaining * c["rank_score"] / tr, 2
                        )
                    active = keep
                    break

                # Some above, some below — free up below-floor budgets
                freed = sum(c["suggested_usd"] for c in below)
                for c in below:
                    floor_skipped.append({
                        "symbol": c["symbol"],
                        "reason": (
                            f"suggested ${c['suggested_usd']:.0f} below "
                            f"minimum add floor (${FLOOR_USD:.0f})"
                        ),
                    })
                tr = sum(c["rank_score"] for c in above) or 1.0
                for c in above:
                    c["suggested_usd"] = round(
                        c["suggested_usd"] + freed * c["rank_score"] / tr, 2
                    )
                active = above
                # Loop once more to confirm no new violations (redistributed
                # amounts can only increase, so this converges immediately)

            candidates = active

        return {
            "budget":        budget,
            "fg":            fg,
            "fear_weight":   fear_weight,
            "min_score":     min_score,
            "floor_usd":     FLOOR_USD,
            "candidates":    candidates,
            "excluded":      excluded,
            "floor_skipped": floor_skipped,
        }

    try:
        return await asyncio.to_thread(_run)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ── Spot Portfolio OS — Step 4: Rebalance Tracker ────────────────────────────

@router.get("/rebalance-tracker")
async def spot_rebalance_tracker_ep(_: str = Depends(get_current_user)):
    """
    Capital build-out roadmap. Read-only planning surface.

    Computes an anchor-based implied target book size from currently held
    positions that have thesis targets configured.  For each configured token,
    returns target_dollars, current_dollars, and dollar_gap so the operator
    can see exactly how much capital remains to fully build the intended book.

    Anchor method (v1 — simple, explainable):
      For every configured + held token:
        implied_book_size = current_dollars / (target_pct / 100)
      Average those implied sizes → single implied_book_size.
      If nothing is held yet → implied_book_size = 0 (no anchor available).
    """
    _ensure_engine_path()

    def _run() -> dict:
        import sqlite3 as _sqlite3
        from utils.db import get_conn  # type: ignore

        # ── Load thesis targets ───────────────────────────────────────────────
        thesis: dict = {}
        try:
            with get_conn() as conn:
                _ensure_thesis_table(conn)
                conn.row_factory = _sqlite3.Row
                for r in conn.execute(
                    "SELECT symbol, target_pct, status FROM spot_thesis"
                ).fetchall():
                    if r["target_pct"] is not None:
                        thesis[r["symbol"]] = {
                            "target_pct": float(r["target_pct"]),
                            "status":     r["status"] or "WATCH",
                        }
        except Exception:
            pass

        # ── Load current holdings ─────────────────────────────────────────────
        holdings: dict = {}   # symbol → total_invested
        sum_invested: float = 0.0
        try:
            with get_conn() as conn:
                conn.row_factory = _sqlite3.Row
                for r in conn.execute(
                    "SELECT symbol, total_invested, token_amount FROM spot_holdings"
                ).fetchall():
                    if (r["token_amount"] or 0) > 0:
                        inv = float(r["total_invested"] or 0)
                        holdings[r["symbol"]] = inv
                        sum_invested += inv
        except Exception:
            pass

        # ── Anchor inference ──────────────────────────────────────────────────
        # For each configured token that is currently held, infer what the
        # full target book would be if this position were exactly at target.
        implied_sizes: list[float] = []
        anchors_used:  list[str]   = []
        for sym, t in thesis.items():
            inv = holdings.get(sym, 0.0)
            if inv > 0 and t["target_pct"] > 0:
                implied_sizes.append(inv / (t["target_pct"] / 100))
                anchors_used.append(sym)

        implied_book_size: float = (
            sum(implied_sizes) / len(implied_sizes) if implied_sizes else 0.0
        )

        # ── Per-token breakdown ───────────────────────────────────────────────
        tokens: list[dict] = []
        total_gap_positive: float = 0.0   # sum of BUILD NEEDED gaps only

        for sym, t in sorted(thesis.items(), key=lambda x: (-x[1]["target_pct"], x[0])):
            target_dollars  = implied_book_size * (t["target_pct"] / 100)
            current_dollars = holdings.get(sym, 0.0)
            dollar_gap      = target_dollars - current_dollars   # + = need to add

            # State: within $10 tolerance is AT TARGET
            if dollar_gap > 10:
                state = "BUILD NEEDED"
            elif dollar_gap < -10:
                state = "ABOVE TARGET"
            else:
                state = "AT TARGET"

            if dollar_gap > 10:
                total_gap_positive += dollar_gap

            tokens.append({
                "symbol":          sym,
                "target_pct":      t["target_pct"],
                "status":          t["status"],
                "target_dollars":  round(target_dollars,  2),
                "current_dollars": round(current_dollars, 2),
                "dollar_gap":      round(dollar_gap,      2),
                "state":           state,
                "is_anchor":       sym in anchors_used,
            })

        return {
            "implied_book_size":  round(implied_book_size,     2),
            "current_deployed":   round(sum_invested,          2),
            "additional_needed":  round(total_gap_positive,    2),
            "anchor_count":       len(anchors_used),
            "anchors_used":       anchors_used,
            "tokens":             tokens,
        }

    try:
        return await asyncio.to_thread(_run)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ── Roadmap 2: Spot lane operating summary ────────────────────────────────────

def _spot_lane_operating_summary(
    signal_confidence: str,
    outcomes_complete: int,
    actionable_count: int,
    poor_count: int,
    basket_size: int,
    holdings_count: int,
    fg: "int | None",
) -> dict:
    """
    Compact operating-model summary for the Spot lane.

    Operating states
    ─────────────────
    PROOF_BUILDING   — signal engine still accumulating learning data
    ACCUMULATING     — actionable setups exist, conditions supportive
    FEAR_MANAGED     — actionable setups exist but market fear limits adds
    MONITORING       — watching; no actionable posture yet
    BLOCKED          — hard blocker prevents any action

    Checklist (4 items)
    ────────────────────
    signal_proof_built         — outcomes_complete >= 10
    confidence_calibrated      — signal_confidence != "pending"
    actionable_posture_exists  — actionable_count > 0
    fear_conditions_supportive — fg is None or fg >= 25
    """
    # ── checklist ──────────────────────────────────────────────────────────────
    chk_proof        = outcomes_complete >= 10
    chk_confidence   = signal_confidence not in ("pending", None, "")
    chk_actionable   = actionable_count > 0
    chk_fear         = fg is None or fg >= 25

    checklist = [
        {
            "key":    "signal_proof_built",
            "label":  "Signal proof built (≥10 outcomes)",
            "passed": chk_proof,
            "note":   (
                f"{outcomes_complete}/10 outcomes complete"
                if not chk_proof
                else f"{outcomes_complete} outcomes complete"
            ),
        },
        {
            "key":    "confidence_calibrated",
            "label":  "Signal confidence calibrated",
            "passed": chk_confidence,
            "note":   (
                "Tuner confidence still pending"
                if not chk_confidence
                else f"Confidence: {signal_confidence}"
            ),
        },
        {
            "key":    "actionable_posture_exists",
            "label":  "Actionable entry posture exists",
            "passed": chk_actionable,
            "note":   (
                "No bucket win rate ≥50% yet"
                if not chk_actionable
                else f"{actionable_count} token(s) at PRIME_ENTRY/ACCUMULATE"
            ),
        },
        {
            "key":    "fear_conditions_supportive",
            "label":  "Market conditions not extreme fear",
            "passed": chk_fear,
            "note":   (
                f"F&G={fg} — extreme fear conditions"
                if (fg is not None and fg < 15)
                else (
                    f"F&G={fg} — fear conditions"
                    if (fg is not None and not chk_fear)
                    else f"F&G={fg}" if fg is not None else "F&G unavailable"
                )
            ),
        },
    ]

    passed = sum(1 for c in checklist if c["passed"])
    total  = len(checklist)

    # ── operating state ────────────────────────────────────────────────────────
    if not chk_proof or not chk_confidence:
        lane_operating_state = "PROOF_BUILDING"
        lane_operating_note  = (
            f"Signal engine building proof — {outcomes_complete} of 10 outcomes "
            "needed before confidence calibrates."
        )
    elif chk_actionable and chk_fear:
        lane_operating_state = "ACCUMULATING"
        lane_operating_note  = (
            f"{actionable_count} token(s) show actionable posture "
            f"(PRIME_ENTRY/ACCUMULATE). Conditions supportive."
        )
    elif chk_actionable and not chk_fear:
        lane_operating_state = "FEAR_MANAGED"
        lane_operating_note  = (
            f"{actionable_count} token(s) actionable but F&G={fg} signals fear — "
            "adds should be reduced or paused."
        )
    elif fg is not None and fg < 15:
        lane_operating_state = "FEAR_MANAGED"
        lane_operating_note  = (
            f"Extreme fear (F&G={fg}). No actionable posture — "
            "monitoring only until conditions improve."
        )
    else:
        lane_operating_state = "MONITORING"
        lane_operating_note  = (
            "No actionable entry posture yet. "
            "Watching for PRIME_ENTRY or ACCUMULATE signals."
        )

    # ── next unlock ────────────────────────────────────────────────────────────
    if not chk_proof:
        next_unlock_state = "SIGNAL_PROOF_10"
        next_unlock_note  = (
            f"Reach 10 completed signal outcomes ({outcomes_complete} so far) "
            "to unlock tuner calibration."
        )
    elif not chk_confidence:
        next_unlock_state = "TUNER_CALIBRATION"
        next_unlock_note  = (
            "10+ outcomes recorded; tuner needs a bit more data "
            "to produce a calibrated confidence rating."
        )
    elif not chk_fear:
        next_unlock_state = "FEAR_RESOLUTION"
        next_unlock_note  = (
            f"F&G={fg}. Wait for fear gauge to recover (≥25) "
            "before resuming normal add cadence."
        )
    elif not chk_actionable:
        next_unlock_state = "POSTURE_IMPROVEMENT"
        next_unlock_note  = (
            "Need at least one basket token to reach ≥50% bucket win rate "
            "to unlock ACCUMULATING state."
        )
    else:
        next_unlock_state = "MAINTAIN_BASKET"
        next_unlock_note  = (
            "Lane is active. Maintain basket allocation and add on signal."
        )

    # ── primary constraint ─────────────────────────────────────────────────────
    if not chk_proof:
        primary_constraint_key  = "SIGNAL_PROOF_PENDING"
        primary_constraint_note = (
            f"Only {outcomes_complete} of 10 signal outcomes complete — "
            "tuner cannot calibrate without sufficient proof data."
        )
    elif not chk_confidence:
        primary_constraint_key  = "CONFIDENCE_PENDING"
        primary_constraint_note = (
            "Signal confidence still pending — add cadence should stay conservative "
            "until the tuner produces a calibrated threshold."
        )
    elif fg is not None and fg < 15:
        primary_constraint_key  = "EXTREME_FEAR_MARKET"
        primary_constraint_note = (
            f"F&G={fg} (extreme fear) — market conditions contraindicate adds; "
            "monitor only."
        )
    elif fg is not None and fg < 25:
        primary_constraint_key  = "FEAR_CONDITIONS"
        primary_constraint_note = (
            f"F&G={fg} (fear) — reduces add confidence; "
            "prefer smaller size or waiting for recovery."
        )
    elif not chk_actionable:
        primary_constraint_key  = "NO_ACTIONABLE_POSTURE"
        primary_constraint_note = (
            "No basket token has a bucket win rate ≥50% — "
            "posture is INSUFFICIENT_DATA or HOLD across the basket."
        )
    else:
        primary_constraint_key  = "NONE"
        primary_constraint_note = "No binding constraint — lane may accumulate normally."

    # ── alignment ──────────────────────────────────────────────────────────────
    if lane_operating_state == "ACCUMULATING":
        operating_alignment = "STACKED" if (fg is not None and fg >= 40) else "BACKED"
    elif lane_operating_state == "FEAR_MANAGED":
        operating_alignment = "TENTATIVE"
    elif lane_operating_state == "MONITORING":
        operating_alignment = "QUIET"
    elif lane_operating_state == "PROOF_BUILDING":
        operating_alignment = "QUIET"
    else:
        operating_alignment = "ADVERSE"

    # Lane momentum — directional signal for cross-lane consumers
    if lane_operating_state == "ACCUMULATING":
        lane_momentum = "ADVANCING"
    elif lane_operating_state == "FEAR_MANAGED":
        lane_momentum = "BUILDING"   # has proof/signal, constrained by fear
    elif lane_operating_state == "PROOF_BUILDING":
        lane_momentum = "BUILDING"   # proof accumulating — directionally positive
    elif lane_operating_state == "MONITORING" and passed == 0:
        lane_momentum = "STALLING"
    else:
        lane_momentum = "BUILDING"

    # Unlock horizon — how many checks remain
    _spot_outstanding = total - passed
    if next_unlock_state == "MAINTAIN_BASKET" or _spot_outstanding <= 0:
        unlock_horizon = "NONE"
    elif _spot_outstanding == 1:
        unlock_horizon = "NEAR"
    elif _spot_outstanding <= 3:
        unlock_horizon = "MEDIUM"
    else:
        unlock_horizon = "FAR"

    return {
        "lane_operating_state":     lane_operating_state,
        "lane_operating_note":      lane_operating_note,
        "next_unlock_state":        next_unlock_state,
        "next_unlock_note":         next_unlock_note,
        "primary_constraint_key":   primary_constraint_key,
        "primary_constraint_note":  primary_constraint_note,
        "passed_checks":            passed,
        "total_checks":             total,
        "checklist":                checklist,
        "operating_alignment":      operating_alignment,
        "lane_momentum":            lane_momentum,
        "unlock_horizon":           unlock_horizon,
    }


# ── Patch 304: Spot Posture — outcome-conditioned entry verdict ───────────────

@router.get("/posture")
async def spot_posture_ep(_: str = Depends(get_current_user)):
    """
    Per-token outcome-conditioned entry posture. Patch 304.

    For each basket token, cross-references the current signal tier with the
    historical win rate for that signal_type × F&G_bucket combination from
    completed spot_signals outcomes.

    Posture labels (deterministic):
      PRIME_ENTRY       — bucket win rate ≥ 65 % AND n ≥ 10
      ACCUMULATE        — bucket win rate ≥ 50 % AND n ≥ 10
      HOLD              — bucket win rate ≥ 35 % AND n ≥ 10, or signal is HOLD/AVOID
      POOR_CONDITIONS   — bucket win rate < 35 % AND n ≥ 10
      INSUFFICIENT_DATA — bucket n < 10 (not enough outcomes to judge)
      F&G_FEED_DOWN     — current F&G is unavailable; bucket cannot be determined

    contradiction = True when posture is POOR_CONDITIONS but raw signal is
    DCA_NOW or WATCH (system says act, history says don't).
    """
    _ensure_engine_path()

    def _run():
        import sqlite3 as _sqlite3
        from utils.db import get_conn       # type: ignore
        from utils.spot_accumulator import BASKET  # type: ignore

        # ── 1. Current signals from kv_store ─────────────────────────────────
        current_signals: dict = {}
        try:
            with get_conn() as conn:
                row = conn.execute(
                    "SELECT value FROM kv_store WHERE key='spot_current_signals'"
                ).fetchone()
            if row:
                cached = json.loads(row[0])
                current_signals = cached.get("data") or {}
        except Exception:
            pass

        # ── 2. Historical bucket win rates from completed outcomes ────────────
        #  bucket key: (signal_type, fg_bucket_label)
        #  fg_bucket_label: "XFEAR" (<15), "FEAR" (15-25), "CAUTIOUS" (25-40),
        #                   "NEUTRAL+" (≥40), "UNKNOWN" (fg_at_signal IS NULL)
        def _fg_bucket(fg: int | None) -> str:
            if fg is None:
                return "UNKNOWN"
            if fg < 15:
                return "XFEAR"
            if fg < 25:
                return "FEAR"
            if fg < 40:
                return "CAUTIOUS"
            return "NEUTRAL+"

        bucket_stats: dict[tuple, dict] = {}
        try:
            with get_conn() as conn:
                conn.row_factory = _sqlite3.Row
                rows = conn.execute("""
                    SELECT signal_type, fg_at_signal, return_7d_pct
                    FROM   spot_signals
                    WHERE  status = 'COMPLETE' AND return_7d_pct IS NOT NULL
                """).fetchall()
            for r in rows:
                key = (r["signal_type"], _fg_bucket(r["fg_at_signal"]))
                if key not in bucket_stats:
                    bucket_stats[key] = {"n": 0, "wins": 0, "sum_ret": 0.0}
                bucket_stats[key]["n"]       += 1
                bucket_stats[key]["wins"]    += 1 if (r["return_7d_pct"] or 0) > 0 else 0
                bucket_stats[key]["sum_ret"] += r["return_7d_pct"] or 0.0
        except Exception:
            pass

        # Convert to win_rate / avg_return
        bucket_perf: dict[tuple, dict] = {}
        for key, s in bucket_stats.items():
            n = s["n"]
            bucket_perf[key] = {
                "n":          n,
                "win_rate":   round(s["wins"] / n * 100, 1) if n else None,
                "avg_return": round(s["sum_ret"] / n, 2)    if n else None,
            }

        # ── Roadmap 2: lane operating inputs ──────────────────────────────────
        outcomes_complete: int = sum(s["n"] for s in bucket_stats.values())
        signal_confidence: str = "pending"
        try:
            with get_conn() as conn:
                _tr = conn.execute(
                    "SELECT value FROM kv_store WHERE key='spot_signal_thresholds'"
                ).fetchone()
            if _tr:
                _td = json.loads(_tr[0])
                signal_confidence = str(_td.get("confidence") or "pending")
        except Exception:
            pass

        # ── 3. Posture rules ──────────────────────────────────────────────────
        _MIN_N = 10  # minimum outcomes before asserting confident posture

        def _posture_for(signal_type: str, fg: int | None) -> tuple[str, str, bool]:
            """Return (posture_label, reason, contradiction)."""
            # F&G feed down — cannot form bucket
            if fg is None:
                if signal_type in ("DCA_NOW", "WATCH"):
                    return (
                        "F&G_FEED_DOWN",
                        f"F\u0026G unavailable \u2014 signal is {signal_type} but bucket win rate unknown",
                        False,
                    )
                return (
                    "F&G_FEED_DOWN",
                    "F\u0026G unavailable \u2014 posture suspended",
                    False,
                )

            fg_label = _fg_bucket(fg)
            key      = (signal_type, fg_label)
            perf     = bucket_perf.get(key)

            # HOLD or AVOID signals — no bucket win rate needed
            if signal_type in ("HOLD", "AVOID"):
                wr_str = ""
                if perf and perf["n"] >= _MIN_N:
                    wr_str = f" (bucket {fg_label}: {perf['win_rate']}% WR, n={perf['n']})"
                return (
                    "HOLD",
                    f"signal {signal_type} at F\u0026G={fg} ({fg_label}){wr_str}",
                    False,
                )

            # DCA_NOW or WATCH — use bucket win rate
            if perf is None or perf["n"] < _MIN_N:
                n_seen = perf["n"] if perf else 0
                return (
                    "INSUFFICIENT_DATA",
                    f"signal {signal_type} \u2014 only {n_seen} outcomes in {fg_label} bucket (need {_MIN_N})",
                    False,
                )

            wr   = perf["n"] and perf["win_rate"]
            n    = perf["n"]
            ar   = perf["avg_return"]
            desc = f"F\u0026G={fg} ({fg_label}) \u2014 {wr}% win rate, avg {ar:+.1f}% (n={n})"

            if wr >= 65:
                label = "PRIME_ENTRY"
            elif wr >= 50:
                label = "ACCUMULATE"
            elif wr >= 35:
                label = "HOLD"
            else:
                label = "POOR_CONDITIONS"

            contradiction = label == "POOR_CONDITIONS" and signal_type in ("DCA_NOW", "WATCH")

            if contradiction:
                reason = (
                    f"signal {signal_type} but {desc} \u2014 "
                    f"history says wait for better conditions"
                )
            else:
                reason = f"signal {signal_type} \u2014 {desc}"

            return (label, reason, contradiction)

        # ── 4. Build per-token posture ────────────────────────────────────────
        result = []
        for token in BASKET:
            sym  = token["symbol"]
            sig  = current_signals.get(sym) or {}
            stype = sig.get("signal_type") or "HOLD"
            fg    = sig.get("fg")          # may be None if feed is down
            score = sig.get("score")

            posture, reason, contradiction = _posture_for(stype, fg)

            # Pull bucket stats for transparency
            fg_label = _fg_bucket(fg) if fg is not None else None
            bkey     = (stype, fg_label) if fg_label else None
            bperf    = bucket_perf.get(bkey) if bkey else None

            result.append({
                "symbol":          sym,
                "signal_type":     stype,
                "score":           score,
                "fg":              fg,
                "fg_bucket":       fg_label,
                "posture":         posture,
                "reason":          reason,
                "contradiction":   contradiction,
                "bucket_n":        bperf["n"]          if bperf else None,
                "bucket_win_rate": bperf["win_rate"]   if bperf else None,
                "bucket_avg_ret":  bperf["avg_return"] if bperf else None,
            })

        # ── Roadmap 2: derive lane-level inputs from built result ──────────────
        _actionable_count = sum(
            1 for r in result if r["posture"] in ("PRIME_ENTRY", "ACCUMULATE")
        )
        _poor_count = sum(
            1 for r in result if r["posture"] == "POOR_CONDITIONS"
        )
        # fg from any row that has one
        _lane_fg: "int | None" = None
        for r in result:
            if r.get("fg") is not None:
                try:
                    _lane_fg = int(r["fg"])
                    break
                except Exception:
                    pass
        # holdings_count from portfolio state (best-effort; doesn't block)
        _holdings_count = 0
        try:
            from utils.spot_accumulator import get_portfolio_state as _gps  # type: ignore
            _ps = _gps() or {}
            _holdings_count = int(_ps.get("holdings_count") or 0)
        except Exception:
            pass

        _operating_summary = _spot_lane_operating_summary(
            signal_confidence = signal_confidence,
            outcomes_complete = outcomes_complete,
            actionable_count  = _actionable_count,
            poor_count        = _poor_count,
            basket_size       = len(result),
            holdings_count    = _holdings_count,
            fg                = _lane_fg,
        )

        return {
            "postures":         result,
            "operating_summary": _operating_summary,
            "generated_at":     __import__("datetime").datetime.utcnow().isoformat() + "Z",
        }

    try:
        return await asyncio.to_thread(_run)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/sell/{symbol}")
async def spot_sell_ep(
    symbol: str,
    pct: float = Query(default=100.0, ge=1, le=100, description="Percentage to sell (1–100)"),
    _: str = Depends(get_current_user),
):
    """Sell pct% of a basket token holding (default 100 = full sell)."""
    _ensure_engine_path()
    symbol = symbol.upper()

    try:
        from utils.spot_accumulator import BASKET, sell_spot  # type: ignore
        # Look up mint from basket
        mint = next((b["mint"] for b in BASKET if b["symbol"] == symbol), None)
        if not mint:
            raise HTTPException(status_code=404, detail=f"{symbol} not in basket")

        result = await asyncio.to_thread(sell_spot, symbol, mint, pct)
        if not result.get("success"):
            raise HTTPException(status_code=422, detail=result.get("error", "sell failed"))
        return result
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
