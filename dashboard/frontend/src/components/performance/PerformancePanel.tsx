import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../../api'
import type { OutcomeWinrates, EquityPoint, ScoreDistribution, PortfolioMetrics } from '../../types'
import { LoadingSpinner } from '../shared/LoadingSpinner'
import { EmptyState } from '../shared/EmptyState'
import {
  LineChart, Line, XAxis, YAxis, Tooltip, CartesianGrid, ResponsiveContainer,
  BarChart, Bar, ReferenceLine,
} from 'recharts'

interface OutcomeRecapRow {
  symbol: string
  alerts: number
  avg_1h: number | null
  avg_4h: number | null
  avg_24h: number | null
  wins_4h: number
  n_4h: number
}

function WinRateBar({ label, wr, n, avg }: { label: string; wr: number; n: number; avg: number }) {
  const filled = Math.round(wr / 10)
  const bar = '█'.repeat(Math.min(filled, 10)) + '░'.repeat(Math.max(0, 10 - filled))
  const color = wr >= 55 ? 'var(--green)' : wr >= 45 ? 'var(--amber)' : 'var(--red)'
  return (
    <div style={{ marginBottom: 10 }}>
      <div style={{ display: 'flex', gap: 10, alignItems: 'center', fontSize: 12 }}>
        <span style={{ width: 28, color: 'var(--muted)' }}>{label}</span>
        <span style={{ fontFamily: 'monospace', color, letterSpacing: -1 }}>{bar}</span>
        <span style={{ color, fontWeight: 700 }}>{wr.toFixed(0)}%</span>
        <span style={{ color: 'var(--muted)' }}>n={n}</span>
        <span style={{ marginLeft: 'auto', color: avg >= 0 ? 'var(--green)' : 'var(--red)' }}>
          avg {avg >= 0 ? '+' : ''}{avg.toFixed(2)}%
        </span>
      </div>
    </div>
  )
}

function KpiTile({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div className="card" style={{ textAlign: 'center' }}>
      <div style={{ color: 'var(--muted)', fontSize: 11, marginBottom: 4 }}>{label}</div>
      <div style={{ fontSize: 18, fontWeight: 700, color: color || 'var(--text)' }}>{value}</div>
    </div>
  )
}

const fmtTs = (ts: string) => new Date(ts).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })

function pctColor(v: number | null) {
  if (v == null) return 'var(--muted)'
  return v >= 0 ? 'var(--green)' : 'var(--red)'
}

function fmtPct(v: number | null) {
  if (v == null) return '—'
  return `${v >= 0 ? '+' : ''}${v.toFixed(1)}%`
}

