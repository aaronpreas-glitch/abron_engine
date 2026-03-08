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
import logging
import os
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

SCAN_INTERVAL_SECONDS               = _cfg("SCAN_INTERVAL_SECONDS",               3600)
WATCHLIST_SCAN_INTERVAL_SECONDS     = _cfg("WATCHLIST_SCAN_INTERVAL_SECONDS",     1800)
NEW_RUNNER_SCAN_INTERVAL_SECONDS    = _cfg("NEW_RUNNER_SCAN_INTERVAL_SECONDS",    1800)
LEGACY_RECOVERY_SCAN_INTERVAL_SECONDS = _cfg("LEGACY_RECOVERY_SCAN_INTERVAL_SECONDS", 1800)
OUTCOME_EVAL_INTERVAL_SECONDS       = _cfg("OUTCOME_EVAL_INTERVAL_SECONDS",       3600)
CHECK_INTERVAL_SECONDS              = _cfg("CHECK_INTERVAL_SECONDS",               60)

log.info(
    "Intervals — engine=%ss  watchlist=%ss  new_runner=%ss  "
    "legacy=%ss  outcome=%ss  lev_monitor=%ss",
    SCAN_INTERVAL_SECONDS, WATCHLIST_SCAN_INTERVAL_SECONDS,
    NEW_RUNNER_SCAN_INTERVAL_SECONDS, LEGACY_RECOVERY_SCAN_INTERVAL_SECONDS,
    OUTCOME_EVAL_INTERVAL_SECONDS, CHECK_INTERVAL_SECONDS,
)

# ── Feature-flag helpers ──────────────────────────────────────────────────────
def _env_bool(name: str, default: bool = True) -> bool:
    return os.getenv(name, "true" if default else "false").lower() in ("1", "true", "yes")

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
    """Swing perp position monitor — every 60s."""
    while True:
        try:
            from utils.perp_executor import perp_monitor_step
            await perp_monitor_step()
        except Exception as _e:
            log.debug("perp_monitor_step error: %s", _e)
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

    # ── Perp/Scalp monitors ───────────────────────────────────────────────────
    tasks.append(asyncio.create_task(_perp_monitor_loop(), name="perp_monitor"))
    tasks.append(asyncio.create_task(_scalp_monitor_loop(), name="scalp_monitor"))

    # ── Executor & Arb (optional, read env at runtime) ────────────────────────
    if os.getenv("EXECUTOR_ENABLED", "false").lower() == "true":
        try:
            from utils.auto_executor import executor_position_monitor_loop as _exec_loop
            tasks.append(asyncio.create_task(_exec_loop(), name="executor_monitor"))
            log.info("Auto-executor monitor loop started.")
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
