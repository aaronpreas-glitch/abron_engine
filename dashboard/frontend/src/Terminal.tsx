import { useQuery } from '@tanstack/react-query'
import { Bell, LogOut } from 'lucide-react'
import { Suspense, lazy, useEffect, useRef, useState } from 'react'
import type { CSSProperties } from 'react'
import { api, getDashboardRequestMetrics } from './api'
import { liveBudgetedInterval, slowBudgetedInterval } from './queryBudget'
import type { JupiterPosition } from './sections/WalletSection'

const HomePage = lazy(() =>
  import('./sections/HomePage').then(m => ({ default: m.HomePage })),
)
const MemecoinsPage = lazy(() =>
  import('./sections/MemecoinsPage').then(m => ({ default: m.MemecoinsPage })),
)
const SystemPage = lazy(() =>
  import('./sections/SystemPage').then(m => ({ default: m.SystemPage })),
)
const AuditPage = lazy(() =>
  import('./sections/AuditPage').then(m => ({ default: m.AuditPage })),
)

interface SystemModes {
  perp: string
  memecoins: string
  spot: string
}

// ── Types ────────────────────────────────────────────────────────────────────

export interface ChecklistItem {
  id: string; label: string; pass: boolean; value: number | string; target: number | string
}
export interface BullReadiness {
  score: number; label: string
  components: Record<string, { pts: number; max: number; value: number; label: string }>
}
export interface Agent {
  name: string; health: 'alive' | 'slow' | 'stalled' | 'init'
  interval_s: number; last_beat_ago_s: number | null; status: string
}
export interface MemoryEntry { ts: string; agent: string; message: string }

export interface ClosedTrade {
  id?: number
  symbol: string
  pnl_pct: number
  exit_reason: string
  closed_ts_utc: string
  side?: string
}

export interface PerpsStatus {
  dry_run?: boolean
  open_positions: number
  market_regime?: string
  enabled?: boolean
}

// ── Mode pill helper ──────────────────────────────────────────────────────────

function ModePill({ label, mode }: { label: string; mode: string }) {
  const isLive = mode === 'LIVE'
  const color = isLive ? '#00d48a' : mode === 'SIM' ? '#f59e0b' : '#60a5fa'
  return (
    <span className="mode-pill" style={{ '--mode-color': color } as CSSProperties}>
      <span style={{ color: 'var(--chrome)' }}>{label}</span>
      <span style={{ color, fontWeight: 700 }}>:{mode}</span>
    </span>
  )
}

function PageLoadingFallback() {
  return (
    <div style={{
      maxWidth: 1200,
      margin: '0 auto',
      padding: '20px 24px',
      color: 'var(--chrome)',
      fontSize: 10,
      fontFamily: 'JetBrains Mono, monospace',
      letterSpacing: '0.08em',
    }}>
      LOADING PAGE…
    </div>
  )
}

// ── Notifications ─────────────────────────────────────────────────────────────

function notify(title: string, body: string) {
  if (!('Notification' in window) || Notification.permission !== 'granted') return
  try {
    new Notification(title, { body, icon: '/vite.svg', silent: false })
  } catch {
    // silent fail
  }
}

// ── Terminal ──────────────────────────────────────────────────────────────────

interface Props {
  onLogout: () => void
}

type Page = 'home' | 'system' | 'memecoins' | 'audit'

const PAGE_LABEL: Record<Page, string> = {
  home: 'Home',
  system: 'System',
  memecoins: 'Memecoins',
  audit: 'Ops',
}

const PRIMARY_PAGES: Page[] = ['home', 'system', 'audit']

