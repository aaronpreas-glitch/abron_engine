/**
 * GlobalMetricsBar — CMC-style global market metrics strip.
 * Total Market Cap · BTC Dom · Fear & Greed gauge · Altcoin Season · Avg RSI
 */
import { useQuery } from '@tanstack/react-query'
import { api } from '../../api'

interface GlobalData {
  market_cap_usd:        number | null
  market_cap_change_24h: number | null
  btc_dominance:         number | null
  eth_dominance:         number | null
  fear_greed_value:      string | null
  fear_greed_label:      string | null
  altcoin_season:        number | null
  top20_positive_pct:    number | null
  avg_change_24h:        number | null
  rsi_proxy:             number | null
}

// ─── Formatters ──────────────────────────────────────────────────────────────

function fmtMcap(v: number | null) {
  if (v == null) return '—'
  if (v >= 1e12) return `$${(v / 1e12).toFixed(2)}T`
  if (v >= 1e9)  return `$${(v / 1e9).toFixed(1)}B`
  return `$${(v / 1e6).toFixed(0)}M`
}

function chgColor(v: number | null) {
  if (v == null) return 'var(--muted)'
  return v >= 0 ? 'var(--green)' : 'var(--red)'
}

function fgColor(v: string | null) {
  if (!v) return 'var(--muted)'
  const n = parseInt(v)
  if (n >= 75) return '#22c55e'
  if (n >= 55) return '#86efac'
  if (n >= 45) return '#f5a623'
  if (n >= 25) return '#f97316'
  return '#f04f4f'
}

function fgGradient(v: string | null): string {
  // Maps 0-100 to a color stop position on a red→yellow→green arc
  if (!v) return 'var(--muted)'
  const n = parseInt(v)
  if (n >= 75) return 'var(--green)'
  if (n >= 55) return '#86efac'
  if (n >= 45) return 'var(--amber)'
  if (n >= 25) return '#f97316'
  return 'var(--red)'
}

// ─── Sub-components ───────────────────────────────────────────────────────────

function MetricChip({
  label, value, sub, subColor, children,
}: {
  label: string
  value?: string
  sub?: string
  subColor?: string
  children?: React.ReactNode
}) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 3, flexShrink: 0 }}>
      <span style={{
        fontSize: 9, color: 'var(--dim)', letterSpacing: '0.14em',
        fontFamily: 'JetBrains Mono, monospace', textTransform: 'uppercase',
      }}>
        {label}
      </span>
      {value && (
        <span style={{
          fontSize: 13, fontWeight: 700, color: 'var(--text)',
          fontFamily: 'JetBrains Mono, monospace', lineHeight: 1,
          display: 'flex', alignItems: 'center', gap: 5,
        }}>
          {value}
          {sub && (
            <span style={{ fontSize: 10, fontWeight: 600, color: subColor || 'var(--muted)' }}>
              {sub}
            </span>
          )}
        </span>
      )}
      {children}
    </div>
  )
}

/** Semicircle Fear & Greed gauge */
function FearGreedGauge({ value, label }: { value: string | null; label: string | null }) {
  const n = value ? parseInt(value) : 0
  // Needle angle: 0=far left (-90°), 100=far right (+90°) → map to -90 to +90
  const angle = -90 + (n / 100) * 180
  const color = fgGradient(value)

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 3, flexShrink: 0 }}>
      <span style={{
        fontSize: 9, color: 'var(--dim)', letterSpacing: '0.14em',
        fontFamily: 'JetBrains Mono, monospace', textTransform: 'uppercase',
      }}>
        Fear & Greed
      </span>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        {/* Mini arc gauge */}
        <div style={{ position: 'relative', width: 44, height: 26, flexShrink: 0 }}>
          <svg width="44" height="26" viewBox="0 0 44 26">
            {/* Background arc */}
            <path
              d="M 4 24 A 18 18 0 0 1 40 24"
              fill="none" stroke="var(--border)" strokeWidth="4" strokeLinecap="round"
            />
            {/* Colored arc segments */}
            <path d="M 4 24 A 18 18 0 0 1 10.4 9.6" fill="none" stroke="#f04f4f" strokeWidth="4" strokeLinecap="round" opacity="0.8"/>
            <path d="M 10.4 9.6 A 18 18 0 0 1 22 4"   fill="none" stroke="#f97316" strokeWidth="4" strokeLinecap="round" opacity="0.8"/>
            <path d="M 22 4 A 18 18 0 0 1 33.6 9.6"   fill="none" stroke="#f5a623" strokeWidth="4" strokeLinecap="round" opacity="0.8"/>
            <path d="M 33.6 9.6 A 18 18 0 0 1 40 24"  fill="none" stroke="#22c55e" strokeWidth="4" strokeLinecap="round" opacity="0.8"/>
            {/* Needle */}
            <g transform={`rotate(${angle}, 22, 24)`}>
              <line x1="22" y1="24" x2="22" y2="8" stroke={color} strokeWidth="2" strokeLinecap="round" />
              <circle cx="22" cy="24" r="2.5" fill={color} />
            </g>
          </svg>
        </div>
        <div>
          <div style={{
            fontSize: 15, fontWeight: 800, color: fgColor(value),
            fontFamily: 'JetBrains Mono, monospace', lineHeight: 1,
          }}>
            {value ?? '—'}
          </div>
          <div style={{ fontSize: 9, color: 'var(--muted)', fontFamily: 'JetBrains Mono, monospace', marginTop: 2 }}>
            {label ?? ''}
          </div>
        </div>
      </div>
    </div>
  )
}