export function PerformancePanel() {
  const [lookback, setLookback] = useState(7)
  const [recapHours, setRecapHours] = useState(48)

  const wr = useQuery<OutcomeWinrates>({ queryKey: ['outcomes', lookback], queryFn: () => api.get(`/performance/outcomes?lookback_days=${lookback}`).then(r => r.data) })
  const eq = useQuery<EquityPoint[]>({ queryKey: ['equity', lookback], queryFn: () => api.get(`/performance/equity-curve?lookback_days=${lookback}&horizon_hours=4`).then(r => r.data) })
  const sd = useQuery<ScoreDistribution>({ queryKey: ['score-dist'], queryFn: () => api.get('/performance/score-distribution').then(r => r.data) })
  const pm = useQuery<PortfolioMetrics>({ queryKey: ['portfolio', lookback], queryFn: () => api.get(`/performance/portfolio?lookback_days=${lookback}&horizon_hours=4`).then(r => r.data) })
  const recap = useQuery<OutcomeRecapRow[]>({ queryKey: ['outcome-recap', recapHours], queryFn: () => api.get(`/performance/outcome-recap?lookback_hours=${recapHours}&limit=15`).then(r => r.data) })
  const isLoading = wr.isLoading || eq.isLoading

  const btnStyle = (active: boolean) => ({
    padding: '3px 10px', fontSize: 11, cursor: 'pointer',
    background: active ? 'var(--surface2)' : 'transparent',
    border: `1px solid ${active ? 'var(--green)' : 'var(--border)'}`,
    color: active ? 'var(--green)' : 'var(--muted)',
    borderRadius: 3,
  })

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16 }}>
        <h2 style={{ fontSize: 14, fontWeight: 700, letterSpacing: '0.06em' }}>📈 PERFORMANCE</h2>
        <div style={{ marginLeft: 'auto', display: 'flex', gap: 6 }}>
          {[7, 14, 30].map(d => (
            <button key={d} style={btnStyle(lookback === d)} onClick={() => setLookback(d)}>{d}d</button>
          ))}
        </div>
      </div>

      {isLoading ? <LoadingSpinner /> : (
        <>
          {/* KPI row */}
          {pm.data && (
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(6, 1fr)', gap: 8, marginBottom: 20 }}>
              <KpiTile label="Trades"     value={String(pm.data.trades)} />
              <KpiTile label="Win Rate 4H" value={`${pm.data.win_rate_pct.toFixed(0)}%`}
                color={pm.data.win_rate_pct >= 55 ? 'var(--green)' : pm.data.win_rate_pct >= 45 ? 'var(--amber)' : 'var(--red)'} />
              <KpiTile label="Avg Ret 4H"  value={`${pm.data.avg_return_pct >= 0 ? '+' : ''}${pm.data.avg_return_pct.toFixed(2)}%`}
                color={pm.data.avg_return_pct >= 0 ? 'var(--green)' : 'var(--red)'} />
              <KpiTile label="Expectancy" value={`${pm.data.expectancy_pct >= 0 ? '+' : ''}${pm.data.expectancy_pct.toFixed(2)}%`}
                color={pm.data.expectancy_pct >= 0 ? 'var(--green)' : 'var(--red)'} />
              <KpiTile label="Max DD"     value={`${pm.data.max_drawdown_pct.toFixed(1)}%`} color="var(--red)" />
              <KpiTile label="Equity"     value={`${pm.data.equity_end.toFixed(3)}x`}
                color={pm.data.equity_end >= 1 ? 'var(--green)' : 'var(--red)'} />
            </div>
          )}

          {/* Win rate bars */}
          {wr.data && (
            <div className="card" style={{ marginBottom: 16 }}>
              <div style={{ color: 'var(--muted)', fontSize: 11, marginBottom: 10 }}>OUTCOME WIN RATES</div>
              <WinRateBar label="1H" wr={wr.data.outcomes_1h.win_rate} n={wr.data.outcomes_1h.n} avg={wr.data.outcomes_1h.avg} />
              <WinRateBar label="4H" wr={wr.data.outcomes_4h.win_rate} n={wr.data.outcomes_4h.n} avg={wr.data.outcomes_4h.avg} />
              <WinRateBar label="24H" wr={wr.data.outcomes_24h.win_rate} n={wr.data.outcomes_24h.n} avg={wr.data.outcomes_24h.avg} />
            </div>
          )}

          {/* Equity curve */}
          {eq.data && eq.data.length > 1 && (
            <div className="card" style={{ marginBottom: 16 }}>
              <div style={{ color: 'var(--muted)', fontSize: 11, marginBottom: 12 }}>EQUITY CURVE (4H SIM)</div>
              <ResponsiveContainer width="100%" height={180}>
                <LineChart data={eq.data} margin={{ top: 0, right: 8, bottom: 0, left: 0 }}>
                  <CartesianGrid stroke="#30363d" strokeDasharray="3 3" />
                  <XAxis dataKey="ts" tickFormatter={fmtTs} tick={{ fontSize: 10 }} />
                  <YAxis tick={{ fontSize: 10 }} tickFormatter={v => `${v.toFixed(2)}x`} />
                  <Tooltip
                    contentStyle={{ background: '#161b22', border: '1px solid #30363d', borderRadius: 4 }}
                    labelStyle={{ color: '#8b949e', fontSize: 11 }}
                    formatter={(v: unknown) => [`${(v as number).toFixed(3)}x`, 'Equity']}
                    labelFormatter={(l: unknown) => new Date(l as string).toLocaleString()}
                  />
                  <ReferenceLine y={1} stroke="#30363d" strokeDasharray="4 4" />
                  <Line type="monotone" dataKey="equity" stroke="#39d353" dot={false} strokeWidth={1.5} />
                </LineChart>
              </ResponsiveContainer>
            </div>
          )}

          {/* Score histogram */}
          {sd.data && (
            <div className="card" style={{ marginBottom: 16 }}>
              <div style={{ color: 'var(--muted)', fontSize: 11, marginBottom: 4 }}>
                SCORE DISTRIBUTION  ·  P50 {sd.data.p50} / P75 {sd.data.p75} / P90 {sd.data.p90}
              </div>
              <ResponsiveContainer width="100%" height={120}>
                <BarChart data={sd.data.buckets} margin={{ top: 4, right: 4, bottom: 0, left: 0 }}>
                  <XAxis dataKey="range" tick={{ fontSize: 10 }} />
                  <YAxis tick={{ fontSize: 10 }} />
                  <Tooltip
                    contentStyle={{ background: '#161b22', border: '1px solid #30363d', borderRadius: 4 }}
                    labelStyle={{ fontSize: 11 }}
                  />
                  <Bar dataKey="count" fill="#39d35355" stroke="#39d353" strokeWidth={1} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}

          {/* Outcome Recap — per symbol */}
          <div className="card">
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12 }}>
              <span style={{ color: 'var(--muted)', fontSize: 11, fontWeight: 700 }}>OUTCOME RECAP BY SYMBOL</span>
              <div style={{ marginLeft: 'auto', display: 'flex', gap: 6 }}>
                {[24, 48, 168].map(h => (
                  <button key={h} style={btnStyle(recapHours === h)} onClick={() => setRecapHours(h)}>
                    {h < 48 ? `${h}h` : h === 48 ? '2d' : '7d'}
                  </button>
                ))}
              </div>
            </div>

            {recap.isLoading ? <LoadingSpinner /> : (!recap.data || recap.data.length === 0) ? (
              <EmptyState message="No outcome data yet for this window." />
            ) : (
              <div style={{ overflowX: 'auto' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                  <thead>
                    <tr style={{ color: 'var(--muted)', fontSize: 10, letterSpacing: '0.04em' }}>
                      <th style={{ textAlign: 'left', padding: '4px 8px', fontWeight: 700 }}>SYMBOL</th>
                      <th style={{ textAlign: 'center', padding: '4px 8px', fontWeight: 700 }}>ALERTS</th>
                      <th style={{ textAlign: 'center', padding: '4px 8px', fontWeight: 700 }}>4H W%</th>
                      <th style={{ textAlign: 'right', padding: '4px 8px', fontWeight: 700 }}>AVG 1H</th>
                      <th style={{ textAlign: 'right', padding: '4px 8px', fontWeight: 700 }}>AVG 4H</th>
                      <th style={{ textAlign: 'right', padding: '4px 8px', fontWeight: 700 }}>AVG 24H</th>
                    </tr>
                  </thead>
                  <tbody>
                    {recap.data.map((row, i) => {
                      const wr4h = row.n_4h > 0 ? (row.wins_4h / row.n_4h * 100) : null
                      const wrColor = wr4h == null ? 'var(--muted)' : wr4h >= 55 ? 'var(--green)' : wr4h >= 45 ? 'var(--amber)' : 'var(--red)'
                      return (
                        <tr key={row.symbol} style={{
                          borderTop: i > 0 ? '1px solid var(--border)' : undefined,
                        }}>
                          <td style={{ padding: '6px 8px', fontWeight: 700 }}>${row.symbol}</td>
                          <td style={{ padding: '6px 8px', textAlign: 'center', color: 'var(--muted)' }}>{row.alerts}</td>
                          <td style={{ padding: '6px 8px', textAlign: 'center', color: wrColor, fontWeight: 700 }}>
                            {wr4h != null ? `${wr4h.toFixed(0)}%` : '—'}
                            {row.n_4h > 0 && (
                              <span style={{ color: 'var(--muted)', fontWeight: 400, fontSize: 10 }}> ({row.n_4h})</span>
                            )}
                          </td>
                          <td style={{ padding: '6px 8px', textAlign: 'right', color: pctColor(row.avg_1h) }}>{fmtPct(row.avg_1h)}</td>
                          <td style={{ padding: '6px 8px', textAlign: 'right', color: pctColor(row.avg_4h), fontWeight: 700 }}>{fmtPct(row.avg_4h)}</td>
                          <td style={{ padding: '6px 8px', textAlign: 'right', color: pctColor(row.avg_24h) }}>{fmtPct(row.avg_24h)}</td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </>
      )}
    </div>
  )
}
