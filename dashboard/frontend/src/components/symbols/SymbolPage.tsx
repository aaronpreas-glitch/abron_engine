/**
 * SymbolPage — Per-symbol drill-down.
 * Route: /symbol/:symbol
 *
 * Sections:
 *   1. Header — symbol, live price, DexScreener / Pump.fun links
 *   2. Stat bar — signal count, alert count, avg score, win rate (4h), live price
 *   3. Outcome cards — 1h / 4h / 24h win rate + avg return
 *   4. Horizon bar chart — visual comparison of avg returns at each horizon
 *   5. Signal + Outcome history table — every signal with its outcome inline
 */
import { useParams, useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api } from '../../api'
import { Badge } from '../shared/Badge'
import { LoadingSpinner } from '../shared/LoadingSpinner'
import { EmptyState } from '../shared/EmptyState'

interface TradeLink {
  signal_id: number
  trade_id: number | null
  entry_price: number | null
  exit_price: number | null
  pnl_pct: number | null
  trade_status: string | null
  slippage_pct: number | null
}

// ── types ──────────────────────────────────────────────────────────────────────

interface OutcomeWindow {
  n: number
  avg: number
  win_rate: number
  best?: number
  worst?: number
}

interface SymbolFull {
  symbol: string
  mark_price: number | null
  signals: SignalRow[]
  outcomes: {
    total: number
    outcomes_1h: OutcomeWindow
    outcomes_4h: OutcomeWindow
    outcomes_24h: OutcomeWindow
  }
}

interface SignalRow {
  id: number
  ts_utc: string
  symbol: string
  mint: string | null
  pair_address: string | null
  score_total: number | null
  decision: string
  regime_score: number | null
  regime_label: string | null
  liquidity_usd: number | null
  volume_24h: number | null
  price_usd: number | null
  change_24h: number | null
  rel_strength_vs_sol: number | null
  conviction: number | null
  setup_type: string | null
  notes: string | null
  // from LEFT JOIN alert_outcomes
  return_1h_pct: number | null
  return_4h_pct: number | null
  return_24h_pct: number | null
  outcome_status: string | null
}

// ── helpers ───────────────────────────────────────────────────────────────────

function fmtPrice(v: number | null) {
  if (v == null) return '—'
  if (v < 0.000001) return `$${v.toExponential(3)}`
  if (v < 0.0001)   return `$${v.toFixed(10)}`
  if (v < 0.001)    return `$${v.toFixed(8)}`
  if (v < 1)        return `$${v.toFixed(6)}`
  if (v < 1000)     return `$${v.toFixed(4)}`
  return `$${v.toLocaleString('en-US', { maximumFractionDigits: 2 })}`
}

function fmtUsd(v: number | null) {
  if (v == null) return '—'
  if (v >= 1e9) return `$${(v / 1e9).toFixed(2)}B`
  if (v >= 1e6) return `$${(v / 1e6).toFixed(2)}M`
  if (v >= 1e3) return `$${(v / 1e3).toFixed(1)}K`
  return `$${v.toFixed(2)}`
}

function fmtPct(v: number | null | undefined, showPlus = true) {
  if (v == null) return '—'
  const sign = showPlus && v > 0 ? '+' : ''
  return `${sign}${v.toFixed(2)}%`
}

function pctColor(v: number | null | undefined) {
  if (v == null) return 'var(--muted)'
  return v > 0 ? 'var(--green)' : v < 0 ? 'var(--red)' : 'var(--dim)'
}

function timeAgo(ts: string) {
  const d = Date.now() - new Date(ts + (ts.endsWith('Z') ? '' : 'Z')).getTime()
  const h = Math.floor(d / 3600000)
  const days = Math.floor(h / 24)
  if (h < 1)    return `${Math.floor(d / 60000)}m ago`
  if (h < 24)   return `${h}h ago`
  if (days < 7) return `${days}d ago`
  return new Date(ts + 'Z').toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
}

