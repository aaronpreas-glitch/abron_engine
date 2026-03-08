import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../api'

// ── Types ─────────────────────────────────────────────────────────────────────

interface WalletStats {
  total_wallets:  number
  total_buys:     number
  complete_buys:  number
  accumulations:  number
  wr_24h:         number | null
  phase:          string
  phase_label:    string
  next_milestone: number | null
}

interface TrackedWallet {
  id:              number
  address:         string
  label:           string
  active:          number
  total_buys:      number
  complete_buys:   number        // P178: COUNT of COMPLETE outcome rows
  wr_24h:          number | null
  avg_24h:         number | null // P178: AVG return_24h_pct across COMPLETE rows
  last_checked_ts: number | null
  added_ts:        string
}

interface WalletBuy {
  id:              number
  ts_utc:          string
  wallet_label:    string
  token_symbol:    string | null
  token_mint:      string
  buy_amount_sol:  number | null
  buy_amount_usd:  number | null
  market_cap_usd:  number | null
  dex_source:      string | null
  return_1h_pct:   number | null
  return_4h_pct:   number | null
  return_24h_pct:  number | null
  outcome_status:  string
}

interface Accumulation {
  id:             number
  ts_utc:         string
  token_symbol:   string | null
  token_mint:     string
  wallet_count:   number
  wallet_labels:  string   // JSON array
  total_sol:      number | null
  market_cap_usd: number | null
  return_1h_pct:  number | null
  return_4h_pct:  number | null
  return_24h_pct: number | null
  outcome_status: string
  alert_sent:     number
}

// P178: TRIPLE confluence events
interface TripleEvent {
  id:             number
  ts_utc:         string
  token_mint:     string
  token_symbol:   string | null
  source_details: string | null  // JSON
  market_cap_usd: number | null
  return_1h_pct:  number | null
  return_4h_pct:  number | null
  return_24h_pct: number | null
  outcome_status: string
  expire_reason:  string | null  // Patch 181
}

// ── Constants ─────────────────────────────────────────────────────────────────

const V     = '#8b5cf6'   // violet accent
const AMBER = '#f59e0b'

const PHASES = [
  { key: 'OBSERVE', label: 'OBSERVE', min: 0,  max: 10,  color: '#94a3b8', desc: 'Logging buys, building dataset' },
  { key: 'ANALYZE', label: 'ANALYZE', min: 10, max: 50,  color: AMBER,     desc: 'Win rates emerging' },
  { key: 'SIGNAL',  label: 'SIGNAL',  min: 50, max: Infinity, color: V,    desc: 'Telegram alerts active' },
]

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmtAge(ts: string | null | undefined): string {
  if (!ts) return '—'
  const diff = (Date.now() - new Date(ts.includes('T') ? ts : ts + 'Z').getTime()) / 1000
  if (diff < 60)    return `${Math.floor(diff)}s ago`
  if (diff < 3600)  return `${Math.floor(diff / 60)}m ago`
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`
  return `${Math.floor(diff / 86400)}d ago`
}

function fmtAgeFromEpoch(ts: number | null): string {
  if (!ts) return '—'
  const diff = (Date.now() / 1000 - ts)
  if (diff < 60)    return `${Math.floor(diff)}s ago`
  if (diff < 3600)  return `${Math.floor(diff / 60)}m ago`
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`
  return `${Math.floor(diff / 86400)}d ago`
}

function fmtAddr(addr: string): string {
  return `${addr.slice(0, 8)}…${addr.slice(-8)}`
}

