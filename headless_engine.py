"""
headless_engine.py — Runs the memecoin engine WITHOUT Telegram.

All scan functions from main.py expect a `context` object with a `.bot`
that can call `send_message(...)`.  We provide a FakeBot whose every
method is a silent async no-op so all existing code works unchanged.

Scheduling is handled by APScheduler (already installed) instead of the
Telegram JobQueue, so there is no Telegram Application, no polling, and
no bot token needed at all.

Usage (systemd):
    ExecStart=/usr/bin/python3 /root/memecoin_engine/headless_engine.py
"""

import asyncio
import json
import logging
import os
import sqlite3
import sys
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

# ── Logging setup (mirrors main.py) ──────────────────────────────────────────
LOG_DIR = Path(os.getenv("LOG_DIR", "logs"))
LOG_DIR.mkdir(exist_ok=True)

_fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
_fh  = RotatingFileHandler(LOG_DIR / "headless_engine.log", maxBytes=10_000_000, backupCount=3)
_fh.setFormatter(_fmt)
_sh  = logging.StreamHandler(sys.stdout)
_sh.setFormatter(_fmt)
logging.basicConfig(level=logging.INFO, handlers=[_fh, _sh])
log = logging.getLogger("headless_engine")

# ── Fake Telegram objects ─────────────────────────────────────────────────────
class FakeBot:
    """Silent async no-op replacement for telegram.Bot."""

    async def send_message(self, *args, **kwargs):  pass
    async def send_photo(self, *args, **kwargs):    pass
    async def send_document(self, *args, **kwargs): pass
    async def send_animation(self, *args, **kwargs):pass
    async def send_sticker(self, *args, **kwargs):  pass
    async def edit_message_text(self, *args, **kwargs): pass
    async def answer_callback_query(self, *args, **kwargs): pass
    async def pin_message(self, *args, **kwargs):   pass
    async def unpin_all_chat_messages(self, *args, **kwargs): pass
    async def get_me(self, *args, **kwargs):        return None
    async def delete_message(self, *args, **kwargs):pass
    async def send_chat_action(self, *args, **kwargs): pass
    # Catch-all for any other bot method
    def __getattr__(self, name):
        async def _noop(*a, **kw): pass
        return _noop


class FakeJob:
    """Minimal job stub — some callbacks call context.job."""
    name = "headless"
    data = None
    chat_id = None


class FakeContext:
    """Mimics telegram.ext.CallbackContext."""
    def __init__(self):
        self.bot      = FakeBot()
        self.job      = FakeJob()
        self.args     = []
        self.user_data: dict = {}
        self.chat_data: dict = {}
        self.bot_data:  dict = {}
        # Some functions read context.job_queue — give it a no-op stub
        self.job_queue = None

    # Some helpers check context.application
    @property
    def application(self):
        return self


# ── Import core scan functions from main.py ───────────────────────────────────
# We do this AFTER setting up logging so any module-level logging in main.py
# flows through our handlers.  main.py will import telegram at the module
# level but will NOT call app.run_polling() because __name__ != "__main__".
log.info("Loading main.py module (this may take a few seconds)…")
try:
    # Temporarily spoof __name__ guard — main.py only calls main() when
    # __name__ == "__main__", so importing it is safe.
    import main as _engine
    log.info("main.py loaded successfully.")
except Exception as _e:
    log.critical("Failed to import main.py: %s", _e, exc_info=True)
    sys.exit(1)

# Pull the functions we need
_FUNCS = {
    "run_engine":                  getattr(_engine, "run_engine",                  None),
    "run_watchlist_lane":          getattr(_engine, "run_watchlist_lane",          None),
    "run_new_runner_watch":        getattr(_engine, "run_new_runner_watch",        None),
    "run_legacy_recovery_scanner": getattr(_engine, "run_legacy_recovery_scanner", None),
    "run_lev_monitor":             getattr(_engine, "run_lev_monitor",             None),
    "run_outcome_evaluator":       getattr(_engine, "run_outcome_evaluator",       None),
    "_run_sol_correlation_update": getattr(_engine, "_run_sol_correlation_update", None),
}
for _name, _fn in _FUNCS.items():
    if _fn is None:
        log.warning("Function %s not found in main.py — skipping.", _name)
    else:
        log.info("  ✓ %s", _name)

# ── Pull interval config from main.py (already imported from config) ──────────
def _cfg(name: str, default: int) -> int:
    return int(getattr(_engine, name, None) or default)


def _env_bool(name: str, default: bool = True) -> bool:
    return os.getenv(name, "true" if default else "false").lower() in ("1", "true", "yes")


def _ensure_dashboard_backend_path() -> None:
    backend_root = Path(__file__).resolve().parent / "dashboard" / "backend"
    backend_root_str = str(backend_root)
    if backend_root_str not in sys.path:
        sys.path.insert(0, backend_root_str)

SCAN_INTERVAL_SECONDS               = _cfg("SCAN_INTERVAL_SECONDS",               3600)
WATCHLIST_SCAN_INTERVAL_SECONDS     = _cfg("WATCHLIST_SCAN_INTERVAL_SECONDS",     1800)
NEW_RUNNER_SCAN_INTERVAL_SECONDS    = _cfg("NEW_RUNNER_SCAN_INTERVAL_SECONDS",    1800)
LEGACY_RECOVERY_SCAN_INTERVAL_SECONDS = _cfg("LEGACY_RECOVERY_SCAN_INTERVAL_SECONDS", 1800)
OUTCOME_EVAL_INTERVAL_SECONDS       = _cfg("OUTCOME_EVAL_INTERVAL_SECONDS",       3600)
CHECK_INTERVAL_SECONDS              = _cfg("CHECK_INTERVAL_SECONDS",               60)
MEMECOIN_SCAN_INTERVAL_SECONDS     = max(300, int(os.getenv("MEMECOIN_SCAN_INTERVAL_SECONDS", "300")))
PIPELINE_WATCHDOG_ENABLED          = _env_bool("PIPELINE_WATCHDOG_ENABLED",       True)
PIPELINE_WATCHDOG_INTERVAL_SECONDS = max(300, int(os.getenv("PIPELINE_WATCHDOG_INTERVAL_SECONDS", "3600")))
PIPELINE_STALE_HOURS               = max(1.0, float(os.getenv("PIPELINE_STALE_HOURS", "4")))
ALLOCATOR_CHECK_ENABLED            = _env_bool("ALLOCATOR_CHECK_ENABLED",         True)
ALLOCATOR_CHECK_INTERVAL_SECONDS   = max(900, int(os.getenv("ALLOCATOR_CHECK_INTERVAL_SECONDS", "3600")))
SPECULATION_HEAT_ENABLED          = _env_bool("SPECULATION_HEAT_ENABLED",        True)
SPECULATION_HEAT_INTERVAL_SECONDS = max(900, int(os.getenv("SPECULATION_HEAT_INTERVAL_SECONDS", "1800")))
TOKEN_STATS_STREAM_ENABLED        = _env_bool("TOKEN_STATS_STREAM_ENABLED",      True)
TOKEN_STATS_SYNC_INTERVAL_SECONDS = max(60, int(os.getenv("TOKEN_STATS_SYNC_INTERVAL_SECONDS", "120")))
TOKEN_INTELLIGENCE_ENABLED        = _env_bool("TOKEN_INTELLIGENCE_ENABLED",      True)
TOKEN_INTELLIGENCE_INTERVAL_SECONDS = max(120, int(os.getenv("TOKEN_INTELLIGENCE_INTERVAL_SECONDS", "300")))
LARGE_TRADE_STREAM_ENABLED       = _env_bool("LARGE_TRADE_STREAM_ENABLED",      True)
LARGE_TRADE_SYNC_INTERVAL_SECONDS = max(60, int(os.getenv("LARGE_TRADE_SYNC_INTERVAL_SECONDS", "120")))
WALLET_TX_STREAM_ENABLED         = _env_bool("WALLET_TX_STREAM_ENABLED",        True)
WALLET_TX_SYNC_INTERVAL_SECONDS  = max(60, int(os.getenv("WALLET_TX_SYNC_INTERVAL_SECONDS", "180")))
PAPER_SIGNAL_LEARNING_ENABLED   = _env_bool("PAPER_SIGNAL_LEARNING_ENABLED",   True)
PAPER_SIGNAL_LEARNING_INTERVAL_SECONDS = max(120, int(os.getenv("PAPER_SIGNAL_LEARNING_INTERVAL_SECONDS", "300")))
RUNNER_REVIEW_OUTCOME_ENABLED   = _env_bool("RUNNER_REVIEW_OUTCOME_ENABLED",   True)
RUNNER_REVIEW_OUTCOME_INTERVAL_SECONDS = max(300, int(os.getenv("RUNNER_REVIEW_OUTCOME_INTERVAL_SECONDS", "900")))
RUNNER_REVIEW_AUTO_PAPER_ENABLED = _env_bool("RUNNER_REVIEW_AUTO_PAPER_ENABLED", True)
RUNNER_REVIEW_AUTO_PAPER_INTERVAL_SECONDS = max(300, int(os.getenv("RUNNER_REVIEW_AUTO_PAPER_INTERVAL_SECONDS", "300")))
MEMECOIN_RESEARCH_DOSSIER_ENABLED = _env_bool("MEMECOIN_RESEARCH_DOSSIER_ENABLED", True)
MEMECOIN_RESEARCH_DOSSIER_INTERVAL_SECONDS = max(300, int(os.getenv("MEMECOIN_RESEARCH_DOSSIER_INTERVAL_SECONDS", "600")))
MEMECOIN_CATALYST_ENABLED = _env_bool("MEMECOIN_CATALYST_ENABLED", True)
MEMECOIN_CATALYST_INTERVAL_SECONDS = max(300, int(os.getenv("MEMECOIN_CATALYST_INTERVAL_SECONDS", "300")))
NO_BIRDEYE_MODE                 = _env_bool("NO_BIRDEYE_MODE", False) or _env_bool("INDEPENDENT_SOURCE_MODE", False)
ALLOCATOR_SNAPSHOT_MINUTES         = max(30, int(os.getenv("ALLOCATOR_SNAPSHOT_MINUTES", "360")))
_monitor_cycle: int = 0
AI_ANALYST_ENABLED = _env_bool("AI_ANALYST_ENABLED", True)
AI_ANALYST_INTERVAL_SECONDS = max(1800, int(os.getenv("AI_ANALYST_INTERVAL_SECONDS", "3600")))
HEADLESS_PERP_MONITOR_LOOP_ENABLED = _env_bool("HEADLESS_PERP_MONITOR_LOOP_ENABLED", True)
HEADLESS_PERP_SIGNAL_SCAN_LOOP_ENABLED = _env_bool("HEADLESS_PERP_SIGNAL_SCAN_LOOP_ENABLED", True)
HEADLESS_SCALP_MONITOR_LOOP_ENABLED = _env_bool("HEADLESS_SCALP_MONITOR_LOOP_ENABLED", True)
HEADLESS_SCALP_SIGNAL_SCAN_LOOP_ENABLED = _env_bool("HEADLESS_SCALP_SIGNAL_SCAN_LOOP_ENABLED", True)
HEADLESS_OUTCOME_TRACKER_LOOP_ENABLED = _env_bool("HEADLESS_OUTCOME_TRACKER_LOOP_ENABLED", True)
HEADLESS_RESEARCH_LOOP_ENABLED   = _env_bool("HEADLESS_RESEARCH_LOOP_ENABLED", True)
HEADLESS_MEMECOIN_DISCOVERY_LOOP_ENABLED = _env_bool("HEADLESS_MEMECOIN_DISCOVERY_LOOP_ENABLED", True)
HEADLESS_RUNNER_HEARTBEAT_LOOP_ENABLED = _env_bool("HEADLESS_RUNNER_HEARTBEAT_LOOP_ENABLED", True)
RUNNER_HEARTBEAT_INTERVAL_SECONDS = max(180, int(os.getenv("RUNNER_HEARTBEAT_INTERVAL_SECONDS", "300")))
HEADLESS_EARLY_RUNNER_RADAR_LOOP_ENABLED = _env_bool("HEADLESS_EARLY_RUNNER_RADAR_LOOP_ENABLED", True)
EARLY_RUNNER_RADAR_INTERVAL_SECONDS = max(60, int(os.getenv("EARLY_RUNNER_RADAR_INTERVAL_SECONDS", "75")))
HEADLESS_AUTHORITY_REFRESH_LOOP_ENABLED = _env_bool("HEADLESS_AUTHORITY_REFRESH_LOOP_ENABLED", True)
HEADLESS_WHALE_WATCH_LOOP_ENABLED = _env_bool("HEADLESS_WHALE_WATCH_LOOP_ENABLED", True)
HEADLESS_SPOT_MONITOR_LOOP_ENABLED = _env_bool("HEADLESS_SPOT_MONITOR_LOOP_ENABLED", True)
HEADLESS_SPOT_SIGNAL_SCAN_LOOP_ENABLED = _env_bool("HEADLESS_SPOT_SIGNAL_SCAN_LOOP_ENABLED", True)
ENGINE_DASHBOARD_SNAPSHOT_WARMER_ENABLED = _env_bool("ENGINE_DASHBOARD_SNAPSHOT_WARMER_ENABLED", True)
ENGINE_DASHBOARD_SNAPSHOT_WARMER_INTERVAL_SECONDS = max(
    180,
    int(os.getenv("ENGINE_DASHBOARD_SNAPSHOT_WARMER_INTERVAL_SECONDS", "300")),
)

