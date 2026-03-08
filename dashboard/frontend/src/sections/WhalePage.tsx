import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { api } from '../api'

// ── Types ─────────────────────────────────────────────────────────────────────

interface Milestone {
  outcomes: number
  phase: number
  label: string
  desc: string
}

interface TierData {
  total: number
  in_range: number
  scanner_pass: number
  alerts_sent: number
  complete: number
  wr_24h: number | null
  avg_return_24h: number | null
  avg_return_1h: number | null
}

interface CrossSignal {
  id: number
  ts_utc: string
  source: string
  target: string
  signal_type: string
  token_symbol: string | null
  mc_tier: string | null
  buy_amount_usd: number | null
  market_cap_usd: number | null
  scanner_score: number | null
  consumed: number
  ref_alert_id: number | null
}

interface WhaleStats {
  total: number
  outcomes: number
  in_range: number
  scanner_pass: number
  alerts_sent: number
  phase: number
  phase_label: string
  phase_desc: string
  next_milestone: number | null
  milestones: Milestone[]
  tiers: Record<string, TierData>
  cross_signals: CrossSignal[]
  last_ts: string | null
}

interface WhaleAlert {
  id: number
  ts_utc: string
  alert_type: string
  kol_name: string | null
  token_symbol: string | null
  token_mint: string | null
  buy_amount_usd: number | null
  market_cap_usd: number | null
  mc_tier: string | null
  mc_in_range: number
  scanner_pass: number | null
  scanner_score: number | null
  scanner_rug_label: string | null
  alert_sent: number
  price_at_alert: number | null
  return_1h_pct: number | null
  return_4h_pct: number | null
  return_24h_pct: number | null
  outcome_status: string
}

// ── MC Tier config ─────────────────────────────────────────────────────────────

const TIERS = {
  micro:      { label: 'MICRO',      range: '< $5M',       color: '#f87171', bg: 'rgba(248,113,113,0.05)', border: 'rgba(248,113,113,0.2)',  desc: 'Observation only — too risky to act on',   status: 'LOG ONLY'         },
  sweet_spot: { label: 'SWEET SPOT', range: '$5M – $50M',  color: '#00d48a', bg: 'rgba(0,212,138,0.05)',   border: 'rgba(0,212,138,0.2)',    desc: 'Active zone — scanner-integrated',          status: '✓ SCANNER ACTIVE' },
  mid:        { label: 'MID CAP',    range: '$50M – $200M',color: '#60a5fa', bg: 'rgba(96,165,250,0.05)',  border: 'rgba(96,165,250,0.2)',   desc: 'Future: Spot basket integration (Phase 4)', status: 'PHASE 4 →'        },
  large:      { label: 'LARGE CAP',  range: '$200M+',      color: '#f59e0b', bg: 'rgba(245,158,11,0.05)',  border: 'rgba(245,158,11,0.2)',   desc: 'Future: Macro flow signal (Phase 4)',        status: 'PHASE 4 →'        },
} as const

type TierKey = keyof typeof TIERS

const TIER_FILTERS = [
  { key: 'all',       label: 'ALL'        },
  { key: 'micro',     label: 'MICRO'      },
  { key: 'sweet_spot',label: 'SWEET SPOT' },
  { key: 'mid',       label: 'MID'        },
  { key: 'large',     label: 'LARGE'      },
] as const

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmtUsd(n: number | null | undefined): string {
  if (n == null) return '—'
  if (Math.abs(n) >= 1_000_000) return `$${(n / 1_000_000).toFixed(1)}M`
  if (Math.abs(n) >= 1_000)     return `$${(n / 1_000).toFixed(0)}K`
  return `$${n.toFixed(0)}`
}

