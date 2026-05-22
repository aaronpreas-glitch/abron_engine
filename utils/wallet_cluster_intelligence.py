from __future__ import annotations

import json
import os
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

from utils.db import get_conn


EARLY_BUYER_LIMIT = int(os.getenv("WALLET_CLUSTER_EARLY_BUYER_LIMIT", "80"))
EARLY_WINDOW_HOURS = int(os.getenv("WALLET_CLUSTER_EARLY_WINDOW_HOURS", "24"))
RECENT_WINDOW_HOURS = int(os.getenv("WALLET_CLUSTER_RECENT_WINDOW_HOURS", "6"))
MAX_FUNDING_WALLETS = int(os.getenv("WALLET_CLUSTER_MAX_FUNDING_WALLETS", "8"))
HELIUS_TX_LIMIT = int(os.getenv("WALLET_CLUSTER_HELIUS_TX_LIMIT", "20"))
EXTERNAL_FUNDING_ENABLED = os.getenv("WALLET_CLUSTER_EXTERNAL_FUNDING", "true").lower() in {"1", "true", "yes"}
REPEAT_MEMORY_LOOKBACK_DAYS = int(os.getenv("WALLET_CLUSTER_MEMORY_LOOKBACK_DAYS", "45"))
MEMORY_REFRESH_EVENT_LIMIT = int(os.getenv("WALLET_CLUSTER_MEMORY_REFRESH_EVENT_LIMIT", "1500"))
DISTRIBUTION_ALERT_THRESHOLD = float(os.getenv("WALLET_CLUSTER_DISTRIBUTION_ALERT_THRESHOLD", "65"))
DISTRIBUTION_ALERT_COOLDOWN_MINUTES = int(os.getenv("WALLET_CLUSTER_ALERT_COOLDOWN_MINUTES", "60"))
ATTRIBUTION_MIN_AGE_HOURS = float(os.getenv("WALLET_CLUSTER_ATTRIBUTION_MIN_AGE_HOURS", "1"))
ATTRIBUTION_REFRESH_LIMIT = int(os.getenv("WALLET_CLUSTER_ATTRIBUTION_REFRESH_LIMIT", "250"))
TRUSTED_CLUSTER_ALERT_THRESHOLD = float(os.getenv("WALLET_CLUSTER_TRUSTED_ALERT_THRESHOLD", "54"))

STABLE_OR_BASE_SYMBOLS = {"SOL", "WSOL", "USDC", "USDT", "USDS", "DAI", "USD1", "ETH", "WETH", "BTC", "WBTC"}


SCHEMA = """
CREATE TABLE IF NOT EXISTS mint_wallet_cluster_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mint TEXT NOT NULL,
    symbol TEXT,
    ts_utc TEXT NOT NULL,
    early_buyer_count INTEGER DEFAULT 0,
    fresh_wallet_count INTEGER DEFAULT 0,
    repeat_wallet_count INTEGER DEFAULT 0,
    common_funder_count INTEGER DEFAULT 0,
    linked_wallet_count INTEGER DEFAULT 0,
    early_cluster_score REAL DEFAULT 0,
    fresh_wallet_score REAL DEFAULT 0,
    common_funding_score REAL DEFAULT 0,
    distribution_risk_score REAL DEFAULT 0,
    accumulation_score REAL DEFAULT 0,
    insider_like_score REAL DEFAULT 0,
    cluster_label TEXT,
    accumulation_state TEXT,
    confidence_score REAL DEFAULT 0,
    repeat_operator_count INTEGER DEFAULT 0,
    repeat_operator_score REAL DEFAULT 0,
    wallet_trust_score REAL DEFAULT 50,
    wallet_trust_label TEXT,
    reasons_json TEXT,
    warnings_json TEXT,
    top_wallets_json TEXT,
    top_repeat_wallets_json TEXT,
    funders_json TEXT,
    inputs_json TEXT
)
"""

EARLY_SCHEMA = """
CREATE TABLE IF NOT EXISTS mint_early_wallet_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mint TEXT NOT NULL,
    symbol TEXT,
    wallet_address TEXT NOT NULL,
    first_seen_utc TEXT NOT NULL,
    last_seen_utc TEXT NOT NULL,
    buy_count INTEGER DEFAULT 0,
    sell_count INTEGER DEFAULT 0,
    buy_volume_usd REAL DEFAULT 0,
    sell_volume_usd REAL DEFAULT 0,
    first_tx_hash TEXT,
    source TEXT,
    metadata_json TEXT
)
"""

FUNDING_SCHEMA = """
CREATE TABLE IF NOT EXISTS wallet_funding_edges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    wallet_address TEXT NOT NULL,
    funder_address TEXT NOT NULL,
    observed_at_utc TEXT NOT NULL,
    source TEXT,
    confidence_score REAL DEFAULT 0,
    tx_hash TEXT,
    amount_sol REAL DEFAULT 0,
    metadata_json TEXT
)
"""

MEMORY_SCHEMA = """
CREATE TABLE IF NOT EXISTS wallet_cluster_memory (
    wallet_address TEXT PRIMARY KEY,
    first_seen_utc TEXT,
    last_seen_utc TEXT,
    early_mint_count INTEGER DEFAULT 0,
    repeat_runner_count INTEGER DEFAULT 0,
    distribution_mint_count INTEGER DEFAULT 0,
    avg_max_return_pct REAL DEFAULT 0,
    best_max_return_pct REAL DEFAULT 0,
    memory_score REAL DEFAULT 0,
    memory_label TEXT,
    trust_score REAL DEFAULT 50,
    trust_label TEXT,
    attributed_signal_count INTEGER DEFAULT 0,
    attributed_runner_count INTEGER DEFAULT 0,
    attributed_bad_count INTEGER DEFAULT 0,
    attributed_distribution_hit_count INTEGER DEFAULT 0,
    updated_at_utc TEXT NOT NULL,
    examples_json TEXT,
    metadata_json TEXT
)
"""

ATTRIBUTION_SCHEMA = """
CREATE TABLE IF NOT EXISTS wallet_cluster_outcomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id INTEGER NOT NULL UNIQUE,
    mint TEXT NOT NULL,
    symbol TEXT,
    snapshot_ts_utc TEXT NOT NULL,
    evaluated_at_utc TEXT NOT NULL,
    age_hours REAL DEFAULT 0,
    return_1h_pct REAL DEFAULT 0,
    return_4h_pct REAL DEFAULT 0,
    return_24h_pct REAL DEFAULT 0,
    max_return_pct REAL DEFAULT 0,
    drawdown_from_max_pct REAL DEFAULT 0,
    outcome_label TEXT,
    outcome_score REAL DEFAULT 0,
    cluster_label TEXT,
    accumulation_state TEXT,
    repeat_operator_count INTEGER DEFAULT 0,
    repeat_operator_score REAL DEFAULT 0,
    distribution_risk_score REAL DEFAULT 0,
    accumulation_score REAL DEFAULT 0,
    insider_like_score REAL DEFAULT 0,
    reasons_json TEXT,
    inputs_json TEXT
)
"""

WALLET_ATTRIBUTION_SCHEMA = """
CREATE TABLE IF NOT EXISTS wallet_cluster_wallet_outcomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    wallet_address TEXT NOT NULL,
    snapshot_id INTEGER NOT NULL,
    mint TEXT NOT NULL,
    symbol TEXT,
    snapshot_ts_utc TEXT NOT NULL,
    evaluated_at_utc TEXT NOT NULL,
    max_return_pct REAL DEFAULT 0,
    outcome_label TEXT,
    distribution_risk_score REAL DEFAULT 0,
    repeat_operator_score REAL DEFAULT 0,
    wallet_memory_score_at_signal REAL DEFAULT 0,
    UNIQUE(wallet_address, snapshot_id)
)
"""

ALERT_SCHEMA = """
CREATE TABLE IF NOT EXISTS wallet_cluster_alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mint TEXT NOT NULL,
    symbol TEXT,
    ts_utc TEXT NOT NULL,
    alert_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    score REAL DEFAULT 0,
    headline TEXT,
    detail TEXT,
    cluster_label TEXT,
    accumulation_state TEXT,
    reasons_json TEXT,
    snapshot_json TEXT
)
"""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None = None) -> str:
    return (dt or _utc_now()).astimezone(timezone.utc).isoformat()


def _f(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, float(value)))


def _json_load(raw: Any, default):
    try:
        return json.loads(str(raw or ""))
    except Exception:
        return default


