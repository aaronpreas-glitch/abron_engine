interface PortfolioSignal {
  coin: string; signal: 'ACCUMULATE' | 'HOLD' | 'REDUCE'
  price_usd: number | null; reason: string | null
  fear_greed: number | null; btc_dom_pct: number | null
  regime: string | null; regime_score: number | null
  chg_4w_pct: number | null; ts_utc: string
}
interface Props {
  signals: PortfolioSignal[]
  fearGreed: number | null; btcDom: number | null
  lastUpdated: string | null; loading: boolean
  livePrices?: Array<{ coin: string; price: number; chg24: number | null }>
}

const MONO = { fontFamily: 'JetBrains Mono, monospace' }

function sigColor(s: string) { return s === 'ACCUMULATE' ? '#00d48a' : s === 'REDUCE' ? '#ef4444' : '#f59e0b' }
function sigIcon(s: string)  { return s === 'ACCUMULATE' ? '▲' : s === 'REDUCE' ? '▼' : '■' }
function fgLabel(v: number)  {
  if (v <= 24) return 'Extreme Fear'; if (v <= 44) return 'Fear'
  if (v <= 54) return 'Neutral'; if (v <= 74) return 'Greed'; return 'Extreme Greed'
}
function fgColor(v: number)  {
  if (v <= 24) return '#00d48a'; if (v <= 44) return '#7cb9a0'
  if (v <= 54) return '#a0aec0'; if (v <= 74) return '#f59e0b'; return '#ef4444'
}
function fp(p: number | null) {
  if (p == null) return '—'
  if (p >= 1000) return `$${p.toLocaleString('en-US', { maximumFractionDigits: 0 })}`
  if (p >= 1) return `$${p.toFixed(2)}`; return `$${p.toFixed(4)}`
}
function chgColor(c: number | null) {
  if (c == null) return '#4d5a6e'; if (c > 0) return '#00d48a'
  if (c < -20) return '#ef4444'; return '#f59e0b'
}
function minsAgo(ts: string | null) {
  if (!ts) return ''
  try {
    const d = new Date(ts.endsWith('Z') ? ts : ts + 'Z')
    const m = Math.round((Date.now() - d.getTime()) / 60000)
    if (m < 2) return 'just now'; if (m < 60) return `${m}m ago`
    return `${Math.round(m / 60)}h ago`
  } catch { return '' }
}

export function PortfolioSection({ signals, fearGreed, btcDom, lastUpdated, loading, livePrices }: Props) {
  const priceMap: Record<string, number> = {}
  for (const p of livePrices ?? []) priceMap[p.coin.toUpperCase()] = p.price

  return (
    <div>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 14 }}>
        <span className="section-label">PORTFOLIO WATCHMAN</span>
        {lastUpdated && (
          <span style={{ color: '#2d4060', fontSize: 9, ...MONO }}>updated {minsAgo(lastUpdated)}</span>
        )}
      </div>

      {/* Macro pills */}
      {(fearGreed != null || btcDom != null) && (
        <div style={{ display: 'flex', gap: 8, marginBottom: 14, flexWrap: 'wrap' }}>
          {fearGreed != null && (
            <div style={{
              display: 'flex', alignItems: 'center', gap: 8,
              padding: '8px 14px', borderRadius: 8,
              background: `${fgColor(fearGreed)}10`,
              border: `1px solid ${fgColor(fearGreed)}30`,
            }}>
              <span style={{ fontSize: 9, color: '#4a6280', letterSpacing: '0.08em', ...MONO }}>FEAR & GREED</span>
              <span style={{ fontSize: 20, fontWeight: 800, color: fgColor(fearGreed), lineHeight: 1, ...MONO }}>{fearGreed}</span>
              <span style={{ fontSize: 10, color: fgColor(fearGreed), ...MONO }}>{fgLabel(fearGreed)}</span>
            </div>
          )}
          {btcDom != null && (
            <div style={{
              display: 'flex', alignItems: 'center', gap: 8,
              padding: '8px 14px', borderRadius: 8,
              background: 'rgba(255,255,255,0.03)',
              border: '1px solid rgba(255,255,255,0.07)',
            }}>
              <span style={{ fontSize: 9, color: '#4a6280', letterSpacing: '0.08em', ...MONO }}>BTC DOMINANCE</span>
              <span style={{ fontSize: 18, fontWeight: 800, color: btcDom > 65 ? '#ef4444' : btcDom > 58 ? '#f59e0b' : '#a0aec0', lineHeight: 1, ...MONO }}>
                {btcDom.toFixed(1)}%
              </span>
            </div>
          )}
        </div>
      )}

      {/* Signals table */}
      {loading ? (
        <div style={{ color: '#4d5a6e', fontSize: 10, ...MONO }}>loading signals…</div>
      ) : signals.length === 0 ? (
        <div style={{ color: '#2d4060', fontSize: 10, ...MONO }}>signals load ~15s after startup, refresh every 4h</div>
      ) : (
        <table className="data-table">
          <thead>
            <tr>
              <th>Coin</th>
              <th>Signal</th>
              <th style={{ textAlign: 'right' }}>Price</th>
              <th style={{ textAlign: 'right' }}>4W Change</th>
              <th>Reason</th>
            </tr>
          </thead>
          <tbody>
            {signals.map(s => (
              <tr key={s.coin}>
                <td style={{ fontWeight: 700, color: '#c0cfe0' }}>{s.coin}</td>
                <td>
                  <span style={{
                    display: 'inline-flex', alignItems: 'center', gap: 5,
                    padding: '2px 8px', borderRadius: 4,
                    background: `${sigColor(s.signal)}12`,
                    border: `1px solid ${sigColor(s.signal)}30`,
                    color: sigColor(s.signal), fontWeight: 700, fontSize: 10,
                    letterSpacing: '0.05em', ...MONO,
                  }}>
                    {sigIcon(s.signal)} {s.signal}
                  </span>
                </td>
                <td style={{ textAlign: 'right', color: '#a0aec0' }}>{fp(priceMap[s.coin.toUpperCase()] ?? s.price_usd)}</td>
                <td style={{ textAlign: 'right', color: chgColor(s.chg_4w_pct) }}>
                  {s.chg_4w_pct != null ? (s.chg_4w_pct >= 0 ? '+' : '') + s.chg_4w_pct.toFixed(1) + '%' : '—'}
                </td>
                <td style={{ color: '#4d5a6e', fontSize: 10 }}>{s.reason || '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <div style={{ color: '#2d4060', fontSize: 9, marginTop: 8, ...MONO }}>
        Advisory only · signals update every 4h · regime + Fear & Greed + BTC dom + 4w momentum
      </div>
    </div>
  )
}