function fmtAge(ts: string | null): string {
  if (!ts) return '—'
  const diff = (Date.now() - new Date(ts.includes('T') ? ts : ts + 'Z').getTime()) / 1000
  if (diff < 60)    return `${Math.floor(diff)}s ago`
  if (diff < 3600)  return `${Math.floor(diff / 60)}m ago`
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`
  return `${Math.floor(diff / 86400)}d ago`
}

function fmtRet(n: number | null): { text: string; color: string } {
  if (n == null) return { text: '—', color: 'var(--dim)' }
  return {
    text:  (n > 0 ? '+' : '') + n.toFixed(1) + '%',
    color: n > 0 ? 'var(--green)' : n < 0 ? 'var(--red)' : 'var(--muted)',
  }
}

function tierColor(tier: string | null): string {
  if (!tier || !(tier in TIERS)) return 'var(--muted)'
  return TIERS[tier as TierKey].color
}

function tierLabel(tier: string | null): string {
  if (!tier) return '?'
  if (tier === 'sweet_spot') return 'SWEET'
  return tier.toUpperCase()
}

// ── Learning Loop ─────────────────────────────────────────────────────────────

function LearningLoop({ stats }: { stats: WhaleStats | undefined }) {
  const outcomes   = stats?.outcomes ?? 0
  const phase      = stats?.phase ?? 1
  const nextMs     = stats?.next_milestone ?? 50
  const milestones = stats?.milestones ?? []
  const pct        = nextMs > 0 ? Math.min(100, (outcomes / nextMs) * 100) : 100

  return (
    <div>
      {/* Header row */}
      <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', marginBottom: 18 }}>
        <span className="section-label">LEARNING LOOP</span>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <span style={{
            fontFamily: 'JetBrains Mono, monospace', fontWeight: 700,
            fontSize: 11, color: '#a78bfa',
          }}>
            PHASE {phase}: {stats?.phase_label ?? '…'}
          </span>
          <span style={{ color: 'var(--dim)', fontSize: 10, fontFamily: 'JetBrains Mono, monospace' }}>
            {stats?.phase_desc}
          </span>
        </div>
      </div>

      {/* Milestone timeline */}
      <div style={{ position: 'relative', margin: '0 0 18px' }}>
        {/* Connecting line */}
        <div style={{
          position: 'absolute', top: 7, left: '4%', right: '4%',
          height: 1, background: 'rgba(167,139,250,0.15)',
        }} />
        {/* Progress fill */}
        {milestones.length > 1 && (
          <div style={{
            position: 'absolute', top: 7, left: '4%',
            width: `${Math.min(96, (phase - 1) / (milestones.length - 1) * 96)}%`,
            height: 1, background: 'rgba(167,139,250,0.5)',
          }} />
        )}
        {/* Dots + labels */}
        <div style={{ display: 'flex', justifyContent: 'space-between', position: 'relative' }}>
          {milestones.map((ms) => {
            const reached = outcomes >= ms.outcomes
            const active  = phase === ms.phase
            return (
              <div key={ms.label} style={{
                display: 'flex', flexDirection: 'column', alignItems: 'center',
                gap: 7, width: `${100 / milestones.length}%`,
              }}>
                {/* Dot */}
                <div style={{
                  width:        active ? 14 : 10,
                  height:       active ? 14 : 10,
                  borderRadius: '50%',
                  background:   reached ? '#a78bfa' : 'var(--bg2, #0a1520)',
                  border:       `2px solid ${reached ? '#a78bfa' : 'rgba(167,139,250,0.2)'}`,
                  boxShadow:    active ? '0 0 10px #a78bfa88' : 'none',
                  position:     'relative', zIndex: 1,
                  transition:   'all 0.2s',
                }} />
                {/* Phase label */}
                <span style={{
                  fontSize: 8, letterSpacing: '0.1em', textAlign: 'center',
                  fontFamily: 'JetBrains Mono, monospace', fontWeight: reached ? 700 : 400,
                  color: reached ? '#a78bfa' : 'rgba(167,139,250,0.35)',
                }}>
                  {ms.label}
                </span>
                {/* Outcome threshold */}
                <span style={{
                  fontSize: 8, color: 'var(--dim)', fontFamily: 'JetBrains Mono, monospace',
                  textAlign: 'center',
                }}>
                  {ms.outcomes === 0 ? 'START' : ms.outcomes}
                </span>
              </div>
            )
          })}
        </div>
      </div>

      {/* Progress bar to next milestone */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <span style={{ color: 'var(--muted)', fontSize: 10, fontFamily: 'JetBrains Mono, monospace', flexShrink: 0, minWidth: 100 }}>
          {outcomes} / {nextMs ?? '✓'} outcomes
        </span>
        <div className="mini-bar-track" style={{ flex: 1 }}>
          <div className="mini-bar-fill" style={{
            width: `${pct}%`,
            background: 'linear-gradient(90deg, #a78bfa, #7c3aed)',
          }} />
        </div>
        {nextMs && (
          <span style={{ color: 'var(--dim)', fontSize: 10, fontFamily: 'JetBrains Mono, monospace', flexShrink: 0 }}>
            → PHASE {phase + 1}
          </span>
        )}
        {!nextMs && (
          <span style={{ color: '#a78bfa', fontSize: 10, fontFamily: 'JetBrains Mono, monospace', fontWeight: 700, flexShrink: 0 }}>
            COMPLETE
          </span>
        )}
      </div>
    </div>
  )
}

// ── MC Tier Cards ─────────────────────────────────────────────────────────────

function TierCard({ tierKey, data }: { tierKey: TierKey; data: TierData | undefined }) {
  const cfg = TIERS[tierKey]
  const d   = data ?? { total: 0, in_range: 0, scanner_pass: 0, alerts_sent: 0, complete: 0, wr_24h: null, avg_return_24h: null, avg_return_1h: null }

  return (
    <div style={{
      flex: '1 1 180px', minWidth: 0,
      background: cfg.bg,
      border: `1px solid ${cfg.border}`,
      borderTop: `2px solid ${cfg.color}`,
      borderRadius: '0 0 12px 12px',
      padding: '14px 16px',
      display: 'flex', flexDirection: 'column', gap: 8,
      backdropFilter: 'blur(20px) saturate(160%)',
      WebkitBackdropFilter: 'blur(20px) saturate(160%)',
    }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <div>
          <div style={{ color: cfg.color, fontFamily: 'JetBrains Mono, monospace', fontWeight: 700, fontSize: 10, letterSpacing: '0.14em' }}>
            {cfg.label}
          </div>
          <div style={{ color: 'var(--dim)', fontFamily: 'JetBrains Mono, monospace', fontSize: 9, marginTop: 2 }}>
            {cfg.range}
          </div>
        </div>
        <span className="badge" style={{
          color: cfg.color,
          background: `${cfg.color}18`,
          border: `1px solid ${cfg.color}44`,
          fontSize: 8,
        }}>
          {cfg.status}
        </span>
      </div>

      {/* Divider */}
      <div style={{ height: 1, background: cfg.border }} />

      {/* Metrics */}
      <TierMetric label="alerts"      value={d.total} />
      <TierMetric label="scanner pass" value={d.scanner_pass}
        color={d.scanner_pass > 0 ? cfg.color : undefined} />
      <TierMetric
        label="24h win rate"
        value={d.wr_24h != null ? `${d.wr_24h}%` : d.complete < 5 ? `< 5 outcomes` : '—'}
        color={d.wr_24h != null ? (d.wr_24h >= 40 ? 'var(--green)' : d.wr_24h >= 25 ? 'var(--amber)' : 'var(--red)') : undefined}
      />
      <TierMetric
        label="avg 24h return"
        value={d.avg_return_24h != null ? (d.avg_return_24h > 0 ? `+${d.avg_return_24h}%` : `${d.avg_return_24h}%`) : '—'}
        color={d.avg_return_24h != null ? (d.avg_return_24h > 0 ? 'var(--green)' : 'var(--red)') : undefined}
      />

      {/* Desc */}
      <div style={{ color: 'var(--dim)', fontSize: 9, fontFamily: 'JetBrains Mono, monospace', marginTop: 2, lineHeight: 1.5 }}>
        {cfg.desc}
      </div>
    </div>
  )
}

function TierMetric({ label, value, color }: { label: string; value: string | number; color?: string }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 8 }}>
      <span style={{ color: 'var(--muted)', fontSize: 10, fontFamily: 'JetBrains Mono, monospace' }}>{label}</span>
      <span style={{ color: color ?? 'var(--text2)', fontFamily: 'JetBrains Mono, monospace', fontWeight: 600, fontSize: 11 }}>
        {value}
      </span>
    </div>
  )
}

// ── Alert Feed ────────────────────────────────────────────────────────────────

function GateBadge({ alert }: { alert: WhaleAlert }) {
  if (!alert.mc_in_range) return <span style={{ color: 'var(--dim)', fontSize: 10 }}>out of range</span>
  if (alert.scanner_pass === null) return <span style={{ color: 'var(--dim)', fontSize: 10 }}>—</span>
  if (alert.scanner_pass === 1)    return <span style={{ color: 'var(--green)', fontSize: 10, fontWeight: 600 }}>✓ pass</span>
  const rug = alert.scanner_rug_label
  if (rug === 'DANGER' || rug === 'RUGGED')
    return <span style={{ color: 'var(--red)', fontSize: 10, fontWeight: 600 }}>✗ {rug}</span>
  return <span style={{ color: 'var(--amber)', fontSize: 10, fontWeight: 600 }}>✗ fail</span>
}

function RetCell({ val }: { val: number | null }) {
  const { text, color } = fmtRet(val)
  return <span style={{ color, fontFamily: 'JetBrains Mono, monospace', fontSize: 11 }}>{text}</span>
}

// ── Cross-Agent Signal Bus ─────────────────────────────────────────────────────

function CrossSignalPanel({ stats }: { stats: WhaleStats | undefined }) {
  const phase    = stats?.phase ?? 1
  const signals  = stats?.cross_signals ?? []
  const unlockAt = 100

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 14 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <span className="section-label">CROSS-AGENT SIGNAL BUS</span>
          <span className="badge" style={{
            color: phase >= 3 ? '#00d48a' : 'var(--dim)',
            background: phase >= 3 ? 'rgba(0,212,138,0.1)' : 'rgba(100,100,100,0.1)',
            border: `1px solid ${phase >= 3 ? 'rgba(0,212,138,0.25)' : 'rgba(100,100,100,0.2)'}`,
            fontSize: 9,
          }}>
            {phase >= 3 ? 'ACTIVE' : `PHASE 3 UNLOCK`}
          </span>
        </div>
        <span style={{ color: 'var(--dim)', fontSize: 10, fontFamily: 'JetBrains Mono, monospace' }}>
          {signals.length > 0 ? `${signals.length} signals` : 'no signals yet'}
        </span>
      </div>

      {phase < 3 ? (
        <div style={{
          border: '1px dashed rgba(167,139,250,0.2)',
          borderRadius: 8, padding: '20px',
          textAlign: 'center', background: 'rgba(167,139,250,0.02)',
        }}>
          <div style={{ color: '#a78bfa', fontFamily: 'JetBrains Mono, monospace', fontSize: 10, fontWeight: 700, marginBottom: 8 }}>
            LOCKED — PHASE 3 ({unlockAt} outcomes)
          </div>
          <div style={{ color: 'var(--dim)', fontFamily: 'JetBrains Mono, monospace', fontSize: 10, lineHeight: 1.7 }}>
            Once 100 complete outcomes are collected, the signal bus activates.<br />
            Sweet spot whale buys that pass the scanner will be cross-confirmed<br />
            with the Memecoin Scanner as double-conviction entries.
          </div>
          <div style={{ marginTop: 14, display: 'flex', flexDirection: 'column', gap: 8 }}>
            {[
              { tier: 'sweet_spot', target: 'memecoin_scanner', phase: 3, desc: 'Whale buy + scanner pass → double-confirm memecoin entry' },
              { tier: 'mid/large',  target: 'spot_accumulator', phase: 4, desc: 'Mid/large whale flow → weight Spot basket allocation higher' },
            ].map(row => (
              <div key={row.tier} style={{
                display: 'flex', alignItems: 'center', gap: 10,
                background: 'rgba(167,139,250,0.04)', borderRadius: 6,
                padding: '8px 12px', justifyContent: 'space-between',
              }}>
                <span style={{ color: tierColor(row.tier === 'sweet_spot' ? 'sweet_spot' : 'mid'), fontFamily: 'JetBrains Mono, monospace', fontSize: 10, fontWeight: 700 }}>
                  {row.tier.toUpperCase()}
                </span>
                <span style={{ color: 'var(--dim)', fontSize: 10, fontFamily: 'JetBrains Mono, monospace' }}>→</span>
                <span style={{ color: 'var(--text2)', fontFamily: 'JetBrains Mono, monospace', fontSize: 10 }}>
                  {row.target}
                </span>
                <span style={{ color: '#a78bfa', fontFamily: 'JetBrains Mono, monospace', fontSize: 9 }}>
                  PHASE {row.phase}
                </span>
                <span style={{ color: 'var(--dim)', fontFamily: 'JetBrains Mono, monospace', fontSize: 9, flex: 1, textAlign: 'right' }}>
                  {row.desc}
                </span>
              </div>
            ))}
          </div>
        </div>
      ) : signals.length === 0 ? (
        <div className="empty-state" style={{ padding: 20, textAlign: 'center', borderRadius: 8 }}>
          <div style={{ fontSize: 11, fontFamily: 'JetBrains Mono, monospace' }}>
            Signal bus active — no signals fired yet
          </div>
        </div>
      ) : (
        <div className="pos-table-wrap">
        <table className="data-table" style={{ minWidth: 560 }}>
          <thead>
            <tr>
              <th>TIME</th>
              <th>TOKEN</th>
              <th>TIER</th>
              <th>→ TARGET</th>
              <th>BUY</th>
              <th>SCORE</th>
              <th>STATUS</th>
            </tr>
          </thead>
          <tbody>
            {signals.map(s => (
              <tr key={s.id}>
                <td style={{ color: 'var(--dim)', fontSize: 10, fontFamily: 'JetBrains Mono, monospace' }}>{fmtAge(s.ts_utc)}</td>
                <td style={{ color: 'var(--text)', fontWeight: 700, fontFamily: 'JetBrains Mono, monospace' }}>${s.token_symbol ?? '?'}</td>
                <td>
                  <span style={{ color: tierColor(s.mc_tier), fontFamily: 'JetBrains Mono, monospace', fontSize: 10, fontWeight: 600 }}>
                    {tierLabel(s.mc_tier)}
                  </span>
                </td>
                <td style={{ color: 'var(--blue)', fontFamily: 'JetBrains Mono, monospace', fontSize: 10 }}>{s.target}</td>
                <td style={{ color: 'var(--text2)', fontFamily: 'JetBrains Mono, monospace' }}>{fmtUsd(s.buy_amount_usd)}</td>
                <td style={{ color: 'var(--text2)', fontFamily: 'JetBrains Mono, monospace' }}>{s.scanner_score?.toFixed(0) ?? '—'}</td>
                <td>
                  <span style={{
                    color: s.consumed ? 'var(--green)' : 'var(--amber)',
                    fontSize: 10, fontFamily: 'JetBrains Mono, monospace', fontWeight: 600,
                  }}>
                    {s.consumed ? '✓ CONSUMED' : '● PENDING'}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        </div>
      )}
    </div>
  )
}

// ── Main ──────────────────────────────────────────────────────────────────────

export function WhalePage() {
  const [selectedTier, setSelectedTier] = useState<string>('all')

  const statsQ = useQuery<WhaleStats>({
    queryKey: ['whale-stats'],
    queryFn:  () => api.get('/whale-watch/stats').then(r => r.data),
    refetchInterval: 60_000,
    staleTime: 30_000,
  })

  const alertsQ = useQuery<WhaleAlert[]>({
    queryKey: ['whale-alerts', selectedTier],
    queryFn:  () => api.get(`/whale-watch/alerts?limit=30&tier=${selectedTier}`).then(r => r.data),
    refetchInterval: 30_000,
    staleTime: 15_000,
  })

  const s = statsQ.data

  return (
    <div style={{
      maxWidth: 1200, margin: '0 auto',
      padding: '20px 20px',
      display: 'flex', flexDirection: 'column', gap: 14,
    }}>

      {/* ── Page header ──────────────────────────────────────────────────── */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <span style={{
          color: '#a78bfa', fontFamily: 'JetBrains Mono, monospace',
          fontWeight: 700, fontSize: 13, letterSpacing: '0.14em',
        }}>
          WHALE WATCH
        </span>
        <span className="badge" style={{
          color: '#a78bfa', background: 'rgba(167,139,250,0.12)',
          border: '1px solid rgba(167,139,250,0.3)', fontSize: 9,
        }}>
          OBS MODE
        </span>
        <span style={{ color: 'var(--dim)', fontFamily: 'JetBrains Mono, monospace', fontSize: 10 }}>
          @whalewatchsolana · no auto-buy · cross-agent signal bus
        </span>
        <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 16 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <span style={{ color: 'var(--dim)', fontSize: 10, fontFamily: 'JetBrains Mono, monospace' }}>total</span>
            <span style={{ color: 'var(--text2)', fontFamily: 'JetBrains Mono, monospace', fontWeight: 700, fontSize: 12 }}>{s?.total ?? '—'}</span>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <span style={{ color: 'var(--dim)', fontSize: 10, fontFamily: 'JetBrains Mono, monospace' }}>scanner pass</span>
            <span style={{ color: s?.scanner_pass ? 'var(--green)' : 'var(--dim)', fontFamily: 'JetBrains Mono, monospace', fontWeight: 700, fontSize: 12 }}>{s?.scanner_pass ?? '—'}</span>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <span style={{ color: 'var(--dim)', fontSize: 10, fontFamily: 'JetBrains Mono, monospace' }}>last</span>
            <span style={{ color: 'var(--text2)', fontFamily: 'JetBrains Mono, monospace', fontSize: 10 }}>{fmtAge(s?.last_ts ?? null)}</span>
          </div>
        </div>
      </div>

      {/* ── Learning Loop ─────────────────────────────────────────────────── */}
      <div className="card">
        <LearningLoop stats={s} />
      </div>

      {/* ── MC Tier Cards ─────────────────────────────────────────────────── */}
      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
        {(['micro', 'sweet_spot', 'mid', 'large'] as TierKey[]).map(k => (
          <TierCard key={k} tierKey={k} data={s?.tiers[k]} />
        ))}
      </div>

      {/* ── Alert Feed ────────────────────────────────────────────────────── */}
      <div className="card">

        {/* Feed header + tier filter */}
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 14 }}>
          <span className="section-label">ALERT FEED</span>
          <div style={{ display: 'flex', gap: 6 }}>
            {TIER_FILTERS.map(f => {
              const active = selectedTier === f.key
              const col = f.key !== 'all' && f.key in TIERS
                ? TIERS[f.key as TierKey].color
                : '#a78bfa'
              return (
                <button
                  key={f.key}
                  onClick={() => setSelectedTier(f.key)}
                  style={{
                    background:   active ? `${col}18` : 'transparent',
                    border:       `1px solid ${active ? col + '55' : 'var(--border, #1e2d3d)'}`,
                    borderRadius: 4,
                    color:        active ? col : 'var(--dim)',
                    cursor:       'pointer',
                    fontFamily:   'JetBrains Mono, monospace',
                    fontSize: 9, fontWeight: active ? 700 : 400,
                    letterSpacing: '0.1em',
                    padding:      '3px 8px',
                    transition:   'all 0.15s',
                  }}
                >
                  {f.label}
                </button>
              )
            })}
          </div>
        </div>

        {alertsQ.isLoading ? (
          <div style={{ color: 'var(--dim)', fontSize: 11, padding: '12px 0', fontFamily: 'JetBrains Mono, monospace' }}>
            loading…
          </div>
        ) : !alertsQ.data || alertsQ.data.length === 0 ? (
          <div className="empty-state" style={{ padding: '24px', textAlign: 'center', borderRadius: 8 }}>
            <div style={{ fontSize: 11, fontFamily: 'JetBrains Mono, monospace', marginBottom: 6 }}>
              no alerts yet
            </div>
            <div style={{ fontSize: 10, color: 'var(--dim)', fontFamily: 'JetBrains Mono, monospace' }}>
              listening to @whalewatchsolana
            </div>
          </div>
        ) : (
          <div className="pos-table-wrap">
          <table className="data-table" style={{ minWidth: 700 }}>
            <thead>
              <tr>
                <th>TIME</th>
                <th>TYPE</th>
                <th>TOKEN</th>
                <th>TIER</th>
                <th>BUY</th>
                <th>MARKET CAP</th>
                <th>GATE</th>
                <th style={{ textAlign: 'right' }}>1H</th>
                <th style={{ textAlign: 'right' }}>4H</th>
                <th style={{ textAlign: 'right' }}>24H</th>
              </tr>
            </thead>
            <tbody>
              {alertsQ.data.map(a => {
                const rowBg = a.alert_sent
                  ? 'rgba(0,212,138,0.04)'
                  : a.scanner_pass === 0
                  ? 'rgba(239,68,68,0.03)'
                  : 'transparent'
                const leftBorder = a.alert_sent
                  ? '2px solid rgba(0,212,138,0.4)'
                  : a.scanner_pass === 0
                  ? '2px solid rgba(239,68,68,0.25)'
                  : '2px solid transparent'

                return (
                  <tr key={a.id} style={{ background: rowBg, borderLeft: leftBorder }}>
                    <td style={{ color: 'var(--dim)', fontSize: 10, fontFamily: 'JetBrains Mono, monospace', whiteSpace: 'nowrap' }}>
                      {fmtAge(a.ts_utc)}
                    </td>
                    <td>
                      <span style={{ color: a.alert_type === 'KOL' ? 'var(--blue)' : '#a78bfa', fontSize: 10, fontFamily: 'JetBrains Mono, monospace' }}>
                        {a.alert_type === 'KOL' ? `KOL${a.kol_name ? ' @' + a.kol_name : ''}` : '🐋 WHALE'}
                      </span>
                    </td>
                    <td>
                      <span style={{ color: 'var(--text)', fontWeight: 700, fontFamily: 'JetBrains Mono, monospace' }}>
                        ${a.token_symbol ?? '?'}
                      </span>
                    </td>
                    <td>
                      <span style={{
                        color: tierColor(a.mc_tier), fontFamily: 'JetBrains Mono, monospace',
                        fontSize: 10, fontWeight: 600,
                      }}>
                        {tierLabel(a.mc_tier)}
                      </span>
                    </td>
                    <td style={{ color: 'var(--text2)', fontFamily: 'JetBrains Mono, monospace' }}>
                      {fmtUsd(a.buy_amount_usd)}
                    </td>
                    <td style={{
                      color: a.mc_in_range ? 'var(--text2)' : 'var(--dim)',
                      fontFamily: 'JetBrains Mono, monospace',
                    }}>
                      {fmtUsd(a.market_cap_usd)}
                    </td>
                    <td><GateBadge alert={a} /></td>
                    <td style={{ textAlign: 'right' }}><RetCell val={a.return_1h_pct} /></td>
                    <td style={{ textAlign: 'right' }}><RetCell val={a.return_4h_pct} /></td>
                    <td style={{ textAlign: 'right' }}><RetCell val={a.return_24h_pct} /></td>
                  </tr>
                )
              })}
            </tbody>
          </table>
          </div>
        )}
      </div>

      {/* ── Cross-Agent Signal Bus ─────────────────────────────────────────── */}
      <div className="card">
        <CrossSignalPanel stats={s} />
      </div>

    </div>
  )
}
