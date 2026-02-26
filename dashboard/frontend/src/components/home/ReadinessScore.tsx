/**
 * ReadinessScore — "Is this engine ready for live trading?" card.
 *
 * Composite score (0–100) based on:
 *   • Sample size (n outcomes collected)
 *   • Win rate (needs ≥ 52%)
 *   • Expectancy (positive after fees)
 *   • Max drawdown resilience (< 30%)
 *   • Horizon consistency (4h vs 24h alignment)
 *
 * Shows gate status, component scores, and a readiness verdict.
 * Links to /performance for full equity curve.
 */
import { useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api } from '../../api'

// ─── Types ────────────────────────────────────────────────────────────────────

interface ReadinessData {
  score: number
  status: 'READY' | 'PROMISING' | 'BUILDING' | 'NOT_READY'
  color: string
  gates: {
    sample_size: boolean
    win_rate: boolean
    drawdown: boolean
    expectancy: boolean
    avg_net_return: boolean
  }
  gates_passed: number
  gates_total: number
  components: {
    sample_size: number
    win_rate: number
    expectancy: number
    drawdown: number
    consistency: number
  }
  metrics: {
    n_outcomes: number
    win_rate_4h: number
    win_rate_24h: number
    expectancy_pct: number
    max_drawdown_pct: number
    avg_net_ret: number
  }
  lookback_days: number
  error?: string
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

const MONO: React.CSSProperties = { fontFamily: 'JetBrains Mono, monospace' }

function fmtPct(v: number | null | undefined, plus = true): string {
  if (v == null) return '—'
  const s = plus && v > 0 ? '+' : ''
  return `${s}${v.toFixed(1)}%`
}

// ─── Main Component ───────────────────────────────────────────────────────────

export function ReadinessScore() {
  const navigate = useNavigate()

  const { data, isLoading } = useQuery<ReadinessData>({
    queryKey: ['readiness-score'],
    queryFn: () => api.get('/performance/readiness-score?lookback_days=30').then(r => r.data),
    refetchInterval: 300_000,
    staleTime: 240_000,
  })

  if (isLoading) {
    return (
      <div className="card" style={{ padding: '14px 16px' }}>
        <div style={{ fontSize: 9, color: 'var(--dim)', ...MONO, letterSpacing: '0.18em', textTransform: 'uppercase', marginBottom: 10 }}>
          Readiness
        </div>
        <div style={{ fontSize: 10, color: 'var(--dim)', ...MONO }}>Calculating…</div>
      </div>
    )
  }

  if (!data) return null

  const scoreColor = data.score >= 80 ? 'var(--green)'
    : data.score >= 60 ? '#a3e635'
    : data.score >= 40 ? 'var(--amber)'
    : 'var(--red)'

  const scoreRad = Math.round(data.score)

  const gateList = [
    { key: 'sample_size',    label: 'Sample Size',    ok: data.gates.sample_size,    detail: `${data.metrics.n_outcomes} outcomes` },
    { key: 'win_rate',       label: 'Win Rate',        ok: data.gates.win_rate,        detail: `${data.metrics.win_rate_4h.toFixed(0)}% 4h` },
    { key: 'expectancy',     label: 'Expectancy',      ok: data.gates.expectancy,      detail: fmtPct(data.metrics.expectancy_pct) },
    { key: 'drawdown',       label: 'Max Drawdown',    ok: data.gates.drawdown,        detail: fmtPct(data.metrics.max_drawdown_pct) },
    { key: 'avg_net_return', label: 'Avg Net Return',  ok: data.gates.avg_net_return,  detail: fmtPct(data.metrics.avg_net_ret) },
  ]

  return (
    <div
      className="card"
      onClick={() => navigate('/performance')}
      style={{
        padding: '14px 16px',
        cursor: 'pointer',
        transition: 'border-color 0.15s',
      }}
      onMouseEnter={e => (e.currentTarget.style.borderColor = 'rgba(255,255,255,0.14)')}
      onMouseLeave={e => (e.currentTarget.style.borderColor = '')}
    >
      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 12 }}>
        <div style={{ fontSize: 9, fontWeight: 600, letterSpacing: '0.18em', color: 'var(--dim)', ...MONO, textTransform: 'uppercase' }}>
          Live Readiness
        </div>
        <div style={{ fontSize: 9, color: 'var(--blue)', ...MONO }}>
          {data.lookback_days}d →
        </div>
      </div>

      {/* Score ring / gauge */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 14, marginBottom: 14 }}>
        {/* Arc visualization */}
        <div style={{ position: 'relative', width: 56, height: 56, flexShrink: 0 }}>
          <svg viewBox="0 0 56 56" width="56" height="56">
            {/* Background track */}
            <circle
              cx="28" cy="28" r="22"
              fill="none"
              stroke="rgba(255,255,255,0.07)"
              strokeWidth="5"
            />
            {/* Score arc */}
            <circle
              cx="28" cy="28" r="22"
              fill="none"
              stroke={scoreColor}
              strokeWidth="5"
              strokeLinecap="round"
              strokeDasharray={`${(scoreRad / 100) * 138.2} 138.2`}
              transform="rotate(-90 28 28)"
              opacity={0.85}
            />
          </svg>
          <div style={{
            position: 'absolute', inset: 0,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            flexDirection: 'column',
          }}>
            <span style={{ fontSize: 15, fontWeight: 800, color: scoreColor, ...MONO, lineHeight: 1 }}>
              {scoreRad}
            </span>
          </div>
        </div>

        {/* Status + gates passed */}
        <div>
          <div style={{ fontSize: 14, fontWeight: 800, color: scoreColor, ...MONO, letterSpacing: '0.02em' }}>
            {data.status.replace('_', ' ')}
          </div>
          <div style={{ fontSize: 9, color: 'var(--dim)', ...MONO, marginTop: 4 }}>
            {data.gates_passed}/{data.gates_total} gates
          </div>
        </div>
      </div>

      {/* Gate checks */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
        {gateList.map(g => (
          <div key={g.key} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <span style={{ fontSize: 9, color: g.ok ? 'var(--green)' : 'var(--red)', ...MONO }}>
                {g.ok ? '✓' : '✗'}
              </span>
              <span style={{ fontSize: 9, color: g.ok ? 'var(--text)' : 'var(--dim)', ...MONO }}>
                {g.label}
              </span>
            </div>
            <span style={{
              fontSize: 9,
              color: g.ok ? 'var(--green)' : 'var(--muted)',
              ...MONO,
            }}>
              {g.detail}
            </span>
          </div>
        ))}
      </div>

      {/* Component bar mini-chart */}
      <div style={{ marginTop: 12, display: 'flex', gap: 3, alignItems: 'flex-end', height: 20 }}>
        {Object.entries(data.components ?? {}).map(([key, val]) => {
          const maxVal = 20
          const pct = Math.max(2, (val / maxVal) * 100)
          return (
            <div
              key={key}
              title={`${key}: ${val.toFixed(1)}/20`}
              style={{
                flex: 1,
                height: `${pct}%`,
                background: val >= 14 ? 'var(--green)' : val >= 8 ? 'var(--amber)' : 'rgba(240,79,79,0.6)',
                borderRadius: '2px 2px 0 0',
                opacity: 0.8,
              }}
            />
          )
        })}
      </div>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 3 }}>
        {['Size', 'WR', 'Exp', 'DD', 'Con'].map(label => (
          <span key={label} style={{ fontSize: 7, color: 'var(--dim)', ...MONO, flex: 1, textAlign: 'center' }}>
            {label}
          </span>
        ))}
      </div>
    </div>
  )
}
