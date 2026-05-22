from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any

from utils.db import get_conn, get_latest_memecoin_token_stats_for_mints, with_db_retry


RESEARCH_DOSSIER_LIMIT = int(os.getenv("MEMECOIN_RESEARCH_DOSSIER_LIMIT", "40"))
RESEARCH_TOKEN_STATS_MAX_AGE_MINUTES = int(os.getenv("MEMECOIN_RESEARCH_TOKEN_STATS_MAX_AGE_MINUTES", "720"))
CATALYST_DEDUPE_MINUTES = int(os.getenv("MEMECOIN_CATALYST_DEDUPE_MINUTES", "120"))
CATALYST_EXTERNAL_LIMIT = int(os.getenv("MEMECOIN_CATALYST_EXTERNAL_LIMIT", "80"))
CONVICTION_SNAPSHOT_MIN_INTERVAL_MINUTES = int(os.getenv("MEMECOIN_CONVICTION_SNAPSHOT_MIN_INTERVAL_MINUTES", "15"))
RESEARCH_PAYLOAD_STALE_MINUTES = int(os.getenv("MEMECOIN_RESEARCH_PAYLOAD_STALE_MINUTES", "30"))
ENTRY_SIGNAL_COOLDOWN_MINUTES = int(os.getenv("MEMECOIN_ENTRY_SIGNAL_COOLDOWN_MINUTES", "45"))
ENTRY_SIGNAL_MAX_DATA_AGE_MINUTES = int(os.getenv("MEMECOIN_ENTRY_SIGNAL_MAX_DATA_AGE_MINUTES", "20"))
ENTRY_SIGNAL_PAPER_UNITS = float(os.getenv("MEMECOIN_ENTRY_SIGNAL_PAPER_UNITS", "100"))
ENTRY_OUTCOME_REFRESH_LIMIT = int(os.getenv("MEMECOIN_ENTRY_OUTCOME_REFRESH_LIMIT", "200"))
ENTRY_EXIT_SNAPSHOT_MIN_INTERVAL_MINUTES = int(os.getenv("MEMECOIN_ENTRY_EXIT_SNAPSHOT_MIN_INTERVAL_MINUTES", "15"))
ENTRY_OUTCOME_CLOSEOUT_HOURS = float(os.getenv("MEMECOIN_ENTRY_OUTCOME_CLOSEOUT_HOURS", "24"))
ENTRY_OUTCOME_WIN_PCT = float(os.getenv("MEMECOIN_ENTRY_OUTCOME_WIN_PCT", "18"))
ENTRY_OUTCOME_BIG_WIN_PCT = float(os.getenv("MEMECOIN_ENTRY_OUTCOME_BIG_WIN_PCT", "50"))
ENTRY_OUTCOME_SMALL_WIN_PCT = float(os.getenv("MEMECOIN_ENTRY_OUTCOME_SMALL_WIN_PCT", "10"))
ENTRY_OUTCOME_LOSS_PCT = float(os.getenv("MEMECOIN_ENTRY_OUTCOME_LOSS_PCT", "-18"))
ENTRY_OUTCOME_GIVEBACK_PCT = float(os.getenv("MEMECOIN_ENTRY_OUTCOME_GIVEBACK_PCT", "-12"))
RESEARCH_DB_RETRIES = int(os.getenv("MEMECOIN_RESEARCH_DB_RETRIES", "4"))
RESEARCH_DB_RETRY_SLEEP_SECONDS = float(os.getenv("MEMECOIN_RESEARCH_DB_RETRY_SLEEP_SECONDS", "0.35"))
RESEARCH_CONFLICT_QUEUE_KEY = "memecoin_research_conflict_refresh_queue"
RESEARCH_CONFLICT_QUEUE_MAX_ITEMS = int(os.getenv("MEMECOIN_RESEARCH_CONFLICT_QUEUE_MAX_ITEMS", "40"))
RESEARCH_CONFLICT_QUEUE_COOLDOWN_MINUTES = int(os.getenv("MEMECOIN_RESEARCH_CONFLICT_QUEUE_COOLDOWN_MINUTES", "15"))
RESEARCH_CONFLICT_QUEUE_MAX_AGE_HOURS = int(os.getenv("MEMECOIN_RESEARCH_CONFLICT_QUEUE_MAX_AGE_HOURS", "24"))


KNOWN_COIN_MEMORY: dict[str, dict[str, Any]] = {
    "5UUH9RTDiSpq6HKS6bp4NdU9PNJpXRXuiw6ShBTBhgH2": {
        "symbol": "TROLL",
        "narrative": "classic_meme_revival",
        "memory_type": "former_runner",
        "community_proof": "Known meme with market memory; best when revival volume returns after fear.",
        "why_it_matters": "Ticker is simple, meme spreads quickly, and prior runner memory can pull sidelined buyers back in.",
        "archetype": "REVIVAL_RUNNER",
    },
    "8J69rbLTzWWgUJziFY8jeu5tDwEPBwUz4pKBMr5rpump": {
        "symbol": "WOJAK",
        "narrative": "classic_meme_revival",
        "memory_type": "dead_to_active",
        "community_proof": "Wojak is an internet-native meme with durable recognition beyond one cycle.",
        "why_it_matters": "Dead-to-active recoveries can move violently when old holders and new momentum meet.",
        "archetype": "REVIVAL_RUNNER",
    },
    "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN": {
        "symbol": "JUP",
        "narrative": "solana_infrastructure",
        "memory_type": "quality_spot_beta",
        "community_proof": "Major Solana ecosystem asset with real product mindshare and liquidity.",
        "why_it_matters": "Higher-quality spot beta can compound during SOL strength with less meme-specific rug risk.",
        "archetype": "SPOT_BETA",
    },
    "CB9dDufT3ZuQXqqSfa1c5kY935TEreyBw9XJXxHKpump": {
        "symbol": "USDUC",
        "narrative": "reflexive_meme_revival",
        "memory_type": "violent_spike_risk",
        "community_proof": "Prior violent expansion means market participants remember the ticker, but exits matter.",
        "why_it_matters": "Can produce large spikes, but requires profit protection because giveback risk is high.",
        "archetype": "SPIKE_AND_GIVEBACK",
    },
    "9BB6NFEcjBCtnNLFko2FqVQBq8HHM13kCyYcdQbgpump": {
        "symbol": "FARTCOIN",
        "narrative": "blue_chip_meme",
        "memory_type": "established_meme_leader",
        "community_proof": "Recognized Solana meme leader with broad mindshare and repeat market attention.",
        "why_it_matters": "Established meme leaders often become liquidity magnets when SOL and meme risk turn on.",
        "archetype": "ESTABLISHED_LEADER",
    },
    "63LfDmNb3MQ8mw9MtZ2To9bEA2M71kZUUGq5tiJxcqj9": {
        "symbol": "GIGA",
        "narrative": "blue_chip_meme",
        "memory_type": "established_meme_leader",
        "community_proof": "Large-cap Solana meme with recurring market attention and established liquidity.",
        "why_it_matters": "Big established memes can become safer meme-beta vehicles when risk appetite returns.",
        "archetype": "ESTABLISHED_LEADER",
    },
    "DtR4D9FtVoTX2569gaL837ZgrB6wNjj6tkmnX9Rdk9B2": {
        "symbol": "AURA",
        "narrative": "culture_meme",
        "memory_type": "established_runner",
        "community_proof": "Recognized runner profile in the system with enough memory to deserve repeat review.",
        "why_it_matters": "Culture memes can move quickly when liquidity and attention reconnect after a base.",
        "archetype": "ESTABLISHED_RUNNER",
    },
    "7GCihgDB8fe6KNjn2MYtkzZcRjQy3t9GHdC8uHYmW2hr": {
        "symbol": "POPCAT",
        "narrative": "blue_chip_meme",
        "memory_type": "established_meme_leader",
        "community_proof": "Known cat meme leader with repeat Solana mindshare and deep market memory.",
        "why_it_matters": "Recognized animal memes can become rotation leaders when meme liquidity returns.",
        "archetype": "ESTABLISHED_LEADER",
    },
    "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm": {
        "symbol": "WIF",
        "narrative": "blue_chip_meme",
        "memory_type": "established_meme_leader",
        "community_proof": "Major Solana meme leader with broad exchange and market awareness.",
        "why_it_matters": "High-liquidity meme leaders can act as early gauges for broader meme risk-on conditions.",
        "archetype": "ESTABLISHED_LEADER",
    },
    "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263": {
        "symbol": "BONK",
        "narrative": "solana_culture",
        "memory_type": "ecosystem_meme_leader",
        "community_proof": "Deep Solana-native meme with long-term ecosystem awareness.",
        "why_it_matters": "Solana-native leaders often become beta for SOL strength and meme rotation together.",
        "archetype": "SOLANA_CULTURE_LEADER",
    },
    "MEW1gQWJ3nEXg2qgERiKu7FAFj79PHvQVREQUzScPP5": {
        "symbol": "MEW",
        "narrative": "animal_meme",
        "memory_type": "established_meme_leader",
        "community_proof": "Known cat meme with established liquidity and repeat market attention.",
        "why_it_matters": "Animal meme leaders can catch rotation when cat/dog liquidity wakes up.",
        "archetype": "ESTABLISHED_LEADER",
    },
    "2zMMhcVQEXDtdE6vsFS7S7D5oUodfJHE8vd1gnBouauv": {
        "symbol": "PENGU",
        "narrative": "brand_meme",
        "memory_type": "established_brand_beta",
        "community_proof": "Recognized brand/meme asset with cross-cycle awareness.",
        "why_it_matters": "Brand memes can attract liquidity when broad meme and NFT culture rotation improves.",
        "archetype": "BRAND_BETA",
    },
}


KNOWN_SYMBOL_MEMORY: dict[str, dict[str, Any]] = {
    "MOG": {
        "narrative": "blue_chip_meme",
        "memory_type": "established_meme_leader",
        "community_proof": "Known meme brand, but Solana ticker collisions require mint-level confirmation.",
        "why_it_matters": "Only useful after CA identity is verified; otherwise ticker collision risk is too high.",
        "archetype": "ESTABLISHED_LEADER",
    },
    "JTO": {
        "narrative": "solana_infrastructure",
        "memory_type": "quality_spot_beta",
        "community_proof": "Solana ecosystem asset with utility-driven liquidity.",
        "why_it_matters": "Quality spot beta can work when SOL-led flows broaden beyond memes.",
        "archetype": "SPOT_BETA",
    },
    "PYTH": {
        "narrative": "solana_infrastructure",
        "memory_type": "quality_spot_beta",
        "community_proof": "Infrastructure asset with ecosystem relevance.",
        "why_it_matters": "Useful as a safer beta reference when pure meme quality is weak.",
        "archetype": "SPOT_BETA",
    },
}


MIGRATIONS: tuple[str, ...] = (
    "ALTER TABLE memecoin_research_dossiers ADD COLUMN catalyst_type TEXT",
    "ALTER TABLE memecoin_research_dossiers ADD COLUMN why_now TEXT",
    "ALTER TABLE memecoin_research_dossiers ADD COLUMN rotation_state TEXT",
    "ALTER TABLE memecoin_research_dossiers ADD COLUMN outcome_tuning_bonus REAL DEFAULT 0",
    "ALTER TABLE memecoin_research_dossiers ADD COLUMN last_catalyst_type TEXT",
    "ALTER TABLE memecoin_research_dossiers ADD COLUMN last_catalyst_ts TEXT",
    "ALTER TABLE memecoin_research_dossiers ADD COLUMN last_catalyst_confidence REAL DEFAULT 0",
    "ALTER TABLE memecoin_research_dossiers ADD COLUMN last_catalyst_headline TEXT",
    "ALTER TABLE memecoin_research_dossiers ADD COLUMN catalyst_event_count INTEGER DEFAULT 0",
    "ALTER TABLE memecoin_research_dossiers ADD COLUMN good_coin_score REAL DEFAULT 0",
    "ALTER TABLE memecoin_research_dossiers ADD COLUMN good_coin_status TEXT",
    "ALTER TABLE memecoin_research_dossiers ADD COLUMN good_coin_reasons_json TEXT",
    "ALTER TABLE memecoin_research_dossiers ADD COLUMN good_coin_blockers_json TEXT",
    "ALTER TABLE memecoin_research_dossiers ADD COLUMN conviction_band TEXT",
    "ALTER TABLE memecoin_research_dossiers ADD COLUMN catalyst_strength_score REAL DEFAULT 0",
    "ALTER TABLE memecoin_research_dossiers ADD COLUMN catalyst_strength_label TEXT",
    "ALTER TABLE memecoin_research_dossiers ADD COLUMN too_late_score REAL DEFAULT 0",
    "ALTER TABLE memecoin_research_dossiers ADD COLUMN too_late_label TEXT",
    "ALTER TABLE memecoin_research_dossiers ADD COLUMN too_late_reasons_json TEXT",
    "ALTER TABLE memecoin_research_dossiers ADD COLUMN operator_priority REAL DEFAULT 0",
    "ALTER TABLE memecoin_research_dossiers ADD COLUMN entry_state TEXT",
    "ALTER TABLE memecoin_research_dossiers ADD COLUMN entry_score REAL DEFAULT 0",
    "ALTER TABLE memecoin_research_dossiers ADD COLUMN entry_blocker TEXT",
    "ALTER TABLE memecoin_research_dossiers ADD COLUMN entry_instruction TEXT",
    "ALTER TABLE memecoin_research_dossiers ADD COLUMN entry_reasons_json TEXT",
    "ALTER TABLE memecoin_research_dossiers ADD COLUMN entry_blockers_json TEXT",
    "ALTER TABLE memecoin_research_dossiers ADD COLUMN entry_data_age_seconds REAL",
    "ALTER TABLE memecoin_research_dossiers ADD COLUMN entry_guard_status TEXT",
    "ALTER TABLE memecoin_research_dossiers ADD COLUMN narrative_hooks_json TEXT",
    "ALTER TABLE memecoin_research_dossiers ADD COLUMN attention_persistence_score REAL DEFAULT 0",
    "ALTER TABLE memecoin_research_dossiers ADD COLUMN attention_persistence_label TEXT",
    "ALTER TABLE memecoin_research_dossiers ADD COLUMN catalyst_quality_score REAL DEFAULT 0",
    "ALTER TABLE memecoin_research_dossiers ADD COLUMN catalyst_quality_label TEXT",
    "ALTER TABLE memecoin_research_dossiers ADD COLUMN contradictions_json TEXT",
    "ALTER TABLE memecoin_research_dossiers ADD COLUMN comparable_runner TEXT",
    "ALTER TABLE memecoin_research_dossiers ADD COLUMN comparable_runner_confidence REAL DEFAULT 0",
    "ALTER TABLE memecoin_research_dossiers ADD COLUMN sustainability_score REAL DEFAULT 0",
    "ALTER TABLE memecoin_research_dossiers ADD COLUMN sustainability_label TEXT",
    "ALTER TABLE memecoin_research_dossiers ADD COLUMN sustainability_checks_json TEXT",
    "ALTER TABLE memecoin_research_dossiers ADD COLUMN narrative_intelligence_json TEXT",
)


SCHEMA = """
CREATE TABLE IF NOT EXISTS memecoin_research_dossiers (
    mint TEXT PRIMARY KEY,
    symbol TEXT,
    generated_at TEXT NOT NULL,
    source TEXT,
    narrative TEXT,
    memory_type TEXT,
    archetype TEXT,
    community_proof TEXT,
    catalyst TEXT,
    tradeability TEXT,
    risk_summary TEXT,
    system_thesis TEXT,
    invalidation TEXT,
    action TEXT,
    research_score REAL,
    narrative_score REAL,
    memory_score REAL,
    community_score REAL,
    catalyst_score REAL,
    tradeability_score REAL,
    risk_score REAL,
    outcome_sample_n INTEGER DEFAULT 0,
    outcome_good_n INTEGER DEFAULT 0,
    outcome_bad_n INTEGER DEFAULT 0,
    avg_max_return_pct REAL DEFAULT 0,
    last_outcome_label TEXT,
    marketcap REAL,
    liquidity REAL,
    volume_24h REAL,
    buy_pressure REAL,
    vol_acceleration REAL,
    runner_state TEXT,
    proof_status TEXT,
    blocker_key TEXT,
    entry_window TEXT,
    fuel_quality TEXT,
    move_phase TEXT,
    tags_json TEXT,
    evidence_json TEXT,
    raw_json TEXT
)
"""


CATALYST_SCHEMA = """
CREATE TABLE IF NOT EXISTS memecoin_catalyst_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mint TEXT NOT NULL,
    symbol TEXT,
    event_ts TEXT NOT NULL,
    event_type TEXT NOT NULL,
    source TEXT,
    confidence REAL DEFAULT 0,
    headline TEXT,
    detail TEXT,
    narrative TEXT,
    marketcap REAL,
    liquidity REAL,
    volume_24h REAL,
    buy_pressure REAL,
    vol_acceleration REAL,
    evidence_json TEXT,
    raw_json TEXT
)
"""


CONVICTION_SNAPSHOT_SCHEMA = """
CREATE TABLE IF NOT EXISTS memecoin_conviction_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    generated_at TEXT NOT NULL,
    mint TEXT NOT NULL,
    symbol TEXT,
    conviction_band TEXT,
    action TEXT,
    research_score REAL,
    operator_priority REAL,
    good_coin_score REAL,
    good_coin_status TEXT,
    catalyst_strength_score REAL,
    catalyst_strength_label TEXT,
    too_late_score REAL,
    too_late_label TEXT,
    marketcap REAL,
    liquidity REAL,
    volume_24h REAL,
    catalyst_type TEXT,
    evidence_json TEXT
)
"""


ENTRY_SIGNAL_SCHEMA = """
CREATE TABLE IF NOT EXISTS memecoin_entry_signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    mint TEXT NOT NULL,
    symbol TEXT,
    entry_state TEXT NOT NULL,
    entry_score REAL DEFAULT 0,
    last_blocker TEXT,
    guard_status TEXT,
    data_age_seconds REAL,
    entry_price REAL DEFAULT 0,
    entry_marketcap REAL DEFAULT 0,
    entry_liquidity REAL DEFAULT 0,
    entry_volume_24h REAL DEFAULT 0,
    token_stats_ts_utc TEXT,
    research_score REAL DEFAULT 0,
    operator_priority REAL DEFAULT 0,
    conviction_band TEXT,
    action TEXT,
    signal_hash TEXT,
    status TEXT DEFAULT 'OPEN',
    reasons_json TEXT,
    blockers_json TEXT,
    dossier_json TEXT
)
"""


ENTRY_PAPER_SCHEMA = """
CREATE TABLE IF NOT EXISTS memecoin_entry_paper_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id INTEGER NOT NULL UNIQUE,
    mint TEXT NOT NULL,
    symbol TEXT,
    entry_ts TEXT NOT NULL,
    entry_price REAL DEFAULT 0,
    entry_marketcap REAL DEFAULT 0,
    paper_units REAL DEFAULT 0,
    status TEXT DEFAULT 'OPEN',
    last_eval_ts TEXT,
    current_price REAL DEFAULT 0,
    current_marketcap REAL DEFAULT 0,
    return_15m_pct REAL,
    return_1h_pct REAL,
    return_4h_pct REAL,
    return_24h_pct REAL,
    max_return_pct REAL DEFAULT 0,
    drawdown_from_max_pct REAL DEFAULT 0,
    outcome_label TEXT,
    notes_json TEXT
)
"""


OPPORTUNITY_LEDGER_SCHEMA = """
CREATE TABLE IF NOT EXISTS memecoin_opportunity_ledger (
    mint TEXT PRIMARY KEY,
    symbol TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    state TEXT NOT NULL,
    previous_state TEXT,
    state_changed_at TEXT NOT NULL,
    research_score REAL DEFAULT 0,
    signal_quality_tier TEXT,
    signal_quality_score REAL DEFAULT 0,
    execution_alignment_state TEXT,
    entry_state TEXT,
    last_blocker TEXT,
    trigger_contract TEXT,
    missed_reason TEXT,
    lesson TEXT,
    watch_start_marketcap REAL DEFAULT 0,
    current_marketcap REAL DEFAULT 0,
    max_observed_marketcap REAL DEFAULT 0,
    max_observed_return_pct REAL DEFAULT 0,
    outcome_label TEXT,
    outcome_evaluated_at TEXT,
    return_15m_pct REAL,
    return_1h_pct REAL,
    return_4h_pct REAL,
    return_24h_pct REAL,
    max_return_from_watch_pct REAL DEFAULT 0,
    blocker_verdict TEXT,
    trigger_verdict TEXT,
    trigger_cleared_at TEXT,
    trigger_clear_return_4h_pct REAL,
    learned_summary TEXT,
    status_json TEXT,
    dossier_json TEXT
)
"""


OPPORTUNITY_EVENT_SCHEMA = """
CREATE TABLE IF NOT EXISTS memecoin_opportunity_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_ts TEXT NOT NULL,
    mint TEXT NOT NULL,
    symbol TEXT,
    event_type TEXT NOT NULL,
    previous_state TEXT,
    new_state TEXT NOT NULL,
    trigger_cleared INTEGER NOT NULL DEFAULT 0,
    last_blocker TEXT,
    research_score REAL DEFAULT 0,
    signal_quality_tier TEXT,
    signal_quality_score REAL DEFAULT 0,
    max_return_from_watch_pct REAL DEFAULT 0,
    outcome_label TEXT,
    alert_priority REAL DEFAULT 0,
    headline TEXT,
    detail TEXT,
    trigger_contract TEXT,
    status_json TEXT
)
"""


SHADOW_STRATEGY_SCHEMA = """
CREATE TABLE IF NOT EXISTS memecoin_shadow_strategies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mint TEXT NOT NULL,
    symbol TEXT,
    strategy_key TEXT NOT NULL,
    created_at TEXT NOT NULL,
    opportunity_first_seen_at TEXT,
    archetype TEXT,
    blocker_at_creation TEXT,
    state_at_creation TEXT,
    entry_status TEXT NOT NULL DEFAULT 'PENDING',
    entry_ts TEXT,
    entry_marketcap REAL DEFAULT 0,
    entry_reason TEXT,
    return_15m_pct REAL,
    return_1h_pct REAL,
    return_4h_pct REAL,
    return_24h_pct REAL,
    max_return_pct REAL DEFAULT 0,
    outcome_label TEXT,
    last_eval_at TEXT,
    notes_json TEXT,
    UNIQUE(mint, strategy_key)
)
"""


MANUAL_REVIEW_SCHEMA = """
CREATE TABLE IF NOT EXISTS memecoin_manual_review_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    mint TEXT NOT NULL,
    symbol TEXT,
    decision TEXT NOT NULL,
    operator_note TEXT,
    source TEXT,
    research_score REAL DEFAULT 0,
    entry_zone TEXT,
    position_stance TEXT,
    exit_state TEXT,
    entry_marketcap REAL DEFAULT 0,
    current_marketcap REAL DEFAULT 0,
    last_eval_at TEXT,
    return_1h_pct REAL,
    return_4h_pct REAL,
    return_24h_pct REAL,
    max_return_pct REAL DEFAULT 0,
    outcome_label TEXT,
    dossier_json TEXT,
    notes_json TEXT
)
"""


OPPORTUNITY_LEDGER_MIGRATIONS: tuple[str, ...] = (
    "ALTER TABLE memecoin_opportunity_ledger ADD COLUMN outcome_label TEXT",
    "ALTER TABLE memecoin_opportunity_ledger ADD COLUMN outcome_evaluated_at TEXT",
    "ALTER TABLE memecoin_opportunity_ledger ADD COLUMN return_15m_pct REAL",
    "ALTER TABLE memecoin_opportunity_ledger ADD COLUMN return_1h_pct REAL",
    "ALTER TABLE memecoin_opportunity_ledger ADD COLUMN return_4h_pct REAL",
    "ALTER TABLE memecoin_opportunity_ledger ADD COLUMN return_24h_pct REAL",
    "ALTER TABLE memecoin_opportunity_ledger ADD COLUMN max_return_from_watch_pct REAL DEFAULT 0",
    "ALTER TABLE memecoin_opportunity_ledger ADD COLUMN blocker_verdict TEXT",
    "ALTER TABLE memecoin_opportunity_ledger ADD COLUMN trigger_verdict TEXT",
    "ALTER TABLE memecoin_opportunity_ledger ADD COLUMN trigger_cleared_at TEXT",
    "ALTER TABLE memecoin_opportunity_ledger ADD COLUMN trigger_clear_return_4h_pct REAL",
    "ALTER TABLE memecoin_opportunity_ledger ADD COLUMN learned_summary TEXT",
)


BAND_RANK = {"BUYABLE": 0, "TRIGGERED": 1, "WATCH": 2, "TOO_LATE": 3, "IGNORE": 4}
ACTION_RANK = {"PAPER_ENTRY": 0, "MANUAL_REVIEW": 1, "WATCH": 2, "TOO_LATE": 3, "IGNORE": 4}
SHADOW_STRATEGY_KEYS: tuple[str, ...] = (
    "enter_immediately_when_watching",
    "enter_when_paper_ready",
    "enter_when_volume_clears",
    "enter_after_pullback",
    "enter_only_if_trigger_contract_clears",
    "skip_if_blocker_present",
)


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _research_db_retry(fn):
    return with_db_retry(
        fn,
        retries=max(1, RESEARCH_DB_RETRIES),
        base_sleep_s=max(0.05, RESEARCH_DB_RETRY_SLEEP_SECONDS),
    )


def _f(value: object, default: float = 0.0) -> float:
    try:
        return float(value if value is not None else default)
    except Exception:
        return default


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


def _count_by(items: list[dict[str, Any]], key: str, *, default: str = "UNKNOWN") -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        label = str(item.get(key) or default)
        counts[label] = counts.get(label, 0) + 1
    return counts


def _narrative_intelligence_counts(dossiers: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "durable_attention_count": sum(
            1 for d in dossiers
            if str(d.get("attention_persistence_label") or "").upper() == "DURABLE"
        ),
        "active_attention_count": sum(
            1 for d in dossiers
            if str(d.get("attention_persistence_label") or "").upper() in {"DURABLE", "ACTIVE"}
        ),
        "structural_catalyst_count": sum(
            1 for d in dossiers
            if str(d.get("catalyst_quality_label") or "").upper() == "STRUCTURAL"
        ),
        "confirmed_flow_count": sum(
            1 for d in dossiers
            if str(d.get("catalyst_quality_label") or "").upper() in {"STRUCTURAL", "CONFIRMED_FLOW"}
        ),
        "contradiction_count": sum(len(d.get("contradictions") or []) for d in dossiers),
        "sustainable_count": sum(
            1 for d in dossiers
            if str(d.get("sustainability_label") or "").upper() == "SUSTAINABLE"
        ),
        "fade_risk_count": sum(
            1 for d in dossiers
            if str(d.get("sustainability_label") or "").upper() == "FADE_RISK"
        ),
    }


def _ensure_schema(conn) -> None:
    conn.execute(SCHEMA)
    conn.execute(CATALYST_SCHEMA)
    conn.execute(CONVICTION_SNAPSHOT_SCHEMA)
    conn.execute(ENTRY_SIGNAL_SCHEMA)
    conn.execute(ENTRY_PAPER_SCHEMA)
    conn.execute(OPPORTUNITY_LEDGER_SCHEMA)
    conn.execute(OPPORTUNITY_EVENT_SCHEMA)
    conn.execute(SHADOW_STRATEGY_SCHEMA)
    conn.execute(MANUAL_REVIEW_SCHEMA)
    for ddl in OPPORTUNITY_LEDGER_MIGRATIONS:
        try:
            conn.execute(ddl)
        except Exception:
            pass
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS memecoin_exit_signal_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts_utc TEXT NOT NULL,
            trade_id INTEGER NOT NULL,
            mint TEXT NOT NULL,
            symbol TEXT NOT NULL,
            age_minutes REAL,
            current_price REAL,
            current_return_pct REAL,
            current_scanner_score REAL,
            current_timing_score REAL,
            current_market_quality_score REAL,
            current_support_score REAL,
            current_readiness_score REAL,
            current_profit_room_score REAL,
            current_profit_room_label TEXT,
            current_market_quality_verdict TEXT,
            current_wallet_behavior_state TEXT,
            current_smart_money_quality TEXT,
            current_proof_score REAL,
            current_proof_reason TEXT,
            volume_trend_label TEXT,
            scanner_still_actionable INTEGER NOT NULL DEFAULT 0,
            large_trade_buy_volume REAL,
            large_trade_sell_volume REAL,
            large_trade_buy_count INTEGER,
            large_trade_sell_count INTEGER,
            token_stats_ts_utc TEXT,
            notes_json TEXT
        )
        """
    )
    for ddl in MIGRATIONS:
        try:
            conn.execute(ddl)
        except Exception:
            pass
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_memecoin_research_score
        ON memecoin_research_dossiers(research_score DESC, generated_at DESC)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_memecoin_research_action
        ON memecoin_research_dossiers(action, research_score DESC)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_memecoin_catalyst_mint_ts
        ON memecoin_catalyst_events(mint, event_ts DESC)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_memecoin_catalyst_type_ts
        ON memecoin_catalyst_events(event_type, event_ts DESC)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_memecoin_catalyst_confidence_ts
        ON memecoin_catalyst_events(confidence DESC, event_ts DESC)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_memecoin_conviction_snapshot_mint_ts
        ON memecoin_conviction_snapshots(mint, generated_at DESC)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_memecoin_conviction_snapshot_band_ts
        ON memecoin_conviction_snapshots(conviction_band, generated_at DESC)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_memecoin_entry_signals_mint_ts
        ON memecoin_entry_signals(mint, created_at DESC)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_memecoin_entry_signals_state_ts
        ON memecoin_entry_signals(entry_state, created_at DESC)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_memecoin_entry_paper_status
        ON memecoin_entry_paper_trades(status, entry_ts DESC)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_memecoin_exit_signal_snapshots_trade_ts
        ON memecoin_exit_signal_snapshots(trade_id, ts_utc)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_memecoin_exit_signal_snapshots_mint_ts
        ON memecoin_exit_signal_snapshots(mint, ts_utc)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_memecoin_opportunity_ledger_state
        ON memecoin_opportunity_ledger(state, last_seen_at DESC)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_memecoin_opportunity_events_ts
        ON memecoin_opportunity_events(event_ts DESC)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_memecoin_opportunity_events_type_ts
        ON memecoin_opportunity_events(event_type, event_ts DESC)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_memecoin_shadow_strategies_key
        ON memecoin_shadow_strategies(strategy_key, entry_status, created_at DESC)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_memecoin_shadow_strategies_mint
        ON memecoin_shadow_strategies(mint, strategy_key)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_memecoin_manual_review_mint_ts
        ON memecoin_manual_review_decisions(mint, created_at DESC)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_memecoin_manual_review_decision_ts
        ON memecoin_manual_review_decisions(decision, created_at DESC)
        """
    )
    conn.commit()


def _memory_for(mint: str, symbol: str) -> dict[str, Any]:
    memory = dict(KNOWN_COIN_MEMORY.get(str(mint or "").strip()) or {})
    if not memory:
        memory = dict(KNOWN_SYMBOL_MEMORY.get(str(symbol or "").upper()) or {})
        if memory:
            memory["symbol_memory_only"] = True
    return memory


def _json_load(raw: object, default):
    try:
        return json.loads(str(raw or ""))
    except Exception:
        return default


def _kv_read_json(key: str, default):
    try:
        with get_conn() as conn:
            row = conn.execute("SELECT value FROM kv_store WHERE key=?", (key,)).fetchone()
            if not row or not row[0]:
                return default
            return json.loads(str(row[0]))
    except Exception:
        return default


def _kv_write_json(key: str, value: object) -> None:
    payload = json.dumps(value, separators=(",", ":"), sort_keys=True)
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO kv_store(key, value) VALUES (?, ?)",
            (key, payload),
        )
        conn.commit()


def _age_seconds(ts: object) -> float | None:
    raw = str(ts or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds())
    except Exception:
        return None


def _parse_dt(ts: object) -> datetime | None:
    raw = str(ts or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def _event_age_cutoff(minutes: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=max(1, int(minutes)))).isoformat()


def queue_memecoin_research_conflict_refresh(
    *,
    mint: str,
    symbol: str | None = None,
    reason: str = "signal_conflict",
    conflict_state: str | None = None,
    conflicts: list[str] | None = None,
    source: str = "home_conflict_resolver",
) -> dict[str, Any]:
    """Queue a mint for near-term research refresh when live layers disagree."""
    clean_mint = str(mint or "").strip()
    clean_symbol = str(symbol or "").strip().upper()
    if not clean_mint:
        return {"queued": False, "reason": "missing_mint"}
    now = _iso()
    queue = _kv_read_json(RESEARCH_CONFLICT_QUEUE_KEY, {})
    if not isinstance(queue, dict):
        queue = {}
    existing = dict(queue.get(clean_mint) or {})
    existing_age = _age_seconds(existing.get("requested_at"))
    if existing and existing_age is not None and existing_age < RESEARCH_CONFLICT_QUEUE_COOLDOWN_MINUTES * 60:
        return {"queued": False, "reason": "cooldown", "existing": existing}

    queue[clean_mint] = {
        "mint": clean_mint,
        "symbol": clean_symbol or existing.get("symbol"),
        "reason": reason,
        "conflict_state": conflict_state,
        "conflicts": [str(x) for x in list(conflicts or [])[:6] if str(x or "").strip()],
        "source": source,
        "requested_at": now,
        "status": "PENDING",
        "attempts": int(existing.get("attempts") or 0),
        "last_attempt_at": existing.get("last_attempt_at"),
        "last_completed_at": existing.get("last_completed_at"),
    }
    items = sorted(
        queue.values(),
        key=lambda x: _parse_dt(x.get("requested_at")) or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )[: max(1, RESEARCH_CONFLICT_QUEUE_MAX_ITEMS)]
    _kv_write_json(RESEARCH_CONFLICT_QUEUE_KEY, {str(x["mint"]): x for x in items if x.get("mint")})
    return {"queued": True, "mint": clean_mint, "symbol": clean_symbol, "requested_at": now}


