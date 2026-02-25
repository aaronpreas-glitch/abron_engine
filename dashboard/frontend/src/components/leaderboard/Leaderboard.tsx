import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { api } from '../../api'
import type { LeaderboardEntry } from '../../types'
import { PctChange } from '../shared/PctChange'
import { LoadingSpinner } from '../shared/LoadingSpinner'
import { EmptyState } from '../shared/EmptyState'

function timeAgo(ts: string) {
  const d = Date.now() - new Date(ts + (ts.endsWith('Z') ? '' : 'Z')).getTime()
  const h = Math.floor(d / 3600000)
  if (h < 1)  return `${Math.floor(d / 60000)}m ago`
  if (h < 24) return `${h}h ago`
  return `${Math.floor(h / 24)}d ago`
}

export function Leaderboard() {
  const navigate = useNavigate()
  const { data, isLoading } = useQuery<LeaderboardEntry[]>({
    queryKey: ['leaderboard'],
    queryFn: () => api.get('/signals/leaderboard?lookback_hours=48&limit=20').then(r => r.data),
    refetchInterval: 60000,
  })

  const thS: React.CSSProperties = { color: 'var(--muted)', fontWeight: 400, padding: '4px 8px', borderBottom: '1px solid var(--border)', textAlign: 'left', fontSize: 11 }
  const tdS: React.CSSProperties = { padding: '6px 8px', borderBottom: '1px solid #1c2128', fontSize: 12 }

  return (
    <div>
      <h2 style={{ fontSize: 14, fontWeight: 700, letterSpacing: '0.06em', marginBottom: 16 }}>🏆 SCORE LEADERBOARD (48h)</h2>
      <div className="card">
        {isLoading ? <LoadingSpinner /> : (data || []).length === 0 ? <EmptyState message="No scan data yet." /> : (
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr>
                <th style={thS}>#</th><th style={thS}>Symbol</th><th style={thS}>Score</th>
                <th style={thS}>Regime</th><th style={thS}>24h</th><th style={thS}>Seen</th><th style={thS}>Last</th>
              </tr>
            </thead>
            <tbody>
              {(data || []).map((row, i) => {
                const scoreColor = row.score >= 85 ? 'var(--green)' : row.score >= 70 ? 'var(--amber)' : 'var(--muted)'
                const medal = i === 0 ? '🥇' : i === 1 ? '🥈' : i === 2 ? '🥉' : `${i + 1}`
                return (
                  <tr key={row.symbol} style={{ background: i < 3 ? 'var(--surface2)' : 'transparent' }}>
                    <td style={{ ...tdS, color: 'var(--muted)' }}>{medal}</td>
                    <td style={tdS}>
                      <span
                        style={{ fontWeight: 700, cursor: 'pointer', color: 'var(--text)' }}
                        onClick={() => navigate(`/symbol/${row.symbol}`)}
                        title="View symbol history"
                      >${row.symbol}</span>
                    </td>
                    <td style={{ ...tdS, color: scoreColor, fontWeight: 700 }}>{row.score.toFixed(0)}</td>
                    <td style={{ ...tdS, color: 'var(--muted)' }}>{row.regime_label || '—'}</td>
                    <td style={tdS}><PctChange value={row.change_24h} /></td>
                    <td style={{ ...tdS, color: 'var(--muted)' }}>{row.appearances}×</td>
                    <td style={{ ...tdS, color: 'var(--muted)' }}>{timeAgo(row.last_seen)}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
