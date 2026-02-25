import { useState, useEffect } from 'react'

interface SniperCandidate {
  mint: string
  symbol: string
  ath_price: number
  last_price: number
  drawdown_pct: number
  leg: string
  ath_ts_utc: string
  last_seen_utc?: string
}

interface SniperData {
  prime_candidates: SniperCandidate[]
  approaching: SniperCandidate[]
  total_tracked: number
  updated_at: string
  error?: string
}

function depthColor(dd: number): string {
  if (dd >= 90) return '#ef4444'
  if (dd >= 85) return '#f97316'
  if (dd >= 75) return '#f59e0b'
  return '#eab308'
}

function depthEmoji(dd: number): string {
  if (dd >= 90) return '🔥🔥'
  if (dd >= 85) return '🔥'
  return '📍'
}

function fmtPrice(p: number): string {
  if (!p) return '—'
  if (p < 0.000001) return '$' + p.toExponential(2)
  if (p < 0.01) return '$' + p.toFixed(6)
  if (p < 1) return '$' + p.toFixed(4)
  return '$' + p.toFixed(3)
}

function fmtAge(ts: string): string {
  if (!ts) return '—'
  const diff = Date.now() - new Date(ts).getTime()
  const h = Math.floor(diff / 3600000)
  const m = Math.floor((diff % 3600000) / 60000)
  if (h > 48) return `${Math.floor(h/24)}d ago`
  if (h > 0) return `${h}h ago`
  return `${m}m ago`
}

const BASE = import.meta.env.VITE_API_BASE ?? ''

export default function SniperPanel() {
  const [data, setData] = useState<SniperData | null>(null)
  const [loading, setLoading] = useState(true)

  const load = async () => {
    try {
      const r = await fetch(`${BASE}/api/sniper/second-leg`)
      const j = await r.json()
      setData(j)
    } catch {
      // silent
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
    const t = setInterval(load, 5 * 60 * 1000)
    return () => clearInterval(t)
  }, [])

  const cardStyle: React.CSSProperties = {
    background: '#0f1117',
    border: '1px solid #1e2130',
    borderRadius: 8,
    padding: '16px 20px',
    fontFamily: 'monospace',
  }

  const headerStyle: React.CSSProperties = {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 12,
  }

  const sectionLabel: React.CSSProperties = {
    fontSize: 11,
    fontWeight: 700,
    letterSpacing: '0.08em',
    textTransform: 'uppercase' as const,
    marginBottom: 6,
    marginTop: 12,
  }

  const rowStyle: React.CSSProperties = {
    display: 'grid',
    gridTemplateColumns: '80px 60px 1fr 1fr 60px',
    gap: 8,
    padding: '5px 0',
    borderBottom: '1px solid #1e2130',
    fontSize: 12,
    alignItems: 'center',
  }

  if (loading) {
    return (
      <div style={cardStyle}>
        <div style={{ color: '#6b7280', fontSize: 13 }}>Loading sniper data…</div>
      </div>
    )
  }

  const prime = data?.prime_candidates ?? []
  const approaching = data?.approaching ?? []
  const tracked = data?.total_tracked ?? 0

  return (
    <div style={cardStyle}>
      <div style={headerStyle}>
        <span style={{ color: '#f1f5f9', fontWeight: 700, fontSize: 14 }}>
          🎯 Second Leg Sniper
        </span>
        <span style={{ color: '#6b7280', fontSize: 11 }}>
          {tracked} tokens tracked
        </span>
      </div>

      {prime.length === 0 && approaching.length === 0 ? (
        <div style={{ color: '#6b7280', fontSize: 12, lineHeight: 1.6 }}>
          <div>No second-leg candidates yet.</div>
          <div style={{ marginTop: 4 }}>
            Engine is building ATH history — check back in 24-48h.
          </div>
        </div>
      ) : (
        <>
          {prime.length > 0 && (
            <>
              <div style={{ ...sectionLabel, color: '#ef4444' }}>
                🔴 Prime Zone — 75-95% below ATH ({prime.length})
              </div>
              {/* Column headers */}
              <div style={{ ...rowStyle, color: '#6b7280', fontSize: 10, borderBottom: '1px solid #2d3148' }}>
                <span>SYMBOL</span>
                <span>DOWN</span>
                <span>ATH</span>
                <span>NOW</span>
                <span>SEEN</span>
              </div>
              {prime.slice(0, 10).map((c, i) => (
                <div key={i} style={rowStyle}>
                  <span style={{ color: '#f1f5f9', fontWeight: 700 }}>
                    {depthEmoji(c.drawdown_pct)} {c.symbol}
                  </span>
                  <span style={{ color: depthColor(c.drawdown_pct), fontWeight: 700 }}>
                    ↓{c.drawdown_pct.toFixed(0)}%
                  </span>
                  <span style={{ color: '#9ca3af' }}>{fmtPrice(c.ath_price)}</span>
                  <span style={{ color: '#f1f5f9' }}>{fmtPrice(c.last_price)}</span>
                  <span style={{ color: '#6b7280', fontSize: 10 }}>{fmtAge(c.last_seen_utc ?? c.ath_ts_utc)}</span>
                </div>
              ))}
            </>
          )}

          {approaching.length > 0 && (
            <>
              <div style={{ ...sectionLabel, color: '#f59e0b' }}>
                🟡 Approaching — 60-74% below ATH ({approaching.length})
              </div>
              {approaching.slice(0, 5).map((c, i) => (
                <div key={i} style={{ ...rowStyle, opacity: 0.8 }}>
                  <span style={{ color: '#d1d5db', fontWeight: 600 }}>
                    📊 {c.symbol}
                  </span>
                  <span style={{ color: '#f59e0b', fontWeight: 600 }}>
                    ↓{c.drawdown_pct.toFixed(0)}%
                  </span>
                  <span style={{ color: '#6b7280' }}>{fmtPrice(c.ath_price)}</span>
                  <span style={{ color: '#9ca3af' }}>{fmtPrice(c.last_price)}</span>
                  <span style={{ color: '#6b7280', fontSize: 10 }}>{fmtAge(c.last_seen_utc ?? c.ath_ts_utc)}</span>
                </div>
              ))}
            </>
          )}
        </>
      )}

      <div style={{ marginTop: 12, paddingTop: 8, borderTop: '1px solid #1e2130', color: '#4b5563', fontSize: 10 }}>
        Strategy: Entry zone = 80-90% below ATH • Wait for volume + social confirmation
      </div>
    </div>
  )
}
