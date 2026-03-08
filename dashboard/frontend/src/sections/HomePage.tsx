import { useQuery } from '@tanstack/react-query'
import { api } from '../api'

// ── Types ─────────────────────────────────────────────────────────────────────

interface FundingSymbol {
  rate: number
  mark_price: number | null
  next_funding_ts: number | null
  source: string
}

interface FundingData {
  rates: Record<string, FundingSymbol>
  ts: number | null
}

interface HomeSummary {
  tiers: {
    mode: string; positions: number; collateral_usd: number
    buffer_usd: number; tp_cycles: number
  }
  memecoins: {
    mode: string; outcomes: number; next_milestone: number
    wr_pct: number | null; fg_value: number | null; fg_ok: boolean
  }
  spot: {
    mode: string; outcomes: number; live_buys: number; basket_size: number
  }
  whale_watch: {
    total: number; in_range: number; scanner_pass: number
    alerts_sent: number; last_ts: string | null
  }
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmtUsd(n: number | null | undefined): string {
  if (n == null) return '—'
  if (Math.abs(n) >= 1_000_000) return `$${(n / 1_000_000).toFixed(1)}M`
  if (Math.abs(n) >= 1_000)     return `$${(n / 1_000).toFixed(1)}K`
  return `$${n.toFixed(0)}`
}

function fmtAge(ts: string | null): string {
  if (!ts) return '—'
  const diff = (Date.now() - new Date(ts + 'Z').getTime()) / 1000
  if (diff < 60)    return `${Math.floor(diff)}s ago`
  if (diff < 3600)  return `${Math.floor(diff / 60)}m ago`
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`
  return `${Math.floor(diff / 86400)}d ago`
}

// ── System card colors ────────────────────────────────────────────────────────

const C = {
  tiers:       { main: '#00d48a', bg: 'rgba(0,212,138,0.05)',   border: 'rgba(0,212,138,0.18)'  },
  memecoins:   { main: '#60a5fa', bg: 'rgba(96,165,250,0.05)',  border: 'rgba(96,165,250,0.18)' },
  spot:        { main: '#f59e0b', bg: 'rgba(245,158,11,0.05)',  border: 'rgba(245,158,11,0.18)' },
  whale_watch: { main: '#a78bfa', bg: 'rgba(167,139,250,0.05)', border: 'rgba(167,139,250,0.18)'},
}

// ── System Card ───────────────────────────────────────────────────────────────

function SystemCard({ sys, mode, modeColor, title, children }: {
  sys: keyof typeof C
  mode: string
  modeColor: string
  title: string
  children: React.ReactNode
}) {
  const c = C[sys]
  return (
    <div style={{
      flex: '1 1 0', minWidth: 220,
      background: c.bg,
      border: `1px solid ${c.border}`,
      borderTop: `2px solid ${c.main}`,
      borderRadius: '0 0 12px 12px',
      padding: '14px 16px',
      display: 'flex', flexDirection: 'column', gap: 10,
      backdropFilter: 'blur(20px) saturate(160%)',
      WebkitBackdropFilter: 'blur(20px) saturate(160%)',
    }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <span style={{
          color: c.main,
          fontFamily: 'JetBrains Mono, monospace',
          fontWeight: 700, fontSize: 10, letterSpacing: '0.14em',
        }}>
          {title}
        </span>
        <span className="badge" style={{
          color: modeColor,
          background: `${modeColor}18`,
          border: `1px solid ${modeColor}44`,
          fontSize: 9,
        }}>
          {mode}
        </span>
      </div>
      {/* Content */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
        {children}
      </div>
    </div>
  )
}

function Metric({ label, value, color, sub }: {
  label: string; value: string | number; color?: string; sub?: string
}) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 8 }}>
      <span style={{ color: 'var(--muted)', fontSize: 10, fontFamily: 'JetBrains Mono, monospace', flexShrink: 0 }}>
        {label}
      </span>
      <span style={{ display: 'flex', alignItems: 'baseline', gap: 5 }}>
        {sub && <span style={{ color: 'var(--dim)', fontSize: 9 }}>{sub}</span>}
        <span style={{
          color: color ?? 'var(--text2)',
          fontFamily: 'JetBrains Mono, monospace',
          fontWeight: 600, fontSize: 12,
        }}>
          {value}
        </span>
      </span>
    </div>
  )
}

function MiniBar({ pct, color }: { pct: number; color: string }) {
  return (
    <div className="mini-bar-track" style={{ marginTop: 2 }}>
      <div className="mini-bar-fill" style={{
        width: `${Math.min(100, Math.max(0, pct))}%`,
        background: color,
      }} />
    </div>
  )
}

// ── Funding helpers ───────────────────────────────────────────────────────────

function fmtFundingRate(rate: number): { text: string; color: string } {
  const pct  = rate * 100
  const sign = pct >= 0 ? '+' : ''
  const text = `${sign}${pct.toFixed(4)}%/8h`
  let color: string
  if (rate < -0.0003)      color = '#60a5fa'          // blue  — negative, shorts dominating
  else if (rate < 0.0003)  color = 'var(--green)'     // green — neutral / normal
  else if (rate < 0.001)   color = '#f59e0b'           // amber — elevated
  else                     color = 'var(--red)'        // red   — overheated
  return { text, color }
}

function fmtNextFunding(ts: number | null): string {
  if (!ts) return '—'
  const h = (ts - Date.now()) / 3_600_000
  if (h <= 0) return 'now'
  if (h < 1)  return `${Math.round(h * 60)}m`
  return `${h.toFixed(1)}h`
}

// ── Funding Panel ─────────────────────────────────────────────────────────────

function FundingPanel({ data, loading }: { data: FundingData | undefined; loading: boolean }) {
  const SYMS = ['SOL', 'BTC', 'ETH']
  const staleMin = data?.ts ? Math.round((Date.now() / 1000 - data.ts) / 60) : null

  return (
    <div style={{
      background: 'rgba(255,255,255,0.02)',
      border: '1px solid rgba(255,255,255,0.08)',
      borderTop: '2px solid rgba(255,255,255,0.10)',
      borderRadius: '0 0 12px 12px',
      padding: '12px 16px',
      backdropFilter: 'blur(20px) saturate(160%)',
      WebkitBackdropFilter: 'blur(20px) saturate(160%)',
      display: 'flex',
      alignItems: 'center',
      gap: 16,
      flexWrap: 'wrap',
    }}>
      {/* Title block */}
      <div style={{ flexShrink: 0, minWidth: 100 }}>
        <div style={{
          color: 'var(--text2)', fontFamily: 'JetBrains Mono, monospace',
          fontWeight: 700, fontSize: 10, letterSpacing: '0.14em', marginBottom: 4,
        }}>
          MARKET CONDITIONS
        </div>
        <div style={{
          color: 'var(--dim)', fontFamily: 'JetBrains Mono, monospace',
          fontSize: 9, letterSpacing: '0.06em',
        }}>
          PERP FUNDING · OKX
        </div>
      </div>

      {/* Divider */}
      <div style={{ width: 1, alignSelf: 'stretch', background: 'rgba(255,255,255,0.07)' }} />

      {/* 3 symbol columns */}
      <div style={{ flex: 1, display: 'flex' }}>
        {SYMS.map((sym, i) => {
          const d = data?.rates?.[sym]
          const { text, color } = d != null ? fmtFundingRate(d.rate) : { text: loading ? '…' : '—', color: 'var(--dim)' }
          const nextStr = d ? fmtNextFunding(d.next_funding_ts) : '—'
          return (
            <div key={sym} style={{
              flex: 1,
              borderLeft: i === 0 ? 'none' : '1px solid rgba(255,255,255,0.05)',
              paddingLeft: i === 0 ? 0 : 20,
              paddingRight: 20,
              display: 'flex', flexDirection: 'column', gap: 3,
            }}>
              <span style={{
                color: 'var(--dim)', fontFamily: 'JetBrains Mono, monospace',
                fontWeight: 700, fontSize: 9, letterSpacing: '0.12em',
              }}>
                {sym}
              </span>
              <span style={{
                color, fontFamily: 'JetBrains Mono, monospace',
                fontWeight: 700, fontSize: 13,
              }}>
                {text}
              </span>
              <span style={{
                color: 'var(--dim)', fontFamily: 'JetBrains Mono, monospace', fontSize: 9,
              }}>
                next {nextStr}
              </span>
            </div>
          )
        })}
      </div>

      {/* Staleness */}
      {staleMin != null && (
        <div style={{
          flexShrink: 0,
          color: staleMin > 35 ? 'var(--red)' : 'var(--dim)',
          fontFamily: 'JetBrains Mono, monospace',
          fontSize: 8,
        }}>
          {staleMin}m ago
        </div>
      )}
    </div>
  )
}

// ── Main ──────────────────────────────────────────────────────────────────────

export function HomePage() {
  const summary = useQuery<HomeSummary>({
    queryKey: ['home-summary'],
    queryFn:  () => api.get('/home/summary').then(r => r.data),
    refetchInterval: 60_000,
    staleTime: 30_000,
  })

  const fundingQ = useQuery<FundingData>({
    queryKey: ['funding-current'],
    queryFn:  () => api.get('/funding/current').then(r => r.data),
    refetchInterval: 120_000,
    staleTime: 60_000,
  })

  const s       = summary.data
  const loading = summary.isLoading

  const msPct = s
    ? (s.memecoins.outcomes / (s.memecoins.next_milestone || 1)) * 100
    : 0

  return (
    <div style={{
      maxWidth: 1100, margin: '0 auto',
      padding: '20px 20px',
      display: 'flex', flexDirection: 'column', gap: 14,
    }}>

      {/* ── Page header ──────────────────────────────────────────────────── */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <span style={{
          color: 'var(--text2)', fontFamily: 'JetBrains Mono, monospace',
          fontWeight: 700, fontSize: 13, letterSpacing: '0.14em',
        }}>
          OVERVIEW
        </span>
        <span className="badge" style={{
          color: 'var(--green)', background: 'rgba(0,212,138,0.1)',
          border: '1px solid rgba(0,212,138,0.25)', fontSize: 9,
        }}>
          LIVE
        </span>
        <span style={{ color: 'var(--dim)', fontFamily: 'JetBrains Mono, monospace', fontSize: 10 }}>
          all systems · real-time status
        </span>
      </div>

      {/* ── 4 System Cards ──────────────────────────────────────────────── */}
      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>

        {/* Perp Tiers */}
        <SystemCard sys="tiers" title="PERP TIERS" mode="LIVE" modeColor="var(--green)">
          <Metric label="collateral" value={loading ? '…' : fmtUsd(s?.tiers.collateral_usd)} />
          <Metric label="positions"  value={loading ? '…' : s?.tiers.positions ?? '—'} />
          <Metric
            label="profit buffer"
            value={loading ? '…' : fmtUsd(s?.tiers.buffer_usd)}
            color={s?.tiers.buffer_usd ? 'var(--green)' : 'var(--dim)'}
          />
          <Metric
            label="TP cycles"
            value={loading ? '…' : s?.tiers.tp_cycles ?? '—'}
            color={s?.tiers.tp_cycles ? 'var(--green)' : 'var(--dim)'}
          />
        </SystemCard>

        {/* Memecoin Scanner */}
        <SystemCard sys="memecoins" title="MEMECOIN SCAN" mode="PAPER" modeColor="var(--amber)">
          <Metric
            label="outcomes"
            value={loading ? '…' : s ? `${s.memecoins.outcomes} / ${s.memecoins.next_milestone}` : '—'}
            color="var(--blue)"
          />
          {s && <MiniBar pct={msPct} color="linear-gradient(90deg,#60a5fa,#a78bfa)" />}
          <Metric
            label="win rate (GOOD)"
            value={s?.memecoins.wr_pct != null ? `${s.memecoins.wr_pct}%` : '—'}
            color={s?.memecoins.wr_pct != null
              ? s.memecoins.wr_pct >= 40 ? 'var(--green)' : 'var(--amber)'
              : undefined}
          />
          <Metric
            label="F&G gate"
            value={s?.memecoins.fg_value != null ? `${s.memecoins.fg_value}` : '—'}
            color={s?.memecoins.fg_ok ? 'var(--green)' : 'var(--red)'}
            sub={s?.memecoins.fg_ok ? '✓' : '✗'}
          />
        </SystemCard>

        {/* Spot Accumulation */}
        <SystemCard sys="spot" title="SPOT ACCUM" mode="PAPER" modeColor="var(--amber)">
          <Metric label="basket tokens"   value={loading ? '…' : s?.spot.basket_size ?? '—'} />
          <Metric label="signal outcomes" value={loading ? '…' : s?.spot.outcomes ?? '—'} />
          <Metric
            label="live buys"
            value={loading ? '…' : s?.spot.live_buys ?? '—'}
            color={s?.spot.live_buys ? 'var(--green)' : 'var(--dim)'}
          />
          <Metric label="next gate" value="20 outcomes" color="var(--dim)" />
        </SystemCard>

        {/* Whale Watch */}
        <SystemCard sys="whale_watch" title="WHALE WATCH" mode="OBS" modeColor="var(--purple)">
          <Metric label="total alerts"    value={loading ? '…' : s?.whale_watch.total ?? '—'} />
          <Metric label="in range"        value={loading ? '…' : s?.whale_watch.in_range ?? '—'} />
          <Metric
            label="scanner pass"
            value={loading ? '…' : s?.whale_watch.scanner_pass ?? '—'}
            color={s?.whale_watch.scanner_pass ? 'var(--green)' : 'var(--dim)'}
          />
          <Metric
            label="last alert"
            value={loading ? '…' : fmtAge(s?.whale_watch.last_ts ?? null)}
            color="var(--text2)"
          />
        </SystemCard>

      </div>

      {/* ── Market Conditions ────────────────────────────────────────────── */}
      <FundingPanel data={fundingQ.data} loading={fundingQ.isLoading} />

    </div>
  )
}
