/**
 * RegimeHeatmap — Daily regime score calendar heatmap + momentum panel.
 *
 * Calendar grid: each day cell colored by avg regime score (red→amber→green).
 * Hover shows: avg score, dominant label, alert count.
 *
 * Momentum panel: 48h regime trend sparkline + signal velocity bars.
 * Shows direction (rising / falling / flat) and rate of change.
 */
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../../api'
import {
  AreaChart, Area, BarChart, Bar, XAxis, YAxis, Tooltip,
  ResponsiveContainer, ReferenceLine, CartesianGrid,
} from 'recharts'

// ─── Types ────────────────────────────────────────────────────────────────────

interface DayRow {
  day: string
  avg_score: number | null
  min_score: number | null
  max_score: number | null
  snapshots: number
  dominant_label: string | null
  alerts: number
}

interface Momentum {
  slope_per_day: number
  direction: 'rising' | 'falling' | 'flat'
  current_avg: number | null
  week_avg: number
  days: number
}

interface HeatmapData {
  daily: DayRow[]
  momentum: Momentum | null
}

interface RegimeTrendPoint {
  ts_utc: string
  regime_score: number | null
  regime_label: string | null
  breadth_pct: number | null
  sol_change_24h: number | null
  volume_score: number | null
  liquidity_score: number | null
}

interface VelocityBucket {
  bucket: number
  bucket_ts: string
  total: number
  alerts: number
  watchlist: number
  avg_score: number | null
}

interface MomentumData {
  regime_trend: RegimeTrendPoint[]
  signal_velocity: VelocityBucket[]
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

const MONO: React.CSSProperties = { fontFamily: 'JetBrains Mono, monospace' }

function scoreToColor(score: number | null): string {
  if (score == null) return 'rgba(255,255,255,0.04)'
  if (score >= 70) return 'rgba(0,212,138,0.55)'
  if (score >= 55) return 'rgba(0,212,138,0.30)'
  if (score >= 40) return 'rgba(240,165,0,0.40)'
  if (score >= 25) return 'rgba(240,79,79,0.30)'
  return 'rgba(240,79,79,0.18)'
}

function scoreToBorder(score: number | null): string {
  if (score == null) return 'rgba(255,255,255,0.06)'
  if (score >= 70) return 'rgba(0,212,138,0.5)'
  if (score >= 55) return 'rgba(0,212,138,0.25)'
  if (score >= 40) return 'rgba(240,165,0,0.35)'
  if (score >= 25) return 'rgba(240,79,79,0.3)'
  return 'rgba(240,79,79,0.2)'
}

function regimeColor(score: number | null): string {
  if (score == null) return 'var(--muted)'
  if (score >= 60) return 'var(--green)'
  if (score >= 35) return 'var(--amber)'
  return 'var(--red)'
}

function fmtDay(d: string): string {
  try {
    const dt = new Date(d + 'T00:00:00Z')
    return dt.toLocaleDateString('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' })
  } catch { return d }
}

function fmtLabel(l: string | null): string {
  return (l || 'Unknown').replace(/_/g, ' ')
}

function directionIcon(d: Momentum['direction']): string {
  if (d === 'rising') return '↑'
  if (d === 'falling') return '↓'
  return '→'
}

function directionColor(d: Momentum['direction']): string {
  if (d === 'rising') return 'var(--green)'
  if (d === 'falling') return 'var(--red)'
  return 'var(--amber)'
}

function fmtTime(ts: string): string {
  try {
    const d = new Date(ts.endsWith('Z') ? ts : ts + 'Z')
    return d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', hour12: false, timeZone: 'UTC' })
  } catch { return ts }
}

// ─── Heatmap Day Cell ─────────────────────────────────────────────────────────

type TooltipState = { x: number; y: number; row: DayRow } | null