export function Terminal({ onLogout }: Props) {
  const [page, setPage] = useState<Page>('home')
  const [requestMetrics, setRequestMetrics] = useState(getDashboardRequestMetrics)

  useEffect(() => {
    if ('Notification' in window && Notification.permission === 'default') {
      Notification.requestPermission()
    }
  }, [])

  useEffect(() => {
    const id = window.setInterval(() => setRequestMetrics(getDashboardRequestMetrics()), 5_000)
    return () => window.clearInterval(id)
  }, [])

  // ── Data fetches ──────────────────────────────────────────────────────────

  const modesQuery = useQuery<SystemModes>({
    queryKey: ['system-modes'],
    queryFn: async () => {
      const r = await api.get('/home/modes')
      return r.data
    },
    refetchInterval: slowBudgetedInterval(120_000),
    staleTime: 60_000,
  })

  const modes = modesQuery.data
  const perpsMode = String(modes?.perp || '').toUpperCase()
  const perpsHeaderActive = perpsMode === 'LIVE' || perpsMode === 'SIM'

  const perpsStatus = useQuery<PerpsStatus>({
    queryKey: ['perps-status'],
    queryFn: async () => {
      const r = await api.get('/perps/status')
      return r.data
    },
    refetchInterval: slowBudgetedInterval(300_000, perpsHeaderActive),
    staleTime: 120_000,
    enabled: perpsHeaderActive,
  })

  const perpsOpen = (perpsStatus.data?.open_positions ?? 0) > 0

  const closedTrades = useQuery<ClosedTrade[]>({
    queryKey: ['closed-trades'],
    queryFn: async () => {
      const r = await api.get('/perps/closed?limit=20')
      return r.data?.trades ?? r.data ?? []
    },
    refetchInterval: slowBudgetedInterval(300_000, perpsOpen),
    staleTime: 120_000,
    enabled: perpsOpen,
  })

  const walletQuery = useQuery<{
    wallet: string
    positions: JupiterPosition[]
    sol_balance: number | null
    error: string | null
  }>({
    queryKey: ['wallet'],
    queryFn: async () => {
      const r = await api.get('/wallet/positions')
      return r.data
    },
    refetchInterval: liveBudgetedInterval(120_000),
    staleTime: 60_000,
  })

  // ── Trade notifications ───────────────────────────────────────────────────
  const prevOpenCount = useRef<number | null>(null)
  const lastClosedId  = useRef<number | string | null>(null)
  const initialized   = useRef(false)

  useEffect(() => {
    const openCount = perpsStatus.data?.open_positions
    const topTrade  = closedTrades.data?.[0]
    if (openCount === undefined) return

    if (!initialized.current) {
      prevOpenCount.current = openCount
      lastClosedId.current  = topTrade?.id ?? topTrade?.closed_ts_utc ?? null
      initialized.current   = true
      return
    }

    if (prevOpenCount.current !== null && openCount > prevOpenCount.current) {
      const diff = openCount - prevOpenCount.current
      notify('📈 ABRON ENGINE — Trade Opened',
        `${diff} position${diff > 1 ? 's' : ''} opened · ${openCount} open total`)
    }

    const topId = topTrade?.id ?? topTrade?.closed_ts_utc ?? null
    if (topTrade && topId !== lastClosedId.current && lastClosedId.current !== null) {
      const pnl  = topTrade.pnl_pct > 0 ? `+${topTrade.pnl_pct.toFixed(2)}%` : `${topTrade.pnl_pct.toFixed(2)}%`
      const icon = topTrade.pnl_pct > 0 ? '✅' : '❌'
      notify(`${icon} ABRON ENGINE — Trade Closed`,
        `${topTrade.symbol} ${topTrade.side ?? 'LONG'}  ${pnl}  ${topTrade.exit_reason}`)
      lastClosedId.current = topId
    }

    prevOpenCount.current = openCount
  }, [perpsStatus.data, closedTrades.data])

  // ── Derived ───────────────────────────────────────────────────────────────

  const isDryRun = perpsStatus.data?.dry_run !== false
  const regime   = perpsStatus.data?.market_regime ?? '—'
  const engineOn = perpsStatus.data?.enabled !== false

  const walletPositions = walletQuery.data?.positions ?? []
  const perpPnlUsd   = walletPositions.reduce((s, p) => s + p.pnl_usd, 0)
  const perpValueUsd = walletPositions.reduce((s, p) => s + p.value_usd, 0)
  const hasPerpValue = walletPositions.length > 0

  const solBalance  = walletQuery.data?.sol_balance ?? null
  const netUsd = hasPerpValue ? perpValueUsd : 0

  const dashboardTone =
    requestMetrics.failed_60s > 0 ? '#ef4444'
    : requestMetrics.slow_60s > 0 ? '#f59e0b'
    : requestMetrics.requests_60s > 45 ? '#f59e0b'
    : '#00d48a'

  // ── Layout ────────────────────────────────────────────────────────────────

  return (
    <div className="terminal-shell" style={{
      minHeight: '100vh',
      color: '#e2e8f0',
      fontFamily: 'JetBrains Mono, monospace',
      fontSize: 12,
    }}>

      {/* ── Header ── */}
      <div className="top-bar terminal-top-bar" style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        padding: '0 20px', position: 'sticky', top: 0, zIndex: 10,
      }}>
        <div className="terminal-status-strip" style={{ display: 'flex', alignItems: 'center', gap: 16 }}>

          {/* Engine name + status dot */}
          <div className="brand-lockup" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span style={{
              width: 7, height: 7, borderRadius: '50%',
              background: engineOn && !isDryRun ? '#00d48a' : isDryRun ? '#f59e0b' : '#ef4444',
              display: 'inline-block',
              boxShadow: engineOn && !isDryRun ? '0 0 6px #00d48a88' : 'none',
            }} />
            <span style={{ color: 'var(--text)', fontWeight: 700, letterSpacing: '0.14em', fontSize: 11 }}>
              ABRON ENGINE
            </span>
          </div>

          <span className="top-sep" style={{ color: 'var(--sep)' }}>|</span>

          {/* Mode pills */}
          <div className="mode-pill-row" style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
            <ModePill label="PERP" mode={modes?.perp ?? (isDryRun ? 'SIM' : 'LIVE')} />
            <ModePill label="MEME" mode={modes?.memecoins ?? 'PAPER'} />
            <ModePill label="SPOT" mode={modes?.spot ?? 'PAPER'} />
          </div>

          <span className="top-sep" style={{ color: 'var(--sep)' }}>|</span>

          {regime !== '—' && (
            <>
              <span style={{
                color: regime.toLowerCase().includes('bull') ? '#00d48a'
                  : regime.toLowerCase().includes('bear') ? '#ef4444'
                  : '#a0aec0',
                fontSize: 10,
              }}>
                {regime.toUpperCase()}
              </span>
              <span className="top-sep" style={{ color: 'var(--sep)' }}>|</span>
            </>
          )}

          {/* SOL spot */}
          {solBalance !== null && (
            <>
              <span style={{ fontSize: 11 }}>
                <span style={{ color: 'var(--chrome)' }}>SOL </span>
                <span style={{ color: '#8a9ab0', fontWeight: 700 }}>{solBalance.toFixed(3)}</span>
              </span>
              <span className="top-sep" style={{ color: 'var(--sep)' }}>|</span>
            </>
          )}

          {/* PERP PnL */}
          {hasPerpValue && (
            <>
              <span style={{ fontSize: 11 }}>
                <span style={{ color: 'var(--chrome)' }}>PERP </span>
                <span style={{ color: perpPnlUsd >= 0 ? '#00d48a' : '#ef4444', fontWeight: 700 }}>
                  {perpPnlUsd >= 0 ? '+' : ''}${perpPnlUsd.toFixed(2)}
                </span>
                <span style={{ color: 'var(--recessed)', fontSize: 10 }}> · ${perpValueUsd.toFixed(0)}</span>
              </span>
              <span className="top-sep" style={{ color: 'var(--sep)' }}>|</span>
            </>
          )}

          {/* Net total */}
          {netUsd > 0 && (
            <span style={{ fontSize: 10, color: 'var(--recessed)' }}>
              NET <span style={{ color: '#5a7a9a', fontWeight: 700 }}>${netUsd.toLocaleString('en-US', { maximumFractionDigits: 0 })}</span>
            </span>
          )}

          <span className="top-sep" style={{ color: 'var(--sep)' }}>|</span>
          <span
            title={`Dashboard requests last 60s: ${requestMetrics.requests_60s}; slow: ${requestMetrics.slow_60s}; failed: ${requestMetrics.failed_60s}; inflight: ${requestMetrics.inflight}`}
            className="ui-health-pill"
            style={{ color: dashboardTone }}
          >
            UI {requestMetrics.requests_60s}/m · {requestMetrics.avg_ms}ms
          </span>
        </div>

        <div className="terminal-nav-strip" style={{ display: 'flex', alignItems: 'center', gap: 14 }}>

          {/* ── Page tabs ── */}
          <div className="nav-tabs-row">
            {PRIMARY_PAGES.map(p => (
              <button
                key={p}
                onClick={() => setPage(p)}
                className={`nav-tab${page === p ? ' active' : ''}`}
              >
                {PAGE_LABEL[p]}
              </button>
            ))}
          </div>

          <span className="top-sep" style={{ color: 'var(--sep)' }}>|</span>

          {'Notification' in window && (
            <span
              title={`Notifications: ${Notification.permission}`}
              className="header-icon-status"
              style={{ color: Notification.permission === 'granted' ? '#00d48a' : '#2d4060' }}
            >
              <Bell size={14} strokeWidth={2.2} />
            </span>
          )}
          <button
            onClick={onLogout}
            className="icon-text-button"
          >
            <LogOut size={13} strokeWidth={2.2} />
            LOGOUT
          </button>
        </div>
      </div>

      {/* ── Pages ── */}
      <Suspense fallback={<PageLoadingFallback />}>
        {page === 'home' ? (
          <HomePage />
        ) : page === 'system' ? (
          <SystemPage />
        ) : page === 'memecoins' ? (
          <MemecoinsPage />
        ) : (
          <AuditPage />
        )}
      </Suspense>
    </div>
  )
}
