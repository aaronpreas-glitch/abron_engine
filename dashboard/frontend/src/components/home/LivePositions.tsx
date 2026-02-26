/**
 * LivePositions — shows all open auto-executor positions with live PnL,
 * exit plan details, force-sell, and a Quick Buy form.
 *
 * Data: GET /api/executor/status, GET /api/trades/live-pnl
 * Actions: POST /api/executor/force-sell, /toggle, /manual-buy
 */
import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip,
  ReferenceLine, ResponsiveContainer,
} from 'recharts'
import { api } from '../../api'

// ── Types ──────────────────────────────────────────────────────────────────────

interface OpenPosition {
  id: number
  symbol: string
  mint: string | null
  entry_price: number | null
  stop_price: number | null
  opened_ts: string
  notes: string | null
}

interface PriceFeedStatus {
  ws_connected: boolean
  registered_mints: number
  fallback_poll_sec: number
}

interface ExecutorStatus {
  enabled: boolean
  dry_run: boolean
  portfolio_usd: number
  min_score: number
  max_open_positions: number
  open_positions: number
  price_feed?: PriceFeedStatus
  positions: OpenPosition[]
  total_closed: number
  win_rate: number | null
  avg_pnl_pct: number | null
  exit_summary: Record<string, { count: number; wins: number; avg_pnl: number; win_rate: number | null }>
}

// ── Helpers ───────────────────────────────────────────────────────────────────

const MONO: React.CSSProperties = { fontFamily: 'JetBrains Mono, monospace' }

function fmtPrice(v: number | null) {
  if (v == null) return '—'
  if (v < 0.000001) return `$${v.toFixed(10)}`.replace(/0+$/, '')
  if (v < 0.001) return `$${v.toFixed(8)}`.replace(/0+$/, '')
  if (v < 1) return `$${v.toFixed(6)}`.replace(/0+$/, '')
  return `$${v.toFixed(4)}`
}

function timeAgo(ts: string) {
  const d = Date.now() - new Date(ts + (ts.endsWith('Z') ? '' : 'Z')).getTime()
  const m = Math.floor(d / 60000)
  const h = Math.floor(m / 60)
  if (m < 60) return `${m}m ago`
  if (h < 24) return `${h}h ago`
  return `${Math.floor(h / 24)}d ago`
}

function parseNotes(notes: string | null): Record<string, string> {
  const result: Record<string, string> = {}
  if (!notes) return result
  notes.split('|').forEach(part => {
    const [k, v] = part.trim().split('=')
    if (k && v !== undefined) result[k.trim()] = v.trim()
  })
  return result
}

// ── Stat pill ─────────────────────────────────────────────────────────────────

function StatPill({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div style={{
      background: 'var(--surface2)', border: '1px solid var(--border)',
      borderRadius: 6, padding: '8px 12px', textAlign: 'center',
    }}>
      <div style={{ fontSize: 9, color: 'var(--dim)', letterSpacing: '0.12em', ...MONO, marginBottom: 4 }}>
        {label}
      </div>
      <div style={{ fontSize: 16, fontWeight: 800, color: color || 'var(--text)', ...MONO, lineHeight: 1 }}>
        {value}
      </div>
    </div>
  )
}

interface LivePnlEntry {
  mark: number
  unrealized_pct: number
  unrealized_usd: number
}

// ── Position card ─────────────────────────────────────────────────────────────

