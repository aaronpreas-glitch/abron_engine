import { useState } from 'react'
import { api } from '../api'
import { useQuery } from '@tanstack/react-query'
import type { JupiterPosition } from './WalletSection'

const MONO: React.CSSProperties = { fontFamily: 'JetBrains Mono, monospace' }

interface TierPosition {
  id: number; symbol: string; side: string; entry: number; price: number
  raw_pnl: number; lev_pnl: number; pnl_usd: number; collateral: number
  jup_key: string; opened: string
  stacked_count?: number  // Patch 123
  liq_price?: number      // Patch 180
}
interface TierInfo {
  leverage: number; notional: number; collateral: number; tp_pct: number | null
  reenter: boolean; positions: TierPosition[]; count: number
}
interface TierStatus {
  tiers: { '3x': TierInfo; '5x': TierInfo; '10x': TierInfo }
  profit_buffer: number
  thresholds: { '3x': number; '5x': number }
  total_collateral: number
  total_pnl_usd: number
}

const TIER_COLOR: Record<string, string> = { '3x': '#4ade80', '5x': '#60a5fa', '10x': '#f59e0b' }
const TIER_BG:    Record<string, string> = {
  '3x': 'rgba(74,222,128,0.06)',
  '5x': 'rgba(96,165,250,0.06)',
  '10x': 'rgba(245,158,11,0.06)',
}
const TIER_DESC: Record<string, string> = {
  '3x': 'DIAMOND HANDS',
  '5x': 'TP +20% · RE-ENTER',
  '10x': 'TP +10% · RE-ENTER',
}
const ADD_LIMITS: Record<string, { caution: number; stop: number }> = {
  '3x':  { caution: 130,   stop: 175   },  // SOL
  '5x':  { caution: 85000, stop: 90000 },  // BTC
  '10x': { caution: 2800,  stop: 3000  },  // ETH
}

function fmt(n: number, dec = 2) { return n >= 0 ? `+${n.toFixed(dec)}` : n.toFixed(dec) }
function PnlSpan({ v, children }: { v: number; children: React.ReactNode }) {
  return <span style={{ color: v > 0 ? '#00d48a' : v < 0 ? '#ef4444' : '#5a7a9a' }}>{children}</span>
}

