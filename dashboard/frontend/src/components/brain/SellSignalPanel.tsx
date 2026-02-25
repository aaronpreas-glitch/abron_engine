/**
 * SellSignalPanel — tracks SELL_ALERT signal effectiveness.
 *
 * A "correct" sell = price fell within 4h of the sell signal firing.
 * Groups by sell type (STRUCTURE_BREAK, HYPE_FADE, etc.) and shows
 * overall correct rate + per-type breakdown + recent sell signal table.
 *
 * Data: /api/brain/sell-signal-stats and /api/brain/sell-signals
 * (no new DB tables — JOIN against existing alert_outcomes)
 */
import { useQuery } from '@tanstack/react-query'
import { api } from '../../api'

// ── Types ──────────────────────────────────────────────────────────────────────

interface SellTypeStats {
  decision: string
  type: string
  total: number
  evaluated: number
  correct: number
  correct_rate: number | null
  avg_1h: number | null
  avg_4h: number | null
}

interface OverallStats {
  total_sell_signals: number
  evaluated: number
  correct: number
  correct_rate: number | null
}

interface SellStatsResponse {
  by_type: SellTypeStats[]
  overall: OverallStats
  lookback_days: number
}

interface SellSignal {
  id: number
  ts_utc: string
  symbol: string
  decision: string
  price_usd: number | null
  score_total: number | null
  regime_label: string | null
  price_1h_after: number | null
  price_4h_after: number | null
}

interface SellSignalsResponse {
  signals: SellSignal[]
  note: string
}

// ── Helpers ────────────────────────────────────────────────────────────────────

const MONO: React.CSSProperties = { fontFamily: 'JetBrains Mono, monospace' }

function fmtPct(v: number | null | undefined, plus = true) {
  if (v == null) return '—'
  const s = plus && v > 0 ? '+' : ''
  return `${s}${v.toFixed(2)}%`
}

function retColor(v: number | null | undefined) {
  if (v == null) return 'var(--muted)'
  return v > 0 ? 'var(--green)' : v < 0 ? 'var(--red)' : 'var(--dim)'
}

function correctColor(rate: number | null) {
  if (rate == null) return 'var(--dim)'
  if (rate >= 60) return 'var(--green)'
  if (rate >= 40) return 'var(--amber)'
  return 'var(--red)'
}

function timeAgo(ts: string) {
  const d = Date.now() - new Date(ts + (ts.endsWith('Z') ? '' : 'Z')).getTime()
  const h = Math.floor(d / 3600000)
  const days = Math.floor(h / 24)
  if (h < 1) return `${Math.floor(d / 60000)}m ago`
  if (h < 24) return `${h}h ago`
  if (days < 7) return `${days}d ago`
  return new Date(ts + 'Z').toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
}

function typeShort(decision: string) {
  return decision.replace('SELL_ALERT_', '').replace(/_/g, ' ')
}

// ── Type stats row ─────────────────────────────────────────────────────────────

function TypeRow({ t }: { t: SellTypeStats }) {
  const barW = t.correct_rate != null ? Math.min(100, t.correct_rate) : 0
  const hasData = t.evaluated > 0

  return (
    <div style={{
      display: 'grid',
      gridTemplateColumns: '1fr 40px 56px 56px 120px',
      gap: 8, padding: '8px 0',
      borderBottom: '1px solid rgba(255,255,255,0.04)',
      alignItems: 'center',
    }}>
      {/* Type name */}
      <span style={{ fontSize: 11, fontWeight: 600, color: 'var(--text)', ...MONO }}>
        {t.type}
      </span>

      {/* Count */}
      <span style={{ fontSize: 10, color: 'var(--dim)', ...MONO, textAlign: 'right' }}>
        {t.total}
      </span>

      {/* Avg 4h move */}
      <span style={{ fontSize: 10, fontWeight: 600, color: retColor(t.avg_4h), ...MONO, textAlign: 'right' }}>
        {hasData && t.avg_4h != null ? fmtPct(t.avg_4h) : '—'}
      </span>

      {/* Correct rate */}
      <span style={{
        fontSize: 11, fontWeight: 700,
        color: hasData ? correctColor(t.correct_rate) : 'var(--dim)',
        ...MONO, textAlign: 'right',
      }}>
        {hasData && t.correct_rate != null ? `${t.correct_rate.toFixed(0)}%` : '—'}
      </span>

      {/* Bar */}
      <div style={{ height: 4, background: 'var(--border)', borderRadius: 2, overflow: 'hidden' }}>
        {hasData && (
          <div style={{
            height: '100%',
            width: `${barW}%`,
            background: correctColor(t.correct_rate),
            borderRadius: 2,
            transition: 'width 0.4s ease',
          }} />
        )}
      </div>
    </div>
  )
}

