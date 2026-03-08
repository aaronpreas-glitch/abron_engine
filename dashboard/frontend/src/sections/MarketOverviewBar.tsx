/**
 * MarketOverviewBar — Patch 154
 * 5-card market overview strip.
 * Data: CoinMarketCap API, refreshed every 10 min.
 * F&G gauge uses smooth gradient arc; altcoin season uses 90d/top-100 methodology.
 */
import { useQuery } from '@tanstack/react-query'
import { api } from '../api'

// ── Types ──────────────────────────────────────────────────────────────────────

interface MarketData {
  market_cap_usd:        number | null
  market_cap_change_24h: number | null
  volume_24h_usd:        number | null
  volume_change_24h:     number | null
  btc_dominance:         number | null
  fear_greed_value:      number | null
  fear_greed_label:      string | null
  altcoin_season:        number | null
  rsi_proxy:             number | null
  market_cap_sparkline:  number[]
  volume_sparkline:      number[]
}

// ── Helpers ────────────────────────────────────────────────────────────────────

function fmt(n: number | null): string {
  if (n == null) return '—'
  if (n >= 1e12) return `$${(n / 1e12).toFixed(2)}T`
  if (n >= 1e9)  return `$${(n / 1e9).toFixed(2)}B`
  if (n >= 1e6)  return `$${(n / 1e6).toFixed(2)}M`
  return `$${n.toLocaleString()}`
}

function pctBadge(v: number | null) {
  if (v == null) return null
  const pos = v >= 0
  return (
    <span style={{
      fontSize: 10, fontWeight: 700,
      color: pos ? '#00d48a' : '#ef4444',
    }}>
      {pos ? '▲' : '▼'}{Math.abs(v).toFixed(2)}%
    </span>
  )
}

// ── Sparkline ─────────────────────────────────────────────────────────────────

function Sparkline({ values }: { values: number[] }) {
  const W = 88, H = 30
  if (!values || values.length < 2) {
    return (
      <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} style={{ marginTop: 6 }}>
        <line x1="0" y1={H/2} x2={W} y2={H/2} stroke="#1a2d3d" strokeWidth="1" strokeDasharray="3,3"/>
      </svg>
    )
  }
  const mn = Math.min(...values), mx = Math.max(...values)
  const range = mx - mn || 1
  const pts = values.map((v, i) => {
    const x = 1 + (i / (values.length - 1)) * (W - 2)
    const y = H - 2 - ((v - mn) / range) * (H - 4)
    return `${x.toFixed(1)},${y.toFixed(1)}`
  }).join(' ')

  const rising = values[values.length - 1] >= values[0]
  const col    = rising ? '#00d48a' : '#ef4444'

  const lastX = 1 + ((values.length - 1) / (values.length - 1)) * (W - 2)
  const areaD = `M 1,${H} L ${pts.split(' ').join(' L ')} L ${lastX},${H} Z`

  return (
    <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} style={{ marginTop: 6 }}>
      <defs>
        <linearGradient id={`sg-${rising}`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%"   stopColor={col} stopOpacity="0.18"/>
          <stop offset="100%" stopColor={col} stopOpacity="0"/>
        </linearGradient>
      </defs>
      <path d={areaD} fill={`url(#sg-${rising})`} />
      <polyline points={pts} fill="none" stroke={col} strokeWidth="1.5"
        strokeLinecap="round" strokeLinejoin="round"/>
    </svg>
  )
}

// ── Fear & Greed Gauge ────────────────────────────────────────────────────────
// Clean gradient arc: red (fear) → orange → yellow → green (greed)
// Shows filled arc up to the value; white dot indicator; no visible segment joints.

