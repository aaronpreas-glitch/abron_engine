/**
 * Brain — Self-Learning Analytics
 *
 * Visualises what the engine has learned from signal outcomes:
 *   • Queue status (how many outcomes are pending / complete)
 *   • Score vs Return scatter + bucketed avg line
 *   • Horizon decay (1h / 4h / 24h comparison)
 *   • Confidence tier calibration (A / B / C)
 *   • Regime conditional edge (win rate by regime)
 *   • Symbol edge table (best / worst coins)
 *   • Weekly drift (is the engine improving?)
 *   • Threshold suggestions from the optimizer
 */
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../../api'
import { TuningHistory } from './TuningHistory'
import { ThresholdSimulator } from './ThresholdSimulator'
import { SellSignalPanel } from './SellSignalPanel'
import { ExitLearnings } from './ExitLearnings'
import { EquityCurve } from './EquityCurve'
import {
  ScatterChart, Scatter, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, ReferenceLine, BarChart, Bar, Cell,
  LineChart, Line, Legend,
} from 'recharts'

// ─── Types ────────────────────────────────────────────────────────────────────

interface BrainStatus {
  total: number; pending: number; complete: number; last_evaluated_4h: string | null
}

interface ScatterPoint {
  score: number; ret: number; symbol: string; confidence: string; ts: string
}
interface ScoreBand {
  score_band: string; score_mid: number; n: number; avg_ret: number; win_rate: number
}
interface ScoreVsReturn { points: ScatterPoint[]; bands: ScoreBand[]; n: number }

interface RegimeEdge {
  regime_label: string; n: number; avg_ret: number; win_rate: number; best: number; worst: number
}

interface ConfidenceTier {
  confidence: string; n: number; avg_ret: number; win_rate: number
}

interface HorizonPoint { horizon: string; n: number; avg_ret: number; win_rate: number }

interface SymbolEdge {
  symbol: string; n: number; avg_ret: number; win_rate: number; best: number; worst: number
}

interface WeeklyDrift { week: string; n: number; avg_ret: number; win_rate: number }

