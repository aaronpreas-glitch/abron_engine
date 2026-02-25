import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../../api'
import type { Trade, TradeSummary } from '../../types'
import { PctChange } from '../shared/PctChange'
import { LoadingSpinner } from '../shared/LoadingSpinner'
import { EmptyState } from '../shared/EmptyState'

// ── helpers ────────────────────────────────────────────────────────────────────

function timeAgo(ts: string) {
  const d = Date.now() - new Date(ts + (ts.endsWith('Z') ? '' : 'Z')).getTime()
  const h = Math.floor(d / 3600000)
  if (h < 1)  return `${Math.floor(d / 60000)}m ago`
  if (h < 24) return `${h}h ago`
  return `${Math.floor(h / 24)}d ago`
}

function fmtPrice(v: number | null) {
  if (v == null || v === 0) return '—'
  if (v < 0.0001) return v.toFixed(10)
  if (v < 0.001)  return v.toFixed(8)
  if (v < 1)      return v.toFixed(6)
  if (v < 1000)   return v.toFixed(4)
  return v.toLocaleString('en-US', { maximumFractionDigits: 2 })
}

// ── Live PnL types ─────────────────────────────────────────────────────────────

interface LivePnlEntry {
  mark_price: number
  pnl_pct: number
}
type LivePnlMap = Record<string, LivePnlEntry>

// ── Pulsing LIVE dot ───────────────────────────────────────────────────────────

function LiveDot({ fresh }: { fresh: boolean }) {
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
      <span style={{
        width: 5, height: 5, borderRadius: '50%',
        background: fresh ? 'var(--green)' : 'var(--muted)',
        boxShadow: fresh ? '0 0 6px var(--green)' : 'none',
        display: 'inline-block',
        animation: fresh ? 'liveGlow 2s ease-in-out infinite' : 'none',
      }} />
      <span style={{
        fontSize: 9, fontWeight: 700, letterSpacing: '0.10em',
        color: fresh ? 'var(--green)' : 'var(--muted)',
        fontFamily: 'JetBrains Mono, monospace',
      }}>
        LIVE
      </span>
    </span>
  )
}

// ── PnL badge ─────────────────────────────────────────────────────────────────

function PnlBadge({ pct }: { pct: number }) {
  const pos = pct >= 0
  const big = Math.abs(pct) >= 10
  return (
    <span style={{
      display: 'inline-block',
      padding: '2px 6px',
      borderRadius: 3,
      fontSize: 11,
      fontWeight: 700,
      fontFamily: 'JetBrains Mono, monospace',
      background: pos
        ? (big ? '#003d2a' : 'var(--green-bg)')
        : (big ? '#3d0a0a' : 'rgba(248,81,73,0.10)'),
      color: pos ? 'var(--green)' : 'var(--red)',
      border: `1px solid ${pos ? 'rgba(57,211,83,0.20)' : 'rgba(248,81,73,0.20)'}`,
    }}>
      {pos ? '+' : ''}{pct.toFixed(2)}%
    </span>
  )
}

// ── Open Position Form ─────────────────────────────────────────────────────────

