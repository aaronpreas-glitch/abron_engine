/**
 * EquityCurve — Full fee-adjusted equity curve with drawdown panel.
 *
 * Shows:
 *  - Cumulative equity % (net of simulated fees)
 *  - Drawdown shaded below zero on a second chart
 *  - Trade-level tooltip with symbol, gross/net return
 *  - Lookback + horizon controls
 *  - Download CSV button
 */
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../../api'
import {
  AreaChart, Area,
  XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, ReferenceLine,
} from 'recharts'

// ─── Types ────────────────────────────────────────────────────────────────────

interface CurvePoint {
  trade_n: number
  ts: string
  symbol: string
  gross_ret: number
  net_ret: number
  equity: number
  equity_pct: number
  drawdown_pct: number
  regime_label: string | null
  confidence: string | null
  lane: string | null
}

interface SimSummary {
  trades: number
  wins: number
  losses: number
  win_rate_pct: number
  avg_net_ret: number
  avg_win: number
  avg_loss: number
  payoff_ratio: number
  expectancy_pct: number
  equity_pct: number
  max_drawdown_pct: number
  best_trade: number
  worst_trade: number
  by_regime: { regime: string; n: number; win_rate: number; avg_ret: number }[]
  by_lane: { lane: string; n: number; win_rate: number; avg_ret: number }[]
  fee_pct: number
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

const MONO: React.CSSProperties = { fontFamily: 'JetBrains Mono, monospace' }

function fmtPct(v: number | null | undefined, plus = true): string {
  if (v == null) return '—'
  const s = plus && v > 0 ? '+' : ''
  return `${s}${v.toFixed(2)}%`
}

function retColor(v: number | null | undefined) {
  if (v == null) return 'var(--muted)'
  return v > 0 ? 'var(--green)' : v < 0 ? 'var(--red)' : 'var(--muted)'
}

function winColor(wr: number) {
  return wr >= 55 ? 'var(--green)' : wr >= 45 ? 'var(--amber)' : 'var(--red)'
}

function Stat({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
      <span style={{ fontSize: 9, color: 'var(--dim)', letterSpacing: '0.14em', ...MONO, textTransform: 'uppercase' }}>
        {label}
      </span>
      <span style={{ fontSize: 18, fontWeight: 800, color: color || 'var(--text)', lineHeight: 1, ...MONO }}>
        {value}
      </span>
    </div>
  )
}

// ─── Custom tooltip ───────────────────────────────────────────────────────────

function CurveTooltip({ active, payload }: { active?: boolean; payload?: { payload: CurvePoint }[] }) {
  if (!active || !payload?.length) return null
  const d = payload[0].payload
  return (
    <div style={{
      background: 'rgba(4,7,16,0.95)',
      border: '1px solid rgba(255,255,255,0.12)',
      borderRadius: 8,
      padding: '10px 14px',
      fontSize: 10,
      ...MONO,
    }}>
      <div style={{ fontWeight: 700, color: 'var(--text)', marginBottom: 6 }}>
        #{d.trade_n} ${d.symbol}
      </div>
      <div style={{ color: 'var(--dim)', marginBottom: 4 }}>
        {d.ts ? new Date(d.ts + (d.ts.endsWith('Z') ? '' : 'Z')).toLocaleString() : ''}
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: '80px 1fr', gap: '3px 8px' }}>
        <span style={{ color: 'var(--dim)' }}>Gross:</span>
        <span style={{ color: retColor(d.gross_ret) }}>{fmtPct(d.gross_ret)}</span>
        <span style={{ color: 'var(--dim)' }}>Net:</span>
        <span style={{ color: retColor(d.net_ret), fontWeight: 700 }}>{fmtPct(d.net_ret)}</span>
        <span style={{ color: 'var(--dim)' }}>Equity:</span>
        <span style={{ color: retColor(d.equity_pct) }}>{fmtPct(d.equity_pct)}</span>
        <span style={{ color: 'var(--dim)' }}>Drawdown:</span>
        <span style={{ color: d.drawdown_pct < -5 ? 'var(--red)' : 'var(--muted)' }}>{fmtPct(d.drawdown_pct)}</span>
      </div>
      {d.regime_label && (
        <div style={{ marginTop: 5, color: 'var(--dim)' }}>
          {d.regime_label}{d.confidence ? ` · ${d.confidence}` : ''}{d.lane ? ` · ${d.lane}` : ''}
        </div>
      )}
    </div>
  )
}

// ─── Main component ───────────────────────────────────────────────────────────

interface Props {
  lookback: number
  horizon: number
}

