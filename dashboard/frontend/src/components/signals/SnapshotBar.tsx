/**
 * SnapshotBar — compact ticker-tape data strip.
 * Horizontal row of labeled stat chips separated by dividers.
 */
import { useQuery } from '@tanstack/react-query'
import { api } from '../../api'

interface SnapshotData {
  regime: {
    regime_score:   number
    regime_label:   string
    sol_change_24h: number
  }
  risk: {
    mode:            string
    emoji:           string
    paused:          boolean
    size_multiplier: number
  }
  sol_price:  number | null
  perps: {
    pnl:        number
    leverage:   number
    mark_price: number
  } | null
  fear_greed: {
    value:          string | null
    classification: string | null
  }
  top_picks: {
    symbol:     string
    score:      number
    change_24h: number
  }[]
  open_positions: { symbol: string }[]
}

function regimeColor(score: number) {
  if (score >= 50) return 'var(--green)'
  if (score >= 25) return 'var(--amber)'
  return 'var(--red)'
}

function fgColor(val: string | null) {
  if (!val) return 'var(--muted)'
  const n = parseInt(val)
  if (n >= 70) return 'var(--green)'
  if (n >= 50) return '#4ade80'
  if (n >= 30) return 'var(--amber)'
  return 'var(--red)'
}

function modeColor(mode: string) {
  if (mode === 'DEFENSIVE') return 'var(--red)'
  if (mode === 'CAUTIOUS')  return 'var(--amber)'
  return 'var(--green)'
}

/** A single labeled data chip */
function Stat({
  label, value, valueColor, sub,
}: {
  label: string
  value: string
  valueColor?: string
  sub?: string
}) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 1, flexShrink: 0 }}>
      <span style={{
        fontSize: 8.5, color: 'var(--dim)', letterSpacing: '0.14em',
        fontFamily: 'JetBrains Mono, monospace', textTransform: 'uppercase',
      }}>
        {label}
      </span>
      <span style={{
        fontSize: 11.5, fontWeight: 600,
        color: valueColor || 'var(--text)',
        fontFamily: 'JetBrains Mono, monospace',
        lineHeight: 1,
      }}>
        {value}
        {sub && <span style={{ color: 'var(--muted)', fontWeight: 400, fontSize: 10, marginLeft: 4 }}>{sub}</span>}
      </span>
    </div>
  )
}

function Sep() {
  return <div style={{ width: 1, height: 24, background: 'var(--border)', flexShrink: 0 }} />
}

export function SnapshotBar() {
  const { data } = useQuery<SnapshotData>({
    queryKey: ['snapshot'],
    queryFn: () => api.get('/snapshot').then(r => r.data),
    refetchInterval: 60_000,
    staleTime:       50_000,
  })

  if (!data) return null

  const { regime, risk, sol_price, perps, fear_greed: fg, top_picks, open_positions } = data
  const regimeScore = regime?.regime_score ?? 0
  const regimeLabel = regime?.regime_label ?? '—'
  const solChange   = regime?.sol_change_24h ?? 0

  return (
    <div style={{
      background: 'var(--surface)',
      border: '1px solid var(--border)',
      borderRadius: 6,
      padding: '8px 14px',
      marginBottom: 16,
      display: 'flex',
      alignItems: 'center',
      gap: 16,
      overflowX: 'auto',
      flexWrap: 'nowrap',
      scrollbarWidth: 'none',
    }}>

      {/* Regime */}
      <Stat
        label="Regime"
        value={regimeLabel}
        valueColor={regimeColor(regimeScore)}
        sub={`(${regimeScore.toFixed(0)})`}
      />

      <Sep />

      {/* SOL */}
      <Stat
        label="SOL"
        value={sol_price ? `$${sol_price.toFixed(2)}` : '—'}
        sub={solChange !== 0 ? `${solChange >= 0 ? '+' : ''}${solChange.toFixed(1)}%` : undefined}
        valueColor="var(--text)"
      />

      {/* Perps PnL */}
      {perps && (
        <>
          <Sep />
          <Stat
            label="Perps PnL"
            value={`${perps.pnl >= 0 ? '+' : ''}$${perps.pnl.toFixed(0)}`}
            valueColor={perps.pnl >= 0 ? 'var(--green)' : 'var(--red)'}
            sub={`${perps.leverage.toFixed(1)}×`}
          />
        </>
      )}

      <Sep />

      {/* Fear & Greed */}
      <Stat
        label="Fear & Greed"
        value={fg?.value ?? '—'}
        valueColor={fgColor(fg?.value ?? null)}
        sub={fg?.classification ?? undefined}
      />

      <Sep />

      {/* Risk mode */}
      <Stat
        label="Risk Mode"
        value={risk?.mode ?? '—'}
        valueColor={risk ? modeColor(risk.mode) : 'var(--muted)'}
        sub={risk?.paused ? '⏸ paused' : `${Math.round((risk?.size_multiplier ?? 1) * 100)}% size`}
      />

      {/* Top picks */}
      {top_picks && top_picks.length > 0 && (
        <>
          <Sep />
          <div style={{ display: 'flex', flexDirection: 'column', gap: 1, flexShrink: 0 }}>
            <span style={{
              fontSize: 8.5, color: 'var(--dim)', letterSpacing: '0.14em',
              fontFamily: 'JetBrains Mono, monospace', textTransform: 'uppercase',
            }}>
              Top Picks
            </span>
            <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
              {top_picks.slice(0, 3).map((p, i) => (
                <span key={p.symbol} style={{
                  fontSize: 11, fontWeight: 600,
                  fontFamily: 'JetBrains Mono, monospace',
                  color: i === 0 ? 'var(--green)' : i === 1 ? '#4ade80' : 'var(--text)',
                }}>
                  ${p.symbol}
                  <span style={{ color: 'var(--muted)', fontWeight: 400, fontSize: 9.5 }}> {p.score.toFixed(0)}</span>
                </span>
              ))}
            </div>
          </div>
        </>
      )}

      {/* Open positions */}
      {open_positions && open_positions.length > 0 && (
        <>
          <Sep />
          <div style={{ display: 'flex', flexDirection: 'column', gap: 2, flexShrink: 0 }}>
            <span style={{
              fontSize: 8.5, color: 'var(--dim)', letterSpacing: '0.14em',
              fontFamily: 'JetBrains Mono, monospace', textTransform: 'uppercase',
            }}>
              Open ({open_positions.length})
            </span>
            <div style={{ display: 'flex', gap: 5, alignItems: 'center' }}>
              {open_positions.slice(0, 5).map(p => (
                <span key={p.symbol} style={{
                  fontSize: 9.5, padding: '1px 6px', borderRadius: 3,
                  background: 'rgba(0, 212, 138, 0.08)',
                  color: 'var(--green)',
                  border: '1px solid rgba(0, 212, 138, 0.2)',
                  fontFamily: 'JetBrains Mono, monospace',
                  fontWeight: 600,
                }}>
                  ${p.symbol}
                </span>
              ))}
              {open_positions.length > 5 && (
                <span style={{ fontSize: 9.5, color: 'var(--muted)', fontFamily: 'JetBrains Mono, monospace' }}>
                  +{open_positions.length - 5}
                </span>
              )}
            </div>
          </div>
        </>
      )}
    </div>
  )
}
