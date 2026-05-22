import { useState } from 'react'
import { api } from '../api'
import { useQuery } from '@tanstack/react-query'
import type { JupiterPosition } from './WalletSection'

const MONO: React.CSSProperties = { fontFamily: 'JetBrains Mono, monospace' }

interface TierPosition {
  id: number; symbol: string; side: string; entry: number; price: number
  raw_pnl: number; lev_pnl: number; pnl_usd: number; collateral: number
  jup_key: string; opened: string
  stacked_count?: number
  liq_price?: number
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

// ── PerpRiskPanel types ───────────────────────────────────────────────────────

interface LivePosition {
  symbol: string
  side: 'LONG' | 'SHORT'
  leverage: number
  mark_price: number
  liq_price: number
  liq_distance_pct: number
  pnl_usd: number
  pnl_pct: number
  collateral_usd: number
  action: 'HOLD' | 'WATCH' | 'REDUCE' | 'CLOSE'
  risk_tier: 'LOW' | 'MED' | 'HIGH' | 'CRITICAL'
  size_usd: number
}

// ── ReconciliationPanel types ─────────────────────────────────────────────────

interface ReconPosition {
  symbol: string
  tier_label: string | null
  db_collateral_usd: number
  live_collateral_usd: number
  drift_collateral_usd: number
  status: string
  notes: string | null
}

interface ReconciliationData {
  positions: ReconPosition[]
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
  '3x':  { caution: 130,   stop: 175   },
  '5x':  { caution: 85000, stop: 90000 },
  '10x': { caution: 2800,  stop: 3000  },
}

function fmt(n: number | null, dec = 2) { if (n == null) return '—'; return n >= 0 ? `+${n.toFixed(dec)}` : n.toFixed(dec) }
function PnlSpan({ v, children }: { v: number; children: React.ReactNode }) {
  return <span style={{ color: v > 0 ? '#00d48a' : v < 0 ? '#ef4444' : '#5a7a9a' }}>{children}</span>
}

// ── PerpRiskPanel ─────────────────────────────────────────────────────────────

function PerpRiskPanel() {
  const liveQ = useQuery<{ positions: LivePosition[] }>({
    queryKey: ['tiers-positions-live'],
    queryFn:  () => api.get('/tiers/positions-live').then(r => r.data),
    refetchInterval: 15_000,
  })

  const positions = liveQ.data?.positions ?? []
  if (liveQ.isLoading) return (
    <div style={{ padding: '8px 0', fontSize: 9, color: 'var(--dim)', ...MONO }}>loading live risk…</div>
  )
  if (positions.length === 0) return null

  const ACTION_COLOR: Record<string, string> = {
    HOLD: '#4d5a6e', WATCH: '#f59e0b', REDUCE: '#f97316', CLOSE: '#ef4444',
  }
  const RISK_COLOR: Record<string, string> = {
    LOW: '#4d5a6e', MED: '#f59e0b', HIGH: '#f97316', CRITICAL: '#ef4444',
  }

  const sorted = [...positions].sort((a, b) => {
    const order = { CRITICAL: 0, HIGH: 1, MED: 2, LOW: 3 }
    return order[a.risk_tier] - order[b.risk_tier]
  })

  return (
    <div style={{
      background: 'rgba(255,255,255,0.02)',
      border: '1px solid rgba(255,255,255,0.07)',
      borderRadius: 8,
      padding: '10px 12px',
      marginBottom: 14,
      display: 'flex', flexDirection: 'column', gap: 8,
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <span style={{ color: 'var(--text2)', ...MONO, fontWeight: 700, fontSize: 10, letterSpacing: '0.12em' }}>
          LIVE RISK
        </span>
        <span style={{ color: '#3d5068', fontSize: 9, ...MONO }}>{positions.length} positions · 15s</span>
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
        {sorted.map((p, i) => {
          const actionColor = ACTION_COLOR[p.action]
          const riskColor   = RISK_COLOR[p.risk_tier]
          return (
            <div key={i} style={{
              display: 'flex', alignItems: 'center', gap: 10,
              background: 'rgba(0,0,0,0.2)',
              border: `1px solid ${riskColor}18`,
              borderLeft: `2px solid ${riskColor}`,
              borderRadius: '0 4px 4px 0',
              padding: '5px 8px',
              fontSize: 9, ...MONO,
            }}>
              {/* Symbol + side */}
              <div style={{ width: 60, flexShrink: 0 }}>
                <span style={{ color: '#c0cfe0', fontWeight: 700 }}>{p.symbol}</span>
                <span style={{ color: p.side === 'LONG' ? '#00d48a' : '#f59e0b', marginLeft: 4, fontSize: 8 }}>{p.side}</span>
              </div>
              {/* Leverage */}
              <span style={{ color: '#4d5a6e', width: 24, flexShrink: 0 }}>{p.leverage != null ? p.leverage.toFixed(0) : '—'}×</span>
              {/* PnL */}
              <PnlSpan v={p.pnl_usd}>
                <span style={{ width: 70, display: 'inline-block' }}>
                  {p.pnl_usd != null ? `${p.pnl_usd >= 0 ? '+' : ''}$${p.pnl_usd.toFixed(2)}` : '—'}
                  <span style={{ opacity: 0.6, marginLeft: 3, fontSize: 8 }}>({fmt(p.pnl_pct, 1)}%)</span>
                </span>
              </PnlSpan>
              {/* Liq distance */}
              <div style={{ flex: 1, display: 'flex', gap: 4, alignItems: 'center' }}>
                <span style={{ color: '#3d5068' }}>liq</span>
                <span style={{ color: p.liq_distance_pct < 10 ? '#ef4444' : p.liq_distance_pct < 15 ? '#f59e0b' : '#4d5a6e' }}>
                  {p.liq_distance_pct != null ? `${p.liq_distance_pct.toFixed(1)}%` : '—'}
                </span>
                <span style={{ color: '#2d4060', fontSize: 8 }}>
                  {p.liq_price != null ? `$${p.liq_price >= 1000 ? (p.liq_price / 1000).toFixed(1) + 'k' : p.liq_price.toFixed(0)}` : '—'}
                </span>
              </div>
              {/* Action badge */}
              <span style={{
                color: actionColor, fontWeight: 700, fontSize: 8,
                background: `${actionColor}15`, border: `1px solid ${actionColor}30`,
                borderRadius: 3, padding: '1px 5px', flexShrink: 0,
              }}>
                {p.action}
              </span>
            </div>
          )
        })}
      </div>
    </div>
  )
}

// ── ReconciliationPanel ───────────────────────────────────────────────────────

function ReconciliationPanel() {
  const [expanded, setExpanded] = useState(false)

  const reconQ = useQuery<ReconciliationData>({
    queryKey: ['tiers-reconciliation'],
    queryFn:  () => api.get('/tiers/reconciliation').then(r => r.data),
    refetchInterval: 60_000,
  })

  const d = reconQ.data
  if (!d) return null

  const positions = d.positions ?? []
  const driftCount = positions.filter(p => p.status !== 'OK').length
  const statusColor = driftCount === 0 ? '#4d5a6e' : '#ef4444'

  return (
    <div style={{
      background: 'rgba(0,0,0,0.15)',
      border: `1px solid ${statusColor}20`,
      borderRadius: 6,
      marginBottom: 14,
      overflow: 'hidden',
    }}>
      {/* Strip header */}
      <button
        onClick={() => setExpanded(e => !e)}
        style={{
          width: '100%', display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          padding: '5px 10px',
          background: 'none', border: 'none', cursor: 'pointer',
          fontSize: 8, ...MONO,
        }}
      >
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <span style={{ color: '#3d5068' }}>RECONCILIATION</span>
          <span style={{ color: statusColor, fontWeight: 700 }}>{driftCount === 0 ? 'OK' : 'DRIFT'}</span>
          {driftCount > 0 && (
            <span style={{ color: '#ef4444' }}>{driftCount} drift{driftCount > 1 ? 's' : ''}</span>
          )}
          <span style={{ color: '#2d4060' }}>{positions.length} positions</span>
        </div>
        <span style={{ color: '#2d4060' }}>{expanded ? '▲' : '▼'}</span>
      </button>

      {/* Expanded positions */}
      {expanded && positions.length > 0 && (
        <div style={{ padding: '4px 10px 8px', display: 'flex', flexDirection: 'column', gap: 3 }}>
          {positions.map((pos, i) => (
            <div key={i} style={{ display: 'flex', gap: 8, fontSize: 8, ...MONO, color: '#5a7a9a' }}>
              <span style={{ color: '#c0cfe0', width: 40 }}>{pos.symbol}</span>
              <span style={{ color: pos.status === 'OK' ? '#4d5a6e' : '#ef4444' }}>{pos.status}</span>
              <span style={{ color: pos.drift_collateral_usd < 0 ? '#ef4444' : '#4d5a6e' }}>
                drift {pos.drift_collateral_usd != null ? `$${pos.drift_collateral_usd.toFixed(2)}` : '—'}
              </span>
              {pos.notes && <span style={{ color: '#2d4060' }}>{pos.notes}</span>}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ── TierCard ──────────────────────────────────────────────────────────────────

function TierCard({ label, info, onOpen, loading, jupPos }: {
  label: string; info: TierInfo; onOpen: (t: string) => void; loading: boolean
  jupPos?: JupiterPosition
}) {
  const color = TIER_COLOR[label]
  const bg    = TIER_BG[label]
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
          <span>{info.count} pos · {totalCol != null ? `$${totalCol.toFixed(0)}` : '—'} deployed</span>
          <PnlSpan v={totalPnl}>${fmt(totalPnl)} PnL</PnlSpan>
        </div>
      )}

      {/* Liq buffer */}
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
  const jupiterPnl = jupiterPositions
    ? jupiterPositions.reduce((s, p) => s + p.pnl_usd, 0)
    : (data?.total_pnl_usd ?? 0)
  const jupiterCollateral = jupiterPositions && jupiterPositions.length > 0
    ? jupiterPositions.reduce((s, p) => s + (p.collateral_usd ?? 0), 0)
    : null
  const displayCollateral = jupiterCollateral ?? data?.total_collateral ?? null

  return (
    <div>
      {/* ── PerpRiskPanel ── */}
      <PerpRiskPanel />

      {/* ── ReconciliationPanel ── */}
      <ReconciliationPanel />

      {/* Header row */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 14 }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 12 }}>
          <span style={{ color: '#c0cfe0', fontWeight: 700, fontSize: 11, letterSpacing: '0.12em', ...MONO }}>
            TIER SYSTEM
          </span>
          {data && (
            <span style={{ color: '#3d5068', fontSize: 10, ...MONO }}>
              {jupiterPositions?.length ?? ['3x','5x','10x'].reduce((s, t) => s + (data.tiers[t as '3x']?.count ?? 0), 0)} pos
              · <span style={{ color: '#5a7a9a' }}>{displayCollateral != null ? `$${displayCollateral.toFixed(0)}` : '—'}</span> deployed
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