function PositionCard({
  pos, onForceSell, pnl,
}: {
  pos: OpenPosition
  onForceSell: (s: string) => void
  pnl?: LivePnlEntry
}) {
  const notes = parseNotes(pos.notes)
  // TP values are stored as absolute prices (e.g. "tp1=0.0000312")
  const tp1Raw = notes['tp1']
  const tp2Raw = notes['tp2']
  const tp1 = tp1Raw ? fmtPrice(parseFloat(tp1Raw)) : '—'
  const tp2 = tp2Raw ? fmtPrice(parseFloat(tp2Raw)) : '—'
  const score = notes['score'] || '—'
  const conf = notes['conf'] || '—'
  const regime = notes['regime'] || '—'
  const isAuto = notes['auto'] === '1'

  const pnlPct = pnl?.unrealized_pct ?? null
  const pnlUsd = pnl?.unrealized_usd ?? null
  const markPrice = pnl?.mark ?? null
  const pnlColor = pnlPct == null ? 'var(--dim)' : pnlPct > 0 ? 'var(--green)' : pnlPct < 0 ? 'var(--red)' : 'var(--muted)'

  return (
    <div style={{
      background: 'var(--surface2)', border: '1px solid var(--border)',
      borderRadius: 8, padding: '14px 16px', position: 'relative',
    }}>
      {/* Symbol + meta */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
        <span style={{ fontWeight: 700, fontSize: 15, color: 'var(--green)' }}>
          ${pos.symbol}
        </span>
        <span style={{
          fontSize: 9, padding: '2px 6px', borderRadius: 3,
          background: 'rgba(0,212,138,0.1)', color: 'var(--green)',
          border: '1px solid rgba(0,212,138,0.2)', fontWeight: 600, ...MONO,
        }}>
          OPEN
        </span>
        {isAuto && (
          <span style={{
            fontSize: 9, padding: '2px 6px', borderRadius: 3,
            background: 'rgba(100,100,100,0.15)', color: 'var(--muted)',
            border: '1px solid var(--border)', ...MONO,
          }}>
            AUTO
          </span>
        )}
        <span style={{ marginLeft: 'auto', fontSize: 10, color: 'var(--dim)', ...MONO }}>
          {timeAgo(pos.opened_ts)}
        </span>
      </div>

      {/* Live PnL banner */}
      {pnlPct != null && (
        <div style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          marginBottom: 10, padding: '6px 10px', borderRadius: 6,
          background: pnlPct > 0 ? 'rgba(0,212,138,0.07)' : pnlPct < 0 ? 'rgba(240,79,79,0.07)' : 'rgba(255,255,255,0.04)',
          border: `1px solid ${pnlPct > 0 ? 'rgba(0,212,138,0.2)' : pnlPct < 0 ? 'rgba(240,79,79,0.2)' : 'var(--border)'}`,
        }}>
          <div>
            <div style={{ fontSize: 8.5, color: 'var(--dim)', ...MONO, letterSpacing: '0.12em' }}>UNREALIZED</div>
            <div style={{ fontSize: 16, fontWeight: 800, color: pnlColor, ...MONO, lineHeight: 1.1 }}>
              {pnlPct > 0 ? '+' : ''}{pnlPct.toFixed(2)}%
            </div>
          </div>
          <div style={{ textAlign: 'right' }}>
            {markPrice != null && (
              <div style={{ fontSize: 9.5, color: 'var(--muted)', ...MONO }}>
                Mark: {fmtPrice(markPrice)}
              </div>
            )}
            {pnlUsd != null && (
              <div style={{ fontSize: 11, fontWeight: 700, color: pnlColor, ...MONO }}>
                {pnlUsd > 0 ? '+' : ''}${Math.abs(pnlUsd).toFixed(2)}
              </div>
            )}
          </div>
        </div>
      )}

      {/* Prices */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '4px 12px', fontSize: 11, marginBottom: 10 }}>
        <div><span style={{ color: 'var(--muted)' }}>Entry  </span><span style={{ ...MONO }}>{fmtPrice(pos.entry_price)}</span></div>
        <div><span style={{ color: 'var(--muted)' }}>Stop   </span><span style={{ ...MONO, color: 'var(--red)' }}>{fmtPrice(pos.stop_price)}</span></div>
        <div><span style={{ color: 'var(--muted)' }}>TP1    </span><span style={{ ...MONO, color: 'var(--green)' }}>{tp1}</span></div>
        <div><span style={{ color: 'var(--muted)' }}>TP2    </span><span style={{ ...MONO, color: 'var(--green)' }}>{tp2}</span></div>
      </div>

      {/* Signal meta */}
      <div style={{
        display: 'flex', gap: 6, flexWrap: 'wrap', fontSize: 10,
        borderTop: '1px solid var(--border)', paddingTop: 8, marginBottom: 10,
      }}>
        {score !== '—' && (
          <span style={{ color: 'var(--muted)', ...MONO }}>Score: <b style={{ color: 'var(--text)' }}>{score}</b></span>
        )}
        {conf !== '—' && (
          <span style={{ color: 'var(--muted)', ...MONO }}>Conf: <b style={{ color: 'var(--amber)' }}>{conf}</b></span>
        )}
        {regime !== '—' && (
          <span style={{ color: 'var(--muted)', ...MONO, fontSize: 9 }}>{regime}</span>
        )}
      </div>

      {/* Force sell button */}
      <button
        onClick={() => {
          if (window.confirm(`Force-sell $${pos.symbol}? This will execute a market sell immediately.`)) {
            onForceSell(pos.symbol)
          }
        }}
        style={{
          width: '100%', padding: '6px 0', borderRadius: 5, fontSize: 11, fontWeight: 700,
          background: 'rgba(248,81,73,0.15)', color: 'var(--red)',
          border: '1px solid rgba(248,81,73,0.35)', cursor: 'pointer',
          letterSpacing: '0.06em', ...MONO,
        }}
      >
        ⚡ FORCE SELL
      </button>
    </div>
  )
}

