import os
from dotenv import load_dotenv

# Use .env as source of truth even if process env already has stale values.
load_dotenv(override=True)


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _csv_values(raw: str) -> list[str]:
    return [x.strip() for x in str(raw or "").split(",") if x.strip()]


def _parse_watchlist_entries(raw: str) -> list[dict]:
    entries = []
    for item in _csv_values(raw):
        if ":" in item:
            symbol, address = item.split(":", 1)
            symbol = symbol.strip().upper()
            address = address.strip()
        else:
            symbol = ""
            address = item.strip()
        if not address:
            continue
        entries.append({"symbol": symbol or "WATCH", "address": address})
    return entries


def _normalize_risk_style(raw: str) -> str:
    value = str(raw or "").strip().lower()
    aliases = {
        "capital": "capital",
        "capital-preservation": "capital",
        "preservation": "capital",
        "safe": "capital",
        "balanced": "balanced",
        "balance": "balanced",
        "sniper": "sniper",
        "high-beta": "sniper",
        "aggressive": "sniper",
    }
    return aliases.get(value, "balanced")


ENGINE_PROFILE = os.getenv("ENGINE_PROFILE", "strategic").strip().lower()
if ENGINE_PROFILE not in {"strategic", "tactical"}:
    ENGINE_PROFILE = "strategic"
RISK_STYLE = _normalize_risk_style(os.getenv("RISK_STYLE", "balanced"))


def _profile_default(strategic_value, tactical_value):
    return tactical_value if ENGINE_PROFILE == "tactical" else strategic_value


# =========================================================
# TELEGRAM
# =========================================================

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
TELEGRAM_ACTION_BUTTONS_ENABLED = _env_bool("TELEGRAM_ACTION_BUTTONS_ENABLED", default=True)
# Reduce noisy push traffic from non-core lanes; manual commands still work.
TELEGRAM_QUIET_MODE = _env_bool("TELEGRAM_QUIET_MODE", default=True)
# If enabled, only high-conviction/high-score live BUY alerts are pushed immediately.
TELEGRAM_PUSH_STRONG_ONLY = _env_bool("TELEGRAM_PUSH_STRONG_ONLY", default=True)
TELEGRAM_PUSH_MIN_SCORE_DELTA = int(os.getenv("TELEGRAM_PUSH_MIN_SCORE_DELTA", "8"))
TELEGRAM_PUSH_MIN_CONFIDENCE = os.getenv("TELEGRAM_PUSH_MIN_CONFIDENCE", "A").strip().upper()

# =========================================================
# API KEYS
# =========================================================

DEXSCREENER_API_URL = os.getenv("DEXSCREENER_API_URL", "").strip()  # Add this line for the API URL
DEXSCREENER_CHAIN_ID = os.getenv("DEXSCREENER_CHAIN_ID", "solana").strip().lower()
DEXSCREENER_SEARCH_QUERIES = [
    q.strip() for q in os.getenv("DEXSCREENER_SEARCH_QUERIES", "SOL,BONK,WIF,TRUMP").split(",")
    if q.strip()
]
DEXSCREENER_PAIRS_PER_QUERY = int(os.getenv("DEXSCREENER_PAIRS_PER_QUERY", "8"))
BIRDEYE_API_KEY = os.getenv("BIRDEYE_API_KEY", "").strip()
BIRDEYE_API_URL = os.getenv("BIRDEYE_API_URL", "https://public-api.birdeye.so").strip()
BIRDEYE_CHAIN = os.getenv("BIRDEYE_CHAIN", "solana").strip()
BIRDEYE_MAX_RETRIES = int(os.getenv("BIRDEYE_MAX_RETRIES", "3"))
BIRDEYE_MIN_INTERVAL_SECONDS = float(os.getenv("BIRDEYE_MIN_INTERVAL_SECONDS", "0.25"))
BIRDEYE_BACKOFF_SECONDS = float(os.getenv("BIRDEYE_BACKOFF_SECONDS", "1.0"))
BIRDEYE_TOKENLIST_ENABLED = _env_bool("BIRDEYE_TOKENLIST_ENABLED", default=True)
BIRDEYE_TOKENLIST_COOLDOWN_SECONDS = int(os.getenv("BIRDEYE_TOKENLIST_COOLDOWN_SECONDS", "60"))

