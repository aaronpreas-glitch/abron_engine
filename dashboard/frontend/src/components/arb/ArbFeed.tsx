/**
 * ArbFeed — Cross-DEX price spread opportunities on new launches.
 *
 * Reads from /api/arb/opportunities (backed by arb_feed.jsonl).
 * Shows symbol, spread %, DEX names, prices, score, timestamp.
 * When ARB_ENABLED=false the component shows a disabled state.
 */
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../../api'

// ─── Types ────────────────────────────────────────────────────────────────────

interface ArbOpportunity {
  ts_utc: string
  mint: string
  symbol: string
  score: number
  source: string
  entry_price: number
  spread_pct: number
  best_dex: string
  worst_dex: string
  best_price: number
  worst_price: number
  jupiter_price: number | null
  dex_prices: Record<string, number>
  alerted: boolean
  check_n: number
  elapsed_s: number
}

interface ArbData {
  opportunities: ArbOpportunity[]
  total: number
  arb_enabled: boolean
  min_spread_pct: number
  error?: string
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

const MONO: React.CSSProperties = { fontFamily: 'JetBrains Mono, monospace' }

function spreadColor(pct: number) {
  if (pct >= 8) return 'var(--green)'
  if (pct >= 4) return 'var(--amber)'
  return 'rgba(255,255,255,0.55)'
}

function fmtPrice(v: number | null | undefined): string {
  if (v == null || v === 0) return '—'
  if (v < 0.00001) return v.toExponential(3)
  if (v < 0.01)    return v.toFixed(8)
  if (v < 1)       return v.toFixed(6)
  return v.toFixed(4)
}

function fmtTime(iso: string): string {
  try {
    return new Date(iso).toUTCString().slice(17, 25) + ' UTC'
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

function dexBadge(dex: string, isGood: boolean) {
  const color = isGood ? 'var(--green)' : 'var(--red)'
  return (
    <span style={{
      ...MONO,
      fontSize: 9,
      fontWeight: 600,
      color,
      background: `${color}18`,
      border: `1px solid ${color}30`,
      borderRadius: 4,
      padding: '1px 5px',
      letterSpacing: '0.05em',
    }}>
      {dex}
    </span>
  )
}

// ─── Main component ───────────────────────────────────────────────────────────

export function ArbFeed() {
  const [minSpread, setMinSpread] = useState(0)
  const [showAll, setShowAll] = useState(false)

  const { data, isLoading, dataUpdatedAt } = useQuery<ArbData>({
    queryKey: ['arb-opportunities'],
    queryFn: () => api.get('/arb/opportunities?limit=200').then(r => r.data),
    refetchInterval: 20_000,
    staleTime: 15_000,
  })

  const opps = (data?.opportunities ?? []).filter(o => (o.spread_pct ?? 0) >= minSpread)
  const alertedCount = (data?.opportunities ?? []).filter(o => o.alerted).length
  const maxSpread = opps.length > 0
    ? Math.max(...opps.map(o => o.spread_pct ?? 0))
    : 0

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>

      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
        <div>
          <h1 style={{ margin: 0, fontSize: 18, fontWeight: 700, color: 'var(--text)' }}>
            🔀 Arb Feed
          </h1>
          <p style={{ margin: '3px 0 0', fontSize: 12, color: 'var(--muted)', ...MONO }}>
            Cross-DEX price spread detection on new launches
          </p>
        </div>

        {/* Stats pills */}
        <div style={{ marginLeft: 'auto', display: 'flex', gap: 10, alignItems: 'center' }}>
          {data && (
            <>
              <div className="card" style={{ padding: '6px 12px', display: 'flex', gap: 16, fontSize: 11 }}>
                <span style={{ ...MONO, color: 'var(--muted)' }}>DETECTED</span>
                <span style={{ ...MONO, fontWeight: 700, color: 'var(--text)' }}>{data.opportunities.length}</span>
              </div>
              <div className="card" style={{ padding: '6px 12px', display: 'flex', gap: 16, fontSize: 11 }}>
                <span style={{ ...MONO, color: 'var(--muted)' }}>ALERTED</span>
                <span style={{ ...MONO, fontWeight: 700, color: 'var(--green)' }}>{alertedCount}</span>
              </div>
              {maxSpread > 0 && (
                <div className="card" style={{ padding: '6px 12px', display: 'flex', gap: 16, fontSize: 11 }}>
                  <span style={{ ...MONO, color: 'var(--muted)' }}>MAX SPREAD</span>
                  <span style={{ ...MONO, fontWeight: 700, color: spreadColor(maxSpread) }}>
                    {maxSpread.toFixed(1)}%
                  </span>
                </div>
              )}
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

      {/* Disabled state */}
      {data && !data.arb_enabled && (
        <div className="card" style={{ padding: '14px 18px', color: 'var(--amber)', fontSize: 12, ...MONO }}>
          ⚠ Arb monitor not running — set <code>ARB_ENABLED=true</code> in .env to activate
          cross-DEX spread detection. Min alert spread: <code>{data.min_spread_pct}%</code>
        </div>
      )}

      {/* Error state */}
      {data?.error && (
        <div className="card" style={{ padding: '14px 18px', color: 'var(--red)', fontSize: 12, ...MONO }}>
          ⚠ {data.error}
        </div>
      )}

      {/* Filters */}
      <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
        <button
          onClick={() => setShowAll(false)}
          style={{
            padding: '5px 14px',
            borderRadius: 7,
            border: '1px solid',
            borderColor: !showAll ? 'rgba(0,212,138,0.4)' : 'rgba(255,255,255,0.1)',
            background: !showAll ? 'rgba(0,212,138,0.12)' : 'transparent',
            color: !showAll ? 'var(--green)' : 'var(--muted)',
            fontSize: 11,
            fontWeight: !showAll ? 700 : 400,
            cursor: 'pointer',
            ...MONO,
          }}
        >
          {`⚡ Alerted (${alertedCount})`}
        </button>
        <button
          onClick={() => setShowAll(true)}
          style={{
            padding: '5px 14px',
            borderRadius: 7,
            border: '1px solid',
            borderColor: showAll ? 'rgba(0,212,138,0.4)' : 'rgba(255,255,255,0.1)',
            background: showAll ? 'rgba(0,212,138,0.12)' : 'transparent',
            color: showAll ? 'var(--green)' : 'var(--muted)',
            fontSize: 11,
            fontWeight: showAll ? 700 : 400,
            cursor: 'pointer',
            ...MONO,
          }}
        >
          All Spreads
        </button>
        <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ ...MONO, fontSize: 10, color: 'var(--muted)' }}>MIN SPREAD %</span>
          <input
            type="number"
            value={minSpread}
            min={0} max={50} step={0.5}
            onChange={e => setMinSpread(Number(e.target.value))}
            style={{
              width: 60,
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

      {/* Loading */}
      {isLoading && (
        <div className="card" style={{ padding: 32, textAlign: 'center', color: 'var(--muted)', fontSize: 12 }}>
          Loading arb feed...
        </div>
      )}

      {/* Empty */}
      {!isLoading && opps.filter(o => showAll || o.alerted).length === 0 && (
        <div className="card" style={{ padding: 40, textAlign: 'center' }}>
          <div style={{ fontSize: 28, marginBottom: 10 }}>🔀</div>
          <div style={{ ...MONO, color: 'var(--muted)', fontSize: 12 }}>
            {data?.arb_enabled
              ? 'No arb opportunities logged yet — waiting for high-score launches...'
              : 'Enable ARB_ENABLED=true in .env to start monitoring cross-DEX spreads'}
          </div>
        </div>
      )}

      {/* Table */}
      {opps.filter(o => showAll || o.alerted).length > 0 && (
        <div className="card" style={{ overflow: 'hidden' }}>
          {/* Header */}
          <div style={{
            display: 'grid',
            gridTemplateColumns: '110px 64px 80px 100px 100px 70px 90px 60px',
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
            <span style={{ textAlign: 'right' }}>SPREAD</span>
            <span>BUY ON</span>
            <span>SELL ON</span>
            <span>SOURCE</span>
            <span style={{ textAlign: 'right' }}>TIME</span>
            <span style={{ textAlign: 'right' }}>CHECK</span>
          </div>

          {/* Rows */}
          {opps
            .filter(o => showAll || o.alerted)
            .map((o, i, arr) => (
            <div
              key={`${o.mint}-${o.ts_utc}-${i}`}
              style={{
                display: 'grid',
                gridTemplateColumns: '110px 64px 80px 100px 100px 70px 90px 60px',
                gap: '0 12px',
                padding: '8px 16px',
                borderBottom: i < arr.length - 1 ? '1px solid rgba(255,255,255,0.04)' : 'none',
                background: o.alerted ? 'rgba(0,212,138,0.04)' : 'transparent',
                alignItems: 'center',
              }}
              onMouseEnter={e => (e.currentTarget.style.background = o.alerted ? 'rgba(0,212,138,0.08)' : 'rgba(255,255,255,0.03)')}
              onMouseLeave={e => (e.currentTarget.style.background = o.alerted ? 'rgba(0,212,138,0.04)' : 'transparent')}
            >
              {/* Symbol */}
              <div style={{ display: 'flex', alignItems: 'center', gap: 5, minWidth: 0 }}>
                {o.alerted && <span style={{ fontSize: 9, color: 'var(--green)', flexShrink: 0 }}>⚡</span>}
                <div style={{ minWidth: 0 }}>
                  <a
                    href={`https://dexscreener.com/solana/${o.mint}`}
                    target="_blank"
                    rel="noopener noreferrer"
                    style={{ ...MONO, fontSize: 12, fontWeight: 700, color: 'var(--text)', textDecoration: 'none' }}
                    title={o.mint}
                  >
                    ${o.symbol}
                  </a>
                  <div style={{ ...MONO, fontSize: 8, color: 'var(--muted)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {o.mint.slice(0, 14)}…
                  </div>
                </div>
              </div>

              {/* Score */}
              <span style={{ ...MONO, fontSize: 13, fontWeight: 700, color: 'rgba(255,255,255,0.7)', textAlign: 'right' }}>
                {o.score?.toFixed(0) ?? '—'}
              </span>

              {/* Spread */}
              <span style={{ ...MONO, fontSize: 14, fontWeight: 800, color: spreadColor(o.spread_pct), textAlign: 'right' }}>
                {o.spread_pct?.toFixed(1)}%
              </span>

              {/* Buy on (best/cheapest DEX) */}
              <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                {o.best_dex ? dexBadge(o.best_dex, true) : <span style={{ ...MONO, fontSize: 9, color: 'var(--muted)' }}>—</span>}
                <span style={{ ...MONO, fontSize: 9, color: 'var(--dim)' }}>
                  {fmtPrice(o.best_price)}
                </span>
              </div>

              {/* Sell on (worst/most expensive DEX) */}
              <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                {o.worst_dex ? dexBadge(o.worst_dex, false) : <span style={{ ...MONO, fontSize: 9, color: 'var(--muted)' }}>—</span>}
                <span style={{ ...MONO, fontSize: 9, color: 'var(--dim)' }}>
                  {fmtPrice(o.worst_price)}
                </span>
              </div>

              {/* Source */}
              <div>{sourceBadge(o.source)}</div>

              {/* Time */}
              <span style={{ ...MONO, fontSize: 9, color: 'var(--muted)', textAlign: 'right' }}>
                {fmtTime(o.ts_utc)}
              </span>

              {/* Check # */}
              <span style={{ ...MONO, fontSize: 9, color: 'var(--muted)', textAlign: 'right' }}>
                #{o.check_n}
              </span>
            </div>
          ))}
        </div>
      )}

      {/* Info footer */}
      <div style={{ ...MONO, fontSize: 10, color: 'rgba(255,255,255,0.15)', textAlign: 'center' }}>
        Auto-refreshes every 20s · Min alert spread: {data?.min_spread_pct ?? 4}% ·
        Showing {opps.filter(o => showAll || o.alerted).length} of {data?.opportunities.length ?? 0} spread checks
      </div>
    </div>
  )
}
