/**
 * CommandCenter — Phase 3 redesigned home page.
 *
 * Layout:
 *  1. GlobalMetricsBar  — live launch ticker
 *  2. Hero Stat Strip   — Regime · Readiness · 14d Equity sparkline · 7d Win Rate · Fear/Greed · Perps PnL
 *  3. Three-column grid:
 *       LEFT  (flex) — Recent Signals table (ALERT + WATCHLIST)
 *       CENTER       — Market prices · Top Picks · 7d Win Rates stacked
 *       RIGHT        — Engine state · Readiness gauge · Open Positions stacked
 */
import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { api } from '../../api'
import {
  AreaChart, Area, ResponsiveContainer, Tooltip, ReferenceLine, YAxis,
} from 'recharts'
import { GlobalMetricsBar } from './GlobalMetricsBar'
import SniperPanel from '../sniper/SniperPanel'

// ─── Types ────────────────────────────────────────────────────────────────────

interface SnapshotData {
  regime: { regime_score: number; regime_label: string; sol_change_24h: number }
  risk: {
    mode: string; emoji: string; paused: boolean
    size_multiplier: number; streak: number
    threshold_delta: number; min_confidence: string
    pause?: { reason?: string }
  }
  sol_price: number | null
  perps: { pnl: number; leverage: number; mark_price: number; entry_price: number; liq_price: number } | null
  fear_greed: { value: string | null; classification: string | null }
  top_picks: { symbol: string; score: number; change_24h: number }[]
  open_positions: { symbol: string }[]
}

interface CryptoPrices {
  BTC: { price: number | null; change_24h: number | null }
  ETH: { price: number | null; change_24h: number | null }
  SOL: { price: number | null; change_24h: number | null }
}

interface OutcomeHorizon { n: number; wins: number; avg: number; win_rate: number }
interface OutcomeWinrates {
  outcomes_1h: OutcomeHorizon
  outcomes_4h: OutcomeHorizon
  outcomes_24h: OutcomeHorizon
}

interface EquityPoint { ts: string; equity: number }

interface Signal {
  id: number
  ts_utc: string
  symbol: string
  score_total: number | null
  decision: string
  conviction: string
  regime_label: string
  change_24h: number | null
  liquidity_usd: number
}

interface ReadinessData {
  score: number
  status: 'READY' | 'PROMISING' | 'BUILDING' | 'NOT_READY'
  gates_passed: number
  gates_total: number
  metrics: {
    win_rate_4h: number
    max_drawdown_pct: number
    expectancy_pct: number
  }
}

interface CyclePlaybook {
  stop_loss_pct: number
  tp1_pct: number
  tp2_pct: number
  max_hold_hours: number
  win_rate_4h: number | null
  avg_return_4h: number | null
  sample_size: number
  last_updated: string | null
}

