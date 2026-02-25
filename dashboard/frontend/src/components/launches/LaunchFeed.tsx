/**
 * LaunchFeed — Real-time new token launch detections.
 *
 * Displays live output from the launch_listener.py WebSocket + polling streams.
 * Shows every detected token with score, liquidity, age, source, and alert status.
 *
 * When LAUNCH_LISTENER_ENABLED=false the component shows a disabled state
 * rather than an error, since the engine simply isn't running the listener.
 */
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../../api'

// ─── Types ────────────────────────────────────────────────────────────────────

interface Launch {
  mint: string
  symbol: string
  score: number
  liquidity: number
  volume_5m: number | null
  volume_1h: number | null
  change_1h: number | null
  age_minutes: number | null
  market_cap: number | null
  price: number | null
  source: string
  alerted: boolean
  _ts: string
}

interface LaunchData {
  launches: Launch[]
  error?: string
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

const MONO: React.CSSProperties = { fontFamily: 'JetBrains Mono, monospace' }

function scoreColor(score: number) {
  if (score >= 82) return 'var(--green)'
  if (score >= 72) return 'var(--amber)'
  if (score >= 60) return 'rgba(255,255,255,0.6)'
  return 'var(--muted)'
}

function fmtAge(minutes: number | null | undefined): string {
  if (minutes == null) return '—'
  if (minutes < 1)   return '<1m'
  if (minutes < 60)  return `${Math.round(minutes)}m`
  return `${(minutes / 60).toFixed(1)}h`
}

function fmtUsd(v: number | null | undefined, decimals = 0): string {
  if (v == null) return '—'
  if (v >= 1_000_000) return `$${(v / 1_000_000).toFixed(1)}M`
  if (v >= 1_000)     return `$${(v / 1_000).toFixed(0)}K`
  return `$${v.toFixed(decimals)}`
}

function fmtTime(iso: string): string {
  try {
    const d = new Date(iso)
    return d.toUTCString().slice(17, 25) + ' UTC'
  } catch {
    return iso
  }
}

function sourceBadge(source: string) {
  const map: Record<string, { label: string; color: string }> = {
    pump_fun:            { label: 'pump.fun', color: '#a855f7' },
    pump_fun_ws:         { label: 'pump.fun', color: '#a855f7' },
    dexscreener_profile: { label: 'dex',      color: '#3b82f6' },
    dexscreener:         { label: 'dex',      color: '#3b82f6' },
  }
  const cfg = map[source] ?? { label: source, color: 'var(--muted)' }
  return (
    <span style={{
      ...MONO,
      fontSize: 9,
      fontWeight: 600,
      letterSpacing: '0.08em',
      color: cfg.color,
      background: `${cfg.color}18`,
      border: `1px solid ${cfg.color}40`,
      borderRadius: 4,
      padding: '1px 5px',
      textTransform: 'uppercase',
    }}>
      {cfg.label}
    </span>
  )
}

function ChangeCell({ v }: { v: number | null | undefined }) {
  if (v == null) return <span style={{ ...MONO, fontSize: 11, color: 'var(--muted)' }}>—</span>
  const color = v >= 0 ? 'var(--green)' : 'var(--red)'
  return (
    <span style={{ ...MONO, fontSize: 11, color }}>
      {v >= 0 ? '+' : ''}{v.toFixed(1)}%
    </span>
  )
}

// ─── Main component ───────────────────────────────────────────────────────────

export function LaunchFeed() {
  const [filter, setFilter] = useState<'all' | 'alerted'>('all')
  const [minScore, setMinScore] = useState(0)

  const { data, isLoading, error, dataUpdatedAt } = useQuery<LaunchData>({
    queryKey: ['launches-recent'],
    queryFn: () => api.get('/launches/recent?limit=100').then(r => r.data),
    refetchInterval: 15_000,
    staleTime: 10_000,
  })

  const launches = (data?.launches ?? []).filter(l => {
    if (filter === 'alerted' && !l.alerted) return false
    if (l.score < minScore) return false
    return true
  })

  const alertedCount = (data?.launches ?? []).filter(l => l.alerted).length

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>

      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
        <div>
          <h1 style={{ margin: 0, fontSize: 18, fontWeight: 700, color: 'var(--text)' }}>
            ⚡ Launch Feed
          </h1>
          <p style={{ margin: '3px 0 0', fontSize: 12, color: 'var(--muted)', ...MONO }}>
            Real-time new token detection · Pump.fun WS + DexScreener poll
          </p>
        </div>

        {/* Stats pills */}
        <div style={{ marginLeft: 'auto', display: 'flex', gap: 10, alignItems: 'center' }}>
          {data && (
            <>
              <div className="card" style={{ padding: '6px 12px', display: 'flex', gap: 16, fontSize: 11 }}>
                <span style={{ ...MONO, color: 'var(--muted)' }}>TOTAL</span>
                <span style={{ ...MONO, fontWeight: 700, color: 'var(--text)' }}>{data.launches.length}</span>
              </div>
              <div className="card" style={{ padding: '6px 12px', display: 'flex', gap: 16, fontSize: 11 }}>
                <span style={{ ...MONO, color: 'var(--muted)' }}>ALERTED</span>
                <span style={{ ...MONO, fontWeight: 700, color: 'var(--green)' }}>{alertedCount}</span>
              </div>
              <div className="card" style={{ padding: '6px 12px', display: 'flex', gap: 8, fontSize: 11 }}>
                <span style={{ ...MONO, color: 'var(--muted)' }}>UPDATED</span>
                <span style={{ ...MONO, color: 'var(--dim)' }}>
                  {dataUpdatedAt ? new Date(dataUpdatedAt).toUTCString().slice(17, 25) : '—'}
                </span>
              </div>
            </>
          )}
        </div>
      </div>

      {/* Filters */}
      <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
        {(['all', 'alerted'] as const).map(f => (
          <button
            key={f}
            onClick={() => setFilter(f)}
            style={{
              padding: '5px 14px',
              borderRadius: 7,
              border: '1px solid',
              borderColor: filter === f ? 'rgba(0,212,138,0.4)' : 'rgba(255,255,255,0.1)',
              background: filter === f ? 'rgba(0,212,138,0.12)' : 'transparent',
              color: filter === f ? 'var(--green)' : 'var(--muted)',
              fontSize: 11,
              fontWeight: filter === f ? 700 : 400,
              cursor: 'pointer',
              ...MONO,
            }}
          >
            {f === 'alerted' ? `⚡ Alerted (${alertedCount})` : 'All Detections'}
          </button>
        ))}
        <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ ...MONO, fontSize: 10, color: 'var(--muted)' }}>MIN SCORE</span>
          <input
            type="number"
            value={minScore}
            min={0} max={100}
            onChange={e => setMinScore(Number(e.target.value))}
            style={{
              width: 54,
              background: 'rgba(255,255,255,0.04)',
              border: '1px solid rgba(255,255,255,0.12)',
              borderRadius: 6,
              padding: '4px 8px',
              color: 'var(--text)',
              fontSize: 11,
              ...MONO,
            }}
          />
        </div>
      </div>

      {/* State: listener disabled hint */}
      {data?.error && (
        <div className="card" style={{ padding: '14px 18px', color: 'var(--amber)', fontSize: 12, ...MONO }}>
          ⚠ Launch listener not running — set <code>LAUNCH_LISTENER_ENABLED=true</code> in .env to activate real-time detection.
        </div>
      )}

      {/* State: loading */}
      {isLoading && (
        <div className="card" style={{ padding: 32, textAlign: 'center', color: 'var(--muted)', fontSize: 12 }}>
          Loading launch feed...
        </div>
      )}

      {/* State: empty */}
      {!isLoading && !error && launches.length === 0 && (
        <div className="card" style={{ padding: 40, textAlign: 'center' }}>
          <div style={{ fontSize: 28, marginBottom: 10 }}>⚡</div>
          <div style={{ ...MONO, color: 'var(--muted)', fontSize: 12 }}>
            {filter === 'alerted'
              ? 'No alerted launches yet — lower MIN_SCORE or set LAUNCH_LISTENER_ENABLED=true'
              : 'No launches detected yet — enable LAUNCH_LISTENER_ENABLED in .env'}
          </div>
        </div>
      )}

      {/* Launch table */}
      {launches.length > 0 && (
        <div className="card" style={{ overflow: 'hidden' }}>
          {/* Table header */}
          <div style={{
            display: 'grid',
            gridTemplateColumns: '110px 56px 70px 70px 70px 80px 60px 72px 90px 60px',
            gap: '0 12px',
            padding: '8px 16px',
            borderBottom: '1px solid rgba(255,255,255,0.06)',
            fontSize: 9,
            fontWeight: 600,
            letterSpacing: '0.12em',
            color: 'rgba(255,255,255,0.22)',
            ...MONO,
          }}>
            <span>SYMBOL</span>
            <span style={{ textAlign: 'right' }}>SCORE</span>
            <span style={{ textAlign: 'right' }}>LIQ</span>
            <span style={{ textAlign: 'right' }}>VOL 5M</span>
            <span style={{ textAlign: 'right' }}>VOL 1H</span>
            <span style={{ textAlign: 'right' }}>MCAP</span>
            <span style={{ textAlign: 'right' }}>1H CHG</span>
            <span style={{ textAlign: 'center' }}>AGE</span>
            <span style={{ textAlign: 'center' }}>SOURCE</span>
            <span style={{ textAlign: 'right' }}>TIME</span>
          </div>

          {/* Table rows */}
          {launches.map((l, i) => (
            <div
              key={`${l.mint}-${i}`}
              style={{
                display: 'grid',
                gridTemplateColumns: '110px 56px 70px 70px 70px 80px 60px 72px 90px 60px',
                gap: '0 12px',
                padding: '8px 16px',
                borderBottom: i < launches.length - 1 ? '1px solid rgba(255,255,255,0.04)' : 'none',
                background: l.alerted ? 'rgba(0,212,138,0.04)' : 'transparent',
                alignItems: 'center',
                transition: 'background 0.15s',
              }}
              onMouseEnter={e => (e.currentTarget.style.background = l.alerted ? 'rgba(0,212,138,0.08)' : 'rgba(255,255,255,0.03)')}
              onMouseLeave={e => (e.currentTarget.style.background = l.alerted ? 'rgba(0,212,138,0.04)' : 'transparent')}
            >
              {/* Symbol */}
              <div style={{ display: 'flex', alignItems: 'center', gap: 6, minWidth: 0 }}>
                {l.alerted && (
                  <span title="Alert sent" style={{ fontSize: 9, color: 'var(--green)', flexShrink: 0 }}>⚡</span>
                )}
                <div style={{ minWidth: 0 }}>
                  <a
                    href={`https://dexscreener.com/solana/${l.mint}`}
                    target="_blank"
                    rel="noopener noreferrer"
                    style={{ ...MONO, fontSize: 12, fontWeight: 700, color: 'var(--text)', textDecoration: 'none' }}
                    title={l.mint}
                  >
                    ${l.symbol}
                  </a>
                  <div style={{ ...MONO, fontSize: 8, color: 'var(--muted)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {l.mint.slice(0, 14)}…
                  </div>
                </div>
              </div>

              {/* Score */}
              <span style={{ ...MONO, fontSize: 13, fontWeight: 700, color: scoreColor(l.score), textAlign: 'right' }}>
                {l.score.toFixed(0)}
              </span>

              {/* Liquidity */}
              <span style={{ ...MONO, fontSize: 11, color: 'var(--text)', textAlign: 'right' }}>
                {fmtUsd(l.liquidity)}
              </span>

              {/* Vol 5m */}
              <span style={{ ...MONO, fontSize: 11, color: 'var(--dim)', textAlign: 'right' }}>
                {fmtUsd(l.volume_5m)}
              </span>

              {/* Vol 1h */}
              <span style={{ ...MONO, fontSize: 11, color: 'var(--dim)', textAlign: 'right' }}>
                {fmtUsd(l.volume_1h)}
              </span>

              {/* MCap */}
              <span style={{ ...MONO, fontSize: 11, color: 'var(--dim)', textAlign: 'right' }}>
                {fmtUsd(l.market_cap)}
              </span>

              {/* 1h change */}
              <div style={{ textAlign: 'right' }}>
                <ChangeCell v={l.change_1h} />
              </div>

              {/* Age */}
              <span style={{ ...MONO, fontSize: 11, color: 'var(--muted)', textAlign: 'center' }}>
                {fmtAge(l.age_minutes)}
              </span>

              {/* Source */}
              <div style={{ textAlign: 'center' }}>
                {sourceBadge(l.source)}
              </div>

              {/* Time */}
              <span style={{ ...MONO, fontSize: 9, color: 'var(--muted)', textAlign: 'right' }}>
                {fmtTime(l._ts)}
              </span>
            </div>
          ))}
        </div>
      )}

      {/* Info footer */}
      <div style={{ ...MONO, fontSize: 10, color: 'rgba(255,255,255,0.15)', textAlign: 'center' }}>
        Auto-refreshes every 15s · Score threshold: {minScore > 0 ? minScore : 'LAUNCH_MIN_SCORE'} · Showing {launches.length} of {data?.launches.length ?? 0} detections
      </div>
    </div>
  )
}
