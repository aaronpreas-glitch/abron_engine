import React from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../api'
import { liveBudgetedInterval, slowBudgetedInterval } from '../queryBudget'

// ── Types ─────────────────────────────────────────────────────────────────────

interface BestActionCandidate {
  arm: 'MEMECOINS' | 'SPOT' | 'PERPS'
  asset: string
  token_address?: string | null
  action: string
  conviction: number
  reason: string
  manual_only: boolean
  blockers: string[]
}

interface BestActionData {
  generated_at: string
  focus_mode?: string
  verdict: 'BUY' | 'DCA' | 'LONG' | 'SHORT' | 'DO_NOTHING'
  arm: string | null
  asset: string | null
  token_address?: string | null
  action: string | null
  conviction: number
  reason: string
  execution_blocked: boolean
  candidates: BestActionCandidate[]
}

interface HomeSummary {
  generated_at?: string | null
  tiers: {
    mode: string; positions: number; collateral_usd: number
    buffer_usd: number; tp_cycles: number
  }
  memecoins: {
    mode: string; outcomes: number; next_milestone: number
    wr_pct: number | null; wr_gate_pct: number | null; fg_value: number | null; fg_ok: boolean
  }
  spot: {
    mode: string; holdings_count: number; basket_size: number
    signal_confidence: string; win_rate_7d: number | null; outcomes_complete: number
  }
  whale_watch: {
    total: number; in_range: number; scanner_pass: number
    alerts_sent: number; last_ts: string | null
  }
}

interface SpeculationHeatData {
  generated_at: string
  heat_state: string
  heat_score: number
  momentum: string
  speculation_heat_score: number
  froth_score: number
  sponsorship_score: number
  quality_score: number
  note: string
  reasons: string[]
  inputs: {
    proof_ready_count?: number
    near_ready_count?: number
    scanner_count?: number
    trade_quality_verdicts?: Record<string, number>
  }
  history: Array<{
    ts_utc: string
    heat_state: string
    heat_score: number
    momentum: string
  }>
}

interface AIAnalystData {
  generated_at: string
  status: string
  source: string
  headline: string
  top_lesson: string
  what_improved: string[]
  what_degraded: string[]
  review_now: string[]
  do_not_change_yet: string[]
}

interface DailyBriefTokenItem {
  symbol: string | null
  surface?: string | null
  action?: string | null
  priority?: string | null
  outcome_label?: string | null
  max_return_pct?: number | null
  reason?: string | null
  status?: string | null
  return_1h_pct?: number | null
  return_4h_pct?: number | null
  return_24h_pct?: number | null
}

interface DailyBriefAutopsyExample {
  symbol?: string | null
  mint?: string | null
  surface?: string | null
  decision_state?: string | null
  recommended_action?: string | null
  priority?: string | null
  outcome_label?: string | null
  classification?: string | null
  max_return_pct?: number | null
  direction?: string | null
  confidence?: string | null
  what_happened?: string | null
  why_it_matters?: string | null
  rule_review?: string | null
  primary_blocker?: {
    key?: string | null
    label?: string | null
    category?: string | null
    severity?: string | null
  } | null
}

type ProviderEscalationAction = 'DISMISS' | 'KEEP_WATCHING' | 'FORCE_REFRESH'
type ProviderEscalationReviewState = 'ACKNOWLEDGED' | 'RULE_PATCH_NEEDED' | 'DATA_PATCH_NEEDED' | 'FALSE_ALARM' | 'RESOLVED'
type ProviderEscalationPatchState = 'WATCH' | 'NEEDS_MORE_DATA' | 'READY_FOR_IMPLEMENTATION'
type ProviderEscalationWorkOrderState = 'READY' | 'STARTED' | 'BLOCKED' | 'COMPLETE'
type LiveContextMissionState = 'NEW' | 'INVESTIGATING' | 'RESOLVED_COVERED' | 'RESOLVED_IGNORED' | 'NEEDS_SOURCE'

interface ProviderEscalationItem {
  id?: number | null
  symbol?: string | null
  mint?: string | null
  lane?: string | null
  status?: string | null
  failure_class?: string | null
  repair_status?: string | null
  priority_score?: number | null
  queue_flags?: string[]
  provider_path?: string | null
  reason?: string | null
  age_minutes?: number | null
  stale_minutes?: number | null
  sla_due_ts?: string | null
  sla_breached?: boolean
  operator_decision?: string | null
  outcome_label?: string | null
}

interface ProviderEscalationReviewGroup {
  group_key?: string
  state?: string
  state_updated_at?: string | null
  operator_note?: string | null
  failure_class?: string | null
  lane?: string | null
  alert_count?: number
  kind_counts?: Record<string, number>
  symbols?: string[]
  latest_alert_ts?: string | null
  max_return_pct?: number | null
  unresolved?: boolean
  frozen?: boolean
  recommended_state?: string | null
  next_action?: string | null
  evidence?: Array<{
    symbol?: string | null
    mint?: string | null
    kind?: string | null
    alert_ts?: string | null
    message?: string | null
    row_id?: number | null
    failure_class?: string | null
    lane?: string | null
    correctness?: string | null
    outcome_source?: string | null
    max_return_pct?: number | null
    return_1h_pct?: number | null
    return_4h_pct?: number | null
    return_24h_pct?: number | null
    outcome_reason?: string | null
  }>
}

interface ProviderEscalationPatchPlan {
  group_key?: string
  plan_key?: string
  review_state?: string | null
  manual_state?: string | null
  manual_state_updated_at?: string | null
  operator_note?: string | null
  plan_type?: string | null
  failure_class?: string | null
  lane?: string | null
  proposed_fix?: string | null
  expected_effect?: string | null
  sample_n?: number
  alert_count?: number
  max_return_pct?: number | null
  avg_1h_pct?: number | null
  avg_4h_pct?: number | null
  simulated_benefit_n?: number
  weak_buy_risk_n?: number
  confidence_score?: number | null
  gate_status?: string | null
  gate_reason?: string | null
  ready_for_implementation?: boolean
  evidence?: ProviderEscalationReviewGroup['evidence']
}

interface ProviderEscalationWorkOrder {
  group_key?: string
  work_order_key?: string
  state?: string | null
  state_updated_at?: string | null
  operator_note?: string | null
  risk_label?: string | null
  target_subsystem?: string | null
  target_files?: string[]
  target_functions?: string[]
  goal?: string | null
  constraints?: string[]
  proof_required?: {
    sample_n?: number
    confidence_score?: number | null
    simulated_benefit_n?: number
    weak_buy_risk_n?: number
    gate_status?: string | null
  }
  checklist?: Array<{ key?: string; label?: string; required?: boolean }>
  rollback_condition?: string | null
  source_plan?: ProviderEscalationPatchPlan
  next_action?: string | null
}

interface ProviderEscalationExecutionPack {
  pack_key?: string
  group_key?: string
  state?: string | null
  work_order_state?: string | null
  risk_label?: string | null
  target_subsystem?: string | null
  priority_score?: number | null
  score_breakdown?: {
    impact?: number | null
    confidence?: number | null
    safety?: number | null
    state_bonus?: number | null
    risk_penalty?: number | null
  }
  mission_rank_reason?: string | null
  evidence_bundle?: {
    symbols?: string[]
    mints?: string[]
    alert_kinds?: string[]
    max_return_pct?: number | null
    avg_1h_pct?: number | null
    avg_4h_pct?: number | null
    confidence_score?: number | null
    simulated_benefit_n?: number
    weak_buy_risk_n?: number
    gate_status?: string | null
    why_it_matters?: string | null
    evidence?: ProviderEscalationReviewGroup['evidence']
  }
  replay_test_recipe?: Array<{ key?: string; label?: string; command?: string }>
  target_code_map?: Array<{ file?: string | null; function?: string | null; expected_change?: string | null }>
  completion_criteria?: string[]
  next_action?: string | null
}

interface DailyTopMission {
  status?: string
  mission_type?: string
  title?: string | null
  priority_score?: number | null
  pack_key?: string | null
  group_key?: string | null
  headline?: string | null
  expected_upside?: string | null
  proof?: string | null
  target?: {
    subsystem?: string | null
    file?: string | null
    function?: string | null
  }
  workbench_pack?: ProviderEscalationExecutionPack | null
  source_gap?: LiveOpportunityGap | null
  evidence_requirements?: LiveContextEvidenceRequirements | null
  outcome_loop?: LiveContextOutcomeLoop | null
  next_action?: string | null
}

interface LiveOpportunityGap {
  gap_key?: string
  gap_type?: string
  symbol?: string | null
  mint?: string | null
  source?: string | null
  mission_state?: string | null
  mission_state_updated_at?: string | null
  scope?: string | null
  scope_reason?: string | null
  heat_score?: number | null
  gap_score?: number | null
  data_freshness?: string | null
  data_confidence?: string | null
  quality_score?: number | null
  pressure_score?: number | null
  seen_decision?: boolean
  reason?: string | null
}

interface LiveContextMissionOutcomeItem {
  mission_key?: string
  state?: string | null
  scope?: string | null
  symbol?: string | null
  mint?: string | null
  gap_type?: string | null
  decision_at?: string | null
  operator_note?: string | null
  outcome_label?: string | null
  correctness?: string | null
  reason?: string | null
  review_reason?: string | null
  priority_score?: number | null
  decision_age_hours?: number | null
  live_heat_score?: number | null
  matched_decisions?: number | null
  matched_intelligence_rows?: number | null
}

interface LiveContextEvidenceRequirements {
  status?: string
  mission_type?: string
  requirements?: string[]
  pass_count?: number
  required_count?: number
  next_action?: string | null
}

interface LiveContextOutcomeLoop {
  status?: string
  gap_key?: string | null
  symbol?: string | null
  mint?: string | null
  coverage_gap_count?: number
  decision_surface_count?: number
  freshness?: string | null
  confidence?: string | null
  resolved?: boolean
  surfaced?: boolean
  next_action?: string | null
}

interface DailyCryptoBriefData {
  generated_at: string
  lookback_hours: number
  status: string
  headline: string
  safety: {
    execution_lock: string
    open_memecoin_trades: number | null
    execution_intents: number
    executed_intents: number
    intent_modes: Record<string, number>
    authority_verdicts: Record<string, number>
  }
  data_freshness: {
    rows: number
    freshest_updated_at?: string | null
    freshest_age_minutes?: number | null
    freshness_counts: Record<string, number>
    confidence_counts: Record<string, number>
    stale_high_quality: Array<{
      symbol: string | null
      age_minutes?: number | null
      quality_score?: number | null
      pressure_score?: number | null
      market_source?: string | null
    }>
  }
  decision_quality: {
    journal_count: number
    surface_counts: Record<string, number>
    action_counts: Record<string, number>
    outcome_counts: Record<string, number>
    avg_max_return_pct?: number | null
    missed_runner_count: number
    weak_buy_count: number
  }
  paper_pilot: {
    opened_count: number
    status_counts: Record<string, number>
    outcome_counts: Record<string, number>
    avg_max_return_pct?: number | null
    avg_1h_pct?: number | null
    avg_4h_pct?: number | null
  }
  top_missed_runners: DailyBriefTokenItem[]
  weak_buy_calls: DailyBriefTokenItem[]
  top_paper_movers: DailyBriefTokenItem[]
  outcome_autopsy?: {
    status?: string
    headline?: string
    summary?: {
      resolved_count?: number
      missed_runner_count?: number
      weak_buy_count?: number
      protected_count?: number
      good_buy_confirmed_count?: number
      pending_count?: number
    }
    top_rule_to_review?: {
      key?: string | null
      label?: string | null
      category?: string | null
      sample_n?: number
      missed_runner_n?: number
      protected_n?: number
      weak_buy_n?: number
      avg_missed_return_pct?: number | null
      recommendation?: string | null
    } | null
    examples?: DailyBriefAutopsyExample[]
  }
  rule_simulator?: {
    status?: string
    summary?: {
      resolved_rows?: number
      tighten_rules_tested?: number
      loosen_candidates?: number
    }
    top_candidate?: {
      key?: string | null
      label?: string | null
      direction?: string | null
      net_score?: number | null
      would_block_weak_buy_n?: number
      would_block_good_buy_n?: number
      missed_runner_n?: number
      protected_n?: number
      avg_missed_return_pct?: number | null
    } | null
  }
  candidate_replay_timeline?: {
    status?: string
    anchor?: {
      symbol?: string | null
      classification?: string | null
      max_return_pct?: number | null
    } | null
    events?: Array<{
      ts?: string | null
      surface?: string | null
      action?: string | null
      priority?: string | null
      decision_state?: string | null
      primary_blocker_label?: string | null
      outcome_label?: string | null
      max_return_pct?: number | null
    }>
  }
  data_watchdog?: {
    status?: string
    summary?: {
      rows?: number
      live_recent_rows?: number
      stale_rows?: number
      low_confidence_rows?: number
      sources?: Record<string, number>
    }
    by_source?: Array<{
      source?: string | null
      rows?: number
      live?: number
      recent?: number
      stale?: number
      high_confidence?: number
      low_confidence?: number
      avg_quality?: number | null
      avg_pressure?: number | null
      stale_pct?: number | null
      low_confidence_pct?: number | null
      status?: string | null
    }>
    refresh_priority?: Array<{
      symbol?: string | null
      mint?: string | null
      market_source?: string | null
      data_freshness?: string | null
      data_confidence?: string | null
      quality_score?: number | null
      pressure_score?: number | null
      priority_score?: number | null
      reasons?: string[]
    }>
    provider_alerts?: string[]
    repair_plan?: string | null
    action?: string | null
  }
  freshness_sla?: {
    status?: string
    score?: number | null
    live_recent_pct?: number | null
    stale_pct?: number | null
    low_confidence_pct?: number | null
    repair_attempts_24h?: number
    repair_success_pct?: number | null
    live_repaired_24h?: number
    unresolved_24h?: number
    retired_24h?: number
    next_action?: string | null
  }
  provider_reliability?: {
    status?: string
    attempts_24h?: number
    repair_hit_rate_pct?: number | null
    status_counts?: Record<string, number>
    top_failures?: Array<{ failure_class?: string; count?: number }>
    providers?: Array<{
      provider?: string
      attempts?: number
      live_repaired?: number
      fallback_repaired?: number
      unresolved?: number
      retired?: number
      repair_hit_rate_pct?: number | null
      unresolved_rate_pct?: number | null
      avg_latency_ms?: number | null
      failure_classes?: Record<string, number>
    }>
  }
  provider_failure_drilldown?: {
    status?: string
    items?: Array<{
      symbol?: string | null
      mint?: string | null
      repair_status?: string | null
      failure_class?: string | null
      provider_path?: string | null
      previous_source?: string | null
      new_source?: string | null
      repair_score?: number | null
      queue_flags?: string[]
      latency_ms?: number | null
      reason?: string | null
    }>
  }
  provider_escalation_queue?: {
    status?: string
    active_count?: number
    sla_breached_count?: number
    by_lane?: Record<string, number>
    by_status?: Record<string, number>
    items?: ProviderEscalationItem[]
    next_action?: string | null
  }
  provider_escalation_accuracy?: {
    status?: string
    sample_n?: number
    pending_n?: number
    correct_n?: number
    missed_n?: number
    accuracy_pct?: number | null
    confidence_adjustment?: number | null
    correctness_counts?: Record<string, number>
    by_lane?: Array<{ key?: string; sample_n?: number; correct?: number; missed?: number; pending?: number; accuracy_pct?: number | null }>
    by_failure?: Array<{ key?: string; sample_n?: number; correct?: number; missed?: number; pending?: number; accuracy_pct?: number | null }>
    frozen_failure_classes?: string[]
    frozen_lanes?: string[]
    missed_examples?: Array<{
      symbol?: string | null
      mint?: string | null
      lane?: string | null
      failure_class?: string | null
      correctness?: string | null
      max_return_pct?: number | null
      return_1h_pct?: number | null
      return_4h_pct?: number | null
      return_24h_pct?: number | null
      outcome_source?: string | null
      outcome_1h_status?: string | null
      outcome_4h_status?: string | null
      outcome_24h_status?: string | null
      alert_kind?: string | null
      alert_ts?: string | null
      reason?: string | null
    }>
    next_action?: string | null
  }
  provider_escalation_maturity?: {
    status?: string
    tracked_count?: number
    pending_count?: number
    due_now_count?: number
    overdue_count?: number
    next_check_ts?: string | null
    minutes_until_next_check?: number | null
    by_horizon?: Record<string, { waiting?: number; checked?: number; due?: number }>
    next_due?: Array<{
      symbol?: string | null
      mint?: string | null
      horizon?: string | null
      next_check_ts?: string | null
      correctness?: string | null
    }>
    next_action?: string | null
  }
  provider_escalation_alerts?: {
    status?: string
    count?: number
    items?: Array<{
      ts_utc?: string | null
      kind?: string | null
      severity?: string | null
      symbol?: string | null
      mint?: string | null
      message?: string | null
      row_id?: number | null
      data?: Record<string, unknown>
    }>
  }
  provider_escalation_review_queue?: {
    status?: string
    group_count?: number
    unresolved_count?: number
    state_counts?: Record<string, number>
    frozen_failure_classes?: string[]
    frozen_lanes?: string[]
    items?: ProviderEscalationReviewGroup[]
    next_action?: string | null
  }
  provider_escalation_patch_plans?: {
    status?: string
    plan_count?: number
    ready_count?: number
    implementation_ready_count?: number
    state_counts?: Record<string, number>
    top_plan?: ProviderEscalationPatchPlan | null
    items?: ProviderEscalationPatchPlan[]
    next_action?: string | null
  }
  provider_escalation_work_orders?: {
    status?: string
    work_order_count?: number
    started_count?: number
    ready_count?: number
    state_counts?: Record<string, number>
    top_work_order?: ProviderEscalationWorkOrder | null
    items?: ProviderEscalationWorkOrder[]
    next_action?: string | null
  }
  provider_escalation_execution_packs?: {
    status?: string
    pack_count?: number
    active_count?: number
    scoring?: {
      method?: string
      top_score?: number | null
      scored_count?: number
    }
    top_pack?: ProviderEscalationExecutionPack | null
    items?: ProviderEscalationExecutionPack[]
    next_action?: string | null
  }
  provider_post_patch_outcomes?: {
    status?: string
    tracked_count?: number
    regression_count?: number
    improving_count?: number
    top?: {
      group_key?: string | null
      status?: string | null
      sample_n?: number
      correct_n?: number
      missed_n?: number
      pending_n?: number
      accuracy_pct?: number | null
      max_return_pct?: number | null
      next_action?: string | null
    } | null
    next_action?: string | null
  }
  provider_regression_guard?: {
    status?: string
    freeze_count?: number
    frozen_failure_classes?: string[]
    frozen_lanes?: string[]
    items?: Array<{
      source?: string | null
      group_key?: string | null
      failure_class?: string | null
      lane?: string | null
      severity?: string | null
      reason?: string | null
      freeze?: boolean
    }>
    next_action?: string | null
  }
  provider_escalation_outcome_autorun?: {
    status?: string
    ran?: boolean
    checked_at?: string | null
    last_run_at?: string | null
    due_count?: number
    overdue_count?: number
    result?: {
      checked?: number
      updated?: number
      due_checked?: number
      alerts_emitted?: number
      counts?: Record<string, number>
    }
    error?: string | null
    reason?: string | null
  }
  rule_promotion_gate?: {
    status?: string
    reason?: string | null
    candidate?: {
      key?: string | null
      label?: string | null
      net_score?: number | null
    } | null
  }
  missed_runner_clusters?: {
    status?: string
    top_cluster?: {
      key?: string
      category?: string
      label?: string
      count?: number
      avg_max_return_pct?: number | null
    } | null
    clusters?: Array<{
      key?: string
      category?: string
      label?: string
      count?: number
      avg_max_return_pct?: number | null
    }>
  }
  catalyst_context?: {
    status?: string
    updated_at?: string | null
    decision_overlap_symbols?: string[]
    sources?: {
      coingecko_symbols?: string[]
      dexscreener_symbols?: string[]
      recent_confluence_count?: number
    }
    next_action?: string | null
  }
  live_market_narrative_intake?: {
    status?: string
    updated_at?: string | null
    source_counts?: Record<string, number>
    hot_symbols?: string[]
    hot_mints?: string[]
    items?: Array<{
      source?: string | null
      symbol?: string | null
      mint?: string | null
      name?: string | null
      type?: string | null
      rank?: number | null
      boosts?: number | null
      heat_score?: number | null
      reason?: string | null
    }>
    next_action?: string | null
  }
  live_opportunity_gaps?: {
    status?: string
    gap_count?: number
    by_type?: Record<string, number>
    top_gap?: LiveOpportunityGap | null
    items?: LiveOpportunityGap[]
    next_action?: string | null
  }
  live_context_mission_dossier?: {
    status?: string
    mission_key?: string
    state?: string | null
    state_updated_at?: string | null
    operator_note?: string | null
    source?: string | null
    symbol?: string | null
    mint?: string | null
    name?: string | null
    gap_type?: string | null
    gap_score?: number | null
    heat_score?: number | null
    reason?: string | null
    scope?: {
      scope?: string | null
      reason?: string | null
      manual_only?: boolean
    }
    next_action?: string | null
  }
  live_context_blind_spot_resolution?: {
    status?: string
    mission_key?: string
    scope?: string | null
    recommended_state?: LiveContextMissionState | string | null
    action?: string | null
    next_action?: string | null
  }
  live_context_mission_outcome_journal?: {
    status?: string
    event_count?: number
    mission_count?: number
    outcome_counts?: Record<string, number>
    correctness_counts?: Record<string, number>
    items?: LiveContextMissionOutcomeItem[]
    next_action?: string | null
  }
  live_context_review_queue?: {
    status?: string
    open_count?: number
    top_item?: LiveContextMissionOutcomeItem | null
    items?: LiveContextMissionOutcomeItem[]
    next_action?: string | null
  }
  live_context_decision_accuracy?: {
    status?: string
    sample_n?: number
    correct_n?: number
    miss_n?: number
    review_n?: number
    pending_n?: number
    accuracy_pct?: number | null
    top_miss?: LiveContextMissionOutcomeItem | null
    next_action?: string | null
  }
  live_context_policy_suggestions?: {
    status?: string
    suggestion_count?: number
    accuracy_pct?: number | null
    items?: Array<{
      policy_key?: string | null
      confidence?: string | null
      evidence_n?: number | null
      suggestion?: string | null
      manual_only?: boolean
    }>
    next_action?: string | null
  }
  live_context_generated_mission?: DailyTopMission
  live_context_evidence_requirements?: LiveContextEvidenceRequirements
  live_context_outcome_loop?: LiveContextOutcomeLoop
  daily_build_score?: {
    score?: number | null
    focus?: string
    components?: Record<string, number>
    next_action?: string | null
  }
  daily_top_mission?: DailyTopMission
  paper_to_pilot_gate?: {
    status?: string
    sample_n?: number
    win_rate_pct?: number | null
    avg_4h_pct?: number | null
    avg_max_return_pct?: number | null
    blockers?: string[]
    note?: string | null
  }
  daily_build_hooks?: {
    status?: string
    next_patch?: string | null
    safe_to_rearm_discussion?: boolean
    automation_hooks?: string[]
  }
  next_actions: string[]
}

// /api/home/brief — checks items have `name`, `label`, `pass`, `value`, `detail`
interface ReadinessCheck {
  name: string
  label: string
  pass: boolean
  value: string | number | null
  detail?: string
  wr_sample_concentration_status?: string
  wr_sample_top_token_symbol?: string | null
  wr_sample_top_token_share_pct?: number | null
  wr_sample_top_day?: string | null
  wr_sample_top_day_share_pct?: number | null
  wr_sample_actionable_n?: number | null
  wr_sample_unique_days?: number | null
}

interface BriefData {
  generated_at?: string | null
  checks: ReadinessCheck[]
  perp_24h: { trades: number; win_rate: number | null; pnl_usd: number | null } | null
  alerts: Array<{ ts: string; agent: string; message: string }>
  readiness_pct: number
  pipeline_age_hours?: number | null
  pipeline_last_evaluated_utc?: string | null
  pipeline_health?: 'FRESH' | 'AGING' | 'STALE' | 'NO_DATA' | 'UNKNOWN'
}

interface CapitalEvent {
  id: number
  ts_utc: string
  arm: string
  event_type: string
  amount_usd: number
  notes: string | null
  symbol: string | null
  ref_table: string | null
  ref_id: number | null
  dry_run: number
}

interface CapitalAllocationArm {
  arm: string
  deployed_usd: number
  realized_pnl_usd: number
  deploy_events_usd: number
  release_events_usd: number
  open_positions: number
  open_notional_usd?: number
  notes: string
}

interface CapitalAllocationResponse {
  generated_at: string | null
  totals: {
    deployed_usd: number
    realized_pnl_usd: number
    deploy_events_usd: number
    release_events_usd: number
    open_positions: number
  }
  external_flows: {
    deposits_usd: number
    withdrawals_usd: number
    net_external_flow_usd: number
  }
  arms: CapitalAllocationArm[]
  ledger: {
    total_events: number
    first_event_ts: string | null
    last_event_ts: string | null
    backfill_inserted_total: number
    backfill_inserted_by_arm: Record<string, {
      deploy: number
      release: number
      realized_pnl: number
    }>
  }
  recent_events: CapitalEvent[]
  error?: string
}

interface AllocationRecommendationArm {
  arm: string
  current_pct: number
  target_pct: number
  delta_pct: number
  target_usd: number
  action: 'INCREASE' | 'REDUCE' | 'HOLD'
  reasons: string[]
}

interface AllocationRecommendationResponse {
  generated_at: string | null
  posture: string
  note: string
  allocatable_base_usd: number
  inputs: {
    perp_mode: string
    risk_mode: string
    memecoin_mode: string
    memecoin_allocator_stance: string
    memecoin_regime_bucket: string
    memecoin_deployment_authority: string
    memecoin_graduation_state: string
    proof_ready: number
    reinforced_pending: number
    discovery_promoted: number
    spot_mode: string
    dominant_book: string
  }
  recommendations: AllocationRecommendationArm[]
  recent_history?: Array<{
    id: number
    ts_utc: string
    posture: string
    note: string | null
    allocatable_base_usd: number
    inputs: AllocationRecommendationResponse['inputs']
    recommendations: AllocationRecommendationArm[]
  }>
  latest_change?: {
    changed: boolean
    current_posture: string | null
    previous_posture: string | null
    changed_arms: Array<{
      arm: string
      from_action: string | null
      to_action: string | null
      from_target_pct: number | null
      to_target_pct: number | null
    }>
  }
  error?: string
}

// /api/home/decision-journal — array of entries
interface DecisionJournalEntry {
  id: number
  created_ts: string
  last_seen_ts?: string | null
  surface_count?: number
  system: string
  symbol: string | null
  recommended_action: string
  priority: string
  reason: string
  operator_decision: string | null
  resolution_status: string
  outcome_24h_pct: number | null
}

// /api/home/action-board — comprehensive operator decision surface
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
  change: string | null
  move_type: string | null
  research_priority: string | null
  triage_tags: string[]
  size_guidance: string | null
  freshness_bucket?: string | null
  data_source?: string | null
  data_freshness_state?: string | null
  data_freshness_issues?: string[]
}

interface GoodBuyItem {
  symbol: string
  mint: string
  lane: string
  lane_sources: string[]
  state: 'BUYABLE' | 'WAIT' | 'BLOCKED' | string
  action: string
  good_buy_score: number
  headline: string
  blockers: string[]
  warnings: string[]
  strengths: string[]
  metrics: {
    quality_score: number
    risk_score: number
    pressure_score: number
    price_usd?: number | null
    liquidity_usd: number
    marketcap_usd: number
    volume_24h_usd: number
    volume_1h_usd: number
    vol_liq_ratio: number
    buy_pressure_1h: number
    trade_1h: number
    change_1h_pct: number
    change_24h_pct: number
    holder_top1_pct?: number | null
    holder_top10_pct?: number | null
    age_minutes?: number | null
  }
  data: {
    identity_status: string
    identity_source?: string | null
    identity_confidence?: string | null
    identity_reason?: string | null
    resolved_mint?: string | null
    data_freshness: string
    data_confidence: string
    market_source: string
    pair_address?: string | null
    updated_at?: string | null
  }
  execution_ticket?: {
    ticket_version: string
    route: string
    mode: string
    executable: boolean
    execution_state: string
    suggested_action: string
    suggested_size_usd: number
    max_size_usd: number
    entry?: {
      reference_price_usd?: number | null
      max_chase_price_usd?: number | null
      reference_marketcap_usd?: number | null
      instruction?: string
    }
    invalidation?: {
      price_usd?: number | null
      pressure_below?: number | null
      change_1h_below_pct?: number | null
      instruction?: string
    }
    take_profit?: {
      tp1_price_usd?: number | null
      tp2_price_usd?: number | null
      tp1_marketcap_usd?: number | null
      tp2_marketcap_usd?: number | null
      runner_rule?: string
    }
    authority?: {
      action_law_state?: string
      highest_permitted_action?: string
      fresh_capital_policy?: string
      lane_suspension_state?: string
      snapshot_age_s?: number | null
    }
    blockers?: string[]
    warnings?: string[]
  }
}

interface GoodBuyBoardV2 {
  policy_version: string
  generated_at: string
  status: string
  headline: string
  summary: {
    buyable: number
    wait: number
    blocked: number
    total: number
    provider_warning?: string | null
  }
  buyable: GoodBuyItem[]
  wait: GoodBuyItem[]
  blocked: GoodBuyItem[]
  provider_context?: {
    hard_block?: string | null
    providers?: Record<string, unknown>
  }
  buy_decision_v1?: BuyDecisionV1
}

interface BuyDecisionV1 {
  version: string
  generated_at: string
  verdict: string
  label: string
  is_buy_now: boolean
  confidence: number
  symbol?: string | null
  mint?: string | null
  lane?: string | null
  failures: string[]
  warnings: string[]
  reason: string
  alert_rule?: {
    kind: string
    severity: string
    message: string
  } | null
  candidate?: {
    symbol?: string | null
    mint?: string | null
    lane?: string | null
    state?: string | null
    score?: number | null
    headline?: string | null
    metrics?: GoodBuyItem['metrics']
    data?: GoodBuyItem['data']
  } | null
  buy_plan?: GoodBuyItem['execution_ticket'] | null
}

interface SnapshotMeta {
  name?: string
  updated_at?: string
  age_seconds?: number | null
  status?: 'FRESH' | 'STALE' | string
  source?: string
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
  good_buy_board_v2?: GoodBuyBoardV2
  buy_decision_v1?: BuyDecisionV1
  _snapshot?: SnapshotMeta
}

interface EarlyRunnerItem {
  symbol: string
  mint: string
  source: string
  status: string
  radar_score: number
  scanner_score: number
  state: string
  escalation_state?: string
  verdict?: string
  action_guidance?: string
  verdict_reasons?: string[]
  entry_verdict?: string
  entry_setup?: string
  entry_trigger?: string
  invalidation_plan?: string
  take_profit_plan?: string
  entry_reasons?: string[]
  alerted_at?: string | null
  execution_state: string
  reason: string
  why_it_matters?: string[]
  risk_flags: string[]
  trust_label: string
  triage_state: string
  entry_context: string
  mcap_usd: number
  liquidity_usd: number
  volume_24h: number
  vol_liq_ratio: number
  change_1h: number
  change_24h: number
  scanned_at: string
  last_seen_at?: string | null
  last_refresh_at?: string | null
  age_minutes?: number | null
  next_check_seconds?: number | null
  replay_state?: string | null
  max_return_pct?: number | null
  missed_reason?: string | null
}

interface EarlyRunnersData {
  generated_at: string
  lookback_hours: number
  headline: string
  summary: {
    total: number
    monitor_now: number
    fast_watch: number
    manual_sniper?: number
    proof_candidate?: number
    look_now?: number
    wait_for_pullback?: number
    do_not_touch?: number
    scout_ready?: number
    wait_pullback?: number
    no_trade?: number
    manual_review_only: number
  }
  runners: EarlyRunnerItem[]
}

interface ConvictionRecoveryToken {
  symbol: string
  mint: string
  profile?: string
  thesis?: string
  expansion_score?: number | null
  expansion_source?: string | null
  expansion_reasons?: string[]
  verdict: string
  entry_state: string
  exit_state: string
  trigger: string
  invalidation: string
  take_profit: string
  reasons?: string[]
  survivability_score?: number
  quality_score?: number
  quality_floor_pass?: boolean
  reference_mcap?: number | null
  known_peak_mcap?: number | null
  from_reference_multiple?: number | null
  drawdown_from_known_peak_pct?: number | null
  mcap_usd?: number | null
  liquidity_usd?: number | null
  volume_24h?: number | null
  vol_liq_ratio?: number | null
  change_1h?: number | null
  change_6h?: number | null
  change_24h?: number | null
  txns_h1?: number | null
  age_days?: number | null
  updated_at?: string | null
  snapshot_as_of?: string | null
  data_freshness?: string | null
  provider_source?: string | null
  stale_age_seconds?: number | null
  last_verdict?: string | null
  last_entry_state?: string | null
  money_state?: string | null
  raw_money_state?: string | null
  good_buy?: boolean
  buy_trigger_score?: number | null
  buy_trigger_reasons?: string[]
  entry_plan?: string | null
  exit_lock?: string | null
  exit_trigger_score?: number | null
  exit_trigger_reasons?: string[]
  memory_snapshot_count?: number | null
  observed_low_mcap?: number | null
  observed_high_mcap?: number | null
  multiple_from_observed_low?: number | null
  drawdown_from_observed_high_pct?: number | null
  spike_count?: number | null
  last_spike_ts?: string | null
  memory_note?: string | null
  confirmation_state?: string | null
  confirmation_count?: number | null
  confirmation_required_count?: number | null
  confirmation_first_seen_at?: string | null
  confirmation_last_seen_at?: string | null
  runner_replay_state?: string | null
  runner_replay_note?: string | null
  runner_replay_blocker?: string | null
  runner_first_seen_at?: string | null
  runner_last_seen_at?: string | null
  runner_max_return_pct?: number | null
  runner_entry_verdict?: string | null
  runner_entry_setup?: string | null
  runner_radar_score?: number | null
  runner_risk_flags?: string[]
}

interface ConvictionRecoveryAlert {
  ts_utc: string
  symbol?: string | null
  mint: string
  alert_type: string
  money_state?: string | null
  buy_trigger_score?: number | null
  exit_trigger_score?: number | null
  data_freshness?: string | null
  sent_telegram?: boolean | number
  message?: string
}

interface ConvictionRecoveryData {
  generated_at: string
  headline: string
  summary: {
    total: number
    auto_expanded?: number
    replay_missed?: number
    replay_tracked_winners?: number
    scout_ready: number
    watch_close: number
    accumulation_zone: number
    exit_watch: number
    fresh?: number
    spot_price_fallback?: number
    cache_fallback?: number
    recent_fallback?: number
    stale?: number
    quality_pass?: number
    quality_reject?: number
    buyable_now?: number
    waiting_confirmation?: number
    waiting_trigger?: number
    protect_profit?: number
    data_wait?: number
    no_trade?: number
  }
  tokens: ConvictionRecoveryToken[]
  alerts?: {
    emitted?: ConvictionRecoveryAlert[]
    recent?: ConvictionRecoveryAlert[]
    telegram_enabled?: boolean
    cooldown_seconds?: number
  }
  expansion?: {
    enabled: boolean
    min_score: number
    candidates_loaded: number
    top: Array<{ symbol?: string | null; mint?: string | null; score?: number | null; source?: string | null; reasons?: string[] }>
  }
  replay?: {
    total: number
    missed: number
    tracked_winners: number
    blockers: Record<string, number>
    top: Array<{
      symbol?: string | null
      mint?: string | null
      first_seen_utc?: string | null
      replay_state?: string | null
      max_return_pct?: number | null
      missed_reason?: string | null
      entry_verdict?: string | null
    }>
  }
}

// Scanner regime — used to annotate MEMECOINS entries on the Action Board
interface HomeScannerDiag {
  stage_counts: { returned_relaxed: number; returned_normal: number }
  top_scored: Array<{
    symbol:                    string
    mint:                      string
    scanner_regime:            'NORMAL' | 'RELAXED_NEAR_MISS'
    scanner_relaxation_reason: string | null
  }>
}

// Patch 300 — /api/home/posture
interface PostureLane {
  posture: string
  detail:  string
}
interface PostureData {
  memecoins: PostureLane & { mode: string; lc_count: number; approaching: number }
  perps:     PostureLane & { positions: number; collateral_usd: number }
  spot:      PostureLane & { holdings: number; basket: number }
  whale:     PostureLane & { recent_pass: number }
  system_posture: string
  focus:          string
  generated_at:   string
  // Roadmap 3: compact authority enforcement status
  authority_enforcement?: {
    enforce_active: boolean
    highest_permitted_action: string
    rank: number
    fresh_capital_policy: string
    new_entry_verdict: string
    action_law_state: string
  } | null
}

interface WatchToEntryBlocker {
  key: string
  count?: number
}

interface WatchToEntryItem {
  symbol?: string | null
  mint?: string | null
  score?: number | null
  proof_score?: number | null
  market_quality_score?: number | null
  primary_blocker?: string | null
  remaining_blocker_count?: number | null
  remaining_blocker_keys?: string[]
  true_last_blocker?: boolean
  is_established_runner?: boolean
  buy_pressure?: number | null
  vol_acceleration?: number | null
  generated_at?: string | null
  ts_utc?: string | null
}

interface WatchToEntryStatus {
  state?: string
  generated_at?: string | null
  tracked?: WatchToEntryItem[]
  tracking_count?: number
  true_last_blocker_count?: number
  recent_events?: Array<{ ts_utc?: string | null; symbol?: string | null; mint?: string | null; event?: string | null }>
}

interface WatchToEntryReplay {
  state?: string
  headline?: string
  tracking_count?: number
  true_last_blocker_count?: number
  multi_blocked_count?: number
  established_runner_count?: number
  top_blockers?: WatchToEntryBlocker[]
  surface_summary?: {
    surfaced?: number
    outcomes_complete?: number
    win_rate_pct?: number | null
    avg_return_24h_pct?: number | null
    quality_state?: string
    horizons?: Record<string, { complete?: number; win_rate_pct?: number | null; avg_return_pct?: number | null }>
  }
  next_step?: string
}

interface EstablishedRunnerCoverage {
  state?: string
  headline?: string
  tracked_count?: number
  covered_count?: number
  missing_count?: number
  watching_count?: number
  proof_pipeline_count?: number
  scanner_seen_count?: number
  next_step?: string
}

type RunnerReviewDecision = 'WATCH' | 'PASS' | 'MANUAL_BUY' | 'TOO_LATE'
type MemecoinManualReviewDecision = 'BUY' | 'WATCH' | 'SKIP' | 'TOO_LATE' | 'BAD_CA' | 'NEEDS_MORE_PROOF'

interface MemecoinManualReviewLog {
  id?: number | null
  created_at?: string | null
  mint?: string | null
  symbol?: string | null
  decision?: MemecoinManualReviewDecision | string | null
  operator_note?: string | null
  source?: string | null
  research_score?: number | null
  entry_zone?: string | null
  position_stance?: string | null
  exit_state?: string | null
  entry_marketcap?: number | null
  current_marketcap?: number | null
  return_1h_pct?: number | null
  return_4h_pct?: number | null
  return_24h_pct?: number | null
  max_return_pct?: number | null
  outcome_label?: string | null
  last_eval_at?: string | null
}

interface RunnerReviewLog {
  id: number
  created_ts: string
  symbol?: string | null
  mint: string
  runner_state?: string | null
  decision: RunnerReviewDecision
  operator_note?: string | null
  blocker_key?: string | null
  outcome_status?: string | null
  outcome_label?: string | null
  tracker_label?: string | null
  paper_trade_state?: string | null
  entry_price?: number | null
  entry_marketcap?: number | null
  entry_stats_ts?: string | null
  entry_capture_status?: string | null
  entry_market_source?: string | null
  entry_data_age_seconds?: number | null
  current_price?: number | null
  current_marketcap?: number | null
  current_stats_ts?: string | null
  current_market_source?: string | null
  current_data_age_seconds?: number | null
  return_1h_pct?: number | null
  return_4h_pct?: number | null
  current_return_pct?: number | null
  max_return_pct?: number | null
  min_return_pct?: number | null
  drawdown_from_max_pct?: number | null
  return_24h_pct?: number | null
  return_72h_pct?: number | null
  suggested_decision?: RunnerReviewDecision | null
  suggestion_confidence?: number | null
  decision_alignment?: string | null
  decision_source?: string | null
  best_horizon?: string | null
  exit_signal?: string | null
  exit_urgency?: string | null
  exit_signal_reason?: string | null
  exit_signal_ts?: string | null
  entry_quality_score?: number | null
  entry_quality_label?: string | null
  entry_quality_reason?: string | null
  signal_decay_score?: number | null
  signal_decay_label?: string | null
  paper_position_units?: number | null
  paper_position_label?: string | null
  simulated_pnl_units?: number | null
  management_action?: string | null
  management_reason?: string | null
  follow_up_status?: string | null
  follow_up_due_ts?: string | null
  follow_up_reason?: string | null
  follow_up_due?: boolean
  material_change?: boolean
  recommended_follow_up?: string | null
  current_runner?: {
    runner_state?: string | null
    blocker_key?: string | null
    proof_status?: string | null
  } | null
}

interface RunnerReviewOpportunity {
  symbol: string
  mint: string
  proof_status?: string | null
  runner_state: string
  blocker_key?: string | null
  remaining_blocker_keys?: string[]
  blocker_reasons?: string[]
  manual_review_required?: boolean
  operator_hint?: string | null
  profile?: string | null
  thesis?: string | null
  score?: number | null
  proof_score?: number | null
  readiness_score?: number | null
  market_quality_score?: number | null
  market_quality_verdict?: string | null
  buy_pressure?: number | null
  vol_acceleration?: number | null
  entry_window?: string | null
  fuel_quality?: string | null
  move_phase?: string | null
  first_leg_confirmed?: number | null
  suggested_decision?: RunnerReviewDecision | null
  suggestion_label?: string | null
  suggestion_confidence?: number | null
  suggestion_urgency?: string | null
  suggestion_reason?: string | null
  next_trigger?: string | null
  invalid_if?: string | null
  entry_checklist?: string[]
  learning_prompt?: string | null
  last_decision?: RunnerReviewLog | null
  snapshot?: unknown
}

interface RunnerReviewData {
  generated_at: string
  headline: string
  proof_input_source?: string | null
  summary: {
    total: number
    ready: number
    needs_momentum: number
    extension_risk: number
    decisions_logged: number
    decision_needed?: number
    suggested_manual_buy?: number
    suggested_watch?: number
    suggested_avoid?: number
  }
  decision_bridge?: {
    state?: string
    headline?: string
    primary_prompt?: string
    manual_buy_suggestions?: number
    watch_suggestions?: number
    avoid_suggestions?: number
  }
  opportunities: RunnerReviewOpportunity[]
  recent_decisions: RunnerReviewLog[]
  outcome_summary?: {
    summary: {
      total: number
      active: number
      complete: number
      good: number
      bad: number
    }
    by_decision: Record<string, {
      count: number
      complete: number
      avg_max_return_pct: number
      good: number
      bad: number
      quality_score: number
    }>
    by_label: Record<string, number>
    recent: RunnerReviewLog[]
    follow_up?: RunnerReviewFollowUpData
    trade_tracker?: RunnerTradeTrackerData
  }
  follow_up?: RunnerReviewFollowUpData
  trade_tracker?: RunnerTradeTrackerData
  last_evaluation?: {
    evaluated_at: string
    reviewed: number
    updated: number
    completed: number
    skipped_no_market: number
  }
}

interface RunnerTradeTrackerData {
  generated_at: string
  headline?: string
  summary: {
    total: number
    trackable: number
    active: number
    complete: number
    manual_buy: number
    watch: number
    system_paper?: number
    missing_market: number
    protect_profit?: number
    scale_out?: number
    exit_now?: number
    expired_signal?: number
    exit_review?: number
    hold_observe?: number
    good: number
    bad: number
    avg_max_return_pct: number
    avg_min_return_pct: number
    paper_units?: number
    simulated_pnl_units?: number
  }
  active: RunnerReviewLog[]
  recent: RunnerReviewLog[]
}

interface MemecoinResearchDossier {
  mint: string
  symbol: string
  generated_at: string
  narrative: string
  memory_type: string
  archetype: string
  community_proof: string
  catalyst: string
  tradeability: string
  risk_summary: string
  system_thesis: string
  invalidation: string
  catalyst_type?: string | null
  why_now?: string | null
  rotation_state?: string | null
  outcome_tuning_bonus?: number | null
  last_catalyst_type?: string | null
  last_catalyst_ts?: string | null
  last_catalyst_confidence?: number | null
  last_catalyst_headline?: string | null
  catalyst_event_count?: number | null
  good_coin_score?: number | null
  good_coin_status?: string | null
  good_coin_reasons?: string[]
  good_coin_blockers?: string[]
  conviction_band?: string | null
  catalyst_strength_score?: number | null
  catalyst_strength_label?: string | null
  too_late_score?: number | null
  too_late_label?: string | null
  too_late_reasons?: string[]
  operator_priority?: number | null
  entry_state?: string | null
  entry_score?: number | null
  entry_blocker?: string | null
  entry_instruction?: string | null
  entry_reasons?: string[]
  entry_blockers?: string[]
  execution_alignment_state?: string | null
  execution_alignment_label?: string | null
  execution_alignment_confidence?: number | null
  execution_deployable?: boolean | null
  execution_blockers?: string[]
  signal_quality_tier?: string | null
  signal_quality_score?: number | null
  signal_quality?: {
    tier?: string | null
    score?: number | null
    reasons?: string[]
    blockers?: string[]
  } | null
  execution_alignment?: {
    state?: string | null
    label?: string | null
    confidence?: number | null
    deployable?: boolean | null
    paper_ready?: boolean | null
    research_hot?: boolean | null
    blockers?: string[]
    instruction?: string | null
  } | null
  entry_timing?: {
    state?: string | null
    score?: number | null
    last_blocker?: string | null
    instruction?: string | null
    reasons?: string[]
    blockers?: string[]
  } | null
  entry_zone?: {
    zone?: string | null
    label?: string | null
    confidence?: number | null
    action?: string | null
    ideal_entry?: string | null
    current_marketcap?: number | null
    pullback_marketcap?: number | null
    chase_limit_marketcap?: number | null
    base_reentry_marketcap?: number | null
    confirmation_needed?: string | null
    reasons?: string[]
    blockers?: string[]
  } | null
  trade_thesis?: {
    headline?: string | null
    decision?: string | null
    conviction?: string | null
    action?: string | null
    why_this_coin?: string | null
    why_now?: string | null
    ideal_entry?: string | null
    confirmation_needed?: string | null
    what_must_stay_true?: string[]
    confirmations?: string[]
    risks?: string[]
    invalidation?: string | null
    risk_summary?: string | null
    confidence?: number | null
    attention_label?: string | null
  } | null
  position_plan?: {
    stance?: string | null
    size_units?: number | null
    size_label?: string | null
    starter_marketcap?: number | null
    add_marketcap?: number | null
    invalid_marketcap?: number | null
    trim1_marketcap?: number | null
    trim2_marketcap?: number | null
    runner_marketcap?: number | null
    starter?: string | null
    add_rule?: string | null
    invalid_stop?: string | null
    trim_plan?: string | null
    runner_plan?: string | null
    max_risk_note?: string | null
  } | null
  exit_intelligence?: {
    state?: string | null
    urgency?: string | null
    action?: string | null
    thesis_health_score?: number | null
    thesis_health_label?: string | null
    hold_condition?: string | null
    trim_condition?: string | null
    protect_condition?: string | null
    sell_condition?: string | null
    reasons?: string[]
    risks?: string[]
    flow_alive?: boolean | null
    flow_fading?: boolean | null
    extension_risk?: boolean | null
    distribution_risk?: boolean | null
  } | null
  manual_review_decision?: MemecoinManualReviewLog | null
  narrative_hooks?: Array<{
    hook?: string | null
    strength?: number | null
    reason?: string | null
  }>
  attention_persistence_score?: number | null
  attention_persistence_label?: string | null
  catalyst_quality_score?: number | null
  catalyst_quality_label?: string | null
  contradictions?: Array<{
    key?: string | null
    severity?: string | null
    detail?: string | null
  }>
  comparable_runner?: string | null
  comparable_runner_confidence?: number | null
  sustainability_score?: number | null
  sustainability_label?: string | null
  sustainability_checks?: Array<{
    check?: string | null
    pass?: boolean | null
    detail?: string | null
  }>
  narrative_intelligence?: {
    research_note?: string | null
    attention_reason?: string | null
    catalyst_quality_reason?: string | null
  } | null
  wallet_cluster_intelligence?: {
    cluster_label?: string | null
    accumulation_state?: string | null
    insider_like_score?: number | null
    distribution_risk_score?: number | null
    accumulation_score?: number | null
    early_buyer_count?: number | null
    fresh_wallet_count?: number | null
    common_funder_count?: number | null
    linked_wallet_count?: number | null
    repeat_operator_count?: number | null
    repeat_operator_score?: number | null
    wallet_trust_score?: number | null
    wallet_trust_label?: string | null
    confidence_score?: number | null
    reasons?: string[]
    warnings?: string[]
    top_repeat_wallets?: Array<{
      wallet_address?: string | null
      memory_score?: number | null
      memory_label?: string | null
      trust_score?: number | null
      trust_label?: string | null
      attributed_signal_count?: number | null
      attributed_runner_count?: number | null
      early_mint_count?: number | null
      repeat_runner_count?: number | null
      best_max_return_pct?: number | null
    }>
    attribution_summary?: {
      sample_n?: number | null
      avg_max_return_pct?: number | null
      runner_n?: number | null
      runner_rate_pct?: number | null
      distribution_warning_hit_n?: number | null
      repeat_avg_max_return_pct?: number | null
    } | null
    latest_alert?: {
      severity?: string | null
      score?: number | null
      headline?: string | null
      detail?: string | null
      ts_utc?: string | null
    } | null
  } | null
  action: string
  research_score: number
  narrative_score: number
  memory_score: number
  community_score: number
  catalyst_score: number
  tradeability_score: number
  risk_score: number
  outcome_sample_n: number
  outcome_good_n: number
  outcome_bad_n: number
  avg_max_return_pct: number
  marketcap?: number | null
  liquidity?: number | null
  volume_24h?: number | null
  buy_pressure?: number | null
  vol_acceleration?: number | null
  runner_state?: string | null
  proof_status?: string | null
  blocker_key?: string | null
  tags?: string[]
}

interface MemecoinCatalystEvent {
  id?: number
  mint: string
  symbol?: string | null
  event_ts: string
  event_type: string
  source?: string | null
  confidence?: number | null
  headline?: string | null
  detail?: string | null
  narrative?: string | null
}

interface MemecoinResearchData {
  generated_at: string
  headline: string
  proof_input_source?: string | null
  summary: {
    total: number
    actions: Record<string, number>
    known_memory_count: number
    symbol_memory_count?: number
    outcome_linked_count: number
    catalyst_linked_count?: number
    conviction_bands?: Record<string, number>
    good_coin_pass_count?: number
    strong_catalyst_count?: number
    too_late_count?: number
    insider_like_count?: number
    distribution_risk_count?: number
    repeat_operator_count?: number
    entry_now_count?: number
    deployable_now_count?: number
    paper_entry_now_count?: number
    research_hot_count?: number
    armed_count?: number
    exit_pressure_count?: number
    durable_attention_count?: number
    active_attention_count?: number
    structural_catalyst_count?: number
    confirmed_flow_count?: number
    contradiction_count?: number
    sustainable_count?: number
    fade_risk_count?: number
  }
  operator_action?: {
    mode: string
    headline: string
    instruction: string
    counts: {
      buyable: number
      triggered: number
      watch: number
      too_late: number
      ignored: number
    }
    top?: {
      symbol?: string | null
      mint?: string | null
      action?: string | null
      conviction_band?: string | null
      operator_priority?: number | null
      research_score?: number | null
      good_coin_score?: number | null
      good_coin_status?: string | null
      catalyst_strength_score?: number | null
      catalyst_strength_label?: string | null
      too_late_score?: number | null
      too_late_label?: string | null
      entry_state?: string | null
      entry_score?: number | null
      entry_blocker?: string | null
      entry_instruction?: string | null
      execution_alignment_state?: string | null
      execution_alignment_label?: string | null
      execution_alignment_confidence?: number | null
      execution_deployable?: boolean | null
      execution_blockers?: string[]
      why_now?: string | null
      invalidation?: string | null
      entry_zone?: MemecoinResearchDossier['entry_zone']
      trade_thesis?: MemecoinResearchDossier['trade_thesis']
      position_plan?: MemecoinResearchDossier['position_plan']
      exit_intelligence?: MemecoinResearchDossier['exit_intelligence']
    } | null
  }
  entry_signals?: {
    generated_at: string
    summary: {
      total: number
      state: string
      entry_now?: number
      armed?: number
      open_paper?: number
      calibration_sample_n?: number
      completed_paper?: number
      judged_paper?: number
      tracking_paper?: number
      stale_no_move?: number
    }
    signals: Array<{
      id: number
      created_at: string
      mint: string
      symbol?: string | null
      entry_state: string
      entry_score?: number | null
      last_blocker?: string | null
      guard_status?: string | null
      entry_price?: number | null
      entry_marketcap?: number | null
      return_15m_pct?: number | null
      return_1h_pct?: number | null
      return_4h_pct?: number | null
      return_24h_pct?: number | null
      max_return_pct?: number | null
      drawdown_from_max_pct?: number | null
      outcome_label?: string | null
      paper_status?: string | null
      reasons?: string[]
      blockers?: string[]
    }>
    tuning?: Array<{
      entry_state: string
      sample_n: number
      judged_n?: number
      win_n: number
      big_win_n?: number
      giveback_n?: number
      stale_n?: number
      win_rate_pct: number
      avg_max_return_pct: number
      avg_entry_score: number
    }>
    outcome_calibration?: {
      generated_at?: string
      summary?: {
        sample_n?: number
        judged_n?: number
        win_n?: number
        loss_n?: number
        win_rate_pct?: number
        state?: string
        needs_more_outcomes?: boolean
      }
      by_entry_zone?: Array<{
        group?: string
        label?: string
        sample_n?: number
        judged_n?: number
        win_n?: number
        loss_n?: number
        giveback_n?: number
        stale_n?: number
        win_rate_pct?: number
        avg_max_return_pct?: number
        avg_drawdown_from_max_pct?: number
        avg_entry_score?: number
      }>
      by_position_stance?: Array<{
        group?: string
        label?: string
        sample_n?: number
        judged_n?: number
        win_rate_pct?: number
        avg_max_return_pct?: number
      }>
      by_exit_state?: Array<{
        group?: string
        label?: string
        sample_n?: number
        judged_n?: number
        win_rate_pct?: number
        avg_max_return_pct?: number
      }>
    }
  }
  manual_review_bridge?: {
    generated_at?: string | null
    prompt?: string | null
    summary?: {
      total?: number | null
      needs_review?: number | null
      undecided?: number | null
      judged_n?: number | null
      good_n?: number | null
      missed_n?: number | null
      good_rate_pct?: number | null
      state?: string | null
    }
    recent?: MemecoinManualReviewLog[]
    calibration?: {
      state?: string | null
      guidance_state?: string | null
      headline?: string | null
      actions?: string[]
      buy_signal?: {
        decision?: string | null
        sample_n?: number | null
        judged_n?: number | null
        good_n?: number | null
        missed_n?: number | null
        good_rate_pct?: number | null
        avg_max_return_pct?: number | null
      } | null
      watch_signal?: {
        decision?: string | null
        sample_n?: number | null
        judged_n?: number | null
        good_n?: number | null
        missed_n?: number | null
        good_rate_pct?: number | null
        avg_max_return_pct?: number | null
      } | null
      by_decision?: Array<{
        decision?: string | null
        sample_n?: number | null
        judged_n?: number | null
        good_n?: number | null
        missed_n?: number | null
        good_rate_pct?: number | null
        avg_max_return_pct?: number | null
      }>
    }
    missed_runner_replay?: Array<MemecoinManualReviewLog & {
      blocker?: string | null
      entry_state?: string | null
      execution_alignment_state?: string | null
      headline?: string | null
      trigger_contract?: string | null
      why_it_matters?: string | null
      lesson?: string | null
    }>
    refresh?: {
      scanned?: number | null
      updated?: number | null
      judged?: number | null
      error?: string | null
    }
  }
  rotation_board?: Array<{
    narrative: string
    state: string
    heat_score: number
    count: number
    paper_entry_count: number
    manual_review_count: number
    outcome_sample_n: number
    avg_max_return_pct: number
    top_symbols: Array<{ symbol: string; action: string; score: number }>
  }>
  established_runner_watchlist?: {
    generated_at?: string | null
    summary?: {
      total?: number
      deployable?: number
      paper_ready?: number
      research_hot?: number
      last_blocker?: number
      too_late?: number
    }
    items?: Array<{
      symbol?: string | null
      mint?: string | null
      lane_state?: string | null
      execution_alignment_state?: string | null
      entry_state?: string | null
      last_blocker?: string | null
      research_score?: number | null
      operator_priority?: number | null
      attention_persistence_label?: string | null
      catalyst_quality_label?: string | null
      sustainability_label?: string | null
      comparable_runner?: string | null
      too_late_label?: string | null
      marketcap?: number | null
      liquidity?: number | null
      volume_24h?: number | null
      why_now?: string | null
    }>
  }
  opportunity_ledger?: {
    generated_at?: string | null
    headline?: string | null
    summary?: {
      total?: number
      entry_ready?: number
      paper_ready?: number
      watching?: number
      blocked_improving?: number
      missed_runner?: number
      too_late?: number
      faded?: number
      outcomes?: Record<string, number>
    }
    lessons?: {
      best_blocker?: BlockerLesson | null
      worst_blocker?: BlockerLesson | null
      missed_winner?: OpportunityLedgerItem | null
      correctly_avoided?: OpportunityLedgerItem | null
      blockers?: BlockerLesson[]
      triggers?: Array<{
        trigger_verdict?: string | null
        sample_n?: number | null
        helpful_n?: number | null
        harmful_n?: number | null
        helpful_rate_pct?: number | null
      }>
    }
    trust_calibration?: {
      generated_at?: string | null
      waiting_vs_missing?: {
        state?: string | null
        judged_n?: number | null
        waiting_helped_n?: number | null
        waiting_hurt_n?: number | null
        waiting_helped_pct?: number | null
        waiting_hurt_pct?: number | null
      }
      blocker_trust?: Array<BlockerLesson & {
        judged_n?: number | null
        trust_score?: number | null
        recommendation?: string | null
      }>
      trigger_replay?: Array<{
        trigger_verdict?: string | null
        sample_n?: number | null
        helpful_n?: number | null
        harmful_n?: number | null
        helpful_rate_pct?: number | null
        judged_n?: number | null
        timing_label?: string | null
      }>
      missed_runner_autopsies?: Array<{
        symbol?: string | null
        mint?: string | null
        outcome_label?: string | null
        last_blocker?: string | null
        root_cause?: string | null
        max_return_from_watch_pct?: number | null
        why?: string | null
        policy_hint?: string | null
        trigger_contract?: string | null
        why_now?: string | null
      }>
      policy_guidance?: {
        state?: string | null
        actions?: string[]
        live_policy?: string | null
      }
      dashboard_questions?: Array<{
        label?: string | null
        answer?: string | null
        state?: string | null
      }>
      recent_event_count?: number | null
    }
    shadow_strategy_lab?: {
      generated_at?: string | null
      summary?: {
        strategy_count?: number | null
        sample_n?: number | null
        entered_n?: number | null
        judged_n?: number | null
        state?: string | null
        earlier_entry_beating_waiting?: boolean | null
      }
      best_strategy?: ShadowStrategyRank | null
      worst_strategy?: ShadowStrategyRank | null
      ranking?: ShadowStrategyRank[]
      by_blocker?: Array<{
        blocker?: string | null
        sample_n?: number | null
        best_strategy?: string | null
        best_score?: number | null
      }>
      open_examples?: ShadowStrategyExample[]
      policy_bridge?: {
        state?: string | null
        actions?: string[]
        live_policy?: string | null
      }
    }
    escalation_queue?: {
      generated_at?: string | null
      summary?: {
        total?: number
        newly_actionable?: number
        trigger_cleared?: number
        missed_runner?: number
        faded_after_ready?: number
      }
      items?: OpportunityEventItem[]
    }
    ready_soon?: OpportunityLedgerItem[]
    blocked_improving?: OpportunityLedgerItem[]
    missed_or_learn?: OpportunityLedgerItem[]
  }
  outcome_tuning?: {
    state: string
    rows: Array<{
      narrative: string
      sample_n: number
      avg_max_return_pct: number
      score_bonus: number
      state: string
    }>
  }
  catalysts?: {
    generated_at: string
    summary: {
      total_24h: number
      by_type: Record<string, number>
      state: string
    }
    recent: MemecoinCatalystEvent[]
  }
  catalyst_refresh?: {
    candidate_events?: number
    external_events?: number
    generated_events?: number
    inserted?: number
    skipped_dedupe?: number
    by_type?: Record<string, number>
    include_external?: boolean
  }
  calibration?: {
    state: string
    headline: string
    snapshot_sample_n: number
    outcome_sample_n: number
    snapshots: Array<{
      conviction_band: string
      sample_n: number
      avg_score: number
      avg_priority: number
      avg_catalyst: number
      avg_too_late: number
    }>
    outcomes: Array<{
      conviction_band: string
      sample_n: number
      good_n: number
      bad_n: number
      good_rate_pct: number
      avg_max_return_pct: number
      avg_return_pct: number
    }>
  }
  dossiers: MemecoinResearchDossier[]
}

interface RunnerReviewFollowUpData {
  generated_at: string
  summary: {
    total: number
    due: number
    active_watch: number
    managed: number
    suppressed: number
  }
  followups: RunnerReviewLog[]
}

type RunnerReviewDecisionPayload = RunnerReviewOpportunity & {
  decision: RunnerReviewDecision
  operator_note?: string
}

type MemecoinManualReviewDecisionPayload = MemecoinResearchDossier & {
  decision: MemecoinManualReviewDecision
  operator_note?: string
  source?: string
  dossier?: MemecoinResearchDossier
}

interface SystemAuditData {
  generated_at: string
  watch_to_entry?: WatchToEntryStatus
  watch_to_entry_replay?: WatchToEntryReplay
  established_runner_coverage?: EstablishedRunnerCoverage
  runtime?: {
    data_confidence?: {
      status: 'HIGH' | 'MEDIUM' | 'LOW' | string
      headline: string
      issues: string[]
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
    provider_recovery?: {
      headline: string
      limiting_count: number
      feeds: Array<{
        label: string
        key: string
        status: string
        raw_status?: string
        recovery_state?: string | null
        cooldown_until?: string | null
        consecutive_failures?: number
        reason?: string | null
        detail?: string | null
      }>
    }
    recovery_checklist?: Array<{
      key: string
      label: string
      state: string
      detail: string
      next_step: string
    }>
    source_strategy?: {
      independent_mode?: boolean
      detail?: string | null
      primary_market_source?: string | null
      market_status?: string | null
    }
    memecoin_input?: {
      watch_to_entry?: WatchToEntryStatus
      watch_to_entry_replay?: WatchToEntryReplay
      established_runner_coverage?: EstablishedRunnerCoverage
    }
  }
}

interface BlockerLesson {
  blocker?: string | null
  sample_n?: number | null
  good_n?: number | null
  bad_n?: number | null
  accuracy_pct?: number | null
  avg_max_return_pct?: number | null
}

interface ShadowStrategyRank {
  strategy_key?: string | null
  sample_n?: number | null
  entered_n?: number | null
  judged_n?: number | null
  win_n?: number | null
  loss_n?: number | null
  win_rate_pct?: number | null
  avg_max_return_pct?: number | null
  avg_4h_return_pct?: number | null
}

interface ShadowStrategyExample {
  id?: number | null
  mint?: string | null
  symbol?: string | null
  strategy_key?: string | null
  entry_status?: string | null
  entry_marketcap?: number | null
  max_return_pct?: number | null
  outcome_label?: string | null
  entry_reason?: string | null
}

interface OpportunityEventItem {
  id?: number | null
  event_ts?: string | null
  mint?: string | null
  symbol?: string | null
  event_type?: string | null
  previous_state?: string | null
  new_state?: string | null
  trigger_cleared?: number | boolean | null
  last_blocker?: string | null
  research_score?: number | null
  signal_quality_tier?: string | null
  signal_quality_score?: number | null
  max_return_from_watch_pct?: number | null
  outcome_label?: string | null
  alert_priority?: number | null
  headline?: string | null
  detail?: string | null
  trigger_contract?: string | null
  status?: Record<string, unknown>
}

interface OpportunityLedgerItem {
  mint?: string | null
  symbol?: string | null
  first_seen_at?: string | null
  last_seen_at?: string | null
  state?: string | null
  previous_state?: string | null
  state_changed_at?: string | null
  research_score?: number | null
  signal_quality_tier?: string | null
  signal_quality_score?: number | null
  execution_alignment_state?: string | null
  entry_state?: string | null
  last_blocker?: string | null
  trigger_contract?: string | null
  missed_reason?: string | null
  lesson?: string | null
  current_marketcap?: number | null
  max_observed_marketcap?: number | null
  max_observed_return_pct?: number | null
  outcome_label?: string | null
  return_15m_pct?: number | null
  return_1h_pct?: number | null
  return_4h_pct?: number | null
  return_24h_pct?: number | null
  max_return_from_watch_pct?: number | null
  blocker_verdict?: string | null
  trigger_verdict?: string | null
  learned_summary?: string | null
  status?: {
    action?: string | null
    conviction_band?: string | null
    why_now?: string | null
    invalidation?: string | null
    [key: string]: unknown
  }
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmtUsd(n: number | null | undefined): string {
  if (n == null) return '—'
  if (Math.abs(n) >= 1_000_000) return `$${(n / 1_000_000).toFixed(1)}M`
  if (Math.abs(n) >= 1_000)     return `$${(n / 1_000).toFixed(1)}K`
  return `$${n.toFixed(0)}`
}

function fmtTokenPrice(n: number | null | undefined): string {
  if (n == null || Number.isNaN(Number(n))) return '—'
  const v = Number(n)
  if (Math.abs(v) >= 1) return `$${v.toFixed(2)}`
  if (Math.abs(v) >= 0.01) return `$${v.toFixed(4)}`
  return `$${v.toPrecision(4)}`
}

function fmtAge(ts: string | null): string {
  if (!ts) return '—'
  // Bare 'YYYY-MM-DD HH:MM:SS' has no TZ marker — treat as UTC by appending Z.
  // Strings already carrying an offset (+HH:MM) or Z parse correctly as-is.
  const normalized = /[Z+]/.test(ts.slice(10)) ? ts : ts + 'Z'
  const ms = new Date(normalized).getTime()
  if (isNaN(ms)) return '—'
  const diff = (Date.now() - ms) / 1000
  if (diff < 60)    return `${Math.floor(diff)}s ago`
  if (diff < 3600)  return `${Math.floor(diff / 60)}m ago`
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`
  return `${Math.floor(diff / 86400)}d ago`
}

function tsMs(ts: string | null | undefined): number | null {
  if (!ts) return null
  const normalized = /[Z+]/.test(ts.slice(10)) ? ts : ts + 'Z'
  const ms = new Date(normalized).getTime()
  return Number.isFinite(ms) ? ms : null
}

function freshnessState(
  timestamps: Array<string | null | undefined>,
  freshMs: number,
  agingMs: number,
): { label: string; tone: string; updatedAt: string | null } {
  const values = timestamps
    .map(tsMs)
    .filter((v): v is number => v != null)
  if (!values.length) return { label: 'waiting on live reads', tone: '#7f95a8', updatedAt: null }
  const latest = Math.max(...values)
  const age = Date.now() - latest
  if (age <= freshMs) return { label: 'fresh', tone: '#00d48a', updatedAt: new Date(latest).toISOString() }
  if (age <= agingMs) return { label: 'aging', tone: '#f59e0b', updatedAt: new Date(latest).toISOString() }
  return { label: 'stale', tone: '#ef4444', updatedAt: new Date(latest).toISOString() }
}

function fmtFixed(value: number | null | undefined, digits = 0): string {
  if (value == null || Number.isNaN(Number(value))) return '—'
  return Number(value).toFixed(digits)
}

function fmtPct(value: number | null | undefined, digits = 1): string {
  if (value == null || Number.isNaN(Number(value))) return '—'
  const n = Number(value)
  return `${n > 0 ? '+' : ''}${n.toFixed(digits)}%`
}

function shortTokenAddress(value: string | null | undefined): string {
  const addr = String(value || '').trim()
  if (!addr) return ''
  if (addr.length <= 12) return addr
  return `${addr.slice(0, 6)}…${addr.slice(-6)}`
}

function TokenAddressChip({
  value,
  prefix = 'CA',
  size = 'compact',
}: {
  value: string | null | undefined
  prefix?: string
  size?: 'compact' | 'roomy'
}) {
  const addr = String(value || '').trim()
  const [copied, setCopied] = React.useState(false)
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

  const compact = size === 'compact'
  return (
    <button
      type="button"
      onClick={copy}
      title={copied ? 'Copied' : `${addr} · click to copy`}
      style={{
        ...MONO,
        fontSize: compact ? 7 : 8,
        color: copied ? '#00d48a' : 'var(--recessed)',
        background: copied ? 'rgba(0,212,138,0.08)' : 'rgba(255,255,255,0.03)',
        border: `1px solid ${copied ? 'rgba(0,212,138,0.24)' : 'rgba(255,255,255,0.08)'}`,
        borderRadius: compact ? 2 : 3,
        padding: compact ? '1px 4px' : '3px 6px',
        width: 'fit-content',
        cursor: 'copy',
      }}
    >
      {copied ? 'COPIED' : `${prefix} ${shortTokenAddress(addr)}`}
    </button>
  )
}

const MONO: React.CSSProperties = { fontFamily: 'JetBrains Mono, monospace' }
const DAILY_INTELLIGENCE_REV = 'outcome-loop-v1'

const LANE_COLOR: Record<string, string> = {
  MEMECOINS: '#a78bfa',
  SPOT: '#60a5fa',
  PERPS: '#f59e0b',
  PERP: '#f59e0b',
  WHALE: '#06b6d4',
  CONFLUENCE: '#f472b6',
}

const LANE_SHORT: Record<string, string> = {
  MEMECOINS: 'MC',
  SPOT: 'SP',
  PERPS: 'PP',
  PERP: 'PP',
  WHALE: 'WH',
  CONFLUENCE: 'CF',
}

// ── System card colors ────────────────────────────────────────────────────────

const C = {
  tiers:       { main: '#00d48a', bg: 'rgba(0,212,138,0.05)',   border: 'rgba(0,212,138,0.18)'  },
  memecoins:   { main: '#60a5fa', bg: 'rgba(96,165,250,0.05)',  border: 'rgba(96,165,250,0.18)' },
  spot:        { main: '#f59e0b', bg: 'rgba(245,158,11,0.05)',  border: 'rgba(245,158,11,0.18)' },
  whale_watch: { main: '#a78bfa', bg: 'rgba(167,139,250,0.05)', border: 'rgba(167,139,250,0.18)'},
  speculation_heat: { main: '#ef4444', bg: 'rgba(239,68,68,0.05)', border: 'rgba(239,68,68,0.18)' },
}

// ── System Card ───────────────────────────────────────────────────────────────

function SystemCard({ sys, mode, modeColor, title, children }: {
  sys: keyof typeof C
  mode: string
  modeColor: string
  title: string
  children: React.ReactNode
}) {
  const c = C[sys]
	  return (
	    <div style={{
      flex: '1 1 0', minWidth: 220,
      background: c.bg,
      border: `1px solid ${c.border}`,
      borderTop: `2px solid ${c.main}`,
      borderRadius: '0 0 12px 12px',
      padding: '16px 18px',
      display: 'flex', flexDirection: 'column', gap: 12,
      backdropFilter: 'blur(20px) saturate(160%)',
      WebkitBackdropFilter: 'blur(20px) saturate(160%)',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <span style={{ color: c.main, ...MONO, fontWeight: 700, fontSize: 10, letterSpacing: '0.14em' }}>
          {title}
        </span>
        <span className="badge" style={{
          color: modeColor,
          background: `${modeColor}18`,
          border: `1px solid ${modeColor}44`,
          fontSize: 9,
        }}>
          {mode}
        </span>
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
        {children}
      </div>
    </div>
  )
}

function CapitalAllocationPanel({
  data,
  loading,
  recommendation,
  recommendationLoading,
}: {
  data?: CapitalAllocationResponse
  loading: boolean
  recommendation?: AllocationRecommendationResponse
  recommendationLoading: boolean
}) {
  if (loading) {
    return (
      <div className="card" style={{ padding: '12px 16px' }}>
        <div style={{ ...MONO, fontSize: 8, color: 'var(--dim)' }}>loading capital allocation…</div>
      </div>
    )
  }
  if (!data) return null

  const dot = <span style={{ color: 'rgba(255,255,255,0.15)', margin: '0 8px' }}>·</span>
  const pnlColor = (n: number) => n >= 0 ? '#00d48a' : '#ef4444'
  const turnoverPct = data.totals.deploy_events_usd > 0
    ? Math.round((data.totals.release_events_usd / data.totals.deploy_events_usd) * 100)
    : 0
  const portfolioVelocity =
    data.totals.release_events_usd <= 0
      ? { label: 'capital mostly building', color: '#4d6070' }
      : turnoverPct >= 75
        ? { label: 'capital actively recycling', color: '#60a5fa' }
        : turnoverPct >= 30
          ? { label: 'capital rotating selectively', color: '#f59e0b' }
          : { label: 'capital mostly parked', color: '#4d6070' }
  const recByArm = new Map((recommendation?.recommendations ?? []).map(rec => [rec.arm, rec]))
  const latestChange = recommendation?.latest_change
  const postureColor =
    recommendation?.posture === 'LEAN_MEMECOINS' ? '#60a5fa'
    : recommendation?.posture === 'LEAN_PERPS' ? '#00d48a'
    : '#4d6070'

  return (
    <div className="card" style={{
      display: 'flex', flexDirection: 'column', gap: 12,
      padding: '14px 16px',
      borderLeft: '3px solid rgba(96,165,250,0.45)',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 0 }}>
        <span style={{ ...MONO, fontSize: 9, fontWeight: 700, color: '#60a5fa', letterSpacing: '0.10em' }}>
          CAPITAL ALLOCATION
        </span>
        {dot}
        <span style={{ ...MONO, fontSize: 8, color: 'rgba(255,255,255,0.58)' }}>
          deployed {fmtUsd(data.totals.deployed_usd)}
        </span>
        {dot}
        <span style={{ ...MONO, fontSize: 8, color: pnlColor(data.totals.realized_pnl_usd) }}>
          realized {data.totals.realized_pnl_usd >= 0 ? '+' : ''}{fmtUsd(data.totals.realized_pnl_usd)}
        </span>
        {dot}
        <span style={{ ...MONO, fontSize: 8, color: '#60a5fa' }}>
          released {fmtUsd(data.totals.release_events_usd)}
        </span>
        {dot}
        <span style={{ ...MONO, fontSize: 8, color: 'rgba(255,255,255,0.45)' }}>
          open {data.totals.open_positions}
        </span>
        {dot}
        <span style={{ ...MONO, fontSize: 8, color: data.external_flows.net_external_flow_usd >= 0 ? '#60a5fa' : '#ef4444' }}>
          net flow {data.external_flows.net_external_flow_usd >= 0 ? '+' : ''}{fmtUsd(data.external_flows.net_external_flow_usd)}
        </span>
      </div>

      <div style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 8, ...MONO, fontSize: 7 }}>
        <span style={{ color: 'var(--dim)', letterSpacing: '0.10em' }}>LEDGER</span>
        <span style={{ color: 'rgba(255,255,255,0.58)' }}>
          {data.ledger.total_events} event{data.ledger.total_events === 1 ? '' : 's'}
        </span>
        {data.ledger.last_event_ts && (
          <span style={{ color: 'rgba(255,255,255,0.42)' }}>
            last {fmtAge(data.ledger.last_event_ts)}
          </span>
        )}
        {data.ledger.backfill_inserted_total > 0 && (
          <span style={{ color: '#60a5fa' }}>
            history seeded +{data.ledger.backfill_inserted_total}
          </span>
        )}
        <span style={{ color: portfolioVelocity.color }}>
          {portfolioVelocity.label}
          {data.totals.deploy_events_usd > 0 ? ` · ${turnoverPct}% recycled` : ''}
        </span>
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
        <div style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 8, ...MONO, fontSize: 7 }}>
          <span style={{ color: 'var(--dim)', letterSpacing: '0.10em' }}>ALLOCATOR</span>
          {recommendationLoading && (
            <span style={{ color: 'var(--dim)' }}>loading recommendation…</span>
          )}
          {!recommendationLoading && recommendation && (
            <>
              <span style={{ color: postureColor }}>
                {recommendation.posture.toLowerCase().replace(/_/g, ' ')}
              </span>
              <span style={{ color: 'rgba(255,255,255,0.45)' }}>
                base {fmtUsd(recommendation.allocatable_base_usd)}
              </span>
              <span style={{ color: 'rgba(255,255,255,0.45)' }}>
                risk {recommendation.inputs.risk_mode.toLowerCase()}
              </span>
              <span style={{ color: 'rgba(255,255,255,0.45)' }}>
                memecoin {recommendation.inputs.memecoin_allocator_stance.toLowerCase().replace(/_/g, ' ')}
              </span>
            </>
          )}
        </div>
        {!recommendationLoading && recommendation && (
          <div style={{ ...MONO, fontSize: 7, color: '#4d6070' }}>
            {recommendation.note}
          </div>
        )}
        {!recommendationLoading && latestChange?.changed && (
          <div style={{ ...MONO, fontSize: 7, color: '#60a5fa', display: 'flex', flexWrap: 'wrap', gap: 6 }}>
            <span>
              changed {fmtAge(recommendation?.recent_history?.[0]?.ts_utc ?? recommendation?.generated_at ?? null)}
            </span>
            <span>
              {String(latestChange.previous_posture || 'UNKNOWN').toLowerCase().replace(/_/g, ' ')}
              {' → '}
              {String(latestChange.current_posture || 'UNKNOWN').toLowerCase().replace(/_/g, ' ')}
            </span>
            {latestChange.changed_arms.slice(0, 3).map(item => (
              <span key={`${item.arm}-${item.to_action}`} style={{ color: 'rgba(255,255,255,0.52)' }}>
                {item.arm} {String(item.from_action || '—').toLowerCase()}→{String(item.to_action || '—').toLowerCase()}
                {item.to_target_pct != null ? ` ${Math.round(item.to_target_pct)}%` : ''}
              </span>
            ))}
          </div>
        )}
      </div>

      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
        {data.arms.map(arm => {
          const accent = arm.arm === 'perps' ? '#00d48a' : arm.arm === 'memecoins' ? '#60a5fa' : '#f59e0b'
          const releasedPct = arm.deploy_events_usd > 0
            ? Math.round((arm.release_events_usd / arm.deploy_events_usd) * 100)
            : 0
          const velocity =
            arm.release_events_usd <= 0
              ? { label: 'building', color: '#4d6070' }
              : releasedPct >= 75
                ? { label: 'recycling', color: '#60a5fa' }
                : releasedPct >= 30
                  ? { label: 'rotating', color: '#f59e0b' }
                  : { label: 'mostly parked', color: '#4d6070' }
          return (
            <div key={arm.arm} style={{
              flex: '1 1 220px',
              background: `${accent}08`,
              border: `1px solid ${accent}22`,
              borderRadius: 6,
              padding: '10px 12px',
              display: 'flex',
              flexDirection: 'column',
              gap: 6,
            }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
                <span style={{ ...MONO, fontSize: 8, fontWeight: 700, color: accent, letterSpacing: '0.10em' }}>
                  {arm.arm.toUpperCase()}
                </span>
                <span style={{ ...MONO, fontSize: 8, color: 'rgba(255,255,255,0.42)' }}>
                  {arm.open_positions} open
                </span>
              </div>
              <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', ...MONO, fontSize: 8 }}>
                <span style={{ color: 'rgba(255,255,255,0.62)' }}>deployed {fmtUsd(arm.deployed_usd)}</span>
                <span style={{ color: '#60a5fa' }}>
                  released {fmtUsd(arm.release_events_usd)}
                  {arm.deploy_events_usd > 0 ? ` · ${releasedPct}%` : ''}
                </span>
                <span style={{ color: pnlColor(arm.realized_pnl_usd) }}>pnl {arm.realized_pnl_usd >= 0 ? '+' : ''}{fmtUsd(arm.realized_pnl_usd)}</span>
                {arm.open_notional_usd != null && arm.open_notional_usd > 0 && (
                  <span style={{ color: 'rgba(255,255,255,0.42)' }}>notional {fmtUsd(arm.open_notional_usd)}</span>
                )}
              </div>
              {recByArm.get(arm.arm) && (
                <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', ...MONO, fontSize: 7 }}>
                  <span style={{ color: 'rgba(255,255,255,0.45)' }}>
                    current {recByArm.get(arm.arm)!.current_pct.toFixed(0)}%
                  </span>
                  <span style={{ color: accent }}>
                    target {recByArm.get(arm.arm)!.target_pct.toFixed(0)}%
                  </span>
                  <span style={{
                    color:
                      recByArm.get(arm.arm)!.action === 'INCREASE' ? '#00d48a'
                      : recByArm.get(arm.arm)!.action === 'REDUCE' ? '#ef4444'
                      : '#4d6070',
                  }}>
                    {recByArm.get(arm.arm)!.action.toLowerCase()} {recByArm.get(arm.arm)!.delta_pct >= 0 ? '+' : ''}{recByArm.get(arm.arm)!.delta_pct.toFixed(0)} pts
                  </span>
                  <span style={{ color: 'rgba(255,255,255,0.42)' }}>
                    target {fmtUsd(recByArm.get(arm.arm)!.target_usd)}
                  </span>
                </div>
              )}
              <div style={{ ...MONO, fontSize: 7, color: velocity.color }}>
                {velocity.label}
                {arm.deploy_events_usd > 0 ? ` · ${releasedPct}% recycled` : ''}
              </div>
              {recByArm.get(arm.arm)?.reasons?.length ? (
                <div style={{ ...MONO, fontSize: 7, color: '#4d6070' }}>
                  {recByArm.get(arm.arm)!.reasons.slice(0, 2).join(' · ')}
                </div>
              ) : null}
              <div style={{ ...MONO, fontSize: 7, color: '#4d6070' }}>{arm.notes}</div>
            </div>
          )
        })}
      </div>

      {data.recent_events.length > 0 && (
        <div style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 8, ...MONO, fontSize: 7 }}>
          <span style={{ color: 'var(--dim)', letterSpacing: '0.10em' }}>RECENT</span>
          {data.recent_events.slice(0, 4).map(ev => (
            <span key={ev.id} style={{ color: ev.event_type === 'REALIZED_PNL' ? pnlColor(ev.amount_usd) : 'rgba(255,255,255,0.45)' }}>
              {ev.arm} {ev.event_type.toLowerCase().replace(/_/g, ' ')} {ev.amount_usd >= 0 ? '+' : ''}{fmtUsd(ev.amount_usd)}
            </span>
          ))}
        </div>
      )}

      {recommendation?.recent_history && recommendation.recent_history.length > 0 && (
        <div style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 8, ...MONO, fontSize: 7 }}>
          <span style={{ color: 'var(--dim)', letterSpacing: '0.10em' }}>ALLOCATOR TRAIL</span>
          {recommendation.recent_history.slice(0, 4).map(item => (
            <span key={item.id} style={{ color: item.posture === 'LEAN_PERPS' ? '#00d48a' : item.posture === 'LEAN_MEMECOINS' ? '#60a5fa' : '#4d6070' }}>
              {fmtAge(item.ts_utc)} {item.posture.toLowerCase().replace(/_/g, ' ')}
            </span>
          ))}
        </div>
      )}
    </div>
  )
}

function Metric({ label, value, color, sub }: {
  label: string; value: string | number; color?: string; sub?: string
}) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 8 }}>
      <span style={{ color: 'var(--muted)', fontSize: 10, ...MONO, flexShrink: 0 }}>
        {label}
      </span>
      <span style={{ display: 'flex', alignItems: 'baseline', gap: 5 }}>
        {sub && <span style={{ color: 'var(--dim)', fontSize: 9 }}>{sub}</span>}
        <span style={{ color: color ?? 'var(--text2)', ...MONO, fontWeight: 600, fontSize: 13 }}>
          {value}
        </span>
      </span>
    </div>
  )
}

function MiniBar({ pct, color }: { pct: number; color: string }) {
  return (
    <div className="mini-bar-track" style={{ marginTop: 2 }}>
      <div className="mini-bar-fill" style={{
        width: `${Math.min(100, Math.max(0, pct))}%`,
        background: color,
      }} />
    </div>
  )
}

// ── Readiness Snapshot ────────────────────────────────────────────────────────

function ReadinessSnapshot({ data, loading }: { data: BriefData | undefined; loading: boolean }) {
  if (loading) return (
    <div style={{ background: 'rgba(255,255,255,0.02)', border: '1px solid rgba(255,255,255,0.07)', borderRadius: 8, padding: '14px 18px', fontSize: 10, color: 'var(--dim)', ...MONO }}>
      loading readiness…
    </div>
  )
  if (!data) return null

  const checks = Array.isArray(data.checks) ? data.checks : []
  const passCount = checks.filter(c => c.pass).length
  const totalCount = checks.length
  const allPass = passCount === totalCount && totalCount > 0
  const pipelineAgeHours = typeof data.pipeline_age_hours === 'number' ? data.pipeline_age_hours : null
  const pipelineStatus = data.pipeline_health ?? 'UNKNOWN'

  return (
    <div style={{
      background: allPass ? 'rgba(0,212,138,0.03)' : 'rgba(255,255,255,0.02)',
      border: `1px solid ${allPass ? 'rgba(0,212,138,0.15)' : 'rgba(255,255,255,0.07)'}`,
      borderLeft: `3px solid ${allPass ? '#00d48a' : '#f59e0b'}`,
      borderRadius: '0 10px 10px 0',
      padding: '14px 18px',
      display: 'flex', flexDirection: 'column', gap: 10,
    }}>
      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <span style={{ color: 'var(--chrome)', ...MONO, fontWeight: 700, fontSize: 10, letterSpacing: '0.14em' }}>
          READINESS
        </span>
        <span style={{ ...MONO, fontSize: 9, color: allPass ? '#00d48a' : '#f59e0b', fontWeight: 700 }}>
          {passCount}/{totalCount} GATES PASS
        </span>
      </div>

      {/* Checks */}
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
        {checks.map(c => (
          (() => {
            const isPipeline = c.name === 'pipeline_fresh'
            const pipelineColor =
              pipelineStatus === 'FRESH' ? '#00d48a' :
              pipelineStatus === 'AGING' ? '#f59e0b' :
              pipelineStatus === 'STALE' ? '#ef4444' :
              pipelineStatus === 'NO_DATA' ? '#ef4444' :
              c.pass ? '#00d48a' : '#ef4444'
            const chipBg =
              isPipeline
                ? `${pipelineColor}12`
                : c.pass ? 'rgba(0,212,138,0.06)' : 'rgba(239,68,68,0.06)'
            const chipBorder =
              isPipeline
                ? `1px solid ${pipelineColor}28`
                : `1px solid ${c.pass ? 'rgba(0,212,138,0.2)' : 'rgba(239,68,68,0.2)'}`
            const valueColor =
              isPipeline
                ? pipelineColor
                : c.pass ? '#4d8a70' : '#7a3030'
            return (
          <div key={c.name} style={{
            display: 'flex', alignItems: 'center', gap: 5,
            background: chipBg,
            border: chipBorder,
            borderRadius: 4, padding: '4px 10px',
            fontSize: 10, ...MONO,
          }}>
            <span style={{ color: isPipeline ? pipelineColor : (c.pass ? '#00d48a' : '#ef4444') }}>
              {isPipeline ? '●' : (c.pass ? '✓' : '✗')}
            </span>
            <span style={{ color: 'var(--dim)' }}>{c.label}</span>
            {c.value != null && (
              <span style={{ color: valueColor }}>
                {c.value}
              </span>
            )}
            {isPipeline && pipelineAgeHours != null && (
              <span style={{ color: 'var(--recessed)', fontSize: 8 }}>
                {pipelineAgeHours < 2 ? 'healthy' : pipelineAgeHours < 4 ? 'watch' : 'stale'}
              </span>
            )}
            {c.name === 'wr_gate' && !c.pass && c.wr_sample_concentration_status && (
              <span style={{ color: 'var(--amber)', fontSize: 8, opacity: 0.8 }}>
                {c.wr_sample_unique_days === 1 && c.wr_sample_top_day_share_pct === 100
                  ? '1-day batch'
                  : c.wr_sample_concentration_status !== 'BROAD'
                    ? `${c.wr_sample_top_token_symbol ?? '?'} ${c.wr_sample_top_token_share_pct}%`
                    : null
                }
                {c.wr_sample_actionable_n != null && c.wr_sample_actionable_n < 3 && (
                  <> · {c.wr_sample_actionable_n} act</>
                )}
              </span>
            )}
          </div>
            )
          })()
        ))}
      </div>

      {/* 24h perp stats — Patch 281: labelled PERP SIM so operator cannot misread as memecoin/system stats */}
      {data.perp_24h && (
        <div style={{ display: 'flex', gap: 16, fontSize: 9, ...MONO, color: 'var(--dim)', borderTop: '1px solid rgba(255,255,255,0.05)', paddingTop: 6 }}>
          <span style={{ color: '#2d3d50', letterSpacing: '0.12em', fontWeight: 700 }}>PERP SIM</span>
          <span>24h trades <span style={{ color: 'var(--text2)' }}>{data.perp_24h.trades}</span></span>
          {data.perp_24h.win_rate != null && (
            <span>WR <span style={{ color: data.perp_24h.win_rate >= 50 ? '#00d48a' : '#ef4444' }}>{data.perp_24h.win_rate.toFixed(0)}%</span></span>
          )}
          {data.perp_24h.pnl_usd != null && (
            <span>PnL <span style={{ color: data.perp_24h.pnl_usd >= 0 ? '#00d48a' : '#ef4444' }}>{data.perp_24h.pnl_usd >= 0 ? '+' : ''}${data.perp_24h.pnl_usd.toFixed(2)}</span></span>
          )}
        </div>
      )}

      {/* Recent alerts */}
      {(data.alerts ?? []).length > 0 && (
        <div style={{ borderTop: '1px solid rgba(255,255,255,0.05)', paddingTop: 6, display: 'flex', flexDirection: 'column', gap: 3 }}>
          {(data.alerts ?? []).slice(0, 3).map((a, i) => (
            <div key={i} style={{ fontSize: 9, ...MONO, color: 'var(--chrome)', display: 'flex', gap: 6 }}>
              <span style={{ color: 'var(--recessed)', flexShrink: 0 }}>{a.agent}</span>
              <span style={{ color: 'var(--chrome)', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{a.message}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}


function DecisionJournalPanel({ data, loading }: { data: DecisionJournalEntry[] | undefined; loading: boolean }) {
  if (loading || !data || data.length === 0) return null
  const entries = Array.isArray(data) ? data : []
  if (entries.length === 0) return null

  return (
    <div style={{
      background: 'rgba(255,255,255,0.015)',
      border: '1px solid rgba(255,255,255,0.05)',
      borderRadius: 8,
      padding: '14px 16px',
      display: 'flex', flexDirection: 'column', gap: 8,
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <span style={{ color: 'var(--chrome)', ...MONO, fontWeight: 700, fontSize: 10, letterSpacing: '0.14em' }}>
          DECISION JOURNAL
        </span>
        <span style={{ ...MONO, fontSize: 9, color: 'var(--chrome)' }}>{entries.length} entries</span>
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
        {entries.slice(0, 4).map(e => {
          const statusColor = e.resolution_status === 'RESOLVED' ? '#00d48a' : e.resolution_status === 'FAILED' ? '#ef4444' : '#4d5a6e'
          return (
            <div key={e.id} style={{
              background: 'rgba(0,0,0,0.2)', borderRadius: 5, padding: '8px 10px',
              display: 'flex', flexDirection: 'column', gap: 4,
            }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <span style={{ color: 'var(--text2)', fontSize: 10, ...MONO, fontWeight: 600 }}>
                  {e.recommended_action}{e.symbol ? ` · ${e.symbol}` : ''}
                </span>
                <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                  <span style={{ color: 'var(--recessed)', fontSize: 9, ...MONO }}>{e.system}</span>
                  {(e.surface_count ?? 0) > 1 ? (
                    <>
                      <span style={{
                        fontSize: 8, color: '#f59e0b', background: 'rgba(245,158,11,0.12)',
                        border: '1px solid rgba(245,158,11,0.2)', borderRadius: 3,
                        padding: '0px 5px', ...MONO,
                      }}>
                        {e.surface_count}×
                      </span>
                      <span style={{ color: 'var(--recessed)', fontSize: 9, ...MONO }}>
                        {fmtAge(e.last_seen_ts ?? e.created_ts)}
                      </span>
                    </>
                  ) : (
                    <span style={{ color: 'var(--recessed)', fontSize: 9, ...MONO }}>{fmtAge(e.created_ts)}</span>
                  )}
                  <span style={{ color: statusColor, fontSize: 8, ...MONO }}>{e.resolution_status}</span>
                </div>
              </div>
              <div style={{ color: 'var(--chrome)', fontSize: 9, ...MONO }}>{e.reason}</div>
              {e.outcome_24h_pct != null && (
                <div style={{ color: e.outcome_24h_pct >= 0 ? '#3d6050' : '#7a3030', fontSize: 8, ...MONO }}>
                  24h: {e.outcome_24h_pct >= 0 ? '+' : ''}{e.outcome_24h_pct.toFixed(1)}%
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}

// ── Action Board Panel ────────────────────────────────────────────────────────

// Human-readable headline translations
const AB_HEADLINE: Record<string, { label: string; subtitle: string }> = {
  READY_NOW:    { label: 'Ready Now',          subtitle: 'A clean actionable setup is available' },
  BLOCKED_BEST: { label: 'Best Idea Blocked',  subtitle: 'Strongest setup visible, but not buyable yet' },
  WATCHLIST:    { label: 'Watchlist Only',      subtitle: 'Nothing buy-ready — keep these names on screen' },
  MANAGE_OPEN:  { label: 'Manage Open',         subtitle: 'No new buys — focus on current exposure' },
  QUIET:        { label: 'Quiet',               subtitle: 'No meaningful action candidates right now' },
}

function ActionBoardPanel({ data, loading, scannerDiag, reinfBySymbol }: {
  data:        ActionBoardData | undefined
  loading:     boolean
  scannerDiag?: HomeScannerDiag
  reinfBySymbol?: Map<string, string>
}) {
  if (loading) return (
    <div style={{ background: 'rgba(255,255,255,0.02)', border: '1px solid rgba(255,255,255,0.07)', borderRadius: 8, padding: '14px 18px', fontSize: 10, color: 'var(--dim)', ...MONO }}>
      loading action board…
    </div>
  )
  if (!data) return null

  const hl  = data.headline
  const sm  = data.summary
  const hlt = AB_HEADLINE[hl.state] ?? { label: hl.state.replace(/_/g, ' '), subtitle: hl.note }

  const headlineColor =
    hl.state === 'READY_NOW'    ? '#00d48a' :
    hl.state === 'BLOCKED_BEST' ? '#f59e0b' :
    hl.state === 'WATCHLIST'    ? '#60a5fa' :
    hl.state === 'MANAGE_OPEN'  ? '#06b6d4' : '#4d6070'

  const boardColor =
    (data.ready_now ?? []).length > 0    ? '#00d48a' :
    (data.best_blocked ?? []).length > 0 ? '#f59e0b' : '#4d6070'

  // Build relaxed-symbol set from scanner diagnostics
  const relaxedSymbols = new Map<string, string | null>()
  for (const ts of scannerDiag?.top_scored ?? []) {
    if (ts.scanner_regime === 'RELAXED_NEAR_MISS') {
      relaxedSymbols.set(ts.symbol, ts.scanner_relaxation_reason)
    }
  }

  // Lane chip
  const LaneChipAB = ({ system }: { system: string }) => {
    const lc = LANE_COLOR[system] || '#475569'
    return (
      <span style={{
        ...MONO, fontSize: 8, fontWeight: 700, letterSpacing: '0.06em',
        color: lc, background: `${lc}12`, border: `1px solid ${lc}30`,
        borderRadius: 2, padding: '1px 4px', flexShrink: 0,
      }}>
        {LANE_SHORT[system] || system.slice(0, 2)}
      </span>
    )
  }
  const DataChip = ({ item }: { item: ActionBoardItem }) => {
    const state = String(item.data_freshness_state || item.freshness_bucket || '').toUpperCase()
    const source = String(item.data_source || '').replace(/_/g, ' ').toLowerCase()
    if (!state && !source) return null
    const tone =
      state.includes('LIVE') ? '#00d48a'
      : state.includes('FALLBACK') || state.includes('CACHED') ? '#f59e0b'
      : state.includes('STALE') || state.includes('PENALIZED') ? '#ef4444'
      : '#7f95a8'
    return (
      <span style={{
        ...MONO,
        fontSize: 7,
        color: tone,
        background: `${tone}10`,
        border: `1px solid ${tone}28`,
        borderRadius: 3,
        padding: '1px 5px',
        flexShrink: 0,
      }}>
        {state || 'DATA'}{source ? ` · ${source}` : ''}
      </span>
    )
  }

  // Blocked / ready row — answers: what is it? why can't I buy? what needs to change?
  const BlockedRow = ({ item, accent }: { item: ActionBoardItem; accent: string }) => {
    const proofDemoted = item.proof_state?.includes('DEMOT')
    const proofLabel   = item.proof_state && item.proof_state !== 'OK'
      ? item.proof_state.replace(/_STACK|_SIGNAL/g, '').replace(/_/g, ' ')
      : null
    const proofColor   = proofDemoted ? '#ef4444' : '#f59e0b'
    return (
      <div style={{
        display: 'flex', flexDirection: 'column', gap: 5,
        background: 'rgba(0,0,0,0.2)', borderRadius: 5, padding: '8px 10px',
        borderLeft: `2px solid ${accent}55`,
      }}>
        {/* Name + lane + proof state */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
          <LaneChipAB system={item.system} />
          <span style={{ ...MONO, fontSize: 11, color: accent, fontWeight: 700 }}>
            {item.symbol ?? item.action}
          </span>
          {item.intended_action && (
            <span style={{ ...MONO, fontSize: 8, color: 'var(--chrome)' }}>
              → {item.intended_action}
            </span>
          )}
          {proofLabel && (
            <span style={{
              ...MONO, fontSize: 8, fontWeight: 700,
              color: proofColor,
              background: `${proofColor}12`,
              border: `1px solid ${proofColor}28`,
              borderRadius: 3, padding: '1px 5px',
            }}>
              {proofLabel}
            </span>
          )}
          {item.symbol && relaxedSymbols.has(item.symbol) && (
            <span style={{
              ...MONO, fontSize: 7, fontWeight: 700, letterSpacing: '0.05em',
              color: '#c09030', background: 'rgba(245,158,11,0.08)',
              border: '1px solid rgba(245,158,11,0.22)',
              borderRadius: 2, padding: '1px 5px', flexShrink: 0,
            }}
              title={`relaxed near-miss admission${relaxedSymbols.get(item.symbol) ? ': ' + relaxedSymbols.get(item.symbol) : ''}`}
            >
              RELAXED SCAN{relaxedSymbols.get(item.symbol) ? ` · ${relaxedSymbols.get(item.symbol)}` : ''}
            </span>
          )}
          {item.symbol && reinfBySymbol?.has(item.symbol) && reinfBySymbol.get(item.symbol) !== 'NONE' && (() => {
            const rl = reinfBySymbol.get(item.symbol)!
            const rc = rl === 'STRONG' ? '#00d48a' : rl === 'MODERATE' ? '#60a5fa' : '#f59e0b'
            return (
              <span style={{
                ...MONO, fontSize: 7, fontWeight: 700, letterSpacing: '0.05em',
                color: rc, background: `${rc}0a`,
                border: `1px solid ${rc}28`,
                borderRadius: 2, padding: '1px 5px', flexShrink: 0,
              }}>
                {rl}
              </span>
            )
          })()}
          <DataChip item={item} />
          <span style={{ ...MONO, fontSize: 8, color: 'var(--recessed)', marginLeft: 'auto' }}>
            score {item.priority_score}
          </span>
        </div>
        {/* Why it's blocked */}
        {(item.blockers ?? []).length > 0 && (
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
            {item.blockers.map((b, i) => (
              <span key={i} style={{
                ...MONO, fontSize: 8, fontWeight: 600,
                color: '#d45555', background: 'rgba(239,68,68,0.10)',
                border: '1px solid rgba(239,68,68,0.20)', borderRadius: 3, padding: '2px 6px',
              }}>{b}</span>
            ))}
          </div>
        )}
        <TokenAddressChip value={item.token_address} size="roomy" />
        {/* What needs to change */}
        {item.unlock_hint && (
          <span style={{ ...MONO, fontSize: 8, color: '#c09030', lineHeight: 1.5 }}>
            {item.unlock_hint}
          </span>
        )}
      </div>
    )
  }

  // Watchlist row — compact, informational
  const WatchRow = ({ item }: { item: ActionBoardItem }) => {
    const lc = LANE_COLOR[item.system] || '#475569'
    return (
      <div style={{
        display: 'flex', alignItems: 'center', gap: 6,
        padding: '5px 8px', borderRadius: 4,
        background: 'rgba(0,0,0,0.15)',
        fontSize: 9, ...MONO,
      }}>
        <LaneChipAB system={item.system} />
        <span style={{ color: lc, fontWeight: 700, flexShrink: 0, fontSize: 10 }}>
          {item.symbol ?? item.action}
        </span>
        {item.symbol && relaxedSymbols.has(item.symbol) && (
          <span style={{
            ...MONO, fontSize: 7, fontWeight: 700,
            color: '#c09030', background: 'rgba(245,158,11,0.08)',
            border: '1px solid rgba(245,158,11,0.20)',
            borderRadius: 2, padding: '1px 4px', flexShrink: 0,
          }}>
            RELAXED{relaxedSymbols.get(item.symbol) ? ` · ${relaxedSymbols.get(item.symbol)}` : ''}
          </span>
        )}
        {item.symbol && reinfBySymbol?.has(item.symbol) && reinfBySymbol.get(item.symbol) !== 'NONE' && (() => {
          const rl = reinfBySymbol.get(item.symbol)!
          const rc = rl === 'STRONG' ? '#00d48a' : rl === 'MODERATE' ? '#60a5fa' : '#f59e0b'
          return (
            <span style={{
              ...MONO, fontSize: 7, fontWeight: 700, letterSpacing: '0.05em',
              color: rc, background: `${rc}0a`, border: `1px solid ${rc}28`,
              borderRadius: 2, padding: '1px 4px', flexShrink: 0,
            }}>
              {rl}
            </span>
          )
        })()}
        <DataChip item={item} />
        <TokenAddressChip value={item.token_address} />
        <span style={{ color: 'var(--chrome)', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {item.reason}
        </span>
      </div>
    )
  }

  // Hold row — clearly reads as "already in, manage it"
  const HoldRow = ({ item }: { item: ActionBoardItem }) => {
    const lc = LANE_COLOR[item.system] || '#475569'
    return (
      <div style={{
        display: 'flex', alignItems: 'center', gap: 6,
        padding: '5px 8px', borderRadius: 4,
        background: 'rgba(6,182,212,0.04)',
        border: '1px solid rgba(6,182,212,0.12)',
        fontSize: 9, ...MONO,
      }}>
        <LaneChipAB system={item.system} />
        <span style={{
          ...MONO, fontSize: 7, fontWeight: 700, letterSpacing: '0.08em',
          color: '#06b6d4', background: 'rgba(6,182,212,0.12)',
          border: '1px solid rgba(6,182,212,0.25)',
          borderRadius: 2, padding: '1px 4px', flexShrink: 0,
        }}>IN</span>
        <span style={{ color: lc, fontWeight: 700, flexShrink: 0, fontSize: 10 }}>
          {item.symbol ?? item.action}
        </span>
        <TokenAddressChip value={item.token_address} />
        <span style={{ color: 'var(--chrome)', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {item.reason}
        </span>
        {item.size_guidance && (
          <span style={{ color: 'var(--recessed)', flexShrink: 0, fontSize: 8 }}>{item.size_guidance}</span>
        )}
      </div>
    )
  }

  const readyItems   = data.ready_now              ?? []
  const blockedItems = data.best_blocked            ?? []
  const watchItems   = data.watchlist               ?? []
  const holdItems    = data.holds                   ?? []
  const newItems     = data.new_since_last_check    ?? []
  const hasContent   = readyItems.length > 0 || blockedItems.length > 0 || watchItems.length > 0 || holdItems.length > 0

  return (
    <div style={{
      background: 'rgba(255,255,255,0.025)',
      border: '1px solid rgba(255,255,255,0.07)',
      borderLeft: `3px solid ${boardColor}`,
      borderRadius: '0 10px 10px 0',
      padding: '14px 18px',
      display: 'flex', flexDirection: 'column', gap: 12,
    }}>

      {/* ── Header: human headline + summary strip ── */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: 8 }}>
        {/* Left: state + subtitle */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span style={{ color: 'var(--chrome)', ...MONO, fontWeight: 700, fontSize: 9, letterSpacing: '0.14em', textTransform: 'uppercase' }}>
              NOW
            </span>
            <span style={{ ...MONO, fontSize: 11, fontWeight: 700, color: headlineColor }}>
              {hlt.label}
            </span>
            {newItems.length > 0 && (
              <span style={{
                ...MONO, fontSize: 8, fontWeight: 700,
                color: '#22c55e', background: 'rgba(34,197,94,0.12)',
                border: '1px solid rgba(34,197,94,0.30)', borderRadius: 3, padding: '1px 5px',
              }}>
                {newItems.length} NEW
              </span>
            )}
          </div>
          <span style={{ ...MONO, fontSize: 9, color: 'var(--chrome)', paddingLeft: 2 }}>
            {hlt.subtitle}
          </span>
        </div>
        {/* Right: compact counts + freshness */}
        <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap', paddingTop: 2 }}>
          <span style={{ ...MONO, fontSize: 8, color: sm.ready_now_count > 0 ? '#00d48a' : 'var(--recessed)', fontWeight: sm.ready_now_count > 0 ? 700 : 400 }}>
            {sm.ready_now_count} ready
          </span>
          <span style={{ ...MONO, fontSize: 8, color: sm.blocked_count > 0 ? '#f59e0b' : 'var(--recessed)' }}>
            {sm.blocked_count} blocked
          </span>
          <span style={{ ...MONO, fontSize: 8, color: 'var(--recessed)' }}>
            {sm.watch_count} watch
          </span>
          <span style={{ ...MONO, fontSize: 8, color: 'var(--recessed)' }}>
            {sm.hold_count} open
          </span>
          <span style={{ ...MONO, fontSize: 8, color: 'var(--recessed)', borderLeft: '1px solid rgba(255,255,255,0.08)', paddingLeft: 8 }}>
            {fmtAge(data.generated_at)}
          </span>
        </div>
      </div>

      {/* ── Ready Now ── */}
      {readyItems.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          <span style={{ ...MONO, fontSize: 8, fontWeight: 700, color: '#00d48a', letterSpacing: '0.12em', textTransform: 'uppercase' }}>
            Ready Now
          </span>
          {readyItems.map(item => (
            <BlockedRow key={item.opportunity_key} item={item} accent="#00d48a" />
          ))}
        </div>
      )}

      {/* ── Best Blocked ── */}
      {blockedItems.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          <span style={{ ...MONO, fontSize: 8, fontWeight: 700, color: '#f59e0b', letterSpacing: '0.12em', textTransform: 'uppercase' }}>
            Best Blocked
          </span>
          {blockedItems.map(item => (
            <BlockedRow key={item.opportunity_key} item={item} accent="#f59e0b" />
          ))}
        </div>
      )}

      {/* ── Watchlist ── */}
      {watchItems.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
          <span style={{ ...MONO, fontSize: 8, fontWeight: 700, color: '#60a5fa', letterSpacing: '0.12em', textTransform: 'uppercase' }}>
            Watchlist
          </span>
          {watchItems.map(item => (
            <WatchRow key={item.opportunity_key} item={item} />
          ))}
        </div>
      )}

      {/* ── Open Holds ── */}
      {holdItems.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
          <span style={{ ...MONO, fontSize: 8, fontWeight: 700, color: '#06b6d4', letterSpacing: '0.12em', textTransform: 'uppercase' }}>
            Open Holds
          </span>
          {holdItems.map(item => (
            <HoldRow key={item.opportunity_key} item={item} />
          ))}
        </div>
      )}

      {/* ── Quiet state ── */}
      {!hasContent && (
        <div style={{ ...MONO, fontSize: 9, color: 'var(--recessed)', paddingLeft: 2 }}>
          No candidates — system is quiet.
        </div>
      )}

    </div>
  )
}

// ── Patch 300: System Posture Panel ──────────────────────────────────────────

export function SystemPosturePanel({ data, loading }: { data: PostureData | undefined; loading: boolean }) {
  if (loading) return (
    <div style={{ ...MONO, color: 'var(--dim)', fontSize: 10, padding: '18px 20px',
      background: 'rgba(255,255,255,0.02)', border: '1px solid rgba(255,255,255,0.06)',
      borderRadius: 10 }}>
      loading posture…
    </div>
  )
  if (!data) return null

  function postureColor(p: string): string {
    if (['POSITIONS_ACTIVE', 'ACTIVE', 'SIGNAL_ACTIVE', 'ACCUMULATING', 'AT_CAPACITY'].includes(p)) return '#00d48a'
    if (['TRANSITION_FORMING', 'ENTRY_WATCHING'].includes(p))                                         return '#f59e0b'
    if (['DEGRADED', 'COLLATERAL_CONSTRAINED'].includes(p))                                           return '#ef4444'
    if (['MONITOR_ONLY', 'POSITIONS_MONITORING', 'TRACKING'].includes(p))                              return '#60a5fa'
    if (['ADDS_PAUSED', 'COLLATERAL_TIGHT', 'ENTRY_BLOCKED'].includes(p))                              return '#f59e0b'
    return '#475569'  // IDLE, QUIET, SIM_ONLY, WAITING
  }

  function sysColor(p: string): string {
    if (p === 'FULL_ATTENTION')    return '#00d48a'
    if (p === 'SINGLE_LANE_FOCUS') return '#60a5fa'
    if (p === 'TRANSITION_BUILDING') return '#f59e0b'
    if (p === 'WATCH_AND_WAIT')    return '#60a5fa'
    if (p === 'SYSTEM_BLOCKED')    return '#ef4444'
    return '#475569'
  }

  const lanes = [
    { key: 'PERPS',     posture: data.perps.posture,     detail: data.perps.detail },
    { key: 'MEMECOINS', posture: data.memecoins.posture, detail: data.memecoins.detail },
    { key: 'SPOT',      posture: data.spot.posture,      detail: data.spot.detail },
    { key: 'WHALE',     posture: data.whale.posture,     detail: data.whale.detail },
  ]
  const sc = sysColor(data.system_posture)

  return (
    <div style={{
      background: 'rgba(255,255,255,0.02)',
      border: '1px solid rgba(255,255,255,0.06)',
      borderTop: `2px solid ${sc}`,
      borderRadius: '0 0 10px 10px',
      padding: '18px 20px',
      display: 'flex', flexDirection: 'column', gap: 12,
    }}>
      {/* Header: system posture + focus sentence */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
        <span style={{ ...MONO, fontSize: 10, color: 'var(--chrome)', letterSpacing: '0.14em' }}>
          POSTURE
        </span>
        <span style={{
          ...MONO, fontSize: 10, fontWeight: 700,
          color: sc,
          padding: '2px 8px', borderRadius: 4,
          background: `${sc}15`,
          border: `1px solid ${sc}30`,
        }}>
          {data.system_posture.replace(/_/g, ' ')}
        </span>
        {data.authority_enforcement && (() => {
          const ae = data.authority_enforcement
          // Enforce ON is the expected state — use confident neutral, not alarm red
          const enfColor = ae.enforce_active ? '#60a5fa' : 'var(--dim)'
          // Entry BLOCK at low rank is expected — amber for intentional restriction, green for ALLOW
          const entryColor = ae.new_entry_verdict === 'ALLOW' ? 'var(--green)' : '#f59e0b'
          return (
            <div style={{ display: 'flex', gap: 5, alignItems: 'center', flexShrink: 0 }}>
              <span className="badge" style={{ color: enfColor, background: `${enfColor}14`, border: `1px solid ${enfColor}30`, fontSize: 8 }}>
                ENFORCE {ae.enforce_active ? 'ON' : 'OFF'}
              </span>
              <span className="badge" style={{ color: entryColor, background: `${entryColor}14`, border: `1px solid ${entryColor}30`, fontSize: 8 }}>
                ENTRY {ae.new_entry_verdict}
              </span>
              <span style={{ ...MONO, fontSize: 8, color: 'var(--recessed)' }}>
                R{ae.rank}
              </span>
            </div>
          )
        })()}
        <span style={{ ...MONO, fontSize: 9, color: 'var(--text2)', flex: 1 }}>
          {data.focus}
        </span>
      </div>

      {/* Lane chips */}
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
        {lanes.map(lane => {
          const lc = postureColor(lane.posture)
          return (
            <div key={lane.key} style={{
              flex: '1 1 180px',
              background: 'rgba(0,0,0,0.2)',
              border: `1px solid ${lc}22`,
              borderRadius: 6,
              padding: '6px 10px',
              display: 'flex', flexDirection: 'column', gap: 2,
            }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                <span style={{ ...MONO, fontSize: 9, color: 'var(--dim)', letterSpacing: '0.1em' }}>
                  {lane.key}
                </span>
                <span style={{ ...MONO, fontSize: 9, color: lc, fontWeight: 700 }}>
                  {lane.posture.replace(/_/g, ' ')}
                </span>
              </div>
              <span style={{ ...MONO, fontSize: 9, color: 'var(--chrome)' }}>{lane.detail}</span>
            </div>
          )
        })}
      </div>
    </div>
  )
}

// ── BestActionPanel ───────────────────────────────────────────────────────────

function BestActionPanel({ data, loading }: { data: BestActionData | undefined; loading: boolean }) {
  if (loading) return null

  const verdictColor = (v: string) => {
    if (v === 'DO_NOTHING') return 'var(--dim)'
    if (v === 'SHORT') return '#f59e0b'
    return '#00d48a'
  }

  const armLabel = (arm: string | null) => {
    if (!arm) return ''
    return { MEMECOINS: 'MEME', SPOT: 'SPOT', PERPS: 'PERP' }[arm] ?? arm
  }

  const ageStr = (iso: string | undefined) => {
    if (!iso) return ''
    const diffS = Math.floor((Date.now() - new Date(iso).getTime()) / 1000)
    if (diffS < 60) return `${diffS}s ago`
    return `${Math.floor(diffS / 60)}m ago`
  }

  const verdict   = data?.verdict ?? 'DO_NOTHING'
  const vc        = verdictColor(verdict)
  const isAction  = verdict !== 'DO_NOTHING'
  const conviction = data?.conviction ?? 0

  return (
    <div className="card" style={{
      background: 'rgba(2,6,14,0.75)',
      borderColor: isAction ? `${vc}30` : 'rgba(255,255,255,0.06)',
      padding: '12px 16px',
      display: 'flex', flexDirection: 'column', gap: 8,
    }}>
      {/* Header row */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <span style={{ ...MONO, fontSize: 9, color: 'var(--dim)', letterSpacing: '0.12em' }}>BEST ACTION NOW</span>
        <span style={{ ...MONO, fontSize: 8, color: 'var(--recessed)' }}>{ageStr(data?.generated_at)}</span>
      </div>

      {/* Verdict line */}
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, flexWrap: 'wrap' }}>
        <span style={{ ...MONO, fontSize: 15, fontWeight: 700, color: vc, letterSpacing: '0.08em' }}>
          {isAction
            ? `${verdict} · ${data?.asset ?? ''} · ${armLabel(data?.arm ?? null)}`
            : 'DO NOTHING'}
        </span>
        {isAction && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            {data?.execution_blocked && (
              <span className="badge" style={{
                color: '#f59e0b', background: 'rgba(245,158,11,0.1)',
                border: '1px solid rgba(245,158,11,0.25)', fontSize: 8,
              }}>MANUAL ONLY</span>
            )}
            <span style={{ ...MONO, fontSize: 9, color: 'var(--dim)' }}>conviction {conviction}</span>
          </div>
        )}
      </div>

      {/* Conviction bar */}
      {isAction && (
        <div style={{ height: 2, background: 'rgba(255,255,255,0.07)', borderRadius: 1 }}>
          <div style={{
            height: '100%', borderRadius: 1,
            width: `${conviction}%`,
            background: vc,
            transition: 'width 0.4s ease',
          }} />
        </div>
      )}

      {/* Reason */}
      <span style={{ ...MONO, fontSize: 9, color: isAction ? 'var(--text2)' : 'var(--dim)' }}>
        {data?.reason}
      </span>

      {isAction && <TokenAddressChip value={data?.token_address} size="roomy" />}

      {/* Runners up */}
      {(data?.candidates ?? []).length > 0 && (
        <div style={{ borderTop: '1px solid rgba(255,255,255,0.05)', paddingTop: 6, display: 'flex', flexDirection: 'column', gap: 3 }}>
          <span style={{ ...MONO, fontSize: 8, color: 'var(--recessed)', letterSpacing: '0.1em' }}>
            {isAction ? 'ALSO WATCHING' : 'RUNNERS UP'}
          </span>
          {(data?.candidates ?? []).slice(0, 3).map((c, i) => (
            <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
              <span style={{ ...MONO, fontSize: 8, color: 'var(--dim)', minWidth: 36 }}>{armLabel(c.arm)}</span>
              <span style={{ ...MONO, fontSize: 8, color: 'var(--text2)', fontWeight: 600 }}>{c.asset}</span>
              <TokenAddressChip value={c.token_address} />
              <span style={{ ...MONO, fontSize: 8, color: verdictColor(c.action) }}>{c.action}</span>
              <span style={{ ...MONO, fontSize: 8, color: 'var(--recessed)' }}>{c.conviction}</span>
              <span style={{ ...MONO, fontSize: 8, color: 'var(--recessed)', flex: 1 }}>{c.reason}</span>
              {c.manual_only && (
                <span style={{ ...MONO, fontSize: 7, color: '#f59e0b' }}>MANUAL</span>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function AnalystMemoPanel({ data, loading }: { data: AIAnalystData | undefined; loading: boolean }) {
  if (loading) return null
  if (!data) return null

  const ageStr = (iso: string | undefined) => {
    if (!iso) return ''
    const diffS = Math.floor((Date.now() - new Date(iso).getTime()) / 1000)
    if (diffS < 60) return `${diffS}s ago`
    if (diffS < 3600) return `${Math.floor(diffS / 60)}m ago`
    return `${Math.floor(diffS / 3600)}h ago`
  }

  const sourceColor = data.source === 'ai' ? '#a78bfa' : '#60a5fa'
  const statusColor = data.status === 'READY' ? '#00d48a' : data.status === 'THIN' ? '#f59e0b' : 'var(--dim)'

  const Pill = ({ tone, children }: { tone: 'good' | 'warn' | 'hold'; children: React.ReactNode }) => {
    const color =
      tone === 'good' ? '#00d48a'
      : tone === 'warn' ? '#f59e0b'
      : '#4d6070'
    return (
      <span style={{
        ...MONO,
        fontSize: 8,
        color,
        background: `${color}12`,
        border: `1px solid ${color}28`,
        borderRadius: 4,
        padding: '3px 7px',
        lineHeight: 1.4,
      }}>
        {children}
      </span>
    )
  }

  return (
    <div className="card" style={{
      background: 'rgba(5,10,18,0.72)',
      borderColor: 'rgba(96,165,250,0.16)',
      borderLeft: '3px solid rgba(96,165,250,0.45)',
      padding: '12px 16px',
      display: 'flex',
      flexDirection: 'column',
      gap: 9,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10, flexWrap: 'wrap' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
          <span style={{ ...MONO, fontSize: 9, color: 'var(--dim)', letterSpacing: '0.12em' }}>
            SYSTEM ANALYST
          </span>
          <span className="badge" style={{
            color: sourceColor,
            background: `${sourceColor}14`,
            border: `1px solid ${sourceColor}2f`,
            fontSize: 8,
          }}>
            {data.source === 'ai' ? 'AI MEMO' : 'RULES MEMO'}
          </span>
          <span className="badge" style={{
            color: statusColor,
            background: `${statusColor}14`,
            border: `1px solid ${statusColor}2f`,
            fontSize: 8,
          }}>
            {data.status}
          </span>
        </div>
        <span style={{ ...MONO, fontSize: 8, color: 'var(--recessed)' }}>{ageStr(data.generated_at)}</span>
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
        <span style={{ ...MONO, fontSize: 10, color: '#60a5fa', fontWeight: 700 }}>
          {data.headline}
        </span>
        <span style={{ ...MONO, fontSize: 9, color: 'var(--chrome)' }}>
          {data.top_lesson}
        </span>
      </div>

      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
        {(data.what_improved ?? []).slice(0, 2).map((item, i) => (
          <Pill key={`improved-${i}`} tone="good">improved · {item}</Pill>
        ))}
        {(data.what_degraded ?? []).slice(0, 2).map((item, i) => (
          <Pill key={`degraded-${i}`} tone="warn">degraded · {item}</Pill>
        ))}
        {(data.do_not_change_yet ?? []).slice(0, 1).map((item, i) => (
          <Pill key={`hold-${i}`} tone="hold">hold steady · {item}</Pill>
        ))}
      </div>

      {(data.review_now ?? []).length > 0 && (
        <div style={{
          display: 'flex',
          flexDirection: 'column',
          gap: 4,
          borderTop: '1px solid rgba(255,255,255,0.05)',
          paddingTop: 7,
        }}>
          <span style={{ ...MONO, fontSize: 8, color: '#f59e0b', letterSpacing: '0.1em' }}>
            REVIEW NOW
          </span>
          {(data.review_now ?? []).slice(0, 2).map((item, i) => (
            <span key={`review-${i}`} style={{ ...MONO, fontSize: 8, color: 'var(--text2)' }}>
              {item}
            </span>
          ))}
        </div>
      )}
    </div>
  )
}

export function HomeSignalSnapshot({
  summary,
  loading,
  heat,
  heatLoading,
}: {
  summary: HomeSummary | undefined
  loading: boolean
  heat: SpeculationHeatData | undefined
  heatLoading: boolean
}) {
  const heatColor =
    heat?.heat_state === 'OVERHEATED' ? '#ef4444'
    : heat?.heat_state === 'HOT' ? '#f59e0b'
    : heat?.heat_state === 'WARM' ? '#60a5fa'
    : '#00d48a'

  const snapshotItem = (
    label: string,
    value: string,
    tone?: string,
    note?: string,
  ) => (
    <div style={{
      background: 'rgba(255,255,255,0.02)',
      border: '1px solid rgba(255,255,255,0.05)',
      borderRadius: 8,
      padding: '10px 12px',
      display: 'flex',
      flexDirection: 'column',
      gap: 4,
    }}>
      <span style={{ ...MONO, fontSize: 8, color: 'var(--dim)', letterSpacing: '0.1em' }}>{label}</span>
      <span style={{ ...MONO, fontSize: 12, fontWeight: 700, color: tone || '#d7e1ea' }}>{value}</span>
      {note && (
        <span style={{ ...MONO, fontSize: 8, color: '#7f95a8', lineHeight: 1.45 }}>{note}</span>
      )}
    </div>
  )

  return (
    <div className="card" style={{
      background: 'rgba(5,10,18,0.72)',
      borderColor: 'rgba(255,255,255,0.06)',
      padding: '12px 16px',
      display: 'flex',
      flexDirection: 'column',
      gap: 10,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <span style={{ ...MONO, fontSize: 9, color: 'var(--dim)', letterSpacing: '0.12em' }}>SYSTEM SNAPSHOT</span>
        <span style={{ ...MONO, fontSize: 8, color: '#7f95a8' }}>what the system sees right now</span>
      </div>

      <div style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))',
        gap: 10,
      }}>
        {snapshotItem(
          'MEMECOIN PROGRESS',
          loading || !summary ? '…' : `${summary.memecoins.outcomes} / ${summary.memecoins.next_milestone}`,
          '#60a5fa',
          loading || !summary
            ? undefined
            : summary.memecoins.wr_pct != null
              ? `cohort WR ${summary.memecoins.wr_pct}%`
              : 'waiting on more completed outcomes',
        )}
        {snapshotItem(
          'SPOT SIGNAL HEALTH',
          loading || !summary ? '…' : `${summary.spot.signal_confidence?.toUpperCase?.() || '—'} CONF`,
          summary?.spot.signal_confidence === 'high'
            ? '#00d48a'
            : summary?.spot.signal_confidence === 'medium'
              ? '#f59e0b'
              : '#7f95a8',
          loading || !summary
            ? undefined
            : `${summary.spot.holdings_count} holdings · ${summary.spot.outcomes_complete} completed outcomes`,
        )}
        {snapshotItem(
          'WHALE FLOW',
          loading || !summary ? '…' : `${summary.whale_watch.scanner_pass} pass / ${summary.whale_watch.in_range} in range`,
          '#a78bfa',
          loading || !summary
            ? undefined
            : summary.whale_watch.last_ts
              ? `last alert ${fmtAge(summary.whale_watch.last_ts)}`
              : 'no recent alert',
        )}
        {snapshotItem(
          'SPECULATION HEAT',
          heatLoading || !heat ? '…' : `${heat.heat_state || 'UNKNOWN'} · ${fmtFixed(heat.heat_score, 0)}`,
          heatColor,
          heatLoading || !heat ? undefined : heat.note,
        )}
      </div>
    </div>
  )
}

function HomeTalkTrack({
  heat,
  bestAction,
}: {
  heat: SpeculationHeatData | undefined
  bestAction: BestActionData | undefined
}) {
  const items = [
    bestAction?.execution_blocked
      ? `Best current action is blocked from automation, so the main question is whether that should stay manual-only.`
      : null,
    heat?.heat_state === 'HOT' || heat?.heat_state === 'OVERHEATED'
      ? `Speculation heat is elevated, so we should talk about whether capital should get more selective.`
      : null,
    heat?.quality_score != null && heat.quality_score < 60
      ? `Heat quality is mixed, which usually means we should separate noisy runs from real sponsorship more carefully.`
      : null,
    bestAction?.arm === 'MEMECOINS'
      ? `Memecoins are leading right now, so the next conversation should focus on timing and room left rather than discovery alone.`
      : null,
    bestAction?.arm === 'PERPS'
      ? `Perps are leading right now, but since we are focusing on memecoins and spot, the next conversation should be whether those lanes are blocked, late, or just weaker.`
      : null,
  ].filter(Boolean) as string[]

  return (
    <div className="card" style={{
      background: 'rgba(5,10,18,0.72)',
      borderColor: 'rgba(255,255,255,0.06)',
      padding: '12px 16px',
      display: 'flex',
      flexDirection: 'column',
      gap: 10,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <span style={{ ...MONO, fontSize: 9, color: 'var(--dim)', letterSpacing: '0.12em' }}>NEXT CONVERSATION</span>
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        {items.length > 0 ? items.slice(0, 4).map((item, i) => (
          <div key={i} style={{
            ...MONO,
            fontSize: 10,
            color: '#d7e1ea',
            lineHeight: 1.6,
            padding: '8px 10px',
            borderRadius: 8,
            background: 'rgba(255,255,255,0.02)',
            border: '1px solid rgba(255,255,255,0.05)',
          }}>
            {item}
          </div>
        )) : (
          <div style={{ ...MONO, fontSize: 10, color: '#8ca0b3', lineHeight: 1.55 }}>
            Nothing urgent is pushing up right now. That usually means the best move is to let the learning layers run and review them in the daily audit.
          </div>
        )}
      </div>
    </div>
  )
}

function EarlyRunnerRadarPanel({ data, loading }: { data?: EarlyRunnersData; loading: boolean }) {
  const runners = data?.runners ?? []
  const toneFor = (state: string) => {
    const s = String(state || '').toUpperCase()
    if (s === 'PROOF_CANDIDATE') return '#60a5fa'
    if (s === 'MANUAL_SNIPER') return '#f97316'
    if (s === 'MONITOR_NOW') return '#00d48a'
    if (s === 'FAST_WATCH') return '#f59e0b'
    return '#7f95a8'
  }
  const verdictTone = (verdict?: string) => {
    const v = String(verdict || '').toUpperCase()
    if (v === 'PROOF_CANDIDATE') return '#60a5fa'
    if (v === 'LOOK_NOW') return '#00d48a'
    if (v === 'WAIT_FOR_PULLBACK') return '#f59e0b'
    if (v === 'DO_NOT_TOUCH') return '#ef4444'
    return '#7f95a8'
  }
  const entryTone = (entry?: string) => {
    const e = String(entry || '').toUpperCase()
    if (e === 'SCOUT_READY' || e === 'PROOF_REVIEW_READY') return '#00d48a'
    if (e === 'WAIT_PULLBACK') return '#f59e0b'
    if (e === 'NO_TRADE') return '#ef4444'
    return '#7f95a8'
  }

  return (
    <div className="card" style={{
      background: 'rgba(5,10,18,0.76)',
      borderColor: 'rgba(245,158,11,0.22)',
      borderLeft: '3px solid #f59e0b',
      padding: '14px 16px',
      display: 'flex',
      flexDirection: 'column',
      gap: 10,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <span style={{ ...MONO, fontSize: 9, color: '#f59e0b', letterSpacing: '0.14em', fontWeight: 900 }}>EARLY RUNNER RADAR</span>
        <span style={{ ...MONO, fontSize: 8, color: '#7f95a8' }}>
          {data ? `${data.summary.scout_ready ?? 0} scout · ${data.summary.proof_candidate ?? 0} proof · ${data.summary.wait_pullback ?? 0} pullback · ${data.summary.no_trade ?? data.summary.do_not_touch ?? 0} no trade · ${data.lookback_hours}h` : loading ? 'loading' : 'waiting'}
        </span>
        {data?.generated_at && (
          <span style={{ ...MONO, fontSize: 8, color: '#6f8498', marginLeft: 'auto' }}>
            {fmtAge(data.generated_at)}
          </span>
        )}
      </div>
      <div style={{ ...MONO, fontSize: 9, color: '#b6c7d8', lineHeight: 1.5 }}>
        {data?.headline || 'Surfacing early runners before proof. These are not auto-buy approvals.'}
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        {runners.length ? runners.slice(0, 6).map((runner) => {
          const state = runner.escalation_state || runner.state
          const tone = toneFor(state)
          const verdict = runner.verdict || 'WATCH'
          const vTone = verdictTone(verdict)
          const entry = runner.entry_verdict || 'WAIT_CONFIRMATION'
          const eTone = entryTone(entry)
          return (
            <div key={`${runner.mint}-${runner.last_seen_at || runner.scanned_at}`} style={{
              border: `1px solid ${tone}28`,
              background: `${tone}0b`,
              borderRadius: 10,
              padding: '10px 12px',
              display: 'grid',
              gridTemplateColumns: 'minmax(120px, 0.8fr) minmax(0, 1.5fr) auto',
              gap: 10,
              alignItems: 'center',
            }}>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                  <span style={{ ...MONO, fontSize: 12, color: '#f8fafc', fontWeight: 900 }}>{runner.symbol}</span>
                  <span style={{ ...MONO, fontSize: 8, color: tone, fontWeight: 900 }}>{state.replaceAll('_', ' ')}</span>
                  <span style={{
                    ...MONO,
                    fontSize: 8,
                    color: vTone,
                    background: `${vTone}12`,
                    border: `1px solid ${vTone}35`,
                    borderRadius: 5,
                    padding: '2px 5px',
                    fontWeight: 900,
                  }}>{verdict.replaceAll('_', ' ')}</span>
                  <span style={{
                    ...MONO,
                    fontSize: 8,
                    color: eTone,
                    background: `${eTone}10`,
                    border: `1px solid ${eTone}30`,
                    borderRadius: 5,
                    padding: '2px 5px',
                    fontWeight: 900,
                  }}>ENTRY {entry.replaceAll('_', ' ')}</span>
                  <span style={{ ...MONO, fontSize: 8, color: '#7f95a8' }}>{runner.execution_state.replaceAll('_', ' ')}</span>
                </div>
                <TokenAddressChip value={runner.mint} />
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 5, minWidth: 0 }}>
                <div style={{ ...MONO, fontSize: 9, color: '#b6c7d8', lineHeight: 1.45 }}>
                  {runner.action_guidance || runner.reason}
                </div>
                <div style={{
                  ...MONO,
                  fontSize: 8,
                  color: '#d7e1ea',
                  lineHeight: 1.45,
                  background: 'rgba(255,255,255,0.025)',
                  border: '1px solid rgba(255,255,255,0.055)',
                  borderRadius: 6,
                  padding: '6px 8px',
                }}>
                  <span style={{ color: eTone, fontWeight: 900 }}>{runner.entry_setup?.replaceAll('_', ' ') || 'UNCONFIRMED'}</span>
                  <span style={{ color: '#7f95a8' }}> · trigger: </span>{runner.entry_trigger || 'No clean trigger yet.'}
                </div>
                {runner.entry_verdict && runner.entry_verdict !== 'NO_TRADE' && (
                  <div style={{ ...MONO, fontSize: 8, color: '#8fa3b7', lineHeight: 1.45 }}>
                    invalidation: {runner.invalidation_plan || 'setup degradation'} · TP: {runner.take_profit_plan || 'protect principal into strength'}
                  </div>
                )}
                {((runner.verdict_reasons ?? []).length > 0 || (runner.why_it_matters ?? []).length > 0) && (
                  <div style={{ display: 'flex', gap: 5, flexWrap: 'wrap' }}>
                    {((runner.verdict_reasons?.length ? runner.verdict_reasons : runner.why_it_matters) ?? []).slice(0, 4).map(reason => (
                      <span key={reason} style={{
                        ...MONO,
                        fontSize: 8,
                        color: '#fbbf24',
                        background: 'rgba(245,158,11,0.08)',
                        border: '1px solid rgba(245,158,11,0.18)',
                        borderRadius: 4,
                        padding: '2px 5px',
                      }}>{reason}</span>
                    ))}
                  </div>
                )}
                <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                  <span style={{ ...MONO, fontSize: 8, color: '#7f95a8' }}>MC {fmtUsd(runner.mcap_usd)}</span>
                  <span style={{ ...MONO, fontSize: 8, color: '#7f95a8' }}>LIQ {fmtUsd(runner.liquidity_usd)}</span>
                  <span style={{ ...MONO, fontSize: 8, color: '#7f95a8' }}>VOL {fmtUsd(runner.volume_24h)}</span>
                  <span style={{ ...MONO, fontSize: 8, color: '#7f95a8' }}>next {runner.next_check_seconds != null ? `${runner.next_check_seconds}s` : 'queued'}</span>
                  {runner.risk_flags.slice(0, 3).map(flag => (
                    <span key={flag} style={{ ...MONO, fontSize: 8, color: '#ef4444' }}>{flag.replaceAll('_', ' ')}</span>
                  ))}
                  {runner.replay_state === 'MISSED_RUNNER' && (
                    <span style={{ ...MONO, fontSize: 8, color: '#ef4444' }}>missed {fmtFixed(runner.max_return_pct, 0)}%</span>
                  )}
                </div>
              </div>
              <div style={{ textAlign: 'right', display: 'flex', flexDirection: 'column', gap: 3 }}>
                <span style={{ ...MONO, fontSize: 15, color: tone, fontWeight: 900 }}>{fmtFixed(runner.radar_score, 0)}</span>
                <span style={{ ...MONO, fontSize: 8, color: '#7f95a8' }}>radar</span>
                <span style={{ ...MONO, fontSize: 8, color: '#6f8498' }}>{runner.age_minutes != null ? `${Math.round(runner.age_minutes)}m` : 'fresh?'}</span>
              </div>
            </div>
          )
        }) : (
          <div style={{ ...MONO, fontSize: 9, color: '#7f95a8', lineHeight: 1.5 }}>
            No early runners meeting velocity thresholds right now.
          </div>
        )}
      </div>
    </div>
  )
}

function ConvictionRecoveryPanel({ data, loading }: { data?: ConvictionRecoveryData; loading: boolean }) {
  const tokens = data?.tokens ?? []
  const toneFor = (verdict?: string) => {
    const v = String(verdict || '').toUpperCase()
    if (v === 'SCOUT_READY') return '#00d48a'
    if (v === 'WATCH_CLOSE') return '#60a5fa'
    if (v === 'ACCUMULATION_ZONE') return '#f59e0b'
    if (v === 'SPOT_PRICE_ONLY') return '#38bdf8'
    if (v === 'EXIT_WATCH' || v === 'SELL_INTO_STRENGTH') return '#ef4444'
    if (v === 'QUALITY_REJECT') return '#64748b'
    if (v === 'DATA_STALE' || v === 'NO_DATA') return '#7f95a8'
    return '#7f95a8'
  }
  const freshnessTone = (freshness?: string | null) => {
    const f = String(freshness || '').toUpperCase()
    if (f === 'FRESH') return '#00d48a'
    if (f === 'CACHE_FALLBACK') return '#fbbf24'
    if (f === 'SPOT_PRICE_FALLBACK') return '#38bdf8'
    if (f === 'RECENT_PROVIDER_FALLBACK') return '#f59e0b'
    if (f === 'STALE_REVIEW_ONLY' || f === 'NO_DATA') return '#ef4444'
    return '#7f95a8'
  }
  const moneyTone = (state?: string | null) => {
    const s = String(state || '').toUpperCase()
    if (s === 'BUYABLE_NOW') return '#00d48a'
    if (s === 'WAITING_CONFIRMATION') return '#22c55e'
    if (s === 'WAITING_TRIGGER') return '#60a5fa'
    if (s === 'PROTECT_PROFIT') return '#f59e0b'
    if (s === 'DATA_WAIT') return '#38bdf8'
    if (s === 'NO_TRADE') return '#64748b'
    return '#7f95a8'
  }

  return (
    <div className="card" style={{
      background: 'rgba(5,10,18,0.76)',
      borderColor: 'rgba(96,165,250,0.22)',
      borderLeft: '3px solid #60a5fa',
      padding: '14px 16px',
      display: 'flex',
      flexDirection: 'column',
      gap: 10,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <span style={{ ...MONO, fontSize: 9, color: '#60a5fa', letterSpacing: '0.14em', fontWeight: 900 }}>CONVICTION RECOVERY</span>
        <span style={{ ...MONO, fontSize: 8, color: '#7f95a8' }}>
          {data ? `${data.summary.buyable_now ?? 0} buy now · ${data.summary.waiting_confirmation ?? 0} confirming · ${data.summary.waiting_trigger ?? 0} waiting · ${data.summary.protect_profit ?? 0} protect · ${data.summary.auto_expanded ?? 0} expanded · ${data.summary.replay_missed ?? 0} missed` : loading ? 'loading' : 'waiting'}
        </span>
        {data?.generated_at && (
          <span style={{ ...MONO, fontSize: 8, color: '#6f8498', marginLeft: 'auto' }}>{fmtAge(data.generated_at)}</span>
        )}
      </div>
      <div style={{ ...MONO, fontSize: 9, color: '#b6c7d8', lineHeight: 1.5 }}>
        {data?.headline || 'Tracking proven recovery coins for reload entries and sell-before-roundtrip plans.'}
      </div>
      {!!data?.alerts?.recent?.length && (
        <div style={{
          display: 'flex',
          gap: 6,
          flexWrap: 'wrap',
          borderTop: '1px solid rgba(255,255,255,0.06)',
          paddingTop: 8,
        }}>
          {data.alerts.recent.slice(0, 3).map((alert, i) => {
            const aTone = moneyTone(alert.money_state || alert.alert_type)
            return (
              <span key={`${alert.mint}-${alert.ts_utc}-${i}`} style={{
                ...MONO,
                fontSize: 8,
                color: aTone,
                background: `${aTone}10`,
                border: `1px solid ${aTone}28`,
                borderRadius: 6,
                padding: '3px 6px',
                fontWeight: 900,
              }}>
                alert {String(alert.alert_type || '').replaceAll('_', ' ')} · ${alert.symbol || 'UNKNOWN'} · {fmtAge(alert.ts_utc)}
              </span>
            )
          })}
        </div>
      )}
      {(data?.expansion || data?.replay) && (
        <div style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))',
          gap: 8,
        }}>
          <div style={{
            border: '1px solid rgba(34,197,94,0.18)',
            background: 'rgba(34,197,94,0.06)',
            borderRadius: 9,
            padding: '8px 10px',
          }}>
            <div style={{ ...MONO, fontSize: 8, color: '#22c55e', fontWeight: 900, letterSpacing: '0.12em' }}>WATCHLIST EXPANSION</div>
            <div style={{ ...MONO, fontSize: 8, color: '#9fb4c8', lineHeight: 1.45, marginTop: 5 }}>
              {data.expansion?.enabled ? `${data.expansion.candidates_loaded} quality candidates loaded · floor ${data.expansion.min_score}` : 'disabled'}
            </div>
            {!!data.expansion?.top?.length && (
              <div style={{ ...MONO, fontSize: 8, color: '#7f95a8', lineHeight: 1.45, marginTop: 5 }}>
                top: {data.expansion.top.slice(0, 3).map(x => `${x.symbol || 'UNKNOWN'} ${fmtFixed(x.score, 0)}`).join(' · ')}
              </div>
            )}
          </div>
          <div style={{
            border: '1px solid rgba(239,68,68,0.16)',
            background: 'rgba(239,68,68,0.05)',
            borderRadius: 9,
            padding: '8px 10px',
          }}>
            <div style={{ ...MONO, fontSize: 8, color: '#f59e0b', fontWeight: 900, letterSpacing: '0.12em' }}>MISSED RUNNER REPLAY</div>
            <div style={{ ...MONO, fontSize: 8, color: '#9fb4c8', lineHeight: 1.45, marginTop: 5 }}>
              {data.replay ? `${data.replay.missed} missed · ${data.replay.tracked_winners} tracked winners` : 'waiting for replay data'}
            </div>
            {!!data.replay?.top?.length && (
              <div style={{ ...MONO, fontSize: 8, color: '#7f95a8', lineHeight: 1.45, marginTop: 5 }}>
                top: {data.replay.top.slice(0, 3).map(x => `${x.symbol || 'UNKNOWN'} ${fmtFixed(x.max_return_pct, 0)}%`).join(' · ')}
              </div>
            )}
          </div>
        </div>
      )}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: 8 }}>
        {tokens.length ? tokens.slice(0, 8).map(token => {
          const tone = toneFor(token.verdict)
          const fTone = freshnessTone(token.data_freshness)
          const mTone = moneyTone(token.money_state)
          return (
            <div key={token.mint} style={{
              border: `1px solid ${tone}28`,
              background: `${tone}0b`,
              borderRadius: 10,
              padding: '10px 12px',
              display: 'flex',
              flexDirection: 'column',
              gap: 7,
            }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                <span style={{ ...MONO, fontSize: 12, color: '#f8fafc', fontWeight: 900 }}>{token.symbol}</span>
                <span style={{
                  ...MONO,
                  fontSize: 8,
                  color: mTone,
                  background: `${mTone}10`,
                  border: `1px solid ${mTone}30`,
                  borderRadius: 5,
                  padding: '2px 5px',
                  fontWeight: 900,
                }}>{(token.money_state || 'WAIT').replaceAll('_', ' ')}</span>
                <span style={{ ...MONO, fontSize: 8, color: mTone, fontWeight: 900 }}>BUY {fmtFixed(token.buy_trigger_score, 0)}</span>
                <span style={{ ...MONO, fontSize: 8, color: tone, fontWeight: 900 }}>{token.verdict.replaceAll('_', ' ')}</span>
                <span style={{ ...MONO, fontSize: 8, color: '#7f95a8' }}>{token.entry_state.replaceAll('_', ' ')}</span>
                <span style={{
                  ...MONO,
                  fontSize: 8,
                  color: token.quality_floor_pass ? '#00d48a' : '#ef4444',
                  background: token.quality_floor_pass ? 'rgba(0,212,138,0.08)' : 'rgba(239,68,68,0.08)',
                  border: `1px solid ${token.quality_floor_pass ? 'rgba(0,212,138,0.22)' : 'rgba(239,68,68,0.22)'}`,
                  borderRadius: 5,
                  padding: '2px 5px',
                  fontWeight: 900,
                }}>Q {fmtFixed(token.quality_score, 0)}</span>
                <span style={{
                  ...MONO,
                  fontSize: 8,
                  color: fTone,
                  background: `${fTone}10`,
                  border: `1px solid ${fTone}28`,
                  borderRadius: 5,
                  padding: '2px 5px',
                  fontWeight: 900,
                }}>{(token.data_freshness || 'UNKNOWN').replaceAll('_', ' ')}</span>
                {token.confirmation_required_count != null && token.confirmation_state && token.confirmation_state !== 'IDLE' && (
                  <span style={{ ...MONO, fontSize: 8, color: mTone, fontWeight: 900 }}>
                    CONF {fmtFixed(token.confirmation_count, 0)}/{fmtFixed(token.confirmation_required_count, 0)}
                  </span>
                )}
                <span style={{ ...MONO, fontSize: 8, color: '#7f95a8', marginLeft: 'auto' }}>SURV {fmtFixed(token.survivability_score, 0)}</span>
              </div>
              <TokenAddressChip value={token.mint} />
              <div style={{ ...MONO, fontSize: 8, color: '#8fa3b7', lineHeight: 1.45 }}>{token.thesis}</div>
              <div style={{ ...MONO, fontSize: 9, color: '#d7e1ea', lineHeight: 1.45 }}>
                plan: {token.entry_plan || token.trigger}
              </div>
              <div style={{ ...MONO, fontSize: 8, color: '#8fa3b7', lineHeight: 1.45 }}>
                trigger: {token.trigger}
              </div>
              {token.data_freshness && token.data_freshness !== 'FRESH' && (
                <div style={{ ...MONO, fontSize: 8, color: fTone, lineHeight: 1.45 }}>
                  data guard: {token.data_freshness.replaceAll('_', ' ')}
                  {token.snapshot_as_of ? ` · snapshot ${fmtAge(token.snapshot_as_of)}` : ''}
                  {token.provider_source ? ` · ${token.provider_source.replaceAll('_', ' ')}` : ''}
                  {token.last_verdict ? ` · last live verdict ${token.last_verdict.replaceAll('_', ' ')}` : ''}
                </div>
              )}
              {token.data_freshness === 'FRESH' && token.provider_source && (
                <div style={{ ...MONO, fontSize: 8, color: '#6f8498', lineHeight: 1.45 }}>
                  source: {token.provider_source.replaceAll('_', ' ')}
                  {token.snapshot_as_of ? ` · ${fmtAge(token.snapshot_as_of)}` : ''}
                </div>
              )}
              <div style={{ ...MONO, fontSize: 8, color: '#7f95a8', lineHeight: 1.45 }}>
                exit lock: {(token.exit_lock || 'NONE').replaceAll('_', ' ')} {token.exit_trigger_score != null ? `(${fmtFixed(token.exit_trigger_score, 0)})` : ''} · {token.take_profit}
              </div>
              {token.memory_note && (
                <div style={{ ...MONO, fontSize: 8, color: '#9fb4c8', lineHeight: 1.45 }}>
                  memory: {token.memory_note}
                </div>
              )}
              {token.profile === 'auto_watchlist_expansion' && (
                <div style={{ ...MONO, fontSize: 8, color: '#22c55e', lineHeight: 1.45 }}>
                  expanded: score {fmtFixed(token.expansion_score, 0)}
                  {token.expansion_reasons?.length ? ` · ${token.expansion_reasons.slice(0, 3).join(' · ')}` : ''}
                </div>
              )}
              {token.runner_replay_state && token.runner_replay_state !== 'TRACKING' && (
                <div style={{
                  ...MONO,
                  fontSize: 8,
                  color: token.runner_replay_state === 'MISSED_RUNNER' || token.runner_replay_state === 'UNATTRIBUTED_SPIKE' ? '#f59e0b' : '#60a5fa',
                  lineHeight: 1.45,
                }}>
                  replay: {token.runner_replay_note}
                </div>
              )}
              {!!token.buy_trigger_reasons?.length && (
                <div style={{ ...MONO, fontSize: 8, color: '#7f95a8', lineHeight: 1.45 }}>
                  why: {token.buy_trigger_reasons.slice(0, 3).join(' · ')}
                </div>
              )}
              <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                <span style={{ ...MONO, fontSize: 8, color: '#7f95a8' }}>MC {fmtUsd(token.mcap_usd)}</span>
                <span style={{ ...MONO, fontSize: 8, color: '#7f95a8' }}>LIQ {fmtUsd(token.liquidity_usd)}</span>
                <span style={{ ...MONO, fontSize: 8, color: '#7f95a8' }}>VOL/LIQ {fmtFixed(token.vol_liq_ratio, 1)}x</span>
                <span style={{ ...MONO, fontSize: 8, color: '#7f95a8' }}>1h {fmtFixed(token.change_1h, 1)}%</span>
                <span style={{ ...MONO, fontSize: 8, color: '#7f95a8' }}>24h {fmtFixed(token.change_24h, 1)}%</span>
                <span style={{ ...MONO, fontSize: 8, color: '#7f95a8' }}>age {token.age_days != null ? `${fmtFixed(token.age_days, 0)}d` : '—'}</span>
                {token.from_reference_multiple != null && (
                  <span style={{ ...MONO, fontSize: 8, color: '#fbbf24' }}>{fmtFixed(token.from_reference_multiple, 1)}x your zone</span>
                )}
                {token.multiple_from_observed_low != null && (
                  <span style={{ ...MONO, fontSize: 8, color: '#60a5fa' }}>{fmtFixed(token.multiple_from_observed_low, 1)}x memory low</span>
                )}
                {token.drawdown_from_observed_high_pct != null && (
                  <span style={{ ...MONO, fontSize: 8, color: '#94a3b8' }}>{fmtFixed(token.drawdown_from_observed_high_pct, 0)}% from memory high</span>
                )}
              </div>
            </div>
          )
        }) : (
          <div style={{ ...MONO, fontSize: 9, color: '#7f95a8', lineHeight: 1.5 }}>
            No conviction recovery tokens loaded.
          </div>
        )}
      </div>
    </div>
  )
}

export function ProviderRecoveryPanel({ audit }: { audit: SystemAuditData | undefined }) {
  const recovery = audit?.runtime?.provider_recovery
  const decisionMode = audit?.runtime?.decision_mode
  const sourceStrategy = audit?.runtime?.source_strategy
  const checklist = audit?.runtime?.recovery_checklist ?? []
  const feeds = recovery?.feeds ?? []
  const toneFor = (status: string) => {
    const s = String(status || '').toUpperCase()
    if (s === 'ACTIVE' || s === 'RECOVERED') return '#00d48a'
    if (s === 'DISABLED_INTENTIONAL' || s === 'INDEPENDENT_MODE') return '#60a5fa'
    if (s === 'DISABLED' && sourceStrategy?.independent_mode) return '#60a5fa'
    if (s === 'REPLACEMENT_NEEDED') return '#f59e0b'
    if (s === 'RECOVERING' || s === 'DEGRADED') return '#f59e0b'
    if (s === 'ERROR' || s === 'DISABLED' || s === 'STALE') return '#ef4444'
    return '#7f95a8'
  }

  return (
    <div className="card" style={{
      background: 'rgba(5,10,18,0.72)',
      borderColor: 'rgba(255,255,255,0.06)',
      padding: '12px 16px',
      display: 'flex',
      flexDirection: 'column',
      gap: 10,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <span style={{ ...MONO, fontSize: 9, color: 'var(--dim)', letterSpacing: '0.12em' }}>DATA RECOVERY</span>
        <span style={{ ...MONO, fontSize: 8, color: recovery?.limiting_count ? '#f59e0b' : '#00d48a' }}>
          {recovery ? `${recovery.limiting_count} limiting` : 'loading'}
        </span>
      </div>
      <div style={{ ...MONO, fontSize: 9, color: '#8ca0b3', lineHeight: 1.5 }}>
        {recovery?.headline || 'Waiting for provider recovery status.'}
      </div>
      {sourceStrategy?.independent_mode && (
        <div style={{ ...MONO, fontSize: 8, color: '#60a5fa', lineHeight: 1.45 }}>
          {sourceStrategy.detail || 'Independent mode is active; premium BirdEye feeds are not required.'}
        </div>
      )}
      {decisionMode && (
        <div style={{
          border: '1px solid rgba(96,165,250,0.20)',
          background: 'rgba(96,165,250,0.055)',
          borderRadius: 8,
          padding: '8px 10px',
          display: 'flex',
          flexDirection: 'column',
          gap: 5,
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
            <span style={{ ...MONO, fontSize: 8, color: '#60a5fa', fontWeight: 900, letterSpacing: '0.1em' }}>
              {decisionMode.mode.replace(/_/g, ' ')}
            </span>
            <span style={{ ...MONO, fontSize: 8, color: decisionMode.fallback_active ? '#f59e0b' : '#00d48a' }}>
              {decisionMode.fallback_active ? `fallback · ${decisionMode.market_primary || 'active'}` : 'live source'}
            </span>
          </div>
          <span style={{ ...MONO, fontSize: 8, color: '#b6c7d8', lineHeight: 1.45 }}>
            {decisionMode.headline}
          </span>
        </div>
      )}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 8 }}>
        {feeds.slice(0, 4).map(feed => {
          const tone = toneFor(feed.status)
          return (
            <div key={feed.key} style={{
              border: `1px solid ${tone}28`,
              background: `${tone}0d`,
              borderRadius: 8,
              padding: '8px 10px',
              display: 'flex',
              flexDirection: 'column',
              gap: 4,
            }}>
              <span style={{ ...MONO, fontSize: 8, color: '#7f95a8', letterSpacing: '0.08em' }}>{feed.label}</span>
              <span style={{ ...MONO, fontSize: 10, color: tone, fontWeight: 800 }}>{feed.status}</span>
              <span style={{ ...MONO, fontSize: 8, color: '#b6c7d8', lineHeight: 1.4 }}>
                {feed.recovery_state || feed.reason || feed.detail || 'fresh'}
              </span>
            </div>
          )
        })}
      </div>
      {checklist.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          {checklist.slice(0, 6).map(item => {
            const tone = toneFor(item.state)
            return (
              <div key={item.key} style={{
                display: 'grid',
                gridTemplateColumns: '115px minmax(0, 1fr)',
                gap: 8,
                borderTop: '1px solid rgba(255,255,255,0.05)',
                paddingTop: 6,
              }}>
                <span style={{ ...MONO, fontSize: 8, color: tone, fontWeight: 800 }}>{item.label}</span>
                <span style={{ ...MONO, fontSize: 8, color: '#8ca0b3', lineHeight: 1.45 }}>
                  {item.state.replace(/_/g, ' ').toLowerCase()} · {item.next_step}
                </span>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

function GoodBuyBoardPanel({ board }: { board: GoodBuyBoardV2 | undefined }) {
  if (!board) return null

  const buyable = board.buyable ?? []
  const wait = board.wait ?? []
  const blocked = board.blocked ?? []
  const statusTone =
    board.status === 'BUYABLE' ? '#00d48a'
    : board.status === 'WAIT' ? '#f59e0b'
    : board.status === 'BLOCKED' ? '#ef4444'
    : '#7f95a8'
  const primary = buyable[0] ?? wait[0] ?? blocked[0] ?? null

  const StatePill = ({ item }: { item: GoodBuyItem }) => {
    const tone =
      item.state === 'BUYABLE' ? '#00d48a'
      : item.state === 'WAIT' ? '#f59e0b'
      : '#ef4444'
    return (
      <span style={{
        ...MONO,
        fontSize: 8,
        color: tone,
        background: `${tone}12`,
        border: `1px solid ${tone}28`,
        borderRadius: 999,
        padding: '2px 7px',
        fontWeight: 800,
        letterSpacing: '0.08em',
      }}>
        {item.state}
      </span>
    )
  }

  const MiniMetric = ({ label, value, tone = '#8ca0b3' }: { label: string; value: string; tone?: string }) => (
    <span style={{ ...MONO, fontSize: 8, color: tone }}>
      {label} <span style={{ color: '#d7e1ea' }}>{value}</span>
    </span>
  )

  const GoodBuyRow = ({ item, compact = false }: { item: GoodBuyItem; compact?: boolean }) => {
    const tone =
      item.state === 'BUYABLE' ? '#00d48a'
      : item.state === 'WAIT' ? '#f59e0b'
      : '#ef4444'
    const reasons = item.state === 'BLOCKED' ? item.blockers : item.warnings.length ? item.warnings : item.strengths
    const ticket = item.execution_ticket
    const ticketTone =
      ticket?.execution_state === 'READY_GUARDED' || ticket?.execution_state === 'READY_MANUAL_CONFIRM' ? '#00d48a'
      : ticket?.execution_state === 'AUTHORITY_BLOCKED' || ticket?.execution_state === 'WAIT_CONFIRMATION' ? '#f59e0b'
      : '#ef4444'
    return (
      <div style={{
        border: `1px solid ${tone}22`,
        borderLeft: `3px solid ${tone}`,
        background: `${tone}08`,
        borderRadius: '0 10px 10px 0',
        padding: compact ? '8px 10px' : '11px 12px',
        display: 'flex',
        flexDirection: 'column',
        gap: compact ? 6 : 8,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
          <span style={{ ...MONO, fontSize: compact ? 11 : 13, color: tone, fontWeight: 900, letterSpacing: '0.04em' }}>
            {item.symbol}
          </span>
          <StatePill item={item} />
          <span style={{ ...MONO, fontSize: 8, color: '#7f95a8' }}>{item.lane}</span>
          <TokenAddressChip value={item.mint} size={compact ? 'compact' : 'roomy'} />
          <span style={{ ...MONO, fontSize: 8, color: '#d7e1ea', marginLeft: 'auto' }}>
            {fmtFixed(item.good_buy_score, 1)}
          </span>
        </div>
        <div style={{ ...MONO, fontSize: compact ? 8 : 9, color: '#b6c7d8', lineHeight: 1.55 }}>
          {item.headline}
        </div>
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
          <MiniMetric label="MC" value={fmtUsd(item.metrics.marketcap_usd)} />
          <MiniMetric label="LIQ" value={fmtUsd(item.metrics.liquidity_usd)} />
          <MiniMetric label="VOL" value={fmtUsd(item.metrics.volume_24h_usd)} />
          <MiniMetric label="P" value={fmtFixed(item.metrics.pressure_score, 0)} tone={item.metrics.pressure_score >= 62 ? '#00d48a' : '#f59e0b'} />
          <MiniMetric label="RISK" value={fmtFixed(item.metrics.risk_score, 0)} tone={item.metrics.risk_score >= 75 ? '#00d48a' : '#f59e0b'} />
          <MiniMetric label="1H" value={`${fmtFixed(item.metrics.change_1h_pct, 1)}%`} tone={item.metrics.change_1h_pct >= 0 ? '#00d48a' : '#ef4444'} />
          <MiniMetric label="DATA" value={`${item.data.data_freshness}/${item.data.identity_status}`} tone={item.data.data_freshness === 'LIVE' && item.data.identity_status === 'RESOLVED' ? '#00d48a' : '#f59e0b'} />
        </div>
        {ticket && (
          <div style={{
            border: `1px solid ${ticketTone}24`,
            background: `${ticketTone}0c`,
            borderRadius: 8,
            padding: compact ? '6px 7px' : '8px 9px',
            display: 'flex',
            flexDirection: 'column',
            gap: 6,
          }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
              <span style={{ ...MONO, fontSize: 8, color: ticketTone, fontWeight: 900, letterSpacing: '0.11em' }}>
                TICKET {ticket.execution_state.replaceAll('_', ' ')}
              </span>
              <span style={{ ...MONO, fontSize: 8, color: '#8ca0b3' }}>
                {ticket.mode} · {ticket.route}
              </span>
              <span style={{ ...MONO, fontSize: 8, color: '#d7e1ea', marginLeft: 'auto' }}>
                size {fmtUsd(ticket.suggested_size_usd)}
              </span>
            </div>
            <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
              <MiniMetric label="ENTRY" value={fmtTokenPrice(ticket.entry?.reference_price_usd ?? item.metrics.price_usd)} tone="#d7e1ea" />
              <MiniMetric label="MAX" value={fmtTokenPrice(ticket.entry?.max_chase_price_usd)} tone="#fbbf24" />
              <MiniMetric label="INV" value={fmtTokenPrice(ticket.invalidation?.price_usd)} tone="#ef4444" />
              <MiniMetric label="TP1" value={ticket.take_profit?.tp1_marketcap_usd ? fmtUsd(ticket.take_profit.tp1_marketcap_usd) : fmtTokenPrice(ticket.take_profit?.tp1_price_usd)} tone="#00d48a" />
              <MiniMetric label="LAW" value={ticket.authority?.highest_permitted_action?.replaceAll('_', ' ') || '—'} tone={ticket.executable ? '#00d48a' : '#f59e0b'} />
            </div>
            {!compact && ticket.blockers && ticket.blockers.length > 0 && (
              <div style={{ ...MONO, fontSize: 8, color: '#9fb3c8', lineHeight: 1.45 }}>
                blocker: {ticket.blockers.slice(0, 3).join(' · ')}
              </div>
            )}
          </div>
        )}
        {reasons.length > 0 && (
          <div style={{ display: 'flex', gap: 5, flexWrap: 'wrap' }}>
            {reasons.slice(0, compact ? 3 : 5).map((reason, i) => (
              <span key={`${item.mint}-${reason}-${i}`} style={{
                ...MONO,
                fontSize: 8,
                color: item.state === 'BLOCKED' ? '#ff7777' : item.state === 'WAIT' ? '#fbbf24' : '#6ee7b7',
                background: item.state === 'BLOCKED' ? 'rgba(239,68,68,0.10)' : item.state === 'WAIT' ? 'rgba(245,158,11,0.10)' : 'rgba(0,212,138,0.08)',
                border: item.state === 'BLOCKED' ? '1px solid rgba(239,68,68,0.20)' : item.state === 'WAIT' ? '1px solid rgba(245,158,11,0.20)' : '1px solid rgba(0,212,138,0.18)',
                borderRadius: 5,
                padding: '2px 6px',
              }}>
                {reason}
              </span>
            ))}
          </div>
        )}
      </div>
    )
  }

  return (
    <div style={{
      border: `1px solid ${statusTone}26`,
      borderTop: `2px solid ${statusTone}`,
      borderRadius: 16,
      padding: 16,
      background:
        `radial-gradient(circle at 8% 0%, ${statusTone}14 0%, transparent 28%),` +
        'linear-gradient(180deg, rgba(5,10,18,0.78), rgba(3,7,13,0.72))',
      display: 'flex',
      flexDirection: 'column',
	      gap: 12,
	    }} title={DAILY_INTELLIGENCE_REV}>
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12, flexWrap: 'wrap' }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 5, minWidth: 240, flex: 1 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
            <span style={{ ...MONO, fontSize: 9, color: statusTone, fontWeight: 900, letterSpacing: '0.16em' }}>
              GOOD BUY BOARD V2
            </span>
            <span style={{
              ...MONO,
              fontSize: 8,
              color: statusTone,
              background: `${statusTone}12`,
              border: `1px solid ${statusTone}28`,
              borderRadius: 999,
              padding: '2px 8px',
            }}>
              {board.status}
            </span>
            {board.summary.provider_warning && (
              <span style={{ ...MONO, fontSize: 8, color: '#f59e0b' }}>
                provider: {board.summary.provider_warning}
              </span>
            )}
          </div>
          <span style={{ ...MONO, fontSize: 9, color: '#9fb3c8', lineHeight: 1.55 }}>
            {board.headline}
          </span>
        </div>
        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
          <MiniMetric label="BUY" value={`${board.summary.buyable}`} tone={board.summary.buyable ? '#00d48a' : '#506276'} />
          <MiniMetric label="WAIT" value={`${board.summary.wait}`} tone={board.summary.wait ? '#f59e0b' : '#506276'} />
          <MiniMetric label="BLOCK" value={`${board.summary.blocked}`} tone={board.summary.blocked ? '#ef4444' : '#506276'} />
          <MiniMetric label="AGE" value={fmtAge(board.generated_at)} />
        </div>
      </div>

      {primary && <GoodBuyRow item={primary} />}

      <div className="good-buy-grid" style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: 10 }}>
        {(buyable.length ? buyable.slice(1, 4) : wait.slice(0, 4)).map(item => (
          <GoodBuyRow key={`${item.state}-${item.mint}`} item={item} compact />
        ))}
      </div>

      {!primary && (
        <div style={{ ...MONO, fontSize: 9, color: '#7f95a8' }}>
          Waiting for Token Intelligence to produce named memecoin or spot candidates.
        </div>
      )}
    </div>
  )
}

function DailyCryptoBriefPanel({
  data,
  loading,
  onEscalationAction,
  onEscalationReviewAction,
  onEscalationPatchAction,
  onEscalationWorkOrderAction,
  onLiveContextMissionAction,
  pendingEscalationId,
  pendingEscalationReviewKey,
  pendingEscalationPatchKey,
  pendingEscalationWorkOrderKey,
  pendingLiveContextMissionKey,
}: {
  data?: DailyCryptoBriefData
  loading: boolean
  onEscalationAction?: (item: ProviderEscalationItem, action: ProviderEscalationAction) => void
  onEscalationReviewAction?: (item: ProviderEscalationReviewGroup, state: ProviderEscalationReviewState) => void
  onEscalationPatchAction?: (item: ProviderEscalationPatchPlan, state: ProviderEscalationPatchState) => void
  onEscalationWorkOrderAction?: (item: ProviderEscalationWorkOrder, state: ProviderEscalationWorkOrderState) => void
  onLiveContextMissionAction?: (item: DailyTopMission | LiveOpportunityGap | LiveContextMissionOutcomeItem, state: LiveContextMissionState) => void
  pendingEscalationId?: number | null
  pendingEscalationReviewKey?: string | null
  pendingEscalationPatchKey?: string | null
  pendingEscalationWorkOrderKey?: string | null
  pendingLiveContextMissionKey?: string | null
}) {
  if (loading && !data) {
    return (
      <div style={{ ...MONO, fontSize: 8, color: 'var(--chrome)', padding: '8px 0' }}>
        building daily crypto brief…
      </div>
    )
  }
  if (!data) return null

  const tone =
    data.status === 'WATCH' ? '#00d48a'
    : data.status === 'LOCK_REVIEW' ? '#ef4444'
    : '#f59e0b'
  const safety = data.safety ?? {
    execution_lock: 'UNKNOWN',
    execution_intents: 0,
    executed_intents: 0,
    open_memecoin_trades: null,
    intent_modes: {},
    authority_verdicts: {},
  }
  const freshness = data.data_freshness ?? {
    rows: 0,
    freshness_counts: {},
    confidence_counts: {},
    stale_high_quality: [],
  }
  const decision = data.decision_quality ?? {
    journal_count: 0,
    surface_counts: {},
    action_counts: {},
    outcome_counts: {},
    missed_runner_count: 0,
    weak_buy_count: 0,
  }
  const paper = data.paper_pilot ?? {
    opened_count: 0,
    status_counts: {},
    outcome_counts: {},
  }
    const missedRunners = data.top_missed_runners ?? []
    const weakBuyCalls = data.weak_buy_calls ?? []
    const paperMovers = data.top_paper_movers ?? []
    const nextActions = data.next_actions ?? []
    const autopsy = data.outcome_autopsy ?? {}
    const autopsySummary = autopsy.summary ?? {}
    const topRule = autopsy.top_rule_to_review ?? null
    const autopsyExamples = autopsy.examples ?? []
    const simulator = data.rule_simulator ?? {}
    const simTop = simulator.top_candidate ?? null
    const replay = data.candidate_replay_timeline ?? {}
    const replayEvents = replay.events ?? []
    const watchdog = data.data_watchdog ?? {}
    const freshnessSla = data.freshness_sla ?? {}
    const providerReliability = data.provider_reliability ?? {}
    const providerDrilldown = data.provider_failure_drilldown ?? {}
    const escalationQueue = data.provider_escalation_queue ?? {}
    const escalationAccuracy = data.provider_escalation_accuracy ?? {}
    const escalationMaturity = data.provider_escalation_maturity ?? {}
    const escalationAlerts = data.provider_escalation_alerts ?? {}
    const escalationReviewQueue = data.provider_escalation_review_queue ?? {}
    const escalationPatchPlans = data.provider_escalation_patch_plans ?? {}
    const escalationWorkOrders = data.provider_escalation_work_orders ?? {}
    const escalationExecutionPacks = data.provider_escalation_execution_packs ?? {}
    const postPatchOutcomes = data.provider_post_patch_outcomes ?? {}
    const regressionGuard = data.provider_regression_guard ?? {}
    const escalationAutorun = data.provider_escalation_outcome_autorun ?? {}
    const ruleGate = data.rule_promotion_gate ?? {}
    const missedClusters = data.missed_runner_clusters ?? {}
    const catalystContext = data.catalyst_context ?? {}
    const liveIntake = data.live_market_narrative_intake ?? {}
    const liveGaps = data.live_opportunity_gaps ?? {}
    const liveDossier = data.live_context_mission_dossier ?? {}
    const liveResolution = data.live_context_blind_spot_resolution ?? {}
    const liveMissionJournal = data.live_context_mission_outcome_journal ?? {}
    const liveReviewQueue = data.live_context_review_queue ?? {}
    const liveDecisionAccuracy = data.live_context_decision_accuracy ?? {}
    const livePolicySuggestions = data.live_context_policy_suggestions ?? {}
    const liveMission = data.live_context_generated_mission ?? {}
    const liveEvidence = data.live_context_evidence_requirements ?? {}
    const liveOutcomeLoop = data.live_context_outcome_loop ?? {}
    const buildScore = data.daily_build_score ?? {}
    const topMission = data.daily_top_mission ?? {}
    const pilotGate = data.paper_to_pilot_gate ?? {}
    const providerRows = watchdog.by_source ?? []
    const refreshTargets = watchdog.refresh_priority ?? []
    const topRefreshTarget = refreshTargets[0]
    const providerLine = providerRows.slice(0, 3)
      .map(row => `${String(row.source || 'unknown').toLowerCase()} ${row.live ?? 0}/${row.stale ?? 0} stale`)
      .join(' · ')
    const topProviderFailure = providerReliability.top_failures?.[0]
    const topProviderDrilldown = providerDrilldown.items?.[0]
    const topEscalation = escalationQueue.items?.[0]
    const topEscalationMiss = escalationAccuracy.missed_examples?.[0]
    const nextEscalationDue = escalationMaturity.next_due?.[0]
    const topEscalationAlert = escalationAlerts.items?.[0]
    const topEscalationReview = escalationReviewQueue.items?.[0]
    const topEscalationPatch = escalationPatchPlans.top_plan ?? escalationPatchPlans.items?.[0]
    const topEscalationWorkOrder = escalationWorkOrders.top_work_order ?? escalationWorkOrders.items?.[0]
    const topEscalationExecutionPack = escalationExecutionPacks.top_pack ?? escalationExecutionPacks.items?.[0]
    const workbenchPack = topMission.workbench_pack ?? topEscalationExecutionPack
    const executionPackEvidence = workbenchPack?.evidence_bundle ?? {}
    const executionPackRecipe = workbenchPack?.replay_test_recipe ?? []
    const executionPackTargets = workbenchPack?.target_code_map ?? []
    const executionPackCriteria = workbenchPack?.completion_criteria ?? []
    const executionPackTone =
      regressionGuard.status === 'ACTIVE' ? '#ef4444'
      : workbenchPack?.state === 'ACTIVE' ? '#00d48a'
      : workbenchPack ? '#f59e0b'
      : '#60a5fa'
    const executionPackState =
      regressionGuard.status === 'ACTIVE' ? 'GUARDED'
      : workbenchPack?.state === 'ACTIVE' ? 'IMPLEMENTING'
      : workbenchPack?.state === 'READY' ? 'READY TO START'
      : 'WAITING'
    const executionPackTarget = executionPackTargets[0]
    const executionPackRecipeStep = executionPackRecipe[0]
    const topPostPatch = postPatchOutcomes.top
    const topRegression = regressionGuard.items?.[0]
    const topLiveItem = liveIntake.items?.[0]
    const topLiveGap = liveGaps.top_gap ?? liveGaps.items?.[0]
    const topLiveReview = liveReviewQueue.top_item ?? liveReviewQueue.items?.[0]
    const topLivePolicy = livePolicySuggestions.items?.[0]
    const liveMissionActionItem = liveMission.group_key ? liveMission : topLiveReview ?? topLiveGap
    const topEvidenceRequirement = liveEvidence.requirements?.[0]
    const buildHooks = data.daily_build_hooks ?? {}
    const countText = (counts: Record<string, number>, keys: string[]) =>
      keys.map(key => `${key.toLowerCase()} ${counts[key] ?? 0}`).join(' · ')
  const Metric = ({ label, value, valueTone = '#d7e1ea' }: { label: string; value: string; valueTone?: string }) => (
    <div style={{
      border: '1px solid rgba(255,255,255,0.08)',
      background: 'rgba(255,255,255,0.025)',
      borderRadius: 8,
      padding: '8px 10px',
      minHeight: 54,
      display: 'flex',
      flexDirection: 'column',
      justifyContent: 'center',
      gap: 4,
    }}>
      <span style={{ ...MONO, fontSize: 7, color: '#7f95a8', letterSpacing: '0.14em' }}>{label}</span>
      <span style={{ ...MONO, fontSize: 13, color: valueTone, fontWeight: 900 }}>{value}</span>
    </div>
  )
    const TokenLine = ({ item, tone }: { item: DailyBriefTokenItem; tone: string }) => (
    <div style={{
      display: 'grid',
      gridTemplateColumns: '86px 76px 1fr',
      gap: 8,
      alignItems: 'center',
      padding: '7px 0',
      borderTop: '1px solid rgba(255,255,255,0.055)',
    }}>
      <span style={{ ...MONO, fontSize: 10, color: '#f3f7fb', fontWeight: 900, overflow: 'hidden', textOverflow: 'ellipsis' }}>
        {item.symbol || 'UNKNOWN'}
      </span>
      <span style={{ ...MONO, fontSize: 8, color: tone }}>
        {fmtPct(item.max_return_pct)}
      </span>
      <span style={{ ...MONO, fontSize: 8, color: '#8ca0b3', lineHeight: 1.45, overflow: 'hidden', textOverflow: 'ellipsis' }}>
        {(item.priority || item.outcome_label || item.status || 'tracking').replace(/_/g, ' ').toLowerCase()}
        {item.reason ? ` · ${item.reason}` : ''}
      </span>
    </div>
    )
    const AutopsyLine = ({ item }: { item: DailyBriefAutopsyExample }) => {
      const classification = (item.classification || 'TRACKING').replace(/_/g, ' ').toLowerCase()
      const direction = (item.direction || 'WAIT').replace(/_/g, ' ').toLowerCase()
      const tone =
        item.classification === 'MISSED_RUNNER' ? '#f59e0b'
        : item.classification === 'WEAK_BUY' ? '#ef4444'
        : item.classification === 'PROTECTED' ? '#00d48a'
        : '#60a5fa'
      return (
        <div style={{
          display: 'grid',
          gridTemplateColumns: '86px 92px 1fr',
          gap: 8,
          alignItems: 'center',
          padding: '7px 0',
          borderTop: '1px solid rgba(255,255,255,0.055)',
        }}>
          <span style={{ ...MONO, fontSize: 10, color: '#f3f7fb', fontWeight: 900, overflow: 'hidden', textOverflow: 'ellipsis' }}>
            {item.symbol || 'UNKNOWN'}
          </span>
          <span style={{ ...MONO, fontSize: 8, color: tone }}>
            {classification}
          </span>
          <span style={{ ...MONO, fontSize: 8, color: '#8ca0b3', lineHeight: 1.45, overflow: 'hidden', textOverflow: 'ellipsis' }}>
            {direction} · {item.primary_blocker?.label || item.outcome_label || 'collecting evidence'} · {fmtPct(item.max_return_pct)}
          </span>
        </div>
      )
    }
    const WorkbenchItem = ({ label, value, tone: itemTone = '#d7e1ea' }: { label: string; value: string; tone?: string }) => (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 4, minWidth: 0 }}>
        <span style={{ ...MONO, fontSize: 7, color: '#7f95a8', letterSpacing: '0.14em' }}>{label}</span>
        <span style={{ ...MONO, fontSize: 10, color: itemTone, fontWeight: 900, lineHeight: 1.35, overflowWrap: 'anywhere' }}>{value}</span>
      </div>
    )

  return (
    <div style={{
      border: `1px solid ${tone}28`,
      borderTop: `2px solid ${tone}`,
      borderRadius: 16,
      padding: 16,
      background: 'linear-gradient(180deg, rgba(5,10,18,0.78), rgba(3,7,13,0.72))',
      display: 'flex',
      flexDirection: 'column',
      gap: 12,
    }}>
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12, flexWrap: 'wrap' }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 5, minWidth: 260, flex: 1 }}>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
            <span style={{ ...MONO, fontSize: 9, color: tone, fontWeight: 900, letterSpacing: '0.16em' }}>
              DAILY CRYPTO BRIEF
            </span>
            <span style={{
              ...MONO,
              fontSize: 8,
              color: tone,
              background: `${tone}12`,
              border: `1px solid ${tone}28`,
              borderRadius: 999,
              padding: '2px 8px',
            }}>
              {data.status.replace(/_/g, ' ')}
            </span>
            <span style={{ ...MONO, fontSize: 8, color: '#7f95a8' }}>
              {data.lookback_hours}h · {fmtAge(data.generated_at)}
            </span>
          </div>
          <span style={{ ...MONO, fontSize: 9, color: '#b6c7d8', lineHeight: 1.55 }}>
            {data.headline}
          </span>
        </div>
      </div>

        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 8 }}>
          <Metric label="LOCK" value={safety.execution_lock} valueTone={safety.execution_lock === 'LOCKED' ? '#00d48a' : '#ef4444'} />
          <Metric label="INTENTS" value={`${safety.execution_intents}/${safety.executed_intents} exec`} valueTone={safety.executed_intents ? '#ef4444' : '#60a5fa'} />
          <Metric label="DATA" value={countText(freshness.freshness_counts, ['LIVE', 'RECENT', 'STALE'])} valueTone={(freshness.freshness_counts.STALE ?? 0) > (freshness.freshness_counts.LIVE ?? 0) + (freshness.freshness_counts.RECENT ?? 0) ? '#f59e0b' : '#00d48a'} />
          <Metric label="DECISIONS" value={`${decision.journal_count} · ${fmtPct(decision.avg_max_return_pct)}`} />
          <Metric label="MISSED/WEAK" value={`${decision.missed_runner_count}/${decision.weak_buy_count}`} valueTone={decision.missed_runner_count || decision.weak_buy_count ? '#f59e0b' : '#00d48a'} />
          <Metric label="PAPER" value={`${paper.opened_count} · max ${fmtPct(paper.avg_max_return_pct)}`} valueTone="#a78bfa" />
        </div>

        <div style={{
          border: `1px solid ${executionPackTone}28`,
          borderLeft: `3px solid ${executionPackTone}`,
          borderRadius: 10,
          padding: 12,
          background: workbenchPack || topMission.status ? `${executionPackTone}08` : 'rgba(96,165,250,0.018)',
          display: 'flex',
          flexDirection: 'column',
          gap: 12,
        }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'flex-start', flexWrap: 'wrap' }}>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 5, minWidth: 260, flex: 1 }}>
              <div style={{ display: 'flex', gap: 7, alignItems: 'center', flexWrap: 'wrap' }}>
                <span style={{ ...MONO, fontSize: 8, color: executionPackTone, fontWeight: 900, letterSpacing: '0.14em' }}>
                  DAILY TOP MISSION
                </span>
                <span style={{ ...MONO, fontSize: 8, color: executionPackTone, background: `${executionPackTone}12`, border: `1px solid ${executionPackTone}28`, borderRadius: 999, padding: '2px 8px' }}>
                  {String(topMission.mission_type || executionPackState).replace(/_/g, ' ')}
                </span>
                <span style={{ ...MONO, fontSize: 8, color: '#7f95a8' }}>
                  score {fmtFixed(topMission.priority_score ?? workbenchPack?.priority_score, 0)} · packs {escalationExecutionPacks.pack_count ?? 0} · active {escalationExecutionPacks.active_count ?? 0}
                </span>
              </div>
              <span style={{ ...MONO, fontSize: 11, color: '#d7e1ea', lineHeight: 1.45, fontWeight: 900 }}>
                {topMission.headline
                  || (workbenchPack
                    ? workbenchPack.next_action || 'Use this pack as the implementation handoff.'
                    : escalationExecutionPacks.next_action || 'Start a ready work order to activate an execution pack.')}
              </span>
              <span style={{ ...MONO, fontSize: 8, color: '#8ca0b3', lineHeight: 1.5 }}>
                {topMission.expected_upside
                  || (workbenchPack
                    ? executionPackEvidence.why_it_matters || 'Pack is carrying evidence, target map, replay test, and completion criteria.'
                    : 'No pack is active yet. Promote a patch plan into a work order, then start the work order here.')}
              </span>
            </div>
            {workbenchPack && onEscalationWorkOrderAction && (
              <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
                {([
                  ['STARTED', 'START'],
                  ['BLOCKED', 'BLOCK'],
                  ['COMPLETE', 'READY FOR REVIEW'],
                ] as Array<[ProviderEscalationWorkOrderState, string]>).map(([state, label]) => (
                  <button
                    key={`execution-pack-workbench-${state}`}
                    type="button"
                    className="mini-btn"
                    disabled={pendingEscalationWorkOrderKey === workbenchPack.group_key}
                    onClick={() => onEscalationWorkOrderAction(topEscalationWorkOrder ?? { group_key: workbenchPack.group_key }, state)}
                    style={{ fontSize: 7, padding: '5px 8px' }}
                  >
                    {label}
                  </button>
                ))}
              </div>
            )}
          </div>

          {workbenchPack ? (
            <>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(170px, 1fr))', gap: 10 }}>
                <WorkbenchItem label="MISSION" value={`${topMission.title || 'daily build mission'} · ${String(topMission.status || executionPackState).replace(/_/g, ' ').toLowerCase()}`} tone={executionPackTone} />
                <WorkbenchItem label="TARGET" value={`${workbenchPack.target_subsystem || topMission.target?.subsystem || 'subsystem'} · ${String(workbenchPack.risk_label || 'risk').replace(/_/g, ' ').toLowerCase()}`} tone={executionPackTone} />
                <WorkbenchItem label="EVIDENCE" value={`${(executionPackEvidence.symbols ?? []).slice(0, 3).join(', ') || 'system'} · max ${fmtPct(executionPackEvidence.max_return_pct)} · conf ${fmtFixed(executionPackEvidence.confidence_score, 0)}`} />
                <WorkbenchItem label="SCORE" value={`${fmtFixed(workbenchPack.priority_score, 0)} · ${workbenchPack.mission_rank_reason || 'ranked by impact, confidence, safety, and risk'}`} />
                <WorkbenchItem label="BENEFIT/RISK" value={`${executionPackEvidence.simulated_benefit_n ?? 0} / ${executionPackEvidence.weak_buy_risk_n ?? 0} · ${executionPackEvidence.gate_status || 'gate unknown'}`} />
                <WorkbenchItem label="PATCH TARGET" value={executionPackTarget ? `${executionPackTarget.file || 'file'} · ${executionPackTarget.function || 'function'}` : 'target map pending'} />
              </div>

              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: 12 }}>
                <div style={{ minWidth: 0 }}>
                  <span style={{ ...MONO, fontSize: 8, color: '#60a5fa', fontWeight: 900, letterSpacing: '0.14em' }}>
                    REPLAY TEST
                  </span>
                  <div style={{ ...MONO, fontSize: 9, color: '#d7e1ea', marginTop: 6, lineHeight: 1.45 }}>
                    {topMission.proof || executionPackRecipeStep?.label || 'Replay recipe pending.'}
                  </div>
                  <pre style={{
                    ...MONO,
                    margin: '8px 0 0',
                    padding: 9,
                    border: '1px solid rgba(96,165,250,0.18)',
                    borderRadius: 8,
                    background: 'rgba(0,0,0,0.2)',
                    color: '#9db7ce',
                    fontSize: 8,
                    lineHeight: 1.45,
                    whiteSpace: 'pre-wrap',
                    overflowWrap: 'anywhere',
                  }}>
                    {executionPackRecipeStep?.command || 'No command published yet.'}
                  </pre>
                </div>
                <div style={{ minWidth: 0 }}>
                  <span style={{ ...MONO, fontSize: 8, color: '#00d48a', fontWeight: 900, letterSpacing: '0.14em' }}>
                    COMPLETION CRITERIA
                  </span>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginTop: 7 }}>
                    {executionPackCriteria.slice(0, 4).map((criterion, i) => (
                      <div key={`execution-pack-criterion-${i}`} style={{ display: 'grid', gridTemplateColumns: '12px 1fr', gap: 6, alignItems: 'start' }}>
                        <span style={{ ...MONO, color: executionPackTone, fontSize: 9, lineHeight: 1.45 }}>{i + 1}</span>
                        <span style={{ ...MONO, color: '#8ca0b3', fontSize: 8, lineHeight: 1.45 }}>{criterion}</span>
                      </div>
                    ))}
                    {executionPackCriteria.length === 0 && (
                      <span style={{ ...MONO, color: '#7f95a8', fontSize: 8 }}>criteria pending</span>
                    )}
                  </div>
                </div>
              </div>

              {executionPackTargets.length > 1 && (
                <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                  {executionPackTargets.slice(1, 5).map((target, i) => (
                    <span key={`execution-pack-target-${target.file}-${target.function}-${i}`} style={{ ...MONO, fontSize: 8, color: '#8ca0b3', border: '1px solid rgba(255,255,255,0.08)', borderRadius: 999, padding: '3px 8px', background: 'rgba(255,255,255,0.025)' }}>
                      {target.file || 'file'} · {target.function || 'function'}
                    </span>
                  ))}
                </div>
              )}
            </>
          ) : (
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 10 }}>
              <WorkbenchItem label="MISSION" value={`${topMission.title || liveMission.title || 'daily build focus'} · ${String(topMission.status || liveMission.status || 'observe').replace(/_/g, ' ').toLowerCase()}`} tone={executionPackTone} />
              <WorkbenchItem label="TARGET" value={`${topMission.target?.subsystem || liveMission.target?.subsystem || 'daily brief'} · ${topMission.target?.file || liveMission.target?.file || 'read-only mission'}`} />
              <WorkbenchItem label="PROOF" value={topMission.proof || liveMission.proof || topEvidenceRequirement || 'Wait for a scored pack or a live-context coverage gap.'} />
              <WorkbenchItem label="OUTCOME LOOP" value={`${liveOutcomeLoop.status || 'NO_ACTIVE_MISSION'} · gaps ${liveOutcomeLoop.coverage_gap_count ?? liveGaps.gap_count ?? 0} · surfaced ${liveOutcomeLoop.decision_surface_count ?? 0}`} />
            </div>
          )}
        </div>

        <div style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))',
          gap: 10,
        }}>
          <div style={{ border: '1px solid rgba(245,158,11,0.22)', borderRadius: 10, padding: 10, background: 'rgba(245,158,11,0.035)' }}>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
              <span style={{ ...MONO, fontSize: 8, color: '#f59e0b', fontWeight: 900, letterSpacing: '0.14em' }}>
                OUTCOME AUTOPSY
              </span>
              <span style={{ ...MONO, fontSize: 8, color: '#8ca0b3' }}>
                resolved {autopsySummary.resolved_count ?? 0} · missed {autopsySummary.missed_runner_count ?? 0} · weak {autopsySummary.weak_buy_count ?? 0}
              </span>
            </div>
            <div style={{ ...MONO, fontSize: 10, color: '#d7e1ea', marginTop: 7, lineHeight: 1.45 }}>
              {topRule?.label || autopsy.headline || 'Waiting for resolved outcomes'}
            </div>
            <div style={{ ...MONO, fontSize: 8, color: '#8ca0b3', marginTop: 5, lineHeight: 1.45 }}>
              {(topRule?.recommendation || buildHooks.next_patch || 'Keep collecting outcome evidence.').replace(/_/g, ' ').toLowerCase()}
            </div>
          </div>

          <div style={{ border: '1px solid rgba(96,165,250,0.18)', borderRadius: 10, padding: 10, background: 'rgba(96,165,250,0.035)' }}>
            <span style={{ ...MONO, fontSize: 8, color: '#60a5fa', fontWeight: 900, letterSpacing: '0.14em' }}>
              RULE SIM
            </span>
            <div style={{ ...MONO, fontSize: 10, color: '#d7e1ea', marginTop: 7, lineHeight: 1.45 }}>
              {simTop?.label || 'No rule candidate yet'}
            </div>
            <div style={{ ...MONO, fontSize: 8, color: '#8ca0b3', marginTop: 5, lineHeight: 1.45 }}>
              net {simTop?.net_score ?? 0} · weak blocked {simTop?.would_block_weak_buy_n ?? 0} · good blocked {simTop?.would_block_good_buy_n ?? 0}
            </div>
          </div>

          <div style={{ border: '1px solid rgba(0,212,138,0.18)', borderRadius: 10, padding: 10, background: 'rgba(0,212,138,0.03)' }}>
            <span style={{ ...MONO, fontSize: 8, color: '#00d48a', fontWeight: 900, letterSpacing: '0.14em' }}>
              BUILD HOOKS
            </span>
            <div style={{ ...MONO, fontSize: 10, color: '#d7e1ea', marginTop: 7, lineHeight: 1.45 }}>
              {buildHooks.status || watchdog.status || 'OBSERVE'}
            </div>
            <div style={{ ...MONO, fontSize: 8, color: '#8ca0b3', marginTop: 5, lineHeight: 1.45 }}>
              {providerLine || `data ${watchdog.summary?.live_recent_rows ?? 0}/${watchdog.summary?.stale_rows ?? 0} stale`} · rearm {buildHooks.safe_to_rearm_discussion ? 'separate discussion allowed' : 'locked'}
            </div>
          </div>

          <div style={{ border: '1px solid rgba(245,158,11,0.18)', borderRadius: 10, padding: 10, background: 'rgba(245,158,11,0.03)' }}>
            <span style={{ ...MONO, fontSize: 8, color: '#f59e0b', fontWeight: 900, letterSpacing: '0.14em' }}>
              PROVIDER WATCH
            </span>
            <div style={{ ...MONO, fontSize: 10, color: '#d7e1ea', marginTop: 7, lineHeight: 1.45 }}>
              {topRefreshTarget?.symbol || watchdog.status || 'Coverage normal'}
            </div>
            <div style={{ ...MONO, fontSize: 8, color: '#8ca0b3', marginTop: 5, lineHeight: 1.45 }}>
              {topRefreshTarget
                ? `${String(topRefreshTarget.market_source || 'source').toLowerCase()} · ${String(topRefreshTarget.data_freshness || 'unknown').toLowerCase()}/${String(topRefreshTarget.data_confidence || 'unknown').toLowerCase()} · q ${fmtFixed(topRefreshTarget.quality_score, 0)}`
                : watchdog.action || 'No provider repair target queued.'}
            </div>
          </div>

          <div style={{ border: '1px solid rgba(245,158,11,0.18)', borderRadius: 10, padding: 10, background: 'rgba(245,158,11,0.025)' }}>
            <span style={{ ...MONO, fontSize: 8, color: '#f59e0b', fontWeight: 900, letterSpacing: '0.14em' }}>
              PROVIDER RELIABILITY
            </span>
            <div style={{ ...MONO, fontSize: 10, color: '#d7e1ea', marginTop: 7, lineHeight: 1.45 }}>
              hit {fmtFixed(providerReliability.repair_hit_rate_pct, 0)}% · attempts {providerReliability.attempts_24h ?? 0}
            </div>
            <div style={{ ...MONO, fontSize: 8, color: '#8ca0b3', marginTop: 5, lineHeight: 1.45 }}>
              {topProviderFailure ? `${String(topProviderFailure.failure_class || 'unknown').replace(/_/g, ' ')} · ${topProviderFailure.count ?? 0}` : 'No repair failures in this window.'}
            </div>
          </div>

          <div style={{ border: `1px solid ${(escalationQueue.sla_breached_count ?? 0) ? 'rgba(239,68,68,0.24)' : 'rgba(245,158,11,0.18)'}`, borderRadius: 10, padding: 10, background: (escalationQueue.sla_breached_count ?? 0) ? 'rgba(239,68,68,0.035)' : 'rgba(245,158,11,0.025)' }}>
            <span style={{ ...MONO, fontSize: 8, color: (escalationQueue.sla_breached_count ?? 0) ? '#ef4444' : '#f59e0b', fontWeight: 900, letterSpacing: '0.14em' }}>
              ESCALATION QUEUE
            </span>
            <div style={{ ...MONO, fontSize: 10, color: '#d7e1ea', marginTop: 7, lineHeight: 1.45 }}>
              {escalationQueue.status || 'CLEAR'} · active {escalationQueue.active_count ?? 0} · SLA {escalationQueue.sla_breached_count ?? 0}
            </div>
            <div style={{ ...MONO, fontSize: 8, color: '#8ca0b3', marginTop: 5, lineHeight: 1.45 }}>
              {topEscalation
                ? `${topEscalation.symbol || 'UNKNOWN'} · ${String(topEscalation.lane || 'review').replace(/_/g, ' ').toLowerCase()} · ${String(topEscalation.failure_class || 'unknown').replace(/_/g, ' ')}`
                : escalationQueue.next_action || 'No active provider escalations.'}
            </div>
            {topEscalation && onEscalationAction && (
              <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 8 }}>
                {(['KEEP_WATCHING', 'FORCE_REFRESH', 'DISMISS'] as ProviderEscalationAction[]).map(action => (
                  <button
                    key={`provider-escalation-${action}`}
                    type="button"
                    className="mini-btn"
                    disabled={pendingEscalationId === topEscalation.id}
                    onClick={() => onEscalationAction(topEscalation, action)}
                    style={{ fontSize: 7, padding: '4px 7px' }}
                  >
                    {action === 'KEEP_WATCHING' ? 'WATCH' : action === 'FORCE_REFRESH' ? 'REFRESH' : 'DISMISS'}
                  </button>
                ))}
              </div>
            )}
          </div>

          <div style={{ border: `1px solid ${(escalationAccuracy.missed_n ?? 0) ? 'rgba(239,68,68,0.22)' : 'rgba(0,212,138,0.18)'}`, borderRadius: 10, padding: 10, background: (escalationAccuracy.missed_n ?? 0) ? 'rgba(239,68,68,0.03)' : 'rgba(0,212,138,0.025)' }}>
            <span style={{ ...MONO, fontSize: 8, color: (escalationAccuracy.missed_n ?? 0) ? '#ef4444' : '#00d48a', fontWeight: 900, letterSpacing: '0.14em' }}>
              ESCALATION ACCURACY
            </span>
            <div style={{ ...MONO, fontSize: 10, color: '#d7e1ea', marginTop: 7, lineHeight: 1.45 }}>
              {escalationAccuracy.status || 'LEARNING'} · {fmtFixed(escalationAccuracy.accuracy_pct, 0)}% · sample {escalationAccuracy.sample_n ?? 0}
            </div>
            <div style={{ ...MONO, fontSize: 8, color: '#8ca0b3', marginTop: 5, lineHeight: 1.45 }}>
              {topEscalationMiss
                ? `${topEscalationMiss.symbol || 'UNKNOWN'} · ${String(topEscalationMiss.failure_class || 'blocker').replace(/_/g, ' ')} · max ${fmtPct(topEscalationMiss.max_return_pct)} · 1h ${fmtPct(topEscalationMiss.return_1h_pct)}`
                : `correct ${escalationAccuracy.correct_n ?? 0} · missed ${escalationAccuracy.missed_n ?? 0} · pending ${escalationAccuracy.pending_n ?? 0}`}
            </div>
          </div>

          <div style={{ border: `1px solid ${(escalationAlerts.count ?? 0) ? 'rgba(239,68,68,0.24)' : 'rgba(96,165,250,0.16)'}`, borderRadius: 10, padding: 10, background: (escalationAlerts.count ?? 0) ? 'rgba(239,68,68,0.03)' : 'rgba(96,165,250,0.02)' }}>
            <span style={{ ...MONO, fontSize: 8, color: (escalationAlerts.count ?? 0) ? '#ef4444' : '#60a5fa', fontWeight: 900, letterSpacing: '0.14em' }}>
              ESCALATION EVIDENCE
            </span>
            <div style={{ ...MONO, fontSize: 10, color: '#d7e1ea', marginTop: 7, lineHeight: 1.45 }}>
              {escalationAlerts.status || 'CLEAR'} · alerts {escalationAlerts.count ?? 0}
            </div>
            <div style={{ ...MONO, fontSize: 8, color: '#8ca0b3', marginTop: 5, lineHeight: 1.45 }}>
              {topEscalationAlert
                ? `${topEscalationAlert.symbol || 'SYSTEM'} · ${String(topEscalationAlert.kind || 'alert').replace(/_/g, ' ').toLowerCase()} · ${fmtAge(topEscalationAlert.ts_utc || null)}`
                : `freeze ${(escalationAccuracy.frozen_failure_classes ?? []).slice(0, 2).join(', ') || 'none'}`}
            </div>
          </div>

          <div style={{ border: `1px solid ${(escalationReviewQueue.unresolved_count ?? 0) ? 'rgba(239,68,68,0.24)' : 'rgba(0,212,138,0.16)'}`, borderRadius: 10, padding: 10, background: (escalationReviewQueue.unresolved_count ?? 0) ? 'rgba(239,68,68,0.03)' : 'rgba(0,212,138,0.02)' }}>
            <span style={{ ...MONO, fontSize: 8, color: (escalationReviewQueue.unresolved_count ?? 0) ? '#ef4444' : '#00d48a', fontWeight: 900, letterSpacing: '0.14em' }}>
              REVIEW QUEUE
            </span>
            <div style={{ ...MONO, fontSize: 10, color: '#d7e1ea', marginTop: 7, lineHeight: 1.45 }}>
              {escalationReviewQueue.status || 'NO_ALERTS'} · open {escalationReviewQueue.unresolved_count ?? 0} · groups {escalationReviewQueue.group_count ?? 0}
            </div>
            <div style={{ ...MONO, fontSize: 8, color: '#8ca0b3', marginTop: 5, lineHeight: 1.45 }}>
              {topEscalationReview
                ? `${String(topEscalationReview.failure_class || 'unknown').replace(/_/g, ' ')} · ${String(topEscalationReview.lane || 'lane').replace(/_/g, ' ').toLowerCase()} · max ${fmtPct(topEscalationReview.max_return_pct)}`
                : escalationReviewQueue.next_action || 'No unresolved escalation alert groups.'}
            </div>
            {topEscalationReview && onEscalationReviewAction && (
              <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 8 }}>
                {([
                  ['ACKNOWLEDGED', 'ACK'],
                  ['RULE_PATCH_NEEDED', 'RULE'],
                  ['DATA_PATCH_NEEDED', 'DATA'],
                  ['FALSE_ALARM', 'FALSE'],
                  ['RESOLVED', 'DONE'],
                ] as Array<[ProviderEscalationReviewState, string]>).map(([state, label]) => (
                  <button
                    key={`escalation-review-${state}`}
                    type="button"
                    className="mini-btn"
                    disabled={pendingEscalationReviewKey === topEscalationReview.group_key}
                    onClick={() => onEscalationReviewAction(topEscalationReview, state)}
                    style={{ fontSize: 7, padding: '4px 7px' }}
                  >
                    {label}
                  </button>
                ))}
              </div>
            )}
          </div>

          <div style={{ border: `1px solid ${(escalationPatchPlans.implementation_ready_count ?? 0) ? 'rgba(0,212,138,0.26)' : (escalationPatchPlans.ready_count ?? 0) ? 'rgba(245,158,11,0.24)' : 'rgba(96,165,250,0.16)'}`, borderRadius: 10, padding: 10, background: (escalationPatchPlans.implementation_ready_count ?? 0) ? 'rgba(0,212,138,0.035)' : (escalationPatchPlans.ready_count ?? 0) ? 'rgba(245,158,11,0.03)' : 'rgba(96,165,250,0.02)' }}>
            <span style={{ ...MONO, fontSize: 8, color: (escalationPatchPlans.implementation_ready_count ?? 0) ? '#00d48a' : (escalationPatchPlans.ready_count ?? 0) ? '#f59e0b' : '#60a5fa', fontWeight: 900, letterSpacing: '0.14em' }}>
              PATCH PLAN
            </span>
            <div style={{ ...MONO, fontSize: 10, color: '#d7e1ea', marginTop: 7, lineHeight: 1.45 }}>
              {escalationPatchPlans.status || 'NO_PLANS'} · ready {escalationPatchPlans.ready_count ?? 0} · plans {escalationPatchPlans.plan_count ?? 0}
            </div>
            <div style={{ ...MONO, fontSize: 8, color: '#8ca0b3', marginTop: 5, lineHeight: 1.45 }}>
              {topEscalationPatch
                ? `${String(topEscalationPatch.plan_type || 'patch').replace(/_/g, ' ').toLowerCase()} · conf ${fmtFixed(topEscalationPatch.confidence_score, 0)} · benefit ${topEscalationPatch.simulated_benefit_n ?? 0}/risk ${topEscalationPatch.weak_buy_risk_n ?? 0}`
                : escalationPatchPlans.next_action || 'No escalation patch plan yet.'}
            </div>
            {topEscalationPatch?.proposed_fix && (
              <div style={{ ...MONO, fontSize: 8, color: '#8ca0b3', marginTop: 5, lineHeight: 1.45 }}>
                {topEscalationPatch.proposed_fix}
              </div>
            )}
            {topEscalationPatch && onEscalationPatchAction && (
              <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 8 }}>
                {([
                  ['READY_FOR_IMPLEMENTATION', 'READY'],
                  ['NEEDS_MORE_DATA', 'HOLD'],
                ] as Array<[ProviderEscalationPatchState, string]>).map(([state, label]) => (
                  <button
                    key={`escalation-patch-${state}`}
                    type="button"
                    className="mini-btn"
                    disabled={pendingEscalationPatchKey === topEscalationPatch.group_key || (state === 'READY_FOR_IMPLEMENTATION' && topEscalationPatch.gate_status !== 'READY_FOR_MANUAL_REVIEW')}
                    onClick={() => onEscalationPatchAction(topEscalationPatch, state)}
                    style={{ fontSize: 7, padding: '4px 7px' }}
                  >
                    {label}
                  </button>
                ))}
              </div>
            )}
          </div>

          <div style={{ border: `1px solid ${(escalationWorkOrders.started_count ?? 0) ? 'rgba(0,212,138,0.26)' : (escalationWorkOrders.ready_count ?? 0) ? 'rgba(245,158,11,0.24)' : 'rgba(96,165,250,0.16)'}`, borderRadius: 10, padding: 10, background: (escalationWorkOrders.started_count ?? 0) ? 'rgba(0,212,138,0.035)' : (escalationWorkOrders.ready_count ?? 0) ? 'rgba(245,158,11,0.03)' : 'rgba(96,165,250,0.02)' }}>
            <span style={{ ...MONO, fontSize: 8, color: (escalationWorkOrders.started_count ?? 0) ? '#00d48a' : (escalationWorkOrders.ready_count ?? 0) ? '#f59e0b' : '#60a5fa', fontWeight: 900, letterSpacing: '0.14em' }}>
              WORK ORDER
            </span>
            <div style={{ ...MONO, fontSize: 10, color: '#d7e1ea', marginTop: 7, lineHeight: 1.45 }}>
              {escalationWorkOrders.status || 'NO_WORK_ORDERS'} · ready {escalationWorkOrders.ready_count ?? 0} · started {escalationWorkOrders.started_count ?? 0}
            </div>
            <div style={{ ...MONO, fontSize: 8, color: '#8ca0b3', marginTop: 5, lineHeight: 1.45 }}>
              {topEscalationWorkOrder
                ? `${String(topEscalationWorkOrder.risk_label || 'risk').replace(/_/g, ' ').toLowerCase()} · ${topEscalationWorkOrder.target_subsystem || 'subsystem'} · checks ${topEscalationWorkOrder.checklist?.length ?? 0}`
                : escalationWorkOrders.next_action || 'No implementation work order is ready.'}
            </div>
            {topEscalationWorkOrder?.goal && (
              <div style={{ ...MONO, fontSize: 8, color: '#8ca0b3', marginTop: 5, lineHeight: 1.45 }}>
                {topEscalationWorkOrder.goal}
              </div>
            )}
            {topEscalationWorkOrder && onEscalationWorkOrderAction && (
              <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 8 }}>
                {([
                  ['STARTED', 'START'],
                  ['BLOCKED', 'BLOCK'],
                  ['COMPLETE', 'DONE'],
                ] as Array<[ProviderEscalationWorkOrderState, string]>).map(([state, label]) => (
                  <button
                    key={`escalation-work-order-${state}`}
                    type="button"
                    className="mini-btn"
                    disabled={pendingEscalationWorkOrderKey === topEscalationWorkOrder.group_key}
                    onClick={() => onEscalationWorkOrderAction(topEscalationWorkOrder, state)}
                    style={{ fontSize: 7, padding: '4px 7px' }}
                  >
                    {label}
                  </button>
                ))}
              </div>
            )}
          </div>

          <div style={{ border: `1px solid ${(escalationExecutionPacks.active_count ?? 0) ? 'rgba(0,212,138,0.28)' : (escalationExecutionPacks.pack_count ?? 0) ? 'rgba(245,158,11,0.22)' : 'rgba(96,165,250,0.16)'}`, borderRadius: 10, padding: 10, background: (escalationExecutionPacks.active_count ?? 0) ? 'rgba(0,212,138,0.035)' : (escalationExecutionPacks.pack_count ?? 0) ? 'rgba(245,158,11,0.025)' : 'rgba(96,165,250,0.02)' }}>
            <span style={{ ...MONO, fontSize: 8, color: (escalationExecutionPacks.active_count ?? 0) ? '#00d48a' : (escalationExecutionPacks.pack_count ?? 0) ? '#f59e0b' : '#60a5fa', fontWeight: 900, letterSpacing: '0.14em' }}>
              EXECUTION PACK
            </span>
            <div style={{ ...MONO, fontSize: 10, color: '#d7e1ea', marginTop: 7, lineHeight: 1.45 }}>
              {escalationExecutionPacks.status || 'NO_PACKS'} · active {escalationExecutionPacks.active_count ?? 0} · packs {escalationExecutionPacks.pack_count ?? 0}
            </div>
            <div style={{ ...MONO, fontSize: 8, color: '#8ca0b3', marginTop: 5, lineHeight: 1.45 }}>
              {topEscalationExecutionPack
                ? `score ${fmtFixed(topEscalationExecutionPack.priority_score, 0)} · ${String(topEscalationExecutionPack.risk_label || 'risk').replace(/_/g, ' ').toLowerCase()} · benefit ${topEscalationExecutionPack.evidence_bundle?.simulated_benefit_n ?? 0}/risk ${topEscalationExecutionPack.evidence_bundle?.weak_buy_risk_n ?? 0}`
                : escalationExecutionPacks.next_action || 'No active execution pack.'}
            </div>
            {topEscalationExecutionPack?.replay_test_recipe?.[0]?.label && (
              <div style={{ ...MONO, fontSize: 8, color: '#8ca0b3', marginTop: 5, lineHeight: 1.45 }}>
                {topEscalationExecutionPack.replay_test_recipe[0].label}
              </div>
            )}
          </div>

          <div style={{ border: `1px solid ${(postPatchOutcomes.regression_count ?? 0) ? 'rgba(239,68,68,0.24)' : (postPatchOutcomes.tracked_count ?? 0) ? 'rgba(0,212,138,0.2)' : 'rgba(96,165,250,0.16)'}`, borderRadius: 10, padding: 10, background: (postPatchOutcomes.regression_count ?? 0) ? 'rgba(239,68,68,0.03)' : (postPatchOutcomes.tracked_count ?? 0) ? 'rgba(0,212,138,0.025)' : 'rgba(96,165,250,0.02)' }}>
            <span style={{ ...MONO, fontSize: 8, color: (postPatchOutcomes.regression_count ?? 0) ? '#ef4444' : (postPatchOutcomes.tracked_count ?? 0) ? '#00d48a' : '#60a5fa', fontWeight: 900, letterSpacing: '0.14em' }}>
              PATCH OUTCOMES
            </span>
            <div style={{ ...MONO, fontSize: 10, color: '#d7e1ea', marginTop: 7, lineHeight: 1.45 }}>
              {postPatchOutcomes.status || 'NO_COMPLETED_PATCHES'} · tracked {postPatchOutcomes.tracked_count ?? 0} · regress {postPatchOutcomes.regression_count ?? 0}
            </div>
            <div style={{ ...MONO, fontSize: 8, color: '#8ca0b3', marginTop: 5, lineHeight: 1.45 }}>
              {topPostPatch
                ? `${topPostPatch.group_key || 'patch'} · sample ${topPostPatch.sample_n ?? 0} · correct ${topPostPatch.correct_n ?? 0}/miss ${topPostPatch.missed_n ?? 0}`
                : postPatchOutcomes.next_action || 'Complete a work order to begin patch tracking.'}
            </div>
          </div>

          <div style={{ border: `1px solid ${(regressionGuard.freeze_count ?? 0) ? 'rgba(239,68,68,0.26)' : 'rgba(0,212,138,0.16)'}`, borderRadius: 10, padding: 10, background: (regressionGuard.freeze_count ?? 0) ? 'rgba(239,68,68,0.035)' : 'rgba(0,212,138,0.02)' }}>
            <span style={{ ...MONO, fontSize: 8, color: (regressionGuard.freeze_count ?? 0) ? '#ef4444' : '#00d48a', fontWeight: 900, letterSpacing: '0.14em' }}>
              REGRESSION GUARD
            </span>
            <div style={{ ...MONO, fontSize: 10, color: '#d7e1ea', marginTop: 7, lineHeight: 1.45 }}>
              {regressionGuard.status || 'CLEAR'} · freezes {regressionGuard.freeze_count ?? 0}
            </div>
            <div style={{ ...MONO, fontSize: 8, color: '#8ca0b3', marginTop: 5, lineHeight: 1.45 }}>
              {topRegression
                ? `${String(topRegression.source || 'guard').replace(/_/g, ' ')} · ${topRegression.reason || 'freeze active'}`
                : regressionGuard.next_action || 'No regression freeze is active.'}
            </div>
          </div>

          <div style={{ border: `1px solid ${(escalationMaturity.overdue_count ?? 0) ? 'rgba(239,68,68,0.24)' : (escalationMaturity.due_now_count ?? 0) ? 'rgba(245,158,11,0.24)' : 'rgba(96,165,250,0.18)'}`, borderRadius: 10, padding: 10, background: (escalationMaturity.overdue_count ?? 0) ? 'rgba(239,68,68,0.03)' : (escalationMaturity.due_now_count ?? 0) ? 'rgba(245,158,11,0.03)' : 'rgba(96,165,250,0.025)' }}>
            <span style={{ ...MONO, fontSize: 8, color: (escalationMaturity.overdue_count ?? 0) ? '#ef4444' : (escalationMaturity.due_now_count ?? 0) ? '#f59e0b' : '#60a5fa', fontWeight: 900, letterSpacing: '0.14em' }}>
              OUTCOME CLOCK
            </span>
            <div style={{ ...MONO, fontSize: 10, color: '#d7e1ea', marginTop: 7, lineHeight: 1.45 }}>
              {escalationMaturity.status || 'CLEAR'} · pending {escalationMaturity.pending_count ?? 0} · due {escalationMaturity.due_now_count ?? 0}
            </div>
            <div style={{ ...MONO, fontSize: 8, color: '#8ca0b3', marginTop: 5, lineHeight: 1.45 }}>
              {escalationAutorun.ran
                ? `auto ${String(escalationAutorun.status || 'checked').toLowerCase()} · checked ${escalationAutorun.result?.due_checked ?? escalationAutorun.result?.checked ?? 0} · alerts ${escalationAutorun.result?.alerts_emitted ?? 0}`
                : nextEscalationDue
                ? `${nextEscalationDue.symbol || 'UNKNOWN'} · ${nextEscalationDue.horizon || 'next'} · ${fmtAge(nextEscalationDue.next_check_ts || null)}`
                : escalationMaturity.next_action || 'No pending escalation outcome checks.'}
            </div>
          </div>

          <div style={{ border: '1px solid rgba(239,68,68,0.18)', borderRadius: 10, padding: 10, background: 'rgba(239,68,68,0.025)' }}>
            <span style={{ ...MONO, fontSize: 8, color: '#ef4444', fontWeight: 900, letterSpacing: '0.14em' }}>
              STALE WHY
            </span>
            <div style={{ ...MONO, fontSize: 10, color: '#d7e1ea', marginTop: 7, lineHeight: 1.45 }}>
              {topProviderDrilldown?.symbol || providerDrilldown.status || 'NO_FAILURES'}
            </div>
            <div style={{ ...MONO, fontSize: 8, color: '#8ca0b3', marginTop: 5, lineHeight: 1.45 }}>
              {topProviderDrilldown
                ? `${String(topProviderDrilldown.failure_class || 'unknown').replace(/_/g, ' ')} · ${String(topProviderDrilldown.provider_path || 'provider').replace(/->/g, ' > ')}`
                : 'No stale provider drilldown yet.'}
            </div>
          </div>

          <div style={{ border: '1px solid rgba(45,212,191,0.18)', borderRadius: 10, padding: 10, background: 'rgba(45,212,191,0.03)' }}>
            <span style={{ ...MONO, fontSize: 8, color: '#2dd4bf', fontWeight: 900, letterSpacing: '0.14em' }}>
              FRESHNESS SLA
            </span>
            <div style={{ ...MONO, fontSize: 10, color: '#d7e1ea', marginTop: 7, lineHeight: 1.45 }}>
              {freshnessSla.status || 'UNKNOWN'} · {fmtFixed(freshnessSla.score, 0)}
            </div>
            <div style={{ ...MONO, fontSize: 8, color: '#8ca0b3', marginTop: 5, lineHeight: 1.45 }}>
              live/recent {fmtFixed(freshnessSla.live_recent_pct, 0)}% · repaired {freshnessSla.live_repaired_24h ?? 0}/{freshnessSla.repair_attempts_24h ?? 0}
            </div>
          </div>

          <div style={{ border: '1px solid rgba(167,139,250,0.18)', borderRadius: 10, padding: 10, background: 'rgba(167,139,250,0.03)' }}>
            <span style={{ ...MONO, fontSize: 8, color: '#a78bfa', fontWeight: 900, letterSpacing: '0.14em' }}>
              RULE GATE
            </span>
            <div style={{ ...MONO, fontSize: 10, color: '#d7e1ea', marginTop: 7, lineHeight: 1.45 }}>
              {ruleGate.status || 'NO_RULE'}
            </div>
            <div style={{ ...MONO, fontSize: 8, color: '#8ca0b3', marginTop: 5, lineHeight: 1.45 }}>
              {ruleGate.candidate?.label || ruleGate.reason || 'Waiting for simulator evidence.'}
            </div>
          </div>

          <div style={{ border: '1px solid rgba(239,68,68,0.18)', borderRadius: 10, padding: 10, background: 'rgba(239,68,68,0.025)' }}>
            <span style={{ ...MONO, fontSize: 8, color: '#ef4444', fontWeight: 900, letterSpacing: '0.14em' }}>
              MISS CLUSTERS
            </span>
            <div style={{ ...MONO, fontSize: 10, color: '#d7e1ea', marginTop: 7, lineHeight: 1.45 }}>
              {missedClusters.top_cluster?.label || missedClusters.status || 'NO_MISSES'}
            </div>
            <div style={{ ...MONO, fontSize: 8, color: '#8ca0b3', marginTop: 5, lineHeight: 1.45 }}>
              {missedClusters.top_cluster
                ? `${missedClusters.top_cluster.count ?? 0} misses · avg ${fmtPct(missedClusters.top_cluster.avg_max_return_pct)}`
                : 'No bullish missed-runner cluster in this window.'}
            </div>
          </div>

          <div style={{ border: '1px solid rgba(96,165,250,0.18)', borderRadius: 10, padding: 10, background: 'rgba(96,165,250,0.025)' }}>
            <span style={{ ...MONO, fontSize: 8, color: '#60a5fa', fontWeight: 900, letterSpacing: '0.14em' }}>
              CATALYSTS
            </span>
            <div style={{ ...MONO, fontSize: 10, color: '#d7e1ea', marginTop: 7, lineHeight: 1.45 }}>
              {catalystContext.status || 'NO_FEED'} · confluence {catalystContext.sources?.recent_confluence_count ?? 0}
            </div>
            <div style={{ ...MONO, fontSize: 8, color: '#8ca0b3', marginTop: 5, lineHeight: 1.45 }}>
              {(catalystContext.decision_overlap_symbols ?? []).slice(0, 5).join(', ') || 'No narrative overlap with current decisions.'}
            </div>
          </div>

          <div style={{ border: `1px solid ${(liveIntake.status === 'ACTIVE') ? 'rgba(45,212,191,0.2)' : 'rgba(96,165,250,0.16)'}`, borderRadius: 10, padding: 10, background: (liveIntake.status === 'ACTIVE') ? 'rgba(45,212,191,0.025)' : 'rgba(96,165,250,0.02)' }}>
            <span style={{ ...MONO, fontSize: 8, color: liveIntake.status === 'ACTIVE' ? '#2dd4bf' : '#60a5fa', fontWeight: 900, letterSpacing: '0.14em' }}>
              LIVE PULSE
            </span>
            <div style={{ ...MONO, fontSize: 10, color: '#d7e1ea', marginTop: 7, lineHeight: 1.45 }}>
              {liveIntake.status || 'NO_FEED'} · cg {liveIntake.source_counts?.coingecko ?? 0} · dex {liveIntake.source_counts?.dexscreener ?? 0} · conf {liveIntake.source_counts?.confluence ?? 0}
            </div>
            <div style={{ ...MONO, fontSize: 8, color: '#8ca0b3', marginTop: 5, lineHeight: 1.45 }}>
              {topLiveItem
                ? `${topLiveItem.symbol || topLiveItem.mint || 'market'} · ${String(topLiveItem.source || 'source').replace(/_/g, ' ')} · heat ${fmtFixed(topLiveItem.heat_score, 0)}`
                : liveIntake.next_action || 'Refresh live market narrative intake.'}
            </div>
          </div>

          <div style={{ border: `1px solid ${(liveGaps.gap_count ?? 0) ? 'rgba(245,158,11,0.24)' : 'rgba(0,212,138,0.16)'}`, borderRadius: 10, padding: 10, background: (liveGaps.gap_count ?? 0) ? 'rgba(245,158,11,0.03)' : 'rgba(0,212,138,0.02)' }}>
            <span style={{ ...MONO, fontSize: 8, color: (liveGaps.gap_count ?? 0) ? '#f59e0b' : '#00d48a', fontWeight: 900, letterSpacing: '0.14em' }}>
              OPPORTUNITY GAP
            </span>
            <div style={{ ...MONO, fontSize: 10, color: '#d7e1ea', marginTop: 7, lineHeight: 1.45 }}>
              {liveGaps.status || 'NO_FEED'} · gaps {liveGaps.gap_count ?? 0}
            </div>
            <div style={{ ...MONO, fontSize: 8, color: '#8ca0b3', marginTop: 5, lineHeight: 1.45 }}>
              {topLiveGap
                ? `${topLiveGap.symbol || topLiveGap.mint || 'market'} · ${String(topLiveGap.gap_type || 'gap').replace(/_/g, ' ').toLowerCase()} · score ${fmtFixed(topLiveGap.gap_score, 0)}`
                : liveGaps.next_action || 'No market coverage gap detected.'}
            </div>
          </div>

          <div style={{ border: `1px solid ${liveMission.status === 'READY' ? 'rgba(245,158,11,0.24)' : 'rgba(96,165,250,0.16)'}`, borderRadius: 10, padding: 10, background: liveMission.status === 'READY' ? 'rgba(245,158,11,0.03)' : 'rgba(96,165,250,0.02)' }}>
            <span style={{ ...MONO, fontSize: 8, color: liveMission.status === 'READY' ? '#f59e0b' : '#60a5fa', fontWeight: 900, letterSpacing: '0.14em' }}>
              MARKET MISSION
            </span>
            <div style={{ ...MONO, fontSize: 10, color: '#d7e1ea', marginTop: 7, lineHeight: 1.45 }}>
              {String(liveMission.mission_type || 'LIVE_CONTEXT_OBSERVE').replace(/_/g, ' ')} · {fmtFixed(liveMission.priority_score, 0)} · {String(liveDossier.state || 'NEW').replace(/_/g, ' ')}
            </div>
            <div style={{ ...MONO, fontSize: 8, color: '#8ca0b3', marginTop: 5, lineHeight: 1.45 }}>
              {liveMission.headline || liveMission.next_action || 'No generated live-context mission yet.'}
            </div>
            {liveMissionActionItem && onLiveContextMissionAction && (
              <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 8 }}>
                {([
                  ['INVESTIGATING', 'INVESTIGATE'],
                  ['RESOLVED_COVERED', 'COVER'],
                  ['RESOLVED_IGNORED', 'IGNORE'],
                  ['NEEDS_SOURCE', 'SOURCE'],
                ] as Array<[LiveContextMissionState, string]>).map(([state, label]) => (
                  <button
                    key={`live-context-mission-${state}`}
                    type="button"
                    className="mini-btn"
                    disabled={pendingLiveContextMissionKey === (liveDossier.mission_key || liveMission.group_key || topLiveGap?.gap_key)}
                    onClick={() => onLiveContextMissionAction(liveMissionActionItem, state)}
                    style={{ fontSize: 7, padding: '4px 7px' }}
                  >
                    {label}
                  </button>
                ))}
              </div>
            )}
          </div>

          <div style={{ border: `1px solid ${liveResolution.status === 'RECOMMEND_IGNORE' ? 'rgba(239,68,68,0.22)' : liveResolution.status === 'RECOMMEND_COVERAGE' ? 'rgba(0,212,138,0.2)' : 'rgba(96,165,250,0.16)'}`, borderRadius: 10, padding: 10, background: liveResolution.status === 'RECOMMEND_IGNORE' ? 'rgba(239,68,68,0.025)' : liveResolution.status === 'RECOMMEND_COVERAGE' ? 'rgba(0,212,138,0.025)' : 'rgba(96,165,250,0.02)' }}>
            <span style={{ ...MONO, fontSize: 8, color: liveResolution.status === 'RECOMMEND_IGNORE' ? '#ef4444' : liveResolution.status === 'RECOMMEND_COVERAGE' ? '#00d48a' : '#60a5fa', fontWeight: 900, letterSpacing: '0.14em' }}>
              SCOPE DOSSIER
            </span>
            <div style={{ ...MONO, fontSize: 10, color: '#d7e1ea', marginTop: 7, lineHeight: 1.45 }}>
              {liveDossier.symbol || liveDossier.mint || 'market'} · {String(liveDossier.scope?.scope || liveResolution.scope || 'NEEDS_SOURCE').replace(/_/g, ' ')}
            </div>
            <div style={{ ...MONO, fontSize: 8, color: '#8ca0b3', marginTop: 5, lineHeight: 1.45 }}>
              {liveDossier.scope?.reason || liveResolution.next_action || liveDossier.next_action || 'Classify this mission before resolving it.'}
            </div>
          </div>

          <div style={{ border: '1px solid rgba(45,212,191,0.18)', borderRadius: 10, padding: 10, background: 'rgba(45,212,191,0.025)' }}>
            <span style={{ ...MONO, fontSize: 8, color: '#2dd4bf', fontWeight: 900, letterSpacing: '0.14em' }}>
              MISSION JOURNAL
            </span>
            <div style={{ ...MONO, fontSize: 10, color: '#d7e1ea', marginTop: 7, lineHeight: 1.45 }}>
              {liveMissionJournal.status || 'EMPTY'} · missions {liveMissionJournal.mission_count ?? 0} · events {liveMissionJournal.event_count ?? 0}
            </div>
            <div style={{ ...MONO, fontSize: 8, color: '#8ca0b3', marginTop: 5, lineHeight: 1.45 }}>
              correct {liveMissionJournal.correctness_counts?.CORRECT ?? 0} · review {liveMissionJournal.correctness_counts?.REVIEW ?? 0} · miss {liveMissionJournal.correctness_counts?.MISS ?? 0}
            </div>
          </div>

          <div style={{ border: `1px solid ${(liveReviewQueue.open_count ?? 0) ? 'rgba(245,158,11,0.24)' : 'rgba(0,212,138,0.16)'}`, borderRadius: 10, padding: 10, background: (liveReviewQueue.open_count ?? 0) ? 'rgba(245,158,11,0.03)' : 'rgba(0,212,138,0.02)' }}>
            <span style={{ ...MONO, fontSize: 8, color: (liveReviewQueue.open_count ?? 0) ? '#f59e0b' : '#00d48a', fontWeight: 900, letterSpacing: '0.14em' }}>
              MISSION REVIEW
            </span>
            <div style={{ ...MONO, fontSize: 10, color: '#d7e1ea', marginTop: 7, lineHeight: 1.45 }}>
              {liveReviewQueue.status || 'CLEAR'} · open {liveReviewQueue.open_count ?? 0}
            </div>
            <div style={{ ...MONO, fontSize: 8, color: '#8ca0b3', marginTop: 5, lineHeight: 1.45 }}>
              {topLiveReview
                ? `${topLiveReview.symbol || topLiveReview.mint || topLiveReview.mission_key || 'mission'} · ${String(topLiveReview.outcome_label || topLiveReview.state || 'review').replace(/_/g, ' ').toLowerCase()} · score ${fmtFixed(topLiveReview.priority_score, 0)}`
                : liveReviewQueue.next_action || 'No mission outcomes need review.'}
            </div>
            {topLiveReview && onLiveContextMissionAction && (
              <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 8 }}>
                {([
                  ['INVESTIGATING', 'INVESTIGATE'],
                  ['RESOLVED_COVERED', 'COVER'],
                  ['RESOLVED_IGNORED', 'IGNORE'],
                  ['NEEDS_SOURCE', 'SOURCE'],
                ] as Array<[LiveContextMissionState, string]>).map(([state, label]) => (
                  <button
                    key={`live-context-review-${state}`}
                    type="button"
                    className="mini-btn"
                    disabled={pendingLiveContextMissionKey === topLiveReview.mission_key}
                    onClick={() => onLiveContextMissionAction(topLiveReview, state)}
                    style={{ fontSize: 7, padding: '4px 7px' }}
                  >
                    {label}
                  </button>
                ))}
              </div>
            )}
          </div>

          <div style={{ border: `1px solid ${liveDecisionAccuracy.status === 'NEEDS_REVIEW' ? 'rgba(239,68,68,0.22)' : 'rgba(96,165,250,0.16)'}`, borderRadius: 10, padding: 10, background: liveDecisionAccuracy.status === 'NEEDS_REVIEW' ? 'rgba(239,68,68,0.03)' : 'rgba(96,165,250,0.02)' }}>
            <span style={{ ...MONO, fontSize: 8, color: liveDecisionAccuracy.status === 'NEEDS_REVIEW' ? '#ef4444' : '#60a5fa', fontWeight: 900, letterSpacing: '0.14em' }}>
              MISSION ACCURACY
            </span>
            <div style={{ ...MONO, fontSize: 10, color: '#d7e1ea', marginTop: 7, lineHeight: 1.45 }}>
              {liveDecisionAccuracy.status || 'LEARNING'} · {liveDecisionAccuracy.accuracy_pct == null ? 'n/a' : `${fmtFixed(liveDecisionAccuracy.accuracy_pct, 0)}%`} · sample {liveDecisionAccuracy.sample_n ?? 0}
            </div>
            <div style={{ ...MONO, fontSize: 8, color: '#8ca0b3', marginTop: 5, lineHeight: 1.45 }}>
              correct {liveDecisionAccuracy.correct_n ?? 0} · miss {liveDecisionAccuracy.miss_n ?? 0} · pending {liveDecisionAccuracy.pending_n ?? 0}
            </div>
          </div>

          <div style={{ border: `1px solid ${(livePolicySuggestions.suggestion_count ?? 0) ? 'rgba(167,139,250,0.24)' : 'rgba(96,165,250,0.16)'}`, borderRadius: 10, padding: 10, background: (livePolicySuggestions.suggestion_count ?? 0) ? 'rgba(167,139,250,0.03)' : 'rgba(96,165,250,0.02)' }}>
            <span style={{ ...MONO, fontSize: 8, color: (livePolicySuggestions.suggestion_count ?? 0) ? '#a78bfa' : '#60a5fa', fontWeight: 900, letterSpacing: '0.14em' }}>
              POLICY SUGGESTIONS
            </span>
            <div style={{ ...MONO, fontSize: 10, color: '#d7e1ea', marginTop: 7, lineHeight: 1.45 }}>
              {livePolicySuggestions.status || 'NO_POLICY_CHANGE'} · {livePolicySuggestions.suggestion_count ?? 0}
            </div>
            <div style={{ ...MONO, fontSize: 8, color: '#8ca0b3', marginTop: 5, lineHeight: 1.45 }}>
              {topLivePolicy
                ? `${String(topLivePolicy.policy_key || 'policy').replace(/_/g, ' ').toLowerCase()} · ${String(topLivePolicy.confidence || 'evidence').toLowerCase()} · n ${topLivePolicy.evidence_n ?? 0}`
                : livePolicySuggestions.next_action || 'No policy suggestion until outcomes prove a pattern.'}
            </div>
          </div>

          <div style={{ border: '1px solid rgba(167,139,250,0.18)', borderRadius: 10, padding: 10, background: 'rgba(167,139,250,0.025)' }}>
            <span style={{ ...MONO, fontSize: 8, color: '#a78bfa', fontWeight: 900, letterSpacing: '0.14em' }}>
              MARKET PROOF
            </span>
            <div style={{ ...MONO, fontSize: 10, color: '#d7e1ea', marginTop: 7, lineHeight: 1.45 }}>
              {liveEvidence.status || 'OBSERVE'} · {liveEvidence.pass_count ?? 0}/{liveEvidence.required_count ?? liveEvidence.requirements?.length ?? 0}
            </div>
            <div style={{ ...MONO, fontSize: 8, color: '#8ca0b3', marginTop: 5, lineHeight: 1.45 }}>
              {topEvidenceRequirement || liveEvidence.next_action || 'Evidence requirements appear when a market mission is generated.'}
            </div>
          </div>

          <div style={{ border: `1px solid ${liveOutcomeLoop.status === 'IMPROVING' ? 'rgba(0,212,138,0.2)' : 'rgba(96,165,250,0.16)'}`, borderRadius: 10, padding: 10, background: liveOutcomeLoop.status === 'IMPROVING' ? 'rgba(0,212,138,0.025)' : 'rgba(96,165,250,0.02)' }}>
            <span style={{ ...MONO, fontSize: 8, color: liveOutcomeLoop.status === 'IMPROVING' ? '#00d48a' : '#60a5fa', fontWeight: 900, letterSpacing: '0.14em' }}>
              MARKET OUTCOME
            </span>
            <div style={{ ...MONO, fontSize: 10, color: '#d7e1ea', marginTop: 7, lineHeight: 1.45 }}>
              {liveOutcomeLoop.status || 'NO_ACTIVE_MISSION'} · surfaced {liveOutcomeLoop.decision_surface_count ?? 0}
            </div>
            <div style={{ ...MONO, fontSize: 8, color: '#8ca0b3', marginTop: 5, lineHeight: 1.45 }}>
              {liveOutcomeLoop.next_action || 'Outcome loop starts after a live market mission exists.'}
            </div>
          </div>

          <div style={{ border: '1px solid rgba(0,212,138,0.18)', borderRadius: 10, padding: 10, background: 'rgba(0,212,138,0.025)' }}>
            <span style={{ ...MONO, fontSize: 8, color: '#00d48a', fontWeight: 900, letterSpacing: '0.14em' }}>
              BUILD SCORE
            </span>
            <div style={{ ...MONO, fontSize: 10, color: '#d7e1ea', marginTop: 7, lineHeight: 1.45 }}>
              {fmtFixed(buildScore.score, 0)} · {(buildScore.focus || 'OBSERVE').replace(/_/g, ' ')}
            </div>
            <div style={{ ...MONO, fontSize: 8, color: '#8ca0b3', marginTop: 5, lineHeight: 1.45 }}>
              {buildScore.next_action || 'Keep collecting outcomes.'}
            </div>
          </div>

          <div style={{ border: '1px solid rgba(245,158,11,0.18)', borderRadius: 10, padding: 10, background: 'rgba(245,158,11,0.025)' }}>
            <span style={{ ...MONO, fontSize: 8, color: '#f59e0b', fontWeight: 900, letterSpacing: '0.14em' }}>
              PILOT GATE
            </span>
            <div style={{ ...MONO, fontSize: 10, color: '#d7e1ea', marginTop: 7, lineHeight: 1.45 }}>
              {pilotGate.status || 'NOT_READY'} · sample {pilotGate.sample_n ?? 0}
            </div>
            <div style={{ ...MONO, fontSize: 8, color: '#8ca0b3', marginTop: 5, lineHeight: 1.45 }}>
              win {fmtFixed(pilotGate.win_rate_pct, 0)}% · 4h {fmtPct(pilotGate.avg_4h_pct)} · {(pilotGate.blockers ?? []).slice(0, 2).join(', ') || 'discussion only'}
            </div>
          </div>
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: 14 }}>
          <div>
            <span style={{ ...MONO, fontSize: 8, color: '#f59e0b', fontWeight: 900, letterSpacing: '0.14em' }}>
              AUTOPSY EXAMPLES
            </span>
            {autopsyExamples.slice(0, 3).map((item, i) => (
              <AutopsyLine key={`autopsy-${item.symbol}-${i}`} item={item} />
            ))}
            {autopsyExamples.length === 0 && (
              <div style={{ ...MONO, fontSize: 8, color: '#7f95a8', paddingTop: 8 }}>waiting for resolved outcomes</div>
            )}
          </div>
          <div>
            <span style={{ ...MONO, fontSize: 8, color: '#60a5fa', fontWeight: 900, letterSpacing: '0.14em' }}>
              REPLAY TIMELINE
            </span>
            <div style={{ ...MONO, fontSize: 8, color: '#8ca0b3', paddingTop: 7, lineHeight: 1.5 }}>
              {(replay.anchor?.symbol || 'candidate')} · {(replay.anchor?.classification || replay.status || 'tracking').replace(/_/g, ' ').toLowerCase()} · {replayEvents.length} events
            </div>
            {replayEvents.slice(-3).map((event, i) => (
              <div key={`replay-${event.ts}-${i}`} style={{ ...MONO, fontSize: 8, color: '#8ca0b3', paddingTop: 6, borderTop: i ? '1px solid rgba(255,255,255,0.045)' : 'none' }}>
                {(event.decision_state || event.priority || 'state').replace(/_/g, ' ').toLowerCase()} · {event.primary_blocker_label || event.outcome_label || fmtPct(event.max_return_pct)}
              </div>
            ))}
          </div>
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: 14 }}>
        <div>
          <span style={{ ...MONO, fontSize: 8, color: '#f59e0b', fontWeight: 900, letterSpacing: '0.14em' }}>
            MISSED RUNNERS
          </span>
          {missedRunners.slice(0, 4).map((item, i) => (
            <TokenLine key={`missed-${item.symbol}-${i}`} item={item} tone="#f59e0b" />
          ))}
          {missedRunners.length === 0 && (
            <div style={{ ...MONO, fontSize: 8, color: '#7f95a8', paddingTop: 8 }}>none in this window</div>
          )}
        </div>
        <div>
          <span style={{ ...MONO, fontSize: 8, color: '#60a5fa', fontWeight: 900, letterSpacing: '0.14em' }}>
            PAPER MOVERS
          </span>
          {paperMovers.slice(0, 4).map((item, i) => (
            <TokenLine key={`paper-${item.symbol}-${i}`} item={item} tone="#60a5fa" />
          ))}
        </div>
        <div>
          <span style={{ ...MONO, fontSize: 8, color: '#ef4444', fontWeight: 900, letterSpacing: '0.14em' }}>
            WEAK BUY CALLS
          </span>
          {weakBuyCalls.slice(0, 4).map((item, i) => (
            <TokenLine key={`weak-${item.symbol}-${i}`} item={item} tone="#ef4444" />
          ))}
          {weakBuyCalls.length === 0 && (
            <div style={{ ...MONO, fontSize: 8, color: '#7f95a8', paddingTop: 8 }}>none in this window</div>
          )}
        </div>
      </div>

      {nextActions.length > 0 && (
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
          {nextActions.slice(0, 4).map((action, i) => (
            <span key={`daily-action-${i}`} style={{
              ...MONO,
              fontSize: 8,
              color: '#d7e1ea',
              background: 'rgba(96,165,250,0.08)',
              border: '1px solid rgba(96,165,250,0.18)',
              borderRadius: 999,
              padding: '3px 8px',
            }}>
              {action}
            </span>
          ))}
        </div>
      )}
    </div>
  )
}

function EntryWatchHomeStrip({
  status,
  replay,
  coverage,
}: {
  status: WatchToEntryStatus | undefined
  replay: WatchToEntryReplay | undefined
  coverage: EstablishedRunnerCoverage | undefined
}) {
  const tracked = status?.tracked ?? []
  const trackingCount = replay?.tracking_count ?? status?.tracking_count ?? tracked.length
  const trueLastCount = replay?.true_last_blocker_count ?? status?.true_last_blocker_count ?? tracked.filter(item => item.true_last_blocker).length
  const establishedCount = replay?.established_runner_count ?? tracked.filter(item => item.is_established_runner).length
  const surfaced = replay?.surface_summary?.surfaced ?? 0
  const qualityState = replay?.surface_summary?.quality_state || 'NO_TRIGGER_SAMPLE'
  const tone =
    trueLastCount > 0 ? '#00d48a'
    : trackingCount > 0 ? '#f59e0b'
    : '#60a5fa'
  const stateLabel =
    trueLastCount > 0 ? 'LAST BLOCKER ARMED'
    : trackingCount > 0 ? 'WATCHING BLOCKERS'
    : 'NO ENTRY WATCH'
  const horizon4h = replay?.surface_summary?.horizons?.['4h']
  const topBlockers = replay?.top_blockers ?? []

  const Mini = ({ label, value, valueTone }: { label: string; value: string; valueTone?: string }) => (
    <div style={{
      background: 'rgba(255,255,255,0.025)',
      border: '1px solid rgba(255,255,255,0.07)',
      borderRadius: 9,
      padding: '7px 9px',
      minWidth: 92,
    }}>
      <div style={{ ...MONO, fontSize: 7, color: '#6f879d', letterSpacing: '0.12em', marginBottom: 4 }}>{label}</div>
      <div style={{ ...MONO, fontSize: 12, color: valueTone || '#d7e1ea', fontWeight: 900 }}>{value}</div>
    </div>
  )

  const WatchRow = ({ item }: { item: WatchToEntryItem }) => {
    const rowTone = item.true_last_blocker ? '#00d48a' : item.is_established_runner ? '#f59e0b' : '#60a5fa'
    const blocker = item.primary_blocker || item.remaining_blocker_keys?.[0] || 'waiting_for_clear'
    return (
      <div style={{
        background: `${rowTone}08`,
        border: `1px solid ${rowTone}22`,
        borderLeft: `3px solid ${rowTone}`,
        borderRadius: '0 10px 10px 0',
        padding: '9px 10px',
        display: 'flex',
        flexDirection: 'column',
        gap: 7,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 7, flexWrap: 'wrap' }}>
          <span style={{ ...MONO, fontSize: 12, color: '#f3f7fb', fontWeight: 900, letterSpacing: '0.06em' }}>
            {item.symbol || 'UNKNOWN'}
          </span>
          {item.true_last_blocker && (
            <span className="badge" style={{ color: '#00d48a', background: 'rgba(0,212,138,0.10)', border: '1px solid rgba(0,212,138,0.26)', fontSize: 7 }}>
              TRUE LAST
            </span>
          )}
          {item.is_established_runner && (
            <span className="badge" style={{ color: '#f59e0b', background: 'rgba(245,158,11,0.10)', border: '1px solid rgba(245,158,11,0.24)', fontSize: 7 }}>
              ESTABLISHED
            </span>
          )}
          <TokenAddressChip value={item.mint} />
          <span style={{ ...MONO, fontSize: 8, color: '#8ca0b3', marginLeft: 'auto' }}>
            {fmtAge(item.generated_at || item.ts_utc || null)}
          </span>
        </div>
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
          <span style={{ ...MONO, fontSize: 8, color: '#fbbf24' }}>blocker {blocker}</span>
          <span style={{ ...MONO, fontSize: 8, color: '#8ca0b3' }}>left {fmtFixed(item.remaining_blocker_count, 0)}</span>
          <span style={{ ...MONO, fontSize: 8, color: '#8ca0b3' }}>score {fmtFixed(item.score ?? item.proof_score, 0)}</span>
          <span style={{ ...MONO, fontSize: 8, color: '#8ca0b3' }}>MQ {fmtFixed(item.market_quality_score, 0)}</span>
          <span style={{ ...MONO, fontSize: 8, color: '#8ca0b3' }}>BP {fmtFixed(item.buy_pressure, 0)}</span>
          <span style={{ ...MONO, fontSize: 8, color: '#8ca0b3' }}>VOL {fmtFixed(item.vol_acceleration, 2)}</span>
        </div>
      </div>
    )
  }

  return (
    <div style={{
      border: `1px solid ${tone}24`,
      borderTop: `2px solid ${tone}`,
      borderRadius: 16,
      padding: 14,
      background:
        `radial-gradient(circle at 10% 0%, ${tone}13 0%, transparent 28%),` +
        'linear-gradient(180deg, rgba(5,10,18,0.76), rgba(3,7,13,0.70))',
      display: 'flex',
      flexDirection: 'column',
      gap: 12,
    }}>
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12, flexWrap: 'wrap' }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 5, minWidth: 260, flex: 1 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
            <span style={{ ...MONO, fontSize: 9, color: tone, fontWeight: 900, letterSpacing: '0.16em' }}>
              ENTRY WATCH
            </span>
            <span className="badge" style={{ color: tone, background: `${tone}12`, border: `1px solid ${tone}28`, fontSize: 8 }}>
              {stateLabel}
            </span>
            <span style={{ ...MONO, fontSize: 8, color: '#7f95a8' }}>
              {replay?.headline || 'Tracks strong names until the final blocker clears.'}
            </span>
          </div>
          <span style={{ ...MONO, fontSize: 9, color: '#9fb3c8', lineHeight: 1.55 }}>
            {replay?.next_step || coverage?.next_step || 'Use this strip to see what is close to becoming buyable without digging through raw scanner pages.'}
          </span>
        </div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
          <Mini label="TRACKING" value={`${trackingCount}`} valueTone={trackingCount ? '#f59e0b' : '#506276'} />
          <Mini label="TRUE LAST" value={`${trueLastCount}`} valueTone={trueLastCount ? '#00d48a' : '#506276'} />
          <Mini label="RUNNERS" value={`${establishedCount}`} valueTone={establishedCount ? '#f59e0b' : '#506276'} />
          <Mini label="REPLAY" value={`${surfaced}`} valueTone={qualityState === 'POSITIVE' ? '#00d48a' : qualityState === 'NEGATIVE' ? '#ef4444' : '#7f95a8'} />
          <Mini label="4H AVG" value={horizon4h?.avg_return_pct != null ? `${fmtFixed(horizon4h.avg_return_pct, 1)}%` : '—'} valueTone={(horizon4h?.avg_return_pct ?? 0) > 0 ? '#00d48a' : '#7f95a8'} />
        </div>
      </div>

      {tracked.length > 0 ? (
        <div className="entry-watch-grid" style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: 10 }}>
          {tracked.slice(0, 3).map((item, i) => (
            <WatchRow key={`${item.mint || item.symbol || 'watch'}-${i}`} item={item} />
          ))}
        </div>
      ) : (
        <div style={{ ...MONO, fontSize: 9, color: '#7f95a8', lineHeight: 1.55 }}>
          No active watch-to-entry names right now. Coverage is still watching {coverage?.covered_count ?? 0}/{coverage?.tracked_count ?? 0} established runner profiles.
        </div>
      )}

      {(topBlockers.length > 0 || (coverage?.missing_count ?? 0) > 0) && (
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
          {topBlockers.slice(0, 4).map(blocker => (
            <span key={blocker.key} style={{
              ...MONO,
              fontSize: 8,
              color: '#fbbf24',
              background: 'rgba(245,158,11,0.08)',
              border: '1px solid rgba(245,158,11,0.18)',
              borderRadius: 5,
              padding: '3px 7px',
            }}>
              {blocker.key} {blocker.count ?? 0}
            </span>
          ))}
          {(coverage?.missing_count ?? 0) > 0 && (
            <span style={{ ...MONO, fontSize: 8, color: '#ff7777', background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.18)', borderRadius: 5, padding: '3px 7px' }}>
              coverage gaps {coverage?.missing_count}
            </span>
          )}
        </div>
      )}
    </div>
  )
}

function EstablishedRunnerReviewPanel({
  data,
  loading,
  onDecision,
  pendingMint,
}: {
  data: RunnerReviewData | undefined
  loading: boolean
  onDecision: (item: RunnerReviewOpportunity, decision: RunnerReviewDecision) => void
  pendingMint?: string | null
}) {
  const rows = data?.opportunities ?? []
  const tone =
    (data?.summary.ready ?? 0) > 0 ? '#00d48a'
    : (data?.summary.needs_momentum ?? 0) > 0 ? '#f59e0b'
    : (data?.summary.extension_risk ?? 0) > 0 ? '#ef4444'
    : '#60a5fa'
  const outcome = data?.outcome_summary?.summary
  const recentOutcomes = data?.outcome_summary?.recent ?? []
  const followUp = data?.follow_up ?? data?.outcome_summary?.follow_up
  const tracker = data?.trade_tracker ?? data?.outcome_summary?.trade_tracker
  const activeTracker = tracker?.active?.slice(0, 4) ?? []
  const dueFollowups = (followUp?.followups ?? []).filter(item => item.follow_up_due).slice(0, 4)
  const stateTone = (state: string) => {
    const s = String(state || '').toUpperCase()
    if (s === 'RUNNER_READY') return '#00d48a'
    if (s === 'RUNNER_NEEDS_MOMENTUM' || s === 'RUNNER_LIFECYCLE_UNCONFIRMED') return '#f59e0b'
    if (s === 'RUNNER_EXTENSION_RISK' || s === 'RUNNER_QUALITY_LOW') return '#ef4444'
    return '#60a5fa'
  }
  const actionStyle = (decision: RunnerReviewDecision): React.CSSProperties => {
    const color =
      decision === 'MANUAL_BUY' ? '#00d48a'
      : decision === 'WATCH' ? '#60a5fa'
      : decision === 'TOO_LATE' ? '#f59e0b'
      : '#ef4444'
    return {
      ...MONO,
      border: `1px solid ${color}30`,
      background: `${color}10`,
      color,
      borderRadius: 7,
      padding: '6px 8px',
      fontSize: 8,
      fontWeight: 900,
      letterSpacing: '0.08em',
      cursor: 'pointer',
    }
  }
  const Metric = ({ label, value, valueTone }: { label: string; value: string; valueTone?: string }) => (
    <div style={{
      background: 'rgba(255,255,255,0.025)',
      border: '1px solid rgba(255,255,255,0.07)',
      borderRadius: 9,
      padding: '7px 9px',
      minWidth: 92,
    }}>
      <div style={{ ...MONO, fontSize: 7, color: '#6f879d', letterSpacing: '0.12em', marginBottom: 4 }}>{label}</div>
      <div style={{ ...MONO, fontSize: 12, color: valueTone || '#d7e1ea', fontWeight: 900 }}>{value}</div>
    </div>
  )

  return (
    <div style={{
      border: `1px solid ${tone}24`,
      borderTop: `2px solid ${tone}`,
      borderRadius: 16,
      padding: 14,
      background:
        `radial-gradient(circle at 12% 0%, ${tone}13 0%, transparent 30%),` +
        'linear-gradient(180deg, rgba(5,10,18,0.77), rgba(3,7,13,0.71))',
      display: 'flex',
      flexDirection: 'column',
      gap: 12,
    }}>
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12, flexWrap: 'wrap' }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 5, minWidth: 260, flex: 1 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
            <span style={{ ...MONO, fontSize: 9, color: tone, fontWeight: 900, letterSpacing: '0.16em' }}>
              ESTABLISHED RUNNER REVIEW
            </span>
            <span className="badge" style={{ color: tone, background: `${tone}12`, border: `1px solid ${tone}28`, fontSize: 8 }}>
              {data?.summary.ready ? 'REVIEW NOW' : data?.summary.needs_momentum ? 'WATCH TRIGGERS' : data?.summary.extension_risk ? 'AVOID CHASE' : 'QUIET'}
            </span>
            <span style={{ ...MONO, fontSize: 8, color: '#7f95a8' }}>
              {data?.proof_input_source || 'proof layer'}
            </span>
          </div>
          <span style={{ ...MONO, fontSize: 9, color: '#9fb3c8', lineHeight: 1.55 }}>
            {data?.headline || 'Converts runner-policy states into operator decisions the system can learn from.'}
          </span>
        </div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
          <Metric label="READY" value={`${data?.summary.ready ?? 0}`} valueTone={(data?.summary.ready ?? 0) ? '#00d48a' : '#506276'} />
          <Metric label="MOMENTUM" value={`${data?.summary.needs_momentum ?? 0}`} valueTone={(data?.summary.needs_momentum ?? 0) ? '#f59e0b' : '#506276'} />
          <Metric label="EXTENDED" value={`${data?.summary.extension_risk ?? 0}`} valueTone={(data?.summary.extension_risk ?? 0) ? '#ef4444' : '#506276'} />
          <Metric label="DECIDE" value={`${data?.summary.decision_needed ?? 0}`} valueTone={(data?.summary.decision_needed ?? 0) ? '#f59e0b' : '#506276'} />
          <Metric label="ACTIVE" value={`${outcome?.active ?? 0}`} valueTone={(outcome?.active ?? 0) ? '#60a5fa' : '#506276'} />
          <Metric label="GOOD/BAD" value={`${outcome?.good ?? 0}/${outcome?.bad ?? 0}`} valueTone={(outcome?.bad ?? 0) > (outcome?.good ?? 0) ? '#ef4444' : (outcome?.good ?? 0) ? '#00d48a' : '#506276'} />
          <Metric label="DUE" value={`${followUp?.summary.due ?? 0}`} valueTone={(followUp?.summary.due ?? 0) ? '#f59e0b' : '#506276'} />
        </div>
      </div>

      {data?.decision_bridge?.headline && (
        <div style={{
          background: 'rgba(96,165,250,0.055)',
          border: '1px solid rgba(96,165,250,0.16)',
          borderRadius: 11,
          padding: '9px 10px',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: 10,
          flexWrap: 'wrap',
        }}>
          <span style={{ ...MONO, fontSize: 9, color: '#bcd3e8', lineHeight: 1.5 }}>
            {data.decision_bridge.headline}
          </span>
          <span style={{ ...MONO, fontSize: 8, color: '#60a5fa', lineHeight: 1.5 }}>
            {data.decision_bridge.primary_prompt}
          </span>
        </div>
      )}

      {loading && !rows.length ? (
        <div style={{ ...MONO, fontSize: 9, color: '#7f95a8' }}>Loading runner review board…</div>
      ) : rows.length ? (
        <div className="runner-review-grid" style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(285px, 1fr))', gap: 10 }}>
          {rows.slice(0, 6).map(item => {
            const rowTone = stateTone(item.runner_state)
            const pending = pendingMint === item.mint
            const suggested = item.suggested_decision || null
            const suggestedTone =
              suggested === 'MANUAL_BUY' ? '#00d48a'
              : suggested === 'WATCH' ? '#60a5fa'
              : suggested === 'TOO_LATE' ? '#f59e0b'
              : suggested === 'PASS' ? '#ef4444'
              : rowTone
            return (
              <div key={`${item.mint}-${item.runner_state}`} style={{
                background: `${rowTone}08`,
                border: `1px solid ${rowTone}22`,
                borderLeft: `3px solid ${rowTone}`,
                borderRadius: '0 12px 12px 0',
                padding: 11,
                display: 'flex',
                flexDirection: 'column',
                gap: 9,
              }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 7, flexWrap: 'wrap' }}>
                  <span style={{ ...MONO, fontSize: 13, color: '#f3f7fb', fontWeight: 900 }}>{item.symbol}</span>
                  <span className="badge" style={{ color: rowTone, background: `${rowTone}12`, border: `1px solid ${rowTone}28`, fontSize: 7 }}>
                    {item.runner_state.replace(/^RUNNER_/, '').replace(/_/g, ' ')}
                  </span>
                  {item.last_decision && (
                    <span className="badge" style={{ color: '#7f95a8', background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.08)', fontSize: 7 }}>
                      last {item.last_decision.decision.replace(/_/g, ' ')}
                    </span>
                  )}
                  <TokenAddressChip value={item.mint} />
                </div>

                <div style={{ ...MONO, fontSize: 9, color: '#aebed0', lineHeight: 1.55 }}>
                  {item.operator_hint || item.thesis || 'Review the current runner state before acting.'}
                </div>

                {suggested && (
                  <div style={{
                    background: `${suggestedTone}0b`,
                    border: `1px solid ${suggestedTone}24`,
                    borderRadius: 10,
                    padding: 9,
                    display: 'flex',
                    flexDirection: 'column',
                    gap: 7,
                  }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 7, flexWrap: 'wrap' }}>
                      <span className="badge" style={{ color: suggestedTone, background: `${suggestedTone}12`, border: `1px solid ${suggestedTone}2c`, fontSize: 7 }}>
                        {item.suggestion_label || `Recommended: ${suggested.replace(/_/g, ' ')}`}
                      </span>
                      <span style={{ ...MONO, fontSize: 8, color: '#8ca0b3' }}>
                        conf {fmtFixed(item.suggestion_confidence, 0)} · {item.suggestion_urgency || 'REVIEW'}
                      </span>
                    </div>
                    <span style={{ ...MONO, fontSize: 8, color: '#c5d5e3', lineHeight: 1.5 }}>
                      {item.suggestion_reason}
                    </span>
                    {item.next_trigger && (
                      <span style={{ ...MONO, fontSize: 8, color: '#8fb7dc', lineHeight: 1.45 }}>
                        trigger: {item.next_trigger}
                      </span>
                    )}
                    {item.learning_prompt && (
                      <span style={{ ...MONO, fontSize: 8, color: '#7f95a8', lineHeight: 1.45 }}>
                        {item.learning_prompt}
                      </span>
                    )}
                  </div>
                )}

                <div style={{ display: 'flex', gap: 9, flexWrap: 'wrap' }}>
                  <span style={{ ...MONO, fontSize: 8, color: '#8ca0b3' }}>MQ {fmtFixed(item.market_quality_score, 0)}</span>
                  <span style={{ ...MONO, fontSize: 8, color: '#8ca0b3' }}>BP {fmtFixed(item.buy_pressure, 0)}</span>
                  <span style={{ ...MONO, fontSize: 8, color: '#8ca0b3' }}>VOL {fmtFixed(item.vol_acceleration, 2)}</span>
                  <span style={{ ...MONO, fontSize: 8, color: '#8ca0b3' }}>{item.entry_window || '—'} · {item.fuel_quality || '—'} · {item.move_phase || '—'}</span>
                  <span style={{ ...MONO, fontSize: 8, color: item.first_leg_confirmed ? '#00d48a' : '#f59e0b' }}>
                    leg {item.first_leg_confirmed ? 'confirmed' : 'unconfirmed'}
                  </span>
                </div>

                {(item.blocker_key || item.blocker_reasons?.length) && (
                  <div style={{ display: 'flex', gap: 5, flexWrap: 'wrap' }}>
                    {item.blocker_key && (
                      <span style={{ ...MONO, fontSize: 8, color: rowTone, background: `${rowTone}10`, border: `1px solid ${rowTone}22`, borderRadius: 5, padding: '2px 6px' }}>
                        {item.blocker_key}
                      </span>
                    )}
                    {(item.blocker_reasons ?? []).slice(0, 2).map((reason, i) => (
                      <span key={i} style={{ ...MONO, fontSize: 8, color: '#7f95a8', background: 'rgba(255,255,255,0.025)', border: '1px solid rgba(255,255,255,0.07)', borderRadius: 5, padding: '2px 6px' }}>
                        {reason}
                      </span>
                    ))}
                  </div>
                )}

                <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 2 }}>
                  {suggested && (
                    <button
                      type="button"
                      disabled={pending}
                      onClick={() => onDecision(item, suggested)}
                      style={{
                        ...actionStyle(suggested),
                        background: `${suggestedTone}1a`,
                        border: `1px solid ${suggestedTone}50`,
                        color: suggestedTone,
                        opacity: pending ? 0.55 : 1,
                        cursor: pending ? 'wait' : 'pointer',
                      }}
                    >
                      LOG SUGGESTED
                    </button>
                  )}
                  {(['WATCH', 'PASS', 'MANUAL_BUY', 'TOO_LATE'] as RunnerReviewDecision[]).map(decision => (
                    <button
                      key={decision}
                      type="button"
                      disabled={pending}
                      onClick={() => onDecision(item, decision)}
                      style={{
                        ...actionStyle(decision),
                        opacity: pending ? 0.55 : 1,
                        cursor: pending ? 'wait' : 'pointer',
                      }}
                    >
                      {decision.replace(/_/g, ' ')}
                    </button>
                  ))}
                </div>
              </div>
            )
          })}
        </div>
      ) : (
        <div style={{ ...MONO, fontSize: 9, color: '#7f95a8', lineHeight: 1.55 }}>
          No runner-review candidates right now. The policy is still watching established names in the background.
        </div>
      )}

      {tracker && (
        <div style={{
          background: 'rgba(0,212,138,0.045)',
          border: '1px solid rgba(0,212,138,0.15)',
          borderRadius: 12,
          padding: 10,
          display: 'flex',
          flexDirection: 'column',
          gap: 9,
        }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10, flexWrap: 'wrap' }}>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              <span style={{ ...MONO, fontSize: 8, color: '#00d48a', fontWeight: 900, letterSpacing: '0.14em' }}>
                PAPER RUNNER TRACKER
              </span>
              <span style={{ ...MONO, fontSize: 8, color: '#9fb3c8', lineHeight: 1.45 }}>
                {tracker.headline || 'Logged runner decisions become paper outcomes with entry mcap, max return, drawdown, and labels.'}
              </span>
            </div>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              <span style={{ ...MONO, fontSize: 8, color: '#00d48a' }}>tracked {tracker.summary.trackable}/{tracker.summary.total}</span>
              <span style={{ ...MONO, fontSize: 8, color: '#60a5fa' }}>active {tracker.summary.active}</span>
              <span style={{ ...MONO, fontSize: 8, color: '#8fb7dc' }}>auto {tracker.summary.system_paper ?? 0}</span>
              <span style={{ ...MONO, fontSize: 8, color: '#a78bfa' }}>units {fmtFixed(tracker.summary.paper_units, 2)}</span>
              <span style={{ ...MONO, fontSize: 8, color: (tracker.summary.simulated_pnl_units ?? 0) >= 0 ? '#00d48a' : '#ef4444' }}>sim {fmtFixed(tracker.summary.simulated_pnl_units, 3)}u</span>
              <span style={{ ...MONO, fontSize: 8, color: (tracker.summary.exit_review ?? 0) ? '#ef4444' : '#7f95a8' }}>exit {tracker.summary.exit_review ?? 0}</span>
              <span style={{ ...MONO, fontSize: 8, color: (tracker.summary.exit_now ?? 0) ? '#ef4444' : '#7f95a8' }}>exit now {tracker.summary.exit_now ?? 0}</span>
              <span style={{ ...MONO, fontSize: 8, color: (tracker.summary.scale_out ?? 0) ? '#f59e0b' : '#7f95a8' }}>scale {tracker.summary.scale_out ?? 0}</span>
              <span style={{ ...MONO, fontSize: 8, color: (tracker.summary.protect_profit ?? 0) ? '#00d48a' : '#7f95a8' }}>protect {tracker.summary.protect_profit ?? 0}</span>
              <span style={{ ...MONO, fontSize: 8, color: tracker.summary.missing_market ? '#f59e0b' : '#7f95a8' }}>market gaps {tracker.summary.missing_market}</span>
              <span style={{ ...MONO, fontSize: 8, color: tracker.summary.bad > tracker.summary.good ? '#ef4444' : '#00d48a' }}>good/bad {tracker.summary.good}/{tracker.summary.bad}</span>
              <span style={{ ...MONO, fontSize: 8, color: '#9fb3c8' }}>avg max {fmtFixed(tracker.summary.avg_max_return_pct, 1)}%</span>
            </div>
          </div>

          {activeTracker.length > 0 ? (
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(230px, 1fr))', gap: 8 }}>
              {activeTracker.map(item => {
                const label = String(item.tracker_label || item.outcome_label || 'TRACKING')
                const itemTone =
                  label === 'PROTECT_PROFIT' || label === 'WORKING' ? '#00d48a'
                  : label === 'DEFEND' || label === 'FAILING' || label === 'EXIT_REVIEW' || label === 'INVALIDATE' ? '#ef4444'
                  : label === 'MISSED_RUNNER_CHECK' || label === 'GIVEBACK_WARNING' || label === 'MOMENTUM_FADE' ? '#f59e0b'
                  : '#60a5fa'
                return (
                  <div key={`tracker-${item.id}-${item.mint}`} style={{
                    background: `${itemTone}08`,
                    border: `1px solid ${itemTone}20`,
                    borderRadius: 9,
                    padding: 9,
                    display: 'flex',
                    flexDirection: 'column',
                    gap: 6,
                  }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 7, flexWrap: 'wrap' }}>
                      <span style={{ ...MONO, fontSize: 11, color: '#f3f7fb', fontWeight: 900 }}>{item.symbol || 'RUNNER'}</span>
                      <span className="badge" style={{ color: itemTone, background: `${itemTone}12`, border: `1px solid ${itemTone}28`, fontSize: 7 }}>
                        {label.replace(/_/g, ' ')}
                      </span>
                      <TokenAddressChip value={item.mint} />
                    </div>
                    <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                      <span style={{ ...MONO, fontSize: 8, color: '#8ca0b3' }}>entry {fmtUsd(item.entry_marketcap)}</span>
                      <span style={{ ...MONO, fontSize: 8, color: '#8ca0b3' }}>now {fmtUsd(item.current_marketcap)}</span>
                      <span style={{ ...MONO, fontSize: 8, color: itemTone }}>cur {fmtFixed(item.current_return_pct, 1)}%</span>
                      <span style={{ ...MONO, fontSize: 8, color: itemTone }}>max {fmtFixed(item.max_return_pct, 1)}%</span>
                      <span style={{ ...MONO, fontSize: 8, color: '#ef9999' }}>min {fmtFixed(item.min_return_pct, 1)}%</span>
                      <span style={{ ...MONO, fontSize: 8, color: '#f59e0b' }}>dd {fmtFixed(item.drawdown_from_max_pct, 1)}%</span>
                    </div>
                    {item.exit_signal_reason && (
                      <span style={{ ...MONO, fontSize: 8, color: '#b6c7d8', lineHeight: 1.45 }}>
                        {item.exit_urgency || 'LOW'} · {item.exit_signal_reason}
                      </span>
                    )}
                    <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                      <span style={{ ...MONO, fontSize: 7, color: '#a78bfa', background: 'rgba(167,139,250,0.08)', border: '1px solid rgba(167,139,250,0.18)', borderRadius: 5, padding: '2px 6px' }}>
                        {String(item.paper_position_label || 'NO_SIZE').replace(/_/g, ' ')} {fmtFixed(item.paper_position_units, 2)}u
                      </span>
                      <span style={{ ...MONO, fontSize: 7, color: item.entry_quality_label === 'CHASE_RISK' || item.entry_quality_label === 'LATE' ? '#f59e0b' : '#00d48a', background: 'rgba(255,255,255,0.035)', border: '1px solid rgba(255,255,255,0.08)', borderRadius: 5, padding: '2px 6px' }}>
                        entry {String(item.entry_quality_label || '—').replace(/_/g, ' ')} {fmtFixed(item.entry_quality_score, 0)}
                      </span>
                      <span style={{ ...MONO, fontSize: 7, color: item.signal_decay_label === 'STALE' || item.signal_decay_label === 'EXPIRED' ? '#f59e0b' : '#60a5fa', background: 'rgba(255,255,255,0.035)', border: '1px solid rgba(255,255,255,0.08)', borderRadius: 5, padding: '2px 6px' }}>
                        signal {String(item.signal_decay_label || '—').replace(/_/g, ' ')}
                      </span>
                      <span style={{ ...MONO, fontSize: 7, color: item.management_action === 'EXIT_NOW' ? '#ef4444' : item.management_action === 'SCALE_OUT' || item.management_action === 'PROTECT' ? '#f59e0b' : '#00d48a', background: 'rgba(255,255,255,0.035)', border: '1px solid rgba(255,255,255,0.08)', borderRadius: 5, padding: '2px 6px' }}>
                        {String(item.management_action || 'HOLD').replace(/_/g, ' ')}
                      </span>
                    </div>
                    {item.management_reason && (
                      <span style={{ ...MONO, fontSize: 8, color: '#9fb3c8', lineHeight: 1.45 }}>
                        manage: {item.management_reason}
                      </span>
                    )}
                    <span style={{ ...MONO, fontSize: 8, color: '#7f95a8' }}>
                      {String(item.decision || '').replace(/_/g, ' ')} · {item.decision_source || 'OPERATOR'} · {item.entry_market_source || 'market'} · {item.entry_capture_status || 'capture pending'}
                    </span>
                  </div>
                )
              })}
            </div>
          ) : (
            <span style={{ ...MONO, fontSize: 8, color: '#7f95a8', lineHeight: 1.45 }}>
              No active paper runner decisions yet. The first logged recommendation will start this tracker.
            </span>
          )}
        </div>
      )}

      {dueFollowups.length > 0 && (
        <div style={{
          background: 'rgba(245,158,11,0.055)',
          border: '1px solid rgba(245,158,11,0.18)',
          borderRadius: 11,
          padding: 10,
          display: 'flex',
          flexDirection: 'column',
          gap: 8,
        }}>
          <div style={{ ...MONO, fontSize: 8, color: '#f59e0b', fontWeight: 900, letterSpacing: '0.14em' }}>
            FOLLOW-UP DUE
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(230px, 1fr))', gap: 8 }}>
            {dueFollowups.map(item => (
              <div key={`${item.id}-${item.mint}`} style={{
                background: 'rgba(0,0,0,0.18)',
                border: '1px solid rgba(255,255,255,0.07)',
                borderRadius: 9,
                padding: 9,
                display: 'flex',
                flexDirection: 'column',
                gap: 6,
              }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 7, flexWrap: 'wrap' }}>
                  <span style={{ ...MONO, fontSize: 11, color: '#f3f7fb', fontWeight: 900 }}>{item.symbol || 'RUNNER'}</span>
                  <span className="badge" style={{ color: '#f59e0b', background: 'rgba(245,158,11,0.10)', border: '1px solid rgba(245,158,11,0.24)', fontSize: 7 }}>
                    {String(item.decision || '').replace(/_/g, ' ')}
                  </span>
                  <TokenAddressChip value={item.mint} />
                </div>
                <span style={{ ...MONO, fontSize: 8, color: '#b6c7d8', lineHeight: 1.5 }}>
                  {item.recommended_follow_up || item.follow_up_reason || 'Review this runner decision.'}
                </span>
                <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                  <span style={{ ...MONO, fontSize: 8, color: '#8ca0b3' }}>state {item.current_runner?.runner_state || item.runner_state || '—'}</span>
                  <span style={{ ...MONO, fontSize: 8, color: '#8ca0b3' }}>max {fmtFixed(item.max_return_pct, 1)}%</span>
                  {item.material_change && <span style={{ ...MONO, fontSize: 8, color: '#00d48a' }}>material change</span>}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {recentOutcomes.some(item => item.outcome_label) && (
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
          {recentOutcomes.filter(item => item.outcome_label).slice(0, 5).map(item => {
            const label = String(item.outcome_label || '')
            const labelTone = label.startsWith('BAD') ? '#ef4444' : label.startsWith('GOOD') || label === 'WATCH_CONFIRMED' ? '#00d48a' : '#7f95a8'
            return (
              <span key={`${item.id}-${label}`} style={{
                ...MONO,
                fontSize: 8,
                color: labelTone,
                background: `${labelTone}0f`,
                border: `1px solid ${labelTone}24`,
                borderRadius: 5,
                padding: '3px 7px',
              }}>
                {item.symbol || 'RUNNER'} · {label.replace(/_/g, ' ')} · max {fmtFixed(item.max_return_pct, 1)}%
              </span>
            )
          })}
        </div>
      )}
    </div>
  )
}

function MemecoinResearchDossierPanel({
  data,
  loading,
  onManualDecision,
  pendingManualMint,
}: {
  data: MemecoinResearchData | undefined
  loading: boolean
  onManualDecision?: (item: MemecoinResearchDossier, decision: MemecoinManualReviewDecision) => void
  pendingManualMint?: string | null
}) {
  const rows = data?.dossiers ?? []
  const rotations = data?.rotation_board ?? []
  const establishedWatch = data?.established_runner_watchlist?.items ?? []
  const opportunityLedger = data?.opportunity_ledger
  const readySoon = opportunityLedger?.ready_soon ?? []
  const blockedImproving = opportunityLedger?.blocked_improving ?? []
  const missedOrLearn = opportunityLedger?.missed_or_learn ?? []
  const ledgerLessons = opportunityLedger?.lessons
  const trustCalibration = opportunityLedger?.trust_calibration
  const shadowLab = opportunityLedger?.shadow_strategy_lab
  const escalationItems = opportunityLedger?.escalation_queue?.items ?? []
  const catalysts = data?.catalysts?.recent ?? []
  const operator = data?.operator_action
  const topAction = operator?.top
  const bandCounts = data?.summary.conviction_bands ?? {}
  const calibration = data?.calibration
  const entrySignals = data?.entry_signals?.signals ?? []
  const entryTuning = data?.entry_signals?.tuning ?? []
  const entryOutcomeCalibration = data?.entry_signals?.outcome_calibration
  const manualBridge = data?.manual_review_bridge
  const actionTone = (action: string) => {
    const a = String(action || '').toUpperCase()
    if (a === 'PAPER_ENTRY' || a === 'MANUAL_REVIEW') return '#00d48a'
    if (a === 'WATCH') return '#60a5fa'
    if (a === 'TOO_LATE') return '#f59e0b'
    return '#7f95a8'
  }
  const bandTone = (band: string | null | undefined) => {
    const b = String(band || '').toUpperCase()
    if (b === 'BUYABLE') return '#00d48a'
    if (b === 'TRIGGERED') return '#2dd4bf'
    if (b === 'WATCH') return '#60a5fa'
    if (b === 'TOO_LATE') return '#f59e0b'
    if (b === 'IGNORE') return '#ef4444'
    return '#7f95a8'
  }
  const clusterTone = (label: string | null | undefined, distribution?: number | null) => {
    const l = String(label || '').toUpperCase()
    const d = Number(distribution ?? 0)
    if (l === 'DISTRIBUTION_RISK' || d >= 65) return '#ef4444'
    if (l === 'INSIDER_LIKE') return '#f59e0b'
    if (l === 'COORDINATED_ACCUMULATION') return '#2dd4bf'
    if (l === 'ORGANIC_ACCUMULATION' || l === 'CLEAN_EARLY') return '#00d48a'
    return '#7f95a8'
  }
  const entryTone = (state: string | null | undefined) => {
    const s = String(state || '').toUpperCase()
    if (s === 'ENTRY_NOW') return '#00d48a'
    if (s === 'ARMED') return '#f59e0b'
    if (s === 'AVOID_CHASE' || s === 'EXIT_PRESSURE') return '#ef4444'
    return '#60a5fa'
  }
  const entryZoneTone = (state: string | null | undefined) => {
    const s = String(state || '').toUpperCase()
    if (s === 'BUY_NOW') return '#00d48a'
    if (s === 'WAIT_VOLUME_CLEAR' || s === 'WAIT_TRIGGER' || s === 'WAIT_PROOF') return '#f59e0b'
    if (s === 'WAIT_PULLBACK' || s === 'WAIT_BASE') return '#60a5fa'
    if (s === 'TOO_EXTENDED' || s === 'MISSED_MOVE' || s === 'AVOID_RISK') return '#ef4444'
    return '#7f95a8'
  }
  const exitTone = (state: string | null | undefined) => {
    const s = String(state || '').toUpperCase()
    if (s === 'LET_RUNNER_WORK' || s === 'HOLD_MONITOR') return '#00d48a'
    if (s === 'PROTECT_FAST' || s === 'TRIM_STRENGTH' || s === 'NO_POSITION_YET') return '#f59e0b'
    if (s === 'TRIM_HARD' || s === 'SELL_NOW') return '#ef4444'
    return '#60a5fa'
  }
  const executionTone = (state: string | null | undefined) => {
    const s = String(state || '').toUpperCase()
    if (s === 'DEPLOYABLE_NOW' || s === 'ENTRY_READY') return '#00d48a'
    if (s === 'PAPER_ENTRY_NOW' || s === 'PAPER_READY') return '#2dd4bf'
    if (s === 'RESEARCH_ENTRY_NOW' || s === 'RESEARCH_HOT' || s === 'BLOCKED_BUT_HOT' || s === 'LAST_BLOCKER' || s === 'ARMED' || s === 'WATCHING' || s === 'BLOCKED_IMPROVING') return '#f59e0b'
    if (s === 'AVOID_CHASE' || s === 'EXIT_PRESSURE' || s === 'TOO_LATE' || s === 'MISSED_RUNNER' || s === 'FADED') return '#ef4444'
    return '#60a5fa'
  }
  const intelTone = (label: string | null | undefined) => {
    const l = String(label || '').toUpperCase()
    if (['DURABLE', 'SUSTAINABLE', 'STRUCTURAL'].includes(l)) return '#00d48a'
    if (['ACTIVE', 'CONFIRMED_FLOW', 'NEEDS_CONFIRMATION'].includes(l)) return '#2dd4bf'
    if (['FRAGILE', 'HYPE', 'BACKGROUND'].includes(l)) return '#f59e0b'
    if (['FADE_RISK', 'ONE_SHOT'].includes(l)) return '#ef4444'
    return '#7f95a8'
  }
  const manualDecisionTone = (label: string | null | undefined) => {
    const l = String(label || '').toUpperCase()
    if (l === 'BUY' || l.includes('BUY_WIN') || l.includes('CORRECT')) return '#00d48a'
    if (l === 'WATCH' || l === 'NEEDS_MORE_PROOF' || l.includes('TRACKING')) return '#60a5fa'
    if (l.includes('MISSED') || l === 'TOO_LATE') return '#f59e0b'
    if (l === 'BAD_CA' || l.includes('LOSS')) return '#ef4444'
    return '#14b8a6'
  }
  const eventTone = (eventType: string | null | undefined) => {
    const e = String(eventType || '').toUpperCase()
    if (e === 'NEWLY_ACTIONABLE') return '#00d48a'
    if (e === 'MISSED_RUNNER') return '#f59e0b'
    if (e === 'FADED_AFTER_READY') return '#ef4444'
    return '#60a5fa'
  }
  const ResearchMiniMetric = ({ label, value, tone = '#8ca0b3' }: { label: string; value: string; tone?: string }) => (
    <span style={{ ...MONO, fontSize: 8, color: tone }}>
      {label} <span style={{ color: '#d7e1ea' }}>{value}</span>
    </span>
  )
  const Pill = ({ label, value, tone }: { label: string; value: string | number; tone: string }) => (
    <span style={{
      ...MONO,
      fontSize: 8,
      color: tone,
      background: `${tone}10`,
      border: `1px solid ${tone}24`,
      borderRadius: 7,
      padding: '4px 7px',
    }}>
      {label} {value}
    </span>
  )
  const OpportunityCard = ({ item, compact = false }: { item: OpportunityLedgerItem; compact?: boolean }) => {
    const tone = executionTone(item.state || item.execution_alignment_state || item.entry_state)
    const state = String(item.state || 'WATCH').replace(/_/g, ' ')
    const contract = item.trigger_contract || item.status?.why_now || 'Waiting for a clean trigger contract.'
    return (
      <div style={{
        background: `${tone}08`,
        border: `1px solid ${tone}22`,
        borderRadius: 10,
        padding: compact ? 8 : 10,
        display: 'flex',
        flexDirection: 'column',
        gap: 6,
        minHeight: compact ? undefined : 112,
      }}>
        <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
          <span style={{ ...MONO, fontSize: 11, color: '#f3f7fb', fontWeight: 900 }}>{item.symbol || 'TOKEN'}</span>
          <span style={{ ...MONO, fontSize: 7, color: tone, fontWeight: 900 }}>{state}</span>
          <span style={{ ...MONO, fontSize: 8, color: '#8ca0b3', marginLeft: 'auto' }}>
            Q {item.signal_quality_tier || '—'} {fmtFixed(item.signal_quality_score, 0)}
          </span>
        </div>
        <TokenAddressChip value={item.mint || ''} />
        <span style={{ ...MONO, fontSize: 8, color: '#d7e1ea', lineHeight: 1.45 }}>
          {contract}
        </span>
        {(item.missed_reason || item.lesson) && (
          <span style={{ ...MONO, fontSize: 8, color: item.missed_reason ? '#f59e0b' : '#8ca0b3', lineHeight: 1.45 }}>
            {item.missed_reason || item.lesson}
          </span>
        )}
        {(item.outcome_label || item.learned_summary) && (
          <span style={{ ...MONO, fontSize: 8, color: item.outcome_label?.includes('MISSED') ? '#f59e0b' : item.outcome_label?.includes('WIN') ? '#00d48a' : '#8ca0b3', lineHeight: 1.45 }}>
            {item.outcome_label || 'TRACKING'} · {item.learned_summary || item.trigger_verdict || 'learning pending'}
          </span>
        )}
        <div style={{ display: 'flex', gap: 7, flexWrap: 'wrap', marginTop: 'auto' }}>
          <span style={{ ...MONO, fontSize: 8, color: '#7f95a8' }}>MC {fmtUsd(item.current_marketcap)}</span>
          <span style={{ ...MONO, fontSize: 8, color: '#7f95a8' }}>max {fmtUsd(item.max_observed_marketcap)}</span>
          <span style={{ ...MONO, fontSize: 8, color: item.max_observed_return_pct && item.max_observed_return_pct >= 100 ? '#f59e0b' : '#7f95a8' }}>
            watch +{fmtFixed(item.max_return_from_watch_pct ?? item.max_observed_return_pct, 0)}%
          </span>
          {item.last_blocker && (
            <span style={{ ...MONO, fontSize: 8, color: tone }}>blocker {String(item.last_blocker).replace(/_/g, ' ')}</span>
          )}
        </div>
      </div>
    )
  }
  return (
    <div style={{
      border: '1px solid rgba(96,165,250,0.18)',
      borderTop: '2px solid #60a5fa',
      borderRadius: 16,
      padding: 14,
      background:
        'radial-gradient(circle at 10% 0%, rgba(96,165,250,0.12) 0%, transparent 32%),' +
        'linear-gradient(180deg, rgba(5,10,18,0.78), rgba(3,7,13,0.72))',
      display: 'flex',
      flexDirection: 'column',
      gap: 12,
    }}>
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 5, minWidth: 260, flex: 1 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
            <span style={{ ...MONO, fontSize: 9, color: '#60a5fa', fontWeight: 900, letterSpacing: '0.16em' }}>
              MEMECOIN RESEARCH DOSSIERS
            </span>
            <span className="badge" style={{ color: '#60a5fa', background: 'rgba(96,165,250,0.10)', border: '1px solid rgba(96,165,250,0.24)', fontSize: 8 }}>
              STRUCTURED
            </span>
            <span style={{ ...MONO, fontSize: 8, color: '#7f95a8' }}>
              {data?.summary.total ?? 0} ranked · {data?.summary.known_memory_count ?? 0} mint-memory · {data?.summary.catalyst_linked_count ?? 0} catalyst-linked
            </span>
          </div>
          <span style={{ ...MONO, fontSize: 9, color: '#9fb3c8', lineHeight: 1.55 }}>
            {data?.headline || 'Ranks narrative, memory, community proof, catalyst, tradeability, risk, and outcome feedback.'}
          </span>
        </div>
        <div style={{ display: 'flex', gap: 7, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
          <Pill label="BUYABLE" value={bandCounts.BUYABLE ?? 0} tone={bandTone('BUYABLE')} />
          <Pill label="DEPLOY" value={data?.summary.deployable_now_count ?? 0} tone="#00d48a" />
          <Pill label="PAPER" value={data?.summary.paper_entry_now_count ?? 0} tone="#2dd4bf" />
          <Pill label="HOT" value={data?.summary.research_hot_count ?? 0} tone="#f59e0b" />
          <Pill label="ARMED" value={data?.summary.armed_count ?? 0} tone="#f59e0b" />
          <Pill label="DURABLE" value={data?.summary.active_attention_count ?? 0} tone="#2dd4bf" />
          <Pill label="STRUCT" value={data?.summary.confirmed_flow_count ?? 0} tone="#00d48a" />
          <Pill label="SUSTAIN" value={data?.summary.sustainable_count ?? 0} tone="#00d48a" />
          <Pill label="TRIGGERED" value={bandCounts.TRIGGERED ?? 0} tone={bandTone('TRIGGERED')} />
          <Pill label="WATCH" value={bandCounts.WATCH ?? 0} tone={bandTone('WATCH')} />
          <Pill label="LATE" value={bandCounts.TOO_LATE ?? 0} tone={bandTone('TOO_LATE')} />
          <Pill label="CLUSTER" value={data?.summary.insider_like_count ?? 0} tone="#f59e0b" />
          <Pill label="REPEAT" value={data?.summary.repeat_operator_count ?? 0} tone="#2dd4bf" />
          <Pill label="DISTRIB" value={data?.summary.distribution_risk_count ?? 0} tone="#ef4444" />
          <Pill label="outcomes" value={data?.summary.outcome_linked_count ?? 0} tone="#a78bfa" />
          <Pill label="catalysts" value={`${data?.catalysts?.summary.total_24h ?? 0}/24h`} tone="#2dd4bf" />
        </div>
      </div>

      {operator && (
        <div style={{
          background: `${bandTone(operator.mode)}0b`,
          border: `1px solid ${bandTone(operator.mode)}24`,
          borderLeft: `3px solid ${bandTone(operator.mode)}`,
          borderRadius: '0 12px 12px 0',
          padding: 12,
          display: 'grid',
          gridTemplateColumns: 'minmax(0, 1.5fr) minmax(240px, 0.8fr)',
          gap: 12,
          alignItems: 'center',
        }}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
              <span style={{ ...MONO, fontSize: 8, color: '#8fb7dc', letterSpacing: '0.14em', fontWeight: 900 }}>
                ONE BEST ACTION
              </span>
              <span className="badge" style={{ color: bandTone(operator.mode), background: `${bandTone(operator.mode)}12`, border: `1px solid ${bandTone(operator.mode)}28`, fontSize: 7 }}>
                {operator.mode.replace(/_/g, ' ')}
              </span>
              {topAction?.symbol && (
                <span style={{ ...MONO, fontSize: 12, color: '#f3f7fb', fontWeight: 900 }}>
                  {topAction.symbol}
                </span>
              )}
              {topAction?.mint && <TokenAddressChip value={topAction.mint} />}
            </div>
            <span style={{ ...MONO, fontSize: 10, color: '#d7e1ea', lineHeight: 1.45 }}>
              {operator.headline}
            </span>
            <span style={{ ...MONO, fontSize: 8, color: '#9fb3c8', lineHeight: 1.45 }}>
              {operator.instruction}
            </span>
            {topAction?.why_now && (
              <span style={{ ...MONO, fontSize: 8, color: '#7f95a8', lineHeight: 1.45 }}>
                why now: {topAction.why_now}
              </span>
            )}
            {(topAction?.trade_thesis || topAction?.entry_zone) && (
              <div style={{
                background: `${entryZoneTone(topAction.entry_zone?.zone)}0b`,
                border: `1px solid ${entryZoneTone(topAction.entry_zone?.zone)}24`,
                borderRadius: 10,
                padding: 8,
                display: 'flex',
                flexDirection: 'column',
                gap: 5,
              }}>
                <div style={{ display: 'flex', gap: 7, alignItems: 'center', flexWrap: 'wrap' }}>
                  <span style={{ ...MONO, fontSize: 7, color: entryZoneTone(topAction.entry_zone?.zone), fontWeight: 900 }}>
                    DECISION THESIS
                  </span>
                  <span style={{ ...MONO, fontSize: 8, color: '#f3f7fb', fontWeight: 900 }}>
                    {String(topAction.entry_zone?.label || topAction.trade_thesis?.decision || 'WAIT').replace(/_/g, ' ')}
                  </span>
                  <span style={{ ...MONO, fontSize: 8, color: entryZoneTone(topAction.entry_zone?.zone), marginLeft: 'auto' }}>
                    {fmtFixed(topAction.entry_zone?.confidence ?? topAction.trade_thesis?.confidence, 0)}/100
                  </span>
                </div>
                <span style={{ ...MONO, fontSize: 8, color: '#d7e1ea', lineHeight: 1.45 }}>
                  {topAction.trade_thesis?.headline || topAction.entry_zone?.action || 'Waiting for a cleaner decision map.'}
                </span>
                <span style={{ ...MONO, fontSize: 8, color: '#9fb3c8', lineHeight: 1.45 }}>
                  ideal: {topAction.entry_zone?.ideal_entry || topAction.trade_thesis?.ideal_entry || 'learning'} · confirm: {topAction.entry_zone?.confirmation_needed || topAction.trade_thesis?.confirmation_needed || 'fresh flow'}
                </span>
              </div>
            )}
            {(topAction?.position_plan || topAction?.exit_intelligence) && (
              <div style={{
                display: 'grid',
                gridTemplateColumns: 'repeat(auto-fit, minmax(210px, 1fr))',
                gap: 7,
              }}>
                {topAction.position_plan && (
                  <div style={{
                    background: 'rgba(45,212,191,0.07)',
                    border: '1px solid rgba(45,212,191,0.20)',
                    borderRadius: 10,
                    padding: 8,
                    display: 'flex',
                    flexDirection: 'column',
                    gap: 4,
                  }}>
                    <span style={{ ...MONO, fontSize: 7, color: '#2dd4bf', fontWeight: 900 }}>POSITION PLAN</span>
                    <span style={{ ...MONO, fontSize: 8, color: '#d7e1ea', lineHeight: 1.45 }}>
                      {String(topAction.position_plan.stance || 'WAIT').replace(/_/g, ' ')} · {topAction.position_plan.starter || topAction.position_plan.size_label || 'no size yet'}
                    </span>
                    <span style={{ ...MONO, fontSize: 8, color: '#8ca0b3', lineHeight: 1.45 }}>
                      {topAction.position_plan.invalid_stop || topAction.position_plan.max_risk_note || 'Invalidate on thesis break.'}
                    </span>
                  </div>
                )}
                {topAction.exit_intelligence && (
                  <div style={{
                    background: `${exitTone(topAction.exit_intelligence.state)}0b`,
                    border: `1px solid ${exitTone(topAction.exit_intelligence.state)}24`,
                    borderRadius: 10,
                    padding: 8,
                    display: 'flex',
                    flexDirection: 'column',
                    gap: 4,
                  }}>
                    <span style={{ ...MONO, fontSize: 7, color: exitTone(topAction.exit_intelligence.state), fontWeight: 900 }}>EXIT INTEL</span>
                    <span style={{ ...MONO, fontSize: 8, color: '#d7e1ea', lineHeight: 1.45 }}>
                      {String(topAction.exit_intelligence.state || 'MONITOR').replace(/_/g, ' ')} · health {String(topAction.exit_intelligence.thesis_health_label || '—').replace(/_/g, ' ')} {fmtFixed(topAction.exit_intelligence.thesis_health_score, 0)}
                    </span>
                    <span style={{ ...MONO, fontSize: 8, color: '#8ca0b3', lineHeight: 1.45 }}>
                      {topAction.exit_intelligence.action || topAction.exit_intelligence.hold_condition || 'Monitor thesis health.'}
                    </span>
                  </div>
                )}
              </div>
            )}
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 7 }}>
            <ResearchMiniMetric label="GOOD" value={fmtFixed(topAction?.good_coin_score, 0)} tone={topAction?.good_coin_status === 'PASS' ? '#00d48a' : '#f59e0b'} />
            <ResearchMiniMetric label="CATALYST" value={`${topAction?.catalyst_strength_label || '—'} ${fmtFixed(topAction?.catalyst_strength_score, 0)}`} tone={bandTone(topAction?.catalyst_strength_label === 'STRONG' ? 'BUYABLE' : topAction?.catalyst_strength_label === 'MEDIUM' ? 'TRIGGERED' : 'WATCH')} />
            <ResearchMiniMetric label="LATE" value={`${topAction?.too_late_label || '—'} ${fmtFixed(topAction?.too_late_score, 0)}`} tone={topAction?.too_late_label === 'EXTENDED' ? '#ef4444' : topAction?.too_late_label === 'ELEVATED' ? '#f59e0b' : '#00d48a'} />
            <ResearchMiniMetric label="PRIORITY" value={fmtFixed(topAction?.operator_priority, 0)} tone="#a78bfa" />
            <ResearchMiniMetric label="EXEC" value={`${topAction?.execution_alignment_label || '—'} ${fmtFixed(topAction?.execution_alignment_confidence, 0)}`} tone={executionTone(topAction?.execution_alignment_state)} />
          </div>
        </div>
      )}

      {manualBridge && (
        <div style={{
          background: 'rgba(20,184,166,0.055)',
          border: '1px solid rgba(20,184,166,0.18)',
          borderRadius: 11,
          padding: 10,
          display: 'flex',
          flexDirection: 'column',
          gap: 9,
        }}>
          <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1.15fr) minmax(230px, 0.85fr)', gap: 10 }}>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
              <div style={{ display: 'flex', gap: 7, alignItems: 'center', flexWrap: 'wrap' }}>
                <span style={{ ...MONO, fontSize: 8, color: '#14b8a6', fontWeight: 900, letterSpacing: '0.14em' }}>
                  WHAT YOUR LABELS ARE TEACHING
                </span>
                <span className="badge" style={{ color: '#14b8a6', background: 'rgba(20,184,166,0.10)', border: '1px solid rgba(20,184,166,0.24)', fontSize: 7 }}>
                  {manualBridge.calibration?.guidance_state || manualBridge.summary?.state || 'LEARNING'}
                </span>
                <span style={{ ...MONO, fontSize: 8, color: '#7f95a8' }}>
                  {manualBridge.summary?.undecided ?? 0} undecided · {manualBridge.summary?.judged_n ?? 0} judged · good {fmtFixed(manualBridge.summary?.good_rate_pct, 0)}% · missed {manualBridge.summary?.missed_n ?? 0}
                </span>
              </div>
              <span style={{ ...MONO, fontSize: 8, color: '#d7e1ea', lineHeight: 1.45 }}>
                {manualBridge.calibration?.headline || manualBridge.prompt || 'Label research-hot names so the system can learn from your judgment.'}
              </span>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                {(manualBridge.calibration?.actions ?? []).slice(0, 3).map(action => (
                  <span key={action} style={{ ...MONO, fontSize: 8, color: '#9fb3c8', lineHeight: 1.4 }}>
                    {action}
                  </span>
                ))}
              </div>
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 5, justifyContent: 'center' }}>
              {(manualBridge.calibration?.by_decision ?? []).slice(0, 4).map(row => {
                const tone = manualDecisionTone(row.decision)
                return (
                  <span key={String(row.decision)} style={{
                    ...MONO,
                    fontSize: 8,
                    color: tone,
                    background: `${tone}0f`,
                    border: `1px solid ${tone}24`,
                    borderRadius: 7,
                    padding: '5px 7px',
                    lineHeight: 1.35,
                  }}>
                    {String(row.decision || 'decision').replace(/_/g, ' ')} · {row.judged_n ?? 0}/{row.sample_n ?? 0} judged · good {fmtFixed(row.good_rate_pct, 0)}% · missed {row.missed_n ?? 0} · max {fmtFixed(row.avg_max_return_pct, 1)}%
                  </span>
                )
              })}
              {!(manualBridge.calibration?.by_decision ?? []).length && (
                <span style={{ ...MONO, fontSize: 8, color: '#7f95a8' }}>waiting for manual labels</span>
              )}
            </div>
          </div>

          {(manualBridge.missed_runner_replay ?? []).length > 0 && (
            <div style={{
              borderTop: '1px solid rgba(20,184,166,0.14)',
              paddingTop: 8,
              display: 'flex',
              flexDirection: 'column',
              gap: 7,
            }}>
              <div style={{ display: 'flex', gap: 7, alignItems: 'center', flexWrap: 'wrap' }}>
                <span style={{ ...MONO, fontSize: 8, color: '#f59e0b', fontWeight: 900, letterSpacing: '0.14em' }}>
                  MISSED RUNNER REPLAY
                </span>
                <span style={{ ...MONO, fontSize: 8, color: '#7f95a8' }}>
                  shows where a label/blocker would have kept us out of a move
                </span>
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 7 }}>
                {(manualBridge.missed_runner_replay ?? []).slice(0, 3).map(item => {
                  const tone = manualDecisionTone(item.outcome_label || item.decision)
                  return (
                    <div key={`${item.id}-${item.mint}`} style={{
                      background: `${tone}0a`,
                      border: `1px solid ${tone}22`,
                      borderRadius: 9,
                      padding: 8,
                      display: 'flex',
                      flexDirection: 'column',
                      gap: 5,
                    }}>
                      <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
                        <span style={{ ...MONO, fontSize: 10, color: '#f3f7fb', fontWeight: 900 }}>{item.symbol || 'TOKEN'}</span>
                        <span style={{ ...MONO, fontSize: 7, color: tone, fontWeight: 900 }}>{String(item.decision || 'label').replace(/_/g, ' ')}</span>
                        <span style={{ ...MONO, fontSize: 8, color: '#f59e0b', marginLeft: 'auto' }}>+{fmtFixed(item.max_return_pct, 0)}%</span>
                      </div>
                      <TokenAddressChip value={item.mint || ''} />
                      <span style={{ ...MONO, fontSize: 8, color: '#d7e1ea', lineHeight: 1.4 }}>
                        {item.why_it_matters || item.headline || 'Manual label replay is tracking this move.'}
                      </span>
                      <span style={{ ...MONO, fontSize: 8, color: '#8ca0b3', lineHeight: 1.4 }}>
                        blocker {String(item.blocker || 'unknown').replace(/_/g, ' ')} · {item.lesson || 'Replay once enough samples build.'}
                      </span>
                    </div>
                  )
                })}
              </div>
            </div>
          )}
        </div>
      )}

      {escalationItems.length > 0 && (
        <div style={{
          background: 'linear-gradient(90deg, rgba(0,212,138,0.08), rgba(245,158,11,0.05))',
          border: '1px solid rgba(45,212,191,0.20)',
          borderRadius: 12,
          padding: 10,
          display: 'flex',
          flexDirection: 'column',
          gap: 8,
        }}>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
            <span style={{ ...MONO, fontSize: 8, color: '#2dd4bf', fontWeight: 900, letterSpacing: '0.14em' }}>
              ENTRY ESCALATION ALERTS
            </span>
            <span style={{ ...MONO, fontSize: 8, color: '#7f95a8' }}>
              {opportunityLedger?.escalation_queue?.summary?.newly_actionable ?? 0} actionable · {opportunityLedger?.escalation_queue?.summary?.trigger_cleared ?? 0} trigger-cleared · {opportunityLedger?.escalation_queue?.summary?.missed_runner ?? 0} missed
            </span>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 7 }}>
            {escalationItems.slice(0, 4).map(event => {
              const tone = eventTone(event.event_type)
              return (
                <div key={`${event.id}-${event.mint}-${event.event_type}`} style={{
                  background: `${tone}09`,
                  border: `1px solid ${tone}22`,
                  borderRadius: 10,
                  padding: 8,
                  display: 'flex',
                  flexDirection: 'column',
                  gap: 5,
                }}>
                  <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
                    <span style={{ ...MONO, fontSize: 10, color: '#f3f7fb', fontWeight: 900 }}>{event.symbol || 'TOKEN'}</span>
                    <span style={{ ...MONO, fontSize: 7, color: tone, fontWeight: 900 }}>{String(event.event_type || 'STATE').replace(/_/g, ' ')}</span>
                    <span style={{ ...MONO, fontSize: 8, color: '#8ca0b3', marginLeft: 'auto' }}>prio {fmtFixed(event.alert_priority, 0)}</span>
                  </div>
                  <TokenAddressChip value={event.mint || ''} />
                  <span style={{ ...MONO, fontSize: 8, color: '#d7e1ea', lineHeight: 1.45 }}>
                    {event.headline || `${event.previous_state || 'new'} → ${event.new_state || 'state changed'}`}
                  </span>
                  <span style={{ ...MONO, fontSize: 8, color: '#9fb3c8', lineHeight: 1.45 }}>
                    {event.detail || event.trigger_contract || 'Transition logged for outcome review.'}
                  </span>
                  <div style={{ display: 'flex', gap: 7, flexWrap: 'wrap' }}>
                    <span style={{ ...MONO, fontSize: 8, color: '#7f95a8' }}>{String(event.previous_state || 'new').replace(/_/g, ' ')} → {String(event.new_state || 'unknown').replace(/_/g, ' ')}</span>
                    {event.last_blocker && <span style={{ ...MONO, fontSize: 8, color: tone }}>blocker {String(event.last_blocker).replace(/_/g, ' ')}</span>}
                    <span style={{ ...MONO, fontSize: 8, color: '#7f95a8' }}>max +{fmtFixed(event.max_return_from_watch_pct, 0)}%</span>
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      )}

      {trustCalibration && (
        <div style={{
          background: 'linear-gradient(135deg, rgba(15,23,42,0.64), rgba(8,13,22,0.72))',
          border: '1px solid rgba(167,139,250,0.20)',
          borderTop: '2px solid rgba(167,139,250,0.72)',
          borderRadius: 12,
          padding: 11,
          display: 'flex',
          flexDirection: 'column',
          gap: 9,
        }}>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
            <span style={{ ...MONO, fontSize: 8, color: '#a78bfa', fontWeight: 900, letterSpacing: '0.14em' }}>
              TRUST CALIBRATION
            </span>
            <span className="badge" style={{ color: '#a78bfa', background: 'rgba(167,139,250,0.10)', border: '1px solid rgba(167,139,250,0.24)', fontSize: 7 }}>
              WAITING VS MISSING
            </span>
            <span style={{ ...MONO, fontSize: 8, color: '#7f95a8' }}>
              {trustCalibration.waiting_vs_missing?.judged_n ?? 0} judged · helped {fmtFixed(trustCalibration.waiting_vs_missing?.waiting_helped_pct, 0)}% · hurt {fmtFixed(trustCalibration.waiting_vs_missing?.waiting_hurt_pct, 0)}%
            </span>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(210px, 1fr))', gap: 8 }}>
            {(trustCalibration.dashboard_questions ?? []).slice(0, 4).map((q, idx) => {
              const state = String(q.state || '').toUpperCase()
              const tone = state.includes('REVIEW') ? '#f59e0b' : state.includes('CLEAR') ? '#00d48a' : state.includes('HURT') ? '#ef4444' : '#60a5fa'
              return (
                <div key={`${q.label}-${idx}`} style={{
                  background: `${tone}08`,
                  border: `1px solid ${tone}20`,
                  borderRadius: 10,
                  padding: 8,
                  display: 'flex',
                  flexDirection: 'column',
                  gap: 4,
                }}>
                  <span style={{ ...MONO, fontSize: 7, color: tone, fontWeight: 900 }}>{q.label || 'Calibration question'}</span>
                  <span style={{ ...MONO, fontSize: 8, color: '#d7e1ea', lineHeight: 1.45 }}>{q.answer || 'Learning pending.'}</span>
                </div>
              )
            })}
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(230px, 1fr))', gap: 8 }}>
            <div style={{ background: 'rgba(2,6,23,0.32)', border: '1px solid rgba(148,163,184,0.12)', borderRadius: 10, padding: 8, display: 'flex', flexDirection: 'column', gap: 5 }}>
              <span style={{ ...MONO, fontSize: 7, color: '#60a5fa', fontWeight: 900 }}>BLOCKER TRUST SCORES</span>
              {(trustCalibration.blocker_trust ?? []).slice(0, 4).map(blocker => {
                const trust = Number(blocker.trust_score ?? 0)
                const tone = trust >= 70 ? '#00d48a' : trust <= 38 ? '#ef4444' : '#f59e0b'
                return (
                  <div key={String(blocker.blocker)} style={{ display: 'flex', gap: 7, alignItems: 'center', flexWrap: 'wrap' }}>
                    <span style={{ ...MONO, fontSize: 8, color: '#d7e1ea' }}>{String(blocker.blocker || 'unknown').replace(/_/g, ' ')}</span>
                    <span style={{ ...MONO, fontSize: 8, color: tone, marginLeft: 'auto' }}>{fmtFixed(blocker.trust_score, 0)}</span>
                    <span style={{ ...MONO, fontSize: 7, color: '#7f95a8' }}>{String(blocker.recommendation || 'learning').replace(/_/g, ' ')}</span>
                  </div>
                )
              })}
            </div>
            <div style={{ background: 'rgba(2,6,23,0.32)', border: '1px solid rgba(148,163,184,0.12)', borderRadius: 10, padding: 8, display: 'flex', flexDirection: 'column', gap: 5 }}>
              <span style={{ ...MONO, fontSize: 7, color: '#2dd4bf', fontWeight: 900 }}>TRIGGER TIMING REPLAY</span>
              {(trustCalibration.trigger_replay ?? []).slice(0, 4).map(trigger => {
                const timing = String(trigger.timing_label || 'pending')
                const tone = timing === 'useful' ? '#00d48a' : timing === 'too_late' ? '#f59e0b' : timing === 'bad_trigger' ? '#ef4444' : '#7f95a8'
                return (
                  <div key={String(trigger.trigger_verdict)} style={{ display: 'flex', gap: 7, alignItems: 'center', flexWrap: 'wrap' }}>
                    <span style={{ ...MONO, fontSize: 8, color: '#d7e1ea' }}>{String(trigger.trigger_verdict || 'pending').replace(/_/g, ' ')}</span>
                    <span style={{ ...MONO, fontSize: 8, color: tone, marginLeft: 'auto' }}>{timing.replace(/_/g, ' ')}</span>
                    <span style={{ ...MONO, fontSize: 7, color: '#7f95a8' }}>{trigger.sample_n ?? 0} sample</span>
                  </div>
                )
              })}
            </div>
            <div style={{ background: 'rgba(2,6,23,0.32)', border: '1px solid rgba(148,163,184,0.12)', borderRadius: 10, padding: 8, display: 'flex', flexDirection: 'column', gap: 5 }}>
              <span style={{ ...MONO, fontSize: 7, color: '#f59e0b', fontWeight: 900 }}>POLICY GUIDANCE</span>
              {(trustCalibration.policy_guidance?.actions ?? ['Do not change entry policy yet; judged outcome sample is still thin.']).slice(0, 3).map(action => (
                <span key={action} style={{ ...MONO, fontSize: 8, color: '#d7e1ea', lineHeight: 1.45 }}>{action}</span>
              ))}
              <span style={{ ...MONO, fontSize: 7, color: '#7f95a8', lineHeight: 1.45 }}>
                {trustCalibration.policy_guidance?.live_policy || 'Keep live/manual deployment conservative.'}
              </span>
            </div>
          </div>
          {(trustCalibration.missed_runner_autopsies ?? []).length > 0 && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              <span style={{ ...MONO, fontSize: 7, color: '#ef4444', fontWeight: 900 }}>MISSED RUNNER AUTOPSIES</span>
              {(trustCalibration.missed_runner_autopsies ?? []).slice(0, 2).map(autopsy => (
                <div key={`${autopsy.mint}-${autopsy.root_cause}`} style={{
                  background: 'rgba(239,68,68,0.06)',
                  border: '1px solid rgba(239,68,68,0.18)',
                  borderRadius: 9,
                  padding: 8,
                  display: 'flex',
                  flexDirection: 'column',
                  gap: 5,
                }}>
                  <div style={{ display: 'flex', gap: 7, alignItems: 'center', flexWrap: 'wrap' }}>
                    <span style={{ ...MONO, fontSize: 10, color: '#f3f7fb', fontWeight: 900 }}>{autopsy.symbol || 'TOKEN'}</span>
                    <TokenAddressChip value={autopsy.mint || ''} />
                    <span style={{ ...MONO, fontSize: 8, color: '#f59e0b', marginLeft: 'auto' }}>+{fmtFixed(autopsy.max_return_from_watch_pct, 0)}%</span>
                  </div>
                  <span style={{ ...MONO, fontSize: 8, color: '#d7e1ea', lineHeight: 1.45 }}>{autopsy.why}</span>
                  <span style={{ ...MONO, fontSize: 7, color: '#8ca0b3', lineHeight: 1.45 }}>
                    root: {String(autopsy.root_cause || 'unknown').replace(/_/g, ' ')} · {autopsy.policy_hint}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {shadowLab && (
        <div style={{
          background: 'linear-gradient(135deg, rgba(20,184,166,0.07), rgba(15,23,42,0.70))',
          border: '1px solid rgba(20,184,166,0.20)',
          borderTop: '2px solid rgba(20,184,166,0.72)',
          borderRadius: 12,
          padding: 11,
          display: 'flex',
          flexDirection: 'column',
          gap: 9,
        }}>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
            <span style={{ ...MONO, fontSize: 8, color: '#14b8a6', fontWeight: 900, letterSpacing: '0.14em' }}>
              PAPER SHADOW STRATEGY LAB
            </span>
            <span className="badge" style={{ color: '#14b8a6', background: 'rgba(20,184,166,0.10)', border: '1px solid rgba(20,184,166,0.24)', fontSize: 7 }}>
              EXPERIMENT ENGINE
            </span>
            <span style={{ ...MONO, fontSize: 8, color: '#7f95a8' }}>
              {shadowLab.summary?.strategy_count ?? 0} strategies · {shadowLab.summary?.entered_n ?? 0} entered · {shadowLab.summary?.judged_n ?? 0} judged · {shadowLab.summary?.state || 'LEARNING'}
            </span>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(210px, 1fr))', gap: 8 }}>
            <div style={{ background: 'rgba(2,6,23,0.34)', border: '1px solid rgba(148,163,184,0.12)', borderRadius: 10, padding: 8, display: 'flex', flexDirection: 'column', gap: 4 }}>
              <span style={{ ...MONO, fontSize: 7, color: '#00d48a', fontWeight: 900 }}>BEST STRATEGY</span>
              <span style={{ ...MONO, fontSize: 9, color: '#d7e1ea', fontWeight: 900 }}>
                {String(shadowLab.best_strategy?.strategy_key || 'learning').replace(/_/g, ' ')}
              </span>
              <span style={{ ...MONO, fontSize: 8, color: '#8ca0b3' }}>
                avg max +{fmtFixed(shadowLab.best_strategy?.avg_max_return_pct, 1)}% · win {fmtFixed(shadowLab.best_strategy?.win_rate_pct, 0)}%
              </span>
            </div>
            <div style={{ background: 'rgba(2,6,23,0.34)', border: '1px solid rgba(148,163,184,0.12)', borderRadius: 10, padding: 8, display: 'flex', flexDirection: 'column', gap: 4 }}>
              <span style={{ ...MONO, fontSize: 7, color: '#ef4444', fontWeight: 900 }}>WORST STRATEGY</span>
              <span style={{ ...MONO, fontSize: 9, color: '#d7e1ea', fontWeight: 900 }}>
                {String(shadowLab.worst_strategy?.strategy_key || 'learning').replace(/_/g, ' ')}
              </span>
              <span style={{ ...MONO, fontSize: 8, color: '#8ca0b3' }}>
                avg max +{fmtFixed(shadowLab.worst_strategy?.avg_max_return_pct, 1)}% · losses {shadowLab.worst_strategy?.loss_n ?? 0}
              </span>
            </div>
            <div style={{ background: 'rgba(2,6,23,0.34)', border: '1px solid rgba(148,163,184,0.12)', borderRadius: 10, padding: 8, display: 'flex', flexDirection: 'column', gap: 4 }}>
              <span style={{ ...MONO, fontSize: 7, color: '#f59e0b', fontWeight: 900 }}>EARLIER VS WAITING</span>
              <span style={{ ...MONO, fontSize: 9, color: shadowLab.summary?.earlier_entry_beating_waiting ? '#00d48a' : '#d7e1ea', fontWeight: 900 }}>
                {shadowLab.summary?.earlier_entry_beating_waiting ? 'earlier is winning' : 'not proven yet'}
              </span>
              <span style={{ ...MONO, fontSize: 8, color: '#8ca0b3' }}>
                trigger-confirmed entries remain paper-only until enough samples exist
              </span>
            </div>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 8 }}>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
              <span style={{ ...MONO, fontSize: 7, color: '#14b8a6', fontWeight: 900 }}>STRATEGY RANKING</span>
              {(shadowLab.ranking ?? []).slice(0, 4).map(row => (
                <div key={String(row.strategy_key)} style={{ display: 'flex', gap: 7, alignItems: 'center', flexWrap: 'wrap' }}>
                  <span style={{ ...MONO, fontSize: 8, color: '#d7e1ea' }}>{String(row.strategy_key || '').replace(/_/g, ' ')}</span>
                  <span style={{ ...MONO, fontSize: 8, color: '#14b8a6', marginLeft: 'auto' }}>+{fmtFixed(row.avg_max_return_pct, 1)}%</span>
                  <span style={{ ...MONO, fontSize: 7, color: '#7f95a8' }}>{row.entered_n ?? 0}/{row.sample_n ?? 0}</span>
                </div>
              ))}
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
              <span style={{ ...MONO, fontSize: 7, color: '#60a5fa', fontWeight: 900 }}>POLICY BRIDGE</span>
              {(shadowLab.policy_bridge?.actions ?? ['Keep shadow lab paper-only while samples build.']).slice(0, 3).map(action => (
                <span key={action} style={{ ...MONO, fontSize: 8, color: '#d7e1ea', lineHeight: 1.45 }}>{action}</span>
              ))}
              <span style={{ ...MONO, fontSize: 7, color: '#7f95a8', lineHeight: 1.45 }}>
                {shadowLab.policy_bridge?.live_policy || 'No live policy changes from shadow strategies.'}
              </span>
            </div>
          </div>
        </div>
      )}

      {opportunityLedger && (
        <div style={{
          background: 'rgba(15,23,42,0.46)',
          border: '1px solid rgba(45,212,191,0.16)',
          borderTop: '2px solid rgba(45,212,191,0.72)',
          borderRadius: 12,
          padding: 11,
          display: 'flex',
          flexDirection: 'column',
          gap: 9,
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
            <span style={{ ...MONO, fontSize: 8, color: '#2dd4bf', fontWeight: 900, letterSpacing: '0.14em' }}>
              ACTIVE RESEARCH BOARD
            </span>
            <span className="badge" style={{ color: '#2dd4bf', background: 'rgba(45,212,191,0.10)', border: '1px solid rgba(45,212,191,0.24)', fontSize: 7 }}>
              LEDGER
            </span>
            <span style={{ ...MONO, fontSize: 8, color: '#7f95a8' }}>
              {opportunityLedger.summary?.total ?? 0} tracked · {opportunityLedger.summary?.paper_ready ?? 0} paper · {opportunityLedger.summary?.blocked_improving ?? 0} blocked · {opportunityLedger.summary?.missed_runner ?? 0} missed
            </span>
          </div>
          <span style={{ ...MONO, fontSize: 8, color: '#9fb3c8', lineHeight: 1.45 }}>
            {opportunityLedger.headline || 'Tracks what is improving, what is blocked, and what the system needs to learn from.'}
          </span>
          {ledgerLessons && (
            <div style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(auto-fit, minmax(190px, 1fr))',
              gap: 7,
              background: 'rgba(2,6,23,0.38)',
              border: '1px solid rgba(148,163,184,0.12)',
              borderRadius: 10,
              padding: 8,
            }}>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
                <span style={{ ...MONO, fontSize: 7, color: '#00d48a', fontWeight: 900 }}>BEST BLOCKER</span>
                <span style={{ ...MONO, fontSize: 8, color: '#d7e1ea' }}>
                  {ledgerLessons.best_blocker?.blocker ? String(ledgerLessons.best_blocker.blocker).replace(/_/g, ' ') : 'learning'}
                </span>
                <span style={{ ...MONO, fontSize: 7, color: '#7f95a8' }}>
                  {fmtFixed(ledgerLessons.best_blocker?.accuracy_pct, 0)}% accurate · {ledgerLessons.best_blocker?.sample_n ?? 0} sample
                </span>
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
                <span style={{ ...MONO, fontSize: 7, color: '#f59e0b', fontWeight: 900 }}>WORST BLOCKER</span>
                <span style={{ ...MONO, fontSize: 8, color: '#d7e1ea' }}>
                  {ledgerLessons.worst_blocker?.blocker ? String(ledgerLessons.worst_blocker.blocker).replace(/_/g, ' ') : 'none yet'}
                </span>
                <span style={{ ...MONO, fontSize: 7, color: '#7f95a8' }}>
                  {ledgerLessons.worst_blocker?.bad_n ?? 0} bad · avg max +{fmtFixed(ledgerLessons.worst_blocker?.avg_max_return_pct, 0)}%
                </span>
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
                <span style={{ ...MONO, fontSize: 7, color: '#ef4444', fontWeight: 900 }}>MISSED WINNER</span>
                <span style={{ ...MONO, fontSize: 8, color: '#d7e1ea' }}>
                  {ledgerLessons.missed_winner?.symbol || 'none logged'}
                </span>
                <span style={{ ...MONO, fontSize: 7, color: '#7f95a8' }}>
                  watch +{fmtFixed(ledgerLessons.missed_winner?.max_return_from_watch_pct ?? ledgerLessons.missed_winner?.max_observed_return_pct, 0)}%
                </span>
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
                <span style={{ ...MONO, fontSize: 7, color: '#60a5fa', fontWeight: 900 }}>CORRECT AVOID</span>
                <span style={{ ...MONO, fontSize: 8, color: '#d7e1ea' }}>
                  {ledgerLessons.correctly_avoided?.symbol || 'none logged'}
                </span>
                <span style={{ ...MONO, fontSize: 7, color: '#7f95a8' }}>
                  24h {fmtFixed(ledgerLessons.correctly_avoided?.return_24h_pct, 0)}% · {ledgerLessons.correctly_avoided?.outcome_label || 'tracking'}
                </span>
              </div>
            </div>
          )}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(235px, 1fr))', gap: 9 }}>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 7 }}>
              <span style={{ ...MONO, fontSize: 8, color: '#00d48a', fontWeight: 900, letterSpacing: '0.12em' }}>READY SOON</span>
              {readySoon.length ? readySoon.slice(0, 3).map(item => (
                <OpportunityCard key={`ready-${item.mint}-${item.state}`} item={item} />
              )) : (
                <span style={{ ...MONO, fontSize: 8, color: '#6f8498' }}>No clean ready-soon names right now.</span>
              )}
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 7 }}>
              <span style={{ ...MONO, fontSize: 8, color: '#f59e0b', fontWeight: 900, letterSpacing: '0.12em' }}>BLOCKED BUT IMPROVING</span>
              {blockedImproving.length ? blockedImproving.slice(0, 3).map(item => (
                <OpportunityCard key={`blocked-${item.mint}-${item.state}`} item={item} compact />
              )) : (
                <span style={{ ...MONO, fontSize: 8, color: '#6f8498' }}>No last-blocker setup is improving enough yet.</span>
              )}
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 7 }}>
              <span style={{ ...MONO, fontSize: 8, color: '#ef4444', fontWeight: 900, letterSpacing: '0.12em' }}>MISSED / LEARN</span>
              {missedOrLearn.length ? missedOrLearn.slice(0, 3).map(item => (
                <OpportunityCard key={`missed-${item.mint}-${item.state}`} item={item} compact />
              )) : (
                <span style={{ ...MONO, fontSize: 8, color: '#6f8498' }}>No fresh missed-runner lesson logged.</span>
              )}
            </div>
          </div>
        </div>
      )}

      {establishedWatch.length > 0 && (
        <div style={{
          background: 'rgba(245,158,11,0.045)',
          border: '1px solid rgba(245,158,11,0.16)',
          borderRadius: 11,
          padding: 10,
          display: 'flex',
          flexDirection: 'column',
          gap: 8,
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
            <span style={{ ...MONO, fontSize: 8, color: '#f59e0b', fontWeight: 900, letterSpacing: '0.14em' }}>
              ESTABLISHED RUNNER WATCHLIST
            </span>
            <span style={{ ...MONO, fontSize: 8, color: '#7f95a8' }}>
              {data?.established_runner_watchlist?.summary?.total ?? establishedWatch.length} tracked · {data?.established_runner_watchlist?.summary?.paper_ready ?? 0} paper · {data?.established_runner_watchlist?.summary?.research_hot ?? 0} hot · {data?.established_runner_watchlist?.summary?.last_blocker ?? 0} blocker
            </span>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 7 }}>
            {establishedWatch.slice(0, 6).map(item => {
              const tone = executionTone(item.execution_alignment_state || item.lane_state)
              return (
                <div key={`${item.mint}-${item.lane_state}`} style={{
                  background: `${tone}08`,
                  border: `1px solid ${tone}20`,
                  borderRadius: 9,
                  padding: 8,
                  display: 'flex',
                  flexDirection: 'column',
                  gap: 5,
                }}>
                  <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
                    <span style={{ ...MONO, fontSize: 10, color: '#f3f7fb', fontWeight: 900 }}>{item.symbol || 'TOKEN'}</span>
                    <span style={{ ...MONO, fontSize: 7, color: tone, fontWeight: 900 }}>
                      {String(item.lane_state || 'WATCH').replace(/_/g, ' ')}
                    </span>
                    <span style={{ ...MONO, fontSize: 8, color: '#8ca0b3', marginLeft: 'auto' }}>score {fmtFixed(item.research_score, 0)}</span>
                  </div>
                  <TokenAddressChip value={item.mint || ''} />
                  <span style={{ ...MONO, fontSize: 8, color: '#9fb3c8', lineHeight: 1.4 }}>
                    {String(item.comparable_runner || 'runner').replace(/_/g, ' ')} · {String(item.attention_persistence_label || 'learning').replace(/_/g, ' ')} · {String(item.sustainability_label || 'unknown').replace(/_/g, ' ')}
                  </span>
                  <span style={{ ...MONO, fontSize: 8, color: tone, lineHeight: 1.4 }}>
                    blocker: {String(item.last_blocker || 'none').replace(/_/g, ' ')}
                  </span>
                </div>
              )
            })}
          </div>
        </div>
      )}

      {(entrySignals.length > 0 || entryTuning.length > 0) && (
        <div style={{
          display: 'grid',
          gridTemplateColumns: 'minmax(0, 1.15fr) minmax(240px, 0.85fr)',
          gap: 10,
          background: 'rgba(0,212,138,0.055)',
          border: '1px solid rgba(0,212,138,0.16)',
          borderRadius: 11,
          padding: 10,
        }}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 7 }}>
            <div style={{ display: 'flex', gap: 7, flexWrap: 'wrap', alignItems: 'center' }}>
              <span style={{ ...MONO, fontSize: 8, color: '#00d48a', fontWeight: 900, letterSpacing: '0.14em' }}>
                RECENT ENTRY SIGNALS
              </span>
              <span className="badge" style={{ color: '#00d48a', background: 'rgba(0,212,138,0.10)', border: '1px solid rgba(0,212,138,0.22)', fontSize: 7 }}>
                PAPER TRACKED
              </span>
              <span style={{ ...MONO, fontSize: 8, color: '#7f95a8' }}>
                {data?.entry_signals?.summary.entry_now ?? 0} entry · {data?.entry_signals?.summary.armed ?? 0} armed · {data?.entry_signals?.summary.open_paper ?? 0} open · {data?.entry_signals?.summary.judged_paper ?? 0} judged
              </span>
            </div>
            {entryOutcomeCalibration?.summary && (
              <div style={{
                display: 'grid',
                gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))',
                gap: 6,
                background: 'rgba(2,6,23,0.34)',
                border: '1px solid rgba(0,212,138,0.14)',
                borderRadius: 9,
                padding: 7,
              }}>
                <span style={{ ...MONO, fontSize: 8, color: '#d7e1ea', lineHeight: 1.35 }}>
                  closeout: {entryOutcomeCalibration.summary.judged_n ?? 0}/{entryOutcomeCalibration.summary.sample_n ?? 0} judged · WR {fmtFixed(entryOutcomeCalibration.summary.win_rate_pct, 0)}%
                </span>
                {(entryOutcomeCalibration.by_entry_zone ?? []).slice(0, 2).map(row => (
                  <span key={`zone-${row.label}`} style={{ ...MONO, fontSize: 8, color: entryZoneTone(row.label), lineHeight: 1.35 }}>
                    {String(row.label || 'zone').replace(/_/g, ' ')} · {row.judged_n ?? 0} judged · max {fmtFixed(row.avg_max_return_pct, 1)}%
                  </span>
                ))}
                {(entryOutcomeCalibration.by_position_stance ?? []).slice(0, 1).map(row => (
                  <span key={`stance-${row.label}`} style={{ ...MONO, fontSize: 8, color: '#2dd4bf', lineHeight: 1.35 }}>
                    {String(row.label || 'stance').replace(/_/g, ' ')} · WR {fmtFixed(row.win_rate_pct, 0)}% · {row.judged_n ?? 0} judged
                  </span>
                ))}
              </div>
            )}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(170px, 1fr))', gap: 7 }}>
              {entrySignals.slice(0, 4).map(sig => {
                const tone = entryTone(sig.entry_state)
                return (
                  <div key={sig.id} style={{
                    background: `${tone}08`,
                    border: `1px solid ${tone}20`,
                    borderRadius: 9,
                    padding: 8,
                    display: 'flex',
                    flexDirection: 'column',
                    gap: 4,
                  }}>
                    <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
                      <span style={{ ...MONO, fontSize: 9, color: '#f3f7fb', fontWeight: 900 }}>{sig.symbol || 'TOKEN'}</span>
                      <span style={{ ...MONO, fontSize: 7, color: tone, fontWeight: 900 }}>{sig.entry_state.replace(/_/g, ' ')}</span>
                      <span style={{ ...MONO, fontSize: 7, color: '#7f95a8', marginLeft: 'auto' }}>{fmtAge(sig.created_at)}</span>
                    </div>
                    <span style={{ ...MONO, fontSize: 8, color: '#9fb3c8', lineHeight: 1.35 }}>
                      score {fmtFixed(sig.entry_score, 0)} · guard {sig.guard_status || '—'} · blocker {(sig.last_blocker || 'none').replace(/_/g, ' ')}
                    </span>
                    <span style={{ ...MONO, fontSize: 8, color: Number(sig.max_return_pct ?? 0) >= 10 ? '#00d48a' : '#8ca0b3', lineHeight: 1.35 }}>
                      paper {sig.paper_status || '—'} · max {fmtFixed(sig.max_return_pct, 1)}% · dd {fmtFixed(sig.drawdown_from_max_pct, 1)}%
                    </span>
                  </div>
                )
              })}
            </div>
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6, justifyContent: 'center' }}>
            {entryTuning.slice(0, 3).map(row => {
              const tone = entryTone(row.entry_state)
              return (
                <span key={row.entry_state} style={{
                  ...MONO,
                  fontSize: 8,
                  color: tone,
                  background: `${tone}0f`,
                  border: `1px solid ${tone}24`,
                  borderRadius: 7,
                  padding: '5px 7px',
                  lineHeight: 1.35,
                }}>
                  {row.entry_state.replace(/_/g, ' ')} · {row.judged_n ?? 0}/{row.sample_n} judged · WR {fmtFixed(row.win_rate_pct, 0)}% · stale {row.stale_n ?? 0}
                </span>
              )
            })}
            {!entryTuning.length && (
              <span style={{ ...MONO, fontSize: 8, color: '#7f95a8' }}>waiting for entry signal samples</span>
            )}
          </div>
        </div>
      )}

      {calibration && (
        <div style={{
          display: 'grid',
          gridTemplateColumns: 'minmax(0, 1.3fr) minmax(240px, 0.9fr)',
          gap: 10,
          background: 'rgba(167,139,250,0.055)',
          border: '1px solid rgba(167,139,250,0.16)',
          borderRadius: 11,
          padding: 10,
        }}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
            <div style={{ display: 'flex', gap: 7, flexWrap: 'wrap', alignItems: 'center' }}>
              <span style={{ ...MONO, fontSize: 8, color: '#a78bfa', fontWeight: 900, letterSpacing: '0.14em' }}>
                CALIBRATION
              </span>
              <span className="badge" style={{ color: '#a78bfa', background: 'rgba(167,139,250,0.10)', border: '1px solid rgba(167,139,250,0.22)', fontSize: 7 }}>
                {calibration.state}
              </span>
              <span style={{ ...MONO, fontSize: 8, color: '#7f95a8' }}>
                {calibration.snapshot_sample_n} snapshots · {calibration.outcome_sample_n} outcomes
              </span>
            </div>
            <span style={{ ...MONO, fontSize: 8, color: '#b7c8d8', lineHeight: 1.45 }}>
              {calibration.headline}
            </span>
          </div>
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignContent: 'center', justifyContent: 'flex-end' }}>
            {calibration.outcomes.slice(0, 3).map(row => (
              <span key={row.conviction_band} style={{
                ...MONO,
                fontSize: 8,
                color: bandTone(row.conviction_band),
                background: `${bandTone(row.conviction_band)}0f`,
                border: `1px solid ${bandTone(row.conviction_band)}24`,
                borderRadius: 7,
                padding: '4px 7px',
              }}>
                {row.conviction_band.replace(/_/g, ' ')} {row.sample_n} · max {fmtFixed(row.avg_max_return_pct, 1)}%
              </span>
            ))}
            {!calibration.outcomes.length && (
              <span style={{ ...MONO, fontSize: 8, color: '#7f95a8' }}>
                waiting for closed paper outcomes
              </span>
            )}
          </div>
        </div>
      )}

      {catalysts.length > 0 && (
        <div style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(230px, 1fr))',
          gap: 8,
        }}>
          {catalysts.slice(0, 4).map((event, idx) => {
            const confidence = Number(event.confidence ?? 0)
            const tone = confidence >= 82 ? '#00d48a' : confidence >= 70 ? '#f59e0b' : '#60a5fa'
            return (
              <div key={`${event.id ?? idx}-${event.mint}-${event.event_type}`} style={{
                background: `${tone}08`,
                border: `1px solid ${tone}22`,
                borderRadius: 10,
                padding: 9,
                display: 'flex',
                flexDirection: 'column',
                gap: 5,
              }}>
                <div style={{ display: 'flex', gap: 7, alignItems: 'center', flexWrap: 'wrap' }}>
                  <span style={{ ...MONO, fontSize: 8, color: tone, fontWeight: 900 }}>{event.event_type.replace(/_/g, ' ')}</span>
                  <span style={{ ...MONO, fontSize: 9, color: '#f3f7fb', fontWeight: 900 }}>{event.symbol || 'TOKEN'}</span>
                  <span style={{ ...MONO, fontSize: 8, color: '#8ca0b3', marginLeft: 'auto' }}>{fmtAge(event.event_ts)}</span>
                </div>
                <span style={{ ...MONO, fontSize: 8, color: '#b7c8d8', lineHeight: 1.45 }}>
                  {event.headline || event.detail || 'Catalyst event recorded.'}
                </span>
                <span style={{ ...MONO, fontSize: 8, color: '#7f95a8' }}>
                  confidence {fmtFixed(confidence, 0)} · {String(event.source || 'system').replace(/_/g, ' ')}
                </span>
              </div>
            )
          })}
        </div>
      )}

      {rotations.length > 0 && (
        <div style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(190px, 1fr))',
          gap: 8,
        }}>
          {rotations.slice(0, 5).map(rot => {
            const tone = rot.state === 'HOT' ? '#00d48a' : rot.state === 'WARM' ? '#f59e0b' : '#60a5fa'
            return (
              <div key={rot.narrative} style={{
                background: `${tone}08`,
                border: `1px solid ${tone}20`,
                borderRadius: 10,
                padding: 9,
                display: 'flex',
                flexDirection: 'column',
                gap: 5,
              }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 7, flexWrap: 'wrap' }}>
                  <span style={{ ...MONO, fontSize: 8, color: tone, fontWeight: 900 }}>{rot.state}</span>
                  <span style={{ ...MONO, fontSize: 9, color: '#d7e1ea', fontWeight: 900 }}>{rot.narrative.replace(/_/g, ' ')}</span>
                  <span style={{ ...MONO, fontSize: 8, color: '#8ca0b3', marginLeft: 'auto' }}>{fmtFixed(rot.heat_score, 0)}</span>
                </div>
                <span style={{ ...MONO, fontSize: 8, color: '#8ca0b3', lineHeight: 1.4 }}>
                  {rot.count} names · {rot.paper_entry_count} paper · avg max {fmtFixed(rot.avg_max_return_pct, 1)}%
                </span>
                <span style={{ ...MONO, fontSize: 8, color: '#7f95a8', lineHeight: 1.4 }}>
                  {rot.top_symbols.slice(0, 3).map(s => s.symbol).join(' · ') || 'waiting'}
                </span>
              </div>
            )
          })}
        </div>
      )}

      {loading && !rows.length ? (
        <div style={{ ...MONO, fontSize: 9, color: '#7f95a8' }}>Loading research dossiers…</div>
      ) : rows.length ? (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: 10 }}>
          {rows.slice(0, 6).map(item => {
            const tone = actionTone(item.action)
            const cluster = item.wallet_cluster_intelligence
            const clusterLabel = cluster?.cluster_label || 'NO_CLUSTER_DATA'
            const clusterColor = clusterTone(clusterLabel, cluster?.distribution_risk_score)
            const entry = item.entry_timing
            const entryState = entry?.state || item.entry_state || 'WAIT'
            const entryColor = entryTone(entryState)
            const zone = item.entry_zone
            const thesis = item.trade_thesis
            const zoneColor = entryZoneTone(zone?.zone)
            const plan = item.position_plan
            const exitIntel = item.exit_intelligence
            const exitColor = exitTone(exitIntel?.state)
            const execution = item.execution_alignment
            const executionState = execution?.state || item.execution_alignment_state || 'WAIT'
            const executionColor = executionTone(executionState)
            const hooks = item.narrative_hooks ?? []
            const contradictions = item.contradictions ?? []
            const sustainChecks = item.sustainability_checks ?? []
            const attentionColor = intelTone(item.attention_persistence_label)
            const sustainColor = intelTone(item.sustainability_label)
            return (
              <div key={item.mint} style={{
                background: `${tone}08`,
                border: `1px solid ${tone}20`,
                borderLeft: `3px solid ${tone}`,
                borderRadius: '0 12px 12px 0',
                padding: 11,
                display: 'flex',
                flexDirection: 'column',
                gap: 8,
              }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 7, flexWrap: 'wrap' }}>
                  <span style={{ ...MONO, fontSize: 13, color: '#f3f7fb', fontWeight: 900 }}>{item.symbol}</span>
                  <span className="badge" style={{ color: tone, background: `${tone}12`, border: `1px solid ${tone}28`, fontSize: 7 }}>
                    {(item.conviction_band || item.action).replace(/_/g, ' ')}
                  </span>
                  <span style={{ ...MONO, fontSize: 8, color: '#9fb3c8' }}>score {fmtFixed(item.research_score, 0)}</span>
                  <span className="badge" style={{ color: entryColor, background: `${entryColor}12`, border: `1px solid ${entryColor}28`, fontSize: 7 }}>
                    {String(entryState).replace(/_/g, ' ')} {fmtFixed(entry?.score ?? item.entry_score, 0)}
                  </span>
                  <span className="badge" style={{ color: executionColor, background: `${executionColor}12`, border: `1px solid ${executionColor}28`, fontSize: 7 }}>
                    {String(execution?.label || item.execution_alignment_label || executionState).replace(/_/g, ' ')}
                  </span>
                  {item.signal_quality_tier && (
                    <span className="badge" style={{ color: '#a78bfa', background: 'rgba(167,139,250,0.10)', border: '1px solid rgba(167,139,250,0.24)', fontSize: 7 }}>
                      Q {String(item.signal_quality_tier).replace(/_/g, ' ')} {fmtFixed(item.signal_quality_score, 0)}
                    </span>
                  )}
                  <TokenAddressChip value={item.mint} />
                </div>
                <div style={{ display: 'flex', gap: 5, flexWrap: 'wrap' }}>
                  {[item.narrative, item.memory_type, item.archetype, item.rotation_state].filter(Boolean).slice(0, 4).map(tag => (
                    <span key={tag} style={{ ...MONO, fontSize: 7, color: '#8fb7dc', background: 'rgba(96,165,250,0.07)', border: '1px solid rgba(96,165,250,0.16)', borderRadius: 5, padding: '2px 6px' }}>
                      {String(tag).replace(/_/g, ' ')}
                    </span>
                  ))}
                </div>
                <span style={{ ...MONO, fontSize: 8, color: '#c5d5e3', lineHeight: 1.5 }}>
                  {thesis?.headline || item.system_thesis}
                </span>
                {(zone || thesis) && (
                  <div style={{
                    display: 'flex',
                    flexDirection: 'column',
                    gap: 5,
                    background: `${zoneColor}08`,
                    border: `1px solid ${zoneColor}22`,
                    borderRadius: 8,
                    padding: 8,
                  }}>
                    <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
                      <span style={{ ...MONO, fontSize: 7, color: zoneColor, fontWeight: 900 }}>
                        BUY DECISION
                      </span>
                      <span style={{ ...MONO, fontSize: 8, color: '#f3f7fb', fontWeight: 900 }}>
                        {String(zone?.label || thesis?.decision || 'WAIT').replace(/_/g, ' ')}
                      </span>
                      <span style={{ ...MONO, fontSize: 8, color: zoneColor, marginLeft: 'auto' }}>
                        {fmtFixed(zone?.confidence ?? thesis?.confidence, 0)}/100
                      </span>
                    </div>
                    <span style={{ ...MONO, fontSize: 8, color: '#d7e1ea', lineHeight: 1.45 }}>
                      {zone?.action || thesis?.why_now || 'No entry action yet.'}
                    </span>
                    <span style={{ ...MONO, fontSize: 8, color: '#9fb3c8', lineHeight: 1.45 }}>
                      ideal: {zone?.ideal_entry || thesis?.ideal_entry || 'learning'} · confirm: {zone?.confirmation_needed || thesis?.confirmation_needed || 'fresh flow'}
                    </span>
                    {(thesis?.invalidation || item.invalidation) && (
                      <span style={{ ...MONO, fontSize: 8, color: '#f59e0b', lineHeight: 1.45 }}>
                        invalidation: {thesis?.invalidation || item.invalidation}
                      </span>
                    )}
                  </div>
                )}
                {(plan || exitIntel) && (
                  <div style={{
                    display: 'grid',
                    gridTemplateColumns: 'repeat(auto-fit, minmax(190px, 1fr))',
                    gap: 7,
                  }}>
                    {plan && (
                      <div style={{
                        display: 'flex',
                        flexDirection: 'column',
                        gap: 5,
                        background: 'rgba(45,212,191,0.055)',
                        border: '1px solid rgba(45,212,191,0.18)',
                        borderRadius: 8,
                        padding: 8,
                      }}>
                        <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
                          <span style={{ ...MONO, fontSize: 7, color: '#2dd4bf', fontWeight: 900 }}>POSITION PLAN</span>
                          <span style={{ ...MONO, fontSize: 8, color: '#f3f7fb', fontWeight: 900 }}>
                            {String(plan.stance || 'WAIT').replace(/_/g, ' ')}
                          </span>
                          <span style={{ ...MONO, fontSize: 8, color: '#2dd4bf', marginLeft: 'auto' }}>
                            {fmtFixed(plan.size_units, 2)}u
                          </span>
                        </div>
                        <span style={{ ...MONO, fontSize: 8, color: '#d7e1ea', lineHeight: 1.45 }}>
                          {plan.starter || 'No starter until trigger clears.'}
                        </span>
                        <span style={{ ...MONO, fontSize: 8, color: '#9fb3c8', lineHeight: 1.45 }}>
                          {plan.trim_plan || 'Trim into confirmed expansion.'}
                        </span>
                        <span style={{ ...MONO, fontSize: 8, color: '#f59e0b', lineHeight: 1.45 }}>
                          {plan.invalid_stop || 'Invalidate on thesis break.'}
                        </span>
                      </div>
                    )}
                    {exitIntel && (
                      <div style={{
                        display: 'flex',
                        flexDirection: 'column',
                        gap: 5,
                        background: `${exitColor}08`,
                        border: `1px solid ${exitColor}20`,
                        borderRadius: 8,
                        padding: 8,
                      }}>
                        <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
                          <span style={{ ...MONO, fontSize: 7, color: exitColor, fontWeight: 900 }}>EXIT INTEL</span>
                          <span style={{ ...MONO, fontSize: 8, color: '#f3f7fb', fontWeight: 900 }}>
                            {String(exitIntel.state || 'MONITOR').replace(/_/g, ' ')}
                          </span>
                          <span style={{ ...MONO, fontSize: 8, color: exitColor, marginLeft: 'auto' }}>
                            {String(exitIntel.thesis_health_label || '—').replace(/_/g, ' ')} {fmtFixed(exitIntel.thesis_health_score, 0)}
                          </span>
                        </div>
                        <span style={{ ...MONO, fontSize: 8, color: '#d7e1ea', lineHeight: 1.45 }}>
                          {exitIntel.action || 'Monitor thesis health.'}
                        </span>
                        <span style={{ ...MONO, fontSize: 8, color: '#9fb3c8', lineHeight: 1.45 }}>
                          {exitIntel.protect_condition || exitIntel.hold_condition || 'Protect if flow fades.'}
                        </span>
                        <span style={{ ...MONO, fontSize: 8, color: exitColor, lineHeight: 1.45 }}>
                          {exitIntel.sell_condition || 'Sell/avoid if risk breaks.'}
                        </span>
                      </div>
                    )}
                  </div>
                )}
                {onManualDecision && (
                  <div style={{
                    display: 'flex',
                    flexDirection: 'column',
                    gap: 6,
                    background: 'rgba(20,184,166,0.045)',
                    border: '1px solid rgba(20,184,166,0.16)',
                    borderRadius: 8,
                    padding: 8,
                  }}>
                    <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
                      <span style={{ ...MONO, fontSize: 7, color: '#14b8a6', fontWeight: 900 }}>YOUR LABEL</span>
                      {item.manual_review_decision?.decision ? (
                        <span style={{ ...MONO, fontSize: 8, color: '#d7e1ea' }}>
                          last {String(item.manual_review_decision.decision).replace(/_/g, ' ')} · {item.manual_review_decision.outcome_label || 'tracking'}
                        </span>
                      ) : (
                        <span style={{ ...MONO, fontSize: 8, color: '#8ca0b3' }}>teach the system what you would do</span>
                      )}
                    </div>
                    <div style={{ display: 'flex', gap: 5, flexWrap: 'wrap' }}>
                      {(['BUY', 'WATCH', 'SKIP', 'TOO_LATE', 'BAD_CA', 'NEEDS_MORE_PROOF'] as MemecoinManualReviewDecision[]).map(decision => {
                        const dTone =
                          decision === 'BUY' ? '#00d48a'
                          : decision === 'WATCH' || decision === 'NEEDS_MORE_PROOF' ? '#60a5fa'
                          : decision === 'TOO_LATE' ? '#f59e0b'
                          : '#ef4444'
                        const pending = pendingManualMint === item.mint
                        return (
                          <button
                            key={decision}
                            type="button"
                            disabled={pending}
                            onClick={() => onManualDecision(item, decision)}
                            style={{
                              ...MONO,
                              fontSize: 7,
                              color: dTone,
                              background: `${dTone}10`,
                              border: `1px solid ${dTone}24`,
                              borderRadius: 6,
                              padding: '4px 7px',
                              cursor: pending ? 'wait' : 'pointer',
                              opacity: pending ? 0.55 : 1,
                            }}
                          >
                            {decision.replace(/_/g, ' ')}
                          </button>
                        )
                      })}
                    </div>
                  </div>
                )}
                <span style={{ ...MONO, fontSize: 8, color: '#9fb3c8', lineHeight: 1.45 }}>
                  why now: {item.why_now || item.catalyst}
                </span>
                {item.catalyst_type && (
                  <span style={{ ...MONO, fontSize: 8, color: '#8fb7dc', lineHeight: 1.45 }}>
                    catalyst: {item.catalyst_type.replace(/_/g, ' ')} · strength {item.catalyst_strength_label || '—'} {fmtFixed(item.catalyst_strength_score, 0)} · tuning {fmtFixed(item.outcome_tuning_bonus, 1)}
                  </span>
                )}
                {(hooks.length > 0 || item.attention_persistence_label || item.comparable_runner) && (
                  <div style={{
                    display: 'flex',
                    flexDirection: 'column',
                    gap: 5,
                    background: 'rgba(45,212,191,0.055)',
                    border: '1px solid rgba(45,212,191,0.16)',
                    borderRadius: 8,
                    padding: 8,
                  }}>
                    <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
                      <span style={{ ...MONO, fontSize: 7, color: '#2dd4bf', fontWeight: 900 }}>
                        NARRATIVE INTEL
                      </span>
                      <span style={{ ...MONO, fontSize: 8, color: attentionColor, fontWeight: 900 }}>
                        {String(item.attention_persistence_label || 'LEARNING').replace(/_/g, ' ')} {fmtFixed(item.attention_persistence_score, 0)}
                      </span>
                      <span style={{ ...MONO, fontSize: 8, color: sustainColor, marginLeft: 'auto' }}>
                        {String(item.sustainability_label || 'UNKNOWN').replace(/_/g, ' ')} {fmtFixed(item.sustainability_score, 0)}
                      </span>
                    </div>
                    <span style={{ ...MONO, fontSize: 8, color: '#b7c8d8', lineHeight: 1.45 }}>
                      {item.narrative_intelligence?.research_note || `Comparable: ${String(item.comparable_runner || 'unmapped').replace(/_/g, ' ')} (${fmtFixed(item.comparable_runner_confidence, 0)} conf).`}
                    </span>
                    <span style={{ ...MONO, fontSize: 8, color: '#8fb7dc', lineHeight: 1.45 }}>
                      hooks: {hooks.slice(0, 3).map(h => String(h.hook || '').replace(/_/g, ' ')).filter(Boolean).join(' · ') || 'learning'} · catalyst quality {String(item.catalyst_quality_label || '—').replace(/_/g, ' ')} {fmtFixed(item.catalyst_quality_score, 0)}
                    </span>
                    {(item.comparable_runner || sustainChecks.length > 0) && (
                      <span style={{ ...MONO, fontSize: 8, color: '#9fb3c8', lineHeight: 1.45 }}>
                        comparable: {String(item.comparable_runner || 'unmapped').replace(/_/g, ' ')} · checks {sustainChecks.filter(c => c.pass).length}/{sustainChecks.length || 0} passing
                      </span>
                    )}
                    {contradictions[0]?.detail && (
                      <span style={{ ...MONO, fontSize: 8, color: contradictions[0]?.severity === 'HIGH' ? '#ef4444' : '#f59e0b', lineHeight: 1.45 }}>
                        watch: {contradictions[0].detail}
                      </span>
                    )}
                  </div>
                )}
                <div style={{
                  display: 'flex',
                  flexDirection: 'column',
                  gap: 5,
                  background: `${executionColor}08`,
                  border: `1px solid ${executionColor}20`,
                  borderRadius: 8,
                  padding: 8,
                }}>
                  <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
                    <span style={{ ...MONO, fontSize: 7, color: executionColor, fontWeight: 900 }}>
                      EXECUTION TRUTH
                    </span>
                    <span style={{ ...MONO, fontSize: 8, color: '#f3f7fb', fontWeight: 900 }}>
                      {String(execution?.label || item.execution_alignment_label || executionState).replace(/_/g, ' ')}
                    </span>
                    <span style={{ ...MONO, fontSize: 8, color: executionColor, marginLeft: 'auto' }}>
                      {fmtFixed(execution?.confidence ?? item.execution_alignment_confidence, 0)}/100
                    </span>
                  </div>
                  <span style={{ ...MONO, fontSize: 8, color: '#b7c8d8', lineHeight: 1.45 }}>
                    {execution?.instruction || (item.execution_deployable ? 'Deployable now if operator risk rules agree.' : 'Research may be hot, but live deployment still needs proof and authority alignment.')}
                  </span>
                  {((execution?.blockers || item.execution_blockers || [])[0]) && (
                    <span style={{ ...MONO, fontSize: 8, color: executionColor, lineHeight: 1.45 }}>
                      execution blocker: {String((execution?.blockers || item.execution_blockers || [])[0]).replace(/_/g, ' ')}
                    </span>
                  )}
                </div>
                <div style={{
                  display: 'flex',
                  flexDirection: 'column',
                  gap: 5,
                  background: `${entryColor}08`,
                  border: `1px solid ${entryColor}20`,
                  borderRadius: 8,
                  padding: 8,
                }}>
                  <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
                    <span style={{ ...MONO, fontSize: 7, color: entryColor, fontWeight: 900 }}>
                      ENTRY TIMING
                    </span>
                    <span style={{ ...MONO, fontSize: 8, color: '#f3f7fb', fontWeight: 900 }}>
                      {String(entryState).replace(/_/g, ' ')}
                    </span>
                    <span style={{ ...MONO, fontSize: 8, color: entryColor, marginLeft: 'auto' }}>
                      {fmtFixed(entry?.score ?? item.entry_score, 0)}/100
                    </span>
                  </div>
                  <span style={{ ...MONO, fontSize: 8, color: '#b7c8d8', lineHeight: 1.45 }}>
                    {entry?.instruction || item.entry_instruction || 'Waiting for a cleaner entry trigger.'}
                  </span>
                  <span style={{ ...MONO, fontSize: 8, color: entryColor, lineHeight: 1.45 }}>
                    last blocker: {String(entry?.last_blocker || item.entry_blocker || 'none').replace(/_/g, ' ')}
                  </span>
                  {((entry?.reasons || item.entry_reasons || [])[0] || (entry?.blockers || item.entry_blockers || [])[0]) && (
                    <span style={{ ...MONO, fontSize: 8, color: '#9fb3c8', lineHeight: 1.45 }}>
                      {(entry?.reasons || item.entry_reasons || [])[0] || `blocked by ${(entry?.blockers || item.entry_blockers || [])[0]}`.replace(/_/g, ' ')}
                    </span>
                  )}
                </div>
                {cluster && clusterLabel !== 'NO_CLUSTER_DATA' && (
                  <div style={{
                    display: 'flex',
                    flexDirection: 'column',
                    gap: 5,
                    background: `${clusterColor}08`,
                    border: `1px solid ${clusterColor}22`,
                    borderRadius: 8,
                    padding: 8,
                  }}>
                    <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
                      <span style={{ ...MONO, fontSize: 7, color: clusterColor, fontWeight: 900 }}>
                        WALLET CLUSTER
                      </span>
                      <span style={{ ...MONO, fontSize: 8, color: '#f3f7fb', fontWeight: 900 }}>
                        {clusterLabel.replace(/_/g, ' ')}
                      </span>
                      <span style={{ ...MONO, fontSize: 8, color: '#9fb3c8' }}>
                        {String(cluster.accumulation_state || 'quiet').replace(/_/g, ' ')}
                      </span>
                      <span style={{ ...MONO, fontSize: 8, color: clusterColor, marginLeft: 'auto' }}>
                        {fmtFixed(cluster.insider_like_score, 0)}/100
                      </span>
                    </div>
                    <span style={{ ...MONO, fontSize: 8, color: '#9fb3c8', lineHeight: 1.45 }}>
                      early {cluster.early_buyer_count ?? 0} · repeat {cluster.repeat_operator_count ?? 0} · trust {fmtFixed(cluster.wallet_trust_score, 0)} · fund links {cluster.linked_wallet_count ?? 0} · distrib {fmtFixed(cluster.distribution_risk_score, 0)}
                    </span>
                    {(cluster.top_repeat_wallets?.length || cluster.attribution_summary?.sample_n) && (
                      <div style={{
                        display: 'grid',
                        gridTemplateColumns: 'repeat(auto-fit, minmax(120px, 1fr))',
                        gap: 5,
                      }}>
                        <span style={{ ...MONO, fontSize: 7, color: '#8fb7dc', background: 'rgba(96,165,250,0.07)', border: '1px solid rgba(96,165,250,0.16)', borderRadius: 6, padding: '4px 6px', lineHeight: 1.35 }}>
                          why: {(cluster.wallet_trust_label || 'learning').replace(/_/g, ' ')} · repeat score {fmtFixed(cluster.repeat_operator_score, 0)}
                        </span>
                        <span style={{ ...MONO, fontSize: 7, color: '#2dd4bf', background: 'rgba(45,212,191,0.07)', border: '1px solid rgba(45,212,191,0.16)', borderRadius: 6, padding: '4px 6px', lineHeight: 1.35 }}>
                          outcomes: {cluster.attribution_summary?.sample_n ?? 0} sample · {fmtFixed(cluster.attribution_summary?.runner_rate_pct, 0)}% runners
                        </span>
                        <span style={{ ...MONO, fontSize: 7, color: '#a78bfa', background: 'rgba(167,139,250,0.07)', border: '1px solid rgba(167,139,250,0.16)', borderRadius: 6, padding: '4px 6px', lineHeight: 1.35 }}>
                          top wallet: {cluster.top_repeat_wallets?.[0]?.trust_label?.replace(/_/g, ' ') || cluster.top_repeat_wallets?.[0]?.memory_label?.replace(/_/g, ' ') || 'thin history'} · {fmtFixed(cluster.top_repeat_wallets?.[0]?.best_max_return_pct, 0)}% best
                        </span>
                      </div>
                    )}
                    {cluster.latest_alert?.headline && (
                      <span style={{ ...MONO, fontSize: 8, color: '#f59e0b', lineHeight: 1.45 }}>
                        alert: {cluster.latest_alert.headline.replace(/_/g, ' ')} · {cluster.latest_alert.severity || 'WATCH'} · {cluster.latest_alert.detail || 'watch cluster flow'}
                      </span>
                    )}
                    {(cluster.reasons?.[0] || cluster.warnings?.[0]) && (
                      <span style={{ ...MONO, fontSize: 8, color: cluster.warnings?.length ? '#f59e0b' : '#8fb7dc', lineHeight: 1.45 }}>
                        {(cluster.warnings?.[0] || cluster.reasons?.[0] || '').replace(/_/g, ' ')}
                      </span>
                    )}
                  </div>
                )}
                <div style={{ display: 'flex', gap: 5, flexWrap: 'wrap' }}>
                  <span style={{ ...MONO, fontSize: 7, color: item.good_coin_status === 'PASS' ? '#00d48a' : item.good_coin_status === 'FAIL' ? '#ef4444' : '#f59e0b', background: 'rgba(255,255,255,0.035)', border: '1px solid rgba(255,255,255,0.08)', borderRadius: 5, padding: '2px 6px' }}>
                    good {item.good_coin_status || '—'} {fmtFixed(item.good_coin_score, 0)}
                  </span>
                  <span style={{ ...MONO, fontSize: 7, color: item.too_late_label === 'EXTENDED' ? '#ef4444' : item.too_late_label === 'ELEVATED' ? '#f59e0b' : '#00d48a', background: 'rgba(255,255,255,0.035)', border: '1px solid rgba(255,255,255,0.08)', borderRadius: 5, padding: '2px 6px' }}>
                    late {item.too_late_label || '—'} {fmtFixed(item.too_late_score, 0)}
                  </span>
                  <span style={{ ...MONO, fontSize: 7, color: '#a78bfa', background: 'rgba(167,139,250,0.08)', border: '1px solid rgba(167,139,250,0.18)', borderRadius: 5, padding: '2px 6px' }}>
                    priority {fmtFixed(item.operator_priority, 0)}
                  </span>
                </div>
                {item.last_catalyst_type && (
                  <span style={{ ...MONO, fontSize: 8, color: '#2dd4bf', lineHeight: 1.45 }}>
                    source event: {item.last_catalyst_type.replace(/_/g, ' ')} · {fmtFixed(item.last_catalyst_confidence, 0)} conf · {item.last_catalyst_ts ? fmtAge(item.last_catalyst_ts) : 'fresh'}
                  </span>
                )}
                <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                  <span style={{ ...MONO, fontSize: 8, color: '#8ca0b3' }}>mcap {fmtUsd(item.marketcap)}</span>
                  <span style={{ ...MONO, fontSize: 8, color: '#8ca0b3' }}>liq {fmtUsd(item.liquidity)}</span>
                  <span style={{ ...MONO, fontSize: 8, color: '#8ca0b3' }}>avg max {fmtFixed(item.avg_max_return_pct, 1)}%</span>
                  <span style={{ ...MONO, fontSize: 8, color: item.risk_score < 45 ? '#ef4444' : '#00d48a' }}>risk {fmtFixed(item.risk_score, 0)}</span>
                </div>
                <span style={{ ...MONO, fontSize: 8, color: '#7f95a8', lineHeight: 1.45 }}>
                  invalidation: {item.invalidation}
                </span>
              </div>
            )
          })}
        </div>
      ) : (
        <div style={{ ...MONO, fontSize: 9, color: '#7f95a8' }}>No research dossiers yet.</div>
      )}
    </div>
  )
}

function OperatorCommandHero({
  board,
  bestAction,
  analyst,
  summary,
  heat,
  freshness,
  audit,
}: {
  board: ActionBoardData | undefined
  bestAction: BestActionData | undefined
  analyst: AIAnalystData | undefined
  summary: HomeSummary | undefined
  heat: SpeculationHeatData | undefined
  freshness: { label: string; tone: string; updatedAt: string | null }
  audit: SystemAuditData | undefined
}) {
  const headline = board?.headline
  const top = headline?.top_candidate ?? board?.ready_now?.[0] ?? board?.best_blocked?.[0] ?? board?.watchlist?.[0] ?? null
  const goodBuyTop = board?.good_buy_board_v2?.buyable?.[0] ?? null
  const goodWaitTop = board?.good_buy_board_v2?.wait?.[0] ?? null
  const backendBuyDecision = board?.buy_decision_v1 ?? board?.good_buy_board_v2?.buy_decision_v1 ?? null
  const snapshotMeta = board?._snapshot
  const snapshotStale = String(snapshotMeta?.status || '').toUpperCase() === 'STALE'
  const strictNoBuyWarnings = new Set([
    'market_data_recent_not_live',
    'sell_pressure_watch',
    'weak_24h_trend',
    'liquidity_watch',
    'volume_watch',
    'risk_watch',
    'pressure_watch',
    'identity_asserted_not_live_confirmed',
  ])
  const localStrictBuyFailures = (() => {
    if (!goodBuyTop) return []
    const failures: string[] = []
    const metrics = goodBuyTop.metrics
    const data = goodBuyTop.data
    const ticket = goodBuyTop.execution_ticket
    const executionState = ticket?.execution_state || 'NO_TICKET'

    if (goodBuyTop.state !== 'BUYABLE') failures.push(`state_${goodBuyTop.state || 'unknown'}`)
    if (!ticket) failures.push('no_execution_ticket')
    if (executionState === 'AUTHORITY_BLOCKED') failures.push('authority_blocked')
    if (executionState === 'MARKET_BLOCKED') failures.push('market_blocked')
    if (data?.identity_status !== 'RESOLVED') failures.push(`identity_${data?.identity_status || 'unknown'}`)
    if (data?.data_freshness !== 'LIVE') failures.push(`freshness_${data?.data_freshness || 'unknown'}`)
    if (data?.data_confidence === 'LOW') failures.push('data_confidence_low')
    if ((metrics?.liquidity_usd ?? 0) < 150000) failures.push('liquidity_below_clean_buy')
    if ((metrics?.volume_24h_usd ?? 0) < 175000) failures.push('volume_below_clean_buy')
    if ((metrics?.quality_score ?? 0) < 80) failures.push('quality_below_clean_buy')
    if ((metrics?.risk_score ?? 0) < 75) failures.push('risk_below_clean_buy')
    if ((metrics?.pressure_score ?? 0) < 62) failures.push('pressure_below_clean_buy')
    if ((metrics?.change_1h_pct ?? 0) <= -6) failures.push('sharp_1h_drop')
    failures.push(...(goodBuyTop.blockers ?? []))
    failures.push(...(ticket?.blockers ?? []))
    for (const warning of goodBuyTop.warnings ?? []) {
      if (strictNoBuyWarnings.has(warning)) failures.push(warning)
    }
    return [...new Set(failures)]
  })()
  const strictBuyFailures = backendBuyDecision ? (backendBuyDecision.failures ?? []) : localStrictBuyFailures
  const strictBuyReady = backendBuyDecision ? Boolean(backendBuyDecision.is_buy_now) : Boolean(goodBuyTop && strictBuyFailures.length === 0)
  const bestBuySource = goodBuyTop
    ? 'GOOD_BUY_BOARD'
    : board?.ready_now?.[0]
      ? 'ACTION_BOARD'
      : goodWaitTop
        ? 'WAITLIST'
        : top
          ? 'ACTION_BOARD'
          : 'NONE'
  const readyCount = board?.summary?.ready_now_count ?? 0
  const blockedCount = board?.summary?.blocked_count ?? 0
  const hasReady = readyCount > 0 || Boolean(goodBuyTop)
  const isBlocked = !hasReady && Boolean(top && (top.blockers ?? []).length > 0)
  const bestSymbol = goodBuyTop?.symbol || goodWaitTop?.symbol || top?.symbol || bestAction?.asset || null
  const bestAddress = goodBuyTop?.mint || goodWaitTop?.mint || top?.token_address || bestAction?.token_address || null
  const bestLane = goodBuyTop?.lane || goodWaitTop?.lane || top?.system || bestAction?.arm || null
  const explicitBuy = strictBuyReady
  const waitButClose = !explicitBuy && Boolean(goodBuyTop || goodWaitTop || top)
  const stateColor = explicitBuy ? '#00d48a' : isBlocked ? '#f59e0b' : waitButClose ? '#60a5fa' : '#7f95a8'
  const stateLabel = explicitBuy
    ? 'Best Buy Ready'
    : goodBuyTop
      ? 'Good Coin Needs Confirmation'
      : isBlocked
        ? 'Best Idea Blocked'
        : waitButClose
          ? 'Wait For Trigger'
          : 'No Clean Buy'
  const laneColor = bestLane ? (LANE_COLOR[String(bestLane)] || '#60a5fa') : '#60a5fa'
  const buyVerdict = backendBuyDecision?.label || (explicitBuy
    ? 'BUY NOW'
    : goodBuyTop
      ? 'WAIT - NEEDS CONFIRMATION'
      : isBlocked
        ? 'WAIT - BLOCKED'
        : waitButClose
          ? 'WAIT - NOT BUYABLE YET'
          : 'NO BUY')
  const buyVerdictSubline = explicitBuy
    ? `${backendBuyDecision?.reason || goodBuyTop?.headline || 'This is the cleanest buy candidate on the board right now.'}`
    : goodBuyTop
      ? `${backendBuyDecision?.reason || `Good coin is visible, but strict buy checks still need: ${strictBuyFailures.slice(0, 4).map((x) => x.replace(/_/g, ' ')).join(', ') || 'fresh confirmation'}.`}`
      : isBlocked
        ? 'Do not buy yet. The best visible setup still has a blocker.'
        : waitButClose
          ? 'Keep it on screen, but wait for trigger/proof to clear.'
          : 'No coin currently passes the buy conditions.'
  const decisionLine = explicitBuy
    ? `BUY ${bestSymbol || ''}`.trim()
    : goodBuyTop
      ? `WAIT ${bestSymbol || ''}`.trim()
      : hasReady
        ? `${top?.action || bestAction?.action || 'ACT'} ${bestSymbol || ''}`.trim()
    : isBlocked
      ? `WAIT · ${bestSymbol || 'MEMECOINS'}`
      : bestAction?.verdict === 'DO_NOTHING'
        ? 'DO NOTHING'
        : `${bestAction?.verdict || 'WATCH'} ${bestSymbol || ''}`.trim()
  const primaryReason =
    goodBuyTop?.headline
    || headline?.note
    || bestAction?.reason
    || 'Live action feed is waiting on the next clean memecoin or spot signal.'
  const activeBlockers = strictBuyFailures.length
    ? strictBuyFailures
    : goodBuyTop?.blockers?.length
    ? goodBuyTop.blockers
    : goodWaitTop?.blockers?.length
      ? goodWaitTop.blockers
      : top?.blockers ?? []
  const unlockText =
    goodBuyTop?.execution_ticket?.entry?.instruction
    || goodWaitTop?.blockers?.[0]
    || goodWaitTop?.headline
    || top?.unlock_hint
    || (activeBlockers.length ? 'Wait for the listed blockers to clear before treating this as buyable.' : null)
    || analyst?.review_now?.[0]
    || 'Let the scanner, proof, and outcome layers keep collecting signal.'
  const buyStats = goodBuyTop
    ? {
        score: goodBuyTop.good_buy_score,
        marketcap: goodBuyTop.metrics?.marketcap_usd,
        price: goodBuyTop.metrics?.price_usd,
        pressure: goodBuyTop.metrics?.pressure_score,
        quality: goodBuyTop.metrics?.quality_score,
        risk: goodBuyTop.metrics?.risk_score,
        liquidity: goodBuyTop.metrics?.liquidity_usd,
        volume24h: goodBuyTop.metrics?.volume_24h_usd,
        change1h: goodBuyTop.metrics?.change_1h_pct,
        size: goodBuyTop.execution_ticket?.suggested_size_usd,
        maxSize: goodBuyTop.execution_ticket?.max_size_usd,
        action: goodBuyTop.execution_ticket?.suggested_action,
        executionState: goodBuyTop.execution_ticket?.execution_state,
        entryPrice: goodBuyTop.execution_ticket?.entry?.reference_price_usd,
        entryMarketcap: goodBuyTop.execution_ticket?.entry?.reference_marketcap_usd,
        maxChase: goodBuyTop.execution_ticket?.entry?.max_chase_price_usd,
        invalidation: goodBuyTop.execution_ticket?.invalidation?.instruction,
        invalidPrice: goodBuyTop.execution_ticket?.invalidation?.price_usd,
        pressureBelow: goodBuyTop.execution_ticket?.invalidation?.pressure_below,
        change1hBelow: goodBuyTop.execution_ticket?.invalidation?.change_1h_below_pct,
        tp1Price: goodBuyTop.execution_ticket?.take_profit?.tp1_price_usd,
        tp2Price: goodBuyTop.execution_ticket?.take_profit?.tp2_price_usd,
        tp1Marketcap: goodBuyTop.execution_ticket?.take_profit?.tp1_marketcap_usd,
        tp2Marketcap: goodBuyTop.execution_ticket?.take_profit?.tp2_marketcap_usd,
        runnerRule: goodBuyTop.execution_ticket?.take_profit?.runner_rule,
      }
    : null
  const heatColor =
    heat?.heat_state === 'OVERHEATED' ? '#ef4444'
    : heat?.heat_state === 'HOT' ? '#f59e0b'
    : heat?.heat_state === 'WARM' ? '#60a5fa'
    : '#00d48a'
  const dataConfidence = audit?.runtime?.data_confidence
  const dataColor =
    dataConfidence?.status === 'HIGH' ? '#00d48a'
    : dataConfidence?.status === 'MEDIUM' ? '#f59e0b'
    : dataConfidence?.status === 'LOW' ? '#ef4444'
    : '#7f95a8'

  const Stat = ({ label, value, tone, note }: { label: string; value: string; tone?: string; note?: string }) => (
    <div className="operator-stat-tile" style={{
      background: 'linear-gradient(180deg, rgba(255,255,255,0.045), rgba(255,255,255,0.018))',
      border: '1px solid rgba(255,255,255,0.07)',
      borderRadius: 10,
      padding: '10px 12px',
      minWidth: 0,
    }}>
      <div style={{ ...MONO, fontSize: 8, color: 'var(--dim)', letterSpacing: '0.12em', marginBottom: 5 }}>{label}</div>
      <div style={{ ...MONO, fontSize: 14, color: tone || '#d7e1ea', fontWeight: 800, letterSpacing: '0.03em' }}>{value}</div>
      {note && <div style={{ ...MONO, fontSize: 8, color: '#6f879d', marginTop: 5, lineHeight: 1.45 }}>{note}</div>}
    </div>
  )

  return (
    <div className="operator-command-hero" style={{
      position: 'relative',
      overflow: 'hidden',
      border: `1px solid ${stateColor}30`,
      borderTop: `2px solid ${stateColor}`,
      borderRadius: 18,
      padding: 18,
      background:
        `radial-gradient(circle at 18% 0%, ${stateColor}18 0%, transparent 34%),` +
        `radial-gradient(circle at 86% 12%, ${laneColor}14 0%, transparent 30%),` +
        'linear-gradient(135deg, rgba(7,12,22,0.92), rgba(3,7,13,0.78) 55%, rgba(3,7,13,0.92))',
      boxShadow: `0 18px 70px rgba(0,0,0,0.34), 0 0 0 1px ${stateColor}08 inset`,
      backdropFilter: 'blur(24px) saturate(160%)',
      WebkitBackdropFilter: 'blur(24px) saturate(160%)',
    }}>
      <div className="operator-hero-grid" style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1.55fr) minmax(320px, 0.9fr)', gap: 18 }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 14, minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
            <span style={{ ...MONO, fontSize: 8, color: stateColor, letterSpacing: '0.16em', fontWeight: 800 }}>
              COMMAND CENTER
            </span>
            <span className="badge" style={{ color: stateColor, background: `${stateColor}12`, border: `1px solid ${stateColor}30`, fontSize: 8 }}>
              {stateLabel}
            </span>
            <span className="badge" style={{ color: stateColor, background: `${stateColor}12`, border: `1px solid ${stateColor}30`, fontSize: 8 }}>
              {buyVerdict}
            </span>
            <span className="badge" style={{ color: '#60a5fa', background: 'rgba(96,165,250,0.10)', border: '1px solid rgba(96,165,250,0.24)', fontSize: 8 }}>
              {(bestAction?.focus_mode || 'MEMECOINS_SPOT').replace(/_/g, ' + ')}
            </span>
            {snapshotMeta && (
              <span className="badge" style={{
                color: snapshotStale ? '#f59e0b' : '#00d48a',
                background: snapshotStale ? 'rgba(245,158,11,0.10)' : 'rgba(0,212,138,0.08)',
                border: snapshotStale ? '1px solid rgba(245,158,11,0.24)' : '1px solid rgba(0,212,138,0.18)',
                fontSize: 8,
              }}>
                {snapshotStale ? 'snapshot stale' : 'snapshot fast'} {snapshotMeta.age_seconds != null ? `${Math.round(Number(snapshotMeta.age_seconds))}s` : ''}
              </span>
            )}
            <span style={{ ...MONO, fontSize: 8, color: '#7f95a8' }}>
              source {bestBuySource.toLowerCase().replace(/_/g, ' ')}
            </span>
            <span style={{ ...MONO, fontSize: 8, color: freshness.tone, marginLeft: 'auto' }}>
              {freshness.label} {freshness.updatedAt ? `· ${fmtAge(freshness.updatedAt)}` : ''}
            </span>
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            <div style={{
              ...MONO,
              fontSize: 28,
              lineHeight: 1.05,
              color: stateColor,
              fontWeight: 900,
              letterSpacing: '0.10em',
              textTransform: 'uppercase',
            }}>
              {decisionLine}
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
              <span style={{
                ...MONO,
                fontSize: 11,
                color: stateColor,
                background: `${stateColor}10`,
                border: `1px solid ${stateColor}28`,
                borderRadius: 8,
                padding: '6px 9px',
                fontWeight: 900,
                letterSpacing: '0.08em',
              }}>
                {buyVerdict}
              </span>
              {bestSymbol && (
                <span style={{ ...MONO, fontSize: 10, color: '#d7e1ea' }}>
                  best coin: <span style={{ color: '#f3f7fb', fontWeight: 900 }}>{bestSymbol}</span>
                </span>
              )}
              <TokenAddressChip value={bestAddress} size="roomy" />
            </div>
            <div style={{ ...MONO, fontSize: 10, color: '#d7e1ea', lineHeight: 1.6, maxWidth: 900 }}>
              {buyVerdictSubline}
            </div>
            {snapshotStale && (
              <div style={{
                ...MONO,
                fontSize: 9,
                color: '#f59e0b',
                background: 'rgba(245,158,11,0.07)',
                border: '1px solid rgba(245,158,11,0.20)',
                borderRadius: 8,
                padding: '7px 9px',
                lineHeight: 1.5,
                maxWidth: 900,
              }}>
                health guard: showing last good dashboard snapshot while the backend refreshes. Confirm the CA and live chart before acting.
              </div>
            )}
            {backendBuyDecision?.alert_rule && (
              <div style={{
                ...MONO,
                fontSize: 9,
                color: explicitBuy ? '#00d48a' : '#f59e0b',
                background: explicitBuy ? 'rgba(0,212,138,0.07)' : 'rgba(245,158,11,0.07)',
                border: explicitBuy ? '1px solid rgba(0,212,138,0.20)' : '1px solid rgba(245,158,11,0.20)',
                borderRadius: 8,
                padding: '7px 9px',
                lineHeight: 1.5,
                maxWidth: 900,
              }}>
                alert {backendBuyDecision.alert_rule.kind.replace(/_/g, ' ').toLowerCase()}: {backendBuyDecision.alert_rule.message}
              </div>
            )}
            <div style={{ ...MONO, fontSize: 9, color: '#9fb3c8', lineHeight: 1.55, maxWidth: 900 }}>
              {primaryReason}
            </div>
          </div>

          {(top || goodBuyTop || goodWaitTop) && (
            <div className="operator-setup-grid" style={{
              display: 'grid',
              gridTemplateColumns: 'minmax(0, 1fr) minmax(220px, 0.55fr)',
              gap: 12,
            }}>
              <div style={{
                background: 'rgba(0,0,0,0.24)',
                border: `1px solid ${laneColor}22`,
                borderRadius: 12,
                padding: 12,
                display: 'flex',
                flexDirection: 'column',
                gap: 9,
              }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                  <span style={{ ...MONO, fontSize: 8, color: laneColor, letterSpacing: '0.14em', fontWeight: 800 }}>
                    {explicitBuy ? 'BEST BUY' : 'TOP SETUP'}
                  </span>
                  <span style={{ ...MONO, fontSize: 13, color: '#f3f7fb', fontWeight: 800 }}>
                    {bestSymbol || top?.system}
                  </span>
                  <span className="badge" style={{ color: stateColor, background: `${stateColor}10`, border: `1px solid ${stateColor}25`, fontSize: 8 }}>
                    {explicitBuy ? goodBuyTop?.state : goodBuyTop ? 'NEEDS_CONFIRMATION' : top?.action_state || top?.state || 'WATCH'}
                  </span>
                  {top?.proof_state && (
                    <span className="badge" style={{ color: '#f59e0b', background: 'rgba(245,158,11,0.10)', border: '1px solid rgba(245,158,11,0.24)', fontSize: 8 }}>
                      {top.proof_state.replace(/_/g, ' ')}
                    </span>
                  )}
                  <TokenAddressChip value={bestAddress} size="roomy" />
                  <span style={{ ...MONO, fontSize: 8, color: '#7f95a8', marginLeft: 'auto' }}>
                    score {fmtFixed(goodBuyTop?.good_buy_score ?? top?.priority_score, 0)}
                  </span>
                </div>
                <div style={{ ...MONO, fontSize: 9, color: '#b6c7d8', lineHeight: 1.6 }}>
                  {goodBuyTop?.headline || goodWaitTop?.headline || top?.reason}
                </div>
                {buyStats && (
                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(120px, 1fr))', gap: 7 }}>
                    <span style={{ ...MONO, fontSize: 8, color: '#9fb3c8' }}>MC <span style={{ color: '#d7e1ea' }}>{fmtUsd(buyStats.marketcap)}</span></span>
                    <span style={{ ...MONO, fontSize: 8, color: '#9fb3c8' }}>price <span style={{ color: '#d7e1ea' }}>{fmtTokenPrice(buyStats.price)}</span></span>
                    <span style={{ ...MONO, fontSize: 8, color: '#9fb3c8' }}>quality <span style={{ color: '#00d48a' }}>{fmtFixed(buyStats.quality, 0)}</span></span>
                    <span style={{ ...MONO, fontSize: 8, color: '#9fb3c8' }}>pressure <span style={{ color: '#00d48a' }}>{fmtFixed(buyStats.pressure, 0)}</span></span>
                    <span style={{ ...MONO, fontSize: 8, color: '#9fb3c8' }}>risk <span style={{ color: (buyStats.risk ?? 0) >= 75 ? '#00d48a' : '#f59e0b' }}>{fmtFixed(buyStats.risk, 0)}</span></span>
                    <span style={{ ...MONO, fontSize: 8, color: '#9fb3c8' }}>liq <span style={{ color: '#d7e1ea' }}>{fmtUsd(buyStats.liquidity)}</span></span>
                    <span style={{ ...MONO, fontSize: 8, color: '#9fb3c8' }}>vol 24h <span style={{ color: '#d7e1ea' }}>{fmtUsd(buyStats.volume24h)}</span></span>
                    <span style={{ ...MONO, fontSize: 8, color: '#9fb3c8' }}>1h <span style={{ color: (buyStats.change1h ?? 0) >= 0 ? '#00d48a' : '#ff7777' }}>{fmtPct(buyStats.change1h)}</span></span>
                  </div>
                )}
                {buyStats?.size != null && (
                  <div style={{
                    background: 'rgba(0,212,138,0.06)',
                    border: '1px solid rgba(0,212,138,0.18)',
                    borderRadius: 8,
                    padding: 8,
                    display: 'flex',
                    gap: 8,
                    flexWrap: 'wrap',
                  }}>
                    <span style={{ ...MONO, fontSize: 8, color: '#00d48a', fontWeight: 900 }}>system size {fmtUsd(buyStats.size)}</span>
                    <span style={{ ...MONO, fontSize: 8, color: '#8ca0b3' }}>max {fmtUsd(buyStats.maxSize)}</span>
                    <span style={{ ...MONO, fontSize: 8, color: '#8ca0b3' }}>max chase {fmtTokenPrice(buyStats.maxChase)}</span>
                  </div>
                )}
                {buyStats?.executionState && (
                  <div style={{
                    background: explicitBuy ? 'rgba(0,212,138,0.055)' : 'rgba(96,165,250,0.055)',
                    border: explicitBuy ? '1px solid rgba(0,212,138,0.18)' : '1px solid rgba(96,165,250,0.18)',
                    borderRadius: 10,
                    padding: 10,
                    display: 'flex',
                    flexDirection: 'column',
                    gap: 7,
                  }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                      <span style={{ ...MONO, fontSize: 8, color: explicitBuy ? '#00d48a' : '#60a5fa', letterSpacing: '0.14em', fontWeight: 900 }}>
                        BUY PLAN
                      </span>
                      <span style={{ ...MONO, fontSize: 8, color: '#9fb3c8' }}>
                        {buyStats.executionState.replace(/_/g, ' ').toLowerCase()}
                      </span>
                      {buyStats.action && (
                        <span style={{ ...MONO, fontSize: 8, color: '#f3f7fb', marginLeft: 'auto' }}>
                          {buyStats.action.replace(/_/g, ' ').toLowerCase()}
                        </span>
                      )}
                    </div>
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(170px, 1fr))', gap: 7 }}>
                      <span style={{ ...MONO, fontSize: 8, color: '#9fb3c8' }}>entry ref <span style={{ color: '#d7e1ea' }}>{fmtTokenPrice(buyStats.entryPrice)}</span></span>
                      <span style={{ ...MONO, fontSize: 8, color: '#9fb3c8' }}>entry MC <span style={{ color: '#d7e1ea' }}>{fmtUsd(buyStats.entryMarketcap)}</span></span>
                      <span style={{ ...MONO, fontSize: 8, color: '#9fb3c8' }}>max chase <span style={{ color: '#f59e0b' }}>{fmtTokenPrice(buyStats.maxChase)}</span></span>
                      <span style={{ ...MONO, fontSize: 8, color: '#9fb3c8' }}>invalidate price <span style={{ color: '#ffb4b4' }}>{fmtTokenPrice(buyStats.invalidPrice)}</span></span>
                      <span style={{ ...MONO, fontSize: 8, color: '#9fb3c8' }}>pressure floor <span style={{ color: '#ffb4b4' }}>{fmtFixed(buyStats.pressureBelow, 0)}</span></span>
                      <span style={{ ...MONO, fontSize: 8, color: '#9fb3c8' }}>1h floor <span style={{ color: '#ffb4b4' }}>{fmtPct(buyStats.change1hBelow)}</span></span>
                      <span style={{ ...MONO, fontSize: 8, color: '#9fb3c8' }}>TP1 <span style={{ color: '#00d48a' }}>{fmtTokenPrice(buyStats.tp1Price)} / {fmtUsd(buyStats.tp1Marketcap)}</span></span>
                      <span style={{ ...MONO, fontSize: 8, color: '#9fb3c8' }}>TP2 <span style={{ color: '#00d48a' }}>{fmtTokenPrice(buyStats.tp2Price)} / {fmtUsd(buyStats.tp2Marketcap)}</span></span>
                    </div>
                    {buyStats.runnerRule && (
                      <span style={{ ...MONO, fontSize: 8, color: '#8ca0b3', lineHeight: 1.5 }}>
                        runner: {buyStats.runnerRule}
                      </span>
                    )}
                  </div>
                )}
                {activeBlockers.length > 0 && (
                  <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                    {activeBlockers.slice(0, 4).map((blocker, i) => (
                      <span key={i} style={{
                        ...MONO,
                        fontSize: 8,
                        color: '#ff7777',
                        background: 'rgba(239,68,68,0.10)',
                        border: '1px solid rgba(239,68,68,0.22)',
                        borderRadius: 5,
                        padding: '3px 7px',
                      }}>
                        {blocker}
                      </span>
                    ))}
                  </div>
                )}
              </div>
              <div style={{
                background: 'rgba(245,158,11,0.055)',
                border: '1px solid rgba(245,158,11,0.18)',
                borderRadius: 12,
                padding: 12,
                display: 'flex',
                flexDirection: 'column',
                gap: 7,
                justifyContent: 'center',
              }}>
                <span style={{ ...MONO, fontSize: 8, color: '#f59e0b', letterSpacing: '0.14em', fontWeight: 800 }}>
                  {explicitBuy ? 'BUY RULES' : 'WHAT UNLOCKS IT'}
                </span>
                <span style={{ ...MONO, fontSize: 9, color: '#d7c199', lineHeight: 1.65 }}>
                  {unlockText}
                </span>
                {buyStats?.invalidation && (
                  <span style={{ ...MONO, fontSize: 8, color: '#ffb4b4', lineHeight: 1.5 }}>
                    invalidate: {buyStats.invalidation}
                  </span>
                )}
                {strictBuyFailures.length > 0 && (
                  <span style={{ ...MONO, fontSize: 8, color: '#9fb3c8', lineHeight: 1.5 }}>
                    strict checks: {strictBuyFailures.slice(0, 5).map((x) => x.replace(/_/g, ' ')).join(' · ')}
                  </span>
                )}
                {buyStats?.runnerRule && (
                  <span style={{ ...MONO, fontSize: 8, color: '#8ca0b3', lineHeight: 1.5 }}>
                    runner rule: {buyStats.runnerRule}
                  </span>
                )}
              </div>
            </div>
          )}
        </div>

        <div className="operator-stats-grid" style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(2, minmax(0, 1fr))',
          gap: 10,
          alignContent: 'start',
        }}>
          <Stat
            label="Ready"
            value={`${readyCount}`}
            tone={readyCount > 0 ? '#00d48a' : '#506276'}
            note={readyCount > 0 ? 'buyable now' : 'none clean yet'}
          />
          <Stat
            label="Blocked"
            value={`${blockedCount}`}
            tone={blockedCount > 0 ? '#f59e0b' : '#506276'}
            note={blockedCount > 0 ? 'strong ideas gated' : 'no blocked leaders'}
          />
          <Stat
            label="Data"
            value={dataConfidence?.status || '—'}
            tone={dataColor}
            note={dataConfidence?.issues?.[0] || dataConfidence?.headline || 'freshness confidence'}
          />
          <Stat
            label="Meme Progress"
            value={summary ? `${summary.memecoins.outcomes}/${summary.memecoins.next_milestone}` : '—'}
            tone="#a78bfa"
            note={summary?.memecoins.wr_pct != null ? `WR ${summary.memecoins.wr_pct}%` : 'learning sample building'}
          />
          <Stat
            label="Spot"
            value={summary ? `${summary.spot.signal_confidence?.toUpperCase?.() || '—'}` : '—'}
            tone={summary?.spot.signal_confidence === 'high' ? '#00d48a' : summary?.spot.signal_confidence === 'medium' ? '#f59e0b' : '#7f95a8'}
            note={summary ? `${summary.spot.holdings_count} holdings` : undefined}
          />
          <Stat
            label="Heat"
            value={heat ? `${heat.heat_state} ${fmtFixed(heat.heat_score, 0)}` : '—'}
            tone={heatColor}
            note={heat?.momentum ? `momentum ${heat.momentum.toLowerCase()}` : 'speculation context'}
          />
        </div>
      </div>
    </div>
  )
}

void [
  SystemCard,
  CapitalAllocationPanel,
  BestActionPanel,
  Metric,
  MiniBar,
  ReadinessSnapshot,
  DecisionJournalPanel,
]

// ── Main ──────────────────────────────────────────────────────────────────────

export function HomePage() {
  const queryClient = useQueryClient()
  const [pendingRunnerMint, setPendingRunnerMint] = React.useState<string | null>(null)
  const [pendingManualMint, setPendingManualMint] = React.useState<string | null>(null)
  const [pendingEscalationId, setPendingEscalationId] = React.useState<number | null>(null)
  const [pendingEscalationReviewKey, setPendingEscalationReviewKey] = React.useState<string | null>(null)
  const [pendingEscalationPatchKey, setPendingEscalationPatchKey] = React.useState<string | null>(null)
  const [pendingEscalationWorkOrderKey, setPendingEscalationWorkOrderKey] = React.useState<string | null>(null)
  const [pendingLiveContextMissionKey, setPendingLiveContextMissionKey] = React.useState<string | null>(null)
  const [homeQueryStage, setHomeQueryStage] = React.useState(0)

  React.useEffect(() => {
    setHomeQueryStage(0)
    const timers = [
      window.setTimeout(() => setHomeQueryStage(1), 900),
      window.setTimeout(() => setHomeQueryStage(2), 2200),
      window.setTimeout(() => setHomeQueryStage(3), 4200),
      window.setTimeout(() => setHomeQueryStage(4), 6500),
    ]
    return () => timers.forEach(timer => window.clearTimeout(timer))
  }, [])

  const bestActionQ = useQuery<BestActionData>({
    queryKey: ['home-best-action'],
    queryFn:  () => api.get('/home/best-action').then(r => r.data),
    retry: 1,
    refetchInterval: liveBudgetedInterval(45_000),
    staleTime: 20_000,
  })

  const summary = useQuery<HomeSummary>({
    queryKey: ['home-summary'],
    queryFn:  () => api.get('/home/summary').then(r => r.data),
    enabled: homeQueryStage >= 1,
    retry: 1,
    refetchInterval: liveBudgetedInterval(60_000),
    staleTime: 30_000,
  })

  // Action Board — comprehensive operator decision surface
  const actionBoardQ = useQuery<ActionBoardData>({
    queryKey: ['home-action-board'],
    queryFn:  () => api.get('/home/action-board').then(r => r.data),
    retry: 1,
    refetchInterval: liveBudgetedInterval(45_000),
    staleTime: 20_000,
  })

  const dailyBriefQ = useQuery<DailyCryptoBriefData>({
    queryKey: ['home-daily-crypto-brief'],
    queryFn: () => api.get('/home/daily-crypto-brief?lookback_hours=24').then(r => r.data),
    retry: 1,
    refetchInterval: slowBudgetedInterval(180_000),
    staleTime: 60_000,
  })

  const earlyRunnersQ = useQuery<EarlyRunnersData>({
    queryKey: ['home-early-runners'],
    queryFn: () => api.get('/home/early-runners?limit=8&lookback_hours=24').then(r => r.data),
    enabled: homeQueryStage >= 2,
    retry: 1,
    refetchInterval: liveBudgetedInterval(45_000),
    staleTime: 10_000,
  })

  const convictionRecoveryQ = useQuery<ConvictionRecoveryData>({
    queryKey: ['home-conviction-recovery'],
    queryFn: () => api.get('/home/conviction-recovery?limit=10').then(r => r.data),
    enabled: homeQueryStage >= 3,
    retry: 1,
    refetchInterval: slowBudgetedInterval(120_000),
    staleTime: 30_000,
  })

  const runnerReviewQ = useQuery<RunnerReviewData>({
    queryKey: ['home-runner-review'],
    queryFn: () => api.get('/home/runner-review?limit=8').then(r => r.data),
    enabled: homeQueryStage >= 2,
    retry: 1,
    refetchInterval: liveBudgetedInterval(45_000),
    staleTime: 15_000,
  })

  const memecoinResearchQ = useQuery<MemecoinResearchData>({
    queryKey: ['home-memecoin-research'],
    queryFn: () => api.get('/home/memecoin-research?limit=10').then(r => r.data),
    enabled: homeQueryStage >= 3,
    retry: 1,
    refetchInterval: slowBudgetedInterval(180_000),
    staleTime: 60_000,
  })

  const runnerDecisionMutation = useMutation({
    mutationFn: (payload: RunnerReviewDecisionPayload) =>
      api.post('/home/runner-review/decision', payload).then(r => r.data),
    onMutate: (payload) => {
      setPendingRunnerMint(payload.mint)
    },
    onSettled: async () => {
      setPendingRunnerMint(null)
      await queryClient.invalidateQueries({ queryKey: ['home-runner-review'] })
      await queryClient.invalidateQueries({ queryKey: ['home-memecoin-research'] })
      await queryClient.invalidateQueries({ queryKey: ['home-system-audit-confidence'] })
      await queryClient.invalidateQueries({ queryKey: ['home-action-board'] })
    },
  })

  const recordRunnerDecision = (item: RunnerReviewOpportunity, decision: RunnerReviewDecision) => {
    runnerDecisionMutation.mutate({
      ...item,
      decision,
      operator_note:
        decision === 'MANUAL_BUY' ? 'Operator marked as manual-buy candidate from Home runner review.'
        : decision === 'WATCH' ? 'Operator kept this runner on watch.'
        : decision === 'TOO_LATE' ? 'Operator marked runner as too extended or late.'
        : 'Operator passed on this runner.',
    })
  }

  const memecoinManualDecisionMutation = useMutation({
    mutationFn: (payload: MemecoinManualReviewDecisionPayload) =>
      api.post('/home/memecoin-research/decision', payload).then(r => r.data),
    onMutate: (payload) => {
      setPendingManualMint(payload.mint)
    },
    onSettled: async () => {
      setPendingManualMint(null)
      await queryClient.invalidateQueries({ queryKey: ['home-memecoin-research'] })
      await queryClient.invalidateQueries({ queryKey: ['home-action-board'] })
      await queryClient.invalidateQueries({ queryKey: ['home-system-audit-confidence'] })
    },
  })

  const recordMemecoinManualDecision = (item: MemecoinResearchDossier, decision: MemecoinManualReviewDecision) => {
    memecoinManualDecisionMutation.mutate({
      ...item,
      decision,
      source: 'home_memecoin_research',
      dossier: item,
      operator_note:
        decision === 'BUY' ? 'Operator labeled this research setup as a buy candidate.'
        : decision === 'WATCH' ? 'Operator kept this research setup on watch.'
        : decision === 'SKIP' ? 'Operator skipped this research setup.'
        : decision === 'TOO_LATE' ? 'Operator marked this setup as too late.'
        : decision === 'BAD_CA' ? 'Operator rejected this setup because the CA/token identity looked wrong.'
        : 'Operator wants more proof before acting.',
    })
  }

  const providerEscalationDecisionMutation = useMutation({
    mutationFn: (payload: {
      id?: number | null
      mint?: string | null
      lane?: string | null
      action: ProviderEscalationAction
      operator_note?: string
    }) => api.post('/home/provider-escalation/decision', payload).then(r => r.data),
    onMutate: (payload) => {
      setPendingEscalationId(payload.id ?? null)
    },
    onSettled: async () => {
      setPendingEscalationId(null)
      await queryClient.invalidateQueries({ queryKey: ['home-daily-crypto-brief'] })
      await queryClient.invalidateQueries({ queryKey: ['home-action-board'] })
      await queryClient.invalidateQueries({ queryKey: ['home-system-audit-confidence'] })
    },
  })

  const recordProviderEscalationDecision = (item: ProviderEscalationItem, action: ProviderEscalationAction) => {
    providerEscalationDecisionMutation.mutate({
      id: item.id,
      mint: item.mint,
      lane: item.lane,
      action,
      operator_note:
        action === 'FORCE_REFRESH' ? 'Operator requested a provider repair refresh from Daily Brief.'
        : action === 'KEEP_WATCHING' ? 'Operator kept this provider escalation on watch.'
        : 'Operator dismissed this provider escalation from Daily Brief.',
    })
  }

  const providerEscalationReviewMutation = useMutation({
    mutationFn: (payload: {
      group_key?: string
      state: ProviderEscalationReviewState
      operator_note?: string
    }) => api.post('/home/provider-escalation-review/decision', payload).then(r => r.data),
    onMutate: (payload) => {
      setPendingEscalationReviewKey(payload.group_key ?? null)
    },
    onSettled: async () => {
      setPendingEscalationReviewKey(null)
      await queryClient.invalidateQueries({ queryKey: ['home-daily-crypto-brief'] })
      await queryClient.invalidateQueries({ queryKey: ['home-action-board'] })
      await queryClient.invalidateQueries({ queryKey: ['home-system-audit-confidence'] })
    },
  })

  const recordProviderEscalationReviewDecision = (item: ProviderEscalationReviewGroup, state: ProviderEscalationReviewState) => {
    providerEscalationReviewMutation.mutate({
      group_key: item.group_key,
      state,
      operator_note:
        state === 'RULE_PATCH_NEEDED' ? 'Operator marked this escalation alert group for rule patch review.'
        : state === 'DATA_PATCH_NEEDED' ? 'Operator marked this escalation alert group for data/provider repair.'
        : state === 'FALSE_ALARM' ? 'Operator marked this escalation alert group as a false alarm.'
        : state === 'RESOLVED' ? 'Operator resolved this escalation alert group.'
        : 'Operator acknowledged this escalation alert group.',
    })
  }

  const providerEscalationPatchMutation = useMutation({
    mutationFn: (payload: {
      group_key?: string
      state: ProviderEscalationPatchState
      gate_status?: string | null
      operator_note?: string
    }) => api.post('/home/provider-escalation-patch/decision', payload).then(r => r.data),
    onMutate: (payload) => {
      setPendingEscalationPatchKey(payload.group_key ?? null)
    },
    onSettled: async () => {
      setPendingEscalationPatchKey(null)
      await queryClient.invalidateQueries({ queryKey: ['home-daily-crypto-brief'] })
      await queryClient.invalidateQueries({ queryKey: ['home-action-board'] })
      await queryClient.invalidateQueries({ queryKey: ['home-system-audit-confidence'] })
    },
  })

  const recordProviderEscalationPatchDecision = (item: ProviderEscalationPatchPlan, state: ProviderEscalationPatchState) => {
    providerEscalationPatchMutation.mutate({
      group_key: item.group_key,
      state,
      gate_status: item.gate_status,
      operator_note:
        state === 'READY_FOR_IMPLEMENTATION'
          ? 'Operator marked this simulated escalation patch plan ready for manual implementation work.'
          : 'Operator held this escalation patch plan for more evidence.',
    })
  }

  const providerEscalationWorkOrderMutation = useMutation({
    mutationFn: (payload: {
      group_key?: string
      state: ProviderEscalationWorkOrderState
      operator_note?: string
    }) => api.post('/home/provider-escalation-work-order/decision', payload).then(r => r.data),
    onMutate: (payload) => {
      setPendingEscalationWorkOrderKey(payload.group_key ?? null)
    },
    onSettled: async () => {
      setPendingEscalationWorkOrderKey(null)
      await queryClient.invalidateQueries({ queryKey: ['home-daily-crypto-brief'] })
      await queryClient.invalidateQueries({ queryKey: ['home-action-board'] })
      await queryClient.invalidateQueries({ queryKey: ['home-system-audit-confidence'] })
    },
  })

  const recordProviderEscalationWorkOrderDecision = (item: ProviderEscalationWorkOrder, state: ProviderEscalationWorkOrderState) => {
    providerEscalationWorkOrderMutation.mutate({
      group_key: item.group_key,
      state,
      operator_note:
        state === 'STARTED' ? 'Operator started this escalation implementation work order.'
        : state === 'BLOCKED' ? 'Operator blocked this escalation implementation work order.'
        : state === 'COMPLETE' ? 'Operator completed this escalation implementation work order.'
        : 'Operator reset this escalation implementation work order to ready.',
    })
  }

  const liveContextMissionMutation = useMutation({
    mutationFn: (payload: {
      mission_key?: string
      gap_key?: string
      group_key?: string | null
      symbol?: string | null
      mint?: string | null
      gap_type?: string | null
      scope?: string | null
      source?: string | null
      state: LiveContextMissionState
      operator_note?: string
    }) => api.post('/home/live-context-mission/decision', payload).then(r => r.data),
    onMutate: (payload) => {
      setPendingLiveContextMissionKey(payload.mission_key || payload.gap_key || payload.group_key || null)
    },
    onSettled: async () => {
      setPendingLiveContextMissionKey(null)
      await queryClient.invalidateQueries({ queryKey: ['home-daily-crypto-brief'] })
      await queryClient.invalidateQueries({ queryKey: ['home-action-board'] })
      await queryClient.invalidateQueries({ queryKey: ['home-system-audit-confidence'] })
    },
  })

  const recordLiveContextMissionDecision = (item: DailyTopMission | LiveOpportunityGap | LiveContextMissionOutcomeItem, state: LiveContextMissionState) => {
    const isMission = 'mission_type' in item || 'source_gap' in item
    const mission = isMission ? item as DailyTopMission : null
    const gap = isMission ? mission?.source_gap : item as LiveOpportunityGap
    const review = !isMission && 'mission_key' in item ? item as LiveContextMissionOutcomeItem : null
    const missionKey = (isMission ? mission?.group_key : gap?.gap_key) || gap?.gap_key || review?.mission_key || undefined
    liveContextMissionMutation.mutate({
      mission_key: missionKey,
      gap_key: gap?.gap_key,
      group_key: mission?.group_key,
      symbol: gap?.symbol ?? review?.symbol,
      mint: gap?.mint ?? review?.mint,
      gap_type: gap?.gap_type ?? review?.gap_type,
      scope: gap?.scope ?? review?.scope,
      source: gap?.source,
      state,
      operator_note:
        state === 'INVESTIGATING' ? 'Operator started live-context mission investigation.'
        : state === 'RESOLVED_COVERED' ? 'Operator marked live-context mission covered by Abrons.'
        : state === 'RESOLVED_IGNORED' ? 'Operator marked live-context mission ignored/out of scope.'
        : 'Operator marked live-context mission as needing more source identity.',
    })
  }

  // Confluence reinforcement — annotates MEMECOINS entries on the Action Board
  const confluenceReinQ = useQuery<{
    top_candidates: Array<{ symbol: string; reinforcement_level: string }>
  }>({
    queryKey: ['confluence-reinforcement'],
    queryFn:  () => api.get('/confluence/reinforcement').then(r => r.data),
    enabled: homeQueryStage >= 1,
    retry: 1,
    refetchInterval: liveBudgetedInterval(60_000),
    staleTime: 30_000,
  })

  // Scanner regime — annotates MEMECOINS entries on the Action Board
  const scannerDiagQ = useQuery<HomeScannerDiag>({
    queryKey:        ['scanner-diagnostics'],
    queryFn:         () => api.get('/memecoins/scanner-diagnostics').then(r => r.data),
    enabled:         homeQueryStage >= 3,
    retry:           1,
    refetchInterval: slowBudgetedInterval(120_000),
    staleTime:       60_000,
  })
  const speculationHeatQ = useQuery<SpeculationHeatData>({
    queryKey: ['home-speculation-heat'],
    queryFn: () => api.get('/home/speculation-heat').then(r => r.data),
    enabled: homeQueryStage >= 3,
    retry: 1,
    refetchInterval: slowBudgetedInterval(120_000),
    staleTime: 60_000,
  })
  const aiAnalystQ = useQuery<AIAnalystData>({
    queryKey: ['home-ai-analyst'],
    queryFn: () => api.get('/home/ai-analyst').then(r => r.data),
    enabled: homeQueryStage >= 4,
    retry: 1,
    refetchInterval: slowBudgetedInterval(300_000),
    staleTime: 120_000,
  })
  const systemAuditQ = useQuery<SystemAuditData>({
    queryKey: ['home-system-audit-confidence'],
    queryFn: () => api.get('/system/audit').then(r => r.data),
    enabled: homeQueryStage >= 4,
    retry: 1,
    refetchInterval: slowBudgetedInterval(120_000),
    staleTime: 30_000,
  })

  const s       = summary.data
  const watchToEntry =
    systemAuditQ.data?.watch_to_entry
    ?? systemAuditQ.data?.runtime?.memecoin_input?.watch_to_entry
  const watchToEntryReplay =
    systemAuditQ.data?.watch_to_entry_replay
    ?? systemAuditQ.data?.runtime?.memecoin_input?.watch_to_entry_replay
  const establishedRunnerCoverage =
    systemAuditQ.data?.established_runner_coverage
    ?? systemAuditQ.data?.runtime?.memecoin_input?.established_runner_coverage

  // Build symbol → reinforcement level map for Action Board
  const reinfBySymbol = new Map<string, string>()
  for (const cand of confluenceReinQ.data?.top_candidates ?? []) {
    reinfBySymbol.set(cand.symbol, cand.reinforcement_level)
  }

  const homeFreshness = freshnessState(
    [
      bestActionQ.data?.generated_at,
      summary.data?.generated_at ?? null,
      actionBoardQ.data?.generated_at ?? null,
      dailyBriefQ.data?.generated_at ?? null,
      earlyRunnersQ.data?.generated_at ?? null,
      convictionRecoveryQ.data?.generated_at ?? null,
      runnerReviewQ.data?.generated_at ?? null,
      memecoinResearchQ.data?.generated_at ?? null,
      speculationHeatQ.data?.generated_at ?? null,
      systemAuditQ.data?.generated_at ?? null,
    ],
    90_000,
    5 * 60_000,
  )

  return (
    <div className="home-page" style={{
      maxWidth: 1360, margin: '0 auto',
      padding: '20px 24px 40px',
      display: 'flex', flexDirection: 'column', gap: 18,
    }}>

      <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
        <span style={{ color: 'var(--text)', ...MONO, fontWeight: 700, fontSize: 11, letterSpacing: '0.16em' }}>
          HOME
        </span>
        <span style={{
          ...MONO,
          fontSize: 8,
          color: homeFreshness.tone,
          background: `${homeFreshness.tone}12`,
          border: `1px solid ${homeFreshness.tone}28`,
          borderRadius: 999,
          padding: '3px 8px',
          letterSpacing: '0.08em',
        }}>
          {homeFreshness.label}
        </span>
        <span style={{ color: 'var(--recessed)', ...MONO, fontSize: 9, letterSpacing: '0.04em' }}>
          memecoins + spot focus · what matters now · what changed
        </span>
        <span style={{ color: '#7f95a8', ...MONO, fontSize: 8, marginLeft: 'auto' }}>
          {homeFreshness.updatedAt ? `updated ${fmtAge(homeFreshness.updatedAt)}` : 'waiting on live reads'}
        </span>
      </div>

      <OperatorCommandHero
        board={actionBoardQ.data}
        bestAction={bestActionQ.data}
        analyst={aiAnalystQ.data}
        summary={s}
        heat={speculationHeatQ.data}
        freshness={homeFreshness}
        audit={systemAuditQ.data}
      />

      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        <span style={{ ...MONO, fontSize: 8, color: '#2dd4bf', letterSpacing: '0.16em', fontWeight: 900 }}>
          1 · DAILY CRYPTO BRIEF
        </span>
        <DailyCryptoBriefPanel
          data={dailyBriefQ.data}
          loading={dailyBriefQ.isLoading}
          onEscalationAction={recordProviderEscalationDecision}
          onEscalationReviewAction={recordProviderEscalationReviewDecision}
          onEscalationPatchAction={recordProviderEscalationPatchDecision}
          onEscalationWorkOrderAction={recordProviderEscalationWorkOrderDecision}
          onLiveContextMissionAction={recordLiveContextMissionDecision}
          pendingEscalationId={pendingEscalationId}
          pendingEscalationReviewKey={pendingEscalationReviewKey}
          pendingEscalationPatchKey={pendingEscalationPatchKey}
          pendingEscalationWorkOrderKey={pendingEscalationWorkOrderKey}
          pendingLiveContextMissionKey={pendingLiveContextMissionKey}
        />
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        <span style={{ ...MONO, fontSize: 8, color: '#00d48a', letterSpacing: '0.16em', fontWeight: 900 }}>
          2 · BEST BUYS
        </span>
        <GoodBuyBoardPanel board={actionBoardQ.data?.good_buy_board_v2} />
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        <span style={{ ...MONO, fontSize: 8, color: '#60a5fa', letterSpacing: '0.16em', fontWeight: 900 }}>
          3 · REASONING + CA REVIEW
        </span>
        <MemecoinResearchDossierPanel
          data={memecoinResearchQ.data}
          loading={memecoinResearchQ.isLoading}
          onManualDecision={recordMemecoinManualDecision}
          pendingManualMint={pendingManualMint}
        />
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        <span style={{ ...MONO, fontSize: 8, color: '#f59e0b', letterSpacing: '0.16em', fontWeight: 900 }}>
          4 · ENTRY TRIGGERS
        </span>
        <EntryWatchHomeStrip
          status={watchToEntry}
          replay={watchToEntryReplay}
          coverage={establishedRunnerCoverage}
        />
      </div>

      <EstablishedRunnerReviewPanel
        data={runnerReviewQ.data}
        loading={runnerReviewQ.isLoading}
        onDecision={recordRunnerDecision}
        pendingMint={pendingRunnerMint}
      />

      <div className="home-command-grid" style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) minmax(0, 1fr)', gap: 16 }}>
        <EarlyRunnerRadarPanel data={earlyRunnersQ.data} loading={earlyRunnersQ.isLoading} />
        <ConvictionRecoveryPanel data={convictionRecoveryQ.data} loading={convictionRecoveryQ.isLoading} />
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        <span style={{ ...MONO, fontSize: 8, color: '#8fb7dc', letterSpacing: '0.16em', fontWeight: 900 }}>
          5 · FULL QUEUE
        </span>
        <ActionBoardPanel data={actionBoardQ.data} loading={actionBoardQ.isLoading} scannerDiag={scannerDiagQ.data} reinfBySymbol={reinfBySymbol} />
      </div>

      <div className="home-command-grid" style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) minmax(0, 1fr)', gap: 16 }}>
        <AnalystMemoPanel data={aiAnalystQ.data} loading={aiAnalystQ.isLoading} />
        <HomeTalkTrack heat={speculationHeatQ.data} bestAction={bestActionQ.data} />
      </div>

      {/*
        Backend health, provider recovery, posture, and readiness surfaces still run
        through System/Audit. They are intentionally hidden from Home so this page
        stays focused on signals, CA, buy timing, and reasoning.
      */}

    </div>
  )
}