function FearGreedGauge({ value }: { value: number | null }) {
  const cx = 60, cy = 56, r = 44

  // Map 0–100 to a point on the top-half semicircle
  // pct=0  → left  (angle=π)
  // pct=50 → top   (angle=π/2)
  // pct=100 → right (angle=0)
  const toPoint = (pct: number): [number, number] => {
    const a = Math.PI * (1 - pct / 100)
    return [
      cx + r * Math.cos(a),
      cy - r * Math.sin(a),
    ]
  }

  // Build SVG arc path from fromPct to toPct (sweep-flag=0 = top half)
  const arcPath = (fromPct: number, toPct: number): string => {
    const [x1, y1] = toPoint(fromPct)
    const [x2, y2] = toPoint(toPct)
    const large = (toPct - fromPct) > 50 ? 1 : 0
    return `M${x1.toFixed(2)},${y1.toFixed(2)} A${r},${r} 0 ${large},0 ${x2.toFixed(2)},${y2.toFixed(2)}`
  }

  // Clamp so the arc never collapses to a degenerate zero-length path
  const clamp = value != null ? Math.max(1, Math.min(99, value)) : null
  const dot   = clamp != null ? toPoint(clamp) : null

  // Gradient spans the exact bounding box of the arc endpoints (left → right)
  const gradX1 = cx - r  // 16
  const gradX2 = cx + r  // 104

  return (
    <svg width="120" height="70" viewBox="0 0 120 70" style={{ overflow: 'visible' }}>
      <defs>
        {/* Horizontal gradient maps perfectly: left=red(0%), right=green(100%) */}
        <linearGradient id="fg-grad" gradientUnits="userSpaceOnUse"
          x1={gradX1} y1="0" x2={gradX2} y2="0">
          <stop offset="0%"   stopColor="#ef4444"/>
          <stop offset="28%"  stopColor="#f97316"/>
          <stop offset="50%"  stopColor="#eab308"/>
          <stop offset="72%"  stopColor="#84cc16"/>
          <stop offset="100%" stopColor="#22c55e"/>
        </linearGradient>
      </defs>

      {/* Dark background track (full arc) */}
      <path d={arcPath(0, 100)} fill="none" stroke="#111d28" strokeWidth="9" strokeLinecap="round"/>

      {/* Gradient-filled arc from 0 up to current value */}
      {clamp != null && (
        <path d={arcPath(0, clamp)} fill="none" stroke="url(#fg-grad)"
          strokeWidth="9" strokeLinecap="round"/>
      )}

      {/* White dot at current value */}
      {dot && (
        <circle cx={dot[0].toFixed(2)} cy={dot[1].toFixed(2)} r="5.5"
          fill="white" stroke="#050d14" strokeWidth="1.5"/>
      )}

      {/* Value number centered below arc */}
      <text x={cx} y={cy + 12} textAnchor="middle"
        fill="#e2e8f0" fontSize="23" fontWeight="700" fontFamily="JetBrains Mono, monospace">
        {value ?? '—'}
      </text>
    </svg>
  )
}

// ── Horizontal slider ──────────────────────────────────────────────────────────

function HSlider({
  value, leftLabel, rightLabel, unit = '/100',
}: {
  value: number | null
  leftLabel: string
  rightLabel: string
  unit?: string
}) {
  const pct = value != null ? Math.max(0, Math.min(100, value)) : null

  return (
    <div style={{ marginTop: 6 }}>
      {/* Value */}
      <div style={{ fontSize: 20, fontWeight: 700, color: '#e2e8f0', lineHeight: 1.2 }}>
        {value != null ? value : '—'}
        <span style={{ fontSize: 11, color: '#4d5a6e', marginLeft: 4 }}>{unit}</span>
      </div>
      {/* Track */}
      <div style={{
        position: 'relative', height: 6, marginTop: 8, marginBottom: 4,
        background: 'linear-gradient(to right, #ef4444 0%, #f97316 25%, #eab308 50%, #84cc16 75%, #22c55e 100%)',
        borderRadius: 3,
      }}>
        {pct != null && (
          <div style={{
            position: 'absolute',
            left: `${pct}%`, top: '50%',
            transform: 'translate(-50%, -50%)',
            width: 11, height: 11, borderRadius: '50%',
            background: 'white', boxShadow: '0 0 5px rgba(0,0,0,0.6)',
          }}/>
        )}
      </div>
      {/* Labels */}
      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 9, color: '#2d4060', marginTop: 1 }}>
        <span>{leftLabel}</span>
        <span>{rightLabel}</span>
      </div>
    </div>
  )
}