function fmtMc(n: number | null): string {
  if (n == null) return '—'
  if (n >= 1_000_000) return `$${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000)     return `$${(n / 1_000).toFixed(0)}K`
  return `$${n.toFixed(0)}`
}

function fmtSol(n: number | null): string {
  if (n == null) return '—'
  return `${n.toFixed(2)} SOL`
}

function fmtRet(n: number | null): { text: string; color: string } {
  if (n == null) return { text: '—', color: 'var(--dim)' }
  return {
    text:  (n > 0 ? '+' : '') + n.toFixed(1) + '%',
    color: n > 0 ? 'var(--green)' : n < 0 ? 'var(--red)' : 'var(--muted)',
  }
}

// P178: status badge colour — EXPIRED is amber (was invisible as --dim)
function statusColor(s: string): string {
  if (s === 'COMPLETE') return 'var(--green)'
  if (s === 'EXPIRED')  return AMBER
  return 'var(--dim)'
}

// ── Phase bar ─────────────────────────────────────────────────────────────────

function PhaseBar({ total, next }: { total: number; next: number | null }) {
  const phase    = PHASES.find(p => total < p.max) ?? PHASES[PHASES.length - 1]
  const pct      = next ? Math.min(100, (total / next) * 100) : 100
  const isSignal = !next
  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ fontFamily: 'JetBrains Mono, monospace', fontWeight: 700, fontSize: 11, color: phase.color }}>
            {phase.label}
          </span>
          {isSignal && (
            <span style={{
              fontFamily: 'JetBrains Mono, monospace', fontSize: 8, fontWeight: 700,
              letterSpacing: '0.12em', padding: '2px 6px', borderRadius: 3,
              background: 'rgba(139,92,246,0.12)', border: '1px solid rgba(139,92,246,0.35)',
              color: V,
            }}>
              TELEGRAM ACTIVE
            </span>
          )}
        </div>
        <span style={{ fontFamily: 'JetBrains Mono, monospace', fontSize: 10, color: 'var(--dim)' }}>
          {phase.desc}
        </span>
        <span style={{ fontFamily: 'JetBrains Mono, monospace', fontSize: 10, color: 'var(--muted)' }}>
          {next ? `${total} / ${next}` : `${total} buys tracked`}
        </span>
      </div>
      <div style={{ height: 4, borderRadius: 2, background: 'rgba(255,255,255,0.06)', overflow: 'hidden' }}>
        <div style={{ height: '100%', width: `${pct}%`, background: phase.color, borderRadius: 2, transition: 'width 0.4s ease' }} />
      </div>
    </div>
  )
}

// ── Add wallet form ───────────────────────────────────────────────────────────

function AddWalletForm({ onAdded }: { onAdded: () => void }) {
  const [addr,  setAddr]  = useState('')
  const [label, setLabel] = useState('')
  const [error, setError] = useState<string | null>(null)
  const qc = useQueryClient()

  const mut = useMutation({
    mutationFn: () => api.post('/wallets/add', { address: addr.trim(), label: label.trim() || 'Unknown' }),
    onSuccess: () => {
      setAddr(''); setLabel(''); setError(null)
      qc.invalidateQueries({ queryKey: ['wallet-list'] })
      qc.invalidateQueries({ queryKey: ['wallet-stats'] })
      onAdded()
    },
    onError: (e: any) => {
      setError(e?.response?.data?.detail || 'Failed to add wallet')
    },
  })

  const inputStyle: React.CSSProperties = {
    background: 'rgba(255,255,255,0.04)',
    border: '1px solid rgba(255,255,255,0.10)',
    borderRadius: 6,
    padding: '6px 10px',
    color: 'var(--text2)',
    fontFamily: 'JetBrains Mono, monospace',
    fontSize: 11,
    outline: 'none',
  }

  return (
    <div style={{ marginTop: 14 }}>
      <div style={{
        fontSize: 9, fontFamily: 'JetBrains Mono, monospace',
        color: 'var(--dim)', letterSpacing: '0.1em', marginBottom: 8,
      }}>
        ADD WALLET
      </div>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
        <input
          style={{ ...inputStyle, flex: '2 1 240px' }}
          placeholder="Solana wallet address"
          value={addr}
          onChange={e => { setAddr(e.target.value); setError(null) }}
          spellCheck={false}
        />
        <input
          style={{ ...inputStyle, flex: '1 1 120px' }}
          placeholder="Label (e.g. Alpha 1)"
          value={label}
          onChange={e => setLabel(e.target.value)}
        />
        <button
          onClick={() => mut.mutate()}
          disabled={!addr.trim() || mut.isPending}
          style={{
            background: addr.trim() ? `${V}22` : 'rgba(255,255,255,0.04)',
            border: `1px solid ${addr.trim() ? V + '66' : 'rgba(255,255,255,0.10)'}`,
            borderRadius: 6,
            color: addr.trim() ? V : 'var(--dim)',
            fontFamily: 'JetBrains Mono, monospace',
            fontSize: 10, fontWeight: 700,
            padding: '6px 14px',
            cursor: addr.trim() ? 'pointer' : 'not-allowed',
            letterSpacing: '0.08em',
          }}
        >
          {mut.isPending ? 'ADDING…' : 'ADD'}
        </button>
      </div>
      {error && (
        <div style={{ fontFamily: 'JetBrains Mono, monospace', fontSize: 9, color: 'var(--red)', marginTop: 6 }}>
          {error}
        </div>
      )}
    </div>
  )
}

// ── Wallet roster card ────────────────────────────────────────────────────────

// P178: cull signal thresholds
function cullSignal(w: TrackedWallet): 'CULL' | 'WATCH' | null {
  const n = w.complete_buys ?? 0
  if (n < 10) return null  // not enough data
  const wr = w.wr_24h ?? 100
  if (wr < 15) return 'CULL'
  if (wr < 25) return 'WATCH'
  return null
}

function WalletCard({ wallet }: { wallet: TrackedWallet }) {
  const [confirmRemove, setConfirmRemove] = useState(false)
  const qc = useQueryClient()

  const removeMut = useMutation({
    mutationFn: () => api.delete(`/wallets/${wallet.id}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['wallet-list'] })
      qc.invalidateQueries({ queryKey: ['wallet-stats'] })
    },
  })

  const signal = cullSignal(wallet)
  const n      = wallet.complete_buys ?? 0

  // Border colour: red if CULL, amber if WATCH, default violet
  const borderColor = signal === 'CULL' ? 'rgba(239,68,68,0.5)'
    : signal === 'WATCH' ? `${AMBER}55`
    : `${V}22`
  const topBorder   = signal === 'CULL' ? '2px solid rgba(239,68,68,0.7)'
    : signal === 'WATCH' ? `2px solid ${AMBER}88`
    : `2px solid ${V}55`

  const wrColor = wallet.wr_24h == null ? 'var(--dim)'
    : wallet.wr_24h >= 40  ? 'var(--green)'
    : wallet.wr_24h >= 25  ? AMBER
    : 'var(--red)'

  const avgRet = fmtRet(wallet.avg_24h)

  return (
    <div style={{
      background: signal === 'CULL' ? 'rgba(239,68,68,0.06)' : `${V}08`,
      border: `1px solid ${borderColor}`,
      borderTop: topBorder,
      borderRadius: '0 0 10px 10px',
      padding: '12px 14px',
      display: 'flex', flexDirection: 'column', gap: 8,
      minWidth: 200,
      position: 'relative',
    }}>

      {/* Cull badge — top-right */}
      {signal && (
        <div style={{
          position: 'absolute', top: 8, right: 10,
          fontFamily: 'JetBrains Mono, monospace', fontSize: 8, fontWeight: 700,
          letterSpacing: '0.1em', padding: '2px 6px', borderRadius: 3,
          background: signal === 'CULL' ? 'rgba(239,68,68,0.18)' : `${AMBER}20`,
          border: `1px solid ${signal === 'CULL' ? 'rgba(239,68,68,0.5)' : AMBER + '60'}`,
          color: signal === 'CULL' ? 'var(--red)' : AMBER,
        }}>
          {signal}
        </div>
      )}

      {/* Label + address */}
      <div style={{ paddingRight: signal ? 50 : 0 }}>
        <div style={{ fontFamily: 'JetBrains Mono, monospace', fontWeight: 700, fontSize: 12, color: V, marginBottom: 2 }}>
          {wallet.label}
        </div>
        <div style={{ fontFamily: 'JetBrains Mono, monospace', fontSize: 9, color: 'var(--dim)' }}>
          {fmtAddr(wallet.address)}
        </div>
      </div>

      {/* Metrics */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
        <Row label="buys logged"  value={String(wallet.total_buys)} />
        {/* P178: show n alongside WR so operator can assess confidence */}
        <Row
          label="WR @ 24h"
          value={wallet.wr_24h != null ? `${wallet.wr_24h}%  (n=${n})` : `— (n=${n})`}
          color={n >= 10 ? wrColor : 'var(--dim)'}
        />
        {/* P178: avg return — key for knowing if losses are small or catastrophic */}
        <Row
          label="avg 24h"
          value={avgRet.text}
          color={n >= 10 ? avgRet.color : 'var(--dim)'}
        />
        <Row label="last checked" value={fmtAgeFromEpoch(wallet.last_checked_ts)} />
      </div>

      {/* Remove button */}
      <div>
        {!confirmRemove ? (
          <button
            onClick={() => setConfirmRemove(true)}
            style={{
              background: 'transparent', border: '1px solid rgba(255,255,255,0.08)',
              borderRadius: 5, color: 'var(--dim)',
              fontFamily: 'JetBrains Mono, monospace', fontSize: 9,
              padding: '3px 10px', cursor: 'pointer',
            }}
          >
            REMOVE
          </button>
        ) : (
          <div style={{ display: 'flex', gap: 6 }}>
            <button
              onClick={() => removeMut.mutate()}
              disabled={removeMut.isPending}
              style={{
                background: 'rgba(239,68,68,0.15)', border: '1px solid rgba(239,68,68,0.4)',
                borderRadius: 5, color: 'var(--red)',
                fontFamily: 'JetBrains Mono, monospace', fontSize: 9,
                padding: '3px 10px', cursor: 'pointer',
              }}
            >
              {removeMut.isPending ? '…' : 'CONFIRM'}
            </button>
            <button
              onClick={() => setConfirmRemove(false)}
              style={{
                background: 'transparent', border: '1px solid rgba(255,255,255,0.08)',
                borderRadius: 5, color: 'var(--dim)',
                fontFamily: 'JetBrains Mono, monospace', fontSize: 9,
                padding: '3px 10px', cursor: 'pointer',
              }}
            >
              CANCEL
            </button>
          </div>
        )}
      </div>
    </div>
  )
}