log.info(
    "Intervals — engine=%ss  watchlist=%ss  new_runner=%ss  "
    "legacy=%ss  outcome=%ss  lev_monitor=%ss",
    SCAN_INTERVAL_SECONDS, WATCHLIST_SCAN_INTERVAL_SECONDS,
    NEW_RUNNER_SCAN_INTERVAL_SECONDS, LEGACY_RECOVERY_SCAN_INTERVAL_SECONDS,
    OUTCOME_EVAL_INTERVAL_SECONDS, CHECK_INTERVAL_SECONDS,
)

DB_PATH = Path(getattr(_engine, "DB_PATH", Path("data_storage") / "engine.db"))

# ── Feature-flag helpers ──────────────────────────────────────────────────────
WATCHLIST_LANE_ENABLED          = _env_bool("WATCHLIST_LANE_ENABLED",       True)
NEW_RUNNER_WATCH_ENABLED        = _env_bool("NEW_RUNNER_WATCH_ENABLED",     True)
LEGACY_RECOVERY_ENABLED         = _env_bool("LEGACY_RECOVERY_ENABLED",      True)
OUTCOME_TRACKING_ENABLED        = _env_bool("OUTCOME_TRACKING_ENABLED",     True)
WATCHLIST_ENTRIES               = getattr(_engine, "WATCHLIST_ENTRIES", [])

# ── Async task runner ─────────────────────────────────────────────────────────

async def _run(name: str, fn, ctx: FakeContext):
    """Run a scan function safely, logging errors without crashing."""
    try:
        log.debug("→ %s", name)
        await fn(ctx)
        log.debug("← %s done", name)
    except Exception as exc:
        log.warning("%s error: %s", name, exc, exc_info=False)


async def _loop(name: str, fn, interval: int, first_delay: int = 5):
    """Repeatedly call fn(ctx) every `interval` seconds."""
    ctx = FakeContext()
    log.info("[%s] loop starting (interval=%ss, first_delay=%ss)", name, interval, first_delay)
    await asyncio.sleep(first_delay)
    while True:
        await _run(name, fn, ctx)
        await asyncio.sleep(interval)


# ── Perp/Scalp monitor loops (from dashboard backend main.py pattern) ─────────
async def _perp_monitor_loop():
    """Primary 60s maintenance loop owned by the headless runtime."""
    global _monitor_cycle
    while True:
        _monitor_cycle += 1
        try:
            from utils.perp_executor import perp_monitor_step
            await perp_monitor_step()
        except Exception as _e:
            log.debug("perp_monitor_step error: %s", _e)
        try:
            from utils.memecoin_manager import memecoin_monitor_step as _mm_step  # type: ignore
            await asyncio.to_thread(_mm_step)
        except Exception as _e:
            log.debug("memecoin_monitor_step error: %s", _e)
        try:
            from utils.tier_manager import tier_monitor_step as _tier_step  # type: ignore
            await asyncio.to_thread(_tier_step)
        except Exception as _te:
            log.debug("tier_monitor_step error: %s", _te)
        if _monitor_cycle % 5 == 0:
            try:
                from utils.spot_accumulator import spot_monitor_step as _spot_acc_step  # type: ignore
                await asyncio.to_thread(_spot_acc_step)
            except Exception as _sae:
                log.debug("spot_monitor_step error: %s", _sae)
        try:
            from utils.health_monitor import health_watchdog_step as _hw_step  # type: ignore
            await asyncio.to_thread(_hw_step)
        except Exception as _hwe:
            log.debug("health_watchdog_step error: %s", _hwe)
        if _monitor_cycle % 5 == 0:
            try:
                from utils.agent_coordinator import data_integrity_step as _di_step  # type: ignore
                await asyncio.to_thread(_di_step)
            except Exception as _die:
                log.debug("data_integrity_step error: %s", _die)
        if _monitor_cycle % 5 == 0:
            try:
                from utils.whale_watch import whale_watch_outcome_step as _ww_outcome  # type: ignore
                await asyncio.to_thread(_ww_outcome)
            except Exception as _wwe:
                log.debug("whale_watch_outcome_step error: %s", _wwe)
        if _monitor_cycle % 5 == 0:
            try:
                from utils.confluence_engine import confluence_step as _conf_step  # type: ignore
                await asyncio.to_thread(_conf_step)
            except Exception as _ce:
                log.debug("confluence_step error: %s", _ce)
        if _monitor_cycle % 30 == 0 or _monitor_cycle == 1:
            try:
                from utils.funding_monitor import funding_step as _fund_step  # type: ignore
                await asyncio.to_thread(_fund_step)
            except Exception as _fde:
                log.debug("funding_step error: %s", _fde)
        if _monitor_cycle % 5 == 0 and os.getenv("SMART_WALLET_ENABLED", "false").lower() == "true":
            try:
                from utils.smart_wallet_tracker import smart_wallet_step as _swt_step  # type: ignore
                await asyncio.to_thread(_swt_step)
            except Exception as _swte:
                log.debug("smart_wallet_step error: %s", _swte)
        await asyncio.sleep(60)


async def _scalp_monitor_loop():
    """Scalp perp position monitor — every 5s."""
    while True:
        try:
            from utils.perp_executor import scalp_monitor_step
            await scalp_monitor_step()
        except Exception as _e:
            log.debug("scalp_monitor_step error: %s", _e)
        await asyncio.sleep(5)


async def _perp_signal_scan_loop():
    """Legacy perp signal scan owned by the headless runtime."""
    _CG_IDS = {"SOL": "solana", "BTC": "bitcoin", "ETH": "ethereum"}

    while True:
        await asyncio.sleep(120)
        try:
            perp_enabled = os.getenv("PERP_EXECUTOR_ENABLED", "false").lower() == "true"
            if not perp_enabled:
                continue

            try:
                threshold = float(os.getenv("PERP_1H_THRESHOLD", "0.3"))
            except Exception:
                threshold = 0.3

            import requests as _req
            try:
                ids = ",".join(_CG_IDS.values())
                r = _req.get(
                    f"https://api.coingecko.com/api/v3/simple/price"
                    f"?ids={ids}&vs_currencies=usd&include_24hr_change=true",
                    timeout=8,
                )
                price_data = r.json()
            except Exception as fe:
                log.debug("perp_scan price fetch error: %s", fe)
                continue

            try:
                from utils.market_cycle import get_cycle_phase  # type: ignore
                phase = get_cycle_phase()
            except Exception:
                phase = "TRANSITION"

            from utils.perp_executor import execute_perp_signal  # type: ignore

            for symbol, cg_id in _CG_IDS.items():
                try:
                    asset = price_data.get(cg_id, {})
                    chg_24h = float(asset.get("usd_24h_change", 0))
                    chg_1h = chg_24h / 6.0

                    if chg_1h > threshold and phase != "BEAR":
                        regime_label = phase if phase else "BULL"
                        await execute_perp_signal({
                            "symbol": symbol, "side": "LONG",
                            "regime_label": regime_label, "source": "auto_scan",
                        })
                        log.info(
                            "[PERP SCAN] LONG %s  phase=%s  1h=+%.2f%%  threshold=%.1f%%",
                            symbol, phase, chg_1h, threshold,
                        )
                    elif chg_1h < -threshold and phase != "BULL":
                        regime_label = phase if phase else "BEAR"
                        await execute_perp_signal({
                            "symbol": symbol, "side": "SHORT",
                            "regime_label": regime_label, "source": "auto_scan",
                        })
                        log.info(
                            "[PERP SCAN] SHORT %s  phase=%s  1h=%.2f%%  threshold=%.1f%%",
                            symbol, phase, chg_1h, threshold,
                        )
                except Exception as sym_e:
                    log.debug("perp_scan %s error: %s", symbol, sym_e)
        except Exception as _e:
            log.debug("perp_signal_scan error: %s", _e)