function OpenPositionForm({ onSuccess }: { onSuccess: () => void }) {
  const [form, setForm] = useState({
    symbol: '', entry_price: '', stop_price: '', mint: '', notes: '',
  })
  const [open, setOpen] = useState(false)

  const mut = useMutation({
    mutationFn: () => api.post('/trades/open', {
      symbol:      form.symbol.trim().toUpperCase(),
      entry_price: parseFloat(form.entry_price),
      stop_price:  form.stop_price ? parseFloat(form.stop_price) : null,
      mint:        form.mint.trim() || null,
      notes:       form.notes.trim() || null,
    }).then(r => r.data),
    onSuccess: (res) => {
      if (res.created) {
        setForm({ symbol: '', entry_price: '', stop_price: '', mint: '', notes: '' })
        setOpen(false)
        onSuccess()
      }
    },
  })

  const canSubmit = form.symbol && form.entry_price && parseFloat(form.entry_price) > 0

  const inputStyle: React.CSSProperties = {
    background: 'var(--surface)',
    border: '1px solid var(--border)',
    color: 'var(--text)',
    borderRadius: 3,
    padding: '6px 8px',
    fontSize: 11,
    fontFamily: 'inherit',
    width: '100%',
    boxSizing: 'border-box',
  }

  return (
    <div style={{ marginBottom: 16 }}>
      {!open ? (
        <button
          onClick={() => setOpen(true)}
          style={{
            padding: '6px 16px', borderRadius: 3, fontSize: 11, cursor: 'pointer',
            background: 'var(--green)', color: '#0d1117', border: 'none', fontWeight: 700,
          }}
        >
          + Open Position
        </button>
      ) : (
        <div className="card">
          <div style={{ color: 'var(--muted)', fontSize: 11, marginBottom: 12, letterSpacing: '0.06em' }}>
            OPEN NEW POSITION
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 10, marginBottom: 10 }}>
            <div>
              <div style={{ color: 'var(--muted)', fontSize: 9, marginBottom: 3 }}>SYMBOL *</div>
              <input
                style={inputStyle}
                placeholder="e.g. PUMP"
                value={form.symbol}
                onChange={e => setForm(f => ({ ...f, symbol: e.target.value.toUpperCase() }))}
              />
            </div>
            <div>
              <div style={{ color: 'var(--muted)', fontSize: 9, marginBottom: 3 }}>ENTRY PRICE *</div>
              <input
                style={inputStyle}
                placeholder="0.00000000"
                value={form.entry_price}
                onChange={e => setForm(f => ({ ...f, entry_price: e.target.value }))}
                type="number"
                step="any"
                min="0"
              />
            </div>
            <div>
              <div style={{ color: 'var(--muted)', fontSize: 9, marginBottom: 3 }}>STOP PRICE (opt)</div>
              <input
                style={inputStyle}
                placeholder="auto: entry × 0.9"
                value={form.stop_price}
                onChange={e => setForm(f => ({ ...f, stop_price: e.target.value }))}
                type="number"
                step="any"
                min="0"
              />
            </div>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr', gap: 10, marginBottom: 12 }}>
            <div>
              <div style={{ color: 'var(--muted)', fontSize: 9, marginBottom: 3 }}>MINT ADDRESS (opt)</div>
              <input
                style={inputStyle}
                placeholder="e.g. pump…"
                value={form.mint}
                onChange={e => setForm(f => ({ ...f, mint: e.target.value }))}
              />
            </div>
            <div>
              <div style={{ color: 'var(--muted)', fontSize: 9, marginBottom: 3 }}>NOTES (opt)</div>
              <input
                style={inputStyle}
                placeholder="reason, setup…"
                value={form.notes}
                onChange={e => setForm(f => ({ ...f, notes: e.target.value }))}
              />
            </div>
          </div>

          {/* Already open notice */}
          {mut.isSuccess && !mut.data?.created && (
            <div style={{ color: 'var(--amber)', fontSize: 11, marginBottom: 8 }}>
              ⚠️ Position already open for {form.symbol || 'this symbol'}. Use Close Position to exit first.
            </div>
          )}

          {mut.isError && (
            <div style={{ color: 'var(--red)', fontSize: 11, marginBottom: 8 }}>
              ❌ {String((mut.error as Error)?.message || 'Error opening position')}
            </div>
          )}

          <div style={{ display: 'flex', gap: 8 }}>
            <button
              onClick={() => mut.mutate()}
              disabled={!canSubmit || mut.isPending}
              style={{
                padding: '6px 18px', borderRadius: 3, fontSize: 11, cursor: canSubmit ? 'pointer' : 'default',
                background: canSubmit ? 'var(--green)' : 'var(--surface)',
                color: canSubmit ? '#0d1117' : 'var(--muted)',
                border: 'none', fontWeight: 700,
                opacity: mut.isPending ? 0.6 : 1,
              }}
            >
              {mut.isPending ? 'Opening…' : '✓ Confirm Open'}
            </button>
            <button
              onClick={() => { setOpen(false); mut.reset() }}
              style={{
                padding: '6px 14px', borderRadius: 3, fontSize: 11, cursor: 'pointer',
                background: 'transparent', border: '1px solid var(--border)', color: 'var(--muted)',
              }}
            >
              Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

// ── Close Position Modal ───────────────────────────────────────────────────────

function CloseModal({ trade, markPrice, onClose, onSuccess }: {
  trade: Trade
  markPrice?: number
  onClose: () => void
  onSuccess: () => void
}) {
  // Pre-fill with live mark price if available
  const [exitPrice, setExitPrice] = useState(
    markPrice && markPrice > 0 ? String(markPrice) : ''
  )

  const mut = useMutation({
    mutationFn: () => api.post('/trades/close', {
      symbol:     trade.symbol,
      mint:       trade.mint || null,
      exit_price: exitPrice ? parseFloat(exitPrice) : null,
    }).then(r => r.data),
    onSuccess: () => {
      onSuccess()
      onClose()
    },
  })

  // Estimate PnL if price given
  const entry = trade.entry_price || 0
  const exit  = parseFloat(exitPrice) || 0
  const estPnl = entry && exit ? ((exit - entry) / entry * 100).toFixed(2) : null

  return (
    <div style={{
      position: 'fixed', inset: 0, background: '#000000bb', zIndex: 1000,
      display: 'flex', alignItems: 'center', justifyContent: 'center',
    }}>
      <div style={{
        background: 'var(--surface)', border: '1px solid var(--border)',
        borderRadius: 6, padding: 24, width: 340, maxWidth: '90vw',
      }}>
        <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 16 }}>
          Close Position — <span style={{ color: 'var(--green)' }}>${trade.symbol}</span>
        </div>

        <div style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 12 }}>
          Entry: {fmtPrice(trade.entry_price)}
          {trade.stop_price && <span> · Stop: {fmtPrice(trade.stop_price)}</span>}
          {markPrice && markPrice > 0 && (
            <span style={{ color: 'var(--green)' }}> · Mark: {fmtPrice(markPrice)}</span>
          )}
        </div>

        {/* Mark price pre-fill notice */}
        {markPrice && markPrice > 0 && (
          <div style={{
            background: 'var(--green-bg)', border: '1px solid rgba(57,211,83,0.15)',
            borderRadius: 3, padding: '5px 9px', marginBottom: 12,
            fontSize: 10, color: 'var(--green)', letterSpacing: '0.04em',
          }}>
            ✦ Pre-filled with live Jupiter price
          </div>
        )}

        <div style={{ marginBottom: 16 }}>
          <div style={{ color: 'var(--muted)', fontSize: 10, marginBottom: 4 }}>EXIT PRICE (leave blank to record without PnL)</div>
          <input
            autoFocus
            style={{
              background: 'var(--bg)', border: '1px solid var(--border)',
              color: 'var(--text)', borderRadius: 3, padding: '7px 10px',
              fontSize: 12, width: '100%', boxSizing: 'border-box', fontFamily: 'inherit',
            }}
            placeholder="0.00000000"
            value={exitPrice}
            onChange={e => setExitPrice(e.target.value)}
            type="number"
            step="any"
            min="0"
          />
          {estPnl !== null && (
            <div style={{
              marginTop: 8, fontSize: 12, fontWeight: 700,
              color: parseFloat(estPnl) >= 0 ? 'var(--green)' : 'var(--red)',
            }}>
              Est. PnL: {parseFloat(estPnl) >= 0 ? '+' : ''}{estPnl}%
            </div>
          )}
        </div>

        {mut.isError && (
          <div style={{ color: 'var(--red)', fontSize: 11, marginBottom: 10 }}>
            {String((mut.error as Error)?.message || 'Error closing position')}
          </div>
        )}

        <div style={{ display: 'flex', gap: 8 }}>
          <button
            onClick={() => mut.mutate()}
            disabled={mut.isPending}
            style={{
              flex: 1, padding: '7px 0', borderRadius: 3, fontSize: 12, cursor: 'pointer',
              background: 'var(--red)', color: '#fff', border: 'none', fontWeight: 700,
              opacity: mut.isPending ? 0.6 : 1,
            }}
          >
            {mut.isPending ? 'Closing…' : '✗ Confirm Close'}
          </button>
          <button
            onClick={onClose}
            style={{
              flex: 1, padding: '7px 0', borderRadius: 3, fontSize: 12, cursor: 'pointer',
              background: 'transparent', border: '1px solid var(--border)', color: 'var(--muted)',
            }}
          >
            Cancel
          </button>
        </div>
      </div>
    </div>
  )
}