function Row({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
      <span style={{ color: 'var(--muted)', fontFamily: 'JetBrains Mono, monospace', fontSize: 9 }}>{label}</span>
      <span style={{ color: color ?? 'var(--text2)', fontFamily: 'JetBrains Mono, monospace', fontSize: 11, fontWeight: 600 }}>{value}</span>
    </div>
  )
}

// ── Table styles ──────────────────────────────────────────────────────────────

const TH: React.CSSProperties = {
  fontFamily: 'JetBrains Mono, monospace', fontSize: 9, fontWeight: 700,
  color: 'var(--dim)', letterSpacing: '0.1em',
  padding: '6px 10px', textAlign: 'left',
  borderBottom: '1px solid rgba(255,255,255,0.06)',
  whiteSpace: 'nowrap',
}
const TD: React.CSSProperties = {
  fontFamily: 'JetBrains Mono, monospace', fontSize: 10, color: 'var(--muted)',
  padding: '7px 10px',
  borderBottom: '1px solid rgba(255,255,255,0.04)',
  whiteSpace: 'nowrap',
}

// ── Accumulation table ────────────────────────────────────────────────────────

function AccumulationTable({ items }: { items: Accumulation[] }) {
  if (!items.length) {
    return (
      <div style={{ padding: '30px 20px', textAlign: 'center', color: 'var(--dim)', fontFamily: 'JetBrains Mono, monospace', fontSize: 10 }}>
        No accumulation events yet — need ≥2 wallets buying the same token in 2h
      </div>
    )
  }
  return (
    <div style={{ overflowX: 'auto' }}>
      <table style={{ width: '100%', borderCollapse: 'collapse' }}>
        <thead>
          <tr>
            <th style={TH}>TIME</th>
            <th style={{ ...TH, color: V }}>TOKEN</th>
            <th style={TH}>MC</th>
            <th style={TH}>WALLETS</th>
            <th style={TH}>SOL</th>
            <th style={TH}>1H</th>
            <th style={TH}>4H</th>
            <th style={TH}>24H</th>
          </tr>
        </thead>
        <tbody>
          {items.map(a => {
            const r1  = fmtRet(a.return_1h_pct)
            const r4  = fmtRet(a.return_4h_pct)
            const r24 = fmtRet(a.return_24h_pct)
            let labels: string[] = []
            try { labels = JSON.parse(a.wallet_labels) } catch { labels = [] }
            return (
              <tr key={a.id}>
                <td style={{ ...TD, color: 'var(--dim)', fontSize: 9 }}>{fmtAge(a.ts_utc)}</td>
                <td style={{ ...TD, color: V, fontWeight: 700, fontSize: 11 }}>{a.token_symbol || a.token_mint.slice(0,8)}</td>
                <td style={TD}>{fmtMc(a.market_cap_usd)}</td>
                <td style={TD}>
                  <span style={{ color: V, fontWeight: 700 }}>{a.wallet_count}×</span>
                  {' '}
                  <span style={{ fontSize: 9, color: 'var(--dim)' }}>{labels.join(', ')}</span>
                </td>
                <td style={TD}>{fmtSol(a.total_sol)}</td>
                <td style={{ ...TD, color: r1.color }}>{r1.text}</td>
                <td style={{ ...TD, color: r4.color }}>{r4.text}</td>
                <td style={{ ...TD, color: r24.color }}>{r24.text}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

// ── TRIPLE confluence table (P178) ────────────────────────────────────────────

function TriplesTable({ items }: { items: TripleEvent[] }) {
  if (!items.length) {
    return (
      <div style={{ padding: '30px 20px', textAlign: 'center', color: 'var(--dim)', fontFamily: 'JetBrains Mono, monospace', fontSize: 10 }}>
        No TRIPLE confluences yet — fires when whale_watch + scanner + smart_wallet all agree on same token within 48h
      </div>
    )
  }
  return (
    <div style={{ overflowX: 'auto' }}>
      <table style={{ width: '100%', borderCollapse: 'collapse' }}>
        <thead>
          <tr>
            <th style={TH}>TIME</th>
            <th style={{ ...TH, color: V }}>TOKEN</th>
            <th style={TH}>MC</th>
            <th style={TH}>1H</th>
            <th style={TH}>4H</th>
            <th style={TH}>24H</th>
            <th style={TH}>STATUS</th>
          </tr>
        </thead>
        <tbody>
          {items.map(t => {
            const r1  = fmtRet(t.return_1h_pct)
            const r4  = fmtRet(t.return_4h_pct)
            const r24 = fmtRet(t.return_24h_pct)
            return (
              <tr key={t.id}>
                <td style={{ ...TD, color: 'var(--dim)', fontSize: 9 }}>{fmtAge(t.ts_utc)}</td>
                <td style={{ ...TD, color: V, fontWeight: 700, fontSize: 11 }}>{t.token_symbol || t.token_mint.slice(0,8)}</td>
                <td style={TD}>{fmtMc(t.market_cap_usd)}</td>
                <td style={{ ...TD, color: r1.color }}>{r1.text}</td>
                <td style={{ ...TD, color: r4.color }}>{r4.text}</td>
                <td style={{ ...TD, color: r24.color }}>{r24.text}</td>
                <td style={TD}>
                  <span style={{ fontSize: 9, fontWeight: 700, color: statusColor(t.outcome_status) }}>
                    {t.outcome_status}
                  </span>
                  {/* Patch 181: show expire reason as small dim annotation */}
                  {t.expire_reason && (
                    <div style={{ fontSize: 8, color: 'var(--dim)', fontFamily: 'JetBrains Mono, monospace', marginTop: 1 }}>
                      {t.expire_reason.replace('manual_cleanup_P181', 'cleanup').replace('recovered_P181', 'recovered').replace('_', ' ')}
                    </div>
                  )}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

// ── Buy feed table ────────────────────────────────────────────────────────────

// P178: group by (wallet_label, token_mint) — collapse repeat buys of same token
// into one row with a ×N count badge. Keeps the most recent row's outcome data.
interface BuyGroup extends WalletBuy { count: number }

function dedupBuys(buys: WalletBuy[]): BuyGroup[] {
  const seen = new Map<string, BuyGroup>()
  for (const b of buys) {
    const key = `${b.wallet_label}|${b.token_mint}`
    const existing = seen.get(key)
    if (!existing) {
      seen.set(key, { ...b, count: 1 })
    } else {
      // buys are sorted DESC — first seen is most recent, just bump count
      existing.count++
      // upgrade outcome: prefer COMPLETE over PENDING/EXPIRED
      if (b.outcome_status === 'COMPLETE' && existing.outcome_status !== 'COMPLETE') {
        const saved = existing.count
        Object.assign(existing, b)
        existing.count = saved
      }
    }
  }
  // preserve insertion order (newest first, from DESC sort)
  return Array.from(seen.values())
}

function BuyFeedTable({ buys }: { buys: WalletBuy[] }) {
  if (!buys.length) {
    return (
      <div style={{ padding: '30px 20px', textAlign: 'center', color: 'var(--dim)', fontFamily: 'JetBrains Mono, monospace', fontSize: 10 }}>
        No buys logged yet — add a wallet and wait for the next 5-min poll cycle
      </div>
    )
  }
  const rows = dedupBuys(buys)
  return (
    <div style={{ overflowX: 'auto' }}>
      <table style={{ width: '100%', borderCollapse: 'collapse' }}>
        <thead>
          <tr>
            <th style={TH}>TIME</th>
            <th style={TH}>WALLET</th>
            <th style={{ ...TH, color: V }}>TOKEN</th>
            <th style={TH}>SOL</th>
            <th style={TH}>MC</th>
            <th style={TH}>1H</th>
            <th style={TH}>4H</th>
            <th style={TH}>24H</th>
            <th style={TH}>STATUS</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(b => {
            const r1  = fmtRet(b.return_1h_pct)
            const r4  = fmtRet(b.return_4h_pct)
            const r24 = fmtRet(b.return_24h_pct)
            return (
              <tr key={b.id}>
                <td style={{ ...TD, color: 'var(--dim)', fontSize: 9 }}>{fmtAge(b.ts_utc)}</td>
                <td style={{ ...TD, color: 'var(--muted)', fontSize: 9 }}>{b.wallet_label || '—'}</td>
                <td style={{ ...TD, color: V, fontWeight: 700, fontSize: 11 }}>
                  {b.token_symbol || b.token_mint.slice(0,8)}
                  {/* P178: ×N badge for repeated buys of same token by same wallet */}
                  {b.count > 1 && (
                    <span style={{
                      marginLeft: 5, fontSize: 8, fontWeight: 700,
                      color: AMBER, background: `${AMBER}18`,
                      border: `1px solid ${AMBER}44`,
                      borderRadius: 3, padding: '1px 4px',
                    }}>
                      ×{b.count}
                    </span>
                  )}
                </td>
                <td style={TD}>{fmtSol(b.buy_amount_sol)}</td>
                <td style={TD}>{fmtMc(b.market_cap_usd)}</td>
                <td style={{ ...TD, color: r1.color }}>{r1.text}</td>
                <td style={{ ...TD, color: r4.color }}>{r4.text}</td>
                <td style={{ ...TD, color: r24.color }}>{r24.text}</td>
                <td style={TD}>
                  {/* P178: EXPIRED → amber (was invisible dim) */}
                  <span style={{ fontSize: 9, fontWeight: 700, color: statusColor(b.outcome_status) }}>
                    {b.outcome_status}
                  </span>
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────

export function WalletsPage() {
  const qc = useQueryClient()

  const statsQ = useQuery<WalletStats>({
    queryKey: ['wallet-stats'],
    queryFn:  () => api.get('/wallets/stats').then(r => r.data),
    refetchInterval: 30_000,
  })

  const listQ = useQuery<{ wallets: TrackedWallet[] }>({
    queryKey: ['wallet-list'],
    queryFn:  () => api.get('/wallets/list').then(r => r.data),
    refetchInterval: 30_000,
  })

  const buysQ = useQuery<{ buys: WalletBuy[] }>({
    queryKey: ['wallet-buys'],
    queryFn:  () => api.get('/wallets/buys?limit=50').then(r => r.data),
    refetchInterval: 30_000,
  })

  const accumQ = useQuery<{ accumulations: Accumulation[] }>({
    queryKey: ['wallet-accum'],
    queryFn:  () => api.get('/wallets/accumulations').then(r => r.data),
    refetchInterval: 30_000,
  })

  // P178: TRIPLE confluences on WalletsPage
  const triplesQ = useQuery<{ triples: TripleEvent[] }>({
    queryKey: ['wallet-triples'],
    queryFn:  () => api.get('/wallets/triples?limit=30').then(r => r.data),
    refetchInterval: 60_000,
  })

  const stats   = statsQ.data
  const wallets = (listQ.data?.wallets ?? []).filter(w => w.active)
  const buys    = buysQ.data?.buys ?? []
  const accums  = accumQ.data?.accumulations ?? []
  const triples = triplesQ.data?.triples ?? []

  // P178: overall avg_24h from all wallets for the header stat
  const allAvgRet = wallets.length
    ? wallets.reduce((s, w) => s + (w.avg_24h ?? 0), 0) / wallets.filter(w => (w.complete_buys ?? 0) > 0).length
    : null
  const avgRetFmt = fmtRet(wallets.some(w => (w.complete_buys ?? 0) > 0) ? allAvgRet : null)

  return (
    <div style={{ maxWidth: 1200, margin: '0 auto', padding: '16px 20px' }}>

      {/* ── Header card ──────────────────────────────────────────────────── */}
      <div className="glass-card" style={{ border: `1px solid ${V}33`, marginBottom: 16, padding: '18px 22px' }}>
        <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', marginBottom: 18 }}>
          <div>
            <span className="section-label" style={{ color: V }}>SMART WALLETS</span>
            <span style={{ marginLeft: 12, fontSize: 9, fontFamily: 'JetBrains Mono, monospace', color: 'var(--dim)', letterSpacing: '0.08em' }}>
              ON-CHAIN ACCUMULATION TRACKER
            </span>
          </div>
          <div style={{ display: 'flex', gap: 16, alignItems: 'center' }}>
            <span style={{ fontFamily: 'JetBrains Mono, monospace', fontSize: 9, color: 'var(--dim)' }}>
              {stats?.total_wallets ?? 0} WALLETS · 5MIN POLL · HELIUS
            </span>
          </div>
        </div>

        <PhaseBar total={stats?.total_buys ?? 0} next={stats?.next_milestone ?? null} />

        {/* Stats row — P178: added AVG 24H */}
        <div style={{ display: 'flex', gap: 24, marginTop: 14, flexWrap: 'wrap' }}>
          {[
            { label: 'TOTAL BUYS',    value: String(stats?.total_buys ?? 0) },
            { label: 'COMPLETE',      value: String(stats?.complete_buys ?? 0) },
            { label: 'ACCUMULATIONS', value: String(stats?.accumulations ?? 0), color: V },
            { label: 'WR @ 24H',
              value: stats?.wr_24h != null ? `${stats.wr_24h}%` : '—',
              color: stats?.wr_24h != null ? (stats.wr_24h >= 40 ? 'var(--green)' : AMBER) : 'var(--dim)',
            },
            { label: 'AVG 24H',
              value: avgRetFmt.text,
              color: avgRetFmt.color,
            },
            { label: 'TRIPLES',
              value: String(triples.length),
              color: triples.length > 0 ? V : 'var(--dim)',
            },
          ].map(({ label, value, color }) => (
            <div key={label}>
              <div style={{ fontSize: 8, fontFamily: 'JetBrains Mono, monospace', color: 'var(--dim)', letterSpacing: '0.12em', marginBottom: 3 }}>{label}</div>
              <div style={{ fontSize: 16, fontWeight: 700, fontFamily: 'JetBrains Mono, monospace', color: color ?? V }}>{value}</div>
            </div>
          ))}
        </div>

        {/* Add wallet form */}
        <AddWalletForm onAdded={() => qc.invalidateQueries({ queryKey: ['wallet-list'] })} />
      </div>

      {/* ── Wallet Roster ─────────────────────────────────────────────────── */}
      {wallets.length > 0 && (
        <div style={{ marginBottom: 16 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 10 }}>
            <div style={{
              fontSize: 9, fontFamily: 'JetBrains Mono, monospace',
              color: 'var(--dim)', letterSpacing: '0.12em',
            }}>
              TRACKED WALLETS
            </div>
            {/* P178: cull legend */}
            <div style={{ display: 'flex', gap: 10 }}>
              <span style={{ fontFamily: 'JetBrains Mono, monospace', fontSize: 8, color: 'var(--red)' }}>
                ● CULL = WR&lt;15% n≥10
              </span>
              <span style={{ fontFamily: 'JetBrains Mono, monospace', fontSize: 8, color: AMBER }}>
                ● WATCH = WR&lt;25% n≥10
              </span>
            </div>
          </div>
          <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
            {wallets.map(w => <WalletCard key={w.id} wallet={w} />)}
          </div>
        </div>
      )}

      {wallets.length === 0 && !listQ.isLoading && (
        <div className="glass-card" style={{ border: `1px solid ${V}18`, marginBottom: 16, padding: '20px 22px', textAlign: 'center' }}>
          <div style={{ fontFamily: 'JetBrains Mono, monospace', fontSize: 11, color: 'var(--dim)' }}>
            No wallets tracked yet — add a Solana wallet address above to start
          </div>
        </div>
      )}

      {/* ── Accumulation Events ───────────────────────────────────────────── */}
      <div className="glass-card" style={{ border: `1px solid ${V}22`, marginBottom: 12, padding: '16px 0 8px' }}>
        <div style={{ padding: '0 16px 12px', display: 'flex', alignItems: 'baseline', justifyContent: 'space-between' }}>
          <span className="section-label" style={{ color: V }}>ACCUMULATIONS</span>
          <span style={{ fontFamily: 'JetBrains Mono, monospace', fontSize: 9, color: 'var(--dim)' }}>
            ≥2 wallets · 2h window · auto-refresh 30s
          </span>
        </div>
        {accumQ.isLoading ? (
          <div style={{ padding: '30px', textAlign: 'center', color: 'var(--dim)', fontFamily: 'JetBrains Mono, monospace', fontSize: 10 }}>Loading…</div>
        ) : (
          <AccumulationTable items={accums} />
        )}
      </div>

      {/* ── TRIPLE Confluences (P178) ─────────────────────────────────────── */}
      <div className="glass-card" style={{ border: `1px solid ${V}33`, marginBottom: 12, padding: '16px 0 8px' }}>
        <div style={{ padding: '0 16px 12px', display: 'flex', alignItems: 'baseline', justifyContent: 'space-between' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <span className="section-label" style={{ color: V }}>TRIPLE CONFLUENCES</span>
            <span style={{
              fontFamily: 'JetBrains Mono, monospace', fontSize: 8, fontWeight: 700,
              letterSpacing: '0.1em', padding: '2px 6px', borderRadius: 3,
              background: 'rgba(139,92,246,0.12)', border: '1px solid rgba(139,92,246,0.35)',
              color: V,
            }}>
              WHALE + SCANNER + SMART WALLET
            </span>
          </div>
          <span style={{ fontFamily: 'JetBrains Mono, monospace', fontSize: 9, color: 'var(--dim)' }}>
            {triples.length} events · 48h window · auto-refresh 60s
          </span>
        </div>
        {triplesQ.isLoading ? (
          <div style={{ padding: '30px', textAlign: 'center', color: 'var(--dim)', fontFamily: 'JetBrains Mono, monospace', fontSize: 10 }}>Loading…</div>
        ) : (
          <TriplesTable items={triples} />
        )}
      </div>

      {/* ── Buy Feed ──────────────────────────────────────────────────────── */}
      <div className="glass-card" style={{ border: `1px solid rgba(255,255,255,0.08)`, padding: '16px 0 8px' }}>
        <div style={{ padding: '0 16px 12px', display: 'flex', alignItems: 'baseline', justifyContent: 'space-between' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <span className="section-label" style={{ color: V }}>BUY FEED</span>
            {/* P178: explain dedup */}
            <span style={{ fontFamily: 'JetBrains Mono, monospace', fontSize: 8, color: 'var(--dim)' }}>
              deduplicated by wallet+token · ×N = repeat buys
            </span>
          </div>
          <span style={{ fontFamily: 'JetBrains Mono, monospace', fontSize: 9, color: 'var(--dim)' }}>
            {buys.length} raw · all wallets · auto-refresh 30s
          </span>
        </div>
        {buysQ.isLoading ? (
          <div style={{ padding: '30px', textAlign: 'center', color: 'var(--dim)', fontFamily: 'JetBrains Mono, monospace', fontSize: 10 }}>Loading…</div>
        ) : (
          <BuyFeedTable buys={buys} />
        )}
      </div>

    </div>
  )
}
