"""
launch_listener.py — Real-time new token launch detection.

Runs two parallel detection streams:
  1. Pump.fun WebSocket  — fires the moment a new token is created on-chain
  2. DexScreener polling — new token profiles every 15s (catches Raydium / Orca pools)

When a new launch is detected, it:
  1. Fetches BirdEye overview for the mint (enrichment)
  2. Runs it through the scoring pipeline
  3. If score ≥ LAUNCH_MIN_SCORE:  sends a Telegram alert + queues outcome tracking
  4. Logs the detection to data_storage/launch_feed.jsonl for dashboard streaming

This module is designed to run as an asyncio background task alongside the
existing 30-min scheduled scan — it is the "fast lane" for new launches.

Env vars:
  LAUNCH_LISTENER_ENABLED   bool   default false — master switch
  LAUNCH_MIN_SCORE          float  default 65    — lower than ALERT_THRESHOLD to catch early movers
  LAUNCH_MIN_LIQUIDITY      float  default 20000 — min $ liquidity on new pool
  LAUNCH_MIN_VOLUME         float  default 5000  — min $ 5m volume
  LAUNCH_COOLDOWN_SECONDS   int    default 300   — don't re-alert same mint within 5m
  LAUNCH_MAX_AGE_MINUTES    int    default 60    — ignore tokens older than 1h
  LAUNCH_PUMP_WS_ENABLED    bool   default true  — enable Pump.fun WebSocket stream
  LAUNCH_DEX_POLL_ENABLED   bool   default true  — enable DexScreener profile polling
  LAUNCH_DEX_POLL_INTERVAL  int    default 15    — seconds between DexScreener polls
"""

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests

log = logging.getLogger("launch_listener")

# ─── Config ───────────────────────────────────────────────────────────────────

def _env_bool(key: str, default: bool) -> bool:
    return os.getenv(key, str(default)).lower() in ("true", "1", "yes")

def _env_float(key: str, default: float) -> float:
    try:
        return float(os.getenv(key, str(default)))
    except Exception:
        return default

def _env_int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, str(default)))
    except Exception:
        return default

LAUNCH_LISTENER_ENABLED  = _env_bool("LAUNCH_LISTENER_ENABLED", False)
LAUNCH_MIN_SCORE         = _env_float("LAUNCH_MIN_SCORE", 65.0)
LAUNCH_MIN_LIQUIDITY     = _env_float("LAUNCH_MIN_LIQUIDITY", 20_000)
LAUNCH_MIN_VOLUME        = _env_float("LAUNCH_MIN_VOLUME", 5_000)
LAUNCH_COOLDOWN_SECONDS  = _env_int("LAUNCH_COOLDOWN_SECONDS", 300)
LAUNCH_MAX_AGE_MINUTES   = _env_int("LAUNCH_MAX_AGE_MINUTES", 60)
LAUNCH_PUMP_WS_ENABLED   = _env_bool("LAUNCH_PUMP_WS_ENABLED", True)
LAUNCH_DEX_POLL_ENABLED  = _env_bool("LAUNCH_DEX_POLL_ENABLED", True)
LAUNCH_DEX_POLL_INTERVAL = _env_int("LAUNCH_DEX_POLL_INTERVAL", 15)

# DexScreener new token profiles endpoint
DEXSCREENER_PROFILES_URL = "https://api.dexscreener.com/token-profiles/latest/v1"
DEXSCREENER_PAIRS_URL    = "https://api.dexscreener.com/latest/dex/tokens/{mint}"

# Pump.fun WebSocket — public, no key needed
PUMP_WS_URL = "wss://pumpportal.fun/api/data"

# Storage
_DATA_DIR = Path(__file__).resolve().parents[1] / "data_storage"
_LAUNCH_FEED_PATH = _DATA_DIR / "launch_feed.jsonl"
_DATA_DIR.mkdir(exist_ok=True)

# ─── In-memory state ──────────────────────────────────────────────────────────

# mint → last_alerted_ts  (prevents duplicate alerts within cooldown window)
_alerted: dict[str, float] = {}

# mint → first_seen_ts  (tracks when we first saw this token this session)
_seen: dict[str, float] = {}

# Circular buffer of recent launches for the dashboard API
_recent_launches: list[dict] = []
_MAX_RECENT = 200