// ── Main Component ─────────────────────────────────────────────────────────────

export function SellSignalPanel({ lookback }: { lookback: number }) {
  const { data: stats, isLoading: statsLoading } = useQuery<SellStatsResponse>({
    queryKey: ['brain-sell-stats', lookback],
    queryFn: () => api.get(`/brain/sell-signal-stats?lookback_days=${lookback}`).then(r => r.data),
    staleTime: 300_000,
    refetchInterval: 600_000,
  })

  const { data: recentsRaw } = useQuery<SellSignalsResponse>({
    queryKey: ['brain-sell-signals', lookback],
    queryFn: () => api.get(`/brain/sell-signals?lookback_days=${lookback}&limit=20`).then(r => r.data),
    staleTime: 300_000,
    refetchInterval: 600_000,
  })

  const recents = recentsRaw?.signals ?? []
  const overall = stats?.overall
  const byType  = stats?.by_type ?? []

  const hasAnyData = overall && overall.total_sell_signals > 0

  const thStyle: React.CSSProperties = {
    fontSize: 8, color: 'var(--dim)', fontWeight: 400, padding: '4px 0',
    letterSpacing: '0.12em', ...MONO, textAlign: 'left',
    borderBottom: '1px solid var(--border)', paddingBottom: 6, marginBottom: 4,
  }

  return (
    <div>
      {/* Section title */}
      <div style={{
        fontSize: 9, fontWeight: 600, letterSpacing: '0.18em',
        color: 'var(--dim)', ...MONO, textTransform: 'uppercase', marginBottom: 14,
      }}>
        Sell Signal Quality
      </div>

      {statsLoading && (
        <div style={{ color: 'var(--dim)', fontSize: 11, ...MONO }}>Loading…</div>
      )}

      {!statsLoading && !hasAnyData && (
        <div style={{
          padding: '24px 0', textAlign: 'center',
          color: 'var(--dim)', fontSize: 11, ...MONO,
        }}>
          No SELL_ALERT signals in the last {lookback} days.
          <div style={{ marginTop: 6, fontSize: 10, color: 'var(--muted)' }}>
            Sell signals fire when the engine detects STRUCTURE_BREAK, HYPE_FADE, LIQUIDITY_DRAIN, HOLDER_EXODUS, or CONSOLIDATION patterns.
          </div>
        </div>
      )}

      {!statsLoading && hasAnyData && overall && (
        <>
          {/* Overall stat strip */}
          <div style={{
            display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)',
            gap: 10, marginBottom: 18,
          }}>
            {[
              { label: 'TOTAL SELLS', val: String(overall.total_sell_signals), color: 'var(--text)' },
              { label: 'EVALUATED', val: String(overall.evaluated), color: 'var(--muted)' },
              {
                label: 'CORRECT RATE',
                val: overall.correct_rate != null ? `${overall.correct_rate.toFixed(0)}%` : '—',
                color: correctColor(overall.correct_rate),
              },
              { label: 'CORRECT', val: String(overall.correct), color: 'var(--green)' },
            ].map(({ label, val, color }) => (
              <div key={label} style={{
                background: 'var(--surface2)', border: '1px solid var(--border)',
                borderRadius: 6, padding: '10px 12px',
              }}>
                <div style={{ fontSize: 9, color: 'var(--dim)', letterSpacing: '0.12em', ...MONO, marginBottom: 5 }}>
                  {label}
                </div>
                <div style={{ fontSize: 18, fontWeight: 800, color, ...MONO, lineHeight: 1 }}>
                  {val}
                </div>
              </div>
            ))}
          </div>

          {/* Note */}
          <div style={{ fontSize: 9, color: 'var(--dim)', ...MONO, marginBottom: 12, lineHeight: 1.5 }}>
            "Correct" = price fell within 4h of sell signal. Evaluated = matched to a buy outcome within ±24h.
          </div>

          {/* Per-type breakdown */}
          {byType.length > 0 && (
            <div style={{ marginBottom: 20 }}>
              {/* Column headers */}
              <div style={{
                display: 'grid',
                gridTemplateColumns: '1fr 40px 56px 56px 120px',
                gap: 8, marginBottom: 4,
              }}>
                {['TYPE', 'N', 'AVG 4H', 'CORRECT', 'RATE'].map(h => (
                  <span key={h} style={{ ...thStyle, textAlign: h !== 'TYPE' ? 'right' : 'left' }}>{h}</span>
                ))}
              </div>
              {byType.map(t => <TypeRow key={t.decision} t={t} />)}
            </div>
          )}

          {/* Recent sell signals table */}
          {recents.length > 0 && (
            <>
              <div style={{
                fontSize: 9, fontWeight: 600, letterSpacing: '0.14em',
                color: 'var(--dim)', ...MONO, marginBottom: 10,
              }}>
                RECENT SELLS
              </div>
              <div style={{ overflowX: 'auto' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 540 }}>
                  <thead>
                    <tr>
                      {['DATE', 'SYMBOL', 'TYPE', 'PRICE AT SELL', '1H AFTER', '4H AFTER', 'VERDICT'].map(h => (
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
                    {recents.map(s => {
                      const correct = s.price_4h_after != null ? s.price_4h_after < 0 : null
                      const tdStyle: React.CSSProperties = {
                        padding: '7px 8px',
                        borderBottom: '1px solid rgba(255,255,255,0.04)',
                        fontSize: 11, verticalAlign: 'middle',
                      }
                      return (
                        <tr key={s.id}
                          style={{ background: 'transparent' }}
                          onMouseEnter={e => (e.currentTarget.style.background = '#1c2128')}
                          onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
                        >
                          <td style={{ ...tdStyle, color: 'var(--muted)', whiteSpace: 'nowrap' }}>
                            {timeAgo(s.ts_utc)}
                          </td>
                          <td style={{ ...tdStyle, fontWeight: 700 }}>
                            ${s.symbol}
                          </td>
                          <td style={{ ...tdStyle, color: 'var(--red)', fontSize: 10, ...MONO }}>
                            {typeShort(s.decision)}
                          </td>
                          <td style={{ ...tdStyle, color: 'var(--muted)', ...MONO, fontSize: 10 }}>
                            {s.price_usd != null ? `$${s.price_usd.toFixed(s.price_usd < 0.001 ? 8 : 5)}` : '—'}
                          </td>
                          <td style={{ ...tdStyle, fontWeight: 600, color: retColor(s.price_1h_after), ...MONO }}>
                            {fmtPct(s.price_1h_after)}
                          </td>
                          <td style={{ ...tdStyle, fontWeight: 600, color: retColor(s.price_4h_after), ...MONO }}>
                            {fmtPct(s.price_4h_after)}
                          </td>
                          <td style={{ ...tdStyle }}>
                            {correct === true ? (
                              <span style={{
                                fontSize: 9, padding: '1px 5px', borderRadius: 2,
                                background: 'rgba(57,211,83,0.12)', color: 'var(--green)',
                                fontWeight: 700, ...MONO,
                              }}>✓ CORRECT</span>
                            ) : correct === false ? (
                              <span style={{
                                fontSize: 9, padding: '1px 5px', borderRadius: 2,
                                background: 'rgba(248,81,73,0.12)', color: 'var(--red)',
                                fontWeight: 700, ...MONO,
                              }}>✗ WRONG</span>
                            ) : (
                              <span style={{ fontSize: 9, color: 'var(--dim)', ...MONO }}>no data</span>
                            )}
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