# Refresh key market fields immediately before alert send to reduce source drift.
ALERT_DATA_REFRESH_ENABLED = _env_bool("ALERT_DATA_REFRESH_ENABLED", default=True)
# If true, skip sending an alert when live refresh fails.
ALERT_REQUIRE_REFRESH_SUCCESS = _env_bool("ALERT_REQUIRE_REFRESH_SUCCESS", default=False)
# Log when live refresh changes key fields beyond this percent.
ALERT_DATA_DRIFT_WARN_PCT = float(os.getenv("ALERT_DATA_DRIFT_WARN_PCT", "35"))
# If true, hide holders unless explicitly live-verified by refresh source.
ALERT_HIDE_UNVERIFIED_HOLDERS = _env_bool("ALERT_HIDE_UNVERIFIED_HOLDERS", default=True)

# =========================================================
# SOL REGIME LAYER (cycle-aware gating)
# =========================================================

ENABLE_SOL_REGIME_LAYER = _env_bool("ENABLE_SOL_REGIME_LAYER", default=True)
SOL_REGIME_QUERY = os.getenv("SOL_REGIME_QUERY", "SOL").strip()
SOL_PROXY_MINT = os.getenv("SOL_PROXY_MINT", "So11111111111111111111111111111111111111112").strip()
SOL_RISK_ON_CHANGE_24H = float(os.getenv("SOL_RISK_ON_CHANGE_24H", "2.0"))
SOL_RISK_OFF_CHANGE_24H = float(os.getenv("SOL_RISK_OFF_CHANGE_24H", "-4.0"))
SOL_EXTREME_OFF_CHANGE_24H = float(os.getenv("SOL_EXTREME_OFF_CHANGE_24H", "-10.0"))
SOL_EXTREME_OFF_CHANGE_1H = float(os.getenv("SOL_EXTREME_OFF_CHANGE_1H", "-2.5"))
SOL_BREADTH_MIN_FOR_RISK_ON = float(os.getenv("SOL_BREADTH_MIN_FOR_RISK_ON", "0.50"))

BEARISH_ALERT_THRESHOLD = int(os.getenv("BEARISH_ALERT_THRESHOLD", "85"))
BEARISH_REGIME_MIN_SCORE = int(os.getenv("BEARISH_REGIME_MIN_SCORE", "45"))
BEARISH_MIN_CONFIDENCE_TO_ALERT = os.getenv("BEARISH_MIN_CONFIDENCE_TO_ALERT", "A").strip().upper()
BEARISH_MAX_ALERTS_PER_CYCLE = int(os.getenv("BEARISH_MAX_ALERTS_PER_CYCLE", "1"))
BEARISH_MAX_ALERTS_PER_DAY = int(os.getenv("BEARISH_MAX_ALERTS_PER_DAY", "1"))
BEARISH_ALERT_COOLDOWN_HOURS = int(os.getenv("BEARISH_ALERT_COOLDOWN_HOURS", "24"))

ENABLE_EXTREME_RISK_HARD_BLOCK = _env_bool("ENABLE_EXTREME_RISK_HARD_BLOCK", default=True)

# =========================================================
# NEW RUNNER WATCH (watchlist-only alert stream)
# =========================================================

