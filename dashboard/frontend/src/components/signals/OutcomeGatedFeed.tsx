/**
 * OutcomeGatedFeed — Signal feed filtered to only resolved outcomes.
 *
 * Every row shows: symbol · score · decision · conviction · outcome return (1h/4h/24h)
 * Color-coded so winners are green, losers red, at a glance.
 *
 * Calibration panel below recommends the optimal score threshold
 * based on actual 4h return data for each score band.
 */
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { api } from '../../api'

// ─── Types ────────────────────────────────────────────────────────────────────

interface OutcomeSignal {
  id: number
  ts_utc: string
  symbol: string
  score_total: number | null
  decision: string
  conviction: number | null
  regime_label: string | null
  change_24h: number | null
  outcome_ret: number | null
  ret_1h: number | null
  ret_4h: number | null
  ret_24h: number | null
  outcome_status: string | null
  outcome_conf: string | null
}

interface CalibrationBand {
  band_mid: number
  n: number
  avg_1h: number | null
  avg_4h: number | null
  avg_24h: number | null
  wr_1h: number | null
  wr_4h: number | null
  wr_24h: number | null
}

interface CalibrationData {
  bands: CalibrationBand[]
  optimal_threshold: number | null
  optimal_wr_4h: number | null
  optimal_avg_4h: number | null
  lookback_days: number
  total_outcomes: number
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

const MONO: React.CSSProperties = { fontFamily: 'JetBrains Mono, monospace' }

function retColor(v: number | null | undefined) {
  if (v == null) return 'var(--muted)'
  if (v > 0) return 'var(--green)'
  if (v < 0) return 'var(--red)'
  return 'var(--muted)'
}

function fmtPct(v: number | null | undefined, plus = true) {
  if (v == null) return '—'
  const s = plus && v > 0 ? '+' : ''
  return `${s}${v.toFixed(2)}%`
}

function fmtScore(v: number | null) {
  return v != null ? v.toFixed(0) : '—'
}

function scoreColor(v: number | null) {
  if (v == null) return 'var(--muted)'
  if (v >= 80) return 'var(--green)'
  if (v >= 65) return '#4ade80'
  if (v >= 50) return 'var(--amber)'
  return 'var(--muted)'
}

function convLabel(v: number | null) {
  if (v === 3) return 'A'
  if (v === 2) return 'B'
  if (v === 1) return 'C'
  return '—'
}

function convColor(v: number | null) {
  if (v === 3) return 'var(--green)'
  if (v === 2) return 'var(--amber)'
  return 'var(--muted)'
}

function timeAgo(ts: string) {
  const d = Date.now() - new Date(ts + (ts.endsWith('Z') ? '' : 'Z')).getTime()
  const m = Math.floor(d / 60000)
  if (m < 60) return `${m}m ago`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h ago`
  return `${Math.floor(h / 24)}d ago`
}

function winBadge(ret: number | null) {
  if (ret == null) return null
  if (ret > 0) return { label: 'WIN', bg: 'rgba(0,212,138,0.12)', color: 'var(--green)', border: 'rgba(0,212,138,0.25)' }
  if (ret < 0) return { label: 'LOSS', bg: 'rgba(240,79,79,0.12)', color: 'var(--red)', border: 'rgba(240,79,79,0.25)' }
  return { label: 'FLAT', bg: 'rgba(255,255,255,0.05)', color: 'var(--muted)', border: 'var(--border)' }
}

// ─── Calibration Panel ────────────────────────────────────────────────────────

function CalibrationPanel({ data, horizon }: { data: CalibrationData; horizon: number }) {
  const hKey = horizon === 1 ? 'wr_1h' : horizon === 4 ? 'wr_4h' : 'wr_24h'
  const avgKey = horizon === 1 ? 'avg_1h' : horizon === 4 ? 'avg_4h' : 'avg_24h'
  const maxWr = Math.max(...data.bands.map(b => (b[hKey as keyof CalibrationBand] as number) ?? 0), 1)

  return (
    <div style={{
      background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 10,
      padding: '18px 20px',
    }}>
      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 18 }}>
        <div>
          <div style={{ fontSize: 9, fontWeight: 600, letterSpacing: '0.18em', color: 'var(--dim)', ...MONO, textTransform: 'uppercase', marginBottom: 4 }}>
            Score Calibration — {data.lookback_days}d Window
          </div>
          <div style={{ fontSize: 11, color: 'var(--muted)', ...MONO }}>
            {data.total_outcomes} resolved outcomes · win rate by score band
          </div>
        </div>

        {/* Optimal threshold pill */}
        {data.optimal_threshold != null && (
          <div style={{
            background: 'rgba(0,212,138,0.08)', border: '1px solid rgba(0,212,138,0.25)',
            borderRadius: 8, padding: '10px 16px', textAlign: 'center', minWidth: 140,
          }}>
            <div style={{ fontSize: 9, color: 'var(--dim)', ...MONO, letterSpacing: '0.12em', marginBottom: 4 }}>
              OPTIMAL THRESHOLD
            </div>
            <div style={{ fontSize: 28, fontWeight: 800, color: 'var(--green)', ...MONO, lineHeight: 1 }}>
              {data.optimal_threshold}
            </div>
            <div style={{ fontSize: 10, color: 'var(--muted)', ...MONO, marginTop: 3 }}>
              {data.optimal_wr_4h?.toFixed(0)}% WR · {fmtPct(data.optimal_avg_4h)} avg 4h
            </div>
          </div>
        )}
      </div>

      {/* Band bars */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
        {data.bands.map(b => {
          const wr = (b[hKey as keyof CalibrationBand] as number) ?? 0
          const avg = (b[avgKey as keyof CalibrationBand] as number) ?? 0
          const isOptimal = b.band_mid === data.optimal_threshold
          const barColor = wr >= 55 ? 'var(--green)' : wr >= 45 ? 'var(--amber)' : 'var(--red)'
          const barWidth = (wr / maxWr) * 100

          return (
            <div key={b.band_mid} style={{
              display: 'grid',
              gridTemplateColumns: '44px 1fr 52px 60px 36px',
              gap: 8, alignItems: 'center',
              padding: '5px 8px', borderRadius: 6,
              background: isOptimal ? 'rgba(0,212,138,0.06)' : 'transparent',
              border: `1px solid ${isOptimal ? 'rgba(0,212,138,0.18)' : 'transparent'}`,
              transition: 'background 0.15s',
            }}>
              {/* Band label */}
              <span style={{
                fontSize: 10, fontWeight: 700, color: isOptimal ? 'var(--green)' : 'var(--muted)',
                ...MONO, textAlign: 'right',
              }}>
                {b.band_mid}+
                {isOptimal && <span style={{ fontSize: 8, marginLeft: 2 }}>★</span>}
              </span>

              {/* Win rate bar */}
              <div style={{ position: 'relative', height: 6, background: 'var(--border)', borderRadius: 3, overflow: 'hidden' }}>
                <div style={{
                  position: 'absolute', left: 0, top: 0, bottom: 0,
                  width: `${barWidth}%`,
                  background: barColor,
                  borderRadius: 3,
                  transition: 'width 0.4s ease',
                }} />
              </div>

              {/* Win rate label */}
              <span style={{ fontSize: 11, fontWeight: 700, color: barColor, ...MONO, textAlign: 'right' }}>
                {wr.toFixed(0)}%
              </span>

              {/* Avg return */}
              <span style={{ fontSize: 10, color: retColor(avg), ...MONO, textAlign: 'right' }}>
                {fmtPct(avg)}
              </span>

              {/* n */}
              <span style={{ fontSize: 9, color: 'var(--dim)', ...MONO, textAlign: 'right' }}>
                n={b.n}
              </span>
            </div>
          )
        })}
      </div>

      {data.bands.length === 0 && (
        <div style={{ textAlign: 'center', color: 'var(--dim)', fontSize: 11, ...MONO, padding: '24px 0' }}>
          Not enough resolved outcomes yet — check back after more signals age past {horizon}h.
        </div>
      )}
    </div>
  )
}

// ─── Signal Row ───────────────────────────────────────────────────────────────

function OutcomeRow({
  sig, onClick, horizon,
}: { sig: OutcomeSignal; onClick: () => void; horizon: number }) {
  const mainRet = horizon === 1 ? sig.ret_1h : horizon === 4 ? sig.ret_4h : sig.ret_24h
  const badge = winBadge(mainRet)

  return (
    <div
      onClick={onClick}
      style={{
        display: 'grid',
        gridTemplateColumns: '140px 52px 28px 52px 70px 70px 70px 70px',
        gap: 8, alignItems: 'center',
        padding: '8px 0',
        borderBottom: '1px solid rgba(255,255,255,0.04)',
        cursor: 'pointer',
        transition: 'background 0.1s',
      }}
      onMouseEnter={e => (e.currentTarget.style.background = 'rgba(255,255,255,0.025)')}
      onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
    >
      {/* Symbol + time */}
      <div style={{ minWidth: 0 }}>
        <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--text)', ...MONO }}>
          ${sig.symbol}
        </div>
        <div style={{ fontSize: 9, color: 'var(--dim)', ...MONO, marginTop: 1 }}>
          {timeAgo(sig.ts_utc)}
        </div>
      </div>

      {/* Score */}
      <span style={{ fontSize: 12, fontWeight: 700, color: scoreColor(sig.score_total), ...MONO, textAlign: 'right' }}>
        {fmtScore(sig.score_total)}
      </span>

      {/* Conviction */}
      <span style={{ fontSize: 12, fontWeight: 700, color: convColor(sig.conviction), ...MONO, textAlign: 'center' }}>
        {convLabel(sig.conviction)}
      </span>

      {/* Win/Loss badge */}
      {badge ? (
        <span style={{
          fontSize: 8, fontWeight: 700, padding: '2px 5px', borderRadius: 3, textAlign: 'center',
          background: badge.bg, color: badge.color, border: `1px solid ${badge.border}`,
          ...MONO, letterSpacing: '0.04em',
        }}>
          {badge.label}
        </span>
      ) : <span />}

      {/* 1h return */}
      <span style={{ fontSize: 11, fontWeight: 600, color: retColor(sig.ret_1h), ...MONO, textAlign: 'right' }}>
        {fmtPct(sig.ret_1h)}
      </span>

      {/* 4h return */}
      <span style={{
        fontSize: 11, fontWeight: horizon === 4 ? 700 : 500,
        color: retColor(sig.ret_4h), ...MONO, textAlign: 'right',
        opacity: horizon === 4 ? 1 : 0.7,
      }}>
        {fmtPct(sig.ret_4h)}
      </span>

      {/* 24h return */}
      <span style={{
        fontSize: 11, fontWeight: horizon === 24 ? 700 : 500,
        color: retColor(sig.ret_24h), ...MONO, textAlign: 'right',
        opacity: horizon === 24 ? 1 : 0.7,
      }}>
        {fmtPct(sig.ret_24h)}
      </span>

      {/* Regime */}
      <span style={{ fontSize: 9, color: 'var(--dim)', ...MONO, textAlign: 'right', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
        {sig.regime_label?.replace(/_/g, ' ') ?? '—'}
      </span>
    </div>
  )
}

// ─── Main Component ───────────────────────────────────────────────────────────

export function OutcomeGatedFeed() {
  const navigate = useNavigate()
  const [lookback, setLookback] = useState(7)
  const [horizon, setHorizon] = useState(4)
  const [filterDecision, setFilterDecision] = useState('')
  const [showCalib, setShowCalib] = useState(true)

  const { data: signals = [], isLoading: sigsLoading } = useQuery<OutcomeSignal[]>({
    queryKey: ['outcome-feed', lookback, horizon, filterDecision],
    queryFn: () => api.get(`/signals/outcome-feed?lookback_days=${lookback}&horizon=${horizon}${filterDecision ? `&decision=${filterDecision}` : ''}`).then(r => r.data),
    staleTime: 60_000,
    refetchInterval: 120_000,
  })

  const { data: calibration, isLoading: calibLoading } = useQuery<CalibrationData>({
    queryKey: ['calibration', lookback],
    queryFn: () => api.get(`/signals/calibration?lookback_days=${lookback}`).then(r => r.data),
    staleTime: 120_000,
    refetchInterval: 300_000,
  })

  // Aggregate stats
  const wins = signals.filter(s => {
    const r = horizon === 1 ? s.ret_1h : horizon === 4 ? s.ret_4h : s.ret_24h
    return r != null && r > 0
  }).length
  const losses = signals.filter(s => {
    const r = horizon === 1 ? s.ret_1h : horizon === 4 ? s.ret_4h : s.ret_24h
    return r != null && r < 0
  }).length
  const winRate = wins + losses > 0 ? (wins / (wins + losses) * 100) : null
  const avgRet = signals.length > 0 ? signals.reduce((sum, s) => {
    const r = horizon === 1 ? s.ret_1h : horizon === 4 ? s.ret_4h : s.ret_24h
    return sum + (r ?? 0)
  }, 0) / signals.filter(s => {
    const r = horizon === 1 ? s.ret_1h : horizon === 4 ? s.ret_4h : s.ret_24h
    return r != null
  }).length : null

  const wrColor = winRate == null ? 'var(--muted)' : winRate >= 55 ? 'var(--green)' : winRate >= 45 ? 'var(--amber)' : 'var(--red)'

  const btnStyle = (active: boolean): React.CSSProperties => ({
    padding: '3px 10px', fontSize: 11, cursor: 'pointer',
    background: active ? 'rgba(0,212,138,0.1)' : 'transparent',
    border: `1px solid ${active ? 'rgba(0,212,138,0.3)' : 'var(--border)'}`,
    color: active ? 'var(--green)' : 'var(--muted)',
    borderRadius: 4, ...MONO, transition: 'all 0.1s',
  })

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14, maxWidth: 1020, margin: '0 auto' }}>

      {/* ── Header ──────────────────────────────────────────────────── */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-end' }}>
        <div>
          <div style={{ fontSize: 18, fontWeight: 800, color: 'var(--text)', ...MONO, letterSpacing: '-0.01em' }}>
            Outcome Feed
          </div>
          <div style={{ fontSize: 10, color: 'var(--dim)', ...MONO, marginTop: 3 }}>
            Signals with resolved outcomes — closes the feedback loop
          </div>
        </div>

        {/* Controls */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6, alignItems: 'flex-end' }}>
          {/* Lookback */}
          <div style={{ display: 'flex', gap: 4 }}>
            {[7, 14, 30].map(d => (
              <button key={d} style={btnStyle(lookback === d)} onClick={() => setLookback(d)}>{d}d</button>
            ))}
          </div>
          {/* Horizon */}
          <div style={{ display: 'flex', gap: 4 }}>
            <span style={{ fontSize: 9, color: 'var(--dim)', ...MONO, alignSelf: 'center' }}>horizon:</span>
            {([1, 4, 24] as const).map(h => (
              <button key={h} style={btnStyle(horizon === h)} onClick={() => setHorizon(h)}>{h}h</button>
            ))}
          </div>
        </div>
      </div>

      {/* ── KPI strip ────────────────────────────────────────────────── */}
      <div style={{
        display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 10,
      }}>
        {[
          { label: 'Signals Evaluated', value: String(signals.length), color: 'var(--text)' },
          { label: `Win Rate ${horizon}H`, value: winRate != null ? `${winRate.toFixed(0)}%` : '—', color: wrColor },
          { label: `Avg Return ${horizon}H`, value: fmtPct(avgRet), color: retColor(avgRet) },
          { label: 'Wins / Losses', value: `${wins} / ${losses}`, color: 'var(--muted)' },
        ].map(kpi => (
          <div key={kpi.label} style={{
            background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 8,
            padding: '12px 16px',
          }}>
            <div style={{ fontSize: 9, color: 'var(--dim)', letterSpacing: '0.14em', ...MONO, textTransform: 'uppercase', marginBottom: 4 }}>
              {kpi.label}
            </div>
            <div style={{ fontSize: 20, fontWeight: 800, color: kpi.color, ...MONO, lineHeight: 1 }}>
              {kpi.value}
            </div>
          </div>
        ))}
      </div>

      {/* ── Calibration Panel (toggleable) ───────────────────────────── */}
      <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 10 }}>
        <button
          onClick={() => setShowCalib(v => !v)}
          style={{
            width: '100%', display: 'flex', justifyContent: 'space-between', alignItems: 'center',
            padding: '12px 20px', background: 'none', border: 'none', cursor: 'pointer',
            color: 'var(--text)',
          }}
        >
          <span style={{ fontSize: 9, fontWeight: 600, letterSpacing: '0.18em', color: 'var(--dim)', ...MONO, textTransform: 'uppercase' }}>
            ⚡ Score Calibration &amp; Threshold Recommendation
          </span>
          <span style={{ fontSize: 10, color: 'var(--muted)' }}>{showCalib ? '▲ hide' : '▼ show'}</span>
        </button>

        {showCalib && (
          <div style={{ borderTop: '1px solid var(--border)' }}>
            {calibLoading ? (
              <div style={{ padding: '24px', textAlign: 'center', color: 'var(--dim)', fontSize: 11, ...MONO }}>
                Loading calibration data…
              </div>
            ) : calibration ? (
              <div style={{ padding: '0 2px 2px' }}>
                <CalibrationPanel data={calibration} horizon={horizon} />
              </div>
            ) : (
              <div style={{ padding: '24px', textAlign: 'center', color: 'var(--dim)', fontSize: 11, ...MONO }}>
                No calibration data yet.
              </div>
            )}
          </div>
        )}
      </div>

      {/* ── Signal table ─────────────────────────────────────────────── */}
      <div style={{
        background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 10, overflow: 'hidden',
      }}>
        {/* Table toolbar */}
        <div style={{
          display: 'flex', alignItems: 'center', gap: 12, padding: '14px 20px',
          borderBottom: '1px solid var(--border)',
        }}>
          <span style={{ fontSize: 9, fontWeight: 600, letterSpacing: '0.18em', color: 'var(--dim)', ...MONO, textTransform: 'uppercase' }}>
            Resolved Signals
          </span>
          <div style={{ display: 'flex', gap: 4, marginLeft: 'auto' }}>
            {[
              { label: 'All', value: '' },
              { label: 'Alert', value: 'ALERT' },
              { label: 'Watchlist', value: 'WATCHLIST' },
            ].map(opt => (
              <button key={opt.label} style={btnStyle(filterDecision === opt.value)} onClick={() => setFilterDecision(opt.value)}>
                {opt.label}
              </button>
            ))}
          </div>
        </div>

        {/* Column headers */}
        <div style={{
          display: 'grid',
          gridTemplateColumns: '140px 52px 28px 52px 70px 70px 70px 70px',
          gap: 8, padding: '7px 20px',
          borderBottom: '1px solid rgba(255,255,255,0.06)',
        }}>
          {[
            { h: 'TOKEN', align: 'left' as const },
            { h: 'SCORE', align: 'right' as const },
            { h: 'GR', align: 'center' as const },
            { h: 'RESULT', align: 'center' as const },
            { h: 'RET 1H', align: 'right' as const },
            { h: 'RET 4H', align: 'right' as const },
            { h: 'RET 24H', align: 'right' as const },
            { h: 'REGIME', align: 'right' as const },
          ].map(({ h, align }) => (
            <span key={h} style={{
              fontSize: 8, color: 'var(--dim)', ...MONO, letterSpacing: '0.14em', textAlign: align,
            }}>
              {h}
            </span>
          ))}
        </div>

        {/* Rows */}
        <div style={{ padding: '0 20px' }}>
          {sigsLoading ? (
            <div style={{ padding: '32px 0', textAlign: 'center', color: 'var(--dim)', fontSize: 11, ...MONO }}>
              Loading…
            </div>
          ) : signals.length === 0 ? (
            <div style={{ padding: '32px 0', textAlign: 'center', color: 'var(--dim)', fontSize: 11, ...MONO, lineHeight: 1.7 }}>
              No resolved outcomes in this window.<br />
              <span style={{ color: 'rgba(255,255,255,0.25)', fontSize: 10 }}>
                Outcomes resolve after 1h / 4h / 24h. Extend the lookback or wait for more signals.
              </span>
            </div>
          ) : (
            signals.map(sig => (
              <OutcomeRow
                key={sig.id}
                sig={sig}
                horizon={horizon}
                onClick={() => navigate(`/symbol/${sig.symbol}`)}
              />
            ))
          )}
        </div>

        {signals.length > 0 && (
          <div style={{
            padding: '10px 20px', borderTop: '1px solid rgba(255,255,255,0.04)',
            fontSize: 9, color: 'var(--dim)', ...MONO,
          }}>
            {signals.length} resolved signals · {lookback}d window
          </div>
        )}
      </div>

    </div>
  )
}
