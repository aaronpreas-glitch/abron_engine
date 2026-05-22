import { useQuery } from '@tanstack/react-query'
import { api } from '../api'
import { slowBudgetedInterval } from '../queryBudget'

// ── Types ─────────────────────────────────────────────────────────────────────

interface OperatingModeSummary {
  operating_mode: string
  allocator_posture: string
  route_bucket: string
  headroom_bucket: string
  window_bucket: string
  perp_mode: string
  memecoins_mode: string
  spot_mode: string
  any_live: boolean
}

interface ActionLawSummary {
  action_law_state: string
  highest_permitted_action: string
  law_confidence: string
  allowed_actions: string[]
  conditional_actions: Array<{ action: string; condition: string }>
  disallowed_actions: string[]
}

interface LanePolicySummary {
  primary_focus_lane: string
  allocator_posture: string
  memecoins: { policy_state: string; policy_note: string; source_posture: string }
  perps: { policy_state: string; policy_note: string; source_posture: string }
  spot: { policy_state: string; policy_note: string; source_posture: string }
  whale: { policy_state: string; policy_note: string; source_posture: string }
}

interface LaneAuthoritySummary {
  system_routing_state: string | null
  system_routing_note: string | null
  best_routing_lane: string | null
  routing_ready_lanes: string[] | null
}

interface Posture {
  operating_mode_summary: OperatingModeSummary
  action_law_summary: ActionLawSummary
  lane_policy_summary: LanePolicySummary
  lane_authority_summary: LaneAuthoritySummary
  proof_stack: { mode: string; proof_ready_now: number }
}

interface Blocker {
  label: string
  count: number
  detail: string
}

interface Decision {
  id: number
  ts: string
  source: string
  system: string
  symbol: string | null
  recommended_action: string
  priority: string
  reason: string
  operator_decision: string | null
  resolution_status: string
  verdict: string
  blockers: string[]
  surface_count?: number
  last_seen_ts?: string | null
}

interface Execution {
  kind: string
  id: number
  ts: string
  symbol: string | null
  // Whale
  status?: string
  score?: number | null
  arkham_quality?: string | null
  // Spot
  side?: string
  mode?: string
  amount_usd?: number | null
  price_usd?: number | null
  // Perp
  entry_price?: number | null
  exit_price?: number | null
  pnl_usd?: number | null
  exit_reason?: string
}

interface Anomaly {
  label: string
  severity: string
  detail: string
  ts?: string | null
}

interface WatchdogStatus {
  watchdog?: string
  status?: string
  checked_at?: string | null
  age_hours?: number | null
  last_eval_utc?: string | null
  threshold_hours?: number | null
  detail?: string | null
  recovery_attempted?: boolean
  recovery_result?: string | null
  last_snapshot_utc?: string | null
  last_snapshot_id?: number | null
  history_count?: number | null
  recorded_new_snapshot?: boolean
  latest_posture?: string | null
  latest_change?: {
    changed?: boolean
    current_posture?: string | null
    previous_posture?: string | null
    changed_arms?: Array<{
      arm: string
      from_action?: string | null
      to_action?: string | null
      from_target_pct?: number | null
      to_target_pct?: number | null
    }>
  }
}

interface WatchdogEvent {
  id: number
  ts_utc: string
  watchdog: string
  status: string
  detail: string | null
  metadata?: {
    age_hours?: number | null
    last_eval_utc?: string | null
    threshold_hours?: number | null
  }
}

interface AuditData {
  generated_at: string
  posture: Posture
  watchdogs: {
    pipeline: WatchdogStatus
    events: WatchdogEvent[]
    allocator?: WatchdogStatus
    allocator_history?: Array<{
      id: number
      ts_utc: string
      posture: string
    }>
  }
  blockers: Blocker[]
  recent_decisions: Decision[]
  recent_executions: Execution[]
  anomalies: Anomaly[]
}

// ── Constants ─────────────────────────────────────────────────────────────────

const MONO: React.CSSProperties = { fontFamily: 'JetBrains Mono, monospace' }

const KIND_STYLE: Record<string, { color: string; label: string }> = {
  PERP:           { color: '#00d48a', label: 'PERP' },
  MEMECOIN:       { color: '#f59e0b', label: 'MEME' },
  SPOT:           { color: '#60a5fa', label: 'SPOT' },
  WHALE_SIGNAL:   { color: '#a78bfa', label: 'WHALE' },
  CONFLUENCE:     { color: '#06b6d4', label: 'CONF' },
}