NEW_RUNNER_WATCH_ENABLED = _env_bool("NEW_RUNNER_WATCH_ENABLED", default=True)
NEW_RUNNER_SCAN_INTERVAL_SECONDS = int(os.getenv("NEW_RUNNER_SCAN_INTERVAL_SECONDS", "1800"))
NEW_RUNNER_SEARCH_QUERIES = [
    q.strip()
    for q in os.getenv("NEW_RUNNER_SEARCH_QUERIES", "SOL,PUMP,AI,MEME,CAT,DOG").split(",")
    if q.strip()
]
NEW_RUNNER_PAIRS_PER_QUERY = int(os.getenv("NEW_RUNNER_PAIRS_PER_QUERY", "24"))
NEW_RUNNER_MAX_RESULTS = int(os.getenv("NEW_RUNNER_MAX_RESULTS", "80"))
NEW_RUNNER_USE_LATEST_PROFILES = _env_bool("NEW_RUNNER_USE_LATEST_PROFILES", default=True)
NEW_RUNNER_PROFILE_LIMIT = int(os.getenv("NEW_RUNNER_PROFILE_LIMIT", "30"))
NEW_RUNNER_PROFILE_SAMPLE = int(os.getenv("NEW_RUNNER_PROFILE_SAMPLE", "15"))
NEW_RUNNER_MAX_AGE_HOURS = float(os.getenv("NEW_RUNNER_MAX_AGE_HOURS", "24"))
NEW_RUNNER_MIN_MARKET_CAP = float(os.getenv("NEW_RUNNER_MIN_MARKET_CAP", "10000000"))
NEW_RUNNER_MIN_VOLUME_24H = float(os.getenv("NEW_RUNNER_MIN_VOLUME_24H", "1500000"))
NEW_RUNNER_MIN_LIQUIDITY = float(os.getenv("NEW_RUNNER_MIN_LIQUIDITY", "500000"))
NEW_RUNNER_MIN_CHANGE_24H = float(os.getenv("NEW_RUNNER_MIN_CHANGE_24H", "12"))
NEW_RUNNER_MIN_TXNS_H1 = int(os.getenv("NEW_RUNNER_MIN_TXNS_H1", "120"))
NEW_RUNNER_REQUIRE_SOCIAL_LINKS = _env_bool("NEW_RUNNER_REQUIRE_SOCIAL_LINKS", default=True)
NEW_RUNNER_MIN_ALERT_SCORE = float(os.getenv("NEW_RUNNER_MIN_ALERT_SCORE", "70"))
NEW_RUNNER_MAX_ALERTS_PER_CYCLE = int(os.getenv("NEW_RUNNER_MAX_ALERTS_PER_CYCLE", "2"))
NEW_RUNNER_COOLDOWN_HOURS = int(os.getenv("NEW_RUNNER_COOLDOWN_HOURS", "24"))
NEW_RUNNER_NARRATIVE_KEYWORDS = [
    q.strip().lower()
    for q in os.getenv(
        "NEW_RUNNER_NARRATIVE_KEYWORDS",
        "ai,agent,bot,meme,defi,game,gaming,rwa,sol,launch,pump,cat,dog,pepe,politifi",
    ).split(",")
    if q.strip()
]

# =========================================================
# MANUAL WATCHLIST LANE (watch-only, separate from trade signals)
# =========================================================

WATCHLIST_LANE_ENABLED = _env_bool("WATCHLIST_LANE_ENABLED", default=True)
WATCHLIST_ENTRIES = _parse_watchlist_entries(os.getenv("WATCHLIST_ENTRIES", ""))
WATCHLIST_SCAN_INTERVAL_SECONDS = int(os.getenv("WATCHLIST_SCAN_INTERVAL_SECONDS", "1800"))
WATCHLIST_MAX_ALERTS_PER_CYCLE = int(os.getenv("WATCHLIST_MAX_ALERTS_PER_CYCLE", "3"))
WATCHLIST_ALERT_COOLDOWN_HOURS = int(os.getenv("WATCHLIST_ALERT_COOLDOWN_HOURS", "6"))
WATCHLIST_ALERT_ON_STATUS_CHANGE = _env_bool("WATCHLIST_ALERT_ON_STATUS_CHANGE", default=True)
WATCHLIST_MIN_LIQUIDITY = float(os.getenv("WATCHLIST_MIN_LIQUIDITY", "150000"))
WATCHLIST_MIN_VOLUME_24H = float(os.getenv("WATCHLIST_MIN_VOLUME_24H", "100000"))
WATCHLIST_ALERT_STATUSES = [
    s.strip().title()
    for s in os.getenv("WATCHLIST_ALERT_STATUSES", "Momentum,Reclaim").split(",")
    if s.strip()
]
WATCHLIST_SUMMARY_ENABLED = _env_bool("WATCHLIST_SUMMARY_ENABLED", default=True)
WATCHLIST_SUMMARY_HOUR_UTC = int(os.getenv("WATCHLIST_SUMMARY_HOUR_UTC", "14"))