export function EquityCurve({ lookback, horizon }: Props) {
  const [feePct] = useState(0.5)

  const { data: curve, isLoading: curveLoading } = useQuery<CurvePoint[]>({
    queryKey: ['equity-v2', lookback, horizon, feePct],
    queryFn: () =>
      api.get(`/performance/equity-curve-v2?lookback_days=${lookback}&horizon_hours=${horizon}&fee_pct=${feePct}`)
         .then(r => r.data),
    refetchInterval: 120_000,
    staleTime: 90_000,
  })

  const { data: summary } = useQuery<SimSummary>({
    queryKey: ['sim-summary', lookback, horizon, feePct],
    queryFn: () =>
      api.get(`/performance/sim-summary?lookback_days=${lookback}&horizon_hours=${horizon}&fee_pct=${feePct}`)
         .then(r => r.data),
    refetchInterval: 120_000,
    staleTime: 90_000,
  })

  const handleExport = () => {
    window.open(`/api/performance/export-csv?lookback_days=${lookback}&horizon_hours=${horizon}&fee_pct=${feePct}`, '_blank')
  }

  if (curveLoading) {
    return (
      <div style={{ padding: 40, textAlign: 'center', color: 'var(--dim)', fontSize: 11, ...MONO }}>
        Loading equity curve…
      </div>
    )
  }

  if (!curve || curve.length === 0) {
    return (
      <div style={{ padding: 40, textAlign: 'center', color: 'var(--dim)', fontSize: 12, ...MONO }}>
        No completed outcome data yet. The tracker will fill this as signals age past their evaluation horizon.
      </div>
    )
  }

  const equityEnd = curve[curve.length - 1]?.equity_pct ?? 0
  const maxDd = summary?.max_drawdown_pct ?? 0
  const eqColor = equityEnd >= 0 ? 'var(--green)' : 'var(--red)'
  const ddColor = maxDd < -15 ? 'var(--red)' : maxDd < -8 ? 'var(--amber)' : 'var(--muted)'

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>

      {/* Header + export */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div>
          <div style={{ fontSize: 9, fontWeight: 600, letterSpacing: '0.18em', color: 'var(--dim)', ...MONO, textTransform: 'uppercase' }}>
            Fee-Adjusted Equity Curve
          </div>
          <div style={{ fontSize: 10, color: 'var(--dim)', ...MONO, marginTop: 3 }}>
            {curve.length} trades · {feePct}% round-trip fee · {lookback}d lookback · {horizon}h horizon
          </div>
        </div>
        <button
          onClick={handleExport}
          style={{
            padding: '5px 14px',
            borderRadius: 7,
            border: '1px solid rgba(77,159,255,0.3)',
            background: 'rgba(77,159,255,0.08)',
            color: 'var(--blue)',
            fontSize: 10,
            fontWeight: 600,
            cursor: 'pointer',
            ...MONO,
          }}
        >
          ↓ Export CSV
        </button>
      </div>

      {/* Key stats */}
      {summary && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(90px, 1fr))', gap: '8px 24px' }}>
          <Stat label="Equity" value={fmtPct(equityEnd)} color={eqColor} />
          <Stat label="Win Rate" value={`${summary.win_rate_pct.toFixed(0)}%`} color={winColor(summary.win_rate_pct)} />
          <Stat label="Avg Net" value={fmtPct(summary.avg_net_ret)} color={retColor(summary.avg_net_ret)} />
          <Stat label="Max DD" value={fmtPct(maxDd)} color={ddColor} />
          <Stat label="Payoff" value={`${summary.payoff_ratio.toFixed(2)}×`} />
          <Stat label="Expectancy" value={fmtPct(summary.expectancy_pct)} color={retColor(summary.expectancy_pct)} />
          <Stat label="Best" value={fmtPct(summary.best_trade)} color="var(--green)" />
          <Stat label="Worst" value={fmtPct(summary.worst_trade)} color="var(--red)" />
        </div>
      )}

      {/* Equity chart */}
      <div>
        <div style={{ fontSize: 9, color: 'var(--dim)', ...MONO, marginBottom: 6, letterSpacing: '0.1em' }}>
          CUMULATIVE EQUITY (net of fees)
        </div>
        <ResponsiveContainer width="100%" height={200}>
          <AreaChart data={curve} margin={{ top: 4, right: 8, bottom: 4, left: -10 }}>
            <defs>
              <linearGradient id="eqGradV2" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%"  stopColor={equityEnd >= 0 ? '#00d48a' : '#f04f4f'} stopOpacity={0.25} />
                <stop offset="95%" stopColor={equityEnd >= 0 ? '#00d48a' : '#f04f4f'} stopOpacity={0.02} />
              </linearGradient>
            </defs>
            <CartesianGrid stroke="rgba(255,255,255,0.05)" vertical={false} />
            <XAxis
              dataKey="trade_n"
              tick={{ fontSize: 8, fill: 'var(--dim)', fontFamily: 'JetBrains Mono, monospace' }}
              label={{ value: 'Trade #', position: 'insideBottomRight', offset: -4, fontSize: 8, fill: 'var(--dim)' }}
            />
            <YAxis
              tick={{ fontSize: 8, fill: 'var(--dim)', fontFamily: 'JetBrains Mono, monospace' }}
              tickFormatter={v => `${v > 0 ? '+' : ''}${v.toFixed(0)}%`}
            />
            <ReferenceLine y={0} stroke="rgba(255,255,255,0.2)" strokeDasharray="4 3" />
            <Tooltip content={<CurveTooltip />} />
            <Area
              type="monotone"
              dataKey="equity_pct"
              stroke={equityEnd >= 0 ? '#00d48a' : '#f04f4f'}
              strokeWidth={1.5}
              fill="url(#eqGradV2)"
              dot={false}
              isAnimationActive={false}
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>

      {/* Drawdown chart */}
      <div>
        <div style={{ fontSize: 9, color: 'var(--dim)', ...MONO, marginBottom: 6, letterSpacing: '0.1em' }}>
          DRAWDOWN FROM PEAK (%)
        </div>
        <ResponsiveContainer width="100%" height={100}>
          <AreaChart data={curve} margin={{ top: 4, right: 8, bottom: 4, left: -10 }}>
            <defs>
              <linearGradient id="ddGrad" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%"  stopColor="#f04f4f" stopOpacity={0.3} />
                <stop offset="95%" stopColor="#f04f4f" stopOpacity={0.02} />
              </linearGradient>
            </defs>
            <CartesianGrid stroke="rgba(255,255,255,0.04)" vertical={false} />
            <XAxis dataKey="trade_n" hide />
            <YAxis
              tick={{ fontSize: 8, fill: 'var(--dim)', fontFamily: 'JetBrains Mono, monospace' }}
              tickFormatter={v => `${v.toFixed(0)}%`}
            />
            <ReferenceLine y={0} stroke="rgba(255,255,255,0.15)" />
            <Tooltip
              contentStyle={{ background: 'rgba(4,7,16,0.95)', border: '1px solid rgba(255,255,255,0.1)', fontSize: 10, ...MONO }}
              formatter={(v: number | undefined) => [`${(v ?? 0).toFixed(2)}%`, 'Drawdown']}
            />
            <Area
              type="monotone"
              dataKey="drawdown_pct"
              stroke="#f04f4f"
              strokeWidth={1}
              fill="url(#ddGrad)"
              dot={false}
              isAnimationActive={false}
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>

      {/* Per-regime + per-lane breakdown */}
      {summary && (summary.by_regime.length > 0 || summary.by_lane.length > 0) && (
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>

          {/* By regime */}
          {summary.by_regime.length > 0 && (
            <div>
              <div style={{ fontSize: 9, color: 'var(--dim)', ...MONO, marginBottom: 8, letterSpacing: '0.1em' }}>
                BY REGIME
              </div>
              {summary.by_regime.map(r => (
                <div key={r.regime} style={{
                  display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                  padding: '5px 0', borderBottom: '1px solid rgba(255,255,255,0.04)',
                }}>
                  <span style={{ fontSize: 10, color: 'var(--text)', ...MONO }}>{r.regime || 'UNKNOWN'}</span>
                  <div style={{ display: 'flex', gap: 12 }}>
                    <span style={{ fontSize: 9, color: 'var(--dim)', ...MONO }}>n={r.n}</span>
                    <span style={{ fontSize: 10, fontWeight: 600, color: winColor(r.win_rate), ...MONO }}>
                      {r.win_rate.toFixed(0)}%
                    </span>
                    <span style={{ fontSize: 10, color: retColor(r.avg_ret), ...MONO }}>
                      {fmtPct(r.avg_ret)}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          )}

          {/* By lane */}
          {summary.by_lane.length > 0 && (
            <div>
              <div style={{ fontSize: 9, color: 'var(--dim)', ...MONO, marginBottom: 8, letterSpacing: '0.1em' }}>
                BY LANE
              </div>
              {summary.by_lane.map(l => (
                <div key={l.lane} style={{
                  display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                  padding: '5px 0', borderBottom: '1px solid rgba(255,255,255,0.04)',
                }}>
                  <span style={{ fontSize: 10, color: 'var(--text)', ...MONO }}>{l.lane || 'unknown'}</span>
                  <div style={{ display: 'flex', gap: 12 }}>
                    <span style={{ fontSize: 9, color: 'var(--dim)', ...MONO }}>n={l.n}</span>
                    <span style={{ fontSize: 10, fontWeight: 600, color: winColor(l.win_rate), ...MONO }}>
                      {l.win_rate.toFixed(0)}%
                    </span>
                    <span style={{ fontSize: 10, color: retColor(l.avg_ret), ...MONO }}>
                      {fmtPct(l.avg_ret)}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