interface CycleSummary {
  current_phase: 'BEAR' | 'TRANSITION' | 'BULL'
  phase_emoji: string
  phase_color: string
  playbooks: Record<string, CyclePlaybook>
  history_14d: { date: string; phase: string; avg_regime_score: number }[]
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

const MONO: React.CSSProperties = { fontFamily: 'JetBrains Mono, monospace' }

function retColor(v: number | null | undefined) {
  if (v == null) return 'var(--muted)'
  return v > 0 ? 'var(--green)' : v < 0 ? 'var(--red)' : 'var(--muted)'
}

function fmtPct(v: number | null | undefined, plus = true) {
  if (v == null) return '—'
  const sign = plus && v > 0 ? '+' : ''
  return `${sign}${v.toFixed(1)}%`
}

function fmtPrice(sym: string, v: number | null | undefined) {
  if (v == null) return '—'
  if (sym === 'SOL') return `$${v.toFixed(2)}`
  return `$${v.toLocaleString('en-US', { maximumFractionDigits: 0 })}`
}

function timeAgo(ts: string) {
  const diff = Date.now() - new Date(ts + 'Z').getTime()
  const m = Math.floor(diff / 60000)
  if (m < 1)  return 'now'
  if (m < 60) return `${m}m`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h`
  return `${Math.floor(h / 24)}d`
}

function scoreColor(s: number | null | undefined) {
  if (s == null) return 'var(--muted)'
  if (s >= 82) return 'var(--green)'
  if (s >= 70) return '#4ade80'
  if (s >= 58) return 'var(--amber)'
  return 'var(--muted)'
}

function regimeColor(label: string) {
  if (!label) return 'var(--muted)'
  if (label.includes('RISK_ON') || label.includes('BULL')) return 'var(--green)'
  if (label.includes('RISK_OFF') || label.includes('BEAR')) return 'var(--red)'
  return 'var(--amber)'
}

function winRateColor(wr: number) {
  return wr >= 55 ? 'var(--green)' : wr >= 45 ? 'var(--amber)' : 'var(--red)'
}

function readinessColor(score: number) {
  if (score >= 80) return 'var(--green)'
  if (score >= 60) return '#a3e635'
  if (score >= 40) return 'var(--amber)'
  return 'var(--red)'
}

function modeColor(mode: string) {
  if (mode === 'DEFENSIVE') return 'var(--red)'
  if (mode === 'CAUTIOUS')  return 'var(--amber)'
  return 'var(--green)'
}

function cycleColor(phase: string) {
  if (phase === 'BEAR') return 'var(--red)'
  if (phase === 'BULL') return 'var(--green)'
  return 'var(--amber)'
}

function cycleEmoji(phase: string) {
  if (phase === 'BEAR') return '🐻'
  if (phase === 'BULL') return '🐂'
  return '↔'
}

// ─── Shared primitives ────────────────────────────────────────────────────────

function SectionLabel({ children, style }: { children: React.ReactNode; style?: React.CSSProperties }) {
  return (
    <div style={{
      fontSize: 8.5, fontWeight: 700, letterSpacing: '0.2em',
      color: 'rgba(255,255,255,0.25)', ...MONO,
      textTransform: 'uppercase', marginBottom: 10,
      ...style,
    }}>
      {children}
    </div>
  )
}

function Divider({ style }: { style?: React.CSSProperties }) {
  return <div style={{ height: 1, background: 'rgba(255,255,255,0.06)', ...style }} />
}

// ─── Hero Stat Strip helpers ──────────────────────────────────────────────────

function HeroStat({
  label, value, sub, color, onClick,
}: {
  label: string; value: string; sub?: string; color?: string; onClick?: () => void
}) {
  return (
    <div
      onClick={onClick}
      style={{
        flex: '1 1 0', display: 'flex', flexDirection: 'column', gap: 4,
        padding: '14px 20px',
        borderRight: '1px solid rgba(255,255,255,0.06)',
        cursor: onClick ? 'pointer' : undefined,
        transition: 'background 0.14s',
        minWidth: 0,
      }}
      onMouseEnter={e => onClick && (e.currentTarget.style.background = 'rgba(255,255,255,0.025)')}
      onMouseLeave={e => onClick && (e.currentTarget.style.background = 'transparent')}
    >
      <span style={{ fontSize: 8.5, fontWeight: 700, letterSpacing: '0.2em', color: 'rgba(255,255,255,0.28)', ...MONO, textTransform: 'uppercase' }}>
        {label}
      </span>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 6, minWidth: 0 }}>
        <span style={{ fontSize: 20, fontWeight: 800, ...MONO, lineHeight: 1, color: color || 'var(--text)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {value}
        </span>
        {sub && <span style={{ fontSize: 9.5, color: 'rgba(255,255,255,0.33)', ...MONO, whiteSpace: 'nowrap', flexShrink: 0 }}>{sub}</span>}
      </div>
    </div>
  )
}

// ─── Signal table row ─────────────────────────────────────────────────────────

function SigRow({ sig, onClick }: { sig: Signal; onClick: () => void }) {
  const isAlert     = sig.decision === 'ALERT'
  const isWatchlist = sig.decision === 'WATCHLIST'
  const decBg     = isAlert ? 'rgba(0,212,138,0.1)'  : isWatchlist ? 'rgba(96,165,250,0.08)' : 'rgba(255,255,255,0.04)'
  const decColor  = isAlert ? 'var(--green)'          : isWatchlist ? 'var(--blue)'           : 'var(--muted)'
  const decBorder = isAlert ? 'rgba(0,212,138,0.25)'  : isWatchlist ? 'rgba(96,165,250,0.2)'  : 'var(--border)'

  return (
    <div
      onClick={onClick}
      style={{
        display: 'grid',
        gridTemplateColumns: '1fr 66px 24px 46px 52px',
        alignItems: 'center',
        gap: 8,
        padding: '7px 0',
        borderBottom: '1px solid rgba(255,255,255,0.04)',
        cursor: 'pointer',
        borderRadius: 4,
        transition: 'background 0.1s',
      }}
      onMouseEnter={e => (e.currentTarget.style.background = 'rgba(255,255,255,0.028)')}
      onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 7, minWidth: 0 }}>
        {isAlert && (
          <div style={{
            width: 5, height: 5, borderRadius: '50%',
            background: 'var(--green)', boxShadow: '0 0 6px var(--green)',
            flexShrink: 0,
          }} />
        )}
        <span style={{ fontSize: 11.5, fontWeight: 700, color: 'var(--text)', ...MONO, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          ${sig.symbol}
        </span>
        <span style={{ fontSize: 8.5, color: 'rgba(255,255,255,0.25)', ...MONO, flexShrink: 0 }}>
          {timeAgo(sig.ts_utc)}
        </span>
      </div>

      <span style={{
        fontSize: 8, fontWeight: 700, padding: '2px 6px', borderRadius: 4,
        background: decBg, color: decColor, border: `1px solid ${decBorder}`,
        ...MONO, letterSpacing: '0.05em', textAlign: 'center', whiteSpace: 'nowrap',
      }}>
        {sig.decision}
      </span>

      <span style={{
        fontSize: 11, fontWeight: 700, textAlign: 'center',
        color: sig.conviction === 'A' ? 'var(--green)' : sig.conviction === 'B' ? 'var(--amber)' : 'var(--muted)',
        ...MONO,
      }}>
        {sig.conviction}
      </span>

      <span style={{ fontSize: 12, fontWeight: 700, textAlign: 'right', color: scoreColor(sig.score_total), ...MONO }}>
        {sig.score_total != null ? sig.score_total.toFixed(0) : '—'}
      </span>

      <span style={{ fontSize: 10.5, textAlign: 'right', color: retColor(sig.change_24h), ...MONO }}>
        {fmtPct(sig.change_24h)}
      </span>
    </div>
  )
}

// ─── Main Component ───────────────────────────────────────────────────────────

export function CommandCenter() {
  const navigate = useNavigate()

  const { data: snap } = useQuery<SnapshotData>({
    queryKey: ['snapshot'],
    queryFn: () => api.get('/snapshot').then(r => r.data),
    refetchInterval: 30_000,
    staleTime: 20_000,
  })

  const { data: outcomes } = useQuery<OutcomeWinrates>({
    queryKey: ['outcomes', 7],
    queryFn: () => api.get('/performance/outcomes?lookback_days=7').then(r => r.data),
    refetchInterval: 120_000,
    staleTime: 90_000,
  })

  const { data: equity } = useQuery<EquityPoint[]>({
    queryKey: ['equity', 14],
    queryFn: () => api.get('/performance/equity-curve?lookback_days=14&horizon_hours=4').then(r => r.data),
    refetchInterval: 120_000,
    staleTime: 90_000,
  })

  const { data: prices } = useQuery<CryptoPrices>({
    queryKey: ['crypto-prices'],
    queryFn: () => api.get('/prices').then(r => r.data),
    refetchInterval: 30_000,
    staleTime: 25_000,
  })

  const { data: recentRaw } = useQuery<Signal[]>({
    queryKey: ['recent-home'],
    queryFn: () => api.get('/signals/recent?limit=40').then(r => r.data),
    refetchInterval: 30_000,
    staleTime: 20_000,
  })

  const { data: readiness } = useQuery<ReadinessData>({
    queryKey: ['readiness-score'],
    queryFn: () => api.get('/performance/readiness-score?lookback_days=30').then(r => r.data),
    refetchInterval: 300_000,
    staleTime: 240_000,
  })

  const { data: cycleSummary } = useQuery<CycleSummary>({
    queryKey: ['cycle-summary'],
    queryFn: () => api.get('/market/cycle-summary').then(r => r.data),
    refetchInterval: 300_000,
    staleTime: 240_000,
  })

  // ── Derived ────────────────────────────────────────────────────────────────
  const regime   = snap?.regime
  const risk     = snap?.risk
  const riskMode = risk?.mode ?? ''
  const engColor = modeColor(riskMode)

  const topSignals = (recentRaw || [])
    .filter(s => s.decision === 'ALERT' || s.decision === 'WATCHLIST')
    .sort((a, b) => (b.score_total ?? 0) - (a.score_total ?? 0))
    .slice(0, 12)

  const equityData = (equity || []).map(p => ({ v: (p.equity - 1) * 100 }))
  const equityEnd  = equityData.length ? equityData[equityData.length - 1].v : 0
  const eqColor    = equityEnd >= 0 ? '#00d48a' : '#f04f4f'

  const wr4h   = outcomes?.outcomes_4h?.win_rate ?? null
  const fg     = snap?.fear_greed
  const fgVal  = fg?.value ? parseInt(fg.value) : null
  const fgColor = fgVal == null ? 'var(--muted)' : fgVal >= 60 ? 'var(--green)' : fgVal >= 40 ? 'var(--amber)' : 'var(--red)'

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12, maxWidth: 1440, margin: '0 auto' }}>

      {/* ── Global ticker ────────────────────────────────────────────────── */}
      <GlobalMetricsBar />

      {/* ══ HERO STAT STRIP ════════════════════════════════════════════════ */}
      <div style={{
        display: 'flex',
        background: 'rgba(255,255,255,0.032)',
        backdropFilter: 'blur(32px) saturate(200%)',
        WebkitBackdropFilter: 'blur(32px) saturate(200%)',
        border: '1px solid rgba(255,255,255,0.08)',
        borderRadius: 14,
        overflow: 'hidden',
        boxShadow: 'inset 0 1px 0 rgba(255,255,255,0.1), 0 4px 24px rgba(0,0,0,0.25)',
        position: 'relative',
      }}>

        {/* Regime + Cycle Phase */}
        <div
          onClick={() => navigate('/regime')}
          style={{
            flex: '1 1 0', display: 'flex', flexDirection: 'column', gap: 4,
            padding: '14px 20px',
            borderRight: '1px solid rgba(255,255,255,0.06)',
            cursor: 'pointer', transition: 'background 0.14s', minWidth: 0,
          }}
          onMouseEnter={e => (e.currentTarget.style.background = 'rgba(255,255,255,0.025)')}
          onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
        >
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <span style={{ fontSize: 8.5, fontWeight: 700, letterSpacing: '0.2em', color: 'rgba(255,255,255,0.28)', ...MONO, textTransform: 'uppercase' }}>
              Regime
            </span>
            {/* Cycle phase badge */}
            {cycleSummary && (
              <span style={{
                fontSize: 8, fontWeight: 700, padding: '2px 6px', borderRadius: 4,
                background: `${cycleColor(cycleSummary.current_phase)}22`,
                color: cycleColor(cycleSummary.current_phase),
                border: `1px solid ${cycleColor(cycleSummary.current_phase)}44`,
                ...MONO, letterSpacing: '0.08em',
              }}>
                {cycleEmoji(cycleSummary.current_phase)} {cycleSummary.current_phase}
              </span>
            )}
          </div>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 6 }}>
            <span style={{ fontSize: 20, fontWeight: 800, ...MONO, lineHeight: 1, color: regime ? regimeColor(regime.regime_label) : 'var(--muted)' }}>
              {regime?.regime_label?.replace(/_/g, ' ') || '—'}
            </span>
            {regime?.regime_score != null && (
              <span style={{ fontSize: 9.5, color: 'rgba(255,255,255,0.33)', ...MONO }}>{regime.regime_score.toFixed(0)} pts</span>
            )}
          </div>
        </div>

        {/* Readiness */}
        <HeroStat
          label="Readiness"
          value={readiness ? String(Math.round(readiness.score)) : '—'}
          sub={readiness ? readiness.status.replace('_', ' ') : undefined}
          color={readiness ? readinessColor(readiness.score) : 'var(--muted)'}
          onClick={() => navigate('/performance')}
        />

        {/* 14d Equity — wider with inline sparkline */}
        <div
          onClick={() => navigate('/performance')}
          style={{
            flex: '2 1 0', borderRight: '1px solid rgba(255,255,255,0.06)',
            padding: '12px 20px', cursor: 'pointer',
            display: 'flex', flexDirection: 'column', gap: 4,
            transition: 'background 0.14s', minWidth: 0,
          }}
          onMouseEnter={e => (e.currentTarget.style.background = 'rgba(255,255,255,0.025)')}
          onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
        >
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <span style={{ fontSize: 8.5, fontWeight: 700, letterSpacing: '0.2em', color: 'rgba(255,255,255,0.28)', ...MONO, textTransform: 'uppercase' }}>
              14d Equity
            </span>
            <span style={{ fontSize: 18, fontWeight: 800, ...MONO, color: eqColor }}>
              {(equityEnd ?? 0) >= 0 ? '+' : ''}{(equityEnd ?? 0).toFixed(1)}%
            </span>
          </div>
          {equityData.length > 1 ? (
            <div style={{ height: 38 }}>
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={equityData} margin={{ top: 2, right: 0, bottom: 0, left: 0 }}>
                  <defs>
                    <linearGradient id="eqGradH" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%"  stopColor={eqColor} stopOpacity={0.3} />
                      <stop offset="95%" stopColor={eqColor} stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <YAxis domain={['auto', 'auto']} hide />
                  <ReferenceLine y={0} stroke="rgba(255,255,255,0.12)" strokeDasharray="3 3" />
                  <Tooltip
                    contentStyle={{ background: 'rgba(4,7,16,0.95)', border: '1px solid rgba(255,255,255,0.1)', fontSize: 10, ...MONO }}
                    formatter={(v: number | undefined) => [`${(v ?? 0).toFixed(1)}%`, 'Equity']}
                    labelFormatter={() => ''}
                  />
                  <Area type="monotone" dataKey="v" stroke={eqColor} strokeWidth={1.5} fill="url(#eqGradH)" dot={false} isAnimationActive={false} />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          ) : (
            <div style={{ fontSize: 10, color: 'var(--dim)', ...MONO, marginTop: 4 }}>No data yet</div>
          )}
        </div>

        {/* 7d Win Rate */}
        <HeroStat
          label="7d Win Rate"
          value={wr4h != null ? `${wr4h.toFixed(0)}%` : '—'}
          sub="4h horizon"
          color={wr4h != null ? winRateColor(wr4h) : 'var(--muted)'}
          onClick={() => navigate('/outcome-feed')}
        />

        {/* Fear & Greed */}
        <HeroStat
          label="Fear & Greed"
          value={fgVal != null ? String(fgVal) : '—'}
          sub={fg?.classification ?? undefined}
          color={fgColor}
        />

        {/* Perps PnL (conditional) */}
        {snap?.perps ? (
          <HeroStat
            label="Perps PnL"
            value={`${(snap.perps.pnl ?? 0) >= 0 ? '+' : ''}$${(snap.perps.pnl ?? 0).toFixed(0)}`}
            sub={snap.perps.leverage != null ? `${snap.perps.leverage.toFixed(1)}× lev` : undefined}
            color={snap.perps.pnl >= 0 ? 'var(--green)' : 'var(--red)'}
            onClick={() => navigate('/trading/perps-paper')}
          />
        ) : (
          /* Trailing spacer to keep strip balanced */
          <div style={{ flex: '1 1 0', minWidth: 0 }} />
        )}
      </div>

      {/* ══ MAIN GRID ══════════════════════════════════════════════════════ */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 278px 212px', gap: 12, alignItems: 'start' }}>

        {/* ── LEFT: Recent Signals ───────────────────────────────────────── */}
        <div style={{
          background: 'rgba(255,255,255,0.032)',
          backdropFilter: 'blur(32px) saturate(200%)',
          WebkitBackdropFilter: 'blur(32px) saturate(200%)',
          border: '1px solid rgba(255,255,255,0.08)',
          borderRadius: 14,
          overflow: 'hidden',
          boxShadow: 'inset 0 1px 0 rgba(255,255,255,0.1), 0 4px 24px rgba(0,0,0,0.22)',
        }}>
          {/* Header */}
          <div style={{
            padding: '13px 18px 9px',
            display: 'flex', justifyContent: 'space-between', alignItems: 'center',
            borderBottom: '1px solid rgba(255,255,255,0.06)',
          }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <SectionLabel style={{ marginBottom: 0 }}>Recent Signals</SectionLabel>
              {topSignals.length > 0 && (
                <span style={{
                  fontSize: 9, fontWeight: 700, padding: '1px 7px',
                  background: 'rgba(0,212,138,0.1)', color: 'var(--green)',
                  border: '1px solid rgba(0,212,138,0.2)', borderRadius: 10,
                  ...MONO,
                }}>
                  {topSignals.filter(s => s.decision === 'ALERT').length} alerts
                </span>
              )}
            </div>
            <span
              onClick={() => navigate('/signals')}
              style={{ fontSize: 9.5, color: 'var(--blue)', cursor: 'pointer', ...MONO, letterSpacing: '0.04em' }}
            >
              Full feed →
            </span>
          </div>

          {/* Column headers */}
          <div style={{
            display: 'grid', gridTemplateColumns: '1fr 66px 24px 46px 52px',
            gap: 8, padding: '7px 18px',
            borderBottom: '1px solid rgba(255,255,255,0.05)',
          }}>
            {[
              { h: 'TOKEN', align: 'left' as const },
              { h: 'TYPE',  align: 'center' as const },
              { h: 'GR',    align: 'center' as const },
              { h: 'SCORE', align: 'right' as const },
              { h: '24H',   align: 'right' as const },
            ].map(({ h, align }) => (
              <span key={h} style={{ fontSize: 7.5, color: 'rgba(255,255,255,0.22)', ...MONO, letterSpacing: '0.16em', textAlign: align }}>
                {h}
              </span>
            ))}
          </div>

          {/* Signal rows */}
          <div style={{ padding: '4px 18px 14px' }}>
            {topSignals.length === 0 ? (
              <div style={{ padding: '36px 0', textAlign: 'center', color: 'var(--dim)', fontSize: 12, ...MONO }}>
                No signals yet — run scan
              </div>
            ) : (
              topSignals.map(sig => (
                <SigRow key={sig.id} sig={sig} onClick={() => navigate(`/symbol/${sig.symbol}`)} />
              ))
            )}
          </div>
        </div>

        {/* ── CENTER: Market + Top Picks + Win Rates ─────────────────────── */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>

          {/* Crypto prices */}
          <div className="card" style={{ padding: '14px 16px' }}>
            <SectionLabel>Market</SectionLabel>
            {(['BTC', 'ETH', 'SOL'] as const).map((sym, i) => {
              const p   = prices?.[sym]
              const chg = p?.change_24h ?? 0
              const up  = chg >= 0
              return (
                <div key={sym} style={{
                  display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                  padding: '8px 0',
                  borderBottom: i < 2 ? '1px solid rgba(255,255,255,0.05)' : 'none',
                }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <div style={{
                      width: 26, height: 26, borderRadius: 7,
                      background: 'rgba(255,255,255,0.05)',
                      display: 'flex', alignItems: 'center', justifyContent: 'center',
                      fontSize: 9, fontWeight: 800, color: 'var(--dim)', ...MONO,
                    }}>
                      {sym[0]}
                    </div>
                    <div>
                      <div style={{ fontSize: 9, fontWeight: 700, color: 'rgba(255,255,255,0.28)', ...MONO, letterSpacing: '0.1em' }}>{sym}</div>
                      <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--text)', ...MONO, lineHeight: 1.1 }}>
                        {fmtPrice(sym, p?.price)}
                      </div>
                    </div>
                  </div>
                  <div style={{
                    padding: '3px 8px', borderRadius: 6,
                    background: up ? 'rgba(0,212,138,0.08)' : 'rgba(240,79,79,0.08)',
                    border: `1px solid ${up ? 'rgba(0,212,138,0.2)' : 'rgba(240,79,79,0.2)'}`,
                  }}>
                    <span style={{ fontSize: 11, fontWeight: 700, color: retColor(chg), ...MONO }}>
                      {chg >= 0 ? '▲' : '▼'} {Math.abs(chg).toFixed(1)}%
                    </span>
                  </div>
                </div>
              )
            })}
            {fg?.value && (
              <>
                <Divider style={{ margin: '10px 0 8px' }} />
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
                  <span style={{ fontSize: 8.5, color: 'rgba(255,255,255,0.28)', ...MONO, letterSpacing: '0.14em', textTransform: 'uppercase' }}>
                    Fear &amp; Greed
                  </span>
                  <div style={{ display: 'flex', alignItems: 'baseline', gap: 5 }}>
                    <span style={{ fontSize: 15, fontWeight: 800, color: fgColor, ...MONO }}>{fg.value}</span>
                    <span style={{ fontSize: 9, color: fgColor, ...MONO, opacity: 0.7 }}>{fg.classification}</span>
                  </div>
                </div>
                <div style={{ height: 3, borderRadius: 2, background: 'rgba(255,255,255,0.06)', overflow: 'hidden' }}>
                  <div style={{
                    height: '100%', width: `${fg.value}%`,
                    background: 'linear-gradient(90deg, #ef4444 0%, #f59e0b 50%, #00d48a 100%)',
                    borderRadius: 2,
                  }} />
                </div>
              </>
            )}
          </div>

          {/* Top Picks */}
          <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
            <div style={{ padding: '12px 16px 8px', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <SectionLabel style={{ marginBottom: 0 }}>Top Picks (6H)</SectionLabel>
              <span onClick={() => navigate('/leaderboard')} style={{ fontSize: 9, color: 'var(--blue)', cursor: 'pointer', ...MONO }}>
                Leaderboard →
              </span>
            </div>
            <div style={{ padding: '0 16px 12px' }}>
              {snap?.top_picks && snap.top_picks.length > 0 ? (
                snap.top_picks.slice(0, 6).map((p, i) => (
                  <div
                    key={p.symbol}
                    onClick={() => navigate(`/symbol/${p.symbol}`)}
                    style={{
                      display: 'grid', gridTemplateColumns: '16px 1fr auto auto',
                      alignItems: 'center', gap: 8,
                      padding: '6px 0',
                      borderBottom: i < Math.min(snap.top_picks.length, 6) - 1 ? '1px solid rgba(255,255,255,0.04)' : 'none',
                      cursor: 'pointer', transition: 'background 0.1s',
                    }}
                    onMouseEnter={e => (e.currentTarget.style.background = 'rgba(255,255,255,0.028)')}
                    onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
                  >
                    <span style={{ fontSize: 8, fontWeight: 700, ...MONO, color: 'rgba(255,255,255,0.22)', textAlign: 'right' }}>{i + 1}</span>
                    <span style={{ fontSize: 11.5, fontWeight: 700, ...MONO, color: 'var(--text)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      ${p.symbol}
                    </span>
                    <span style={{ fontSize: 11, fontWeight: 700, color: scoreColor(p.score ?? 0), ...MONO }}>{(p.score ?? 0).toFixed(0)}</span>
                    <span style={{ fontSize: 9.5, color: retColor(p.change_24h), ...MONO }}>{fmtPct(p.change_24h)}</span>
                  </div>
                ))
              ) : (
                <div style={{ padding: '14px 0', textAlign: 'center', color: 'var(--dim)', fontSize: 10.5, ...MONO }}>No picks yet</div>
              )}
            </div>
          </div>

          {/* Win rates */}
          <div className="card" style={{ padding: '14px 16px', cursor: 'pointer' }} onClick={() => navigate('/outcome-feed')}>
            <SectionLabel>7-Day Win Rates</SectionLabel>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {outcomes && ([
                { label: '1H',  data: outcomes.outcomes_1h  },
                { label: '4H',  data: outcomes.outcomes_4h  },
                { label: '24H', data: outcomes.outcomes_24h },
              ]).map(({ label, data: d }) => {
                const r = d?.win_rate ?? 0
                const c = winRateColor(r)
                return (
                  <div key={label}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 4 }}>
                      <span style={{ fontSize: 8.5, color: 'rgba(255,255,255,0.28)', ...MONO, letterSpacing: '0.14em' }}>{label}</span>
                      <div style={{ display: 'flex', gap: 7, alignItems: 'baseline' }}>
                        <span style={{ fontSize: 14, fontWeight: 700, color: c, ...MONO, lineHeight: 1 }}>
                          {d?.win_rate != null ? `${d.win_rate.toFixed(0)}%` : '—'}
                        </span>
                        <span style={{ fontSize: 9.5, color: retColor(d?.avg), ...MONO }}>{fmtPct(d?.avg)}</span>
                      </div>
                    </div>
                    <div style={{ height: 3, borderRadius: 2, background: 'rgba(255,255,255,0.06)', overflow: 'hidden' }}>
                      <div style={{ height: '100%', width: `${Math.min(100, r)}%`, background: c, borderRadius: 2, transition: 'width 0.4s ease' }} />
                    </div>
                  </div>
                )
              })}
            </div>
          </div>
        </div>

        {/* ── RIGHT: Engine + Readiness + Positions ─────────────────────── */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>

          {/* Engine state */}
          <div
            className="card"
            style={{ padding: '14px 16px', cursor: 'pointer', transition: 'border-color 0.15s' }}
            onClick={() => navigate('/risk')}
            onMouseEnter={e => (e.currentTarget.style.borderColor = 'rgba(255,255,255,0.14)')}
            onMouseLeave={e => (e.currentTarget.style.borderColor = '')}
          >
            <SectionLabel>Engine</SectionLabel>
            <div style={{ display: 'flex', alignItems: 'center', gap: 9, marginBottom: 12 }}>
              <div style={{
                width: 7, height: 7, borderRadius: '50%',
                background: engColor, boxShadow: `0 0 8px ${engColor}`,
                animation: 'pulse-glow 2.5s ease-in-out infinite', flexShrink: 0,
              }} />
              <span style={{ fontSize: 15, fontWeight: 800, color: engColor, ...MONO, letterSpacing: '0.02em' }}>
                {riskMode || '—'}
              </span>
              {risk?.paused && (
                <span style={{
                  fontSize: 8, fontWeight: 700, padding: '1px 5px', borderRadius: 4,
                  background: 'rgba(239,68,68,0.12)', color: 'var(--red)',
                  border: '1px solid rgba(239,68,68,0.25)', ...MONO,
                  animation: 'blink 1.8s ease-in-out infinite',
                }}>
                  PAUSED
                </span>
              )}
            </div>

            {[
              { label: 'Win Streak', value: `${risk?.streak ?? 0} signals`, color: risk?.streak ? 'var(--green)' : undefined },
              { label: 'Size',       value: `${Math.round((risk?.size_multiplier ?? 1) * 100)}%`,
                color: (risk?.size_multiplier ?? 1) >= 1 ? 'var(--green)' : (risk?.size_multiplier ?? 1) >= 0.75 ? 'var(--amber)' : 'var(--red)' },
              { label: 'Threshold',  value: `+${risk?.threshold_delta ?? 0} pts` },
              { label: 'Min Grade',  value: `${risk?.min_confidence ?? '—'}+` },
            ].map(({ label, value, color }, i, arr) => (
              <div key={label}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '5px 0' }}>
                  <span style={{ fontSize: 9.5, color: 'rgba(255,255,255,0.32)', ...MONO }}>{label}</span>
                  <span style={{ fontSize: 11, fontWeight: 600, color: color || 'var(--text)', ...MONO }}>{value}</span>
                </div>
                {i < arr.length - 1 && <Divider />}
              </div>
            ))}
          </div>

          {/* Readiness gauge */}
          {readiness && (
            <div
              className="card"
              style={{ padding: '14px 16px', cursor: 'pointer', transition: 'border-color 0.15s' }}
              onClick={() => navigate('/performance')}
              onMouseEnter={e => (e.currentTarget.style.borderColor = 'rgba(255,255,255,0.14)')}
              onMouseLeave={e => (e.currentTarget.style.borderColor = '')}
            >
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
                <SectionLabel style={{ marginBottom: 0 }}>Readiness</SectionLabel>
                <span style={{ fontSize: 8.5, color: 'rgba(255,255,255,0.28)', ...MONO }}>{readiness.gates_passed}/{readiness.gates_total} gates</span>
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                {/* Arc gauge */}
                <div style={{ position: 'relative', width: 48, height: 48, flexShrink: 0 }}>
                  <svg viewBox="0 0 48 48" width="48" height="48">
                    <circle cx="24" cy="24" r="19" fill="none" stroke="rgba(255,255,255,0.07)" strokeWidth="4.5" />
                    <circle
                      cx="24" cy="24" r="19" fill="none"
                      stroke={readinessColor(readiness.score)}
                      strokeWidth="4.5"
                      strokeLinecap="round"
                      strokeDasharray={`${(Math.round(readiness.score ?? 0) / 100) * 119.4} 119.4`}
                      transform="rotate(-90 24 24)"
                      opacity={0.85}
                    />
                  </svg>
                  <div style={{
                    position: 'absolute', inset: 0,
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                  }}>
                    <span style={{ fontSize: 12, fontWeight: 800, color: readinessColor(readiness.score ?? 0), ...MONO, lineHeight: 1 }}>
                      {Math.round(readiness.score ?? 0)}
                    </span>
                  </div>
                </div>
                <div>
                  <div style={{ fontSize: 12, fontWeight: 800, color: readinessColor(readiness.score), ...MONO }}>
                    {readiness.status.replace('_', ' ')}
                  </div>
                  <div style={{ fontSize: 8.5, color: 'rgba(255,255,255,0.28)', ...MONO, marginTop: 3, lineHeight: 1.5 }}>
                    WR {readiness.metrics?.win_rate_4h != null ? readiness.metrics.win_rate_4h.toFixed(0) : '—'}%<br />
                    DD {readiness.metrics?.max_drawdown_pct != null ? readiness.metrics.max_drawdown_pct.toFixed(1) : '—'}%
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* Market Cycle card */}
          {cycleSummary && (
            <div className="card" style={{ padding: '14px 16px' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
                <SectionLabel style={{ marginBottom: 0 }}>Market Cycle</SectionLabel>
                <span style={{
                  fontSize: 9, fontWeight: 700, padding: '2px 7px', borderRadius: 4,
                  background: `${cycleColor(cycleSummary.current_phase)}22`,
                  color: cycleColor(cycleSummary.current_phase),
                  border: `1px solid ${cycleColor(cycleSummary.current_phase)}44`,
                  ...MONO,
                }}>
                  {cycleEmoji(cycleSummary.current_phase)} {cycleSummary.current_phase}
                </span>
              </div>

              {/* Phase rows: BEAR / TRANS / BULL */}
              <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                {(['BEAR', 'TRANSITION', 'BULL'] as const).map(phase => {
                  const pb = cycleSummary.playbooks[phase]
                  const isActive = cycleSummary.current_phase === phase
                  const cc = cycleColor(phase)
                  const n = pb?.sample_size ?? 0
                  const wr = pb?.win_rate_4h
                  return (
                    <div key={phase} style={{
                      display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                      padding: '5px 7px', borderRadius: 5,
                      background: isActive ? `${cc}11` : 'transparent',
                      border: `1px solid ${isActive ? cc + '33' : 'transparent'}`,
                      gap: 6,
                    }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 5, minWidth: 0 }}>
                        {isActive && <div style={{ width: 4, height: 4, borderRadius: '50%', background: cc, boxShadow: `0 0 5px ${cc}`, flexShrink: 0 }} />}
                        <span style={{ fontSize: 8.5, fontWeight: 700, color: isActive ? cc : 'rgba(255,255,255,0.35)', ...MONO, letterSpacing: '0.1em' }}>
                          {cycleEmoji(phase)} {phase === 'TRANSITION' ? 'TRANS' : phase}
                        </span>
                      </div>
                      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexShrink: 0 }}>
                        <span style={{ fontSize: 9.5, color: wr != null ? (wr >= 50 ? 'var(--green)' : 'var(--red)') : 'var(--dim)', ...MONO, fontWeight: 700 }}>
                          {wr != null ? `${wr.toFixed(0)}% WR` : '—'}
                        </span>
                        <span style={{ fontSize: 8, color: 'var(--dim)', ...MONO }}>
                          {n > 0 ? `${n}n` : 'no data'}
                        </span>
                      </div>
                    </div>
                  )
                })}
              </div>

              {/* 14d phase timeline */}
              {cycleSummary.history_14d.length > 0 && (
                <div style={{ marginTop: 9, paddingTop: 8, borderTop: '1px solid rgba(255,255,255,0.06)' }}>
                  <div style={{ fontSize: 7.5, color: 'rgba(255,255,255,0.2)', ...MONO, letterSpacing: '0.15em', marginBottom: 5, textTransform: 'uppercase' }}>
                    14d history
                  </div>
                  <div style={{ display: 'flex', gap: 1.5, alignItems: 'flex-end', height: 18 }}>
                    {cycleSummary.history_14d.map((d, i) => (
                      <div
                        key={i}
                        title={`${d.date}: ${d.phase} (${d.avg_regime_score})`}
                        style={{
                          flex: 1, height: '100%', borderRadius: 2,
                          background: cycleColor(d.phase),
                          opacity: d.phase === cycleSummary.current_phase ? 0.9 : 0.35,
                        }}
                      />
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}

          {/* Open positions */}
          <div
            className="card"
            style={{ padding: '14px 16px', cursor: 'pointer', transition: 'border-color 0.15s' }}
            onClick={() => navigate('/positions')}
            onMouseEnter={e => (e.currentTarget.style.borderColor = 'rgba(255,255,255,0.14)')}
            onMouseLeave={e => (e.currentTarget.style.borderColor = '')}
          >
            <SectionLabel>Positions</SectionLabel>
            {snap?.open_positions && snap.open_positions.length > 0 ? (
              <>
                <div style={{ marginBottom: 10, display: 'flex', alignItems: 'baseline', gap: 6 }}>
                  <span style={{ fontSize: 30, fontWeight: 800, ...MONO, color: 'var(--text)', lineHeight: 1 }}>
                    {snap.open_positions.length}
                  </span>
                  <span style={{ fontSize: 10, color: 'var(--muted)', ...MONO }}>open</span>
                </div>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                  {snap.open_positions.slice(0, 8).map(p => (
                    <span key={p.symbol} style={{
                      fontSize: 8.5, padding: '2px 7px', borderRadius: 5,
                      background: 'rgba(0,212,138,0.08)', color: 'var(--green)',
                      border: '1px solid rgba(0,212,138,0.18)',
                      ...MONO, fontWeight: 600,
                    }}>
                      ${p.symbol}
                    </span>
                  ))}
                </div>
              </>
            ) : (
              <>
                <span style={{ fontSize: 30, fontWeight: 800, ...MONO, color: 'var(--dim)', lineHeight: 1, display: 'block' }}>0</span>
                <span style={{ fontSize: 9.5, color: 'var(--dim)', ...MONO, marginTop: 5, display: 'block' }}>No open positions</span>
              </>
            )}
            <div style={{ marginTop: 10, paddingTop: 8, borderTop: '1px solid rgba(255,255,255,0.06)', display: 'flex', gap: 12 }}>
              <span style={{ fontSize: 9, color: 'var(--blue)', ...MONO }}>Journal →</span>
              <span
                onClick={e => { e.stopPropagation(); navigate('/trading/spot-paper') }}
                style={{ fontSize: 9, color: 'var(--green)', ...MONO, cursor: 'pointer' }}
              >Spot Paper →</span>
              <span
                onClick={e => { e.stopPropagation(); navigate('/trading/perps-paper') }}
                style={{ fontSize: 9, color: '#e879f9', ...MONO, cursor: 'pointer' }}
              >Perps →</span>
            </div>
          </div>

          {/* Second Leg Sniper */}
          <div style={{ marginTop: 16 }}>
            <SniperPanel />
          </div>

        </div>
      </div>
    </div>
  )
}
