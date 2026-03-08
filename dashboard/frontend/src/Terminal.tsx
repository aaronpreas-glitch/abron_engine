import { useQuery } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { api } from './api'
import { TradesSection } from './sections/TradesSection'
import { WalletSection } from './sections/WalletSection'
import type { JupiterPosition } from './sections/WalletSection'
import { PriceStrip } from './sections/PriceStrip'
import { TierSection } from './sections/TierSection'
import { PortfolioSection } from './sections/PortfolioSection'
import { FeedSection } from './sections/FeedSection'
import { NewsTicker } from './sections/NewsTicker'
import { MemecoinsPage } from './sections/MemecoinsPage'
import { SpotPage } from './sections/SpotPage'
import { HomePage } from './sections/HomePage'
import { WhalePage } from './sections/WhalePage'
import { ConfluencePage } from './sections/ConfluencePage'
import { WalletsPage } from './sections/WalletsPage'
import { MarketOverviewBar } from './sections/MarketOverviewBar'  // Patch 152

// ── Health types ──────────────────────────────────────────────────────────────
interface SystemHealth {
  status: 'HEALTHY' | 'WARN' | 'DEGRADED' | 'CRITICAL' | 'UNKNOWN'
  ts: string | null
  db: boolean | null
  issues: string[]
  warnings: string[]
  agents_total: number
  agents_stalled: number
  agents_slow: number
  scan_age_min: number | null
  open_meme: number
  // Patch 122
  fear_greed?: { value: number | null; label: string; favorable: boolean } | null
  auto_buy_enabled?: boolean
}

// ── System Health Bar ─────────────────────────────────────────────────────────
function SystemHealthBar({ health }: { health: SystemHealth | undefined }) {
  if (!health) return null

  const dotColor =
    health.status === 'HEALTHY' ? '#00d48a' :
    health.status === 'WARN'    ? '#f59e0b' :
    health.status === 'DEGRADED'? '#f59e0b' :
    health.status === 'CRITICAL'? '#ef4444' : '#4d5a6e'

  const allIssues = [...health.issues, ...health.warnings]

  return (
    <div style={{
      background: '#050d14',
      borderTop: `1px solid ${dotColor}22`,
      borderBottom: '1px solid #0d1f2d',
      padding: '3px 20px',
      display: 'flex',
      alignItems: 'center',
      gap: 10,
      fontSize: 9,
      fontFamily: 'JetBrains Mono, monospace',
    }}>
      {/* Status dot + label */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 5, flexShrink: 0 }}>
        <span style={{
          width: 6, height: 6, borderRadius: '50%',
          background: dotColor,
          display: 'inline-block',
          boxShadow: health.status === 'HEALTHY' ? `0 0 5px ${dotColor}88` : 'none',
        }} />
        <span style={{ color: dotColor, fontWeight: 700, letterSpacing: '0.1em' }}>
          {health.status}
        </span>
      </div>

      <span style={{ color: 'var(--sep)' }}>·</span>

      {/* Agent summary */}
      <span style={{ color: '#2d4060', flexShrink: 0 }}>
        <span style={{ color: '#4d5a6e' }}>agents </span>
        <span style={{ color: health.agents_stalled > 0 ? '#ef4444' : '#00d48a' }}>
          {health.agents_total - health.agents_stalled}/{health.agents_total}
        </span>
        {health.agents_slow > 0 && (
          <span style={{ color: '#f59e0b' }}> · {health.agents_slow} slow</span>
        )}
      </span>

      {/* Scan age */}
      {health.scan_age_min !== null && (
        <>
          <span style={{ color: 'var(--sep)' }}>·</span>
          <span style={{ color: health.scan_age_min > 15 ? '#f59e0b' : '#2d4060', flexShrink: 0 }}>
            <span style={{ color: '#4d5a6e' }}>scan </span>
            {health.scan_age_min > 1 ? `${health.scan_age_min.toFixed(0)}m ago` : 'fresh'}
          </span>
        </>
      )}

      {/* F&G index — Patch 122 */}
      {health.fear_greed && (
        <>
          <span style={{ color: 'var(--sep)' }}>·</span>
          <span style={{ flexShrink: 0 }}>
            <span style={{ color: '#4d5a6e' }}>F&amp;G </span>
            <span style={{ color: health.fear_greed.favorable ? '#00d48a' : '#ef4444', fontWeight: 600 }}>
              {health.fear_greed.value ?? '—'} {health.fear_greed.label}
            </span>
          </span>
        </>
      )}
      {/* Auto-buy state — Patch 122 */}
      {health.auto_buy_enabled !== undefined && (
        <>
          <span style={{ color: 'var(--sep)' }}>·</span>
          <span style={{ color: health.auto_buy_enabled ? '#00d48a' : '#4d5a6e', flexShrink: 0 }}>
            AUTO-BUY {health.auto_buy_enabled ? 'ON' : 'OFF'}
          </span>
        </>
      )}

      {/* Issues / warnings scrolling */}
      {allIssues.length > 0 && (
        <>
          <span style={{ color: 'var(--sep)' }}>·</span>
          <div style={{ overflow: 'hidden', flex: 1, minWidth: 0 }}>
            <span style={{ color: health.issues.length > 0 ? '#ef4444' : '#f59e0b' }}>
              {allIssues.join(' · ')}
            </span>
          </div>
        </>
      )}

      {/* DB dot */}
      {health.db !== null && (
        <span
          title="Database"
          style={{ marginLeft: 'auto', flexShrink: 0, color: health.db ? '#2d4060' : '#ef4444' }}
        >
          DB {health.db ? '✓' : '✗'}
        </span>
      )}
    </div>
  )
}