def _load_research_conflict_queue() -> list[dict[str, Any]]:
    queue = _kv_read_json(RESEARCH_CONFLICT_QUEUE_KEY, {})
    if not isinstance(queue, dict):
        return []
    out: list[dict[str, Any]] = []
    max_age = max(1, RESEARCH_CONFLICT_QUEUE_MAX_AGE_HOURS) * 3600
    for item in queue.values():
        if not isinstance(item, dict):
            continue
        if str(item.get("status") or "PENDING").upper() not in {"PENDING", "RETRY"}:
            continue
        age = _age_seconds(item.get("requested_at"))
        if age is not None and age > max_age:
            continue
        mint = str(item.get("mint") or "").strip()
        if mint:
            out.append(dict(item))
    out.sort(key=lambda x: _parse_dt(x.get("requested_at")) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return out[: max(1, RESEARCH_CONFLICT_QUEUE_MAX_ITEMS)]


def _mark_research_conflict_queue_processed(mints: list[str], *, error: str | None = None) -> dict[str, Any]:
    clean_mints = {str(m or "").strip() for m in mints if str(m or "").strip()}
    if not clean_mints:
        return {"updated": 0}
    queue = _kv_read_json(RESEARCH_CONFLICT_QUEUE_KEY, {})
    if not isinstance(queue, dict):
        return {"updated": 0}
    now = _iso()
    updated = 0
    for mint in clean_mints:
        item = dict(queue.get(mint) or {})
        if not item:
            continue
        item["attempts"] = int(item.get("attempts") or 0) + 1
        item["last_attempt_at"] = now
        if error:
            item["status"] = "RETRY"
            item["last_error"] = str(error)[:240]
        else:
            item["status"] = "COMPLETE"
            item["last_completed_at"] = now
            item.pop("last_error", None)
        queue[mint] = item
        updated += 1
    _kv_write_json(RESEARCH_CONFLICT_QUEUE_KEY, queue)
    return {"updated": updated, "error": error}


def _conflict_queue_candidates(queue_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    mints = [str(item.get("mint") or "").strip() for item in queue_items if str(item.get("mint") or "").strip()]
    if not mints:
        return []
    by_mint = {str(item.get("mint") or "").strip(): dict(item) for item in queue_items}
    try:
        with get_conn() as conn:
            placeholders = ",".join("?" for _ in mints)
            rows = [
                dict(r)
                for r in conn.execute(
                    f"SELECT * FROM token_intelligence_current WHERE mint IN ({placeholders})",
                    tuple(mints),
                ).fetchall()
            ]
    except Exception:
        rows = []

    existing = {str(r.get("mint") or "").strip(): dict(r) for r in rows if str(r.get("mint") or "").strip()}
    candidates: list[dict[str, Any]] = []
    for mint in mints:
        queued = by_mint.get(mint) or {}
        row = existing.get(mint) or {}
        symbol = str(row.get("symbol") or queued.get("symbol") or "").strip().upper()
        if not symbol:
            continue
        liquidity = _f(row.get("liquidity"))
        volume_24h = _f(row.get("volume_24h_usd"))
        candidates.append({
            "mint": mint,
            "symbol": symbol,
            "source": "CONFLICT_SELF_HEAL_QUEUE",
            "score": max(_f(row.get("quality_score")), 70.0),
            "readiness_score": max(_f(row.get("pressure_score")), 65.0),
            "market_quality_score": max(_f(row.get("quality_score")), 70.0),
            "proof_status": "RESEARCH_RECHECK",
            "blocker_key": None,
            "queue_reason": queued.get("reason"),
            "queue_conflicts": queued.get("conflicts") or [],
            "proof_components": {
                "scanner": {
                    "market_quality_score": row.get("quality_score"),
                    "buy_pressure": row.get("buy_pressure_1h"),
                    "vol_acceleration": volume_24h / max(liquidity, 1.0),
                    "live_token_stats": {
                        "marketcap": row.get("marketcap"),
                        "liquidity": row.get("liquidity"),
                        "volume_24h": row.get("volume_24h_usd"),
                        "price_change_1h_percent": row.get("price_change_1h_percent"),
                        "price_change_24h_percent": row.get("price_change_24h_percent"),
                    },
                },
                "blocker_key": None,
            },
        })
    return candidates


def _candidate_symbol(candidate: dict[str, Any], token_stats: dict[str, Any] | None = None, known: dict[str, Any] | None = None) -> str:
    return str(
        (candidate or {}).get("symbol")
        or (token_stats or {}).get("symbol")
        or (known or {}).get("symbol")
        or ""
    ).strip().upper()


def _extract_chain_token_address(item: dict[str, Any]) -> tuple[str, str]:
    chain = str(item.get("chainId") or item.get("chain") or "").strip().lower()
    token_address = str(
        item.get("tokenAddress")
        or item.get("address")
        or ((item.get("baseToken") or {}).get("address") if isinstance(item.get("baseToken"), dict) else "")
        or ""
    ).strip()
    return chain, token_address


def _event_recent_exists(conn, *, mint: str, event_type: str, source: str, cutoff: str) -> bool:
    row = conn.execute(
        """
        SELECT id
        FROM memecoin_catalyst_events
        WHERE mint=?
          AND event_type=?
          AND COALESCE(source, '')=COALESCE(?, '')
          AND event_ts >= ?
        ORDER BY event_ts DESC
        LIMIT 1
        """,
        (mint, event_type, source, cutoff),
    ).fetchone()
    return bool(row)


def _insert_catalyst_event(conn, event: dict[str, Any], *, dedupe_minutes: int = CATALYST_DEDUPE_MINUTES) -> bool:
    mint = str(event.get("mint") or "").strip()
    event_type = str(event.get("event_type") or "").strip().upper()
    if not mint or not event_type:
        return False
    source = str(event.get("source") or "").strip()
    cutoff = _event_age_cutoff(dedupe_minutes)
    if _event_recent_exists(conn, mint=mint, event_type=event_type, source=source, cutoff=cutoff):
        return False
    conn.execute(
        """
        INSERT INTO memecoin_catalyst_events
        (mint, symbol, event_ts, event_type, source, confidence, headline, detail,
         narrative, marketcap, liquidity, volume_24h, buy_pressure, vol_acceleration,
         evidence_json, raw_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            mint,
            str(event.get("symbol") or "").upper(),
            event.get("event_ts") or _iso(),
            event_type,
            source,
            round(_f(event.get("confidence")), 2),
            event.get("headline"),
            event.get("detail"),
            event.get("narrative"),
            _f(event.get("marketcap")),
            _f(event.get("liquidity")),
            _f(event.get("volume_24h")),
            _f(event.get("buy_pressure")),
            _f(event.get("vol_acceleration")),
            json.dumps(event.get("evidence") or {}, separators=(",", ":")),
            json.dumps(event.get("raw") or {}, separators=(",", ":")),
        ),
    )
    return True


def _latest_catalysts_for_mints(mints: list[str], *, max_age_hours: int = 48) -> dict[str, dict[str, Any]]:
    clean = [str(m or "").strip() for m in mints if str(m or "").strip()]
    if not clean:
        return {}
    try:
        with get_conn() as conn:
            _ensure_schema(conn)
            placeholders = ",".join("?" for _ in clean)
            cutoff = _event_age_cutoff(max(1, int(max_age_hours)) * 60)
            rows = conn.execute(
                f"""
                WITH latest AS (
                    SELECT mint, MAX(id) AS max_id
                    FROM memecoin_catalyst_events
                    WHERE mint IN ({placeholders})
                      AND event_ts >= ?
                    GROUP BY mint
                ),
                counts AS (
                    SELECT mint, COUNT(*) AS event_count
                    FROM memecoin_catalyst_events
                    WHERE mint IN ({placeholders})
                      AND event_ts >= ?
                    GROUP BY mint
                )
                SELECT e.*, COALESCE(c.event_count, 0) AS event_count
                FROM memecoin_catalyst_events e
                INNER JOIN latest l ON l.max_id = e.id
                LEFT JOIN counts c ON c.mint = e.mint
                """,
                list(clean) + [cutoff] + list(clean) + [cutoff],
            ).fetchall()
    except Exception:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        item = dict(row)
        item["evidence"] = _json_load(item.pop("evidence_json", None), {})
        item["raw"] = _json_load(item.pop("raw_json", None), {})
        out[str(item.get("mint") or "")] = item
    return out


def build_memecoin_catalyst_payload(limit: int = 20) -> dict[str, Any]:
    try:
        with get_conn() as conn:
            _ensure_schema(conn)
            rows = conn.execute(
                """
                SELECT *
                FROM memecoin_catalyst_events
                ORDER BY event_ts DESC, confidence DESC
                LIMIT ?
                """,
                (max(1, min(int(limit), 100)),),
            ).fetchall()
            count_rows = conn.execute(
                """
                SELECT event_type, COUNT(*) AS n
                FROM memecoin_catalyst_events
                WHERE event_ts >= ?
                GROUP BY event_type
                ORDER BY n DESC, event_type
                """,
                (_event_age_cutoff(24 * 60),),
            ).fetchall()
    except Exception:
        return {
            "generated_at": _iso(),
            "summary": {"total_24h": 0, "by_type": {}, "state": "UNAVAILABLE"},
            "recent": [],
        }
    recent: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["evidence"] = _json_load(item.pop("evidence_json", None), {})
        item["raw"] = _json_load(item.pop("raw_json", None), {})
        recent.append(item)
    by_type = {str(row["event_type"]): int(row["n"] or 0) for row in count_rows}
    return {
        "generated_at": _iso(),
        "summary": {
            "total_24h": sum(by_type.values()),
            "by_type": by_type,
            "state": "ACTIVE" if by_type else "WAITING",
        },
        "recent": recent,
    }


def _dex_market(mint: str) -> dict[str, Any]:
    if not mint:
        return {}
    try:
        from data.dexscreener import fetch_token_snapshot  # type: ignore

        snap = fetch_token_snapshot(mint) or {}
    except Exception:
        snap = {}
    if not snap:
        return {}
    return {
        "symbol": snap.get("symbol"),
        "marketcap": _f(snap.get("market_cap") or snap.get("fdv")),
        "liquidity": _f(snap.get("liquidity")),
        "volume_24h": _f(snap.get("volume_24h")),
        "price": _f(snap.get("price")),
        "change_1h": _f(snap.get("change_1h")),
        "change_24h": _f(snap.get("change_24h")),
    }


def _outcome_context(mints: list[str]) -> dict[str, dict[str, Any]]:
    if not mints:
        return {}
    try:
        with get_conn() as conn:
            _ensure_schema(conn)
            placeholders = ",".join("?" for _ in mints)
            rows = conn.execute(
                f"""
                SELECT mint, outcome_label, max_return_pct, created_ts
                FROM runner_review_decisions
                WHERE mint IN ({placeholders})
                  AND created_ts >= datetime('now', '-30 days')
                ORDER BY id DESC
                """,
                mints,
            ).fetchall()
    except Exception:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        mint = str(row["mint"] or "")
        item = out.setdefault(
            mint,
            {"sample_n": 0, "good_n": 0, "bad_n": 0, "sum_max_return_pct": 0.0, "last_outcome_label": None},
        )
        label = str(row["outcome_label"] or "")
        item["sample_n"] += 1
        item["sum_max_return_pct"] += _f(row["max_return_pct"])
        if item["last_outcome_label"] is None and label:
            item["last_outcome_label"] = label
        if label.startswith("GOOD") or label == "WATCH_CONFIRMED":
            item["good_n"] += 1
        if label.startswith("BAD"):
            item["bad_n"] += 1
    for item in out.values():
        item["avg_max_return_pct"] = round(float(item["sum_max_return_pct"]) / max(1, int(item["sample_n"])), 2)
    return out


def _classify_narrative(symbol: str, known: dict[str, Any], profile: dict[str, Any]) -> tuple[str, str]:
    if known:
        return str(known.get("narrative") or "known_coin"), str(known.get("archetype") or "KNOWN_RUNNER")
    profile_label = str(profile.get("profile") or "").upper()
    sym = symbol.upper()
    if profile_label:
        return "established_runner", profile_label
    if sym in {"GIGA", "POPCAT", "MEW", "WIF", "BONK"}:
        return "blue_chip_meme", "ESTABLISHED_LEADER"
    if sym in {"WOJAK", "TROLL", "FARTCOIN"}:
        return "classic_meme_revival", "REVIVAL_RUNNER"
    if sym in {"JUP", "JTO", "PYTH", "RENDER"}:
        return "solana_infrastructure", "SPOT_BETA"
    return "emerging_meme", "UNPROVEN_RUNNER"


def _action_from_score(score: float, risk_score: float, runner_state: str, proof_status: str, blocker: str) -> str:
    if risk_score < 35 or blocker in {"rug_warning", "rug_danger", "rug_not_good", "market_quality_avoid"}:
        return "IGNORE"
    if runner_state == "RUNNER_EXTENSION_RISK":
        return "TOO_LATE"
    if score >= 78 and runner_state == "RUNNER_READY":
        return "PAPER_ENTRY"
    if score >= 70 and proof_status in {"PROOF_READY", "RESEARCH_ONLY"}:
        return "MANUAL_REVIEW"
    if score >= 55:
        return "WATCH"
    return "IGNORE"


def _good_coin_filter_v2(
    *,
    mint: str,
    symbol: str,
    known: dict[str, Any],
    profile: dict[str, Any],
    marketcap: float,
    liquidity: float,
    volume_24h: float,
    buy_pressure: float,
    market_quality: float,
    risk_score: float,
    blocker: str,
) -> dict[str, Any]:
    reasons: list[str] = []
    blockers: list[str] = []
    score = 0.0
    mint_known = str(mint or "").strip() in KNOWN_COIN_MEMORY
    symbol_memory_only = bool(known.get("symbol_memory_only"))
    established = bool(mint_known or profile)

    if mint_known:
        score += 24.0
        reasons.append("mint-level known memory")
    elif profile:
        score += 18.0
        reasons.append("established runner profile")
    elif symbol_memory_only:
        score += 8.0
        blockers.append("symbol memory only; CA needs confirmation")
    else:
        blockers.append("no durable history/community proof yet")

    if liquidity >= 100_000:
        score += 22.0
        reasons.append("deep liquidity")
    elif liquidity >= 35_000:
        score += 16.0
        reasons.append("tradable liquidity")
    elif liquidity >= 10_000:
        score += 8.0
        blockers.append("thin liquidity")
    else:
        blockers.append("liquidity too thin")

    if volume_24h >= 500_000:
        score += 20.0
        reasons.append("strong 24h volume")
    elif volume_24h >= 100_000:
        score += 15.0
        reasons.append("active 24h volume")
    elif volume_24h >= 35_000:
        score += 8.0
        blockers.append("volume only moderate")
    else:
        blockers.append("volume too quiet")

    if marketcap >= 1_000_000:
        score += 12.0
        reasons.append("established market cap")
    elif marketcap >= 350_000:
        score += 7.0
        blockers.append("small-cap risk")
    else:
        blockers.append("market cap too small or unknown")

    if market_quality >= 70:
        score += 12.0
        reasons.append("high market quality")
    elif market_quality >= 55:
        score += 8.0
        reasons.append("acceptable market quality")
    elif established and market_quality <= 0:
        score += 5.0
        blockers.append("quality score unavailable")
    else:
        blockers.append("market quality not proven")

    if buy_pressure >= 58:
        score += 6.0
        reasons.append("healthy buy pressure")
    elif buy_pressure and buy_pressure < 45:
        blockers.append("buy pressure weak")

    hard_risk = blocker in {"rug_warning", "rug_danger", "rug_not_good", "market_quality_avoid"} or risk_score < 40
    if hard_risk:
        score -= 35.0
        blockers.append(f"hard risk blocker: {blocker or 'risk_score_low'}")
    elif risk_score >= 65:
        score += 8.0
        reasons.append("risk gate acceptable")
    elif risk_score >= 50:
        score += 4.0
        blockers.append("risk is review-only")
    else:
        blockers.append("risk score too low")

    if not established and market_quality < 65 and volume_24h < 100_000:
        score -= 15.0
        blockers.append("unproven coin without enough activity")

    score = _clamp(score)
    if hard_risk or score < 45:
        status = "FAIL"
    elif score >= 72 and not symbol_memory_only:
        status = "PASS"
    else:
        status = "REVIEW"
    return {
        "score": round(score, 1),
        "status": status,
        "reasons": reasons[:5],
        "blockers": blockers[:6],
        "headline": (
            "Good coin filter passes."
            if status == "PASS"
            else "Good coin filter needs review."
            if status == "REVIEW"
            else "Good coin filter blocks this name."
        ),
    }


def _catalyst_strength(
    *,
    catalyst_event: dict[str, Any] | None,
    runner_state: str,
    buy_pressure: float,
    vol_accel: float,
    volume_24h: float,
    liquidity: float,
    price_change_1h: float,
    price_change_24h: float,
) -> dict[str, Any]:
    event_type = str((catalyst_event or {}).get("event_type") or "").upper()
    confidence = _f((catalyst_event or {}).get("confidence"))
    type_bonus = {
        "LAST_BLOCKER_CLEARED": 16.0,
        "RUNNER_READY": 14.0,
        "VOLUME_WAKEUP": 13.0,
        "KNOWN_COIN_WAKEUP": 11.0,
        "LIQUIDITY_TURNOVER": 9.0,
        "DEX_BOOST": 8.0,
        "PRICE_BREAKOUT": 7.0,
        "PROFILE_UPDATE": 4.0,
    }.get(event_type, 0.0)
    vol_liq = volume_24h / liquidity if liquidity > 0 else 0.0
    score = (
        confidence * 0.58
        + type_bonus
        + min(vol_accel * 2.4, 18.0)
        + max(0.0, buy_pressure - 50.0) * 0.55
        + min(vol_liq * 3.0, 12.0)
        + min(max(price_change_1h, price_change_24h / 4.0), 10.0)
        + (8.0 if runner_state == "RUNNER_READY" else 0.0)
    )
    score = _clamp(score)
    label = "STRONG" if score >= 78 else "MEDIUM" if score >= 60 else "WEAK" if score >= 38 else "NONE"
    return {
        "score": round(score, 1),
        "label": label,
        "event_type": event_type or None,
        "headline": (
            "Strong catalyst active now."
            if label == "STRONG"
            else "Medium catalyst; watch confirmation."
            if label == "MEDIUM"
            else "Catalyst is weak or background only."
            if label == "WEAK"
            else "No actionable catalyst yet."
        ),
    }


def _narrative_hooks_v2(
    *,
    symbol: str,
    narrative: str,
    archetype: str,
    memory_type: str,
    known: dict[str, Any],
    profile: dict[str, Any],
    catalyst_event: dict[str, Any] | None,
    catalyst_strength: dict[str, Any],
    runner_state: str,
    proof_status: str,
    marketcap: float,
    volume_24h: float,
    liquidity: float,
    buy_pressure: float,
    vol_accel: float,
    price_change_1h: float,
    price_change_24h: float,
    cluster_ctx: dict[str, Any],
) -> list[dict[str, Any]]:
    hooks: list[dict[str, Any]] = []

    def add(hook: str, strength: float, reason: str) -> None:
        if not reason:
            return
        hooks.append({"hook": hook, "strength": round(_clamp(strength), 1), "reason": reason})

    text = " ".join([symbol, narrative, archetype, memory_type, str(known.get("why_it_matters") or ""), str(profile.get("thesis") or "")]).lower()
    event_type = str((catalyst_event or {}).get("event_type") or "").upper()
    catalyst_label = str(catalyst_strength.get("label") or "").upper()
    repeat_score = _f(cluster_ctx.get("repeat_operator_score"))
    wallet_trust = _f(cluster_ctx.get("wallet_trust_score"), 50.0)
    accumulation = _f(cluster_ctx.get("accumulation_score"))

    if known:
        add("known_memory", 86.0, "Token has explicit system memory instead of being a cold unknown.")
    if profile:
        add("established_runner_profile", 74.0, "Established-runner profile gives the system prior context to compare against.")
    if any(word in text for word in ("revival", "dead", "comeback", "recovery", "wakeup")):
        add("revival_setup", 78.0, "Narrative resembles a former runner waking back up from a quiet base.")
    if any(word in text for word in ("blue_chip", "leader", "cult", "culture", "community")):
        add("community_memory", 76.0, "Community or meme-memory hook can persist longer than a one-candle catalyst.")
    if any(word in text for word in ("solana", "spot_beta", "infrastructure", "jup", "jto", "pyth")):
        add("solana_beta", 72.0, "Solana-beta names can catch broader SOL ecosystem flow.")
    if vol_accel >= 4.0 or volume_24h >= 250_000:
        add("volume_wakeup", min(88.0, 54.0 + vol_accel * 6.0 + min(volume_24h / 100_000.0, 12.0)), f"Volume is waking up: accel {vol_accel:.2f}, 24h volume {volume_24h:,.0f}.")
    if price_change_1h >= 8.0 or price_change_24h >= 25.0:
        add("price_breakout", min(82.0, 48.0 + max(price_change_1h, price_change_24h / 3.0)), f"Price has breakout pressure: 1h {price_change_1h:.1f}%, 24h {price_change_24h:.1f}%.")
    if repeat_score >= 45.0 or wallet_trust >= 62.0:
        add("repeat_wallets", min(85.0, 40.0 + repeat_score * 0.45 + max(wallet_trust - 50.0, 0.0)), "Repeat or trusted wallets are connected to the setup.")
    if accumulation >= 55.0:
        add("accumulation_cluster", min(82.0, 42.0 + accumulation * 0.55), "Wallet cluster data suggests accumulation instead of pure noise.")
    if event_type:
        base = 68.0 if catalyst_label in {"STRONG", "MEDIUM"} else 52.0
        add("fresh_catalyst", base + min(_f((catalyst_event or {}).get("confidence")) * 0.18, 15.0), f"Fresh catalyst event detected: {event_type.replace('_', ' ').lower()}.")
    if runner_state == "RUNNER_READY" or proof_status in {"PROOF_READY", "RESEARCH_ONLY"}:
        add("system_alignment", 70.0, "Runner/proof state is aligned enough for research attention.")
    if marketcap >= 3_000_000 and liquidity >= 25_000:
        add("tradable_base", min(78.0, 45.0 + min(marketcap / 1_000_000.0, 18.0) + min(liquidity / 20_000.0, 15.0)), "Market cap and liquidity are large enough to behave like an established name.")
    if buy_pressure >= 58.0:
        add("active_bid", min(80.0, 42.0 + buy_pressure * 0.55), f"Buy pressure is active at {buy_pressure:.0f}.")

    hooks.sort(key=lambda item: (_f(item.get("strength")), str(item.get("hook") or "")), reverse=True)
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for hook in hooks:
        key = str(hook.get("hook") or "")
        if key and key not in seen:
            deduped.append(hook)
            seen.add(key)
        if len(deduped) >= 6:
            break
    return deduped


def _attention_persistence_v2(
    *,
    hooks: list[dict[str, Any]],
    known: dict[str, Any],
    profile: dict[str, Any],
    volume_24h: float,
    liquidity: float,
    buy_pressure: float,
    vol_accel: float,
    cluster_ctx: dict[str, Any],
) -> dict[str, Any]:
    hook_score = sum(_f(h.get("strength")) for h in hooks[:4]) / max(1, min(len(hooks), 4))
    hook_diversity = min(len(hooks) * 5.0, 24.0)
    repeat_score = _f(cluster_ctx.get("repeat_operator_score"))
    wallet_trust = _f(cluster_ctx.get("wallet_trust_score"), 50.0)
    vol_liq = volume_24h / liquidity if liquidity > 0 else 0.0
    score = (
        hook_score * 0.44
        + hook_diversity
        + (10.0 if known else 0.0)
        + (6.0 if profile else 0.0)
        + min(vol_accel * 2.5, 12.0)
        + min(vol_liq * 2.5, 10.0)
        + max(buy_pressure - 50.0, 0.0) * 0.45
        + min(repeat_score * 0.12, 8.0)
        + min(max(wallet_trust - 50.0, 0.0) * 0.25, 7.0)
    )
    score = _clamp(score)
    label = "DURABLE" if score >= 78 else "ACTIVE" if score >= 62 else "FRAGILE" if score >= 42 else "ONE_SHOT"
    return {
        "score": round(score, 1),
        "label": label,
        "reason": (
            "Multiple durable hooks are present."
            if label == "DURABLE"
            else "Attention is active but still needs confirmation."
            if label == "ACTIVE"
            else "Attention can fade quickly unless flow improves."
            if label == "FRAGILE"
            else "No persistent attention base detected yet."
        ),
    }


def _catalyst_quality_v2(
    *,
    catalyst_event: dict[str, Any] | None,
    catalyst_strength: dict[str, Any],
    buy_pressure: float,
    vol_accel: float,
    volume_24h: float,
    liquidity: float,
    cluster_ctx: dict[str, Any],
) -> dict[str, Any]:
    event_type = str((catalyst_event or {}).get("event_type") or catalyst_strength.get("event_type") or "").upper()
    structural_bonus = {
        "LAST_BLOCKER_CLEARED": 22.0,
        "RUNNER_READY": 20.0,
        "KNOWN_COIN_WAKEUP": 17.0,
        "VOLUME_WAKEUP": 15.0,
        "LIQUIDITY_TURNOVER": 12.0,
        "DEX_BOOST": 8.0,
        "PRICE_BREAKOUT": 7.0,
        "PROFILE_UPDATE": 5.0,
    }.get(event_type, 0.0)
    vol_liq = volume_24h / liquidity if liquidity > 0 else 0.0
    confirmation = (
        max(buy_pressure - 50.0, 0.0) * 0.55
        + min(vol_accel * 3.0, 18.0)
        + min(vol_liq * 2.2, 10.0)
        + min(_f(cluster_ctx.get("accumulation_score")) * 0.12, 8.0)
    )
    score = _clamp(_f(catalyst_strength.get("score")) * 0.62 + structural_bonus + confirmation)
    label = "STRUCTURAL" if score >= 82 else "CONFIRMED_FLOW" if score >= 66 else "HYPE" if score >= 48 else "BACKGROUND" if event_type else "STALE"
    return {
        "score": round(score, 1),
        "label": label,
        "event_type": event_type or None,
        "reason": (
            "Catalyst changed the setup, not just the candle."
            if label == "STRUCTURAL"
            else "Catalyst has market-flow confirmation."
            if label == "CONFIRMED_FLOW"
            else "Catalyst exists, but confirmation is still thin."
            if label == "HYPE"
            else "Catalyst is background context only."
            if label == "BACKGROUND"
            else "No fresh catalyst quality signal."
        ),
    }


def _narrative_contradictions_v2(
    *,
    hooks: list[dict[str, Any]],
    attention: dict[str, Any],
    catalyst_quality: dict[str, Any],
    good_coin: dict[str, Any],
    too_late: dict[str, Any],
    risk_score: float,
    liquidity: float,
    volume_24h: float,
    buy_pressure: float,
    vol_accel: float,
    price_change_1h: float,
    price_change_24h: float,
    cluster_ctx: dict[str, Any],
    entry_signal: dict[str, Any],
) -> list[dict[str, Any]]:
    contradictions: list[dict[str, Any]] = []

    def add(key: str, severity: str, detail: str) -> None:
        contradictions.append({"key": key, "severity": severity, "detail": detail})

    attention_label = str(attention.get("label") or "")
    catalyst_label = str(catalyst_quality.get("label") or "")
    distribution = _f(cluster_ctx.get("distribution_risk_score"))
    repeat_score = _f(cluster_ctx.get("repeat_operator_score"))
    wallet_trust = _f(cluster_ctx.get("wallet_trust_score"), 50.0)
    guard_status = str(entry_signal.get("guard_status") or "").upper()

    if hooks and volume_24h < 40_000:
        add("story_without_volume", "MEDIUM", "Narrative hook exists but 24h volume is still thin.")
    if liquidity < 12_000:
        add("thin_liquidity", "HIGH", "Liquidity is too thin for clean execution.")
    if attention_label in {"DURABLE", "ACTIVE"} and buy_pressure < 50:
        add("attention_without_bid", "MEDIUM", "Attention score is good, but current buy pressure is below neutral.")
    if catalyst_label in {"STRUCTURAL", "CONFIRMED_FLOW"} and guard_status == "STALE":
        add("fresh_story_stale_data", "HIGH", "Catalyst looks useful but token stats are stale.")
    if price_change_1h >= 12 and vol_accel < 2.2:
        add("price_without_fresh_volume", "MEDIUM", "Price is moving without enough fresh volume acceleration.")
    if price_change_24h >= 65 and str(too_late.get("label") or "") != "NORMAL":
        add("chase_risk", "HIGH", "The move may already be stretched for a fresh entry.")
    if distribution >= 55:
        add("distribution_risk", "HIGH", "Wallet cluster shows elevated distribution risk.")
    if repeat_score >= 45 and wallet_trust < 50:
        add("repeat_but_untrusted_wallets", "MEDIUM", "Repeat wallets are present but historical trust is still weak.")
    if str(good_coin.get("status") or "") == "FAIL":
        add("good_coin_filter_failed", "HIGH", "Good-coin filter currently blocks this name.")
    if risk_score < 45:
        add("risk_score_low", "HIGH", "Risk score is below the usable threshold.")
    return contradictions[:6]


def _comparable_runner_v2(
    *,
    symbol: str,
    narrative: str,
    archetype: str,
    memory_type: str,
    known: dict[str, Any],
    hooks: list[dict[str, Any]],
) -> dict[str, Any]:
    text = " ".join([symbol, narrative, archetype, memory_type, str(known.get("archetype") or ""), str(known.get("memory_type") or "")]).lower()
    hook_names = {str(h.get("hook") or "").lower() for h in hooks}
    comparable = "UNMAPPED_RUNNER"
    confidence = 34.0
    if symbol in {"JUP", "JTO", "PYTH"} or "spot_beta" in text or "infrastructure" in text:
        comparable, confidence = "JUP_SPOT_BETA", 78.0
    elif symbol in {"FARTCOIN", "WIF", "POPCAT", "BONK", "GIGA", "MEW", "PENGU"} or "blue_chip" in text or "leader" in text:
        comparable, confidence = "BLUECHIP_MEME_LEADER", 76.0
    elif symbol == "USDUC" or "spike" in text or "giveback" in text:
        comparable, confidence = "USDUC_SPIKE_GIVEBACK", 74.0
    elif symbol == "TROLL" or "troll" in text:
        comparable, confidence = "TROLL_REVIVAL", 76.0
    elif symbol == "WOJAK" or "revival_setup" in hook_names or "revival" in text:
        comparable, confidence = "WOJAK_REVIVAL", 70.0
    elif symbol == "AURA" or "culture" in text or "cult" in text:
        comparable, confidence = "AURA_CULTURE_RUNNER", 68.0
    elif "volume_wakeup" in hook_names and "known_memory" in hook_names:
        comparable, confidence = "KNOWN_COIN_WAKEUP", 64.0
    elif "volume_wakeup" in hook_names:
        comparable, confidence = "VOLUME_WAKEUP_RUNNER", 54.0
    if known:
        confidence += 8.0
    if len(hooks) >= 4:
        confidence += 5.0
    return {"runner": comparable, "confidence": round(_clamp(confidence), 1)}


def _sustainability_v2(
    *,
    attention: dict[str, Any],
    catalyst_quality: dict[str, Any],
    contradictions: list[dict[str, Any]],
    liquidity: float,
    volume_24h: float,
    buy_pressure: float,
    vol_accel: float,
    too_late: dict[str, Any],
    cluster_ctx: dict[str, Any],
    entry_signal: dict[str, Any],
) -> dict[str, Any]:
    distribution = _f(cluster_ctx.get("distribution_risk_score"))
    checks = [
        {"check": "volume_acceleration", "pass": vol_accel >= 3.0, "detail": f"vol accel {vol_accel:.2f}"},
        {"check": "buy_pressure", "pass": buy_pressure >= 53.0, "detail": f"buy pressure {buy_pressure:.0f}"},
        {"check": "liquidity_depth", "pass": liquidity >= 20_000, "detail": f"liquidity {liquidity:,.0f}"},
        {"check": "volume_liquidity_turnover", "pass": (volume_24h / liquidity if liquidity > 0 else 0.0) >= 1.2, "detail": f"turnover {(volume_24h / liquidity if liquidity > 0 else 0.0):.1f}x"},
        {"check": "distribution_control", "pass": distribution < 50.0, "detail": f"distribution {distribution:.0f}"},
        {"check": "not_extended", "pass": str(too_late.get("label") or "") == "NORMAL", "detail": f"late risk {too_late.get('label') or 'UNKNOWN'}"},
        {"check": "fresh_data", "pass": str(entry_signal.get("guard_status") or "").upper() != "STALE", "detail": f"data {entry_signal.get('guard_status') or 'unknown'}"},
    ]
    passed = sum(1 for check in checks if check["pass"])
    high_contradictions = sum(1 for item in contradictions if str(item.get("severity") or "").upper() == "HIGH")
    score = (
        _f(attention.get("score")) * 0.36
        + _f(catalyst_quality.get("score")) * 0.32
        + passed * 7.0
        - high_contradictions * 10.0
        - max(0, len(contradictions) - high_contradictions) * 4.0
    )
    score = _clamp(score)
    label = "SUSTAINABLE" if score >= 78 and high_contradictions == 0 else "NEEDS_CONFIRMATION" if score >= 58 else "FRAGILE" if score >= 40 else "FADE_RISK"
    return {"score": round(score, 1), "label": label, "checks": checks}


def _build_narrative_intelligence_v2(
    *,
    symbol: str,
    narrative: str,
    archetype: str,
    memory_type: str,
    known: dict[str, Any],
    profile: dict[str, Any],
    catalyst_event: dict[str, Any] | None,
    catalyst_strength: dict[str, Any],
    good_coin: dict[str, Any],
    too_late: dict[str, Any],
    entry_signal: dict[str, Any],
    runner_state: str,
    proof_status: str,
    marketcap: float,
    liquidity: float,
    volume_24h: float,
    buy_pressure: float,
    vol_accel: float,
    price_change_1h: float,
    price_change_24h: float,
    risk_score: float,
    cluster_ctx: dict[str, Any],
) -> dict[str, Any]:
    hooks = _narrative_hooks_v2(
        symbol=symbol,
        narrative=narrative,
        archetype=archetype,
        memory_type=memory_type,
        known=known,
        profile=profile,
        catalyst_event=catalyst_event,
        catalyst_strength=catalyst_strength,
        runner_state=runner_state,
        proof_status=proof_status,
        marketcap=marketcap,
        volume_24h=volume_24h,
        liquidity=liquidity,
        buy_pressure=buy_pressure,
        vol_accel=vol_accel,
        price_change_1h=price_change_1h,
        price_change_24h=price_change_24h,
        cluster_ctx=cluster_ctx,
    )
    attention = _attention_persistence_v2(
        hooks=hooks,
        known=known,
        profile=profile,
        volume_24h=volume_24h,
        liquidity=liquidity,
        buy_pressure=buy_pressure,
        vol_accel=vol_accel,
        cluster_ctx=cluster_ctx,
    )
    catalyst_quality = _catalyst_quality_v2(
        catalyst_event=catalyst_event,
        catalyst_strength=catalyst_strength,
        buy_pressure=buy_pressure,
        vol_accel=vol_accel,
        volume_24h=volume_24h,
        liquidity=liquidity,
        cluster_ctx=cluster_ctx,
    )
    contradictions = _narrative_contradictions_v2(
        hooks=hooks,
        attention=attention,
        catalyst_quality=catalyst_quality,
        good_coin=good_coin,
        too_late=too_late,
        risk_score=risk_score,
        liquidity=liquidity,
        volume_24h=volume_24h,
        buy_pressure=buy_pressure,
        vol_accel=vol_accel,
        price_change_1h=price_change_1h,
        price_change_24h=price_change_24h,
        cluster_ctx=cluster_ctx,
        entry_signal=entry_signal,
    )
    comparable = _comparable_runner_v2(
        symbol=symbol,
        narrative=narrative,
        archetype=archetype,
        memory_type=memory_type,
        known=known,
        hooks=hooks,
    )
    sustainability = _sustainability_v2(
        attention=attention,
        catalyst_quality=catalyst_quality,
        contradictions=contradictions,
        liquidity=liquidity,
        volume_24h=volume_24h,
        buy_pressure=buy_pressure,
        vol_accel=vol_accel,
        too_late=too_late,
        cluster_ctx=cluster_ctx,
        entry_signal=entry_signal,
    )
    first_hook = str((hooks[0] or {}).get("hook") or "no dominant hook") if hooks else "no dominant hook"
    note = (
        f"{attention['label'].lower()} attention via {first_hook.replace('_', ' ')}; "
        f"catalyst {catalyst_quality['label'].lower()}; "
        f"sustainability {sustainability['label'].lower()}."
    )
    return {
        "hooks": hooks,
        "attention_persistence_score": attention["score"],
        "attention_persistence_label": attention["label"],
        "attention_reason": attention["reason"],
        "catalyst_quality_score": catalyst_quality["score"],
        "catalyst_quality_label": catalyst_quality["label"],
        "catalyst_quality_reason": catalyst_quality["reason"],
        "contradictions": contradictions,
        "comparable_runner": comparable["runner"],
        "comparable_runner_confidence": comparable["confidence"],
        "sustainability_score": sustainability["score"],
        "sustainability_label": sustainability["label"],
        "sustainability_checks": sustainability["checks"],
        "research_note": note,
    }


def _signal_quality_tier(
    *,
    execution_alignment: dict[str, Any],
    narrative_intel: dict[str, Any],
    good_coin: dict[str, Any],
    too_late: dict[str, Any],
    risk_score: float,
    liquidity: float,
    volume_24h: float,
    buy_pressure: float,
    vol_accel: float,
) -> dict[str, Any]:
    blockers: list[str] = []
    reasons: list[str] = []
    score = 0.0
    if str(good_coin.get("status") or "") == "PASS":
        score += 18.0
        reasons.append("good coin pass")
    else:
        blockers.append("good_coin_not_pass")
    if str(execution_alignment.get("state") or "") == "DEPLOYABLE_NOW":
        score += 24.0
        reasons.append("deployable alignment")
    elif str(execution_alignment.get("state") or "") == "PAPER_ENTRY_NOW":
        score += 16.0
        reasons.append("paper-ready alignment")
    elif str(execution_alignment.get("state") or "") == "RESEARCH_ENTRY_NOW":
        score += 9.0
        blockers.append("execution_not_deployable")
    attention = str(narrative_intel.get("attention_persistence_label") or "")
    catalyst = str(narrative_intel.get("catalyst_quality_label") or "")
    sustain = str(narrative_intel.get("sustainability_label") or "")
    if attention == "DURABLE":
        score += 13.0
        reasons.append("durable attention")
    if catalyst == "STRUCTURAL":
        score += 13.0
        reasons.append("structural catalyst")
    elif catalyst == "CONFIRMED_FLOW":
        score += 8.0
    if sustain == "SUSTAINABLE":
        score += 12.0
        reasons.append("sustainable setup")
    elif sustain in {"FRAGILE", "FADE_RISK"}:
        blockers.append("sustainability_weak")
    if risk_score >= 70:
        score += 8.0
    else:
        blockers.append("risk_not_clean")
    if liquidity >= 150_000 and volume_24h >= 175_000:
        score += 8.0
    else:
        blockers.append("liquidity_or_volume_thin")
    if buy_pressure >= 52 or vol_accel >= 4:
        score += 4.0
    else:
        blockers.append("flow_confirmation_thin")
    contradictions = list(narrative_intel.get("contradictions") or [])
    high_contradictions = [c for c in contradictions if str(c.get("severity") or "").upper() == "HIGH"]
    score -= len(high_contradictions) * 10.0
    score -= max(0, len(contradictions) - len(high_contradictions)) * 4.0
    for item in high_contradictions[:3]:
        blockers.append(str(item.get("key") or "high_contradiction"))
    if str(too_late.get("label") or "") != "NORMAL":
        blockers.append("too_late_not_normal")
        score -= 8.0
    score = _clamp(score)
    if score >= 82 and not blockers:
        tier = "A_PLUS"
    elif score >= 70 and len(blockers) <= 1:
        tier = "A"
    elif score >= 58:
        tier = "B"
    elif score >= 42:
        tier = "C"
    else:
        tier = "REJECT"
    return {
        "tier": tier,
        "score": round(score, 1),
        "reasons": reasons[:6],
        "blockers": blockers[:8],
    }


def _too_late_detector(
    *,
    runner_state: str,
    marketcap: float,
    liquidity: float,
    volume_24h: float,
    buy_pressure: float,
    vol_accel: float,
    price_change_1h: float,
    price_change_24h: float,
    catalyst_label: str,
) -> dict[str, Any]:
    reasons: list[str] = []
    score = 0.0
    if runner_state == "RUNNER_EXTENSION_RISK":
        score += 45.0
        reasons.append("runner policy says extension risk")
    if price_change_1h >= 35:
        score += 24.0
        reasons.append(f"1h move already {price_change_1h:.1f}%")
    elif price_change_1h >= 18:
        score += 12.0
        reasons.append(f"1h move stretched {price_change_1h:.1f}%")
    if price_change_24h >= 120:
        score += 28.0
        reasons.append(f"24h move already {price_change_24h:.1f}%")
    elif price_change_24h >= 65:
        score += 16.0
        reasons.append(f"24h move stretched {price_change_24h:.1f}%")
    if buy_pressure and buy_pressure < 45 and (price_change_1h > 8 or price_change_24h > 25):
        score += 18.0
        reasons.append("price expanded while buy pressure faded")
    if vol_accel < 2 and (price_change_1h > 12 or price_change_24h > 40):
        score += 12.0
        reasons.append("price expansion lacks fresh volume acceleration")
    vol_liq = volume_24h / liquidity if liquidity > 0 else 0.0
    if vol_liq >= 10:
        score += 10.0
        reasons.append("very high turnover can mark exhaustion")
    if marketcap >= 50_000_000 and catalyst_label in {"WEAK", "NONE"}:
        score += 8.0
        reasons.append("large cap needs stronger catalyst for fresh entry")
    score = _clamp(score)
    label = "EXTENDED" if score >= 70 else "ELEVATED" if score >= 45 else "NORMAL"
    return {"score": round(score, 1), "label": label, "reasons": reasons[:5]}


def _conviction_band(
    *,
    research_score: float,
    good_coin: dict[str, Any],
    catalyst: dict[str, Any],
    too_late: dict[str, Any],
    risk_score: float,
    tradeability_score: float,
    runner_state: str,
    proof_status: str,
) -> str:
    good_status = str(good_coin.get("status") or "")
    catalyst_label = str(catalyst.get("label") or "")
    too_late_label = str(too_late.get("label") or "")
    if good_status == "FAIL" or risk_score < 40:
        return "IGNORE"
    if too_late_label == "EXTENDED" or runner_state == "RUNNER_EXTENSION_RISK":
        return "TOO_LATE"
    if (
        good_status == "PASS"
        and catalyst_label == "STRONG"
        and research_score >= 78
        and risk_score >= 55
        and tradeability_score >= 55
        and too_late_label == "NORMAL"
    ):
        return "BUYABLE"
    if good_status in {"PASS", "REVIEW"} and catalyst_label in {"STRONG", "MEDIUM"} and research_score >= 65:
        return "TRIGGERED"
    if good_status in {"PASS", "REVIEW"} and (research_score >= 52 or proof_status == "RESEARCH_ONLY"):
        return "WATCH"
    return "IGNORE"


def _action_from_conviction(conviction: str, runner_state: str, proof_status: str, research_score: float) -> str:
    if conviction == "BUYABLE":
        return "PAPER_ENTRY" if runner_state == "RUNNER_READY" else "MANUAL_REVIEW"
    if conviction == "TRIGGERED":
        return "MANUAL_REVIEW" if research_score >= 70 or proof_status in {"PROOF_READY", "RESEARCH_ONLY"} else "WATCH"
    if conviction == "WATCH":
        return "WATCH"
    if conviction == "TOO_LATE":
        return "TOO_LATE"
    return "IGNORE"


def _entry_timing_signal(
    *,
    good_coin: dict[str, Any],
    catalyst: dict[str, Any],
    too_late: dict[str, Any],
    research_score: float,
    risk_score: float,
    tradeability_score: float,
    buy_pressure: float,
    vol_accel: float,
    volume_24h: float,
    liquidity: float,
    price_change_1h: float,
    price_change_24h: float,
    runner_state: str,
    proof_status: str,
    blocker: str,
    cluster_ctx: dict[str, Any],
) -> dict[str, Any]:
    good_status = str(good_coin.get("status") or "").upper()
    catalyst_label = str(catalyst.get("label") or "").upper()
    too_late_label = str(too_late.get("label") or "").upper()
    cluster_state = str(cluster_ctx.get("accumulation_state") or "QUIET").upper()
    distribution = _f(cluster_ctx.get("distribution_risk_score"))
    repeat_score = _f(cluster_ctx.get("repeat_operator_score"))
    wallet_trust = _f(cluster_ctx.get("wallet_trust_score"), 50.0)
    accumulation = _f(cluster_ctx.get("accumulation_score"))
    vol_liq = volume_24h / liquidity if liquidity > 0 else 0.0

    reasons: list[str] = []
    blockers: list[str] = []
    hard_blockers: list[str] = []

    if good_status == "PASS":
        reasons.append("good coin filter passes")
    elif good_status == "REVIEW":
        blockers.append("good_coin_needs_review")
    else:
        hard_blockers.append("good_coin_failed")

    if catalyst_label == "STRONG":
        reasons.append("strong catalyst active")
    elif catalyst_label == "MEDIUM":
        reasons.append("medium catalyst active")
    else:
        blockers.append("catalyst_not_confirmed")

    if buy_pressure >= 58:
        reasons.append(f"buy pressure strong ({buy_pressure:.0f})")
    elif buy_pressure < 50:
        blockers.append("buy_pressure_low")

    if vol_accel >= 4:
        reasons.append(f"volume acceleration active ({vol_accel:.2f})")
    elif vol_accel < 2.2:
        blockers.append("vol_acceleration_low")

    if tradeability_score >= 58:
        reasons.append("tradeability/liquidity are usable")
    elif liquidity < 10_000 or tradeability_score < 42:
        hard_blockers.append("tradeability_low")
    else:
        blockers.append("tradeability_needs_improvement")

    if risk_score < 45:
        hard_blockers.append("risk_too_high")
    elif risk_score < 58:
        blockers.append("risk_not_clean")

    if distribution >= 65:
        hard_blockers.append("distribution_pressure")
    elif distribution >= 45:
        blockers.append("distribution_rising")

    if too_late_label == "EXTENDED":
        hard_blockers.append("too_extended")
    elif too_late_label == "ELEVATED":
        blockers.append("extension_elevated")

    if cluster_state in {"ACCUMULATING", "EARLY_CLUSTER"} and distribution < 50:
        reasons.append(f"wallet cluster {cluster_state.lower().replace('_', ' ')}")
    if repeat_score >= 54 and distribution < 50:
        reasons.append(f"repeat-wallet score {repeat_score:.0f}")
    elif repeat_score > 0 and wallet_trust < 52:
        blockers.append("wallet_trust_thin")
    if wallet_trust >= 62 and distribution < 50:
        reasons.append(f"wallet trust {wallet_trust:.0f}")

    if runner_state == "RUNNER_READY":
        reasons.append("runner policy is ready")
    elif blocker:
        blockers.append(str(blocker))

    entry_score = _clamp(
        research_score * 0.30
        + _f(good_coin.get("score")) * 0.16
        + _f(catalyst.get("score")) * 0.20
        + tradeability_score * 0.12
        + risk_score * 0.10
        + min(max(buy_pressure - 45.0, 0.0), 35.0) * 0.30
        + min(max(vol_accel, 0.0), 12.0) * 1.4
        + min(repeat_score, 80.0) * 0.06
        + max(wallet_trust - 50.0, 0.0) * 0.15
        + min(vol_liq, 6.0) * 1.1
        - distribution * 0.28
        - _f(too_late.get("score")) * 0.18
    )

    unique_blockers: list[str] = []
    for key in hard_blockers + blockers:
        clean = str(key or "").strip()
        if clean and clean not in unique_blockers:
            unique_blockers.append(clean)
    unique_reasons: list[str] = []
    for reason in reasons:
        clean = str(reason or "").strip()
        if clean and clean not in unique_reasons:
            unique_reasons.append(clean)

    if "distribution_pressure" in hard_blockers:
        state = "EXIT_PRESSURE"
        instruction = "Do not enter; distribution pressure is active. Only consider exits or wait for sell pressure to reset."
    elif "too_extended" in hard_blockers:
        state = "AVOID_CHASE"
        instruction = "Avoid chasing. Wait for a reset, base, or fresh volume confirmation after cooldown."
    elif hard_blockers:
        state = "WAIT"
        instruction = "No entry yet. Hard quality/risk blockers must clear first."
    elif entry_score >= 78 and catalyst_label in {"STRONG", "MEDIUM"} and (vol_accel >= 3 or buy_pressure >= 56 or repeat_score >= 54):
        state = "ENTRY_NOW"
        instruction = "Entry window is active; use manual risk rules, size control, and invalidate fast if the trigger fades."
    elif entry_score >= 62 and good_status in {"PASS", "REVIEW"}:
        state = "ARMED"
        instruction = "Setup is armed. Wait for the last blocker to clear before entry."
    elif too_late_label == "ELEVATED" and price_change_1h >= 12:
        state = "AVOID_CHASE"
        instruction = "Momentum exists, but extension is elevated. Wait for a cleaner pullback or base."
    else:
        state = "WAIT"
        instruction = "Keep watching. The setup is not timed cleanly yet."

    last_blocker = unique_blockers[0] if unique_blockers else None
    if state == "ENTRY_NOW":
        last_blocker = None
    return {
        "state": state,
        "score": round(entry_score, 1),
        "last_blocker": last_blocker,
        "blockers": unique_blockers[:6],
        "reasons": unique_reasons[:6],
        "instruction": instruction,
        "inputs": {
            "buy_pressure": round(buy_pressure, 1),
            "vol_acceleration": round(vol_accel, 2),
            "vol_liq_ratio": round(vol_liq, 2),
            "price_change_1h": round(price_change_1h, 1),
            "price_change_24h": round(price_change_24h, 1),
            "distribution_risk_score": round(distribution, 1),
            "repeat_operator_score": round(repeat_score, 1),
            "wallet_trust_score": round(wallet_trust, 1),
            "proof_status": proof_status,
            "runner_state": runner_state,
        },
    }


def _apply_entry_freshness_guard(entry_signal: dict[str, Any], token_stats: dict[str, Any]) -> dict[str, Any]:
    guarded = dict(entry_signal or {})
    token_stats_ts = token_stats.get("ts_utc")
    data_age = _age_seconds(token_stats_ts)
    max_age_seconds = max(60, int(ENTRY_SIGNAL_MAX_DATA_AGE_MINUTES) * 60)
    guarded["data_age_seconds"] = round(data_age, 1) if data_age is not None else None
    guarded["token_stats_ts_utc"] = token_stats_ts
    guarded["guard_status"] = "FRESH" if data_age is not None and data_age <= max_age_seconds else "STALE"
    if guarded.get("state") == "ENTRY_NOW" and guarded["guard_status"] != "FRESH":
        blockers = list(guarded.get("blockers") or [])
        if "data_stale" not in blockers:
            blockers.insert(0, "data_stale")
        guarded["state"] = "ARMED"
        guarded["last_blocker"] = "data_stale"
        guarded["blockers"] = blockers[:6]
        guarded["instruction"] = "Setup is armed, but fresh token stats are required before treating this as ENTRY_NOW."
    return guarded


def _execution_alignment_signal(
    *,
    action: str,
    conviction: str,
    entry_signal: dict[str, Any],
    proof_status: str,
    runner_state: str,
    blocker: str,
) -> dict[str, Any]:
    entry_state = str(entry_signal.get("state") or "").upper()
    guard_status = str(entry_signal.get("guard_status") or "").upper()
    proof = str(proof_status or "").upper()
    runner = str(runner_state or "").upper()
    act = str(action or "").upper()
    blockers = list(entry_signal.get("blockers") or [])
    reasons = list(entry_signal.get("reasons") or [])

    deploy_blockers: list[str] = []
    if entry_state != "ENTRY_NOW":
        deploy_blockers.append(f"timing_{entry_state.lower() or 'not_ready'}")
    if guard_status != "FRESH":
        deploy_blockers.append("data_not_fresh")
    if proof != "PROOF_READY":
        deploy_blockers.append("proof_not_ready")
    if runner != "RUNNER_READY":
        deploy_blockers.append("runner_not_ready")
    if blocker:
        deploy_blockers.append(str(blocker))
    if blockers:
        deploy_blockers.extend(str(b) for b in blockers[:3])

    clean_blockers: list[str] = []
    for item in deploy_blockers:
        clean = str(item or "").strip()
        if clean and clean not in clean_blockers:
            clean_blockers.append(clean)

    deployable = (
        entry_state == "ENTRY_NOW"
        and guard_status == "FRESH"
        and proof == "PROOF_READY"
        and runner == "RUNNER_READY"
        and act == "PAPER_ENTRY"
        and not blocker
        and not blockers
    )
    paper_ready = entry_state == "ENTRY_NOW" and guard_status == "FRESH" and act in {"PAPER_ENTRY", "MANUAL_REVIEW"}
    hot_blocked = entry_state == "ENTRY_NOW" and not deployable

    if deployable:
        state = "DEPLOYABLE_NOW"
        label = "DEPLOYABLE NOW"
        instruction = "Timing, proof, runner state, and deployment gates align. Still apply operator risk rules."
    elif paper_ready and act == "PAPER_ENTRY":
        state = "PAPER_ENTRY_NOW"
        label = "PAPER ENTRY NOW"
        instruction = "Paper entry timing is active, but true deployment still needs proof/authority alignment."
    elif hot_blocked:
        state = "RESEARCH_ENTRY_NOW"
        label = "RESEARCH HOT"
        instruction = "Research timing is hot. Treat as manual review only until execution blockers clear."
    elif entry_state == "ARMED":
        state = "BLOCKED_BUT_HOT" if conviction in {"BUYABLE", "TRIGGERED"} else "ARMED"
        label = "BLOCKED HOT" if state == "BLOCKED_BUT_HOT" else "ARMED"
        instruction = "Setup is close, but execution should wait for the listed blocker to clear."
    elif entry_state in {"AVOID_CHASE", "EXIT_PRESSURE"}:
        state = entry_state
        label = entry_state.replace("_", " ")
        instruction = str(entry_signal.get("instruction") or "Avoid fresh entry.")
    else:
        state = "WAIT"
        label = "WAIT"
        instruction = "No deployable entry alignment yet."

    confidence = _clamp(
        _f(entry_signal.get("score")) * 0.62
        + (18.0 if proof == "PROOF_READY" else 0.0)
        + (12.0 if runner == "RUNNER_READY" else 0.0)
        + (8.0 if guard_status == "FRESH" else -12.0)
        - len(clean_blockers) * 4.5
    )
    return {
        "state": state,
        "label": label,
        "confidence": round(confidence, 1),
        "deployable": deployable,
        "paper_ready": paper_ready,
        "research_hot": hot_blocked or entry_state == "ENTRY_NOW",
        "blockers": clean_blockers[:8],
        "reasons": reasons[:6],
        "instruction": instruction,
        "proof_status": proof,
        "runner_state": runner,
        "entry_state": entry_state,
        "guard_status": guard_status,
    }


def _fmt_mcap_zone(value: float) -> str:
    if value <= 0:
        return "unknown market-cap zone"
    if value >= 1_000_000_000:
        return f"${value / 1_000_000_000:.2f}B"
    if value >= 1_000_000:
        return f"${value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"${value / 1_000:.0f}K"
    return f"${value:.0f}"


def _build_entry_zone_engine(
    *,
    marketcap: float,
    liquidity: float,
    volume_24h: float,
    buy_pressure: float,
    vol_accel: float,
    price_change_1h: float,
    price_change_24h: float,
    entry_signal: dict[str, Any],
    execution_alignment: dict[str, Any],
    too_late: dict[str, Any],
    signal_quality: dict[str, Any],
    risk_score: float,
    distribution_risk_score: float,
) -> dict[str, Any]:
    entry_state = str(entry_signal.get("state") or "").upper()
    execution_state = str(execution_alignment.get("state") or "").upper()
    last_blocker = str(entry_signal.get("last_blocker") or "").strip()
    too_late_label = str(too_late.get("label") or "").upper()
    quality_tier = str(signal_quality.get("tier") or "").upper()
    vol_liq = volume_24h / liquidity if liquidity > 0 else 0.0

    reasons: list[str] = []
    blockers: list[str] = []

    if buy_pressure >= 56:
        reasons.append(f"buy pressure is supportive ({buy_pressure:.0f})")
    elif buy_pressure < 48:
        blockers.append("buy pressure is not strong enough yet")
    if vol_accel >= 3:
        reasons.append(f"volume acceleration is active ({vol_accel:.2f})")
    elif vol_accel < 2.2:
        blockers.append("volume acceleration has not cleared")
    if vol_liq >= 1.0:
        reasons.append(f"volume/liquidity turnover is usable ({vol_liq:.1f}x)")
    elif liquidity > 0:
        blockers.append("volume/liquidity turnover is still thin")
    if quality_tier in {"A", "A_PLUS", "B"}:
        reasons.append(f"signal quality is {quality_tier.replace('_', '+')}")
    if risk_score < 45 or distribution_risk_score >= 65:
        blockers.append("risk or distribution pressure is too high")

    if entry_state in {"EXIT_PRESSURE"} or distribution_risk_score >= 65 or risk_score < 42:
        zone = "AVOID_RISK"
        label = "AVOID RISK"
        action = "Do not enter. Wait for distribution/risk pressure to reset."
    elif too_late_label == "EXTENDED" or entry_state == "AVOID_CHASE":
        zone = "TOO_EXTENDED"
        label = "TOO EXTENDED"
        action = "Do not chase. Wait for a base, pullback, or fresh confirmation after cooldown."
    elif price_change_24h >= 180 and price_change_1h < 1 and vol_accel < 2.0:
        zone = "MISSED_MOVE"
        label = "MISSED MOVE"
        action = "Treat the first move as missed. Only reconsider after a clean reset and new catalyst."
    elif entry_state == "ENTRY_NOW" and execution_state in {"DEPLOYABLE_NOW", "PAPER_ENTRY_NOW", "RESEARCH_ENTRY_NOW"}:
        zone = "BUY_NOW"
        label = "BUY NOW"
        action = "Timing is active. Use sizing/risk rules and invalidate fast if flow fades."
    elif last_blocker and ("vol" in last_blocker or "volume" in last_blocker):
        zone = "WAIT_VOLUME_CLEAR"
        label = "WAIT VOLUME CLEAR"
        action = "Wait for volume acceleration and buy pressure to clear before entry."
    elif last_blocker and ("proof" in last_blocker or "runner" in last_blocker or "catalyst" in last_blocker):
        zone = "WAIT_PROOF"
        label = "WAIT PROOF"
        action = "Wait for the proof/catalyst blocker to clear; do not force the entry."
    elif entry_state == "ARMED" and (price_change_1h >= 8 or too_late_label == "ELEVATED"):
        zone = "WAIT_PULLBACK"
        label = "WAIT PULLBACK"
        action = "Setup is interesting, but current move is stretched. Prefer a pullback/base."
    elif entry_state == "ARMED":
        zone = "WAIT_TRIGGER"
        label = "WAIT TRIGGER"
        action = "Setup is armed. Wait for the listed blocker to clear."
    else:
        zone = "WAIT_BASE"
        label = "WAIT BASE"
        action = "Keep watching until momentum, volume, and quality line up."

    pullback_mcap = marketcap * 0.88 if marketcap > 0 else 0.0
    chase_limit = marketcap * (1.06 if zone == "BUY_NOW" else 1.02) if marketcap > 0 else 0.0
    base_reentry = marketcap * 0.78 if zone in {"TOO_EXTENDED", "MISSED_MOVE"} and marketcap > 0 else pullback_mcap
    confidence = _clamp(
        _f(entry_signal.get("score")) * 0.48
        + _f(execution_alignment.get("confidence")) * 0.26
        + min(max(buy_pressure - 45.0, 0.0), 35.0) * 0.32
        + min(max(vol_accel, 0.0), 8.0) * 1.3
        - (18.0 if zone in {"TOO_EXTENDED", "MISSED_MOVE", "AVOID_RISK"} else 0.0)
        - min(distribution_risk_score, 80.0) * 0.12
    )
    return {
        "zone": zone,
        "label": label,
        "confidence": round(confidence, 1),
        "action": action,
        "ideal_entry": (
            f"Current zone up to {_fmt_mcap_zone(chase_limit)}"
            if zone == "BUY_NOW"
            else f"Prefer {_fmt_mcap_zone(pullback_mcap)} to {_fmt_mcap_zone(marketcap)}"
            if zone in {"WAIT_PULLBACK", "WAIT_TRIGGER", "WAIT_VOLUME_CLEAR", "WAIT_PROOF", "WAIT_BASE"}
            else f"Only reconsider near {_fmt_mcap_zone(base_reentry)} or after a fresh base"
        ),
        "current_marketcap": round(marketcap, 2),
        "pullback_marketcap": round(pullback_mcap, 2) if pullback_mcap > 0 else None,
        "chase_limit_marketcap": round(chase_limit, 2) if chase_limit > 0 else None,
        "base_reentry_marketcap": round(base_reentry, 2) if base_reentry > 0 else None,
        "confirmation_needed": (
            last_blocker.replace("_", " ") if last_blocker else
            "flow must stay active" if zone == "BUY_NOW" else
            "fresh volume and catalyst confirmation"
        ),
        "reasons": reasons[:5],
        "blockers": blockers[:5],
    }


def _build_trade_thesis(
    *,
    symbol: str,
    why_it_matters: str,
    conviction: str,
    action: str,
    narrative_intel: dict[str, Any],
    good_coin: dict[str, Any],
    catalyst_strength: dict[str, Any],
    too_late: dict[str, Any],
    entry_zone: dict[str, Any],
    entry_signal: dict[str, Any],
    execution_alignment: dict[str, Any],
    cluster_ctx: dict[str, Any],
    risk_summary: str,
    invalidation: str,
) -> dict[str, Any]:
    zone = str(entry_zone.get("label") or entry_zone.get("zone") or "WAIT").replace("_", " ")
    catalyst_label = str(catalyst_strength.get("label") or "UNKNOWN").replace("_", " ")
    good_status = str(good_coin.get("status") or "UNKNOWN").replace("_", " ")
    attention = str(narrative_intel.get("attention_persistence_label") or "LEARNING").replace("_", " ")
    comparable = str(narrative_intel.get("comparable_runner") or "").replace("_", " ")
    cluster_state = str(cluster_ctx.get("accumulation_state") or "QUIET").replace("_", " ").lower()

    confirms: list[str] = []
    for reason in list(entry_zone.get("reasons") or []) + list(entry_signal.get("reasons") or []):
        clean = str(reason or "").strip()
        if clean and clean not in confirms:
            confirms.append(clean)
    if good_status:
        confirms.insert(0, f"good coin status: {good_status.lower()}")
    if catalyst_label:
        confirms.append(f"catalyst strength: {catalyst_label.lower()}")
    if comparable and comparable != "unmapped":
        confirms.append(f"runner memory comparable: {comparable}")
    if cluster_state != "quiet":
        confirms.append(f"wallet cluster state: {cluster_state}")

    risks: list[str] = []
    for blocker in list(entry_zone.get("blockers") or []) + list(entry_signal.get("blockers") or []) + list(execution_alignment.get("blockers") or []):
        clean = str(blocker or "").replace("_", " ").strip()
        if clean and clean not in risks:
            risks.append(clean)
    too_late_label = str(too_late.get("label") or "").replace("_", " ").lower()
    if too_late_label and too_late_label not in {"normal", "none"}:
        risks.append(f"extension risk is {too_late_label}")

    summary = (
        f"{symbol} is a {zone.lower()} setup: {why_it_matters} "
        f"Current decision is {action.replace('_', ' ').lower()} with {conviction.replace('_', ' ').lower()} conviction."
    )
    must_stay_true = [
        "volume acceleration and buy pressure must not fade",
        "liquidity/market quality must stay usable",
        "contract and distribution risk must remain clean",
    ]
    return {
        "headline": summary[:360],
        "decision": zone.upper(),
        "conviction": conviction,
        "action": action,
        "why_this_coin": why_it_matters,
        "why_now": str(narrative_intel.get("research_note") or entry_zone.get("action") or ""),
        "ideal_entry": entry_zone.get("ideal_entry"),
        "confirmation_needed": entry_zone.get("confirmation_needed"),
        "what_must_stay_true": must_stay_true,
        "confirmations": confirms[:6],
        "risks": risks[:6],
        "invalidation": invalidation,
        "risk_summary": risk_summary,
        "confidence": entry_zone.get("confidence"),
        "attention_label": attention,
    }


def _build_position_plan(
    *,
    marketcap: float,
    liquidity: float,
    volume_24h: float,
    risk_score: float,
    distribution_risk_score: float,
    entry_zone: dict[str, Any],
    entry_signal: dict[str, Any],
    execution_alignment: dict[str, Any],
    signal_quality: dict[str, Any],
) -> dict[str, Any]:
    zone = str(entry_zone.get("zone") or "").upper()
    confidence = _f(entry_zone.get("confidence"))
    quality_score = _f(signal_quality.get("score"))
    execution_conf = _f(execution_alignment.get("confidence"))
    vol_liq = volume_24h / liquidity if liquidity > 0 else 0.0
    risk_clean = risk_score >= 58 and distribution_risk_score < 45

    if zone == "BUY_NOW" and confidence >= 72 and risk_clean:
        stance = "STARTER_ALLOWED"
        size_units = 1.0 if confidence >= 82 and quality_score >= 72 and execution_conf >= 70 else 0.5
        size_label = "standard starter" if size_units >= 1 else "half starter"
    elif zone == "BUY_NOW":
        stance = "TINY_STARTER_ONLY"
        size_units = 0.25
        size_label = "tiny starter"
    elif zone in {"WAIT_TRIGGER", "WAIT_VOLUME_CLEAR", "WAIT_PROOF", "WAIT_PULLBACK"}:
        stance = "WAIT_FOR_TRIGGER"
        size_units = 0.0
        size_label = "no size yet"
    else:
        stance = "NO_POSITION"
        size_units = 0.0
        size_label = "no size"

    starter_mcap = marketcap if zone == "BUY_NOW" else _f(entry_zone.get("pullback_marketcap")) or marketcap
    add_mcap = marketcap * 1.18 if marketcap > 0 else None
    invalid_mcap = marketcap * (0.82 if confidence >= 75 else 0.88) if marketcap > 0 else None
    trim1_mcap = marketcap * 1.35 if marketcap > 0 else None
    trim2_mcap = marketcap * 1.85 if marketcap > 0 else None
    runner_mcap = marketcap * 2.5 if marketcap > 0 else None
    max_risk_note = (
        "Risk small until proof and flow are both confirmed."
        if size_units <= 0.25
        else "Use normal starter risk; do not average down if thesis breaks."
    )
    return {
        "stance": stance,
        "size_units": round(size_units, 2),
        "size_label": size_label,
        "starter_marketcap": round(starter_mcap, 2) if starter_mcap else None,
        "add_marketcap": round(add_mcap, 2) if add_mcap else None,
        "invalid_marketcap": round(invalid_mcap, 2) if invalid_mcap else None,
        "trim1_marketcap": round(trim1_mcap, 2) if trim1_mcap else None,
        "trim2_marketcap": round(trim2_mcap, 2) if trim2_mcap else None,
        "runner_marketcap": round(runner_mcap, 2) if runner_mcap else None,
        "starter": (
            f"{size_label} near {_fmt_mcap_zone(starter_mcap)}"
            if size_units > 0 and starter_mcap > 0
            else "No starter until entry trigger clears."
        ),
        "add_rule": (
            f"Only add after confirmation above {_fmt_mcap_zone(add_mcap or 0)} with volume still expanding."
            if add_mcap
            else "No add rule until market cap is known."
        ),
        "invalid_stop": (
            f"Invalidate below {_fmt_mcap_zone(invalid_mcap or 0)} or if buy pressure/volume fades."
            if invalid_mcap
            else "Invalidate on thesis break, stale data, or risk deterioration."
        ),
        "trim_plan": (
            f"First trim near {_fmt_mcap_zone(trim1_mcap or 0)}; second trim near {_fmt_mcap_zone(trim2_mcap or 0)}."
            if trim1_mcap and trim2_mcap
            else "Trim only into confirmed expansion."
        ),
        "runner_plan": (
            f"Let runner work toward {_fmt_mcap_zone(runner_mcap or 0)} only while volume, buy pressure, and distribution stay clean."
            if runner_mcap
            else "Runner hold requires sustained flow and clean distribution."
        ),
        "max_risk_note": max_risk_note,
        "vol_liq_ratio": round(vol_liq, 2),
        "entry_state": entry_signal.get("state"),
        "entry_zone": zone,
    }


def _build_exit_intelligence_engine(
    *,
    marketcap: float,
    buy_pressure: float,
    vol_accel: float,
    volume_24h: float,
    liquidity: float,
    price_change_1h: float,
    price_change_24h: float,
    risk_score: float,
    distribution_risk_score: float,
    too_late: dict[str, Any],
    entry_zone: dict[str, Any],
    signal_quality: dict[str, Any],
    trade_thesis: dict[str, Any] | None = None,
) -> dict[str, Any]:
    zone = str(entry_zone.get("zone") or "").upper()
    too_late_label = str(too_late.get("label") or "").upper()
    vol_liq = volume_24h / liquidity if liquidity > 0 else 0.0
    flow_alive = buy_pressure >= 54 and vol_accel >= 2.8 and vol_liq >= 0.85
    flow_fading = buy_pressure < 48 or vol_accel < 1.8
    extended = too_late_label == "EXTENDED" or price_change_24h >= 160 or price_change_1h >= 28
    distribution = distribution_risk_score >= 55
    risk_break = risk_score < 45 or distribution_risk_score >= 70

    thesis_health = _clamp(
        52.0
        + max(buy_pressure - 50.0, -20.0) * 0.9
        + min(vol_accel, 8.0) * 3.2
        + min(vol_liq, 4.0) * 4.0
        + _f(signal_quality.get("score")) * 0.18
        - distribution_risk_score * 0.24
        - _f(too_late.get("score")) * 0.14
        - (14.0 if risk_score < 50 else 0.0)
    )

    reasons: list[str] = []
    risks: list[str] = []
    if flow_alive:
        reasons.append("flow is still alive")
    if buy_pressure >= 58:
        reasons.append(f"buy pressure supportive ({buy_pressure:.0f})")
    if vol_accel >= 3:
        reasons.append(f"volume expanding ({vol_accel:.2f})")
    if flow_fading:
        risks.append("flow is fading")
    if extended:
        risks.append("extension risk is elevated")
    if distribution:
        risks.append("distribution pressure is rising")
    if risk_break:
        risks.append("risk has broken acceptable bounds")

    if risk_break:
        state = "SELL_NOW"
        urgency = "HIGH"
        action = "Exit/avoid. Risk or distribution pressure is no longer acceptable."
    elif extended and flow_fading:
        state = "TRIM_HARD"
        urgency = "HIGH"
        action = "Protect gains aggressively; extension plus fading flow is a giveback setup."
    elif extended:
        state = "TRIM_STRENGTH"
        urgency = "MEDIUM"
        action = "Trim into strength. Keep only a runner while flow stays alive."
    elif zone == "BUY_NOW" and flow_alive and thesis_health >= 70:
        state = "LET_RUNNER_WORK"
        urgency = "LOW"
        action = "Hold runner while thesis health stays strong; avoid early full exit."
    elif zone == "BUY_NOW" and flow_fading:
        state = "PROTECT_FAST"
        urgency = "MEDIUM"
        action = "If entered, protect quickly because the entry trigger is fading."
    elif zone.startswith("WAIT"):
        state = "NO_POSITION_YET"
        urgency = "LOW"
        action = "No exit needed yet; use this as the future management plan after entry."
    else:
        state = "HOLD_MONITOR"
        urgency = "LOW"
        action = "Monitor thesis health and react if flow or risk deteriorates."

    protect_mcap = marketcap * 0.92 if marketcap > 0 else None
    failure_mcap = marketcap * 0.82 if marketcap > 0 else None
    stretch_mcap = marketcap * 1.55 if marketcap > 0 else None
    return {
        "state": state,
        "urgency": urgency,
        "action": action,
        "thesis_health_score": round(thesis_health, 1),
        "thesis_health_label": "STRONG" if thesis_health >= 72 else "OK" if thesis_health >= 56 else "WEAK",
        "hold_condition": "Hold only while buy pressure >=54, volume acceleration >=2.8, and distribution stays below danger.",
        "trim_condition": (
            f"Trim strength near {_fmt_mcap_zone(stretch_mcap or 0)} or sooner if flow starts fading."
            if stretch_mcap
            else "Trim into extension or fading volume."
        ),
        "protect_condition": (
            f"Protect below {_fmt_mcap_zone(protect_mcap or 0)} or when buy pressure falls under 48."
            if protect_mcap
            else "Protect if flow fades or thesis health turns weak."
        ),
        "sell_condition": (
            f"Sell/avoid below {_fmt_mcap_zone(failure_mcap or 0)} or if distribution risk hits danger."
            if failure_mcap
            else "Sell/avoid if distribution, contract risk, or liquidity breaks."
        ),
        "reasons": reasons[:5],
        "risks": risks[:5],
        "flow_alive": flow_alive,
        "flow_fading": flow_fading,
        "extension_risk": extended,
        "distribution_risk": distribution,
        "vol_liq_ratio": round(vol_liq, 2),
        "entry_zone": zone,
        "thesis_decision": (trade_thesis or {}).get("decision") if isinstance(trade_thesis, dict) else None,
    }


def _fallback_entry_zone_from_dossier(item: dict[str, Any]) -> dict[str, Any]:
    entry_state = str(item.get("entry_state") or "").upper()
    blocker = str(item.get("entry_blocker") or item.get("blocker_key") or "").strip()
    too_late = str(item.get("too_late_label") or "").upper()
    marketcap = _f(item.get("marketcap"))
    score = _f(item.get("entry_score") or item.get("research_score"))
    if entry_state == "ENTRY_NOW":
        zone, label, action = "BUY_NOW", "BUY NOW", "Timing is active. Use sizing/risk rules and invalidate fast if flow fades."
    elif entry_state == "AVOID_CHASE" or too_late == "EXTENDED":
        zone, label, action = "TOO_EXTENDED", "TOO EXTENDED", "Do not chase. Wait for a reset, base, or fresh confirmation."
    elif entry_state == "EXIT_PRESSURE":
        zone, label, action = "AVOID_RISK", "AVOID RISK", "Do not enter while exit/distribution pressure is active."
    elif blocker and ("vol" in blocker or "volume" in blocker):
        zone, label, action = "WAIT_VOLUME_CLEAR", "WAIT VOLUME CLEAR", "Wait for volume acceleration and buy pressure to clear."
    elif blocker and ("proof" in blocker or "runner" in blocker or "catalyst" in blocker):
        zone, label, action = "WAIT_PROOF", "WAIT PROOF", "Wait for proof/catalyst confirmation before entry."
    elif entry_state == "ARMED":
        zone, label, action = "WAIT_TRIGGER", "WAIT TRIGGER", "Setup is armed. Wait for the last blocker to clear."
    else:
        zone, label, action = "WAIT_BASE", "WAIT BASE", "Keep watching until momentum, volume, and quality line up."
    pullback = marketcap * 0.88 if marketcap > 0 else 0.0
    chase_limit = marketcap * (1.06 if zone == "BUY_NOW" else 1.02) if marketcap > 0 else 0.0
    return {
        "zone": zone,
        "label": label,
        "confidence": round(_clamp(score), 1),
        "action": action,
        "ideal_entry": (
            f"Current zone up to {_fmt_mcap_zone(chase_limit)}"
            if zone == "BUY_NOW"
            else f"Prefer {_fmt_mcap_zone(pullback)} to {_fmt_mcap_zone(marketcap)}"
            if marketcap > 0
            else "Waiting for a reliable market-cap zone"
        ),
        "current_marketcap": round(marketcap, 2) if marketcap > 0 else None,
        "pullback_marketcap": round(pullback, 2) if pullback > 0 else None,
        "chase_limit_marketcap": round(chase_limit, 2) if chase_limit > 0 else None,
        "confirmation_needed": blocker.replace("_", " ") if blocker else "fresh flow confirmation",
        "reasons": list(item.get("entry_reasons") or [])[:5],
        "blockers": list(item.get("entry_blockers") or [])[:5],
    }


def _fallback_trade_thesis_from_dossier(item: dict[str, Any], entry_zone: dict[str, Any]) -> dict[str, Any]:
    symbol = str(item.get("symbol") or "TOKEN")
    conviction = str(item.get("conviction_band") or item.get("action") or "WATCH")
    action = str(item.get("action") or "WATCH")
    why = str(item.get("system_thesis") or item.get("why_now") or "The system is still building the thesis from available quality, momentum, and risk data.")
    invalidation = str(item.get("invalidation") or "Invalidate if volume fades, quality turns unstable, or extension risk rises.")
    return {
        "headline": f"{symbol} is a {str(entry_zone.get('label') or 'WAIT').lower()} setup. {why}"[:360],
        "decision": entry_zone.get("label") or entry_zone.get("zone") or "WAIT",
        "conviction": conviction,
        "action": action,
        "why_this_coin": why,
        "why_now": str(item.get("why_now") or entry_zone.get("action") or ""),
        "ideal_entry": entry_zone.get("ideal_entry"),
        "confirmation_needed": entry_zone.get("confirmation_needed"),
        "what_must_stay_true": [
            "volume acceleration and buy pressure must not fade",
            "liquidity/market quality must stay usable",
            "contract and distribution risk must remain clean",
        ],
        "confirmations": list(item.get("entry_reasons") or [])[:6],
        "risks": list(item.get("entry_blockers") or item.get("execution_blockers") or [])[:6],
        "invalidation": invalidation,
        "risk_summary": item.get("risk_summary"),
        "confidence": entry_zone.get("confidence"),
        "attention_label": item.get("attention_persistence_label"),
    }


def _fallback_position_plan_from_dossier(item: dict[str, Any], entry_zone: dict[str, Any]) -> dict[str, Any]:
    marketcap = _f(item.get("marketcap"))
    zone = str(entry_zone.get("zone") or "").upper()
    confidence = _f(entry_zone.get("confidence") or item.get("entry_score") or item.get("research_score"))
    size_units = 0.5 if zone == "BUY_NOW" and confidence >= 72 else 0.25 if zone == "BUY_NOW" else 0.0
    invalid_mcap = marketcap * (0.82 if confidence >= 75 else 0.88) if marketcap > 0 else None
    trim1_mcap = marketcap * 1.35 if marketcap > 0 else None
    trim2_mcap = marketcap * 1.85 if marketcap > 0 else None
    runner_mcap = marketcap * 2.5 if marketcap > 0 else None
    return {
        "stance": "STARTER_ALLOWED" if size_units >= 0.5 else "TINY_STARTER_ONLY" if size_units > 0 else "WAIT_FOR_TRIGGER" if zone.startswith("WAIT") else "NO_POSITION",
        "size_units": round(size_units, 2),
        "size_label": "half starter" if size_units >= 0.5 else "tiny starter" if size_units > 0 else "no size yet",
        "starter_marketcap": round(marketcap, 2) if marketcap > 0 and size_units > 0 else None,
        "invalid_marketcap": round(invalid_mcap, 2) if invalid_mcap else None,
        "trim1_marketcap": round(trim1_mcap, 2) if trim1_mcap else None,
        "trim2_marketcap": round(trim2_mcap, 2) if trim2_mcap else None,
        "runner_marketcap": round(runner_mcap, 2) if runner_mcap else None,
        "starter": f"{'half starter' if size_units >= 0.5 else 'tiny starter'} near {_fmt_mcap_zone(marketcap)}" if size_units > 0 else "No starter until entry trigger clears.",
        "add_rule": f"Only add after confirmation above {_fmt_mcap_zone(marketcap * 1.18)} with volume still expanding." if marketcap > 0 else "No add rule until market cap is known.",
        "invalid_stop": f"Invalidate below {_fmt_mcap_zone(invalid_mcap or 0)} or if buy pressure/volume fades." if invalid_mcap else "Invalidate on thesis break, stale data, or risk deterioration.",
        "trim_plan": f"First trim near {_fmt_mcap_zone(trim1_mcap or 0)}; second trim near {_fmt_mcap_zone(trim2_mcap or 0)}." if trim1_mcap and trim2_mcap else "Trim only into confirmed expansion.",
        "runner_plan": f"Let runner work toward {_fmt_mcap_zone(runner_mcap or 0)} only while flow and distribution stay clean." if runner_mcap else "Runner hold requires sustained flow and clean distribution.",
        "max_risk_note": "Fallback plan from cached dossier; refresh will sharpen sizing.",
        "entry_zone": zone,
    }


def _fallback_exit_intelligence_from_dossier(item: dict[str, Any], entry_zone: dict[str, Any]) -> dict[str, Any]:
    marketcap = _f(item.get("marketcap"))
    buy_pressure = _f(item.get("buy_pressure"), 50.0)
    vol_accel = _f(item.get("vol_acceleration"))
    risk_score = _f(item.get("risk_score"), 50.0)
    too_late = str(item.get("too_late_label") or "").upper()
    zone = str(entry_zone.get("zone") or "").upper()
    flow_alive = buy_pressure >= 54 and vol_accel >= 2.8
    flow_fading = buy_pressure < 48 or vol_accel < 1.8
    extended = too_late == "EXTENDED"
    health = _clamp(55.0 + max(buy_pressure - 50.0, -20.0) * 0.8 + min(vol_accel, 8.0) * 3.0 - (18.0 if extended else 0.0) - (12.0 if risk_score < 45 else 0.0))
    if risk_score < 45:
        state, urgency, action = "SELL_NOW", "HIGH", "Exit/avoid. Risk is no longer acceptable."
    elif extended and flow_fading:
        state, urgency, action = "TRIM_HARD", "HIGH", "Protect gains aggressively; extension plus fading flow is a giveback setup."
    elif extended:
        state, urgency, action = "TRIM_STRENGTH", "MEDIUM", "Trim into strength. Keep only a runner while flow stays alive."
    elif zone == "BUY_NOW" and flow_alive:
        state, urgency, action = "LET_RUNNER_WORK", "LOW", "Hold runner while thesis health stays strong; avoid early full exit."
    elif zone.startswith("WAIT"):
        state, urgency, action = "NO_POSITION_YET", "LOW", "No exit needed yet; use this as the future management plan after entry."
    else:
        state, urgency, action = "HOLD_MONITOR", "LOW", "Monitor thesis health and react if flow or risk deteriorates."
    return {
        "state": state,
        "urgency": urgency,
        "action": action,
        "thesis_health_score": round(health, 1),
        "thesis_health_label": "STRONG" if health >= 72 else "OK" if health >= 56 else "WEAK",
        "hold_condition": "Hold only while buy pressure >=54, volume acceleration >=2.8, and distribution stays clean.",
        "trim_condition": f"Trim strength near {_fmt_mcap_zone(marketcap * 1.55)} or sooner if flow starts fading." if marketcap > 0 else "Trim into extension or fading volume.",
        "protect_condition": f"Protect below {_fmt_mcap_zone(marketcap * 0.92)} or when buy pressure falls under 48." if marketcap > 0 else "Protect if flow fades or thesis health turns weak.",
        "sell_condition": f"Sell/avoid below {_fmt_mcap_zone(marketcap * 0.82)} or if distribution risk hits danger." if marketcap > 0 else "Sell/avoid if distribution, contract risk, or liquidity breaks.",
        "reasons": ["flow is still alive"] if flow_alive else [],
        "risks": ["flow is fading"] if flow_fading else [],
        "flow_alive": flow_alive,
        "flow_fading": flow_fading,
        "extension_risk": extended,
        "entry_zone": zone,
    }


def _historical_narrative_tuning() -> dict[str, float]:
    try:
        with get_conn() as conn:
            _ensure_schema(conn)
            rows = conn.execute(
                """
                SELECT narrative,
                       COALESCE(SUM(outcome_sample_n), 0) AS n,
                       COALESCE(SUM(outcome_good_n), 0) AS good_n,
                       COALESCE(SUM(outcome_bad_n), 0) AS bad_n,
                       COALESCE(AVG(avg_max_return_pct), 0) AS avg_max
                FROM memecoin_research_dossiers
                WHERE narrative IS NOT NULL AND narrative != ''
                GROUP BY narrative
                """
            ).fetchall()
    except Exception:
        return {}
    out: dict[str, float] = {}
    for row in rows:
        n = int(row["n"] or 0)
        if n <= 0:
            continue
        good = float(row["good_n"] or 0)
        bad = float(row["bad_n"] or 0)
        avg_max = float(row["avg_max"] or 0.0)
        bonus = min(8.0, avg_max / 4.0) + good * 1.5 - bad * 2.0
        out[str(row["narrative"])] = round(_clamp(bonus, -8.0, 10.0), 2)
    return out


def _catalyst_context(
    *,
    runner_state: str,
    proof_status: str,
    blocker: str,
    buy_pressure: float,
    vol_accel: float,
    volume_24h: float,
    liquidity: float,
    price_change_1h: float,
    price_change_24h: float,
) -> dict[str, str]:
    vol_liq = volume_24h / liquidity if liquidity > 0 else 0.0
    if runner_state == "RUNNER_READY":
        ctype = "runner_ready"
        why = "Runner policy is ready now; the system is watching whether momentum converts into a profitable paper entry."
    elif vol_accel >= 8.0 and buy_pressure >= 58.0:
        ctype = "volume_acceleration"
        why = f"Volume acceleration is strong ({vol_accel:.2f}) with buy pressure {buy_pressure:.1f}."
    elif vol_liq >= 3.0:
        ctype = "liquidity_turnover"
        why = f"24h volume is {vol_liq:.1f}x liquidity, which can indicate active rotation."
    elif price_change_1h >= 8.0 or price_change_24h >= 25.0:
        ctype = "price_breakout"
        why = f"Price is expanding: 1h {price_change_1h:.1f}%, 24h {price_change_24h:.1f}%."
    elif blocker == "runner_momentum_unconfirmed":
        ctype = "last_blocker_watch"
        why = "Known runner is close, but momentum confirmation is still the last blocker."
    elif proof_status == "RESEARCH_ONLY":
        ctype = "research_watch"
        why = "The setup is research-only; watch for a specific blocker clearing before entry."
    else:
        ctype = "background_memory"
        why = "No urgent catalyst detected yet; dossier is maintained because token memory or structure matters."
    return {"catalyst_type": ctype, "why_now": why}


def _market_catalyst_events(
    *,
    candidates: list[dict[str, Any]],
    token_stats: dict[str, dict[str, Any]],
    previous_by_mint: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    previous_by_mint = previous_by_mint or {}
    for candidate in candidates:
        mint = str(candidate.get("mint") or "").strip()
        if not mint:
            continue
        stats = dict(token_stats.get(mint) or {})
        known = _memory_for(mint, _candidate_symbol(candidate, stats))
        symbol = _candidate_symbol(candidate, stats, known)
        components = dict(candidate.get("proof_components") or {})
        scanner = dict(components.get("scanner") or {})
        policy = dict(candidate.get("established_runner_policy") or components.get("established_runner_policy") or {})
        live_stats = dict(scanner.get("live_token_stats") or {})
        runner_state = str(policy.get("state") or candidate.get("runner_policy_state") or "").upper()
        proof_status = str(candidate.get("proof_status") or "").upper()
        blocker = str(candidate.get("blocker_key") or components.get("blocker_key") or policy.get("blocker_key") or "").strip()
        marketcap = _f(stats.get("marketcap") or live_stats.get("marketcap") or candidate.get("mcap_at_scan"))
        liquidity = _f(stats.get("liquidity") or scanner.get("liquidity"))
        volume_24h = _f(stats.get("volume_24h_usd") or scanner.get("volume_24h"))
        buy_pressure = _f(scanner.get("buy_pressure") or (stats.get("inputs") or {}).get("buy_pressure_1h"))
        vol_accel = _f(scanner.get("vol_acceleration") or (stats.get("inputs") or {}).get("vol_acceleration_est"))
        price_change_1h = _f(stats.get("price_change_1h_percent") or live_stats.get("price_change_1h_percent"))
        price_change_24h = _f(stats.get("price_change_24h_percent") or live_stats.get("price_change_24h_percent"))
        narrative, _archetype = _classify_narrative(symbol, known, dict(candidate.get("established_runner_profile") or {}))
        base = {
            "mint": mint,
            "symbol": symbol,
            "event_ts": _iso(),
            "narrative": narrative,
            "marketcap": marketcap,
            "liquidity": liquidity,
            "volume_24h": volume_24h,
            "buy_pressure": buy_pressure,
            "vol_acceleration": vol_accel,
            "evidence": {
                "runner_state": runner_state,
                "proof_status": proof_status,
                "blocker": blocker,
                "price_change_1h": price_change_1h,
                "price_change_24h": price_change_24h,
                "known_memory": bool(known),
            },
            "raw": {"candidate": candidate, "token_stats": stats},
        }
        prev = previous_by_mint.get(mint) or {}
        prev_blocker = str(prev.get("blocker_key") or "").strip()
        if prev_blocker and prev_blocker != blocker and (runner_state == "RUNNER_READY" or not blocker):
            events.append(
                {
                    **base,
                    "event_type": "LAST_BLOCKER_CLEARED",
                    "source": "research_dossier_delta",
                    "confidence": 88.0,
                    "headline": f"{symbol} cleared {prev_blocker.replace('_', ' ')}.",
                    "detail": "The latest dossier no longer shows the previous entry blocker, so this deserves immediate review.",
                }
            )
        if runner_state == "RUNNER_READY":
            events.append(
                {
                    **base,
                    "event_type": "RUNNER_READY",
                    "source": "runner_policy",
                    "confidence": 86.0,
                    "headline": f"{symbol} runner policy is ready.",
                    "detail": "The established-runner gate is open enough for paper entry or manual review.",
                }
            )
        if known and (volume_24h >= 100_000 or vol_accel >= 3.0 or buy_pressure >= 56.0 or price_change_24h >= 15.0):
            confidence = 68.0 + min(vol_accel * 2.0, 12.0) + (6.0 if buy_pressure >= 58.0 else 0.0)
            events.append(
                {
                    **base,
                    "event_type": "KNOWN_COIN_WAKEUP",
                    "source": "known_memory_market",
                    "confidence": _clamp(confidence, 60.0, 92.0),
                    "headline": f"{symbol} known-memory coin is waking up.",
                    "detail": f"Known ticker with volume {volume_24h:,.0f}, buy pressure {buy_pressure:.1f}, vol acceleration {vol_accel:.2f}.",
                }
            )
        if vol_accel >= 8.0 and buy_pressure >= 55.0:
            events.append(
                {
                    **base,
                    "event_type": "VOLUME_WAKEUP",
                    "source": "token_stats",
                    "confidence": _clamp(70.0 + vol_accel + max(0.0, buy_pressure - 55.0), 70.0, 96.0),
                    "headline": f"{symbol} volume acceleration is active.",
                    "detail": f"Volume acceleration {vol_accel:.2f} with buy pressure {buy_pressure:.1f}.",
                }
            )
        vol_liq = volume_24h / liquidity if liquidity > 0 else 0.0
        if vol_liq >= 3.0 and liquidity >= 10_000:
            events.append(
                {
                    **base,
                    "event_type": "LIQUIDITY_TURNOVER",
                    "source": "token_stats",
                    "confidence": _clamp(62.0 + min(vol_liq * 4.0, 25.0), 62.0, 90.0),
                    "headline": f"{symbol} is turning over liquidity.",
                    "detail": f"24h volume is {vol_liq:.1f}x liquidity, a possible rotation signal.",
                }
            )
        if price_change_1h >= 8.0 or price_change_24h >= 25.0:
            events.append(
                {
                    **base,
                    "event_type": "PRICE_BREAKOUT",
                    "source": "token_stats",
                    "confidence": _clamp(62.0 + max(price_change_1h, price_change_24h / 3.0), 62.0, 92.0),
                    "headline": f"{symbol} price expansion is showing.",
                    "detail": f"Price change: 1h {price_change_1h:.1f}%, 24h {price_change_24h:.1f}%.",
                }
            )
    return events


def _external_catalyst_events(
    *,
    watched_by_mint: dict[str, dict[str, Any]],
    token_stats: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    if not watched_by_mint:
        return []
    events: list[dict[str, Any]] = []
    watched = {str(m or "").strip(): dict(c or {}) for m, c in watched_by_mint.items() if str(m or "").strip()}
    try:
        from data.dexscreener import fetch_latest_profiles, fetch_token_boosts  # type: ignore
    except Exception:
        return []

    def add_external(item: dict[str, Any], *, event_type: str, source: str, confidence: float) -> None:
        chain, mint = _extract_chain_token_address(item)
        if chain and chain not in {"solana", "sol"}:
            return
        if mint not in watched:
            return
        candidate = watched.get(mint) or {}
        stats = dict(token_stats.get(mint) or {})
        known = _memory_for(mint, _candidate_symbol(candidate, stats))
        symbol = _candidate_symbol(candidate, stats, known) or str(item.get("tokenSymbol") or item.get("symbol") or "").upper()
        narrative, _archetype = _classify_narrative(symbol, known, dict(candidate.get("established_runner_profile") or {}))
        marketcap = _f(stats.get("marketcap"))
        liquidity = _f(stats.get("liquidity"))
        volume_24h = _f(stats.get("volume_24h_usd"))
        events.append(
            {
                "mint": mint,
                "symbol": symbol,
                "event_ts": _iso(),
                "event_type": event_type,
                "source": source,
                "confidence": confidence,
                "headline": f"{symbol} external catalyst detected: {event_type.replace('_', ' ').lower()}.",
                "detail": "DexScreener surfaced a boost/profile signal for a tracked mint.",
                "narrative": narrative,
                "marketcap": marketcap,
                "liquidity": liquidity,
                "volume_24h": volume_24h,
                "buy_pressure": _f((stats.get("inputs") or {}).get("buy_pressure_1h")),
                "vol_acceleration": _f((stats.get("inputs") or {}).get("vol_acceleration_est")),
                "evidence": {"external_source": source, "known_memory": bool(known)},
                "raw": item,
            }
        )

    try:
        for kind, confidence in (("top", 76.0), ("latest", 69.0)):
            for item in fetch_token_boosts(kind, reason=f"research_catalyst_boosts_{kind}_429")[:CATALYST_EXTERNAL_LIMIT]:
                if isinstance(item, dict):
                    add_external(item, event_type="DEX_BOOST", source=f"dexscreener_boost_{kind}", confidence=confidence)
    except Exception:
        pass
    try:
        for item in fetch_latest_profiles(reason="research_catalyst_profiles_429")[:CATALYST_EXTERNAL_LIMIT]:
            if isinstance(item, dict):
                add_external(item, event_type="PROFILE_UPDATE", source="dexscreener_profile", confidence=63.0)
    except Exception:
        pass
    return events


def refresh_memecoin_catalyst_events(
    limit: int = RESEARCH_DOSSIER_LIMIT,
    *,
    record: bool = True,
    include_external: bool = False,
    candidates: list[dict[str, Any]] | None = None,
    token_stats: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    snapshot: dict[str, Any] = {}
    if candidates is None:
        try:
            from utils.memecoin_manager import get_proof_candidate_snapshot  # type: ignore

            snapshot = get_proof_candidate_snapshot(limit=max(int(limit), 20), include_recent_complete=True)
            candidates = [dict(c or {}) for c in list(snapshot.get("candidates") or [])]
        except Exception:
            candidates = []
    candidates = [dict(c or {}) for c in list(candidates or [])]
    by_mint: dict[str, dict[str, Any]] = {
        str(c.get("mint") or "").strip(): c for c in candidates if str(c.get("mint") or "").strip()
    }
    for mint, memory in KNOWN_COIN_MEMORY.items():
        by_mint.setdefault(mint, {"mint": mint, "symbol": memory.get("symbol")})
    mints = list(by_mint.keys())
    if token_stats is None:
        token_stats = get_latest_memecoin_token_stats_for_mints(mints, max_age_minutes=RESEARCH_TOKEN_STATS_MAX_AGE_MINUTES)
    for mint in mints:
        if mint not in token_stats:
            dex = _dex_market(mint)
            if dex:
                token_stats[mint] = {
                    "mint": mint,
                    "symbol": dex.get("symbol"),
                    "marketcap": dex.get("marketcap"),
                    "liquidity": dex.get("liquidity"),
                    "volume_24h_usd": dex.get("volume_24h"),
                    "price": dex.get("price"),
                    "price_change_1h_percent": dex.get("change_1h"),
                    "price_change_24h_percent": dex.get("change_24h"),
                    "inputs": {},
                }
    previous_by_mint: dict[str, dict[str, Any]] = {}
    try:
        with get_conn() as conn:
            _ensure_schema(conn)
            if mints:
                placeholders = ",".join("?" for _ in mints)
                rows = conn.execute(
                    f"SELECT mint, blocker_key, action, runner_state FROM memecoin_research_dossiers WHERE mint IN ({placeholders})",
                    mints,
                ).fetchall()
                previous_by_mint = {str(row["mint"]): dict(row) for row in rows}
    except Exception:
        previous_by_mint = {}

    market_events = _market_catalyst_events(
        candidates=list(by_mint.values()),
        token_stats=token_stats,
        previous_by_mint=previous_by_mint,
    )
    external_events = (
        _external_catalyst_events(watched_by_mint=by_mint, token_stats=token_stats)
        if include_external
        else []
    )
    events = market_events + external_events
    inserted = 0
    skipped = 0
    if record and events:
        def _write_events() -> None:
            nonlocal inserted, skipped
            inserted = 0
            skipped = 0
            with get_conn() as conn:
                _ensure_schema(conn)
                for event in events:
                    if _insert_catalyst_event(conn, event):
                        inserted += 1
                    else:
                        skipped += 1
                conn.execute(
                    "INSERT OR REPLACE INTO kv_store (key, value) VALUES (?, ?)",
                    (
                        "memecoin_catalyst_status",
                        json.dumps(
                            {
                                "generated_at": _iso(),
                                "candidate_events": len(market_events),
                                "external_events": len(external_events),
                                "inserted": inserted,
                                "skipped_dedupe": skipped,
                                "include_external": bool(include_external),
                            },
                            separators=(",", ":"),
                        ),
                    ),
                )
                conn.commit()

        _research_db_retry(_write_events)
    counts: dict[str, int] = {}
    for event in events:
        etype = str(event.get("event_type") or "").upper()
        counts[etype] = counts.get(etype, 0) + 1
    return {
        "generated_at": _iso(),
        "summary": {
            "candidate_events": len(market_events),
            "external_events": len(external_events),
            "generated_events": len(events),
            "inserted": inserted,
            "skipped_dedupe": skipped,
            "by_type": counts,
            "include_external": bool(include_external),
        },
        "events": sorted(events, key=lambda e: (_f(e.get("confidence")), str(e.get("event_type") or "")), reverse=True)[:50],
        "proof_input_source": snapshot.get("proof_input_source"),
    }


def _build_dossier_from_candidate(
    candidate: dict[str, Any],
    token_stats: dict[str, Any],
    known: dict[str, Any],
    outcome: dict[str, Any],
    narrative_tuning: dict[str, float] | None = None,
    catalyst_event: dict[str, Any] | None = None,
    wallet_cluster: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    mint = str(candidate.get("mint") or known.get("mint") or "").strip()
    symbol = str(candidate.get("symbol") or known.get("symbol") or token_stats.get("symbol") or "").strip().upper()
    if not mint or not symbol:
        return None

    components = dict(candidate.get("proof_components") or {})
    scanner = dict(components.get("scanner") or {})
    lifecycle = dict(components.get("lifecycle") or {})
    profile = dict(candidate.get("established_runner_profile") or components.get("established_runner") or {})
    policy = dict(candidate.get("established_runner_policy") or components.get("established_runner_policy") or {})
    runner_state = str(policy.get("state") or candidate.get("runner_policy_state") or "").upper()
    proof_status = str(candidate.get("proof_status") or "").upper()
    blocker = str(candidate.get("blocker_key") or components.get("blocker_key") or policy.get("blocker_key") or "").strip()
    live_stats = dict(scanner.get("live_token_stats") or {})

    marketcap = _f(token_stats.get("marketcap") or live_stats.get("marketcap") or candidate.get("mcap_at_scan"))
    liquidity = _f(token_stats.get("liquidity") or scanner.get("liquidity"))
    volume_24h = _f(token_stats.get("volume_24h_usd") or scanner.get("volume_24h"))
    buy_pressure = _f(scanner.get("buy_pressure") or (token_stats.get("inputs") or {}).get("buy_pressure_1h"))
    vol_accel = _f(scanner.get("vol_acceleration") or (token_stats.get("inputs") or {}).get("vol_acceleration_est"))
    price_change_1h = _f(token_stats.get("price_change_1h_percent") or live_stats.get("price_change_1h_percent"))
    price_change_24h = _f(token_stats.get("price_change_24h_percent") or live_stats.get("price_change_24h_percent"))
    market_quality = _f(candidate.get("market_quality_score") or scanner.get("market_quality_score"))
    readiness = _f(candidate.get("readiness_score"))

    narrative, archetype = _classify_narrative(symbol, known, profile)
    memory_type = str(known.get("memory_type") or ("established_runner" if profile else "new_memory"))
    community_proof = str(
        known.get("community_proof")
        or profile.get("thesis")
        or "Community proof is inferred from market quality, volume, runner memory, and liquidity until social data is connected."
    )
    why_it_matters = str(
        known.get("why_it_matters")
        or profile.get("thesis")
        or "Potential interest depends on whether the token keeps volume, liquidity, and holder attention after initial discovery."
    )

    narrative_score = 78.0 if known else 68.0 if profile else 46.0
    memory_score = 85.0 if known else 72.0 if profile else 38.0
    community_score = _clamp((market_quality * 0.45) + min(volume_24h / 25_000.0, 25.0) + min(liquidity / 20_000.0, 20.0))
    catalyst_confidence = _f((catalyst_event or {}).get("confidence"))
    catalyst_strength = _catalyst_strength(
        catalyst_event=catalyst_event,
        runner_state=runner_state,
        buy_pressure=buy_pressure,
        vol_accel=vol_accel,
        volume_24h=volume_24h,
        liquidity=liquidity,
        price_change_1h=price_change_1h,
        price_change_24h=price_change_24h,
    )
    catalyst_score = _clamp(
        (readiness * 0.45)
        + (buy_pressure * 0.18)
        + (vol_accel * 2.5)
        + min(catalyst_confidence * 0.12, 11.0)
        + _f(catalyst_strength.get("score")) * 0.18
    )
    tradeability_score = _clamp(min(liquidity / 5_000.0, 40.0) + min(volume_24h / 20_000.0, 35.0) + (20.0 if marketcap > 1_000_000 else 8.0))
    risk_score = _clamp(82.0 - (25.0 if "rug" in blocker else 0.0) - (20.0 if "holder" in blocker else 0.0) - (15.0 if "unstable" in blocker else 0.0))
    cluster_ctx = dict(wallet_cluster or {})
    cluster_label = str(cluster_ctx.get("cluster_label") or "NO_CLUSTER_DATA").upper()
    cluster_state = str(cluster_ctx.get("accumulation_state") or "QUIET").upper()
    insider_like_score = _f(cluster_ctx.get("insider_like_score"))
    distribution_risk_score = _f(cluster_ctx.get("distribution_risk_score"))
    accumulation_score = _f(cluster_ctx.get("accumulation_score"))
    repeat_operator_score = _f(cluster_ctx.get("repeat_operator_score"))
    wallet_trust_score = _f(cluster_ctx.get("wallet_trust_score"), 50.0)
    attribution_ctx = dict(cluster_ctx.get("attribution_summary") or {})
    attribution_runner_rate = _f(attribution_ctx.get("runner_rate_pct"))
    if distribution_risk_score >= 65.0:
        risk_score = _clamp(risk_score - 14.0)
    elif cluster_state == "ACCUMULATING" and insider_like_score >= 50.0 and distribution_risk_score < 45.0:
        risk_score = _clamp(risk_score + 4.0)
    if wallet_trust_score >= 62.0 and distribution_risk_score < 50.0:
        risk_score = _clamp(risk_score + 3.0)
    outcome_bonus = min(_f(outcome.get("avg_max_return_pct")), 20.0) + (_f(outcome.get("good_n")) * 4.0) - (_f(outcome.get("bad_n")) * 6.0)
    outcome_tuning_bonus = float((narrative_tuning or {}).get(narrative) or 0.0)
    good_coin = _good_coin_filter_v2(
        mint=mint,
        symbol=symbol,
        known=known,
        profile=profile,
        marketcap=marketcap,
        liquidity=liquidity,
        volume_24h=volume_24h,
        buy_pressure=buy_pressure,
        market_quality=market_quality,
        risk_score=risk_score,
        blocker=blocker,
    )
    too_late = _too_late_detector(
        runner_state=runner_state,
        marketcap=marketcap,
        liquidity=liquidity,
        volume_24h=volume_24h,
        buy_pressure=buy_pressure,
        vol_accel=vol_accel,
        price_change_1h=price_change_1h,
        price_change_24h=price_change_24h,
        catalyst_label=str(catalyst_strength.get("label") or ""),
    )
    good_adjustment = (
        7.0
        if good_coin["status"] == "PASS"
        else -4.0
        if good_coin["status"] == "REVIEW"
        else -22.0
    )
    too_late_penalty = _f(too_late.get("score")) * 0.18
    research_score = _clamp(
        narrative_score * 0.16
        + memory_score * 0.18
        + community_score * 0.16
        + catalyst_score * 0.22
        + tradeability_score * 0.16
        + risk_score * 0.12
        + outcome_bonus
        + outcome_tuning_bonus
        + (min(accumulation_score, 75.0) * 0.055 if cluster_state == "ACCUMULATING" else 0.0)
        + (min(repeat_operator_score, 80.0) * 0.045 if distribution_risk_score < 50.0 else 0.0)
        + (max(wallet_trust_score - 50.0, 0.0) * 0.12 if distribution_risk_score < 50.0 else 0.0)
        + (min(attribution_runner_rate, 80.0) * 0.035 if int(attribution_ctx.get("sample_n") or 0) >= 2 else 0.0)
        - (distribution_risk_score * 0.12)
        + good_adjustment
        - too_late_penalty
    )
    conviction = _conviction_band(
        research_score=research_score,
        good_coin=good_coin,
        catalyst=catalyst_strength,
        too_late=too_late,
        risk_score=risk_score,
        tradeability_score=tradeability_score,
        runner_state=runner_state,
        proof_status=proof_status,
    )
    action = _action_from_conviction(conviction, runner_state, proof_status, research_score)
    operator_priority = _clamp(
        research_score * 0.46
        + _f(good_coin.get("score")) * 0.22
        + _f(catalyst_strength.get("score")) * 0.24
        + (min(insider_like_score, 85.0) * 0.06 if cluster_state in {"ACCUMULATING", "EARLY_CLUSTER"} else 0.0)
        + (min(repeat_operator_score, 80.0) * 0.06 if distribution_risk_score < 50.0 else 0.0)
        + (max(wallet_trust_score - 50.0, 0.0) * 0.18 if distribution_risk_score < 50.0 else 0.0)
        - _f(too_late.get("score")) * 0.28
        - distribution_risk_score * 0.22
        + (8.0 if conviction == "BUYABLE" else 4.0 if conviction == "TRIGGERED" else 0.0)
    )
    entry_signal = _entry_timing_signal(
        good_coin=good_coin,
        catalyst=catalyst_strength,
        too_late=too_late,
        research_score=research_score,
        risk_score=risk_score,
        tradeability_score=tradeability_score,
        buy_pressure=buy_pressure,
        vol_accel=vol_accel,
        volume_24h=volume_24h,
        liquidity=liquidity,
        price_change_1h=price_change_1h,
        price_change_24h=price_change_24h,
        runner_state=runner_state,
        proof_status=proof_status,
        blocker=blocker,
        cluster_ctx=cluster_ctx,
    )
    entry_signal = _apply_entry_freshness_guard(entry_signal, token_stats)
    if entry_signal["state"] == "ENTRY_NOW":
        operator_priority = _clamp(operator_priority + 8.0)
        if action == "WATCH":
            action = "MANUAL_REVIEW"
    elif entry_signal["state"] == "ARMED":
        operator_priority = _clamp(operator_priority + 3.0)
    elif entry_signal["state"] in {"AVOID_CHASE", "EXIT_PRESSURE"}:
        operator_priority = _clamp(operator_priority - 10.0)

    execution_alignment = _execution_alignment_signal(
        action=action,
        conviction=conviction,
        entry_signal=entry_signal,
        proof_status=proof_status,
        runner_state=runner_state,
        blocker=blocker,
    )

    narrative_intel = _build_narrative_intelligence_v2(
        symbol=symbol,
        narrative=narrative,
        archetype=archetype,
        memory_type=memory_type,
        known=known,
        profile=profile,
        catalyst_event=catalyst_event,
        catalyst_strength=catalyst_strength,
        good_coin=good_coin,
        too_late=too_late,
        entry_signal=entry_signal,
        runner_state=runner_state,
        proof_status=proof_status,
        marketcap=marketcap,
        liquidity=liquidity,
        volume_24h=volume_24h,
        buy_pressure=buy_pressure,
        vol_accel=vol_accel,
        price_change_1h=price_change_1h,
        price_change_24h=price_change_24h,
        risk_score=risk_score,
        cluster_ctx=cluster_ctx,
    )
    signal_quality = _signal_quality_tier(
        execution_alignment=execution_alignment,
        narrative_intel=narrative_intel,
        good_coin=good_coin,
        too_late=too_late,
        risk_score=risk_score,
        liquidity=liquidity,
        volume_24h=volume_24h,
        buy_pressure=buy_pressure,
        vol_accel=vol_accel,
    )
    entry_zone = _build_entry_zone_engine(
        marketcap=marketcap,
        liquidity=liquidity,
        volume_24h=volume_24h,
        buy_pressure=buy_pressure,
        vol_accel=vol_accel,
        price_change_1h=price_change_1h,
        price_change_24h=price_change_24h,
        entry_signal=entry_signal,
        execution_alignment=execution_alignment,
        too_late=too_late,
        signal_quality=signal_quality,
        risk_score=risk_score,
        distribution_risk_score=distribution_risk_score,
    )

    entry_window = str(candidate.get("entry_window") or lifecycle.get("entry_window") or "")
    fuel_quality = str(candidate.get("fuel_quality") or lifecycle.get("fuel_quality") or "")
    move_phase = str(candidate.get("move_phase") or lifecycle.get("move_phase") or "")
    catalyst_ctx = _catalyst_context(
        runner_state=runner_state,
        proof_status=proof_status,
        blocker=blocker,
        buy_pressure=buy_pressure,
        vol_accel=vol_accel,
        volume_24h=volume_24h,
        liquidity=liquidity,
        price_change_1h=price_change_1h,
        price_change_24h=price_change_24h,
    )
    if catalyst_event:
        catalyst_ctx = {
            "catalyst_type": str(catalyst_event.get("event_type") or catalyst_ctx["catalyst_type"]).lower(),
            "why_now": str(catalyst_event.get("detail") or catalyst_event.get("headline") or catalyst_ctx["why_now"]),
        }
    catalyst = (
        f"{runner_state.replace('_', ' ').lower()} with buy pressure {buy_pressure:.1f} and volume acceleration {vol_accel:.2f}."
        if runner_state
        else f"Research catalyst is volume/liquidity expansion: buy pressure {buy_pressure:.1f}, vol accel {vol_accel:.2f}."
    )
    catalyst = f"{catalyst} Why now: {catalyst_ctx['why_now']}"
    tradeability = f"Market cap {marketcap:,.0f}, liquidity {liquidity:,.0f}, 24h volume {volume_24h:,.0f}."
    cluster_note = ""
    if cluster_label != "NO_CLUSTER_DATA":
        cluster_note = (
            f" Wallet cluster: {cluster_label.replace('_', ' ').lower()} "
            f"({insider_like_score:.0f}) / {cluster_state.replace('_', ' ').lower()}; "
            f"repeat {repeat_operator_score:.0f}, trust {wallet_trust_score:.0f}, "
            f"distribution {distribution_risk_score:.0f}."
        )
    risk_summary = f"Primary blocker: {blocker or 'none'}; risk score {risk_score:.0f}; too-late risk {too_late['label'].lower()} ({too_late['score']:.0f}).{cluster_note}"
    system_thesis = (
        f"{why_it_matters} Conviction band: {conviction.replace('_', ' ').lower()}; "
        f"action: {action.replace('_', ' ').lower()}. "
        f"Narrative intel: {str(narrative_intel.get('research_note') or '')}"
    )
    invalidation = (
        "Invalidate if volume fades, buy pressure drops below 45, quality turns unstable, or extension risk rises."
        if conviction in {"BUYABLE", "TRIGGERED", "WATCH"}
        else "Avoid chasing until the extension cools or a fresh base forms."
        if conviction == "TOO_LATE"
        else "No active thesis until quality, momentum, or risk improves."
    )
    trade_thesis = _build_trade_thesis(
        symbol=symbol,
        why_it_matters=why_it_matters,
        conviction=conviction,
        action=action,
        narrative_intel=narrative_intel,
        good_coin=good_coin,
        catalyst_strength=catalyst_strength,
        too_late=too_late,
        entry_zone=entry_zone,
        entry_signal=entry_signal,
        execution_alignment=execution_alignment,
        cluster_ctx=cluster_ctx,
        risk_summary=risk_summary,
        invalidation=invalidation,
    )
    position_plan = _build_position_plan(
        marketcap=marketcap,
        liquidity=liquidity,
        volume_24h=volume_24h,
        risk_score=risk_score,
        distribution_risk_score=distribution_risk_score,
        entry_zone=entry_zone,
        entry_signal=entry_signal,
        execution_alignment=execution_alignment,
        signal_quality=signal_quality,
    )
    exit_intelligence = _build_exit_intelligence_engine(
        marketcap=marketcap,
        buy_pressure=buy_pressure,
        vol_accel=vol_accel,
        volume_24h=volume_24h,
        liquidity=liquidity,
        price_change_1h=price_change_1h,
        price_change_24h=price_change_24h,
        risk_score=risk_score,
        distribution_risk_score=distribution_risk_score,
        too_late=too_late,
        entry_zone=entry_zone,
        signal_quality=signal_quality,
        trade_thesis=trade_thesis,
    )
    tags = [
        narrative,
        memory_type,
        archetype,
        runner_state.lower() if runner_state else "runner_unknown",
        conviction.lower(),
        action.lower(),
    ]
    evidence = {
        "why_it_matters": why_it_matters,
        "profile": profile,
        "known_memory": known,
        "scores": {
            "narrative": round(narrative_score, 1),
            "memory": round(memory_score, 1),
            "community": round(community_score, 1),
            "catalyst": round(catalyst_score, 1),
            "tradeability": round(tradeability_score, 1),
            "risk": round(risk_score, 1),
            "good_coin": round(_f(good_coin.get("score")), 1),
            "catalyst_strength": round(_f(catalyst_strength.get("score")), 1),
            "too_late": round(_f(too_late.get("score")), 1),
        },
        "latest_catalyst": catalyst_event or {},
        "good_coin": good_coin,
        "catalyst_strength": catalyst_strength,
        "too_late": too_late,
        "wallet_cluster_intelligence": cluster_ctx,
        "entry_timing": entry_signal,
        "execution_alignment": execution_alignment,
        "narrative_intelligence": narrative_intel,
        "signal_quality": signal_quality,
        "entry_zone": entry_zone,
        "trade_thesis": trade_thesis,
        "position_plan": position_plan,
        "exit_intelligence": exit_intelligence,
    }
    return {
        "mint": mint,
        "symbol": symbol,
        "generated_at": _iso(),
        "source": "proof_candidate" if candidate else "known_memory",
        "narrative": narrative,
        "memory_type": memory_type,
        "archetype": archetype,
        "community_proof": community_proof,
        "catalyst": catalyst,
        "tradeability": tradeability,
        "risk_summary": risk_summary,
        "system_thesis": system_thesis,
        "invalidation": invalidation,
        "action": action,
        "research_score": round(research_score, 1),
        "narrative_score": round(narrative_score, 1),
        "memory_score": round(memory_score, 1),
        "community_score": round(community_score, 1),
        "catalyst_score": round(catalyst_score, 1),
        "tradeability_score": round(tradeability_score, 1),
        "risk_score": round(risk_score, 1),
        "outcome_sample_n": int(outcome.get("sample_n") or 0),
        "outcome_good_n": int(outcome.get("good_n") or 0),
        "outcome_bad_n": int(outcome.get("bad_n") or 0),
        "avg_max_return_pct": round(_f(outcome.get("avg_max_return_pct")), 2),
        "outcome_tuning_bonus": round(outcome_tuning_bonus, 2),
        "last_outcome_label": outcome.get("last_outcome_label"),
        "marketcap": round(marketcap, 2),
        "liquidity": round(liquidity, 2),
        "volume_24h": round(volume_24h, 2),
        "buy_pressure": round(buy_pressure, 1),
        "vol_acceleration": round(vol_accel, 2),
        "runner_state": runner_state,
        "proof_status": proof_status,
        "blocker_key": blocker,
        "entry_window": entry_window,
        "fuel_quality": fuel_quality,
        "move_phase": move_phase,
        "catalyst_type": catalyst_ctx["catalyst_type"],
        "why_now": catalyst_ctx["why_now"],
        "last_catalyst_type": str((catalyst_event or {}).get("event_type") or "").upper() or None,
        "last_catalyst_ts": (catalyst_event or {}).get("event_ts"),
        "last_catalyst_confidence": round(catalyst_confidence, 1),
        "last_catalyst_headline": (catalyst_event or {}).get("headline"),
        "catalyst_event_count": int((catalyst_event or {}).get("event_count") or (1 if catalyst_event else 0)),
        "good_coin_score": round(_f(good_coin.get("score")), 1),
        "good_coin_status": good_coin.get("status"),
        "good_coin_reasons": list(good_coin.get("reasons") or []),
        "good_coin_blockers": list(good_coin.get("blockers") or []),
        "conviction_band": conviction,
        "catalyst_strength_score": round(_f(catalyst_strength.get("score")), 1),
        "catalyst_strength_label": catalyst_strength.get("label"),
        "too_late_score": round(_f(too_late.get("score")), 1),
        "too_late_label": too_late.get("label"),
        "too_late_reasons": list(too_late.get("reasons") or []),
        "operator_priority": round(operator_priority, 1),
        "entry_state": entry_signal["state"],
        "entry_score": entry_signal["score"],
        "entry_blocker": entry_signal.get("last_blocker"),
        "entry_instruction": entry_signal["instruction"],
        "entry_reasons": list(entry_signal.get("reasons") or []),
        "entry_blockers": list(entry_signal.get("blockers") or []),
        "entry_data_age_seconds": entry_signal.get("data_age_seconds"),
        "entry_guard_status": entry_signal.get("guard_status"),
        "entry_timing": entry_signal,
        "entry_zone": entry_zone,
        "trade_thesis": trade_thesis,
        "position_plan": position_plan,
        "exit_intelligence": exit_intelligence,
        "execution_alignment_state": execution_alignment["state"],
        "execution_alignment_label": execution_alignment["label"],
        "execution_alignment_confidence": execution_alignment["confidence"],
        "execution_deployable": execution_alignment["deployable"],
        "execution_blockers": execution_alignment["blockers"],
        "execution_alignment": execution_alignment,
        "signal_quality_tier": signal_quality["tier"],
        "signal_quality_score": signal_quality["score"],
        "signal_quality": signal_quality,
        "narrative_hooks": narrative_intel["hooks"],
        "attention_persistence_score": narrative_intel["attention_persistence_score"],
        "attention_persistence_label": narrative_intel["attention_persistence_label"],
        "catalyst_quality_score": narrative_intel["catalyst_quality_score"],
        "catalyst_quality_label": narrative_intel["catalyst_quality_label"],
        "contradictions": narrative_intel["contradictions"],
        "comparable_runner": narrative_intel["comparable_runner"],
        "comparable_runner_confidence": narrative_intel["comparable_runner_confidence"],
        "sustainability_score": narrative_intel["sustainability_score"],
        "sustainability_label": narrative_intel["sustainability_label"],
        "sustainability_checks": narrative_intel["sustainability_checks"],
        "narrative_intelligence": narrative_intel,
        "wallet_cluster_intelligence": cluster_ctx,
        "rotation_state": "UNRANKED",
        "tags": tags,
        "evidence": evidence,
        "raw": {
            "candidate": candidate,
            "token_stats": token_stats,
            "outcome": outcome,
        },
    }


def _known_candidate(memory: dict[str, Any], mint: str, token_stats: dict[str, Any]) -> dict[str, Any]:
    return {
        "mint": mint,
        "symbol": memory.get("symbol") or token_stats.get("symbol"),
        "proof_status": "RESEARCH_ONLY",
        "runner_policy_state": "RUNNER_WATCHING",
        "market_quality_score": 55.0 if token_stats else 45.0,
        "readiness_score": 50.0,
        "proof_components": {
            "scanner": {
                "market_quality_score": 55.0 if token_stats else 45.0,
                "buy_pressure": (token_stats.get("inputs") or {}).get("buy_pressure_1h", 50.0),
                "vol_acceleration": (token_stats.get("inputs") or {}).get("vol_acceleration_est", 0.0),
            },
            "established_runner": {
                "profile": memory.get("archetype"),
                "thesis": memory.get("why_it_matters"),
            },
        },
        "established_runner_profile": {
            "profile": memory.get("archetype"),
            "thesis": memory.get("why_it_matters"),
        },
    }


def _persist_dossiers(dossiers: list[dict[str, Any]]) -> None:
    with get_conn() as conn:
        _ensure_schema(conn)
        for item in dossiers:
            conn.execute(
                """
                INSERT OR REPLACE INTO memecoin_research_dossiers
                (mint, symbol, generated_at, source, narrative, memory_type, archetype,
                 community_proof, catalyst, tradeability, risk_summary, system_thesis,
                 invalidation, action, research_score, narrative_score, memory_score,
                 community_score, catalyst_score, tradeability_score, risk_score,
                 outcome_sample_n, outcome_good_n, outcome_bad_n, avg_max_return_pct,
                 last_outcome_label, marketcap, liquidity, volume_24h, buy_pressure,
                 vol_acceleration, runner_state, proof_status, blocker_key, entry_window,
                 fuel_quality, move_phase, catalyst_type, why_now, rotation_state,
                 outcome_tuning_bonus, last_catalyst_type, last_catalyst_ts,
                 last_catalyst_confidence, last_catalyst_headline, catalyst_event_count,
                 good_coin_score, good_coin_status, good_coin_reasons_json,
                 good_coin_blockers_json, conviction_band, catalyst_strength_score,
                 catalyst_strength_label, too_late_score, too_late_label,
                 too_late_reasons_json, operator_priority, entry_state, entry_score,
                 entry_blocker, entry_instruction, entry_reasons_json, entry_blockers_json,
                 entry_data_age_seconds, entry_guard_status,
                 narrative_hooks_json, attention_persistence_score, attention_persistence_label,
                 catalyst_quality_score, catalyst_quality_label, contradictions_json,
                 comparable_runner, comparable_runner_confidence, sustainability_score,
                 sustainability_label, sustainability_checks_json, narrative_intelligence_json,
                 tags_json, evidence_json, raw_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item["mint"],
                    item["symbol"],
                    item["generated_at"],
                    item["source"],
                    item["narrative"],
                    item["memory_type"],
                    item["archetype"],
                    item["community_proof"],
                    item["catalyst"],
                    item["tradeability"],
                    item["risk_summary"],
                    item["system_thesis"],
                    item["invalidation"],
                    item["action"],
                    item["research_score"],
                    item["narrative_score"],
                    item["memory_score"],
                    item["community_score"],
                    item["catalyst_score"],
                    item["tradeability_score"],
                    item["risk_score"],
                    item["outcome_sample_n"],
                    item["outcome_good_n"],
                    item["outcome_bad_n"],
                    item["avg_max_return_pct"],
                    item["last_outcome_label"],
                    item["marketcap"],
                    item["liquidity"],
                    item["volume_24h"],
                    item["buy_pressure"],
                    item["vol_acceleration"],
                    item["runner_state"],
                    item["proof_status"],
                    item["blocker_key"],
                    item["entry_window"],
                    item["fuel_quality"],
                    item["move_phase"],
                    item.get("catalyst_type"),
                    item.get("why_now"),
                    item.get("rotation_state"),
                    item.get("outcome_tuning_bonus"),
                    item.get("last_catalyst_type"),
                    item.get("last_catalyst_ts"),
                    item.get("last_catalyst_confidence"),
                    item.get("last_catalyst_headline"),
                    item.get("catalyst_event_count"),
                    item.get("good_coin_score"),
                    item.get("good_coin_status"),
                    json.dumps(item.get("good_coin_reasons") or [], separators=(",", ":")),
                    json.dumps(item.get("good_coin_blockers") or [], separators=(",", ":")),
                    item.get("conviction_band"),
                    item.get("catalyst_strength_score"),
                    item.get("catalyst_strength_label"),
                    item.get("too_late_score"),
                    item.get("too_late_label"),
                    json.dumps(item.get("too_late_reasons") or [], separators=(",", ":")),
                    item.get("operator_priority"),
                    item.get("entry_state"),
                    item.get("entry_score"),
                    item.get("entry_blocker"),
                    item.get("entry_instruction"),
                    json.dumps(item.get("entry_reasons") or [], separators=(",", ":")),
                    json.dumps(item.get("entry_blockers") or [], separators=(",", ":")),
                    item.get("entry_data_age_seconds"),
                    item.get("entry_guard_status"),
                    json.dumps(item.get("narrative_hooks") or [], separators=(",", ":")),
                    item.get("attention_persistence_score"),
                    item.get("attention_persistence_label"),
                    item.get("catalyst_quality_score"),
                    item.get("catalyst_quality_label"),
                    json.dumps(item.get("contradictions") or [], separators=(",", ":")),
                    item.get("comparable_runner"),
                    item.get("comparable_runner_confidence"),
                    item.get("sustainability_score"),
                    item.get("sustainability_label"),
                    json.dumps(item.get("sustainability_checks") or [], separators=(",", ":")),
                    json.dumps(item.get("narrative_intelligence") or {}, separators=(",", ":")),
                    json.dumps(item["tags"], separators=(",", ":")),
                    json.dumps(item["evidence"], separators=(",", ":")),
                    json.dumps(item["raw"], separators=(",", ":")),
                ),
            )
            _record_entry_signal(conn, item)
        snapshot_inserted = _persist_conviction_snapshots(conn, dossiers)
        conn.execute(
            "INSERT OR REPLACE INTO kv_store (key, value) VALUES (?, ?)",
            (
                "memecoin_research_dossier_status",
                json.dumps(
                    {
                        "generated_at": _iso(),
                        "count": len(dossiers),
                        "top": [
                            {"symbol": d["symbol"], "action": d["action"], "score": d["research_score"]}
                            for d in sorted(dossiers, key=lambda x: x["research_score"], reverse=True)[:5]
                        ],
                        "conviction_snapshots_inserted": snapshot_inserted,
                    },
                    separators=(",", ":"),
                ),
            ),
        )
        conn.commit()


def _dossier_from_row(row: Any) -> dict[str, Any]:
    item = dict(row)
    item["tags"] = _json_load(item.pop("tags_json", None), [])
    item["evidence"] = _json_load(item.pop("evidence_json", None), {})
    item["raw"] = _json_load(item.pop("raw_json", None), {})
    item["good_coin_reasons"] = _json_load(item.pop("good_coin_reasons_json", None), [])
    item["good_coin_blockers"] = _json_load(item.pop("good_coin_blockers_json", None), [])
    item["too_late_reasons"] = _json_load(item.pop("too_late_reasons_json", None), [])
    item["entry_reasons"] = _json_load(item.pop("entry_reasons_json", None), [])
    item["entry_blockers"] = _json_load(item.pop("entry_blockers_json", None), [])
    item["narrative_hooks"] = _json_load(item.pop("narrative_hooks_json", None), [])
    item["contradictions"] = _json_load(item.pop("contradictions_json", None), [])
    item["sustainability_checks"] = _json_load(item.pop("sustainability_checks_json", None), [])
    narrative_intel = _json_load(item.pop("narrative_intelligence_json", None), {})
    for key in (
        "source",
        "narrative",
        "memory_type",
        "archetype",
        "community_proof",
        "catalyst",
        "tradeability",
        "risk_summary",
        "system_thesis",
        "invalidation",
        "action",
        "conviction_band",
    ):
        item[key] = item.get(key) or ""
    wallet_cluster = (item.get("evidence") or {}).get("wallet_cluster_intelligence")
    if isinstance(wallet_cluster, dict):
        item["wallet_cluster_intelligence"] = wallet_cluster
    entry_timing = (item.get("evidence") or {}).get("entry_timing")
    if isinstance(entry_timing, dict):
        item["entry_timing"] = entry_timing
    elif item.get("entry_state"):
        item["entry_timing"] = {
            "state": item.get("entry_state"),
            "score": item.get("entry_score"),
            "last_blocker": item.get("entry_blocker"),
            "instruction": item.get("entry_instruction"),
            "reasons": item.get("entry_reasons") or [],
            "blockers": item.get("entry_blockers") or [],
        }
    execution_alignment = (item.get("evidence") or {}).get("execution_alignment")
    if isinstance(execution_alignment, dict):
        item["execution_alignment"] = execution_alignment
        item.setdefault("execution_alignment_state", execution_alignment.get("state"))
        item.setdefault("execution_alignment_label", execution_alignment.get("label"))
        item.setdefault("execution_alignment_confidence", execution_alignment.get("confidence"))
        item.setdefault("execution_deployable", execution_alignment.get("deployable"))
        item.setdefault("execution_blockers", execution_alignment.get("blockers") or [])
    signal_quality = (item.get("evidence") or {}).get("signal_quality")
    if isinstance(signal_quality, dict):
        item["signal_quality"] = signal_quality
        item.setdefault("signal_quality_tier", signal_quality.get("tier"))
        item.setdefault("signal_quality_score", signal_quality.get("score"))
    else:
        item["signal_quality"] = {
            "tier": "UNRATED",
            "reasons": ["cached dossier predates signal quality scoring"],
            "blockers": ["needs_research_refresh"],
        }
        item.setdefault("signal_quality_tier", "UNRATED")
    entry_zone = (item.get("evidence") or {}).get("entry_zone")
    if isinstance(entry_zone, dict):
        item["entry_zone"] = entry_zone
    else:
        item["entry_zone"] = _fallback_entry_zone_from_dossier(item)
    trade_thesis = (item.get("evidence") or {}).get("trade_thesis")
    if isinstance(trade_thesis, dict):
        item["trade_thesis"] = trade_thesis
    else:
        item["trade_thesis"] = _fallback_trade_thesis_from_dossier(item, dict(item.get("entry_zone") or {}))
    position_plan = (item.get("evidence") or {}).get("position_plan")
    if isinstance(position_plan, dict):
        item["position_plan"] = position_plan
    else:
        item["position_plan"] = _fallback_position_plan_from_dossier(item, dict(item.get("entry_zone") or {}))
    exit_intelligence = (item.get("evidence") or {}).get("exit_intelligence")
    if isinstance(exit_intelligence, dict):
        item["exit_intelligence"] = exit_intelligence
    else:
        item["exit_intelligence"] = _fallback_exit_intelligence_from_dossier(item, dict(item.get("entry_zone") or {}))
    if isinstance(narrative_intel, dict) and narrative_intel:
        item["narrative_intelligence"] = narrative_intel
    else:
        evidence_intel = (item.get("evidence") or {}).get("narrative_intelligence")
        if isinstance(evidence_intel, dict):
            item["narrative_intelligence"] = evidence_intel
    if not item.get("narrative_hooks") and isinstance(item.get("narrative_intelligence"), dict):
        item["narrative_hooks"] = list((item["narrative_intelligence"].get("hooks") or [])[:6])
    if not item.get("contradictions") and isinstance(item.get("narrative_intelligence"), dict):
        item["contradictions"] = list((item["narrative_intelligence"].get("contradictions") or [])[:6])
    if not item.get("sustainability_checks") and isinstance(item.get("narrative_intelligence"), dict):
        item["sustainability_checks"] = list((item["narrative_intelligence"].get("sustainability_checks") or [])[:8])
    return item


def _signal_hash(item: dict[str, Any]) -> str:
    parts = [
        str(item.get("mint") or ""),
        str(item.get("entry_state") or ""),
        str(item.get("entry_blocker") or ""),
        str(item.get("conviction_band") or ""),
        str(round(_f(item.get("entry_score")), 0)),
    ]
    return "|".join(parts)


def _record_entry_signal(conn, item: dict[str, Any]) -> int:
    state = str(item.get("entry_state") or "").upper()
    if state not in {"ENTRY_NOW", "ARMED"}:
        return 0
    mint = str(item.get("mint") or "").strip()
    if not mint:
        return 0
    recent = conn.execute(
        """
        SELECT id
        FROM memecoin_entry_signals
        WHERE mint=?
          AND entry_state=?
          AND created_at >= ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (mint, state, _event_age_cutoff(ENTRY_SIGNAL_COOLDOWN_MINUTES)),
    ).fetchone()
    if recent:
        return 0
    raw = dict(item.get("raw") or {})
    stats = dict(raw.get("token_stats") or {})
    entry_timing = dict(item.get("entry_timing") or {})
    created_at = str(item.get("generated_at") or _iso())
    conn.execute(
        """
        INSERT INTO memecoin_entry_signals
        (created_at, mint, symbol, entry_state, entry_score, last_blocker, guard_status,
         data_age_seconds, entry_price, entry_marketcap, entry_liquidity, entry_volume_24h,
         token_stats_ts_utc, research_score, operator_priority, conviction_band, action,
         signal_hash, status, reasons_json, blockers_json, dossier_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            created_at,
            mint,
            item.get("symbol"),
            state,
            _f(item.get("entry_score")),
            item.get("entry_blocker"),
            item.get("entry_guard_status"),
            item.get("entry_data_age_seconds"),
            _f(stats.get("price")),
            _f(item.get("marketcap")),
            _f(item.get("liquidity")),
            _f(item.get("volume_24h")),
            entry_timing.get("token_stats_ts_utc") or stats.get("ts_utc"),
            _f(item.get("research_score")),
            _f(item.get("operator_priority")),
            item.get("conviction_band"),
            item.get("action"),
            _signal_hash(item),
            "OPEN",
            json.dumps(item.get("entry_reasons") or [], separators=(",", ":")),
            json.dumps(item.get("entry_blockers") or [], separators=(",", ":")),
            json.dumps(item, separators=(",", ":")),
        ),
    )
    signal_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0] or 0)
    if state == "ENTRY_NOW" and signal_id:
        conn.execute(
            """
            INSERT OR IGNORE INTO memecoin_entry_paper_trades
            (signal_id, mint, symbol, entry_ts, entry_price, entry_marketcap, paper_units, status, notes_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'OPEN', ?)
            """,
            (
                signal_id,
                mint,
                item.get("symbol"),
                created_at,
                _f(stats.get("price")),
                _f(item.get("marketcap")),
                float(ENTRY_SIGNAL_PAPER_UNITS),
                json.dumps({"source": "entry_signal", "entry_state": state}, separators=(",", ":")),
            ),
        )
    return 1


