export interface V3Reason {
  key: string
  label: string
  detail: string
  severity: 'GOOD' | 'INFO' | 'WARN' | 'BLOCK'
}

export interface V3Constraint extends V3Reason {
  count: number
}

export interface V3ReinforcementThresholds {
  light_min: number
  moderate_min: number
  strong_min: number
}

export interface V3ReinforcementDebug {
  next_level: string | null
  points_to_next: number
  thresholds: V3ReinforcementThresholds
  missing_reasons: string[]
  breakdown?: {
    scanner?: Record<string, unknown>
    lifecycle?: Record<string, unknown>
    whale?: {
      kind?: string
      exact_overlap?: boolean
      exact_overlap_count?: number
      symbol_family_count?: number
      meaningful_arkham_count?: number
      latest_event_ts?: string | null
      age_minutes?: number | null
    }
  }
}

export interface V3ScoreBreakdown {
  readiness: number | null
  support: number | null
  safety: number | null
  timing: number | null
  market: number | null
  profit_room: number | null
}

export interface V3TradeQuality {
  verdict: string
  reasons: V3Reason[]
}

export interface V3ProfitRoom {
  label: string
  confidence: number | null
  reasons: string[]
}

export interface V3ExitReview {
  id: number
  ts_utc: string
  trade_id: number
  mint: string
  symbol: string
  scanner_regime: string | null
  current_price: number | null
  current_return_pct: number | null
  age_hours: number | null
  entry_proof_score: number | null
  current_proof_score: number | null
  proof_score_delta: number | null
  current_proof_reason: string | null
  review_state: string
  review_state_label?: string | null
  recommended_action: string
  recommended_pct: number | null
  exit_reason: string | null
  exit_reason_label?: string | null
  current_readiness_score?: number | null
  current_readiness_level?: string | null
  readiness_delta?: number | null
  should_exit: number
  auto_exit_enabled: number
  executed: number
  intent_id: number | null
  notes: string | null
}

export interface V3ExitReviewHistoryItem {
  id: number
  ts_utc: string
  trade_id: number
  mint: string
  symbol: string
  current_return_pct: number | null
  proof_score_delta: number | null
  review_state: string
  review_state_label?: string | null
  recommended_action: string
  recommended_pct: number | null
  exit_reason: string | null
  exit_reason_label?: string | null
  executed: number
}

export interface V3Candidate {
  id: number
  symbol: string
  mint: string
  source?: string
  promotion_state?: string | null
  discovery_stage?: string | null
  discovery_rank_score?: number | null
  discovery_flags?: string[]
  row_status: string
  scanner_score: number
  scanner_regime: string
  scanner_relaxation_reason: string | null
  return_4h_pct: number | null
  return_24h_pct: number | null
  freshness: {
    input_source: string
    freshness_score: number
    cache_lineage: string
    drought_reason: string | null
    scanned_at: string
    age_minutes: number
  }
  safety: {
    rug_label: string
    safety_confidence: number
    telemetry_missing: boolean
    holder_quality_level: string
    lp_locked_pct: number | null
    mint_revoked: boolean
    freeze_revoked: boolean
  }
  reinforcement: {
    level: string
    score: number
    support_score?: number
    support_level?: string
    support_kind: string
    confidence: number
    recency_minutes: number | null
    reasons: V3Reason[]
    support_signals?: V3Reason[]
    wallet?: {
      level: string
      overlap_score: number
      confidence: number
      unique_wallets: number
      repeat_wallets: number
      high_quality_wallets: number
      recent_activity: boolean
      reasons: V3Reason[]
    }
    debug?: V3ReinforcementDebug
  }
  scores: V3ScoreBreakdown
  trade_quality: V3TradeQuality
  profit_room: V3ProfitRoom | null
  proof: {
    route: string
    stage: string
    confidence: number
    score: number | null
    readiness_score?: number | null
    readiness_level?: string | null
    readiness_reasons?: V3Reason[]
    hard_blockers: V3Reason[]
    soft_penalties: V3Reason[]
    promotion_summary: string | null
    proof_reason: string | null
    first_leg_confirmed: boolean
    provisional_first_leg: boolean
    provisional_score: number | null
    provisional_reason: string | null
  }
  policy: {
    deploy_authority: string
    slot_state: string
    recommended_slots: number
    used_slots: number
    available_slots: number
    unlock_next: V3Reason[]
  }
  labels: {
    trust_label: string | null
    triage_state: string | null
    label_confidence: number | null
    label_source: string | null
    labeled_at: string | null
  }
}