/** Horizontal bar slider (like CMC's altcoin season / RSI bar) */
function SliderBar({
  value, min = 0, max = 100, leftLabel, rightLabel, color,
}: {
  value: number | null
  min?: number
  max?: number
  leftLabel: string
  rightLabel: string
  color: string
}) {
  const pct = value != null ? Math.max(0, Math.min(100, ((value - min) / (max - min)) * 100)) : 50
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 4, minWidth: 110 }}>
      <div style={{ position: 'relative', height: 4, background: 'var(--border)', borderRadius: 2 }}>
        {/* gradient track */}
        <div style={{
          position: 'absolute', inset: 0, borderRadius: 2,
          background: 'linear-gradient(to right, #f04f4f, #f5a623, #22c55e)',
          opacity: 0.25,
        }} />
        {/* thumb */}
        <div style={{
          position: 'absolute', top: '50%', transform: 'translate(-50%, -50%)',
          left: `${pct}%`, width: 8, height: 8, borderRadius: '50%',
          background: color, boxShadow: `0 0 4px ${color}`,
          transition: 'left 0.4s ease',
        }} />
      </div>
      <div style={{ display: 'flex', justifyContent: 'space-between' }}>
        <span style={{ fontSize: 8.5, color: 'var(--red)', fontFamily: 'JetBrains Mono, monospace' }}>{leftLabel}</span>
        <span style={{ fontSize: 8.5, color: 'var(--green)', fontFamily: 'JetBrains Mono, monospace' }}>{rightLabel}</span>
      </div>
    </div>
  )
}

function Sep() {
  return <div style={{ width: 1, height: 32, background: 'var(--border)', flexShrink: 0 }} />
}

// ─── Main Component ───────────────────────────────────────────────────────────