function HeatCell({
  row, onHover, isHovered,
}: { row: DayRow; onHover: (s: TooltipState) => void; isHovered: boolean }) {
  return (
    <div
      onMouseEnter={e => {
        const rect = e.currentTarget.getBoundingClientRect()
        onHover({ x: rect.left + rect.width / 2, y: rect.top, row })
      }}
      onMouseLeave={() => onHover(null)}
      style={{
        width: 28, height: 28, borderRadius: 4,
        background: scoreToColor(row.avg_score),
        border: `1px solid ${isHovered ? 'rgba(255,255,255,0.4)' : scoreToBorder(row.avg_score)}`,
        cursor: 'default',
        transition: 'border-color 0.1s, transform 0.1s',
        transform: isHovered ? 'scale(1.15)' : 'scale(1)',
        position: 'relative',
      }}
    >
      {/* Alert dot */}
      {row.alerts > 0 && (
        <div style={{
          position: 'absolute', top: 2, right: 2,
          width: 5, height: 5, borderRadius: '50%',
          background: 'var(--green)',
          boxShadow: '0 0 4px var(--green)',
        }} />
      )}
    </div>
  )
}

// ─── Legend ───────────────────────────────────────────────────────────────────

function HeatLegend() {
  const steps = [
    { label: '<25', color: 'rgba(240,79,79,0.18)' },
    { label: '25', color: 'rgba(240,79,79,0.30)' },
    { label: '40', color: 'rgba(240,165,0,0.40)' },
    { label: '55', color: 'rgba(0,212,138,0.30)' },
    { label: '70+', color: 'rgba(0,212,138,0.55)' },
  ]
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
      <span style={{ fontSize: 9, color: 'var(--dim)', ...MONO }}>score:</span>
      {steps.map(s => (
        <div key={s.label} style={{ display: 'flex', alignItems: 'center', gap: 3 }}>
          <div style={{
            width: 14, height: 14, borderRadius: 2,
            background: s.color, border: '1px solid rgba(255,255,255,0.1)',
          }} />
          <span style={{ fontSize: 8, color: 'var(--dim)', ...MONO }}>{s.label}</span>
        </div>
      ))}
      <div style={{ display: 'flex', alignItems: 'center', gap: 3, marginLeft: 8 }}>
        <div style={{
          width: 8, height: 8, borderRadius: '50%',
          background: 'var(--green)', boxShadow: '0 0 4px var(--green)',
        }} />
        <span style={{ fontSize: 8, color: 'var(--dim)', ...MONO }}>= alert fired</span>
      </div>
    </div>
  )
}

// ─── Momentum Panel ───────────────────────────────────────────────────────────

