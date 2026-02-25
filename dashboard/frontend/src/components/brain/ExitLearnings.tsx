/**
 * ExitLearnings — Brain panel showing what the exit strategy has learned.
 *
 * Shows:
 * - Overall win rate + avg PnL across all auto-closed positions
 * - Per-exit-reason breakdown (STOP_LOSS, TP1, TP2, TRAILING_STOP, MAX_HOLD, FORCE_SELL)
 * - Recent exit history table
 *
 * Data: GET /api/executor/exit-learnings
 */
import { useQuery } from '@tanstack/react-query'
import { api } from '../../api'

// ── Types ──────────────────────────────────────────────────────────────────────

interface ReasonStats {
  count: number
  wins: number
  avg_pnl: number
  win_rate: number | null
}

interface Summary {
  total: number
  win_rate: number | null
  avg_pnl_pct: number | null
  by_reason: Record<string, ReasonStats>
}

interface ExitRecord {
  ts: string
  trade_id: number
  symbol: string
  exit_reason: string
  entry_price: number
  exit_price: number
  pnl_pct: number
  pnl_usd: number
  position_usd: number
  profile_key: string
  plan_tp1: number | null
  plan_tp2: number | null
  plan_stop: number | null
  plan_max_hold_h: number | null
  best_horizon_h: number | null
  learned_from: number
}

interface LearningsResponse {
  summary: Summary
  recent: ExitRecord[]
}

// ── Helpers ───────────────────────────────────────────────────────────────────

const MONO: React.CSSProperties = { fontFamily: 'JetBrains Mono, monospace' }

function pnlColor(v: number) {
  return v > 0 ? 'var(--green)' : v < 0 ? 'var(--red)' : 'var(--dim)'
}

function fmtPct(v: number | null, plus = true) {
  if (v == null) return '—'
  return `${plus && v > 0 ? '+' : ''}${v.toFixed(1)}%`
}

function timeAgo(ts: string) {
  const d = Date.now() - new Date(ts).getTime()
  const m = Math.floor(d / 60000)
  const h = Math.floor(m / 60)
  if (m < 60) return `${m}m ago`
  if (h < 24) return `${h}h ago`
  return `${Math.floor(h / 24)}d ago`
}

const REASON_COLOR: Record<string, string> = {
  STOP_LOSS:       'var(--red)',
  TP1:             'var(--green)',
  TP2:             '#00c87a',
  TRAILING_STOP:   'var(--amber)',
  MAX_HOLD:        'var(--muted)',
  FORCE_SELL:      'var(--amber)',
}

// ── Reason bar ────────────────────────────────────────────────────────────────

function ReasonRow({ name, stats }: { name: string; stats: ReasonStats }) {
  const barW = stats.win_rate != null ? Math.min(100, stats.win_rate) : 0
  const color = REASON_COLOR[name] || 'var(--muted)'

  return (
    <div style={{
      display: 'grid',
      gridTemplateColumns: '1fr 36px 56px 56px 120px',
      gap: 8, padding: '7px 0',
      borderBottom: '1px solid rgba(255,255,255,0.04)',
      alignItems: 'center',
    }}>
      <span style={{ fontSize: 11, fontWeight: 600, color, ...MONO }}>{name}</span>
      <span style={{ fontSize: 10, color: 'var(--dim)', textAlign: 'right', ...MONO }}>{stats.count}</span>
      <span style={{
        fontSize: 10, fontWeight: 600, textAlign: 'right', ...MONO,
        color: pnlColor(stats.avg_pnl),
      }}>
        {fmtPct(stats.avg_pnl)}
      </span>
      <span style={{
        fontSize: 11, fontWeight: 700, textAlign: 'right', ...MONO,
        color: stats.win_rate != null
          ? (stats.win_rate >= 60 ? 'var(--green)' : stats.win_rate >= 40 ? 'var(--amber)' : 'var(--red)')
          : 'var(--dim)',
      }}>
        {stats.win_rate != null ? `${stats.win_rate.toFixed(0)}%` : '—'}
      </span>
      <div style={{ height: 4, background: 'var(--border)', borderRadius: 2, overflow: 'hidden' }}>
        <div style={{
          height: '100%', width: `${barW}%`,
          background: color, borderRadius: 2,
          transition: 'width 0.4s ease',
        }} />
      </div>
    </div>
  )
}

// ── Main ──────────────────────────────────────────────────────────────────────

