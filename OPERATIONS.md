# Memecoin Engine Operations

## 1) Create/refresh virtualenv

```bash
cd "/Users/abron/Documents/New project/memecoin_engine"
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

## 2) Run manually

```bash
cd "/Users/abron/Documents/New project/memecoin_engine"
source .venv/bin/activate
python main.py
```

## 3) Install launchd auto-start

```bash
cd "/Users/abron/Documents/New project/memecoin_engine"
./scripts/install_launchd.sh
```

Check status:

```bash
launchctl list | grep com.memecoin.engine
```

Stop/unload:

```bash
launchctl unload "$HOME/Library/LaunchAgents/com.memecoin.engine.plist"
```

## 4) Logs

- Stdout: `logs/engine.out.log`
- Stderr: `logs/engine.err.log`

```bash
tail -f logs/engine.out.log
```

## 5) Watchdog tuning (`.env`)

- `WATCHDOG_ENABLED=true`
- `WATCHDOG_INTERVAL_SECONDS=300`
- `WATCHDOG_MAX_SCAN_GAP_MINUTES=90`
- `WATCHDOG_MAX_ALERT_GAP_HOURS=12`

The watchdog sends Telegram anomaly/recovery messages if scans stall or alerts stop for too long.

## 6) Confidence + Regime controls (`.env`)

- `ENABLE_REGIME_GATE=true`
- `REGIME_MIN_SCORE=50`
- `CONFIDENCE_MIN_A=85`
- `CONFIDENCE_MIN_B=70`
- `MIN_CONFIDENCE_TO_ALERT=B`

Alerts are only sent when confidence tier meets `MIN_CONFIDENCE_TO_ALERT`, and when regime gate is enabled, only when regime score is at least `REGIME_MIN_SCORE`.

## 6.1) Strategic vs Tactical profile (`.env`)

- `ENGINE_PROFILE=strategic|tactical`

`strategic` defaults to slower, higher-quality scanning.  
`tactical` defaults to short-term momentum/pullback behavior.

Tactical baseline knobs:
- `SCAN_INTERVAL_SECONDS=1800`
- `MIN_LIQUIDITY=500000`
- `MIN_VOLUME_24H=200000`
- `ALERT_COOLDOWN_HOURS=6`
- `ALERT_THRESHOLD=70`
- `TACTICAL_PULLBACK_MIN_PCT=20`
- `TACTICAL_PULLBACK_MAX_PCT=30`
- `TACTICAL_TREND_MIN_CHANGE_24H=5`
- `TACTICAL_MIN_MOMENTUM_CHANGE_1H=0.3`
- `TACTICAL_MIN_VOL_TO_LIQ_RATIO=0.12`
- `TACTICAL_MAX_LAST_TRADE_AGE_MINUTES=45`
- `TACTICAL_ENABLE_REAL_TECHNICALS=true`
- `TACTICAL_REQUIRE_TECHNICAL_CONFIRMATION=false`
- `TACTICAL_OHLCV_TYPE=15m`
- `TACTICAL_OHLCV_LOOKBACK_HOURS=36`
- `TACTICAL_RSI_PERIOD=14`
- `TACTICAL_RSI_MIN=50`
- `TACTICAL_RSI_MAX=78`
- `TACTICAL_MACD_HIST_MIN=0`

After changing profile values:

```bash
./scripts/install_launchd.sh
```

## 7) Top-N alerts + cooldown caps (`.env`)

- `ALERT_TOP_N=3`
- `MAX_ALERTS_PER_CYCLE=3`
- `ALERT_COOLDOWN_HOURS=12`

The engine ranks candidates by score, then alerts up to `ALERT_TOP_N` while enforcing `MAX_ALERTS_PER_CYCLE` and per-symbol cooldown.

## 8) Risk governor circuit breakers (`.env`)

- `ENABLE_RISK_GOVERNOR=true`
- `GLOBAL_TRADING_PAUSE=false`
- `MAX_ALERTS_PER_DAY=8`
- `MAX_ALERTS_PER_SYMBOL_PER_DAY=2`
- `MAX_CONSECUTIVE_4H_LOSSES=3`
- `LOSS_STREAK_PAUSE_HOURS=6`

Risk governor blocks alerts when limits are breached and can auto-pause after a losing streak.

## 9) Execution-quality filters (`.env`)

- `ENABLE_EXECUTION_QUALITY_FILTERS=true`
- `MIN_VOL_TO_LIQ_RATIO=0.02`
- `MAX_VOL_TO_LIQ_RATIO=3.0`
- `MAX_ABS_CHANGE_24H=80`
- `MIN_PRICE_USD=0.00000001`
- `MIN_LIQUIDITY_PER_HOLDER=10`

These filters reject low-quality or unstable setups before scoring.

## 10) Outcome-driven symbol controls (`.env`)

- `SYMBOL_CONTROL_ENABLED=true`
- `SYMBOL_LOSS_STREAK_TRIGGER=3`
- `SYMBOL_COOLDOWN_HOURS=24`
- `SYMBOL_BLACKLIST_MIN_SAMPLES=3`
- `SYMBOL_BLACKLIST_AVG_24H_PCT=-8`
- `SYMBOL_BLACKLIST_HOURS=72`

Symbols with repeated poor outcomes are auto-cooled or blacklisted.

## 11) Weekly auto-tuning report (`.env`)

- `WEEKLY_TUNING_ENABLED=true`
- `WEEKLY_TUNING_DAY_UTC=SUN` (`MON|TUE|WED|THU|FRI|SAT|SUN`)
- `WEEKLY_TUNING_HOUR_UTC=1`
- `WEEKLY_TUNING_LOOKBACK_DAYS=7`
- `WEEKLY_TUNING_MIN_OUTCOMES_4H=8`

The report recommends updated `ALERT_THRESHOLD`, `REGIME_MIN_SCORE`, and `MIN_CONFIDENCE_TO_ALERT` based on recent scan outcomes, prioritizing 4h/24h realized returns.

## 12) Outcome attribution (`.env`)

- `OUTCOME_TRACKING_ENABLED=true`
- `OUTCOME_EVAL_INTERVAL_SECONDS=300`
- `OUTCOME_EVAL_BATCH_SIZE=50`

Alerts are written to `alert_outcomes` and evaluated at +1h/+4h/+24h using live BirdEye price snapshots.

## 13) One-click apply of weekly tuning

Preview only:

```bash
./scripts/apply_weekly_tuning.sh --dry-run
```

Apply + reload launchd:

```bash
./scripts/apply_weekly_tuning.sh --reload
```

Notes:
- Writes backup before any change: `.env.bak.YYYYMMDD_HHMMSS`
- Applies bounded values only:
  - `ALERT_THRESHOLD` in `[55, 95]`
  - `REGIME_MIN_SCORE` in `[35, 70]`
  - `MIN_CONFIDENCE_TO_ALERT` in `{A,B,C}`

## 14) Singleton protection

- `PROCESS_LOCK_FILE=engine.lock`

Only one engine process can run at a time. Extra starts exit immediately with a lock warning.

## 15) Structured JSON logs + rotation

- `LOG_JSON_ENABLED=true`
- `LOG_JSON_PATH=logs/engine.jsonl`
- `LOG_MAX_BYTES=5242880`
- `LOG_BACKUP_COUNT=5`

JSON logs are emitted to `LOG_JSON_PATH` with automatic rotation.

## 16) Health snapshot command

```bash
./scripts/health_snapshot.sh
```

Returns service status, last scan/alert timestamps, 24h metrics, portfolio simulation metrics, risk-pause state, symbol-control counts, and outcome queue depth.

## 17) Telegram controls

Bot commands (authorized chat only):
- `/status`
- `/mode strategic`
- `/mode tactical`
- `/risk`
- `/pause 6`
- `/resume`
- `/performance`
- `/digest`
- `/help`

Alert actions:
- `DexScreener` button
- `BirdEye` button
- `Mute 24h` button
- `Acknowledge` button

Digest settings (`.env`):
- `SIGNAL_DIGEST_ENABLED=true`
- `SIGNAL_DIGEST_INTERVAL_SECONDS=10800`
- `SIGNAL_DIGEST_LOOKBACK_HOURS=6`
- `SIGNAL_DIGEST_MAX_ITEMS=5`

Button toggle (`.env`):
- `TELEGRAM_ACTION_BUTTONS_ENABLED=true`
