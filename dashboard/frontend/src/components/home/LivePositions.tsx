/**
 * LivePositions — shows all open auto-executor positions with live PnL,
 * exit plan details, and a force-sell button per position.
 *
 * Data: GET /api/executor/status
 * Actions: POST /api/executor/force-sell, POST /api/executor/toggle
 */
import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
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

// ── Position card ─────────────────────────────────────────────────────────────

function PositionCard({ pos, onForceSell }: { pos: OpenPosition; onForceSell: (s: string) => void }) {
  const notes = parseNotes(pos.notes)
  const tp1 = notes['tp1'] || '—'
  const tp2 = notes['tp2'] || '—'
  const score = notes['score'] || '—'
  const conf = notes['conf'] || '—'
  const regime = notes['regime'] || '—'

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
        {notes['auto'] === undefined ? null : (
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

// ── Main component ────────────────────────────────────────────────────────────

export function LivePositions() {
  const qc = useQueryClient()
  const [toggling, setToggling] = useState(false)
  const [togglingDryRun, setTogglingDryRun] = useState(false)
  const [portfolioInput, setPortfolioInput] = useState('')
  const [savingPortfolio, setSavingPortfolio] = useState(false)
  const [portfolioSaved, setPortfolioSaved] = useState(false)

  const { data, isLoading, error } = useQuery<ExecutorStatus>({
    queryKey: ['executor-status'],
    queryFn: () => api.get('/executor/status').then(r => r.data),
    refetchInterval: 30_000,
    staleTime: 15_000,
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

  const enabled   = data?.enabled ?? false
  const dry_run   = data?.dry_run ?? true
  const positions = data?.positions ?? []

  return (
    <div style={{ padding: '0 0 32px' }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 14, marginBottom: 20 }}>
        <h2 style={{ fontSize: 14, fontWeight: 700, letterSpacing: '0.06em' }}>
          ⚡ LIVE POSITIONS
        </h2>
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
    </div>
  )
}
