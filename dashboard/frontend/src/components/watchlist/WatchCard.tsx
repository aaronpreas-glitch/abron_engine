import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import type { WatchCard as WC } from '../../types'
import { PctChange } from '../shared/PctChange'

const STATUS_STYLE: Record<string, { color: string; bg: string; border: string }> = {
  Momentum:  { color: '#39d353', bg: '#1a3a22', border: '#2d6a35' },
  Reclaim:   { color: '#f0a500', bg: '#3a2c00', border: '#6a4e00' },
  Volatile:  { color: '#f0a500', bg: '#3a2c00', border: '#6a4e00' },
  Range:     { color: '#8b949e', bg: '#1c2128', border: '#30363d' },
  Breakdown: { color: '#f85149', bg: '#3a1a1a', border: '#6a2a2a' },
  Illiquid:  { color: '#8b949e', bg: '#1c2128', border: '#30363d' },
  NoData:    { color: '#8b949e', bg: '#1c2128', border: '#30363d' },
}

const HEAT_MAP: Record<string, string> = {
  HOT:    '🔥🔥🔥',
  ACTIVE: '🔥🔥',
  MOVING: '🔥',
  COLD:   '❄️',
}

function fmtUsd(v: number | null) {
  if (v == null) return '—'
  if (v >= 1e9) return `$${(v / 1e9).toFixed(2)}B`
  if (v >= 1e6) return `$${(v / 1e6).toFixed(2)}M`
  if (v >= 1e3) return `$${(v / 1e3).toFixed(1)}K`
  return `$${v.toFixed(2)}`
}

function fmtPrice(v: number | null) {
  if (v == null) return '—'
  if (v < 0.001)  return `$${v.toFixed(8)}`.replace(/0+$/, '')
  if (v < 1)      return `$${v.toFixed(6)}`.replace(/0+$/, '')
  return `$${v.toFixed(4)}`
}

export function WatchCard({ card, isChanged, prevStatus }: { card: WC; isChanged?: boolean; prevStatus?: string }) {
  const style = STATUS_STYLE[card.status] || STATUS_STYLE.Range
  const navigate = useNavigate()

  // Auto-dismiss the CHANGED badge after 5 minutes
  const [showBadge, setShowBadge] = useState(false)
  useEffect(() => {
    if (isChanged) {
      setShowBadge(true)
      const t = setTimeout(() => setShowBadge(false), 5 * 60 * 1000)
      return () => clearTimeout(t)
    }
  }, [isChanged])

  return (
    <div className="card" style={{ borderTop: `2px solid ${style.border}`, position: 'relative' }}>
      {showBadge && prevStatus && (
        <div style={{
          position: 'absolute', top: 8, right: 8,
          fontSize: 8, fontWeight: 700, padding: '2px 6px', borderRadius: 3,
          background: 'rgba(240,165,0,0.15)', color: 'var(--amber)',
          border: '1px solid rgba(240,165,0,0.3)',
          letterSpacing: '0.08em', fontFamily: 'JetBrains Mono, monospace',
          whiteSpace: 'nowrap',
        }}>
          ⚡ {prevStatus} → {card.status}
        </div>
      )}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
        <span
          style={{ fontWeight: 700, fontSize: 14, cursor: 'pointer' }}
          onClick={() => navigate(`/symbol/${card.symbol}`)}
          title={`View $${card.symbol} signal history`}
        >
          ${card.symbol}
        </span>
        <span style={{
          padding: '1px 6px', borderRadius: 3, fontSize: 11, fontWeight: 600,
          color: style.color, background: style.bg, border: `1px solid ${style.border}`,
        }}>
          {card.status}
        </span>
        {card.heat && (
          <span style={{ marginLeft: 'auto', fontSize: 12 }}>{HEAT_MAP[card.heat] || ''}</span>
        )}
      </div>

      {card.has_live_data ? (
        <>
          <div style={{ fontWeight: 600, fontSize: 15, marginBottom: 6, color: 'var(--text)' }}>
            {fmtPrice(card.price)}
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '3px 8px', fontSize: 11 }}>
            <div><span style={{ color: 'var(--muted)' }}>Cap  </span>{fmtUsd(card.market_cap)}</div>
            <div><span style={{ color: 'var(--muted)' }}>Liq  </span>{fmtUsd(card.liquidity)}</div>
            <div><span style={{ color: 'var(--muted)' }}>Vol  </span>{fmtUsd(card.volume_24h)}</div>
            <div><span style={{ color: 'var(--muted)' }}>Txns/1h  </span>{card.txns_h1 ?? '—'}</div>
            <div><span style={{ color: 'var(--muted)' }}>24h  </span><PctChange value={card.change_24h} /></div>
            <div><span style={{ color: 'var(--muted)' }}>1h   </span><PctChange value={card.change_1h} /></div>
          </div>
          <div style={{ marginTop: 8, fontSize: 10, color: 'var(--muted)', borderTop: '1px solid var(--border)', paddingTop: 6 }}>
            {card.reason}
          </div>
        </>
      ) : (
        <div style={{ color: 'var(--muted)', fontSize: 11, padding: '8px 0' }}>No live data</div>
      )}
    </div>
  )
}