# =========================================================
# SCAN SETTINGS
# =========================================================

SCAN_INTERVAL_SECONDS = int(os.getenv("SCAN_INTERVAL_SECONDS", str(_profile_default(3600, 1800))))
MAX_TOKENS_PER_SCAN = int(os.getenv("MAX_TOKENS_PER_SCAN", str(_profile_default(4, 10))))
ALERT_TOP_N = int(os.getenv("ALERT_TOP_N", "3"))
MAX_ALERTS_PER_CYCLE = int(os.getenv("MAX_ALERTS_PER_CYCLE", "3"))
ENABLE_EXECUTION_QUALITY_FILTERS = _env_bool("ENABLE_EXECUTION_QUALITY_FILTERS", default=True)
MIN_VOL_TO_LIQ_RATIO = float(os.getenv("MIN_VOL_TO_LIQ_RATIO", str(_profile_default(0.02, 0.08))))
MAX_VOL_TO_LIQ_RATIO = float(os.getenv("MAX_VOL_TO_LIQ_RATIO", "3.0"))
MAX_ABS_CHANGE_24H = float(os.getenv("MAX_ABS_CHANGE_24H", "80"))
MIN_PRICE_USD = float(os.getenv("MIN_PRICE_USD", "0.00000001"))
MIN_LIQUIDITY_PER_HOLDER = float(os.getenv("MIN_LIQUIDITY_PER_HOLDER", "10"))

# =========================================================
# HARD FILTERS (Professional Capital Rules)
# =========================================================

MIN_LIQUIDITY = float(os.getenv("MIN_LIQUIDITY", str(_profile_default(1_000_000, 500_000))))
MIN_VOLUME_24H = float(os.getenv("MIN_VOLUME_24H", str(_profile_default(300_000, 200_000))))
MIN_TOKEN_AGE_HOURS = 72            # 3 days minimum age
MIN_HOLDERS = int(os.getenv("MIN_HOLDERS", "200"))
MAX_ALLOWED_DRAWDOWN_24H = float(os.getenv("MAX_ALLOWED_DRAWDOWN_24H", "-35"))

# =========================================================
# SCORING
# =========================================================

ALERT_THRESHOLD = int(os.getenv("ALERT_THRESHOLD", "70"))
DRY_RUN = _env_bool("DRY_RUN", default=False)
ENABLE_REGIME_GATE = _env_bool("ENABLE_REGIME_GATE", default=True)
REGIME_MIN_SCORE = int(os.getenv("REGIME_MIN_SCORE", str(_profile_default(50, 20))))
CONFIDENCE_MIN_A = int(os.getenv("CONFIDENCE_MIN_A", "85"))
CONFIDENCE_MIN_B = int(os.getenv("CONFIDENCE_MIN_B", "70"))
MIN_CONFIDENCE_TO_ALERT = os.getenv("MIN_CONFIDENCE_TO_ALERT", "B").strip().upper()

# =========================================================
# REPORTING
# =========================================================