interface SuggestReport {
  outcomes_4h_count: number
  current: { alert_threshold: number; regime_min_score: number; min_confidence_to_alert: string }
  recommended: { alert_threshold: number; regime_min_score: number; min_confidence_to_alert: string }
  reasons: string[]
  optimizer: { samples: number; avg_return_4h_pct: number; win_rate_4h_pct: number } | null
  avg_return_4h: number
  winrate_4h: number
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

const MONO: React.CSSProperties = { fontFamily: 'JetBrains Mono, monospace' }

function retColor(v: number) {
  return v > 0 ? 'var(--green)' : v < 0 ? 'var(--red)' : 'var(--muted)'
}

function fmtPct(v: number | null | undefined, plus = true) {
  if (v == null) return '—'
  const s = plus && v > 0 ? '+' : ''
  return `${s}${v.toFixed(2)}%`
}

function winColor(wr: number) {
  return wr >= 55 ? 'var(--green)' : wr >= 45 ? 'var(--amber)' : 'var(--red)'
}

// ─── Primitives ───────────────────────────────────────────────────────────────

function Card({ children, style }: { children: React.ReactNode; style?: React.CSSProperties }) {
  return (
    <div style={{
      background: 'var(--surface)', border: '1px solid var(--border)',
      borderRadius: 10, padding: '18px 20px', ...style,
    }}>
      {children}
    </div>
  )
}

function SectionTitle({ children }: { children: React.ReactNode }) {
  return (
    <div style={{
      fontSize: 9, fontWeight: 600, letterSpacing: '0.18em',
      color: 'var(--dim)', ...MONO, textTransform: 'uppercase', marginBottom: 14,
    }}>
      {children}
    </div>
  )
}

function Stat({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
      <span style={{ fontSize: 9, color: 'var(--dim)', letterSpacing: '0.14em', ...MONO, textTransform: 'uppercase' }}>
        {label}
      </span>
      <span style={{ fontSize: 20, fontWeight: 800, color: color || 'var(--text)', lineHeight: 1, ...MONO }}>
        {value}
      </span>
    </div>
  )
}

function EmptyState({ message }: { message: string }) {
  return (
    <div style={{
      padding: '40px 0', textAlign: 'center',
      color: 'var(--dim)', fontSize: 12, ...MONO,
    }}>
      {message}
    </div>
  )
}

function LookbackTabs({
  value, onChange,
}: { value: number; onChange: (v: number) => void }) {
  return (
    <div style={{ display: 'flex', gap: 4 }}>
      {[7, 14, 30, 60, 90].map(d => (
        <button
          key={d}
          onClick={() => onChange(d)}
          style={{
            padding: '3px 10px', borderRadius: 4, border: '1px solid var(--border)',
            background: value === d ? 'var(--green)' : 'transparent',
            color: value === d ? '#000' : 'var(--muted)',
            fontSize: 10, fontWeight: value === d ? 700 : 400,
            cursor: 'pointer', ...MONO,
            transition: 'all 0.1s',
          }}
        >
          {d}d
        </button>
      ))}
    </div>
  )
}

function HorizonTabs({
  value, onChange,
}: { value: number; onChange: (v: number) => void }) {
  return (
    <div style={{ display: 'flex', gap: 4 }}>
      {([1, 4, 24] as const).map(h => (
        <button
          key={h}
          onClick={() => onChange(h)}
          style={{
            padding: '3px 10px', borderRadius: 4, border: '1px solid var(--border)',
            background: value === h ? 'rgba(77,159,255,0.15)' : 'transparent',
            color: value === h ? 'var(--blue)' : 'var(--muted)',
            fontSize: 10, fontWeight: value === h ? 700 : 400,
            cursor: 'pointer', ...MONO,
            transition: 'all 0.1s',
          }}
        >
          {h}h
        </button>
      ))}
    </div>
  )
}

// ─── Sections ─────────────────────────────────────────────────────────────────

function QueueStatus({ lookback }: { lookback: number }) {
  const { data } = useQuery<BrainStatus>({
    queryKey: ['brain-status'],
    queryFn: () => api.get('/brain/status').then(r => r.data),
    refetchInterval: 60_000,
  })
  const { data: suggest } = useQuery<SuggestReport>({
    queryKey: ['brain-suggest', lookback],
    queryFn: () => api.get(`/brain/suggest?lookback_days=${lookback}`).then(r => r.data),
    refetchInterval: 300_000,
  })

  const pctComplete = data?.total
    ? Math.round((data.complete / data.total) * 100)
    : 0

  return (
    <Card>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 24 }}>
        {/* Queue metrics */}
        <div style={{ flex: 1 }}>
          <SectionTitle>Outcome Queue</SectionTitle>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, auto)', gap: '0 32px' }}>
            <Stat label="Total Tracked" value={data?.total?.toString() ?? '—'} />
            <Stat label="Complete"
              value={data?.complete?.toString() ?? '—'}
              color="var(--green)"
            />
            <Stat label="Pending"
              value={data?.pending?.toString() ?? '—'}
              color={data?.pending ? 'var(--amber)' : 'var(--muted)'}
            />
            <Stat label="Fill Rate"
              value={data?.total ? `${pctComplete}%` : '—'}
              color={pctComplete >= 80 ? 'var(--green)' : pctComplete >= 40 ? 'var(--amber)' : 'var(--red)'}
            />
          </div>
          {data?.last_evaluated_4h && (
            <div style={{ marginTop: 8, fontSize: 10, color: 'var(--dim)', ...MONO }}>
              Last 4h eval: {new Date(data.last_evaluated_4h + 'Z').toLocaleString()}
            </div>
          )}
          {/* Progress bar */}
          <div style={{ marginTop: 12, height: 3, background: 'var(--border)', borderRadius: 2, overflow: 'hidden' }}>
            <div style={{
              height: '100%', width: `${pctComplete}%`, borderRadius: 2,
              background: pctComplete >= 80 ? 'var(--green)' : pctComplete >= 40 ? 'var(--amber)' : 'var(--red)',
              transition: 'width 0.5s ease',
            }} />
          </div>
        </div>

        {/* Suggestion pill */}
        {suggest && suggest.outcomes_4h_count >= 3 && (
          <div style={{
            background: 'rgba(0,212,138,0.06)', border: '1px solid rgba(0,212,138,0.2)',
            borderRadius: 8, padding: '12px 16px', minWidth: 280,
          }}>
            <div style={{ fontSize: 9, color: 'var(--green)', letterSpacing: '0.15em', ...MONO, marginBottom: 10 }}>
              ⚡ OPTIMIZER SUGGESTION
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '6px 20px' }}>
              {[
                { label: 'Alert Threshold', cur: suggest.current.alert_threshold, rec: suggest.recommended.alert_threshold },
                { label: 'Regime Min', cur: suggest.current.regime_min_score, rec: suggest.recommended.regime_min_score },
                { label: 'Min Confidence', cur: suggest.current.min_confidence_to_alert, rec: suggest.recommended.min_confidence_to_alert },
              ].map(({ label, cur, rec }) => (
                <div key={label}>
                  <div style={{ fontSize: 9, color: 'var(--dim)', ...MONO, letterSpacing: '0.1em' }}>{label}</div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 2 }}>
                    <span style={{ fontSize: 12, color: 'var(--muted)', ...MONO }}>{cur}</span>
                    <span style={{ fontSize: 10, color: 'var(--dim)' }}>→</span>
                    <span style={{
                      fontSize: 13, fontWeight: 700, ...MONO,
                      color: String(rec) !== String(cur) ? 'var(--green)' : 'var(--muted)',
                    }}>
                      {rec}
                    </span>
                    {String(rec) !== String(cur) && (
                      <span style={{ fontSize: 9, color: 'var(--green)', ...MONO }}>CHANGE</span>
                    )}
                  </div>
                </div>
              ))}
            </div>
            <div style={{ marginTop: 10, fontSize: 9.5, color: 'var(--muted)', ...MONO, lineHeight: 1.5 }}>
              Based on {suggest.outcomes_4h_count} outcomes · {fmtPct(suggest.avg_return_4h)} avg 4h · {suggest.winrate_4h.toFixed(0)}% win
            </div>
            {suggest.reasons.slice(0, 1).map((r, i) => (
              <div key={i} style={{ marginTop: 6, fontSize: 9, color: 'var(--dim)', ...MONO, lineHeight: 1.4 }}>
                {r}
              </div>
            ))}
          </div>
        )}
      </div>
    </Card>
  )
}