function MomentumPanel({ data }: { data: MomentumData }) {
  const trend = data.regime_trend.map(r => ({
    ts: r.ts_utc,
    score: r.regime_score,
    breadth: r.breadth_pct != null ? Math.round(r.breadth_pct * 100) : null,
  }))

  const velocity = data.signal_velocity

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>

      {/* 48h Regime Trend */}
      <div>
        <div style={{ fontSize: 9, fontWeight: 600, letterSpacing: '0.18em', color: 'var(--dim)', ...MONO, textTransform: 'uppercase', marginBottom: 10 }}>
          48H Regime Trend
        </div>
        {trend.length === 0 ? (
          <div style={{ color: 'var(--dim)', fontSize: 11, ...MONO, padding: '16px 0' }}>No data yet.</div>
        ) : (
          <ResponsiveContainer width="100%" height={140}>
            <AreaChart data={trend} margin={{ top: 4, right: 4, bottom: 0, left: -10 }}>
              <defs>
                <linearGradient id="regimeGrad" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#00d48a" stopOpacity={0.3} />
                  <stop offset="95%" stopColor="#00d48a" stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid stroke="var(--border)" strokeOpacity={0.3} vertical={false} />
              <XAxis
                dataKey="ts"
                tickFormatter={fmtTime}
                tick={{ fontSize: 8, fill: 'var(--dim)', fontFamily: 'JetBrains Mono, monospace' }}
                interval="preserveStartEnd"
              />
              <YAxis
                domain={[0, 100]}
                tick={{ fontSize: 9, fill: 'var(--dim)', fontFamily: 'JetBrains Mono, monospace' }}
                width={28}
              />
              <ReferenceLine y={60} stroke="rgba(0,212,138,0.2)" strokeDasharray="3 3" />
              <ReferenceLine y={35} stroke="rgba(240,79,79,0.2)" strokeDasharray="3 3" />
              <Tooltip
                contentStyle={{
                  background: 'var(--surface)', border: '1px solid var(--border)',
                  fontSize: 10, ...MONO,
                }}
                formatter={(v: number | undefined) => [v != null ? v.toFixed(1) : '—', 'Regime Score']}
                labelFormatter={(l: unknown) => {
                  try { return new Date(l as string).toLocaleString() } catch { return String(l) }
                }}
              />
              <Area
                type="monotone" dataKey="score"
                stroke="#00d48a" strokeWidth={1.5}
                fill="url(#regimeGrad)"
                dot={false} isAnimationActive={false}
              />
            </AreaChart>
          </ResponsiveContainer>
        )}
      </div>

      {/* Signal velocity */}
      <div>
        <div style={{ fontSize: 9, fontWeight: 600, letterSpacing: '0.18em', color: 'var(--dim)', ...MONO, textTransform: 'uppercase', marginBottom: 10 }}>
          Signal Velocity (6H Buckets)
        </div>
        {velocity.length === 0 ? (
          <div style={{ color: 'var(--dim)', fontSize: 11, ...MONO, padding: '8px 0' }}>No signals in last 48h.</div>
        ) : (
          <ResponsiveContainer width="100%" height={100}>
            <BarChart data={velocity} margin={{ top: 4, right: 4, bottom: 0, left: -10 }}>
              <XAxis
                dataKey="bucket_ts"
                tickFormatter={fmtTime}
                tick={{ fontSize: 8, fill: 'var(--dim)', fontFamily: 'JetBrains Mono, monospace' }}
                interval="preserveStartEnd"
              />
              <YAxis
                tick={{ fontSize: 9, fill: 'var(--dim)', fontFamily: 'JetBrains Mono, monospace' }}
                allowDecimals={false} width={20}
              />
              <Tooltip
                contentStyle={{
                  background: 'var(--surface)', border: '1px solid var(--border)',
                  fontSize: 10, ...MONO,
                }}
                formatter={(v: number | string | undefined) => [v != null ? String(v) : '—', '']}
                labelFormatter={(l: unknown) => {
                  try { return new Date(l as string + 'Z').toLocaleString() } catch { return String(l) }
                }}
              />
              <Bar dataKey="alerts" stackId="a" fill="rgba(0,212,138,0.7)" radius={[0,0,0,0]} name="alerts" />
              <Bar dataKey="watchlist" stackId="a" fill="rgba(77,159,255,0.5)" radius={[2,2,0,0]} name="watchlist" />
            </BarChart>
          </ResponsiveContainer>
        )}
      </div>
    </div>
  )
}

// ─── Main Component ───────────────────────────────────────────────────────────