function decisionLabel(d: string) {
  if (d.startsWith('SELL_ALERT_')) return 'SELL ' + d.replace('SELL_ALERT_', '').replace(/_/g, ' ')
  return d
    .replace('ALERT_DRY_RUN', 'DRY RUN')
    .replace('SCAN_BEST', 'SCAN')
    .replace('WATCHLIST_ALERT', 'WATCH')
    .replace('RUNNER_WATCH_ALERT', 'RUNNER')
    .replace('LEGACY_RECOVERY_ALERT', 'LEGACY')
    .replace(/_/g, ' ')
}

function decisionColor(d: string): 'green' | 'amber' | 'red' | 'blue' | 'muted' {
  if (d.startsWith('SELL_ALERT')) return 'red'
  if (d.includes('ALERT') && !d.includes('DRY')) return 'green'
  if (d.includes('DRY_RUN'))  return 'blue'
  if (d.includes('WATCHLIST')) return 'amber'
  if (d.includes('REGIME_BLOCK')) return 'red'
  return 'muted'
}

function convLabel(c: number | null) {
  if (c === 3) return 'A'
  if (c === 2) return 'B'
  if (c === 1) return 'C'
  return null
}

function scoreColor(s: number) {
  if (s >= 85) return 'var(--green)'
  if (s >= 70) return 'var(--amber)'
  return 'var(--muted)'
}

function winRateColor(wr: number, n: number) {
  if (n === 0) return 'var(--muted)'
  if (wr >= 60) return 'var(--green)'
  if (wr >= 45) return 'var(--amber)'
  return 'var(--red)'
}

// ── Horizon bar mini-chart ─────────────────────────────────────────────────────