const MODE_COLOR: Record<string, string> = {
  LIVE: '#00d48a',
  LIVE_MANAGED: '#00d48a',
  PAPER: '#f59e0b',
  PILOT: '#f59e0b',
  SIMULATE: '#4d6070',
}

const LAW_COLOR: Record<string, string> = {
  RESTRICTED: '#ef4444',
  CONDITIONAL: '#f59e0b',
  PERMISSIVE: '#60a5fa',
  EXECUTION_ENABLED: '#00d48a',
}

const POLICY_COLOR: Record<string, string> = {
  EXECUTION_CAPABLE: '#00d48a',
  REINFORCEMENT_ACTIVE: '#60a5fa',
  PLANNING_ONLY: '#f59e0b',
  MANUAL_ONLY: '#4d6070',
  SIM_ONLY: '#4d6070',
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmtAge(ts: string): string {
  try {
    const d = new Date(ts.includes('T') ? ts : ts + 'Z')
    const diff = (Date.now() - d.getTime()) / 1000
    if (diff < 60) return `${Math.floor(diff)}s ago`
    if (diff < 3600) return `${Math.floor(diff / 60)}m ago`
    if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`
    return `${Math.floor(diff / 86400)}d ago`
  } catch { return '—' }
}

function fmtUsd(n: number | null | undefined): string {
  if (n == null) return '—'
  if (Math.abs(n) >= 1_000_000) return `$${(n / 1_000_000).toFixed(1)}M`
  if (Math.abs(n) >= 1_000) return `$${(n / 1_000).toFixed(0)}K`
  return `$${n.toFixed(2)}`
}

// ── Posture Strip ─────────────────────────────────────────────────────────────

function PostureStrip({ posture }: { posture: Posture }) {
  const oms = posture.operating_mode_summary
  const als = posture.action_law_summary
  const las = posture.lane_authority_summary
  const lps = posture.lane_policy_summary
  const ps  = posture.proof_stack

  const modeColor = MODE_COLOR[oms.operating_mode] ?? '#4d6070'
  const lawColor  = LAW_COLOR[als.action_law_state] ?? '#4d6070'

  return (
    <div className="card" style={{
      display: 'flex', flexDirection: 'column', gap: 12,
      borderLeft: `3px solid ${modeColor}60`,
    }}>
      {/* Top row: mode + action law + routing */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
        <span style={{ ...MONO, fontSize: 9, fontWeight: 700, color: modeColor, letterSpacing: '0.10em' }}>
          {(oms.operating_mode ?? '').replace(/_/g, ' ')}
        </span>
        <Pill color={lawColor}>{(als.action_law_state ?? '').replace(/_/g, ' ')}</Pill>
        <span style={{ ...MONO, fontSize: 8, color: '#4d6070' }}>
          permits: {(als.highest_permitted_action ?? '').replace(/_/g, ' ').toLowerCase()}
        </span>
        {las.system_routing_state && (
          <>
            <span style={{ color: 'rgba(255,255,255,0.08)' }}>|</span>
            <span style={{ ...MONO, fontSize: 8, color: '#4d6070' }}>
              routing: {las.system_routing_state.replace(/_/g, ' ').toLowerCase()}
            </span>
          </>
        )}
      </div>

      {/* Lane state row */}
      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
        {(['perps', 'memecoins', 'spot', 'whale'] as const).map(lane => {
          const lp = lps[lane]
          if (!lp) return null
          const laneMode = lane === 'perps' ? oms.perp_mode
            : lane === 'memecoins' ? oms.memecoins_mode
            : lane === 'spot' ? oms.spot_mode
            : null
          const pColor = POLICY_COLOR[lp.policy_state] ?? '#4d6070'
          return (
            <div key={lane} style={{
              flex: '1 1 140px', minWidth: 0,
              display: 'flex', alignItems: 'center', gap: 6,
              padding: '6px 10px',
              background: `${pColor}06`,
              border: `1px solid ${pColor}18`,
              borderRadius: 5,
            }}>
              <span style={{ ...MONO, fontSize: 8, fontWeight: 700, color: pColor, letterSpacing: '0.06em', minWidth: 36 }}>
                {lane === 'whale' ? 'WHALE' : lane.toUpperCase().slice(0, 5)}
              </span>
              {laneMode && (
                <span style={{
                  ...MONO, fontSize: 7, fontWeight: 700,
                  color: MODE_COLOR[laneMode] ?? '#4d6070',
                  background: `${MODE_COLOR[laneMode] ?? '#4d6070'}12`,
                  border: `1px solid ${MODE_COLOR[laneMode] ?? '#4d6070'}28`,
                  borderRadius: 2, padding: '0 4px',
                }}>
                  {laneMode}
                </span>
              )}
              <span style={{ ...MONO, fontSize: 7, color: '#4d6070' }}>
                {(lp.source_posture ?? '').replace(/_/g, ' ').toLowerCase()}
              </span>
            </div>
          )
        })}
      </div>

      {/* Bottom: allocator + buckets + trade readiness */}
      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', ...MONO, fontSize: 8, color: '#4d6070' }}>
        <span>allocator <span style={{ fontWeight: 700 }}>{oms.allocator_posture}</span></span>
        <span>route <span style={{ fontWeight: 700 }}>{oms.route_bucket}</span></span>
        <span>headroom <span style={{ fontWeight: 700 }}>{oms.headroom_bucket}</span></span>
        <span>window <span style={{ fontWeight: 700 }}>{oms.window_bucket}</span></span>
        <span>
          trade readiness <span style={{ fontWeight: 700, color: ps.proof_ready_now > 0 ? '#00d48a' : '#4d6070' }}>
            {ps.proof_ready_now > 0 ? `${ps.proof_ready_now} trade-ready` : ps.mode}
          </span>
        </span>
        <span>focus <span style={{ fontWeight: 700, color: '#60a5fa' }}>{lps.primary_focus_lane}</span></span>
      </div>
    </div>
  )
}

function Pill({ color, children }: { color: string; children: React.ReactNode }) {
  return (
    <span style={{
      ...MONO, fontSize: 8, fontWeight: 700, letterSpacing: '0.06em',
      color, background: `${color}12`, border: `1px solid ${color}30`,
      borderRadius: 3, padding: '1px 6px',
    }}>
      {children}
    </span>
  )
}

function watchdogColor(status: string | undefined): string {
  const key = (status || '').toUpperCase()
  if (key === 'FRESH' || key === 'RECOVERED') return '#00d48a'
  if (key === 'AGING' || key === 'RECOVERY_TRIGGERED') return '#f59e0b'
  if (key === 'STALE' || key === 'NO_DATA' || key === 'RECOVERY_FAILED') return '#ef4444'
  return '#4d6070'
}

function fmtHours(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return '—'
  return `${value.toFixed(value >= 10 ? 0 : 1)}h`
}

function WatchdogPanel({ pipeline, events }: { pipeline: WatchdogStatus; events: WatchdogEvent[] }) {
  const status = (pipeline.status || 'UNKNOWN').toUpperCase()
  const color = watchdogColor(status)
  return (
    <div className="card" style={{
      display: 'flex', flexDirection: 'column', gap: 10,
      borderLeft: `3px solid ${color}60`,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <span style={{ ...MONO, fontSize: 9, fontWeight: 700, color: 'var(--dim)', letterSpacing: '0.12em' }}>
          WATCHDOG
        </span>
        <Pill color={color}>{status.replace(/_/g, ' ')}</Pill>
        {pipeline.recovery_result && (
          <span style={{ ...MONO, fontSize: 8, color: '#4d6070' }}>
            recovery {pipeline.recovery_result.replace(/_/g, ' ').toLowerCase()}
          </span>
        )}
        <span style={{ marginLeft: 'auto', ...MONO, fontSize: 8, color: '#4d6070' }}>
          checked {pipeline.checked_at ? fmtAge(pipeline.checked_at) : '—'}
        </span>
      </div>

      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', ...MONO, fontSize: 8, color: '#4d6070' }}>
        <span>eval age <span style={{ color, fontWeight: 700 }}>{fmtHours(pipeline.age_hours)}</span></span>
        <span>stale threshold <span style={{ fontWeight: 700 }}>{fmtHours(pipeline.threshold_hours)}</span></span>
        <span>last eval <span style={{ fontWeight: 700 }}>{pipeline.last_eval_utc ? fmtAge(pipeline.last_eval_utc) : '—'}</span></span>
      </div>

      {pipeline.detail && (
        <div style={{ ...MONO, fontSize: 9, color: '#4d6070' }}>
          {pipeline.detail}
        </div>
      )}

      <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
        {(events || []).length === 0 ? (
          <div style={{ ...MONO, fontSize: 9, color: '#4d6070' }}>
            No watchdog events recorded yet.
          </div>
        ) : (
          events.map((evt) => {
            const evtColor = watchdogColor(evt.status)
            const age = evt.metadata?.age_hours
            return (
              <div key={evt.id} style={{
                display: 'flex', alignItems: 'center', gap: 8,
                padding: '6px 10px',
                background: `${evtColor}06`,
                border: `1px solid ${evtColor}18`,
                borderRadius: 5,
                ...MONO, fontSize: 8,
              }}>
                <Pill color={evtColor}>{evt.status.replace(/_/g, ' ')}</Pill>
                <span style={{ color: '#4d6070', flex: 1 }}>
                  {evt.detail || evt.watchdog}
                </span>
                {age != null && (
                  <span style={{ color: '#4d6070' }}>
                    age {fmtHours(age)}
                  </span>
                )}
                <span style={{ color: '#4d6070', flexShrink: 0 }}>
                  {fmtAge(evt.ts_utc)}
                </span>
              </div>
            )
          })
        )}
      </div>
    </div>
  )
}

function AllocatorPanel({
  allocator,
  history,
}: {
  allocator: WatchdogStatus
  history: Array<{ id: number; ts_utc: string; posture: string }>
}) {
  const status = (allocator.status || 'UNKNOWN').toUpperCase()
  const color = status === 'ACTIVE' ? '#60a5fa' : status === 'ERROR' ? '#ef4444' : '#4d6070'
  const latestChange = allocator.latest_change
  return (
    <div className="card" style={{
      display: 'flex', flexDirection: 'column', gap: 10,
      borderLeft: `3px solid ${color}60`,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <span style={{ ...MONO, fontSize: 9, fontWeight: 700, color: 'var(--dim)', letterSpacing: '0.12em' }}>
          ALLOCATOR
        </span>
        <Pill color={color}>{status.replace(/_/g, ' ')}</Pill>
        {allocator.latest_posture && (
          <span style={{ ...MONO, fontSize: 8, color: '#60a5fa' }}>
            {allocator.latest_posture.replace(/_/g, ' ').toLowerCase()}
          </span>
        )}
        <span style={{ marginLeft: 'auto', ...MONO, fontSize: 8, color: '#4d6070' }}>
          checked {allocator.checked_at ? fmtAge(allocator.checked_at) : '—'}
        </span>
      </div>

      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', ...MONO, fontSize: 8, color: '#4d6070' }}>
        <span>history <span style={{ fontWeight: 700 }}>{allocator.history_count ?? history.length ?? 0}</span></span>
        <span>last snapshot <span style={{ fontWeight: 700 }}>{allocator.last_snapshot_utc ? fmtAge(allocator.last_snapshot_utc) : '—'}</span></span>
        <span>new snapshot <span style={{ fontWeight: 700, color: allocator.recorded_new_snapshot ? '#00d48a' : '#4d6070' }}>{allocator.recorded_new_snapshot ? 'yes' : 'no'}</span></span>
      </div>

      {allocator.detail && (
        <div style={{ ...MONO, fontSize: 9, color: '#4d6070' }}>
          {allocator.detail}
        </div>
      )}

      {latestChange?.changed ? (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          <div style={{ ...MONO, fontSize: 8, color: '#60a5fa' }}>
            {String(latestChange.previous_posture || 'UNKNOWN').replace(/_/g, ' ').toLowerCase()}
            {' → '}
            {String(latestChange.current_posture || 'UNKNOWN').replace(/_/g, ' ').toLowerCase()}
          </div>
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            {(latestChange.changed_arms || []).slice(0, 4).map(item => (
              <span key={`${item.arm}-${item.to_action}`} style={{
                ...MONO, fontSize: 8, color: '#4d6070',
                padding: '2px 6px', borderRadius: 4,
                background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.06)',
              }}>
                {item.arm} {String(item.from_action || '—').toLowerCase()}→{String(item.to_action || '—').toLowerCase()}
              </span>
            ))}
          </div>
        </div>
      ) : (
        <div style={{ ...MONO, fontSize: 9, color: '#4d6070' }}>
          No material allocator posture change has been recorded yet.
        </div>
      )}

      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
        {history.length === 0 ? (
          <div style={{ ...MONO, fontSize: 9, color: '#4d6070' }}>No allocator history yet.</div>
        ) : (
          history.map(item => (
            <span key={item.id} style={{
              ...MONO, fontSize: 8,
              color: item.posture === 'LEAN_PERPS' ? '#00d48a' : item.posture === 'LEAN_MEMECOINS' ? '#60a5fa' : '#4d6070',
              padding: '2px 6px', borderRadius: 4,
              background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.06)',
            }}>
              {fmtAge(item.ts_utc)} {item.posture.replace(/_/g, ' ').toLowerCase()}
            </span>
          ))
        )}
      </div>
    </div>
  )
}

// ── Blockers Panel ────────────────────────────────────────────────────────────

function BlockersPanel({ blockers }: { blockers: Blocker[] }) {
  if (!blockers.length) {
    return (
      <div style={{
        ...MONO, fontSize: 9, color: '#4d6070',
        padding: '10px 14px',
        background: 'rgba(0,212,138,0.04)',
        border: '1px solid rgba(0,212,138,0.12)',
        borderRadius: 6,
      }}>
        <span style={{ color: '#00d48a', fontWeight: 700 }}>NO ACTIVE BLOCKERS</span>
        <span style={{ color: '#4d6070', marginLeft: 8 }}>— system operating within policy constraints</span>
      </div>
    )
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
      {blockers.map((b, i) => {
        const isWhale = b.label.startsWith('whale_fail:')
        const isPolicy = b.label.startsWith('action_law:') || b.label.startsWith('highest_permitted:')
        const isConf = b.label.startsWith('confluence:')
        const accent = isPolicy ? '#f59e0b' : isWhale ? '#a78bfa' : isConf ? '#06b6d4' : '#4d6070'
        return (
          <div key={i} style={{
            display: 'flex', alignItems: 'center', gap: 10,
            padding: '6px 12px',
            background: `${accent}06`,
            border: `1px solid ${accent}14`,
            borderLeft: `2px solid ${accent}50`,
            borderRadius: '0 5px 5px 0',
            ...MONO, fontSize: 9,
          }}>
            <span style={{ color: accent, fontWeight: 700, minWidth: 24, textAlign: 'right' }}>
              {b.count > 1 ? `x${b.count}` : ''}
            </span>
            <span style={{ color: 'var(--muted)' }}>{b.label}</span>
            <span style={{ color: '#4d6070', flex: 1 }}>{b.detail}</span>
          </div>
        )
      })}
    </div>
  )
}

// ── Decisions Panel ───────────────────────────────────────────────────────────

function DecisionsPanel({ decisions }: { decisions: Decision[] }) {
  if (!decisions.length) {
    return (
      <div style={{ ...MONO, fontSize: 9, color: '#4d6070', padding: '12px 14px' }}>
        No recent decisions recorded.
      </div>
    )
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
      {decisions.map(d => {
        const sysColor = d.system === 'MEMECOINS' ? '#f59e0b'
          : d.system === 'PERP' || d.system === 'PERPS' ? '#00d48a'
          : d.system === 'SPOT' ? '#60a5fa'
          : d.system === 'WHALE' ? '#a78bfa'
          : '#4d6070'
        const resColor = d.resolution_status === 'COMPLETE' ? '#00d48a'
          : d.resolution_status === 'ACTED' ? '#00d48a'
          : d.resolution_status === 'EXPIRED' ? '#4d6070'
          : d.resolution_status === 'WINDOW_EXPIRED' ? '#4d6070'
          : d.resolution_status === 'SKIPPED' ? '#4d6070'
          : d.resolution_status === 'PENDING' ? '#f59e0b'
          : '#4d6070'
        return (
          <div key={d.id} style={{
            display: 'flex', alignItems: 'flex-start', gap: 8,
            padding: '7px 12px',
            borderBottom: '1px solid rgba(255,255,255,0.03)',
            ...MONO, fontSize: 9,
          }}>
            <span style={{ color: '#4d6070', fontSize: 8, minWidth: 44, flexShrink: 0, paddingTop: 1 }}>
              {fmtAge(d.last_seen_ts ?? d.ts)}
            </span>
            <Pill color={sysColor}>{d.system.slice(0, 5)}</Pill>
            {d.symbol && (
              <span style={{ color: 'var(--text)', fontWeight: 700, minWidth: 40 }}>{d.symbol}</span>
            )}
            <span style={{ color: 'var(--muted)', flex: 1 }}>
              {d.recommended_action}
              {d.reason && (
                <span style={{ color: '#4d6070', marginLeft: 6 }}>
                  — {d.reason.length > 90 ? d.reason.slice(0, 90) + '...' : d.reason}
                </span>
              )}
            </span>
            {(d.surface_count ?? 0) > 1 && (
              <span style={{
                fontSize: 7, color: '#f59e0b', background: 'rgba(245,158,11,0.12)',
                border: '1px solid rgba(245,158,11,0.25)', borderRadius: 3,
                padding: '1px 5px', flexShrink: 0, alignSelf: 'center',
              }}>
                {d.surface_count}×
              </span>
            )}
            <span style={{ color: resColor, fontWeight: 700, fontSize: 8, flexShrink: 0 }}>
              {d.resolution_status}
            </span>
          </div>
        )
      })}
    </div>
  )
}

// ── Executions Panel ──────────────────────────────────────────────────────────

function ExecutionsPanel({ executions }: { executions: Execution[] }) {
  if (!executions.length) {
    return (
      <div style={{ ...MONO, fontSize: 9, color: '#4d6070', padding: '12px 14px' }}>
        No recent executions.
      </div>
    )
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
      {executions.map((e, i) => {
        const ks = KIND_STYLE[e.kind] ?? { color: '#4d6070', label: e.kind.slice(0, 5) }
        return (
          <div key={`${e.kind}-${e.id}-${i}`} style={{
            display: 'flex', alignItems: 'center', gap: 8,
            padding: '6px 12px',
            borderBottom: '1px solid rgba(255,255,255,0.03)',
            ...MONO, fontSize: 9,
          }}>
            <span style={{ color: '#4d6070', fontSize: 8, minWidth: 44, flexShrink: 0 }}>
              {fmtAge(e.ts)}
            </span>
            <span style={{
              fontSize: 7, fontWeight: 700, letterSpacing: '0.06em',
              color: ks.color, background: `${ks.color}12`,
              border: `1px solid ${ks.color}30`,
              borderRadius: 3, padding: '1px 5px', minWidth: 32, textAlign: 'center',
            }}>
              {ks.label}
            </span>
            <span style={{ color: 'var(--text)', fontWeight: 700, minWidth: 40 }}>
              {e.symbol ?? '—'}
            </span>
            <ExecutionDetail e={e} />
          </div>
        )
      })}
    </div>
  )
}

function ExecutionDetail({ e }: { e: Execution }) {
  if (e.kind === 'WHALE_SIGNAL') {
    const sColor = e.status === 'SCANNER_PASS' ? '#00d48a' : e.status === 'SCANNER_FAIL' ? '#ef4444' : '#4d6070'
    return (
      <span style={{ color: '#4d6070', flex: 1, display: 'flex', gap: 8, alignItems: 'center' }}>
        <span style={{ color: sColor, fontWeight: 600 }}>{e.status?.replace(/_/g, ' ')}</span>
        {e.score != null && <span>sc {e.score}</span>}
        {e.arkham_quality && <span>ark {e.arkham_quality}</span>}
      </span>
    )
  }
  if (e.kind === 'PERP') {
    const sideColor = e.side === 'LONG' ? '#00d48a' : e.side === 'SHORT' ? '#ef4444' : '#4d6070'
    return (
      <span style={{ color: '#4d6070', flex: 1, display: 'flex', gap: 8, alignItems: 'center' }}>
        <span style={{ color: sideColor, fontWeight: 600 }}>{e.side}</span>
        <span style={{ color: MODE_COLOR[e.mode ?? ''] ?? '#4d6070', fontSize: 7, fontWeight: 700 }}>{e.mode}</span>
        <span>{e.status?.replace(/_/g, ' ')}</span>
        {e.entry_price != null && <span>@ ${e.entry_price.toFixed(2)}</span>}
        {e.pnl_usd != null && (
          <span style={{ color: e.pnl_usd >= 0 ? '#00d48a' : '#ef4444', fontWeight: 700 }}>
            {e.pnl_usd >= 0 ? '+' : ''}{fmtUsd(e.pnl_usd)}
          </span>
        )}
      </span>
    )
  }
  if (e.kind === 'SPOT') {
    const sideColor = e.side === 'BUY' ? '#00d48a' : '#ef4444'
    return (
      <span style={{ color: '#4d6070', flex: 1, display: 'flex', gap: 8, alignItems: 'center' }}>
        <span style={{ color: sideColor, fontWeight: 600 }}>{e.side}</span>
        <span style={{ color: MODE_COLOR[e.mode ?? ''] ?? '#4d6070', fontSize: 7, fontWeight: 700 }}>{e.mode}</span>
        {e.amount_usd != null && <span>{fmtUsd(e.amount_usd)}</span>}
      </span>
    )
  }
  // Generic fallback
  return (
    <span style={{ color: '#4d6070', flex: 1 }}>
      {e.status ?? ''}
    </span>
  )
}

// ── Anomalies Panel ───────────────────────────────────────────────────────────

function AnomaliesPanel({ anomalies }: { anomalies: Anomaly[] }) {
  if (!anomalies.length) {
    return (
      <div style={{
        display: 'flex', alignItems: 'center', gap: 8,
        padding: '10px 14px',
        background: 'rgba(0,212,138,0.04)',
        border: '1px solid rgba(0,212,138,0.12)',
        borderRadius: 6,
        ...MONO, fontSize: 9,
      }}>
        <span style={{ color: '#00d48a', fontWeight: 700 }}>CLEAR</span>
        <span style={{ color: '#4d6070' }}>— no active anomalies detected</span>
      </div>
    )
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
      {anomalies.map((a, i) => {
        const sevColor = a.severity === 'HIGH' || a.severity === 'CRITICAL' ? '#ef4444'
          : a.severity === 'MEDIUM' ? '#f59e0b'
          : '#4d6070'
        return (
          <div key={i} style={{
            display: 'flex', alignItems: 'center', gap: 10,
            padding: '6px 12px',
            background: `${sevColor}06`,
            border: `1px solid ${sevColor}14`,
            borderLeft: `2px solid ${sevColor}50`,
            borderRadius: '0 5px 5px 0',
            ...MONO, fontSize: 9,
          }}>
            <Pill color={sevColor}>{a.severity}</Pill>
            <span style={{ color: 'var(--muted)', fontWeight: 700 }}>{a.label.replace(/_/g, ' ')}</span>
            <span style={{ color: '#4d6070', flex: 1 }}>{a.detail}</span>
            {a.ts && <span style={{ color: '#4d6070', fontSize: 8 }}>{fmtAge(a.ts)}</span>}
          </div>
        )
      })}
    </div>
  )
}

// ── Section Label ─────────────────────────────────────────────────────────────

function SectionLabel({ text, count }: { text: string; count?: number }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8 }}>
      <span style={{ ...MONO, fontSize: 9, fontWeight: 700, color: 'var(--dim)', letterSpacing: '0.12em' }}>
        {text}
      </span>
      {count != null && (
        <span style={{ ...MONO, fontSize: 8, color: '#4d6070' }}>{count}</span>
      )}
      <div style={{ flex: 1, height: 1, background: 'rgba(255,255,255,0.04)' }} />
    </div>
  )
}

function AuditSummary({
  data,
}: {
  data: AuditData
}) {
  const blockerCount = data.blockers.length
  const anomalyCount = data.anomalies.length
  const pendingDecisions = (data.recent_decisions ?? []).filter(d => (d.resolution_status || '').toUpperCase() === 'PENDING').length
  const pipelineStatus = String(data.watchdogs?.pipeline?.status || 'UNKNOWN').toUpperCase()
  const allocatorStatus = String(data.watchdogs?.allocator?.status || 'UNKNOWN').toUpperCase()

  const summaryTone =
    blockerCount > 0 || anomalyCount > 0
      ? '#f59e0b'
      : pipelineStatus === 'STALE' || allocatorStatus === 'ERROR'
        ? '#ef4444'
        : '#00d48a'

  const cards = [
    {
      label: 'PIPELINE',
      value: pipelineStatus,
      tone: watchdogColor(pipelineStatus),
      note: data.watchdogs?.pipeline?.detail || 'watchdog state',
    },
    {
      label: 'ALLOCATOR',
      value: allocatorStatus,
      tone: allocatorStatus === 'ACTIVE' ? '#60a5fa' : allocatorStatus === 'ERROR' ? '#ef4444' : '#7f95a8',
      note: data.watchdogs?.allocator?.detail || 'allocation state',
    },
    {
      label: 'BLOCKERS',
      value: `${blockerCount}`,
      tone: blockerCount > 0 ? '#f59e0b' : '#00d48a',
      note: blockerCount > 0 ? 'things actively constraining action' : 'nothing active',
    },
    {
      label: 'ANOMALIES',
      value: `${anomalyCount}`,
      tone: anomalyCount > 0 ? '#ef4444' : '#00d48a',
      note: anomalyCount > 0 ? 'requires attention' : 'nothing unusual',
    },
    {
      label: 'PENDING DECISIONS',
      value: `${pendingDecisions}`,
      tone: pendingDecisions > 0 ? '#60a5fa' : '#7f95a8',
      note: pendingDecisions > 0 ? 'open operator questions' : 'nothing waiting',
    },
  ]

  return (
    <div className="card" style={{
      background: 'rgba(5,10,18,0.72)',
      borderColor: 'rgba(255,255,255,0.06)',
      borderLeft: `3px solid ${summaryTone}55`,
      padding: '12px 16px',
      display: 'flex',
      flexDirection: 'column',
      gap: 12,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
        <span style={{ ...MONO, fontSize: 9, color: 'var(--dim)', letterSpacing: '0.12em' }}>TRUTH SUMMARY</span>
        <span style={{ ...MONO, fontSize: 10, color: '#d7e1ea' }}>
          {blockerCount > 0 || anomalyCount > 0
            ? 'The system is carrying active friction that deserves operator attention.'
            : 'No major friction is showing right now. Ops is mostly confirming system health.'}
        </span>
      </div>

      <div style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))',
        gap: 10,
      }}>
        {cards.map(card => (
          <div key={card.label} style={{
            background: 'rgba(255,255,255,0.02)',
            border: '1px solid rgba(255,255,255,0.05)',
            borderRadius: 8,
            padding: '10px 12px',
            display: 'flex',
            flexDirection: 'column',
            gap: 4,
          }}>
            <span style={{ ...MONO, fontSize: 8, color: 'var(--dim)', letterSpacing: '0.1em' }}>{card.label}</span>
            <span style={{ ...MONO, fontSize: 12, fontWeight: 700, color: card.tone }}>{card.value}</span>
            <span style={{ ...MONO, fontSize: 8, color: '#7f95a8', lineHeight: 1.45 }}>{card.note}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

// ── Main Page ─────────────────────────────────────────────────────────────────

export function AuditPage() {
  const { data, isLoading } = useQuery<AuditData>({
    queryKey: ['system-audit'],
    queryFn: () => api.get('/system/audit').then(r => r.data),
    refetchInterval: slowBudgetedInterval(90_000),
    staleTime: 45_000,
  })

  if (isLoading || !data) {
    return (
      <div style={{
        maxWidth: 1100, margin: '0 auto', padding: '60px 24px',
        textAlign: 'center', ...MONO, fontSize: 10, color: '#4d6070',
      }}>
        Loading ops surface...
      </div>
    )
  }

  return (
    <div style={{
      maxWidth: 1120, margin: '0 auto',
      padding: '20px 24px 40px',
      display: 'flex', flexDirection: 'column', gap: 18,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
        <span style={{ ...MONO, color: 'var(--text)', fontWeight: 700, fontSize: 11, letterSpacing: '0.16em' }}>
          OPS
        </span>
        <span style={{ ...MONO, color: 'var(--recessed)', fontSize: 9, letterSpacing: '0.04em' }}>
          blockers · anomalies · watchdog health
        </span>
        <span style={{ marginLeft: 'auto', ...MONO, fontSize: 8, color: '#4d6070' }}>
          {fmtAge(data.generated_at)}
        </span>
      </div>

      <PostureStrip posture={data.posture} />
      <AuditSummary data={data} />

      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) minmax(0, 1fr)', gap: 12 }}>
        <WatchdogPanel
          pipeline={data.watchdogs?.pipeline ?? {}}
          events={data.watchdogs?.events ?? []}
        />
        <AllocatorPanel
          allocator={data.watchdogs?.allocator ?? {}}
          history={data.watchdogs?.allocator_history ?? []}
        />
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) minmax(0, 1fr)', gap: 12 }}>
        <div>
          <SectionLabel text="BLOCKERS" count={data.blockers.length} />
          <BlockersPanel blockers={data.blockers} />
        </div>
        <div>
          <SectionLabel text="ANOMALIES" count={data.anomalies.length} />
          <AnomaliesPanel anomalies={data.anomalies} />
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) minmax(0, 1fr)', gap: 12 }}>
        <div className="card" style={{ padding: '12px 16px', display: 'flex', flexDirection: 'column', gap: 8 }}>
          <SectionLabel text="RECENT DECISIONS" count={data.recent_decisions.length} />
          <DecisionsPanel decisions={data.recent_decisions} />
        </div>
        <div className="card" style={{ padding: '12px 16px', display: 'flex', flexDirection: 'column', gap: 8 }}>
          <SectionLabel text="RECENT EXECUTIONS" count={data.recent_executions.length} />
          <ExecutionsPanel executions={data.recent_executions} />
        </div>
      </div>
    </div>
  )
}