// ── Main component ─────────────────────────────────────────────────────────────

export function MarketOverviewBar() {
  const { data, isLoading } = useQuery<MarketData>({
    queryKey: ['market-global'],
    queryFn: async () => {
      const r = await api.get('/market-global')
      return r.data
    },
    refetchInterval: 10 * 60 * 1000,  // 10 min
    staleTime:        9 * 60 * 1000,
  })

  const card: React.CSSProperties = {
    background: '#071520',
    border: '1px solid #0d1f2d',
    borderRadius: 6,
    padding: '8px 12px',
    flex: 1,
    minWidth: 0,
    opacity: isLoading ? 0.5 : 1,
    transition: 'opacity 0.3s',
  }

  const title: React.CSSProperties = {
    fontSize: 10, fontWeight: 600, color: '#2d4060',
    letterSpacing: '0.08em', marginBottom: 2,
  }

  const fgVal   = data?.fear_greed_value ?? null
  const fgLabel = data?.fear_greed_label ?? null
  const fgColor = fgVal == null ? '#4d5a6e'
    : fgVal <= 25 ? '#ef4444'
    : fgVal <= 45 ? '#f97316'
    : fgVal <= 55 ? '#eab308'
    : fgVal <= 75 ? '#84cc16'
    : '#22c55e'

  // BTC dominance: display as rounded integer on 0-100 scale
  const btcDom      = data?.btc_dominance ?? null
  const btcDomRound = btcDom != null ? Math.round(btcDom) : null

  return (
    <div style={{
      display: 'flex', gap: 6, padding: '7px 14px',
      background: '#050d14',
      borderBottom: '1px solid #0d1f2d',
    }}>

      {/* ── Card 1: Market Cap ──────────────────────────────────────────── */}
      <div style={card}>
        <div style={title}>MARKET CAP</div>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 7 }}>
          <span style={{ fontSize: 17, fontWeight: 700, color: '#e2e8f0' }}>
            {fmt(data?.market_cap_usd ?? null)}
          </span>
          {pctBadge(data?.market_cap_change_24h ?? null)}
        </div>
        <Sparkline values={data?.market_cap_sparkline ?? []}/>
      </div>

      {/* ── Card 2: 24h Volume ──────────────────────────────────────────── */}
      <div style={card}>
        <div style={title}>24H VOLUME</div>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 7 }}>
          <span style={{ fontSize: 17, fontWeight: 700, color: '#e2e8f0' }}>
            {fmt(data?.volume_24h_usd ?? null)}
          </span>
          {pctBadge(data?.volume_change_24h ?? null)}
        </div>
        <Sparkline values={data?.volume_sparkline ?? []}/>
      </div>

      {/* ── Card 3: Fear & Greed ─────────────────────────────────────────── */}
      <div style={{ ...card, display: 'flex', flexDirection: 'column', alignItems: 'center', padding: '8px 8px 6px' }}>
        <div style={{ ...title, marginBottom: 2 }}>FEAR &amp; GREED</div>
        <FearGreedGauge value={fgVal}/>
        <div style={{
          fontSize: 11, fontWeight: 600, color: fgColor,
          marginTop: 3, letterSpacing: '0.05em',
          textTransform: 'lowercase',
        }}>
          {fgLabel || '—'}
        </div>
      </div>

      {/* ── Card 4: Altcoin Season ──────────────────────────────────────── */}
      <div style={card}>
        <div style={title}>ALTCOIN SEASON</div>
        <HSlider
          value={data?.altcoin_season ?? null}
          leftLabel="Bitcoin"
          rightLabel="Altcoin"
        />
      </div>

      {/* ── Card 5: BTC Dominance ───────────────────────────────────────── */}
      <div style={card}>
        <div style={title}>BTC DOMINANCE</div>
        <HSlider
          value={btcDomRound}
          leftLabel="Low"
          rightLabel="High"
          unit="%"
        />
      </div>

    </div>
  )
}