def _latest_stats_at_or_after(conn, mint: str, entry_ts: str, minutes: int) -> dict[str, Any] | None:
    target = (_parse_dt(entry_ts) or datetime.now(timezone.utc)) + timedelta(minutes=max(0, int(minutes)))
    row = conn.execute(
        """
        SELECT *
        FROM memecoin_token_stats_snapshots
        WHERE mint=?
          AND datetime(ts_utc) >= datetime(?)
        ORDER BY datetime(ts_utc) ASC, id ASC
        LIMIT 1
        """,
        (mint, target.isoformat()),
    ).fetchone()
    return dict(row) if row else None


def _entry_exit_review_state(
    *,
    latest_return: float,
    max_return: float,
    drawdown: float,
    age_hours: float,
    latest_item: dict[str, Any],
) -> tuple[str, str, str | None]:
    buy_pressure = _f((latest_item.get("inputs") or {}).get("buy_pressure_1h"), 50.0)
    vol_accel = _f((latest_item.get("inputs") or {}).get("vol_acceleration_est"))
    volume_24h = _f(latest_item.get("volume_24h_usd"))
    liquidity = _f(latest_item.get("liquidity"))
    volume_turnover = volume_24h / liquidity if liquidity > 0 else 0.0
    if latest_return <= -18:
        return "EXIT_STOP", "close_paper", f"paper_entry_stop — return {latest_return:+.1f}%"
    if max_return >= 50 and drawdown <= -18:
        return "EXIT_GIVEBACK", "close_paper", f"paper_entry_giveback — max {max_return:+.1f}% gave back {abs(drawdown):.1f}%"
    if max_return >= 25 and drawdown <= -12:
        return "TRIM_OR_PROTECT", "protect_profit", f"paper_entry_protect — max {max_return:+.1f}% drawdown {abs(drawdown):.1f}%"
    if latest_return >= 18 and (buy_pressure < 48 or vol_accel < 1.8):
        return "TAKE_PROFIT_WATCH", "protect_profit", f"paper_entry_profit_fade — return {latest_return:+.1f}% with fading flow"
    if age_hours >= 24 and max_return < 10:
        return "EXIT_STALE", "close_paper", f"paper_entry_stale — open {age_hours:.1f}h without enough expansion"
    if buy_pressure >= 55 and vol_accel >= 3 and volume_turnover >= 1:
        return "HOLD_EXPANDING", "hold_paper", None
    return "HOLD_MONITOR", "hold_paper", None