async def _scalp_signal_scan_loop():
    """Legacy scalp perp signal scan owned by the headless runtime."""
    _KRAKEN_PAIRS = {
        "SOL": ("SOLUSD", "SOLUSD"),
        "BTC": ("XBTUSD", "XXBTZUSD"),
        "ETH": ("ETHUSD", "XETHZUSD"),
    }

    while True:
        await asyncio.sleep(30)
        try:
            scalp_enabled = os.getenv("SCALP_ENABLED", "false").lower() == "true"
            if not scalp_enabled:
                continue

            try:
                threshold = float(os.getenv("SCALP_5M_THRESHOLD", "0.15"))
            except Exception:
                threshold = 0.15

            import requests as _req
            from utils.perp_executor import execute_perp_signal  # type: ignore

            try:
                from utils.market_cycle import get_cycle_phase  # type: ignore
                phase = get_cycle_phase()
            except Exception:
                phase = "TRANSITION"

            for symbol, (kraken_pair, result_key) in _KRAKEN_PAIRS.items():
                try:
                    r = _req.get(
                        f"https://api.kraken.com/0/public/OHLC?pair={kraken_pair}&interval=5",
                        timeout=8,
                    )
                    data = r.json()
                    errors = data.get("error", [])
                    if errors:
                        log.warning("scalp_scan Kraken error for %s: %s", symbol, errors)
                        continue

                    candles = data.get("result", {}).get(result_key, [])
                    if len(candles) < 3:
                        log.debug("scalp_scan: not enough candles for %s (%d)", symbol, len(candles))
                        continue

                    prev_close = float(candles[-3][4])
                    curr_close = float(candles[-2][4])
                    price_now = float(candles[-2][4])
                    if prev_close <= 0:
                        continue

                    chg_5m = (curr_close - prev_close) / prev_close * 100
                    log.info(
                        "[SCALP SCAN] %s  5m=%.3f%%  threshold=±%.2f%%  price=$%.2f",
                        symbol, chg_5m, threshold, price_now,
                    )

                    if chg_5m > threshold:
                        await execute_perp_signal({
                            "symbol": symbol, "side": "LONG",
                            "regime_label": phase or "SCALP", "source": "scalp",
                        })
                        log.info("[SCALP SCAN] → LONG signal fired for %s", symbol)
                    elif chg_5m < -threshold:
                        await execute_perp_signal({
                            "symbol": symbol, "side": "SHORT",
                            "regime_label": phase or "SCALP", "source": "scalp",
                        })
                        log.info("[SCALP SCAN] → SHORT signal fired for %s", symbol)
                except Exception as sym_e:
                    log.warning("scalp_scan %s error: %s", symbol, sym_e)
        except Exception as _e:
            log.warning("scalp_signal_scan error: %s", _e)


async def _outcome_tracker_loop():
    """Outcome evaluation loop owned by the headless runtime."""
    _ensure_dashboard_backend_path()
    from outcome_tracker import outcome_tracker_loop as _tracker  # type: ignore
    await _tracker()


async def _spot_monitor_loop():
    """Legacy executor-driven spot monitor owned by the headless runtime."""
    while True:
        try:
            spot_enabled = os.getenv("EXECUTOR_ENABLED", "false").lower() == "true"
            if spot_enabled:
                from utils.executor import monitor_positions  # type: ignore
                await monitor_positions()
        except Exception as _e:
            log.warning("spot_monitor error: %s", _e)
        await asyncio.sleep(30)


_spot_last_signal_id: int = 0


async def _spot_signal_scan_loop():
    """Legacy executor-driven spot signal scan owned by the headless runtime."""
    global _spot_last_signal_id

    try:
        import sqlite3 as _sq

        with _sq.connect(f"file:{DB_PATH}?mode=ro", uri=True) as _c:
            row = _c.execute("SELECT COALESCE(MAX(id),0) FROM signals").fetchone()
            _spot_last_signal_id = int(row[0])
        log.info("[SPOT SCAN] Initialised. Watching for signals after id=%d", _spot_last_signal_id)
    except Exception as _init_e:
        log.warning("[SPOT SCAN] Could not init last_id: %s", _init_e)

    while True:
        await asyncio.sleep(60)
        try:
            spot_enabled = os.getenv("EXECUTOR_ENABLED", "false").lower() == "true"
            if not spot_enabled:
                continue

            try:
                min_score = float(os.getenv("MIN_SCORE_TO_EXECUTE", "55"))
            except Exception:
                min_score = 55.0

            import sqlite3 as _sq
            with _sq.connect(f"file:{DB_PATH}?mode=ro", uri=True) as _c:
                _c.row_factory = _sq.Row
                rows = _c.execute(
                    """
                    SELECT id, ts_utc, symbol, mint, score_total, decision,
                           regime_score, regime_label, price_usd, conviction, notes
                    FROM signals
                    WHERE id > ?
                      AND mint IS NOT NULL AND mint != ''
                      AND (score_total >= ? OR score_total IS NULL)
                      AND decision LIKE '%ALERT%'
                      AND decision NOT LIKE '%DRY%'
                      AND (notes IS NULL OR notes NOT LIKE '%status=Breakdown%')
                      AND (notes IS NULL OR notes NOT LIKE '%status=Illiquid%')
                    ORDER BY id ASC
                    LIMIT 20
                    """,
                    (_spot_last_signal_id, min_score),
                ).fetchall()

            if not rows:
                continue

            from utils.executor import execute_signal  # type: ignore

            for row in rows:
                sig_id = row["id"]
                symbol = row["symbol"]
                mint = row["mint"]
                score = float(row["score_total"] or 0)
                regime = row["regime_label"] or "UNKNOWN"
                price = float(row["price_usd"] or 0)
                conv_raw = row["conviction"]
                conviction = {3: "A", 2: "B", 1: "C"}.get(int(conv_raw), "C") if conv_raw else "C"

                _spot_last_signal_id = max(_spot_last_signal_id, sig_id)

                if price <= 0:
                    log.debug("[SPOT SCAN] Skipping %s — no price in signal", symbol)
                    continue

                try:
                    portfolio = float(os.getenv("PORTFOLIO_USD", "1000"))
                except Exception:
                    portfolio = 1000.0
                position_usd = min(portfolio * 0.05, 50.0)

                base_signal = {
                    "symbol": symbol,
                    "mint": mint,
                    "entry_price": price,
                    "score": score,
                    "confidence": conviction,
                    "regime_label": regime,
                    "position_usd": position_usd,
                }

                scalp_signal = {**base_signal, "scalp_mode": True, "source": "spot_scalp"}
                try:
                    fired = await execute_signal(scalp_signal)
                    if fired:
                        log.info(
                            "[SPOT SCALP] Opened %s @ $%.6g  score=%.0f  pos=$%.0f",
                            symbol, price, score, position_usd,
                        )
                except Exception as _se:
                    log.warning("[SPOT SCALP] execute_signal error for %s: %s", symbol, _se)

                swing_signal = {**base_signal, "scalp_mode": False, "source": "spot_swing"}
                try:
                    fired = await execute_signal(swing_signal)
                    if fired:
                        log.info(
                            "[SPOT SWING] Opened %s @ $%.6g  score=%.0f  pos=$%.0f",
                            symbol, price, score, position_usd,
                        )
                except Exception as _se:
                    log.warning("[SPOT SWING] execute_signal error for %s: %s", symbol, _se)

                await asyncio.sleep(0.5)

        except Exception as _outer_e:
            log.warning("[SPOT SCAN] outer error: %s", _outer_e)


async def _exit_monitor_loop():
    """Phase 5: Spot & memecoin exit monitor — every 60s."""
    await asyncio.sleep(120)  # initial delay — let other monitors warm up
    while True:
        try:
            from utils.exit_monitor import spot_exit_monitor_step, memecoin_exit_monitor_step
            spot_exit_monitor_step()
            memecoin_exit_monitor_step()
        except Exception as _e:
            log.debug("exit_monitor error: %s", _e)
        await asyncio.sleep(60)


async def _dca_monitor_loop():
    """Phase 5: Spot DCA auto-accumulation monitor — every 5 min."""
    await asyncio.sleep(180)  # initial delay — let signal engine warm up first
    while True:
        try:
            from utils.dca_monitor import spot_dca_monitor_step
            spot_dca_monitor_step()
        except Exception as _e:
            log.debug("dca_monitor error: %s", _e)
        await asyncio.sleep(300)  # 5 min — signals update hourly, no need to poll faster


async def _entry_monitor_loop():
    """Phase 5: Auto-entry proposal monitor — every 5 min."""
    await asyncio.sleep(240)  # initial delay — let scanners populate caches
    while True:
        try:
            from utils.entry_monitor import memecoin_entry_monitor_step, spot_entry_monitor_step
            memecoin_entry_monitor_step()
            spot_entry_monitor_step()
        except Exception as _e:
            log.debug("entry_monitor error: %s", _e)
        await asyncio.sleep(300)


async def _memecoin_scan_loop():
    """Refresh memecoin scanner cache and lifecycle state on cadence."""
    log.info(
        "[memecoin_scan] loop starting (interval=%ss, first_delay=%ss)",
        MEMECOIN_SCAN_INTERVAL_SECONDS,
        20,
    )
    await asyncio.sleep(20)
    while True:
        try:
            from utils import orchestrator
            from utils.memecoin_scanner import refresh_signal_cache, scan_trending_solana_diagnostics
            from utils.lifecycle_engine import compute_lifecycle
            from utils.lifecycle_validation import (
                link_lifecycle_outcomes,
                snapshot_lifecycle_lane,
            )

            signals = refresh_signal_cache()
            lifecycle_n = compute_lifecycle()
            snapshot_lifecycle_lane()
            link_lifecycle_outcomes()
            orchestrator.heartbeat("memecoin_scan")
            top = signals[0] if signals else {}
            log.info(
                "[memecoin_scan] refreshed cache rows=%s lifecycle=%s top=%s score=%s",
                len(signals),
                lifecycle_n,
                str(top.get("symbol") or "none"),
                (
                    f"{float(top.get('score') or 0.0):.1f}"
                    if top else
                    "n/a"
                ),
            )
            if not signals:
                try:
                    diag = scan_trending_solana_diagnostics(top_n=10)
                    top_scored = list(diag.get("top_scored") or [])
                    safety = list(diag.get("safety_counts") or [])
                    raw_n = int(diag.get("raw_candidates") or 0)
                    prefiltered_n = int(diag.get("prefiltered_candidates") or 0)
                    near_misses = list(diag.get("near_misses") or [])
                    if raw_n <= 0 and prefiltered_n <= 0:
                        near_misses = []
                    best = top_scored[0] if top_scored else {}
                    log.info(
                        "[memecoin_scan] empty-cycle diagnostics raw=%s prefiltered=%s scored=%s best=%s best_score=%s near_misses=%s safety=%s",
                        raw_n,
                        prefiltered_n,
                        int(diag.get("scored_candidates") or 0),
                        str(best.get("symbol") or "none"),
                        (
                            f"{float(best.get('score') or 0.0):.1f}"
                            if best else
                            "n/a"
                        ),
                        [
                            {
                                "symbol": str(nm.get("symbol") or ""),
                                "class": str(nm.get("classification") or ""),
                                "reason": str(nm.get("relaxation_reason") or ""),
                            }
                            for nm in near_misses[:3]
                        ],
                        safety[:3],
                    )
                except Exception as _diag_e:
                    log.warning("memecoin_scan diagnostics error: %s", _diag_e)
        except Exception as _e:
            log.warning("memecoin_scan error: %s", _e)
        await asyncio.sleep(MEMECOIN_SCAN_INTERVAL_SECONDS)


