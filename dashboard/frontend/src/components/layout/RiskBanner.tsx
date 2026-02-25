import { useState, useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../../api'
import type { RiskState } from '../../types'

function modeColor(mode: string) {
  if (mode === 'DEFENSIVE') return 'var(--red)'
  if (mode === 'CAUTIOUS')  return 'var(--amber)'
  return 'var(--green)'
}

function fmtPrice(sym: string, v: number | null | undefined) {
  if (v == null) return '—'
  if (sym === 'BTC') return `$${v.toLocaleString('en-US', { maximumFractionDigits: 0 })}`
  if (sym === 'ETH') return `$${v.toLocaleString('en-US', { maximumFractionDigits: 0 })}`
  return `$${v.toFixed(2)}`
}

interface Prices {
  BTC: { price: number | null; change_24h: number | null }
  ETH: { price: number | null; change_24h: number | null }
  SOL: { price: number | null; change_24h: number | null }
}

const MONO: React.CSSProperties = { fontFamily: 'JetBrains Mono, monospace' }

function VSep() {
  return <div style={{ width: 1, height: 14, background: 'rgba(255,255,255,0.1)', flexShrink: 0 }} />
}

function PricePill({ sym, price, change }: { sym: string; price: number | null | undefined; change: number | null | undefined }) {
  const up = (change ?? 0) >= 0
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
      <span style={{ fontSize: 9, color: 'rgba(255,255,255,0.28)', ...MONO, letterSpacing: '0.12em', fontWeight: 600 }}>
        {sym}
      </span>
      <span style={{ fontSize: 11.5, fontWeight: 700, color: 'var(--text)', ...MONO }}>
        {fmtPrice(sym, price)}
      </span>
      {change != null && (
        <span style={{
          fontSize: 9.5, ...MONO,
          color: up ? 'var(--green)' : 'var(--red)',
        }}>
          {up ? '+' : ''}{change.toFixed(1)}%
        </span>
      )}
    </div>
  )
}

export function RiskBanner() {
  const [time, setTime] = useState('')

  useEffect(() => {
    const tick = () => {
      const now = new Date()
      setTime(now.toUTCString().slice(17, 25) + ' UTC')
    }
    tick()
    const id = setInterval(tick, 1000)
    return () => clearInterval(id)
  }, [])

  const { data } = useQuery<RiskState>({
    queryKey: ['risk-state'],
    queryFn: () => api.get('/risk/state').then(r => r.data),
    refetchInterval: 30_000,
  })

  const { data: prices } = useQuery<Prices>({
    queryKey: ['crypto-prices'],
    queryFn: () => api.get('/prices').then(r => r.data),
    refetchInterval: 30_000,
    staleTime: 25_000,
  })

  const color = data ? modeColor(data.mode) : 'var(--muted)'

  return (
    <header style={{
      background: 'rgba(4,6,12,0.7)',
      backdropFilter: 'blur(24px) saturate(180%)',
      WebkitBackdropFilter: 'blur(24px) saturate(180%)',
      borderBottom: '1px solid rgba(255,255,255,0.065)',
      padding: '0 18px',
      height: 36,
      display: 'flex',
      alignItems: 'center',
      gap: 14,
      flexShrink: 0,
      position: 'relative',
      overflow: 'hidden',
    }}>

      {/* Left mode accent stripe */}
      {data && (
        <div style={{
          position: 'absolute', left: 0, top: 0, bottom: 0,
          width: 2, background: color, opacity: 0.9,
        }} />
      )}

      {/* ── Risk mode ────────────────────────────────────── */}
      {data && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
          <div style={{
            width: 5, height: 5, borderRadius: '50%',
            background: color, boxShadow: `0 0 7px ${color}`,
            animation: 'pulse-glow 2.5s ease-in-out infinite', flexShrink: 0,
          }} />
          <span style={{
            fontSize: 10, fontWeight: 700, color,
            letterSpacing: '0.1em', ...MONO,
          }}>
            {data.mode}
          </span>
          {data.paused && (
            <span style={{
              fontSize: 9, color: 'var(--red)', fontWeight: 600, ...MONO,
              background: 'rgba(239,68,68,0.1)',
              border: '1px solid rgba(239,68,68,0.25)',
              borderRadius: 4, padding: '1px 5px',
              animation: 'blink 1.8s ease-in-out infinite',
            }}>
              PAUSED
            </span>
          )}
        </div>
      )}

      {data && <VSep />}

      {/* ── Engine chips ─────────────────────────────────── */}
      {data && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <span style={{ fontSize: 10, ...MONO, color: 'rgba(255,255,255,0.32)' }}>
            streak <span style={{ color: data.streak ? 'var(--green)' : 'var(--muted)', fontWeight: 700 }}>{data.streak ?? 0}</span>
          </span>
          <span style={{ fontSize: 10, ...MONO, color: 'rgba(255,255,255,0.32)' }}>
            size <span style={{
              color: (data.size_multiplier ?? 1) >= 1 ? 'var(--green)' : (data.size_multiplier ?? 1) >= 0.75 ? 'var(--amber)' : 'var(--red)',
              fontWeight: 700,
            }}>
              {Math.round((data.size_multiplier ?? 1) * 100)}%
            </span>
          </span>
          {data.min_confidence && (
            <span style={{ fontSize: 10, ...MONO, color: 'rgba(255,255,255,0.32)' }}>
              grade <span style={{ color, fontWeight: 700 }}>{data.min_confidence}+</span>
            </span>
          )}
        </div>
      )}

      <VSep />

      {/* ── Prices ───────────────────────────────────────── */}
      {prices && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
          <PricePill sym="BTC" price={prices.BTC?.price} change={prices.BTC?.change_24h} />
          <VSep />
          <PricePill sym="ETH" price={prices.ETH?.price} change={prices.ETH?.change_24h} />
          <VSep />
          <PricePill sym="SOL" price={prices.SOL?.price} change={prices.SOL?.change_24h} />
        </div>
      )}

      {/* ── Clock ────────────────────────────────────────── */}
      <span style={{
        marginLeft: 'auto', fontSize: 10, color: 'rgba(255,255,255,0.22)',
        ...MONO, letterSpacing: '0.06em', flexShrink: 0,
      }}>
        {time}
      </span>
    </header>
  )
}