DAILY_SUMMARY_ENABLED = _env_bool("DAILY_SUMMARY_ENABLED", default=True)
DAILY_SUMMARY_HOUR_UTC = int(os.getenv("DAILY_SUMMARY_HOUR_UTC", "0"))
WEEKLY_TUNING_ENABLED = _env_bool("WEEKLY_TUNING_ENABLED", default=True)
WEEKLY_TUNING_DAY_UTC = os.getenv("WEEKLY_TUNING_DAY_UTC", "SUN").strip().upper()
WEEKLY_TUNING_HOUR_UTC = int(os.getenv("WEEKLY_TUNING_HOUR_UTC", "1"))
WEEKLY_TUNING_LOOKBACK_DAYS = int(os.getenv("WEEKLY_TUNING_LOOKBACK_DAYS", "7"))
WEEKLY_TUNING_MIN_OUTCOMES_4H = int(os.getenv("WEEKLY_TUNING_MIN_OUTCOMES_4H", "8"))
OUTCOME_TRACKING_ENABLED = _env_bool("OUTCOME_TRACKING_ENABLED", default=True)
OUTCOME_EVAL_INTERVAL_SECONDS = int(os.getenv("OUTCOME_EVAL_INTERVAL_SECONDS", "300"))
OUTCOME_EVAL_BATCH_SIZE = int(os.getenv("OUTCOME_EVAL_BATCH_SIZE", "50"))
WATCHDOG_ENABLED = _env_bool("WATCHDOG_ENABLED", default=True)
WATCHDOG_INTERVAL_SECONDS = int(os.getenv("WATCHDOG_INTERVAL_SECONDS", "300"))
WATCHDOG_MAX_SCAN_GAP_MINUTES = int(os.getenv("WATCHDOG_MAX_SCAN_GAP_MINUTES", "90"))
WATCHDOG_MAX_ALERT_GAP_HOURS = int(os.getenv("WATCHDOG_MAX_ALERT_GAP_HOURS", "12"))
SIGNAL_DIGEST_ENABLED = _env_bool("SIGNAL_DIGEST_ENABLED", default=True)
SIGNAL_DIGEST_INTERVAL_SECONDS = int(os.getenv("SIGNAL_DIGEST_INTERVAL_SECONDS", "10800"))
SIGNAL_DIGEST_LOOKBACK_HOURS = int(os.getenv("SIGNAL_DIGEST_LOOKBACK_HOURS", "6"))
SIGNAL_DIGEST_MAX_ITEMS = int(os.getenv("SIGNAL_DIGEST_MAX_ITEMS", "5"))

# Automated market board (push "good buys now" snapshot)
GOOD_BUY_BULLETIN_ENABLED = _env_bool("GOOD_BUY_BULLETIN_ENABLED", default=True)
GOOD_BUY_BULLETIN_INTERVAL_SECONDS = int(os.getenv("GOOD_BUY_BULLETIN_INTERVAL_SECONDS", "21600"))
GOOD_BUY_BULLETIN_TIER = os.getenv("GOOD_BUY_BULLETIN_TIER", "balanced").strip().lower()
GOOD_BUY_BULLETIN_MAX_TOKENS = int(os.getenv("GOOD_BUY_BULLETIN_MAX_TOKENS", "5"))
GOOD_BUY_BULLETIN_BOOT_SECONDS = int(os.getenv("GOOD_BUY_BULLETIN_BOOT_SECONDS", "90"))
GOOD_BUY_BULLETIN_INCLUDE_ALL_TIERS = _env_bool("GOOD_BUY_BULLETIN_INCLUDE_ALL_TIERS", default=True)
GOOD_BUY_BULLETIN_PER_TIER_MAX_TOKENS = int(os.getenv("GOOD_BUY_BULLETIN_PER_TIER_MAX_TOKENS", "2"))

# =========================================================
# RISK CONTROL / ANTI-SPAM
# =========================================================

