import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../api'
import {
  V3_STAGE_LABELS,
  v3DeployColor,
  v3LaneColor,
  v3PolicyLabel,
  v3ProfitRoomColor,
  v3ReinfColor,
  v3RouteColor,
  v3SeverityColor,
  v3StageColor,
} from './memecoinV3'
import type {
  V3Candidate,
  V3CandidateDetailResponse,
  V3LaneStateResponse,
  V3ProofWorkspaceResponse,
  V3QueueResponse,
  V3Reason,
} from './memecoinV3'

// ── Types ──────────────────────────────────────────────────────────────────

type RugLabel = 'GOOD' | 'WARN' | 'DANGER' | 'RUGGED' | 'UNKNOWN'

interface MemecoinSignal {
  mint:             string
  symbol:           string
  price:            number
  change_1h:        number | null
  change_24h:       number | null
  volume_24h:       number
  liquidity_usd:    number
  mcap_usd:         number
  token_age_days:   number
  vol_acceleration: number
  buy_pressure?:    number   // % of 1h txns that are buys (0-100); >60% bullish
  score:            number
  rug_label:        RugLabel
  top_holder_pct:   number
  lp_locked_pct:    number
  mint_revoked:     boolean
  freeze_revoked:   boolean
  dex_url:           string
  scanned_at:        string
  narrative?:        boolean
  narrative_sources?: string[]
  // Scanner regime fields (optional — populated when backend annotates)
  scanner_regime?:            'NORMAL' | 'RELAXED_NEAR_MISS'
  scanner_relaxation_reason?: string | null
}

// Scanner regime diagnostics (from /memecoins/scanner-diagnostics)
interface ScannerDiagData {
  stage_counts: {
    returned:          number
    returned_normal:   number
    returned_relaxed:  number
  }
  top_scored: Array<{
    symbol:                    string
    mint:                      string
    score:                     number
    scanner_regime:            'NORMAL' | 'RELAXED_NEAR_MISS'
    scanner_relaxation_reason: string | null
  }>
  near_misses: Array<{
    symbol:            string
    near_miss_class:   'ACCEPTABLE_NEAR_MISS' | 'WEAK_NEAR_MISS' | 'STRUCTURAL_REJECT'
    relaxation_reason: string | null
    failed_gates:      string[]
    fail_count:        number
  }>
}

interface MemecoinPosition {
  id:            number
  mint:          string
  symbol:        string
  entry_price:   number
  current_price: number
  pnl_pct:       number
  pnl_usd:       number
  amount_usd:    number
  opened:        string
}

interface ClosedTrade {
  symbol:      string
  mint:        string
  pnl_pct:     number
  pnl_usd:     number
  exit_reason: string
  closed_at:   string
}

interface LearnedThresholds {
  thresholds:  Record<string, number | boolean>
  sample_size: number
  win_rate:    number
  rug_stats:   Record<string, { count: number; win_rate: number }>
  updated_at:  string
  confidence:  'low' | 'medium' | 'high'
}

interface MemecoinsStatus {
  signals:            MemecoinSignal[]
  positions:          MemecoinPosition[]
  stats: {
    win_rate:     number
    total_pnl:    number
    closed_count: number
  }
  recent_closed:      ClosedTrade[]
  learned_thresholds: LearnedThresholds | null
}

// Patch 182: score threshold decision support
interface ScoreAnalysis {
  config_score_min: number
  verdict:    { label: string; message: string }
  threshold_sim: Array<{
    threshold: number; n: number; wr: number; avg_24h: number
    avg_win: number | null; avg_loss: number | null; is_current: boolean
  }>
  optimal_window: { lo: number; hi: number; n: number; wr: number; avg_24h: number } | null
  tuner: {
    min_score: number; max_score: number; confidence: string; sample_size: number; win_rate: number
    score_bands?: Array<{ lo: number; hi: number; n: number; wr: number; avg_4h: number; expectancy: number }>
    multi_band_mode?: boolean
  } | null
  bought_split: {
    bought:     { n: number; avg_score: number; wr: number; avg_24h: number }
    not_bought: { n: number; avg_score: number; wr: number; avg_24h: number }
  }
  bands: Array<{ lo: number; n: number; wr: number; avg_24h: number; avg_win: number | null; avg_loss: number | null }>
  horizon_comparison?: {
    n_both: number
    bands_4h:           Array<{ lo: number; hi: number; n: number; wr: number; avg_ret: number; expectancy: number }>
    bands_24h:          Array<{ lo: number; hi: number; n: number; wr: number; avg_ret: number; expectancy: number }>
    bands_missed_by_4h: Array<{ lo: number; hi: number; n: number; wr: number; avg_ret: number; expectancy: number }>
    verdict: { label: 'SWITCH_RECOMMENDED' | 'ALIGNED' | 'INSUFFICIENT_DATA'; message: string }
    error?: string
  }
}

interface ScoreBucket {
  label:          string
  count:          number
  win_rate_4h:    number | null
  avg_return_1h:  number | null
  avg_return_4h:  number | null
  avg_return_24h: number | null
  buy_rate:       number | null
}

interface RugBucket {
  label:         string
  count:         number
  win_rate_4h:   number | null
  avg_return_4h: number | null
}

interface NarrativeCoin {
  symbol:  string
  name?:   string
  rank?:   number | null
  mint?:   string
  boosts?: number
  source:  string
}

interface NarrativeData {
  updated_at?:  string | null
  coingecko?:   NarrativeCoin[]
  dexscreener?: NarrativeCoin[]
}