export interface V3LaneStateResponse {
  generated_at: string
  status: string
  detail: string
  lane_state: {
    proof_authority: string
    reinforcement_authority: string
    promotion_authority: string
    deployment_authority: string
    graduation_state: string
    proof_input_source: string
    active_policy_posture: string
  }
  proof_slots: {
    total_slots: number
    used_slots: number
    recommended_slots: number
    slot_state: string
    slot_authority?: string
    slot_gates: Array<{ key: string; passed: boolean; note: string }>
  }
  cohorts: Array<{
    cohort_key: string
    label: string
    sample_n: number
    proof_trade_n: number
    open_proof_trade_n: number
    win_rate_4h: number | null
    avg_return_4h: number | null
    avg_return_24h: number | null
    win_rate_24h: number | null
    verdict: string
    reason: string
    recent_trend: string
  }>
  system_health: {
    pipeline_status: string
    pipeline_detail: string
    funnel_status: string
    funnel_reason: string
    proof_input_source: string
    reinforcement_status: string
    reinforcement_detail?: string
    reinforcement_thresholds?: V3ReinforcementThresholds
    reinforcement_summary?: {
      candidate_count: number
      level_counts: {
        STRONG: number
        MODERATE: number
        LIGHT: number
        NONE: number
      }
      exact_overlap_candidates: number
      symbol_family_candidates: number
      relaxed_candidates: number
    }
    discovery_summary?: {
      qualified_count: number
      promoted_count: number
      scan_best_overlap: number
      lifecycle_early: number
      discovery_plus: number
      discovery_ingress: number
    }
    label_coverage_pct: number
    recent_scanner_rows: number
    rug_mix: {
      good: number
      warn: number
      unknown: number
    }
  }
  top_constraints: V3Constraint[]
}

export interface V3QueueResponse {
  generated_at: string
  status: string
  detail: string
  counts: Record<string, number>
  lane_state: V3LaneStateResponse['lane_state']
  discovery_summary?: V3LaneStateResponse['system_health']['discovery_summary']
  groups: Record<string, V3Candidate[]>
}

export interface V3ProofTrade {
  id: number
  opened_ts_utc: string
  closed_ts_utc: string
  symbol: string
  mint: string
  status: string
  amount_usd: number
  initial_amount_usd?: number
  realized_release_usd?: number
  realized_pnl_usd?: number
  entry_score: number
  proof_status: string
  proof_reason: string
  proof_score: number
  readiness_score?: number | null
  readiness_level?: string | null
  scanner_regime: string
  scanner_relaxation_reason: string
  trust_label: string
  triage_state: string
  source_scanned_at: string
  source_return_4h_pct: number | null
  source_return_24h_pct: number | null
  support_signals?: V3Reason[]
  age_hours: number | null
  exit_review?: V3ExitReview | null
  exit_history?: V3ExitReviewHistoryItem[]
}

export interface V3CandidateDetailResponse {
  generated_at: string
  mint: string
  lane_state: V3LaneStateResponse['lane_state']
  candidate: V3Candidate | null
  history: Array<{
    id: number
    scanned_at: string
    source: string
    status: string
    symbol: string
    mint: string
    score: number | null
    proof_score: number | null
    readiness_score?: number | null
    readiness_level?: string | null
    proof_reason: string | null
    return_4h_pct: number | null
    return_24h_pct: number | null
    scanner_regime: string
    scanner_relaxation_reason: string | null
    trust_label: string | null
    triage_state: string | null
    label_source: string | null
    labeled_at: string | null
  }>
  proof_review: {
    open_trade: V3ProofTrade | null
    recent_outcomes: Array<{
      id: number
      symbol: string
      mint: string
      source: string
      status: string
      scanner_regime: string
      scanner_relaxation_reason: string | null
      return_4h_pct: number | null
      return_24h_pct: number | null
      proof_reason: string | null
      proof_score: number
      readiness_score?: number | null
      readiness_level?: string | null
      support_signals?: V3Reason[]
      has_support: boolean
      route: 'NORMAL' | 'RELAXED'
    }>
  }
}