function ScoreVsReturnChart({ lookback, horizon }: { lookback: number; horizon: number }) {
  const { data, isLoading } = useQuery<ScoreVsReturn>({
    queryKey: ['brain-svr', lookback, horizon],
    queryFn: () => api.get(`/brain/score-vs-return?lookback_days=${lookback}&horizon=${horizon}`).then(r => r.data),
    refetchInterval: 300_000,
  })

  if (isLoading) return <div style={{ height: 260, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--dim)', fontSize: 11 }}>Loading…</div>

  const points = data?.points ?? []
  const bands  = data?.bands  ?? []

  if (points.length === 0) return <EmptyState message="No outcome data yet. Tracker will fill this as signals age past 1h / 4h / 24h." />

  const winPoints  = points.filter(p => p.ret >= 0)
  const lossPoints = points.filter(p => p.ret < 0)

  return (
    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
      {/* Scatter */}
      <div>
        <div style={{ fontSize: 10, color: 'var(--dim)', ...MONO, marginBottom: 8 }}>
          Individual signals ({data?.n} pts)
        </div>
        <ResponsiveContainer width="100%" height={220}>
          <ScatterChart margin={{ top: 4, right: 4, bottom: 4, left: -10 }}>
            <CartesianGrid stroke="var(--border)" strokeOpacity={0.4} />
            <XAxis
              type="number" dataKey="score" name="Score"
              domain={[55, 100]} tickCount={6}
              tick={{ fontSize: 9, fill: "var(--dim)", fontFamily: "JetBrains Mono, monospace" }}
              label={{ value: 'Score', position: 'insideBottomRight', offset: -4, fontSize: 9, fill: 'var(--dim)' }}
            />
            <YAxis
              type="number" dataKey="ret" name="Return"
              tick={{ fontSize: 9, fill: "var(--dim)", fontFamily: "JetBrains Mono, monospace" }}
              tickFormatter={v => `${v > 0 ? '+' : ''}${v.toFixed(0)}%`}
            />
            <ReferenceLine y={0} stroke="rgba(255,255,255,0.2)" strokeDasharray="4 3" />
            <Tooltip
              cursor={{ strokeDasharray: '3 3' }}
              contentStyle={{ background: 'var(--surface)', border: '1px solid var(--border)', fontSize: 10, ...MONO }}
              formatter={(v: number | undefined, name: string | undefined) => [
                name === 'ret' ? `${(v ?? 0) > 0 ? '+' : ''}${(v ?? 0).toFixed(2)}%` : (v ?? 0).toFixed(0),
                name === 'ret' ? 'Return' : 'Score',
              ]}
            />
            <Scatter name="Win" data={winPoints}  fill="var(--green)" opacity={0.65} />
            <Scatter name="Loss" data={lossPoints} fill="var(--red)"   opacity={0.55} />
          </ScatterChart>
        </ResponsiveContainer>
      </div>

      {/* Bucketed bar */}
      <div>
        <div style={{ fontSize: 10, color: 'var(--dim)', ...MONO, marginBottom: 8 }}>
          Avg return by score band
        </div>
        <ResponsiveContainer width="100%" height={220}>
          <BarChart data={bands} margin={{ top: 4, right: 4, bottom: 4, left: -10 }}>
            <CartesianGrid stroke="var(--border)" strokeOpacity={0.4} vertical={false} />
            <XAxis dataKey="score_band" tick={{ fontSize: 8, fill: "var(--dim)", fontFamily: "JetBrains Mono, monospace" }} />
            <YAxis tick={{ fontSize: 9, fill: "var(--dim)", fontFamily: "JetBrains Mono, monospace" }} tickFormatter={v => `${v}%`} />
            <ReferenceLine y={0} stroke="rgba(255,255,255,0.25)" />
            <Tooltip
              contentStyle={{ background: 'var(--surface)', border: '1px solid var(--border)', fontSize: 10, ...MONO }}
              formatter={(v: number | undefined, name: string | undefined) => [
                name === 'avg_ret' ? `${(v ?? 0) > 0 ? '+' : ''}${(v ?? 0).toFixed(2)}%` : `${(v ?? 0).toFixed(0)}%`,
                name === 'avg_ret' ? 'Avg Return' : 'Win Rate',
              ]}
            />
            <Bar dataKey="avg_ret" radius={[3,3,0,0]}>
              {bands.map((b, i) => (
                <Cell key={i} fill={b.avg_ret >= 0 ? '#00d48a' : '#f04f4f'} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px 16px', marginTop: 8 }}>
          {bands.map(b => (
            <span key={b.score_band} style={{ fontSize: 9, color: 'var(--dim)', ...MONO }}>
              {b.score_band}: <span style={{ color: winColor(b.win_rate) }}>{b.win_rate.toFixed(0)}%</span> WR ({b.n})
            </span>
          ))}
        </div>
      </div>
    </div>
  )
}

function HorizonDecay({ lookback }: { lookback: number }) {
  const { data } = useQuery<HorizonPoint[]>({
    queryKey: ['brain-decay', lookback],
    queryFn: () => api.get(`/brain/horizon-decay?lookback_days=${lookback}`).then(r => r.data),
    refetchInterval: 300_000,
  })
  const rows = data ?? []
  const hasData = rows.some(r => r.n > 0)

  return (
    <div>
      <SectionTitle>Horizon Decay — Which Hold Window Wins?</SectionTitle>
      {!hasData ? (
        <EmptyState message="No evaluated outcomes yet." />
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 12, marginBottom: 16 }}>
          {rows.map(r => (
            <div key={r.horizon} style={{
              background: 'var(--bg)', border: '1px solid var(--border)',
              borderRadius: 8, padding: '14px 16px',
            }}>
              <div style={{ fontSize: 9, color: 'var(--dim)', letterSpacing: '0.16em', ...MONO, marginBottom: 8 }}>
                {r.horizon.toUpperCase()} HORIZON
              </div>
              <div style={{ fontSize: 22, fontWeight: 800, color: retColor(r.avg_ret), ...MONO, lineHeight: 1 }}>
                {fmtPct(r.avg_ret)}
              </div>
              <div style={{ marginTop: 6, fontSize: 11, color: winColor(r.win_rate), ...MONO }}>
                {r.win_rate.toFixed(0)}% win rate
              </div>
              <div style={{ marginTop: 4, height: 3, background: 'var(--border)', borderRadius: 2, overflow: 'hidden' }}>
                <div style={{ height: '100%', width: `${Math.min(100, r.win_rate)}%`, background: winColor(r.win_rate), borderRadius: 2 }} />
              </div>
              <div style={{ marginTop: 5, fontSize: 9, color: 'var(--dim)', ...MONO }}>{r.n} evals</div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function ConfidenceCalibration({ lookback, horizon }: { lookback: number; horizon: number }) {
  const { data } = useQuery<ConfidenceTier[]>({
    queryKey: ['brain-conf', lookback, horizon],
    queryFn: () => api.get(`/brain/confidence-calibration?lookback_days=${lookback}&horizon=${horizon}`).then(r => r.data),
    refetchInterval: 300_000,
  })
  const rows = (data ?? []).sort((a,b) => b.avg_ret - a.avg_ret)
  const hasData = rows.some(r => r.n > 0)

  const tierColor = (t: string) => t === 'A' ? 'var(--green)' : t === 'B' ? 'var(--amber)' : 'var(--muted)'

  return (
    <div>
      <SectionTitle>Confidence Tier Calibration</SectionTitle>
      {!hasData ? (
        <EmptyState message="No data yet." />
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          {rows.map(r => (
            <div key={r.confidence} style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
              <div style={{
                width: 32, height: 32, borderRadius: 6, display: 'flex', alignItems: 'center', justifyContent: 'center',
                background: `${tierColor(r.confidence)}18`, border: `1px solid ${tierColor(r.confidence)}40`,
                fontSize: 15, fontWeight: 800, color: tierColor(r.confidence), ...MONO, flexShrink: 0,
              }}>
                {r.confidence}
              </div>
              <div style={{ flex: 1 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 5 }}>
                  <span style={{ fontSize: 12, fontWeight: 700, color: retColor(r.avg_ret), ...MONO }}>
                    {fmtPct(r.avg_ret)} avg
                  </span>
                  <span style={{ fontSize: 11, color: winColor(r.win_rate), ...MONO }}>
                    {r.win_rate.toFixed(0)}% WR · {r.n} evals
                  </span>
                </div>
                <div style={{ height: 6, background: 'var(--border)', borderRadius: 3, overflow: 'hidden' }}>
                  <div style={{
                    height: '100%', width: `${Math.min(100, r.win_rate)}%`,
                    background: winColor(r.win_rate), borderRadius: 3,
                    transition: 'width 0.4s ease',
                  }} />
                </div>
              </div>
            </div>
          ))}
          {/* Calibration note */}
          {rows.length >= 2 && (() => {
            const sorted = [...rows].sort((a,b) => {
              const o: Record<string,number> = { A: 3, B: 2, C: 1 }
              return (o[b.confidence] ?? 0) - (o[a.confidence] ?? 0)
            })
            const a = sorted.find(r => r.confidence === 'A')
            const b = sorted.find(r => r.confidence === 'B')
            if (a && b && b.avg_ret > a.avg_ret) {
              return (
                <div style={{
                  marginTop: 6, padding: '8px 12px', borderRadius: 6,
                  background: 'rgba(255,179,71,0.08)', border: '1px solid rgba(255,179,71,0.2)',
                  fontSize: 10, color: 'var(--amber)', ...MONO,
                }}>
                  ⚠ B-tier is outperforming A-tier. Consider reviewing CONFIDENCE_MIN_A threshold.
                </div>
              )
            }
            return null
          })()}
        </div>
      )}
    </div>
  )
}

function RegimeEdgeChart({ lookback, horizon }: { lookback: number; horizon: number }) {
  const { data } = useQuery<RegimeEdge[]>({
    queryKey: ['brain-regime', lookback, horizon],
    queryFn: () => api.get(`/brain/regime-edge?lookback_days=${lookback}&horizon=${horizon}`).then(r => r.data),
    refetchInterval: 300_000,
  })
  const rows = data ?? []
  const hasData = rows.some(r => r.n > 0)

  const labelColor = (l: string) => {
    const u = (l || '').toUpperCase()
    if (u.includes('RISK_ON') || u.includes('BULL')) return 'var(--green)'
    if (u.includes('RISK_OFF') || u.includes('BEAR')) return 'var(--red)'
    return 'var(--amber)'
  }

  return (
    <div>
      <SectionTitle>Regime Conditional Edge</SectionTitle>
      {!hasData ? (
        <EmptyState message="No regime data yet." />
      ) : (
        <>
          <ResponsiveContainer width="100%" height={160}>
            <BarChart data={rows} margin={{ top: 4, right: 4, bottom: 0, left: -10 }}>
              <CartesianGrid stroke="var(--border)" strokeOpacity={0.4} vertical={false} />
              <XAxis dataKey="regime_label" tick={{ fontSize: 9, fill: "var(--dim)", fontFamily: "JetBrains Mono, monospace" }} />
              <YAxis tick={{ fontSize: 9, fill: "var(--dim)", fontFamily: "JetBrains Mono, monospace" }} tickFormatter={v => `${v}%`} />
              <ReferenceLine y={0} stroke="rgba(255,255,255,0.25)" />
              <Tooltip
                contentStyle={{ background: 'var(--surface)', border: '1px solid var(--border)', fontSize: 10, ...MONO }}
                formatter={(v: number | undefined, name: string | undefined) => [
                  name === 'avg_ret' ? `${(v ?? 0) > 0 ? '+' : ''}${(v ?? 0).toFixed(2)}%` : `${(v ?? 0).toFixed(0)}%`,
                  name === 'avg_ret' ? 'Avg Return' : 'Win Rate',
                ]}
              />
              <Bar dataKey="avg_ret" radius={[3,3,0,0]} name="avg_ret">
                {rows.map((r, i) => (
                  <Cell key={i} fill={r.avg_ret >= 0 ? '#00d48a' : '#f04f4f'} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px 20px', marginTop: 10 }}>
            {rows.map(r => (
              <div key={r.regime_label} style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <span style={{ fontSize: 10, fontWeight: 600, color: labelColor(r.regime_label), ...MONO }}>
                  {r.regime_label}
                </span>
                <span style={{ fontSize: 9, color: 'var(--dim)', ...MONO }}>
                  {r.win_rate.toFixed(0)}% WR · {r.n}
                </span>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  )
}

function SymbolEdgeTable({ lookback, horizon }: { lookback: number; horizon: number }) {
  const { data } = useQuery<SymbolEdge[]>({
    queryKey: ['brain-symbol', lookback, horizon],
    queryFn: () => api.get(`/brain/symbol-edge?lookback_days=${lookback}&horizon=${horizon}&min_signals=2`).then(r => r.data),
    refetchInterval: 300_000,
  })
  const rows = data ?? []

  return (
    <div>
      <SectionTitle>Symbol Edge (≥2 signals)</SectionTitle>
      {rows.length === 0 ? (
        <EmptyState message="Need at least 2 outcomes per symbol." />
      ) : (
        <>
          {/* Header */}
          <div style={{
            display: 'grid', gridTemplateColumns: '1fr 36px 68px 68px 52px 52px',
            gap: 8, paddingBottom: 6, borderBottom: '1px solid var(--border)', marginBottom: 4,
          }}>
            {['SYMBOL', 'N', 'AVG RET', 'WIN RATE', 'BEST', 'WORST'].map(h => (
              <span key={h} style={{ fontSize: 8, color: 'var(--dim)', ...MONO, letterSpacing: '0.14em', textAlign: h === 'SYMBOL' ? 'left' : 'right' }}>{h}</span>
            ))}
          </div>
          {rows.slice(0, 12).map((r, i) => (
            <div key={r.symbol} style={{
              display: 'grid', gridTemplateColumns: '1fr 36px 68px 68px 52px 52px',
              gap: 8, padding: '6px 0', borderBottom: '1px solid rgba(255,255,255,0.04)',
              alignItems: 'center',
            }}>
              <span style={{ fontSize: 11.5, fontWeight: 700, color: i < 3 ? 'var(--green)' : 'var(--text)', ...MONO }}>
                ${r.symbol}
              </span>
              <span style={{ fontSize: 10, color: 'var(--dim)', ...MONO, textAlign: 'right' }}>{r.n}</span>
              <span style={{ fontSize: 11.5, fontWeight: 600, color: retColor(r.avg_ret), ...MONO, textAlign: 'right' }}>
                {fmtPct(r.avg_ret)}
              </span>
              <div style={{ textAlign: 'right' }}>
                <span style={{ fontSize: 11.5, fontWeight: 600, color: winColor(r.win_rate), ...MONO }}>
                  {r.win_rate.toFixed(0)}%
                </span>
              </div>
              <span style={{ fontSize: 10, color: 'var(--green)', ...MONO, textAlign: 'right' }}>
                {fmtPct(r.best)}
              </span>
              <span style={{ fontSize: 10, color: 'var(--red)', ...MONO, textAlign: 'right' }}>
                {fmtPct(r.worst)}
              </span>
            </div>
          ))}
        </>
      )}
    </div>
  )
}

function WeeklyDriftChart({ }: {}) {
  const { data } = useQuery<WeeklyDrift[]>({
    queryKey: ['brain-drift'],
    queryFn: () => api.get('/brain/weekly-drift').then(r => r.data),
    refetchInterval: 600_000,
  })
  const rows = data ?? []

  return (
    <div>
      <SectionTitle>Weekly Drift — Is the Engine Improving?</SectionTitle>
      {rows.length === 0 ? (
        <EmptyState message="Need multiple weeks of outcome data." />
      ) : (
        <ResponsiveContainer width="100%" height={180}>
          <LineChart data={rows} margin={{ top: 4, right: 16, bottom: 0, left: -10 }}>
            <CartesianGrid stroke="var(--border)" strokeOpacity={0.4} vertical={false} />
            <XAxis dataKey="week" tick={{ fontSize: 8, fill: "var(--dim)", fontFamily: "JetBrains Mono, monospace" }} />
            <YAxis tick={{ fontSize: 9, fill: "var(--dim)", fontFamily: "JetBrains Mono, monospace" }} />
            <ReferenceLine y={0} stroke="rgba(255,255,255,0.25)" strokeDasharray="4 3" />
            <Tooltip
              contentStyle={{ background: 'var(--surface)', border: '1px solid var(--border)', fontSize: 10, ...MONO }}
              formatter={(v: number | undefined, name: string | undefined) => [
                name === 'avg_ret' ? `${(v ?? 0) > 0 ? '+' : ''}${(v ?? 0).toFixed(2)}%` : `${(v ?? 0).toFixed(0)}%`,
                name === 'avg_ret' ? 'Avg Return' : 'Win Rate',
              ]}
            />
            <Legend wrapperStyle={{ fontSize: 9, ...MONO }} />
            <Line type="monotone" dataKey="avg_ret" stroke="#00d48a" strokeWidth={2} dot={{ r: 3 }} name="avg_ret" />
            <Line type="monotone" dataKey="win_rate" stroke="#4d9fff" strokeWidth={2} dot={{ r: 3 }} name="win_rate" />
          </LineChart>
        </ResponsiveContainer>
      )}
    </div>
  )
}

// ─── Lane Win Rates ───────────────────────────────────────────────────────────

interface LaneRow  { lane: string;   count: number; win_rate_4h: number; avg_return_4h: number }
interface SourceRow { source: string; count: number; win_rate_4h: number; avg_return_4h: number }
interface LaneWinRatesData {
  lanes: LaneRow[]
  by_source: SourceRow[]
  total_tagged: number
  total_outcomes: number
  lookback_days: number
  error?: string
}

function LaneWinRates({ lookback }: { lookback: number }) {
  const { data, isLoading } = useQuery<LaneWinRatesData>({
    queryKey: ['brain-lane-win-rates', lookback],
    queryFn: () => api.get(`/brain/lane-win-rates?lookback_days=${lookback}`).then(r => r.data),
    staleTime: 60_000,
    refetchInterval: 120_000,
  })

  if (isLoading) return (
    <div style={{ padding: 24, textAlign: 'center', color: 'var(--muted)', fontSize: 11, ...MONO }}>
      Loading lane win rates...
    </div>
  )

  const lanes = data?.lanes ?? []
  const sources = data?.by_source ?? []

  if (lanes.length === 0 && sources.length === 0) return (
    <div style={{ padding: 24, textAlign: 'center', color: 'var(--muted)', fontSize: 11, ...MONO }}>
      No lane data yet — outcomes are tagged with lane + source at alert time.<br />
      Data will appear after the first alerts fire with lane tracking enabled.
    </div>
  )

  const laneColor = (wr: number) => wr >= 55 ? 'var(--green)' : wr >= 45 ? 'var(--amber)' : 'var(--red)'

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10 }}>
        <span style={{ fontSize: 13, fontWeight: 700, color: 'var(--text)', ...MONO }}>Lane Win Rates</span>
        <span style={{ fontSize: 10, color: 'var(--muted)', ...MONO }}>
          {data?.total_outcomes} outcomes · {lookback}d · {data?.total_tagged} lane-tagged
        </span>
      </div>

      {/* Lanes */}
      {lanes.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          {lanes.map(row => {
            const wrPct = row.win_rate_4h
            const barW = Math.min(100, wrPct)
            return (
              <div key={row.lane} style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                <span style={{ ...MONO, fontSize: 10, color: 'var(--dim)', width: 90, flexShrink: 0 }}>
                  {row.lane}
                </span>
                {/* Bar */}
                <div style={{ flex: 1, height: 6, background: 'rgba(255,255,255,0.06)', borderRadius: 3, overflow: 'hidden' }}>
                  <div style={{
                    width: `${barW}%`,
                    height: '100%',
                    background: laneColor(wrPct),
                    borderRadius: 3,
                    transition: 'width 0.4s ease',
                  }} />
                </div>
                <span style={{ ...MONO, fontSize: 11, fontWeight: 700, color: laneColor(wrPct), width: 44, textAlign: 'right' }}>
                  {wrPct.toFixed(1)}%
                </span>
                <span style={{ ...MONO, fontSize: 10, color: 'var(--dim)', width: 54, textAlign: 'right' }}>
                  {row.avg_return_4h > 0 ? '+' : ''}{row.avg_return_4h.toFixed(1)}% avg
                </span>
                <span style={{ ...MONO, fontSize: 9, color: 'rgba(255,255,255,0.2)', width: 36, textAlign: 'right' }}>
                  n={row.count}
                </span>
              </div>
            )
          })}
        </div>
      )}

      {/* By source */}
      {sources.length > 0 && (
        <>
          <div style={{ height: 1, background: 'rgba(255,255,255,0.06)' }} />
          <span style={{ fontSize: 10, fontWeight: 600, color: 'var(--muted)', ...MONO, letterSpacing: '0.1em' }}>BY SOURCE</span>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            {sources.map(row => (
              <div key={row.source} className="card" style={{ padding: '8px 12px', minWidth: 110 }}>
                <div style={{ ...MONO, fontSize: 9, color: 'var(--muted)', letterSpacing: '0.08em', marginBottom: 4 }}>
                  {row.source.replace(/_/g, ' ').toUpperCase()}
                </div>
                <div style={{ ...MONO, fontSize: 14, fontWeight: 800, color: laneColor(row.win_rate_4h) }}>
                  {row.win_rate_4h.toFixed(1)}%
                </div>
                <div style={{ ...MONO, fontSize: 9, color: 'var(--dim)', marginTop: 2 }}>
                  {row.avg_return_4h > 0 ? '+' : ''}{row.avg_return_4h.toFixed(1)}% avg · n={row.count}
                </div>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  )
}

// ─── Main Page ────────────────────────────────────────────────────────────────

const BRAIN_TABS = [
  { id: 'overview',   label: 'Overview'   },
  { id: 'score',      label: 'Score vs Return' },
  { id: 'horizons',   label: 'Horizons'   },
  { id: 'regime',     label: 'Regime'     },
  { id: 'symbols',    label: 'Symbols'    },
  { id: 'tuning',     label: 'Tuning'     },
] as const

type BrainTab = typeof BRAIN_TABS[number]['id']

export function Brain() {
  const [lookback, setLookback] = useState(30)
  const [horizon,  setHorizon]  = useState(4)
  const [activeTab, setActiveTab] = useState<BrainTab>('overview')

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14, maxWidth: 1200, margin: '0 auto' }}>

      {/* ── Header + controls ─────────────────────────────────────────── */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-end' }}>
        <div>
          <div style={{ fontSize: 18, fontWeight: 800, color: 'var(--text)', ...MONO, letterSpacing: '-0.01em' }}>
            Engine Brain
          </div>
          <div style={{ fontSize: 10, color: 'var(--dim)', ...MONO, marginTop: 3 }}>
            Self-learning analytics · {lookback}d of signal outcomes
          </div>
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6, alignItems: 'flex-end' }}>
          <LookbackTabs value={lookback} onChange={setLookback} />
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span style={{ fontSize: 9, color: 'var(--dim)', ...MONO }}>HORIZON:</span>
            <HorizonTabs value={horizon} onChange={setHorizon} />
          </div>
        </div>
      </div>

      {/* ── Sticky tab bar ────────────────────────────────────────────── */}
      <div style={{
        position: 'sticky', top: 0, zIndex: 10,
        background: 'rgba(4, 6, 10, 0.80)',
        backdropFilter: 'blur(20px) saturate(160%)',
        WebkitBackdropFilter: 'blur(20px) saturate(160%)',
        borderRadius: 10,
        border: '1px solid rgba(255,255,255,0.07)',
        padding: '5px 6px',
        display: 'flex', gap: 4,
      }}>
        {BRAIN_TABS.map(tab => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            style={{
              flex: 1,
              padding: '7px 10px',
              borderRadius: 7,
              border: 'none',
              background: activeTab === tab.id
                ? 'rgba(255,255,255,0.09)'
                : 'transparent',
              color: activeTab === tab.id ? 'var(--text)' : 'var(--dim)',
              fontSize: 11,
              fontWeight: activeTab === tab.id ? 600 : 400,
              cursor: 'pointer',
              ...MONO,
              letterSpacing: '0.03em',
              transition: 'all 0.15s',
              boxShadow: activeTab === tab.id
                ? 'inset 0 1px 0 rgba(255,255,255,0.08)'
                : 'none',
              whiteSpace: 'nowrap',
            }}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* ── Tab content ───────────────────────────────────────────────── */}

      {activeTab === 'overview' && (
        <>
          <QueueStatus lookback={lookback} />
          <Card>
            <ThresholdSimulator lookback={lookback} horizon={horizon} />
          </Card>
          <Card>
            <ExitLearnings />
          </Card>
          <Card>
            <TuningHistory />
          </Card>
        </>
      )}

      {activeTab === 'score' && (
        <Card>
          <SectionTitle>Score vs Return — Does a Higher Score Actually Mean Better Outcomes?</SectionTitle>
          <ScoreVsReturnChart lookback={lookback} horizon={horizon} />
        </Card>
      )}

      {activeTab === 'horizons' && (
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 300px', gap: 14 }}>
          <Card>
            <HorizonDecay lookback={lookback} />
          </Card>
          <Card>
            <ConfidenceCalibration lookback={lookback} horizon={horizon} />
          </Card>
        </div>
      )}

      {activeTab === 'regime' && (
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>
          <Card>
            <RegimeEdgeChart lookback={lookback} horizon={horizon} />
          </Card>
          <Card>
            <WeeklyDriftChart />
          </Card>
        </div>
      )}

      {activeTab === 'symbols' && (
        <>
          <Card>
            <LaneWinRates lookback={lookback} />
          </Card>
          <Card>
            <SymbolEdgeTable lookback={lookback} horizon={horizon} />
          </Card>
          <Card>
            <SellSignalPanel lookback={lookback} />
          </Card>
        </>
      )}

      {activeTab === 'tuning' && (
        <>
          <QueueStatus lookback={lookback} />
          <Card>
            <EquityCurve lookback={lookback} horizon={horizon} />
          </Card>
          <Card>
            <ThresholdSimulator lookback={lookback} horizon={horizon} />
          </Card>
          <Card>
            <TuningHistory />
          </Card>
        </>
      )}

    </div>
  )
}