# Shared bot reference (set by main.py on startup)
_bot_ref = None
_chat_id: Optional[str] = None
_telegram_app_ref = None


def set_bot(app, chat_id: str):
    """Called from main.py after the Telegram app is built."""
    global _bot_ref, _chat_id, _telegram_app_ref
    _telegram_app_ref = app
    _bot_ref = app.bot
    _chat_id = chat_id


# ─── Launch feed storage ──────────────────────────────────────────────────────

def _append_launch_feed(entry: dict):
    """Append a launch event to the JSONL feed file and in-memory buffer."""
    global _recent_launches
    entry["_ts"] = datetime.utcnow().isoformat() + "Z"
    _recent_launches.append(entry)
    if len(_recent_launches) > _MAX_RECENT:
        _recent_launches = _recent_launches[-_MAX_RECENT:]
    try:
        with open(_LAUNCH_FEED_PATH, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as e:
        log.debug("launch feed write error: %s", e)


def get_recent_launches(limit: int = 50) -> list[dict]:
    """Return recent launches for dashboard API."""
    return list(reversed(_recent_launches[-limit:]))


# ─── Token enrichment + scoring ───────────────────────────────────────────────

def _fetch_dexscreener_token(mint: str) -> Optional[dict]:
    """Fetch token data from DexScreener for a given mint address."""
    try:
        url = DEXSCREENER_PAIRS_URL.format(mint=mint)
        resp = requests.get(url, timeout=8)
        if resp.status_code != 200:
            return None
        data = resp.json()
        pairs = data.get("pairs") or []
        if not pairs:
            return None

        # Pick the pair with the highest liquidity
        pairs_sol = [p for p in pairs if p.get("chainId") == "solana"]
        if not pairs_sol:
            pairs_sol = pairs
        pairs_sol.sort(key=lambda p: float(p.get("liquidity", {}).get("usd", 0) or 0), reverse=True)
        pair = pairs_sol[0]

        liq   = float(pair.get("liquidity", {}).get("usd", 0) or 0)
        vol5m = float(pair.get("volume", {}).get("m5", 0) or 0)
        vol1h = float(pair.get("volume", {}).get("h1", 0) or 0)
        vol24 = float(pair.get("volume", {}).get("h24", 0) or 0)
        price = float(pair.get("priceUsd") or 0)
        c1h   = float(pair.get("priceChange", {}).get("h1", 0) or 0)
        c6h   = float(pair.get("priceChange", {}).get("h6", 0) or 0)
        c24   = float(pair.get("priceChange", {}).get("h24", 0) or 0)
        mc    = float(pair.get("marketCap") or pair.get("fdv") or 0)

        # pairCreatedAt is unix ms
        created_at = pair.get("pairCreatedAt")
        age_minutes = None
        if created_at:
            try:
                age_minutes = (time.time() - int(created_at) / 1000) / 60
            except Exception:
                pass

        symbol = pair.get("baseToken", {}).get("symbol", "UNKNOWN")
        name   = pair.get("baseToken", {}).get("name", "")

        return {
            "symbol":        symbol,
            "name":          name,
            "address":       mint,
            "pair_address":  pair.get("pairAddress"),
            "price":         price,
            "liquidity":     liq,
            "volume_5m":     vol5m,
            "volume_24h":    vol24,
            "volume_1h":     vol1h,
            "change_1h":     c1h,
            "change_6h":     c6h,
            "change_24h":    c24,
            "market_cap":    mc,
            "age_minutes":   age_minutes,
            "dex_url":       pair.get("url"),
            "source":        "dexscreener",
        }
    except Exception as e:
        log.debug("dexscreener fetch error for %s: %s", mint, e)
        return None


def _try_birdeye_enrich(token: dict) -> dict:
    """Attempt to enrich with BirdEye overview. Non-fatal if it fails."""
    try:
        from data.birdeye import fetch_birdeye_token_overview
        address = token.get("address")
        if not address:
            return token
        overview = fetch_birdeye_token_overview(address)
        if overview:
            enriched = dict(token)
            enriched.update(overview)
            return enriched
    except Exception as e:
        log.debug("birdeye enrich error: %s", e)
    return token


def _score_launch_token(token: dict) -> Optional[float]:
    """Run the existing scoring pipeline on a launch token. Returns score or None."""
    try:
        from scoring import calculate_token_score
        token["engine_profile"] = "tactical"  # Use tactical profile for launches
        return calculate_token_score(token)
    except Exception as e:
        log.debug("scoring error: %s", e)
        return None


def _confidence_grade(score: float) -> str:
    a_min = float(os.getenv("CONFIDENCE_MIN_A", "82"))
    b_min = float(os.getenv("CONFIDENCE_MIN_B", "72"))
    if score >= a_min:
        return "A"
    if score >= b_min:
        return "B"
    return "C"


# ─── Alert sending ────────────────────────────────────────────────────────────

async def _send_launch_alert(token: dict, score: float, source: str):
    """Format and send a Telegram alert for a high-scoring new launch."""
    if not _bot_ref or not _chat_id:
        return

    symbol   = token.get("symbol", "UNKNOWN")
    price    = token.get("price", 0)
    liq      = token.get("liquidity", 0)
    vol5m    = token.get("volume_5m", 0)
    vol1h    = token.get("volume_1h", 0)
    mc       = token.get("market_cap", 0)
    c1h      = token.get("change_1h", 0)
    age_min  = token.get("age_minutes")
    dex_url  = token.get("dex_url") or f"https://dexscreener.com/solana/{token.get('address', '')}"
    pump_url = f"https://pump.fun/{token.get('address', '')}"

    conf  = _confidence_grade(score)
    grade = {"A": "🟢", "B": "🟡", "C": "⚪"}.get(conf, "⚪")

    age_str = f"{age_min:.0f}m old" if age_min is not None else "new"

    lines = [
        f"⚡ <b>LAUNCH ALERT</b> — ${symbol}",
        f"{grade} Score: <b>{score:.0f}</b>  |  Grade: <b>{conf}</b>  |  {age_str}",
        "",
        f"💰 Price:  <code>${price:.8f}</code>",
        f"🌊 Liq:    <code>${liq:,.0f}</code>",
        f"📊 Vol 5m: <code>${vol5m:,.0f}</code>  |  1h: <code>${vol1h:,.0f}</code>",
    ]
    if mc:
        lines.append(f"🎯 MCap:   <code>${mc:,.0f}</code>")
    if c1h:
        arrow = "▲" if c1h >= 0 else "▼"
        lines.append(f"📈 1h:     {arrow} <code>{c1h:+.1f}%</code>")
    lines += [
        "",
        f"🔗 <a href='{dex_url}'>DexScreener</a>  |  <a href='{pump_url}'>Pump.fun</a>",
        f"<code>{token.get('address', '')[:44]}</code>",
        f"<i>via {source}</i>",
    ]

    msg = "\n".join(lines)
    try:
        await _bot_ref.send_message(
            chat_id=int(_chat_id),
            text=msg,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        log.info("Launch alert sent: %s score=%.1f", symbol, score)
    except Exception as e:
        log.warning("Failed to send launch alert for %s: %s", symbol, e)


# ─── Core processing pipeline ─────────────────────────────────────────────────

async def _process_mint(mint: str, source: str, raw_data: Optional[dict] = None):
    """
    Full pipeline for a newly detected mint:
    1. Cooldown check
    2. Fetch DexScreener data (liquidity, volume, age)
    3. Age + liquidity gate
    4. BirdEye enrichment
    5. Score
    6. Alert if score ≥ threshold
    7. Log to launch feed
    """
    now = time.time()

    # Cooldown check
    if mint in _alerted:
        if now - _alerted[mint] < LAUNCH_COOLDOWN_SECONDS:
            return

    # Don't re-score tokens we've already seen and decided not to alert on
    # within the last 2 minutes (avoid hammering API on DexScreener re-polls)
    if mint in _seen:
        if now - _seen[mint] < 120:
            return
    _seen[mint] = now

    # Step 1: Get market data
    token = raw_data or {}
    if not token.get("liquidity"):
        dex_data = await asyncio.get_event_loop().run_in_executor(
            None, _fetch_dexscreener_token, mint
        )
        if dex_data:
            token.update(dex_data)

    if not token.get("address"):
        token["address"] = mint

    # Step 2: Age filter — ignore tokens older than LAUNCH_MAX_AGE_MINUTES
    age_min = token.get("age_minutes")
    if age_min is not None and age_min > LAUNCH_MAX_AGE_MINUTES:
        log.debug("Skipping %s — too old (%.0fm)", mint, age_min)
        return

    # Step 3: Liquidity + volume gates
    liq   = float(token.get("liquidity", 0) or 0)
    vol5m = float(token.get("volume_5m", 0) or 0)
    vol1h = float(token.get("volume_1h", 0) or 0)
    vol   = max(vol5m * 12, vol1h)  # Annualise 5m to rough 1h equivalent

    if liq < LAUNCH_MIN_LIQUIDITY:
        log.debug("Skipping %s — low liq $%.0f", mint, liq)
        return
    if vol < LAUNCH_MIN_VOLUME and vol5m < LAUNCH_MIN_VOLUME / 5:
        log.debug("Skipping %s — low volume", mint)
        return

    # Step 4: BirdEye enrichment (non-fatal)
    token = await asyncio.get_event_loop().run_in_executor(
        None, _try_birdeye_enrich, token
    )

    # Step 5: Score
    score = await asyncio.get_event_loop().run_in_executor(
        None, _score_launch_token, token
    )
    if score is None:
        score = 0.0

    symbol = token.get("symbol", mint[:8])
    log.info("Launch candidate: %s  score=%.1f  liq=$%.0f  source=%s",
             symbol, score, liq, source)

    # Step 6: Log to feed regardless of score (dashboard shows all detections)
    feed_entry = {
        "mint":       mint,
        "symbol":     symbol,
        "score":      round(score, 1),
        "liquidity":  liq,
        "volume_5m":  vol5m,
        "volume_1h":  vol1h,
        "change_1h":  token.get("change_1h"),
        "age_minutes":token.get("age_minutes"),
        "market_cap": token.get("market_cap"),
        "price":      token.get("price"),
        "source":     source,
        "alerted":    score >= LAUNCH_MIN_SCORE,
    }
    _append_launch_feed(feed_entry)

    # Step 7: Alert if score ≥ threshold
    if score >= LAUNCH_MIN_SCORE:
        _alerted[mint] = now

        # Queue outcome tracking
        try:
            from utils.db import queue_alert_outcome
            entry_price = float(token.get("price") or 0)
            if entry_price > 0:
                queue_alert_outcome({
                    "symbol":       symbol,
                    "mint":         mint,
                    "entry_price":  entry_price,
                    "score":        score,
                    "regime_score": 0,
                    "regime_label": "LAUNCH",
                    "confidence":   _confidence_grade(score),
                    "lane":         "launch",
                    "source":       source,  # 'pump_fun_ws' or 'dexscreener_profile'
                })
        except Exception as e:
            log.debug("outcome queue error: %s", e)

        # Auto-execute if executor is enabled
        try:
            exec_enabled = os.getenv("EXECUTOR_ENABLED", "false").lower() == "true"
            if exec_enabled and token.get("address"):
                from utils.position_sizing import calculate_position_size
                from utils.executor import execute_signal as _exec_signal
                portfolio_usd = float(os.getenv("PORTFOLIO_USD", "1000"))
                pos = calculate_position_size(token, portfolio_usd)
                signal = {
                    "symbol":       symbol,
                    "mint":         mint,
                    "entry_price":  float(token.get("price") or 0),
                    "score":        score,
                    "confidence":   _confidence_grade(score),
                    "regime_label": "LAUNCH",
                    "position_usd": pos.get("position_usd", 0),
                }
                asyncio.create_task(_exec_signal(signal))
        except Exception as e:
            log.debug("executor hook error: %s", e)

        await _send_launch_alert(token, score, source)

        # ── B1: Cross-DEX arb monitor ──────────────────────────────────────────
        try:
            from utils.dex_price_monitor import enqueue_launch_for_arb as _arb_enqueue
            entry_price_arb = float(token.get("price") or 0)
            if entry_price_arb > 0:
                _arb_enqueue(
                    mint=mint,
                    symbol=symbol,
                    score=score,
                    entry_price=entry_price_arb,
                    source=source,
                )
        except Exception as _arb_e:
            log.debug("arb enqueue error: %s", _arb_e)


# ─── Stream 1: DexScreener new token profile polling ──────────────────────────

async def _dexscreener_poll_loop():
    """
    Poll DexScreener's /token-profiles/latest/v1 every LAUNCH_DEX_POLL_INTERVAL
    seconds and process any new mints we haven't seen before.
    """
    log.info("DexScreener launch poll starting (interval=%ds)", LAUNCH_DEX_POLL_INTERVAL)
    headers = {"User-Agent": "memecoin-engine/1.0"}

    while True:
        try:
            resp = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: requests.get(DEXSCREENER_PROFILES_URL, headers=headers, timeout=10)
            )
            if resp.status_code == 200:
                profiles = resp.json()
                if isinstance(profiles, list):
                    for profile in profiles:
                        mint = (
                            profile.get("tokenAddress")
                            or profile.get("address")
                            or profile.get("mint")
                        )
                        chain = profile.get("chainId", "")
                        if not mint or chain != "solana":
                            continue
                        asyncio.create_task(_process_mint(mint, "dexscreener_profile"))
        except Exception as e:
            log.debug("DexScreener poll error: %s", e)

        await asyncio.sleep(LAUNCH_DEX_POLL_INTERVAL)


# ─── Stream 2: Pump.fun WebSocket ─────────────────────────────────────────────

async def _pump_ws_loop():
    """
    Connect to Pump.fun WebSocket and subscribe to new token creation events.
    Reconnects automatically on disconnect.
    """
    log.info("Pump.fun WebSocket starting: %s", PUMP_WS_URL)

    while True:
        try:
            import websockets  # type: ignore
            async with websockets.connect(
                PUMP_WS_URL,
                ping_interval=30,
                ping_timeout=10,
                close_timeout=5,
            ) as ws:
                # Subscribe to new token creation events
                await ws.send(json.dumps({"method": "subscribeNewToken"}))
                log.info("Pump.fun WS connected — subscribed to new tokens")

                async for raw in ws:
                    try:
                        msg = json.loads(raw)
                        event_type = msg.get("txType") or msg.get("type") or ""

                        # New token creation event
                        if event_type in ("create", "newToken", "mint"):
                            mint = msg.get("mint") or msg.get("tokenAddress")
                            if not mint:
                                continue

                            symbol = msg.get("symbol") or msg.get("name") or mint[:8]
                            name   = msg.get("name") or symbol

                            log.info("Pump.fun new token: %s (%s)", symbol, mint[:16])

                            # Build initial token dict from WS payload
                            raw_token = {
                                "symbol":  symbol,
                                "name":    name,
                                "address": mint,
                                "price":   float(msg.get("vSolInBondingCurve", 0) or 0) * 1e-9,
                                "market_cap": float(msg.get("marketCapSol", 0) or 0),
                                "age_minutes": 0,  # Brand new
                                "source":  "pump_fun_ws",
                            }

                            asyncio.create_task(_process_mint(mint, "pump_fun", raw_token))

                    except json.JSONDecodeError:
                        pass
                    except Exception as e:
                        log.debug("Pump.fun WS message error: %s", e)

        except Exception as e:
            log.warning("Pump.fun WS disconnected: %s — reconnecting in 10s", e)
            await asyncio.sleep(10)


# ─── Main entry point ─────────────────────────────────────────────────────────

async def launch_listener_main(app=None, chat_id: str = None):
    """
    Entry point called from main.py.
    Starts both detection streams concurrently.
    """
    if not LAUNCH_LISTENER_ENABLED:
        log.info("Launch listener disabled (LAUNCH_LISTENER_ENABLED=false)")
        return

    if app and chat_id:
        set_bot(app, chat_id)

    tasks = []

    if LAUNCH_DEX_POLL_ENABLED:
        tasks.append(asyncio.create_task(_dexscreener_poll_loop()))

    if LAUNCH_PUMP_WS_ENABLED:
        # Try to import websockets — if not available, skip silently
        try:
            import websockets  # noqa: F401
            tasks.append(asyncio.create_task(_pump_ws_loop()))
        except ImportError:
            log.warning(
                "websockets package not installed — Pump.fun WS disabled. "
                "Install with: pip install websockets"
            )

    if not tasks:
        log.warning("Launch listener: no streams enabled")
        return

    log.info("Launch listener running: %d stream(s) active", len(tasks))
    await asyncio.gather(*tasks, return_exceptions=True)
