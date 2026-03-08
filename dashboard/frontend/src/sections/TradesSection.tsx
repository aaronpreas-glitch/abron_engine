import type { ClosedTrade, PerpsStatus } from '../Terminal'

interface Props {
  perpsStatus?: PerpsStatus
  closedTrades: ClosedTrade[]
  loading: boolean
}

const MONO = { fontFamily: 'JetBrains Mono, monospace' }

function pc(p: number) { return p > 0 ? '#00d48a' : p < 0 ? '#ef4444' : '#a0aec0' }
function fmt(p: number) { return `${p > 0 ? '+' : ''}${p.toFixed(2)}%` }
function fmtDate(ts: string) {
  if (!ts) return '—'
  try {
    return new Intl.DateTimeFormat('en-US', {
      timeZone: 'America/New_York',
      month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit', hour12: false,
    }).format(new Date(ts.includes('T') ? ts : ts + 'Z'))
  } catch { return ts.slice(0, 16) }
}
function exitColor(r: string) {
  if (r.includes('TRAIL') || r.includes('WINNER')) return '#00d48a'
  if (r.includes('STOP_LOSS')) return '#ef4444'
  if (r.includes('TIME_LIMIT')) return '#f59e0b'
  return '#5a7a9a'
}

export function TradesSection({ perpsStatus, closedTrades, loading }: Props) {
  const openCount = perpsStatus?.open_positions ?? 0
  const wins = closedTrades.filter(t => t.pnl_pct > 0).length
  const wr = closedTrades.length > 0 ? (wins / closedTrades.length * 100).toFixed(0) : null
  const avgPnl = closedTrades.length > 0
    ? closedTrades.reduce((s, t) => s + t.pnl_pct, 0) / closedTrades.length
    : null

  return (
    <div>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 14 }}>
        <span className="section-label">TRADE HISTORY</span>
        <span style={{ color: '#2d4060', fontSize: 9, ...MONO }}>last 20 · 30s refresh</span>
      </div>

      {/* Stats bar */}
      <div style={{ display: 'flex', gap: 10, marginBottom: 16 }}>
        <div style={{ padding: '10px 14px', background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.07)', borderRadius: 8, flex: 1 }}>
          <div style={{ fontSize: 9, color: '#4a6280', letterSpacing: '0.1em', marginBottom: 4, ...MONO }}>OPEN</div>
          <div style={{ fontSize: 20, fontWeight: 700, color: openCount > 0 ? '#f59e0b' : '#3d5068', ...MONO }}>{openCount}</div>
        </div>
        {wr !== null && (
          <div style={{ padding: '10px 14px', background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.07)', borderRadius: 8, flex: 1 }}>
            <div style={{ fontSize: 9, color: '#4a6280', letterSpacing: '0.1em', marginBottom: 4, ...MONO }}>WIN RATE</div>
            <div style={{ fontSize: 20, fontWeight: 700, color: Number(wr) >= 50 ? '#00d48a' : '#ef4444', ...MONO }}>{wr}%</div>
          </div>
        )}
        {avgPnl !== null && (
          <div style={{ padding: '10px 14px', background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.07)', borderRadius: 8, flex: 1 }}>
            <div style={{ fontSize: 9, color: '#4a6280', letterSpacing: '0.1em', marginBottom: 4, ...MONO }}>AVG PnL</div>
            <div style={{ fontSize: 20, fontWeight: 700, color: pc(avgPnl), ...MONO }}>{avgPnl >= 0 ? '+' : ''}{avgPnl.toFixed(2)}%</div>
          </div>
        )}
      </div>

      {/* Table */}
      {loading ? (
        <div style={{ color: '#4d5a6e', fontSize: 10, ...MONO }}>loading trades…</div>
      ) : closedTrades.length === 0 ? (
        <div style={{ color: '#2d4060', fontSize: 10, ...MONO }}>no closed trades yet</div>
      ) : (
        <table className="data-table">
          <thead>
            <tr>
              <th>Symbol</th>
              <th>Side</th>
              <th style={{ textAlign: 'right' }}>PnL</th>
              <th>Exit</th>
              <th>Closed (ET)</th>
            </tr>
          </thead>
          <tbody>
            {closedTrades.map((t, i) => (
              <tr key={i}>
                <td style={{ fontWeight: 700, color: '#c0cfe0' }}>{t.symbol}</td>
                <td style={{ color: '#5a7a9a', fontSize: 10 }}>{t.side ?? 'LONG'}</td>
                <td style={{ textAlign: 'right', color: pc(t.pnl_pct), fontWeight: 700 }}>{fmt(t.pnl_pct)}</td>
                <td style={{ color: exitColor(t.exit_reason), fontSize: 10 }}>{t.exit_reason}</td>
                <td style={{ color: '#4d5a6e', fontSize: 10 }}>{fmtDate(t.closed_ts_utc)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}