def _parse_dt(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _ensure_column(conn, table: str, column: str, definition: str) -> None:
    try:
        cols = {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
    except Exception:
        pass


def _ensure_schema(conn) -> None:
    conn.execute(SCHEMA)
    conn.execute(EARLY_SCHEMA)
    conn.execute(FUNDING_SCHEMA)
    conn.execute(MEMORY_SCHEMA)
    conn.execute(ALERT_SCHEMA)
    conn.execute(ATTRIBUTION_SCHEMA)
    conn.execute(WALLET_ATTRIBUTION_SCHEMA)
    _ensure_column(conn, "mint_wallet_cluster_snapshots", "repeat_operator_count", "INTEGER DEFAULT 0")
    _ensure_column(conn, "mint_wallet_cluster_snapshots", "repeat_operator_score", "REAL DEFAULT 0")
    _ensure_column(conn, "mint_wallet_cluster_snapshots", "wallet_trust_score", "REAL DEFAULT 50")
    _ensure_column(conn, "mint_wallet_cluster_snapshots", "wallet_trust_label", "TEXT")
    _ensure_column(conn, "mint_wallet_cluster_snapshots", "top_repeat_wallets_json", "TEXT")
    for column, definition in (
        ("trust_score", "REAL DEFAULT 50"),
        ("trust_label", "TEXT"),
        ("attributed_signal_count", "INTEGER DEFAULT 0"),
        ("attributed_runner_count", "INTEGER DEFAULT 0"),
        ("attributed_bad_count", "INTEGER DEFAULT 0"),
        ("attributed_distribution_hit_count", "INTEGER DEFAULT 0"),
    ):
        _ensure_column(conn, "wallet_cluster_memory", column, definition)
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_wallet_cluster_mint_ts
        ON mint_wallet_cluster_snapshots(mint, ts_utc)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_early_wallet_events_mint_first
        ON mint_early_wallet_events(mint, first_seen_utc)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_early_wallet_events_wallet_first
        ON mint_early_wallet_events(wallet_address, first_seen_utc)
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_early_wallet_events_unique
        ON mint_early_wallet_events(mint, wallet_address)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_funding_edges_wallet
        ON wallet_funding_edges(wallet_address, observed_at_utc)
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_funding_edges_unique
        ON wallet_funding_edges(wallet_address, funder_address, tx_hash)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_wallet_cluster_memory_score
        ON wallet_cluster_memory(memory_score DESC, last_seen_utc DESC)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_wallet_cluster_alerts_mint_ts
        ON wallet_cluster_alerts(mint, ts_utc)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_wallet_cluster_alerts_type_ts
        ON wallet_cluster_alerts(alert_type, ts_utc)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_wallet_cluster_outcomes_mint
        ON wallet_cluster_outcomes(mint, evaluated_at_utc)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_wallet_cluster_wallet_outcomes_wallet
        ON wallet_cluster_wallet_outcomes(wallet_address, evaluated_at_utc)
        """
    )


def _coerce_candidates(candidates: list[dict] | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in list(candidates or []):
        data = dict(item or {})
        mint = str(data.get("mint") or data.get("address") or data.get("token_address") or "").strip()
        if not mint or mint in seen:
            continue
        seen.add(mint)
        out.append({"mint": mint, "symbol": str(data.get("symbol") or "").upper() or None, **data})
    return out


def _helius_api_key() -> str:
    return str(os.getenv("HELIUS_API_KEY") or "").strip()


def _provider_allowed(provider: str, lane: str) -> bool:
    try:
        from utils.provider_budget import provider_budget_allow  # type: ignore

        return bool(provider_budget_allow(provider, lane=lane, reserve=True).get("allowed"))
    except Exception:
        return True


def _fetch_wallet_funders(wallet_address: str, *, before_ts: datetime | None = None) -> list[dict[str, Any]]:
    api_key = _helius_api_key()
    if not api_key or not wallet_address or not EXTERNAL_FUNDING_ENABLED:
        return []
    if not _provider_allowed("helius", "wallet_cluster_funding"):
        return []
    try:
        response = requests.get(
            f"https://api.helius.xyz/v0/addresses/{wallet_address}/transactions",
            params={"api-key": api_key, "limit": max(5, min(int(HELIUS_TX_LIMIT), 100))},
            headers={"User-Agent": "memecoin-engine/wallet-cluster"},
            timeout=12,
        )
        if response.status_code != 200:
            return []
        payload = response.json()
        if not isinstance(payload, list):
            return []
    except Exception:
        return []

    edges: list[dict[str, Any]] = []
    before_unix = int(before_ts.timestamp()) if before_ts else None
    for tx in payload:
        if not isinstance(tx, dict):
            continue
        tx_ts = int(tx.get("timestamp") or 0)
        if before_unix and tx_ts > before_unix:
            continue
        signature = str(tx.get("signature") or "").strip()
        for transfer in list(tx.get("nativeTransfers") or []):
            if not isinstance(transfer, dict):
                continue
            to_user = str(transfer.get("toUserAccount") or "").strip()
            from_user = str(transfer.get("fromUserAccount") or "").strip()
            if to_user != wallet_address or not from_user or from_user == wallet_address:
                continue
            amount_sol = _f(transfer.get("amount")) / 1_000_000_000.0
            if amount_sol <= 0:
                continue
            confidence = 70.0 if amount_sol >= 1.0 else 55.0
            edges.append(
                {
                    "wallet_address": wallet_address,
                    "funder_address": from_user,
                    "observed_at_utc": _iso(datetime.fromtimestamp(tx_ts, tz=timezone.utc)) if tx_ts else _iso(),
                    "source": "helius_native_transfer",
                    "confidence_score": confidence,
                    "tx_hash": signature,
                    "amount_sol": round(amount_sol, 6),
                    "metadata": {"description": str(tx.get("description") or "")[:180]},
                }
            )
    return edges[:3]


def _record_funding_edges(edges: list[dict[str, Any]]) -> int:
    if not edges:
        return 0
    written = 0
    try:
        with get_conn() as conn:
            _ensure_schema(conn)
            for edge in edges:
                wallet = str(edge.get("wallet_address") or "").strip()
                funder = str(edge.get("funder_address") or "").strip()
                if not wallet or not funder:
                    continue
                conn.execute(
                    """
                    INSERT OR IGNORE INTO wallet_funding_edges
                    (wallet_address, funder_address, observed_at_utc, source, confidence_score,
                     tx_hash, amount_sol, metadata_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        wallet,
                        funder,
                        str(edge.get("observed_at_utc") or _iso()),
                        str(edge.get("source") or "") or None,
                        _f(edge.get("confidence_score")),
                        str(edge.get("tx_hash") or "") or None,
                        _f(edge.get("amount_sol")),
                        json.dumps(edge.get("metadata") or {}, separators=(",", ":")),
                    ),
                )
                written += int(conn.execute("SELECT changes()").fetchone()[0] or 0)
    except Exception:
        return written
    return written


def _load_cached_funding(wallets: list[str]) -> dict[str, list[dict[str, Any]]]:
    clean = [w for w in wallets if w]
    if not clean:
        return {}
    try:
        with get_conn() as conn:
            _ensure_schema(conn)
            placeholders = ",".join("?" for _ in clean)
            rows = conn.execute(
                f"""
                SELECT *
                FROM wallet_funding_edges
                WHERE wallet_address IN ({placeholders})
                  AND observed_at_utc >= datetime('now', '-30 days')
                ORDER BY observed_at_utc DESC
                """,
                clean,
            ).fetchall()
    except Exception:
        return {}
    out: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        item = dict(row)
        item["metadata"] = _json_load(item.pop("metadata_json", None), {})
        out[str(item.get("wallet_address") or "")].append(item)
    return dict(out)


def _wallet_history(conn, wallets: list[str]) -> dict[str, dict[str, Any]]:
    clean = [w for w in wallets if w]
    if not clean:
        return {}
    placeholders = ",".join("?" for _ in clean)
    rows = conn.execute(
        f"""
        SELECT wallet_address,
               MIN(observed_at_utc) AS first_seen_utc,
               COUNT(DISTINCT mint) AS distinct_mints,
               COUNT(*) AS event_count
        FROM wallet_token_observations
        WHERE wallet_address IN ({placeholders})
        GROUP BY wallet_address
        """,
        clean,
    ).fetchall()
    out = {str(row["wallet_address"]): dict(row) for row in rows}
    live_rows = conn.execute(
        f"""
        SELECT wallet_address,
               MIN(observed_at_utc) AS first_seen_utc,
               COUNT(DISTINCT mint) AS distinct_mints,
               COUNT(*) AS event_count
        FROM wallet_live_txs
        WHERE wallet_address IN ({placeholders})
        GROUP BY wallet_address
        """,
        clean,
    ).fetchall()
    for row in live_rows:
        wallet = str(row["wallet_address"] or "")
        existing = out.get(wallet)
        if not existing:
            out[wallet] = dict(row)
            continue
        existing["first_seen_utc"] = min(str(existing.get("first_seen_utc") or "9999"), str(row["first_seen_utc"] or "9999"))
        existing["distinct_mints"] = int(existing.get("distinct_mints") or 0) + int(row["distinct_mints"] or 0)
        existing["event_count"] = int(existing.get("event_count") or 0) + int(row["event_count"] or 0)
    return out


def _table_exists(conn, table: str) -> bool:
    try:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        return bool(row)
    except Exception:
        return False


def _load_wallet_memory(conn, wallets: list[str]) -> dict[str, dict[str, Any]]:
    clean = [str(w or "").strip() for w in wallets if str(w or "").strip()]
    if not clean:
        return {}
    try:
        placeholders = ",".join("?" for _ in clean)
        rows = conn.execute(
            f"""
            SELECT *
            FROM wallet_cluster_memory
            WHERE wallet_address IN ({placeholders})
            """,
            clean,
        ).fetchall()
    except Exception:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        item = dict(row)
        item["examples"] = _json_load(item.pop("examples_json", None), [])
        item["metadata"] = _json_load(item.pop("metadata_json", None), {})
        out[str(item.get("wallet_address") or "")] = item
    return out


def _outcome_label(max_return_pct: float, drawdown_from_max_pct: float = 0.0) -> str:
    if max_return_pct >= 80.0:
        return "BIG_RUNNER"
    if max_return_pct >= 35.0:
        return "RUNNER"
    if max_return_pct >= 15.0:
        return "GOOD_SPIKE"
    if drawdown_from_max_pct <= -35.0:
        return "FADED"
    if max_return_pct <= -20.0:
        return "BAD"
    return "FLAT"


def _load_latest_mint_outcome(conn, mint: str) -> dict[str, Any] | None:
    clean = str(mint or "").strip()
    if not clean:
        return None
    best: dict[str, Any] | None = None
    if _table_exists(conn, "runner_review_decisions"):
        try:
            row = conn.execute(
                """
                SELECT mint, symbol,
                       COALESCE(return_1h_pct, 0) AS return_1h_pct,
                       COALESCE(return_4h_pct, 0) AS return_4h_pct,
                       COALESCE(return_24h_pct, 0) AS return_24h_pct,
                       COALESCE(max_return_pct, current_return_pct, return_24h_pct, return_4h_pct, return_1h_pct, 0) AS max_return_pct,
                       COALESCE(drawdown_from_max_pct, 0) AS drawdown_from_max_pct,
                       COALESCE(evaluated_ts, current_stats_ts, created_ts) AS evaluated_at_utc,
                       outcome_label
                FROM runner_review_decisions
                WHERE mint=?
                  AND (max_return_pct IS NOT NULL OR return_24h_pct IS NOT NULL OR current_return_pct IS NOT NULL)
                ORDER BY COALESCE(evaluated_ts, current_stats_ts, created_ts) DESC
                LIMIT 1
                """,
                (clean,),
            ).fetchone()
            if row:
                best = dict(row)
                best["source"] = "runner_review_decisions"
        except Exception:
            pass
    if _table_exists(conn, "memecoin_signal_outcomes"):
        try:
            row = conn.execute(
                """
                SELECT mint, symbol,
                       COALESCE(return_1h_pct, 0) AS return_1h_pct,
                       COALESCE(return_4h_pct, 0) AS return_4h_pct,
                       COALESCE(return_24h_pct, 0) AS return_24h_pct,
                       MAX(COALESCE(return_24h_pct, return_4h_pct, return_1h_pct, 0)) AS max_return_pct,
                       0 AS drawdown_from_max_pct,
                       COALESCE(evaluated_24h_ts_utc, evaluated_4h_ts_utc, evaluated_1h_ts_utc, scanned_at) AS evaluated_at_utc,
                       rug_label AS outcome_label
                FROM memecoin_signal_outcomes
                WHERE mint=?
                  AND (return_24h_pct IS NOT NULL OR return_4h_pct IS NOT NULL OR return_1h_pct IS NOT NULL)
                GROUP BY mint
                ORDER BY COALESCE(evaluated_24h_ts_utc, evaluated_4h_ts_utc, evaluated_1h_ts_utc, scanned_at) DESC
                LIMIT 1
                """,
                (clean,),
            ).fetchone()
            if row and (_f(row["max_return_pct"]) > _f((best or {}).get("max_return_pct")) or not best):
                best = dict(row)
                best["source"] = "memecoin_signal_outcomes"
        except Exception:
            pass
    if not best:
        return None
    max_return = _f(best.get("max_return_pct"))
    drawdown = _f(best.get("drawdown_from_max_pct"))
    label = str(best.get("outcome_label") or "").upper()
    if not label or label in {"UNKNOWN", "NONE", "PENDING"}:
        label = _outcome_label(max_return, drawdown)
    outcome_score = _clamp(50.0 + max_return * 0.6 + min(drawdown, 0.0) * 0.35)
    return {
        "return_1h_pct": _f(best.get("return_1h_pct")),
        "return_4h_pct": _f(best.get("return_4h_pct")),
        "return_24h_pct": _f(best.get("return_24h_pct")),
        "max_return_pct": max_return,
        "drawdown_from_max_pct": drawdown,
        "evaluated_at_utc": str(best.get("evaluated_at_utc") or _iso()),
        "outcome_label": label,
        "outcome_score": round(outcome_score, 1),
        "source": best.get("source"),
    }


def _refresh_wallet_trust_from_attributions(conn, wallets: list[str] | None = None) -> int:
    clean = [str(w or "").strip() for w in (wallets or []) if str(w or "").strip()]
    filter_clause = ""
    params: list[Any] = []
    if clean:
        filter_clause = f"WHERE wallet_address IN ({','.join('?' for _ in clean)})"
        params.extend(clean)
    try:
        rows = conn.execute(
            f"""
            SELECT wallet_address,
                   COUNT(*) AS signal_count,
                   SUM(CASE WHEN max_return_pct >= 25 THEN 1 ELSE 0 END) AS runner_count,
                   SUM(CASE WHEN max_return_pct <= -15 OR outcome_label IN ('BAD','FADED') THEN 1 ELSE 0 END) AS bad_count,
                   SUM(CASE WHEN distribution_risk_score >= 65 AND max_return_pct <= 10 THEN 1 ELSE 0 END) AS distribution_hit_count,
                   AVG(max_return_pct) AS avg_max_return_pct,
                   MAX(max_return_pct) AS best_max_return_pct
            FROM wallet_cluster_wallet_outcomes
            {filter_clause}
            GROUP BY wallet_address
            """,
            params,
        ).fetchall()
    except Exception:
        return 0
    written = 0
    for row in rows:
        signal_count = int(row["signal_count"] or 0)
        runner_count = int(row["runner_count"] or 0)
        bad_count = int(row["bad_count"] or 0)
        distribution_hit_count = int(row["distribution_hit_count"] or 0)
        avg_return = _f(row["avg_max_return_pct"])
        best_return = _f(row["best_max_return_pct"])
        trust_score = _clamp(
            45.0
            + runner_count * 10.0
            - bad_count * 12.0
            + min(max(avg_return, 0.0) * 0.22, 18.0)
            + min(max(best_return, 0.0) * 0.08, 12.0)
            + min(signal_count, 10) * 1.4
            + distribution_hit_count * 4.0
        )
        if signal_count < 2:
            trust_label = "THIN_HISTORY"
        elif trust_score >= 72:
            trust_label = "TRUSTED_RUNNER_WALLET"
        elif trust_score >= 58:
            trust_label = "POSITIVE_OPERATOR"
        elif trust_score <= 34:
            trust_label = "FADE_RISK"
        else:
            trust_label = "NEUTRAL"
        conn.execute(
            """
            UPDATE wallet_cluster_memory
            SET trust_score=?,
                trust_label=?,
                attributed_signal_count=?,
                attributed_runner_count=?,
                attributed_bad_count=?,
                attributed_distribution_hit_count=?,
                updated_at_utc=?
            WHERE wallet_address=?
            """,
            (
                round(trust_score, 1),
                trust_label,
                signal_count,
                runner_count,
                bad_count,
                distribution_hit_count,
                _iso(),
                str(row["wallet_address"] or ""),
            ),
        )
        written += 1
    return written


def refresh_wallet_cluster_outcome_attribution(limit: int = ATTRIBUTION_REFRESH_LIMIT) -> dict[str, Any]:
    try:
        with get_conn() as conn:
            _ensure_schema(conn)
            rows = conn.execute(
                """
                SELECT s.*
                FROM mint_wallet_cluster_snapshots s
                LEFT JOIN wallet_cluster_outcomes o ON o.snapshot_id = s.id
                WHERE o.snapshot_id IS NULL
                  AND datetime(s.ts_utc) <= datetime('now', ?)
                ORDER BY s.id DESC
                LIMIT ?
                """,
                (f"-{max(0.1, float(ATTRIBUTION_MIN_AGE_HOURS))} hours", max(1, min(int(limit), 1000))),
            ).fetchall()
            inserted = 0
            wallet_inserted = 0
            touched_wallets: set[str] = set()
            for row in rows:
                item = dict(row)
                mint = str(item.get("mint") or "").strip()
                snapshot_id = int(item.get("id") or 0)
                snapshot_dt = _parse_dt(item.get("ts_utc"))
                outcome = _load_latest_mint_outcome(conn, mint)
                if not mint or not snapshot_id or not snapshot_dt or not outcome:
                    continue
                evaluated_dt = _parse_dt(outcome.get("evaluated_at_utc")) or _utc_now()
                age_hours = max(0.0, (evaluated_dt - snapshot_dt).total_seconds() / 3600.0)
                if age_hours < max(0.1, float(ATTRIBUTION_MIN_AGE_HOURS)):
                    continue
                top_wallets = _json_load(item.get("top_wallets_json"), [])
                top_repeat_wallets = _json_load(item.get("top_repeat_wallets_json"), [])
                reasons = _json_load(item.get("reasons_json"), [])
                inputs = _json_load(item.get("inputs_json"), {})
                conn.execute(
                    """
                    INSERT OR IGNORE INTO wallet_cluster_outcomes
                    (snapshot_id, mint, symbol, snapshot_ts_utc, evaluated_at_utc, age_hours,
                     return_1h_pct, return_4h_pct, return_24h_pct, max_return_pct,
                     drawdown_from_max_pct, outcome_label, outcome_score, cluster_label,
                     accumulation_state, repeat_operator_count, repeat_operator_score,
                     distribution_risk_score, accumulation_score, insider_like_score,
                     reasons_json, inputs_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        snapshot_id,
                        mint,
                        item.get("symbol"),
                        item.get("ts_utc"),
                        outcome["evaluated_at_utc"],
                        round(age_hours, 2),
                        outcome["return_1h_pct"],
                        outcome["return_4h_pct"],
                        outcome["return_24h_pct"],
                        outcome["max_return_pct"],
                        outcome["drawdown_from_max_pct"],
                        outcome["outcome_label"],
                        outcome["outcome_score"],
                        item.get("cluster_label"),
                        item.get("accumulation_state"),
                        int(item.get("repeat_operator_count") or 0),
                        _f(item.get("repeat_operator_score")),
                        _f(item.get("distribution_risk_score")),
                        _f(item.get("accumulation_score")),
                        _f(item.get("insider_like_score")),
                        json.dumps(reasons, separators=(",", ":")),
                        json.dumps({**inputs, "outcome_source": outcome.get("source")}, separators=(",", ":")),
                    ),
                )
                inserted += int(conn.execute("SELECT changes()").fetchone()[0] or 0)
                repeat_memory = {
                    str(w.get("wallet_address") or ""): _f(w.get("memory_score"))
                    for w in top_repeat_wallets
                    if isinstance(w, dict)
                }
                for wallet_item in top_wallets:
                    if not isinstance(wallet_item, dict):
                        continue
                    wallet = str(wallet_item.get("wallet_address") or "").strip()
                    if not wallet:
                        continue
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO wallet_cluster_wallet_outcomes
                        (wallet_address, snapshot_id, mint, symbol, snapshot_ts_utc, evaluated_at_utc,
                         max_return_pct, outcome_label, distribution_risk_score,
                         repeat_operator_score, wallet_memory_score_at_signal)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            wallet,
                            snapshot_id,
                            mint,
                            item.get("symbol"),
                            item.get("ts_utc"),
                            outcome["evaluated_at_utc"],
                            outcome["max_return_pct"],
                            outcome["outcome_label"],
                            _f(item.get("distribution_risk_score")),
                            _f(item.get("repeat_operator_score")),
                            repeat_memory.get(wallet, 0.0),
                        ),
                    )
                    changed = int(conn.execute("SELECT changes()").fetchone()[0] or 0)
                    wallet_inserted += changed
                    if changed:
                        touched_wallets.add(wallet)
            trust_updated = _refresh_wallet_trust_from_attributions(conn, sorted(touched_wallets)) if touched_wallets else 0
            return {
                "generated_at": _iso(),
                "scanned": len(rows),
                "inserted": inserted,
                "wallet_inserted": wallet_inserted,
                "trust_updated": trust_updated,
            }
    except Exception as exc:
        return {"generated_at": _iso(), "error": str(exc), "scanned": 0, "inserted": 0, "wallet_inserted": 0, "trust_updated": 0}


def _cluster_attribution_summary(conn, mints: list[str]) -> dict[str, dict[str, Any]]:
    clean = [str(m or "").strip() for m in mints if str(m or "").strip()]
    if not clean:
        return {}
    try:
        placeholders = ",".join("?" for _ in clean)
        rows = conn.execute(
            f"""
            SELECT mint,
                   COUNT(*) AS sample_n,
                   AVG(max_return_pct) AS avg_max_return_pct,
                   SUM(CASE WHEN max_return_pct >= 25 THEN 1 ELSE 0 END) AS runner_n,
                   SUM(CASE WHEN distribution_risk_score >= 65 AND max_return_pct <= 10 THEN 1 ELSE 0 END) AS distribution_warning_hit_n,
                   AVG(CASE WHEN repeat_operator_count > 0 THEN max_return_pct END) AS repeat_avg_max_return_pct
            FROM wallet_cluster_outcomes
            WHERE mint IN ({placeholders})
            GROUP BY mint
            """,
            clean,
        ).fetchall()
    except Exception:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        sample_n = int(row["sample_n"] or 0)
        runner_n = int(row["runner_n"] or 0)
        out[str(row["mint"] or "")] = {
            "sample_n": sample_n,
            "avg_max_return_pct": round(_f(row["avg_max_return_pct"]), 1),
            "runner_n": runner_n,
            "runner_rate_pct": round((runner_n / sample_n) * 100.0, 1) if sample_n else 0.0,
            "distribution_warning_hit_n": int(row["distribution_warning_hit_n"] or 0),
            "repeat_avg_max_return_pct": round(_f(row["repeat_avg_max_return_pct"]), 1),
        }
    return out


def _load_mint_outcomes(conn, mints: list[str]) -> dict[str, dict[str, Any]]:
    clean = [str(m or "").strip() for m in mints if str(m or "").strip()]
    if not clean:
        return {}
    placeholders = ",".join("?" for _ in clean)
    outcomes: dict[str, dict[str, Any]] = {}
    if _table_exists(conn, "runner_review_decisions"):
        try:
            rows = conn.execute(
                f"""
                SELECT mint,
                       MAX(COALESCE(max_return_pct, current_return_pct, return_24h_pct, return_4h_pct, return_1h_pct, 0)) AS max_return_pct,
                       GROUP_CONCAT(DISTINCT outcome_label) AS outcome_labels,
                       GROUP_CONCAT(DISTINCT outcome_status) AS outcome_statuses
                FROM runner_review_decisions
                WHERE mint IN ({placeholders})
                GROUP BY mint
                """,
                clean,
            ).fetchall()
            for row in rows:
                mint = str(row["mint"] or "")
                outcomes[mint] = {
                    "max_return_pct": _f(row["max_return_pct"]),
                    "labels": str(row["outcome_labels"] or ""),
                    "statuses": str(row["outcome_statuses"] or ""),
                    "source": "runner_review_decisions",
                }
        except Exception:
            pass
    if _table_exists(conn, "memecoin_signal_outcomes"):
        try:
            rows = conn.execute(
                f"""
                SELECT mint,
                       MAX(COALESCE(return_24h_pct, return_4h_pct, return_1h_pct, 0)) AS max_return_pct,
                       GROUP_CONCAT(DISTINCT rug_label) AS outcome_labels,
                       GROUP_CONCAT(DISTINCT status) AS outcome_statuses
                FROM memecoin_signal_outcomes
                WHERE mint IN ({placeholders})
                GROUP BY mint
                """,
                clean,
            ).fetchall()
            for row in rows:
                mint = str(row["mint"] or "")
                existing = outcomes.setdefault(mint, {"max_return_pct": 0.0, "labels": "", "statuses": "", "source": ""})
                existing["max_return_pct"] = max(_f(existing.get("max_return_pct")), _f(row["max_return_pct"]))
                existing["labels"] = ",".join([x for x in [str(existing.get("labels") or ""), str(row["outcome_labels"] or "")] if x])
                existing["statuses"] = ",".join([x for x in [str(existing.get("statuses") or ""), str(row["outcome_statuses"] or "")] if x])
                existing["source"] = ",".join([x for x in [str(existing.get("source") or ""), "memecoin_signal_outcomes"] if x])
        except Exception:
            pass
    return outcomes


def _refresh_wallet_cluster_memory(wallet_filter: list[str] | None = None) -> int:
    clean_filter = [str(w or "").strip() for w in (wallet_filter or []) if str(w or "").strip()]
    try:
        with get_conn() as conn:
            _ensure_schema(conn)
            params: list[Any] = [f"-{max(1, int(REPEAT_MEMORY_LOOKBACK_DAYS))} days"]
            filter_clause = ""
            if clean_filter:
                filter_clause = f"AND wallet_address IN ({','.join('?' for _ in clean_filter)})"
                params.extend(clean_filter)
            rows = conn.execute(
                f"""
                SELECT *
                FROM mint_early_wallet_events
                WHERE first_seen_utc >= datetime('now', ?)
                {filter_clause}
                ORDER BY first_seen_utc ASC
                LIMIT ?
                """,
                [*params, max(100, int(MEMORY_REFRESH_EVENT_LIMIT))],
            ).fetchall()
            if not rows:
                return 0
            mints = sorted({str(row["mint"] or "") for row in rows if str(row["mint"] or "")})
            outcomes = _load_mint_outcomes(conn, mints)
            grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for row in rows:
                grouped[str(row["wallet_address"] or "")].append(dict(row))

            now_iso = _iso()
            written = 0
            for wallet, events in grouped.items():
                mint_set = {str(e.get("mint") or "") for e in events if str(e.get("mint") or "")}
                returns: list[float] = []
                runner_mints: set[str] = set()
                distribution_mints: set[str] = set()
                examples: list[dict[str, Any]] = []
                for mint in mint_set:
                    mint_events = [e for e in events if str(e.get("mint") or "") == mint]
                    outcome = outcomes.get(mint) or {}
                    max_return = _f(outcome.get("max_return_pct"))
                    labels = str(outcome.get("labels") or "").upper()
                    returns.append(max_return)
                    if max_return >= 25.0 or any(tag in labels for tag in ("GOOD", "WIN", "RUNNER", "EXPANSION", "BREAKOUT")):
                        runner_mints.add(mint)
                    if any(int(e.get("sell_count") or 0) > 0 or _f(e.get("sell_volume_usd")) > _f(e.get("buy_volume_usd")) * 0.75 for e in mint_events):
                        distribution_mints.add(mint)
                    sample = mint_events[-1]
                    examples.append(
                        {
                            "mint": mint,
                            "symbol": sample.get("symbol"),
                            "max_return_pct": round(max_return, 1),
                            "buy_volume_usd": round(sum(_f(e.get("buy_volume_usd")) for e in mint_events), 2),
                            "sell_volume_usd": round(sum(_f(e.get("sell_volume_usd")) for e in mint_events), 2),
                        }
                    )
                early_mint_count = len(mint_set)
                repeat_runner_count = len(runner_mints)
                distribution_mint_count = len(distribution_mints)
                positive_returns = [r for r in returns if r > 0]
                avg_max_return = sum(positive_returns) / len(positive_returns) if positive_returns else 0.0
                best_max_return = max(returns or [0.0])
                memory_score = _clamp(
                    early_mint_count * 7.0
                    + repeat_runner_count * 16.0
                    + min(max(avg_max_return, 0.0) / 2.0, 18.0)
                    + min(max(best_max_return, 0.0) / 3.0, 22.0)
                    + (8.0 if early_mint_count >= 5 else 0.0)
                    - distribution_mint_count * 5.0
                )
                if memory_score >= 70 or repeat_runner_count >= 3:
                    label = "REPEAT_RUNNER"
                elif memory_score >= 48 or repeat_runner_count >= 2:
                    label = "WATCHED_OPERATOR"
                elif memory_score >= 24:
                    label = "EARLY_PARTICIPANT"
                else:
                    label = "UNPROVEN"
                first_seen = min(str(e.get("first_seen_utc") or "") for e in events)
                last_seen = max(str(e.get("last_seen_utc") or "") for e in events)
                examples = sorted(examples, key=lambda x: _f(x.get("max_return_pct")), reverse=True)[:8]
                conn.execute(
                    """
                    INSERT INTO wallet_cluster_memory
                    (wallet_address, first_seen_utc, last_seen_utc, early_mint_count,
                     repeat_runner_count, distribution_mint_count, avg_max_return_pct,
                     best_max_return_pct, memory_score, memory_label, updated_at_utc,
                     examples_json, metadata_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(wallet_address) DO UPDATE SET
                        first_seen_utc=MIN(wallet_cluster_memory.first_seen_utc, excluded.first_seen_utc),
                        last_seen_utc=MAX(wallet_cluster_memory.last_seen_utc, excluded.last_seen_utc),
                        early_mint_count=excluded.early_mint_count,
                        repeat_runner_count=excluded.repeat_runner_count,
                        distribution_mint_count=excluded.distribution_mint_count,
                        avg_max_return_pct=excluded.avg_max_return_pct,
                        best_max_return_pct=excluded.best_max_return_pct,
                        memory_score=excluded.memory_score,
                        memory_label=excluded.memory_label,
                        updated_at_utc=excluded.updated_at_utc,
                        examples_json=excluded.examples_json,
                        metadata_json=excluded.metadata_json
                    """,
                    (
                        wallet,
                        first_seen,
                        last_seen,
                        early_mint_count,
                        repeat_runner_count,
                        distribution_mint_count,
                        round(avg_max_return, 2),
                        round(best_max_return, 2),
                        round(memory_score, 1),
                        label,
                        now_iso,
                        json.dumps(examples, separators=(",", ":")),
                        json.dumps({"lookback_days": int(REPEAT_MEMORY_LOOKBACK_DAYS)}, separators=(",", ":")),
                    ),
                )
                written += 1
            return written
    except Exception:
        return 0


def _load_flow_rows(mints: list[str], *, early_hours: int, recent_hours: int) -> dict[str, list[dict[str, Any]]]:
    if not mints:
        return {}
    try:
        with get_conn() as conn:
            placeholders = ",".join("?" for _ in mints)
            obs_rows = conn.execute(
                f"""
                SELECT observed_at_utc, wallet_address, mint, symbol, tx_hash, side,
                       source, volume_usd AS amount_usd, block_unix_time, metadata_json
                FROM wallet_token_observations
                WHERE mint IN ({placeholders})
                  AND observed_at_utc >= datetime('now', ?)
                ORDER BY observed_at_utc ASC
                """,
                [*mints, f"-{max(early_hours, recent_hours, 1)} hours"],
            ).fetchall()
            live_rows = conn.execute(
                f"""
                SELECT observed_at_utc, wallet_address, mint, symbol, tx_hash, side,
                       source, amount_usd, NULL AS block_unix_time, metadata_json
                FROM wallet_live_txs
                WHERE mint IN ({placeholders})
                  AND observed_at_utc >= datetime('now', ?)
                ORDER BY observed_at_utc ASC
                """,
                [*mints, f"-{max(early_hours, recent_hours, 1)} hours"],
            ).fetchall()
            large_rows = conn.execute(
                f"""
                SELECT ts_utc AS observed_at_utc, owner AS wallet_address, mint, symbol, tx_hash,
                       trade_side AS side, source, volume_usd AS amount_usd, NULL AS block_unix_time,
                       raw_json AS metadata_json
                FROM memecoin_large_trade_snapshots
                WHERE mint IN ({placeholders})
                  AND owner IS NOT NULL
                  AND owner != ''
                  AND ts_utc >= datetime('now', ?)
                ORDER BY ts_utc ASC
                """,
                [*mints, f"-{max(early_hours, recent_hours, 1)} hours"],
            ).fetchall()
    except Exception:
        return {}
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    seen: set[tuple[str, str, str]] = set()
    for row in list(obs_rows) + list(live_rows) + list(large_rows):
        item = dict(row)
        mint = str(item.get("mint") or "").strip()
        wallet = str(item.get("wallet_address") or "").strip()
        tx = str(item.get("tx_hash") or "").strip()
        if not mint or not wallet or not tx:
            continue
        key = (mint, wallet, tx)
        if key in seen:
            continue
        seen.add(key)
        item["side"] = str(item.get("side") or "").upper()
        item["metadata"] = _json_load(item.pop("metadata_json", None), {})
        grouped[mint].append(item)
    return dict(grouped)


def _persist_early_events(mint: str, symbol: str | None, events_by_wallet: dict[str, dict[str, Any]]) -> int:
    if not events_by_wallet:
        return 0
    written = 0
    try:
        with get_conn() as conn:
            _ensure_schema(conn)
            for wallet, item in events_by_wallet.items():
                conn.execute(
                    """
                    INSERT INTO mint_early_wallet_events
                    (mint, symbol, wallet_address, first_seen_utc, last_seen_utc, buy_count,
                     sell_count, buy_volume_usd, sell_volume_usd, first_tx_hash, source, metadata_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(mint, wallet_address) DO UPDATE SET
                        symbol=COALESCE(excluded.symbol, mint_early_wallet_events.symbol),
                        first_seen_utc=MIN(mint_early_wallet_events.first_seen_utc, excluded.first_seen_utc),
                        last_seen_utc=MAX(mint_early_wallet_events.last_seen_utc, excluded.last_seen_utc),
                        buy_count=MAX(mint_early_wallet_events.buy_count, excluded.buy_count),
                        sell_count=MAX(mint_early_wallet_events.sell_count, excluded.sell_count),
                        buy_volume_usd=MAX(mint_early_wallet_events.buy_volume_usd, excluded.buy_volume_usd),
                        sell_volume_usd=MAX(mint_early_wallet_events.sell_volume_usd, excluded.sell_volume_usd),
                        source=COALESCE(excluded.source, mint_early_wallet_events.source),
                        metadata_json=excluded.metadata_json
                    """,
                    (
                        mint,
                        symbol,
                        wallet,
                        item["first_seen_utc"],
                        item["last_seen_utc"],
                        int(item.get("buy_count") or 0),
                        int(item.get("sell_count") or 0),
                        _f(item.get("buy_volume_usd")),
                        _f(item.get("sell_volume_usd")),
                        item.get("first_tx_hash"),
                        item.get("source"),
                        json.dumps(item.get("metadata") or {}, separators=(",", ":")),
                    ),
                )
                written += 1
    except Exception:
        return written
    return written


def _score_cluster(
    *,
    mint: str,
    symbol: str | None,
    flow_rows: list[dict[str, Any]],
    funding_by_wallet: dict[str, list[dict[str, Any]]],
    wallet_history: dict[str, dict[str, Any]],
    wallet_memory: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    now = _utc_now()
    early_cutoff = now - timedelta(hours=max(1, int(EARLY_WINDOW_HOURS)))
    recent_cutoff = now - timedelta(hours=max(1, int(RECENT_WINDOW_HOURS)))
    by_wallet: dict[str, dict[str, Any]] = {}
    recent_buy_volume = 0.0
    recent_sell_volume = 0.0
    recent_sell_wallets: set[str] = set()
    recent_buy_wallets: set[str] = set()

    for row in sorted(flow_rows, key=lambda x: str(x.get("observed_at_utc") or "")):
        wallet = str(row.get("wallet_address") or "").strip()
        if not wallet:
            continue
        dt = _parse_dt(row.get("observed_at_utc"))
        if not dt:
            continue
        side = str(row.get("side") or "").upper()
        amount = _f(row.get("amount_usd"))
        if dt >= recent_cutoff:
            if side == "SELL":
                recent_sell_volume += amount
                recent_sell_wallets.add(wallet)
            elif side == "BUY":
                recent_buy_volume += amount
                recent_buy_wallets.add(wallet)
        if dt < early_cutoff and wallet not in by_wallet:
            continue
        item = by_wallet.setdefault(
            wallet,
            {
                "wallet_address": wallet,
                "first_seen_utc": row.get("observed_at_utc"),
                "last_seen_utc": row.get("observed_at_utc"),
                "buy_count": 0,
                "sell_count": 0,
                "buy_volume_usd": 0.0,
                "sell_volume_usd": 0.0,
                "first_tx_hash": row.get("tx_hash"),
                "source": row.get("source"),
                "metadata": {},
            },
        )
        item["last_seen_utc"] = max(str(item.get("last_seen_utc") or ""), str(row.get("observed_at_utc") or ""))
        if side == "BUY":
            item["buy_count"] += 1
            item["buy_volume_usd"] += amount
        elif side == "SELL":
            item["sell_count"] += 1
            item["sell_volume_usd"] += amount

    early_wallets = [w for w, item in by_wallet.items() if int(item.get("buy_count") or 0) > 0]
    repeat_wallets = [w for w, item in by_wallet.items() if int(item.get("buy_count") or 0) + int(item.get("sell_count") or 0) >= 2]
    early_buy_volume = sum(_f(item.get("buy_volume_usd")) for item in by_wallet.values())
    early_sell_volume = sum(_f(item.get("sell_volume_usd")) for item in by_wallet.values())

    fresh_wallets: list[str] = []
    for wallet in early_wallets:
        hist = wallet_history.get(wallet) or {}
        first_seen = _parse_dt(hist.get("first_seen_utc"))
        distinct_mints = int(hist.get("distinct_mints") or 0)
        if distinct_mints <= 2:
            fresh_wallets.append(wallet)
        elif first_seen and (now - first_seen.astimezone(timezone.utc)).total_seconds() <= 7 * 86400:
            fresh_wallets.append(wallet)

    repeat_operator_wallets = [
        wallet for wallet in early_wallets
        if _f((wallet_memory.get(wallet) or {}).get("memory_score")) >= 48.0
        or int((wallet_memory.get(wallet) or {}).get("repeat_runner_count") or 0) >= 2
        or _f((wallet_memory.get(wallet) or {}).get("trust_score"), 50.0) >= 62.0
    ]
    repeat_trust_scores = [_f((wallet_memory.get(wallet) or {}).get("trust_score"), 50.0) for wallet in repeat_operator_wallets]
    wallet_trust_score = sum(repeat_trust_scores) / len(repeat_trust_scores) if repeat_trust_scores else 50.0
    repeat_operator_score = _clamp(
        len(repeat_operator_wallets) * 14.0
        + min(
            sum(_f((wallet_memory.get(wallet) or {}).get("memory_score")) for wallet in repeat_operator_wallets) / max(len(repeat_operator_wallets), 1) * 0.7,
            42.0,
        )
        + max(0.0, wallet_trust_score - 50.0) * 0.35
    )

    funder_counts: Counter[str] = Counter()
    linked_wallets: set[str] = set()
    for wallet in early_wallets:
        for edge in funding_by_wallet.get(wallet) or []:
            funder = str(edge.get("funder_address") or "").strip()
            if not funder:
                continue
            funder_counts[funder] += 1
            linked_wallets.add(wallet)
    common_funders = {f: n for f, n in funder_counts.items() if n >= 2}
    linked_wallet_count = sum(common_funders.values()) if common_funders else 0

    early_cluster_score = _clamp(len(early_wallets) * 6.0 + len(repeat_wallets) * 5.0 + min(early_buy_volume / 5_000.0, 20.0))
    fresh_wallet_score = _clamp((len(fresh_wallets) / max(len(early_wallets), 1)) * 70.0 + min(len(fresh_wallets) * 5.0, 30.0))
    common_funding_score = _clamp(len(common_funders) * 22.0 + linked_wallet_count * 8.0)
    distribution_risk_score = _clamp(
        len(recent_sell_wallets) * 10.0
        + min(recent_sell_volume / max(recent_buy_volume + 1.0, 1.0) * 38.0, 38.0)
        + (18.0 if recent_sell_volume > recent_buy_volume * 1.25 and recent_sell_volume > 1_000 else 0.0)
    )
    accumulation_score = _clamp(
        len(recent_buy_wallets) * 9.0
        + min(recent_buy_volume / 5_000.0, 28.0)
        + (12.0 if recent_buy_volume > recent_sell_volume * 1.5 and recent_buy_volume > 1_000 else 0.0)
    )
    insider_like_score = _clamp(
        early_cluster_score * 0.36
        + fresh_wallet_score * 0.22
        + common_funding_score * 0.28
        + repeat_operator_score * 0.22
        + max(0.0, wallet_trust_score - 50.0) * 0.18
        + min(len(repeat_wallets) * 4.0, 14.0)
    )

    if distribution_risk_score >= 70:
        label = "DISTRIBUTION_RISK"
    elif insider_like_score >= 76 and (common_funding_score >= 35 or repeat_operator_score >= 45):
        label = "INSIDER_LIKE"
    elif insider_like_score >= 58:
        label = "COORDINATED_ACCUMULATION"
    elif accumulation_score >= 45:
        label = "ORGANIC_ACCUMULATION"
    elif len(early_wallets) > 0:
        label = "CLEAN_EARLY"
    else:
        label = "NO_CLUSTER_DATA"

    if distribution_risk_score >= 62:
        state = "DISTRIBUTING"
    elif accumulation_score >= 48 and distribution_risk_score < 45:
        state = "ACCUMULATING"
    elif len(early_wallets) >= 3 and distribution_risk_score < 55:
        state = "EARLY_CLUSTER"
    elif recent_sell_volume > 0 or recent_buy_volume > 0:
        state = "MIXED_FLOW"
    else:
        state = "QUIET"

    confidence_score = _clamp(
        min(len(flow_rows), 50) * 1.4
        + len(early_wallets) * 3.0
        + (18.0 if funding_by_wallet else 0.0)
        + (12.0 if repeat_operator_wallets else 0.0)
    )
    reasons: list[str] = []
    warnings: list[str] = []
    if early_wallets:
        reasons.append(f"{len(early_wallets)} early buyer wallet(s) observed")
    if fresh_wallets:
        reasons.append(f"{len(fresh_wallets)} fresh/thin-history wallet(s)")
    if common_funders:
        reasons.append(f"{linked_wallet_count} wallet funding link(s) across {len(common_funders)} shared funder(s)")
    if repeat_operator_wallets:
        reasons.append(f"{len(repeat_operator_wallets)} repeat early wallet(s) with prior runner memory")
    if wallet_trust_score >= 62 and repeat_operator_wallets:
        reasons.append(f"repeat-wallet trust {wallet_trust_score:.0f}/100 from prior outcomes")
    if accumulation_score >= 45:
        reasons.append(f"recent buy flow ${recent_buy_volume:,.0f}")
    if distribution_risk_score >= 45:
        warnings.append(f"recent sell/distribution pressure ${recent_sell_volume:,.0f}")
    if common_funding_score >= 45:
        warnings.append("shared funding pattern detected")
    if fresh_wallet_score >= 65:
        warnings.append("fresh wallet swarm pattern")
    if repeat_operator_score >= 60:
        warnings.append("repeat early-wallet operator pattern")
    if wallet_trust_score <= 36 and repeat_operator_wallets:
        warnings.append("repeat wallet history has fade risk")
    if not reasons:
        reasons.append("not enough wallet-cluster evidence yet")

    top_wallets = sorted(
        by_wallet.values(),
        key=lambda x: (_f(x.get("buy_volume_usd")) - _f(x.get("sell_volume_usd")), int(x.get("buy_count") or 0)),
        reverse=True,
    )[:10]
    funders = [
        {"funder_address": funder, "linked_wallet_count": int(count)}
        for funder, count in funder_counts.most_common(8)
    ]
    top_repeat_wallets = sorted(
        [
            {
                "wallet_address": wallet,
                "memory_score": round(_f((wallet_memory.get(wallet) or {}).get("memory_score")), 1),
                "memory_label": (wallet_memory.get(wallet) or {}).get("memory_label"),
                "trust_score": round(_f((wallet_memory.get(wallet) or {}).get("trust_score"), 50.0), 1),
                "trust_label": (wallet_memory.get(wallet) or {}).get("trust_label"),
                "attributed_signal_count": int((wallet_memory.get(wallet) or {}).get("attributed_signal_count") or 0),
                "attributed_runner_count": int((wallet_memory.get(wallet) or {}).get("attributed_runner_count") or 0),
                "early_mint_count": int((wallet_memory.get(wallet) or {}).get("early_mint_count") or 0),
                "repeat_runner_count": int((wallet_memory.get(wallet) or {}).get("repeat_runner_count") or 0),
                "best_max_return_pct": round(_f((wallet_memory.get(wallet) or {}).get("best_max_return_pct")), 1),
                "examples": (wallet_memory.get(wallet) or {}).get("examples") or [],
            }
            for wallet in repeat_operator_wallets
        ],
        key=lambda x: (_f(x.get("memory_score")), int(x.get("repeat_runner_count") or 0)),
        reverse=True,
    )[:6]
    return {
        "mint": mint,
        "symbol": symbol,
        "ts_utc": _iso(now),
        "early_buyer_count": len(early_wallets),
        "fresh_wallet_count": len(fresh_wallets),
        "repeat_wallet_count": len(repeat_wallets),
        "common_funder_count": len(common_funders),
        "linked_wallet_count": linked_wallet_count,
        "repeat_operator_count": len(repeat_operator_wallets),
        "wallet_trust_score": round(wallet_trust_score, 1),
        "wallet_trust_label": (
            "TRUSTED" if wallet_trust_score >= 62 else
            "FADE_RISK" if wallet_trust_score <= 36 and repeat_operator_wallets else
            "THIN_HISTORY" if repeat_operator_wallets else
            "NO_REPEAT_MEMORY"
        ),
        "early_cluster_score": round(early_cluster_score, 1),
        "fresh_wallet_score": round(fresh_wallet_score, 1),
        "common_funding_score": round(common_funding_score, 1),
        "repeat_operator_score": round(repeat_operator_score, 1),
        "distribution_risk_score": round(distribution_risk_score, 1),
        "accumulation_score": round(accumulation_score, 1),
        "insider_like_score": round(insider_like_score, 1),
        "cluster_label": label,
        "accumulation_state": state,
        "confidence_score": round(confidence_score, 1),
        "reasons": reasons[:6],
        "warnings": warnings[:5],
        "top_wallets": [
            {
                "wallet_address": str(w.get("wallet_address") or ""),
                "first_seen_utc": w.get("first_seen_utc"),
                "last_seen_utc": w.get("last_seen_utc") or w.get("first_seen_utc"),
                "buy_count": int(w.get("buy_count") or 0),
                "sell_count": int(w.get("sell_count") or 0),
                "buy_volume_usd": round(_f(w.get("buy_volume_usd")), 2),
                "sell_volume_usd": round(_f(w.get("sell_volume_usd")), 2),
                "first_tx_hash": w.get("first_tx_hash"),
                "source": w.get("source"),
            }
            for w in top_wallets
        ],
        "top_repeat_wallets": top_repeat_wallets,
        "funders": funders,
        "inputs": {
            "flow_event_count": len(flow_rows),
            "early_buy_volume_usd": round(early_buy_volume, 2),
            "early_sell_volume_usd": round(early_sell_volume, 2),
            "recent_buy_volume_usd": round(recent_buy_volume, 2),
            "recent_sell_volume_usd": round(recent_sell_volume, 2),
            "recent_buy_wallet_count": len(recent_buy_wallets),
            "recent_sell_wallet_count": len(recent_sell_wallets),
            "early_window_hours": int(EARLY_WINDOW_HOURS),
            "recent_window_hours": int(RECENT_WINDOW_HOURS),
            "funding_external_enabled": bool(EXTERNAL_FUNDING_ENABLED),
        },
    }


def _persist_snapshot(item: dict[str, Any]) -> int:
    try:
        with get_conn() as conn:
            _ensure_schema(conn)
            conn.execute(
                """
                INSERT INTO mint_wallet_cluster_snapshots
                (mint, symbol, ts_utc, early_buyer_count, fresh_wallet_count,
                 repeat_wallet_count, common_funder_count, linked_wallet_count,
                 early_cluster_score, fresh_wallet_score, common_funding_score,
                 distribution_risk_score, accumulation_score, insider_like_score,
                 cluster_label, accumulation_state, confidence_score, repeat_operator_count,
                 repeat_operator_score, wallet_trust_score, wallet_trust_label,
                 reasons_json, warnings_json, top_wallets_json,
                 top_repeat_wallets_json, funders_json, inputs_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.get("mint"),
                    item.get("symbol"),
                    item.get("ts_utc") or _iso(),
                    int(item.get("early_buyer_count") or 0),
                    int(item.get("fresh_wallet_count") or 0),
                    int(item.get("repeat_wallet_count") or 0),
                    int(item.get("common_funder_count") or 0),
                    int(item.get("linked_wallet_count") or 0),
                    _f(item.get("early_cluster_score")),
                    _f(item.get("fresh_wallet_score")),
                    _f(item.get("common_funding_score")),
                    _f(item.get("distribution_risk_score")),
                    _f(item.get("accumulation_score")),
                    _f(item.get("insider_like_score")),
                    item.get("cluster_label"),
                    item.get("accumulation_state"),
                    _f(item.get("confidence_score")),
                    int(item.get("repeat_operator_count") or 0),
                    _f(item.get("repeat_operator_score")),
                    _f(item.get("wallet_trust_score"), 50.0),
                    item.get("wallet_trust_label"),
                    json.dumps(item.get("reasons") or [], separators=(",", ":")),
                    json.dumps(item.get("warnings") or [], separators=(",", ":")),
                    json.dumps(item.get("top_wallets") or [], separators=(",", ":")),
                    json.dumps(item.get("top_repeat_wallets") or [], separators=(",", ":")),
                    json.dumps(item.get("funders") or [], separators=(",", ":")),
                    json.dumps(item.get("inputs") or {}, separators=(",", ":")),
                ),
            )
        return 1
    except Exception:
        return 0


def _latest_alerts_for_mints(conn, mints: list[str]) -> dict[str, dict[str, Any]]:
    clean = [str(m or "").strip() for m in mints if str(m or "").strip()]
    if not clean:
        return {}
    try:
        placeholders = ",".join("?" for _ in clean)
        rows = conn.execute(
            f"""
            WITH latest AS (
                SELECT mint, MAX(id) AS max_id
                FROM wallet_cluster_alerts
                WHERE mint IN ({placeholders})
                GROUP BY mint
            )
            SELECT a.*
            FROM wallet_cluster_alerts a
            INNER JOIN latest l ON l.max_id = a.id
            """,
            clean,
        ).fetchall()
    except Exception:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        item = dict(row)
        item["reasons"] = _json_load(item.pop("reasons_json", None), [])
        item["snapshot"] = _json_load(item.pop("snapshot_json", None), {})
        out[str(item.get("mint") or "")] = item
    return out


def _maybe_record_distribution_alert(item: dict[str, Any]) -> dict[str, Any] | None:
    score = _f(item.get("distribution_risk_score"))
    if score < DISTRIBUTION_ALERT_THRESHOLD:
        return None
    mint = str(item.get("mint") or "").strip()
    if not mint:
        return None
    try:
        with get_conn() as conn:
            _ensure_schema(conn)
            recent = conn.execute(
                """
                SELECT id
                FROM wallet_cluster_alerts
                WHERE mint=?
                  AND alert_type='DISTRIBUTION_RISK'
                  AND ts_utc >= datetime('now', ?)
                ORDER BY id DESC
                LIMIT 1
                """,
                (mint, f"-{max(1, int(DISTRIBUTION_ALERT_COOLDOWN_MINUTES))} minutes"),
            ).fetchone()
            if recent:
                return None
            severity = "CRITICAL" if score >= 82 else "HIGH" if score >= 65 else "MEDIUM"
            symbol = str(item.get("symbol") or mint[:6]).upper()
            headline = f"{symbol} early cluster is distributing"
            detail = str((item.get("warnings") or item.get("reasons") or [headline])[0] or headline)
            payload = {
                "mint": mint,
                "symbol": item.get("symbol"),
                "ts_utc": _iso(),
                "alert_type": "DISTRIBUTION_RISK",
                "severity": severity,
                "score": round(score, 1),
                "headline": headline,
                "detail": detail,
                "cluster_label": item.get("cluster_label"),
                "accumulation_state": item.get("accumulation_state"),
                "reasons": list(item.get("warnings") or [])[:3] + list(item.get("reasons") or [])[:3],
            }
            conn.execute(
                """
                INSERT INTO wallet_cluster_alerts
                (mint, symbol, ts_utc, alert_type, severity, score, headline, detail,
                 cluster_label, accumulation_state, reasons_json, snapshot_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload["mint"],
                    payload["symbol"],
                    payload["ts_utc"],
                    payload["alert_type"],
                    payload["severity"],
                    payload["score"],
                    payload["headline"],
                    payload["detail"],
                    payload["cluster_label"],
                    payload["accumulation_state"],
                    json.dumps(payload["reasons"], separators=(",", ":")),
                    json.dumps(item, separators=(",", ":")),
                ),
            )
            return payload
    except Exception:
        return None


def _maybe_record_trusted_cluster_alert(item: dict[str, Any]) -> dict[str, Any] | None:
    repeat_score = _f(item.get("repeat_operator_score"))
    trust_score = _f(item.get("wallet_trust_score"), 50.0)
    distribution = _f(item.get("distribution_risk_score"))
    accumulation = _f(item.get("accumulation_score"))
    repeat_count = int(item.get("repeat_operator_count") or 0)
    if repeat_count <= 0 or distribution >= 50:
        return None
    if trust_score < 58 and accumulation < 45:
        return None
    if repeat_score < TRUSTED_CLUSTER_ALERT_THRESHOLD and trust_score < 62 and accumulation < 45:
        return None
    mint = str(item.get("mint") or "").strip()
    if not mint:
        return None
    try:
        with get_conn() as conn:
            _ensure_schema(conn)
            recent = conn.execute(
                """
                SELECT id
                FROM wallet_cluster_alerts
                WHERE mint=?
                  AND alert_type='TRUSTED_CLUSTER_ENTRY'
                  AND ts_utc >= datetime('now', ?)
                ORDER BY id DESC
                LIMIT 1
                """,
                (mint, f"-{max(1, int(DISTRIBUTION_ALERT_COOLDOWN_MINUTES))} minutes"),
            ).fetchone()
            if recent:
                return None
            symbol = str(item.get("symbol") or mint[:6]).upper()
            severity = "HIGH" if trust_score >= 68 or repeat_score >= 65 else "MEDIUM"
            headline = f"{symbol} repeat-wallet cluster is strengthening"
            detail = (
                f"{repeat_count} repeat wallet(s), trust {trust_score:.0f}, "
                f"distribution {distribution:.0f}; watch for clean entry confirmation."
            )
            payload = {
                "mint": mint,
                "symbol": item.get("symbol"),
                "ts_utc": _iso(),
                "alert_type": "TRUSTED_CLUSTER_ENTRY",
                "severity": severity,
                "score": round(max(repeat_score, trust_score, accumulation), 1),
                "headline": headline,
                "detail": detail,
                "cluster_label": item.get("cluster_label"),
                "accumulation_state": item.get("accumulation_state"),
                "reasons": list(item.get("reasons") or [])[:4],
            }
            conn.execute(
                """
                INSERT INTO wallet_cluster_alerts
                (mint, symbol, ts_utc, alert_type, severity, score, headline, detail,
                 cluster_label, accumulation_state, reasons_json, snapshot_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload["mint"],
                    payload["symbol"],
                    payload["ts_utc"],
                    payload["alert_type"],
                    payload["severity"],
                    payload["score"],
                    payload["headline"],
                    payload["detail"],
                    payload["cluster_label"],
                    payload["accumulation_state"],
                    json.dumps(payload["reasons"], separators=(",", ":")),
                    json.dumps(item, separators=(",", ":")),
                ),
            )
            return payload
    except Exception:
        return None


def get_latest_wallet_cluster_for_mints(mints: list[str], *, max_age_minutes: int | None = None) -> dict[str, dict[str, Any]]:
    clean = [str(m or "").strip() for m in mints if str(m or "").strip()]
    if not clean:
        return {}
    try:
        with get_conn() as conn:
            _ensure_schema(conn)
            placeholders = ",".join("?" for _ in clean)
            params: list[Any] = list(clean)
            age_clause = ""
            if max_age_minutes is not None:
                age_clause = "AND ts_utc >= datetime('now', ?)"
                params.append(f"-{max(1, int(max_age_minutes))} minutes")
            rows = conn.execute(
                f"""
                WITH latest AS (
                    SELECT mint, MAX(id) AS max_id
                    FROM mint_wallet_cluster_snapshots
                    WHERE mint IN ({placeholders})
                    {age_clause}
                    GROUP BY mint
                )
                SELECT s.*
                FROM mint_wallet_cluster_snapshots s
                INNER JOIN latest l ON l.max_id = s.id
                """,
                params,
            ).fetchall()
            alerts = _latest_alerts_for_mints(conn, clean)
            attribution = _cluster_attribution_summary(conn, clean)
    except Exception:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        item = dict(row)
        item["reasons"] = _json_load(item.pop("reasons_json", None), [])
        item["warnings"] = _json_load(item.pop("warnings_json", None), [])
        item["top_wallets"] = _json_load(item.pop("top_wallets_json", None), [])
        item["top_repeat_wallets"] = _json_load(item.pop("top_repeat_wallets_json", None), [])
        item["funders"] = _json_load(item.pop("funders_json", None), [])
        item["inputs"] = _json_load(item.pop("inputs_json", None), {})
        item["latest_alert"] = alerts.get(str(item.get("mint") or ""))
        item["attribution_summary"] = attribution.get(str(item.get("mint") or ""))
        out[str(item.get("mint") or "")] = item
    return out


def build_wallet_cluster_intelligence_map(
    candidates: list[dict] | None,
    *,
    record: bool = True,
    max_funding_wallets: int | None = None,
) -> dict[str, dict[str, Any]]:
    candidate_rows = _coerce_candidates(candidates)
    if not candidate_rows:
        return {}
    mints = [c["mint"] for c in candidate_rows]
    symbol_by_mint = {c["mint"]: str(c.get("symbol") or "").upper() or None for c in candidate_rows}
    flow_by_mint = _load_flow_rows(mints, early_hours=EARLY_WINDOW_HOURS, recent_hours=RECENT_WINDOW_HOURS)
    all_wallets: set[str] = set()
    first_seen_by_wallet: dict[str, datetime] = {}
    for rows in flow_by_mint.values():
        for row in rows:
            wallet = str(row.get("wallet_address") or "").strip()
            if not wallet:
                continue
            all_wallets.add(wallet)
            dt = _parse_dt(row.get("observed_at_utc"))
            if dt and (wallet not in first_seen_by_wallet or dt < first_seen_by_wallet[wallet]):
                first_seen_by_wallet[wallet] = dt

    funding_by_wallet = _load_cached_funding(sorted(all_wallets))
    missing_for_funding = [
        wallet for wallet in sorted(all_wallets)
        if wallet not in funding_by_wallet
    ][: max(0, int(max_funding_wallets if max_funding_wallets is not None else MAX_FUNDING_WALLETS))]
    fetched_edges: list[dict[str, Any]] = []
    for wallet in missing_for_funding:
        fetched_edges.extend(_fetch_wallet_funders(wallet, before_ts=first_seen_by_wallet.get(wallet)))
    if fetched_edges:
        _record_funding_edges(fetched_edges)
        funding_by_wallet = _load_cached_funding(sorted(all_wallets))

    try:
        with get_conn() as conn:
            _ensure_schema(conn)
            wallet_history = _wallet_history(conn, sorted(all_wallets))
            wallet_memory = _load_wallet_memory(conn, sorted(all_wallets))
    except Exception:
        wallet_history = {}
        wallet_memory = {}

    out: dict[str, dict[str, Any]] = {}
    for candidate in candidate_rows:
        mint = candidate["mint"]
        symbol = symbol_by_mint.get(mint)
        rows = flow_by_mint.get(mint, [])
        item = _score_cluster(
            mint=mint,
            symbol=symbol,
            flow_rows=rows,
            funding_by_wallet=funding_by_wallet,
            wallet_history=wallet_history,
            wallet_memory=wallet_memory,
        )
        if record:
            by_wallet = {str(w.get("wallet_address")): w for w in item.get("top_wallets") or [] if w.get("wallet_address")}
            _persist_early_events(mint, symbol, by_wallet)
            _persist_snapshot(item)
            alert = _maybe_record_distribution_alert(item)
            trusted_alert = _maybe_record_trusted_cluster_alert(item)
            if alert or trusted_alert:
                item["latest_alert"] = alert or trusted_alert
        out[mint] = item
    if record and all_wallets:
        _refresh_wallet_cluster_memory(sorted(all_wallets))
        refresh_wallet_cluster_outcome_attribution(limit=ATTRIBUTION_REFRESH_LIMIT)
    return out


def build_wallet_cluster_payload(limit: int = 20) -> dict[str, Any]:
    try:
        with get_conn() as conn:
            _ensure_schema(conn)
            rows = conn.execute(
                """
                SELECT *
                FROM mint_wallet_cluster_snapshots
                ORDER BY ts_utc DESC, insider_like_score DESC
                LIMIT ?
                """,
                (max(1, min(int(limit), 100)),),
            ).fetchall()
            mints = [str(row["mint"] or "") for row in rows]
            alerts = _latest_alerts_for_mints(conn, mints)
            attribution = _cluster_attribution_summary(conn, mints)
    except Exception:
        return {"generated_at": _iso(), "summary": {"total": 0, "state": "UNAVAILABLE"}, "clusters": []}
    clusters: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["reasons"] = _json_load(item.pop("reasons_json", None), [])
        item["warnings"] = _json_load(item.pop("warnings_json", None), [])
        item["top_wallets"] = _json_load(item.pop("top_wallets_json", None), [])
        item["top_repeat_wallets"] = _json_load(item.pop("top_repeat_wallets_json", None), [])
        item["funders"] = _json_load(item.pop("funders_json", None), [])
        item["inputs"] = _json_load(item.pop("inputs_json", None), {})
        item["latest_alert"] = alerts.get(str(item.get("mint") or ""))
        item["attribution_summary"] = attribution.get(str(item.get("mint") or ""))
        clusters.append(item)
    labels = Counter(str(c.get("cluster_label") or "UNKNOWN") for c in clusters)
    return {
        "generated_at": _iso(),
        "summary": {
            "total": len(clusters),
            "state": "ACTIVE" if clusters else "WAITING",
            "by_label": dict(labels),
            "insider_like_count": int(labels.get("INSIDER_LIKE", 0)),
            "distribution_risk_count": int(labels.get("DISTRIBUTION_RISK", 0)),
            "repeat_operator_count": sum(1 for c in clusters if int(c.get("repeat_operator_count") or 0) > 0),
            "active_alert_count": sum(1 for c in clusters if c.get("latest_alert")),
            "attributed_count": sum(1 for c in clusters if c.get("attribution_summary")),
        },
        "clusters": clusters,
    }