export function ExitLearnings() {
  const { data, isLoading } = useQuery<LearningsResponse>({
    queryKey: ['exit-learnings'],
    queryFn: () => api.get('/executor/exit-learnings').then(r => r.data),
    staleTime: 120_000,
    refetchInterval: 300_000,
  })

  const summary = data?.summary
  const recent  = data?.recent ?? []
  const byReason = summary?.by_reason ?? {}
  const hasData = (summary?.total ?? 0) > 0

  const thStyle: React.CSSProperties = {
    fontSize: 8, color: 'var(--dim)', fontWeight: 400,
    letterSpacing: '0.12em', ...MONO, textAlign: 'left',
    borderBottom: '1px solid var(--border)', paddingBottom: 6,
  }

  return (
    <div>
      {/* Section title */}
      <div style={{
        fontSize: 9, fontWeight: 600, letterSpacing: '0.18em',
        color: 'var(--dim)', ...MONO, textTransform: 'uppercase', marginBottom: 14,
      }}>
        Exit Strategy Learnings
      </div>

      {isLoading && (
        <div style={{ color: 'var(--dim)', fontSize: 11, ...MONO }}>Loading…</div>
      )}

      {!isLoading && !hasData && (
        <div style={{
          padding: '24px 0', textAlign: 'center',
          color: 'var(--dim)', fontSize: 11, ...MONO,
        }}>
          No auto-trades closed yet.
          <div style={{ marginTop: 6, fontSize: 10, color: 'var(--muted)' }}>
            Once positions close, exit outcomes will be tracked here and used to calibrate future TP/stop levels.
          </div>
        </div>
      )}

      {!isLoading && hasData && summary && (
        <>
          {/* Overall stats */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 10, marginBottom: 20 }}>
            {[
              { label: 'TOTAL EXITS',  val: String(summary.total),          color: 'var(--text)' },
              {
                label: 'WIN RATE',
                val: summary.win_rate != null ? `${summary.win_rate.toFixed(0)}%` : '—',
                color: summary.win_rate != null
                  ? (summary.win_rate >= 50 ? 'var(--green)' : 'var(--red)')
                  : 'var(--dim)',
              },
              {
                label: 'AVG PNL',
                val: summary.avg_pnl_pct != null
                  ? `${summary.avg_pnl_pct > 0 ? '+' : ''}${summary.avg_pnl_pct.toFixed(1)}%`
                  : '—',
                color: summary.avg_pnl_pct != null
                  ? (summary.avg_pnl_pct > 0 ? 'var(--green)' : 'var(--red)')
                  : 'var(--dim)',
              },
            ].map(({ label, val, color }) => (
              <div key={label} style={{
                background: 'var(--surface2)', border: '1px solid var(--border)',
                borderRadius: 6, padding: '10px 12px',
              }}>
                <div style={{ fontSize: 9, color: 'var(--dim)', letterSpacing: '0.12em', ...MONO, marginBottom: 4 }}>
                  {label}
                </div>
                <div style={{ fontSize: 18, fontWeight: 800, color, ...MONO, lineHeight: 1 }}>
                  {val}
                </div>
              </div>
            ))}
          </div>

          {/* Per-reason breakdown */}
          {Object.keys(byReason).length > 0 && (
            <div style={{ marginBottom: 20 }}>
              <div style={{
                display: 'grid', gridTemplateColumns: '1fr 36px 56px 56px 120px',
                gap: 8, marginBottom: 4,
              }}>
                {['EXIT REASON', 'N', 'AVG PNL', 'WIN %', 'RATE'].map(h => (
                  <span key={h} style={{ ...thStyle, textAlign: h !== 'EXIT REASON' ? 'right' : 'left' }}>
                    {h}
                  </span>
                ))}
              </div>
              {Object.entries(byReason).map(([name, stats]) => (
                <ReasonRow key={name} name={name} stats={stats} />
              ))}
            </div>
          )}

          {/* Note about calibration */}
          <div style={{
            fontSize: 9, color: 'var(--dim)', ...MONO, marginBottom: 20, lineHeight: 1.6,
            padding: '10px 12px', background: 'rgba(255,255,255,0.02)',
            borderRadius: 6, border: '1px solid var(--border)',
          }}>
            The exit engine learns from these outcomes. After {'>'}5 similar signals, it auto-calibrates
            TP1/TP2 levels to historical win percentiles and adjusts stop-loss based on avg loss depth.
          </div>

          {/* Recent exits table */}
          {recent.length > 0 && (
            <>
              <div style={{
                fontSize: 9, fontWeight: 600, letterSpacing: '0.14em',
                color: 'var(--dim)', ...MONO, marginBottom: 10,
              }}>
                RECENT EXITS
              </div>
              <div style={{ overflowX: 'auto' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 580 }}>
                  <thead>
                    <tr>
                      {['DATE', 'SYMBOL', 'EXIT REASON', 'ENTRY', 'EXIT', 'PNL', 'LEARNED FROM'].map(h => (
                        <th key={h} style={{
                          ...thStyle, padding: '4px 8px',
                          borderBottom: '1px solid var(--border)', whiteSpace: 'nowrap',
                        }}>
                          {h}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {recent.map((r, i) => {
                      const tdStyle: React.CSSProperties = {
                        padding: '6px 8px', borderBottom: '1px solid rgba(255,255,255,0.04)',
                        fontSize: 11, verticalAlign: 'middle',
                      }
                      return (
                        <tr key={i}
                          style={{ background: 'transparent' }}
                          onMouseEnter={e => (e.currentTarget.style.background = '#1c2128')}
                          onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
                        >
                          <td style={{ ...tdStyle, color: 'var(--muted)', whiteSpace: 'nowrap' }}>
                            {timeAgo(r.ts)}
                          </td>
                          <td style={{ ...tdStyle, fontWeight: 700 }}>${r.symbol}</td>
                          <td style={{ ...tdStyle, color: REASON_COLOR[r.exit_reason] || 'var(--muted)', fontSize: 10, ...MONO }}>
                            {r.exit_reason}
                          </td>
                          <td style={{ ...tdStyle, color: 'var(--muted)', ...MONO, fontSize: 10 }}>
                            ${r.entry_price?.toFixed(r.entry_price < 0.001 ? 8 : 5) ?? '—'}
                          </td>
                          <td style={{ ...tdStyle, color: 'var(--muted)', ...MONO, fontSize: 10 }}>
                            ${r.exit_price?.toFixed(r.exit_price < 0.001 ? 8 : 5) ?? '—'}
                          </td>
                          <td style={{ ...tdStyle, fontWeight: 700, color: pnlColor(r.pnl_pct), ...MONO }}>
                            {fmtPct(r.pnl_pct)}
                          </td>
                          <td style={{ ...tdStyle, color: 'var(--dim)', fontSize: 10, ...MONO }}>
                            {r.learned_from > 0 ? `${r.learned_from} signals` : 'default'}
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </>
      )}
    </div>
  )
}
