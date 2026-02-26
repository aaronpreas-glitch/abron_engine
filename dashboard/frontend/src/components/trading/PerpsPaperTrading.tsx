/**
 * PerpsPaperTrading — Simulated perpetuals trading on SOL/BTC/ETH.
 * Auto-fires on BULL/BEAR regime signals. No real money, no Jupiter API calls.
 * Learning loop: every closed position feeds perp_outcomes → future auto-tune.
 */
import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip,
  ReferenceLine, ResponsiveContainer,
} from 'recharts'
import { api } from '../../api'

// ── Types ──────────────────────────────────────────────────────────────────────

interface PerpPosition {
  id: number
  symbol: string
  side: 'LONG' | 'SHORT'
  entry_price: number
  stop_price: number
  tp1_price: number | null
  tp2_price: number | null
  size_usd: number
  leverage: number
  collateral_usd: number | null
  regime_label: string | null
  opened_ts_utc: string
  status: string
  dry_run: number
  notes: string | null
}

interface PerpStatus {
  enabled: boolean
  dry_run: boolean
  max_positions: number
  size_usd: number
  default_leverage: number
  open_positions: number
  positions: PerpPosition[]
  total_closed: number
  win_rate: number | null
  avg_pnl_pct: number | null
}

interface PerpEquityPoint {
  trade_n: number
  ts: string
  symbol: string
  side: string
  gross_ret: number
  net_ret: number
  equity_pct: number
  drawdown_pct: number
}

// ── Helpers ───────────────────────────────────────────────────────────────────

const MONO: React.CSSProperties = { fontFamily: 'JetBrains Mono, monospace' }

