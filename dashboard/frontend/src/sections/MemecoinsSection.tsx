import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../api'

// ── Types ─────────────────────────────────────────────────────────────────────

interface MemecoinSignal {
  mint:          string
  symbol:        string
  price:         number
  change_1h:     number | null
  change_24h:    number | null
  volume_24h:    number
  liquidity_usd: number
  score:         number
  dex_url:       string
}

interface MemecoinPosition {
  id:            number
  mint:          string
  symbol:        string
  entry_price:   number
  current_price: number
  pnl_pct:       number
  pnl_usd:       number
  amount_usd:    number
  opened:        string
}

interface MemecoinsStatus {
  signals:   MemecoinSignal[]
  positions: MemecoinPosition[]
  stats: {
    win_rate:     number
    total_pnl:    number
    closed_count: number
  }
}

// ── Helpers ───────────────────────────────────────────────────────────────────

const MONO = { fontFamily: 'JetBrains Mono, monospace' }

function fmtPrice(p: number): string {
  if (!p) return '—'
  if (p >= 1)      return `$${p.toFixed(4)}`
  if (p >= 0.0001) return `$${p.toFixed(6)}`
  return `$${p.toExponential(3)}`
}

function fmtVol(v: number): string {
  if (v >= 1_000_000) return `$${(v / 1_000_000).toFixed(1)}M`
  if (v >= 1_000)     return `$${(v / 1_000).toFixed(0)}k`
  return `$${v.toFixed(0)}`
}

function scoreColor(score: number): string {
  if (score >= 70) return '#00d48a'
  if (score >= 50) return '#f59e0b'
  return '#ef4444'
}

function pnlColor(pct: number): string {
  return pct >= 0 ? '#00d48a' : '#ef4444'
}

// ── Component ─────────────────────────────────────────────────────────────────

