import React from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../api'

// ── Types ─────────────────────────────────────────────────────────────────────

interface Candidate {
  symbol:             string
  score:              number
  action:             'ACT' | 'RESEARCH' | 'MONITOR'
  entry_window:       string
  fuel_quality:       string
  move_phase:         string
  lifecycle_state:    string
  n_windows:          number
  best_return_pct:    number
  move_type:          string
  research_priority:  'HIGH' | 'MEDIUM' | 'QUICK_GLANCE'
  research_budget:    string
  triage_tags:        string[]
  is_second_leg:      boolean
  vacc:               number
  first_leg_confirmed:  boolean
  multi_leg_confirmed:  boolean
  mcap_at_scan:         number | null   // Patch 281: exposed for operator mcap visibility
}

interface CandidatesResponse {
  candidates:       Candidate[]
  total:            number
  second_leg_count: number
  act_count:        number
  research_count:   number
  monitor_count:    number
  error?:           string
}

// ── Resolved decisions types ──────────────────────────────────────────────────

interface ResolvedDecision {
  id:                 number
  created_ts:         string
  symbol:             string
  operator_decision:  string
  resolution_status:  string
  verdict:            string | null
  outcome_24h_pct:    number | null
  resolved_ts:        string | null
  snapshot_json:      string | null
  reason:             string | null
}

// ── Pending decisions types ───────────────────────────────────────────────────

interface PendingDecision {
  id:                 number
  created_ts:         string
  symbol:             string
  recommended_action: string
  priority:           string
  reason:             string
  snapshot_json:      string
  operator_decision:  string
  operator_note:      string | null
  resolution_status:  string
}

interface PendingDecisionsResponse {
  decisions: PendingDecision[]
  total:     number
}

interface DecisionSnapshot {
  move_type?:         string
  fuel_quality?:      string
  entry_window?:      string
  score?:             number
  n_windows?:         number
  is_second_leg?:     number
}

// ── Trust state types ─────────────────────────────────────────────────────────

interface TrustPattern {
  label:            string
  type:             string
  win_rate_14d:     number
  win_rate_alltime: number
  delta_pp:         number
  n_14d:            number
}

interface OperatorAccuracy {
  followed_n:      number
  good_follow_pct: number | null
  skipped_n:       number
  good_skip_pct:   number | null
}

interface TrustStateResponse {
  system_confidence: 'NORMAL' | 'CAUTIOUS' | 'LOW' | 'INSUFFICIENT_DATA'
  confidence_reason: string
  working_lately:    TrustPattern[]
  failing_lately:    TrustPattern[]
  caution_flags:     string[]
  operator_accuracy: OperatorAccuracy
  review_notes:      string
  data_coverage: {
    total_resolved:      number
    recent_14d_resolved: number
  }
  error?: string
}

// ── Calibration types ─────────────────────────────────────────────────────────

interface CalibBucket {
  n:                     number
  win_rate_pct:          number
  avg_return_24h_pct?:   number
  median_return_24h_pct?: number
  thin:                  boolean
}

interface ActionTierBucket extends CalibBucket { action: string }
interface MoveTypeBucket   extends CalibBucket { move_type: string }
interface FuelWindowBucket extends CalibBucket { fuel_quality: string; entry_window: string }

interface CalibWindow {
  n:                    number
  overall_win_rate_pct: number
  action_tier:          ActionTierBucket[]
  move_type:            MoveTypeBucket[]
  fuel_window:          FuelWindowBucket[]
}

interface CalibrationResponse {
  total_resolved:    number
  data_window_start: string | null
  data_window_end:   string | null
  thin_threshold:    number
  all_time:          CalibWindow
  recent_14d:        CalibWindow
  error?:            string
}

// ── Constants ─────────────────────────────────────────────────────────────────

const AMBER  = '#f59e0b'
const BLUE   = '#60a5fa'
const GREEN  = '#00d48a'
const TEAL   = '#2dd4bf'
const DIM    = '#4d5a6e'
const MUTED  = '#6b7a8d'

// Action tier colors (mirrors HomePage ActionQueuePanel)
const ACTION_COLOR: Record<string, string> = {
  ACT:      AMBER,
  RESEARCH: BLUE,
  MONITOR:  DIM,
}

// Move type colors — match research priority
const BUDGET_COLOR: Record<string, string> = {
  HIGH:         AMBER,
  MEDIUM:       BLUE,
  QUICK_GLANCE: DIM,
}

// Entry window badge colors
const EW_COLOR: Record<string, string> = {
  OPEN:    GREEN,
  CLOSING: AMBER,
  CLOSED:  DIM,
}

// Fuel quality colors
const FQ_COLOR: Record<string, string> = {
  STRONG:   GREEN,
  MODERATE: AMBER,
  WEAK:     DIM,
  TRAP:     '#ef4444',
}

// ── Mcap helpers (Patch 281) ──────────────────────────────────────────────────

function fmtMcap(v: number | null | undefined): string {
  if (!v || v <= 0) return ''
  if (v >= 1_000_000) return `$${(v / 1_000_000).toFixed(1)}m`
  return `$${(v / 1_000).toFixed(0)}k`
}

// Band colors mirror Patch 277/279 modifier tiers
function mcapBandColor(v: number | null): string {
  if (!v || v <= 0) return DIM
  if (v < 1_500_000)  return '#ef4444'  // below scanner floor — -20 modifier
  if (v < 3_000_000)  return MUTED      // +7
  if (v < 10_000_000) return GREEN      // +15 peak band
  if (v < 25_000_000) return AMBER      // +5
  return DIM                             // +3 or +0
}

// ── Table helpers ─────────────────────────────────────────────────────────────

const TH: React.CSSProperties = {
  fontFamily: 'JetBrains Mono, monospace', fontSize: 8, fontWeight: 700,
  color: DIM, letterSpacing: '0.1em',
  padding: '6px 10px', textAlign: 'left',
  borderBottom: '1px solid rgba(255,255,255,0.06)',
  whiteSpace: 'nowrap',
}
const TD: React.CSSProperties = {
  fontFamily: 'JetBrains Mono, monospace', fontSize: 10, color: MUTED,
  padding: '7px 10px',
  borderBottom: '1px solid rgba(255,255,255,0.035)',
  whiteSpace: 'nowrap',
  verticalAlign: 'middle',
}

// ── Sub-components ────────────────────────────────────────────────────────────

function Badge({ label, color, bg }: { label: string; color: string; bg?: string }) {
  return (
    <span style={{
      fontFamily: 'JetBrains Mono, monospace', fontSize: 7, fontWeight: 700,
      letterSpacing: '0.08em', padding: '2px 5px', borderRadius: 3,
      color,
      background: bg ?? `${color}18`,
      border:     `1px solid ${color}44`,
      display: 'inline-block',
    }}>
      {label}
    </span>
  )
}