function HorizonBars({ outcomes }: { outcomes: SymbolFull['outcomes'] }) {
  const horizons = [
    { label: '1H', data: outcomes.outcomes_1h },
    { label: '4H', data: outcomes.outcomes_4h },
    { label: '24H', data: outcomes.outcomes_24h },
  ]

  const maxAbs = Math.max(
    ...horizons.map(h => Math.abs(h.data.avg || 0)),
    0.01
  )

  return (
    <div className="card" style={{ marginBottom: 16 }}>
      <div style={{ color: 'var(--muted)', fontSize: 10, fontWeight: 700, marginBottom: 14, letterSpacing: '0.06em' }}>
        AVERAGE RETURN BY HORIZON
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
        {horizons.map(({ label, data }) => {
          const pct = data.n > 0 ? data.avg : null
          const barW = pct != null ? Math.abs(pct) / maxAbs * 100 : 0
          const pos = (pct ?? 0) >= 0
          return (
            <div key={label} style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <div style={{ width: 28, color: 'var(--muted)', fontSize: 11, fontFamily: 'JetBrains Mono, monospace', flexShrink: 0 }}>{label}</div>
              {/* bar track */}
              <div style={{ flex: 1, height: 16, background: 'var(--surface2)', borderRadius: 2, position: 'relative', overflow: 'hidden' }}>
                {pct != null && (
                  <div style={{
                    position: 'absolute',
                    top: 0, bottom: 0,
                    left: pos ? '50%' : `${50 - barW / 2}%`,
                    width: `${barW / 2}%`,
                    background: pos ? 'var(--green)' : 'var(--red)',
                    opacity: 0.75,
                    borderRadius: 1,
                  }} />
                )}
                {/* center line */}
                <div style={{ position: 'absolute', top: 0, bottom: 0, left: '50%', width: 1, background: 'var(--border)' }} />
              </div>
              <div style={{
                width: 70, textAlign: 'right',
                fontFamily: 'JetBrains Mono, monospace', fontSize: 11,
                color: pct != null ? pctColor(pct) : 'var(--dim)',
                fontWeight: 700,
              }}>
                {pct != null ? fmtPct(pct) : 'no data'}
              </div>
              <div style={{ width: 56, textAlign: 'right', fontSize: 10, color: 'var(--muted)' }}>
                {data.n > 0 ? `${data.win_rate.toFixed(0)}% WR · n=${data.n}` : ''}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

// ── Outcome stat cards ─────────────────────────────────────────────────────────

function OutcomeCard({ label, data }: { label: string; data: OutcomeWindow }) {
  const wrC = winRateColor(data.win_rate, data.n)
  return (
    <div className="card" style={{ textAlign: 'center', flex: 1 }}>
      <div style={{ color: 'var(--muted)', fontSize: 10, marginBottom: 8, letterSpacing: '0.06em' }}>{label}</div>
      {data.n === 0 ? (
        <div style={{ color: 'var(--dim)', fontSize: 12 }}>no data</div>
      ) : (
        <>
          <div style={{ fontSize: 22, fontWeight: 700, color: wrC }}>
            {data.win_rate.toFixed(0)}%
          </div>
          <div style={{ fontSize: 10, color: 'var(--muted)', margin: '4px 0' }}>win rate · n={data.n}</div>
          <div style={{ fontSize: 14, fontWeight: 700, color: pctColor(data.avg) }}>
            {fmtPct(data.avg)}
          </div>
          <div style={{ fontSize: 10, color: 'var(--muted)', marginBottom: 4 }}>avg return</div>
          {data.best != null && (
            <div style={{ marginTop: 6, fontSize: 10, display: 'flex', justifyContent: 'space-between' }}>
              <span style={{ color: 'var(--green)' }}>▲ {fmtPct(data.best)}</span>
              <span style={{ color: 'var(--red)' }}>▼ {fmtPct(data.worst)}</span>
            </div>
          )}
        </>
      )}
    </div>
  )
}

// ── Signal + Outcome table row ────────────────────────────────────────────────

function SignalRow({ sig, idx, tradeLink }: { sig: SignalRow; idx: number; tradeLink?: TradeLink }) {
  const isAlert = sig.decision.includes('ALERT') && !sig.decision.includes('DRY')
  const conv = convLabel(sig.conviction)
  const isPending = sig.outcome_status === 'PENDING'
  const win4h = sig.return_4h_pct != null ? sig.return_4h_pct > 0 : null

  const tdStyle: React.CSSProperties = {
    padding: '7px 10px',
    borderBottom: '1px solid #1c2128',
    fontSize: 11,
    verticalAlign: 'middle',
  }

  return (
    <tr
      style={{ background: isAlert ? 'rgba(57,211,83,0.03)' : 'transparent' }}
      onMouseEnter={e => (e.currentTarget.style.background = '#1c2128')}
      onMouseLeave={e => (e.currentTarget.style.background = isAlert ? 'rgba(57,211,83,0.03)' : 'transparent')}
    >
      {/* # */}
      <td style={{ ...tdStyle, color: 'var(--dim)', width: 28, fontSize: 10 }}>{idx + 1}</td>

      {/* Date */}
      <td style={{ ...tdStyle, color: 'var(--muted)', whiteSpace: 'nowrap' }}>{timeAgo(sig.ts_utc)}</td>

      {/* Decision */}
      <td style={{ ...tdStyle }}>
        <Badge label={decisionLabel(sig.decision)} color={decisionColor(sig.decision)} />
      </td>

      {/* Score */}
      <td style={{ ...tdStyle, fontFamily: 'JetBrains Mono, monospace' }}>
        {sig.score_total != null ? (
          <span style={{ color: scoreColor(sig.score_total), fontWeight: 700 }}>
            {sig.score_total.toFixed(0)}
          </span>
        ) : '—'}
      </td>

      {/* Conv */}
      <td style={{ ...tdStyle }}>
        {conv ? (
          <span style={{
            padding: '1px 6px', borderRadius: 2, fontSize: 10, fontWeight: 700,
            background: conv === 'A' ? '#39d35322' : conv === 'B' ? '#f0a50022' : '#ffffff0a',
            color: conv === 'A' ? 'var(--green)' : conv === 'B' ? 'var(--amber)' : 'var(--muted)',
          }}>{conv}</span>
        ) : '—'}
      </td>

      {/* Regime */}
      <td style={{ ...tdStyle, color: 'var(--muted)', maxWidth: 100 }}>
        <span style={{ fontSize: 10 }}>
          {sig.regime_label?.replace(/_/g, ' ') || '—'}
        </span>
      </td>

      {/* Entry price */}
      <td style={{ ...tdStyle, fontFamily: 'JetBrains Mono, monospace', color: 'var(--dim)' }}>
        {fmtPrice(sig.price_usd)}
      </td>

      {/* 1H outcome */}
      <td style={{ ...tdStyle }}>
        {sig.return_1h_pct != null ? (
          <span style={{ color: pctColor(sig.return_1h_pct), fontWeight: 600, fontFamily: 'JetBrains Mono, monospace' }}>
            {fmtPct(sig.return_1h_pct)}
          </span>
        ) : isPending ? (
          <span style={{ color: 'var(--dim)', fontSize: 10 }}>…</span>
        ) : '—'}
      </td>

      {/* 4H outcome */}
      <td style={{ ...tdStyle }}>
        {sig.return_4h_pct != null ? (
          <span style={{ color: pctColor(sig.return_4h_pct), fontWeight: 600, fontFamily: 'JetBrains Mono, monospace' }}>
            {fmtPct(sig.return_4h_pct)}
          </span>
        ) : isPending ? (
          <span style={{ color: 'var(--dim)', fontSize: 10 }}>…</span>
        ) : '—'}
      </td>

      {/* 24H outcome */}
      <td style={{ ...tdStyle }}>
        {sig.return_24h_pct != null ? (
          <span style={{ color: pctColor(sig.return_24h_pct), fontWeight: 600, fontFamily: 'JetBrains Mono, monospace' }}>
            {fmtPct(sig.return_24h_pct)}
          </span>
        ) : isPending ? (
          <span style={{ color: 'var(--dim)', fontSize: 10 }}>…</span>
        ) : '—'}
      </td>

      {/* WIN/LOSS badge */}
      <td style={{ ...tdStyle }}>
        {win4h === true ? (
          <span style={{
            fontSize: 9, padding: '1px 5px', borderRadius: 2,
            background: 'rgba(57,211,83,0.12)', color: 'var(--green)',
            fontWeight: 700, letterSpacing: '0.05em',
          }}>WIN</span>
        ) : win4h === false ? (
          <span style={{
            fontSize: 9, padding: '1px 5px', borderRadius: 2,
            background: 'rgba(248,81,73,0.12)', color: 'var(--red)',
            fontWeight: 700, letterSpacing: '0.05em',
          }}>LOSS</span>
        ) : isPending ? (
          <span style={{
            fontSize: 9, padding: '1px 5px', borderRadius: 2,
            background: 'rgba(240,165,0,0.12)', color: 'var(--amber)',
            fontWeight: 700, letterSpacing: '0.05em',
          }}>PEND</span>
        ) : (
          <span style={{ fontSize: 9, color: 'var(--dim)' }}>—</span>
        )}
      </td>

      {/* TRADE column */}
      <td style={{ ...tdStyle, fontSize: 10 }}>
        {tradeLink && tradeLink.trade_id != null ? (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
            <span style={{
              fontSize: 9, padding: '1px 5px', borderRadius: 2, fontWeight: 700, letterSpacing: '0.04em',
              background: tradeLink.trade_status === 'OPEN' ? 'rgba(88,166,255,0.12)' : 'rgba(255,255,255,0.06)',
              color: tradeLink.trade_status === 'OPEN' ? '#58a6ff' : 'var(--muted)',
            }}>
              {tradeLink.trade_status === 'OPEN' ? '● OPEN' : '✓ CLOSED'}
            </span>
            {tradeLink.pnl_pct != null && (
              <span style={{
                fontFamily: 'JetBrains Mono, monospace', fontSize: 10, fontWeight: 600,
                color: tradeLink.pnl_pct >= 0 ? 'var(--green)' : 'var(--red)',
              }}>
                {tradeLink.pnl_pct >= 0 ? '+' : ''}{tradeLink.pnl_pct.toFixed(1)}%
              </span>
            )}
            {tradeLink.slippage_pct != null && (
              <span style={{ fontSize: 9, color: 'var(--dim)' }}>
                slip {tradeLink.slippage_pct >= 0 ? '+' : ''}{tradeLink.slippage_pct.toFixed(2)}%
              </span>
            )}
          </div>
        ) : (
          <span style={{ color: 'var(--dim)', fontSize: 10 }}>—</span>
        )}
      </td>

      {/* Liq / Vol quick data */}
      <td style={{ ...tdStyle, color: 'var(--dim)', fontSize: 10 }}>
        {fmtUsd(sig.liquidity_usd)}
      </td>
    </tr>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────

export function SymbolPage() {
  const { symbol } = useParams<{ symbol: string }>()
  const navigate   = useNavigate()
  const sym        = (symbol || '').toUpperCase()

  const { data, isLoading } = useQuery<SymbolFull>({
    queryKey: ['symbol-full', sym],
    queryFn: () => api.get(`/symbols/${sym}/full?limit=100`).then(r => r.data),
    enabled: !!sym,
    refetchInterval: 30_000,
  })

  const { data: tradeLinkRaw } = useQuery<TradeLink[]>({
    queryKey: ['symbol-trade-map', sym],
    queryFn: () => api.get(`/symbols/${sym}/signal-trade-map?limit=100`).then(r => r.data),
    enabled: !!sym,
    refetchInterval: 60_000,
  })

  // Build map: signal_id → TradeLink (only entries with an actual trade)
  const tradeMap = new Map<number, TradeLink>()
  ;(tradeLinkRaw || []).forEach(t => {
    if (t.trade_id != null) tradeMap.set(t.signal_id, t)
  })

  const signals  = data?.signals || []
  const outcomes = data?.outcomes
  const markPrice = data?.mark_price

  // Derived stats
  const scored    = signals.filter(s => s.score_total != null)
  const alerts    = signals.filter(s => s.decision.includes('ALERT') && !s.decision.includes('DRY'))
  const avgScore  = scored.length > 0 ? scored.reduce((a, s) => a + (s.score_total || 0), 0) / scored.length : null
  const maxScore  = scored.length > 0 ? Math.max(...scored.map(s => s.score_total || 0)) : null
  const lastSignal = signals[0]
  const latestMint = signals.find(s => s.mint)?.mint
  const latestPair = signals.find(s => s.pair_address)?.pair_address

  // Price change: mark vs last known signal price
  const lastPrice  = signals.find(s => s.price_usd)?.price_usd
  const priceChange = markPrice && lastPrice ? ((markPrice - lastPrice) / lastPrice) * 100 : null

  const thStyle: React.CSSProperties = {
    color: 'var(--muted)', fontWeight: 400, padding: '4px 10px',
    borderBottom: '1px solid var(--border)', textAlign: 'left', fontSize: 10,
    letterSpacing: '0.04em', whiteSpace: 'nowrap',
  }

  if (isLoading) return <div style={{ padding: 40 }}><LoadingSpinner /></div>

  return (
    <div>
      {/* ── Header ──────────────────────────────────────────────────────────── */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 20, flexWrap: 'wrap' }}>
        <button
          onClick={() => navigate(-1)}
          style={{
            background: 'none', border: '1px solid var(--border)', color: 'var(--muted)',
            padding: '4px 10px', borderRadius: 3, cursor: 'pointer', fontSize: 11,
            fontFamily: 'JetBrains Mono, monospace',
          }}
        >
          ← back
        </button>

        <h2 style={{ fontSize: 22, fontWeight: 800, letterSpacing: '0.02em' }}>
          ${sym}
        </h2>

        {/* Live price */}
        {markPrice != null && (
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 6 }}>
            <span style={{
              fontFamily: 'JetBrains Mono, monospace', fontSize: 16, fontWeight: 700, color: 'var(--text)',
            }}>
              {fmtPrice(markPrice)}
            </span>
            {priceChange != null && (
              <span style={{
                fontSize: 12, fontWeight: 700,
                color: pctColor(priceChange),
              }}>
                {priceChange >= 0 ? '+' : ''}{priceChange.toFixed(2)}% vs last signal
              </span>
            )}
            <span style={{
              fontSize: 9, padding: '1px 5px', borderRadius: 2,
              background: 'rgba(57,211,83,0.12)', color: 'var(--green)',
              fontWeight: 700, marginLeft: 2,
            }}>LIVE</span>
          </div>
        )}

        {/* External links */}
        <div style={{ marginLeft: 'auto', display: 'flex', gap: 8 }}>
          {(latestPair || latestMint) && (
            <a
              href={`https://dexscreener.com/solana/${latestPair || latestMint}`}
              target="_blank" rel="noopener noreferrer"
              style={{
                color: '#58a6ff', fontSize: 11, textDecoration: 'none',
                padding: '4px 12px', border: '1px solid #58a6ff44', borderRadius: 3,
              }}
            >
              DexScreener ↗
            </a>
          )}
          {latestMint && (
            <a
              href={`https://pump.fun/coin/${latestMint}`}
              target="_blank" rel="noopener noreferrer"
              style={{
                color: 'var(--muted)', fontSize: 11, textDecoration: 'none',
                padding: '4px 12px', border: '1px solid var(--border)', borderRadius: 3,
              }}
            >
              Pump.fun ↗
            </a>
          )}
          {latestMint && (
            <a
              href={`https://birdeye.so/token/${latestMint}?chain=solana`}
              target="_blank" rel="noopener noreferrer"
              style={{
                color: 'var(--muted)', fontSize: 11, textDecoration: 'none',
                padding: '4px 12px', border: '1px solid var(--border)', borderRadius: 3,
              }}
            >
              Birdeye ↗
            </a>
          )}
        </div>
      </div>

      {signals.length === 0 && (
        <EmptyState message={`No signals found for $${sym}.`} />
      )}

      {signals.length > 0 && (
        <>
          {/* ── Stat bar ────────────────────────────────────────────────────── */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', gap: 8, marginBottom: 16 }}>
            {[
              {
                label: 'TOTAL SIGNALS', val: String(signals.length),
                color: 'var(--text)',
              },
              {
                label: 'ALERTS FIRED', val: String(alerts.length),
                color: alerts.length > 0 ? 'var(--green)' : 'var(--muted)',
              },
              {
                label: 'AVG SCORE',
                val: avgScore != null ? avgScore.toFixed(1) : '—',
                color: avgScore != null && avgScore >= 70 ? 'var(--green)' : 'var(--muted)',
              },
              {
                label: 'BEST SCORE',
                val: maxScore != null ? maxScore.toFixed(0) : '—',
                color: maxScore != null && maxScore >= 85 ? 'var(--green)' : maxScore != null ? 'var(--amber)' : 'var(--muted)',
              },
              {
                label: 'LAST SEEN',
                val: lastSignal ? timeAgo(lastSignal.ts_utc) : '—',
                color: 'var(--muted)',
                small: true,
              },
            ].map(({ label, val, color, small }) => (
              <div key={label} className="card" style={{ textAlign: 'center' }}>
                <div style={{ color: 'var(--muted)', fontSize: 10, marginBottom: 4, letterSpacing: '0.05em' }}>{label}</div>
                <div style={{ fontSize: small ? 14 : 20, fontWeight: 700, color }}>{val}</div>
              </div>
            ))}
          </div>

          {/* ── Outcome section ─────────────────────────────────────────────── */}
          {outcomes && outcomes.total > 0 && (
            <>
              <div style={{ color: 'var(--muted)', fontSize: 11, marginBottom: 10, fontWeight: 700, letterSpacing: '0.06em' }}>
                OUTCOME PERFORMANCE · {outcomes.total} alerts evaluated
              </div>

              {/* Outcome cards */}
              <div style={{ display: 'flex', gap: 8, marginBottom: 14 }}>
                <OutcomeCard label="1H RETURNS"  data={outcomes.outcomes_1h} />
                <OutcomeCard label="4H RETURNS"  data={outcomes.outcomes_4h} />
                <OutcomeCard label="24H RETURNS" data={outcomes.outcomes_24h} />
              </div>

              {/* Horizon bar chart */}
              <HorizonBars outcomes={outcomes} />
            </>
          )}

          {/* Conviction breakdown */}
          <div className="card" style={{ marginBottom: 16, display: 'flex', gap: 16, alignItems: 'center', flexWrap: 'wrap' }}>
            <span style={{ color: 'var(--muted)', fontSize: 10, fontWeight: 700, letterSpacing: '0.06em' }}>CONVICTION MIX</span>
            {[3, 2, 1].map(c => {
              const label = c === 3 ? 'A' : c === 2 ? 'B' : 'C'
              const count = signals.filter(s => s.conviction === c).length
              if (count === 0) return null
              return (
                <span key={c} style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                  <Badge label={label} color={c === 3 ? 'green' : c === 2 ? 'amber' : 'muted'} />
                  <span style={{ color: 'var(--muted)', fontSize: 11 }}>{count}×</span>
                </span>
              )
            })}

            {/* Regime breakdown */}
            <span style={{ color: 'var(--muted)', fontSize: 10, fontWeight: 700, marginLeft: 12, letterSpacing: '0.06em' }}>TOP REGIMES</span>
            {Object.entries(
              signals.reduce((acc, s) => {
                const r = s.regime_label || 'UNKNOWN'
                acc[r] = (acc[r] || 0) + 1
                return acc
              }, {} as Record<string, number>)
            )
              .sort((a, b) => b[1] - a[1])
              .slice(0, 3)
              .map(([regime, cnt]) => (
                <span key={regime} style={{ fontSize: 10, color: 'var(--muted)' }}>
                  {regime.replace(/_/g, ' ')} <span style={{ color: 'var(--text)' }}>{cnt}×</span>
                </span>
              ))}

            <span style={{ marginLeft: 'auto', color: 'var(--dim)', fontSize: 10 }}>last {signals.length} signals</span>
          </div>

          {/* ── Signal history table ─────────────────────────────────────────── */}
          <div style={{ color: 'var(--muted)', fontSize: 11, marginBottom: 10, fontWeight: 700, letterSpacing: '0.06em' }}>
            SIGNAL HISTORY
          </div>
          <div className="card" style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 780 }}>
              <thead>
                <tr>
                  <th style={thStyle}>#</th>
                  <th style={thStyle}>DATE</th>
                  <th style={thStyle}>TYPE</th>
                  <th style={thStyle}>SCORE</th>
                  <th style={thStyle}>CONV</th>
                  <th style={thStyle}>REGIME</th>
                  <th style={thStyle}>PRICE</th>
                  <th style={{ ...thStyle, color: 'var(--green)', opacity: 0.7 }}>1H RET</th>
                  <th style={{ ...thStyle, color: 'var(--green)', opacity: 0.85 }}>4H RET</th>
                  <th style={{ ...thStyle, color: 'var(--green)' }}>24H RET</th>
                  <th style={thStyle}>RESULT</th>
                  <th style={{ ...thStyle, color: '#58a6ff', opacity: 0.85 }}>TRADE</th>
                  <th style={thStyle}>LIQ</th>
                </tr>
              </thead>
              <tbody>
                {signals.map((sig, idx) => (
                  <SignalRow key={sig.id} sig={sig} idx={idx} tradeLink={tradeMap.get(sig.id)} />
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  )
}