def _maybe_record_entry_exit_snapshot(
    conn,
    *,
    item: dict[str, Any],
    latest_item: dict[str, Any],
    latest_return: float,
    max_return: float,
    drawdown: float,
    age_hours: float,
    outcome_label: str,
) -> None:
    paper_id = int(item.get("id") or 0)
    if paper_id <= 0:
        return
    cutoff = _event_age_cutoff(ENTRY_EXIT_SNAPSHOT_MIN_INTERVAL_MINUTES)
    recent = conn.execute(
        """
        SELECT 1
        FROM memecoin_exit_signal_snapshots
        WHERE trade_id=?
          AND ts_utc >= ?
        LIMIT 1
        """,
        (-paper_id, cutoff),
    ).fetchone()
    if recent:
        return
    stats_inputs = dict(latest_item.get("inputs") or {})
    review_state, recommended_action, exit_reason = _entry_exit_review_state(
        latest_return=latest_return,
        max_return=max_return,
        drawdown=drawdown,
        age_hours=age_hours,
        latest_item=latest_item,
    )
    volume_24h = _f(latest_item.get("volume_24h_usd"))
    liquidity = _f(latest_item.get("liquidity"))
    buy_pressure = _f(stats_inputs.get("buy_pressure_1h"), 50.0)
    vol_accel = _f(stats_inputs.get("vol_acceleration_est"))
    volume_trend = (
        "accelerating" if vol_accel >= 3 and buy_pressure >= 52 else
        "fading" if vol_accel < 1.8 or buy_pressure < 48 else
        "mixed"
    )
    notes = {
            "source": "memecoin_entry_paper_trade",
            "paper_trade_id": paper_id,
            "signal_id": item.get("signal_id"),
            "review_state": review_state,
            "recommended_action": recommended_action,
            "exit_reason": exit_reason,
            "max_return_pct": round(max_return, 2),
            "drawdown_from_max_pct": round(drawdown, 2),
            "buy_pressure": round(buy_pressure, 1),
            "vol_acceleration": round(vol_accel, 2),
            "volume_turnover": round(volume_24h / liquidity, 2) if liquidity > 0 else 0,
    }
    conn.execute(
        """
        INSERT INTO memecoin_exit_signal_snapshots
            (ts_utc, trade_id, mint, symbol, age_minutes, current_price,
             current_return_pct, current_scanner_score, current_timing_score,
             current_market_quality_score, current_support_score, current_readiness_score,
             current_profit_room_score, current_profit_room_label,
             current_market_quality_verdict, current_wallet_behavior_state,
             current_smart_money_quality, current_proof_score, current_proof_reason,
             volume_trend_label, scanner_still_actionable,
             large_trade_buy_volume, large_trade_sell_volume,
             large_trade_buy_count, large_trade_sell_count,
             token_stats_ts_utc, notes_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            _iso(),
            -paper_id,
            str(item.get("mint") or ""),
            str(item.get("symbol") or ""),
            round(age_hours * 60.0, 1),
            _f(latest_item.get("price")),
            round(latest_return, 2),
            None,
            None,
            _f(stats_inputs.get("market_quality_score")),
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            outcome_label,
            volume_trend,
            1 if review_state.startswith("HOLD") else 0,
            None,
            None,
            0,
            0,
            str(latest_item.get("ts_utc") or ""),
            json.dumps(notes, separators=(",", ":")),
        ),
    )


def _entry_paper_outcome_closeout(
    *,
    latest_return: float,
    max_return: float,
    drawdown: float,
    age_hours: float,
) -> tuple[str, str, str]:
    terminal = age_hours >= ENTRY_OUTCOME_CLOSEOUT_HOURS
    if latest_return <= ENTRY_OUTCOME_LOSS_PCT:
        return "LOSS", "COMPLETE", f"Stopped by return {latest_return:+.1f}%."
    if max_return >= ENTRY_OUTCOME_BIG_WIN_PCT and drawdown <= -18:
        return "GIVEBACK", "COMPLETE", f"Big runner gave back {abs(drawdown):.1f}% from max {max_return:+.1f}%."
    if max_return >= ENTRY_OUTCOME_WIN_PCT and drawdown <= ENTRY_OUTCOME_GIVEBACK_PCT:
        return "GIVEBACK", "COMPLETE" if terminal else "OPEN", f"Winner gave back {abs(drawdown):.1f}% from max {max_return:+.1f}%."
    if max_return >= ENTRY_OUTCOME_BIG_WIN_PCT:
        return "BIG_WIN", "COMPLETE" if terminal else "OPEN", f"Big winner max return {max_return:+.1f}%."
    if max_return >= ENTRY_OUTCOME_WIN_PCT:
        return "WIN", "COMPLETE" if terminal else "OPEN", f"Winner max return {max_return:+.1f}%."
    if terminal and max_return >= ENTRY_OUTCOME_SMALL_WIN_PCT:
        return "SMALL_WIN", "COMPLETE", f"Small win max return {max_return:+.1f}%."
    if terminal:
        return "STALE_NO_MOVE", "COMPLETE", f"Closed after {age_hours:.1f}h without enough expansion."
    return "TRACKING", "OPEN", "Still inside paper outcome window."


def _merge_paper_notes(existing: Any, closeout: dict[str, Any]) -> str:
    notes = _json_load(existing, {})
    if not isinstance(notes, dict):
        notes = {"previous": notes}
    notes["closeout"] = closeout
    return json.dumps(notes, separators=(",", ":"))


def _entry_outcome_is_judged(label: str | None) -> bool:
    return str(label or "").upper() not in {"", "TRACKING", "PENDING", "OPEN"}


def _entry_outcome_is_win(label: str | None) -> bool:
    return str(label or "").upper() in {"BIG_WIN", "WIN", "SMALL_WIN"}


MANUAL_REVIEW_DECISIONS = {"BUY", "WATCH", "SKIP", "TOO_LATE", "BAD_CA", "NEEDS_MORE_PROOF"}


def _manual_review_context_from_dossier(dossier: dict[str, Any]) -> dict[str, Any]:
    entry_zone = dict(dossier.get("entry_zone") or {})
    position_plan = dict(dossier.get("position_plan") or {})
    exit_intel = dict(dossier.get("exit_intelligence") or {})
    return {
        "entry_zone": str(entry_zone.get("zone") or entry_zone.get("label") or dossier.get("entry_state") or "UNKNOWN").upper(),
        "position_stance": str(position_plan.get("stance") or "UNKNOWN").upper(),
        "exit_state": str(exit_intel.get("state") or "UNKNOWN").upper(),
    }


def _manual_decision_outcome_label(decision: str, max_return: float, latest_return: float, age_hours: float) -> tuple[str, bool]:
    decision = str(decision or "").upper()
    terminal = age_hours >= ENTRY_OUTCOME_CLOSEOUT_HOURS
    if decision == "BAD_CA":
        return "EXCLUDED_BAD_CA", True
    if decision in {"BUY"}:
        if latest_return <= ENTRY_OUTCOME_LOSS_PCT:
            return "MANUAL_BUY_LOSS", True
        if max_return >= ENTRY_OUTCOME_BIG_WIN_PCT:
            return "MANUAL_BUY_BIG_WIN", terminal
        if max_return >= ENTRY_OUTCOME_WIN_PCT:
            return "MANUAL_BUY_WIN", terminal
        if terminal and max_return >= ENTRY_OUTCOME_SMALL_WIN_PCT:
            return "MANUAL_BUY_SMALL_WIN", True
        if terminal:
            return "MANUAL_BUY_STALE", True
        return "TRACKING", False
    if decision in {"SKIP", "TOO_LATE", "NEEDS_MORE_PROOF", "WATCH"}:
        if max_return >= ENTRY_OUTCOME_BIG_WIN_PCT:
            return "MANUAL_MISSED_BIG_RUNNER", terminal
        if max_return >= ENTRY_OUTCOME_WIN_PCT:
            return "MANUAL_MISSED_WINNER", terminal
        if terminal and latest_return <= ENTRY_OUTCOME_LOSS_PCT:
            return "MANUAL_CORRECT_AVOID", True
        if terminal and max_return < ENTRY_OUTCOME_SMALL_WIN_PCT:
            return "MANUAL_CORRECT_WAIT", True
        return "TRACKING", False
    return "TRACKING", False


def _manual_outcome_is_judged(label: str | None) -> bool:
    return str(label or "").upper() not in {"", "TRACKING", "PENDING"}


def _manual_outcome_is_good(label: str | None) -> bool:
    return str(label or "").upper() in {
        "MANUAL_BUY_BIG_WIN",
        "MANUAL_BUY_WIN",
        "MANUAL_BUY_SMALL_WIN",
        "MANUAL_CORRECT_AVOID",
        "MANUAL_CORRECT_WAIT",
        "EXCLUDED_BAD_CA",
    }


def _latest_manual_review_decisions(conn) -> dict[str, dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT r.*
        FROM memecoin_manual_review_decisions r
        INNER JOIN (
            SELECT mint, MAX(id) AS max_id
            FROM memecoin_manual_review_decisions
            GROUP BY mint
        ) latest
          ON latest.mint = r.mint AND latest.max_id = r.id
        """
    ).fetchall()
    return {str(row["mint"]): dict(row) for row in rows if row["mint"]}


