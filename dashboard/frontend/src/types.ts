// types.ts — TypeScript interfaces matching all API response shapes

export interface Signal {
  id: number
  ts_utc: string
  symbol: string
  mint: string | null
  pair_address: string | null
  score_total: number | null
  decision: string
  regime_score: number | null
  regime_label: string | null
  liquidity_usd: number | null
  volume_24h: number | null
  price_usd: number | null
  change_24h: number | null
  rel_strength_vs_sol: number | null
  conviction: number | null   // 1=C, 2=B, 3=A
  setup_type: string | null
  category: string | null
  notes: string | null
  helius_grade: string | null
}

export interface WatchCard {
  symbol: string
  address: string
  status: 'Momentum' | 'Reclaim' | 'Range' | 'Breakdown' | 'Volatile' | 'Illiquid' | 'NoData'
  reason: string
  has_live_data: boolean
  heat?: 'HOT' | 'ACTIVE' | 'MOVING' | 'COLD'
  vol_to_liq?: number
  price: number | null
  market_cap: number | null
  liquidity: number | null
  volume_24h: number | null
  change_1h: number | null
  change_24h: number | null
  txns_h1: number | null
}

export interface OutcomeWindow {
  n: number
  wins: number
  avg: number
  win_rate: number
}

export interface OutcomeWinrates {
  lookback_days: number
  outcomes_1h: OutcomeWindow
  outcomes_4h: OutcomeWindow
  outcomes_24h: OutcomeWindow
}

export interface EquityPoint {
  ts: string
  equity: number
  ret: number
  symbol: string
}

export interface HistogramBucket {
  range: string
  count: number
}

export interface ScoreDistribution {
  buckets: HistogramBucket[]
  p50: number
  p75: number
  p90: number
  total: number
}

export interface PortfolioMetrics {
  lookback_days: number
  horizon_hours: number
  trades: number
  avg_return_pct: number
  median_return_pct: number
  win_rate_pct: number
  payoff_ratio: number
  expectancy_pct: number
  max_drawdown_pct: number
  equity_end: number
}

export interface PerformanceSummary {
  scans: number
  alerts: number
  alert_rate: number
  avg_score: number
  max_score: number
  top_alert_symbols: string[]
}

export interface RegimePoint {
  ts_utc: string
  sol_change_24h: number | null
  breadth_pct: number | null
  liquidity_score: number | null
  volume_score: number | null
  regime_score: number | null
  regime_label: string | null
}

export interface RiskState {
  mode: 'NORMAL' | 'CAUTIOUS' | 'DEFENSIVE'
  emoji: string
  streak: number
  threshold_delta: number
  size_multiplier: number
  min_confidence: string | null
  paused: boolean
  pause?: {
    pause_until?: string
    reason?: string
  }
}

export interface Trade {
  id: number
  symbol: string
  mint: string | null
  pair_address: string | null
  entry_price: number
  exit_price: number | null
  stop_price: number
  pnl_pct: number | null
  r_multiple: number | null
  opened_ts_utc: string
  closed_ts_utc: string | null
  setup_type: string | null
  regime_label: string | null
  notes: string | null
}

export interface TradeSummary {
  total_closed: number
  wins: number
  losses: number
  win_rate: number
  avg_pnl: number
  avg_r: number
  total_pnl: number
}

export interface LeaderboardEntry {
  symbol: string
  score: number
  regime_label: string | null
  change_24h: number | null
  appearances: number
  last_seen: string
}

export interface ConfigValues {
  ALERT_THRESHOLD: number
  REGIME_MIN_SCORE: number
  MIN_CONFIDENCE_TO_ALERT: string
  MAX_ALERTS_PER_CYCLE: number
  PORTFOLIO_USD: number
}