// ── Main Panel ─────────────────────────────────────────────────────────────────

export function PositionsPanel() {
  const qc = useQueryClient()
  const [closingTrade, setClosingTrade] = useState<Trade | null>(null)

  const openQ = useQuery<Trade[]>({
    queryKey: ['trades-open'],
    queryFn: () => api.get('/trades/open').then(r => r.data),
    refetchInterval: 30_000,
  })
  const closed  = useQuery<Trade[]>({
    queryKey: ['trades-closed'],
    queryFn: () => api.get('/trades/closed?limit=30').then(r => r.data),
  })
  const summary = useQuery<TradeSummary>({
    queryKey: ['trades-summary'],
    queryFn: () => api.get('/trades/summary').then(r => r.data),
  })

  // Live P&L — 15s refetch, only meaningful if open positions exist
  const livePnl = useQuery<LivePnlMap>({
    queryKey: ['trades-live-pnl'],
    queryFn: () => api.get('/trades/live-pnl').then(r => r.data),
    refetchInterval: 15_000,
    enabled: (openQ.data?.length ?? 0) > 0,
  })

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ['trades-open'] })
    qc.invalidateQueries({ queryKey: ['trades-closed'] })
    qc.invalidateQueries({ queryKey: ['trades-summary'] })
    qc.invalidateQueries({ queryKey: ['trades-live-pnl'] })
  }

  // How many open positions have live data
  const openTrades = openQ.data || []
  const liveCoverage = openTrades.filter(t => livePnl.data?.[String(t.id)]).length
  const hasLiveData = liveCoverage > 0

  // Aggregate unrealized PnL for banner
  const totalUnrealPnl = openTrades.reduce((sum, t) => {
    const live = livePnl.data?.[String(t.id)]
    return live ? sum + live.pnl_pct : sum
  }, 0)
  const avgUnrealPnl = liveCoverage > 0 ? totalUnrealPnl / liveCoverage : null

  const thStyle: React.CSSProperties = {
    color: 'var(--muted)', fontWeight: 400, padding: '4px 8px',
    borderBottom: '1px solid var(--border)', textAlign: 'left', fontSize: 10,
    letterSpacing: '0.04em',
  }
  const tdStyle: React.CSSProperties = {
    padding: '7px 8px', borderBottom: '1px solid #1c2128', fontSize: 12,
  }

  return (
    <div>
      {/* Keyframe for live glow pulse */}
      <style>{`
        @keyframes liveGlow {
          0%, 100% { opacity: 1; box-shadow: 0 0 6px var(--green); }
          50%       { opacity: 0.4; box-shadow: 0 0 2px var(--green); }
        }
      `}</style>

      <h2 style={{ fontSize: 14, fontWeight: 700, letterSpacing: '0.06em', marginBottom: 16 }}>
        💼 POSITIONS &amp; JOURNAL
      </h2>

      {/* Summary row */}
      {summary.data && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', gap: 8, marginBottom: 20 }}>
          {[
            { label: 'Closed',    val: String(summary.data.total_closed) },
            { label: 'Win Rate',  val: `${summary.data.win_rate.toFixed(0)}%`,
              color: summary.data.win_rate >= 55 ? 'var(--green)' : summary.data.win_rate >= 45 ? 'var(--amber)' : 'var(--red)' },
            { label: 'Avg PnL',  val: `${summary.data.avg_pnl >= 0 ? '+' : ''}${summary.data.avg_pnl.toFixed(1)}%`,
              color: summary.data.avg_pnl >= 0 ? 'var(--green)' : 'var(--red)' },
            { label: 'Avg R',    val: `${summary.data.avg_r >= 0 ? '+' : ''}${summary.data.avg_r.toFixed(2)}R`,
              color: summary.data.avg_r >= 0 ? 'var(--green)' : 'var(--red)' },
            { label: 'Total PnL', val: `${summary.data.total_pnl >= 0 ? '+' : ''}${summary.data.total_pnl.toFixed(1)}%`,
              color: summary.data.total_pnl >= 0 ? 'var(--green)' : 'var(--red)' },
          ].map(({ label, val, color }) => (
            <div key={label} className="card" style={{ textAlign: 'center' }}>
              <div style={{ color: 'var(--muted)', fontSize: 11, marginBottom: 3 }}>{label}</div>
              <div style={{ fontSize: 16, fontWeight: 700, color: color || 'var(--text)' }}>{val}</div>
            </div>
          ))}
        </div>
      )}

      {/* Unrealized PnL banner — only shows when we have live data */}
      {hasLiveData && avgUnrealPnl !== null && (
        <div style={{
          marginBottom: 14,
          padding: '8px 14px',
          borderRadius: 4,
          background: avgUnrealPnl >= 0 ? 'var(--green-bg)' : 'rgba(248,81,73,0.08)',
          border: `1px solid ${avgUnrealPnl >= 0 ? 'rgba(57,211,83,0.20)' : 'rgba(248,81,73,0.20)'}`,
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <LiveDot fresh={!livePnl.isLoading} />
            <span style={{ fontSize: 11, color: 'var(--muted)', letterSpacing: '0.04em' }}>
              {liveCoverage} of {openTrades.length} position{openTrades.length !== 1 ? 's' : ''} tracked via Jupiter
            </span>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span style={{ fontSize: 10, color: 'var(--muted)' }}>AVG UNREALIZED</span>
            <PnlBadge pct={avgUnrealPnl} />
          </div>
        </div>
      )}

      {/* Open position form */}
      <OpenPositionForm onSuccess={refresh} />

      {/* Open positions table */}
      <div className="card" style={{ marginBottom: 16 }}>
        <div style={{
          color: 'var(--muted)', fontSize: 11, marginBottom: 10,
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        }}>
          <span>OPEN POSITIONS ({openQ.data?.length ?? 0})</span>
          {openTrades.length > 0 && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              {livePnl.isLoading && !livePnl.data && (
                <span style={{ fontSize: 9, color: 'var(--muted)' }}>fetching prices…</span>
              )}
              {hasLiveData && <LiveDot fresh={!livePnl.isLoading} />}
              {!hasLiveData && !livePnl.isLoading && openTrades.some(t => t.mint) && (
                <span style={{ fontSize: 9, color: 'var(--muted)' }}>no mint → no live price</span>
              )}
            </div>
          )}
        </div>

        {openQ.isLoading ? <LoadingSpinner size={16} /> : openTrades.length === 0 ? (
          <EmptyState message="No open positions. Use the form above to track entries." />
        ) : (
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr>
                <th style={thStyle}>SYMBOL</th>
                <th style={thStyle}>ENTRY</th>
                <th style={thStyle}>MARK</th>
                <th style={thStyle}>UNREAL PNL</th>
                <th style={thStyle}>STOP</th>
                <th style={thStyle}>OPENED</th>
                <th style={thStyle}>NOTES</th>
                <th style={thStyle}></th>
              </tr>
            </thead>
            <tbody>
              {openTrades.map(t => {
                const live = livePnl.data?.[String(t.id)]
                return (
                  <tr
                    key={t.id}
                    style={{ background: 'transparent' }}
                    onMouseEnter={e => (e.currentTarget.style.background = '#1c2128')}
                    onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
                  >
                    <td style={tdStyle}>
                      <span style={{ fontWeight: 700, color: 'var(--green)' }}>${t.symbol}</span>
                      {t.mint && (
                        <div style={{ fontSize: 9, color: 'var(--muted)', marginTop: 1 }}>
                          {t.mint.slice(0, 8)}…{t.mint.slice(-4)}
                        </div>
                      )}
                    </td>
                    <td style={{ ...tdStyle, fontFamily: 'JetBrains Mono, monospace', fontSize: 11 }}>
                      {fmtPrice(t.entry_price)}
                    </td>
                    {/* MARK PRICE — live from Jupiter */}
                    <td style={{ ...tdStyle, fontFamily: 'JetBrains Mono, monospace', fontSize: 11 }}>
                      {live ? (
                        <span style={{ color: 'var(--text)' }}>{fmtPrice(live.mark_price)}</span>
                      ) : !t.mint ? (
                        <span style={{ color: 'var(--dim)', fontSize: 10 }}>no mint</span>
                      ) : livePnl.isLoading ? (
                        <span style={{ color: 'var(--dim)', fontSize: 10 }}>…</span>
                      ) : (
                        <span style={{ color: 'var(--dim)', fontSize: 10 }}>—</span>
                      )}
                    </td>
                    {/* UNREALIZED PNL */}
                    <td style={tdStyle}>
                      {live ? (
                        <PnlBadge pct={live.pnl_pct} />
                      ) : (
                        <span style={{ color: 'var(--dim)', fontSize: 10 }}>—</span>
                      )}
                    </td>
                    <td style={{ ...tdStyle, color: 'var(--red)', fontFamily: 'JetBrains Mono, monospace', fontSize: 11 }}>
                      {fmtPrice(t.stop_price)}
                    </td>
                    <td style={{ ...tdStyle, color: 'var(--muted)' }}>{timeAgo(t.opened_ts_utc)}</td>
                    <td style={{ ...tdStyle, color: 'var(--muted)', maxWidth: 140 }}>
                      <span style={{ fontSize: 11 }}>
                        {(t.notes || '').replace('manual_dashboard_buy', '').replace('manual_telegram_buy', '').slice(0, 36) || '—'}
                      </span>
                    </td>
                    <td style={{ ...tdStyle, textAlign: 'right' }}>
                      <button
                        onClick={() => setClosingTrade(t)}
                        style={{
                          padding: '3px 10px', fontSize: 10, cursor: 'pointer', borderRadius: 3,
                          background: 'transparent', border: '1px solid var(--red)',
                          color: 'var(--red)', fontWeight: 600,
                        }}
                      >
                        Close
                      </button>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
      </div>

      {/* Closed trades journal */}
      <div className="card">
        <div style={{ color: 'var(--muted)', fontSize: 11, marginBottom: 10 }}>
          TRADE JOURNAL (last 30)
        </div>
        {closed.isLoading ? <LoadingSpinner size={16} /> : (closed.data || []).length === 0 ? (
          <EmptyState message="No closed trades yet." />
        ) : (
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr>
                <th style={thStyle}>SYMBOL</th>
                <th style={thStyle}>ENTRY</th>
                <th style={thStyle}>EXIT</th>
                <th style={thStyle}>PNL</th>
                <th style={thStyle}>R</th>
                <th style={thStyle}>CLOSED</th>
              </tr>
            </thead>
            <tbody>
              {(closed.data || []).map(t => (
                <tr key={t.id}
                  style={{ opacity: t.pnl_pct && t.pnl_pct > 0 ? 1 : 0.75, background: 'transparent' }}
                  onMouseEnter={e => (e.currentTarget.style.background = '#1c2128')}
                  onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
                >
                  <td style={tdStyle}>
                    <span style={{
                      fontWeight: 700,
                      color: t.pnl_pct && t.pnl_pct > 0 ? 'var(--green)' : 'var(--red)',
                    }}>
                      {t.pnl_pct && t.pnl_pct > 0 ? '✓' : '✗'} ${t.symbol}
                    </span>
                  </td>
                  <td style={{ ...tdStyle, fontFamily: 'JetBrains Mono, monospace', fontSize: 11 }}>{fmtPrice(t.entry_price)}</td>
                  <td style={{ ...tdStyle, fontFamily: 'JetBrains Mono, monospace', fontSize: 11 }}>{fmtPrice(t.exit_price)}</td>
                  <td style={tdStyle}><PctChange value={t.pnl_pct} /></td>
                  <td style={tdStyle}>
                    {t.r_multiple != null
                      ? <span style={{ color: t.r_multiple >= 0 ? 'var(--green)' : 'var(--red)' }}>
                          {t.r_multiple >= 0 ? '+' : ''}{t.r_multiple.toFixed(2)}R
                        </span>
                      : '—'}
                  </td>
                  <td style={{ ...tdStyle, color: 'var(--muted)', fontSize: 11 }}>
                    {t.closed_ts_utc ? t.closed_ts_utc.slice(0, 10) : '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* Close modal — pass live mark price so it pre-fills exit */}
      {closingTrade && (
        <CloseModal
          trade={closingTrade}
          markPrice={livePnl.data?.[String(closingTrade.id)]?.mark_price}
          onClose={() => setClosingTrade(null)}
          onSuccess={refresh}
        />
      )}
    </div>
  )
}
