import logging
import json
import html
import os
import sys
import time as time_module
from pathlib import Path
from datetime import datetime, time, timedelta, timezone
from logging.handlers import RotatingFileHandler

from telegram import CopyTextButton, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from config import (
    ENGINE_PROFILE,
    ALERT_THRESHOLD,
    ALERT_TOP_N,
    ALERT_COOLDOWN_HOURS,
    ALERT_DATA_DRIFT_WARN_PCT,
    ALERT_DATA_REFRESH_ENABLED,
    ALERT_HIDE_UNVERIFIED_HOLDERS,
    ALERT_REQUIRE_REFRESH_SUCCESS,
    BEARISH_ALERT_COOLDOWN_HOURS,
    BEARISH_ALERT_THRESHOLD,
    BEARISH_MAX_ALERTS_PER_CYCLE,
    BEARISH_MAX_ALERTS_PER_DAY,
    BEARISH_MIN_CONFIDENCE_TO_ALERT,
    BEARISH_REGIME_MIN_SCORE,
    CONFIDENCE_MIN_A,
    CONFIDENCE_MIN_B,
    DAILY_SUMMARY_ENABLED,
    DAILY_SUMMARY_HOUR_UTC,
    DRY_RUN,
    ENABLE_REGIME_GATE,
    ENABLE_EXECUTION_QUALITY_FILTERS,
    ENABLE_EXTREME_RISK_HARD_BLOCK,
    ENABLE_RISK_GOVERNOR,
    ENABLE_SOL_REGIME_LAYER,
    GLOBAL_TRADING_PAUSE,
    GOOD_BUY_BULLETIN_BOOT_SECONDS,
    GOOD_BUY_BULLETIN_ENABLED,
    GOOD_BUY_BULLETIN_INTERVAL_SECONDS,
    GOOD_BUY_BULLETIN_PER_TIER_MAX_TOKENS,
    GOOD_BUY_BULLETIN_TIER,
    LOG_BACKUP_COUNT,
    LOG_JSON_ENABLED,
    LOG_JSON_PATH,
    LOG_MAX_BYTES,
    LOSS_STREAK_PAUSE_HOURS,
    LOSS_STREAK_LOOKBACK_HOURS,
    MAX_ABS_CHANGE_24H,
    MAX_ALERTS_PER_DAY,
    MAX_ALERTS_PER_SYMBOL_PER_DAY,
    MAX_ALLOWED_DRAWDOWN_24H,
    MAX_ALERTS_PER_CYCLE,
    MAX_CONSECUTIVE_4H_LOSSES,
    MAX_VOL_TO_LIQ_RATIO,
    MIN_LIQUIDITY_PER_HOLDER,
    MIN_CONFIDENCE_TO_ALERT,
    MIN_HOLDERS,
    MIN_LIQUIDITY,
    MIN_PRICE_USD,
    MIN_VOL_TO_LIQ_RATIO,
    MIN_VOLUME_24H,
    NEW_RUNNER_COOLDOWN_HOURS,
    NEW_RUNNER_MAX_AGE_HOURS,
    NEW_RUNNER_MAX_ALERTS_PER_CYCLE,
    NEW_RUNNER_MAX_RESULTS,
    NEW_RUNNER_MIN_ALERT_SCORE,
    NEW_RUNNER_MIN_CHANGE_24H,
    NEW_RUNNER_MIN_LIQUIDITY,
    NEW_RUNNER_MIN_MARKET_CAP,
    NEW_RUNNER_MIN_TXNS_H1,
    NEW_RUNNER_MIN_VOLUME_24H,
    NEW_RUNNER_NARRATIVE_KEYWORDS,
    NEW_RUNNER_PAIRS_PER_QUERY,
    NEW_RUNNER_REQUIRE_SOCIAL_LINKS,
    NEW_RUNNER_SCAN_INTERVAL_SECONDS,
    NEW_RUNNER_SEARCH_QUERIES,
    NEW_RUNNER_WATCH_ENABLED,
    OUTCOME_EVAL_BATCH_SIZE,
    OUTCOME_EVAL_INTERVAL_SECONDS,
    OUTCOME_TRACKING_ENABLED,
    PROCESS_LOCK_FILE,
    REGIME_MIN_SCORE,
    RISK_STYLE,
    SCAN_INTERVAL_SECONDS,
    SELL_ALERT_COOLDOWN_HOURS,
    SELL_ALERT_MAX_PER_CYCLE,
    SELL_ALERTS_ENABLED,
    SELL_CONSOLIDATION_MAX_ABS_CHANGE_1H,
    SELL_CONSOLIDATION_MAX_ABS_CHANGE_6H,
    SELL_CONSOLIDATION_MAX_TXNS_H1,
    SELL_CONSOLIDATION_MAX_VOL_TO_LIQ,
    SELL_CONSOLIDATION_MIN_CHANGE_24H,
    SELL_HYPE_FADE_MAX_CHANGE_1H,
    SELL_HYPE_FADE_MAX_CHANGE_24H,
    SELL_HYPE_FADE_MAX_CHANGE_6H,
    SELL_HYPE_FADE_MAX_VOL_TO_LIQ,
    SIGNAL_DIGEST_ENABLED,
    SIGNAL_DIGEST_INTERVAL_SECONDS,
    SIGNAL_DIGEST_LOOKBACK_HOURS,
    SIGNAL_DIGEST_MAX_ITEMS,
    SOL_BREADTH_MIN_FOR_RISK_ON,
    SOL_EXTREME_OFF_CHANGE_1H,
    SOL_EXTREME_OFF_CHANGE_24H,
    SOL_REGIME_QUERY,
    SOL_RISK_OFF_CHANGE_24H,
    SOL_RISK_ON_CHANGE_24H,
    TELEGRAM_CHAT_ID,
    TELEGRAM_ACTION_BUTTONS_ENABLED,
    TELEGRAM_TOKEN,
    TELEGRAM_QUIET_MODE,
    TELEGRAM_PUSH_MIN_CONFIDENCE,
    TELEGRAM_PUSH_MIN_SCORE_DELTA,
    TELEGRAM_PUSH_STRONG_ONLY,
    WATCHLIST_ALERT_COOLDOWN_HOURS,
    WATCHLIST_ALERT_ON_STATUS_CHANGE,
    WATCHLIST_ALERT_STATUSES,
    WATCHLIST_ENTRIES,
    WATCHLIST_LANE_ENABLED,
    WATCHLIST_MAX_ALERTS_PER_CYCLE,
    WATCHLIST_MIN_LIQUIDITY,
    WATCHLIST_MIN_VOLUME_24H,
    WATCHLIST_SCAN_INTERVAL_SECONDS,
    WATCHLIST_SUMMARY_ENABLED,
    WATCHLIST_SUMMARY_HOUR_UTC,
    WEEKLY_TUNING_DAY_UTC,
    WEEKLY_TUNING_ENABLED,
    WEEKLY_TUNING_HOUR_UTC,
    WEEKLY_TUNING_LOOKBACK_DAYS,
    WEEKLY_TUNING_MIN_OUTCOMES_4H,
    SYMBOL_BLACKLIST_AVG_24H_PCT,
    SYMBOL_BLACKLIST_HOURS,
    SYMBOL_BLACKLIST_MIN_SAMPLES,
    SYMBOL_CONTROL_ENABLED,
    SYMBOL_COOLDOWN_HOURS,
    SYMBOL_LOSS_STREAK_TRIGGER,
    TACTICAL_MAX_LAST_TRADE_AGE_MINUTES,
    TACTICAL_ENABLE_REAL_TECHNICALS,
    TACTICAL_MACD_HIST_MIN,
    TACTICAL_OHLCV_LOOKBACK_HOURS,
    TACTICAL_OHLCV_TYPE,
    TACTICAL_REQUIRE_TECHNICAL_CONFIRMATION,
    TACTICAL_RSI_MAX,
    TACTICAL_RSI_MIN,
    TACTICAL_RSI_PERIOD,
    TACTICAL_TECH_CACHE_SECONDS,
    TACTICAL_MIN_MOMENTUM_CHANGE_1H,
    TACTICAL_MIN_VOL_TO_LIQ_RATIO,
    TACTICAL_PULLBACK_MAX_PCT,
    TACTICAL_PULLBACK_MIN_PCT,
    TACTICAL_TREND_MIN_CHANGE_24H,
    LEGACY_RECOVERY_ENABLED,
    LEGACY_RECOVERY_SCAN_INTERVAL_SECONDS,
    LEGACY_RECOVERY_SEARCH_QUERIES,
    LEGACY_RECOVERY_PAIRS_PER_QUERY,
    LEGACY_RECOVERY_MIN_AGE_DAYS,
    LEGACY_RECOVERY_MIN_LIQUIDITY,
    LEGACY_RECOVERY_MIN_VOLUME_24H,
    LEGACY_RECOVERY_VOLUME_SPIKE_MULTIPLIER,
    LEGACY_RECOVERY_MAX_ALERTS_PER_CYCLE,
    LEGACY_RECOVERY_COOLDOWN_HOURS,
    PORTFOLIO_USD,
)
from data.birdeye import (
    fetch_birdeye_ohlcv,
    fetch_birdeye_price,
    fetch_birdeye_token_overview,
)
from data.dexscreener import (
    fetch_legacy_recovery_candidates,
    fetch_runner_watch_candidates,
    fetch_sol_market_proxy,
    fetch_token_snapshot as fetch_dexscreener_token_snapshot,
)
from data.market_data import fetch_market_data
from scoring import calculate_token_score, calculate_token_score_with_breakdown
from utils.metrics import closes, macd, rsi
from utils.db import (
    close_manual_position,
    clear_risk_pause,
    count_alerts_since,
    get_active_symbol_control,
    get_alert_outcome_recap,
    get_consecutive_losing_outcomes_4h,
    get_engine_health_snapshot,
    get_last_alert_timestamp,
    get_latest_4h_outcome_timestamp,
    get_latest_engine_event,
    get_last_decision_timestamp_for_symbol,
    get_outcome_queue_stats,
    get_open_positions,
    get_pending_alert_outcomes,
    get_performance_summary,
    get_portfolio_simulation_metrics,
    get_recent_scan_bests,
    get_risk_mode,
    get_risk_pause_state,
    get_symbol_outcome_stats,
    get_weekly_tuning_report,
    init_db,
    log_signal,
    mark_alert_outcome_complete,
    mark_alert_outcome_error,
    open_manual_position,
    queue_alert_outcome,
    set_risk_pause,
    set_symbol_control,
    update_alert_outcome_horizon,
)
from utils.format import (
    format_legacy_recovery,
    format_runner_watch,
    format_signal,
    format_watchlist_signal,
    format_watchlist_summary,
)
from utils.singleton import SingletonProcessLock
from crypto_news import get_digest as get_news_digest, check_news_updates
from elite_features import (
    format_sol_macro_alert,
    update_sol_correlations,
    ensure_correlations_table,
)
from jupiter_perps import (
    fetch_jupiter_position,
    fetch_sol_price,
    fetch_sol_volatility_30d,
    check_alerts,
    check_dca_zone_alert,
    calc_leverage_recommendation,
    calc_price_zones,
    add_dca_entry,
    calc_dca_summary,
    format_lev_dashboard,
    format_lev_status,
    format_what_if,
    format_leverage_rec,
    format_price_zones,
    format_dca_dashboard,
    PRICE_TARGETS,
    PRICE_ZONE_LEVELS,
    CHECK_INTERVAL_SECONDS,
    MONTHLY_ADD_USD,
)

class _MaxLevelFilter(logging.Filter):
    def __init__(self, max_level):
        super().__init__()
        self.max_level = max_level

    def filter(self, record):
        return record.levelno <= self.max_level


class _JsonFormatter(logging.Formatter):
    def format(self, record):
        payload = {
            "ts": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, separators=(",", ":"))


_root_logger = logging.getLogger()
_root_logger.setLevel(logging.INFO)
_root_logger.handlers.clear()

_stdout_handler = logging.StreamHandler(sys.stdout)
_stdout_handler.setLevel(logging.DEBUG)
_stdout_handler.addFilter(_MaxLevelFilter(logging.INFO))

_stderr_handler = logging.StreamHandler(sys.stderr)
_stderr_handler.setLevel(logging.WARNING)

_formatter = logging.Formatter("%(levelname)s:%(name)s:%(message)s")
_stdout_handler.setFormatter(_formatter)
_stderr_handler.setFormatter(_formatter)

_root_logger.addHandler(_stdout_handler)
_root_logger.addHandler(_stderr_handler)

if LOG_JSON_ENABLED:
    os.makedirs(os.path.dirname(LOG_JSON_PATH) or ".", exist_ok=True)
    _json_handler = RotatingFileHandler(
        LOG_JSON_PATH,
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
    )
    _json_handler.setLevel(logging.INFO)
    _json_handler.setFormatter(_JsonFormatter())
    _root_logger.addHandler(_json_handler)

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("telegram.vendor.ptb_urllib3.urllib3").setLevel(logging.WARNING)

_tactical_tech_cache = {}
_birdeye_enrichment_cache = {}

_CONFIDENCE_ORDER = {"C": 1, "B": 2, "A": 3}


def _enrich_token_for_scoring(token):
    """
    Enrich token with BirdEye token_overview data + Helius on-chain safety before scoring.
    Returns enriched token dict.
    """
    address = token.get("address")
    if not address:
        return token

    # Check cache first (5min TTL = scan interval, avoids API spam)
    cache_key = address
    now = time_module.time()
    cache_ttl = 300  # 5 minutes

    if cache_key in _birdeye_enrichment_cache:
        cached_data, cached_ts = _birdeye_enrichment_cache[cache_key]
        if now - cached_ts < cache_ttl:
            enriched = dict(token)
            enriched.update(cached_data)
            return enriched

    # Fetch fresh data from BirdEye
    overview_data = fetch_birdeye_token_overview(address)
    if overview_data:
        _birdeye_enrichment_cache[cache_key] = (overview_data, now)
        enriched = dict(token)
        enriched.update(overview_data)
    else:
        enriched = dict(token)

    # Helius on-chain safety enrichment (free tier — mint authority + holder concentration)
    try:
        from utils.helius import get_token_safety, is_available as helius_available  # type: ignore
        if helius_available():
            safety = get_token_safety(address, use_cache=True)
            enriched["helius_safety_score"]        = safety.get("safety_score", 50)
            enriched["helius_grade"]               = safety.get("grade", "UNKNOWN")
            enriched["helius_flags"]               = safety.get("flags", [])
            enriched["mint_authority_revoked"]     = safety.get("mint_authority_revoked", False)
            enriched["freeze_authority_revoked"]   = safety.get("freeze_authority_revoked", False)
            enriched["top1_pct"]                   = safety.get("top1_pct", 0.0)
            enriched["top5_pct"]                   = safety.get("top5_pct", 0.0)
            enriched["concentration_risk"]         = safety.get("concentration_risk", "UNKNOWN")
    except Exception as _he:
        logging.debug("Helius enrichment error for %s: %s", address, _he)

    # ATH tracking — update and stamp leg classification onto token
    try:
        from utils.ath_tracker import update_ath  # type: ignore
        _price = float(enriched.get("price") or 0)
        if _price > 0:
            _ts = datetime.utcnow().isoformat()
            _ath = update_ath(address, enriched.get("symbol", ""), _price, _ts)
            enriched["ath_price"]    = _ath.get("ath_price", 0.0)
            enriched["drawdown_pct"] = _ath.get("drawdown_pct", 0.0)
            enriched["leg"]          = _ath.get("leg", "UNKNOWN")
            enriched["is_second_leg"] = _ath.get("is_second_leg", False)
    except Exception as _ae:
        logging.debug("ATH tracker error for %s: %s", address, _ae)

    return enriched
_DAY_TO_WEEKDAY = {"MON": 0, "TUE": 1, "WED": 2, "THU": 3, "FRI": 4, "SAT": 5, "SUN": 6}
_PROJECT_ROOT = Path(__file__).resolve().parent
_ENV_PATH = _PROJECT_ROOT / ".env"
_WATCHLIST_STATE_PATH = _PROJECT_ROOT / "data_storage" / "watchlist_state.json"
_ALLOWED_CHAT_ID = int(TELEGRAM_CHAT_ID)
_app_ref = None
_digest_state = {"last_sent_ts": None}
_watchlist_state = {"statuses": {}}
_SELL_ALERT_EXCLUDED_SYMBOLS = {"USDC", "USDT", "USDS", "USD1", "DAI", "SOL", "WSOL"}

_MODE_PRESETS = {
    "strategic": {
        "profile": "strategic",
        "scan_interval_seconds": 3600,
        "min_liquidity": 1_000_000.0,
        "min_volume_24h": 300_000.0,
        "alert_cooldown_hours": 12,
        "alert_threshold": 80,
        "min_confidence_to_alert": "A",
        "regime_min_score": 45,
    },
    "tactical": {
        "profile": "tactical",
        "scan_interval_seconds": 1800,
        "min_liquidity": 500_000.0,
        "min_volume_24h": 200_000.0,
        "alert_cooldown_hours": 6,
        "alert_threshold": 70,
        "min_confidence_to_alert": "B",
        "regime_min_score": 15,
    },
}