def _manual_review_public(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    return {
        "id": row.get("id"),
        "created_at": row.get("created_at"),
        "mint": row.get("mint"),
        "symbol": row.get("symbol"),
        "decision": row.get("decision"),
        "operator_note": row.get("operator_note"),
        "source": row.get("source"),
        "research_score": row.get("research_score"),
        "entry_zone": row.get("entry_zone"),
        "position_stance": row.get("position_stance"),
        "exit_state": row.get("exit_state"),
        "entry_marketcap": row.get("entry_marketcap"),
        "current_marketcap": row.get("current_marketcap"),
        "return_1h_pct": row.get("return_1h_pct"),
        "return_4h_pct": row.get("return_4h_pct"),
        "return_24h_pct": row.get("return_24h_pct"),
        "max_return_pct": row.get("max_return_pct"),
        "outcome_label": row.get("outcome_label"),
        "last_eval_at": row.get("last_eval_at"),
    }


def _manual_review_dossier_context(row: dict[str, Any]) -> dict[str, Any]:
    dossier = _json_load(row.get("dossier_json"), {})
    if not isinstance(dossier, dict):
        dossier = {}
    evidence = dossier.get("evidence") if isinstance(dossier.get("evidence"), dict) else {}
    entry_zone = dossier.get("entry_zone") if isinstance(dossier.get("entry_zone"), dict) else {}
    position_plan = dossier.get("position_plan") if isinstance(dossier.get("position_plan"), dict) else {}
    exit_intel = dossier.get("exit_intelligence") if isinstance(dossier.get("exit_intelligence"), dict) else {}
    thesis = dossier.get("trade_thesis") if isinstance(dossier.get("trade_thesis"), dict) else {}
    if not entry_zone and isinstance(evidence, dict):
        entry_zone = evidence.get("entry_zone") if isinstance(evidence.get("entry_zone"), dict) else {}
    if not position_plan and isinstance(evidence, dict):
        position_plan = evidence.get("position_plan") if isinstance(evidence.get("position_plan"), dict) else {}
    if not exit_intel and isinstance(evidence, dict):
        exit_intel = evidence.get("exit_intelligence") if isinstance(evidence.get("exit_intelligence"), dict) else {}
    if not thesis and isinstance(evidence, dict):
        thesis = evidence.get("trade_thesis") if isinstance(evidence.get("trade_thesis"), dict) else {}
    blocker = (
        dossier.get("entry_blocker")
        or dossier.get("blocker_key")
        or dossier.get("last_blocker")
        or (entry_zone or {}).get("blocker")
        or (thesis or {}).get("blocker")
        or "unknown"
    )
    return {
        "entry_zone": str((entry_zone or {}).get("zone") or row.get("entry_zone") or "UNKNOWN").upper(),
        "position_stance": str((position_plan or {}).get("stance") or row.get("position_stance") or "UNKNOWN").upper(),
        "exit_state": str((exit_intel or {}).get("state") or row.get("exit_state") or "UNKNOWN").upper(),
        "blocker": str(blocker or "unknown"),
        "execution_alignment_state": str(dossier.get("execution_alignment_state") or "").upper(),
        "entry_state": str(dossier.get("entry_state") or "").upper(),
        "headline": str((thesis or {}).get("headline") or dossier.get("system_thesis") or dossier.get("why_now") or "").strip(),
        "trigger_contract": str(dossier.get("trigger_contract") or (entry_zone or {}).get("confirmation_needed") or "").strip(),
    }


def _manual_replay_item(row: dict[str, Any]) -> dict[str, Any]:
    ctx = _manual_review_dossier_context(row)
    max_return = _f(row.get("max_return_pct"))
    decision = str(row.get("decision") or "").upper()
    blocker = str(ctx.get("blocker") or "unknown").replace("_", " ")
    return {
        **(_manual_review_public(row) or {}),
        "blocker": ctx.get("blocker"),
        "entry_state": ctx.get("entry_state"),
        "execution_alignment_state": ctx.get("execution_alignment_state"),
        "headline": ctx.get("headline"),
        "trigger_contract": ctx.get("trigger_contract"),
        "why_it_matters": (
            f"{decision} would have missed a {max_return:.0f}% move while blocker was {blocker}."
            if decision != "BUY"
            else f"BUY captured a setup with {max_return:.0f}% max follow-through."
        ),
        "lesson": (
            f"If {blocker} clears on a similar setup, promote from watch to entry review faster."
            if decision in {"WATCH", "NEEDS_MORE_PROOF"}
            else f"Re-check whether {blocker} should block this archetype when quality/history are strong."
            if decision in {"SKIP", "TOO_LATE"}
            else "Use as a positive manual-buy reference once enough similar samples exist."
        ),
    }


def _manual_calibration_guidance(
    *,
    decision_rows: list[dict[str, Any]],
    rows: list[dict[str, Any]],
    judged_n: int,
    missed_n: int,
    good_rate: float,
) -> dict[str, Any]:
    buy_row = next((r for r in decision_rows if str(r.get("decision") or "") == "BUY"), None)
    watch_row = next((r for r in decision_rows if str(r.get("decision") or "") == "WATCH"), None)
    skip_like = [
        r for r in decision_rows
        if str(r.get("decision") or "") in {"SKIP", "TOO_LATE", "NEEDS_MORE_PROOF"}
    ]
    missed_rows = [
        r for r in rows
        if "MISSED" in str(r.get("outcome_label") or "").upper()
           or (
               str(r.get("decision") or "").upper() in {"WATCH", "SKIP", "TOO_LATE", "NEEDS_MORE_PROOF"}
               and _f(r.get("max_return_pct")) >= ENTRY_OUTCOME_WIN_PCT
           )
    ]
    missed_replay = sorted(
        (_manual_replay_item(r) for r in missed_rows),
        key=lambda item: -_f(item.get("max_return_pct")),
    )[:8]
    if judged_n < 10:
        headline = "Manual labels are live; the system needs more judged outcomes before changing scoring."
        state = "NEEDS_MORE_LABELS"
    elif missed_n >= max(2, judged_n * 0.25):
        headline = "Manual review is missing too many runners; watch/skip blockers need replay before tightening."
        state = "MISSED_RUNNER_REPLAY"
    elif good_rate >= 65:
        headline = "Manual labels are directionally useful; start using them as a scoring overlay."
        state = "SCORING_OVERLAY_READY"
    else:
        headline = "Manual labels are mixed; keep collecting samples before trusting them as policy."
        state = "MIXED_SAMPLE"
    actions: list[str] = []
    if buy_row and int(buy_row.get("judged_n") or 0) > 0:
        actions.append(
            f"BUY labels: {buy_row.get('good_rate_pct', 0)}% good over {buy_row.get('judged_n', 0)} judged; avg max {buy_row.get('avg_max_return_pct', 0)}%."
        )
    if watch_row and int(watch_row.get("missed_n") or 0) > 0:
        actions.append(
            f"WATCH missed {watch_row.get('missed_n', 0)} runner(s); promote faster when the last blocker clears."
        )
    for row in skip_like[:2]:
        if int(row.get("missed_n") or 0) > 0:
            actions.append(
                f"{row.get('decision')} missed {row.get('missed_n', 0)} move(s); replay blocker logic before treating it as a hard avoid."
            )
    if not actions:
        actions.append("Keep labeling the hot research cards; calibration starts becoming useful after roughly 10 judged labels.")
    return {
        "state": state,
        "headline": headline,
        "actions": actions[:4],
        "buy_signal": buy_row,
        "watch_signal": watch_row,
        "missed_runner_replay": missed_replay,
    }


def record_memecoin_manual_review_decision(payload: dict[str, Any]) -> dict[str, Any]:
    decision = str(payload.get("decision") or "").strip().upper()
    if decision not in MANUAL_REVIEW_DECISIONS:
        raise ValueError(f"decision must be one of {sorted(MANUAL_REVIEW_DECISIONS)}")
    mint = str(payload.get("mint") or "").strip()
    if not mint:
        raise ValueError("mint is required")
    symbol = str(payload.get("symbol") or "").strip().upper()
    dossier = dict(payload.get("dossier") or payload)
    ctx = _manual_review_context_from_dossier(dossier)
    entry_marketcap = _f(payload.get("marketcap") or dossier.get("marketcap"))
    latest = get_latest_memecoin_token_stats_for_mints([mint], max_age_minutes=RESEARCH_TOKEN_STATS_MAX_AGE_MINUTES).get(mint) or {}
    current_marketcap = _f(latest.get("marketcap") or entry_marketcap)
    created_at = _iso()
    with get_conn() as conn:
        _ensure_schema(conn)
        conn.execute(
            """
            INSERT INTO memecoin_manual_review_decisions
                (created_at, mint, symbol, decision, operator_note, source,
                 research_score, entry_zone, position_stance, exit_state,
                 entry_marketcap, current_marketcap, last_eval_at,
                 max_return_pct, outcome_label, dossier_json, notes_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                created_at,
                mint,
                symbol or dossier.get("symbol"),
                decision,
                payload.get("operator_note"),
                str(payload.get("source") or "home_memecoin_research"),
                _f(payload.get("research_score") or dossier.get("research_score")),
                ctx["entry_zone"],
                ctx["position_stance"],
                ctx["exit_state"],
                entry_marketcap,
                current_marketcap,
                created_at,
                0.0,
                "EXCLUDED_BAD_CA" if decision == "BAD_CA" else "TRACKING",
                json.dumps(dossier, separators=(",", ":")),
                json.dumps({"recorded_from": "manual_review_bridge"}, separators=(",", ":")),
            ),
        )
        row_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0] or 0)
        row = conn.execute("SELECT * FROM memecoin_manual_review_decisions WHERE id=?", (row_id,)).fetchone()
        conn.commit()
    return _manual_review_public(dict(row)) if row else {"id": row_id, "mint": mint, "decision": decision}


def refresh_memecoin_manual_review_outcomes(limit: int = 200) -> dict[str, Any]:
    try:
        with get_conn() as conn:
            _ensure_schema(conn)
            rows = conn.execute(
                """
                SELECT *
                FROM memecoin_manual_review_decisions
                WHERE outcome_label IS NULL
                   OR outcome_label IN ('TRACKING', 'PENDING')
                ORDER BY created_at ASC
                LIMIT ?
                """,
                (max(1, min(int(limit), 1000)),),
            ).fetchall()
            updated = 0
            judged = 0
            for row in rows:
                item = dict(row)
                mint = str(item.get("mint") or "")
                if not mint:
                    continue
                latest = conn.execute(
                    """
                    SELECT *
                    FROM memecoin_token_stats_snapshots
                    WHERE mint=?
                    ORDER BY datetime(ts_utc) DESC, id DESC
                    LIMIT 1
                    """,
                    (mint,),
                ).fetchone()
                if not latest:
                    continue
                latest_item = dict(latest)
                current_marketcap = _f(latest_item.get("marketcap") or item.get("current_marketcap"))
                entry_marketcap = _f(item.get("entry_marketcap"))
                latest_return = ((current_marketcap - entry_marketcap) / entry_marketcap) * 100.0 if entry_marketcap > 0 and current_marketcap > 0 else 0.0
                max_return = max(_f(item.get("max_return_pct")), latest_return)
                created = _parse_dt(item.get("created_at")) or datetime.now(timezone.utc)
                age_hours = (datetime.now(timezone.utc) - created).total_seconds() / 3600.0
                label, is_terminal = _manual_decision_outcome_label(
                    str(item.get("decision") or ""),
                    max_return=max_return,
                    latest_return=latest_return,
                    age_hours=age_hours,
                )
                conn.execute(
                    """
                    UPDATE memecoin_manual_review_decisions
                    SET current_marketcap=?,
                        last_eval_at=?,
                        return_1h_pct=COALESCE(return_1h_pct, CASE WHEN ? >= 1 THEN ? ELSE NULL END),
                        return_4h_pct=COALESCE(return_4h_pct, CASE WHEN ? >= 4 THEN ? ELSE NULL END),
                        return_24h_pct=COALESCE(return_24h_pct, CASE WHEN ? >= 24 THEN ? ELSE NULL END),
                        max_return_pct=?,
                        outcome_label=?
                    WHERE id=?
                    """,
                    (
                        current_marketcap,
                        str(latest_item.get("ts_utc") or _iso()),
                        age_hours,
                        latest_return,
                        age_hours,
                        latest_return,
                        age_hours,
                        latest_return,
                        round(max_return, 2),
                        label,
                        item.get("id"),
                    ),
                )
                updated += 1
                judged += int(is_terminal or _manual_outcome_is_judged(label))
            conn.commit()
            return {"generated_at": _iso(), "scanned": len(rows), "updated": updated, "judged": judged}
    except Exception as exc:
        return {"generated_at": _iso(), "error": str(exc), "scanned": 0, "updated": 0, "judged": 0}


def build_memecoin_manual_review_bridge(dossiers: list[dict[str, Any]], *, limit: int = 50) -> dict[str, Any]:
    refresh = refresh_memecoin_manual_review_outcomes(limit=200)
    try:
        with get_conn() as conn:
            _ensure_schema(conn)
            latest_by_mint = _latest_manual_review_decisions(conn)
            rows = [dict(r) for r in conn.execute(
                """
                SELECT *
                FROM memecoin_manual_review_decisions
                ORDER BY id DESC
                LIMIT ?
                """,
                (max(1, min(int(limit), 200)),),
            ).fetchall()]
    except Exception as exc:
        return {"generated_at": _iso(), "error": str(exc), "summary": {"total": 0}, "recent": [], "calibration": {}, "refresh": refresh}

    needs_review = [
        d for d in dossiers
        if str(d.get("execution_alignment_state") or "").upper() in {"RESEARCH_ENTRY_NOW", "BLOCKED_BUT_HOT", "PAPER_ENTRY_NOW"}
        or str(d.get("action") or "").upper() == "MANUAL_REVIEW"
    ]
    undecided = [d for d in needs_review if str(d.get("mint") or "") not in latest_by_mint]
    judged = [r for r in rows if _manual_outcome_is_judged(str(r.get("outcome_label") or ""))]
    good = [r for r in judged if _manual_outcome_is_good(str(r.get("outcome_label") or ""))]
    missed = [r for r in judged if "MISSED" in str(r.get("outcome_label") or "")]
    by_decision: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = str(row.get("decision") or "UNKNOWN").upper()
        item = by_decision.setdefault(key, {"decision": key, "sample_n": 0, "judged_n": 0, "good_n": 0, "missed_n": 0, "max_return_sum": 0.0})
        item["sample_n"] += 1
        item["max_return_sum"] += _f(row.get("max_return_pct"))
        label = str(row.get("outcome_label") or "")
        if _manual_outcome_is_judged(label):
            item["judged_n"] += 1
            item["good_n"] += int(_manual_outcome_is_good(label))
            item["missed_n"] += int("MISSED" in label)
    decision_rows = []
    for item in by_decision.values():
        sample_n = int(item["sample_n"] or 0)
        judged_n = int(item["judged_n"] or 0)
        good_n = int(item["good_n"] or 0)
        decision_rows.append(
            {
                "decision": item["decision"],
                "sample_n": sample_n,
                "judged_n": judged_n,
                "good_n": good_n,
                "missed_n": int(item["missed_n"] or 0),
                "good_rate_pct": round((good_n / judged_n) * 100.0, 1) if judged_n else 0.0,
                "avg_max_return_pct": round(item["max_return_sum"] / max(1, sample_n), 2),
            }
        )
    decision_rows.sort(key=lambda x: (-int(x["judged_n"]), -_f(x["avg_max_return_pct"]), str(x["decision"])))
    latest_public = {mint: _manual_review_public(row) for mint, row in latest_by_mint.items()}
    good_rate = round((len(good) / len(judged)) * 100.0, 1) if judged else 0.0
    guidance = _manual_calibration_guidance(
        decision_rows=decision_rows,
        rows=rows,
        judged_n=len(judged),
        missed_n=len(missed),
        good_rate=good_rate,
    )
    return {
        "generated_at": _iso(),
        "summary": {
            "total": len(rows),
            "needs_review": len(needs_review),
            "undecided": len(undecided),
            "judged_n": len(judged),
            "good_n": len(good),
            "missed_n": len(missed),
            "good_rate_pct": good_rate,
            "state": "NEEDS_LABELS" if undecided else "LABELING_ACTIVE",
        },
        "prompt": "Label research-hot names with BUY, WATCH, SKIP, TOO LATE, BAD CA, or NEEDS MORE PROOF so the system can learn from your judgment.",
        "latest_by_mint": latest_public,
        "recent": [_manual_review_public(r) for r in rows[:12]],
        "calibration": {
            "state": "LEARNING" if len(judged) < 30 else "CALIBRATING",
            "guidance_state": guidance.get("state"),
            "headline": guidance.get("headline"),
            "actions": guidance.get("actions"),
            "buy_signal": guidance.get("buy_signal"),
            "watch_signal": guidance.get("watch_signal"),
            "by_decision": decision_rows,
        },
        "missed_runner_replay": guidance.get("missed_runner_replay") or [],
        "refresh": refresh,
    }


def _decision_context_from_dossier_json(raw: object) -> dict[str, str]:
    dossier = _json_load(raw, {})
    if not isinstance(dossier, dict):
        dossier = {}
    evidence = dossier.get("evidence") if isinstance(dossier.get("evidence"), dict) else {}
    entry_zone = dossier.get("entry_zone") if isinstance(dossier.get("entry_zone"), dict) else evidence.get("entry_zone") if isinstance(evidence, dict) else {}
    position_plan = dossier.get("position_plan") if isinstance(dossier.get("position_plan"), dict) else evidence.get("position_plan") if isinstance(evidence, dict) else {}
    exit_intel = dossier.get("exit_intelligence") if isinstance(dossier.get("exit_intelligence"), dict) else evidence.get("exit_intelligence") if isinstance(evidence, dict) else {}
    trade_thesis = dossier.get("trade_thesis") if isinstance(dossier.get("trade_thesis"), dict) else evidence.get("trade_thesis") if isinstance(evidence, dict) else {}
    return {
        "entry_zone": str((entry_zone or {}).get("zone") or (entry_zone or {}).get("label") or dossier.get("entry_state") or "UNKNOWN").upper(),
        "position_stance": str((position_plan or {}).get("stance") or "UNKNOWN").upper(),
        "exit_state": str((exit_intel or {}).get("state") or "UNKNOWN").upper(),
        "thesis_decision": str((trade_thesis or {}).get("decision") or "UNKNOWN").upper(),
    }


def _build_entry_outcome_calibration(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def bucket(group_key: str, label: str) -> list[dict[str, Any]]:
        grouped: dict[str, dict[str, Any]] = {}
        for row in rows:
            ctx = row.get("decision_context") or {}
            key = str(ctx.get(group_key) or "UNKNOWN").upper()
            item = grouped.setdefault(
                key,
                {
                    "label": key,
                    "sample_n": 0,
                    "judged_n": 0,
                    "win_n": 0,
                    "loss_n": 0,
                    "giveback_n": 0,
                    "stale_n": 0,
                    "max_return_sum": 0.0,
                    "drawdown_sum": 0.0,
                    "score_sum": 0.0,
                },
            )
            item["sample_n"] += 1
            outcome = str(row.get("outcome_label") or "").upper()
            item["max_return_sum"] += _f(row.get("max_return_pct"))
            item["drawdown_sum"] += _f(row.get("drawdown_from_max_pct"))
            item["score_sum"] += _f(row.get("entry_score"))
            if _entry_outcome_is_judged(outcome):
                item["judged_n"] += 1
                if _entry_outcome_is_win(outcome):
                    item["win_n"] += 1
                else:
                    item["loss_n"] += 1
                if outcome == "GIVEBACK":
                    item["giveback_n"] += 1
                if outcome == "STALE_NO_MOVE":
                    item["stale_n"] += 1
        out = []
        for item in grouped.values():
            sample_n = int(item["sample_n"] or 0)
            judged_n = int(item["judged_n"] or 0)
            win_n = int(item["win_n"] or 0)
            avg_max = item["max_return_sum"] / max(1, sample_n)
            avg_drawdown = item["drawdown_sum"] / max(1, sample_n)
            out.append(
                {
                    "group": label,
                    "label": item["label"],
                    "sample_n": sample_n,
                    "judged_n": judged_n,
                    "win_n": win_n,
                    "loss_n": int(item["loss_n"] or 0),
                    "giveback_n": int(item["giveback_n"] or 0),
                    "stale_n": int(item["stale_n"] or 0),
                    "win_rate_pct": round((win_n / judged_n) * 100.0, 1) if judged_n else 0.0,
                    "avg_max_return_pct": round(avg_max, 2),
                    "avg_drawdown_from_max_pct": round(avg_drawdown, 2),
                    "avg_entry_score": round(item["score_sum"] / max(1, sample_n), 1),
                }
            )
        out.sort(key=lambda x: (-int(x["judged_n"]), -_f(x["avg_max_return_pct"]), str(x["label"])))
        return out

    judged = [r for r in rows if _entry_outcome_is_judged(str(r.get("outcome_label") or ""))]
    wins = [r for r in judged if _entry_outcome_is_win(str(r.get("outcome_label") or ""))]
    state = "LEARNING" if len(judged) < 30 else "CALIBRATING"
    return {
        "generated_at": _iso(),
        "summary": {
            "sample_n": len(rows),
            "judged_n": len(judged),
            "win_n": len(wins),
            "loss_n": max(0, len(judged) - len(wins)),
            "win_rate_pct": round((len(wins) / len(judged)) * 100.0, 1) if judged else 0.0,
            "state": state,
            "needs_more_outcomes": len(judged) < 30,
        },
        "by_entry_zone": bucket("entry_zone", "ENTRY_ZONE")[:10],
        "by_position_stance": bucket("position_stance", "POSITION_STANCE")[:10],
        "by_exit_state": bucket("exit_state", "EXIT_STATE")[:10],
    }


def refresh_entry_signal_paper_outcomes(limit: int = ENTRY_OUTCOME_REFRESH_LIMIT) -> dict[str, Any]:
    try:
        with get_conn() as conn:
            _ensure_schema(conn)
            rows = conn.execute(
                """
                SELECT *
                FROM memecoin_entry_paper_trades
                WHERE status='OPEN'
                   OR outcome_label IS NULL
                   OR outcome_label IN ('TRACKING', 'PENDING')
                ORDER BY entry_ts ASC
                LIMIT ?
                """,
                (max(1, min(int(limit), 1000)),),
            ).fetchall()
            updated = 0
            completed = 0
            relabeled = 0
            for row in rows:
                item = dict(row)
                mint = str(item.get("mint") or "")
                entry_price = _f(item.get("entry_price"))
                if not mint or entry_price <= 0:
                    continue
                latest = conn.execute(
                    """
                    SELECT *
                    FROM memecoin_token_stats_snapshots
                    WHERE mint=?
                    ORDER BY datetime(ts_utc) DESC, id DESC
                    LIMIT 1
                    """,
                    (mint,),
                ).fetchone()
                if not latest:
                    continue
                latest_item = dict(latest)

                def ret_for(minutes: int) -> float | None:
                    snap = _latest_stats_at_or_after(conn, mint, str(item.get("entry_ts") or ""), minutes)
                    if not snap or _f(snap.get("price")) <= 0:
                        return None
                    return ((_f(snap.get("price")) - entry_price) / entry_price) * 100.0

                latest_return = ((_f(latest_item.get("price")) - entry_price) / entry_price) * 100.0 if _f(latest_item.get("price")) > 0 else 0.0
                max_return = max(_f(item.get("max_return_pct")), latest_return)
                drawdown = latest_return - max_return
                entry_dt = _parse_dt(item.get("entry_ts")) or datetime.now(timezone.utc)
                age_hours = (datetime.now(timezone.utc) - entry_dt).total_seconds() / 3600.0
                outcome_label, status, closeout_reason = _entry_paper_outcome_closeout(
                    latest_return=latest_return,
                    max_return=max_return,
                    drawdown=drawdown,
                    age_hours=age_hours,
                )
                _maybe_record_entry_exit_snapshot(
                    conn,
                    item=item,
                    latest_item=latest_item,
                    latest_return=latest_return,
                    max_return=max_return,
                    drawdown=drawdown,
                    age_hours=age_hours,
                    outcome_label=outcome_label,
                )
                prior_label = str(item.get("outcome_label") or "")
                if status == "COMPLETE" and str(item.get("status") or "").upper() != "COMPLETE":
                    completed += 1
                if outcome_label != prior_label:
                    relabeled += 1
                closeout_notes = {
                    "label": outcome_label,
                    "status": status,
                    "reason": closeout_reason,
                    "age_hours": round(age_hours, 2),
                    "latest_return_pct": round(latest_return, 2),
                    "max_return_pct": round(max_return, 2),
                    "drawdown_from_max_pct": round(drawdown, 2),
                    "evaluated_at": _iso(),
                }
                conn.execute(
                    """
                    UPDATE memecoin_entry_paper_trades
                    SET status=?,
                        last_eval_ts=?,
                        current_price=?,
                        current_marketcap=?,
                        return_15m_pct=COALESCE(return_15m_pct, ?),
                        return_1h_pct=COALESCE(return_1h_pct, ?),
                        return_4h_pct=COALESCE(return_4h_pct, ?),
                        return_24h_pct=COALESCE(return_24h_pct, ?),
                        max_return_pct=?,
                        drawdown_from_max_pct=?,
                        outcome_label=?,
                        notes_json=?
                    WHERE id=?
                    """,
                    (
                        status,
                        str(latest_item.get("ts_utc") or _iso()),
                        _f(latest_item.get("price")),
                        _f(latest_item.get("marketcap")),
                        ret_for(15),
                        ret_for(60),
                        ret_for(240),
                        ret_for(1440),
                        round(max_return, 2),
                        round(drawdown, 2),
                        outcome_label,
                        _merge_paper_notes(item.get("notes_json"), closeout_notes),
                        item.get("id"),
                    ),
                )
                conn.execute(
                    "UPDATE memecoin_entry_signals SET status=? WHERE id=?",
                    (status, item.get("signal_id")),
                )
                updated += 1
            conn.commit()
            return {
                "generated_at": _iso(),
                "scanned": len(rows),
                "updated": updated,
                "completed": completed,
                "relabeled": relabeled,
                "closeout_hours": ENTRY_OUTCOME_CLOSEOUT_HOURS,
            }
    except Exception as exc:
        return {"generated_at": _iso(), "error": str(exc), "scanned": 0, "updated": 0, "completed": 0, "relabeled": 0}


def build_entry_signal_payload(limit: int = 12) -> dict[str, Any]:
    refresh = refresh_entry_signal_paper_outcomes(limit=ENTRY_OUTCOME_REFRESH_LIMIT)
    try:
        with get_conn() as conn:
            _ensure_schema(conn)
            signals = conn.execute(
                """
                SELECT s.*, p.status AS paper_status, p.return_15m_pct, p.return_1h_pct,
                       p.return_4h_pct, p.return_24h_pct, p.max_return_pct,
                       p.drawdown_from_max_pct, p.outcome_label,
                       p.notes_json AS paper_notes_json
                FROM memecoin_entry_signals s
                LEFT JOIN memecoin_entry_paper_trades p ON p.signal_id = s.id
                ORDER BY datetime(s.created_at) DESC, s.id DESC
                LIMIT ?
                """,
                (max(1, min(int(limit), 100)),),
            ).fetchall()
            calibration_source = conn.execute(
                """
                SELECT p.*, s.entry_state, s.entry_score, s.dossier_json
                FROM memecoin_entry_paper_trades p
                LEFT JOIN memecoin_entry_signals s ON s.id = p.signal_id
                ORDER BY datetime(p.entry_ts) DESC, p.id DESC
                LIMIT 1000
                """
            ).fetchall()
            closeout_rows = conn.execute(
                """
                SELECT COALESCE(status, 'UNKNOWN') AS status,
                       COALESCE(outcome_label, 'UNLABELED') AS outcome_label,
                       COUNT(*) AS n
                FROM memecoin_entry_paper_trades
                GROUP BY COALESCE(status, 'UNKNOWN'), COALESCE(outcome_label, 'UNLABELED')
                ORDER BY n DESC
                """
            ).fetchall()
            tuning_rows = conn.execute(
                """
                SELECT s.entry_state,
                       COUNT(*) AS sample_n,
                       SUM(CASE WHEN COALESCE(p.outcome_label, '') NOT IN ('', 'TRACKING', 'PENDING') THEN 1 ELSE 0 END) AS judged_n,
                       AVG(COALESCE(p.max_return_pct, 0)) AS avg_max_return_pct,
                       AVG(COALESCE(p.drawdown_from_max_pct, 0)) AS avg_drawdown_from_max_pct,
                       SUM(CASE WHEN p.outcome_label IN ('BIG_WIN', 'WIN', 'SMALL_WIN') THEN 1 ELSE 0 END) AS win_n,
                       SUM(CASE WHEN COALESCE(p.max_return_pct, 0) >= 50 THEN 1 ELSE 0 END) AS big_win_n,
                       SUM(CASE WHEN p.outcome_label='GIVEBACK' THEN 1 ELSE 0 END) AS giveback_n,
                       SUM(CASE WHEN p.outcome_label='STALE_NO_MOVE' THEN 1 ELSE 0 END) AS stale_n,
                       AVG(s.entry_score) AS avg_entry_score
                FROM memecoin_entry_signals s
                LEFT JOIN memecoin_entry_paper_trades p ON p.signal_id = s.id
                GROUP BY s.entry_state
                ORDER BY sample_n DESC
                """
            ).fetchall()
    except Exception:
        return {"generated_at": _iso(), "summary": {"total": 0, "state": "UNAVAILABLE"}, "signals": [], "tuning": [], "refresh": refresh}
    out: list[dict[str, Any]] = []
    for row in signals:
        item = dict(row)
        item["decision_context"] = _decision_context_from_dossier_json(item.get("dossier_json"))
        item["reasons"] = _json_load(item.pop("reasons_json", None), [])
        item["blockers"] = _json_load(item.pop("blockers_json", None), [])
        item["paper_notes"] = _json_load(item.pop("paper_notes_json", None), {})
        item.pop("dossier_json", None)
        out.append(item)
    calibration_rows = []
    for row in calibration_source:
        item = dict(row)
        item["decision_context"] = _decision_context_from_dossier_json(item.get("dossier_json"))
        calibration_rows.append(item)
    closeout_counts = [
        {"status": row["status"], "outcome_label": row["outcome_label"], "count": int(row["n"] or 0)}
        for row in closeout_rows
    ]
    tuning = []
    for row in tuning_rows:
        sample_n = int(row["sample_n"] or 0)
        judged_n = int(row["judged_n"] or 0)
        win_n = int(row["win_n"] or 0)
        tuning.append(
            {
                "entry_state": row["entry_state"],
                "sample_n": sample_n,
                "judged_n": judged_n,
                "win_n": win_n,
                "big_win_n": int(row["big_win_n"] or 0),
                "giveback_n": int(row["giveback_n"] or 0),
                "stale_n": int(row["stale_n"] or 0),
                "win_rate_pct": round((win_n / judged_n) * 100.0, 1) if judged_n else 0.0,
                "avg_max_return_pct": round(_f(row["avg_max_return_pct"]), 1),
                "avg_drawdown_from_max_pct": round(_f(row["avg_drawdown_from_max_pct"]), 1),
                "avg_entry_score": round(_f(row["avg_entry_score"]), 1),
            }
        )
    state_counts = _count_by(out, "entry_state")
    outcome_counts = _count_by(calibration_rows, "outcome_label", default="UNLABELED")
    judged_n = sum(1 for row in calibration_rows if _entry_outcome_is_judged(str(row.get("outcome_label") or "")))
    completed_n = sum(1 for row in calibration_rows if str(row.get("status") or "").upper() == "COMPLETE")
    return {
        "generated_at": _iso(),
        "summary": {
            "total": len(out),
            "state": "ACTIVE" if out else "WAITING",
            "entry_now": int(state_counts.get("ENTRY_NOW", 0)),
            "armed": int(state_counts.get("ARMED", 0)),
            "open_paper": sum(1 for x in out if x.get("paper_status") == "OPEN"),
            "calibration_sample_n": len(calibration_rows),
            "completed_paper": completed_n,
            "judged_paper": judged_n,
            "tracking_paper": int(outcome_counts.get("TRACKING", 0)),
            "stale_no_move": int(outcome_counts.get("STALE_NO_MOVE", 0)),
        },
        "signals": out,
        "tuning": tuning,
        "outcome_calibration": _build_entry_outcome_calibration(calibration_rows),
        "closeout_counts": closeout_counts,
        "refresh": refresh,
    }


def _load_persisted_research_dossiers(limit: int = 12) -> list[dict[str, Any]]:
    try:
        with get_conn() as conn:
            _ensure_schema(conn)
            rows = conn.execute(
                """
                SELECT *
                FROM memecoin_research_dossiers
                ORDER BY generated_at DESC, operator_priority DESC, research_score DESC
                LIMIT ?
                """,
                (max(1, min(max(int(limit), 40), 100)),),
            ).fetchall()
    except Exception:
        return []

    dossiers = [_dossier_from_row(row) for row in rows]
    dossiers.sort(
        key=lambda d: (
            {"ENTRY_NOW": -2, "ARMED": -1, "WAIT": 0, "AVOID_CHASE": 2, "EXIT_PRESSURE": 3}.get(str(d.get("entry_state") or ""), 0),
            BAND_RANK.get(str(d.get("conviction_band")), 9),
            -float(d.get("operator_priority") or 0),
            -float(d.get("research_score") or 0),
            str(d.get("symbol") or ""),
        )
    )
    return dossiers[: max(1, min(int(limit), 100))]


def _build_rotation_board(dossiers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for d in dossiers:
        narrative = str(d.get("narrative") or "unknown")
        item = grouped.setdefault(
            narrative,
            {
                "narrative": narrative,
                "count": 0,
                "score_sum": 0.0,
                "paper_entry_count": 0,
                "manual_review_count": 0,
                "watch_count": 0,
                "outcome_sample_n": 0,
                "avg_max_sum": 0.0,
                "top_symbols": [],
            },
        )
        item["count"] += 1
        item["score_sum"] += _f(d.get("research_score"))
        item["paper_entry_count"] += int(str(d.get("action") or "") == "PAPER_ENTRY")
        item["manual_review_count"] += int(str(d.get("action") or "") == "MANUAL_REVIEW")
        item["watch_count"] += int(str(d.get("action") or "") == "WATCH")
        item["outcome_sample_n"] += int(d.get("outcome_sample_n") or 0)
        item["avg_max_sum"] += _f(d.get("avg_max_return_pct"))
        item["top_symbols"].append(
            {"symbol": d.get("symbol"), "action": d.get("action"), "score": d.get("research_score")}
        )
    board: list[dict[str, Any]] = []
    for item in grouped.values():
        avg_score = item["score_sum"] / max(1, item["count"])
        avg_max = item["avg_max_sum"] / max(1, item["count"])
        heat = _clamp(
            avg_score * 0.72
            + item["paper_entry_count"] * 8.0
            + item["manual_review_count"] * 4.0
            + min(float(item["outcome_sample_n"]) * 2.0, 10.0)
            + min(avg_max, 12.0)
        )
        state = "HOT" if heat >= 76 else "WARM" if heat >= 62 else "COOL"
        top_symbols = sorted(item["top_symbols"], key=lambda x: _f(x.get("score")), reverse=True)[:5]
        board.append(
            {
                "narrative": item["narrative"],
                "state": state,
                "heat_score": round(heat, 1),
                "count": item["count"],
                "paper_entry_count": item["paper_entry_count"],
                "manual_review_count": item["manual_review_count"],
                "watch_count": item["watch_count"],
                "outcome_sample_n": item["outcome_sample_n"],
                "avg_research_score": round(avg_score, 1),
                "avg_max_return_pct": round(avg_max, 2),
                "top_symbols": top_symbols,
            }
        )
    board.sort(key=lambda x: (-_f(x.get("heat_score")), str(x.get("narrative") or "")))
    state_by_narrative = {str(item["narrative"]): str(item["state"]) for item in board}
    for d in dossiers:
        d["rotation_state"] = state_by_narrative.get(str(d.get("narrative") or ""), "COOL")
    return board


def _build_outcome_tuning_summary(dossiers: list[dict[str, Any]], tuning: dict[str, float]) -> dict[str, Any]:
    rows = []
    for narrative in sorted({str(d.get("narrative") or "") for d in dossiers if d.get("narrative")}):
        ds = [d for d in dossiers if str(d.get("narrative") or "") == narrative]
        n = sum(int(d.get("outcome_sample_n") or 0) for d in ds)
        avg_max = sum(_f(d.get("avg_max_return_pct")) for d in ds) / max(1, len(ds))
        rows.append(
            {
                "narrative": narrative,
                "sample_n": n,
                "avg_max_return_pct": round(avg_max, 2),
                "score_bonus": round(float(tuning.get(narrative) or 0.0), 2),
                "state": "LEARNING" if n > 0 else "WAITING_FOR_OUTCOMES",
            }
        )
    rows.sort(key=lambda x: (-abs(_f(x.get("score_bonus"))), -int(x.get("sample_n") or 0), str(x.get("narrative") or "")))
    return {
        "state": "ACTIVE" if any(int(r["sample_n"]) > 0 for r in rows) else "WAITING_FOR_OUTCOMES",
        "rows": rows[:10],
    }


def _build_operator_action(dossiers: list[dict[str, Any]]) -> dict[str, Any]:
    ranked = sorted(
        dossiers,
        key=lambda d: (
            -_f(d.get("operator_priority")),
            -_f(d.get("research_score")),
            str(d.get("symbol") or ""),
        ),
    )
    buyable = [d for d in ranked if d.get("conviction_band") == "BUYABLE"]
    triggered = [d for d in ranked if d.get("conviction_band") == "TRIGGERED"]
    watch = [d for d in ranked if d.get("conviction_band") == "WATCH"]
    too_late = [d for d in ranked if d.get("conviction_band") == "TOO_LATE"]
    entry_now = [d for d in ranked if str(d.get("entry_state") or "").upper() == "ENTRY_NOW"]
    deployable_now = [d for d in ranked if str(d.get("execution_alignment_state") or "").upper() == "DEPLOYABLE_NOW"]
    paper_entry_now = [d for d in ranked if str(d.get("execution_alignment_state") or "").upper() == "PAPER_ENTRY_NOW"]
    research_hot = [d for d in ranked if str(d.get("execution_alignment_state") or "").upper() == "RESEARCH_ENTRY_NOW"]
    armed = [d for d in ranked if str(d.get("entry_state") or "").upper() == "ARMED"]
    exit_pressure = [d for d in ranked if str(d.get("entry_state") or "").upper() == "EXIT_PRESSURE"]
    if deployable_now:
        top = deployable_now[0]
        mode = "DEPLOYABLE_NOW"
        headline = f"{top['symbol']} is deployable now."
        instruction = str(top.get("execution_alignment", {}).get("instruction") or "Execution gates align; apply manual risk rules.")
    elif paper_entry_now:
        top = paper_entry_now[0]
        mode = "PAPER_ENTRY_NOW"
        headline = f"{top['symbol']} is paper-entry ready, but not confirmed deployable."
        instruction = str(top.get("execution_alignment", {}).get("instruction") or "Paper timing is active; wait for true execution alignment before live capital.")
    elif research_hot:
        top = research_hot[0]
        mode = "RESEARCH_ENTRY_NOW"
        headline = f"{top['symbol']} is research-hot, but execution is still blocked."
        instruction = str(top.get("execution_alignment", {}).get("instruction") or "Treat as manual review only until proof/authority blockers clear.")
    elif buyable:
        top = buyable[0]
        mode = "BUYABLE"
        headline = f"{top['symbol']} is the cleanest buyable setup now."
        instruction = "Paper-entry or manual-buy review is justified if operator risk rules agree."
    elif triggered:
        top = triggered[0]
        mode = "TRIGGERED"
        headline = f"{top['symbol']} has an active catalyst, but still needs final review."
        instruction = "Watch the last blocker and avoid forcing entry until quality/extension stay clean."
    elif watch:
        top = (armed[0] if armed else watch[0])
        mode = "ARMED" if armed else "WATCH"
        headline = f"{top['symbol']} is armed, but needs the last blocker to clear." if armed else f"{top['symbol']} is the best watchlist name, but not buyable yet."
        instruction = str(top.get("entry_instruction") or "Wait for catalyst strength or the last blocker to clear.")
    elif too_late:
        top = too_late[0]
        mode = "TOO_LATE"
        headline = f"{top['symbol']} is strong but looks extended; avoid chasing."
        instruction = "Wait for a reset, base, or fresh catalyst after cooldown."
    else:
        top = ranked[0] if ranked else None
        mode = "NO_TRADE"
        headline = "No clean memecoin entry is available right now."
        instruction = "Preserve capital until a good coin filter pass and catalyst align."
    return {
        "mode": mode,
        "headline": headline,
        "instruction": instruction,
        "top": (
            {
                "symbol": top.get("symbol"),
                "mint": top.get("mint"),
                "action": top.get("action"),
                "conviction_band": top.get("conviction_band"),
                "operator_priority": top.get("operator_priority"),
                "research_score": top.get("research_score"),
                "good_coin_score": top.get("good_coin_score"),
                "good_coin_status": top.get("good_coin_status"),
                "catalyst_strength_score": top.get("catalyst_strength_score"),
                "catalyst_strength_label": top.get("catalyst_strength_label"),
                "too_late_score": top.get("too_late_score"),
                "too_late_label": top.get("too_late_label"),
                "entry_state": top.get("entry_state"),
                "entry_score": top.get("entry_score"),
                "entry_blocker": top.get("entry_blocker"),
                "entry_instruction": top.get("entry_instruction"),
                "execution_alignment_state": top.get("execution_alignment_state"),
                "execution_alignment_label": top.get("execution_alignment_label"),
                "execution_alignment_confidence": top.get("execution_alignment_confidence"),
                "execution_deployable": top.get("execution_deployable"),
                "execution_blockers": top.get("execution_blockers"),
                "why_now": top.get("why_now"),
                "invalidation": top.get("invalidation"),
                "entry_zone": top.get("entry_zone"),
                "trade_thesis": top.get("trade_thesis"),
                "position_plan": top.get("position_plan"),
                "exit_intelligence": top.get("exit_intelligence"),
            }
            if top
            else None
        ),
        "counts": {
            "buyable": len(buyable),
            "triggered": len(triggered),
            "watch": len(watch),
            "too_late": len(too_late),
            "ignored": sum(1 for d in dossiers if d.get("conviction_band") == "IGNORE"),
            "entry_now": len(entry_now),
            "deployable_now": len(deployable_now),
            "paper_entry_now": len(paper_entry_now),
            "research_hot": len(research_hot),
            "armed": len(armed),
            "exit_pressure": len(exit_pressure),
        },
    }


def _persist_conviction_snapshots(conn, dossiers: list[dict[str, Any]]) -> int:
    cutoff = _event_age_cutoff(CONVICTION_SNAPSHOT_MIN_INTERVAL_MINUTES)
    inserted = 0
    for item in dossiers:
        mint = str(item.get("mint") or "").strip()
        if not mint:
            continue
        existing = conn.execute(
            """
            SELECT id
            FROM memecoin_conviction_snapshots
            WHERE mint=?
              AND generated_at >= ?
            ORDER BY generated_at DESC
            LIMIT 1
            """,
            (mint, cutoff),
        ).fetchone()
        if existing:
            continue
        conn.execute(
            """
            INSERT INTO memecoin_conviction_snapshots
            (generated_at, mint, symbol, conviction_band, action, research_score,
             operator_priority, good_coin_score, good_coin_status, catalyst_strength_score,
             catalyst_strength_label, too_late_score, too_late_label, marketcap, liquidity,
             volume_24h, catalyst_type, evidence_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item.get("generated_at") or _iso(),
                mint,
                item.get("symbol"),
                item.get("conviction_band"),
                item.get("action"),
                item.get("research_score"),
                item.get("operator_priority"),
                item.get("good_coin_score"),
                item.get("good_coin_status"),
                item.get("catalyst_strength_score"),
                item.get("catalyst_strength_label"),
                item.get("too_late_score"),
                item.get("too_late_label"),
                item.get("marketcap"),
                item.get("liquidity"),
                item.get("volume_24h"),
                item.get("catalyst_type"),
                json.dumps(
                    {
                        "why_now": item.get("why_now"),
                        "last_catalyst_type": item.get("last_catalyst_type"),
                        "good_coin_reasons": item.get("good_coin_reasons") or [],
                        "too_late_reasons": item.get("too_late_reasons") or [],
                    },
                    separators=(",", ":"),
                ),
            ),
        )
        inserted += 1
    return inserted


def _build_conviction_calibration_summary() -> dict[str, Any]:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    try:
        with get_conn() as conn:
            _ensure_schema(conn)
            snapshot_rows = conn.execute(
                """
                SELECT conviction_band,
                       COUNT(*) AS n,
                       AVG(research_score) AS avg_score,
                       AVG(operator_priority) AS avg_priority,
                       AVG(catalyst_strength_score) AS avg_catalyst,
                       AVG(too_late_score) AS avg_too_late
                FROM memecoin_conviction_snapshots
                WHERE generated_at >= ?
                GROUP BY conviction_band
                """,
                (cutoff,),
            ).fetchall()
            has_runner_table = conn.execute(
                """
                SELECT 1
                FROM sqlite_master
                WHERE type='table'
                  AND name='runner_review_decisions'
                LIMIT 1
                """
            ).fetchone()
            outcome_rows = (
                conn.execute(
                    """
                    SELECT COALESCE(
                               NULLIF(r.conviction_band, ''),
                               (
                                   SELECT s.conviction_band
                                   FROM memecoin_conviction_snapshots s
                                   WHERE s.mint = r.mint
                                     AND s.generated_at <= r.created_ts
                                   ORDER BY s.generated_at DESC
                                   LIMIT 1
                               ),
                               'UNATTRIBUTED'
                           ) AS conviction_band,
                           COUNT(*) AS n,
                           AVG(COALESCE(r.max_return_pct, 0)) AS avg_max_return_pct,
                           AVG(COALESCE(r.current_return_pct, r.return_24h_pct, r.return_72h_pct, 0)) AS avg_return_pct,
                           SUM(CASE WHEN COALESCE(r.outcome_label, '') LIKE 'GOOD%' OR r.outcome_label='WATCH_CONFIRMED' THEN 1 ELSE 0 END) AS good_n,
                           SUM(CASE WHEN COALESCE(r.outcome_label, '') LIKE 'BAD%' THEN 1 ELSE 0 END) AS bad_n
                    FROM runner_review_decisions r
                    WHERE r.created_ts >= ?
                      AND r.max_return_pct IS NOT NULL
                    GROUP BY conviction_band
                    """,
                    (cutoff,),
                ).fetchall()
                if has_runner_table
                else []
            )
    except Exception as exc:
        return {
            "state": "UNAVAILABLE",
            "headline": f"Calibration unavailable: {exc}",
            "snapshot_sample_n": 0,
            "outcome_sample_n": 0,
            "snapshots": [],
            "outcomes": [],
        }
    snapshots = [
        {
            "conviction_band": row["conviction_band"] or "UNKNOWN",
            "sample_n": int(row["n"] or 0),
            "avg_score": round(_f(row["avg_score"]), 1),
            "avg_priority": round(_f(row["avg_priority"]), 1),
            "avg_catalyst": round(_f(row["avg_catalyst"]), 1),
            "avg_too_late": round(_f(row["avg_too_late"]), 1),
        }
        for row in snapshot_rows
    ]
    snapshots.sort(key=lambda x: (BAND_RANK.get(str(x["conviction_band"]), 99), -int(x["sample_n"])))
    outcomes = []
    for row in outcome_rows:
        n = int(row["n"] or 0)
        good = int(row["good_n"] or 0)
        bad = int(row["bad_n"] or 0)
        outcomes.append(
            {
                "conviction_band": row["conviction_band"] or "UNATTRIBUTED",
                "sample_n": n,
                "good_n": good,
                "bad_n": bad,
                "good_rate_pct": round((good / max(1, n)) * 100.0, 1),
                "avg_max_return_pct": round(_f(row["avg_max_return_pct"]), 2),
                "avg_return_pct": round(_f(row["avg_return_pct"]), 2),
            }
        )
    outcomes.sort(key=lambda x: (BAND_RANK.get(str(x["conviction_band"]), 99), -int(x["sample_n"])))
    total_outcomes = sum(int(row["sample_n"]) for row in outcomes)
    total_snapshots = sum(int(row["sample_n"]) for row in snapshots)
    state = "LEARNING" if total_outcomes < 10 else "CALIBRATING"
    headline = (
        f"Calibration has {total_outcomes} outcome(s); keep collecting before changing thresholds."
        if state == "LEARNING"
        else "Calibration has enough outcomes to start threshold tuning."
    )
    return {
        "state": state,
        "headline": headline,
        "snapshot_sample_n": total_snapshots,
        "outcome_sample_n": total_outcomes,
        "snapshots": snapshots,
        "outcomes": outcomes,
    }


def _build_established_runner_watchlist(dossiers: list[dict[str, Any]], *, limit: int = 12) -> dict[str, Any]:
    focus_archetypes = {
        "ESTABLISHED_LEADER",
        "ESTABLISHED_RUNNER",
        "REVIVAL_RUNNER",
        "SOLANA_CULTURE_LEADER",
        "BRAND_BETA",
        "SPOT_BETA",
        "SPIKE_AND_GIVEBACK",
    }
    rows: list[dict[str, Any]] = []
    for d in dossiers:
        known = bool(d.get("mint") in KNOWN_COIN_MEMORY or (d.get("evidence") or {}).get("known_memory"))
        archetype = str(d.get("archetype") or "").upper()
        memory = str(d.get("memory_type") or "").lower()
        if not known and archetype not in focus_archetypes and "established" not in memory and "revival" not in memory:
            continue
        execution_state = str(d.get("execution_alignment_state") or "").upper()
        entry_state = str(d.get("entry_state") or "").upper()
        too_late = str(d.get("too_late_label") or "").upper()
        if execution_state == "DEPLOYABLE_NOW":
            lane_state = "DEPLOYABLE"
        elif execution_state == "PAPER_ENTRY_NOW":
            lane_state = "PAPER_READY"
        elif execution_state == "RESEARCH_ENTRY_NOW":
            lane_state = "RESEARCH_HOT"
        elif entry_state == "ARMED":
            lane_state = "LAST_BLOCKER"
        elif too_late in {"ELEVATED", "EXTENDED"}:
            lane_state = "TOO_LATE"
        else:
            lane_state = "BASE_BUILDING"
        blockers = list(d.get("execution_blockers") or d.get("entry_blockers") or [])
        rows.append(
            {
                "symbol": d.get("symbol"),
                "mint": d.get("mint"),
                "lane_state": lane_state,
                "execution_alignment_state": d.get("execution_alignment_state"),
                "entry_state": d.get("entry_state"),
                "last_blocker": blockers[0] if blockers else d.get("entry_blocker"),
                "research_score": d.get("research_score"),
                "operator_priority": d.get("operator_priority"),
                "attention_persistence_label": d.get("attention_persistence_label"),
                "catalyst_quality_label": d.get("catalyst_quality_label"),
                "sustainability_label": d.get("sustainability_label"),
                "comparable_runner": d.get("comparable_runner"),
                "too_late_label": d.get("too_late_label"),
                "marketcap": d.get("marketcap"),
                "liquidity": d.get("liquidity"),
                "volume_24h": d.get("volume_24h"),
                "why_now": d.get("why_now"),
            }
        )
    state_rank = {
        "DEPLOYABLE": 0,
        "PAPER_READY": 1,
        "RESEARCH_HOT": 2,
        "LAST_BLOCKER": 3,
        "BASE_BUILDING": 4,
        "TOO_LATE": 5,
    }
    rows.sort(
        key=lambda x: (
            state_rank.get(str(x.get("lane_state") or ""), 9),
            -_f(x.get("operator_priority")),
            -_f(x.get("research_score")),
            str(x.get("symbol") or ""),
        )
    )
    counts = _count_by(rows, "lane_state")
    return {
        "generated_at": _iso(),
        "summary": {
            "total": len(rows),
            "deployable": counts.get("DEPLOYABLE", 0),
            "paper_ready": counts.get("PAPER_READY", 0),
            "research_hot": counts.get("RESEARCH_HOT", 0),
            "last_blocker": counts.get("LAST_BLOCKER", 0),
            "too_late": counts.get("TOO_LATE", 0),
        },
        "items": rows[: max(1, min(int(limit), 30))],
    }


def _opportunity_trigger_contract(dossier: dict[str, Any]) -> str:
    symbol = str(dossier.get("symbol") or "TOKEN").upper()
    blockers = [str(b) for b in (dossier.get("execution_blockers") or dossier.get("entry_blockers") or []) if str(b or "").strip()]
    last_blocker = str(dossier.get("entry_blocker") or (blockers[0] if blockers else "") or "").strip()
    entry_state = str(dossier.get("entry_state") or "").upper()
    execution_state = str(dossier.get("execution_alignment_state") or "").upper()
    guard = str(dossier.get("entry_guard_status") or "").upper()
    too_late = str(dossier.get("too_late_label") or "").upper()
    buy_pressure = _f(dossier.get("buy_pressure"), 50.0)
    vol_accel = _f(dossier.get("vol_acceleration"))
    liquidity = _f(dossier.get("liquidity"))
    volume = _f(dossier.get("volume_24h"))
    volume_turnover = volume / liquidity if liquidity > 0 else 0.0

    if execution_state == "DEPLOYABLE_NOW":
        return f"{symbol}: entry is allowed only while proof stays ready, data is fresh, and buy pressure holds above 52."
    if execution_state == "PAPER_ENTRY_NOW":
        return f"{symbol}: paper entry now; real entry waits for proof/runner gates while volume acceleration stays above {max(2.0, vol_accel * 0.75):.1f}."
    if too_late in {"ELEVATED", "EXTENDED"}:
        return f"{symbol}: do not chase; only reconsider after a base/reset and fresh volume above liquidity turnover 1.0x."
    if last_blocker:
        clean = last_blocker.replace("_", " ")
        if "vol" in last_blocker or "volume" in last_blocker:
            return f"{symbol}: alert only when {clean} clears, volume acceleration is above 3.0, and buy pressure stays above 52."
        if "data" in last_blocker or guard == "STALE":
            return f"{symbol}: alert only after fresh token data confirms buy pressure above 52 and volume/liquidity turnover above 1.0x."
        if "proof" in last_blocker or "runner" in last_blocker:
            return f"{symbol}: alert only when {clean} clears and the setup is not stretched beyond normal extension risk."
        return f"{symbol}: alert only when {clean} clears and flow stays constructive."
    if entry_state == "ARMED":
        return f"{symbol}: alert when buy pressure clears 52, volume acceleration clears 3.0, and liquidity remains above ${liquidity:,.0f}."
    if volume_turnover < 1.0:
        return f"{symbol}: keep watching until volume/liquidity turnover clears 1.0x and buy pressure is above 52."
    return f"{symbol}: keep on research watch; require fresh momentum confirmation before treating it as buyable."


def _opportunity_state_from_dossier(dossier: dict[str, Any], previous: dict[str, Any] | None = None) -> tuple[str, str | None, str | None]:
    execution_state = str(dossier.get("execution_alignment_state") or "").upper()
    entry_state = str(dossier.get("entry_state") or "").upper()
    quality_tier = str(dossier.get("signal_quality_tier") or "").upper()
    too_late = str(dossier.get("too_late_label") or "").upper()
    research_score = _f(dossier.get("research_score"))
    current_mcap = _f(dossier.get("marketcap"))
    previous = previous or {}
    watch_start_mcap = _f(previous.get("watch_start_marketcap")) or current_mcap
    prior_max_mcap = _f(previous.get("max_observed_marketcap"))
    max_mcap = max(prior_max_mcap, current_mcap)
    max_return = ((max_mcap - watch_start_mcap) / watch_start_mcap) * 100.0 if watch_start_mcap > 0 else 0.0

    missed_reason: str | None = None
    lesson: str | None = None
    if max_return >= 100 and execution_state not in {"DEPLOYABLE_NOW", "PAPER_ENTRY_NOW"}:
        blocker = str(dossier.get("entry_blocker") or (dossier.get("execution_blockers") or ["unknown"])[0] or "unknown")
        missed_reason = f"Moved {max_return:.0f}% from watch baseline while still blocked by {blocker.replace('_', ' ')}."
        lesson = "Review whether this blocker was too strict, stale, or missing a faster paper-entry path."
        return "MISSED_RUNNER", missed_reason, lesson

    if execution_state == "DEPLOYABLE_NOW":
        return "ENTRY_READY", None, None
    if execution_state == "PAPER_ENTRY_NOW":
        return "PAPER_READY", None, None
    if execution_state == "RESEARCH_ENTRY_NOW":
        return "WATCHING", None, None
    if entry_state == "ARMED" or execution_state in {"BLOCKED_BUT_HOT", "ARMED"}:
        return "BLOCKED_IMPROVING", None, None
    if too_late in {"ELEVATED", "EXTENDED"}:
        return "TOO_LATE", None, "Wait for a reset/base before allowing a new entry trigger."
    if current_mcap > 0 and watch_start_mcap > 0 and current_mcap <= watch_start_mcap * 0.65 and research_score < 70:
        return "FADED", None, "The thesis faded before confirmation; keep it out until momentum rebuilds."
    if quality_tier in {"A_PLUS", "A", "B"} or research_score >= 65:
        return "WATCHING", None, None
    return "DISCOVERED", None, None


def _marketcap_return(current: float, base: float) -> float | None:
    if base <= 0 or current <= 0:
        return None
    return ((current - base) / base) * 100.0


def _marketcap_at_or_after(conn, mint: str, base_ts: str, minutes: int) -> float | None:
    base_dt = _parse_dt(base_ts)
    if not base_dt:
        return None
    target = base_dt + timedelta(minutes=max(0, int(minutes)))
    row = conn.execute(
        """
        SELECT marketcap
        FROM memecoin_token_stats_snapshots
        WHERE mint=?
          AND datetime(ts_utc) >= datetime(?)
        ORDER BY datetime(ts_utc) ASC, id ASC
        LIMIT 1
        """,
        (mint, target.isoformat()),
    ).fetchone()
    return _f(row["marketcap"]) if row and _f(row["marketcap"]) > 0 else None


def _max_marketcap_since(conn, mint: str, base_ts: str) -> float:
    row = conn.execute(
        """
        SELECT MAX(marketcap) AS max_marketcap
        FROM memecoin_token_stats_snapshots
        WHERE mint=?
          AND datetime(ts_utc) >= datetime(?)
        """,
        (mint, base_ts),
    ).fetchone()
    return _f(row["max_marketcap"]) if row else 0.0


def _opportunity_outcome_eval(conn, row: dict[str, Any]) -> dict[str, Any]:
    mint = str(row.get("mint") or "").strip()
    first_seen_at = str(row.get("first_seen_at") or row.get("last_seen_at") or _iso())
    base_mcap = _f(row.get("watch_start_marketcap")) or _f(row.get("current_marketcap"))
    current_mcap = _f(row.get("current_marketcap"))
    observed_max = max(_f(row.get("max_observed_marketcap")), current_mcap)
    if mint:
        observed_max = max(observed_max, _max_marketcap_since(conn, mint, first_seen_at))
    max_return = _marketcap_return(observed_max, base_mcap)
    returns = {
        "return_15m_pct": None,
        "return_1h_pct": None,
        "return_4h_pct": None,
        "return_24h_pct": None,
    }
    if mint and base_mcap > 0:
        for key, minutes in (
            ("return_15m_pct", 15),
            ("return_1h_pct", 60),
            ("return_4h_pct", 240),
            ("return_24h_pct", 1440),
        ):
            value = _marketcap_at_or_after(conn, mint, first_seen_at, minutes)
            ret = _marketcap_return(_f(value), base_mcap) if value else None
            returns[key] = round(ret, 2) if ret is not None else None

    state = str(row.get("state") or "").upper()
    blocker = str(row.get("last_blocker") or "none").strip() or "none"
    trigger_cleared = state in {"ENTRY_READY", "PAPER_READY"} or str(row.get("entry_state") or "").upper() == "ENTRY_NOW"
    if trigger_cleared and not row.get("trigger_cleared_at"):
        trigger_cleared_at = str(row.get("last_seen_at") or _iso())
    else:
        trigger_cleared_at = row.get("trigger_cleared_at")

    trigger_clear_return = None
    if trigger_cleared_at and mint and base_mcap > 0:
        clear_mcap = _marketcap_at_or_after(conn, mint, str(trigger_cleared_at), 240)
        trigger_clear_return = _marketcap_return(_f(clear_mcap), base_mcap) if clear_mcap else None

    max_ret = float(max_return or 0.0)
    return_24h = returns["return_24h_pct"]
    outcome = "TRACKING"
    blocker_verdict = "PENDING"
    trigger_verdict = "PENDING"
    learned = "Keep collecting outcome data for this opportunity."
    if state in {"ENTRY_READY", "PAPER_READY"} and max_ret >= 50:
        outcome = "EARLY_WIN"
        blocker_verdict = "NO_BLOCK"
        trigger_verdict = "TRIGGER_HELPFUL"
        learned = "The ready state caught meaningful expansion; preserve this signal path."
    elif state in {"BLOCKED_IMPROVING", "WATCHING", "DISCOVERED"} and max_ret >= 100:
        outcome = "MISSED_WINNER"
        blocker_verdict = "BAD_BLOCK" if blocker != "none" else "UNDERCALLED"
        trigger_verdict = "TRIGGER_TOO_SLOW"
        learned = f"{blocker.replace('_', ' ')} blocked or delayed a runner; review whether it was too strict or stale."
    elif state in {"BLOCKED_IMPROVING", "WATCHING", "DISCOVERED"} and max_ret >= 50:
        outcome = "MISSED_MOVE"
        blocker_verdict = "QUESTIONABLE_BLOCK" if blocker != "none" else "UNDERCALLED"
        trigger_verdict = "TRIGGER_LATE"
        learned = "The watch caught a move but did not graduate fast enough; tighten the trigger-clearing path."
    elif state in {"TOO_LATE", "FADED"} and return_24h is not None and return_24h <= -20:
        outcome = "GOOD_AVOID"
        blocker_verdict = "GOOD_BLOCK"
        trigger_verdict = "AVOID_HELPFUL"
        learned = "Avoid/late logic protected against a weak follow-through setup."
    elif state in {"BLOCKED_IMPROVING", "WATCHING", "DISCOVERED"} and return_24h is not None and return_24h <= -20:
        outcome = "FADED_CORRECTLY"
        blocker_verdict = "GOOD_BLOCK" if blocker != "none" else "NO_ACTION_OK"
        trigger_verdict = "WAIT_HELPFUL"
        learned = "Waiting was useful; the opportunity faded before confirmation."
    elif trigger_cleared and trigger_clear_return is not None:
        if trigger_clear_return >= 18:
            trigger_verdict = "TRIGGER_HELPFUL"
        elif trigger_clear_return <= -18:
            trigger_verdict = "TRIGGER_HARMFUL"
        else:
            trigger_verdict = "TRIGGER_NEUTRAL"

    return {
        "outcome_label": outcome,
        "outcome_evaluated_at": _iso(),
        "return_15m_pct": returns["return_15m_pct"],
        "return_1h_pct": returns["return_1h_pct"],
        "return_4h_pct": returns["return_4h_pct"],
        "return_24h_pct": returns["return_24h_pct"],
        "max_observed_marketcap": round(observed_max, 2),
        "max_observed_return_pct": round(max_ret, 2),
        "max_return_from_watch_pct": round(max_ret, 2),
        "blocker_verdict": blocker_verdict,
        "trigger_verdict": trigger_verdict,
        "trigger_cleared_at": trigger_cleared_at,
        "trigger_clear_return_4h_pct": round(trigger_clear_return, 2) if trigger_clear_return is not None else None,
        "learned_summary": learned,
    }


def _opportunity_event_type(previous_state: str | None, new_state: str, outcome_label: str | None) -> str | None:
    prev = str(previous_state or "").upper()
    new = str(new_state or "").upper()
    outcome = str(outcome_label or "").upper()
    if new in {"ENTRY_READY", "PAPER_READY"} and prev != new:
        return "NEWLY_ACTIONABLE"
    if new == "MISSED_RUNNER" and prev != new:
        return "MISSED_RUNNER"
    if new in {"FADED", "TOO_LATE"} and prev in {"ENTRY_READY", "PAPER_READY"}:
        return "FADED_AFTER_READY"
    if outcome in {"MISSED_WINNER", "MISSED_MOVE"} and prev != "MISSED_RUNNER":
        return "MISSED_RUNNER"
    return None


def _record_opportunity_event(
    conn,
    *,
    dossier: dict[str, Any],
    previous_state: str | None,
    new_state: str,
    last_blocker: str | None,
    trigger_contract: str,
    outcome_eval: dict[str, Any],
) -> None:
    event_type = _opportunity_event_type(previous_state, new_state, str(outcome_eval.get("outcome_label") or ""))
    if not event_type:
        return
    mint = str(dossier.get("mint") or "").strip()
    if not mint:
        return
    # State changes are already edge-triggered, but this keeps manual refreshes idempotent.
    recent = conn.execute(
        """
        SELECT 1
        FROM memecoin_opportunity_events
        WHERE mint=?
          AND event_type=?
          AND previous_state IS ?
          AND new_state=?
          AND event_ts >= ?
        LIMIT 1
        """,
        (mint, event_type, previous_state, new_state, _event_age_cutoff(30)),
    ).fetchone()
    if recent:
        return
    symbol = str(dossier.get("symbol") or "TOKEN")
    clean_new = new_state.replace("_", " ").lower()
    clean_prev = str(previous_state or "new").replace("_", " ").lower()
    if event_type == "NEWLY_ACTIONABLE":
        headline = f"{symbol} just became {clean_new}."
        detail = f"Transitioned from {clean_prev}; trigger cleared enough for the actionable queue."
        base_priority = 90.0 if new_state == "ENTRY_READY" else 78.0
    elif event_type == "MISSED_RUNNER":
        headline = f"{symbol} moved before the system cleared entry."
        detail = str(outcome_eval.get("learned_summary") or "Missed-runner review needed.")
        base_priority = 82.0
    else:
        headline = f"{symbol} faded after being ready."
        detail = "Outcome loop flagged this as a ready-state fade; review the trigger quality."
        base_priority = 68.0
    max_return = _f(outcome_eval.get("max_return_from_watch_pct") or outcome_eval.get("max_observed_return_pct"))
    status = {
        "entry_state": dossier.get("entry_state"),
        "execution_alignment_state": dossier.get("execution_alignment_state"),
        "action": dossier.get("action"),
        "conviction_band": dossier.get("conviction_band"),
        "why_now": dossier.get("why_now"),
        "invalidation": dossier.get("invalidation"),
        "blocker_verdict": outcome_eval.get("blocker_verdict"),
        "trigger_verdict": outcome_eval.get("trigger_verdict"),
    }
    conn.execute(
        """
        INSERT INTO memecoin_opportunity_events
            (event_ts, mint, symbol, event_type, previous_state, new_state,
             trigger_cleared, last_blocker, research_score, signal_quality_tier,
             signal_quality_score, max_return_from_watch_pct, outcome_label,
             alert_priority, headline, detail, trigger_contract, status_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            _iso(),
            mint,
            symbol,
            event_type,
            previous_state,
            new_state,
            1 if new_state in {"ENTRY_READY", "PAPER_READY"} else 0,
            last_blocker,
            _f(dossier.get("research_score")),
            dossier.get("signal_quality_tier"),
            _f(dossier.get("signal_quality_score")),
            max_return,
            outcome_eval.get("outcome_label"),
            round(base_priority + min(max_return, 120.0) * 0.08 + _f(dossier.get("operator_priority")) * 0.05, 1),
            headline,
            detail,
            trigger_contract,
            json.dumps(status, separators=(",", ":")),
        ),
    )


def _upsert_opportunity_ledger(dossiers: list[dict[str, Any]]) -> dict[str, Any]:
    if not dossiers:
        return _build_opportunity_ledger_payload([])
    now = _iso()
    mints = [str(d.get("mint") or "").strip() for d in dossiers if str(d.get("mint") or "").strip()]
    if not mints:
        return _build_opportunity_ledger_payload([])
    with get_conn() as conn:
        _ensure_schema(conn)
        existing_rows = conn.execute(
            """
            SELECT *
            FROM memecoin_opportunity_ledger
            WHERE mint IN ({})
            """.format(",".join("?" for _ in mints)),
            tuple(mints),
        ).fetchall()
        existing = {str(row["mint"]): dict(row) for row in existing_rows}
        for dossier in dossiers:
            mint = str(dossier.get("mint") or "").strip()
            if not mint:
                continue
            prev = existing.get(mint) or {}
            previous_state = str(prev.get("state") or "") or None
            state, missed_reason, lesson = _opportunity_state_from_dossier(dossier, prev)
            first_seen = str(prev.get("first_seen_at") or dossier.get("generated_at") or now)
            state_changed_at = str(prev.get("state_changed_at") or first_seen)
            if previous_state != state:
                state_changed_at = now
            current_mcap = _f(dossier.get("marketcap"))
            watch_start_mcap = _f(prev.get("watch_start_marketcap")) or current_mcap
            max_mcap = max(_f(prev.get("max_observed_marketcap")), current_mcap)
            max_return = ((max_mcap - watch_start_mcap) / watch_start_mcap) * 100.0 if watch_start_mcap > 0 else 0.0
            trigger_contract = _opportunity_trigger_contract(dossier)
            status = {
                "action": dossier.get("action"),
                "conviction_band": dossier.get("conviction_band"),
                "entry_score": dossier.get("entry_score"),
                "operator_priority": dossier.get("operator_priority"),
                "good_coin_status": dossier.get("good_coin_status"),
                "attention_persistence_label": dossier.get("attention_persistence_label"),
                "catalyst_quality_label": dossier.get("catalyst_quality_label"),
                "sustainability_label": dossier.get("sustainability_label"),
                "too_late_label": dossier.get("too_late_label"),
                "why_now": dossier.get("why_now"),
                "invalidation": dossier.get("invalidation"),
            }
            eval_seed = {
                **prev,
                "mint": mint,
                "first_seen_at": first_seen,
                "last_seen_at": now,
                "state": state,
                "entry_state": dossier.get("entry_state"),
                "last_blocker": dossier.get("entry_blocker") or ((dossier.get("execution_blockers") or [None])[0]),
                "watch_start_marketcap": watch_start_mcap,
                "current_marketcap": current_mcap,
                "max_observed_marketcap": max_mcap,
                "max_observed_return_pct": round(max_return, 2),
            }
            outcome_eval = _opportunity_outcome_eval(conn, eval_seed)
            if outcome_eval.get("outcome_label") == "MISSED_WINNER" and state not in {"MISSED_RUNNER", "ENTRY_READY", "PAPER_READY"}:
                state = "MISSED_RUNNER"
                missed_reason = missed_reason or f"Moved {outcome_eval.get('max_observed_return_pct'):.0f}% from watch baseline while still blocked."
                lesson = lesson or outcome_eval.get("learned_summary")
                if previous_state != state:
                    state_changed_at = now
            last_blocker_value = dossier.get("entry_blocker") or ((dossier.get("execution_blockers") or [None])[0])
            _record_opportunity_event(
                conn,
                dossier=dossier,
                previous_state=previous_state,
                new_state=state,
                last_blocker=str(last_blocker_value or "") or None,
                trigger_contract=trigger_contract,
                outcome_eval=outcome_eval,
            )
            conn.execute(
                """
                INSERT INTO memecoin_opportunity_ledger
                    (mint, symbol, first_seen_at, last_seen_at, state, previous_state,
                     state_changed_at, research_score, signal_quality_tier, signal_quality_score,
                     execution_alignment_state, entry_state, last_blocker, trigger_contract,
                     missed_reason, lesson, watch_start_marketcap, current_marketcap,
                     max_observed_marketcap, max_observed_return_pct, outcome_label,
                     outcome_evaluated_at, return_15m_pct, return_1h_pct, return_4h_pct,
                     return_24h_pct, max_return_from_watch_pct, blocker_verdict,
                     trigger_verdict, trigger_cleared_at, trigger_clear_return_4h_pct,
                     learned_summary, status_json, dossier_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(mint) DO UPDATE SET
                    symbol=excluded.symbol,
                    last_seen_at=excluded.last_seen_at,
                    state=excluded.state,
                    previous_state=excluded.previous_state,
                    state_changed_at=excluded.state_changed_at,
                    research_score=excluded.research_score,
                    signal_quality_tier=excluded.signal_quality_tier,
                    signal_quality_score=excluded.signal_quality_score,
                    execution_alignment_state=excluded.execution_alignment_state,
                    entry_state=excluded.entry_state,
                    last_blocker=excluded.last_blocker,
                    trigger_contract=excluded.trigger_contract,
                    missed_reason=excluded.missed_reason,
                    lesson=excluded.lesson,
                    current_marketcap=excluded.current_marketcap,
                    max_observed_marketcap=excluded.max_observed_marketcap,
                    max_observed_return_pct=excluded.max_observed_return_pct,
                    outcome_label=excluded.outcome_label,
                    outcome_evaluated_at=excluded.outcome_evaluated_at,
                    return_15m_pct=COALESCE(memecoin_opportunity_ledger.return_15m_pct, excluded.return_15m_pct),
                    return_1h_pct=COALESCE(memecoin_opportunity_ledger.return_1h_pct, excluded.return_1h_pct),
                    return_4h_pct=COALESCE(memecoin_opportunity_ledger.return_4h_pct, excluded.return_4h_pct),
                    return_24h_pct=COALESCE(memecoin_opportunity_ledger.return_24h_pct, excluded.return_24h_pct),
                    max_return_from_watch_pct=excluded.max_return_from_watch_pct,
                    blocker_verdict=excluded.blocker_verdict,
                    trigger_verdict=excluded.trigger_verdict,
                    trigger_cleared_at=COALESCE(memecoin_opportunity_ledger.trigger_cleared_at, excluded.trigger_cleared_at),
                    trigger_clear_return_4h_pct=COALESCE(memecoin_opportunity_ledger.trigger_clear_return_4h_pct, excluded.trigger_clear_return_4h_pct),
                    learned_summary=excluded.learned_summary,
                    status_json=excluded.status_json,
                    dossier_json=excluded.dossier_json
                """,
                (
                    mint,
                    dossier.get("symbol"),
                    first_seen,
                    now,
                    state,
                    previous_state,
                    state_changed_at,
                    _f(dossier.get("research_score")),
                    dossier.get("signal_quality_tier"),
                    _f(dossier.get("signal_quality_score")),
                    dossier.get("execution_alignment_state"),
                    dossier.get("entry_state"),
                    last_blocker_value,
                    trigger_contract,
                    missed_reason,
                    lesson,
                    watch_start_mcap,
                    current_mcap,
                    outcome_eval.get("max_observed_marketcap") or max_mcap,
                    outcome_eval.get("max_observed_return_pct") or round(max_return, 2),
                    outcome_eval.get("outcome_label"),
                    outcome_eval.get("outcome_evaluated_at"),
                    outcome_eval.get("return_15m_pct"),
                    outcome_eval.get("return_1h_pct"),
                    outcome_eval.get("return_4h_pct"),
                    outcome_eval.get("return_24h_pct"),
                    outcome_eval.get("max_return_from_watch_pct"),
                    outcome_eval.get("blocker_verdict"),
                    outcome_eval.get("trigger_verdict"),
                    outcome_eval.get("trigger_cleared_at"),
                    outcome_eval.get("trigger_clear_return_4h_pct"),
                    outcome_eval.get("learned_summary"),
                    json.dumps(status, separators=(",", ":")),
                    json.dumps(dossier, separators=(",", ":")),
                ),
            )
        conn.commit()
        rows = conn.execute(
            """
            SELECT *
            FROM memecoin_opportunity_ledger
            ORDER BY
                CASE state
                    WHEN 'ENTRY_READY' THEN 0
                    WHEN 'PAPER_READY' THEN 1
                    WHEN 'WATCHING' THEN 2
                    WHEN 'BLOCKED_IMPROVING' THEN 3
                    WHEN 'MISSED_RUNNER' THEN 4
                    WHEN 'TOO_LATE' THEN 5
                    WHEN 'FADED' THEN 6
                    ELSE 7
                END,
                research_score DESC,
                last_seen_at DESC
            LIMIT 80
            """
        ).fetchall()
        ledger_items = [_opportunity_ledger_row(row) for row in rows]
        _refresh_shadow_strategy_lab(conn, ledger_items)
        conn.commit()
        events = _load_opportunity_events(conn, limit=20)
        shadow_lab = _load_shadow_strategy_lab_payload(conn)
    return _build_opportunity_ledger_payload(ledger_items, events=events, shadow_lab=shadow_lab)


def _opportunity_ledger_row(row: Any) -> dict[str, Any]:
    item = dict(row)
    item["status"] = _json_load(item.pop("status_json", None), {})
    item.pop("dossier_json", None)
    return item


def _opportunity_event_row(row: Any) -> dict[str, Any]:
    item = dict(row)
    item["status"] = _json_load(item.pop("status_json", None), {})
    return item


def _load_opportunity_events(conn, *, limit: int = 20) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT *
        FROM memecoin_opportunity_events
        ORDER BY alert_priority DESC, datetime(event_ts) DESC, id DESC
        LIMIT ?
        """,
        (max(1, min(int(limit), 100)),),
    ).fetchall()
    return [_opportunity_event_row(row) for row in rows]


def _load_opportunity_ledger_payload(limit: int = 40) -> dict[str, Any]:
    try:
        with get_conn() as conn:
            _ensure_schema(conn)
            rows = conn.execute(
                """
                SELECT *
                FROM memecoin_opportunity_ledger
                ORDER BY
                    CASE state
                        WHEN 'ENTRY_READY' THEN 0
                        WHEN 'PAPER_READY' THEN 1
                        WHEN 'WATCHING' THEN 2
                        WHEN 'BLOCKED_IMPROVING' THEN 3
                        WHEN 'MISSED_RUNNER' THEN 4
                        WHEN 'TOO_LATE' THEN 5
                        WHEN 'FADED' THEN 6
                        ELSE 7
                    END,
                    research_score DESC,
                    last_seen_at DESC
                LIMIT ?
                """,
                (max(1, min(int(limit), 100)),),
            ).fetchall()
            events = _load_opportunity_events(conn, limit=20)
            shadow_lab = _load_shadow_strategy_lab_payload(conn)
    except Exception:
        return _build_opportunity_ledger_payload([])
    return _build_opportunity_ledger_payload([_opportunity_ledger_row(row) for row in rows], events=events, shadow_lab=shadow_lab)


def _snapshot_inputs(row: dict[str, Any]) -> dict[str, Any]:
    raw = row.get("inputs")
    if isinstance(raw, dict):
        return raw
    return _json_load(raw, {})


def _strategy_entry_candidate(conn, ledger: dict[str, Any], strategy_key: str) -> tuple[str, str | None, float, str]:
    mint = str(ledger.get("mint") or "").strip()
    first_seen = str(ledger.get("first_seen_at") or ledger.get("last_seen_at") or "")
    base_mcap = _f(ledger.get("watch_start_marketcap") or ledger.get("current_marketcap"))
    state = str(ledger.get("state") or "").upper()
    blocker = str(ledger.get("last_blocker") or "").strip()
    if not mint or not first_seen or base_mcap <= 0:
        return "PENDING", None, 0.0, "waiting for usable opportunity baseline"
    if strategy_key == "enter_immediately_when_watching":
        return "OPEN", first_seen, base_mcap, "entered at first opportunity watch baseline"
    if strategy_key == "enter_when_paper_ready":
        if state in {"PAPER_READY", "ENTRY_READY"}:
            ts = str(ledger.get("state_changed_at") or ledger.get("last_seen_at") or first_seen)
            return "OPEN", ts, _marketcap_at_or_after(conn, mint, ts, 0) or _f(ledger.get("current_marketcap")) or base_mcap, "entered when ledger became paper/entry ready"
        return "PENDING", None, 0.0, "waiting for paper-ready state"
    if strategy_key == "enter_only_if_trigger_contract_clears":
        trigger_ts = str(ledger.get("trigger_cleared_at") or "")
        if not trigger_ts and state in {"PAPER_READY", "ENTRY_READY"}:
            trigger_ts = str(ledger.get("state_changed_at") or ledger.get("last_seen_at") or first_seen)
        if trigger_ts:
            return "OPEN", trigger_ts, _marketcap_at_or_after(conn, mint, trigger_ts, 0) or _f(ledger.get("current_marketcap")) or base_mcap, "entered when trigger contract cleared"
        return "PENDING", None, 0.0, "waiting for trigger contract clear"
    if strategy_key == "skip_if_blocker_present":
        if blocker:
            return "SKIPPED", None, 0.0, f"skipped because blocker was present: {blocker}"
        return "OPEN", first_seen, base_mcap, "no blocker present, entered at watch baseline"

    rows = conn.execute(
        """
        SELECT *
        FROM memecoin_token_stats_snapshots
        WHERE mint=?
          AND datetime(ts_utc) >= datetime(?)
        ORDER BY datetime(ts_utc) ASC, id ASC
        LIMIT 500
        """,
        (mint, first_seen),
    ).fetchall()
    running_max = base_mcap
    for raw in rows:
        snap = dict(raw)
        mcap = _f(snap.get("marketcap"))
        if mcap <= 0:
            continue
        running_max = max(running_max, mcap)
        inputs = _snapshot_inputs(snap)
        buy_pressure = _f(inputs.get("buy_pressure_1h"), 50.0)
        vol_accel = _f(inputs.get("vol_acceleration_est"))
        if strategy_key == "enter_when_volume_clears" and vol_accel >= 3.0 and buy_pressure >= 52.0:
            return "OPEN", str(snap.get("ts_utc") or first_seen), mcap, "volume acceleration and buy pressure cleared"
        if strategy_key == "enter_after_pullback" and running_max >= base_mcap * 1.15 and mcap <= running_max * 0.90 and mcap >= base_mcap * 0.75:
            return "OPEN", str(snap.get("ts_utc") or first_seen), mcap, "entered after a pullback from local expansion"
    return "PENDING", None, 0.0, "strategy condition has not cleared yet"


def _evaluate_shadow_strategy(conn, item: dict[str, Any], ledger_by_mint: dict[str, dict[str, Any]]) -> dict[str, Any]:
    mint = str(item.get("mint") or "").strip()
    strategy_key = str(item.get("strategy_key") or "")
    ledger = ledger_by_mint.get(mint) or {}
    entry_status = str(item.get("entry_status") or "PENDING")
    entry_ts = str(item.get("entry_ts") or "")
    entry_mcap = _f(item.get("entry_marketcap"))
    entry_reason = str(item.get("entry_reason") or "")
    if entry_status == "PENDING":
        entry_status, entry_ts, entry_mcap, entry_reason = _strategy_entry_candidate(conn, ledger, strategy_key)

    if entry_status == "SKIPPED":
        max_return = _f(ledger.get("max_return_from_watch_pct") or ledger.get("max_observed_return_pct"))
        outcome = "BAD_SKIP" if max_return >= 50 else "GOOD_SKIP" if max_return <= 0 else "NEUTRAL_SKIP"
        return {
            "entry_status": "SKIPPED",
            "entry_ts": None,
            "entry_marketcap": 0.0,
            "entry_reason": entry_reason,
            "return_15m_pct": None,
            "return_1h_pct": None,
            "return_4h_pct": None,
            "return_24h_pct": None,
            "max_return_pct": round(max_return, 2),
            "outcome_label": outcome,
        }

    if entry_status not in {"OPEN", "COMPLETE"} or not entry_ts or entry_mcap <= 0:
        return {
            "entry_status": "PENDING",
            "entry_ts": None,
            "entry_marketcap": 0.0,
            "entry_reason": entry_reason,
            "return_15m_pct": None,
            "return_1h_pct": None,
            "return_4h_pct": None,
            "return_24h_pct": None,
            "max_return_pct": 0.0,
            "outcome_label": "PENDING",
        }

    returns: dict[str, float | None] = {}
    for key, minutes in (
        ("return_15m_pct", 15),
        ("return_1h_pct", 60),
        ("return_4h_pct", 240),
        ("return_24h_pct", 1440),
    ):
        mcap = _marketcap_at_or_after(conn, mint, entry_ts, minutes)
        ret = _marketcap_return(_f(mcap), entry_mcap) if mcap else None
        returns[key] = round(ret, 2) if ret is not None else None
    max_mcap = _max_marketcap_since(conn, mint, entry_ts)
    max_return = _marketcap_return(max_mcap, entry_mcap) if max_mcap > 0 else 0.0
    ret_24h = returns.get("return_24h_pct")
    age_hours = ((datetime.now(timezone.utc) - (_parse_dt(entry_ts) or datetime.now(timezone.utc))).total_seconds() / 3600.0)
    if max_return and max_return >= 50:
        outcome = "BIG_WIN"
    elif max_return and max_return >= 18:
        outcome = "WIN"
    elif ret_24h is not None and ret_24h <= -18:
        outcome = "LOSS"
    elif age_hours >= 24:
        outcome = "FLAT_OR_FAIL"
    else:
        outcome = "TRACKING"
    return {
        "entry_status": "COMPLETE" if age_hours >= 24 else "OPEN",
        "entry_ts": entry_ts,
        "entry_marketcap": round(entry_mcap, 2),
        "entry_reason": entry_reason,
        "return_15m_pct": returns.get("return_15m_pct"),
        "return_1h_pct": returns.get("return_1h_pct"),
        "return_4h_pct": returns.get("return_4h_pct"),
        "return_24h_pct": returns.get("return_24h_pct"),
        "max_return_pct": round(float(max_return or 0.0), 2),
        "outcome_label": outcome,
    }


def _refresh_shadow_strategy_lab(conn, ledger_rows: list[dict[str, Any]]) -> None:
    now = _iso()
    ledger_by_mint = {str(row.get("mint") or ""): row for row in ledger_rows if str(row.get("mint") or "")}
    for ledger in ledger_rows:
        mint = str(ledger.get("mint") or "").strip()
        if not mint:
            continue
        status = dict(ledger.get("status") or {})
        notes = {
            "source": "opportunity_ledger",
            "archetype": status.get("archetype") or status.get("memory_type"),
            "state": ledger.get("state"),
            "blocker": ledger.get("last_blocker"),
            "trigger_contract": ledger.get("trigger_contract"),
        }
        for strategy_key in SHADOW_STRATEGY_KEYS:
            conn.execute(
                """
                INSERT OR IGNORE INTO memecoin_shadow_strategies
                    (mint, symbol, strategy_key, created_at, opportunity_first_seen_at,
                     archetype, blocker_at_creation, state_at_creation, entry_status, notes_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', ?)
                """,
                (
                    mint,
                    ledger.get("symbol"),
                    strategy_key,
                    now,
                    ledger.get("first_seen_at"),
                    notes.get("archetype"),
                    ledger.get("last_blocker"),
                    ledger.get("state"),
                    json.dumps(notes, separators=(",", ":")),
                ),
            )
    if not ledger_by_mint:
        return
    rows = conn.execute(
        """
        SELECT *
        FROM memecoin_shadow_strategies
        WHERE mint IN ({})
        """.format(",".join("?" for _ in ledger_by_mint)),
        tuple(ledger_by_mint.keys()),
    ).fetchall()
    for row in rows:
        item = dict(row)
        eval_result = _evaluate_shadow_strategy(conn, item, ledger_by_mint)
        conn.execute(
            """
            UPDATE memecoin_shadow_strategies
            SET entry_status=?,
                entry_ts=?,
                entry_marketcap=?,
                entry_reason=?,
                return_15m_pct=COALESCE(return_15m_pct, ?),
                return_1h_pct=COALESCE(return_1h_pct, ?),
                return_4h_pct=COALESCE(return_4h_pct, ?),
                return_24h_pct=COALESCE(return_24h_pct, ?),
                max_return_pct=?,
                outcome_label=?,
                last_eval_at=?
            WHERE id=?
            """,
            (
                eval_result["entry_status"],
                eval_result["entry_ts"],
                eval_result["entry_marketcap"],
                eval_result["entry_reason"],
                eval_result["return_15m_pct"],
                eval_result["return_1h_pct"],
                eval_result["return_4h_pct"],
                eval_result["return_24h_pct"],
                eval_result["max_return_pct"],
                eval_result["outcome_label"],
                now,
                item.get("id"),
            ),
        )


def _load_shadow_strategy_lab_payload(conn) -> dict[str, Any]:
    rows = [dict(row) for row in conn.execute(
        """
        SELECT *
        FROM memecoin_shadow_strategies
        ORDER BY datetime(created_at) DESC, id DESC
        LIMIT 1000
        """
    ).fetchall()]
    by_strategy: dict[str, dict[str, Any]] = {}
    by_blocker: dict[str, dict[str, Any]] = {}
    open_examples = []
    for row in rows:
        strategy = str(row.get("strategy_key") or "unknown")
        bucket = by_strategy.setdefault(strategy, {"strategy_key": strategy, "sample_n": 0, "entered_n": 0, "judged_n": 0, "win_n": 0, "loss_n": 0, "avg_max_sum": 0.0, "avg_4h_sum": 0.0})
        bucket["sample_n"] += 1
        if str(row.get("entry_status") or "") in {"OPEN", "COMPLETE"}:
            bucket["entered_n"] += 1
        outcome = str(row.get("outcome_label") or "")
        if outcome not in {"", "PENDING", "TRACKING", "NEUTRAL_SKIP"}:
            bucket["judged_n"] += 1
        if outcome in {"BIG_WIN", "WIN", "GOOD_SKIP"}:
            bucket["win_n"] += 1
        if outcome in {"LOSS", "BAD_SKIP"}:
            bucket["loss_n"] += 1
        bucket["avg_max_sum"] += _f(row.get("max_return_pct"))
        bucket["avg_4h_sum"] += _f(row.get("return_4h_pct"))

        blocker = str(row.get("blocker_at_creation") or "none")
        b = by_blocker.setdefault(blocker, {"blocker": blocker, "sample_n": 0, "best_strategy": None, "best_score": -999.0})
        b["sample_n"] += 1
        score = _f(row.get("max_return_pct")) + _f(row.get("return_4h_pct")) * 0.25
        if score > _f(b.get("best_score"), -999.0):
            b["best_score"] = round(score, 1)
            b["best_strategy"] = strategy
        if len(open_examples) < 8 and str(row.get("entry_status") or "") in {"OPEN", "SKIPPED"}:
            open_examples.append(row)

    ranking = []
    for item in by_strategy.values():
        sample_n = int(item["sample_n"] or 0)
        entered_n = int(item["entered_n"] or 0)
        judged_n = int(item["judged_n"] or 0)
        win_n = int(item["win_n"] or 0)
        loss_n = int(item["loss_n"] or 0)
        ranking.append(
            {
                "strategy_key": item["strategy_key"],
                "sample_n": sample_n,
                "entered_n": entered_n,
                "judged_n": judged_n,
                "win_n": win_n,
                "loss_n": loss_n,
                "win_rate_pct": round((win_n / max(1, judged_n)) * 100.0, 1) if judged_n else 0.0,
                "avg_max_return_pct": round(_f(item["avg_max_sum"]) / max(1, sample_n), 1),
                "avg_4h_return_pct": round(_f(item["avg_4h_sum"]) / max(1, sample_n), 1),
            }
        )
    ranking.sort(key=lambda x: (-_f(x.get("avg_max_return_pct")), -_f(x.get("win_rate_pct")), str(x.get("strategy_key"))))
    best = ranking[0] if ranking else None
    worst = sorted(ranking, key=lambda x: (_f(x.get("avg_max_return_pct")), _f(x.get("win_rate_pct"))))[0] if ranking else None
    immediate = next((r for r in ranking if r["strategy_key"] == "enter_immediately_when_watching"), None)
    trigger = next((r for r in ranking if r["strategy_key"] == "enter_only_if_trigger_contract_clears"), None)
    earlier_beating_waiting = bool(immediate and trigger and _f(immediate.get("avg_max_return_pct")) > _f(trigger.get("avg_max_return_pct")) + 8.0)
    judged_n = sum(int(r.get("judged_n") or 0) for r in ranking)
    bridge_actions = []
    if judged_n < 30:
        bridge_actions.append("Keep shadow strategies paper-only until at least 30 judged shadow outcomes exist.")
    elif earlier_beating_waiting:
        bridge_actions.append("Earlier paper entries are outperforming trigger-confirmed entries; consider lowering paper escalation delay.")
    elif best and str(best.get("strategy_key")) != "skip_if_blocker_present":
        bridge_actions.append(f"Favor {str(best.get('strategy_key')).replace('_', ' ')} in paper scoring experiments.")
    elif best:
        bridge_actions.append("Skip-if-blocker-present is strongest so far; keep blockers enforced in paper until entry strategies beat skipping.")
    return {
        "generated_at": _iso(),
        "summary": {
            "strategy_count": len(ranking),
            "sample_n": len(rows),
            "entered_n": sum(int(r.get("entered_n") or 0) for r in ranking),
            "judged_n": judged_n,
            "state": "LEARNING" if judged_n < 30 else "READY_TO_BRIDGE",
            "earlier_entry_beating_waiting": earlier_beating_waiting,
        },
        "best_strategy": best,
        "worst_strategy": worst,
        "ranking": ranking[:10],
        "by_blocker": sorted(by_blocker.values(), key=lambda x: (-int(x["sample_n"]), str(x["blocker"])))[:10],
        "open_examples": open_examples[:8],
        "policy_bridge": {
            "state": "PAPER_ONLY" if judged_n < 30 else "PAPER_SCORING_READY",
            "actions": bridge_actions,
            "live_policy": "Do not use shadow strategy results for live entries until paper outcomes prove the edge.",
        },
    }


def _build_opportunity_ledger_payload(
    rows: list[dict[str, Any]],
    *,
    events: list[dict[str, Any]] | None = None,
    shadow_lab: dict[str, Any] | None = None,
) -> dict[str, Any]:
    state_counts = _count_by(rows, "state")
    outcome_counts = _count_by(rows, "outcome_label", default="TRACKING")
    events = list(events or [])
    ready_soon = [
        row for row in rows
        if str(row.get("state") or "") in {"ENTRY_READY", "PAPER_READY", "WATCHING"}
    ][:8]
    blocked_improving = [
        row for row in rows
        if str(row.get("state") or "") == "BLOCKED_IMPROVING"
    ][:8]
    missed = [
        row for row in rows
        if str(row.get("state") or "") in {"MISSED_RUNNER", "TOO_LATE", "FADED"}
    ][:8]
    blocker_stats: dict[str, dict[str, Any]] = {}
    trigger_stats: dict[str, dict[str, Any]] = {}
    for row in rows:
        blocker = str(row.get("last_blocker") or "none")
        b = blocker_stats.setdefault(blocker, {"blocker": blocker, "sample_n": 0, "good_n": 0, "bad_n": 0, "max_return_sum": 0.0})
        b["sample_n"] += 1
        verdict = str(row.get("blocker_verdict") or "")
        if verdict in {"GOOD_BLOCK", "NO_ACTION_OK", "NO_BLOCK"}:
            b["good_n"] += 1
        if verdict in {"BAD_BLOCK", "QUESTIONABLE_BLOCK", "UNDERCALLED"}:
            b["bad_n"] += 1
        b["max_return_sum"] += _f(row.get("max_return_from_watch_pct") or row.get("max_observed_return_pct"))

        trigger = str(row.get("trigger_verdict") or "PENDING")
        t = trigger_stats.setdefault(trigger, {"trigger_verdict": trigger, "sample_n": 0, "helpful_n": 0, "harmful_n": 0})
        t["sample_n"] += 1
        if trigger in {"TRIGGER_HELPFUL", "WAIT_HELPFUL", "AVOID_HELPFUL"}:
            t["helpful_n"] += 1
        if trigger in {"TRIGGER_HARMFUL", "TRIGGER_TOO_SLOW", "TRIGGER_LATE"}:
            t["harmful_n"] += 1

    blockers = []
    for item in blocker_stats.values():
        sample_n = int(item["sample_n"] or 0)
        good_n = int(item["good_n"] or 0)
        bad_n = int(item["bad_n"] or 0)
        blockers.append(
            {
                "blocker": item["blocker"],
                "sample_n": sample_n,
                "good_n": good_n,
                "bad_n": bad_n,
                "accuracy_pct": round((good_n / sample_n) * 100.0, 1) if sample_n else 0.0,
                "avg_max_return_pct": round(_f(item["max_return_sum"]) / max(1, sample_n), 1),
            }
        )
    blockers.sort(key=lambda x: (-int(x["sample_n"]), -float(x["accuracy_pct"])))
    judged_blockers = [x for x in blockers if int(x["good_n"] or 0) + int(x["bad_n"] or 0) > 0]
    best_blocker = max(judged_blockers, key=lambda x: (float(x["accuracy_pct"]), int(x["sample_n"])), default=None)
    worst_blocker = max([x for x in blockers if int(x["bad_n"] or 0) > 0], key=lambda x: (int(x["bad_n"]), float(x["avg_max_return_pct"])), default=None)
    missed_winner = max(rows, key=lambda x: _f(x.get("max_return_from_watch_pct") or x.get("max_observed_return_pct")), default=None)
    if missed_winner and str(missed_winner.get("outcome_label") or "") not in {"MISSED_WINNER", "MISSED_MOVE"}:
        missed_winner = None
    correctly_avoided = next(
        (
            row for row in sorted(rows, key=lambda x: _f(x.get("return_24h_pct")), reverse=False)
            if str(row.get("outcome_label") or "") in {"GOOD_AVOID", "FADED_CORRECTLY"}
        ),
        None,
    )
    trigger_rows = []
    for item in trigger_stats.values():
        sample_n = int(item["sample_n"] or 0)
        trigger_rows.append(
            {
                "trigger_verdict": item["trigger_verdict"],
                "sample_n": sample_n,
                "helpful_n": int(item["helpful_n"] or 0),
                "harmful_n": int(item["harmful_n"] or 0),
                "helpful_rate_pct": round((int(item["helpful_n"] or 0) / sample_n) * 100.0, 1) if sample_n else 0.0,
            }
        )
    trigger_rows.sort(key=lambda x: (-int(x["sample_n"]), str(x["trigger_verdict"])))
    trust_calibration = _build_trust_calibration_payload(
        rows=rows,
        blocker_rows=blockers,
        trigger_rows=trigger_rows,
        events=events,
    )
    headline = (
        f"{len(ready_soon)} ready-soon opportunity(s), {len(blocked_improving)} blocked-improving, {len(missed)} learn/review."
        if rows
        else "Opportunity ledger is waiting for the next research refresh."
    )
    event_counts = _count_by(events, "event_type")
    return {
        "generated_at": _iso(),
        "headline": headline,
        "summary": {
            "total": len(rows),
            "entry_ready": state_counts.get("ENTRY_READY", 0),
            "paper_ready": state_counts.get("PAPER_READY", 0),
            "watching": state_counts.get("WATCHING", 0),
            "blocked_improving": state_counts.get("BLOCKED_IMPROVING", 0),
            "missed_runner": state_counts.get("MISSED_RUNNER", 0),
            "too_late": state_counts.get("TOO_LATE", 0),
            "faded": state_counts.get("FADED", 0),
            "outcomes": outcome_counts,
        },
        "lessons": {
            "best_blocker": best_blocker,
            "worst_blocker": worst_blocker,
            "missed_winner": missed_winner,
            "correctly_avoided": correctly_avoided,
            "blockers": blockers[:8],
            "triggers": trigger_rows[:8],
        },
        "trust_calibration": trust_calibration,
        "shadow_strategy_lab": shadow_lab or {
            "generated_at": _iso(),
            "summary": {"strategy_count": 0, "sample_n": 0, "entered_n": 0, "state": "WAITING", "earlier_entry_beating_waiting": False},
            "best_strategy": None,
            "worst_strategy": None,
            "ranking": [],
            "by_blocker": [],
            "open_examples": [],
            "policy_bridge": {"state": "PAPER_ONLY", "actions": ["Waiting for opportunity ledger rows."], "live_policy": "Paper only."},
        },
        "escalation_queue": {
            "generated_at": _iso(),
            "summary": {
                "total": len(events),
                "newly_actionable": event_counts.get("NEWLY_ACTIONABLE", 0),
                "trigger_cleared": sum(1 for item in events if int(item.get("trigger_cleared") or 0) == 1),
                "missed_runner": event_counts.get("MISSED_RUNNER", 0),
                "faded_after_ready": event_counts.get("FADED_AFTER_READY", 0),
            },
            "items": events[:12],
        },
        "ready_soon": ready_soon,
        "blocked_improving": blocked_improving,
        "missed_or_learn": missed,
    }


def _build_trust_calibration_payload(
    *,
    rows: list[dict[str, Any]],
    blocker_rows: list[dict[str, Any]],
    trigger_rows: list[dict[str, Any]],
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    waiting_helped_labels = {"GOOD_AVOID", "FADED_CORRECTLY"}
    waiting_hurt_labels = {"MISSED_WINNER", "MISSED_MOVE"}
    helped = [r for r in rows if str(r.get("outcome_label") or "") in waiting_helped_labels]
    hurt = [r for r in rows if str(r.get("outcome_label") or "") in waiting_hurt_labels]
    judged = len(helped) + len(hurt)
    helped_pct = round((len(helped) / judged) * 100.0, 1) if judged else 0.0
    hurt_pct = round((len(hurt) / judged) * 100.0, 1) if judged else 0.0
    if judged < 10:
        waiting_state = "LEARNING"
    elif helped_pct >= 62:
        waiting_state = "WAITING_HELPING"
    elif hurt_pct >= 45:
        waiting_state = "WAITING_HURTING"
    else:
        waiting_state = "MIXED"

    blocker_trust = []
    for row in blocker_rows:
        sample_n = int(row.get("sample_n") or 0)
        good_n = int(row.get("good_n") or 0)
        bad_n = int(row.get("bad_n") or 0)
        judged_n = good_n + bad_n
        trust = 50.0 if judged_n == 0 else _clamp(50.0 + ((good_n - bad_n) / max(1, judged_n)) * 42.0)
        if sample_n >= 5 and judged_n == 0:
            trust = 45.0
        if trust >= 70:
            recommendation = "enforce_harder"
        elif trust <= 38:
            recommendation = "downgrade_to_paper_or_watch"
        elif judged_n == 0:
            recommendation = "collect_more_outcomes"
        else:
            recommendation = "keep_current"
        blocker_trust.append(
            {
                **row,
                "judged_n": judged_n,
                "trust_score": round(trust, 1),
                "recommendation": recommendation,
            }
        )
    blocker_trust.sort(key=lambda x: (-int(x.get("judged_n") or 0), float(x.get("trust_score") or 0)))

    trigger_replay = []
    for row in trigger_rows:
        verdict = str(row.get("trigger_verdict") or "PENDING")
        sample_n = int(row.get("sample_n") or 0)
        helpful_n = int(row.get("helpful_n") or 0)
        harmful_n = int(row.get("harmful_n") or 0)
        judged_n = helpful_n + harmful_n
        if verdict in {"TRIGGER_TOO_SLOW", "TRIGGER_LATE"}:
            timing = "too_late"
        elif verdict == "TRIGGER_HARMFUL":
            timing = "bad_trigger"
        elif verdict in {"TRIGGER_HELPFUL", "WAIT_HELPFUL", "AVOID_HELPFUL"}:
            timing = "useful"
        elif verdict == "TRIGGER_NEUTRAL":
            timing = "neutral"
        else:
            timing = "pending"
        trigger_replay.append(
            {
                **row,
                "judged_n": judged_n,
                "timing_label": timing,
            }
        )

    autopsies = []
    for row in rows:
        outcome = str(row.get("outcome_label") or "")
        if outcome not in {"MISSED_WINNER", "MISSED_MOVE"} and str(row.get("state") or "") != "MISSED_RUNNER":
            continue
        blocker = str(row.get("last_blocker") or "unknown")
        max_return = _f(row.get("max_return_from_watch_pct") or row.get("max_observed_return_pct"))
        status = dict(row.get("status") or {})
        if blocker == "data_stale":
            root_cause = "provider_or_refresh_latency"
        elif "proof" in blocker:
            root_cause = "proof_gate_too_slow"
        elif "vol" in blocker:
            root_cause = "momentum_confirmation_late"
        elif "good_coin" in blocker:
            root_cause = "quality_filter_too_strict_or_unproven"
        else:
            root_cause = "unclassified_blocker"
        autopsies.append(
            {
                "symbol": row.get("symbol"),
                "mint": row.get("mint"),
                "outcome_label": outcome or "MISSED_RUNNER",
                "last_blocker": blocker,
                "root_cause": root_cause,
                "max_return_from_watch_pct": round(max_return, 1),
                "why": row.get("missed_reason") or row.get("learned_summary") or f"{blocker.replace('_', ' ')} delayed a move of {max_return:.0f}%.",
                "policy_hint": "Lower this blocker to paper-watch first, not live entry, until more outcomes confirm it.",
                "trigger_contract": row.get("trigger_contract"),
                "why_now": status.get("why_now"),
            }
        )
    autopsies.sort(key=lambda x: -_f(x.get("max_return_from_watch_pct")))

    low_trust = [b for b in blocker_trust if str(b.get("recommendation")) == "downgrade_to_paper_or_watch"]
    high_trust = [b for b in blocker_trust if str(b.get("recommendation")) == "enforce_harder"]
    late_triggers = [t for t in trigger_replay if str(t.get("timing_label")) == "too_late"]
    policy_actions: list[str] = []
    if low_trust:
        policy_actions.append(f"Treat {str(low_trust[0].get('blocker')).replace('_', ' ')} as a paper/watch downgrade before it fully blocks.")
    if high_trust:
        policy_actions.append(f"Keep enforcing {str(high_trust[0].get('blocker')).replace('_', ' ')}; it has helped more than hurt.")
    if late_triggers:
        policy_actions.append("Allow earlier paper escalation when trigger contracts repeatedly clear after the move.")
    if not policy_actions:
        policy_actions.append("Do not change entry policy yet; judged outcome sample is still thin.")

    questions = [
        {
            "label": "Waiting helped or hurt?",
            "answer": f"{helped_pct:.0f}% helped / {hurt_pct:.0f}% hurt across {judged} judged opportunities.",
            "state": waiting_state,
        },
        {
            "label": "Which blocker is lying?",
            "answer": str(low_trust[0].get("blocker")).replace("_", " ") if low_trust else "No low-trust blocker proven yet.",
            "state": "WATCH",
        },
        {
            "label": "Which trigger is working?",
            "answer": next((str(t.get("trigger_verdict")).replace("_", " ") for t in trigger_replay if str(t.get("timing_label")) == "useful"), "No useful trigger proven yet."),
            "state": "LEARNING",
        },
        {
            "label": "What did we miss and why?",
            "answer": autopsies[0]["why"] if autopsies else "No missed-runner autopsy logged yet.",
            "state": "REVIEW" if autopsies else "CLEAR",
        },
    ]

    return {
        "generated_at": _iso(),
        "waiting_vs_missing": {
            "state": waiting_state,
            "judged_n": judged,
            "waiting_helped_n": len(helped),
            "waiting_hurt_n": len(hurt),
            "waiting_helped_pct": helped_pct,
            "waiting_hurt_pct": hurt_pct,
        },
        "blocker_trust": blocker_trust[:12],
        "trigger_replay": trigger_replay[:12],
        "missed_runner_autopsies": autopsies[:8],
        "policy_guidance": {
            "state": "READY_TO_TUNE" if judged >= 25 else "LEARNING",
            "actions": policy_actions[:5],
            "live_policy": "Keep live/manual deployment conservative; apply low-trust blocker changes to paper escalation first.",
        },
        "dashboard_questions": questions,
        "recent_event_count": len(events),
    }


def refresh_memecoin_research_dossiers(limit: int = RESEARCH_DOSSIER_LIMIT, *, record: bool = True) -> dict[str, Any]:
    try:
        from utils.memecoin_manager import get_proof_candidate_snapshot  # type: ignore

        snapshot = get_proof_candidate_snapshot(limit=max(int(limit), 20), include_recent_complete=True)
        candidates = [dict(c or {}) for c in list(snapshot.get("candidates") or [])]
    except Exception:
        snapshot = {}
        candidates = []

    conflict_queue = _load_research_conflict_queue()
    conflict_candidates = _conflict_queue_candidates(conflict_queue)
    if conflict_candidates:
        candidates = conflict_candidates + candidates

    by_mint: dict[str, dict[str, Any]] = {
        str(c.get("mint") or "").strip(): c for c in candidates if str(c.get("mint") or "").strip()
    }
    for mint, memory in KNOWN_COIN_MEMORY.items():
        by_mint.setdefault(mint, {"mint": mint, "symbol": memory.get("symbol")})

    mints = list(by_mint.keys())
    token_stats = get_latest_memecoin_token_stats_for_mints(mints, max_age_minutes=RESEARCH_TOKEN_STATS_MAX_AGE_MINUTES)
    for mint in mints:
        if mint not in token_stats:
            dex = _dex_market(mint)
            if dex:
                token_stats[mint] = {
                    "mint": mint,
                    "symbol": dex.get("symbol"),
                    "marketcap": dex.get("marketcap"),
                    "liquidity": dex.get("liquidity"),
                    "volume_24h_usd": dex.get("volume_24h"),
                    "price": dex.get("price"),
                    "inputs": {},
                }
    outcomes = _outcome_context(mints)
    narrative_tuning = _historical_narrative_tuning()
    catalyst_refresh = refresh_memecoin_catalyst_events(
        limit=limit,
        record=record,
        include_external=False,
        candidates=list(by_mint.values()),
        token_stats=token_stats,
    )
    latest_catalysts = _latest_catalysts_for_mints(mints)
    try:
        from utils.wallet_cluster_intelligence import build_wallet_cluster_intelligence_map  # type: ignore

        wallet_clusters = build_wallet_cluster_intelligence_map(list(by_mint.values()), record=record)
    except Exception:
        wallet_clusters = {}

    dossiers: list[dict[str, Any]] = []
    for mint, candidate in by_mint.items():
        symbol = str(candidate.get("symbol") or "").upper()
        known = _memory_for(mint, symbol)
        stats = dict(token_stats.get(mint) or {})
        if not candidate.get("proof_components") and known:
            candidate = _known_candidate(known, mint, stats)
        dossier = _build_dossier_from_candidate(
            candidate,
            stats,
            known,
            dict(outcomes.get(mint) or {}),
            narrative_tuning=narrative_tuning,
            catalyst_event=latest_catalysts.get(mint),
            wallet_cluster=wallet_clusters.get(mint),
        )
        if dossier:
            dossiers.append(dossier)

    rotation_board = _build_rotation_board(dossiers)
    established_watchlist = _build_established_runner_watchlist(dossiers)
    outcome_tuning = _build_outcome_tuning_summary(dossiers, narrative_tuning)
    dossiers.sort(
        key=lambda d: (
            BAND_RANK.get(str(d.get("conviction_band")), 9),
            -float(d.get("operator_priority") or 0),
            -float(d.get("research_score") or 0),
            str(d["symbol"]),
        )
    )
    conflict_queue_mints = {str(item.get("mint") or "").strip() for item in conflict_queue if str(item.get("mint") or "").strip()}
    if conflict_queue_mints:
        forced = [d for d in dossiers if str(d.get("mint") or "").strip() in conflict_queue_mints]
        rest = [d for d in dossiers if str(d.get("mint") or "").strip() not in conflict_queue_mints]
        dossiers = forced + rest
    dossiers = dossiers[: max(1, min(int(limit), 100))]
    if record:
        _research_db_retry(lambda: _persist_dossiers(dossiers))
        if conflict_queue:
            _mark_research_conflict_queue_processed([str(d.get("mint") or "") for d in dossiers])
        try:
            from utils.runner_review import backfill_runner_review_conviction_attribution  # type: ignore

            backfill_runner_review_conviction_attribution(limit=200)
        except Exception:
            pass
        opportunity_ledger = _research_db_retry(lambda: _upsert_opportunity_ledger(dossiers))
    else:
        now = _iso()
        opportunity_ledger = _build_opportunity_ledger_payload(
            [
                {
                    "mint": d.get("mint"),
                    "symbol": d.get("symbol"),
                    "first_seen_at": d.get("generated_at") or now,
                    "last_seen_at": now,
                    "state": _opportunity_state_from_dossier(d)[0],
                    "research_score": d.get("research_score"),
                    "signal_quality_tier": d.get("signal_quality_tier"),
                    "signal_quality_score": d.get("signal_quality_score"),
                    "execution_alignment_state": d.get("execution_alignment_state"),
                    "entry_state": d.get("entry_state"),
                    "last_blocker": d.get("entry_blocker"),
                    "trigger_contract": _opportunity_trigger_contract(d),
                    "missed_reason": None,
                    "lesson": None,
                    "current_marketcap": d.get("marketcap"),
                    "max_observed_marketcap": d.get("marketcap"),
                    "max_observed_return_pct": 0,
                    "status": {"why_now": d.get("why_now"), "invalidation": d.get("invalidation")},
                }
                for d in dossiers
            ]
        )

    counts = _count_by(dossiers, "action")
    bands = _count_by(dossiers, "conviction_band")
    narrative_counts = _narrative_intelligence_counts(dossiers)
    operator_action = _build_operator_action(dossiers)
    calibration = _build_conviction_calibration_summary()
    entry_signals = build_entry_signal_payload(limit=12)
    manual_review_bridge = build_memecoin_manual_review_bridge(dossiers)
    latest_manual = dict(manual_review_bridge.get("latest_by_mint") or {})
    for item in dossiers:
        item["manual_review_decision"] = latest_manual.get(str(item.get("mint") or ""))
    return {
        "generated_at": _iso(),
        "summary": {
            "total": len(dossiers),
            "actions": counts,
            "conviction_bands": bands,
            "known_memory_count": sum(1 for d in dossiers if d["mint"] in KNOWN_COIN_MEMORY),
            "symbol_memory_count": sum(1 for d in dossiers if bool((d.get("evidence") or {}).get("known_memory", {}).get("symbol_memory_only"))),
            "outcome_linked_count": sum(1 for d in dossiers if int(d.get("outcome_sample_n") or 0) > 0),
            "catalyst_linked_count": sum(1 for d in dossiers if d.get("last_catalyst_type")),
            "good_coin_pass_count": sum(1 for d in dossiers if d.get("good_coin_status") == "PASS"),
            "strong_catalyst_count": sum(1 for d in dossiers if d.get("catalyst_strength_label") == "STRONG"),
            "too_late_count": sum(1 for d in dossiers if d.get("conviction_band") == "TOO_LATE"),
            "insider_like_count": sum(
                1 for d in dossiers
                if str(((d.get("wallet_cluster_intelligence") or {}).get("cluster_label") or "")).upper() == "INSIDER_LIKE"
            ),
            "distribution_risk_count": sum(
                1 for d in dossiers
                if str(((d.get("wallet_cluster_intelligence") or {}).get("cluster_label") or "")).upper() == "DISTRIBUTION_RISK"
            ),
            "repeat_operator_count": sum(
                1 for d in dossiers
                if int(((d.get("wallet_cluster_intelligence") or {}).get("repeat_operator_count") or 0)) > 0
            ),
            "entry_now_count": sum(1 for d in dossiers if d.get("entry_state") == "ENTRY_NOW"),
            "deployable_now_count": sum(1 for d in dossiers if d.get("execution_alignment_state") == "DEPLOYABLE_NOW"),
            "paper_entry_now_count": sum(1 for d in dossiers if d.get("execution_alignment_state") == "PAPER_ENTRY_NOW"),
            "research_hot_count": sum(1 for d in dossiers if d.get("execution_alignment_state") == "RESEARCH_ENTRY_NOW"),
            "armed_count": sum(1 for d in dossiers if d.get("entry_state") == "ARMED"),
            "exit_pressure_count": sum(1 for d in dossiers if d.get("entry_state") == "EXIT_PRESSURE"),
            **narrative_counts,
        },
        "headline": (
            operator_action.get("headline")
            if dossiers
            else "No research dossiers available yet."
        ),
        "operator_action": operator_action,
        "dossiers": dossiers,
        "rotation_board": rotation_board,
        "established_runner_watchlist": established_watchlist,
        "opportunity_ledger": opportunity_ledger,
        "outcome_tuning": outcome_tuning,
        "calibration": calibration,
        "entry_signals": entry_signals,
        "manual_review_bridge": manual_review_bridge,
        "catalysts": build_memecoin_catalyst_payload(limit=20),
        "catalyst_refresh": catalyst_refresh.get("summary"),
        "proof_input_source": snapshot.get("proof_input_source"),
        "conflict_self_heal": {
            "queued": len(conflict_queue),
            "included": len(conflict_candidates),
            "processed": sum(1 for d in dossiers if str(d.get("mint") or "") in {str(x.get("mint") or "") for x in conflict_queue}),
        },
    }


def build_memecoin_research_payload(limit: int = 12) -> dict[str, Any]:
    """Fast dashboard payload backed by the engine-owned persisted snapshot.

    Heavy refresh work belongs to the headless engine loop. Keeping the Home
    endpoint read-only prevents a provider timeout from turning into a dashboard
    crash while still exposing freshness metadata for stale-data detection.
    """
    dossiers = _load_persisted_research_dossiers(limit=limit)
    generated_at = max((str(d.get("generated_at") or "") for d in dossiers), default="") or _iso()
    age = _age_seconds(generated_at)
    rotation_board = _build_rotation_board(dossiers)
    established_watchlist = _build_established_runner_watchlist(dossiers)
    opportunity_ledger = _load_opportunity_ledger_payload(limit=40)
    outcome_tuning = _build_outcome_tuning_summary(dossiers, _historical_narrative_tuning())
    operator_action = _build_operator_action(dossiers)
    calibration = _build_conviction_calibration_summary()
    entry_signals = build_entry_signal_payload(limit=12)
    manual_review_bridge = build_memecoin_manual_review_bridge(dossiers)
    latest_manual = dict(manual_review_bridge.get("latest_by_mint") or {})
    for item in dossiers:
        item["manual_review_decision"] = latest_manual.get(str(item.get("mint") or ""))
    counts = _count_by(dossiers, "action")
    bands = _count_by(dossiers, "conviction_band")
    narrative_counts = _narrative_intelligence_counts(dossiers)
    return {
        "generated_at": generated_at,
        "summary": {
            "total": len(dossiers),
            "actions": counts,
            "conviction_bands": bands,
            "known_memory_count": sum(1 for d in dossiers if d.get("mint") in KNOWN_COIN_MEMORY),
            "symbol_memory_count": sum(1 for d in dossiers if bool((d.get("evidence") or {}).get("known_memory", {}).get("symbol_memory_only"))),
            "outcome_linked_count": sum(1 for d in dossiers if int(d.get("outcome_sample_n") or 0) > 0),
            "catalyst_linked_count": sum(1 for d in dossiers if d.get("last_catalyst_type")),
            "good_coin_pass_count": sum(1 for d in dossiers if d.get("good_coin_status") == "PASS"),
            "strong_catalyst_count": sum(1 for d in dossiers if d.get("catalyst_strength_label") == "STRONG"),
            "too_late_count": sum(1 for d in dossiers if d.get("conviction_band") == "TOO_LATE"),
            "insider_like_count": sum(
                1 for d in dossiers
                if str(((d.get("wallet_cluster_intelligence") or {}).get("cluster_label") or "")).upper() == "INSIDER_LIKE"
            ),
            "distribution_risk_count": sum(
                1 for d in dossiers
                if str(((d.get("wallet_cluster_intelligence") or {}).get("cluster_label") or "")).upper() == "DISTRIBUTION_RISK"
            ),
            "repeat_operator_count": sum(
                1 for d in dossiers
                if int(((d.get("wallet_cluster_intelligence") or {}).get("repeat_operator_count") or 0)) > 0
            ),
            "entry_now_count": sum(1 for d in dossiers if d.get("entry_state") == "ENTRY_NOW"),
            "deployable_now_count": sum(1 for d in dossiers if d.get("execution_alignment_state") == "DEPLOYABLE_NOW"),
            "paper_entry_now_count": sum(1 for d in dossiers if d.get("execution_alignment_state") == "PAPER_ENTRY_NOW"),
            "research_hot_count": sum(1 for d in dossiers if d.get("execution_alignment_state") == "RESEARCH_ENTRY_NOW"),
            "armed_count": sum(1 for d in dossiers if d.get("entry_state") == "ARMED"),
            "exit_pressure_count": sum(1 for d in dossiers if d.get("entry_state") == "EXIT_PRESSURE"),
            **narrative_counts,
            "cache_age_seconds": round(age, 1) if age is not None else None,
            "stale": bool(age is not None and age > RESEARCH_PAYLOAD_STALE_MINUTES * 60),
        },
        "headline": (
            operator_action.get("headline")
            if dossiers
            else "No research dossiers available yet."
        ),
        "operator_action": operator_action,
        "dossiers": dossiers,
        "rotation_board": rotation_board,
        "established_runner_watchlist": established_watchlist,
        "opportunity_ledger": opportunity_ledger,
        "outcome_tuning": outcome_tuning,
        "calibration": calibration,
        "entry_signals": entry_signals,
        "manual_review_bridge": manual_review_bridge,
        "catalysts": build_memecoin_catalyst_payload(limit=20),
        "catalyst_refresh": None,
        "proof_input_source": "persisted_research_dossiers",
    }
