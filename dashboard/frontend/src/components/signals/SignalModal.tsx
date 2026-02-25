/**
 * SignalModal — Click any signal card to see full breakdown:
 * score bar with tier, all market data fields, outcome returns if evaluated.
 * Includes a "score intelligence" panel reconstructed from available fields.
 */
import { useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../../api'
import type { Signal } from '../../types'

interface Outcome {
  return_1h_pct: number | null
  return_4h_pct: number | null
  return_24h_pct: number | null
  status: string | null
  last_error: string | null
}

interface SignalDetail {
  signal: Signal & { liquidity_change_24h?: number | null; chain?: string }
  outcome: Outcome | null
}

function fmtUsd(v: number | null) {
  if (v == null) return '—'
  if (v >= 1e9) return `$${(v / 1e9).toFixed(2)}B`
  if (v >= 1e6) return `$${(v / 1e6).toFixed(2)}M`
  if (v >= 1e3) return `$${(v / 1e3).toFixed(1)}K`
  return `$${v.toFixed(2)}`
}

function fmtPrice(v: number | null) {
  if (v == null) return '—'
  if (v < 0.000001) return `$${v.toExponential(3)}`
  if (v < 0.01) return `$${v.toFixed(8)}`.replace(/\.?0+$/, '')
  if (v < 1) return `$${v.toFixed(6)}`.replace(/\.?0+$/, '')
  return `$${v.toFixed(4)}`.replace(/\.?0+$/, '')
}

function pct(v: number | null) {
  if (v == null) return '—'
  return `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`
}

function pctColor(v: number | null) {
  if (v == null) return 'var(--muted)'
  return v > 0 ? 'var(--green)' : v < 0 ? 'var(--red)' : 'var(--muted)'
}

function ReturnBadge({ label, value }: { label: string; value: number | null }) {
  return (
    <div style={{
      flex: 1, textAlign: 'center', padding: '10px 8px', borderRadius: 4,
      background: value == null ? 'var(--surface2)'
        : value > 0 ? '#1a3a22' : value < 0 ? '#3a1a1a' : 'var(--surface2)',
      border: `1px solid ${value == null ? 'var(--border)'
        : value > 0 ? '#39d35344' : value < 0 ? '#f8514944' : 'var(--border)'}`,
    }}>
      <div style={{ color: 'var(--muted)', fontSize: 10, marginBottom: 4 }}>{label}</div>
      <div style={{ fontWeight: 700, fontSize: 16, color: pctColor(value) }}>
        {value == null ? '—' : pct(value)}
      </div>
    </div>
  )
}

function ScoreBar({ label, score, max, color }: { label: string; score: number; max: number; color: string }) {
  const pct = Math.min(100, Math.round((score / max) * 100))
  return (
    <div style={{ marginBottom: 8 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11, marginBottom: 3 }}>
        <span style={{ color: 'var(--muted)' }}>{label}</span>
        <span style={{ color, fontWeight: 700 }}>{score.toFixed(1)} / {max}</span>
      </div>
      <div style={{ height: 4, background: 'var(--border)', borderRadius: 2 }}>
        <div style={{ height: 4, width: `${pct}%`, background: color, borderRadius: 2, transition: 'width 0.3s' }} />
      </div>
    </div>
  )
}

function estimateBreakdown(sig: Signal & { liquidity_change_24h?: number | null }) {
  // Reconstruct approximate score components from stored market fields.
  // We don't have the raw enrichment data (holder wallets, txn count, age)
  // but we can show regime and relative strength contributions.
  const items: { label: string; score: number; max: number; color: string; note: string }[] = []

  if (sig.score_total == null) return items

  // Regime contribution (~20pts): regime_score / 100 * 20
  if (sig.regime_score != null) {
    const regime_contrib = Math.min(20, (sig.regime_score / 100) * 25)
    items.push({
      label: 'Market Regime',
      score: regime_contrib,
      max: 20,
      color: sig.regime_score >= 50 ? 'var(--green)' : sig.regime_score >= 25 ? 'var(--amber)' : 'var(--red)',
      note: `Regime score ${sig.regime_score.toFixed(1)} → ${sig.regime_label || '—'}`,
    })
  }

  // Liquidity health (~15pts)
  if (sig.liquidity_usd != null) {
    let liq_score = 0
    if (sig.liquidity_usd >= 2_000_000) liq_score = 15
    else if (sig.liquidity_usd >= 1_000_000) liq_score = 12
    else if (sig.liquidity_usd >= 500_000) liq_score = 9
    else if (sig.liquidity_usd >= 100_000) liq_score = 6
    else liq_score = 3
    items.push({
      label: 'Liquidity Health',
      score: liq_score,
      max: 15,
      color: liq_score >= 12 ? 'var(--green)' : liq_score >= 8 ? 'var(--amber)' : 'var(--muted)',
      note: `${fmtUsd(sig.liquidity_usd)} liquidity`,
    })
  }

  // Volume structure (~15pts) — vol/liq ratio is a key signal
  if (sig.volume_24h != null && sig.liquidity_usd != null && sig.liquidity_usd > 0) {
    const ratio = sig.volume_24h / sig.liquidity_usd
    let vol_score = 0
    if (ratio > 3) vol_score = 15
    else if (ratio > 1.5) vol_score = 12
    else if (ratio > 0.8) vol_score = 8
    else if (ratio > 0.3) vol_score = 5
    else vol_score = 2
    items.push({
      label: 'Volume Structure',
      score: vol_score,
      max: 15,
      color: vol_score >= 12 ? 'var(--green)' : vol_score >= 8 ? 'var(--amber)' : 'var(--muted)',
      note: `Vol/Liq ratio ${ratio.toFixed(2)}x`,
    })
  }

  // Price action (~15pts) — based on 24h change + rel strength
  if (sig.change_24h != null) {
    let price_score = 0
    if (sig.change_24h > 30) price_score = 14
    else if (sig.change_24h > 15) price_score = 11
    else if (sig.change_24h > 5) price_score = 8
    else if (sig.change_24h > 0) price_score = 5
    else price_score = 2
    // Boost if outperforming SOL
    if (sig.rel_strength_vs_sol != null && sig.rel_strength_vs_sol > 5) price_score = Math.min(15, price_score + 2)
    items.push({
      label: 'Price Action',
      score: price_score,
      max: 15,
      color: price_score >= 11 ? 'var(--green)' : price_score >= 7 ? 'var(--amber)' : 'var(--muted)',
      note: `24h ${pct(sig.change_24h)}${sig.rel_strength_vs_sol != null ? `, vs SOL ${pct(sig.rel_strength_vs_sol)}` : ''}`,
    })
  }

  // Relative strength (~10pts)
  if (sig.rel_strength_vs_sol != null) {
    let rs_score = 0
    if (sig.rel_strength_vs_sol > 20) rs_score = 10
    else if (sig.rel_strength_vs_sol > 10) rs_score = 7
    else if (sig.rel_strength_vs_sol > 3) rs_score = 5
    else if (sig.rel_strength_vs_sol >= 0) rs_score = 3
    else rs_score = 0
    items.push({
      label: 'Rel. Strength vs SOL',
      score: rs_score,
      max: 10,
      color: rs_score >= 7 ? 'var(--green)' : rs_score >= 4 ? 'var(--amber)' : 'var(--muted)',
      note: `${pct(sig.rel_strength_vs_sol)} vs SOL`,
    })
  }

  return items
}

interface Props {
  signalId: number
  onClose: () => void
}

export function SignalModal({ signalId, onClose }: Props) {
  const { data, isLoading } = useQuery<SignalDetail>({
    queryKey: ['signal-detail', signalId],
    queryFn: () => api.get(`/signals/${signalId}`).then(r => r.data),
  })

  // Close on Escape
  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [onClose])

  const sig = data?.signal
  const outcome = data?.outcome
  const breakdown = sig ? estimateBreakdown(sig) : []
  const score = sig?.score_total

  const convictionLabel = (c: number | null) => c === 3 ? 'A' : c === 2 ? 'B' : c === 1 ? 'C' : '—'
  const convictionColor = (c: number | null) => c === 3 ? 'var(--green)' : c === 2 ? 'var(--amber)' : 'var(--muted)'

  return (
    <div
      style={{
        position: 'fixed', inset: 0, zIndex: 1000,
        background: 'rgba(0,0,0,0.7)', backdropFilter: 'blur(2px)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        padding: 16,
      }}
      onClick={e => { if (e.target === e.currentTarget) onClose() }}
    >
      <div style={{
        background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 6,
        width: '100%', maxWidth: 580, maxHeight: '90vh', overflowY: 'auto',
        padding: 20,
      }}>
        {/* Header */}
        <div style={{ display: 'flex', alignItems: 'center', marginBottom: 16 }}>
          <div>
            <span style={{ fontWeight: 700, fontSize: 20 }}>{sig ? `$${sig.symbol}` : '…'}</span>
            {sig?.setup_type && (
              <span style={{ color: 'var(--muted)', fontSize: 11, marginLeft: 10, fontStyle: 'italic' }}>
                {sig.setup_type}
              </span>
            )}
          </div>
          <button
            onClick={onClose}
            style={{
              marginLeft: 'auto', background: 'none', border: 'none',
              color: 'var(--muted)', fontSize: 20, cursor: 'pointer', padding: '0 4px',
            }}
          >✕</button>
        </div>

        {isLoading && (
          <div style={{ color: 'var(--muted)', textAlign: 'center', padding: 32 }}>Loading…</div>
        )}

        {sig && (
          <>
            {/* Score + conviction */}
            {score != null && (
              <div className="card" style={{ marginBottom: 12 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 12 }}>
                  <span style={{ fontSize: 28, fontWeight: 700, color: score >= 85 ? 'var(--green)' : score >= 70 ? 'var(--amber)' : 'var(--muted)' }}>
                    {score.toFixed(0)}
                  </span>
                  <div>
                    <div style={{ fontSize: 11, color: 'var(--muted)' }}>Score / 100</div>
                    <div style={{
                      fontSize: 11, fontWeight: 700,
                      color: score >= 85 ? 'var(--green)' : score >= 75 ? 'var(--amber)' : 'var(--muted)',
                    }}>
                      {score >= 85 ? 'ELITE' : score >= 75 ? 'HIGH' : score >= 65 ? 'MED' : 'LOW'}
                    </div>
                  </div>
                  {sig.conviction != null && (
                    <div style={{ marginLeft: 'auto', textAlign: 'center' }}>
                      <div style={{ fontSize: 10, color: 'var(--muted)', marginBottom: 2 }}>CONVICTION</div>
                      <span style={{
                        fontSize: 22, fontWeight: 900, color: convictionColor(sig.conviction),
                      }}>
                        {convictionLabel(sig.conviction)}
                      </span>
                    </div>
                  )}
                </div>

                {/* Score component bars */}
                {breakdown.length > 0 && (
                  <div>
                    <div style={{ color: 'var(--muted)', fontSize: 10, marginBottom: 8, fontWeight: 700 }}>
                      SCORE COMPONENTS (estimated from market data)
                    </div>
                    {breakdown.map(b => (
                      <div key={b.label}>
                        <ScoreBar label={b.label} score={b.score} max={b.max} color={b.color} />
                        <div style={{ fontSize: 10, color: 'var(--muted)', marginBottom: 6, marginTop: -4 }}>
                          {b.note}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}

            {/* Outcome returns */}
            {outcome && (
              <div className="card" style={{ marginBottom: 12 }}>
                <div style={{ color: 'var(--muted)', fontSize: 10, marginBottom: 10, fontWeight: 700 }}>
                  OUTCOME RETURNS {outcome.status && `· ${outcome.status}`}
                </div>
                <div style={{ display: 'flex', gap: 8 }}>
                  <ReturnBadge label="1H RETURN" value={outcome.return_1h_pct} />
                  <ReturnBadge label="4H RETURN" value={outcome.return_4h_pct} />
                  <ReturnBadge label="24H RETURN" value={outcome.return_24h_pct} />
                </div>
                {outcome.last_error && (
                  <div style={{ marginTop: 8, fontSize: 10, color: 'var(--red)' }}>
                    Eval error: {outcome.last_error}
                  </div>
                )}
              </div>
            )}

            {/* Market data grid */}
            <div className="card" style={{ marginBottom: 12 }}>
              <div style={{ color: 'var(--muted)', fontSize: 10, marginBottom: 10, fontWeight: 700 }}>MARKET DATA</div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px 16px', fontSize: 12 }}>
                {[
                  { label: 'Price', value: fmtPrice(sig.price_usd) },
                  { label: '24h Change', value: pct(sig.change_24h), color: pctColor(sig.change_24h) },
                  { label: 'Liquidity', value: fmtUsd(sig.liquidity_usd) },
                  { label: 'Liq Change 24h', value: pct((sig as SignalDetail['signal']).liquidity_change_24h ?? null), color: pctColor((sig as SignalDetail['signal']).liquidity_change_24h ?? null) },
                  { label: 'Volume 24h', value: fmtUsd(sig.volume_24h) },
                  { label: 'vs SOL', value: pct(sig.rel_strength_vs_sol), color: pctColor(sig.rel_strength_vs_sol) },
                  { label: 'Regime Score', value: sig.regime_score != null ? sig.regime_score.toFixed(1) : '—', color: sig.regime_score != null ? (sig.regime_score >= 50 ? 'var(--green)' : sig.regime_score >= 25 ? 'var(--amber)' : 'var(--red)') : 'var(--muted)' },
                  { label: 'Regime Label', value: sig.regime_label?.replace(/_/g, ' ') || '—' },
                  { label: 'Category', value: sig.category || '—' },
                  { label: 'Chain', value: (sig as SignalDetail['signal']).chain || '—' },
                ].map(({ label, value, color }) => (
                  <div key={label}>
                    <span style={{ color: 'var(--muted)' }}>{label}: </span>
                    <span style={{ color: color || 'var(--text)', fontWeight: 600 }}>{value}</span>
                  </div>
                ))}
              </div>
            </div>

            {/* Links */}
            {(sig.mint || sig.pair_address) && (
              <div className="card" style={{ marginBottom: 12 }}>
                <div style={{ color: 'var(--muted)', fontSize: 10, marginBottom: 8, fontWeight: 700 }}>LINKS</div>
                <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
                  {(sig.pair_address || sig.mint) && (
                    <a
                      href={`https://dexscreener.com/solana/${sig.pair_address || sig.mint}`}
                      target="_blank" rel="noopener noreferrer"
                      style={{ color: '#58a6ff', fontSize: 12, textDecoration: 'none', padding: '4px 10px', border: '1px solid #58a6ff44', borderRadius: 3 }}
                    >
                      DexScreener ↗
                    </a>
                  )}
                  {sig.mint && (
                    <a
                      href={`https://pump.fun/coin/${sig.mint}`}
                      target="_blank" rel="noopener noreferrer"
                      style={{ color: 'var(--muted)', fontSize: 12, textDecoration: 'none', padding: '4px 10px', border: '1px solid var(--border)', borderRadius: 3 }}
                    >
                      Pump.fun ↗
                    </a>
                  )}
                  {sig.mint && (
                    <a
                      href={`https://solscan.io/token/${sig.mint}`}
                      target="_blank" rel="noopener noreferrer"
                      style={{ color: 'var(--muted)', fontSize: 12, textDecoration: 'none', padding: '4px 10px', border: '1px solid var(--border)', borderRadius: 3 }}
                    >
                      Solscan ↗
                    </a>
                  )}
                </div>
                {sig.mint && (
                  <div style={{ marginTop: 8, fontSize: 10, color: 'var(--muted)', fontFamily: 'monospace', wordBreak: 'break-all' }}>
                    mint: {sig.mint}
                  </div>
                )}
              </div>
            )}

            {/* Notes */}
            {sig.notes && (
              <div className="card">
                <div style={{ color: 'var(--muted)', fontSize: 10, marginBottom: 4, fontWeight: 700 }}>NOTES</div>
                <div style={{ fontSize: 11, color: 'var(--muted)', lineHeight: 1.5 }}>{sig.notes}</div>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}