function StatPill({ label, value, color }: { label: string; value: string | number; color?: string }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-start', gap: 2 }}>
      <span style={{ fontFamily: 'JetBrains Mono, monospace', fontSize: 8, color: DIM, letterSpacing: '0.1em' }}>
        {label}
      </span>
      <span style={{ fontFamily: 'JetBrains Mono, monospace', fontSize: 16, fontWeight: 700, color: color ?? MUTED }}>
        {value}
      </span>
    </div>
  )
}

// ── Candidate row ─────────────────────────────────────────────────────────────

function CandidateRow({ c, idx }: { c: Candidate; idx: number }) {
  const actionColor  = ACTION_COLOR[c.action]  ?? DIM
  const budgetColor  = BUDGET_COLOR[c.research_priority] ?? DIM
  const ewColor      = EW_COLOR[c.entry_window] ?? DIM
  const fqColor      = FQ_COLOR[c.fuel_quality]  ?? DIM

  // Row tint: ACT rows get faint amber tint, second_leg gets faint teal tint
  const rowBg = c.is_second_leg
    ? 'rgba(45,212,191,0.03)'
    : c.action === 'ACT'
      ? 'rgba(245,158,11,0.03)'
      : 'transparent'

  return (
    <tr style={{ background: rowBg }}>
      {/* Rank */}
      <td style={{ ...TD, color: DIM, fontSize: 9, textAlign: 'right', paddingRight: 6 }}>
        {idx + 1}
      </td>

      {/* Symbol + second-leg badge */}
      <td style={{ ...TD, fontWeight: 700, fontSize: 12 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
          <span style={{ color: actionColor }}>{c.symbol}</span>
          {c.is_second_leg && (
            <Badge label="2ND LEG" color={TEAL} />
          )}
        </div>
      </td>

      {/* Action tier */}
      <td style={TD}>
        <Badge label={c.action} color={actionColor} />
      </td>

      {/* Move type */}
      <td style={{ ...TD, fontSize: 9 }}>
        <div style={{ color: budgetColor, fontWeight: 600 }}>
          {c.move_type.replace('_', ' ')}
        </div>
        <div style={{ color: DIM, fontSize: 7, marginTop: 2, letterSpacing: '0.04em' }}>
          {c.research_budget}
        </div>
      </td>

      {/* Entry window */}
      <td style={TD}>
        <span style={{ color: ewColor, fontWeight: 600, fontSize: 9 }}>
          {c.entry_window}
        </span>
      </td>

      {/* Fuel quality */}
      <td style={TD}>
        <span style={{ color: fqColor, fontWeight: 600, fontSize: 9 }}>
          {c.fuel_quality}
        </span>
      </td>

      {/* Move phase */}
      <td style={{ ...TD, fontSize: 9, color: MUTED }}>
        {c.move_phase}
      </td>

      {/* Windows + peak return */}
      <td style={{ ...TD, textAlign: 'right' }}>
        <span style={{ color: MUTED, fontSize: 10, fontWeight: 600 }}>{c.n_windows}w</span>
        {c.best_return_pct > 0 && (
          <span style={{ color: DIM, fontSize: 8, marginLeft: 6 }}>
            {c.best_return_pct > 0 ? '+' : ''}{c.best_return_pct.toFixed(0)}%
          </span>
        )}
      </td>

      {/* Score — Patch 281: mcap shown as sub-line so operator can verify mcap alignment */}
      <td style={{ ...TD, textAlign: 'right', fontWeight: 700, verticalAlign: 'middle' }}>
        <span style={{
          color: c.score >= 150 ? AMBER : c.score >= 100 ? BLUE : DIM,
          fontSize: 11,
        }}>
          {c.score}
        </span>
        {c.mcap_at_scan != null && fmtMcap(c.mcap_at_scan) && (
          <div style={{ color: mcapBandColor(c.mcap_at_scan), fontSize: 8, marginTop: 2, fontWeight: 400 }}>
            {fmtMcap(c.mcap_at_scan)}
          </div>
        )}
      </td>

      {/* Triage tags */}
      <td style={{ ...TD, maxWidth: 240 }}>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
          {c.triage_tags.map((t, i) => (
            <span key={i} style={{
              fontFamily: 'JetBrains Mono, monospace', fontSize: 8,
              color: '#3d5068', background: 'rgba(255,255,255,0.04)',
              border: '1px solid rgba(255,255,255,0.07)',
              borderRadius: 3, padding: '1px 5px',
            }}>
              {t}
            </span>
          ))}
        </div>
      </td>
    </tr>
  )
}

// ── Pending Decisions helpers ─────────────────────────────────────────────────

function ageStr(ts: string): string {
  const diff = Date.now() - new Date(ts.includes('T') ? ts : ts + 'Z').getTime()
  const h = Math.floor(diff / 3_600_000)
  if (h < 1) return `${Math.max(1, Math.floor(diff / 60_000))}m ago`
  return `${h}h ago`
}

function parseSnapshot(raw: string): DecisionSnapshot {
  try { return JSON.parse(raw) } catch { return {} }
}

// ── TrustStatePanel ───────────────────────────────────────────────────────────

const CONF_COLOR: Record<string, string> = {
  NORMAL:            '#00d48a',
  CAUTIOUS:          '#f59e0b',
  LOW:               '#ef4444',
  INSUFFICIENT_DATA: '#4d5a6e',
}

function TrustStatePanel() {
  const { data, isLoading } = useQuery<TrustStateResponse>({
    queryKey: ['research-trust-state'],
    queryFn:  () => api.get('/research/trust-state').then(r => r.data),
    refetchInterval: 120_000,
  })

  if (isLoading || !data) return null

  const conf      = data.system_confidence
  const confColor = CONF_COLOR[conf] ?? DIM
  const hasCoverage = (data.data_coverage?.total_resolved ?? 0) > 0

  return (
    <div className="glass-card" style={{
      border:        `1px solid ${confColor}33`,
      marginBottom:  12,
      padding:       '14px 18px',
    }}>

      {/* ── Header row ── */}
      <div style={{
        display: 'flex', alignItems: 'center',
        justifyContent: 'space-between', marginBottom: 8,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <span className="section-label" style={{ color: DIM }}>SYSTEM TRUST</span>
          <Badge label={conf.replace(/_/g, ' ')} color={confColor} />
        </div>
        <span style={{ fontFamily: 'JetBrains Mono, monospace', fontSize: 8, color: DIM }}>
          {data.data_coverage?.total_resolved ?? 0} resolved
          {' · '}
          {data.data_coverage?.recent_14d_resolved ?? 0} 14d
        </span>
      </div>

      {/* ── Confidence reason ── */}
      <div style={{
        fontFamily: 'JetBrains Mono, monospace', fontSize: 9, color: MUTED,
        marginBottom: hasCoverage ? 8 : 4,
      }}>
        {data.confidence_reason}
      </div>

      {/* ── Working / failing patterns ── */}
      {(data.working_lately.length > 0 || data.failing_lately.length > 0) && (
        <div style={{ display: 'flex', gap: 24, marginBottom: 8, flexWrap: 'wrap' }}>
          {data.working_lately.length > 0 && (
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 6, flexWrap: 'wrap' }}>
              <span style={{
                fontFamily: 'JetBrains Mono, monospace', fontSize: 7, fontWeight: 700,
                color: GREEN, letterSpacing: '0.1em',
              }}>WORKING</span>
              {data.working_lately.map(p => (
                <span key={p.label} style={{
                  fontFamily: 'JetBrains Mono, monospace', fontSize: 8, color: GREEN,
                }}>
                  {p.label} {p.win_rate_14d.toFixed(0)}%
                  <span style={{ color: DIM }}> (+{p.delta_pp.toFixed(0)}pp)</span>
                </span>
              ))}
            </div>
          )}
          {data.failing_lately.length > 0 && (
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 6, flexWrap: 'wrap' }}>
              <span style={{
                fontFamily: 'JetBrains Mono, monospace', fontSize: 7, fontWeight: 700,
                color: '#ef4444', letterSpacing: '0.1em',
              }}>FAILING</span>
              {data.failing_lately.map(p => (
                <span key={p.label} style={{
                  fontFamily: 'JetBrains Mono, monospace', fontSize: 8, color: '#ef4444',
                }}>
                  {p.label} {p.win_rate_14d.toFixed(0)}%
                  <span style={{ color: DIM }}> ({p.delta_pp.toFixed(0)}pp)</span>
                </span>
              ))}
            </div>
          )}
        </div>
      )}

      {/* ── Caution flags ── */}
      {data.caution_flags.length > 0 && (
        <div style={{ display: 'flex', gap: 6, marginBottom: 8, flexWrap: 'wrap' }}>
          {data.caution_flags.map(flag => (
            <Badge key={flag} label={flag.replace(/_/g, ' ')} color="#ef4444" />
          ))}
        </div>
      )}

      {/* ── Review note ── */}
      <div style={{
        fontFamily: 'JetBrains Mono, monospace', fontSize: 8,
        color: MUTED, fontStyle: 'italic',
      }}>
        {data.review_notes}
      </div>

      {/* ── Operator accuracy ── */}
      {((data.operator_accuracy?.followed_n ?? 0) > 0 ||
        (data.operator_accuracy?.skipped_n  ?? 0) > 0) && (
        <div style={{
          marginTop: 8, paddingTop: 8,
          borderTop: '1px solid rgba(255,255,255,0.04)',
          display: 'flex', gap: 20, flexWrap: 'wrap',
        }}>
          {(data.operator_accuracy?.followed_n ?? 0) > 0 && (
            <span style={{ fontFamily: 'JetBrains Mono, monospace', fontSize: 8, color: DIM }}>
              FOLLOWED {data.operator_accuracy.followed_n}×
              <span style={{
                color: (data.operator_accuracy.good_follow_pct ?? 0) >= 50 ? GREEN : AMBER,
                marginLeft: 4,
              }}>
                {data.operator_accuracy.good_follow_pct?.toFixed(0) ?? '—'}% GOOD
              </span>
            </span>
          )}
          {(data.operator_accuracy?.skipped_n ?? 0) > 0 && (
            <span style={{ fontFamily: 'JetBrains Mono, monospace', fontSize: 8, color: DIM }}>
              SKIPPED {data.operator_accuracy.skipped_n}×
              <span style={{
                color: (data.operator_accuracy.good_skip_pct ?? 0) >= 50 ? GREEN : AMBER,
                marginLeft: 4,
              }}>
                {data.operator_accuracy.good_skip_pct?.toFixed(0) ?? '—'}% GOOD
              </span>
            </span>
          )}
        </div>
      )}
    </div>
  )
}