function fmtPrice(v: number | null) {
  if (v == null) return '—'
  if (v < 1) return `$${v.toFixed(6)}`
  if (v < 100) return `$${v.toFixed(4)}`
  return `$${v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

function timeAgo(ts: string) {
  const d = Date.now() - new Date(ts.endsWith('Z') ? ts : ts + 'Z').getTime()
  const m = Math.floor(d / 60000)
  const h = Math.floor(m / 60)
  if (m < 60) return `${m}m ago`
  if (h < 24) return `${h}h ago`
  return `${Math.floor(h / 24)}d ago`
}

// ── Perp Position Card ────────────────────────────────────────────────────────

function PerpCard({
  pos, onForceClose,
}: {
  pos: PerpPosition
  onForceClose: (id: number) => void
}) {
  const isLong  = pos.side === 'LONG'
  const sideColor = isLong ? 'var(--green)' : '#e879f9'
  const sideBg    = isLong ? 'rgba(0,212,138,0.08)' : 'rgba(232,121,249,0.08)'
  const sideBdr   = isLong ? 'rgba(0,212,138,0.2)' : 'rgba(232,121,249,0.2)'

  return (
    <div style={{
      background: 'var(--surface2)', border: '1px solid var(--border)',
      borderRadius: 8, padding: '14px 16px', position: 'relative',
    }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
        <span style={{ fontWeight: 800, fontSize: 15, color: 'var(--text)', ...MONO }}>
          {pos.symbol}
        </span>
        <span style={{
          fontSize: 9, padding: '2px 8px', borderRadius: 3, fontWeight: 700, ...MONO,
          background: sideBg, color: sideColor, border: `1px solid ${sideBdr}`,
        }}>
          {pos.side}
        </span>
        <span style={{
          fontSize: 9, padding: '2px 6px', borderRadius: 3, fontWeight: 600, ...MONO,
          background: 'rgba(240,165,0,0.1)', color: 'var(--amber)',
          border: '1px solid rgba(240,165,0,0.2)',
        }}>
          {pos.leverage}×
        </span>
        <span style={{
          fontSize: 9, padding: '2px 6px', borderRadius: 3, fontWeight: 600, ...MONO,
          background: 'rgba(100,100,100,0.15)', color: 'var(--muted)',
          border: '1px solid var(--border)',
        }}>
          PAPER
        </span>
        <span style={{ marginLeft: 'auto', fontSize: 10, color: 'var(--dim)', ...MONO }}>
          {timeAgo(pos.opened_ts_utc)}
        </span>
      </div>

      {/* Price levels */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '4px 12px', fontSize: 11, marginBottom: 12 }}>
        <div><span style={{ color: 'var(--muted)' }}>Entry  </span><span style={{ ...MONO }}>{fmtPrice(pos.entry_price)}</span></div>
        <div><span style={{ color: 'var(--muted)' }}>Stop   </span><span style={{ ...MONO, color: 'var(--red)' }}>{fmtPrice(pos.stop_price)}</span></div>
        <div><span style={{ color: 'var(--muted)' }}>TP1    </span><span style={{ ...MONO, color: sideColor }}>{fmtPrice(pos.tp1_price)}</span></div>
        <div><span style={{ color: 'var(--muted)' }}>TP2    </span><span style={{ ...MONO, color: sideColor }}>{fmtPrice(pos.tp2_price)}</span></div>
      </div>

      {/* Size + collateral */}
      <div style={{
        display: 'flex', gap: 12, fontSize: 10, color: 'var(--muted)',
        borderTop: '1px solid var(--border)', paddingTop: 8, marginBottom: 10, ...MONO,
      }}>
        <span>Size: <b style={{ color: 'var(--text)' }}>${pos.size_usd}</b></span>
        {pos.collateral_usd != null && (
          <span>Collateral: <b style={{ color: 'var(--text)' }}>${pos.collateral_usd.toFixed(2)}</b></span>
        )}
        {pos.regime_label && (
          <span style={{ marginLeft: 'auto', fontSize: 9 }}>{pos.regime_label}</span>
        )}
      </div>

      {/* Force close */}
      <button
        onClick={() => {
          if (window.confirm(`Force-close ${pos.side} ${pos.symbol}?`)) {
            onForceClose(pos.id)
          }
        }}
        style={{
          width: '100%', padding: '6px 0', borderRadius: 5, fontSize: 11, fontWeight: 700,
          background: 'rgba(248,81,73,0.12)', color: 'var(--red)',
          border: '1px solid rgba(248,81,73,0.3)', cursor: 'pointer',
          letterSpacing: '0.06em', ...MONO,
        }}
      >
        ⚡ FORCE CLOSE
      </button>
    </div>
  )
}

// ── Equity tooltip ─────────────────────────────────────────────────────────────

function PerpEquityTooltip({ active, payload }: { active?: boolean; payload?: { payload: PerpEquityPoint }[] }) {
  if (!active || !payload?.length) return null
  const d = payload[0].payload
  const pct   = d.equity_pct
  const color = pct >= 0 ? '#00d48a' : '#f04f4f'
  return (
    <div style={{
      background: 'rgba(14,17,27,0.97)', border: '1px solid rgba(255,255,255,0.1)',
      borderRadius: 8, padding: '10px 14px', fontSize: 11,
      fontFamily: 'JetBrains Mono, monospace', minWidth: 150,
      boxShadow: '0 8px 32px rgba(0,0,0,0.5)',
    }}>
      <div style={{ color: '#666', fontSize: 9, letterSpacing: '0.1em', marginBottom: 4 }}>
        #{d.trade_n} · {d.symbol} {d.side}
      </div>
      <div style={{ fontSize: 18, fontWeight: 800, color, lineHeight: 1.1, marginBottom: 4 }}>
        {pct >= 0 ? '+' : ''}{pct.toFixed(2)}%
      </div>
      <div style={{ color: '#888', fontSize: 10 }}>
        Net: <span style={{ color: d.net_ret >= 0 ? '#00d48a' : '#f04f4f' }}>
          {d.net_ret >= 0 ? '+' : ''}{d.net_ret.toFixed(2)}%
        </span>
      </div>
      {d.drawdown_pct < 0 && (
        <div style={{ color: '#f04f4f', fontSize: 10, marginTop: 2 }}>
          DD: {d.drawdown_pct.toFixed(2)}%
        </div>
      )}
    </div>
  )
}

// ── Main Component ────────────────────────────────────────────────────────────

export function PerpsPaperTrading() {
  const qc = useQueryClient()
  const [toggling, setToggling] = useState(false)
  const [lookback, setLookback] = useState(30)

  // Quick Open form state
  const [openSymbol, setOpenSymbol]   = useState<'SOL' | 'BTC' | 'ETH'>('SOL')
  const [openSide, setOpenSide]       = useState<'LONG' | 'SHORT'>('LONG')
  const [openSize, setOpenSize]       = useState('')
  const [openLev, setOpenLev]         = useState('2')
  const [openLoading, setOpenLoading] = useState(false)
  const [openResult, setOpenResult]   = useState<{ ok: boolean; msg: string } | null>(null)

  const { data, isLoading, error } = useQuery<PerpStatus>({
    queryKey: ['perps-status'],
    queryFn: () => api.get('/perps/status').then(r => r.data),
    refetchInterval: 30_000,
    staleTime: 15_000,
  })

  const { data: curveData } = useQuery<PerpEquityPoint[]>({
    queryKey: ['perps-equity', lookback],
    queryFn: () => api.get(`/perps/equity-curve?lookback_days=${lookback}`).then(r => r.data),
    refetchInterval: 5 * 60_000,
    staleTime: 4 * 60_000,
  })

  const forceCloseMut = useMutation({
    mutationFn: (id: number) => api.post('/perps/force-close', { position_id: id }).then(r => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['perps-status'] }),
  })

  async function togglePerps(enable: boolean) {
    setToggling(true)
    try {
      await api.post('/perps/toggle', { enabled: enable })
      qc.invalidateQueries({ queryKey: ['perps-status'] })
    } finally {
      setToggling(false)
    }
  }

  async function quickOpen() {
    setOpenLoading(true)
    setOpenResult(null)
    try {
      const body = {
        symbol: openSymbol,
        side: openSide,
        leverage: parseFloat(openLev) || 2,
        size_usd: parseFloat(openSize) || undefined,
      }
      const res = await api.post('/perps/manual-open', body).then(r => r.data)
      if (res.success) {
        setOpenResult({ ok: true, msg: `✅ Paper ${res.side} ${res.symbol} x${res.leverage} sent` })
        setOpenSize('')
        qc.invalidateQueries({ queryKey: ['perps-status'] })
      } else {
        setOpenResult({ ok: false, msg: res.error ?? 'Unknown error' })
      }
    } catch (e: unknown) {
      const msg = (e as { response?: { data?: { error?: string } } })?.response?.data?.error ?? 'Request failed'
      setOpenResult({ ok: false, msg })
    } finally {
      setOpenLoading(false)
      setTimeout(() => setOpenResult(null), 5000)
    }
  }

  const enabled   = data?.enabled ?? false
  const positions = Array.isArray(data?.positions) ? data!.positions : []
  const pts       = Array.isArray(curveData) ? curveData : []
  const hasChart  = pts.length >= 2
  const last      = pts[pts.length - 1]
  const totalPct  = last?.equity_pct ?? 0
  const maxDD     = hasChart ? Math.min(...pts.map(p => p.drawdown_pct)) : 0
  const wins      = pts.filter(p => p.net_ret > 0).length
  const winRate   = pts.length > 0 ? (wins / pts.length) * 100 : 0
  const curveColor = totalPct >= 0 ? '#00d48a' : '#f04f4f'

  function fmtDate(ts: string) {
    try {
      const d = new Date(ts.endsWith('Z') ? ts : ts + 'Z')
      return `${d.getMonth() + 1}/${d.getDate()}`
    } catch { return '' }
  }

  const xTicks = hasChart
    ? pts.filter((_, i) => i === 0 || i === pts.length - 1 || i % Math.max(1, Math.floor(pts.length / 5)) === 0)
        .map(p => p.trade_n)
    : []

  return (
    <div style={{ padding: '0 0 32px' }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 20 }}>
        <h2 style={{ fontSize: 14, fontWeight: 700, letterSpacing: '0.06em' }}>
          ◇ PERPS — PAPER TRADING
        </h2>
        <span style={{
          fontSize: 9, padding: '2px 8px', borderRadius: 3, fontWeight: 700, ...MONO,
          background: 'rgba(240,165,0,0.12)', color: 'var(--amber)',
          border: '1px solid rgba(240,165,0,0.25)',
        }}>
          SIMULATION
        </span>
        {data && (
          <>
            <span style={{
              fontSize: 9, padding: '2px 8px', borderRadius: 3, fontWeight: 700, ...MONO,
              background: enabled ? 'rgba(0,212,138,0.12)' : 'rgba(100,100,100,0.12)',
              color: enabled ? 'var(--green)' : 'var(--muted)',
              border: `1px solid ${enabled ? 'rgba(0,212,138,0.25)' : 'rgba(100,100,100,0.25)'}`,
            }}>
              {enabled ? 'EXECUTOR ON' : 'EXECUTOR OFF'}
            </span>
            <button
              disabled={toggling}
              onClick={() => togglePerps(!enabled)}
              style={{
                marginLeft: 'auto', padding: '5px 14px', borderRadius: 5,
                fontSize: 11, fontWeight: 700, cursor: 'pointer', ...MONO,
                background: enabled ? 'rgba(248,81,73,0.15)' : 'rgba(0,212,138,0.15)',
                color: enabled ? 'var(--red)' : 'var(--green)',
                border: `1px solid ${enabled ? 'rgba(248,81,73,0.3)' : 'rgba(0,212,138,0.3)'}`,
              }}
            >
              {toggling ? '…' : enabled ? 'DISABLE' : 'ENABLE'}
            </button>
          </>
        )}
      </div>

      {/* Stats strip */}
      {data && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 10, marginBottom: 20 }}>
          {[
            { label: 'OPEN', value: String(data.open_positions), color: 'var(--text)' },
            { label: 'CLOSED', value: String(data.total_closed), color: 'var(--muted)' },
            {
              label: 'WIN RATE',
              value: data.win_rate != null ? `${data.win_rate.toFixed(0)}%` : '—',
              color: data.win_rate != null ? (data.win_rate >= 50 ? 'var(--green)' : 'var(--red)') : 'var(--dim)',
            },
            {
              label: 'AVG PNL',
              value: data.avg_pnl_pct != null ? `${data.avg_pnl_pct > 0 ? '+' : ''}${data.avg_pnl_pct.toFixed(1)}%` : '—',
              color: data.avg_pnl_pct != null ? (data.avg_pnl_pct > 0 ? 'var(--green)' : 'var(--red)') : 'var(--dim)',
            },
          ].map(({ label, value, color }) => (
            <div key={label} style={{
              background: 'var(--surface2)', border: '1px solid var(--border)',
              borderRadius: 6, padding: '8px 12px', textAlign: 'center',
            }}>
              <div style={{ fontSize: 9, color: 'var(--dim)', ...MONO, letterSpacing: '0.12em', marginBottom: 4 }}>{label}</div>
              <div style={{ fontSize: 16, fontWeight: 800, color, ...MONO }}>{value}</div>
            </div>
          ))}
        </div>
      )}

      {/* Quick Open form */}
      {data && enabled && (
        <div style={{
          marginBottom: 20, padding: '14px 16px',
          background: 'var(--surface2)', border: '1px solid var(--border)', borderRadius: 8,
        }}>
          <div style={{
            fontSize: 9, fontWeight: 600, letterSpacing: '0.18em',
            color: 'var(--dim)', ...MONO, textTransform: 'uppercase', marginBottom: 10,
          }}>
            ⚡ Quick Open (Paper)
          </div>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'flex-end' }}>
            {/* Symbol */}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              <label style={{ fontSize: 9, color: 'var(--dim)', ...MONO, letterSpacing: '0.1em' }}>SYMBOL</label>
              <div style={{ display: 'flex', gap: 4 }}>
                {(['SOL', 'BTC', 'ETH'] as const).map(sym => (
                  <button key={sym} onClick={() => setOpenSymbol(sym)} style={{
                    padding: '5px 10px', borderRadius: 4, fontSize: 11, fontWeight: 700,
                    cursor: 'pointer', ...MONO,
                    background: openSymbol === sym ? 'rgba(255,255,255,0.12)' : 'var(--surface)',
                    color: openSymbol === sym ? 'var(--text)' : 'var(--dim)',
                    border: `1px solid ${openSymbol === sym ? 'rgba(255,255,255,0.2)' : 'var(--border)'}`,
                  }}>{sym}</button>
                ))}
              </div>
            </div>

            {/* Side */}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              <label style={{ fontSize: 9, color: 'var(--dim)', ...MONO, letterSpacing: '0.1em' }}>SIDE</label>
              <div style={{ display: 'flex', gap: 4 }}>
                <button onClick={() => setOpenSide('LONG')} style={{
                  padding: '5px 12px', borderRadius: 4, fontSize: 11, fontWeight: 700,
                  cursor: 'pointer', ...MONO,
                  background: openSide === 'LONG' ? 'rgba(0,212,138,0.15)' : 'var(--surface)',
                  color: openSide === 'LONG' ? 'var(--green)' : 'var(--dim)',
                  border: `1px solid ${openSide === 'LONG' ? 'rgba(0,212,138,0.3)' : 'var(--border)'}`,
                }}>LONG ↑</button>
                <button onClick={() => setOpenSide('SHORT')} style={{
                  padding: '5px 12px', borderRadius: 4, fontSize: 11, fontWeight: 700,
                  cursor: 'pointer', ...MONO,
                  background: openSide === 'SHORT' ? 'rgba(232,121,249,0.15)' : 'var(--surface)',
                  color: openSide === 'SHORT' ? '#e879f9' : 'var(--dim)',
                  border: `1px solid ${openSide === 'SHORT' ? 'rgba(232,121,249,0.3)' : 'var(--border)'}`,
                }}>SHORT ↓</button>
              </div>
            </div>

            {/* Leverage */}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              <label style={{ fontSize: 9, color: 'var(--dim)', ...MONO, letterSpacing: '0.1em' }}>LEVERAGE</label>
              <div style={{ display: 'flex', gap: 4 }}>
                {['1', '2', '3', '5'].map(lev => (
                  <button key={lev} onClick={() => setOpenLev(lev)} style={{
                    padding: '5px 8px', borderRadius: 4, fontSize: 11, fontWeight: 700,
                    cursor: 'pointer', ...MONO,
                    background: openLev === lev ? 'rgba(240,165,0,0.15)' : 'var(--surface)',
                    color: openLev === lev ? 'var(--amber)' : 'var(--dim)',
                    border: `1px solid ${openLev === lev ? 'rgba(240,165,0,0.3)' : 'var(--border)'}`,
                  }}>{lev}×</button>
                ))}
              </div>
            </div>

            {/* Size */}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              <label style={{ fontSize: 9, color: 'var(--dim)', ...MONO, letterSpacing: '0.1em' }}>USD (opt)</label>
              <input
                type="number" placeholder="auto"
                value={openSize} onChange={e => setOpenSize(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && quickOpen()}
                style={{
                  width: 72, padding: '5px 8px', borderRadius: 4, fontSize: 12,
                  background: 'var(--surface)', border: '1px solid var(--border)',
                  color: 'var(--text)', ...MONO, outline: 'none',
                }}
              />
            </div>

            <button
              disabled={openLoading}
              onClick={quickOpen}
              style={{
                padding: '5px 18px', borderRadius: 4, fontSize: 12, fontWeight: 700,
                cursor: openLoading ? 'default' : 'pointer', ...MONO,
                background: openSide === 'LONG' ? 'rgba(0,212,138,0.15)' : 'rgba(232,121,249,0.15)',
                color: openSide === 'LONG' ? 'var(--green)' : '#e879f9',
                border: `1px solid ${openSide === 'LONG' ? 'rgba(0,212,138,0.3)' : 'rgba(232,121,249,0.3)'}`,
                opacity: openLoading ? 0.5 : 1,
              }}
            >
              {openLoading ? '…' : `⚡ ${openSide}`}
            </button>
          </div>
          {openResult && (
            <div style={{
              marginTop: 8, fontSize: 11, ...MONO, fontWeight: 600,
              color: openResult.ok ? 'var(--green)' : 'var(--red)',
            }}>
              {openResult.msg}
            </div>
          )}
        </div>
      )}

      {/* Loading/error */}
      {isLoading && <div style={{ color: 'var(--dim)', fontSize: 11, ...MONO }}>Loading perp status…</div>}
      {error && <div style={{ color: 'var(--red)', fontSize: 11, ...MONO }}>Failed to load perp status</div>}

      {/* Position cards */}
      {!isLoading && positions.length === 0 && (
        <div style={{ padding: '40px 0', textAlign: 'center', color: 'var(--dim)', fontSize: 12, ...MONO }}>
          No open paper perp positions.
          {!enabled && (
            <div style={{ marginTop: 8, fontSize: 10, color: 'var(--muted)' }}>
              Enable the executor to start auto-firing paper trades on BULL/BEAR regime signals.
            </div>
          )}
        </div>
      )}

      {positions.length > 0 && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))', gap: 12, marginBottom: 28 }}>
          {positions.map(pos => (
            <PerpCard key={pos.id} pos={pos} onForceClose={id => forceCloseMut.mutate(id)} />
          ))}
        </div>
      )}

      {/* Equity Curve */}
      <div style={{
        background: 'var(--surface2)', border: '1px solid var(--border)',
        borderRadius: 10, padding: '18px 20px', marginTop: 12,
      }}>
        <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: 16 }}>
          <div>
            <div style={{ fontSize: 9, fontWeight: 600, letterSpacing: '0.18em', color: 'var(--dim)', ...MONO, textTransform: 'uppercase', marginBottom: 6 }}>
              Perps Paper Equity Curve
            </div>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 10 }}>
              <span style={{ fontSize: 28, fontWeight: 800, color: curveColor, ...MONO, lineHeight: 1 }}>
                {totalPct >= 0 ? '+' : ''}{totalPct.toFixed(2)}%
              </span>
              <span style={{ fontSize: 11, color: 'var(--dim)', ...MONO }}>{pts.length} trades</span>
            </div>
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 10 }}>
            <div style={{ display: 'flex', gap: 4 }}>
              {[7, 30, 90].map(d => (
                <button key={d} onClick={() => setLookback(d)} style={{
                  padding: '3px 10px', borderRadius: 4, fontSize: 10, fontWeight: 700,
                  cursor: 'pointer', ...MONO,
                  background: lookback === d ? 'rgba(255,255,255,0.1)' : 'transparent',
                  color: lookback === d ? 'var(--text)' : 'var(--dim)',
                  border: `1px solid ${lookback === d ? 'rgba(255,255,255,0.2)' : 'transparent'}`,
                }}>{d}D</button>
              ))}
            </div>
            <div style={{ display: 'flex', gap: 16 }}>
              <div style={{ textAlign: 'right' }}>
                <div style={{ fontSize: 8.5, color: 'var(--dim)', ...MONO, letterSpacing: '0.1em' }}>WIN RATE</div>
                <div style={{ fontSize: 13, fontWeight: 700, ...MONO, color: winRate >= 50 ? 'var(--green)' : 'var(--red)' }}>{winRate.toFixed(0)}%</div>
              </div>
              <div style={{ textAlign: 'right' }}>
                <div style={{ fontSize: 8.5, color: 'var(--dim)', ...MONO, letterSpacing: '0.1em' }}>MAX DD</div>
                <div style={{ fontSize: 13, fontWeight: 700, ...MONO, color: 'var(--red)' }}>{maxDD.toFixed(1)}%</div>
              </div>
            </div>
          </div>
        </div>

        {!hasChart && (
          <div style={{ height: 180, display: 'flex', alignItems: 'center', justifyContent: 'center', flexDirection: 'column', gap: 8 }}>
            <div style={{ color: 'var(--dim)', fontSize: 12, ...MONO }}>No perp trade data yet</div>
            <div style={{ color: 'var(--muted)', fontSize: 10, ...MONO }}>Enable executor + wait for BULL/BEAR signal to fire first paper trade</div>
          </div>
        )}

        {hasChart && (
          <ResponsiveContainer width="100%" height={200}>
            <AreaChart data={pts} margin={{ top: 4, right: 4, left: 0, bottom: 0 }}>
              <defs>
                <linearGradient id="perp-grad" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%"  stopColor={curveColor} stopOpacity={0.18} />
                  <stop offset="95%" stopColor={curveColor} stopOpacity={0.01} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.04)" vertical={false} />
              <XAxis
                dataKey="trade_n"
                ticks={xTicks}
                tickFormatter={n => { const pt = pts.find(p => p.trade_n === n); return pt ? fmtDate(pt.ts) : String(n) }}
                tick={{ fill: '#444', fontSize: 9, fontFamily: 'JetBrains Mono, monospace' }}
                axisLine={false} tickLine={false}
              />
              <YAxis
                tickFormatter={v => `${v > 0 ? '+' : ''}${Number(v).toFixed(0)}%`}
                tick={{ fill: '#444', fontSize: 9, fontFamily: 'JetBrains Mono, monospace' }}
                axisLine={false} tickLine={false} width={46}
              />
              <Tooltip content={<PerpEquityTooltip />} />
              <ReferenceLine y={0} stroke="rgba(255,255,255,0.12)" strokeDasharray="4 4" />
              <Area type="monotone" dataKey="equity_pct" stroke={curveColor} strokeWidth={2}
                fill="url(#perp-grad)" dot={false}
                activeDot={{ r: 4, fill: curveColor, stroke: '#0e111b', strokeWidth: 2 }}
                isAnimationActive={false} />
            </AreaChart>
          </ResponsiveContainer>
        )}

        {hasChart && last && (
          <div style={{ display: 'flex', marginTop: 10, borderTop: '1px solid var(--border)', paddingTop: 10 }}>
            <span style={{ fontSize: 9, color: 'var(--dim)', ...MONO }}>
              Leveraged · 0.05% fee/side · Jupiter Perps · paper only
            </span>
            <span style={{ fontSize: 9, color: 'var(--dim)', ...MONO, marginLeft: 'auto' }}>
              Last: {last.side} {last.symbol} {last.net_ret >= 0 ? '+' : ''}{last.net_ret.toFixed(2)}%
            </span>
          </div>
        )}
      </div>
    </div>
  )
}