// ── Equity Curve ──────────────────────────────────────────────────────────────

interface EquityPoint {
  trade_n: number
  ts: string
  symbol: string
  gross_ret: number
  net_ret: number
  equity: number
  equity_pct: number
  drawdown_pct: number
  regime_label?: string
  confidence?: string
  score?: number
}

const LOOKBACK_OPTIONS = [
  { label: '7D',  days: 7  },
  { label: '30D', days: 30 },
  { label: '90D', days: 90 },
]

// Custom tooltip for the equity chart
function EquityTooltip({ active, payload }: { active?: boolean; payload?: { payload: EquityPoint }[] }) {
  if (!active || !payload?.length) return null
  const d = payload[0].payload
  const pct = d.equity_pct
  const color = pct >= 0 ? '#00d48a' : '#f04f4f'
  return (
    <div style={{
      background: 'rgba(14,17,27,0.97)', border: '1px solid rgba(255,255,255,0.1)',
      borderRadius: 8, padding: '10px 14px', fontSize: 11,
      fontFamily: 'JetBrains Mono, monospace', minWidth: 160,
      boxShadow: '0 8px 32px rgba(0,0,0,0.5)',
    }}>
      <div style={{ color: '#666', fontSize: 9, letterSpacing: '0.1em', marginBottom: 4 }}>
        TRADE #{d.trade_n} · {d.symbol}
      </div>
      <div style={{ fontSize: 18, fontWeight: 800, color, lineHeight: 1.1, marginBottom: 6 }}>
        {pct >= 0 ? '+' : ''}{pct.toFixed(2)}%
      </div>
      <div style={{ color: '#888', fontSize: 10 }}>
        Net ret: <span style={{ color: d.net_ret >= 0 ? '#00d48a' : '#f04f4f' }}>
          {d.net_ret >= 0 ? '+' : ''}{d.net_ret.toFixed(2)}%
        </span>
      </div>
      {d.drawdown_pct < 0 && (
        <div style={{ color: '#888', fontSize: 10, marginTop: 2 }}>
          Drawdown: <span style={{ color: '#f04f4f' }}>{d.drawdown_pct.toFixed(2)}%</span>
        </div>
      )}
      {d.regime_label && (
        <div style={{ color: '#555', fontSize: 9, marginTop: 4 }}>
          {d.regime_label}{d.confidence ? ` · Conf ${d.confidence}` : ''}
        </div>
      )}
    </div>
  )
}