// ── PendingDecisionsPanel ─────────────────────────────────────────────────────

function PendingDecisionsPanel() {
  const qc = useQueryClient()
  const [overriding, setOverriding] = React.useState<Set<number>>(new Set())
  const [notes, setNotes]           = React.useState<Record<number, string>>({})
  const [busy, setBusy]             = React.useState<Record<number, boolean>>({})

  const q = useQuery<PendingDecisionsResponse>({
    queryKey:        ['research-pending-decisions'],
    queryFn:         () => api.get('/research/pending-decisions').then(r => r.data),
    refetchInterval: 30_000,
  })

  const decisions = q.data?.decisions ?? []

  // Don't render the panel at all if empty and not loading
  if (!q.isLoading && decisions.length === 0) return null

  const resolve = async (id: number, decision: string, note?: string) => {
    setBusy(prev => ({ ...prev, [id]: true }))
    try {
      const body: Record<string, string> = { operator_decision: decision }
      if (note) body.operator_note = note
      await api.patch(`/home/decision-journal/${id}`, body)
      qc.invalidateQueries({ queryKey: ['research-pending-decisions'] })
      // Clean up override state for this row
      setOverriding(prev => { const s = new Set(prev); s.delete(id); return s })
      setNotes(prev => { const n = { ...prev }; delete n[id]; return n })
    } catch { /* silent fail */ }
    setBusy(prev => { const n = { ...prev }; delete n[id]; return n })
  }

  const toggleOverride = (id: number) => {
    setOverriding(prev => {
      const s = new Set(prev)
      s.has(id) ? s.delete(id) : s.add(id)
      return s
    })
  }

  return (
    <div className="glass-card" style={{
      border:        `1px solid ${AMBER}22`,
      padding:       '12px 16px',
      marginBottom:  12,
    }}>
      {/* Header */}
      <div style={{
        display: 'flex', alignItems: 'baseline',
        justifyContent: 'space-between', marginBottom: 10,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span className="section-label" style={{ color: AMBER, fontSize: 9 }}>
            PENDING DECISIONS
          </span>
          {decisions.length > 0 && (
            <span style={{
              fontFamily: 'JetBrains Mono, monospace', fontSize: 8,
              background: `${AMBER}22`, border: `1px solid ${AMBER}44`,
              borderRadius: 10, padding: '1px 7px', color: AMBER,
            }}>
              {decisions.length}
            </span>
          )}
        </div>
        <span style={{ fontFamily: 'JetBrains Mono, monospace', fontSize: 8, color: DIM }}>
          ACT surfacings awaiting your decision
        </span>
      </div>

      {q.isLoading ? (
        <span style={{ fontFamily: 'JetBrains Mono, monospace', fontSize: 9, color: DIM }}>
          Loading…
        </span>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          {decisions.map(d => {
            const snap   = parseSnapshot(d.snapshot_json)
            const isBusy = busy[d.id]
            const isOverride = overriding.has(d.id)
            const fqColor = FQ_COLOR[snap.fuel_quality ?? ''] ?? DIM
            const ewColor = EW_COLOR[snap.entry_window  ?? ''] ?? DIM
            const moveLabel = (snap.move_type ?? '').replace(/_/g, ' ')

            return (
              <div key={d.id} style={{
                padding:      '8px 10px',
                background:   'rgba(245,158,11,0.04)',
                border:       `1px solid ${AMBER}1a`,
                borderRadius: 6,
              }}>
                {/* Row top: symbol + context + buttons */}
                <div style={{
                  display: 'flex', alignItems: 'center',
                  gap: 10, flexWrap: 'wrap',
                }}>
                  {/* Symbol + age */}
                  <div style={{ display: 'flex', alignItems: 'baseline', gap: 6, minWidth: 90 }}>
                    <span style={{
                      fontFamily: 'JetBrains Mono, monospace',
                      fontWeight: 700, fontSize: 13, color: AMBER,
                    }}>
                      {d.symbol}
                    </span>
                    <span style={{
                      fontFamily: 'JetBrains Mono, monospace', fontSize: 8, color: DIM,
                    }}>
                      {ageStr(d.created_ts)}
                    </span>
                  </div>

                  {/* Context badges */}
                  <div style={{ display: 'flex', gap: 5, alignItems: 'center', flex: 1 }}>
                    {snap.score != null && (
                      <span style={{
                        fontFamily: 'JetBrains Mono, monospace', fontSize: 8,
                        color: AMBER, fontWeight: 600,
                      }}>
                        {snap.score}
                      </span>
                    )}
                    {snap.fuel_quality && (
                      <Badge label={snap.fuel_quality} color={fqColor} />
                    )}
                    {snap.entry_window && (
                      <Badge label={snap.entry_window} color={ewColor} />
                    )}
                    {moveLabel && (
                      <span style={{
                        fontFamily: 'JetBrains Mono, monospace', fontSize: 8,
                        color: DIM,
                      }}>
                        {moveLabel}
                      </span>
                    )}
                    {snap.n_windows != null && snap.n_windows > 0 && (
                      <span style={{
                        fontFamily: 'JetBrains Mono, monospace', fontSize: 8, color: DIM,
                      }}>
                        {snap.n_windows}w
                      </span>
                    )}
                    {!!snap.is_second_leg && (
                      <Badge label="2ND LEG" color={TEAL} />
                    )}
                  </div>

                  {/* Action buttons */}
                  <div style={{ display: 'flex', gap: 5, alignItems: 'center' }}>
                    {(['FOLLOWED', 'SKIPPED'] as const).map(decision => (
                      <button
                        key={decision}
                        disabled={isBusy}
                        onClick={() => resolve(d.id, decision)}
                        style={{
                          fontFamily:    'JetBrains Mono, monospace',
                          fontSize:      8,
                          fontWeight:    700,
                          padding:       '3px 10px',
                          borderRadius:  4,
                          cursor:        isBusy ? 'default' : 'pointer',
                          opacity:       isBusy ? 0.5 : 1,
                          border:        `1px solid ${decision === 'FOLLOWED' ? GREEN + '55' : 'rgba(255,255,255,0.12)'}`,
                          background:    decision === 'FOLLOWED' ? `${GREEN}18` : 'transparent',
                          color:         decision === 'FOLLOWED' ? GREEN : DIM,
                          letterSpacing: '0.06em',
                        }}
                      >
                        {decision}
                      </button>
                    ))}
                    <button
                      disabled={isBusy}
                      onClick={() => toggleOverride(d.id)}
                      style={{
                        fontFamily:   'JetBrains Mono, monospace',
                        fontSize:     8,
                        fontWeight:   700,
                        padding:      '3px 10px',
                        borderRadius: 4,
                        cursor:       isBusy ? 'default' : 'pointer',
                        opacity:      isBusy ? 0.5 : 1,
                        border:       `1px solid ${isOverride ? AMBER + '55' : 'rgba(255,255,255,0.12)'}`,
                        background:   isOverride ? `${AMBER}18` : 'transparent',
                        color:        isOverride ? AMBER : DIM,
                        letterSpacing:'0.06em',
                      }}
                    >
                      OVERRIDE
                    </button>
                  </div>
                </div>

                {/* Reason line */}
                {d.reason && (
                  <div style={{
                    marginTop: 4,
                    fontFamily: 'JetBrains Mono, monospace',
                    fontSize: 8, color: DIM,
                    overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                    maxWidth: 700,
                  }}>
                    {d.reason}
                  </div>
                )}

                {/* Inline override note */}
                {isOverride && (
                  <div style={{ marginTop: 7, display: 'flex', gap: 6, alignItems: 'center' }}>
                    <input
                      placeholder="Override reason (optional)…"
                      value={notes[d.id] ?? ''}
                      onChange={e => setNotes(prev => ({ ...prev, [d.id]: e.target.value }))}
                      style={{
                        fontFamily:  'JetBrains Mono, monospace',
                        fontSize:    9,
                        flex:        1,
                        maxWidth:    320,
                        background:  'rgba(255,255,255,0.04)',
                        border:      `1px solid ${AMBER}33`,
                        borderRadius: 4,
                        color:       MUTED,
                        padding:     '4px 8px',
                        outline:     'none',
                      }}
                    />
                    <button
                      disabled={isBusy}
                      onClick={() => resolve(d.id, 'OVERRIDDEN', notes[d.id])}
                      style={{
                        fontFamily:   'JetBrains Mono, monospace',
                        fontSize:     8,
                        fontWeight:   700,
                        padding:      '4px 12px',
                        borderRadius: 4,
                        cursor:       isBusy ? 'default' : 'pointer',
                        opacity:      isBusy ? 0.5 : 1,
                        border:       `1px solid ${AMBER}55`,
                        background:   `${AMBER}22`,
                        color:        AMBER,
                        letterSpacing:'0.06em',
                      }}
                    >
                      SUBMIT
                    </button>
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

// ── ResolvedDecisionsPanel ────────────────────────────────────────────────────

const RED = '#ef4444'

const VERDICT_COLOR: Record<string, string> = {
  GOOD_FOLLOW:   '#00d48a',
  GOOD_SKIP:     '#00d48a',
  BAD_FOLLOW:    '#ef4444',
  BAD_SKIP:      '#ef4444',
  GOOD_OVERRIDE: '#00d48a',
  BAD_OVERRIDE:  '#ef4444',
}

const VERDICT_LABEL: Record<string, string> = {
  GOOD_FOLLOW:   'GOOD FOLLOW',
  GOOD_SKIP:     'GOOD SKIP',
  BAD_FOLLOW:    'BAD FOLLOW',
  BAD_SKIP:      'BAD SKIP',
  GOOD_OVERRIDE: 'GOOD OVERRIDE',
  BAD_OVERRIDE:  'BAD OVERRIDE',
}

function ResolvedDecisionsPanel() {
  const [expanded, setExpanded] = React.useState<Set<number>>(new Set())

  const q = useQuery<ResolvedDecision[]>({
    queryKey:        ['decision-journal-resolved'],
    queryFn:         () => api.get('/home/decision-journal?status=resolved&limit=20').then(r => r.data),
    refetchInterval: 60_000,
  })

  const rows = Array.isArray(q.data) ? q.data : []

  // Compact verdict summary from visible rows
  const goodFollow  = rows.filter(r => r.verdict === 'GOOD_FOLLOW').length
  const goodSkip    = rows.filter(r => r.verdict === 'GOOD_SKIP').length
  const badDecision = rows.filter(r => r.verdict === 'BAD_FOLLOW' || r.verdict === 'BAD_SKIP').length
  const noOutcome   = rows.filter(r => r.resolution_status === 'NO_OUTCOME').length
  const totalGood   = goodFollow + goodSkip
  const totalVerdicted = rows.filter(r => r.verdict !== null).length

  const toggleExpand = (id: number) => {
    setExpanded(prev => {
      const s = new Set(prev)
      s.has(id) ? s.delete(id) : s.add(id)
      return s
    })
  }

  // Don't render at all when empty and not loading
  if (!q.isLoading && rows.length === 0) {
    return (
      <div className="glass-card" style={{
        border:       '1px solid rgba(255,255,255,0.06)',
        padding:      '12px 16px',
        marginBottom: 12,
      }}>
        <span style={{ fontFamily: 'JetBrains Mono, monospace', fontSize: 9, color: DIM }}>
          RESOLVED DECISIONS — no resolved entries yet
        </span>
      </div>
    )
  }

  return (
    <div className="glass-card" style={{
      border:       '1px solid rgba(255,255,255,0.07)',
      padding:      '12px 16px',
      marginBottom: 12,
    }}>

      {/* ── Header row ── */}
      <div style={{
        display: 'flex', alignItems: 'center',
        justifyContent: 'space-between', marginBottom: 10,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span className="section-label" style={{ color: DIM, fontSize: 9 }}>
            RESOLVED DECISIONS
          </span>
          {rows.length > 0 && (
            <span style={{
              fontFamily: 'JetBrains Mono, monospace', fontSize: 8,
              background: 'rgba(255,255,255,0.05)', border: '1px solid rgba(255,255,255,0.1)',
              borderRadius: 10, padding: '1px 7px', color: MUTED,
            }}>
              {rows.length}
            </span>
          )}
        </div>

        {/* ── Verdict summary bar ── */}
        {totalVerdicted > 0 || noOutcome > 0 ? (
          <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
            {totalGood > 0 && (
              <span style={{ fontFamily: 'JetBrains Mono, monospace', fontSize: 8, color: '#00d48a' }}>
                {totalGood} GOOD
                <span style={{ color: DIM, marginLeft: 3 }}>
                  ({goodFollow}F / {goodSkip}S)
                </span>
              </span>
            )}
            {badDecision > 0 && (
              <span style={{ fontFamily: 'JetBrains Mono, monospace', fontSize: 8, color: '#ef4444' }}>
                {badDecision} BAD
              </span>
            )}
            {noOutcome > 0 && (
              <span style={{ fontFamily: 'JetBrains Mono, monospace', fontSize: 8, color: DIM }}>
                {noOutcome} NO OUTCOME
              </span>
            )}
          </div>
        ) : (
          <span style={{ fontFamily: 'JetBrains Mono, monospace', fontSize: 8, color: DIM }}>
            verdicts pending
          </span>
        )}
      </div>

      {/* ── Rows ── */}
      {q.isLoading ? (
        <span style={{ fontFamily: 'JetBrains Mono, monospace', fontSize: 9, color: DIM }}>
          Loading…
        </span>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          {rows.map(d => {
            const snap       = d.snapshot_json ? (() => { try { return JSON.parse(d.snapshot_json!) } catch { return {} } })() : {}
            const isExpanded = expanded.has(d.id)
            const fqColor    = FQ_COLOR[snap.fuel_quality ?? ''] ?? DIM
            const ewColor    = EW_COLOR[snap.entry_window  ?? ''] ?? DIM
            const pct        = d.outcome_24h_pct
            const pctColor   = pct == null ? DIM : pct >= 0 ? '#00d48a' : '#ef4444'
            const verdictLabel = d.verdict ? (VERDICT_LABEL[d.verdict] ?? d.verdict) : null
            const verdictColor = d.verdict ? (VERDICT_COLOR[d.verdict] ?? DIM) : DIM

            return (
              <div key={d.id} style={{
                borderRadius: 5,
                border:       `1px solid rgba(255,255,255,0.05)`,
                background:   isExpanded ? 'rgba(255,255,255,0.025)' : 'transparent',
                overflow:     'hidden',
              }}>

                {/* ── Main row ── */}
                <div
                  onClick={() => toggleExpand(d.id)}
                  style={{
                    display:    'flex',
                    alignItems: 'center',
                    gap:        10,
                    padding:    '7px 10px',
                    cursor:     'pointer',
                    flexWrap:   'wrap',
                  }}
                >
                  {/* Symbol */}
                  <span style={{
                    fontFamily: 'JetBrains Mono, monospace',
                    fontWeight: 700, fontSize: 12, color: MUTED,
                    minWidth: 80,
                  }}>
                    {d.symbol}
                  </span>

                  {/* Operator decision badge */}
                  <span style={{
                    fontFamily:    'JetBrains Mono, monospace',
                    fontSize:      8, fontWeight: 700,
                    letterSpacing: '0.06em',
                    padding:       '2px 6px', borderRadius: 3,
                    color:         d.operator_decision === 'FOLLOWED' ? BLUE : DIM,
                    border:        `1px solid ${d.operator_decision === 'FOLLOWED' ? BLUE + '44' : 'rgba(255,255,255,0.1)'}`,
                    background:    d.operator_decision === 'FOLLOWED' ? BLUE + '15' : 'rgba(255,255,255,0.04)',
                  }}>
                    {d.operator_decision}
                  </span>

                  {/* Verdict chip */}
                  {verdictLabel ? (
                    <Badge label={verdictLabel} color={verdictColor} />
                  ) : (
                    <span style={{
                      fontFamily:    'JetBrains Mono, monospace',
                      fontSize:      8,
                      letterSpacing: '0.06em',
                      padding:       '2px 6px', borderRadius: 3,
                      color:         DIM,
                      border:        '1px solid rgba(255,255,255,0.08)',
                      background:    'rgba(255,255,255,0.03)',
                    }}>
                      NO OUTCOME
                    </span>
                  )}

                  {/* 24h return */}
                  <span style={{
                    fontFamily: 'JetBrains Mono, monospace',
                    fontSize:   10, fontWeight: 700, color: pctColor,
                    minWidth:   56, textAlign: 'right',
                  }}>
                    {pct == null ? '—' : `${pct >= 0 ? '+' : ''}${pct.toFixed(1)}%`}
                  </span>

                  {/* Resolved age */}
                  <span style={{
                    fontFamily: 'JetBrains Mono, monospace',
                    fontSize: 8, color: DIM,
                    marginLeft: 'auto',
                  }}>
                    {d.resolved_ts ? ageStr(d.resolved_ts) : ageStr(d.created_ts)}
                  </span>

                  {/* Expand indicator */}
                  <span style={{
                    fontFamily: 'JetBrains Mono, monospace',
                    fontSize: 8, color: DIM,
                  }}>
                    {isExpanded ? '▲' : '▼'}
                  </span>
                </div>

                {/* ── Expanded snapshot context ── */}
                {isExpanded && (
                  <div style={{
                    padding:    '8px 10px 10px 10px',
                    borderTop:  '1px solid rgba(255,255,255,0.05)',
                    background: 'rgba(0,0,0,0.15)',
                  }}>
                    {/* Context fields from snapshot_json */}
                    <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', marginBottom: 6 }}>
                      {snap.score != null && (
                        <div>
                          <div style={{ fontFamily: 'JetBrains Mono, monospace', fontSize: 7, color: DIM, letterSpacing: '0.1em' }}>SCORE</div>
                          <div style={{ fontFamily: 'JetBrains Mono, monospace', fontSize: 11, fontWeight: 700, color: AMBER }}>{snap.score}</div>
                        </div>
                      )}
                      {snap.move_type && (
                        <div>
                          <div style={{ fontFamily: 'JetBrains Mono, monospace', fontSize: 7, color: DIM, letterSpacing: '0.1em' }}>MOVE TYPE</div>
                          <div style={{ fontFamily: 'JetBrains Mono, monospace', fontSize: 10, color: MUTED }}>{snap.move_type.replace(/_/g, ' ')}</div>
                        </div>
                      )}
                      {snap.fuel_quality && (
                        <div>
                          <div style={{ fontFamily: 'JetBrains Mono, monospace', fontSize: 7, color: DIM, letterSpacing: '0.1em' }}>FUEL</div>
                          <div style={{ fontFamily: 'JetBrains Mono, monospace', fontSize: 10, fontWeight: 700, color: fqColor }}>{snap.fuel_quality}</div>
                        </div>
                      )}
                      {snap.entry_window && (
                        <div>
                          <div style={{ fontFamily: 'JetBrains Mono, monospace', fontSize: 7, color: DIM, letterSpacing: '0.1em' }}>WINDOW</div>
                          <div style={{ fontFamily: 'JetBrains Mono, monospace', fontSize: 10, fontWeight: 700, color: ewColor }}>{snap.entry_window}</div>
                        </div>
                      )}
                      {snap.move_phase && (
                        <div>
                          <div style={{ fontFamily: 'JetBrains Mono, monospace', fontSize: 7, color: DIM, letterSpacing: '0.1em' }}>PHASE</div>
                          <div style={{ fontFamily: 'JetBrains Mono, monospace', fontSize: 10, color: MUTED }}>{snap.move_phase}</div>
                        </div>
                      )}
                      {snap.n_windows != null && snap.n_windows > 0 && (
                        <div>
                          <div style={{ fontFamily: 'JetBrains Mono, monospace', fontSize: 7, color: DIM, letterSpacing: '0.1em' }}>WINDOWS</div>
                          <div style={{ fontFamily: 'JetBrains Mono, monospace', fontSize: 10, color: MUTED }}>{snap.n_windows}w</div>
                        </div>
                      )}
                    </div>
                    {/* Reason */}
                    {(d.reason || snap.reason) && (
                      <div style={{
                        fontFamily: 'JetBrains Mono, monospace', fontSize: 8,
                        color: MUTED, fontStyle: 'italic', lineHeight: 1.5,
                      }}>
                        {d.reason || snap.reason}
                      </div>
                    )}
                    {/* Surfaced at */}
                    <div style={{
                      marginTop: 6,
                      fontFamily: 'JetBrains Mono, monospace', fontSize: 7, color: DIM,
                    }}>
                      surfaced {ageStr(d.created_ts)} · decision: {d.operator_decision} · status: {d.resolution_status}
                    </div>
                  </div>
                )}

              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

// ── CalibrationPanel ──────────────────────────────────────────────────────────

function wrColor(wr: number): string {
  if (wr >= 60) return GREEN
  if (wr >= 50) return AMBER
  return RED
}

function CalibrationPanel() {
  const [window, setWindow] = React.useState<'all' | '14d'>('14d')

  const q = useQuery<CalibrationResponse>({
    queryKey:        ['research-calibration'],
    queryFn:         () => api.get('/research/calibration').then(r => r.data),
    refetchInterval: 120_000,
  })

  const data = q.data

  if (q.isLoading) {
    return (
      <div className="glass-card" style={{
        border: '1px solid rgba(255,255,255,0.06)',
        padding: '14px 18px', marginTop: 12,
      }}>
        <span style={{ fontFamily: 'JetBrains Mono, monospace', fontSize: 9, color: DIM }}>
          Loading calibration…
        </span>
      </div>
    )
  }

  if (!data || data.total_resolved === 0) {
    return (
      <div className="glass-card" style={{
        border: '1px solid rgba(255,255,255,0.06)',
        padding: '14px 18px', marginTop: 12,
      }}>
        <span style={{ fontFamily: 'JetBrains Mono, monospace', fontSize: 9, color: DIM }}>
          CALIBRATION — waiting for first resolved research outcomes
        </span>
        {/* Patch 281: explain the dependency so operator understands empty state */}
        <div style={{
          fontFamily: 'JetBrains Mono, monospace', fontSize: 8,
          color: '#2d3a4a', marginTop: 6, lineHeight: 1.7,
        }}>
          Pipeline: research decisions logged → 24h scanner returns fill outcomes → calibration builds.
          Data populates automatically — no action needed.
        </div>
      </div>
    )
  }

  const w: CalibWindow = window === 'all' ? data.all_time : data.recent_14d
  const windowLabel = window === 'all'
    ? `all time  (n=${data.total_resolved})`
    : `14d  (n=${w.n})`

  const thStyle: React.CSSProperties = {
    ...TH, fontSize: 7, padding: '4px 8px',
  }
  const tdStyle: React.CSSProperties = {
    ...TD, fontSize: 9, padding: '5px 8px',
  }

  return (
    <div className="glass-card" style={{
      border: '1px solid rgba(255,255,255,0.06)',
      padding: '14px 18px', marginTop: 12,
    }}>
      {/* Header row */}
      <div style={{
        display: 'flex', alignItems: 'baseline',
        justifyContent: 'space-between', marginBottom: 12,
      }}>
        <span className="section-label" style={{ color: DIM, fontSize: 9 }}>
          CALIBRATION
        </span>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          {/* Summary stats */}
          <span style={{ fontFamily: 'JetBrains Mono, monospace', fontSize: 9, color: wrColor(w.overall_win_rate_pct) }}>
            {w.overall_win_rate_pct.toFixed(1)}% WR
          </span>
          <span style={{ fontFamily: 'JetBrains Mono, monospace', fontSize: 9, color: DIM }}>
            {windowLabel}
          </span>
          {/* Window toggle */}
          {(['14d', 'all'] as const).map(opt => (
            <button
              key={opt}
              onClick={() => setWindow(opt)}
              style={{
                fontFamily: 'JetBrains Mono, monospace', fontSize: 8,
                padding: '2px 8px', borderRadius: 4, cursor: 'pointer',
                border: `1px solid ${window === opt ? AMBER + '66' : 'rgba(255,255,255,0.1)'}`,
                background: window === opt ? AMBER + '18' : 'transparent',
                color: window === opt ? AMBER : DIM,
              }}
            >
              {opt === '14d' ? 'LAST 14D' : 'ALL TIME'}
            </button>
          ))}
        </div>
      </div>

      {w.n === 0 ? (
        <span style={{ fontFamily: 'JetBrains Mono, monospace', fontSize: 9, color: DIM }}>
          No resolved outcomes in this window
        </span>
      ) : (
        <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>

          {/* Action Tier */}
          <div style={{ flex: '1 1 220px', minWidth: 200 }}>
            <div style={{
              fontFamily: 'JetBrains Mono, monospace', fontSize: 7, fontWeight: 700,
              color: DIM, letterSpacing: '0.1em', marginBottom: 6,
            }}>
              ACTION TIER
            </div>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead>
                <tr>
                  <th style={thStyle}>TIER</th>
                  <th style={{ ...thStyle, textAlign: 'right' }}>N</th>
                  <th style={{ ...thStyle, textAlign: 'right' }}>WR%</th>
                  <th style={{ ...thStyle, textAlign: 'right' }}>AVG</th>
                  <th style={{ ...thStyle, textAlign: 'right' }}>MED</th>
                </tr>
              </thead>
              <tbody>
                {w.action_tier.map(r => (
                  <tr key={r.action} style={{ opacity: r.thin ? 0.55 : 1 }}>
                    <td style={{ ...tdStyle, color: ACTION_COLOR[r.action] ?? MUTED }}>
                      {r.action}
                      {r.thin && (
                        <span style={{ color: DIM, fontSize: 7, marginLeft: 4 }}>thin</span>
                      )}
                    </td>
                    <td style={{ ...tdStyle, textAlign: 'right', color: DIM }}>{r.n}</td>
                    <td style={{ ...tdStyle, textAlign: 'right', color: wrColor(r.win_rate_pct), fontWeight: 700 }}>
                      {r.win_rate_pct.toFixed(1)}%
                    </td>
                    <td style={{ ...tdStyle, textAlign: 'right', color: (r.avg_return_24h_pct ?? 0) >= 0 ? GREEN : RED }}>
                      {(r.avg_return_24h_pct ?? 0) >= 0 ? '+' : ''}{(r.avg_return_24h_pct ?? 0).toFixed(1)}%
                    </td>
                    <td style={{ ...tdStyle, textAlign: 'right', color: (r.median_return_24h_pct ?? 0) >= 0 ? GREEN : RED }}>
                      {(r.median_return_24h_pct ?? 0) >= 0 ? '+' : ''}{(r.median_return_24h_pct ?? 0).toFixed(1)}%
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Move Type */}
          <div style={{ flex: '1 1 260px', minWidth: 230 }}>
            <div style={{
              fontFamily: 'JetBrains Mono, monospace', fontSize: 7, fontWeight: 700,
              color: DIM, letterSpacing: '0.1em', marginBottom: 6,
            }}>
              MOVE TYPE
            </div>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead>
                <tr>
                  <th style={thStyle}>TYPE</th>
                  <th style={{ ...thStyle, textAlign: 'right' }}>N</th>
                  <th style={{ ...thStyle, textAlign: 'right' }}>WR%</th>
                  <th style={{ ...thStyle, textAlign: 'right' }}>AVG</th>
                  <th style={{ ...thStyle, textAlign: 'right' }}>MED</th>
                </tr>
              </thead>
              <tbody>
                {w.move_type.map(r => (
                  <tr key={r.move_type} style={{ opacity: r.thin ? 0.55 : 1 }}>
                    <td style={{ ...tdStyle, color: MUTED, fontSize: 8 }}>
                      {r.move_type.replace(/_/g, ' ')}
                      {r.thin && (
                        <span style={{ color: DIM, fontSize: 7, marginLeft: 4 }}>thin</span>
                      )}
                    </td>
                    <td style={{ ...tdStyle, textAlign: 'right', color: DIM }}>{r.n}</td>
                    <td style={{ ...tdStyle, textAlign: 'right', color: wrColor(r.win_rate_pct), fontWeight: 700 }}>
                      {r.win_rate_pct.toFixed(1)}%
                    </td>
                    <td style={{ ...tdStyle, textAlign: 'right', color: (r.avg_return_24h_pct ?? 0) >= 0 ? GREEN : RED }}>
                      {(r.avg_return_24h_pct ?? 0) >= 0 ? '+' : ''}{(r.avg_return_24h_pct ?? 0).toFixed(1)}%
                    </td>
                    <td style={{ ...tdStyle, textAlign: 'right', color: (r.median_return_24h_pct ?? 0) >= 0 ? GREEN : RED }}>
                      {(r.median_return_24h_pct ?? 0) >= 0 ? '+' : ''}{(r.median_return_24h_pct ?? 0).toFixed(1)}%
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Fuel × Window */}
          <div style={{ flex: '1 1 200px', minWidth: 180 }}>
            <div style={{
              fontFamily: 'JetBrains Mono, monospace', fontSize: 7, fontWeight: 700,
              color: DIM, letterSpacing: '0.1em', marginBottom: 6,
            }}>
              FUEL × WINDOW
            </div>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead>
                <tr>
                  <th style={thStyle}>FUEL</th>
                  <th style={thStyle}>WIN</th>
                  <th style={{ ...thStyle, textAlign: 'right' }}>N</th>
                  <th style={{ ...thStyle, textAlign: 'right' }}>WR%</th>
                </tr>
              </thead>
              <tbody>
                {w.fuel_window.map((r, i) => (
                  <tr key={i} style={{ opacity: r.thin ? 0.55 : 1 }}>
                    <td style={{ ...tdStyle, color: FQ_COLOR[r.fuel_quality] ?? MUTED }}>
                      {r.fuel_quality}
                    </td>
                    <td style={{ ...tdStyle, color: EW_COLOR[r.entry_window] ?? DIM }}>
                      {r.entry_window}
                      {r.thin && (
                        <span style={{ color: DIM, fontSize: 7, marginLeft: 4 }}>thin</span>
                      )}
                    </td>
                    <td style={{ ...tdStyle, textAlign: 'right', color: DIM }}>{r.n}</td>
                    <td style={{ ...tdStyle, textAlign: 'right', color: wrColor(r.win_rate_pct), fontWeight: 700 }}>
                      {r.win_rate_pct.toFixed(1)}%
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

        </div>
      )}

      {/* Footer */}
      <div style={{
        marginTop: 10, fontFamily: 'JetBrains Mono, monospace',
        fontSize: 7, color: DIM, lineHeight: 1.7,
      }}>
        RESOLVED = 24h outcome filled from scanner data · thin = n&lt;{data.thin_threshold} · read-only reporting layer
        {data.data_window_start && (
          <span style={{ marginLeft: 10 }}>
            · data from {data.data_window_start.slice(0, 10)}
          </span>
        )}
      </div>
    </div>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────

export function ResearchPage() {
  const q = useQuery<CandidatesResponse>({
    queryKey: ['research-candidates'],
    queryFn:  () => api.get('/research/candidates').then(r => r.data),
    refetchInterval: 60_000,
  })

  const data = q.data
  const candidates = data?.candidates ?? []

  // Separate second-leg items for the top strip
  const secondLeg = candidates.filter(c => c.is_second_leg)

  return (
    <div style={{ maxWidth: 1200, margin: '0 auto', padding: '16px 20px' }}>

      {/* ── Header ────────────────────────────────────────────────────────── */}
      <div className="glass-card" style={{
        border: `1px solid ${AMBER}33`,
        marginBottom: 16,
        padding: '18px 22px',
      }}>
        <div style={{
          display: 'flex', alignItems: 'baseline',
          justifyContent: 'space-between', marginBottom: 18,
        }}>
          <div>
            <span className="section-label" style={{ color: AMBER }}>RESEARCH</span>
            <span style={{
              marginLeft: 12, fontSize: 9,
              fontFamily: 'JetBrains Mono, monospace',
              color: DIM, letterSpacing: '0.08em',
            }}>
              LIFECYCLE CANDIDATE WORKSPACE
            </span>
          </div>
          <span style={{
            fontFamily: 'JetBrains Mono, monospace', fontSize: 9, color: DIM,
          }}>
            {q.isFetching ? 'refreshing…' : 'auto-refresh 60s'}
          </span>
        </div>

        {/* Stats row */}
        <div style={{ display: 'flex', gap: 28, flexWrap: 'wrap' }}>
          <StatPill
            label="CANDIDATES"
            value={data?.total ?? '—'}
            color={MUTED}
          />
          <StatPill
            label="ACT"
            value={data?.act_count ?? '—'}
            color={AMBER}
          />
          <StatPill
            label="RESEARCH"
            value={data?.research_count ?? '—'}
            color={BLUE}
          />
          <StatPill
            label="MONITOR"
            value={data?.monitor_count ?? '—'}
            color={DIM}
          />
          <StatPill
            label="2ND LEG"
            value={data?.second_leg_count ?? '—'}
            color={TEAL}
          />
        </div>

        {/* Second-leg highlight strip */}
        {secondLeg.length > 0 && (
          <div style={{
            marginTop: 16,
            padding: '10px 14px',
            background: 'rgba(45,212,191,0.05)',
            border: `1px solid ${TEAL}33`,
            borderRadius: 8,
          }}>
            <div style={{
              fontFamily: 'JetBrains Mono, monospace', fontSize: 8, fontWeight: 700,
              color: TEAL, letterSpacing: '0.12em', marginBottom: 8,
            }}>
              SECOND LEG SETUPS
            </div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
              {secondLeg.map(c => (
                <div key={c.symbol} style={{
                  display: 'flex', alignItems: 'center', gap: 6,
                  padding: '4px 10px',
                  background: 'rgba(45,212,191,0.07)',
                  border: `1px solid ${TEAL}44`,
                  borderRadius: 6,
                }}>
                  <span style={{
                    fontFamily: 'JetBrains Mono, monospace',
                    fontWeight: 700, fontSize: 11, color: TEAL,
                  }}>
                    {c.symbol}
                  </span>
                  <span style={{
                    fontFamily: 'JetBrains Mono, monospace', fontSize: 8, color: DIM,
                  }}>
                    {c.n_windows}w · {c.best_return_pct > 0 ? `+${c.best_return_pct.toFixed(0)}%` : '—'} peak
                  </span>
                  <Badge label={c.move_type.replace('_', ' ')} color={BUDGET_COLOR[c.research_priority] ?? DIM} />
                </div>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* ── Trust state ───────────────────────────────────────────────────── */}
      <TrustStatePanel />

      {/* ── Pending decisions ─────────────────────────────────────────────── */}
      <PendingDecisionsPanel />

      {/* ── Resolved decisions review ─────────────────────────────────────── */}
      <ResolvedDecisionsPanel />

      {/* ── Candidate table ───────────────────────────────────────────────── */}
      <div className="glass-card" style={{
        border: '1px solid rgba(255,255,255,0.08)',
        padding: '16px 0 8px',
      }}>
        <div style={{
          padding: '0 16px 12px',
          display: 'flex', alignItems: 'baseline', justifyContent: 'space-between',
        }}>
          <span className="section-label" style={{ color: MUTED }}>RANKED CANDIDATES</span>
          <div style={{ display: 'flex', gap: 14, alignItems: 'center' }}>
            {/* Tier legend */}
            {(['ACT', 'RESEARCH', 'MONITOR'] as const).map(a => (
              <span key={a} style={{
                fontFamily: 'JetBrains Mono, monospace', fontSize: 8,
                color: ACTION_COLOR[a],
              }}>
                ● {a}
              </span>
            ))}
            <span style={{
              fontFamily: 'JetBrains Mono, monospace', fontSize: 8, color: TEAL,
            }}>
              ● 2ND LEG
            </span>
          </div>
        </div>

        {q.isLoading ? (
          <div style={{
            padding: '40px', textAlign: 'center',
            color: DIM, fontFamily: 'JetBrains Mono, monospace', fontSize: 10,
          }}>
            Loading…
          </div>
        ) : candidates.length === 0 ? (
          <div style={{
            padding: '40px', textAlign: 'center',
            color: DIM, fontFamily: 'JetBrains Mono, monospace', fontSize: 10,
          }}>
            {data?.error ? `Error: ${data.error}` : 'No candidates pass current gates'}
          </div>
        ) : (
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead>
                <tr>
                  <th style={{ ...TH, textAlign: 'right', width: 30 }}>#</th>
                  <th style={{ ...TH, color: AMBER }}>SYMBOL</th>
                  <th style={TH}>TIER</th>
                  <th style={TH}>MOVE TYPE</th>
                  <th style={TH}>WINDOW</th>
                  <th style={TH}>FUEL</th>
                  <th style={TH}>PHASE</th>
                  <th style={{ ...TH, textAlign: 'right' }}>WINDOWS / PEAK</th>
                  <th style={{ ...TH, textAlign: 'right' }}>SCORE</th>
                  <th style={TH}>TAGS</th>
                </tr>
              </thead>
              <tbody>
                {candidates.map((c, i) => (
                  <CandidateRow key={c.symbol} c={c} idx={i} />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* ── Calibration ───────────────────────────────────────────────────── */}
      <CalibrationPanel />

      {/* ── Legend ────────────────────────────────────────────────────────── */}
      <div style={{
        marginTop: 10, padding: '8px 14px',
        fontFamily: 'JetBrains Mono, monospace', fontSize: 8,
        color: DIM, lineHeight: 1.7,
      }}>
        SCORE ≥150 → ACT · ≥100 → RESEARCH · ≥60 → MONITOR
        {'   ·   '}
        2ND LEG: RELOAD state + first_leg_confirmed + ≥3 windows
        {'   ·   '}
        BUDGET: HIGH=15m · MEDIUM=5m · QUICK_GLANCE=30s
      </div>

    </div>
  )
}
