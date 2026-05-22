import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../api'
import { liveBudgetedInterval, slowBudgetedInterval } from '../queryBudget'

const MONO: React.CSSProperties = { fontFamily: 'JetBrains Mono, monospace' }

interface AIAnalystData {
  generated_at: string
  status: string
  source: string
  headline: string
  top_lesson: string
  what_improved?: string[]
  what_degraded?: string[]
  review_now?: string[]
  do_not_change_yet?: string[]
}

interface OutcomeInsight {
  kind: string
  title: string
  detail: string
  metric: number
  confidence: string
}

interface OutcomeInsightsData {
  window_days: number
  closed_trades: number
  attributed_trades: number
  attribution_coverage_pct: number
  status: string
  minimum_required: number
  headline: string
  highlights: OutcomeInsight[]
}

interface CalibrationGroup {
  label: string
  count: number
  win_rate: number | null
  avg_pnl_pct: number | null
  avg_latency_minutes: number | null
  avg_move_cost_pct: number | null
  avg_mfe_pct: number | null
  avg_mae_pct: number | null
  avg_capture_ratio_pct: number | null
}

interface CalibrationTrade {
  trade_id: number
  symbol: string
  pnl_pct: number | null
  entry_timing_bucket: string
  signal_window_phase: string
  profit_room_label: string
  regime_label: string
  minutes_scan_to_entry: number | null
  pct_move_scan_to_entry: number | null
  max_favorable_excursion_pct: number | null
  max_adverse_excursion_pct: number | null
  capture_ratio_pct: number | null
  exit_reason: string | null
}

interface CalibrationInsight {
  kind: string
  title: string
  detail: string
  metric: number
}

interface CalibrationView {
  summary: {
    window_days: number
    closed_trades: number
    calibrated_trades: number
    calibration_coverage_pct?: number
  }
  overview: {
    avg_latency_minutes?: number | null
    avg_move_cost_pct?: number | null
    avg_mfe_pct?: number | null
    avg_mae_pct?: number | null
    avg_capture_ratio_pct?: number | null
  }
  groups: {
    entry_timing?: CalibrationGroup[]
    signal_window_phase?: CalibrationGroup[]
    profit_room?: CalibrationGroup[]
    regime?: CalibrationGroup[]
  }
  recent_trades: CalibrationTrade[]
  insights: CalibrationInsight[]
}

interface ScoreAnalysis {
  config_score_min: number
  verdict: { label: string; message: string }
  optimal_window: { lo: number; hi: number; n: number; wr: number; avg_24h: number } | null
  tuner: {
    min_score: number
    max_score: number
    confidence: string
    sample_size: number
    win_rate: number
  } | null
  horizon_comparison?: {
    n_both: number
    verdict: { label: string; message: string }
  }
}

interface SpeculationHeatData {
  generated_at?: string
  heat_state?: string
  heat_score?: number
  momentum?: string
  quality_score?: number
  note?: string
}

interface BestActionData {
  status?: string
  winner?: {
    arm: string
    action: string
    symbol?: string | null
    conviction?: number | null
    reason?: string | null
    manual_only?: boolean
  }
}

interface ActionBoardItem {
  opportunity_key: string
  state: string
  lane: string
  system: string
  symbol: string | null
  token_address?: string | null
  action: string
  intended_action: string | null
  priority: string
  priority_score: number
  confidence: string
  reason: string
  blockers: string[]
  unlock_hint: string | null
  block_state: string
  action_state: string
  proof_state: string | null
  size_guidance: string | null
}

interface ActionBoardData {
  generated_at: string
  headline: { state: string; note: string; top_candidate: ActionBoardItem | null }
  summary: {
    ready_now_count: number
    blocked_count: number
    watch_count: number
    hold_count: number
    changed_count: number
    candidate_count: number
  }
  ready_now: ActionBoardItem[]
  best_blocked: ActionBoardItem[]
  watchlist: ActionBoardItem[]
  holds: ActionBoardItem[]
  new_since_last_check: ActionBoardItem[]
}

interface RuntimeStreamStatus {
  status?: string
  tracking_enabled?: boolean
  tracked_count?: number
  worker_count?: number
  polled_wallets?: number
  failed_wallets?: number
  quarantined_wallets?: string[]
  matched_events?: number
  persisted_events?: number
  behavior_mints?: number
  checked_at?: string | null
  detail?: string | null
}

interface ProviderStatus {
  status?: string
  reason?: string | null
  detail?: string | null
  updated_at?: string | null
  cooldown_until?: string | null
}

interface PerpPatternRow {
  count: number
  avg_pnl_usd?: number | null
  avg_return_pct?: number | null
  total_pnl_usd?: number | null
  side?: string
  weekday?: string
  phase?: string
  regime_label?: string
}

interface PerpPatterns {
  mode?: {
    executor_enabled?: boolean
    dry_run?: boolean
    scalp_enabled?: boolean
  }
  summary?: {
    closed_trades?: number
    avg_pnl_usd?: number | null
    avg_return_pct?: number | null
  }
  by_side?: PerpPatternRow[]
  by_weekday?: PerpPatternRow[]
  by_phase?: PerpPatternRow[]
  by_regime?: PerpPatternRow[]
  headline?: string
}

interface ManualPerpJournalRow {
  id: number
  ts: string
  symbol: string
  action: string
  side: string
  price?: number | null
  size_usd?: number | null
  deposit_withdraw_usd?: number | null
  fee_usd?: number | null
  realized_pnl_usd?: number | null
  source?: string
  notes?: string
}

interface ManualPerpJournal {
  summary?: {
    total_events?: number
    realized_events?: number
    total_realized_pnl_usd?: number | null
    avg_realized_pnl_usd?: number | null
    first_event_ts?: string | null
    last_event_ts?: string | null
  }
  by_action?: Array<{
    action: string
    side: string
    count: number
    total_realized_pnl_usd?: number | null
    avg_realized_pnl_usd?: number | null
  }>
  recent?: ManualPerpJournalRow[]
  headline?: string
}

interface SolPerpBias {
  generated_at: string
  current_weekday: string
  current_phase: string
  historical_bias: string
  manual_bias: string
  system_bias: string
  combined_bias: string
  alignment: string
  headline: string
  historical_note?: string
  manual_note?: string
  phase_stats?: {
    count?: number
    avg_return_pct?: number | null
    total_pnl_usd?: number | null
  }
  manual_stats?: {
    realized_events?: number
    short_realized_pnl_usd?: number | null
    long_realized_pnl_usd?: number | null
  }
}

interface PaperSignalPosition {
  id?: number
  symbol?: string
  mint?: string
  status?: string
  opened_ts_utc?: string | null
  closed_ts_utc?: string | null
  last_review_ts_utc?: string | null
  age_hours?: number | null
  entry_price?: number | null
  current_price?: number | null
  entry_marketcap?: number | null
  current_marketcap?: number | null
  entry_score?: number | null
  current_return_pct?: number | null
  max_favorable_excursion_pct?: number | null
  max_adverse_excursion_pct?: number | null
  exit_reason?: string | null
  lesson?: string
  pressure_score?: number | null
  quality_score?: number | null
  risk_score?: number | null
  current_updated_at?: string | null
  current_data_freshness?: string | null
}

interface PaperSignalLearningResults {
  status?: string
  headline?: string
  summary?: {
    total?: number
    open?: number
    closed?: number
    win_rate_pct?: number | null
    avg_closed_return_pct?: number | null
    avg_open_return_pct?: number | null
    avg_open_mfe_pct?: number | null
    avg_open_mae_pct?: number | null
  }
  best_open?: PaperSignalPosition | null
  worst_open?: PaperSignalPosition | null
  positions?: PaperSignalPosition[]
}

interface WatchToEntryBlocker {
  key: string
  reason?: string | null
  primary?: boolean
}

interface EstablishedRunnerProfile {
  symbol?: string
  mint?: string
  profile?: string
  is_established_runner?: boolean
  reference_mcap?: number | null
  known_peak_mcap?: number | null
  quality_score?: number | null
  verdict?: string | null
  entry_state?: string | null
  thesis?: string | null
  source?: string | null
}

interface WatchToEntryItem {
  symbol: string
  mint: string
  proof_status?: string
  blocker_key?: string | null
  remaining_blocker_keys?: string[]
  remaining_blockers?: WatchToEntryBlocker[]
  remaining_blocker_count?: number
  true_last_blocker?: boolean
  proof_reason?: string
  score?: number | null
  proof_score?: number | null
  readiness_score?: number | null
  market_quality_score?: number | null
  buy_pressure?: number | null
  vol_acceleration?: number | null
  min_vol_acceleration?: number | null
  top_holder_pct?: number | null
  entry_window?: string | null
  fuel_quality?: string | null
  move_phase?: string | null
  first_leg_confirmed?: number
  updated_at?: string | null
  watched_at?: string | null
  is_established_runner?: boolean
  established_runner_profile?: EstablishedRunnerProfile | null
}

interface WatchToEntryStatus {
  enabled?: boolean
  checked_at?: string
  proof_input_source?: string
  tracking_count?: number
  true_last_blocker_count?: number
  tracked?: WatchToEntryItem[]
  recent_events?: Array<WatchToEntryItem & {
    event?: string
    ts_utc?: string
    from_blocker?: string | null
    from_remaining_blocker_keys?: string[]
  }>
  alert_cooldown_seconds?: number
  detail?: string
}

interface WatchToEntryReplay {
  state?: string
  headline?: string
  tracking_count?: number
  true_last_blocker_count?: number
  multi_blocked_count?: number
  established_runner_count?: number
  top_blockers?: Array<{ key: string; count: number }>
  event_summary?: {
    logged_triggers?: number
    last_trigger_at?: string | null
    last_trigger_symbol?: string | null
  }
  surface_summary?: {
    surfaced?: number
    outcomes_complete?: number
    win_rate_pct?: number | null
    avg_return_24h_pct?: number | null
  }
  next_step?: string
}

interface SystemAuditLite {
  generated_at: string
  runtime?: {
    wallet_stream?: RuntimeStreamStatus
    provider_status?: {
      birdeye?: ProviderStatus
      dexscreener?: ProviderStatus
      dexscreener_spot?: ProviderStatus
    }
    provider_budget?: {
      allocator?: {
        status?: string
        detail?: string
        hot_providers?: string[]
        constrained_providers?: string[]
        providers?: Record<string, {
          used?: number
          remaining?: number
          max_requests?: number
          pressure_pct?: number
          last_lane?: string | null
        }>
      }
    }
    market_data_source?: {
      status?: string
      primary?: string | null
      fallback_active?: boolean
      detail?: string | null
    }
    source_strategy?: {
      independent_mode?: boolean
      detail?: string | null
      primary_market_source?: string | null
      market_status?: string | null
    }
    decision_mode?: {
      mode: string
      headline: string
      confidence: string
      fallback_active: boolean
      market_primary?: string | null
      allowed_actions: string[]
      blocked_actions: string[]
      hard_blocks: string[]
      soft_blocks: string[]
    }
    recovery_checklist?: Array<{
      key: string
      label: string
      state: string
      detail: string
      next_step: string
    }>
    fallbacks?: {
      birdeye_token_stats?: {
        status?: string
        watchdog_status?: string
        data_status?: string
        checked_at?: string | null
        latest_snapshot_utc?: string | null
        snapshot_age_minutes?: number | null
        snapshot_count?: number
        latest_symbols?: string[]
        detail?: string | null
      }
      paper_signal_learning?: {
        status?: string
        checked_at?: string
        opened?: number
        reviewed?: number
        closed?: number
        detail?: string
        summary?: {
          total?: number
          open?: number
          closed?: number
          win_rate_pct?: number | null
          avg_closed_return_pct?: number | null
        }
      }
    }
    memecoin_input?: {
      proof_input_source?: string
      pipeline_status?: string
      pipeline_detail?: string
      using_fallback?: boolean
      scan_cache?: {
        status?: string
        latest_scan_utc?: string | null
        latest_scan_age_minutes?: number | null
        latest_scan_count?: number
        last_nonempty_scan_utc?: string | null
        last_nonempty_age_minutes?: number | null
        last_nonempty_count?: number
        last_nonempty_top_symbol?: string | null
        last_nonempty_top_mint?: string | null
        detail?: string | null
      }
      live_buy_gate?: {
        status?: string
        sample_size?: number
        minimum_sample?: number
        win_rate_pct?: number | null
        pause_threshold_pct?: number
        live_buy_paused?: boolean
        detail?: string | null
      }
      watch_to_entry?: WatchToEntryStatus
      watch_to_entry_replay?: WatchToEntryReplay
    }
  }
  authority_unlock?: {
    status?: string
    headline?: string
    primary_unlock?: string
    action_law_state?: string
    highest_permitted_action?: string
    current_rank?: number
    required_rank?: number
    fresh_capital_policy?: string
    authority_open_for_new_entries?: boolean
    new_entries_open?: boolean
    checks?: Array<{
      key?: string
      label?: string
      passed?: boolean
      detail?: string
    }>
    unmet_checks?: Array<{
      key?: string
      label?: string
      passed?: boolean
      detail?: string
    }>
    top_session_action?: {
      label?: string
      lane?: string
      urgency?: string
      note?: string
    }
  }
  watchdogs?: {
    allocator?: {
      status?: string
      history_status?: string
      history_last_snapshot_utc?: string | null
      history_age_minutes?: number | null
      detail?: string | null
    }
  }
  perp_patterns?: PerpPatterns
  manual_perp_journal?: ManualPerpJournal
  sol_perp_bias?: SolPerpBias
  memecoin_graduation?: {
    state: string
    headline: string
    proof_authority: string
    reinforcement_authority: string
    deployment_authority: string
    proof_ready_now?: number
    slot_state?: string
    used_slots?: number
    recommended_slots?: number
    ladder?: Array<{
      key: string
      label: string
      status: string
      note: string
    }>
    strongest_cohorts?: Array<{
      label: string
      verdict: string
      sample_n: number
      avg_24h?: number | null
    }>
    weakest_cohorts?: Array<{
      label: string
      verdict: string
      sample_n: number
      avg_24h?: number | null
    }>
    top_constraints?: Array<{
      key: string
      count: number
    }>
    next_policy_move?: string
  }
  spot_decision?: {
    action: string
    headline: string
    posture: string
    detail: string
    signal_confidence: string
    holdings_count?: number
    basket_size?: number
    actionable_setups?: number
    poor_conditions?: number
    win_rate_7d?: number | null
    why_not_now?: string[]
    next_moves?: string[]
  }
  paper_signal_learning?: PaperSignalLearningResults
  memecoin_reinforcement?: {
    headline: string
    reinforcement_authority: string
    proof_authority: string
    normal_reinforced?: {
      sample_n: number
      verdict: string
      avg_24h?: number | null
    }
    relaxed_reinforced?: {
      sample_n: number
      verdict: string
      avg_24h?: number | null
    }
    standalone_proof?: {
      sample_n: number
      verdict: string
      avg_24h?: number | null
    }
    next_unlock_hint?: string
  }
  memecoin_rep_depth?: {
    state: string
    headline: string
    proof_trades_total?: number
    proof_trades_closed?: number
    proof_outcomes?: number
    complete_4h?: number
    complete_24h?: number
    memecoin_trades?: number
    exit_reviews?: number
    exit_snapshots?: number
    next_rep_goal?: string
  }
  memecoin_proof_expansion?: {
    planner_state: string
    planner_note: string
    available_slots_now?: number
    recommended_openings?: number
    deployment_authority?: string
    graduation_state?: string
    identity_conflicts?: number
    proof_input_source?: string
    fallback_mode_active?: boolean
    fallback_note?: string
    top_candidates?: Array<{
      symbol: string
      stage: string
      cohort_bucket: string
      slot_eligible_now: boolean
      slot_blockers?: string[]
      proof_score?: number | null
    }>
  }
  spot_rotation?: {
    state: string
    headline: string
    incoming_candidates?: Array<{
      symbol: string
      posture: string
      signal_type: string
      score?: number | null
      gap?: number | null
      already_held?: boolean
      trend?: string | null
    }>
    weakest_holdings?: Array<{
      symbol: string
      posture: string
      pnl_pct?: number | null
      trend?: string | null
      current_pct?: number | null
    }>
  }
  focus_build?: {
    generated_at: string
    memecoin_route?: {
      state: string
      headline: string
      next_unlock_state: string
      next_step: string
      route_bucket: string
      window_bucket: string
      headroom_bucket: string
      proof_ready_now?: number
      proof_input_source?: string
      unlock_progress?: string
      blockers?: string[]
      blocker_details?: Array<{
        key: string
        label: string
        reason: string
      }>
      unmet_checks?: Array<{
        key: string
        label: string
        note: string
      }>
      moves_to_backed?: string[]
      outcomes?: number
      win_rate_pct?: number | null
    }
    spot_adds?: {
      state: string
      headline: string
      next_unlock_state: string
      next_step: string
      signal_confidence: string
      unlock_progress?: string
      holdings_count?: number
      basket_size?: number
      win_rate_7d?: number | null
      source_posture?: string
      unmet_checks?: Array<{
        key: string
        label: string
        note: string
      }>
      moves_to_add_ready?: string[]
      context_note?: string
    }
  }
  posture?: {
    memecoins?: {
      mode?: string
      outcomes?: number
      next_milestone?: number
      wr_pct?: number | null
      fg_value?: number | null
      fg_ok?: boolean
    }
    spot?: {
      mode?: string
      holdings_count?: number
      basket_size?: number
      signal_confidence?: string
      win_rate_7d?: number | null
      outcomes_complete?: number
    }
    proof_stack?: {
      proof_ready_now?: number
      current_blockers?: Array<{ key?: string; count?: number; reason?: string }>
    }
  }
  watch_to_entry?: WatchToEntryStatus
  watch_to_entry_replay?: WatchToEntryReplay
}