_RISK_STYLE_ALIASES = {
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

_RISK_STYLE_LABELS = {
    "capital": "Capital Preservation",
    "balanced": "Balanced",
    "sniper": "Sniper",
}

_RISK_STYLE_DESCRIPTIONS = {
    "capital": "Defensive: stricter liquidity/volume and tighter volatility limits.",
    "balanced": "Default: moderate filters for steady opportunity flow.",
    "sniper": "High-beta: wider gates for faster, riskier setups.",
}

_MARKET_TIER_RULES = {
    "conservative": {
        "label": "Long-Term",
        "min_confidence": "A",
        "min_score_delta": 6.0,
        "min_liquidity": 1_500_000.0,
        "min_volume_24h": 600_000.0,
        "min_change_24h": 1.5,
        "max_abs_change_24h": 30.0,
        "max_vol_to_liq": 1.2,
        "allow_risk_off": False,
    },
    "balanced": {
        "label": "Mid-Term",
        "min_confidence": "B",
        "min_score_delta": 0.0,
        "min_liquidity": 700_000.0,
        "min_volume_24h": 250_000.0,
        "min_change_24h": 0.5,
        "max_abs_change_24h": 45.0,
        "max_vol_to_liq": 1.8,
        "allow_risk_off": True,
    },
    "aggressive": {
        "label": "Short-Term",
        "min_confidence": "B",
        "min_score_delta": -6.0,
        "min_liquidity": 250_000.0,
        "min_volume_24h": 120_000.0,
        "min_change_24h": -2.0,
        "max_abs_change_24h": 70.0,
        "max_vol_to_liq": 2.8,
        "allow_risk_off": True,
    },
}

_RISK_STYLE_TIER_OVERRIDES = {
    "capital": {
        "conservative": {
            "min_score_delta": 8.0,
            "min_liquidity": 2_000_000.0,
            "min_volume_24h": 800_000.0,
            "min_change_24h": 2.5,
            "max_abs_change_24h": 24.0,
            "max_vol_to_liq": 1.0,
            "allow_risk_off": False,
        },
        "balanced": {
            "min_confidence": "A",
            "min_score_delta": 4.0,
            "min_liquidity": 1_100_000.0,
            "min_volume_24h": 450_000.0,
            "min_change_24h": 1.0,
            "max_abs_change_24h": 35.0,
            "max_vol_to_liq": 1.4,
            "allow_risk_off": False,
        },
        "aggressive": {
            "min_confidence": "B",
            "min_score_delta": 0.0,
            "min_liquidity": 600_000.0,
            "min_volume_24h": 240_000.0,
            "min_change_24h": 0.0,
            "max_abs_change_24h": 50.0,
            "max_vol_to_liq": 1.8,
            "allow_risk_off": False,
        },
    },
    "balanced": {},
    "sniper": {
        "conservative": {
            "min_score_delta": 4.0,
            "min_liquidity": 1_100_000.0,
            "min_volume_24h": 420_000.0,
            "min_change_24h": 1.0,
            "max_abs_change_24h": 36.0,
            "max_vol_to_liq": 1.5,
            "allow_risk_off": False,
        },
        "balanced": {
            "min_confidence": "B",
            "min_score_delta": -2.0,
            "min_liquidity": 500_000.0,
            "min_volume_24h": 180_000.0,
            "min_change_24h": 0.0,
            "max_abs_change_24h": 55.0,
            "max_vol_to_liq": 2.2,
            "allow_risk_off": True,
        },
        "aggressive": {
            "min_confidence": "B",
            "min_score_delta": -10.0,
            "min_liquidity": 180_000.0,
            "min_volume_24h": 90_000.0,
            "min_change_24h": -4.0,
            "max_abs_change_24h": 80.0,
            "max_vol_to_liq": 3.2,
            "allow_risk_off": True,
        },
    },
}

_WALLET_PLAYBOOK = {
    "W1": {
        "name": "Core Long-Term",
        "position_hint": "size 8-15% max, scale-in only",
        "risk_plan": "Soft stop on thesis break; avoid impulse exits",
        "rotation_plan": "Primary destination for harvested profits",
    },
    "W2": {
        "name": "Quality Swing",
        "position_hint": "size 4-8% max",
        "risk_plan": "Hard SL -8% | TP +12/+25 | trail 10%",
        "rotation_plan": "Rotate 40-60% wins to W1/staking/USDC",
    },
    "W3": {
        "name": "Tactical/Degen",
        "position_hint": "size 1-3% max",
        "risk_plan": "Hard SL -12% | scale at +15/+30/+50",
        "rotation_plan": "Rotate 60-80% wins to W1/W2 or USDC",
    },
}

_runtime = {
    "profile": ENGINE_PROFILE if ENGINE_PROFILE in _MODE_PRESETS else "strategic",
    "risk_style": _RISK_STYLE_ALIASES.get(str(RISK_STYLE or "").lower(), "balanced"),
    "scan_interval_seconds": SCAN_INTERVAL_SECONDS,
    "min_liquidity": float(MIN_LIQUIDITY),
    "min_volume_24h": float(MIN_VOLUME_24H),
    "alert_cooldown_hours": ALERT_COOLDOWN_HOURS,
    "alert_threshold": ALERT_THRESHOLD,
    "min_confidence_to_alert": MIN_CONFIDENCE_TO_ALERT,
    "regime_min_score": REGIME_MIN_SCORE,
}


def _mode() -> str:
    return str(_runtime["profile"]).lower()


def _normalize_risk_style(raw: str | None) -> str:
    return _RISK_STYLE_ALIASES.get(str(raw or "").strip().lower(), "balanced")


def _risk_style_label(style: str | None = None) -> str:
    normalized = _normalize_risk_style(style or _runtime.get("risk_style"))
    return _RISK_STYLE_LABELS.get(normalized, "Balanced")


def _risk_style_description(style: str | None = None) -> str:
    normalized = _normalize_risk_style(style or _runtime.get("risk_style"))
    return _RISK_STYLE_DESCRIPTIONS.get(
        normalized,
        "Default: moderate filters for steady opportunity flow.",
    )


def _market_tier_rule(tier_key: str) -> dict:
    tier = _normalize_market_tier(tier_key)
    base = dict(_MARKET_TIER_RULES[tier])
    style = _normalize_risk_style(_runtime.get("risk_style"))
    overrides = _RISK_STYLE_TIER_OVERRIDES.get(style, {}).get(tier, {})
    if overrides:
        base.update(overrides)
    return base


def _is_authorized(update: Update) -> bool:
    chat = update.effective_chat
    return bool(chat and chat.id == _ALLOWED_CHAT_ID)


def _set_env_values(values: dict[str, str]):
    existing = _ENV_PATH.read_text().splitlines() if _ENV_PATH.exists() else []
    output = []
    seen = set()

    for line in existing:
        stripped = line.strip()
        replaced = False
        for key, value in values.items():
            if stripped.startswith(f"{key}="):
                if key not in seen:
                    output.append(f"{key}={value}")
                    seen.add(key)
                replaced = True
                break
        if not replaced:
            output.append(line)

    for key, value in values.items():
        if key not in seen:
            output.append(f"{key}={value}")

    _ENV_PATH.write_text("\n".join(output).rstrip() + "\n")


def _apply_mode(mode: str):
    mode = mode.lower()
    if mode not in _MODE_PRESETS:
        return
    _runtime.update(_MODE_PRESETS[mode])


def _persist_mode(mode: str):
    preset = _MODE_PRESETS[mode]
    _set_env_values(
        {
            "ENGINE_PROFILE": preset["profile"],
            "SCAN_INTERVAL_SECONDS": str(preset["scan_interval_seconds"]),
            "MIN_LIQUIDITY": str(int(preset["min_liquidity"])),
            "MIN_VOLUME_24H": str(int(preset["min_volume_24h"])),
            "ALERT_COOLDOWN_HOURS": str(preset["alert_cooldown_hours"]),
            "ALERT_THRESHOLD": str(preset["alert_threshold"]),
            "MIN_CONFIDENCE_TO_ALERT": str(preset["min_confidence_to_alert"]),
            "REGIME_MIN_SCORE": str(preset["regime_min_score"]),
        }
    )


def _fmt_dt(dt):
    if not dt:
        return "N/A"
    return dt.strftime("%Y-%m-%d %H:%M:%S UTC")


def _try_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_symbol_or_mint(raw: str):
    token = str(raw or "").strip()
    if not token:
        return None, None
    token = token.lstrip("$")
    if len(token) >= 32:
        return None, token
    symbol = "".join(ch for ch in token.upper() if ch.isalnum())[:16]
    if not symbol:
        return None, None
    return symbol, None


def _resolve_token_for_tracking(symbol: str | None, mint: str | None):
    if mint:
        snap = fetch_dexscreener_token_snapshot(mint)
        if snap:
            return snap
    if symbol:
        candidates = fetch_runner_watch_candidates(
            queries=[symbol],
            pairs_per_query=16,
            limit=50,
        )
        if candidates:
            exact = [t for t in candidates if str(t.get("symbol") or "").upper() == symbol]
            pool = exact or candidates
            pool.sort(key=lambda t: (float(t.get("liquidity", 0) or 0), float(t.get("volume_24h", 0) or 0)), reverse=True)
            return pool[0]
    return None


def _build_alert_keyboard(symbol: str, mint: str | None, pair_address: str | None = None):
    if not TELEGRAM_ACTION_BUTTONS_ENABLED:
        return None
    if not symbol:
        symbol = "UNKNOWN"
    rows = []
    mint_text = str(mint or "").strip()
    dexscreener_target = pair_address or mint
    if dexscreener_target:
        first_row = [
            InlineKeyboardButton("DexScreener", url=f"https://dexscreener.com/solana/{dexscreener_target}"),
        ]
        if mint_text:
            first_row.append(
                InlineKeyboardButton(
                    "Copy CA",
                    copy_text=CopyTextButton(text=mint_text),
                )
            )
        rows.append(first_row)
    if mint_text:
        rows.append(
            [
                InlineKeyboardButton("Solscan", url=f"https://solscan.io/token/{mint_text}"),
                InlineKeyboardButton("RugCheck", url=f"https://rugcheck.xyz/tokens/{mint_text}"),
            ]
        )
    if not rows:
        return None
    return InlineKeyboardMarkup(rows)


def _reschedule_run_engine_jobs(application):
    interval = int(_runtime["scan_interval_seconds"])
    for job in application.job_queue.get_jobs_by_name("run_engine_cycle"):
        job.schedule_removal()
    for job in application.job_queue.get_jobs_by_name("run_engine_boot"):
        job.schedule_removal()

    application.job_queue.run_once(
        run_engine,
        when=2,
        name="run_engine_boot",
        job_kwargs={"misfire_grace_time": 30},
    )
    application.job_queue.run_repeating(
        run_engine,
        interval=interval,
        first=interval,
        name="run_engine_cycle",
        job_kwargs={"misfire_grace_time": 30, "coalesce": True},
    )


def _require_env():
    if not TELEGRAM_TOKEN:
        raise ValueError("❌ TELEGRAM_TOKEN missing in .env")
    if not TELEGRAM_CHAT_ID:
        raise ValueError("❌ TELEGRAM_CHAT_ID missing in .env")
    if not str(TELEGRAM_CHAT_ID).isdigit():
        raise ValueError("❌ TELEGRAM_CHAT_ID must be numeric (example: 1887678023)")


def _to_int_or_none(value):
    if value in (None, "", "N/A"):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _passes_quality_filters(token):
    """
    Basic quality filters to exclude junk/illiquid tokens.
    Focus: minimum standards, not entry timing.
    """
    liquidity = float(token.get("liquidity", 0) or 0)
    volume_24h = float(token.get("volume_24h", 0) or 0)
    change_24h = float(token.get("change_24h", 0) or 0)
    holders = _to_int_or_none(token.get("holders"))

    # Minimum liquidity/volume standards
    if liquidity < float(_runtime["min_liquidity"]):
        return False
    if volume_24h < float(_runtime["min_volume_24h"]):
        return False

    # Not already dumping hard
    if change_24h <= MAX_ALLOWED_DRAWDOWN_24H:
        return False

    # Minimum holder base
    if holders is not None and holders < MIN_HOLDERS:
        return False

    # EARLY ENTRY LOGIC: Avoid late pumps
    # If 24h change is too extreme, we're late to the party
    if change_24h > 100:  # Already 2x+ in 24h = too late
        return False

    # If holders are present, check for healthy growth vs pump & dump
    uw_1h = token.get("uniqueWallet1h")
    uw_1h_change = token.get("uniqueWallet1hChangePercent")

    # If holders are rapidly exiting (negative growth), skip
    if uw_1h_change is not None and uw_1h_change < -15:
        return False

    # If we have very low holder count with massive price pump = likely bot farm
    if uw_1h is not None and uw_1h < 30 and change_24h > 50:
        return False

    return True


def _passes_execution_quality_filters(token):
    """
    Advanced filters focused on execution quality and early-stage detection.
    Goal: catch momentum EARLY, not after parabolic moves.
    """
    if not ENABLE_EXECUTION_QUALITY_FILTERS:
        return True

    liquidity = float(token.get("liquidity", 0) or 0)
    volume_24h = float(token.get("volume_24h", 0) or 0)
    price = float(token.get("price", 0) or 0)
    change_24h = float(token.get("change_24h", 0) or 0)
    change_1h = token.get("change_1h") or token.get("priceChange1hPercent")
    change_4h = token.get("change_4h") or token.get("priceChange4hPercent")
    holders = _to_int_or_none(token.get("holders"))

    # Price must be tradeable
    if price < MIN_PRICE_USD:
        return False

    # Avoid parabolic late pumps
    if abs(change_24h) > MAX_ABS_CHANGE_24H:
        return False

    # Volume/liquidity ratio must be healthy (not overheated, not dead)
    vol_to_liq = (volume_24h / liquidity) if liquidity > 0 else 0
    if vol_to_liq < MIN_VOL_TO_LIQ_RATIO or vol_to_liq > MAX_VOL_TO_LIQ_RATIO:
        return False

    # Liquidity per holder concentration check
    if holders and holders > 0:
        liq_per_holder = liquidity / holders
        if liq_per_holder < MIN_LIQUIDITY_PER_HOLDER:
            return False

    # EARLY ENTRY ENHANCEMENT: Look for acceleration patterns
    # Best signal: 4h trend + 1h acceleration (not late pump)
    if change_4h is not None and change_1h is not None:
        # If 4h is strongly positive but 1h is dumping = top signal, skip
        if change_4h > 10 and change_1h < -5:
            return False

        # If 24h is parabolic but short-term is fading = late, skip
        if change_24h > 50 and change_4h < 5:
            return False

    # Check holder momentum for quality
    uw_1h_change = token.get("uniqueWallet1hChangePercent")
    uw_4h_change = token.get("uniqueWallet4hChangePercent")

    # If holders are accelerating out (negative growth), skip
    if uw_1h_change is not None and uw_1h_change < -20:
        return False

    # If 4h holder trend is negative while price is pumping = distribution
    if uw_4h_change is not None and change_24h > 20 and uw_4h_change < -10:
        return False

    return True


def _passes_tactical_filters(token):
    if _mode() != "tactical":
        return True

    liquidity = float(token.get("liquidity", 0) or 0)
    volume_24h = float(token.get("volume_24h", 0) or 0)
    change_24h = float(token.get("change_24h", 0) or 0)
    change_1h_raw = token.get("change_1h")
    change_6h_raw = token.get("change_6h")
    change_1h = float(change_1h_raw) if isinstance(change_1h_raw, (int, float)) else None
    change_6h = float(change_6h_raw) if isinstance(change_6h_raw, (int, float)) else None

    if liquidity <= 0 or volume_24h <= 0:
        return False

    vol_to_liq = volume_24h / liquidity
    if vol_to_liq < TACTICAL_MIN_VOL_TO_LIQ_RATIO:
        return False

    pullback_pct = abs(change_24h) if change_24h < 0 else 0.0
    in_pullback_zone = TACTICAL_PULLBACK_MIN_PCT <= pullback_pct <= TACTICAL_PULLBACK_MAX_PCT

    momentum_ok = change_24h >= TACTICAL_TREND_MIN_CHANGE_24H
    if change_1h is not None:
        momentum_ok = momentum_ok and change_1h >= TACTICAL_MIN_MOMENTUM_CHANGE_1H
    elif change_6h is not None:
        momentum_ok = momentum_ok and change_6h > 0

    support_bounce_ok = in_pullback_zone and (
        (change_1h is not None and change_1h >= 0)
        or (change_6h is not None and change_6h >= 0)
        or (change_1h is None and change_6h is None)
    )

    if not (momentum_ok or support_bounce_ok):
        return False

    last_trade_unix = _to_int_or_none(token.get("last_trade_unix"))
    if last_trade_unix:
        age_minutes = (datetime.utcnow() - datetime.utcfromtimestamp(last_trade_unix)).total_seconds() / 60.0
        if age_minutes > TACTICAL_MAX_LAST_TRADE_AGE_MINUTES:
            return False

    tech = _get_tactical_technicals(token)
    if tech:
        token.update(tech)
        rsi_ok = TACTICAL_RSI_MIN <= tech["rsi"] <= TACTICAL_RSI_MAX
        macd_ok = (
            tech["macd_hist"] >= TACTICAL_MACD_HIST_MIN
            and tech["macd_line"] >= tech["macd_signal"]
        )
        if not (rsi_ok and macd_ok):
            return False
    elif TACTICAL_ENABLE_REAL_TECHNICALS and TACTICAL_REQUIRE_TECHNICAL_CONFIRMATION:
        return False

    return True


def _pct_drift(old_value, new_value):
    try:
        old_n = float(old_value)
        new_n = float(new_value)
    except (TypeError, ValueError):
        return None
    if old_n <= 0 or new_n <= 0:
        return None
    return abs(new_n - old_n) / old_n * 100.0


def _refresh_alert_market_snapshot(token: dict):
    if not ALERT_DATA_REFRESH_ENABLED:
        return token

    address = token.get("address")
    if not address:
        return token

    snapshot = fetch_dexscreener_token_snapshot(address)
    if not snapshot:
        if ALERT_REQUIRE_REFRESH_SUCCESS:
            return None
        return token

    merged = dict(token)
    for field in ("liquidity", "volume_24h", "price", "change_24h", "change_6h", "change_1h", "market_cap", "fdv"):
        snap_value = snapshot.get(field)
        if snap_value is None:
            continue
        drift = _pct_drift(token.get(field), snap_value)
        if drift is not None and drift >= ALERT_DATA_DRIFT_WARN_PCT:
            logging.info(
                "Alert data drift adjusted for %s field=%s old=%.4f new=%.4f drift=%.1f%%",
                token.get("symbol", "UNKNOWN"),
                field,
                float(token.get(field) or 0),
                float(snap_value or 0),
                drift,
            )
        merged[field] = snap_value

    if snapshot.get("pair_address"):
        merged["pair_address"] = snapshot["pair_address"]

    # Dex live snapshot currently does not provide holder count; mark as unverified.
    merged["holders_verified"] = bool(snapshot.get("holders"))
    if merged["holders_verified"]:
        merged["holders"] = snapshot.get("holders")

    merged["source"] = "dexscreener_live"
    return merged


def _holders_for_alert(token: dict):
    holders = token.get("holders")
    if not ALERT_HIDE_UNVERIFIED_HOLDERS:
        return holders
    if token.get("holders_verified"):
        return holders
    return "N/A"


def _get_tactical_technicals(token):
    if _mode() != "tactical" or not TACTICAL_ENABLE_REAL_TECHNICALS:
        return {}
    address = token.get("address")
    if not address:
        return {}

    now_ts = int(time_module.time())
    cached = _tactical_tech_cache.get(address)
    if cached and now_ts - cached["ts"] <= TACTICAL_TECH_CACHE_SECONDS:
        return cached["data"]

    candles = fetch_birdeye_ohlcv(
        address=address,
        candle_type=TACTICAL_OHLCV_TYPE,
        lookback_hours=TACTICAL_OHLCV_LOOKBACK_HOURS,
    )
    close_values = closes(candles)
    if len(close_values) < max(35, TACTICAL_RSI_PERIOD + 2):
        return {}

    rsi_value = rsi(close_values, period=TACTICAL_RSI_PERIOD)
    macd_value = macd(close_values)
    if rsi_value is None or not macd_value:
        return {}

    snapshot = {
        "rsi": float(rsi_value),
        "macd_line": float(macd_value["macd_line"]),
        "macd_signal": float(macd_value["macd_signal"]),
        "macd_hist": float(macd_value["macd_hist"]),
    }
    _tactical_tech_cache[address] = {"ts": now_ts, "data": snapshot}
    return snapshot


def _confidence_from_score(score):
    if score >= CONFIDENCE_MIN_A:
        return "A"
    if score >= CONFIDENCE_MIN_B:
        return "B"
    return "C"


def _confidence_meets_rule(confidence, minimum):
    minimum_raw = str(minimum or "B").upper()
    minimum_norm = minimum_raw if minimum_raw in _CONFIDENCE_ORDER else "B"
    return _CONFIDENCE_ORDER.get(confidence, 0) >= _CONFIDENCE_ORDER[minimum_norm]


def _confidence_meets_alert_rule(confidence):
    return _confidence_meets_rule(confidence, _runtime["min_confidence_to_alert"])


def _is_symbol_on_cooldown(symbol: str, cooldown_hours: int | float | None = None) -> bool:
    if not symbol:
        return False
    last_alert_ts = get_last_alert_timestamp(symbol)
    if not last_alert_ts:
        return False
    effective_hours = int(cooldown_hours if cooldown_hours is not None else _runtime["alert_cooldown_hours"])
    return (datetime.utcnow() - last_alert_ts) < timedelta(hours=effective_hours)


def _apply_symbol_controls(symbol: str):
    if not SYMBOL_CONTROL_ENABLED or not symbol:
        return None
    return get_active_symbol_control(symbol)


def _risk_governor_status() -> tuple[bool, str]:
    if GLOBAL_TRADING_PAUSE:
        return False, "GLOBAL_TRADING_PAUSE enabled"
    if not ENABLE_RISK_GOVERNOR:
        return True, ""

    now = datetime.utcnow()
    pause_state = get_risk_pause_state()
    pause_until = pause_state.get("pause_until")
    if pause_until and pause_until > now:
        return False, f"Risk pause active until {pause_until.isoformat()}"
    if pause_until and pause_until <= now:
        clear_risk_pause()

    streak = get_consecutive_losing_outcomes_4h(limit=100)
    latest_4h_outcome = get_latest_4h_outcome_timestamp()
    recent_outcome_window = now - timedelta(hours=LOSS_STREAK_LOOKBACK_HOURS)
    if (
        streak >= MAX_CONSECUTIVE_4H_LOSSES
        and latest_4h_outcome
        and latest_4h_outcome >= recent_outcome_window
    ):
        reason = f"Loss streak {streak} >= {MAX_CONSECUTIVE_4H_LOSSES}"
        set_risk_pause(LOSS_STREAK_PAUSE_HOURS, reason)
        return False, reason

    alerts_24h = count_alerts_since(datetime.utcnow() - timedelta(hours=24))
    if MAX_ALERTS_PER_DAY > 0 and alerts_24h >= MAX_ALERTS_PER_DAY:
        return False, f"Daily alert cap reached ({alerts_24h}/{MAX_ALERTS_PER_DAY})"

    return True, ""


def _compute_regime(tokens):
    if not tokens:
        return {"score": 0.0, "label": "RISK_OFF", "breadth_pct": 0.0, "avg_change_24h": 0.0}

    changes = [float(t.get("change_24h", 0) or 0) for t in tokens]
    positives = sum(1 for x in changes if x > 0)
    breadth_pct = positives / len(changes)
    avg_change = sum(changes) / len(changes)

    # Blend directional breadth and mean change into a 0-100 regime score.
    regime_score = 50.0 + (avg_change * 2.0) + ((breadth_pct - 0.5) * 40.0)
    regime_score = max(0.0, min(100.0, regime_score))

    if regime_score >= 65:
        label = "RISK_ON"
    elif regime_score >= 45:
        label = "NEUTRAL"
    else:
        label = "RISK_OFF"

    return {
        "score": regime_score,
        "label": label,
        "breadth_pct": breadth_pct,
        "avg_change_24h": avg_change,
    }


def _compute_sol_regime_proxy():
    if not ENABLE_SOL_REGIME_LAYER:
        return {
            "enabled": False,
            "available": False,
            "symbol": "SOL",
            "change_24h": 0.0,
            "change_1h": 0.0,
            "pair_address": None,
            "liquidity": 0.0,
        }

    snapshot = fetch_sol_market_proxy(SOL_REGIME_QUERY)
    if not snapshot:
        return {
            "enabled": True,
            "available": False,
            "symbol": "SOL",
            "change_24h": 0.0,
            "change_1h": 0.0,
            "pair_address": None,
            "liquidity": 0.0,
        }

    return {
        "enabled": True,
        "available": True,
        "symbol": snapshot.get("symbol") or "SOL",
        "change_24h": float(snapshot.get("change_24h", 0) or 0),
        "change_1h": float(snapshot.get("change_1h", 0) or 0),
        "pair_address": snapshot.get("pair_address"),
        "liquidity": float(snapshot.get("liquidity", 0) or 0),
    }


def _build_market_policy(regime, sol_proxy):
    policy = {
        "state": regime.get("label", "NEUTRAL"),
        "alert_threshold": float(_runtime["alert_threshold"]),
        "min_confidence_to_alert": str(_runtime["min_confidence_to_alert"]).upper(),
        "regime_min_score": float(_runtime["regime_min_score"]),
        "max_alerts_per_cycle": max(1, int(MAX_ALERTS_PER_CYCLE)),
        "max_alerts_per_day": int(MAX_ALERTS_PER_DAY),
        "alert_cooldown_hours": int(_runtime["alert_cooldown_hours"]),
        "hard_block": False,
        "hard_block_reason": "",
    }

    if not ENABLE_SOL_REGIME_LAYER:
        policy["state"] = regime.get("label", "NEUTRAL")
        try:
            from utils.market_cycle import get_recent_regime_scores, classify_phase_from_scores  # type: ignore
            _s = get_recent_regime_scores(n=50)
            policy["cycle_phase"] = classify_phase_from_scores(_s + [float(regime.get("score", 50) or 50)])
        except Exception:
            policy["cycle_phase"] = "TRANSITION"
        return policy

    sol_change_24h = float(sol_proxy.get("change_24h", 0) or 0)
    sol_change_1h = float(sol_proxy.get("change_1h", 0) or 0)
    breadth = float(regime.get("breadth_pct", 0) or 0)
    regime_score = float(regime.get("score", 0) or 0)

    if (
        sol_change_24h <= SOL_EXTREME_OFF_CHANGE_24H
        or sol_change_1h <= SOL_EXTREME_OFF_CHANGE_1H
    ):
        state = "EXTREME_RISK_OFF"
    elif sol_change_24h <= SOL_RISK_OFF_CHANGE_24H or regime.get("label") == "RISK_OFF":
        state = "RISK_OFF"
    elif (
        sol_change_24h >= SOL_RISK_ON_CHANGE_24H
        and breadth >= SOL_BREADTH_MIN_FOR_RISK_ON
        and regime_score >= 45
    ):
        state = "RISK_ON"
    else:
        state = "NEUTRAL"

    policy["state"] = state

    if state in {"RISK_OFF", "EXTREME_RISK_OFF"}:
        policy["alert_threshold"] = max(policy["alert_threshold"], float(BEARISH_ALERT_THRESHOLD))
        policy["regime_min_score"] = max(policy["regime_min_score"], float(BEARISH_REGIME_MIN_SCORE))

        bearish_conf = str(BEARISH_MIN_CONFIDENCE_TO_ALERT).upper()
        if bearish_conf not in _CONFIDENCE_ORDER:
            bearish_conf = "A"
        policy["min_confidence_to_alert"] = bearish_conf

        bearish_cycle_cap = max(1, int(BEARISH_MAX_ALERTS_PER_CYCLE))
        policy["max_alerts_per_cycle"] = min(policy["max_alerts_per_cycle"], bearish_cycle_cap)

        bearish_daily_cap = int(BEARISH_MAX_ALERTS_PER_DAY)
        if bearish_daily_cap > 0:
            if policy["max_alerts_per_day"] > 0:
                policy["max_alerts_per_day"] = min(policy["max_alerts_per_day"], bearish_daily_cap)
            else:
                policy["max_alerts_per_day"] = bearish_daily_cap

        policy["alert_cooldown_hours"] = max(policy["alert_cooldown_hours"], int(BEARISH_ALERT_COOLDOWN_HOURS))

    if state == "EXTREME_RISK_OFF" and ENABLE_EXTREME_RISK_HARD_BLOCK:
        policy["hard_block"] = True
        policy["hard_block_reason"] = (
            f"EXTREME_RISK_OFF sol24h={sol_change_24h:.2f}% sol1h={sol_change_1h:.2f}%"
        )

    # Phase 3: Determine macro market cycle phase and attach to policy.
    # This is used to tag signals/outcomes and select cycle-aware exit plans.
    try:
        from utils.market_cycle import get_recent_regime_scores, classify_phase_from_scores  # type: ignore
        _recent_scores = get_recent_regime_scores(n=50)
        _cycle_phase   = classify_phase_from_scores(_recent_scores + [regime_score])
        policy["cycle_phase"] = _cycle_phase
    except Exception:
        policy["cycle_phase"] = "TRANSITION"

    return policy


def _passes_live_push_gate(token: dict, policy: dict) -> bool:
    if not TELEGRAM_PUSH_STRONG_ONLY:
        return True

    conf = str(token.get("confidence") or "C").upper()
    min_conf = str(TELEGRAM_PUSH_MIN_CONFIDENCE or "A").upper()
    if min_conf not in _CONFIDENCE_ORDER:
        min_conf = "A"
    if _CONFIDENCE_ORDER.get(conf, 1) < _CONFIDENCE_ORDER[min_conf]:
        return False

    score = float(token.get("score", 0) or 0)
    base_threshold = float(policy.get("alert_threshold", _runtime["alert_threshold"]) or 0)
    required_score = base_threshold + max(0, int(TELEGRAM_PUSH_MIN_SCORE_DELTA))
    return score >= required_score


def _pair_age_hours(pair_created_at):
    if pair_created_at is None:
        return None
    try:
        ts = float(pair_created_at)
    except (TypeError, ValueError):
        return None
    if ts <= 0:
        return None
    # DexScreener may provide milliseconds.
    if ts > 1_000_000_000_000:
        ts = ts / 1000.0
    try:
        created = datetime.utcfromtimestamp(ts)
    except (OverflowError, OSError, ValueError):
        return None
    age = (datetime.utcnow() - created).total_seconds() / 3600.0
    if age < 0:
        return None
    return age


def _runner_narrative_label(token):
    text = f"{token.get('symbol', '')} {token.get('name', '')} {token.get('description', '')}".lower()
    hits = [kw for kw in NEW_RUNNER_NARRATIVE_KEYWORDS if kw and kw in text]
    social_links = int(token.get("social_links") or 0)
    if len(hits) >= 2 and social_links > 0:
        return "Strong", len(hits)
    if len(hits) >= 1 or social_links > 0:
        return "Developing", len(hits)
    return "Weak", len(hits)


def _runner_x_proxy_label(token):
    txns_h1 = int(token.get("txns_h1") or 0)
    social_links = int(token.get("social_links") or 0)
    boosts_active = int(token.get("boosts_active") or 0)
    if txns_h1 >= max(250, NEW_RUNNER_MIN_TXNS_H1 * 2) or (social_links > 1 and boosts_active > 0):
        return "High"
    if txns_h1 >= NEW_RUNNER_MIN_TXNS_H1:
        return "Medium"
    return "Low"


def _runner_watch_enrich(token):
    age_hours = _pair_age_hours(token.get("pair_created_at"))
    if age_hours is None or age_hours > NEW_RUNNER_MAX_AGE_HOURS:
        return None

    market_cap = token.get("market_cap")
    fdv = token.get("fdv")
    cap_value = market_cap if isinstance(market_cap, (int, float)) and market_cap > 0 else fdv
    if not isinstance(cap_value, (int, float)) or cap_value < NEW_RUNNER_MIN_MARKET_CAP:
        return None

    liquidity = float(token.get("liquidity", 0) or 0)
    volume_24h = float(token.get("volume_24h", 0) or 0)
    change_24h = float(token.get("change_24h", 0) or 0)
    txns_h1 = int(token.get("txns_h1") or 0)
    social_links = int(token.get("social_links") or 0)
    boosts_active = int(token.get("boosts_active") or 0)

    if liquidity < NEW_RUNNER_MIN_LIQUIDITY:
        return None
    if volume_24h < NEW_RUNNER_MIN_VOLUME_24H:
        return None
    if change_24h < NEW_RUNNER_MIN_CHANGE_24H:
        return None
    if txns_h1 < NEW_RUNNER_MIN_TXNS_H1:
        return None
    if NEW_RUNNER_REQUIRE_SOCIAL_LINKS and social_links <= 0:
        return None

    narrative_label, keyword_hits = _runner_narrative_label(token)
    x_proxy_label = _runner_x_proxy_label(token)

    watch_score = 0.0
    watch_score += 20.0  # first-day age condition passed
    watch_score += 20.0 if cap_value >= NEW_RUNNER_MIN_MARKET_CAP else 0.0
    watch_score += 20.0 if volume_24h >= NEW_RUNNER_MIN_VOLUME_24H else 0.0
    watch_score += 15.0 if liquidity >= NEW_RUNNER_MIN_LIQUIDITY else 0.0
    watch_score += min(15.0, (txns_h1 / max(1, NEW_RUNNER_MIN_TXNS_H1)) * 15.0)
    watch_score += min(5.0, keyword_hits * 2.5)
    watch_score += 5.0 if social_links > 0 else 0.0
    watch_score += 3.0 if boosts_active > 0 else 0.0
    watch_score = min(100.0, watch_score)
    if watch_score < NEW_RUNNER_MIN_ALERT_SCORE:
        return None

    enriched = dict(token)
    risk_plan, rotation_plan = _wallet_guidance("W3")
    enriched.update(
        {
            "age_hours": age_hours,
            "watch_score": watch_score,
            "x_proxy_label": x_proxy_label,
            "narrative_label": narrative_label,
            "wallet_fit": "W3",
            "risk_plan": risk_plan,
            "rotation_plan": rotation_plan,
            "note": "Watchlist-only alert. Early runner to monitor, not a buy signal.",
        }
    )
    return enriched


def _runner_watch_on_cooldown(symbol: str) -> bool:
    if not symbol:
        return True
    last_sent = get_last_decision_timestamp_for_symbol(
        symbol,
        ["RUNNER_WATCH_ALERT", "RUNNER_WATCH_DRY_RUN"],
    )
    if not last_sent:
        return False
    return (datetime.utcnow() - last_sent) < timedelta(hours=NEW_RUNNER_COOLDOWN_HOURS)


# ── LEGACY RECOVERY helpers ─────────────────────────────────────────────────

_REVERSAL_PATTERNS = {"falling_wedge", "double_bottom", "cup_handle", "cup_and_handle", "reversal"}


def _detect_legacy_recovery(token: dict) -> dict | None:
    """
    Returns an enriched token dict if it passes all LEGACY RECOVERY criteria,
    or None if it fails any filter.

    Criteria:
      - age >= LEGACY_RECOVERY_MIN_AGE_DAYS (default 90 days)
      - liquidity >= LEGACY_RECOVERY_MIN_LIQUIDITY (default $1M)
      - volume_24h >= LEGACY_RECOVERY_MIN_VOLUME_24H (default $500K)
      - volume spike: txns_h1 >= LEGACY_RECOVERY_VOLUME_SPIKE_MULTIPLIER × (txns_h24/24)
      - reversal-type pattern (falling wedge / double bottom / cup & handle)
        inferred from price action when no explicit pattern field present
    """
    age_hours = _pair_age_hours(token.get("pair_created_at"))
    min_age_hours = LEGACY_RECOVERY_MIN_AGE_DAYS * 24.0
    if age_hours is None or age_hours < min_age_hours:
        return None

    liquidity = float(token.get("liquidity", 0) or 0)
    if liquidity < LEGACY_RECOVERY_MIN_LIQUIDITY:
        return None

    volume_24h = float(token.get("volume_24h", 0) or 0)
    if volume_24h < LEGACY_RECOVERY_MIN_VOLUME_24H:
        return None

    # Volume spike: compare last-hour txn rate vs 24h average rate
    txns_h1 = float(token.get("txns_h1", 0) or 0)
    txns_h24 = float(token.get("txns_h24", 0) or 0)
    avg_hourly = txns_h24 / 24.0 if txns_h24 > 0 else 0.0
    has_volume_spike = avg_hourly > 0 and txns_h1 >= LEGACY_RECOVERY_VOLUME_SPIKE_MULTIPLIER * avg_hourly

    # Reversal pattern detection
    pattern_setup = token.get("pattern_setup")
    explicit_pattern = ""
    if isinstance(pattern_setup, dict):
        explicit_pattern = str(pattern_setup.get("pattern", "") or "").lower().replace(" ", "_")
    elif isinstance(pattern_setup, str):
        explicit_pattern = pattern_setup.lower().replace(" ", "_")

    has_reversal_pattern = explicit_pattern in _REVERSAL_PATTERNS

    # Infer reversal from price action when no explicit pattern:
    # A declining 24h but positive or recovering 1h suggests potential reversal base
    if not has_reversal_pattern:
        change_24h = float(token.get("change_24h", 0) or 0)
        change_1h = float(token.get("change_1h", 0) or 0)
        change_6h = float(token.get("change_6h", 0) or 0)
        # Double-bottom / falling-wedge proxy: coin was down 24h but 1h is recovering
        if -40.0 < change_24h < -5.0 and change_1h > 1.5:
            has_reversal_pattern = True
            explicit_pattern = "double_bottom"
        # Cup-and-handle proxy: modest 24h decline but 6h and 1h both recovering
        elif -20.0 < change_24h < 0 and change_6h > 0 and change_1h > 0.5:
            has_reversal_pattern = True
            explicit_pattern = "cup_handle"

    if not has_reversal_pattern:
        return None

    if not has_volume_spike:
        return None

    # Map internal pattern key to human label
    _pattern_labels = {
        "falling_wedge": "Falling Wedge",
        "double_bottom": "Double Bottom",
        "cup_handle": "Cup & Handle",
        "cup_and_handle": "Cup & Handle",
        "reversal": "Reversal",
    }
    pattern_label = _pattern_labels.get(explicit_pattern, "Reversal")
    pattern_status = "Confirmed" if txns_h1 >= avg_hourly * 3 else "Forming"
    age_days = age_hours / 24.0

    # Stamp second-leg status onto the enriched token
    leg          = token.get("leg", "UNKNOWN")
    drawdown_pct = float(token.get("drawdown_pct") or 0)
    is_second_leg = token.get("is_second_leg", False)

    return {
        **token,
        "age_days":      age_days,
        "pattern_label": pattern_label,
        "pattern_status": pattern_status,
        "leg":           leg,
        "drawdown_pct":  drawdown_pct,
        "is_second_leg": is_second_leg,
    }


def _legacy_recovery_on_cooldown(symbol: str) -> bool:
    if not symbol:
        return True
    last_sent = get_last_decision_timestamp_for_symbol(
        symbol,
        ["LEGACY_RECOVERY_ALERT", "LEGACY_RECOVERY_DRY_RUN"],
    )
    if not last_sent:
        return False
    return (datetime.utcnow() - last_sent) < timedelta(hours=LEGACY_RECOVERY_COOLDOWN_HOURS)


def _load_watchlist_state():
    global _watchlist_state
    if not _WATCHLIST_STATE_PATH.exists():
        _watchlist_state = {"statuses": {}}
        return
    try:
        raw = json.loads(_WATCHLIST_STATE_PATH.read_text())
        statuses = raw.get("statuses")
        if not isinstance(statuses, dict):
            statuses = {}
        _watchlist_state = {"statuses": {str(k).upper(): str(v) for k, v in statuses.items()}}
    except (ValueError, OSError):
        _watchlist_state = {"statuses": {}}


def _save_watchlist_state():
    try:
        _WATCHLIST_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _WATCHLIST_STATE_PATH.write_text(json.dumps(_watchlist_state, indent=2))
    except OSError:
        logging.warning("Failed to persist watchlist state at %s", _WATCHLIST_STATE_PATH)


def _watchlist_status_for_token(token: dict):
    liquidity = float(token.get("liquidity", 0) or 0)
    volume_24h = float(token.get("volume_24h", 0) or 0)
    change_1h = float(token.get("change_1h", 0) or 0)
    change_6h = float(token.get("change_6h", 0) or 0)
    change_24h = float(token.get("change_24h", 0) or 0)
    vol_to_liq = (volume_24h / liquidity) if liquidity > 0 else 0.0

    if liquidity < WATCHLIST_MIN_LIQUIDITY or volume_24h < WATCHLIST_MIN_VOLUME_24H:
        return "Illiquid", (
            f"Liquidity/volume below watch floor "
            f"(${WATCHLIST_MIN_LIQUIDITY:,.0f}/${WATCHLIST_MIN_VOLUME_24H:,.0f})."
        )

    if change_24h >= 12 and change_6h >= 4 and change_1h >= 1 and vol_to_liq >= 0.35:
        return "Momentum", "Multi-timeframe strength with sustained turnover."
    if change_24h >= 2 and change_6h <= -2 and change_1h >= 0.8 and vol_to_liq >= 0.20:
        return "Reclaim", "Short-term reclaim after pullback; buyers stepping back in."
    if change_1h <= -2.5 and change_6h <= -6:
        return "Breakdown", "Downside pressure accelerating; structure likely weakening."
    if abs(change_1h) <= 1.2 and abs(change_6h) <= 4 and abs(change_24h) <= 12:
        return "Range", "Range-bound conditions with no confirmed break."
    return "Volatile", "Mixed tape; momentum and mean-reversion signals conflict."


def _watchlist_opportunity_tags(token: dict, status: str):
    liquidity = float(token.get("liquidity", 0) or 0)
    volume_24h = float(token.get("volume_24h", 0) or 0)
    change_1h = float(token.get("change_1h", 0) or 0)
    change_24h = float(token.get("change_24h", 0) or 0)
    txns_h1 = int(token.get("txns_h1") or 0)
    vol_to_liq = (volume_24h / liquidity) if liquidity > 0 else 0.0

    upside_points = 0.0
    risk_points = 0.0
    s = str(status or "").title()

    if s == "Momentum":
        upside_points += 2.0
    elif s == "Reclaim":
        upside_points += 1.5
    elif s == "Breakdown":
        risk_points += 2.5
    elif s == "Illiquid":
        risk_points += 3.0
    elif s == "Volatile":
        risk_points += 1.5

    if change_24h >= 12:
        upside_points += 2.0
    elif change_24h >= 5:
        upside_points += 1.0
    elif change_24h <= -10:
        risk_points += 1.5

    if change_1h >= 1.0:
        upside_points += 1.0
    elif change_1h <= -2.0:
        risk_points += 1.2

    if txns_h1 >= 300:
        upside_points += 1.0
    elif txns_h1 >= 150:
        upside_points += 0.5

    if liquidity >= 750_000:
        upside_points += 1.0
    elif liquidity < 250_000:
        risk_points += 1.2

    if volume_24h >= 1_500_000:
        upside_points += 1.0

    if vol_to_liq > 2.0:
        risk_points += 1.5
    elif vol_to_liq > 1.0:
        risk_points += 1.0

    if abs(change_24h) > 35:
        risk_points += 1.0

    if upside_points >= 5.0:
        upside_label = "High"
    elif upside_points >= 3.0:
        upside_label = "Medium"
    else:
        upside_label = "Low"

    if risk_points >= 4.5:
        risk_label = "High"
    elif risk_points >= 2.5:
        risk_label = "Medium"
    else:
        risk_label = "Low"

    return upside_label, risk_label


def _wallet_fit_for_main_token(token: dict, regime: dict, policy: dict) -> str:
    score = float(token.get("score", 0) or 0)
    confidence = str(token.get("confidence", "C")).upper()
    liquidity = float(token.get("liquidity", 0) or 0)
    volume_24h = float(token.get("volume_24h", 0) or 0)
    change_24h = abs(float(token.get("change_24h", 0) or 0))
    regime_score = float(regime.get("score", 0) or 0)
    state = str(policy.get("state") or "")

    if (
        confidence == "A"
        and score >= max(88.0, float(policy.get("alert_threshold", 70)) + 6.0)
        and liquidity >= 2_000_000
        and volume_24h >= 1_200_000
        and change_24h <= 25
        and regime_score >= 55
        and state in {"RISK_ON", "NEUTRAL"}
    ):
        return "W1/W2"

    if (
        _confidence_meets_rule(confidence, "A")
        and score >= float(policy.get("alert_threshold", 70))
        and liquidity >= 700_000
        and volume_24h >= 250_000
    ):
        return "W2"

    return "W3"


def _wallet_guidance(wallet_fit: str):
    fit = str(wallet_fit or "W2").upper()
    if fit == "W1/W2":
        risk = "Tiered entries | avoid chasing >15% candles | thesis stop only"
        rotation = "Take partials into W1 staking/USDC while preserving core"
        return risk, rotation
    if fit == "W2":
        pb = _WALLET_PLAYBOOK["W2"]
        return pb["risk_plan"], pb["rotation_plan"]
    pb = _WALLET_PLAYBOOK["W3"]
    return pb["risk_plan"], pb["rotation_plan"]


def _wallet_header(wallet_id: str) -> str:
    pb = _WALLET_PLAYBOOK.get(wallet_id, _WALLET_PLAYBOOK["W2"])
    return (
        f"{wallet_id} | {pb['name']}\n"
        f"Position: {pb['position_hint']}\n"
        f"Risk: {pb['risk_plan']}\n"
        f"Rotation: {pb['rotation_plan']}"
    )


def _watchlist_on_cooldown(symbol: str) -> bool:
    if not symbol:
        return False
    last_sent = get_last_decision_timestamp_for_symbol(
        symbol,
        ["WATCHLIST_ALERT", "WATCHLIST_ALERT_DRY_RUN"],
    )
    if not last_sent:
        return False
    return (datetime.utcnow() - last_sent) < timedelta(hours=WATCHLIST_ALERT_COOLDOWN_HOURS)


def _build_watchlist_rows():
    import time as _time
    rows = []
    if not WATCHLIST_ENTRIES:
        return rows

    for entry in WATCHLIST_ENTRIES:
        configured_symbol = str(entry.get("symbol") or "").upper()
        address = str(entry.get("address") or "").strip()
        if not address:
            continue
        symbol = configured_symbol or "UNKNOWN"
        previous_status = str(_watchlist_state.get("statuses", {}).get(symbol) or "")

        _time.sleep(0.35)
        snapshot = fetch_dexscreener_token_snapshot(address)
        if not snapshot:
            risk_plan, rotation_plan = _wallet_guidance("W3")
            rows.append(
                {
                    "symbol": symbol,
                    "address": address,
                    "status": previous_status or "NoData",
                    "reason": (
                        "Live data unavailable; showing last known status."
                        if previous_status
                        else "No live DexScreener pair found."
                    ),
                    "eligible": False,
                    "has_live_data": False,
                    "wallet_fit": "W3",
                    "risk_plan": risk_plan,
                    "rotation_plan": rotation_plan,
                }
            )
            continue

        row = dict(snapshot)
        if configured_symbol and configured_symbol != "WATCH":
            row["symbol"] = configured_symbol
        status, reason = _watchlist_status_for_token(row)
        upside, risk = _watchlist_opportunity_tags(row, status)
        row["status"] = status
        row["reason"] = reason
        row["upside_potential"] = upside
        row["failure_risk"] = risk
        row["eligible"] = status != "Illiquid"
        row["has_live_data"] = True
        row["wallet_fit"] = "W3"
        risk_plan, rotation_plan = _wallet_guidance("W3")
        row["risk_plan"] = risk_plan
        row["rotation_plan"] = rotation_plan
        rows.append(row)

    return rows


async def send_watchlist_summary(context):
    rows = _build_watchlist_rows()
    if not rows:
        return
    message = format_watchlist_summary(rows)
    if DRY_RUN:
        logging.info("DRY_RUN enabled. Watchlist summary not sent.\n%s", message)
        return
    await context.bot.send_message(
        chat_id=int(TELEGRAM_CHAT_ID),
        text=message,
        parse_mode="HTML",
        disable_web_page_preview=True,
    )
    log_signal(
        {
            "symbol": "WATCHLIST",
            "decision": "WATCHLIST_SUMMARY",
            "notes": f"rows={len(rows)}",
        }
    )


async def run_watchlist_lane(context):
    if not WATCHLIST_LANE_ENABLED or not WATCHLIST_ENTRIES:
        return
    try:
        print("Running watchlist lane scan...")
        rows = _build_watchlist_rows()
        if not rows:
            print("Watchlist lane: no rows.")
            return

        rows.sort(
            key=lambda r: (
                str(r.get("status", "")) not in set(WATCHLIST_ALERT_STATUSES),
                -(float(r.get("volume_24h", 0) or 0)),
            )
        )

        alerts_sent = 0
        max_per_cycle = max(1, WATCHLIST_MAX_ALERTS_PER_CYCLE)
        no_data_count = 0

        for row in rows:
            symbol = str(row.get("symbol") or "UNKNOWN").upper()
            status = str(row.get("status") or "Unknown").title()
            has_live_data = bool(row.get("has_live_data", True))
            if not has_live_data:
                no_data_count += 1
                continue
            previous_status = str(_watchlist_state.get("statuses", {}).get(symbol) or "")
            changed = previous_status != status
            _watchlist_state.setdefault("statuses", {})[symbol] = status

            if status not in WATCHLIST_ALERT_STATUSES:
                continue
            if not row.get("eligible", False):
                continue
            if WATCHLIST_ALERT_ON_STATUS_CHANGE and not changed:
                continue
            if _watchlist_on_cooldown(symbol):
                log_signal(
                    {
                        "symbol": symbol,
                        "mint": row.get("address"),
                        "decision": "WATCHLIST_COOLDOWN_SKIP",
                        "notes": f"status={status} cooldown_h={WATCHLIST_ALERT_COOLDOWN_HOURS}",
                    }
                )
                continue
            if alerts_sent >= max_per_cycle:
                break

            msg = format_watchlist_signal(row, compact=True)
            decision = "WATCHLIST_ALERT"
            if DRY_RUN:
                decision = "WATCHLIST_ALERT_DRY_RUN"
                logging.info("DRY_RUN enabled. Watchlist alert suppressed for %s", symbol)
            elif TELEGRAM_QUIET_MODE and context.job is not None:
                decision = "WATCHLIST_ALERT_QUIET_MODE"
            else:
                await context.bot.send_message(
                    chat_id=int(TELEGRAM_CHAT_ID),
                    text=msg,
                    parse_mode="HTML",
                    reply_markup=_build_alert_keyboard(
                        symbol,
                        row.get("address"),
                        row.get("pair_address"),
                    ),
                    disable_web_page_preview=True,
                )

            log_signal(
                {
                    "symbol": symbol,
                    "mint": row.get("address"),
                    "pair_address": row.get("pair_address"),
                    "liquidity": row.get("liquidity"),
                    "volume_24h": row.get("volume_24h"),
                    "price": row.get("price"),
                    "change_24h": row.get("change_24h"),
                    "decision": decision,
                    "notes": f"status={status} reason={row.get('reason', '')}",
                }
            )
            # Track outcome for lane learning (watchlist lane)
            if OUTCOME_TRACKING_ENABLED:
                _wl_price = float(row.get("price") or 0)
                if _wl_price > 0 and "QUIET" not in decision and "COOLDOWN" not in decision:
                    try:
                        from utils.market_cycle import get_current_cycle_phase as _gcp  # type: ignore
                        _wl_cycle = _gcp()
                    except Exception:
                        _wl_cycle = "TRANSITION"
                    queue_alert_outcome({
                        "symbol": symbol,
                        "mint": row.get("address"),
                        "entry_price": _wl_price,
                        "score": 0,
                        "regime_score": 0,
                        "regime_label": status or "WATCHLIST",
                        "confidence": "C",
                        "lane": "watchlist",
                        "source": "dexscreener",
                        "cycle_phase": _wl_cycle,
                    })
            alerts_sent += 1

        _save_watchlist_state()
        if no_data_count:
            logging.info("Watchlist lane: live data unavailable for %d token(s) this cycle.", no_data_count)
        if alerts_sent == 0:
            print("Watchlist lane: no status-change alerts this cycle.")
    except Exception as exc:
        print("WATCHLIST LANE ERROR:", repr(exc))


def _format_daily_summary(summary, outcome_recap=None):
    sep = "━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    top_symbols = summary.get("top_alert_symbols", [])
    top_line = ", ".join(f"${row['symbol']}({row['alerts']})" for row in top_symbols) if top_symbols else "None"
    lines = [
        f"<b>📅 [SIGNAL]: DAILY SUMMARY</b>",
        f"<code>{sep}</code>",
        f"<code>🕐 WINDOW: 24H</code>",
        f"<code>🔍 SCANS: {_esc_html_main(str(summary['scans']))}</code>",
        f"<code>🚨 ALERTS: {_esc_html_main(str(summary['alerts']))} ({summary['alert_rate']:.1f}% rate)</code>",
        f"<code>⭐ AVG SCORE: {summary['avg_score']:.2f} | MAX: {summary['max_score']:.2f}</code>",
        f"<code>🏆 TOP: {_esc_html_main(top_line)}</code>",
        f"<code>{sep}</code>",
        f"<code>📋 RECAP (24H)</code>",
    ]
    recap_rows = outcome_recap or []
    if recap_rows:
        for idx, row in enumerate(recap_rows[:6], start=1):
            symbol = str(row.get("symbol") or "UNKNOWN").upper()
            alerts = int(row.get("alerts") or 0)
            avg_4h = row.get("avg_4h")
            n_4h = int(row.get("n_4h") or 0)
            wins_4h = int(row.get("wins_4h") or 0)
            avg_4h_text = f"{float(avg_4h):+.2f}%" if avg_4h is not None else "N/A"
            win_rate_text = f"{(wins_4h / n_4h * 100.0):.0f}%" if n_4h > 0 else "N/A"
            result_emoji = "✅" if avg_4h is not None and float(avg_4h) > 0 else "❌"
            lines.append(
                f"<code>{result_emoji} {idx}. ${_esc_html_main(symbol)} | {alerts} alerts | 4h {_esc_html_main(avg_4h_text)} | win {_esc_html_main(win_rate_text)} ({wins_4h}/{n_4h})</code>"
            )
    else:
        lines.append(f"<code>⚪ No evaluated outcomes yet.</code>")
    return "\n".join(lines)


def _format_weekly_tuning_report(report):
    sep = "━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    current = report["current"]
    recommended = report["recommended"]
    reasons = [str(r) for r in report["reasons"]]
    pf = report.get("portfolio_4h", {})
    optimizer = report.get("optimizer")

    e = _esc_html_main
    lines = [
        f"<b>📊 [SIGNAL]: WEEKLY TUNING</b>",
        f"<code>{sep}</code>",
        f"<code>🕐 WINDOW: 7D</code>",
        f"<code>🔍 SCANS: {e(str(report['scan_runs']))} | BEST: {e(str(report['scan_best']))}</code>",
        f"<code>🚨 ALERTS: {e(str(report['alerts']))} ({report['alert_rate']:.1f}% rate) | BLOCK: {report['block_rate']:.1f}%</code>",
        f"<code>⭐ SCORES P50/75/90: {report['p50_score']:.0f}/{report['p75_score']:.0f}/{report['p90_score']:.0f}</code>",
        f"<code>{sep}</code>",
        f"<code>📈 EDGE</code>",
        f"<code>  1H:  {report['avg_return_1h']:+.2f}% | win {report['winrate_1h']:.0f}% | n={report['outcomes_1h_count']}</code>",
        f"<code>  4H:  {report['avg_return_4h']:+.2f}% | win {report['winrate_4h']:.0f}% | n={report['outcomes_4h_count']}</code>",
        f"<code>  24H: {report['avg_return_24h']:+.2f}% | win {report['winrate_24h']:.0f}% | n={report['outcomes_24h_count']}</code>",
        f"<code>🧮 SIM 4H: {pf.get('trades', 0)} trades | exp {pf.get('expectancy_pct', 0):.2f}% | DD {pf.get('max_drawdown_pct', 0):.2f}%</code>",
        f"<code>{sep}</code>",
        f"<code>⚙️ SETTINGS</code>",
        f"<code>  CURRENT → thr {e(str(current['alert_threshold']))} | reg {e(str(current['regime_min_score']))} | conf {e(str(current['min_confidence_to_alert']))}</code>",
        f"<code>  NEW     → thr {e(str(recommended['alert_threshold']))} | reg {e(str(recommended['regime_min_score']))} | conf {e(str(recommended['min_confidence_to_alert']))}</code>",
    ]
    if optimizer:
        lines.append(
            f"<code>🔧 OPT: thr {e(str(optimizer['alert_threshold']))} reg {e(str(optimizer['regime_min_score']))} conf {e(str(optimizer['min_confidence_to_alert']))} n={e(str(optimizer['samples']))}</code>"
        )
    lines.append(f"<code>{sep}</code>")
    lines.append(f"<code>📝 RATIONALE</code>")
    for reason in (reasons[:4] if reasons else ["No rationale available"]):
        lines.append(f"<code>  • {e(reason)}</code>")
    return "\n".join(lines)


async def send_daily_summary(context):
    summary = get_performance_summary(lookback_hours=24)
    recap = get_alert_outcome_recap(lookback_hours=24, limit=8)
    message = _format_daily_summary(summary, outcome_recap=recap)
    if DRY_RUN:
        logging.info("DRY_RUN enabled. Daily summary not sent.\n%s", message)
        return
    await context.bot.send_message(
        chat_id=int(TELEGRAM_CHAT_ID),
        text=message,
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


async def send_weekly_tuning_report(context):
    report = get_weekly_tuning_report(
        lookback_days=WEEKLY_TUNING_LOOKBACK_DAYS,
        current_alert_threshold=ALERT_THRESHOLD,
        current_regime_min_score=REGIME_MIN_SCORE,
        current_min_confidence_to_alert=MIN_CONFIDENCE_TO_ALERT,
        min_outcomes_4h=WEEKLY_TUNING_MIN_OUTCOMES_4H,
    )
    message = _format_weekly_tuning_report(report)
    if DRY_RUN:
        logging.info("DRY_RUN enabled. Weekly tuning report not sent.\n%s", message)
        return
    await context.bot.send_message(
        chat_id=int(TELEGRAM_CHAT_ID),
        text=message,
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


def _update_symbol_control_from_outcomes(symbol: str):
    if not SYMBOL_CONTROL_ENABLED or not symbol:
        return
    stats = get_symbol_outcome_stats(symbol, lookback_days=30)
    returns_4h = stats.get("returns_4h", [])
    returns_24h = stats.get("returns_24h", [])
    avg_24h = float(stats.get("avg_24h", 0) or 0)

    if len(returns_24h) >= SYMBOL_BLACKLIST_MIN_SAMPLES and avg_24h <= SYMBOL_BLACKLIST_AVG_24H_PCT:
        set_symbol_control(
            symbol=symbol,
            control_type="BLACKLIST",
            hours=SYMBOL_BLACKLIST_HOURS,
            reason=f"24h avg {avg_24h:.2f}% <= {SYMBOL_BLACKLIST_AVG_24H_PCT:.2f}%",
        )
        return

    trigger = max(1, SYMBOL_LOSS_STREAK_TRIGGER)
    if len(returns_4h) >= trigger and all(ret < 0 for ret in returns_4h[:trigger]):
        set_symbol_control(
            symbol=symbol,
            control_type="COOLDOWN",
            hours=SYMBOL_COOLDOWN_HOURS,
            reason=f"{trigger} consecutive negative 4h outcomes",
        )


async def run_outcome_evaluator(context):
    if not OUTCOME_TRACKING_ENABLED:
        return

    rows = get_pending_alert_outcomes(limit=OUTCOME_EVAL_BATCH_SIZE)
    if not rows:
        return

    # OPTIMIZATION: Fetch market data once instead of per-token API calls
    # This dramatically reduces BirdEye API pressure
    market_tokens = fetch_market_data() or []
    price_by_mint = {
        str(t.get("address") or ""): float(t.get("price", 0) or 0)
        for t in market_tokens
        if t.get("address") and t.get("price")
    }

    now = datetime.utcnow()
    updated = 0
    touched_symbols = set()
    for row in rows:
        outcome_id = int(row["id"])
        mint = row.get("mint")
        entry_price = float(row.get("entry_price") or 0)
        if not mint or entry_price <= 0:
            mark_alert_outcome_complete(outcome_id)
            continue

        try:
            created = datetime.fromisoformat(row["created_ts_utc"])
        except (TypeError, ValueError):
            mark_alert_outcome_error(outcome_id, "invalid_created_ts")
            continue

        age = now - created
        due_horizons = []
        if row.get("return_1h_pct") is None and age >= timedelta(hours=1):
            due_horizons.append(1)
        if row.get("return_4h_pct") is None and age >= timedelta(hours=4):
            due_horizons.append(4)
        if row.get("return_24h_pct") is None and age >= timedelta(hours=24):
            due_horizons.append(24)
        if not due_horizons:
            continue

        # Try to get price from market data first (batched, efficient)
        current_price = price_by_mint.get(mint)

        # Fallback to individual fetch only if not in market data
        if current_price is None or current_price <= 0:
            current_price = fetch_birdeye_price(mint)
            if current_price is None or float(current_price) <= 0:
                mark_alert_outcome_error(outcome_id, "price_unavailable")
                continue

        ret_pct = ((float(current_price) - entry_price) / entry_price) * 100.0
        for horizon in due_horizons:
            update_alert_outcome_horizon(outcome_id, horizon, ret_pct)
            updated += 1
            if row.get("symbol"):
                touched_symbols.add(str(row.get("symbol")))

    for symbol in touched_symbols:
        _update_symbol_control_from_outcomes(symbol)

    if updated:
        logging.info("Outcome evaluator updated %d horizon result(s)", updated)


async def _reject_unauthorized(update: Update):
    msg = update.effective_message
    if msg:
        await msg.reply_text("Unauthorized chat.")


def _current_mode_text():
    sell_rules = _sell_style_rules()
    return (
        f"Mode: {_mode()}\n"
        f"Risk profile: {_risk_style_label()} ({_normalize_risk_style(_runtime.get('risk_style'))})\n"
        f"Scan interval: {_runtime['scan_interval_seconds']}s\n"
        f"Threshold: {_runtime['alert_threshold']} | Confidence: {_runtime['min_confidence_to_alert']}\n"
        f"Regime min: {_runtime['regime_min_score']}\n"
        f"Min liq/vol24h: ${int(_runtime['min_liquidity']):,} / ${int(_runtime['min_volume_24h']):,}\n"
        f"Cooldown: {_runtime['alert_cooldown_hours']}h\n"
        f"SOL regime layer: {'ON' if ENABLE_SOL_REGIME_LAYER else 'OFF'}\n"
        f"New runner watch: {'ON' if NEW_RUNNER_WATCH_ENABLED else 'OFF'}\n"
        f"Legacy recovery scan: {'ON' if LEGACY_RECOVERY_ENABLED else 'OFF'} "
        f"(age>={LEGACY_RECOVERY_MIN_AGE_DAYS:.0f}d, liq>${LEGACY_RECOVERY_MIN_LIQUIDITY/1e6:.1f}M, vol>${LEGACY_RECOVERY_MIN_VOLUME_24H/1e3:.0f}K)\n"
        f"Manual watchlist lane: {'ON' if WATCHLIST_LANE_ENABLED and bool(WATCHLIST_ENTRIES) else 'OFF'}\n"
        f"Analysis bulletin: {'ON' if GOOD_BUY_BULLETIN_ENABLED else 'OFF'} ({GOOD_BUY_BULLETIN_INTERVAL_SECONDS}s)\n"
        f"Quiet mode: {'ON' if TELEGRAM_QUIET_MODE else 'OFF'}\n"
        f"Live push gate: {'STRONG-ONLY' if TELEGRAM_PUSH_STRONG_ONLY else 'OFF'} "
        f"(+{TELEGRAM_PUSH_MIN_SCORE_DELTA}, conf>={TELEGRAM_PUSH_MIN_CONFIDENCE})\n"
        f"Sell/exit scanner: {'ON' if SELL_ALERTS_ENABLED else 'OFF'} "
        f"(max/cycle={sell_rules['max_per_cycle']}, cooldown={sell_rules['cooldown_hours']}h)"
    )


def _fmt_usd_compact_main(value):
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


def _fmt_pct_main(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "N/A"
    sign = "+" if number > 0 else ""
    return f"{sign}{number:.1f}%"


def _fmt_int_main(value):
    try:
        return f"{int(float(value)):,}"
    except (TypeError, ValueError):
        return "N/A"


def _fmt_price_precise_main(value):
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
    return _fmt_usd_compact_main(p)


def _esc_html_main(value):
    return html.escape(str(value))


def _render_pre_main(rows):
    lines = [str(row or "") for row in rows]
    if not lines:
        return ""
    panel_width = _PANEL_WIDTH_MAIN
    wrapped = []
    for line in lines:
        wrapped.extend(_wrap_text_main(line, panel_width))
    return "\n".join(f"<code>{_esc_html_main(line)}</code>" for line in wrapped)


_PANEL_WIDTH_MAIN = 42


def _trim_text_main(value, max_len):
    text = str(value or "")
    if max_len <= 0:
        return text
    return text


def _wrap_text_main(value, max_len):
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


def _kv_main(label, value, width: int = _PANEL_WIDTH_MAIN):
    key = str(label or "").upper()[:9]
    line = f"{key:<9} | {value}"
    return _trim_text_main(line, width)


def _header_block_main(tag: str, rows=None, width: int = _PANEL_WIDTH_MAIN) -> str:
    header_rows = [
        f"<b>{_esc_html_main(tag)}</b>",
        f"<code>{'-' * min(30, width)}</code>",
    ]
    for row in (rows or []):
        text = _trim_text_main(str(row or ""), width)
        header_rows.append(f"<code>{_esc_html_main(text)}</code>")
    return "\n".join(header_rows)


def _priority_from_score_main(score):
    try:
        s = float(score or 0)
    except (TypeError, ValueError):
        return "P3"
    if s >= 90:
        return "P1"
    if s >= 80:
        return "P2"
    return "P3"


def _good_buy_reason(token: dict) -> str:
    change_24h = float(token.get("change_24h", 0) or 0)
    liquidity = float(token.get("liquidity", 0) or 0)
    volume_24h = float(token.get("volume_24h", 0) or 0)
    vol_to_liq = (volume_24h / liquidity) if liquidity > 0 else 0.0
    if change_24h >= 12 and vol_to_liq >= 0.6:
        return "Strong trend + strong participation"
    if change_24h >= 5 and vol_to_liq >= 0.35:
        return "Momentum confirmed + healthy turnover"
    if change_24h >= 0:
        return "Constructive structure + acceptable liquidity"
    return "Pullback setup with still-valid liquidity"


def _sell_style_rules(style: str | None = None) -> dict:
    risk_style = _normalize_risk_style(style or _runtime.get("risk_style"))
    base = {
        "style": risk_style,
        "max_per_cycle": max(1, int(SELL_ALERT_MAX_PER_CYCLE)),
        "cooldown_hours": max(1, int(SELL_ALERT_COOLDOWN_HOURS)),
        "hype_fade_max_change_1h": float(SELL_HYPE_FADE_MAX_CHANGE_1H),
        "hype_fade_max_change_6h": float(SELL_HYPE_FADE_MAX_CHANGE_6H),
        "hype_fade_max_change_24h": float(SELL_HYPE_FADE_MAX_CHANGE_24H),
        "hype_fade_max_vol_to_liq": float(SELL_HYPE_FADE_MAX_VOL_TO_LIQ),
        "consolidation_min_change_24h": float(SELL_CONSOLIDATION_MIN_CHANGE_24H),
        "consolidation_max_abs_change_1h": float(SELL_CONSOLIDATION_MAX_ABS_CHANGE_1H),
        "consolidation_max_abs_change_6h": float(SELL_CONSOLIDATION_MAX_ABS_CHANGE_6H),
        "consolidation_max_vol_to_liq": float(SELL_CONSOLIDATION_MAX_VOL_TO_LIQ),
        "consolidation_max_txns_h1": int(SELL_CONSOLIDATION_MAX_TXNS_H1),
    }

    if risk_style == "capital":
        base.update(
            {
                "max_per_cycle": max(1, base["max_per_cycle"] + 1),
                "cooldown_hours": max(1, int(round(base["cooldown_hours"] * 0.75))),
                "hype_fade_max_change_1h": base["hype_fade_max_change_1h"] * 0.6,
                "hype_fade_max_change_6h": base["hype_fade_max_change_6h"] * 0.6,
                "hype_fade_max_change_24h": base["hype_fade_max_change_24h"] * 0.6,
                "hype_fade_max_vol_to_liq": base["hype_fade_max_vol_to_liq"] * 1.4,
                "consolidation_min_change_24h": base["consolidation_min_change_24h"] * 0.7,
                "consolidation_max_abs_change_1h": base["consolidation_max_abs_change_1h"] * 1.35,
                "consolidation_max_abs_change_6h": base["consolidation_max_abs_change_6h"] * 1.3,
                "consolidation_max_vol_to_liq": base["consolidation_max_vol_to_liq"] * 1.3,
                "consolidation_max_txns_h1": int(base["consolidation_max_txns_h1"] * 1.35),
            }
        )
    elif risk_style == "sniper":
        base.update(
            {
                "max_per_cycle": max(1, base["max_per_cycle"] - 1),
                "cooldown_hours": max(1, int(round(base["cooldown_hours"] * 1.25))),
                "hype_fade_max_change_1h": base["hype_fade_max_change_1h"] * 1.6,
                "hype_fade_max_change_6h": base["hype_fade_max_change_6h"] * 1.6,
                "hype_fade_max_change_24h": base["hype_fade_max_change_24h"] * 1.6,
                "hype_fade_max_vol_to_liq": base["hype_fade_max_vol_to_liq"] * 0.75,
                "consolidation_min_change_24h": base["consolidation_min_change_24h"] * 1.35,
                "consolidation_max_abs_change_1h": base["consolidation_max_abs_change_1h"] * 0.75,
                "consolidation_max_abs_change_6h": base["consolidation_max_abs_change_6h"] * 0.75,
                "consolidation_max_vol_to_liq": base["consolidation_max_vol_to_liq"] * 0.75,
                "consolidation_max_txns_h1": int(base["consolidation_max_txns_h1"] * 0.75),
            }
        )

    return base


def _sell_signal_on_cooldown(symbol: str, cooldown_hours: int | float | None = None) -> bool:
    if not symbol:
        return True
    last_sent = get_last_decision_timestamp_for_symbol(
        symbol,
        [
            "SELL_ALERT_HYPE_FADE",
            "SELL_ALERT_CONSOLIDATION",
            "SELL_ALERT_DRY_RUN",
        ],
    )
    if not last_sent:
        return False
    effective_cooldown = cooldown_hours if cooldown_hours is not None else SELL_ALERT_COOLDOWN_HOURS
    return (datetime.utcnow() - last_sent) < timedelta(hours=max(1, effective_cooldown))


def _detect_sell_signal(token: dict, rules: dict | None = None):
    """
    Detect exit signals using real market structure and flow analysis.
    Prioritized by severity: structure break > liquidity drain > holder exodus > hype fade > consolidation
    """
    symbol = str(token.get("symbol") or "").upper()
    if not symbol or symbol in _SELL_ALERT_EXCLUDED_SYMBOLS:
        return None

    liquidity = float(token.get("liquidity", 0) or 0)
    volume_24h = float(token.get("volume_24h", 0) or 0)
    if liquidity <= 0 or volume_24h <= 0:
        return None
    vol_to_liq = volume_24h / liquidity
    rules = rules or _sell_style_rules()

    change_1h = token.get("change_1h") or token.get("priceChange1hPercent")
    change_4h = token.get("change_4h") or token.get("priceChange4hPercent")
    change_6h = token.get("change_6h") or token.get("priceChange6hPercent")
    change_24h = token.get("change_24h")
    c1 = float(change_1h) if isinstance(change_1h, (int, float)) else None
    c4 = float(change_4h) if isinstance(change_4h, (int, float)) else None
    c6 = float(change_6h) if isinstance(change_6h, (int, float)) else None
    c24 = float(change_24h) if isinstance(change_24h, (int, float)) else 0.0
    txns_h1 = int(float(token.get("txns_h1") or 0))

    # Get holder momentum data
    uw_1h_change = token.get("uniqueWallet1hChangePercent")
    uw_4h_change = token.get("uniqueWallet4hChangePercent")

    # ─────────────────────────────────────────────────────────────
    # 1. STRUCTURE BREAK (highest priority)
    # Price breaking down hard across multiple timeframes = structural failure
    # ─────────────────────────────────────────────────────────────
    if c1 is not None and c4 is not None:
        if c1 < -8 and c4 < -15:
            return {
                "type": "STRUCTURE_BREAK",
                "title": "Structure Break",
                "action": "EXIT NOW - structure failed",
                "style": rules["style"],
                "reason": (
                    f"Multi-timeframe breakdown: 1h {c1:+.1f}%, 4h {c4:+.1f}%. "
                    f"Price support has failed."
                ),
            }

    # Sharp dump with accelerating downside = cascade forming
    if c1 is not None and c6 is not None:
        if c1 < -10 and c6 < -20 and c1 < c6 / 2:
            return {
                "type": "STRUCTURE_BREAK",
                "title": "Structure Break",
                "action": "EXIT NOW - cascade forming",
                "style": rules["style"],
                "reason": (
                    f"Accelerating dump: 1h {c1:+.1f}%, 6h {c6:+.1f}%. "
                    f"Downside momentum accelerating."
                ),
            }

    # ─────────────────────────────────────────────────────────────
    # 2. LIQUIDITY DRAIN
    # Volume/liquidity ratio collapsing = can't exit without slippage
    # ─────────────────────────────────────────────────────────────
    if vol_to_liq < 0.05:  # Volume dropped to 5% of liquidity = dried up
        return {
            "type": "LIQUIDITY_DRAIN",
            "title": "Liquidity Drain",
            "action": "EXIT ASAP - market drying up",
            "style": rules["style"],
            "reason": (
                f"Volume collapsed to {vol_to_liq:.3f}x liquidity. "
                f"Market depth evaporating - exit before slippage spikes."
            ),
        }

    # ─────────────────────────────────────────────────────────────
    # 3. HOLDER EXODUS
    # Unique wallets rapidly exiting = distribution phase
    # ─────────────────────────────────────────────────────────────
    if uw_1h_change is not None and uw_4h_change is not None:
        if uw_1h_change < -25 and uw_4h_change < -20:
            return {
                "type": "HOLDER_EXODUS",
                "title": "Holder Exodus",
                "action": "EXIT - smart money leaving",
                "style": rules["style"],
                "reason": (
                    f"Holder exodus: 1h wallets {uw_1h_change:+.1f}%, 4h {uw_4h_change:+.1f}%. "
                    f"Smart money is exiting en masse."
                ),
            }

    # Holders exiting while price still holding = distribution before dump
    if uw_1h_change is not None and c1 is not None:
        if uw_1h_change < -20 and c1 > -5:
            return {
                "type": "HOLDER_EXODUS",
                "title": "Holder Exodus",
                "action": "EXIT - distribution phase",
                "style": rules["style"],
                "reason": (
                    f"Holders exiting ({uw_1h_change:+.1f}%) while price still {c1:+.1f}%. "
                    f"Distribution phase - dump incoming."
                ),
            }

    # ─────────────────────────────────────────────────────────────
    # 4. HYPE FADE (original logic, now lower priority)
    # Momentum rolled over + participation faded
    # ─────────────────────────────────────────────────────────────
    if c1 is not None and c6 is not None:
        if (
            c1 <= rules["hype_fade_max_change_1h"]
            and c6 <= rules["hype_fade_max_change_6h"]
            and vol_to_liq <= rules["hype_fade_max_vol_to_liq"]
        ) or (
            c24 <= rules["hype_fade_max_change_24h"]
            and vol_to_liq <= rules["hype_fade_max_vol_to_liq"]
        ):
            return {
                "type": "HYPE_FADE",
                "title": "Hype Fade",
                "action": "Bad buy / de-risk now",
                "style": rules["style"],
                "reason": (
                    f"Momentum rolled over (1h {c1:+.1f}%, 6h {c6:+.1f}%) and participation faded "
                    f"(vol/liq {vol_to_liq:.2f})."
                ),
            }

    # ─────────────────────────────────────────────────────────────
    # 5. CONSOLIDATION (lowest priority - not always bad)
    # Token ran but is stalling with cooling flow
    # ─────────────────────────────────────────────────────────────
    if c1 is not None and c6 is not None:
        if (
            c24 >= rules["consolidation_min_change_24h"]
            and abs(c1) <= rules["consolidation_max_abs_change_1h"]
            and abs(c6) <= rules["consolidation_max_abs_change_6h"]
            and vol_to_liq <= rules["consolidation_max_vol_to_liq"]
            and txns_h1 <= rules["consolidation_max_txns_h1"]
        ):
            return {
                "type": "CONSOLIDATION",
                "title": "Post-Run Consolidation",
                "action": "Take profit / tighten risk",
                "style": rules["style"],
                "reason": (
                    f"Token ran ({c24:+.1f}% 24h) but is stalling (1h {c1:+.1f}%, 6h {c6:+.1f}%) "
                    f"with cooling flow (txns 1h {txns_h1}, vol/liq {vol_to_liq:.2f})."
                ),
            }

    return None


def _format_sell_alert_message(token: dict, signal: dict, compact: bool = True) -> str:
    import html as _html
    def _esc(v):
        return _html.escape(str(v))
    sep = "━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    thin = "┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄"
    symbol = str(token.get("symbol") or "UNKNOWN").upper()
    market_cap = token.get("market_cap")
    fdv = token.get("fdv")
    cap_value = market_cap if isinstance(market_cap, (int, float)) and market_cap > 0 else fdv
    price = float(token.get("price") or 0)
    move_24 = float(token.get("change_24h", 0) or 0)
    move_1h = float(token.get("change_1h", 0) or 0)
    liquidity = float(token.get("liquidity") or 0)
    volume_24h = float(token.get("volume_24h") or 0)
    holders = token.get("holders")

    action = str(signal.get("action") or "De-risk")
    reason = str(signal.get("reason") or "Flow deterioration detected.")
    signal_title = str(signal.get("title") or "Exit Warning")

    # Priority by severity of drawdown
    if move_24 <= -15 or move_1h <= -8:
        priority = "P1"
        priority_label = "URGENT — EXIT NOW"
        priority_emoji = "🔴"
    elif move_24 <= -8 or move_1h <= -5:
        priority = "P2"
        priority_label = "DE-RISK — REDUCE SIZE"
        priority_emoji = "🟠"
    else:
        priority = "P3"
        priority_label = "CAUTION — WATCH CLOSELY"
        priority_emoji = "🟡"

    price_display = _fmt_price_precise_main(price) if price > 0 else "N/A"
    cap_display = _fmt_usd_compact_main(cap_value)
    liq_display = _fmt_usd_compact_main(liquidity)
    vol_display = _fmt_usd_compact_main(volume_24h)
    holders_display = f"{int(float(holders)):,}" if holders else "—"

    chg_24_display = f"{move_24:+.2f}%"
    chg_1h_display = f"{move_1h:+.2f}%"
    chg_24_arrow = "▼" if move_24 < 0 else "▲"
    chg_1h_arrow = "▼" if move_1h < 0 else "▲"

    lines = [
        f"<b>🚨 EXIT ALERT — ${_esc(symbol)}</b>",
        f"<code>{sep}</code>",
        f"<code>  {priority_emoji} {_esc(priority_label)}</code>",
        f"<code>{thin}</code>",
        f"<code>  Price   {_esc(price_display)}</code>",
        f"<code>  24h    {chg_24_arrow} {_esc(chg_24_display):<10}  1h   {chg_1h_arrow} {_esc(chg_1h_display)}</code>",
        f"<code>  Cap    {_esc(cap_display):<10}  Liq   {_esc(liq_display)}</code>",
        f"<code>  Vol24h {_esc(vol_display):<10}  Holders {_esc(holders_display)}</code>",
        f"<code>{sep}</code>",
        f"<code>  Trigger  {_esc(signal_title)}</code>",
        f"<code>  Action   {_esc(action.upper())}</code>",
        f"<code>  Reason   {_esc(reason)}</code>",
        f"<code>{thin}</code>",
        f"<code>  Re-entry if: volume recovers + price reclaims</code>",
        f"<code>{sep}</code>",
    ]
    return "\n".join(lines)


def _normalize_market_tier(raw: str | None) -> str:
    value = str(raw or "balanced").strip().lower()
    aliases = {
        "c": "conservative",
        "cons": "conservative",
        "conservative": "conservative",
        "b": "balanced",
        "bal": "balanced",
        "balanced": "balanced",
        "a": "aggressive",
        "agg": "aggressive",
        "aggressive": "aggressive",
    }
    return aliases.get(value, "balanced")


def _stricter_confidence(min_a: str, min_b: str) -> str:
    a = str(min_a or "B").upper()
    b = str(min_b or "B").upper()
    if a not in _CONFIDENCE_ORDER:
        a = "B"
    if b not in _CONFIDENCE_ORDER:
        b = "B"
    return a if _CONFIDENCE_ORDER[a] >= _CONFIDENCE_ORDER[b] else b


def _analyze_market_now(limit_good: int = 5, limit_bad: int = 5, tier: str = "balanced") -> dict:
    tier_key = _normalize_market_tier(tier)
    tier_rule = _market_tier_rule(tier_key)
    risk_style = _normalize_risk_style(_runtime.get("risk_style"))
    tokens = fetch_market_data() or []
    regime = _compute_regime(tokens)
    sol_proxy = _compute_sol_regime_proxy()
    policy = _build_market_policy(regime, sol_proxy)

    if not tokens:
        return {
            "tokens": 0,
            "policy": policy,
            "regime": regime,
            "good": [],
            "bad": [],
            "regime_blocked": False,
            "tier": tier_key,
            "tier_rule": tier_rule,
            "risk_style": risk_style,
        }

    regime_min = float(policy["regime_min_score"])
    regime_blocked = ENABLE_REGIME_GATE and regime["score"] < regime_min

    scored = []
    for token in tokens:
        if not token.get("address"):
            continue
        # Enrich with BirdEye overview data (unique wallets, social, etc.)
        token = _enrich_token_for_scoring(token)
        token["engine_profile"] = _mode()
        score, breakdown = calculate_token_score_with_breakdown(token)
        confidence = _confidence_from_score(score)
        token["score"] = score
        token["confidence"] = confidence
        # Stamp mcap_tier onto token so it flows through to signals/alerts
        token["mcap_tier"] = breakdown.get("mcap_tier", "UNKNOWN")
        scored.append(token)

    scored.sort(key=lambda t: (float(t.get("score", 0) or 0), float(t.get("volume_24h", 0) or 0)), reverse=True)

    good = []
    bad = []
    required_conf = _stricter_confidence(policy["min_confidence_to_alert"], tier_rule["min_confidence"])
    required_score = float(policy["alert_threshold"]) + float(tier_rule["min_score_delta"])
    required_score = max(0.0, min(100.0, required_score))
    wallet_target = "W3" if tier_key == "aggressive" else ("W1/W2" if tier_key == "conservative" else "W2")

    for token in scored:
        symbol = token.get("symbol") or "UNKNOWN"
        reasons = []
        liquidity = float(token.get("liquidity", 0) or 0)
        volume_24h = float(token.get("volume_24h", 0) or 0)
        change_24h = float(token.get("change_24h", 0) or 0)
        vol_to_liq = (volume_24h / liquidity) if liquidity > 0 else 0.0

        token["wallet_fit"] = wallet_target
        risk_plan, rotation_plan = _wallet_guidance(wallet_target)
        token["risk_plan"] = risk_plan
        token["rotation_plan"] = rotation_plan

        if not _passes_quality_filters(token):
            reasons.append("fails base quality filters")
        if not _passes_execution_quality_filters(token):
            reasons.append("fails execution quality filters")
        if not _passes_tactical_filters(token):
            reasons.append("fails tactical confirmation")
        if liquidity < float(tier_rule["min_liquidity"]):
            reasons.append(f"liq<${int(tier_rule['min_liquidity']):,}")
        if volume_24h < float(tier_rule["min_volume_24h"]):
            reasons.append(f"vol24h<${int(tier_rule['min_volume_24h']):,}")
        if change_24h < float(tier_rule["min_change_24h"]):
            reasons.append(f"24h<{tier_rule['min_change_24h']:+.1f}%")
        if abs(change_24h) > float(tier_rule["max_abs_change_24h"]):
            reasons.append(f"|24h|>{tier_rule['max_abs_change_24h']:.0f}%")
        if vol_to_liq > float(tier_rule["max_vol_to_liq"]):
            reasons.append("turnover overheated")
        if float(token.get("score", 0) or 0) < required_score:
            reasons.append(f"score<{required_score:.1f}")
        if not _confidence_meets_rule(token.get("confidence", "C"), required_conf):
            reasons.append(f"confidence<{required_conf}")
        if _is_symbol_on_cooldown(symbol, cooldown_hours=policy["alert_cooldown_hours"]):
            reasons.append("cooldown active")
        if _apply_symbol_controls(symbol):
            reasons.append("symbol control active")
        if regime_blocked and not bool(tier_rule["allow_risk_off"]):
            reasons.append("regime gate blocked")
        if policy["state"] in {"RISK_OFF", "EXTREME_RISK_OFF"} and not bool(tier_rule["allow_risk_off"]):
            reasons.append("risk-off state")

        if not reasons:
            good.append(token)
        else:
            bad.append((token, reasons))

    return {
        "tokens": len(scored),
        "policy": policy,
        "regime": regime,
        "good": good[:max(1, limit_good)],
        "bad": bad[:max(1, limit_bad)],
        "regime_blocked": regime_blocked,
        "tier": tier_key,
        "tier_rule": tier_rule,
        "risk_style": risk_style,
    }


def _format_market_now_message(snapshot: dict, mode: str = "both") -> str:
    policy = snapshot["policy"]
    regime = snapshot["regime"]
    tier_rule = snapshot.get("tier_rule") or {}
    tier_label = tier_rule.get("label") or str(snapshot.get("tier") or "Balanced").title()
    risk_style = _normalize_risk_style(snapshot.get("risk_style"))
    regime_score = float(regime.get("score", 0) or 0)
    regime_floor = float(policy.get("regime_min_score", 0) or 0)
    signal_action = "BUY" if regime_score >= regime_floor else "WAIT"
    action_emoji = "🟢" if signal_action == "BUY" else "🔴"
    sep = "━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    profile_label = f"{_mode().upper()} | {_risk_style_label(risk_style)} | {tier_label}"

    lines = [
        f"<b>📊 MARKET NOW</b>",
        f"<code>{sep}</code>",
        f"<code>📋 PROFILE: {_esc_html_main(profile_label)}</code>",
        f"<code>📡 REGIME: {_esc_html_main(regime['label'])} ({regime_score:.1f}/{regime_floor:.1f})</code>",
        f"<code>{action_emoji} STATUS: {signal_action} | thr={int(policy['alert_threshold'])} conf&gt;={_esc_html_main(policy['min_confidence_to_alert'])}</code>",
        f"<code>🌐 UNIVERSE: {snapshot['tokens']} tokens | liq&gt;={_esc_html_main(_fmt_usd_compact_main(tier_rule.get('min_liquidity')))} vol&gt;={_esc_html_main(_fmt_usd_compact_main(tier_rule.get('min_volume_24h')))}</code>",
        f"<code>{sep}</code>",
    ]

    if int(snapshot.get("tokens", 0)) == 0:
        lines.append(f"<code>⚠️  Data feed unavailable — try again in 1-2 min</code>")
        return "\n".join(lines)

    if mode in {"both", "good"}:
        lines.append(f"<b>✅ GOOD BUYS</b>")
        if snapshot["good"]:
            for idx, token in enumerate(snapshot["good"], start=1):
                sym = str(token.get("symbol", "UNKNOWN")).upper()
                score = float(token.get("score", 0) or 0)
                conf = str(token.get("confidence", "C")).upper()
                chg = float(token.get("change_24h", 0) or 0)
                chg_str = f"{chg:+.1f}%"
                liq = _fmt_usd_compact_main(token.get("liquidity"))
                cap_val = token.get("market_cap") or token.get("fdv")
                cap = _fmt_usd_compact_main(cap_val) if cap_val else "N/A"
                fit = str(token.get("wallet_fit", "W2"))
                lines.append(
                    f"<code>{idx}. ${_esc_html_main(sym):<9} {score:.0f}pts {_esc_html_main(conf)} {_esc_html_main(chg_str):>7}</code>"
                )
                lines.append(
                    f"<code>   liq {_esc_html_main(liq)} | cap {_esc_html_main(cap)} | fit {_esc_html_main(fit)}</code>"
                )
        else:
            if snapshot.get("regime_blocked"):
                lines.append(f"<code>🚫 Regime gate blocking — score {regime_score:.1f} &lt; floor {regime_floor:.1f}</code>")
            else:
                lines.append(f"<code>⏳ None cleared gates — market is selective</code>")

    if mode in {"both", "bad"}:
        if mode == "both":
            lines.append(f"<code>{sep}</code>")
        lines.append(f"<b>❌ AVOID NOW</b>")
        if snapshot["bad"]:
            for idx, item in enumerate(snapshot["bad"], start=1):
                token, reasons = item
                sym = str(token.get("symbol", "UNKNOWN")).upper()
                chg = float(token.get("change_24h", 0) or 0)
                reason_text = "; ".join(reasons[:2])
                lines.append(
                    f"<code>{idx}. ${_esc_html_main(sym):<9} {chg:+.1f}% — {_esc_html_main(reason_text)}</code>"
                )
        else:
            lines.append(f"<code>✔️  No obvious avoids in current universe</code>")

    lines.append(f"<code>{sep}</code>")
    lines.append(f"<i>Use /goodbuy all for full 3-tier breakdown</i>")
    return "\n".join(lines)


def _format_market_now_multi_tier(mode: str = "both") -> str:
    sections = []
    for tier in ("conservative", "balanced", "aggressive"):
        snap = _analyze_market_now(limit_good=3, limit_bad=3, tier=tier)
        sections.append(_format_market_now_message(snap, mode=mode))
    return "\n\n".join(sections)


def _tier_arg_or_default(args, default: str = "balanced"):
    if not args:
        return default
    raw = str(args[0]).strip().lower()
    if raw == "all":
        return "all"
    return _normalize_market_tier(raw)


def _format_wallet2_message(mode: str = "both") -> str:
    base = _analyze_market_now(limit_good=5, limit_bad=5, tier="balanced")
    state = str((base.get("policy") or {}).get("state") or "")
    snap = base
    if state in {"RISK_OFF", "EXTREME_RISK_OFF"}:
        snap = _analyze_market_now(limit_good=5, limit_bad=5, tier="conservative")
    return _wallet_header("W2") + "\n\n" + _format_market_now_message(snap, mode=mode)


def _format_wallet3_message(mode: str = "both") -> str:
    snap = _analyze_market_now(limit_good=5, limit_bad=5, tier="aggressive")
    rows = _build_watchlist_rows()
    live_rows = [r for r in rows if bool(r.get("has_live_data", False))]
    good_watch = [
        r for r in live_rows
        if str(r.get("status") or "").title() in {"Momentum", "Reclaim"}
        and str(r.get("failure_risk") or "").title() != "High"
    ][:4]
    bad_watch = [
        r for r in rows
        if str(r.get("status") or "").title() in {"Breakdown", "Illiquid", "NoData"}
        or str(r.get("failure_risk") or "").title() == "High"
    ][:4]

    lines = [_wallet_header("W3"), ""]
    if mode in {"both", "good"}:
        lines.append("W3 WATCHLIST OPPORTUNITIES")
        if good_watch:
            for idx, r in enumerate(good_watch, start=1):
                lines.append(
                    f"{idx}. ${str(r.get('symbol') or 'UNKNOWN').upper()} | {r.get('status')} | "
                    f"upside {r.get('upside_potential','N/A')} | risk {r.get('failure_risk','N/A')} | "
                    f"24h {float(r.get('change_24h',0) or 0):+.1f}%"
                )
        else:
            lines.append("No watchlist setups with favorable asymmetry right now.")
        lines.append("")

    if mode in {"both", "bad"}:
        lines.append("W3 WATCHLIST AVOID/CAUTION")
        if bad_watch:
            for idx, r in enumerate(bad_watch, start=1):
                status = str(r.get("status") or "Unknown")
                reason = str(r.get("reason") or "").strip()
                if reason:
                    reason = f" | {reason}"
                lines.append(
                    f"{idx}. ${str(r.get('symbol') or 'UNKNOWN').upper()} | {status} | "
                    f"risk {r.get('failure_risk','N/A')}{reason}"
                )
        else:
            lines.append("No major watchlist danger flags right now.")
        lines.append("")

    if mode in {"both", "good"}:
        lines.append(_format_market_now_message(snap, mode="good"))
    elif mode == "bad":
        lines.append(_format_market_now_message(snap, mode="bad"))
    else:
        lines.append(_format_market_now_message(snap, mode="both"))
    return "\n".join(lines)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        await _reject_unauthorized(update)
        return
    SEP = "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    msg = (
        f"<b>[HELP]</b>\n"
        f"<code>{SEP}</code>\n"
        f"<b>🧭 COMMAND CENTER</b>\n"
        f"<code>{SEP}</code>\n"
        f"<b>CORE</b>\n"
        f"<code>{SEP}</code>\n"
        f"<code>/help               Show this menu</code>\n"
        f"<code>/status             Engine health snapshot</code>\n"
        f"<code>/scan               Full scan snapshot</code>\n"
        f"<code>/performance        24h/7d performance panel</code>\n"
        f"<code>/mode               strategic|tactical</code>\n"
        f"<code>/riskprofile        capital|balanced|sniper</code>\n"
        f"<code>/risk               Risk governor state</code>\n"
        f"<code>/pause [hours]      Pause alerts</code>\n"
        f"<code>/resume             Clear pause</code>\n"
        f"<code>{SEP}</code>\n"
        f"<b>SIGNALS</b>\n"
        f"<code>{SEP}</code>\n"
        f"<code>/marketnow [tier]   Good+bad now</code>\n"
        f"<code>/goodbuy &lt;CA&gt;       Good Buy evaluator</code>\n"
        f"<code>/scout &lt;CA&gt;         Runner scout evaluator</code>\n"
        f"<code>/badbuy [tier]      Bad buys now</code>\n"
        f"<code>/conviction &lt;CA&gt;    Conviction v1 card</code>\n"
        f"<code>/digest             Send setup digest now</code>\n"
        f"<code>/execdigest         Send execution digest now</code>\n"
        f"<code>/recap              End-of-day summary card</code>\n"
        f"<code>/snapshot           Full briefing card on demand</code>\n"
        f"<code>/news               Daily crypto news digest</code>\n"
        f"<code>{SEP}</code>\n"
        f"<b>PORTFOLIO</b>\n"
        f"<code>{SEP}</code>\n"
        f"<code>/menu               Open Simple Mode UI</code>\n"
        f"<code>/portfolio          Portfolio summary card</code>\n"
        f"<code>/positions          Tracked open positions</code>\n"
        f"<code>/pnl                Live unrealized PnL</code>\n"
        f"<code>/buy &lt;sym/mint&gt;     Start tracked position</code>\n"
        f"<code>/sold &lt;sym/mint&gt;    Close tracked position</code>\n"
        f"<code>{SEP}</code>\n"
        f"<b>WATCH / RESEARCH</b>\n"
        f"<code>{SEP}</code>\n"
        f"<code>/watchlist [query]  Top 20 / narrative view</code>\n"
        f"<code>/watchlist scan     Run watchlist lane now</code>\n"
        f"<code>/watchlistsummary   Send watchlist summary</code>\n"
        f"<code>/runnerwatch        Run first-day runner scan</code>\n"
        f"<code>/mindshare [symbol] Regime + guardrails</code>\n"
        f"<code>/rules              Guardrail rules</code>\n"
        f"<code>/cooldowns          Active cooldowns &amp; mutes</code>\n"
        f"<code>{SEP}</code>\n"
        f"<b>WALLET ROUTING</b>\n"
        f"<code>{SEP}</code>\n"
        f"<code>/wallet2now         Mid-term good buys</code>\n"
        f"<code>/wallet2bad         Mid-term avoids</code>\n"
        f"<code>/wallet3now         Short-term opportunities</code>\n"
        f"<code>/wallet3bad         Short-term danger list</code>\n"
        f"<code>/walletplan         Wallet allocation playbook</code>\n"
        f"<code>{SEP}</code>\n"
        f"<b>⚡ SOL LEVERAGE</b>\n"
        f"<code>{SEP}</code>\n"
        f"<code>/lev                Full position dashboard</code>\n"
        f"<code>/levstatus          Quick one-liner status</code>\n"
        f"<code>/levrec             DCA leverage recommendation</code>\n"
        f"<code>/whatif             PnL at price targets</code>\n"
        f"<code>/pricezones         SOL price zone analysis</code>\n"
        f"<code>/dca [amt] [lev]    Log DCA entry / view tracker</code>\n"
        f"<code>{SEP}</code>\n"
        f"<code>Auto jobs: board (6h) + daily recap + scanners</code>"
    )
    await update.effective_message.reply_text(msg, parse_mode="HTML")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        await _reject_unauthorized(update)
        return
    health = get_engine_health_snapshot()
    perf = get_performance_summary(lookback_hours=24)
    pause = get_risk_pause_state()
    latest = get_latest_engine_event() or {}
    blocker = (
        f"{latest.get('decision')} | {latest.get('notes', '')}"
        if latest.get("decision")
        else "N/A"
    )
    sep = "━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    legacy_status = "ON" if LEGACY_RECOVERY_ENABLED else "OFF"
    lines = [
        f"<b>🤖 [STATUS]: ENGINE</b>",
        f"<code>{sep}</code>",
        f"<code>{_current_mode_text()}</code>",
        f"<code>{sep}</code>",
        f"<code>🕐 Last scan:   {_fmt_dt(health.get('last_scan_run'))}</code>",
        f"<code>📢 Last alert:  {_fmt_dt(health.get('last_alert'))}</code>",
        f"<code>📊 24h stats:   {perf['scans']} scans / {perf['alerts']} alerts ({perf['alert_rate']:.1f}%)</code>",
        f"<code>⏸  Pause until: {_fmt_dt(pause.get('pause_until'))}</code>",
        f"<code>{sep}</code>",
        f"<code>🔎 LEGACY RECOVERY: {legacy_status}</code>",
        f"<code>   Age ≥{LEGACY_RECOVERY_MIN_AGE_DAYS:.0f}d | Liq ≥${LEGACY_RECOVERY_MIN_LIQUIDITY/1e6:.1f}M | Vol ≥${LEGACY_RECOVERY_MIN_VOLUME_24H/1e3:.0f}K</code>",
        f"<code>{sep}</code>",
        f"<code>⚡ Latest event: {html.escape(str(blocker))}</code>",
    ]
    await update.effective_message.reply_text(
        "\n".join(lines),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


async def cmd_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        await _reject_unauthorized(update)
        return
    if not context.args:
        await update.effective_message.reply_text(
            "Usage: /mode strategic or /mode tactical\n\n"
            f"{_current_mode_text()}"
        )
        return

    mode = context.args[0].strip().lower()
    if mode not in _MODE_PRESETS:
        await update.effective_message.reply_text("Invalid mode. Use: strategic or tactical")
        return

    _apply_mode(mode)
    _persist_mode(mode)
    _reschedule_run_engine_jobs(context.application)
    log_signal(
        {
            "symbol": "ENGINE",
            "decision": "MODE_SWITCH",
            "notes": f"telegram mode switch -> {mode}",
        }
    )
    await update.effective_message.reply_text(
        "Mode switched and persisted.\n\n"
        f"{_current_mode_text()}"
    )


async def cmd_riskprofile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        await _reject_unauthorized(update)
        return

    current = _normalize_risk_style(_runtime.get("risk_style"))
    if not context.args:
        await update.effective_message.reply_text(
            "Usage: /riskprofile capital | balanced | sniper\n\n"
            f"Current: {_risk_style_label(current)} ({current})\n"
            f"{_risk_style_description(current)}"
        )
        return

    raw = str(context.args[0]).strip().lower()
    resolved = _RISK_STYLE_ALIASES.get(raw)
    if not resolved:
        await update.effective_message.reply_text(
            "Invalid risk profile. Use: capital, balanced, or sniper."
        )
        return

    _runtime["risk_style"] = resolved
    _set_env_values({"RISK_STYLE": resolved})
    log_signal(
        {
            "symbol": "ENGINE",
            "decision": "RISK_STYLE_SWITCH",
            "notes": f"telegram risk style switch -> {resolved}",
        }
    )
    await update.effective_message.reply_text(
        "Risk profile updated and persisted.\n\n"
        f"{_current_mode_text()}\n"
        f"Style detail: {_risk_style_description(resolved)}"
    )


async def cmd_pause(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        await _reject_unauthorized(update)
        return
    hours = 6
    if context.args:
        try:
            hours = max(1, int(context.args[0]))
        except ValueError:
            pass
    set_risk_pause(hours, "manual_telegram_pause")
    await update.effective_message.reply_text(f"Alerts paused for {hours}h.")


async def cmd_resume(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        await _reject_unauthorized(update)
        return
    clear_risk_pause()
    await update.effective_message.reply_text("Alerts resumed (pause cleared).")


async def cmd_risk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        await _reject_unauthorized(update)
        return
    pause = get_risk_pause_state()
    streak = get_consecutive_losing_outcomes_4h(limit=100)
    alerts_24h = count_alerts_since(datetime.utcnow() - timedelta(hours=24))
    await update.effective_message.reply_text(
        "Risk Panel\n"
        f"Governor: {'ON' if ENABLE_RISK_GOVERNOR else 'OFF'}\n"
        f"Alerts 24h: {alerts_24h}/{MAX_ALERTS_PER_DAY}\n"
        f"Consecutive losing 4h outcomes: {streak}/{MAX_CONSECUTIVE_4H_LOSSES}\n"
        f"Pause until: {_fmt_dt(pause.get('pause_until'))}\n"
        f"Pause reason: {pause.get('reason') or 'N/A'}"
    )


async def cmd_performance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        await _reject_unauthorized(update)
        return
    perf = get_performance_summary(lookback_hours=24)
    sim = get_portfolio_simulation_metrics(lookback_days=7, horizon_hours=4)
    queue = get_outcome_queue_stats()
    await update.effective_message.reply_text(
        "Performance Panel\n"
        f"24h scans/alerts: {perf['scans']}/{perf['alerts']} ({perf['alert_rate']:.1f}%)\n"
        f"Avg/Max score: {perf['avg_score']:.2f}/{perf['max_score']:.2f}\n"
        f"Sim 7d@4h: trades={sim['trades']} win={sim['win_rate_pct']:.1f}% "
        f"exp={sim['expectancy_pct']:.2f}% dd={sim['max_drawdown_pct']:.2f}%\n"
        f"Outcome queue: pending={queue['pending']} complete={queue['complete']}"
    )


def _format_week_report(report: dict) -> str:
    """Format /week command output — premium card style."""
    e = _esc_html_main
    sep = "━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    thin = "┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄"

    pf = report.get("portfolio_4h", {})
    current = report["current"]
    recommended = report["recommended"]
    reasons = report.get("reasons", [])

    # Score bar for 4h win rate
    wr4 = report.get("winrate_4h", 0)
    bar_filled = int(round(wr4 / 10))
    bar_filled = max(0, min(10, bar_filled))
    wr_bar = "█" * bar_filled + "░" * (10 - bar_filled)

    # Equity multiplier label
    equity = pf.get("equity_end", 1.0)
    if equity >= 1.5:
        eq_emoji = "🚀"
    elif equity >= 1.1:
        eq_emoji = "📈"
    elif equity >= 0.9:
        eq_emoji = "➡️"
    else:
        eq_emoji = "📉"

    # Win rate emoji
    if wr4 >= 60:
        wr_emoji = "🟢"
    elif wr4 >= 45:
        wr_emoji = "🟡"
    else:
        wr_emoji = "🔴"

    # Settings change indicator
    thr_changed = recommended["alert_threshold"] != current["alert_threshold"]
    reg_changed = recommended["regime_min_score"] != current["regime_min_score"]
    conf_changed = recommended["min_confidence_to_alert"] != current["min_confidence_to_alert"]
    settings_changed = thr_changed or reg_changed or conf_changed
    settings_emoji = "⚡" if settings_changed else "✅"

    lines = [
        f"<b>📊 WEEKLY REPORT</b>",
        f"<code>{sep}</code>",
        f"<code>Window: 7 days  ·  Scans: {e(str(report['scan_runs']))}  ·  Alerts: {e(str(report['alerts']))}</code>",
        f"<code>Alert rate {report['alert_rate']:.1f}%  ·  Regime blocks {report['block_rate']:.1f}%</code>",
        f"<code>{thin}</code>",
        f"<code>📈 EDGE  (n outcomes)</code>",
        f"<code>  1H   {report['avg_return_1h']:+.2f}%  win {report['winrate_1h']:.0f}%  n={report['outcomes_1h_count']}</code>",
        f"<code>  4H   {report['avg_return_4h']:+.2f}%  win {report['winrate_4h']:.0f}%  n={report['outcomes_4h_count']}  {wr_emoji}</code>",
        f"<code>  24H  {report['avg_return_24h']:+.2f}%  win {report['winrate_24h']:.0f}%  n={report['outcomes_24h_count']}</code>",
        f"<code>{thin}</code>",
        f"<code>🧮 SIMULATION  (4H horizon)</code>",
        f"<code>  Win rate   {wr_bar} {wr4:.0f}%</code>",
        f"<code>  Trades {pf.get('trades', 0)}  ·  Expect {pf.get('expectancy_pct', 0):+.2f}%  ·  DD {pf.get('max_drawdown_pct', 0):.1f}%</code>",
        f"<code>  Equity  {eq_emoji} {equity:.3f}x  (payoff {pf.get('payoff_ratio', 0):.2f})</code>",
        f"<code>{thin}</code>",
        f"<code>⭐ SCORE DIST  P50/75/90: {report['p50_score']:.0f}/{report['p75_score']:.0f}/{report['p90_score']:.0f}</code>",
        f"<code>{sep}</code>",
        f"<code>{settings_emoji} SETTINGS</code>",
        f"<code>  Now  →  thr {e(str(current['alert_threshold']))}  reg {e(str(current['regime_min_score']))}  conf {e(str(current['min_confidence_to_alert']))}</code>",
        f"<code>  Rec  →  thr {e(str(recommended['alert_threshold']))}  reg {e(str(recommended['regime_min_score']))}  conf {e(str(recommended['min_confidence_to_alert']))}</code>",
    ]
    if settings_changed:
        lines.append(f"<code>  ↑ Changes suggested — apply via /mode or config</code>")

    lines.append(f"<code>{thin}</code>")
    lines.append(f"<code>📝 RATIONALE</code>")
    for r in (reasons[:3] if reasons else ["No rationale available"]):
        lines.append(f"<code>  · {e(str(r)[:90])}</code>")
    lines.append(f"<code>{sep}</code>")
    return "\n".join(lines)


async def cmd_week(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Weekly performance report — /week"""
    if not _is_authorized(update):
        await _reject_unauthorized(update)
        return
    await update.effective_message.reply_text("⏳ Building weekly report…")
    try:
        report = get_weekly_tuning_report(
            lookback_days=7,
            current_alert_threshold=int(ALERT_THRESHOLD),
            current_regime_min_score=int(REGIME_MIN_SCORE),
            current_min_confidence_to_alert=str(MIN_CONFIDENCE_TO_ALERT),
        )
        msg = _format_week_report(report)
        await update.effective_message.reply_text(msg, parse_mode="HTML", disable_web_page_preview=True)
    except Exception as exc:
        logging.exception("cmd_week error: %s", exc)
        await update.effective_message.reply_text(f"⚠️ Week report error: {exc}")


def _format_journal_report(rows: list, lookback_days: int) -> str:
    """Format /journal command output — closed trade review card."""
    e = _esc_html_main
    sep = "━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    thin = "┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄"

    if not rows:
        return (
            f"<b>📓 JOURNAL</b>\n"
            f"<code>{sep}</code>\n"
            f"<code>No closed trades in the last {lookback_days}d.</code>\n"
            f"<code>Use /buy to track entries, /sold to close them.</code>\n"
            f"<code>{sep}</code>"
        )

    wins, losses, total_pnl = 0, 0, 0.0
    r_multiples = []
    for row in rows:
        pnl = row.get("pnl_pct") or 0.0
        total_pnl += pnl
        if pnl > 0:
            wins += 1
        else:
            losses += 1
        rm = row.get("r_multiple")
        if rm is not None:
            r_multiples.append(float(rm))

    n = len(rows)
    win_rate = (wins / n * 100) if n else 0
    avg_r = (sum(r_multiples) / len(r_multiples)) if r_multiples else 0.0
    avg_pnl = total_pnl / n if n else 0.0

    # Summary bar
    bar_filled = int(round(win_rate / 10))
    bar_filled = max(0, min(10, bar_filled))
    wr_bar = "█" * bar_filled + "░" * (10 - bar_filled)
    wr_emoji = "🟢" if win_rate >= 60 else ("🟡" if win_rate >= 45 else "🔴")
    pnl_emoji = "📈" if total_pnl > 0 else "📉"

    lines = [
        f"<b>📓 JOURNAL  ·  Last {lookback_days}d</b>",
        f"<code>{sep}</code>",
        f"<code>Trades {n}  ·  W {wins}  L {losses}  ·  {wr_emoji}</code>",
        f"<code>Win rate  {wr_bar}  {win_rate:.0f}%</code>",
        f"<code>Avg PnL {avg_pnl:+.2f}%  ·  Total {total_pnl:+.2f}%  {pnl_emoji}</code>",
    ]
    if r_multiples:
        lines.append(f"<code>Avg R    {avg_r:+.2f}R  ·  n={len(r_multiples)}</code>")
    lines.append(f"<code>{sep}</code>")
    lines.append(f"<code>TRADES</code>")

    for row in rows[:12]:  # cap at 12 to avoid message limits
        symbol = str(row.get("symbol") or "?").upper()
        entry = row.get("entry_price") or 0
        exit_p = row.get("exit_price")
        pnl = row.get("pnl_pct") or 0.0
        rm = row.get("r_multiple")
        closed_ts = str(row.get("closed_ts_utc") or "")[:10]
        pnl_str = f"{pnl:+.1f}%" if pnl else "—"
        rm_str = f"{rm:+.2f}R" if rm is not None else "—"
        exit_str = f"{exit_p:.6g}" if exit_p else "—"
        result_emoji = "✅" if pnl > 0 else "❌"
        lines.append(
            f"<code>  {result_emoji} ${e(symbol):<8}  {pnl_str:<7}  {rm_str:<7}  {closed_ts}</code>"
        )
        lines.append(
            f"<code>     entry {entry:.6g}  →  exit {exit_str}</code>"
        )

    if len(rows) > 12:
        lines.append(f"<code>  … +{len(rows) - 12} more trades not shown</code>")

    lines.append(f"<code>{sep}</code>")
    return "\n".join(lines)


async def cmd_journal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Closed trade review — /journal [days]"""
    if not _is_authorized(update):
        await _reject_unauthorized(update)
        return

    lookback_days = 30
    if context.args:
        try:
            lookback_days = max(1, min(365, int(context.args[0])))
        except (ValueError, TypeError):
            pass

    try:
        cutoff_iso = (datetime.utcnow() - timedelta(days=lookback_days)).isoformat()
        from utils.db import get_conn as _get_conn
        with _get_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT symbol, entry_price, exit_price, pnl_pct, r_multiple,
                       opened_ts_utc, closed_ts_utc, notes
                FROM trades
                WHERE status = 'CLOSED'
                  AND closed_ts_utc >= ?
                ORDER BY closed_ts_utc DESC
                LIMIT 50
                """,
                (cutoff_iso,),
            )
            rows = [dict(r) for r in cur.fetchall()]

        msg = _format_journal_report(rows, lookback_days)
        await update.effective_message.reply_text(msg, parse_mode="HTML", disable_web_page_preview=True)
    except Exception as exc:
        logging.exception("cmd_journal error: %s", exc)
        await update.effective_message.reply_text(f"⚠️ Journal error: {exc}")


async def cmd_buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        await _reject_unauthorized(update)
        return
    if not context.args:
        await update.effective_message.reply_text(
            "Usage: /buy <SYMBOL_or_MINT> [entry_price]\n"
            "Example: /buy PUMP 0.0042"
        )
        return

    symbol, mint = _parse_symbol_or_mint(context.args[0])
    if not symbol and not mint:
        await update.effective_message.reply_text("Invalid symbol/mint. Example: /buy PUMP or /buy <mint>")
        return

    entry_override = None
    if len(context.args) >= 2:
        entry_override = _try_float(context.args[1])
        if entry_override is None or entry_override <= 0:
            await update.effective_message.reply_text("Invalid entry price. Example: /buy PUMP 0.0042")
            return

    token = _resolve_token_for_tracking(symbol=symbol, mint=mint)
    if token:
        symbol = symbol or str(token.get("symbol") or "").upper() or None
        mint = mint or token.get("address")
        pair_address = token.get("pair_address")
        live_price = _try_float(token.get("price"))
    else:
        pair_address = None
        live_price = None

    if not symbol and mint:
        symbol = str(mint)[:10].upper()
    if not symbol:
        await update.effective_message.reply_text("Could not resolve symbol for tracking. Try /buy <SYMBOL> <entry_price>.")
        return

    entry_price = entry_override if entry_override is not None else (live_price if live_price and live_price > 0 else 0.0)
    result = open_manual_position(
        symbol=symbol,
        mint=mint,
        pair_address=pair_address,
        entry_price=entry_price,
        stop_price=(entry_price * 0.9 if entry_price > 0 else 0.0),
        notes="manual_telegram_buy",
    )
    position = result.get("position") or {}
    entry_out = _try_float(position.get("entry_price"))
    mint_out = str(position.get("mint") or mint or "")

    if result.get("created"):
        log_signal(
            {
                "symbol": symbol,
                "mint": mint_out or None,
                "price": entry_out,
                "decision": "TRACK_POSITION_OPENED",
                "notes": "manual_telegram_buy",
            }
        )
        entry_text = f"{entry_out:.8f}" if entry_out and entry_out > 0 else "N/A"
        lines = [
            "Position tracked.",
            f"Symbol: ${symbol}",
            f"Entry: {entry_text}",
        ]
        if mint_out:
            lines.append(f"Mint: {mint_out}")
        lines.append("Sell alerts are now enabled for this position only.")
        await update.effective_message.reply_text("\n".join(lines))
        return

    log_signal(
        {
            "symbol": symbol,
            "mint": mint_out or None,
            "price": entry_out,
            "decision": "TRACK_POSITION_ALREADY_OPEN",
            "notes": "manual_telegram_buy_duplicate",
        }
    )
    opened_ts = position.get("opened_ts_utc") or "N/A"
    entry_text = f"{entry_out:.8f}" if entry_out and entry_out > 0 else "N/A"
    await update.effective_message.reply_text(
        "Position already tracked.\n"
        f"Symbol: ${symbol}\n"
        f"Entry: {entry_text}\n"
        f"Opened: {opened_ts}\n"
        "Use /sold <SYMBOL> when you exit."
    )


async def cmd_sold(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        await _reject_unauthorized(update)
        return
    if not context.args:
        await update.effective_message.reply_text(
            "Usage: /sold <SYMBOL_or_MINT> [exit_price]\n"
            "Example: /sold PUMP 0.0051"
        )
        return

    symbol, mint = _parse_symbol_or_mint(context.args[0])
    if not symbol and not mint:
        await update.effective_message.reply_text("Invalid symbol/mint. Example: /sold PUMP")
        return

    exit_price = None
    if len(context.args) >= 2:
        exit_price = _try_float(context.args[1])
        if exit_price is None or exit_price <= 0:
            await update.effective_message.reply_text("Invalid exit price. Example: /sold PUMP 0.0051")
            return

    closed = close_manual_position(
        symbol=symbol,
        mint=mint,
        exit_price=exit_price,
        notes="manual_telegram_sold",
    )
    label = symbol or (str(mint)[:10].upper() if mint else "UNKNOWN")
    if closed <= 0:
        await update.effective_message.reply_text(f"No open tracked position found for {label}.")
        return

    log_signal(
        {
            "symbol": symbol or label,
            "mint": mint,
            "price": exit_price,
            "decision": "TRACK_POSITION_CLOSED",
            "notes": f"manual_telegram_sold count={closed}",
        }
    )
    await update.effective_message.reply_text(
        f"Closed {closed} tracked position(s) for {label}.\n"
        "Sell alerts for this token are now disabled unless you /buy it again."
    )


async def cmd_positions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        await _reject_unauthorized(update)
        return
    rows = get_open_positions(limit=30)
    if not rows:
        await update.effective_message.reply_text(
            "No open tracked positions.\n"
            "Sell alerts are currently paused because nothing is marked as bought.\n"
            "Use /buy <SYMBOL> [entry_price]."
        )
        return

    lines = [
        "Tracked Open Positions (sell alerts only for these):",
    ]
    for idx, row in enumerate(rows, start=1):
        symbol = str(row.get("symbol") or "UNKNOWN").upper()
        entry = _try_float(row.get("entry_price"))
        entry_text = f"{entry:.8f}" if entry and entry > 0 else "N/A"
        opened = str(row.get("opened_ts_utc") or "N/A")
        lines.append(f"{idx}. ${symbol} | entry {entry_text} | opened {opened}")
    await update.effective_message.reply_text("\n".join(lines))


# ── /pnl ─────────────────────────────────────────────────────────────────────

async def cmd_pnl(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Live unrealized PnL for all open tracked positions."""
    if not _is_authorized(update):
        await _reject_unauthorized(update)
        return

    rows = get_open_positions(limit=20)
    if not rows:
        await update.effective_message.reply_text(
            "No open tracked positions. Use /buy <SYMBOL> [entry_price] to start tracking."
        )
        return

    sep = "━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    lines = [
        f"<b>💼 OPEN POSITION PnL</b>",
        f"<code>{sep}</code>",
    ]

    total_invested = 0.0
    total_value = 0.0
    live_count = 0
    no_data = []

    for idx, row in enumerate(rows, start=1):
        symbol = str(row.get("symbol") or "UNKNOWN").upper()
        mint   = str(row.get("mint") or "")
        entry  = _try_float(row.get("entry_price"))

        # Fetch live price via DexScreener snapshot
        live_price = None
        if mint:
            try:
                snap = fetch_dexscreener_token_snapshot(mint)
                if snap:
                    live_price = _try_float(snap.get("price"))
            except Exception:
                pass

        if not live_price or not entry or entry <= 0:
            no_data.append(symbol)
            lines.append(f"<code>{idx}. ${_esc_html_main(symbol):<10} entry — | price N/A</code>")
            continue

        pct    = ((live_price - entry) / entry) * 100.0
        pnl_emoji = "🟢" if pct >= 0 else "🔴"
        pct_str   = f"{pct:+.1f}%"

        lines.append(
            f"<code>{idx}. ${_esc_html_main(symbol):<10} {pnl_emoji} {pct_str:>7}</code>"
        )
        lines.append(
            f"<code>   entry ${entry:.6g}  now ${live_price:.6g}</code>"
        )
        live_count += 1

    lines.append(f"<code>{sep}</code>")
    if live_count == 0 and no_data:
        lines.append(f"<code>⚠️  Live price unavailable — make sure mint addresses are tracked</code>")
    elif no_data:
        lines.append(f"<code>⚠️  No data: {', '.join(['$' + s for s in no_data[:4]])}</code>")
    lines.append(f"<i>Prices from DexScreener. Use /positions for full details.</i>")

    await update.effective_message.reply_text(
        "\n".join(lines),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


# ── /cooldowns ────────────────────────────────────────────────────────────────

async def cmd_cooldowns(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show all symbols currently on alert cooldown or blacklist."""
    if not _is_authorized(update):
        await _reject_unauthorized(update)
        return

    # Pull all active symbol controls from DB
    from utils.db import get_conn as _get_db_conn
    now = datetime.utcnow()
    rows = []
    try:
        with _get_db_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT symbol, cooldown_until_utc, blacklist_until_utc, reason
                FROM symbol_controls
                WHERE (cooldown_until_utc IS NOT NULL AND cooldown_until_utc > ?)
                   OR (blacklist_until_utc IS NOT NULL AND blacklist_until_utc > ?)
                ORDER BY updated_ts_utc DESC
                """,
                (now.isoformat(), now.isoformat()),
            )
            rows = [dict(r) for r in cur.fetchall()]
    except Exception as exc:
        logging.warning("cmd_cooldowns DB error: %s", exc)

    # Also check which symbols are on alert-cooldown via recent ALERT signals
    # (these are time-based, not DB-stored — show last alert time if recent)
    recent_alerts = []
    try:
        with _get_db_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT symbol, MAX(ts_utc) AS last_alert
                FROM signals
                WHERE decision IN ('ALERT', 'RUNNER_WATCH_ALERT', 'LEGACY_RECOVERY_ALERT', 'WATCHLIST_ALERT')
                  AND ts_utc > ?
                GROUP BY symbol
                ORDER BY last_alert DESC
                LIMIT 20
                """,
                ((now - timedelta(hours=48)).isoformat(),),
            )
            recent_alerts = [dict(r) for r in cur.fetchall()]
    except Exception as exc:
        logging.warning("cmd_cooldowns recent_alerts error: %s", exc)

    sep = "━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    lines = [
        f"<b>🔇 ACTIVE COOLDOWNS</b>",
        f"<code>{sep}</code>",
    ]

    if rows:
        lines.append(f"<b>🔒 Symbol Controls</b>")
        for row in rows:
            sym = str(row.get("symbol") or "?").upper()
            reason = str(row.get("reason") or "manual").replace("manual_telegram_mute", "Muted via bot")
            blacklist_until = row.get("blacklist_until_utc")
            cooldown_until  = row.get("cooldown_until_utc")
            if blacklist_until:
                try:
                    until_dt = datetime.fromisoformat(blacklist_until)
                    hrs_left = max(0, (until_dt - now).total_seconds() / 3600)
                    lines.append(f"<code>🚫 ${_esc_html_main(sym):<10} blacklist  {hrs_left:.1f}h left</code>")
                except Exception:
                    lines.append(f"<code>🚫 ${_esc_html_main(sym):<10} blacklist  (unknown)</code>")
            elif cooldown_until:
                try:
                    until_dt = datetime.fromisoformat(cooldown_until)
                    hrs_left = max(0, (until_dt - now).total_seconds() / 3600)
                    lines.append(f"<code>⏳ ${_esc_html_main(sym):<10} cooldown   {hrs_left:.1f}h left</code>")
                except Exception:
                    lines.append(f"<code>⏳ ${_esc_html_main(sym):<10} cooldown   (unknown)</code>")
            lines.append(f"<code>   reason: {_esc_html_main(reason[:40])}</code>")
        lines.append(f"<code>{sep}</code>")
    else:
        lines.append(f"<code>No symbol controls active</code>")
        lines.append(f"<code>{sep}</code>")

    cooldown_hours = int(_runtime.get("alert_cooldown_hours", 6))
    if recent_alerts:
        lines.append(f"<b>⏱ Recent Alerts (cooldown: {cooldown_hours}h)</b>")
        for row in recent_alerts:
            sym = str(row.get("symbol") or "?").upper()
            last_ts = row.get("last_alert", "")
            try:
                last_dt = datetime.fromisoformat(last_ts)
                hrs_ago = max(0, (now - last_dt).total_seconds() / 3600)
                still_cd = hrs_ago < cooldown_hours
                cd_icon = "🔴" if still_cd else "🟢"
                cd_label = f"{hrs_ago:.1f}h ago"
                lines.append(f"<code>{cd_icon} ${_esc_html_main(sym):<10} last alert {cd_label}</code>")
            except Exception:
                lines.append(f"<code>⏱ ${_esc_html_main(sym):<10} {_esc_html_main(last_ts[:16])}</code>")
        lines.append(f"<code>{sep}</code>")
    else:
        lines.append(f"<code>No recent alerts in last 48h</code>")
        lines.append(f"<code>{sep}</code>")

    lines.append(f"<i>🔴 = still cooling down  🟢 = cooldown expired</i>")

    await update.effective_message.reply_text(
        "\n".join(lines),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


# ── /snapshot ────────────────────────────────────────────────────────────────

async def cmd_snapshot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """On-demand full briefing card: regime + top picks + SOL position + fear & greed."""
    if not _is_authorized(update):
        await _reject_unauthorized(update)
        return

    try:
        sep = "━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        from datetime import datetime as _dt, timezone as _tz
        now_str = _dt.now(_tz.utc).strftime("%Y-%m-%d  %H:%M UTC")

        # ── Market regime ──────────────────────────────────────
        snap = _analyze_market_now(limit_good=3, limit_bad=0, tier="balanced")
        regime       = snap.get("regime") or {}
        policy       = snap.get("policy") or {}
        regime_score = float(regime.get("score", 0) or 0)
        regime_floor = float(policy.get("regime_min_score", 0) or 0)
        regime_label = str(regime.get("label") or "N/A")
        signal_action = "BUY" if regime_score >= regime_floor else "WAIT"
        action_emoji  = "🟢" if signal_action == "BUY" else "🔴"
        state         = str(policy.get("state") or "N/A")

        lines = [
            f"<b>⚡ SNAPSHOT</b>",
            f"<code>{sep}</code>",
            f"<code>🕐 {now_str}</code>",
            f"<code>{sep}</code>",
            f"<b>📡 MARKET REGIME</b>",
            f"<code>{action_emoji} {_esc_html_main(signal_action)} | {_esc_html_main(state)}</code>",
            f"<code>Regime: {_esc_html_main(regime_label)} ({regime_score:.1f}/{regime_floor:.1f})</code>",
        ]

        # ── Fear & Greed ───────────────────────────────────────
        try:
            fg_resp = requests.get("https://api.alternative.me/fng/?limit=1", timeout=8)
            fg_data = fg_resp.json().get("data", [{}])[0] if fg_resp.status_code == 200 else {}
            fg_val   = fg_data.get("value", "?")
            fg_label = fg_data.get("value_classification", "?")
            fg_num   = int(fg_val) if str(fg_val).isdigit() else 0
            fg_emoji = "🟢" if fg_num >= 60 else ("🟡" if fg_num >= 40 else "🔴")
            lines.append(f"<code>{fg_emoji} Fear &amp; Greed: {fg_val} — {_esc_html_main(fg_label)}</code>")
        except Exception:
            pass

        # ── Top picks ──────────────────────────────────────────
        lines += [f"<code>{sep}</code>", f"<b>🎯 TOP PICKS NOW</b>"]
        good = snap.get("good") or []
        if good:
            for idx, token in enumerate(good, start=1):
                sym   = str(token.get("symbol") or "?").upper()
                score = float(token.get("score", 0) or 0)
                chg   = float(token.get("change_24h", 0) or 0)
                conf  = str(token.get("confidence", "C")).upper()
                liq   = _fmt_usd_compact_main(token.get("liquidity"))
                lines.append(
                    f"<code>{idx}. ${_esc_html_main(sym):<9} {score:.0f}pts {conf} {chg:+.1f}%  liq {_esc_html_main(liq)}</code>"
                )
        else:
            if snap.get("regime_blocked"):
                lines.append(f"<code>🚫 Regime gate blocking entries</code>")
            else:
                lines.append(f"<code>⏳ No qualified setups right now</code>")

        # ── SOL leverage position ──────────────────────────────
        lines += [f"<code>{sep}</code>", f"<b>⚡ SOL POSITION</b>"]
        try:
            position = fetch_jupiter_position()
            sol_price = fetch_sol_price()
            if position:
                mark  = position.get("mark_price") or sol_price or 0
                entry = position.get("entry_price", 0)
                lev   = position.get("leverage", 0)
                pnl   = position.get("pnl", 0)
                liq_p = position.get("liq_price", 0)
                from jupiter_perps import calc_liq_distance_pct as _liq_dist
                liq_d = _liq_dist(mark, liq_p)
                pnl_emoji = "🟢" if pnl >= 0 else "🔴"
                liq_emoji = "🔴" if (liq_d and liq_d < 20) else ("🟡" if (liq_d and liq_d < 35) else "🟢")
                lines += [
                    f"<code>SOL  ${mark:.2f}  |  {lev:.1f}x</code>",
                    f"<code>{pnl_emoji} PnL ${pnl:+,.2f}  |  {liq_emoji} liq dist {liq_d:.1f}%</code>" if liq_d else f"<code>{pnl_emoji} PnL ${pnl:+,.2f}</code>",
                ]
            elif sol_price:
                lines.append(f"<code>SOL ${sol_price:.2f} — no open position</code>")
            else:
                lines.append(f"<code>No open perps position</code>")
        except Exception as exc:
            logging.warning("cmd_snapshot sol fetch error: %s", exc)
            lines.append(f"<code>SOL data unavailable</code>")

        # ── Open spot positions ────────────────────────────────
        positions = get_open_positions(limit=5)
        if positions:
            lines += [f"<code>{sep}</code>", f"<b>💼 SPOT POSITIONS</b>"]
            for pos in positions:
                sym   = str(pos.get("symbol") or "?").upper()
                entry = _try_float(pos.get("entry_price"))
                ep    = f"${entry:.6g}" if entry and entry > 0 else "—"
                lines.append(f"<code>${_esc_html_main(sym):<10} entry {ep}</code>")

        lines += [
            f"<code>{sep}</code>",
            f"<i>Use /marketnow for full tier breakdown  |  /lev for position detail</i>",
        ]

        await update.effective_message.reply_text(
            "\n".join(lines),
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    except Exception as exc:
        logging.exception("cmd_snapshot error: %s", exc)
        await update.effective_message.reply_text("⚠️ Snapshot failed — check logs.")


def _build_digest_message(rows: list[dict]) -> str | None:
    if not rows:
        return None
    unique = []
    seen = set()
    for row in rows:
        symbol = row.get("symbol")
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        unique.append(row)
        if len(unique) >= SIGNAL_DIGEST_MAX_ITEMS:
            break
    if not unique:
        return None

    import html as _html
    def _esc(v):
        return _html.escape(str(v))
    sep = "━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    lines = [
        f"<b>📡 [SIGNAL]: DIGEST</b>",
        f"<code>{sep}</code>",
        f"<code>🕐 LOOKBACK: {SIGNAL_DIGEST_LOOKBACK_HOURS}h</code>",
        f"<code>📊 SETUPS: {len(unique)}</code>",
        f"<code>{sep}</code>",
    ]
    for idx, row in enumerate(unique, start=1):
        score = float(row.get("score_total") or 0)
        chg = float(row.get("change_24h") or 0)
        regime = row.get("regime_label") or "N/A"
        chg_str = f"{chg:+.1f}%"
        sym = str(row.get("symbol") or "UNKNOWN").upper()
        lines.append(f"<code>{idx:02d}. ${_esc(sym):<9} {score:.0f}pts {chg_str:>7} {_esc(regime)}</code>")
    return "\n".join(lines)


def _format_multi_tier_good_buy_bulletin(per_tier_limit: int = 2) -> str:
    tier_order = ("conservative", "balanced", "aggressive")
    tier_priority = {"conservative": 0, "balanced": 1, "aggressive": 2}
    tier_display = {
        "conservative": "Long-Term",
        "balanced": "Mid-Term",
        "aggressive": "Short-Term",
    }

    snapshots = {
        tier: _analyze_market_now(limit_good=max(8, per_tier_limit * 3), limit_bad=0, tier=tier)
        for tier in tier_order
    }
    base_snap = snapshots.get("balanced") or snapshots.get("conservative") or snapshots.get("aggressive") or {}
    regime = base_snap.get("regime") or {}
    policy = base_snap.get("policy") or {}
    regime_score = float(regime.get("score", 0) or 0)
    regime_floor = float(policy.get("regime_min_score", 0) or 0)
    signal_action = "BUY" if regime_score >= regime_floor else "WAIT"

    best_fit_by_symbol = {}
    for tier in tier_order:
        snap = snapshots.get(tier) or {}
        for token in snap.get("good") or []:
            symbol = str(token.get("symbol") or "UNKNOWN").upper()
            score = float(token.get("score", 0) or 0)
            volume = float(token.get("volume_24h", 0) or 0)
            existing = best_fit_by_symbol.get(symbol)
            if not existing:
                best_fit_by_symbol[symbol] = {"tier": tier, "token": token}
                continue

            existing_tier = str(existing.get("tier") or "aggressive")
            existing_token = existing.get("token") or {}
            existing_score = float(existing_token.get("score", 0) or 0)
            existing_volume = float(existing_token.get("volume_24h", 0) or 0)
            if tier_priority.get(tier, 9) < tier_priority.get(existing_tier, 9):
                best_fit_by_symbol[symbol] = {"tier": tier, "token": token}
            elif tier_priority.get(tier, 9) == tier_priority.get(existing_tier, 9):
                if (score, volume) > (existing_score, existing_volume):
                    best_fit_by_symbol[symbol] = {"tier": tier, "token": token}

    ranked = sorted(
        best_fit_by_symbol.values(),
        key=lambda row: (
            float((row.get("token") or {}).get("score", 0) or 0),
            float((row.get("token") or {}).get("volume_24h", 0) or 0),
        ),
        reverse=True,
    )

    sep = "━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    thin = "┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄"

    # If regime says BUY but no token cleared the gates, show WAIT — no contradiction
    if signal_action == "BUY" and not ranked:
        signal_action = "WAIT"

    # Regime display
    regime_score_int = int(round(regime_score))
    regime_filled = int(round(regime_score / 10))
    regime_bar = "█" * regime_filled + "░" * (10 - regime_filled)
    regime_label_raw = regime.get("label", "UNKNOWN")
    regime_emoji = "🟢" if signal_action == "BUY" else ("🔴" if regime_score < regime_floor * 0.7 else "🟡")

    profile_label = f"{_mode().upper()} · {_risk_style_label()}"
    now_ts = datetime.utcnow().strftime("%H:%M UTC")

    if not ranked:
        lines = [
            f"<b>📊 MARKET ANALYSIS</b>",
            f"<code>{sep}</code>",
            f"<code>  Profile  {_esc_html_main(profile_label)}</code>",
            f"<code>  Regime   {regime_bar} {regime_score_int}/100</code>",
            f"<code>  Floor    {regime_floor:.0f}/100  Threshold not met</code>",
            f"<code>{sep}</code>",
            f"<code>  🔴 STATUS: WAIT</code>",
            f"<code>  No setups cleared entry gates right now.</code>",
            f"<code>  Regime must reach {regime_floor:.0f}+ to trigger buys.</code>",
            f"<code>{thin}</code>",
            f"<code>  Updated {_esc_html_main(now_ts)}</code>",
            f"<code>{sep}</code>",
            f"<i>Use /goodbuy all for full tier breakdown.</i>",
        ]
    else:
        # Build compact top-picks list (up to 5)
        top_picks = ranked[:5]

        # Header
        lines = [
            f"<b>📊 MARKET ANALYSIS</b>",
            f"<code>{sep}</code>",
            f"<code>  {regime_emoji} Regime  {regime_bar} {regime_score_int}/100</code>",
            f"<code>  Profile  {_esc_html_main(profile_label)}  ·  {_esc_html_main(now_ts)}</code>",
            f"<code>{sep}</code>",
            f"<code>  🟢 STATUS: {_esc_html_main(signal_action)} — {len(ranked)} setup(s) qualified</code>",
            f"<code>{thin}</code>",
        ]

        # Top picks rows
        for i, row in enumerate(top_picks, 1):
            t = row.get("token") or {}
            tier = str(row.get("tier") or "balanced")
            term = tier_display.get(tier, "Mid")[0:3].upper()  # "LON"/"MID"/"SHT"
            sym = str(t.get("symbol") or "?").upper()
            sc = float(t.get("score", 0) or 0)
            conf = str(t.get("confidence") or "C").upper()
            chg = float(t.get("change_24h", 0) or 0)
            liq = _fmt_usd_compact_main(t.get("liquidity"))
            cap_v = t.get("market_cap") or t.get("fdv")
            cap_d = _fmt_usd_compact_main(cap_v) if cap_v else "—"
            chg_sign = "+" if chg >= 0 else ""
            rank_emoji = "🥇" if i == 1 else ("🥈" if i == 2 else ("🥉" if i == 3 else "  "))
            lines.append(
                f"<code>  {rank_emoji} ${_esc_html_main(sym):<8} {sc:.0f}pt [{_esc_html_main(conf)}] {_esc_html_main(term)}</code>"
            )
            lines.append(
                f"<code>       Cap {_esc_html_main(cap_d):<8}  Liq {_esc_html_main(liq):<7}  24h {chg_sign}{chg:.1f}%</code>"
            )
            if i < len(top_picks):
                lines.append(f"<code>{thin}</code>")

        lines += [
            f"<code>{sep}</code>",
            f"<i>Use /goodbuy all for full details · /snapshot for briefing</i>",
        ]

    return "\n".join(lines).strip()


async def send_signal_digest(context: ContextTypes.DEFAULT_TYPE) -> bool:
    rows = get_recent_scan_bests(
        lookback_hours=SIGNAL_DIGEST_LOOKBACK_HOURS,
        limit=max(10, SIGNAL_DIGEST_MAX_ITEMS * 4),
    )
    msg = _build_digest_message(rows)
    if not msg:
        return False
    latest_ts = rows[0].get("ts_utc")
    if context.job and _digest_state.get("last_sent_ts") == latest_ts:
        return False

    if DRY_RUN:
        logging.info("DRY_RUN enabled. Digest not sent.\n%s", msg)
    else:
        await context.bot.send_message(
            chat_id=int(TELEGRAM_CHAT_ID),
            text=msg,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    _digest_state["last_sent_ts"] = latest_ts
    return True


async def send_good_buy_bulletin(context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Unified market analysis bulletin — always uses multi-tier analysis format."""
    per_tier_limit = max(1, int(GOOD_BUY_BULLETIN_PER_TIER_MAX_TOKENS))
    msg = _format_multi_tier_good_buy_bulletin(per_tier_limit=per_tier_limit)

    # Grab a snapshot just for logging metadata.
    tier = _normalize_market_tier(GOOD_BUY_BULLETIN_TIER)
    snap = _analyze_market_now(limit_good=1, limit_bad=0, tier=tier)

    decision = "ANALYSIS_BULLETIN"
    if DRY_RUN:
        decision = "ANALYSIS_BULLETIN_DRY_RUN"
        logging.info("DRY_RUN enabled. Analysis bulletin suppressed.\n%s", msg)
    else:
        await context.bot.send_message(
            chat_id=int(TELEGRAM_CHAT_ID),
            text=msg,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )

    log_signal(
        {
            "symbol": "ENGINE",
            "decision": decision,
            "regime_score": float((snap.get("regime") or {}).get("score") or 0),
            "regime_label": str((snap.get("regime") or {}).get("label") or ""),
            "notes": f"tier={tier} good_count={len(snap.get('good') or [])} universe={snap.get('tokens', 0)}",
        }
    )
    return True


async def run_sell_signal_scanner(context: ContextTypes.DEFAULT_TYPE, tokens: list[dict] | None = None):
    if not SELL_ALERTS_ENABLED:
        return

    open_positions = get_open_positions(limit=200)
    if not open_positions:
        return
    tracked_symbols = {str(p.get("symbol") or "").upper() for p in open_positions if p.get("symbol")}
    tracked_mints = {str(p.get("mint") or "") for p in open_positions if p.get("mint")}

    market_tokens = tokens if tokens is not None else (fetch_market_data() or [])
    if not market_tokens:
        return

    rules = _sell_style_rules()
    sent = 0
    for raw_token in market_tokens:
        if sent >= int(rules["max_per_cycle"]):
            break
        token = dict(raw_token)
        symbol = str(token.get("symbol") or "").upper()
        mint = str(token.get("address") or "")
        if symbol not in tracked_symbols and mint not in tracked_mints:
            continue
        if not symbol or _sell_signal_on_cooldown(symbol, cooldown_hours=rules["cooldown_hours"]):
            continue

        refreshed = _refresh_alert_market_snapshot(token)
        if refreshed:
            token = refreshed

        signal = _detect_sell_signal(token, rules=rules)
        if not signal:
            continue

        msg = _format_sell_alert_message(token, signal, compact=True)
        decision = f"SELL_ALERT_{signal['type']}"
        if DRY_RUN:
            decision = "SELL_ALERT_DRY_RUN"
            logging.info("DRY_RUN enabled. Sell alert suppressed for %s", symbol)
        else:
            await context.bot.send_message(
                chat_id=int(TELEGRAM_CHAT_ID),
                text=msg,
                parse_mode="HTML",
                reply_markup=_build_alert_keyboard(
                    symbol,
                    token.get("address"),
                    token.get("pair_address"),
                ),
                disable_web_page_preview=True,
            )

        log_signal(
            {
                "symbol": symbol,
                "mint": token.get("address"),
                "score": token.get("score"),
                "liquidity": token.get("liquidity"),
                "volume_24h": token.get("volume_24h"),
                "price": token.get("price"),
                "change_24h": token.get("change_24h"),
                "decision": decision,
                "notes": signal.get("reason"),
            }
        )
        sent += 1


async def cmd_digest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        await _reject_unauthorized(update)
        return
    sent = await send_signal_digest(context)
    if sent:
        await update.effective_message.reply_text("Digest sent.")
    else:
        await update.effective_message.reply_text("No new setups for digest right now.")


async def cmd_runnerwatch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        await _reject_unauthorized(update)
        return
    await run_new_runner_watch(context)
    await update.effective_message.reply_text("Runner watch scan executed.")


async def cmd_watchlist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        await _reject_unauthorized(update)
        return
    await run_watchlist_lane(context)
    rows = _build_watchlist_rows()
    live = sum(1 for r in rows if bool(r.get("has_live_data", True)))
    await update.effective_message.reply_text(
        f"Watchlist scan executed. Coverage: {live}/{len(rows)} live."
    )


async def cmd_watchlistsummary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        await _reject_unauthorized(update)
        return
    await send_watchlist_summary(context)
    await update.effective_message.reply_text("Watchlist summary sent.")


async def cmd_marketnow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        await _reject_unauthorized(update)
        return
    tier = _tier_arg_or_default(context.args, default="balanced")
    if tier == "all":
        await update.effective_message.reply_text(
            _format_market_now_multi_tier(mode="both"),
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        return
    snap = _analyze_market_now(limit_good=5, limit_bad=5, tier=tier)
    await update.effective_message.reply_text(
        _format_market_now_message(snap, mode="both"),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


async def cmd_goodbuy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        await _reject_unauthorized(update)
        return
    tier = _tier_arg_or_default(context.args, default="balanced")
    if tier == "all":
        await update.effective_message.reply_text(
            _format_market_now_multi_tier(mode="good"),
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        return
    snap = _analyze_market_now(limit_good=6, limit_bad=3, tier=tier)
    await update.effective_message.reply_text(
        _format_market_now_message(snap, mode="good"),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


async def cmd_badbuy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        await _reject_unauthorized(update)
        return
    tier = _tier_arg_or_default(context.args, default="balanced")
    if tier == "all":
        await update.effective_message.reply_text(
            _format_market_now_multi_tier(mode="bad"),
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        return
    snap = _analyze_market_now(limit_good=3, limit_bad=6, tier=tier)
    await update.effective_message.reply_text(
        _format_market_now_message(snap, mode="bad"),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


async def cmd_wallet2now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        await _reject_unauthorized(update)
        return
    await update.effective_message.reply_text(
        _format_wallet2_message(mode="good"),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


async def cmd_wallet2bad(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        await _reject_unauthorized(update)
        return
    await update.effective_message.reply_text(
        _format_wallet2_message(mode="bad"),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


async def cmd_wallet3now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        await _reject_unauthorized(update)
        return
    await update.effective_message.reply_text(
        _format_wallet3_message(mode="good"),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


async def cmd_wallet3bad(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        await _reject_unauthorized(update)
        return
    await update.effective_message.reply_text(
        _format_wallet3_message(mode="bad"),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


async def cmd_walletplan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        await _reject_unauthorized(update)
        return
    text = (
        "WALLET PLAYBOOK\n\n"
        f"{_wallet_header('W1')}\n\n"
        f"{_wallet_header('W2')}\n\n"
        f"{_wallet_header('W3')}\n\n"
        "Use /wallet2now /wallet2bad /wallet3now /wallet3bad for live routing."
    )
    await update.effective_message.reply_text(text)


async def on_alert_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return
    if not _is_authorized(update):
        await query.answer("Unauthorized", show_alert=True)
        return

    data = query.data or ""
    if data.startswith("mute:"):
        symbol = data.split(":", 1)[1].strip().upper()[:16]
        set_symbol_control(symbol=symbol, control_type="COOLDOWN", hours=24, reason="manual_telegram_mute")
        log_signal({"symbol": symbol, "decision": "TELEGRAM_MUTE_24H", "notes": "manual mute from button"})
        await query.answer(f"{symbol} muted 24h", show_alert=False)
    elif data.startswith("ack:"):
        symbol = data.split(":", 1)[1].strip().upper()[:16]
        log_signal({"symbol": symbol, "decision": "ALERT_ACK", "notes": "telegram acknowledge"})
        await query.answer("Acknowledged", show_alert=False)
    else:
        await query.answer()


async def run_new_runner_watch(context):
    if not NEW_RUNNER_WATCH_ENABLED:
        return
    try:
        print("Running new-runner watch...")
        tokens = fetch_runner_watch_candidates(
            queries=NEW_RUNNER_SEARCH_QUERIES,
            pairs_per_query=NEW_RUNNER_PAIRS_PER_QUERY,
            limit=NEW_RUNNER_MAX_RESULTS,
        )
        if not tokens:
            logging.info("New-runner watch: no candidates from DexScreener.")
            return

        candidates = []
        for token in tokens:
            enriched = _runner_watch_enrich(token)
            if not enriched:
                continue
            symbol = str(enriched.get("symbol") or "").upper()
            if _runner_watch_on_cooldown(symbol):
                log_signal(
                    {
                        "symbol": symbol,
                        "mint": enriched.get("address"),
                        "score": enriched.get("watch_score"),
                        "decision": "RUNNER_WATCH_COOLDOWN_SKIP",
                        "notes": f"cooldown_hours={NEW_RUNNER_COOLDOWN_HOURS}",
                    }
                )
                continue
            candidates.append(enriched)

        if not candidates:
            print("New-runner watch: no tokens passed filters.")
            return

        candidates.sort(key=lambda t: t.get("watch_score", 0), reverse=True)
        sent = 0
        seen = set()
        max_per_cycle = max(1, NEW_RUNNER_MAX_ALERTS_PER_CYCLE)

        for token in candidates:
            if sent >= max_per_cycle:
                break
            symbol = str(token.get("symbol") or "").upper()
            if not symbol or symbol in seen:
                continue
            seen.add(symbol)

            msg = format_runner_watch(token, compact=True)
            decision = "RUNNER_WATCH_ALERT"
            if DRY_RUN:
                decision = "RUNNER_WATCH_DRY_RUN"
                logging.info("DRY_RUN enabled. Runner watch alert suppressed for %s", symbol)
            elif TELEGRAM_QUIET_MODE and context.job is not None:
                decision = "RUNNER_WATCH_QUIET_MODE"
            else:
                await context.bot.send_message(
                    chat_id=int(TELEGRAM_CHAT_ID),
                    text=msg,
                    parse_mode="HTML",
                    reply_markup=_build_alert_keyboard(
                        symbol,
                        token.get("address"),
                        token.get("pair_address"),
                    ),
                    disable_web_page_preview=True,
                )

            log_signal(
                {
                    "symbol": symbol,
                    "mint": token.get("address"),
                    "pair_address": token.get("pair_address"),
                    "score": token.get("watch_score"),
                    "liquidity": token.get("liquidity"),
                    "volume_24h": token.get("volume_24h"),
                    "price": token.get("price"),
                    "change_24h": token.get("change_24h"),
                    "decision": decision,
                    "notes": (
                        f"runner_watch age_h={token.get('age_hours'):.2f} "
                        f"x_proxy={token.get('x_proxy_label')} narrative={token.get('narrative_label')}"
                    ),
                }
            )
            sent += 1

        if sent == 0:
            print("New-runner watch: candidates found but skipped by cooldown/caps.")
    except Exception as exc:
        print("RUNNER WATCH ERROR:", repr(exc))


async def run_legacy_recovery_scanner(context):
    """Scan established Solana memecoins (>90d) showing fresh reversal patterns."""
    if not LEGACY_RECOVERY_ENABLED:
        return
    try:
        print("Running legacy recovery scan...")
        # Use broad fetch — scans full established Solana universe, not a hardcoded list.
        # Custom queries from .env override the built-in broad sweep if set.
        custom_queries = LEGACY_RECOVERY_SEARCH_QUERIES if LEGACY_RECOVERY_SEARCH_QUERIES else None
        tokens = fetch_legacy_recovery_candidates(
            queries=custom_queries,
            pairs_per_query=LEGACY_RECOVERY_PAIRS_PER_QUERY,
            limit=300,
        )
        if not tokens:
            logging.info("Legacy recovery scan: no candidates from DexScreener.")
            return

        # Get current SOL status to embed in alert
        sol_proxy = _compute_sol_regime_proxy()
        sol_change_24h = float(sol_proxy.get("change_24h", 0) or 0)
        sol_change_1h = float(sol_proxy.get("change_1h", 0) or 0)
        if sol_change_24h <= SOL_EXTREME_OFF_CHANGE_24H or sol_change_1h <= SOL_EXTREME_OFF_CHANGE_1H:
            sol_status = "EXTREME_RISK_OFF"
        elif sol_change_24h <= SOL_RISK_OFF_CHANGE_24H:
            sol_status = "RISK_OFF"
        elif sol_change_24h >= SOL_RISK_ON_CHANGE_24H:
            sol_status = "RISK_ON"
        else:
            sol_status = "NEUTRAL"

        candidates = []
        for token in tokens:
            enriched = _detect_legacy_recovery(token)
            if not enriched:
                continue
            symbol = str(enriched.get("symbol") or "").upper()
            if _legacy_recovery_on_cooldown(symbol):
                log_signal({
                    "symbol": symbol,
                    "mint": enriched.get("address"),
                    "decision": "LEGACY_RECOVERY_COOLDOWN_SKIP",
                    "notes": f"cooldown_hours={LEGACY_RECOVERY_COOLDOWN_HOURS}",
                })
                continue
            enriched["sol_status"] = sol_status
            candidates.append(enriched)

        if not candidates:
            print("Legacy recovery scan: no tokens passed filters.")
            return

        # Sort by volume_24h descending (most active established coins first)
        candidates.sort(key=lambda t: float(t.get("volume_24h", 0) or 0), reverse=True)
        sent = 0
        seen = set()
        max_per_cycle = max(1, LEGACY_RECOVERY_MAX_ALERTS_PER_CYCLE)

        for token in candidates:
            if sent >= max_per_cycle:
                break
            symbol = str(token.get("symbol") or "").upper()
            if not symbol or symbol in seen:
                continue
            seen.add(symbol)

            msg = format_legacy_recovery(token)
            decision = "LEGACY_RECOVERY_ALERT"
            if DRY_RUN:
                decision = "LEGACY_RECOVERY_DRY_RUN"
                logging.info("DRY_RUN enabled. Legacy recovery alert suppressed for %s", symbol)
            elif TELEGRAM_QUIET_MODE and context.job is not None:
                decision = "LEGACY_RECOVERY_QUIET_MODE"
            else:
                await context.bot.send_message(
                    chat_id=int(TELEGRAM_CHAT_ID),
                    text=msg,
                    parse_mode="HTML",
                    reply_markup=_build_alert_keyboard(
                        symbol,
                        token.get("address"),
                        token.get("pair_address"),
                    ),
                    disable_web_page_preview=True,
                )

            log_signal({
                "symbol": symbol,
                "mint": token.get("address"),
                "pair_address": token.get("pair_address"),
                "score": 0,
                "liquidity": token.get("liquidity"),
                "volume_24h": token.get("volume_24h"),
                "price": token.get("price"),
                "change_24h": token.get("change_24h"),
                "decision": decision,
                "notes": (
                    f"legacy_recovery age_d={token.get('age_days', 0):.1f} "
                    f"pattern={token.get('pattern_label')} sol={sol_status}"
                ),
            })
            # Track outcome for lane learning (legacy recovery lane)
            if OUTCOME_TRACKING_ENABLED:
                _lr_price = float(token.get("price") or 0)
                if _lr_price > 0 and "DRY_RUN" not in decision and "QUIET" not in decision:
                    try:
                        from utils.market_cycle import get_current_cycle_phase as _gcp2  # type: ignore
                        _lr_cycle = _gcp2()
                    except Exception:
                        _lr_cycle = "TRANSITION"
                    queue_alert_outcome({
                        "symbol": symbol,
                        "mint": token.get("address"),
                        "entry_price": _lr_price,
                        "score": float(token.get("score") or 0),
                        "regime_score": 0,
                        "regime_label": token.get("pattern_label") or "LEGACY",
                        "confidence": "C",
                        "lane": "legacy",
                        "source": "dexscreener",
                        "cycle_phase": _lr_cycle,
                    })
            sent += 1

        if sent == 0:
            print("Legacy recovery scan: candidates found but skipped by cooldown/caps.")
    except Exception as exc:
        print("LEGACY RECOVERY SCAN ERROR:", repr(exc))


async def run_engine(context):
    try:
        print(f"Running {_mode()} scan...")
        tokens = fetch_market_data()
        log_signal({
            "symbol": "ENGINE",
            "decision": "SCAN_RUN",
            "notes": f"fetched_tokens={len(tokens)}",
        })
        if not tokens:
            print("No valid tokens found (filters removed everything).")
            return

        regime = _compute_regime(tokens)
        sol_proxy = _compute_sol_regime_proxy()
        policy = _build_market_policy(regime, sol_proxy)

        # Persist regime snapshot (used by market_cycle learning)
        try:
            from utils.db import get_conn as _get_regime_conn  # type: ignore
            _now_iso = datetime.utcnow().isoformat()
            with _get_regime_conn() as _rc:
                _rc.execute(
                    """INSERT INTO regime_snapshots
                       (ts_utc, sol_change_24h, breadth_pct, liquidity_score, volume_score,
                        regime_score, regime_label, notes, cycle_phase)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        _now_iso,
                        float(sol_proxy.get("change_24h", 0) or 0),
                        float(regime.get("breadth_pct", 0) or 0),
                        0.0,  # liquidity_score — placeholder (not separately computed)
                        0.0,  # volume_score    — placeholder (not separately computed)
                        float(regime.get("score", 50) or 50),
                        regime.get("label", "RISK_NEUTRAL"),
                        f"sol1h={sol_proxy.get('change_1h', 0):.2f}%",
                        policy.get("cycle_phase", "TRANSITION"),
                    ),
                )
        except Exception as _re:
            logging.debug("regime_snapshot write error: %s", _re)

        # Feature 3: SOL Macro Correlator — fire alert when SOL makes a big 1h move
        try:
            sol_1h = float(sol_proxy.get("change_1h", 0) or 0)
            if abs(sol_1h) >= 5.0 and not DRY_RUN:
                macro_msg = format_sol_macro_alert(sol_1h)
                if macro_msg:
                    _last_sol_macro = getattr(run_engine, "_last_sol_macro_ts", 0)
                    _now_ts = time_module.time()
                    if _now_ts - _last_sol_macro > 3600:  # Max once per hour
                        run_engine._last_sol_macro_ts = _now_ts
                        await context.bot.send_message(
                            chat_id=int(TELEGRAM_CHAT_ID),
                            text=macro_msg,
                            parse_mode="HTML",
                            disable_web_page_preview=True,
                        )
        except Exception as _e:
            logging.debug("SOL macro alert error: %s", _e)

        # Exit-risk alerts run independently of buy-gate conditions.
        await run_sell_signal_scanner(context, tokens=tokens)

        if policy["hard_block"]:
            log_signal({
                "symbol": "ENGINE",
                "decision": "MARKET_HARD_BLOCK",
                "regime_score": regime["score"],
                "regime_label": regime["label"],
                "notes": policy["hard_block_reason"],
            })
            print(f"Market hard block: {policy['hard_block_reason']}")
            return

        can_trade, block_reason = _risk_governor_status()
        if not can_trade:
            log_signal({
                "symbol": "ENGINE",
                "decision": "RISK_GOVERNOR_BLOCK",
                "regime_score": regime["score"],
                "regime_label": regime["label"],
                "notes": block_reason,
            })
            print(f"Risk governor blocked alerts: {block_reason}")
            return

        # ── Loss Streak Radar: apply dynamic threshold escalation ──────────────
        try:
            _risk_mode = get_risk_mode()
            _rm_delta = float(_risk_mode.get("threshold_delta", 0))
            _rm_mode = _risk_mode.get("mode", "NORMAL")
            _rm_streak = int(_risk_mode.get("streak", 0))
            _rm_emoji = _risk_mode.get("emoji", "🟢")
            _prev_rm = getattr(run_engine, "_last_risk_mode", "NORMAL")

            if _rm_delta > 0:
                policy["alert_threshold"] = float(policy.get("alert_threshold", 70)) + _rm_delta
                logging.info("Risk mode %s: threshold raised by +%d to %.1f (streak=%d)",
                             _rm_mode, _rm_delta, policy["alert_threshold"], _rm_streak)

            # Fire a Telegram warning when risk mode worsens
            if _rm_mode != _prev_rm and not DRY_RUN:
                run_engine._last_risk_mode = _rm_mode
                sep_ = "━━━━━━━━━━━━━━━━━━━━━━━━━━━"
                thin_ = "┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄"
                if _rm_mode == "DEFENSIVE":
                    action_txt = f"Threshold raised +{_rm_delta:.0f}pt · A-grade only · sizing -70%"
                    advice_txt = "Consider standing aside until streak breaks."
                elif _rm_mode == "CAUTIOUS":
                    action_txt = f"Threshold raised +{_rm_delta:.0f}pt · sizing -50%"
                    advice_txt = "Reduce size. Wait for clean setups."
                else:
                    action_txt = "Thresholds restored to normal"
                    advice_txt = "Streak cleared. Full sizing re-enabled."
                mode_msg = "\n".join([
                    f"<b>⚠️ RISK MODE CHANGE</b>",
                    f"<code>{sep_}</code>",
                    f"<code>  {_rm_emoji} Mode     {_rm_mode}</code>",
                    f"<code>  Streak   {_rm_streak} consecutive losses</code>",
                    f"<code>{thin_}</code>",
                    f"<code>  Action   {action_txt}</code>",
                    f"<code>  Advice   {advice_txt}</code>",
                    f"<code>{sep_}</code>",
                ])
                await context.bot.send_message(
                    chat_id=int(TELEGRAM_CHAT_ID),
                    text=mode_msg,
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                )
            elif _prev_rm == "NORMAL":
                # First run — set baseline
                run_engine._last_risk_mode = _rm_mode
        except Exception as _rm_err:
            logging.debug("Risk mode escalation error: %s", _rm_err)
        # ──────────────────────────────────────────────────────────────────────

        logging.info(
            "Market policy: state=%s sol24h=%.2f%% sol1h=%.2f%% thr=%.1f conf>=%s regime_min=%.1f "
            "cycle_cap=%s day_cap=%s cooldown_h=%s",
            policy["state"],
            float(sol_proxy.get("change_24h", 0) or 0),
            float(sol_proxy.get("change_1h", 0) or 0),
            float(policy["alert_threshold"]),
            policy["min_confidence_to_alert"],
            float(policy["regime_min_score"]),
            policy["max_alerts_per_cycle"],
            policy["max_alerts_per_day"],
            policy["alert_cooldown_hours"],
        )

        alerts_24h = count_alerts_since(datetime.utcnow() - timedelta(hours=24))
        policy_day_cap = int(policy["max_alerts_per_day"])
        if policy_day_cap > 0 and alerts_24h >= policy_day_cap:
            reason = f"Policy daily cap reached ({alerts_24h}/{policy_day_cap}) state={policy['state']}"
            log_signal({
                "symbol": "ENGINE",
                "decision": "POLICY_DAILY_CAP_BLOCK",
                "regime_score": regime["score"],
                "regime_label": regime["label"],
                "notes": reason,
            })
            print(f"Policy blocked alerts: {reason}")
            return

        candidates = []
        for token in tokens:
            if not token.get("address"):
                continue
            if not _passes_quality_filters(token):
                continue
            if not _passes_execution_quality_filters(token):
                continue
            if not _passes_tactical_filters(token):
                continue
            symbol = token.get("symbol", "")
            control = _apply_symbol_controls(symbol)
            if control:
                log_signal({
                    "symbol": symbol,
                    "mint": token.get("address"),
                    "decision": "SYMBOL_CONTROL_SKIP",
                    "notes": f"{control['type']} until {control['until'].isoformat()}",
                })
                continue

            # Enrich with BirdEye overview data (unique wallets, social, etc.)
            token = _enrich_token_for_scoring(token)
            token["engine_profile"] = _mode()
            score = calculate_token_score(token)
            token["score"] = score
            token["confidence"] = _confidence_from_score(score)
            logging.debug("Token %s scored %.2f", token.get("symbol", "UNKNOWN"), score)

            if score >= float(policy["alert_threshold"]) and _confidence_meets_rule(
                token["confidence"],
                policy["min_confidence_to_alert"],
            ):
                candidates.append(token)

        regime_min = float(policy["regime_min_score"])
        if ENABLE_REGIME_GATE and regime["score"] < regime_min:
            log_signal({
                "symbol": "ENGINE",
                "decision": "REGIME_BLOCK",
                "regime_score": regime["score"],
                "regime_label": regime["label"],
                "notes": (
                    f"Gate active. min={regime_min}, breadth={regime['breadth_pct']:.2f}, "
                    f"market_state={policy['state']}"
                ),
            })
            print(
                f"Regime gate blocked alerts: {regime['label']} "
                f"({regime['score']:.1f} < {regime_min})."
            )
            return

        if not candidates:
            print(f"No tokens meet the score threshold ({policy['alert_threshold']}).")
            return

        candidates.sort(key=lambda x: x["score"], reverse=True)
        best_token = candidates[0]

        log_signal({
            "symbol": best_token.get("symbol"),
            "mint": best_token.get("address"),
            "score": best_token.get("score"),
            "liquidity": best_token.get("liquidity"),
            "volume_24h": best_token.get("volume_24h"),
            "price": best_token.get("price"),
            "change_24h": best_token.get("change_24h"),
            "regime_score": regime["score"],
            "regime_label": regime["label"],
            "conviction": _CONFIDENCE_ORDER.get(best_token.get("confidence", "C"), 1),
            "decision": "SCAN_BEST",
        })
        policy_cycle_cap = max(1, int(policy["max_alerts_per_cycle"]))
        max_cycle_alerts = max(1, min(MAX_ALERTS_PER_CYCLE, policy_cycle_cap))
        max_ranked = max(1, min(ALERT_TOP_N, policy_cycle_cap))
        alerts_sent = 0
        seen_symbols = set()

        for token in candidates:
            if alerts_sent >= max_cycle_alerts or alerts_sent >= max_ranked:
                break

            refreshed = _refresh_alert_market_snapshot(token)
            if refreshed is None:
                log_signal({
                    "symbol": token.get("symbol", "UNKNOWN"),
                    "mint": token.get("address"),
                    "decision": "ALERT_SKIP_NO_LIVE_DATA",
                    "notes": "live refresh required but unavailable",
                })
                continue
            token = refreshed

            symbol = token.get("symbol", "UNKNOWN")
            if symbol in seen_symbols:
                continue
            seen_symbols.add(symbol)

            if _is_symbol_on_cooldown(symbol, cooldown_hours=policy["alert_cooldown_hours"]):
                log_signal({
                    "symbol": symbol,
                    "mint": token.get("address"),
                    "score": token.get("score"),
                    "regime_score": regime["score"],
                    "regime_label": regime["label"],
                    "conviction": _CONFIDENCE_ORDER.get(token.get("confidence", "C"), 1),
                    "decision": "COOLDOWN_SKIP",
                    "notes": f"cooldown_hours={policy['alert_cooldown_hours']} state={policy['state']}",
                })
                continue
            symbol_daily_alerts = count_alerts_since(
                datetime.utcnow() - timedelta(hours=24),
                symbol=symbol,
            )
            if (
                ENABLE_RISK_GOVERNOR
                and MAX_ALERTS_PER_SYMBOL_PER_DAY > 0
                and symbol_daily_alerts >= MAX_ALERTS_PER_SYMBOL_PER_DAY
            ):
                log_signal({
                    "symbol": symbol,
                    "mint": token.get("address"),
                    "score": token.get("score"),
                    "decision": "SYMBOL_DAILY_CAP_SKIP",
                    "notes": f"daily_cap={MAX_ALERTS_PER_SYMBOL_PER_DAY}",
                })
                continue

            change_24h = float(token.get("change_24h", 0) or 0)
            trend = "Uptrend" if change_24h >= 0 else "Pullback"
            entry_type = "Momentum continuation" if change_24h >= 0 else "Dip recovery setup"
            wallet_fit = _wallet_fit_for_main_token(token, regime, policy)
            risk_plan, rotation_plan = _wallet_guidance(wallet_fit)
            token_data = {
                "symbol": symbol,
                "score": token.get("score"),
                "price": token.get("price"),
                "liquidity": token.get("liquidity"),
                "volume_24h": token.get("volume_24h"),
                "market_cap": token.get("market_cap"),
                "fdv": token.get("fdv"),
                "holders": _holders_for_alert(token),
                "trend": trend,
                "entry_type": entry_type,
                "confidence": token.get("confidence", "C"),
                "regime_label": regime["label"],
                "profile": _mode(),
                "change_24h": change_24h,
                "change_1h": token.get("change_1h"),
                "txns_h1": token.get("txns_h1"),
                "rsi": token.get("rsi"),
                "macd_hist": token.get("macd_hist"),
                "wallet_fit": wallet_fit,
                "risk_plan": risk_plan,
                "rotation_plan": rotation_plan,
            }

            if not _passes_live_push_gate(token, policy):
                required_score = float(policy.get("alert_threshold", _runtime["alert_threshold"]) or 0) + max(
                    0,
                    int(TELEGRAM_PUSH_MIN_SCORE_DELTA),
                )
                log_signal(
                    {
                        "symbol": symbol,
                        "mint": token.get("address"),
                        "score": token.get("score"),
                        "regime_score": regime["score"],
                        "regime_label": regime["label"],
                        "decision": "ALERT_SUPPRESSED_NOISE_GATE",
                        "notes": (
                            f"score>={required_score:.1f} conf>={TELEGRAM_PUSH_MIN_CONFIDENCE} "
                            f"got score={float(token.get('score', 0) or 0):.1f} conf={token.get('confidence', 'C')}"
                        ),
                    }
                )
                continue

            msg = format_signal(token_data, compact=True)

            decision = "ALERT"
            if DRY_RUN:
                decision = "ALERT_DRY_RUN"
                logging.info("DRY_RUN enabled. Telegram alert suppressed for %s", symbol)
            else:
                await context.bot.send_message(
                    chat_id=int(TELEGRAM_CHAT_ID),
                    text=msg,
                    parse_mode="HTML",
                    reply_markup=_build_alert_keyboard(
                        symbol,
                        token.get("address"),
                        token.get("pair_address"),
                    ),
                    disable_web_page_preview=True,
                )

            # Capture score breakdown for feature-level win rate analysis
            _score_breakdown_json = None
            try:
                from scoring import calculate_token_score_with_breakdown as _score_with_bd
                import json as _json_alert
                _, _bd = _score_with_bd(token)
                _score_breakdown_json = _json_alert.dumps({k: round(float(v), 2) for k, v in _bd.items()})
            except Exception:
                pass

            log_signal({
                "symbol": symbol,
                "mint": token.get("address"),
                "score": token.get("score"),
                "regime_score": regime["score"],
                "regime_label": regime["label"],
                "conviction": _CONFIDENCE_ORDER.get(token.get("confidence", "C"), 1),
                "decision": decision,
                "score_breakdown": _score_breakdown_json,
                "notes": f"mcap_tier={token.get('mcap_tier','UNKNOWN')} helius={token.get('helius_grade','?')}",
            })

            if OUTCOME_TRACKING_ENABLED:
                entry_price = float(token.get("price") or 0)
                if entry_price > 0:
                    queue_alert_outcome({
                        "symbol": symbol,
                        "mint": token.get("address"),
                        "entry_price": entry_price,
                        "score": token.get("score"),
                        "regime_score": regime["score"],
                        "regime_label": regime["label"],
                        "confidence": token.get("confidence", "C"),
                        "lane": "new_runner",
                        "source": "birdeye",
                        "cycle_phase": policy.get("cycle_phase", "TRANSITION"),
                    })

            # ── Auto-execution ─────────────────────────────────────────────
            _exec_enabled = os.getenv("EXECUTOR_ENABLED", "false").lower() == "true"
            if _exec_enabled and token.get("address"):
                try:
                    from utils.position_sizing import calculate_position_size
                    from utils.executor import execute_signal as _execute_signal
                    _portfolio_usd = float(os.getenv("PORTFOLIO_USD", "1000"))
                    _pos = calculate_position_size(token, _portfolio_usd)
                    _signal_for_exec = {
                        "symbol": symbol,
                        "mint": token.get("address"),
                        "entry_price": float(token.get("price") or 0),
                        "score": token.get("score"),
                        "confidence": token.get("confidence", "C"),
                        "regime_label": regime["label"],
                        "position_usd": _pos.get("position_usd", 0),
                        "cycle_phase": policy.get("cycle_phase", "TRANSITION"),
                    }
                    import asyncio as _asyncio
                    _asyncio.create_task(_execute_signal(_signal_for_exec))
                except Exception as _exec_err:
                    logging.warning("executor.execute_signal error: %s", _exec_err)

            alerts_sent += 1

        if alerts_sent == 0:
            print("Candidates found but skipped by cooldown/caps.")
    except Exception as exc:
        print("ENGINE ERROR:", repr(exc))


# ── End of Day Recap ──────────────────────────────────────────────────────────

def _format_recap(perf: dict, positions: list, scan_bests: list, position: dict | None) -> str:
    """Build the /recap end-of-day summary card."""
    from datetime import datetime, timezone
    sep = "━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    lines = [
        f"<b>📋 DAILY RECAP</b>",
        f"<code>{sep}</code>",
        f"<code>🕐 {now_utc}</code>",
        f"<code>{sep}</code>",
    ]

    # ── Engine signals ────────────────────────────────────────
    alerts      = perf.get("total_alerts", 0)
    scans       = perf.get("total_scans", 0)
    avg_score   = perf.get("avg_score", 0.0)
    max_score   = perf.get("max_score", 0.0)
    top_symbols = perf.get("top_alert_symbols", [])

    lines += [
        f"<b>📡 SIGNALS (24h)</b>",
        f"<code>{sep}</code>",
        f"<code>Alerts fired   : {alerts}</code>",
        f"<code>Scans run      : {scans}</code>",
        f"<code>Avg score      : {avg_score:.1f}</code>",
        f"<code>Peak score     : {max_score:.1f}</code>",
    ]
    if top_symbols:
        syms = "  ".join(s.upper() for s in top_symbols[:5])
        lines.append(f"<code>Top symbols    : {syms}</code>")

    # ── Top scan picks ────────────────────────────────────────
    if scan_bests:
        lines += [
            f"<code>{sep}</code>",
            f"<b>🔍 TOP PICKS (6h)</b>",
            f"<code>{sep}</code>",
        ]
        for row in scan_bests[:5]:
            sym   = str(row.get("symbol", "?")).upper()
            score = float(row.get("score_total", 0))
            ch24  = row.get("change_24h")
            ch_str = f"{float(ch24):+.1f}%" if ch24 is not None else "—"
            lines.append(f"<code>{sym:<10} score {score:.0f}  24h {ch_str}</code>")

    # ── Open positions ────────────────────────────────────────
    lines += [
        f"<code>{sep}</code>",
        f"<b>💼 OPEN POSITIONS</b>",
        f"<code>{sep}</code>",
    ]
    if positions:
        for pos in positions[:5]:
            sym   = str(pos.get("symbol", "?")).upper()
            entry = pos.get("entry_price")
            ep    = f"${float(entry):.4f}" if entry else "—"
            lines.append(f"<code>{sym:<10} entry {ep}</code>")
    else:
        lines.append(f"<code>No tracked positions</code>")

    # ── SOL leverage position ─────────────────────────────────
    lines += [
        f"<code>{sep}</code>",
        f"<b>⚡ SOL LEVERAGE</b>",
        f"<code>{sep}</code>",
    ]
    if position:
        mark    = position.get("mark_price", 0)
        pnl     = position.get("pnl", 0)
        lev     = position.get("leverage", 0)
        liq_d   = position.get("liq_distance_pct")
        pnl_sym = "🟢" if pnl >= 0 else "🔴"
        liq_str = f"{liq_d:.1f}%" if liq_d is not None else "—"
        lines += [
            f"<code>SOL price      : ${mark:.2f}</code>",
            f"<code>Leverage       : {lev:.2f}x</code>",
            f"<code>PnL            : {pnl_sym} ${pnl:+.2f}</code>",
            f"<code>Liq distance   : {liq_str}</code>",
        ]
    else:
        lines.append(f"<code>No open perps position</code>")

    lines.append(f"<code>{sep}</code>")
    return "\n".join(lines)


async def cmd_recap(update, context):
    """Handler for /recap — end of day summary."""
    if not _is_authorized(update):
        return
    try:
        perf        = get_performance_summary(lookback_hours=24)
        positions   = get_open_positions(limit=10)
        scan_bests  = get_recent_scan_bests(lookback_hours=6, limit=5)
        position    = fetch_jupiter_position()
        msg = _format_recap(perf, positions, scan_bests, position)
        await update.effective_message.reply_text(msg, parse_mode="HTML")
    except Exception as exc:
        logging.exception("cmd_recap error: %s", exc)
        await update.effective_message.reply_text("⚠️ Recap failed — check logs.")


# ── SOL DCA Tracker ───────────────────────────────────────────────────────────

async def cmd_dca(update, context):
    """
    Handler for /dca — log a DCA entry or show summary.
    Usage:
      /dca                    — show DCA summary
      /dca 250                — log $250 at current SOL price (spot)
      /dca 250 3              — log $250 at 3x leverage at current price
      /dca 250 3 82.91        — log $250 at 3x leverage at price $82.91
      /dca clear              — clear all DCA entries and start fresh
    """
    if not _is_authorized(update):
        return
    try:
        args = (context.args or [])

        # /dca clear — wipe all entries
        if args and args[0].lower() == "clear":
            from jupiter_perps import _DCA_FILE
            if _DCA_FILE.exists():
                _DCA_FILE.unlink()
            await update.effective_message.reply_text(
                "✅ All DCA entries cleared.\nUse /dca &lt;amount&gt; [leverage] [price] to start fresh.",
                parse_mode="HTML"
            )
            return

        # Always fetch live price for dashboard display
        live_price = fetch_sol_price()
        if not live_price:
            position = fetch_jupiter_position()
            live_price = (position or {}).get("mark_price") or 0
        if not live_price:
            await update.effective_message.reply_text(
                "⚠️ Could not fetch SOL price. Try again in a moment."
            )
            return

        added_entry = None
        if args:
            try:
                amount_usd = float(args[0].replace("$", "").replace(",", ""))
            except ValueError:
                await update.effective_message.reply_text(
                    "⚠️ Invalid amount.\n"
                    "Usage: /dca 250 [leverage] [price]\n"
                    "Example: /dca 250 3 82.91"
                )
                return

            leverage = 1.0
            if len(args) >= 2:
                try:
                    leverage = float(args[1])
                except ValueError:
                    pass

            # Optional price override — use if provided, else live price
            entry_price = live_price
            if len(args) >= 3:
                try:
                    entry_price = float(args[2].replace("$", ""))
                except ValueError:
                    pass

            if amount_usd <= 0 or leverage <= 0 or entry_price <= 0:
                await update.effective_message.reply_text("⚠️ Amount, leverage and price must be positive.")
                return

            added_entry = add_dca_entry(amount_usd, entry_price, leverage)

        msg = format_dca_dashboard(live_price, added_entry=added_entry)
        await update.effective_message.reply_text(msg, parse_mode="HTML")
    except Exception as exc:
        logging.exception("cmd_dca error: %s", exc)
        await update.effective_message.reply_text("⚠️ DCA command failed — check logs.")


# ── Jupiter Perps Leverage Assistant ─────────────────────────────────────────

async def cmd_lev(update, context):
    """Handler for /lev — full position dashboard."""
    if not _is_authorized(update):
        return
    try:
        position = fetch_jupiter_position()
        sol_price = fetch_sol_price() if not position else None
        msg = format_lev_dashboard(position, sol_price=sol_price)
        await update.effective_message.reply_text(msg, parse_mode="HTML", disable_web_page_preview=True)
    except Exception as exc:
        logging.warning("cmd_lev error: %s", exc)
        await update.effective_message.reply_text("❌ Error fetching position data.")


async def cmd_lev_status(update, context):
    """Handler for /levstatus — quick one-liner status."""
    if not _is_authorized(update):
        return
    try:
        position = fetch_jupiter_position()
        sol_price = fetch_sol_price() if not position else None
        msg = format_lev_status(position, sol_price=sol_price)
        await update.effective_message.reply_text(msg, parse_mode="HTML", disable_web_page_preview=True)
    except Exception as exc:
        logging.warning("cmd_lev_status error: %s", exc)
        await update.effective_message.reply_text("❌ Error fetching position data.")


async def cmd_what_if(update, context):
    """Handler for /whatif — what-if calculator at price targets."""
    if not _is_authorized(update):
        return
    try:
        position = fetch_jupiter_position()
        # Check if a custom price was passed e.g. /whatif 150
        custom_targets = None
        if context.args:
            try:
                custom_targets = [float(a) for a in context.args if a.replace(".", "").isdigit()]
            except ValueError:
                pass
        msg = format_what_if(position, targets=custom_targets or PRICE_TARGETS)
        await update.effective_message.reply_text(msg, parse_mode="HTML", disable_web_page_preview=True)
    except Exception as exc:
        logging.warning("cmd_what_if error: %s", exc)
        await update.effective_message.reply_text("❌ Error running what-if calculation.")


async def cmd_lev_rec(update, context):
    """Handler for /levrec — leverage recommendation for next $250 add."""
    if not _is_authorized(update):
        return
    try:
        await update.effective_message.reply_text(
            "<code>⏳ Fetching position, SOL price and volatility...</code>",
            parse_mode="HTML",
        )
        position = fetch_jupiter_position()
        sol_price = fetch_sol_price() or (position or {}).get("mark_price")
        vol = fetch_sol_volatility_30d()
        funding = (position or {}).get("funding_rate")
        # Allow custom add amount e.g. /levrec 500
        add_usd = MONTHLY_ADD_USD
        if context.args:
            try:
                add_usd = float(context.args[0])
            except (ValueError, IndexError):
                pass
        rec = calc_leverage_recommendation(position, sol_price, add_usd=add_usd, vol=vol, funding=funding)
        msg = format_leverage_rec(rec)
        await update.effective_message.reply_text(msg, parse_mode="HTML", disable_web_page_preview=True)
    except Exception as exc:
        logging.warning("cmd_lev_rec error: %s", exc)
        await update.effective_message.reply_text("❌ Error calculating leverage recommendation.")


async def cmd_price_zones(update, context):
    """Handler for /pricezones — liquidation risk at key price levels."""
    if not _is_authorized(update):
        return
    try:
        position = fetch_jupiter_position()
        sol_price = fetch_sol_price() or (position or {}).get("mark_price")
        # Allow custom prices e.g. /pricezones 90 70 55
        zone_prices = None
        if context.args:
            try:
                zone_prices = [float(a) for a in context.args]
            except ValueError:
                pass
        pz = calc_price_zones(position, sol_price, zone_prices=zone_prices or PRICE_ZONE_LEVELS)
        msg = format_price_zones(pz)
        await update.effective_message.reply_text(msg, parse_mode="HTML", disable_web_page_preview=True)
    except Exception as exc:
        logging.warning("cmd_price_zones error: %s", exc)
        await update.effective_message.reply_text("❌ Error calculating price zones.")


async def send_daily_news_digest(context):
    """Scheduled job — sends full morning crypto digest at 9:00 UTC."""
    try:
        position = fetch_jupiter_position()
        msgs = get_news_digest(position=position, force=True)
        for msg in msgs:
            if msg.strip():
                await context.bot.send_message(
                    chat_id=int(TELEGRAM_CHAT_ID),
                    text=msg,
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                )
    except Exception as exc:
        logging.warning("send_daily_news_digest error: %s", exc)


async def _run_sol_correlation_update(context):
    """Scheduled job — update SOL/memecoin rolling correlations daily at 10:00 UTC."""
    try:
        updated = update_sol_correlations()
        logging.info("SOL correlation update complete: %d symbols", updated)
    except Exception as exc:
        logging.warning("_run_sol_correlation_update error: %s", exc)


async def run_intraday_news_check(context):
    """Scheduled job — checks for new headlines every 3h, sends update if any found."""
    try:
        position = fetch_jupiter_position()
        msg = check_news_updates(position=position)
        if msg:
            await context.bot.send_message(
                chat_id=int(TELEGRAM_CHAT_ID),
                text=msg,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
    except Exception as exc:
        logging.warning("run_intraday_news_check error: %s", exc)


async def cmd_news(update, context):
    """Handler for /news — send crypto digest on demand."""
    if not _is_authorized(update):
        return
    try:
        await update.effective_message.reply_text("⏳ Fetching crypto digest...")
        position = fetch_jupiter_position()
        # Force refresh if user explicitly calls /news
        msgs = get_news_digest(position=position, force=True)
        for msg in msgs:
            if msg.strip():
                await update.effective_message.reply_text(
                    msg, parse_mode="HTML", disable_web_page_preview=True
                )
    except Exception as exc:
        logging.exception("cmd_news error: %s", exc)
        await update.effective_message.reply_text("⚠️ News digest failed — check logs.")


async def send_daily_sol_report(context):
    """Scheduled job — sends daily SOL position report at 8am EST (13:00 UTC)."""
    try:
        from datetime import datetime, timezone as _tz
        position = fetch_jupiter_position()
        sol_price = fetch_sol_price()
        if not sol_price and position:
            sol_price = position.get("mark_price")
        vol = fetch_sol_volatility_30d()
        dca_summary = calc_dca_summary(sol_price) if sol_price else None

        sep = "━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        now = datetime.now(_tz.utc).strftime("%Y-%m-%d  %H:%M UTC")

        lines = [
            f"<b>☀️ DAILY SOL REPORT</b>",
            f"<code>{sep}</code>",
            f"<code>🕐 {now}</code>",
            f"<code>{sep}</code>",
        ]

        # ── SOL Price ──────────────────────────────────────────
        if sol_price:
            lines += [
                f"<b>💰 SOL PRICE</b>",
                f"<code>{sep}</code>",
                f"<code>Mark price     : ${sol_price:.2f}</code>",
            ]
        else:
            lines += [f"<b>💰 SOL PRICE</b>", f"<code>N/A</code>"]

        # ── Volatility regime ──────────────────────────────────
        if vol is not None:
            if vol > 12.0:
                vol_label = "HIGH ⚠️"
                vol_emoji = "🔴"
            elif vol > 8.0:
                vol_label = "MODERATE"
                vol_emoji = "🟡"
            else:
                vol_label = "LOW"
                vol_emoji = "🟢"
            lines.append(f"<code>{vol_emoji} Vol regime    : {vol:.1f}% ({vol_label})</code>")

        # ── Open position ──────────────────────────────────────
        lines += [f"<code>{sep}</code>", f"<b>⚡ POSITION</b>", f"<code>{sep}</code>"]
        if position:
            mark  = position.get("mark_price") or sol_price or 0
            entry = position.get("entry_price", 0)
            lev   = position.get("leverage", 0)
            pnl   = position.get("pnl", 0)
            liq   = position.get("liq_price", 0)
            fund  = position.get("funding_rate", 0)
            size  = position.get("size_usd", 0)
            coll  = position.get("collateral", 0)
            from jupiter_perps import calc_liq_distance_pct as _liq_dist
            liq_d = _liq_dist(mark, liq)
            pnl_emoji = "🟢" if pnl >= 0 else "🔴"
            liq_emoji = "🔴" if (liq_d and liq_d < 20) else ("🟡" if (liq_d and liq_d < 35) else "🟢")
            lines += [
                f"<code>Entry price    : ${entry:.2f}</code>",
                f"<code>Mark price     : ${mark:.2f}</code>",
                f"<code>Leverage       : {lev:.2f}x</code>",
                f"<code>Size           : ${size:,.2f}</code>",
                f"<code>Collateral     : ${coll:,.2f}</code>",
                f"<code>{pnl_emoji} PnL           : ${pnl:+,.2f}</code>",
                f"<code>{liq_emoji} Liq distance  : {liq_d:.1f}% (liq ${liq:.2f})</code>" if liq_d else f"<code>Liq distance   : N/A</code>",
                f"<code>💸 Funding rate : {fund:.4f}%/day</code>" if fund else "",
            ]
        else:
            lines.append(f"<code>No open position found</code>")

        # ── DCA tracker ────────────────────────────────────────
        if dca_summary and dca_summary.get("count", 0) > 0 and sol_price:
            avg  = dca_summary["avg_cost"]
            dpnl = dca_summary["pnl"]
            dpct = dca_summary["pnl_pct"]
            be   = dca_summary["breakeven"]
            pnl_emoji = "🟢" if dpnl >= 0 else "🔴"
            lines += [
                f"<code>{sep}</code>",
                f"<b>💰 DCA TRACKER</b>",
                f"<code>{sep}</code>",
                f"<code>Avg cost       : ${avg:.2f}</code>",
                f"<code>Breakeven      : ${be:.2f}</code>",
                f"<code>{pnl_emoji} DCA PnL       : ${dpnl:+,.2f} ({dpct:+.1f}%)</code>",
                f"<code>Next zones     : ${dca_summary['dca_zones'][0]:.2f} / ${dca_summary['dca_zones'][1]:.2f} / ${dca_summary['dca_zones'][2]:.2f}</code>",
            ]

        lines.append(f"<code>{sep}</code>")

        # filter empty lines
        msg = "\n".join(l for l in lines if l)
        await context.bot.send_message(
            chat_id=int(TELEGRAM_CHAT_ID),
            text=msg,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    except Exception as exc:
        logging.warning("send_daily_sol_report error: %s", exc)


async def run_lev_monitor(context):
    """Scheduled job — checks alerts every CHECK_INTERVAL_SECONDS."""
    try:
        position = fetch_jupiter_position()
        sol_price = fetch_sol_price() if not position else None

        # Check price/liq/funding alerts
        alerts = check_alerts(position) if position else []
        for _, msg in alerts:
            await context.bot.send_message(
                chat_id=int(TELEGRAM_CHAT_ID),
                text=msg,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )

    except Exception as exc:
        logging.warning("run_lev_monitor error: %s", exc)


async def cmd_sniper(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show tokens currently in second-leg territory (75-95% below ATH)."""
    if not _is_authorized(update):
        await _reject_unauthorized(update)
        return
    try:
        from utils.ath_tracker import get_second_leg_candidates, get_ath
        from utils.db import get_conn
        sep = "━━━━━━━━━━━━━━━━━━━━━━━━━━━"

        # Get prime second-leg candidates (75%+ drawdown)
        candidates = get_second_leg_candidates(min_drawdown_pct=75.0, limit=20)

        # Also get honorable mentions from DB (60-74% drawdown)
        honorable = []
        try:
            with get_conn() as conn:
                rows = conn.execute(
                    """SELECT mint, symbol, ath_price, last_price, pct_from_ath, leg, ath_ts_utc
                       FROM token_ath
                       WHERE pct_from_ath >= 0.60 AND pct_from_ath < 0.75
                       ORDER BY pct_from_ath DESC LIMIT 5"""
                ).fetchall()
                honorable = [
                    {
                        "symbol": r[1], "ath_price": r[2], "last_price": r[3],
                        "drawdown_pct": round(r[4] * 100, 1), "leg": r[5], "ath_ts_utc": r[6],
                    }
                    for r in rows
                ]
        except Exception:
            pass

        lines = [
            f"<b>🎯 SNIPER WATCHLIST — Second Leg Candidates</b>",
            f"<code>{sep}</code>",
        ]

        if not candidates:
            lines.append("<code>No second-leg candidates yet.</code>")
            lines.append("<code>Engine needs more scan cycles to build ATH history.</code>")
            lines.append("<code>Check back in 24-48 hours.</code>")
        else:
            lines.append(f"<code>🔴 PRIME ZONE (75-95% below ATH) — {len(candidates)} tokens</code>")
            lines.append(f"<code>{sep}</code>")
            for i, c in enumerate(candidates[:10], 1):
                dd = c.get('drawdown_pct', 0)
                sym = c.get('symbol', '?')
                ath = c.get('ath_price', 0)
                last = c.get('last_price', 0)
                # Depth indicator
                if dd >= 90:
                    depth = "🔥🔥"
                elif dd >= 85:
                    depth = "🔥"
                else:
                    depth = "📍"
                ath_str = f"${ath:.6f}" if ath < 0.01 else f"${ath:.4f}"
                last_str = f"${last:.6f}" if last < 0.01 else f"${last:.4f}"
                lines.append(
                    f"<code>{i:2}. {depth} {sym:<10} ↓{dd:.0f}%  ATH:{ath_str} Now:{last_str}</code>"
                )

        if honorable:
            lines.append(f"<code>{sep}</code>")
            lines.append(f"<code>🟡 APPROACHING (60-74% below ATH)</code>")
            for c in honorable:
                dd = c.get('drawdown_pct', 0)
                sym = c.get('symbol', '?')
                lines.append(f"<code>   📊 {sym:<10} ↓{dd:.0f}%</code>")

        lines.append(f"<code>{sep}</code>")
        lines.append(f"<code>Strategy: Ape conviction bags 80-90% below ATH</code>")
        lines.append(f"<code>Wait for volume + social confirmation before entry</code>")

        await update.effective_message.reply_text(
            "\n".join(lines),
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    except Exception as exc:
        await update.effective_message.reply_text(f"❌ Sniper error: {exc}")


def main():
    _require_env()
    init_db()
    ensure_correlations_table()  # Elite Feature 3: create sol_correlations table if needed
    _load_watchlist_state()
    lock = SingletonProcessLock(PROCESS_LOCK_FILE)
    if not lock.acquire():
        logging.error("Another engine instance is running. Exiting (lock: %s).", PROCESS_LOCK_FILE)
        return

    sell_rules = _sell_style_rules()
    logging.info(
        "Startup config: ENGINE_PROFILE=%s RISK_STYLE=%s ALERT_THRESHOLD=%s MIN_CONFIDENCE_TO_ALERT=%s "
        "ENABLE_REGIME_GATE=%s REGIME_MIN_SCORE=%s DRY_RUN=%s "
        "WEEKLY_TUNING_ENABLED=%s WEEKLY_TUNING_DAY_UTC=%s WEEKLY_TUNING_HOUR_UTC=%s "
        "OUTCOME_TRACKING_ENABLED=%s OUTCOME_EVAL_INTERVAL_SECONDS=%s "
        "ALERT_TOP_N=%s MAX_ALERTS_PER_CYCLE=%s ALERT_COOLDOWN_HOURS=%s "
        "ENABLE_RISK_GOVERNOR=%s GLOBAL_TRADING_PAUSE=%s MAX_ALERTS_PER_DAY=%s "
        "MAX_ALERTS_PER_SYMBOL_PER_DAY=%s MAX_CONSECUTIVE_4H_LOSSES=%s "
        "LOSS_STREAK_LOOKBACK_HOURS=%s ENABLE_EXECUTION_QUALITY_FILTERS=%s "
        "TACTICAL_ENABLE_REAL_TECHNICALS=%s TACTICAL_REQUIRE_TECHNICAL_CONFIRMATION=%s "
        "TACTICAL_OHLCV_TYPE=%s TACTICAL_RSI_RANGE=%s-%s TACTICAL_MACD_HIST_MIN=%s "
        "ALERT_DATA_REFRESH_ENABLED=%s ALERT_REQUIRE_REFRESH_SUCCESS=%s ALERT_DATA_DRIFT_WARN_PCT=%s "
        "ALERT_HIDE_UNVERIFIED_HOLDERS=%s ENABLE_SOL_REGIME_LAYER=%s "
        "BEARISH_ALERT_THRESHOLD=%s BEARISH_MIN_CONFIDENCE_TO_ALERT=%s "
        "BEARISH_MAX_ALERTS_PER_CYCLE=%s BEARISH_MAX_ALERTS_PER_DAY=%s "
        "BEARISH_ALERT_COOLDOWN_HOURS=%s ENABLE_EXTREME_RISK_HARD_BLOCK=%s "
        "NEW_RUNNER_WATCH_ENABLED=%s NEW_RUNNER_SCAN_INTERVAL_SECONDS=%s "
        "NEW_RUNNER_MIN_MARKET_CAP=%s NEW_RUNNER_MAX_AGE_HOURS=%s "
        "NEW_RUNNER_MIN_VOLUME_24H=%s NEW_RUNNER_MIN_LIQUIDITY=%s "
        "NEW_RUNNER_MIN_ALERT_SCORE=%s NEW_RUNNER_COOLDOWN_HOURS=%s "
        "WATCHLIST_LANE_ENABLED=%s WATCHLIST_ENTRIES=%s WATCHLIST_SCAN_INTERVAL_SECONDS=%s "
        "WATCHLIST_MAX_ALERTS_PER_CYCLE=%s WATCHLIST_ALERT_COOLDOWN_HOURS=%s "
        "WATCHLIST_ALERT_ON_STATUS_CHANGE=%s WATCHLIST_ALERT_STATUSES=%s "
        "WATCHLIST_SUMMARY_ENABLED=%s WATCHLIST_SUMMARY_HOUR_UTC=%s "
        "ANALYSIS_BULLETIN_ENABLED=%s ANALYSIS_BULLETIN_INTERVAL=%s "
        "SELL_ALERTS_ENABLED=%s SELL_ALERT_MAX_PER_CYCLE=%s SELL_ALERT_COOLDOWN_HOURS=%s "
        "SELL_EFFECTIVE_MAX_PER_CYCLE=%s SELL_EFFECTIVE_COOLDOWN_HOURS=%s "
        "LEGACY_RECOVERY_ENABLED=%s LEGACY_RECOVERY_SCAN_INTERVAL_SECONDS=%s "
        "LEGACY_RECOVERY_MIN_AGE_DAYS=%s LEGACY_RECOVERY_MIN_LIQUIDITY=%s "
        "LEGACY_RECOVERY_MIN_VOLUME_24H=%s LEGACY_RECOVERY_MAX_ALERTS_PER_CYCLE=%s "
        "LEGACY_RECOVERY_COOLDOWN_HOURS=%s",
        _mode(),
        _normalize_risk_style(_runtime.get("risk_style")),
        _runtime["alert_threshold"],
        _runtime["min_confidence_to_alert"],
        ENABLE_REGIME_GATE,
        _runtime["regime_min_score"],
        DRY_RUN,
        WEEKLY_TUNING_ENABLED,
        WEEKLY_TUNING_DAY_UTC,
        WEEKLY_TUNING_HOUR_UTC,
        OUTCOME_TRACKING_ENABLED,
        OUTCOME_EVAL_INTERVAL_SECONDS,
        ALERT_TOP_N,
        MAX_ALERTS_PER_CYCLE,
        _runtime["alert_cooldown_hours"],
        ENABLE_RISK_GOVERNOR,
        GLOBAL_TRADING_PAUSE,
        MAX_ALERTS_PER_DAY,
        MAX_ALERTS_PER_SYMBOL_PER_DAY,
        MAX_CONSECUTIVE_4H_LOSSES,
        LOSS_STREAK_LOOKBACK_HOURS,
        ENABLE_EXECUTION_QUALITY_FILTERS,
        TACTICAL_ENABLE_REAL_TECHNICALS,
        TACTICAL_REQUIRE_TECHNICAL_CONFIRMATION,
        TACTICAL_OHLCV_TYPE,
        TACTICAL_RSI_MIN,
        TACTICAL_RSI_MAX,
        TACTICAL_MACD_HIST_MIN,
        ALERT_DATA_REFRESH_ENABLED,
        ALERT_REQUIRE_REFRESH_SUCCESS,
        ALERT_DATA_DRIFT_WARN_PCT,
        ALERT_HIDE_UNVERIFIED_HOLDERS,
        ENABLE_SOL_REGIME_LAYER,
        BEARISH_ALERT_THRESHOLD,
        BEARISH_MIN_CONFIDENCE_TO_ALERT,
        BEARISH_MAX_ALERTS_PER_CYCLE,
        BEARISH_MAX_ALERTS_PER_DAY,
        BEARISH_ALERT_COOLDOWN_HOURS,
        ENABLE_EXTREME_RISK_HARD_BLOCK,
        NEW_RUNNER_WATCH_ENABLED,
        NEW_RUNNER_SCAN_INTERVAL_SECONDS,
        NEW_RUNNER_MIN_MARKET_CAP,
        NEW_RUNNER_MAX_AGE_HOURS,
        NEW_RUNNER_MIN_VOLUME_24H,
        NEW_RUNNER_MIN_LIQUIDITY,
        NEW_RUNNER_MIN_ALERT_SCORE,
        NEW_RUNNER_COOLDOWN_HOURS,
        WATCHLIST_LANE_ENABLED,
        len(WATCHLIST_ENTRIES),
        WATCHLIST_SCAN_INTERVAL_SECONDS,
        WATCHLIST_MAX_ALERTS_PER_CYCLE,
        WATCHLIST_ALERT_COOLDOWN_HOURS,
        WATCHLIST_ALERT_ON_STATUS_CHANGE,
        ",".join(WATCHLIST_ALERT_STATUSES),
        WATCHLIST_SUMMARY_ENABLED,
        WATCHLIST_SUMMARY_HOUR_UTC,
        GOOD_BUY_BULLETIN_ENABLED,
        GOOD_BUY_BULLETIN_INTERVAL_SECONDS,
        SELL_ALERTS_ENABLED,
        SELL_ALERT_MAX_PER_CYCLE,
        SELL_ALERT_COOLDOWN_HOURS,
        sell_rules["max_per_cycle"],
        sell_rules["cooldown_hours"],
        LEGACY_RECOVERY_ENABLED,
        LEGACY_RECOVERY_SCAN_INTERVAL_SECONDS,
        LEGACY_RECOVERY_MIN_AGE_DAYS,
        LEGACY_RECOVERY_MIN_LIQUIDITY,
        LEGACY_RECOVERY_MIN_VOLUME_24H,
        LEGACY_RECOVERY_MAX_ALERTS_PER_CYCLE,
        LEGACY_RECOVERY_COOLDOWN_HOURS,
    )
    try:
        global _app_ref
        app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
        _app_ref = app

        app.add_handler(CommandHandler("help", cmd_help))
        app.add_handler(CommandHandler("status", cmd_status))
        app.add_handler(CommandHandler("mode", cmd_mode))
        app.add_handler(CommandHandler("riskprofile", cmd_riskprofile))
        app.add_handler(CommandHandler("risk", cmd_risk))
        app.add_handler(CommandHandler("pause", cmd_pause))
        app.add_handler(CommandHandler("resume", cmd_resume))
        app.add_handler(CommandHandler("performance", cmd_performance))
        app.add_handler(CommandHandler("week", cmd_week))
        app.add_handler(CommandHandler("journal", cmd_journal))
        app.add_handler(CommandHandler("buy", cmd_buy))
        app.add_handler(CommandHandler("sold", cmd_sold))
        app.add_handler(CommandHandler("positions", cmd_positions))
        app.add_handler(CommandHandler("digest", cmd_digest))
        app.add_handler(CommandHandler("marketnow", cmd_marketnow))
        app.add_handler(CommandHandler("goodbuy", cmd_goodbuy))
        app.add_handler(CommandHandler("badbuy", cmd_badbuy))
        app.add_handler(CommandHandler("wallet2now", cmd_wallet2now))
        app.add_handler(CommandHandler("wallet2bad", cmd_wallet2bad))
        app.add_handler(CommandHandler("wallet3now", cmd_wallet3now))
        app.add_handler(CommandHandler("wallet3bad", cmd_wallet3bad))
        app.add_handler(CommandHandler("walletplan", cmd_walletplan))
        app.add_handler(CommandHandler("runnerwatch", cmd_runnerwatch))
        app.add_handler(CommandHandler("watchlist", cmd_watchlist))
        app.add_handler(CommandHandler("watchlistsummary", cmd_watchlistsummary))
        app.add_handler(CallbackQueryHandler(on_alert_action, pattern=r"^(mute|ack):"))

        app.add_handler(CommandHandler("recap", cmd_recap))
        app.add_handler(CommandHandler("dca", cmd_dca))
        app.add_handler(CommandHandler("news", cmd_news))
        app.add_handler(CommandHandler("pnl", cmd_pnl))
        app.add_handler(CommandHandler("cooldowns", cmd_cooldowns))
        app.add_handler(CommandHandler("snapshot", cmd_snapshot))

        # Jupiter Perps Leverage Assistant
        app.add_handler(CommandHandler("lev", cmd_lev))
        app.add_handler(CommandHandler("levstatus", cmd_lev_status))
        app.add_handler(CommandHandler("whatif", cmd_what_if))
        app.add_handler(CommandHandler("levrec", cmd_lev_rec))
        app.add_handler(CommandHandler("pricezones", cmd_price_zones))
        app.add_handler(CommandHandler("sniper", cmd_sniper))
        app.job_queue.run_repeating(
            run_lev_monitor,
            interval=CHECK_INTERVAL_SECONDS,
            first=60,
            name="lev_monitor",
            job_kwargs={"misfire_grace_time": 60, "coalesce": True},
        )
        # Daily SOL report — 8am EST = 13:00 UTC
        app.job_queue.run_daily(
            send_daily_sol_report,
            time=time(hour=13, minute=0, tzinfo=timezone.utc),
            name="daily_sol_report",
            job_kwargs={"misfire_grace_time": 1800, "coalesce": True},
        )
        # Daily crypto news digest — 9:00 UTC
        app.job_queue.run_daily(
            send_daily_news_digest,
            time=time(hour=9, minute=0, tzinfo=timezone.utc),
            name="daily_news_digest",
            job_kwargs={"misfire_grace_time": 1800, "coalesce": True},
        )
        # Intraday news check — every 3 hours, first run 3h after startup
        app.job_queue.run_repeating(
            run_intraday_news_check,
            interval=3 * 3600,
            first=3 * 3600,
            name="intraday_news_check",
            job_kwargs={"misfire_grace_time": 1800, "coalesce": True},
        )
        # Elite Feature 3: Daily SOL correlation update — 10:00 UTC
        app.job_queue.run_daily(
            _run_sol_correlation_update,
            time=time(hour=10, minute=0, tzinfo=timezone.utc),
            name="sol_correlation_update",
            job_kwargs={"misfire_grace_time": 3600, "coalesce": True},
        )

        app.job_queue.run_once(
            run_engine,
            when=1,
            name="run_engine_boot",
            job_kwargs={"misfire_grace_time": 30},
        )
        app.job_queue.run_repeating(
            run_engine,
            interval=int(_runtime["scan_interval_seconds"]),
            first=int(_runtime["scan_interval_seconds"]),
            name="run_engine_cycle",
            job_kwargs={"misfire_grace_time": 30, "coalesce": True},
        )
        if NEW_RUNNER_WATCH_ENABLED:
            app.job_queue.run_once(
                run_new_runner_watch,
                when=15,
                name="runner_watch_boot",
                job_kwargs={"misfire_grace_time": 60},
            )
            app.job_queue.run_repeating(
                run_new_runner_watch,
                interval=max(300, int(NEW_RUNNER_SCAN_INTERVAL_SECONDS)),
                first=max(300, int(NEW_RUNNER_SCAN_INTERVAL_SECONDS)),
                name="runner_watch_cycle",
                job_kwargs={"misfire_grace_time": 120, "coalesce": True},
            )
        if LEGACY_RECOVERY_ENABLED:
            app.job_queue.run_once(
                run_legacy_recovery_scanner,
                when=35,
                name="legacy_recovery_boot",
                job_kwargs={"misfire_grace_time": 60},
            )
            app.job_queue.run_repeating(
                run_legacy_recovery_scanner,
                interval=max(300, int(LEGACY_RECOVERY_SCAN_INTERVAL_SECONDS)),
                first=max(300, int(LEGACY_RECOVERY_SCAN_INTERVAL_SECONDS)),
                name="legacy_recovery_cycle",
                job_kwargs={"misfire_grace_time": 120, "coalesce": True},
            )
        if WATCHLIST_LANE_ENABLED and WATCHLIST_ENTRIES:
            app.job_queue.run_once(
                run_watchlist_lane,
                when=25,
                name="watchlist_boot",
                job_kwargs={"misfire_grace_time": 60},
            )
            app.job_queue.run_repeating(
                run_watchlist_lane,
                interval=max(300, int(WATCHLIST_SCAN_INTERVAL_SECONDS)),
                first=max(300, int(WATCHLIST_SCAN_INTERVAL_SECONDS)),
                name="watchlist_cycle",
                job_kwargs={"misfire_grace_time": 120, "coalesce": True},
            )
        if OUTCOME_TRACKING_ENABLED:
            app.job_queue.run_repeating(
                run_outcome_evaluator,
                interval=OUTCOME_EVAL_INTERVAL_SECONDS,
                first=OUTCOME_EVAL_INTERVAL_SECONDS,
                name="outcome_evaluator",
                job_kwargs={"misfire_grace_time": 120, "coalesce": True},
            )
        # ── Auto-execution position monitor ────────────────────────────────
        import asyncio as _asyncio_startup
        import os as _os_startup
        if _os_startup.getenv("EXECUTOR_ENABLED", "false").lower() == "true":
            try:
                from utils.executor import position_monitor_loop as _pos_monitor
                _asyncio_startup.create_task(_pos_monitor())
                logging.info("Auto-executor position monitor started")
            except Exception as _exec_start_err:
                logging.warning("Could not start position monitor: %s", _exec_start_err)
        # ── Launch listener — real-time new token detection ─────────────────
        if _os_startup.getenv("LAUNCH_LISTENER_ENABLED", "false").lower() == "true":
            try:
                from utils.launch_listener import launch_listener_main as _launch_main
                _asyncio_startup.create_task(_launch_main(app, TELEGRAM_CHAT_ID))
                logging.info("Launch listener started (Pump.fun WS + DexScreener poll)")
            except Exception as _launch_start_err:
                logging.warning("Could not start launch listener: %s", _launch_start_err)
        # ── Arb monitor loop — cross-DEX price spread detection ──────────────
        if _os_startup.getenv("ARB_ENABLED", "false").lower() == "true":
            try:
                from utils.dex_price_monitor import arb_monitor_loop as _arb_loop
                _asyncio_startup.create_task(_arb_loop(app))
                logging.info("Arb monitor loop started (ARB_ENABLED=true)")
            except Exception as _arb_start_err:
                logging.warning("Could not start arb monitor loop: %s", _arb_start_err)
        if SIGNAL_DIGEST_ENABLED:
            app.job_queue.run_repeating(
                send_signal_digest,
                interval=SIGNAL_DIGEST_INTERVAL_SECONDS,
                first=SIGNAL_DIGEST_INTERVAL_SECONDS,
                name="signal_digest",
                job_kwargs={"misfire_grace_time": 120, "coalesce": True},
            )
        if GOOD_BUY_BULLETIN_ENABLED:
            bulletin_interval = max(900, int(GOOD_BUY_BULLETIN_INTERVAL_SECONDS))
            bulletin_boot = max(15, min(int(GOOD_BUY_BULLETIN_BOOT_SECONDS), bulletin_interval))
            app.job_queue.run_once(
                send_good_buy_bulletin,
                when=bulletin_boot,
                name="good_buy_bulletin_boot",
                job_kwargs={"misfire_grace_time": 120},
            )
            app.job_queue.run_repeating(
                send_good_buy_bulletin,
                interval=bulletin_interval,
                first=bulletin_interval,
                name="good_buy_bulletin",
                job_kwargs={"misfire_grace_time": 300, "coalesce": True},
            )

        if DAILY_SUMMARY_ENABLED:
            summary_hour = max(0, min(23, DAILY_SUMMARY_HOUR_UTC))
            app.job_queue.run_daily(
                send_daily_summary,
                time=time(hour=summary_hour, minute=0, tzinfo=timezone.utc),
                name="daily_summary",
                job_kwargs={"misfire_grace_time": 1800, "coalesce": True},
            )
        if WEEKLY_TUNING_ENABLED:
            weekly_hour = max(0, min(23, WEEKLY_TUNING_HOUR_UTC))
            weekly_day = _DAY_TO_WEEKDAY.get(WEEKLY_TUNING_DAY_UTC, 6)
            app.job_queue.run_daily(
                send_weekly_tuning_report,
                time=time(hour=weekly_hour, minute=5, tzinfo=timezone.utc),
                days=(weekly_day,),
                name="weekly_tuning",
                job_kwargs={"misfire_grace_time": 3600, "coalesce": True},
            )
        if WATCHLIST_SUMMARY_ENABLED and WATCHLIST_LANE_ENABLED and WATCHLIST_ENTRIES:
            watch_summary_hour = max(0, min(23, WATCHLIST_SUMMARY_HOUR_UTC))
            app.job_queue.run_daily(
                send_watchlist_summary,
                time=time(hour=watch_summary_hour, minute=10, tzinfo=timezone.utc),
                name="watchlist_summary",
                job_kwargs={"misfire_grace_time": 1800, "coalesce": True},
            )

        print("Engine started...")
        app.run_polling()
    finally:
        lock.release()


if __name__ == "__main__":
    main()
