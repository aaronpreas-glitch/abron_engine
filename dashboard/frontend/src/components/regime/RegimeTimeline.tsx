import { useQuery } from '@tanstack/react-query'
import { api } from '../../api'
import type { RegimePoint } from '../../types'
import { LoadingSpinner } from '../shared/LoadingSpinner'
import { EmptyState } from '../shared/EmptyState'
import {
  ComposedChart, Line, XAxis, YAxis, Tooltip, CartesianGrid,
  ResponsiveContainer, ReferenceLine, Legend,
  RadarChart, PolarGrid, PolarAngleAxis, Radar,
} from 'recharts'

interface CurrentRegime {
  ts_utc: string | null
  sol_change_24h: number | null
  breadth_pct: number | null
  liquidity_score: number | null
  volume_score: number | null
  regime_score: number | null
  regime_label: string | null
  notes: string | null
}

function ComponentBar({
  label, value, max, color, note,
}: { label: string; value: number | null; max: number; color: string; note?: string }) {
  const pct = value != null ? Math.min(100, Math.round((value / max) * 100)) : 0
  return (
    <div style={{ marginBottom: 10 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11, marginBottom: 3 }}>
        <span style={{ color: 'var(--muted)' }}>{label}</span>
        <span style={{ color: value != null ? color : 'var(--muted)', fontWeight: 700 }}>
          {value != null ? value.toFixed(1) : '—'} / {max}
        </span>
      </div>
      <div style={{ height: 5, background: 'var(--border)', borderRadius: 2 }}>
        <div style={{ height: 5, width: `${pct}%`, background: color, borderRadius: 2, transition: 'width 0.4s' }} />
      </div>
      {note && <div style={{ fontSize: 10, color: 'var(--muted)', marginTop: 2 }}>{note}</div>}
    </div>
  )
}

function regimeColor(score: number | null) {
  if (score == null) return 'var(--muted)'
  if (score >= 60) return 'var(--green)'
  if (score >= 35) return 'var(--amber)'
  return 'var(--red)'
}

function regimeBg(label: string | null) {
  const l = label || ''
  if (l.includes('BULL') || l.includes('STRONG')) return '#1a3a22'
  if (l.includes('BEAR') || l.includes('WEAK')) return '#3a1a1a'
  return 'var(--surface2)'
}

const fmtTs = (ts: string) => new Date(ts).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })

export function RegimeTimeline() {
  const { data: timeline, isLoading } = useQuery<RegimePoint[]>({
    queryKey: ['regime-timeline'],
    queryFn: () => api.get('/regime/timeline?hours=168').then(r => r.data),
    refetchInterval: 60000,
  })

  const { data: current } = useQuery<CurrentRegime>({
    queryKey: ['regime-current'],
    queryFn: () => api.get('/regime/current').then(r => r.data),
    refetchInterval: 30000,
  })

  const { data: alertOverlay } = useQuery<{ ts_utc: string; symbol: string; score_total: number }[]>({
    queryKey: ['alerts-overlay'],
    queryFn: () => api.get('/regime/alerts-overlay?hours=168').then(r => r.data),
  })

  if (isLoading) return <LoadingSpinner />

  const rows = (timeline || []).map(r => ({
    ts: r.ts_utc,
    regime: r.regime_score,
    sol: r.sol_change_24h,
    breadth: r.breadth_pct != null ? r.breadth_pct * 100 : null,
    label: r.regime_label,
  }))

  // Radar data normalized 0–100
  const radarData = current ? [
    {
      subject: 'SOL 24h',
      value: Math.max(0, Math.min(100, ((current.sol_change_24h ?? 0) + 20) / 40 * 100)),
      raw: current.sol_change_24h,
    },
    {
      subject: 'Breadth',
      value: Math.min(100, (current.breadth_pct ?? 0) * 100),
      raw: current.breadth_pct != null ? current.breadth_pct * 100 : null,
    },
    {
      subject: 'Liquidity',
      value: Math.min(100, (current.liquidity_score ?? 0) * 100),
      raw: current.liquidity_score != null ? current.liquidity_score * 100 : null,
    },
    {
      subject: 'Volume',
      value: Math.min(100, (current.volume_score ?? 0) * 100),
      raw: current.volume_score != null ? current.volume_score * 100 : null,
    },
  ] : []

  const hasComponentData = current && (
    current.breadth_pct != null ||
    current.liquidity_score != null ||
    current.volume_score != null
  )

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 16, marginBottom: 16 }}>
        <h2 style={{ fontSize: 14, fontWeight: 700, letterSpacing: '0.06em' }}>🌐 REGIME TIMELINE</h2>
        {current && current.regime_score != null && (
          <span style={{ color: 'var(--muted)', fontSize: 12 }}>
            Now: <span style={{ color: regimeColor(current.regime_score), fontWeight: 700 }}>
              {current.regime_label?.replace(/_/g, ' ')}
            </span> {`(${current.regime_score.toFixed(1)})`}
          </span>
        )}
      </div>

      {/* Current regime breakdown */}
      {current && (
        <div style={{ display: 'grid', gridTemplateColumns: hasComponentData ? '1fr 1fr' : '1fr', gap: 12, marginBottom: 16 }}>
          <div className="card" style={{ background: regimeBg(current.regime_label) }}>
            <div style={{ color: 'var(--muted)', fontSize: 10, marginBottom: 12, fontWeight: 700 }}>CURRENT REGIME</div>

            <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, marginBottom: 14 }}>
              <span style={{ fontSize: 36, fontWeight: 900, color: regimeColor(current.regime_score) }}>
                {current.regime_score != null ? current.regime_score.toFixed(1) : '—'}
              </span>
              <div>
                <div style={{ fontSize: 13, fontWeight: 700, color: regimeColor(current.regime_score) }}>
                  {current.regime_label?.replace(/_/g, ' ') || 'UNKNOWN'}
                </div>
                <div style={{ fontSize: 10, color: 'var(--muted)' }}>regime score / 100</div>
              </div>
            </div>

            <ComponentBar
              label="SOL 24h Change"
              value={current.sol_change_24h}
              max={30}
              color={current.sol_change_24h != null && current.sol_change_24h >= 0 ? 'var(--green)' : 'var(--red)'}
              note={current.sol_change_24h != null ? `${current.sol_change_24h >= 0 ? '+' : ''}${current.sol_change_24h.toFixed(2)}%` : undefined}
            />
            <ComponentBar
              label="Market Breadth"
              value={current.breadth_pct != null ? current.breadth_pct * 100 : null}
              max={100}
              color={(current.breadth_pct ?? 0) >= 0.5 ? 'var(--green)' : (current.breadth_pct ?? 0) >= 0.3 ? 'var(--amber)' : 'var(--red)'}
              note={current.breadth_pct != null ? `${(current.breadth_pct * 100).toFixed(1)}% tokens gaining` : undefined}
            />
            <ComponentBar
              label="Liquidity Health"
              value={current.liquidity_score != null ? current.liquidity_score * 100 : null}
              max={100}
              color={(current.liquidity_score ?? 0) >= 0.6 ? 'var(--green)' : (current.liquidity_score ?? 0) >= 0.4 ? 'var(--amber)' : 'var(--red)'}
              note={current.liquidity_score != null ? `score ${current.liquidity_score.toFixed(2)}` : undefined}
            />
            <ComponentBar
              label="Volume Structure"
              value={current.volume_score != null ? current.volume_score * 100 : null}
              max={100}
              color={(current.volume_score ?? 0) >= 0.6 ? 'var(--green)' : (current.volume_score ?? 0) >= 0.4 ? 'var(--amber)' : 'var(--red)'}
              note={current.volume_score != null ? `score ${current.volume_score.toFixed(2)}` : undefined}
            />

            {current.notes && (
              <div style={{ fontSize: 10, color: 'var(--muted)', marginTop: 8, lineHeight: 1.4 }}>{current.notes}</div>
            )}
          </div>

          {hasComponentData && radarData.length > 0 && (
            <div className="card">
              <div style={{ color: 'var(--muted)', fontSize: 10, marginBottom: 4, fontWeight: 700 }}>COMPONENT RADAR</div>
              <ResponsiveContainer width="100%" height={200}>
                <RadarChart data={radarData} margin={{ top: 10, right: 20, bottom: 10, left: 20 }}>
                  <PolarGrid stroke="#30363d" />
                  <PolarAngleAxis dataKey="subject" tick={{ fontSize: 10, fill: '#8b949e' }} />
                  <Radar
                    name="Regime"
                    dataKey="value"
                    stroke="#58a6ff"
                    fill="#58a6ff"
                    fillOpacity={0.2}
                    strokeWidth={1.5}
                  />
                  <Tooltip
                    contentStyle={{ background: '#161b22', border: '1px solid #30363d', borderRadius: 4, fontSize: 11 }}
                    formatter={(_v: unknown, _: unknown, p: { payload?: { raw?: number | null } }) => {
                      const raw = p.payload?.raw
                      return [raw != null ? raw.toFixed(1) : '—', '']
                    }}
                  />
                </RadarChart>
              </ResponsiveContainer>
              <div style={{ color: 'var(--muted)', fontSize: 10, textAlign: 'center', marginTop: 2 }}>all axes normalized 0–100</div>
            </div>
          )}
        </div>
      )}

      {/* 7-day timeline chart */}
      {rows.length === 0 ? <EmptyState message="No regime data yet." /> : (
        <div className="card">
          <div style={{ color: 'var(--muted)', fontSize: 11, marginBottom: 8 }}>7-DAY TIMELINE</div>
          <ResponsiveContainer width="100%" height={260}>
            <ComposedChart data={rows} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
              <CartesianGrid stroke="#30363d" strokeDasharray="3 3" />
              <XAxis dataKey="ts" tickFormatter={fmtTs} tick={{ fontSize: 10 }} />
              <YAxis yAxisId="score" domain={[0, 100]} tick={{ fontSize: 10 }} />
              <YAxis yAxisId="sol" orientation="right" tick={{ fontSize: 10 }} tickFormatter={v => `${v}%`} />
              <Tooltip
                contentStyle={{ background: '#161b22', border: '1px solid #30363d', borderRadius: 4 }}
                labelStyle={{ color: '#8b949e', fontSize: 11 }}
                labelFormatter={(l: unknown) => new Date(l as string).toLocaleString()}
              />
              <Legend wrapperStyle={{ fontSize: 11 }} />
              <ReferenceLine yAxisId="score" y={60} stroke="#39d35344" strokeDasharray="4 4" label={{ value: 'BULL', fill: '#39d353', fontSize: 9 }} />
              <ReferenceLine yAxisId="score" y={35} stroke="#f8514944" strokeDasharray="4 4" label={{ value: 'BEAR', fill: '#f85149', fontSize: 9 }} />
              <Line yAxisId="score" type="monotone" dataKey="regime" name="Regime Score" stroke="#58a6ff" dot={false} strokeWidth={1.5} />
              <Line yAxisId="sol" type="monotone" dataKey="sol" name="SOL 24h %" stroke="#39d353" dot={false} strokeWidth={1} strokeDasharray="4 2" />
              <Line yAxisId="score" type="monotone" dataKey="breadth" name="Breadth %" stroke="#f0a500" dot={false} strokeWidth={1} strokeDasharray="2 2" />
              {(alertOverlay || []).slice(0, 10).map(a => (
                <ReferenceLine
                  key={`${a.ts_utc}-${a.symbol}`}
                  yAxisId="score"
                  x={a.ts_utc}
                  stroke="#39d35360"
                  label={{ value: a.symbol, fill: '#39d353', fontSize: 9 }}
                />
              ))}
            </ComposedChart>
          </ResponsiveContainer>
        </div>
      )}
    </div>
  )
}