function EquityChart() {
  const [lookback, setLookback] = useState(30)

  const { data: points, isLoading, error } = useQuery<EquityPoint[]>({
    queryKey: ['equity-curve', lookback],
    queryFn: () => api.get(`/performance/equity-curve-v2?lookback_days=${lookback}&horizon_hours=4`)
      .then(r => r.data),
    refetchInterval: 5 * 60_000,   // refresh every 5 min
    staleTime: 4 * 60_000,
  })

  const pts = Array.isArray(points) ? points : []
  const hasData = pts.length >= 2

  // Derive stats from curve data
  const last = pts[pts.length - 1]
  const totalPct  = last?.equity_pct ?? 0
  const maxDD     = hasData ? Math.min(...pts.map(p => p.drawdown_pct)) : 0
  const wins      = pts.filter(p => p.net_ret > 0).length
  const winRate   = pts.length > 0 ? (wins / pts.length) * 100 : 0
  const isPos     = totalPct >= 0
  const curveColor = isPos ? '#00d48a' : '#f04f4f'
  const gradId = 'eq-grad'

  // Format x-axis label: show date from ts
  function fmtDate(ts: string) {
    try {
      const d = new Date(ts.endsWith('Z') ? ts : ts + 'Z')
      return `${d.getMonth() + 1}/${d.getDate()}`
    } catch { return '' }
  }

  // Thin out points for x-axis labels (avoid crowding)
  const xTicks = hasData
    ? pts.filter((_, i) => i === 0 || i === pts.length - 1 || i % Math.max(1, Math.floor(pts.length / 5)) === 0)
        .map(p => p.trade_n)
    : []

  return (
    <div style={{
      background: 'var(--surface2)', border: '1px solid var(--border)',
      borderRadius: 10, padding: '18px 20px', marginTop: 28,
    }}>
      {/* Header row */}
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: 16 }}>
        <div>
          <div style={{
            fontSize: 9, fontWeight: 600, letterSpacing: '0.18em',
            color: 'var(--dim)', ...MONO, textTransform: 'uppercase', marginBottom: 6,
          }}>
            Portfolio Equity Curve
          </div>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 10 }}>
            <span style={{ fontSize: 28, fontWeight: 800, color: curveColor, ...MONO, lineHeight: 1 }}>
              {totalPct >= 0 ? '+' : ''}{totalPct.toFixed(2)}%
            </span>
            <span style={{ fontSize: 11, color: 'var(--dim)', ...MONO }}>
              {pts.length} trades
            </span>
          </div>
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 10 }}>
          {/* Lookback selector */}
          <div style={{ display: 'flex', gap: 4 }}>
            {LOOKBACK_OPTIONS.map(opt => (
              <button
                key={opt.days}
                onClick={() => setLookback(opt.days)}
                style={{
                  padding: '3px 10px', borderRadius: 4, fontSize: 10, fontWeight: 700,
                  cursor: 'pointer', ...MONO,
                  background: lookback === opt.days ? 'rgba(255,255,255,0.1)' : 'transparent',
                  color: lookback === opt.days ? 'var(--text)' : 'var(--dim)',
                  border: `1px solid ${lookback === opt.days ? 'rgba(255,255,255,0.2)' : 'transparent'}`,
                }}
              >
                {opt.label}
              </button>
            ))}
          </div>

          {/* Mini stat pills */}
          <div style={{ display: 'flex', gap: 16 }}>
            <div style={{ textAlign: 'right' }}>
              <div style={{ fontSize: 8.5, color: 'var(--dim)', ...MONO, letterSpacing: '0.1em' }}>WIN RATE</div>
              <div style={{ fontSize: 13, fontWeight: 700, ...MONO, color: winRate >= 50 ? 'var(--green)' : 'var(--red)' }}>
                {winRate.toFixed(0)}%
              </div>
            </div>
            <div style={{ textAlign: 'right' }}>
              <div style={{ fontSize: 8.5, color: 'var(--dim)', ...MONO, letterSpacing: '0.1em' }}>MAX DD</div>
              <div style={{ fontSize: 13, fontWeight: 700, ...MONO, color: 'var(--red)' }}>
                {maxDD.toFixed(1)}%
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* Chart */}
      {isLoading && (
        <div style={{ height: 180, display: 'flex', alignItems: 'center', justifyContent: 'center',
          color: 'var(--dim)', fontSize: 11, ...MONO }}>
          Loading equity curve…
        </div>
      )}
      {error && (
        <div style={{ height: 180, display: 'flex', alignItems: 'center', justifyContent: 'center',
          color: 'var(--red)', fontSize: 11, ...MONO }}>
          Failed to load curve
        </div>
      )}
      {!isLoading && !error && !hasData && (
        <div style={{ height: 180, display: 'flex', alignItems: 'center', justifyContent: 'center',
          flexDirection: 'column', gap: 8 }}>
          <div style={{ color: 'var(--dim)', fontSize: 12, ...MONO }}>No trade data yet</div>
          <div style={{ color: 'var(--muted)', fontSize: 10, ...MONO }}>
            Paper trades will appear here once the engine fires its first signal
          </div>
        </div>
      )}
      {hasData && (
        <ResponsiveContainer width="100%" height={200}>
          <AreaChart data={pts} margin={{ top: 4, right: 4, left: 0, bottom: 0 }}>
            <defs>
              <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%"  stopColor={curveColor} stopOpacity={0.18} />
                <stop offset="95%" stopColor={curveColor} stopOpacity={0.01} />
              </linearGradient>
            </defs>
            <CartesianGrid
              strokeDasharray="3 3"
              stroke="rgba(255,255,255,0.04)"
              vertical={false}
            />
            <XAxis
              dataKey="trade_n"
              ticks={xTicks}
              tickFormatter={n => {
                const pt = pts.find(p => p.trade_n === n)
                return pt ? fmtDate(pt.ts) : String(n)
              }}
              tick={{ fill: '#444', fontSize: 9, fontFamily: 'JetBrains Mono, monospace' }}
              axisLine={false}
              tickLine={false}
            />
            <YAxis
              tickFormatter={v => `${v > 0 ? '+' : ''}${Number(v).toFixed(0)}%`}
              tick={{ fill: '#444', fontSize: 9, fontFamily: 'JetBrains Mono, monospace' }}
              axisLine={false}
              tickLine={false}
              width={46}
            />
            <Tooltip content={<EquityTooltip />} />
            <ReferenceLine y={0} stroke="rgba(255,255,255,0.12)" strokeDasharray="4 4" />
            <Area
              type="monotone"
              dataKey="equity_pct"
              stroke={curveColor}
              strokeWidth={2}
              fill={`url(#${gradId})`}
              dot={false}
              activeDot={{ r: 4, fill: curveColor, stroke: '#0e111b', strokeWidth: 2 }}
              isAnimationActive={false}
            />
          </AreaChart>
        </ResponsiveContainer>
      )}

      {/* Bottom meta */}
      {hasData && (
        <div style={{
          display: 'flex', gap: 6, marginTop: 10, flexWrap: 'wrap',
          borderTop: '1px solid var(--border)', paddingTop: 10,
        }}>
          <span style={{ fontSize: 9, color: 'var(--dim)', ...MONO }}>
            Fee-adjusted · 0.5% round-trip · 4h horizon · paper trades only
          </span>
          {last && (
            <span style={{ fontSize: 9, color: 'var(--dim)', ...MONO, marginLeft: 'auto' }}>
              Last: {last.symbol} {last.net_ret >= 0 ? '+' : ''}{last.net_ret.toFixed(2)}%
            </span>
          )}
        </div>
      )}
    </div>
  )
}