export function MemecoinsSection() {
  const queryClient = useQueryClient()
  const [buyAmounts, setBuyAmounts]   = useState<Record<string, string>>({})
  const [busyMints,  setBusyMints]    = useState<Set<string>>(new Set())
  const [msg, setMsg]                 = useState<string | null>(null)

  const { data, isLoading } = useQuery<MemecoinsStatus>({
    queryKey: ['memecoins'],
    queryFn:  async () => (await api.get('/memecoins/status')).data,
    refetchInterval: 30_000,
  })

  const signals   = data?.signals   ?? []
  const positions = data?.positions ?? []
  const stats     = data?.stats     ?? { win_rate: 0, total_pnl: 0, closed_count: 0 }

  function flash(text: string) {
    setMsg(text)
    setTimeout(() => setMsg(null), 4000)
  }

  async function handleBuy(signal: MemecoinSignal) {
    const rawAmt = buyAmounts[signal.mint] ?? '10'
    const amt    = parseFloat(rawAmt)
    if (!amt || amt <= 0) return
    setBusyMints(s => new Set(s).add(signal.mint))
    try {
      const r = await api.post('/memecoins/buy', {
        mint:       signal.mint,
        symbol:     signal.symbol,
        amount_usd: amt,
      })
      if (r.data?.success) {
        flash(`✅ Bought ${signal.symbol} — $${amt}`)
        queryClient.invalidateQueries({ queryKey: ['memecoins'] })
      } else {
        flash(`❌ Buy failed: ${r.data?.error ?? 'unknown error'}`)
      }
    } catch (e: any) {
      flash(`❌ ${e?.response?.data?.detail ?? e.message}`)
    }
    setBusyMints(s => { const n = new Set(s); n.delete(signal.mint); return n })
  }

  async function handleSell(pos: MemecoinPosition) {
    if (!confirm(`Sell ${pos.symbol}? Current PnL: ${pos.pnl_pct >= 0 ? '+' : ''}${pos.pnl_pct.toFixed(1)}%`)) return
    setBusyMints(s => new Set(s).add(pos.mint))
    try {
      const r = await api.post(`/memecoins/sell/${pos.mint}`)
      if (r.data?.success) {
        const sign = r.data.pnl_pct >= 0 ? '+' : ''
        flash(`✅ Sold ${pos.symbol} — ${sign}${r.data.pnl_pct.toFixed(1)}% ($${r.data.pnl_usd >= 0 ? '+' : ''}${r.data.pnl_usd.toFixed(2)})`)
        queryClient.invalidateQueries({ queryKey: ['memecoins'] })
      } else {
        flash(`❌ Sell failed: ${r.data?.error ?? 'unknown error'}`)
      }
    } catch (e: any) {
      flash(`❌ ${e?.response?.data?.detail ?? e.message}`)
    }
    setBusyMints(s => { const n = new Set(s); n.delete(pos.mint); return n })
  }

  return (
    <div>
      {/* ── Header ── */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 14 }}>
        <span className="section-label">MEMECOIN SCANNER</span>
        <span style={{ color: '#2d4060', fontSize: 9, ...MONO }}>
          {signals.length} signals · 5m refresh · semi-auto
        </span>
      </div>

      {/* ── Flash message ── */}
      {msg && (
        <div style={{
          marginBottom: 10, padding: '6px 10px',
          background: 'rgba(255,255,255,0.04)',
          border: '1px solid rgba(255,255,255,0.08)',
          borderRadius: 4, fontSize: 10, ...MONO,
          color: msg.startsWith('✅') ? '#00d48a' : '#ef4444',
        }}>
          {msg}
        </div>
      )}

      {/* ── Scanner Signals ── */}
      <div style={{ marginBottom: 16 }}>
        <div style={{ color: '#3d5a78', fontSize: 9, letterSpacing: '0.08em', marginBottom: 8, ...MONO }}>
          SCANNER SIGNALS
        </div>

        {isLoading ? (
          <div style={{ color: '#4d5a6e', fontSize: 10, ...MONO }}>scanning…</div>
        ) : signals.length === 0 ? (
          <div style={{ color: '#2d4060', fontSize: 10, ...MONO }}>no signals yet — scan runs every 5 min</div>
        ) : (
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 10, ...MONO }}>
              <thead>
                <tr style={{ color: '#2d4060', fontSize: 9 }}>
                  <th style={{ textAlign: 'left',  padding: '3px 6px 6px 0' }}>SYMBOL</th>
                  <th style={{ textAlign: 'right', padding: '3px 6px 6px' }}>PRICE</th>
                  <th style={{ textAlign: 'right', padding: '3px 6px 6px' }}>1H</th>
                  <th style={{ textAlign: 'right', padding: '3px 6px 6px' }}>24H</th>
                  <th style={{ textAlign: 'right', padding: '3px 6px 6px' }}>VOL 24H</th>
                  <th style={{ textAlign: 'right', padding: '3px 6px 6px' }}>LIQ</th>
                  <th style={{ textAlign: 'right', padding: '3px 6px 6px' }}>SCORE</th>
                  <th style={{ textAlign: 'right', padding: '3px 0   6px' }}>BUY</th>
                </tr>
              </thead>
              <tbody>
                {signals.map((s, i) => {
                  const busy = busyMints.has(s.mint)
                  const amt  = buyAmounts[s.mint] ?? '10'
                  return (
                    <tr key={s.mint} style={{
                      borderTop: i > 0 ? '1px solid rgba(255,255,255,0.03)' : 'none',
                    }}>
                      <td style={{ padding: '6px 6px 6px 0' }}>
                        <a
                          href={s.dex_url}
                          target="_blank"
                          rel="noreferrer"
                          style={{ color: '#c0cfe0', textDecoration: 'none', fontWeight: 700 }}
                        >
                          {s.symbol}
                        </a>
                      </td>
                      <td style={{ textAlign: 'right', padding: '6px', color: '#8a9ab0' }}>
                        {fmtPrice(s.price)}
                      </td>
                      <td style={{ textAlign: 'right', padding: '6px', color: (s.change_1h ?? 0) >= 0 ? '#00d48a' : '#ef4444', fontWeight: 700 }}>
                        {s.change_1h != null ? `${s.change_1h >= 0 ? '+' : ''}${s.change_1h.toFixed(1)}%` : '—'}
                      </td>
                      <td style={{ textAlign: 'right', padding: '6px', color: (s.change_24h ?? 0) >= 0 ? '#00d48a' : '#ef4444' }}>
                        {s.change_24h != null ? `${s.change_24h >= 0 ? '+' : ''}${s.change_24h.toFixed(1)}%` : '—'}
                      </td>
                      <td style={{ textAlign: 'right', padding: '6px', color: '#7c9fd4' }}>
                        {fmtVol(s.volume_24h)}
                      </td>
                      <td style={{ textAlign: 'right', padding: '6px', color: '#4d5a6e' }}>
                        {fmtVol(s.liquidity_usd)}
                      </td>
                      <td style={{ textAlign: 'right', padding: '6px' }}>
                        <span style={{ color: scoreColor(s.score), fontWeight: 700 }}>{s.score}</span>
                      </td>
                      <td style={{ textAlign: 'right', padding: '6px 0 6px 6px' }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 4, justifyContent: 'flex-end' }}>
                          <span style={{ color: '#2d4060', fontSize: 9 }}>$</span>
                          <input
                            type="number"
                            value={amt}
                            min="1"
                            onChange={e => setBuyAmounts(prev => ({ ...prev, [s.mint]: e.target.value }))}
                            style={{
                              width: 36, background: 'rgba(255,255,255,0.04)',
                              border: '1px solid rgba(255,255,255,0.08)',
                              borderRadius: 3, color: '#8a9ab0',
                              fontSize: 9, padding: '2px 4px', textAlign: 'right',
                              fontFamily: 'JetBrains Mono, monospace',
                            }}
                          />
                          <button
                            onClick={() => handleBuy(s)}
                            disabled={busy}
                            style={{
                              background: busy ? 'rgba(0,212,138,0.05)' : 'rgba(0,212,138,0.10)',
                              border: '1px solid rgba(0,212,138,0.25)',
                              borderRadius: 3, color: busy ? '#2d4060' : '#00d48a',
                              cursor: busy ? 'default' : 'pointer',
                              fontFamily: 'JetBrains Mono, monospace',
                              fontSize: 9, padding: '3px 8px', fontWeight: 700,
                            }}
                          >
                            {busy ? '…' : 'BUY'}
                          </button>
                        </div>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* ── Open Positions ── */}
      {positions.length > 0 && (
        <div style={{ marginBottom: 16 }}>
          <div style={{ color: '#3d5a78', fontSize: 9, letterSpacing: '0.08em', marginBottom: 8, ...MONO }}>
            OPEN POSITIONS
          </div>
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 10, ...MONO }}>
              <thead>
                <tr style={{ color: '#2d4060', fontSize: 9 }}>
                  <th style={{ textAlign: 'left',  padding: '3px 6px 6px 0' }}>SYMBOL</th>
                  <th style={{ textAlign: 'right', padding: '3px 6px 6px' }}>ENTRY</th>
                  <th style={{ textAlign: 'right', padding: '3px 6px 6px' }}>NOW</th>
                  <th style={{ textAlign: 'right', padding: '3px 6px 6px' }}>PNL</th>
                  <th style={{ textAlign: 'right', padding: '3px 6px 6px' }}>SIZE</th>
                  <th style={{ textAlign: 'right', padding: '3px 0   6px' }}></th>
                </tr>
              </thead>
              <tbody>
                {positions.map((p, i) => {
                  const busy  = busyMints.has(p.mint)
                  const sign  = p.pnl_pct >= 0 ? '+' : ''
                  const color = pnlColor(p.pnl_pct)
                  return (
                    <tr key={p.id} style={{ borderTop: i > 0 ? '1px solid rgba(255,255,255,0.03)' : 'none' }}>
                      <td style={{ padding: '6px 6px 6px 0', color: '#c0cfe0', fontWeight: 700 }}>{p.symbol}</td>
                      <td style={{ textAlign: 'right', padding: '6px', color: '#4d5a6e' }}>{fmtPrice(p.entry_price)}</td>
                      <td style={{ textAlign: 'right', padding: '6px', color: '#8a9ab0' }}>{fmtPrice(p.current_price)}</td>
                      <td style={{ textAlign: 'right', padding: '6px', color, fontWeight: 700 }}>
                        {sign}{p.pnl_pct.toFixed(1)}%
                        <span style={{ color: pnlColor(p.pnl_usd), fontSize: 9, marginLeft: 4 }}>
                          ({p.pnl_usd >= 0 ? '+' : ''}${p.pnl_usd.toFixed(2)})
                        </span>
                      </td>
                      <td style={{ textAlign: 'right', padding: '6px', color: '#4d5a6e' }}>${p.amount_usd.toFixed(0)}</td>
                      <td style={{ textAlign: 'right', padding: '6px 0 6px 6px' }}>
                        <button
                          onClick={() => handleSell(p)}
                          disabled={busy}
                          style={{
                            background: busy ? 'rgba(239,68,68,0.03)' : 'rgba(239,68,68,0.08)',
                            border: '1px solid rgba(239,68,68,0.20)',
                            borderRadius: 3, color: busy ? '#2d4060' : '#ef4444',
                            cursor: busy ? 'default' : 'pointer',
                            fontFamily: 'JetBrains Mono, monospace',
                            fontSize: 9, padding: '3px 8px', fontWeight: 700,
                          }}
                        >
                          {busy ? '…' : 'SELL'}
                        </button>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* ── Footer stats ── */}
      <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
        <span style={{ color: '#2d4060', fontSize: 9, ...MONO }}>
          WIN RATE <span style={{ color: stats.win_rate >= 50 ? '#00d48a' : '#f59e0b' }}>
            {stats.win_rate.toFixed(0)}%
          </span>
        </span>
        <span style={{ color: '#2d4060', fontSize: 9, ...MONO }}>
          TOTAL PNL <span style={{ color: stats.total_pnl >= 0 ? '#00d48a' : '#ef4444' }}>
            {stats.total_pnl >= 0 ? '+' : ''}${stats.total_pnl.toFixed(2)}
          </span>
        </span>
        <span style={{ color: '#2d4060', fontSize: 9, ...MONO }}>
          {stats.closed_count} closed
        </span>
        <span style={{ color: '#2d4060', fontSize: 9, ...MONO }}>
          auto-exit: 2× TP · −50% SL
        </span>
      </div>
    </div>
  )
}