async def _memecoin_auto_buy_loop():
    """Real memecoin auto-buy path — every 60s."""
    log.info("[memecoin_auto_buy] loop starting (interval=60s, first_delay=150s)")
    await asyncio.sleep(150)  # let scanner caches and lifecycle warm up first
    cycle_n = 0
    while True:
        try:
            from utils.memecoin_manager import _auto_buy_step, memecoin_watch_to_entry_step
            from utils.memecoin_scanner import get_cached_signals, get_last_nonempty_cached_signals
            from utils.db import get_conn  # type: ignore

            cycle_n += 1
            live_cached = get_cached_signals() or []
            fallback_cached = get_last_nonempty_cached_signals(max_age_hours=6) or []
            cached_n = len(live_cached)
            fallback_n = len(fallback_cached)
            with get_conn() as conn:
                open_trades = int(conn.execute(
                    "SELECT COUNT(*) FROM memecoin_trades WHERE status='OPEN'"
                ).fetchone()[0] or 0)
            memecoin_watch_to_entry_step()
            _auto_buy_step()
            if cycle_n == 1 or cycle_n % 15 == 0:
                log.info(
                    "[memecoin_auto_buy] heartbeat cycle=%s cached=%s fallback=%s effective=%s open_trades=%s",
                    cycle_n,
                    cached_n,
                    fallback_n,
                    cached_n or fallback_n,
                    open_trades,
                )
        except Exception as _e:
            log.warning("memecoin_auto_buy error: %s", _e)
        await asyncio.sleep(60)


async def _research_loop():
    """Periodic research synthesis owned by the headless runtime."""
    log.info("[research] loop starting (interval=14400s, first_delay=60s)")
    await asyncio.sleep(60)
    while True:
        try:
            from utils.agent_coordinator import research_step  # type: ignore
            await asyncio.to_thread(research_step)
            log.debug("research_step completed")
        except Exception as _e:
            log.debug("research_loop error: %s", _e)
        await asyncio.sleep(14400)


async def _memecoin_discovery_loop():
    """Broad discovery ingress loop owned by the headless runtime."""
    interval = max(180, int(os.getenv("MEMECOIN_DISCOVERY_INTERVAL_SECONDS", "300")))
    log.info("[memecoin_discovery] loop starting (interval=%ss, first_delay=45s)", interval)
    await asyncio.sleep(45)
    while True:
        try:
            from utils.memecoin_scanner import ingest_discovery_candidates  # type: ignore
            n = await asyncio.to_thread(ingest_discovery_candidates)
            log.debug("[DISCOVERY] loop: %d candidates ingested", n)
        except Exception as _e:
            log.warning("[DISCOVERY] loop error: %s", _e)
        await asyncio.sleep(interval)


async def _runner_heartbeat_loop():
    """Keep established runner profiles warm and available to proof/watch."""
    log.info("[runner_heartbeat] loop starting (interval=%ss, first_delay=35s)", RUNNER_HEARTBEAT_INTERVAL_SECONDS)
    await asyncio.sleep(35)
    while True:
        try:
            from utils.conviction_recovery import runner_heartbeat_step  # type: ignore

            status = await asyncio.to_thread(runner_heartbeat_step)
            log.info(
                "[runner_heartbeat] refreshed=%s signals=%s inserted=%s top=%s",
                status.get("tokens_refreshed"),
                status.get("signals_count"),
                status.get("outcomes_inserted"),
                str(((status.get("top") or [{}])[0] or {}).get("symbol") or "none"),
            )
        except Exception as _e:
            log.warning("[runner_heartbeat] loop error: %s", _e)
        await asyncio.sleep(RUNNER_HEARTBEAT_INTERVAL_SECONDS)


async def _early_runner_radar_loop():
    """Refresh early-runner radar, live snapshots, and missed-runner replay state."""
    log.info("[early_runner_radar] loop starting (interval=%ss, first_delay=70s)", EARLY_RUNNER_RADAR_INTERVAL_SECONDS)
    await asyncio.sleep(70)
    while True:
        try:
            from utils.early_runner_radar import early_runner_maintenance_step  # type: ignore
            payload = await asyncio.to_thread(early_runner_maintenance_step)
            log.debug("[early_runner_radar] maintenance: %s", payload)
        except Exception as _e:
            log.warning("[early_runner_radar] loop error: %s", _e)
        await asyncio.sleep(EARLY_RUNNER_RADAR_INTERVAL_SECONDS)


async def _authority_snapshot_refresh_loop():
    """Keep the authority snapshot fresh from the headless runtime."""
    log.info("[authority_refresh] loop starting (interval=120s, first_delay=30s)")
    await asyncio.sleep(30)
    while True:
        try:
            _ensure_dashboard_backend_path()
            from routers.home import refresh_authority_snapshot  # type: ignore

            ok = await asyncio.to_thread(refresh_authority_snapshot)
            if ok:
                log.debug("[AUTHORITY] snapshot refreshed")
            else:
                log.warning("[AUTHORITY] snapshot refresh returned False")
        except Exception as exc:
            log.warning("[AUTHORITY] snapshot refresh error: %s", exc)
        await asyncio.sleep(120)


async def _whale_watch_loop():
    """Telethon whale-watch runtime owned by the headless engine."""
    log.info("[whale_watch] loop starting (first_delay=15s)")
    await asyncio.sleep(15)
    try:
        from utils.whale_watch import start_whale_watch as _ww_start  # type: ignore
        await _ww_start()
    except Exception as _wwe:
        log.warning("whale_watch_loop fatal: %s", _wwe)


