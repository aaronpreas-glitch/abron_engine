import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../../api'
import { LoadingSpinner } from '../shared/LoadingSpinner'

// ── types ─────────────────────────────────────────────────────────────────────

interface PerpsPosition {
  entry_price:    number
  mark_price:     number
  size_usd:       number
  collateral:     number
  liq_price:      number
  pnl:            number
  funding_rate:   number
  leverage:       number
  market:         string
  side:           string
  borrow_fees_usd: number
}

interface PerpsResponse {
  position:  PerpsPosition | null
  sol_price: number | null
}

interface DcaEntry {
  ts:         number
  date:       string
  amount_usd: number
  sol_price:  number
  leverage:   number
  size_usd:   number
  sol_amount: number
  note:       string
}

interface DcaSummary {
  total_invested_usd: number
  total_size_usd:     number
  total_sol:          number
  avg_price:          number
  avg_lev_price:      number
  current_value:      number
  pnl:                number
  pnl_pct:            number
  count:              number
}

interface DcaResponse {
  sol_price: number | null
  entries:   DcaEntry[]
  summary:   DcaSummary | null
}

// ── helpers ────────────────────────────────────────────────────────────────────

const fmt2 = (v: number | null | undefined) =>
  v != null ? `$${v.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : '—'

const fmtPct = (v: number | null | undefined) =>
  v != null ? `${v >= 0 ? '+' : ''}${v.toFixed(2)}%` : '—'

const fmtSol = (v: number | null | undefined) =>
  v != null ? `$${v.toFixed(2)}` : '—'

function liqColor(markPrice: number, liqPrice: number): string {
  if (!liqPrice) return 'var(--text)'
  const dist = Math.abs(markPrice - liqPrice) / markPrice * 100
  if (dist < 20) return 'var(--red)'
  if (dist < 35) return 'var(--amber)'
  return 'var(--green)'
}

function liqDistPct(markPrice: number, liqPrice: number): string {
  if (!liqPrice || !markPrice) return '—'
  const dist = Math.abs(markPrice - liqPrice) / markPrice * 100
  return `${dist.toFixed(1)}% to liq`
}

// ── Perps card ────────────────────────────────────────────────────────────────

function PerpsCard({ data }: { data: PerpsResponse }) {
  const { position: pos, sol_price: sol } = data
  const mark = pos?.mark_price || sol || 0

  return (
    <div className="card" style={{ marginBottom: 16 }}>
      <div style={{ color: 'var(--muted)', fontSize: 11, marginBottom: 12, letterSpacing: '0.06em' }}>
        ⚡ SOL-LONG PERPS (JUPITER)
      </div>

      {!pos ? (
        <div style={{ color: 'var(--muted)', fontSize: 12, textAlign: 'center', padding: '16px 0' }}>
          No open perps position
          {sol && <div style={{ marginTop: 6, color: 'var(--text)', fontWeight: 700, fontSize: 14 }}>SOL {fmtSol(sol)}</div>}
        </div>
      ) : (
        <>
          {/* SOL price + leverage badge */}
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, marginBottom: 14 }}>
            <span style={{ fontSize: 22, fontWeight: 700, color: 'var(--text)' }}>
              {fmtSol(mark)}
            </span>
            <span style={{
              padding: '2px 8px', borderRadius: 3, fontSize: 11, fontWeight: 700,
              background: '#58a6ff22', color: '#58a6ff', border: '1px solid #58a6ff44',
            }}>
              {pos.leverage.toFixed(1)}x {pos.side}
            </span>
            <span style={{
              marginLeft: 'auto', fontSize: 18, fontWeight: 700,
              color: pos.pnl >= 0 ? 'var(--green)' : 'var(--red)',
            }}>
              {pos.pnl >= 0 ? '+' : ''}{fmt2(pos.pnl)}
            </span>
          </div>

          {/* Data grid */}
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '8px 16px', fontSize: 11 }}>
            {[
              { label: 'Entry',      val: fmtSol(pos.entry_price) },
              { label: 'Mark',       val: fmtSol(pos.mark_price) },
              { label: 'Size',       val: fmt2(pos.size_usd) },
              { label: 'Collateral', val: fmt2(pos.collateral) },
              { label: 'Borrow Fees', val: fmt2(pos.borrow_fees_usd) },
              { label: 'Funding/d', val: pos.funding_rate ? `${pos.funding_rate.toFixed(4)}%` : '—' },
            ].map(({ label, val }) => (
              <div key={label}>
                <div style={{ color: 'var(--muted)', marginBottom: 1 }}>{label}</div>
                <div style={{ fontWeight: 600 }}>{val}</div>
              </div>
            ))}
          </div>

          {/* Liquidation distance bar */}
          {pos.liq_price > 0 && (
            <div style={{ marginTop: 14, padding: '8px 12px', background: 'var(--surface)', borderRadius: 4 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11, marginBottom: 4 }}>
                <span style={{ color: 'var(--muted)' }}>Liquidation {fmtSol(pos.liq_price)}</span>
                <span style={{ color: liqColor(mark, pos.liq_price), fontWeight: 700 }}>
                  {liqDistPct(mark, pos.liq_price)}
                </span>
              </div>
              <div style={{ height: 4, background: 'var(--border)', borderRadius: 2 }}>
                {(() => {
                  const dist = Math.min(100, Math.abs(mark - pos.liq_price) / mark * 100)
                  return (
                    <div style={{
                      height: '100%', width: `${dist}%`, borderRadius: 2,
                      background: liqColor(mark, pos.liq_price),
                      transition: 'width 0.3s',
                    }} />
                  )
                })()}
              </div>
            </div>
          )}
        </>
      )}
    </div>
  )
}

// ── DCA Tracker ───────────────────────────────────────────────────────────────

function DcaTracker({ data, onAdded, onCleared }: {
  data: DcaResponse
  onAdded: () => void
  onCleared: () => void
}) {
  const qc = useQueryClient()
  const [form, setForm] = useState({ amount: '', leverage: '1', price: '' })
  const [expanded, setExpanded] = useState(false)
  const [confirmClear, setConfirmClear] = useState(false)

  const addMut = useMutation({
    mutationFn: () => api.post('/dca', {
      amount_usd: parseFloat(form.amount),
      leverage:   parseFloat(form.leverage) || 1,
      price:      form.price ? parseFloat(form.price) : null,
    }).then(r => r.data),
    onSuccess: () => {
      setForm({ amount: '', leverage: '1', price: '' })
      qc.invalidateQueries({ queryKey: ['dca'] })
      onAdded()
    },
  })

  const clearMut = useMutation({
    mutationFn: () => api.delete('/dca').then(r => r.data),
    onSuccess: () => {
      setConfirmClear(false)
      qc.invalidateQueries({ queryKey: ['dca'] })
      onCleared()
    },
  })

  const { summary, entries, sol_price: sol } = data
  const canAdd = form.amount && parseFloat(form.amount) > 0

  const inputStyle: React.CSSProperties = {
    background: 'var(--surface)',
    border: '1px solid var(--border)',
    color: 'var(--text)',
    borderRadius: 3,
    padding: '5px 8px',
    fontSize: 11,
    width: '100%',
    fontFamily: 'inherit',
  }

  return (
    <div className="card">
      <div style={{ color: 'var(--muted)', fontSize: 11, marginBottom: 12, letterSpacing: '0.06em' }}>
        📊 SOL DCA TRACKER
      </div>

      {/* Summary stats */}
      {summary && summary.count > 0 ? (
        <div style={{ marginBottom: 14 }}>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 8, marginBottom: 10 }}>
            {[
              { label: 'Entries',    val: String(summary.count) },
              { label: 'Avg Price',  val: fmtSol(summary.avg_price) },
              { label: 'Total In',   val: fmt2(summary.total_invested_usd) },
              { label: 'PnL',        val: fmtPct(summary.pnl_pct),
                color: summary.pnl_pct >= 0 ? 'var(--green)' : 'var(--red)' },
            ].map(({ label, val, color }) => (
              <div key={label} style={{ textAlign: 'center', padding: '6px 4px', background: 'var(--surface)', borderRadius: 4 }}>
                <div style={{ color: 'var(--muted)', fontSize: 10, marginBottom: 2 }}>{label}</div>
                <div style={{ fontWeight: 700, fontSize: 13, color: color || 'var(--text)' }}>{val}</div>
              </div>
            ))}
          </div>

          {/* Expand/collapse entries */}
          <button
            onClick={() => setExpanded(e => !e)}
            style={{
              width: '100%', padding: '4px 8px', fontSize: 10, cursor: 'pointer',
              background: 'transparent', border: '1px solid var(--border)',
              color: 'var(--muted)', borderRadius: 3, marginBottom: expanded ? 8 : 0,
            }}
          >
            {expanded ? '▲ Hide entries' : `▼ Show ${entries.length} entries`}
          </button>

          {expanded && (
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 10 }}>
              <thead>
                <tr>
                  {['Date', 'Amount', 'Price', 'Lev', 'SOL', 'Size'].map(h => (
                    <th key={h} style={{ color: 'var(--muted)', fontWeight: 400, padding: '3px 6px',
                      borderBottom: '1px solid var(--border)', textAlign: 'left' }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {entries.map((e, i) => (
                  <tr key={i}>
                    <td style={{ padding: '3px 6px', color: 'var(--muted)' }}>{e.date}</td>
                    <td style={{ padding: '3px 6px' }}>{fmt2(e.amount_usd)}</td>
                    <td style={{ padding: '3px 6px' }}>{fmtSol(e.sol_price)}</td>
                    <td style={{ padding: '3px 6px' }}>{e.leverage.toFixed(1)}x</td>
                    <td style={{ padding: '3px 6px' }}>{e.sol_amount.toFixed(3)}</td>
                    <td style={{ padding: '3px 6px' }}>{fmt2(e.size_usd)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      ) : (
        <div style={{ color: 'var(--muted)', fontSize: 11, marginBottom: 14 }}>
          No DCA entries yet. Add your first entry below.
          {sol && <span style={{ color: 'var(--text)' }}> SOL: {fmtSol(sol)}</span>}
        </div>
      )}

      {/* Add entry form */}
      <div style={{ borderTop: '1px solid var(--border)', paddingTop: 12 }}>
        <div style={{ color: 'var(--muted)', fontSize: 10, marginBottom: 8 }}>LOG ENTRY</div>
        <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr 2fr auto', gap: 6 }}>
          <div>
            <div style={{ color: 'var(--muted)', fontSize: 9, marginBottom: 3 }}>Amount (USD)</div>
            <input
              style={inputStyle}
              placeholder={`e.g. 250`}
              value={form.amount}
              onChange={e => setForm(f => ({ ...f, amount: e.target.value }))}
              type="number"
              min="0"
              step="any"
            />
          </div>
          <div>
            <div style={{ color: 'var(--muted)', fontSize: 9, marginBottom: 3 }}>Leverage</div>
            <input
              style={inputStyle}
              placeholder="1"
              value={form.leverage}
              onChange={e => setForm(f => ({ ...f, leverage: e.target.value }))}
              type="number"
              min="1"
              step="0.5"
            />
          </div>
          <div>
            <div style={{ color: 'var(--muted)', fontSize: 9, marginBottom: 3 }}>Price (blank = live)</div>
            <input
              style={inputStyle}
              placeholder={sol ? `${sol.toFixed(2)}` : 'live'}
              value={form.price}
              onChange={e => setForm(f => ({ ...f, price: e.target.value }))}
              type="number"
              min="0"
              step="any"
            />
          </div>
          <div style={{ display: 'flex', alignItems: 'flex-end' }}>
            <button
              onClick={() => addMut.mutate()}
              disabled={!canAdd || addMut.isPending}
              style={{
                padding: '5px 14px', borderRadius: 3, fontSize: 11, cursor: canAdd ? 'pointer' : 'default',
                background: canAdd ? 'var(--green)' : 'var(--surface)',
                color: canAdd ? '#0d1117' : 'var(--muted)',
                border: 'none', fontWeight: 700,
                opacity: addMut.isPending ? 0.6 : 1,
              }}
            >
              {addMut.isPending ? '…' : '+ Add'}
            </button>
          </div>
        </div>
        {addMut.isError && (
          <div style={{ color: 'var(--red)', fontSize: 10, marginTop: 6 }}>
            {String((addMut.error as Error)?.message || 'Error adding entry')}
          </div>
        )}
      </div>

      {/* Clear all */}
      {entries.length > 0 && (
        <div style={{ marginTop: 10, textAlign: 'right' }}>
          {!confirmClear ? (
            <button onClick={() => setConfirmClear(true)} style={{
              fontSize: 10, cursor: 'pointer', background: 'transparent',
              border: 'none', color: 'var(--muted)', padding: '2px 0',
            }}>
              clear all entries
            </button>
          ) : (
            <span style={{ fontSize: 10 }}>
              <span style={{ color: 'var(--muted)' }}>Are you sure? </span>
              <button onClick={() => clearMut.mutate()} style={{
                fontSize: 10, cursor: 'pointer', background: 'transparent',
                border: 'none', color: 'var(--red)', padding: '0 4px',
              }}>
                Yes, clear
              </button>
              <button onClick={() => setConfirmClear(false)} style={{
                fontSize: 10, cursor: 'pointer', background: 'transparent',
                border: 'none', color: 'var(--muted)', padding: '0 4px',
              }}>
                Cancel
              </button>
            </span>
          )}
        </div>
      )}
    </div>
  )
}

// ── Main SolPanel ─────────────────────────────────────────────────────────────

export function SolPanel() {
  const qc = useQueryClient()

  const perps = useQuery<PerpsResponse>({
    queryKey: ['perps'],
    queryFn: () => api.get('/perps/position').then(r => r.data),
    refetchInterval: 30_000,
  })

  const dca = useQuery<DcaResponse>({
    queryKey: ['dca'],
    queryFn: () => api.get('/dca').then(r => r.data),
    refetchInterval: 60_000,
  })

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16 }}>
        <h2 style={{ fontSize: 14, fontWeight: 700, letterSpacing: '0.06em' }}>⚡ SOL POSITION</h2>
        <button
          onClick={() => {
            qc.invalidateQueries({ queryKey: ['perps'] })
            qc.invalidateQueries({ queryKey: ['dca'] })
          }}
          style={{
            marginLeft: 'auto', padding: '3px 10px', fontSize: 10, cursor: 'pointer',
            background: 'transparent', border: '1px solid var(--border)',
            color: 'var(--muted)', borderRadius: 3,
          }}
        >
          ↻ refresh
        </button>
      </div>

      {perps.isLoading ? <LoadingSpinner /> : perps.data && (
        <PerpsCard data={perps.data} />
      )}

      {dca.isLoading ? <LoadingSpinner /> : dca.data && (
        <DcaTracker
          data={dca.data}
          onAdded={() => qc.invalidateQueries({ queryKey: ['dca'] })}
          onCleared={() => qc.invalidateQueries({ queryKey: ['dca'] })}
        />
      )}
    </div>
  )
}