STATE_FILE = "engine_state.json"
ALERT_COOLDOWN_HOURS = int(os.getenv("ALERT_COOLDOWN_HOURS", str(_profile_default(12, 6))))
PROCESS_LOCK_FILE = os.getenv("PROCESS_LOCK_FILE", "engine.lock")
LOG_JSON_ENABLED = _env_bool("LOG_JSON_ENABLED", default=True)
LOG_JSON_PATH = os.getenv("LOG_JSON_PATH", "logs/engine.jsonl")
LOG_MAX_BYTES = int(os.getenv("LOG_MAX_BYTES", "5242880"))
LOG_BACKUP_COUNT = int(os.getenv("LOG_BACKUP_COUNT", "5"))
ENABLE_RISK_GOVERNOR = _env_bool("ENABLE_RISK_GOVERNOR", default=True)
GLOBAL_TRADING_PAUSE = _env_bool("GLOBAL_TRADING_PAUSE", default=False)
# Portfolio size for position sizing (total trading capital in USD)
PORTFOLIO_USD = float(os.getenv("PORTFOLIO_USD", "5000"))
# Position size bounds as % of portfolio
POSITION_SIZE_MIN_PCT = float(os.getenv("POSITION_SIZE_MIN_PCT", "1.0"))
POSITION_SIZE_MAX_PCT = float(os.getenv("POSITION_SIZE_MAX_PCT", "8.0"))
MAX_ALERTS_PER_DAY = int(os.getenv("MAX_ALERTS_PER_DAY", "8"))
MAX_ALERTS_PER_SYMBOL_PER_DAY = int(os.getenv("MAX_ALERTS_PER_SYMBOL_PER_DAY", "2"))
MAX_CONSECUTIVE_4H_LOSSES = int(os.getenv("MAX_CONSECUTIVE_4H_LOSSES", "3"))
LOSS_STREAK_PAUSE_HOURS = int(os.getenv("LOSS_STREAK_PAUSE_HOURS", "6"))
LOSS_STREAK_LOOKBACK_HOURS = int(os.getenv("LOSS_STREAK_LOOKBACK_HOURS", "24"))
SYMBOL_CONTROL_ENABLED = _env_bool("SYMBOL_CONTROL_ENABLED", default=True)
SYMBOL_LOSS_STREAK_TRIGGER = int(os.getenv("SYMBOL_LOSS_STREAK_TRIGGER", "3"))
SYMBOL_COOLDOWN_HOURS = int(os.getenv("SYMBOL_COOLDOWN_HOURS", "24"))
SYMBOL_BLACKLIST_MIN_SAMPLES = int(os.getenv("SYMBOL_BLACKLIST_MIN_SAMPLES", "3"))
SYMBOL_BLACKLIST_AVG_24H_PCT = float(os.getenv("SYMBOL_BLACKLIST_AVG_24H_PCT", "-8"))
SYMBOL_BLACKLIST_HOURS = int(os.getenv("SYMBOL_BLACKLIST_HOURS", "72"))

# Auto sell/exit warnings — disabled: BUY-only mode
SELL_ALERTS_ENABLED = _env_bool("SELL_ALERTS_ENABLED", default=False)
SELL_ALERT_MAX_PER_CYCLE = int(os.getenv("SELL_ALERT_MAX_PER_CYCLE", "2"))
SELL_ALERT_COOLDOWN_HOURS = int(os.getenv("SELL_ALERT_COOLDOWN_HOURS", "8"))
SELL_HYPE_FADE_MAX_CHANGE_1H = float(os.getenv("SELL_HYPE_FADE_MAX_CHANGE_1H", "-2.0"))
SELL_HYPE_FADE_MAX_CHANGE_6H = float(os.getenv("SELL_HYPE_FADE_MAX_CHANGE_6H", "-6.0"))
SELL_HYPE_FADE_MAX_CHANGE_24H = float(os.getenv("SELL_HYPE_FADE_MAX_CHANGE_24H", "-10.0"))
SELL_HYPE_FADE_MAX_VOL_TO_LIQ = float(os.getenv("SELL_HYPE_FADE_MAX_VOL_TO_LIQ", "0.25"))
SELL_CONSOLIDATION_MIN_CHANGE_24H = float(os.getenv("SELL_CONSOLIDATION_MIN_CHANGE_24H", "18.0"))
SELL_CONSOLIDATION_MAX_ABS_CHANGE_1H = float(os.getenv("SELL_CONSOLIDATION_MAX_ABS_CHANGE_1H", "1.2"))
SELL_CONSOLIDATION_MAX_ABS_CHANGE_6H = float(os.getenv("SELL_CONSOLIDATION_MAX_ABS_CHANGE_6H", "3.0"))
SELL_CONSOLIDATION_MAX_VOL_TO_LIQ = float(os.getenv("SELL_CONSOLIDATION_MAX_VOL_TO_LIQ", "0.35"))
SELL_CONSOLIDATION_MAX_TXNS_H1 = int(os.getenv("SELL_CONSOLIDATION_MAX_TXNS_H1", "220"))

# =========================================================
# LEGACY RECOVERY SCANNER (old established coins showing fresh reversals)
# =========================================================