def _parse_utc_ts(ts: str | None) -> datetime | None:
    if not ts:
        return None
    raw = str(ts).strip()
    if not raw:
        return None
    try:
        if raw.endswith("Z"):
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        parsed = datetime.fromisoformat(raw)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except Exception:
        try:
            return datetime.strptime(raw[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        except Exception:
            return None


def _latest_pipeline_eval_snapshot() -> tuple[datetime | None, float | None, dict]:
    meta = {
        "last_1h_eval_utc": None,
        "last_4h_eval_utc": None,
        "last_24h_eval_utc": None,
        "last_eval_horizon": None,
        "pending_count": 0,
        "due_1h_count": 0,
        "due_4h_count": 0,
        "due_24h_count": 0,
    }
    try:
        with sqlite3.connect(DB_PATH) as conn:
            row = conn.execute(
                """
                SELECT
                    MAX(evaluated_1h_ts_utc) AS last_1h,
                    MAX(evaluated_4h_ts_utc) AS last_4h,
                    MAX(evaluated_24h_ts_utc) AS last_24h,
                    SUM(CASE WHEN status='PENDING' THEN 1 ELSE 0 END) AS pending_count,
                    SUM(CASE WHEN status='PENDING'
                              AND return_1h_pct IS NULL
                              AND scanned_at <= datetime('now', '-1 hours')
                             THEN 1 ELSE 0 END) AS due_1h_count,
                    SUM(CASE WHEN status='PENDING'
                              AND return_4h_pct IS NULL
                              AND scanned_at <= datetime('now', '-4 hours')
                             THEN 1 ELSE 0 END) AS due_4h_count,
                    SUM(CASE WHEN status='PENDING'
                              AND return_24h_pct IS NULL
                              AND scanned_at <= datetime('now', '-24 hours')
                             THEN 1 ELSE 0 END) AS due_24h_count
                FROM memecoin_signal_outcomes
                """
            ).fetchone()
    except Exception as exc:
        log.warning("pipeline_watchdog db read failed: %s", exc)
        return None, None, meta

    if row:
        meta.update(
            {
                "last_1h_eval_utc": row[0],
                "last_4h_eval_utc": row[1],
                "last_24h_eval_utc": row[2],
                "pending_count": int(row[3] or 0),
                "due_1h_count": int(row[4] or 0),
                "due_4h_count": int(row[5] or 0),
                "due_24h_count": int(row[6] or 0),
            }
        )

    candidates = [
        ("1h", _parse_utc_ts(meta["last_1h_eval_utc"])),
        ("4h", _parse_utc_ts(meta["last_4h_eval_utc"])),
        ("24h", _parse_utc_ts(meta["last_24h_eval_utc"])),
    ]
    candidates = [(h, ts) for h, ts in candidates if ts is not None]
    if not candidates:
        return None, None, meta

    horizon, last_eval = max(candidates, key=lambda item: item[1])
    meta["last_eval_horizon"] = horizon
    if not last_eval:
        return None, None, meta

    age_hours = (datetime.now(timezone.utc) - last_eval).total_seconds() / 3600
    return last_eval, max(age_hours, 0.0), meta


def _get_pipeline_watchdog_snapshot() -> dict:
    try:
        from utils.db import get_conn  # type: ignore
        with get_conn() as conn:
            row = conn.execute(
                "SELECT value FROM kv_store WHERE key='pipeline_watchdog_status'"
            ).fetchone()
        if row and row[0]:
            return json.loads(row[0])
    except Exception:
        pass
    return {}


def _set_pipeline_watchdog_snapshot(snapshot: dict) -> None:
    try:
        from utils.db import get_conn, with_db_retry  # type: ignore

        def _write() -> None:
            with get_conn() as conn:
                conn.execute(
                    """
                    INSERT INTO kv_store (key, value) VALUES (?, ?)
                    ON CONFLICT(key) DO UPDATE SET value=excluded.value
                    """,
                    ("pipeline_watchdog_status", json.dumps(snapshot, separators=(",", ":"))),
                )

        with_db_retry(_write, retries=6, base_sleep_s=0.35)
    except Exception as exc:
        log.debug("pipeline_watchdog snapshot write skipped: %s", exc)


def _set_allocator_stream_snapshot(snapshot: dict) -> None:
    try:
        from utils.db import get_conn  # type: ignore
        with get_conn() as conn:
            conn.execute(
                """
                INSERT INTO kv_store (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
                """,
                ("allocator_stream_status", json.dumps(snapshot, separators=(",", ":"))),
            )
    except Exception:
        pass


def _set_speculation_heat_stream_snapshot(snapshot: dict) -> None:
    try:
        from utils.db import get_conn  # type: ignore
        with get_conn() as conn:
            conn.execute(
                """
                INSERT INTO kv_store (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
                """,
                ("speculation_heat_status", json.dumps(snapshot, separators=(",", ":"))),
            )
    except Exception:
        pass


def _set_token_stats_stream_snapshot(snapshot: dict) -> None:
    try:
        from utils.db import get_conn  # type: ignore
        with get_conn() as conn:
            conn.execute(
                """
                INSERT INTO kv_store (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
                """,
                ("token_stats_stream_status", json.dumps(snapshot, separators=(",", ":"))),
            )
    except Exception:
        pass


def _set_large_trade_stream_snapshot(snapshot: dict) -> None:
    try:
        from utils.db import get_conn  # type: ignore
        with get_conn() as conn:
            conn.execute(
                """
                INSERT INTO kv_store (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
                """,
                ("large_trade_stream_status", json.dumps(snapshot, separators=(",", ":"))),
            )
    except Exception:
        pass


def _set_wallet_tx_stream_snapshot(snapshot: dict) -> None:
    try:
        from utils.db import get_conn  # type: ignore
        with get_conn() as conn:
            conn.execute(
                """
                INSERT INTO kv_store (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
                """,
                ("wallet_tx_stream_status", json.dumps(snapshot, separators=(",", ":"))),
            )
    except Exception:
        pass


def _set_ai_analyst_status_snapshot(snapshot: dict) -> None:
    try:
        from utils.db import get_conn  # type: ignore
        with get_conn() as conn:
            conn.execute(
                """
                INSERT INTO kv_store (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
                """,
                ("ai_analyst_status", json.dumps(snapshot, separators=(",", ":"))),
            )
    except Exception:
        pass


async def _pipeline_watchdog_loop():
    """Detect stale memecoin outcome evaluation and trigger a recovery pass."""
    await asyncio.sleep(60)
    while True:
        try:
            checked_at = datetime.now(timezone.utc)
            last_eval, age_hours, eval_meta = _latest_pipeline_eval_snapshot()
            prev = _get_pipeline_watchdog_snapshot()
            prev_status = str(prev.get("status") or "").upper()
            recovery_attempted = False
            recovery_result = None

            if age_hours is None:
                status = "NO_DATA"
                log.warning("pipeline_watchdog: no evaluated 24h outcomes found yet")
                detail = "No evaluated 24h memecoin outcomes found yet."
            elif age_hours > PIPELINE_STALE_HOURS:
                status = "STALE"
                last_eval_text = last_eval.isoformat() if last_eval else "unknown"
                log.critical(
                    "pipeline_watchdog: evaluator stale (age=%.2fh, last_eval=%s) — triggering recovery run",
                    age_hours,
                    last_eval_text,
                )
                detail = f"Evaluator stale at {age_hours:.2f}h; last_eval={last_eval_text}."
                if OUTCOME_TRACKING_ENABLED:
                    recovery_attempted = True
                    try:
                        from utils.memecoin_manager import memecoin_outcome_step  # type: ignore

                        before_eval = last_eval
                        await asyncio.to_thread(memecoin_outcome_step)
                        after_eval, after_age_hours, after_meta = _latest_pipeline_eval_snapshot()
                        progressed = bool(after_eval and (before_eval is None or after_eval > before_eval))
                        if progressed:
                            last_eval = after_eval
                            age_hours = after_age_hours
                            eval_meta = after_meta
                            status = "FRESH" if (age_hours is not None and age_hours < 2) else "AGING"
                            detail = f"Evaluator recovered via memecoin outcome fill; age is {age_hours:.2f}h."
                            recovery_result = "RECOVERED"
                        else:
                            eval_meta = after_meta
                            recovery_result = "NO_PROGRESS"
                            detail = (
                                f"Evaluator stale at {age_hours:.2f}h; recovery ran but no new due rows were filled."
                            )
                        from utils.db import record_watchdog_event  # type: ignore
                        record_watchdog_event(
                            "PIPELINE_EVAL",
                            "RECOVERY_" + recovery_result,
                            "Triggered memecoin outcome recovery pass from watchdog.",
                            metadata={
                                "age_hours": round(age_hours, 2),
                                "last_eval_utc": last_eval.isoformat() if last_eval else None,
                                "threshold_hours": PIPELINE_STALE_HOURS,
                                **eval_meta,
                            },
                        )
                    except Exception as recovery_exc:
                        recovery_result = "FAILED"
                        from utils.db import record_watchdog_event  # type: ignore
                        record_watchdog_event(
                            "PIPELINE_EVAL",
                            "RECOVERY_FAILED",
                            f"Evaluator recovery pass failed: {recovery_exc}",
                            metadata={
                                "age_hours": round(age_hours, 2),
                                "last_eval_utc": last_eval.isoformat() if last_eval else None,
                                **eval_meta,
                            },
                        )
                else:
                    recovery_result = "SKIPPED"
            else:
                status = "FRESH" if age_hours < 2 else "AGING"
                log.info("pipeline_watchdog: evaluator healthy (age=%.2fh)", age_hours)
                detail = f"Evaluator age is {age_hours:.2f}h."
                if prev_status == "STALE":
                    from utils.db import record_watchdog_event  # type: ignore
                    record_watchdog_event(
                        "PIPELINE_EVAL",
                        "RECOVERED",
                        "Pipeline evaluator freshness recovered.",
                        metadata={
                            "age_hours": round(age_hours, 2),
                            "last_eval_utc": last_eval.isoformat() if last_eval else None,
                            **eval_meta,
                        },
                    )

            snapshot = {
                "watchdog": "PIPELINE_EVAL",
                "status": status,
                "checked_at": checked_at.isoformat(),
                "age_hours": round(age_hours, 2) if age_hours is not None else None,
                "last_eval_utc": last_eval.isoformat() if last_eval else None,
                "threshold_hours": PIPELINE_STALE_HOURS,
                "detail": detail,
                "recovery_attempted": recovery_attempted,
                "recovery_result": recovery_result,
                **eval_meta,
            }
            _set_pipeline_watchdog_snapshot(snapshot)

            if status != prev_status:
                from utils.db import record_watchdog_event  # type: ignore
                record_watchdog_event(
                    "PIPELINE_EVAL",
                    status,
                    detail,
                    metadata={
                        "age_hours": round(age_hours, 2) if age_hours is not None else None,
                        "last_eval_utc": last_eval.isoformat() if last_eval else None,
                        "threshold_hours": PIPELINE_STALE_HOURS,
                        **eval_meta,
                    },
                )
        except Exception as exc:
            log.warning("pipeline_watchdog error: %s", exc)
        await asyncio.sleep(PIPELINE_WATCHDOG_INTERVAL_SECONDS)


async def _allocator_check_loop():
    """Persist allocator snapshots without requiring dashboard traffic."""
    await asyncio.sleep(150)
    while True:
        try:
            checked_at = datetime.now(timezone.utc)
            _ensure_dashboard_backend_path()
            from routers.portfolio import _build_allocation_change_summary, _build_allocation_recommendation  # type: ignore
            from utils.db import (  # type: ignore
                get_recent_allocation_recommendations,
                record_allocation_recommendation,
                record_execution_intent,
            )

            payload = await asyncio.to_thread(_build_allocation_recommendation)
            inserted_id = await asyncio.to_thread(
                record_allocation_recommendation,
                payload,
                min_interval_minutes=ALLOCATOR_SNAPSHOT_MINUTES,
            )
            history = await asyncio.to_thread(get_recent_allocation_recommendations, 8)
            latest_change = _build_allocation_change_summary(history)

            if inserted_id:
                log.info(
                    "allocator_check: snapshot recorded id=%s posture=%s",
                    inserted_id,
                    str(payload.get("posture") or "UNKNOWN"),
                )
                if latest_change.get("changed"):
                    changed_arms = list(latest_change.get("changed_arms") or [])
                    arm_summary = ", ".join(
                        f"{str(item.get('arm') or '').upper()}: {str(item.get('from_action') or '—')}→{str(item.get('to_action') or '—')}"
                        for item in changed_arms[:3]
                    ) or "posture changed"
                    note = (
                        f"Allocator shifted from {str(latest_change.get('previous_posture') or 'UNKNOWN').lower().replace('_', ' ')} "
                        f"to {str(latest_change.get('current_posture') or 'UNKNOWN').lower().replace('_', ' ')}. {arm_summary}. "
                        f"{str(payload.get('note') or '').strip()}"
                    ).strip()
                    await asyncio.to_thread(
                        record_execution_intent,
                        "portfolio_allocator",
                        "PORTFOLIO",
                        "ALLOCATION_REBALANCE",
                        None,
                        float(payload.get("allocatable_base_usd") or 0.0),
                        "OBSERVE",
                        str(payload.get("posture") or "UNKNOWN"),
                        [str(payload.get("note") or "Allocator posture changed.")],
                        False,
                        None,
                        note,
                    )
                    log.info("allocator_check: posture change recorded (%s)", arm_summary)
            else:
                log.debug("allocator_check: no new snapshot needed")
            latest_row = history[0] if history else {}
            snapshot = {
                "watchdog": "ALLOCATOR_STREAM",
                "status": "ACTIVE",
                "checked_at": checked_at.isoformat(),
                "last_snapshot_utc": latest_row.get("ts_utc"),
                "last_snapshot_id": latest_row.get("id"),
                "history_count": len(history),
                "recorded_new_snapshot": bool(inserted_id),
                "latest_posture": payload.get("posture"),
                "latest_change": latest_change,
                "detail": (
                    f"Allocator stream is active; latest posture is {str(payload.get('posture') or 'UNKNOWN').replace('_', ' ')}."
                    if history else
                    "Allocator stream is active but has not recorded a snapshot yet."
                ),
            }
            _set_allocator_stream_snapshot(snapshot)
        except Exception as exc:
            log.warning("allocator_check error: %s", exc)
            _set_allocator_stream_snapshot({
                "watchdog": "ALLOCATOR_STREAM",
                "status": "ERROR",
                "checked_at": datetime.now(timezone.utc).isoformat(),
                "detail": f"Allocator stream error: {exc}",
            })
        await asyncio.sleep(ALLOCATOR_CHECK_INTERVAL_SECONDS)


async def _speculation_heat_loop():
    """Persist speculation heat snapshots without requiring dashboard traffic."""
    await asyncio.sleep(180)
    while True:
        try:
            checked_at = datetime.now(timezone.utc)
            from utils.db import record_speculation_heat_snapshot  # type: ignore
            from utils.speculation_heat import build_speculation_heat_snapshot  # type: ignore

            payload = await asyncio.to_thread(build_speculation_heat_snapshot)
            inserted_id = await asyncio.to_thread(
                record_speculation_heat_snapshot,
                payload,
                min_interval_minutes=max(15, int(SPECULATION_HEAT_INTERVAL_SECONDS // 60)),
            )
            if inserted_id:
                log.info(
                    "speculation_heat: snapshot recorded id=%s state=%s score=%.1f",
                    inserted_id,
                    str(payload.get("heat_state") or "UNKNOWN"),
                    float(payload.get("heat_score") or 0.0),
                )
            else:
                log.debug("speculation_heat: no new snapshot needed")
            _set_speculation_heat_stream_snapshot({
                "watchdog": "SPECULATION_HEAT",
                "status": "ACTIVE",
                "checked_at": checked_at.isoformat(),
                "recorded_new_snapshot": bool(inserted_id),
                "heat_state": payload.get("heat_state"),
                "heat_score": payload.get("heat_score"),
                "momentum": payload.get("momentum"),
                "detail": str(payload.get("note") or ""),
            })
        except Exception as exc:
            log.warning("speculation_heat error: %s", exc)
            _set_speculation_heat_stream_snapshot({
                "watchdog": "SPECULATION_HEAT",
                "status": "ERROR",
                "checked_at": datetime.now(timezone.utc).isoformat(),
                "detail": f"Speculation heat error: {exc}",
            })
        await asyncio.sleep(SPECULATION_HEAT_INTERVAL_SECONDS)


async def _token_stats_stream_loop():
    """Keep Birdeye token-stats subscriptions aligned with the active memecoin working set."""
    if NO_BIRDEYE_MODE:
        while True:
            _set_token_stats_stream_snapshot({
                "watchdog": "TOKEN_STATS_STREAM",
                "status": "DISABLED",
                "data_status": "INDEPENDENT_MODE",
                "checked_at": datetime.now(timezone.utc).isoformat(),
                "tracked_count": 0,
                "tracked_symbols": [],
                "ws_connected": False,
                "cached_count": 0,
                "detail": "BirdEye token stats disabled intentionally; independent mode requires Gecko/Dex/on-chain confirmation before proof upgrades.",
            })
            await asyncio.sleep(TOKEN_STATS_SYNC_INTERVAL_SECONDS)
    await asyncio.sleep(120)
    try:
        from utils import token_stats_feed  # type: ignore

        token_stats_feed.start()
    except Exception as exc:
        log.warning("token_stats_stream start failed: %s", exc)
        _set_token_stats_stream_snapshot({
            "watchdog": "TOKEN_STATS_STREAM",
            "status": "ERROR",
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "detail": f"Token stats stream start failed: {exc}",
        })
        return

    while True:
        try:
            checked_at = datetime.now(timezone.utc)
            from utils.db import get_conn  # type: ignore
            from utils.memecoin_manager import get_proof_candidate_snapshot  # type: ignore
            from utils.memecoin_scanner import get_cached_signals, get_last_nonempty_cached_signals  # type: ignore
            from utils import token_stats_feed  # type: ignore

            tracked: dict[str, str | None] = {}
            snapshot = await asyncio.to_thread(get_proof_candidate_snapshot, 20)
            for cand in list(snapshot.get("candidates") or [])[:20]:
                mint = str(cand.get("mint") or "").strip()
                if mint:
                    tracked[mint] = str(cand.get("symbol") or "").upper() or None

            cache_rows = list(get_cached_signals() or [])
            if not cache_rows:
                cache_rows = list(get_last_nonempty_cached_signals() or [])
            for row in cache_rows[:10]:
                mint = str(row.get("mint") or "").strip()
                if mint and mint not in tracked:
                    tracked[mint] = str(row.get("symbol") or "").upper() or None

            try:
                with get_conn() as conn:
                    rows = conn.execute(
                        """
                        SELECT mint, symbol
                        FROM memecoin_trades
                        WHERE status='OPEN'
                        ORDER BY opened_ts_utc DESC
                        LIMIT 10
                        """
                    ).fetchall()
                for row in rows:
                    mint = str(row["mint"] or "").strip()
                    if mint:
                        tracked[mint] = str(row["symbol"] or "").upper() or None
            except Exception:
                pass

            token_stats_feed.set_tracked_mints(tracked)
            feed_status = token_stats_feed.status()
            _set_token_stats_stream_snapshot({
                "watchdog": "TOKEN_STATS_STREAM",
                "status": "ACTIVE" if feed_status.get("ws_connected") else "DEGRADED",
                "checked_at": checked_at.isoformat(),
                "tracked_count": len(tracked),
                "tracked_symbols": [sym for sym in tracked.values() if sym][:10],
                "ws_connected": bool(feed_status.get("ws_connected")),
                "cached_count": int(feed_status.get("cached_count") or 0),
                "detail": f"Tracking {len(tracked)} active memecoin mint(s) via Birdeye token stats.",
            })
        except Exception as exc:
            log.warning("token_stats_stream error: %s", exc)
            _set_token_stats_stream_snapshot({
                "watchdog": "TOKEN_STATS_STREAM",
                "status": "ERROR",
                "checked_at": datetime.now(timezone.utc).isoformat(),
                "detail": f"Token stats stream error: {exc}",
            })
        await asyncio.sleep(TOKEN_STATS_SYNC_INTERVAL_SECONDS)


async def _token_intelligence_loop():
    """Independent BirdEye replacement layer for focused token stats."""
    log.info("[token_intelligence] loop starting (interval=%ss, first_delay=90s)", TOKEN_INTELLIGENCE_INTERVAL_SECONDS)
    await asyncio.sleep(90)
    while True:
        try:
            from utils.token_intelligence import token_intelligence_step  # type: ignore

            result = await asyncio.to_thread(token_intelligence_step)
            log.info(
                "[token_intelligence] %s tracked=%s snapshots=%s conflicts=%s",
                result.get("status"),
                result.get("tracked_count"),
                result.get("snapshot_count"),
                result.get("identity_conflicts"),
            )
        except Exception as exc:
            log.warning("token_intelligence error: %s", exc)
        await asyncio.sleep(TOKEN_INTELLIGENCE_INTERVAL_SECONDS)


async def _large_trade_stream_loop():
    """Keep Birdeye large-trade sponsorship flow aligned with the active memecoin working set."""
    if NO_BIRDEYE_MODE:
        while True:
            _set_large_trade_stream_snapshot({
                "watchdog": "LARGE_TRADE_STREAM",
                "status": "DISABLED",
                "data_status": "INDEPENDENT_MODE",
                "checked_at": datetime.now(timezone.utc).isoformat(),
                "tracked_count": 0,
                "tracked_symbols": [],
                "ws_connected": False,
                "matched_events": 0,
                "persisted_events": 0,
                "detail": "BirdEye large-trade stream disabled intentionally; large-flow confirmation is not treated as live.",
            })
            await asyncio.sleep(LARGE_TRADE_SYNC_INTERVAL_SECONDS)
    await asyncio.sleep(140)
    try:
        from utils import large_trade_feed  # type: ignore

        large_trade_feed.start()
    except Exception as exc:
        log.warning("large_trade_stream start failed: %s", exc)
        _set_large_trade_stream_snapshot({
            "watchdog": "LARGE_TRADE_STREAM",
            "status": "ERROR",
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "detail": f"Large trade stream start failed: {exc}",
        })
        return

    while True:
        try:
            checked_at = datetime.now(timezone.utc)
            from utils.db import get_conn  # type: ignore
            from utils.memecoin_manager import get_proof_candidate_snapshot  # type: ignore
            from utils.memecoin_scanner import get_cached_signals, get_last_nonempty_cached_signals  # type: ignore
            from utils import large_trade_feed  # type: ignore

            tracked: dict[str, str | None] = {}
            snapshot = await asyncio.to_thread(get_proof_candidate_snapshot, 20)
            for cand in list(snapshot.get("candidates") or [])[:20]:
                mint = str(cand.get("mint") or "").strip()
                if mint:
                    tracked[mint] = str(cand.get("symbol") or "").upper() or None

            cache_rows = list(get_cached_signals() or [])
            if not cache_rows:
                cache_rows = list(get_last_nonempty_cached_signals() or [])
            for row in cache_rows[:10]:
                mint = str(row.get("mint") or "").strip()
                if mint and mint not in tracked:
                    tracked[mint] = str(row.get("symbol") or "").upper() or None

            try:
                with get_conn() as conn:
                    rows = conn.execute(
                        """
                        SELECT mint, symbol
                        FROM memecoin_trades
                        WHERE status='OPEN'
                        ORDER BY opened_ts_utc DESC
                        LIMIT 10
                        """
                    ).fetchall()
                for row in rows:
                    mint = str(row["mint"] or "").strip()
                    if mint:
                        tracked[mint] = str(row["symbol"] or "").upper() or None
            except Exception:
                pass

            large_trade_feed.set_tracked_mints(tracked)
            feed_status = large_trade_feed.status()
            _set_large_trade_stream_snapshot({
                "watchdog": "LARGE_TRADE_STREAM",
                "status": "ACTIVE" if feed_status.get("ws_connected") else "DEGRADED",
                "checked_at": checked_at.isoformat(),
                "tracked_count": len(tracked),
                "tracked_symbols": [sym for sym in tracked.values() if sym][:10],
                "ws_connected": bool(feed_status.get("ws_connected")),
                "matched_events": int(feed_status.get("matched_events") or 0),
                "persisted_events": int(feed_status.get("persisted_events") or 0),
                "last_event_ts": feed_status.get("last_event_ts"),
                "detail": f"Tracking {len(tracked)} memecoin mint(s) against Birdeye large-trade flow.",
            })
        except Exception as exc:
            log.warning("large_trade_stream error: %s", exc)
            _set_large_trade_stream_snapshot({
                "watchdog": "LARGE_TRADE_STREAM",
                "status": "ERROR",
                "checked_at": datetime.now(timezone.utc).isoformat(),
                "detail": f"Large trade stream error: {exc}",
            })
        await asyncio.sleep(LARGE_TRADE_SYNC_INTERVAL_SECONDS)


async def _wallet_tx_stream_loop():
    """Track curated wallet cohort flow.

    In independent mode this uses Helius polling instead of BirdEye streams so
    wallet reinforcement can keep learning without the BirdEye subscription.
    """
    if NO_BIRDEYE_MODE:
        while True:
            try:
                from utils.memecoin_manager import get_proof_candidate_snapshot  # type: ignore
                from utils.wallet_reinforcement import sync_independent_wallet_flow  # type: ignore

                proof_snapshot = await asyncio.to_thread(get_proof_candidate_snapshot, 20)
                payload = await asyncio.to_thread(
                    sync_independent_wallet_flow,
                    list(proof_snapshot.get("candidates") or [])[:20],
                )
                payload = dict(payload or {})
                payload.setdefault("watchdog", "WALLET_TX_STREAM")
                payload["watchdog"] = "WALLET_TX_STREAM"
                payload["data_status"] = "INDEPENDENT_MODE"
                payload["worker_count"] = 0
                payload["frames_received"] = 0
                payload["matched_events"] = int(payload.get("inserted_live_events") or 0)
                payload["persisted_events"] = int(payload.get("inserted_live_events") or 0)
                payload.setdefault("checked_at", datetime.now(timezone.utc).isoformat())
                payload.setdefault("detail", "Independent wallet flow active through Helius.")
                _set_wallet_tx_stream_snapshot(payload)
            except Exception as exc:
                log.warning("independent wallet flow error: %s", exc)
                _set_wallet_tx_stream_snapshot({
                    "watchdog": "WALLET_TX_STREAM",
                    "status": "ERROR",
                    "data_status": "INDEPENDENT_MODE",
                    "checked_at": datetime.now(timezone.utc).isoformat(),
                    "detail": f"Independent wallet flow error: {exc}",
                })
            await asyncio.sleep(WALLET_TX_SYNC_INTERVAL_SECONDS)
    await asyncio.sleep(160)
    try:
        from utils import wallet_tx_feed  # type: ignore

        wallet_tx_feed.start()
    except Exception as exc:
        log.warning("wallet_tx_stream start failed: %s", exc)
        _set_wallet_tx_stream_snapshot({
            "watchdog": "WALLET_TX_STREAM",
            "status": "ERROR",
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "detail": f"Wallet tx stream start failed: {exc}",
        })
        return

    while True:
        try:
            checked_at = datetime.now(timezone.utc)
            from utils import wallet_tx_feed  # type: ignore
            from utils.wallet_reinforcement import (
                build_live_wallet_behavior_map,
                get_active_tracked_wallets,
                get_wallet_tracking_settings,
                seed_wallet_tracking_cohort,
            )  # type: ignore
            from utils.memecoin_manager import get_proof_candidate_snapshot  # type: ignore

            settings = get_wallet_tracking_settings()
            if settings.get("enabled"):
                active_wallets = get_active_tracked_wallets(limit=int(settings.get("cohort_size") or 10))
                if not active_wallets:
                    seed_wallet_tracking_cohort(
                        target_size=int(settings.get("cohort_size") or 10),
                        min_quality_score=float(settings.get("min_quality_score") or 65.0),
                    )
                    active_wallets = get_active_tracked_wallets(limit=int(settings.get("cohort_size") or 10))
            else:
                active_wallets = []

            wallet_tx_feed.set_tracked_wallets(active_wallets)
            await wallet_tx_feed.maintain_workers()
            feed_status = wallet_tx_feed.status()
            proof_snapshot = await asyncio.to_thread(get_proof_candidate_snapshot, 20)
            behavior_map = await asyncio.to_thread(
                build_live_wallet_behavior_map,
                list(proof_snapshot.get("candidates") or [])[:20],
                max_age_minutes=max(60, int(WALLET_TX_SYNC_INTERVAL_SECONDS * 2 / 60)),
            )

            _set_wallet_tx_stream_snapshot({
                "watchdog": "WALLET_TX_STREAM",
                "status": "ACTIVE" if settings.get("enabled") else "PAUSED",
                "checked_at": checked_at.isoformat(),
                "tracking_enabled": bool(settings.get("enabled")),
                "tracked_count": len(active_wallets),
                "tracked_wallets": [str(w.get("wallet_address") or "")[:10] for w in active_wallets[:10]],
                "worker_count": int(feed_status.get("worker_count") or 0),
                "frames_received": int(feed_status.get("frames_received") or 0),
                "non_json_frames": int(feed_status.get("non_json_frames") or 0),
                "non_data_frames": int(feed_status.get("non_data_frames") or 0),
                "normalize_dropped": int(feed_status.get("normalize_dropped") or 0),
                "duplicate_events": int(feed_status.get("duplicate_events") or 0),
                "persist_failures": int(feed_status.get("persist_failures") or 0),
                "matched_events": int(feed_status.get("matched_events") or 0),
                "persisted_events": int(feed_status.get("persisted_events") or 0),
                "last_frame_ts": feed_status.get("last_frame_ts"),
                "last_event_ts": feed_status.get("last_event_ts"),
                "last_message_type": feed_status.get("last_message_type"),
                "last_drop_reason": feed_status.get("last_drop_reason"),
                "last_persist_error": feed_status.get("last_persist_error"),
                "last_matched_sample": feed_status.get("last_matched_sample"),
                "last_ignored_sample": feed_status.get("last_ignored_sample"),
                "behavior_mints": len(behavior_map),
                "detail": (
                    f"Tracking {len(active_wallets)} curated wallet(s) via Birdeye wallet tx stream."
                    if settings.get("enabled")
                    else "Wallet tracking disabled by env."
                ),
            })
        except Exception as exc:
            log.warning("wallet_tx_stream error: %s", exc)
            _set_wallet_tx_stream_snapshot({
                "watchdog": "WALLET_TX_STREAM",
                "status": "ERROR",
                "checked_at": datetime.now(timezone.utc).isoformat(),
                "detail": f"Wallet tx stream error: {exc}",
            })
        await asyncio.sleep(WALLET_TX_SYNC_INTERVAL_SECONDS)


async def _ai_analyst_loop():
    """Advisory-only system analyst. Builds memos but never changes execution."""
    log.info(
        "[ai_analyst] loop starting (interval=%ss, first_delay=%ss)",
        AI_ANALYST_INTERVAL_SECONDS,
        180,
    )
    await asyncio.sleep(180)
    while True:
        try:
            from utils.ai_analyst import refresh_ai_analyst_snapshot  # type: ignore

            payload = await refresh_ai_analyst_snapshot(window_days=90, append_memory_entry=True)
            _set_ai_analyst_status_snapshot(
                {
                    "watchdog": "AI_ANALYST",
                    "status": "READY",
                    "checked_at": datetime.now(timezone.utc).isoformat(),
                    "generated_at": payload.get("generated_at"),
                    "source": payload.get("source"),
                    "headline": payload.get("headline"),
                    "top_lesson": payload.get("top_lesson"),
                }
            )
            log.info(
                "[ai_analyst] refreshed source=%s headline=%s",
                str(payload.get("source") or "rules"),
                str(payload.get("headline") or "")[:160],
            )
        except Exception as exc:
            _set_ai_analyst_status_snapshot(
                {
                    "watchdog": "AI_ANALYST",
                    "status": "ERROR",
                    "checked_at": datetime.now(timezone.utc).isoformat(),
                    "detail": f"AI analyst error: {exc}",
                }
            )
            log.warning("ai_analyst_loop error: %s", exc)
        await asyncio.sleep(AI_ANALYST_INTERVAL_SECONDS)


async def _paper_signal_learning_loop():
    """Paper-enter and paper-exit clean token signals without touching live capacity."""
    log.info("[paper_signal_learning] loop starting (interval=%ss, first_delay=110s)", PAPER_SIGNAL_LEARNING_INTERVAL_SECONDS)
    await asyncio.sleep(110)
    while True:
        try:
            from utils.paper_signal_learning import paper_signal_learning_step  # type: ignore

            payload = await asyncio.to_thread(paper_signal_learning_step)
            log.info(
                "[paper_signal_learning] %s opened=%s reviewed=%s closed=%s",
                payload.get("status"),
                payload.get("opened"),
                payload.get("reviewed"),
                payload.get("closed"),
            )
        except Exception as exc:
            log.warning("paper_signal_learning error: %s", exc)
            try:
                from utils.db import get_conn  # type: ignore
                import json as _json

                with get_conn() as conn:
                    conn.execute(
                        "INSERT OR REPLACE INTO kv_store (key, value) VALUES (?, ?)",
                        (
                            "paper_signal_learning_status",
                            _json.dumps({
                                "watchdog": "PAPER_SIGNAL_LEARNING",
                                "status": "ERROR",
                                "checked_at": datetime.now(timezone.utc).isoformat(),
                                "detail": f"Paper signal learning error: {exc}",
                            }, separators=(",", ":")),
                        ),
                    )
            except Exception:
                pass
        await asyncio.sleep(PAPER_SIGNAL_LEARNING_INTERVAL_SECONDS)


async def _runner_review_outcome_loop():
    """Evaluate operator runner-review decisions against live token stats."""
    log.info(
        "[runner_review_outcome] loop starting (interval=%ss, first_delay=180s)",
        RUNNER_REVIEW_OUTCOME_INTERVAL_SECONDS,
    )
    await asyncio.sleep(180)
    while True:
        try:
            from utils.runner_review import evaluate_runner_review_outcomes  # type: ignore

            payload = await asyncio.to_thread(evaluate_runner_review_outcomes, 100)
            log.info(
                "[runner_review_outcome] reviewed=%s updated=%s completed=%s skipped_no_market=%s",
                payload.get("reviewed"),
                payload.get("updated"),
                payload.get("completed"),
                payload.get("skipped_no_market"),
            )
        except Exception as exc:
            log.warning("runner_review_outcome error: %s", exc)
        await asyncio.sleep(RUNNER_REVIEW_OUTCOME_INTERVAL_SECONDS)


async def _runner_review_auto_paper_loop():
    """Seed paper entries for runner-ready ideas while authority blocks live capital."""
    log.info(
        "[runner_review_auto_paper] loop starting (interval=%ss, first_delay=240s)",
        RUNNER_REVIEW_AUTO_PAPER_INTERVAL_SECONDS,
    )
    await asyncio.sleep(240)
    while True:
        try:
            from utils.runner_review import auto_seed_runner_paper_entries  # type: ignore

            payload = await asyncio.to_thread(auto_seed_runner_paper_entries)
            log.info(
                "[runner_review_auto_paper] created=%s eligible=%s skipped=%s reasons=%s",
                payload.get("created"),
                payload.get("eligible"),
                payload.get("skipped"),
                payload.get("skipped_reasons"),
            )
        except Exception as exc:
            log.warning("runner_review_auto_paper error: %s", exc)
        await asyncio.sleep(RUNNER_REVIEW_AUTO_PAPER_INTERVAL_SECONDS)


async def _memecoin_research_dossier_loop():
    """Refresh structured memecoin research dossiers for Home and learning."""
    log.info(
        "[memecoin_research_dossier] loop starting (interval=%ss, first_delay=260s)",
        MEMECOIN_RESEARCH_DOSSIER_INTERVAL_SECONDS,
    )
    await asyncio.sleep(260)
    while True:
        try:
            from utils.memecoin_research import refresh_memecoin_research_dossiers  # type: ignore

            payload = await asyncio.to_thread(refresh_memecoin_research_dossiers, 40)
            log.info(
                "[memecoin_research_dossier] total=%s actions=%s outcome_linked=%s",
                (payload.get("summary") or {}).get("total"),
                (payload.get("summary") or {}).get("actions"),
                (payload.get("summary") or {}).get("outcome_linked_count"),
            )
        except Exception as exc:
            log.warning("memecoin_research_dossier error: %s", exc)
        await asyncio.sleep(MEMECOIN_RESEARCH_DOSSIER_INTERVAL_SECONDS)


async def _memecoin_catalyst_loop():
    """Refresh persistent catalyst intelligence for tracked memecoin names."""
    log.info(
        "[memecoin_catalyst] loop starting (interval=%ss, first_delay=220s)",
        MEMECOIN_CATALYST_INTERVAL_SECONDS,
    )
    await asyncio.sleep(220)
    while True:
        try:
            from utils.memecoin_research import refresh_memecoin_catalyst_events  # type: ignore

            payload = await asyncio.to_thread(
                refresh_memecoin_catalyst_events,
                60,
                include_external=True,
            )
            summary = payload.get("summary") or {}
            log.info(
                "[memecoin_catalyst] generated=%s inserted=%s external=%s by_type=%s",
                summary.get("generated_events"),
                summary.get("inserted"),
                summary.get("external_events"),
                summary.get("by_type"),
            )
        except Exception as exc:
            log.warning("memecoin_catalyst error: %s", exc)
        await asyncio.sleep(MEMECOIN_CATALYST_INTERVAL_SECONDS)


async def _dashboard_snapshot_warmer_loop():
    """Engine-owned dashboard snapshot warmer.

    The dashboard process is intentionally read-mostly. This loop keeps the UI
    snapshots fresh from the engine runtime, where DB writes are now serialized.
    """
    _ensure_dashboard_backend_path()
    log.info(
        "[dashboard_snapshot_warmer] loop starting (interval=%ss, first_delay=210s)",
        ENGINE_DASHBOARD_SNAPSHOT_WARMER_INTERVAL_SECONDS,
    )
    await asyncio.sleep(210)
    while True:
        try:
            from snapshot_cache import load_snapshot, store_snapshot  # type: ignore
            from routers import home as home_router  # type: ignore
            from routers.system_audit import _build_system_audit_fast_payload  # type: ignore

            def _action_board_builder() -> dict:
                snap = load_snapshot("home:action-board:5")
                base = snap.get("data") if isinstance(snap, dict) else None
                return home_router._build_action_board_fast_payload(5, base if isinstance(base, dict) else None)

            jobs = [
                ("home:summary", home_router._build_home_summary_payload),
                ("home:modes", home_router._build_runtime_modes_payload),
                ("home:action-board:5", _action_board_builder),
                ("home:conviction-recovery:8", lambda: home_router._build_conviction_recovery(8)),
                ("home:early-runners:8:24", lambda: home_router._build_early_runner_radar(8, 24)),
                ("home:runner-review:8", lambda: home_router._build_runner_review_payload(8)),
                ("home:memecoin-research:8", lambda: home_router._build_memecoin_research_payload(8)),
                ("system:audit", lambda: _build_system_audit_fast_payload(None)),
            ]
            refreshed: list[str] = []
            for name, builder in jobs:
                try:
                    payload = await asyncio.to_thread(builder)
                    await asyncio.to_thread(store_snapshot, name, payload, status="OK")
                    refreshed.append(name)
                except Exception as exc:
                    log.debug("[dashboard_snapshot_warmer] %s skipped: %s", name, exc)
                await asyncio.sleep(1.0)
            log.info("[dashboard_snapshot_warmer] refreshed=%s", ",".join(refreshed) or "none")
        except Exception as exc:
            log.warning("dashboard_snapshot_warmer error: %s", exc)
        await asyncio.sleep(ENGINE_DASHBOARD_SNAPSHOT_WARMER_INTERVAL_SECONDS)


# ── Main async entry point ────────────────────────────────────────────────────

async def main():
    log.info("=" * 60)
    log.info("Headless Memecoin Engine starting (no Telegram)")
    log.info("=" * 60)

    tasks = []

    # ── Core engine scan ──────────────────────────────────────────────────────
    if _FUNCS["run_engine"]:
        tasks.append(asyncio.create_task(
            _loop("run_engine", _FUNCS["run_engine"],
                  SCAN_INTERVAL_SECONDS, first_delay=1),
            name="run_engine",
        ))

    # ── Watchlist lane ────────────────────────────────────────────────────────
    if WATCHLIST_LANE_ENABLED and WATCHLIST_ENTRIES and _FUNCS["run_watchlist_lane"]:
        tasks.append(asyncio.create_task(
            _loop("run_watchlist_lane", _FUNCS["run_watchlist_lane"],
                  max(300, WATCHLIST_SCAN_INTERVAL_SECONDS), first_delay=25),
            name="run_watchlist_lane",
        ))
    else:
        log.info("Watchlist lane disabled or no entries — skipping.")

    # ── New runner watch ──────────────────────────────────────────────────────
    if NEW_RUNNER_WATCH_ENABLED and _FUNCS["run_new_runner_watch"]:
        tasks.append(asyncio.create_task(
            _loop("run_new_runner_watch", _FUNCS["run_new_runner_watch"],
                  max(300, NEW_RUNNER_SCAN_INTERVAL_SECONDS), first_delay=15),
            name="run_new_runner_watch",
        ))

    # ── Legacy recovery scanner ───────────────────────────────────────────────
    if LEGACY_RECOVERY_ENABLED and _FUNCS["run_legacy_recovery_scanner"]:
        tasks.append(asyncio.create_task(
            _loop("run_legacy_recovery_scanner", _FUNCS["run_legacy_recovery_scanner"],
                  max(300, LEGACY_RECOVERY_SCAN_INTERVAL_SECONDS), first_delay=35),
            name="run_legacy_recovery_scanner",
        ))

    # ── Leverage monitor ──────────────────────────────────────────────────────
    if _FUNCS["run_lev_monitor"]:
        tasks.append(asyncio.create_task(
            _loop("run_lev_monitor", _FUNCS["run_lev_monitor"],
                  CHECK_INTERVAL_SECONDS, first_delay=60),
            name="run_lev_monitor",
        ))

    # ── Outcome evaluator ─────────────────────────────────────────────────────
    if OUTCOME_TRACKING_ENABLED and _FUNCS["run_outcome_evaluator"]:
        tasks.append(asyncio.create_task(
            _loop("run_outcome_evaluator", _FUNCS["run_outcome_evaluator"],
                  OUTCOME_EVAL_INTERVAL_SECONDS, first_delay=90),
            name="run_outcome_evaluator",
        ))
    if HEADLESS_OUTCOME_TRACKER_LOOP_ENABLED:
        tasks.append(asyncio.create_task(_outcome_tracker_loop(), name="outcome_tracker"))

    # ── Perp/Scalp monitors ───────────────────────────────────────────────────
    if HEADLESS_PERP_MONITOR_LOOP_ENABLED:
        tasks.append(asyncio.create_task(_perp_monitor_loop(), name="perp_monitor"))
    if HEADLESS_PERP_SIGNAL_SCAN_LOOP_ENABLED:
        tasks.append(asyncio.create_task(_perp_signal_scan_loop(), name="perp_signal_scan"))
    if HEADLESS_SCALP_MONITOR_LOOP_ENABLED:
        tasks.append(asyncio.create_task(_scalp_monitor_loop(), name="scalp_monitor"))
    if HEADLESS_SCALP_SIGNAL_SCAN_LOOP_ENABLED:
        tasks.append(asyncio.create_task(_scalp_signal_scan_loop(), name="scalp_signal_scan"))
    if HEADLESS_SPOT_MONITOR_LOOP_ENABLED:
        tasks.append(asyncio.create_task(_spot_monitor_loop(), name="spot_monitor"))
    if HEADLESS_SPOT_SIGNAL_SCAN_LOOP_ENABLED:
        tasks.append(asyncio.create_task(_spot_signal_scan_loop(), name="spot_signal_scan"))

    # ── Phase 5: Spot & Memecoin exit monitor ───────────────────────────────
    tasks.append(asyncio.create_task(_exit_monitor_loop(), name="exit_monitor"))

    # ── Phase 5: DCA auto-accumulation monitor ───────────────────────────────
    tasks.append(asyncio.create_task(_dca_monitor_loop(), name="dca_monitor"))

    # ── Phase 5: Auto-entry proposal monitor ─────────────────────────────────
    tasks.append(asyncio.create_task(_entry_monitor_loop(), name="entry_monitor"))

    # ── Memecoin scanner cache refresh ───────────────────────────────────────
    tasks.append(asyncio.create_task(_memecoin_scan_loop(), name="memecoin_scan"))

    # ── Memecoin live auto-buy monitor ───────────────────────────────────────
    tasks.append(asyncio.create_task(_memecoin_auto_buy_loop(), name="memecoin_auto_buy"))

    if HEADLESS_RESEARCH_LOOP_ENABLED:
        tasks.append(asyncio.create_task(_research_loop(), name="research"))

    if HEADLESS_MEMECOIN_DISCOVERY_LOOP_ENABLED:
        tasks.append(asyncio.create_task(_memecoin_discovery_loop(), name="memecoin_discovery"))

    if HEADLESS_RUNNER_HEARTBEAT_LOOP_ENABLED:
        tasks.append(asyncio.create_task(_runner_heartbeat_loop(), name="runner_heartbeat"))

    if HEADLESS_EARLY_RUNNER_RADAR_LOOP_ENABLED:
        tasks.append(asyncio.create_task(_early_runner_radar_loop(), name="early_runner_radar"))

    if HEADLESS_AUTHORITY_REFRESH_LOOP_ENABLED:
        tasks.append(asyncio.create_task(_authority_snapshot_refresh_loop(), name="authority_refresh"))

    if HEADLESS_WHALE_WATCH_LOOP_ENABLED:
        tasks.append(asyncio.create_task(_whale_watch_loop(), name="whale_watch"))

    # ── Eval freshness watchdog ──────────────────────────────────────────────
    if PIPELINE_WATCHDOG_ENABLED:
        tasks.append(asyncio.create_task(_pipeline_watchdog_loop(), name="pipeline_watchdog"))

    # ── Portfolio allocator stream ──────────────────────────────────────────
    if ALLOCATOR_CHECK_ENABLED:
        tasks.append(asyncio.create_task(_allocator_check_loop(), name="allocator_check"))

    # ── Speculation heat stream ─────────────────────────────────────────────
    if SPECULATION_HEAT_ENABLED:
        tasks.append(asyncio.create_task(_speculation_heat_loop(), name="speculation_heat"))

    if TOKEN_STATS_STREAM_ENABLED:
        tasks.append(asyncio.create_task(_token_stats_stream_loop(), name="token_stats_stream"))
    if TOKEN_INTELLIGENCE_ENABLED:
        tasks.append(asyncio.create_task(_token_intelligence_loop(), name="token_intelligence"))
    if LARGE_TRADE_STREAM_ENABLED:
        tasks.append(asyncio.create_task(_large_trade_stream_loop(), name="large_trade_stream"))
    if WALLET_TX_STREAM_ENABLED:
        tasks.append(asyncio.create_task(_wallet_tx_stream_loop(), name="wallet_tx_stream"))
    if PAPER_SIGNAL_LEARNING_ENABLED:
        tasks.append(asyncio.create_task(_paper_signal_learning_loop(), name="paper_signal_learning"))
    if RUNNER_REVIEW_OUTCOME_ENABLED:
        tasks.append(asyncio.create_task(_runner_review_outcome_loop(), name="runner_review_outcome"))
    if RUNNER_REVIEW_AUTO_PAPER_ENABLED:
        tasks.append(asyncio.create_task(_runner_review_auto_paper_loop(), name="runner_review_auto_paper"))
    if MEMECOIN_RESEARCH_DOSSIER_ENABLED:
        tasks.append(asyncio.create_task(_memecoin_research_dossier_loop(), name="memecoin_research_dossier"))
    if MEMECOIN_CATALYST_ENABLED:
        tasks.append(asyncio.create_task(_memecoin_catalyst_loop(), name="memecoin_catalyst"))
    if AI_ANALYST_ENABLED:
        tasks.append(asyncio.create_task(_ai_analyst_loop(), name="ai_analyst"))
    if ENGINE_DASHBOARD_SNAPSHOT_WARMER_ENABLED:
        tasks.append(asyncio.create_task(_dashboard_snapshot_warmer_loop(), name="dashboard_snapshot_warmer"))

    # ── Executor & Arb (optional, read env at runtime) ────────────────────────
    if os.getenv("EXECUTOR_ENABLED", "false").lower() == "true":
        try:
            from utils.executor import position_monitor_loop as _exec_loop
            tasks.append(asyncio.create_task(_exec_loop(), name="executor_monitor"))
            log.info("Executor monitor loop started.")
        except Exception as _e:
            log.warning("Could not start executor loop: %s", _e)

    if os.getenv("ARB_ENABLED", "false").lower() == "true":
        try:
            from utils.dex_price_monitor import arb_monitor_loop as _arb_loop
            # arb_monitor_loop expects an app object — pass a FakeContext
            tasks.append(asyncio.create_task(_arb_loop(FakeContext()), name="arb_monitor"))
            log.info("Arb monitor loop started.")
        except Exception as _e:
            log.warning("Could not start arb monitor loop: %s", _e)

    log.info("All %d task(s) launched. Engine running headlessly.", len(tasks))

    # Run forever — restart any task that dies unexpectedly
    while True:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
        for t in done:
            exc = t.exception()
            if exc:
                log.error("Task %s died: %s — restarting in 10s", t.get_name(), exc, exc_info=exc)
                await asyncio.sleep(10)
                # Re-create the task from the same coroutine would require
                # factories; for now just log — the loop functions themselves
                # catch all exceptions so this should never trigger.
            tasks = [t for t in tasks if not t.done()]
            if not tasks:
                log.critical("All tasks have exited. Shutting down.")
                return


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Headless engine stopped by KeyboardInterrupt.")