function TierCard({ label, info, onOpen, loading, jupPos }: {
  label: string; info: TierInfo; onOpen: (t: string) => void; loading: boolean
  jupPos?: JupiterPosition
}) {
  const color = TIER_COLOR[label]
  const bg    = TIER_BG[label]
  // Use Jupiter live PnL as ground truth (blended entry vs internal tracker)
  const totalPnl = jupPos ? jupPos.pnl_usd : info.positions.reduce((s, p) => s + p.pnl_usd, 0)
  const totalCol = jupPos ? jupPos.collateral_usd : info.positions.reduce((s, p) => s + p.collateral, 0)

  return (
    <div style={{
      flex: '1 1 200px', minWidth: 0,
      background: bg,
      border: `1px solid ${color}22`,
      borderTop: `2px solid ${color}`,
      borderRadius: '0 0 8px 8px',
      padding: '14px 14px 12px',
      display: 'flex', flexDirection: 'column', gap: 10,
    }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between' }}>
        <div>
          <div style={{ color, fontWeight: 800, fontSize: 18, lineHeight: 1, ...MONO }}>{Math.round(info.leverage)}x</div>
          <div style={{ color: `${color}88`, fontSize: 8, marginTop: 3, letterSpacing: '0.1em', ...MONO }}>
            {TIER_DESC[label]}
          </div>
        </div>
        <div style={{ textAlign: 'right' }}>
          <div style={{ color: '#5a7a9a', fontSize: 9, ...MONO }}>col/pos</div>
          <div style={{ color: '#a0b4c8', fontWeight: 700, fontSize: 13, ...MONO }}>${info.collateral}</div>
        </div>
      </div>

      {/* Config pills */}
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
        <span style={{
          background: 'rgba(255,255,255,0.05)', border: '1px solid rgba(255,255,255,0.08)',
          borderRadius: 4, padding: '2px 7px', fontSize: 9, color: '#6a8aaa', ...MONO,
        }}>
          {info.leverage}× leverage
        </span>
        <span style={{
          background: 'rgba(255,255,255,0.05)', border: '1px solid rgba(255,255,255,0.08)',
          borderRadius: 4, padding: '2px 7px', fontSize: 9, color: '#6a8aaa', ...MONO,
        }}>
          ${info.notional} notional
        </span>
        {info.tp_pct && (
          <span style={{
            background: `${color}12`, border: `1px solid ${color}30`,
            borderRadius: 4, padding: '2px 7px', fontSize: 9, color, ...MONO,
          }}>
            TP {info.tp_pct}%
          </span>
        )}
      </div>

      {/* Positions */}
      <div style={{ flex: 1 }}>
        {info.count === 0 ? (
          <div style={{ color: 'var(--dim)', fontSize: 10, padding: '6px 0', ...MONO }}>no open positions</div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            {info.positions.map(pos => (
              <div key={pos.id} style={{
                display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                padding: '5px 8px',
                background: 'rgba(0,0,0,0.2)',
                borderRadius: 4,
                fontSize: 10, ...MONO,
              }}>
                <div>
                  <span style={{ color: '#4a6280' }}>#{pos.id} </span>
                  <span style={{ color: '#8a9ab0', fontWeight: 600 }}>{pos.symbol}</span>
                  <span style={{ color: '#2d4060' }}> @{pos.entry?.toFixed(2)}</span>
                  {pos.stacked_count && pos.stacked_count > 1 && (
                    <span style={{ color: '#f59e0b', fontSize: 9, marginLeft: 4 }}>
                      ×{pos.stacked_count} stacked
                    </span>
                  )}
                </div>
                <PnlSpan v={jupPos ? jupPos.pnl_usd : pos.pnl_usd}>
                  <span style={{ fontSize: 9 }}>{jupPos ? fmt(jupPos.pnl_pct) : fmt(pos.lev_pnl)}%</span>
                  <span style={{ marginLeft: 6 }}>${fmt(jupPos ? jupPos.pnl_usd : pos.pnl_usd)}</span>
                </PnlSpan>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Footer */}
      {info.count > 0 && (
        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 9, color: '#4d5a6e', ...MONO, paddingTop: 4, borderTop: '1px solid rgba(255,255,255,0.05)' }}>
          <span>{info.count} pos · ${totalCol.toFixed(0)} deployed</span>
          <PnlSpan v={totalPnl}>${fmt(totalPnl)} PnL</PnlSpan>
        </div>
      )}

      {/* Liq buffer — Patch 180: prefer jupPos live liq/mark, fall back to tier_status liq_price */}
      {info.count > 0 && (() => {
        const liqPrice  = jupPos?.liq_price  ?? info.positions[0]?.liq_price
        const markPrice = jupPos?.mark_price ?? info.positions[0]?.price
        if (!liqPrice || !markPrice) return null
        const buf = (markPrice - liqPrice) / markPrice * 100
        const bufColor = buf < 10 ? '#ef4444' : buf < 15 ? '#f59e0b' : '#4a6280'
        return (
          <div style={{ fontSize: 9, color: bufColor, ...MONO, paddingTop: 2 }}>
            liq ${liqPrice.toLocaleString('en-US', { maximumFractionDigits: 0 })} · buf {buf.toFixed(1)}%
          </div>
        )
      })()}

      {/* Add limit indicator */}
      {(() => {
        const lim = ADD_LIMITS[label]
        const price = info.positions[0]?.price ?? null
        if (!lim) return null
        const status = price === null ? 'free'
          : price >= lim.stop    ? 'stop'
          : price >= lim.caution ? 'caution'
          : 'free'
        const statusColor = status === 'stop' ? '#ef4444' : status === 'caution' ? '#f97316' : '#00d48a'
        const statusLabel = status === 'stop' ? 'HARD STOP' : status === 'caution' ? 'SLOW DOWN' : 'ADD FREELY'
        const fmtP = (n: number) => n >= 1000 ? `$${(n/1000).toFixed(0)}k` : `$${n}`
        return (
          <div style={{
            display: 'flex', justifyContent: 'space-between', alignItems: 'center',
            padding: '4px 7px',
            background: `${statusColor}08`,
            border: `1px solid ${statusColor}25`,
            borderRadius: 4, fontSize: 9, ...MONO,
          }}>
            <span style={{ color: '#3d5068' }}>
              add limit{' '}
              <span style={{ color: '#5a7a9a' }}>{fmtP(lim.caution)}</span>
              <span style={{ color: '#2d4060' }}> → </span>
              <span style={{ color: '#ef444488' }}>{fmtP(lim.stop)}</span>
            </span>
            <span style={{ color: statusColor, fontWeight: 700, letterSpacing: '0.08em' }}>{statusLabel}</span>
          </div>
        )
      })()}

      {/* Button */}
      <button onClick={() => onOpen(label)} disabled={loading} style={{
        width: '100%', padding: '6px 0',
        background: loading ? 'transparent' : `${color}10`,
        border: `1px solid ${loading ? '#1a2535' : color + '40'}`,
        borderRadius: 5, color: loading ? '#2d4060' : color,
        cursor: loading ? 'default' : 'pointer',
        fontFamily: 'JetBrains Mono, monospace', fontSize: 9,
        letterSpacing: '0.1em', fontWeight: 600,
        transition: 'all 0.15s',
      }}>
        {loading ? 'OPENING…' : `+ OPEN ${Math.round(info.leverage)}x`}
      </button>
    </div>
  )
}

// Match a tier label to its Jupiter live position by leverage
function matchJupPos(tier: string, positions: JupiterPosition[] | undefined): JupiterPosition | undefined {
  if (!positions?.length) return undefined
  if (tier === '3x')  return positions.find(p => Math.round(p.leverage) === 3)
  if (tier === '5x')  return positions.find(p => Math.round(p.leverage) === 5)
  if (tier === '10x') return positions.find(p => Math.round(p.leverage) !== 3 && Math.round(p.leverage) !== 5)
  return undefined
}

export function TierSection({ jupiterPositions }: { jupiterPositions?: JupiterPosition[] }) {
  const [openingTier, setOpeningTier] = useState<string | null>(null)

  const tierQuery = useQuery<TierStatus>({
    queryKey: ['tiers'],
    queryFn: async () => { const r = await api.get('/tiers/status'); return r.data },
    refetchInterval: 30_000,
  })

  const data = tierQuery.data

  const handleOpen = async (tier: string) => {
    setOpeningTier(tier)
    try { await api.post(`/tiers/open/${tier}`); await tierQuery.refetch() }
    catch (e) { console.error('open tier failed', e) }
    finally { setOpeningTier(null) }
  }

  const handleOpenAll = async () => {
    setOpeningTier('all')
    try { await api.post('/tiers/open-all'); await tierQuery.refetch() }
    catch (e) { console.error('open all failed', e) }
    finally { setOpeningTier(null) }
  }

  const buffer = data?.profit_buffer ?? 0
  const thresh5x = data?.thresholds?.['5x'] ?? 20
  const thresh3x = data?.thresholds?.['3x'] ?? 50
  const nextThresh = buffer >= thresh5x ? thresh3x : thresh5x
  const nextTier = buffer >= thresh5x ? '3x' : '5x'
  const bufferPct = Math.min(100, (buffer / nextThresh) * 100)
  // Use Jupiter's pnlAfterFeesUsd as ground truth — matches WalletSection PERP PnL card
  const jupiterPnl = jupiterPositions
    ? jupiterPositions.reduce((s, p) => s + p.pnl_usd, 0)
    : (data?.total_pnl_usd ?? 0)

  return (
    <div>
      {/* Header row */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 14 }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 12 }}>
          <span style={{ color: '#c0cfe0', fontWeight: 700, fontSize: 11, letterSpacing: '0.12em', ...MONO }}>
            TIER SYSTEM
          </span>
          {data && (
            <span style={{ color: '#3d5068', fontSize: 10, ...MONO }}>
              {jupiterPositions?.length ?? ['3x','5x','10x'].reduce((s, t) => s + (data.tiers[t as '3x']?.count ?? 0), 0)} pos
              · <span style={{ color: '#5a7a9a' }}>${data.total_collateral.toFixed(0)}</span> deployed
              · <PnlSpan v={jupiterPnl}>${fmt(jupiterPnl)} PnL</PnlSpan>
            </span>
          )}
        </div>
        <button onClick={handleOpenAll} disabled={openingTier !== null} className="btn" style={{
          color: openingTier !== null ? 'var(--dim)' : 'var(--green)',
          borderColor: openingTier !== null ? 'var(--border)' : 'rgba(0,212,138,0.35)',
          cursor: openingTier !== null ? 'default' : 'pointer',
          fontSize: 9, letterSpacing: '0.1em',
        }}>
          {openingTier === 'all' ? 'OPENING…' : '⊕ OPEN ALL'}
        </button>
      </div>

      {/* Profit buffer */}
      {buffer > 0 && (
        <div style={{ marginBottom: 14, padding: '10px 12px', background: 'rgba(0,212,138,0.04)', border: '1px solid rgba(0,212,138,0.1)', borderRadius: 6 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 9, marginBottom: 6, ...MONO }}>
            <span style={{ color: '#4d5a6e' }}>PROFIT BUFFER  <span style={{ color: '#00d48a', fontWeight: 700 }}>${buffer.toFixed(2)}</span></span>
            <span style={{ color: '#3d5068' }}>→ next {nextTier} @ ${nextThresh}</span>
          </div>
          <div style={{ height: 3, background: 'rgba(255,255,255,0.06)', borderRadius: 2, overflow: 'hidden' }}>
            <div style={{ height: '100%', width: `${bufferPct}%`, background: 'linear-gradient(90deg, #00d48a, #00f0a0)', borderRadius: 2, transition: 'width 0.5s ease' }} />
          </div>
        </div>
      )}

      {/* Cards */}
      {tierQuery.isLoading ? (
        <div style={{ color: 'var(--dim)', fontSize: 10, textAlign: 'center', padding: '24px 0', ...MONO }}>loading…</div>
      ) : data ? (
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
          {(['3x', '5x', '10x'] as const).map(tier => (
            <TierCard key={tier} label={tier}
              info={data.tiers[tier] ?? { leverage: 0, notional: 0, collateral: 0, tp_pct: null, reenter: false, positions: [], count: 0 }}
              onOpen={handleOpen} loading={openingTier === tier}
              jupPos={matchJupPos(tier, jupiterPositions)}
            />
          ))}
        </div>
      ) : (
        <div style={{ color: '#ef4444', fontSize: 10, ...MONO }}>tier API unavailable</div>
      )}
    </div>
  )
}
