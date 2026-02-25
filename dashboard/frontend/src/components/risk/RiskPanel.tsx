/**
 * RiskPanel — Risk mode overview + cooldowns/blacklist detail table.
 * Route: /risk
 */
import { useQuery } from '@tanstack/react-query'
import { api } from '../../api'
import type { RiskState } from '../../types'
import { LoadingSpinner } from '../shared/LoadingSpinner'
import { EmptyState } from '../shared/EmptyState'

interface SymbolControl {
  symbol: string
  type: 'BLACKLIST' | 'COOLDOWN'
  until: string
  mins_remaining: number | null
  reason: string | null
  updated_ts_utc: string | null
}

function fmtMins(mins: number | null): string {
  if (mins == null) return '—'
  if (mins < 60) return `${mins}m`
  const h = Math.floor(mins / 60)
  const m = mins % 60
  return m > 0 ? `${h}h ${m}m` : `${h}h`
}

function timeAgo(ts: string | null): string {
  if (!ts) return '—'
  const diff = Date.now() - new Date(ts + (ts.endsWith('Z') ? '' : 'Z')).getTime()
  const s = Math.floor(diff / 1000)
  if (s < 60) return `${s}s ago`
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`
  return `${Math.floor(s / 86400)}d ago`
}

export function RiskPanel() {
  const riskQ = useQuery<RiskState>({
    queryKey: ['risk-state'],
    queryFn: () => api.get('/risk/state').then(r => r.data),
    refetchInterval: 30_000,
  })

  const controlsQ = useQuery<SymbolControl[]>({
    queryKey: ['symbol-controls-detail'],
    queryFn: () => api.get('/risk/symbol-controls/detail').then(r => r.data),
    refetchInterval: 60_000,
  })

  const risk = riskQ.data
  const controls = controlsQ.data || []
  const blacklisted = controls.filter(c => c.type === 'BLACKLIST')
  const cooldowns   = controls.filter(c => c.type === 'COOLDOWN')

  const modeColor = (mode?: string) => {
    if (mode === 'NORMAL')    return 'var(--green)'
    if (mode === 'CAUTIOUS')  return 'var(--amber)'
    if (mode === 'DEFENSIVE') return 'var(--red)'
    return 'var(--muted)'
  }

  return (
    <div>
      <h2 style={{ fontSize: 14, fontWeight: 700, letterSpacing: '0.06em', marginBottom: 16 }}>
        🛡️ RISK STATE
      </h2>

      {/* Risk mode card */}
      {riskQ.isLoading ? <LoadingSpinner /> : risk && (
        <div className="card" style={{ marginBottom: 16 }}>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', gap: '10px 8px' }}>
            <div>
              <div style={{ color: 'var(--muted)', fontSize: 10, marginBottom: 3 }}>MODE</div>
              <div style={{ fontSize: 18, fontWeight: 700, color: modeColor(risk.mode) }}>
                {risk.emoji} {risk.mode}
              </div>
            </div>
            <div>
              <div style={{ color: 'var(--muted)', fontSize: 10, marginBottom: 3 }}>STREAK</div>
              <div style={{ fontSize: 16, fontWeight: 700 }}>{risk.streak}</div>
            </div>
            <div>
              <div style={{ color: 'var(--muted)', fontSize: 10, marginBottom: 3 }}>THRESHOLD Δ</div>
              <div style={{ fontSize: 16, fontWeight: 700, color: risk.threshold_delta > 0 ? 'var(--amber)' : 'var(--green)' }}>
                {risk.threshold_delta >= 0 ? '+' : ''}{risk.threshold_delta}
              </div>
            </div>
            <div>
              <div style={{ color: 'var(--muted)', fontSize: 10, marginBottom: 3 }}>SIZE MULT</div>
              <div style={{ fontSize: 16, fontWeight: 700 }}>{risk.size_multiplier.toFixed(2)}x</div>
            </div>
            <div>
              <div style={{ color: 'var(--muted)', fontSize: 10, marginBottom: 3 }}>MIN CONF</div>
              <div style={{ fontSize: 16, fontWeight: 700 }}>{risk.min_confidence || '—'}</div>
            </div>
          </div>

          {risk.paused && risk.pause && (
            <div style={{
              marginTop: 12, padding: '8px 12px', borderRadius: 3,
              background: '#3a1a1a', border: '1px solid var(--red)', fontSize: 11,
            }}>
              <span style={{ color: 'var(--red)', fontWeight: 700 }}>⏸ PAUSED</span>
              {risk.pause.reason && (
                <span style={{ color: 'var(--muted)', marginLeft: 8 }}>{risk.pause.reason}</span>
              )}
              {risk.pause.pause_until && (
                <span style={{ color: 'var(--muted)', marginLeft: 8 }}>
                  until {new Date(risk.pause.pause_until).toLocaleString()}
                </span>
              )}
            </div>
          )}
        </div>
      )}

      {/* Controls section */}
      {controlsQ.isLoading ? <LoadingSpinner /> : (
        <>
          {/* Summary counts */}
          <div style={{ display: 'flex', gap: 12, marginBottom: 16 }}>
            <div className="card" style={{ flex: 1, textAlign: 'center' }}>
              <div style={{ color: 'var(--muted)', fontSize: 10 }}>BLACKLISTED</div>
              <div style={{
                fontSize: 24, fontWeight: 700,
                color: blacklisted.length > 0 ? 'var(--red)' : 'var(--muted)',
              }}>
                {blacklisted.length}
              </div>
            </div>
            <div className="card" style={{ flex: 1, textAlign: 'center' }}>
              <div style={{ color: 'var(--muted)', fontSize: 10 }}>ON COOLDOWN</div>
              <div style={{
                fontSize: 24, fontWeight: 700,
                color: cooldowns.length > 0 ? 'var(--amber)' : 'var(--muted)',
              }}>
                {cooldowns.length}
              </div>
            </div>
            <div className="card" style={{ flex: 1, textAlign: 'center' }}>
              <div style={{ color: 'var(--muted)', fontSize: 10 }}>TOTAL BLOCKED</div>
              <div style={{
                fontSize: 24, fontWeight: 700,
                color: controls.length > 0 ? 'var(--text)' : 'var(--muted)',
              }}>
                {controls.length}
              </div>
            </div>
          </div>

          {controls.length === 0 ? (
            <EmptyState message="No active cooldowns or blacklists." />
          ) : (
            <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
              <div style={{
                display: 'grid',
                gridTemplateColumns: '80px 90px 80px 1fr 90px',
                gap: '0 8px',
                padding: '8px 12px',
                background: 'var(--surface2)',
                fontSize: 10, color: 'var(--muted)', fontWeight: 700, letterSpacing: '0.05em',
                borderBottom: '1px solid var(--border)',
              }}>
                <span>SYMBOL</span>
                <span>TYPE</span>
                <span>REMAINING</span>
                <span>REASON</span>
                <span style={{ textAlign: 'right' }}>UPDATED</span>
              </div>

              {controls.map((c, i) => (
                <div key={`${c.symbol}-${c.type}`} style={{
                  display: 'grid',
                  gridTemplateColumns: '80px 90px 80px 1fr 90px',
                  gap: '0 8px',
                  padding: '8px 12px',
                  borderBottom: i < controls.length - 1 ? '1px solid var(--border)' : undefined,
                  alignItems: 'center',
                }}>
                  <span style={{ fontWeight: 700, fontSize: 12 }}>${c.symbol}</span>
                  <span style={{
                    display: 'inline-block',
                    padding: '1px 6px', borderRadius: 3, fontSize: 10, fontWeight: 700,
                    background: c.type === 'BLACKLIST' ? '#3a1a1a' : '#3a2a1a',
                    color: c.type === 'BLACKLIST' ? 'var(--red)' : 'var(--amber)',
                    border: `1px solid ${c.type === 'BLACKLIST' ? 'var(--red)' : 'var(--amber)'}44`,
                  }}>
                    {c.type === 'BLACKLIST' ? '🚫 BL' : '⏸ CD'}
                  </span>
                  <span style={{
                    fontSize: 12, fontWeight: 700,
                    color: (c.mins_remaining ?? 9999) < 60 ? 'var(--amber)' : 'var(--text)',
                  }}>
                    {fmtMins(c.mins_remaining)}
                  </span>
                  <span style={{ fontSize: 11, color: 'var(--muted)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {c.reason || '—'}
                  </span>
                  <span style={{ fontSize: 10, color: 'var(--muted)', textAlign: 'right' }}>
                    {timeAgo(c.updated_ts_utc)}
                  </span>
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  )
}