function fmtPct(n?: number | null, digits = 1) {
  if (n == null) return '—'
  return `${n >= 0 ? '+' : ''}${n.toFixed(digits)}%`
}

function fmtCount(n?: number | null) {
  if (n == null) return '—'
  return `${n}`
}

function fmtNum(n?: number | null, digits = 1) {
  if (n == null || !Number.isFinite(Number(n))) return '—'
  return Number(n).toFixed(digits)
}

function fmtCompactUsd(n?: number | null) {
  if (n == null) return '—'
  const abs = Math.abs(n)
  if (abs >= 1_000_000_000) return `$${(n / 1_000_000_000).toFixed(2)}B`
  if (abs >= 1_000_000) return `$${(n / 1_000_000).toFixed(1)}M`
  if (abs >= 1_000) return `$${(n / 1_000).toFixed(1)}K`
  return `$${n.toFixed(abs >= 10 ? 0 : 2)}`
}

function ageStr(ts?: string | null) {
  if (!ts) return '—'
  try {
    const d = new Date(ts.includes('T') ? ts : ts + 'Z')
    const diff = Math.max(0, (Date.now() - d.getTime()) / 1000)
    if (diff < 60) return `${Math.floor(diff)}s ago`
    if (diff < 3600) return `${Math.floor(diff / 60)}m ago`
    if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`
    return `${Math.floor(diff / 86400)}d ago`
  } catch {
    return '—'
  }
}

function tsMs(ts?: string | null): number | null {
  if (!ts) return null
  try {
    const d = new Date(ts.includes('T') ? ts : ts + 'Z')
    const ms = d.getTime()
    return Number.isFinite(ms) ? ms : null
  } catch {
    return null
  }
}

function freshnessState(
  timestamps: Array<string | null | undefined>,
  freshMs: number,
  agingMs: number,
): { label: string; kind: 'good' | 'warn' | 'bad' | 'dim'; updatedAt: string | null } {
  const values = timestamps
    .map(tsMs)
    .filter((v): v is number => v != null)
  if (!values.length) return { label: 'waiting on live reads', kind: 'dim', updatedAt: null }
  const latest = Math.max(...values)
  const oldest = Math.min(...values)
  const age = Date.now() - oldest
  if (age <= freshMs) return { label: 'fresh', kind: 'good', updatedAt: new Date(latest).toISOString() }
  if (age <= agingMs) return { label: 'aging', kind: 'warn', updatedAt: new Date(latest).toISOString() }
  return { label: 'stale', kind: 'bad', updatedAt: new Date(latest).toISOString() }
}

function toneColor(kind: 'good' | 'warn' | 'dim' | 'blue' | 'bad') {
  if (kind === 'good') return '#00d48a'
  if (kind === 'warn') return '#f59e0b'
  if (kind === 'blue') return '#60a5fa'
  if (kind === 'bad') return '#ef4444'
  return '#8ca0b3'
}

function surfaceTone(metric?: number | null, positiveGood = true) {
  if (metric == null) return toneColor('dim')
  if (metric === 0) return toneColor('blue')
  return (metric > 0) === positiveGood ? toneColor('good') : toneColor('bad')
}

function shortTokenAddress(value: string | null | undefined): string {
  const addr = String(value || '').trim()
  if (!addr) return ''
  if (addr.length <= 12) return addr
  return `${addr.slice(0, 6)}…${addr.slice(-6)}`
}

function TokenAddressChip({ value }: { value: string | null | undefined }) {
  const addr = String(value || '').trim()
  const [copied, setCopied] = useState(false)
  if (!addr) return null

  const legacyCopy = (text: string): boolean => {
    try {
      const ta = document.createElement('textarea')
      ta.value = text
      ta.setAttribute('readonly', '')
      ta.style.position = 'fixed'
      ta.style.top = '-9999px'
      ta.style.left = '-9999px'
      document.body.appendChild(ta)
      ta.focus()
      ta.select()
      ta.setSelectionRange(0, ta.value.length)
      const ok = document.execCommand('copy')
      document.body.removeChild(ta)
      return ok
    } catch {
      return false
    }
  }

  const copy = async () => {
    try {
      if (navigator?.clipboard?.writeText) {
        await navigator.clipboard.writeText(addr)
      } else if (!legacyCopy(addr)) {
        return
      }
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1200)
    } catch {
      if (legacyCopy(addr)) {
        setCopied(true)
        window.setTimeout(() => setCopied(false), 1200)
      }
    }
  }

  return (
    <button
      type="button"
      onClick={copy}
      title={copied ? 'Copied' : `${addr} · click to copy`}
      style={{
        ...MONO,
        fontSize: 8,
        color: copied ? toneColor('good') : '#8ca0b3',
        background: copied ? 'rgba(0,212,138,0.10)' : 'rgba(255,255,255,0.03)',
        border: `1px solid ${copied ? 'rgba(0,212,138,0.26)' : 'rgba(255,255,255,0.08)'}`,
        borderRadius: 999,
        padding: '3px 7px',
        width: 'fit-content',
        cursor: 'copy',
      }}
    >
      {copied ? 'COPIED' : `CA ${shortTokenAddress(addr)}`}
    </button>
  )
}

function Card({
  title,
  subtitle,
  children,
}: {
  title: string
  subtitle?: string
  children: React.ReactNode
}) {
  return (
    <section style={{
      background: 'linear-gradient(180deg, rgba(15,24,37,0.96), rgba(10,16,27,0.94))',
      border: '1px solid rgba(255,255,255,0.06)',
      borderRadius: 12,
      padding: '14px 16px',
      display: 'flex',
      flexDirection: 'column',
      gap: 12,
    }}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
        <span style={{ ...MONO, fontSize: 9, color: '#c5d3de', letterSpacing: '0.12em' }}>{title}</span>
        {subtitle && (
          <span style={{ ...MONO, fontSize: 9, color: '#7f95a8' }}>{subtitle}</span>
        )}
      </div>
      {children}
    </section>
  )
}

function Metric({
  label,
  value,
  tone,
}: {
  label: string
  value: string
  tone?: string
}) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
      <span style={{ ...MONO, fontSize: 8, color: '#6f8498', letterSpacing: '0.1em' }}>{label}</span>
      <span style={{ ...MONO, fontSize: 12, fontWeight: 700, color: tone || '#d8e2eb' }}>{value}</span>
    </div>
  )
}

function Pill({
  text,
  kind = 'dim',
}: {
  text: string
  kind?: 'good' | 'warn' | 'dim' | 'blue' | 'bad'
}) {
  const color = toneColor(kind)
  return (
    <span style={{
      ...MONO,
      fontSize: 8,
      color,
      background: `${color}12`,
      border: `1px solid ${color}28`,
      borderRadius: 999,
      padding: '4px 8px',
      letterSpacing: '0.06em',
    }}>
      {text}
    </span>
  )
}

function ActionBoardRow({ item, accent }: { item: ActionBoardItem; accent: string }) {
  const blockers = (item.blockers || []).slice(0, 3)
  return (
    <div style={{
      padding: '10px 12px',
      borderRadius: 10,
      background: 'rgba(255,255,255,0.025)',
      border: '1px solid rgba(255,255,255,0.06)',
      borderLeft: `3px solid ${accent}`,
      display: 'flex',
      flexDirection: 'column',
      gap: 7,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <span style={{ ...MONO, fontSize: 12, color: accent, fontWeight: 800 }}>
          {item.symbol || item.action || '—'}
        </span>
        <Pill text={item.system.toLowerCase()} kind={item.system === 'MEMECOINS' ? 'blue' : item.system === 'SPOT' ? 'good' : 'dim'} />
        <Pill text={(item.action || 'watch').toLowerCase()} kind={item.state === 'READY_NOW' ? 'good' : item.state === 'BLOCKED' ? 'warn' : 'dim'} />
        {item.proof_state ? <Pill text={item.proof_state.toLowerCase().replaceAll('_', ' ')} kind={item.proof_state.includes('PROMOT') ? 'good' : item.proof_state.includes('DEMOT') || item.proof_state.includes('LOCK') ? 'bad' : 'warn'} /> : null}
        <span style={{ ...MONO, fontSize: 8, color: '#7f95a8', marginLeft: 'auto' }}>
          score {item.priority_score ?? '—'}
        </span>
      </div>
      <div style={{ ...MONO, fontSize: 10, color: '#d8e2eb', lineHeight: 1.55 }}>
        {item.reason || 'No reason provided yet.'}
      </div>
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
        <TokenAddressChip value={item.token_address} />
        {blockers.map((blocker, i) => (
          <Pill key={`${item.opportunity_key}-blocker-${i}`} text={blocker.toLowerCase().replaceAll('_', ' ')} kind="warn" />
        ))}
        {item.size_guidance ? <Pill text={item.size_guidance.toLowerCase()} kind="dim" /> : null}
      </div>
      {item.unlock_hint ? (
        <div style={{ ...MONO, fontSize: 9, color: '#fbbf24', lineHeight: 1.5 }}>
          unlock: {item.unlock_hint}
        </div>
      ) : null}
    </div>
  )
}

function EntryWatchPanel({
  status,
  replay,
}: {
  status?: WatchToEntryStatus
  replay?: WatchToEntryReplay
}) {
  const tracked = status?.tracked ?? []
  const recent = status?.recent_events ?? []
  const trueLast = status?.true_last_blocker_count ?? replay?.true_last_blocker_count ?? 0
  const panelTone = trueLast > 0 ? toneColor('good') : tracked.length > 0 ? toneColor('warn') : toneColor('dim')

  return (
    <Card
      title="ENTRY WATCH"
      subtitle="high-quality names waiting for the real final blocker to clear"
    >
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, minmax(0, 1fr))', gap: 12 }}>
        <Metric label="TRACKING" value={`${status?.tracking_count ?? replay?.tracking_count ?? 0}`} tone={tracked.length ? toneColor('blue') : toneColor('dim')} />
        <Metric label="TRUE LAST" value={`${trueLast}`} tone={trueLast > 0 ? toneColor('good') : toneColor('dim')} />
        <Metric label="ESTABLISHED" value={`${replay?.established_runner_count ?? tracked.filter(x => x.is_established_runner).length}`} tone={(replay?.established_runner_count ?? 0) > 0 ? toneColor('good') : toneColor('dim')} />
        <Metric label="REPLAY" value={(replay?.state || 'NO_SAMPLE').replaceAll('_', ' ').toLowerCase()} tone={replay?.state === 'REPLAY_READY' ? toneColor('good') : replay?.state === 'ARMED_TRUE_LAST' ? toneColor('good') : toneColor('warn')} />
      </div>

      <div style={{ ...MONO, fontSize: 10, color: '#d8e2eb', lineHeight: 1.55 }}>
        {replay?.headline || status?.detail || 'Entry watch is waiting for high-quality final-blocker setups.'}
      </div>

      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
        <Pill text={status?.enabled === false ? 'disabled' : 'enabled'} kind={status?.enabled === false ? 'bad' : 'good'} />
        <Pill text={status?.proof_input_source ? `proof ${status.proof_input_source.toLowerCase().replaceAll('_', ' ')}` : 'proof source unknown'} kind="blue" />
        <Pill text={`events ${replay?.event_summary?.logged_triggers ?? recent.length}`} kind={(replay?.event_summary?.logged_triggers ?? recent.length) > 0 ? 'good' : 'dim'} />
        <Pill text={`surfaced ${replay?.surface_summary?.surfaced ?? 0}`} kind={(replay?.surface_summary?.surfaced ?? 0) > 0 ? 'blue' : 'dim'} />
        <Pill text={`24h WR ${replay?.surface_summary?.win_rate_pct == null ? '—' : `${replay.surface_summary.win_rate_pct.toFixed(1)}%`}`} kind={(replay?.surface_summary?.outcomes_complete ?? 0) >= 5 ? 'good' : 'dim'} />
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
        {tracked.length ? tracked.slice(0, 5).map((item) => {
          const blockers: WatchToEntryBlocker[] = item.remaining_blockers?.length
            ? item.remaining_blockers
            : (item.remaining_blocker_keys ?? []).map(key => ({ key }))
          const tone = item.true_last_blocker ? toneColor('good') : toneColor('warn')
          const profile = item.established_runner_profile
          return (
            <div key={`${item.mint}-${item.blocker_key || 'watch'}`} style={{
              padding: '10px 12px',
              borderRadius: 10,
              background: 'rgba(255,255,255,0.025)',
              border: '1px solid rgba(255,255,255,0.06)',
              borderLeft: `3px solid ${tone}`,
              display: 'flex',
              flexDirection: 'column',
              gap: 8,
            }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                <span style={{ ...MONO, color: tone, fontWeight: 900, fontSize: 13 }}>
                  {item.symbol || 'UNKNOWN'}
                </span>
                <Pill text={item.true_last_blocker ? 'true last blocker' : `${item.remaining_blocker_count ?? blockers.length} blockers`} kind={item.true_last_blocker ? 'good' : 'warn'} />
                {item.is_established_runner ? <Pill text={(profile?.profile || 'established runner').replaceAll('_', ' ')} kind="blue" /> : null}
                <Pill text={(item.proof_status || 'watch').toLowerCase().replaceAll('_', ' ')} kind={item.proof_status === 'PROOF_READY' ? 'good' : 'dim'} />
                <span style={{ ...MONO, fontSize: 8, color: '#7f95a8', marginLeft: 'auto' }}>
                  {item.updated_at ? `updated ${ageStr(item.updated_at)}` : 'waiting'}
                </span>
              </div>
              <div style={{ display: 'flex', gap: 7, flexWrap: 'wrap', alignItems: 'center' }}>
                <TokenAddressChip value={item.mint} />
                <Pill text={`score ${fmtNum(item.score)}`} kind="dim" />
                <Pill text={`MQ ${fmtNum(item.market_quality_score)}`} kind={(item.market_quality_score ?? 0) >= 80 ? 'good' : 'warn'} />
                <Pill text={`vol ${fmtNum(item.vol_acceleration)} / ${fmtNum(item.min_vol_acceleration)}`} kind={(item.vol_acceleration ?? 0) >= (item.min_vol_acceleration ?? 999) ? 'good' : 'warn'} />
                <Pill text={`bp ${fmtNum(item.buy_pressure)}%`} kind={(item.buy_pressure ?? 0) >= 55 ? 'good' : 'warn'} />
              </div>
              <div style={{ ...MONO, fontSize: 9, color: '#9fb2c3', lineHeight: 1.5 }}>
                next clear: {(item.blocker_key || 'unknown').replaceAll('_', ' ')}
                {item.proof_reason ? ` · ${item.proof_reason}` : ''}
              </div>
              {profile?.thesis ? (
                <div style={{ ...MONO, fontSize: 8, color: '#7f95a8', lineHeight: 1.45 }}>
                  profile: {profile.thesis}
                </div>
              ) : null}
              <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                {blockers.slice(0, 6).map((blocker) => (
                  <Pill
                    key={`${item.mint}-${blocker.key}`}
                    text={`${blocker.primary ? 'primary ' : ''}${String(blocker.key || '').replaceAll('_', ' ')}`}
                    kind={blocker.primary ? 'warn' : 'dim'}
                  />
                ))}
              </div>
            </div>
          )
        }) : (
          <div style={{ ...MONO, fontSize: 10, color: '#8ca0b3', lineHeight: 1.55 }}>
            No high-quality final-blocker watches are active. That means the system is not pretending a messy setup is ready.
          </div>
        )}
      </div>

      {recent.length > 0 ? (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          <span style={{ ...MONO, fontSize: 8, color: '#6f8498', letterSpacing: '0.1em' }}>RECENT TRIGGERS</span>
          {recent.slice(0, 3).map((event, i) => (
            <div key={`watch-event-${event.mint}-${event.ts_utc || i}`} style={{ ...MONO, fontSize: 9, color: '#8ca0b3', lineHeight: 1.45 }}>
              {event.symbol} · {(event.event || 'trigger').replaceAll('_', ' ').toLowerCase()} · cleared {(event.from_blocker || 'watch').replaceAll('_', ' ')} · {event.ts_utc ? ageStr(event.ts_utc) : 'time unknown'}
            </div>
          ))}
        </div>
      ) : null}

      <div style={{ ...MONO, fontSize: 9, color: panelTone, lineHeight: 1.5 }}>
        {replay?.next_step || 'Wait until a setup becomes true-last-blocker before treating a clear as actionable.'}
      </div>
    </Card>
  )
}

export function SystemPage() {
  const analystQ = useQuery<AIAnalystData>({
    queryKey: ['home-ai-analyst'],
    queryFn: () => api.get('/home/ai-analyst').then(r => r.data),
    refetchInterval: slowBudgetedInterval(300_000),
    staleTime: 120_000,
  })

  const outcomeInsightsQ = useQuery<OutcomeInsightsData>({
    queryKey: ['home-memecoin-outcome-insights'],
    queryFn: () => api.get('/home/memecoin-outcome-insights').then(r => r.data),
    refetchInterval: slowBudgetedInterval(300_000),
    staleTime: 120_000,
  })

  const calibrationQ = useQuery<CalibrationView>({
    queryKey: ['memecoin-calibration-view'],
    queryFn: () => api.get('/memecoins/calibration-view?window_days=90&limit=10').then(r => r.data),
    refetchInterval: slowBudgetedInterval(240_000),
    staleTime: 60_000,
  })

  const scoreQ = useQuery<ScoreAnalysis>({
    queryKey: ['memecoin-score-analysis'],
    queryFn: () => api.get('/memecoins/score-analysis').then(r => r.data),
    refetchInterval: slowBudgetedInterval(300_000),
    staleTime: 120_000,
  })

  const heatQ = useQuery<SpeculationHeatData>({
    queryKey: ['home-speculation-heat'],
    queryFn: () => api.get('/home/speculation-heat').then(r => r.data),
    refetchInterval: slowBudgetedInterval(180_000),
    staleTime: 60_000,
  })

  const bestActionQ = useQuery<BestActionData>({
    queryKey: ['home-best-action'],
    queryFn: () => api.get('/home/best-action').then(r => r.data),
    refetchInterval: liveBudgetedInterval(60_000),
    staleTime: 20_000,
  })

  const actionBoardQ = useQuery<ActionBoardData>({
    queryKey: ['home-action-board-system'],
    queryFn: () => api.get('/home/action-board?limit=4').then(r => r.data),
    refetchInterval: liveBudgetedInterval(60_000),
    staleTime: 20_000,
  })

  const systemAuditQ = useQuery<SystemAuditLite>({
    queryKey: ['system-audit-lite'],
    queryFn: () => api.get('/system/audit').then(r => r.data),
    refetchInterval: slowBudgetedInterval(120_000),
    staleTime: 30_000,
  })

  const analyst = analystQ.data
  const outcome = outcomeInsightsQ.data
  const calibration = calibrationQ.data
  const score = scoreQ.data
  const heat = heatQ.data
  const best = bestActionQ.data
  const actionBoard = actionBoardQ.data
  const runtime = systemAuditQ.data?.runtime
  const perpPatterns = systemAuditQ.data?.perp_patterns
  const manualPerpJournal = systemAuditQ.data?.manual_perp_journal
  const solPerpBias = systemAuditQ.data?.sol_perp_bias
  const authorityUnlock = systemAuditQ.data?.authority_unlock
  const focusBuild = systemAuditQ.data?.focus_build
  const memecoinGraduation = systemAuditQ.data?.memecoin_graduation
  const spotDecision = systemAuditQ.data?.spot_decision
  const memecoinReinforcement = systemAuditQ.data?.memecoin_reinforcement
  const memecoinRepDepth = systemAuditQ.data?.memecoin_rep_depth
  const memecoinProofExpansion = systemAuditQ.data?.memecoin_proof_expansion
  const spotRotation = systemAuditQ.data?.spot_rotation
  const paperResults = systemAuditQ.data?.paper_signal_learning
  const posture = systemAuditQ.data?.posture
  const walletRuntime = runtime?.wallet_stream
  const dexStatus = runtime?.provider_status?.dexscreener
  const spotDexStatus = runtime?.provider_status?.dexscreener_spot
  const birdeyeProviderStatus = runtime?.provider_status?.birdeye
  const birdeyeStatus = runtime?.fallbacks?.birdeye_token_stats
  const paperLearning = runtime?.fallbacks?.paper_signal_learning
  const providerBudget = runtime?.provider_budget?.allocator
  const dexBudget = providerBudget?.providers?.dexscreener
  const recoveryChecklist = runtime?.recovery_checklist ?? []
  const decisionMode = runtime?.decision_mode
  const sourceStrategy = runtime?.source_strategy
  const independentMode = !!sourceStrategy?.independent_mode || decisionMode?.mode?.startsWith('INDEPENDENT')
  const memecoinInput = runtime?.memecoin_input
  const scanCache = memecoinInput?.scan_cache
  const liveBuyGate = memecoinInput?.live_buy_gate
  const watchToEntry = systemAuditQ.data?.watch_to_entry || memecoinInput?.watch_to_entry
  const watchToEntryReplay = systemAuditQ.data?.watch_to_entry_replay || memecoinInput?.watch_to_entry_replay
  const allocatorWatchdog = systemAuditQ.data?.watchdogs?.allocator
  const weekendPerps = perpPatterns?.by_phase?.find((row) => row.phase === 'WEEKEND')
  const midweekPerps = perpPatterns?.by_phase?.find((row) => row.phase === 'MIDWEEK')
  const shortPerps = perpPatterns?.by_side?.find((row) => row.side === 'SHORT')
  const longPerps = perpPatterns?.by_side?.find((row) => row.side === 'LONG')
  const manualShorts = manualPerpJournal?.by_action?.find((row) => row.action === 'INCREASE_SHORT')
  const manualLongs = manualPerpJournal?.by_action?.find((row) => row.action === 'INCREASE_LONG')

  const thinOutcome = (outcome?.status || 'THIN') !== 'READY'
  const thinCalibration = ((calibration?.summary?.calibrated_trades ?? 0) < 5)

  const tuningShift = score?.tuner
    ? score.tuner.min_score - score.config_score_min
    : null

  const systemFreshness = freshnessState(
    [
      analyst?.generated_at,
      bestActionQ.dataUpdatedAt ? new Date(bestActionQ.dataUpdatedAt).toISOString() : null,
      actionBoard?.generated_at,
      heat?.generated_at,
      outcomeInsightsQ.dataUpdatedAt ? new Date(outcomeInsightsQ.dataUpdatedAt).toISOString() : null,
      calibrationQ.dataUpdatedAt ? new Date(calibrationQ.dataUpdatedAt).toISOString() : null,
      scoreQ.dataUpdatedAt ? new Date(scoreQ.dataUpdatedAt).toISOString() : null,
      systemAuditQ.dataUpdatedAt ? new Date(systemAuditQ.dataUpdatedAt).toISOString() : null,
    ],
    3 * 60_000,
    12 * 60_000,
  )
  const primaryActions = [
    ...(actionBoard?.ready_now || []),
    ...(actionBoard?.best_blocked || []),
    ...(actionBoard?.watchlist || []),
  ].slice(0, 4)
  const actionHeadline = actionBoard?.headline?.state
    ? actionBoard.headline.state.toLowerCase().replaceAll('_', ' ')
    : 'warming up'
  const paperPositions = paperResults?.positions ?? []
  const bestPaper = paperResults?.best_open
  const worstPaper = paperResults?.worst_open

  return (
    <div style={{
      maxWidth: 1180,
      margin: '0 auto',
      padding: '20px 24px 40px',
      display: 'flex',
      flexDirection: 'column',
      gap: 18,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
        <span style={{ ...MONO, color: '#d7e1ea', fontWeight: 700, fontSize: 12, letterSpacing: '0.16em' }}>
          SYSTEM
        </span>
        <Pill text={thinOutcome || thinCalibration ? 'learning in progress' : 'learning live'} kind={thinOutcome || thinCalibration ? 'warn' : 'good'} />
        <Pill text={systemFreshness.label} kind={systemFreshness.kind} />
        <span style={{ ...MONO, fontSize: 9, color: '#7f95a8' }}>
          memecoins + spot focus · what improved · what still needs sample
        </span>
        <span style={{ ...MONO, fontSize: 8, color: '#7f95a8', marginLeft: 'auto' }}>
          {systemFreshness.updatedAt ? `updated ${ageStr(systemFreshness.updatedAt)}` : 'waiting on live reads'}
        </span>
      </div>

      <Card
        title="ACTION BOARD"
        subtitle="what deserves attention now, with CA copy buttons when a token is involved"
      >
        <div style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(4, minmax(0, 1fr))',
          gap: 12,
        }}>
          <Metric
            label="STATE"
            value={actionHeadline}
            tone={actionBoard?.headline?.state === 'READY_NOW' ? toneColor('good') : actionBoard?.headline?.state === 'BLOCKED_BEST' ? toneColor('warn') : toneColor('blue')}
          />
          <Metric label="READY" value={`${actionBoard?.summary?.ready_now_count ?? 0}`} tone={(actionBoard?.summary?.ready_now_count ?? 0) > 0 ? toneColor('good') : toneColor('dim')} />
          <Metric label="BEST BLOCKED" value={`${actionBoard?.summary?.blocked_count ?? 0}`} tone={(actionBoard?.summary?.blocked_count ?? 0) > 0 ? toneColor('warn') : toneColor('dim')} />
          <Metric label="WATCH" value={`${actionBoard?.summary?.watch_count ?? 0}`} tone={(actionBoard?.summary?.watch_count ?? 0) > 0 ? toneColor('blue') : toneColor('dim')} />
        </div>
        <div style={{ ...MONO, fontSize: 10, color: '#d8e2eb', lineHeight: 1.55 }}>
          {actionBoard?.headline?.note || 'Action board is warming up.'}
        </div>
        <div style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))',
          gap: 12,
        }}>
          {primaryActions.length > 0 ? primaryActions.map((item) => (
            <ActionBoardRow
              key={item.opportunity_key}
              item={item}
              accent={item.state === 'READY_NOW' ? toneColor('good') : item.state === 'BLOCKED' ? toneColor('warn') : toneColor('blue')}
            />
          )) : (
            <div style={{ ...MONO, fontSize: 10, color: '#8ca0b3', lineHeight: 1.55 }}>
              No current candidates. That is useful too: do not force a trade just because the page is open.
            </div>
          )}
        </div>
      </Card>

      <EntryWatchPanel status={watchToEntry} replay={watchToEntryReplay} />

      <Card
        title="WHAT CHANGED"
        subtitle={analyst?.generated_at ? `updated ${ageStr(analyst.generated_at)}` : 'live system memo'}
      >
        <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1.5fr) minmax(280px, 1fr)', gap: 16 }}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
              <Pill text={`${analyst?.source || 'rules'} memo`} kind={analyst?.source === 'ai' ? 'good' : 'blue'} />
              <Pill text={`${best?.winner?.arm || 'none'} now`} kind="dim" />
              {best?.winner?.manual_only ? <Pill text="manual only" kind="warn" /> : null}
            </div>
            <div style={{ ...MONO, fontSize: 12, color: '#60a5fa', fontWeight: 700, lineHeight: 1.55 }}>
              {analyst?.headline || 'System memo is warming up.'}
            </div>
            <div style={{ ...MONO, fontSize: 10, color: '#9fb2c3', lineHeight: 1.6 }}>
              {analyst?.top_lesson || 'We will surface the clearest learning here as the sample builds.'}
            </div>
          </div>

          <div style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(2, minmax(0, 1fr))',
            gap: 12,
          }}>
            <Metric label="BEST ACTION" value={`${best?.winner?.action || 'WAIT'}${best?.winner?.symbol ? ` · ${best.winner.symbol}` : ''}`} />
            <Metric label="HEAT" value={`${heat?.heat_state || '—'}${heat?.heat_score != null ? ` · ${heat.heat_score.toFixed(0)}` : ''}`} tone={surfaceTone((heat?.quality_score ?? 0) - 60)} />
            <Metric label="OUTCOME SAMPLE" value={`${fmtCount(outcome?.attributed_trades)} / ${fmtCount(outcome?.minimum_required)}`} tone={thinOutcome ? toneColor('warn') : toneColor('good')} />
            <Metric label="CALIBRATED CLOSES" value={`${fmtCount(calibration?.summary?.calibrated_trades)} / ${fmtCount(calibration?.summary?.closed_trades)}`} tone={thinCalibration ? toneColor('warn') : toneColor('good')} />
          </div>
        </div>
      </Card>

      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1.2fr) minmax(0, 1fr)', gap: 16 }}>
        <Card title="LIVE RUNTIME" subtitle="wallet lane health and provider posture">
          <div style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(2, minmax(0, 1fr))',
            gap: 12,
          }}>
            <Metric
              label="WALLET LANE"
              value={walletRuntime?.status || '—'}
              tone={walletRuntime?.status === 'ACTIVE' ? toneColor('good') : toneColor('warn')}
            />
            <Metric
              label="DEXSCREENER"
              value={providerBudget?.status || dexStatus?.status || '—'}
              tone={providerBudget?.status === 'CONSTRAINED' ? toneColor('warn') : dexStatus?.status === 'DEGRADED' ? toneColor('warn') : dexStatus?.status === 'ACTIVE' ? toneColor('good') : toneColor('dim')}
            />
            <Metric
              label="WALLET EVENTS"
              value={walletRuntime ? `${walletRuntime.persisted_events ?? 0} persisted` : '—'}
              tone={(walletRuntime?.persisted_events ?? 0) > 0 ? toneColor('good') : toneColor('warn')}
            />
            <Metric
              label="PAPER LOOP"
              value={paperLearning?.summary ? `${paperLearning.summary.open ?? 0} open` : paperLearning?.status || '—'}
              tone={paperLearning?.status === 'ACTIVE' ? toneColor('good') : toneColor('warn')}
            />
          </div>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            <Pill
              text={walletRuntime ? `${walletRuntime.polled_wallets ?? walletRuntime.worker_count ?? 0}/${walletRuntime.tracked_count ?? 0} wallets polled` : 'wallet stream waiting'}
              kind={(walletRuntime?.polled_wallets ?? walletRuntime?.worker_count ?? 0) > 0 ? 'good' : 'warn'}
            />
            <Pill
              text={walletRuntime?.failed_wallets ? `${walletRuntime.failed_wallets} wallet failures` : 'wallet failures clear'}
              kind={walletRuntime?.failed_wallets ? 'warn' : 'good'}
            />
            <Pill
              text={walletRuntime?.quarantined_wallets?.length ? `${walletRuntime.quarantined_wallets.length} wallet quarantined` : 'no wallet quarantine'}
              kind={walletRuntime?.quarantined_wallets?.length ? 'warn' : 'dim'}
            />
            <Pill
              text={providerBudget?.status ? `budget ${providerBudget.status.toLowerCase()}` : 'budget unknown'}
              kind={providerBudget?.status === 'CONSTRAINED' ? 'warn' : providerBudget?.status === 'HEALTHY' ? 'good' : 'blue'}
            />
            <Pill
              text={dexBudget ? `dex ${dexBudget.used ?? 0}/${dexBudget.max_requests ?? '—'} · ${Math.round(dexBudget.pressure_pct ?? 0)}%` : 'dex budget warming'}
              kind={(dexBudget?.pressure_pct ?? 0) >= 80 ? 'warn' : 'good'}
            />
            <Pill
              text={paperLearning?.summary ? `paper ${paperLearning.summary.open ?? 0} open · ${paperLearning.summary.closed ?? 0} closed` : 'paper loop warming'}
              kind={paperLearning?.status === 'ACTIVE' ? 'good' : 'warn'}
            />
            <Pill
              text={
                birdeyeStatus?.status
                  ? `BirdEye ${String(birdeyeStatus.status).toLowerCase()}${birdeyeStatus.snapshot_age_minutes != null ? ` · ${Math.round(birdeyeStatus.snapshot_age_minutes)}m` : ''}`
                  : 'BirdEye status unknown'
              }
              kind={birdeyeStatus?.status === 'ACTIVE' ? 'good' : 'dim'}
            />
            <Pill
              text={decisionMode?.mode ? `mode ${decisionMode.mode.toLowerCase().replaceAll('_', ' ')}` : 'mode unknown'}
              kind={decisionMode?.mode === 'LIVE_DECISION' ? 'good' : decisionMode?.fallback_active ? 'warn' : 'bad'}
            />
            <Pill
              text={memecoinInput?.proof_input_source ? `proof ${memecoinInput.proof_input_source.toLowerCase().replaceAll('_', ' ')}` : 'proof input unknown'}
              kind={memecoinInput?.using_fallback ? 'warn' : 'good'}
            />
            <Pill
              text={
                scanCache?.status
                  ? `scan ${scanCache.status.toLowerCase().replaceAll('_', ' ')}${scanCache.latest_scan_age_minutes != null ? ` · ${Math.round(scanCache.latest_scan_age_minutes)}m` : ''}`
                  : 'scan cache unknown'
              }
              kind={scanCache?.status === 'LIVE_NONEMPTY' ? 'good' : scanCache?.status === 'FRESH_EMPTY' ? 'warn' : 'dim'}
            />
            <Pill
              text={birdeyeProviderStatus?.status ? `birdeye ${birdeyeProviderStatus.status.toLowerCase()}` : 'birdeye unknown'}
              kind={independentMode && birdeyeProviderStatus?.status === 'DISABLED' ? 'dim' : birdeyeProviderStatus?.status === 'ERROR' ? 'bad' : birdeyeProviderStatus?.status === 'ACTIVE' ? 'good' : 'warn'}
            />
            <Pill
              text={spotDexStatus?.status ? `spot dex ${String(spotDexStatus.status).toLowerCase()}` : 'spot dex scoped'}
              kind={spotDexStatus?.status === 'DEGRADED' ? 'warn' : spotDexStatus?.status === 'ACTIVE' ? 'good' : 'dim'}
            />
            <Pill
              text={
                liveBuyGate?.status
                  ? `live buys ${liveBuyGate.status.toLowerCase().replaceAll('_', ' ')}${liveBuyGate.win_rate_pct != null ? ` · ${liveBuyGate.win_rate_pct.toFixed(1)}%` : ''}`
                  : 'live buy gate unknown'
              }
              kind={liveBuyGate?.live_buy_paused ? 'warn' : liveBuyGate?.status === 'OPEN' ? 'good' : 'dim'}
            />
            <Pill
              text={allocatorWatchdog?.history_status ? `allocator ${allocatorWatchdog.history_status.toLowerCase()}` : 'allocator unknown'}
              kind={
                allocatorWatchdog?.history_status === 'STALE'
                  ? 'warn'
                  : allocatorWatchdog?.history_status === 'FRESH'
                    ? 'good'
                    : 'dim'
              }
            />
            {dexStatus?.status === 'DEGRADED' ? (
              <Pill text="fallback mode active" kind="warn" />
            ) : null}
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            <div style={{ ...MONO, fontSize: 10, color: '#8ca0b3', lineHeight: 1.55 }}>
              {decisionMode?.headline || 'Decision mode will show here once source control reports.'}
            </div>
            <div style={{ ...MONO, fontSize: 10, color: '#8ca0b3', lineHeight: 1.55 }}>
              {(walletRuntime?.persisted_events ?? 0) > 0
                ? `The wallet lane is expressing live flow and has already persisted ${walletRuntime?.persisted_events ?? 0} wallet events.`
                : 'The wallet lane is connected, but we are still waiting for live persisted wallet flow.'}
            </div>
            <div style={{ ...MONO, fontSize: 9, color: '#6f8498', lineHeight: 1.5 }}>
              {paperLearning?.detail || 'Paper signal learning will show simulated entries and exits here once it runs.'}
            </div>
            <div style={{ ...MONO, fontSize: 9, color: '#6f8498', lineHeight: 1.5 }}>
              {providerBudget?.detail || 'Provider budget allocator will show request pressure once providers are used.'}
            </div>
            <div style={{ ...MONO, fontSize: 9, color: '#6f8498', lineHeight: 1.5 }}>
              {dexStatus?.status === 'DEGRADED'
                ? `DexScreener is degraded${dexStatus?.reason ? ` (${dexStatus.reason.replaceAll('_', ' ').toLowerCase()})` : ''}. ${independentMode ? 'GeckoTerminal fallback is helping keep the lane usable.' : 'BirdEye fallback is helping keep the lane usable.'}`
                : 'Provider posture looks steady right now.'}
            </div>
            <div style={{ ...MONO, fontSize: 9, color: '#6f8498', lineHeight: 1.5 }}>
              {independentMode
                ? (sourceStrategy?.detail || 'BirdEye is disabled intentionally; independent market sources are active.')
                : birdeyeProviderStatus?.status === 'ERROR'
                ? `BirdEye API is failing (${birdeyeProviderStatus.reason || 'auth/API error'}). Refresh the key/env before trusting token-stat recovery.`
                : birdeyeProviderStatus?.detail || 'BirdEye API provider health will show here once REST calls report.'}
            </div>
            <div style={{ ...MONO, fontSize: 9, color: '#6f8498', lineHeight: 1.5 }}>
              {birdeyeStatus?.detail || 'BirdEye token-stat freshness will show here once the stream reports.'}
            </div>
            {recoveryChecklist.length > 0 ? (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 5, marginTop: 2 }}>
                {recoveryChecklist.slice(0, 6).map((item) => (
                  <div key={item.key} style={{
                    display: 'grid',
                    gridTemplateColumns: '130px minmax(0, 1fr)',
                    gap: 8,
                    padding: '5px 0',
                    borderTop: '1px solid rgba(255,255,255,0.05)',
                  }}>
                    <span style={{ ...MONO, fontSize: 8, color: '#d7e1ea', fontWeight: 800 }}>{item.label}</span>
                    <span style={{ ...MONO, fontSize: 8, color: '#7f95a8', lineHeight: 1.45 }}>
                      {item.state.replaceAll('_', ' ').toLowerCase()} · {item.next_step}
                    </span>
                  </div>
                ))}
              </div>
            ) : null}
            <div style={{ ...MONO, fontSize: 9, color: '#6f8498', lineHeight: 1.5 }}>
              {memecoinInput?.using_fallback
                ? (memecoinInput?.pipeline_detail || `Memecoin proof review is currently reading from ${String(memecoinInput?.proof_input_source || 'fallback').toLowerCase().replaceAll('_', ' ')}.`)
                : 'Memecoin proof review is using fresh live-cache input.'}
            </div>
            <div style={{ ...MONO, fontSize: 9, color: '#6f8498', lineHeight: 1.5 }}>
              {scanCache?.detail || 'Scanner cache truth will show here.'}
            </div>
            {spotDexStatus?.status ? (
              <div style={{ ...MONO, fontSize: 9, color: '#6f8498', lineHeight: 1.5 }}>
                {spotDexStatus.status === 'DEGRADED'
                  ? `Spot DexScreener pricing has its own scoped cooldown${spotDexStatus.reason ? ` (${spotDexStatus.reason.replaceAll('_', ' ').toLowerCase()})` : ''}; it will not block memecoin scanner requests.`
                  : 'Spot DexScreener pricing is using its own scoped provider health.'}
              </div>
            ) : null}
            <div style={{ ...MONO, fontSize: 9, color: '#6f8498', lineHeight: 1.5 }}>
              {liveBuyGate?.detail || 'Memecoin live-buy gate truth will show here.'}
            </div>
            <div style={{ ...MONO, fontSize: 9, color: '#6f8498', lineHeight: 1.5 }}>
              {allocatorWatchdog?.detail || 'Allocator watchdog truth will show here.'}
            </div>
          </div>
        </Card>

        <Card title="ENTRY UNLOCK" subtitle="why new memecoin or spot entries are allowed, blocked, or paused">
          <div style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(2, minmax(0, 1fr))',
            gap: 12,
          }}>
            <Metric
              label="NEW ENTRIES"
              value={authorityUnlock?.status ? authorityUnlock.status.replaceAll('_', ' ') : '—'}
              tone={authorityUnlock?.new_entries_open ? toneColor('good') : toneColor('warn')}
            />
            <Metric
              label="LAW RANK"
              value={`${authorityUnlock?.highest_permitted_action || '—'}${authorityUnlock?.current_rank != null ? ` · ${authorityUnlock.current_rank}/${authorityUnlock.required_rank ?? 3}` : ''}`}
              tone={(authorityUnlock?.current_rank ?? -1) >= (authorityUnlock?.required_rank ?? 3) ? toneColor('good') : toneColor('warn')}
            />
            <Metric
              label="FRESH CAPITAL"
              value={authorityUnlock?.fresh_capital_policy || '—'}
              tone={authorityUnlock?.fresh_capital_policy === 'OPEN' || authorityUnlock?.fresh_capital_policy === 'SELECTIVE' ? toneColor('good') : toneColor('warn')}
            />
            <Metric
              label="UNMET CHECKS"
              value={`${authorityUnlock?.unmet_checks?.length ?? 0}`}
              tone={(authorityUnlock?.unmet_checks?.length ?? 0) === 0 ? toneColor('good') : toneColor('warn')}
            />
          </div>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            <Pill
              text={authorityUnlock?.action_law_state ? `law ${authorityUnlock.action_law_state.toLowerCase()}` : 'law unknown'}
              kind={authorityUnlock?.new_entries_open ? 'good' : 'warn'}
            />
            <Pill
              text={authorityUnlock?.authority_open_for_new_entries ? 'authority open' : 'authority not open'}
              kind={authorityUnlock?.authority_open_for_new_entries ? 'good' : 'warn'}
            />
            {authorityUnlock?.top_session_action?.label ? (
              <Pill
                text={`next ${authorityUnlock.top_session_action.label.toLowerCase()}`}
                kind="blue"
              />
            ) : null}
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            <div style={{ ...MONO, fontSize: 10, color: '#d8e2eb', lineHeight: 1.55 }}>
              {authorityUnlock?.headline || 'Entry unlock summary is warming up.'}
            </div>
            <div style={{ ...MONO, fontSize: 9, color: '#8ca0b3', lineHeight: 1.5 }}>
              {authorityUnlock?.primary_unlock || 'The next unlock condition will show here.'}
            </div>
            {(authorityUnlock?.unmet_checks || []).slice(0, 3).map((item, i) => (
              <div key={`unlock-${item.key || i}`} style={{
                padding: '8px 10px',
                borderRadius: 8,
                background: 'rgba(255,255,255,0.025)',
                border: '1px solid rgba(255,255,255,0.05)',
                display: 'flex',
                flexDirection: 'column',
                gap: 4,
              }}>
                <span style={{ ...MONO, fontSize: 9, color: toneColor('warn'), letterSpacing: '0.05em' }}>
                  {(item.label || item.key || 'unlock check').toUpperCase()}
                </span>
                <span style={{ ...MONO, fontSize: 9, color: '#8ca0b3', lineHeight: 1.5 }}>
                  {item.detail || 'Still needs confirmation.'}
                </span>
              </div>
            ))}
          </div>
        </Card>

        <Card title="PAPER LEARNING RESULTS" subtitle="simulated entries we are using to learn earlier buys and better skips">
          <div style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(4, minmax(0, 1fr))',
            gap: 12,
          }}>
            <Metric
              label="SAMPLE"
              value={`${paperResults?.summary?.open ?? 0} open · ${paperResults?.summary?.closed ?? 0} closed`}
              tone={(paperResults?.summary?.open ?? 0) > 0 ? toneColor('good') : toneColor('dim')}
            />
            <Metric
              label="AVG OPEN"
              value={fmtPct(paperResults?.summary?.avg_open_return_pct)}
              tone={surfaceTone(paperResults?.summary?.avg_open_return_pct)}
            />
            <Metric
              label="BEST OPEN"
              value={bestPaper?.symbol ? `${bestPaper.symbol} ${fmtPct(bestPaper.current_return_pct)}` : '—'}
              tone={surfaceTone(bestPaper?.current_return_pct)}
            />
            <Metric
              label="WORST OPEN"
              value={worstPaper?.symbol ? `${worstPaper.symbol} ${fmtPct(worstPaper.current_return_pct)}` : '—'}
              tone={surfaceTone(worstPaper?.current_return_pct)}
            />
          </div>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            <Pill
              text={paperResults?.status ? paperResults.status.toLowerCase().replaceAll('_', ' ') : 'paper warming'}
              kind={paperResults?.status === 'ACTIONABLE_SAMPLE' ? 'good' : paperResults?.status === 'TRACKING_OPEN' ? 'blue' : 'warn'}
            />
            <Pill
              text={`MFE ${fmtPct(paperResults?.summary?.avg_open_mfe_pct)}`}
              kind={(paperResults?.summary?.avg_open_mfe_pct ?? 0) > 0 ? 'good' : 'dim'}
            />
            <Pill
              text={`MAE ${fmtPct(paperResults?.summary?.avg_open_mae_pct)}`}
              kind={(paperResults?.summary?.avg_open_mae_pct ?? 0) < -8 ? 'warn' : 'dim'}
            />
            <Pill
              text={`closed WR ${paperResults?.summary?.win_rate_pct == null ? '—' : `${paperResults.summary.win_rate_pct.toFixed(1)}%`}`}
              kind={(paperResults?.summary?.closed ?? 0) >= 10 ? 'good' : 'dim'}
            />
          </div>
          <div style={{ ...MONO, fontSize: 10, color: '#d8e2eb', lineHeight: 1.55 }}>
            {paperResults?.headline || 'Paper learning results will appear after simulated entries are opened.'}
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {paperPositions.length > 0 ? paperPositions.slice(0, 6).map((row) => (
              <div key={`paper-${row.id ?? row.mint ?? row.symbol}`} style={{
                padding: '9px 10px',
                borderRadius: 10,
                background: 'rgba(255,255,255,0.025)',
                border: '1px solid rgba(255,255,255,0.06)',
                borderLeft: `3px solid ${surfaceTone(row.current_return_pct)}`,
                display: 'flex',
                flexDirection: 'column',
                gap: 7,
              }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 7, flexWrap: 'wrap' }}>
                  <span style={{ ...MONO, fontSize: 12, color: surfaceTone(row.current_return_pct), fontWeight: 800 }}>
                    {row.symbol || 'UNKNOWN'}
                  </span>
                  <Pill
                    text={(row.status || 'tracking').toLowerCase()}
                    kind={row.status === 'OPEN' ? 'blue' : (row.current_return_pct ?? 0) > 0 ? 'good' : 'bad'}
                  />
                  <TokenAddressChip value={row.mint} />
                  <span style={{ ...MONO, fontSize: 8, color: '#7f95a8', marginLeft: 'auto' }}>
                    {row.last_review_ts_utc ? `reviewed ${ageStr(row.last_review_ts_utc)}` : row.opened_ts_utc ? `opened ${ageStr(row.opened_ts_utc)}` : 'time unknown'}
                  </span>
                </div>
                <div style={{
                  display: 'grid',
                  gridTemplateColumns: 'repeat(4, minmax(0, 1fr))',
                  gap: 10,
                }}>
                  <Metric label="RETURN" value={fmtPct(row.current_return_pct)} tone={surfaceTone(row.current_return_pct)} />
                  <Metric label="MFE / MAE" value={`${fmtPct(row.max_favorable_excursion_pct)} / ${fmtPct(row.max_adverse_excursion_pct)}`} tone={surfaceTone((row.max_favorable_excursion_pct ?? 0) + (row.max_adverse_excursion_pct ?? 0))} />
                  <Metric label="SCORE" value={row.entry_score == null ? '—' : row.entry_score.toFixed(1)} tone={surfaceTone((row.entry_score ?? 0) - 70)} />
                  <Metric label="MCAP" value={`${fmtCompactUsd(row.entry_marketcap)} → ${fmtCompactUsd(row.current_marketcap)}`} tone={surfaceTone((row.current_marketcap ?? 0) - (row.entry_marketcap ?? 0))} />
                </div>
                <div style={{ ...MONO, fontSize: 9, color: '#8ca0b3', lineHeight: 1.5 }}>
                  {row.lesson || 'Lesson is still forming.'}
                </div>
              </div>
            )) : (
              <div style={{ ...MONO, fontSize: 10, color: '#8ca0b3', lineHeight: 1.55 }}>
                No paper entries are visible yet. The engine needs fresh token-intelligence candidates before this panel becomes useful.
              </div>
            )}
          </div>
        </Card>

        <Card title="WHAT IMPROVED" subtitle="the clearest things the system thinks are getting better">
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            {(analyst?.what_improved ?? []).slice(0, 4).map((item, i) => (
              <Pill key={`improved-${i}`} text={item} kind="good" />
            ))}
            {(!(analyst?.what_improved ?? []).length) && <Pill text="still building sample" kind="dim" />}
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {(outcome?.highlights ?? []).slice(0, 3).map((item, i) => (
              <div key={`${item.kind}-${i}`} style={{
                padding: '9px 10px',
                borderRadius: 8,
                background: 'rgba(255,255,255,0.025)',
                border: '1px solid rgba(255,255,255,0.05)',
                display: 'flex',
                flexDirection: 'column',
                gap: 4,
              }}>
                <span style={{ ...MONO, fontSize: 9, color: surfaceTone(item.metric), letterSpacing: '0.05em' }}>{item.title}</span>
                <span style={{ ...MONO, fontSize: 10, color: '#8ca0b3', lineHeight: 1.5 }}>{item.detail}</span>
              </div>
            ))}
            {thinOutcome && (
              <div style={{ ...MONO, fontSize: 10, color: '#8ca0b3', lineHeight: 1.55 }}>
                {outcome?.headline || 'We need a few more attributed closes before outcome lessons are trustworthy.'}
              </div>
            )}
          </div>
        </Card>

        <Card title="BUILD NEXT" subtitle="the two highest-leverage things to improve now in memecoins and spot">
          <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) minmax(0, 1fr)', gap: 12 }}>
            <div style={{
              padding: '10px 12px',
              borderRadius: 8,
              background: 'rgba(255,255,255,0.025)',
              border: '1px solid rgba(255,255,255,0.05)',
              display: 'flex',
              flexDirection: 'column',
              gap: 8,
            }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                <span style={{ ...MONO, fontSize: 9, color: '#60a5fa', letterSpacing: '0.05em' }}>MEMECOIN ROUTE</span>
                <Pill text={(focusBuild?.memecoin_route?.state || '—').toLowerCase()} kind={focusBuild?.memecoin_route?.state === 'NEAR_DEPLOYABLE' ? 'good' : focusBuild?.memecoin_route?.state === 'PROMOTING' ? 'blue' : 'warn'} />
              </div>
              <div style={{ ...MONO, fontSize: 10, color: '#d8e2eb', lineHeight: 1.55 }}>
                {focusBuild?.memecoin_route?.headline || 'Memecoin route summary will show here.'}
              </div>
              <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                {focusBuild?.memecoin_route?.route_bucket ? <Pill text={`route ${focusBuild.memecoin_route.route_bucket.toLowerCase()}`} kind="dim" /> : null}
                {focusBuild?.memecoin_route?.proof_ready_now != null ? <Pill text={`${focusBuild.memecoin_route.proof_ready_now} proof ready`} kind={(focusBuild.memecoin_route.proof_ready_now ?? 0) > 0 ? 'good' : 'warn'} /> : null}
                {focusBuild?.memecoin_route?.unlock_progress ? <Pill text={`checks ${focusBuild.memecoin_route.unlock_progress}`} kind="dim" /> : null}
                {focusBuild?.memecoin_route?.proof_input_source ? (
                  <Pill
                    text={`input ${focusBuild.memecoin_route.proof_input_source.toLowerCase().replaceAll('_', ' ')}`}
                    kind={focusBuild.memecoin_route.proof_input_source === 'LIVE_CACHE' ? 'good' : 'warn'}
                  />
                ) : null}
              </div>
              <div style={{ ...MONO, fontSize: 9, color: '#8ca0b3', lineHeight: 1.5 }}>
                {focusBuild?.memecoin_route?.next_step || 'Memecoin next-step guidance will show here.'}
              </div>
              {((focusBuild?.memecoin_route?.blocker_details?.length ?? 0) > 0) ? (
                <div style={{
                  padding: '8px 10px',
                  borderRadius: 8,
                  background: 'rgba(255,255,255,0.02)',
                  border: '1px solid rgba(255,255,255,0.05)',
                  display: 'flex',
                  flexDirection: 'column',
                  gap: 4,
                }}>
                  <span style={{ ...MONO, fontSize: 9, color: '#fbbf24', letterSpacing: '0.05em' }}>WHAT IS ACTUALLY BLOCKING IT</span>
                  {focusBuild?.memecoin_route?.blocker_details?.slice(0, 2).map((item, i) => (
                    <span key={`meme-blocker-detail-${i}`} style={{ ...MONO, fontSize: 9, color: '#8ca0b3', lineHeight: 1.45 }}>
                      {item.label.toLowerCase()}: {item.reason}
                    </span>
                  ))}
                </div>
              ) : null}
              {((focusBuild?.memecoin_route?.moves_to_backed?.length ?? 0) > 0) ? (
                <div style={{
                  padding: '8px 10px',
                  borderRadius: 8,
                  background: 'rgba(255,255,255,0.02)',
                  border: '1px solid rgba(255,255,255,0.05)',
                  display: 'flex',
                  flexDirection: 'column',
                  gap: 4,
                }}>
                  <span style={{ ...MONO, fontSize: 9, color: '#9dd6b3', letterSpacing: '0.05em' }}>WHAT MOVES IT TO BACKED</span>
                  {focusBuild?.memecoin_route?.moves_to_backed?.slice(0, 3).map((item, i) => (
                    <span key={`meme-move-backed-${i}`} style={{ ...MONO, fontSize: 9, color: '#8ca0b3', lineHeight: 1.45 }}>
                      {item}
                    </span>
                  ))}
                </div>
              ) : null}
            </div>

            <div style={{
              padding: '10px 12px',
              borderRadius: 8,
              background: 'rgba(255,255,255,0.025)',
              border: '1px solid rgba(255,255,255,0.05)',
              display: 'flex',
              flexDirection: 'column',
              gap: 8,
            }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                <span style={{ ...MONO, fontSize: 9, color: '#60a5fa', letterSpacing: '0.05em' }}>SPOT ADDS</span>
                <Pill text={(focusBuild?.spot_adds?.state || '—').toLowerCase()} kind={focusBuild?.spot_adds?.state === 'CLOSE' ? 'good' : focusBuild?.spot_adds?.state === 'WATCHING' ? 'blue' : 'warn'} />
              </div>
              <div style={{ ...MONO, fontSize: 10, color: '#d8e2eb', lineHeight: 1.55 }}>
                {focusBuild?.spot_adds?.headline || 'Spot add summary will show here.'}
              </div>
              <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                {focusBuild?.spot_adds?.signal_confidence ? <Pill text={`confidence ${focusBuild.spot_adds.signal_confidence}`} kind={focusBuild.spot_adds.signal_confidence === 'high' ? 'good' : 'warn'} /> : null}
                {focusBuild?.spot_adds?.unlock_progress ? <Pill text={`checks ${focusBuild.spot_adds.unlock_progress}`} kind="dim" /> : null}
                {focusBuild?.spot_adds?.holdings_count != null ? <Pill text={`basket ${focusBuild.spot_adds.holdings_count}/${focusBuild?.spot_adds?.basket_size ?? '—'}`} kind="dim" /> : null}
              </div>
              <div style={{ ...MONO, fontSize: 9, color: '#8ca0b3', lineHeight: 1.5 }}>
                {focusBuild?.spot_adds?.next_step || 'Spot next-step guidance will show here.'}
              </div>
              {((focusBuild?.spot_adds?.unmet_checks?.length ?? 0) > 0) ? (
                <div style={{
                  padding: '8px 10px',
                  borderRadius: 8,
                  background: 'rgba(255,255,255,0.02)',
                  border: '1px solid rgba(255,255,255,0.05)',
                  display: 'flex',
                  flexDirection: 'column',
                  gap: 4,
                }}>
                  <span style={{ ...MONO, fontSize: 9, color: '#fbbf24', letterSpacing: '0.05em' }}>WHY THERE IS NO ADD YET</span>
                  {focusBuild?.spot_adds?.unmet_checks?.slice(0, 2).map((item, i) => (
                    <span key={`spot-unmet-${i}`} style={{ ...MONO, fontSize: 9, color: '#8ca0b3', lineHeight: 1.45 }}>
                      {item.label.toLowerCase()}: {item.note}
                    </span>
                  ))}
                </div>
              ) : null}
              {((focusBuild?.spot_adds?.moves_to_add_ready?.length ?? 0) > 0) ? (
                <div style={{
                  padding: '8px 10px',
                  borderRadius: 8,
                  background: 'rgba(255,255,255,0.02)',
                  border: '1px solid rgba(255,255,255,0.05)',
                  display: 'flex',
                  flexDirection: 'column',
                  gap: 4,
                }}>
                  <span style={{ ...MONO, fontSize: 9, color: '#9dd6b3', letterSpacing: '0.05em' }}>WHAT WOULD MAKE IT ADD-READY</span>
                  {focusBuild?.spot_adds?.moves_to_add_ready?.slice(0, 3).map((item, i) => (
                    <span key={`spot-move-add-${i}`} style={{ ...MONO, fontSize: 9, color: '#8ca0b3', lineHeight: 1.45 }}>
                      {item}
                    </span>
                  ))}
                </div>
              ) : null}
            </div>
          </div>
        </Card>

        <Card title="MEMECOIN GRADUATION" subtitle="how the route is progressing from relaxed names into something deployable">
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, minmax(0, 1fr))', gap: 12 }}>
            <Metric label="STATE" value={memecoinGraduation?.state || '—'} tone={memecoinGraduation?.state === 'PROMOTIVE' ? toneColor('good') : memecoinGraduation?.state === 'EARNING_TRUST' ? toneColor('warn') : toneColor('dim')} />
            <Metric label="PROOF AUTHORITY" value={memecoinGraduation?.proof_authority || '—'} tone={(memecoinGraduation?.proof_authority || '').includes('BACKED') || (memecoinGraduation?.proof_authority || '').includes('FORCEFUL') ? toneColor('good') : toneColor('warn')} />
            <Metric label="REINFORCEMENT" value={memecoinGraduation?.reinforcement_authority || '—'} tone={(memecoinGraduation?.reinforcement_authority || '') === 'BACKED' || (memecoinGraduation?.reinforcement_authority || '') === 'FORCEFUL' ? toneColor('good') : toneColor('warn')} />
            <Metric label="SLOTS" value={memecoinGraduation?.used_slots != null ? `${memecoinGraduation.used_slots}/${memecoinGraduation?.recommended_slots ?? '—'}` : '—'} />
          </div>
          <div style={{ ...MONO, fontSize: 10, color: '#d8e2eb', lineHeight: 1.55 }}>
            {memecoinGraduation?.headline || 'Graduation tracker will show here.'}
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 10 }}>
            {memecoinGraduation?.ladder?.map((step, i) => (
              <div key={`grad-step-${i}`} style={{
                padding: '8px 10px',
                borderRadius: 8,
                background: 'rgba(255,255,255,0.02)',
                border: '1px solid rgba(255,255,255,0.05)',
                display: 'flex',
                flexDirection: 'column',
                gap: 4,
              }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                  <span style={{ ...MONO, fontSize: 9, color: '#60a5fa', letterSpacing: '0.05em' }}>{step.label.toUpperCase()}</span>
                  <Pill text={step.status.toLowerCase()} kind={step.status === 'DONE' ? 'good' : step.status === 'ACTIVE' ? 'blue' : 'dim'} />
                </div>
                <span style={{ ...MONO, fontSize: 9, color: '#8ca0b3', lineHeight: 1.45 }}>{step.note}</span>
              </div>
            ))}
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) minmax(0, 1fr)', gap: 12 }}>
            <div style={{
              padding: '8px 10px',
              borderRadius: 8,
              background: 'rgba(255,255,255,0.02)',
              border: '1px solid rgba(255,255,255,0.05)',
              display: 'flex',
              flexDirection: 'column',
              gap: 4,
            }}>
              <span style={{ ...MONO, fontSize: 9, color: '#9dd6b3', letterSpacing: '0.05em' }}>STRONGEST COHORTS</span>
              {(memecoinGraduation?.strongest_cohorts ?? []).slice(0, 2).map((item, i) => (
                <span key={`strong-cohort-${i}`} style={{ ...MONO, fontSize: 9, color: '#8ca0b3', lineHeight: 1.45 }}>
                  {item.label.toLowerCase()} · {item.verdict.toLowerCase()} · n={item.sample_n}{item.avg_24h != null ? ` · ${fmtPct(item.avg_24h, 1)} 24h` : ''}
                </span>
              ))}
            </div>
            <div style={{
              padding: '8px 10px',
              borderRadius: 8,
              background: 'rgba(255,255,255,0.02)',
              border: '1px solid rgba(255,255,255,0.05)',
              display: 'flex',
              flexDirection: 'column',
              gap: 4,
            }}>
              <span style={{ ...MONO, fontSize: 9, color: '#fbbf24', letterSpacing: '0.05em' }}>TOP CONSTRAINTS</span>
              {(memecoinGraduation?.top_constraints ?? []).slice(0, 3).map((item, i) => (
                <span key={`grad-constraint-${i}`} style={{ ...MONO, fontSize: 9, color: '#8ca0b3', lineHeight: 1.45 }}>
                  {item.key.replaceAll('_', ' ').toLowerCase()} · {item.count}
                </span>
              ))}
            </div>
          </div>
          <div style={{ ...MONO, fontSize: 9, color: '#8ca0b3', lineHeight: 1.5 }}>
            {memecoinGraduation?.next_policy_move || 'Policy guidance will show here.'}
          </div>
        </Card>

        <Card title="SPOT DECISION" subtitle="whether the next spot move should be add, rotate, hold, or wait">
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, minmax(0, 1fr))', gap: 12 }}>
            <Metric label="ACTION" value={spotDecision?.action || '—'} tone={spotDecision?.action === 'ADD' ? toneColor('good') : spotDecision?.action === 'ROTATE' ? toneColor('warn') : toneColor('dim')} />
            <Metric label="POSTURE" value={spotDecision?.posture || '—'} />
            <Metric label="ACTIONABLE" value={spotDecision?.actionable_setups != null ? `${spotDecision.actionable_setups}` : '—'} tone={(spotDecision?.actionable_setups ?? 0) > 0 ? toneColor('good') : toneColor('warn')} />
            <Metric label="BASKET" value={spotDecision?.holdings_count != null ? `${spotDecision.holdings_count}/${spotDecision?.basket_size ?? '—'}` : '—'} />
          </div>
          <div style={{ ...MONO, fontSize: 10, color: '#d8e2eb', lineHeight: 1.55 }}>
            {spotDecision?.headline || 'Spot decision layer will show here.'}
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) minmax(0, 1fr)', gap: 12 }}>
            <div style={{
              padding: '8px 10px',
              borderRadius: 8,
              background: 'rgba(255,255,255,0.02)',
              border: '1px solid rgba(255,255,255,0.05)',
              display: 'flex',
              flexDirection: 'column',
              gap: 4,
            }}>
              <span style={{ ...MONO, fontSize: 9, color: '#fbbf24', letterSpacing: '0.05em' }}>WHY NOT NOW</span>
              {(spotDecision?.why_not_now ?? []).slice(0, 3).map((item, i) => (
                <span key={`spot-why-not-${i}`} style={{ ...MONO, fontSize: 9, color: '#8ca0b3', lineHeight: 1.45 }}>{item}</span>
              ))}
            </div>
            <div style={{
              padding: '8px 10px',
              borderRadius: 8,
              background: 'rgba(255,255,255,0.02)',
              border: '1px solid rgba(255,255,255,0.05)',
              display: 'flex',
              flexDirection: 'column',
              gap: 4,
            }}>
              <span style={{ ...MONO, fontSize: 9, color: '#9dd6b3', letterSpacing: '0.05em' }}>NEXT MOVES</span>
              {(spotDecision?.next_moves ?? []).slice(0, 3).map((item, i) => (
                <span key={`spot-next-${i}`} style={{ ...MONO, fontSize: 9, color: '#8ca0b3', lineHeight: 1.45 }}>{item}</span>
              ))}
            </div>
          </div>
        </Card>

        <Card title="MEMECOIN REINFORCEMENT" subtitle="whether reinforcement is actually strong enough to lift memecoin trust">
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, minmax(0, 1fr))', gap: 12 }}>
            <Metric label="REINFORCEMENT" value={memecoinReinforcement?.reinforcement_authority || '—'} tone={(memecoinReinforcement?.reinforcement_authority || '') === 'BACKED' || (memecoinReinforcement?.reinforcement_authority || '') === 'FORCEFUL' ? toneColor('good') : toneColor('warn')} />
            <Metric label="PROOF AUTHORITY" value={memecoinReinforcement?.proof_authority || '—'} tone={(memecoinReinforcement?.proof_authority || '').includes('BACKED') ? toneColor('good') : toneColor('warn')} />
            <Metric label="NORMAL + REINFORCED" value={memecoinReinforcement?.normal_reinforced?.sample_n != null ? `${memecoinReinforcement.normal_reinforced.sample_n}` : '—'} />
            <Metric label="RELAXED + REINFORCED" value={memecoinReinforcement?.relaxed_reinforced?.sample_n != null ? `${memecoinReinforcement.relaxed_reinforced.sample_n}` : '—'} />
          </div>
          <div style={{ ...MONO, fontSize: 10, color: '#d8e2eb', lineHeight: 1.55 }}>
            {memecoinReinforcement?.headline || 'Reinforcement memory summary will show here.'}
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, minmax(0, 1fr))', gap: 10 }}>
            {[
              { label: 'Normal + Reinforced', row: memecoinReinforcement?.normal_reinforced },
              { label: 'Relaxed + Reinforced', row: memecoinReinforcement?.relaxed_reinforced },
              { label: 'Standalone Proof', row: memecoinReinforcement?.standalone_proof },
            ].map(({ label, row }, i) => (
              <div key={`reinf-box-${i}`} style={{
                padding: '8px 10px',
                borderRadius: 8,
                background: 'rgba(255,255,255,0.02)',
                border: '1px solid rgba(255,255,255,0.05)',
                display: 'flex',
                flexDirection: 'column',
                gap: 4,
              }}>
                <span style={{ ...MONO, fontSize: 9, color: '#60a5fa', letterSpacing: '0.05em' }}>{label.toUpperCase()}</span>
                <span style={{ ...MONO, fontSize: 9, color: '#8ca0b3', lineHeight: 1.45 }}>
                  verdict {String(row?.verdict || '—').toLowerCase()} · n={row?.sample_n ?? '—'}
                </span>
                <span style={{ ...MONO, fontSize: 9, color: '#8ca0b3', lineHeight: 1.45 }}>
                  {row?.avg_24h != null ? `${fmtPct(row.avg_24h, 1)} 24h` : '24h still thin'}
                </span>
              </div>
            ))}
          </div>
          <div style={{ ...MONO, fontSize: 9, color: '#8ca0b3', lineHeight: 1.5 }}>
            {memecoinReinforcement?.next_unlock_hint || 'Reinforcement next-step guidance will show here.'}
          </div>
        </Card>

        <Card title="MEMECOIN REP DEPTH" subtitle="how much real completed memecoin learning sample we actually have">
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, minmax(0, 1fr))', gap: 12 }}>
            <Metric label="STATE" value={memecoinRepDepth?.state || '—'} tone={memecoinRepDepth?.state === 'MATURE' ? toneColor('good') : memecoinRepDepth?.state === 'BUILDING' ? toneColor('warn') : toneColor('dim')} />
            <Metric label="PROOF OUTCOMES" value={memecoinRepDepth?.proof_outcomes != null ? `${memecoinRepDepth.proof_outcomes}` : '—'} />
            <Metric label="24H COMPLETE" value={memecoinRepDepth?.complete_24h != null ? `${memecoinRepDepth.complete_24h}` : '—'} />
            <Metric label="EXIT REVIEWS" value={memecoinRepDepth?.exit_reviews != null ? `${memecoinRepDepth.exit_reviews}` : '—'} />
          </div>
          <div style={{ ...MONO, fontSize: 10, color: '#d8e2eb', lineHeight: 1.55 }}>
            {memecoinRepDepth?.headline || 'Rep-depth summary will show here.'}
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, minmax(0, 1fr))', gap: 10 }}>
            <div style={{
              padding: '8px 10px',
              borderRadius: 8,
              background: 'rgba(255,255,255,0.02)',
              border: '1px solid rgba(255,255,255,0.05)',
              display: 'flex',
              flexDirection: 'column',
              gap: 4,
            }}>
              <span style={{ ...MONO, fontSize: 9, color: '#60a5fa', letterSpacing: '0.05em' }}>TRADES</span>
              <span style={{ ...MONO, fontSize: 9, color: '#8ca0b3', lineHeight: 1.45 }}>
                total {memecoinRepDepth?.memecoin_trades ?? '—'} · proof closed {memecoinRepDepth?.proof_trades_closed ?? '—'}
              </span>
            </div>
            <div style={{
              padding: '8px 10px',
              borderRadius: 8,
              background: 'rgba(255,255,255,0.02)',
              border: '1px solid rgba(255,255,255,0.05)',
              display: 'flex',
              flexDirection: 'column',
              gap: 4,
            }}>
              <span style={{ ...MONO, fontSize: 9, color: '#60a5fa', letterSpacing: '0.05em' }}>TIMING SAMPLE</span>
              <span style={{ ...MONO, fontSize: 9, color: '#8ca0b3', lineHeight: 1.45 }}>
                4h complete {memecoinRepDepth?.complete_4h ?? '—'} · 24h complete {memecoinRepDepth?.complete_24h ?? '—'}
              </span>
            </div>
            <div style={{
              padding: '8px 10px',
              borderRadius: 8,
              background: 'rgba(255,255,255,0.02)',
              border: '1px solid rgba(255,255,255,0.05)',
              display: 'flex',
              flexDirection: 'column',
              gap: 4,
            }}>
              <span style={{ ...MONO, fontSize: 9, color: '#60a5fa', letterSpacing: '0.05em' }}>EXIT SAMPLE</span>
              <span style={{ ...MONO, fontSize: 9, color: '#8ca0b3', lineHeight: 1.45 }}>
                reviews {memecoinRepDepth?.exit_reviews ?? '—'} · snapshots {memecoinRepDepth?.exit_snapshots ?? '—'}
              </span>
            </div>
          </div>
          <div style={{ ...MONO, fontSize: 9, color: '#8ca0b3', lineHeight: 1.5 }}>
            {memecoinRepDepth?.next_rep_goal || 'The next rep target will show here.'}
          </div>
        </Card>

        <Card title="MEMECOIN PROOF EXPANSION" subtitle="how we would earn the next disciplined proof rep without forcing the lane">
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, minmax(0, 1fr))', gap: 12 }}>
            <Metric label="PLANNER" value={memecoinProofExpansion?.planner_state || '—'} tone={memecoinProofExpansion?.planner_state === 'EXPANDABLE' ? toneColor('good') : memecoinProofExpansion?.planner_state === 'WAITING_ON_QUALITY' ? toneColor('warn') : toneColor('dim')} />
            <Metric label="OPENINGS" value={memecoinProofExpansion?.recommended_openings != null ? `${memecoinProofExpansion.recommended_openings}` : '—'} />
            <Metric label="SLOTS NOW" value={memecoinProofExpansion?.available_slots_now != null ? `${memecoinProofExpansion.available_slots_now}` : '—'} />
            <Metric label="IDENTITY WATCH" value={memecoinProofExpansion?.identity_conflicts != null ? `${memecoinProofExpansion.identity_conflicts}` : '—'} tone={(memecoinProofExpansion?.identity_conflicts ?? 0) > 0 ? toneColor('warn') : toneColor('good')} />
          </div>
          <div style={{ ...MONO, fontSize: 10, color: '#d8e2eb', lineHeight: 1.55 }}>
            {memecoinProofExpansion?.planner_note || 'Proof expansion guidance will show here.'}
          </div>
          {memecoinProofExpansion?.fallback_note ? (
            <div style={{ ...MONO, fontSize: 9, color: '#8ca0b3', lineHeight: 1.5 }}>
              {memecoinProofExpansion.fallback_note}
            </div>
          ) : null}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, minmax(0, 1fr))', gap: 10 }}>
            {(memecoinProofExpansion?.top_candidates ?? []).slice(0, 3).map((item, i) => (
              <div key={`proof-expand-${i}`} style={{
                padding: '8px 10px',
                borderRadius: 8,
                background: 'rgba(255,255,255,0.02)',
                border: '1px solid rgba(255,255,255,0.05)',
                display: 'flex',
                flexDirection: 'column',
                gap: 4,
              }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                  <span style={{ ...MONO, fontSize: 9, color: '#60a5fa', letterSpacing: '0.05em' }}>{item.symbol || 'candidate'}</span>
                  <Pill text={item.slot_eligible_now ? 'ready now' : 'not ready'} kind={item.slot_eligible_now ? 'good' : 'warn'} />
                </div>
                <span style={{ ...MONO, fontSize: 9, color: '#8ca0b3', lineHeight: 1.45 }}>
                  {item.cohort_bucket.toLowerCase()} · {item.stage.toLowerCase()} {item.proof_score != null ? `· proof ${item.proof_score}` : ''}
                </span>
                {item.slot_blockers?.length ? (
                  <span style={{ ...MONO, fontSize: 9, color: '#8ca0b3', lineHeight: 1.45 }}>
                    blockers: {item.slot_blockers.slice(0, 2).join(', ')}
                  </span>
                ) : null}
              </div>
            ))}
          </div>
        </Card>

        <Card title="SPOT ROTATION" subtitle="what could come in next, and what currently looks weakest in the basket">
          <div style={{ ...MONO, fontSize: 10, color: '#d8e2eb', lineHeight: 1.55 }}>
            {spotRotation?.headline || 'Spot rotation guidance will show here.'}
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) minmax(0, 1fr)', gap: 12 }}>
            <div style={{
              padding: '8px 10px',
              borderRadius: 8,
              background: 'rgba(255,255,255,0.02)',
              border: '1px solid rgba(255,255,255,0.05)',
              display: 'flex',
              flexDirection: 'column',
              gap: 4,
            }}>
              <span style={{ ...MONO, fontSize: 9, color: '#9dd6b3', letterSpacing: '0.05em' }}>ROTATE IN</span>
              {(spotRotation?.incoming_candidates ?? []).slice(0, 3).map((item, i) => (
                <span key={`rotate-in-${i}`} style={{ ...MONO, fontSize: 9, color: '#8ca0b3', lineHeight: 1.45 }}>
                  {item.symbol} · {item.posture.toLowerCase()} · {item.signal_type.toLowerCase()}{item.already_held ? ' · already held' : ''}
                </span>
              ))}
              {!(spotRotation?.incoming_candidates ?? []).length && (
                <span style={{ ...MONO, fontSize: 9, color: '#8ca0b3', lineHeight: 1.45 }}>
                  no clear rotate-in candidate yet
                </span>
              )}
            </div>
            <div style={{
              padding: '8px 10px',
              borderRadius: 8,
              background: 'rgba(255,255,255,0.02)',
              border: '1px solid rgba(255,255,255,0.05)',
              display: 'flex',
              flexDirection: 'column',
              gap: 4,
            }}>
              <span style={{ ...MONO, fontSize: 9, color: '#fbbf24', letterSpacing: '0.05em' }}>ROTATE OUT</span>
              {(spotRotation?.weakest_holdings ?? []).slice(0, 3).map((item, i) => (
                <span key={`rotate-out-${i}`} style={{ ...MONO, fontSize: 9, color: '#8ca0b3', lineHeight: 1.45 }}>
                  {item.symbol} · {item.posture.toLowerCase()} · {item.trend?.toLowerCase() || 'trend n/a'}{item.pnl_pct != null ? ` · ${fmtPct(item.pnl_pct, 1)}` : ''}
                </span>
              ))}
              {!(spotRotation?.weakest_holdings ?? []).length && (
                <span style={{ ...MONO, fontSize: 9, color: '#8ca0b3', lineHeight: 1.45 }}>
                  no weak basket names surfaced yet
                </span>
              )}
            </div>
          </div>
        </Card>

        <Card title="MEMECOIN FOCUS" subtitle="current memecoin learning and what is still blocking clean deployment">
          <div style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(2, minmax(0, 1fr))',
            gap: 12,
          }}>
            <Metric label="MODE" value={posture?.memecoins?.mode || '—'} />
            <Metric
              label="WIN RATE"
              value={posture?.memecoins?.wr_pct != null ? `${posture.memecoins.wr_pct.toFixed(1)}%` : '—'}
              tone={surfaceTone((posture?.memecoins?.wr_pct ?? 0) - 50)}
            />
            <Metric
              label="OUTCOMES"
              value={posture?.memecoins?.outcomes != null ? `${posture.memecoins.outcomes}` : '—'}
            />
            <Metric
              label="PROOF READY NOW"
              value={posture?.proof_stack?.proof_ready_now != null ? `${posture.proof_stack.proof_ready_now}` : '—'}
              tone={(posture?.proof_stack?.proof_ready_now ?? 0) > 0 ? toneColor('good') : toneColor('warn')}
            />
          </div>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            {posture?.memecoins?.fg_value != null ? (
              <Pill
                text={`fear & greed ${posture.memecoins.fg_value}`}
                kind={posture?.memecoins?.fg_ok ? 'good' : 'warn'}
              />
            ) : null}
            {((posture?.proof_stack?.current_blockers) ?? []).slice(0, 2).map((item, i) => (
              <Pill
                key={`meme-blocker-${i}`}
                text={`${item.key || 'blocker'}${item.count ? ` · ${item.count}` : ''}`}
                kind="warn"
              />
            ))}
          </div>
          <div style={{ ...MONO, fontSize: 10, color: '#8ca0b3', lineHeight: 1.55 }}>
            {((posture?.proof_stack?.proof_ready_now ?? 0) > 0)
              ? 'There is at least one proof-ready memecoin candidate now, so the main question is whether it still has enough room and route support.'
              : 'Memecoin discovery is alive, but deployment is still mostly being held back by route and proof authority rather than lack of names.'}
          </div>
        </Card>

        <Card title="SPOT FOCUS" subtitle="current spot quality and whether the lane is giving us anything worth adding">
          <div style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(2, minmax(0, 1fr))',
            gap: 12,
          }}>
            <Metric label="MODE" value={posture?.spot?.mode || '—'} />
            <Metric
              label="SIGNAL CONFIDENCE"
              value={posture?.spot?.signal_confidence || '—'}
              tone={(posture?.spot?.signal_confidence || '').toLowerCase() === 'high' ? toneColor('good') : toneColor('warn')}
            />
            <Metric
              label="7D WIN RATE"
              value={posture?.spot?.win_rate_7d != null ? `${posture.spot.win_rate_7d.toFixed(1)}%` : '—'}
              tone={surfaceTone((posture?.spot?.win_rate_7d ?? 0) - 50)}
            />
            <Metric
              label="HOLDINGS"
              value={posture?.spot?.holdings_count != null ? `${posture.spot.holdings_count}/${posture?.spot?.basket_size ?? '—'}` : '—'}
            />
          </div>
          <div style={{ ...MONO, fontSize: 10, color: '#8ca0b3', lineHeight: 1.55 }}>
            {(posture?.spot?.signal_confidence || '').toLowerCase() === 'high'
              ? 'Spot quality is cleaner now, so the question is not whether spot belongs in the mix, but whether we have any add-ready setup worth acting on.'
              : 'Spot is still more of a supporting lane right now. We should keep watching confidence and not force adds just because the basket exists.'}
          </div>
        </Card>

        <Card title="WHAT WE ARE TUNING" subtitle="current threshold pressure and where the system wants to move">
          <div style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(2, minmax(0, 1fr))',
            gap: 12,
          }}>
            <Metric label="CURRENT MIN SCORE" value={`${score?.config_score_min ?? '—'}`} />
            <Metric label="TUNER MIN SCORE" value={`${score?.tuner?.min_score ?? '—'}`} tone={surfaceTone(-(tuningShift ?? 0))} />
            <Metric label="TUNER WINDOW" value={score?.tuner ? `${score.tuner.min_score}-${score.tuner.max_score}` : '—'} />
            <Metric label="TUNER CONFIDENCE" value={score?.tuner?.confidence ?? '—'} tone={score?.tuner?.confidence === 'high' ? toneColor('good') : score?.tuner?.confidence === 'medium' ? toneColor('warn') : toneColor('dim')} />
          </div>
          <div style={{
            padding: '10px 12px',
            borderRadius: 8,
            background: 'rgba(255,255,255,0.025)',
            border: '1px solid rgba(255,255,255,0.05)',
            display: 'flex',
            flexDirection: 'column',
            gap: 5,
          }}>
            <span style={{ ...MONO, fontSize: 9, color: score?.verdict?.label === 'CALIBRATED' ? toneColor('good') : toneColor('warn'), letterSpacing: '0.05em' }}>
              {score?.verdict?.label || 'TUNER STATUS'}
            </span>
            <span style={{ ...MONO, fontSize: 10, color: '#8ca0b3', lineHeight: 1.55 }}>
              {score?.verdict?.message || 'Threshold tuning status will show up here.'}
            </span>
            {score?.horizon_comparison?.verdict?.message ? (
              <span style={{ ...MONO, fontSize: 9, color: '#6f8498', lineHeight: 1.45 }}>
                Horizon check: {score.horizon_comparison.verdict.message}
              </span>
            ) : null}
          </div>
        </Card>
      </div>

      <Card title="PERP REFERENCE" subtitle="background history only, kept here for context while we focus on memecoins and spot">
        <div style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(4, minmax(0, 1fr))',
          gap: 12,
        }}>
          <Metric
            label="PERP MODE"
            value={perpPatterns?.mode?.dry_run ? 'PAPER' : 'LIVE'}
            tone={perpPatterns?.mode?.dry_run ? toneColor('good') : toneColor('warn')}
          />
          <Metric
            label="CLOSED TRADES"
            value={fmtCount(perpPatterns?.summary?.closed_trades)}
          />
          <Metric
            label="AVG RETURN"
            value={perpPatterns?.summary?.avg_return_pct != null ? fmtPct(perpPatterns.summary.avg_return_pct, 2) : '—'}
            tone={surfaceTone(perpPatterns?.summary?.avg_return_pct)}
          />
          <Metric
            label="AVG PNL"
            value={perpPatterns?.summary?.avg_pnl_usd != null ? `$${perpPatterns.summary.avg_pnl_usd.toFixed(2)}` : '—'}
            tone={surfaceTone(perpPatterns?.summary?.avg_pnl_usd)}
          />
        </div>
        <div style={{ ...MONO, fontSize: 10, color: '#8ca0b3', lineHeight: 1.55 }}>
          {perpPatterns?.headline || 'Perp pattern learning will show here as the history becomes more legible.'}
        </div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          {weekendPerps ? (
            <Pill
              text={`weekend ${fmtPct(weekendPerps.avg_return_pct, 2)} · ${weekendPerps.count} trades`}
              kind={(weekendPerps.total_pnl_usd ?? 0) >= 0 ? 'good' : 'warn'}
            />
          ) : null}
          {midweekPerps ? (
            <Pill
              text={`midweek ${fmtPct(midweekPerps.avg_return_pct, 2)} · ${midweekPerps.count} trades`}
              kind={(midweekPerps.total_pnl_usd ?? 0) >= 0 ? 'good' : 'warn'}
            />
          ) : null}
          {shortPerps ? (
            <Pill
              text={`shorts ${fmtPct(shortPerps.avg_return_pct, 2)} · ${shortPerps.count}`}
              kind={(shortPerps.total_pnl_usd ?? 0) >= 0 ? 'good' : 'warn'}
            />
          ) : null}
          {longPerps ? (
            <Pill
              text={`longs ${fmtPct(longPerps.avg_return_pct, 2)} · ${longPerps.count}`}
              kind={(longPerps.total_pnl_usd ?? 0) >= 0 ? 'good' : 'warn'}
            />
          ) : null}
        </div>
      </Card>

      <Card title="MANUAL SOL REFERENCE" subtitle="recent screenshot-derived SOL scalps, kept as background context rather than a primary lane">
        <div style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(4, minmax(0, 1fr))',
          gap: 12,
        }}>
          <Metric
            label="MANUAL EVENTS"
            value={fmtCount(manualPerpJournal?.summary?.total_events)}
          />
          <Metric
            label="REALIZED EXITS"
            value={fmtCount(manualPerpJournal?.summary?.realized_events)}
          />
          <Metric
            label="REALIZED PNL"
            value={manualPerpJournal?.summary?.total_realized_pnl_usd != null ? `$${manualPerpJournal.summary.total_realized_pnl_usd.toFixed(2)}` : '—'}
            tone={surfaceTone(manualPerpJournal?.summary?.total_realized_pnl_usd)}
          />
          <Metric
            label="AVG EXIT PNL"
            value={manualPerpJournal?.summary?.avg_realized_pnl_usd != null ? `$${manualPerpJournal.summary.avg_realized_pnl_usd.toFixed(2)}` : '—'}
            tone={surfaceTone(manualPerpJournal?.summary?.avg_realized_pnl_usd)}
          />
        </div>
        <div style={{ ...MONO, fontSize: 10, color: '#8ca0b3', lineHeight: 1.55 }}>
          {manualPerpJournal?.headline || 'Manual SOL scalp entries will show here once we start logging them.'}
        </div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          {manualShorts ? (
            <Pill
              text={`manual shorts ${manualShorts.count}`}
              kind={(manualShorts.total_realized_pnl_usd ?? 0) >= 0 ? 'good' : 'warn'}
            />
          ) : null}
          {manualLongs ? (
            <Pill
              text={`manual longs ${manualLongs.count}`}
              kind={(manualLongs.total_realized_pnl_usd ?? 0) >= 0 ? 'good' : 'warn'}
            />
          ) : null}
          {manualPerpJournal?.summary?.last_event_ts ? (
            <Pill text={`last ${ageStr(manualPerpJournal.summary.last_event_ts)}`} kind="dim" />
          ) : null}
        </div>
        {(manualPerpJournal?.recent ?? []).length > 0 ? (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {(manualPerpJournal?.recent ?? []).slice(0, 6).map((event) => (
              <div key={event.id} style={{
                display: 'grid',
                gridTemplateColumns: '120px 1fr auto',
                gap: 12,
                alignItems: 'center',
                padding: '9px 10px',
                borderRadius: 8,
                background: 'rgba(255,255,255,0.025)',
                border: '1px solid rgba(255,255,255,0.05)',
              }}>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
                  <span style={{ ...MONO, fontSize: 10, color: '#d8e2eb', fontWeight: 700 }}>
                    {event.action.replaceAll('_', ' ').toLowerCase()}
                  </span>
                  <span style={{ ...MONO, fontSize: 8, color: '#6f8498' }}>
                    {ageStr(event.ts)}
                  </span>
                </div>
                <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                  {[
                    event.price != null ? `$${event.price.toFixed(2)}` : null,
                    event.size_usd != null ? `size $${event.size_usd.toFixed(0)}` : null,
                    event.deposit_withdraw_usd != null ? `flow ${event.deposit_withdraw_usd >= 0 ? '+' : ''}$${event.deposit_withdraw_usd.toFixed(2)}` : null,
                    event.fee_usd != null ? `fee $${event.fee_usd.toFixed(2)}` : null,
                    event.realized_pnl_usd != null ? `pnl ${event.realized_pnl_usd >= 0 ? '+' : ''}$${event.realized_pnl_usd.toFixed(2)}` : null,
                  ].filter(Boolean).map((chip) => (
                    <Pill key={chip} text={chip as string} kind="dim" />
                  ))}
                </div>
                <span style={{ ...MONO, fontSize: 8, color: '#6f8498', textAlign: 'right' }}>
                  {(event.notes || event.source || 'manual').toLowerCase()}
                </span>
              </div>
            ))}
          </div>
        ) : (
          <div style={{ ...MONO, fontSize: 10, color: '#8ca0b3', lineHeight: 1.55 }}>
            No manual SOL journal entries yet. Once we log them, this section will keep the pattern visible without making the page noisy.
          </div>
        )}
      </Card>

      <Card title="SOL PERP REFERENCE" subtitle="combined weekday-phase read, kept in the background while the main build focus is memecoins and spot">
        <div style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(4, minmax(0, 1fr))',
          gap: 12,
        }}>
          <Metric
            label="CURRENT PHASE"
            value={solPerpBias ? `${solPerpBias.current_phase.replaceAll('_', ' ')} · ${solPerpBias.current_weekday}` : '—'}
          />
          <Metric
            label="COMBINED BIAS"
            value={solPerpBias?.combined_bias || '—'}
            tone={solPerpBias?.combined_bias === 'LONG' ? toneColor('good') : solPerpBias?.combined_bias === 'SHORT' ? toneColor('warn') : toneColor('dim')}
          />
          <Metric
            label="ALIGNMENT"
            value={solPerpBias?.alignment || '—'}
            tone={solPerpBias?.alignment === 'ALIGNED' ? toneColor('good') : solPerpBias?.alignment === 'MIXED' ? toneColor('warn') : toneColor('blue')}
          />
          <Metric
            label="SYSTEM BIAS"
            value={solPerpBias?.system_bias || '—'}
            tone={solPerpBias?.system_bias === 'LONG' ? toneColor('good') : solPerpBias?.system_bias === 'SHORT' ? toneColor('warn') : toneColor('dim')}
          />
        </div>
        <div style={{ ...MONO, fontSize: 10, color: '#8ca0b3', lineHeight: 1.55 }}>
          {solPerpBias?.headline || 'SOL perp bias will show here as system and manual evidence accumulate.'}
        </div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          {solPerpBias?.historical_bias ? (
            <Pill text={`history ${solPerpBias.historical_bias.toLowerCase()}`} kind={solPerpBias.historical_bias === 'LONG' ? 'good' : solPerpBias.historical_bias === 'SHORT' ? 'warn' : 'dim'} />
          ) : null}
          {solPerpBias?.manual_bias ? (
            <Pill text={`manual ${solPerpBias.manual_bias.toLowerCase()}`} kind={solPerpBias.manual_bias === 'LONG' ? 'good' : solPerpBias.manual_bias === 'SHORT' ? 'warn' : 'dim'} />
          ) : null}
          {solPerpBias?.phase_stats?.count != null ? (
            <Pill text={`${solPerpBias.phase_stats.count} system phase trades`} kind="dim" />
          ) : null}
          {solPerpBias?.manual_stats?.realized_events != null ? (
            <Pill text={`${solPerpBias.manual_stats.realized_events} manual realized exits`} kind="dim" />
          ) : null}
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) minmax(0, 1fr)', gap: 12 }}>
          <div style={{
            padding: '9px 10px',
            borderRadius: 8,
            background: 'rgba(255,255,255,0.025)',
            border: '1px solid rgba(255,255,255,0.05)',
            display: 'flex',
            flexDirection: 'column',
            gap: 4,
          }}>
            <span style={{ ...MONO, fontSize: 9, color: '#60a5fa', letterSpacing: '0.05em' }}>SYSTEM HISTORY</span>
            <span style={{ ...MONO, fontSize: 10, color: '#8ca0b3', lineHeight: 1.5 }}>
              {solPerpBias?.historical_note || 'System phase note will show here.'}
            </span>
          </div>
          <div style={{
            padding: '9px 10px',
            borderRadius: 8,
            background: 'rgba(255,255,255,0.025)',
            border: '1px solid rgba(255,255,255,0.05)',
            display: 'flex',
            flexDirection: 'column',
            gap: 4,
          }}>
            <span style={{ ...MONO, fontSize: 9, color: '#60a5fa', letterSpacing: '0.05em' }}>MANUAL SAMPLE</span>
            <span style={{ ...MONO, fontSize: 10, color: '#8ca0b3', lineHeight: 1.5 }}>
              {solPerpBias?.manual_note || 'Manual journal note will show here.'}
            </span>
          </div>
        </div>
      </Card>

      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1.25fr) minmax(0, 1fr)', gap: 16 }}>
        <Card title="CALIBRATION" subtitle="are we arriving too late and capturing enough of the move?">
          <div style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(130px, 1fr))',
            gap: 12,
          }}>
            <Metric label="AVG LATENCY" value={calibration?.overview?.avg_latency_minutes != null ? `${calibration.overview.avg_latency_minutes.toFixed(0)}m` : '—'} tone={surfaceTone(-(calibration?.overview?.avg_latency_minutes ?? 0))} />
            <Metric label="MOVE COST" value={calibration?.overview?.avg_move_cost_pct != null ? fmtPct(calibration.overview.avg_move_cost_pct) : '—'} tone={surfaceTone(-(calibration?.overview?.avg_move_cost_pct ?? 0))} />
            <Metric label="CAPTURE RATIO" value={calibration?.overview?.avg_capture_ratio_pct != null ? `${calibration.overview.avg_capture_ratio_pct.toFixed(0)}%` : '—'} tone={surfaceTone((calibration?.overview?.avg_capture_ratio_pct ?? 0) - 50)} />
            <Metric label="AVG MFE / MAE" value={
              calibration?.overview?.avg_mfe_pct != null || calibration?.overview?.avg_mae_pct != null
                ? `${calibration?.overview?.avg_mfe_pct != null ? fmtPct(calibration.overview.avg_mfe_pct) : '—'} / ${calibration?.overview?.avg_mae_pct != null ? fmtPct(calibration.overview.avg_mae_pct) : '—'}`
                : '—'
            } />
          </div>
          {thinCalibration ? (
            <div style={{ ...MONO, fontSize: 10, color: '#8ca0b3', lineHeight: 1.55 }}>
              We are still building calibration sample. Once a few more trades close with full timing and excursion data, this area will show the real timing leak clearly.
            </div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {(calibration?.insights ?? []).slice(0, 3).map((item, i) => (
                <div key={`${item.kind}-${i}`} style={{
                  padding: '9px 10px',
                  borderRadius: 8,
                  background: 'rgba(255,255,255,0.025)',
                  border: '1px solid rgba(255,255,255,0.05)',
                  display: 'flex',
                  flexDirection: 'column',
                  gap: 4,
                }}>
                  <span style={{ ...MONO, fontSize: 9, color: surfaceTone(item.metric), letterSpacing: '0.05em' }}>{item.title}</span>
                  <span style={{ ...MONO, fontSize: 10, color: '#8ca0b3', lineHeight: 1.5 }}>{item.detail}</span>
                </div>
              ))}
            </div>
          )}
        </Card>

        <Card title="WHAT STILL NEEDS SAMPLE" subtitle="where we should be patient instead of over-tuning">
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            <div style={{
              padding: '9px 10px',
              borderRadius: 8,
              background: 'rgba(255,255,255,0.025)',
              border: '1px solid rgba(255,255,255,0.05)',
              display: 'flex',
              flexDirection: 'column',
              gap: 4,
            }}>
              <span style={{ ...MONO, fontSize: 9, color: thinOutcome ? toneColor('warn') : toneColor('good'), letterSpacing: '0.05em' }}>
                outcome learning
              </span>
              <span style={{ ...MONO, fontSize: 10, color: '#8ca0b3', lineHeight: 1.5 }}>
                {outcome?.headline || 'Outcome learning status will show here.'}
              </span>
            </div>
            <div style={{
              padding: '9px 10px',
              borderRadius: 8,
              background: 'rgba(255,255,255,0.025)',
              border: '1px solid rgba(255,255,255,0.05)',
              display: 'flex',
              flexDirection: 'column',
              gap: 4,
            }}>
              <span style={{ ...MONO, fontSize: 9, color: thinCalibration ? toneColor('warn') : toneColor('good'), letterSpacing: '0.05em' }}>
                timing + exit calibration
              </span>
              <span style={{ ...MONO, fontSize: 10, color: '#8ca0b3', lineHeight: 1.5 }}>
                {thinCalibration
                  ? 'We do not have enough calibrated closes yet to trust timing and capture conclusions.'
                  : 'We have enough calibrated closes to start reading timing and capture lessons.'}
              </span>
            </div>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              <Pill text={`${fmtCount(outcome?.attributed_trades)} attributed closes`} kind={thinOutcome ? 'warn' : 'good'} />
              <Pill text={`${fmtCount(calibration?.summary?.calibrated_trades)} calibrated closes`} kind={thinCalibration ? 'warn' : 'good'} />
              <Pill text={`${score?.tuner?.sample_size ?? '—'} tuner sample`} kind="blue" />
            </div>
          </div>
        </Card>
      </div>

      <Card title="NEXT CONVERSATION" subtitle="the questions the system is teeing up for us next">
        <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) minmax(0, 1fr)', gap: 16 }}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            <span style={{ ...MONO, fontSize: 8, color: '#f59e0b', letterSpacing: '0.1em' }}>REVIEW NOW</span>
            {(analyst?.review_now ?? []).length > 0 ? (
              (analyst?.review_now ?? []).slice(0, 4).map((item, i) => (
                <div key={`review-${i}`} style={{ ...MONO, fontSize: 10, color: '#d8e2eb', lineHeight: 1.55 }}>
                  {item}
                </div>
              ))
            ) : (
              <div style={{ ...MONO, fontSize: 10, color: '#8ca0b3', lineHeight: 1.55 }}>
                Nothing urgent is being pushed yet. That usually means the right move is to let the new sample build.
              </div>
            )}
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            <span style={{ ...MONO, fontSize: 8, color: '#6f8498', letterSpacing: '0.1em' }}>DO NOT CHANGE YET</span>
            {(analyst?.do_not_change_yet ?? []).length > 0 ? (
              (analyst?.do_not_change_yet ?? []).slice(0, 4).map((item, i) => (
                <div key={`hold-${i}`} style={{ ...MONO, fontSize: 10, color: '#d8e2eb', lineHeight: 1.55 }}>
                  {item}
                </div>
              ))
            ) : (
              <div style={{ ...MONO, fontSize: 10, color: '#8ca0b3', lineHeight: 1.55 }}>
                We do not have a strong hold-steady instruction yet. Once the sample thickens, this area will keep us from overreacting.
              </div>
            )}
          </div>
        </div>
      </Card>

      <Card title="RECENT CALIBRATED TRADES" subtitle="compact readout of timing, room left, and how much of the move we actually captured">
        {(calibration?.recent_trades ?? []).length > 0 ? (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {(calibration?.recent_trades ?? []).slice(0, 6).map((trade) => (
              <div key={trade.trade_id} style={{
                display: 'grid',
                gridTemplateColumns: '92px 1fr auto',
                gap: 12,
                alignItems: 'center',
                padding: '9px 10px',
                borderRadius: 8,
                background: 'rgba(255,255,255,0.025)',
                border: '1px solid rgba(255,255,255,0.05)',
              }}>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
                  <span style={{ ...MONO, fontSize: 11, color: '#d8e2eb', fontWeight: 700 }}>{trade.symbol}</span>
                  <span style={{ ...MONO, fontSize: 8, color: surfaceTone(trade.pnl_pct) }}>{fmtPct(trade.pnl_pct)}</span>
                </div>
                <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                  {[`timing ${trade.entry_timing_bucket.toLowerCase()}`,
                    `window ${trade.signal_window_phase.toLowerCase()}`,
                    `room ${trade.profit_room_label.toLowerCase()}`,
                    trade.minutes_scan_to_entry != null ? `${trade.minutes_scan_to_entry.toFixed(0)}m delay` : null,
                    trade.pct_move_scan_to_entry != null ? `move cost ${fmtPct(trade.pct_move_scan_to_entry)}` : null,
                    trade.capture_ratio_pct != null ? `capture ${trade.capture_ratio_pct.toFixed(0)}%` : null,
                    trade.max_favorable_excursion_pct != null ? `mfe ${fmtPct(trade.max_favorable_excursion_pct)}` : null,
                    trade.max_adverse_excursion_pct != null ? `mae ${fmtPct(trade.max_adverse_excursion_pct)}` : null,
                  ].filter(Boolean).map((chip) => (
                    <Pill key={chip} text={chip as string} kind="dim" />
                  ))}
                </div>
                <span style={{ ...MONO, fontSize: 8, color: '#6f8498', textAlign: 'right' }}>
                  {(trade.exit_reason || '—').replaceAll('_', ' ').toLowerCase()}
                </span>
              </div>
            ))}
          </div>
        ) : (
          <div style={{ ...MONO, fontSize: 10, color: '#8ca0b3', lineHeight: 1.55 }}>
            No calibrated trades yet. This section will fill automatically once more trades close with the new timing and excursion fields.
          </div>
        )}
      </Card>
    </div>
  )
}