interface AnalyticsData {
  total_tracked:      number
  complete:           number
  pending:            number
  bought_count:       number
  score_buckets:      ScoreBucket[]
  rug_breakdown:      RugBucket[]
  top_performers:     Array<{
    symbol: string; score: number; rug_label: string
    mcap_at_scan: number | null; token_age_days: number | null
    vol_acceleration: number | null; top_holder_pct: number | null
    return_1h_pct: number | null; return_4h_pct: number | null
    return_24h_pct: number | null; bought: number; scanned_at: string
  }>
  learned_thresholds: LearnedThresholds | null
  auto_buy?: {
    enabled:          boolean
    dry_run:          boolean
    score_min:        number
    max_open:         number
    buy_usd:          number
    tuner_threshold:  number
    complete_pct:     number
  }
  phase?:         number | null
  phase_label?:   string | null
  phase_desc?:    string | null
  next_milestone?: number | null
  phase_pct?:     number | null
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

// ── Helpers ────────────────────────────────────────────────────────────────

const MONO: React.CSSProperties = { fontFamily: 'JetBrains Mono, monospace' }

function fmtPrice(p: number): string {
  if (!p) return '—'
  if (p >= 1)      return `$${p.toFixed(4)}`
  if (p >= 0.0001) return `$${p.toFixed(6)}`
  return `$${p.toExponential(3)}`
}

function fmtMcap(v: number): string {
  if (!v || v <= 0) return '—'
  if (v >= 1_000_000_000) return `$${(v / 1_000_000_000).toFixed(1)}B`
  if (v >= 1_000_000)     return `$${(v / 1_000_000).toFixed(1)}M`
  if (v >= 1_000)         return `$${(v / 1_000).toFixed(0)}k`
  return `$${v.toFixed(0)}`
}

function fmtAge(d: number): string {
  if (!d || d <= 0)   return '—'
  if (d < 1)          return `${Math.round(d * 24)}h`
  if (d < 100)        return `${d.toFixed(0)}d`
  return `${Math.round(d / 30)}mo`
}

function fmtRelTime(ts: string): string {
  if (!ts) return '—'
  try {
    const d   = new Date(ts.includes('T') ? ts : ts + 'Z')
    const ago = Math.floor((Date.now() - d.getTime()) / 1000)
    if (ago < 60)    return `${ago}s ago`
    if (ago < 3600)  return `${Math.floor(ago / 60)}m ago`
    if (ago < 86400) return `${Math.floor(ago / 3600)}h ago`
    return `${Math.floor(ago / 86400)}d ago`
  } catch { return '—' }
}

function scoreColor(s: number) {
  if (s >= 70) return '#00d48a'
  if (s >= 50) return '#f59e0b'
  return '#ef4444'
}

function tradeQualityColor(v?: string | null) {
  if (v === 'CLEAN') return '#00d48a'
  if (v === 'WATCH') return '#60a5fa'
  if (v === 'UNSTABLE') return '#f59e0b'
  if (v === 'AVOID') return '#ef4444'
  return '#4d6070'
}

function tradeQualityLabel(v?: string | null) {
  if (!v) return 'market unknown'
  return `market ${v.toLowerCase()}`
}

function pnlColor(n: number) { return n >= 0 ? '#00d48a' : '#ef4444' }

function rugColor(l: RugLabel | string) {
  if (l === 'GOOD')              return '#00d48a'
  if (l === 'WARN')              return '#f59e0b'
  if (l === 'DANGER' || l === 'RUGGED') return '#ef4444'
  return '#4d5a6e'
}

function rugEmoji(l: RugLabel | string) {
  if (l === 'GOOD')              return '🟢'
  if (l === 'WARN')              return '🟡'
  if (l === 'DANGER' || l === 'RUGGED') return '🔴'
  return '⚪'
}

function confidenceColor(c: string) {
  if (c === 'high')   return '#00d48a'
  if (c === 'medium') return '#f59e0b'
  return '#4d5a6e'
}

function calibrationAccent(metric?: number | null, positiveGood = true) {
  if (metric == null) return '#4d6070'
  if (metric === 0) return '#60a5fa'
  const positive = metric > 0
  return (positive === positiveGood) ? '#00d48a' : '#ef4444'
}

function timingBucketColor(bucket?: string | null) {
  const v = String(bucket || '').toUpperCase()
  if (v === 'IMMEDIATE' || v === 'EARLY') return '#00d48a'
  if (v === 'FAST' || v === 'MID') return '#60a5fa'
  if (v === 'LATE') return '#f59e0b'
  if (v === 'VERY_LATE' || v === 'EXHAUSTED') return '#ef4444'
  return '#4d6070'
}

type TopSetupEntry = {
  signal: MemecoinSignal
  candidate?: V3Candidate
  rank: number
  readiness: number
  stage: string
  blocker: string | null
  status: {
    label: string
    color: string
    unsafe: boolean
    clean: boolean
    note: string
  }
}

function topSetupReadiness(signal: MemecoinSignal, candidate?: V3Candidate): {
  label: string
  color: string
  unsafe: boolean
  clean: boolean
  note: string
} {
  const blocker = candidate?.proof.hard_blockers?.[0]?.key ?? null
  const readiness =
    candidate?.proof.readiness_score ??
    candidate?.scores.readiness ??
    candidate?.proof.confidence ??
    signal.score
  const severeSafetyBlock =
    signal.rug_label === 'DANGER' ||
    signal.rug_label === 'RUGGED' ||
    blocker === 'rug_warning' ||
    blocker === 'rug_warn_review' ||
    blocker === 'score_above_hard_ceiling' ||
    signal.top_holder_pct > 15

  if (!blocker && signal.rug_label === 'GOOD' && readiness >= 78) {
    return {
      label: 'BEST NOW',
      color: '#00d48a',
      unsafe: false,
      clean: true,
      note: 'cleanest current setup',
    }
  }
  if (
    blocker === 'buy_pressure_low' ||
    blocker === 'no_reinforcement' ||
    blocker === 'first_leg_unconfirmed' ||
    blocker === 'deployment_blocked'
  ) {
    return {
      label: 'WATCH CLOSELY',
      color: '#60a5fa',
      unsafe: false,
      clean: signal.rug_label !== 'DANGER' && signal.rug_label !== 'RUGGED' && signal.top_holder_pct <= 15,
      note: blocker.replace(/_/g, ' '),
    }
  }
  return {
    label: 'NOT READY',
    color: '#f59e0b',
    unsafe: severeSafetyBlock,
    clean: !severeSafetyBlock,
    note: blocker?.replace(/_/g, ' ') ?? 'still building',
  }
}

function getTopSetupEntries(signals: MemecoinSignal[], queue?: V3QueueResponse): TopSetupEntry[] {
  const candidates = Object.values(queue?.groups ?? {}).flat()
  const candidateByMint = new Map(candidates.map(c => [c.mint, c]))

  return [...signals]
    .map(signal => {
      const candidate = candidateByMint.get(signal.mint)
      const readiness =
        candidate?.proof.readiness_score ??
        candidate?.scores.readiness ??
        candidate?.proof.confidence ??
        Math.max(25, Math.min(95, signal.score))
      const support = candidate?.reinforcement.support_score ?? candidate?.scores.support ?? 0
      const safety = candidate?.scores.safety ?? candidate?.safety.safety_confidence ?? 0
      const rank = (readiness * 0.55) + (support * 0.2) + (safety * 0.1) + (signal.score * 0.15)
      const stage = candidate?.proof.stage ?? 'SCANNER_PENDING'
      const blocker = candidate?.proof.hard_blockers?.[0]?.label ?? null
      const status = topSetupReadiness(signal, candidate)
      return { signal, candidate, rank, readiness, stage, blocker, status }
    })
    .sort((a, b) => b.rank - a.rank)
    .slice(0, 5)
}

// ── Extracted components ───────────────────────────────────────────────────

function TopBuyList({
  signals,
  queue,
}: {
  signals: MemecoinSignal[]
  queue?: V3QueueResponse
}) {
  const top = getTopSetupEntries(signals, queue)

  if (top.length === 0) return null

  const topEntries = top.map((entry, idx) => ({ ...entry, idx }))

  const closestClean =
    topEntries.find(entry => entry.status.clean && entry.status.label !== 'NOT READY') ||
    topEntries.find(entry => entry.status.clean) ||
    null

  const strongestUnsafe =
    topEntries.find(entry => entry.status.unsafe) ||
    null

  return (
    <section>
      <ZoneLabel text="TOP 5 BUY SETUPS" />
      {(closestClean || strongestUnsafe) && (
        <div style={{
          display: 'flex',
          flexWrap: 'wrap',
          gap: 8,
          marginBottom: 8,
        }}>
          {closestClean && (
            <div style={{
              display: 'flex',
              alignItems: 'center',
              gap: 8,
              padding: '6px 10px',
              background: 'rgba(0,212,138,0.06)',
              border: '1px solid rgba(0,212,138,0.14)',
              borderRadius: 6,
            }}>
              <span style={{ ...MONO, fontSize: 7, color: '#00d48a', letterSpacing: '0.10em' }}>CLOSEST CLEAN</span>
              <span style={{ ...MONO, fontSize: 9, color: 'rgba(255,255,255,0.85)', fontWeight: 700 }}>
                {closestClean.signal.symbol}
              </span>
              <span style={{ ...MONO, fontSize: 8, color: closestClean.status.color }}>
                {closestClean.status.label.toLowerCase()}
              </span>
            </div>
          )}
          {strongestUnsafe && (
            <div style={{
              display: 'flex',
              alignItems: 'center',
              gap: 8,
              padding: '6px 10px',
              background: 'rgba(245,158,11,0.06)',
              border: '1px solid rgba(245,158,11,0.14)',
              borderRadius: 6,
            }}>
              <span style={{ ...MONO, fontSize: 7, color: '#f59e0b', letterSpacing: '0.10em' }}>STRONG BUT UNSAFE</span>
              <span style={{ ...MONO, fontSize: 9, color: 'rgba(255,255,255,0.85)', fontWeight: 700 }}>
                {strongestUnsafe.signal.symbol}
              </span>
              <span style={{ ...MONO, fontSize: 8, color: '#f59e0b' }}>
                {strongestUnsafe.blocker ?? strongestUnsafe.status.note}
              </span>
            </div>
          )}
        </div>
      )}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
        {topEntries.map(({ signal, readiness, stage, blocker, status, idx, candidate }) => {
          const walletLevel = top[idx]?.candidate?.reinforcement.wallet?.level ?? 'NONE'
          const walletBadge =
            walletLevel === 'STRONG' ? 'wallet strong'
            : walletLevel === 'MODERATE' ? 'wallet backed'
            : walletLevel === 'LIGHT' ? 'wallet light'
            : null
          const timing = candidate?.scores.timing
          const market = candidate?.scores.market
          const support = candidate?.scores.support ?? candidate?.reinforcement.support_score ?? null
          const tradeQualityVerdict = candidate?.trade_quality?.verdict ?? null
          const profitRoom = candidate?.profit_room ?? null
          const profitRoomScore = candidate?.scores?.profit_room ?? null
          return (
            <div
              key={signal.mint}
              style={{
                display: 'grid',
                gridTemplateColumns: '28px 88px 88px 72px 78px 1fr',
                gap: 10,
                alignItems: 'center',
                padding: '9px 12px',
                background: 'rgba(255,255,255,0.018)',
                border: '1px solid rgba(255,255,255,0.05)',
                borderRadius: 6,
              }}
            >
              <span style={{ ...MONO, fontSize: 9, color: '#4d6070' }}>#{idx + 1}</span>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <span style={{ ...MONO, fontSize: 11, fontWeight: 700, color: 'rgba(255,255,255,0.85)' }}>
                  {signal.symbol}
                </span>
                <span title={signal.rug_label} style={{ fontSize: 10, color: rugColor(signal.rug_label) }}>
                  {rugEmoji(signal.rug_label)}
                </span>
              </div>
              <span style={{ ...MONO, fontSize: 8, color: status.color, fontWeight: 700, letterSpacing: '0.08em' }}>
                {status.label}
              </span>
              <span style={{ ...MONO, fontSize: 9, color: scoreColor(signal.score), fontWeight: 700 }}>
                sc {signal.score.toFixed(0)}
              </span>
              <span style={{ ...MONO, fontSize: 9, color: readiness >= 75 ? '#00d48a' : readiness >= 60 ? '#f59e0b' : '#4d6070', fontWeight: 700 }}>
                ready {readiness.toFixed(0)}
              </span>
              <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap', minWidth: 0 }}>
                <span style={{ ...MONO, fontSize: 7, color: v3StageColor(stage), letterSpacing: '0.08em' }}>
                  {V3_STAGE_LABELS[stage] ?? stage}
                </span>
                {timing != null && (
                  <span style={{ ...MONO, fontSize: 8, color: scoreColor(timing) }}>
                    t {timing.toFixed(0)}
                  </span>
                )}
                {market != null && (
                  <span style={{ ...MONO, fontSize: 8, color: scoreColor(market) }}>
                    m {market.toFixed(0)}
                  </span>
                )}
                {tradeQualityVerdict && (
                  <span style={{ ...MONO, fontSize: 8, color: tradeQualityColor(tradeQualityVerdict) }}>
                    {tradeQualityLabel(tradeQualityVerdict)}
                  </span>
                )}
                {support != null && (
                  <span style={{ ...MONO, fontSize: 8, color: scoreColor(support) }}>
                    s {support.toFixed(0)}
                  </span>
                )}
                <span style={{ ...MONO, fontSize: 8, color: 'var(--chrome)' }}>
                  mc {fmtMcap(signal.mcap_usd)}
                </span>
                <span style={{ ...MONO, fontSize: 8, color: signal.vol_acceleration >= 5 ? '#00d48a' : '#f59e0b' }}>
                  va {signal.vol_acceleration.toFixed(1)}
                </span>
                <span style={{ ...MONO, fontSize: 8, color: 'var(--chrome)' }}>
                  age {fmtAge(signal.token_age_days)}
                </span>
                {walletBadge && (
                  <span style={{ ...MONO, fontSize: 8, color: '#60a5fa' }}>
                    {walletBadge}
                  </span>
                )}
                {profitRoom && (() => {
                  const c = v3ProfitRoomColor(profitRoom.label)
                  return (
                    <span style={{
                      ...MONO, fontSize: 7,
                      color: c,
                      background: `${c}12`,
                      border: `1px solid ${c}28`,
                      borderRadius: 3,
                      padding: '1px 5px',
                      letterSpacing: '0.06em',
                      flexShrink: 0,
                    }}>
                      room {profitRoom.label.toLowerCase().replace('_', ' ')}
                      {profitRoomScore !== null ? ` ${profitRoomScore.toFixed(0)}` : ''}
                    </span>
                  )
                })()}
                {blocker ? (
                  <span style={{ ...MONO, fontSize: 8, color: '#f59e0b', minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    blocked: {blocker}
                  </span>
                ) : (
                  <span style={{ ...MONO, fontSize: 8, color: status.clean ? '#00d48a' : '#4d6070' }}>
                    {status.note}
                  </span>
                )}
              </div>
            </div>
          )
        })}
      </div>
    </section>
  )
}

function OperatorSummaryStrip({
  signals,
  queue,
  proofWorkspace,
  laneState,
}: {
  signals: MemecoinSignal[]
  queue?: V3QueueResponse
  proofWorkspace?: V3ProofWorkspaceResponse
  laneState?: V3LaneStateResponse
}) {
  const topEntries = getTopSetupEntries(signals, queue)
  if (topEntries.length === 0 && !proofWorkspace?.recent_blocker_summary) return null

  const closestClean =
    topEntries.find(entry => entry.status.clean && entry.status.label !== 'NOT READY') ||
    topEntries.find(entry => entry.status.clean) ||
    null

  const blockerSummary = proofWorkspace?.recent_blocker_summary
  const blockerKey = blockerSummary?.dominant_key
  const blockerLabel = blockerKey ? blockerKey.replace(/_/g, ' ') : null
  const blockerCount = blockerSummary?.categories?.find(b => b.key === blockerKey)?.count ?? null
  const lane = laneState?.lane_state
  const slotGates = laneState?.proof_slots?.slot_gates ?? []
  const failedGate = slotGates.find(g => !g.passed)
  const laneBlockReason =
    lane?.deployment_authority === 'BLOCKED'
      ? failedGate?.key?.replace(/_/g, ' ') ?? lane?.active_policy_posture?.replace(/_/g, ' ').toLowerCase()
      : null

  return (
    <div style={{
      display: 'flex',
      flexWrap: 'wrap',
      gap: 8,
      alignItems: 'center',
      padding: '8px 10px',
      background: 'rgba(255,255,255,0.018)',
      border: '1px solid rgba(255,255,255,0.05)',
      borderRadius: 6,
    }}>
      {closestClean ? (
        <span style={{ ...MONO, fontSize: 8, color: '#00d48a' }}>
          closest valid candidate <span style={{ fontWeight: 700 }}>{closestClean.signal.symbol}</span> · {closestClean.status.label.toLowerCase()}
        </span>
      ) : (
        <span style={{ ...MONO, fontSize: 8, color: '#4d6070' }}>
          no clean candidate is close enough yet
        </span>
      )}
      {blockerLabel && (
        <>
          <span style={{ color: 'rgba(255,255,255,0.14)' }}>·</span>
          <span style={{ ...MONO, fontSize: 8, color: '#f59e0b' }}>
            dominant blocker today <span style={{ fontWeight: 700 }}>{blockerLabel}</span>{blockerCount ? ` ×${blockerCount}` : ''}
          </span>
        </>
      )}
      {laneBlockReason && (
        <>
          <span style={{ color: 'rgba(255,255,255,0.14)' }}>·</span>
          <span style={{ ...MONO, fontSize: 8, color: '#60a5fa' }}>
            lane gate <span style={{ fontWeight: 700 }}>{laneBlockReason}</span>
          </span>
        </>
      )}
    </div>
  )
}

function NarrativeStrip({ nd }: { nd: NarrativeData | undefined }) {
  const cgCoins  = nd?.coingecko   ?? []
  const dexCoins = nd?.dexscreener ?? []
  const hasData  = cgCoins.length > 0 || dexCoins.length > 0
  const updAt    = nd?.updated_at
  const updLabel = updAt ? (() => {
    try {
      const ago = Math.floor((Date.now() - new Date(updAt).getTime()) / 60000)
      if (ago < 60) return `${ago}m ago`
      return `${Math.floor(ago / 60)}h ago`
    } catch { return '' }
  })() : null

  return (
    <div style={{
      background: 'rgba(255,255,255,0.015)',
      border: '1px solid rgba(255,255,255,0.05)',
      borderRadius: 8, padding: '10px 16px',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: hasData ? 10 : 0 }}>
        <span style={{ ...MONO, color: 'var(--dim)', fontSize: 8, letterSpacing: '0.12em', flexShrink: 0 }}>
          🔥 NARRATIVE TRENDING
        </span>
        {updLabel && (
          <span style={{ ...MONO, color: 'var(--dim)', fontSize: 8 }}>
            updated {updLabel}
          </span>
        )}
        {!hasData && (
          <span style={{ ...MONO, color: 'var(--dim)', fontSize: 8 }}>
            collecting… refreshes every 4h via research loop
          </span>
        )}
      </div>

      {hasData && (
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
          {/* CoinGecko coins */}
          {cgCoins.slice(0, 7).map(c => (
            <span key={`cg-${c.symbol}`} style={{
              ...MONO, fontSize: 8, fontWeight: 700,
              color: '#f59e0b',
              background: 'rgba(245,158,11,0.08)',
              border: '1px solid rgba(245,158,11,0.2)',
              borderRadius: 4, padding: '3px 7px',
              letterSpacing: '0.08em',
            }}>
              {c.symbol}
              <span style={{ color: '#f59e0b55', fontWeight: 400 }}> CG</span>
            </span>
          ))}

          {/* DexScreener Solana boosted */}
          {dexCoins.slice(0, 10).map(t => (
            <span key={`dex-${t.mint ?? t.symbol}`} style={{
              ...MONO, fontSize: 8, fontWeight: 700,
              color: '#00d48a',
              background: 'rgba(0,212,138,0.06)',
              border: '1px solid rgba(0,212,138,0.15)',
              borderRadius: 4, padding: '3px 7px',
              letterSpacing: '0.08em',
            }}>
              {t.symbol || t.mint?.slice(0, 6) + '…'}
              {t.boosts && t.boosts > 0 && (
                <span style={{ color: '#00d48a55', fontWeight: 400 }}> ×{t.boosts}</span>
              )}
              <span style={{ color: '#00d48a55', fontWeight: 400 }}> DEX</span>
            </span>
          ))}
        </div>
      )}
    </div>
  )
}

// ── Patch 182: Score threshold analysis panel ──────────────────────────────────

function ScoreAnalysisPanel({ sa }: { sa: ScoreAnalysis }) {
  const verdictColor = sa.verdict.label === 'MISALIGNED' ? '#ef4444'
    : sa.verdict.label === 'SUBOPTIMAL' ? '#f59e0b' : '#00d48a'

  const ow = sa.optimal_window
  const envT = sa.threshold_sim.find(t => t.is_current)
  const allT  = sa.threshold_sim.find(t => t.threshold === 0)

  return (
    <div style={{
      background: `${verdictColor}08`,
      border: `1px solid ${verdictColor}25`,
      borderRadius: 6, padding: '10px 14px', marginBottom: 14,
    }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <span style={{ color: verdictColor, fontSize: 8 }}>◆</span>
          <span style={{ color: 'var(--muted)', fontSize: 8, letterSpacing: '0.1em', ...MONO }}>
            SCORE THRESHOLD ANALYSIS
          </span>
        </div>
        <span style={{
          color: verdictColor, fontSize: 8, ...MONO,
          background: `${verdictColor}15`, border: `1px solid ${verdictColor}30`,
          borderRadius: 3, padding: '2px 6px', fontWeight: 700,
        }}>
          {sa.verdict.label}
        </span>
      </div>

      {/* Verdict message */}
      <div style={{ color: '#8a9ab0', fontSize: 8, ...MONO, marginBottom: 10, lineHeight: 1.5 }}>
        {sa.verdict.message}
      </div>

      {/* Two-column layout: threshold sim + summary stats */}
      <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>

        {/* Threshold sim table */}
        <div style={{ flex: '1 1 200px' }}>
          <div style={{ color: 'var(--dim)', fontSize: 8, letterSpacing: '0.08em', ...MONO, marginBottom: 5 }}>
            THRESHOLD COMPARISON (24H OUTCOMES)
          </div>
          <table style={{ width: '100%', borderCollapse: 'collapse', ...MONO }}>
            <thead>
              <tr style={{ color: 'var(--dim)', fontSize: 8 }}>
                <th style={{ textAlign: 'left', paddingBottom: 3 }}>GATE</th>
                <th style={{ textAlign: 'right', paddingBottom: 3 }}>N</th>
                <th style={{ textAlign: 'right', paddingBottom: 3 }}>WR%</th>
                <th style={{ textAlign: 'right', paddingBottom: 3 }}>AVG24H</th>
              </tr>
            </thead>
            <tbody>
              {sa.threshold_sim.map(t => {
                const isCurrent = t.is_current
                const wrColor   = t.wr >= 45 ? '#00d48a' : t.wr >= 30 ? '#f59e0b' : '#ef4444'
                const retColor  = t.avg_24h >= 0 ? '#00d48a' : '#ef4444'
                return (
                  <tr key={t.threshold} style={{
                    borderTop: '1px solid rgba(255,255,255,0.03)',
                    background: isCurrent ? `${verdictColor}10` : 'transparent',
                  }}>
                    <td style={{ padding: '4px 0', fontSize: 9, color: isCurrent ? verdictColor : 'var(--muted)', fontWeight: isCurrent ? 700 : 400 }}>
                      ≥{t.threshold}{isCurrent ? ' ← ENV' : ''}
                    </td>
                    <td style={{ textAlign: 'right', padding: '4px 6px', fontSize: 9, color: 'var(--dim)' }}>{t.n}</td>
                    <td style={{ textAlign: 'right', padding: '4px 6px', fontSize: 10, fontWeight: 700, color: wrColor }}>{t.wr.toFixed(0)}%</td>
                    <td style={{ textAlign: 'right', padding: '4px 0', fontSize: 9, color: retColor }}>
                      {t.avg_24h >= 0 ? '+' : ''}{t.avg_24h.toFixed(1)}%
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>

        {/* Right column: optimal window + bought split */}
        <div style={{ flex: '0 1 180px', display: 'flex', flexDirection: 'column', gap: 10 }}>

          {/* Optimal window */}
          {ow && (
            <div>
              <div style={{ color: 'var(--dim)', fontSize: 8, letterSpacing: '0.08em', ...MONO, marginBottom: 5 }}>
                OPTIMAL SCORE WINDOW
              </div>
              <div style={{ ...MONO }}>
                <span style={{ color: '#00d48a', fontSize: 12, fontWeight: 700 }}>
                  {ow.lo}–{ow.hi}
                </span>
                <span style={{ color: 'var(--dim)', fontSize: 8 }}> score range</span>
              </div>
              <div style={{ color: '#8a9ab0', fontSize: 8, ...MONO, marginTop: 2 }}>
                n={ow.n} · WR <span style={{ color: '#00d48a' }}>{ow.wr.toFixed(0)}%</span>
                {' '}· avg <span style={{ color: ow.avg_24h >= 0 ? '#00d48a' : '#ef4444' }}>
                  {ow.avg_24h >= 0 ? '+' : ''}{ow.avg_24h.toFixed(1)}%
                </span>
              </div>
              {/* Patch 183: show multi-band or single-band tuner recommendation */}
              {sa.tuner?.multi_band_mode && sa.tuner.score_bands && sa.tuner.score_bands.length > 0 ? (
                <div style={{ marginTop: 6 }}>
                  <div style={{ color: 'var(--dim)', fontSize: 8, letterSpacing: '0.08em', ...MONO, marginBottom: 4 }}>
                    TUNER BANDS (4H-OPTIMIZED)
                  </div>
                  {sa.tuner.score_bands.map((b, i) => {
                    const bColor = b.wr >= 60 ? '#00d48a' : b.wr >= 50 ? '#f59e0b' : '#8a9ab0'
                    return (
                      <div key={i} style={{
                        display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                        marginBottom: 3, padding: '3px 6px', borderRadius: 3,
                        background: i === 0 ? `${bColor}12` : 'transparent',
                        border: `1px solid ${bColor}${i === 0 ? '25' : '10'}`,
                      }}>
                        <span style={{ color: bColor, fontSize: 9, fontWeight: 700, ...MONO }}>
                          {b.lo}–{b.hi}
                        </span>
                        <span style={{ color: 'var(--dim)', fontSize: 8, ...MONO }}>
                          WR {b.wr.toFixed(0)}% · 4h {b.avg_4h >= 0 ? '+' : ''}{b.avg_4h.toFixed(1)}%
                        </span>
                      </div>
                    )
                  })}
                  <div style={{ color: '#4a6280', fontSize: 8, ...MONO, marginTop: 3 }}>
                    {sa.tuner.confidence} conf · {sa.tuner.sample_size} samples
                  </div>
                </div>
              ) : sa.tuner ? (
                <div style={{ color: 'var(--dim)', fontSize: 8, ...MONO, marginTop: 4 }}>
                  tuner: {sa.tuner.min_score}–{sa.tuner.max_score}
                  {' '}({sa.tuner.confidence} conf)
                </div>
              ) : null}
            </div>
          )}

          {/* Bought vs not-bought */}
          <div>
            <div style={{ color: 'var(--dim)', fontSize: 8, letterSpacing: '0.08em', ...MONO, marginBottom: 5 }}>
              BOUGHT vs SKIPPED
            </div>
            {(['bought', 'not_bought'] as const).map(k => {
              const row = sa.bought_split[k]
              const wrC = row.wr >= 40 ? '#00d48a' : row.wr >= 25 ? '#f59e0b' : '#ef4444'
              return (
                <div key={k} style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 3 }}>
                  <span style={{ color: 'var(--dim)', fontSize: 8, ...MONO }}>
                    {k === 'bought' ? 'BOUGHT' : 'SKIPPED'}
                    <span style={{ color: '#4a6280', marginLeft: 4 }}>n={row.n}</span>
                  </span>
                  <span style={{ color: wrC, fontSize: 9, fontWeight: 700, ...MONO }}>{row.wr.toFixed(0)}%</span>
                </div>
              )
            })}
          </div>

          {/* Env vs baseline comparison */}
          {envT && allT && (
            <div style={{ color: 'var(--dim)', fontSize: 8, ...MONO, borderTop: '1px solid rgba(255,255,255,0.04)', paddingTop: 6 }}>
              ENV gate vs no-gate:{' '}
              <span style={{ color: '#ef4444', fontWeight: 700 }}>
                {envT.wr.toFixed(0)}% vs {allT.wr.toFixed(0)}%
              </span>
            </div>
          )}
        </div>
      </div>

      {/* Patch 184: Horizon comparison — 4h vs 24h tuner optimization */}
      {sa.horizon_comparison && !sa.horizon_comparison.error && (() => {
        const hc = sa.horizon_comparison!
        const hvLabel  = hc.verdict.label
        const hvColor  = hvLabel === 'SWITCH_RECOMMENDED' ? '#f59e0b'
          : hvLabel === 'ALIGNED' ? '#00d48a' : '#4a6280'
        return (
          <div style={{
            marginTop: 12, borderTop: '1px solid rgba(255,255,255,0.05)', paddingTop: 10,
          }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6 }}>
              <span style={{ color: 'var(--dim)', fontSize: 8, letterSpacing: '0.08em', ...MONO }}>
                TUNER HORIZON COMPARISON · {hc.n_both} dual-outcome samples
              </span>
              <span style={{
                color: hvColor, fontSize: 8, ...MONO, fontWeight: 700,
                background: `${hvColor}15`, border: `1px solid ${hvColor}30`,
                borderRadius: 3, padding: '2px 5px',
              }}>{hvLabel.replace('_', ' ')}</span>
            </div>

            {/* Side-by-side band columns */}
            <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 6 }}>
              {(['4h', '24h'] as const).map(hz => {
                const bList = hz === '4h' ? hc.bands_4h : hc.bands_24h
                const hzColor = hz === '4h' ? '#8a9ab0' : '#00d48a'
                return (
                  <div key={hz} style={{ flex: '1 1 140px' }}>
                    <div style={{ color: hzColor, fontSize: 8, letterSpacing: '0.08em', ...MONO, marginBottom: 4 }}>
                      {hz.toUpperCase()} BANDS ({bList.length})
                    </div>
                    {bList.map((b, i) => {
                      const isMissed = hz === '24h' && hc.bands_missed_by_4h.some(m => m.lo === b.lo && m.hi === b.hi)
                      const bColor   = b.wr >= 60 ? '#00d48a' : b.wr >= 50 ? '#f59e0b' : '#8a9ab0'
                      return (
                        <div key={i} style={{
                          display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                          padding: '3px 6px', marginBottom: 2, borderRadius: 3,
                          background: isMissed ? '#f59e0b10' : 'rgba(255,255,255,0.02)',
                          border: isMissed ? '1px solid #f59e0b30' : '1px solid transparent',
                        }}>
                          <span style={{ color: bColor, fontSize: 9, fontWeight: 700, ...MONO }}>
                            {b.lo}–{b.hi}
                            {isMissed && <span style={{ color: '#f59e0b', fontSize: 8, marginLeft: 3 }}>★</span>}
                          </span>
                          <span style={{ color: 'var(--dim)', fontSize: 8, ...MONO }}>
                            WR {b.wr.toFixed(0)}% · {hz === '4h' ? '4h' : '24h'} {b.avg_ret >= 0 ? '+' : ''}{b.avg_ret.toFixed(1)}%
                          </span>
                        </div>
                      )
                    })}
                  </div>
                )
              })}
            </div>

            {/* Verdict message */}
            <div style={{ color: hvColor, fontSize: 8, ...MONO, lineHeight: 1.5, opacity: 0.85 }}>
              {hc.verdict.message}
            </div>
          </div>
        )
      })()}
    </div>
  )
}

function LearningEngineStatus({ an, learnedT }: { an: AnalyticsData; learnedT: LearnedThresholds | null }) {
  const ab = an.auto_buy!
  // Effective score_min: use tuner learned value if medium/high confidence, else env baseline
  const effectiveScore = (learnedT && (learnedT.confidence === 'medium' || learnedT.confidence === 'high'))
    ? (learnedT.thresholds['min_score'] as number)
    : ab.score_min
  return (
    <div style={{
      background: 'rgba(255,255,255,0.02)',
      border: '1px solid rgba(255,255,255,0.06)',
      borderRadius: 8, padding: '12px 16px',
      display: 'flex', alignItems: 'center', gap: 16, flexWrap: 'wrap',
    }}>
      {/* Mode badges */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexShrink: 0 }}>
        <span style={{
          ...MONO, fontSize: 9, fontWeight: 700, letterSpacing: '0.12em',
          padding: '3px 8px', borderRadius: 3,
          background: ab.dry_run ? 'rgba(245,158,11,0.12)' : 'rgba(0,212,138,0.12)',
          border: `1px solid ${ab.dry_run ? '#f59e0b' : '#00d48a'}44`,
          color: ab.dry_run ? '#f59e0b' : '#00d48a',
        }}>
          {ab.dry_run ? 'PAPER MODE' : 'LIVE MODE'}
        </span>
        <span style={{
          ...MONO, fontSize: 9, fontWeight: 700, letterSpacing: '0.12em',
          padding: '3px 8px', borderRadius: 3,
          background: ab.enabled ? 'rgba(0,212,138,0.08)' : 'rgba(77,90,110,0.10)',
          border: `1px solid ${ab.enabled ? '#00d48a' : '#2d4060'}44`,
          color: ab.enabled ? '#00d48a' : '#4d5a6e',
        }}>
          AUTO-BUY {ab.enabled ? 'ON' : 'OFF'}
        </span>
      </div>

      {/* Tuner progress bar — Patch 149: phase system */}
      <div style={{ flex: 1, minWidth: 180 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 5 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <span style={{ ...MONO, color: 'var(--dim)', fontSize: 8, letterSpacing: '0.1em' }}>
              LEARNING LOOP
            </span>
            {an.phase_label && (
              <span style={{
                ...MONO, fontSize: 8, fontWeight: 700, letterSpacing: '0.1em',
                padding: '1px 5px', borderRadius: 3,
                background: an.phase === 4 ? 'rgba(0,212,138,0.10)' : an.phase === 3 ? 'rgba(245,158,11,0.10)' : 'rgba(96,165,250,0.08)',
                border: `1px solid ${an.phase === 4 ? 'rgba(0,212,138,0.3)' : an.phase === 3 ? 'rgba(245,158,11,0.3)' : 'rgba(96,165,250,0.2)'}`,
                color: an.phase === 4 ? '#00d48a' : an.phase === 3 ? '#f59e0b' : '#60a5fa',
              }}>
                {an.phase_label}
              </span>
            )}
          </div>
          <span style={{ ...MONO, fontSize: 8, color:
            learnedT?.confidence === 'high'   ? '#00d48a' :
            learnedT?.confidence === 'medium' ? '#f59e0b' :
            learnedT?.confidence === 'low'    ? '#f59e0b' : 'var(--muted)',
          }}>
            {an.complete} / {ab.tuner_threshold} outcomes
            {learnedT
              ? ` · ${learnedT.confidence.toUpperCase()} CONF`
              : an.complete >= 20 ? ' · LOW CONF' : ' · tuner pending'}
          </span>
        </div>
        <div style={{ height: 4, background: 'rgba(255,255,255,0.05)', borderRadius: 2, overflow: 'hidden' }}>
          <div style={{
            height: '100%',
            width: `${ab.complete_pct}%`,
            background: learnedT?.confidence === 'high' ? '#00d48a' : learnedT ? '#f59e0b' : 'rgba(90,120,160,0.45)',
            borderRadius: 2, transition: 'width 0.6s ease',
          }} />
        </div>
      </div>

      {/* Config chips */}
      <div style={{ display: 'flex', gap: 14, flexShrink: 0 }}>
        {([['BUY $', `$${ab.buy_usd}`], ['MAX OPEN', `${ab.max_open}`], ['SCORE MIN', `${effectiveScore}`]] as const).map(([lbl, val]) => (
          <div key={lbl} style={{ textAlign: 'center' }}>
            <div style={{ ...MONO, color: 'var(--dim)', fontSize: 8, letterSpacing: '0.1em', marginBottom: 2 }}>{lbl}</div>
            <div style={{ ...MONO, color: 'var(--dim)', fontSize: 11, fontWeight: 700 }}>{val}</div>
          </div>
        ))}
      </div>
    </div>
  )
}

function OpenPositionsTable({ positions, busyMints, onSell }: {
  positions: MemecoinPosition[]
  busyMints: Set<string>
  onSell: (pos: MemecoinPosition) => void
}) {
  return (
    <div style={{
      background: 'rgba(255,255,255,0.02)',
      border: '1px solid rgba(245,158,11,0.15)',
      borderRadius: 8, padding: '16px 18px',
    }}>
      <div style={{ color: '#f59e0b', fontSize: 9, letterSpacing: '0.1em', marginBottom: 14, ...MONO }}>
        ▶ OPEN POSITIONS ({positions.length})
      </div>
      <div className="pos-table-wrap">
      <table style={{ width: '100%', minWidth: 560, borderCollapse: 'collapse', ...MONO }}>
        <thead>
          <tr style={{ color: 'var(--muted)', fontSize: 9 }}>
            {['TOKEN', 'ENTRY', 'CURRENT', 'PNL', 'SIZE', 'OPENED', ''].map((h, i) => (
              <th key={i} style={{ textAlign: i === 0 ? 'left' : 'right', padding: `0 ${i === 6 ? 0 : 10}px 8px ${i === 0 ? 0 : 0}px` }}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {positions.map(p => {
            const busy    = busyMints.has(p.mint)
            const pnlBar  = Math.min(100, Math.max(0, Math.abs(p.pnl_pct)))
            const barClr  = p.pnl_pct >= 0 ? '#00d48a' : '#ef4444'
            return (
              <tr key={p.id} style={{ borderTop: '1px solid rgba(255,255,255,0.04)' }}>
                <td style={{ padding: '12px 10px 12px 0' }}>
                  <span style={{ color: '#c0cfe0', fontWeight: 700, fontSize: 13 }}>{p.symbol}</span>
                  <span style={{ color: 'var(--dim)', fontSize: 8, marginLeft: 6 }}>#{p.id}</span>
                </td>
                <td style={{ textAlign: 'right', padding: '12px 10px', color: 'var(--chrome)', fontSize: 11 }}>
                  {fmtPrice(p.entry_price)}
                </td>
                <td style={{ textAlign: 'right', padding: '12px 10px', color: '#8a9ab0', fontSize: 11 }}>
                  {fmtPrice(p.current_price)}
                </td>
                <td style={{ textAlign: 'right', padding: '12px 10px' }}>
                  <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 3 }}>
                    <span style={{ color: barClr, fontWeight: 700, fontSize: 12 }}>
                      {p.pnl_pct >= 0 ? '+' : ''}{p.pnl_pct.toFixed(1)}%
                      <span style={{ color: barClr, fontSize: 9, marginLeft: 5, fontWeight: 400 }}>
                        (${p.pnl_usd >= 0 ? '+' : ''}{p.pnl_usd.toFixed(2)})
                      </span>
                    </span>
                    <div style={{ width: 56, height: 3, background: 'rgba(255,255,255,0.06)', borderRadius: 2 }}>
                      <div style={{ width: `${pnlBar}%`, height: '100%', background: barClr, borderRadius: 2 }} />
                    </div>
                  </div>
                </td>
                <td style={{ textAlign: 'right', padding: '12px 10px', color: 'var(--chrome)', fontSize: 11 }}>
                  ${p.amount_usd.toFixed(0)}
                </td>
                <td style={{ textAlign: 'right', padding: '12px 10px', color: 'var(--dim)', fontSize: 9 }}>
                  {fmtRelTime(p.opened)}
                </td>
                <td style={{ textAlign: 'right', padding: '12px 0' }}>
                  <button
                    onClick={() => onSell(p)} disabled={busy}
                    style={{
                      background: busy ? 'rgba(239,68,68,0.03)' : 'rgba(239,68,68,0.09)',
                      border: '1px solid rgba(239,68,68,0.22)', borderRadius: 4,
                      color: busy ? 'var(--dim)' : '#ef4444',
                      cursor: busy ? 'default' : 'pointer',
                      ...MONO, fontSize: 9, padding: '5px 14px', fontWeight: 700,
                    }}
                  >
                    {busy ? '…' : 'SELL'}
                  </button>
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
      </div>
    </div>
  )
}

// ── Scanner Regime Strip — compact near-miss overview ────────────────────────

type NearMissClass = 'ACCEPTABLE_NEAR_MISS' | 'WEAK_NEAR_MISS' | 'STRUCTURAL_REJECT'

function nmClassLabel(c: NearMissClass): string {
  if (c === 'ACCEPTABLE_NEAR_MISS') return 'close miss'
  if (c === 'WEAK_NEAR_MISS')       return 'weak miss'
  if (c === 'STRUCTURAL_REJECT')    return 'structural reject'
  return c
}

function nmClassColor(c: NearMissClass): string {
  if (c === 'ACCEPTABLE_NEAR_MISS') return '#f59e0b'
  if (c === 'WEAK_NEAR_MISS')       return '#64748b'
  if (c === 'STRUCTURAL_REJECT')    return '#ef4444'
  return '#4d6070'
}

function ScannerRegimeStrip({ diag }: { diag: ScannerDiagData | undefined }) {
  if (!diag) return null
  const sc = diag.stage_counts
  const MONO: React.CSSProperties = { fontFamily: 'JetBrains Mono, monospace' }
  const hasRelaxed = sc.returned_relaxed > 0
  const relaxedItem = hasRelaxed
    ? diag.top_scored.find(t => t.scanner_regime === 'RELAXED_NEAR_MISS')
    : null

  return (
    <div style={{
      background: 'rgba(255,255,255,0.015)',
      border: '1px solid rgba(255,255,255,0.04)',
      borderRadius: 6, padding: '10px 14px',
      display: 'flex', flexDirection: 'column', gap: 10,
    }}>
      {/* Regime summary strip */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
        <span style={{ ...MONO, fontSize: 8, color: 'var(--chrome)', letterSpacing: '0.12em', fontWeight: 700 }}>
          SCANNER REGIME
        </span>
        <span style={{ ...MONO, fontSize: 9, color: sc.returned_normal > 0 ? '#00d48a' : '#4d6070' }}>
          {sc.returned_normal} normal
        </span>
        <span style={{ color: '#2d3a4a' }}>·</span>
        <span style={{ ...MONO, fontSize: 9, color: hasRelaxed ? '#c09030' : '#4d6070', fontWeight: hasRelaxed ? 700 : 400 }}>
          {sc.returned_relaxed} relaxed
        </span>
        {relaxedItem && (
          <>
            <span style={{ color: '#2d3a4a' }}>·</span>
            <span style={{
              ...MONO, fontSize: 8, color: '#c09030',
              background: 'rgba(245,158,11,0.08)',
              border: '1px solid rgba(245,158,11,0.20)',
              borderRadius: 3, padding: '1px 6px',
            }}>
              {relaxedItem.symbol} admitted on narrow fallback
              {relaxedItem.scanner_relaxation_reason ? `: ${relaxedItem.scanner_relaxation_reason}` : ''}
            </span>
          </>
        )}
      </div>

      {/* Near-miss table — compact */}
      {diag.near_misses.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          <span style={{ ...MONO, fontSize: 7, color: 'var(--recessed)', letterSpacing: '0.1em' }}>
            NEAR MISSES ({diag.near_misses.length})
          </span>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 5 }}>
            {diag.near_misses.slice(0, 8).map((nm, i) => {
              const col = nmClassColor(nm.near_miss_class)
              return (
                <div key={i} style={{
                  display: 'flex', alignItems: 'center', gap: 4,
                  background: 'rgba(0,0,0,0.2)', borderRadius: 3,
                  padding: '3px 7px', border: `1px solid ${col}20`,
                }}>
                  <span style={{ ...MONO, fontSize: 9, color: '#8a9ab0', fontWeight: 700 }}>{nm.symbol}</span>
                  <span style={{
                    ...MONO, fontSize: 7, color: col,
                    background: `${col}15`, border: `1px solid ${col}30`,
                    borderRadius: 2, padding: '1px 4px',
                  }}>
                    {nmClassLabel(nm.near_miss_class)}
                  </span>
                  {nm.relaxation_reason && (
                    <span style={{ ...MONO, fontSize: 7, color: '#c09030' }}>↑ {nm.relaxation_reason}</span>
                  )}
                  <span style={{ ...MONO, fontSize: 7, color: 'var(--recessed)' }}>
                    {nm.failed_gates.map(g => g.replace(/_below_min|_above_max|_/g, m =>
                      m === '_below_min' ? '<' : m === '_above_max' ? '>' : ' '
                    )).join(', ')}
                  </span>
                </div>
              )
            })}
          </div>
        </div>
      )}
    </div>
  )
}

function ScannerSignalsPanel({ signals, isLoading, busyMints, buyAmounts, onBuy, onAmountChange, regimeByMint }: {
  signals: MemecoinSignal[]
  isLoading: boolean
  busyMints: Set<string>
  buyAmounts: Record<string, string>
  onBuy: (signal: MemecoinSignal) => void
  onAmountChange: (mint: string, value: string) => void
  regimeByMint?: Record<string, { regime: string; reason: string | null }>
}) {
  return (
    <div style={{
      background: 'rgba(255,255,255,0.02)',
      border: '1px solid rgba(255,255,255,0.05)',
      borderRadius: 8, padding: '16px 18px',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 14 }}>
        <div style={{ color: 'var(--text2)', fontSize: 9, letterSpacing: '0.1em', ...MONO }}>
          SCANNER SIGNALS{signals.length > 0 ? ` (${signals.length})` : ''}
        </div>
        <div style={{ display: 'flex', gap: 16, ...MONO }}>
          {[
            `mcap $300k–$50M`,
            `age 1–30d`,
            `rug GOOD/WARN only`,
            `vol accel ≥5%`,
          ].map(t => (
            <span key={t} style={{ color: 'var(--muted)', fontSize: 8 }}>· {t}</span>
          ))}
        </div>
      </div>

      {isLoading ? (
        <div style={{ padding: '24px 0', color: 'var(--dim)', fontSize: 11, ...MONO }}>scanning…</div>
      ) : signals.length === 0 ? (
        <div style={{ padding: '24px 0', color: 'var(--dim)', fontSize: 11, ...MONO }}>
          no signals yet — scan runs every 5 min
        </div>
      ) : (
        <div style={{ overflowX: 'auto', WebkitOverflowScrolling: 'touch' as any }}>
          <table style={{ width: '100%', minWidth: 700, borderCollapse: 'collapse', ...MONO }}>
            <thead>
              <tr style={{ color: 'var(--muted)', fontSize: 8, letterSpacing: '0.06em' }}>
                <th style={{ textAlign: 'left',  padding: '0 8px 10px 0'  }}>SAFETY</th>
                <th style={{ textAlign: 'left',  padding: '0 10px 10px 0' }}>TOKEN</th>
                <th style={{ textAlign: 'right', padding: '0 10px 10px'   }}>PRICE</th>
                <th style={{ textAlign: 'right', padding: '0 10px 10px'   }}>MCAP</th>
                <th style={{ textAlign: 'right', padding: '0 10px 10px'   }}>AGE</th>
                <th style={{ textAlign: 'right', padding: '0 10px 10px'   }}>SCORE</th>
                <th style={{ textAlign: 'right', padding: '0 10px 10px'   }}>1H ▲</th>
                <th style={{ textAlign: 'right', padding: '0 10px 10px'   }}>VACC</th>
                <th style={{ textAlign: 'right', padding: '0 10px 10px'   }}>BUYS</th>
                <th style={{ textAlign: 'right', padding: '0 10px 10px'   }}>TOP HLDR</th>
                <th style={{ textAlign: 'right', padding: '0 0 10px'      }}>BUY</th>
              </tr>
            </thead>
            <tbody>
              {signals.map(s => {
                const busy = busyMints.has(s.mint)
                const amt  = buyAmounts[s.mint] ?? '10'
                const sc   = scoreColor(s.score)
                const rc   = rugColor(s.rug_label)
                return (
                  <tr key={s.mint} style={{ borderTop: '1px solid rgba(255,255,255,0.04)' }}>

                    {/* Safety badge */}
                    <td style={{ padding: '12px 8px 12px 0' }}>
                      <div style={{
                        display: 'inline-flex', alignItems: 'center', gap: 4,
                        background: `${rc}11`,
                        border: `1px solid ${rc}33`,
                        borderRadius: 4, padding: '3px 7px',
                      }}>
                        <span style={{ fontSize: 8 }}>{rugEmoji(s.rug_label)}</span>
                        <span style={{ color: rc, fontSize: 8, fontWeight: 700 }}>{s.rug_label}</span>
                      </div>
                      {/* Sub-indicators */}
                      <div style={{ display: 'flex', gap: 4, marginTop: 4, flexWrap: 'wrap' }}>
                        {s.mint_revoked    && <span style={{ color: '#00d48a', fontSize: 8 }}>✓MINT</span>}
                        {s.freeze_revoked  && <span style={{ color: '#00d48a', fontSize: 8 }}>✓FREEZE</span>}
                        {s.lp_locked_pct > 50 && (
                          <span style={{ color: '#7c9fd4', fontSize: 8 }}>LP{s.lp_locked_pct.toFixed(0)}%</span>
                        )}
                      </div>
                    </td>

                    {/* Token */}
                    <td style={{ padding: '12px 10px 12px 0' }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
                        <a
                          href={s.dex_url} target="_blank" rel="noreferrer"
                          style={{ color: '#c0cfe0', textDecoration: 'none', fontWeight: 700, fontSize: 13 }}
                        >
                          {s.symbol}
                        </a>
                        {s.narrative && (
                          <span title={`Trending on: ${(s.narrative_sources ?? []).join(', ')}`}
                            style={{ fontSize: 10 }}>🔥</span>
                        )}
                        {(() => {
                          const ri = regimeByMint?.[s.mint]
                          if (!ri || ri.regime === 'NORMAL') return null
                          return (
                            <span style={{
                              fontFamily: 'JetBrains Mono, monospace',
                              fontSize: 7, fontWeight: 700, letterSpacing: '0.06em',
                              color: '#c09030', background: 'rgba(245,158,11,0.10)',
                              border: '1px solid rgba(245,158,11,0.25)',
                              borderRadius: 2, padding: '1px 4px', flexShrink: 0,
                            }}
                              title={`relaxed scanner admission: ${ri.reason ?? 'close miss'}`}
                            >
                              RELAXED{ri.reason ? ` · ${ri.reason}` : ''}
                            </span>
                          )
                        })()}
                      </div>
                      <div style={{ color: 'var(--dim)', fontSize: 8, marginTop: 2 }}>
                        {s.mint.slice(0, 8)}…
                      </div>
                    </td>

                    {/* Price */}
                    <td style={{ textAlign: 'right', padding: '12px 10px', color: '#8a9ab0', fontSize: 11 }}>
                      {fmtPrice(s.price)}
                    </td>

                    {/* Mcap */}
                    <td style={{ textAlign: 'right', padding: '12px 10px' }}>
                      <span style={{ color: '#7c9fd4', fontSize: 11 }}>{fmtMcap(s.mcap_usd)}</span>
                    </td>

                    {/* Age */}
                    <td style={{ textAlign: 'right', padding: '12px 10px', color: 'var(--chrome)', fontSize: 11 }}>
                      {fmtAge(s.token_age_days)}
                    </td>

                    {/* Score */}
                    <td style={{ textAlign: 'right', padding: '12px 10px' }}>
                      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 3 }}>
                        <span style={{ color: sc, fontWeight: 800, fontSize: 14 }}>{s.score}</span>
                        <div style={{ width: 40, height: 3, background: 'rgba(255,255,255,0.06)', borderRadius: 2 }}>
                          <div style={{ width: `${s.score}%`, height: '100%', background: sc, borderRadius: 2 }} />
                        </div>
                      </div>
                    </td>

                    {/* 1H */}
                    <td style={{
                      textAlign: 'right', padding: '12px 10px', fontWeight: 700, fontSize: 12,
                      color: (s.change_1h ?? 0) >= 0 ? '#00d48a' : '#ef4444',
                    }}>
                      {s.change_1h != null ? `${s.change_1h >= 0 ? '+' : ''}${s.change_1h.toFixed(1)}%` : '—'}
                    </td>

                    {/* Vol acceleration */}
                    <td style={{ textAlign: 'right', padding: '12px 10px' }}>
                      <span style={{
                        color: s.vol_acceleration >= 20 ? '#00d48a' : s.vol_acceleration >= 10 ? '#f59e0b' : '#4d5a6e',
                        fontSize: 11, fontWeight: s.vol_acceleration >= 20 ? 700 : 400,
                      }}>
                        {s.vol_acceleration.toFixed(0)}%
                      </span>
                      <div style={{ color: 'var(--dim)', fontSize: 8, marginTop: 1 }}>of daily</div>
                    </td>

                    {/* Buy pressure */}
                    <td style={{ textAlign: 'right', padding: '12px 10px' }}>
                      {s.buy_pressure != null ? (
                        <span style={{
                          color: s.buy_pressure > 60 ? '#00d48a' : s.buy_pressure < 40 ? '#ef4444' : '#a0aec0',
                          fontSize: 11,
                          fontWeight: s.buy_pressure > 60 || s.buy_pressure < 40 ? 700 : 400,
                        }}>
                          {s.buy_pressure.toFixed(0)}%
                        </span>
                      ) : (
                        <span style={{ color: 'var(--dim)' }}>—</span>
                      )}
                      <div style={{ color: 'var(--dim)', fontSize: 8, marginTop: 1 }}>1h buys</div>
                    </td>

                    {/* Top holder */}
                    <td style={{ textAlign: 'right', padding: '12px 10px' }}>
                      <span style={{
                        color: s.top_holder_pct > 20 ? '#f59e0b' : s.top_holder_pct > 35 ? '#ef4444' : '#4d5a6e',
                        fontSize: 11,
                      }}>
                        {s.top_holder_pct > 0 ? `${s.top_holder_pct.toFixed(1)}%` : '—'}
                      </span>
                    </td>

                    {/* Buy */}
                    <td style={{ textAlign: 'right', padding: '12px 0' }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 5, justifyContent: 'flex-end' }}>
                        <span style={{ color: 'var(--dim)', fontSize: 9 }}>$</span>
                        <input
                          type="number" value={amt} min="1"
                          onChange={e => onAmountChange(s.mint, e.target.value)}
                          style={{
                            width: 44, background: 'rgba(255,255,255,0.04)',
                            border: '1px solid rgba(255,255,255,0.08)',
                            borderRadius: 3, color: '#8a9ab0',
                            fontSize: 9, padding: '3px 5px', textAlign: 'right',
                            fontFamily: 'JetBrains Mono, monospace',
                          }}
                        />
                        <button
                          onClick={() => onBuy(s)} disabled={busy}
                          style={{
                            background: busy ? 'rgba(0,212,138,0.04)' : 'rgba(0,212,138,0.11)',
                            border: '1px solid rgba(0,212,138,0.28)', borderRadius: 4,
                            color: busy ? 'var(--dim)' : '#00d48a',
                            cursor: busy ? 'default' : 'pointer',
                            ...MONO, fontSize: 9, padding: '5px 14px', fontWeight: 700,
                          }}
                        >
                          {busy ? '…' : 'BUY'}
                        </button>
                      </div>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

function LearningSystem({ an, learnedT }: { an: AnalyticsData | undefined; learnedT: LearnedThresholds | null }) {
  // Patch 182: score threshold analysis — hook lives here so JSX below can access it
  const scoreAnalysisQuery = useQuery<ScoreAnalysis>({
    queryKey: ['memecoins-score-analysis'],
    queryFn:  async () => (await api.get('/memecoins/score-analysis')).data,
    refetchInterval: 600_000,
  })

  return (
    <div style={{
      background: 'rgba(255,255,255,0.02)',
      border: '1px solid rgba(255,255,255,0.05)',
      borderRadius: 8, padding: '16px 18px',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 14 }}>
        <div style={{ color: 'var(--text2)', fontSize: 9, letterSpacing: '0.1em', ...MONO }}>
          LEARNING SYSTEM
        </div>
        {an && (
          <div style={{ color: 'var(--dim)', fontSize: 8, ...MONO }}>
            {an.total_tracked} tracked · {an.complete} complete · {an.pending} pending · {an.bought_count} bought
          </div>
        )}
      </div>

      {(!an || an.total_tracked === 0) ? (
        <div style={{ color: 'var(--dim)', fontSize: 9, ...MONO }}>
          collecting data — 1h/4h/24h returns fill in automatically · need 20 complete to tune
        </div>
      ) : (
        <>
          {/* Score bucket cards */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 10, marginBottom: 14 }}>
            {an.score_buckets.map(b => {
              const wr     = b.win_rate_4h
              const r4     = b.avg_return_4h
              const wrClr  = wr === null ? 'var(--dim)' : wr >= 55 ? '#00d48a' : wr >= 40 ? '#f59e0b' : '#ef4444'
              const r4Clr  = r4 === null ? 'var(--dim)' : r4 >= 0 ? '#00d48a' : '#ef4444'
              return (
                <div key={b.label} style={{
                  background: 'rgba(255,255,255,0.02)',
                  border: '1px solid rgba(255,255,255,0.05)',
                  borderRadius: 6, padding: '12px 14px',
                }}>
                  <div style={{ color: 'var(--muted)', fontSize: 8, letterSpacing: '0.1em', marginBottom: 8, ...MONO }}>
                    SCORE {b.label}
                  </div>
                  {b.count === 0 ? (
                    <div style={{ color: 'var(--dim)', fontSize: 9, ...MONO }}>no data yet</div>
                  ) : (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                        <span style={{ color: 'var(--muted)', fontSize: 8, ...MONO }}>WIN RATE 4H</span>
                        <span style={{ color: wrClr, fontWeight: 700, fontSize: 13, ...MONO }}>
                          {wr !== null ? `${wr.toFixed(0)}%` : '—'}
                        </span>
                      </div>
                      <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                        <span style={{ color: 'var(--muted)', fontSize: 8, ...MONO }}>AVG 4H</span>
                        <span style={{ color: r4Clr, fontWeight: 700, fontSize: 11, ...MONO }}>
                          {r4 !== null ? `${r4 >= 0 ? '+' : ''}${r4.toFixed(1)}%` : '—'}
                        </span>
                      </div>
                      <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                        <span style={{ color: 'var(--dim)', fontSize: 8, ...MONO }}>AVG 24H</span>
                        <span style={{ color: b.avg_return_24h !== null ? pnlColor(b.avg_return_24h) : 'var(--dim)', fontSize: 10, ...MONO }}>
                          {b.avg_return_24h !== null ? `${b.avg_return_24h >= 0 ? '+' : ''}${b.avg_return_24h.toFixed(1)}%` : '—'}
                        </span>
                      </div>
                      <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                        <span style={{ color: 'var(--dim)', fontSize: 8, ...MONO }}>SIGNALS / BUY RATE</span>
                        <span style={{ color: 'var(--chrome)', fontSize: 9, ...MONO }}>
                          {b.count} · {b.buy_rate !== null ? `${b.buy_rate.toFixed(0)}%` : '—'}
                        </span>
                      </div>
                      {/* Win rate bar */}
                      {wr !== null && (
                        <div style={{ width: '100%', height: 3, background: 'rgba(255,255,255,0.06)', borderRadius: 2, marginTop: 2 }}>
                          <div style={{ width: `${wr}%`, height: '100%', background: wrClr, borderRadius: 2, transition: 'width 0.5s' }} />
                        </div>
                      )}
                    </div>
                  )}
                </div>
              )
            })}
          </div>

          {/* Rug label breakdown */}
          {an.rug_breakdown.length > 0 && (
            <div style={{ marginBottom: 14 }}>
              <div style={{ color: 'var(--muted)', fontSize: 8, letterSpacing: '0.1em', marginBottom: 8, ...MONO }}>
                SAFETY LABEL PERFORMANCE
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: `repeat(${an.rug_breakdown.length}, 1fr)`, gap: 8 }}>
                {an.rug_breakdown.map(rb => {
                  const rc  = rugColor(rb.label)
                  const r4c = rb.avg_return_4h !== null ? pnlColor(rb.avg_return_4h) : 'var(--dim)'
                  return (
                    <div key={rb.label} style={{
                      background: `${rc}08`,
                      border: `1px solid ${rc}22`,
                      borderRadius: 6, padding: '10px 12px',
                      display: 'flex', flexDirection: 'column', gap: 4,
                    }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 5, marginBottom: 4 }}>
                        <span style={{ fontSize: 10 }}>{rugEmoji(rb.label)}</span>
                        <span style={{ color: rc, fontSize: 9, fontWeight: 700, ...MONO }}>{rb.label}</span>
                        <span style={{ color: 'var(--dim)', fontSize: 8, ...MONO }}>({rb.count})</span>
                      </div>
                      <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                        <span style={{ color: 'var(--muted)', fontSize: 8, ...MONO }}>WIN 4H</span>
                        <span style={{ color: rb.win_rate_4h !== null ? (rb.win_rate_4h >= 50 ? '#00d48a' : '#f59e0b') : 'var(--dim)', fontSize: 11, fontWeight: 700, ...MONO }}>
                          {rb.win_rate_4h !== null ? `${rb.win_rate_4h.toFixed(0)}%` : '—'}
                        </span>
                      </div>
                      <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                        <span style={{ color: 'var(--muted)', fontSize: 8, ...MONO }}>AVG 4H</span>
                        <span style={{ color: r4c, fontSize: 10, ...MONO }}>
                          {rb.avg_return_4h !== null ? `${rb.avg_return_4h >= 0 ? '+' : ''}${rb.avg_return_4h.toFixed(1)}%` : '—'}
                        </span>
                      </div>
                    </div>
                  )
                })}
              </div>
            </div>
          )}

          {/* Auto-tuned thresholds */}
          {learnedT && (
            <div style={{
              background: 'rgba(255,255,255,0.01)',
              border: `1px solid ${confidenceColor(learnedT.confidence)}18`,
              borderRadius: 6, padding: '10px 14px', marginBottom: 14,
            }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                  <span style={{ color: confidenceColor(learnedT.confidence), fontSize: 8 }}>◆</span>
                  <span style={{ color: 'var(--muted)', fontSize: 8, letterSpacing: '0.1em', ...MONO }}>
                    AUTO-TUNED THRESHOLDS
                  </span>
                </div>
                <span style={{
                  color: confidenceColor(learnedT.confidence), fontSize: 8, ...MONO,
                  background: `${confidenceColor(learnedT.confidence)}15`,
                  border: `1px solid ${confidenceColor(learnedT.confidence)}30`,
                  borderRadius: 3, padding: '2px 6px',
                }}>
                  {learnedT.confidence.toUpperCase()} CONFIDENCE · {learnedT.sample_size} samples
                </span>
              </div>
              <div style={{ display: 'flex', gap: 20, flexWrap: 'wrap' }}>
                {Object.entries(learnedT.thresholds).map(([k, v]) => (
                  <div key={k} style={{ ...MONO }}>
                    <span style={{ color: 'var(--dim)', fontSize: 8 }}>{k.replace(/_/g, ' ')} </span>
                    <span style={{ color: '#7c9fd4', fontSize: 10, fontWeight: 700 }}>
                      {typeof v === 'number' ? (k.includes('pct') || k.includes('accel') ? `${v}%` : v) : String(v)}
                    </span>
                  </div>
                ))}
              </div>
              <div style={{ color: 'var(--dim)', fontSize: 8, marginTop: 6, ...MONO }}>
                updated {fmtRelTime(learnedT.updated_at)} · overall win rate {learnedT.win_rate.toFixed(0)}%
              </div>
            </div>
          )}

          {/* Patch 182: Score threshold analysis — surfaces threshold mismatch */}
          {scoreAnalysisQuery.data?.verdict && (
            <ScoreAnalysisPanel sa={scoreAnalysisQuery.data} />
          )}

          {/* Top performers table */}
          {an.top_performers.length > 0 && (
            <>
              <div style={{ color: 'var(--muted)', fontSize: 8, letterSpacing: '0.1em', marginBottom: 8, ...MONO }}>
                TOP PERFORMERS (4H RETURN)
              </div>
              <div className="pos-table-wrap">
              <table style={{ width: '100%', minWidth: 560, borderCollapse: 'collapse', ...MONO }}>
                <thead>
                  <tr style={{ color: 'var(--muted)', fontSize: 8 }}>
                    {['TOKEN', 'SAFETY', 'MCAP', 'AGE', 'SCORE', '1H AT SCAN', '4H', '24H', 'BOUGHT'].map((h, i) => (
                      <th key={h} style={{ textAlign: i === 0 ? 'left' : 'right', padding: `0 ${i === 8 ? 0 : 8}px 6px ${i === 0 ? 0 : 0}px` }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {an.top_performers.map((p, i) => (
                    <tr key={i} style={{ borderTop: '1px solid rgba(255,255,255,0.03)' }}>
                      <td style={{ padding: '7px 8px 7px 0', color: '#8a9ab0', fontWeight: 700, fontSize: 11 }}>{p.symbol}</td>
                      <td style={{ textAlign: 'right', padding: '7px 8px' }}>
                        <span style={{ color: rugColor(p.rug_label), fontSize: 9 }}>{rugEmoji(p.rug_label)} {p.rug_label}</span>
                      </td>
                      <td style={{ textAlign: 'right', padding: '7px 8px', color: 'var(--chrome)', fontSize: 9 }}>
                        {p.mcap_at_scan ? fmtMcap(p.mcap_at_scan) : '—'}
                      </td>
                      <td style={{ textAlign: 'right', padding: '7px 8px', color: 'var(--chrome)', fontSize: 9 }}>
                        {p.token_age_days ? fmtAge(p.token_age_days) : '—'}
                      </td>
                      <td style={{ textAlign: 'right', padding: '7px 8px', color: scoreColor(p.score), fontSize: 11, fontWeight: 700 }}>
                        {p.score}
                      </td>
                      <td style={{ textAlign: 'right', padding: '7px 8px', fontSize: 9, color: 'var(--chrome)' }}>
                        {p.vol_acceleration != null ? `${p.vol_acceleration.toFixed(0)}%` : '—'}
                      </td>
                      <td style={{ textAlign: 'right', padding: '7px 8px', fontSize: 11, fontWeight: 700,
                        color: p.return_4h_pct !== null ? pnlColor(p.return_4h_pct) : 'var(--dim)' }}>
                        {p.return_4h_pct !== null ? `${p.return_4h_pct >= 0 ? '+' : ''}${p.return_4h_pct.toFixed(1)}%` : '—'}
                      </td>
                      <td style={{ textAlign: 'right', padding: '7px 8px', fontSize: 10,
                        color: p.return_24h_pct !== null ? pnlColor(p.return_24h_pct) : 'var(--dim)' }}>
                        {p.return_24h_pct !== null ? `${p.return_24h_pct >= 0 ? '+' : ''}${p.return_24h_pct.toFixed(1)}%` : '—'}
                      </td>
                      <td style={{ textAlign: 'right', padding: '7px 0', fontSize: 9,
                        color: p.bought ? '#00d48a' : 'var(--dim)' }}>
                        {p.bought ? '✓ YES' : 'passed'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              </div>
            </>
          )}
        </>
      )}
    </div>
  )
}


// ── LifecycleAttributionPanel — Patch 245 ────────────────────────────────────

interface AttributionCell {
  label:                  string
  resolved:               number
  survival_rate:          number
  positive_return_rate:   number
  meaningful_return_rate: number
  avg_return:             number
  low_n:                  boolean
}
interface LifecycleAttributionData {
  by_state:        AttributionCell[]
  by_priority:     AttributionCell[]
  by_freshness:    AttributionCell[]
  by_surface:      AttributionCell[]
  total_snapshots: number
  total_resolved:  number
  min_for_signal:  number
  generated_at:    string
}

function AttributionTable({ rows }: { rows: AttributionCell[] }) {
  if (rows.length === 0) return null
  return (
    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 10, fontFamily: 'monospace' }}>
      <thead>
        <tr style={{ color: '#2d3748' }}>
          <th style={{ textAlign: 'left',  paddingBottom: 3, fontWeight: 700 }}>BUCKET</th>
          <th style={{ textAlign: 'right', paddingBottom: 3 }}>N</th>
          <th style={{ textAlign: 'right', paddingBottom: 3 }}>SURV%</th>
          <th style={{ textAlign: 'right', paddingBottom: 3 }}>POS%</th>
          <th style={{ textAlign: 'right', paddingBottom: 3 }}>MEAN%</th>
          <th style={{ textAlign: 'right', paddingBottom: 3 }}>AVG</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r, i) => {
          const dim = r.low_n ? '#2d3748' : '#4b5563'
          const survColor  = !r.low_n && r.survival_rate >= 50  ? '#00d48a' : dim
          const posColor   = !r.low_n && r.positive_return_rate >= 50 ? '#00d48a' : dim
          const meanColor  = !r.low_n && r.meaningful_return_rate >= 30 ? '#00d48a' : dim
          const avgColor   = !r.low_n && r.avg_return >= 20 ? '#00d48a' : r.avg_return < 0 ? '#ef4444' : dim
          return (
            <tr key={i} style={{ borderTop: '1px solid #111' }}>
              <td style={{ paddingTop: 3, paddingBottom: 3, color: '#6b7280', fontWeight: 600 }}>
                {r.label}
                {r.low_n && <span style={{ color: '#1f2937', marginLeft: 4 }}>·low-n</span>}
              </td>
              <td style={{ textAlign: 'right', color: dim }}>{r.resolved}</td>
              <td style={{ textAlign: 'right', color: survColor }}>{r.survival_rate.toFixed(0)}%</td>
              <td style={{ textAlign: 'right', color: posColor  }}>{r.positive_return_rate.toFixed(0)}%</td>
              <td style={{ textAlign: 'right', color: meanColor }}>{r.meaningful_return_rate.toFixed(0)}%</td>
              <td style={{ textAlign: 'right', color: avgColor  }}>
                {r.avg_return >= 0 ? '+' : ''}{r.avg_return.toFixed(0)}%
              </td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}

function LifecycleAttributionPanel() {
  const q = useQuery<LifecycleAttributionData>({
    queryKey: ['lifecycle-attribution'],
    queryFn:  () => api.get('/memecoins/lifecycle-attribution').then(r => r.data),
    refetchInterval: 300_000,
  })
  const d = q.data
  if (!d) return null

  const hasData = d.total_resolved > 0
  const dims = [
    { label: 'BY STATE',    rows: d.by_state    },
    { label: 'BY PRIORITY', rows: d.by_priority },
    { label: 'BY FRESHNESS',rows: d.by_freshness},
    { label: 'BY SURFACE',  rows: d.by_surface  },
  ]

  return (
    <div style={{
      background: 'rgba(255,255,255,0.01)',
      border: '1px solid #111',
      borderRadius: 6, padding: '10px 12px',
    }}>
      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
        <span style={{ fontSize: 9, fontWeight: 700, color: '#283040',
                       letterSpacing: '0.12em', fontFamily: 'monospace' }}>
          LIFECYCLE ATTRIBUTION
        </span>
        <span style={{ fontSize: 9, color: '#1f2937', fontFamily: 'monospace' }}>
          {d.total_resolved}/{d.total_snapshots} resolved
        </span>
      </div>

      {!hasData ? (
        <div style={{ fontSize: 9, color: '#1f2937', fontFamily: 'monospace', lineHeight: 1.6 }}>
          collecting — {d.total_snapshots} snapshots recorded<br />
          outcomes resolve after 24h · signal appears at ~{d.min_for_signal}+ per bucket
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          {dims.filter(dim => dim.rows.length > 0).map(dim => (
            <div key={dim.label}>
              <div style={{ fontSize: 8, color: '#283040', fontFamily: 'monospace',
                            fontWeight: 700, letterSpacing: '0.1em', marginBottom: 4 }}>
                {dim.label}
              </div>
              <AttributionTable rows={dim.rows} />
            </div>
          ))}
          <div style={{ fontSize: 8, color: '#1f2937', fontFamily: 'monospace', paddingTop: 2 }}>
            surv = liq held ≥25% · mean = return ≥20% · low-n = &lt;{d.min_for_signal} resolved
          </div>
        </div>
      )}
    </div>
  )
}

// ── LifecycleValidationPanel — Patch 243 ─────────────────────────────────────

interface ValidationTier {
  priority:   'HIGH' | 'MEDIUM' | 'LOW'
  total:      number
  resolved:   number
  wins:       number
  win_rate:   number | null
  avg_return: number | null
}
interface ValidationData {
  tiers:             ValidationTier[]
  total_snapshots:   number
  total_resolved:    number
  overall_win_rate:  number | null
  generated_at:      string
}

function LifecycleValidationPanel() {
  const q = useQuery<ValidationData>({
    queryKey: ['lifecycle-validation'],
    queryFn:  () => api.get('/memecoins/lifecycle-validation').then(r => r.data),
    refetchInterval: 120_000,
  })
  const d = q.data
  if (!d) return null

  const hasData   = d.total_resolved > 0
  const tierColor = (p: string) =>
    p === 'HIGH' ? '#f59e0b' : p === 'MEDIUM' ? '#9ca3af' : '#4b5563'

  return (
    <div style={{ background: 'var(--card)', borderRadius: 8, padding: '14px 16px',
                  marginBottom: 12, border: '1px solid #1f2937' }}>

      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
        <span style={{ fontWeight: 700, letterSpacing: 1, color: '#6b7280', fontSize: 13 }}>
          LANE VALIDATION
        </span>
        <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
          {d.overall_win_rate !== null && (
            <span style={{ fontSize: 11, color: '#00d48a', fontWeight: 700 }}>
              {d.overall_win_rate}% WR
            </span>
          )}
          <span style={{ fontSize: 10, color: '#4b5563' }}>
            {d.total_resolved}/{d.total_snapshots} resolved
          </span>
        </div>
      </div>

      {!hasData ? (
        <div style={{ fontSize: 11, color: '#4b5563', fontFamily: 'monospace', padding: '4px 0' }}>
          collecting — outcomes link after 24h
        </div>
      ) : (
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11, fontFamily: 'monospace' }}>
          <thead>
            <tr style={{ color: '#4b5563' }}>
              <th style={{ textAlign: 'left',  paddingBottom: 4 }}>TIER</th>
              <th style={{ textAlign: 'right', paddingBottom: 4 }}>WR%</th>
              <th style={{ textAlign: 'right', paddingBottom: 4 }}>AVG RET</th>
              <th style={{ textAlign: 'right', paddingBottom: 4 }}>RESOLVED</th>
              <th style={{ textAlign: 'right', paddingBottom: 4 }}>TOTAL</th>
            </tr>
          </thead>
          <tbody>
            {d.tiers.map(t => (
              <tr key={t.priority} style={{ borderTop: '1px solid #1f2937' }}>
                <td style={{ paddingTop: 4, paddingBottom: 4, color: tierColor(t.priority), fontWeight: 700 }}>
                  {t.priority}
                </td>
                <td style={{ textAlign: 'right', color: t.win_rate !== null ? (t.win_rate >= 50 ? '#00d48a' : '#ef4444') : '#4b5563' }}>
                  {t.win_rate !== null ? `${t.win_rate}%` : '—'}
                </td>
                <td style={{ textAlign: 'right', color: t.avg_return !== null ? (t.avg_return >= 0 ? '#00d48a' : '#ef4444') : '#4b5563' }}>
                  {t.avg_return !== null ? `${t.avg_return > 0 ? '+' : ''}${t.avg_return}%` : '—'}
                </td>
                <td style={{ textAlign: 'right', color: '#9ca3af' }}>{t.resolved}</td>
                <td style={{ textAlign: 'right', color: '#4b5563'  }}>{t.total}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}

// ── DiscoveryMonitorPanel — Patch 249 ────────────────────────────────────────

interface DiscoveryEntry {
  symbol:           string
  mint:             string
  ingested_at:      string
  liq_at_ingress:   number | null
  vol24_at_ingress: number | null
  age_at_ingress:   number | null
  lifecycle_state:  string | null
  liq_current:      number | null
  vol_acc_current:  number | null
  first_leg_confirmed: number
  fuel_quality:     string | null
  entry_window:     string | null
  move_phase:       string | null
  status:           'CANDIDATE' | 'EARLY_WATCH' | 'TRACKING' | 'NONE'
  attention_infrastructure: string | null
  boost_active:     number
  attention_quality: string | null
}
interface DiscoveryMonitorData {
  entries:      DiscoveryEntry[]
  counts:       Record<string, number>
  total:        number
  generated_at: string
}

function DiscoveryMonitorPanel() {
  const q = useQuery<DiscoveryMonitorData>({
    queryKey:        ['discovery-monitor'],
    queryFn:         () => api.get('/memecoins/discovery-monitor').then(r => r.data),
    refetchInterval: 120_000,
  })
  const d = q.data
  if (!d) return null

  const statusColor = (s: string) =>
    s === 'CANDIDATE'   ? '#00d48a' :
    s === 'EARLY_WATCH' ? '#f59e0b' :
    s === 'TRACKING'    ? '#60a5fa' : '#374151'

  function fmtAgo(ts: string): string {
    if (!ts) return '—'
    const diffMs = Date.now() - new Date(ts.replace(' ', 'T') + 'Z').getTime()
    const h = Math.floor(diffMs / 3_600_000)
    if (h < 1) return `${Math.round(diffMs / 60_000)}m ago`
    if (h < 24) return `${h}h ago`
    return `${(h / 24).toFixed(1)}d ago`
  }

  return (
    <div style={{ background: 'var(--card)', borderRadius: 8, padding: '14px 16px',
                  marginBottom: 12, border: '1px solid #1f2937' }}>

      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
        <span style={{ fontWeight: 700, letterSpacing: 1, color: '#6b7280', fontSize: 13 }}>
          DISCOVERY MONITOR
        </span>
        <div style={{ display: 'flex', gap: 10, alignItems: 'center', fontSize: 10, fontFamily: 'monospace' }}>
          {d.counts['CANDIDATE']   > 0 && <span style={{ color: '#00d48a' }}>{d.counts['CANDIDATE']} cand</span>}
          {d.counts['EARLY_WATCH'] > 0 && <span style={{ color: '#f59e0b' }}>{d.counts['EARLY_WATCH']} watch</span>}
          <span style={{ color: '#4b5563' }}>{d.total} ingested 48h</span>
        </div>
      </div>

      {d.total === 0 ? (
        <div style={{ fontSize: 11, color: 'var(--recessed)', fontFamily: 'monospace', padding: '4px 0' }}>
          no discovery candidates in last 48h
        </div>
      ) : (
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 10, fontFamily: 'monospace' }}>
          <thead>
            <tr style={{ color: 'var(--recessed)' }}>
              <th style={{ textAlign: 'left',  paddingBottom: 5 }}>SYMBOL</th>
              <th style={{ textAlign: 'right', paddingBottom: 5 }}>INGESTED</th>
              <th style={{ textAlign: 'right', paddingBottom: 5 }}>LIQ IN</th>
              <th style={{ textAlign: 'right', paddingBottom: 5 }}>V24 IN</th>
              <th style={{ textAlign: 'right', paddingBottom: 5 }}>AGE</th>
              <th style={{ textAlign: 'right', paddingBottom: 5 }}>LC STATE</th>
              <th style={{ textAlign: 'right', paddingBottom: 5 }}>FUEL</th>
              <th style={{ textAlign: 'right', paddingBottom: 5 }}>ATTN</th>
              <th style={{ textAlign: 'right', paddingBottom: 5 }}>STATUS</th>
            </tr>
          </thead>
          <tbody>
            {d.entries.map((e, i) => (
              <tr key={i} style={{ borderTop: '1px solid #1a1a1a' }}>
                <td style={{ paddingTop: 4, paddingBottom: 4, color: '#e5e7eb', fontWeight: 600 }}>
                  {e.symbol}
                </td>
                <td style={{ textAlign: 'right', color: '#4b5563' }}>
                  {fmtAgo(e.ingested_at)}
                </td>
                <td style={{ textAlign: 'right', color: '#9ca3af' }}>
                  {e.liq_at_ingress != null
                    ? e.liq_at_ingress >= 1000
                      ? `$${(e.liq_at_ingress / 1000).toFixed(0)}k`
                      : `$${e.liq_at_ingress.toFixed(0)}`
                    : '—'}
                </td>
                <td style={{ textAlign: 'right', color: '#6b7280' }}>
                  {e.vol24_at_ingress != null && e.vol24_at_ingress > 0
                    ? e.vol24_at_ingress >= 1000
                      ? `$${(e.vol24_at_ingress / 1000).toFixed(0)}k`
                      : `$${e.vol24_at_ingress.toFixed(0)}`
                    : '—'}
                </td>
                <td style={{ textAlign: 'right', color: '#6b7280' }}>
                  {e.age_at_ingress != null ? `${e.age_at_ingress.toFixed(1)}d` : '—'}
                </td>
                <td style={{ textAlign: 'right' }}>
                  {e.lifecycle_state ? (
                    <span style={{
                      color: e.lifecycle_state === 'RELOAD' ? '#00d48a' :
                             e.lifecycle_state === 'REVIVAL' ? '#f59e0b' :
                             e.lifecycle_state === 'ACTIVE'  ? '#60a5fa' :
                             e.lifecycle_state === 'DEAD'    ? '#374151' : '#6b7280',
                    }}>
                      {e.lifecycle_state}
                    </span>
                  ) : (
                    <span style={{ color: '#283040' }}>—</span>
                  )}
                </td>
                <td style={{ textAlign: 'right', color:
                  e.fuel_quality === 'STRONG'   ? '#00d48a' :
                  e.fuel_quality === 'MODERATE' ? '#f59e0b' :
                  e.fuel_quality === 'WEAK'     ? '#ef4444' :
                  e.fuel_quality === 'TRAP'     ? '#dc2626' : '#283040'
                }}>
                  {e.fuel_quality ?? '—'}
                </td>
                <td style={{ textAlign: 'right' }}>
                  <span style={{
                    fontSize: 9, fontWeight: 700,
                    color: e.attention_quality === 'STRONG'   ? '#00d48a' :
                           e.attention_quality === 'MODERATE' ? '#f59e0b' :
                           e.attention_quality === 'WEAK'     ? '#6b7280' : '#283040',
                    letterSpacing: 0.5,
                  }}>
                    {e.attention_quality === 'STRONG'   ? '◆STR' :
                     e.attention_quality === 'MODERATE' ? '◆MOD' :
                     e.attention_quality === 'WEAK'     ? '◇WK'  :
                     e.attention_quality === 'NONE'     ? '—'    : '—'}
                    {e.boost_active === 1 ? ' ↑' : ''}
                  </span>
                </td>
                <td style={{ textAlign: 'right' }}>
                  <span style={{
                    fontSize: 9, fontWeight: 700,
                    color: statusColor(e.status),
                    letterSpacing: 0.5,
                  }}>
                    {e.status}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}


// ── QualityLanePanel — Patch 238 ─────────────────────────────────────────────

interface QLCandidate {
  symbol: string
  mint: string
  score: number | null
  top_holder_pct: number
  liquidity_usd: number
  appearances_72h: number
  qualifies: boolean
  fails: string[]
  scanned_at: string
}

interface QLRecentRow {
  symbol: string
  mint: string
  score: number | null
  top_holder_pct: number | null
  liquidity_usd: number | null
  scanned_at: string
  return_24h_pct: number | null
  status: string
  appearances_72h: number
  quality_lane_tag: boolean | null
}

interface QualityLaneData {
  live: { count: number; empty: boolean; candidates: QLCandidate[] }
  all_live_count: number
  recent_48h: { rows: QLRecentRow[]; n: number; evaluated: number; wr: number | null }
  criteria: { max_top_holder_pct: number; max_liquidity_usd: number; max_appearances_72h: number; description: string }
  generated_at: string
}

function fmtLiq(v: number | null): string {
  if (v === null) return '—'
  if (v >= 1000) return `$${(v / 1000).toFixed(0)}k`
  return `$${v.toFixed(0)}`
}

function QualityLanePanel() {
  const q = useQuery<QualityLaneData>({
    queryKey: ['memecoins-quality-lane'],
    queryFn:  () => api.get('/memecoins/quality-lane').then(r => r.data),
    refetchInterval: 120_000,
  })
  const d = q.data
  if (!d) return null

  const { live, recent_48h, criteria } = d
  const countColor = live.empty ? 'var(--dim)' : '#00d48a'

  return (
    <div style={{ background: 'var(--card)', borderRadius: 8, padding: '14px 16px', marginBottom: 12,
                  border: live.empty ? '1px solid #333' : '1px solid #1a4a2e' }}>

      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
        <span style={{ fontWeight: 700, letterSpacing: 1, color: live.empty ? 'var(--dim)' : '#00d48a' }}>
          QUALITY LANE
        </span>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <span style={{ fontSize: 11, color: countColor, fontWeight: 700 }}>
            {live.empty ? 'EMPTY' : `LIVE: ${live.count}`}
          </span>
          <span style={{ fontSize: 11, color: 'var(--dim)' }}>
            {d.all_live_count} total in cache
          </span>
        </div>
      </div>

      {/* Criteria reminder */}
      <div style={{ fontSize: 11, color: '#6b7280', marginBottom: 10, fontFamily: 'monospace' }}>
        top_h &lt; {criteria.max_top_holder_pct}% · liq &lt; {fmtLiq(criteria.max_liquidity_usd)} · ≤{criteria.max_appearances_72h} appearances/72h
      </div>

      {/* Live candidates */}
      {!live.empty && (
        <div style={{ marginBottom: 12 }}>
          <div style={{ fontSize: 11, color: '#9ca3af', marginBottom: 6, fontWeight: 600 }}>LIVE CANDIDATES</div>
          {live.candidates.map((c, i) => (
            <div key={i} style={{ display: 'flex', gap: 10, alignItems: 'center', padding: '5px 8px',
                                  background: '#0d2b1a', borderRadius: 5, marginBottom: 4,
                                  fontSize: 12, fontFamily: 'monospace' }}>
              <span style={{ color: '#00d48a', fontWeight: 700, minWidth: 70 }}>{c.symbol}</span>
              <span style={{ color: '#9ca3af' }}>score {c.score ?? '—'}</span>
              <span style={{ color: '#60a5fa' }}>top_h {c.top_holder_pct != null ? `${c.top_holder_pct.toFixed(1)}%` : '—'}</span>
              <span style={{ color: '#60a5fa' }}>liq {fmtLiq(c.liquidity_usd)}</span>
              <span style={{ color: c.appearances_72h <= 1 ? '#00d48a' : '#f59e0b' }}>
                ×{c.appearances_72h} 72h
              </span>
            </div>
          ))}
        </div>
      )}

      {/* Recent 48h tracked */}
      {recent_48h.n > 0 && (
        <div>
          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 6 }}>
            <span style={{ fontSize: 11, color: '#9ca3af', fontWeight: 600 }}>RECENT 48H HITS</span>
            {recent_48h.wr !== null && (
              <span style={{ fontSize: 11, color: recent_48h.wr >= 55 ? '#00d48a' : '#f59e0b', fontWeight: 700 }}>
                WR {recent_48h.wr}% ({recent_48h.evaluated} evaluated)
              </span>
            )}
          </div>
          <div style={{ maxHeight: 160, overflowY: 'auto' }}>
            {recent_48h.rows.slice(0, 12).map((r, i) => {
              const ret = r.return_24h_pct
              const retColor = ret === null ? 'var(--dim)' : ret > 0 ? '#00d48a' : '#ef4444'
              return (
                <div key={i} style={{ display: 'flex', gap: 8, alignItems: 'center', padding: '3px 8px',
                                      background: '#111', borderRadius: 4, marginBottom: 3,
                                      fontSize: 11, fontFamily: 'monospace', opacity: r.status === 'PENDING' ? 0.7 : 1 }}>
                  <span style={{ color: '#e5e7eb', minWidth: 70 }}>{r.symbol}</span>
                  <span style={{ color: '#6b7280' }}>th {r.top_holder_pct?.toFixed(1) ?? '—'}%</span>
                  <span style={{ color: '#6b7280' }}>{fmtLiq(r.liquidity_usd)}</span>
                  <span style={{ color: '#6b7280', fontSize: 10 }}>×{r.appearances_72h}</span>
                  <span style={{ marginLeft: 'auto', color: retColor, fontWeight: 700 }}>
                    {ret !== null ? `${ret > 0 ? '+' : ''}${ret.toFixed(1)}%` : r.status === 'PENDING' ? 'pending' : '—'}
                  </span>
                </div>
              )
            })}
          </div>
        </div>
      )}

      {recent_48h.n === 0 && live.empty && (
        <div style={{ fontSize: 12, color: 'var(--dim)', textAlign: 'center', padding: '6px 0' }}>
          No quality-lane candidates in scan cache or recent 48h history.
        </div>
      )}
    </div>
  )
}

// ── AttributionPanel ───────────────────────────────────────────────────────

interface AttributionBucket {
  bucket: string
  n: number
  wr_24h: number
  avg_24h: number
  cat_rate: number
  verdict: string
}

interface AttributionFactor {
  name: string
  label: string
  buckets: AttributionBucket[]
}

interface AttributionData {
  n_complete: number
  baseline_wr: number
  baseline_avg?: number
  factors: AttributionFactor[]
}

// ── Patch 272: Paper Trade Lifecycle Attribution ──────────────────────────────

interface PaperTradeBucket {
  fuel:     string
  window:   string
  n:        number
  wins:     number
  win_rate: number
  avg_pnl:  number | null
  min_pnl:  number | null
  max_pnl:  number | null
}

interface PaperTrade {
  id:                  number
  symbol:              string
  pnl_pct:             number | null
  exit_reason:         string | null
  opened_ts_utc:       string
  closed_ts_utc:       string | null
  entry_fuel_quality:  string | null
  entry_window:        string | null
  entry_move_phase:    string | null
  entry_score:         number | null
  is_pilot:            number
}

interface PaperTradeAttributionData {
  buckets:  PaperTradeBucket[]
  trades:   PaperTrade[]
  summary: {
    total_closed:    number
    context_tagged:  number
    untagged:        number
    tagged_win_rate: number | null
    tagged_avg_pnl:  number | null
  }
}

// ── Patch 286: Setup Performance Ledger + Exit-Shape Attribution ──────────────

interface ExitShapes {
  TP_CLEAN:     number
  SL_STANDARD:  number
  SL_SLIPPAGE:  number
  RUG:          number
  MANUAL:       number
}

type ShapeQuality = 'CLEAN_EXITS' | 'SLIPPAGE_HEAVY' | 'RUG_PRONE' | 'INSUFFICIENT_DATA'

interface SetupLedgerRow {
  setup_key:        string
  entry_window:     string
  fuel_quality:     string
  move_phase:       string
  lifetime_n:       number
  lifetime_wins:    number
  lifetime_wr:      number
  lifetime_avg_pnl: number
  trailing_n:       number
  trailing_wins:    number
  trailing_wr:      number
  trailing_avg_pnl: number
  status:           'WORKING' | 'DEGRADING' | 'FAILING' | 'INSUFFICIENT_DATA'
  exit_shapes:      ExitShapes
  slippage_rate:    number | null
  rug_rate:         number | null
  shape_quality:    ShapeQuality
}

interface SetupLedgerData {
  setups:               SetupLedgerRow[]
  total_closed:         number
  tagged:               number
  untagged:             number
  global_shapes:        ExitShapes
  global_slippage_rate: number | null
  global_rug_rate:      number | null
  global_shape_quality: ShapeQuality
  generated_at:         string
}

// ── Patch 301: Predictive Validation Layer ────────────────────────────────────

interface ValidationRow {
  tier?:       string   // perf tier variant
  status?:     string   // transition status variant
  n:           number
  avg_return:  number | null
  wr_10:       number | null
  wr_0:        number | null
}
interface ValidationSection {
  rows:              ValidationRow[]
  verdict:           'EARNING_ITS_PLACE' | 'NOT_YET_PROVEN' | 'FALSIFIED' | 'ERROR'
  verdict_reason:    string
  total_n:           number
  scope_note?:       string
  directional_state?: string  // e.g. "THIN_POSITIVE_SIGNAL" — early directional without promotion
}
interface IntelValidationData {
  perf_tier_forward:       ValidationSection
  transition_detector:     ValidationSection
  trust_label_validation:  ValidationSection   // Patch 305
  triage_state_validation: ValidationSection   // Patch 305
  not_yet_validated:       string[]
  generated_at:            string
}


const FUEL_COLOR: Record<string, string> = {
  STRONG:   '#00d48a',
  MODERATE: '#f59e0b',
  WEAK:     '#4d5a6e',
  TRAP:     '#ef4444',
  UNKNOWN:  '#2d4060',
}

const WIN_COLOR: Record<string, string> = {
  OPEN:    '#00d48a',
  CLOSING: '#f59e0b',
  CLOSED:  '#4d5a6e',
  UNKNOWN: '#2d4060',
}

function PaperTradeAttributionPanel() {
  const [showTrades, setShowTrades] = useState(false)

  const q = useQuery<PaperTradeAttributionData>({
    queryKey:        ['paper-trade-attribution'],
    queryFn:         () => api.get('/memecoins/paper-trade-attribution').then(r => r.data),
    refetchInterval: 120_000,
  })

  const d = q.data
  if (!d) return null

  const { buckets, trades, summary } = d
  const hasTagged = summary.context_tagged > 0

  return (
    <div style={{
      background: 'rgba(255,255,255,0.015)',
      border: '1px solid rgba(255,255,255,0.06)',
      borderRadius: 8, padding: '14px 18px',
    }}>

      {/* Header */}
      <div style={{
        display: 'flex', justifyContent: 'space-between',
        alignItems: 'center', marginBottom: 12,
      }}>
        <span style={{ color: 'var(--text2)', ...MONO, fontWeight: 700, fontSize: 10, letterSpacing: '0.12em' }}>
          PAPER TRADE ATTRIBUTION
        </span>
        <div style={{ display: 'flex', gap: 14, alignItems: 'center' }}>
          {summary.tagged_win_rate !== null && (
            <span style={{ ...MONO, fontSize: 9, color: summary.tagged_win_rate >= 50 ? '#00d48a' : '#f59e0b' }}>
              {summary.tagged_win_rate.toFixed(0)}% WR
            </span>
          )}
          {summary.tagged_avg_pnl !== null && (
            <span style={{ ...MONO, fontSize: 9, color: pnlColor(summary.tagged_avg_pnl) }}>
              avg {summary.tagged_avg_pnl >= 0 ? '+' : ''}{summary.tagged_avg_pnl.toFixed(1)}%
            </span>
          )}
          <span style={{ ...MONO, fontSize: 8, color: 'var(--chrome)' }}>
            {summary.context_tagged} tagged · {summary.untagged} untagged · {summary.total_closed} total
          </span>
        </div>
      </div>

      {/* No data yet — Patch 281: explain dependency so operator understands empty state */}
      {!hasTagged && (
        <div style={{ ...MONO, fontSize: 9, color: 'var(--chrome)', lineHeight: 1.7 }}>
          No lifecycle-tagged trades yet.
          {summary.untagged > 0 && (
            <span style={{ marginLeft: 4 }}>
              {summary.untagged} existing {summary.untagged === 1 ? 'trade was' : 'trades were'} executed
              before lifecycle context capture — attribution unavailable for those.
            </span>
          )}
          <div style={{ marginTop: 5, fontSize: 8, color: '#2a3547' }}>
            Attribution data appears here after the next auto-buy executes with lifecycle context.
            Currently blocked by F&amp;G and WR gates — will populate automatically when gates pass.
          </div>
        </div>
      )}

      {/* Fuel × Window bucket table */}
      {hasTagged && (
        <table style={{ width: '100%', borderCollapse: 'collapse', marginBottom: 10 }}>
          <thead>
            <tr>
              {['FUEL', 'WINDOW', 'N', 'WIN %', 'AVG PNL', 'RANGE'].map(h => (
                <th key={h} style={{
                  ...MONO, fontSize: 8, fontWeight: 700, color: 'var(--recessed)',
                  letterSpacing: '0.1em', padding: '4px 8px', textAlign: h === 'N' || h === 'WIN %' || h === 'AVG PNL' || h === 'RANGE' ? 'right' : 'left',
                  borderBottom: '1px solid rgba(255,255,255,0.05)',
                }}>
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {buckets.map((b, i) => (
              <tr key={i} style={{ opacity: b.fuel === 'UNKNOWN' && b.window === 'UNKNOWN' ? 0.45 : 1 }}>
                <td style={{ ...MONO, fontSize: 9, padding: '5px 8px', color: FUEL_COLOR[b.fuel] ?? '#4d5a6e', fontWeight: 600 }}>
                  {b.fuel}
                </td>
                <td style={{ ...MONO, fontSize: 9, padding: '5px 8px', color: WIN_COLOR[b.window] ?? '#4d5a6e' }}>
                  {b.window}
                </td>
                <td style={{ ...MONO, fontSize: 9, padding: '5px 8px', textAlign: 'right', color: 'var(--chrome)' }}>
                  {b.n}
                </td>
                <td style={{ ...MONO, fontSize: 9, padding: '5px 8px', textAlign: 'right', fontWeight: 700, color: b.win_rate >= 50 ? '#00d48a' : '#ef4444' }}>
                  {b.win_rate.toFixed(0)}%
                </td>
                <td style={{ ...MONO, fontSize: 9, padding: '5px 8px', textAlign: 'right', color: (b.avg_pnl ?? 0) >= 0 ? '#00d48a' : '#ef4444' }}>
                  {b.avg_pnl != null ? `${b.avg_pnl >= 0 ? '+' : ''}${b.avg_pnl.toFixed(1)}%` : '—'}
                </td>
                <td style={{ ...MONO, fontSize: 8, padding: '5px 8px', textAlign: 'right', color: 'var(--recessed)' }}>
                  {b.min_pnl != null && b.max_pnl != null
                    ? `${b.min_pnl.toFixed(0)} / ${b.max_pnl >= 0 ? '+' : ''}${b.max_pnl.toFixed(0)}%`
                    : '—'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {/* Trade list toggle */}
      {trades.length > 0 && (
        <>
          <button
            onClick={() => setShowTrades(v => !v)}
            style={{
              ...MONO, fontSize: 8, color: 'var(--chrome)', background: 'none',
              border: 'none', cursor: 'pointer', padding: '2px 0', letterSpacing: '0.08em',
            }}
          >
            {showTrades ? '▲ hide' : '▼ show'} individual trades ({trades.length})
          </button>
          {showTrades && (
            <div style={{ marginTop: 8, display: 'flex', flexDirection: 'column', gap: 3 }}>
              {trades.map(t => {
                const pct = t.pnl_pct ?? 0
                const fuelC = FUEL_COLOR[t.entry_fuel_quality ?? 'UNKNOWN'] ?? '#2d4060'
                const winC  = WIN_COLOR[t.entry_window ?? 'UNKNOWN'] ?? '#2d4060'
                const reasonC = t.exit_reason === 'TP_2X' ? '#00d48a'
                              : t.exit_reason === 'SL_50' ? '#ef4444'
                              : t.exit_reason?.startsWith('RUG') ? '#7f1d1d' : '#4d5a6e'
                return (
                  <div key={t.id} style={{
                    display: 'flex', alignItems: 'center', gap: 8,
                    padding: '4px 6px', borderRadius: 4,
                    background: 'rgba(255,255,255,0.02)',
                    flexWrap: 'wrap',
                  }}>
                    <span style={{ ...MONO, fontSize: 10, fontWeight: 700, color: '#8090a0', minWidth: 60 }}>
                      {t.symbol}
                    </span>
                    {t.entry_fuel_quality ? (
                      <span style={{ ...MONO, fontSize: 8, color: fuelC }}>{t.entry_fuel_quality}</span>
                    ) : (
                      <span style={{ ...MONO, fontSize: 8, color: 'var(--recessed)' }}>—</span>
                    )}
                    {t.entry_window ? (
                      <span style={{ ...MONO, fontSize: 8, color: winC }}>{t.entry_window}</span>
                    ) : (
                      <span style={{ ...MONO, fontSize: 8, color: 'var(--recessed)' }}>—</span>
                    )}
                    {t.entry_move_phase && (
                      <span style={{ ...MONO, fontSize: 8, color: 'var(--chrome)' }}>{t.entry_move_phase}</span>
                    )}
                    {t.entry_score != null && (
                      <span style={{ ...MONO, fontSize: 8, color: 'var(--chrome)' }}>s={t.entry_score.toFixed(0)}</span>
                    )}
                    <span style={{ ...MONO, fontSize: 8, color: reasonC, marginLeft: 'auto' }}>
                      {t.exit_reason ?? '—'}
                    </span>
                    <span style={{ ...MONO, fontSize: 10, fontWeight: 700, color: pnlColor(pct), minWidth: 52, textAlign: 'right' }}>
                      {pct >= 0 ? '+' : ''}{pct.toFixed(1)}%
                    </span>
                  </div>
                )
              })}
            </div>
          )}
        </>
      )}

      <div style={{ marginTop: 8, ...MONO, fontSize: 8, color: 'var(--deep)' }}>
        lifecycle context captured at buy time (Patch 272) · untagged = pre-patch trades
      </div>
    </div>
  )
}

// ── Patch 286: Setup Performance Ledger + Exit-Shape Attribution ──────────────

function SetupPerformanceLedgerPanel() {
  const q = useQuery<SetupLedgerData>({
    queryKey:        ['setup-performance-ledger'],
    queryFn:         () => api.get('/memecoins/setup-performance-ledger').then(r => r.data),
    refetchInterval: 120_000,
  })

  const d = q.data
  if (!d) return null

  const DIM   = 'var(--dim)'
  const MUTED = 'var(--muted)'
  const TEXT2 = 'var(--text2)'
  const SLATE = '#2d3a4a'

  function statusChipColor(s: string) {
    if (s === 'WORKING')   return '#00d48a'
    if (s === 'DEGRADING') return '#f59e0b'
    if (s === 'FAILING')   return '#ef4444'
    return SLATE
  }
  function statusChipLabel(s: string) {
    if (s === 'INSUFFICIENT_DATA') return 'NO DATA'
    return s
  }
  function shapeQualityColor(sq: string) {
    if (sq === 'CLEAN_EXITS')    return '#00d48a'
    if (sq === 'SLIPPAGE_HEAVY') return '#f59e0b'
    if (sq === 'RUG_PRONE')      return '#ef4444'
    return '#2d3a4a'
  }
  function shapeQualityLabel(sq: string) {
    if (sq === 'CLEAN_EXITS')     return 'CLEAN'
    if (sq === 'SLIPPAGE_HEAVY')  return 'SLIP↑'
    if (sq === 'RUG_PRONE')       return 'RUG↑'
    return '—'
  }
  // Render a compact exit shape mini-bar: "TP×2 SL×5 SLIP×3 RUG×3"
  function ExitShapeBar({ shapes, slippage_rate, rug_rate }: {
    shapes: ExitShapes
    slippage_rate: number | null
    rug_rate: number | null
  }) {
    const total = shapes.TP_CLEAN + shapes.SL_STANDARD + shapes.SL_SLIPPAGE + shapes.RUG + shapes.MANUAL
    if (total === 0) return <span style={{ ...MONO, fontSize: 8, color: 'var(--deep)' }}>—</span>
    return (
      <span style={{ display: 'inline-flex', gap: 5, alignItems: 'center', flexWrap: 'wrap' }}>
        {shapes.TP_CLEAN    > 0 && <span style={{ ...MONO, fontSize: 8, color: '#00d48a'  }}>TP×{shapes.TP_CLEAN}</span>}
        {shapes.SL_STANDARD > 0 && <span style={{ ...MONO, fontSize: 8, color: 'var(--chrome)'  }}>SL×{shapes.SL_STANDARD}</span>}
        {shapes.SL_SLIPPAGE > 0 && <span style={{ ...MONO, fontSize: 8, color: '#f59e0b'  }}>SLIP×{shapes.SL_SLIPPAGE}</span>}
        {shapes.RUG         > 0 && <span style={{ ...MONO, fontSize: 8, color: '#ef4444'  }}>RUG×{shapes.RUG}</span>}
        {shapes.MANUAL      > 0 && <span style={{ ...MONO, fontSize: 8, color: 'var(--recessed)'  }}>MAN×{shapes.MANUAL}</span>}
        {slippage_rate !== null  && <span style={{ ...MONO, fontSize: 8, color: slippage_rate >= 0.4 ? '#f59e0b' : '#374151' }}>slip={Math.round(slippage_rate * 100)}%</span>}
        {rug_rate      !== null  && <span style={{ ...MONO, fontSize: 8, color: rug_rate      >= 0.25 ? '#ef4444' : '#374151' }}>rug={Math.round(rug_rate * 100)}%</span>}
      </span>
    )
  }

  const hasSetups = d.setups.length > 0
  const gs        = d.global_shapes
  const gTotal    = gs.TP_CLEAN + gs.SL_STANDARD + gs.SL_SLIPPAGE + gs.RUG + gs.MANUAL

  return (
    <div style={{
      background: 'rgba(255,255,255,0.015)',
      border: '1px solid rgba(255,255,255,0.06)',
      borderRadius: 8, padding: '14px 18px',
    }}>
      {/* Header */}
      <div style={{
        display: 'flex', justifyContent: 'space-between',
        alignItems: 'center', marginBottom: 10,
      }}>
        <span style={{ color: TEXT2, ...MONO, fontWeight: 700, fontSize: 10, letterSpacing: '0.12em' }}>
          SETUP PERFORMANCE LEDGER
        </span>
        <span style={{ ...MONO, fontSize: 8, color: SLATE }}>
          {d.tagged} tagged · {d.untagged} untagged · {d.total_closed} closed
        </span>
      </div>

      {/* ── Global exit-shape overview (all closed trades) ── */}
      {gTotal > 0 && (
        <div style={{
          background: 'rgba(255,255,255,0.025)', border: '1px solid rgba(255,255,255,0.07)',
          borderRadius: 10, padding: '10px 14px', marginBottom: 10,
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 5, flexWrap: 'wrap' }}>
            <span style={{ ...MONO, fontSize: 8, color: DIM, textTransform: 'uppercase', letterSpacing: 0.5 }}>
              exit shape · all {gTotal} trades
            </span>
            <span style={{
              ...MONO, fontSize: 8, fontWeight: 700,
              color: shapeQualityColor(d.global_shape_quality),
              background: `${shapeQualityColor(d.global_shape_quality)}18`,
              padding: '1px 5px', borderRadius: 3,
            }}>
              {d.global_shape_quality === 'INSUFFICIENT_DATA' ? 'THIN' : d.global_shape_quality.replace('_', ' ')}
            </span>
          </div>
          <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'center' }}>
            {gs.TP_CLEAN    > 0 && (
              <span style={{ ...MONO, fontSize: 9 }}>
                <span style={{ color: '#00d48a', fontWeight: 700 }}>TP_CLEAN</span>
                <span style={{ color: 'var(--chrome)' }}> ×{gs.TP_CLEAN} ({Math.round(gs.TP_CLEAN/gTotal*100)}%)</span>
              </span>
            )}
            {gs.SL_STANDARD > 0 && (
              <span style={{ ...MONO, fontSize: 9 }}>
                <span style={{ color: 'var(--chrome)', fontWeight: 700 }}>SL_STD</span>
                <span style={{ color: 'var(--chrome)' }}> ×{gs.SL_STANDARD} ({Math.round(gs.SL_STANDARD/gTotal*100)}%)</span>
              </span>
            )}
            {gs.SL_SLIPPAGE > 0 && (
              <span style={{ ...MONO, fontSize: 9 }}>
                <span style={{ color: '#f59e0b', fontWeight: 700 }}>SL_SLIP</span>
                <span style={{ color: 'var(--chrome)' }}> ×{gs.SL_SLIPPAGE} ({Math.round(gs.SL_SLIPPAGE/gTotal*100)}%)</span>
              </span>
            )}
            {gs.RUG         > 0 && (
              <span style={{ ...MONO, fontSize: 9 }}>
                <span style={{ color: '#ef4444', fontWeight: 700 }}>RUG</span>
                <span style={{ color: 'var(--chrome)' }}> ×{gs.RUG} ({Math.round(gs.RUG/gTotal*100)}%)</span>
              </span>
            )}
            {gs.MANUAL      > 0 && (
              <span style={{ ...MONO, fontSize: 9 }}>
                <span style={{ color: 'var(--recessed)', fontWeight: 700 }}>MANUAL</span>
                <span style={{ color: 'var(--chrome)' }}> ×{gs.MANUAL}</span>
              </span>
            )}
          </div>
          {(d.global_slippage_rate !== null || d.global_rug_rate !== null) && (
            <div style={{ marginTop: 5, display: 'flex', gap: 12 }}>
              {d.global_slippage_rate !== null && (
                <span style={{ ...MONO, fontSize: 8, color: d.global_slippage_rate >= 0.4 ? '#f59e0b' : '#4d6070' }}>
                  SL slippage rate: {Math.round(d.global_slippage_rate * 100)}%
                  {d.global_slippage_rate >= 0.4 && ' ⚠ high'}
                </span>
              )}
              {d.global_rug_rate !== null && (
                <span style={{ ...MONO, fontSize: 8, color: d.global_rug_rate >= 0.25 ? '#ef4444' : '#4d6070' }}>
                  rug rate: {Math.round(d.global_rug_rate * 100)}%
                  {d.global_rug_rate >= 0.25 && ' ⚠ high'}
                </span>
              )}
            </div>
          )}
          <div style={{ marginTop: 4, ...MONO, fontSize: 8, color: 'var(--deep)' }}>
            pre-Patch 272 trades included · per-setup breakdown appears below as lifecycle-tagged trades close
          </div>
        </div>
      )}

      {/* Empty state — no lifecycle-tagged trades yet */}
      {!hasSetups && (
        <div style={{ ...MONO, fontSize: 9, color: 'var(--chrome)', lineHeight: 1.7 }}>
          No lifecycle-tagged closed trades yet.
          {d.untagged > 0 && (
            <span style={{ marginLeft: 4 }}>
              {d.untagged} {d.untagged === 1 ? 'trade was' : 'trades were'} executed before
              lifecycle context capture — not available for per-setup learning.
            </span>
          )}
          <div style={{ marginTop: 5, fontSize: 8, color: '#2a3547' }}>
            Per-setup ledger populates after the first lifecycle-tagged trade closes.
            Exit shape attribution appears automatically per setup key.
          </div>
        </div>
      )}

      {/* Setup table */}
      {hasSetups && (
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead>
            <tr>
              {['SETUP KEY', 'TOTAL', 'LT WR', 'LT AVG', 'TRAIL WR', 'TRAIL AVG', 'EXIT SHAPE', 'STATUS'].map(h => (
                <th key={h} style={{
                  ...MONO, fontSize: 8, fontWeight: 700, color: 'var(--recessed)',
                  letterSpacing: '0.1em', padding: '4px 8px',
                  textAlign: h === 'SETUP KEY' || h === 'EXIT SHAPE' ? 'left' : 'right',
                  borderBottom: '1px solid rgba(255,255,255,0.05)',
                }}>
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {d.setups.map((row, i) => (
              <tr key={i} style={{ borderBottom: '1px solid rgba(255,255,255,0.025)' }}>
                {/* Setup key */}
                <td style={{ padding: '5px 8px' }}>
                  <span style={{ ...MONO, fontSize: 8, color: MUTED }}>{row.entry_window}</span>
                  <span style={{ color: SLATE, marginInline: 4, fontSize: 8 }}>·</span>
                  <span style={{ ...MONO, fontSize: 8, color: FUEL_COLOR[row.fuel_quality] ?? DIM }}>{row.fuel_quality}</span>
                  <span style={{ color: SLATE, marginInline: 4, fontSize: 8 }}>·</span>
                  <span style={{ ...MONO, fontSize: 8, color: DIM }}>{row.move_phase}</span>
                </td>
                <td style={{ ...MONO, fontSize: 8, color: DIM, textAlign: 'right', padding: '5px 8px' }}>
                  {row.lifetime_n}
                </td>
                <td style={{ ...MONO, fontSize: 8, color: row.lifetime_wr >= 50 ? '#00d48a' : '#f59e0b', textAlign: 'right', padding: '5px 8px' }}>
                  {row.lifetime_wr.toFixed(0)}%
                </td>
                <td style={{ ...MONO, fontSize: 8, color: pnlColor(row.lifetime_avg_pnl), textAlign: 'right', padding: '5px 8px' }}>
                  {row.lifetime_avg_pnl >= 0 ? '+' : ''}{row.lifetime_avg_pnl.toFixed(1)}%
                </td>
                <td style={{
                  ...MONO, fontSize: 8, fontWeight: 700, textAlign: 'right', padding: '5px 8px',
                  color: row.trailing_n < 5 ? SLATE
                    : row.trailing_wr >= 55 ? '#00d48a'
                    : row.trailing_wr < 35  ? '#ef4444'
                    : '#f59e0b',
                }}>
                  {row.trailing_n >= 5 ? `${row.trailing_wr.toFixed(0)}%` : `${row.trailing_n}tr`}
                </td>
                <td style={{ ...MONO, fontSize: 8, color: row.trailing_n >= 5 ? pnlColor(row.trailing_avg_pnl) : SLATE, textAlign: 'right', padding: '5px 8px' }}>
                  {row.trailing_n >= 5
                    ? `${row.trailing_avg_pnl >= 0 ? '+' : ''}${row.trailing_avg_pnl.toFixed(1)}%`
                    : '—'}
                </td>
                {/* Exit shape attribution */}
                <td style={{ padding: '5px 8px' }}>
                  <ExitShapeBar
                    shapes={row.exit_shapes}
                    slippage_rate={row.slippage_rate}
                    rug_rate={row.rug_rate}
                  />
                  {row.shape_quality !== 'INSUFFICIENT_DATA' && (
                    <span style={{
                      ...MONO, fontSize: 8, fontWeight: 700, marginLeft: 4,
                      color: shapeQualityColor(row.shape_quality),
                    }}>
                      {shapeQualityLabel(row.shape_quality)}
                    </span>
                  )}
                </td>
                {/* Status chip */}
                <td style={{ textAlign: 'right', padding: '5px 8px' }}>
                  <span style={{
                    ...MONO, fontSize: 8, fontWeight: 700,
                    color: statusChipColor(row.status),
                    background: `${statusChipColor(row.status)}18`,
                    border: `1px solid ${statusChipColor(row.status)}30`,
                    borderRadius: 3, padding: '2px 6px',
                    letterSpacing: '0.08em',
                  }}>
                    {statusChipLabel(row.status)}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {/* Rule key */}
      {hasSetups && (
        <div style={{ ...MONO, fontSize: 8, color: SLATE, marginTop: 8 }}>
          trailing-10 per setup · WORKING ≥55% WR · DEGRADING &gt;20pp drop · FAILING &lt;35% WR ·
          exit shape: TP=take-profit · SL=stop-loss · SLIP=SL slippage · RUG=wipeout
        </div>
      )}
    </div>
  )
}

// ── Patch 301: Intel Validation Panel ────────────────────────────────────────

function IntelValidationPanel() {
  const q = useQuery<IntelValidationData>({
    queryKey: ['intel-validation'],
    queryFn:  () => api.get('/memecoins/intel-validation').then(r => r.data),
    refetchInterval: 300_000,
    staleTime: 120_000,
  })

  if (q.isLoading) return (
    <div style={{ ...MONO, color: '#1a2535', fontSize: 9, padding: '8px 0' }}>
      loading intel validation…
    </div>
  )
  if (!q.data) return null
  const d = q.data

  function verdictColor(v: string): string {
    if (v === 'EARNING_ITS_PLACE') return '#00d48a'
    if (v === 'FALSIFIED')         return '#ef4444'
    if (v === 'ERROR')             return '#ef4444'
    return '#f59e0b'  // NOT_YET_PROVEN
  }

  function renderSection(
    label:   string,
    section: ValidationSection,
    labelKey: 'tier' | 'status',
  ) {
    const vc = verdictColor(section.verdict)
    return (
      <div style={{
        background: 'rgba(0,0,0,0.25)',
        border: '1px solid #0a1520',
        borderRadius: 6,
        padding: '8px 10px',
        display: 'flex', flexDirection: 'column', gap: 6,
      }}>
        {/* Section header */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
          <span style={{ ...MONO, fontSize: 8, color: '#4a5a70', letterSpacing: '0.1em', fontWeight: 700 }}>
            {label}
          </span>
          <span style={{
            ...MONO, fontSize: 8, fontWeight: 700, color: vc,
            padding: '1px 6px', borderRadius: 3,
            background: `${vc}15`, border: `1px solid ${vc}30`,
          }}>
            {section.verdict.replace(/_/g, ' ')}
          </span>
          {/* directional_state badge — shown when early signal exists without promotion proof */}
          {section.directional_state && (
            <span style={{
              ...MONO, fontSize: 8, fontWeight: 700, letterSpacing: '0.07em',
              color: section.directional_state.includes('POSITIVE') ? '#f59e0b' : '#64748b',
              background: section.directional_state.includes('POSITIVE') ? 'rgba(245,158,11,0.10)' : 'rgba(100,116,139,0.10)',
              border: `1px solid ${section.directional_state.includes('POSITIVE') ? 'rgba(245,158,11,0.28)' : 'rgba(100,116,139,0.25)'}`,
              borderRadius: 3, padding: '1px 5px',
            }}>
              {section.directional_state.replace(/_/g, ' ')}
            </span>
          )}
          <span style={{ ...MONO, fontSize: 8, color: 'var(--recessed)', flex: 1 }}>
            n={section.total_n}
          </span>
        </div>

        {/* Data rows */}
        {section.rows.length > 0 && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
            {/* Column headers */}
            <div style={{ display: 'grid', gridTemplateColumns: '120px 40px 52px 52px 52px', gap: 4 }}>
              {['label', 'n', 'avg', 'wr≥10%', 'wr>0%'].map(h => (
                <span key={h} style={{ ...MONO, fontSize: 8, color: 'var(--recessed)' }}>{h}</span>
              ))}
            </div>
            {section.rows.map((row, i) => {
              const lbl = (labelKey === 'tier' ? row.tier : row.status) ?? '?'
              const rowColor =
                lbl === 'PROVEN_POSITIVE' || lbl === 'APPROACHING'  || lbl === 'HIGH_TRUST'        || lbl === 'INVESTIGATE_NOW' ? '#00d48a' :
                lbl === 'PROVEN_NEGATIVE' || lbl === 'FADING'        || lbl === 'DISTRUST'          || lbl === 'DO_NOT_TOUCH'    ? '#ef4444' :
                lbl === 'TESTED_NEUTRAL'  || lbl === 'DORMANT'       || lbl === 'CONDITIONAL_TRUST' || lbl === 'MONITOR'         ? '#f59e0b' :
                lbl === 'LOW_TRUST'       || lbl === 'BLOCKED'       ? '#64748b' :
                '#374151'
              return (
                <div key={i} style={{ display: 'grid', gridTemplateColumns: '120px 40px 52px 52px 52px', gap: 4, alignItems: 'center' }}>
                  <span style={{ ...MONO, fontSize: 8, color: rowColor, fontWeight: 700 }}>
                    {lbl.replace(/_/g, ' ')}
                  </span>
                  <span style={{ ...MONO, fontSize: 8, color: 'var(--recessed)' }}>{row.n}</span>
                  <span style={{ ...MONO, fontSize: 8, color: row.avg_return != null && row.avg_return >= 0 ? '#00d48a' : '#ef4444' }}>
                    {row.avg_return != null ? `${row.avg_return >= 0 ? '+' : ''}${row.avg_return}%` : '—'}
                  </span>
                  <span style={{ ...MONO, fontSize: 8, color: row.wr_10 != null && row.wr_10 >= 45 ? '#00d48a' : '#374151' }}>
                    {row.wr_10 != null ? `${row.wr_10}%` : '—'}
                  </span>
                  <span style={{ ...MONO, fontSize: 8, color: 'var(--recessed)' }}>
                    {row.wr_0 != null ? `${row.wr_0}%` : '—'}
                  </span>
                </div>
              )
            })}
          </div>
        )}

        {/* Verdict reason */}
        <span style={{ ...MONO, fontSize: 8, color: '#4a5a70', fontStyle: 'italic' }}>
          {section.verdict_reason}
        </span>
        {section.scope_note && (
          <span style={{ ...MONO, fontSize: 8, color: 'var(--recessed)' }}>
            scope: {section.scope_note}
          </span>
        )}
      </div>
    )
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      {/* Panel header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span style={{ ...MONO, fontSize: 8, color: '#1a2535', letterSpacing: '0.14em', fontWeight: 700 }}>
          INTEL VALIDATION · PATCH 305
        </span>
        <span style={{ ...MONO, fontSize: 8, color: '#0e1a26' }}>
          4 layers measured · outcomes-only
        </span>
      </div>

      {renderSection('PERF TIER FORWARD VALIDITY', d.perf_tier_forward, 'tier')}
      {renderSection('TRANSITION DETECTOR (SCANNER PROXY)', d.transition_detector, 'status')}
      {d.trust_label_validation  && renderSection('TRUST LABEL VALIDATION', d.trust_label_validation,  'status')}
      {d.triage_state_validation && renderSection('TRIAGE STATE VALIDATION', d.triage_state_validation, 'status')}

      {/* Deferred layers */}
      {d.not_yet_validated.length > 0 && (
        <div style={{ padding: '4px 0' }}>
          <span style={{ ...MONO, fontSize: 8, color: '#0e1a26', letterSpacing: '0.08em' }}>
            NOT YET VALIDATED (require additional scan-time logging):
          </span>
          {d.not_yet_validated.map((item, i) => (
            <div key={i} style={{ ...MONO, fontSize: 8, color: '#0e1a26', paddingLeft: 8 }}>
              · {item}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function AttributionPanel() {
  const q = useQuery<AttributionData>({
    queryKey: ['memecoins-attribution'],
    queryFn:  () => api.get('/memecoins/attribution').then(r => r.data),
    refetchInterval: 300_000,
  })
  const d = q.data
  if (!d) return null

  const factors = d.factors ?? []
  if (factors.length === 0) return null

  return (
    <div style={{
      background: 'rgba(255,255,255,0.015)',
      border: '1px solid rgba(255,255,255,0.05)',
      borderRadius: 8, padding: '12px 14px',
      display: 'flex', flexDirection: 'column', gap: 8,
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <span style={{ color: 'var(--text2)', ...MONO, fontWeight: 700, fontSize: 10, letterSpacing: '0.12em' }}>
          ATTRIBUTION
        </span>
        <span style={{ color: 'var(--chrome)', fontSize: 8, ...MONO }}>
          baseline {d.baseline_wr?.toFixed(1)}% WR · n={d.n_complete}
        </span>
      </div>
      <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
        {factors.map(factor => (
          <div key={factor.name} style={{ flex: '1 1 160px', minWidth: 0 }}>
            <div style={{ color: 'var(--dim)', fontSize: 8, ...MONO, marginBottom: 5, letterSpacing: '0.1em' }}>
              {factor.label.toUpperCase()}
            </div>
            {factor.buckets.filter(b => b.verdict !== 'TOO_SMALL').map(b => (
              <div key={b.bucket} style={{ display: 'flex', justifyContent: 'space-between', fontSize: 9, ...MONO, marginBottom: 3 }}>
                <span style={{ color: '#5a7a9a', width: 40 }}>{b.bucket}</span>
                <span style={{ color: b.wr_24h >= 50 ? '#00d48a' : '#f59e0b' }}>{b.wr_24h.toFixed(0)}%</span>
                <span style={{ color: b.avg_24h >= 0 ? '#00d48a' : '#ef4444' }}>{b.avg_24h >= 0 ? '+' : ''}{b.avg_24h.toFixed(1)}%</span>
                <span style={{ color: 'var(--recessed)' }}>n={b.n}</span>
              </div>
            ))}
          </div>
        ))}
      </div>
    </div>
  )
}

// ── ZoneLabel — thin section separator for mission-control layout ──────────

function ZoneLabel({ text }: { text: string }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 14 }}>
      <span style={{
        ...MONO, fontSize: 9, color: 'var(--chrome)',
        letterSpacing: '0.18em', fontWeight: 700,
      }}>
        {text}
      </span>
      <div style={{ flex: 1, height: 1, background: 'var(--deep)' }} />
    </div>
  )
}


// ── LaneAuthorityBar ──────────────────────────────────────────────────────

function LaneAuthorityBar({ data }: { data?: V3LaneStateResponse }) {
  if (!data) return (
    <div style={{ ...MONO, fontSize: 8, color: 'var(--chrome)', padding: '8px 0' }}>
      loading lane state…
    </div>
  )
  const ls  = data.lane_state
  const ps  = data.proof_slots
  const sh  = data.system_health
  const laneC   = v3LaneColor(ls.graduation_state)
  const deployC = v3DeployColor(ls.deployment_authority)
  const passedGates = ps.slot_gates?.filter(g => g.passed).length ?? 0
  const totalGates  = ps.slot_gates?.length ?? 0
  const gatesC = passedGates === totalGates ? '#00d48a'
               : passedGates >= totalGates - 1 ? '#f59e0b'
               : '#ef4444'
  const hasHealthDetail = (
    sh.reinforcement_status !== 'healthy' ||
    sh.funnel_status !== 'healthy' ||
    sh.label_coverage_pct < 80 ||
    (passedGates < totalGates && totalGates > 0)
  )

  const dot = <span style={{ color: 'rgba(255,255,255,0.15)', margin: '0 8px' }}>·</span>

  return (
    <div style={{
      background: 'rgba(255,255,255,0.018)',
      border: '1px solid rgba(255,255,255,0.055)',
      borderRadius: 6, padding: '8px 14px',
      display: 'flex', flexDirection: 'column', gap: 5,
    }}>
      {/* Primary authority row */}
      <div style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap' }}>
        <span style={{ ...MONO, fontSize: 9, fontWeight: 700, color: laneC, letterSpacing: '0.1em' }}>
          {ls.graduation_state.replace(/_/g, ' ')}
        </span>
        {dot}
        <span style={{ ...MONO, fontSize: 8, color: deployC }}>
          deploy {ls.deployment_authority.toLowerCase()}
        </span>
        {dot}
        <span style={{ ...MONO, fontSize: 8, color: ps.slot_state === 'FULL' ? '#f59e0b' : 'rgba(255,255,255,0.45)' }}>
          slots {ps.used_slots}/{ps.total_slots}
        </span>
        {dot}
        <span style={{ ...MONO, fontSize: 8, color: 'rgba(255,255,255,0.45)' }}>
          {v3PolicyLabel(ls.active_policy_posture)}
        </span>
        {dot}
        <span style={{ ...MONO, fontSize: 8, color: sh.pipeline_status === 'FLOWING' ? '#4d6a5a' : '#f59e0b' }}>
          pipeline: {sh.pipeline_status.toLowerCase().replace(/_/g, ' ')}
        </span>
        {totalGates > 0 && passedGates < totalGates && (
          <>
            {dot}
            <span style={{ ...MONO, fontSize: 8, color: gatesC }}>
              gates {passedGates}/{totalGates}
            </span>
          </>
        )}
      </div>
      {/* Health detail row — only when something is notable */}
      {hasHealthDetail && (
        <div style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap' }}>
          <span style={{ ...MONO, fontSize: 8, color: sh.reinforcement_status === 'ACTIVE' || sh.reinforcement_status === 'STRONG_SIGNALING' ? '#4d6a5a' : '#f59e0b' }}>
            reinf: {sh.reinforcement_status.toLowerCase().replace(/_/g, ' ')}
          </span>
          {dot}
          <span style={{ ...MONO, fontSize: 8, color: sh.label_coverage_pct >= 60 ? '#4d6a5a' : '#f59e0b' }}>
            {sh.label_coverage_pct.toFixed(0)}% labeled
          </span>
          {dot}
          <span style={{ ...MONO, fontSize: 8, color: sh.funnel_status === 'FLOWING' ? '#4d6a5a' : '#f59e0b' }}>
            funnel: {sh.funnel_status.toLowerCase().replace(/_/g, ' ')}
          </span>
          {dot}
          <span style={{ ...MONO, fontSize: 8, color: 'var(--chrome)' }}>
            {sh.recent_scanner_rows} recent rows
          </span>
        </div>
      )}
    </div>
  )
}

// ── ReasonPills ───────────────────────────────────────────────────────────

function ReasonPills({ reasons, limit = 999 }: { reasons: V3Reason[]; limit?: number }) {
  return (
    <>
      {reasons.slice(0, limit).map(r => (
        <span key={r.key} style={{ ...MONO, fontSize: 7, color: v3SeverityColor(r.severity) }}>
          {r.label} [{r.severity}]
        </span>
      ))}
    </>
  )
}

function TextPills({
  items,
  color = '#4d6070',
  border = 'rgba(255,255,255,0.08)',
  background = 'rgba(255,255,255,0.025)',
}: {
  items: string[]
  color?: string
  border?: string
  background?: string
}) {
  const uniq = Array.from(new Set(items.map(item => String(item || '').trim()).filter(Boolean)))
  if (uniq.length === 0) return null
  return (
    <>
      {uniq.map(item => (
        <span
          key={item}
          style={{
            ...MONO,
            fontSize: 7,
            color,
            background,
            border: `1px solid ${border}`,
            borderRadius: 3,
            padding: '1px 5px',
          }}
        >
          {item}
        </span>
      ))}
    </>
  )
}

// ── CaseRow — labeled section row in case file ────────────────────────────

function CaseRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={{ display: 'flex', gap: 8, alignItems: 'flex-start' }}>
      <span style={{
        ...MONO, fontSize: 7, color: 'var(--chrome)', letterSpacing: '0.10em',
        minWidth: 56, flexShrink: 0, paddingTop: 1,
      }}>
        {label}
      </span>
      <div style={{
        ...MONO, fontSize: 8, color: 'rgba(255,255,255,0.65)',
        display: 'flex', flexWrap: 'wrap', gap: '3px 10px', lineHeight: 1.5,
      }}>
        {children}
      </div>
    </div>
  )
}

// ── CandidateExpanded — inline case file ──────────────────────────────────

function CandidateExpanded({ c }: { c: V3Candidate }) {
  const f  = c.freshness
  const sa = c.safety
  const ri = c.reinforcement
  const rd = ri.debug
  const whaleBreakdown = rd?.breakdown?.whale
  const pr = c.proof
  const po = c.policy
  const lb = c.labels
  const sc = c.scores
  const dot = <span style={{ color: 'rgba(255,255,255,0.15)' }}>·</span>

  return (
    <div style={{
      background: 'rgba(255,255,255,0.012)',
      border: '1px solid rgba(255,255,255,0.06)',
      borderRadius: 5, padding: '10px 12px',
      display: 'flex', flexDirection: 'column', gap: 7,
      marginTop: 4,
    }}>
      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 2 }}>
        <span style={{ ...MONO, fontSize: 7, color: 'var(--chrome)', letterSpacing: '0.14em', fontWeight: 700 }}>
          CASE FILE
        </span>
        <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
          <span style={{ ...MONO, fontSize: 7, color: v3RouteColor(pr.route) }}>{pr.route}</span>
          {dot}
          <span style={{ ...MONO, fontSize: 7, color: v3StageColor(pr.stage) }}>
            {V3_STAGE_LABELS[pr.stage] ?? pr.stage}
          </span>
        </div>
      </div>

      {/* FRESHNESS */}
      <CaseRow label="FRESHNESS">
        <span style={{ color: 'rgba(255,255,255,0.75)' }}>
          {f.input_source.toLowerCase().replace(/_/g, ' ')}
        </span>
        {dot}
        <span>age {f.age_minutes}m</span>
        {dot}
        <span>{f.cache_lineage}</span>
        {dot}
        <span style={{ color: f.freshness_score >= 80 ? '#00d48a' : f.freshness_score >= 50 ? '#f59e0b' : '#ef4444' }}>
          score {f.freshness_score}
        </span>
        {f.drought_reason && <span style={{ color: '#4d6070' }}>{f.drought_reason}</span>}
      </CaseRow>

      {/* SOURCE */}
      {(c.source === 'DISCOVERY' || c.discovery_stage || c.promotion_state) && (
        <CaseRow label="SOURCE">
          <span style={{ color: c.source === 'DISCOVERY' ? '#60a5fa' : 'rgba(255,255,255,0.75)', fontWeight: 700 }}>
            {(c.source || 'SCANNER').toLowerCase()}
          </span>
          {c.promotion_state && (
            <>
              {dot}
              <span style={{ color: '#4d6a8a' }}>{c.promotion_state.toLowerCase().replace(/_/g, ' ')}</span>
            </>
          )}
          {c.discovery_stage && (
            <>
              {dot}
              <span style={{ color: '#4d6070' }}>{c.discovery_stage.toLowerCase().replace(/_/g, ' ')}</span>
            </>
          )}
          {c.discovery_rank_score !== null && c.discovery_rank_score !== undefined && (
            <>
              {dot}
              <span style={{ color: '#60a5fa' }}>rank {c.discovery_rank_score.toFixed(1)}</span>
            </>
          )}
          {c.discovery_flags && c.discovery_flags.length > 0 && (
            <div style={{ width: '100%', display: 'flex', flexWrap: 'wrap', gap: '3px 8px', marginTop: 2 }}>
              <span style={{ color: 'var(--chrome)' }}>flags:</span>
              <TextPills items={c.discovery_flags.map(flag => flag.toLowerCase().replace(/_/g, ' '))} color="#60a5fa" border="rgba(96,165,250,0.18)" background="rgba(96,165,250,0.06)" />
            </div>
          )}
        </CaseRow>
      )}

      {/* SAFETY */}
      <CaseRow label="SAFETY">
        <span style={{ color: sa.rug_label === 'GOOD' ? '#00d48a' : sa.rug_label === 'CAUTION' ? '#f59e0b' : '#ef4444' }}>
          {sa.rug_label}
        </span>
        <span>({sa.safety_confidence}%)</span>
        {dot}
        <span>holder quality {sa.holder_quality_level.toLowerCase()}</span>
        {sa.lp_locked_pct !== null && (
          <><span>{dot}</span><span>LP {sa.lp_locked_pct.toFixed(0)}%</span></>
        )}
        {sa.mint_revoked && (
          <><span>{dot}</span><span style={{ color: '#00d48a' }}>mint revoked</span></>
        )}
        {sa.freeze_revoked && (
          <><span>{dot}</span><span style={{ color: '#00d48a' }}>freeze revoked</span></>
        )}
        {sa.telemetry_missing && (
          <><span>{dot}</span><span style={{ color: '#f59e0b' }}>telemetry missing</span></>
        )}
      </CaseRow>

      {/* TRADE QUALITY */}
      <CaseRow label="TRADE QUALITY">
        <span style={{ color: tradeQualityColor(c.trade_quality.verdict), fontWeight: 700 }}>
          {tradeQualityLabel(c.trade_quality.verdict)}
        </span>
        {sc.market !== null && sc.market !== undefined && (
          <>
            {dot}
            <span style={{ color: scoreColor(sc.market) }}>score {sc.market.toFixed(0)}</span>
          </>
        )}
        {c.trade_quality.reasons.length > 0 && (
          <div style={{ width: '100%', display: 'flex', flexWrap: 'wrap', gap: '3px 10px', marginTop: 2 }}>
            <span style={{ color: 'var(--chrome)' }}>why:</span>
            <ReasonPills reasons={c.trade_quality.reasons} />
          </div>
        )}
      </CaseRow>

      {/* PROFIT ROOM */}
      {c.profit_room && (() => {
        const pr = c.profit_room!
        const prC = v3ProfitRoomColor(pr.label)
        return (
          <CaseRow label="PROFIT ROOM">
            <span style={{ color: prC, fontWeight: 700 }}>
              {pr.label.toLowerCase().replace('_', ' ')}
            </span>
            {sc.profit_room !== null && sc.profit_room !== undefined && (
              <><span>{dot}</span><span style={{ color: prC }}>score {sc.profit_room.toFixed(0)}</span></>
            )}
            {pr.reasons.length > 0 && (
              <div style={{ width: '100%', display: 'flex', flexWrap: 'wrap', gap: '3px 8px', marginTop: 2 }}>
                {pr.reasons.slice(0, 3).map((r, i) => (
                  <span key={i} style={{ ...MONO, fontSize: 7, color: 'rgba(255,255,255,0.45)' }}>
                    {i > 0 && <span style={{ color: 'rgba(255,255,255,0.12)', marginRight: 4 }}>·</span>}
                    {r}
                  </span>
                ))}
              </div>
            )}
          </CaseRow>
        )
      })()}

      {/* REINF */}
      <CaseRow label="REINF">
        <span style={{ color: v3ReinfColor(ri.level), fontWeight: 700 }}>{ri.level}</span>
        {dot}
        <span>{ri.support_kind.toLowerCase().replace(/_/g, ' ')}</span>
        {ri.recency_minutes !== null && (
          <><span>{dot}</span><span>recency {ri.recency_minutes}m</span></>
        )}
        {dot}
        <span>score {ri.score.toFixed(2)}</span>
        {rd?.next_level && (
          <>
            {dot}
            <span style={{ color: '#4d6a8a' }}>
              next {rd.next_level.toLowerCase()} in {rd.points_to_next.toFixed(1)}
            </span>
          </>
        )}
        {rd?.thresholds && (
          <div style={{ width: '100%', display: 'flex', flexWrap: 'wrap', gap: '3px 10px', marginTop: 2 }}>
            <span style={{ color: 'var(--chrome)' }}>levels:</span>
            <span style={{ color: 'rgba(255,255,255,0.45)' }}>light {rd.thresholds.light_min.toFixed(1)}</span>
            <span style={{ color: 'rgba(255,255,255,0.45)' }}>moderate {rd.thresholds.moderate_min.toFixed(1)}</span>
            <span style={{ color: 'rgba(255,255,255,0.45)' }}>strong {rd.thresholds.strong_min.toFixed(1)}</span>
          </div>
        )}
        {whaleBreakdown && (
          <div style={{ width: '100%', display: 'flex', flexWrap: 'wrap', gap: '3px 10px', marginTop: 2 }}>
            <span style={{ color: 'var(--chrome)' }}>support:</span>
            <span style={{ color: whaleBreakdown.exact_overlap ? '#00d48a' : '#4d6070' }}>
              exact {Number(whaleBreakdown.exact_overlap_count ?? 0)}
            </span>
            <span style={{ color: Number(whaleBreakdown.symbol_family_count ?? 0) > 0 ? '#60a5fa' : '#4d6070' }}>
              family {Number(whaleBreakdown.symbol_family_count ?? 0)}
            </span>
            <span style={{ color: Number(whaleBreakdown.meaningful_arkham_count ?? 0) > 0 ? '#60a5fa' : '#4d6070' }}>
              arkham {Number(whaleBreakdown.meaningful_arkham_count ?? 0)}
            </span>
          </div>
        )}
        {(ri.support_signals && ri.support_signals.length > 0) && (
          <div style={{ width: '100%', display: 'flex', flexWrap: 'wrap', gap: '3px 10px', marginTop: 2 }}>
            <span style={{ color: 'var(--chrome)' }}>signals:</span>
            <ReasonPills reasons={ri.support_signals} />
          </div>
        )}
        {ri.reasons.length > 0 && (
          <div style={{ width: '100%', display: 'flex', flexWrap: 'wrap', gap: '3px 10px', marginTop: 2 }}>
            <span style={{ color: 'var(--chrome)' }}>→</span>
            <ReasonPills reasons={ri.reasons} />
          </div>
        )}
        {rd?.missing_reasons && rd.missing_reasons.length > 0 && (
          <div style={{ width: '100%', display: 'flex', flexWrap: 'wrap', gap: '3px 8px', marginTop: 2 }}>
            <span style={{ color: 'var(--chrome)' }}>needs:</span>
            <TextPills items={rd.missing_reasons} color="#f59e0b" border="rgba(245,158,11,0.18)" background="rgba(245,158,11,0.06)" />
          </div>
        )}
      </CaseRow>

      {/* SCORES */}
      <CaseRow label="SCORES">
        {pr.readiness_score !== null && pr.readiness_score !== undefined && (
          <span style={{ color: scoreColor(pr.readiness_score), fontWeight: 700 }}>
            readiness {pr.readiness_score.toFixed(0)}
          </span>
        )}
        {sc.timing !== null && sc.timing !== undefined && (
          <><span>{dot}</span><span style={{ color: scoreColor(sc.timing) }}>timing {sc.timing.toFixed(0)}</span></>
        )}
        {sc.market !== null && sc.market !== undefined && (
          <><span>{dot}</span><span style={{ color: scoreColor(sc.market) }}>market {sc.market.toFixed(0)}</span></>
        )}
        {ri.support_score !== null && ri.support_score !== undefined && (
          <><span>{dot}</span><span style={{ color: scoreColor(ri.support_score) }}>support {ri.support_score.toFixed(0)}</span></>
        )}
        {sc.safety !== null && sc.safety !== undefined && (
          <><span>{dot}</span><span style={{ color: scoreColor(sc.safety) }}>safety {sc.safety.toFixed(0)}</span></>
        )}
      </CaseRow>

      {/* READINESS */}
      <CaseRow label="READINESS">
        <span style={{ color: v3RouteColor(pr.route) }}>{pr.route.toLowerCase()} route</span>
        {dot}
        <span>confidence {pr.confidence}</span>
        {pr.readiness_level && (
          <><span>{dot}</span><span style={{ color: scoreColor(pr.readiness_score ?? 0) }}>{pr.readiness_level.toLowerCase()}</span></>
        )}
        {pr.readiness_reasons && pr.readiness_reasons.length > 0 && (
          <div style={{ width: '100%', display: 'flex', flexWrap: 'wrap', gap: '3px 10px', marginTop: 2 }}>
            <span style={{ color: 'var(--chrome)' }}>drivers:</span>
            <ReasonPills reasons={pr.readiness_reasons} />
          </div>
        )}
        {pr.hard_blockers.length > 0 && (
          <div style={{ width: '100%', display: 'flex', flexWrap: 'wrap', gap: '3px 10px', marginTop: 2 }}>
            <span style={{ color: 'var(--chrome)' }}>hard:</span>
            <ReasonPills reasons={pr.hard_blockers} />
          </div>
        )}
        {pr.soft_penalties.length > 0 && (
          <div style={{ width: '100%', display: 'flex', flexWrap: 'wrap', gap: '3px 10px', marginTop: 2 }}>
            <span style={{ color: 'var(--chrome)' }}>soft:</span>
            <ReasonPills reasons={pr.soft_penalties} />
          </div>
        )}
        {pr.provisional_first_leg && pr.provisional_reason && (
          <div style={{ width: '100%', marginTop: 2 }}>
            <span style={{ ...MONO, fontSize: 7, color: '#7a9a8a' }}>
              provisional: {pr.provisional_reason}
            </span>
          </div>
        )}
        {pr.proof_reason && (
          <div style={{ width: '100%', marginTop: 2 }}>
            <span style={{ ...MONO, fontSize: 7, color: '#4d5f6a' }}>decision note: {pr.proof_reason}</span>
          </div>
        )}
        {pr.score !== null && (
          <div style={{ width: '100%', marginTop: 2 }}>
            <span style={{ ...MONO, fontSize: 7, color: '#4d6070' }}>legacy proof score {pr.score}</span>
          </div>
        )}
      </CaseRow>

      {/* POLICY — unlock_next is candidate-specific: show first */}
      <CaseRow label="POLICY">
        {po.unlock_next.length > 0
          ? <ReasonPills reasons={po.unlock_next} />
          : <span style={{ color: '#00d48a' }}>no blockers</span>
        }
        <div style={{ width: '100%', display: 'flex', gap: '3px 10px', marginTop: 2 }}>
          <span style={{ color: 'rgba(255,255,255,0.35)' }}>slots {po.used_slots}/{po.recommended_slots}</span>
          {dot}
          <span style={{ color: po.available_slots > 0 ? '#00d48a' : '#4d6070' }}>
            {po.available_slots} available
          </span>
          {dot}
          <span style={{ color: 'rgba(255,255,255,0.35)' }}>{v3PolicyLabel(po.slot_state)}</span>
        </div>
      </CaseRow>

      {/* LABELS */}
      {(lb.trust_label || lb.triage_state) && (
        <CaseRow label="LABELS">
          {lb.trust_label && (
            <span style={{ color: 'rgba(255,255,255,0.75)', fontWeight: 700 }}>{lb.trust_label}</span>
          )}
          {lb.triage_state && (
            <><span>{dot}</span><span>{lb.triage_state}</span></>
          )}
          {lb.label_confidence !== null && (
            <><span>{dot}</span><span>{Math.round(lb.label_confidence * 100)}% conf</span></>
          )}
          {lb.labeled_at && (
            <><span>{dot}</span><span style={{ color: 'var(--chrome)' }}>labeled {fmtRelTime(lb.labeled_at)}</span></>
          )}
        </CaseRow>
      )}
    </div>
  )
}

// ── CandidateRow ──────────────────────────────────────────────────────────

// ── CandidateDetailPanel — lazy-loaded history + proof_review on expand ──────

function CandidateDetailPanel({ mint }: { mint: string }) {
  const q = useQuery<V3CandidateDetailResponse>({
    queryKey: ['v3-candidate-detail', mint],
    queryFn:  () => api.get(`/v3/memecoins/candidate/${encodeURIComponent(mint)}`).then(r => r.data),
    staleTime: 60_000,
  })

  if (q.isLoading) return (
    <div style={{ ...MONO, fontSize: 7, color: 'var(--chrome)', padding: '5px 12px' }}>loading detail…</div>
  )
  if (!q.data) return null

  const d   = q.data
  const dot = <span style={{ color: 'rgba(255,255,255,0.15)' }}>·</span>

  const returnColor = (v: number | null) =>
    v === null ? 'var(--chrome)' : v >= 0 ? '#00d48a' : '#ef4444'

  return (
    <div style={{
      display: 'flex', flexDirection: 'column', gap: 8,
      padding: '8px 12px',
      borderTop: '1px solid rgba(255,255,255,0.04)',
    }}>
      {/* Open trade */}
      {d.proof_review.open_trade && (() => {
        const t = d.proof_review.open_trade!
        const psC = t.proof_status === 'CONFIRMED' ? '#00d48a'
                  : t.proof_status === 'PENDING'   ? '#60a5fa' : '#4d6070'
        return (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
            <span style={{ ...MONO, fontSize: 7, color: 'var(--chrome)', letterSpacing: '0.10em' }}>OPEN TRADE</span>
            <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', ...MONO, fontSize: 8 }}>
              <span style={{ color: psC, fontWeight: 700 }}>{t.proof_status}</span>
              {dot}
              <span style={{ color: 'rgba(255,255,255,0.55)' }}>${t.amount_usd.toFixed(0)} deployed</span>
              {t.age_hours !== null && <><span>{dot}</span><span style={{ color: 'var(--chrome)' }}>{t.age_hours.toFixed(1)}h open</span></>}
              {t.source_return_4h_pct !== null && <><span>{dot}</span><span style={{ color: returnColor(t.source_return_4h_pct) }}>4h {t.source_return_4h_pct >= 0 ? '+' : ''}{t.source_return_4h_pct.toFixed(1)}%</span></>}
              {t.source_return_24h_pct !== null && <><span>{dot}</span><span style={{ color: returnColor(t.source_return_24h_pct) }}>24h {t.source_return_24h_pct >= 0 ? '+' : ''}{t.source_return_24h_pct.toFixed(1)}%</span></>}
              {t.readiness_score != null && <><span>{dot}</span><span style={{ color: scoreColor(t.readiness_score) }}>ready {t.readiness_score.toFixed(0)}</span></>}
              {dot}
              <span style={{ color: '#4d5f6a' }}>decision note: {t.proof_reason}</span>
              {t.support_signals && t.support_signals.length > 0 && (
                <ReasonPills reasons={t.support_signals} />
              )}
            </div>
          </div>
        )
      })()}

      {/* Recent outcomes for this mint */}
      {d.proof_review.recent_outcomes.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
          <span style={{ ...MONO, fontSize: 7, color: 'var(--chrome)', letterSpacing: '0.10em' }}>
            OUTCOMES ({d.proof_review.recent_outcomes.length})
          </span>
          {d.proof_review.recent_outcomes.map(o => (
            <div key={o.id} style={{ display: 'flex', gap: 10, flexWrap: 'wrap', ...MONO, fontSize: 8 }}>
              <span style={{ color: v3RouteColor(o.route) }}>{o.route}</span>
              {dot}
              <span style={{ color: returnColor(o.return_4h_pct) }}>
                4h {o.return_4h_pct !== null ? `${o.return_4h_pct >= 0 ? '+' : ''}${o.return_4h_pct.toFixed(1)}%` : '—'}
              </span>
              {dot}
              <span style={{ color: returnColor(o.return_24h_pct) }}>
                24h {o.return_24h_pct !== null ? `${o.return_24h_pct >= 0 ? '+' : ''}${o.return_24h_pct.toFixed(1)}%` : '—'}
              </span>
              {o.readiness_score != null && <span style={{ color: scoreColor(o.readiness_score) }}>ready {o.readiness_score.toFixed(0)}</span>}
              {o.proof_score !== null && <span style={{ color: '#4d6070' }}>legacy ps {o.proof_score}</span>}
              {o.support_signals && o.support_signals.length > 0
                ? <ReasonPills reasons={o.support_signals} />
                : o.has_support && <span style={{ color: '#00d48a' }}>reinforced</span>
              }
              {o.proof_reason && <><span>{dot}</span><span style={{ color: '#4d5f6a' }}>{o.proof_reason}</span></>}
            </div>
          ))}
        </div>
      )}

      {/* Scanner history */}
      {d.history.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
          <span style={{ ...MONO, fontSize: 7, color: 'var(--chrome)', letterSpacing: '0.10em' }}>
            HISTORY ({d.history.length} scans)
          </span>
          {d.history.map(h => (
            <div key={h.id} style={{ display: 'flex', gap: 8, flexWrap: 'wrap', ...MONO, fontSize: 7, color: 'rgba(255,255,255,0.45)' }}>
              <span style={{ minWidth: 64, color: 'var(--chrome)' }}>{fmtRelTime(h.scanned_at)}</span>
              <span>{h.scanner_regime.toLowerCase().replace(/_/g, ' ')}</span>
              {h.score !== null && <span style={{ color: 'rgba(255,255,255,0.55)' }}>sc {h.score}</span>}
              {h.readiness_score != null && <span style={{ color: scoreColor(h.readiness_score) }}>ready {h.readiness_score.toFixed(0)}</span>}
              {h.proof_score !== null && <span style={{ color: '#4d6070' }}>legacy ps {h.proof_score}</span>}
              {h.return_4h_pct !== null && <span style={{ color: returnColor(h.return_4h_pct) }}>4h {h.return_4h_pct >= 0 ? '+' : ''}{h.return_4h_pct.toFixed(1)}%</span>}
              {h.trust_label && <span style={{ color: '#4d6a8a' }}>{h.trust_label}</span>}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function CandidateRow({ c, expanded, onToggle }: {
  c: V3Candidate
  expanded: boolean
  onToggle: () => void
}) {
  const isComplete = c.row_status === 'COMPLETE'
  const stageC     = v3StageColor(c.proof.stage)
  const routeC     = v3RouteColor(c.proof.route)
  const primaryBlocker = c.proof.hard_blockers[0]?.key ?? null

  function operatorRead(): { label: string; color: string } {
    if (c.proof.stage === 'PROOF_READY') {
      return { label: 'closest to trade-ready', color: '#00d48a' }
    }
    if (c.proof.stage === 'IN_PROOF_TRADE') {
      return { label: 'active trade', color: '#06b6d4' }
    }
    if (
      primaryBlocker === 'rug_warning' ||
      primaryBlocker === 'rug_warn_review' ||
      primaryBlocker === 'score_above_hard_ceiling'
    ) {
      return { label: 'unsafe structure', color: '#f59e0b' }
    }
    if (
      primaryBlocker === 'no_reinforcement' ||
      primaryBlocker === 'relaxed_needs_exact_reinforcement'
    ) {
      return { label: 'needs reinforcement', color: '#60a5fa' }
    }
    if (
      primaryBlocker === 'first_leg_unconfirmed' ||
      primaryBlocker === 'buy_pressure_low'
    ) {
      return { label: 'needs confirmation', color: '#60a5fa' }
    }
    if (primaryBlocker === 'deployment_blocked') {
      return { label: 'waiting on lane', color: '#4d6070' }
    }
    if (c.proof.stage === 'REINFORCED_PENDING') {
      return { label: 'building toward trade-ready', color: '#60a5fa' }
    }
    if (c.proof.stage === 'SCANNER_PENDING') {
      return { label: 'early watch', color: '#4d6070' }
    }
    return { label: 'recent complete', color: '#4d6070' }
  }

  const read = operatorRead()

  const leftBorder = (c.proof.stage === 'PROOF_READY' || c.proof.stage === 'IN_PROOF_TRADE')
    ? `3px solid ${stageC}60`
    : '3px solid transparent'

  return (
    <div style={{ borderLeft: leftBorder }}>
      <div
        onClick={onToggle}
        style={{
          display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap',
          padding: '6px 10px', cursor: 'pointer',
          background: isComplete ? 'rgba(255,255,255,0.008)' : `${stageC}05`,
          border: isComplete ? '1px solid rgba(255,255,255,0.05)' : `1px solid ${stageC}15`,
          borderRadius: 4,
        }}
      >
        {/* Symbol */}
        <span style={{
          ...MONO, fontSize: 11, fontWeight: 700, minWidth: 72,
          color: isComplete ? 'rgba(255,255,255,0.38)' : 'rgba(255,255,255,0.9)',
        }}>
          {c.symbol}
        </span>

        {/* Route pill */}
        <span style={{
          ...MONO, fontSize: 7, color: routeC,
          background: `${routeC}12`, border: `1px solid ${routeC}28`,
          borderRadius: 3, padding: '1px 5px', letterSpacing: '0.06em', flexShrink: 0,
        }}>
          {c.proof.route}
        </span>

        {c.source === 'DISCOVERY' && (
          <span style={{
            ...MONO, fontSize: 7, color: '#60a5fa',
            background: 'rgba(96,165,250,0.08)', border: '1px solid rgba(96,165,250,0.22)',
            borderRadius: 3, padding: '1px 5px', letterSpacing: '0.06em', flexShrink: 0,
          }}>
            DISCOVERY
          </span>
        )}

        {/* Stage pill */}
        <span style={{
          ...MONO, fontSize: 7, color: stageC,
          background: `${stageC}12`, border: `1px solid ${stageC}28`,
          borderRadius: 3, padding: '1px 5px', letterSpacing: '0.06em', flexShrink: 0,
        }}>
          {V3_STAGE_LABELS[c.proof.stage] ?? c.proof.stage}
        </span>

        <span style={{
          ...MONO, fontSize: 7, color: read.color,
          background: `${read.color}12`,
          border: `1px solid ${read.color}24`,
          borderRadius: 3,
          padding: '1px 5px',
          letterSpacing: '0.06em',
          flexShrink: 0,
        }}>
          {read.label}
        </span>

        {/* Recent complete label */}
        {isComplete && (
          <span style={{
            ...MONO, fontSize: 7, color: '#4d6070',
            background: 'rgba(255,255,255,0.025)', border: '1px solid rgba(255,255,255,0.08)',
            borderRadius: 3, padding: '1px 5px', flexShrink: 0,
          }}>
            recent complete
          </span>
        )}

        {/* Provisional pill */}
        {c.proof.provisional_first_leg && (
          <span style={{
            ...MONO, fontSize: 7, color: '#7a9a8a',
            background: 'rgba(100,160,130,0.07)', border: '1px solid rgba(100,160,130,0.18)',
            borderRadius: 3, padding: '1px 5px', flexShrink: 0,
          }}>
            PROVISIONAL
          </span>
        )}

        {/* Reinforcement level (non-NONE) */}
        {c.reinforcement.level !== 'NONE' && (
          <span style={{ ...MONO, fontSize: 7, color: v3ReinfColor(c.reinforcement.level) }}>
            {c.reinforcement.level}
          </span>
        )}

        {/* Scanner score */}
        <span style={{ ...MONO, fontSize: 7, color: 'rgba(255,255,255,0.35)' }}>
          sc {c.scanner_score}
        </span>

        {c.proof.readiness_score !== null && c.proof.readiness_score !== undefined && (
          <span style={{ ...MONO, fontSize: 7, color: scoreColor(c.proof.readiness_score), fontWeight: 700 }}>
            ready {c.proof.readiness_score.toFixed(0)}
          </span>
        )}

        {c.scores.timing !== null && c.scores.timing !== undefined && (
          <span style={{ ...MONO, fontSize: 7, color: scoreColor(c.scores.timing) }}>
            t {c.scores.timing.toFixed(0)}
          </span>
        )}

        {c.scores.market !== null && c.scores.market !== undefined && (
          <span style={{ ...MONO, fontSize: 7, color: scoreColor(c.scores.market) }}>
            m {c.scores.market.toFixed(0)}
          </span>
        )}

        {c.trade_quality.verdict && (
          <span style={{ ...MONO, fontSize: 7, color: tradeQualityColor(c.trade_quality.verdict) }}>
            {tradeQualityLabel(c.trade_quality.verdict)}
          </span>
        )}

        {c.reinforcement.support_score !== null && c.reinforcement.support_score !== undefined && (
          <span style={{ ...MONO, fontSize: 7, color: scoreColor(c.reinforcement.support_score) }}>
            s {c.reinforcement.support_score.toFixed(0)}
          </span>
        )}

        {c.source === 'DISCOVERY' && c.discovery_rank_score !== null && c.discovery_rank_score !== undefined && (
          <span style={{ ...MONO, fontSize: 7, color: '#60a5fa' }}>
            rank {c.discovery_rank_score.toFixed(1)}
          </span>
        )}

        {/* Profit room chip */}
        {c.profit_room && (() => {
          const prC = v3ProfitRoomColor(c.profit_room!.label)
          return (
            <span style={{
              ...MONO, fontSize: 7,
              color: prC,
              background: `${prC}12`,
              border: `1px solid ${prC}28`,
              borderRadius: 3,
              padding: '1px 5px',
              letterSpacing: '0.06em',
              flexShrink: 0,
            }}>
              room {c.profit_room!.label.toLowerCase().replace('_', ' ')}
              {c.scores.profit_room !== null && c.scores.profit_room !== undefined
                ? ` ${c.scores.profit_room.toFixed(0)}`
                : ''}
            </span>
          )
        })()}

        {/* Legacy proof score kept as secondary context during migration */}
        {c.proof.score !== null && (
          <span style={{ ...MONO, fontSize: 7, color: '#4d6070' }}>
            ps {c.proof.score}
          </span>
        )}

        {/* Primary hard blocker */}
        {c.proof.hard_blockers.length > 0 && (
          <span style={{
            ...MONO, fontSize: 7, color: '#4d5a6a',
            flexShrink: 1, minWidth: 0, overflow: 'hidden',
            textOverflow: 'ellipsis', whiteSpace: 'nowrap',
          }}>
            {c.proof.hard_blockers[0].label}
          </span>
        )}

        {/* Expand chevron */}
        <span style={{ ...MONO, fontSize: 8, color: 'var(--chrome)', marginLeft: 'auto', flexShrink: 0 }}>
          {expanded ? '▼' : '▶'}
        </span>
      </div>

      {/* Inline case file + lazy detail */}
      {expanded && (
        <>
          <CandidateExpanded c={c} />
          <CandidateDetailPanel mint={c.mint} />
        </>
      )}
    </div>
  )
}

// ── CandidateQueue ────────────────────────────────────────────────────────

// STAGE_ORDER uses UPPERCASE (matches c.proof.stage); STAGE_DICT_KEY maps to backend group dict keys (lowercase)
const STAGE_ORDER = ['SCANNER_PENDING', 'REINFORCED_PENDING', 'PROOF_READY', 'IN_PROOF_TRADE', 'COMPLETE']
const STAGE_DICT_KEY: Record<string, string> = {
  SCANNER_PENDING:    'scanner_pending',
  REINFORCED_PENDING: 'reinforced_pending',
  PROOF_READY:        'proof_ready',
  IN_PROOF_TRADE:     'in_proof_trade',
  COMPLETE:           'complete',
}

function CandidateQueue({ data, isLoading }: { data?: V3QueueResponse; isLoading: boolean }) {
  const [expandedId,    setExpandedId]    = useState<number | null>(null)
  const [selectedStage, setSelectedStage] = useState<string | null>(null)

  if (isLoading) return (
    <div style={{ ...MONO, fontSize: 8, color: 'var(--chrome)', padding: '8px 0' }}>loading queue…</div>
  )
  if (!data) return null

  const groupsDict = data.groups ?? {}
  const counts     = data.counts ?? {}

  // Build ordered stage list from STAGE_ORDER, pulling candidates from the dict
  const groups = STAGE_ORDER
    .map(stage => ({ stage, candidates: groupsDict[STAGE_DICT_KEY[stage]] ?? [] }))
    .filter(g => selectedStage ? g.stage === selectedStage : g.candidates.length > 0)

  function toggleExpand(id: number) {
    setExpandedId(prev => (prev === id ? null : id))
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      {/* Stage navigation chips */}
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
        {STAGE_ORDER.map(stage => {
          const count  = counts[STAGE_DICT_KEY[stage]] ?? 0
          const stageC = v3StageColor(stage)
          const isActive = selectedStage === stage
          const hasItems = count > 0
          return (
            <button
              key={stage}
              onClick={() => setSelectedStage(isActive ? null : stage)}
              style={{
                ...MONO, fontSize: 7, letterSpacing: '0.08em',
                cursor: 'pointer', padding: '3px 9px', borderRadius: 3,
                background: isActive ? `${stageC}20` : hasItems ? `${stageC}08` : 'transparent',
                border: isActive ? `1px solid ${stageC}60` : hasItems ? `1px solid ${stageC}30` : '1px solid rgba(255,255,255,0.08)',
                color: hasItems ? stageC : '#4d6070',
              }}
            >
              {count} {V3_STAGE_LABELS[stage] ?? stage}
            </button>
          )
        })}
        {selectedStage && (
          <button
            onClick={() => setSelectedStage(null)}
            style={{
              ...MONO, fontSize: 7, cursor: 'pointer', padding: '3px 9px', borderRadius: 3,
              background: 'transparent', border: '1px solid rgba(255,255,255,0.1)', color: 'var(--chrome)',
            }}
          >
            show all
          </button>
        )}
      </div>

      {/* Candidate groups */}
      {groups.length === 0 && (
        <div style={{ ...MONO, fontSize: 8, color: 'var(--chrome)', padding: '4px 0' }}>
          {selectedStage ? 'no candidates in this stage' : 'queue empty'}
        </div>
      )}
      {groups.map(group => (
        <div key={group.stage} style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 2 }}>
            <span style={{
              ...MONO, fontSize: 8, fontWeight: 700, letterSpacing: '0.12em',
              color: v3StageColor(group.stage),
            }}>
              {V3_STAGE_LABELS[group.stage] ?? group.stage}
            </span>
            <span style={{ ...MONO, fontSize: 7, color: 'var(--chrome)' }}>
              {group.candidates.length}
            </span>
            <div style={{ flex: 1, height: 1, background: `${v3StageColor(group.stage)}20` }} />
          </div>
          {group.candidates.map(c => (
            <CandidateRow
              key={c.id}
              c={c}
              expanded={expandedId === c.id}
              onToggle={() => toggleExpand(c.id)}
            />
          ))}
        </div>
      ))}
    </div>
  )
}

// ── SlotGateChecklist ─────────────────────────────────────────────────────

const GATE_LABELS: Record<string, string> = {
  normal_cohort_promotive:       'Normal cohort promotive',
  proof_stack_backed:            'Proof stack backed',
  reinforced_cohort_maturing:    'Reinforced cohort maturing',
  identity_clear:                'Identity clear',
  relaxed_not_driving_expansion: 'Relaxed not driving expansion',
}

function SlotGateChecklist({ gates }: { gates: Array<{ key: string; passed: boolean; note: string }> }) {
  const passed = gates.filter(g => g.passed).length
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
      <div style={{ ...MONO, fontSize: 7, color: 'var(--chrome)', letterSpacing: '0.10em', marginBottom: 2 }}>
        SLOT GATES — {passed}/{gates.length} passed
      </div>
      {gates.map(gate => (
        <div key={gate.key} style={{ display: 'flex', alignItems: 'flex-start', gap: 8 }}>
          <span style={{ ...MONO, fontSize: 8, color: gate.passed ? '#00d48a' : '#ef4444', flexShrink: 0 }}>
            {gate.passed ? '✓' : '✗'}
          </span>
          <span style={{
            ...MONO, fontSize: 8,
            color: gate.passed ? 'rgba(255,255,255,0.45)' : 'rgba(255,255,255,0.72)',
            fontWeight: gate.passed ? 400 : 600,
          }}>
            {GATE_LABELS[gate.key] ?? gate.key.replace(/_/g, ' ')}
          </span>
          {gate.note && (
            <span style={{ ...MONO, fontSize: 7, color: '#4d6070', marginLeft: 'auto', textAlign: 'right' }}>
              {gate.note}
            </span>
          )}
        </div>
      ))}
    </div>
  )
}

// ── CohortTable ───────────────────────────────────────────────────────────

function CohortTable({ cohorts }: { cohorts: V3LaneStateResponse['cohorts'] }) {
  if (!cohorts || cohorts.length === 0) return (
    <div style={{ ...MONO, fontSize: 8, color: 'var(--chrome)' }}>no cohort data</div>
  )
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
      <div style={{ display: 'flex', gap: 12, ...MONO, fontSize: 7, color: 'var(--chrome)', letterSpacing: '0.08em', marginBottom: 2 }}>
        <span style={{ minWidth: 120 }}>COHORT</span>
        <span style={{ minWidth: 32 }}>N</span>
        <span style={{ minWidth: 40 }}>WR 4H</span>
        <span style={{ minWidth: 48 }}>AVG 4H</span>
        <span style={{ minWidth: 60 }}>VERDICT</span>
        <span>TREND</span>
      </div>
      {cohorts.map((row, i) => (
        <div key={i} style={{ display: 'flex', gap: 12, ...MONO, fontSize: 8, alignItems: 'center' }}>
          <span style={{ minWidth: 120, color: 'rgba(255,255,255,0.6)' }}>{row.label ?? row.cohort_key}</span>
          <span style={{ minWidth: 32, color: 'var(--chrome)' }}>{row.sample_n}</span>
          <span style={{ minWidth: 40, color: row.win_rate_4h !== null && row.win_rate_4h >= 50 ? '#00d48a' : '#f59e0b' }}>
            {row.win_rate_4h !== null ? `${row.win_rate_4h.toFixed(0)}%` : '—'}
          </span>
          <span style={{ minWidth: 48, color: row.avg_return_4h !== null && row.avg_return_4h >= 0 ? '#00d48a' : '#ef4444' }}>
            {row.avg_return_4h !== null
              ? `${row.avg_return_4h >= 0 ? '+' : ''}${row.avg_return_4h.toFixed(1)}%`
              : '—'}
          </span>
          <span style={{ minWidth: 60, color: row.verdict === 'PROMOTE' ? '#00d48a' : row.verdict === 'TIGHTEN' ? '#ef4444' : '#f59e0b' }}>
            {row.verdict}
          </span>
          <span style={{ color: 'var(--chrome)' }}>{row.recent_trend}</span>
        </div>
      ))}
    </div>
  )
}

// ── SystemHealthRail ──────────────────────────────────────────────────────

function SystemHealthRail({ laneData }: { laneData?: V3LaneStateResponse }) {
  const [open, setOpen] = useState(false)

  const sh    = laneData?.system_health
  const gates = laneData?.proof_slots?.slot_gates ?? []
  const reinfSummary = sh?.reinforcement_summary
  const reinfThresholds = sh?.reinforcement_thresholds
  const discoverySummary = sh?.discovery_summary

  const summary = sh
    ? `pipeline: ${sh.pipeline_status}  ·  reinf: ${sh.reinforcement_status}`
    : 'loading…'

  return (
    <div style={{
      background: 'rgba(255,255,255,0.012)',
      border: '1px solid rgba(255,255,255,0.04)',
      borderRadius: 6,
    }}>
      <button
        onClick={() => setOpen(v => !v)}
        style={{
          display: 'flex', alignItems: 'center', gap: 10, width: '100%',
          background: 'none', border: 'none', cursor: 'pointer',
          padding: '8px 14px',
        }}
      >
        <span style={{ ...MONO, fontSize: 8, fontWeight: 700, color: 'var(--chrome)', letterSpacing: '0.14em' }}>
          SYSTEM HEALTH
        </span>
        <span style={{ ...MONO, fontSize: 8, color: 'var(--chrome)' }}>{summary}</span>
        <span style={{ ...MONO, fontSize: 8, color: 'var(--chrome)', marginLeft: 'auto' }}>
          {open ? '▲' : '▼'}
        </span>
      </button>

      {open && laneData && sh && (
        <div style={{ padding: '0 14px 12px', display: 'flex', flexDirection: 'column', gap: 14 }}>
          {/* Health metrics row */}
          <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
            {([
              { label: 'pipeline', value: sh.pipeline_status },
              { label: 'funnel',   value: sh.funnel_status },
              { label: 'reinf',    value: sh.reinforcement_status },
              { label: 'labeled',  value: `${sh.label_coverage_pct.toFixed(0)}%` },
              { label: 'rows',     value: String(sh.recent_scanner_rows) },
              { label: 'rug mix',  value: `good ${sh.rug_mix.good} · warn ${sh.rug_mix.warn} · unknown ${sh.rug_mix.unknown}` },
            ] as const).map(m => (
              <div key={m.label} style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                <span style={{ ...MONO, fontSize: 7, color: 'var(--chrome)', letterSpacing: '0.08em' }}>
                  {m.label.toUpperCase()}
                </span>
                <span style={{ ...MONO, fontSize: 9, color: 'rgba(255,255,255,0.6)' }}>{m.value}</span>
              </div>
            ))}
          </div>

          {/* Reinforcement diagnostics */}
          {(sh.reinforcement_detail || reinfSummary || reinfThresholds) && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              <span style={{ ...MONO, fontSize: 7, color: 'var(--chrome)', letterSpacing: '0.10em' }}>
                REINFORCEMENT DIAGNOSTICS
              </span>
              {sh.reinforcement_detail && (
                <div style={{ ...MONO, fontSize: 8, color: 'rgba(255,255,255,0.55)' }}>
                  {sh.reinforcement_detail}
                </div>
              )}
              {reinfSummary && (
                <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', ...MONO, fontSize: 8 }}>
                  <span style={{ color: 'rgba(255,255,255,0.6)' }}>
                    candidates {reinfSummary.candidate_count}
                  </span>
                  <span style={{ color: '#00d48a' }}>
                    strong {reinfSummary.level_counts.STRONG}
                  </span>
                  <span style={{ color: '#60a5fa' }}>
                    moderate {reinfSummary.level_counts.MODERATE}
                  </span>
                  <span style={{ color: '#f59e0b' }}>
                    light {reinfSummary.level_counts.LIGHT}
                  </span>
                  <span style={{ color: '#4d6070' }}>
                    none {reinfSummary.level_counts.NONE}
                  </span>
                  <span style={{ color: reinfSummary.exact_overlap_candidates > 0 ? '#00d48a' : '#ef4444' }}>
                    exact {reinfSummary.exact_overlap_candidates}
                  </span>
                  <span style={{ color: reinfSummary.symbol_family_candidates > 0 ? '#60a5fa' : '#4d6070' }}>
                    family {reinfSummary.symbol_family_candidates}
                  </span>
                  <span style={{ color: 'var(--chrome)' }}>
                    relaxed {reinfSummary.relaxed_candidates}
                  </span>
                </div>
              )}
              {reinfThresholds && (
                <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', ...MONO, fontSize: 8, color: 'rgba(255,255,255,0.45)' }}>
                  <span>light {reinfThresholds.light_min.toFixed(1)}</span>
                  <span>moderate {reinfThresholds.moderate_min.toFixed(1)}</span>
                  <span>strong {reinfThresholds.strong_min.toFixed(1)}</span>
                </div>
              )}
            </div>
          )}

          {discoverySummary && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              <span style={{ ...MONO, fontSize: 7, color: 'var(--chrome)', letterSpacing: '0.10em' }}>
                DISCOVERY PROMOTION
              </span>
              <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', ...MONO, fontSize: 8 }}>
                <span style={{ color: 'rgba(255,255,255,0.6)' }}>
                  qualified {discoverySummary.qualified_count}
                </span>
                <span style={{ color: discoverySummary.promoted_count > 0 ? '#60a5fa' : '#4d6070' }}>
                  promoted {discoverySummary.promoted_count}
                </span>
                <span style={{ color: discoverySummary.scan_best_overlap > 0 ? '#00d48a' : '#4d6070' }}>
                  scan-best overlap {discoverySummary.scan_best_overlap}
                </span>
                <span style={{ color: 'var(--chrome)' }}>
                  early {discoverySummary.lifecycle_early}
                </span>
                <span style={{ color: 'var(--chrome)' }}>
                  plus {discoverySummary.discovery_plus}
                </span>
                <span style={{ color: 'var(--chrome)' }}>
                  ingress {discoverySummary.discovery_ingress}
                </span>
              </div>
            </div>
          )}

          {/* Cohort table */}
          {laneData.cohorts && laneData.cohorts.length > 0 && (
            <CohortTable cohorts={laneData.cohorts} />
          )}

          {/* Slot gate checklist */}
          {gates.length > 0 && <SlotGateChecklist gates={gates} />}

          {/* Top constraints */}
          {laneData.top_constraints && laneData.top_constraints.length > 0 && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              <span style={{ ...MONO, fontSize: 7, color: 'var(--chrome)', letterSpacing: '0.10em' }}>
                TOP CONSTRAINTS
              </span>
              {laneData.top_constraints.map(r => (
                <div key={r.key} style={{ display: 'flex', gap: 8, ...MONO, fontSize: 8 }}>
                  <span style={{ color: v3SeverityColor(r.severity) }}>[{r.severity}]</span>
                  <span style={{ color: 'rgba(255,255,255,0.6)' }}>{r.label}</span>
                  <span style={{ color: 'var(--chrome)' }}>×{r.count}</span>
                  <span style={{ color: '#4d6070' }}>{r.detail}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ── ProofWorkspace ────────────────────────────────────────────────────────

function ProofTradeCard({
  entry,
  busyMints,
  onExit,
  onReduce,
}: {
  entry: V3ProofWorkspaceResponse['in_proof_trade'][number]
  busyMints: Set<string>
  onExit: (mint: string, symbol: string, currentReturnPct?: number | null) => void
  onReduce: (
    mint: string,
    symbol: string,
    currentReturnPct?: number | null,
    pct?: number,
    label?: string,
    reason?: string,
  ) => void
}) {
  const [open, setOpen] = useState(false)
  const t   = entry.trade
  const psC = t.proof_status === 'CONFIRMED' ? '#00d48a'
            : t.proof_status === 'PENDING'   ? '#60a5fa' : '#4d6070'
  const dot = <span style={{ color: 'rgba(255,255,255,0.15)', margin: '0 6px' }}>·</span>
  const rc  = (v: number | null) => v === null ? 'var(--chrome)' : v >= 0 ? '#00d48a' : '#ef4444'
  const review = t.exit_review
  const busy = busyMints.has(t.mint)
  const initialCapital = t.initial_amount_usd ?? t.amount_usd
  const realizedRelease = t.realized_release_usd ?? 0
  const realizedPnl = t.realized_pnl_usd ?? 0
  const remainingCapital = t.amount_usd
  const releasedPct = initialCapital > 0 ? Math.round((realizedRelease / initialCapital) * 100) : 0
  const reviewColor =
    review?.review_state === 'EXIT_NOW' ? '#ef4444'
    : review?.review_state === 'EXIT_READY' ? '#f59e0b'
    : review?.review_state === 'TAKE_PROFIT' ? '#00d48a'
    : review?.review_state === 'DE_RISK' ? '#60a5fa'
    : '#4d6070'
  const reduceLabel = review?.review_state === 'TAKE_PROFIT'
    ? `TP ${Math.round(review?.recommended_pct ?? 35)}%`
    : `DE-RISK ${Math.round(review?.recommended_pct ?? 50)}%`
  return (
    <div style={{
      border: '1px solid rgba(96,165,250,0.15)', borderRadius: 4,
      background: 'rgba(96,165,250,0.03)',
    }}>
      <div
        onClick={() => setOpen(v => !v)}
        style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', padding: '6px 10px', cursor: 'pointer' }}
      >
        <span style={{ ...MONO, fontSize: 11, fontWeight: 700, color: 'rgba(255,255,255,0.88)', minWidth: 72 }}>
          {t.symbol}
        </span>
        <span style={{ ...MONO, fontSize: 7, color: psC, background: `${psC}12`, border: `1px solid ${psC}28`, borderRadius: 3, padding: '1px 5px' }}>
          {t.proof_status}
        </span>
        {dot}
        <span style={{ ...MONO, fontSize: 7, color: 'rgba(255,255,255,0.45)' }}>rem ${remainingCapital.toFixed(0)}</span>
        {(realizedRelease > 0 || realizedPnl !== 0) && (
          <>
            {dot}
            <span style={{ ...MONO, fontSize: 7, color: realizedPnl >= 0 ? '#00d48a' : '#ef4444' }}>
              realized {realizedPnl >= 0 ? '+' : ''}${Math.abs(realizedPnl).toFixed(2)}
            </span>
          </>
        )}
        {t.age_hours !== null && <><span>{dot}</span><span style={{ ...MONO, fontSize: 7, color: 'var(--chrome)' }}>{t.age_hours.toFixed(1)}h</span></>}
        {t.source_return_4h_pct !== null && <><span>{dot}</span><span style={{ ...MONO, fontSize: 7, color: rc(t.source_return_4h_pct) }}>4h {t.source_return_4h_pct >= 0 ? '+' : ''}{t.source_return_4h_pct.toFixed(1)}%</span></>}
        {t.source_return_24h_pct !== null && <span style={{ ...MONO, fontSize: 7, color: rc(t.source_return_24h_pct) }}>24h {t.source_return_24h_pct >= 0 ? '+' : ''}{t.source_return_24h_pct.toFixed(1)}%</span>}
        {review?.current_return_pct != null && (
          <>
            {dot}
            <span style={{ ...MONO, fontSize: 7, color: rc(review.current_return_pct) }}>
              live {review.current_return_pct >= 0 ? '+' : ''}{review.current_return_pct.toFixed(1)}%
            </span>
          </>
        )}
        {review?.review_state && (
          <>
            {dot}
            <span style={{
              ...MONO, fontSize: 7, color: reviewColor,
              background: `${reviewColor}12`, border: `1px solid ${reviewColor}28`,
              borderRadius: 3, padding: '1px 5px',
            }}>
              {review.review_state_label ?? review.review_state.replace(/_/g, ' ')}
            </span>
          </>
        )}
        <span style={{ ...MONO, fontSize: 7, color: '#4d5f6a', flex: 1, textAlign: 'right' }}>{t.proof_reason}</span>
        {(review?.review_state === 'DE_RISK' || review?.review_state === 'TAKE_PROFIT') && (
          <button
            onClick={(e) => {
              e.stopPropagation()
              onReduce(
                t.mint,
                t.symbol,
                review?.current_return_pct ?? null,
                review?.recommended_pct ?? (review?.review_state === 'TAKE_PROFIT' ? 35 : 50),
                review?.review_state === 'TAKE_PROFIT' ? 'Take profit' : 'De-risk',
                review?.review_state === 'TAKE_PROFIT' ? 'MANUAL_TAKE_PROFIT' : 'MANUAL_DE_RISK',
              )
            }}
            disabled={busy}
            style={{
              background: busy ? 'rgba(96,165,250,0.03)' : 'rgba(96,165,250,0.09)',
              border: '1px solid rgba(96,165,250,0.22)',
              borderRadius: 4,
              color: busy ? 'var(--chrome)' : '#60a5fa',
              cursor: busy ? 'default' : 'pointer',
              ...MONO, fontSize: 7, padding: '3px 8px', fontWeight: 700,
            }}
          >
            {busy ? '…' : reduceLabel}
          </button>
        )}
        <button
          onClick={(e) => { e.stopPropagation(); onExit(t.mint, t.symbol, review?.current_return_pct ?? null) }}
          disabled={busy}
          style={{
            background: busy ? 'rgba(239,68,68,0.03)' : 'rgba(239,68,68,0.09)',
            border: '1px solid rgba(239,68,68,0.22)',
            borderRadius: 4,
            color: busy ? 'var(--chrome)' : '#ef4444',
            cursor: busy ? 'default' : 'pointer',
            ...MONO, fontSize: 7, padding: '3px 8px', fontWeight: 700,
          }}
        >
          {busy ? '…' : 'EXIT'}
        </button>
        <span style={{ ...MONO, fontSize: 8, color: 'var(--chrome)' }}>{open ? '▼' : '▶'}</span>
      </div>
      {open && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          {review && (
            <div style={{
              padding: '8px 10px 0',
              display: 'flex',
              flexWrap: 'wrap',
              gap: '4px 10px',
              ...MONO,
              fontSize: 7,
              color: 'rgba(255,255,255,0.55)',
            }}>
              <span style={{ color: reviewColor }}>review: {review.review_state_label ?? review.review_state.replace(/_/g, ' ')}</span>
              {review.current_readiness_score != null && (
                <span style={{ color: scoreColor(review.current_readiness_score) }}>
                  ready {review.current_readiness_score.toFixed(0)}
                  {review.current_readiness_level ? ` ${review.current_readiness_level.toLowerCase()}` : ''}
                </span>
              )}
              {review.readiness_delta != null && (
                <span style={{ color: review.readiness_delta >= 0 ? '#00d48a' : '#f59e0b' }}>
                  ready Δ {review.readiness_delta >= 0 ? '+' : ''}{review.readiness_delta.toFixed(0)}
                </span>
              )}
              {review.proof_score_delta != null && (
                <span style={{ color: review.proof_score_delta >= 0 ? '#00d48a' : '#f59e0b' }}>
                  legacy proof Δ {review.proof_score_delta >= 0 ? '+' : ''}{review.proof_score_delta.toFixed(0)}
                </span>
              )}
              {review.recommended_pct != null && review.recommended_action === 'reduce_risk' && (
                <span style={{ color: reviewColor }}>
                  suggested {review.recommended_pct.toFixed(0)}%
                </span>
              )}
              {(review.exit_reason_label || review.exit_reason) && (
                <span style={{ color: '#4d6070' }}>{review.exit_reason_label ?? review.exit_reason}</span>
              )}
            </div>
          )}
          <div style={{
            padding: '0 10px',
            display: 'flex',
            flexWrap: 'wrap',
            gap: '4px 10px',
            ...MONO,
            fontSize: 7,
            color: 'rgba(255,255,255,0.55)',
          }}>
            <span style={{ color: 'var(--chrome)', letterSpacing: '0.10em' }}>CAPITAL</span>
            <span style={{ color: 'rgba(255,255,255,0.62)' }}>total ${initialCapital.toFixed(2)}</span>
            <span style={{ color: '#60a5fa' }}>released ${realizedRelease.toFixed(2)}{initialCapital > 0 ? ` · ${releasedPct}%` : ''}</span>
            <span style={{ color: 'rgba(255,255,255,0.62)' }}>remaining ${remainingCapital.toFixed(2)}</span>
            <span style={{ color: realizedPnl >= 0 ? '#00d48a' : '#ef4444' }}>
              realized pnl {realizedPnl >= 0 ? '+' : '-'}${Math.abs(realizedPnl).toFixed(2)}
            </span>
          </div>
          {t.exit_history && t.exit_history.length > 0 && (
            <div style={{
              padding: '0 10px',
              display: 'flex',
              flexWrap: 'wrap',
              gap: '4px 8px',
              ...MONO,
              fontSize: 7,
            }}>
              <span style={{ color: 'var(--chrome)', letterSpacing: '0.10em' }}>RECENT REVIEWS</span>
              {t.exit_history.slice(0, 4).map(h => {
                const hc =
                  h.review_state === 'EXIT_NOW' ? '#ef4444'
                  : h.review_state === 'EXIT_READY' ? '#f59e0b'
                  : h.review_state === 'TAKE_PROFIT' ? '#00d48a'
                  : h.review_state === 'DE_RISK' ? '#60a5fa'
                  : '#4d6070'
                return (
                  <span key={h.id} style={{
                    color: hc,
                    background: `${hc}10`,
                    border: `1px solid ${hc}24`,
                    borderRadius: 3,
                    padding: '2px 5px',
                  }}>
                    {fmtRelTime(h.ts_utc)} · {h.review_state_label ?? h.review_state.replace(/_/g, ' ')}
                    {h.current_return_pct != null ? ` · ${h.current_return_pct >= 0 ? '+' : ''}${h.current_return_pct.toFixed(1)}%` : ''}
                    {h.recommended_pct != null ? ` · ${h.recommended_pct.toFixed(0)}%` : ''}
                    {(h.exit_reason_label || h.exit_reason) ? ` · ${h.exit_reason_label ?? h.exit_reason}` : ''}
                    {h.executed ? ' · executed' : ''}
                  </span>
                )
              })}
            </div>
          )}
          {entry.candidate && <CandidateExpanded c={entry.candidate} />}
        </div>
      )}
    </div>
  )
}

function ProofWorkspace({
  data,
  isLoading,
  busyMints,
  onExit,
  onReduce,
}: {
  data?: V3ProofWorkspaceResponse
  isLoading: boolean
  busyMints: Set<string>
  onExit: (mint: string, symbol: string, currentReturnPct?: number | null) => void
  onReduce: (
    mint: string,
    symbol: string,
    currentReturnPct?: number | null,
    pct?: number,
    label?: string,
    reason?: string,
  ) => void
}) {
  const [expandedId, setExpandedId] = useState<number | null>(null)

  const toggleExpand = (id: number) => setExpandedId(prev => (prev === id ? null : id))

  if (isLoading) return (
    <div style={{ ...MONO, fontSize: 8, color: 'var(--chrome)', padding: '8px 0' }}>loading workspace…</div>
  )
  if (!data) return null

  const c   = data.counts
  const rc  = (v: number | null) => v === null ? 'var(--chrome)' : v >= 0 ? '#00d48a' : '#ef4444'
  const dot = <span style={{ color: 'rgba(255,255,255,0.15)', margin: '0 8px' }}>·</span>

  const hasContent = c.proof_ready > 0 || c.reinforced_pending > 0 || c.in_proof_trade > 0 || c.recent_outcomes > 0
  const recentBlockers = data.recent_blocker_summary?.categories ?? []

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      {/* Stats strip */}
      <div style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 0, ...MONO, fontSize: 8 }}>
        <span style={{ color: c.proof_ready > 0 ? '#00d48a' : '#4d6070' }}>trade ready {c.proof_ready}</span>
        {dot}
        <span style={{ color: c.reinforced_pending > 0 ? '#60a5fa' : '#4d6070' }}>reinforced {c.reinforced_pending}</span>
        {dot}
        <span style={{ color: c.in_proof_trade > 0 ? '#06b6d4' : '#4d6070' }}>active trades {c.in_proof_trade}</span>
        {dot}
        <span style={{ color: 'var(--chrome)' }}>outcomes {c.recent_outcomes}</span>
        {data.current_blockers.length > 0 && (
          <>
            {dot}
            <span style={{ color: '#ef4444' }}>
              {data.current_blockers.map(b => `${b.key.replace(/_/g, ' ')} ×${b.count}`).join(' · ')}
            </span>
          </>
        )}
        {recentBlockers.length > 0 && (
          <>
            {dot}
            <span style={{ color: '#f59e0b' }}>
              recent blockers {recentBlockers.slice(0, 3).map(b => `${b.key.replace(/_/g, ' ')} ×${b.count}`).join(' · ')}
            </span>
          </>
        )}
      </div>

      {!hasContent && (
        <div style={{ ...MONO, fontSize: 8, color: 'var(--chrome)' }}>no active trade setups</div>
      )}

      {/* Active trades */}
      {data.in_proof_trade.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span style={{ ...MONO, fontSize: 8, fontWeight: 700, color: '#06b6d4', letterSpacing: '0.12em' }}>ACTIVE TRADES</span>
            <div style={{ flex: 1, height: 1, background: 'rgba(6,182,212,0.15)' }} />
          </div>
          {data.in_proof_trade.map(entry => (
            <ProofTradeCard key={entry.trade.id} entry={entry} busyMints={busyMints} onExit={onExit} onReduce={onReduce} />
          ))}
        </div>
      )}

      {/* Trade ready */}
      {data.proof_ready.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span style={{ ...MONO, fontSize: 8, fontWeight: 700, color: '#00d48a', letterSpacing: '0.12em' }}>TRADE READY</span>
            <div style={{ flex: 1, height: 1, background: 'rgba(0,212,138,0.15)' }} />
          </div>
          {data.proof_ready.map(cand => (
            <CandidateRow
              key={cand.id}
              c={cand}
              expanded={expandedId === cand.id}
              onToggle={() => toggleExpand(cand.id)}
            />
          ))}
        </div>
      )}

      {/* Reinforced pending (if any) */}
      {data.reinforced_pending.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span style={{ ...MONO, fontSize: 8, fontWeight: 700, color: '#60a5fa', letterSpacing: '0.12em' }}>REINFORCED PENDING</span>
            <div style={{ flex: 1, height: 1, background: 'rgba(96,165,250,0.15)' }} />
          </div>
          {data.reinforced_pending.map(cand => (
            <CandidateRow
              key={cand.id}
              c={cand}
              expanded={expandedId === cand.id}
              onToggle={() => toggleExpand(cand.id)}
            />
          ))}
        </div>
      )}

      {/* Recent outcomes */}
      {data.recent_outcomes.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span style={{ ...MONO, fontSize: 8, fontWeight: 700, color: '#4d6070', letterSpacing: '0.12em' }}>RECENT OUTCOMES</span>
            <div style={{ flex: 1, height: 1, background: 'rgba(255,255,255,0.05)' }} />
          </div>
          {data.recent_outcomes.map((o, i) => (
            <div key={i} style={{ display: 'flex', gap: 8, flexWrap: 'wrap', ...MONO, fontSize: 8, alignItems: 'center' }}>
              <span style={{ fontWeight: 700, color: 'rgba(255,255,255,0.7)', minWidth: 72 }}>{o.symbol}</span>
              <span style={{ color: v3RouteColor(o.route), fontSize: 7 }}>{o.route}</span>
              <span style={{ color: rc(o.return_4h_pct) }}>
                4h {o.return_4h_pct !== null ? `${o.return_4h_pct >= 0 ? '+' : ''}${o.return_4h_pct.toFixed(1)}%` : '—'}
              </span>
              <span style={{ color: rc(o.return_24h_pct) }}>
                24h {o.return_24h_pct !== null ? `${o.return_24h_pct >= 0 ? '+' : ''}${o.return_24h_pct.toFixed(1)}%` : '—'}
              </span>
              {o.readiness_score != null && <span style={{ color: scoreColor(o.readiness_score) }}>ready {o.readiness_score.toFixed(0)}</span>}
              {o.proof_score !== null && <span style={{ color: '#4d6070' }}>legacy ps {o.proof_score}</span>}
              {o.support_signals && o.support_signals.length > 0
                ? <ReasonPills reasons={o.support_signals} />
                : o.has_support && <span style={{ color: '#00d48a', fontSize: 7 }}>reinforced</span>
              }
              {o.proof_reason && <span style={{ color: '#4d5f6a' }}>{o.proof_reason}</span>}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function CalibrationStrip({ data, isLoading }: { data?: CalibrationView; isLoading: boolean }) {
  const summary = data?.summary
  const overview = data?.overview
  const insights = data?.insights ?? []
  const recentTrades = data?.recent_trades ?? []
  const timingGroups = data?.groups?.entry_timing ?? []
  const phaseGroups = data?.groups?.signal_window_phase ?? []

  const thin = !summary || (summary.calibrated_trades ?? 0) < 5
  const leadingTiming = timingGroups.find(g => (g.count ?? 0) > 0)
  const leadingPhase = phaseGroups.find(g => (g.count ?? 0) > 0)

  const Stat = ({
    label,
    value,
    tone,
  }: {
    label: string
    value: string
    tone?: string
  }) => (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
      <span style={{ ...MONO, fontSize: 8, color: 'var(--dim)', letterSpacing: '0.1em' }}>{label}</span>
      <span style={{ ...MONO, fontSize: 12, fontWeight: 700, color: tone || '#d7e1ea' }}>{value}</span>
    </div>
  )

  return (
    <section style={{
      background: 'linear-gradient(180deg, rgba(15,24,37,0.95), rgba(10,16,27,0.92))',
      border: '1px solid rgba(96,165,250,0.12)',
      borderRadius: 10,
      padding: '14px 16px',
      display: 'flex',
      flexDirection: 'column',
      gap: 14,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
        <span style={{ ...MONO, fontSize: 9, color: '#60a5fa', letterSpacing: '0.12em' }}>CALIBRATION</span>
        <span style={{
          ...MONO,
          fontSize: 8,
          color: thin ? '#f59e0b' : '#00d48a',
          background: thin ? 'rgba(245,158,11,0.08)' : 'rgba(0,212,138,0.08)',
          border: `1px solid ${thin ? 'rgba(245,158,11,0.2)' : 'rgba(0,212,138,0.2)'}`,
          borderRadius: 999,
          padding: '2px 8px',
          letterSpacing: '0.08em',
        }}>
          {isLoading ? 'loading' : thin ? 'building sample' : 'learning live'}
        </span>
        <span style={{ ...MONO, fontSize: 9, color: '#8ca0b3' }}>
          {summary
            ? `${summary.calibrated_trades}/${summary.closed_trades} calibrated closes`
            : 'waiting for calibration data'}
        </span>
      </div>

      <div style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fit, minmax(130px, 1fr))',
        gap: 12,
      }}>
        <Stat
          label="AVG LATENCY"
          value={overview?.avg_latency_minutes != null ? `${overview.avg_latency_minutes.toFixed(0)}m` : '—'}
          tone={calibrationAccent((overview?.avg_latency_minutes ?? null) != null ? -(overview?.avg_latency_minutes ?? 0) : null)}
        />
        <Stat
          label="MOVE COST"
          value={overview?.avg_move_cost_pct != null ? `${overview.avg_move_cost_pct >= 0 ? '+' : ''}${overview.avg_move_cost_pct.toFixed(1)}%` : '—'}
          tone={calibrationAccent(-(overview?.avg_move_cost_pct ?? 0))}
        />
        <Stat
          label="CAPTURE RATIO"
          value={overview?.avg_capture_ratio_pct != null ? `${overview.avg_capture_ratio_pct.toFixed(0)}%` : '—'}
          tone={calibrationAccent((overview?.avg_capture_ratio_pct ?? 0) - 50)}
        />
        <Stat
          label="AVG MFE / MAE"
          value={
            overview?.avg_mfe_pct != null || overview?.avg_mae_pct != null
              ? `${overview?.avg_mfe_pct != null ? `${overview.avg_mfe_pct >= 0 ? '+' : ''}${overview.avg_mfe_pct.toFixed(1)}` : '—'} / ${overview?.avg_mae_pct != null ? `${overview.avg_mae_pct >= 0 ? '+' : ''}${overview.avg_mae_pct.toFixed(1)}` : '—'}`
              : '—'
          }
        />
      </div>

      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
        {leadingTiming && (
          <span style={{
            ...MONO,
            fontSize: 8,
            color: timingBucketColor(leadingTiming.label),
            background: `${timingBucketColor(leadingTiming.label)}12`,
            border: `1px solid ${timingBucketColor(leadingTiming.label)}28`,
            borderRadius: 999,
            padding: '4px 8px',
            letterSpacing: '0.08em',
          }}>
            timing {leadingTiming.label.toLowerCase()} · {leadingTiming.avg_pnl_pct != null ? `${leadingTiming.avg_pnl_pct >= 0 ? '+' : ''}${leadingTiming.avg_pnl_pct.toFixed(1)}%` : '—'}
          </span>
        )}
        {leadingPhase && (
          <span style={{
            ...MONO,
            fontSize: 8,
            color: timingBucketColor(leadingPhase.label),
            background: `${timingBucketColor(leadingPhase.label)}12`,
            border: `1px solid ${timingBucketColor(leadingPhase.label)}28`,
            borderRadius: 999,
            padding: '4px 8px',
            letterSpacing: '0.08em',
          }}>
            window {leadingPhase.label.toLowerCase()} · {leadingPhase.avg_pnl_pct != null ? `${leadingPhase.avg_pnl_pct >= 0 ? '+' : ''}${leadingPhase.avg_pnl_pct.toFixed(1)}%` : '—'}
          </span>
        )}
        {insights.slice(0, 2).map((insight, i) => (
          <span key={`${insight.kind}-${i}`} style={{
            ...MONO,
            fontSize: 8,
            color: calibrationAccent(insight.metric),
            background: `${calibrationAccent(insight.metric)}12`,
            border: `1px solid ${calibrationAccent(insight.metric)}28`,
            borderRadius: 999,
            padding: '4px 8px',
            letterSpacing: '0.08em',
          }}>
            {insight.title.toLowerCase()}
          </span>
        ))}
      </div>

      {thin ? (
        <div style={{
          ...MONO,
          fontSize: 10,
          color: '#8ca0b3',
          lineHeight: 1.5,
          paddingTop: 2,
        }}>
          We’re still building sample here. The strip will become much more useful once a few more trades close with full timing and excursion data.
        </div>
      ) : (
        <div style={{
          display: 'grid',
          gridTemplateColumns: 'minmax(0, 1.4fr) minmax(280px, 1fr)',
          gap: 14,
          alignItems: 'start',
        }}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            <div style={{ ...MONO, fontSize: 8, color: 'var(--dim)', letterSpacing: '0.1em' }}>RECENT CALIBRATED CLOSES</div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              {recentTrades.slice(0, 4).map((trade) => (
                <div key={trade.trade_id} style={{
                  display: 'grid',
                  gridTemplateColumns: '78px 1fr auto',
                  gap: 10,
                  alignItems: 'center',
                  padding: '8px 10px',
                  borderRadius: 8,
                  background: 'rgba(255,255,255,0.02)',
                  border: '1px solid rgba(255,255,255,0.04)',
                }}>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                    <span style={{ ...MONO, fontSize: 11, color: '#d7e1ea', fontWeight: 700 }}>{trade.symbol}</span>
                    <span style={{ ...MONO, fontSize: 8, color: pnlColor(trade.pnl_pct ?? 0) }}>
                      {trade.pnl_pct != null ? `${trade.pnl_pct >= 0 ? '+' : ''}${trade.pnl_pct.toFixed(1)}%` : '—'}
                    </span>
                  </div>
                  <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                    {[
                      `timing ${trade.entry_timing_bucket.toLowerCase()}`,
                      `window ${trade.signal_window_phase.toLowerCase()}`,
                      `room ${trade.profit_room_label.toLowerCase()}`,
                      trade.minutes_scan_to_entry != null ? `${trade.minutes_scan_to_entry.toFixed(0)}m late` : null,
                      trade.capture_ratio_pct != null ? `capture ${trade.capture_ratio_pct.toFixed(0)}%` : null,
                      trade.max_favorable_excursion_pct != null ? `mfe ${trade.max_favorable_excursion_pct >= 0 ? '+' : ''}${trade.max_favorable_excursion_pct.toFixed(1)}%` : null,
                      trade.max_adverse_excursion_pct != null ? `mae ${trade.max_adverse_excursion_pct >= 0 ? '+' : ''}${trade.max_adverse_excursion_pct.toFixed(1)}%` : null,
                    ].filter(Boolean).map((tag) => (
                      <span key={tag} style={{
                        ...MONO,
                        fontSize: 8,
                        color: '#9fb2c3',
                        background: 'rgba(255,255,255,0.03)',
                        border: '1px solid rgba(255,255,255,0.05)',
                        borderRadius: 999,
                        padding: '3px 7px',
                        letterSpacing: '0.04em',
                      }}>
                        {tag}
                      </span>
                    ))}
                  </div>
                  <span style={{ ...MONO, fontSize: 8, color: '#6f8498', textAlign: 'right' }}>
                    {(trade.exit_reason || '—').replaceAll('_', ' ').toLowerCase()}
                  </span>
                </div>
              ))}
            </div>
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            <div style={{ ...MONO, fontSize: 8, color: 'var(--dim)', letterSpacing: '0.1em' }}>WHAT THE SYSTEM IS LEARNING</div>
            {insights.length === 0 ? (
              <div style={{
                ...MONO,
                fontSize: 10,
                color: '#8ca0b3',
                lineHeight: 1.5,
                padding: '8px 10px',
                borderRadius: 8,
                background: 'rgba(255,255,255,0.02)',
                border: '1px solid rgba(255,255,255,0.04)',
              }}>
                We have enough calibrated closes to track latency and capture, but not enough signal separation yet for a strong lesson.
              </div>
            ) : insights.slice(0, 3).map((insight, i) => (
              <div key={`${insight.kind}-${i}`} style={{
                padding: '8px 10px',
                borderRadius: 8,
                background: 'rgba(255,255,255,0.02)',
                border: '1px solid rgba(255,255,255,0.04)',
                display: 'flex',
                flexDirection: 'column',
                gap: 4,
              }}>
                <span style={{ ...MONO, fontSize: 9, color: calibrationAccent(insight.metric), letterSpacing: '0.06em' }}>{insight.title}</span>
                <span style={{ ...MONO, fontSize: 10, color: '#8ca0b3', lineHeight: 1.45 }}>{insight.detail}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </section>
  )
}

void [
  NarrativeStrip,
  LearningEngineStatus,
  ScannerRegimeStrip,
  ScannerSignalsPanel,
  LearningSystem,
  LifecycleAttributionPanel,
  LifecycleValidationPanel,
  DiscoveryMonitorPanel,
  QualityLanePanel,
  PaperTradeAttributionPanel,
  SetupPerformanceLedgerPanel,
  IntelValidationPanel,
  AttributionPanel,
  SystemHealthRail,
  ProofWorkspace,
]

// ── MemecoinsPage (V3) ────────────────────────────────────────────────────

export function MemecoinsPage() {
  const queryClient                 = useQueryClient()
  const [buyAmounts, setBuyAmounts] = useState<Record<string, string>>({})
  const [busyMints,  setBusyMints]  = useState<Set<string>>(new Set())
  const [msg, setMsg]               = useState<{ text: string; ok: boolean } | null>(null)

  // Main memecoin status (positions, signals, stats, recent_closed)
  const { data, isLoading } = useQuery<MemecoinsStatus>({
    queryKey: ['memecoins'],
    queryFn:  async () => (await api.get('/memecoins/status')).data,
    refetchInterval: 30_000,
  })

  // V3 lane state — authority bar + system health rail
  const v3LaneQ = useQuery<V3LaneStateResponse>({
    queryKey: ['v3-memecoins-lane-state'],
    queryFn:  () => api.get('/v3/memecoins/lane-state').then(r => r.data),
    refetchInterval: 120_000,
    staleTime: 60_000,
  })

  // V3 queue — operator candidate queue
  const v3QueueQ = useQuery<V3QueueResponse>({
    queryKey: ['v3-memecoins-queue'],
    queryFn:  () => api.get('/v3/memecoins/queue').then(r => r.data),
    refetchInterval: 60_000,
    staleTime: 30_000,
  })

  const v3ProofWorkspaceQ = useQuery<V3ProofWorkspaceResponse>({
    queryKey: ['v3-proof-workspace'],
    queryFn: () => api.get('/v3/memecoins/proof-workspace').then(r => r.data),
    refetchInterval: 120_000,
    staleTime: 60_000,
  })

  const calibrationQ = useQuery<CalibrationView>({
    queryKey: ['memecoin-calibration-view'],
    queryFn: () => api.get('/memecoins/calibration-view?window_days=90&limit=12').then(r => r.data),
    refetchInterval: 180_000,
    staleTime: 60_000,
  })

  const signals      = data?.signals       ?? []
  const positions    = data?.positions     ?? []
  const stats        = data?.stats         ?? { win_rate: 0, total_pnl: 0, closed_count: 0 }
  const recentClosed = data?.recent_closed ?? []

  void buyAmounts
  void setBuyAmounts
  void isLoading

  function flash(text: string, ok = true) {
    setMsg({ text, ok })
    setTimeout(() => setMsg(null), 4500)
  }

  async function handleBuy(signal: MemecoinSignal) {
    const amt = parseFloat(buyAmounts[signal.mint] ?? '10')
    if (!amt || amt <= 0) return
    setBusyMints(s => new Set(s).add(signal.mint))
    try {
      const r = await api.post('/memecoins/buy', {
        mint: signal.mint, symbol: signal.symbol, amount_usd: amt,
      })
      if (r.data?.success) {
        flash(`Bought ${signal.symbol} — $${amt}`)
        queryClient.invalidateQueries({ queryKey: ['memecoins'] })
      } else {
        flash(r.data?.error ?? 'Buy failed', false)
      }
    } catch (e: any) {
      flash(e?.response?.data?.detail ?? e.message, false)
    }
    setBusyMints(s => { const n = new Set(s); n.delete(signal.mint); return n })
  }

  async function handleSell(pos: MemecoinPosition) {
    await handleExitMint(pos.mint, pos.symbol, pos.pnl_pct)
  }

  async function runExitAction(
    mint: string,
    symbol: string,
    {
      pct = 100,
      reason = 'MANUAL',
      label = 'Exit',
      currentReturnPct,
    }: {
      pct?: number
      reason?: string
      label?: string
      currentReturnPct?: number | null
    },
  ) {
    const returnText = currentReturnPct != null
      ? ` Return: ${currentReturnPct >= 0 ? '+' : ''}${currentReturnPct.toFixed(1)}%.`
      : ''
    if (!confirm(`${label} ${symbol}${pct < 100 ? ` (${pct.toFixed(0)}%)` : ''}?${returnText}`)) return
    setBusyMints(s => new Set(s).add(mint))
    try {
      const r = await api.post(`/memecoins/sell/${mint}`, { pct, reason })
      if (r.data?.success) {
        const pnlBase = typeof r.data.realized_pnl_delta_usd === 'number' ? r.data.realized_pnl_delta_usd : r.data.pnl_usd
        const s = pnlBase >= 0 ? '+' : ''
        const actionWord = r.data.closed ? 'Sold' : 'Reduced'
        const suffix = r.data.closed
          ? `${s}${r.data.pnl_pct.toFixed(1)}% ($${s}${Math.abs(Number(r.data.pnl_usd ?? 0)).toFixed(2)})`
          : `${r.data.pct_sold?.toFixed?.(0) ?? pct}% ($${s}${Math.abs(Number(pnlBase ?? 0)).toFixed(2)})`
        flash(`${actionWord} ${symbol} — ${suffix}`)
        queryClient.invalidateQueries({ queryKey: ['memecoins'] })
        queryClient.invalidateQueries({ queryKey: ['v3-proof-workspace'] })
      } else {
        flash(r.data?.error ?? 'Sell failed', false)
      }
    } catch (e: any) {
      flash(e?.response?.data?.detail ?? e.message, false)
    }
    setBusyMints(s => { const n = new Set(s); n.delete(mint); return n })
  }

  async function handleExitMint(mint: string, symbol: string, currentReturnPct?: number | null) {
    await runExitAction(mint, symbol, { pct: 100, reason: 'MANUAL', label: 'Exit', currentReturnPct })
  }

  async function handleReduceMintPct(
    mint: string,
    symbol: string,
    currentReturnPct?: number | null,
    pct = 50,
    label = 'De-risk',
    reason = 'MANUAL_DE_RISK',
  ) {
    await runExitAction(mint, symbol, { pct, reason, label, currentReturnPct })
  }

  void handleBuy
  void handleReduceMintPct

  return (
    <div style={{ maxWidth: 1400, margin: '0 auto', padding: '20px 24px', display: 'flex', flexDirection: 'column', gap: 24 }}>

      {/* ── HEADER — compact identity strip ── */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <span style={{ ...MONO, color: '#00d48a', fontWeight: 700, fontSize: 13, letterSpacing: '0.14em' }}>
          MEMECOIN OPS
        </span>
        <span style={{
          ...MONO, fontSize: 8, color: '#f59e0b',
          background: 'rgba(245,158,11,0.08)', border: '1px solid rgba(245,158,11,0.2)',
          borderRadius: 3, padding: '2px 7px', letterSpacing: '0.1em',
        }}>
          PAPER
        </span>
        <span style={{ ...MONO, fontSize: 8, color: '#1a2535', letterSpacing: '0.06em' }}>
          setups · queue · active trades · learning
        </span>
        <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 20 }}>
          {signals.length > 0 && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
              <span style={{ ...MONO, color: '#1a2535', fontSize: 8 }}>signals</span>
              <span style={{ ...MONO, fontWeight: 700, fontSize: 12, color: '#00d48a' }}>{signals.length}</span>
            </div>
          )}
          {stats.closed_count > 0 && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
              <span style={{ ...MONO, color: '#1a2535', fontSize: 8 }}>win rate</span>
              <span style={{
                ...MONO, fontWeight: 700, fontSize: 12,
                color: stats.win_rate >= 40 ? '#00d48a' : '#f59e0b',
              }}>
                {stats.win_rate.toFixed(0)}%
              </span>
            </div>
          )}
        </div>
      </div>

      {/* ── V3: LANE AUTHORITY BAR ── */}
      <LaneAuthorityBar data={v3LaneQ.data} />

      <OperatorSummaryStrip
        signals={signals}
        queue={v3QueueQ.data}
        proofWorkspace={v3ProofWorkspaceQ.data}
        laneState={v3LaneQ.data}
      />

      <CalibrationStrip data={calibrationQ.data} isLoading={calibrationQ.isLoading} />

      <TopBuyList signals={signals} queue={v3QueueQ.data} />

      {/* ── V3: CURRENT QUEUE ── */}
      <section>
        <ZoneLabel text="CURRENT QUEUE" />
        <CandidateQueue data={v3QueueQ.data} isLoading={v3QueueQ.isLoading} />
      </section>

      {/* ── ZONE 4: TRADE BOOK — only rendered when there is data ── */}
      {(msg !== null || positions.length > 0 || recentClosed.length > 0) && (
        <section style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          <ZoneLabel text="TRADE BOOK" />
          {msg && (
            <div style={{
              padding: '10px 14px', marginBottom: 10,
              background: msg.ok ? 'rgba(0,212,138,0.07)' : 'rgba(239,68,68,0.07)',
              border: `1px solid ${msg.ok ? 'rgba(0,212,138,0.2)' : 'rgba(239,68,68,0.2)'}`,
              borderRadius: 6, fontSize: 11, ...MONO,
              color: msg.ok ? '#00d48a' : '#ef4444',
            }}>
              {msg.ok ? '✅' : '❌'} {msg.text}
            </div>
          )}
          {positions.length > 0 && (
            <div style={{ marginBottom: 10 }}>
              <OpenPositionsTable positions={positions} busyMints={busyMints} onSell={handleSell} />
            </div>
          )}
          {recentClosed.length > 0 && (
            <div style={{
              background: 'rgba(255,255,255,0.015)',
              border: '1px solid rgba(255,255,255,0.04)',
              borderRadius: 8, padding: '12px 16px',
            }}>
              <div style={{ ...MONO, color: '#1a2535', fontSize: 8, letterSpacing: '0.12em', marginBottom: 10 }}>
                RECENT CLOSED
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                {recentClosed.map((t, i) => {
                  const clr  = pnlColor(t.pnl_pct)
                  const sign = t.pnl_pct >= 0 ? '+' : ''
                  const reasonClr = t.exit_reason === 'TP_2X' ? '#00d48a'
                                  : t.exit_reason === 'SL_50' ? '#ef4444'
                                  : '#4d5a6e'
                  return (
                    <div key={i} style={{
                      display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                      padding: '6px 0',
                      borderTop: i > 0 ? '1px solid rgba(255,255,255,0.03)' : 'none',
                      ...MONO,
                    }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                        <span style={{ color: '#c0cfe0', fontWeight: 700, fontSize: 11, minWidth: 60 }}>
                          {t.symbol}
                        </span>
                        <span style={{
                          color: reasonClr, fontSize: 8,
                          background: `${reasonClr}12`, border: `1px solid ${reasonClr}28`,
                          borderRadius: 3, padding: '2px 6px',
                        }}>
                          {t.exit_reason}
                        </span>
                      </div>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
                        <span style={{ color: clr, fontWeight: 700, fontSize: 11 }}>
                          {sign}{t.pnl_pct.toFixed(1)}%
                        </span>
                        <span style={{ color: clr, fontSize: 9 }}>
                          ${sign}{t.pnl_usd.toFixed(2)}
                        </span>
                        <span style={{ color: 'var(--deep)', fontSize: 8 }}>
                          {fmtRelTime(t.closed_at)}
                        </span>
                      </div>
                    </div>
                  )
                })}
              </div>
            </div>
          )}
        </section>
      )}
    </div>
  )
}