export function RegimeHeatmap() {
  const [days, setDays] = useState(30)
  const [tooltip, setTooltip] = useState<{ x: number; y: number; row: DayRow } | null>(null)

  const { data: heatmap, isLoading: hmLoading } = useQuery<HeatmapData>({
    queryKey: ['regime-heatmap', days],
    queryFn: () => api.get(`/regime/heatmap?days=${days}`).then(r => r.data),
    staleTime: 120_000,
    refetchInterval: 300_000,
  })

  const { data: momentumData, isLoading: momLoading } = useQuery<MomentumData>({
    queryKey: ['regime-momentum'],
    queryFn: () => api.get('/regime/momentum').then(r => r.data),
    staleTime: 60_000,
    refetchInterval: 60_000,
  })

  const mom = heatmap?.momentum
  const daily = heatmap?.daily ?? []

  const btnStyle = (active: boolean): React.CSSProperties => ({
    padding: '3px 10px', fontSize: 11, cursor: 'pointer',
    background: active ? 'rgba(0,212,138,0.1)' : 'transparent',
    border: `1px solid ${active ? 'rgba(0,212,138,0.3)' : 'var(--border)'}`,
    color: active ? 'var(--green)' : 'var(--muted)',
    borderRadius: 4, ...MONO, transition: 'all 0.1s',
  })

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14, maxWidth: 1020, margin: '0 auto' }}>

      {/* ── Header ────────────────────────────────────────────────────── */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-end' }}>
        <div>
          <div style={{ fontSize: 18, fontWeight: 800, color: 'var(--text)', ...MONO, letterSpacing: '-0.01em' }}>
            Regime Heatmap
          </div>
          <div style={{ fontSize: 10, color: 'var(--dim)', ...MONO, marginTop: 3 }}>
            Market context · daily regime scores · momentum direction
          </div>
        </div>
        <div style={{ display: 'flex', gap: 4 }}>
          {[14, 30, 60].map(d => (
            <button key={d} style={btnStyle(days === d)} onClick={() => setDays(d)}>{d}d</button>
          ))}
        </div>
      </div>

      {/* ── Momentum strip ────────────────────────────────────────────── */}
      {mom && (
        <div style={{
          display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 10,
        }}>
          {[
            {
              label: 'Direction',
              value: `${directionIcon(mom.direction)} ${mom.direction.toUpperCase()}`,
              color: directionColor(mom.direction),
            },
            {
              label: 'Current Score',
              value: mom.current_avg != null ? mom.current_avg.toFixed(1) : '—',
              color: regimeColor(mom.current_avg),
            },
            {
              label: '7D Avg Score',
              value: mom.week_avg.toFixed(1),
              color: regimeColor(mom.week_avg),
            },
            {
              label: 'Slope / Day',
              value: `${mom.slope_per_day >= 0 ? '+' : ''}${mom.slope_per_day.toFixed(1)} pts`,
              color: mom.slope_per_day > 0.5 ? 'var(--green)' : mom.slope_per_day < -0.5 ? 'var(--red)' : 'var(--amber)',
            },
          ].map(kpi => (
            <div key={kpi.label} style={{
              background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 8, padding: '12px 16px',
            }}>
              <div style={{ fontSize: 9, color: 'var(--dim)', letterSpacing: '0.14em', ...MONO, textTransform: 'uppercase', marginBottom: 4 }}>
                {kpi.label}
              </div>
              <div style={{ fontSize: 18, fontWeight: 800, color: kpi.color, ...MONO, lineHeight: 1 }}>
                {kpi.value}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* ── Main grid: Heatmap + Momentum Panel ──────────────────────── */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 320px', gap: 14 }}>

        {/* Heatmap calendar */}
        <div style={{
          background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 10, padding: '18px 20px',
        }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 14 }}>
            <div style={{ fontSize: 9, fontWeight: 600, letterSpacing: '0.18em', color: 'var(--dim)', ...MONO, textTransform: 'uppercase' }}>
              Daily Regime Calendar
            </div>
            <HeatLegend />
          </div>

          {hmLoading ? (
            <div style={{ color: 'var(--dim)', fontSize: 11, ...MONO, padding: '32px 0', textAlign: 'center' }}>
              Loading…
            </div>
          ) : daily.length === 0 ? (
            <div style={{ color: 'var(--dim)', fontSize: 11, ...MONO, padding: '32px 0', textAlign: 'center' }}>
              No regime snapshot data yet.
            </div>
          ) : (
            <>
              {/* Calendar grid — wrap every 7 days */}
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                {daily.map(row => (
                  <div key={row.day} style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 2 }}>
                    <HeatCell
                      row={row}
                      onHover={s => setTooltip(s)}
                      isHovered={tooltip?.row.day === row.day}
                    />
                  </div>
                ))}
              </div>

              {/* Day labels below (first of every 7) */}
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, marginTop: 6 }}>
                {daily.map((row, i) => (
                  <div key={row.day} style={{ width: 28, textAlign: 'center' }}>
                    {i % 7 === 0 && (
                      <span style={{ fontSize: 7.5, color: 'var(--dim)', ...MONO }}>
                        {fmtDay(row.day)}
                      </span>
                    )}
                  </div>
                ))}
              </div>

              {/* Hover tooltip */}
              {tooltip && (
                <div style={{
                  position: 'fixed',
                  left: tooltip.x, top: tooltip.y - 120,
                  transform: 'translateX(-50%)',
                  zIndex: 999,
                  background: 'rgba(0,0,0,0.92)',
                  border: `1px solid ${scoreToBorder(tooltip.row.avg_score)}`,
                  backdropFilter: 'blur(12px)',
                  borderRadius: 8,
                  padding: '10px 14px',
                  minWidth: 160,
                  pointerEvents: 'none',
                }}>
                  <div style={{ fontSize: 10, fontWeight: 700, color: 'var(--text)', ...MONO, marginBottom: 6 }}>
                    {fmtDay(tooltip.row.day)}
                  </div>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 3, fontSize: 10, ...MONO }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', gap: 16 }}>
                      <span style={{ color: 'var(--dim)' }}>Avg Score</span>
                      <span style={{ color: regimeColor(tooltip.row.avg_score), fontWeight: 700 }}>
                        {tooltip.row.avg_score?.toFixed(1) ?? '—'}
                      </span>
                    </div>
                    <div style={{ display: 'flex', justifyContent: 'space-between', gap: 16 }}>
                      <span style={{ color: 'var(--dim)' }}>Range</span>
                      <span style={{ color: 'var(--muted)' }}>
                        {tooltip.row.min_score?.toFixed(0) ?? '—'} – {tooltip.row.max_score?.toFixed(0) ?? '—'}
                      </span>
                    </div>
                    <div style={{ display: 'flex', justifyContent: 'space-between', gap: 16 }}>
                      <span style={{ color: 'var(--dim)' }}>Label</span>
                      <span style={{ color: 'var(--text)' }}>{fmtLabel(tooltip.row.dominant_label)}</span>
                    </div>
                    <div style={{ display: 'flex', justifyContent: 'space-between', gap: 16 }}>
                      <span style={{ color: 'var(--dim)' }}>Alerts</span>
                      <span style={{ color: tooltip.row.alerts > 0 ? 'var(--green)' : 'var(--muted)' }}>
                        {tooltip.row.alerts}
                      </span>
                    </div>
                  </div>
                </div>
              )}
            </>
          )}
        </div>

        {/* Momentum Panel */}
        <div style={{
          background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 10, padding: '18px 20px',
        }}>
          <div style={{ fontSize: 9, fontWeight: 600, letterSpacing: '0.18em', color: 'var(--dim)', ...MONO, textTransform: 'uppercase', marginBottom: 14 }}>
            Momentum
          </div>
          {momLoading ? (
            <div style={{ color: 'var(--dim)', fontSize: 11, ...MONO, padding: '16px 0', textAlign: 'center' }}>
              Loading…
            </div>
          ) : momentumData ? (
            <MomentumPanel data={momentumData} />
          ) : (
            <div style={{ color: 'var(--dim)', fontSize: 11, ...MONO, padding: '16px 0', textAlign: 'center' }}>
              No momentum data yet.
            </div>
          )}
        </div>
      </div>

      {/* ── Score distribution bar across all days ─────────────────── */}
      {daily.length > 0 && (
        <div style={{
          background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 10, padding: '18px 20px',
        }}>
          <div style={{ fontSize: 9, fontWeight: 600, letterSpacing: '0.18em', color: 'var(--dim)', ...MONO, textTransform: 'uppercase', marginBottom: 14 }}>
            Daily Avg Score — {days}D Trend
          </div>
          <ResponsiveContainer width="100%" height={120}>
            <AreaChart data={daily} margin={{ top: 4, right: 4, bottom: 0, left: -10 }}>
              <defs>
                <linearGradient id="dailyGrad" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#00d48a" stopOpacity={0.3} />
                  <stop offset="95%" stopColor="#00d48a" stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid stroke="var(--border)" strokeOpacity={0.25} vertical={false} />
              <XAxis
                dataKey="day"
                tickFormatter={fmtDay}
                tick={{ fontSize: 8, fill: 'var(--dim)', fontFamily: 'JetBrains Mono, monospace' }}
                interval="preserveStartEnd"
              />
              <YAxis
                domain={[0, 100]}
                tick={{ fontSize: 8, fill: 'var(--dim)', fontFamily: 'JetBrains Mono, monospace' }}
                width={24}
              />
              <ReferenceLine y={60} stroke="rgba(0,212,138,0.2)" strokeDasharray="3 3" />
              <ReferenceLine y={35} stroke="rgba(240,79,79,0.2)" strokeDasharray="3 3" />
              <Tooltip
                contentStyle={{
                  background: 'var(--surface)', border: '1px solid var(--border)',
                  fontSize: 10, ...MONO,
                }}
                formatter={(v: number | undefined, name: unknown) => [
                  v != null ? v.toFixed(1) : '—',
                  name === 'avg_score' ? 'Avg Score' : String(name),
                ]}
                labelFormatter={(l: unknown) => fmtDay(String(l))}
              />
              <Area
                type="monotone" dataKey="avg_score"
                stroke="#00d48a" strokeWidth={1.5}
                fill="url(#dailyGrad)"
                dot={false} isAnimationActive={false}
              />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      )}

    </div>
  )
}