export interface V3ProofWorkspaceResponse {
  generated_at: string
  status: string
  detail: string
  lane_state: V3LaneStateResponse['lane_state']
  system_health: V3LaneStateResponse['system_health']
  counts: { proof_ready: number; reinforced_pending: number; in_proof_trade: number; recent_outcomes: number }
  proof_ready: V3Candidate[]
  reinforced_pending: V3Candidate[]
  in_proof_trade: Array<{ candidate: V3Candidate | null; trade: V3ProofTrade }>
  recent_outcomes: Array<{
    symbol: string
    mint: string
    route: 'NORMAL' | 'RELAXED'
    scanner_regime: string
    scanner_relaxation_reason: string | null
    return_4h_pct: number | null
    return_24h_pct: number | null
    proof_score: number | null
    readiness_score?: number | null
    readiness_level?: string | null
    proof_reason: string | null
    has_support: boolean
    support_signals?: V3Reason[]
  }>
  current_blockers: Array<{ key: string; count: number }>
  recent_blocker_summary: {
    window_hours: number
    total_reviewed: number
    dominant_key: string | null
    categories: Array<{ key: string; count: number; sample_symbols: string[] }>
  }
}

export const V3_STAGE_LABELS: Record<string, string> = {
  SCANNER_PENDING: 'SCANNER PENDING',
  REINFORCED_PENDING: 'REINFORCED PENDING',
  PROOF_READY: 'PROOF READY',
  IN_PROOF_TRADE: 'IN PROOF TRADE',
  COMPLETE: 'COMPLETE',
}

export function v3SeverityColor(sev: V3Reason['severity']): string {
  if (sev === 'GOOD') return '#00d48a'
  if (sev === 'INFO') return '#60a5fa'
  if (sev === 'WARN') return '#f59e0b'
  if (sev === 'BLOCK') return '#ef4444'
  return '#4d6070'
}

export function v3RouteColor(route: string): string {
  if (route === 'NORMAL') return '#60a5fa'
  if (route === 'RELAXED') return '#f59e0b'
  if (route === 'RESEARCH_ONLY') return '#4d6070'
  return '#4d6070'
}

export function v3StageColor(stage: string): string {
  if (stage === 'SCANNER_PENDING') return '#f59e0b'
  if (stage === 'REINFORCED_PENDING') return '#60a5fa'
  if (stage === 'PROOF_READY') return '#00d48a'
  if (stage === 'IN_PROOF_TRADE') return '#06b6d4'
  if (stage === 'COMPLETE') return '#4d6070'
  return '#4d6070'
}

export function v3LaneColor(state: string): string {
  if (state === 'GRADUATED') return '#00d48a'
  if (state === 'EARNING_TRUST') return '#60a5fa'
  if (state === 'RESTRICTED') return '#f59e0b'
  if (state === 'LOCKED') return '#ef4444'
  return '#4d6070'
}

export function v3DeployColor(auth: string): string {
  if (auth === 'FORCEFUL') return '#00d48a'
  if (auth === 'SUPPORTED') return '#60a5fa'
  if (auth === 'BLOCKED') return '#ef4444'
  return '#4d6070'
}

export function v3ReinfColor(level: string): string {
  if (level === 'STRONG') return '#00d48a'
  if (level === 'MODERATE') return '#60a5fa'
  if (level === 'LIGHT') return '#f59e0b'
  return '#4d6070'
}

export function v3ProfitRoomColor(label: string | null | undefined): string {
  const room = String(label || '').toUpperCase()
  if (room.includes('HIGH') || room.includes('EXPANSIVE') || room.includes('OPEN')) return '#00d48a'
  if (room.includes('MEDIUM') || room.includes('ENOUGH') || room.includes('OK')) return '#60a5fa'
  if (room.includes('LOW') || room.includes('TIGHT') || room.includes('LATE')) return '#f59e0b'
  if (room.includes('NONE') || room.includes('BLOCK') || room.includes('EXHAUST')) return '#ef4444'
  return '#4d6070'
}

export function v3PolicyLabel(posture: string): string {
  if (posture === 'PAPER_PROOF_ONLY') return 'paper proof only'
  if (posture === 'LIMITED_PROOF_DEPLOYMENT') return 'limited deployment'
  if (posture === 'PROOF_STILL_ACCUMULATING') return 'proof accumulating'
  if (posture === 'AUTO_BUY_DISABLED') return 'auto-buy disabled'
  return posture.toLowerCase().replace(/_/g, ' ')
}