export function GlobalMetricsBar() {
  const { data } = useQuery<GlobalData>({
    queryKey: ['market-global'],
    queryFn: () => api.get('/market-global').then(r => r.data),
    refetchInterval: 60_000,
    staleTime: 55_000,
  })

  if (!data) return (
    <div style={{
      background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 6,
      padding: '10px 16px', marginBottom: 14, height: 56,
      display: 'flex', alignItems: 'center',
    }}>
      <span style={{ fontSize: 10, color: 'var(--dim)', fontFamily: 'JetBrains Mono, monospace' }}>
        Loading market data…
      </span>
    </div>
  )

  const altcoinSeason  = data.altcoin_season ?? 0
  const rsi            = data.rsi_proxy ?? 50
  const altcoinColor   = altcoinSeason >= 75 ? 'var(--green)' : altcoinSeason >= 50 ? '#f5a623' : 'var(--red)'
  const rsiColor       = rsi >= 70 ? 'var(--red)' : rsi <= 30 ? 'var(--green)' : '#f5a623'
  const rsiLabel       = rsi >= 70 ? 'Overbought' : rsi <= 30 ? 'Oversold' : 'Neutral'

  return (
    <div style={{
      background: 'var(--surface)',
      border: '1px solid var(--border)',
      borderRadius: 6,
      padding: '10px 18px',
      marginBottom: 14,
      display: 'flex',
      alignItems: 'center',
      gap: 20,
      overflowX: 'auto',
      flexWrap: 'nowrap',
      scrollbarWidth: 'none',
    }}>

      {/* Total Market Cap */}
      <MetricChip
        label="Market Cap"
        value={fmtMcap(data.market_cap_usd)}
        sub={data.market_cap_change_24h != null
          ? `${data.market_cap_change_24h >= 0 ? '+' : ''}${data.market_cap_change_24h.toFixed(2)}%`
          : undefined}
        subColor={chgColor(data.market_cap_change_24h)}
      />

      <Sep />

      {/* BTC Dominance */}
      <MetricChip label="BTC Dom">
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <span style={{ fontSize: 13, fontWeight: 700, color: '#f7931a', fontFamily: 'JetBrains Mono, monospace', lineHeight: 1 }}>
              {data.btc_dominance != null ? `${data.btc_dominance.toFixed(1)}%` : '—'}
            </span>
            <span style={{ fontSize: 10, color: 'var(--muted)', fontFamily: 'JetBrains Mono, monospace' }}>
              ETH {data.eth_dominance != null ? `${data.eth_dominance.toFixed(1)}%` : '—'}
            </span>
          </div>
          <div style={{ height: 3, borderRadius: 2, background: 'var(--border)', width: 100, overflow: 'hidden' }}>
            <div style={{
              height: '100%', borderRadius: 2,
              width: `${data.btc_dominance ?? 50}%`,
              background: 'linear-gradient(to right, #f7931a, #f59e0b)',
            }} />
          </div>
        </div>
      </MetricChip>

      <Sep />

      {/* Fear & Greed */}
      <FearGreedGauge value={data.fear_greed_value} label={data.fear_greed_label} />

      <Sep />

      {/* Altcoin Season */}
      <MetricChip label="Altcoin Season">
        <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 5 }}>
            <span style={{ fontSize: 15, fontWeight: 800, color: altcoinColor, fontFamily: 'JetBrains Mono, monospace', lineHeight: 1 }}>
              {altcoinSeason}
            </span>
            <span style={{ fontSize: 9, color: 'var(--muted)', fontFamily: 'JetBrains Mono, monospace' }}>/100</span>
            <span style={{ fontSize: 9, fontWeight: 600, color: altcoinColor, fontFamily: 'JetBrains Mono, monospace' }}>
              {altcoinSeason >= 75 ? 'ALT SEASON' : altcoinSeason >= 50 ? 'MIXED' : 'BTC SEASON'}
            </span>
          </div>
          <SliderBar value={altcoinSeason} leftLabel="Bitcoin" rightLabel="Altcoin" color={altcoinColor} />
        </div>
      </MetricChip>

      <Sep />

      {/* Avg Crypto RSI */}
      <MetricChip label="Avg Crypto RSI">
        <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 5 }}>
            <span style={{ fontSize: 15, fontWeight: 800, color: rsiColor, fontFamily: 'JetBrains Mono, monospace', lineHeight: 1 }}>
              {rsi.toFixed(1)}
            </span>
            <span style={{
              fontSize: 8.5, fontWeight: 700, padding: '1px 5px', borderRadius: 3,
              background: rsi >= 70 ? 'rgba(240,79,79,0.15)' : rsi <= 30 ? 'rgba(0,212,138,0.15)' : 'rgba(245,166,35,0.15)',
              color: rsiColor,
              fontFamily: 'JetBrains Mono, monospace',
            }}>
              {rsiLabel}
            </span>
          </div>
          <SliderBar value={rsi} leftLabel="Oversold" rightLabel="Overbought" color={rsiColor} />
        </div>
      </MetricChip>

      {/* Top 20 breadth */}
      <Sep />
      <MetricChip
        label="Top 20 Up"
        value={data.top20_positive_pct != null ? `${data.top20_positive_pct}%` : '—'}
        sub={data.avg_change_24h != null
          ? `avg ${data.avg_change_24h >= 0 ? '+' : ''}${data.avg_change_24h.toFixed(1)}%`
          : undefined}
        subColor={chgColor(data.avg_change_24h)}
      />

    </div>
  )
}