// ── Types ────────────────────────────────────────────────────────────────────

// Kept for section files that import these (not rendered, but TypeScript checks them)
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

type Page = 'home' | 'trading' | 'memecoins' | 'spot' | 'whale' | 'confluence' | 'wallets'

export function Terminal({ onLogout }: Props) {
  const [page, setPage] = useState<Page>('home')

  // ── Notification permission ───────────────────────────────────────────────
  useEffect(() => {
    if ('Notification' in window && Notification.permission === 'default') {
      Notification.requestPermission()
    }
  }, [])

  // ── Data fetches ──────────────────────────────────────────────────────────

  const perpsStatus = useQuery<PerpsStatus>({
    queryKey: ['perps-status'],
    queryFn: async () => {
      const r = await api.get('/perps/status')
      return r.data
    },
    refetchInterval: 30_000,
  })

  const closedTrades = useQuery<ClosedTrade[]>({
    queryKey: ['closed-trades'],
    queryFn: async () => {
      const r = await api.get('/perps/closed?limit=20')
      return r.data?.trades ?? r.data ?? []
    },
    refetchInterval: 30_000,
  })

  const portfolio = useQuery<{
    signals: Array<{
      coin: string; signal: 'ACCUMULATE' | 'HOLD' | 'REDUCE'
      price_usd: number | null; reason: string | null
      fear_greed: number | null; btc_dom_pct: number | null
      regime: string | null; regime_score: number | null
      chg_4w_pct: number | null; ts_utc: string
    }>
    fear_greed: number | null
    btc_dom_pct: number | null
    last_updated: string | null
  }>({
    queryKey: ['portfolio'],
    queryFn: async () => {
      const r = await api.get('/portfolio/signals')
      return r.data
    },
    refetchInterval: 60_000,
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
    refetchInterval: 30_000,
  })

  const pricesQuery = useQuery<{
    prices: Array<{ coin: string; price: number; chg24: number | null }>
  }>({
    queryKey: ['prices'],
    queryFn: async () => {
      const r = await api.get('/prices')
      // API returns {BTC: {price, change_24h}, ...} — transform to {prices: [...]}
      const raw = r.data as Record<string, { price: number; change_24h: number | null }>
      const prices = Object.entries(raw).map(([coin, d]) => ({
        coin,
        price: d.price,
        chg24: d.change_24h,
      }))
      return { prices }
    },
    refetchInterval: 60_000,
  })

  const healthQuery = useQuery<SystemHealth>({
    queryKey: ['system-health'],
    queryFn: async () => {
      const r = await api.get('/health/status')
      return r.data
    },
    refetchInterval: 60_000,
  })

  const memoryQuery = useQuery<MemoryEntry[]>({
    queryKey: ['memory'],
    queryFn: async () => {
      const r = await api.get('/orchestrator/memory?lines=80')
      const raw: string = r.data?.memory ?? ''
      // Parse "## [2026-03-01 18:30:00 UTC] AGENT\nmessage" blocks
      const entries: MemoryEntry[] = []
      const lines = raw.split('\n')
      let i = 0
      while (i < lines.length) {
        const m = lines[i].match(/^##\s+\[(.+?)\s*UTC\]\s+(.+)$/)
        if (m) {
          const ts   = m[1].trim()   // "2026-03-01 18:30:00" — strip UTC for ISO compat
          const agent = m[2].trim()
          const msgParts: string[] = []
          i++
          while (i < lines.length && !lines[i].startsWith('##')) {
            if (lines[i].trim()) msgParts.push(lines[i].trim())
            i++
          }
          if (msgParts.length) entries.push({ ts, agent, message: msgParts.join(' ') })
        } else {
          i++
        }
      }
      return entries.reverse()  // newest first
    },
    refetchInterval: 15_000,
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
  const mode     = isDryRun ? 'SIMULATE' : 'LIVE'
  const regime   = perpsStatus.data?.market_regime ?? '—'
  const engineOn = perpsStatus.data?.enabled !== false

  // Jupiter wallet
  const walletPositions = walletQuery.data?.positions ?? []
  const perpPnlUsd   = walletPositions.reduce((s, p) => s + p.pnl_usd, 0)
  const perpValueUsd = walletPositions.reduce((s, p) => s + p.value_usd, 0)
  const hasPerpValue = walletPositions.length > 0

  // SOL spot
  const solBalance  = walletQuery.data?.sol_balance ?? null
  const solPrice    = pricesQuery.data?.prices.find(p => p.coin === 'SOL')?.price ?? null
  const solValueUsd = solBalance !== null && solPrice ? solBalance * solPrice : null

  // Net portfolio
  const netUsd = (solValueUsd ?? 0) + (hasPerpValue ? perpValueUsd : 0)

  // ── Layout ────────────────────────────────────────────────────────────────

  return (
    <div style={{
      minHeight: '100vh',
      color: '#e2e8f0',
      fontFamily: 'JetBrains Mono, monospace',
      fontSize: 12,
    }}>

      {/* ── Header ── */}
      <div className="top-bar" style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        padding: '0 20px', position: 'sticky', top: 0, zIndex: 10,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 18 }}>

          {/* Engine name + status dot */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span style={{
              width: 7, height: 7, borderRadius: '50%',
              background: engineOn && !isDryRun ? '#00d48a' : isDryRun ? '#f59e0b' : '#ef4444',
              display: 'inline-block',
              boxShadow: engineOn && !isDryRun ? '0 0 6px #00d48a88' : 'none',
            }} />
            <span style={{ color: '#c0cfe0', fontWeight: 700, letterSpacing: '0.12em', fontSize: 13 }}>
              ABRON ENGINE
            </span>
          </div>

          <span style={{ color: 'var(--sep)' }}>|</span>

          <span style={{ color: mode === 'LIVE' ? '#00d48a' : '#f59e0b', fontSize: 11, fontWeight: 700 }}>
            {mode}
          </span>

          {regime !== '—' && (
            <>
              <span style={{ color: 'var(--sep)' }}>|</span>
              <span style={{
                color: regime.toLowerCase().includes('bull') ? '#00d48a'
                  : regime.toLowerCase().includes('bear') ? '#ef4444'
                  : '#a0aec0',
                fontSize: 10,
              }}>
                {regime.toUpperCase()}
              </span>
            </>
          )}

          {/* SOL spot */}
          {solValueUsd !== null && (
            <>
              <span style={{ color: 'var(--sep)' }}>|</span>
              <span style={{ fontSize: 11 }}>
                <span style={{ color: '#4d5a6e' }}>SOL </span>
                <span style={{ color: '#8a9ab0', fontWeight: 700 }}>{solBalance!.toFixed(3)}</span>
                <span style={{ color: '#2d4060', fontSize: 10 }}> · ${solValueUsd.toLocaleString('en-US', { maximumFractionDigits: 0 })}</span>
              </span>
            </>
          )}

          {/* PERP PnL */}
          {hasPerpValue && (
            <>
              <span style={{ color: 'var(--sep)' }}>|</span>
              <span style={{ fontSize: 11 }}>
                <span style={{ color: '#4d5a6e' }}>PERP </span>
                <span style={{ color: perpPnlUsd >= 0 ? '#00d48a' : '#ef4444', fontWeight: 700 }}>
                  {perpPnlUsd >= 0 ? '+' : ''}${perpPnlUsd.toFixed(2)}
                </span>
                <span style={{ color: '#2d4060', fontSize: 10 }}> · ${perpValueUsd.toFixed(0)}</span>
              </span>
            </>
          )}

          {/* Net total */}
          {netUsd > 0 && (
            <>
              <span style={{ color: 'var(--sep)' }}>|</span>
              <span style={{ fontSize: 10, color: '#2d4060' }}>
                NET <span style={{ color: '#5a7a9a', fontWeight: 700 }}>${netUsd.toLocaleString('en-US', { maximumFractionDigits: 0 })}</span>
              </span>
            </>
          )}
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>

          {/* ── Page tabs ── */}
          <div className="nav-tabs-row">
            {(['home', 'trading', 'memecoins', 'spot', 'whale', 'confluence', 'wallets'] as Page[]).map(p => (
              <button
                key={p}
                onClick={() => setPage(p)}
                className={`nav-tab${page === p ? ' active' : ''}`}
              >
                {p.toUpperCase()}
              </button>
            ))}
          </div>

          <span style={{ color: 'var(--sep)' }}>|</span>

          {'Notification' in window && (
            <span
              title={`Notifications: ${Notification.permission}`}
              style={{ fontSize: 10, color: Notification.permission === 'granted' ? '#00d48a' : '#2d4060', cursor: 'default' }}
            >
              🔔
            </span>
          )}
          <button
            onClick={onLogout}
            style={{
              background: 'none', border: '1px solid var(--sep)', borderRadius: 3,
              color: 'var(--dim)', cursor: 'pointer', fontFamily: 'inherit',
              fontSize: 10, padding: '3px 8px',
            }}
          >
            LOGOUT
          </button>
        </div>
      </div>

      {/* ── Price Strip ── */}
      <PriceStrip
        prices={pricesQuery.data?.prices ?? []}
        loading={pricesQuery.isLoading}
      />

      {/* ── News Ticker ── */}
      <NewsTicker />

      {/* ── System Health Bar ── */}
      <SystemHealthBar health={healthQuery.data} />

      {/* ── Market Overview Bar ── */}
      <MarketOverviewBar />  {/* Patch 152 */}

      {/* ── Pages ── */}
      {page === 'home' ? (
        <HomePage />
      ) : page === 'wallets' ? (
        <WalletsPage />
      ) : page === 'confluence' ? (
        <ConfluencePage />
      ) : page === 'whale' ? (
        <WhalePage />
      ) : page === 'spot' ? (
        <SpotPage />
      ) : page === 'memecoins' ? (
        <MemecoinsPage />
      ) : (
        <div style={{ maxWidth: 1200, margin: '0 auto', padding: '16px 20px', display: 'flex', flexDirection: 'column', gap: 12 }}>

          {/* ── Trading page header ── */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
            <span style={{
              color: '#00d48a', fontFamily: 'JetBrains Mono, monospace',
              fontWeight: 700, fontSize: 13, letterSpacing: '0.14em',
            }}>
              TRADING
            </span>
            <span className="badge" style={{
              color: isDryRun ? 'var(--amber)' : 'var(--green)',
              background: isDryRun ? 'rgba(245,158,11,0.1)' : 'rgba(0,212,138,0.1)',
              border: `1px solid ${isDryRun ? 'rgba(245,158,11,0.25)' : 'rgba(0,212,138,0.25)'}`,
              fontSize: 9,
            }}>
              {isDryRun ? 'SIMULATE' : 'LIVE'}
            </span>
            <span style={{ color: 'var(--dim)', fontFamily: 'JetBrains Mono, monospace', fontSize: 10 }}>
              Jupiter Perps · 3× SOL · 5× BTC · 10× ETH
            </span>
            <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 16 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <span style={{ color: 'var(--dim)', fontSize: 10, fontFamily: 'JetBrains Mono, monospace' }}>positions</span>
                <span style={{
                  color: (perpsStatus.data?.open_positions ?? 0) > 0 ? 'var(--green)' : 'var(--dim)',
                  fontFamily: 'JetBrains Mono, monospace', fontWeight: 700, fontSize: 12,
                }}>
                  {walletPositions.length > 0 ? walletPositions.length : (perpsStatus.data?.open_positions ?? '—')}
                </span>
              </div>
              {hasPerpValue && (
                <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                  <span style={{ color: 'var(--dim)', fontSize: 10, fontFamily: 'JetBrains Mono, monospace' }}>perp PnL</span>
                  <span style={{
                    color: perpPnlUsd >= 0 ? 'var(--green)' : 'var(--red)',
                    fontFamily: 'JetBrains Mono, monospace', fontWeight: 700, fontSize: 12,
                  }}>
                    {perpPnlUsd >= 0 ? '+' : ''}${perpPnlUsd.toFixed(2)}
                  </span>
                </div>
              )}
              {regime !== '—' && (
                <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                  <span style={{ color: 'var(--dim)', fontSize: 10, fontFamily: 'JetBrains Mono, monospace' }}>regime</span>
                  <span style={{
                    color: regime.toLowerCase().includes('bull') ? 'var(--green)'
                      : regime.toLowerCase().includes('bear') ? 'var(--red)' : 'var(--muted)',
                    fontFamily: 'JetBrains Mono, monospace', fontWeight: 700, fontSize: 11,
                  }}>
                    {regime.toUpperCase()}
                  </span>
                </div>
              )}
            </div>
          </div>

          <div className="card">
            <TierSection jupiterPositions={walletPositions} />
          </div>

          <div className="card">
            <WalletSection
              wallet={walletQuery.data?.wallet ?? null}
              positions={walletPositions}
              solBalance={solBalance}
              solPrice={solPrice}
              loading={walletQuery.isLoading}
              error={walletQuery.data?.error ?? null}
            />
          </div>

          <div className="card">
            <PortfolioSection
              signals={portfolio.data?.signals ?? []}
              fearGreed={portfolio.data?.fear_greed ?? null}
              btcDom={portfolio.data?.btc_dom_pct ?? null}
              lastUpdated={portfolio.data?.last_updated ?? null}
              loading={portfolio.isLoading}
              livePrices={pricesQuery.data?.prices ?? []}
            />
          </div>

          <div className="card">
            <TradesSection
              perpsStatus={perpsStatus.data}
              closedTrades={closedTrades.data ?? []}
              loading={closedTrades.isLoading || perpsStatus.isLoading}
            />
          </div>

          <div className="card">
            <FeedSection
              entries={memoryQuery.data ?? []}
              loading={memoryQuery.isLoading}
            />
          </div>

        </div>
      )}
    </div>
  )
}