// ── Main component ────────────────────────────────────────────────────────────

export function LivePositions({ mode = 'paper' }: { mode?: 'paper' | 'live' } = {}) {
  const qc = useQueryClient()
  const [toggling, setToggling] = useState(false)
  const [togglingDryRun, setTogglingDryRun] = useState(false)
  const [portfolioInput, setPortfolioInput] = useState('')
  const [savingPortfolio, setSavingPortfolio] = useState(false)
  const [portfolioSaved, setPortfolioSaved] = useState(false)

  // Quick Buy form state
  const [buySymbol, setBuySymbol] = useState('')
  const [buyMint, setBuyMint] = useState('')
  const [buyAmount, setBuyAmount] = useState('')
  const [buyLoading, setBuyLoading] = useState(false)
  const [buyResult, setBuyResult] = useState<{ ok: boolean; msg: string } | null>(null)

  const { data, isLoading, error } = useQuery<ExecutorStatus>({
    queryKey: ['executor-status'],
    queryFn: () => api.get('/executor/status').then(r => r.data),
    refetchInterval: 30_000,
    staleTime: 15_000,
  })

  // Live PnL — poll every 15s when positions are open
  const { data: livePnl } = useQuery<Record<string, LivePnlEntry>>({
    queryKey: ['executor-live-pnl'],
    queryFn: () => api.get('/trades/live-pnl').then(r => r.data),
    refetchInterval: 15_000,
    staleTime: 10_000,
    enabled: (data?.open_positions ?? 0) > 0,
  })

  const forceSellMut = useMutation({
    mutationFn: (symbol: string) => api.post('/executor/force-sell', { symbol }).then(r => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['executor-status'] }),
  })

  async function toggleExecutor(enable: boolean) {
    setToggling(true)
    try {
      await api.post('/executor/toggle', { enabled: enable })
      qc.invalidateQueries({ queryKey: ['executor-status'] })
    } finally {
      setToggling(false)
    }
  }

  async function toggleDryRun(dry: boolean) {
    setTogglingDryRun(true)
    try {
      await api.post('/executor/set-dry-run', { dry_run: dry })
      qc.invalidateQueries({ queryKey: ['executor-status'] })
    } finally {
      setTogglingDryRun(false)
    }
  }

  async function savePortfolio() {
    const val = parseFloat(portfolioInput)
    if (!val || val <= 0) return
    setSavingPortfolio(true)
    try {
      await api.post('/executor/set-portfolio', { portfolio_usd: val })
      qc.invalidateQueries({ queryKey: ['executor-status'] })
      setPortfolioInput('')
      setPortfolioSaved(true)
      setTimeout(() => setPortfolioSaved(false), 2000)
    } finally {
      setSavingPortfolio(false)
    }
  }

  async function quickBuy() {
    const sym = buySymbol.trim().toUpperCase()
    const mint = buyMint.trim()
    if (!sym || !mint) {
      setBuyResult({ ok: false, msg: 'Symbol and mint address are required' })
      return
    }
    setBuyLoading(true)
    setBuyResult(null)
    try {
      const body: Record<string, unknown> = { symbol: sym, mint }
      const amt = parseFloat(buyAmount)
      if (amt > 0) body.position_usd = amt
      const res = await api.post('/executor/manual-buy', body).then(r => r.data)
      if (res.success) {
        setBuyResult({ ok: true, msg: `✅ Order sent — ${sym} $${res.position_usd}` })
        setBuySymbol('')
        setBuyMint('')
        setBuyAmount('')
        qc.invalidateQueries({ queryKey: ['executor-status'] })
      } else {
        setBuyResult({ ok: false, msg: res.error ?? 'Unknown error' })
      }
    } catch (e: unknown) {
      const msg = (e as { response?: { data?: { error?: string } } })?.response?.data?.error ?? 'Request failed'
      setBuyResult({ ok: false, msg })
    } finally {
      setBuyLoading(false)
      setTimeout(() => setBuyResult(null), 5000)
    }
  }

  const enabled   = data?.enabled ?? false
  const dry_run   = data?.dry_run ?? true
  const positions = data?.positions ?? []

  return (
    <div style={{ padding: '0 0 32px' }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 14, marginBottom: 20 }}>
        <h2 style={{ fontSize: 14, fontWeight: 700, letterSpacing: '0.06em' }}>
          ⚡ {mode === 'live' ? 'SPOT — LIVE TRADING' : 'SPOT — PAPER TRADING'}
        </h2>
        <span style={{
          fontSize: 9, padding: '2px 8px', borderRadius: 3, fontWeight: 700, ...MONO,
          background: mode === 'live' ? 'rgba(248,81,73,0.12)' : 'rgba(240,165,0,0.12)',
          color: mode === 'live' ? 'var(--red)' : 'var(--amber)',
          border: `1px solid ${mode === 'live' ? 'rgba(248,81,73,0.3)' : 'rgba(240,165,0,0.25)'}`,
        }}>
          {mode === 'live' ? '⬤ LIVE' : 'SIMULATION'}
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
            {dry_run && (
              <span style={{
                fontSize: 9, padding: '2px 8px', borderRadius: 3, fontWeight: 700, ...MONO,
                background: 'rgba(240,165,0,0.12)', color: 'var(--amber)',
                border: '1px solid rgba(240,165,0,0.25)',
              }}>
                DRY RUN
              </span>
            )}
            <button
              disabled={toggling}
              onClick={() => toggleExecutor(!enabled)}
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

      {/* Controls row — dry run toggle + portfolio size */}
      {data && (
        <div style={{
          display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap',
          marginBottom: 20, padding: '12px 14px',
          background: 'var(--surface2)', border: '1px solid var(--border)', borderRadius: 8,
        }}>
          {/* Dry run toggle */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span style={{ fontSize: 11, color: 'var(--muted)', ...MONO }}>Dry Run</span>
            <button
              disabled={togglingDryRun}
              onClick={() => toggleDryRun(!dry_run)}
              style={{
                padding: '4px 12px', borderRadius: 4, fontSize: 11, fontWeight: 700,
                cursor: 'pointer', ...MONO,
                background: dry_run ? 'rgba(240,165,0,0.15)' : 'rgba(100,100,100,0.15)',
                color: dry_run ? 'var(--amber)' : 'var(--muted)',
                border: `1px solid ${dry_run ? 'rgba(240,165,0,0.3)' : 'rgba(100,100,100,0.25)'}`,
              }}
            >
              {togglingDryRun ? '…' : dry_run ? 'ON — click to go LIVE' : 'OFF — click to enable'}
            </button>
            {!dry_run && (
              <span style={{ fontSize: 9, color: 'var(--red)', fontWeight: 700, ...MONO }}>
                ⚠ LIVE TRADING
              </span>
            )}
          </div>

          <div style={{ width: 1, height: 20, background: 'var(--border)' }} />

          {/* Portfolio size */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span style={{ fontSize: 11, color: 'var(--muted)', ...MONO }}>
              Portfolio: <b style={{ color: 'var(--text)' }}>${data.portfolio_usd?.toLocaleString() ?? '—'}</b>
            </span>
            <input
              type="number"
              placeholder="new amount"
              value={portfolioInput}
              onChange={e => setPortfolioInput(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && savePortfolio()}
              style={{
                width: 100, padding: '4px 8px', borderRadius: 4, fontSize: 11,
                background: 'var(--surface)', border: '1px solid var(--border)',
                color: 'var(--text)', ...MONO, outline: 'none',
              }}
            />
            <button
              disabled={savingPortfolio || !portfolioInput}
              onClick={savePortfolio}
              style={{
                padding: '4px 10px', borderRadius: 4, fontSize: 11, fontWeight: 700,
                cursor: portfolioInput ? 'pointer' : 'default', ...MONO,
                background: portfolioSaved ? 'rgba(0,212,138,0.15)' : 'rgba(100,100,100,0.15)',
                color: portfolioSaved ? 'var(--green)' : 'var(--muted)',
                border: '1px solid var(--border)',
              }}
            >
              {portfolioSaved ? '✓ saved' : savingPortfolio ? '…' : 'set'}
            </button>
          </div>
        </div>
      )}

      {/* ── Quick Buy form — shown when executor is enabled ─────────────────── */}
      {data && enabled && (
        <div style={{
          marginBottom: 20, padding: '14px 16px',
          background: 'var(--surface2)', border: '1px solid var(--border)', borderRadius: 8,
        }}>
          <div style={{
            fontSize: 9, fontWeight: 600, letterSpacing: '0.18em',
            color: 'var(--dim)', ...MONO, textTransform: 'uppercase', marginBottom: 10,
          }}>
            ⚡ Quick Buy {dry_run ? '(Paper)' : '(LIVE)'}
          </div>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'flex-end' }}>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              <label style={{ fontSize: 9, color: 'var(--dim)', ...MONO, letterSpacing: '0.1em' }}>SYMBOL</label>
              <input
                type="text"
                placeholder="BONK"
                value={buySymbol}
                onChange={e => setBuySymbol(e.target.value.toUpperCase())}
                onKeyDown={e => e.key === 'Enter' && quickBuy()}
                style={{
                  width: 90, padding: '5px 8px', borderRadius: 4, fontSize: 12,
                  background: 'var(--surface)', border: '1px solid var(--border)',
                  color: 'var(--text)', ...MONO, outline: 'none', textTransform: 'uppercase',
                }}
              />
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4, flex: 1, minWidth: 200 }}>
              <label style={{ fontSize: 9, color: 'var(--dim)', ...MONO, letterSpacing: '0.1em' }}>MINT ADDRESS</label>
              <input
                type="text"
                placeholder="So11111...111"
                value={buyMint}
                onChange={e => setBuyMint(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && quickBuy()}
                style={{
                  width: '100%', padding: '5px 8px', borderRadius: 4, fontSize: 11,
                  background: 'var(--surface)', border: '1px solid var(--border)',
                  color: 'var(--text)', ...MONO, outline: 'none',
                }}
              />
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              <label style={{ fontSize: 9, color: 'var(--dim)', ...MONO, letterSpacing: '0.1em' }}>USD (optional)</label>
              <input
                type="number"
                placeholder="auto"
                value={buyAmount}
                onChange={e => setBuyAmount(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && quickBuy()}
                style={{
                  width: 80, padding: '5px 8px', borderRadius: 4, fontSize: 12,
                  background: 'var(--surface)', border: '1px solid var(--border)',
                  color: 'var(--text)', ...MONO, outline: 'none',
                }}
              />
            </div>
            <button
              disabled={buyLoading || !buySymbol || !buyMint}
              onClick={quickBuy}
              style={{
                padding: '5px 18px', borderRadius: 4, fontSize: 12, fontWeight: 700,
                cursor: buyLoading || !buySymbol || !buyMint ? 'default' : 'pointer',
                ...MONO,
                background: !dry_run ? 'rgba(248,81,73,0.18)' : 'rgba(0,212,138,0.15)',
                color: !dry_run ? 'var(--red)' : 'var(--green)',
                border: `1px solid ${!dry_run ? 'rgba(248,81,73,0.35)' : 'rgba(0,212,138,0.3)'}`,
                opacity: buyLoading || !buySymbol || !buyMint ? 0.5 : 1,
              }}
            >
              {buyLoading ? '…' : '⚡ BUY'}
            </button>
          </div>
          {buyResult && (
            <div style={{
              marginTop: 8, fontSize: 11, ...MONO, fontWeight: 600,
              color: buyResult.ok ? 'var(--green)' : 'var(--red)',
            }}>
              {buyResult.msg}
            </div>
          )}
        </div>
      )}

      {isLoading && (
        <div style={{ color: 'var(--dim)', fontSize: 11, ...MONO }}>Loading executor status…</div>
      )}

      {error && (
        <div style={{ color: 'var(--red)', fontSize: 11, ...MONO }}>
          Failed to load executor status
        </div>
      )}

      {/* Stats strip */}
      {data && (
        <>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 10, marginBottom: 10 }}>
            <StatPill label="OPEN"        value={String(data.open_positions)}    color="var(--text)" />
            <StatPill label="CLOSED"      value={String(data.total_closed)}      color="var(--muted)" />
            <StatPill
              label="WIN RATE"
              value={data.win_rate != null ? `${data.win_rate.toFixed(0)}%` : '—'}
              color={data.win_rate != null ? (data.win_rate >= 50 ? 'var(--green)' : 'var(--red)') : 'var(--dim)'}
            />
            <StatPill
              label="AVG PNL"
              value={data.avg_pnl_pct != null ? `${data.avg_pnl_pct > 0 ? '+' : ''}${data.avg_pnl_pct.toFixed(1)}%` : '—'}
              color={data.avg_pnl_pct != null ? (data.avg_pnl_pct > 0 ? 'var(--green)' : 'var(--red)') : 'var(--dim)'}
            />
          </div>

          {/* Price feed status bar */}
          {data.price_feed && (
            <div style={{
              display: 'flex', alignItems: 'center', gap: 10, marginBottom: 20,
              padding: '8px 12px',
              background: 'var(--surface2)', border: '1px solid var(--border)',
              borderRadius: 6, fontSize: 10,
            }}>
              <div style={{
                width: 6, height: 6, borderRadius: '50%',
                background: data.price_feed.ws_connected ? 'var(--green)' : 'var(--amber)',
                boxShadow: `0 0 6px ${data.price_feed.ws_connected ? 'var(--green)' : 'var(--amber)'}`,
                flexShrink: 0,
              }} />
              <span style={{ color: 'var(--muted)', ...MONO }}>
                Price Feed:&nbsp;
                <b style={{ color: data.price_feed.ws_connected ? 'var(--green)' : 'var(--amber)' }}>
                  {data.price_feed.ws_connected ? 'LIVE (Birdeye WS)' : `POLLING (${data.price_feed.fallback_poll_sec}s fallback)`}
                </b>
              </span>
              {data.price_feed.registered_mints > 0 && (
                <span style={{ color: 'var(--dim)', ...MONO, marginLeft: 4 }}>
                  · {data.price_feed.registered_mints} mint{data.price_feed.registered_mints !== 1 ? 's' : ''} monitored
                </span>
              )}
              <span style={{ marginLeft: 'auto', color: 'var(--dim)', ...MONO, fontSize: 9 }}>
                Phase 3 · exit latency {data.price_feed.ws_connected ? '~1s' : `≤${data.price_feed.fallback_poll_sec}s`}
              </span>
            </div>
          )}
        </>
      )}

      {/* Open positions grid */}
      {!isLoading && positions.length === 0 && (
        <div style={{
          padding: '40px 0', textAlign: 'center',
          color: 'var(--dim)', fontSize: 12, ...MONO,
        }}>
          No open positions.
          {!enabled && (
            <div style={{ marginTop: 8, fontSize: 10, color: 'var(--muted)' }}>
              Enable the executor and configure your wallet to start auto-trading.
            </div>
          )}
        </div>
      )}

      {positions.length > 0 && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))', gap: 12 }}>
          {positions.map(pos => (
            <PositionCard
              key={pos.id}
              pos={pos}
              onForceSell={sym => forceSellMut.mutate(sym)}
              pnl={livePnl?.[pos.symbol]}
            />
          ))}
        </div>
      )}

      {/* Exit reason breakdown */}
      {data && Object.keys(data.exit_summary).length > 0 && (
        <div style={{ marginTop: 28 }}>
          <div style={{
            fontSize: 9, fontWeight: 600, letterSpacing: '0.18em',
            color: 'var(--dim)', ...MONO, textTransform: 'uppercase', marginBottom: 12,
          }}>
            Exit Reason Breakdown
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))', gap: 8 }}>
            {Object.entries(data.exit_summary).map(([reason, stats]) => (
              <div key={reason} style={{
                background: 'var(--surface2)', border: '1px solid var(--border)',
                borderRadius: 6, padding: '10px 12px',
              }}>
                <div style={{ fontSize: 10, fontWeight: 700, ...MONO, marginBottom: 6, color: 'var(--text)' }}>
                  {reason}
                </div>
                <div style={{ fontSize: 11, color: 'var(--muted)' }}>
                  {stats.count} trades · {stats.win_rate != null ? `${stats.win_rate}% wins` : '—'}
                </div>
                <div style={{
                  fontSize: 12, fontWeight: 700, ...MONO, marginTop: 4,
                  color: stats.avg_pnl > 0 ? 'var(--green)' : stats.avg_pnl < 0 ? 'var(--red)' : 'var(--dim)',
                }}>
                  avg {stats.avg_pnl > 0 ? '+' : ''}{stats.avg_pnl.toFixed(1)}%
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Config hint */}
      {data && !data.enabled && (
        <div style={{
          marginTop: 24, padding: '14px 16px',
          background: 'rgba(240,165,0,0.06)', border: '1px solid rgba(240,165,0,0.2)',
          borderRadius: 8, fontSize: 11, color: 'var(--amber)', lineHeight: 1.7,
        }}>
          <b>Setup required:</b> Set <code style={{ ...MONO }}>WALLET_PRIVATE_KEY</code> in your .env,
          then flip <code style={{ ...MONO }}>EXECUTOR_DRY_RUN=true</code> to test before going live.
          Use the ENABLE button above or set <code style={{ ...MONO }}>EXECUTOR_ENABLED=true</code> in .env.
        </div>
      )}

      {/* Equity curve chart */}
      <EquityChart />
    </div>
  )
}