LEGACY_RECOVERY_ENABLED = _env_bool("LEGACY_RECOVERY_ENABLED", default=True)
LEGACY_RECOVERY_SCAN_INTERVAL_SECONDS = int(os.getenv("LEGACY_RECOVERY_SCAN_INTERVAL_SECONDS", "1800"))
LEGACY_RECOVERY_SEARCH_QUERIES = [
    q.strip()
    for q in os.getenv("LEGACY_RECOVERY_SEARCH_QUERIES", "BONK,WIF,PEPE,SHIB,DOGE,FLOKI,POPCAT,BOME,MYRO,NEIRO").split(",")
    if q.strip()
]
LEGACY_RECOVERY_PAIRS_PER_QUERY = int(os.getenv("LEGACY_RECOVERY_PAIRS_PER_QUERY", "10"))
LEGACY_RECOVERY_MIN_AGE_DAYS = float(os.getenv("LEGACY_RECOVERY_MIN_AGE_DAYS", "90"))
LEGACY_RECOVERY_MIN_LIQUIDITY = float(os.getenv("LEGACY_RECOVERY_MIN_LIQUIDITY", "1000000"))
LEGACY_RECOVERY_MIN_VOLUME_24H = float(os.getenv("LEGACY_RECOVERY_MIN_VOLUME_24H", "500000"))
LEGACY_RECOVERY_VOLUME_SPIKE_MULTIPLIER = float(os.getenv("LEGACY_RECOVERY_VOLUME_SPIKE_MULTIPLIER", "2.0"))
LEGACY_RECOVERY_MAX_ALERTS_PER_CYCLE = int(os.getenv("LEGACY_RECOVERY_MAX_ALERTS_PER_CYCLE", "2"))
LEGACY_RECOVERY_COOLDOWN_HOURS = int(os.getenv("LEGACY_RECOVERY_COOLDOWN_HOURS", "12"))

# =========================================================
# TACTICAL PROFILE (short-term momentum + pullback)
# =========================================================

TACTICAL_PULLBACK_MIN_PCT = float(os.getenv("TACTICAL_PULLBACK_MIN_PCT", "20"))
TACTICAL_PULLBACK_MAX_PCT = float(os.getenv("TACTICAL_PULLBACK_MAX_PCT", "30"))
TACTICAL_TREND_MIN_CHANGE_24H = float(os.getenv("TACTICAL_TREND_MIN_CHANGE_24H", "5"))
TACTICAL_MIN_MOMENTUM_CHANGE_1H = float(os.getenv("TACTICAL_MIN_MOMENTUM_CHANGE_1H", "0.3"))
TACTICAL_MIN_VOL_TO_LIQ_RATIO = float(os.getenv("TACTICAL_MIN_VOL_TO_LIQ_RATIO", "0.12"))
TACTICAL_MAX_LAST_TRADE_AGE_MINUTES = int(os.getenv("TACTICAL_MAX_LAST_TRADE_AGE_MINUTES", "45"))
TACTICAL_ENABLE_REAL_TECHNICALS = _env_bool("TACTICAL_ENABLE_REAL_TECHNICALS", default=True)
TACTICAL_REQUIRE_TECHNICAL_CONFIRMATION = _env_bool("TACTICAL_REQUIRE_TECHNICAL_CONFIRMATION", default=False)
TACTICAL_OHLCV_TYPE = os.getenv("TACTICAL_OHLCV_TYPE", "15m").strip()
TACTICAL_OHLCV_LOOKBACK_HOURS = int(os.getenv("TACTICAL_OHLCV_LOOKBACK_HOURS", "36"))
TACTICAL_TECH_CACHE_SECONDS = int(os.getenv("TACTICAL_TECH_CACHE_SECONDS", "600"))
TACTICAL_RSI_PERIOD = int(os.getenv("TACTICAL_RSI_PERIOD", "14"))
TACTICAL_RSI_MIN = float(os.getenv("TACTICAL_RSI_MIN", "50"))
TACTICAL_RSI_MAX = float(os.getenv("TACTICAL_RSI_MAX", "78"))
TACTICAL_MACD_HIST_MIN = float(os.getenv("TACTICAL_MACD_HIST_MIN", "0"))
